"""The client half of the AgentCore boundary.

:mod:`countersign.agentcore` is the runtime that AWS hosts. This is the console
side that calls it. With ``COUNTERSIGN_MODEL_MODE=agentcore`` the application
builds no model of its own: onboarding and review leave the process over
``InvokeAgentRuntime`` and come back as JSON, which is then validated against
the same Pydantic models a local run would have produced.

Three properties make that safe to do with something running outside the trust
boundary, and they are the reason the mode exists at all rather than being a
configuration flag over the same code.

*The runtime is never asked for an outcome.* It is asked for prose and proposals.
Both sides run the deterministic test independently, and the console compares the
two counts: a disagreement is recorded on the run and the console's own count is
the one that is published. A runtime that returned "effective" for a control with
three exceptions would change nothing except the trace, which would name it.

*The runtime cannot be trusted into the schedule.* Controls it proposes are
validated against the deterministic test registry and preflighted locally before
a person is offered the approve button, exactly as locally-proposed controls are.

*The runtime is not load-bearing.* If it is unreachable, a review degrades to the
deterministic composer and says so in the trace. The outcome was never the
model's to produce, so an unreachable runtime costs the report its prose and
costs the run nothing.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from countersign.config import Settings

# AgentCore requires a session identifier of at least 33 characters.
SESSION_ID_MIN = 33


class AgentCoreUnavailable(RuntimeError):
    """The deployed runtime could not be reached, or did not answer usefully."""


def session_id(key: str) -> str:
    """A deterministic session id for a unit of work.

    Deterministic so that a retry of the same control run resumes the runtime's
    session rather than starting a second one, and hashed so that a tenant name
    or a control code never has to satisfy AgentCore's identifier rules.
    """
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    identifier = f"countersign-{digest}"
    assert len(identifier) >= SESSION_ID_MIN
    return identifier[:64]


def _client(settings: Settings) -> Any:
    import boto3
    from botocore.config import Config

    return boto3.client(
        "bedrock-agentcore",
        region_name=settings.runtime_region,
        config=Config(
            read_timeout=settings.agentcore_timeout_seconds,
            connect_timeout=10,
            retries={"max_attempts": 2, "mode": "standard"},
        ),
    )


def _payload(response: Any) -> str:
    """Read the runtime's answer, whatever shape boto3 handed back.

    ``response`` is a streaming body for a buffered answer and an iterable of
    chunks for a streamed one. Both occur, and neither is worth a branch at the
    call site.
    """
    body = response.get("response")
    if body is None:
        return ""
    if isinstance(body, bytes | bytearray):
        return bytes(body).decode("utf-8", "replace")
    if hasattr(body, "read"):
        return body.read().decode("utf-8", "replace")
    chunks: list[str] = []
    for chunk in body:
        if isinstance(chunk, dict):
            chunk = chunk.get("chunk", {}).get("bytes", b"")
        if isinstance(chunk, bytes | bytearray):
            chunks.append(bytes(chunk).decode("utf-8", "replace"))
        else:
            chunks.append(str(chunk))
    return "".join(chunks)


def invoke(
    settings: Settings,
    operation: str,
    body: dict[str, Any],
    *,
    session_key: str,
    client: Any | None = None,
) -> dict[str, Any]:
    """Call the deployed runtime once and return its decoded answer.

    Raises :class:`AgentCoreUnavailable` for every failure mode there is, so a
    caller has one thing to catch and one decision to make about it.
    """
    if not settings.agentcore_arn:
        raise AgentCoreUnavailable(
            "COUNTERSIGN_MODEL_MODE=agentcore, but COUNTERSIGN_AGENTCORE_ARN is not set. Deploy "
            "the runtime with deployment/deploy_agentcore.py and set the ARN it prints."
        )

    request: dict[str, Any] = {
        "agentRuntimeArn": settings.agentcore_arn,
        "runtimeSessionId": session_id(session_key),
        "contentType": "application/json",
        "accept": "application/json",
        "payload": json.dumps({"operation": operation, **body}, default=str).encode("utf-8"),
    }
    if settings.agentcore_qualifier:
        request["qualifier"] = settings.agentcore_qualifier

    try:
        response = (client or _client(settings)).invoke_agent_runtime(**request)
        text = _payload(response)
    except AgentCoreUnavailable:
        raise
    except Exception as error:
        raise AgentCoreUnavailable(
            f"invoking {settings.agentcore_arn}: {type(error).__name__}: {error}"
        ) from error

    status = int(response.get("statusCode") or 200)
    try:
        document = json.loads(text)
    except ValueError as error:
        raise AgentCoreUnavailable(
            f"runtime returned {status} with a body that is not JSON: {text[:200]!r}"
        ) from error

    if not isinstance(document, dict):
        raise AgentCoreUnavailable(f"runtime returned {type(document).__name__}, expected an object")
    if status >= 400 or document.get("error"):
        raise AgentCoreUnavailable(
            f"runtime returned {status}: {document.get('error') or text[:200]}"
        )
    return document


def status(settings: Settings, client: Any | None = None) -> dict[str, Any]:
    """Ask the runtime what it is, without spending a model call.

    Used by ``countersign runtime`` and by CI, so that "the console is wired to
    the runtime" is something a person can check in one command rather than a
    claim in a README.
    """
    return invoke(settings, "status", {}, session_key="status", client=client)
