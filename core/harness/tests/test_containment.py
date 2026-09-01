from pathlib import Path

import pytest

from ufo.harness.containment import (
    ContainmentError,
    LocationEscape,
    NonDirectoryAncestor,
    NotRegularFile,
    RelativeEscape,
    configured_root,
    contained_dir,
    contained_file,
    contained_leaf,
    contained_pattern,
    contained_relative,
    contained_root,
)


def test_relative_path_refuses_unusable_names() -> None:
    for value in ("", ".", "..", "/tmp/file"):
        with pytest.raises(ContainmentError):
            contained_relative(value, "/workspace")


def test_lexical_guards_keep_nested_names_and_refuse_escapes(tmp_path: Path) -> None:
    assert contained_relative("a/../b.txt", "/workspace") == "/workspace/b.txt"
    assert contained_leaf("a/b.txt", "fallback.txt") == "b.txt"
    with pytest.raises(RelativeEscape):
        contained_pattern("../*.txt", tmp_path)
    with pytest.raises(LocationEscape):
        contained_pattern("/tmp/*.txt", tmp_path)


def test_root_policy_refuses_agent_symlinks_and_follows_operator_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    root = tmp_path / "root"
    root.symlink_to(target, target_is_directory=True)

    with pytest.raises(NonDirectoryAncestor):
        contained_root(root)
    assert configured_root(root, "workspace_root") == target.resolve()


def test_contained_file_reads_pinned_parents_and_refuses_symlink_escapes(tmp_path: Path) -> None:
    root = tmp_path / "root"
    target = root / "nested" / "value.txt"
    target.parent.mkdir(parents=True)
    target.write_text("value")

    with contained_file("nested/value.txt", root) as contained:
        assert contained.read_bytes(16) == b"value"

    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    (root / "linked.txt").symlink_to(outside)

    with pytest.raises(NotRegularFile), contained_file("linked.txt", root) as contained:
        contained.read_bytes(16)

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (root / "escape").symlink_to(outside_dir, target_is_directory=True)

    with pytest.raises(LocationEscape), contained_file("escape/value.txt", root):
        pass


def test_contained_file_creates_parents_and_replaces_bytes(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()

    with contained_file("nested/value.txt", root, create_parent=True) as contained:
        contained.replace_bytes(b"value", 0o640)

    assert (root / "nested" / "value.txt").read_bytes() == b"value"
    assert (root / "nested" / "value.txt").stat().st_mode & 0o777 == 0o640


def test_contained_dir_returns_a_directory(tmp_path: Path) -> None:
    root = tmp_path / "root"
    nested = root / "nested"
    nested.mkdir(parents=True)

    assert contained_dir("nested", root) == nested.resolve()
