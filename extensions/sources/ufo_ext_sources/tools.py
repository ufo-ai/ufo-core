"""Chat-native discovery and registration for the source catalog."""

import json
import re
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from ufo.sdk.context import CredentialSlotUnset
from ufo.sdk.sources import ConnectorSourceConfig
from ufo.sdk.tools import ConnectUnavailable, TextContent, ToolContext, ToolDef, ToolResult
from ufo_ext_sources.registry import CONNECTORS

DIRECT_ACCOUNT = "default"
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


class SyncSourceInput(BaseModel):
    provider: str | None = Field(
        default=None,
        description="Source provider slug. Omit to list providers; select one to inspect streams.",
    )
    streams: tuple[str, ...] = Field(
        default=(),
        description="Exact stream names to sync. Omit with a provider to inspect its catalog.",
    )
    account_id: str | None = Field(
        default=None,
        description="Connected-account ID. Required only when this agent has multiple accounts.",
    )
    base_url: str | None = Field(
        default=None,
        description="Tenant API URL for providers whose catalog entry requires one.",
    )


async def sync_source(ctx: ToolContext, args: SyncSourceInput) -> ToolResult:
    if ctx.ext is None:
        raise RuntimeError("sync_source dispatched without its ExtensionContext")
    if ctx.connectors is None:
        raise RuntimeError("sync_source dispatched without the turn's connector registry")
    brokered = ctx.connectors.entries
    direct = ctx.ext.credentials.declared
    has_direct_backend = ctx.connectors.fallback is not None
    if args.provider is None:
        if args.streams:
            return _error("provider_required", "Choose a provider before selecting streams.")
        if args.account_id is not None or args.base_url is not None:
            return _error(
                "invalid_discovery_input",
                "account_id and base_url apply only when registering provider streams.",
            )
        return _json_result(
            {
                "providers": [
                    _provider_row(
                        provider,
                        brokered=provider in brokered,
                        direct_available=provider in direct and has_direct_backend,
                    )
                    for provider in sorted(CONNECTORS)
                ]
            }
        )

    connector_cls = CONNECTORS.get(args.provider)
    if connector_cls is None:
        return _error(
            "unsupported_provider",
            f"Unknown source provider {args.provider!r}.",
            providers=sorted(CONNECTORS),
        )
    connector = connector_cls()
    stream_specs = connector.streams()
    available_streams = tuple(stream.name for stream in stream_specs)
    if not args.streams:
        if args.account_id is not None or args.base_url is not None:
            return _error(
                "streams_required",
                "Choose one or more streams before supplying account_id or base_url.",
            )
        provider = _provider_row(
            args.provider,
            brokered=args.provider in brokered,
            direct_available=args.provider in direct and has_direct_backend,
        )
        provider["streams"] = list(available_streams)
        provider["canonical_streams"] = [stream.name for stream in stream_specs if stream.canonical]
        return _json_result({"provider": provider})
    if not await ctx.speaker_is_owner():
        return _error("owner_required", "Only the workspace owner can register shared sources.")

    selected_streams = tuple(dict.fromkeys(args.streams))
    unsupported = [stream for stream in selected_streams if stream not in available_streams]
    if unsupported:
        return _error(
            "unsupported_stream",
            f"{args.provider!r} does not provide every requested stream.",
            unsupported=unsupported,
            streams=list(available_streams),
        )
    try:
        base_url = _validated_base_url(args.provider, args.base_url)
    except ValueError as error:
        return _error("invalid_base_url", str(error), provider=args.provider)

    if args.provider in brokered:
        try:
            accounts = await ctx.connector_accounts(args.provider)
        except ConnectUnavailable:
            accounts = ()
        if not accounts:
            return _error(
                "account_not_connected",
                f"Connect a {args.provider!r} account before registering its sources.",
                action={"tool": "connect_account", "arguments": {"provider": args.provider}},
            )
        if args.account_id is None and len(accounts) > 1:
            return _error(
                "account_ambiguous",
                f"Choose which {args.provider!r} account to sync.",
                accounts=list(accounts),
            )
        account = args.account_id or accounts[0]
        if account not in accounts:
            return _error(
                "account_not_granted",
                f"This agent has no active {args.provider!r} grant for {account!r}.",
                accounts=list(accounts),
            )
        auth = "broker"
    else:
        if args.provider not in direct or not has_direct_backend:
            return _error(
                "auth_unavailable",
                f"No direct authentication backend can sync {args.provider!r}.",
            )
        if args.account_id is not None:
            return _error(
                "account_not_applicable",
                f"{args.provider!r} uses its workspace credential, not a connected account.",
            )
        try:
            await ctx.ext.credentials.get(args.provider)
        except CredentialSlotUnset:
            return _error(
                "credential_required",
                f"Add the {args.provider!r} credential before registering its sources.",
                action={
                    "tool": "request_credentials",
                    "arguments": {
                        "reason": f"Authenticate {args.provider} source sync.",
                        "prompts": [
                            {
                                "slot": args.provider,
                                "prompt": f"Enter the {args.provider} API credential.",
                            }
                        ],
                    },
                },
            )
        account = DIRECT_ACCOUNT
        auth = "direct"

    sources: list[dict[str, str]] = []
    for stream in selected_streams:
        source_id = await ctx.ext.register_source(
            args.provider,
            ConnectorSourceConfig(account=account, stream=stream, base_url=base_url),
        )
        sources.append({"stream": stream, "source_id": str(source_id)})
    return _json_result(
        {
            "provider": args.provider,
            "account_id": account,
            "auth": auth,
            "sources": sources,
        }
    )


def _provider_row(provider: str, *, brokered: bool, direct_available: bool) -> dict[str, object]:
    return {
        "provider": provider,
        "auth": "broker" if brokered else ("direct" if direct_available else "unavailable"),
        "requires_base_url": not bool(CONNECTORS[provider].base_url),
    }


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


def _json_result(payload: dict[str, object], *, is_error: bool = False) -> ToolResult:
    return ToolResult(
        content=(TextContent(text=json.dumps(payload, sort_keys=True)),), is_error=is_error
    )


def _error(code: str, message: str, **details: object) -> ToolResult:
    return _json_result({"error": {"code": code, "message": message, **details}}, is_error=True)


SYNC_SOURCE_TOOL = ToolDef(
    name="sync_source",
    description=(
        "Discover content-source providers and register selected streams into shared memory. Omit "
        "provider to list providers; pass a provider without streams to inspect its stream "
        "catalog; pass provider plus streams to register them. Brokered providers use this "
        "agent's active connected-account grant; providers without a broker use their workspace "
        "credential. Only the workspace owner can register sources."
    ),
    input_model=SyncSourceInput,
    handler=sync_source,
    side_effecting=True,
)
