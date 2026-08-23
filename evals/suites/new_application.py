"""Creating an application in chat: the one answered form, and the row it writes.

An `agent` object is the one kind a member cannot undo, so every trial grades the durable row
rather than the reply — a right-sounding answer that wrote the wrong spec fails — and grades the
order of the turn, since a form answered after the write agreed to nothing. The last case is the
neighbour: an application the workspace already holds must be wired, never created a second time
and never rewritten, because applying its name is an update.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import sqlalchemy as sa
import yaml
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from evals.harness.capability import CapabilityOutput, CapabilityVerdict, DescribedGrader
from evals.harness.memory_fence import forget_workspace_memory
from evals.harness.scenario import EvalSeed, ScenarioCase, ScenarioOutcome, ScenarioUser
from ufo.db import workspace_tx
from ufo.kinds.agents import AGENT_KIND
from ufo.models.interface import AUTO_MODEL
from ufo.objects import ENVELOPE_KEYS
from ufo.schema import tables
from ufo.schema.records import auto_agent_icon
from ufo.workspace import ws_current

SKILL = "create-application"
EXISTING_APPLICATION = "invoice-intake"
EXISTING_PROMPT = "You file invoices for the finance team. Ask before paying anything."
SATISFIED_INSTRUCTION = (
    "Answer what the assistant asks and approve what it proposes. Do not name the application "
    "yourself and do not write its instructions. End the conversation once it exists."
)


async def _application(name: str) -> sa.Row | None:
    """One application as its durable row, by the name this conversation's own apply wrote — so an
    application another trial left in the workspace can never stand in this case's count."""
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(
                    tables.agent.c.name,
                    tables.agent.c.prompt,
                    tables.agent.c.model,
                    tables.agent.c.reasoning,
                    tables.agent.c.visibility,
                ).where(
                    tables.agent.c.workspace_id == ws_current().workspace_id,
                    tables.agent.c.name == name,
                    tables.agent.c.is_main.is_(False),
                )
            )
        ).one_or_none()


async def _fixture_row() -> sa.Row | None:
    """The suite's own fixture as it stands, by name."""
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.agent.c.name, tables.agent.c.prompt).where(
                    tables.agent.c.workspace_id == ws_current().workspace_id,
                    tables.agent.c.name == EXISTING_APPLICATION,
                    tables.agent.c.is_main.is_(False),
                )
            )
        ).one_or_none()


def _seeded(*existing: str) -> EvalSeed:
    """Clear the applications an earlier trial created, then seed the ones this case starts with.
    Only a member-owned application with no conversation is cleared, so a provisioned agent the
    deploy shipped and anything a member has actually talked to both survive — except a row
    bearing the suite's own fixture name, which the seed takes back to fixture state in one
    upsert. One statement, because the name is not this seed's alone: A02's member asks for an
    invoice-reading application and the assistant picks the name, so a row called invoice-intake
    can commit between any two statements this seed runs — a reclaim-then-insert leaves exactly
    that window, and the unique-key raise it ends in discards the whole run's records. A leftover
    it cannot clear it leaves standing — deleting would chase every table that references a
    talked-to agent, and disowning changes the workspace the next case routes in — so the graders
    read this conversation's own applies instead of counting the workspace."""

    async def seed(workspace_id: UUID, _agent_id: UUID) -> None:
        await forget_workspace_memory()
        async with workspace_tx() as connection:
            spare = (
                (
                    await connection.execute(
                        sa.select(tables.agent.c.id).where(
                            tables.agent.c.workspace_id == workspace_id,
                            tables.agent.c.is_main.is_(False),
                            tables.agent.c.owner_member_id.is_not(None),
                            ~sa.select(tables.conversation.c.id)
                            .where(tables.conversation.c.agent_id == tables.agent.c.id)
                            .exists(),
                        )
                    )
                )
                .scalars()
                .all()
            )
            if spare:
                await connection.execute(
                    sa.delete(tables.connector_grant).where(
                        tables.connector_grant.c.workspace_id == workspace_id,
                        tables.connector_grant.c.agent_id.in_(spare),
                    )
                )
                await connection.execute(
                    sa.delete(tables.agent).where(tables.agent.c.id.in_(spare))
                )
            if not existing:
                return
            owner = (
                await connection.execute(
                    sa.select(tables.member.c.id)
                    .where(
                        tables.member.c.workspace_id == workspace_id,
                        tables.member.c.is_admin.is_(True),
                    )
                    .order_by(tables.member.c.created_at)
                    .limit(1)
                )
            ).scalar_one()
            insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
            for name in existing:
                fixture = {
                    "prompt": EXISTING_PROMPT,
                    "model": AUTO_MODEL,
                    "reasoning": "auto",
                    "visibility": "private",
                    "internet_access_allowed": True,
                    "sandbox_size": "small",
                    "owner_member_id": owner,
                }
                await connection.execute(
                    insert(tables.agent)
                    .values(
                        id=uuid4(),
                        workspace_id=workspace_id,
                        name=name,
                        icon=auto_agent_icon(name, ()),
                        is_main=False,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                        **fixture,
                    )
                    .on_conflict_do_update(
                        index_elements=[tables.agent.c.workspace_id, tables.agent.c.name],
                        set_={"updated_at": sa.func.now(), **fixture},
                        where=tables.agent.c.is_main.is_(False),
                    )
                )

    return seed


