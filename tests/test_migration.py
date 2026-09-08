"""Opening a database written by an earlier version of the product.

The seeded demonstration always starts from an empty file, so nothing else in
this suite ever opens a store that already has rows in it. A deployment does
exactly that on every release, and it is the one path where a schema change can
take the console down while every test stays green.
"""

from __future__ import annotations

import sqlite3

from countersign.database import ADDED_COLUMNS, Store


def _downgrade(path) -> None:
    """Put the database back the way the previous release wrote it."""
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.execute("DROP INDEX IF EXISTS findings_by_challenge")
        for table, columns in ADDED_COLUMNS.items():
            for name, _ in columns:
                connection.execute(f"ALTER TABLE {table} DROP COLUMN {name}")
    finally:
        connection.close()


def test_a_database_from_the_previous_release_opens_and_keeps_its_rows(app, settings):
    app.seed("kestrel")
    before = [(f["id"], f["title"]) for f in app.store.findings("kestrel")]
    assert before, "this test needs findings to preserve"
    intact_before, _ = app.store.audit_intact()
    assert intact_before

    _downgrade(settings.database_path)

    reopened = Store(settings.database_path)
    after = [(f["id"], f["title"]) for f in reopened.findings("kestrel")]
    assert after == before, "an upgrade keeps the findings that were already raised"

    raised = reopened.findings("kestrel")[0]
    assert raised["challenge_survives"] == 1, "a row from before the column defaults to raised"
    assert raised["suggested_severity"] == ""
    assert raised["origin"] == "deterministic"

    intact_after, detail = reopened.audit_intact()
    assert intact_after, detail


def test_opening_it_twice_is_a_no_op(app, settings):
    app.seed("kestrel")
    _downgrade(settings.database_path)
    Store(settings.database_path)
    reopened = Store(settings.database_path)
    assert reopened.findings("kestrel")
