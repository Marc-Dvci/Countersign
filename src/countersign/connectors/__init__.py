"""Connector registry.

``build_connectors`` returns one connector per source kind for a tenant. A kind
resolves to its live adapter when the environment carries credentials for it,
and to the seeded corpus otherwise. Nothing above this line knows which it got,
which is the property that lets the same control run in both places.

That property has one hard boundary. **The corpus is a fallback only while every
source is a fallback.** As soon as one kind is live, an uncredentialed kind
resolves to :class:`DisconnectedConnector` rather than to the corpus, so a
control cannot join real GitHub merges to a fictional leaver list and publish a
single outcome over both. The escape hatch, ``COUNTERSIGN_ALLOW_SOURCE_MIXING``,
exists for demonstrating one live adapter against the seeded estate; it is off
by default, and the console shows which mode each source is in either way.

Three kinds have live adapters today: ``github``, ``jira`` and ``identity``. The
rest are corpus-only, which is a coverage gap rather than a hidden one: the
console names it, and in a live deployment those sources are disconnected rather
than quietly synthetic.
"""

from __future__ import annotations

import os
from pathlib import Path

from countersign.connectors.base import (
    DATASETS,
    Connector,
    ConnectorError,
    CorpusConnector,
    DisconnectedConnector,
    digest,
    evidence_for,
)
from countersign.connectors.github import GitHubConnector
from countersign.connectors.identity import OktaConnector
from countersign.connectors.jira import JiraConnector
from countersign.domain import SourceKind

__all__ = [
    "DATASETS",
    "Connector",
    "ConnectorError",
    "CorpusConnector",
    "DisconnectedConnector",
    "GitHubConnector",
    "JiraConnector",
    "OktaConnector",
    "build_connectors",
    "describe_live_state",
    "digest",
    "evidence_for",
    "live_kinds",
]

CORPUS_ROOT = Path(__file__).resolve().parent.parent / "corpus"

ALL_KINDS: tuple[SourceKind, ...] = (
    "github",
    "jira",
    "identity",
    "hris",
    "documents",
    "obligations",
    "vendors",
    "ledger",
)


def _live_github() -> GitHubConnector | None:
    org, token = os.environ.get("GITHUB_ORG"), os.environ.get("GITHUB_TOKEN")
    return GitHubConnector(org, token) if org and token else None


def _live_jira() -> JiraConnector | None:
    site = os.environ.get("JIRA_SITE")
    email = os.environ.get("JIRA_EMAIL")
    token = os.environ.get("JIRA_TOKEN")
    return JiraConnector(site, email, token) if site and email and token else None


def _live_identity() -> OktaConnector | None:
    domain, token = os.environ.get("OKTA_DOMAIN"), os.environ.get("OKTA_TOKEN")
    return OktaConnector(domain, token) if domain and token else None


LIVE_BUILDERS = {
    "github": _live_github,
    "jira": _live_jira,
    "identity": _live_identity,
}


def live_kinds() -> set[str]:
    """Which source kinds currently have credentials in the environment."""
    return {kind for kind, builder in LIVE_BUILDERS.items() if builder() is not None}


def build_connectors(
    tenant: str,
    corpus_root: Path | None = None,
    allow_live: bool = True,
    allow_mixed_sources: bool = False,
) -> dict[SourceKind, Connector]:
    """One connector per source kind, live where credentialed.

    A kind with no live adapter falls back to the seeded corpus only while
    nothing else is live. Once any source is real, the fallback becomes a
    :class:`DisconnectedConnector`, so the estate a control walks is either
    entirely synthetic or entirely real. ``allow_mixed_sources`` turns that off
    deliberately, for demonstrating a single live adapter against the seeded
    estate.
    """
    root = corpus_root or CORPUS_ROOT
    live: dict[SourceKind, Connector] = {}
    if allow_live:
        for kind, builder in LIVE_BUILDERS.items():
            connector = builder()
            if connector is not None:
                live[kind] = connector

    mixing = bool(live) and not allow_mixed_sources
    connectors: dict[SourceKind, Connector] = {}
    for kind in ALL_KINDS:
        if kind in live:
            connectors[kind] = live[kind]
        elif mixing:
            connectors[kind] = DisconnectedConnector(kind)
        else:
            connectors[kind] = CorpusConnector(kind, root, tenant)
    return connectors


def describe_live_state(connectors: dict[SourceKind, Connector]) -> list[dict[str, object]]:
    """What each connected source is and what it can serve.

    Used by the console header and by the discovery agent's ``list_sources``
    tool, so a person and an agent are told the same thing about where the
    evidence is coming from.
    """
    described: list[dict[str, object]] = []
    for kind, connector in connectors.items():
        available: list[str]
        if isinstance(connector, DisconnectedConnector):
            available = []
        elif isinstance(connector, CorpusConnector):
            available = list(connector.available())
        else:
            available = list(DATASETS.get(kind, ()))
        described.append(
            {
                "source": kind,
                "mode": connector.mode,
                "datasets": available,
                "connected": bool(available),
                "note": getattr(connector, "reason", ""),
            }
        )
    return described
