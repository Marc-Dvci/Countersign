"""What reaches the findings queue is decided by the count.

The outcome has been protected from the model since the first commit here. The
findings are what a person actually acts on, and they were not protected in the
same way: a review could conclude "ineffective" and then write about two of the
three exceptions, or a challenger could argue a finding away, and the third
exception would have no first-class record anywhere. Nobody downstream would
know to look for it, because the absence is what is invisible.

Two rules close that, and these tests hold them.

1. Every exception of an ineffective run is represented by a finding. Anything a
   review left uncovered is composed from the test result and marked
   `deterministic`.
2. A challenge is an argument recorded beside a finding. It never removes one,
   and it never rewrites its severity.
"""

from __future__ import annotations

from datetime import date

from countersign.domain import (
    Challenge,
    ChallengeSet,
    ProposedFinding,
    ReviewNarrative,
)
from countersign.workflow import InvocationTrace, cover_every_exception

from .conftest import QUARTER


def _control(store, code):
    return store.control("kestrel", code)


def _result(app, code):
    from countersign.control_tests import run_test

    row = _control(app.store, code)
    return row, run_test(row["test_kind"], app.connectors("kestrel"), row["parameters"], *QUARTER)


def _typed(app, code):
    row, result = _result(app, code)
    return app._as_control(row), result


def _finding(title, subjects, severity="low"):
    return ProposedFinding(
        title=title,
        severity=severity,
        condition="c",
        criterion="c",
        cause="c",
        effect="e",
        subjects=subjects,
    )


# ------------------------------------------------- every exception is covered


def test_a_review_that_writes_nothing_still_raises_the_exceptions(seeded):
    """The worst case: an ineffective run whose review produced no findings."""
    control, result = _typed(seeded, "IAM-JML-01")
    assert result.outcome(control.tolerance) == "ineffective"

    report = ReviewNarrative(summary="s", proposed_outcome="ineffective", findings=[])
    trace = InvocationTrace()
    cover_every_exception(report, control, result, trace)

    covered = {subject for finding in report.findings for subject in finding.subjects}
    assert covered == {item.subject for item in result.exceptions}
    assert all(finding.origin == "deterministic" for finding in report.findings)
    assert any("uncovered_exceptions" in event["event"] for event in trace.events)
    assert any("decided by the count" in line for line in report.observations)


def test_a_review_that_covers_some_exceptions_has_the_rest_composed(seeded):
    control, result = _typed(seeded, "IAM-JML-01")
    exceptions = result.exceptions
    assert len(exceptions) > 1, "this test needs a control with more than one exception"

    kept, dropped = exceptions[0], exceptions[1:]
    report = ReviewNarrative(
        summary="s",
        proposed_outcome="ineffective",
        findings=[_finding("Only the first one", [kept.subject])],
    )
    cover_every_exception(report, control, result, InvocationTrace())

    covered = {subject for finding in report.findings for subject in finding.subjects}
    assert {item.subject for item in dropped} <= covered
    origins = {finding.title: finding.origin for finding in report.findings}
    assert origins["Only the first one"] == "agent"
    assert "deterministic" in origins.values()


def test_a_complete_review_is_left_alone(seeded):
    """The reconciliation is a floor, not a rewrite."""
    control, result = _typed(seeded, "IAM-JML-01")
    report = ReviewNarrative(
        summary="s",
        proposed_outcome="ineffective",
        findings=[_finding("All of them", [item.subject for item in result.exceptions])],
    )
    cover_every_exception(report, control, result, InvocationTrace())
    assert [finding.title for finding in report.findings] == ["All of them"]
    assert report.observations == []


def test_an_effective_run_gains_nothing(seeded):
    control, result = _typed(seeded, "PAY-4EYES-01")
    assert result.outcome(control.tolerance) == "effective"
    report = ReviewNarrative(summary="s", proposed_outcome="effective", findings=[])
    cover_every_exception(report, control, result, InvocationTrace())
    assert report.findings == []


def test_a_model_cannot_claim_to_be_the_deterministic_half(seeded):
    """`origin` is stamped by the pipeline, not accepted from the payload."""
    control, result = _typed(seeded, "IAM-JML-01")
    impostor = _finding("I wrote this myself", [item.subject for item in result.exceptions])
    impostor.origin = "deterministic"
    report = ReviewNarrative(summary="s", proposed_outcome="ineffective", findings=[impostor])
    cover_every_exception(report, control, result, InvocationTrace(), from_model=True)
    assert report.findings[0].origin == "agent"


# ---------------------------------------- a challenge argues, it does not veto


def test_a_defeated_finding_is_still_in_the_queue(seeded):
    """End to end against the store: the reviewer has to see it either way."""
    row, result = _result(seeded, "IAM-DORM-01")
    title = "Argued against"
    report = ReviewNarrative(
        summary="s",
        proposed_outcome="ineffective",
        findings=[
            _finding(title, [item.subject for item in result.exceptions], severity="critical")
        ],
    )
    challenges = ChallengeSet(
        challenges=[
            Challenge(
                finding_title=title,
                strongest_counterargument="Both holders are current employees in good standing.",
                survives=False,
                reason="The access is dormant rather than wrong.",
                suggested_downgrade="low",
            )
        ]
    )

    seeded.store.record_run(
        "kestrel", row, result, report, challenges, [], "demo", date(2026, 9, 1)
    )

    queue = seeded.store.findings("kestrel", status="open")
    raised = next(finding for finding in queue if finding["title"] == title)
    assert raised["challenge_survives"] == 0
    assert raised["severity"] == "critical", "the severity it was raised at, not the suggested one"
    assert raised["suggested_severity"] == "low"
    assert raised["challenge"]["strongest_counterargument"]


def test_the_challenge_is_written_into_the_audit_chain(seeded):
    row, result = _result(seeded, "IAM-DORM-01")
    title = "Argued against, audibly"
    report = ReviewNarrative(
        summary="s",
        proposed_outcome="ineffective",
        findings=[_finding(title, [item.subject for item in result.exceptions])],
    )
    challenges = ChallengeSet(
        challenges=[
            Challenge(
                finding_title=title,
                strongest_counterargument="Nobody used the access.",
                survives=False,
                reason="Use is not the trigger.",
            )
        ]
    )
    seeded.store.record_run(
        "kestrel", row, result, report, challenges, [], "demo", date(2026, 9, 1)
    )

    events = [
        entry for entry in seeded.store.audit_events() if entry["event"] == "finding.challenged"
    ]
    assert events and events[-1]["actor"] == "agent:challenger"
    intact, detail = seeded.store.audit_intact()
    assert intact, detail
