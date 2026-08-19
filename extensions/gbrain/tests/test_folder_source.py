"""The gbrain folder backend over real temporary directories: posix-relative refs, the markdown
filter, title resolution, and the fail-closed missing root. No conftest: the shared
`ufo_testsupport` plugin covers fixtures, and these tests are offline (real files, no DB, no
transport)."""

import re
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from ufo_ext_gbrain.folder import GbrainFolderConfig, GbrainFolderSource

from ufo.sdk.sandbox import ContainmentError
from ufo.sdk.sources import SourceAuth, StreamFault, SyncResult


async def _fetch(root: Path) -> SyncResult:
    return await GbrainFolderSource().fetch(
        GbrainFolderConfig(root=str(root)), None, SourceAuth(workspace_id=uuid4())
    )


async def test_scan_lands_markdown_with_posix_refs_and_titles(tmp_path: Path) -> None:
    (tmp_path / "docs" / "nested").mkdir(parents=True)
    (tmp_path / ".git").mkdir()
    (tmp_path / "readme.md").write_text("# Top\n\nHello.\n")
    (tmp_path / "docs" / "guide.markdown").write_text("---\ntitle: Guide\n---\nBody.\n")
    (tmp_path / "docs" / "nested" / "deep.md").write_text("plain prose\n")
    (tmp_path / "docs" / "logo.png").write_bytes(b"\x89PNG\r\n")
    (tmp_path / ".git" / "objects.bin").write_bytes(b"\x00\x01\xff")

    result = await _fetch(tmp_path)

    assert [page.source_ref for page in result.pages] == [
        "docs/guide.markdown",
        "docs/nested/deep.md",
        "readme.md",
    ]
    assert {page.source_ref: page.title for page in result.pages} == {
        "docs/guide.markdown": "Guide",
        "docs/nested/deep.md": "docs/nested/deep.md",
        "readme.md": "Top",
    }
    assert result.snapshot is True
    assert result.next_cursor is None


async def test_empty_root_is_an_empty_snapshot(tmp_path: Path) -> None:
    result = await _fetch(tmp_path)
    assert result.pages == ()
    assert result.snapshot is True


async def test_missing_root_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ContainmentError, match="gone"):
        await _fetch(tmp_path / "gone")


async def test_undecodable_markdown_names_the_file(tmp_path: Path) -> None:
    (tmp_path / "bad.md").write_bytes(b"\xff\xfe")
    with pytest.raises(StreamFault, match=re.escape("bad.md is not utf-8 text")):
        await _fetch(tmp_path)


def test_relative_root_is_refused() -> None:
    with pytest.raises(ValidationError, match="pattern"):
        GbrainFolderConfig(root="notes")


async def test_symlink_is_never_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("# Secret\n")
    root = tmp_path / "brain"
    root.mkdir()
    (root / "kept.md").write_text("# Kept\n")
    (root / "link.md").symlink_to(outside)

    result = await _fetch(root)

    assert [page.source_ref for page in result.pages] == ["kept.md"]


async def test_oversize_folder_names_the_cap(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("x" * 16)
    (tmp_path / "b.md").write_text("y" * 16)
    source = GbrainFolderSource(max_bytes=24)
    with pytest.raises(StreamFault, match="24 bytes of markdown"):
        await source.fetch(
            GbrainFolderConfig(root=str(tmp_path)), None, SourceAuth(workspace_id=uuid4())
        )
