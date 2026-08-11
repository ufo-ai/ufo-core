"""The `source` object kind: registered content-sync bindings managed through the object verbs.

A source object is one provider binding — an account (or the workspace's BYOK credential) plus a
tenant URL where the provider needs one — carrying the selected streams, each stream a `source`
row the core sync driver polls. Identity IS the binding, so names derive from it
(`<provider>-<8-hex digest>`): apply with the wrong name refuses and hands back the exact one,
changing streams is delete-and-recreate, and re-applying the identical spec is a no-op. A stream
that declares its own reach — an email message stream, 30 days — bounds its first sync to that
window or to whatever `backfill_days` asks for, resolved into an absolute date the row keeps.
Raising it later re-pins further back from that same date and refetches; lowering it is
delete-and-recreate. A stream declaring none takes no cutoff and reports none.

A source is private to its registering member by default; the model decides `shared` at
registration, and only the registrar may later flip a private source to shared — the reverse is
delete-and-recreate. Delete is registrar-or-admin. Validation refuses with the
valid provider and stream sets, so discovery is error-driven plus `object_explain`.

The `subscribers` field is the one part any member who can see the source may change: a
conversation adds its own id (surfaced as `status.subscriber_id`) to be alerted when the source's
synced content changes, and removes it to stop. That edit is gated on visibility, not ownership,
and may only toggle the caller's own id — a conversation cannot subscribe or unsubscribe another,
nor reach a source private to someone else. The `page_change` hook reads a changed binding's
subscribers and invokes one alert turn per subscribed conversation, carrying per-stream counts of
what changed (only the shared pages a subscriber may read) and writing the whole delta — one JSON
line per page — into that conversation's workspace for the agent to read with its file tools."""

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, ClassVar, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ufo.sdk.authproxy import DIRECT_ACCOUNT
from ufo.sdk.connectors import ConnectorRegistry
from ufo.sdk.context import CredentialSlotUnset, ExtensionContext, SourceReader
from ufo.sdk.credentials import credential_object_name
from ufo.sdk.grants import account_object_name
from ufo.sdk.manifest import HookContext, HookOutcome, PageChangeBatch
from ufo.sdk.objects import (
    CREDENTIAL_KIND,
    AdminRequired,
    MemberReadableObjects,
    ObjectDetail,
    ObjectKind,
    ObjectLink,
    ObjectOwner,
    ObjectRef,
    OwnedRow,
    UnknownObject,
    VerbNotSupported,
)
from ufo.sdk.sources import (
    ConnectorSourceConfig,
    PageChange,
    binding_name,
)
from ufo.sdk.subjects import SHARED_SUBJECT, member_subject, subject_shared
from ufo.sdk.tools import ConnectUnavailable, ToolContext
from ufo_ext_sources.pages import PAGE_KIND
from ufo_ext_sources.registry import CONNECTORS, SOURCE_KIND

CONNECTION_OBJECT_KIND = "connection"
SUMMARY_MAX = 120
MAX_BACKFILL_DAYS = 36500
SUBSCRIBERS_PREFIX = "subscribers:"
ALERT_NAMED_MAX = 5
ALERT_LABEL_CHARS = 60
CHANGE_LOG_DIR = ".sources"
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
        description="Exact stream names to sync; a wrong stream's refusal lists the provider's.",
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
    subscribers: tuple[str, ...] = Field(
        default=(),
        description="Conversation ids alerted when this source's synced content changes. Add or "
        "remove only your own id (shown as status.subscriber_id) to subscribe or unsubscribe; "
        "this is the one field an apply may change on an existing source you can see.",
    )
    resync: bool = Field(
        default=False,
        description="Set true to schedule an immediate sync of this binding's streams — an act, "
        "not state: it changes nothing else, ignores subscribers, always reads back false, and "
        "is the registering member's or a workspace admin's.",
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
            "far, which tombstones what it had.",
        )
    )


