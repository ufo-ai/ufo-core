"""The turn-end changes recorder: targets mined from the rounds' tool calls, the scan asked only
where they and the last recorded scan point, and the projection row the portal reads."""

import asyncio
import json
from uuid import UUID, uuid4

import sqlalchemy as sa

from ufo.db import workspace_tx
from ufo.models.interface import ToolUseBlock
from ufo.sandbox.session import ProxyEndpoint, SandboxSession, SandboxSpec
from ufo.sandbox.terminal import TerminalCarrier, TerminalOp, Terminals
from ufo.schema import tables
from ufo.workspace_changes import (
    WORKSPACE_CHANGE_TARGET_DIRS_MAX,
    WorkspaceChange,
    WorkspaceChangeRecorder,
    WorkspaceChanges,
    change_targets,
    recorded_workspace_changes,
)


def test_change_targets_mines_file_tool_paths_and_the_root_for_bash() -> None:
    """`write` and `edit` calls name their files and a `bash` call names the workspace root,
    deduped in call order; a read, a block with no parseable path, and a path escaping the
    workspace name nothing."""
    calls = (
        ToolUseBlock(id="1", name="write", input={"file_path": "/workspace/src/a.py"}),
        ToolUseBlock(id="2", name="edit", input={"file_path": "notes/b.md"}),
        ToolUseBlock(id="3", name="read", input={"file_path": "/workspace/c.txt"}),
        ToolUseBlock(id="4", name="write", input={"file_path": "/workspace/src/a.py"}),
        ToolUseBlock(id="5", name="write", input={"file_path": "/workspace/../out.txt"}),
        ToolUseBlock(id="6", name="write", input={"content": "no path"}),
        ToolUseBlock(id="7", name="bash", input={"command": "sed -i s/x/y/ mod.py"}),
    )
    assert change_targets(calls) == ("src/a.py", "notes/b.md", ".")


async def _seeded_conversation() -> tuple[UUID, UUID]:
    workspace_id, member_id, agent_id, conversation_id = (uuid4() for _ in range(4))
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="a@b.c",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="cli",
                queue_key=uuid4().hex,
                member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, conversation_id


async def _answer(terminals: Terminals, conversation_id: UUID, reply: bytes) -> TerminalOp:
    op = await terminals.next_op(conversation_id)
    assert terminals.resolve(conversation_id, op.op_id, reply)
    return op


async def test_record_scans_where_targets_and_the_last_scan_point(db: None) -> None:
    """The enumeration receives the target directories plus the last scan's own, sorted and
    deduped, and the fresh scan replaces the stored row."""
    workspace_id, conversation_id = await _seeded_conversation()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation_change).values(
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                scan=WorkspaceChanges(
                    changes=(WorkspaceChange(path="repoa/mod.py", patch="", truncated=False),),
                    truncated=False,
                ).model_dump(mode="json"),
            )
        )
    terminals = Terminals()
    carrier = TerminalCarrier(terminals=terminals)
    terminals.connect(conversation_id, "/p", None)
    handle = await carrier.create(_spec(conversation_id))
    recorder = WorkspaceChangeRecorder(
        sandbox=SandboxSession(carrier=carrier, handle=handle),
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        targets=("src/new.py", "repoa/other.py"),
    )

    recording = asyncio.ensure_future(recorder.record())
    exec_ok = json.dumps({"exit_code": 0, "stdout_b64": "", "stderr_b64": ""}).encode()
    listing = await _answer(terminals, conversation_id, exec_ok)
    assert json.loads(listing.params)["argv"][3:] == ["sh", "repoa", "src"]
    fresh = {
        "changes": [{"path": "src/new.py", "patch": "+new", "truncated": False}],
        "truncated": False,
    }
    op = await _answer(terminals, conversation_id, json.dumps(fresh).encode())
    assert op.kind == "fileop" and op.name == "changes"
    await recording

    assert await recorded_workspace_changes(conversation_id) == WorkspaceChanges(
        changes=(WorkspaceChange(path="src/new.py", patch="+new", truncated=False),),
        truncated=False,
    )


