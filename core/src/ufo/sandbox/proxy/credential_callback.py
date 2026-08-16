"""The control-plane credential callback the sandbox cache daemon phones home to.

The daemon holds no credential and can mint nothing. For a git host it needs authentication for, it
POSTs `(workspace_id, user_id, host, repo_path)` here and this resolves the workspace's own git
credential — the same slot the egress proxy injects directly — and returns it with a mirror-scoping
principal. Because the daemon can only ask for the identity the proxy stamped on the request, it can
never obtain another workspace's token: the isolation is the resolution, not a policy.

It runs on the proxy pod beside the daemon, bound to loopback, authenticated by a shared token — a
machine-to-machine surface, never reachable from a sandbox or the public edge. Own-org git today
(the App-backed slot); a per-user PAT slot keyed by `user_id` is the seam that widens it later."""

import asyncio
import json
from dataclasses import dataclass
from uuid import UUID

from ufo.credentials import CredentialStore, credential_host, slot_is_set, slot_secret
from ufo.ext.manifest import CredentialSlot
from ufo.o11y import log, warn
from ufo.workspace import ws

CALLBACK_PATH = "/internal/git-credential"
_MAX_BODY_BYTES = 64 * 1024


@dataclass(frozen=True)
class CredentialCallback:
    """Resolves the git credential and principal for one `(workspace, host)`. `token` is the shared
    secret the daemon presents; a request without it is refused before any resolution."""

    credentials: CredentialStore | None
    slots: tuple[CredentialSlot, ...]
    token: str

    async def serve(self, host: str, port: int) -> asyncio.Server:
        return await asyncio.start_server(self._handle, host, port)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await reader.readline()
            headers: dict[str, str] = {}
            while (line := await reader.readline()) not in (b"\r\n", b""):
                name, _, value = line.decode("latin-1").partition(":")
                headers[name.strip().lower()] = value.strip()
            method, _, rest = request_line.decode("latin-1").partition(" ")
            path = rest.split(" ", 1)[0]
            if method != "POST" or path != CALLBACK_PATH:
                await _respond(writer, 404, {"error": "not found"})
                return
            if headers.get("authorization") != f"Bearer {self.token}":
                await _respond(writer, 401, {"error": "unauthorized"})
                return
            length = int(headers.get("content-length", "0"))
            if length > _MAX_BODY_BYTES:
                await _respond(writer, 413, {"error": "body too large"})
                return
            payload = json.loads(await reader.readexactly(length)) if length else {}
            await _respond(writer, 200, await self._resolve(payload))
        except (OSError, ValueError, asyncio.IncompleteReadError):
            await _respond(writer, 400, {"error": "bad request"})
        finally:
            writer.close()

    async def _resolve(self, payload: dict[str, object]) -> dict[str, object]:
        workspace_raw = payload.get("workspace_id")
        host = payload.get("host")
        if not isinstance(workspace_raw, str) or not isinstance(host, str):
            return {"principal": "public"}
        try:
            workspace_id = UUID(workspace_raw)
        except ValueError:
            return {"principal": "public"}
        with ws(workspace_id):
            credential = await self._git_credential(workspace_id, host)
        if credential is None:
            return {"credential": None, "principal": "public"}
        username, secret = credential
        return {"username": username, "token": secret, "principal": f"w{workspace_id}"}

    async def _git_credential(self, workspace_id: UUID, host: str) -> tuple[str, str] | None:
        """The workspace's git secret for `host`, resolved exactly as the proxy's injection does:
        the `git_basic_user` slot whose stored host matches. A slot that fails to resolve
        contributes nothing — the daemon falls back to anonymous — never another slot's identity."""
        if self.credentials is None:
            return None
        for slot in self.slots:
            target = slot.injection
            if target is None or target.git_basic_user is None:
                continue
            try:
                if not await slot_is_set(slot.name, slot.source, workspace_id, self.credentials):
                    continue
                if await credential_host(self.credentials, workspace_id, target.host) != host:
                    continue
                secret = await slot_secret(slot.name, slot.source, workspace_id, self.credentials)
            except Exception as error:
                warn(
                    "cache.credential_slot_failed",
                    slot=slot.name,
                    error_class=type(error).__name__,
                )
                continue
            if secret is None:
                continue
            log("cache.credential_resolved", slot=slot.name, host=host)
            return target.git_basic_user, secret
        return None


async def _respond(writer: asyncio.StreamWriter, status: int, body: dict[str, object]) -> None:
    payload = json.dumps(body).encode()
    try:
        writer.write(
            f"HTTP/1.1 {status} x\r\ncontent-type: application/json\r\n".encode()
            + b"content-length: "
            + str(len(payload)).encode()
            + b"\r\nconnection: close\r\n\r\n"
            + payload
        )
        await writer.drain()
    except OSError:
        pass
