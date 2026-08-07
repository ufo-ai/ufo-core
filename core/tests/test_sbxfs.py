import base64
import sys
from contextlib import contextmanager
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from types import ModuleType

import pytest

SANDBOX_DIR = Path(__file__).parents[1] / "src" / "ufo" / "sandbox"
SBXFS_PATH = SANDBOX_DIR / "image" / "sbxfs"
CONTAINMENT_PATH = SANDBOX_DIR / "containment.py"


def _load(name: str, path: Path) -> ModuleType:
    loader = SourceFileLoader(name, str(path))
    spec = spec_from_loader(loader.name, loader)
    assert spec is not None
    module = module_from_spec(spec)
    # Registered before it runs: a dataclass in the module resolves its own module by name while the
    # decorator runs, and an import of the module by name has to reach this object, not a new load.
    sys.modules[name] = module
    loader.exec_module(module)
    return module


# `sbxfs` imports the containment guard as a sibling module: the image bakes it beside the script
# and the local carrier installs it beside the script, so `sys.path[0]` finds it under any carrier.
# Registering it under that name here is what makes the script under test import the very module the
# serve process imports as `ufo.sandbox.containment`, rather than a second copy of the same checks.
containment = _load("containment", CONTAINMENT_PATH)


def _sbxfs() -> ModuleType:
    return _load("ufo_test_sbxfs", SBXFS_PATH)


def _workspace(tmp_path: Path) -> tuple[Path, Path]:
    """A workspace holding one file, and an outside directory holding the file an escape would
    reach."""
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (workspace / "notes.txt").write_text("workspace\n")
    (outside / "secret.txt").write_text("outside\n")
    return workspace, outside


