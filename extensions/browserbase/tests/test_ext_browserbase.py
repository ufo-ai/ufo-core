"""The browserbase cdp provider's proof: one hosted session per browser run, minted against a
Context that survives a re-mint inside the run and is deleted with the session at run end.

Each test drives the provider over an `httpx.MockTransport` that records the requests it emits and
answers canned Browserbase JSON — no live account — while the API key comes from the REAL credential
store and the Context mapping from the REAL `ext_store`, so the host-side key read and the durable
per-run mapping are exercised end to end. The recorded requests are the verbatim shapes the provider
puts on Browserbase's wire."""

import json
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
import ufo_ext_browserbase as browserbase
from cryptography.fernet import Fernet

from ufo.access.credentials import CredentialSlotUnset, CredentialStore
from ufo.db import workspace_tx
from ufo.ext.context import context_for
from ufo.sandbox.session import SandboxHandle, SandboxSession
from ufo.schema import tables
from ufo.sdk.browser import SessionGone
from ufo.workspace import init_workspace_credentials, ws

API_KEY = "bb-live-secret-0xfeedface"
SESSION_ID = "0f9d1c22-4d0a-4a1e-9a4a-9c3d9f1b7a01"
CONTEXT_ID = "6b6f1f2e-31b7-4a44-9a2a-1d9f4c8e2b55"
CONNECT_URL = f"wss://connect.browserbase.com?apiKey={API_KEY}&sessionId={SESSION_ID}"
DOWNLOAD_GUID = "1d5ac50b-756b-4bc1-a25b-8744e50bb6d0"
DOWNLOAD_ID = "a7031623-b731-4892-9bcc-83e7aa7e5b2d"
DOWNLOAD_BYTES = b"ID3\x04the downloaded file"


class _Browserbase:
    """Records each request the provider emits and answers canned Browserbase bodies — the stand-in
    for the API, never the thing asserted. `session_status` is what a later GET reports, so a test
    can make a run's session look reaped."""

    def __init__(self, session_status: str = "RUNNING", downloads_sync_after: int = 0) -> None:
        self.requests: list[httpx.Request] = []
        self.session_status = session_status
        self.contexts_created = 0
        self.downloads_sync_after = downloads_sync_after
        self.download_listings = 0
        self.download_size: int | None = len(DOWNLOAD_BYTES)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "POST" and path == "/v1/contexts":
            self.contexts_created += 1
            return httpx.Response(201, json={"id": CONTEXT_ID})
        if request.method == "POST" and path == "/v1/sessions":
            return httpx.Response(
                201, json={"id": SESSION_ID, "connectUrl": CONNECT_URL, "status": "RUNNING"}
            )
        if request.method == "GET" and path == f"/v1/sessions/{SESSION_ID}":
            return httpx.Response(
                200,
                json={"id": SESSION_ID, "connectUrl": CONNECT_URL, "status": self.session_status},
            )
        if request.method == "POST" and path == f"/v1/sessions/{SESSION_ID}":
            return httpx.Response(200, json={"id": SESSION_ID, "status": "REQUEST_RELEASE"})
        if request.method == "POST" and path == f"/v1/sessions/{SESSION_ID}/uploads":
            return httpx.Response(200, json={"message": "File uploaded successfully"})
        if request.method == "DELETE" and path == f"/v1/contexts/{CONTEXT_ID}":
            return httpx.Response(204)
        if request.method == "GET" and path == "/v1/downloads":
            self.download_listings += 1
            if self.download_listings <= self.downloads_sync_after:
                return httpx.Response(200, json={"downloads": [], "total": 0})
            return httpx.Response(
                200,
                json={
                    "downloads": [
                        {"id": "other-id", "filename": "some-other-guid", "size": 10},
                        {"id": DOWNLOAD_ID, "filename": DOWNLOAD_GUID}
                        if self.download_size is None
                        else {
                            "id": DOWNLOAD_ID,
                            "filename": DOWNLOAD_GUID,
                            "size": self.download_size,
                        },
                    ],
                    "total": 2,
                },
            )
        if request.method == "GET" and path == f"/v1/downloads/{DOWNLOAD_ID}":
            assert request.headers["Accept"] == "application/octet-stream"
            return httpx.Response(200, content=DOWNLOAD_BYTES)
        raise AssertionError(f"unexpected browserbase call {request.method} {path}")

    def sent(self, method: str, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and r.url.path == path]


