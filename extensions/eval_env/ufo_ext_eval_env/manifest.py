"""The deterministic eval environment: a mailbox and a calendar the agent reaches only through the
real connector dispatch (`list_external_tools` → `describe_external_tools` → `call_external_tool`),
backed by the extension's own workspace-scoped tables. Evals seed those tables, run a conversation,
and assert the end state on the same rows the broker mutated — a controllable domain over the
production seam, never a mock of it. The providers ride the `assistant_eval` pack only; a product
pack never lists them."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import BaseModel, Field

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
from ufo.sdk.manifest import ConnectorProvider, Manifest

NAME = "eval_env"
VERSION = "0.1.0"
EMAIL_PROVIDER = "eval_email"
EMAIL_LABEL = "Email (eval)"
EMAIL_HOST = "email.evalenv.test"
CALENDAR_PROVIDER = "eval_calendar"
CALENDAR_LABEL = "Calendar (eval)"
CALENDAR_HOST = "calendar.evalenv.test"
ACCOUNT_ID = "eval-env-account"
OWN_ADDRESS = "assistant@evalco.test"
MAX_LIST_LIMIT = 50
CONFIRMED = "confirmed"
CANCELLED = "cancelled"

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


class SendEmailArgs(BaseModel):
    to: tuple[str, ...] = Field(min_length=1, description="Recipient email addresses.")
    subject: str = Field(min_length=1, description="Subject line.")
    body: str = Field(min_length=1, description="Plain-text body.")


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


_CATALOG: dict[str, tuple[BrokerTool, ...]] = {
    EMAIL_PROVIDER: (
        BrokerTool(
            slug="send_email",
            description="Send a plain-text email from the member's mailbox.",
            input_schema=SendEmailArgs.model_json_schema(),
        ),
        BrokerTool(
            slug="list_emails",
            description="List emails in a folder, newest first, optionally filtered.",
            input_schema=ListEmailsArgs.model_json_schema(),
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
        raise UnknownBrokerTool(slug)

    async def _send_email(self, workspace_id: UUID, args: SendEmailArgs) -> dict[str, object]:
        email_id = uuid4()
        async with _transaction() as connection:
            await connection.execute(
                sa.insert(eval_env_email).values(
                    id=email_id,
                    workspace_id=workspace_id,
                    folder="sent",
                    sender=OWN_ADDRESS,
                    recipients=list(args.to),
                    subject=args.subject,
                    body=args.body,
                    sent_at=datetime.now(UTC),
                )
            )
        return {"id": str(email_id), "status": "sent", "to": list(args.to)}

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
        ),
    )
