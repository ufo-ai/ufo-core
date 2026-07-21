"""The `source` object kind: registered content-sync bindings managed through the object verbs.

A source object is one provider binding — an account (or the workspace's BYOK credential) plus a
tenant URL where the provider needs one — carrying the selected streams, each stream a `source`
row the core sync driver polls. Identity IS the binding, so names derive from it
(`<provider>-<8-hex digest>`): apply with the wrong name refuses and hands back the exact one,
changing streams is delete-and-recreate, and re-applying the identical spec is a no-op. A source
is private to its registering member by default; the model decides `shared` at registration, and
only the registrar or the workspace owner may later flip a private source to shared — the
reverse is delete-and-recreate. Delete is registrar-or-owner too. Validation refuses with the
valid provider and stream sets, so discovery is error-driven plus `object_explain`.

The `subscribers` field is the one part any member who can see the source may change: a
conversation adds its own id (surfaced as `status.subscriber_id`) to be alerted when the source's
synced content changes, and removes it to stop. That edit is gated on visibility, not ownership,
and may only toggle the caller's own id — a conversation cannot subscribe or unsubscribe another,
nor reach a source private to someone else. The `page_change` hook reads a changed binding's
subscribers and invokes one alert turn per subscribed conversation, referencing the changed pages
as `page/<id>` objects (only the shared pages a subscriber may read)."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ufo.sdk.connectors import ConnectorRegistry
from ufo.sdk.context import CredentialSlotUnset, ExtensionContext
from ufo.sdk.manifest import HookContext, HookOutcome, PageChangeBatch
from ufo.sdk.objects import (
    MemberOwnedObjects,
    ObjectKind,
    ObjectOwner,
    OwnedRow,
    UnknownObject,
    VerbNotSupported,
)
from ufo.sdk.sources import SHARED_SUBJECT, ConnectorSourceConfig, PageChange, member_subject
from ufo.sdk.tools import ConnectUnavailable, ToolContext
from ufo_ext_sources.pages import PAGE_KIND
from ufo_ext_sources.registry import CONNECTORS

SOURCE_KIND = "source"
DIRECT_ACCOUNT = "default"
NAME_DIGEST_HEX = 8
SUMMARY_MAX = 120
SUBSCRIBERS_PREFIX = "subscribers:"
ALERT_LABELS_MAX = 5
ALERT_LABEL_CHARS = 60
DOMAIN_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
TENANT_URL_RULES: dict[str, tuple[re.Pattern[str], re.Pattern[str], str]] = {
    "activecampaign": (
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


def _binding_name(provider: str, account: str, base_url: str | None) -> str:
    digest = hashlib.sha256(
        json.dumps(
            {"account": account, "base_url": base_url, "provider": provider}, sort_keys=True
        ).encode()
    ).hexdigest()[:NAME_DIGEST_HEX]
    return f"{provider.replace('_', '-')}-{digest}"


@dataclass(frozen=True)
class _Stream:
    name: str
    next_sync_at: datetime
    consecutive_errors: int
    source_id: UUID


@dataclass(frozen=True)
class _Binding:
    provider: str
    account: str
    base_url: str | None
    subject: str
    owner_member_id: UUID | None
    streams: tuple[_Stream, ...]

    @property
    def name(self) -> str:
        return _binding_name(self.provider, self.account, self.base_url)

    def spec(self, subscribers: tuple[str, ...] = ()) -> SourceSpec:
        return SourceSpec(
            provider=self.provider,
            streams=tuple(stream.name for stream in self.streams),
            account_id="" if self.account == DIRECT_ACCOUNT else self.account,
            base_url=self.base_url or "",
            shared=self.subject == SHARED_SUBJECT,
            subscribers=subscribers,
        )

    def summary(self) -> str:
        streams = ", ".join(stream.name for stream in self.streams)
        return f"{self.provider} ({self.account}): {streams}"[:SUMMARY_MAX]


def _require_ext(ctx: ToolContext) -> ExtensionContext:
    if ctx.ext is None:
        raise RuntimeError("source objects dispatched without their ExtensionContext")
    return ctx.ext


def _require_connectors(ctx: ToolContext) -> ConnectorRegistry:
    if ctx.connectors is None:
        raise RuntimeError("source objects dispatched without the turn's connector registry")
    return ctx.connectors


async def _bindings_from_ext(ext: ExtensionContext) -> tuple[_Binding, ...]:
    grouped: dict[tuple[str, str, str | None], list[_Stream]] = {}
    disclosure: dict[tuple[str, str, str | None], tuple[str, UUID | None]] = {}
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
            )
        )
        disclosure.setdefault(key, (record.subject, record.owner_member_id))
    return tuple(
        _Binding(
            provider=provider,
            account=account,
            base_url=base_url,
            subject=disclosure[(provider, account, base_url)][0],
            owner_member_id=disclosure[(provider, account, base_url)][1],
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


def _binding_identity(spec: SourceSpec) -> tuple[str, tuple[str, ...], str, str, bool]:
    return (spec.provider, tuple(sorted(spec.streams)), spec.account_id, spec.base_url, spec.shared)


def _self_only_change(old: tuple[str, ...], new: tuple[str, ...], caller: str) -> None:
    """A subscribers edit may only add or remove the caller's own conversation id; any other
    difference is refused so a conversation cannot subscribe or unsubscribe another."""
    if (set(old) ^ set(new)) - {caller}:
        raise ValueError(
            "you may only add or remove your own conversation (status.subscriber_id) in "
            "subscribers; leave every other id unchanged"
        )


SHARE_GATE = "only the registering member or the workspace owner may change a source's sharing"
DELETE_GATE = "only the registering member or the workspace owner may remove a source"


@dataclass(frozen=True)
class SourceObjects(MemberOwnedObjects[SourceSpec]):
    """The kind's handlers over the workspace's registered source rows: get/list reconstruct
    bindings by grouping rows on (provider, account, base_url); apply validates provider, streams,
    tenant URL, and auth exactly as registration always has, then registers one row per stream
    (the first sync is scheduled immediately) — private to the registering member unless the
    model asks for `shared`; delete removes the binding's rows and their synced pages follow
    through the page-tombstone pipeline. The per-member visibility and registrar-or-owner gate is
    the base's; this kind supplies the bindings, their specs, and the register/share/remove acts."""

    kind_name: ClassVar[str] = SOURCE_KIND
    mutate_gate: ClassVar[str] = SHARE_GATE
    delete_gate: ClassVar[str] = DELETE_GATE
    delete_requires_speaker: ClassVar[bool] = True

    async def apply(
        self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None
    ) -> None:
        """A subscribers-only edit on a source the caller can already see (`old` is non-None only
        for a visible source, since the base `get` hides the rest) is gated on visibility, not
        ownership: any member who sees the source may add or remove their own conversation. Every
        other apply — register, share-flip, recreate — goes through the base's registrar-or-owner
        gate."""
        if old is not None and _binding_identity(spec) == _binding_identity(old):
            caller = ctx.turn.conversation_id.hex
            _self_only_change(old.subscribers, spec.subscribers, caller)
            await self._edit_subscribers(
                _require_ext(ctx), name, spec.subscribers, caller, ctx.turn.agent_id
            )
            return
        await super().apply(ctx, name, spec, old)

    async def _edit_subscribers(
        self, ext: ExtensionContext, name: str, desired: tuple[str, ...], caller: str, agent: UUID
    ) -> None:
        """Toggle only the caller's membership (the self-only rule already held the diff to it):
        add captures the caller's agent so the alert re-enters the same conversation and agent;
        remove drops it. Other subscribers' entries are preserved untouched."""
        mapping = await _subscribers_map(ext, name)
        if caller in desired:
            mapping[caller] = agent.hex
        else:
            mapping.pop(caller, None)
        await _store_subscribers(ext, name, mapping)

    async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow, ...]:
        return tuple(
            OwnedRow(
                name=binding.name,
                summary=binding.summary(),
                owner=ObjectOwner(
                    member_id=binding.owner_member_id,
                    shared=binding.subject == SHARED_SUBJECT,
                ),
            )
            for binding in await self._bindings(ctx)
        )

    async def _spec(self, ctx: ToolContext, name: str) -> SourceSpec | None:
        binding = await self._find(ctx, name)
        if binding is None:
            return None
        subscribers = tuple(sorted((await _subscribers_map(_require_ext(ctx), name)).keys()))
        return binding.spec(subscribers=subscribers)

    async def _status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None:
        binding = await self._find(ctx, name)
        if binding is None:
            return None
        shared = binding.subject == SHARED_SUBJECT
        caller = ctx.turn.conversation_id.hex
        subscribers = await _subscribers_map(_require_ext(ctx), name)
        status: dict[str, JsonValue] = {
            "shared": shared,
            "subscriber_id": caller,
            "subscribed": caller in subscribers,
            "streams": {
                stream.name: {
                    "next_sync_at": stream.next_sync_at.isoformat(),
                    "consecutive_errors": stream.consecutive_errors,
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
        ext = _require_ext(ctx)
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
        available = tuple(stream.name for stream in connector_cls().streams())
        streams = tuple(sorted(dict.fromkeys(spec.streams)))
        unsupported = [stream for stream in streams if stream not in available]
        if unsupported:
            raise ValueError(
                f"{spec.provider!r} does not provide {', '.join(map(repr, unsupported))}; "
                f"streams: {', '.join(available)}"
            )
        base_url = _validated_base_url(spec.provider, spec.base_url or None)
        account = await self._resolved_account(ctx, spec)
        derived = _binding_name(spec.provider, account, base_url)
        if name != derived:
            raise ValueError(
                f"source names derive from the binding — apply this spec as name {derived!r}"
            )
        resolved = SourceSpec(
            provider=spec.provider,
            streams=streams,
            account_id="" if account == DIRECT_ACCOUNT else account,
            base_url=base_url or "",
            shared=spec.shared,
        )
        binding = await self._find(ctx, name)
        if binding is not None:
            old_spec = binding.spec()
            if resolved != old_spec:
                if resolved.model_copy(update={"shared": old_spec.shared}) != old_spec:
                    raise VerbNotSupported(
                        "a source's identity is its config — delete the binding and recreate it"
                    )
                if not resolved.shared:
                    raise VerbNotSupported(
                        "a shared source stays shared — delete the binding and "
                        "recreate it privately"
                    )
                await ext.set_source_subject(
                    tuple(stream.source_id for stream in binding.streams), SHARED_SUBJECT
                )
            return
        subject = SHARED_SUBJECT if resolved.shared else member_subject(ctx.speaker_member_id)
        for stream in streams:
            await ext.register_source(
                spec.provider,
                ConnectorSourceConfig(account=account, stream=stream, base_url=base_url),
                subject=subject,
                owner_member_id=ctx.speaker_member_id,
            )

    async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None:
        ext = _require_ext(ctx)
        binding = await self._find(ctx, name)
        if binding is None:
            raise UnknownObject(f"no {SOURCE_KIND} object named {name!r}")
        for stream in binding.streams:
            await ext.remove_source(stream.source_id)
        await _store_subscribers(ext, name, {})

    async def _resolved_account(self, ctx: ToolContext, spec: SourceSpec) -> str:
        ext = _require_ext(ctx)
        registry = _require_connectors(ctx)
        if spec.provider in registry.entries:
            try:
                accounts = await ctx.connector_accounts(spec.provider)
            except ConnectUnavailable:
                accounts = ()
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
            return account
        if spec.provider not in ext.credentials.declared or registry.fallback is None:
            raise ValueError(f"no direct authentication backend can sync {spec.provider!r}")
        if spec.account_id:
            raise ValueError(
                f"{spec.provider!r} uses its workspace credential, not a connected account"
            )
        try:
            await ext.credentials.get(spec.provider)
        except CredentialSlotUnset:
            raise ValueError(
                f"add the {spec.provider!r} credential before registering its sources "
                f"(request_credentials for slot {spec.provider!r})"
            ) from None
        return DIRECT_ACCOUNT

    async def _find(self, ctx: ToolContext, name: str) -> _Binding | None:
        return next(
            (binding for binding in await self._bindings(ctx) if binding.name == name), None
        )

    async def _bindings(self, ctx: ToolContext) -> tuple[_Binding, ...]:
        return await _bindings_from_ext(_require_ext(ctx))


async def on_page_change(ctx: HookContext) -> HookOutcome:
    """Alert each changed source's subscribers: group the batch by binding, and for every binding
    with subscribers invoke one turn per subscribed conversation, referencing the changed pages as
    `page/<id>` objects. Only shared changes are surfaced — a page private to some member is never
    referenced, counted, or cause to alert, so its existence never leaks to a subscriber who
    cannot read it. Idempotency-keyed on binding + conversation + latest change, so a replayed
    batch never double-alerts; a changed row no binding claims alerts nothing."""
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
        latest = max(change.changed_at for change in shared).isoformat()
        message = _alert_message(binding, shared)
        for conversation, agent in subscribers.items():
            await ctx.ext.invoke(
                UUID(conversation),
                UUID(agent),
                message,
                idempotency_key=f"source-sub:{binding.name}:{conversation}:{latest}",
            )
    return None


def _alert_message(binding: _Binding, changes: list[PageChange]) -> str:
    references = [_page_reference(change) for change in changes[:ALERT_LABELS_MAX]]
    more = len(changes) - len(references)
    listing = "; ".join(references) + (f"; +{more} more" if more else "")
    removed = sum(1 for change in changes if change.tombstone)
    removed_note = f" ({removed} removed)" if removed else ""
    noun = "page" if len(changes) == 1 else "pages"
    return (
        f"The source {binding.name!r} ({binding.summary()}) you subscribed to changed — "
        f"{len(changes)} synced {noun}{removed_note}. Changed pages (object_get each to read what "
        f"changed): {listing}. Then tell the member what is new and why it matters."
    )


def _page_reference(change: PageChange) -> str:
    """The changed page as its `page` object reference, so the alerted agent can object_get it —
    a label from the body's first line makes the reference legible."""
    label = "an empty page"
    if not change.tombstone and change.body:
        label = change.body.splitlines()[0].lstrip("# ")[:ALERT_LABEL_CHARS]
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
        "delete-and-recreate; sharing and delete are gated to the registrar or the owner."
    ),
    guidance=(
        "Apply a manifest to register selected streams of a content-source provider; an unknown "
        "provider or stream is refused with the valid choices, and an unknown name is refused "
        "with the exact derived name to re-apply (names derive from provider, account, and "
        "tenant URL). Brokered providers use this agent's active connected-account grant; "
        "providers without a broker use their workspace credential. A source syncs privately to "
        "its registering member by default; set `shared: true` at apply — or in a later "
        "re-apply by the registrar or the workspace owner — to sync it into workspace-shared "
        "memory instead, only when the member's words say the source is for the team. "
        "Unsharing is delete-and-recreate; a source's identity is otherwise its config, so "
        "changing streams is delete and recreate too. Delete is registrar-or-owner. Reads show "
        "shared sources plus the member's own — a workspace owner sees all. To be alerted when a "
        "source you can see changes, object_get it, then object_apply the same manifest with your "
        "own conversation id (shown as status.subscriber_id) added to `subscribers`; remove it to "
        "stop. You may only add or remove your own id, and subscribing is not owner-gated."
    ),
    spec_model=SourceSpec,
    store=SourceObjects(),
)
