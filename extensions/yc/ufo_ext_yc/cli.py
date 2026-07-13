"""The authenticated YC CLI boundary used by YC tools and content sources."""

import asyncio
import hashlib
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, Field, HttpUrl, model_validator

from ufo.sdk.context import CredentialAccess, CredentialSlotUnset
from ufo.sdk.tools import TextContent, ToolContext, ToolResult

YC_CREDENTIALS_SLOT = "yc_cli_credentials"
YC_CREDENTIALS_PATH = Path(".yc/credentials.json")
YC_CLI_EXECUTABLE = "yc"
YC_CLI_OUTPUT_MAX_BYTES = 32 * 1024 * 1024
YC_CLI_ERROR_MAX_BYTES = 64 * 1024
YC_CLI_TIMEOUT_SECONDS = 120
STREAM_CHUNK_BYTES = 64 * 1024
YC_AUTH_BASE_URL = "https://account.ycombinator.com"
YC_AUTH_CLIENT_ID = "Dw5mFzLA8iWT8R2_k6nTcNZmB_4hha11Zt_2PFSKwJU"
YC_AUTH_SCOPES = ("profile:read", "search:read", "agent:query", "skills:read")
YC_AUTH_PENDING_KEY = "device_authorization"
YC_AUTH_TIMEOUT_SECONDS = 15
YC_AUTH_RESPONSE_MAX_BYTES = 64 * 1024
YC_CLI_VERSION = "0.0.18"


class YcCliError(RuntimeError):
    """The YC CLI was missing, timed out, failed, or exceeded an output bound."""


class YcCredentials(BaseModel):
    """The boundary record the YC CLI persists in `~/.yc/credentials.json`."""

    access_token: str
    refresh_token: str
    token_type: str
    expires_in: float
    scope: str
    created_at: float


class YcDeviceAuthorization(BaseModel):
    device_code: str = Field(min_length=1, max_length=4096)
    user_code: str = Field(min_length=1, max_length=128)
    verification_uri: HttpUrl
    verification_uri_complete: HttpUrl | None = None
    expires_in: int = Field(gt=0, le=3600)
    interval: int = Field(default=5, ge=1, le=60)

    @model_validator(mode="after")
    def validate_verification_url(self) -> "YcDeviceAuthorization":
        url = self.verification_uri_complete or self.verification_uri
        if url.host != "account.ycombinator.com":
            raise ValueError("YC device authorization returned another verification host")
        return self


class YcPendingAuthorization(BaseModel):
    device_code: str
    user_code: str
    verification_url: str
    expires_at: float


class YcStoredAuthorization(BaseModel):
    idempotency_key: str
    sealed: str
    credential_digest: str | None


class YcAuthResult(BaseModel):
    status: Literal["authorization_required", "pending", "connected"]
    verification_url: str | None = None
    user_code: str | None = None


class YcAuthInput(BaseModel):
    action: Literal["start", "complete"]
    user_description: str | None = Field(
        default=None, description="Brief plain-language description shown in the activity timeline."
    )


class YcRunner(Protocol):
    async def run(self, args: tuple[str, ...], session: str) -> str: ...


