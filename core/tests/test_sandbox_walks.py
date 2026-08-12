"""The six client file ops against the real `sbxfs`, on one real tree, under the real runner.

`sbxfs` is the oracle and runs as a subprocess, installed onto a scratch PATH exactly as the local
carrier installs it, with `PATH` pinned to the stock directories so a walk is compared against the
stdlib branch a machine without ripgrep takes. The subject is the shipped payload itself —
`client_payload(op, params)`, the composition the carrier sends — run the way the relay runs it:
`osascript -l JavaScript <payload> run <workdir>`. Both sides get the same params and the same
tree; a mutating op (`edit`, `write`) runs each side against its own copy of the tree, and the
whole tree state is compared after. The assertion is on the parsed JSON, since one side spells
non-ASCII as an escape and the other as itself.

A program cannot list a directory, so enumeration arrives as a file in the session workdir, written
by one command the carrier issues first. `ENUMERATION` holds that command per op, verbatim, reading
`$UFO_WALK_ROOT` (`path` or `workspace` for `grep`, `workspace` for the other two) and writing
`$UFO_OP_WORKDIR/<params.enum>`:

    grep     one `find` listing every directory and regular file, pruning the skipped names. The
             directories carry the order — `os.walk` visits a directory's own files, sorted, before
             descending, and `find` meets subdirectories in exactly the `readdir` order `os.walk`
             does, so a file's parent's position in the listing puts the hits back in the oracle's
             order.
    glob     two sections in one file: the `find` listing, a `\\0`, then one `stat` line a file in
             the same order. `stat` has no record terminator a filename cannot contain, so the names
             arrive `find`-terminated and the measurements arrive positionally; the program refuses
             sections whose lengths disagree, which is the answer the oracle gives when a file
             leaves between its listing and its `stat`. `%z %.9Fm` is BSD; GNU spells it `%s %.9Y`.
    changes  one `find` naming each checkout, and one `git status` plus one `git diff HEAD` a
             checkout — the two commands the oracle runs — framed into `\\0` fields.

The fixture tree is hostile on purpose: a name with a space, a file with no trailing newline, a
nested directory, a skipped directory, a text extension holding NUL bytes, a binary extension
holding text, an unknown extension, invalid UTF-8, CRLF and lone-CR line endings, an astral
character straddling the line cap, and more matches than the head limit. `modified` is compared
exactly, since `stat`'s nanosecond format reconstructs the same double `os.stat` reports."""

import base64
import json
import os
import subprocess
import sys
from collections.abc import Callable
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from types import ModuleType

import pytest

from ufo.sandbox.local import _provision_scratch
from ufo.sandbox.terminal import client_payload

SANDBOX = Path(__file__).parents[1] / "src" / "ufo" / "sandbox"
CLIENT = SANDBOX / "client"
RUNNER = "/usr/bin/osascript"
STOCK_PATH = "/usr/bin:/bin"
GIT = "/usr/bin/git"
GREP_LINE_CHAR_CAP = 2000
GLOB_MAX_RESULTS = 1000

SKIP_NAMES = (
    ".cache",
    ".git",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".svelte-kit",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
    "vendor",
    "venv",
)
SKIPPED = " -o ".join(f"-name '{name}'" for name in SKIP_NAMES)
UNSKIPPED_ROOT = r'! -path "$UFO_WALK_ROOT"'
REPOSITORY_COMMANDS = rf"""[ -n "$1" ] || exit 0
r=${{1%/.git}}
printf "R\000%s\000" "$r"
{GIT} -C "$r" -c core.quotePath=false status --porcelain=v1 -z --no-renames -uall
printf "D\000%s\000" "$({GIT} -C "$r" -c core.quotePath=false diff HEAD --no-renames -U3 \
2>/dev/null)"
"""

ENUMERATION = {
    "grep": rf"""/usr/bin/find "$UFO_WALK_ROOT" \( {UNSKIPPED_ROOT} -a \( {SKIPPED} \) \) -prune \
-o \( -type d -o -type f \) -print0 > "$UFO_OP_WORKDIR/grep-enum"
""",
    "glob": r"""/usr/bin/find "$UFO_WALK_ROOT" -type f -print0 > "$UFO_OP_WORKDIR/glob-enum"
printf '\000' >> "$UFO_OP_WORKDIR/glob-enum"
/usr/bin/find "$UFO_WALK_ROOT" -type f -print0 \
| /usr/bin/xargs -0 /usr/bin/stat -f '%z %.9Fm' >> "$UFO_OP_WORKDIR/glob-enum"
""",
    "changes": rf"""r='{REPOSITORY_COMMANDS}'
/usr/bin/find "$UFO_WALK_ROOT" \( {UNSKIPPED_ROOT} -a -name '.git' \) -prune -print0 \
-o \( {UNSKIPPED_ROOT} -a \( {SKIPPED} \) \) -prune \
| /usr/bin/xargs -0 -n1 /bin/sh -c "$r" sh > "$UFO_OP_WORKDIR/changes-enum"
""",
}

pytestmark = pytest.mark.skipif(
    not Path(RUNNER).exists(), reason="the op programs run under osascript"
)


@pytest.fixture(scope="module")
def installed() -> Path:
    return _provision_scratch() / "bin" / "sbxfs"


def _load(name: str, path: Path) -> ModuleType:
    loader = SourceFileLoader(name, str(path))
    spec = spec_from_loader(loader.name, loader)
    assert spec is not None
    module = module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


def _oracle_module() -> ModuleType:
    if "containment" not in sys.modules:
        _load("containment", SANDBOX / "containment.py")
    return _load("walks_oracle", SANDBOX / "image" / "sbxfs")


def _environment(home: Path) -> dict[str, str]:
    return {
        "PATH": STOCK_PATH,
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": str(home / "gitconfig"),
        "LANG": "en_US.UTF-8",
    }


def _oracle(installed: Path, home: Path, op: str, params: dict[str, object]) -> dict:
    run = subprocess.run(
        [sys.executable, str(installed), op, json.dumps(params)],
        capture_output=True,
        text=True,
        env=_environment(home),
    )
    assert run.stdout, run.stderr
    parsed = json.loads(run.stdout)
    assert isinstance(parsed, dict)
    return parsed


