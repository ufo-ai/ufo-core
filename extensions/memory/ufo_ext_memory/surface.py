"""The memory explorer: a read-only operator surface over one workspace's durable memory.

Authorization is the shared operator web session (`resolve_operator_workspace`, `bind_operator_
session`): the request's bearer must verify and be granted by the deploy's operator rule, which
scopes it to the workspace it claims or, under fleet reach, to the one `?ws=` names; that binding
is the workspace every read runs under. The page is a self-contained HTML file served
whole; it renders `api/memories`, a plain enumeration of the bound workspace's newest memories —
shared and per-member — so an operator sees exactly what recall draws from. Where the deploy selects
the memory service, the surface's cloud lists them there naming no subject, which answers every
subject the workspace holds. Otherwise it reads the extension's OWN `memory_item` table — live and
superseded, indexed and still due — through the extension's workspace-scoped transaction
(`ExtensionContext.transaction`) rather than a core internal, scoped to the same ambient workspace
the resolver bound."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from pydantic import AwareDatetime, BaseModel

from ufo.sdk.accounting import MEMORY_SERVICE
from ufo.sdk.context import CredentialAccess, ExtensionContext, ScopedStore
from ufo.sdk.http import HTMLResponse, JSONResponse, Request, Response
from ufo.sdk.operator import bind_operator_session
from ufo.sdk.surfaces import SurfaceContext, SurfaceRoute
from ufo_ext_memory.client import ItemClass, MemoryApi, MemoryKind
from ufo_ext_memory.store import MEMORY_INVENTORY_LIMIT, inventory

SURFACE_MEMORY = "memory"
EXTENSION = "memory"
APP_FILE = Path(__file__).parent / "static" / "memory.html"
APP_HTML = APP_FILE.read_text() if APP_FILE.is_file() else None
SECONDS_PER_DAY = 86400.0


class ServedInventoryItem(BaseModel):
    """One memory as the operator explorer reads it from the memory service: what the service
    answers, and how old its information is (`as_of`, else when it was written)."""

    subject: str
    body: str
    item_class: ItemClass
    memory_kind: MemoryKind
    confidence: int
    source_ref: str | None
    superseded_by: UUID | None
    overtaken_by: UUID | None
    created_at: AwareDatetime
    age_days: float


async def app_page(ctx: SurfaceContext, request: Request) -> Response:
    if APP_HTML is None:
        raise RuntimeError("memory explorer page is missing at static/memory.html")
    return HTMLResponse(APP_HTML)


async def memories(ctx: SurfaceContext, request: Request) -> Response:
    """The bound workspace's newest memories as JSON, newest first: the memory service's where the
    deploy selects it, else every memory_item row. The extension context is built only to reach
    its own scoped transaction; the ambient workspace the resolver bound is already set, so the
    read runs RLS-scoped and the explicit `workspace_id` predicate matches."""
    if ctx.cloud is not None and MEMORY_SERVICE in ctx.cloud.clients:
        served = await MemoryApi(cloud=ctx.cloud.bound(ctx.workspace_id)).newest(
            total=MEMORY_INVENTORY_LIMIT
        )
        now = datetime.now(UTC)
        return JSONResponse(
            [
                ServedInventoryItem(
                    subject=memory.subject,
                    body=memory.body,
                    item_class=memory.item_class,
                    memory_kind=memory.kind,
                    confidence=memory.confidence,
                    source_ref=memory.source_ref,
                    superseded_by=memory.superseded_by,
                    overtaken_by=memory.invalidated_by,
                    created_at=memory.created_at,
                    age_days=max(
                        0.0,
                        (now - (memory.as_of or memory.created_at)).total_seconds()
                        / SECONDS_PER_DAY,
                    ),
                ).model_dump(mode="json")
                for memory in served
            ]
        )
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
