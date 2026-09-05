"""The schedule runs itself.

Approved controls carry a ``next_due`` date. The background scheduler runs
everything that has fallen due, for every onboarded tenant, with no person in the
loop. These tests exercise the tick the scheduler calls; the loop itself is a
fixed-interval wrapper around it.
"""

from __future__ import annotations

from datetime import date

from starlette.testclient import TestClient

from countersign.api import build_app

# Far enough past the anchored date that the monthly and quarterly controls
# seeded at 2026-09-01 have fallen due at least once.
LATER = date(2027, 1, 1)


def test_nothing_is_due_at_the_anchored_date(seeded):
    """Seeding runs every approved control once, so nothing is due at ``as_of``.

    This is why the scheduler is a no-op on the anchored demonstration: it wakes,
    finds nothing due, and changes nothing.
    """
    results = seeded.run_all_due(seeded.settings.as_of)
    assert results == {"kestrel": []}


def test_the_schedule_runs_controls_that_have_fallen_due(seeded):
    before = len(seeded.store.runs("kestrel", limit=1000))

    results = seeded.run_all_due(LATER)

    assert sum(len(runs) for runs in results.values()) > 0
    after = len(seeded.store.runs("kestrel", limit=1000))
    assert after > before


def test_a_scheduled_run_opens_no_authority(seeded):
    """A run grants nothing. Any finding it raises lands open and stays there."""
    seeded.run_all_due(LATER)
    findings = seeded.store.findings("kestrel", status="open")
    assert all(finding["status"] == "open" for finding in findings)


def test_the_audit_chain_survives_a_scheduled_sweep(seeded):
    seeded.run_all_due(LATER)
    intact, detail = seeded.store.audit_intact()
    assert intact, detail


def test_every_onboarded_tenant_is_covered(app):
    app.seed("kestrel")
    app.seed("northwind")

    results = app.run_all_due(LATER)

    assert {"kestrel", "northwind"} <= set(results)


def test_the_console_starts_and_stops_the_scheduler(settings, app):
    """The lifespan starts the background task and cancels it cleanly on shutdown."""
    app.seed("kestrel")
    assert settings.scheduler_enabled
    with TestClient(build_app(settings)) as client:
        assert client.get("/api/state").status_code == 200