def _walked(op: str, params: dict[str, object], workdir: Path, home: Path) -> dict:
    root = params.get("path") if op == "grep" and params.get("path") else params["workspace"]
    listing = subprocess.run(
        ["/bin/sh", "-c", ENUMERATION[op]],
        capture_output=True,
        text=True,
        env={**_environment(home), "UFO_OP_WORKDIR": str(workdir), "UFO_WALK_ROOT": str(root)},
    )
    assert listing.returncode == 0, listing.stderr
    return _run(op, params, workdir)


def _run(op: str, params: dict[str, object], workdir: Path) -> dict:
    payload = workdir / f"{op}.payload.js"
    payload.write_text(client_payload(op, params))
    run = subprocess.run(
        [RUNNER, "-l", "JavaScript", str(payload), "run", str(workdir)],
        capture_output=True,
        text=True,
    )
    assert run.stdout, run.stderr
    parsed = json.loads(run.stdout)
    assert isinstance(parsed, dict)
    return parsed


@pytest.fixture
def session(tmp_path: Path) -> Path:
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    (tmp_path / "home").mkdir(exist_ok=True)
    return workdir


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """One workspace holding every shape the walks could disagree over."""
    root = tmp_path / "workspace"
    nested = root / "src" / "deep"
    nested.mkdir(parents=True)
    (root / "node_modules").mkdir()
    (root / "empty").mkdir()
    (root / "notes.txt").write_text("alpha needle\nbeta\nneedle again\ngamma\n")
    (root / "with space.txt").write_text("space needle\n")
    (root / "trailing.md").write_text("needle without newline")
    (root / "app.py").write_text("import os\n\n\ndef needle():\n    return 1\n")
    (root / "src" / "lib.py").write_text("needle = 2\nother = 3\n")
    (nested / "deeper.py").write_text("deep needle\n")
    (root / "src" / "styles.css").write_text("a { color: red }\n")
    (root / "node_modules" / "skipped.py").write_text("needle in a skipped tree\n")
    (root / "accented.txt").write_text("café needle\n中文\n")
    (root / "data.dat").write_text("needle in a binary extension\n")
    (root / "unknown.zzz").write_text("needle in an unknown extension\n")
    (root / "nul.txt").write_bytes(b"needle\x00binary\n")
    (root / "many.txt").write_text("".join(f"needle {index}\n" for index in range(12)))
    (root / "link.py").symlink_to(root / "app.py")
    (root / "long.txt").write_text(
        "needle " + "x" * (GREP_LINE_CHAR_CAP - 1992) + "\N{GRINNING FACE}" + "y" * 500 + "\n"
    )
    (root / "invalid.txt").write_bytes(
        b"needle \xff\xfe here\ntruncated needle \xe0\xa0\nbad needle \xc3(\n"
    )
    (root / "crlf.txt").write_bytes(b"needle one\r\nneedle two\rneedle three\n")
    (root / "late-nul.txt").write_bytes(b"needle early\n" + b"pad\n" * 3000 + b"\x00tail\n")
    return root


