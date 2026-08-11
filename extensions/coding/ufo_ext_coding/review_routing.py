"""Pull-request source changes routed into one configured review conversation."""

import hashlib
from dataclasses import dataclass
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ufo.sdk.context import ExtensionContext
from ufo.sdk.manifest import HookContext, HookOutcome, PageChangeBatch
from ufo.sdk.sources import ConnectorSourceConfig, PageChange, binding_name
from ufo.sdk.subjects import SHARED_SUBJECT
from ufo.sdk.tools import TextContent, ToolContext, ToolResult

GITHUB_PROVIDER = "github"
PULL_REQUEST_STREAM = "pull_requests"
PULL_REQUEST_BODY_PREFIX = f"# {GITHUB_PROVIDER} {PULL_REQUEST_STREAM}: "
REVIEW_IDEMPOTENCY_PREFIX = "code-review"

_metadata = sa.MetaData()
review_inbox = sa.Table(
    "coding_review_inbox",
    _metadata,
    sa.Column("workspace_id", sa.Uuid, primary_key=True),
    sa.Column("source_id", sa.Uuid, primary_key=True),
    sa.Column("conversation_id", sa.Uuid, nullable=False),
    sa.Column("agent_id", sa.Uuid, nullable=False),
    sa.Column("baseline_revision", sa.BigInteger, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)
review_run = sa.Table(
    "coding_review_run",
    _metadata,
    sa.Column("workspace_id", sa.Uuid, primary_key=True),
    sa.Column("source_id", sa.Uuid, nullable=False),
    sa.Column("repository", sa.Text, primary_key=True),
    sa.Column("pull_request_number", sa.Integer, primary_key=True),
    sa.Column("base_sha", sa.Text, primary_key=True),
    sa.Column("head_sha", sa.Text, primary_key=True),
    sa.Column("run_id", sa.Uuid, nullable=False),
    sa.Column("conversation_id", sa.Uuid, nullable=False),
    sa.Column("agent_id", sa.Uuid, nullable=False),
    sa.Column("turn_id", sa.Uuid, nullable=True),
    sa.Column("review_conversation_id", sa.Uuid, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)


class ConfigureReviewInboxInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(
        description="The GitHub source object whose pull_requests stream this conversation owns."
    )
    user_description: str = Field(description="That you are configuring automatic code review.")


class GitRef(BaseModel):
    sha: str = Field(pattern=r"^[0-9a-f]{40}$")


class PullRequestPage(BaseModel):
    repo_full_name: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    number: int = Field(gt=0)
    state: str
    draft: bool
    base: GitRef
    head: GitRef

    @field_validator("repo_full_name")
    @classmethod
    def repository_has_two_names(cls, value: str) -> str:
        if any(part in {".", ".."} for part in value.split("/")):
            raise ValueError("repository must be owner/name")
        return value


@dataclass(frozen=True)
class ReviewInbox:
    source_id: UUID
    conversation_id: UUID
    agent_id: UUID
    baseline_revision: int


@dataclass(frozen=True)
class ReviewTarget:
    repository: str
    pull_request_number: int
    base_sha: str
    head_sha: str

    def message(self, run_id: UUID) -> str:
        return (
            "Review this exact pull-request comparison.\n"
            f"Review run: {run_id}\n"
            f"Repository: {self.repository}\n"
            f"Pull request: {self.pull_request_number}\n"
            f"Base SHA: {self.base_sha}\n"
            f"Head SHA: {self.head_sha}\n"
            "Spawn exactly one `code_review` subagent in the background with these values, then "
            "end the turn without publishing. Its validated result arrives on this conversation "
            f"as a later message: call `publish_code_review` with review run {run_id} and the "
            "subagent id named by that message, even when its status is not done, then end that "
            "turn. Do not copy the review into the tool call or ask the user a question."
        )

    @property
    def idempotency_key(self) -> str:
        identity = (
            f"{self.repository}\0{self.pull_request_number}\0{self.base_sha}\0{self.head_sha}"
        )
        return f"{REVIEW_IDEMPOTENCY_PREFIX}:{hashlib.sha256(identity.encode()).hexdigest()}"


def _run_match(workspace_id: UUID, target: ReviewTarget) -> sa.ColumnElement[bool]:
    return sa.and_(
        review_run.c.workspace_id == workspace_id,
        review_run.c.repository == target.repository,
        review_run.c.pull_request_number == target.pull_request_number,
        review_run.c.base_sha == target.base_sha,
        review_run.c.head_sha == target.head_sha,
    )


@dataclass(frozen=True)
class StoredReviewRun:
    run_id: UUID
    repository: str
    pull_request_number: int
    base_sha: str
    head_sha: str
    conversation_id: UUID
    review_conversation_id: UUID | None


async def review_run_for(
    ext: ExtensionContext, run_id: UUID, conversation_id: UUID
) -> StoredReviewRun | None:
    async with ext.transaction() as connection:
        row = (
            (
                await connection.execute(
                    sa.select(review_run).where(
                        review_run.c.workspace_id == ext.store.workspace_id,
                        review_run.c.run_id == run_id,
                        review_run.c.conversation_id == conversation_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        return None
    return StoredReviewRun(
        run_id=row["run_id"],
        repository=row["repository"],
        pull_request_number=row["pull_request_number"],
        base_sha=row["base_sha"],
        head_sha=row["head_sha"],
        conversation_id=row["conversation_id"],
        review_conversation_id=row["review_conversation_id"],
    )


async def record_review_conversation(
    ext: ExtensionContext, target: ReviewTarget, conversation_id: UUID
) -> None:
    """Which conversation actually ran the review, written by the reviewer child itself: the run row
    names the inbox conversation that ordered the review, while the reviewer's own transcript lives
    in the fresh conversation its turn runs in. The child writes its own turn's id, so publication
    links a conversation the host resolved rather than one a review claimed. A comparison with no
    stored run — a reviewer spawned by hand — matches no row and records nothing."""
    async with ext.transaction() as connection:
        await connection.execute(
            sa.update(review_run)
            .values(review_conversation_id=conversation_id, updated_at=sa.func.now())
            .where(_run_match(ext.store.workspace_id, target))
        )


@dataclass(frozen=True)
class ReviewRouting:
    ext: ExtensionContext

    async def activate(self, ctx: ToolContext, source_name: str) -> int:
        if not await ctx.speaker_is_admin():
            raise ValueError("only a workspace admin can configure automatic code review")
        source_id = await self._pull_request_source(ctx, source_name)
        baseline = max(
            (
                page.revision
                for page in await self.ext.source_pages(ctx.source_reader())
                if page.source_id == source_id
            ),
            default=0,
        )
        workspace_id = self.ext.store.workspace_id
        async with self.ext.transaction() as connection:
            insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
            stored = (
                await connection.execute(
                    insert(review_inbox)
                    .values(
                        workspace_id=workspace_id,
                        source_id=source_id,
                        conversation_id=ctx.turn.conversation_id,
                        agent_id=ctx.turn.agent_id,
                        baseline_revision=baseline,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                    .on_conflict_do_update(
                        index_elements=[review_inbox.c.workspace_id, review_inbox.c.source_id],
                        set_={
                            "conversation_id": ctx.turn.conversation_id,
                            "agent_id": ctx.turn.agent_id,
                            "updated_at": sa.func.now(),
                        },
                    )
                    .returning(review_inbox.c.baseline_revision)
                )
            ).scalar_one()
        return int(stored)

    async def route(self, changes: tuple[PageChange, ...]) -> None:
        inboxes = await self._inboxes(tuple({change.source_id for change in changes}))
        for change in changes:
            inbox = inboxes.get(change.source_id)
            if (
                inbox is None
                or change.revision <= inbox.baseline_revision
                or change.tombstone
                or change.stream != PULL_REQUEST_STREAM
            ):
                continue
            heading, separator, record = change.body.partition("\n\n")
            if not separator or not heading.startswith(PULL_REQUEST_BODY_PREFIX):
                raise ValueError("pull request source page has an invalid body")
            page = PullRequestPage.model_validate_json(record)
            if page.state != "open" or page.draft:
                continue
            await self._invoke(
                inbox,
                ReviewTarget(
                    repository=page.repo_full_name,
                    pull_request_number=page.number,
                    base_sha=page.base.sha,
                    head_sha=page.head.sha,
                ),
            )

    async def _pull_request_source(self, ctx: ToolContext, source_name: str) -> UUID:
        readable = await self.ext.readable_source_ids(ctx.source_reader())
        matches = []
        for source in await self.ext.sources(GITHUB_PROVIDER):
            config = ConnectorSourceConfig.model_validate(source.config)
            if (
                config.stream == PULL_REQUEST_STREAM
                and binding_name(GITHUB_PROVIDER, config.account, config.base_url) == source_name
            ):
                matches.append(source)
        if len(matches) != 1:
            raise ValueError(
                f"source {source_name!r} must name one GitHub binding with a pull_requests stream"
            )
        source = matches[0]
        if source.subject != SHARED_SUBJECT or source.id not in readable:
            raise ValueError(
                "automatic code review requires a shared pull_requests source granted to this agent"
            )
        return source.id

    async def _inboxes(self, source_ids: tuple[UUID, ...]) -> dict[UUID, ReviewInbox]:
        if not source_ids:
            return {}
        async with self.ext.transaction() as connection:
            rows = (
                await connection.execute(
                    sa.select(review_inbox).where(
                        review_inbox.c.workspace_id == self.ext.store.workspace_id,
                        review_inbox.c.source_id.in_(source_ids),
                    )
                )
            ).mappings()
        return {
            row["source_id"]: ReviewInbox(
                source_id=row["source_id"],
                conversation_id=row["conversation_id"],
                agent_id=row["agent_id"],
                baseline_revision=row["baseline_revision"],
            )
            for row in rows
        }

    async def _invoke(self, inbox: ReviewInbox, target: ReviewTarget) -> None:
        workspace_id = self.ext.store.workspace_id
        candidate_run_id = uuid4()
        async with self.ext.transaction() as connection:
            insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
            await connection.execute(
                insert(review_run)
                .values(
                    workspace_id=workspace_id,
                    source_id=inbox.source_id,
                    repository=target.repository,
                    pull_request_number=target.pull_request_number,
                    base_sha=target.base_sha,
                    head_sha=target.head_sha,
                    run_id=candidate_run_id,
                    conversation_id=inbox.conversation_id,
                    agent_id=inbox.agent_id,
                    turn_id=None,
                    review_conversation_id=None,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        review_run.c.workspace_id,
                        review_run.c.repository,
                        review_run.c.pull_request_number,
                        review_run.c.base_sha,
                        review_run.c.head_sha,
                    ]
                )
            )
            stored = (
                await connection.execute(
                    sa.select(
                        review_run.c.run_id,
                        review_run.c.conversation_id,
                        review_run.c.agent_id,
                        review_run.c.turn_id,
                    ).where(_run_match(workspace_id, target))
                )
            ).one()
        if stored.turn_id is not None:
            return
        turn_id = await self.ext.invoke(
            stored.conversation_id,
            stored.agent_id,
            target.message(stored.run_id),
            target.idempotency_key,
        )
        async with self.ext.transaction() as connection:
            updated = await connection.execute(
                sa.update(review_run)
                .values(turn_id=turn_id, updated_at=sa.func.now())
                .where(
                    review_run.c.turn_id.is_(None),
                    _run_match(workspace_id, target),
                )
            )
            if updated.rowcount == 0:
                stored = (
                    await connection.execute(
                        sa.select(review_run.c.turn_id).where(_run_match(workspace_id, target))
                    )
                ).scalar_one()
                if stored != turn_id:
                    raise RuntimeError("one review comparison resolved to different turns")


async def configure_review_inbox(ctx: ToolContext, args: ConfigureReviewInboxInput) -> ToolResult:
    if ctx.ext is None:
        raise RuntimeError("review inbox configuration requires the coding extension context")
    baseline = await ReviewRouting(ctx.ext).activate(ctx, args.source)
    return ToolResult(
        content=(
            TextContent(
                text=(
                    f"This conversation reviews new pull-request comparisons from {args.source}. "
                    f"Pages through source revision {baseline} are baselined."
                )
            ),
        )
    )


async def route_review_pages(ctx: HookContext) -> HookOutcome:
    if not isinstance(ctx.payload, PageChangeBatch):
        raise RuntimeError("review routing fired on a non-page_change payload")
    await ReviewRouting(ctx.ext).route(ctx.payload.changes)
    return None