async def _provider(
    api: _Browserbase, key: str | None = API_KEY
) -> tuple[browserbase.BrowserbaseCdpProvider, UUID]:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    if key is not None:
        await store.put(workspace_id, browserbase.API_KEY_SLOT, key)
    credentials = context_for(browserbase.NAME, frozenset({browserbase.API_KEY_SLOT})).credentials
    provider = browserbase.BrowserbaseCdpProvider(
        credentials=credentials, transport=httpx.MockTransport(api.handle)
    )
    return provider, workspace_id


def _sandbox(conversation_id: UUID) -> SandboxSession:
    """The turn's sandbox as the provider reads it: only its handle's conversation id, which for a
    browser subagent turn names that one browser run."""
    return SandboxSession(
        carrier=None,  # type: ignore[arg-type]
        handle=SandboxHandle(conversation_id=conversation_id, container_id="c1"),
    )


def test_manifest_declares_the_api_key_slot_and_the_browserbase_cdp_provider() -> None:
    manifest = browserbase.manifest()
    assert manifest.name == "browserbase"
    (slot,) = manifest.credentials
    assert slot.name == "browserbase_api_key"
    assert slot.injection is None
    (spec,) = manifest.cdp_providers
    assert spec.backend == "browserbase"
    assert not manifest.tools


async def test_lease_mints_a_session_on_a_fresh_context_for_the_run(db: None) -> None:
    api = _Browserbase()
    provider, workspace_id = await _provider(api)
    conversation_id = uuid4()
    with ws(workspace_id):
        lease = await provider.lease(_sandbox(conversation_id))
        assert (await lease.endpoint()).url == CONNECT_URL
        assert await lease.token() == f"{conversation_id}/{SESSION_ID}/{CONTEXT_ID}"
    (created,) = api.sent("POST", "/v1/sessions")
    assert created.headers[browserbase.API_KEY_HEADER] == API_KEY
    assert json.loads(created.content) == {
        "browserSettings": {"context": {"id": CONTEXT_ID, "persist": True}},
        "timeout": browserbase.SESSION_TIMEOUT_SECONDS,
    }
    assert api.contexts_created == 1


async def test_the_session_outlives_a_browser_runs_budget(db: None) -> None:
    """The plan default reaps a session in minutes while a browser run's budget starts at twenty, so
    an unset timeout would kill the browser mid-run."""
    api = _Browserbase()
    provider, workspace_id = await _provider(api)
    with ws(workspace_id):
        await provider.lease(_sandbox(uuid4()))
    (created,) = api.sent("POST", "/v1/sessions")
    assert json.loads(created.content)["timeout"] >= 20 * 60


async def test_downloads_target_the_hosted_sessions_own_storage(db: None) -> None:
    """A hosted Chrome refuses an absolute download path as a restricted directory, which fails the
    whole CDP bootstrap — so the lease names the one target Browserbase accepts."""
    api = _Browserbase()
    provider, workspace_id = await _provider(api)
    with ws(workspace_id):
        lease = await provider.lease(_sandbox(uuid4()))
        assert await lease.download_dir() == "downloads"


async def test_a_re_mint_inside_one_run_reuses_that_runs_context(db: None) -> None:
    """The Context is what makes a re-minted session resume the run's logins: a second lease for the
    same conversation must carry the same Context rather than starting the browser cold."""
    api = _Browserbase()
    provider, workspace_id = await _provider(api)
    conversation_id = uuid4()
    with ws(workspace_id):
        await provider.lease(_sandbox(conversation_id))
        await provider.lease(_sandbox(conversation_id))
    assert api.contexts_created == 1
    assert [json.loads(r.content)["browserSettings"] for r in api.sent("POST", "/v1/sessions")] == [
        {"context": {"id": CONTEXT_ID, "persist": True}}
    ] * 2


