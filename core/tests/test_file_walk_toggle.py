"""The opt-out for the workspace tree walk — `terminal.walk_files` and its `UFO_NO_FILE_WALK`
override — across the three layers that hold it: how the deploy resolves the setting, what a
walking file op does on a terminal carrier that carries it off, and what the turn-end changes scan
records when the walk it needs never runs.

The client's `list files` row is the label of the walk's own exec op, so a walk that sends no op is
also a walk the member never sees; every carrier assertion here is about which ops reach the
terminal.
"""

import asyncio
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from ufo.config import NO_FILE_WALK_ENV, Config, load_config, terminal_walks_files
from ufo.sandbox.session import (
    WORKSPACE_DIR,
    FileWalkDisabled,
    ProxyEndpoint,
    SandboxHandle,
    SandboxSession,
    SandboxSpec,
)
from ufo.sandbox.terminal import TerminalCarrier, TerminalOp, Terminals
from ufo.workspace_changes import WorkspaceChange, WorkspaceChangeRecorder, WorkspaceChanges

CONFIG = """
[database]
url = "sqlite+aiosqlite:///ufo.db"

[blob]
backend = "filesystem"
root = "./blobs"
"""
EXEC_OK = json.dumps({"exit_code": 0, "stdout_b64": "", "stderr_b64": ""}).encode()
SCANNED = json.dumps(
    {
        "type": "changes",
        "changes": [{"path": "mod.py", "patch": "+x = 2", "truncated": False}],
        "truncated": False,
    }
).encode()


def _config(tmp_path: Path, walk_files: str = "") -> Config:
    path = tmp_path / "ufo.toml"
    path.write_text(CONFIG + walk_files)
    return load_config(path)


def _spec(conversation_id: UUID, cwd: str) -> SandboxSpec:
    return SandboxSpec(
        conversation_id=conversation_id,
        image_ref="unused",
        workspace_host_path=cwd,
        proxy=ProxyEndpoint(port=8080, ca_cert="ca-pem", public_url=None),
        run_token="run-token",
    )


async def _answer(terminals: Terminals, conversation_id: UUID, reply: bytes) -> TerminalOp:
    op = await terminals.next_op(conversation_id)
    assert terminals.resolve(conversation_id, op.op_id, reply)
    return op


async def _bound(walk_files: bool) -> tuple[Terminals, TerminalCarrier, UUID, SandboxHandle]:
    """A conversation bound to a connected terminal, on a carrier holding the toggle."""
    terminals = Terminals()
    carrier = TerminalCarrier(terminals=terminals, walk_files=walk_files)
    conversation_id = uuid4()
    terminals.connect(conversation_id, "/p", None)
    handle = await carrier.create(_spec(conversation_id, "/p"))
    return terminals, carrier, conversation_id, handle


def test_the_walk_is_on_when_no_deploy_says_otherwise(tmp_path: Path) -> None:
    assert terminal_walks_files(_config(tmp_path)) is True


def test_the_config_key_turns_the_walk_off(tmp_path: Path) -> None:
    assert terminal_walks_files(_config(tmp_path, "\n[terminal]\nwalk_files = false\n")) is False


