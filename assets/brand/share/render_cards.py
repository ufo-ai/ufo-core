#!/usr/bin/env python3
"""Render the UFO share cards (1200x630) used as og:image.

Two cards: `og-home.jpg` for ufo.ai and `og-site.jpg` for a hosted site's frame.
Both are `card.html` over a flat field, carrying the logo lockup in `art/` and
nothing else. The lockup's own 1184x555 canvas holds the reference padding, so
the card places that canvas at the full frame width and measures no margins of
its own; the hosted-site card adds one line of type above it.

The brand faces are not duplicated here — they are extracted at render time from
the subset woff2 already inlined in `infra/modules/edge/landing.html`.

Both renders are written into `servers/control/src/assets`, beside the gateway's other
compiled-in artwork: the gateway image builds from the `control` tree alone, so a
card held above it is outside that build context.

Usage:  python3 render_cards.py [--out DIR] [--repo DIR] [--check]
Requires: chromium (headless) and Pillow.
"""

from __future__ import annotations

import argparse
import base64
import filecmp
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
# assets/brand/share -> repo root, when the script sits where it is committed.
DEFAULT_REPO = HERE.parents[2] if len(HERE.parents) > 2 else HERE
CARD_DIR = "servers/control/src/assets"
W, H = 1200, 630

LOCKUP = "art/logo-ember-with-padding.svg"
# stem -> the one line set above the lockup, empty where the card carries none.
# `<em>` takes the italic; `card.html` holds the space that the style change needs.
CARDS = {
    "og-home": "",
    "og-site": "<em>Made</em> with",
}


def extract_faces(landing: str, into: pathlib.Path) -> None:
    """Write the page's subset woff2 faces next to the template."""
    into.mkdir(parents=True, exist_ok=True)
    found = 0
    for face in re.findall(r"@font-face\s*\{(.*?)\}", landing, re.S):
        family = re.search(r"font-family:\s*'([^']+)'", face)
        style = re.search(r"font-style:\s*(\w+)", face)
        weight = re.search(r"font-weight:\s*(\d+)", face)
        data = re.search(r"url\('data:font/woff2;base64,([A-Za-z0-9+/=]+)'\)", face)
        if not (family and data):
            continue
        name = (
            f"{family.group(1)}-{style.group(1) if style else 'normal'}"
            f"-{weight.group(1) if weight else '400'}.woff2"
        )
        (into / name).write_bytes(base64.b64decode(data.group(1)))
        found += 1
    if found < 3:
        raise SystemExit(f"expected 3 faces inlined in landing.html, found {found}")


def render(work: pathlib.Path, out: pathlib.Path) -> list[pathlib.Path]:
    from PIL import Image

    template = (HERE / "card.html").read_text()
    lockup = HERE / LOCKUP
    shutil.copy(lockup, work / lockup.name)

    written = []
    for stem, line in CARDS.items():
        page = work / f"{stem}.html"
        page.write_text(
            template.replace("LOCKUP_SRC", lockup.name).replace(
                "LINE", f'<div class="line">{line}</div>' if line else ""
            )
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
                f"--window-size={W},{H}",
                f"--screenshot={shot}",
                str(page),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        frame = Image.open(shot).convert("RGB")
        if frame.size != (W, H):
            raise SystemExit(f"{stem}: expected {W}x{H}, got {frame.size}")
        target = out / f"{stem}.jpg"
        frame.save(target, quality=88, optimize=True, progressive=True)
        print(f"{target} ({target.stat().st_size} bytes)")
        written.append(target)
    return written


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out")
    parser.add_argument("--repo", default=str(DEFAULT_REPO))
    parser.add_argument(
        "--check",
        action="store_true",
        help="render to a temporary directory and compare against the committed cards",
    )
    args = parser.parse_args()
    repo = pathlib.Path(args.repo).resolve()
    committed = repo / CARD_DIR
    if args.check:
        out = pathlib.Path(tempfile.mkdtemp(prefix="ufo-cards-check-"))
    else:
        out = pathlib.Path(args.out).resolve() if args.out else committed
    out.mkdir(parents=True, exist_ok=True)

    work = pathlib.Path(tempfile.mkdtemp(prefix="ufo-cards-"))
    try:
        extract_faces((repo / "infra/modules/edge/landing.html").read_text(), work / "fonts")
        written = render(work, out)
        if args.check:
            bad = [p.name for p in written if not filecmp.cmp(p, committed / p.name, shallow=False)]
            if bad:
                print("differs from the committed card: " + ", ".join(bad), file=sys.stderr)
                return 1
            print("both cards reproduce byte-identically")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
