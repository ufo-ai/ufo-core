"""The E2B carrier extension: a per-conversation cloud sandbox reached through the e2b SDK.

Docker is core's default carrier; this extension registers `e2b` on the `carriers` Manifest point,
so a deploy that sets `[sandbox] backend = "e2b"` runs its sandboxes on E2B without core naming the
provider. The e2b SDK is synchronous, so every provider call crosses `asyncio.to_thread` — the one
blocking boundary tolerated, kept to the named SDK callable it wraps. `create` opens a fresh sandbox
on the deploy's template or resumes the conversation's live one; `exec` runs a command through
`commands.run`; `export` reads a produced file out with `files.read`; `destroy` pauses the sandbox
so its next turn resumes cheaply. The container stays a disposable cache over the durable workspace.

Egress for a remote sandbox needs a publicly-reachable proxy URL the sandbox dials back through;
that route, the durable conversation→sandbox map that survives a process restart (so the reaper can
reach a sandbox created by a prior process rather than leaving it to the provider's own idle-pause),
and workspace materialization are the e2b depth this carrier is built to carry but does not yet
wire."""

import asyncio
import base64
import os
import shlex
from dataclasses import dataclass, field
from typing import Literal, Protocol, cast
from uuid import UUID

from e2b import Sandbox as E2BSdkSandbox
from e2b.exceptions import TimeoutException
from e2b.sandbox.commands.command_handle import CommandExitException
from e2b.sandbox.sandbox_api import SandboxLifecycle

from selfhost.sdk.manifest import Manifest
from selfhost.sdk.sandbox import (
    WORKSPACE_DIR,
    BlobStore,
    CarrierSpec,
    ExecResult,
    SandboxHandle,
    SandboxSpec,
)

CARRIER_NAME = "e2b"
E2B_API_KEY_ENVS = ("E2B_API_KEY", "SELFHOST_E2B_API_KEY")
E2B_TEMPLATE_ENV = "SELFHOST_E2B_TEMPLATE"
DEFAULT_TIMEOUT_SECONDS = 300
EXEC_TIMEOUT_CODE = 124
READ_FORMAT: Literal["bytes"] = "bytes"
CONVERSATION_METADATA_KEY = "selfhost.conversation_id"
E2B_LIFECYCLE: SandboxLifecycle = {"on_timeout": "pause", "auto_resume": True}


class E2BCommandResult(Protocol):
    stdout: str
    stderr: str
    exit_code: int


class E2BCommands(Protocol):
    def run(
        self,
        cmd: str,
        *,
        cwd: str | None = None,
        envs: dict[str, str] | None = None,
        user: str | None = None,
        timeout: float | None = None,
    ) -> E2BCommandResult: ...


class E2BFiles(Protocol):
    def read(self, path: str, format: Literal["bytes"]) -> bytes: ...

    def make_dir(self, path: str, *, user: str | None = None) -> bool: ...


class E2BSandbox(Protocol):
    sandbox_id: str
    commands: E2BCommands
    files: E2BFiles

    def pause(self, **opts: object) -> bool: ...


class E2BSdk(Protocol):
    def create(
        self,
        *,
        template: str,
        timeout: int,
        metadata: dict[str, str],
        lifecycle: SandboxLifecycle,
        api_key: str,
    ) -> E2BSandbox: ...

    def connect(self, sandbox_id: str, *, timeout: int, api_key: str) -> E2BSandbox: ...


E2B_SDK = cast(E2BSdk, E2BSdkSandbox)


