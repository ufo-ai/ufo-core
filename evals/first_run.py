"""First-run cases for four recipes, one selected application, and its forward offer."""

from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa
import yaml

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilitySeed,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
)
from ufo.agents import AGENT_KIND
from ufo.blob import BlobStore
from ufo.db import workspace_tx
from ufo.objects import ENVELOPE_KEYS
from ufo.schema import tables

FIRST_RUN_PACKS = ("assistant", "assistant_billing", "assistant_hosted")
FIRST_RUN_SKILL = "first-run"
APPLICATION_SKILL = "create-application"
ASK_TOOL = "ask_user"
APPLY_TOOL = "object_apply"
LIST_TOOL = "object_list"
LOAD_TOOL = "load_skill"
MEMORY_TOOL = "memory_search"
READ_TOOL = "read"

HANDOVER = "We use Slack, GitHub, Gmail, Google Calendar, and Drive. What could you set up for us?"
SELECTED = "This is our first app. We use Slack and GitHub. Set up the pull request babysitter."
RECIPE_LABELS = (
    "AI news review",
    "Pull request babysitter",
    "Competitive intel digest",
    "What we learned",
)
PULL_REQUEST_RECIPE = "references/recipes/pull-request-babysitter.md"
PULL_REQUEST_APPLICATION = "pull-request-babysitter"
INTERVIEW_ANSWER = (
    "Set up the app you proposed. It may do routine work and ask about the rest. "
    "Everyone in the workspace can use it."
)


def _context_indexes(output: CapabilityOutput) -> tuple[list[int], list[int], dict[str, int]]:
    memory = [
        index
        for index, call in enumerate(output.calls)
        if call.name == MEMORY_TOOL and call.succeeded
    ]
    applications = [
        index
        for index, call in enumerate(output.calls)
        if call.name == LIST_TOOL and call.input.get("kind") == "agent" and call.succeeded
    ]
    loaded = {
        str(call.input.get("name")): index
        for index, call in enumerate(output.calls)
        if call.name == LOAD_TOOL and call.succeeded
    }
    return memory, applications, loaded


def _four_recipe_offer_scorer() -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        memory, applications, loaded = _context_indexes(output)
        asks = [
            (index, call)
            for index, call in enumerate(output.calls)
            if call.name == ASK_TOOL and call.succeeded
        ]
        if not memory:
            return CapabilityVerdict(False, "did not search workspace memory")
        if not applications:
            return CapabilityVerdict(False, "did not read existing applications")
        if FIRST_RUN_SKILL not in loaded:
            return CapabilityVerdict(False, "did not load first-run")
        if len(asks) != 1:
            return CapabilityVerdict(False, f"asked {len(asks)} times, expected one recipe offer")
        ask_index, ask = asks[0]
        if max(memory[0], applications[0]) > ask_index:
            return CapabilityVerdict(False, "offered recipes before reading context")
        questions = ask.input.get("questions")
        if not isinstance(questions, list) or len(questions) != 1:
            held = len(questions) if isinstance(questions, list) else 0
            return CapabilityVerdict(False, f"offer held {held} questions, expected one")
        question = questions[0]
        if not isinstance(question, dict) or question.get("multi_select"):
            return CapabilityVerdict(False, "recipe offer was a multi-select")
        options = question.get("options")
        if not isinstance(options, list) or len(options) != len(RECIPE_LABELS):
            held = len(options) if isinstance(options, list) else 0
            return CapabilityVerdict(False, f"offered {held} recipes, expected four")
        labels = []
        for option in options:
            if not isinstance(option, dict):
                return CapabilityVerdict(False, "recipe offer carried a malformed option")
            labels.append(str(option.get("label", "")))
        offered = tuple(labels)
        if offered != RECIPE_LABELS:
            return CapabilityVerdict(False, f"offered the wrong recipes: {offered}")
        if any(call.name == APPLY_TOOL for call in output.calls):
            return CapabilityVerdict(False, "created an application before the member chose")
        return CapabilityVerdict(True, "offered the four first-run recipes on one single-select")

    return DescribedGrader(
        "searches memory and existing applications, then offers the four recipes once",
        grade,
    )


