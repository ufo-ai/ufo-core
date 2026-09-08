"""One report: the sandbox ingress telling a hosted site's own conversation that the site is down.

The ingress is its own process — a reverse proxy with no turn engine — so the one thing that learns
a site has stopped answering cannot admit the turn that gets it looked at. Both ends of that hop
are here, so the whole report reads in one pass: the ingress mints a report token over the very
claims the request was gated by and posts it to serve, and serve verifies it against the same
deploy secret and invokes the conversation the site belongs to. The token is the credential and the
payload at once, so this carries no bearer of its own and writes nothing down.

The waiting page a stopped site answers with reloads on a fixed interval, so a site that stays down
reports itself again on every reload. `REPORT_BUCKET_SECONDS` is what makes that one turn rather
than dozens: the bucket in the report's idempotency key advances on that period and on nothing
else, so admission's own idempotency check is the whole of this report's memory. The invocation is
not standalone, so a report landing while the agent is already repairing folds into that turn
instead of founding a second one beside it."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import Response

from ufo.harness.o11y import warn
from ufo.harness.sandbox.ingress_token import (
    SITE_REPORT_KIND,
    IngressClaims,
    IngressTokenError,
    mint_ingress_token,
    verify_ingress_token,
)
from ufo.runtime.authority import authority_from_member_id
from ufo.runtime.ext.context import AgentArchived, TurnInvoker, conversation_agent_id
from ufo.runtime.workspace import ws

SITE_REPORT_PATH = "/internal/site-not-answering"
SITE_REPORT_TTL_SECONDS = 60
"""How long a report token is good for — one hop between two pods of the same deploy, so the window
is the request's own rather than a visit's. A token that arrives late is refused and the next reload
mints a fresh one."""
SITE_REPORT_TIMEOUT_SECONDS = 5.0
REPORT_BUCKET_SECONDS = 300
"""The period the report's idempotency bucket advances on, and so the most often one site can found
a turn about itself. Ten reloads of the waiting page fall inside one bucket and admit one turn; a
site still down when the bucket turns over says so again, which is what a member watching a page
that never comes back needs to happen."""
SITE_NOT_ANSWERING_FIRE = (
    "The hosted site on port {port} in this conversation was opened and did not answer: nothing "
    "is listening on that port in this conversation's sandbox. Find out why its server stopped, "
    "start it again, and check that the site answers."
)
"""What the agent reads. It restates the whole fact — which port, which sandbox, what was observed
— so a report landing after a rollover needs no earlier transcript to act on, and it asks for the
check as well as the restart, because the member is watching a page that reloads until one of them
succeeds."""


@dataclass(frozen=True)
class SiteReporter:
    """The ingress's end of the hop. `serve_base_url` is `[connect] public_base_url`, the origin
    the frame that reads a site is served from and the one address the ingress already holds for
    serve; `None` leaves every site unreported, which is the same deploy that frames no site at all.

    A failure is logged and dropped rather than retried: the member's page is already served, the
    next reload reports again, and a retry loop here would be this process spending a member's
    request on our own fleet's uncertainty."""

    client: httpx.AsyncClient
    serve_base_url: str | None

    async def report(self, claims: IngressClaims) -> None:
        if self.serve_base_url is None or claims.shipped is not None:
            return
        now = int(datetime.now(UTC).timestamp())
        token = mint_ingress_token(
            replace(
                claims,
                expires_at=now + SITE_REPORT_TTL_SECONDS,
                shipped=None,
                framer=None,
            ),
            SITE_REPORT_KIND,
        )
        try:
            answered = await self.client.post(
                f"{self.serve_base_url.rstrip('/')}{SITE_REPORT_PATH}",
                headers={"authorization": f"Bearer {token}"},
                timeout=SITE_REPORT_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as error:
            warn(
                "ingress.site_report_failed",
                conversation_id=str(claims.conversation_id),
                error=repr(error),
            )
            return
        if answered.status_code != 204:
            warn(
                "ingress.site_report_refused",
                conversation_id=str(claims.conversation_id),
                http_status=answered.status_code,
            )


@dataclass(frozen=True)
class SiteReports:
    """Serve's end of the hop, mounted on the app the ingress posts to. The report token is the
    whole gate: it names the workspace, conversation and port the ingress had already verified a
    session for, signed with the deploy secret this process holds too, under a kind neither hop of
    a visit accepts — so a session cookie cannot be posted here and this token opens no site.

    The conversation names its own agent, and the authority is the workspace's: a site going down
    is nobody's delegated act, and the member whose browser met it may not be the one who built the
    page. A conversation this workspace does not hold answers 404 rather than founding anything —
    a shipped app page's origin is a synthetic anchor with no conversation behind it, so there is no
    agent to tell."""

    invoker_for: Callable[[UUID], TurnInvoker]
    """One invoker per workspace, the factory `serve` already holds. Named by the protocol it calls
    rather than by the job role's alias for it: nothing here runs a job."""

    def router(self) -> APIRouter:
        router = APIRouter()
        router.add_api_route(SITE_REPORT_PATH, self._report, methods=["POST"])
        return router

    async def _report(self, authorization: Annotated[str, Header()] = "") -> Response:
        now = datetime.now(UTC)
        try:
            claims = verify_ingress_token(
                authorization.removeprefix("Bearer ").strip(), now, SITE_REPORT_KIND
            )
        except IngressTokenError:
            raise HTTPException(status_code=401, detail="unauthorized") from None
        with ws(claims.workspace_id):
            agent_id = await conversation_agent_id(claims.workspace_id, claims.conversation_id)
            if agent_id is None:
                return Response(status_code=404)
            bucket = int(now.timestamp()) // REPORT_BUCKET_SECONDS
            try:
                await self.invoker_for(claims.workspace_id).invoke(
                    claims.conversation_id,
                    agent_id,
                    SITE_NOT_ANSWERING_FIRE.format(port=claims.port),
                    f"site-down:{claims.conversation_id.hex}:{claims.port}:{bucket}",
                    authority=authority_from_member_id(None),
                    holds_work_already_done=True,
                )
            except AgentArchived:
                return Response(status_code=204)
        return Response(status_code=204)
