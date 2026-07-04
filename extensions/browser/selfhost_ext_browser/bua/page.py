"""Accessibility-tree snapshot and rendering: the model's structured view of a page.

The snapshot pipeline follows browser-use (browser_use/dom/service.py): one DOMSnapshot per target
joined on backendNodeId with an Accessibility.getFullAXTree per frame; Chrome's per-frame AX trees
stop at iframe nodes, so child documents splice in under their iframe's AX node. `render_page`
renders the playwright aria-snapshot line grammar (`- role "name" [ref=...]`) extended with
model-space center coordinates and state suffixes; `render_markdown` renders a reading view. Refs
follow playwright's ref grammar frame-prefixed, with the CDP backendNodeId as the element id, so a
ref stays valid until its document navigates."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlparse

from selfhost_ext_browser.bua.cdp import CdpError
from selfhost_ext_browser.bua.coordinate import Size, effective_model_size
from selfhost_ext_browser.bua.errors import HallucinationError
from selfhost_ext_browser.bua.wire import (
    Json,
    JsonDict,
    ValidationError,
    as_int,
    as_list,
    as_map,
    as_str,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_DEPTH = 15
MAX_NAME_LEN = 100
MAX_VALUE_LEN = 50

IGNORED_IFRAME_SCHEME = "chrome-extension://"

IGNORED_ROLES = {
    "GenericContainer",
    "InlineTextBox",
    "LayoutTable",
    "LayoutTableCell",
    "LayoutTableRow",
    "LineBreak",
    "ListMarker",
    "none",
    "presentation",
    "sectionheader",
}
SKIP_ROLES = {
    "Section",
    "article",
    "contentinfo",
    "generic",
    "group",
    "list",
    "listitem",
    "main",
    "navigation",
    "paragraph",
}
INTERACTIVE_ROLES = {
    "button",
    "checkbox",
    "combobox",
    "link",
    "listbox",
    "menuitem",
    "menuitemcheckbox",
    "menuitemradio",
    "option",
    "radio",
    "searchbox",
    "slider",
    "spinbutton",
    "switch",
    "tab",
    "textbox",
    "treeitem",
}
STATE_PROPERTIES = ("checked", "expanded", "selected", "disabled", "value")
EXTRA_PROPERTIES = (
    "busy",
    "editable",
    "focused",
    "invalid",
    "keyshortcuts",
    "roledescription",
    "live",
    "atomic",
    "relevant",
    "autocomplete",
    "hasPopup",
    "multiselectable",
    "readonly",
    "required",
    "valuemin",
    "valuemax",
    "valuetext",
    "modal",
    "pressed",
    "selected",
    "errormessage",
    "url",
    "placeholder",
    "contenteditable",
)

REF_RE = re.compile(r"^(?P<prefix>(?:f\d+)*)e(?P<backend>\d+)$")


def split_ref(ref: str) -> tuple[str, int] | None:
    match = REF_RE.match(ref)
    if not match:
        return None
    return match.group("prefix"), int(match.group("backend"))


class Cdp(Protocol):
    async def send(
        self, method: str, params: JsonDict | None = None, session_id: str | None = None
    ) -> JsonDict: ...


@dataclass
class NodeGeom:
    bounds: tuple[float, float, float, float] | None = None
    cursor_pointer: bool = False
    input_type: str | None = None
    img_src: str | None = None


@dataclass
class FrameSnapshot:
    frame_id: str
    session_id: str
    prefix: str
    origin: tuple[float, float]
    ax_by_id: dict[str, JsonDict]
    root_ax_id: str | None
    geom: dict[int, NodeGeom]
    children: dict[int, FrameSnapshot] = field(default_factory=dict)
    oop_iframes: list[int] = field(default_factory=list)


@dataclass
class FrameNode:
    frame_id: str
    session_id: str
    origin: tuple[float, float]


class PageTab(Protocol):
    session_id: str
    ref_frames: dict[str, FrameNode]

    def frame_seq(self, frame_id: str) -> int: ...


class BrowserPageSession(Protocol):
    model_size: Size
    oop_sessions: dict[str, str]

    def connection(self) -> Cdp: ...

    async def init_session(self, session_id: str) -> None: ...


@dataclass
class _RawDoc:
    frame_id: str
    backend_ids: list[int]
    geom: dict[int, NodeGeom]
    content_documents: dict[int, int]
    iframe_nodes: dict[int, str | None]


@dataclass
class DocData:
    frame_id: str
    geom: dict[int, NodeGeom]
    iframe_docs: dict[int, str]
    oop_iframes: list[int]


def _float(value: Json | None, default: float = 0.0) -> float:
    match value:
        case int() | float():
            return float(value)
        case _:
            return default


def _attr(strings: list[str], attrs: list[Json], name: str) -> str | None:
    for index in range(0, len(attrs) - 1, 2):
        key, value = attrs[index], attrs[index + 1]
        if isinstance(key, int) and isinstance(value, int) and strings[key] == name:
            return strings[value]
    return None


def _parse_document(doc: JsonDict, strings: list[str], dpr: float) -> _RawDoc:
    nodes = as_map(doc.get("nodes"), "nodes")
    layout = as_map(doc.get("layout"), "layout")
    frame_id = strings[as_int(doc.get("frameId"), "frameId")]
    scroll_x = _float(doc.get("scrollOffsetX")) / dpr
    scroll_y = _float(doc.get("scrollOffsetY")) / dpr
    backend_ids = [
        as_int(backend, "backendNodeId[]")
        for backend in as_list(nodes.get("backendNodeId"), "backendNodeId")
    ]
    node_names = as_list(nodes.get("nodeName"), "nodeName")
    attributes = as_list(nodes.get("attributes"), "attributes")
    rare = as_map(
        nodes.get("contentDocumentIndex") or {"index": [], "value": []}, "contentDocumentIndex"
    )
    content_documents = {
        as_int(node_idx, "contentDocumentIndex.index[]"): as_int(
            doc_idx, "contentDocumentIndex.value[]"
        )
        for node_idx, doc_idx in zip(
            as_list(rare.get("index"), "index"),
            as_list(rare.get("value"), "value"),
            strict=True,
        )
    }

    geom: dict[int, NodeGeom] = {}
    iframe_nodes: dict[int, str | None] = {}
    for node_idx, name_idx in enumerate(node_names):
        if not isinstance(name_idx, int):
            continue
        tag = strings[name_idx].upper()
        if tag not in {"INPUT", "IMG", "IFRAME", "FRAME"}:
            continue
        attrs = as_list(attributes[node_idx], "attributes[]") if node_idx < len(attributes) else []
        entry = geom.setdefault(backend_ids[node_idx], NodeGeom())
        match tag:
            case "INPUT":
                entry.input_type = _attr(strings, attrs, "type")
            case "IMG":
                entry.img_src = _attr(strings, attrs, "src")
            case "IFRAME" | "FRAME":
                iframe_nodes[node_idx] = _attr(strings, attrs, "src")

    bounds_rows = as_list(layout.get("bounds"), "bounds")
    style_rows = as_list(layout.get("styles"), "styles")
    for layout_idx, node_idx_raw in enumerate(as_list(layout.get("nodeIndex"), "nodeIndex")):
        node_idx = as_int(node_idx_raw, "nodeIndex[]")
        entry = geom.setdefault(backend_ids[node_idx], NodeGeom())
        row = as_list(bounds_rows[layout_idx], "bounds[]")
        if entry.bounds is None and len(row) == 4:
            entry.bounds = (
                _float(row[0]) / dpr - scroll_x,
                _float(row[1]) / dpr - scroll_y,
                _float(row[2]) / dpr,
                _float(row[3]) / dpr,
            )
        styles = as_list(style_rows[layout_idx], "styles[]")
        if styles and isinstance(styles[0], int) and strings[styles[0]] == "pointer":
            entry.cursor_pointer = True
    return _RawDoc(frame_id, backend_ids, geom, content_documents, iframe_nodes)


def parse_snapshot(
    snapshot: JsonDict, dpr: float, base_origin: tuple[float, float] = (0.0, 0.0)
) -> list[DocData]:
    """Document geometry after browser-use (browser_use/dom/service.py): bounds normalize to CSS
    pixels via dpr minus the owning document's scroll, and each child document's origin accumulates
    its iframe node's viewport position through the contentDocumentIndex chain."""
    strings = [as_str(value, "strings[]") for value in as_list(snapshot.get("strings"), "strings")]
    documents = as_list(snapshot.get("documents"), "documents")
    if not documents:
        raise RuntimeError("DOMSnapshot returned no documents")
    raws = [_parse_document(as_map(doc, "documents[]"), strings, dpr) for doc in documents]

    origins: dict[int, tuple[float, float]] = {0: base_origin}
    queue = [0]
    while queue:
        parent_idx = queue.pop(0)
        parent = raws[parent_idx]
        parent_x, parent_y = origins[parent_idx]
        for node_idx, child_idx in parent.content_documents.items():
            if child_idx in origins or child_idx >= len(raws):
                continue
            entry = parent.geom.get(parent.backend_ids[node_idx])
            bounds = entry.bounds if entry else None
            origins[child_idx] = (
                (parent_x + bounds[0], parent_y + bounds[1]) if bounds else (parent_x, parent_y)
            )
            queue.append(child_idx)

    docs: list[DocData] = []
    for doc_idx, raw in enumerate(raws):
        origin = origins.get(doc_idx)
        if origin is None:
            continue
        for entry in raw.geom.values():
            if entry.bounds is not None:
                bx, by, bw, bh = entry.bounds
                entry.bounds = (bx + origin[0], by + origin[1], bw, bh)
        iframe_docs: dict[int, str] = {}
        oop_iframes: list[int] = []
        for node_idx, src in raw.iframe_nodes.items():
            if src is not None and src.startswith(IGNORED_IFRAME_SCHEME):
                continue
            backend = raw.backend_ids[node_idx]
            child_doc_idx = raw.content_documents.get(node_idx)
            if child_doc_idx is not None and child_doc_idx < len(raws):
                iframe_docs[backend] = raws[child_doc_idx].frame_id
            else:
                oop_iframes.append(backend)
        docs.append(DocData(raw.frame_id, raw.geom, iframe_docs, oop_iframes))
    return docs