@dataclass(frozen=True)
class E2BCarrier:
    api_key: str
    template: str
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    sdk: E2BSdk = E2B_SDK
    _live: dict[UUID, E2BSandbox] = field(default_factory=dict)

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        live = self._live.get(spec.conversation_id)
        if live is not None:
            sandbox = await asyncio.to_thread(
                self.sdk.connect,
                live.sandbox_id,
                timeout=self.timeout_seconds,
                api_key=self.api_key,
            )
        else:
            sandbox = await asyncio.to_thread(
                self.sdk.create,
                template=self.template,
                timeout=self.timeout_seconds,
                metadata={CONVERSATION_METADATA_KEY: str(spec.conversation_id)},
                lifecycle=E2B_LIFECYCLE,
                api_key=self.api_key,
            )
            await asyncio.to_thread(sandbox.files.make_dir, WORKSPACE_DIR)
        self._live[spec.conversation_id] = sandbox
        return SandboxHandle(
            conversation_id=spec.conversation_id, container_id=sandbox.sandbox_id, mount=spec.mount
        )

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], stdin: bytes, timeout_s: int
    ) -> ExecResult:
        """Run one command through the sandbox's synchronous `commands.run`. e2b takes a shell
        string with no stdin channel, so argv is quoted into one command and any stdin rides in
        base64 through the sandbox's own `base64 -d` — the write path the file tools depend on. A
        non-zero exit and a command timeout arrive as SDK exceptions, mapped to the ExecResult the
        session reads exactly as the shell's own exit code would."""
        sandbox = await self._sandbox(handle)
        command = shlex.join(argv)
        if stdin:
            payload = shlex.quote(base64.b64encode(stdin).decode())
            command = f"printf %s {payload} | base64 -d | {command}"
        try:
            result = await asyncio.to_thread(
                sandbox.commands.run, command, cwd=WORKSPACE_DIR, timeout=timeout_s
            )
        except CommandExitException as error:
            return ExecResult(stdout=error.stdout, stderr=error.stderr, exit_code=error.exit_code)
        except TimeoutException as error:
            return ExecResult(stdout="", stderr=str(error), exit_code=EXEC_TIMEOUT_CODE)
        return ExecResult(stdout=result.stdout, stderr=result.stderr, exit_code=result.exit_code)

    async def export(self, handle: SandboxHandle, path: str, blob: BlobStore, key: str) -> None:
        """Read the produced file out of the remote sandbox and hand it to the blob store. The sync
        SDK returns the whole file, so this is a single bounded read, not the streaming copy the
        bind-mounted Docker carrier does — the remote provider has no filesystem the host shares."""
        sandbox = await self._sandbox(handle)
        data = await asyncio.to_thread(sandbox.files.read, path, READ_FORMAT)
        await blob.put(key, data)

    async def destroy(self, handle: SandboxHandle) -> None:
        """Pause and drop the conversation's sandbox. The idle reaper reaps by conversation identity
        with no container id, so a conversation this process still holds is paused, and one it never
        held (already gone, or created before a restart) is a no-op that never raises — the
        provider's own idle-pause reclaims a sandbox this process can no longer address."""
        live = self._live.pop(handle.conversation_id, None)
        if live is not None:
            await asyncio.to_thread(live.pause, api_key=self.api_key)

    async def _sandbox(self, handle: SandboxHandle) -> E2BSandbox:
        live = self._live.get(handle.conversation_id)
        if live is not None:
            return live
        sandbox = await asyncio.to_thread(
            self.sdk.connect,
            handle.container_id,
            timeout=self.timeout_seconds,
            api_key=self.api_key,
        )
        self._live[handle.conversation_id] = sandbox
        return sandbox


def build_e2b_carrier() -> E2BCarrier:
    """Build the carrier the `[sandbox] backend = "e2b"` deploy selects: the template and API key
    come from the environment (`SELFHOST_E2B_TEMPLATE`, and `E2B_API_KEY` / `SELFHOST_E2B_API_KEY`).
    An e2b backend with either unset fails loud at boot rather than on the first turn. `serve` calls
    this once, only when the deploy selects `e2b`."""
    template = os.environ.get(E2B_TEMPLATE_ENV)
    if not template:
        raise RuntimeError(f"e2b carrier selected but {E2B_TEMPLATE_ENV} is not set")
    key = next((os.environ[name] for name in E2B_API_KEY_ENVS if os.environ.get(name)), None)
    if not key:
        raise RuntimeError(
            f"e2b carrier selected but none of {E2B_API_KEY_ENVS} is set in the environment"
        )
    return E2BCarrier(api_key=key, template=template)


def manifest() -> Manifest:
    return Manifest(
        name=CARRIER_NAME,
        version="0.1.0",
        carriers=(CarrierSpec(name=CARRIER_NAME, factory=build_e2b_carrier),),
    )
