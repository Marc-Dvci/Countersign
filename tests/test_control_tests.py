"""The deterministic tests, against the seeded estate.

These are the tests that matter most, because everything Countersign publishes
is decided here. A control test that quietly gets the population wrong produces
a confident, evidenced, wrong report.
"""

from __future__ import annotations

from datetime import date

import pytest

from countersign.connectors import ConnectorError, build_connectors
from countersign.control_tests import REGISTRY, run_test
from countersign.domain import PopulationItem, TestResult

from .conftest import AS_OF, QUARTER


def result_for(connectors, kind, parameters):
    return run_test(kind, connectors, parameters, *QUARTER)


# ---------------------------------------------------------------- outcomes


def test_outcome_is_counted_not_asserted():
    """The single place an outcome is decided, exercised directly."""
    passing = [PopulationItem(subject=str(i), label="x", passed=True) for i in range(5)]
    failing = [PopulationItem(subject="bad", label="x", passed=False)]
    result = TestResult(
        test_kind="k", period_start=date(2026, 1, 1), period_end=date(2026, 2, 1),
        population=passing + failing,
    )
    assert result.outcome(tolerance=0) == "ineffective"
    assert result.outcome(tolerance=1) == "effective"


def test_empty_population_is_inconclusive_not_effective():
    """An empty population is the most dangerous pass in assurance work."""
    result = TestResult(
        test_kind="k", period_start=date(2026, 1, 1), period_end=date(2026, 2, 1), population=[]
    )
    assert result.outcome(tolerance=0) == "inconclusive"


# -------------------------------------------------------- planted conditions


def test_obligation_reconciliation_measures_against_the_register(connectors):
    """Three incidents breach a 4-hour register limit while three systems say 24."""
    result = result_for(
        connectors,
        "obligation_reconciliation",
        {
            "obligation_reference": "DORA-19-1",
            "dataset": "jira.issues",
            "filter": {"project": "INC", "classification": "major"},
            "start_field": "classified_major_at",
            "end_field": "regulator_notified_at",
            "internal_declaration": {
                "dataset": "jira.automation_settings",
                "field": "regulator_notification_target_hours",
            },
        },
    )
    assert result.population_size == 4, "the non-major incident must not be in the population"
    assert result.exception_count == 3
    assert all(item.attributes["limit"] == 4 for item in result.population)
    assert result.notes, "the divergence between the register and the tooling must be reported"
    assert "24" in result.notes[0] and "4 hours" in result.notes[0]


def test_a_test_cannot_invent_a_threshold(connectors):
    """An obligation that is not in the register cannot be tested against."""
    with pytest.raises(ConnectorError, match="not in the register"):
        result_for(
            connectors,
            "obligation_reconciliation",
            {
                "obligation_reference": "MADE-UP-99",
                "dataset": "jira.issues",
                "start_field": "created_at",
                "end_field": "resolved_at",
            },
        )


def test_leaver_control_finds_two_and_excuses_the_rehire(connectors):
    result = result_for(connectors, "leaver_access_revocation", {"obligation_reference": "INT-ACC-REVOKE"})
    assert result.population_size == 3
    assert result.exception_count == 2
    suppressed = [i for i in result.population if i.attributes.get("disposition") == "suppressed"]
    assert len(suppressed) == 1
    assert suppressed[0].subject == "s.byrne@kestrelpay.eu"
    assert "Rehired" in suppressed[0].reason, "a suppression without a reason is a silent pass"
    privileged = [i for i in result.exceptions if i.attributes["privileged_groups"]]
    assert privileged and "payments-operators" in privileged[0].attributes["privileged_groups"]


def test_change_control_suppresses_the_approved_emergency(connectors):
    result = result_for(connectors, "change_approval", {"emergency_label": "emergency"})
    assert result.exception_count == 2
    subjects = {item.subject for item in result.exceptions}
    assert subjects == {
        "kestrelpay/kestrel-gateway#398",
        "kestrelpay/kestrel-ledger#421",
    }
    emergency = next(
        i for i in result.population if i.subject == "kestrelpay/kestrel-ledger#412"
    )
    assert emergency.passed
    assert emergency.attributes["disposition"] == "suppressed"
    assert "CHG-2210" in emergency.reason, "the suppressing ticket must be cited"


def test_dormant_privileged_accounts(connectors):
    result = result_for(connectors, "dormant_privileged_access", {"obligation_reference": "INT-ACC-DORMANT"})
    assert result.exception_count == 2
    assert all(item.attributes["idle_days"] > 90 for item in result.exceptions)


def test_vendor_obligation_reports_both_failures_on_one_line(connectors):
    result = result_for(
        connectors, "vendor_obligation", {"obligation_reference": "DORA-28-8"}
    )
    assert result.population_size == 3, "only providers supporting a critical function"
    assert result.exception_count == 1
    reason = result.exceptions[0].reason.lower()
    assert "due diligence" in reason and "exit strategy" in reason


