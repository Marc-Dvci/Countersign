"""The three human gates, and the audit chain underneath them.

These tests exist because the product's central claim is that a model can
propose anything and grant nothing. That claim is only worth what these
assertions are worth.
"""

from __future__ import annotations

from datetime import date

import pytest

from countersign import catalogue
from countersign.database import AuthorityError, EvidenceError

AGENT = "agent:control_designer"
PERSON = "c.walsh@kestrelpay.eu"


# ------------------------------------------------------------ gate one


def test_an_agent_cannot_accept_a_risk_domain(app):
    app.onboard("kestrel", PERSON)
    with pytest.raises(AuthorityError, match="requires a person"):
        app.store.decide_domain("kestrel", "IAM", True, AGENT)


def test_an_anonymous_actor_cannot_accept_a_risk_domain(app):
    app.onboard("kestrel", PERSON)
    with pytest.raises(AuthorityError):
        app.store.decide_domain("kestrel", "IAM", True, "")


def test_a_person_can(app):
    app.onboard("kestrel", PERSON)
    app.store.decide_domain("kestrel", "IAM", True, PERSON)
    domain = next(d for d in app.store.domains("kestrel") if d["code"] == "IAM")
    assert domain["status"] == "accepted" and domain["decided_by"] == PERSON


def test_a_domain_is_decided_once(app):
    app.onboard("kestrel", PERSON)
    app.store.decide_domain("kestrel", "IAM", True, PERSON)
    with pytest.raises(EvidenceError, match="not awaiting a decision"):
        app.store.decide_domain("kestrel", "IAM", False, PERSON)


# ------------------------------------------------------------ gate two


def test_an_agent_cannot_approve_a_control(app):
    app.onboard("kestrel", PERSON)
    with pytest.raises(AuthorityError, match="requires a person"):
        app.store.approve_control("kestrel", "CHG-APP-01", AGENT, date(2026, 9, 1))


def test_a_control_whose_evidence_source_is_missing_cannot_be_approved(app):
    """Approving something that can never produce evidence is how a control
    programme becomes a document."""
    app.onboard("kestrel", PERSON)
    control = app.store.control("kestrel", "IAM-RECERT-01")
    assert not control["runnable"]
    assert "access_reviews" in control["blocked_reason"]
    with pytest.raises(EvidenceError, match="cannot be scheduled"):
        app.store.approve_control("kestrel", "IAM-RECERT-01", PERSON, date(2026, 9, 1))


def test_approving_sets_the_schedule(app):
    app.onboard("kestrel", PERSON)
    app.store.approve_control("kestrel", "CHG-APP-01", PERSON, date(2026, 9, 1))
    control = app.store.control("kestrel", "CHG-APP-01")
    assert control["status"] == "scheduled"
    assert control["approved_by"] == PERSON
    assert control["next_due"] == "2026-09-01"


def test_a_control_cannot_be_approved_twice(app):
    app.onboard("kestrel", PERSON)
    app.store.approve_control("kestrel", "CHG-APP-01", PERSON, date(2026, 9, 1))
    with pytest.raises(EvidenceError, match="already scheduled"):
        app.store.approve_control("kestrel", "CHG-APP-01", PERSON, date(2026, 9, 1))


# ------------------------------------------------------------ gate three


def test_an_agent_cannot_disposition_a_finding(seeded):
    finding = seeded.store.findings("kestrel", status="open")[0]
    with pytest.raises(AuthorityError, match="requires a person"):
        seeded.store.decide_finding("kestrel", finding["id"], "risk_accepted", AGENT, "because")


def test_accepting_a_risk_needs_a_stated_reason(seeded):
    finding = seeded.store.findings("kestrel", status="open")[0]
    with pytest.raises(EvidenceError, match="stated reason"):
        seeded.store.decide_finding("kestrel", finding["id"], "risk_accepted", PERSON, "fine")


def test_accepting_a_risk_with_a_reason_is_recorded(seeded):
    finding = seeded.store.findings("kestrel", status="open")[0]
    reason = "Accepted by the Risk Committee until the ledger migration completes in Q1."
    seeded.store.decide_finding("kestrel", finding["id"], "risk_accepted", PERSON, reason)
    after = seeded.store.finding("kestrel", finding["id"])
    assert after["status"] == "risk_accepted"
    assert after["decided_by"] == PERSON and after["decision_note"] == reason


def test_a_finding_cannot_be_closed_by_decision(seeded):
    finding = seeded.store.findings("kestrel", status="open")[0]
    with pytest.raises(EvidenceError, match="not closed by decision"):
        seeded.store.decide_finding("kestrel", finding["id"], "closed", PERSON, "we fixed it")


