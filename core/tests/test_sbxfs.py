import base64
from contextlib import contextmanager
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from types import ModuleType

SBXFS_PATH = Path(__file__).parents[1] / "src" / "ufo" / "sandbox" / "image" / "sbxfs"


def _sbxfs() -> ModuleType:
    loader = SourceFileLoader("ufo_test_sbxfs", str(SBXFS_PATH))
    spec = spec_from_loader(loader.name, loader)
    assert spec is not None
    module = module_from_spec(spec)
    loader.exec_module(module)
    return module


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