async def fetch_target(
    cdp: Cdp,
    session_id: str,
    *,
    root_prefix: str,
    prefix_for: Callable[[str], str],
    base_origin: tuple[float, float] = (0.0, 0.0),
) -> FrameSnapshot:
    await cdp.send("Accessibility.enable", session_id=session_id)
    await cdp.send("DOMSnapshot.enable", session_id=session_id)
    snapshot = await cdp.send(
        "DOMSnapshot.captureSnapshot",
        {"computedStyles": ["cursor"], "includeDOMRects": True},
        session_id=session_id,
    )
    metrics = await cdp.send(
        "Runtime.evaluate",
        {"expression": "window.devicePixelRatio", "returnByValue": True},
        session_id=session_id,
    )
    dpr = _float(as_map(metrics.get("result"), "result").get("value"), 1.0) or 1.0
    docs = parse_snapshot(snapshot, dpr, base_origin)

    ax_results = await asyncio.gather(
        *(
            cdp.send(
                "Accessibility.getFullAXTree", {"frameId": doc.frame_id}, session_id=session_id
            )
            for doc in docs
        ),
        return_exceptions=True,
    )
    frames: dict[str, FrameSnapshot] = {}
    for doc, result in zip(docs, ax_results, strict=True):
        if isinstance(result, BaseException):
            if doc is docs[0]:
                raise result
            logger.warning(
                "browser.frame_ax_failed",
                extra={"frame_id": doc.frame_id, "error": str(result)},
            )
            continue
        ax_nodes = [as_map(node, "nodes[]") for node in as_list(result.get("nodes"), "nodes")]
        ax_by_id = {as_str(node.get("nodeId"), "nodeId"): node for node in ax_nodes}
        frames[doc.frame_id] = FrameSnapshot(
            frame_id=doc.frame_id,
            session_id=session_id,
            prefix=root_prefix if doc is docs[0] else prefix_for(doc.frame_id),
            origin=base_origin,
            ax_by_id=ax_by_id,
            root_ax_id=as_str(ax_nodes[0].get("nodeId"), "nodeId") if ax_nodes else None,
            geom=doc.geom,
            oop_iframes=list(doc.oop_iframes),
        )
    for doc in docs:
        frame = frames.get(doc.frame_id)
        if frame is None:
            continue
        for backend, child_frame_id in doc.iframe_docs.items():
            child = frames.get(child_frame_id)
            if child is not None:
                frame.children[backend] = child
    return frames[docs[0].frame_id]


