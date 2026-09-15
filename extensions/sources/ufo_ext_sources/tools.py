"""The `source_trigger` object kind: the conversations that wake when a connection's feed changes.

A connection is one account's authority and its syncing streams land as `source` rows the moment
it lands, so nobody registers a feed. A trigger is one conversation's standing interest in one of
those feeds: apply the kind from the conversation and delete the row to stop. Each batch of changed
pages wakes that conversation. Only a shared connection can carry one — a private connection's
pages are disclosed to its owner alone, so the alert filter would drop every change it ever made —
and the `page_change` hook includes only the shared pages the trigger's agent may read.

A trigger narrows to one resource of that feed — a pull request, an issue — named by its URL, and
then only the changes about that resource wake the conversation: its comments, its reviews, its
checks passing or failing, and its merged or closed state, unless the trigger names the streams it
wants instead. Its name derives from the triple it is, so an apply under any other name
is refused with the one to use.

The `user_prompt_submit` and `post_tool_use` hooks offer one: a link in a member's message, in a
spawned coding child's result or in a tool's output that names a resource of a shared connection
this agent may read, and that the conversation does not watch yet, earns a `<watch_offer>` naming
the exact trigger to apply. The offer writes nothing; the agent applies the trigger in the open, so
the thread that opened a pull request hears that its checks failed in the thread and not in the
channel. A resource's changes reach it only on the streams the connection syncs."""

import json
import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from hashlib import sha256
from typing import ClassVar
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ufo.sdk.context import (
    SUBAGENT_SURFACE,
    AgentArchived,
    ExtensionContext,
    FiredBy,
    SourceReader,
    TurnRuntimeConfig,
)
from ufo.sdk.grants import FeedConnection, account_object_name, feed_connections
from ufo.sdk.manifest import (
    HookContext,
    HookOutcome,
    InjectContext,
    PageChangeBatch,
    PostToolUse,
    UserPromptSubmit,
)
from ufo.sdk.objects import (
    CONVERSATION_KIND,
    OBJECT_NAME_MAX_LENGTH,
    GeneratedObjectOwner,
    MemberObject,
    MemberReadableObjects,
    ObjectDetail,
    ObjectKind,
    ObjectLink,
    ObjectListQuery,
    ObjectPage,
    ObjectRef,
    ObjectRow,
    OwnedRow,
    UnknownObject,
    last_fires,
    object_page,
    owner_emails,
)
from ufo.sdk.sources import PageChange
from ufo.sdk.subjects import SHARED_SUBJECT
from ufo.sdk.surfaces import with_agent_detail
from ufo.sdk.tools import ToolContext
from ufo_ext_sources.pages import CONNECTION_OBJECT_KIND, PAGE_KIND
from ufo_ext_sources.resources import (
    canonical_resource,
    resource_digest,
    resource_keys,
    resource_matches,
    wakes_by_default,
)
from ufo_ext_sources.triggers import (
    ListedTrigger,
    SourceTrigger,
    SourceTriggerDelivery,
    SourceTriggerStore,
)

SOURCE_TRIGGER_KIND = "source_trigger"
SUMMARY_MAX = 120
TRIGGER_NAME_DIGEST_HEX = 8
TRIGGER_NAME_HEAD_MAX = OBJECT_NAME_MAX_LENGTH - TRIGGER_NAME_DIGEST_HEX - 1
ALERT_NAMED_MAX = 5
ALERT_LABEL_CHARS = 60
ALERT_CLOSING = (
    "No member is reading. Do what this conversation set the watch for; write to the member only "
    "when the change needs them."
)
WATCH_OFFER_MAX = 4
"""How many links one message or tool result is offered a trigger for. A board of links is not a
list of things to watch, and every offer costs the turn context."""
WATCH_OFFER_OPEN = "<watch_offer>"
WATCH_OFFER_CLOSE = "</watch_offer>"
MANIFEST_OPEN = "```yaml"
MANIFEST_CLOSE = "```"
OFFER_BREAK = "\n\n"
"""Each offer's manifest sits in its own yaml fence and a blank line parts one offer from the next,
so a second link's prose never reads as more of the first manifest."""
LINK = re.compile(r"https://[^\s<>\"'`\\()\[\]{}]+")
"""A link ends where prose or markup around it begins: whitespace, a quote, an angle bracket, a
backtick, a bracket, a brace, a parenthesis or a backslash. A coding child reports its pull request
as a markdown link inside a JSON-encoded payload, so the link there is followed by a closing
parenthesis and an escaped newline written as two characters."""
LINK_TRAIL = ".,;:!?*"
CHANGE_LOG_DIR = "sources"
DISPOSITIONS = ("added", "updated", "removed")
TRIGGER_GATE = "only the trigger's creator may change it; an admin may pause or resume it"
TRIGGER_DELETE_GATE = "only the trigger's creator or a workspace admin may delete a source trigger"


