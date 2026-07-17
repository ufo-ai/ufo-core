"""The `source` object kind: registered content-sync bindings managed through the object verbs.

A source object is one provider binding — an account (or the workspace's BYOK credential) plus a
tenant URL where the provider needs one — carrying the selected streams, each stream a `source`
row the core sync driver polls. Identity IS the binding, so names derive from it
(`<provider>-<8-hex digest>`): apply with the wrong name refuses and hands back the exact one,
changing streams is delete-and-recreate, and re-applying the identical spec is a no-op. Every
mutation is owner-gated; validation refuses with the valid provider and stream sets, so discovery
is error-driven plus `object_explain`."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ufo.sdk.connectors import ConnectorRegistry
from ufo.sdk.context import CredentialSlotUnset, ExtensionContext
from ufo.sdk.objects import (
    OBJECT_LIST_PAGE,
    ObjectKind,
    ObjectPage,
    ObjectRow,
    OwnerRequired,
    VerbNotSupported,
)
from ufo.sdk.sources import ConnectorSourceConfig
from ufo.sdk.tools import ConnectUnavailable, ToolContext
from ufo_ext_sources.registry import CONNECTORS

SOURCE_KIND = "source"
DIRECT_ACCOUNT = "default"
NAME_DIGEST_HEX = 8
SUMMARY_MAX = 120
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
    streams: tuple[_Stream, ...]

    @property
    def name(self) -> str:
        return _binding_name(self.provider, self.account, self.base_url)

    def spec(self) -> SourceSpec:
        return SourceSpec(
            provider=self.provider,
            streams=tuple(stream.name for stream in self.streams),
            account_id="" if self.account == DIRECT_ACCOUNT else self.account,
            base_url=self.base_url or "",
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


@dataclass(frozen=True)
class SourceObjects:
    """The kind's handlers over the workspace's registered source rows: get/list reconstruct
    bindings by grouping rows on (provider, account, base_url); apply validates provider, streams,
    tenant URL, and auth exactly as registration always has, then registers one row per stream
    (the first sync is scheduled immediately); delete removes the binding's rows and their synced
    pages follow through the page-tombstone pipeline. Mutations are owner-gated."""

    async def list(self, ctx: ToolContext, query: str, cursor: str) -> ObjectPage:
        bindings = [
            binding
            for binding in await self._bindings(ctx)
            if query in binding.name or query in binding.summary()
        ]
        bindings.sort(key=lambda binding: binding.name)
        remaining = [b for b in bindings if b.name > cursor] if cursor else bindings
        page, rest = remaining[:OBJECT_LIST_PAGE], remaining[OBJECT_LIST_PAGE:]
        rows = tuple(ObjectRow(name=b.name, summary=b.summary()) for b in page)
        return ObjectPage(rows=rows, next_cursor=page[-1].name if rest else None)

    async def get(self, ctx: ToolContext, name: str) -> SourceSpec | None:
        binding = await self._find(ctx, name)
        return None if binding is None else binding.spec()

    async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None:
        binding = await self._find(ctx, name)
        if binding is None:
            return None
        return {
            "streams": {
                stream.name: {
                    "next_sync_at": stream.next_sync_at.isoformat(),
                    "consecutive_errors": stream.consecutive_errors,
                }
                for stream in binding.streams
            }
        }

    async def apply(
        self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None
    ) -> None:
        ext = _require_ext(ctx)
        if not await ctx.speaker_is_owner():
            raise OwnerRequired("only the workspace owner can register shared sources")
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
        )
        if old is not None and resolved != old:
            raise VerbNotSupported(
                "a source's identity is its config — delete the binding and recreate it"
            )
        for stream in streams:
            await ext.register_source(
                spec.provider,
                ConnectorSourceConfig(account=account, stream=stream, base_url=base_url),
            )

    async def delete(self, ctx: ToolContext, name: str) -> None:
        ext = _require_ext(ctx)
        if not await ctx.speaker_is_owner():
            raise OwnerRequired("only the workspace owner can remove shared sources")
        binding = await self._find(ctx, name)
        if binding is None:
            raise ValueError(f"no source binding named {name!r}")
        for stream in binding.streams:
            await ext.remove_source(stream.source_id)

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
        grouped: dict[tuple[str, str, str | None], list[_Stream]] = {}
        for record in await _require_ext(ctx).sources():
            if record.backend not in CONNECTORS:
                continue
            config = ConnectorSourceConfig.model_validate(record.config)
            grouped.setdefault((record.backend, config.account, config.base_url), []).append(
                _Stream(
                    name=config.stream,
                    next_sync_at=record.next_sync_at,
                    consecutive_errors=record.consecutive_errors,
                    source_id=record.id,
                )
            )
        return tuple(
            _Binding(
                provider=provider,
                account=account,
                base_url=base_url,
                streams=tuple(sorted(streams, key=lambda stream: stream.name)),
            )
            for (provider, account, base_url), streams in grouped.items()
        )


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
        "A registered content-sync binding: one provider account's selected streams syncing "
        "into shared memory. Owner-only mutations: create and delete; changing streams is "
        "delete-and-recreate."
    ),
    guidance=(
        "Apply a manifest to register selected streams of a content-source provider into "
        "shared memory; an unknown provider or stream is refused with the valid choices, and "
        "an unknown name is refused with the exact derived name to re-apply (names derive from "
        "provider, account, and tenant URL). Brokered providers use this agent's active "
        "connected-account grant; providers without a broker use their workspace credential. "
        "Only the workspace owner can register or delete sources; a source's identity is its "
        "config, so changing one is delete and recreate."
    ),
    spec_model=SourceSpec,
    store=SourceObjects(),
)
