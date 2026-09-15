from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Protocol


class CommandResult(Protocol):
    """The carrier-independent fields returned by a sandbox command."""

    @property
    def stdout(self) -> str: ...

    @property
    def stderr(self) -> str: ...

    @property
    def exit_code(self) -> int: ...


@dataclass(frozen=True)
class SandboxCommands[ResultT]:
    """Build bounded command requests while a caller supplies the sandbox carrier."""

    execute: Callable[[tuple[str, ...], int], Awaitable[ResultT]]
    default_timeout_s: int
    python_flag: str
    supervisor: tuple[str, ...]

    async def bash(self, command: str, timeout_s: int | None = None) -> ResultT:
        """Run one bash command through the sandbox command supervisor."""
        return await self.execute(
            (*self.supervisor, "--", "bash", "-lc", command),
            self.default_timeout_s if timeout_s is None else timeout_s,
        )

    async def bash_task(
        self,
        command: str,
        base: str,
        *,
        detach: bool,
        timeout_s: int | None = None,
    ) -> ResultT:
        """Run or reattach one journaled bash command through the supervisor."""
        flags = ("--detach",) if detach else ()
        return await self.execute(
            (
                *self.supervisor,
                "--task",
                base,
                *flags,
                "--",
                "bash",
                "-lc",
                command,
            ),
            self.default_timeout_s if timeout_s is None else timeout_s,
        )

    async def sh(self, script: str, *args: str, timeout_s: int | None = None) -> ResultT:
        """Run a POSIX script with every argument kept as its own argv element."""
        return await self.execute(
            ("sh", "-c", script, "sh", *args),
            self.default_timeout_s if timeout_s is None else timeout_s,
        )

    async def python(self, program: str, *args: str, timeout_s: int | None = None) -> ResultT:
        """Run an isolated Python program with `args` as its argv."""
        return await self.execute(
            ("python3", self.python_flag, "-c", program, *args),
            self.default_timeout_s if timeout_s is None else timeout_s,
        )


@dataclass(frozen=True)
class SandboxFileOperations[ResultT: CommandResult]:
    """Run the sandbox file protocol and parse its single structured response."""

    execute: Callable[[tuple[str, ...], int], Awaitable[ResultT]]
    default_timeout_s: int
    document_read_timeout_s: int
    document_suffixes: frozenset[str]
    command: tuple[str, ...]
    name: str

    async def run(self, op: str, params: dict[str, object]) -> dict[str, object]:
        """Execute one bounded file operation; handled failures remain caller-visible values."""
        path = params.get("path")
        timeout_s = (
            self.document_read_timeout_s
            if op == "read"
            and isinstance(path, str)
            and PurePosixPath(path).suffix.lower() in self.document_suffixes
            else self.default_timeout_s
        )
        result = await self.execute(
            (
                *self.command,
                op,
                json.dumps(params, separators=(",", ":")),
            ),
            timeout_s,
        )
        stdout = result.stdout.strip()
        if not stdout:
            raise RuntimeError(result.stderr.strip() or f"{self.name} {op} produced no output")
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError(result.stderr.strip() or stdout) from error
        if not isinstance(parsed, dict):
            raise RuntimeError(f"{self.name} {op} did not return a JSON object")
        failure = parsed.get("error")
        if isinstance(failure, str):
            raise ValueError(failure)
        return parsed