def _require_ext(ext: ExtensionContext | None) -> ExtensionContext:
    if ext is None:
        raise RuntimeError("source trigger objects dispatched without their ExtensionContext")
    return ext


def _require_triggers(ext: ExtensionContext | None) -> SourceTriggerStore:
    return SourceTriggerStore(_require_ext(ext))


def _feed_name(connection: FeedConnection) -> str:
    """The `connection` object a trigger names — one spelling of an account across the trigger's
    spec, its link, its alert and the offer that proposes it."""
    return account_object_name(connection.provider, connection.account_id)


def _feed_summary(connection: FeedConnection) -> str:
    """What a member reads a connection as. The workspace's own connection — a key it holds rather
    than an account someone connected — has no account handle to name."""
    if not connection.account_id:
        return connection.provider
    return f"{connection.provider} account {connection.account_id}"


def _portal_actions(
    owner: GeneratedObjectOwner,
    *,
    paused: bool,
    member_id: UUID,
    admin: bool,
) -> dict[str, bool]:
    owned = owner.member_id == member_id
    return {
        "pausable": not paused and (owned or admin),
        "resumable": paused and (owned or admin),
        "deletable": owned or admin,
    }


def _shared_reader(agent_id: UUID, connections: tuple[UUID, ...] | None = None) -> SourceReader:
    """Which feeds one agent may read of what the whole workspace shares. An alert and an offer both
    ask it, and neither has a live speaker whose private content could widen the answer."""
    return SourceReader(
        agent_id=agent_id,
        requesting_member_id=None,
        subjects=frozenset({SHARED_SUBJECT}),
        connections=connections,
    )


async def _reachable_feeds(
    ext: ExtensionContext, reader: SourceReader
) -> tuple[FeedConnection, ...]:
    """The connections this reader may read a stream of. A connection it holds no grant on is
    absent, and so is one whose streams have not landed a row yet, since a trigger on it would be a
    promise nothing keeps."""
    readable = await ext.readable_source_ids(reader)
    reachable = {record.connection_id for record in await ext.sources() if record.id in readable}
    return tuple(
        connection for connection in await feed_connections() if connection.id in reachable
    )


def trigger_name(
    connection: str, conversation_id: UUID, resource: str = "", streams: Sequence[str] = ()
) -> str:
    """A trigger IS what it names — the connection whose feed it watches, the conversation it
    wakes, and the resource and streams of that feed a narrowed trigger is about — so its object
    name derives from all of them, and an apply under any other name is refused with the one to
    use rather than filed as a second row over the same identity.

    It is spelled the way `account_object_name` spells an account, and for the same reason: the
    connection's own name leads it so a member reading a list of triggers sees which feed each one
    watches, and a digest of the whole of it qualifies it, because a resource is a URL and the head
    is truncated to leave the digest whole."""
    joined = ",".join(sorted(streams))
    identity = f"{connection}\0{conversation_id}\0{resource}\0{joined}".encode()
    qualifier = sha256(identity).hexdigest()[:TRIGGER_NAME_DIGEST_HEX]
    head = f"{connection}-{conversation_id.hex}"[:TRIGGER_NAME_HEAD_MAX].strip("-")
    return f"{head}-{qualifier}"


class SourceTriggerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection: str = Field(
        title="Connection",
        description="The shared connection whose feed this trigger watches, by its "
        f"{CONNECTION_OBJECT_KIND} object name.",
    )
    resource: str = Field(
        default="",
        title="Resource",
        description="The URL of one resource of that feed to narrow the trigger to — a GitHub "
        "pull request or issue. With no `streams`, the conversation wakes on the comments, the "
        "reviews, the checks that passed or failed, and the merged or closed state of it, and on "
        "nothing else about it. Leave it empty to watch the whole feed.",
    )
    streams: tuple[str, ...] = Field(
        default=(),
        title="Streams",
        description="The streams of that connection to narrow the trigger to. Only the changes "
        "on them wake the conversation. Leave it empty to watch every stream the connection "
        "syncs.",
    )
    delivery: SourceTriggerDelivery = Field(
        default="current",
        title="Delivery",
        description="Current delivery wakes this conversation for each batch of changes.",
    )
    paused: bool = Field(
        default=False,
        title="Paused",
        description=(
            "True stops the trigger waking its conversation without losing it; false wakes it "
            "again from the next batch of changes. It is the one field an apply on a standing "
            "trigger may change."
        ),
    )


