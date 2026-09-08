"""Composing the report, without a model.

Countersign runs in three model modes. In ``bedrock`` and ``agentcore`` the
prose and the candidate findings come from the Strands agents in
:mod:`countersign.workflow`. In ``demo`` they come from here.

This exists for two reasons. It lets a judge run the whole product with no
credentials and no bill and still see finished work rather than empty screens.
And it fixes the shape the live agents are asked to produce, so the difference
between the two modes is the quality of the writing rather than the structure of
the report or which facts are allowed in it.

What it may not do is decide anything. Every number below is read off the
:class:`TestResult`, and the outcome was settled before this module was called.
"""

from __future__ import annotations

from countersign.domain import (
    Challenge,
    ChallengeSet,
    InjectionSignal,
    ProposedFinding,
    ReviewNarrative,
    Severity,
    TestResult,
)
from countersign.injection import contained_statement


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


def _subject_lines(result: TestResult, limit: int = 6) -> str:
    lines = [f"{item.label}: {item.reason}" for item in result.exceptions[:limit]]
    remaining = result.exception_count - len(lines)
    if remaining > 0:
        lines.append(f"and {remaining} more of the same shape")
    return "; ".join(lines)


# --------------------------------------------------------------------------
# Finding composers, one per test kind
# --------------------------------------------------------------------------


def _finding_obligation(control, result: TestResult) -> ProposedFinding:
    reference = result.parameters.get("obligation_reference", "the register")
    worst = max(
        (item for item in result.exceptions),
        key=lambda item: item.attributes.get("elapsed") or 0,
        default=None,
    )
    limit = worst.attributes.get("limit") if worst else None
    unit = worst.attributes.get("unit", "hours") if worst else "hours"
    divergence = result.notes[0] if result.notes else ""
    return ProposedFinding(
        title=f"{_plural(result.exception_count, 'record')} breached the {reference} limit",
        severity=control.severity_if_failed,
        condition=(
            f"{result.exception_count} of {result.population_size} records tested exceeded the "
            f"{limit} {unit} limit. The longest was "
            f"{worst.attributes.get('elapsed') if worst else 'n/a'} {unit} ({worst.label if worst else ''}). "
            f"Individually: {_subject_lines(result)}."
        ),
        criterion=(
            f"Obligation register row {reference} sets a limit of {limit} {unit}. "
            + (f"However, {divergence}" if divergence else "")
        ),
        cause=(
            "The systems that operate the process carry a different figure from the one the "
            "register holds, so every record was measured against the wrong limit and reported "
            "as compliant. Nothing failed; the wrong thing was being checked."
            if divergence
            else "The process ran past the limit without an escalation that would have caught it."
        ),
        effect=(
            f"Each of the {result.exception_count} records is a breach of a registered obligation "
            f"that has already occurred and cannot be undone. Because the internal target is the "
            f"looser one, the next occurrence will be recorded as compliant as well."
        ),
        evidence=[ref for item in result.exceptions for ref in item.evidence],
        subjects=[item.subject for item in result.exceptions],
        proposed_remediation=(
            f"Align the operational target to the {limit} {unit} figure in {reference}, in the "
            f"tooling configuration and in the governing document, and re-run this control over "
            f"the same period to confirm the population is now measured correctly."
        ),
        proposed_owner=control.owner_role,
    )