@dataclass(frozen=True)
class YcAuth:
    ctx: ToolContext
    http: httpx.AsyncClient

    async def run(self, action: Literal["start", "complete"], session: str) -> YcAuthResult:
        if self.ctx.ext is None:
            raise RuntimeError("yc_auth dispatched without the YC extension context")
        if self.ctx.audience_member_id != self.ctx.speaker_member_id:
            raise ValueError("YC authorization requires the speaker's private audience")
        if self.ctx.requestable_credentials is None:
            raise ValueError("no credential key is configured — this deploy cannot store secrets")
        if YC_CREDENTIALS_SLOT not in self.ctx.ext.credentials.declared:
            raise ValueError("the YC extension does not declare its credential slot")
        if not await self.ctx.speaker_is_owner():
            raise ValueError("only the workspace owner can authorize YC")
        match action:
            case "start":
                return await self._start(session)
            case "complete":
                return await self._complete(session)

    async def _start(self, session: str) -> YcAuthResult:
        if self.ctx.ext is None:
            raise RuntimeError("yc_auth dispatched without the YC extension context")
        idempotency_key = self.ctx.idempotency_key or str(self.ctx.turn.id)
        current = await self.ctx.ext.store.get(YC_AUTH_PENDING_KEY)
        if isinstance(current, dict):
            previous = YcStoredAuthorization.model_validate(current)
            if previous.idempotency_key == idempotency_key:
                credential_digest = await self._credential_digest()
                if (
                    credential_digest is not None
                    and credential_digest != previous.credential_digest
                ):
                    await self.ctx.ext.store.delete(YC_AUTH_PENDING_KEY)
                    return YcAuthResult(status="connected")
                pending = YcPendingAuthorization.model_validate_json(
                    await self.ctx.open_credential_authorization(
                        YC_CREDENTIALS_SLOT, previous.sealed
                    )
                )
                return YcAuthResult(
                    status="authorization_required",
                    verification_url=pending.verification_url,
                    user_code=pending.user_code,
                )
        credential_digest = await self._credential_digest()
        response = await self.http.post(
            f"{YC_AUTH_BASE_URL}/oauth/authorize_device",
            headers=self._headers(session),
            json={"client_id": YC_AUTH_CLIENT_ID, "scope": " ".join(YC_AUTH_SCOPES)},
        )
        self._bound(response)
        if response.is_error:
            raise YcCliError(f"YC device authorization failed ({response.status_code})")
        authorization = YcDeviceAuthorization.model_validate_json(response.content)
        pending = YcPendingAuthorization(
            device_code=authorization.device_code,
            user_code=authorization.user_code,
            verification_url=(
                str(authorization.verification_uri_complete or authorization.verification_uri)
            ),
            expires_at=time.time() + authorization.expires_in,
        )
        sealed = await self.ctx.begin_credential_authorization(
            YC_CREDENTIALS_SLOT, pending.model_dump_json()
        )
        await self.ctx.ext.store.put(
            YC_AUTH_PENDING_KEY,
            YcStoredAuthorization(
                idempotency_key=idempotency_key,
                sealed=sealed,
                credential_digest=credential_digest,
            ).model_dump(mode="json"),
        )
        return YcAuthResult(
            status="authorization_required",
            verification_url=pending.verification_url,
            user_code=pending.user_code,
        )

    async def _complete(self, session: str) -> YcAuthResult:
        if self.ctx.ext is None:
            raise RuntimeError("yc_auth dispatched without the YC extension context")
        stored = await self.ctx.ext.store.get(YC_AUTH_PENDING_KEY)
        if not isinstance(stored, dict):
            try:
                YcCredentials.model_validate_json(
                    await self.ctx.ext.credentials.get(YC_CREDENTIALS_SLOT)
                )
            except (KeyError, ValueError):
                raise YcCliError("no YC device authorization is pending; start one first") from None
            return YcAuthResult(status="connected")
        authorization = YcStoredAuthorization.model_validate(stored)
        credential_digest = await self._credential_digest()
        if credential_digest is not None and credential_digest != authorization.credential_digest:
            await self.ctx.ext.store.delete(YC_AUTH_PENDING_KEY)
            return YcAuthResult(status="connected")
        sealed = authorization.sealed
        pending = YcPendingAuthorization.model_validate_json(
            await self.ctx.open_credential_authorization(YC_CREDENTIALS_SLOT, sealed)
        )
        if time.time() >= pending.expires_at:
            await self.ctx.ext.store.delete(YC_AUTH_PENDING_KEY)
            raise YcCliError("YC device authorization expired; start again")
        response = await self.http.post(
            f"{YC_AUTH_BASE_URL}/oauth/token",
            headers=self._headers(session),
            json={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": pending.device_code,
                "client_id": YC_AUTH_CLIENT_ID,
            },
        )
        self._bound(response)
        if response.is_success:
            credentials = YcCredentials.model_validate_json(response.content)
            await self.ctx.fulfill_credential_authorization(
                YC_CREDENTIALS_SLOT, sealed, credentials.model_dump_json()
            )
            await self.ctx.ext.store.delete(YC_AUTH_PENDING_KEY)
            return YcAuthResult(status="connected")
        error = json.loads(response.content)
        code = error.get("error") if isinstance(error, dict) else None
        if code in ("authorization_pending", "slow_down"):
            return YcAuthResult(
                status="pending",
                verification_url=pending.verification_url,
                user_code=pending.user_code,
            )
        await self.ctx.ext.store.delete(YC_AUTH_PENDING_KEY)
        if code == "expired_token":
            raise YcCliError("YC device authorization expired; start again")
        raise YcCliError(f"YC device token exchange failed ({response.status_code})")

    async def _credential_digest(self) -> str | None:
        if self.ctx.ext is None:
            raise RuntimeError("yc_auth dispatched without the YC extension context")
        try:
            raw = await self.ctx.ext.credentials.get(YC_CREDENTIALS_SLOT)
        except CredentialSlotUnset:
            return None
        YcCredentials.model_validate_json(raw)
        return hashlib.sha256(raw.encode()).hexdigest()

    def _headers(self, session: str) -> dict[str, str]:
        return {
            "User-Agent": f"yc-cli/{YC_CLI_VERSION}",
            "X-Yc-Cli-Version": YC_CLI_VERSION,
            "X-Yc-Cli-Session": session,
        }

    def _bound(self, response: httpx.Response) -> None:
        if len(response.content) > YC_AUTH_RESPONSE_MAX_BYTES:
            raise YcCliError(f"YC auth response exceeds {YC_AUTH_RESPONSE_MAX_BYTES} bytes")


