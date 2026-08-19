"""Markdown files rendered as source pages: the path filter, the utf-8 gate, and the
frontmatter/heading title chain both gbrain backends share."""

from pathlib import PurePosixPath

import yaml

from ufo.sdk.sources import Page, StreamFault

MARKDOWN_SUFFIXES = frozenset({".md", ".markdown"})
FRONTMATTER_DELIMITER = "---"
FRONTMATTER_TERMINATORS = (FRONTMATTER_DELIMITER, "...")
PAGE_STREAM = "pages"


def is_markdown_path(relpath: str) -> bool:
    """Whether a root-relative posix path names a markdown file outside hidden directories — the
    one filter that keeps `.git` objects, dotfiles, and binaries out of a sync."""
    parts = relpath.split("/")
    if any(part.startswith(".") for part in parts):
        return False
    return PurePosixPath(relpath).suffix.lower() in MARKDOWN_SUFFIXES


def decoded(source_ref: str, data: bytes) -> str:
    """The file's utf-8 text, or a `StreamFault` naming the file — one undecodable file fails the
    run with its path in the record rather than an anonymous `UnicodeDecodeError`."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise StreamFault(f"{source_ref} is not utf-8 text") from error


def markdown_page(source_ref: str, text: str) -> Page:
    """One markdown file as a page: title from frontmatter `title`, else the first `#` heading,
    else the path; the frontmatter block stays out of the indexed body."""
    title, body = _split_frontmatter(text)
    return Page(
        source_ref=source_ref,
        body=body,
        stream=PAGE_STREAM,
        title=title or _first_heading(body) or source_ref,
    )


def _split_frontmatter(text: str) -> tuple[str | None, str]:
    if not text.startswith(FRONTMATTER_DELIMITER + "\n"):
        return None, text
    lines = text.split("\n")
    for index in range(1, len(lines)):
        if lines[index].rstrip() not in FRONTMATTER_TERMINATORS:
            continue
        try:
            loaded = yaml.safe_load("\n".join(lines[1:index]))
        except yaml.YAMLError:
            return None, text
        body = "\n".join(lines[index + 1 :]).lstrip("\n")
        title = loaded.get("title") if isinstance(loaded, dict) else None
        return (title.strip() if isinstance(title, str) and title.strip() else None), body
    return None, text


def _first_heading(body: str) -> str | None:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip() or None
    return None
