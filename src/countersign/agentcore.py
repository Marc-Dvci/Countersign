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

Two operations are accepted:

``onboard``   given a tenant's inventory, propose the profile, the risk domains
              and the controls.
``review``    given a settled test result, write the report and challenge the
              findings it proposes.

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
from countersign.connectors import build_connectors
from countersign.control_tests import run_test
from countersign.domain import ProposedControl
from countersign.workflow import InvocationTrace, run_onboarding, run_review


def _settings() -> Settings:
    """Force the model path on. An AgentCore runtime that ran the deterministic
    composer would be an expensive way to do nothing."""
    settings = Settings()
    if settings.model_mode == "demo":
        settings = settings.model_copy(update={"model_mode": "bedrock"})
    return settings


async def ping(request: Request) -> JSONResponse:
    return JSONResponse({"status": "healthy"})


async def invocations(request: Request) -> JSONResponse:
    payload = await request.json()
    operation = payload.get("operation", "review")
    tenant = payload.get("tenant", "kestrel")
    settings = _settings()
    connectors = build_connectors(tenant, allow_live=settings.allow_live_connectors)
    trace = InvocationTrace()

    try:
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
            report, challenges = run_review(settings, connectors, control, result, trace)
            return JSONResponse(
                {
                    "operation": "review",
                    "tenant": tenant,
                    "control": control.code,
                    "outcome": result.outcome(control.tolerance),
                    "population_size": result.population_size,
                    "exception_count": result.exception_count,
                    "report": report.model_dump(mode="json"),
                    "challenges": challenges.model_dump(mode="json"),
                    "trace": trace.events,
                    "model_mode": settings.model_mode,
                }
            )

        return JSONResponse({"error": f"unknown operation {operation!r}"}, status_code=400)

    except Exception as error:  # the runtime reports failure; it never invents a result
        return JSONResponse(
            {"error": f"{type(error).__name__}: {error}", "trace": trace.events},
            status_code=500,
        )


app = Starlette(
    routes=[
        Route("/ping", ping),
        Route("/invocations", invocations, methods=["POST"]),
    ]
)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))


if __name__ == "__main__":
    main()