@dataclass(frozen=True)
class _Stream:
    name: str
    next_sync_at: datetime
    consecutive_errors: int
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

    def spec(self, subscribers: tuple[str, ...] = ()) -> SourceSpec:
        return SourceSpec(
            provider=self.provider,
            streams=tuple(stream.name for stream in self.streams),
            account_id="" if self.account == DIRECT_ACCOUNT else self.account,
            base_url=self.base_url or "",
            shared=subject_shared(self.subject),
            subscribers=subscribers,
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


async def _subscribers_map(ext: ExtensionContext, name: str) -> dict[str, str]:
    """This source's subscribers as a `{conversation_id: agent_id}` map — the agent is captured
    from the subscribing turn so a change alert re-enters the same conversation and agent."""
    value = await ext.store.get(SUBSCRIBERS_PREFIX + name)
    match value:
        case dict() as stored if all(
            isinstance(k, str) and isinstance(v, str) for k, v in stored.items()
        ):
            return dict(stored)
        case None:
            return {}
        case _:
            raise RuntimeError(f"malformed subscribers for source {name!r}")


async def _store_subscribers(ext: ExtensionContext, name: str, mapping: dict[str, str]) -> None:
    if mapping:
        await ext.store.put(
            SUBSCRIBERS_PREFIX + name, {conv: agent for conv, agent in mapping.items()}
        )
    else:
        await ext.store.delete(SUBSCRIBERS_PREFIX + name)


def _effective_days(request: int | Literal["all"] | None, declared: int | None) -> int | None:
    """How many days back a stream is actually pinned: the member's request where they named one,
    the stream's own declaration where they did not, and None where the answer is all history —
    either because they asked for it or because the stream declares no window at all."""
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


def _self_only_change(old: tuple[str, ...], new: tuple[str, ...], caller: str) -> None:
    """A subscribers edit may only add or remove the caller's own conversation id; any other
    difference is refused so a conversation cannot subscribe or unsubscribe another."""
    if (set(old) ^ set(new)) - {caller}:
        raise ValueError(
            "you may only add or remove your own conversation (status.subscriber_id) in "
            "subscribers; leave every other id unchanged"
        )


SHARE_GATE = (
    "only the registering member may change a source; workspace admins may inspect or remove it"
)
DELETE_GATE = "only the registering member or a workspace admin may remove a source"
RESYNC_GATE = "only the registering member or a workspace admin may resync a source"


@dataclass(frozen=True)
class SourceObjects(MemberReadableObjects[SourceSpec, ObjectOwner]):
    """The kind's handlers over the workspace's registered source rows: get/list reconstruct
    bindings by grouping rows on (provider, account, base_url); apply validates provider, streams,
    tenant URL, and auth exactly as registration always has, then registers one row per stream
    (the first sync is scheduled immediately) — private to the registering member unless the
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
        """A subscribers-only edit on a source the caller can already see (`old` is non-None only
        for a visible source, since the base `get` hides the rest) is gated on visibility, not
        ownership: any member who sees the source may add or remove their own conversation. A
        resync is the registering member's or an admin's, changes nothing else, and ignores
        subscribers. Every other apply — register, share-flip, recreate — goes through the base's
        member/admin gate."""
        if spec.resync:
            await self._resync(ctx, name, spec, old)
            return
        if old is not None and _binding_identity(spec) == _binding_identity(old):
            caller = ctx.turn.conversation_id.hex
            owner = await self._owner(ctx, name)
            if owner is not None and not owner.shared and owner.member_id != ctx.acting_member_id:
                if spec.subscribers != old.subscribers:
                    raise AdminRequired(SHARE_GATE)
                return
            _self_only_change(old.subscribers, spec.subscribers, caller)
            await self._edit_subscribers(ctx, name, spec.subscribers, caller, ctx.turn.agent_id)
            return
        await super().apply(ctx, name, spec, old, expected_generation=expected_generation)

    async def _resync(
        self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None
    ) -> None:
        """Schedule an immediate sync of the binding's streams: the act rides `apply` with
        `resync` set and the binding's current spec, so a submit that also edits what identifies
        the binding — provider, streams, account, tenant URL, or disclosure — is refused whole
        rather than half-applied, and subscribers ride their own act. The gate is the delete
        gate's population — a resync drives connector traffic on the registering member's
        credential, so seeing a shared source is not enough to spend it."""
        if old is None or _binding_identity(spec) != _binding_identity(old):
            raise VerbNotSupported(
                "a resync changes nothing else — apply the binding's current spec with resync set"
            )
        owner = await self._owner(ctx, name)
        is_admin = await ctx.speaker_is_admin()
        if owner is None or not self._visible(owner, ctx.acting_member_id, is_admin):
            raise UnknownObject(f"no {SOURCE_KIND} object named {name!r}")
        if not self._owned(owner, ctx.acting_member_id) and not is_admin:
            raise AdminRequired(RESYNC_GATE)
        binding = await self._find(ctx.ext, name)
        if binding is None:
            raise UnknownObject(f"no {SOURCE_KIND} object named {name!r}")
        await _require_ext(ctx.ext).schedule_source_sync(
            tuple(stream.source_id for stream in binding.streams)
        )

    async def _edit_subscribers(
        self, ctx: ToolContext, name: str, desired: tuple[str, ...], caller: str, agent: UUID
    ) -> None:
        """Toggle only the caller's membership (the self-only rule already held the diff to it):
        add captures the caller's agent so the alert re-enters the same conversation and agent;
        remove drops it. Other subscribers' entries are preserved untouched. The map belongs to the
        binding, so it stands only while the binding does: the removal that took the rows took the
        subscribers with them, and re-reading after the write clears a map that landed behind it.
        The recheck sees presence, not identity — a revival reuses the source row — so a write
        landing inside a concurrent delete-then-revive of that name survives onto the revived
        binding.
        Bounded to `SHARED_SUBJECT` pages by the alert filter, so it is stale state rather than
        disclosure; closing it needs the map to live in the source rows."""
        ext = _require_ext(ctx.ext)
        mapping = await _subscribers_map(ext, name)
        if caller in desired:
            mapping[caller] = agent.hex
        else:
            mapping.pop(caller, None)
        await _store_subscribers(ext, name, mapping)
        if await self._find(ctx.ext, name) is None:
            await _store_subscribers(ext, name, {})
            raise UnknownObject(f"no {SOURCE_KIND} object named {name!r}")

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
        binding = await self._find(ext, name)
        if binding is None:
            return None
        subscribers = tuple(sorted((await _subscribers_map(_require_ext(ext), name)).keys()))
        return ObjectDetail(
            spec=binding.spec(subscribers=subscribers),
            created_at=binding.created_at,
            updated_at=binding.updated_at,
            links=binding.links(),
        )

    async def _status(
        self, ctx: ToolContext, name: str, _owner: ObjectOwner
    ) -> dict[str, JsonValue] | None:
        binding = await self._find(ctx.ext, name)
        if binding is None:
            return None
        shared = subject_shared(binding.subject)
        caller = ctx.turn.conversation_id.hex
        subscribers = await _subscribers_map(_require_ext(ctx.ext), name)
        status: dict[str, JsonValue] = {
            "shared": shared,
            "subscriber_id": caller,
            "subscribed": caller in subscribers,
            "streams": {
                stream.name: {
                    "next_sync_at": stream.next_sync_at.isoformat(),
                    "consecutive_errors": stream.consecutive_errors,
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
            raise ValueError("registering a source requires a speaking member")
        if old is None and spec.subscribers:
            raise ValueError(
                "register the source first, then object_apply it again with your subscriber id "
                "added to subscribers"
            )
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
        resolved = SourceSpec(
            provider=spec.provider,
            streams=streams,
            account_id=(
                "" if resolved_account.account == DIRECT_ACCOUNT else resolved_account.account
            ),
            base_url=base_url or "",
            shared=spec.shared,
            backfill_days=spec.backfill_days,
        )
        binding = await self._find(ctx.ext, name)
        if binding is not None:
            old_spec = binding.spec()
            # Every refusal is raised before any write: a submit that edits the window AND what
            # identifies the binding is refused whole, never left with the window applied and the
            # rest rejected. So the identity comparison holds the window equal — it is decided on
            # its own terms below — and both writes happen only once nothing can still raise.
            aligned = old_spec.model_copy(update={"backfill_days": resolved.backfill_days})
            share_flip = resolved != aligned
            if share_flip:
                if resolved.model_copy(update={"shared": aligned.shared}) != aligned:
                    raise VerbNotSupported(
                        "a source's identity is its config — delete the binding and recreate it"
                    )
                if not resolved.shared:
                    raise VerbNotSupported(
                        "a shared source stays shared — delete the binding and "
                        "recreate it privately"
                    )
            if resolved.backfill_days != old_spec.backfill_days:
                await self._widen_window(
                    ctx,
                    binding,
                    declared=declared,
                    windowed=windowed,
                    account=resolved_account.account,
                    base_url=base_url,
                    request=resolved.backfill_days,
                )
            if share_flip:
                await ext.set_source_subject(
                    tuple(stream.source_id for stream in binding.streams), SHARED_SUBJECT
                )
            return
        subject = SHARED_SUBJECT if resolved.shared else member_subject(ctx.speaker_member_id)
        registered_at = datetime.now(UTC)
        for stream in streams:
            days = (
                _effective_days(spec.backfill_days, declared[stream])
                if stream in windowed
                else None
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

    async def _widen_window(
        self,
        ctx: ToolContext,
        binding: _Binding,
        *,
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
        refetched."""
        ext = _require_ext(ctx.ext)
        pins: dict[UUID, datetime | None] = {}
        for stream in binding.streams:
            if stream.name not in windowed:
                pins[stream.source_id] = None
                continue
            was = _effective_days(binding.backfill_days, declared[stream.name])
            now_wants = _effective_days(request, declared[stream.name])
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
                for stream in binding.streams
            },
            refetch=frozenset(
                stream.source_id
                for stream in binding.streams
                if pins[stream.source_id] != stream.backfill_after
            ),
        )

    async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None:
        ext = _require_ext(ctx.ext)
        binding = await self._find(ctx.ext, name)
        if binding is None:
            raise UnknownObject(f"no {SOURCE_KIND} object named {name!r}")
        for stream in binding.streams:
            await ext.remove_source(stream.source_id)
        await _store_subscribers(ext, name, {})

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
            f"(request_credentials for slot {spec.provider!r})"
        )

    async def _find(self, ext: ExtensionContext | None, name: str) -> _Binding | None:
        return next(
            (
                binding
                for binding in await _bindings_from_ext(_require_ext(ext))
                if binding.name == name
            ),
            None,
        )


