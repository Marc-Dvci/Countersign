"""The live adapters, and the boundary around them.

Two claims are tested here, and both of them are claims the product makes in its
own marketing. The first is that a control walks the whole population: an
adapter that returns the first page of an organisation would make every control
built on it a sample dressed as a census, and it would never fail visibly. The
second is that real evidence and seeded evidence never appear in the same run.

No network. ``httpx.MockTransport`` serves paginated fixtures that carry the
same Link headers GitHub and the same ``isLast`` field Jira does.
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from countersign.connectors import (
    ConnectorError,
    CorpusConnector,
    DisconnectedConnector,
    GitHubConnector,
    JiraConnector,
    build_connectors,
    describe_live_state,
)

ORG = "acme"


# ---------------------------------------------------------------- fixtures


def _repo(index: int) -> dict:
    return {
        "full_name": f"{ORG}/repo-{index}",
        "name": f"repo-{index}",
        "private": True,
        "default_branch": "main",
        "archived": False,
        "pushed_at": "2026-08-01T00:00:00Z",
        "language": "Python",
        "html_url": f"https://github.com/{ORG}/repo-{index}",
    }


def _pull(number: int, merged: str, updated: str) -> dict:
    return {
        "number": number,
        "title": f"change {number}",
        "merged_at": merged,
        "updated_at": updated,
        "user": {"login": "author"},
        "merged_by": {"login": "merger"},
        "base": {"ref": "main"},
        "labels": [],
        "body": "",
        "html_url": f"https://github.com/{ORG}/repo-1/pull/{number}",
    }


def github_with(pages: dict[str, list[list[dict]]], repo_count: int = 0) -> GitHubConnector:
    """A connector whose transport serves the given pages, with Link headers.

    ``pages`` maps a path to the list of pages it serves. Anything not listed
    answers with an empty page, which is what an organisation of mostly-quiet
    repositories looks like.
    """
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen.append(str(request.url))
        index = int(request.url.params.get("page", "1"))
        served = pages.get(path, [[]])
        body = served[index - 1] if index <= len(served) else []
        headers = {}
        if index < len(served):
            headers["link"] = f'<https://api.github.com{path}?page={index + 1}>; rel="next"'
        return httpx.Response(200, json=body, headers=headers)

    connector = GitHubConnector(ORG, "token")
    connector._client = httpx.Client(
        base_url="https://api.github.com", transport=httpx.MockTransport(handle)
    )
    connector.requests = seen
    return connector


# ------------------------------------------------------- walking the whole org


def test_every_repository_is_returned_not_the_first_page():
    """Seventy repositories over three pages. All seventy, or the control lies."""
    pages = [[_repo(i) for i in range(0, 30)], [_repo(i) for i in range(30, 60)],
             [_repo(i) for i in range(60, 70)]]
    connector = github_with({f"/orgs/{ORG}/repos": pages})
    repositories = connector.fetch("repositories")
    assert len(repositories) == 70
    assert {repo["name"] for repo in repositories} == {f"repo-{i}" for i in range(70)}


def test_archived_repositories_are_excluded_from_every_page():
    pages = [[_repo(0), {**_repo(1), "archived": True}], [{**_repo(2), "archived": True}, _repo(3)]]
    connector = github_with({f"/orgs/{ORG}/repos": pages})
    assert [repo["name"] for repo in connector.fetch("repositories")] == ["repo-0", "repo-3"]


def test_pull_requests_are_read_from_every_repository():
    """The population is merges across the organisation, not across page one."""
    connector = github_with(
        {
            f"/orgs/{ORG}/repos": [[_repo(0)], [_repo(1)]],
            f"/repos/{ORG}/repo-0/pulls": [[_pull(1, "2026-08-01T00:00:00Z", "2026-08-01T00:00:00Z")]],
            f"/repos/{ORG}/repo-1/pulls": [[_pull(2, "2026-08-02T00:00:00Z", "2026-08-02T00:00:00Z")]],
        }
    )
    rows = connector.fetch("pull_requests", since=date(2026, 7, 1))
    assert {row["repository"] for row in rows} == {"repo-0", "repo-1"}


def test_pull_requests_are_paginated_within_a_repository():
    connector = github_with(
        {
            f"/orgs/{ORG}/repos": [[_repo(0)]],
            f"/repos/{ORG}/repo-0/pulls": [
                [_pull(n, "2026-08-01T00:00:00Z", "2026-08-01T00:00:00Z") for n in range(0, 5)],
                [_pull(n, "2026-07-01T00:00:00Z", "2026-07-15T00:00:00Z") for n in range(5, 9)],
            ],
        }
    )
    assert len(connector.fetch("pull_requests", since=date(2026, 6, 1))) == 9


def test_the_walk_stops_only_where_the_ordering_proves_it_can():
    """Ordered by updated descending, so a page updated entirely before the
    period began cannot hide a merge inside it. Stopping there is exact."""
    connector = github_with(
        {
            f"/orgs/{ORG}/repos": [[_repo(0)]],
            f"/repos/{ORG}/repo-0/pulls": [
                [_pull(1, "2026-08-01T00:00:00Z", "2026-08-01T00:00:00Z")],
                [_pull(2, "2025-01-01T00:00:00Z", "2025-01-02T00:00:00Z")],
                [_pull(3, "2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z")],
            ],
        }
    )
    rows = connector.fetch("pull_requests", since=date(2026, 7, 1))
    assert [row["id"] for row in rows] == [f"{ORG}/repo-0#1"]
    assert not any("page=3" in url for url in connector.requests), "walked past a proven bound"


def test_the_repository_list_is_walked_once_per_connector():
    """Three datasets, one repository inventory. Rate limits are finite."""
    connector = github_with(
        {
            f"/orgs/{ORG}/repos": [[_repo(0)]],
            f"/repos/{ORG}/repo-0/pulls": [[]],
            f"/repos/{ORG}/repo-0/deployments": [[]],
        }
    )
    connector.fetch("repositories")
    connector.fetch("pull_requests")
    connector.fetch("deployments")
    assert sum(1 for url in connector.requests if "/orgs/" in url) == 1


def test_an_organisation_larger_than_the_bound_fails_instead_of_truncating():
    """The one case where a partial answer is possible, and it raises."""

    def endless(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[_repo(1)],
            headers={"link": '<https://api.github.com/orgs/acme/repos?page=99>; rel="next"'},
        )

    connector = GitHubConnector(ORG, "token", max_pages=3)
    connector._client = httpx.Client(
        base_url="https://api.github.com", transport=httpx.MockTransport(endless)
    )
    with pytest.raises(ConnectorError, match="not walked to the end"):
        connector.fetch("repositories")


def test_a_failed_page_is_an_error_not_a_short_list():
    def flaky(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("page") == "2":
            return httpx.Response(502, text="bad gateway")
        return httpx.Response(
            200,
            json=[_repo(1)],
            headers={"link": '<https://api.github.com/orgs/acme/repos?page=2>; rel="next"'},
        )

    connector = GitHubConnector(ORG, "token")
    connector._client = httpx.Client(
        base_url="https://api.github.com", transport=httpx.MockTransport(flaky)
    )
    with pytest.raises(ConnectorError, match="502"):
        connector.fetch("repositories")


def test_jira_projects_are_paginated_to_the_end():
    """The issue walk is bounded by the project list, so the project list counts."""

    def handle(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params.get("startAt", "0"))
        every = [{"key": f"P{i}", "name": f"Project {i}", "projectTypeKey": "software"}
                 for i in range(0, 130)]
        page = every[start : start + 100]
        return httpx.Response(
            200, json={"values": page, "total": len(every), "isLast": start + len(page) >= len(every)}
        )

    connector = JiraConnector("https://acme.atlassian.net", "a@b.c", "token")
    connector._client = httpx.Client(
        base_url="https://acme.atlassian.net/rest/api/3", transport=httpx.MockTransport(handle)
    )
    assert len(connector.fetch("projects")) == 130


# ------------------------------------------------- the production boundary


def test_with_no_credentials_every_source_is_the_seeded_corpus(monkeypatch):
    for name in ("GITHUB_ORG", "GITHUB_TOKEN", "JIRA_SITE", "OKTA_DOMAIN"):
        monkeypatch.delenv(name, raising=False)
    connectors = build_connectors("kestrel")
    assert all(isinstance(c, CorpusConnector) for c in connectors.values())


def test_one_live_source_disconnects_the_rest_rather_than_inventing_them(monkeypatch):
    """The defect this prevents: real merges joined to a fictional leaver list."""
    monkeypatch.setenv("GITHUB_ORG", ORG)
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    connectors = build_connectors("kestrel")

    assert connectors["github"].mode == "live"
    assert isinstance(connectors["hris"], DisconnectedConnector)
    assert isinstance(connectors["obligations"], DisconnectedConnector)
    with pytest.raises(ConnectorError, match="not connected"):
        connectors["hris"].fetch("employees")


def test_the_console_says_which_sources_are_disconnected(monkeypatch):
    monkeypatch.setenv("GITHUB_ORG", ORG)
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    described = {row["source"]: row for row in describe_live_state(build_connectors("kestrel"))}
    assert described["hris"]["mode"] == "disconnected"
    assert described["hris"]["connected"] is False
    assert "ALLOW_SOURCE_MIXING" in described["hris"]["note"]


def test_mixing_is_available_but_has_to_be_asked_for(monkeypatch):
    monkeypatch.setenv("GITHUB_ORG", ORG)
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    connectors = build_connectors("kestrel", allow_mixed_sources=True)
    assert connectors["github"].mode == "live"
    assert isinstance(connectors["hris"], CorpusConnector)


def test_allow_live_false_keeps_a_credentialed_environment_on_the_corpus(monkeypatch):
    """A judge with GitHub credentials in the shell still sees the demonstration."""
    monkeypatch.setenv("GITHUB_ORG", ORG)
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    connectors = build_connectors("kestrel", allow_live=False)
    assert all(isinstance(c, CorpusConnector) for c in connectors.values())
