"""The HTTP surface: what is readable without an account, and what is not writable."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from countersign.api import build_app


@pytest.fixture
def client(settings, app):
    app.seed("kestrel")
    with TestClient(build_app(settings)) as client:
        yield client


def sign_in(client, settings):
    response = client.post(
        "/api/session",
        json={"username": settings.console_user, "password": settings.console_password},
    )
    assert response.status_code == 200
    return response


# ------------------------------------------------------------------- reads


@pytest.mark.parametrize(
    "path",
    [
        "/api/state",
        "/api/registry",
        "/api/tenants/kestrel/state",
        "/api/tenants/kestrel/profile",
        "/api/tenants/kestrel/domains",
        "/api/tenants/kestrel/controls",
        "/api/tenants/kestrel/findings",
        "/api/tenants/kestrel/obligations",
        "/api/tenants/kestrel/runs",
        "/api/tenants/kestrel/audit",
    ],
)
def test_every_read_works_without_an_account(client, path):
    """A product that needs credentials to be understood will not be understood."""
    response = client.get(path)
    assert response.status_code == 200, path


def test_the_console_serves_its_own_page(client):
    assert client.get("/").status_code == 200
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/assets/app.css").status_code == 200
    assert client.get("/assets/mark.svg").status_code == 200


def test_an_unknown_tenant_is_a_404(client):
    assert client.get("/api/tenants/acme/controls").status_code == 404


def test_state_reports_the_model_mode_and_the_chain(client):
    payload = client.get("/api/state").json()
    assert payload["model_mode"] == "demo"
    assert payload["audit"]["intact"] is True
    assert payload["summary"]["open_findings"] == 6
    assert len(payload["tenants"]) == 3


def test_a_control_carries_its_runs_and_findings(client):
    payload = client.get("/api/tenants/kestrel/controls/DORA-INC-01").json()
    assert payload["runs"] and payload["findings"]
    assert payload["runs"][0]["outcome"] == "ineffective"


def test_a_run_carries_the_whole_population(client):
    run_id = client.get("/api/tenants/kestrel/runs").json()[0]["id"]
    run = client.get(f"/api/tenants/kestrel/runs/{run_id}").json()
    assert len(run["population"]) == run["population_size"]
    assert sum(1 for item in run["population"] if not item["passed"]) == run["exception_count"]


# ------------------------------------------------------------------ writes


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("post", "/api/tenants/kestrel/seed", None),
        ("post", "/api/tenants/kestrel/onboard", None),
        ("post", "/api/tenants/kestrel/domains/IAM/decision", {"accept": True}),
        ("post", "/api/tenants/kestrel/controls/CHG-APP-01/approve", None),
        ("post", "/api/tenants/kestrel/controls/CHG-APP-01/run", {}),
        ("post", "/api/tenants/kestrel/run-due", None),
    ],
)
def test_no_write_works_without_a_session(client, method, path, body):
    response = getattr(client, method)(path, json=body)
    assert response.status_code == 401, path


def test_running_a_control_needs_a_session_but_grants_nothing(client, settings):
    sign_in(client, settings)
    response = client.post("/api/tenants/kestrel/controls/CHG-APP-01/run", json={"lookback": 91})
    assert response.status_code == 200
    outcome = response.json()
    assert outcome["outcome"] == "ineffective" and outcome["exceptions"] == 2


def test_the_api_refuses_to_approve_an_untestable_control(client, settings):
    sign_in(client, settings)
    response = client.post("/api/tenants/kestrel/controls/IAM-RECERT-01/approve")
    assert response.status_code == 409
    assert "cannot be scheduled" in response.json()["error"]


def test_the_api_refuses_a_bare_risk_acceptance(client, settings):
    sign_in(client, settings)
    finding = client.get("/api/tenants/kestrel/findings?status=open").json()[0]
    response = client.post(
        f"/api/tenants/kestrel/findings/{finding['id']}/decision",
        json={"status": "risk_accepted", "note": "ok"},
    )
    assert response.status_code == 409
    assert "stated reason" in response.json()["error"]


def test_the_api_refuses_to_close_a_finding_by_decision(client, settings):
    sign_in(client, settings)
    finding = client.get("/api/tenants/kestrel/findings?status=open").json()[0]
    response = client.post(
        f"/api/tenants/kestrel/findings/{finding['id']}/decision",
        json={"status": "closed", "note": "we have fixed it, honestly"},
    )
    assert response.status_code == 409
    assert "not closed by decision" in response.json()["error"]


def test_signing_out_ends_the_session(client, settings):
    sign_in(client, settings)
    assert client.get("/api/state").json()["user"] is not None
    client.delete("/api/session")
    assert client.get("/api/state").json()["user"] is None
    assert client.post("/api/tenants/kestrel/run-due").status_code == 401


def test_bad_credentials_are_refused(client):
    assert client.post("/api/session", json={"username": "risk", "password": "no"}).status_code == 401


def test_security_headers_are_set(client):
    headers = client.get("/").headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in headers["Content-Security-Policy"]