async def on_page_change(ctx: HookContext) -> HookOutcome:
    """Alert each changed source's subscribers: group the batch by binding, and for every binding
    with subscribers invoke one turn per subscribed conversation. The turn carries per-stream
    added/updated/removed counts and the path to a change log holding every changed page, written
    into that conversation's own workspace — a delta runs to a full batch of pages, so counts are
    what the agent reads to decide and the file is what it reads to drill in. Only shared changes
    are surfaced — a page private to some member is never referenced, counted, logged, or cause to
    alert, so its existence never leaks to a subscriber who cannot read it, and a subscriber whose
    agent holds no grant for the changed source is alerted about nothing. Idempotency-keyed on
    binding + conversation + latest change, so a replayed batch never double-alerts; a changed row
    no binding claims alerts nothing."""
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
    for binding, binding_changes in by_binding.values():
        subscribers = await _subscribers_map(ctx.ext, binding.name)
        if not subscribers:
            continue
        shared = [change for change in binding_changes if change.subject == SHARED_SUBJECT]
        if not shared:
            continue
        for conversation, agent in subscribers.items():
            agent_id = UUID(agent)
            readable = await ctx.ext.readable_source_ids(
                SourceReader(
                    agent_id=agent_id,
                    requesting_member_id=None,
                    subjects=frozenset({SHARED_SUBJECT}),
                )
            )
            authorized = [change for change in shared if change.source_id in readable]
            if not authorized:
                continue
            latest = max(change.changed_at for change in authorized).isoformat()
            conversation_id = UUID(conversation)
            path = await _write_change_log(
                ctx.ext,
                conversation_id,
                binding,
                latest,
                authorized,
            )
            await ctx.ext.invoke(
                conversation_id,
                agent_id,
                _alert_message(binding, authorized, path),
                idempotency_key=f"source-sub:{binding.name}:{conversation}:{latest}",
            )
    return None


