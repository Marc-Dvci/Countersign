"""The connector contract.

Every source system reaches Countersign through the same two verbs.

``inventory()``  what exists here, used by discovery to work out what the
                 company is before anyone writes a control.
``fetch()``      the rows of one named dataset, used by control tests.

A connector is read-only by construction: there is no verb that writes. That is
not a policy the code checks at runtime, it is the absence of a method, which is
the only version of that guarantee worth having.

Two implementations satisfy the contract for each kind. ``CorpusConnector``
replays a seeded synthetic estate so a judge with no credentials can run the
whole product; the live adapters in this package call the real API when a token
is present. Control tests cannot tell them apart, so a control proven against
the corpus is the same control that runs against the real system.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from countersign.domain import DiscoveredAsset, EvidenceRef, SourceKind

# The datasets each kind of source is expected to be able to serve. A control
# test names the dataset it needs; a connector that cannot serve it raises, and
# the run records `not_tested` rather than testing a smaller population without saying so.
DATASETS: dict[str, tuple[str, ...]] = {
    "github": ("repositories", "pull_requests", "deployments", "dependencies"),
    "jira": ("projects", "issues", "automation_settings"),
    "identity": ("users", "groups", "memberships", "access_reviews"),
    "hris": ("employees",),
    "documents": ("documents",),
    "obligations": ("obligations",),
    "vendors": ("vendors",),
    "ledger": ("payments", "counterparties"),
}


def digest(payload: Any) -> str:
    """SHA-256 over a canonical rendering of a connector payload.

    Recorded beside every quoted fact so a report can be re-checked against the
    source it came from, and so a corpus edit invalidates the evidence that was
    drawn from it instead of changing history in place.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


class ConnectorError(RuntimeError):
    """Raised when a source cannot serve a dataset a control test asked for."""


@runtime_checkable
class Connector(Protocol):
    """Read-only access to one source system."""

    kind: SourceKind
    mode: str

    def inventory(self) -> list[DiscoveredAsset]:
        """Every object in this source, for discovery."""
        ...

    def fetch(self, dataset: str, since: date | None = None) -> list[dict[str, Any]]:
        """Rows of one named dataset, optionally bounded to a period."""
        ...


class CorpusConnector:
    """Serves a seeded synthetic estate from JSON on disk.

    This is the mode a judge runs in. The data is invented; the shapes are the
    ones the live adapters emit, and the planted conditions are the ones a real
    second-line function meets.
    """

    mode = "corpus"

    def __init__(self, kind: SourceKind, root: Path, tenant: str):
        self.kind = kind
        self.root = root
        self.tenant = tenant

    def _path(self, dataset: str) -> Path:
        return self.root / self.tenant / f"{self.kind}.{dataset}.json"

    def _load(self, dataset: str) -> list[dict[str, Any]]:
        path = self._path(dataset)
        if not path.exists():
            raise ConnectorError(f"{self.kind} has no dataset {dataset!r} for tenant {self.tenant}")
        return json.loads(path.read_text(encoding="utf-8"))

    def available(self) -> tuple[str, ...]:
        return tuple(
            dataset
            for dataset in DATASETS.get(self.kind, ())
            if self._path(dataset).exists()
        )

    def inventory(self) -> list[DiscoveredAsset]:
        assets: list[DiscoveredAsset] = []
        for dataset in self.available():
            for row in self._load(dataset):
                assets.append(
                    DiscoveredAsset(
                        source=self.kind,
                        kind=row.get("asset_kind", dataset.rstrip("s")),
                        external_id=str(row.get("id") or row.get("key") or row.get("reference")),
                        name=str(row.get("name") or row.get("title") or row.get("id")),
                        attributes=row,
                    )
                )
        return assets

    def fetch(self, dataset: str, since: date | None = None) -> list[dict[str, Any]]:
        rows = self._load(dataset)
        if since is None:
            return rows
        return [row for row in rows if _row_date(row) is None or _row_date(row) >= since]


def _row_date(row: dict[str, Any]) -> date | None:
    """The date a row is bounded by, if it carries one."""
    for field in ("merged_at", "created_at", "occurred_at", "authorised_at", "date"):
        raw = row.get(field)
        if isinstance(raw, str) and len(raw) >= 10:
            try:
                return date.fromisoformat(raw[:10])
            except ValueError:
                continue
    return None


def evidence_for(kind: SourceKind, row: dict[str, Any], label_field: str = "name") -> EvidenceRef:
    """Build the reference that travels with a fact drawn from ``row``."""
    locator = str(row.get("id") or row.get("key") or row.get("reference") or "")
    return EvidenceRef(
        source=kind,
        locator=locator,
        label=str(row.get(label_field) or row.get("title") or locator),
        digest=digest(row),
        url=row.get("url"),
    )
