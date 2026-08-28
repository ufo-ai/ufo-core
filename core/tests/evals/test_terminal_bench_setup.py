import io
from pathlib import Path

import pytest

from evals.terminal_bench.models import UpstreamMetadata
from evals.terminal_bench.setup import UPSTREAM_FILE, TerminalBenchSetup, load_upstream

DATASET_DIGEST = "sha256:7d7bdc1cbedad549fc1140404bd4dc45e5fd0ea7c4186773687d177ad3a0699a"


def test_upstream_dataset_and_complete_roster_are_pinned() -> None:
    upstream = load_upstream(UPSTREAM_FILE)

    assert upstream.repository == "harbor-framework/terminal-bench-2-1"
    assert upstream.dataset == "terminal-bench/terminal-bench-2-1"
    assert upstream.revision == 6
    assert upstream.digest == DATASET_DIGEST
    assert upstream.harbor_version == "0.21.0"
    assert len(upstream.tasks) == 89
    assert upstream.tasks[:3] == (
        "write-compressor",
        "torch-tensor-parallelism",
        "schemelike-metacircular-eval",
    )
    assert upstream.tasks[-3:] == (
        "tune-mjcf",
        "fix-code-vulnerability",
        "portfolio-optimization",
    )


def test_duplicate_task_ids_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate selected task ids: duplicate"):
        UpstreamMetadata(
            repository="harbor-framework/terminal-bench-2-1",
            dataset="terminal-bench/terminal-bench-2-1",
            revision=6,
            digest=DATASET_DIGEST,
            harbor_version="0.21.0",
            tasks=("duplicate", "duplicate"),
        )


def test_setup_reports_the_dataset_and_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup = TerminalBenchSetup(root=tmp_path)
    client = tmp_path / "bin/x86_64/ufo"
    monkeypatch.setattr(TerminalBenchSetup, "_client_source_digest", lambda _self: "source")
    monkeypatch.setattr(
        TerminalBenchSetup,
        "_build_client",
        lambda _self, _folder, _platform, _digest: client,
    )

    setup.run()

    assert capsys.readouterr().out.splitlines() == [
        f"terminal-bench/terminal-bench-2-1@{DATASET_DIGEST} revision 6",
        "89 tasks",
        str(client),
    ]


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


def test_client_dockerfile_embeds_the_release_gh_payload() -> None:
    dockerfile = (
        TerminalBenchSetup().repository_root / "evals/terminal_bench/client.Dockerfile"
    ).read_text()

    assert "golang:1.27.0-bookworm AS gh" in dockerfile
    assert 'client/scripts/build-gh.sh "$target" /ufo-gh.gz' in dockerfile
    assert "UFO_GH_ARCHIVE=/ufo-gh.gz" in dockerfile
