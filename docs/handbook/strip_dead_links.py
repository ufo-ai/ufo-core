"""Unlink handbook references to stage pages that were not emitted.

Empty stages are dropped by the pipeline, so index/register/stage pages can carry
Markdown links to `stage-*.md` files that do not exist on disk. Deleting whole
entries would orphan their prose, so instead each dangling `[label](stage-X.md)`
becomes plain `label` — the same "no dead link" convention the generator applies
to unwritten stages internally. Applied across every page, not just index.md.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_LINK = re.compile(r"\[([^\]]+)\]\((stage-[0-9.]+\.md)\)")


def unlink_dangling(text: str, existing: frozenset[str]) -> str:
    """Replace links to stage pages absent from `existing` with their label text;
    links to existing pages are left untouched."""

    def replace(match: re.Match[str]) -> str:
        return match.group(0) if match.group(2) in existing else match.group(1)

    return _LINK.sub(replace, text)


def strip_dir(handbook_dir: Path) -> None:
    existing = frozenset(page.name for page in handbook_dir.glob("stage-*.md"))
    for page in handbook_dir.glob("*.md"):
        original = page.read_text(encoding="utf-8")
        fixed = unlink_dangling(original, existing)
        if fixed != original:
            page.write_text(fixed, encoding="utf-8")


if __name__ == "__main__":
    strip_dir(Path(sys.argv[1]))
