"""Okta, live.

Users, groups and memberships come from the API. Access-review records do not:
Okta Identity Governance exposes campaigns on a separate licensed API that most
tenants do not have, so ``access_reviews`` raises rather than returning an empty
list. An empty list would read as "nothing overdue"; raising reads as "not
tested", which is the truth.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from countersign.connectors.base import ConnectorError
from countersign.domain import DiscoveredAsset


class OktaConnector:
    """Read-only Okta directory access via an API token."""

    kind = "identity"
    mode = "live"

    def __init__(self, domain: str, token: str, timeout: float = 20.0):
        self.domain = domain.rstrip("/")
        self._client = httpx.Client(
            base_url=f"{self.domain}/api/v1",
            timeout=timeout,
            headers={"Authorization": f"SSWS {token}", "Accept": "application/json"},
        )

    def close(self) -> None:
        self._client.close()

    def _get_all(self, path: str, **params: Any) -> list[dict[str, Any]]:
        """Follow Okta's Link-header pagination to the end."""
        rows: list[dict[str, Any]] = []
        url: str | None = path
        while url:
            response = self._client.get(url, params=params if url == path else None)
            if response.status_code >= 400:
                raise ConnectorError(
                    f"Okta {path} returned {response.status_code}: {response.text[:200]}"
                )
            rows.extend(response.json())
            url = _next_link(response.headers.get("link", ""))
        return rows

    def _users(self) -> list[dict[str, Any]]:
        return [
            {
                "id": user["id"],
                "name": (user["profile"].get("email") or user["id"]),
                "asset_kind": "user",
                "email": user["profile"].get("email", ""),
                "display_name": " ".join(
                    part
                    for part in (
                        user["profile"].get("firstName"),
                        user["profile"].get("lastName"),
                    )
                    if part
                ),
                "status": user.get("status", ""),
                "created_at": user.get("created", ""),
                "last_login": user.get("lastLogin") or "",
                "deactivated_at": user.get("statusChanged") if user.get("status") == "DEPROVISIONED" else "",
            }
            for user in self._get_all("/users", limit=200)
        ]

    def _groups(self) -> list[dict[str, Any]]:
        return [
            {
                "id": group["id"],
                "name": group["profile"].get("name", ""),
                "asset_kind": "group",
                "description": group["profile"].get("description") or "",
                "type": group.get("type", ""),
            }
            for group in self._get_all("/groups", limit=200)
        ]

    def inventory(self) -> list[DiscoveredAsset]:
        assets = [
            DiscoveredAsset(
                source="identity", kind="group", external_id=g["id"], name=g["name"], attributes=g
            )
            for g in self._groups()
        ]
        assets += [
            DiscoveredAsset(
                source="identity", kind="user", external_id=u["id"], name=u["name"], attributes=u
            )
            for u in self._users()
        ]
        return assets

    def fetch(self, dataset: str, since: date | None = None) -> list[dict[str, Any]]:
        if dataset == "users":
            return self._users()
        if dataset == "groups":
            return self._groups()
        if dataset == "memberships":
            rows: list[dict[str, Any]] = []
            for group in self._groups():
                for user in self._get_all(f"/groups/{group['id']}/users", limit=200):
                    rows.append(
                        {
                            "id": f"{group['id']}:{user['id']}",
                            "name": f"{group['name']} / {user['profile'].get('email', '')}",
                            "asset_kind": "membership",
                            "group": group["name"],
                            "group_id": group["id"],
                            "user_id": user["id"],
                            "email": user["profile"].get("email", ""),
                        }
                    )
            return rows
        if dataset == "access_reviews":
            raise ConnectorError(
                "Access-review campaigns require Okta Identity Governance; this tenant's token "
                "cannot read them, so recertification currency is untested rather than passing"
            )
        raise ConnectorError(f"Okta connector cannot serve dataset {dataset!r}")


def _next_link(header: str) -> str | None:
    """Extract the ``rel="next"`` URL from an Okta Link header."""
    for part in header.split(","):
        if 'rel="next"' in part:
            start, end = part.find("<"), part.find(">")
            if start != -1 and end > start:
                return part[start + 1 : end]
    return None
