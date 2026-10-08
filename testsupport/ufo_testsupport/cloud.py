"""The cloud API as a test serves it: the stand-ins of its services under one base, as the deploy's
façade routes them, and a bearer that needs no credential store."""

from uuid import UUID

import httpx
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Match
from starlette.types import Receive, Scope, Send

from ufo.runtime.cloud import CloudApis

TEST_BEARER = "cloud-bearer-test"


async def _test_bearer(_workspace_id: UUID) -> str:
    return TEST_BEARER


def cloud_apis_for(*apps: Starlette, base_url: str = "https://api.test") -> CloudApis:
    """`CloudApis` over every app's routes, the first app whose route matches a request answering
    it, so each stand-in keeps its own state."""

    async def routed(scope: Scope, receive: Receive, send: Send) -> None:
        matched = [
            (match, app)
            for app in apps
            for route in app.routes
            if (match := route.matches(scope)[0]) is not Match.NONE
        ]
        full = [app for match, app in matched if match is Match.FULL]
        partial = [app for _, app in matched]
        if full or partial:
            await (full or partial)[0](scope, receive, send)
            return
        await PlainTextResponse("Not Found", status_code=404)(scope, receive, send)

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=routed), base_url=base_url)
    return CloudApis(base_url=base_url, client=lambda: client, bearer_for=_test_bearer)
