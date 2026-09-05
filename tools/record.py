"""Record the demonstration against a live console and mux the narration onto it.

The recording is of the real product. A server is started on a freshly seeded
throwaway database, Chromium plays the guided demonstration at 1920x1080, and
the per-beat timings measured by ``narrate.py`` are injected first so the beats
land on the words.

    python tools/narrate.py      # first: measure the speech
    python tools/record.py       # then: record to it

Pass a URL to record a deployment instead of a local server:

    python tools/record.py --url https://countersign.example.com

A take where any beat failed is raised rather than written.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from ui_smoke import free_port, poll_until, start_server

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "demo_video"
RAW = HERE / "_raw"
OUTPUT = HERE / "countersign-demo.mp4"


def record(base: str, seconds: list[float], headed: bool) -> Path:
    from playwright.sync_api import sync_playwright

    if RAW.exists():
        shutil.rmtree(RAW)
    RAW.mkdir(parents=True)

    budget = sum(seconds) + 40
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=not headed, args=["--force-device-scale-factor=1", "--hide-scrollbars"]
        )
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            record_video_dir=str(RAW),
            record_video_size={"width": 1920, "height": 1080},
        )
        page = context.new_page()
        problems: list[str] = []
        page.on("pageerror", lambda error: problems.append(f"pageerror: {error}"))
        page.on(
            "response",
            lambda response: problems.append(f"HTTP {response.status} {response.url}")
            if response.status >= 500
            else None,
        )
        page.add_init_script(f"window.__COUNTERSIGN_TIMING = {json.dumps(seconds)};")

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
            budget,
            "the demonstration never finished",
        )
        failures = page.evaluate("() => window.CountersignDemo.failures")
        page.wait_for_timeout(1200)
        video = page.video.path()
        context.close()
        browser.close()

    if failures:
        raise RuntimeError("beats failed, refusing to ship this take: " + "; ".join(failures))
    if problems:
        raise RuntimeError("the browser complained: " + "; ".join(problems))
    return Path(video)


def encode(raw: Path, narration: Path, total: float) -> None:
    fade = 0.6
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(raw),
            "-i", str(narration),
            "-filter_complex",
            f"[0:v]fps=30,scale=1920:1080:flags=lanczos,"
            f"fade=t=in:st=0:d={fade},fade=t=out:st={max(total - fade, 0):.2f}:d={fade}[v];"
            f"[1:a]afade=t=out:st={max(total - fade, 0):.2f}:d={fade}[a]",
            "-map", "[v]", "-map", "[a]",
            "-t", f"{total:.2f}",
            "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
            str(OUTPUT),
        ],
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=None, help="record a deployment instead of a local server")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    timing_path = HERE / "timing.json"
    narration = HERE / "narration.wav"
    if not timing_path.exists() or not narration.exists():
        raise SystemExit("run tools/narrate.py first")
    timing = json.loads(timing_path.read_text(encoding="utf-8"))
    seconds = timing["seconds"]

    server = None
    if args.url:
        base = args.url.rstrip("/")
    else:
        database = ROOT / "data" / "record.db"
        for suffix in ("", "-wal", "-shm"):
            path = Path(str(database) + suffix)
            if path.exists():
                path.unlink()
        port = free_port()
        server = start_server(port, database)
        base = f"http://127.0.0.1:{port}"

    try:
        print(f"recording {base}")
        raw = record(base, seconds, args.headed)
    finally:
        if server:
            server.terminate()

    total = timing["total"] + 2.0
    print(f"encoding {raw.name} at {total:.1f}s")
    encode(raw, narration, total)
    size = OUTPUT.stat().st_size / 1_000_000
    print(f"\n{OUTPUT.relative_to(ROOT)}  {total:.0f}s  {size:.1f} MB")
    print(f"subtitles: {(HERE / 'countersign-demo.srt').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
