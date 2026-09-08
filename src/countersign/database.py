"""Canonical state.

SQLite, one writer, short transactions. Three properties are enforced here
rather than in the interface, because an interface is a suggestion and a
database constraint is not.

**An agent cannot grant authority.** Accepting a risk domain, approving a
control into the schedule and dispositioning a finding all refuse an actor whose
identity begins with ``agent:``. There is no flag that turns this off, and there
is no code path where a model supplies the actor.

**A finding cannot be closed by assertion.** ``close_finding`` requires a later
run of the same control that came back effective. Somebody saying the problem is
fixed is not evidence that it is; a clean population over the same test is.

**The audit log is append-only.** Triggers reject every ``UPDATE`` and
``DELETE``, and each row's hash covers the previous hash, so removing or editing
history breaks the chain at the point it was touched.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from countersign.domain import (
    ControlProposal,
    EnterpriseProfile,
    RiskDomainProposal,
    TestResult,
    next_due,
)

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS tenants (
    id            TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    sector        TEXT NOT NULL DEFAULT '',
    onboarded_at  TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profiles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id   TEXT NOT NULL REFERENCES tenants(id),
    payload     TEXT NOT NULL,
    model_mode  TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS taxonomy_meta (
    tenant_id      TEXT PRIMARY KEY REFERENCES tenants(id),
    coverage_note  TEXT NOT NULL DEFAULT '',
    excluded       TEXT NOT NULL DEFAULT '[]',
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_domains (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id            TEXT NOT NULL REFERENCES tenants(id),
    code                 TEXT NOT NULL,
    title                TEXT NOT NULL,
    description          TEXT NOT NULL,
    why_this_company     TEXT NOT NULL,
    regulatory_drivers   TEXT NOT NULL DEFAULT '[]',
    inherent_likelihood  INTEGER NOT NULL,
    inherent_impact      INTEGER NOT NULL,
    evidence             TEXT NOT NULL DEFAULT '[]',
    status               TEXT NOT NULL DEFAULT 'proposed',
    decided_by           TEXT,
    decided_at           TEXT,
    created_at           TEXT NOT NULL,
    UNIQUE (tenant_id, code)
);

CREATE TABLE IF NOT EXISTS controls (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id              TEXT NOT NULL REFERENCES tenants(id),
    domain_code            TEXT NOT NULL,
    code                   TEXT NOT NULL,
    title                  TEXT NOT NULL,
    objective              TEXT NOT NULL,
    nature                 TEXT NOT NULL,
    test_kind              TEXT NOT NULL,
    parameters             TEXT NOT NULL DEFAULT '{}',
    periodicity            TEXT NOT NULL,
    periodicity_rationale  TEXT NOT NULL DEFAULT '',
    tolerance              INTEGER NOT NULL DEFAULT 0,
    severity_if_failed     TEXT NOT NULL DEFAULT 'medium',
    owner_role             TEXT NOT NULL DEFAULT '',
    automation_note        TEXT NOT NULL DEFAULT '',
    residual_gap           TEXT NOT NULL DEFAULT '',
    status                 TEXT NOT NULL DEFAULT 'proposed',
    runnable               INTEGER NOT NULL DEFAULT 0,
    blocked_reason         TEXT NOT NULL DEFAULT '',
    approved_by            TEXT,
    approved_at            TEXT,
    next_due               TEXT,
    created_at             TEXT NOT NULL,
    UNIQUE (tenant_id, code)
);

CREATE TABLE IF NOT EXISTS runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id         TEXT NOT NULL REFERENCES tenants(id),
    control_id        INTEGER NOT NULL REFERENCES controls(id),
    control_code      TEXT NOT NULL,
    period_start      TEXT NOT NULL,
    period_end        TEXT NOT NULL,
    started_at        TEXT NOT NULL,
    finished_at       TEXT NOT NULL,
    outcome           TEXT NOT NULL,
    population_size   INTEGER NOT NULL,
    exception_count   INTEGER NOT NULL,
    suppressed_count  INTEGER NOT NULL DEFAULT 0,
    model_mode        TEXT NOT NULL,
    summary           TEXT NOT NULL DEFAULT '',
    observations      TEXT NOT NULL DEFAULT '[]',
    injection_signals TEXT NOT NULL DEFAULT '[]',
    challenges        TEXT NOT NULL DEFAULT '[]',
    trace             TEXT NOT NULL DEFAULT '[]',
    not_tested        TEXT NOT NULL DEFAULT '[]',
    result_digest     TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS population (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       INTEGER NOT NULL REFERENCES runs(id),
    subject      TEXT NOT NULL,
    label        TEXT NOT NULL,
    passed       INTEGER NOT NULL,
    disposition  TEXT NOT NULL DEFAULT '',
    reason       TEXT NOT NULL DEFAULT '',
    attributes   TEXT NOT NULL DEFAULT '{}',
    evidence     TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS findings (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id             TEXT NOT NULL REFERENCES tenants(id),
    run_id                INTEGER NOT NULL REFERENCES runs(id),
    control_code          TEXT NOT NULL,
    title                 TEXT NOT NULL,
    severity              TEXT NOT NULL,
    condition_text        TEXT NOT NULL,
    criterion_text        TEXT NOT NULL,
    cause_text            TEXT NOT NULL,
    effect_text           TEXT NOT NULL,
    evidence              TEXT NOT NULL DEFAULT '[]',
    subjects              TEXT NOT NULL DEFAULT '[]',
    proposed_remediation  TEXT NOT NULL DEFAULT '',
    proposed_owner        TEXT NOT NULL DEFAULT '',
    challenge             TEXT NOT NULL DEFAULT '{}',
    challenge_survives    INTEGER NOT NULL DEFAULT 1,
    suggested_severity    TEXT NOT NULL DEFAULT '',
    origin                TEXT NOT NULL DEFAULT 'deterministic',
    status                TEXT NOT NULL DEFAULT 'open',
    decided_by            TEXT,
    decided_at            TEXT,
    decision_note         TEXT NOT NULL DEFAULT '',
    closing_run_id        INTEGER REFERENCES runs(id),
    created_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT NOT NULL,
    actor      TEXT NOT NULL,
    event      TEXT NOT NULL,
    entity     TEXT NOT NULL,
    payload    TEXT NOT NULL DEFAULT '{}',
    prev_hash  TEXT NOT NULL,
    hash       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,
    username    TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS audit_no_update
BEFORE UPDATE ON audit
BEGIN SELECT RAISE(ABORT, 'the audit log is append-only'); END;

CREATE TRIGGER IF NOT EXISTS audit_no_delete
BEFORE DELETE ON audit
BEGIN SELECT RAISE(ABORT, 'the audit log is append-only'); END;

CREATE INDEX IF NOT EXISTS runs_by_control ON runs (control_code, period_end);
CREATE INDEX IF NOT EXISTS findings_by_challenge ON findings (tenant_id, challenge_survives);
CREATE INDEX IF NOT EXISTS findings_by_status ON findings (tenant_id, status);
CREATE INDEX IF NOT EXISTS population_by_run ON population (run_id);
"""

