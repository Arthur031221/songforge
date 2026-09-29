"""Capture studio screenshots and GIF frames with a headless Chromium.

Needs a running studio and Playwright (not a songforge dependency):

    uv run --with playwright==1.55.0 python scripts/capture_ui.py shots
    uv run --with playwright==1.55.0 python scripts/capture_ui.py gif

Set CHROMIUM to a Chromium or Chrome for Testing binary if Playwright has not
downloaded its own.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"
URL = os.environ.get("SONGFORGE_URL", "http://127.0.0.1:7860")
SIZE = {"width": 1440, "height": 1080}

STYLE = "indie pop, warm female vocal, jangly electric guitar, summer night, 112 BPM"
LYRICS = """[Verse]
Porch lights blinking on the avenue
Every radio is playing something new

[Chorus]
Stay, stay, the summer is ours
Counting the planes between the stars"""


def browser(p):
    exe = os.environ.get("CHROMIUM")
    return p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()


def shots() -> None:
    DEMO.mkdir(exist_ok=True)
    with sync_playwright() as p:
        b = browser(p)
        page = b.new_page(viewport=SIZE, device_scale_factor=2)
        page.goto(URL + "/#create")
        page.wait_for_selector(".card, .empty")
        page.fill("#style", STYLE)
        page.fill("#lyrics", LYRICS)
        page.wait_for_timeout(600)
        page.screenshot(path=str(DEMO / "create.png"))

        page.click("nav button[data-tab=library]")
        page.wait_for_timeout(800)
        first = page.query_selector("#library-feed .card [data-act=play]")
        if first:
            first.click()
            page.wait_for_timeout(4000)
        page.screenshot(path=str(DEMO / "library.png"))

        score = page.query_selector("#library-feed .card [data-act=score]")
        if score:
            score.click()
            page.wait_for_timeout(1200)
            page.screenshot(path=str(DEMO / "score.png"))
            page.click("#modal-close")

        page.click("nav button[data-tab=cover]")
        page.wait_for_timeout(500)
        source = os.environ.get("COVER_SOURCE")
        if source:
            page.set_input_files("#file", source)
            page.wait_for_selector("#source:not(.hidden)")
            use = page.query_selector("#cover-feed .card [data-act=usescore]")
            if use:
                use.click()
                page.wait_for_timeout(1500)
            page.fill("#cv-style", "heavy metal, distorted guitars, double-kick drums, 160 BPM")
            page.fill("#cv-lyrics", "[Verse]\nDashing through the snow\nIn a one-horse open sleigh")
        page.wait_for_timeout(800)
        page.screenshot(path=str(DEMO / "cover.png"))
        b.close()
    print("wrote", ", ".join(p.name for p in sorted(DEMO.glob("*.png"))))


def gif() -> None:
    """Record frames of one pass through the studio, then encode a GIF with ffmpeg."""
    frames = DEMO / "raw" / "frames"
    shutil.rmtree(frames, ignore_errors=True)
    frames.mkdir(parents=True)
    count = [0]

    def snap(page, hold=1):
        for _ in range(hold):
            page.screenshot(path=str(frames / f"{count[0]:04d}.png"))
            count[0] += 1

    with sync_playwright() as p:
        b = browser(p)
        page = b.new_page(viewport={"width": 1280, "height": 800}, device_scale_factor=1)
        page.goto(URL + "/#create")
        page.wait_for_selector(".card, .empty")
        snap(page, 3)
        page.click("#style")
        for i in range(0, len(STYLE), 6):
            page.fill("#style", STYLE[: i + 6])
            snap(page)
        page.click("#tag-chips .chip:nth-child(2)")
        snap(page)
        page.type("#lyrics", "Porch lights blinking on the avenue\n", delay=0)
        snap(page)
        page.click("#tag-chips .chip:nth-child(4)")
        page.type("#lyrics", "Stay, stay, the summer is ours\n", delay=0)
        snap(page, 2)
        page.click("#mode-seg button[data-mode=hq]")
        snap(page)
        page.click("#mode-seg button[data-mode=fast]")
        snap(page)
        # Watch the job that is already rendering instead of adding one to the queue.
        for _ in range(6):
            page.wait_for_timeout(2500)
            snap(page)
        page.click("nav button[data-tab=library]")
        page.wait_for_timeout(700)
        snap(page, 2)
        play = page.query_selector("#library-feed .card [data-act=play]")
        if play:
            play.click()
            for _ in range(8):
                page.wait_for_timeout(700)
                snap(page)
        score = page.query_selector("#library-feed .card [data-act=score]")
        if score:
            score.click()
            page.wait_for_timeout(1200)
            snap(page, 4)
            page.click("#modal-close")
        page.click("nav button[data-tab=cover]")
        page.wait_for_timeout(700)
        snap(page, 4)
        b.close()
    palette = frames.parent / "palette.png"
    out = DEMO / "demo.gif"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-framerate",
            "2",
            "-i",
            str(frames / "%04d.png"),
            "-vf",
            "scale=960:-1:flags=lanczos,palettegen=max_colors=128",
            str(palette),
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-framerate",
            "2",
            "-i",
            str(frames / "%04d.png"),
            "-i",
            str(palette),
            "-lavfi",
            "scale=960:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=4",
            str(out),
        ],
        check=True,
    )
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB, {count[0]} frames)")


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "shots"
    start = time.time()
    shots() if what == "shots" else gif()
    print(f"done in {time.time() - start:.0f} s")
