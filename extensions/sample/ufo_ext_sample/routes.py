from uuid import UUID

from ufo.sdk.bearer import workspace_claim
from ufo.sdk.context import ExtensionContext, JsonValue, WorkspaceUnbound
from ufo.sdk.http import PlainTextResponse, Request, Response

ROUTE_PATH = "hook"
ROUTE_KEY = "route:hit"
UNBOUND_PATH = "unbound"
UNBOUND_REPLY = "No workspace is bound."


async def hook(ctx: ExtensionContext, request: Request) -> Response:
    body = (await request.body()).decode()
    minted: list[JsonValue] = [slot for slot in sorted(ctx.credentials.minted)]
    await ctx.store.put(
        ROUTE_KEY,
        {
            "body": body,
            "home_url": ctx.home_url(),
            "spend": (await ctx.spend_admitted()).outcome,
            "minted": minted,
        },
    )
    return PlainTextResponse(body)


async def unbound(ctx: ExtensionContext, request: Request) -> Response:
    try:
        workspace_id = ctx.store.workspace_id
    except WorkspaceUnbound:
        return PlainTextResponse(UNBOUND_REPLY)
    return PlainTextResponse(str(workspace_id))


def resolve_workspace(request: Request) -> UUID | None:
    """The workspace a request's bearer claims, or None to reject — the shared fleet scopes each
    request by it before the handler runs. Mirrors `ufo_ext_ufo.surface.resolve_workspace`, so the
    sample stays a valid shared-fleet conformance probe. This is the route's synchronous
    `RouteSpec.identify`."""
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return workspace_claim(token.strip())
