"""The environment a sandbox open exports, derived from the workspace's credentials and grants.

Two callers open a conversation's sandbox to run a command in it: the turn opener, under the turn's
run token, and an off-turn probe, under its own probe token. Both need the same derivations — git's
proxy-auth and credential-helper config, the connector CLI sentinels, the conversation's own id — so
they live here rather than in either caller. What a probe deliberately does not export is the keyed
provider environment: those variables carry a model key's sentinel, and an unattended exec is not
the workspace's model spend to make.

Nothing here holds a secret. Every value is a sentinel the egress proxy swaps for the real
credential on the wire, so the sandbox never sees a key even for a host it authenticates to."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from uuid import UUID

from ufo.harness.o11y import log, warn
from ufo.harness.sandbox.session import ProxyEndpoint, egress_proxy_env
from ufo.runtime.access.connectors import CliCredential
from ufo.runtime.access.credentials import (
    CredentialSlotUnset,
    CredentialStore,
    HostChoice,
    credential_host,
)
from ufo.runtime.access.grants import Grant, GrantStore, cli_accounts, grant_sentinel
from ufo.runtime.access.workspace_slots import WorkspaceSlots
from ufo.runtime.tools.bridge import TOOL_BRIDGE_URL_ENV
from ufo.runtime.workspace import ws_current

GIT_PROXY_AUTH_CONFIG = (("http.proxyAuthMethod", "basic"),)
CONVERSATION_ID_ENV = "UFO_CONVERSATION_ID"
GIT_IDENTITY_ENV = frozenset(
    {"GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"}
)
"""The committer variables a git-cloning grant exports. Dropped on every re-authorization beside
the CLI variables, so a scope holding no such grant commits under no stale identity."""


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

    The member the probe acts for selects connector CLI sentinels exactly as a turn's
    re-authorization does."""

    grants: GrantStore | None = None
    clis: Mapping[str, CliCredential] = field(default_factory=dict)
    credentials: CredentialStore | None = None
    slots: WorkspaceSlots = field(default_factory=WorkspaceSlots)

    async def exports(
        self,
        conversation_id: UUID,
        probe_id: UUID,
        member_id: UUID | None = None,
    ) -> dict[str, str]:
        workspace_id = ws_current().workspace_id
        return {
            CONVERSATION_ID_ENV: str(conversation_id),
            **_git_config_env((*GIT_PROXY_AUTH_CONFIG, *cli_git_config(self.clis))),
            **await _grant_cli_env(self.grants, self.clis, probe_id, member_id),
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


def cli_git_config(clis: Mapping[str, CliCredential]) -> tuple[tuple[str, str], ...]:
    """Each connector git host wired to the CLI's own credential helper, so a plain `git clone` or
    `git push` there authenticates exactly as the CLI's clone does: git asks the helper, the helper
    answers with the sentinel the CLI's env var carries, and the proxy swaps the token in. The
    config names no account, so it is set once at open and holds across every re-authorization
    that rewrites the env var — the variable is the whole credential. A turn with no usable grant
    exports no variable, the helper answers nothing, and an anonymous clone of a public repository
    proceeds as it would with no helper at all."""
    settings: list[tuple[str, str]] = []
    for cli in clis.values():
        if cli.git is None:
            continue
        key = f"credential.https://{cli.git.host}.helper"
        settings.append((key, ""))
        settings.append((key, cli.git.helper))
    return tuple(settings)


_SAMPLE_PROXY = ProxyEndpoint(port=443, ca_cert="", public_url="https://proxy.invalid")
"""A stand-in endpoint `sandbox_exported_env` reads the proxy environment's variable *names* off.
The names are the same for every endpoint, and reading them from the export itself is what keeps
one list: a variable added to `egress_proxy_env` is reserved without being written down again."""


def sandbox_exported_env(clis: Mapping[str, CliCredential]) -> frozenset[str]:
    """Every sandbox variable core itself exports on an open, whatever the carrier: the egress
    proxy's environment (the model-key sentinels, the proxy URLs, the CA paths), git's config
    channel including each connector CLI's credential helper, the conversation id, and the
    committer identity a cloning grant sets.

    One sandbox variable carries one value and the later export wins the merge, so this namespace
    is claimed beside the deploy's declared slots: a workspace declaring a slot on one of these
    names would replace a value core exported — a model-key sentinel the proxy holds no workspace
    rule for, and every model call from the sandbox would carry a sentinel nothing swaps."""
    return frozenset(
        {
            CONVERSATION_ID_ENV,
            TOOL_BRIDGE_URL_ENV,
            *GIT_IDENTITY_ENV,
            *_git_config_env((*GIT_PROXY_AUTH_CONFIG, *cli_git_config(clis))),
            *egress_proxy_env(_SAMPLE_PROXY, "sample-run-token"),
        }
    )


async def _keyed_provider_env(
    credentials: CredentialStore | None,
    slots: WorkspaceSlots,
    workspace_id: UUID,
) -> dict[str, str]:
    """The sandbox sees only the sentinel; the egress proxy swaps the secret in on the wire."""
    if credentials is None:
        return {}
    env: dict[str, str] = {}
    for slot in await slots.all(workspace_id):
        target = slot.injection
        if target is None:
            continue
        host_env = target.host.env if isinstance(target.host, HostChoice) else None
        if target.env is None and host_env is None:
            continue
        try:
            await credentials.get(workspace_id, slot.name)
            host = await credential_host(credentials, workspace_id, target.host)
        except CredentialSlotUnset:
            continue
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
    run_id: UUID,
    member_id: UUID | None,
) -> dict[str, str]:
    """The proxy swaps the account's token in by the same sentinel. git reads one identity pair per
    sandbox however many hosts it clones from, so a second claimant withdraws it."""
    if grants is None or not clis:
        return {}
    granted = await grants.active_grants()
    env: dict[str, str] = {}
    for provider, cli in clis.items():
        accounts = cli_accounts(granted, provider, member_id)
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
            if cli.git is None:
                continue
            identity = _git_identity_env(granted, provider, accounts[0])
            if identity and GIT_IDENTITY_ENV & set(env):
                log(
                    "sandbox.git_identity_ambiguous",
                    provider=provider,
                    run_id=str(run_id),
                )
                return {name: value for name, value in env.items() if name not in GIT_IDENTITY_ENV}
            env.update(identity)
    return env


def _git_identity_env(granted: tuple[Grant, ...], provider: str, account_id: str) -> dict[str, str]:
    """git refuses a commit on an empty ident, and a provider links a commit to an account by author
    address, so an address from another namespace is attributed to nobody."""
    grant = next(
        (
            grant
            for grant in granted
            if grant.provider == provider and grant.account_id == account_id
        ),
        None,
    )
    if grant is None or grant.commit is None:
        return {}
    return {
        "GIT_AUTHOR_NAME": grant.commit.name,
        "GIT_AUTHOR_EMAIL": grant.commit.email,
        "GIT_COMMITTER_NAME": grant.commit.name,
        "GIT_COMMITTER_EMAIL": grant.commit.email,
    }
