"""Create or update the Bedrock AgentCore runtime from a published ARM64 image.

    python deployment/deploy_agentcore.py \
        --image <account>.dkr.ecr.eu-west-1.amazonaws.com/countersign:latest \
        --role  arn:aws:iam::<account>:role/CountersignAgentCoreRuntime

The role wants the two policies beside this file: ``runtime-trust-policy.json``
so AgentCore may assume it, and ``runtime-permissions-policy.json`` for model
invocation and telemetry. Neither grants any data access, which is the point.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from typing import Any

import boto3

TERMINAL = {"CREATE_FAILED", "UPDATE_FAILED", "DELETING"}


def environment(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise argparse.ArgumentTypeError("environment values must use NAME=VALUE")
        key, item = value.split("=", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise argparse.ArgumentTypeError(f"invalid environment variable name: {key}")
        result[key] = item
    return result


def wait_until_ready(client: Any, runtime_id: str, timeout: int = 900) -> dict[str, Any]:
    """Wait for a create or update, and surface the failure reason rather than a timeout."""
    deadline = time.monotonic() + timeout
    while True:
        runtime = client.get_agent_runtime(agentRuntimeId=runtime_id)
        status = runtime.get("status", "UNKNOWN")
        if status == "READY":
            return runtime
        if status in TERMINAL:
            raise RuntimeError(
                f"runtime entered {status}: {runtime.get('failureReason') or 'no reason given'}"
            )
        if time.monotonic() >= deadline:
            raise TimeoutError(f"runtime was still {status} after {timeout}s")
        time.sleep(5)


def find_existing(client: Any, name: str) -> str | None:
    paginator = client.get_paginator("list_agent_runtimes")
    for page in paginator.paginate():
        for runtime in page.get("agentRuntimes", []):
            if runtime.get("agentRuntimeName") == name:
                return runtime["agentRuntimeId"]
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True, help="ECR image URI, ARM64")
    parser.add_argument("--role", required=True, help="execution role ARN")
    parser.add_argument("--name", default="countersign")
    parser.add_argument("--region", default="eu-west-1")
    parser.add_argument("--env", nargs="*", default=[], help="NAME=VALUE, repeatable")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    variables = {
        "COUNTERSIGN_MODEL_MODE": "bedrock",
        "COUNTERSIGN_BEDROCK_REGION": args.region,
        **environment(args.env),
    }

    request: dict[str, Any] = {
        "agentRuntimeName": args.name,
        "agentRuntimeArtifact": {"containerConfiguration": {"containerUri": args.image}},
        "networkConfiguration": {"networkMode": "PUBLIC"},
        "roleArn": args.role,
        "environmentVariables": variables,
        "description": (
            "Countersign: proposes risk domains and controls, and writes control reports. "
            "Outcomes are decided by deterministic tests in the application, not here."
        ),
    }

    if args.dry_run:
        print(json.dumps(request, indent=2))
        return 0

    client = boto3.client("bedrock-agentcore-control", region_name=args.region)
    existing = find_existing(client, args.name)

    if existing:
        print(f"updating runtime {existing}")
        client.update_agent_runtime(agentRuntimeId=existing, **request)
        runtime_id = existing
    else:
        print("creating runtime")
        runtime_id = client.create_agent_runtime(**request)["agentRuntimeId"]

    runtime = wait_until_ready(client, runtime_id)
    print(json.dumps({
        "agentRuntimeId": runtime_id,
        "agentRuntimeArn": runtime.get("agentRuntimeArn"),
        "status": runtime.get("status"),
        "version": runtime.get("agentRuntimeVersion"),
    }, indent=2))
    print(
        "\nSet COUNTERSIGN_AGENTCORE_ARN to the ARN above and "
        "COUNTERSIGN_MODEL_MODE=agentcore on the console."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