def _ax_value(value: Json | None) -> str:
    if not isinstance(value, dict):
        return ""
    raw = value.get("value")
    return "" if raw is None else str(raw)


def _ax_property(node: JsonDict, name: str) -> Json:
    for prop in as_list(node.get("properties") or [], "properties"):
        entry = as_map(prop, "properties[]")
        if entry.get("name") == name:
            value = entry.get("value")
            if isinstance(value, dict):
                return value.get("value", value)
            return value
    return None


def _should_skip(node: JsonDict, role: str, name: str) -> bool:
    if role not in SKIP_ROLES or name:
        return False
    return all(
        as_map(prop, "properties[]").get("name") not in STATE_PROPERTIES
        for prop in as_list(node.get("properties") or [], "properties")
    )


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _image_name(src: str | None) -> str:
    if not src:
        return ""
    path = urlparse(src).path
    filename = path.rsplit("/", 1)[-1]
    if "." in filename:
        return _truncate(filename, MAX_NAME_LEN)
    return ""


def _format_extras(node: JsonDict, geom: NodeGeom | None) -> str:
    extras: list[str] = []
    if geom is not None and geom.input_type:
        extras.append(f'type="{geom.input_type}"')
    value = _ax_property(node, "value")
    if value is not None and value != "":
        extras.append(f'value="{_truncate(str(value), MAX_VALUE_LEN)}"')
    checked = _ax_property(node, "checked")
    if checked is not None:
        extras.append(f"checked={checked}")
    expanded = _ax_property(node, "expanded")
    if expanded is not None:
        extras.append(f"expanded={expanded}")
    if _ax_property(node, "disabled"):
        extras.append("disabled")
    for prop_name in EXTRA_PROPERTIES:
        prop = _ax_property(node, prop_name)
        if prop is None or prop is False or prop == "":
            continue
        if isinstance(prop, bool):
            extras.append(prop_name)
        elif prop_name == "url":
            url = str(prop)
            if url.startswith(("data:", "blob:", "file:")):
                continue
            if url.startswith("javascript:"):
                url = "#"
            extras.append(f'url="{url}"')
        else:
            extras.append(f'{prop_name}="{_truncate(str(prop), MAX_VALUE_LEN)}"')
    return " " + " ".join(extras) if extras else ""


