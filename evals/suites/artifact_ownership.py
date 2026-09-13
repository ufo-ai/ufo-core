"""Artifact listing as member provenance rather than conversation ownership."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

import sqlalchemy as sa

from evals.driver import EVAL_SURFACE
from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
)
from evals.harness.harness import JsonObject
from ufo.blob import BlobStore
from ufo.db import workspace_tx
from ufo.runtime.turns.audience import conversation_audience
from ufo.schema import tables

EVAL_OWNER_EMAIL = "evals@localhost"
COLLEAGUE_EMAIL = "artifact-colleague@eval.test"
MINE_FILENAME = "member-plan.txt"
THEIRS_FILENAME = "colleague-plan.txt"
MINE_KEY = f"artifacts/00000000-0000-0000-0000-000000000001/{MINE_FILENAME}"
THEIRS_KEY = f"artifacts/00000000-0000-0000-0000-000000000002/{THEIRS_FILENAME}"
SHARED_AT = datetime(2026, 9, 13, tzinfo=UTC)


@dataclass(frozen=True)
class _ArtifactFixture:
    async def seed(self, workspace_id: UUID, agent_id: UUID, blob: BlobStore) -> None:
        await self.cleanup(workspace_id, agent_id, blob)
        colleague_id = uuid5(NAMESPACE_URL, f"{workspace_id}:artifact-colleague")
        conversation_id = uuid5(NAMESPACE_URL, f"{workspace_id}:artifact-conversation")
        mine_turn_id = uuid5(NAMESPACE_URL, f"{workspace_id}:artifact-mine-turn")
        theirs_turn_id = uuid5(NAMESPACE_URL, f"{workspace_id}:artifact-theirs-turn")
        async with workspace_tx() as connection:
            member_id = (
                await connection.execute(
                    sa.select(tables.member.c.id).where(
                        tables.member.c.workspace_id == workspace_id,
                        tables.member.c.email == EVAL_OWNER_EMAIL,
                    )
                )
            ).scalar_one()
            await connection.execute(
                sa.insert(tables.member).values(
                    id=colleague_id,
                    workspace_id=workspace_id,
                    email=COLLEAGUE_EMAIL,
                    created_at=SHARED_AT,
                    updated_at=SHARED_AT,
                )
            )
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=conversation_id,
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    surface=EVAL_SURFACE,
                    queue_key=f"{EVAL_SURFACE}-artifact-ownership",
                    member_id=colleague_id,
                    audience=str(conversation_audience(member_id)),
                    created_at=SHARED_AT,
                    updated_at=SHARED_AT,
                )
            )
            await connection.execute(
                sa.insert(tables.turn),
                (
                    {
                        "id": mine_turn_id,
                        "workspace_id": workspace_id,
                        "conversation_id": conversation_id,
                        "agent_id": agent_id,
                        "seq": 1,
                        "status": "done",
                        "inbound": "shared member plan",
                        "admission_source": "member",
                        "speaker_member_id": member_id,
                        "terminal": {"status": "done", "text": "Shared."},
                        "created_at": SHARED_AT,
                        "updated_at": SHARED_AT,
                    },
                    {
                        "id": theirs_turn_id,
                        "workspace_id": workspace_id,
                        "conversation_id": conversation_id,
                        "agent_id": agent_id,
                        "seq": 2,
                        "status": "done",
                        "inbound": "shared colleague plan",
                        "admission_source": "member",
                        "speaker_member_id": colleague_id,
                        "terminal": {"status": "done", "text": "Shared."},
                        "created_at": SHARED_AT,
                        "updated_at": SHARED_AT,
                    },
                ),
            )
            await connection.execute(
                sa.insert(tables.shared_artifact),
                (
                    {
                        "turn_id": mine_turn_id,
                        "blob_key": MINE_KEY,
                        "id": uuid5(NAMESPACE_URL, MINE_KEY),
                        "workspace_id": workspace_id,
                        "member_id": member_id,
                        "filename": MINE_FILENAME,
                        "media_type": "text/plain",
                        "size_bytes": 11,
                        "created_at": SHARED_AT,
                        "updated_at": SHARED_AT,
                    },
                    {
                        "turn_id": theirs_turn_id,
                        "blob_key": THEIRS_KEY,
                        "id": uuid5(NAMESPACE_URL, THEIRS_KEY),
                        "workspace_id": workspace_id,
                        "member_id": colleague_id,
                        "filename": THEIRS_FILENAME,
                        "media_type": "text/plain",
                        "size_bytes": 14,
                        "created_at": SHARED_AT,
                        "updated_at": SHARED_AT,
                    },
                ),
            )
        await blob.put(MINE_KEY, b"member plan")
        await blob.put(THEIRS_KEY, b"colleague plan")

    async def cleanup(self, workspace_id: UUID, agent_id: UUID, blob: BlobStore) -> None:
        colleague_id = uuid5(NAMESPACE_URL, f"{workspace_id}:artifact-colleague")
        conversation_id = uuid5(NAMESPACE_URL, f"{workspace_id}:artifact-conversation")
        turn_ids = (
            uuid5(NAMESPACE_URL, f"{workspace_id}:artifact-mine-turn"),
            uuid5(NAMESPACE_URL, f"{workspace_id}:artifact-theirs-turn"),
        )
        async with workspace_tx() as connection:
            await connection.execute(
                sa.delete(tables.shared_artifact).where(
                    tables.shared_artifact.c.workspace_id == workspace_id,
                    tables.shared_artifact.c.turn_id.in_(turn_ids),
                )
            )
            await connection.execute(
                sa.delete(tables.turn).where(
                    tables.turn.c.workspace_id == workspace_id,
                    tables.turn.c.id.in_(turn_ids),
                )
            )
            await connection.execute(
                sa.delete(tables.conversation).where(
                    tables.conversation.c.workspace_id == workspace_id,
                    tables.conversation.c.id == conversation_id,
                )
            )
            await connection.execute(
                sa.delete(tables.member).where(
                    tables.member.c.workspace_id == workspace_id,
                    tables.member.c.id == colleague_id,
                )
            )
        await blob.delete(MINE_KEY)
        await blob.delete(THEIRS_KEY)


async def _graded_mine_listing(output: CapabilityOutput) -> CapabilityVerdict:
    listings = sum(
        call.name == "object_list" and call.succeeded and call.input.get("kind") == "artifact"
        for call in output.calls
    )
    evidence: JsonObject = {"artifact_listings": listings}
    if not listings:
        return CapabilityVerdict(False, "no successful artifact listing", evidence)
    answer = output.response.casefold()
    if MINE_FILENAME not in answer:
        return CapabilityVerdict(False, f"the reply omitted {MINE_FILENAME}", evidence)
    if THEIRS_FILENAME in answer:
        return CapabilityVerdict(False, f"the reply included {THEIRS_FILENAME}", evidence)
    return CapabilityVerdict(True, "listed only the file the member shared", evidence)


async def _graded_mine_meaning(output: CapabilityOutput) -> CapabilityVerdict:
    inspected = any(
        call.name == "object_explain" and call.succeeded and call.input.get("kind") == "artifact"
        for call in output.calls
    )
    return CapabilityVerdict(
        inspected,
        "inspected the artifact kind" if inspected else "did not inspect the artifact kind",
    )


FIXTURE = _ArtifactFixture()

CASES = (
    CapabilityCase(
        "A01-list-files-i-shared",
        "Which .txt files did I personally share? List only the filenames. Do not include files "
        "a colleague shared into a conversation I can read.",
        DescribedGrader(
            "the agent lists artifacts and the reply names only the current member's file",
            _graded_mine_listing,
        ),
        member_key=EVAL_OWNER_EMAIL,
        seed=FIXTURE.seed,
        cleanup=FIXTURE.cleanup,
        digest_tag="artifact-ownership:mine-listing",
    ),
    CapabilityCase(
        "A02-explain-mine",
        "For artifact listings, does mine=true mean files from conversations I own, or files I "
        "shared? Check the artifact object definition and give me the exact rule. Do not list or "
        "change anything.",
        DescribedGrader(
            "the artifact kind is inspected before the rule is explained",
            _graded_mine_meaning,
        ),
        rubric=(
            "The answer says mine=true selects artifacts whose current version was shared by the "
            "speaking member, not artifacts based on who owns the conversation.",
        ),
        digest_tag="artifact-ownership:mine-meaning",
    ),
)