def _repository(root: Path, home: Path) -> None:
    subprocess.run([GIT, "init", "-q", "-b", "main", str(root)], check=True, env=_environment(home))
    for setting, value in (("user.email", "t@t"), ("user.name", "t")):
        subprocess.run(
            [GIT, "-C", str(root), "config", setting, value], check=True, env=_environment(home)
        )


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """A workspace holding two checkouts — one with a history and every kind of pending change, one
    with no commit at all — plus a checkout nested inside the first, whose changes the outer one's
    own report is what a walk answers with instead."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    root = tmp_path / "workspace"
    main = root / "main"
    main.mkdir(parents=True)
    _repository(main, home)
    (main / "tracked.txt").write_text("one\ntwo\nthree\n")
    (main / "gone.txt").write_text("removed\n")
    (main / "spaced name.txt").write_text("one\n")
    subprocess.run([GIT, "-C", str(main), "add", "."], check=True, env=_environment(home))
    subprocess.run(
        [GIT, "-C", str(main), "commit", "-qm", "first"], check=True, env=_environment(home)
    )
    (main / "tracked.txt").write_text("one\ntwo changed\nthree\n")
    (main / "spaced name.txt").write_text("two\n")
    (main / "gone.txt").unlink()
    (main / "fresh.txt").write_text("added line\nsecond line\n")
    (main / "no-newline.txt").write_text("added without newline")
    (main / "picture.png").write_bytes(b"\x89PNG\r\n\x1a\nnot really\n")
    (main / "accented.txt").write_text("café\n中文\n")
    (main / "crlf.txt").write_bytes(b"one\r\ntwo\rthree\n")
    (main / "invalid.txt").write_bytes(b"bad \xff\xfe bytes\n")
    (main / "staged.txt").write_text("staged\n")
    subprocess.run([GIT, "-C", str(main), "add", "staged.txt"], check=True, env=_environment(home))
    inner = main / "vendored"
    inner.mkdir()
    _repository(inner, home)
    (inner / "inner.txt").write_text("inner\n")
    unborn = root / "unborn"
    unborn.mkdir()
    _repository(unborn, home)
    (unborn / "first.txt").write_text("unborn staged\n")
    subprocess.run([GIT, "-C", str(unborn), "add", "."], check=True, env=_environment(home))
    return root


GREP_CASES: tuple[dict[str, object], ...] = (
    {"pattern": "needle"},
    {"pattern": "needle", "output_mode": "content"},
    {"pattern": "needle", "output_mode": "count"},
    {"pattern": "nothing here matches"},
    {"pattern": "nothing here matches", "output_mode": "content"},
    {"pattern": "nothing here matches", "output_mode": "count"},
    {"pattern": "needle", "output_mode": "content", "head_limit": 3},
    {"pattern": "needle", "output_mode": "content", "head_limit": 19},
    {"pattern": "needle", "output_mode": "content", "head_limit": 20},
    {"pattern": "needle", "output_mode": "files_with_matches", "head_limit": 2},
    {"pattern": "needle", "output_mode": "files_with_matches", "head_limit": 6},
    {"pattern": "needle", "output_mode": "content", "context": 1},
    {"pattern": "needle", "output_mode": "content", "before_context": 2, "after_context": 1},
    {"pattern": "needle t", "output_mode": "content", "context": 2},
    {"pattern": "NEEDLE", "ignore_case": True},
    {"pattern": "NEEDLE"},
    {"pattern": "needle", "glob": "*.py"},
    {"pattern": "needle", "glob": "*.txt", "output_mode": "content"},
    {"pattern": "needle", "glob": "*deep*"},
    {"pattern": "needle", "type": "py"},
    {"pattern": "needle", "type": "nonsense"},
    {"pattern": r"needle \d+", "output_mode": "content"},
    {"pattern": r"\w+ = \d", "output_mode": "content"},
    {"pattern": r"caf\S", "output_mode": "content"},
    {"pattern": "^$", "output_mode": "count"},
    {"pattern": "^$", "output_mode": "content"},
    {"pattern": "needle", "output_mode": "content", "path": "src"},
    {"pattern": "needle", "output_mode": "content", "path": "notes.txt"},
    {"pattern": "needle", "output_mode": "content", "path": "empty"},
    {"pattern": "^needle", "output_mode": "content"},
    {"pattern": "e{2}dle", "output_mode": "count"},
    {"pattern": "needle x", "output_mode": "content"},
    {"pattern": "GRINNING", "output_mode": "count"},
    {"pattern": "needle", "output_mode": "content", "path": "data.dat"},
    {"pattern": "needle\\n.*again", "multiline": True},
    {"pattern": "needle.{0,20}\\n", "multiline": True, "output_mode": "count"},
    {"pattern": "needle early", "output_mode": "count"},
)


@pytest.mark.parametrize("case", GREP_CASES, ids=lambda case: json.dumps(case, sort_keys=True))
def test_grep_matches_sbxfs(
    tree: Path, session: Path, installed: Path, case: dict[str, object]
) -> None:
    home = session.parent / "home"
    params: dict[str, object] = {**case, "workspace": str(tree)}
    if isinstance(case.get("path"), str):
        params["path"] = str(tree / str(case["path"]))
    expected = _oracle(installed, home, "grep", params)
    assert _walked("grep", {**params, "enum": "grep-enum"}, session, home) == expected


def test_grep_refuses_an_unknown_output_mode(tree: Path, session: Path, installed: Path) -> None:
    home = session.parent / "home"
    params: dict[str, object] = {
        "pattern": "needle",
        "output_mode": "lines",
        "workspace": str(tree),
    }
    expected = _oracle(installed, home, "grep", params)
    answered = _walked("grep", {**params, "enum": "grep-enum"}, session, home)
    assert answered == expected == {"error": "invalid output_mode: lines"}


def test_grep_refuses_an_invalid_pattern(tree: Path, session: Path, installed: Path) -> None:
    """Both engines refuse it and both say so under the same key; only the message is the engine's
    own, since one is Python's `re` and the other JavaScriptCore's RegExp."""
    home = session.parent / "home"
    params: dict[str, object] = {"pattern": "needle(", "workspace": str(tree)}
    expected = _oracle(installed, home, "grep", params)
    answered = _walked("grep", {**params, "enum": "grep-enum"}, session, home)
    assert str(expected["error"]).startswith("invalid regex: ")
    assert str(answered["error"]).startswith("invalid regex: ")
    assert answered != expected


def test_a_name_holding_a_line_break_is_read_like_any_other(
    tmp_path: Path, session: Path, installed: Path
) -> None:
    """The `jsc` shell's `readFile` dropped a line break from the path it was given; the prelude's
    `NSData` reader takes the path whole, so the one name a walk could list but not open now reads —
    the enumeration is `\\0`-terminated end to end, and no measurement rides the same line as a
    name."""
    home = session.parent / "home"
    root = tmp_path / "broken"
    root.mkdir()
    (root / "plain.txt").write_text("needle plain\n")
    (root / "line\nbreak.txt").write_text("needle in a name with a line break\n")
    grep: dict[str, object] = {"pattern": "needle", "workspace": str(root), "enum": "grep-enum"}
    expected = _oracle(installed, home, "grep", {"pattern": "needle", "workspace": str(root)})
    answered = _walked("grep", grep, session, home)
    assert sorted(str(name) for name in expected["files"]) == sorted(
        [str(root / "line\nbreak.txt"), str(root / "plain.txt")]
    )
    assert answered == expected
    listed = _oracle(installed, home, "glob", {"pattern": "*.txt", "workspace": str(root)})
    assert (
        _walked(
            "glob", {"pattern": "*.txt", "workspace": str(root), "enum": "glob-enum"}, session, home
        )
        == listed
    )


GLOB_CASES: tuple[dict[str, object], ...] = (
    {"pattern": "*.txt"},
    {"pattern": "*.py"},
    {"pattern": "**/*.py"},
    {"pattern": "**/*"},
    {"pattern": "**"},
    {"pattern": "src/*.py"},
    {"pattern": "src/**/*.py"},
    {"pattern": "with space.txt"},
    {"pattern": "*.nothing"},
    {"pattern": "[an]*.txt"},
    {"pattern": "?????.txt"},
    {"pattern": "**/*.py", "exclude_names": ["deep"]},
    {"pattern": "**/*", "exclude_names": ["src", "node_modules"]},
    {"pattern": "**/*.py", "path": "src"},
    {"pattern": "*.py", "path": "src"},
    {"pattern": "*.zzz"},
)


@pytest.mark.parametrize("case", GLOB_CASES, ids=lambda case: json.dumps(case, sort_keys=True))
def test_glob_matches_sbxfs(
    tree: Path, session: Path, installed: Path, case: dict[str, object]
) -> None:
    home = session.parent / "home"
    for index, path in enumerate(sorted(path for path in tree.rglob("*") if path.is_file())):
        os.utime(path, ns=(1_700_000_000_000_000_000 + index * 1_000_000_007,) * 2)
    params: dict[str, object] = {**case, "workspace": str(tree)}
    if isinstance(case.get("path"), str):
        params["path"] = str(tree / str(case["path"]))
    expected = _oracle(installed, home, "glob", params)
    assert _walked("glob", {**params, "enum": "glob-enum"}, session, home) == expected


@pytest.mark.parametrize("pattern", ("../outside/*.txt", "/elsewhere/*.txt"))
def test_glob_refuses_a_pattern_that_leaves_the_workspace(
    tree: Path, session: Path, installed: Path, pattern: str
) -> None:
    home = session.parent / "home"
    params: dict[str, object] = {"pattern": pattern, "workspace": str(tree)}
    expected = _oracle(installed, home, "glob", params)
    answered = _walked("glob", {**params, "enum": "glob-enum"}, session, home)
    assert answered == expected
    assert "error" in expected


@pytest.mark.parametrize("names", ([""], ["a/b"], ["a\\b"]))
def test_glob_refuses_a_name_that_is_not_a_component(
    tree: Path, session: Path, installed: Path, names: list[str]
) -> None:
    home = session.parent / "home"
    params: dict[str, object] = {
        "pattern": "*.txt",
        "exclude_names": names,
        "workspace": str(tree),
    }
    expected = _oracle(installed, home, "glob", params)
    answered = _walked("glob", {**params, "enum": "glob-enum"}, session, home)
    assert answered == expected
    assert expected == {"error": "exclude_names must be a list of path component names"}


def test_glob_caps_and_reports_the_overflow(tmp_path: Path, session: Path, installed: Path) -> None:
    home = session.parent / "home"
    root = tmp_path / "workspace"
    root.mkdir()
    for index in range(GLOB_MAX_RESULTS + 5):
        (root / f"file-{index:05d}.txt").write_text("x\n")
    params: dict[str, object] = {"pattern": "*.txt", "workspace": str(root)}
    expected = _oracle(installed, home, "glob", params)
    answered = _walked("glob", {**params, "enum": "glob-enum"}, session, home)
    assert answered["truncated"] is True
    assert answered["count"] == expected["count"] == GLOB_MAX_RESULTS
    assert answered == expected


def test_changes_matches_sbxfs(checkout: Path, session: Path, installed: Path) -> None:
    home = session.parent / "home"
    params: dict[str, object] = {"workspace": str(checkout)}
    expected = _oracle(installed, home, "changes", params)
    assert _walked("changes", {**params, "enum": "changes-enum"}, session, home) == expected


def test_changes_reads_every_kind_of_pending_change(
    checkout: Path, session: Path, installed: Path
) -> None:
    """What the agreement above is agreement about: a modified tracked file carries git's own patch,
    an untracked one carries a patch built from its bytes, a missing trailing newline carries git's
    marker, a binary extension carries no patch, a deletion carries the removal, and the checkout
    nested inside another is not listed at all."""
    expected = _oracle(installed, session.parent / "home", "changes", {"workspace": str(checkout)})
    by_path = {change["path"]: change for change in expected["changes"]}
    assert "two changed" in by_path["main/tracked.txt"]["patch"]
    assert by_path["main/fresh.txt"]["patch"].startswith(
        "--- /dev/null\n+++ b/fresh.txt\n@@ -0,0 +1,2 @@"
    )
    assert by_path["main/no-newline.txt"]["patch"].endswith("\\ No newline at end of file\n")
    assert by_path["main/picture.png"]["patch"] == ""
    assert by_path["main/gone.txt"]["patch"].startswith("--- a/gone.txt")
    assert by_path["main/staged.txt"]["patch"].startswith("--- /dev/null")
    assert by_path["unborn/first.txt"]["patch"].startswith("--- /dev/null")
    assert not [path for path in by_path if "vendored/" in path]


def test_changes_carries_the_patch_of_a_tracked_name_with_a_space(
    checkout: Path, session: Path, installed: Path
) -> None:
    """`git diff` appends a tab to the `---`/`+++` paths of a name holding a space — git's own
    disambiguator, not part of the name — so both sides strip it and the file carries its patch."""
    home = session.parent / "home"
    params: dict[str, object] = {"workspace": str(checkout)}
    expected = _oracle(installed, home, "changes", params)
    answered = _walked("changes", {**params, "enum": "changes-enum"}, session, home)
    spaced = next(
        change for change in expected["changes"] if change["path"] == "main/spaced name.txt"
    )
    assert "+++" in spaced["patch"]
    assert answered == expected


def test_changes_truncates_at_the_file_cap(tmp_path: Path, session: Path, installed: Path) -> None:
    home = session.parent / "home"
    root = tmp_path / "workspace"
    root.mkdir()
    _repository(root, home)
    for index in range(105):
        (root / f"new-{index:04d}.txt").write_text(f"file {index}\n")
    params: dict[str, object] = {"workspace": str(root)}
    expected = _oracle(installed, home, "changes", params)
    answered = _walked("changes", {**params, "enum": "changes-enum"}, session, home)
    assert expected["truncated"] is True
    assert len(expected["changes"]) == 100
    assert answered == expected


def test_changes_truncates_a_long_untracked_file(
    tmp_path: Path, session: Path, installed: Path
) -> None:
    home = session.parent / "home"
    root = tmp_path / "workspace"
    root.mkdir()
    _repository(root, home)
    (root / "tracked.txt").write_text("".join(f"kept {index}\n" for index in range(1500)))
    subprocess.run([GIT, "-C", str(root), "add", "."], check=True, env=_environment(home))
    subprocess.run(
        [GIT, "-C", str(root), "commit", "-qm", "first"], check=True, env=_environment(home)
    )
    (root / "tracked.txt").write_text("".join(f"changed {index}\n" for index in range(1500)))
    (root / "wide.txt").write_text("".join(f"line {index}\n" for index in range(2500)))
    (root / "huge.txt").write_text("x" * 30_000 + "\n")
    params: dict[str, object] = {"workspace": str(root)}
    expected = _oracle(installed, home, "changes", params)
    answered = _walked("changes", {**params, "enum": "changes-enum"}, session, home)
    assert all(change["truncated"] for change in expected["changes"])
    assert all(len(change["patch"]) == 10_000 for change in expected["changes"])
    assert answered == expected


def test_changes_stops_at_the_character_budget(
    tmp_path: Path, session: Path, installed: Path
) -> None:
    home = session.parent / "home"
    root = tmp_path / "workspace"
    root.mkdir()
    _repository(root, home)
    for index in range(40):
        (root / f"bulk-{index:03d}.txt").write_text(f"line {index}\n" * 900)
    params: dict[str, object] = {"workspace": str(root)}
    expected = _oracle(installed, home, "changes", params)
    answered = _walked("changes", {**params, "enum": "changes-enum"}, session, home)
    assert expected["truncated"] is True
    assert 0 < len(expected["changes"]) < 40
    assert answered == expected


def test_changes_answers_a_workspace_holding_no_checkout(
    tmp_path: Path, session: Path, installed: Path
) -> None:
    home = session.parent / "home"
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "loose.txt").write_text("not in a checkout\n")
    params: dict[str, object] = {"workspace": str(root)}
    expected = _oracle(installed, home, "changes", params)
    answered = _walked("changes", {**params, "enum": "changes-enum"}, session, home)
    assert answered == expected == {"changes": [], "truncated": False}


def test_a_file_the_reader_cannot_open_ends_the_oracles_scan_and_not_the_programs(
    tmp_path: Path, session: Path, installed: Path
) -> None:
    """A second difference, and this one favours the program. `_contained_text` lets a
    `PermissionError` out, so one unreadable file turns the whole scan into a refusal; `readFile`
    only throws for the file, so the program skips it and answers for the rest — which is what the
    op's own docstring says an unreadable hit should do."""
    home = session.parent / "home"
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "open.txt").write_text("needle visible\n")
    closed = root / "closed.txt"
    closed.write_text("needle hidden\n")
    closed.chmod(0o000)
    params: dict[str, object] = {"pattern": "needle", "workspace": str(root)}
    expected = _oracle(installed, home, "grep", params)
    answered = _walked("grep", {**params, "enum": "grep-enum"}, session, home)
    assert str(expected["error"]).startswith("PermissionError: ")
    assert answered == {"files": [str(root / "open.txt")], "count": 1, "truncated": False}


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake image body"
JPEG_BYTES = b"\xff\xd8\xff" + b"jpeg body"
GIF_BYTES = b"GIF89a" + b"gif body"
WEBP_BYTES = b"RIFF\x24\x00\x00\x00WEBP" + b"webp body"