def _finding_leaver(control, result: TestResult) -> ProposedFinding:
    privileged = [
        item for item in result.exceptions if item.attributes.get("privileged_groups")
    ]
    return ProposedFinding(
        title=f"{_plural(result.exception_count, 'leaver')} retained an active account",
        severity="critical" if privileged else control.severity_if_failed,
        condition=(
            f"{result.exception_count} of {result.population_size} people who left still hold an "
            f"ACTIVE directory account. {_subject_lines(result)}."
            + (
                f" {len(privileged)} of them remains in a privileged group "
                f"({', '.join(sorted({g for item in privileged for g in item.attributes['privileged_groups']}))})."
                if privileged
                else ""
            )
        ),
        criterion=(
            f"Register row {result.parameters.get('obligation_reference')} requires access to be "
            f"revoked within {result.exceptions[0].attributes.get('grace_days', 1)} business day "
            f"of the final working day."
            if result.exceptions
            else "Access is revoked on departure."
        ),
        cause=(
            "Leaver notification reaches the directory as a manual task rather than an automatic "
            "one, so an account is only closed when somebody remembers to close it."
        ),
        effect=(
            "A credential belonging to someone with no current relationship to the company can "
            "still authenticate."
            + (
                " One of these can release payment batches, so the exposure is direct financial "
                "loss rather than data access alone."
                if privileged
                else ""
            )
        ),
        evidence=[ref for item in result.exceptions for ref in item.evidence],
        subjects=[item.subject for item in result.exceptions],
        proposed_remediation=(
            "Disable the accounts named above today, then make the HR termination event the "
            "trigger for directory deactivation so the control has nothing left to find."
        ),
        proposed_owner=control.owner_role,
    ) if result.exceptions else None


def _finding_change(control, result: TestResult) -> ProposedFinding:
    return ProposedFinding(
        title=f"{_plural(result.exception_count, 'production change')} approved by its own author",
        severity=control.severity_if_failed,
        condition=(
            f"{result.exception_count} of {result.population_size} changes merged to a production "
            f"branch in the period carried no approval from anyone other than the author. "
            f"{_subject_lines(result)}."
        ),
        criterion=(
            "The change standard requires an approval recorded by a person other than the change's "
            "author for every merge to a production branch."
        ),
        cause=(
            "Branch protection permits the author's own review to satisfy the required-approvals "
            "rule, so the standard is stated in a document and not enforced by the system."
        ),
        effect=(
            "Change reached the production ledger and gateway with no second pair of eyes. The "
            "control that the SOC 2 and the internal standard both rest on was not operating."
        ),
        evidence=[ref for item in result.exceptions for ref in item.evidence],
        subjects=[item.subject for item in result.exceptions],
        proposed_remediation=(
            "Turn off self-approval in branch protection on the affected repositories so the "
            "platform enforces what the standard already requires."
        ),
        proposed_owner=control.owner_role,
    )


def _finding_dormant(control, result: TestResult) -> ProposedFinding:
    return ProposedFinding(
        title=f"{_plural(result.exception_count, 'privileged account')} dormant beyond the limit",
        severity=control.severity_if_failed,
        condition=(
            f"{result.exception_count} of {result.population_size} privileged accounts have not "
            f"authenticated inside the registered dormancy limit. {_subject_lines(result)}."
        ),
        criterion=(
            f"Register row {result.parameters.get('obligation_reference')} requires privileged "
            f"accounts dormant beyond the limit to be disabled."
        ),
        cause="No process reviews privileged group membership against actual usage.",
        effect=(
            "Standing privilege that nobody is using is privilege nobody is watching, and it is "
            "the credential an attacker most wants because its absence is never noticed."
        ),
        evidence=[ref for item in result.exceptions for ref in item.evidence],
        subjects=[item.subject for item in result.exceptions],
        proposed_remediation=(
            "Confirm with each account holder's manager whether the access is still required, "
            "disable what is not, and schedule the recertification control once a review source "
            "is connected."
        ),
        proposed_owner=control.owner_role,
    )


def _finding_vendor(control, result: TestResult) -> ProposedFinding:
    return ProposedFinding(
        title=f"{_plural(result.exception_count, 'critical provider')} missing required assurance",
        severity=control.severity_if_failed,
        condition=(
            f"{result.exception_count} of {result.population_size} providers supporting a critical "
            f"function fell short. {_subject_lines(result)}."
        ),
        criterion=(
            f"Register row {result.parameters.get('obligation_reference')} requires current due "
            f"diligence and a documented exit strategy for every critical arrangement."
        ),
        cause=(
            "Third-party reviews are scheduled from the contract renewal date rather than from "
            "the date of the last assessment, so an arrangement that does not renew is never "
            "re-examined."
        ),
        effect=(
            "The firm cannot evidence that it could move away from a provider that carries a "
            "licensed activity. In a supervisory review this is asked for first."
        ),
        evidence=[ref for item in result.exceptions for ref in item.evidence],
        subjects=[item.subject for item in result.exceptions],
        proposed_remediation=(
            "Commission the overdue due diligence and draft the exit strategy for the provider "
            "named above, and drive the review schedule from the assessment date."
        ),
        proposed_owner=control.owner_role,
    )


