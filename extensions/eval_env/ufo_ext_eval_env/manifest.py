"""The deterministic eval environment: fixed workplace services the agent reaches
only through the real connector dispatch (`list_external_tools` → `describe_external_tools` →
`call_external_tool`), backed by the extension's own workspace-scoped storage. Evals seed that
storage, run a conversation, and assert the end state on the same rows the broker mutated — a
controllable domain over the production seam, never a mock of it. The providers ride the
`assistant_eval` pack only; a product pack never lists them.

The mailbox and calendar own tables because a case mutates them. Read-only provider responses live
in the scoped store under exact provider and tool keys. A missing response fails loud, so an eval
cannot pass against data it did not seed."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Literal
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ufo.sdk.authproxy import Credential
from ufo.sdk.connectors import (
    BrokerFile,
    BrokerSearch,
    BrokerTool,
    OAuthAccount,
    StagedUpload,
    UnknownBrokerTool,
)
from ufo.sdk.context import CredentialAccess, ExtensionContext, ScopedStore
from ufo.sdk.manifest import (
    AgentProvision,
    AgentSpec,
    ConnectorProvider,
    Deny,
    HookContext,
    HookSpec,
    Manifest,
    PreToolUse,
)
from ufo.sdk.objects import (
    ObjectDetail,
    ObjectKind,
    ObjectListQuery,
    ObjectPage,
    ObjectRow,
    VerbNotSupported,
    object_page,
)
from ufo.sdk.tools import ToolContext

NAME = "eval_env"
VERSION = "0.1.0"
EMAIL_PROVIDER = "eval_email"
EMAIL_LABEL = "Email (eval)"
EMAIL_HOST = "email.evalenv.test"
CALENDAR_PROVIDER = "eval_calendar"
CALENDAR_LABEL = "Calendar (eval)"
CALENDAR_HOST = "calendar.evalenv.test"
CODE_PROVIDER = "eval_code_search"
CODE_LABEL = "Code Search (eval)"
CODE_HOST = "code.evalenv.test"
CODE_FIXTURE_PREFIX = "code_search:"
DRIVE_PROVIDER = "google_drive"
DRIVE_LABEL = "Google Drive (eval)"
DRIVE_HOST = "drive.evalenv.test"
GITHUB_PROVIDER = "eval_github"
GITHUB_LABEL = "GitHub (eval)"
GITHUB_HOST = "github.evalenv.test"
STRIPE_PROVIDER = "stripe"
STRIPE_LABEL = "Stripe (eval)"
STRIPE_HOST = "stripe.evalenv.test"
HUBSPOT_PROVIDER = "hubspot"
HUBSPOT_LABEL = "HubSpot (eval)"
HUBSPOT_HOST = "hubspot.evalenv.test"
GREENHOUSE_PROVIDER = "greenhouse"
GREENHOUSE_LABEL = "Greenhouse (eval)"
GREENHOUSE_HOST = "greenhouse.evalenv.test"
APP_FIXTURE_PREFIX = "app_fixture:"
APP_ACTION_KIND = "eval_app_action"
APP_ACTION_KEY_PREFIX = "app_action:"
APP_ACTION_FIXTURE_PREFIX = "app_action_fixture:"
ACCOUNT_ID = "eval-env-account"
MAILBOX_ADDRESS = "member@evalco.test"
MAX_LIST_LIMIT = 50
CONFIRMED = "confirmed"
CANCELLED = "cancelled"
APP_QA_REPAIR_AGENT_NAME = "app-qa-repair"
APP_QA_SOURCE_PATH = "/workspace/ufo-app/app.tsx"
APP_QA_EDIT_CALL_LIMIT = 8
APP_QA_EDIT_OLD_BYTES_LIMIT = 16_384
APP_QA_EDIT_NEW_BYTES_LIMIT = 16_384
APP_QA_EDIT_BUDGET_KEY = "app-qa-repair/{turn_id}/edit-budget"
APP_QA_REPAIR_PROMPT = (
    (Path(__file__).parent / "prompts" / "agent_app_qa_repair.md").read_text().strip()
)


class AppQaEditBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calls: int = Field(ge=0)
    old_bytes: int = Field(ge=0)
    new_bytes: int = Field(ge=0)


_metadata = sa.MetaData()

eval_env_email = sa.Table(
    "eval_env_email",
    _metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("folder", sa.Text, nullable=False),
    sa.Column("sender", sa.Text, nullable=False),
    sa.Column("recipients", sa.JSON, nullable=False),
    sa.Column("subject", sa.Text, nullable=False),
    sa.Column("body", sa.Text, nullable=False),
    sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
)

eval_env_event = sa.Table(
    "eval_env_event",
    _metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("attendees", sa.JSON, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
)


class CreateCommitStatusArgs(BaseModel):
    """The verdict a reviewing agent publishes. The eval repository is a local fixture with no
    GitHub identity, so nothing can post a real status — but a case that cannot publish cannot
    grade the end of the chain, and the agent's own prompt fails the turn when publication fails.
    This accepts the write, checks its shape, and answers as the provider would."""

    sha: str = Field(min_length=7, description="The commit the verdict covers.")
    state: Literal["success", "failure", "pending", "error"] = Field(
        description="The verdict state."
    )
    context: str = Field(min_length=1, description="The status context name.")
    description: str = Field(default="", description="One line shown beside the status.")
    target_url: str = Field(default="", description="Where the status links.")


class SendEmailArgs(BaseModel):
    to: tuple[str, ...] = Field(min_length=1, description="Recipient email addresses.")
    subject: str = Field(min_length=1, description="Subject line.")
    body: str = Field(min_length=1, description="Plain-text body.")


class ReplyAllEmailArgs(BaseModel):
    message_id: UUID = Field(description="Id of the email to reply to.")
    body: str = Field(min_length=1, description="Plain-text reply body.")


class ListEmailsArgs(BaseModel):
    folder: Literal["inbox", "sent"] = Field(default="inbox", description="Folder to read.")
    query: str = Field(default="", description="Substring match over sender, subject, and body.")
    limit: int = Field(default=20, ge=1, le=MAX_LIST_LIMIT, description="Maximum emails returned.")


class CreateEventArgs(BaseModel):
    title: str = Field(min_length=1, description="Event title.")
    start: str = Field(description="Start time, ISO 8601.")
    end: str = Field(description="End time, ISO 8601.")
    attendees: tuple[str, ...] = Field(default=(), description="Attendee email addresses.")


class ListEventsArgs(BaseModel):
    query: str = Field(default="", description="Substring match over the event title.")
    limit: int = Field(default=20, ge=1, le=MAX_LIST_LIMIT, description="Maximum events returned.")


class UpdateEventArgs(BaseModel):
    event_id: str = Field(description="Id of the event to update.")
    title: str | None = Field(default=None, description="New title, unchanged if omitted.")
    start: str | None = Field(default=None, description="New start time, ISO 8601.")
    end: str | None = Field(default=None, description="New end time, ISO 8601.")
    attendees: tuple[str, ...] | None = Field(default=None, description="Replacement attendees.")


class CancelEventArgs(BaseModel):
    event_id: str = Field(description="Id of the event to cancel.")


class SearchCodeArgs(BaseModel):
    query: str = Field(min_length=1, description="Code search query, e.g. 'reserve repo:acme/x'.")


class AppActionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case: Literal["meeting-tasks", "issue-owner", "pr-babysitter"]
    action: Literal["create_issue", "assign_issue", "set_babysitter"]
    target: str = Field(min_length=1)
    value: str = Field(min_length=1)


class StoredAppAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: AppActionSpec
    result: str
    created_at: datetime
    updated_at: datetime


_CATALOG: dict[str, tuple[BrokerTool, ...]] = {
    EMAIL_PROVIDER: (
        BrokerTool(
            slug="send_email",
            description="Send a plain-text email from the member's mailbox.",
            input_schema=SendEmailArgs.model_json_schema(),
        ),
        BrokerTool(
            slug="reply_all_email",
            description="Reply to the sender and every other recipient of an existing email.",
            input_schema=ReplyAllEmailArgs.model_json_schema(),
        ),
        BrokerTool(
            slug="list_emails",
            description="List emails in a folder, newest first, optionally filtered.",
            input_schema=ListEmailsArgs.model_json_schema(),
            read_only=True,
        ),
    ),
    CALENDAR_PROVIDER: (
        BrokerTool(
            slug="create_event",
            description="Create a calendar event.",
            input_schema=CreateEventArgs.model_json_schema(),
        ),
        BrokerTool(
            slug="list_events",
            description="List calendar events in start order, optionally filtered by title.",
            input_schema=ListEventsArgs.model_json_schema(),
            read_only=True,
        ),
        BrokerTool(
            slug="update_event",
            description="Update an existing event's title, times, or attendees.",
            input_schema=UpdateEventArgs.model_json_schema(),
        ),
        BrokerTool(
            slug="cancel_event",
            description="Cancel an event; it stays listed with status 'cancelled'.",
            input_schema=CancelEventArgs.model_json_schema(),
        ),
    ),
    CODE_PROVIDER: (
        BrokerTool(
            slug="search_code",
            description=(
                "Search source files across the org's repositories. Each hit carries the full "
                "repository record it belongs to."
            ),
            input_schema=SearchCodeArgs.model_json_schema(),
            read_only=True,
        ),
    ),
    DRIVE_PROVIDER: (
        BrokerTool(
            slug="list_documents",
            description="List the connected Drive documents with their text content.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            read_only=True,
        ),
    ),
    GITHUB_PROVIDER: (
        BrokerTool(
            slug="create_commit_status",
            description="Publish a commit status: the verdict a review posts against a head SHA.",
            input_schema=CreateCommitStatusArgs.model_json_schema(),
            read_only=False,
        ),
        BrokerTool(
            slug="list_issues",
            description="List repository issues with labels, owners, status, and source links.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            read_only=True,
        ),
        BrokerTool(
            slug="list_pull_requests",
            description="List pull requests with review, check, issue, and merge state.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            read_only=True,
        ),
        BrokerTool(
            slug="list_members",
            description="List repository members with ownership areas and current load.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            read_only=True,
        ),
        BrokerTool(
            slug="get_delivery_metrics",
            description="Get pull-request delivery metrics and their reporting window.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            read_only=True,
        ),
    ),
    STRIPE_PROVIDER: (
        BrokerTool(
            slug="list_subscriptions",
            description="List subscriptions with customer, status, amount, and interval.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            read_only=True,
        ),
        BrokerTool(
            slug="list_invoices",
            description="List invoices with amount, status, customer, and billing date.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            read_only=True,
        ),
        BrokerTool(
            slug="list_balance_transactions",
            description="List balance transactions with net amount, fee, type, and date.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            read_only=True,
        ),
    ),
    HUBSPOT_PROVIDER: (
        BrokerTool(
            slug="list_companies",
            description="List customer companies with owner, lifecycle stage, and activity.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            read_only=True,
        ),
        BrokerTool(
            slug="list_deals",
            description="List customer deals with amount, stage, close date, and company.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            read_only=True,
        ),
        BrokerTool(
            slug="list_tickets",
            description="List customer tickets with priority, state, age, and company.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            read_only=True,
        ),
    ),
    GREENHOUSE_PROVIDER: (
        BrokerTool(
            slug="list_candidates",
            description="List candidates with role, stage, interviews, and source.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            read_only=True,
        ),
        BrokerTool(
            slug="list_scorecards",
            description="List submitted interview scorecards with ratings and evidence.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            read_only=True,
        ),
    ),
}


def _transaction():
    return ExtensionContext(
        store=ScopedStore(extension=NAME),
        credentials=CredentialAccess(declared=frozenset()),
    ).transaction()


def _moment(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


@dataclass(frozen=True)
class EvalEnvBroker:
    """Executes the catalog against the extension's tables, scoped to the calling workspace. Every
    mutation is durable, so a grader in another process reads the same end state the turn left."""

    async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]:
        needle = query.strip().lower()
        catalog = _CATALOG[provider]
        if not needle:
            return catalog
        matched = tuple(
            tool for tool in catalog if needle in tool.slug or needle in tool.description.lower()
        )
        return matched or catalog

    async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool:
        for tool in _CATALOG[provider]:
            if tool.slug == slug:
                return tool
        raise UnknownBrokerTool(slug)

    async def execute(
        self,
        workspace_id: UUID,
        provider: str,
        slug: str,
        arguments: Mapping[str, object],
        account_id: str,
        idempotency_key: str | None,
    ) -> dict[str, object]:
        if provider == EMAIL_PROVIDER:
            match slug:
                case "send_email":
                    return await self._send_email(
                        workspace_id, SendEmailArgs.model_validate(arguments)
                    )
                case "reply_all_email":
                    return await self._reply_all_email(
                        workspace_id, ReplyAllEmailArgs.model_validate(arguments)
                    )
                case "list_emails":
                    return await self._list_emails(
                        workspace_id, ListEmailsArgs.model_validate(arguments)
                    )
        if provider == CALENDAR_PROVIDER:
            match slug:
                case "create_event":
                    return await self._create_event(
                        workspace_id, CreateEventArgs.model_validate(arguments)
                    )
                case "list_events":
                    return await self._list_events(
                        workspace_id, ListEventsArgs.model_validate(arguments)
                    )
                case "update_event":
                    return await self._update_event(
                        workspace_id, UpdateEventArgs.model_validate(arguments)
                    )
                case "cancel_event":
                    return await self._cancel_event(
                        workspace_id, CancelEventArgs.model_validate(arguments)
                    )
        if provider == GITHUB_PROVIDER and slug == "create_commit_status":
            return await self._create_commit_status(
                CreateCommitStatusArgs.model_validate(arguments)
            )
        if provider == CODE_PROVIDER and slug == "search_code":
            return await self._search_code(SearchCodeArgs.model_validate(arguments))
        if provider in {
            DRIVE_PROVIDER,
            GITHUB_PROVIDER,
            STRIPE_PROVIDER,
            HUBSPOT_PROVIDER,
            GREENHOUSE_PROVIDER,
        }:
            if arguments:
                raise ValueError(f"{provider}.{slug} accepts no arguments")
            await self.schema(workspace_id, provider, slug)
            seeded = await ScopedStore(extension=NAME).get(f"{APP_FIXTURE_PREFIX}{provider}:{slug}")
            if not isinstance(seeded, dict):
                raise ValueError(f"no app fixture is seeded for {provider}.{slug}")
            return dict(seeded)
        raise UnknownBrokerTool(slug)

    async def _create_commit_status(self, args: CreateCommitStatusArgs) -> dict[str, object]:
        return {
            "sha": args.sha,
            "state": args.state,
            "context": args.context,
            "description": args.description,
            "target_url": args.target_url,
        }

    async def _search_code(self, args: SearchCodeArgs) -> dict[str, object]:
        """The response the eval seeded under this query, verbatim. An unseeded query fails loud
        rather than answering an empty page: a case whose fixture never landed would otherwise grade
        the agent against a payload nothing under test ever shaped."""
        seeded = await ScopedStore(extension=NAME).get(f"{CODE_FIXTURE_PREFIX}{args.query}")
        if not isinstance(seeded, dict):
            raise ValueError(f"no code-search fixture is seeded for query {args.query!r}")
        response: dict[str, object] = dict(seeded)
        return response

    async def _send_email(self, workspace_id: UUID, args: SendEmailArgs) -> dict[str, object]:
        email_id = uuid4()
        async with _transaction() as connection:
            await connection.execute(
                sa.insert(eval_env_email).values(
                    id=email_id,
                    workspace_id=workspace_id,
                    folder="sent",
                    sender=MAILBOX_ADDRESS,
                    recipients=list(args.to),
                    subject=args.subject,
                    body=args.body,
                    sent_at=datetime.now(UTC),
                )
            )
        return {"id": str(email_id), "status": "sent", "to": list(args.to)}

    async def _reply_all_email(
        self, workspace_id: UUID, args: ReplyAllEmailArgs
    ) -> dict[str, object]:
        email_id = uuid4()
        async with _transaction() as connection:
            original = (
                await connection.execute(
                    sa.select(eval_env_email).where(
                        eval_env_email.c.workspace_id == workspace_id,
                        eval_env_email.c.id == args.message_id,
                    )
                )
            ).one_or_none()
            if original is None:
                raise ValueError(f"no email {str(args.message_id)!r} in this mailbox")
            recipients = tuple(
                dict.fromkeys(
                    address
                    for address in (original.sender, *original.recipients)
                    if address.casefold() != MAILBOX_ADDRESS.casefold()
                )
            )
            if not recipients:
                raise ValueError(f"email {str(args.message_id)!r} has nobody to reply to")
            subject = (
                original.subject
                if original.subject.casefold().startswith("re:")
                else f"Re: {original.subject}"
            )
            await connection.execute(
                sa.insert(eval_env_email).values(
                    id=email_id,
                    workspace_id=workspace_id,
                    folder="sent",
                    sender=MAILBOX_ADDRESS,
                    recipients=list(recipients),
                    subject=subject,
                    body=args.body,
                    sent_at=datetime.now(UTC),
                )
            )
        return {"id": str(email_id), "status": "sent", "to": list(recipients)}

    async def _list_emails(self, workspace_id: UUID, args: ListEmailsArgs) -> dict[str, object]:
        conditions = [
            eval_env_email.c.workspace_id == workspace_id,
            eval_env_email.c.folder == args.folder,
        ]
        if args.query:
            needle = f"%{args.query}%"
            conditions.append(
                sa.or_(
                    eval_env_email.c.sender.ilike(needle),
                    eval_env_email.c.subject.ilike(needle),
                    eval_env_email.c.body.ilike(needle),
                )
            )
        async with _transaction() as connection:
            rows = (
                await connection.execute(
                    sa.select(eval_env_email)
                    .where(*conditions)
                    .order_by(eval_env_email.c.sent_at.desc())
                    .limit(args.limit)
                )
            ).all()
        return {
            "emails": [
                {
                    "id": str(row.id),
                    "from": row.sender,
                    "to": row.recipients,
                    "subject": row.subject,
                    "body": row.body,
                    "sent_at": row.sent_at.isoformat(),
                }
                for row in rows
            ]
        }

    async def _create_event(self, workspace_id: UUID, args: CreateEventArgs) -> dict[str, object]:
        event_id = uuid4()
        async with _transaction() as connection:
            await connection.execute(
                sa.insert(eval_env_event).values(
                    id=event_id,
                    workspace_id=workspace_id,
                    title=args.title,
                    start_at=_moment(args.start),
                    end_at=_moment(args.end),
                    attendees=list(args.attendees),
                    status=CONFIRMED,
                )
            )
        return {"id": str(event_id), "status": CONFIRMED}

    async def _list_events(self, workspace_id: UUID, args: ListEventsArgs) -> dict[str, object]:
        conditions = [eval_env_event.c.workspace_id == workspace_id]
        if args.query:
            conditions.append(eval_env_event.c.title.ilike(f"%{args.query}%"))
        async with _transaction() as connection:
            rows = (
                await connection.execute(
                    sa.select(eval_env_event)
                    .where(*conditions)
                    .order_by(eval_env_event.c.start_at)
                    .limit(args.limit)
                )
            ).all()
        return {"events": [self._event_json(row) for row in rows]}

    async def _update_event(self, workspace_id: UUID, args: UpdateEventArgs) -> dict[str, object]:
        changes: dict[str, object] = {}
        if args.title is not None:
            changes["title"] = args.title
        if args.start is not None:
            changes["start_at"] = _moment(args.start)
        if args.end is not None:
            changes["end_at"] = _moment(args.end)
        if args.attendees is not None:
            changes["attendees"] = list(args.attendees)
        if not changes:
            raise ValueError("update_event needs at least one field to change")
        return await self._change_event(workspace_id, args.event_id, changes)

    async def _cancel_event(self, workspace_id: UUID, args: CancelEventArgs) -> dict[str, object]:
        return await self._change_event(workspace_id, args.event_id, {"status": CANCELLED})

    async def _change_event(
        self, workspace_id: UUID, event_id: str, changes: dict[str, object]
    ) -> dict[str, object]:
        async with _transaction() as connection:
            result = await connection.execute(
                sa.update(eval_env_event)
                .where(
                    eval_env_event.c.workspace_id == workspace_id,
                    eval_env_event.c.id == UUID(event_id),
                )
                .values(**changes)
            )
            if result.rowcount != 1:
                raise ValueError(f"no event {event_id!r} in this calendar")
            row = (
                await connection.execute(
                    sa.select(eval_env_event).where(eval_env_event.c.id == UUID(event_id))
                )
            ).one()
        return self._event_json(row)

    def _event_json(self, row: sa.Row) -> dict[str, object]:
        return {
            "id": str(row.id),
            "title": row.title,
            "start": row.start_at.isoformat(),
            "end": row.end_at.isoformat(),
            "attendees": row.attendees,
            "status": row.status,
        }

    def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]:
        return ()

    async def stage_upload(
        self,
        workspace_id: UUID,
        provider: str,
        slug: str,
        filename: str,
        mimetype: str,
        md5: str,
    ) -> StagedUpload:
        raise RuntimeError("eval_env providers accept no file uploads")

    async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch:
        return BrokerSearch(tools=await self.tools(workspace_id, provider, query))

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(bearer=f"eval-env:{account}")


@dataclass(frozen=True)
class _EvalEnvOAuth:
    """A stub handoff: the connect flow is never exercised in evals — grants are seeded directly —
    but the registry requires a provider descriptor, and exchange stays honest if ever driven."""

    provider: str
    host: str

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        return f"https://{self.host}/authorize?state={state}&redirect_uri={redirect_uri}"

    async def exchange(
        self, code: str, redirect_uri: str, workspace_id: UUID, state: str
    ) -> OAuthAccount:
        return OAuthAccount(account_id=ACCOUNT_ID)


@dataclass(frozen=True)
class AppActionStore:
    kind_name: ClassVar[str] = APP_ACTION_KIND

    async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage:
        entries = await self._ext(ctx).store.list(APP_ACTION_KEY_PREFIX)
        rows = tuple(
            ObjectRow(
                name=key.removeprefix(APP_ACTION_KEY_PREFIX),
                summary=stored.result,
                fields={
                    "state": "applied",
                    "case": stored.spec.case,
                    "action": stored.spec.action,
                    "target": stored.spec.target,
                    "value": stored.spec.value,
                    "result": stored.result,
                },
            )
            for key, value in entries
            if (stored := StoredAppAction.model_validate(value))
        )
        return object_page(rows, query)

    async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[AppActionSpec] | None:
        stored = await self._stored(ctx, name)
        if stored is None:
            return None
        return ObjectDetail(
            spec=stored.spec,
            created_at=stored.created_at,
            updated_at=stored.updated_at,
        )

    async def status(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> dict[str, JsonValue] | None:
        stored = await self._stored(ctx, name)
        if stored is None:
            return None
        return {"state": "applied", "result": stored.result}

    async def apply(
        self,
        ctx: ToolContext,
        name: str,
        spec: AppActionSpec,
        old: AppActionSpec | None,
        *,
        expected_generation: UUID | None,
    ) -> None:
        stored = await self._stored(ctx, name)
        if stored is not None:
            if stored.spec != spec:
                raise ValueError(f"application action {name!r} already has a different request")
            return
        result = await self._apply_fixture(ctx, name, spec)
        now = datetime.now(UTC)
        await self._ext(ctx).store.put(
            APP_ACTION_KEY_PREFIX + name,
            StoredAppAction(
                spec=spec,
                result=result,
                created_at=now,
                updated_at=now,
            ).model_dump(mode="json"),
        )

    async def delete(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> None:
        raise VerbNotSupported("eval application actions are immutable")

    async def _apply_fixture(self, ctx: ToolContext, name: str, spec: AppActionSpec) -> str:
        key, field = {
            "meeting-tasks": (f"{APP_FIXTURE_PREFIX}{GITHUB_PROVIDER}:list_issues", "issues"),
            "issue-owner": (f"{APP_FIXTURE_PREFIX}{GITHUB_PROVIDER}:list_issues", "issues"),
            "pr-babysitter": (
                f"{APP_FIXTURE_PREFIX}{GITHUB_PROVIDER}:list_pull_requests",
                "pull_requests",
            ),
        }[spec.case]
        seeded = await self._ext(ctx).store.get(key)
        if not isinstance(seeded, dict):
            raise ValueError(f"no app fixture is seeded for {spec.case!r}")
        response = json.loads(json.dumps(seeded))
        records = response.get(field)
        if not isinstance(records, list):
            raise ValueError(f"app fixture {spec.case!r} has no {field}")
        match spec.case, spec.action, spec.target, spec.value:
            case "meeting-tasks", "create_issue", "support-runbook", "priya":
                if not any(
                    isinstance(item, dict) and item.get("number") == 900 for item in records
                ):
                    records.append(
                        {
                            "number": 900,
                            "title": "Update support runbook for billing cutover",
                            "state": "open",
                            "owner": "priya",
                        }
                    )
                result = "Issue #900 created for priya."
            case "issue-owner", "assign_issue", "521", "alex":
                issue = next(
                    (
                        item
                        for item in records
                        if isinstance(item, dict) and item.get("number") == 521
                    ),
                    None,
                )
                if issue is None:
                    raise ValueError("app fixture has no issue 521")
                issue["owner"] = "alex"
                issue["project_status"] = "Assigned"
                result = "Issue #521 assigned to alex."
            case "pr-babysitter", "set_babysitter", "743", "Gemini 3.7 Flash":
                pull_request = next(
                    (
                        item
                        for item in records
                        if isinstance(item, dict) and item.get("number") == 743
                    ),
                    None,
                )
                if pull_request is None:
                    raise ValueError("app fixture has no pull request 743")
                pull_request["babysitter"] = {
                    "enabled": True,
                    "model": "Gemini 3.7 Flash",
                    "state": "watching",
                }
                result = "PR #743 babysitter set to Gemini 3.7 Flash."
            case _:
                raise ValueError(f"invalid application action for {spec.case!r}")
        await self._ext(ctx).store.put(APP_ACTION_FIXTURE_PREFIX + name, response)
        return result

    async def _stored(self, ctx: ToolContext, name: str) -> StoredAppAction | None:
        value = await self._ext(ctx).store.get(APP_ACTION_KEY_PREFIX + name)
        return None if value is None else StoredAppAction.model_validate(value)

    def _ext(self, ctx: ToolContext) -> ExtensionContext:
        if ctx.ext is None:
            raise RuntimeError("eval app action dispatched without its ExtensionContext")
        return ctx.ext


APP_ACTION_OBJECT = ObjectKind(
    name=APP_ACTION_KIND,
    description="Fixed app-bench actions over the mutable connector fixture.",
    guidance="Apply only the exact action contract returned by the eval connector fixture.",
    spec_model=AppActionSpec,
    store=AppActionStore(),
    list_fields=frozenset({"state", "case", "action", "target", "value"}),
)


async def bound_app_qa_repair_tools(ctx: HookContext):
    if ctx.agent is None or ctx.agent.name != APP_QA_REPAIR_AGENT_NAME:
        return None
    if not isinstance(ctx.payload, PreToolUse):
        raise RuntimeError("app QA repair bounds require pre_tool_use")
    tool_input = ctx.payload.tool_input.model_dump()
    match ctx.payload.tool_name, tool_input:
        case "read", {"file_path": path} if path == APP_QA_SOURCE_PATH:
            return None
        case "edit", {"file_path": path, "edits": edits} if path == APP_QA_SOURCE_PATH:
            if any(edit.get("replace_all") is True for edit in edits):
                return Deny(reason="app QA repair does not allow replace_all")
            if ctx.turn is None:
                raise RuntimeError("app QA repair edit budget requires a turn")
            old_bytes = 0
            new_bytes = 0
            for edit in edits:
                old_string = edit.get("old_string")
                new_string = edit.get("new_string")
                if not isinstance(old_string, str) or not isinstance(new_string, str):
                    raise RuntimeError("app QA repair received invalid edit strings")
                old_bytes += len(old_string.encode())
                new_bytes += len(new_string.encode())
            key = APP_QA_EDIT_BUDGET_KEY.format(turn_id=ctx.turn.id)
            stored = await ctx.ext.store.get(key)
            budget = (
                AppQaEditBudget(calls=0, old_bytes=0, new_bytes=0)
                if stored is None
                else AppQaEditBudget.model_validate(stored)
            )
            updated = AppQaEditBudget(
                calls=budget.calls + 1,
                old_bytes=budget.old_bytes + old_bytes,
                new_bytes=budget.new_bytes + new_bytes,
            )
            if updated.calls > APP_QA_EDIT_CALL_LIMIT:
                return Deny(reason=f"app QA repair allows {APP_QA_EDIT_CALL_LIMIT} edit calls")
            if updated.old_bytes > APP_QA_EDIT_OLD_BYTES_LIMIT:
                return Deny(
                    reason=(f"app QA repair old_string byte limit is {APP_QA_EDIT_OLD_BYTES_LIMIT}")
                )
            if updated.new_bytes > APP_QA_EDIT_NEW_BYTES_LIMIT:
                return Deny(
                    reason=(f"app QA repair new_string byte limit is {APP_QA_EDIT_NEW_BYTES_LIMIT}")
                )
            if not await ctx.ext.store.put_if(
                key,
                updated.model_dump(mode="json"),
                stored,
            ):
                return Deny(reason="app QA repair edit budget changed concurrently")
            return None
        case "read" | "edit", _:
            return Deny(reason=f"app QA repair can change only {APP_QA_SOURCE_PATH}")
        case _:
            return Deny(reason="app QA repair allows only read and edit")


APP_QA_REPAIR_AGENT = AgentProvision(
    name=APP_QA_REPAIR_AGENT_NAME,
    spec=AgentSpec(
        prompt=APP_QA_REPAIR_PROMPT,
        purpose="Repairs one fixed application source from deterministic product QA issues.",
        model="claude-opus-5",
        reasoning="medium",
        internet_access_allowed=False,
        use_workspace_skills=False,
        sandbox_size="large",
        visibility="private",
    ),
    tools=("read", "edit"),
    icon="tool",
)


def manifest() -> Manifest:
    broker = EvalEnvBroker()
    return Manifest(
        name=NAME,
        version=VERSION,
        connectors=(
            ConnectorProvider(
                oauth=_EvalEnvOAuth(EMAIL_PROVIDER, EMAIL_HOST),
                label=EMAIL_LABEL,
                broker=broker,
            ),
            ConnectorProvider(
                oauth=_EvalEnvOAuth(CALENDAR_PROVIDER, CALENDAR_HOST),
                label=CALENDAR_LABEL,
                broker=broker,
            ),
            ConnectorProvider(
                oauth=_EvalEnvOAuth(CODE_PROVIDER, CODE_HOST),
                label=CODE_LABEL,
                broker=broker,
            ),
            ConnectorProvider(
                oauth=_EvalEnvOAuth(DRIVE_PROVIDER, DRIVE_HOST),
                label=DRIVE_LABEL,
                broker=broker,
            ),
            ConnectorProvider(
                oauth=_EvalEnvOAuth(GITHUB_PROVIDER, GITHUB_HOST),
                label=GITHUB_LABEL,
                broker=broker,
            ),
            ConnectorProvider(
                oauth=_EvalEnvOAuth(STRIPE_PROVIDER, STRIPE_HOST),
                label=STRIPE_LABEL,
                broker=broker,
            ),
            ConnectorProvider(
                oauth=_EvalEnvOAuth(HUBSPOT_PROVIDER, HUBSPOT_HOST),
                label=HUBSPOT_LABEL,
                broker=broker,
            ),
            ConnectorProvider(
                oauth=_EvalEnvOAuth(GREENHOUSE_PROVIDER, GREENHOUSE_HOST),
                label=GREENHOUSE_LABEL,
                broker=broker,
            ),
        ),
        objects=(APP_ACTION_OBJECT,),
        agents=(APP_QA_REPAIR_AGENT,),
        hooks=(
            HookSpec(
                event="pre_tool_use",
                handler=bound_app_qa_repair_tools,
                tools=("read", "edit"),
            ),
        ),
    )