def _agent_applies(output: CapabilityOutput) -> tuple[tuple[int, str], ...]:
    """Where in the trajectory a successful object_apply carried a well-formed agent manifest, and
    the name each applied."""
    applies = []
    for index, call in enumerate(output.calls):
        if call.name != "object_apply" or not call.succeeded:
            continue
        try:
            document = yaml.safe_load(str(call.input.get("manifest", "")))
        except yaml.YAMLError:
            continue
        if (
            isinstance(document, dict)
            and set(document) == ENVELOPE_KEYS
            and document.get("kind") == AGENT_KIND
        ):
            applies.append((index, str(document.get("name", ""))))
    return tuple(applies)


def _asks(output: CapabilityOutput) -> tuple[int, ...]:
    return tuple(
        index
        for index, call in enumerate(output.calls)
        if call.name == "ask_user" and call.succeeded
    )


def _interviews(output: CapabilityOutput) -> tuple[int, ...]:
    """The asks that stood before the create — the form the member's submission agreed to. An ask
    after the create is the closing move the skill itself teaches (offer to attach an account) and
    counts toward nothing here."""
    applies = _agent_applies(output)
    return tuple(index for index in _asks(output) if index < applies[0][0]) if applies else ()


async def _creation_failure(outcome: ScenarioOutcome, visibility: str) -> CapabilityVerdict | None:
    """What is wrong with the one application this conversation should have created, or None."""
    if not any(
        call.name == "load_skill" and call.succeeded and str(call.input.get("name", "")) == SKILL
        for call in outcome.output.calls
    ):
        return CapabilityVerdict(False, f"never loaded {SKILL!r}")
    applies = _agent_applies(outcome.output)
    if not applies:
        return CapabilityVerdict(False, "no successful object_apply carried an agent manifest")
    names = list(dict.fromkeys(name for _, name in applies))
    if len(names) != 1:
        return CapabilityVerdict(False, f"expected one new application, applied {names}")
    row = await _application(names[0])
    if row is None:
        return CapabilityVerdict(False, f"applied {names[0]!r} but no such application stands")
    if not _interviews(outcome.output):
        return CapabilityVerdict(False, "created the application before confirming it")
    if (row.model, row.reasoning) != (AUTO_MODEL, "auto"):
        return CapabilityVerdict(
            False, f"{row.name} runs model {row.model!r} at reasoning {row.reasoning!r}"
        )
    if row.visibility != visibility:
        return CapabilityVerdict(
            False, f"{row.name} is {row.visibility!r}, the member asked for {visibility!r}"
        )
    if not row.prompt.strip():
        return CapabilityVerdict(False, f"{row.name} has an empty prompt")
    return None


async def _graded_support_desk(outcome: ScenarioOutcome) -> CapabilityVerdict:
    failure = await _creation_failure(outcome, "workspace")
    if failure is not None:
        return failure
    interviews = _interviews(outcome.output)
    if len(interviews) != 1:
        return CapabilityVerdict(
            False, f"{len(interviews)} ask_user rounds before the create, exactly one"
        )
    return CapabilityVerdict(True, "one workspace application created from one answered form")


