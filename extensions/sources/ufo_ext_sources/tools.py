"""The `source` and `source_trigger` object kinds: registered content-sync bindings managed through
the object verbs, and the conversations that wake when one changes.

A source object is one provider binding — an account (or the workspace's BYOK credential) plus a
tenant URL where the provider needs one — carrying the selected streams, each stream a `source`
row the core sync driver polls. Identity IS the binding, so names derive from it
(`<provider>-<8-hex digest>`): apply with the wrong name refuses and hands back the exact one, and
re-applying the identical spec is a no-op.

The streams are what a binding carries rather than which binding it is, so an apply settles it on
exactly the streams the submit names: one row is registered for each stream it adds, and the row of
each stream it drops is removed with its pages — the one act that clears what a stream synced, which
is why dropping one needs no delete of the binding and why nothing ever recreates it.

A stream that declares its own reach — an email message stream, 30 days — bounds its first sync to
that window or to whatever `backfill_days` asks for, resolved into an absolute date the row keeps.
Raising it later re-pins further back from that same date and refetches; lowering it is
delete-and-recreate. The window is judged on the streams a submit keeps alone, so a submit that
drops the last windowed stream drops the window with that row. A stream declaring none takes no
cutoff and reports none.

A source is private to its registering member by default; the model decides `shared` at
registration, and only the registrar may later flip a private source to shared — the reverse is
delete-and-recreate. Delete is registrar-or-admin. Validation refuses with the
valid provider and stream sets, so discovery is error-driven plus `object_explain`.

A source trigger is one conversation's standing interest in one shared source: apply the kind from
the conversation and delete the row to stop. It can wake that conversation for each source batch,
or open one stable agent conversation per changed page. Its name derives from the source and the
conversation that owns the trigger. Only a shared source can carry one. The `page_change` hook
includes only shared pages that the agent may read.

A trigger narrows to one resource of that source — a pull request, an issue — named by its URL, and
then only the changes about that resource wake the conversation. The `user_prompt_submit` and
`post_tool_use` hooks offer one: a link in a member's message, in a spawned coding child's result or
in a tool's output that names a resource of a shared source this agent may read, and that the
conversation does not watch yet, earns a `<watch_offer>` naming the exact trigger to apply. The
offer writes nothing; the agent applies the trigger in the open, so the thread that opened a pull
request hears that its checks failed in the thread and not in the channel. A resource's changes
reach it only on the streams the source syncs."""

import json
import re
from collections import Counter, defaultdict
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, ClassVar, Literal
from urllib.parse import urlsplit
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ufo.sdk.audience import Audience, conversation_audience
from ufo.sdk.authority import authority_from_member_id, authority_member_id
from ufo.sdk.authproxy import DIRECT_ACCOUNT
from ufo.sdk.connectors import ConnectorRegistry
from ufo.sdk.context import (
    SUBAGENT_SURFACE,
    AgentArchived,
    CredentialSlotUnset,
    ExtensionContext,
    SourceReader,
)
from ufo.sdk.credentials import credential_object_name
from ufo.sdk.grants import account_object_name
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
    CREDENTIAL_KIND,
    AdminRequired,
    GeneratedObjectOwner,
    MemberReadableObjects,
    ObjectDetail,
    ObjectKind,
    ObjectLink,
    ObjectOwner,
    ObjectRef,
    OwnedRow,
    UnknownObject,
    VerbNotSupported,
    owner_emails,
)
from ufo.sdk.sources import (
    ConnectorSourceConfig,
    PageChange,
    binding_name,
)
from ufo.sdk.subjects import SHARED_SUBJECT, member_subject, subject_shared
from ufo.sdk.tools import ConnectUnavailable, SpeakerRequired, ToolContext
from ufo_ext_sources.pages import PAGE_KIND
from ufo_ext_sources.registry import CONNECTORS, SOURCE_KIND
from ufo_ext_sources.resources import canonical_resource, resource_digest, resource_matches
from ufo_ext_sources.triggers import (
    ListedTrigger,
    SourceTrigger,
    SourceTriggerDelivery,
    SourceTriggerStore,
)

CONNECTION_OBJECT_KIND = "connection"
SOURCE_TRIGGER_KIND = "source_trigger"
SUMMARY_MAX = 120
MAX_BACKFILL_DAYS = 36500
ALERT_NAMED_MAX = 5
ALERT_LABEL_CHARS = 60
WATCH_OFFER_MAX = 4
"""How many links one message or tool result is offered a trigger for. A board of links is not a
list of things to watch, and every offer costs the turn context."""
WATCH_OFFER_OPEN = "<watch_offer>"
WATCH_OFFER_CLOSE = "</watch_offer>"
LINK = re.compile(r"https://[^\s<>\"'`\\()\[\]{}]+")
"""A link ends where prose or markup around it begins: whitespace, a quote, an angle bracket, a
backtick, a bracket, a brace, a parenthesis or a backslash. A coding child reports its pull request
as a markdown link inside a JSON-encoded payload, so the link there is followed by a closing
parenthesis and an escaped newline written as two characters."""
LINK_TRAIL = ".,;:!?*"
CHANGE_LOG_DIR = "sources"
DISPOSITIONS = ("added", "updated", "removed")
DOMAIN_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
TENANT_URL_RULES: dict[str, tuple[re.Pattern[str], re.Pattern[str], str]] = {
    "active_campaign": (
        re.compile(rf"{DOMAIN_LABEL}\.api-us1\.com"),
        re.compile(r"/?"),
        "https://<account>.api-us1.com",
    ),
    "bamboohr": (
        re.compile(r"api\.bamboohr\.com"),
        re.compile(rf"/api/gateway\.php/{DOMAIN_LABEL}/?"),
        "https://api.bamboohr.com/api/gateway.php/<subdomain>",
    ),
    "chargebee": (
        re.compile(rf"{DOMAIN_LABEL}\.chargebee\.com"),
        re.compile(r"/api/v2/?"),
        "https://<site>.chargebee.com/api/v2",
    ),
    "freshdesk": (
        re.compile(rf"{DOMAIN_LABEL}\.freshdesk\.com"),
        re.compile(r"/?"),
        "https://<domain>.freshdesk.com",
    ),
    "mailchimp": (
        re.compile(rf"{DOMAIN_LABEL}\.api\.mailchimp\.com"),
        re.compile(r"/?"),
        "https://<dc>.api.mailchimp.com",
    ),
    "quickbooks": (
        re.compile(r"quickbooks\.api\.intuit\.com"),
        re.compile(r"/v3/company/[0-9]{1,32}/?"),
        "https://quickbooks.api.intuit.com/v3/company/<realmId>",
    ),
    "recruitee": (
        re.compile(r"api\.recruitee\.com"),
        re.compile(rf"/c/{DOMAIN_LABEL}/?"),
        "https://api.recruitee.com/c/<company_id>",
    ),
    "salesforce": (
        re.compile(rf"(?:{DOMAIN_LABEL}\.)+salesforce\.com"),
        re.compile(r"/?"),
        "https://<instance>.salesforce.com",
    ),
    "zendesk": (
        re.compile(rf"{DOMAIN_LABEL}\.zendesk\.com"),
        re.compile(r"/?"),
        "https://<subdomain>.zendesk.com",
    ),
}


class SourceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(
        description="Source provider slug; an unknown provider's refusal lists the catalog."
    )
    streams: tuple[str, ...] = Field(
        min_length=1,
        description="Exact stream names to sync; a wrong stream's refusal lists the provider's. "
        "A re-apply is the whole set: a stream left out of it is removed with its pages.",
    )
    account_id: str = Field(
        default="",
        description="Connected-account ID, needed only when the agent holds several accounts. "
        "Direct (BYOK-credential) providers leave it empty.",
    )
    base_url: str = Field(
        default="",
        description="Tenant API URL, only for providers that require one; the refusal names the "
        "expected shape.",
    )
    shared: bool = Field(
        default=False,
        description="Sync into the whole workspace's shared memory rather than privately to the "
        "registering member. Set it only when the member's words say the source is for the team.",
    )
    resync: bool = Field(
        default=False,
        description="Set true to schedule an immediate sync of this binding's streams — an act, "
        "not state: it changes nothing else, always reads back false, and is the registering "
        "member's or a workspace admin's.",
    )
    backfill_days: Annotated[int, Field(ge=1, le=MAX_BACKFILL_DAYS)] | Literal["all"] | None = (
        Field(
            default=None,
            description="How many days back the first sync reaches, for the selected streams that "
            "take a window — email message streams today, 30 days unless this says otherwise. "
            f'Set a number of days the member named (at most {MAX_BACKFILL_DAYS}), or "all" for '
            "the whole history; every other stream reaches back the fixed distance its provider "
            "gives it and a binding of only those refuses this field rather than report a window "
            "nothing honours. It is pinned to a fixed date when the binding is registered, and "
            "raising it later re-pins the binding further back from that same date and refetches "
            "the wider window. Lowering it is refused — recreate the binding to reach back less "
            "far, which tombstones what it had. A submit that drops every windowed stream drops "
            "the window with their rows, so leave this unset in that one.",
        )
    )


@dataclass(frozen=True)
class _Stream:
    name: str
    next_sync_at: datetime
    consecutive_errors: int
    parked_at: datetime | None
    parked_reason: str | None
    source_id: UUID
    created_at: datetime
    updated_at: datetime
    backfill_after: datetime | None


@dataclass(frozen=True)
class _Binding:
    provider: str
    account: str
    base_url: str | None
    subject: str
    owner_member_id: UUID | None
    backfill_days: int | Literal["all"] | None
    streams: tuple[_Stream, ...]

    @property
    def name(self) -> str:
        return binding_name(self.provider, self.account, self.base_url)

    @property
    def created_at(self) -> datetime:
        return min(stream.created_at for stream in self.streams)

    @property
    def updated_at(self) -> datetime:
        return max(stream.updated_at for stream in self.streams)

    def links(self) -> tuple[ObjectLink, ...]:
        """What the binding authenticates through: the workspace credential slot a direct provider
        spends, or the connection its account handle resolves to. A shared binding is
        workspace-readable while its connection stays owner-or-admin, so only a private binding
        names the connection."""
        if self.account == DIRECT_ACCOUNT:
            access = ObjectRef(kind=CREDENTIAL_KIND, name=credential_object_name(self.provider))
        elif self.subject == SHARED_SUBJECT:
            return ()
        else:
            access = ObjectRef(
                kind=CONNECTION_OBJECT_KIND, name=account_object_name(self.provider, self.account)
            )
        return (ObjectLink(relation="access_to", target=access),)

    def spec(self) -> SourceSpec:
        return SourceSpec(
            provider=self.provider,
            streams=tuple(stream.name for stream in self.streams),
            account_id="" if self.account == DIRECT_ACCOUNT else self.account,
            base_url=self.base_url or "",
            shared=subject_shared(self.subject),
            backfill_days=self.backfill_days,
        )

    def summary(self) -> str:
        streams = ", ".join(stream.name for stream in self.streams)
        return f"{self.provider} ({self.account}): {streams}"[:SUMMARY_MAX]


@dataclass(frozen=True)
class _ResolvedAccount:
    account: str
    connection_id: UUID | None


def _require_ext(ext: ExtensionContext | None) -> ExtensionContext:
    if ext is None:
        raise RuntimeError("source objects dispatched without their ExtensionContext")
    return ext


def _require_connectors(ctx: ToolContext) -> ConnectorRegistry:
    if ctx.connectors is None:
        raise RuntimeError("source objects dispatched without the turn's connector registry")
    return ctx.connectors


async def _bindings_from_ext(ext: ExtensionContext) -> tuple[_Binding, ...]:
    grouped: dict[tuple[str, str, str | None], list[_Stream]] = {}
    disclosure: dict[tuple[str, str, str | None], tuple[str, UUID | None]] = {}
    windows: dict[tuple[str, str, str | None], int | Literal["all"] | None] = {}
    for record in await ext.sources():
        if record.backend not in CONNECTORS:
            continue
        config = ConnectorSourceConfig.model_validate(record.config)
        key = (record.backend, config.account, config.base_url)
        grouped.setdefault(key, []).append(
            _Stream(
                name=config.stream,
                next_sync_at=record.next_sync_at,
                consecutive_errors=record.consecutive_errors,
                parked_at=record.parked_at,
                parked_reason=record.parked_reason,
                source_id=record.id,
                created_at=record.created_at,
                updated_at=record.updated_at,
                backfill_after=config.backfill_after,
            )
        )
        disclosure.setdefault(key, (record.subject, record.owner_member_id))
        windows.setdefault(key, config.backfill_days)
    return tuple(
        _Binding(
            provider=provider,
            account=account,
            base_url=base_url,
            subject=disclosure[(provider, account, base_url)][0],
            owner_member_id=disclosure[(provider, account, base_url)][1],
            backfill_days=windows[(provider, account, base_url)],
            streams=tuple(sorted(streams, key=lambda stream: stream.name)),
        )
        for (provider, account, base_url), streams in grouped.items()
    )


async def _binding_named(ext: ExtensionContext | None, name: str) -> _Binding | None:
    """One registered binding by its derived name — the lookup the source kind and the trigger
    kind both start from, since a trigger names the binding it watches."""
    return next(
        (
            binding
            for binding in await _bindings_from_ext(_require_ext(ext))
            if binding.name == name
        ),
        None,
    )


