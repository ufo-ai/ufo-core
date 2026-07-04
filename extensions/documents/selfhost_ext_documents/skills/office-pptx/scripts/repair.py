"""Repair known pptxgenjs OOXML issues in a PPTX file.

Fixes issues that cause PowerPoint to show "cannot read" or "repair" dialogs:
  - Phantom slideMaster entries in [Content_Types].xml (pptxgenjs #1444)
  - ZIP directory entries (violate Open Packaging Convention)

Fixes silent data corruption:
  - Missing xml:space="preserve" on text elements with leading/trailing whitespace

Usage:
    python scripts/repair.py presentation.pptx
"""

import re
import shutil
import sys
import zipfile
from pathlib import Path

from lxml import etree

PHANTOM_MASTER_RE = re.compile(
    rb'<Override\s+PartName="/ppt/slideMasters/slideMaster(\d+)\.xml"[^>]*/>'
)
_DML_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
_TEXT_TAG = f"{{{_DML_NS}}}t"

_TEXT_ENTRY_RE = re.compile(
    r"ppt/(slides/slide|slideLayouts/slideLayout|slideMasters/slideMaster|"
    r"notesSlides/notesSlide|notesMasters/notesMaster)\d*\.xml$"
)


def _repair_whitespace_preservation(
    entries: dict[str, bytes],
) -> tuple[dict[str, bytes], int]:
    """Add xml:space="preserve" to <a:t> text runs with leading/trailing whitespace.

    PowerPoint silently strips leading/trailing spaces from text elements that
    lack this attribute, corrupting content like indented code or aligned text.
    Returns updated entries dict and count of repaired elements.
    """
    fixes = 0
    updated: dict[str, bytes] = {}
    for name, data in entries.items():
        if not _TEXT_ENTRY_RE.match(name):
            continue
        try:
            root = etree.fromstring(data)
        except etree.XMLSyntaxError:
            continue
        modified = False
        for elem in root.iter(_TEXT_TAG):
            text = elem.text or ""
            if text and (text[0] in (" ", "\t") or text[-1] in (" ", "\t")):
                if elem.get(_XML_SPACE) != "preserve":
                    elem.set(_XML_SPACE, "preserve")
                    fixes += 1
                    modified = True
        if modified:
            updated[name] = etree.tostring(
                root, xml_declaration=True, encoding="UTF-8", standalone=True
            )
    return updated, fixes


def repair(filename):
    src = Path(filename)
    if not src.exists():
        print(f"Error: {src} not found", file=sys.stderr)
        return False

    actual_ids = set()
    has_dir_entries = False

    with zipfile.ZipFile(src, "r") as zf:
        all_entries = {
            item.filename: zf.read(item.filename)
            for item in zf.infolist()
            if not item.filename.endswith("/")
        }
        for name in zf.namelist():
            if name.endswith("/"):
                has_dir_entries = True
            m = re.match(r"ppt/slideMasters/slideMaster(\d+)\.xml$", name)
            if m:
                actual_ids.add(int(m.group(1)))

        ct_data = all_entries.get("[Content_Types].xml", b"")
        ct_fixed = PHANTOM_MASTER_RE.sub(
            lambda m: m.group(0) if int(m.group(1).decode()) in actual_ids else b"",
            ct_data,
        )
        ct_fixed = re.sub(rb"\n\s*\n", b"\n", ct_fixed)
        has_phantoms = ct_fixed != ct_data

        ws_updates, ws_fixes = _repair_whitespace_preservation(all_entries)

        if not has_dir_entries and not has_phantoms and not ws_fixes:
            print(f"No repairs needed for {src.name}")
            return True

        fixes = 0
        tmp = str(src) + ".tmp"
        with (
            zipfile.ZipFile(src, "r") as zin,
            zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout,
        ):
            for item in zin.infolist():
                if item.filename.endswith("/"):
                    fixes += 1
                    continue
                if item.filename == "[Content_Types].xml":
                    data = ct_fixed
                elif item.filename in ws_updates:
                    data = ws_updates[item.filename]
                else:
                    data = zin.read(item.filename)
                zout.writestr(item, data)

    if has_phantoms:
        fixes += len(PHANTOM_MASTER_RE.findall(ct_data)) - len(
            PHANTOM_MASTER_RE.findall(ct_fixed)
        )
    fixes += ws_fixes

    try:
        shutil.move(tmp, str(src))
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise
    print(f"Repaired {src.name}: {fixes} fixes applied")
    return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python repair.py <pptx_file>", file=sys.stderr)
        sys.exit(1)
    if not repair(sys.argv[1]):
        sys.exit(1)
