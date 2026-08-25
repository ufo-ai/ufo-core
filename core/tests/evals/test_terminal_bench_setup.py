import hashlib
import io
import tarfile
import tomllib
from pathlib import Path

import pytest

from evals.terminal_bench.models import ReleaseAsset, SelectedTask, UpstreamMetadata
from evals.terminal_bench.setup import UPSTREAM_FILE, TerminalBenchSetup, load_upstream

SELECTED = (
    SelectedTask(
        name="interleaved-vigenere",
        digest="sha256:83a4f0074137ab36629d33f3f4045d62b63b0feb83295ff7cb6636cbedd31bc0",
        restrict_agent_network=True,
    ),
    SelectedTask(
        name="html-js-filter",
        digest="sha256:832a5b309edca4f1a7c728da5f1ca530c2712f20a0b7f1db6d1bb6e3171a8866",
        restrict_agent_network=True,
    ),
    SelectedTask(
        name="kv-live-surgery",
        digest="sha256:bb58097aee168627e1eea82a50feaf1e021d502fe18e0c24b4d88e5a88a6f53f",
        restrict_agent_network=False,
    ),
)


def test_upstream_release_and_selected_roster_are_pinned() -> None:
    upstream = load_upstream(UPSTREAM_FILE)

    assert upstream.repository == "harbor-framework/terminal-bench"
    assert upstream.release_tag == "v3.0.0"
    assert upstream.asset == ReleaseAsset(
        url=(
            "https://github.com/harbor-framework/terminal-bench/releases/download/"
            "v3.0.0/tasks.tar.gz"
        ),
        size_bytes=453_742_756,
        sha256="sha256:7b035a9768087cd2727925fea1531845497d40412b0cdeed34a9ec9f16c3ff3e",
    )
    assert upstream.harbor_version == "0.21.0"
    assert upstream.tasks == SELECTED


def test_materializes_only_dataset_and_selected_task_directories(tmp_path: Path) -> None:
    archive = _archive(tmp_path / "tasks.tar.gz")
    setup = TerminalBenchSetup(root=tmp_path / "local", asset=archive)

    setup._verify_asset(archive, _upstream(archive).asset)
    tasks = setup._materialize_tasks(archive, _upstream(archive))

    assert {path.name for path in tasks.iterdir()} == {
        "dataset.toml",
        *(task.name for task in SELECTED),
    }
    assert (tasks / "dataset.toml").read_text() == _dataset_manifest()
    for task in SELECTED:
        assert (tasks / task.name / "instruction.md").read_text() == f"solve {task.name}\n"
    assert not (tasks / "unselected-task").exists()


def test_materializes_the_pinned_single_tasks_wrapper(tmp_path: Path) -> None:
    archive = _archive(tmp_path / "tasks.tar.gz", wrapper="tasks")

    tasks = TerminalBenchSetup(root=tmp_path / "local", asset=archive)._materialize_tasks(
        archive, _upstream(archive)
    )

    assert {path.name for path in tasks.iterdir()} == {
        "dataset.toml",
        *(task.name for task in SELECTED),
    }
    assert (tasks / "dataset.toml").read_text() == _dataset_manifest()
    for task in SELECTED:
        assert (tasks / task.name / "instruction.md").read_text() == f"solve {task.name}\n"
    assert not (tasks / "tasks").exists()
    assert not (tasks / "unselected-task").exists()


def test_materialized_tasks_restrict_the_agent_network(tmp_path: Path) -> None:
    archive = _archive(tmp_path / "tasks.tar.gz")

    tasks = TerminalBenchSetup(root=tmp_path / "local", asset=archive)._materialize_tasks(
        archive, _upstream(archive)
    )

    for task in SELECTED:
        manifest = tomllib.loads((tasks / task.name / "task.toml").read_text())
        expected_agent = {"timeout_sec": 60.0}
        if task.restrict_agent_network:
            expected_agent["network_mode"] = "no-network"
        assert manifest["agent"] == expected_agent
        assert manifest["environment"] == {"cpus": 1}
        assert manifest["verifier"] == {"timeout_sec": 90.0}
        assert ((tasks / task.name / "task.toml").read_text() == TASK_MANIFEST_BODY) == (
            not task.restrict_agent_network
        )


@pytest.mark.parametrize(
    "body",
    (
        '[agent]\nnetwork_mode = "public"\n',
        '[agent]\ntimeout_sec = 60.0\nallowed_hosts = ["example.com"]\n',
        '[agent]\ntimeout_sec = 60.0\n\n[environment]\nnetwork_mode = "public"\n',
    ),
)
def test_rejects_task_declaring_its_own_network_policy(tmp_path: Path, body: str) -> None:
    archive = _archive(tmp_path / "tasks.tar.gz", task_manifest=body)

    with pytest.raises(ValueError, match="declares its own network policy"):
        TerminalBenchSetup(root=tmp_path / "local", asset=archive)._materialize_tasks(
            archive, _upstream(archive)
        )