def _require_triggers(ext: ExtensionContext | None) -> SourceTriggerStore:
    return SourceTriggerStore(_require_ext(ext))


def effective_days(request: int | Literal["all"] | None, declared: int | None) -> int | None:
    """How many days back a stream is actually pinned: the member's request where they named one,
    the stream's own declaration where they did not, and None where the answer is all history —
    either because they asked for it or because the stream declares no window at all. The member's
    apply and the connected-account registrar both pin rows through this one resolution."""
    if isinstance(request, int):
        return request
    if request is None:
        return declared
    return None


def _binding_identity(
    spec: SourceSpec,
) -> tuple[str, tuple[str, ...], str, str, bool, int | Literal["all"] | None]:
    return (
        spec.provider,
        tuple(sorted(spec.streams)),
        spec.account_id,
        spec.base_url,
        spec.shared,
        spec.backfill_days,
    )


SHARE_GATE = (
    "only the registering member may change a source; workspace admins may inspect or remove it"
)
DELETE_GATE = "only the registering member or a workspace admin may remove a source"
RESYNC_GATE = "only the registering member or a workspace admin may resync a source"
TRIGGER_GATE = "only the member who created a source trigger may change it"
TRIGGER_DELETE_GATE = "only the trigger's creator or a workspace admin may delete a source trigger"