async def _write_change_log(
    ext: ExtensionContext,
    conversation_id: UUID,
    binding: _Binding,
    latest: str,
    changes: list[PageChange],
) -> str | None:
    """The whole delta as one JSON line per changed page, written into the subscribed
    conversation's workspace so the alerted agent reads it with its file tools instead of carrying
    it in context. Named for the same `latest` stamp the alert's idempotency key carries, so a
    replayed batch overwrites its own line-for-line identical file rather than appending a
    duplicate. A failed write propagates: the page feed inlines every body through this same blob
    store, so storage being unreachable fails the batch before the hook runs, and a handler that
    raises leaves the cursor unadvanced for the next tick to retry."""
    if ext.files is None:
        return None
    directory = f"{CHANGE_LOG_DIR}/{binding.name}"
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
    path = await ext.files.write(conversation_id, f"{directory}/{latest}.jsonl", body.encode())
    await ext.files.prune(conversation_id, directory)
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


def _alert_message(binding: _Binding, changes: list[PageChange], log_path: str | None) -> str:
    if len(changes) <= ALERT_NAMED_MAX:
        detail = (
            "Changed pages (object_get each to read what changed): "
            f"{'; '.join(_page_reference(change) for change in changes)}."
        )
    elif log_path is not None:
        detail = (
            f"Every changed page is one JSON line in {log_path} — narrow it with bash (jq, grep) "
            "or read it with offset/limit, then object_get the ones that matter."
        )
    else:
        detail = (
            "List them with object_list page, filtered on this source and stream and ordered by "
            "updated_at desc."
        )
    return (
        f"The source {binding.name!r} ({binding.summary()}) you subscribed to changed — "
        f"{_stream_counts(changes)}. {detail} Then tell the member what is new and why it matters."
    )


