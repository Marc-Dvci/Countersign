"""The AgentCore invocation path, over a socket, with no AWS account.

`tests/test_agentcore.py` stubs the transport so it can drive the seam quickly.
This does not stub it. A real `boto3` `bedrock-agentcore` client, with real SigV4
signing and the real `InvokeAgentRuntime` request shape, is pointed at the
runtime's own ASGI app served by uvicorn on a local port. What crosses the wire
is what crosses it against AWS.

That matters because the strongest claim this project makes about the cloud is
the one hardest for a reader to check: that `model_mode=agentcore` is a real
routing rather than a configuration flag. Here it is checked without credentials,
which means anybody can check it. What it does not prove is anything about a
model, and the runtime says as much in its own status response.
"""

from __future__ import annotations

import socket
import threading
import time
from datetime import date

import pytest
import uvicorn

from countersign import agentcore, agentcore_client, workflow
from countersign.config import Settings
from countersign.control_tests import run_test
from countersign.domain import ProposedControl
from countersign.workflow import InvocationTrace

ARN = "arn:aws:bedrock-agentcore:eu-west-1:123456789012:runtime/countersign-local"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture(scope="module")
def runtime_url() -> str:
    """The runtime container's app, served on a local port for the length of the module."""
    port = _free_port()
    config = uvicorn.Config(agentcore.app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 30
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("the runtime did not start")
        time.sleep(0.05)

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture
def settings(tmp_path, runtime_url, monkeypatch) -> Settings:
    # The runtime runs deterministically, which is what a judge with no model
    # access gets. Credentials are the local placeholders boto3 needs in order to
    # sign; nothing checks them at the other end.
    monkeypatch.setenv("COUNTERSIGN_RUNTIME_DETERMINISTIC", "true")
    monkeypatch.setenv("COUNTERSIGN_MODEL_MODE", "demo")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "local")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "local")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    return Settings(
        database_path=tmp_path / "countersign.db",
        session_path=tmp_path / "sessions",
        model_mode="agentcore",
        agentcore_arn=ARN,
        agentcore_endpoint_url=runtime_url,
        allow_live_connectors=False,
        as_of=date(2026, 9, 1),
    )


def test_the_client_reaches_the_runtime_through_boto3(settings):
    """One real InvokeAgentRuntime call, signed and sent over a socket."""
    payload = agentcore_client.status(settings)
    assert payload["service"] == "countersign-agentcore"
    assert payload["invokes_a_model"] is False, "the runtime says plainly what it is doing"
    assert "leaver_access_revocation" in payload["test_kinds"]


def test_a_review_round_trip_crosses_the_wire(settings, connectors):
    """The whole path: console, boto3, HTTP, runtime, deterministic test, back.

    The runtime re-runs the control itself and returns its own count. The console
    compares it with its own and records `runtime.count.agreed`, which is the
    property that makes a remote review worth trusting at all.
    """
    control = ProposedControl(
        code="IAM-JML-01",
        title="Leaver access removed",
        objective="Leaver access is removed inside the registered grace period.",
        nature="detective",
        test_kind="leaver_access_revocation",
        parameters={"obligation_reference": "INT-ACC-REVOKE"},
        periodicity="weekly",
        periodicity_rationale="Leavers arrive continuously and the grace period is one day.",
        tolerance=0,
        severity_if_failed="high",
    )
    result = run_test(
        control.test_kind, connectors, control.parameters, date(2026, 6, 2), date(2026, 9, 1)
    )
    trace = InvocationTrace()

    report, challenges = workflow.run_review(
        settings, connectors, control, result, trace, tenant="kestrel"
    )

    assert report.proposed_outcome == result.outcome(control.tolerance) == "ineffective"
    assert report.findings, "the review came back with something to act on"
    events = [event["event"] for event in trace.events if event["agent"] == "agentcore"]
    assert "runtime.count.agreed" in events, events
    assert "runtime.completed.review" in events, events
    assert not any("degraded" in event for event in events), "it really answered"


def test_a_runtime_that_is_not_there_still_produces_a_report(settings, connectors):
    """The same console, pointed at a port nothing is listening on."""
    settings = settings.model_copy(
        update={"agentcore_endpoint_url": f"http://127.0.0.1:{_free_port()}"}
    )
    control = ProposedControl(
        code="IAM-DORM-01",
        title="Dormant privileged access",
        objective="Privileged accounts are used or removed.",
        nature="detective",
        test_kind="dormant_privileged_access",
        parameters={"obligation_reference": "INT-ACC-DORMANT"},
        periodicity="monthly",
        periodicity_rationale="The register measures dormancy in days, so monthly is the floor.",
        tolerance=0,
        severity_if_failed="medium",
    )
    result = run_test(
        control.test_kind, connectors, control.parameters, date(2026, 6, 2), date(2026, 9, 1)
    )
    trace = InvocationTrace()

    report, _ = workflow.run_review(
        settings, connectors, control, result, trace, tenant="kestrel"
    )

    assert report.proposed_outcome == result.outcome(control.tolerance)
    assert any("runtime.unavailable.degraded" in event["event"] for event in trace.events)
