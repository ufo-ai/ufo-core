from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ufo_ext_browser.bua.cdp import CdpError
from ufo_ext_browser.bua.errors import HallucinationError
from ufo_ext_browser.bua.page import Cdp
from ufo_ext_browser.bua.wire import Json, JsonDict, as_list, as_map, as_str

JS_FORM_INPUT = """
function(value) {
  const el = this;
  if (el instanceof HTMLInputElement && ["checkbox", "radio"].includes(el.type)) {
    el.checked = Boolean(value);
  } else if (el instanceof HTMLSelectElement) {
    el.value = String(value);
  } else if (el.isContentEditable) {
    el.textContent = String(value);
  } else {
    el.value = String(value);
  }
  el.dispatchEvent(new Event("input", {bubbles: true}));
  el.dispatchEvent(new Event("change", {bubbles: true}));
  return {value: el.value ?? el.textContent ?? ""};
}
"""


class BrowserFormNode(Protocol):
    session_id: str


class BrowserFormSession(Protocol):
    async def page(self, tab_id: int | None = None) -> Any: ...

    def connection(self) -> Cdp: ...

    async def call_on(
        self,
        session_id: str,
        object_id: str,
        function: str,
        arguments: list[Json] | None = None,
    ) -> JsonDict: ...

    def resolve_ref(self, tab: Any, ref: str) -> tuple[BrowserFormNode, int]: ...


@dataclass(frozen=True)
class BrowserForms:
    browser: BrowserFormSession

    async def upload_file(self, args: JsonDict) -> JsonDict:
        tab = await self.browser.page(_tab_id(args.get("tab_id")))
        ref = as_str(args.get("ref"), "ref")
        paths: list[Json] = [
            as_str(path, "files[]") for path in as_list(args.get("files"), "files")
        ]
        node, backend_id = self.browser.resolve_ref(tab, ref)
        try:
            await self.browser.connection().send(
                "DOM.setFileInputFiles",
                {"backendNodeId": backend_id, "files": paths},
                session_id=node.session_id,
            )
        except CdpError:
            raise HallucinationError(
                f"browser ref {ref!r} is not a file input: read_page and use a file input's ref"
            ) from None
        return {"ref": ref, "files": paths}

    async def input(self, args: JsonDict) -> JsonDict:
        tab = await self.browser.page(_tab_id(args.get("tab_id")))
        ref = as_str(args.get("ref"), "ref")
        node, backend_id = self.browser.resolve_ref(tab, ref)
        try:
            resolved = await self.browser.connection().send(
                "DOM.resolveNode", {"backendNodeId": backend_id}, session_id=node.session_id
            )
        except CdpError:
            raise HallucinationError(
                f"browser ref {ref!r} is not resolvable: re-read the page and use a ref it returns"
            ) from None
        object_id = as_str(as_map(resolved.get("object"), "object").get("objectId"), "objectId")
        return await self.browser.call_on(
            node.session_id, object_id, JS_FORM_INPUT, [args.get("value")]
        )


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