@dataclass(frozen=True)
class SourceObjects(MemberReadableObjects[SourceSpec, ObjectOwner]):
    """The kind's handlers over the workspace's registered source rows: get/list reconstruct
    bindings by grouping rows on (provider, account, base_url); apply validates provider, streams,
    tenant URL, and auth exactly as registration always has, then registers one row for each named
    stream the binding does not hold (the first sync is scheduled immediately) and removes the row
    of each stream it no longer names — private to the registering member unless the
    model asks for `shared`; delete removes the binding's rows and their synced pages follow
    through the page-tombstone pipeline. The per-member visibility and registrar-or-admin gate is
    the base's, in a turn and in the portal alike; this kind supplies the bindings, their specs,
    and the register/share/remove acts."""

    kind_name: ClassVar[str] = SOURCE_KIND
    mutate_gate: ClassVar[str] = SHARE_GATE
    delete_gate: ClassVar[str] = DELETE_GATE
    delete_requires_speaker: ClassVar[bool] = True

    async def apply(
        self,
        ctx: ToolContext,
        name: str,
        spec: SourceSpec,
        old: SourceSpec | None,
        *,
        expected_generation: UUID | None,
    ) -> None:
        """A resync is the registering member's or an admin's and changes nothing else. Re-applying
        the identical spec of a source the caller can already see (`old` is non-None only for a
        visible source, since the base `get` hides the rest) registers nothing and stays outside the
        base's gate: all it does is grant the calling agent the binding it names. Every other
        apply — register, share-flip, restream, rewindow — goes through the base's member/admin
        gate."""
        if spec.resync:
            await self._resync(ctx, name, spec, old)
            return
        if old is not None and _binding_identity(spec) == _binding_identity(old):
            await self._grant_settled(ctx, name)
            return
        await super().apply(ctx, name, spec, old, expected_generation=expected_generation)

    async def _grant_settled(self, ctx: ToolContext, name: str) -> None:
        """Grant the calling agent the binding its identical submit settles on. Registration is what
        grants an agent a feed, and a submit that names what the workspace already holds registers
        nothing, so the grant is the whole of what this path owes the agent that asked for the
        source — without it the agent reads back `updated`, holds no feed, and sees no error.

        The grant is the registering member's own or a shared binding's: the authority behind a
        private feed belongs to the member whose connection serves it, so an admin re-applying it
        grants nothing, and a speakerless turn grants nothing either — a granting act takes a live
        member."""
        speaker = ctx.speaker_member_id
        owner = await self._owner(ctx, name)
        if speaker is None or owner is None or not (owner.shared or self._owned(owner, speaker)):
            return
        binding = await _binding_named(ctx.ext, name)
        if binding is None:
            return
        ext = _require_ext(ctx.ext)
        for stream in binding.streams:
            await ext.grant_source(
                stream.source_id, agent_id=ctx.turn.agent_id, actor_member_id=speaker
            )

    async def _resync(
        self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None
    ) -> None:
        """Schedule an immediate sync of the binding's streams: the act rides `apply` with
        `resync` set and the binding's current spec, so a submit that also edits what identifies
        the binding — provider, streams, account, tenant URL, or disclosure — is refused whole
        rather than half-applied. The gate is the delete
        gate's population — a resync drives connector traffic on the registering member's
        credential, so seeing a shared source is not enough to spend it."""
        if old is None or _binding_identity(spec) != _binding_identity(old):
            raise VerbNotSupported(
                "a resync changes nothing else — apply the binding's current spec with resync set"
            )
        owner = await self._owner(ctx, name)
        is_admin = await ctx.speaker_is_admin()
        if owner is None or not self._visible(owner, authority_member_id(ctx.authority), is_admin):
            raise UnknownObject(f"no {SOURCE_KIND} object named {name!r}")
        owned = self._owned(owner, authority_member_id(ctx.authority))
        if not owned and not await ctx.require_speaking_admin(RESYNC_GATE):
            raise AdminRequired(RESYNC_GATE)
        binding = await _binding_named(ctx.ext, name)
        if binding is None:
            raise UnknownObject(f"no {SOURCE_KIND} object named {name!r}")
        await _require_ext(ctx.ext).schedule_source_sync(
            tuple(stream.source_id for stream in binding.streams)
        )

    async def _member_rows(
        self, ext: ExtensionContext | None, *, member_id: UUID | None
    ) -> tuple[OwnedRow[ObjectOwner], ...]:
        return tuple(
            OwnedRow(
                name=binding.name,
                summary=binding.summary(),
                owner=ObjectOwner(
                    member_id=binding.owner_member_id,
                    shared=subject_shared(binding.subject),
                ),
            )
            for binding in await _bindings_from_ext(_require_ext(ext))
        )

    async def _member_object(
        self,
        ext: ExtensionContext | None,
        name: str,
        owner: ObjectOwner,
        *,
        member_id: UUID | None,
    ) -> ObjectDetail[SourceSpec] | None:
        binding = await _binding_named(ext, name)
        if binding is None:
            return None
        return ObjectDetail(
            spec=binding.spec(),
            created_at=binding.created_at,
            updated_at=binding.updated_at,
            links=binding.links(),
        )

    async def _status(
        self, ctx: ToolContext, name: str, _owner: ObjectOwner
    ) -> dict[str, JsonValue] | None:
        binding = await _binding_named(ctx.ext, name)
        if binding is None:
            return None
        shared = subject_shared(binding.subject)
        status: dict[str, JsonValue] = {
            "shared": shared,
            "streams": {
                stream.name: {
                    "next_sync_at": stream.next_sync_at.isoformat(),
                    "consecutive_errors": stream.consecutive_errors,
                    "parked_at": (
                        None if stream.parked_at is None else stream.parked_at.isoformat()
                    ),
                    "parked_reason": stream.parked_reason,
                    "backfill_after": (
                        None if stream.backfill_after is None else stream.backfill_after.isoformat()
                    ),
                }
                for stream in binding.streams
            },
        }
        if not shared and binding.owner_member_id is not None:
            status["owner_member_id"] = str(binding.owner_member_id)
        return status

    async def _apply_owned(
        self,
        ctx: ToolContext,
        name: str,
        spec: SourceSpec,
        old: SourceSpec | None,
        owner: ObjectOwner | None,
    ) -> None:
        ext = _require_ext(ctx.ext)
        if ctx.speaker_member_id is None:
            raise SpeakerRequired("registering a source requires a speaking member")
        connector_cls = CONNECTORS.get(spec.provider)
        if connector_cls is None:
            raise ValueError(
                f"unknown source provider {spec.provider!r}; providers: "
                f"{', '.join(sorted(CONNECTORS))}"
            )
        declared = {
            stream.name: stream.backfill_window_days for stream in connector_cls().streams()
        }
        streams = tuple(sorted(dict.fromkeys(spec.streams)))
        unsupported = [stream for stream in streams if stream not in declared]
        if unsupported:
            raise ValueError(
                f"{spec.provider!r} does not provide {', '.join(map(repr, unsupported))}; "
                f"streams: {', '.join(declared)}"
            )
        windowed = frozenset(
            stream_name for stream_name, window in declared.items() if window is not None
        )
        if spec.backfill_days is not None and not windowed.intersection(streams):
            takes = f"windowed streams: {', '.join(sorted(windowed))}" if windowed else "none"
            raise ValueError(
                f"no selected {spec.provider!r} stream takes a backfill window — each reaches back "
                f"the fixed distance its provider gives it, so leave backfill_days unset ({takes})"
            )
        base_url = _validated_base_url(spec.provider, spec.base_url or None)
        resolved_account = await self._resolved_account(ctx, spec)
        derived = binding_name(spec.provider, resolved_account.account, base_url)
        if name != derived:
            raise ValueError(
                f"source names derive from the binding — apply this spec as name {derived!r}"
            )
        binding = await _binding_named(ctx.ext, name)
        held: dict[str, _Stream] = (
            {} if binding is None else {stream.name: stream for stream in binding.streams}
        )
        if binding is not None:
            old_spec = binding.spec()
            # Every refusal is raised before any write: a submit that edits the window AND the
            # stream set is refused whole, never left with the streams applied and the window
            # rejected. So each edit is decided on its own terms, and every write below happens
            # only once nothing can still raise.
            if old_spec.shared and not spec.shared:
                raise VerbNotSupported(
                    "a shared source stays shared — delete the binding and recreate it privately"
                )
            if spec.backfill_days != old_spec.backfill_days:
                await self._widen_window(
                    ctx,
                    binding,
                    kept=tuple(held[stream] for stream in streams if stream in held),
                    declared=declared,
                    windowed=windowed,
                    account=resolved_account.account,
                    base_url=base_url,
                    request=spec.backfill_days,
                )
            if spec.shared and not old_spec.shared:
                await ext.set_source_subject(
                    tuple(stream.source_id for stream in binding.streams), SHARED_SUBJECT
                )
        subject = SHARED_SUBJECT if spec.shared else member_subject(ctx.speaker_member_id)
        registered_at = datetime.now(UTC)
        for stream in streams:
            if stream in held:
                # The row is settled — same account, same authority, same window — so registering
                # it again would grant nothing and this agent would hold no feed and see no error.
                # A workspace that already watches an account is exactly where a shipped agent
                # asks for one, so the grant is what the apply owes it.
                await ext.grant_source(
                    held[stream].source_id,
                    agent_id=ctx.turn.agent_id,
                    actor_member_id=ctx.speaker_member_id,
                )
                continue
            days = (
                effective_days(spec.backfill_days, declared[stream]) if stream in windowed else None
            )
            await ext.register_source(
                spec.provider,
                ConnectorSourceConfig(
                    account=resolved_account.account,
                    stream=stream,
                    base_url=base_url,
                    backfill_days=spec.backfill_days,
                    backfill_after=(None if days is None else registered_at - timedelta(days=days)),
                ),
                subject=subject,
                owner_member_id=ctx.speaker_member_id,
                connection_id=resolved_account.connection_id,
                agent_id=ctx.turn.agent_id,
            )
        for dropped in sorted(set(held).difference(streams)):
            await ext.remove_source(held[dropped].source_id)

    async def _widen_window(
        self,
        ctx: ToolContext,
        binding: _Binding,
        *,
        kept: tuple[_Stream, ...],
        declared: dict[str, int | None],
        windowed: frozenset[str],
        account: str,
        base_url: str | None,
        request: int | Literal["all"] | None,
    ) -> None:
        """Re-pin a live binding further back, and refuse a request that reaches less far.

        The new floor is resolved against the instant the binding was registered, reconstructed per
        row as `backfill_after + <the days it was pinned with>`. Against `now` instead, widening an
        old binding would pin a LATER floor than the one it replaced.

        Only widening is safe: the floor moves earlier, so the refetch re-walks a superset and
        every synced page stays inside the window. Narrowing would strand the pages between the two
        floors — never revisited, never tombstoned, since mail is not `delete_missing`.

        Rows whose stream takes no window carry the request but hold no cutoff and are not
        refetched.

        Only the rows the same submit keeps are in play. A stream it drops loses its row and its
        pages, so nothing is left between two floors to strand: dropping the last windowed stream
        drops the window with it, and the request that comes back unset is no narrowing."""
        ext = _require_ext(ctx.ext)
        pins: dict[UUID, datetime | None] = {}
        for stream in kept:
            if stream.name not in windowed:
                pins[stream.source_id] = None
                continue
            was = effective_days(binding.backfill_days, declared[stream.name])
            now_wants = effective_days(request, declared[stream.name])
            if now_wants is None:
                pins[stream.source_id] = None
                continue
            if stream.backfill_after is None or was is None:
                raise VerbNotSupported(
                    f"{binding.name} already reaches all history on {stream.name!r} — a binding's "
                    "backfill window only ever widens in place; delete it and recreate it to "
                    "reach back less far"
                )
            anchor = stream.backfill_after + timedelta(days=was)
            pin = anchor - timedelta(days=now_wants)
            # strictly later is the narrowing; landing on the same instant is a request that
            # resolves to the window the row already holds — dropping an explicit `30` where the
            # stream declares 30 — and only relabels what was asked for
            if pin > stream.backfill_after:
                raise VerbNotSupported(
                    f"{binding.name} is pinned to {stream.backfill_after.isoformat()} on "
                    f"{stream.name!r} and this reaches back only to {pin.isoformat()} — a "
                    "binding's backfill window only ever widens in place; delete it and recreate "
                    "it to reach back less far"
                )
            pins[stream.source_id] = pin
        await ext.rewindow_sources(
            {
                stream.source_id: ConnectorSourceConfig(
                    account=account,
                    stream=stream.name,
                    base_url=base_url,
                    backfill_days=request,
                    backfill_after=pins[stream.source_id],
                )
                for stream in kept
            },
            refetch=frozenset(
                stream.source_id
                for stream in kept
                if pins[stream.source_id] != stream.backfill_after
            ),
        )

    async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None:
        ext = _require_ext(ctx.ext)
        binding = await _binding_named(ctx.ext, name)
        if binding is None:
            raise UnknownObject(f"no {SOURCE_KIND} object named {name!r}")
        for stream in binding.streams:
            await ext.remove_source(stream.source_id)
        await _require_triggers(ctx.ext).remove_binding(name)

    async def _resolved_account(self, ctx: ToolContext, spec: SourceSpec) -> _ResolvedAccount:
        """The account a source authenticates as. An explicitly registered connector always uses its
        broker; an open provider the broker namespace serves uses the broker once an account is
        connected, else its direct BYOK credential when one is set — so a member picks the path
        by connecting an account or setting a key, never a flag. The handle returned here IS that
        choice: it lands in `ConnectorSourceConfig.account`, and every run replays it through
        the connection-bound source credential resolver, which sends `DIRECT_ACCOUNT` to the
        deploy's fallback backend and a connected account to its broker.

        A provider the broker serves with neither is asked to connect an account, never to add a
        workspace key; a provider the direct path is the only path for refuses a named `account_id`
        as the contradiction it is, key set or not."""
        ext = _require_ext(ctx.ext)
        registry = _require_connectors(ctx)
        explicit = spec.provider in registry.entries
        accounts: tuple[str, ...] = ()
        if explicit or registry.resolver is not None:
            try:
                accounts = tuple(await ctx.connector_accounts(spec.provider))
            except ConnectUnavailable:
                accounts = ()
        if explicit or accounts:
            if not accounts:
                raise ValueError(
                    f"connect a {spec.provider!r} account before registering its sources "
                    f"(connect_account with provider={spec.provider!r})"
                )
            if not spec.account_id and len(accounts) > 1:
                raise ValueError(
                    f"choose which {spec.provider!r} account to sync; set account_id to one of: "
                    f"{', '.join(accounts)}"
                )
            account = spec.account_id or accounts[0]
            if account not in accounts:
                raise ValueError(
                    f"this agent has no active {spec.provider!r} grant for {account!r}; "
                    f"accounts: {', '.join(accounts)}"
                )
            connection = await ctx.connector_connection(spec.provider, account)
            if connection.owner_member_id != ctx.speaker_member_id:
                raise ValueError(
                    "only the member who owns a connection may register a persistent source from it"
                )
            return _ResolvedAccount(
                account=connection.account_id,
                connection_id=connection.id,
            )
        direct_capable = spec.provider in ext.credentials.declared and registry.fallback is not None
        keyed = False
        if direct_capable:
            try:
                await ext.credentials.get(spec.provider)
            except CredentialSlotUnset:
                pass
            else:
                keyed = True
        if keyed and not spec.account_id:
            return _ResolvedAccount(account=DIRECT_ACCOUNT, connection_id=None)
        brokerable = registry.resolver is not None and await registry.resolver.claims(spec.provider)
        if brokerable:
            raise ValueError(
                f"connect a {spec.provider!r} account before registering its sources "
                f"(connect_account with provider={spec.provider!r})"
            )
        if not direct_capable:
            raise ValueError(f"no direct authentication backend can sync {spec.provider!r}")
        if spec.account_id:
            raise ValueError(
                f"{spec.provider!r} uses its workspace credential, not a connected account"
            )
        raise ValueError(
            f"add the {spec.provider!r} credential before registering its sources "
            f"(the credential collection's request_credentials action for slot {spec.provider!r})"
        )


