"""The browser_use pack end to end against the REAL Browser Use API — the proof the recorder cannot
give, since every shape the run flow reads was derived from their OpenAPI document rather than
observed.

`browser_task` runs one cheap real objective against a stable page and must come back with the
page's content and a terminal status. It costs real money per run, so the objective is trivial and
`maxCostUsd` is floored to the cheapest run that can still complete. Skips with a clear reason where
no key is present, so the suite stays collectable everywhere."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_browser_use as browser_use
from cryptography.fernet import Fernet
from ufo_ext_browser_use import BrowserTaskInput

from ufo.blob import FilesystemBlobStore
from ufo.credentials import CredentialStore
from ufo.db import workspace_tx
from ufo.ext.context import context_for
from ufo.sandbox.session import ExecResult
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import conversation_audience
from ufo.tools.context import ToolContext
from ufo.workspace import init_workspace_credentials, ws

LIVE_KEY_ENV = "BROWSER_USE_API_KEY"
LIVE_PAGE_URL = "https://example.com"
LIVE_TASK = "Report the exact text of the page's main heading, then stop."
LIVE_MAX_COST_USD = 0.25


class _NullSandbox:
    async def bash(self, command: str, timeout_s: int = 120) -> ExecResult:
        return ExecResult(stdout="", stderr="", exit_code=1)

    async def write_file(self, path: str, content: bytes) -> None:
        self.written = (path, content)


async def _live_workspace(key: str) -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    await store.put(workspace_id, browser_use.API_KEY_SLOT, key)
    return workspace_id


async def test_a_real_run_returns_a_result_and_a_live_view(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = os.environ.get(LIVE_KEY_ENV)
    if not key:
        pytest.skip(f"{LIVE_KEY_ENV} is not set; the live Browser Use proof needs a real key")
    monkeypatch.setattr(browser_use, "TASK_MAX_COST_USD", LIVE_MAX_COST_USD)
    workspace_id = await _live_workspace(key)
    ctx = ToolContext(
        sandbox=_NullSandbox(),
        blob=FilesystemBlobStore(root=tmp_path),
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="hi",
            created_at=datetime.now(UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=None,
        speaker_member_id=None,
        audience=conversation_audience(None),
        artifact_token_secret="",
        ext=context_for(browser_use.NAME, frozenset({browser_use.API_KEY_SLOT})),
    )
    tool = next(t for t in browser_use.BROWSER_USE_TOOLS if t.name == "browser_task")
    with ws(workspace_id):
        result = await tool.handler(
            ctx,
            BrowserTaskInput(
                url=LIVE_PAGE_URL,
                task=LIVE_TASK,
                task_name="Live heading read",
                user_description="live proof",
            ),
        )

    assert not result.is_error, result.content[0].text
    payload = json.loads(result.content[0].text)
    assert payload["result"].strip()
    assert "example domain" in payload["result"].lower()