@pytest.fixture
def readable(tmp_path: Path) -> Path:
    """One tree holding every shape a read could disagree over: windows, caps, line-ending zoo,
    every image magic, a mislabeled image, binary refusals."""
    root = tmp_path / "readable"
    root.mkdir()
    (root / "plain.txt").write_text("".join(f"line {index}\n" for index in range(1, 9)))
    (root / "wide.txt").write_text("".join(f"row {index}\n" for index in range(1, 121)))
    (root / "long.txt").write_text(
        "lead " + "x" * (GREP_LINE_CHAR_CAP - 1992) + "\N{GRINNING FACE}" + "y" * 500 + "\n"
    )
    (root / "trailing.md").write_text("last line keeps no newline")
    (root / "empty.txt").write_bytes(b"")
    (root / "data.dat").write_text("text in a binary extension\n")
    (root / "nul.txt").write_bytes(b"early\x00null\n")
    (root / "crlf.txt").write_bytes(b"one\r\ntwo\rthree\n")
    (root / "invalid.txt").write_bytes(b"bad \xff\xfe bytes\ntruncated \xe0\xa0\n")
    (root / "with space.txt").write_text("a named space\n")
    (root / "pic.png").write_bytes(PNG_BYTES)
    (root / "photo.jpg").write_bytes(JPEG_BYTES)
    (root / "anim.gif").write_bytes(GIF_BYTES)
    (root / "leaf.webp").write_bytes(WEBP_BYTES)
    (root / "fake.png").write_bytes(b"not an image at all\n")
    (root / "ctrl.png").write_bytes(b"\x00\x01machine bytes")
    (root / "doc.pdf").write_bytes(b"%PDF-1.4 junk")
    return root


