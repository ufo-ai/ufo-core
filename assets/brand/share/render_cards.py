#!/usr/bin/env python3
"""Render the UFO share cards (1200x630) used as og:image.

Two cards: `og-home.jpg` for ufo.ai and `og-site.jpg` for a hosted site's frame.
Both are `card.html` over one of the brand illustrations in `art/`, with the
crop measured rather than eyeballed: the illustration is scaled to 1200 wide and
the window is chosen so its black silhouettes begin at `LAND_AT`, which keeps the
type on empty sky whatever the artwork does above that line.

The brand faces are not duplicated here — they are extracted at render time from
the subset woff2 already inlined in `infra/modules/edge/landing.html`.

Both renders are written into `control/src/assets`, beside the gateway's other
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
CARD_DIR = "control/src/assets"
W, H = 1200, 630

# stem -> (illustration, where its silhouettes may begin, headline, display size, claim)
CARDS = {
    "og-home": (
        "science-lab-three",
        470,
        "<span>Go </span><em>further.</em>",
        136,
        "An agent that works where<br>your team works",
    ),
    "og-site": (
        "factory-kids-two",
        430,
        "<em>Made</em> with UFO",
        120,
        "Built and hosted by an agent",
    ),
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


def silhouette_top(im, threshold: int = 64, share: float = 0.02) -> int:
    """First row where at least `share` of the pixels are silhouette-dark."""
    grey = im.convert("L")
    width, height = grey.size
    px = grey.load()
    step = max(1, width // 240)
    columns = width // step
    for y in range(height):
        dark = sum(1 for x in range(0, width, step) if px[x, y] < threshold)
        if dark / columns >= share:
            return y
    return height


def cut_background(art: pathlib.Path, land_at: int, out: pathlib.Path) -> int:
    from PIL import Image

    im = Image.open(art).convert("RGB").resize((W, W), Image.LANCZOS)
    top = min(max(silhouette_top(im) - land_at, 0), W - H)
    im.crop((0, top, W, top + H)).save(out, quality=95)
    return top


def render(work: pathlib.Path, out: pathlib.Path, repo: pathlib.Path) -> list[pathlib.Path]:
    from PIL import Image

    template = (HERE / "card.html").read_text()
    mark = re.sub(
        r'width="46" height="46"',
        "",
        (repo / "assets/brand/ufo-mark-on-dark.svg").read_text(),
        count=1,
    ).replace("fill: #FFFAEF", "fill: #0D1418")

    written = []
    for stem, (art_name, land_at, headline, display_size, claim) in CARDS.items():
        bg = work / f"bg-{stem}.jpg"
        top = cut_background(HERE / "art" / f"{art_name}.webp", land_at, bg)
        page = work / f"{stem}.html"
        page.write_text(
            template.replace("ART", bg.name)
            .replace("MARK", mark)
            .replace("DISPLAY_SIZE", str(display_size))
            .replace("HEADLINE", headline)
            .replace("CLAIM", claim)
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
        print(f"{target} ({target.stat().st_size} bytes, {art_name} cropped at y={top})")
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
        shutil.copy(HERE / "card.html", work / "card.html")
        written = render(work, out, repo)
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