def _page_reference(change: PageChange) -> str:
    """The changed page as its `page` object reference, so the alerted agent can object_get it —
    the synced title makes the reference legible."""
    label = change.title[:ALERT_LABEL_CHARS] if change.title else "an untitled page"
    return f"{PAGE_KIND}/{change.page_id} ({label})"


def _validated_base_url(provider: str, base_url: str | None) -> str | None:
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
        "A registered content-sync binding: one provider account's selected streams, synced "
        "privately to its registering member unless shared. Changing streams is "
        "delete-and-recreate; only the registrar may share, while an admin may inspect or remove."
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
        "Unsharing is delete-and-recreate; a source's identity is otherwise its config, so "
        "changing streams is delete and recreate too. Delete is registrar-or-admin. Reads show "
        "shared sources plus the member's own — a workspace admin sees all. Its `access_to` link "
        "names the workspace credential slot a direct provider spends, or — while the source is "
        "private — the connection a brokered one resolves to. To be alerted when a "
        "source you can see changes, object_get it, then object_apply the same manifest with your "
        "own conversation id (shown as status.subscriber_id) added to `subscribers`; remove it to "
        "stop. You may only add or remove your own id, and subscribing is not admin-gated."
    ),
    spec_model=SourceSpec,
    store=SourceObjects(),
)