async def test_a_second_run_gets_its_own_context(db: None) -> None:
    """Two browser subagents in one workspace are two runs: neither may inherit the other's cookies,
    so each conversation mints its own Context."""
    api = _Browserbase()
    provider, workspace_id = await _provider(api)
    with ws(workspace_id):
        await provider.lease(_sandbox(uuid4()))
        await provider.lease(_sandbox(uuid4()))
    assert api.contexts_created == 2


async def test_aclose_releases_the_session_and_deletes_the_runs_context(db: None) -> None:
    api = _Browserbase()
    provider, workspace_id = await _provider(api)
    conversation_id = uuid4()
    with ws(workspace_id):
        lease = await provider.lease(_sandbox(conversation_id))
        await lease.aclose()
        (released,) = api.sent("POST", f"/v1/sessions/{SESSION_ID}")
        assert json.loads(released.content) == {"status": "REQUEST_RELEASE"}
        assert api.sent("DELETE", f"/v1/contexts/{CONTEXT_ID}")
        await provider.lease(_sandbox(conversation_id))
    assert api.contexts_created == 2


async def test_a_failed_context_delete_still_clears_the_runs_row(db: None) -> None:
    """A transient failure deleting the Context must not leave the row naming it: the next lease
    reads that row straight back into `create_session`, so a kept row would break every later browse
    in this conversation. A leaked Context costs storage; a kept row costs the conversation."""

    class _RefusesContextDelete(_Browserbase):
        def handle(self, request: httpx.Request) -> httpx.Response:
            if request.method == "DELETE":
                self.requests.append(request)
                return httpx.Response(503, text="try again")
            return super().handle(request)

    api = _RefusesContextDelete()
    provider, workspace_id = await _provider(api)
    conversation_id = uuid4()
    with ws(workspace_id):
        lease = await provider.lease(_sandbox(conversation_id))
        with pytest.raises(browserbase.BrowserbaseError):
            await lease.aclose()
        await provider.lease(_sandbox(conversation_id))
    assert api.contexts_created == 2


async def test_two_files_sharing_a_name_do_not_overwrite_each_other(db: None) -> None:
    """Uploads land in one flat remote directory keyed by file name, so two workspace files with the
    same base name would collide and the page would receive one file twice."""
    api = _Browserbase()
    provider, workspace_id = await _provider(api)
    with ws(workspace_id):
        lease = await provider.lease(_sandbox(uuid4()))
        first = await lease.place_file("/workspace/a/report.pdf", lambda: _bytes(b"first"))
        second = await lease.place_file("/workspace/b/report.pdf", lambda: _bytes(b"second"))
        again = await lease.place_file("/workspace/a/report.pdf", lambda: _bytes(b"first"))
    assert first == "/tmp/.uploads/report.pdf"
    assert second != first and second.endswith("-report.pdf")
    assert again == first


async def test_a_download_comes_back_from_the_sessions_storage(db: None) -> None:
    """`allowAndName` stores a completed download under its CDP guid, and that guid is the
    `filename` the Downloads API reports — measured against the live API — so a session's downloads
    are matched on it rather than on the name the site suggested."""
    api = _Browserbase()
    provider, workspace_id = await _provider(api)
    with ws(workspace_id):
        lease = await provider.lease(_sandbox(uuid4()))
        assert await lease.fetch_download(DOWNLOAD_GUID) == DOWNLOAD_BYTES


