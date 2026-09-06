"""A tool that names a flag is offered by it: where the flag reads off for the bound workspace, or
nothing answers, the tool leaves the catalog and its action leaves the grants — withheld, never
refused. An unflagged tool never consults the flag service."""

from uuid import uuid4

from openfeature.provider.in_memory_provider import InMemoryFlag, InMemoryProvider
from pydantic import BaseModel

from ufo.flags import SERVED_FALSE, SERVED_TRUE, init_flags
from ufo.host.assemble import flags_reading_off, granted_without_flagged
from ufo.runtime.objects import BoundAction
from ufo.runtime.tools.context import TextContent, ToolContext, ToolResult
from ufo.runtime.tools.registry import ObjectBinding, ToolDef
from ufo.runtime.workspace import ws

FLAG = "enable-example-app"


class _Input(BaseModel):
    pass


async def _handler(ctx: ToolContext, args: _Input) -> ToolResult:
    return ToolResult(content=(TextContent(text="ok"),))


PLAIN = ToolDef(name="plain", description="always offered", input_model=_Input, handler=_handler)
FLAGGED = ToolDef(
    name="flagged", description="offered by a flag", input_model=_Input, handler=_handler, flag=FLAG
)
ACTION = ToolDef(
    name="act",
    description="an action offered by the same flag",
    input_model=_Input,
    handler=_handler,
    bound=ObjectBinding(kind="example", binding="collection"),
    flag=FLAG,
)
ACTIONS = {"example": {"act": BoundAction(action=ACTION, extension="example", context=None)}}


def _serving(value: str) -> InMemoryProvider:
    return InMemoryProvider({FLAG: InMemoryFlag(default_variant="set", variants={"set": value})})


async def test_a_flag_reading_off_withholds_the_tool_and_its_action() -> None:
    granted = frozenset({ACTION.canonical_id, "action:other:keep"})
    try:
        init_flags(_serving(SERVED_FALSE))
        with ws(uuid4()):
            off = await flags_reading_off((PLAIN, FLAGGED), ACTIONS)
        init_flags(_serving(SERVED_TRUE))
        with ws(uuid4()):
            on = await flags_reading_off((PLAIN, FLAGGED), ACTIONS)
        init_flags(InMemoryProvider({}))
        with ws(uuid4()):
            unanswered = await flags_reading_off((PLAIN, FLAGGED), ACTIONS)
    finally:
        init_flags(InMemoryProvider({}))

    assert off == {FLAG}
    assert on == frozenset()
    assert unanswered == {FLAG}
    assert tuple(tool.name for tool in (PLAIN, FLAGGED) if tool.flag not in off) == ("plain",)
    assert granted_without_flagged(granted, ACTIONS, off) == {"action:other:keep"}
    assert granted_without_flagged(granted, ACTIONS, on) == granted


async def test_an_unflagged_catalog_reads_no_flag() -> None:
    with ws(uuid4()):
        assert await flags_reading_off((PLAIN,), {}) == frozenset()
