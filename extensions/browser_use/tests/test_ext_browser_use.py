"""The browser_use pack's proof: `browser_task` and `wide_browse` drive Browser Use's hosted agent
over the v4 REST API and hand the turn a result and the run's own files.

Each test drives the real tool handlers over an `httpx.MockTransport` that records every request and
answers each path with canned v4 JSON — no live key or network — while the API key is read through
the REAL credential store, so the host-side key read is exercised end to end. The scripted API is a
stand-in for the dependency, never the thing asserted: the assertions are on what the run flow put
on the wire, what it wrote into the workspace, and what it returned."""

import json
import shlex
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
import ufo_ext_browser_use as browser_use
from cryptography.fernet import Fernet
from ufo_ext_browser_use import BrowserTaskInput, WideBrowseInput

from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.harness.containment import ContainmentError
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import ExecResult, ProxyEndpoint, SandboxSession, SandboxSpec
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.ext.context import context_for
from ufo.runtime.tools.context import ToolContext
from ufo.runtime.tools.registry import ToolRegistry
from ufo.runtime.workspace import init_workspace_credentials, ws
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import conversation_audience

API_KEY = "bu_live_secret_0xdeadbeef"
RUN_ID = "11111111-1111-1111-1111-111111111111"
SESSION_ID = "22222222-2222-2222-2222-222222222222"
WORKSPACE_ID = "33333333-3333-3333-3333-333333333333"
DOWNLOAD_URL = "https://files.browser-use.test/out/data.csv"


@dataclass
class _Api:
    """Answers the v4 paths the run flow walks and records every request it emits. `statuses` is
    consumed one per status poll, the last value repeating once exhausted."""

    statuses: list[str] = field(default_factory=lambda: ["running", "completed"])
    result: str = "found it"
    error: str | None = None
    files: list[dict[str, object]] = field(default_factory=list)
    has_more: bool = False
    listing: dict[str, object] | None = None
    content: bytes = b"col\n1\n"
    create_status: int = 200
    create_body: dict[str, object] | None = None
    create_text: str | None = None
    download_status: int = 200
    cancel_status: int = 200
    fail_status_for: set[str] = field(default_factory=set)
    requests: list[httpx.Request] = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.url.host == "files.browser-use.test":
            if self.download_status >= 400:
                return httpx.Response(self.download_status, text="denied")
            return httpx.Response(200, content=self.content)
        if path == "/api/v4/runs" and request.method == "POST":
            if self.create_status >= 400:
                return httpx.Response(self.create_status, json={"detail": "nope"})
            task = json.loads(request.content)["task"]
            if any(bad in task for bad in self.fail_status_for):
                return httpx.Response(500, json={"detail": "vendor hiccup"})
            if self.create_text is not None:
                return httpx.Response(200, text=self.create_text)
            return httpx.Response(
                200,
                json=self.create_body
                if self.create_body is not None
                else {
                    "id": RUN_ID,
                    "status": "queued",
                    "model": "m",
                    "sessionId": SESSION_ID,
                    "workspaceId": WORKSPACE_ID,
                    "eventsUrl": "e",
                },
            )
        if path.endswith("/cancel"):
            if self.cancel_status >= 400:
                return httpx.Response(self.cancel_status, json={"detail": "nope"})
            return httpx.Response(200, json={"id": RUN_ID, "status": "cancelled"})
        if path.endswith("/status"):
            status = self.statuses[0] if len(self.statuses) == 1 else self.statuses.pop(0)
            return httpx.Response(200, json={"status": status})
        if path.endswith("/files"):
            if self.listing is not None:
                return httpx.Response(200, json=self.listing)
            return httpx.Response(200, json={"files": self.files, "hasMore": self.has_more})
        if path == f"/api/v4/runs/{RUN_ID}":
            return httpx.Response(200, json={"result": self.result, "error": self.error})
        raise AssertionError(f"unscripted request: {request.method} {request.url}")

    def sent(self, method: str, path: str) -> list[httpx.Request]:
        """Requests at exactly `path` — never a prefix, so `/runs/{id}` cannot silently count the
        `/runs/{id}/status` polls."""
        return [
            request
            for request in self.requests
            if request.method == method and request.url.path == path
        ]

    def touched(self, fragment: str) -> bool:
        return any(fragment in request.url.path for request in self.requests)


