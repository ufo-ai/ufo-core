"""The knowledge-graph extension's declared points: the graph_search tool, the graph-context hook,
and the graph-extract page-change hook.

`graph_search` traverses the typed-edge graph a bounded number of hops out from a named entity and
returns the relations it finds, each citing the source page it was derived from. The
`user_prompt_submit` hook injects the subgraph relevant to a member's message before the model runs
— best-effort under the hook deadline, mirroring memory's recall hook, so a slow or failing graph
read never denies the turn. `extract_graph` is the derivation: a `page_change` hook the core
page-change runner drives off this extension's own cursor, turning each changed source page into
nodes and typed edges, never inline on a write.
"""

import asyncio
import logging

from pydantic import BaseModel, Field

from selfhost.sdk.manifest import (
    HookContext,
    HookOutcome,
    HookSpec,
    InjectContext,
    Manifest,
    PageChangeBatch,
    UserPromptSubmit,
)
from selfhost.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult
from selfhost_ext_knowledge_graph.store import (
    DEFAULT_HOPS,
    EDGE_TYPES,
    MAX_HOPS,
    GraphExtractor,
    GraphStore,
    graph_subjects,
    render_subgraph,
    to_edge_type,
)

NAME = "knowledge_graph"
VERSION = "0.1.0"
GRAPH_HOOK_TIMEOUT_SECONDS = 4.0
GRAPH_CONTEXT_PREFIX = "Relevant graph relations:\n"
EDGE_TYPE_HELP = ", ".join(sorted(EDGE_TYPES))

logger = logging.getLogger(__name__)


class GraphSearchInput(BaseModel):
    entity: str = Field(
        description="The entity to start from — a person, company, organization, or topic name. "
        "Resolved by name to its node(s) in the graph."
    )
    hops: int = Field(
        default=DEFAULT_HOPS,
        ge=1,
        le=MAX_HOPS,
        description=f"How many relation hops to follow outward (1-{MAX_HOPS}); defaults to "
        f"{DEFAULT_HOPS}.",
    )
    edge_types: tuple[str, ...] | None = Field(
        default=None,
        description="Optional filter restricting the walk to these relation types. One or "
        f"more of: {EDGE_TYPE_HELP}.",
    )
    user_description: str | None = Field(
        default=None,
        description="Brief plain-language description shown in the activity timeline.",
    )


async def graph_search_handler(ctx: ToolContext, args: GraphSearchInput) -> ToolResult:
    """Resolve the named entity and expand its bounded neighbourhood, returning one line per
    relation with its source-page citation. An `edge_types` value outside the bounded vocabulary is
    rejected as a recoverable tool error rather than silently ignored."""
    if ctx.ext is None:
        raise RuntimeError("graph_search dispatched without its ExtensionContext")
    edge_types = frozenset(to_edge_type(kind) for kind in args.edge_types or ())
    store = GraphStore(transaction=ctx.ext.transaction, workspace_id=ctx.ext.store.workspace_id)
    subgraph = await store.traverse(
        args.entity, graph_subjects(ctx.member_id), args.hops, edge_types
    )
    lines = render_subgraph(subgraph)
    if not lines:
        return ToolResult(
            content=(TextContent(text=f"No graph relations found for {args.entity!r}."),)
        )
    header = f"Graph relations around {args.entity!r}:"
    return ToolResult(content=(TextContent(text=header + "\n" + "\n".join(lines)),))


async def graph_context_hook(ctx: HookContext) -> HookOutcome:
    """Inject the subgraph relevant to the inbound message. user_prompt_submit is gating — a raising
    or slow handler denies the turn — so this stays strictly best-effort: it runs under a soft
    timeout below the hook deadline and swallows every error, returning None on any failure or empty
    result rather than ever failing the turn."""
    if not isinstance(ctx.payload, UserPromptSubmit):
        return None
    try:
        async with asyncio.timeout(GRAPH_HOOK_TIMEOUT_SECONDS):
            store = GraphStore(
                transaction=ctx.ext.transaction, workspace_id=ctx.ext.store.workspace_id
            )
            subgraph = await store.context_for(
                ctx.payload.text, graph_subjects(ctx.member_id), DEFAULT_HOPS
            )
    except Exception:
        logger.warning("knowledge_graph.context_hook.degraded", exc_info=True)
        return None
    lines = render_subgraph(subgraph)
    return InjectContext(GRAPH_CONTEXT_PREFIX + "\n".join(lines)) if lines else None


async def extract_graph(ctx: HookContext) -> HookOutcome:
    """The `page_change` consumer: materialize graph nodes and typed edges from each replayed
    source-page change the core runner delivers. The runner owns the cursor and batch loop; this
    applies one delivered batch through the extension's jobs-way context, so Tier-B reaches
    ctx.ext.model."""
    if not isinstance(ctx.payload, PageChangeBatch):
        return None
    await GraphExtractor(
        transaction=ctx.ext.transaction,
        workspace_id=ctx.ext.store.workspace_id,
        model=ctx.ext.model,
    ).apply(ctx.payload.changes)
    return None


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        tools=(
            ToolDef(
                name="graph_search",
                description=(
                    "Traverse the knowledge graph derived from synced source pages: start from a "
                    "named entity (person, company, organization, or topic) and follow typed "
                    "relations a bounded number of hops outward. Returns the relations found, "
                    "each citing the source page it was derived from, so you answer "
                    "who-relates-to-whom questions the embedding search cannot. Optionally "
                    "restrict the walk to "
                    f"specific relation types ({EDGE_TYPE_HELP})."
                ),
                input_model=GraphSearchInput,
                handler=graph_search_handler,
            ),
        ),
        hooks=(
            HookSpec(event="user_prompt_submit", handler=graph_context_hook),
            HookSpec(event="page_change", handler=extract_graph),
        ),
    )