async def _read_bounded(stream: asyncio.StreamReader, limit: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while chunk := await stream.read(STREAM_CHUNK_BYTES):
        size += len(chunk)
        if size > limit:
            raise YcCliError(f"YC CLI output exceeds {limit} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


@dataclass(frozen=True)
class YcCli:
    credentials: CredentialAccess
    executable: str = YC_CLI_EXECUTABLE

    async def run(self, args: tuple[str, ...], session: str) -> str:
        raw = await self.credentials.get(YC_CREDENTIALS_SLOT)
        YcCredentials.model_validate_json(raw)
        home = await asyncio.to_thread(self._prepare_home, raw)
        try:
            try:
                return await self._execute(home, args, session)
            finally:
                await self._persist_refresh(home, raw)
        finally:
            await asyncio.to_thread(shutil.rmtree, home)

    def _prepare_home(self, raw: str) -> Path:
        home = Path(tempfile.mkdtemp(prefix="ufo-yc-"))
        home.chmod(0o700)
        path = home / YC_CREDENTIALS_PATH
        path.parent.mkdir(mode=0o700)
        path.write_text(raw, encoding="utf-8")
        path.chmod(0o600)
        return home

    async def _execute(self, home: Path, args: tuple[str, ...], session: str) -> str:
        env = {
            "HOME": str(home),
            "PATH": os.environ.get("PATH", os.defpath),
            "YC_CLI_SESSION": session,
        }
        try:
            process = await asyncio.create_subprocess_exec(
                self.executable,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError as error:
            raise YcCliError(
                "YC CLI is not installed; install it in the ufo host image from "
                "https://bookface.ycombinator.com/cli/install.sh"
            ) from error
        if process.stdout is None or process.stderr is None:
            raise YcCliError("YC CLI subprocess has no output pipes")
        stdout = asyncio.create_task(_read_bounded(process.stdout, YC_CLI_OUTPUT_MAX_BYTES))
        stderr = asyncio.create_task(_read_bounded(process.stderr, YC_CLI_ERROR_MAX_BYTES))
        try:
            async with asyncio.timeout(YC_CLI_TIMEOUT_SECONDS):
                streams = await asyncio.gather(stdout, stderr)
                stdout_output = streams[0]
                stderr_output = streams[1]
                returncode = await process.wait()
        except (TimeoutError, YcCliError, asyncio.CancelledError):
            process.kill()
            await process.wait()
            stdout.cancel()
            stderr.cancel()
            raise
        if returncode != 0:
            message = (
                stderr_output.decode("utf-8", "replace").strip()
                or stdout_output.decode("utf-8", "replace").strip()
            )
            raise YcCliError(message or f"YC CLI exited with status {returncode}")
        return stdout_output.decode("utf-8", "replace")

    async def _persist_refresh(self, home: Path, expected: str) -> None:
        refreshed = await asyncio.to_thread(
            (home / YC_CREDENTIALS_PATH).read_text, encoding="utf-8"
        )
        YcCredentials.model_validate_json(refreshed)
        if refreshed == expected:
            return
        if await self.credentials.rotate(YC_CREDENTIALS_SLOT, expected, refreshed):
            return
        current = await self.credentials.get(YC_CREDENTIALS_SLOT)
        if current == expected:
            return
        refreshed_at = YcCredentials.model_validate_json(refreshed).created_at
        while refreshed_at > YcCredentials.model_validate_json(current).created_at:
            if await self.credentials.rotate(YC_CREDENTIALS_SLOT, current, refreshed):
                return
            latest = await self.credentials.get(YC_CREDENTIALS_SLOT)
            if latest == current:
                raise YcCliError("YC credential refresh could not be reconciled")
            current = latest


class YcReadInput(BaseModel):
    action: Literal["ask", "search", "skills_list", "skills_read", "tools_context"]
    query: str | None = Field(default=None, max_length=10_000)
    entity: str | None = Field(default=None, max_length=64)
    name: str | None = Field(default=None, max_length=128)
    user_description: str | None = Field(
        default=None, description="Brief plain-language description shown in the activity timeline."
    )

    @model_validator(mode="after")
    def validate_action(self) -> "YcReadInput":
        if self.action in ("ask", "search") and not self.query:
            raise ValueError(f"{self.action} requires query")
        if self.action == "skills_read" and not self.name:
            raise ValueError("skills_read requires name")
        if self.action != "search" and self.entity is not None:
            raise ValueError("entity is valid only for search")
        return self


@dataclass(frozen=True)
class YcRead:
    runner: YcRunner

    async def run(self, args: YcReadInput, session: str) -> str:
        command: tuple[str, ...]
        match args.action:
            case "ask":
                command = ("agent", args.query or "", "--json")
            case "search":
                command = ("search", args.query or "", "--json")
                if args.entity is not None:
                    command = (*command, "--type", args.entity)
            case "skills_list":
                command = ("skills", "list", "--json")
            case "skills_read":
                command = ("skills", "read", args.name or "")
            case "tools_context":
                command = ("tools", "context")
        return await self.runner.run(command, session)


async def yc_read(ctx: ToolContext, args: YcReadInput) -> ToolResult:
    if ctx.ext is None:
        raise RuntimeError("yc_read dispatched without the YC extension context")
    output = await YcRead(YcCli(ctx.ext.credentials)).run(
        args, session=f"ufo-conversation-{ctx.turn.conversation_id}"
    )
    return ToolResult(content=(TextContent(text=output),))


async def yc_auth(ctx: ToolContext, args: YcAuthInput) -> ToolResult:
    if ctx.ext is None:
        raise RuntimeError("yc_auth dispatched without the YC extension context")
    async with httpx.AsyncClient(timeout=YC_AUTH_TIMEOUT_SECONDS) as http:
        result = await YcAuth(ctx, http).run(
            args.action, session=f"ufo-conversation-{ctx.turn.conversation_id}"
        )
    return ToolResult(content=(TextContent(text=result.model_dump_json(exclude_none=True)),))
