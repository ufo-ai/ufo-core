"""The two tools this boundary offers, defined by the strategy that implements them.

`new_context` is how a model asks for a fresh window; the rollover flow reads the request off the
recorded window — that call and its result — once the tool batch commits, so the handler only
acknowledges and a crash-recovery replay decides the same reset without it running again.
`search_history` reads the history file the boundary appends the outgoing window to: one awk pass
inside the sandbox, so nothing but a page of hits crosses the carrier.

`cap_checklist` and `history_filename` live here beside them because both the acknowledgement and
the boundary itself apply the same rules — one cap on a carried checklist, one name for the
conversation's history file."""

import json
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ufo.sdk.tools import ContextControl, TextContent, ToolContext, ToolDef, ToolResult

NEW_CONTEXT_TOOL = "new_context"
SEARCH_HISTORY_TOOL = "search_history"


def cap_checklist(lines: tuple[str, ...], cap: int) -> tuple[str, ...]:
    """Whole lines from the front until `cap` characters; a first line longer than the cap is cut
    to it. The one rule the `new_context` acknowledgement and the boundary both apply."""
    kept: list[str] = []
    spent = 0
    for line in lines:
        if spent + len(line) > cap:
            if not kept:
                kept.append(line[:cap])
            break
        kept.append(line)
        spent += len(line)
    return tuple(kept)


def history_filename(conversation_id: UUID) -> str:
    """The conversation's history file inside its sandbox run root. A subagent runs in the sandbox
    of the conversation that spawned it and rolls over on its own, so the file is named per
    conversation, never per sandbox."""
    return f"history-{conversation_id.hex}.jsonl"


def _require_context(ctx: ToolContext) -> ContextControl:
    if ctx.context is None:
        raise RuntimeError("this context has no window of its own")
    return ctx.context


class NewContextInput(BaseModel):
    handoff: str = Field(
        default="",
        description="What the fresh window opens with: what is done, what is next, and the exact "
        "paths, ids, and values the next window needs. Trimmed at 20000 characters.",
    )
    checklist: tuple[str, ...] = Field(
        default=(),
        description="Lines carried across the reset verbatim — the standing checklist the work is "
        "measured against. They survive every later reset until you replace them. Whole lines are "
        "kept from the front up to 8000 characters.",
    )


class SearchHistoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(
        min_length=1, description="Phrase to find, matched literally and case-insensitively."
    )
    page: int = Field(default=1, ge=1, description="Page of hits, newest first, twenty a page.")


HISTORY_SEARCH_SCRIPT = (
    '[ -f "$1" ] || { printf \'no history file at %s\\n\' "$1"; exit 0; }\n'
    'awk -v needle="$2" -v page="$3" -v size=20 -v width=400 \'\n'
    "BEGIN { needle = tolower(needle); n = 0 }\n"
    "{ low = tolower($0); p = index(low, needle)\n"
    "  if (p > 0) { n++; ln[n] = NR; pos[n] = p; txt[n] = $0 } }\n"
    "END {\n"
    "  pages = int((n + size - 1) / size); if (pages < 1) pages = 1\n"
    "  if (page < 1) page = 1\n"
    '  printf "matches: %d  page %d of %d  newest first\\n", n, page, pages\n'
    "  start = n - (page - 1) * size\n"
    "  stop = start - size + 1; if (stop < 1) stop = 1\n"
    "  for (i = start; i >= stop; i--) {\n"
    "    s = pos[i] - int(width / 2); if (s < 1) s = 1\n"
    "    piece = substr(txt[i], s, width)\n"
    '    head = (s > 1 ? "..." : ""); tail = (s + width <= length(txt[i]) ? "..." : "")\n'
    '    printf "[line %d] %s%s%s\\n", ln[i], head, piece, tail\n'
    "  }\n"
    '  if (page < pages) printf "next page: %d\\n", page + 1\n'
    '}\' "$1"\n'
)


async def new_context_handler(ctx: ToolContext, args: NewContextInput) -> ToolResult:
    """Acknowledge a reset request. The request is the call and this result in the recorded
    window: the rollover flow reads it there once the batch commits, so every result in flight
    still reaches the journal and a crash-recovery replay decides the same reset without this
    handler running again."""
    control = _require_context(ctx)
    handoff = args.handoff.strip()
    kept = min(len(handoff), control.handoff_cap())
    checklist = cap_checklist(args.checklist, control.checklist_cap())
    return ToolResult(
        content=(
            TextContent(
                text=json.dumps(
                    {
                        "reset": "after this tool batch commits",
                        "handoff_chars_kept": kept,
                        "handoff_trimmed": kept < len(handoff),
                        "checklist_lines_kept": len(checklist),
                        "checklist_trimmed": checklist != tuple(args.checklist),
                    }
                )
            ),
        )
    )


async def search_history_handler(ctx: ToolContext, args: SearchHistoryInput) -> ToolResult:
    """Search the sandbox history file in place: one awk pass in the sandbox returns a page of
    hits, newest first, each with its line number, so nothing but the page crosses the carrier."""
    path = await ctx.sandbox.runtime_path(history_filename(ctx.turn.conversation_id))
    result = await ctx.sandbox.sh(HISTORY_SEARCH_SCRIPT, path, args.query.strip(), str(args.page))
    if result.exit_code != 0:
        raise RuntimeError(
            f"history search failed (exit {result.exit_code}): "
            f"{(result.stderr or result.stdout).strip()[:200]}"
        )
    return ToolResult(content=(TextContent(text=result.stdout),))


ROLLOVER_TOOLS: tuple[ToolDef, ...] = (
    ToolDef(
        name=NEW_CONTEXT_TOOL,
        description=(
            "Reset your context window at a clean point of your choosing. The reset happens after "
            "the current tool batch commits, so no result in flight is lost. Pass a `handoff` "
            "naming what is done, what is next, and the exact paths, ids, and values the fresh "
            "window needs — it becomes that window's first state. Pass a `checklist` to carry "
            "lines across verbatim. Nothing is summarized and nothing is deleted: the whole "
            "history stays in the sandbox history file the recovery record names."
        ),
        input_model=NewContextInput,
        handler=new_context_handler,
        subagent_default=True,
        binds_member_authority=False,
    ),
    ToolDef(
        name=SEARCH_HISTORY_TOOL,
        description=(
            "Search this conversation's history file, everything that left your window at a "
            "reset, for a phrase: literal, case-insensitive, newest first, twenty hits a page, "
            "each tagged with its line number and the text around the match. Read a whole line "
            "with bash: sed -n 'Np' on the file the recovery record names."
        ),
        input_model=SearchHistoryInput,
        handler=search_history_handler,
        parallel_safe=True,
        subagent_default=True,
        binds_member_authority=False,
    ),
)
"""The tools the `rollover` strategy declares on its Manifest. The boundary spec names them too, so
a deploy that selects another strategy drops them from the turn's registry rather than offering a
reset the boundary it runs cannot honor."""
