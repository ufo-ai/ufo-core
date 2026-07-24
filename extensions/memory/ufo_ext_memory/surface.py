"""The memory explorer: a read-only operator surface over one workspace's durable memory store.

Authorization is the shared operator web session (`resolve_operator_workspace`, `bind_operator_
session`): the request's gateway bearer must verify and carry an operator-domain email before `?ws=`
scopes it to any workspace, whose binding becomes the ambient RLS scope every read runs under. The
page is a self-contained HTML file served whole; it renders `api/memories`, a plain enumeration of
every memory_item in the bound workspace — shared and per-member, live and superseded, indexed and
still due — so an operator sees exactly what recall draws from. Surfaces receive a `SurfaceContext`,
but the explorer reads the extension's OWN `memory_item` table, so it opens the extension's
workspace-scoped transaction (`ExtensionContext.transaction`) rather than a core internal — scoped
to the same ambient workspace the resolver bound."""

from pathlib import Path

from ufo.sdk.context import CredentialAccess, ExtensionContext, ScopedStore
from ufo.sdk.http import HTMLResponse, JSONResponse, Request, Response
from ufo.sdk.operator import bind_operator_session
from ufo.sdk.surfaces import SurfaceContext, SurfaceRoute
from ufo_ext_memory.store import inventory

SURFACE_MEMORY = "memory"
EXTENSION = "memory"
APP_FILE = Path(__file__).parent / "static" / "memory.html"
APP_HTML = APP_FILE.read_text() if APP_FILE.is_file() else None


async def app_page(ctx: SurfaceContext, request: Request) -> Response:
    if APP_HTML is None:
        raise RuntimeError("memory explorer page is missing at static/memory.html")
    return HTMLResponse(APP_HTML)


async def memories(ctx: SurfaceContext, request: Request) -> Response:
    """Every memory_item in the bound workspace as JSON, newest first. The extension context is
    built only to reach its own scoped transaction; the ambient workspace the resolver bound is
    already set, so the read runs RLS-scoped and the explicit `workspace_id` predicate matches."""
    extension = ExtensionContext(
        store=ScopedStore(extension=EXTENSION),
        credentials=CredentialAccess(declared=frozenset()),
    )
    listed = await inventory(extension.transaction, ctx.workspace_id)
    return JSONResponse([item.model_dump(mode="json") for item in listed])


ROUTES = (
    SurfaceRoute(method="GET", path="", handler=app_page),
    SurfaceRoute(method="POST", path="", handler=bind_operator_session),
    SurfaceRoute(method="GET", path="api/memories", handler=memories),
)