# Columns added after the first release. SQLite has no "ADD COLUMN IF NOT
# EXISTS", and the schema above only runs for a database that does not exist
# yet, so an already-deployed volume needs these applied by name.
ADDED_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "findings": [
        ("challenge_survives", "INTEGER NOT NULL DEFAULT 1"),
        ("suggested_severity", "TEXT NOT NULL DEFAULT ''"),
        ("origin", "TEXT NOT NULL DEFAULT 'deterministic'"),
    ]
}

AGENT_PREFIX = "agent:"


class AuthorityError(PermissionError):
    """Raised when an actor tries to do something only a person may do."""


class EvidenceError(RuntimeError):
    """Raised when a state change would not be supported by evidence."""


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _require_person(actor: str, action: str) -> None:
    """The single gate every authority-bearing action passes through."""
    if not actor or actor.startswith(AGENT_PREFIX):
        raise AuthorityError(
            f"{action} requires a person. '{actor or 'anonymous'}' cannot hold authority: "
            f"a model may propose this, and only a named human may grant it."
        )


class Store:
    """Every read and write of canonical state."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._add_missing_columns(connection)

    @staticmethod
    def _add_missing_columns(connection: sqlite3.Connection) -> None:
        """Bring a database written by an earlier version up to this schema.

        Additive only. Nothing here drops or rewrites a column, so a store that
        already holds findings keeps them, and the defaults are the values those
        rows would have been written with.
        """
        for table, columns in ADDED_COLUMNS.items():
            present = {
                row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for name, definition in columns:
                if name not in present:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    # ----------------------------------------------------------------
    # Audit
    # ----------------------------------------------------------------

    def _append_audit(
        self,
        connection: sqlite3.Connection,
        actor: str,
        event: str,
        entity: str,
        payload: dict[str, Any] | None = None,
    ) -> str:
        row = connection.execute("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
        previous = row["hash"] if row else "genesis"
        at = _now()
        body = _canonical(payload or {})
        digest = hashlib.sha256(
            f"{previous}|{at}|{actor}|{event}|{entity}|{body}".encode()
        ).hexdigest()
        connection.execute(
            "INSERT INTO audit (at, actor, event, entity, payload, prev_hash, hash) "
            "VALUES (?,?,?,?,?,?,?)",
            (at, actor, event, entity, body, previous, digest),
        )
        return digest

    def audit_events(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM audit ORDER BY seq DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def audit_intact(self) -> tuple[bool, str]:
        """Recompute the chain from the beginning."""
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM audit ORDER BY seq ASC").fetchall()
        previous = "genesis"
        for row in rows:
            expected = hashlib.sha256(
                f"{previous}|{row['at']}|{row['actor']}|{row['event']}|{row['entity']}|{row['payload']}".encode()
            ).hexdigest()
            if expected != row["hash"] or row["prev_hash"] != previous:
                return False, f"chain breaks at event {row['seq']} ({row['event']})"
            previous = row["hash"]
        return True, f"{len(rows)} events, chain intact"

    # ----------------------------------------------------------------
    # Sessions
    # ----------------------------------------------------------------

    def open_session(self, username: str, hours: int = 12) -> str:
        token = secrets.token_urlsafe(32)
        with self.write() as connection:
            connection.execute(
                "INSERT INTO sessions (token, username, created_at, expires_at) VALUES (?,?,?,?)",
                (
                    token,
                    username,
                    _now(),
                    (datetime.now() + timedelta(hours=hours)).replace(microsecond=0).isoformat(),
                ),
            )
        return token

    def session_user(self, token: str) -> str | None:
        if not token:
            return None
        with self.connect() as connection:
            row = connection.execute(
                "SELECT username, expires_at FROM sessions WHERE token = ?", (token,)
            ).fetchone()
        if row is None or row["expires_at"] < _now():
            return None
        return row["username"]

    def close_session(self, token: str) -> None:
        with self.write() as connection:
            connection.execute("DELETE FROM sessions WHERE token = ?", (token,))

    # ----------------------------------------------------------------
    # Tenants and onboarding
    # ----------------------------------------------------------------

    def upsert_tenant(self, tenant: str, display_name: str, description: str) -> None:
        with self.write() as connection:
            connection.execute(
                "INSERT INTO tenants (id, display_name, description, created_at) VALUES (?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET display_name=excluded.display_name, "
                "description=excluded.description",
                (tenant, display_name, description, _now()),
            )

    def tenants(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM tenants ORDER BY display_name").fetchall()
        return [dict(row) for row in rows]

    def record_profile(
        self, tenant: str, profile: EnterpriseProfile, model_mode: str, actor: str
    ) -> None:
        with self.write() as connection:
            connection.execute(
                "INSERT INTO profiles (tenant_id, payload, model_mode, created_at) VALUES (?,?,?,?)",
                (tenant, profile.model_dump_json(), model_mode, _now()),
            )
            connection.execute(
                "UPDATE tenants SET sector = ?, onboarded_at = ? WHERE id = ?",
                (profile.sector, _now(), tenant),
            )
            self._append_audit(
                connection,
                actor,
                "discovery.profile_proposed",
                f"tenant:{tenant}",
                {"sector": profile.sector, "confidence": profile.confidence, "model_mode": model_mode},
            )

    def profile(self, tenant: str) -> EnterpriseProfile | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM profiles WHERE tenant_id = ? ORDER BY id DESC LIMIT 1",
                (tenant,),
            ).fetchone()
        return EnterpriseProfile.model_validate_json(row["payload"]) if row else None

    def record_domains(
        self, tenant: str, proposal: RiskDomainProposal, model_mode: str, actor: str
    ) -> None:
        with self.write() as connection:
            for domain in proposal.domains:
                connection.execute(
                    "INSERT INTO risk_domains (tenant_id, code, title, description, "
                    "why_this_company, regulatory_drivers, inherent_likelihood, inherent_impact, "
                    "evidence, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,'proposed',?) "
                    "ON CONFLICT(tenant_id, code) DO NOTHING",
                    (
                        tenant,
                        domain.code,
                        domain.title,
                        domain.description,
                        domain.why_this_company,
                        _canonical([ref for ref in domain.regulatory_drivers]),
                        domain.inherent_likelihood,
                        domain.inherent_impact,
                        _canonical([ref.model_dump(mode="json") for ref in domain.evidence]),
                        _now(),
                    ),
                )
            connection.execute(
                "INSERT INTO taxonomy_meta (tenant_id, coverage_note, excluded, created_at) "
                "VALUES (?,?,?,?) ON CONFLICT(tenant_id) DO UPDATE SET "
                "coverage_note=excluded.coverage_note, excluded=excluded.excluded",
                (
                    tenant,
                    proposal.coverage_note,
                    _canonical(proposal.deliberately_excluded),
                    _now(),
                ),
            )
            self._append_audit(
                connection,
                actor,
                "taxonomy.domains_proposed",
                f"tenant:{tenant}",
                {
                    "codes": [d.code for d in proposal.domains],
                    "excluded": proposal.deliberately_excluded,
                    "model_mode": model_mode,
                },
            )

    def domains(self, tenant: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM risk_domains WHERE tenant_id = ? ORDER BY "
                "(inherent_likelihood * inherent_impact) DESC, code",
                (tenant,),
            ).fetchall()
        domains = []
        for row in rows:
            item = dict(row)
            item["regulatory_drivers"] = json.loads(item["regulatory_drivers"])
            item["evidence"] = json.loads(item["evidence"])
            item["inherent_score"] = item["inherent_likelihood"] * item["inherent_impact"]
            domains.append(item)
        return domains

    def taxonomy_meta(self, tenant: str) -> dict[str, Any]:
        """The coverage note and the domains deliberately left out.

        Held separately from the domains themselves because it is a statement
        about the taxonomy as a whole, and because what a taxonomy leaves out is
        the part nobody will ever be shown again unless it is written down.
        """
        with self.connect() as connection:
            row = connection.execute(
                "SELECT coverage_note, excluded FROM taxonomy_meta WHERE tenant_id = ?", (tenant,)
            ).fetchone()
        if row is None:
            return {"coverage_note": "", "excluded": []}
        return {"coverage_note": row["coverage_note"], "excluded": json.loads(row["excluded"])}

    def decide_domain(self, tenant: str, code: str, accept: bool, actor: str) -> None:
        """A person accepts or rejects a proposed risk domain. Gate one."""
        _require_person(actor, "Accepting a risk domain")
        status = "accepted" if accept else "rejected"
        with self.write() as connection:
            changed = connection.execute(
                "UPDATE risk_domains SET status = ?, decided_by = ?, decided_at = ? "
                "WHERE tenant_id = ? AND code = ? AND status = 'proposed'",
                (status, actor, _now(), tenant, code),
            ).rowcount
            if not changed:
                raise EvidenceError(f"risk domain {code} is not awaiting a decision")
            self._append_audit(
                connection, actor, f"taxonomy.domain_{status}", f"domain:{tenant}/{code}", {}
            )

    # ----------------------------------------------------------------
    # Controls
    # ----------------------------------------------------------------

    def record_controls(
        self,
        tenant: str,
        proposals: list[ControlProposal],
        runnable: dict[str, tuple[bool, str]],
        model_mode: str,
        actor: str,
    ) -> None:
        with self.write() as connection:
            codes: list[str] = []
            for proposal in proposals:
                for control in proposal.controls:
                    ok, reason = runnable.get(control.code, (False, "not checked"))
                    connection.execute(
                        "INSERT INTO controls (tenant_id, domain_code, code, title, objective, "
                        "nature, test_kind, parameters, periodicity, periodicity_rationale, "
                        "tolerance, severity_if_failed, owner_role, automation_note, residual_gap, "
                        "status, runnable, blocked_reason, created_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'proposed',?,?,?) "
                        "ON CONFLICT(tenant_id, code) DO NOTHING",
                        (
                            tenant,
                            proposal.domain_code,
                            control.code,
                            control.title,
                            control.objective,
                            control.nature,
                            control.test_kind,
                            _canonical(control.parameters),
                            control.periodicity,
                            control.periodicity_rationale,
                            control.tolerance,
                            control.severity_if_failed,
                            control.owner_role,
                            control.automation_note,
                            proposal.residual_gap,
                            1 if ok else 0,
                            "" if ok else reason,
                            _now(),
                        ),
                    )
                    codes.append(control.code)
            self._append_audit(
                connection,
                actor,
                "control_design.controls_proposed",
                f"tenant:{tenant}",
                {"codes": codes, "model_mode": model_mode},
            )

    def controls(self, tenant: str, status: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM controls WHERE tenant_id = ?"
        params: list[Any] = [tenant]
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY domain_code, code"
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        controls = []
        for row in rows:
            item = dict(row)
            item["parameters"] = json.loads(item["parameters"])
            item["runnable"] = bool(item["runnable"])
            controls.append(item)
        return controls

    def control(self, tenant: str, code: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM controls WHERE tenant_id = ? AND code = ?", (tenant, code)
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["parameters"] = json.loads(item["parameters"])
        item["runnable"] = bool(item["runnable"])
        return item

    def approve_control(self, tenant: str, code: str, actor: str, first_due: date) -> None:
        """A person approves a proposed control into the schedule. Gate two.

        Refused for a control whose test cannot run. Approving something that
        will never produce evidence is how a control programme becomes a
        document instead of a system.
        """
        _require_person(actor, "Approving a control")
        with self.write() as connection:
            row = connection.execute(
                "SELECT runnable, blocked_reason, status FROM controls WHERE tenant_id = ? AND code = ?",
                (tenant, code),
            ).fetchone()
            if row is None:
                raise EvidenceError(f"control {code} does not exist")
            if row["status"] != "proposed":
                raise EvidenceError(f"control {code} is already {row['status']}")
            if not row["runnable"]:
                raise EvidenceError(
                    f"control {code} cannot be scheduled: {row['blocked_reason']}"
                )
            connection.execute(
                "UPDATE controls SET status = 'scheduled', approved_by = ?, approved_at = ?, "
                "next_due = ? WHERE tenant_id = ? AND code = ?",
                (actor, _now(), first_due.isoformat(), tenant, code),
            )
            self._append_audit(
                connection,
                actor,
                "control.approved",
                f"control:{tenant}/{code}",
                {"first_due": first_due.isoformat()},
            )

    def set_control_status(self, tenant: str, code: str, status: str, actor: str) -> None:
        _require_person(actor, "Changing a control's status")
        with self.write() as connection:
            connection.execute(
                "UPDATE controls SET status = ? WHERE tenant_id = ? AND code = ?",
                (status, tenant, code),
            )
            self._append_audit(
                connection, actor, f"control.{status}", f"control:{tenant}/{code}", {}
            )

    def due_controls(self, tenant: str, as_of: date) -> list[dict[str, Any]]:
        return [
            control
            for control in self.controls(tenant, status="scheduled")
            if control["next_due"] and control["next_due"] <= as_of.isoformat()
        ]

    # ----------------------------------------------------------------
    # Runs
    # ----------------------------------------------------------------

    def record_run(
        self,
        tenant: str,
        control: dict[str, Any],
        result: TestResult,
        report,
        challenges,
        trace: list[dict[str, str]],
        model_mode: str,
        as_of: date,
    ) -> int:
        """Persist one control execution and everything it produced.

        The run is written by the scheduler, not by a person, and it grants no
        authority: a finding lands in ``open`` and stays there until somebody
        with a name decides what to do about it.
        """
        outcome = result.outcome(control["tolerance"])
        suppressed = [
            item for item in result.population if item.attributes.get("disposition") == "suppressed"
        ]
        digest = hashlib.sha256(
            _canonical(
                {
                    "control": control["code"],
                    "period": [result.period_start.isoformat(), result.period_end.isoformat()],
                    "population": [
                        {"subject": item.subject, "passed": item.passed} for item in result.population
                    ],
                }
            ).encode()
        ).hexdigest()[:16]

        with self.write() as connection:
            cursor = connection.execute(
                "INSERT INTO runs (tenant_id, control_id, control_code, period_start, period_end, "
                "started_at, finished_at, outcome, population_size, exception_count, "
                "suppressed_count, model_mode, summary, observations, injection_signals, "
                "challenges, trace, not_tested, result_digest) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    tenant,
                    control["id"],
                    control["code"],
                    result.period_start.isoformat(),
                    result.period_end.isoformat(),
                    _now(),
                    _now(),
                    outcome,
                    result.population_size,
                    result.exception_count,
                    len(suppressed),
                    model_mode,
                    report.summary,
                    _canonical(report.observations),
                    _canonical([s.model_dump(mode="json") for s in report.injection_signals]),
                    _canonical([c.model_dump(mode="json") for c in challenges.challenges]),
                    _canonical(trace),
                    _canonical(result.not_tested),
                    digest,
                ),
            )
            run_id = int(cursor.lastrowid)

            for item in result.population:
                connection.execute(
                    "INSERT INTO population (run_id, subject, label, passed, disposition, reason, "
                    "attributes, evidence) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        run_id,
                        item.subject,
                        item.label,
                        1 if item.passed else 0,
                        str(item.attributes.get("disposition", "")),
                        item.reason,
                        _canonical(item.attributes),
                        _canonical([ref.model_dump(mode="json") for ref in item.evidence]),
                    ),
                )

            # Every finding is raised. A challenge is an argument recorded
            # beside a finding, not a veto over it: a challenger that argued
            # well would otherwise be able to delete the only first-class
            # record of a deterministic exception, and the fact that a
            # reviewer never saw it would itself be invisible. Severity is the
            # control's, and a suggested downgrade is stored as a suggestion.
            challenge_by_title = {c.finding_title: c for c in challenges.challenges}
            for finding in report.findings:
                challenge = challenge_by_title.get(finding.title)
                survives = challenge is None or challenge.survives
                connection.execute(
                    "INSERT INTO findings (tenant_id, run_id, control_code, title, severity, "
                    "condition_text, criterion_text, cause_text, effect_text, evidence, subjects, "
                    "proposed_remediation, proposed_owner, challenge, challenge_survives, "
                    "suggested_severity, origin, status, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'open',?)",
                    (
                        tenant,
                        run_id,
                        control["code"],
                        finding.title,
                        finding.severity,
                        finding.condition,
                        finding.criterion,
                        finding.cause,
                        finding.effect,
                        _canonical([ref.model_dump(mode="json") for ref in finding.evidence]),
                        _canonical(finding.subjects),
                        finding.proposed_remediation,
                        finding.proposed_owner,
                        _canonical(challenge.model_dump(mode="json") if challenge else {}),
                        1 if survives else 0,
                        (challenge.suggested_downgrade or "") if challenge else "",
                        finding.origin,
                        _now(),
                    ),
                )
                if not survives:
                    self._append_audit(
                        connection,
                        f"{AGENT_PREFIX}challenger",
                        "finding.challenged",
                        f"control:{tenant}/{control['code']}",
                        {
                            "run": run_id,
                            "finding": finding.title,
                            "reason": challenge.reason,
                            "note": (
                                "Recorded as an argument against the finding. The finding is "
                                "raised regardless and waits for a person."
                            ),
                        },
                    )

            connection.execute(
                "UPDATE controls SET next_due = ? WHERE id = ?",
                (next_due(as_of, control["periodicity"]).isoformat(), control["id"]),
            )
            self._append_audit(
                connection,
                f"{AGENT_PREFIX}scheduler",
                "control.executed",
                f"control:{tenant}/{control['code']}",
                {
                    "run": run_id,
                    "outcome": outcome,
                    "population": result.population_size,
                    "exceptions": result.exception_count,
                    "model_mode": model_mode,
                    "digest": digest,
                },
            )
        return run_id

    def runs(self, tenant: str, control_code: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query = "SELECT * FROM runs WHERE tenant_id = ?"
        params: list[Any] = [tenant]
        if control_code:
            query += " AND control_code = ?"
            params.append(control_code)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._inflate_run(dict(row)) for row in rows]

    def run(self, tenant: str, run_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE tenant_id = ? AND id = ?", (tenant, run_id)
            ).fetchone()
            if row is None:
                return None
            run = self._inflate_run(dict(row))
            items = connection.execute(
                "SELECT * FROM population WHERE run_id = ? ORDER BY passed ASC, id ASC", (run_id,)
            ).fetchall()
            findings = connection.execute(
                "SELECT * FROM findings WHERE run_id = ? ORDER BY id", (run_id,)
            ).fetchall()
        run["population"] = [
            {
                **dict(item),
                "passed": bool(item["passed"]),
                "attributes": json.loads(item["attributes"]),
                "evidence": json.loads(item["evidence"]),
            }
            for item in items
        ]
        run["findings"] = [self._inflate_finding(dict(f)) for f in findings]
        return run

    @staticmethod
    def _inflate_run(row: dict[str, Any]) -> dict[str, Any]:
        for field in ("observations", "injection_signals", "challenges", "trace", "not_tested"):
            row[field] = json.loads(row[field])
        return row

    @staticmethod
    def _inflate_finding(row: dict[str, Any]) -> dict[str, Any]:
        row["evidence"] = json.loads(row["evidence"])
        row["subjects"] = json.loads(row["subjects"])
        row["challenge"] = json.loads(row["challenge"])
        return row

    # ----------------------------------------------------------------
    # Findings
    # ----------------------------------------------------------------

    def findings(self, tenant: str, status: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM findings WHERE tenant_id = ?"
        params: list[Any] = [tenant]
        if status:
            query += " AND status = ?"
            params.append(status)
        query += (
            " ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
            "WHEN 'medium' THEN 2 ELSE 3 END, id DESC"
        )
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._inflate_finding(dict(row)) for row in rows]

    def finding(self, tenant: str, finding_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM findings WHERE tenant_id = ? AND id = ?", (tenant, finding_id)
            ).fetchone()
        return self._inflate_finding(dict(row)) if row else None

    def decide_finding(
        self, tenant: str, finding_id: int, status: str, actor: str, note: str = ""
    ) -> None:
        """A person dispositions a finding. Gate three.

        ``closed`` is not available here. Closing needs evidence, so it has its
        own method, and this one refuses the word.
        """
        _require_person(actor, "Dispositioning a finding")
        if status == "closed":
            raise EvidenceError(
                "A finding is not closed by decision. Re-run the control; a clean population "
                "over the same test is what closes it."
            )
        if status not in {"open", "risk_accepted", "remediation_agreed"}:
            raise EvidenceError(f"unknown disposition {status!r}")
        if status == "risk_accepted" and len(note.strip()) < 20:
            raise EvidenceError(
                "Accepting a risk requires a stated reason. Somebody will be asked about this "
                "decision a year from now."
            )
        with self.write() as connection:
            changed = connection.execute(
                "UPDATE findings SET status = ?, decided_by = ?, decided_at = ?, decision_note = ? "
                "WHERE tenant_id = ? AND id = ? AND status NOT IN ('closed')",
                (status, actor, _now(), note, tenant, finding_id),
            ).rowcount
            if not changed:
                raise EvidenceError("finding does not exist or is already closed")
            self._append_audit(
                connection,
                actor,
                f"finding.{status}",
                f"finding:{tenant}/{finding_id}",
                {"note": note},
            )

    def close_findings_with_evidence(self, tenant: str, run_id: int) -> list[int]:
        """Close open findings whose control has just come back clean.

        The closing evidence is a later run of the same control, over a fresh
        population, that produced no exceptions and left nothing untested.
        Nobody's assertion closes a finding, including the assertion of the
        person who fixed it.

        Both conditions are checked here. ``effective`` already implies complete
        coverage, because :meth:`TestResult.outcome` refuses to return it
        otherwise, but the stored ``not_tested`` list is read rather than
        inferred: closing a finding on a run that never looked at the thing the
        finding was about is the one error in this system that nobody would ever
        notice afterwards.
        """
        closed: list[int] = []
        with self.write() as connection:
            run = connection.execute(
                "SELECT * FROM runs WHERE id = ? AND tenant_id = ?", (run_id, tenant)
            ).fetchone()
            if run is None or run["outcome"] != "effective":
                return []
            if json.loads(run["not_tested"] or "[]"):
                return []
            # Ordered by run id rather than by timestamp. Timestamps here have
            # one-second resolution, so a finding raised and remediated inside
            # the same second would never close; run ids are monotonic and
            # exact, and a finding always belongs to the run that raised it.
            rows = connection.execute(
                "SELECT f.id FROM findings f "
                "WHERE f.tenant_id = ? AND f.control_code = ? AND f.status != 'closed' "
                "AND f.run_id < ?",
                (tenant, run["control_code"], run_id),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE findings SET status = 'closed', closing_run_id = ?, decided_at = ?, "
                    "decision_note = ? WHERE id = ?",
                    (
                        run_id,
                        _now(),
                        f"Closed by run {run_id}: {run['population_size']} items tested, "
                        f"no exceptions.",
                        row["id"],
                    ),
                )
                self._append_audit(
                    connection,
                    f"{AGENT_PREFIX}scheduler",
                    "finding.closed_by_evidence",
                    f"finding:{tenant}/{row['id']}",
                    {"closing_run": run_id, "population": run["population_size"]},
                )
                closed.append(int(row["id"]))
        return closed

    # ----------------------------------------------------------------
    # Overview
    # ----------------------------------------------------------------

    def summary(self, tenant: str, as_of: date) -> dict[str, Any]:
        with self.connect() as connection:
            counts = connection.execute(
                "SELECT status, COUNT(*) AS n FROM controls WHERE tenant_id = ? GROUP BY status",
                (tenant,),
            ).fetchall()
            findings = connection.execute(
                "SELECT status, severity, COUNT(*) AS n FROM findings WHERE tenant_id = ? "
                "GROUP BY status, severity",
                (tenant,),
            ).fetchall()
            runs = connection.execute(
                "SELECT outcome, COUNT(*) AS n FROM runs WHERE tenant_id = ? GROUP BY outcome",
                (tenant,),
            ).fetchall()
            last = connection.execute(
                "SELECT MAX(finished_at) AS last FROM runs WHERE tenant_id = ?", (tenant,)
            ).fetchone()
        control_status = {row["status"]: row["n"] for row in counts}
        open_by_severity: dict[str, int] = {}
        total_open = 0
        for row in findings:
            if row["status"] in {"open", "remediation_agreed"}:
                open_by_severity[row["severity"]] = open_by_severity.get(row["severity"], 0) + row["n"]
                total_open += row["n"]
        return {
            "controls": control_status,
            "runs": {row["outcome"]: row["n"] for row in runs},
            "open_findings": total_open,
            "open_by_severity": open_by_severity,
            "due_now": len(self.due_controls(tenant, as_of)),
            "last_run_at": last["last"] if last else None,
        }
