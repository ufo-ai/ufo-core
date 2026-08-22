"""The environment a sandbox open exports, derived from the workspace's credentials and grants.

Two callers open a conversation's sandbox to run a command in it: the turn opener, under the turn's
run token, and an off-turn probe, under its own probe token. Both need the same derivations — git's
proxy-auth and credential config, the connector CLI sentinels, the conversation's own id — so they
live here rather than in either caller. What a probe deliberately does not export is the keyed
provider environment: those variables carry a model key's sentinel, and an unattended exec is not
the workspace's model spend to make.

Nothing here holds a secret. Every value is a sentinel the egress proxy swaps for the real
credential on the wire, so the sandbox never sees a key even for a host it authenticates to."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from uuid import UUID

from ufo.access.connectors import CliCredential
from ufo.access.credentials import (
    CredentialStore,
    HostChoice,
    credential_host,
    slot_is_set,
)
from ufo.access.grants import GrantStore, grant_sentinel
from ufo.ext.manifest import CredentialSlot
from ufo.o11y import log, warn
from ufo.workspace import ws_current

GIT_PROXY_AUTH_CONFIG = (("http.proxyAuthMethod", "basic"),)
CONVERSATION_ID_ENV = "UFO_CONVERSATION_ID"


@dataclass(frozen=True)
class ProbeEnv:
    """What an off-turn probe's sandbox open exports — `ConversationProbes`' environment seam, held
    here because the derivation reads the deploy's declared credential slots and the capability that
    consumes it cannot reach them.

    It is the turn opener's environment. Every sentinel a turn's own open exports is exported here
    too, keyed connectors included: a watch on a keyed provider — "is this Datadog monitor
    alerting?" — needs its `DD_API_KEY` sentinel, and the proxy holds the matching injection rule
    either way, so withholding the variable would leave that rule inert and 401 every probe.

    The deployment's own model key is the one thing an off-turn exec may not spend, and it is
    withheld where it actually lives: the platform sentinel rides each carrier's base environment,
    so the proxy declines to resolve its injection rule (`_without_the_model_key`). A workspace's
    BYOK model key needs nothing here either — those slots declare no injection target at all and
    are read in-process by the model registry, never exported to a sandbox.

    `acting_member_id` is the member the probe acts as — whoever armed the watch. It selects the
    connector CLI sentinel exactly as a turn's re-authorization does, so a probe of a member's own
    connected account exports that account's sentinel and the proxy forwards it; unset exports only
    the sentinels of connections shared with the whole workspace."""

    grants: GrantStore | None = None
    clis: Mapping[str, CliCredential] = field(default_factory=dict)
    credentials: CredentialStore | None = None
    slots: tuple[CredentialSlot, ...] = ()

    async def exports(
        self, conversation_id: UUID, probe_id: UUID, acting_member_id: UUID | None = None
    ) -> dict[str, str]:
        workspace_id = ws_current().workspace_id
        return {
            CONVERSATION_ID_ENV: str(conversation_id),
            **_git_config_env(
                (
                    *GIT_PROXY_AUTH_CONFIG,
                    *await _git_credential_config(self.credentials, self.slots, workspace_id),
                )
            ),
            **await _grant_cli_env(self.grants, self.clis, acting_member_id, probe_id),
            **await _keyed_provider_env(self.credentials, self.slots, workspace_id),
        }


def _git_config_env(settings: tuple[tuple[str, str], ...]) -> dict[str, str]:
    """git's own env channel for configuration, which is how the turn reaches a git it never
    writes a config file for: one indexed key/value pair per setting, and the count git reads."""
    env = {"GIT_CONFIG_COUNT": str(len(settings))}
    for index, (key, value) in enumerate(settings):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    return env


async def _git_credential_config(
    credentials: CredentialStore | None,
    slots: tuple[CredentialSlot, ...],
    workspace_id: UUID,
) -> tuple[tuple[str, str], ...]:
    """Each git host this workspace holds a credential for, as an `extraheader` carrying the slot's
    sentinel — never the secret, which the egress proxy swaps for `Basic` on the wire. git has no
    env var to read auth from, so a header it is; the proxy admits and MITMs the host off the same
    slot, which is why a slot with nothing stored must configure nothing: the sentinel would reach
    the provider verbatim over an opaque tunnel, failing a clone anonymous git would serve."""
    if credentials is None:
        return ()
    settings: list[tuple[str, str]] = []
    for slot in slots:
        target = slot.injection
        if target is None or target.git_basic_user is None:
            continue
        try:
            if not await slot_is_set(slot.name, slot.source, workspace_id, credentials):
                continue
            host = await credential_host(credentials, workspace_id, target.host)
        except Exception as error:
            warn(
                "sandbox.credential_slot_failed",
                slot=slot.name,
                error_class=type(error).__name__,
                error=str(error),
            )
            continue
        if host is None:
            warn("sandbox.git_host_unavailable", slot=slot.name)
            continue
        settings.append(
            (f"http.https://{host}/.extraheader", f"{target.header}: {target.sentinel}")
        )
    return tuple(settings)


async def _keyed_provider_env(
    credentials: CredentialStore | None,
    slots: tuple[CredentialSlot, ...],
    workspace_id: UUID,
) -> dict[str, str]:
    """Each keyed provider this workspace has a secret for, as the sandbox sees it: the declared env
    var set to the slot's sentinel — never the secret, which the egress proxy swaps in on the wire —
    and the resolved provider host, so the agent's own client authenticates and addresses the right
    region without holding or guessing either. A slot with nothing stored exports nothing, so the
    agent finds no half-usable variable for a provider the member has not keyed yet. A selection the
    declaration does not offer exports nothing and warns here as well as at the proxy, because the
    two roles withhold at different moments — the export when the sandbox opens, the egress when a
    request is made — and the member would otherwise see a variable that never appeared."""
    if credentials is None:
        return {}
    env: dict[str, str] = {}
    for slot in slots:
        target = slot.injection
        if target is None:
            continue
        host_env = target.host.env if isinstance(target.host, HostChoice) else None
        if target.env is None and host_env is None:
            continue
        try:
            if not await slot_is_set(slot.name, slot.source, workspace_id, credentials):
                continue
            host = await credential_host(credentials, workspace_id, target.host)
        except Exception as error:
            warn(
                "sandbox.credential_slot_failed",
                slot=slot.name,
                error_class=type(error).__name__,
                error=str(error),
            )
            continue
        if host is None:
            warn("sandbox.keyed_host_unavailable", slot=slot.name)
            continue
        if target.env is not None:
            env[target.env] = target.sentinel
        if host_env is not None:
            env[host_env] = host
    return env


async def _grant_cli_env(
    grants: GrantStore | None,
    clis: Mapping[str, CliCredential],
    acting_member_id: UUID | None,
    run_id: UUID,
) -> dict[str, str]:
    """Each connector-declared CLI env var whose provider this process may use — its member's
    own grant preferred, one shared with the agent's audience as the fallback — set to that
    grant's sentinel, so the CLI inside the sandbox authenticates and the proxy forwards by the
    same sentinel. A static env var names no account, so two accounts in the
    winning tier cannot be disambiguated per request: rather than silently pick one —
    `connector_account` fails loud on the same ambiguity — the export is skipped and logged
    against `run_id`, whichever run this open serves, so the CLI fails visibly to authenticate
    instead of acting as an unintended account."""
    if grants is None or not clis:
        return {}
    granted = await grants.active_grants()
    env: dict[str, str] = {}
    for provider, cli in clis.items():
        private = sorted(
            grant.account_id
            for grant in granted
            if grant.provider == provider
            and not grant.connection_shared
            and grant.owner_member_id == acting_member_id
        )
        shared = sorted(
            grant.account_id
            for grant in granted
            if grant.provider == provider and grant.connection_shared
        )
        accounts = private or shared
        if len(accounts) > 1:
            log(
                "sandbox.cli_grant_ambiguous",
                provider=provider,
                run_id=str(run_id),
                accounts=len(accounts),
            )
            continue
        if accounts:
            env[cli.env] = grant_sentinel(accounts[0])
    return env