READ_CASES: tuple[dict[str, object], ...] = (
    {"path": "plain.txt"},
    {"path": "plain.txt", "offset": 3},
    {"path": "plain.txt", "offset": 3, "limit": 2},
    {"path": "plain.txt", "offset": 99},
    {"path": "plain.txt", "limit": 3},
    {"path": "plain.txt", "offset": 0},
    {"path": "wide.txt", "offset": 5, "limit": 10},
    {"path": "wide.txt", "offset": 118},
    {"path": "long.txt"},
    {"path": "trailing.md"},
    {"path": "empty.txt"},
    {"path": "crlf.txt"},
    {"path": "invalid.txt"},
    {"path": "with space.txt"},
    {"path": "pic.png"},
    {"path": "photo.jpg"},
    {"path": "anim.gif"},
    {"path": "leaf.webp"},
    {"path": "fake.png"},
    {"path": "ctrl.png"},
    {"path": "data.dat"},
    {"path": "nul.txt"},
    {"path": "missing.txt"},
)


@pytest.mark.parametrize("case", READ_CASES, ids=lambda case: json.dumps(case, sort_keys=True))
def test_read_matches_sbxfs(
    readable: Path, session: Path, installed: Path, case: dict[str, object]
) -> None:
    """Windows, numbering, caps, image results and the refusals — including the mislabeled-image
    message, whose `data[:8]!r` is a Python bytes repr the program reproduces byte for byte."""
    home = session.parent / "home"
    params: dict[str, object] = {
        **case,
        "path": str(readable / str(case["path"])),
        "workspace": str(readable),
    }
    expected = _oracle(installed, home, "read", params)
    assert _run("read", params, session) == expected


