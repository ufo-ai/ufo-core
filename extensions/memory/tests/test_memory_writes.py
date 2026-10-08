"""The write tools where the deploy selects the memory service: what each sends the service and
what the model is handed back."""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import ufo_ext_memory.manifest as memory
from ufo_testsupport.cloud import cloud_apis_for
from ufo_testsupport.memory_service import MEMORY_WIRE, MemoryServiceStandIn
from ufo_testsupport.service_stand_in import SentRequest

from ufo.blob import FilesystemBlobStore
from ufo.runtime.billing.accounting import MEMORY_SERVICE
from ufo.runtime.ext.context import context_for
from ufo.runtime.tools.context import SpawnResult, ToolContext, ToolResult
from ufo.runtime.workspace import ws
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import Audience, conversation_audience
from ufo.sdk.objects import ObjectRef

GOLDEN: dict[str, dict] = json.loads(MEMORY_WIRE.read_text(encoding="utf-8"))
WRITTEN: dict = GOLDEN["memory.write"]["answer"]["body"]
CORRECTION: dict = GOLDEN["memory.correct"]["answer"]["body"]
MEMORY_TOOLS = {tool.name: tool for tool in memory.manifest().tools}
SELECTED = frozenset({MEMORY_SERVICE})
REFUSAL = (
    "No memory was saved. `deprecates` name 'launch date' must appear verbatim in `body`. Retry "
    "with that name in `body` and keep it in `deprecates`; omitting `deprecates` leaves the old "
    "memory active."
)
BODY = "Acme Corp — the launch moved to March."


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise RuntimeError("spawn is not wired in the memory write tests")


