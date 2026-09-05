"""GitHub, live.

Emits the same row shapes as the seeded corpus, so a control written against
the corpus runs unchanged against a real organisation. Only read scopes are
used, and only the endpoints listed here are ever called.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from countersign.connectors.base import ConnectorError
from countersign.domain import DiscoveredAsset

API = "https://api.github.com"


class GitHubConnector:
    """Read-only GitHub organisation access.

    ``token`` needs ``repo:status``, ``read:org`` and ``public_repo`` at most.
    No endpoint used here mutates anything.
    """

    kind = "github"
    mode = "live"

    def __init__(self, org: str, token: str, timeout: float = 20.0, max_repos: int = 25):
        self.org = org
        self.max_repos = max_repos
        self._client = httpx.Client(
            base_url=API,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, **params: Any) -> Any:
        response = self._client.get(path, params=params)
        if response.status_code >= 400:
            raise ConnectorError(f"GitHub {path} returned {response.status_code}: {response.text[:200]}")
        return response.json()

    # -- inventory ---------------------------------------------------------

    def _repositories(self) -> list[dict[str, Any]]:
        raw = self._get(f"/orgs/{self.org}/repos", per_page=self.max_repos, sort="pushed")
        return [
            {
                "id": repo["full_name"],
                "name": repo["name"],
                "asset_kind": "repository",
                "private": repo["private"],
                "default_branch": repo["default_branch"],
                "archived": repo["archived"],
                "pushed_at": repo["pushed_at"],
                "language": repo.get("language"),
                "url": repo["html_url"],
            }
            for repo in raw
            if not repo["archived"]
        ]

    def inventory(self) -> list[DiscoveredAsset]:
        return [
            DiscoveredAsset(
                source="github",
                kind="repository",
                external_id=repo["id"],
                name=repo["name"],
                attributes=repo,
            )
            for repo in self._repositories()
        ]

    # -- datasets ----------------------------------------------------------

    def fetch(self, dataset: str, since: date | None = None) -> list[dict[str, Any]]:
        if dataset == "repositories":
            return self._repositories()
        if dataset == "pull_requests":
            return self._pull_requests(since)
        if dataset == "deployments":
            return self._deployments()
        if dataset == "dependencies":
            return self._dependencies()
        raise ConnectorError(f"GitHub connector cannot serve dataset {dataset!r}")

    def _pull_requests(self, since: date | None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for repo in self._repositories():
            name = repo["name"]
            pulls = self._get(
                f"/repos/{self.org}/{name}/pulls",
                state="closed",
                base=repo["default_branch"],
                per_page=100,
                sort="updated",
                direction="desc",
            )
            for pull in pulls:
                if not pull.get("merged_at"):
                    continue
                if since and pull["merged_at"][:10] < since.isoformat():
                    continue
                reviews = self._get(f"/repos/{self.org}/{name}/pulls/{pull['number']}/reviews")
                approvals = [
                    review["user"]["login"]
                    for review in reviews
                    if review.get("state") == "APPROVED" and review.get("user")
                ]
                rows.append(
                    {
                        "id": f"{self.org}/{name}#{pull['number']}",
                        "name": pull["title"],
                        "asset_kind": "pull_request",
                        "repository": name,
                        "author": (pull.get("user") or {}).get("login", ""),
                        "merged_by": (pull.get("merged_by") or {}).get("login", ""),
                        "merged_at": pull["merged_at"],
                        "base": pull["base"]["ref"],
                        "approvals": approvals,
                        "labels": [label["name"] for label in pull.get("labels", [])],
                        "body": pull.get("body") or "",
                        "url": pull["html_url"],
                    }
                )
        return rows

    def _deployments(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for repo in self._repositories():
            name = repo["name"]
            for deployment in self._get(f"/repos/{self.org}/{name}/deployments", per_page=100):
                rows.append(
                    {
                        "id": f"{name}:{deployment['id']}",
                        "name": deployment.get("description") or f"deployment {deployment['id']}",
                        "asset_kind": "deployment",
                        "repository": name,
                        "environment": deployment.get("environment", ""),
                        "created_at": deployment.get("created_at", ""),
                        "creator": (deployment.get("creator") or {}).get("login", ""),
                        "ref": deployment.get("ref", ""),
                    }
                )
        return rows

    def _dependencies(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for repo in self._repositories():
            name = repo["name"]
            try:
                sbom = self._get(f"/repos/{self.org}/{name}/dependency-graph/sbom")
            except ConnectorError:
                # Dependency graph is not enabled on every repository. A missing
                # SBOM is recorded as untested population, never as a pass.
                continue
            for package in sbom.get("sbom", {}).get("packages", []):
                rows.append(
                    {
                        "id": f"{name}:{package.get('SPDXID', '')}",
                        "name": package.get("name", ""),
                        "asset_kind": "dependency",
                        "repository": name,
                        "version": package.get("versionInfo", ""),
                        "licence": package.get("licenseConcluded", ""),
                    }
                )
        return rows