def trigger_name(binding: str, conversation_id: UUID, resource: str = "") -> str:
    """A trigger IS the pair it names, so its object name derives from that pair exactly as a
    binding's derives from its config — one rule, so an apply under any other name is refused with
    the one to use rather than filed as a second row over the same pair. A trigger narrowed to one
    resource is a third element of that identity, digested the way a binding digests its config."""
    if not resource:
        return f"{binding}-{conversation_id.hex}"
    return f"{binding}-{conversation_id.hex}-{resource_digest(resource)}"


class SourceTriggerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(
        title="Source",
        description="The shared source object this trigger watches.",
    )
    resource: str = Field(
        default="",
        title="Resource",
        description="The URL of one resource of that source to narrow the trigger to — a GitHub "
        "pull request or issue. Only the changes about it, on the streams the source syncs, wake "
        "the conversation. Leave it empty to watch the whole source.",
    )
    delivery: SourceTriggerDelivery = Field(
        default="current",
        title="Delivery",
        description="Use current to wake this conversation for each source batch. Use per_page to "
        "open one stable agent conversation for each changed page.",
    )


@dataclass(frozen=True)
class SourceTriggerObjects(MemberReadableObjects[SourceTriggerSpec, GeneratedObjectOwner]):
    """The kind's handlers over the core trigger store: a trigger is seen by whoever reads the
    conversation that owns it, plus its creator and a workspace admin — the gate is the base's,
    and this kind supplies only the `shared` fact it decides from, taken from that conversation's
    audience. Deleting stays the creator's and an admin's, so a member reading a shared trigger is
    never a member who can silence it. Only a shared source can carry one: a private source's
    pages reach no other reader, so the alert filter would drop every change it ever made and the
    trigger would stand as a promise nothing keeps."""

    kind_name: ClassVar[str] = SOURCE_TRIGGER_KIND
    mutate_gate: ClassVar[str] = TRIGGER_GATE
    delete_gate: ClassVar[str] = TRIGGER_DELETE_GATE

    async def _member_rows(
        self, ext: ExtensionContext | None, *, member_id: UUID | None
    ) -> tuple[OwnedRow[GeneratedObjectOwner], ...]:
        """A trigger is shared exactly as far as its owning conversation, so every surface
        listing the kind answers one question one way. Its summary is the source's own, since what
        a member came to read is which source wakes them and not the pair's derived name."""
        listed = await _require_triggers(ext).list_reported()
        bindings = {
            binding.name: binding for binding in await _bindings_from_ext(_require_ext(ext))
        }
        emails = await owner_emails(row.trigger.created_by_member_id for row in listed)
        return tuple(
            OwnedRow(
                name=trigger_name(
                    row.trigger.binding, row.trigger.conversation_id, row.trigger.resource
                ),
                summary=_trigger_summary(bindings.get(row.trigger.binding), row.trigger),
                owner=GeneratedObjectOwner(
                    member_id=row.trigger.created_by_member_id,
                    shared=subject_shared(row.audience),
                    generation=row.trigger.id,
                ),
                fields={
                    "conversation": str(row.trigger.conversation_id),
                    "source": row.trigger.binding,
                    "resource": row.trigger.resource,
                    "delivery": row.trigger.delivery,
                    "origin": row.surface_label or "Portal",
                    "owner_email": emails.get(row.trigger.created_by_member_id),
                    "mine": row.trigger.created_by_member_id == member_id,
                },
            )
            for row in listed
        )

    async def _member_object(
        self,
        ext: ExtensionContext | None,
        name: str,
        owner: GeneratedObjectOwner,
        *,
        member_id: UUID | None,
    ) -> ObjectDetail[SourceTriggerSpec] | None:
        listed = await self._find(ext, name)
        if listed is None or listed.trigger.id != owner.generation:
            return None
        links = [
            ObjectLink(
                relation="watches",
                target=ObjectRef(kind=SOURCE_KIND, name=listed.trigger.binding),
            )
        ]
        if listed.trigger.delivery == "current":
            links.append(
                ObjectLink(
                    relation="reports_to",
                    target=ObjectRef(
                        kind=CONVERSATION_KIND,
                        name=str(listed.trigger.conversation_id),
                    ),
                )
            )
        return ObjectDetail(
            spec=SourceTriggerSpec(
                source=listed.trigger.binding,
                resource=listed.trigger.resource,
                delivery=listed.trigger.delivery,
            ),
            created_at=listed.trigger.created_at,
            updated_at=listed.trigger.updated_at,
            links=tuple(links),
        )

    async def _status(
        self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner
    ) -> dict[str, JsonValue] | None:
        listed = await self._find(ctx.ext, name)
        if listed is None or listed.trigger.id != owner.generation:
            return None
        emails = await owner_emails((listed.trigger.created_by_member_id,))
        return {
            "conversation": str(listed.trigger.conversation_id),
            "source": listed.trigger.binding,
            "resource": listed.trigger.resource,
            "delivery": listed.trigger.delivery,
            "origin": listed.surface_label or "Portal",
            "owner_email": emails.get(listed.trigger.created_by_member_id),
            "mine": listed.trigger.created_by_member_id == authority_member_id(ctx.authority),
        }

    async def _apply_owned(
        self,
        ctx: ToolContext,
        name: str,
        spec: SourceTriggerSpec,
        old: SourceTriggerSpec | None,
        owner: GeneratedObjectOwner | None,
    ) -> None:
        """A trigger has nothing to change — re-applying the one that exists is the no-op a
        re-registered source is — so the only act here is creating one. A resource is read against
        the source's provider into the one link the row stores, so every spelling of a pull request
        is the one trigger. The name is checked before any write, because it names the conversation:
        applying the name of a trigger some other conversation already holds would otherwise report
        success for a conversation no row was written for. The binding is re-read after the write,
        because a source's removal drops its triggers in another transaction and one landing behind
        that sweep would watch a source nobody can reach."""
        source = await self._watchable(ctx, spec.source)
        if spec.resource:
            resource = canonical_resource(source.spec.provider, spec.resource)
            if resource is None:
                raise ValueError(
                    f"{spec.resource!r} is not the link of a resource of {spec.source!r} that a "
                    f"{SOURCE_TRIGGER_KIND} narrows to"
                )
            spec = spec.model_copy(update={"resource": resource})
        expected = trigger_name(spec.source, ctx.turn.conversation_id, spec.resource)
        if name != expected:
            raise ValueError(
                f"a {SOURCE_TRIGGER_KIND} is named for the pair it is — apply it as {expected!r}"
            )
        if owner is not None:
            if old == spec:
                return
            raise ValueError(
                f"a {SOURCE_TRIGGER_KIND} is the source and conversation it names — delete this "
                "one and apply another"
            )
        triggers = _require_triggers(ctx.ext)
        await triggers.create(
            conversation_id=ctx.turn.conversation_id,
            binding=spec.source,
            delivery=spec.delivery,
            created_by_member_id=authority_member_id(ctx.authority),
            resource=spec.resource,
        )
        if await _binding_named(ctx.ext, spec.source) is None:
            await triggers.remove_binding(spec.source)
            raise UnknownObject(f"no {SOURCE_KIND} object named {spec.source!r}")

    async def _watchable(self, ctx: ToolContext, source: str) -> ObjectDetail[SourceSpec]:
        """The source this caller may watch, asked of the source kind itself — its `get` is the one
        place the per-member gate on a binding lives, so a source the caller cannot see comes back
        as nothing and a stranger guessing a binding never learns one exists. A private source the
        caller does own is refused for what it is: its pages reach no other reader, so the alert
        filter would drop every change it ever made."""
        source_object = await SOURCE_OBJECT.store.get(ctx, source)
        if source_object is None:
            raise UnknownObject(f"no {SOURCE_KIND} object named {source!r}")
        if not source_object.spec.shared:
            raise ValueError(
                f"{source!r} syncs privately to the member who registered it, so its changes "
                "reach no conversation; re-register it as shared to watch it"
            )
        return source_object

    async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None:
        listed = await self._find(ctx.ext, name)
        if listed is None or listed.trigger.id != owner.generation:
            raise ValueError(f"{SOURCE_TRIGGER_KIND} {name!r} changed while deleting")
        await _require_triggers(ctx.ext).remove(listed.trigger)

    async def _find(self, ext: ExtensionContext | None, name: str) -> ListedTrigger | None:
        return next(
            (
                row
                for row in await _require_triggers(ext).list_reported()
                if trigger_name(
                    row.trigger.binding, row.trigger.conversation_id, row.trigger.resource
                )
                == name
            ),
            None,
        )


