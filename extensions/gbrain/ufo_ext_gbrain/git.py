"""The `gbrain_git` backend: a GitHub repository's markdown files as pages.

Each run resolves the tracked ref's head sha with one conditional request, and only a new sha
downloads the repo tarball, so the poll is one cheap call while the repo sits still. Every request
counts against GitHub's primary rate limit, 304s included, and the anonymous budget is 60 per hour
per address — so a workspace with no stored `github_token` probes each source at most once per
`UNAUTHENTICATED_PROBE_SECONDS` (the cursor carries the last probe instant), while a stored token
probes on the driver's own minute tick against the 5000-per-hour authenticated budget. A changed
repo returns a full snapshot pinned to the sha the run resolved, so the driver tombstones pages
whose files left the tree: a git delete is a soft delete. The archive spools to a temporary file
and the markdown is streamed out of it, so the process never holds the tarball in memory — what it
holds is the markdown itself, bounded by `max_bytes` compressed on disk and decompressed in the
extract. The `github_token` credential slot opens private repositories; without it the backend
reads public ones."""

import asyncio
import json
import os
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ufo.sdk.context import CredentialAccess
from ufo.sdk.sources import SourceAuth, StreamFault, SyncResult
from ufo_ext_gbrain.pages import decoded, is_markdown_path, markdown_page

GIT_BACKEND = "gbrain_git"
GITHUB_TOKEN_SLOT = "github_token"
GITHUB_API = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"
SHA_ACCEPT = "application/vnd.github.sha"
TARBALL_MAX_BYTES = 2 * 1024 * 1024 * 1024
TARBALL_CHUNK_BYTES = 4 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 60.0
EMPTY_REPOSITORY_STATUS = 409
UNAUTHENTICATED_PROBE_SECONDS = 15 * 60


class GbrainGitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    branch: str | None = None


class _Cursor(BaseModel):
    sha: str
    etag: str | None = None
    checked_at: str | None = None

    def encoded(self) -> str:
        return json.dumps(
            {"sha": self.sha, "etag": self.etag, "checked_at": self.checked_at}, sort_keys=True
        )


def _prior_cursor(cursor: str | None) -> _Cursor | None:
    if cursor is None:
        return None
    try:
        return _Cursor.model_validate_json(cursor)
    except ValueError:
        return None


def _probe_due(prior: _Cursor) -> bool:
    if prior.checked_at is None:
        return True
    try:
        checked = datetime.fromisoformat(prior.checked_at)
    except ValueError:
        return True
    return (datetime.now(UTC) - checked).total_seconds() >= UNAUTHENTICATED_PROBE_SECONDS


@dataclass(frozen=True)
class GbrainGitSource:
    """Syncs one GitHub repository's markdown tree, keyed by path within the repo and titled from
    frontmatter or first heading. `branch` unset tracks the repository's default branch."""

    credentials: CredentialAccess
    transport: httpx.AsyncBaseTransport | None = None
    max_bytes: int = TARBALL_MAX_BYTES
    config_model: ClassVar[type[GbrainGitConfig]] = GbrainGitConfig

    async def fetch(
        self, config: GbrainGitConfig, cursor: str | None, auth: SourceAuth
    ) -> SyncResult:
        prior = _prior_cursor(cursor)
        token_stored = await self.credentials.stored(GITHUB_TOKEN_SLOT)
        if not token_stored and prior is not None and not _probe_due(prior):
            return SyncResult(pages=(), next_cursor=cursor, snapshot=False)
        now = datetime.now(UTC).isoformat(timespec="seconds")
        async with httpx.AsyncClient(
            transport=self.transport,
            base_url=GITHUB_API,
            timeout=REQUEST_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers=await self._headers(token_stored),
        ) as client:
            head = await self._head(client, config, prior)
            if head is None:
                stamped = None if prior is None else prior.model_copy(update={"checked_at": now})
                return SyncResult(
                    pages=(),
                    next_cursor=cursor if stamped is None else stamped.encoded(),
                    snapshot=False,
                )
            head = head.model_copy(update={"checked_at": now})
            if head.sha == "":
                return SyncResult(pages=(), next_cursor=head.encoded(), snapshot=True)
            if prior is not None and prior.sha == head.sha:
                return SyncResult(pages=(), next_cursor=head.encoded(), snapshot=False)
            spool = await self._spool_tarball(client, config, head.sha)
        try:
            entries = await asyncio.to_thread(
                self._markdown_entries, config.repo, spool, self.max_bytes
            )
        finally:
            await asyncio.to_thread(os.unlink, spool)
        pages = tuple(markdown_page(ref, decoded(ref, data)) for ref, data in entries)
        return SyncResult(pages=pages, next_cursor=head.encoded(), snapshot=True)

    async def _headers(self, token_stored: bool) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }
        if token_stored:
            headers["Authorization"] = f"Bearer {await self.credentials.get(GITHUB_TOKEN_SLOT)}"
        return headers

    async def _head(
        self, client: httpx.AsyncClient, config: GbrainGitConfig, prior: _Cursor | None
    ) -> _Cursor | None:
        ref = config.branch or "HEAD"
        headers = {"Accept": SHA_ACCEPT}
        if prior is not None and prior.etag is not None:
            headers["If-None-Match"] = prior.etag
        response = await client.get(f"/repos/{config.repo}/commits/{ref}", headers=headers)
        if response.status_code == httpx.codes.NOT_MODIFIED:
            return None
        if response.status_code == EMPTY_REPOSITORY_STATUS:
            return _Cursor(sha="")
        _refuse_client_error(response, f"{config.repo}@{ref} head")
        return _Cursor(sha=response.text.strip(), etag=response.headers.get("etag"))

    async def _spool_tarball(
        self, client: httpx.AsyncClient, config: GbrainGitConfig, sha: str
    ) -> str:
        descriptor, path = await asyncio.to_thread(tempfile.mkstemp, suffix=".tar.gz")
        received = 0
        try:
            async with client.stream("GET", f"/repos/{config.repo}/tarball/{sha}") as response:
                _refuse_client_error(response, f"{config.repo} tarball")
                async for chunk in response.aiter_bytes(TARBALL_CHUNK_BYTES):
                    received += len(chunk)
                    if received > self.max_bytes:
                        raise StreamFault(f"{config.repo} tarball exceeds {self.max_bytes} bytes")
                    await asyncio.to_thread(os.write, descriptor, chunk)
        except BaseException:
            await asyncio.to_thread(os.close, descriptor)
            await asyncio.to_thread(os.unlink, path)
            raise
        await asyncio.to_thread(os.close, descriptor)
        return path

    @staticmethod
    def _markdown_entries(repo: str, spool: str, max_bytes: int) -> tuple[tuple[str, bytes], ...]:
        entries: list[tuple[str, bytes]] = []
        total = 0
        with tarfile.open(name=spool, mode="r:gz") as tar:
            for member in tar:
                if not member.isfile():
                    continue
                _, _, ref = member.name.partition("/")
                if not ref or not is_markdown_path(ref):
                    continue
                handle = tar.extractfile(member)
                if handle is None:
                    continue
                data = handle.read()
                total += len(data)
                if total > max_bytes:
                    raise StreamFault(f"{repo} markdown exceeds {max_bytes} bytes decompressed")
                entries.append((ref, data))
        return tuple(sorted(entries))


def _refuse_client_error(response: httpx.Response, what: str) -> None:
    if response.is_client_error:
        raise StreamFault(f"github answered {response.status_code} for {what}")
    response.raise_for_status()