def _frame_by_prefix(root: FrameSnapshot, prefix: str) -> FrameSnapshot | None:
    if root.prefix == prefix:
        return root
    for child in root.children.values():
        found = _frame_by_prefix(child, prefix)
        if found is not None:
            return found
    return None


def _node_by_backend(frame: FrameSnapshot, backend_id: int) -> str | None:
    for ax_id, node in frame.ax_by_id.items():
        if node.get("backendDOMNodeId") == backend_id:
            return ax_id
    return None


def render_page(
    root: FrameSnapshot,
    *,
    viewport: Size,
    model_size: Size | None = None,
    filter_type: str = "all",
    max_depth: int = DEFAULT_MAX_DEPTH,
    ref: str | None = None,
) -> str | None:
    target = effective_model_size(viewport, model_size)
    scale_x = target.width / viewport.width
    scale_y = target.height / viewport.height
    lines: list[str] = []
    visited: set[tuple[int, str]] = set()

    def coord_str(geom: NodeGeom | None) -> str:
        if geom is None or geom.bounds is None:
            return ""
        bx, by, bw, bh = geom.bounds
        return f" (x={int((bx + bw / 2) * scale_x)},y={int((by + bh / 2) * scale_y)})"

    def splice(frame: FrameSnapshot, backend_id: int | None, depth: int) -> None:
        child = frame.children.get(backend_id) if backend_id is not None else None
        if child is not None and child.root_ax_id is not None:
            render_node(child, child.root_ax_id, depth, "")

    def render_node(frame: FrameSnapshot, ax_id: str, depth: int, parent_name: str) -> None:
        if depth > max_depth or (id(frame), ax_id) in visited:
            return
        visited.add((id(frame), ax_id))
        node = frame.ax_by_id.get(ax_id)
        if node is None or _ax_property(node, "hidden"):
            return
        role = _ax_value(node.get("role"))
        name = _ax_value(node.get("name"))
        backend = node.get("backendDOMNodeId")
        backend_id = backend if isinstance(backend, int) else None
        child_ids = [
            as_str(child, "childIds[]") for child in as_list(node.get("childIds") or [], "childIds")
        ]
        geom = frame.geom.get(backend_id) if backend_id is not None else None

        def descend(child_depth: int, child_parent_name: str) -> None:
            for child_id in child_ids:
                render_node(frame, child_id, child_depth, child_parent_name)
            splice(frame, backend_id, child_depth)

        if role == "StaticText" and name == parent_name:
            return
        transparent = (
            bool(node.get("ignored"))
            or (role in IGNORED_ROLES and not name)
            or _should_skip(node, role, name)
            or (
                filter_type == "interactive"
                and role not in INTERACTIVE_ROLES
                and not (geom is not None and geom.cursor_pointer)
            )
        )
        if transparent:
            descend(depth, parent_name)
            return
        if filter_type == "viewport" and geom is not None and geom.bounds is not None:
            bx, by, bw, bh = geom.bounds
            offscreen = (
                bx + bw <= 0 or by + bh <= 0 or bx >= viewport.width or by >= viewport.height
            )
            if bw <= 0 or bh <= 0 or offscreen:
                return

        ref_str = ""
        if backend_id is not None and role != "option":
            ref_str = f" [ref={frame.prefix}e{backend_id}]"
        display_name = name
        if not display_name and role == "image":
            display_name = _image_name(geom.img_src if geom else None)
        name_str = f' "{_truncate(display_name, MAX_NAME_LEN)}"' if display_name else ""
        indent = "  " * depth
        extras = _format_extras(node, geom)
        lines.append(f"{indent}- {role}{name_str}{ref_str}{coord_str(geom)}{extras}")
        descend(depth + 1, name)

    if ref is not None:
        parsed = split_ref(ref)
        if parsed is None:
            return None
        prefix, backend_id = parsed
        frame = _frame_by_prefix(root, prefix)
        if frame is None:
            return None
        start = _node_by_backend(frame, backend_id)
        if start is None:
            return None
        render_node(frame, start, 0, "")
    elif root.root_ax_id is not None:
        render_node(root, root.root_ax_id, 0, "")
    return "\n".join(lines)


