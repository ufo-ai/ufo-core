"""Two isolated stacks on one machine, with real `ufoctl init` and `ufoctl serve` subprocesses —
the collision surface (ports, databases, blob roots, CLI token dirs) is the thing under test."""

import asyncio
import os
from pathlib import Path

import pytest
from httpx import AsyncClient

from evals.harness.viewer import load_runs
from evals.stack import EvalStack, RunSpec, StackResult
from evals.stack import main as stack_main

TEMPLATE = """\
[database]
url = "sqlite+aiosqlite:///ufo.db"

[blob]
backend = "filesystem"
root = "./blobs"

[connect]
public_base_url = "http://evals.invalid"

[pack]
name = "assistant"

[research]
search_provider = "perplexity"
"""


def _stack(tmp_path: Path, label: str, args: tuple[str, ...] = ()) -> EvalStack:
    template = tmp_path / "template.toml"
    if not template.exists():
        template.write_text(TEMPLATE)
    return EvalStack.provision(
        RunSpec(label=label, config=template, args=args),
        root=tmp_path / "stamp" / label,
        out=tmp_path / "archive",
        repo_root=Path.cwd(),
    )


def test_two_stacks_serve_simultaneously_without_collisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-stack")
    home_token = Path.home() / ".ufoctl" / "token"
    home_before = home_token.read_text() if home_token.exists() else None
    first, second = _stack(tmp_path, "first"), _stack(tmp_path, "second")
    assert first.config.serve.port != second.config.serve.port
    assert first.config.sandbox.proxy_port != second.config.sandbox.proxy_port

    async def both_serve_at_once() -> list[int]:
        serves: list[tuple[EvalStack, asyncio.subprocess.Process]] = []
        try:
            await asyncio.gather(first._seed(), second._seed())
            for stack in (first, second):
                serves.append((stack, await stack._start_serve()))
            await asyncio.gather(*(stack._ready(serve) for stack, serve in serves))
            async with AsyncClient() as client:
                statuses = await asyncio.gather(
                    *(
                        client.get(f"http://127.0.0.1:{stack.config.serve.port}/openapi.json")
                        for stack, _ in serves
                    )
                )
            return [response.status_code for response in statuses]
        finally:
            for stack, serve in serves:
                await stack._shutdown(serve)

    try:
        assert asyncio.run(both_serve_at_once()) == [200, 200]
    finally:
        for stack in (first, second):
            for log in (stack.seed_log, stack.serve_log, stack.eval_log):
                log.close()

    for stack in (first, second):
        assert (stack.root / "ufo.db").exists()
        assert (stack.root / ".ufoctl" / "token").read_text()
    home_after = home_token.read_text() if home_token.exists() else None
    assert home_after == home_before


def test_stack_run_propagates_the_child_exit_and_tears_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-stack")
    stack = _stack(tmp_path, "lifecycle", args=("--only", "no_such_suite"))

    async def run_stack() -> "StackResult":
        return await stack.run(asyncio.Lock())

    result = asyncio.run(run_stack())

    assert not result.passed
    assert result.exit_code == 2
    assert "no_such_suite" in (stack.root / "eval.log").read_text()
    assert (stack.root / "serve.log").stat().st_size > 0


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY for live eval turns"
)
def test_matrix_records_two_live_runs_into_one_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    template = tmp_path / "template.toml"
    template.write_text(TEMPLATE)
    matrix = tmp_path / "matrix.toml"
    matrix.write_text(
        f"""\
[[run]]
label = "smoke-a"
config = "{template}"
args = ["--only", "basics"]

[[run]]
label = "smoke-b"
config = "{template}"
args = ["--only", "tool_calling"]
"""
    )

    try:
        stack_main([str(matrix), "--out", str(tmp_path / "archive")])
    except SystemExit:
        pass

    runs = load_runs(tmp_path / "archive")
    assert {run.label for run in runs} == {"smoke-a", "smoke-b"}, "\n\n".join(
        path.read_text() for path in (tmp_path / ".local").rglob("*.log") if path.stat().st_size
    )
    assert (tmp_path / "archive" / "index.html").exists()