def test_the_env_opt_out_turns_the_walk_off_and_dropping_it_turns_it_back_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The env var is the opt-out a member sets on their own node, and it outranks a config key that
    says to walk. Nothing about setting it is sticky: dropping it leaves the config key answering
    again, which is how a deploy that turned the walk off gets it back."""
    walking = _config(tmp_path)
    monkeypatch.setenv(NO_FILE_WALK_ENV, "1")
    assert terminal_walks_files(walking) is False
    monkeypatch.delenv(NO_FILE_WALK_ENV)
    assert terminal_walks_files(walking) is True


async def test_a_walk_off_sends_no_op_and_names_the_setting() -> None:
    """Each walking op refuses, and the refusal is not an empty answer: the client is asked for
    nothing, so the tree is never read and no row is painted. The op that follows proves it — it is
    the first op the terminal sees."""
    terminals, carrier, conversation_id, handle = await _bound(walk_files=False)

    for op, params in (
        ("grep", {"pattern": "x", "path": WORKSPACE_DIR, "workspace": WORKSPACE_DIR}),
        ("glob", {"pattern": "**/*", "path": WORKSPACE_DIR, "workspace": WORKSPACE_DIR}),
        ("changes", {"workspace": WORKSPACE_DIR}),
    ):
        with pytest.raises(FileWalkDisabled, match=f"the {op} walk"):
            await carrier.file_op(handle, op, dict(params))

    reading = asyncio.ensure_future(
        carrier.file_op(
            handle,
            "read",
            {"path": f"{WORKSPACE_DIR}/mod.py", "workspace": WORKSPACE_DIR},
        )
    )
    await asyncio.sleep(0)
    first = await _answer(terminals, conversation_id, b'{"content": "1\\tx = 1"}')
    assert (first.kind, first.name) == ("fileop", "read")
    assert await reading == {"content": "1\tx = 1"}


async def test_a_walk_back_on_enumerates_again() -> None:
    """The setting is read per carrier, so a deploy that turned the walk off and then back on walks
    the very next op — the refusal left nothing behind."""
    terminals, off, conversation_id, handle = await _bound(walk_files=False)
    with pytest.raises(FileWalkDisabled):
        await off.file_op(
            handle,
            "grep",
            {"pattern": "x", "path": WORKSPACE_DIR, "workspace": WORKSPACE_DIR},
        )

    on = TerminalCarrier(terminals=terminals, walk_files=True)
    running = asyncio.ensure_future(
        on.file_op(
            handle, "grep", {"pattern": "x", "path": WORKSPACE_DIR, "workspace": WORKSPACE_DIR}
        )
    )
    await asyncio.sleep(0)
    listing = await _answer(terminals, conversation_id, EXEC_OK)
    assert listing.kind == "exec"
    assert "UFO_WALK_ROOT=/p\n" in json.loads(listing.params)["argv"][2]
    op = await _answer(terminals, conversation_id, b'{"matches": []}')
    assert (op.kind, op.name) == ("fileop", "grep")
    assert json.loads(op.params)["enum"] == "grep-enum"
    assert await running == {"matches": []}


async def test_the_turn_end_scan_is_skipped_while_the_walk_is_off_and_lands_once_it_is_back_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scan the engine runs at every turn end is the `changes` walk, and it is the one a member
    sees with no file work in the turn. Off, nothing is scanned and nothing is stored, so the
    projection the portal reads keeps its last answer rather than being overwritten with `nothing
    changed`. Back on, the next turn's scan lands."""
    stored: list[WorkspaceChanges] = []

    async def _store(self: WorkspaceChangeRecorder, scanned: WorkspaceChanges) -> None:
        stored.append(scanned)

    monkeypatch.setattr(WorkspaceChangeRecorder, "_store", _store)
    terminals, off, conversation_id, handle = await _bound(walk_files=False)
    workspace_id = uuid4()
    await WorkspaceChangeRecorder(
        sandbox=SandboxSession(carrier=off, handle=handle),
        workspace_id=workspace_id,
        conversation_id=conversation_id,
    ).record()
    assert stored == []

    on = TerminalCarrier(terminals=terminals, walk_files=True)
    recording = asyncio.ensure_future(
        WorkspaceChangeRecorder(
            sandbox=SandboxSession(carrier=on, handle=handle),
            workspace_id=workspace_id,
            conversation_id=conversation_id,
        ).record()
    )
    await asyncio.sleep(0)
    await _answer(terminals, conversation_id, EXEC_OK)
    await _answer(terminals, conversation_id, SCANNED)
    await recording
    assert stored == [
        WorkspaceChanges(
            changes=(WorkspaceChange(path="mod.py", patch="+x = 2", truncated=False),),
            truncated=False,
        )
    ]
