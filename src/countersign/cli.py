"""Command line.

    countersign seed            onboard, accept, approve and run one worked example
    countersign serve           run the console
    countersign onboard         discovery, taxonomy and control design only
    countersign run CODE        execute one control
    countersign due             execute everything that has fallen due
    countersign score           mark the seeded run against the answer key
    countersign verify          recompute the audit chain
"""

from __future__ import annotations

import argparse
import json
import sys

import uvicorn

from countersign.config import load_settings
from countersign.service import Countersign


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="countersign", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("seed", "onboard", "due", "verify"):
        item = sub.add_parser(name)
        item.add_argument("--tenant", default=None)

    serve = sub.add_parser("serve")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--reload", action="store_true")

    run = sub.add_parser("run")
    run.add_argument("code")
    run.add_argument("--tenant", default=None)
    run.add_argument("--lookback", type=int, default=91)

    score = sub.add_parser("score")
    score.add_argument("--tenant", default=None)

    args = parser.parse_args(argv)
    settings = load_settings()
    tenant = getattr(args, "tenant", None) or settings.tenant

    if args.command == "serve":
        host = args.host or settings.host
        port = args.port or settings.port
        print(f"Countersign on http://{host}:{port}  ({settings.describe_mode()})")
        uvicorn.run(
            "countersign.api:build_app",
            host=host,
            port=port,
            factory=True,
            reload=args.reload,
            log_level="info",
        )
        return 0

    app = Countersign(settings)

    if args.command == "seed":
        print(json.dumps(app.seed(tenant), indent=2, default=str))
        return 0

    if args.command == "onboard":
        print(json.dumps(app.onboard(tenant, "cli"), indent=2, default=str))
        return 0

    if args.command == "run":
        print(json.dumps(app.run_control(tenant, args.code, lookback=args.lookback), indent=2))
        return 0

    if args.command == "due":
        print(json.dumps(app.run_due(tenant, lookback=91), indent=2))
        return 0

    if args.command == "verify":
        intact, detail = app.store.audit_intact()
        print(f"{'intact' if intact else 'BROKEN'}: {detail}")
        return 0 if intact else 1

    if args.command == "score":
        from countersign.scoring import render, score_tenant

        report = score_tenant(app.store, tenant)
        print(render(report))
        return 0 if report["passed"] else 1

    return 1


if __name__ == "__main__":
    sys.exit(main())