@dataclass(frozen=True)
class _Watched:
    """One listed trigger beside the connection it watches. Every read of the kind answers from the
    pair, because a trigger's name, summary, spec and link are all the connection's to spell."""

    listed: ListedTrigger
    connection: FeedConnection

    @property
    def name(self) -> str:
        return trigger_name(
            _feed_name(self.connection),
            self.listed.trigger.conversation_id,
            self.listed.trigger.resource,
            self.listed.trigger.streams,
        )


@dataclass(frozen=True)
class SourceTriggerObjects(MemberReadableObjects[SourceTriggerSpec, GeneratedObjectOwner]):
    """The kind's handlers over the trigger store: a trigger is seen by whoever reads the
    conversation that owns it, plus its creator and a workspace admin — the gate is the base's, and
    this kind supplies only the `shared` fact it decides from, taken from that conversation's
    audience. Pausing and deleting stay the creator's and an admin's, so a member reading a shared
    trigger is never a member who can silence it. A trigger whose connection is gone is absent from
    every read here: disconnecting the account cascaded its rows away."""

    kind_name: ClassVar[str] = SOURCE_TRIGGER_KIND
    mutate_gate: ClassVar[str] = TRIGGER_GATE
    delete_gate: ClassVar[str] = TRIGGER_DELETE_GATE

    def _admin_can_apply(self, old: SourceTriggerSpec, spec: SourceTriggerSpec) -> bool:
        return spec.paused != old.paused

    async def member_page(
        self,
        ext: ExtensionContext | None,
        *,
        member_id: UUID,
        admin: bool,
        query: ObjectListQuery,
    ) -> ObjectPage:
        rows = tuple(
            row
            for row in await self._member_rows(ext, member_id=member_id)
            if self._visible(row.owner, member_id, admin) and self._listed(row, query)
        )
        owners = {row.name: row.owner for row in rows}
        page = object_page(
            tuple(ObjectRow(name=row.name, summary=row.summary, fields=row.fields) for row in rows),
            query,
        )
        return ObjectPage(
            rows=tuple(
                ObjectRow(
                    name=row.name,
                    summary=row.summary,
                    fields={
                        **row.fields,
                        **_portal_actions(
                            owners[row.name],
                            paused=row.fields["paused"] is True,
                            member_id=member_id,
                            admin=admin,
                        ),
                    },
                )
                for row in page.rows
            ),
            next_cursor=page.next_cursor,
        )

    async def member_detail(
        self,
        ext: ExtensionContext | None,
        name: str,
        *,
        member_id: UUID,
        admin: bool,
    ) -> MemberObject[SourceTriggerSpec] | None:
        rows = await self._member_rows(ext, member_id=member_id)
        found = next((row for row in rows if row.name == name), None)
        if found is None or not self._visible(found.owner, member_id, admin):
            return None
        detail = await self._member_object(ext, name, found.owner, member_id=member_id)
        if detail is None:
            return None
        return MemberObject(
            row=ObjectRow(
                name=found.name,
                summary=found.summary,
                fields={
                    **found.fields,
                    **_portal_actions(
                        found.owner,
                        paused=found.fields["paused"] is True,
                        member_id=member_id,
                        admin=admin,
                    ),
                },
            ),
            detail=detail,
        )

    async def _member_rows(
        self, ext: ExtensionContext | None, *, member_id: UUID | None
    ) -> tuple[OwnedRow[GeneratedObjectOwner], ...]:
        """A trigger is shared exactly as far as its owning conversation, so every surface listing
        the kind answers one question one way. Its summary names the connection, since what a
        member came to read is which feed wakes them and not the triple's derived name."""
        return await self._rows(await self._watched(ext), member_id=member_id)

    async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[GeneratedObjectOwner], ...]:
        watched = await self._watched(ctx.ext)
        connections = ctx.connection_scope
        if connections is not None:
            allowed = frozenset(connections)
            watched = tuple(row for row in watched if row.listed.trigger.connection_id in allowed)
        return await self._rows(watched, member_id=ctx.speaker_member_id)

    async def _rows(
        self, watched: tuple[_Watched, ...], *, member_id: UUID | None
    ) -> tuple[OwnedRow[GeneratedObjectOwner], ...]:
        emails = await owner_emails(row.listed.trigger.created_by_member_id for row in watched)
        fires = await last_fires(SOURCE_TRIGGER_KIND, tuple(row.name for row in watched))
        return tuple(
            OwnedRow(
                name=row.name,
                summary=_trigger_summary(row.connection, row.listed.trigger),
                owner=GeneratedObjectOwner(
                    member_id=row.listed.trigger.created_by_member_id,
                    audience=row.listed.audience,
                    generation=row.listed.trigger.id,
                ),
                fields={
                    "conversation": str(row.listed.trigger.conversation_id),
                    "connection": _feed_name(row.connection),
                    "resource": row.listed.trigger.resource,
                    "streams": ",".join(row.listed.trigger.streams),
                    "delivery": row.listed.trigger.delivery,
                    "paused": row.listed.trigger.paused,
                    "provider": row.connection.provider,
                    "last_run_at": (None if row.name not in fires else fires[row.name].isoformat()),
                    "origin": row.listed.surface_label or "Portal",
                    "owner_email": emails.get(row.listed.trigger.created_by_member_id),
                    "mine": row.listed.trigger.created_by_member_id == member_id,
                },
            )
            for row in watched
        )

    async def _member_object(
        self,
        ext: ExtensionContext | None,
        name: str,
        owner: GeneratedObjectOwner,
        *,
        member_id: UUID | None,
    ) -> ObjectDetail[SourceTriggerSpec] | None:
        found = await self._find(ext, name)
        if found is None or found.listed.trigger.id != owner.generation:
            return None
        trigger = found.listed.trigger
        links = [
            ObjectLink(
                relation="watches",
                target=ObjectRef(kind=CONNECTION_OBJECT_KIND, name=_feed_name(found.connection)),
            )
        ]
        links.append(
            ObjectLink(
                relation="reports_to",
                target=ObjectRef(
                    kind=CONVERSATION_KIND,
                    name=str(trigger.conversation_id),
                ),
            )
        )
        return ObjectDetail(
            spec=SourceTriggerSpec(
                connection=_feed_name(found.connection),
                resource=trigger.resource,
                streams=trigger.streams,
                delivery=trigger.delivery,
                paused=trigger.paused,
            ),
            created_at=trigger.created_at,
            updated_at=trigger.updated_at,
            links=tuple(links),
        )

    async def _status(
        self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner
    ) -> dict[str, JsonValue] | None:
        found = await self._find(ctx.ext, name)
        if found is None or found.listed.trigger.id != owner.generation:
            return None
        trigger = found.listed.trigger
        emails = await owner_emails((trigger.created_by_member_id,))
        return {
            "conversation": str(trigger.conversation_id),
            "connection": _feed_name(found.connection),
            "resource": trigger.resource,
            "streams": ",".join(trigger.streams),
            "delivery": trigger.delivery,
            "paused": trigger.paused,
            "origin": found.listed.surface_label or "Portal",
            "owner_email": emails.get(trigger.created_by_member_id),
            "mine": trigger.created_by_member_id == ctx.speaker_member_id,
        }

    async def _apply_owned(
        self,
        ctx: ToolContext,
        name: str,
        spec: SourceTriggerSpec,
        old: SourceTriggerSpec | None,
        owner: GeneratedObjectOwner | None,
    ) -> None:
        """Creating a trigger, and pausing or resuming a standing one — the trigger's identity is
        the connection, resource, streams and conversation it names, so nothing else can be changed
        in place. A resource is read against the connection's provider into the one link the row
        stores, so every spelling of a pull request is the one trigger. On a create the name is
        checked before any write, because it names the conversation: applying the name of a trigger
        some other conversation already holds would otherwise report success for a conversation no
        row was written for — which is also what keeps the portal's lane, whose turn runs in the
        member's own intent conversation, from creating a trigger there. What the connection syncs
        today is weighed on the create alone: a stream the feed stopped carrying is when a member
        reaches for Pause, and refusing it would leave the trigger standing and unstoppable."""
        connection = await self._watchable(ctx, spec.connection)
        if spec.resource:
            resource = canonical_resource(connection.provider, spec.resource)
            if resource is None:
                raise ValueError(
                    f"{spec.resource!r} is not the link of a resource of {spec.connection!r} that "
                    f"a {SOURCE_TRIGGER_KIND} narrows to"
                )
            spec = spec.model_copy(update={"resource": resource})
        if spec.streams:
            spec = spec.model_copy(update={"streams": tuple(sorted(set(spec.streams)))})
        if owner is not None:
            found = await self._find(ctx.ext, name)
            if found is None or found.listed.trigger.id != owner.generation:
                raise ValueError(f"{SOURCE_TRIGGER_KIND} {name!r} changed while editing")
            trigger = found.listed.trigger
            identity = (_feed_name(found.connection), trigger.resource, trigger.streams)
            if identity != (spec.connection, spec.resource, spec.streams):
                raise ValueError(
                    f"a {SOURCE_TRIGGER_KIND} is the connection, resource, streams and "
                    "conversation it names — delete this one and apply another"
                )
            if spec.paused != trigger.paused:
                await _require_triggers(ctx.ext).set_paused(trigger, spec.paused)
            return
        creating_member_id = ctx.require_speaker()
        if spec.streams:
            synced = frozenset(
                stream
                for record in await _require_ext(ctx.ext).sources()
                if record.connection_id == connection.id
                and isinstance(stream := record.config.get("stream"), str)
            )
            if unknown := sorted(set(spec.streams) - synced):
                raise ValueError(
                    f"{spec.connection!r} syncs no {', '.join(unknown)} — it syncs "
                    f"{', '.join(sorted(synced))}"
                )
        expected = trigger_name(
            _feed_name(connection), ctx.turn.conversation_id, spec.resource, spec.streams
        )
        if name != expected:
            raise ValueError(
                f"a {SOURCE_TRIGGER_KIND} is named for the triple it is — apply it as {expected!r}"
            )
        if spec.paused:
            raise ValueError(f"a new {SOURCE_TRIGGER_KIND} watches from the moment it is applied")
        await _require_triggers(ctx.ext).create(
            conversation_id=ctx.turn.conversation_id,
            connection_id=connection.id,
            delivery=spec.delivery,
            created_by_member_id=creating_member_id,
            requesting_message_ref=ctx.require_requesting_message(),
            internet_access=(
                None if ctx.turn.runtime_config is None else ctx.turn.runtime_config.internet_access
            ),
            resource=spec.resource,
            streams=spec.streams,
        )

    async def _watchable(self, ctx: ToolContext, connection: str) -> FeedConnection:
        """The connection this caller may watch. One whose feed this turn cannot read comes back as
        nothing, so a stranger guessing a name never learns one exists. A private connection the
        caller does read is refused for what it is: its pages are disclosed to its owner alone, so
        the alert filter would drop every change it ever made."""
        reachable = await _reachable_feeds(_require_ext(ctx.ext), ctx.source_reader())
        found = next(
            (candidate for candidate in reachable if _feed_name(candidate) == connection), None
        )
        if found is None:
            raise UnknownObject(f"no {CONNECTION_OBJECT_KIND} object named {connection!r}")
        if not found.shared:
            raise ValueError(
                f"{connection!r} syncs privately to the member who connected it, so its changes "
                "reach no conversation; share the connection to watch it"
            )
        return found

    async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None:
        found = await self._find(ctx.ext, name)
        if found is None or found.listed.trigger.id != owner.generation:
            raise ValueError(f"{SOURCE_TRIGGER_KIND} {name!r} changed while deleting")
        await _require_triggers(ctx.ext).remove(found.listed.trigger)

    async def _find(self, ext: ExtensionContext | None, name: str) -> _Watched | None:
        return next((row for row in await self._watched(ext) if row.name == name), None)

    async def _watched(self, ext: ExtensionContext | None) -> tuple[_Watched, ...]:
        """This agent's triggers, each beside the connection it watches. One whose connection is
        gone is absent: disconnecting the account cascaded the trigger away, and a row this read
        raced past has nothing left to name."""
        feeds = {connection.id: connection for connection in await feed_connections()}
        return tuple(
            _Watched(listed=row, connection=feeds[row.trigger.connection_id])
            for row in await _require_triggers(ext).list_reported()
            if row.trigger.connection_id in feeds
        )