async def on_page_change(ctx: HookContext) -> HookOutcome:
    """Wake each changed source's triggers. Current delivery sends one batch to the trigger's
    conversation. Per-page delivery sends each change to the stable agent conversation keyed by
    that trigger and page. Only shared pages that the trigger's agent may read cause a wake.

    A trigger narrowed to one resource is woken by the changes about that resource alone."""
    match ctx.payload:
        case PageChangeBatch(changes=changes):
            pass
        case _:
            raise RuntimeError("sources hook fired on a non-page_change payload")
    binding_of_source = {
        stream.source_id: binding
        for binding in await _bindings_from_ext(ctx.ext)
        for stream in binding.streams
    }
    by_binding: dict[str, tuple[_Binding, list[PageChange]]] = {}
    for change in changes:
        binding = binding_of_source.get(change.source_id)
        if binding is None:
            continue
        by_binding.setdefault(binding.name, (binding, []))[1].append(change)
    triggers = _require_triggers(ctx.ext)
    for binding, binding_changes in by_binding.values():
        woken = await triggers.waking(binding.name)
        if not woken:
            continue
        facts = await ctx.ext.conversation_facts(
            tuple(trigger.conversation_id for trigger in woken)
        )
        shared = [change for change in binding_changes if change.subject == SHARED_SUBJECT]
        if not shared:
            continue
        for trigger in woken:
            readable = await ctx.ext.readable_source_ids(
                SourceReader(
                    agent_id=trigger.agent_id,
                    requesting_member_id=None,
                    subjects=frozenset({SHARED_SUBJECT}),
                )
            )
            authorized = [change for change in shared if change.source_id in readable]
            authorized = _about_resource(binding.provider, trigger, authorized)
            if not authorized:
                continue
            with suppress(AgentArchived):
                await _fire_trigger(
                    ctx.ext,
                    binding,
                    trigger,
                    facts[trigger.conversation_id].audience,
                    authorized,
                )
    return None


