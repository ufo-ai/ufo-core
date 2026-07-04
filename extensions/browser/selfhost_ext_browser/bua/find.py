"""Accessibility-tree find/parse — the consumer side of the tree-line grammar page.py renders."""

from __future__ import annotations

import re

from selfhost_ext_browser.bua.wire import JsonDict

MAX_RESULTS = 20

TREE_LINE_RE = re.compile(r'^\s*- (?P<role>\w+)(?: "(?P<name>(?:[^"\\]|\\.)*)")?')

TREE_REF_RE = re.compile(r"\[ref=(?P<ref>(?:f\d+)*e\d+)\]")

TREE_COORD_RE = re.compile(r"\(x=(?P<x>-?\d+),y=(?P<y>-?\d+)\)")

REPLY_REF_RE = re.compile(r"(?:f\d+)*e\d+")

FIND_TREE_CHAR_LIMIT = 500_000

FIND_SYSTEM_PROMPT = (
    "You locate elements in a browser accessibility tree for a query.\n"
    'Tree lines look like: - role "name" [ref=e12] (x=34,y=56)\n'
    "Reply with one line per matching element, best match first, formatted as\n"
    "<ref> | <why it matches>\n"
    f"using only refs that appear in the tree, at most {MAX_RESULTS} lines. End with a line "
    "MORE when further elements match; reply NO_MATCHES when nothing does."
)


def tree_entries(tree: str) -> list[JsonDict]:
    entries: list[JsonDict] = []
    for line in tree.splitlines():
        match = TREE_LINE_RE.match(line)
        ref_match = TREE_REF_RE.search(line)
        if not match or not ref_match:
            continue
        coord_match = TREE_COORD_RE.search(line)
        entries.append(
            {
                "ref": ref_match.group("ref"),
                "role": match.group("role"),
                "name": match.group("name") or "",
                "coords": f"{coord_match.group('x')},{coord_match.group('y')}"
                if coord_match
                else "0,0",
                "line": line.lower(),
            }
        )
    return entries


def _match_payload(entry: JsonDict, reason: str) -> JsonDict:
    return {
        "ref": entry["ref"],
        "role": entry["role"],
        "name": entry["name"],
        "coords": entry["coords"],
        "reason": reason,
    }


def parse_tree_matches(tree: str, query: str) -> list[JsonDict]:
    terms = [term for term in re.findall(r"[a-z0-9]+", query.lower()) if len(term) > 1]
    matches: list[JsonDict] = []
    for entry in tree_entries(tree):
        if all(term in str(entry["line"]) for term in terms):
            matches.append(_match_payload(entry, ""))
        if len(matches) == MAX_RESULTS:
            break
    return matches


def resolve_find_reply(reply: str, tree: str) -> tuple[list[JsonDict], bool]:
    """Ground a find reply against the tree: only refs that exist are kept, and their
    role/name/coords come from the tree itself, so a misremembered ref cannot propagate."""
    by_ref = {str(entry["ref"]): entry for entry in tree_entries(tree)}
    matches: list[JsonDict] = []
    seen: set[str] = set()
    has_more = False
    for raw_line in reply.splitlines():
        line = raw_line.strip().strip("`").lstrip("-*• ").strip()
        if not line:
            continue
        if line.upper().startswith("NO_MATCHES"):
            break
        if line.upper().rstrip(".") == "MORE":
            has_more = True
            continue
        head, _, reason = line.partition("|")
        ref_match = REPLY_REF_RE.search(head)
        if ref_match is None:
            continue
        ref = ref_match.group()
        entry = by_ref.get(ref)
        if entry is None or ref in seen:
            continue
        seen.add(ref)
        matches.append(_match_payload(entry, reason.strip()))
        if len(matches) == MAX_RESULTS:
            break
    return matches, has_more


def format_matches(matches: list[JsonDict], has_more: bool = False) -> str:
    lines = []
    for m in matches:
        line = f'[{m["ref"]}] {m["role"]} "{m["name"]}" ({m["coords"]})'
        if m.get("reason"):
            line += f" — {m['reason']}"
        lines.append(line)
    if has_more:
        lines.append("(more matches exist — refine the query)")
    return "\n".join(lines)
