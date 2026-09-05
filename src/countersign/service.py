"""The control function, as one object.

Everything the API and the CLI do goes through here: onboard a tenant, run a
control that has fallen due, run every control that has fallen due, seed a
worked example.

The division of labour is the same at every entry point. The agents propose, the
deterministic tests decide, the store records, and the two verbs that carry
authority, accepting a domain and approving a control, are not on this class
at all. They are on :class:`~countersign.database.Store`, behind a check that
refuses an agent identity.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from countersign import catalogue
from countersign.config import Settings
from countersign.connectors import build_connectors, describe_live_state
from countersign.control_tests import run_test
from countersign.database import Store
from countersign.domain import PERIOD_DAYS, ProposedControl, period_for
from countersign.workflow import InvocationTrace, preflight, run_onboarding, run_review

# The seeded demonstration is accepted and approved by a named person from the
# tenant's own staff, because that is what the gate requires and pretending
# otherwise would make the audit trail a lie about its own subject.
SEED_ACTORS = {
    "kestrel": "c.walsh@kestrelpay.eu",
    "northwind": "p.raman@northwindsystems.com",
    "brandt": "a.reinhardt@brandtwerke.de",
}


class Countersign:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = Store(settings.database_path)
        self._connectors: dict[str, dict] = {}

    # ----------------------------------------------------------------

    def connectors(self, tenant: str) -> dict:
        if tenant not in self._connectors:
            self._connectors[tenant] = build_connectors(
                tenant, allow_live=self.settings.allow_live_connectors
            )
        return self._connectors[tenant]

    def sources(self, tenant: str) -> list[dict[str, Any]]:
        return describe_live_state(self.connectors(tenant))

    # ----------------------------------------------------------------
    # Onboarding
    # ----------------------------------------------------------------

    def onboard(self, tenant: str, actor: str) -> dict[str, Any]:
        """Discover the company, propose the taxonomy, design the controls.

        Nothing here is accepted or scheduled. It ends with a set of proposals
        and a person who has to read them.
        """
        meta = catalogue.TENANTS.get(tenant, {})
        self.store.upsert_tenant(
            tenant,
            meta.get("display_name", tenant.title()),
            meta.get("description", ""),
        )
        connectors = self.connectors(tenant)
        trace = InvocationTrace()

        profile, taxonomy, proposals = run_onboarding(self.settings, connectors, tenant, trace)

        # Every proposed control is executed once, discarded, purely to find out
        # whether it can run at all. A control whose evidence source is missing
        # is still proposed, because the gap is the most useful thing on the page, but
        # it cannot be approved until the source exists.
        as_of = self.settings.as_of
        runnable: dict[str, tuple[bool, str]] = {}
        for proposal in proposals:
            for control in proposal.controls:
                start, end = period_for(as_of, control.periodicity)
                runnable[control.code] = preflight(control, connectors, start, end)

        self.store.record_profile(tenant, profile, self.settings.model_mode, "agent:discovery")
        self.store.record_domains(tenant, taxonomy, self.settings.model_mode, "agent:taxonomy")
        self.store.record_controls(
            tenant, proposals, runnable, self.settings.model_mode, "agent:control_designer"
        )
        return {
            "tenant": tenant,
            "sector": profile.sector,
            "domains": len(taxonomy.domains),
            "controls": sum(len(p.controls) for p in proposals),
            "blocked": [code for code, (ok, _) in runnable.items() if not ok],
            "trace": trace.events,
            "excluded": taxonomy.deliberately_excluded,
        }

    # ----------------------------------------------------------------
    # Execution
    # ----------------------------------------------------------------

    def _as_control(self, row: dict[str, Any]) -> ProposedControl:
        """Rebuild the typed control from its stored row, so the tests see one shape."""
        return ProposedControl(
            code=row["code"],
            title=row["title"],
            objective=row["objective"],
            nature=row["nature"],
            test_kind=row["test_kind"],
            parameters=row["parameters"],
            periodicity=row["periodicity"],
            periodicity_rationale=row["periodicity_rationale"] or "Recorded at proposal time.",
            tolerance=row["tolerance"],
            severity_if_failed=row["severity_if_failed"],
            owner_role=row["owner_role"],
            automation_note=row["automation_note"],
        )

    def run_control(
        self, tenant: str, code: str, as_of: date | None = None, lookback: int | None = None
    ) -> dict[str, Any]:
        """Execute one control: test first, then narrate, then record.

        ``lookback`` widens the period beyond the control's own cadence. The
        seeded estate holds a quarter of activity, so a daily control asked to
        look at one day would legitimately find an empty population and report
        inconclusive. Widening it is a property of the demonstration, and the
        period tested is written on every run.
        """
        as_of = as_of or self.settings.as_of
        row = self.store.control(tenant, code)
        if row is None:
            raise KeyError(f"control {code} does not exist for {tenant}")
        control = self._as_control(row)

        days = lookback if lookback is not None else PERIOD_DAYS[control.periodicity]
        period_start = as_of - timedelta(days=days)
        connectors = self.connectors(tenant)
        trace = InvocationTrace()

        result = run_test(control.test_kind, connectors, control.parameters, period_start, as_of)
        report, challenges = run_review(self.settings, connectors, control, result, trace)

        run_id = self.store.record_run(
            tenant,
            row,
            result,
            report,
            challenges,
            trace.events,
            self.settings.model_mode,
            as_of,
        )
        closed = self.store.close_findings_with_evidence(tenant, run_id)
        return {
            "run_id": run_id,
            "control": code,
            "outcome": result.outcome(control.tolerance),
            "population": result.population_size,
            "exceptions": result.exception_count,
            "findings": len(report.findings),
            "closed_findings": closed,
        }

    def run_due(self, tenant: str, as_of: date | None = None, lookback: int | None = None) -> list[dict[str, Any]]:
        """Run everything that has fallen due. This is what the schedule means."""
        as_of = as_of or self.settings.as_of
        return [
            self.run_control(tenant, control["code"], as_of, lookback)
            for control in self.store.due_controls(tenant, as_of)
        ]

    def run_all_due(
        self, as_of: date | None = None, lookback: int | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        """Run everything due for every onboarded tenant.

        This is what the background scheduler calls on each tick. ``lookback``
        defaults to each control's own cadence, so the schedule runs the controls
        on the periods they were approved with.
        """
        as_of = as_of or self.settings.as_of
        return {
            tenant["id"]: self.run_due(tenant["id"], as_of, lookback)
            for tenant in self.store.tenants()
        }

    # ----------------------------------------------------------------
    # Seeded demonstration
    # ----------------------------------------------------------------

    def seed(self, tenant: str, lookback: int = 91) -> dict[str, Any]:
        """Onboard, accept, approve and run once, so the product opens on finished work.

        The acceptances and approvals below are real gate transactions by a
        named person from the tenant's staff, recorded in the audit chain like
        any other. What makes them a demonstration is the company, not the
        mechanism.
        """
        actor = SEED_ACTORS.get(tenant, "risk@example.com")
        summary = self.onboard(tenant, actor)

        with self.store.write() as connection:
            self.store._append_audit(
                connection,
                actor,
                "demo.seeded",
                f"tenant:{tenant}",
                {
                    "note": (
                        "Seeded demonstration. The company is synthetic; the acceptances, "
                        "approvals and runs that follow are real transactions against it."
                    )
                },
            )

        for domain in self.store.domains(tenant):
            if domain["status"] == "proposed":
                self.store.decide_domain(tenant, domain["code"], True, actor)

        as_of = self.settings.as_of
        approved: list[str] = []
        for control in self.store.controls(tenant, status="proposed"):
            if not control["runnable"]:
                continue
            self.store.approve_control(tenant, control["code"], actor, as_of)
            approved.append(control["code"])

        runs = [self.run_control(tenant, code, as_of, lookback) for code in approved]
        return {
            **summary,
            "accepted_domains": len(self.store.domains(tenant)),
            "approved_controls": approved,
            "runs": runs,
            "findings_open": len(self.store.findings(tenant, status="open")),
        }
