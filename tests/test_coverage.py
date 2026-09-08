"""Untested is not passing.

Countersign's population is the whole population, so the interesting failure is
not a wrong count. It is a *short* count that still reads as a clean one: a
control that walked eight of eleven members, found nothing wrong with the eight,
reported ``effective``, and closed the finding that was about the ninth.

These tests hold the rule that makes that impossible. A run with anything in
``not_tested`` cannot conclude ``effective``, and cannot close a finding.
"""

from __future__ import annotations

from datetime import date

from countersign.domain import PopulationItem, TestResult

PERIOD = {"period_start": date(2026, 1, 1), "period_end": date(2026, 2, 1)}


def result(passed: int, failed: int = 0, untested: int = 0) -> TestResult:
    return TestResult(
        test_kind="k",
        population=[PopulationItem(subject=f"ok{i}", label="x", passed=True) for i in range(passed)]
        + [PopulationItem(subject=f"no{i}", label="x", passed=False) for i in range(failed)],
        not_tested=[f"member{i}: no evidence could be served" for i in range(untested)],
        **PERIOD,
    )


# ------------------------------------------------------------ the rule


def test_a_complete_clean_run_is_effective():
    assert result(passed=5).outcome(tolerance=0) == "effective"


def test_an_incomplete_clean_run_is_not_effective():
    """The whole point. Eight passes and one member nobody could look at."""
    partial = result(passed=8, untested=1)
    assert partial.exception_count == 0
    assert partial.outcome(tolerance=0) == "inconclusive"


def test_an_incomplete_run_that_already_failed_stays_ineffective():
    """Missing coverage does not rescue a control that has already failed."""
    assert result(passed=3, failed=2, untested=4).outcome(tolerance=0) == "ineffective"


def test_tolerance_does_not_absorb_untested_members():
    """A tolerance is a budget for exceptions, not a budget for ignorance."""
    assert result(passed=3, failed=1, untested=1).outcome(tolerance=1) == "inconclusive"


def test_coverage_is_reported_as_well_as_counted():
    partial = result(passed=2, untested=3)
    assert not partial.coverage_complete
    assert "3 member(s)" in partial.coverage_note()
    assert result(passed=2).coverage_note() == ""


# ------------------------------------------- the rule, through the product


def test_an_incomplete_run_cannot_close_a_finding(seeded, monkeypatch):
    """The defect this rule exists to prevent, end to end against the store.

    IAM-JML-01 has two open findings, one for each leaver whose account is still
    live. The estate is then fixed for both, *and* one leaver's directory account
    disappears entirely, which is exactly what a half-migrated directory looks
    like. The remaining population is clean, so the naive answer is "effective,
    close the findings". The run has to refuse both.
    """
    connectors = seeded.connectors("kestrel")
    identity = connectors["identity"]
    original = identity.fetch
    vanished = "a.kowalski@kestrelpay.eu"

    def half_migrated(dataset, since=None):
        rows = original(dataset, since)
        if dataset == "users":
            rows = [
                {**row, "status": "DEPROVISIONED"}
                if row["email"] == "r.delacroix@kestrelpay.eu"
                else row
                for row in rows
                if row["email"] != vanished
            ]
        return rows

    monkeypatch.setattr(identity, "fetch", half_migrated)
    outcome = seeded.run_control("kestrel", "IAM-JML-01", date(2026, 9, 1), 91)

    assert outcome["exceptions"] == 0, "every member that could be tested passed"
    assert outcome["outcome"] == "inconclusive", "a partial population is not a pass"
    assert outcome["closed_findings"] == [], "a partial population cannot close a finding"

    run = seeded.store.run("kestrel", outcome["run_id"])
    assert any(vanished in entry for entry in run["not_tested"])
    assert "inconclusive" in run["summary"]

    still_open = [f for f in seeded.store.findings("kestrel") if f["control_code"] == "IAM-JML-01"]
    assert still_open and all(f["status"] == "open" for f in still_open)


def test_full_coverage_of_the_same_fix_does_close_it(seeded, monkeypatch):
    """The control against the one above: the only difference is coverage."""
    connectors = seeded.connectors("kestrel")
    identity = connectors["identity"]
    original = identity.fetch

    def fixed(dataset, since=None):
        rows = original(dataset, since)
        if dataset == "users":
            rows = [
                {**row, "status": "DEPROVISIONED"}
                if row["email"] in {"r.delacroix@kestrelpay.eu", "a.kowalski@kestrelpay.eu"}
                else row
                for row in rows
            ]
        return rows

    monkeypatch.setattr(identity, "fetch", fixed)
    outcome = seeded.run_control("kestrel", "IAM-JML-01", date(2026, 9, 1), 91)
    assert outcome["outcome"] == "effective"
    assert outcome["closed_findings"]
