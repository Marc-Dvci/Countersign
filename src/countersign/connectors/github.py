"""GitHub, live.

Emits the same row shapes as the seeded corpus, so a control written against
the corpus runs unchanged against a real organisation. Only read scopes are
used, and only the endpoints listed here are ever called.

**Every listing here is walked to the end.** A control's whole claim is that it
counted the population rather than sampling it, so a connector that quietly
returned the first page would make that claim false in the one place nobody
would check: an organisation with 70 repositories would have 45 of them absent
from a change-approval control, and the control would still report `effective`.
Nothing here truncates. The single bound, ``max_pages``, raises
:class:`ConnectorError` when it is reached, and a run that could not walk its
population records that rather than reporting a pass.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Any

import httpx

from countersign.connectors.base import ConnectorError
from countersign.domain import DiscoveredAsset

API = "https://api.github.com"

# GitHub's maximum page size for every listing used here.
PAGE_SIZE = 100


class GitHubConnector:
    """Read-only GitHub organisation access.

    ``token`` needs ``repo:status``, ``read:org`` and ``public_repo`` at most.
    No endpoint used here mutates anything.
    """

    kind = "github"
    mode = "live"

    def __init__(self, org: str, token: str, timeout: float = 20.0, max_pages: int = 100):
        self.org = org
        self.max_pages = max_pages
        self._repository_cache: list[dict[str, Any]] | None = None
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
            raise ConnectorError(
                f"GitHub {path} returned {response.status_code}: {response.text[:200]}"
            )
        return response.json()

    def _pages(self, path: str, **params: Any) -> Iterator[list[dict[str, Any]]]:
        """Yield every page of a listing, following the ``rel="next"`` Link header.

        ``max_pages`` is a circuit breaker, not a page limit. Reaching it raises,
        so an organisation larger than this connector was configured for produces
        a failed run rather than a short population that reads as a clean one.
        """
        url: str | None = path
        query: dict[str, Any] | None = {"per_page": PAGE_SIZE, **params}
        for _ in range(self.max_pages):
            response = self._client.get(url, params=query)
            if response.status_code >= 400:
                raise ConnectorError(
                    f"GitHub {path} returned {response.status_code}: {response.text[:200]}"
                )
            payload = response.json()
            yield payload if isinstance(payload, list) else [payload]
            url = _next_link(response.headers.get("link", ""))
            query = None  # the next URL already carries the cursor
            if not url:
                return
        raise ConnectorError(
            f"GitHub {path} has more than {self.max_pages} pages of {PAGE_SIZE}. The population "
            f"was not walked to the end, so this run is not evidence of anything: raise max_pages "
            f"rather than testing part of a population"
        )

    def _get_all(self, path: str, **params: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for page in self._pages(path, **params):
            rows.extend(page)
        return rows

    # -- inventory ---------------------------------------------------------

    def _repositories(self) -> list[dict[str, Any]]:
        """Every non-archived repository in the organisation, all pages of it.

        Cached for the life of the connector. A control that reads pull requests
        and deployments would otherwise re-walk the repository list once per
        dataset: the same answer at three times the rate limit.
        """
        if self._repository_cache is None:
            self._repository_cache = [
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
                for repo in self._get_all(f"/orgs/{self.org}/repos", sort="pushed")
                if not repo["archived"]
            ]
        return self._repository_cache

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
        """Every merged pull request into each repository's default branch.

        Paginated to the end of the closed list. When a period is given the walk
        stops at the first page whose entries were all last updated before the
        period began: the list is ordered by ``updated`` descending, and a pull
        request cannot have been merged after it was last updated, so nothing in
        scope lies beyond that point. That is an exact bound, not a sample.
        """
        rows: list[dict[str, Any]] = []
        for repo in self._repositories():
            name = repo["name"]
            for page in self._pages(
                f"/repos/{self.org}/{name}/pulls",
                state="closed",
                base=repo["default_branch"],
                sort="updated",
                direction="desc",
            ):
                for pull in page:
                    if not pull.get("merged_at"):
                        continue
                    if since and pull["merged_at"][:10] < since.isoformat():
                        continue
                    rows.append(self._pull_request_row(name, pull))
                if since and page and all(_touched(pull) < since.isoformat() for pull in page):
                    break
        return rows

    def _pull_request_row(self, repository: str, pull: dict[str, Any]) -> dict[str, Any]:
        reviews = self._get_all(f"/repos/{self.org}/{repository}/pulls/{pull['number']}/reviews")
        approvals = [
            review["user"]["login"]
            for review in reviews
            if review.get("state") == "APPROVED" and review.get("user")
        ]
        return {
            "id": f"{self.org}/{repository}#{pull['number']}",
            "name": pull["title"],
            "asset_kind": "pull_request",
            "repository": repository,
            "author": (pull.get("user") or {}).get("login", ""),
            "merged_by": (pull.get("merged_by") or {}).get("login", ""),
            "merged_at": pull["merged_at"],
            "base": pull["base"]["ref"],
            "approvals": approvals,
            "labels": [label["name"] for label in pull.get("labels", [])],
            "body": pull.get("body") or "",
            "url": pull["html_url"],
        }

    def _deployments(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for repo in self._repositories():
            name = repo["name"]
            for deployment in self._get_all(f"/repos/{self.org}/{name}/deployments"):
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


def _touched(pull: dict[str, Any]) -> str:
    """The day a pull request was last written to, as an ISO date."""
    return (pull.get("updated_at") or pull.get("created_at") or "")[:10]


def _next_link(header: str) -> str | None:
    """The ``rel="next"`` URL from a GitHub Link header, if there is one."""
    for part in header.split(","):
        if 'rel="next"' in part:
            start, end = part.find("<"), part.find(">")
            if start != -1 and end > start:
                return part[start + 1 : end]
    return None