async def on_page_change(ctx: HookContext) -> HookOutcome:
    """Wake each changed connection's triggers. Each trigger sends one batch to its conversation.
    Only shared pages that the trigger's agent may read cause a wake.

    A trigger narrowed to one resource is woken by the changes about that resource alone, and one
    narrowed to streams by the changes on those streams alone. A resource-narrowed trigger naming no
    stream is woken by the provider's minimal set of them."""
    match ctx.payload:
        case PageChangeBatch(changes=changes):
            pass
        case _:
            raise RuntimeError("sources hook fired on a non-page_change payload")
    feeds = {connection.id: connection for connection in await feed_connections()}
    feed_of_source = {
        record.id: feeds[record.connection_id]
        for record in await ctx.ext.sources()
        if record.connection_id in feeds
    }
    by_feed: dict[UUID, tuple[FeedConnection, list[PageChange]]] = {}
    for change in changes:
        feed = feed_of_source.get(change.source_id)
        if feed is None:
            continue
        by_feed.setdefault(feed.id, (feed, []))[1].append(change)
    triggers = _require_triggers(ctx.ext)
    for feed, feed_changes in by_feed.values():
        woken = await triggers.waking(feed.id)
        if not woken:
            continue
        shared = [change for change in feed_changes if change.subject == SHARED_SUBJECT]
        if not shared:
            continue
        for trigger in woken:
            if trigger.created_by_member_id is None:
                await triggers.retire_unattributed(trigger)
                continue
            readable = await ctx.ext.readable_source_ids(_shared_reader(trigger.agent_id))
            authorized = [change for change in shared if change.source_id in readable]
            authorized = _about_resource(feed.provider, trigger, authorized)
            authorized = _on_streams(feed.provider, trigger, authorized)
            if not authorized:
                continue
            with suppress(AgentArchived):
                await _fire_trigger(
                    ctx.ext,
                    feed,
                    trigger,
                    authorized,
                )
    return None


