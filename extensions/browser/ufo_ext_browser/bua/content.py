from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ufo.sdk.browser import FindCompleter
from ufo_ext_browser.bua.find import (
    FIND_SYSTEM_PROMPT,
    FIND_TREE_CHAR_LIMIT,
    MAX_RESULTS,
    format_matches,
    parse_tree_matches,
    resolve_find_reply,
)
from ufo_ext_browser.bua.page import PageTab
from ufo_ext_browser.bua.wire import Json, JsonDict, as_str

MAX_READ_CHARS = 50_000
MAX_TEXT_CHARS = 100_000


class BrowserContentPageReader(Protocol):
    async def tree(self, tab: PageTab, filter_type: str, ref: str | None) -> str | None: ...

    async def markdown(self, tab: PageTab) -> str: ...


class BrowserContentSession(Protocol):
    async def page(self, tab_id: int | None = None) -> PageTab: ...

    def page_reader(self) -> BrowserContentPageReader: ...

    async def tab_info(self, tab: Any) -> JsonDict: ...


@dataclass(frozen=True)
class BrowserContent:
    browser: BrowserContentSession

    async def tree(self, args: JsonDict, filter_type: str = "all") -> str:
        tab = await self.browser.page(_tab_id(args.get("tab_id")))
        ref_id = args.get("ref_id")
        ref = ref_id if isinstance(ref_id, str) and ref_id else None
        text = await self.browser.page_reader().tree(tab, filter_type, ref)
        if text is None:
            return f"No element found for {ref}"
        return text

    async def read_page(self, args: JsonDict) -> JsonDict:
        filter_arg = str(args.get("filter") or "all")
        filter_type = filter_arg if filter_arg in {"all", "interactive", "viewport"} else "all"
        tree = await self.tree(args, filter_type)
        return {"tree": tree[:MAX_READ_CHARS], "truncated": len(tree) > MAX_READ_CHARS}

    async def get_page_text(self, args: JsonDict) -> JsonDict:
        tab = await self.browser.page(_tab_id(args.get("tab_id")))
        text = await self.browser.page_reader().markdown(tab)
        return {
            "text": text[:MAX_TEXT_CHARS],
            "truncated": len(text) > MAX_TEXT_CHARS,
            **await self.browser.tab_info(tab),
        }

    async def find(self, args: JsonDict, complete: FindCompleter | None = None) -> JsonDict:
        query = as_str(args.get("query"), "query")
        tree = await self.tree(args, "all")
        if complete is None:
            matches = parse_tree_matches(tree, query)
            has_more = len(matches) >= MAX_RESULTS
        else:
            reply = await complete(
                FIND_SYSTEM_PROMPT,
                f"Query: {query}\n\nPage tree:\n{tree[:FIND_TREE_CHAR_LIMIT]}",
            )
            matches, has_more = resolve_find_reply(reply, tree)
        items: list[Json] = [*matches]
        return {"matches": items, "summary": format_matches(matches, has_more)}


def _tab_id(value: Json | None) -> int | None:
    match value:
        case int():
            return value
        case float():
            return int(value)
        case str() if value:
            return int(value)
        case _:
            return None