@dataclass
class _Sandbox:
    """Answers `cat <path>` from a scripted file map and captures write_file calls."""

    files: dict[str, str] = field(default_factory=dict)
    writes: dict[str, bytes] = field(default_factory=dict)
    commands: list[str] = field(default_factory=list)

    async def bash(self, command: str, timeout_s: int = 120) -> ExecResult:
        self.commands.append(command)
        for path, content in self.files.items():
            if shlex.quote(path) in command:
                return ExecResult(stdout=content, stderr="", exit_code=0)
        return ExecResult(stdout="", stderr="not found", exit_code=1)

    async def write_file(self, path: str, content: bytes) -> None:
        self.writes[path] = content


async def _keyed_workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    await store.put(workspace_id, browser_use.API_KEY_SLOT, API_KEY)
    return workspace_id


def _context(
    sandbox: _Sandbox | SandboxSession, tmp_path: Path, idempotency_key: str | None = None
) -> ToolContext:
    return ToolContext(
        sandbox=sandbox,
        blob=FilesystemBlobStore(root=tmp_path),
        turn=Turn(
            id=uuid4(),
            workspace_id=uuid4(),
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="hi",
            created_at=datetime(2026, 7, 28, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=None,
        speaker_member_id=None,
        audience=conversation_audience(None),
        artifact_token_secret="",
        idempotency_key=idempotency_key,
        ext=context_for(browser_use.NAME, frozenset({browser_use.API_KEY_SLOT})),
    )


@pytest.fixture
def fast_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser_use, "POLL_SECONDS", 0)


def _wire(monkeypatch: pytest.MonkeyPatch, api: _Api) -> None:
    monkeypatch.setattr(browser_use, "BROWSER_USE_TRANSPORT", httpx.MockTransport(api.handle))


def _tool(name: str):
    return next(tool for tool in browser_use.BROWSER_USE_TOOLS if tool.name == name)


def test_manifest_publishes_the_browser_packs_two_tools_and_nothing_else() -> None:
    manifest = browser_use.manifest()
    assert manifest.name == "browser_use"
    assert [tool.name for tool in manifest.tools] == ["browser_task", "wide_browse"]
    assert all(tool.untrusted for tool in manifest.tools)
    (slot,) = manifest.credentials
    assert slot.name == "browser_use_api_key"
    assert slot.injection is None
    (section,) = manifest.prompt_sections
    assert section.name == "browser"
    assert not manifest.subagents
    assert not manifest.cdp_providers
    assert not manifest.requires


def test_this_pack_and_the_browser_pack_cannot_load_together() -> None:
    """The whole 'optionally replace' contract: the duplicate names are refused at boot, so no
    deploy can hold both web-automation stacks and no config has to choose between them."""
    from ufo_ext_browser.manifest import manifest as browser_manifest

    both = (*browser_manifest().tools, *browser_use.manifest().tools)
    with pytest.raises(ValueError, match="duplicate tool names: browser_task, wide_browse"):
        ToolRegistry(tuple(tool for tool in both if not tool.profile_only))


async def test_browser_task_creates_one_run_and_returns_its_result_and_files(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fast_poll: None
) -> None:
    api = _Api(files=[{"path": "data.csv", "size": 6, "url": DOWNLOAD_URL}])
    _wire(monkeypatch, api)
    workspace_id = await _keyed_workspace()
    sandbox = _Sandbox()
    with ws(workspace_id):
        result = await _tool("browser_task").handler(
            _context(sandbox, tmp_path),
            BrowserTaskInput(
                url="https://shop.test",
                task="read the price",
                task_name="Price check",
            ),
        )

    (created,) = api.sent("POST", "/api/v4/runs")
    assert created.headers[browser_use.API_KEY_HEADER] == API_KEY
    body = json.loads(created.content)
    assert body["model"] == "claude-sonnet-5"
    assert body["maxCostUsd"] == browser_use.TASK_MAX_COST_USD
    assert body["browserSettings"] == {"proxyCountryCode": "us"}
    assert body["task"] == "Start at https://shop.test\n\nread the price"

    assert sandbox.writes == {"/workspace/data.csv": b"col\n1\n"}
    payload = json.loads(result.content[0].text)
    assert payload == {
        "result": "found it",
        "files": ["data.csv"],
        "files_not_fetched": [],
        "more_files_exist": False,
    }
    assert not result.is_error


async def test_the_api_key_never_reaches_the_presigned_storage_host(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fast_poll: None
) -> None:
    api = _Api(files=[{"path": "data.csv", "size": 6, "url": DOWNLOAD_URL}])
    _wire(monkeypatch, api)
    workspace_id = await _keyed_workspace()
    with ws(workspace_id):
        await _tool("browser_task").handler(
            _context(_Sandbox(), tmp_path),
            BrowserTaskInput(url="https://shop.test", task="t", task_name="n"),
        )
    (download,) = [r for r in api.requests if r.url.host == "files.browser-use.test"]
    assert browser_use.API_KEY_HEADER not in download.headers
    assert API_KEY not in str(download.headers)


async def test_browser_task_cancels_the_run_when_its_timeout_expires(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fast_poll: None
) -> None:
    api = _Api(statuses=["running"])
    _wire(monkeypatch, api)
    workspace_id = await _keyed_workspace()
    with ws(workspace_id):
        result = await _tool("browser_task").handler(
            _context(_Sandbox(), tmp_path),
            BrowserTaskInput.model_construct(
                url="https://slow.test",
                task="t",
                task_name="Wedged",
                timeout_minutes=0,
            ),
        )
    assert api.sent("POST", f"/api/v4/runs/{RUN_ID}/cancel")
    assert not api.sent("GET", f"/api/v4/runs/{RUN_ID}")
    assert result.is_error
    assert "was cancelled" in result.content[0].text


async def test_a_run_that_finished_just_before_the_deadline_keeps_its_result(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deadline lands in the sleep between polls: the first poll sees `running`, the sleep
    outlives the budget, and by the recheck the run has completed. It is already paid for, so it
    must not be cancelled and reported as a timeout.

    Driven through `HostedRun.execute` rather than the tool, because entering `except TimeoutError`
    at all needs a sub-second budget that `timeout_minutes` cannot express."""
    monkeypatch.setattr(browser_use, "POLL_SECONDS", 5)
    api = _Api(statuses=["running", "completed"], files=[])
    workspace_id = await _keyed_workspace()
    ctx = _context(_Sandbox(), tmp_path)
    with ws(workspace_id):
        assert ctx.ext is not None
        outcome = await browser_use.HostedRun(
            credentials=ctx.ext.credentials,
            model=browser_use.TASK_MODEL,
            max_cost_usd=browser_use.TASK_MAX_COST_USD,
            save_outputs=True,
            transport=httpx.MockTransport(api.handle),
        ).execute(ctx, "t", timeout_seconds=0.05)
    assert api.sent("POST", f"/api/v4/runs/{RUN_ID}/cancel") == []
    assert outcome.status == "completed"
    assert outcome.output == "found it"


async def test_a_model_supplied_path_cannot_break_out_of_the_cat_command(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fast_poll: None
) -> None:
    """`sandbox.bash` runs a real shell, so a JSON-quoted path would leave `$(...)` live and let a
    tool argument run commands in the sandbox."""
    evil = 'entities.txt"; $(touch pwned) `id` $HOME'
    _wire(monkeypatch, _Api(statuses=["completed"]))
    workspace_id = await _keyed_workspace()
    sandbox = _Sandbox(files={evil: "a.test\n", "schema.json": "{}"})
    with ws(workspace_id):
        await _tool("wide_browse").handler(
            _context(sandbox, tmp_path),
            WideBrowseInput(
                entities_file=evil,
                prompt_template="visit {entity}",
                output_schema_file="schema.json",
            ),
        )
    assert sandbox.commands[0] == f"cat {shlex.quote(evil)}"
    assert sandbox.commands[0].startswith("cat '")
    assert '"; $(touch pwned)' not in sandbox.commands[0].replace(shlex.quote(evil), "")


async def test_a_vendor_side_cancellation_keeps_its_output_and_is_not_called_a_timeout(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fast_poll: None
) -> None:
    """A run the vendor cancels reports `cancelled` through an ordinary status poll. Sharing a name
    with our own timeout would discard everything it collected behind a false timeout message."""
    api = _Api(statuses=["cancelled"], result="", error="cancelled from the dashboard")
    _wire(monkeypatch, api)
    workspace_id = await _keyed_workspace()
    with ws(workspace_id):
        result = await _tool("browser_task").handler(
            _context(_Sandbox(), tmp_path),
            BrowserTaskInput(url="https://shop.test", task="t", task_name="Dashboard stop"),
        )
    assert not api.sent("POST", f"/api/v4/runs/{RUN_ID}/cancel")
    assert result.is_error
    assert "timeout" not in result.content[0].text
    assert json.loads(result.content[0].text)["result"] == "cancelled from the dashboard"


async def test_a_failed_run_surfaces_its_error_text_as_an_error(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fast_poll: None
) -> None:
    api = _Api(statuses=["failed"], error="site blocked the agent")
    _wire(monkeypatch, api)
    workspace_id = await _keyed_workspace()
    with ws(workspace_id):
        result = await _tool("browser_task").handler(
            _context(_Sandbox(), tmp_path),
            BrowserTaskInput(url="https://blocked.test", task="t", task_name="n"),
        )
    assert result.is_error
    assert json.loads(result.content[0].text)["result"] == "site blocked the agent"


async def test_an_output_file_past_the_size_bound_is_reported_not_written(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fast_poll: None
) -> None:
    api = _Api(
        files=[
            {"path": "small.csv", "size": 6, "url": DOWNLOAD_URL},
            {"path": "huge.csv", "size": browser_use.MAX_OUTPUT_BYTES + 1, "url": DOWNLOAD_URL},
        ]
    )
    _wire(monkeypatch, api)
    workspace_id = await _keyed_workspace()
    sandbox = _Sandbox()
    with ws(workspace_id):
        result = await _tool("browser_task").handler(
            _context(sandbox, tmp_path),
            BrowserTaskInput(url="https://shop.test", task="t", task_name="n"),
        )
    assert list(sandbox.writes) == ["/workspace/small.csv"]
    payload = json.loads(result.content[0].text)
    assert payload["files"] == ["small.csv"]
    assert payload["files_not_fetched"] == [
        {"path": "huge.csv", "size": browser_use.MAX_OUTPUT_BYTES + 1}
    ]


async def test_a_listing_the_count_bound_cut_short_says_so(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fast_poll: None
) -> None:
    """Files past the count bound never reach the listing at all, so there is no entry to report —
    the vendor's own hasMore is the only thing that keeps the accounting honest."""
    api = _Api(files=[{"path": "a.csv", "size": 6, "url": DOWNLOAD_URL}], has_more=True)
    _wire(monkeypatch, api)
    workspace_id = await _keyed_workspace()
    with ws(workspace_id):
        result = await _tool("browser_task").handler(
            _context(_Sandbox(), tmp_path),
            BrowserTaskInput(url="https://shop.test", task="t", task_name="n"),
        )
    assert json.loads(result.content[0].text)["more_files_exist"] is True


async def test_an_output_path_escaping_the_workspace_is_a_fault(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fast_poll: None
) -> None:
    api = _Api(files=[{"path": "../../etc/passwd", "size": 6, "url": DOWNLOAD_URL}])
    _wire(monkeypatch, api)
    workspace_id = await _keyed_workspace()
    sandbox = _Sandbox()
    with (
        ws(workspace_id),
        pytest.raises(browser_use.BrowserUseError, match="escapes the workspace"),
    ):
        await _tool("browser_task").handler(
            _context(sandbox, tmp_path),
            BrowserTaskInput(url="https://shop.test", task="t", task_name="n"),
        )
    assert not sandbox.writes


async def test_an_output_path_is_not_written_through_a_planted_symlink(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fast_poll: None
) -> None:
    """Through a real carrier: the vendor names a path under a directory the agent replaced with a
    link in its own workspace. The write is refused at that component, so a run's output cannot be
    steered onto a host file by a link the agent left behind."""
    api = _Api(files=[{"path": "out/data.csv", "size": 6, "url": DOWNLOAD_URL}])
    _wire(monkeypatch, api)
    workspace_id = await _keyed_workspace()
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    outside.mkdir()
    carrier = LocalCarrier()
    session = SandboxSession(
        carrier=carrier,
        handle=await carrier.create(
            SandboxSpec(
                conversation_id=uuid4(),
                image_ref="ufo-sandbox:latest",
                workspace_host_path=str(workspace),
                proxy=ProxyEndpoint(port=9999, ca_cert="CA-PEM"),
                run_token="run-token",
            )
        ),
    )
    (workspace / "out").symlink_to(outside)

    with ws(workspace_id), pytest.raises(ContainmentError):
        await _tool("browser_task").handler(
            _context(session, tmp_path),
            BrowserTaskInput(url="https://shop.test", task="t", task_name="n"),
        )

    assert list(outside.iterdir()) == []


async def test_a_failed_output_download_fails_loud_rather_than_writing_an_error_body(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fast_poll: None
) -> None:
    api = _Api(files=[{"path": "data.csv", "size": 6, "url": DOWNLOAD_URL}], download_status=403)
    _wire(monkeypatch, api)
    workspace_id = await _keyed_workspace()
    sandbox = _Sandbox()
    with ws(workspace_id), pytest.raises(browser_use.BrowserUseError, match="403"):
        await _tool("browser_task").handler(
            _context(sandbox, tmp_path),
            BrowserTaskInput(url="https://shop.test", task="t", task_name="n"),
        )
    assert not sandbox.writes


@pytest.mark.parametrize(
    ("listing", "expected"),
    [
        ({"files": "nope"}, "no files array"),
        ({"files": ["nope"]}, "non-object file"),
        ({"files": [{"size": 6}]}, "without a path and size"),
        ({"files": [{"path": "a.csv", "size": "big"}]}, "without a path and size"),
    ],
)
async def test_a_malformed_file_listing_fails_loud(
    listing: dict[str, object],
    expected: str,
    db: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fast_poll: None,
) -> None:
    api = _Api(listing=listing)
    _wire(monkeypatch, api)
    workspace_id = await _keyed_workspace()
    with ws(workspace_id), pytest.raises(browser_use.BrowserUseError, match=expected):
        await _tool("browser_task").handler(
            _context(_Sandbox(), tmp_path),
            BrowserTaskInput(url="https://shop.test", task="t", task_name="n"),
        )


@pytest.mark.parametrize(
    ("create_body", "expected"),
    [
        ({"status": "queued", "sessionId": SESSION_ID, "workspaceId": WORKSPACE_ID}, "no id"),
        ({"id": RUN_ID, "status": "queued", "sessionId": SESSION_ID}, "no workspaceId"),
    ],
)
async def test_a_create_response_missing_a_field_faults_as_a_browser_use_error(
    create_body: dict[str, object],
    expected: str,
    db: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fast_poll: None,
) -> None:
    _wire(monkeypatch, _Api(create_body=create_body))
    workspace_id = await _keyed_workspace()
    with ws(workspace_id), pytest.raises(browser_use.BrowserUseError, match=expected):
        await _tool("browser_task").handler(
            _context(_Sandbox(), tmp_path),
            BrowserTaskInput(url="https://shop.test", task="t", task_name="n"),
        )


async def test_a_2xx_answer_that_is_not_json_fails_loud(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fast_poll: None
) -> None:
    """Driven through the create call. Every response in the flow is read by the same `_json`, so
    one that answers 2xx with a body that is not JSON faults the same way wherever it arrives."""
    _wire(monkeypatch, _Api(create_text="<html>maintenance</html>"))
    workspace_id = await _keyed_workspace()
    with ws(workspace_id), pytest.raises(browser_use.BrowserUseError, match="non-JSON body"):
        await _tool("browser_task").handler(
            _context(_Sandbox(), tmp_path),
            BrowserTaskInput(url="https://shop.test", task="t", task_name="n"),
        )


async def test_a_non_2xx_answer_fails_loud_with_its_status_and_body(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fast_poll: None
) -> None:
    _wire(monkeypatch, _Api(create_status=402))
    workspace_id = await _keyed_workspace()
    with ws(workspace_id), pytest.raises(browser_use.BrowserUseError, match="402"):
        await _tool("browser_task").handler(
            _context(_Sandbox(), tmp_path),
            BrowserTaskInput(url="https://shop.test", task="t", task_name="n"),
        )


async def test_a_task_over_the_send_bound_is_refused_rather_than_truncated(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fast_poll: None
) -> None:
    """Truncating would silently cut a wide_browse schema off the end of the prompt, so the bound
    refuses instead."""
    api = _Api()
    _wire(monkeypatch, api)
    workspace_id = await _keyed_workspace()
    with ws(workspace_id), pytest.raises(ValueError, match="over the"):
        await _tool("browser_task").handler(
            _context(_Sandbox(), tmp_path),
            BrowserTaskInput(
                url="https://shop.test",
                task="x" * (browser_use.MAX_TASK_CHARS + 1),
                task_name="n",
            ),
        )
    assert not api.sent("POST", "/api/v4/runs")


async def test_wide_browse_runs_each_entity_on_the_batch_model_and_collects_rows(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fast_poll: None
) -> None:
    api = _Api(statuses=["completed"])
    _wire(monkeypatch, api)
    workspace_id = await _keyed_workspace()
    sandbox = _Sandbox(
        files={"entities.txt": "a.test\nb.test\na.test\n", "schema.json": '{"type": "object"}'}
    )
    with ws(workspace_id):
        result = await _tool("wide_browse").handler(
            _context(sandbox, tmp_path),
            WideBrowseInput(
                entities_file="entities.txt",
                prompt_template="visit {entity}",
                output_schema_file="schema.json",
            ),
        )

    creates = api.sent("POST", "/api/v4/runs")
    assert len(creates) == 2
    bodies = [json.loads(request.content) for request in creates]
    assert {body["model"] for body in bodies} == {"gemini-3.5-flash"}
    assert {body["maxCostUsd"] for body in bodies} == {browser_use.WIDE_BROWSE_MAX_COST_USD}
    assert sorted(body["task"].splitlines()[0] for body in bodies) == [
        "visit a.test",
        "visit b.test",
    ]
    assert all('{"type": "object"}' in body["task"] for body in bodies)

    assert not api.touched("/workspaces")
    rows = json.loads(sandbox.writes[browser_use.WIDE_BROWSE_OUTPUT])
    assert [row["entity"] for row in rows] == ["a.test", "b.test"]
    assert {row["status"] for row in rows} == {"completed"}
    assert json.loads(result.content[0].text)["output_file"] == browser_use.WIDE_BROWSE_OUTPUT


async def test_one_failing_entity_does_not_discard_its_siblings_paid_results(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fast_poll: None
) -> None:
    """A vendor fault on one of up to 128 entities must not throw away every sibling's already-paid
    result and leave no output file — a vendor-reported `failed` already survives as a row."""
    api = _Api(statuses=["completed"], fail_status_for={"b.test"})
    _wire(monkeypatch, api)
    workspace_id = await _keyed_workspace()
    sandbox = _Sandbox(files={"entities.txt": "a.test\nb.test\nc.test\n", "schema.json": "{}"})
    with ws(workspace_id):
        await _tool("wide_browse").handler(
            _context(sandbox, tmp_path),
            WideBrowseInput(
                entities_file="entities.txt",
                prompt_template="visit {entity}",
                output_schema_file="schema.json",
            ),
        )
    rows = {
        row["entity"]: row for row in json.loads(sandbox.writes[browser_use.WIDE_BROWSE_OUTPUT])
    }
    assert rows["a.test"]["status"] == "completed"
    assert rows["c.test"]["status"] == "completed"
    assert rows["b.test"]["status"] == "errored"
    assert "500" in rows["b.test"]["result"]


async def test_wide_browse_reattaches_a_recorded_run_instead_of_paying_twice(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fast_poll: None
) -> None:
    """A crash-recovery re-run reconnects to the run the first attempt already bought — the store
    key is what makes a repeated fan-out cost nothing extra."""
    api = _Api(statuses=["completed"])
    _wire(monkeypatch, api)
    workspace_id = await _keyed_workspace()
    sandbox = _Sandbox(files={"entities.txt": "a.test\n", "schema.json": "{}"})
    ctx = _context(sandbox, tmp_path, idempotency_key="idem")
    with ws(workspace_id):
        await ctx.ext.store.put("run/idem/a.test", {"id": RUN_ID, "workspace_id": WORKSPACE_ID})
        await _tool("wide_browse").handler(
            ctx,
            WideBrowseInput(
                entities_file="entities.txt",
                prompt_template="visit {entity}",
                output_schema_file="schema.json",
            ),
        )
    assert not api.sent("POST", "/api/v4/runs")
    assert api.sent("GET", f"/api/v4/runs/{RUN_ID}")


async def test_a_keyed_browser_task_reattaches_the_run_its_first_attempt_bought(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fast_poll: None
) -> None:
    api = _Api(statuses=["completed"])
    _wire(monkeypatch, api)
    workspace_id = await _keyed_workspace()
    ctx = _context(_Sandbox(), tmp_path, idempotency_key="idem")
    assert _tool("browser_task").side_effecting is True
    with ws(workspace_id):
        await ctx.ext.store.put("run/idem", {"id": RUN_ID, "workspace_id": WORKSPACE_ID})
        result = await _tool("browser_task").handler(
            ctx,
            BrowserTaskInput(
                url="https://shop.test",
                task="read the price",
                task_name="Price check",
            ),
        )
    assert not api.sent("POST", "/api/v4/runs")
    assert api.sent("GET", f"/api/v4/runs/{RUN_ID}")
    assert json.loads(result.content[0].text)["result"] == "found it"


@pytest.mark.parametrize("vendor_status", ["stopped", "cancelled"])
async def test_a_reattached_run_its_earlier_attempt_cancelled_reports_the_timeout(
    db: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fast_poll: None,
    vendor_status: str,
) -> None:
    api = _Api(statuses=[vendor_status])
    _wire(monkeypatch, api)
    workspace_id = await _keyed_workspace()
    ctx = _context(_Sandbox(), tmp_path, idempotency_key="idem")
    with ws(workspace_id):
        await ctx.ext.store.put(
            "run/idem", {"id": RUN_ID, "workspace_id": WORKSPACE_ID, "timed_out": True}
        )
        result = await _tool("browser_task").handler(
            ctx,
            BrowserTaskInput(
                url="https://shop.test",
                task="read the price",
                task_name="Price check",
            ),
        )
    assert not api.sent("POST", "/api/v4/runs")
    assert result.is_error
    assert "timeout" in result.content[0].text


async def test_a_reattached_run_the_vendor_ended_keeps_its_output(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fast_poll: None
) -> None:
    api = _Api(statuses=["cancelled"], result="", error="cancelled from the dashboard")
    _wire(monkeypatch, api)
    workspace_id = await _keyed_workspace()
    ctx = _context(_Sandbox(), tmp_path, idempotency_key="idem")
    with ws(workspace_id):
        await ctx.ext.store.put("run/idem", {"id": RUN_ID, "workspace_id": WORKSPACE_ID})
        result = await _tool("browser_task").handler(
            ctx,
            BrowserTaskInput(
                url="https://shop.test",
                task="read the price",
                task_name="Price check",
            ),
        )
    assert not api.sent("POST", "/api/v4/runs")
    assert result.is_error
    assert "timeout" not in result.content[0].text
    assert json.loads(result.content[0].text)["result"] == "cancelled from the dashboard"


async def test_a_keyed_timeout_cancel_marks_the_recorded_run(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fast_poll: None
) -> None:
    api = _Api(statuses=["running"])
    _wire(monkeypatch, api)
    workspace_id = await _keyed_workspace()
    ctx = _context(_Sandbox(), tmp_path, idempotency_key="idem")
    with ws(workspace_id):
        result = await _tool("browser_task").handler(
            ctx,
            BrowserTaskInput.model_construct(
                url="https://slow.test",
                task="t",
                task_name="Wedged",
                timeout_minutes=0,
            ),
        )
        recorded = await ctx.ext.store.get("run/idem")
    assert api.sent("POST", f"/api/v4/runs/{RUN_ID}/cancel")
    assert result.is_error
    assert browser_use.StartedRun.model_validate(recorded).timed_out is True


async def test_the_mark_is_durable_before_the_cancel_is_attempted(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fast_poll: None
) -> None:
    api = _Api(statuses=["running"], cancel_status=500)
    _wire(monkeypatch, api)
    workspace_id = await _keyed_workspace()
    ctx = _context(_Sandbox(), tmp_path, idempotency_key="idem")
    with ws(workspace_id):
        with pytest.raises(browser_use.BrowserUseError, match="500"):
            await _tool("browser_task").handler(
                ctx,
                BrowserTaskInput.model_construct(
                    url="https://slow.test",
                    task="t",
                    task_name="Wedged",
                    timeout_minutes=0,
                ),
            )
        recorded = await ctx.ext.store.get("run/idem")
    assert api.sent("POST", f"/api/v4/runs/{RUN_ID}/cancel")
    assert browser_use.StartedRun.model_validate(recorded).timed_out is True


async def test_a_keyed_run_records_its_handle_so_a_later_attempt_can_find_it(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fast_poll: None
) -> None:
    """The write half of the reattach: without this record the recovery read has nothing to find."""
    api = _Api(statuses=["completed"])
    _wire(monkeypatch, api)
    workspace_id = await _keyed_workspace()
    sandbox = _Sandbox(files={"entities.txt": "a.test\n", "schema.json": "{}"})
    ctx = _context(sandbox, tmp_path, idempotency_key="idem")
    with ws(workspace_id):
        await _tool("wide_browse").handler(
            ctx,
            WideBrowseInput(
                entities_file="entities.txt",
                prompt_template="visit {entity}",
                output_schema_file="schema.json",
            ),
        )
        recorded = await ctx.ext.store.get("run/idem/a.test")
    assert len(api.sent("POST", "/api/v4/runs")) == 1
    assert recorded == {"id": RUN_ID, "workspace_id": WORKSPACE_ID, "timed_out": False}
    assert browser_use.StartedRun.model_validate(recorded).id == RUN_ID


async def test_an_unreadable_schema_file_fails_loud_like_an_unreadable_entities_file(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fast_poll: None
) -> None:
    """Degrading to an empty schema would send every entity off unstructured and call it success."""
    api = _Api(statuses=["completed"])
    _wire(monkeypatch, api)
    workspace_id = await _keyed_workspace()
    with ws(workspace_id), pytest.raises(ValueError, match=r"not found|cannot read"):
        await _tool("wide_browse").handler(
            _context(_Sandbox(files={"entities.txt": "a.test\n"}), tmp_path),
            WideBrowseInput(
                entities_file="entities.txt",
                prompt_template="visit {entity}",
                output_schema_file="missing.json",
            ),
        )
    assert not api.sent("POST", "/api/v4/runs")


async def test_a_non_https_output_url_is_refused_before_it_is_fetched(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fast_poll: None
) -> None:
    """The url is the vendor's to choose; a plaintext internal address must never have its body
    written into the workspace."""
    api = _Api(files=[{"path": "data.csv", "size": 6, "url": "http://169.254.169.254/latest/meta"}])
    _wire(monkeypatch, api)
    workspace_id = await _keyed_workspace()
    sandbox = _Sandbox()
    with ws(workspace_id), pytest.raises(browser_use.BrowserUseError, match="not https"):
        await _tool("browser_task").handler(
            _context(sandbox, tmp_path),
            BrowserTaskInput(url="https://shop.test", task="t", task_name="n"),
        )
    assert not sandbox.writes
    assert not [r for r in api.requests if r.url.host == "169.254.169.254"]