def _about_resource(
    provider: str, trigger: SourceTrigger, changes: list[PageChange]
) -> list[PageChange]:
    """The changes one trigger is woken by: all of them for a whole-binding trigger, and for a
    narrowed one the changes whose page the provider linked to its resource."""
    if not trigger.resource:
        return changes
    return [
        change for change in changes if resource_matches(provider, trigger.resource, change.body)
    ]


def _trigger_summary(binding: _Binding | None, trigger: SourceTrigger) -> str:
    """What a member reads the trigger as: the resource it watches where it watches one, since a
    thread's watch is about that pull request and not about the feed carrying it."""
    source = binding.summary() if binding is not None else trigger.binding
    if not trigger.resource:
        return source
    return f"{trigger.resource} on {source}"[:SUMMARY_MAX]


async def on_link_seen(ctx: HookContext) -> HookOutcome:
    """Offer this conversation a trigger on each resource the text it just read names — a pull
    request in a member's message, in a spawned coding child's result, in a tool's output — when a
    shared source this agent may read syncs it and the conversation does not watch it yet.

    The offer is text and nothing else: no row is written, so a link in a machine conversation makes
    no standing waker, and the agent decides in the open whether the thread should hear about the
    resource. A conversation a member does not read — a spawned child's, a room this extension
    opened for an alert — is offered nothing, since a trigger applied there would wake nobody. Each
    offer names the exact trigger to apply, so taking it is one call."""
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
    readable = await ext.readable_source_ids(
        SourceReader(
            agent_id=ctx.turn.agent_id,
            requesting_member_id=None,
            subjects=frozenset({SHARED_SUBJECT}),
        )
    )
    bindings = [
        binding
        for binding in await _bindings_from_ext(ext)
        if binding.subject == SHARED_SUBJECT
        and any(stream.source_id in readable for stream in binding.streams)
    ]
    if not bindings:
        return None
    watched = await _require_triggers(ctx.ext).watched(conversation_id)
    lines: list[str] = []
    for link in links:
        for binding in bindings:
            resource = canonical_resource(binding.provider, link)
            if resource is None or (binding.name, resource) in watched:
                continue
            watched |= {(binding.name, resource)}
            manifest = yaml.safe_dump(
                {
                    "kind": SOURCE_TRIGGER_KIND,
                    "name": trigger_name(binding.name, conversation_id, resource),
                    "spec": {"source": binding.name, "resource": resource},
                },
                sort_keys=False,
            ).strip()
            lines.append(
                f"{resource} is a resource of the source {binding.name!r} ({binding.summary()}) "
                "this workspace syncs. To hear its changes in this conversation, call object_apply "
                f"with this manifest:\n{manifest}"
            )
    if not lines:
        return None
    return InjectContext(
        text="\n".join((WATCH_OFFER_OPEN, *lines[:WATCH_OFFER_MAX], WATCH_OFFER_CLOSE))
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
    binding: _Binding,
    trigger: SourceTrigger,
    audience: Audience,
    authorized: list[PageChange],
) -> None:
    """Deliver one trigger's changes. An archived app raises `AgentArchived` out of the first
    invoke, which drops the rest of this trigger's changes with it — none of them can be answered
    until the app is restored."""
    match trigger.delivery:
        case "current":
            member_id = (
                trigger.created_by_member_id
                if trigger.created_by_member_id is not None
                and audience == conversation_audience(trigger.created_by_member_id)
                else None
            )
            latest = max(change.changed_at for change in authorized).isoformat()
            path = await _write_change_log(
                ext,
                trigger.conversation_id,
                _trigger_scope(binding, trigger),
                latest,
                authorized,
            )
            await ext.invoke(
                trigger.conversation_id,
                trigger.agent_id,
                _alert_message(binding, trigger, authorized, path),
                idempotency_key=(
                    f"source-trigger:{_trigger_scope(binding, trigger)}:"
                    f"{trigger.conversation_id.hex}:{latest}"
                ),
                authority=authority_from_member_id(member_id),
                holds_work_already_done=True,
                standalone=True,
            )
        case "per_page":
            member_key = (
                "shared"
                if trigger.created_by_member_id is None
                else trigger.created_by_member_id.hex
            )
            for change in authorized:
                conversation_id = await ext.open_conversation(
                    trigger.agent_id,
                    f"source-trigger:{trigger.id.hex}:{member_key}:page:{change.page_id.hex}",
                    member_id=trigger.created_by_member_id,
                )
                revision = f"{change.changed_at.isoformat()}-{change.revision}"
                path = await _write_change_log(
                    ext, conversation_id, _trigger_scope(binding, trigger), revision, [change]
                )
                await ext.invoke(
                    conversation_id,
                    trigger.agent_id,
                    _alert_message(binding, trigger, [change], path),
                    idempotency_key=(
                        f"source-trigger:{trigger.id.hex}:{member_key}:"
                        f"{change.page_id.hex}:{change.revision}"
                    ),
                    authority=authority_from_member_id(trigger.created_by_member_id),
                    holds_work_already_done=True,
                    standalone=True,
                )


