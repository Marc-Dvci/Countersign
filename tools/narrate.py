"""Synthesise the narration from the product's own captions.

The captions are read out of the running console rather than kept in a script
beside it, so the words spoken and the words burnt into the frame cannot drift
apart. Each beat is synthesised separately and its measured duration is written
to ``timing.json``, which ``record.py`` injects so the demonstration paces
itself to the voice instead of to a guess.

    python tools/narrate.py

Writes ``narration.wav``, ``timing.json`` and ``countersign-demo.srt``.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import wave
from pathlib import Path

from ui_smoke import free_port, start_server

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "demo_video"
SPEECH = HERE / "speech"

VOICE = "en-GB-RyanNeural"
RATE = "+6%"
CEILING_SECONDS = 290  # the hackathon allows five minutes
GAP = 0.45  # breath between beats


def captions(base: str) -> list[str]:
    """Read the beat captions out of the running product."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.add_init_script("window.__COUNTERSIGN_DEMO_MANUAL = true;")
        page.goto(f"{base}?demo=1", wait_until="networkidle")
        for _ in range(60):
            if page.evaluate("() => !!(window.CountersignDemo && window.CountersignDemo.ready)"):
                break
            page.wait_for_timeout(300)
        lines = page.evaluate("() => window.CountersignDemo.captions")
        browser.close()
    if not lines:
        raise RuntimeError("the demonstration exposed no captions")
    return lines


async def speak(text: str, path: Path) -> None:
    import edge_tts

    communicate = edge_tts.Communicate(text, VOICE, rate=RATE)
    await communicate.save(str(path))


def duration(path: Path) -> float:
    """Measured length of a synthesised clip, in seconds."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def timestamp(seconds: float) -> str:
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{int(hours):02}:{int(minutes):02}:{int(secs):02},{int((secs % 1) * 1000):03}"


def main() -> int:
    HERE.mkdir(parents=True, exist_ok=True)
    SPEECH.mkdir(parents=True, exist_ok=True)
    database = ROOT / "data" / "narrate.db"
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(database) + suffix)
        if path.exists():
            path.unlink()

    port = free_port()
    server = start_server(port, database)
    try:
        lines = captions(f"http://127.0.0.1:{port}")
    finally:
        server.terminate()

    print(f"{len(lines)} beats")

    clips: list[Path] = []
    seconds: list[float] = []
    for index, line in enumerate(lines):
        clip = SPEECH / f"{index:02}.mp3"
        asyncio.run(speak(line, clip))
        length = duration(clip)
        clips.append(clip)
        seconds.append(round(length + GAP, 3))
        print(f"  {index:02}  {length:5.1f}s  {line[:72]}")

    total = sum(seconds)
    print(f"\ntotal {total:.1f}s")
    if total > CEILING_SECONDS:
        raise SystemExit(
            f"narration is {total:.0f}s, over the {CEILING_SECONDS}s ceiling. Shorten a beat."
        )

    # One wav, with each clip padded to its slot so the audio and the injected
    # timing describe the same timeline.
    listing = HERE / "concat.txt"
    silence = SPEECH / "gap.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", str(GAP),
         "-c:a", "pcm_s16le", str(silence)],
        check=True, capture_output=True,
    )
    entries = []
    for clip in clips:
        wav = clip.with_suffix(".wav")
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(clip), "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)],
            check=True, capture_output=True,
        )
        entries.append(f"file '{wav.as_posix()}'")
        entries.append(f"file '{silence.as_posix()}'")
    listing.write_text("\n".join(entries) + "\n", encoding="utf-8")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
         "-c:a", "pcm_s16le", str(HERE / "narration.wav")],
        check=True, capture_output=True,
    )
    listing.unlink()

    (HERE / "timing.json").write_text(
        json.dumps({"seconds": seconds, "captions": lines, "total": round(total, 2)}, indent=2),
        encoding="utf-8",
    )

    cues = []
    cursor = 0.0
    for index, (line, length) in enumerate(zip(lines, seconds, strict=True), start=1):
        cues.append(
            f"{index}\n{timestamp(cursor)} --> {timestamp(cursor + length - 0.1)}\n{line}\n"
        )
        cursor += length
    (HERE / "countersign-demo.srt").write_text("\n".join(cues), encoding="utf-8")

    with wave.open(str(HERE / "narration.wav")) as handle:
        measured = handle.getnframes() / handle.getframerate()
    print(f"narration.wav  {measured:.1f}s")
    print("timing.json and countersign-demo.srt written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
