"""Connector registry.

``build_connectors`` returns one connector per source kind for a tenant. A kind
resolves to its live adapter when the environment carries credentials for it,
and to the seeded corpus otherwise. Nothing above this line knows which it got,
which is the property that lets the same control run in both places.
"""

from __future__ import annotations

import os
from pathlib import Path

from countersign.connectors.base import (
    DATASETS,
    Connector,
    ConnectorError,
    CorpusConnector,
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
    tenant: str, corpus_root: Path | None = None, allow_live: bool = True
) -> dict[SourceKind, Connector]:
    """One connector per source kind, live where credentialed, corpus otherwise."""
    root = corpus_root or CORPUS_ROOT
    connectors: dict[SourceKind, Connector] = {}
    for kind in ALL_KINDS:
        connector = None
        if allow_live and kind in LIVE_BUILDERS:
            connector = LIVE_BUILDERS[kind]()
        connectors[kind] = connector or CorpusConnector(kind, root, tenant)
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
        if isinstance(connector, CorpusConnector):
            available = list(connector.available())
        else:
            available = list(DATASETS.get(kind, ()))
        described.append(
            {
                "source": kind,
                "mode": connector.mode,
                "datasets": available,
                "connected": bool(available),
            }
        )
    return described