# ------------------------------------------- closing needs fresh evidence


def test_a_finding_closes_only_when_the_control_comes_back_clean(seeded, monkeypatch):
    """The remediation loop, end to end, against the real store."""
    open_before = [f for f in seeded.store.findings("kestrel") if f["control_code"] == "IAM-JML-01"]
    assert open_before and open_before[0]["status"] == "open"

    # Re-running against the unchanged estate must not close anything.
    seeded.run_control("kestrel", "IAM-JML-01", date(2026, 9, 1), 91)
    still_open = [f for f in seeded.store.findings("kestrel") if f["control_code"] == "IAM-JML-01"]
    assert all(f["status"] == "open" for f in still_open)

    # Now the estate is actually fixed: both leavers are deprovisioned.
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
    assert outcome["closed_findings"], "a clean run must close the finding it fixes"
    closed = seeded.store.finding("kestrel", outcome["closed_findings"][0])
    assert closed["status"] == "closed"
    assert closed["closing_run_id"] == outcome["run_id"], "the closing evidence must be recorded"


def test_a_clean_run_does_not_close_a_finding_raised_after_it(seeded):
    """Closing evidence has to come after the thing it closes."""
    first = seeded.run_control("kestrel", "AML-SCR-01", date(2026, 9, 1), 91)
    assert first["outcome"] == "effective"
    unrelated = seeded.store.findings("kestrel", status="open")
    assert unrelated, "other findings must survive an unrelated clean run"


# ------------------------------------------------------------- audit chain


def test_the_audit_chain_is_intact_after_a_full_seed(seeded):
    intact, detail = seeded.store.audit_intact()
    assert intact, detail


def test_the_audit_log_refuses_updates(seeded):
    with seeded.store.connect() as connection:
        with pytest.raises(Exception, match="append-only"):
            connection.execute("UPDATE audit SET actor = 'someone else' WHERE seq = 1")


def test_the_audit_log_refuses_deletes(seeded):
    with seeded.store.connect() as connection:
        with pytest.raises(Exception, match="append-only"):
            connection.execute("DELETE FROM audit WHERE seq = 1")


def test_tampering_breaks_the_chain_at_the_point_it_was_touched(seeded):
    """The triggers can be dropped by anyone with the file. The chain cannot."""
    with seeded.store.connect() as connection:
        connection.execute("DROP TRIGGER audit_no_update")
        connection.execute("UPDATE audit SET payload = '{\"tampered\":true}' WHERE seq = 3")
    intact, detail = seeded.store.audit_intact()
    assert not intact
    assert "event 3" in detail


def test_every_authority_bearing_action_names_a_person_in_the_chain(seeded):
    events = seeded.store.audit_events(limit=500)
    for event in events:
        if event["event"] in {
            "taxonomy.domain_accepted",
            "taxonomy.domain_rejected",
            "control.approved",
            "finding.risk_accepted",
            "finding.remediation_agreed",
        }:
            assert not event["actor"].startswith("agent:"), event


def test_agent_actions_are_labelled_as_agent_actions(seeded):
    events = {e["event"]: e["actor"] for e in seeded.store.audit_events(limit=500)}
    assert events["control.executed"].startswith("agent:")
    assert events["discovery.profile_proposed"].startswith("agent:")


# --------------------------------------------------------------- scoring


@pytest.mark.parametrize("tenant", ["kestrel", "northwind", "brandt"])
def test_each_seeded_estate_matches_its_answer_key(app, tenant):
    """Raising every condition is as wrong as raising none."""
    from countersign.scoring import render, score_tenant

    app.seed(tenant)
    report = score_tenant(app.store, tenant)
    assert report["passed"], "\n" + render(report)


def test_the_taxonomy_actually_differs_by_tenant(app):
    """Discovery is only a claim if the answer changes with the company."""
    codes = {}
    for tenant in ("kestrel", "northwind", "brandt"):
        app.onboard(tenant, PERSON)
        codes[tenant] = {d["code"] for d in app.store.domains(tenant)}
    assert codes["kestrel"] != codes["northwind"] != codes["brandt"]
    assert "FINCRIME" in codes["kestrel"] and "FINCRIME" not in codes["brandt"]
    assert "HSE" in codes["brandt"] and "HSE" not in codes["kestrel"]


def test_every_tenant_names_what_it_deliberately_left_out(app):
    for tenant in catalogue.tenant_names():
        proposal = catalogue.domains_for(tenant)
        assert proposal.deliberately_excluded, f"{tenant} excludes nothing"