def test_rejects_task_without_a_manifest(tmp_path: Path) -> None:
    archive = _archive(tmp_path / "tasks.tar.gz", omit_task_manifest=True)

    with pytest.raises(FileNotFoundError, match=r"task\.toml"):
        TerminalBenchSetup(root=tmp_path / "local", asset=archive)._materialize_tasks(
            archive, _upstream(archive)
        )


def test_rejects_task_whose_agent_table_has_no_header_line(tmp_path: Path) -> None:
    archive = _archive(tmp_path / "tasks.tar.gz", task_manifest="agent = { timeout_sec = 60.0 }\n")

    with pytest.raises(ValueError, match=r"0 \[agent\] header lines"):
        TerminalBenchSetup(root=tmp_path / "local", asset=archive)._materialize_tasks(
            archive, _upstream(archive)
        )


def test_materializes_wrapped_archive_with_appledouble_members(tmp_path: Path) -> None:
    archive = _archive(
        tmp_path / "tasks.tar.gz",
        wrapper="tasks",
        appledouble=True,
    )

    tasks = TerminalBenchSetup(root=tmp_path / "local", asset=archive)._materialize_tasks(
        archive, _upstream(archive)
    )

    assert {path.name for path in tasks.iterdir()} == {
        "dataset.toml",
        *(task.name for task in SELECTED),
    }
    assert not any(
        part.startswith("._") for path in tasks.rglob("*") for part in path.relative_to(tasks).parts
    )


def test_rejects_unsafe_appledouble_member(tmp_path: Path) -> None:
    archive = _archive(
        tmp_path / "tasks.tar.gz",
        wrapper="tasks",
        appledouble=True,
        extra_member=("tasks/._link", "symlink"),
    )

    with pytest.raises(ValueError, match="archive links are forbidden"):
        TerminalBenchSetup(root=tmp_path / "local", asset=archive)._materialize_tasks(
            archive, _upstream(archive)
        )


@pytest.mark.parametrize("difference", (-1, 1))
def test_rejects_wrong_archive_size(tmp_path: Path, difference: int) -> None:
    archive = _archive(tmp_path / "tasks.tar.gz")
    upstream = _upstream(archive)
    asset = upstream.asset.model_copy(update={"size_bytes": upstream.asset.size_bytes + difference})

    with pytest.raises(ValueError, match="size"):
        TerminalBenchSetup(root=tmp_path / "local", asset=archive)._verify_asset(archive, asset)


def test_rejects_wrong_archive_digest(tmp_path: Path) -> None:
    archive = _archive(tmp_path / "tasks.tar.gz")
    asset = _upstream(archive).asset.model_copy(update={"sha256": f"sha256:{'0' * 64}"})

    with pytest.raises(ValueError, match="digest"):
        TerminalBenchSetup(root=tmp_path / "local", asset=archive)._verify_asset(archive, asset)


@pytest.mark.parametrize(
    ("name", "kind"),
    (
        ("/absolute", "file"),
        ("../escape", "file"),
        ("interleaved-vigenere/link", "symlink"),
        ("interleaved-vigenere/hard-link", "hardlink"),
    ),
)
def test_rejects_unsafe_archive_members(tmp_path: Path, name: str, kind: str) -> None:
    archive = _archive(tmp_path / "tasks.tar.gz", extra_member=(name, kind))

    with pytest.raises(ValueError, match="archive"):
        TerminalBenchSetup(root=tmp_path / "local", asset=archive)._materialize_tasks(
            archive, _upstream(archive)
        )


def test_rejects_duplicate_archive_members(tmp_path: Path) -> None:
    archive = _archive(
        tmp_path / "tasks.tar.gz",
        extra_member=("html-js-filter/instruction.md", "file"),
    )

    with pytest.raises(ValueError, match="duplicate archive member"):
        TerminalBenchSetup(root=tmp_path / "local", asset=archive)._materialize_tasks(
            archive, _upstream(archive)
        )


def test_rejects_missing_selected_case_root(tmp_path: Path) -> None:
    archive = _archive(tmp_path / "tasks.tar.gz", omitted_case="kv-live-surgery")

    with pytest.raises(ValueError, match=r"missing selected task roots.*kv-live-surgery"):
        TerminalBenchSetup(root=tmp_path / "local", asset=archive)._materialize_tasks(
            archive, _upstream(archive)
        )


