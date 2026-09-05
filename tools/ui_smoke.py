"""Drive the real console in a real browser and fail on anything broken.

A passing API test proves the JSON is right. It proves nothing about whether the
page renders it, whether a button is wired, or whether the thing a reviewer is
meant to click exists. This walks the product the way a person would, fails on
any console error or failed request, and writes a screenshot of every view.

    python tools/ui_smoke.py            # the manual tour
    python tools/ui_smoke.py --demo     # play the guided demonstration instead
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "docs" / "screenshots"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
if not PYTHON.exists():  # posix checkouts
    PYTHON = ROOT / ".venv" / "bin" / "python"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_server(port: int, database: Path, seed: bool = True) -> subprocess.Popen:
    """A server on its own throwaway database, seeded and ready."""
    environment = {
        "COUNTERSIGN_DATABASE_PATH": str(database),
        "COUNTERSIGN_SESSION_PATH": str(database.parent / "sessions"),
        "COUNTERSIGN_MODEL_MODE": "demo",
        "COUNTERSIGN_ALLOW_LIVE_CONNECTORS": "false",
        "PYTHONIOENCODING": "utf-8",
    }
    import os

    env = {**os.environ, **environment}

    if seed:
        for tenant in ("kestrel", "northwind", "brandt"):
            subprocess.run(
                [str(PYTHON), "-m", "countersign.cli", "seed", "--tenant", tenant],
                cwd=ROOT,
                env=env,
                check=True,
                capture_output=True,
            )

    # Uvicorn logs a line per request. Piping that to a handle nobody reads
    # fills the OS pipe buffer part-way through a long tour and the server
    # blocks forever on its own log write, which looks exactly like the product
    # hanging. Logs go to a file.
    log = ROOT / "data" / "ui-smoke-server.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    handle = log.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [str(PYTHON), "-m", "countersign.cli", "serve", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
    )
    for _ in range(80):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/state", timeout=1).status_code == 200:
                return process
        except Exception:
            time.sleep(0.25)
    process.kill()
    raise RuntimeError("the server did not come up")


class Watcher:
    """Collects everything the browser complained about."""

    def __init__(self) -> None:
        self.problems: list[str] = []
        self.allowed: list[str] = []

    def allow(self, fragment: str) -> None:
        """Permit one console error containing `fragment`.

        Used only where the tour provokes a refusal on purpose. Everything else
        the browser complains about still fails the run.
        """
        self.allowed.append(fragment)

    def _record(self, message: str) -> None:
        for fragment in self.allowed:
            if fragment in message:
                self.allowed.remove(fragment)
                return
        self.problems.append(message)

    def attach(self, page) -> None:
        page.on(
            "console",
            lambda message: self._record(f"console.{message.type}: {message.text}")
            if message.type == "error"
            else None,
        )
        page.on("pageerror", lambda error: self._record(f"pageerror: {error}"))
        page.on(
            "requestfailed",
            lambda request: self.problems.append(
                f"request failed: {request.url} ({request.failure})"
            ),
        )
        page.on(
            "response",
            lambda response: self.problems.append(f"HTTP {response.status}: {response.url}")
            if response.status >= 500
            else None,
        )


def shoot(page, name: str) -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)


def expect(page, selector: str, what: str) -> None:
    if page.locator(selector).count() == 0:
        raise AssertionError(f"{what}: nothing matched {selector!r}")


def clear_toasts(page) -> None:
    """Empty the toast region so the next assertion reads a fresh message."""
    page.evaluate("document.querySelector('#toast-region').replaceChildren()")


def go(page, base: str, fragment: str, selector: str, what: str) -> None:
    """Navigate by hash and wait for the view to actually render.

    A hash change is not a navigation, so `networkidle` returns before the view
    has fetched anything. Waiting on the thing the view is supposed to produce
    is the only reliable signal that it produced it.
    """
    view, _, param = fragment.lstrip("#").partition("/")
    page.evaluate("hash => { location.hash = hash; }", fragment)
    try:
        page.wait_for_selector(
            f'#content[data-view="{view}"][data-param="{param}"]', timeout=15000, state="attached"
        )
        page.wait_for_selector(selector, timeout=15000, state="attached")
    except Exception as error:
        diagnostics = page.evaluate(
            "() => ({ hash: location.hash, view: window.Countersign.state.view,"
            " param: window.Countersign.state.param, tenant: window.Countersign.state.tenant,"
            " renders: window.Countersign.state.renders.slice(-8) })"
        )
        raise AssertionError(
            f"{what}: {selector!r} never appeared at {fragment}. "
            f"State: {diagnostics}. Page said: {page.inner_text('#content')[:200]!r}"
        ) from error


def tour(page, base: str, watcher) -> None:
    """The path a reviewer walks on their first ten minutes with the product."""
    page.goto(base, wait_until="networkidle")
    expect(page, ".shellbar", "the shell bar renders")
    expect(page, ".tile", "the overview shows KPI tiles")
    expect(page, "#chain-pill.is-good", "the audit chain reports intact")
    shoot(page, "01-overview")

    # Sign in, because every gate below needs a person.
    page.click("#auth-button")
    page.wait_for_selector("#dialog[open]")
    page.click("#dialog-submit")
    page.wait_for_selector("#avatar:not([hidden])", timeout=5000)

    go(page, base, "#discovery", ".source-tile", "connected sources render")
    expect(page, ".callout", "the sector rationale renders")
    shoot(page, "02-discovery")

    go(page, base, "#domains", "table.table tbody tr", "risk domains render")
    shoot(page, "03-domains")

    go(page, base, "#controls", "table.table tbody tr", "the control programme renders")
    shoot(page, "04-controls")

    # The flagship control: open it and walk every tab.
    go(page, base, "#control/DORA-INC-01", ".object-fact", "the run header renders")
    body = page.inner_text("#content")
    for needed in ("4 hours", "24 hours"):
        if needed not in body:
            raise AssertionError(f"the report does not mention {needed!r}")
    if "not followed" not in body:
        raise AssertionError("the report does not show the injection as contained")
    shoot(page, "05-control-report")

    for tab in ("Population", "Findings", "Challenge", "Definition", "Trace"):
        page.click(f'.tab:text-is("{tab}")')
        page.wait_for_timeout(200)
        if page.locator("#content .empty").count() and tab in ("Population", "Findings"):
            raise AssertionError(f"the {tab} tab came back empty")
        shoot(page, f"06-tab-{tab.lower()}")

    go(page, base, "#findings", "table.table tbody tr", "the findings worklist renders")
    shoot(page, "07-findings")

    page.click("table.table tbody tr")
    page.wait_for_selector(".finding-grid", timeout=5000)
    expect(page, ".finding-part", "the four-part finding renders")
    shoot(page, "08-finding")

    # The gate: accepting a risk needs a stated reason, and a short one is refused.
    clear_toasts(page)
    watcher.allow("409")
    page.click('[data-action="finding-accept"]')
    page.wait_for_selector("#dialog[open]")
    page.fill("#dialog textarea", "no")
    page.click("#dialog-submit")
    page.wait_for_selector(".toast.is-bad", timeout=5000)
    refusal = page.inner_text(".toast.is-bad")
    if "stated reason" not in refusal:
        raise AssertionError(f"a bare risk acceptance was not refused: {refusal!r}")
    shoot(page, "09-gate-refuses")

    go(page, base, "#register", "table.table tbody tr", "the obligation register renders")
    shoot(page, "10-register")

    go(page, base, "#activity", ".timeline li", "the audit chain renders")
    shoot(page, "11-activity")

    # Running a control from the UI actually runs it.
    go(page, base, "#control/CHG-APP-01", '[data-action="run"]', "the run button is offered")
    clear_toasts(page)
    page.click('[data-action="run"]')
    page.wait_for_selector(".toast", timeout=20000)
    message = page.inner_text(".toast")
    if "CHG-APP-01" not in message:
        raise AssertionError(f"running the control produced no result: {message!r}")

    # The taxonomy differs by tenant, which is the discovery claim.
    go(page, base, "#domains", "table.table tbody tr", "kestrel domains render")
    kestrel = set(page.locator("table.table tbody tr td:first-child").all_inner_texts())
    page.select_option("#tenant-select", "brandt")
    page.wait_for_timeout(900)
    go(page, base, "#domains", "table.table tbody tr", "brandt domains render")
    brandt = set(page.locator("table.table tbody tr td:first-child").all_inner_texts())
    if not brandt or brandt == kestrel:
        raise AssertionError(
            f"the taxonomy did not change between tenants: {sorted(kestrel)} vs {sorted(brandt)}"
        )
    shoot(page, "12-other-tenant")
    print(f"  taxonomy differs by tenant: kestrel={sorted(kestrel)} brandt={sorted(brandt)}")


def poll_until(page, expression: str, timeout: float, what: str):
    """Poll a page expression until it is truthy.

    Playwright's wait_for_function evaluates a string, which the console's
    Content-Security-Policy refuses without 'unsafe-eval'. Loosening the policy
    so a test harness can watch the page would be the wrong trade, so the
    harness polls instead.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if page.evaluate(expression):
            return True
        page.wait_for_timeout(400)
    raise AssertionError(f"{what}: still false after {timeout:.0f}s")


