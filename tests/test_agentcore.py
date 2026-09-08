"""The AgentCore boundary, from both sides.

The runtime is a real service and the console really calls it, so the thing
worth testing is the seam: that ``model_mode=agentcore`` leaves this process,
that what comes back is validated rather than believed, and that the console's
own count is what gets published when the two disagree.

Only the transport is stubbed. ``invoke_agent_runtime`` is replaced with a call
into the runtime's own ASGI app, so every test below runs the real
``/invocations`` handler, the real deterministic test and the real report
composer. Nothing here reaches AWS and nothing here invokes a model.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from starlette.testclient import TestClient

from countersign import agentcore, agentcore_client, workflow
from countersign.agentcore_client import AgentCoreUnavailable, session_id
from countersign.config import Settings
from countersign.control_tests import run_test
from countersign.domain import ProposedControl
from countersign.service import Countersign
from countersign.workflow import InvocationTrace

ARN = "arn:aws:bedrock-agentcore:eu-west-1:123456789012:runtime/countersign-abc123"


class LoopbackRuntime:
    """``invoke_agent_runtime``, wired to the runtime's ASGI app in this process.

    boto3's response shape is reproduced exactly, including the streaming body,
    because the decoding of that shape is part of what these tests are for.
    """

    def __init__(self, client: TestClient):
        self.client = client
        self.calls: list[dict] = []

    def invoke_agent_runtime(self, **request):
        self.calls.append(request)
        payload = json.loads(request["payload"])
        response = self.client.post("/invocations", json=payload)
        return {
            "statusCode": response.status_code,
            "contentType": "application/json",
            "runtimeSessionId": request["runtimeSessionId"],
            "response": _Streaming(response.content),
        }


class _Streaming:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload


class DeadRuntime:
    def invoke_agent_runtime(self, **request):
        raise RuntimeError("Could not connect to the endpoint URL")


@pytest.fixture
def runtime_settings(tmp_path) -> Settings:
    return Settings(
        database_path=tmp_path / "countersign.db",
        session_path=tmp_path / "sessions",
        model_mode="agentcore",
        agentcore_arn=ARN,
        allow_live_connectors=False,
        as_of=date(2026, 9, 1),
    )


@pytest.fixture
def loopback(monkeypatch) -> LoopbackRuntime:
    """The runtime, in demo mode, reachable as if it were deployed.

    The runtime forces ``bedrock`` on itself in production; here it is pinned to
    the deterministic composer so the seam can be exercised without a model. The
    handler, the test registry and the response shape are the real ones.
    """
    monkeypatch.setattr(
        agentcore, "_settings", lambda: Settings(model_mode="demo", allow_live_connectors=False)
    )
    client = LoopbackRuntime(TestClient(agentcore.app))
    monkeypatch.setattr(agentcore_client, "_client", lambda settings: client)
    return client


# ---------------------------------------------------------------- the seam


def test_agentcore_mode_leaves_the_process(runtime_settings, loopback, monkeypatch):
    """The deduction this exists to answer: the mode has to actually route."""
    monkeypatch.setattr(
        workflow, "build_model", _refuse("a local model was built in agentcore mode")
    )
    app = Countersign(runtime_settings)
    summary = app.onboard("kestrel", "c.walsh@kestrelpay.eu")

    assert loopback.calls, "onboarding did not reach the runtime"
    assert loopback.calls[0]["agentRuntimeArn"] == ARN
    assert json.loads(loopback.calls[0]["payload"])["operation"] == "onboard"
    assert summary["domains"] and summary["controls"]
    assert any(event["agent"] == "agentcore" for event in summary["trace"])


def test_a_control_run_is_reviewed_by_the_runtime(runtime_settings, loopback, monkeypatch):
    monkeypatch.setattr(
        workflow, "build_model", _refuse("a local model was built in agentcore mode")
    )
    app = Countersign(runtime_settings)
    app.seed("kestrel")

    operations = [json.loads(call["payload"])["operation"] for call in loopback.calls]
    assert "onboard" in operations and "review" in operations

    run = app.store.runs("kestrel", limit=500)[0]
    trace = [event for event in run["trace"] if event["agent"] == "agentcore"]
    assert any(event["event"] == "runtime.count.agreed" for event in trace), trace


def test_the_runtime_is_told_the_control_and_never_the_result(runtime_settings, loopback):
    """It re-derives the population itself, so its count is independent."""
    app = Countersign(runtime_settings)
    app.seed("kestrel")

    reviews = [
        json.loads(call["payload"])
        for call in loopback.calls
        if json.loads(call["payload"])["operation"] == "review"
    ]
    assert reviews
    for review in reviews:
        assert {"control", "period_start", "period_end", "tenant"} <= set(review)
        assert "outcome" not in review and "population" not in review


# ------------------------------------------------------- what is believed


def test_the_consoles_count_wins_when_the_runtime_disagrees(
    runtime_settings, connectors, monkeypatch
):
    """A runtime that returns the wrong outcome changes the trace and nothing else."""

    class LyingRuntime:
        def invoke_agent_runtime(self, **request):
            body = {
                "operation": "review",
                "outcome": "effective",
                "population_size": 1,
                "exception_count": 0,
                "report": {
                    "summary": "Everything is fine.",
                    "proposed_outcome": "effective",
                    "findings": [],
                },
                "challenges": {"challenges": []},
                "trace": [],
            }
            return {"statusCode": 200, "response": _Streaming(json.dumps(body).encode())}

    monkeypatch.setattr(agentcore_client, "_client", lambda settings: LyingRuntime())
    control, result = _leaver_run(connectors)
    trace = InvocationTrace()
    report, _ = workflow.review_via_runtime(runtime_settings, "kestrel", control, result, trace)

    assert result.outcome(control.tolerance) == "ineffective"
    assert any(event["event"].startswith("runtime.count.disagreed") for event in trace.events)
    assert any("disagreement is recorded" in line for line in report.observations)


def test_an_unreachable_runtime_degrades_the_report_not_the_outcome(
    runtime_settings, connectors, monkeypatch
):
    monkeypatch.setattr(agentcore_client, "_client", lambda settings: DeadRuntime())
    control, result = _leaver_run(connectors)
    trace = InvocationTrace()

    report, challenges = workflow.run_review(
        runtime_settings, connectors, control, result, trace, tenant="kestrel"
    )

    assert report.proposed_outcome == result.outcome(control.tolerance) == "ineffective"
    assert report.findings, "the deterministic composer still writes the report"
    assert any("runtime.unavailable.degraded" in event["event"] for event in trace.events)
    assert any(event["event"] == "deterministic.compose" for event in trace.events)


def test_onboarding_fails_loudly_rather_than_inventing_a_programme(runtime_settings, monkeypatch):
    """There is no honest fallback for onboarding, so there is no fallback."""
    monkeypatch.setattr(agentcore_client, "_client", lambda settings: DeadRuntime())
    with pytest.raises(AgentCoreUnavailable, match="Could not connect"):
        workflow.run_onboarding(runtime_settings, {}, "kestrel", InvocationTrace())


def test_agentcore_mode_without_an_arn_says_so(tmp_path):
    settings = Settings(
        database_path=tmp_path / "db.sqlite", model_mode="agentcore", allow_live_connectors=False
    )
    assert "not configured" in settings.describe_mode()
    with pytest.raises(AgentCoreUnavailable, match="COUNTERSIGN_AGENTCORE_ARN"):
        agentcore_client.invoke(settings, "status", {}, session_key="x")


# --------------------------------------------------------- the runtime itself


def test_the_runtime_answers_ping_and_status(loopback):
    assert loopback.client.get("/ping").json() == {"status": "healthy"}

    payload = loopback.client.post("/invocations", json={"operation": "status"}).json()
    assert payload["service"] == "countersign-agentcore"
    assert "leaver_access_revocation" in payload["test_kinds"]
    assert "cannot" in payload["grants"]


def test_the_runtime_refuses_an_operation_it_does_not_have(loopback):
    response = loopback.client.post("/invocations", json={"operation": "approve"})
    assert response.status_code == 400
    assert "unknown operation" in response.json()["error"]


def test_the_runtime_never_calls_itself(monkeypatch):
    """A runtime left in agentcore mode would invoke itself, forever."""
    monkeypatch.setenv("COUNTERSIGN_MODEL_MODE", "agentcore")
    monkeypatch.setenv("COUNTERSIGN_AGENTCORE_ARN", ARN)
    assert agentcore._settings().model_mode == "bedrock"


def test_a_session_id_satisfies_the_runtimes_rules():
    """AgentCore rejects a session id shorter than 33 characters."""
    identifier = session_id("review-kestrel-IAM-JML-01-2026-09-01")
    assert len(identifier) >= 33
    assert identifier == session_id("review-kestrel-IAM-JML-01-2026-09-01")
    assert identifier != session_id("review-kestrel-IAM-JML-01-2026-12-01")


# ---------------------------------------------------------------- helpers


def _refuse(message: str):
    def refuse(*args, **kwargs):
        raise AssertionError(message)

    return refuse


def _leaver_run(connectors):
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
        control.test_kind,
        connectors,
        control.parameters,
        date(2026, 6, 2),
        date(2026, 9, 1),
    )
    return control, result


def test_the_request_matches_the_real_invoke_agent_runtime_contract(runtime_settings):
    """The one defect class the loopback cannot catch: a wrong parameter name.

    botocore validates the request against the service model here, and validates
    the response against it too, so a rename or a missing required field fails in
    the suite rather than on the first real invocation.
    """
    import boto3
    from botocore.stub import Stubber

    client = boto3.client(
        "bedrock-agentcore",
        region_name="eu-west-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    stubber = Stubber(client)
    stubber.add_response(
        "invoke_agent_runtime",
        {"statusCode": 200, "contentType": "application/json", "response": b'{"ok": true}'},
        {
            "agentRuntimeArn": ARN,
            "runtimeSessionId": session_id("status"),
            "qualifier": "DEFAULT",
            "contentType": "application/json",
            "accept": "application/json",
            "payload": b'{"operation": "status"}',
        },
    )
    with stubber:
        assert agentcore_client.invoke(
            runtime_settings, "status", {}, session_key="status", client=client
        ) == {"ok": True}
    stubber.assert_no_pending_responses()
