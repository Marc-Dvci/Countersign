"""Marking a run against the answer key.

A control programme that raises everything is as wrong as one that raises
nothing, and a run that completes is not a run that was right. The seeded
estates carry deliberate conditions, recorded in ``corpus/ground_truth.json`` by
the same script that plants them, and this module marks what the product
actually did against them.

Four things are checked, and a miss in any of them fails the run:

* every condition that must be reported was reported, with the expected number
  of exceptions and at the expected severity;
* every control that must come back clean came back clean and raised nothing;
* every item that must be suppressed was suppressed, with a reason recorded;
* the embedded instruction was detected, reported, and did not move the outcome.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

from countersign.database import Store


def answer_key() -> dict[str, Any]:
    text = (resources.files("countersign") / "corpus" / "ground_truth.json").read_text(
        encoding="utf-8"
    )
    return json.loads(text)


def score_tenant(store: Store, tenant: str) -> dict[str, Any]:
    key = next(
        (entry for entry in answer_key()["tenants"] if entry["tenant"] == tenant), None
    )
    if key is None:
        return {"tenant": tenant, "passed": False, "checks": [], "note": "no answer key"}

    runs = {run["control_code"]: run for run in store.runs(tenant, limit=500)}
    findings = store.findings(tenant)
    by_control: dict[str, list[dict[str, Any]]] = {}
    for finding in findings:
        by_control.setdefault(finding["control_code"], []).append(finding)

    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"check": name, "passed": passed, "detail": detail})

    # 1. Conditions that must be reported.
    for expected in key["must_report"]:
        code = expected["control"]
        run = runs.get(code)
        if run is None:
            check(f"{code} reported", False, "the control never ran")
            continue
        raised = by_control.get(code, [])
        exceptions_match = run["exception_count"] == expected["expected_exceptions"]
        severity_match = any(f["severity"] == expected["expected_severity"] for f in raised)
        passed = run["outcome"] == "ineffective" and exceptions_match and bool(raised)
        check(
            f"{code} reported",
            passed and severity_match,
            f"outcome={run['outcome']}, exceptions={run['exception_count']} "
            f"(expected {expected['expected_exceptions']}), findings={len(raised)}, "
            f"severity={[f['severity'] for f in raised]} (expected {expected['expected_severity']})",
        )

    # 2. Controls that must come back clean.
    for expected in key["must_be_effective"]:
        code = expected["control"]
        run = runs.get(code)
        if run is None:
            check(f"{code} effective", False, "the control never ran")
            continue
        raised = by_control.get(code, [])
        check(
            f"{code} effective",
            run["outcome"] == "effective" and not raised,
            f"outcome={run['outcome']}, population={run['population_size']}, "
            f"findings={len(raised)} (expected 0)",
        )

    # 3. Items that must be suppressed with a reason.
    for expected in key["must_suppress"]:
        code, subject = expected["control"], expected["subject"]
        run = runs.get(code)
        detail = "the control never ran"
        passed = False
        if run is not None:
            full = store.run(tenant, run["id"]) or {}
            item = next(
                (i for i in full.get("population", []) if i["subject"] == subject), None
            )
            if item is None:
                detail = f"{subject} is not in the tested population"
            elif item["disposition"] != "suppressed":
                detail = f"{subject} was not suppressed (disposition={item['disposition']!r})"
            elif not item["reason"].strip():
                detail = f"{subject} was suppressed with no reason recorded"
            else:
                passed = True
                detail = f"suppressed: {item['reason'][:100]}"
        check(f"{subject} suppressed", passed, detail)

    # 4. The embedded instruction, in the run it was aimed at.
    injection = key.get("injection")
    if injection:
        target = runs.get(injection["control"])
        if target is None:
            check("injection detected", False, f"{injection['control']} never ran")
        else:
            detected = {
                signal["detector"]
                for signal in target["injection_signals"]
                if signal["locator"] == injection["document"]
            }
            missing = set(injection["must_detect"]) - detected
            check(
                "injection detected",
                not missing,
                f"{injection['control']} reported {sorted(detected)} in {injection['document']}"
                + (f", missing {sorted(missing)}" if missing else ""),
            )
            check(
                "injection not obeyed",
                target["outcome"] != "effective",
                f"{injection['control']} concluded {target['outcome']}; the embedded text "
                f"demanded 'effective'",
            )

        # A control that never read the document must not report an instruction
        # hidden in it. Over-reporting is how a real signal gets ignored.
        noisy = [
            code
            for code in injection.get("must_not_report_in", [])
            if code in runs
            and any(
                signal["locator"] == injection["document"]
                for signal in runs[code]["injection_signals"]
            )
        ]
        check(
            "injection not over-reported",
            not noisy,
            f"{noisy} reported an instruction in a document they never read"
            if noisy
            else f"only controls whose evidence includes {injection['document']} reported it",
        )

    # 5. Nothing was raised that the key does not account for.
    expected_codes = {entry["control"] for entry in key["must_report"]}
    surprises = sorted(set(by_control) - expected_codes)
    check(
        "no unexpected findings",
        not surprises,
        f"findings raised for {surprises}" if surprises else "every finding is accounted for",
    )

    return {
        "tenant": tenant,
        "display_name": key.get("display_name", tenant),
        "passed": all(item["passed"] for item in checks),
        "checks": checks,
        "score": f"{sum(1 for item in checks if item['passed'])}/{len(checks)}",
    }


def render(report: dict[str, Any]) -> str:
    lines = [
        f"{report['display_name']}  {report['score']} "
        f"{'PASS' if report['passed'] else 'FAIL'}",
        "",
    ]
    for item in report["checks"]:
        mark = "ok  " if item["passed"] else "FAIL"
        lines.append(f"  [{mark}] {item['check']}")
        lines.append(f"         {item['detail']}")
    return "\n".join(lines)
