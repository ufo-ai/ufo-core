"""The gbrain git backend over a mock transport: the conditional head probe, the sha-pinned
tarball snapshot, the JSON cursor, and the stored-token Authorization gate. No conftest: the
shared `ufo_testsupport` plugin covers fixtures, and every GitHub answer is canned — only the
stored-credential test touches the test database, through the real credential store."""

import io
import json
import tarfile
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from ufo_ext_gbrain.git import (
    GITHUB_API_VERSION,
    GITHUB_TOKEN_SLOT,
    SHA_ACCEPT,
    UNAUTHENTICATED_PROBE_SECONDS,
    GbrainGitConfig,
    GbrainGitSource,
)

from ufo.db import workspace_tx
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.workspace import init_workspace_credentials, ws
from ufo.schema import tables
from ufo.sdk.context import CredentialAccess
from ufo.sdk.sources import SourceAuth, StreamFault, SyncResult

REPO = "acme/brain"
SHA = "a" * 40
ETAG = '"etag-1"'
REFRESHED_ETAG = '"etag-2"'
TOKEN = "ghp_stored"
PREFIX = "acme-brain-" + SHA[:7]
ENTRIES = {
    "pax_global_header": b"52 comment=" + SHA.encode(),
    f"{PREFIX}/": b"",
    f"{PREFIX}/README.md": b"# Brain\n\nHello.\n",
    f"{PREFIX}/docs/guide.md": b"---\ntitle: Guide\n---\nBody.\n",
    f"{PREFIX}/logo.png": b"\x89PNG\r\n",
    f"{PREFIX}/.git/HEAD": b"ref: refs/heads/main\n",
}
MEGABYTE = b"x" * (1024 * 1024)

Handler = Callable[[httpx.Request], httpx.Response]


def _archive(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            if name.endswith("/"):
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
                continue
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _source(handler: Handler) -> GbrainGitSource:
    return GbrainGitSource(
        credentials=CredentialAccess(declared=frozenset({GITHUB_TOKEN_SLOT})),
        transport=httpx.MockTransport(handler),
    )


async def _fetch(
    handler: Handler, cursor: str | None = None, branch: str | None = None
) -> SyncResult:
    config = GbrainGitConfig(repo=REPO, branch=branch)
    with ws(uuid4()):
        return await _source(handler).fetch(config, cursor, SourceAuth(workspace_id=uuid4()))


def _parsed_cursor(result: SyncResult) -> dict[str, str | None]:
    assert result.next_cursor is not None
    parsed: dict[str, str | None] = json.loads(result.next_cursor)
    return parsed


def _repo_handler(seen: list[httpx.Request]) -> Handler:
    archive = _archive(ENTRIES)

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == f"/repos/{REPO}/commits/HEAD":
            return httpx.Response(200, text=SHA + "\n", headers={"ETag": ETAG})
        if request.url.path == f"/repos/{REPO}/tarball/{SHA}":
            return httpx.Response(200, content=archive)
        return httpx.Response(404)

    return handle


async def test_first_sync_resolves_head_then_snapshots_the_tarball() -> None:
    seen: list[httpx.Request] = []
    result = await _fetch(_repo_handler(seen))

    assert [request.url.path for request in seen] == [
        f"/repos/{REPO}/commits/HEAD",
        f"/repos/{REPO}/tarball/{SHA}",
    ]
    assert seen[0].headers["Accept"] == SHA_ACCEPT
    assert seen[0].headers["X-GitHub-Api-Version"] == GITHUB_API_VERSION
    assert "If-None-Match" not in seen[0].headers
    assert [page.source_ref for page in result.pages] == ["README.md", "docs/guide.md"]
    assert {page.source_ref: page.title for page in result.pages} == {
        "README.md": "Brain",
        "docs/guide.md": "Guide",
    }
    assert result.snapshot is True
    parsed = _parsed_cursor(result)
    assert (parsed["sha"], parsed["etag"]) == (SHA, ETAG)
    assert parsed["checked_at"] is not None


async def test_not_modified_is_idle_and_restamps_the_probe_instant() -> None:
    cursor = json.dumps({"sha": SHA, "etag": ETAG})
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(304)

    result = await _fetch(handle, cursor=cursor)

    assert seen[0].headers["If-None-Match"] == ETAG
    assert len(seen) == 1
    assert result.pages == ()
    assert result.snapshot is False
    parsed = _parsed_cursor(result)
    assert (parsed["sha"], parsed["etag"]) == (SHA, ETAG)
    assert parsed["checked_at"] is not None


async def test_same_sha_is_idle_with_a_refreshed_cursor() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=SHA, headers={"ETag": REFRESHED_ETAG})

    result = await _fetch(handle, cursor=json.dumps({"sha": SHA, "etag": None}))

    assert "If-None-Match" not in seen[0].headers
    assert len(seen) == 1
    assert result.pages == ()
    assert result.snapshot is False
    parsed = _parsed_cursor(result)
    assert (parsed["sha"], parsed["etag"]) == (SHA, REFRESHED_ETAG)
    assert parsed["checked_at"] is not None