def test_read_refuses_an_image_over_the_cap(tmp_path: Path, session: Path, installed: Path) -> None:
    home = session.parent / "home"
    root = tmp_path / "big"
    root.mkdir()
    (root / "huge.png").write_bytes(PNG_BYTES + b"\x00" * (5 * 1024 * 1024))
    params: dict[str, object] = {"path": str(root / "huge.png"), "workspace": str(root)}
    expected = _oracle(installed, home, "read", params)
    answered = _run("read", params, session)
    assert answered == expected
    assert "image read cap" in str(expected["error"])


def test_a_pdf_read_stays_on_the_deploy(readable: Path, session: Path, installed: Path) -> None:
    """The one read the client does not answer: poppler lives on the server, so the program refuses
    in the `{"error": …}` shape and the carrier routes the pull server-side — while `sbxfs` itself,
    with poppler absent from the stock PATH, still answers a text-free pdf result. Asserted as the
    divergence it is."""
    home = session.parent / "home"
    params: dict[str, object] = {"path": str(readable / "doc.pdf"), "workspace": str(readable)}
    expected = _oracle(installed, home, "read", params)
    answered = _run("read", params, session)
    assert expected["type"] == "pdf" and expected["render_unavailable"] is True
    assert answered == {"error": f"{readable / 'doc.pdf'} is a pdf; a pdf read runs on the deploy"}


def _b64(text: str) -> str:
    return base64.b64encode(text.encode(), altchars=b"-_").decode()


def _edit(old: str, new: str, replace_all: bool = False) -> dict[str, object]:
    return {"old_string_b64": _b64(old), "new_string_b64": _b64(new), "replace_all": replace_all}


def _twins(tmp_path: Path, build: Callable[[Path], None]) -> tuple[Path, Path]:
    """One tree twice: the oracle mutates one copy, the program the other, and the differential
    compares what is left standing."""
    sides = (tmp_path / "oracle-ws", tmp_path / "client-ws")
    for side in sides:
        side.mkdir()
        build(side)
    return sides


def _tree_state(root: Path) -> dict[str, tuple[bytes | None, int]]:
    state = {}
    for path in sorted(root.rglob("*")):
        content = path.read_bytes() if path.is_file() else None
        state[str(path.relative_to(root))] = (content, path.stat().st_mode & 0o777)
    return state


def _rootless(result: dict, root: Path) -> dict:
    return json.loads(json.dumps(result).replace(str(root), "WS"))


def _mutated(
    op: str,
    params: dict[str, object],
    tmp_path: Path,
    session: Path,
    installed: Path,
    build: Callable[[Path], None],
) -> tuple[dict, dict]:
    """Run one mutating op through both sides, each on its own copy, asserting the surviving trees
    and the root-normalized results agree — the return is for assertions on the content."""
    oracle_ws, client_ws = _twins(tmp_path, build)
    home = session.parent / "home"
    expected = _oracle(installed, home, op, _placed(params, oracle_ws))
    answered = _run(op, _placed(params, client_ws), session)
    assert _rootless(answered, client_ws) == _rootless(expected, oracle_ws)
    assert _tree_state(client_ws) == _tree_state(oracle_ws)
    return expected, answered


def _placed(params: dict[str, object], root: Path) -> dict[str, object]:
    placed: dict[str, object] = {**params, "workspace": "WS"}
    return {
        key: _at(value, root) if isinstance(value, str) else value for key, value in placed.items()
    }


def _at(value: str, root: Path) -> str:
    if value == "WS":
        return str(root)
    return str(root / value.removeprefix("WS/")) if value.startswith("WS/") else value


EDIT_CASES: tuple[tuple[bytes, list[dict[str, object]]], ...] = (
    (b"alpha beta\ngamma\n", [_edit("beta", "BETA")]),
    (b"a\x00b line\nnext\n", [_edit("a\x00b", "kept")]),
    (b"last line keeps no newline", [_edit("no newline", "still none")]),
    (b"one\r\ntwo\r\nthree\n", [_edit("two\r\n", "TWO\r\n")]),
    (
        b"def f():\n    return 1\n\ndef g():\n    return 2\n",
        [_edit("def g():\n    return 2", "def g():\n    return 3")],
    ),
    (b"dup\ndup\n", [_edit("dup", "one")]),
    (b"dup\ndup\n", [_edit("dup", "one", replace_all=True)]),
    (b"aaa\n", [_edit("aa", "X")]),
    (b"hello\n", [_edit("absent", "x")]),
    (b"hello\n", []),
    (b"one two\n", [_edit("one", "1"), _edit("two", "2")]),
    (b"x" * 2500 + b" needle tail\n", [_edit("needle", "FOUND")]),
    (
        (
            "".join(f"before {index}\n" for index in range(150))
            + "target\n"
            + "".join(f"after {index}\n" for index in range(150))
        ).encode(),
        [_edit("target", "replaced target")],
    ),
    (b"", [_edit("", "seeded", replace_all=True)]),
)


@pytest.mark.parametrize("content,edits", EDIT_CASES, ids=lambda value: repr(value)[:60])
def test_edit_matches_sbxfs(
    tmp_path: Path,
    session: Path,
    installed: Path,
    content: bytes,
    edits: list[dict[str, object]],
) -> None:
    """Applied edits and refusals alike: the result JSON (message, count, numbered snippet), the
    file's bytes after, and its mode — a refused edit leaves the twin trees identical too."""

    def build(root: Path) -> None:
        (root / "subject.txt").write_bytes(content)
        (root / "subject.txt").chmod(0o750)

    params: dict[str, object] = {"path": "WS/subject.txt", "edits": edits}
    _mutated("edit", params, tmp_path, session, installed, build)