def test_write_pins_the_checked_parent_directory(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    parent = workspace / "repo"
    moved = workspace / "moved"
    outside = tmp_path / "outside"
    staged = workspace / ".tool-output" / "write.stage"
    parent.mkdir(parents=True)
    outside.mkdir()
    staged.parent.mkdir()
    (parent / "app.py").write_text("workspace-old\n")
    (outside / "app.py").write_text("outside-old\n")
    staged.write_text("workspace-new\n")
    module = _sbxfs()

    @contextmanager
    def swapped_lock(_path: Path):
        parent.rename(moved)
        parent.symlink_to(outside, target_is_directory=True)
        yield

    module.__dict__["_file_lock"] = swapped_lock
    result = module.op_write(
        {
            "path": str(parent / "app.py"),
            "workspace": str(workspace),
            "staged_path": str(staged),
            "allow_existing": True,
        }
    )

    assert result["change"]["patch"] == (
        "--- before\n+++ after\n@@ -1 +1 @@\n-workspace-old\n+workspace-new\n"
    )
    assert (moved / "app.py").read_text() == "workspace-new\n"
    assert (outside / "app.py").read_text() == "outside-old\n"
    assert not staged.exists()


def test_edit_pins_the_checked_parent_directory(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    parent = workspace / "repo"
    moved = workspace / "moved"
    outside = tmp_path / "outside"
    parent.mkdir(parents=True)
    outside.mkdir()
    (parent / "app.py").write_text("workspace-old\n")
    (outside / "app.py").write_text("outside-old\n")
    module = _sbxfs()

    @contextmanager
    def swapped_lock(_path: Path):
        parent.rename(moved)
        parent.symlink_to(outside, target_is_directory=True)
        yield

    module.__dict__["_file_lock"] = swapped_lock
    result = module.op_edit(
        {
            "path": str(parent / "app.py"),
            "workspace": str(workspace),
            "edits": [
                {
                    "old_string_b64": base64.urlsafe_b64encode(b"workspace-old").decode(),
                    "new_string_b64": base64.urlsafe_b64encode(b"workspace-new").decode(),
                    "replace_all": False,
                }
            ],
        }
    )

    assert result["change"]["patch"] == (
        "--- before\n+++ after\n@@ -1 +1 @@\n-workspace-old\n+workspace-new\n"
    )
    assert (moved / "app.py").read_text() == "workspace-new\n"
    assert (outside / "app.py").read_text() == "outside-old\n"


def _edit(old: str, new: str) -> list[dict]:
    return [
        {
            "old_string_b64": base64.urlsafe_b64encode(old.encode()).decode(),
            "new_string_b64": base64.urlsafe_b64encode(new.encode()).decode(),
            "replace_all": False,
        }
    ]


def test_read_returns_a_contained_file(tmp_path: Path) -> None:
    workspace, _outside = _workspace(tmp_path)
    module = _sbxfs()

    result = module.op_read({"path": str(workspace / "notes.txt"), "workspace": str(workspace)})

    assert result["content"] == "1\tworkspace"
    assert result["total_lines"] == 1


def test_read_refuses_a_traversal_path(tmp_path: Path) -> None:
    workspace, outside = _workspace(tmp_path)
    module = _sbxfs()

    with pytest.raises(containment.LocationEscape, match="escapes"):
        module.op_read(
            {"path": str(workspace / ".." / "outside" / "secret.txt"), "workspace": str(workspace)}
        )
    assert (outside / "secret.txt").read_text() == "outside\n"


def test_read_refuses_a_planted_symlink(tmp_path: Path) -> None:
    """CVE-2026-56692's shape: the name is inside the workspace, the bytes are not. A read that
    resolved the name and opened it would return the host file's contents."""
    workspace, outside = _workspace(tmp_path)
    (workspace / "link.txt").symlink_to(outside / "secret.txt")
    module = _sbxfs()

    with pytest.raises(containment.NotRegularFile):
        module.op_read({"path": str(workspace / "link.txt"), "workspace": str(workspace)})


def test_read_refuses_a_symlinked_directory_component_out_of_the_workspace(tmp_path: Path) -> None:
    """The link need not be at the target: a directory whose link leaves the workspace is enough,
    and canonicalizing the parent is what turns that into a refusal instead of a read of the link's
    target — check 2's work. The descent walks components already canonical, so an ancestor link
    inside the root is followed; that case is the in-root write test below."""
    workspace, outside = _workspace(tmp_path)
    (workspace / "dir").symlink_to(outside, target_is_directory=True)
    module = _sbxfs()

    with pytest.raises(containment.LocationEscape, match="escapes"):
        module.op_read({"path": str(workspace / "dir" / "secret.txt"), "workspace": str(workspace)})


def test_read_refuses_a_symlinked_workspace_root(tmp_path: Path) -> None:
    """A symlinked root is the case containment alone misses: every path under the link resolves
    inside the link's target, so each later check passes while the bytes come from elsewhere."""
    workspace, _outside = _workspace(tmp_path)
    linked_root = tmp_path / "linked-workspace"
    linked_root.symlink_to(workspace, target_is_directory=True)
    module = _sbxfs()

    with pytest.raises(containment.NonDirectoryAncestor):
        module.op_read({"path": str(workspace / "notes.txt"), "workspace": str(linked_root)})


def test_write_refuses_a_traversal_path(tmp_path: Path) -> None:
    workspace, outside = _workspace(tmp_path)
    staged = workspace / ".tool-output" / "write.stage"
    staged.parent.mkdir()
    staged.write_text("planted\n")
    module = _sbxfs()

    with pytest.raises(containment.LocationEscape, match="escapes"):
        module.op_write(
            {
                "path": str(workspace / ".." / "outside" / "secret.txt"),
                "workspace": str(workspace),
                "staged_path": str(staged),
                "allow_existing": True,
            }
        )
    assert (outside / "secret.txt").read_text() == "outside\n"


def test_write_refuses_a_planted_symlink(tmp_path: Path) -> None:
    workspace, outside = _workspace(tmp_path)
    (workspace / "link.txt").symlink_to(outside / "secret.txt")
    staged = workspace / ".tool-output" / "write.stage"
    staged.parent.mkdir()
    staged.write_text("planted\n")
    module = _sbxfs()

    with pytest.raises(containment.NotRegularFile):
        module.op_write(
            {
                "path": str(workspace / "link.txt"),
                "workspace": str(workspace),
                "staged_path": str(staged),
                "allow_existing": True,
            }
        )
    assert (outside / "secret.txt").read_text() == "outside\n"
    assert not staged.exists()


def test_edit_refuses_a_traversal_path(tmp_path: Path) -> None:
    workspace, outside = _workspace(tmp_path)
    module = _sbxfs()

    with pytest.raises(containment.LocationEscape, match="escapes"):
        module.op_edit(
            {
                "path": str(workspace / ".." / "outside" / "secret.txt"),
                "workspace": str(workspace),
                "edits": _edit("outside", "edited"),
            }
        )
    assert (outside / "secret.txt").read_text() == "outside\n"


def test_edit_refuses_a_planted_symlink(tmp_path: Path) -> None:
    workspace, outside = _workspace(tmp_path)
    (workspace / "link.txt").symlink_to(outside / "secret.txt")
    module = _sbxfs()

    with pytest.raises(containment.NotRegularFile):
        module.op_edit(
            {
                "path": str(workspace / "link.txt"),
                "workspace": str(workspace),
                "edits": _edit("outside", "edited"),
            }
        )
    assert (outside / "secret.txt").read_text() == "outside\n"


def test_glob_refuses_a_traversal_pattern(tmp_path: Path) -> None:
    workspace, _outside = _workspace(tmp_path)
    module = _sbxfs()

    with pytest.raises(containment.RelativeEscape):
        module.op_glob(
            {"pattern": "../outside/*.txt", "path": str(workspace), "workspace": str(workspace)}
        )


def test_glob_refuses_an_absolute_pattern_outside_the_workspace(tmp_path: Path) -> None:
    """The pattern, not the scoped path, decides where the walk starts: an absolute pattern used to
    re-root at the filesystem anchor and enumerate outside the workspace entirely."""
    workspace, outside = _workspace(tmp_path)
    module = _sbxfs()

    with pytest.raises(containment.LocationEscape, match="escapes"):
        module.op_glob(
            {
                "pattern": f"{outside}/*.txt",
                "path": str(workspace),
                "workspace": str(workspace),
            }
        )


def test_glob_re_roots_an_absolute_workspace_pattern(tmp_path: Path) -> None:
    workspace, _outside = _workspace(tmp_path)
    module = _sbxfs()

    result = module.op_glob(
        {
            "pattern": f"{workspace}/*.txt",
            "path": str(workspace),
            "workspace": str(workspace),
        }
    )

    assert [file["path"] for file in result["files"]] == [str(workspace / "notes.txt")]


def test_glob_omits_planted_symlinks(tmp_path: Path) -> None:
    workspace, outside = _workspace(tmp_path)
    (workspace / "link.txt").symlink_to(outside / "secret.txt")
    (workspace / "dir").symlink_to(outside, target_is_directory=True)
    module = _sbxfs()

    flat = module.op_glob({"pattern": "*.txt", "path": str(workspace), "workspace": str(workspace)})
    through_link = module.op_glob(
        {"pattern": "dir/*.txt", "path": str(workspace), "workspace": str(workspace)}
    )

    assert [file["path"] for file in flat["files"]] == [str(workspace / "notes.txt")]
    assert through_link["files"] == []


def _grep(module: ModuleType, workspace: Path, **params: object) -> dict:
    return module.op_grep(
        {"pattern": "SECRET", "output_mode": "content", "workspace": str(workspace), **params}
    )


@pytest.mark.parametrize("ripgrep", [True, False], ids=["rg", "stdlib"])
def test_grep_omits_a_planted_symlink_on_either_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ripgrep: bool
) -> None:
    """The one op that returns file contents in bulk. `read` refuses the planted link; a scan that
    followed it returned the same host file's lines instead, with the model-facing `grep` pinned to
    the workspace root so no traversal was even needed.

    Both branches are asserted, because ripgrep's own default of skipping links it meets while
    walking is a third-party default, not a check of ours: with `rg` absent the stdlib walk yields
    symlinked files, and the guard is what refuses them either way."""
    workspace, outside = _workspace(tmp_path)
    (outside / "secret.txt").write_text("root:x:0:0:SECRET\n")
    (workspace / "notes.txt").write_text("nothing here\n")
    (workspace / "link.txt").symlink_to(outside / "secret.txt")
    (workspace / "real.txt").write_text("SECRET but ours\n")
    module = _sbxfs()
    if not ripgrep:
        monkeypatch.setattr(module.shutil, "which", lambda name: None)

    result = _grep(module, workspace, path=str(workspace), glob="*.txt")

    assert [match["file"] for match in result["matches"]] == [str(workspace / "real.txt")]


def test_grep_drops_a_ripgrep_hit_the_guard_does_not_vouch_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ripgrep skipping a link it meets while walking is its default, not a check of ours, and the
    lesson the issue quotes is not to rest on an upstream one. So its events are filtered by the
    same rule the stdlib walk's hits pass.

    Driven through `op_grep` with the ripgrep run stubbed, because today's `rg` never emits such an
    event: asserting on the filter alone leaves the branch that calls it unproven, and a build where
    it is never called reads exactly the same."""
    workspace, outside = _workspace(tmp_path)
    (outside / "secret.txt").write_text("root:x:0:0:SECRET\n")
    (workspace / "link.txt").symlink_to(outside / "secret.txt")
    module = _sbxfs()
    monkeypatch.setattr(module.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        module,
        "_rg_run",
        lambda argv: [
            _rg_match(workspace / "link.txt", 1, "root:x:0:0:SECRET"),
            _rg_match(workspace / "notes.txt", 3, "SECRET but ours"),
        ],
    )

    result = _grep(module, workspace, path=str(workspace))

    assert [match["file"] for match in result["matches"]] == [str(workspace / "notes.txt")]
    assert result["count"] == 1


def _rg_match(path: Path, line: int, content: str) -> dict:
    return {
        "type": "match",
        "data": {
            "path": {"text": str(path)},
            "line_number": line,
            "lines": {"text": f"{content}\n"},
        },
    }


def test_grep_refuses_a_path_outside_the_workspace(tmp_path: Path) -> None:
    workspace, outside = _workspace(tmp_path)
    (outside / "secret.txt").write_text("root:x:0:0:SECRET\n")
    module = _sbxfs()

    with pytest.raises(containment.LocationEscape, match="escapes"):
        _grep(module, workspace, path=str(outside / "secret.txt"))


def test_grep_refuses_a_planted_symlink_named_as_its_own_path(tmp_path: Path) -> None:
    """A scan of one file is a read of that file, so the link is refused rather than silently
    skipped: the caller named it, and answering "no matches" would hide the refusal."""
    workspace, outside = _workspace(tmp_path)
    (outside / "secret.txt").write_text("root:x:0:0:SECRET\n")
    (workspace / "link.txt").symlink_to(outside / "secret.txt")
    module = _sbxfs()

    with pytest.raises(containment.NotRegularFile):
        _grep(module, workspace, path=str(workspace / "link.txt"))


def test_grep_refuses_a_traversal_glob(tmp_path: Path) -> None:
    """The `glob` selects paths of its own, so scoping only the directory the walk starts from does
    not scope it."""
    workspace, _outside = _workspace(tmp_path)
    module = _sbxfs()

    with pytest.raises(containment.RelativeEscape):
        _grep(module, workspace, path=str(workspace), glob="../outside/*.txt")


@pytest.mark.parametrize("ripgrep", [True, False], ids=["rg", "stdlib"])
def test_grep_reads_a_relative_path_against_the_workspace_not_the_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ripgrep: bool
) -> None:
    """Whether `path` names one file or a directory decides which guard it goes through, and the
    guard roots a relative name at the workspace. A probe rooted at the process cwd instead picks
    the branch for one path while the guard runs on another — here cwd holds a *directory* of that
    name and the workspace holds a *file*, so the two disagree and only the workspace's answer is
    the right one.

    Both branches, because only one of them runs on any given machine: with `rg` on PATH the scan
    never consults the candidate list at all, so a rooting bug that the stdlib walk would surface
    hides behind whichever binary the runner happens to have."""
    workspace, outside = _workspace(tmp_path)
    (workspace / "target.txt").write_text("SECRET in the workspace file\n")
    (outside / "target.txt").mkdir()
    monkeypatch.chdir(outside)
    module = _sbxfs()
    if not ripgrep:
        monkeypatch.setattr(module.shutil, "which", lambda name: None)

    result = _grep(module, workspace, path="target.txt")

    assert [match["file"] for match in result["matches"]] == [str(workspace / "target.txt")]


def test_grep_reads_a_file_under_an_in_root_symlinked_directory(tmp_path: Path) -> None:
    """A scan still answers for the workspace's own files reached through a link that stays inside
    it — the policy the guard states: an ancestor link is followed once, under a resolution already
    proved contained, and the canonical hit is what comes back."""
    workspace, _outside = _workspace(tmp_path)
    (workspace / "real").mkdir()
    (workspace / "real" / "hit.txt").write_text("SECRET but ours\n")
    (workspace / "dir").symlink_to(workspace / "real", target_is_directory=True)
    module = _sbxfs()

    result = _grep(module, workspace, path=str(workspace / "dir"))

    assert [match["file"] for match in result["matches"]] == [str(workspace / "real" / "hit.txt")]


def test_write_through_an_in_root_symlinked_directory_lands_on_the_canonical_inode(
    tmp_path: Path,
) -> None:
    """The case that distinguishes check 2 from check 3, which nothing else in the suite reaches:
    an ancestor link pointing *inside* the root. Check 2 canonicalizes it, so the descent walks the
    real directory's components and the bytes land on that inode — never on a second copy under the
    link, and the link is left as the agent made it. An ancestor pointing *out* of the root is check
    2's refusal, asserted by the symlinked-directory-component read test above."""
    workspace, _outside = _workspace(tmp_path)
    (workspace / "real").mkdir()
    (workspace / "dir").symlink_to(workspace / "real", target_is_directory=True)
    staged = workspace / ".tool-output" / "write.stage"
    staged.parent.mkdir()
    staged.write_text("landed\n")
    module = _sbxfs()

    result = module.op_write(
        {
            "path": str(workspace / "dir" / "new.txt"),
            "workspace": str(workspace),
            "staged_path": str(staged),
        }
    )

    assert result["created"] is True
    assert (workspace / "real" / "new.txt").read_text() == "landed\n"
    assert (workspace / "dir").is_symlink()
