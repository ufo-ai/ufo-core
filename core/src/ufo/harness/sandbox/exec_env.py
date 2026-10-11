"""The environment a sandbox open exports beside its proxy session, derived from the workspace's
credentials and grants.

Two callers open a conversation's sandbox to run a command in it: the turn opener and an off-turn
probe. Both need the same derivations — git's proxy-auth and credential-helper config, the
committer identity of the acting member's cloning account, each filled keyed slot's selected host,
the conversation's own id — so they live here rather than in either caller.

Nothing here holds a secret, and nothing here is a sentinel: the proxy session carries every
sentinel in the env it answers, one per secret its policy binds by name, and the proxy service puts
the real value in its place on the wire."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from uuid import UUID

from ufo.harness.models.catalog import ANTHROPIC_KEY_ENV, OPENAI_KEY_ENV
from ufo.harness.o11y import log, warn
from ufo.harness.sandbox.session import PROXY_SESSION_ENV_NAMES
from ufo.runtime.access.connectors import CliCredential
from ufo.runtime.access.credentials import CredentialStore, HostChoice, credential_host
from ufo.runtime.access.grants import Grant, GrantStore, cli_accounts
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
    """What an off-turn probe's sandbox open exports beside its session — `ConversationProbes`'
    environment seam, held here because the derivation reads the deploy's declared credential slots
    and the capability that consumes it cannot reach them.

    It is the turn opener's environment: the conversation id, git's config channel, each filled
    keyed slot's selected host, and the committer identity of the acting member's cloning account.
    What a probe may reach is its session's policy, compiled for a scope that is not running, so it
    binds no `ufo/models` and an unattended exec cannot spend the deployment's model key."""

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
            **await git_identity_env(self.grants, self.clis, probe_id, member_id),
            **await keyed_host_env(self.credentials, self.slots, workspace_id),
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


CA_BUNDLE_ENV_NAMES = frozenset(
    {"SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "NODE_EXTRA_CA_CERTS"}
)


def sandbox_exported_env(clis: Mapping[str, CliCredential]) -> frozenset[str]:
    """Every sandbox variable an open exports beside the deploy's declared slots, whatever the
    carrier: a proxy session's proxy variables and the model keys its bindings mint sentinels for,
    the CA paths an off-cluster carrier points at the proxy's CA, git's config channel including
    each connector CLI's credential helper, the conversation id, and the committer identity a
    cloning grant sets.

    One sandbox variable carries one value and the later export wins the merge, so this namespace
    is claimed beside the deploy's declared slots: a workspace declaring a slot on one of these
    names would replace a value the open exported — a model-key sentinel the session binds, and
    every model call from the sandbox would carry a value the proxy swaps nothing for."""
    return frozenset(
        {
            CONVERSATION_ID_ENV,
            TOOL_BRIDGE_URL_ENV,
            ANTHROPIC_KEY_ENV,
            OPENAI_KEY_ENV,
            *PROXY_SESSION_ENV_NAMES,
            *CA_BUNDLE_ENV_NAMES,
            *GIT_IDENTITY_ENV,
            *_git_config_env((*GIT_PROXY_AUTH_CONFIG, *cli_git_config(clis))),
        }
    )


async def keyed_host_env(
    credentials: CredentialStore | None,
    slots: WorkspaceSlots,
    workspace_id: UUID,
) -> dict[str, str]:
    """Each filled keyed slot whose declaration lets the member select its host exports the host it
    resolves to under the choice's `env`, so the agent addresses the site its key belongs to. The
    key itself reaches the sandbox only as the sentinel its session binds. Which slots hold a value
    is one read; no value is read, and a slot whose selection will not resolve is withheld alone."""
    if credentials is None:
        return {}
    stored = await credentials.stored_slots(workspace_id)
    env: dict[str, str] = {}
    for slot in await slots.all(workspace_id):
        target = slot.injection
        if (
            target is None
            or slot.name not in stored
            or not isinstance(target.host, HostChoice)
            or target.host.env is None
        ):
            continue
        try:
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
        env[target.host.env] = host
    return env


async def git_identity_env(
    grants: GrantStore | None,
    clis: Mapping[str, CliCredential],
    run_id: UUID,
    member_id: UUID | None,
) -> dict[str, str]:
    """The committer identity of the one account the acting member's tier resolves to for a CLI that
    clones, the tier the session policy binds that CLI's connection from. A tier holding several
    accounts exports nothing for that CLI, and git reads one identity pair per sandbox however many
    hosts it clones from, so a second claimant withdraws it."""
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
        if not accounts or cli.git is None:
            continue
        identity = _git_identity_env(granted, provider, accounts[0])
        if identity and GIT_IDENTITY_ENV & set(env):
            log("sandbox.git_identity_ambiguous", provider=provider, run_id=str(run_id))
            return {}
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