def test_edit_aliases_invalid_utf8_the_way_sbxfs_does(
    tmp_path: Path, session: Path, installed: Path
) -> None:
    """Both sides edit the `errors="replace"` decoding, so a byte the edit never touched is still
    rewritten: the lone `\\xe9` comes back as U+FFFD's three bytes from each. Pinned as the shared
    aliasing it is — byte-exactness was traded for `sbxfs` parity on both sides at once."""

    def build(root: Path) -> None:
        (root / "subject.txt").write_bytes(b"caf\xe9 x\n")

    params: dict[str, object] = {"path": "WS/subject.txt", "edits": [_edit("x", "y")]}
    _mutated("edit", params, tmp_path, session, installed, build)
    edited = (tmp_path / "client-ws" / "subject.txt").read_bytes()
    assert edited == b"caf\xef\xbf\xbd y\n"


def test_edit_refuses_text_that_is_not_base64(
    tmp_path: Path, session: Path, installed: Path
) -> None:
    def build(root: Path) -> None:
        (root / "subject.txt").write_text("hello\n")

    params: dict[str, object] = {
        "path": "WS/subject.txt",
        "edits": [{"old_string_b64": "!!!", "new_string_b64": _b64("x"), "replace_all": False}],
    }
    expected, _ = _mutated("edit", params, tmp_path, session, installed, build)
    assert expected == {"error": "text must be base64 encoded"}


def test_edit_reports_the_missing_file(tmp_path: Path, session: Path, installed: Path) -> None:
    def build(root: Path) -> None:
        pass

    params: dict[str, object] = {"path": "WS/absent.txt", "edits": [_edit("a", "b")]}
    expected, _ = _mutated("edit", params, tmp_path, session, installed, build)
    assert str(expected["error"]).endswith("absent.txt not found")


def _staged_writer(
    content: bytes, mode: int, target: str | None = None, target_mode: int = 0o755
) -> Callable[[Path], None]:
    def build(root: Path) -> None:
        (root / "staged.tmp").write_bytes(content)
        (root / "staged.tmp").chmod(mode)
        if target is not None:
            (root / "target.txt").write_bytes(target.encode())
            (root / "target.txt").chmod(target_mode)

    return build


def test_write_creates_and_carries_the_staged_mode(
    tmp_path: Path, session: Path, installed: Path
) -> None:
    params: dict[str, object] = {"path": "WS/target.txt", "staged_path": "WS/staged.tmp"}
    expected, _ = _mutated(
        "write", params, tmp_path, session, installed, _staged_writer(b"fresh body\n", 0o600)
    )
    assert expected == {"created": True}
    assert (tmp_path / "client-ws" / "target.txt").stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / "client-ws" / "staged.tmp").exists()


def test_write_overwrite_keeps_the_targets_mode(
    tmp_path: Path, session: Path, installed: Path
) -> None:
    params: dict[str, object] = {
        "path": "WS/target.txt",
        "staged_path": "WS/staged.tmp",
        "allow_existing": True,
    }
    expected, _ = _mutated(
        "write",
        params,
        tmp_path,
        session,
        installed,
        _staged_writer(b"new body\n", 0o600, target="old body\n"),
    )
    assert expected == {"created": False}
    assert (tmp_path / "client-ws" / "target.txt").read_bytes() == b"new body\n"
    assert (tmp_path / "client-ws" / "target.txt").stat().st_mode & 0o777 == 0o755


def test_write_refuses_an_unread_target_and_still_removes_the_staged_copy(
    tmp_path: Path, session: Path, installed: Path
) -> None:
    params: dict[str, object] = {"path": "WS/target.txt", "staged_path": "WS/staged.tmp"}
    expected, _ = _mutated(
        "write",
        params,
        tmp_path,
        session,
        installed,
        _staged_writer(b"new body\n", 0o600, target="old body\n"),
    )
    assert str(expected["error"]).endswith("target.txt must be read before it is written")
    assert (tmp_path / "client-ws" / "target.txt").read_bytes() == b"old body\n"
    assert not (tmp_path / "client-ws" / "staged.tmp").exists()


def test_write_reports_the_missing_staged_file(
    tmp_path: Path, session: Path, installed: Path
) -> None:
    params: dict[str, object] = {"path": "WS/target.txt", "staged_path": "WS/absent.tmp"}
    expected, _ = _mutated("write", params, tmp_path, session, installed, lambda root: None)
    assert str(expected["error"]).endswith("absent.tmp not found")


def test_write_refuses_a_staged_path_that_is_the_target(
    tmp_path: Path, session: Path, installed: Path
) -> None:
    params: dict[str, object] = {"path": "WS/staged.tmp", "staged_path": "WS/staged.tmp"}
    expected, _ = _mutated(
        "write", params, tmp_path, session, installed, _staged_writer(b"kept\n", 0o600)
    )
    assert expected == {"error": "staged file must differ from its target"}
    assert (tmp_path / "client-ws" / "staged.tmp").read_bytes() == b"kept\n"


def test_write_creates_the_targets_parents(tmp_path: Path, session: Path, installed: Path) -> None:
    params: dict[str, object] = {"path": "WS/a/b/c.txt", "staged_path": "WS/staged.tmp"}
    expected, _ = _mutated(
        "write", params, tmp_path, session, installed, _staged_writer(b"nested\n", 0o644)
    )
    assert expected == {"created": True}
    assert (tmp_path / "client-ws" / "a" / "b" / "c.txt").read_bytes() == b"nested\n"


def test_write_lands_on_a_name_with_a_space(tmp_path: Path, session: Path, installed: Path) -> None:
    params: dict[str, object] = {"path": "WS/with space.txt", "staged_path": "WS/staged.tmp"}
    expected, _ = _mutated(
        "write", params, tmp_path, session, installed, _staged_writer(b"spaced\n", 0o644)
    )
    assert expected == {"created": True}