def play_demo(page, base: str) -> None:
    """Play the guided demonstration and fail if any beat could not find its target."""
    page.goto(f"{base}?demo=1", wait_until="networkidle")
    poll_until(
        page,
        "() => !!(window.CountersignDemo && window.CountersignDemo.ready)",
        30,
        "the demonstration never became ready",
    )
    poll_until(
        page,
        "() => window.CountersignDemo.finished === true",
        420,
        "the demonstration never finished",
    )
    failures = page.evaluate("() => window.CountersignDemo.failures")
    if failures:
        raise AssertionError("demonstration beats failed: " + "; ".join(failures))
    count = page.evaluate("() => window.CountersignDemo.beats.length")
    print(f"  {count} beats played cleanly")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true", help="play the guided demonstration")
    parser.add_argument("--url", default=None, help="drive an existing deployment instead")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    process = None
    database = ROOT / "data" / "ui-smoke.db"
    if args.url:
        base = args.url.rstrip("/")
    else:
        for suffix in ("", "-wal", "-shm"):
            path = Path(str(database) + suffix)
            if path.exists():
                path.unlink()
        port = free_port()
        process = start_server(port, database)
        base = f"http://127.0.0.1:{port}"

    watcher = Watcher()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not args.headed)
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            watcher.attach(page)
            print(f"driving {base}")
            if args.demo:
                # The demonstration provokes the risk-acceptance refusal on
                # purpose, and the browser logs the 409 that carries it.
                watcher.allow("409")
                play_demo(page, base)
            else:
                tour(page, base, watcher)
            browser.close()
    finally:
        if process:
            process.terminate()

    if watcher.problems:
        print("\nthe browser complained:")
        for problem in watcher.problems:
            print(f"  {problem}")
        return 1

    print(f"\nUI smoke passed. Screenshots in {SHOTS.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
