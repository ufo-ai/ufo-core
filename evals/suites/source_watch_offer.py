"""A link to a resource of a synced source, and whether the conversation ends up watching it.

The sources extension offers a narrowed `source_trigger` for every such link the turn reads — in the
member's message, or in a tool result — as one `<watch_offer>` block naming the exact manifest. The
offer is text; the agent applies the trigger, or does not. These cases grade that choice on the
durable act alone: a successful `object_apply` of a `source_trigger` whose `resource` canonicalizes
to the case's pull request. Three cases ask for the watch and should apply it, once from the
member's own words, once from a file a tool read, and once for two pull requests in one message,
which reads two offers out of one block; two name a link in passing, or a repository, and should
apply nothing. The workspace holds one shared GitHub connection, seeded directly with its streams
pinned far ahead so the sync driver never calls GitHub for it."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
import yaml
from ufo_ext_sources.manifest import NAME
from ufo_ext_sources.registry import CONNECTORS
from ufo_ext_sources.resources import canonical_resource
from ufo_ext_sources.tools import SOURCE_TRIGGER_KIND

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    Grader,
    ToolInvocation,
    WorkspaceFile,
)
from evals.harness.harness import JsonObject
from evals.harness.scorers import DescribedGrader
from ufo.blob import BlobStore
from ufo.db import workspace_tx
from ufo.runtime.agent_scope import agent
from ufo.runtime.ext.context import context_for
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.sdk.sources import ConnectorSourceConfig

GITHUB = "github"
ACCOUNT = "metalcraftai"
STREAMS = ("pull_requests", "workflow_runs")
PR = "https://github.com/metalcraftai/ufo/pull/3112"
SECOND_PR = "https://github.com/metalcraftai/ufo/pull/3113"
REPOSITORY = "https://github.com/metalcraftai/ufo"
NEVER = datetime(2100, 1, 1, tzinfo=UTC)
PR_NOTE = WorkspaceFile(
    "pr.txt",
    f"Coding agent report: pushed ufo/29c88018-alert-copy and opened {PR}. Tests pass "
    "locally.\n".encode(),
)


async def seed(workspace_id: UUID, agent_id: UUID, _blob: BlobStore) -> None:
    """One shared GitHub connection the case's agent is granted, carrying a stream apiece with its
    next sync pinned far ahead: the offer needs a connection whose streams sync the resource, not
    the pages themselves, and no credential backs the account.

    Settles rather than inserts. Every case of this suite seeds the same account into the same
    workspace, so a plain insert makes the first case the only one that runs."""
    async with workspace_tx() as connection:
        owner_id = (
            await connection.execute(
                sa.select(tables.member.c.id)
                .where(tables.member.c.workspace_id == workspace_id)
                .order_by(tables.member.c.created_at)
                .limit(1)
            )
        ).scalar_one()
        connection_id = (
            await connection.execute(
                sa.select(tables.connection.c.id).where(
                    tables.connection.c.workspace_id == workspace_id,
                    tables.connection.c.provider == GITHUB,
                    tables.connection.c.account_id == ACCOUNT,
                )
            )
        ).scalar_one_or_none()
        if connection_id is None:
            connection_id = uuid4()
            await connection.execute(
                sa.insert(tables.connection).values(
                    id=connection_id,
                    workspace_id=workspace_id,
                    provider=GITHUB,
                    account_id=ACCOUNT,
                    host="github.com",
                    owner_member_id=owner_id,
                    shared=True,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        granted = (
            await connection.execute(
                sa.select(tables.connector_grant.c.id).where(
                    tables.connector_grant.c.workspace_id == workspace_id,
                    tables.connector_grant.c.agent_id == agent_id,
                    tables.connector_grant.c.connection_id == connection_id,
                )
            )
        ).scalar_one_or_none()
        if granted is None:
            await connection.execute(
                sa.insert(tables.connector_grant).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    connection_id=connection_id,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    with ws(workspace_id), agent(agent_id):
        ext = context_for(NAME, frozenset(CONNECTORS))
        for stream in STREAMS:
            source_id = await ext.register_source(
                GITHUB,
                ConnectorSourceConfig(stream=stream),
                connection_id=connection_id,
            )
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.update(tables.source)
                    .values(next_sync_at=NEVER)
                    .where(tables.source.c.uid == source_id)
                )


def _trigger_resource(call: ToolInvocation) -> str | None:
    """The canonical resource a `source_trigger` apply narrows to — "" for a trigger on the whole
    connection, None for a call that applies no source trigger."""
    if call.name != "object_apply":
        return None
    manifest = call.input.get("manifest")
    if not isinstance(manifest, str):
        return None
    try:
        document = yaml.safe_load(manifest)
    except yaml.YAMLError:
        return None
    if not isinstance(document, dict) or document.get("kind") != SOURCE_TRIGGER_KIND:
        return None
    spec = document.get("spec")
    resource = spec.get("resource", "") if isinstance(spec, dict) else ""
    if not isinstance(resource, str) or not resource:
        return ""
    return canonical_resource(GITHUB, resource) or resource


def _round_trips(output: CapabilityOutput) -> JsonObject:
    """What taking the offer cost beyond the apply itself: the `object_explain` reads the agent made
    first. The offer carries the whole manifest so that this can be zero; the count is evidence, not
    a verdict, so the report shows the price without failing a case over it."""
    return {
        "explains": sum(call.name == "object_explain" for call in output.calls),
        "applies": sum(call.name == "object_apply" for call in output.calls),
    }


def watches(resource: str) -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        applied = [call for call in output.calls if _trigger_resource(call) == resource]
        evidence = _round_trips(output)
        if not applied:
            return CapabilityVerdict(
                False, f"no source_trigger narrowed to {resource} was applied", evidence
            )
        if not any(call.succeeded for call in applied):
            return CapabilityVerdict(
                False,
                f"the source_trigger apply did not land: {applied[-1].result[:200]}",
                evidence,
            )
        return CapabilityVerdict(True, f"applied a source_trigger narrowed to {resource}", evidence)

    return DescribedGrader(f"applies a source_trigger narrowed to {resource}", grade)


def watches_each(*resources: str) -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        landed = {_trigger_resource(call) for call in output.calls if call.succeeded}
        evidence = _round_trips(output)
        missing = [resource for resource in resources if resource not in landed]
        if missing:
            return CapabilityVerdict(
                False, f"no source_trigger narrowed to {', '.join(missing)} landed", evidence
            )
        return CapabilityVerdict(
            True, f"applied a source_trigger narrowed to each of {', '.join(resources)}", evidence
        )

    return DescribedGrader(
        f"applies a source_trigger narrowed to each of {', '.join(resources)}", grade
    )


def watches_nothing() -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        applied = [
            resource or "the whole source"
            for call in output.calls
            if (resource := _trigger_resource(call)) is not None
        ]
        evidence = _round_trips(output)
        if applied:
            return CapabilityVerdict(
                False, f"applied a source_trigger on {', '.join(applied)}", evidence
            )
        return CapabilityVerdict(True, "applied no source_trigger", evidence)

    return DescribedGrader("applies no source_trigger", grade)


CASES = (
    CapabilityCase(
        "watch-pr-from-message",
        f"Keep an eye on {PR} for me. I want to hear here when its checks finish or someone "
        "reviews it.",
        watches(PR),
        seed=seed,
        digest_tag="source-watch-offer:watch-pr-from-message:authored",
    ),
    CapabilityCase(
        "watch-pr-from-tool-output",
        "My coding agent left its report in pr.txt. Read it, and make sure this thread hears what "
        "happens to the pull request it opened.",
        watches(PR),
        workspace_files=(PR_NOTE,),
        seed=seed,
        digest_tag="source-watch-offer:watch-pr-from-tool-output:authored",
    ),
    CapabilityCase(
        "watch-two-prs",
        f"Two pull requests went up tonight, {PR} and {SECOND_PR}. Keep this thread posted on "
        "both: their checks and their reviews.",
        watches_each(PR, SECOND_PR),
        seed=seed,
        digest_tag="source-watch-offer:watch-two-prs:authored",
    ),
    CapabilityCase(
        "pr-mentioned-in-passing",
        f"FYI, the alert wording fix landed in {PR}. Draft a two-line release note for it: the "
        "alert now names the pull request it is about instead of the whole source.",
        watches_nothing(),
        seed=seed,
        digest_tag="source-watch-offer:pr-mentioned-in-passing:authored",
    ),
    CapabilityCase(
        "repository-link",
        f"Have a look at {REPOSITORY} when you get a chance and tell me in two sentences what the "
        "repository is for.",
        watches_nothing(),
        seed=seed,
        digest_tag="source-watch-offer:repository-link:authored",
    ),
)