def test_a_staged_path_outside_the_workspace_is_only_refused_by_the_oracle(
    tmp_path: Path, session: Path, installed: Path
) -> None:
    """The pinned containment divergence: `sbxfs` confines the staged path to the workspace and
    refuses one outside it, while the client program checks nothing the member's own shell would
    not — the RFC's stated posture, so the write applies there."""
    home = session.parent / "home"
    oracle_ws, client_ws = _twins(tmp_path, lambda root: None)
    for outside in (tmp_path / "outside-o", tmp_path / "outside-c"):
        outside.mkdir()
        (outside / "staged.tmp").write_bytes(b"escaped\n")
    expected = _oracle(
        installed,
        home,
        "write",
        {
            "path": str(oracle_ws / "target.txt"),
            "staged_path": str(tmp_path / "outside-o" / "staged.tmp"),
            "workspace": str(oracle_ws),
        },
    )
    answered = _run(
        "write",
        {
            "path": str(client_ws / "target.txt"),
            "staged_path": str(tmp_path / "outside-c" / "staged.tmp"),
            "workspace": str(client_ws),
        },
        session,
    )
    assert "escapes" in str(expected["error"])
    assert not (oracle_ws / "target.txt").exists()
    assert answered == {"created": True}
    assert (client_ws / "target.txt").read_bytes() == b"escaped\n"


def test_a_directory_target_refuses_in_two_voices(
    tmp_path: Path, session: Path, installed: Path
) -> None:
    """The second pinned write divergence: the oracle's `lstat` names the shape (`is a directory`),
    the program's `rename(2)` names the act it refused. Both refuse, both remove the staged copy."""
    home = session.parent / "home"

    def build(root: Path) -> None:
        (root / "staged.tmp").write_bytes(b"body\n")
        (root / "target.txt").mkdir()

    oracle_ws, client_ws = _twins(tmp_path, build)
    params: dict[str, object] = {
        "path": "WS/target.txt",
        "staged_path": "WS/staged.tmp",
        "allow_existing": True,
    }
    expected = _oracle(installed, home, "write", _placed(params, oracle_ws))
    answered = _run("write", _placed(params, client_ws), session)
    assert str(expected["error"]).endswith("target.txt is a directory")
    assert "Could not rename" in str(answered["error"])
    assert not (oracle_ws / "staged.tmp").exists()
    assert not (client_ws / "staged.tmp").exists()


def test_the_programs_carry_what_sbxfs_holds_as_constants(session: Path) -> None:
    """The one thing the differential cannot see: a set drifting apart from the op it mirrors. Each
    program declares its own classification and caps, so they are read back out of the shipped file
    — by the same trailer mechanism that calls `main` — and compared with `sbxfs` itself."""
    oracle = _oracle_module()
    stated = _stated(session, "grep", ("TEXT_EXTENSIONS", "BINARY_EXTENSIONS", "TYPE_EXTENSIONS"))
    assert frozenset(stated["TEXT_EXTENSIONS"]) == oracle.TEXT_EXTENSIONS
    assert frozenset(stated["BINARY_EXTENSIONS"]) == oracle.BINARY_EXTENSIONS
    assert {name: frozenset(values) for name, values in stated["TYPE_EXTENSIONS"].items()} == (
        oracle.TYPE_EXTENSIONS
    )
    caps = _stated(
        session, "grep", ("GREP_DEFAULT_HEAD", "GREP_LINE_CHAR_CAP", "BINARY_SNIFF_BYTES")
    )
    assert caps == {
        "GREP_DEFAULT_HEAD": oracle.GREP_DEFAULT_HEAD,
        "GREP_LINE_CHAR_CAP": oracle.GREP_LINE_CHAR_CAP,
        "BINARY_SNIFF_BYTES": oracle.BINARY_SNIFF_BYTES,
    }
    assert _stated(session, "glob", ("GLOB_MAX_RESULTS",)) == {
        "GLOB_MAX_RESULTS": oracle.GLOB_MAX_RESULTS
    }
    changes = _stated(
        session,
        "changes",
        (
            "CHANGES_INPUT_MAX_CHARS",
            "CHANGES_INPUT_MAX_LINES",
            "CHANGES_PATCH_MAX_CHARS",
            "CHANGES_MAX_REPOSITORIES",
            "CHANGES_MAX_FILES",
            "CHANGES_TOTAL_MAX_CHARS",
            "UNTRACKED_STATUS",
            "BINARY_EXTENSIONS",
        ),
    )
    assert frozenset(changes.pop("BINARY_EXTENSIONS")) == oracle.BINARY_EXTENSIONS
    assert changes == {name: getattr(oracle, name) for name in changes}
    assert frozenset(SKIP_NAMES) == oracle.SKIP_DIRS
    read = _stated(
        session,
        "read",
        (
            "READ_DEFAULT_LIMIT",
            "LINE_CHAR_CAP",
            "BINARY_SNIFF_BYTES",
            "IMAGE_MAX_BYTES",
            "IMAGE_MEDIA_TYPES",
            "BINARY_EXTENSIONS",
        ),
    )
    assert frozenset(read.pop("BINARY_EXTENSIONS")) == oracle.BINARY_EXTENSIONS
    assert read == {name: getattr(oracle, name) for name in read}
    assert _stated(
        session, "edit", ("LINE_CHAR_CAP", "EDIT_SNIPPET_CONTEXT", "EDIT_SNIPPET_MAX_CHARS")
    ) == {
        "LINE_CHAR_CAP": oracle.LINE_CHAR_CAP,
        "EDIT_SNIPPET_CONTEXT": oracle.EDIT_SNIPPET_CONTEXT,
        "EDIT_SNIPPET_MAX_CHARS": oracle.EDIT_SNIPPET_MAX_CHARS,
    }


def _stated(workdir: Path, op: str, names: tuple[str, ...]) -> dict:
    payload = workdir / f"{op}.stated.js"
    body = ", ".join(f'"{name}": {name}' for name in names)
    unset = "function (key, value) { return value instanceof Set ? Array.from(value) : value; }"
    prelude = (CLIENT / "prelude.js").read_text()
    program = (CLIENT / f"{op}.js").read_text()
    payload.write_text(
        f"{prelude}\n{program}\n"
        f"function run(argv) {{ __start__(argv); print(JSON.stringify({{{body}}}, {unset})); }}\n"
    )
    run = subprocess.run(
        [RUNNER, "-l", "JavaScript", str(payload), "run", str(workdir)],
        capture_output=True,
        text=True,
    )
    assert run.stdout, run.stderr
    return json.loads(run.stdout)