HEADING_ROLES = {"heading"}
LIST_ITEM_ROLES = {"listitem"}
BLOCK_ROLES = {"paragraph", "blockquote", "article", "Section", "main", "region"}


def render_markdown(root: FrameSnapshot) -> str:
    """Reading view: the accessibility tree rendered as markdown — headings keep their level, links
    carry their href, list items bullet — so the model reads structure, not a flat innerText smear.
    Same frame splice as render_page."""
    blocks: list[str] = []
    visited: set[tuple[int, str]] = set()

    def emit(text: str) -> None:
        text = text.strip()
        if text and (not blocks or blocks[-1] != text):
            blocks.append(text)

    def walk(frame: FrameSnapshot, ax_id: str, parent_name: str) -> None:
        if (id(frame), ax_id) in visited:
            return
        visited.add((id(frame), ax_id))
        node = frame.ax_by_id.get(ax_id)
        if node is None or _ax_property(node, "hidden"):
            return
        role = _ax_value(node.get("role"))
        name = _ax_value(node.get("name"))
        backend = node.get("backendDOMNodeId")
        backend_id = backend if isinstance(backend, int) else None

        if not (role == "StaticText" and name == parent_name):
            if role in HEADING_ROLES and name:
                level = _ax_property(node, "level")
                depth = int(level) if isinstance(level, int) and 1 <= level <= 6 else 1
                emit(f"{'#' * depth} {name}")
            elif role == "link" and name:
                url = _ax_property(node, "url")
                emit(f"[{name}]({url})" if isinstance(url, str) and url else name)
            elif role in LIST_ITEM_ROLES and name:
                emit(f"- {name}")
            elif role == "image" and (name or (backend_id and frame.geom.get(backend_id))):
                geom = frame.geom.get(backend_id) if backend_id is not None else None
                alt = name or _image_name(geom.img_src if geom else None)
                if alt:
                    emit(f"![{alt}]")
            elif name and role not in IGNORED_ROLES:
                emit(name)

        for child in as_list(node.get("childIds") or [], "childIds"):
            walk(frame, as_str(child, "childIds[]"), name)
        child_frame = frame.children.get(backend_id) if backend_id is not None else None
        if child_frame is not None and child_frame.root_ax_id is not None:
            walk(child_frame, child_frame.root_ax_id, "")

    if root.root_ax_id is not None:
        walk(root, root.root_ax_id, "")
    return "\n\n".join(blocks)


