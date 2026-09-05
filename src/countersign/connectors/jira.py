"""Jira Cloud, live.

Serves ``projects`` and ``issues`` from the REST API. ``automation_settings``
is served from a project property, because Jira exposes no read API for
automation rule configuration; a control that needs it against an estate where
the property is absent records untested population rather than inventing a
setting. That distinction is the whole point of the ``not_tested`` list.
"""

from __future__ import annotations

import base64
from datetime import date
from typing import Any

import httpx

from countersign.connectors.base import ConnectorError
from countersign.domain import DiscoveredAsset

# The property a tenant sets on a project to declare, in a readable place, the
# thresholds its automation enforces. Countersign reads it; it never writes it.
SETTINGS_PROPERTY = "countersign.thresholds"


class JiraConnector:
    """Read-only Jira Cloud access via an API token."""

    kind = "jira"
    mode = "live"

    def __init__(self, site: str, email: str, token: str, timeout: float = 20.0):
        credential = base64.b64encode(f"{email}:{token}".encode()).decode()
        self.site = site.rstrip("/")
        self._client = httpx.Client(
            base_url=f"{self.site}/rest/api/3",
            timeout=timeout,
            headers={"Authorization": f"Basic {credential}", "Accept": "application/json"},
        )

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, **params: Any) -> Any:
        response = self._client.get(path, params=params)
        if response.status_code >= 400:
            raise ConnectorError(f"Jira {path} returned {response.status_code}: {response.text[:200]}")
        return response.json()

    def _projects(self) -> list[dict[str, Any]]:
        payload = self._get("/project/search", maxResults=50)
        return [
            {
                "id": project["key"],
                "key": project["key"],
                "name": project["name"],
                "asset_kind": "project",
                "project_type": project.get("projectTypeKey", ""),
                "url": f"{self.site}/browse/{project['key']}",
            }
            for project in payload.get("values", [])
        ]

    def inventory(self) -> list[DiscoveredAsset]:
        return [
            DiscoveredAsset(
                source="jira",
                kind="project",
                external_id=project["key"],
                name=project["name"],
                attributes=project,
            )
            for project in self._projects()
        ]

    def fetch(self, dataset: str, since: date | None = None) -> list[dict[str, Any]]:
        if dataset == "projects":
            return self._projects()
        if dataset == "issues":
            return self._issues(since)
        if dataset == "automation_settings":
            return self._automation_settings()
        raise ConnectorError(f"Jira connector cannot serve dataset {dataset!r}")

    def _issues(self, since: date | None) -> list[dict[str, Any]]:
        jql = "order by created DESC"
        if since:
            jql = f"created >= '{since.isoformat()}' order by created DESC"
        rows: list[dict[str, Any]] = []
        start = 0
        while True:
            payload = self._get(
                "/search/jql",
                jql=jql,
                maxResults=100,
                startAt=start,
                fields="summary,issuetype,status,created,resolutiondate,priority,labels,project,assignee,reporter",
            )
            issues = payload.get("issues", [])
            for issue in issues:
                fields = issue.get("fields", {})
                rows.append(
                    {
                        "id": issue["key"],
                        "key": issue["key"],
                        "name": fields.get("summary", ""),
                        "asset_kind": "issue",
                        "project": (fields.get("project") or {}).get("key", ""),
                        "issue_type": (fields.get("issuetype") or {}).get("name", ""),
                        "status": (fields.get("status") or {}).get("name", ""),
                        "priority": (fields.get("priority") or {}).get("name", ""),
                        "created_at": fields.get("created", ""),
                        "resolved_at": fields.get("resolutiondate") or "",
                        "labels": fields.get("labels", []),
                        "reporter": ((fields.get("reporter") or {}).get("emailAddress") or ""),
                        "assignee": ((fields.get("assignee") or {}).get("emailAddress") or ""),
                        "url": f"{self.site}/browse/{issue['key']}",
                    }
                )
            start += len(issues)
            if len(issues) < 100 or start >= payload.get("total", start):
                break
        return rows

    def _automation_settings(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for project in self._projects():
            try:
                payload = self._get(f"/project/{project['key']}/properties/{SETTINGS_PROPERTY}")
            except ConnectorError:
                continue
            value = payload.get("value", {})
            rows.append({"id": project["key"], "name": project["name"], **value})
        if not rows:
            raise ConnectorError(
                "No project declares the countersign.thresholds property; automation settings "
                "cannot be read from the Jira API and must not be assumed"
            )
        return rows