def _recipe_interview_scorer() -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        memory, applications, loaded = _context_indexes(output)
        reads = [
            index
            for index, call in enumerate(output.calls)
            if call.name == READ_TOOL
            and str(call.input.get("file_path", "")).endswith(PULL_REQUEST_RECIPE)
            and call.succeeded
        ]
        asks = [
            (index, call)
            for index, call in enumerate(output.calls)
            if call.name == ASK_TOOL and call.succeeded
        ]
        if not memory or not applications:
            return CapabilityVerdict(False, "did not read workspace context")
        if FIRST_RUN_SKILL not in loaded or APPLICATION_SKILL not in loaded:
            return CapabilityVerdict(False, "did not load both first-run and create-application")
        if len(reads) != 1:
            return CapabilityVerdict(False, f"read the selected recipe {len(reads)} times")
        if len(asks) != 1:
            return CapabilityVerdict(False, f"asked {len(asks)} times, expected one interview")
        ask_index, ask = asks[0]
        if max(memory[0], applications[0], loaded[APPLICATION_SKILL], reads[0]) > ask_index:
            return CapabilityVerdict(False, "opened the interview before reading its recipe")
        questions = ask.input.get("questions")
        if not isinstance(questions, list) or not 3 <= len(questions) <= 4:
            held = len(questions) if isinstance(questions, list) else 0
            return CapabilityVerdict(False, f"interview held {held} questions, expected 3 to 4")
        job_question = questions[0]
        if not isinstance(job_question, dict):
            return CapabilityVerdict(False, "interview carried a malformed question")
        for question in questions:
            if not isinstance(question, dict):
                return CapabilityVerdict(False, "interview carried a malformed question")
            if question.get("multi_select"):
                return CapabilityVerdict(False, "interview included a multi-select")
        if not str(job_question.get("chosen", "")).strip():
            return CapabilityVerdict(False, "left the selected recipe's job blank")
        if any(call.name == APPLY_TOOL for call in output.calls):
            return CapabilityVerdict(False, "created an application before the interview returned")
        return CapabilityVerdict(
            True, "read the selected recipe and opened its prefilled interview"
        )

    return DescribedGrader(
        "reads the selected recipe, then opens one prefilled application interview",
        grade,
    )


def _created_application_indexes(output: CapabilityOutput) -> tuple[int, ...]:
    indexes = []
    for index, call in enumerate(output.calls):
        if call.name != APPLY_TOOL or not call.succeeded:
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
            indexes.append(index)
    return tuple(indexes)


def _forward_offer_scorer() -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        created = _created_application_indexes(output)
        if len(created) != 1:
            return CapabilityVerdict(False, f"created {len(created)} applications, expected one")
        apply_index = created[0]
        asks = [
            (index, call)
            for index, call in enumerate(output.calls)
            if call.name == ASK_TOOL and call.succeeded and index > apply_index
        ]
        if len(asks) != 1:
            return CapabilityVerdict(False, f"made {len(asks)} forward offers, expected one")
        _, ask = asks[0]
        questions = ask.input.get("questions")
        if not isinstance(questions, list) or len(questions) != 1:
            held = len(questions) if isinstance(questions, list) else 0
            return CapabilityVerdict(False, f"forward offer held {held} questions, expected one")
        question = questions[0]
        if not isinstance(question, dict) or question.get("multi_select"):
            return CapabilityVerdict(False, "forward offer was a multi-select")
        options = question.get("options")
        if not isinstance(options, list) or len(options) != 2:
            held = len(options) if isinstance(options, list) else 0
            return CapabilityVerdict(False, f"forward offer held {held} choices, expected two")
        return CapabilityVerdict(True, "created one application and offered one easy next act")

    return DescribedGrader(
        "creates one application, then asks one two-choice, single-select forward question",
        grade,
    )


async def _answer_interview(_output: CapabilityOutput) -> str | None:
    return INTERVIEW_ANSWER


def _without_prior_application() -> CapabilitySeed:
    async def seed(workspace_id: UUID, _agent_id: UUID, _blob: BlobStore) -> None:
        async with workspace_tx() as connection:
            prior = (
                await connection.execute(
                    sa.select(tables.agent.c.id).where(
                        tables.agent.c.workspace_id == workspace_id,
                        tables.agent.c.name == PULL_REQUEST_APPLICATION,
                        tables.agent.c.is_main.is_(False),
                        tables.agent.c.owner_member_id.is_not(None),
                        ~sa.select(tables.conversation.c.id)
                        .where(tables.conversation.c.agent_id == tables.agent.c.id)
                        .exists(),
                    )
                )
            ).scalar_one_or_none()
            if prior is None:
                return
            await connection.execute(
                sa.delete(tables.connector_grant).where(
                    tables.connector_grant.c.workspace_id == workspace_id,
                    tables.connector_grant.c.agent_id == prior,
                )
            )
            await connection.execute(sa.delete(tables.agent).where(tables.agent.c.id == prior))

    return seed


CASES = (
    CapabilityCase(
        "first-run-offers-four-recipes",
        HANDOVER,
        _four_recipe_offer_scorer(),
        seed=_without_prior_application(),
        digest_tag="first-run:offer",
    ),
    CapabilityCase(
        "first-run-starts-the-selected-recipe",
        SELECTED,
        _recipe_interview_scorer(),
        seed=_without_prior_application(),
        digest_tag="first-run:recipe",
    ),
    CapabilityCase(
        "first-run-creates-then-offers-forward",
        SELECTED,
        _forward_offer_scorer(),
        followup=_answer_interview,
        seed=_without_prior_application(),
        digest_tag="first-run:forward",
    ),
)
