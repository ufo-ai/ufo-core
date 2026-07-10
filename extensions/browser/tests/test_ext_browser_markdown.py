"""render_markdown block segmentation: inline text and links merge into one block per
BLOCK_ROLES node instead of one block per accessibility-tree text node."""

from ufo_ext_browser.bua.page import FrameSnapshot, render_markdown


def _ax(node_id: str, role: str, name: str = "", children: tuple[str, ...] = (), **props: object):
    node = {
        "nodeId": node_id,
        "role": {"value": role},
        "name": {"value": name},
        "childIds": list(children),
    }
    if props:
        node["properties"] = [
            {"name": key, "value": {"value": value}} for key, value in props.items()
        ]
    return node


def _snapshot(*nodes) -> FrameSnapshot:
    return FrameSnapshot(
        frame_id="frame",
        session_id="session",
        prefix="",
        origin=(0.0, 0.0),
        ax_by_id={node["nodeId"]: node for node in nodes},
        root_ax_id=nodes[0]["nodeId"],
        geom={},
    )


def test_a_paragraph_with_an_inline_link_renders_as_one_block() -> None:
    root = _snapshot(
        _ax("1", "RootWebArea", children=("2", "6")),
        _ax("2", "paragraph", children=("3", "4", "5")),
        _ax("3", "StaticText", "Read the"),
        _ax("4", "link", "docs", url="https://example.test/docs"),
        _ax("5", "StaticText", "before deploying."),
        _ax("6", "paragraph", children=("7",)),
        _ax("7", "StaticText", "Second paragraph."),
    )
    assert render_markdown(root) == (
        "Read the [docs](https://example.test/docs) before deploying.\n\nSecond paragraph."
    )


def test_headings_and_landmarks_flush_the_inline_buffer() -> None:
    root = _snapshot(
        _ax("1", "RootWebArea", children=("2", "3", "4")),
        _ax("2", "StaticText", "preamble"),
        _ax("3", "heading", "Title", level=2),
        _ax("4", "main", children=("5", "6")),
        _ax("5", "paragraph", children=("7",)),
        _ax("6", "paragraph", children=("8",)),
        _ax("7", "StaticText", "first"),
        _ax("8", "StaticText", "second"),
    )
    assert render_markdown(root) == "preamble\n\n## Title\n\nfirst\n\nsecond"
