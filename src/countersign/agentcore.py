"""The Amazon Bedrock AgentCore runtime.

A stateless HTTP service exposing the two endpoints AgentCore requires,
``/ping`` and ``/invocations``. It runs the Strands agents and returns their
structured output. It does not hold the database, it cannot reach the tenant's
canonical state, and its IAM role grants model invocation and telemetry only.

That division is the deployment-shaped version of the same rule that holds
everywhere else in this codebase. The runtime can propose a taxonomy, design a
control or write a report. The deterministic test still decides the outcome and
a person still signs, and both of those happen in the application, on the other
side of this boundary.

Three operations are accepted:

``onboard``   given a tenant's inventory, propose the profile, the risk domains
              and the controls.
``review``    given a control and a period, re-run the deterministic test, write
              the report and challenge the findings it proposes.
``status``    what this runtime is, without spending a model call. It exists so
              that "the console is wired to the runtime" is a thing a person, or
              a CI job, can check in one command.

The application sends a deterministic session id so a retry resumes rather than
duplicating, and always re-runs the injection scan and the outcome check on its
own side. Nothing this service returns is trusted to be either safe or final.
"""

from __future__ import annotations

import os
from datetime import date

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from countersign.config import Settings
from countersign.connectors import build_connectors, describe_live_state
from countersign.control_tests import REGISTRY, run_test
from countersign.domain import ProposedControl
from countersign.workflow import InvocationTrace, run_onboarding, run_review


def _settings() -> Settings:
    """Force the local model path on, whatever the environment says.

    A runtime left in ``agentcore`` mode would call *itself* through
    InvokeAgentRuntime, which is a loop that ends in a bill, so that mode is
    refused unconditionally. ``demo`` is refused too, because a deployed runtime
    running the deterministic composer would be an expensive way to do nothing.

    ``COUNTERSIGN_RUNTIME_DETERMINISTIC`` lifts only the second of those, and
    exists for one reason: it lets somebody with no AWS account run this
    container, point a console at it and exercise the entire invocation path.
    What that proves is the transport, the handler, the independent re-run of the
    deterministic test and the console's cross-check of the two counts. It
    proves nothing about a model, and the runtime says so in its own status.
    """
    settings = Settings()
    if settings.model_mode == "demo" and _deterministic_runtime():
        return settings
    if settings.model_mode != "bedrock":
        settings = settings.model_copy(update={"model_mode": "bedrock"})
    return settings


def _deterministic_runtime() -> bool:
    return os.environ.get("COUNTERSIGN_RUNTIME_DETERMINISTIC", "").lower() in {"1", "true", "yes"}


async def ping(request: Request) -> JSONResponse:
    return JSONResponse({"status": "healthy"})


async def invocations(request: Request) -> JSONResponse:
    payload = await request.json()
    operation = payload.get("operation", "review")
    tenant = payload.get("tenant", "kestrel")
    settings = _settings()
    connectors = build_connectors(
        tenant,
        allow_live=settings.allow_live_connectors,
        allow_mixed_sources=settings.allow_source_mixing,
    )
    trace = InvocationTrace()

    try:
        if operation == "status":
            return JSONResponse(
                {
                    "operation": "status",
                    "service": "countersign-agentcore",
                    "healthy": True,
                    "model_mode": settings.model_mode,
                    "invokes_a_model": settings.uses_model,
                    "model_id": settings.bedrock_model_id,
                    "region": settings.bedrock_region,
                    "test_kinds": sorted(REGISTRY),
                    "sources": describe_live_state(connectors),
                    "grants": (
                        "This runtime proposes and explains. It holds no database, it cannot "
                        "accept a domain, approve a control or close a finding, and the outcome "
                        "of every control is counted by the console that called it."
                    ),
                }
            )

        if operation == "onboard":
            profile, taxonomy, proposals = run_onboarding(settings, connectors, tenant, trace)
            return JSONResponse(
                {
                    "operation": "onboard",
                    "tenant": tenant,
                    "profile": profile.model_dump(mode="json"),
                    "taxonomy": taxonomy.model_dump(mode="json"),
                    "controls": [p.model_dump(mode="json") for p in proposals],
                    "trace": trace.events,
                    "model_mode": settings.model_mode,
                }
            )

        if operation == "review":
            control = ProposedControl.model_validate(payload["control"])
            period_end = date.fromisoformat(payload["period_end"])
            period_start = date.fromisoformat(payload["period_start"])
            # The runtime re-runs the deterministic test rather than accepting a
            # result from its caller. A settled outcome that arrived over the
            # wire is a settled outcome somebody could have edited.
            result = run_test(
                control.test_kind, connectors, control.parameters, period_start, period_end
            )
            report, challenges = run_review(
                settings, connectors, control, result, trace, tenant=tenant
            )
            return JSONResponse(
                {
                    "operation": "review",
                    "tenant": tenant,
                    "control": control.code,
                    "outcome": result.outcome(control.tolerance),
                    "population_size": result.population_size,
                    "exception_count": result.exception_count,
                    "not_tested": result.not_tested,
                    "report": report.model_dump(mode="json"),
                    "challenges": challenges.model_dump(mode="json"),
                    "trace": trace.events,
                    "model_mode": settings.model_mode,
                }
            )

        return JSONResponse(
            {"error": f"unknown operation {operation!r}; expected onboard, review or status"},
            status_code=400,
        )

    except Exception as error:  # the runtime reports failure; it never invents a result
        return JSONResponse(
            {"error": f"{type(error).__name__}: {error}", "trace": trace.events},
            status_code=500,
        )


app = Starlette(
    routes=[
        Route("/ping", ping),
        Route("/invocations", invocations, methods=["POST"]),
        # The path AgentCore's own data plane exposes. Serving it here means the
        # console's client can be pointed straight at this container with
        # COUNTERSIGN_AGENTCORE_ENDPOINT and reach it through the same boto3
        # call, the same signing and the same request shape it uses against AWS.
        # That is what makes the invocation path runnable without an account.
        Route("/runtimes/{arn:path}/invocations", invocations, methods=["POST"]),
    ]
)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))


if __name__ == "__main__":
    main()