def _about_resource(
    provider: str, trigger: SourceTrigger, changes: list[PageChange]
) -> list[PageChange]:
    """The changes one trigger is woken by: all of them for a whole-feed trigger, and for a narrowed
    one the changes whose page the provider linked to its resource."""
    if not trigger.resource:
        return changes
    return [
        change for change in changes if resource_matches(provider, trigger.resource, change.body)
    ]


def _on_streams(
    provider: str, trigger: SourceTrigger, changes: list[PageChange]
) -> list[PageChange]:
    """The changes one trigger's streams admit: those on the streams it names, and only those. A
    trigger naming no stream takes the whole feed where it watches the whole feed, and the
    provider's minimal set where it watches one resource — the comments, reviews, finished checks
    and closures a member asked about that pull request to hear, rather than every edit that moves a
    page linked to it."""
    if trigger.streams:
        return [change for change in changes if change.stream in trigger.streams]
    if not trigger.resource:
        return changes
    return [change for change in changes if wakes_by_default(provider, change.stream, change.body)]


def _trigger_summary(connection: FeedConnection, trigger: SourceTrigger) -> str:
    """What a member reads the trigger as: the resource it watches where it watches one, since a
    thread's watch is about that pull request and not about the feed carrying it."""
    feed = f"{_feed_name(connection)} ({_feed_summary(connection)})"
    watched = feed if not trigger.resource else f"{trigger.resource} on {feed}"
    if trigger.streams:
        watched = f"{watched}, {', '.join(trigger.streams)} only"
    return watched[:SUMMARY_MAX]


