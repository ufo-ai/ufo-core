"""Connecting the workspace's GitHub: an admin proves the installation is reachable.

An installation id is a small integer and this deploy's App key mints a token for any installation
of the App, so the id can never be taken on trust. A seal naming the workspace is not enough on its
own either: an admin who can mint one could hand-craft the return leg carrying another
organization's id. What settles it is GitHub. The install returns with an authorization code, the
code exchanges for a token that is the *member's own*, and `GET /user/installations` under that
token names every ufo installation that member can actually reach. The id on the query string is
only a selector into that answer — a workspace binds an installation its member holds, or nothing.

`connect_github` mints the install link carrying the sealed state; GitHub returns the member to
`/ext/coding/installed`, which does the exchange and binds. What lands in the slot is a seal over
`(workspace, installation)`, so an id typed into the slot through the ordinary credential prompt
never opens when a token is minted against it."""

import os
from dataclasses import dataclass
from uuid import UUID

import httpx
from pydantic import BaseModel, Field

from ufo.sdk.context import ExtensionContext
from ufo.sdk.credentials import authorized_slot_workspace
from ufo.sdk.http import Request, Response
from ufo.sdk.tools import TextContent, ToolContext, ToolResult

APP_SLUG = "flyingobject-ai-ufo"
GIT_INSTALLATION_SLOT = "github_app_installation"
INSTALL_PAYLOAD = "github-app-install"
ROUTE_PATH = "installed"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
INSTALLATIONS_URL = "https://api.github.com/user/installations"
INSTALL_URL = f"https://github.com/apps/{APP_SLUG}/installations/new"
HTTP_TIMEOUT_SECONDS = 10
JSON_HEADERS = {"Accept": "application/json"}


class ConnectGitHubInput(BaseModel):
    """The connection is for the speaking admin's workspace, so it takes no target."""

    user_description: str = Field(
        description="That you are getting their GitHub hooked up, in plain language for the "
        "activity timeline."
    )


async def connect_github(ctx: ToolContext, args: ConnectGitHubInput) -> ToolResult:
    """Hand an admin the link that installs the ufo GitHub App on their organization. The link
    carries a sealed state naming this workspace and this slot, which is what lets the return leg
    resolve a workspace from a browser redirect that has no turn behind it.

    Admin-only, because the installation this binds is workspace-wide."""
    from ufo_ext_coding.manifest import github_app_id

    if not await ctx.speaker_is_admin():
        raise ValueError("only a workspace admin can connect GitHub")
    if github_app_id() is None:
        raise RuntimeError("this deploy has no GitHub App configured")
    sealed = await ctx.begin_credential_authorization(GIT_INSTALLATION_SLOT, INSTALL_PAYLOAD)
    return ToolResult(
        content=(
            TextContent(
                text=(
                    f"Install the ufo GitHub App to connect this workspace: {INSTALL_URL}"
                    f"?state={sealed}\n\nChoose the organization and which repositories it may "
                    "reach. GitHub returns you here when it is done, and the connection finishes "
                    "itself — the link is single-purpose and expires shortly."
                )
            ),
        )
    )


def install_workspace(request: Request) -> UUID | None:
    """The workspace this return leg belongs to, read from the state ufo sealed when it minted the
    install link — the only thing in the request that this deploy authored."""
    return authorized_slot_workspace(
        request.query_params.get("state", ""), GIT_INSTALLATION_SLOT, INSTALL_PAYLOAD
    )


class GitHubAuthorizationError(RuntimeError):
    """GitHub declined the authorization code, so no member identity backs this return leg."""


@dataclass(frozen=True)
class GitHubInstallExchange:
    """The return leg's one question: does the member who just authorized actually reach the
    installation being claimed? Answered by exchanging their code for their own user token and
    reading `GET /user/installations` under it, which is GitHub's own account of what that member
    can see. Holds the deploy's App identity, so the check cannot be run without it."""

    client_id: str
    client_secret: str
    app_id: str
    transport: httpx.AsyncBaseTransport | None = None

    async def reaches(self, code: str, installation_id: str) -> bool:
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT_SECONDS, transport=self.transport
        ) as client:
            granted = (
                await client.post(
                    ACCESS_TOKEN_URL,
                    headers=JSON_HEADERS,
                    data={
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "code": code,
                    },
                )
            ).json()
            token = granted.get("access_token")
            if not token:
                raise GitHubAuthorizationError(str(granted.get("error", "unknown")))
            listed = (
                await client.get(
                    INSTALLATIONS_URL, headers={**JSON_HEADERS, "Authorization": f"Bearer {token}"}
                )
            ).json()
        return installation_id in {
            str(entry.get("id"))
            for entry in listed.get("installations", [])
            if str(entry.get("app_id")) == self.app_id
        }


def install_exchange() -> GitHubInstallExchange:
    """The deploy's App identity for the return leg, read at call time so a test can stand in."""
    from ufo_ext_coding.manifest import GIT_APP_CLIENT_ID_ENV, GIT_APP_SECRET_ENV, github_app_id

    app_id = github_app_id()
    if app_id is None:
        raise RuntimeError("this deploy has no GitHub App configured")
    return GitHubInstallExchange(
        client_id=os.environ[GIT_APP_CLIENT_ID_ENV],
        client_secret=os.environ[GIT_APP_SECRET_ENV],
        app_id=app_id,
    )


async def github_installed(ctx: ExtensionContext, request: Request) -> Response:
    """GitHub's return leg. The state named the workspace — that is how this request found one at
    all — and the exchange decides the rest: bind only an installation the authorizing member
    reaches. The claimed id is never trusted on its own, because the same admin who obtained a
    valid link could otherwise return with an id belonging to an organization they have nothing to
    do with."""
    code = request.query_params.get("code", "")
    installation_id = request.query_params.get("installation_id", "")
    if not code or not installation_id:
        return _page(
            "GitHub returned without an authorization. Ask ufo to connect GitHub again, and be "
            "sure to authorize as well as install.",
            400,
        )
    try:
        reaches = await install_exchange().reaches(code, installation_id)
    except GitHubAuthorizationError:
        return _page("GitHub declined the authorization — ask ufo to connect again.", 502)
    if not reaches:
        return _page(
            "That installation does not belong to the account that just authorized, so it was not "
            "connected.",
            403,
        )
    await ctx.credentials.bind_installation(GIT_INSTALLATION_SLOT, installation_id)
    return _page(
        "GitHub is connected. The agent can now clone and push the repositories this installation "
        "grants — you can close this tab and return to the conversation.",
        200,
    )


def _page(message: str, status: int) -> Response:
    return Response(
        status_code=status,
        media_type="text/html",
        content=(
            "<!doctype html><html><body style='font-family:system-ui;padding:3rem;max-width:34rem'>"
            f"<p>{message}</p></body></html>"
        ),
    )
