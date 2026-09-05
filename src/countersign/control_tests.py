"""The deterministic control tests.

This module is the reason a model cannot talk a control into passing. Every
outcome Countersign publishes is produced here, in ordinary Python, over rows a
connector returned, before any model is invoked. A model chooses which test to
run and with what parameters; it never gets to write the answer.

Each test returns a :class:`TestResult` carrying the whole population, not a
sample and not a summary. An exception is a population member that failed, with
the reason attached. A member that was considered and deliberately excluded is
recorded as passing with ``disposition="suppressed"`` and a stated reason, so a
reviewer can see what the control chose not to raise and disagree with it.

Three limits are respected everywhere:

* thresholds come from the tenant's obligation register, never from this file;
* a dataset that cannot be served produces ``not_tested``, never a pass;
* nothing here reads the wall clock, so a run is reproducible from its period.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from countersign.connectors import ConnectorError, evidence_for
from countersign.domain import PopulationItem, TestResult

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _rows(connectors: dict, dataset: str, since: date | None = None) -> list[dict[str, Any]]:
    """Fetch ``"<kind>.<dataset>"`` through the connector for that kind."""
    kind, _, name = dataset.partition(".")
    connector = connectors.get(kind)
    if connector is None:
        raise ConnectorError(f"no connector configured for {kind!r}")
    return connector.fetch(name, since)


def _obligation(connectors: dict, reference: str) -> dict[str, Any]:
    for row in _rows(connectors, "obligations.obligations"):
        if row.get("reference") == reference:
            return row
    raise ConnectorError(
        f"obligation {reference!r} is not in the register; a control may not test against a "
        f"limit that the tenant has not written down"
    )


def _moment(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            return datetime.fromisoformat(value[:10])
        except ValueError:
            return None
    return parsed.replace(tzinfo=None)


def _day(value: Any) -> date | None:
    moment = _moment(value)
    return moment.date() if moment else None


def _matches(row: dict[str, Any], criteria: dict[str, Any]) -> bool:
    for key, wanted in criteria.items():
        actual = row.get(key)
        if isinstance(wanted, list):
            if actual not in wanted:
                return False
        elif actual != wanted:
            return False
    return True


def _in_period(row: dict[str, Any], field_name: str, start: date, end: date) -> bool:
    when = _day(row.get(field_name))
    return when is not None and start <= when <= end


def _months_between(earlier: date, later: date) -> float:
    return (later - earlier).days / 30.4375


def _suppressed(item: PopulationItem, reason: str) -> PopulationItem:
    """Mark a would-be exception as considered and excluded, with the reason."""
    item.passed = True
    item.reason = reason
    item.attributes["disposition"] = "suppressed"
    return item


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TestKind:
    """One deterministic test, and what a control must supply to use it."""

    key: str
    title: str
    what_it_does: str
    datasets: tuple[str, ...]
    required_parameters: tuple[str, ...]
    optional_parameters: tuple[str, ...] = ()
    run: Callable[..., TestResult] = field(default=None, repr=False)

    def validate(self, parameters: dict[str, Any]) -> list[str]:
        """Complaints about a proposed parameter set. Empty means usable."""
        problems = [
            f"missing required parameter {name!r}"
            for name in self.required_parameters
            if name not in parameters
        ]
        allowed = set(self.required_parameters) | set(self.optional_parameters)
        problems += [f"unknown parameter {name!r}" for name in parameters if name not in allowed]
        return problems


REGISTRY: dict[str, TestKind] = {}


def register(kind: TestKind) -> TestKind:
    REGISTRY[kind.key] = kind
    return kind


def describe_registry() -> list[dict[str, Any]]:
    """The catalogue handed to the control-design agent.

    A control whose ``test_kind`` is not in this list cannot be scheduled, so
    this is also the complete set of things the agent is allowed to propose.
    """
    return [
        {
            "test_kind": kind.key,
            "title": kind.title,
            "what_it_does": kind.what_it_does,
            "datasets": list(kind.datasets),
            "required_parameters": list(kind.required_parameters),
            "optional_parameters": list(kind.optional_parameters),
        }
        for kind in REGISTRY.values()
    ]


# --------------------------------------------------------------------------
# 1. Obligation reconciliation
# --------------------------------------------------------------------------


def obligation_reconciliation(
    connectors: dict, parameters: dict[str, Any], period_start: date, period_end: date
) -> TestResult:
    """Measure elapsed time against the limit in the obligation register.

    The interesting case is not the measurement. It is that the register and the
    systems that implement it can hold different numbers, and every system can
    agree with every other system while all of them disagree with the register.
    When that happens the test records it, because a population walked against
    the wrong limit passes.
    """
    obligation = _obligation(connectors, parameters["obligation_reference"])
    limit = obligation.get("quantitative_limit")
    if limit is None:
        raise ConnectorError(
            f"obligation {obligation['reference']} carries no quantitative limit to test against"
        )
    unit = obligation.get("limit_unit", "hours")
    divisor = 3600.0 if unit == "hours" else 86400.0

    notes: list[str] = []
    declaration = parameters.get("internal_declaration")
    if declaration:
        try:
            for row in _rows(connectors, declaration["dataset"]):
                declared = row.get(declaration["field"])
                if declared is not None and float(declared) != float(limit):
                    notes.append(
                        f"{row.get('name', row.get('id'))} is configured to {declared} {unit} "
                        f"against a register limit of {limit} {unit} "
                        f"(declared source: {row.get('declared_source', 'not stated')}). "
                        f"Every item below was measured against the register."
                    )
        except ConnectorError as error:
            notes.append(f"internal declaration not readable: {error}")

    population: list[PopulationItem] = []
    for row in _rows(connectors, parameters["dataset"]):
        if not _matches(row, parameters.get("filter", {})):
            continue
        started = _moment(row.get(parameters["start_field"]))
        if started is None or not (period_start <= started.date() <= period_end):
            continue
        finished = _moment(row.get(parameters["end_field"]))
        item = PopulationItem(
            subject=str(row.get("id")),
            label=str(row.get("name", row.get("id"))),
            passed=False,
            evidence=[evidence_for(parameters["dataset"].partition(".")[0], row)],
        )
        if finished is None:
            item.reason = (
                f"No {parameters['end_field'].replace('_', ' ')} is recorded; the "
                f"{limit} {unit} limit in {obligation['reference']} cannot have been met."
            )
            item.attributes = {"elapsed": None, "limit": limit, "unit": unit}
        else:
            elapsed = (finished - started).total_seconds() / divisor
            item.passed = elapsed <= float(limit)
            item.attributes = {
                "elapsed": round(elapsed, 2),
                "limit": limit,
                "unit": unit,
                "started_at": started.isoformat(),
                "finished_at": finished.isoformat(),
            }
            item.reason = (
                f"{round(elapsed, 1)} {unit} against a {limit} {unit} limit "
                f"({obligation['reference']})"
            )
        population.append(item)

    return TestResult(
        test_kind="obligation_reconciliation",
        period_start=period_start,
        period_end=period_end,
        population=population,
        notes=notes,
        parameters=parameters,
    )


register(
    TestKind(
        key="obligation_reconciliation",
        title="Elapsed time against a registered obligation",
        what_it_does=(
            "Measures the interval between two timestamps on each record against the quantitative "
            "limit held in the obligation register, and reports when a system is configured to a "
            "different limit than the register holds."
        ),
        datasets=("obligations.obligations", "jira.issues"),
        required_parameters=("obligation_reference", "dataset", "start_field", "end_field"),
        optional_parameters=("filter", "internal_declaration"),
        run=obligation_reconciliation,
    )
)


# --------------------------------------------------------------------------
# 2. Leaver access revocation
# --------------------------------------------------------------------------


def leaver_access_revocation(
    connectors: dict, parameters: dict[str, Any], period_start: date, period_end: date
) -> TestResult:
    """Every leaver's access is gone within the registered grace period.

    A rehire is not an exception. A control that cannot tell a returning
    colleague from an unrevoked account gets switched off within a month, and
    then nothing is watching at all.
    """
    obligation = _obligation(connectors, parameters["obligation_reference"])
    grace_days = int(obligation.get("quantitative_limit") or 1)

    users = {row.get("email", "").lower(): row for row in _rows(connectors, "identity.users")}
    privileged_groups = {
        row["name"] for row in _rows(connectors, "identity.groups") if row.get("privileged")
    }
    privileged_emails: dict[str, list[str]] = {}
    for row in _rows(connectors, "identity.memberships"):
        if row.get("group") in privileged_groups:
            privileged_emails.setdefault(row.get("email", "").lower(), []).append(row["group"])

    population: list[PopulationItem] = []
    not_tested: list[str] = []
    for employee in _rows(connectors, "hris.employees"):
        left = _day(employee.get("termination_date"))
        if left is None or left > period_end:
            continue
        email = employee.get("email", "").lower()
        user = users.get(email)
        item = PopulationItem(
            subject=email,
            label=f"{employee.get('name')}, left {left.isoformat()}",
            passed=True,
            evidence=[evidence_for("hris", employee)],
        )
        if user is None:
            not_tested.append(f"{email}: no directory account found to test")
            continue
        item.evidence.append(evidence_for("identity", user))
        rehired = _day(employee.get("rehire_start_date"))
        active = user.get("status") == "ACTIVE"
        elapsed = (period_end - left).days
        item.attributes = {
            "status": user.get("status"),
            "days_since_leaving": elapsed,
            "grace_days": grace_days,
            "privileged_groups": privileged_emails.get(email, []),
            "last_login": user.get("last_login", ""),
        }
        if rehired and rehired >= left:
            population.append(
                _suppressed(
                    item,
                    f"Rehired on {rehired.isoformat()}; the open account belongs to a current "
                    f"employee, not to a leaver.",
                )
            )
            continue
        if active and elapsed > grace_days:
            item.passed = False
            groups = privileged_emails.get(email, [])
            item.reason = (
                f"Account still ACTIVE {elapsed} days after the final working day, against a "
                f"{grace_days} day limit ({obligation['reference']})."
                + (f" Still a member of {', '.join(groups)}." if groups else "")
            )
        else:
            item.reason = f"Access removed within the {grace_days} day limit."
        population.append(item)

    return TestResult(
        test_kind="leaver_access_revocation",
        period_start=period_start,
        period_end=period_end,
        population=population,
        not_tested=not_tested,
        parameters=parameters,
    )


register(
    TestKind(
        key="leaver_access_revocation",
        title="Leaver access removed inside the registered grace period",
        what_it_does=(
            "Joins the HR leaver list to the directory and reports accounts still active beyond "
            "the grace period in the register. Rehires are excluded with the reason stated."
        ),
        datasets=("hris.employees", "identity.users", "identity.groups", "identity.memberships"),
        required_parameters=("obligation_reference",),
        run=leaver_access_revocation,
    )
)


# --------------------------------------------------------------------------
# 3. Change approval
# --------------------------------------------------------------------------


def change_approval(
    connectors: dict, parameters: dict[str, Any], period_start: date, period_end: date
) -> TestResult:
    """Every production change carries an approval from someone other than its author.

    The emergency path is part of the standard, not a hole in it. A change merged
    without prior approval, under an emergency ticket that was approved
    afterwards, complies; the test suppresses it and says which ticket.
    """
    production = {
        repo["name"]
        for repo in _rows(connectors, "github.repositories")
        if repo.get("production", True) and not repo.get("archived")
    }
    emergency_tickets: dict[str, dict[str, Any]] = {}
    try:
        for issue in _rows(connectors, "jira.issues"):
            linked = issue.get("linked_change")
            if linked:
                emergency_tickets[linked] = issue
    except ConnectorError:
        pass

    approved_states = set(parameters.get("cab_approved_states", ["Approved", "Done", "Closed"]))
    emergency_label = parameters.get("emergency_label", "emergency")

    population: list[PopulationItem] = []
    for pull in _rows(connectors, "github.pull_requests"):
        if pull.get("repository") not in production:
            continue
        merged = _day(pull.get("merged_at"))
        if merged is None or not (period_start <= merged <= period_end):
            continue
        author = pull.get("author", "")
        independent = [name for name in pull.get("approvals", []) if name and name != author]
        item = PopulationItem(
            subject=str(pull.get("id")),
            label=str(pull.get("name")),
            passed=bool(independent),
            attributes={
                "author": author,
                "approvals": pull.get("approvals", []),
                "repository": pull.get("repository"),
                "merged_at": pull.get("merged_at"),
            },
            evidence=[evidence_for("github", pull)],
        )
        if independent:
            item.reason = f"Approved by {', '.join(independent)}."
            population.append(item)
            continue

        ticket = emergency_tickets.get(str(pull.get("id")))
        if (
            emergency_label in pull.get("labels", [])
            and ticket is not None
            and ticket.get("status") in approved_states
        ):
            item.evidence.append(evidence_for("jira", ticket))
            population.append(
                _suppressed(
                    item,
                    f"Emergency change permitted by the change standard; {ticket['key']} was "
                    f"approved by {ticket.get('approved_by', 'the change manager')} on "
                    f"{str(ticket.get('approved_at', ''))[:10]}.",
                )
            )
            continue

        item.passed = False
        item.reason = (
            f"Merged with no approval from anyone other than the author ({author})."
            if not pull.get("approvals")
            else f"The only approval recorded is the author's own ({author})."
        )
        population.append(item)

    return TestResult(
        test_kind="change_approval",
        period_start=period_start,
        period_end=period_end,
        population=population,
        parameters=parameters,
    )


register(
    TestKind(
        key="change_approval",
        title="Production change approved by someone other than its author",
        what_it_does=(
            "Walks every change merged to a production branch in the period and checks for an "
            "approval by a second person. Emergency changes carrying an approved change ticket "
            "are suppressed with the ticket cited."
        ),
        datasets=("github.repositories", "github.pull_requests", "jira.issues"),
        required_parameters=(),
        optional_parameters=("emergency_label", "cab_approved_states"),
        run=change_approval,
    )
)


# --------------------------------------------------------------------------
# 4. Dormant privileged accounts
# --------------------------------------------------------------------------


def dormant_privileged_access(
    connectors: dict, parameters: dict[str, Any], period_start: date, period_end: date
) -> TestResult:
    """Privileged accounts that have stopped being used are still privileged."""
    obligation = _obligation(connectors, parameters["obligation_reference"])
    limit_days = int(obligation.get("quantitative_limit") or 90)

    users = {row.get("email", "").lower(): row for row in _rows(connectors, "identity.users")}
    privileged = {
        row["name"] for row in _rows(connectors, "identity.groups") if row.get("privileged")
    }

    seen: dict[str, PopulationItem] = {}
    for membership in _rows(connectors, "identity.memberships"):
        group = membership.get("group")
        if group not in privileged:
            continue
        email = membership.get("email", "").lower()
        user = users.get(email)
        if user is None:
            continue
        last = _day(user.get("last_login"))
        idle = (period_end - last).days if last else None
        item = seen.get(email)
        if item is None:
            item = PopulationItem(
                subject=email,
                label=f"{user.get('display_name', email)} · {group}",
                passed=True,
                attributes={
                    "last_login": user.get("last_login", ""),
                    "idle_days": idle,
                    "limit_days": limit_days,
                    "groups": [],
                },
                evidence=[evidence_for("identity", user)],
            )
            seen[email] = item
        item.attributes["groups"].append(group)
        item.label = f"{user.get('display_name', email)} · {', '.join(item.attributes['groups'])}"
        if idle is None:
            item.passed = False
            item.reason = "No successful authentication is recorded for this privileged account."
        elif idle > limit_days:
            item.passed = False
            item.reason = (
                f"Last authenticated {idle} days ago against a {limit_days} day limit "
                f"({obligation['reference']})."
            )
        else:
            item.reason = f"Authenticated {idle} days ago."

    return TestResult(
        test_kind="dormant_privileged_access",
        period_start=period_start,
        period_end=period_end,
        population=list(seen.values()),
        parameters=parameters,
    )


register(
    TestKind(
        key="dormant_privileged_access",
        title="Privileged accounts still in use",
        what_it_does=(
            "Every member of a group flagged privileged, checked against the dormancy limit in "
            "the register."
        ),
        datasets=("identity.users", "identity.groups", "identity.memberships"),
        required_parameters=("obligation_reference",),
        run=dormant_privileged_access,
    )
)


# --------------------------------------------------------------------------
# 5. Vendor obligations
# --------------------------------------------------------------------------


def vendor_obligation(
    connectors: dict, parameters: dict[str, Any], period_start: date, period_end: date
) -> TestResult:
    """Critical third parties carry current due diligence and a way out."""
    obligation = _obligation(connectors, parameters["obligation_reference"])
    months = obligation.get("quantitative_limit")
    require_exit = bool(parameters.get("require_exit_plan", True))
    critical_only = bool(parameters.get("critical_only", True))

    population: list[PopulationItem] = []
    for vendor in _rows(connectors, "vendors.vendors"):
        if critical_only and not vendor.get("supports_critical_function"):
            continue
        failures: list[str] = []
        reviewed = _day(vendor.get("last_due_diligence"))
        if months is not None:
            if reviewed is None:
                failures.append("no due diligence is recorded")
            elif _months_between(reviewed, period_end) > float(months):
                age = round(_months_between(reviewed, period_end), 1)
                failures.append(
                    f"due diligence is {age} months old against a {months} month limit"
                )
        if require_exit and not vendor.get("exit_plan_reference"):
            failures.append("no exit strategy is documented")
        population.append(
            PopulationItem(
                subject=str(vendor.get("id")),
                label=f"{vendor.get('name')} · {vendor.get('service', '')}",
                passed=not failures,
                reason=(
                    f"{'; '.join(failures).capitalize()} ({obligation['reference']})."
                    if failures
                    else "Due diligence current and an exit strategy is on file."
                ),
                attributes={
                    "critical": vendor.get("supports_critical_function"),
                    "last_due_diligence": vendor.get("last_due_diligence", ""),
                    "exit_plan_reference": vendor.get("exit_plan_reference", ""),
                },
                evidence=[evidence_for("vendors", vendor)],
            )
        )

    return TestResult(
        test_kind="vendor_obligation",
        period_start=period_start,
        period_end=period_end,
        population=population,
        parameters=parameters,
    )


register(
    TestKind(
        key="vendor_obligation",
        title="Critical third parties carry current due diligence and an exit strategy",
        what_it_does=(
            "Checks every provider supporting a critical function against the due-diligence "
            "recency limit in the register and, optionally, for a documented exit strategy."
        ),
        datasets=("vendors.vendors", "obligations.obligations"),
        required_parameters=("obligation_reference",),
        optional_parameters=("require_exit_plan", "critical_only"),
        run=vendor_obligation,
    )
)


# --------------------------------------------------------------------------
# 6. Policy review currency
# --------------------------------------------------------------------------


def policy_review_currency(
    connectors: dict, parameters: dict[str, Any], period_start: date, period_end: date
) -> TestResult:
    """A policy past its own review date is a policy nobody has confirmed is still true."""
    population: list[PopulationItem] = []
    for document in _rows(connectors, "documents.documents"):
        if parameters.get("mandated_only", True) and not document.get("mandated"):
            continue
        cycle = document.get("review_cycle_months")
        reviewed = _day(document.get("last_reviewed"))
        if not cycle or reviewed is None:
            population.append(
                PopulationItem(
                    subject=str(document.get("id")),
                    label=str(document.get("name")),
                    passed=False,
                    reason="No review cycle or no review date is recorded.",
                    evidence=[evidence_for("documents", document)],
                )
            )
            continue
        age = _months_between(reviewed, period_end)
        overdue = age > float(cycle)
        population.append(
            PopulationItem(
                subject=str(document.get("id")),
                label=str(document.get("name")),
                passed=not overdue,
                reason=(
                    f"Last reviewed {reviewed.isoformat()}, {round(age, 1)} months ago, against a "
                    f"{cycle} month cycle."
                ),
                attributes={
                    "last_reviewed": document.get("last_reviewed"),
                    "review_cycle_months": cycle,
                    "months_since_review": round(age, 1),
                    "owner": document.get("owner", ""),
                },
                evidence=[evidence_for("documents", document)],
            )
        )

    return TestResult(
        test_kind="policy_review_currency",
        period_start=period_start,
        period_end=period_end,
        population=population,
        parameters=parameters,
    )


register(
    TestKind(
        key="policy_review_currency",
        title="Mandated policies reviewed inside their own cycle",
        what_it_does="Compares each policy's last review date against the review cycle it declares.",
        datasets=("documents.documents",),
        required_parameters=(),
        optional_parameters=("mandated_only",),
        run=policy_review_currency,
    )
)


# --------------------------------------------------------------------------
# 7. Dual authorisation
# --------------------------------------------------------------------------


def dual_authorisation(
    connectors: dict, parameters: dict[str, Any], period_start: date, period_end: date
) -> TestResult:
    """Value above the registered threshold needs two independent people."""
    obligation = _obligation(connectors, parameters["obligation_reference"])
    threshold = float(obligation.get("quantitative_limit") or 0)
    amount_field = parameters.get("amount_field", "amount_eur")
    minimum = int(parameters.get("minimum_authorisers", 2))

    population: list[PopulationItem] = []
    for payment in _rows(connectors, "ledger.payments"):
        when = _day(payment.get("authorised_at"))
        if when is None or not (period_start <= when <= period_end):
            continue
        amount = float(payment.get(amount_field) or 0)
        if amount < threshold:
            continue
        authorisers = {name for name in payment.get("authorisers", []) if name}
        initiator = payment.get("initiator", "")
        independent = authorisers - {initiator}
        passed = len(authorisers) >= minimum and bool(independent)
        population.append(
            PopulationItem(
                subject=str(payment.get("id")),
                label=f"{payment.get('id')} · EUR {amount:,.2f}",
                passed=passed,
                reason=(
                    f"{len(authorisers)} authorisers, {len(independent)} independent of the initiator."
                    if passed
                    else f"Only {len(authorisers)} authoriser(s) recorded for EUR {amount:,.2f}, "
                    f"against the {obligation['reference']} threshold of EUR {threshold:,.0f}."
                ),
                attributes={
                    "amount": amount,
                    "authorisers": sorted(authorisers),
                    "initiator": initiator,
                },
                evidence=[evidence_for("ledger", payment)],
            )
        )

    return TestResult(
        test_kind="dual_authorisation",
        period_start=period_start,
        period_end=period_end,
        population=population,
        parameters=parameters,
    )


register(
    TestKind(
        key="dual_authorisation",
        title="Two independent authorisers above the registered value threshold",
        what_it_does=(
            "Walks every transaction at or above the threshold held in the register and checks "
            "for a second authoriser who is not the initiator."
        ),
        datasets=("ledger.payments", "obligations.obligations"),
        required_parameters=("obligation_reference",),
        optional_parameters=("amount_field", "minimum_authorisers"),
        run=dual_authorisation,
    )
)


# --------------------------------------------------------------------------
# 8. Screening coverage
# --------------------------------------------------------------------------


def screening_coverage(
    connectors: dict, parameters: dict[str, Any], period_start: date, period_end: date
) -> TestResult:
    """Every counterparty onboarded in the period was screened before it was paid."""
    _obligation(connectors, parameters["obligation_reference"])
    payments_by_counterparty: dict[str, date] = {}
    try:
        for payment in _rows(connectors, "ledger.payments"):
            when = _day(payment.get("authorised_at"))
            key = payment.get("counterparty")
            if when and key and (key not in payments_by_counterparty or when < payments_by_counterparty[key]):
                payments_by_counterparty[key] = when
    except ConnectorError:
        pass

    population: list[PopulationItem] = []
    for counterparty in _rows(connectors, "ledger.counterparties"):
        onboarded = _day(counterparty.get("onboarded_at"))
        if onboarded is None or not (period_start <= onboarded <= period_end):
            continue
        reference = counterparty.get("screening_reference")
        screened = _day(counterparty.get("screening_at"))
        first_payment = payments_by_counterparty.get(str(counterparty.get("id")))
        problems: list[str] = []
        if not reference:
            problems.append("no screening reference is recorded")
        if screened and first_payment and screened > first_payment:
            problems.append(
                f"screened on {screened.isoformat()} after the first payment on "
                f"{first_payment.isoformat()}"
            )
        population.append(
            PopulationItem(
                subject=str(counterparty.get("id")),
                label=str(counterparty.get("name")),
                passed=not problems,
                reason="; ".join(problems).capitalize() if problems else f"Screened, reference {reference}.",
                attributes={
                    "screening_reference": reference,
                    "screened_at": counterparty.get("screening_at", ""),
                },
                evidence=[evidence_for("ledger", counterparty)],
            )
        )

    return TestResult(
        test_kind="screening_coverage",
        period_start=period_start,
        period_end=period_end,
        population=population,
        parameters=parameters,
    )


register(
    TestKind(
        key="screening_coverage",
        title="Counterparties screened before the first payment",
        what_it_does=(
            "Every counterparty onboarded in the period must carry a screening reference dated "
            "before the first payment executed for it."
        ),
        datasets=("ledger.counterparties", "ledger.payments"),
        required_parameters=("obligation_reference",),
        run=screening_coverage,
    )
)


# --------------------------------------------------------------------------
# 9. Required field present
# --------------------------------------------------------------------------


def required_field_present(
    connectors: dict, parameters: dict[str, Any], period_start: date, period_end: date
) -> TestResult:
    """A record that triggers an obligation carries the artefact that obligation demands.

    Deliberately generic: a processor handling personal data needs a data
    processing agreement, a listed dual-use shipment needs an export
    authorisation. Both are the same shape, so both are the same test.
    """
    obligation = _obligation(connectors, parameters["obligation_reference"])
    dataset = parameters["dataset"]
    required = parameters["required_field"]
    trigger = parameters.get("filter", {})

    population: list[PopulationItem] = []
    for row in _rows(connectors, dataset):
        if not _matches(row, trigger):
            continue
        date_field = parameters.get("date_field")
        if date_field and not _in_period(row, date_field, period_start, period_end):
            continue
        value = row.get(required)
        population.append(
            PopulationItem(
                subject=str(row.get("id")),
                label=str(row.get("name", row.get("id"))),
                passed=bool(value),
                reason=(
                    f"{required.replace('_', ' ')} recorded: {value}"
                    if value
                    else f"No {required.replace('_', ' ')} is recorded, required by "
                    f"{obligation['reference']}."
                ),
                attributes={required: value or ""},
                evidence=[evidence_for(dataset.partition(".")[0], row)],
            )
        )

    return TestResult(
        test_kind="required_field_present",
        period_start=period_start,
        period_end=period_end,
        population=population,
        parameters=parameters,
    )


register(
    TestKind(
        key="required_field_present",
        title="Records that trigger an obligation carry what it demands",
        what_it_does=(
            "Filters a dataset to the records an obligation applies to and checks that the "
            "required artefact reference is present on each one."
        ),
        datasets=("obligations.obligations",),
        required_parameters=("obligation_reference", "dataset", "required_field"),
        optional_parameters=("filter", "date_field"),
        run=required_field_present,
    )
)


# --------------------------------------------------------------------------
# 10. Recurrence interval
# --------------------------------------------------------------------------


def recurrence_interval(
    connectors: dict, parameters: dict[str, Any], period_start: date, period_end: date
) -> TestResult:
    """Something that must happen every N days has happened inside N days.

    The population is the things that must be covered, not the events. A site
    that was never inspected produces no event, and a test that walks events
    will never notice it is missing.
    """
    obligation = _obligation(connectors, parameters["obligation_reference"])
    limit_days = int(obligation.get("quantitative_limit") or 30)
    dataset = parameters["dataset"]
    group_field = parameters["group_field"]
    date_field = parameters["date_field"]

    latest: dict[str, tuple[date, dict[str, Any]]] = {}
    for row in _rows(connectors, dataset):
        if not _matches(row, parameters.get("filter", {})):
            continue
        subject = row.get(group_field)
        when = _day(row.get(date_field))
        if not subject or when is None:
            continue
        if subject not in latest or when > latest[subject][0]:
            latest[subject] = (when, row)

    expected = parameters.get("expected_subjects") or sorted(latest)
    population: list[PopulationItem] = []
    for subject in expected:
        entry = latest.get(subject)
        if entry is None:
            population.append(
                PopulationItem(
                    subject=str(subject),
                    label=str(subject),
                    passed=False,
                    reason=f"No event of this kind is recorded at all ({obligation['reference']}).",
                )
            )
            continue
        when, row = entry
        gap = (period_end - when).days
        population.append(
            PopulationItem(
                subject=str(subject),
                label=str(subject),
                passed=gap <= limit_days,
                reason=(
                    f"Last recorded {when.isoformat()}, {gap} days ago, against a "
                    f"{limit_days} day interval ({obligation['reference']})."
                ),
                attributes={"last_event": when.isoformat(), "gap_days": gap, "limit_days": limit_days},
                evidence=[evidence_for(dataset.partition(".")[0], row)],
            )
        )

    return TestResult(
        test_kind="recurrence_interval",
        period_start=period_start,
        period_end=period_end,
        population=population,
        parameters=parameters,
    )


register(
    TestKind(
        key="recurrence_interval",
        title="A recurring obligation has recurred inside its interval",
        what_it_does=(
            "Groups events by the thing they cover, takes the most recent one for each, and "
            "compares the gap against the interval in the register. Subjects with no event at "
            "all are exceptions, not absences."
        ),
        datasets=("obligations.obligations",),
        required_parameters=("obligation_reference", "dataset", "group_field", "date_field"),
        optional_parameters=("filter", "expected_subjects"),
        run=recurrence_interval,
    )
)


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------


def run_test(
    test_kind: str,
    connectors: dict,
    parameters: dict[str, Any],
    period_start: date,
    period_end: date,
) -> TestResult:
    """Execute one registered test. Unknown kinds are refused, not improvised."""
    kind = REGISTRY.get(test_kind)
    if kind is None:
        raise ConnectorError(
            f"{test_kind!r} is not a registered test. A control can only bind to a test that "
            f"exists: {', '.join(sorted(REGISTRY))}"
        )
    problems = kind.validate(parameters)
    if problems:
        raise ConnectorError(f"{test_kind} parameters rejected: {'; '.join(problems)}")
    return kind.run(connectors, parameters, period_start, period_end)
