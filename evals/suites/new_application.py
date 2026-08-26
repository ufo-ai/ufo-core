"""Creating an application in chat: the one answered form, and the row it writes.

An `agent` object is the one kind a member cannot undo, so every trial grades the durable row
rather than the reply — a right-sounding answer that wrote the wrong spec fails — and grades the
order of the turn, since a form answered after the write agreed to nothing. The last case is the
neighbour: an application the workspace already holds must be wired, never created a second time
and never rewritten, because applying its name is an update.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
import yaml
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from ufo_ext_sites.application_builder import (
    APPLICATION_BUILDER_DELEGATION_TOOL,
    APPLICATION_BUILDER_DEPLOY_TOOL,
    APPLICATION_BUILDER_DESIGN_TOOL,
    APPLICATION_BUILDER_NAME,
    APPLICATION_BUILDER_QA_TOOL,
    APPLICATION_BUILDER_WRITE_TOOL,
    APPLICATION_SOURCE_PATH,
    ApplicationBuilderResult,
    ApplicationBuilderTask,
)
from ufo_ext_sites.store import hosted_site
from ufo_ext_web.surface import SEED_PROMPT

from evals.harness.capability import (
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    ToolInvocation,
)
from evals.harness.memory_fence import forget_workspace_memory
from evals.harness.scenario import EvalSeed, ScenarioCase, ScenarioOutcome, ScenarioUser
from evals.harness.target import CapabilityTarget
from evals.suites.ufo_app_bench import APP_WORKSPACE_FILES
from ufo.db import workspace_tx
from ufo.ext.context import ScopedStore
from ufo.kinds.agents import AGENT_KIND
from ufo.models.interface import AUTO_MODEL
from ufo.objects import ENVELOPE_KEYS
from ufo.schema import tables
from ufo.schema.records import auto_agent_icon
from ufo.turns.audience import conversation_audience
from ufo.workspace import ws_current

SKILL = "create-application"
PREVIEW_TOOL = "render_application_preview"
WEB_EXTENSION = "web"
HOMEPAGE_SEED_PREFIX = "homepage-seed/"
SANDBOX_CONTAINER_PREFIX = "ufo-sbx-"
REPAIR_EVIDENCE_EXTENSION = "evals"
REPAIR_SOURCE_KEY = "application-repair/source/{application_id}"
REPAIR_BOUND_KEY = "application-repair/bound/{application_id}"
CONTRACT_CONNECTIVE_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "be",
        "can",
        "each",
        "every",
        "in",
        "it",
        "let",
        "member",
        "on",
        "one",
        "read",
        "s",
        "see",
        "show",
        "so",
        "the",
        "their",
        "this",
        "to",
        "what",
        "with",
        "you",
        "your",
    }
)
EXISTING_APPLICATION = "invoice-intake"
EXISTING_PROMPT = "You file invoices for the finance team. Ask before paying anything."
SATISFIED_INSTRUCTION = (
    "Answer what the assistant asks and approve what it proposes. Do not name the application "
    "yourself and do not write its instructions. End the conversation once it exists."
)
GUIDED_INSTRUCTION = (
    "Send exactly 'Build me a new app.' first. Choose Build that on the proposal. Accept the "
    "proposed job in the form and choose Just me. Choose Build it after the design preview. End "
    "the conversation once the application exists."
)
GUIDED_REVISION_INSTRUCTION = (
    "Send exactly 'Build me a new app.' first. Choose Build that on the proposal. Accept the "
    "proposed job in the form and choose Just me. On the first design, choose Change the design "
    "and ask it to put the overdue queue above the summary. Choose Build it on the revised design. "
    "End the conversation once the application exists. A preview is not the application. Never "
    "stop after a preview or design change."
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


def _guided_design_failure(output: CapabilityOutput, previews: int) -> str | None:
    applies = _agent_applies(output)
    if not applies:
        return "no application create follows the guided design"
    create = applies[0][0]
    asks = tuple(index for index in _asks(output) if index < create)
    preview_calls = tuple(
        (index, call)
        for index, call in enumerate(output.calls)
        if index < create and call.name == PREVIEW_TOOL and call.succeeded
    )
    delegated_previews = tuple(
        index
        for index, call in enumerate(output.calls)
        if index < create and call.name == "build_application_preview" and call.succeeded
    )
    parent_build_calls = tuple(
        call.name
        for index, call in enumerate(output.calls)
        if index < create
        and call.succeeded
        and call.name in {"bash", "read", "write", "edit", "start_server", "js_repl"}
    )
    parent_website_skill = any(
        index < create
        and call.name == "load_skill"
        and call.succeeded
        and call.input.get("name") == "website-building"
        for index, call in enumerate(output.calls)
    )
    if parent_build_calls:
        return f"the parent ran preview build tools: {', '.join(parent_build_calls)}"
    if parent_website_skill:
        return "the parent loaded website-building for preview work"
    if delegated_previews:
        return "the design preview used a model worker"
    if any(
        index < create and call.name == "share_file" and call.succeeded
        for index, call in enumerate(output.calls)
    ):
        return "the parent shared the product preview a second time"
    allowed_asks = {previews + 2, previews + 3}
    if len(asks) not in allowed_asks:
        expected = " or ".join(str(count) for count in sorted(allowed_asks))
        return f"the guided build used {len(asks)} asks before create, expected {expected}"
    if len(preview_calls) != previews:
        return f"the guided build rendered {len(preview_calls)} previews, expected {previews}"
    render_indexes = tuple(index for index, _ in preview_calls)
    design_asks = tuple(
        next((ask for ask in asks if ask > render), -1) for render in render_indexes
    )
    for position, (render, ask) in enumerate(zip(render_indexes, design_asks, strict=True)):
        if ask < 0:
            return f"preview {position + 1} has no later design choice"
        if render >= ask:
            return f"preview {position + 1} was not rendered before its design choice"
        if position and design_asks[position - 1] >= render:
            return f"preview {position + 1} was not built after the prior design choice"
    for position, (_, call) in enumerate(preview_calls):
        contract = call.input
        required = {
            "purpose",
            "first_screen_priority",
            "regions",
            "layout",
            "design_direction",
        }
        if missing := sorted(required - contract.keys()):
            return f"preview {position + 1} contract omits {', '.join(missing)}"
    if previews > 1:
        contract = preview_calls[-1][1].input
        regions = contract.get("regions", [])
        priority = str(contract.get("first_screen_priority", "")).casefold()
        first_region = str(regions[0]).casefold() if isinstance(regions, list) and regions else ""
        waiting_on_member = first_region.strip() == "waiting on you"
        if "overdue" not in priority or ("overdue" not in first_region and not waiting_on_member):
            return "the revised preview contract does not put the overdue queue first"
    return None


def _accepted_contract_failure(output: CapabilityOutput, prompt: str) -> str | None:
    previews = tuple(call for call in output.calls if call.name == PREVIEW_TOOL and call.succeeded)
    if not previews:
        return "the application has no accepted preview contract"
    contract = previews[-1].input
    regions = contract.get("regions", [])
    values = [
        contract.get("purpose", ""),
        contract.get("first_screen_priority", ""),
        contract.get("design_direction", ""),
        *(regions if isinstance(regions, list) else []),
    ]
    lowered = " ".join(prompt.casefold().split())
    prompt_terms = " ".join(
        term for term in re.findall(r"[a-z0-9]+", lowered) if term not in CONTRACT_CONNECTIVE_WORDS
    )
    missing = tuple(
        str(value)
        for value in values
        if str(value).strip()
        and " ".join(
            term
            for term in re.findall(r"[a-z0-9]+", str(value).casefold())
            if term not in CONTRACT_CONNECTIVE_WORDS
        )
        not in prompt_terms
    )
    layout = " ".join(
        term
        for term in re.findall(r"[a-z0-9]+", str(contract.get("layout", "")).casefold())
        if term not in CONTRACT_CONNECTIVE_WORDS
    )
    if layout and layout not in prompt_terms:
        missing = (*missing, layout)
    if missing:
        return f"the application prompt omits accepted design values: {', '.join(missing)}"
    return None


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


async def _prepare_created_homepage(
    outcome: ScenarioOutcome, target: CapabilityTarget
) -> tuple[sa.Row, UUID]:
    applies = _agent_applies(outcome.output)
    if len(applies) != 1:
        raise RuntimeError("homepage followup requires exactly one created application")
    async with workspace_tx() as connection:
        application = (
            await connection.execute(
                sa.select(tables.agent.c.id, tables.agent.c.owner_member_id).where(
                    tables.agent.c.workspace_id == ws_current().workspace_id,
                    tables.agent.c.name == applies[0][1],
                )
            )
        ).one()
    if application.owner_member_id is None:
        raise RuntimeError("created application has no owner for its homepage turn")
    await ScopedStore(extension=WEB_EXTENSION).put(
        f"{HOMEPAGE_SEED_PREFIX}{application.id}", str(application.owner_member_id)
    )
    key = f"homepage/{application.id}/{application.owner_member_id}"
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent)
            .where(
                tables.agent.c.workspace_id == ws_current().workspace_id,
                tables.agent.c.id == application.id,
            )
            .values(tools=[APPLICATION_BUILDER_DELEGATION_TOOL])
        )
        insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
        await connection.execute(
            insert(tables.conversation)
            .values(
                id=uuid4(),
                workspace_id=ws_current().workspace_id,
                agent_id=application.id,
                surface=WEB_EXTENSION,
                queue_key=key,
                member_id=application.owner_member_id,
                audience=str(conversation_audience(application.owner_member_id)),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
            .on_conflict_do_nothing(
                index_elements=[
                    tables.conversation.c.workspace_id,
                    tables.conversation.c.surface,
                    tables.conversation.c.queue_key,
                ]
            )
        )
        conversation_id = (
            await connection.execute(
                sa.select(tables.conversation.c.id).where(
                    tables.conversation.c.workspace_id == ws_current().workspace_id,
                    tables.conversation.c.surface == WEB_EXTENSION,
                    tables.conversation.c.queue_key == key,
                )
            )
        ).scalar_one()
    workspace = target.conversations.workspace_path(conversation_id, "")
    await asyncio.to_thread(workspace.mkdir, parents=True, exist_ok=True)
    for item in APP_WORKSPACE_FILES:
        path = workspace / item.path
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, item.content)
    await _sync_active_application_workspace(conversation_id, workspace / "ufo-app")
    return application, conversation_id


async def _sync_active_application_workspace(conversation_id: UUID, source: Path) -> None:
    container = f"{SANDBOX_CONTAINER_PREFIX}{conversation_id}"
    inspect = await asyncio.create_subprocess_exec(
        "docker",
        "inspect",
        container,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await inspect.communicate()
    if inspect.returncode != 0:
        return
    copied = await asyncio.create_subprocess_exec(
        "docker",
        "cp",
        f"{source}/.",
        f"{container}:/workspace/ufo-app",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await copied.communicate()
    if copied.returncode != 0:
        raise RuntimeError(stderr.decode().strip() or "active application workspace sync failed")


async def _build_created_homepage(outcome: ScenarioOutcome, target: CapabilityTarget):
    application, conversation_id = await _prepare_created_homepage(outcome, target)
    return await target.invoke(
        conversation_id,
        application.id,
        SEED_PROMPT,
        f"homepage-seed:{application.id}:{datetime.now(UTC).date().isoformat()}",
        on_behalf_of_member_id=application.owner_member_id,
        as_scheduled=True,
    )


async def _repair_created_homepage(outcome: ScenarioOutcome, target: CapabilityTarget):
    application, conversation_id = await _prepare_created_homepage(outcome, target)
    first = await target.invoke(
        conversation_id,
        application.id,
        SEED_PROMPT,
        f"homepage-seed:{application.id}:without-authority",
        on_behalf_of_member_id=None,
        as_scheduled=True,
    )
    workspace = target.conversations.workspace_path(conversation_id, "")
    source_path = workspace / APPLICATION_SOURCE_PATH.removeprefix("/workspace/")
    source_digest = ""
    if await asyncio.to_thread(source_path.is_file):
        source_digest = sha256(await asyncio.to_thread(source_path.read_bytes)).hexdigest()
    async with workspace_tx() as connection:
        homepage = (
            await connection.execute(
                sa.select(hosted_site.c.name).where(
                    hosted_site.c.workspace_id == ws_current().workspace_id,
                    hosted_site.c.homepage_agent_id == application.id,
                )
            )
        ).one_or_none()
    evidence = ScopedStore(extension=REPAIR_EVIDENCE_EXTENSION)
    await evidence.put(REPAIR_SOURCE_KEY.format(application_id=application.id), source_digest)
    await evidence.put(
        REPAIR_BOUND_KEY.format(application_id=application.id),
        "yes" if homepage is not None else "no",
    )
    second = await target.invoke(
        conversation_id,
        application.id,
        SEED_PROMPT,
        f"homepage-seed:{application.id}:with-authority",
        on_behalf_of_member_id=application.owner_member_id,
        as_scheduled=True,
    )
    return first, second


def _application_worker_tool_failure(calls: tuple[ToolInvocation, ...]) -> str | None:
    completed = frozenset(call.name for call in calls if call.succeeded)
    required = {
        APPLICATION_BUILDER_DEPLOY_TOOL,
        APPLICATION_BUILDER_DESIGN_TOOL,
        APPLICATION_BUILDER_QA_TOOL,
    }
    if missing := sorted(required - completed):
        return f"the Gemini worker did not complete: {', '.join(missing)}"
    if not any(call.name == APPLICATION_BUILDER_WRITE_TOOL for call in calls):
        return f"the Gemini worker did not call {APPLICATION_BUILDER_WRITE_TOOL}"
    if not any(call.name == APPLICATION_BUILDER_QA_TOOL and call.succeeded for call in calls):
        return "the Gemini worker completed no product QA batch"
    if any(call.name == "set_homepage" for call in calls):
        return "the Gemini worker tried to certify its own homepage"
    return None


async def _homepage_journey_failure(outcome: ScenarioOutcome) -> str | None:
    followup = outcome.followup
    if followup is None:
        return "the created application ran no homepage turn"
    delegations = tuple(
        call for call in followup.own_calls if call.name == APPLICATION_BUILDER_DELEGATION_TOOL
    )
    if not delegations or any(not call.succeeded for call in delegations):
        return f"the application made {len(delegations)} successful-or-failed build call(s)"
    if len({call.result for call in delegations}) != 1:
        return "the application parent received inconsistent cached build results"
    try:
        result = ApplicationBuilderResult.model_validate_json(delegations[0].result)
    except ValueError:
        return "the application parent received no structured build result"
    if result.status != "deployed":
        return f"the deterministic acceptance result was {result.status}: {result.blocker}"
    forbidden = {
        "list_external_tools",
        "describe_external_tools",
        "search_connector_tools",
        "call_external_tool",
        "read",
        "bash",
        "start_server",
        "js_repl",
        APPLICATION_BUILDER_WRITE_TOOL,
        "edit_application_source",
        "read_application_source",
        "deploy_website",
        "set_homepage",
        "build_website",
        "write",
        "edit",
    }
    parent_work = tuple(call.name for call in followup.own_calls if call.name in forbidden)
    if parent_work:
        return f"the Opus application parent entered the build loop: {', '.join(parent_work)}"
    if failure := _application_worker_tool_failure(followup.calls):
        return failure
    name = _agent_applies(outcome.output)[0][1]
    async with workspace_tx() as connection:
        application = (
            await connection.execute(
                sa.select(tables.agent.c.id, tables.agent.c.prompt).where(
                    tables.agent.c.workspace_id == ws_current().workspace_id,
                    tables.agent.c.name == name,
                )
            )
        ).one()
        homepage = (
            await connection.execute(
                sa.select(hosted_site.c.source_manifest).where(
                    hosted_site.c.workspace_id == ws_current().workspace_id,
                    hosted_site.c.homepage_agent_id == application.id,
                )
            )
        ).one_or_none()
        turns = (
            await connection.execute(
                sa.select(
                    tables.turn.c.parent_turn_id,
                    tables.turn.c.subagent_profile,
                    tables.turn.c.inbound,
                    tables.turn.c.status,
                ).where(
                    tables.turn.c.workspace_id == ws_current().workspace_id,
                    tables.turn.c.agent_id == application.id,
                )
            )
        ).all()
    if homepage is None or not homepage.source_manifest:
        return "the application has no bound homepage with retained source"
    parents = tuple(turn for turn in turns if turn.parent_turn_id is None)
    children = tuple(turn for turn in turns if turn.parent_turn_id is not None)
    if len(parents) != 1 or parents[0].inbound != SEED_PROMPT or parents[0].status != "done":
        return "the application did not run one successful scheduled homepage parent turn"
    if (
        len(children) != 1
        or children[0].subagent_profile != APPLICATION_BUILDER_NAME
        or children[0].status != "done"
    ):
        return "the homepage parent did not run one successful Gemini builder child"
    task = ApplicationBuilderTask.model_validate_json(children[0].inbound)
    if application.prompt not in task.objective:
        return "the Gemini task omitted the created application's instructions"
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


async def _graded_daily_brief(outcome: ScenarioOutcome) -> CapabilityVerdict:
    failure = await _creation_failure(outcome, "private")
    if failure is not None:
        return failure
    apply = _agent_applies(outcome.output)[0]
    row = await _application(apply[1])
    if row is None:
        return CapabilityVerdict(False, "the applied Daily Brief application is missing")
    prompt = row.prompt.lower()
    required = (
        "daily-brief",
        "configure_daily_brief",
        "sweep_newspaper",
        "scheduled",
        "markdown",
        "share_file",
        "homepage",
    )
    missing = tuple(value for value in required if value not in prompt)
    if missing:
        return CapabilityVerdict(False, f"application prompt omits {', '.join(missing)}")
    return CapabilityVerdict(True, "one private Daily Brief application holds the complete job")


async def _graded_guided_build(outcome: ScenarioOutcome) -> CapabilityVerdict:
    failure = await _creation_failure(outcome, "private")
    if failure is not None:
        return failure
    design_failure = _guided_design_failure(outcome.output, 1)
    if design_failure is not None:
        return CapabilityVerdict(False, design_failure)
    name = _agent_applies(outcome.output)[0][1]
    row = await _application(name)
    if row is None:
        return CapabilityVerdict(False, f"applied {name!r} but no such application stands")
    contract_failure = _accepted_contract_failure(outcome.output, row.prompt)
    if contract_failure is not None:
        return CapabilityVerdict(False, contract_failure)
    return CapabilityVerdict(True, "one proposal, interview, and preview precede the create")


async def _graded_guided_revision(outcome: ScenarioOutcome) -> CapabilityVerdict:
    failure = await _creation_failure(outcome, "private")
    if failure is not None:
        return failure
    design_failure = _guided_design_failure(outcome.output, 2)
    if design_failure is not None:
        return CapabilityVerdict(False, design_failure)
    name = _agent_applies(outcome.output)[0][1]
    row = await _application(name)
    if row is None:
        return CapabilityVerdict(False, f"applied {name!r} but no such application stands")
    contract_failure = _accepted_contract_failure(outcome.output, row.prompt)
    if contract_failure is not None:
        return CapabilityVerdict(False, contract_failure)
    prompt = row.prompt.casefold()
    if "overdue" not in prompt or "queue" not in prompt:
        return CapabilityVerdict(False, "the accepted overdue-queue revision is absent from prompt")
    return CapabilityVerdict(True, "the second preview and its accepted revision precede create")


async def _graded_named_journey(outcome: ScenarioOutcome) -> CapabilityVerdict:
    failure = await _creation_failure(outcome, "private")
    if failure is not None:
        return failure
    journey_failure = await _homepage_journey_failure(outcome)
    if journey_failure is not None:
        return CapabilityVerdict(False, journey_failure)
    return CapabilityVerdict(
        True,
        "one approval creates the application; one parent route and one Gemini build bind its page",
    )


async def _graded_guided_journey(outcome: ScenarioOutcome) -> CapabilityVerdict:
    creation = await _graded_guided_build(outcome)
    if not creation.passed:
        return creation
    journey_failure = await _homepage_journey_failure(outcome)
    if journey_failure is not None:
        return CapabilityVerdict(False, journey_failure)
    return CapabilityVerdict(
        True,
        "one proposal, interview, preview, parent route, and Gemini build produce one homepage",
    )


async def _graded_guided_revision_journey(outcome: ScenarioOutcome) -> CapabilityVerdict:
    creation = await _graded_guided_revision(outcome)
    if not creation.passed:
        return creation
    journey_failure = await _homepage_journey_failure(outcome)
    if journey_failure is not None:
        return CapabilityVerdict(False, journey_failure)
    return CapabilityVerdict(
        True,
        "one revised preview, parent route, and Gemini build produce one homepage",
    )


def _application_repair_tool_failure(
    first: CapabilityOutput, second: CapabilityOutput
) -> str | None:
    if not any(call.name == APPLICATION_BUILDER_WRITE_TOOL for call in first.calls):
        return "the failed attempt made no initial source write"
    if any(call.name == APPLICATION_BUILDER_DEPLOY_TOOL and call.succeeded for call in first.calls):
        return "the failed attempt deployed a site"
    if not any(
        call.name == APPLICATION_BUILDER_DEPLOY_TOOL and call.succeeded for call in second.calls
    ):
        return "the repair attempt deployed no site"
    return None


async def _graded_repair_journey(outcome: ScenarioOutcome) -> CapabilityVerdict:
    failure = await _creation_failure(outcome, "private")
    if failure is not None:
        return failure
    if len(outcome.followups) != 2:
        return CapabilityVerdict(False, f"the journey retained {len(outcome.followups)} build(s)")
    build_statuses: list[str] = []
    for followup in outcome.followups:
        calls = tuple(
            call
            for call in followup.own_calls
            if call.name == APPLICATION_BUILDER_DELEGATION_TOOL and call.succeeded
        )
        if not calls:
            return CapabilityVerdict(
                False,
                "an application parent made no successful build call",
            )
        results = {call.result for call in calls}
        if len(results) != 1:
            return CapabilityVerdict(False, "an application parent received inconsistent results")
        result = yaml.safe_load(results.pop())
        if not isinstance(result, dict) or not isinstance(result.get("status"), str):
            return CapabilityVerdict(False, "a build delegation returned no status")
        build_statuses.append(result["status"])
    if build_statuses != ["blocked", "deployed"]:
        return CapabilityVerdict(False, f"build statuses were {build_statuses}")
    if any(call.name == "set_homepage" for call in outcome.output.calls):
        return CapabilityVerdict(False, "a Gemini worker tried to certify its own homepage")
    first, second = outcome.followups
    if repair_failure := _application_repair_tool_failure(first, second):
        return CapabilityVerdict(False, repair_failure)
    name = _agent_applies(outcome.output)[0][1]
    async with workspace_tx() as connection:
        application = (
            await connection.execute(
                sa.select(tables.agent.c.id).where(
                    tables.agent.c.workspace_id == ws_current().workspace_id,
                    tables.agent.c.name == name,
                )
            )
        ).one()
        homepage = (
            await connection.execute(
                sa.select(hosted_site.c.source_manifest).where(
                    hosted_site.c.workspace_id == ws_current().workspace_id,
                    hosted_site.c.homepage_agent_id == application.id,
                )
            )
        ).one_or_none()
        turns = (
            await connection.execute(
                sa.select(tables.turn.c.parent_turn_id, tables.turn.c.subagent_profile).where(
                    tables.turn.c.workspace_id == ws_current().workspace_id,
                    tables.turn.c.agent_id == application.id,
                )
            )
        ).all()
    evidence = ScopedStore(extension=REPAIR_EVIDENCE_EXTENSION)
    first_source = await evidence.get(REPAIR_SOURCE_KEY.format(application_id=application.id))
    first_bound = await evidence.get(REPAIR_BOUND_KEY.format(application_id=application.id))
    if not isinstance(first_source, str) or len(first_source) != 64:
        return CapabilityVerdict(False, "the failed attempt retained no source digest")
    if first_bound != "no":
        return CapabilityVerdict(False, "the failed attempt bound a homepage")
    if homepage is None or not homepage.source_manifest:
        return CapabilityVerdict(False, "the repair attempt bound no retained homepage")
    parents = tuple(turn for turn in turns if turn.parent_turn_id is None)
    children = tuple(turn for turn in turns if turn.parent_turn_id is not None)
    if len(parents) != 2 or len(children) != 2:
        return CapabilityVerdict(
            False, f"the repair used {len(parents)} parent(s) and {len(children)} worker(s)"
        )
    if any(turn.subagent_profile != APPLICATION_BUILDER_NAME for turn in children):
        return CapabilityVerdict(False, "a repair worker used the wrong profile")
    return CapabilityVerdict(
        True,
        "one blocked build retains evidence; one later Gemini build binds the same application",
    )


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
    ScenarioCase(
        "A04-daily-brief",
        ScenarioUser(
            reason_for_call="You want a private Daily Brief application that reviews your work "
            "each weekday morning, publishes each edition in Radar, and keeps its homepage "
            "current.",
            known_info="Only you use it. Sweep is installed.",
            task_instructions=SATISFIED_INSTRUCTION,
        ),
        DescribedGrader(
            "one private application prompt carries the Daily Brief skill, Sweep tool, scheduled "
            "Markdown publication, and homepage job",
            _graded_daily_brief,
        ),
        seed=_seeded(),
        rubric=(
            "The assistant creates an ordinary member-owned application, not an extension-owned "
            "or provisioned agent.",
            "The application registers its private conversation before it creates the recurring "
            "task.",
            "The assistant says scheduling happens in the new application's own conversation, "
            "where one recurring task keeps reporting.",
            "The application prompt keeps task and memory suggestions as drafts until the member "
            "approves them in a later turn.",
        ),
        digest_tag="new-application:daily-brief",
    ),
    ScenarioCase(
        "A05-guided-build",
        ScenarioUser(
            reason_for_call="You opened New application without deciding what job it should do.",
            task_instructions=GUIDED_INSTRUCTION,
        ),
        DescribedGrader(
            "one proposal, one standard interview, and one shared design preview precede the "
            "private application create",
            _graded_guided_build,
        ),
        seed=_seeded(),
        rubric=(
            "The assistant proposes one concrete application instead of asking the member what "
            "to build.",
        ),
        digest_tag="new-application:guided-build",
    ),
    ScenarioCase(
        "A06-guided-revision",
        ScenarioUser(
            reason_for_call="You opened New application without deciding what job it should do.",
            task_instructions=GUIDED_REVISION_INSTRUCTION,
        ),
        DescribedGrader(
            "two ordered design previews precede the create and the accepted overdue-queue "
            "revision reaches the durable application prompt",
            _graded_guided_revision,
        ),
        seed=_seeded(),
        rubric=(
            "The assistant changes the existing design instead of starting the interview again.",
            "The assistant creates nothing before the member accepts the revised design.",
        ),
        digest_tag="new-application:guided-revision",
    ),
    ScenarioCase(
        "A07-named-homepage-journey",
        ScenarioUser(
            reason_for_call="You want a private application that prepares one compact brief before "
            "each customer call from calendar and email context.",
            known_info="Only you use it.",
            task_instructions=(
                f"{SATISFIED_INSTRUCTION} If asked whether to connect accounts first, choose to "
                "create the application now and attach the accounts later. Do not wait for a "
                "connection."
            ),
        ),
        DescribedGrader(
            "one approved application runs one parent routing turn and one complete Gemini "
            "homepage build, then holds one bound page with retained source",
            _graded_named_journey,
        ),
        seed=_seeded(),
        rubric=(
            "The creation reply states what the application still needs and does not claim that "
            "the homepage build was verified by Opus.",
        ),
        digest_tag="new-application:named-homepage-journey",
        followup=_build_created_homepage,
    ),
    ScenarioCase(
        "A08-guided-homepage-journey",
        ScenarioUser(
            reason_for_call="You opened New application without deciding what job it should do.",
            task_instructions=GUIDED_INSTRUCTION,
        ),
        DescribedGrader(
            "one proposal, one standard interview, one shared preview, one parent route, and one "
            "Gemini build produce a bound homepage with retained source",
            _graded_guided_journey,
        ),
        seed=_seeded(),
        rubric=(
            "The assistant proposes one concrete application instead of asking the member what "
            "to build.",
            "No parent turn claims to inspect or verify the Gemini build.",
        ),
        digest_tag="new-application:guided-homepage-journey",
        followup=_build_created_homepage,
    ),
    ScenarioCase(
        "A09-revised-homepage-journey",
        ScenarioUser(
            reason_for_call="You opened New application without deciding what job it should do.",
            task_instructions=GUIDED_REVISION_INSTRUCTION,
        ),
        DescribedGrader(
            "two ordered previews carry the accepted revision into one application, one parent "
            "route, and one Gemini-built bound homepage with retained source",
            _graded_guided_revision_journey,
        ),
        seed=_seeded(),
        rubric=(
            "The assistant changes the existing design instead of starting the interview again.",
            "No parent turn claims to inspect or verify the Gemini build.",
        ),
        digest_tag="new-application:revised-homepage-journey",
        followup=_build_created_homepage,
    ),
    ScenarioCase(
        "A10-failed-repaired-homepage-journey",
        ScenarioUser(
            reason_for_call="You want a private application that prepares one compact brief before "
            "each customer call from calendar and email context.",
            known_info="Only you use it.",
            task_instructions=(
                f"{SATISFIED_INSTRUCTION} If asked whether to connect accounts first, choose to "
                "create the application now and attach the accounts later. Do not wait for a "
                "connection."
            ),
        ),
        DescribedGrader(
            "one blocked build retains its source and evidence without binding; one later Gemini "
            "build repairs the same application and binds its first retained homepage",
            _graded_repair_journey,
        ),
        max_turns=2,
        seed=_seeded(),
        rubric=(
            "The creation reply states what the application still needs.",
            "No parent turn claims to inspect or verify either Gemini build.",
        ),
        digest_tag="new-application:failed-repaired-homepage-journey",
        followup=_repair_created_homepage,
    ),
)
