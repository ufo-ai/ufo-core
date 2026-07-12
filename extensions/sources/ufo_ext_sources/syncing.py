"""The chat act that starts a provider syncing: `sync_source` registers connector source rows.

A member asks in conversation ("start syncing my Google Meet transcripts"); the agent calls this
tool and core's sync driver takes it from there — the speaker gates the granting act, subsequent
syncs are the wire's job. Auth resolves before any row lands, so a misconfigured source fails this
call with what to do rather than fail every future sync run — and it resolves exactly as the sync
driver will: a brokered provider (one a connector extension registers) through the turn-agent's
grant, any other through the deploy's fallback auth backend, whose BYOK key must already sit in
the provider's credential slot. Registration is idempotent on (workspace, backend, config) —
re-asking settles on the same rows. Without a stream named, the provider's canonical streams
register; pages land in shared memory and recall like any synced source."""

from pydantic import BaseModel, Field

from ufo.sdk.context import CredentialSlotUnset
from ufo.sdk.sources import ConnectorSourceConfig
from ufo.sdk.tools import ConnectUnavailable, TextContent, ToolContext, ToolResult
from ufo_ext_sources.registry import CONNECTORS

DIRECT_ACCOUNT = "default"


class SyncSourceInput(BaseModel):
    provider: str = Field(
        description="The source provider's slug, e.g. 'google_meet', 'gmail', 'notion', 'linear'."
    )
    stream: str = Field(
        default="",
        description="One stream to sync; omit for the provider's canonical content streams.",
    )
    account: str = Field(
        default="",
        description="Broker connected-account id when the agent holds several grants for the "
        "provider; omit otherwise.",
    )
    base_url: str = Field(
        default="",
        description="Provider host for per-tenant providers only, "
        "e.g. 'https://<tenant>.freshdesk.com'.",
    )


async def sync_source(ctx: ToolContext, args: SyncSourceInput) -> ToolResult:
    if ctx.ext is None:
        raise RuntimeError("sync_source dispatched without its ExtensionContext")
    connector_cls = CONNECTORS.get(args.provider)
    if connector_cls is None:
        raise ValueError(
            f"unknown source provider {args.provider!r}; providers: {', '.join(sorted(CONNECTORS))}"
        )
    if not connector_cls.base_url and not args.base_url:
        raise ValueError(
            f"{args.provider!r} is a per-tenant provider: pass base_url (the tenant's own host, "
            f"e.g. 'https://<tenant>.{args.provider}.com')"
        )
    streams = connector_cls().streams()
    if args.stream:
        selected = [stream for stream in streams if stream.name == args.stream]
        if not selected:
            names = ", ".join(stream.name for stream in streams)
            raise ValueError(f"{args.provider!r} has no stream {args.stream!r}; streams: {names}")
    else:
        selected = [stream for stream in streams if stream.canonical]
    account = await _account(ctx, args)
    for stream in selected:
        await ctx.ext.register_source(
            args.provider,
            ConnectorSourceConfig(
                account=account, stream=stream.name, base_url=args.base_url or None
            ),
        )
    synced = ", ".join(stream.name for stream in selected)
    return ToolResult(
        content=(
            TextContent(
                text=(
                    f"Syncing {args.provider} ({synced}) for account {account!r} — pages land in "
                    "shared memory on the next sync run and recall like any synced source."
                )
            ),
        )
    )


async def _account(ctx: ToolContext, args: SyncSourceInput) -> str:
    """The account handle the source rows carry, resolved the way the sync driver will resolve the
    credential: a brokered provider through the turn-agent's grant (a BYOK key cannot stand in —
    the driver routes a brokered provider to its broker unconditionally), any other through the
    fallback auth backend's credential slot."""
    if ctx.connectors is None or ctx.ext is None:
        raise RuntimeError("sync_source dispatched without the turn's connector registry")
    if ctx.connectors.entries.get(args.provider) is not None:
        try:
            return await ctx.connector_account(args.provider, args.account or None)
        except (ConnectUnavailable, ValueError) as error:
            raise ValueError(
                f"{args.provider!r} syncs through its broker but the agent holds no matching "
                f"grant ({error}). Connect the account in chat first."
            ) from error
    if ctx.connectors.fallback is None:
        raise ValueError(
            f"no broker registers {args.provider!r} and the deploy selects no fallback: have the "
            f'operator set `[connectors] auth_backend = "direct"` and a provider key with '
            f"`ufoctl credential set {args.provider}`."
        )
    try:
        await ctx.ext.credentials.get(args.provider)
    except CredentialSlotUnset:
        raise ValueError(
            f"{args.provider!r} syncs through the fallback auth backend but its credential slot "
            f"holds no key: have the operator set one with "
            f"`ufoctl credential set {args.provider}`."
        ) from None
    return DIRECT_ACCOUNT