def test_rejects_missing_selected_task_in_dataset_manifest(tmp_path: Path) -> None:
    manifest = _dataset_manifest().replace(
        _dataset_entry(SELECTED[1]),
        "",
    )
    archive = _archive(tmp_path / "tasks.tar.gz", manifest=manifest)

    with pytest.raises(ValueError, match=r"missing selected tasks.*html-js-filter"):
        TerminalBenchSetup(root=tmp_path / "local", asset=archive)._materialize_tasks(
            archive, _upstream(archive)
        )


def test_rejects_duplicate_task_id_in_dataset_manifest(tmp_path: Path) -> None:
    manifest = _dataset_manifest() + _dataset_entry(SELECTED[0])
    archive = _archive(tmp_path / "tasks.tar.gz", manifest=manifest)

    with pytest.raises(ValueError, match=r"duplicate task ids.*interleaved-vigenere"):
        TerminalBenchSetup(root=tmp_path / "local", asset=archive)._materialize_tasks(
            archive, _upstream(archive)
        )


def test_dockerignore_bytes_affect_client_source_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup = TerminalBenchSetup()
    dockerignore = setup.repository_root / ".dockerignore"
    dockerignore_body = [b"first context rules\n"]
    path_open = Path.open

    def open_with_dockerignore(path: Path, *args, **kwargs):
        if path == dockerignore:
            return io.BytesIO(dockerignore_body[0])
        return path_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_with_dockerignore)
    first = setup._client_source_digest()
    dockerignore_body[0] = b"second context rules\n"

    assert setup._client_source_digest() != first


def _upstream(archive: Path) -> UpstreamMetadata:
    body = archive.read_bytes()
    return UpstreamMetadata(
        repository="harbor-framework/terminal-bench",
        release_tag="v3.0.0",
        asset=ReleaseAsset(
            url="https://example.invalid/tasks.tar.gz",
            size_bytes=len(body),
            sha256=f"sha256:{hashlib.sha256(body).hexdigest()}",
        ),
        harbor_version="0.21.0",
        tasks=SELECTED,
    )


def _dataset_entry(task: SelectedTask) -> str:
    return f'[[tasks]]\nname = "terminal-bench/{task.name}"\ndigest = "{task.digest}"\n\n'


def _dataset_manifest() -> str:
    return '[dataset]\nname = "terminal-bench/terminal-bench-3"\n\n' + "".join(
        _dataset_entry(task)
        for task in (
            SELECTED[0],
            SelectedTask(
                name="unselected-task",
                digest=f"sha256:{'1' * 64}",
                restrict_agent_network=True,
            ),
            SELECTED[1],
            SELECTED[2],
        )
    )


TASK_MANIFEST_BODY = """[verifier]
timeout_sec = 90.0

[agent]
timeout_sec = 60.0

[environment]
cpus = 1
"""


def _archive(
    path: Path,
    *,
    manifest: str | None = None,
    task_manifest: str | None = None,
    omit_task_manifest: bool = False,
    omitted_case: str | None = None,
    extra_member: tuple[str, str] | None = None,
    wrapper: str | None = None,
    appledouble: bool = False,
) -> Path:
    prefix = f"{wrapper}/" if wrapper is not None else ""
    with tarfile.open(path, "w:gz") as bundle:
        if wrapper is not None:
            directory = tarfile.TarInfo(wrapper)
            directory.type = tarfile.DIRTYPE
            directory.mode = 0o755
            bundle.addfile(directory)
            if appledouble:
                _add_file(bundle, f"._{wrapper}", b"appledouble root\n")
        _add_file(bundle, f"{prefix}dataset.toml", (manifest or _dataset_manifest()).encode())
        for name in (*[task.name for task in SELECTED], "unselected-task"):
            if name == omitted_case:
                continue
            directory = tarfile.TarInfo(f"{prefix}{name}")
            directory.type = tarfile.DIRTYPE
            directory.mode = 0o755
            bundle.addfile(directory)
            if appledouble:
                _add_file(bundle, f"{prefix}._{name}", b"appledouble directory\n")
            _add_file(
                bundle,
                f"{prefix}{name}/instruction.md",
                f"solve {name}\n".encode(),
            )
            if not omit_task_manifest:
                _add_file(
                    bundle,
                    f"{prefix}{name}/task.toml",
                    (task_manifest or TASK_MANIFEST_BODY).encode(),
                )
            if appledouble:
                _add_file(
                    bundle,
                    f"{prefix}{name}/._instruction.md",
                    b"appledouble file\n",
                )
        if extra_member is not None:
            name, kind = extra_member
            if kind == "file":
                _add_file(bundle, name, b"unsafe\n")
            else:
                member = tarfile.TarInfo(name)
                member.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
                member.linkname = "dataset.toml"
                bundle.addfile(member)
    return path


def _add_file(bundle: tarfile.TarFile, name: str, body: bytes) -> None:
    member = tarfile.TarInfo(name)
    member.size = len(body)
    member.mode = 0o644
    bundle.addfile(member, io.BytesIO(body))