async def on_link_seen(ctx: HookContext) -> HookOutcome:
    """Offer this conversation a trigger on each resource the text it just read names — a pull
    request in a member's message, in a spawned coding child's result, in a tool's output — when a
    shared connection this agent may read syncs it and the conversation does not watch it yet.

    The offer is text and nothing else: no row is written, so a link in a machine conversation makes
    no standing waker, and the agent decides in the open whether the thread should hear about the
    resource. A conversation a member does not read — a spawned child's, a room this extension
    opened for an alert — is offered nothing, since a trigger applied there would wake nobody. Each
    offer names the exact trigger to apply, so taking it is one call.

    One resource is one offer however many ways the text spells it — a provider's record names a
    pull request as its page and again as an API link — and the offer spells it the way the text
    spells its page, so a spelling read out of an API form never displaces the one a person would
    write."""
    match ctx.payload:
        case UserPromptSubmit(text=text) | PostToolUse(output=text):
            pass
        case _:
            raise RuntimeError("sources link hook fired on an unexpected payload")
    if ctx.turn is None:
        return None
    links = _links_in(text)
    if not links:
        return None
    ext = _require_ext(ctx.ext)
    conversation_id = ctx.turn.conversation_id
    facts = await ext.conversation_facts((conversation_id,))
    if conversation_id not in facts or facts[conversation_id].surface in (
        SUBAGENT_SURFACE,
        ext.store.extension,
    ):
        return None
    feeds = await _reachable_feeds(
        ext,
        _shared_reader(
            ctx.turn.agent_id,
            None if ctx.turn.runtime_config is None else ctx.turn.runtime_config.connections,
        ),
    )
    if not feeds:
        return None
    named = [
        (feed, resource, link.lower().startswith(resource.lower()))
        for link in links
        for feed in feeds
        if (resource := canonical_resource(feed.provider, link)) is not None
    ]
    providers = {feed.id: feed.provider for feed in feeds}
    seen = {
        (feed_id, key)
        for feed_id, watched in await _require_triggers(ctx.ext).watched(conversation_id)
        if feed_id in providers
        for key in resource_keys(providers[feed_id], watched)
    }
    offers: list[str] = []
    for feed, resource, _ in sorted(named, key=lambda spelled: not spelled[2]):
        keys = {(feed.id, key) for key in resource_keys(feed.provider, resource)}
        if seen & keys:
            continue
        seen |= keys
        manifest = yaml.safe_dump(
            {
                "kind": SOURCE_TRIGGER_KIND,
                "name": trigger_name(_feed_name(feed), conversation_id, resource),
                "spec": {
                    "connection": _feed_name(feed),
                    "resource": resource,
                    "delivery": "current",
                },
            },
            sort_keys=False,
        ).strip()
        offers.append(
            f"{resource} is a resource of the connection {_feed_name(feed)!r} "
            f"({_feed_summary(feed)}) this workspace syncs. To hear its changes in this "
            f"conversation, call object_apply with this manifest:\n"
            f"{MANIFEST_OPEN}\n{manifest}\n{MANIFEST_CLOSE}"
        )
    if not offers:
        return None
    return InjectContext(
        text="\n".join(
            (WATCH_OFFER_OPEN, OFFER_BREAK.join(offers[:WATCH_OFFER_MAX]), WATCH_OFFER_CLOSE)
        )
    )