def _ctx(stand_in: MemoryServiceStandIn, tmp_path: Path) -> ToolContext:
    member = uuid4()
    audience: Audience = conversation_audience(member)
    return ToolContext(
        sandbox=None,
        blob=FilesystemBlobStore(root=tmp_path),
        turn=Turn(
            id=uuid4(),
            workspace_id=uuid4(),
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="hi",
            created_at=datetime(2026, 7, 9, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=member,
        audience=audience,
        artifact_token_secret="",
        ext=context_for(
            memory.NAME,
            frozenset(),
            audience=audience,
            cloud_client=True,
            cloud=cloud_apis_for(stand_in.app, clients=SELECTED),
        ),
    )


async def _run(name: str, ctx: ToolContext, **args: object) -> ToolResult:
    tool = MEMORY_TOOLS[name]
    with ws(ctx.turn.workspace_id):
        return await tool.handler(ctx, tool.input_model.model_validate(args))


def _sent(stand_in: MemoryServiceStandIn, operation: str) -> list[SentRequest]:
    return [sent for sent in stand_in.sent if sent.operation == operation]


def _refuse_writes(stand_in: MemoryServiceStandIn) -> None:
    stand_in.answer("memory.write", 400, {"error": {"code": "invalid_request", "message": REFUSAL}})


async def test_memory_update_sends_its_audience_kind_class_and_declaration(
    tmp_path: Path,
) -> None:
    stand_in = MemoryServiceStandIn()
    ctx = _ctx(stand_in, tmp_path)

    await _run(
        "memory_update",
        ctx,
        body=BODY,
        item_class="episodic",
        memory_kind="decision",
        confidence=7,
        source_ref="the launch thread",
        deprecates=["launch moved"],
    )

    (sent,) = _sent(stand_in, "memory.write")
    assert sent.body == {
        "subject": str(ctx.effective_audience),
        "body": BODY,
        "kind": "decision",
        "item_class": "episodic",
        "confidence": 7,
        "source_ref": "the launch thread",
        "deprecates": ["launch moved"],
        "conversation_id": str(ctx.turn.conversation_id),
    }


async def test_memory_update_leaves_out_what_it_was_not_given(tmp_path: Path) -> None:
    stand_in = MemoryServiceStandIn()
    ctx = _ctx(stand_in, tmp_path)

    await _run("memory_update", ctx, body=BODY)

    (sent,) = _sent(stand_in, "memory.write")
    assert sent.body == {
        "subject": str(ctx.effective_audience),
        "body": BODY,
        "kind": "fact",
        "item_class": "fact",
        "confidence": 5,
        "conversation_id": str(ctx.turn.conversation_id),
    }


async def test_memory_update_names_the_item_it_wrote(tmp_path: Path) -> None:
    stand_in = MemoryServiceStandIn()
    ctx = _ctx(stand_in, tmp_path)

    result = await _run("memory_update", ctx, body=BODY)

    assert result.content[0].text == f"Remembered ({ctx.effective_audience})."
    assert result.created == (ObjectRef(kind="memory", name=WRITTEN["id"]),)


async def test_a_refused_declaration_reaches_the_model_verbatim(tmp_path: Path) -> None:
    stand_in = MemoryServiceStandIn()
    _refuse_writes(stand_in)
    ctx = _ctx(stand_in, tmp_path)

    with pytest.raises(ValueError) as refused:
        await _run("memory_update", ctx, body=BODY, deprecates=["launch date"])

    assert str(refused.value) == REFUSAL


async def test_record_correction_sends_the_correctors_audience(tmp_path: Path) -> None:
    stand_in = MemoryServiceStandIn()
    ctx = _ctx(stand_in, tmp_path)
    corrected = uuid4()

    result = await _run("record_correction", ctx, corrects=str(corrected), body=BODY)

    (sent,) = _sent(stand_in, "memory.correct")
    assert sent.path == f"/v1/memory/memories/{corrected}"
    assert sent.body == {
        "body": BODY,
        "subject": str(ctx.effective_audience),
        "conversation_id": str(ctx.turn.conversation_id),
    }
    assert result.content[0].text == f"Remembered ({ctx.effective_audience})."
    assert result.created == (ObjectRef(kind="memory", name=CORRECTION["id"]),)


async def test_every_tool_write_names_the_conversation_it_was_written_in(tmp_path: Path) -> None:
    stand_in = MemoryServiceStandIn()
    ctx = _ctx(stand_in, tmp_path)

    await _run("memory_update", ctx, body=BODY)
    await _run("record_correction", ctx, corrects=str(uuid4()), body=BODY)
    await _run("record_first_run", ctx, body="The team uses Linear.")

    assert [sent.operation for sent in stand_in.sent] == [
        "memory.write",
        "memory.correct",
        "memory.write",
    ]
    assert {UUID(sent.body["conversation_id"]) for sent in stand_in.sent} == {
        ctx.turn.conversation_id
    }


async def test_record_first_run_marks_its_source_ref(tmp_path: Path) -> None:
    stand_in = MemoryServiceStandIn()
    ctx = _ctx(stand_in, tmp_path)

    result = await _run("record_first_run", ctx, body="The team uses Linear.")

    (sent,) = _sent(stand_in, "memory.write")
    assert sent.body == {
        "subject": str(ctx.effective_audience),
        "body": "The team uses Linear.",
        "kind": "fact",
        "item_class": "fact",
        "confidence": 5,
        "source_ref": "first run",
        "conversation_id": str(ctx.turn.conversation_id),
    }
    assert result.content[0].text == f"Remembered ({ctx.effective_audience})."


async def test_no_memory_body_reaches_a_log_record(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    stand_in = MemoryServiceStandIn()
    ctx = _ctx(stand_in, tmp_path)

    with caplog.at_level(logging.DEBUG):
        await _run("memory_update", ctx, body=BODY)
        _refuse_writes(stand_in)
        with pytest.raises(ValueError):
            await _run("memory_update", ctx, body=BODY, deprecates=["launch date"])

    assert len(_sent(stand_in, "memory.write")) == 2
    for record in caplog.records:
        assert BODY not in record.getMessage()
        assert BODY not in json.dumps(record.__dict__, default=str)