async def test_a_download_still_syncing_is_waited_for(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Storage trails the browser finishing the file, so an empty first listing is a not-yet, not a
    no — reporting it as missing would lose a download the member watched complete."""
    monkeypatch.setattr(browserbase, "DOWNLOAD_SYNC_SLEEP_SECONDS", 0.0)
    api = _Browserbase(downloads_sync_after=2)
    provider, workspace_id = await _provider(api)
    with ws(workspace_id):
        lease = await provider.lease(_sandbox(uuid4()))
        assert await lease.fetch_download(DOWNLOAD_GUID) == DOWNLOAD_BYTES
    assert api.download_listings == 3


async def test_a_download_that_never_stores_fails_loud(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(browserbase, "DOWNLOAD_SYNC_SLEEP_SECONDS", 0.0)
    api = _Browserbase(downloads_sync_after=browserbase.DOWNLOAD_SYNC_ATTEMPTS)
    provider, workspace_id = await _provider(api)
    with ws(workspace_id):
        lease = await provider.lease(_sandbox(uuid4()))
        with pytest.raises(browserbase.BrowserbaseError, match="never stored"):
            await lease.fetch_download(DOWNLOAD_GUID)


async def test_a_failed_upload_leaves_its_name_free_for_the_retry(db: None) -> None:
    """A name is claimed by bytes that arrived, not by an attempt: an upload that failed to read
    must not push its retry onto a digest-qualified name."""
    api = _Browserbase()
    provider, workspace_id = await _provider(api)

    async def unreadable() -> bytes:
        raise ValueError("cannot read /workspace/report.pdf")

    with ws(workspace_id):
        lease = await provider.lease(_sandbox(uuid4()))
        with pytest.raises(ValueError, match="cannot read"):
            await lease.place_file("/workspace/report.pdf", unreadable)
        assert await lease.place_file("/workspace/report.pdf", lambda: _bytes(b"ok")) == (
            "/tmp/.uploads/report.pdf"
        )


async def test_an_oversized_download_is_refused_before_its_bytes_are_read(db: None) -> None:
    """The file crosses whole into this shared process, and what a page chooses to download is not
    ours to trust — so the listing's size decides, and the bytes are never fetched."""
    api = _Browserbase()
    api.download_size = browserbase.MAX_DOWNLOAD_BYTES + 1
    provider, workspace_id = await _provider(api)
    with ws(workspace_id):
        lease = await provider.lease(_sandbox(uuid4()))
        with pytest.raises(ValueError, match="at most"):
            await lease.fetch_download(DOWNLOAD_GUID)
    assert not api.sent("GET", f"/v1/downloads/{DOWNLOAD_ID}")


async def test_a_download_listed_without_a_size_fails_rather_than_fetching(db: None) -> None:
    """The size is the only thing between a page's choice of file and this process's memory, so a
    listing missing it is a failure, never a reason to skip the bound."""
    api = _Browserbase()
    api.download_size = None
    provider, workspace_id = await _provider(api)
    with ws(workspace_id):
        lease = await provider.lease(_sandbox(uuid4()))
        with pytest.raises(browserbase.BrowserbaseError, match="no size"):
            await lease.fetch_download(DOWNLOAD_GUID)
    assert not api.sent("GET", f"/v1/downloads/{DOWNLOAD_ID}")


async def test_an_upload_that_fails_to_send_leaves_its_name_free(db: None) -> None:
    """A name is claimed by bytes that landed, not by an attempt: an upload the API refused must not
    push its retry onto a digest-qualified name the site would then see."""

    class _RefusesFirstUpload(_Browserbase):
        refusals = 1

        def handle(self, request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/uploads") and self.refusals:
                self.refusals -= 1
                self.requests.append(request)
                return httpx.Response(502, text="bad gateway")
            return super().handle(request)

    api = _RefusesFirstUpload()
    provider, workspace_id = await _provider(api)
    with ws(workspace_id):
        lease = await provider.lease(_sandbox(uuid4()))
        with pytest.raises(browserbase.BrowserbaseError):
            await lease.place_file("/workspace/report.pdf", lambda: _bytes(b"first try"))
        assert await lease.place_file("/workspace/report.pdf", lambda: _bytes(b"retry")) == (
            "/tmp/.uploads/report.pdf"
        )


async def test_a_download_whose_size_is_not_a_number_fails(db: None) -> None:
    """A JSON `true` would pass an `int` check in Python, where `bool` subclasses `int`, and be read
    as one byte — so the bound would wave through a file of unknown size."""
    api = _Browserbase()
    api.download_size = True  # type: ignore[assignment]
    provider, workspace_id = await _provider(api)
    with ws(workspace_id):
        lease = await provider.lease(_sandbox(uuid4()))
        with pytest.raises(browserbase.BrowserbaseError, match="no size"):
            await lease.fetch_download(DOWNLOAD_GUID)
    assert not api.sent("GET", f"/v1/downloads/{DOWNLOAD_ID}")


async def test_reattach_yields_a_lease_over_the_live_session(db: None) -> None:
    api = _Browserbase()
    provider, workspace_id = await _provider(api)
    conversation_id = uuid4()
    with ws(workspace_id):
        token = await (await provider.lease(_sandbox(conversation_id))).token()
        reattached = await provider.reattach(token)
        assert (await reattached.endpoint()).url == CONNECT_URL
        assert await reattached.token() == token
    assert api.sent("GET", f"/v1/sessions/{SESSION_ID}")
    assert len(api.sent("POST", "/v1/sessions")) == 1


async def test_reattach_reports_a_reaped_session_gone(db: None) -> None:
    api = _Browserbase(session_status="COMPLETED")
    provider, workspace_id = await _provider(api)
    conversation_id = uuid4()
    with ws(workspace_id), pytest.raises(SessionGone):
        token = await (await provider.lease(_sandbox(conversation_id))).token()
        await provider.reattach(token)


async def test_reattach_reports_an_unparseable_token_gone(db: None) -> None:
    api = _Browserbase()
    provider, workspace_id = await _provider(api)
    with ws(workspace_id), pytest.raises(SessionGone):
        await provider.reattach("not-a-run-token")


async def test_place_file_uploads_the_bytes_and_answers_the_remote_path(db: None) -> None:
    api = _Browserbase()
    provider, workspace_id = await _provider(api)
    with ws(workspace_id):
        lease = await provider.lease(_sandbox(uuid4()))
        placed = await lease.place_file("/workspace/report.pdf", lambda: _bytes(b"%PDF-1.7"))
    assert placed == "/tmp/.uploads/report.pdf"
    (upload,) = api.sent("POST", f"/v1/sessions/{SESSION_ID}/uploads")
    assert b'name="file"; filename="report.pdf"' in upload.content
    assert b"%PDF-1.7" in upload.content


async def test_place_file_refuses_a_file_over_the_upload_cap(db: None) -> None:
    api = _Browserbase()
    provider, workspace_id = await _provider(api)
    with ws(workspace_id):
        lease = await provider.lease(_sandbox(uuid4()))
        with pytest.raises(ValueError, match="capped"):
            await lease.place_file(
                "/workspace/huge.bin", lambda: _bytes(b"x" * (browserbase.MAX_UPLOAD_BYTES + 1))
            )
    assert not api.sent("POST", f"/v1/sessions/{SESSION_ID}/uploads")


async def test_lease_without_the_api_key_set_fails_loud(db: None) -> None:
    api = _Browserbase()
    provider, workspace_id = await _provider(api, key=None)
    with ws(workspace_id), pytest.raises(CredentialSlotUnset):
        await provider.lease(_sandbox(uuid4()))


async def test_a_workspace_with_no_byok_key_uses_the_platform_key(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """How a hosted deploy is keyed: the fleet's own Browserbase key arrives in serve's environment
    as `BROWSERBASE_API_KEY` and every workspace browses on it, with a stored BYOK value overriding
    it per workspace."""
    monkeypatch.setenv("BROWSERBASE_API_KEY", "bb-platform-key")
    api = _Browserbase()
    provider, workspace_id = await _provider(api, key=None)
    with ws(workspace_id):
        await provider.lease(_sandbox(uuid4()))
    (created,) = api.sent("POST", "/v1/sessions")
    assert created.headers[browserbase.API_KEY_HEADER] == "bb-platform-key"


async def test_lease_without_the_turns_sandbox_fails_loud(db: None) -> None:
    """The sandbox is how the provider learns which browser run it is leasing for; without it a
    session would be minted under no run at all."""
    api = _Browserbase()
    provider, workspace_id = await _provider(api)
    with ws(workspace_id), pytest.raises(RuntimeError, match="browser run"):
        await provider.lease(None)


async def test_a_browserbase_error_surfaces_with_its_status(db: None) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, text="payment required")

    provider, workspace_id = await _provider(_Browserbase())
    provider = browserbase.BrowserbaseCdpProvider(
        credentials=provider.credentials, transport=httpx.MockTransport(refuse)
    )
    with ws(workspace_id), pytest.raises(browserbase.BrowserbaseError, match="402"):
        await provider.lease(_sandbox(uuid4()))


async def _bytes(data: bytes) -> bytes:
    return data
