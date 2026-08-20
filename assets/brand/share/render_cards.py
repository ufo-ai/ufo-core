#!/usr/bin/env python3
"""Render the UFO share cards (1200x630) used as og:image.

Everything the cards draw already lives in the repository: the artwork and the
subset brand faces are inlined in the marketing page, so this script extracts
them from `infra/modules/edge/landing.html` rather than keeping a second copy.
Chromium renders `card.html` at exactly 1200x630 and the frames are written as
progressive JPEG (~90 KB each), the format every link unfurler accepts.

They are written into `control/src/assets`, beside the gateway's other
compiled-in artwork: the gateway image builds from the `control` tree alone, so
a card held above it is outside that build context.

Usage:  python3 render_cards.py [--out DIR]
Requires: chromium (headless) and Pillow.
"""

from __future__ import annotations

import argparse
import base64
import pathlib
import re
import shutil
import subprocess
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
# assets/brand/share -> repo root, when the script sits where it is committed.
DEFAULT_REPO = HERE.parents[2] if len(HERE.parents) > 2 else HERE
CARD_DIR = "control/src/assets"

CARDS = {
    # file stem -> (body class, headline markup)
    "og-home": ("home", '<span class="lead">Go</span> <em>further.</em>'),
    "og-site": ("sites", "<em>Made</em> with UFO"),
}


def extract_assets(landing: str, work: pathlib.Path) -> None:
    """Pull the hero artwork and the three subset faces out of the page."""
    art = re.search(r'src="data:image/webp;base64,([A-Za-z0-9+/=]+)"', landing)
    if not art:
        raise SystemExit("hero artwork not found in landing.html")
    (work / "hero.webp").write_bytes(base64.b64decode(art.group(1)))

    from PIL import Image

    Image.open(work / "hero.webp").save(work / "hero.png")

    fonts = work / "fonts"
    fonts.mkdir(exist_ok=True)
    for face in re.findall(r"@font-face\s*\{(.*?)\}", landing, re.S):
        family = re.search(r"font-family:\s*'([^']+)'", face)
        style = re.search(r"font-style:\s*(\w+)", face)
        weight = re.search(r"font-weight:\s*(\d+)", face)
        data = re.search(r"url\('data:font/woff2;base64,([A-Za-z0-9+/=]+)'\)", face)
        if not (family and data):
            continue
        face_style = style.group(1) if style else "normal"
        face_weight = weight.group(1) if weight else "400"
        name = f"{family.group(1)}-{face_style}-{face_weight}.woff2"
        (fonts / name).write_bytes(base64.b64decode(data.group(1)))


def render(work: pathlib.Path, out: pathlib.Path, repo: pathlib.Path) -> list[pathlib.Path]:
    from PIL import Image

    template = (HERE / "card.html").read_text()
    mark_svg = (repo / "assets/brand/ufo-mark-on-dark.svg").read_text()
    mark = re.sub(r'width="46" height="46"', "", mark_svg, count=1)
    written = []
    for stem, (body_class, headline) in CARDS.items():
        page = work / f"{stem}.html"
        page.write_text(
            template.replace("VARIANT", body_class)
            .replace("MARK", mark)
            .replace("HEADLINE", headline)
        )
        shot = work / f"{stem}.png"
        subprocess.run(
            [
                "chromium",
                "--headless",
                "--disable-gpu",
                "--no-sandbox",
                "--hide-scrollbars",
                "--force-device-scale-factor=1",
                "--window-size=1200,630",
                f"--screenshot={shot}",
                str(page),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        frame = Image.open(shot).convert("RGB")
        if frame.size != (1200, 630):
            raise SystemExit(f"{stem}: expected 1200x630, got {frame.size}")
        target = out / f"{stem}.jpg"
        frame.save(target, quality=88, optimize=True, progressive=True)
        written.append(target)
    return written


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out")
    parser.add_argument("--repo", default=str(DEFAULT_REPO))
    args = parser.parse_args()
    repo = pathlib.Path(args.repo).resolve()
    out = pathlib.Path(args.out).resolve() if args.out else repo / CARD_DIR
    out.mkdir(parents=True, exist_ok=True)

    work = pathlib.Path(tempfile.mkdtemp(prefix="ufo-share-cards-"))
    try:
        extract_assets((repo / "infra/modules/edge/landing.html").read_text(), work)
        shutil.copy(HERE / "card.html", work / "card.html")
        for path in render(work, out, repo):
            print(f"{path} ({path.stat().st_size} bytes)")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