@dataclass(frozen=True)
class BrowserPage:
    browser: BrowserPageSession
    viewport: Size
    max_frame_depth: int

    async def tree(self, tab: PageTab, filter_type: str, ref: str | None) -> str | None:
        root = await self.snapshot(tab)
        return render_page(
            root,
            viewport=self.viewport,
            model_size=self.browser.model_size,
            filter_type=filter_type,
            ref=ref,
        )

    async def markdown(self, tab: PageTab) -> str:
        return render_markdown(await self.snapshot(tab))

    def resolve_ref(self, tab: PageTab, ref: str) -> tuple[FrameNode, int]:
        parsed = split_ref(ref)
        if parsed is None:
            raise HallucinationError(
                f'invalid browser ref {ref!r}: refs look like "e12" or "f1e3" and come from '
                "read_page or find — do not invent them"
            )
        prefix, backend_id = parsed
        node = tab.ref_frames.get(prefix)
        if node is None:
            raise HallucinationError(
                f"browser ref {ref!r} is not resolvable: re-read the page and use a ref it returns"
            )
        return node, backend_id

    async def ref_point(self, tab: PageTab, ref: str) -> tuple[int, int]:
        node, backend_id = self.resolve_ref(tab, ref)
        conn = self.browser.connection()
        try:
            await conn.send(
                "DOM.scrollIntoViewIfNeeded",
                {"backendNodeId": backend_id},
                session_id=node.session_id,
            )
            quads = await conn.send(
                "DOM.getContentQuads", {"backendNodeId": backend_id}, session_id=node.session_id
            )
            quad = next(
                (as_list(item, "quads[]") for item in as_list(quads.get("quads"), "quads")), None
            )
            if quad is None:
                model = await conn.send(
                    "DOM.getBoxModel", {"backendNodeId": backend_id}, session_id=node.session_id
                )
                quad = as_list(as_map(model.get("model"), "model").get("content"), "content")
        except CdpError:
            raise HallucinationError(
                f"browser ref {ref!r} is not resolvable: re-read the page and use a ref it returns"
            ) from None
        xs = [_coord_float_or_default(quad[index], 0.0) for index in range(0, 8, 2)]
        ys = [_coord_float_or_default(quad[index], 0.0) for index in range(1, 8, 2)]
        return int(sum(xs) / 4 + node.origin[0]), int(sum(ys) / 4 + node.origin[1])

    async def snapshot(self, tab: PageTab) -> FrameSnapshot:
        root = await self._snapshot_target(tab, tab.session_id, "", (0.0, 0.0), 0)
        tab.ref_frames = {}
        self._register_frames(tab, root)
        return root

    async def _snapshot_target(
        self,
        tab: PageTab,
        session_id: str,
        root_prefix: str,
        origin: tuple[float, float],
        depth: int,
    ) -> FrameSnapshot:
        root = await fetch_target(
            self.browser.connection(),
            session_id,
            root_prefix=root_prefix,
            prefix_for=lambda frame_id: f"f{tab.frame_seq(frame_id)}",
            base_origin=origin,
        )
        if depth < self.max_frame_depth:
            await self._attach_oop_frames(tab, root, depth)
        return root

    async def _attach_oop_frames(self, tab: PageTab, root: FrameSnapshot, depth: int) -> None:
        stack = [root]
        while stack:
            frame = stack.pop()
            stack.extend(frame.children.values())
            for backend_id in frame.oop_iframes:
                child = await self._snapshot_oop(tab, frame, backend_id, depth)
                if child is not None:
                    frame.children[backend_id] = child

    async def _snapshot_oop(
        self, tab: PageTab, frame: FrameSnapshot, backend_id: int, depth: int
    ) -> FrameSnapshot | None:
        conn = self.browser.connection()
        try:
            described = await conn.send(
                "DOM.describeNode", {"backendNodeId": backend_id}, session_id=frame.session_id
            )
        except CdpError:
            logger.warning("browser.oop_describe_failed", extra={"backend_id": backend_id})
            return None
        frame_id = as_map(described.get("node"), "node").get("frameId")
        if not isinstance(frame_id, str):
            return None
        session_id = await self._oop_session(frame_id)
        if session_id is None:
            return None
        geom = frame.geom.get(backend_id)
        bounds = geom.bounds if geom is not None else None
        origin = (bounds[0], bounds[1]) if bounds is not None else frame.origin
        try:
            return await self._snapshot_target(
                tab, session_id, f"f{tab.frame_seq(frame_id)}", origin, depth + 1
            )
        except CdpError:
            logger.warning("browser.oop_snapshot_failed", extra={"frame_id": frame_id})
            return None

    def _register_frames(self, tab: PageTab, frame: FrameSnapshot) -> None:
        tab.ref_frames[frame.prefix] = FrameNode(frame.frame_id, frame.session_id, frame.origin)
        for child in frame.children.values():
            self._register_frames(tab, child)

    async def _oop_session(self, frame_id: str) -> str | None:
        cached = self.browser.oop_sessions.get(frame_id)
        if cached is not None:
            return cached
        try:
            attached = await self.browser.connection().send(
                "Target.attachToTarget", {"targetId": frame_id, "flatten": True}
            )
        except CdpError:
            return None
        session_id = as_str(attached.get("sessionId"), "sessionId")
        await self.browser.init_session(session_id)
        self.browser.oop_sessions[frame_id] = session_id
        return session_id


def _coord_float_or_default(value: Json | None, default: float) -> float:
    match value:
        case int() | float():
            return float(value)
        case str() if value:
            return float(value)
        case None:
            return default
        case _:
            raise ValidationError("value must be numeric")
