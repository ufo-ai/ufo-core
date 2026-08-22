"""The operator web session shared by operator-only surfaces (the session debugger, the memory
explorer): verify the gateway bearer the request carries — the Authorization header, the session
cookie, or the one POST that opens a session, never a query parameter, so the long-lived credential
stays out of URLs, access logs, and browser history — and resolve it to the workspace the request is
scoped to under the operator-domain gate. One cookie serves every operator surface, so an operator
authenticates once and browses all of them.

Beside the session sits `FleetDirectory`, the index of what an operator may then pick: the
workspaces this deploy serves and the threads that moved most recently across all of them.

It lives in core, re-exported through `ufo.sdk.operator`, because two sibling extensions (the
debugger and the memory explorer) share it and an extension imports only `ufo.sdk` — never another
extension — so this session can live in neither. Verification stays the caller's: `verified_claims`
takes the token and resolves `UFO_TOKEN_SECRET` itself, so this module never holds the signing
key."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_DNS, UUID, uuid5

import sqlalchemy as sa
from pydantic import BaseModel, field_validator

from ufo.auth.bearer import LOGIN_PATH, verified_claims
from ufo.db import owner_tx, workspace_tx
from ufo.ext.surface import OPERATOR_EMAIL_DOMAIN, SurfaceAuth, SurfaceContext
from ufo.schema import tables
from ufo.schema.records import SUBAGENT_SURFACE
from ufo.sdk.http import JSONResponse, RedirectResponse, Request, Response, set_session_cookie
from ufo.seats import email_domain, workspace_by_domain, workspace_domain
from ufo.workspace import ws

OPERATOR_COOKIE = "ufo_debug"
TOKEN_FIELD = "token"
OPERATOR_PAGE_PATH = re.compile(r"/surface/[^/]+/?")
# The sign-in page posts the minted bearer to the member portal unless it is asked for the operator
# surfaces by name, so the bounce below carries the ask: the click that wanted one comes back to it,
# and an ordinary sign-in on the same page is never diverted there.
OPERATOR_LOGIN_PATH = f"{LOGIN_PATH}?debug=1"


async def operator_claims(request: Request) -> tuple[str, str] | None:
    """The `(workspace, email)` the request's bearer proves: the Authorization header, then the
    session cookie, then — for the one POST that opens a session — the form body. Never a query
    parameter. Each fallback keys on the previous credential failing to RESOLVE, not merely being
    absent, so an operator whose cookie outlived its bearer's expiry recovers by posting a fresh
    token instead of being locked behind a stale cookie they can neither read nor delete."""
    scheme, _, header_token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer":
        claims = _candidate_claims(header_token)
        if claims is not None:
            return claims
    claims = _candidate_claims(request.cookies.get(OPERATOR_COOKIE, ""))
    if claims is not None:
        return claims
    if request.method == "POST":
        posted = (await request.form()).get(TOKEN_FIELD, "")
        if isinstance(posted, str):
            return _candidate_claims(posted)
    return None


def _candidate_claims(candidate: str) -> tuple[str, str] | None:
    token = candidate.strip()
    return verified_claims(token) if token else None


async def resolve_operator_workspace(
    request: Request, _auth: SurfaceAuth
) -> UUID | Response | None:
    """The workspace this operator request is scoped to, or None to reject. The domain gate is the
    whole authorization: the verified bearer's email domain must equal the operator's domain before
    `?ws=` may re-scope the request to any workspace in the fleet — a raw workspace UUID, or the
    domain its members are seated at, which `workspace_by_domain` resolves to that workspace's own
    id so an address the fleet directory prints reaches the workspace the directory lists it as.
    A domain no workspace is seated at falls to `uuid5(NAMESPACE_DNS, domain)`, the id a workspace
    provisioned for that domain is created under, so a tenant reached before anyone has onboarded
    still resolves. Without `?ws=` the bearer's own workspace claim is the scope.

    A GET of the surface page carrying no credential that resolves redirects to the deploy's one
    sign-in page under the ask that sends the minted bearer back here, so a link into an operator
    surface — the `debug` footer of a Slack turn, a bookmark — leads to the card that mints one
    instead of dead-ending on `unauthorized`, and reaches this surface rather than the member
    portal. One cookie serves every operator surface, so the session a bounce from any of them
    opens is the session all of them read. The surface's own API routes still reject, so a fetch
    fails loudly rather than reading a page."""
    claims = await operator_claims(request)
    if claims is None:
        if request.method == "GET" and OPERATOR_PAGE_PATH.fullmatch(request.url.path):
            return RedirectResponse(OPERATOR_LOGIN_PATH, status_code=303)
        return None
    claimed_workspace, email = claims
    if email_domain(email) != OPERATOR_EMAIL_DOMAIN:
        return None
    target = request.query_params.get("ws", "").strip()
    if not target:
        try:
            return UUID(claimed_workspace)
        except ValueError:
            return None
    try:
        return UUID(target)
    except ValueError:
        pass
    async with owner_tx() as connection:
        seated = await workspace_by_domain(connection, target)
    return seated or uuid5(NAMESPACE_DNS, target.lower())


async def bind_operator_session(ctx: SurfaceContext, request: Request) -> Response:
    """Open a session: land the POSTed bearer as the httponly session cookie and redirect into the
    page. The token crosses only in the form body — never a URL — so access logs and browser
    history hold no credential; the identify resolver has already verified this exact form token
    before the handler runs. The cookie is `lax`, not `strict`, because arrival IS a cross-site
    navigation (the apex login page posts here, a Slack footer links here) and the redirected GET
    must already carry it."""
    posted = (await request.form()).get(TOKEN_FIELD, "")
    if not isinstance(posted, str) or not posted.strip():
        return JSONResponse({"error": "token form field is required"}, status_code=400)
    response = RedirectResponse(str(request.url), status_code=303)
    set_session_cookie(response, OPERATOR_COOKIE, posted.strip(), samesite="lax")
    return response


FLEET_THREAD_LIMIT = 50


class FleetWorkspace(BaseModel):
    """One workspace as the operator's index lists it: the domain that addresses it, how much is
    in it, and when it last did anything."""

    workspace_id: UUID
    domain: str | None
    members: int
    conversations: int
    last_turn_at: datetime | None

    @field_validator("last_turn_at")
    @classmethod
    def _aware_utc(cls, value: datetime | None) -> datetime | None:
        return value if value is None or value.tzinfo is not None else value.replace(tzinfo=UTC)


class FleetThread(BaseModel):
    """One recently active conversation, carrying the workspace it belongs to so a click can
    re-scope and open it in the same step."""

    workspace_id: UUID
    domain: str | None
    conversation_id: UUID
    surface: str
    queue_key: str
    title: str | None
    turn_count: int
    last_turn_at: datetime

    @field_validator("last_turn_at")
    @classmethod
    def _aware_utc(cls, value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class FleetListing(BaseModel):
    workspaces: tuple[FleetWorkspace, ...]
    threads: tuple[FleetThread, ...]


@dataclass(frozen=True)
class FleetDirectory:
    """The operator's index of the deploy: every workspace it serves, newest activity first, and
    the most recently active threads across all of them. It grants no reach `?ws=` does not already
    have — an operator bearer already re-scopes this surface to any workspace in the fleet — it
    only removes having to know a domain before typing it.

    Crossing workspaces means `owner_tx`, whose contract admits identifiers and nothing else, so
    the two passes below split on exactly that line: the owner pass reads workspace and
    conversation ids plus the activity timestamps that order them, and every word an operator
    reads — the domain, the surface, the queue key, the title — comes from the scoped pass, re-bound
    under `with ws(...)` and read through RLS like any other workspace read. The scoped pass costs
    one transaction per workspace, which is right for a deploy holding tens of them and wrong for
    one holding thousands.

    Only rooted turns count. A subagent runs in a conversation of its own, so a busy workspace's
    fan-out otherwise fills the index and hides every other workspace's threads behind it —
    measured on the live fleet, 35 of 50 slots. `parent_turn_id is null` separates the two exactly
    (every subagent turn carries a parent, no member-facing turn does), and it is the filter the
    owner pass can apply, holding only identifiers; the conversation count beside it drops the same
    runs by the surface the portal's own listing excludes them by."""

    threads: int = FLEET_THREAD_LIMIT

    async def read(self) -> FleetListing:
        activity, recent = await self._enumerate()
        wanted: dict[UUID, list[UUID]] = {}
        for row in recent:
            wanted.setdefault(row.workspace_id, []).append(row.conversation_id)
        listed: list[FleetWorkspace] = []
        opened: dict[UUID, sa.Row[Any]] = {}
        for row in activity:
            with ws(row.id):
                workspace, conversations = await self._scoped(
                    row.id, row.last_turn_at, wanted.get(row.id, [])
                )
            listed.append(workspace)
            opened.update(conversations)
        named = {workspace.workspace_id: workspace.domain for workspace in listed}
        return FleetListing(
            workspaces=tuple(listed),
            threads=tuple(
                FleetThread(
                    workspace_id=row.workspace_id,
                    domain=named.get(row.workspace_id),
                    conversation_id=row.conversation_id,
                    surface=opened[row.conversation_id].surface,
                    queue_key=opened[row.conversation_id].queue_key,
                    title=opened[row.conversation_id].title,
                    turn_count=row.turn_count,
                    last_turn_at=row.last_turn_at,
                )
                for row in recent
                if row.conversation_id in opened
            ),
        )

    async def _enumerate(self) -> tuple[Sequence[sa.Row[Any]], Sequence[sa.Row[Any]]]:
        """The owner pass: which workspaces exist and which conversations moved last. Identifiers
        and their ordering keys only — a workspace with no turn yet still lists, so a deploy's
        newest tenant is visible the moment it is created."""
        per_workspace = (
            sa.select(
                tables.turn.c.workspace_id,
                sa.func.max(tables.turn.c.updated_at).label("last_turn_at"),
            )
            .where(tables.turn.c.parent_turn_id.is_(None))
            .group_by(tables.turn.c.workspace_id)
            .subquery()
        )
        workspaces = (
            sa.select(tables.workspace.c.id, per_workspace.c.last_turn_at)
            .select_from(
                tables.workspace.outerjoin(
                    per_workspace, per_workspace.c.workspace_id == tables.workspace.c.id
                )
            )
            .order_by(per_workspace.c.last_turn_at.desc().nulls_last(), tables.workspace.c.id)
        )
        recent = (
            sa.select(
                tables.turn.c.workspace_id,
                tables.turn.c.conversation_id,
                sa.func.count().label("turn_count"),
                sa.func.max(tables.turn.c.updated_at).label("last_turn_at"),
            )
            .where(tables.turn.c.parent_turn_id.is_(None))
            .group_by(tables.turn.c.workspace_id, tables.turn.c.conversation_id)
            .order_by(sa.func.max(tables.turn.c.updated_at).desc())
            .limit(self.threads)
        )
        async with owner_tx() as connection:
            return (
                (await connection.execute(workspaces)).all(),
                (await connection.execute(recent)).all(),
            )

    async def _scoped(
        self, workspace_id: UUID, last_turn_at: datetime | None, conversation_ids: Sequence[UUID]
    ) -> tuple[FleetWorkspace, dict[UUID, sa.Row[Any]]]:
        """The scoped pass for one re-bound workspace: everything the index renders in words. RLS
        is pinned to this workspace for the whole transaction, so these reads are no broader than
        the ones the surface already serves under `?ws=`."""
        async with workspace_tx() as connection:
            domain = await workspace_domain(connection, workspace_id)
            members = (
                await connection.execute(
                    sa.select(sa.func.count())
                    .select_from(tables.member)
                    .where(tables.member.c.workspace_id == workspace_id)
                )
            ).scalar_one()
            conversations = (
                await connection.execute(
                    sa.select(sa.func.count())
                    .select_from(tables.conversation)
                    .where(
                        tables.conversation.c.workspace_id == workspace_id,
                        tables.conversation.c.surface != SUBAGENT_SURFACE,
                    )
                )
            ).scalar_one()
            opened = (
                (
                    await connection.execute(
                        sa.select(
                            tables.conversation.c.id,
                            tables.conversation.c.surface,
                            tables.conversation.c.queue_key,
                            tables.conversation.c.title,
                        ).where(tables.conversation.c.id.in_(conversation_ids))
                    )
                ).all()
                if conversation_ids
                else ()
            )
        return (
            FleetWorkspace(
                workspace_id=workspace_id,
                domain=domain,
                members=members,
                conversations=conversations,
                last_turn_at=last_turn_at,
            ),
            {row.id: row for row in opened},
        )
