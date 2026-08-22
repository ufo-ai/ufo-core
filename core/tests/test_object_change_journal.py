import json
from dataclasses import dataclass, replace
from uuid import UUID

import pytest
import sqlalchemy as sa
import ufo_ext_sample as sample
from test_objects import (
    ADMIN_CREATED_AT,
    _member,
    _object_tools,
    _text,
    _tool_context,
    _widget_manifest,
    _workspace,
)

import ufo.objects
from ufo.db import workspace_tx
from ufo.ext.surface import SurfaceContext
from ufo.objects import UnknownObject
from ufo.schema import tables
from ufo.workspace import ws


async def _changes(workspace_id: object) -> list[sa.Row]:
    async with workspace_tx() as connection:
        result = await connection.execute(
            sa.select(tables.object_change)
            .where(tables.object_change.c.workspace_id == workspace_id)
            .order_by(tables.object_change.c.created_at)
        )
        return list(result)


async def test_verbs_journal_one_row_each_with_verb_and_caller(db: None) -> None:
    """A create, an update, and a delete through the object verbs each land one object_change row,
    naming the verb and the acting member as caller, with the before/after specs recorded."""
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        owner = await _member(workspace_id, ADMIN_CREATED_AT)
        ctx = _tool_context(workspace_id, speaker_member_id=owner)

        await _text(tools, "object_apply", ctx, manifest=_widget_manifest("anvil"))
        await _text(tools, "object_apply", ctx, manifest=_widget_manifest("anvil", color="red"))
        await _text(tools, "object_delete", ctx, kind=sample.WIDGET_KIND, name="anvil")

        rows = await _changes(workspace_id)

    assert [row.verb for row in rows] == ["create", "update", "delete"]
    assert {row.caller for row in rows} == {f"member:{owner}"}
    assert {row.kind for row in rows} == {sample.WIDGET_KIND}
    assert {row.name for row in rows} == {"anvil"}
    assert {row.agent_id for row in rows} == {ctx.turn.agent_id}

    create, update, delete = rows
    assert create.spec_before is None
    assert json.loads(create.spec_after)["color"] == "teal"
    assert json.loads(update.spec_before)["color"] == "teal"
    assert json.loads(update.spec_after)["color"] == "red"
    assert json.loads(delete.spec_before)["color"] == "red"
    assert delete.spec_after is None


async def test_speakerless_write_journals_the_turn_as_caller(db: None) -> None:
    """A write with no speaking member records the turn as caller, so an audit row always names
    who or what changed the object."""
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        ctx = _tool_context(workspace_id, speaker_member_id=None)
        await _text(tools, "object_apply", ctx, manifest=_widget_manifest("bolt"))
        rows = await _changes(workspace_id)

    assert [row.verb for row in rows] == ["create"]
    assert rows[0].caller == f"turn:{ctx.turn.id}"


async def test_a_rerun_of_one_dispatch_journals_once_with_the_true_before(db: None) -> None:
    """A crash-recovery re-run of one dispatch — same idempotency key — inserts nothing: the row
    written before the first mutation keeps verb `create` and no before-spec, which the re-run,
    reading the already-created row, could not recompute. A second call with its own key is its
    own change and journals again."""
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        ctx = replace(_tool_context(workspace_id), idempotency_key="turn/object_apply/call-1")
        await _text(tools, "object_apply", ctx, manifest=_widget_manifest("anvil"))
        await _text(tools, "object_apply", ctx, manifest=_widget_manifest("anvil"))
        rows = await _changes(workspace_id)
        assert [row.verb for row in rows] == ["create"]
        assert rows[0].spec_before is None

        second = replace(ctx, idempotency_key="turn/object_apply/call-2")
        await _text(tools, "object_apply", second, manifest=_widget_manifest("anvil", color="red"))
        rows = await _changes(workspace_id)

    assert [row.verb for row in rows] == ["create", "update"]
    assert json.loads(rows[1].spec_before)["color"] == "teal"


async def test_a_journal_failure_precedes_the_mutation(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The journal writes ahead of the store, so a journal failure surfaces as a write failure
    while nothing has committed — a reported failure is never a committed mutation."""
    workspace_id = await _workspace()
    tools = _object_tools()

    async def refuse(*args: object, **kwargs: object) -> UUID:
        raise RuntimeError("journal unavailable")

    monkeypatch.setattr(ufo.objects, "_journal_object_change", refuse)
    with ws(workspace_id):
        ctx = _tool_context(workspace_id)
        with pytest.raises(RuntimeError, match="journal unavailable"):
            await _text(tools, "object_apply", ctx, manifest=_widget_manifest("anvil"))
        with pytest.raises(UnknownObject):
            await _text(tools, "object_get", ctx, kind=sample.WIDGET_KIND, name="anvil")
        assert await _changes(workspace_id) == []


async def test_a_refused_mutation_withdraws_its_journal_row(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A store refusal rolls its own transaction back, so the row journaled ahead of it is
    withdrawn — the journal records what happened, never what was refused."""
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        ctx = _tool_context(workspace_id)
        await _text(tools, "object_apply", ctx, manifest=_widget_manifest("anvil"))

        async def refuse(*args: object, **kwargs: object) -> None:
            raise ValueError("the store refuses")

        monkeypatch.setattr(sample.WidgetStore, "apply", refuse)
        with pytest.raises(ValueError, match="the store refuses"):
            await _text(tools, "object_apply", ctx, manifest=_widget_manifest("anvil", color="red"))
        rows = await _changes(workspace_id)

    assert [row.verb for row in rows] == ["create"]


async def test_a_rerun_refusal_keeps_the_first_attempts_committed_row(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash-recovery re-run reuses the first attempt's idempotency key, so its journal call
    inserts nothing; when the store then refuses — the stale generation fence, the first attempt
    having committed and bumped the generation — the withdraw must not delete the row recording
    the mutation that happened."""
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        ctx = replace(_tool_context(workspace_id), idempotency_key="turn/object_apply/call-1")
        await _text(tools, "object_apply", ctx, manifest=_widget_manifest("anvil"))
        [committed] = await _changes(workspace_id)

        async def refuse(*args: object, **kwargs: object) -> None:
            raise ValueError("sample_widget 'anvil' changed while editing")

        monkeypatch.setattr(sample.WidgetStore, "apply", refuse)
        with pytest.raises(ValueError, match="changed while editing"):
            await _text(tools, "object_apply", ctx, manifest=_widget_manifest("anvil"))
        rows = await _changes(workspace_id)

    assert [row.id for row in rows] == [committed.id]
    assert rows[0].verb == "create"
    assert rows[0].spec_before is None


@dataclass(frozen=True)
class _Reader:
    workspace_id: UUID


async def test_the_audit_read_answers_aware_timestamps(db: None) -> None:
    """`recent_object_changes` normalizes `created_at` to UTC-aware, so the audit read renders the
    same instant whichever dialect stored it."""
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        ctx = _tool_context(workspace_id)
        await _text(tools, "object_apply", ctx, manifest=_widget_manifest("anvil"))
        changes = await SurfaceContext.recent_object_changes(_Reader(workspace_id), 10)

    assert len(changes) == 1
    assert changes[0].created_at.tzinfo is not None
    assert changes[0].agent_id == ctx.turn.agent_id
