import asyncio
import os
import shlex
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, override
from urllib.parse import urlsplit
from uuid import uuid4

import httpcore
from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

CLIENT_TARGET = "/installed-agent/ufo"
HOME_TARGET = "/installed-agent/home"
CREDENTIALS_TARGET = f"{HOME_TARGET}/credentials"
ENVIRONMENT_TARGET = f"{HOME_TARGET}/environment.yaml"
POLL_INTERVAL_SECONDS = 5
OUTPUT_TAIL_BYTES = 10_000
TRANSPORT_FAULTS = (httpcore.LocalProtocolError, httpcore.ReadError)
TRANSPORT_ATTEMPTS = 5
TRANSPORT_RETRY_SECONDS = 2


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


@dataclass(frozen=True)
class _ClientExit:
    return_code: int
    stdout: str
    stderr: str


class UfoAgent(BaseInstalledAgent):
    """Install the native client and run one turn inside Harbor's graded environment."""

    _workspace_url: str
    _model: str | None = None
    _environment: str | None = None

    @staticmethod
    @override
    def name() -> str:
        return "ufo"

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        client = await asyncio.to_thread(Path(_required_environment("UFO_BENCH_CLIENT")).resolve)
        token = _required_environment("UFO_BENCH_TOKEN")
        workspace_url = _required_environment("UFO_BENCH_WORKSPACE_URL")
        self._model = os.environ.get("UFO_BENCH_MODEL") or None
        document = os.environ.get("UFO_BENCH_ENVIRONMENT") or None
        parsed = urlsplit(workspace_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("UFO_BENCH_WORKSPACE_URL must be a public HTTPS URL")
        if not await asyncio.to_thread(client.is_file):
            raise FileNotFoundError(f"Terminal-Bench client is missing: {client}")
        if not await asyncio.to_thread(os.access, client, os.X_OK):
            raise PermissionError(f"Terminal-Bench client is not executable: {client}")
        self._workspace_url = workspace_url

        await environment.upload_file(client, CLIENT_TARGET)
        await self.exec_as_root(environment, command=f"mkdir -p {shlex.quote(HOME_TARGET)}")
        descriptor, temporary_name = await asyncio.to_thread(
            tempfile.mkstemp, prefix="ufo-bench-credentials-"
        )
        credentials = Path(temporary_name)
        try:
            await asyncio.to_thread(os.close, descriptor)
            await asyncio.to_thread(credentials.write_text, token, encoding="utf-8")
            await environment.upload_file(credentials, CREDENTIALS_TARGET)
        finally:
            await asyncio.to_thread(credentials.unlink, missing_ok=True)
        if document is not None:
            source = await asyncio.to_thread(Path(document).resolve)
            if not await asyncio.to_thread(source.is_file):
                raise FileNotFoundError(f"Terminal-Bench environment document is missing: {source}")
            await environment.upload_file(source, ENVIRONMENT_TARGET)
            self._environment = ENVIRONMENT_TARGET

        owner = shlex.quote(
            str(environment.default_user) if environment.default_user is not None else "root"
        )
        await self.exec_as_root(
            environment,
            command=(
                f"chown -R {owner} {shlex.quote(HOME_TARGET)} {shlex.quote(CLIENT_TARGET)} && "
                f"chmod 700 {shlex.quote(HOME_TARGET)} && "
                f"chmod 600 {shlex.quote(CREDENTIALS_TARGET)} && "
                f"chmod 755 {shlex.quote(CLIENT_TARGET)}"
            ),
        )
        await self.exec_as_agent(
            environment, command=f"{shlex.quote(CLIENT_TARGET)} --help >/dev/null"
        )

    @override
    @with_prompt_template
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        if environment.context_id is None:
            raise RuntimeError("Harbor environment context_id is required")
        channel = f"{environment.context_id.hex}-{uuid4().hex[:12]}"
        context.metadata = {
            "channel": channel,
            "client_target": CLIENT_TARGET,
        }
        command = [CLIENT_TARGET]
        if self._model is not None:
            command.extend(("--model", self._model))
        if self._environment is not None:
            command.extend(("--environment", self._environment))
        command.extend(("--json", instruction))
        client = f"{shlex.join(command)} </dev/null"
        stdout_target = f"{HOME_TARGET}/{channel}.out"
        stderr_target = f"{HOME_TARGET}/{channel}.err"
        exit_target = f"{HOME_TARGET}/{channel}.exit"
        try:
            await self.exec_as_agent(
                environment,
                command=(
                    f"nohup sh -c {shlex.quote(f'{client}; echo $? >{exit_target}')} "
                    f"</dev/null >{stdout_target} 2>{stderr_target} &"
                ),
                env={
                    "UFO_HOME": HOME_TARGET,
                    "WORKSPACE_URL": self._workspace_url,
                    "UFO_CHANNEL": channel,
                },
            )
        except TRANSPORT_FAULTS:
            if not await self._client_started(environment, exit_target, stdout_target):
                raise
        exit_code = await self._recorded_exit_code(environment, exit_target)
        if exit_code != 0:
            raise self._classify_exec_error(
                client,
                _ClientExit(
                    return_code=exit_code,
                    stdout=await self._output_tail(environment, stdout_target),
                    stderr=await self._output_tail(environment, stderr_target),
                ),
            )

    async def _client_started(
        self, environment: BaseEnvironment, exit_target: str, stdout_target: str
    ) -> bool:
        probe = await self._idempotent_exec(
            environment,
            command=f"if [ -e {exit_target} ] || [ -e {stdout_target} ]; then echo started; fi",
        )
        return bool((probe.stdout or "").strip())

    async def _recorded_exit_code(self, environment: BaseEnvironment, exit_target: str) -> int:
        while True:
            result = await self._idempotent_exec(
                environment, command=f"cat {exit_target} 2>/dev/null || true"
            )
            recorded = (result.stdout or "").strip()
            if recorded:
                return int(recorded)
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    async def _output_tail(self, environment: BaseEnvironment, target: str) -> str:
        result = await self._idempotent_exec(
            environment, command=f"tail -c {OUTPUT_TAIL_BYTES} {target} 2>/dev/null || true"
        )
        return result.stdout or ""

    async def _idempotent_exec(self, environment: BaseEnvironment, command: str) -> Any:
        for _ in range(TRANSPORT_ATTEMPTS - 1):
            try:
                return await self.exec_as_agent(environment, command=command)
            except TRANSPORT_FAULTS:
                await asyncio.sleep(TRANSPORT_RETRY_SECONDS)
        return await self.exec_as_agent(environment, command=command)
