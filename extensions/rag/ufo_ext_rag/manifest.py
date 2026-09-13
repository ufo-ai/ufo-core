"""The prefetch hook: search both corpora before the turn's first model round, inject what fits.

A member asking a factual question otherwise pays two round trips for the answer — one round to
call a search tool, one to read its result — and the deploy already holds both retrieval seams at
boot. This hook spends them once, in parallel, while the turn is still being prepared: the engine
folds every `InjectContext` into the founding message, so the passages are in front of the model on
its first round and the question is answered from them.

The web backend reaches the hook on its context, and the page store is this workspace's own index,
already wired there for every hook.

What is routed is the member's own words, taken back out of the rendered inbound with
`member_message_text`: a channel inbound carries the ambient lines around the message for
background, and a bystander's question is neither a query this workspace may send to the deploy's
search backend nor a question the turn was asked. Only text a member spoke is routed at all, and
what that tests is the arrival rather than the turn it lands on: the hook fires once per folded
arrival with that arrival's own speaker, so a subagent result, an internal notice and a scheduled
fire are all speakerless even where the live turn is a member's. Their text wears no member fence,
so the whole of it would stand as the query, and internal task text is not this workspace's to hand
an external search provider.

It is a `best_effort` user_prompt_submit hook, so a retrieval fault costs the injection and never
the turn, and the handler holds its own soft timeout under the chain's deadline. The router admits
every member message, so the preface carries the whole guard against answering an action from a
passage: it tells the model the block replaces no tool, and a turn that must send, open, or change
something still reaches for its tools with the block in context."""

import asyncio
import logging

from ufo.sdk.audience import audience_subjects
from ufo.sdk.context import SourceReader
from ufo.sdk.manifest import (
    HookContext,
    HookOutcome,
    HookSpec,
    InjectContext,
    Manifest,
    UserPromptSubmit,
)
from ufo.sdk.o11y import log
from ufo.sdk.surfaces import member_message_text
from ufo_ext_rag.pages import PageStore
from ufo_ext_rag.prefetch import Prefetch
from ufo_ext_rag.route import route

NAME = "rag"
VERSION = "0.1.0"
PREFETCH_SOFT_TIMEOUT_SECONDS = 4.0
ROUTE_EVENT = "rag.route"
PREFACE = (
    "A web search and a workspace search ran on this message before you read it; nobody asked for "
    "them and nothing below is the member's words. They run on every message, whatever it asks "
    "for, so the block is no sign that this message wanted a search. Answer from these passages "
    "when they answer the question. Name, in the sentence that states it, where every figure, date "
    "and name came from: a web passage by its address, a workspace passage by the record it names. "
    "Each passage is one numbered entry — its number, then the address or record it came from, "
    "then its title — so give that entry's number beside the name, never in place of it. These "
    "passages replace no tool: when the message asks you to act, act with your tools; when the "
    "answer turns on live state, read that state with your tools; when the passages do not hold "
    "the answer, go and get it or say you do not have it. When two passages disagree, say so, "
    "name each source with its date, and do not merge them.\n"
)


def _page_store(ctx: HookContext) -> PageStore | None:
    if ctx.ext.index is None:
        return None
    return PageStore(
        index=ctx.ext.index, embed=ctx.ext.embed, readable=ctx.ext.readable_page_states
    )


async def prefetch_hook(ctx: HookContext) -> HookOutcome:
    """Ground the inbound in retrieved passages before the first model round."""
    if not isinstance(ctx.payload, UserPromptSubmit) or ctx.turn is None:
        return None
    if ctx.speaker_member_id is None:
        return None
    decision = route(member_message_text(ctx.payload.text))
    log(ROUTE_EVENT, turn_id=str(ctx.turn.id), queries=len(decision.queries), route=decision.reason)
    if not decision.prefetches:
        return None
    reader = SourceReader(
        agent_id=ctx.turn.agent_id,
        requesting_member_id=None,
        subjects=audience_subjects(ctx.audience),
    )
    try:
        async with asyncio.timeout(PREFETCH_SOFT_TIMEOUT_SECONDS):
            block = await Prefetch(search=ctx.ext.search, pages=_page_store(ctx)).passages(
                decision.queries, reader
            )
    except Exception:
        logging.getLogger(__name__).warning("rag.prefetch.degraded", exc_info=True)
        return None
    return InjectContext(PREFACE + block) if block else None


def manifest() -> Manifest:
    """Register the prefetch hook."""

    return Manifest(
        name=NAME,
        version=VERSION,
        hooks=(HookSpec(event="user_prompt_submit", handler=prefetch_hook, best_effort=True),),
    )