def _links_in(text: str) -> tuple[str, ...]:
    """Every https link the text carries, once each, in order, with the punctuation prose hangs on
    a link stripped."""
    found: list[str] = []
    for match in LINK.finditer(text):
        link = match.group().rstrip(LINK_TRAIL)
        if link not in found:
            found.append(link)
    return tuple(found)


async def _fire_trigger(
    ext: ExtensionContext,
    connection: FeedConnection,
    trigger: SourceTrigger,
    authorized: list[PageChange],
) -> None:
    """Deliver one trigger's changes to its conversation."""
    fired_by = FiredBy(
        kind=SOURCE_TRIGGER_KIND,
        name=trigger_name(
            _feed_name(connection), trigger.conversation_id, trigger.resource, trigger.streams
        ),
        title=_trigger_summary(connection, trigger),
        provider=connection.provider,
    )
    batch_id = sha256(
        "\n".join(f"{change.revision}:{change.page_id.hex}" for change in authorized).encode()
    ).hexdigest()
    latest = max(change.changed_at for change in authorized).isoformat()
    path = await _write_change_log(
        ext,
        trigger.conversation_id,
        _trigger_scope(connection, trigger),
        f"{latest}-{batch_id}",
        authorized,
    )
    await ext.invoke(
        trigger.conversation_id,
        trigger.agent_id,
        alert_message(connection, trigger, authorized, path),
        idempotency_key=(
            f"source-trigger:{_trigger_scope(connection, trigger)}:"
            f"{trigger.conversation_id.hex}:{batch_id}"
        ),
        holds_work_already_done=True,
        standalone=True,
        requesting_message_ref=trigger.requesting_message_ref,
        fired_by=fired_by,
        runtime_config=TurnRuntimeConfig(
            connections=(trigger.connection_id,),
            internet_access=trigger.internet_access,
        ),
    )


def _trigger_scope(connection: FeedConnection, trigger: SourceTrigger) -> str:
    """What one trigger watches, as one path segment: the connection, or the connection and the
    digest of its resource. It names the trigger's change-log directory and its alert's idempotency
    key — a conversation holds a whole-feed trigger and a narrowed one on the same connection, one
    landing stamps every page it carries with the same `changed_at`, and under the connection alone
    the second log would overwrite the first and the second alert would be dropped as the first's
    repeat."""
    scope = _feed_name(connection)
    if trigger.resource:
        scope = f"{scope}/{resource_digest(trigger.resource)}"
    if trigger.streams:
        scope = f"{scope}/{','.join(trigger.streams)}"
    return scope


async def _write_change_log(
    ext: ExtensionContext,
    conversation_id: UUID,
    directory: str,
    batch_name: str,
    changes: list[PageChange],
) -> str | None:
    """The whole delta as one JSON line per changed page, written into the woken
    conversation's workspace so the alerted agent reads it with its file tools instead of carrying
    it in context. Named for the latest change and the batch digest, so a replayed batch overwrites
    its own line-for-line identical file rather than appending a duplicate. A failed write
    propagates: the page feed inlines every body through this same blob store, so storage being
    unreachable fails the batch before the hook runs, and a handler that raises leaves the cursor
    unadvanced for the next tick to retry."""
    if ext.files is None:
        return None
    body = "".join(
        json.dumps(
            {
                "page": f"{PAGE_KIND}/{change.page_id}",
                "stream": change.stream,
                "title": change.title,
                "change": _disposition(change),
                "as_of": change.as_of.isoformat(),
            },
            sort_keys=True,
        )
        + "\n"
        for change in changes
    )
    path = await ext.files.write_runtime(
        conversation_id, CHANGE_LOG_DIR, f"{directory}/{batch_name}.jsonl", body.encode()
    )
    await ext.files.prune_runtime(conversation_id, CHANGE_LOG_DIR, directory)
    return path


def _disposition(change: PageChange) -> str:
    """Which of the three things this replay did to the page. The sync driver stamps one `now` into
    both `created_at` and `updated_at` when it first indexes a row and only `updated_at` when it
    rewrites one, so equal stamps mark a page this batch adds."""
    if change.tombstone:
        return "removed"
    return "added" if change.created_at == change.changed_at else "updated"


