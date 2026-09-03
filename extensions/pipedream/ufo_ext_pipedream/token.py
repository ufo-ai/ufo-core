"""The sandbox-side credential of a Pipedream connector whose token the deploy may hold: the
`CliCredential` the manifest attaches, and the `GrantSecret` behind it.

Pipedream releases an account's provider token only for a connector on the deploy's own OAuth
client, and the one connector that needs it in the sandbox is GitHub: `gh` reads `GH_TOKEN`, and
git smart-HTTP takes a Basic credential, not a proxied call. The sandbox holds the grant's sentinel
in that variable; the egress proxy swaps the token in on `api.github.com` and, as the password half
of `x-access-token:<token>`, on `github.com`. `gh auth git-credential` is the git helper the sandbox
is configured with for that host — it answers git's prompt from `GH_TOKEN`, so a plain `git clone`
authenticates exactly as `gh repo clone` does.

`commit_identity` is the other half of the same connector: the token clones and pushes, and a
commit between them needs an author GitHub attributes to the account that pushed it. It is read as
the consent completes and held on the connection, so no sandbox open pays a provider call for it."""

import time
from dataclasses import dataclass, field
from uuid import UUID

import httpx

from ufo.sdk.connectors import CliCredential, CommitIdentity, GitWire
from ufo_ext_pipedream import client as pipedream

TOKEN_CACHE_SECONDS = 300.0
CLI_AUTH_HEADER = "authorization"
GITHUB_APP = "github"
GITHUB_GIT = GitWire(
    host="github.com", basic_user="x-access-token", helper="!gh auth git-credential"
)
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_API_VERSION = "2022-11-28"
GITHUB_NOREPLY_HOST = "users.noreply.github.com"
GITHUB_USER_TIMEOUT_SECONDS = 10.0


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


async def commit_identity(
    spec: pipedream.ConnectorSpec, account_id: str, workspace_id: UUID
) -> CommitIdentity | None:
    """The identity this account's commits carry, read once as its consent completes.

    GitHub links a commit to an account by the author address, and the address that does it is the
    account's own `<id>+<login>@users.noreply.github.com` — the form GitHub's own web editor and
    Actions commit under. It is the only address that works for every account: `GET /user` answers
    a null `email` for anyone with email privacy on, the verified list needs a `user:email` scope
    this deploy's OAuth client does not ask for, and a member's real address does not belong in
    public history. The numeric id is what keeps attribution working for an account that enabled
    privacy after GitHub's 2017 cutover, so the login alone is not enough.

    The display name is the account's where git accepts it and the login otherwise. A display name
    is free text its owner sets, and git strips a set of punctuation from an ident before using it:
    a name that strip empties fails the commit outright (`fatal: name consists only of disallowed
    characters`), stopping every commit a turn makes rather than mis-naming one. One alphanumeric
    character is the test, rather than git's own strip table — the table is git's and would drift,
    while a name holding a letter or a digit survives every version of it — and the login GitHub
    falls back to is already bounded to characters git keeps.

    Only the connector whose token this deploy holds has an identity to read; every other account's
    token stays with Pipedream and signs no commits. Raises on a `GET /user` this cannot read: an
    identity guessed from a partial answer would attribute a member's commits to the wrong account
    or to none."""
    if spec.app != GITHUB_APP:
        return None
    client = pipedream.pipedream_client()
    token = await client.account_token(account_id, workspace_id)
    async with httpx.AsyncClient(
        timeout=GITHUB_USER_TIMEOUT_SECONDS, transport=client.transport
    ) as http:
        response = await http.get(
            GITHUB_USER_URL,
            headers={
                "authorization": f"Bearer {token}",
                "accept": "application/vnd.github+json",
                "x-github-api-version": GITHUB_API_VERSION,
            },
        )
    if response.status_code != httpx.codes.OK:
        raise pipedream.PipedreamError(
            response.status_code, f"github refused the identity read for {account_id!r}"
        )
    record = response.json()
    user_id = record.get("id") if isinstance(record, dict) else None
    login = record.get("login") if isinstance(record, dict) else None
    if not isinstance(user_id, int) or not isinstance(login, str) or not login:
        raise pipedream.PipedreamError(
            502, f"github named no account for {account_id!r}: {str(record)[:200]}"
        )
    name = record.get("name")
    return CommitIdentity(
        name=name
        if isinstance(name, str) and any(character.isalnum() for character in name)
        else login,
        email=f"{user_id}+{login}@{GITHUB_NOREPLY_HOST}",
    )