def _trigger_scope(binding: _Binding, trigger: SourceTrigger) -> str:
    """What one trigger watches, as one path segment: the binding, or the binding and the digest of
    its resource. It names the trigger's change-log directory and its alert's idempotency key — a
    conversation holds a whole-binding trigger and a narrowed one on the same binding, one landing
    stamps every page it carries with the same `changed_at`, and under the binding alone the second
    log would overwrite the first and the second alert would be dropped as the first's repeat."""
    if not trigger.resource:
        return binding.name
    return f"{binding.name}/{resource_digest(trigger.resource)}"


async def _write_change_log(
    ext: ExtensionContext,
    conversation_id: UUID,
    directory: str,
    latest: str,
    changes: list[PageChange],
) -> str | None:
    """The whole delta as one JSON line per changed page, written into the woken
    conversation's workspace so the alerted agent reads it with its file tools instead of carrying
    it in context. Named for the same `latest` stamp the alert's idempotency key carries, so a
    replayed batch overwrites its own line-for-line identical file rather than appending a
    duplicate. A failed write propagates: the page feed inlines every body through this same blob
    store, so storage being unreachable fails the batch before the hook runs, and a handler that
    raises leaves the cursor unadvanced for the next tick to retry."""
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
        conversation_id, CHANGE_LOG_DIR, f"{directory}/{latest}.jsonl", body.encode()
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


def _alert_message(
    binding: _Binding, trigger: SourceTrigger, changes: list[PageChange], log_path: str | None
) -> str:
    if len(changes) <= ALERT_NAMED_MAX:
        detail = (
            "Changed pages (pass each ref unchanged to object_get): "
            f"{'; '.join(_page_reference(change) for change in changes)}."
        )
    elif log_path is not None:
        detail = (
            f"Every changed page is one JSON line in {log_path} — narrow it with bash (jq, grep) "
            "or read it with offset/limit, then pass the refs that matter to object_get."
        )
    else:
        detail = (
            "List them with object_list page, filtered on this source and stream and ordered by "
            "updated_at desc."
        )
    watched = (
        f"{trigger.resource} on the source {binding.name!r} ({binding.summary()})"
        if trigger.resource
        else f"The source {binding.name!r} ({binding.summary()})"
    )
    return (
        f"{watched} you watch changed — {_stream_counts(changes)}. {detail} Then tell the member "
        "what is new and why it matters."
    )


def _page_reference(change: PageChange) -> str:
    """The changed page as the exact `object_get` ref; the synced title makes it legible."""
    label = change.title[:ALERT_LABEL_CHARS] if change.title else "an untitled page"
    return f"{PAGE_KIND}/{change.page_id} ({label})"


def _validated_base_url(provider: str, base_url: str | None) -> str | None:
    """The tenant URL this binding stores, from what the submit typed — None where the provider
    needs none.

    Which of the two connector address shapes the provider declares decides what a row may carry.
    A complete fixed host refuses any override. A per-tenant host (the class constant empty) needs
    one and validates it against the provider's rule."""
    connector_cls = CONNECTORS[provider]
    value = base_url.strip() if base_url else None
    if connector_cls.base_url:
        if value is not None:
            raise ValueError(f"{provider!r} has a fixed API host; base_url cannot override it")
        return None
    rule = TENANT_URL_RULES.get(provider)
    if rule is None:
        raise ValueError(f"{provider!r} has no safe tenant URL rule")
    host_pattern, path_pattern, example = rule
    if value is None:
        raise ValueError(f"{provider!r} requires base_url, for example {example}")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError(f"{provider!r} base_url has an invalid port") from error
    hostname = parsed.hostname or ""
    safe_origin = (
        parsed.scheme == "https"
        and parsed.username is None
        and parsed.password is None
        and port is None
        and not parsed.query
        and not parsed.fragment
    )
    if (
        not safe_origin
        or host_pattern.fullmatch(hostname) is None
        or path_pattern.fullmatch(parsed.path) is None
    ):
        raise ValueError(f"{provider!r} base_url must match {example}")
    path = parsed.path.removesuffix("/")
    return f"https://{hostname}{path}"


SOURCE_OBJECT = ObjectKind(
    name=SOURCE_KIND,
    description=(
        "A content-sync binding: one provider account's selected streams, synced into memory "
        "for the member who registered it."
    ),
    guidance=(
        "Apply a manifest to register selected streams of a content-source provider; an unknown "
        "provider or stream is refused with the valid choices, and an unknown name is refused "
        "with the exact derived name to re-apply (names derive from provider, account, and "
        "tenant URL). Brokered providers use this agent's active connected-account grant; "
        "providers without a broker use their workspace credential. A source syncs privately to "
        "its registering member by default; set `shared: true` at apply — or in a later "
        "re-apply by the registrar — to sync it into workspace-shared "
        "memory instead, only when the member's words say the source is for the team. "
        "Unsharing is delete-and-recreate. The streams a submit names are the streams the binding "
        "then syncs: one it adds joins the binding, and one it drops has its row removed and its "
        "pages tombstoned, so narrow a feed by re-applying it with fewer streams rather than by "
        "deleting it. Delete is registrar-or-admin. Reads show "
        "shared sources plus the member's own — a workspace admin sees all. Its `access_to` link "
        "names the workspace credential slot a direct provider spends, or — while the source is "
        "private — the connection a brokered one resolves to. To be woken when a shared source "
        f"changes, apply a {SOURCE_TRIGGER_KIND} naming it."
    ),
    spec_model=SourceSpec,
    store=SourceObjects(),
)

SOURCE_TRIGGER_OBJECT = ObjectKind(
    name=SOURCE_TRIGGER_KIND,
    description=(
        "A standing wake-up for one shared source, or for one resource of it: each batch of "
        "changed pages wakes a conversation. Only its creator or an admin may delete it."
    ),
    guidance=(
        "Apply a manifest naming a shared source. Set `delivery: current` to wake this "
        "conversation for each source batch. Set `delivery: per_page` to open one stable agent "
        "conversation for "
        "each changed page. Set `resource` to the URL of one pull request or issue of a GitHub "
        "source to be woken only by the changes about it, on the streams that source syncs. "
        f"A {SOURCE_TRIGGER_KIND} IS the source, resource, and owning "
        "conversation it names, so its name derives from all three. Delete it to stop. A private "
        "or unknown source cannot be watched. Removing the source removes every trigger on it. "
        "Listing returns `source`, `resource`, `delivery`, the owning `conversation`, its creator "
        "(`owner_email`), and `origin`."
    ),
    spec_model=SourceTriggerSpec,
    store=SourceTriggerObjects(),
    list_fields=frozenset(
        {"conversation", "source", "resource", "delivery", "origin", "owner_email", "mine"}
    ),
    agent_target_verbs=frozenset({"list", "get", "delete"}),
)