async def test_record_with_nothing_watched_stores_an_empty_scan(db: None) -> None:
    """No targets and no prior scan ask the member's machine nothing: no op travels, and the row
    still lands so the portal reads `no changes` from a fact."""
    workspace_id, conversation_id = await _seeded_conversation()
    terminals = Terminals()
    carrier = TerminalCarrier(terminals=terminals)
    terminals.connect(conversation_id, "/p", None)
    handle = await carrier.create(_spec(conversation_id))

    await WorkspaceChangeRecorder(
        sandbox=SandboxSession(carrier=carrier, handle=handle),
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        targets=(),
    ).record()

    assert terminals.in_flight(conversation_id) is None
    assert await recorded_workspace_changes(conversation_id) == WorkspaceChanges(
        changes=(), truncated=False
    )


async def test_concurrent_recorders_for_one_sandbox_both_land(db: None) -> None:
    """A parent and a background subagent share one sandbox and can end together. The slower
    recorder read the row before the faster one stored, so its scan never asked about the faster
    one's checkout — the store must keep those entries rather than replace them away."""
    workspace_id, conversation_id = await _seeded_conversation()
    terminals = Terminals()
    carrier = TerminalCarrier(terminals=terminals)
    terminals.connect(conversation_id, "/p", None)
    handle = await carrier.create(_spec(conversation_id))
    sandbox = SandboxSession(carrier=carrier, handle=handle)

    slow = WorkspaceChangeRecorder(
        sandbox=sandbox,
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        targets=("src/y.py",),
    )
    slow_recording = asyncio.ensure_future(slow.record())
    exec_ok = json.dumps({"exit_code": 0, "stdout_b64": "", "stderr_b64": ""}).encode()
    listing = await _answer(terminals, conversation_id, exec_ok)
    assert json.loads(listing.params)["argv"][3:] == ["sh", "src"]

    fast = WorkspaceChangeRecorder(
        sandbox=sandbox,
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        targets=("repox/x.py",),
    )
    fast_recording = asyncio.ensure_future(fast.record())
    await asyncio.sleep(0.05)

    slow_scan = {
        "changes": [{"path": "src/y.py", "patch": "+y", "truncated": False}],
        "truncated": False,
    }
    await _answer(terminals, conversation_id, json.dumps(slow_scan).encode())
    await slow_recording
    await _answer(terminals, conversation_id, exec_ok)
    fast_scan = {
        "changes": [{"path": "repox/x.py", "patch": "+x", "truncated": False}],
        "truncated": False,
    }
    await _answer(terminals, conversation_id, json.dumps(fast_scan).encode())
    await fast_recording

    assert await recorded_workspace_changes(conversation_id) == WorkspaceChanges(
        changes=(
            WorkspaceChange(path="repox/x.py", patch="+x", truncated=False),
            WorkspaceChange(path="src/y.py", patch="+y", truncated=False),
        ),
        truncated=False,
    )


async def test_the_watched_directory_list_is_bounded(db: None) -> None:
    workspace_id, conversation_id = await _seeded_conversation()
    terminals = Terminals()
    carrier = TerminalCarrier(terminals=terminals)
    terminals.connect(conversation_id, "/p", None)
    handle = await carrier.create(_spec(conversation_id))
    recorder = WorkspaceChangeRecorder(
        sandbox=SandboxSession(carrier=carrier, handle=handle),
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        targets=tuple(f"d{index:03}/f.py" for index in range(300)),
    )

    recording = asyncio.ensure_future(recorder.record())
    exec_ok = json.dumps({"exit_code": 0, "stdout_b64": "", "stderr_b64": ""}).encode()
    listing = await _answer(terminals, conversation_id, exec_ok)
    assert len(json.loads(listing.params)["argv"][3:]) == 1 + WORKSPACE_CHANGE_TARGET_DIRS_MAX
    await _answer(terminals, conversation_id, b'{"changes": [], "truncated": false}')
    await recording


def _spec(conversation_id: UUID) -> SandboxSpec:
    return SandboxSpec(
        conversation_id=conversation_id,
        image_ref="unused",
        workspace_host_path="/p",
        proxy=ProxyEndpoint(port=8080, ca_cert="ca-pem", public_url=None),
        run_token="run-token",
        env={"UFO_CONVERSATION_ID": str(conversation_id)},
    )
