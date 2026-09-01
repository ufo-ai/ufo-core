from dataclasses import dataclass, field

import pytest

from ufo.harness.sandbox.protocol import SandboxCommands, SandboxFileOperations


@dataclass(frozen=True)
class Result:
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0


@dataclass
class Executor:
    results: list[Result] = field(default_factory=list)
    calls: list[tuple[tuple[str, ...], int]] = field(default_factory=list)

    async def __call__(self, argv: tuple[str, ...], timeout_s: int) -> Result:
        self.calls.append((argv, timeout_s))
        return self.results.pop(0) if self.results else Result()


@pytest.mark.asyncio
async def test_command_protocol_builds_supervised_and_isolated_argv() -> None:
    execute = Executor()
    commands = SandboxCommands(
        execute=execute,
        default_timeout_s=30,
        python_flag="-I",
        python_bootstrap="guard\n",
        supervisor=("runner",),
    )

    await commands.bash("pwd")
    await commands.bash_task("build", "/runtime/task", detach=True, timeout_s=60)
    await commands.sh('printf %s "$1"', "value")
    await commands.python("print('ok')", "arg")

    assert execute.calls == [
        (("runner", "--", "bash", "-lc", "pwd"), 30),
        (
            (
                "runner",
                "--task",
                "/runtime/task",
                "--detach",
                "--",
                "bash",
                "-lc",
                "build",
            ),
            60,
        ),
        (("sh", "-c", 'printf %s "$1"', "sh", "value"), 30),
        (("python3", "-I", "-c", "guard\nprint('ok')", "arg"), 30),
    ]


@pytest.mark.asyncio
async def test_file_protocol_uses_document_timeout_and_separates_failures() -> None:
    execute = Executor(results=[Result(stdout='{"content":"page"}')])
    files = SandboxFileOperations(
        execute=execute,
        default_timeout_s=30,
        document_read_timeout_s=120,
        document_suffixes=frozenset({".pdf"}),
        command=("files",),
        name="files",
    )

    result = await files.run("read", {"path": "/workspace/report.pdf", "offset": 0})

    assert result == {"content": "page"}
    argv, timeout_s = execute.calls[0]
    assert argv[-2:] == ("read", '{"path":"/workspace/report.pdf","offset":0}')
    assert timeout_s == 120

    handled = Executor(results=[Result(stdout='{"error":"path denied"}')])
    broken = Executor(results=[Result(stdout="not json", stderr="decoder failed")])
    values = {
        "default_timeout_s": 30,
        "document_read_timeout_s": 120,
        "document_suffixes": frozenset({".pdf"}),
        "command": ("files",),
        "name": "files",
    }

    with pytest.raises(ValueError, match="path denied"):
        await SandboxFileOperations(execute=handled, **values).run("read", {"path": "a.txt"})
    with pytest.raises(RuntimeError, match="decoder failed"):
        await SandboxFileOperations(execute=broken, **values).run("glob", {"path": "*"})