def _stream_counts(changes: list[PageChange]) -> str:
    """What changed, per stream — `pull_requests: 3 added, 47 updated; issues: 1 removed`. Counts
    are what the alert carries; the page ids live in the change log."""
    counted: dict[str, Counter[str]] = defaultdict(Counter)
    for change in changes:
        counted[change.stream][_disposition(change)] += 1
    return "; ".join(
        f"{stream}: "
        + ", ".join(
            f"{tally[disposition]} {disposition}"
            for disposition in DISPOSITIONS
            if tally[disposition]
        )
        for stream, tally in sorted(counted.items())
    )


def alert_message(
    connection: FeedConnection,
    trigger: SourceTrigger,
    changes: list[PageChange],
    log_path: str | None,
) -> str:
    """What one trigger's batch of changes says to the conversation it wakes: the headline every
    member's view draws, then the detail the agent alone reads. Evals that stage a woken turn build
    their inbound here, so a case reads the words the deploy sends."""
    if len(changes) <= ALERT_NAMED_MAX:
        detail = (
            "object_get refs, in order, unchanged: "
            f"{'; '.join(f'{PAGE_KIND}/{change.page_id}' for change in changes)}."
        )
    elif log_path is not None:
        detail = (
            f"One JSON line per changed page in {log_path}; narrow it with jq or grep, then "
            "object_get the refs that matter."
        )
    else:
        detail = (
            "List them with object_list page, filtered on this source and stream, ordered by "
            "updated_at desc."
        )
    feed = f"connection {_feed_name(connection)} ({_feed_summary(connection)})"
    watched = f"{trigger.resource} on {feed}" if trigger.resource else feed
    return with_agent_detail(
        _headline(connection, changes),
        f"{_stream_counts(changes)} on {watched}.\n{detail}\n{ALERT_CLOSING}",
    )


def _headline(connection: FeedConnection, changes: list[PageChange]) -> str:
    """The one line a member reads of the alert, under the provider's name and beside its mark,
    and the name a conversation it opens carries for good — nothing but the payload names a
    conversation no member spoke in. The changed pages' own titles follow: under counts alone every
    batch of one feed reads alike. The feed replays in revision order, so the last change of a long
    batch is its newest."""
    if len(changes) > ALERT_NAMED_MAX:
        titles = f"{_label(changes[-1])} and {len(changes) - 1} other pages"
    else:
        titles = "; ".join(_label(change) for change in changes)
    return f"{connection.label} update: {titles}"


def _label(change: PageChange) -> str:
    """What a member calls one changed page: its synced title, bounded."""
    return change.title[:ALERT_LABEL_CHARS] if change.title else "an untitled page"


SOURCE_TRIGGER_OBJECT = ObjectKind(
    name=SOURCE_TRIGGER_KIND,
    description=(
        "A standing wake-up for one shared connection's feed, or for one resource or some "
        "streams of it: each batch of changed pages wakes a conversation. Only its creator or an "
        "admin may pause or delete it."
    ),
    guidance=(
        "Apply a manifest naming a shared connection with `delivery: current` to wake this "
        "conversation for each batch of "
        "changes. Set `resource` to the URL of one pull request "
        "or issue of a GitHub connection to be woken by its comments, its reviews, its checks "
        "that passed or failed, and its merged or closed state, and by nothing else about it. "
        "Set `streams` to stream names of that connection to be woken by "
        "the changes on them alone, which is how to hear every change about a resource; applying "
        "an unsynced name answers with the ones it syncs. "
        "Leave both empty to wake on every stream of the whole feed. "
        f"A {SOURCE_TRIGGER_KIND} IS the connection, resource, streams, and owning conversation "
        "it names, so its name derives from all of them. Delete it to stop, and delete it once the "
        "pull request or issue it watches is merged or closed — only its creator or a workspace "
        "admin may. A private or unknown "
        "connection cannot be watched. Disconnecting the account removes every trigger on it. "
        "Applying `paused: true` on a standing trigger stops it waking the conversation and keeps "
        "it; false wakes it again from the next batch. That is the one field an apply may change — "
        "an apply naming a different connection, resource or streams is refused, and an admin who "
        "did not create it may pause, resume or delete it and nothing else. "
        "Listing returns `connection`, `resource`, `streams`, `delivery`, `paused`, the owning "
        "`conversation`, its creator (`owner_email`), `origin`, the `provider` whose feed it "
        "watches, and `last_run_at` — when it last woke a conversation, null until it has."
    ),
    spec_model=SourceTriggerSpec,
    store=SourceTriggerObjects(),
    list_fields=frozenset(
        {
            "conversation",
            "connection",
            "resource",
            "streams",
            "delivery",
            "paused",
            "provider",
            "last_run_at",
            "origin",
            "owner_email",
            "mine",
        }
    ),
    agent_target_verbs=frozenset({"list", "get", "delete"}),
)
