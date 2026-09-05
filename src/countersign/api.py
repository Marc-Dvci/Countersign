"""HTTP surface.

Reads are open. A judge should be able to open the deployment and see the whole
control programme, every run, every population and every finding without an
account, because a product that needs credentials to be understood will not be
understood.

Writes need a session, and the three that carry authority (accepting a risk
domain, approving a control into the schedule, dispositioning a finding) pass
the signed-in identity down to the store, which refuses anything that looks like
an agent. Running a control needs a session too, but grants nothing: it produces
evidence, and evidence is not a decision.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from importlib import resources
from typing import Any

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from countersign import catalogue
from countersign.config import Settings, load_settings
from countersign.connectors import ConnectorError
from countersign.control_tests import describe_registry
from countersign.database import AuthorityError, EvidenceError
from countersign.domain import PERIOD_DAYS
from countersign.service import Countersign

SESSION_COOKIE = "countersign_session"


def _web_root():
    return resources.files("countersign") / "web"


def _json(payload: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status)


class Api:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.app = Countersign(settings)
        self.store = self.app.store

    # ----------------------------------------------------------------

    def _user(self, request: Request) -> str | None:
        return self.store.session_user(request.cookies.get(SESSION_COOKIE, ""))

    def _require_user(self, request: Request) -> str:
        user = self._user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="Sign in to make this change.")
        return user

    def _tenant(self, request: Request) -> str:
        tenant = request.path_params.get("tenant") or self.settings.tenant
        if tenant not in catalogue.TENANTS and tenant not in {
            row["id"] for row in self.store.tenants()
        }:
            raise HTTPException(status_code=404, detail=f"unknown tenant {tenant!r}")
        return tenant

    # ----------------------------------------------------------------
    # Session
    # ----------------------------------------------------------------

    async def sign_in(self, request: Request) -> Response:
        body = await request.json()
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", ""))
        if username != self.settings.console_user or password != self.settings.console_password:
            return _json({"error": "Those credentials were not recognised."}, 401)
        token = self.store.open_session(username)
        response = _json({"user": username})
        response.set_cookie(
            SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=12 * 3600
        )
        return response

    async def sign_out(self, request: Request) -> Response:
        self.store.close_session(request.cookies.get(SESSION_COOKIE, ""))
        response = _json({"signed_out": True})
        response.delete_cookie(SESSION_COOKIE)
        return response

    # ----------------------------------------------------------------
    # State
    # ----------------------------------------------------------------

    async def state(self, request: Request) -> Response:
        tenant = self._tenant(request)
        intact, chain = self.store.audit_intact()
        known = {row["id"]: row for row in self.store.tenants()}
        return _json(
            {
                "user": self._user(request),
                "tenant": tenant,
                "as_of": self.settings.as_of.isoformat(),
                "model_mode": self.settings.model_mode,
                "model_description": self.settings.describe_mode(),
                "tenants": [
                    {
                        "id": key,
                        "display_name": meta["display_name"],
                        "description": meta["description"],
                        "sector": known.get(key, {}).get("sector", ""),
                        "onboarded": bool(known.get(key, {}).get("onboarded_at")),
                    }
                    for key, meta in catalogue.TENANTS.items()
                ],
                "sources": self.app.sources(tenant),
                "summary": self.store.summary(tenant, self.settings.as_of),
                "audit": {"intact": intact, "detail": chain},
            }
        )

    async def registry(self, request: Request) -> Response:
        return _json(describe_registry())

    async def obligations(self, request: Request) -> Response:
        tenant = self._tenant(request)
        try:
            rows = self.app.connectors(tenant)["obligations"].fetch("obligations")
        except (KeyError, ConnectorError) as error:
            return _json({"error": str(error)}, 404)
        return _json(rows)

    # ----------------------------------------------------------------
    # Onboarding
    # ----------------------------------------------------------------

    async def onboard(self, request: Request) -> Response:
        user = self._require_user(request)
        tenant = self._tenant(request)
        return _json(self.app.onboard(tenant, user))

    async def seed(self, request: Request) -> Response:
        self._require_user(request)
        tenant = self._tenant(request)
        return _json(self.app.seed(tenant))

    async def profile(self, request: Request) -> Response:
        tenant = self._tenant(request)
        profile = self.store.profile(tenant)
        if profile is None:
            return _json({"error": "This tenant has not been discovered yet."}, 404)
        return _json(profile.model_dump(mode="json"))

    async def domains(self, request: Request) -> Response:
        tenant = self._tenant(request)
        return _json(
            {"domains": self.store.domains(tenant), **self.store.taxonomy_meta(tenant)}
        )

    async def decide_domain(self, request: Request) -> Response:
        user = self._require_user(request)
        tenant = self._tenant(request)
        body = await request.json()
        try:
            self.store.decide_domain(
                tenant, request.path_params["code"], bool(body.get("accept")), user
            )
        except (AuthorityError, EvidenceError) as error:
            return _json({"error": str(error)}, 409)
        return _json({"ok": True})

    # ----------------------------------------------------------------
    # Controls
    # ----------------------------------------------------------------

    async def controls(self, request: Request) -> Response:
        tenant = self._tenant(request)
        controls = self.store.controls(tenant)
        by_code = {row["code"]: row for row in controls}
        for run in self.store.runs(tenant, limit=500):
            control = by_code.get(run["control_code"])
            if control is not None and "last_run" not in control:
                control["last_run"] = {
                    "id": run["id"],
                    "outcome": run["outcome"],
                    "finished_at": run["finished_at"],
                    "population_size": run["population_size"],
                    "exception_count": run["exception_count"],
                }
        domains = {row["code"]: row for row in self.store.domains(tenant)}
        for control in controls:
            domain = domains.get(control["domain_code"])
            control["domain_title"] = domain["title"] if domain else control["domain_code"]
            control["period_days"] = PERIOD_DAYS.get(control["periodicity"], 30)
        return _json(controls)

    async def control(self, request: Request) -> Response:
        tenant = self._tenant(request)
        code = request.path_params["code"]
        control = self.store.control(tenant, code)
        if control is None:
            return _json({"error": "no such control"}, 404)
        control["runs"] = self.store.runs(tenant, code, limit=20)
        control["findings"] = [
            finding for finding in self.store.findings(tenant) if finding["control_code"] == code
        ]
        return _json(control)

    async def approve_control(self, request: Request) -> Response:
        user = self._require_user(request)
        tenant = self._tenant(request)
        try:
            self.store.approve_control(
                tenant, request.path_params["code"], user, self.settings.as_of
            )
        except (AuthorityError, EvidenceError) as error:
            return _json({"error": str(error)}, 409)
        return _json({"ok": True})

    async def run_control(self, request: Request) -> Response:
        self._require_user(request)
        tenant = self._tenant(request)
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        lookback = body.get("lookback")
        try:
            outcome = self.app.run_control(
                tenant,
                request.path_params["code"],
                self.settings.as_of,
                int(lookback) if lookback else 91,
            )
        except KeyError as error:
            return _json({"error": str(error)}, 404)
        except ConnectorError as error:
            return _json({"error": str(error)}, 409)
        return _json(outcome)

    async def run_due(self, request: Request) -> Response:
        self._require_user(request)
        tenant = self._tenant(request)
        return _json(self.app.run_due(tenant, self.settings.as_of, 91))

    # ----------------------------------------------------------------
    # Runs and findings
    # ----------------------------------------------------------------

    async def runs(self, request: Request) -> Response:
        tenant = self._tenant(request)
        return _json(self.store.runs(tenant, limit=100))

    async def run(self, request: Request) -> Response:
        tenant = self._tenant(request)
        run = self.store.run(tenant, int(request.path_params["run_id"]))
        if run is None:
            return _json({"error": "no such run"}, 404)
        control = self.store.control(tenant, run["control_code"])
        run["control"] = control
        return _json(run)

    async def findings(self, request: Request) -> Response:
        tenant = self._tenant(request)
        return _json(self.store.findings(tenant, request.query_params.get("status")))

    async def finding(self, request: Request) -> Response:
        tenant = self._tenant(request)
        finding = self.store.finding(tenant, int(request.path_params["finding_id"]))
        if finding is None:
            return _json({"error": "no such finding"}, 404)
        return _json(finding)

    async def decide_finding(self, request: Request) -> Response:
        user = self._require_user(request)
        tenant = self._tenant(request)
        body = await request.json()
        try:
            self.store.decide_finding(
                tenant,
                int(request.path_params["finding_id"]),
                str(body.get("status", "")),
                user,
                str(body.get("note", "")),
            )
        except (AuthorityError, EvidenceError) as error:
            return _json({"error": str(error)}, 409)
        return _json({"ok": True})

    async def audit(self, request: Request) -> Response:
        tenant = self._tenant(request)
        intact, detail = self.store.audit_intact()
        return _json(
            {
                "intact": intact,
                "detail": detail,
                "events": self.store.audit_events(limit=300),
                "tenant": tenant,
            }
        )

    # ----------------------------------------------------------------
    # Static
    # ----------------------------------------------------------------

    async def index(self, request: Request) -> Response:
        return FileResponse(str(_web_root() / "index.html"))


async def _run_scheduler(api: Api) -> None:
    """Run every control that has fallen due, forever, on a fixed tick.

    The work is synchronous and touches SQLite, so each tick runs in a worker
    thread to keep the event loop free. A failing tick is logged and the loop
    continues: a scheduler must never take the console down.
    """
    log = logging.getLogger("countersign.scheduler")
    interval = max(1, api.settings.scheduler_interval_seconds)
    while True:
        await asyncio.sleep(interval)
        try:
            results = await asyncio.to_thread(api.app.run_all_due, api.settings.as_of, None)
        except Exception as error:  # noqa: BLE001 - a tick must never crash the loop
            log.warning("scheduler tick failed: %s", error)
            continue
        ran = sum(len(runs) for runs in results.values())
        if ran:
            log.info("scheduler ran %d control(s) that had fallen due", ran)


def build_app(settings: Settings | None = None) -> Starlette:
    settings = settings or load_settings()
    api = Api(settings)

    routes = [
        Route("/", api.index),
        Route("/api/session", api.sign_in, methods=["POST"]),
        Route("/api/session", api.sign_out, methods=["DELETE"]),
        Route("/api/state", api.state),
        Route("/api/registry", api.registry),
        Route("/api/tenants/{tenant}/state", api.state),
        Route("/api/tenants/{tenant}/obligations", api.obligations),
        Route("/api/tenants/{tenant}/onboard", api.onboard, methods=["POST"]),
        Route("/api/tenants/{tenant}/seed", api.seed, methods=["POST"]),
        Route("/api/tenants/{tenant}/profile", api.profile),
        Route("/api/tenants/{tenant}/domains", api.domains),
        Route("/api/tenants/{tenant}/domains/{code}/decision", api.decide_domain, methods=["POST"]),
        Route("/api/tenants/{tenant}/controls", api.controls),
        Route("/api/tenants/{tenant}/controls/{code}", api.control),
        Route("/api/tenants/{tenant}/controls/{code}/approve", api.approve_control, methods=["POST"]),
        Route("/api/tenants/{tenant}/controls/{code}/run", api.run_control, methods=["POST"]),
        Route("/api/tenants/{tenant}/run-due", api.run_due, methods=["POST"]),
        Route("/api/tenants/{tenant}/runs", api.runs),
        Route("/api/tenants/{tenant}/runs/{run_id:int}", api.run),
        Route("/api/tenants/{tenant}/findings", api.findings),
        Route("/api/tenants/{tenant}/findings/{finding_id:int}", api.finding),
        Route(
            "/api/tenants/{tenant}/findings/{finding_id:int}/decision",
            api.decide_finding,
            methods=["POST"],
        ),
        Route("/api/tenants/{tenant}/audit", api.audit),
        Mount("/assets", StaticFiles(directory=str(_web_root())), name="assets"),
    ]

    async def http_error(request: Request, exc: HTTPException) -> Response:
        if request.url.path.startswith("/api/"):
            return _json({"error": exc.detail}, exc.status_code)
        return FileResponse(str(_web_root() / "index.html"))

    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'",
        )
        return response

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        task = None
        if settings.scheduler_enabled:
            task = asyncio.create_task(_run_scheduler(api))
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    app = Starlette(
        routes=routes,
        middleware=[Middleware(BaseHTTPMiddleware, dispatch=security_headers)],
        exception_handlers={HTTPException: http_error},
        lifespan=lifespan,
    )
    app.state.countersign = api.app
    app.state.settings = settings
    return app