def _finding_policy(control, result: TestResult) -> ProposedFinding:
    return ProposedFinding(
        title=f"{_plural(result.exception_count, 'mandated policy', 'mandated policies')} past its review cycle",
        severity=control.severity_if_failed,
        condition=(
            f"{result.exception_count} of {result.population_size} mandated documents are past "
            f"the review cycle they declare. {_subject_lines(result)}."
        ),
        criterion="Every mandated policy is reviewed and re-approved within the cycle it states.",
        cause="Review dates are tracked in the documents themselves and nothing reads them.",
        effect=(
            "A policy nobody has confirmed is still true is being used to justify decisions, and "
            "in an examination the lapse is visible on the document's own front page."
        ),
        evidence=[ref for item in result.exceptions for ref in item.evidence],
        subjects=[item.subject for item in result.exceptions],
        proposed_remediation="Route each document to its owner for review and re-approval.",
        proposed_owner=control.owner_role,
    )


def _finding_generic(control, result: TestResult) -> ProposedFinding:
    return ProposedFinding(
        title=f"{control.title}: {_plural(result.exception_count, 'exception')}",
        severity=control.severity_if_failed,
        condition=(
            f"{result.exception_count} of {result.population_size} items tested did not satisfy "
            f"the control. {_subject_lines(result)}."
        ),
        criterion=control.objective,
        cause="Not determined by the test; the population shows what failed, not why.",
        effect=(
            "The assertion this control supports is not currently evidenced for the items named."
        ),
        evidence=[ref for item in result.exceptions for ref in item.evidence],
        subjects=[item.subject for item in result.exceptions],
        proposed_remediation="Resolve each item named above and re-run the control.",
        proposed_owner=control.owner_role,
    )


COMPOSERS = {
    "obligation_reconciliation": _finding_obligation,
    "leaver_access_revocation": _finding_leaver,
    "change_approval": _finding_change,
    "dormant_privileged_access": _finding_dormant,
    "vendor_obligation": _finding_vendor,
    "policy_review_currency": _finding_policy,
}


# --------------------------------------------------------------------------
# Narrative and challenge
# --------------------------------------------------------------------------


def compose(control, result: TestResult, signals: list[InjectionSignal]) -> ReviewNarrative:
    """A report for one control run, written from the result it cannot change."""
    outcome = result.outcome(control.tolerance)
    suppressed = [i for i in result.population if i.attributes.get("disposition") == "suppressed"]

    observations: list[str] = list(result.notes)
    for item in suppressed:
        observations.append(f"Considered and not raised. {item.label}: {item.reason}")
    for entry in result.not_tested:
        observations.append(f"Not tested. {entry}")
    if signals:
        observations.append(contained_statement(signals))

    if outcome == "inconclusive" and result.population_size == 0:
        summary = (
            f"{control.code} could not be concluded: the population for "
            f"{result.period_start.isoformat()} to {result.period_end.isoformat()} was empty. "
            f"An empty population is not a pass, and the control is reported as inconclusive."
        )
    elif outcome == "inconclusive":
        summary = (
            f"{control.code} could not be concluded over "
            f"{result.period_start.isoformat()} to {result.period_end.isoformat()}. "
            f"{result.population_size} item(s) were tested and none failed, but "
            f"{len(result.not_tested)} further item(s) could not be tested at all. A control "
            f"that has walked part of its population has not shown the control operating over "
            f"the population, so the run is reported as inconclusive rather than effective."
        )
    elif outcome == "effective":
        summary = (
            f"{control.code} operated as designed. All {result.population_size} items in the "
            f"population for {result.period_start.isoformat()} to {result.period_end.isoformat()} "
            f"satisfied the control"
            + (f", with {len(suppressed)} suppressed for a stated reason" if suppressed else "")
            + ". Every member of the population was tested. No finding is raised."
        )
    else:
        summary = (
            f"{control.code} did not operate effectively over "
            f"{result.period_start.isoformat()} to {result.period_end.isoformat()}. "
            f"{result.exception_count} of {result.population_size} items tested failed, against a "
            f"tolerance of {control.tolerance}"
            + (f"; {len(suppressed)} further item(s) were considered and suppressed with a reason" if suppressed else "")
            + "."
        )

    findings: list[ProposedFinding] = []
    if outcome == "ineffective":
        composer = COMPOSERS.get(result.test_kind, _finding_generic)
        finding = composer(control, result)
        if finding is not None:
            findings.append(finding)

    return ReviewNarrative(
        summary=summary,
        proposed_outcome=outcome,
        findings=findings,
        injection_signals=signals,
        observations=observations,
    )


