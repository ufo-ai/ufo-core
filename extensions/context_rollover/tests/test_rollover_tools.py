"""The two tools this strategy ships, exercised through the registry a turn dispatches against.

They live here because the behavior they name is the boundary's own: a deploy that selects another
strategy offers neither, so neither belongs to the builtin set core owns."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from ufo_ext_context_rollover.rollover import SandboxJournal
from ufo_ext_context_rollover.tools import ROLLOVER_TOOLS

from ufo.blob import FilesystemBlobStore
from ufo.harness.context import ContextRemaining
from ufo.harness.sandbox.conversation import SANDBOX_IMAGE_REF
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import ProxyEndpoint, SandboxSession, SandboxSpec
from ufo.runtime.tools.context import SpawnResult, TextContent, ToolContext, ToolResult
from ufo.runtime.tools.registry import ToolRegistry
from ufo.runtime.turns.audience import conversation_audience
from ufo.schema.records import Agent, Turn

REGISTRY = ToolRegistry(ROLLOVER_TOOLS)
HANDOFF_CAP = 100
CHECKLIST_CAP = 20


async def _unavailable_spawn(
    profile: str,
    payload: dict[str, object],
    background: bool = False,
    dedup_key: str | None = None,
) -> SpawnResult:
    raise RuntimeError("spawn is not wired in this context")


class StubContextControl:
    """The window as the tools reach it: the two caps the acknowledgement applies, nothing else."""

    def remaining(self) -> ContextRemaining:
        return ContextRemaining(
            used_tokens=0,
            rollover_at_tokens=0,
            tokens_until_rollover=0,
            hard_limit_tokens=0,
            tokens_until_hard_limit=0,
        )

    def handoff_cap(self) -> int:
        return HANDOFF_CAP

    def checklist_cap(self) -> int:
        return CHECKLIST_CAP


async def _session(workspace: Path) -> SandboxSession:
    carrier = LocalCarrier()
    handle = await carrier.create(
        SandboxSpec(
            conversation_id=uuid4(),
            image_ref=SANDBOX_IMAGE_REF,
            workspace_host_path=str(workspace),
            proxy=ProxyEndpoint(port=0, ca_cert="test-ca"),
            run_token="rollover-tools-test",
        )
    )
    return SandboxSession(carrier=carrier, handle=handle)


def _context(sandbox: SandboxSession, tmp_path: Path) -> ToolContext:
    turn = Turn(
        id=uuid4(),
        workspace_id=uuid4(),
        conversation_id=uuid4(),
        agent_id=uuid4(),
        seq=0,
        status="running",
        inbound="hello",
        created_at=datetime(2026, 7, 9, tzinfo=UTC),
    )
    return ToolContext(
        sandbox=sandbox,
        blob=FilesystemBlobStore(root=tmp_path),
        turn=turn,
        agent=Agent(prompt="be terse", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=None,
        audience=conversation_audience(None),
        artifact_token_secret="rollover-tools-secret",
        context=StubContextControl(),
    )


async def _run(name: str, ctx: ToolContext, **args: object) -> ToolResult:
    tool = REGISTRY.get(name)
    return await tool.handler(ctx, tool.input_model.model_validate(args))


def _text(result: ToolResult) -> str:
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text


async def _journal_lines(ctx: ToolContext, lines: tuple[str, ...]) -> None:
    conversation_id: UUID = ctx.turn.conversation_id
    assert isinstance(ctx.sandbox, SandboxSession)
    await SandboxJournal(ctx.sandbox, conversation_id).append(lines, after=0)


async def test_new_context_acknowledges_the_reset_and_reports_what_it_kept(tmp_path: Path) -> None:
    ctx = _context(await _session(tmp_path / "workspace"), tmp_path)

    kept = json.loads(
        _text(await _run("new_context", ctx, handoff="  done: A. next: B.  ", checklist=("one",)))
    )
    assert kept == {
        "reset": "after this tool batch commits",
        "handoff_chars_kept": len("done: A. next: B."),
        "handoff_trimmed": False,
        "checklist_lines_kept": 1,
        "checklist_trimmed": False,
    }

    trimmed = json.loads(
        _text(
            await _run(
                "new_context",
                ctx,
                handoff="H" * (HANDOFF_CAP * 2),
                checklist=("a" * CHECKLIST_CAP, "b" * CHECKLIST_CAP),
            )
        )
    )
    assert trimmed["handoff_chars_kept"] == HANDOFF_CAP
    assert trimmed["handoff_trimmed"] is True
    assert trimmed["checklist_lines_kept"] == 1
    assert trimmed["checklist_trimmed"] is True


async def test_search_history_pages_the_sandbox_history_file_newest_first(tmp_path: Path) -> None:
    ctx = _context(await _session(tmp_path / "workspace"), tmp_path)

    absent = _text(await _run("search_history", ctx, query="batch window"))
    assert absent.startswith("no history file at ")

    notes = tuple(
        json.dumps({"role": "user", "text": f"note {n}: the Email batch window is {9000 + n} ms"})
        for n in range(1, 26)
    )
    long = json.dumps({"role": "assistant", "text": "x" * 3000 + " needle here " + "y" * 3000})
    await _journal_lines(ctx, (*notes, long))

    lines = _text(await _run("search_history", ctx, query="email BATCH window")).splitlines()
    assert lines[0] == "matches: 25  page 1 of 2  newest first"
    assert lines[1].startswith("[line 25] ") and "9025 ms" in lines[1]
    assert lines[20].startswith("[line 6] ")
    assert lines[21] == "next page: 2"

    second = _text(await _run("search_history", ctx, query="email batch window", page=2))
    assert [line.split("]")[0] for line in second.splitlines()[1:]] == [
        f"[line {n}" for n in range(5, 0, -1)
    ]

    cut = _text(await _run("search_history", ctx, query="NEEDLE here"))
    hit = cut.splitlines()[1]
    assert hit.startswith("[line 26] ...") and hit.endswith("...") and " needle here " in hit
    assert len(hit) < 500