async def test_head_not_found_is_a_stream_fault_naming_the_ref() -> None:
    with pytest.raises(StreamFault, match=f"github answered 404 for {REPO}@HEAD head"):
        await _fetch(lambda request: httpx.Response(404))


async def test_empty_repository_is_an_empty_snapshot() -> None:
    result = await _fetch(lambda request: httpx.Response(409))
    assert result.pages == ()
    assert _parsed_cursor(result)["sha"] == ""
    assert result.snapshot is True


async def test_tarball_client_error_is_a_stream_fault() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/repos/{REPO}/commits/HEAD":
            return httpx.Response(200, text=SHA)
        return httpx.Response(403)

    with pytest.raises(StreamFault, match=f"github answered 403 for {REPO} tarball"):
        await _fetch(handle)


async def test_server_error_raises_for_status() -> None:
    with pytest.raises(httpx.HTTPStatusError):
        await _fetch(lambda request: httpx.Response(502))


async def test_oversize_tarball_is_a_stream_fault() -> None:
    cap = 4 * len(MEGABYTE)

    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(cap // len(MEGABYTE) + 1):
            yield MEGABYTE

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/repos/{REPO}/commits/HEAD":
            return httpx.Response(200, text=SHA)
        return httpx.Response(200, content=chunks())

    source = GbrainGitSource(
        credentials=CredentialAccess(declared=frozenset({GITHUB_TOKEN_SLOT})),
        transport=httpx.MockTransport(handle),
        max_bytes=cap,
    )
    with ws(uuid4()):
        with pytest.raises(StreamFault, match=f"{REPO} tarball exceeds {cap} bytes"):
            await source.fetch(GbrainGitConfig(repo=REPO), None, SourceAuth(workspace_id=uuid4()))


async def test_oversize_decompressed_markdown_is_a_stream_fault() -> None:
    cap = 1024 * 1024
    entries = dict(ENTRIES) | {f"{PREFIX}/big.md": b"x" * (2 * cap)}
    archive = _archive(entries)
    assert len(archive) < cap

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/repos/{REPO}/commits/HEAD":
            return httpx.Response(200, text=SHA)
        return httpx.Response(200, content=archive)

    source = GbrainGitSource(
        credentials=CredentialAccess(declared=frozenset({GITHUB_TOKEN_SLOT})),
        transport=httpx.MockTransport(handle),
        max_bytes=cap,
    )
    with ws(uuid4()):
        with pytest.raises(StreamFault, match=f"{REPO} markdown exceeds {cap} bytes decompressed"):
            await source.fetch(GbrainGitConfig(repo=REPO), None, SourceAuth(workspace_id=uuid4()))


async def test_tokenless_probe_is_throttled_inside_the_interval() -> None:
    seen: list[httpx.Request] = []
    fresh = datetime.now(UTC).isoformat(timespec="seconds")
    cursor = json.dumps({"sha": SHA, "etag": ETAG, "checked_at": fresh})

    result = await _fetch(_repo_handler(seen), cursor=cursor)

    assert seen == []
    assert result.pages == ()
    assert result.snapshot is False
    assert result.next_cursor == cursor


async def test_tokenless_probe_resumes_past_the_interval() -> None:
    seen: list[httpx.Request] = []
    stale = (datetime.now(UTC) - timedelta(seconds=UNAUTHENTICATED_PROBE_SECONDS)).isoformat(
        timespec="seconds"
    )
    cursor = json.dumps({"sha": SHA, "etag": ETAG, "checked_at": stale})

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(304)

    result = await _fetch(handle, cursor=cursor)

    assert len(seen) == 1
    assert _parsed_cursor(result)["checked_at"] != stale


async def test_branch_config_is_the_head_ref() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == f"/repos/{REPO}/commits/main":
            return httpx.Response(200, text=SHA)
        return httpx.Response(200, content=_archive(ENTRIES))

    result = await _fetch(handle, branch="main")

    assert seen[0].url.path == f"/repos/{REPO}/commits/main"
    assert result.snapshot is True


async def test_unreadable_cursor_falls_back_to_a_full_sync() -> None:
    seen: list[httpx.Request] = []
    result = await _fetch(_repo_handler(seen), cursor="not json")
    assert len(seen) == 2
    assert result.snapshot is True


async def test_without_a_stored_token_no_authorization_header_is_sent() -> None:
    seen: list[httpx.Request] = []
    await _fetch(_repo_handler(seen))
    assert seen and all("Authorization" not in request.headers for request in seen)


async def test_a_stored_token_authorizes_every_request(db: None) -> None:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    await store.put(workspace_id, GITHUB_TOKEN_SLOT, TOKEN)
    init_workspace_credentials(store)
    seen: list[httpx.Request] = []

    with ws(workspace_id):
        result = await _source(_repo_handler(seen)).fetch(
            GbrainGitConfig(repo=REPO), None, SourceAuth(workspace_id=workspace_id)
        )

    assert result.snapshot is True
    assert seen and all(request.headers["Authorization"] == f"Bearer {TOKEN}" for request in seen)
