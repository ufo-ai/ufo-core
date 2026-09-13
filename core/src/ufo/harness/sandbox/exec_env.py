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
from ufo.runtime.access.grants import Grant, GrantStore, grant_sentinel, scoped_cli_accounts
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

    The probe's immutable connection capabilities select connector CLI sentinels exactly as a
    turn's re-authorization does."""

    grants: GrantStore | None = None
    clis: Mapping[str, CliCredential] = field(default_factory=dict)
    credentials: CredentialStore | None = None
    slots: WorkspaceSlots = field(default_factory=WorkspaceSlots)

    async def exports(
        self,
        conversation_id: UUID,
        probe_id: UUID,
        connections: tuple[UUID, ...] = (),
    ) -> dict[str, str]:
        workspace_id = ws_current().workspace_id
        return {
            CONVERSATION_ID_ENV: str(conversation_id),
            **_git_config_env((*GIT_PROXY_AUTH_CONFIG, *cli_git_config(self.clis))),
            **await _grant_cli_env(self.grants, self.clis, probe_id, connections),
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
    """Each keyed provider this workspace has a secret for — the deploy's declared slots and the
    ones this workspace declares for itself — as the sandbox sees it: the declared env
    var set to the slot's sentinel — never the secret, which the egress proxy swaps in on the wire —
    and the resolved provider host, so the agent's own client authenticates and addresses the right
    region without holding or guessing either. A slot with nothing stored exports nothing, so the
    agent finds no half-usable variable for a provider the member has not keyed yet. A selection the
    declaration does not offer exports nothing and warns here as well as at the proxy, because the
    two roles withhold at different moments — the export when the sandbox opens, the egress when a
    request is made — and the member would otherwise see a variable that never appeared.

    A slot whose stored value this deploy cannot read withholds its own variables and warns. Every
    sandbox open runs this, so a fault escaping here would fail the open of a turn that touches no
    keyed provider at all — one unreadable value costs its provider, never the turn."""
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
    connections: tuple[UUID, ...],
) -> dict[str, str]:
    """Each connector-declared CLI env var whose provider this process may use from its exact
    connection capabilities, set to that grant's sentinel so the CLI inside the sandbox
    authenticates and the proxy swaps the account's token in by the same sentinel. A static env
    var names no account, so two accounts cannot be disambiguated per request: rather than pick —
    `connector_account` fails loud on the same ambiguity — the export is skipped and logged
    against `run_id`, whichever run this open serves, so the CLI fails visibly to authenticate
    instead of acting as an unintended account. An exact connection scope admits only its listed
    accounts and is independent of their owners; a private capability outranks every shared one.

    One sandbox has one git identity, because git reads one pair of variables however many hosts a
    turn clones from. Two CLIs claiming it would otherwise resolve by dict order, so the second
    claimant withdraws the identity entirely and logs: a turn that cannot commit says so, where a
    turn committing as whichever provider happened to be first says nothing."""
    if grants is None or not clis:
        return {}
    granted = await grants.active_grants()
    env: dict[str, str] = {}
    for provider, cli in clis.items():
        accounts = scoped_cli_accounts(granted, provider, connections)
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
    """The identity git stamps a commit with, taken from the account the sandbox's clone and push
    authenticate as. A fresh container configures none, so a turn that clones a repository and
    pushes a branch cannot commit at all — git refuses on an empty ident — and one configured by
    hand attributes the work to whatever the turn invented.

    The identity is the connected account's own, recorded when its consent completed: a provider
    links a commit to an account by the author address, so an address from any other namespace —
    the member's ufo login among them — pushes as the account and is attributed to nobody, while
    writing that address into public history for good. A connection carrying none exports none, so
    git refuses the commit and says so rather than attributing the work to the wrong identity.

    Only a git-cloning grant exports it, so a turn commits as the account it pushes as or under no
    identity at all."""
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
