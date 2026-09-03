"""The sandbox-side credential of a Pipedream connector whose token the deploy may hold: the
`CliCredential` the manifest attaches, and the `GrantSecret` behind it.

Pipedream releases an account's provider token only for a connector on the deploy's own OAuth
client, and the one connector that needs it in the sandbox is GitHub: `gh` reads `GH_TOKEN`, and
git smart-HTTP takes a Basic credential, not a proxied call. The sandbox holds the grant's sentinel
in that variable; the egress proxy swaps the token in on `api.github.com` and, as the password half
of `x-access-token:<token>`, on `github.com`. `gh auth git-credential` is the git helper the sandbox
is configured with for that host — it answers git's prompt from `GH_TOKEN`, so a plain `git clone`
authenticates exactly as `gh repo clone` does."""

import time
from dataclasses import dataclass, field
from uuid import UUID

from ufo.sdk.connectors import CliCredential, GitWire
from ufo_ext_pipedream import client as pipedream

TOKEN_CACHE_SECONDS = 300.0
CLI_AUTH_HEADER = "authorization"
GITHUB_GIT = GitWire(
    host="github.com", basic_user="x-access-token", helper="!gh auth git-credential"
)


@dataclass(frozen=True)
class PipedreamGrantSecret:
    """Reads a granted account's token from Pipedream, held for `TOKEN_CACHE_SECONDS` so the rule
    derivations one turn runs share a read rather than each asking the broker. The client is
    resolved per read so a test's transport override is honoured."""

    held: dict[str, tuple[str, float]] = field(default_factory=dict)

    async def secret(self, workspace_id: UUID, account_id: str) -> str:
        cached = self.held.get(account_id)
        now = time.monotonic()
        if cached is not None and cached[1] > now:
            return cached[0]
        token = await pipedream.pipedream_client().account_token(account_id, workspace_id)
        self.held[account_id] = (token, now + TOKEN_CACHE_SECONDS)
        return token


def cli_credential(spec: pipedream.ConnectorSpec) -> CliCredential | None:
    """The CLI credential a connector declares, or None for one whose token stays with Pipedream."""
    if spec.cli_env is None:
        return None
    return CliCredential(
        env=spec.cli_env,
        header=CLI_AUTH_HEADER,
        secret=PipedreamGrantSecret(),
        git=GITHUB_GIT,
    )