def test_policy_currency(connectors):
    result = result_for(connectors, "policy_review_currency", {"mandated_only": True})
    assert result.exception_count == 1
    assert result.exceptions[0].subject == "POL-AML-002"


def test_controls_that_must_come_back_clean(connectors):
    """Raising everything is as wrong as raising nothing."""
    payments = result_for(connectors, "dual_authorisation", {"obligation_reference": "INT-PAY-4EYES"})
    assert payments.population_size > 0 and payments.exception_count == 0
    assert payments.outcome(0) == "effective"

    screening = result_for(connectors, "screening_coverage", {"obligation_reference": "AML-SCR-01"})
    assert screening.population_size == 212 and screening.exception_count == 0


def test_dual_authorisation_only_walks_the_population_above_the_threshold(connectors):
    result = result_for(connectors, "dual_authorisation", {"obligation_reference": "INT-PAY-4EYES"})
    assert all(item.attributes["amount"] >= 50000 for item in result.population)


# ------------------------------------------------------- other tenants


def test_recurrence_interval_counts_subjects_not_events():
    """A site that was never inspected produces no event and must still fail."""
    connectors = build_connectors("brandt", allow_live=False)
    result = run_test(
        "recurrence_interval",
        connectors,
        {
            "obligation_reference": "INT-HSE-INSP",
            "dataset": "jira.issues",
            "group_field": "work_area",
            "date_field": "inspected_at",
            "filter": {"project": "HSE"},
            "expected_subjects": ["Press shop line 2", "Coating bay", "Furnace hall", "Solvent store", "Paint store"],
        },
        *QUARTER,
    )
    assert result.population_size == 5
    missing = next(i for i in result.population if i.subject == "Paint store")
    assert not missing.passed and "no event of this kind" in missing.reason.lower()


def test_required_field_present_filters_to_the_triggering_records():
    connectors = build_connectors("brandt", allow_live=False)
    result = run_test(
        "required_field_present",
        connectors,
        {
            "obligation_reference": "DUAL-USE-ART3",
            "dataset": "jira.issues",
            "required_field": "export_authorisation",
            "filter": {"project": "EXP", "dual_use_listed": True},
        },
        *QUARTER,
    )
    assert result.population_size == 2, "unlisted shipments do not trigger the obligation"
    assert result.exception_count == 1
    assert result.exceptions[0].subject == "EXP-3312"


# ------------------------------------------------------------- the registry


def test_an_unregistered_test_is_refused(connectors):
    with pytest.raises(ConnectorError, match="not a registered test"):
        run_test("do_what_i_mean", connectors, {}, *QUARTER)


def test_parameters_are_validated_against_the_registry(connectors):
    with pytest.raises(ConnectorError, match="missing required parameter"):
        run_test("vendor_obligation", connectors, {}, *QUARTER)
    with pytest.raises(ConnectorError, match="unknown parameter"):
        run_test(
            "vendor_obligation",
            connectors,
            {"obligation_reference": "DORA-28-8", "tolerate_everything": True},
            *QUARTER,
        )


def test_every_registered_test_is_described_for_the_agent():
    """The catalogue an agent is shown must cover everything it may propose."""
    from countersign.control_tests import describe_registry

    described = {entry["test_kind"] for entry in describe_registry()}
    assert described == set(REGISTRY)
    for entry in describe_registry():
        assert entry["what_it_does"], f"{entry['test_kind']} has no description"


# ------------------------------------------------------------ connectors


def test_a_missing_dataset_raises_rather_than_returning_nothing(connectors):
    """An empty list reads as 'nothing wrong'. Raising reads as 'not tested'."""
    with pytest.raises(ConnectorError, match="access_reviews"):
        connectors["identity"].fetch("access_reviews")


def test_evidence_digests_change_when_the_row_changes():
    from countersign.connectors import digest

    row = {"id": "PAY-1", "amount": 100}
    assert digest(row) == digest({"amount": 100, "id": "PAY-1"}), "key order must not matter"
    assert digest(row) != digest({"id": "PAY-1", "amount": 101})


def test_results_do_not_depend_on_the_wall_clock(connectors):
    """The same period must produce the same answer whenever it is run."""
    first = result_for(connectors, "change_approval", {})
    second = run_test("change_approval", connectors, {}, *QUARTER)
    assert [(i.subject, i.passed) for i in first.population] == [
        (i.subject, i.passed) for i in second.population
    ]


def test_period_bounds_are_respected(connectors):
    """A one-day window over a quarter of activity finds almost nothing."""
    narrow = run_test("change_approval", connectors, {}, AS_OF, AS_OF)
    wide = result_for(connectors, "change_approval", {})
    assert narrow.population_size < wide.population_size