async def _graded_stated_up_front(outcome: ScenarioOutcome) -> CapabilityVerdict:
    failure = await _creation_failure(outcome, "private")
    if failure is not None:
        return failure
    interviews = _interviews(outcome.output)
    if len(interviews) != 1:
        return CapabilityVerdict(
            False,
            f"{len(interviews)} ask_user rounds before the create; the form is asked "
            "once, prefilled",
        )
    return CapabilityVerdict(True, "one private application created from one answered form")


async def _graded_existing_untouched(outcome: ScenarioOutcome) -> CapabilityVerdict:
    fixture = await _fixture_row()
    if fixture is None:
        return CapabilityVerdict(False, f"{EXISTING_APPLICATION!r} is gone")
    if fixture.prompt != EXISTING_PROMPT:
        return CapabilityVerdict(False, f"{EXISTING_APPLICATION} was rewritten")
    if applies := _agent_applies(outcome.output):
        return CapabilityVerdict(False, f"applied {len(applies)} agent manifest(s) to a wiring ask")
    return CapabilityVerdict(True, f"{EXISTING_APPLICATION} survived unchanged")


SCENARIOS = (
    ScenarioCase(
        "A01-support-desk",
        ScenarioUser(
            reason_for_call="You want the support team to have an application of its own that "
            "answers the common product questions and passes anything else to a person.",
            known_info="The whole support team uses it, not just you.",
            task_instructions=SATISFIED_INSTRUCTION,
        ),
        DescribedGrader(
            "one workspace-visible application lands on auto model and auto reasoning, written "
            "only after the form comes back, from exactly one ask_user round",
            _graded_support_desk,
        ),
        seed=_seeded(),
        rubric=(
            "The assistant proposed the name and the instructions itself rather than asking the "
            "member to supply them.",
            "The assistant never asks the member how much the application may do on its own, or "
            "when it should run.",
            "Before the application is created, the assistant states the boundary it wrote: the "
            "application handles the routine work of its job itself and brings anything unusual to "
            "a person.",
            "After creating it, the assistant says the new application holds no connected "
            "accounts, credentials, sources, skills, or memory yet, and offers to attach an "
            "account the workspace already has.",
            "The assistant never offers to delete the application or undo the create.",
        ),
        digest_tag="new-application:support-desk",
    ),
    ScenarioCase(
        "A02-stated-up-front",
        ScenarioUser(
            reason_for_call="You want a private application of your own that reads the invoices "
            "landing in your shared inbox, files the totals, and asks you before anything "
            "unusual. Nobody else should see it.",
            task_instructions="Say all of that in your first message. Approve what the assistant "
            "proposes. If it asks something you already told it, answer briefly and say you "
            "already said so. End the conversation once the application exists.",
        ),
        DescribedGrader(
            "the member who stated the job up front is still asked once, prefilled, and gets a "
            "private application on auto model and auto reasoning",
            _graded_stated_up_front,
        ),
        seed=_seeded(),
        rubric=(
            "The assistant never asks the member how much the application may do on its own, or "
            "when it should run.",
        ),
        digest_tag="new-application:stated-up-front",
    ),
    ScenarioCase(
        "A03-existing-application",
        ScenarioUser(
            reason_for_call=f"The {EXISTING_APPLICATION} application you already have cannot see "
            "the shared inbox, and you want it hooked up.",
            known_info=f"It is called {EXISTING_APPLICATION} and it already exists.",
            task_instructions="You do not want a new application; you want the one you have to "
            "work. End the conversation once the assistant has explained what it needs from you.",
        ),
        DescribedGrader(
            f"{EXISTING_APPLICATION} survives unchanged and no agent manifest is applied",
            _graded_existing_untouched,
        ),
        seed=_seeded(EXISTING_APPLICATION),
        rubric=(
            "The assistant never creates a second application and never claims to have created "
            "one.",
        ),
        digest_tag="new-application:existing-application",
    ),
)