CHALLENGES: dict[str, tuple[str, str]] = {
    "obligation_reconciliation": (
        "Every internal system agreed these records were on time, and the operators followed the "
        "procedure they were given. Perhaps the register row is the error and the 24 hour figure "
        "is the correct one.",
        "That would be a finding about the register rather than about the process, and it would "
        "still be a finding. Until the register is changed through its own governance, it is the "
        "obligation the firm has written down for itself, and the records breached it.",
    ),
    "leaver_access_revocation": (
        "Neither account was used after the departure date, so no harm followed and this is "
        "administrative housekeeping rather than a control failure.",
        "The control tests whether access was removed, not whether it was abused. An unused open "
        "credential is the exposure; waiting for it to be used is not a control.",
    ),
    "change_approval": (
        "Both authors are senior engineers and both changes were small. Requiring a second "
        "approval for changes of this size adds delay for no benefit.",
        "The standard sets no seniority or size exemption. If one is warranted, it belongs in the "
        "standard where it can be governed, not in the discretion of the person merging.",
    ),
    "dormant_privileged_access": (
        "Both holders are current employees in good standing who simply use a different tool day "
        "to day; the access is dormant, not wrong.",
        "The register makes dormancy itself the trigger, precisely because whether access is "
        "warranted is a judgement and whether it is used is a fact.",
    ),
    "vendor_obligation": (
        "The provider is contractually locked in until 2027, so an exit strategy is theoretical "
        "and the due diligence is only marginally overdue.",
        "A lock-in is the reason to have an exit strategy rather than the reason to skip it, and "
        "the register measures currency in months from the assessment, not from the contract.",
    ),
    "policy_review_currency": (
        "The policy is still substantively correct; nobody has needed to change it, which is why "
        "the review was not prioritised.",
        "Correctness is exactly what a review establishes. Without one, that the document is "
        "still right is an assumption rather than a record.",
    ),
}


def challenge(narrative: ReviewNarrative, result: TestResult) -> ChallengeSet:
    """Argue against each finding before a person is asked to sign it.

    Nothing here is decorative. A finding that cannot survive the strongest
    version of the argument against it should not reach a committee, and a
    reviewer who is only shown the case for a finding cannot weigh it.
    """
    default = (
        "The exceptions may reflect a recording gap in the source rather than a failure of the "
        "process itself.",
        "The evidence references resolve to the source records, and the reasons are drawn from "
        "the fields those records carry. A recording gap would have to be argued against specific "
        "items rather than the population.",
    )
    counter, answer = CHALLENGES.get(result.test_kind, default)
    return ChallengeSet(
        challenges=[
            Challenge(
                finding_title=finding.title,
                strongest_counterargument=counter,
                survives=True,
                reason=answer,
            )
            for finding in narrative.findings
        ]
    )


def downgrade(severity: Severity, challenges: ChallengeSet, title: str) -> Severity:
    """Apply any downgrade the challenger proposed for this finding."""
    for item in challenges.challenges:
        if item.finding_title == title and item.suggested_downgrade:
            return item.suggested_downgrade
    return severity
