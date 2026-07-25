# Browser page inspection and content modeling  `stage-11.2.3`

This stage is the system’s “eyes” for a live browser page. It is used during the main work loop, when the system needs to understand what is on a page before deciding what to click, type, or inspect next. It turns messy browser data into short, safe, model-friendly descriptions.

content.py provides the basic reading tools. It can pull plain text from a page, inspect page content, and search for elements. It also limits result size and avoids exposing raw browser details when other parts only need usable facts.

page.py builds the fuller page model. It reads the live page and creates a clean description of visible content and interactive items. At the same time, it keeps the browser’s internal element IDs, so a later step can act on the exact button, link, or input that was described.

find.py helps locate specific elements in the accessibility tree, which is the browser’s structured map of usable page parts. It cleans up matches so search results are consistent and safe to use.

## Files in this stage

### Page content modeling
Utilities for reading live browser pages, extracting model-usable content, and locating accessible elements safely.

### `extensions/browser/ufo_ext_browser/bua/content.py`

`domain_logic` · `request handling`

A browser page can be too large, too detailed, or too awkward for the rest of the system to use directly. This file acts like a helpful librarian: it knows how to ask the browser for the current tab, request a readable version of the page, trim it to a safe size, and return it in a predictable shape.

The main class, BrowserContent, is built around a browser session. That session supplies two things: the current page tab, and a page reader that can produce either a structured “tree” of page elements or markdown-style text. The tree is useful when the system needs to understand buttons, links, fields, and other page parts. The markdown text is useful when it needs the human-readable page content.

The file also supports finding things on a page. For simple searches, it scans the page tree directly. If a FindCompleter is provided, it can ask a language-model-style helper to interpret the query against a shortened page tree, then resolve that answer back into real page matches. Results are summarized for easy display.

Two limits protect the system from enormous pages: one for page trees and one for plain text. Without this file, callers would need to repeat tab selection, page reading, filtering, search, and truncation logic themselves, increasing bugs and inconsistent behavior.

#### Function details

##### `BrowserContentPageReader.tree`  (lines 23–23)

```
async def tree(self, tab: PageTab, filter_type: str, ref: str | None) -> str | None
```

**Purpose**: This is a promised interface for producing a structured view of a browser tab. The structure is a text tree of page elements, optionally filtered to a certain kind of content or focused around one referenced element.

**Data flow**: It receives a page tab, a filter name, and an optional reference string. An implementation elsewhere reads the page and returns a text tree, or returns nothing if the requested reference cannot be found.

**Call relations**: BrowserContent.tree relies on this contract after it has chosen the right tab. The actual work is done by whatever concrete page reader the browser session provides.


##### `BrowserContentPageReader.markdown`  (lines 25–25)

```
async def markdown(self, tab: PageTab) -> str
```

**Purpose**: This is a promised interface for producing the readable text of a browser tab. It represents the page in markdown, which is plain text with simple formatting markers.

**Data flow**: It receives a page tab. An implementation elsewhere reads the page content and returns a markdown-style string.

**Call relations**: BrowserContent.get_page_text asks the browser session for a page reader, then uses this method to turn the current tab into readable text.


##### `BrowserContentSession.page`  (lines 29–29)

```
async def page(self, tab_id: int | None=None) -> PageTab
```

**Purpose**: This is a promised interface for getting a browser tab to work with. It can use a specific tab id when one is supplied, or choose the current/default tab when none is supplied.

**Data flow**: It receives an optional tab id. An implementation elsewhere locates the matching browser tab and returns a PageTab object that represents it.

**Call relations**: BrowserContent.tree and BrowserContent.get_page_text call this before reading content, because they first need to know which tab is being inspected.


##### `BrowserContentSession.page_reader`  (lines 31–31)

```
def page_reader(self) -> BrowserContentPageReader
```

**Purpose**: This is a promised interface for getting the object that knows how to read page content. It separates browser-tab selection from the details of turning a page into text.

**Data flow**: It takes no extra input beyond the session object. It returns a page reader that can produce trees and markdown from tabs.

**Call relations**: BrowserContent.tree uses the returned reader for structured page trees, and BrowserContent.get_page_text uses it for markdown page text.


##### `BrowserContentSession.tab_info`  (lines 33–33)

```
async def tab_info(self, tab: Any) -> JsonDict
```

**Purpose**: This is a promised interface for collecting basic information about a tab. That information is added to page-text responses so the caller knows which page the text came from.

**Data flow**: It receives a tab object. An implementation elsewhere reads details such as tab metadata and returns them as a JSON-style dictionary.

**Call relations**: BrowserContent.get_page_text calls this after reading markdown so the final response includes both the text and useful tab context.


##### `BrowserContent.tree`  (lines 40–47)

```
async def tree(self, args: JsonDict, filter_type: str='all') -> str
```

**Purpose**: This gets a structured text tree for a browser page. Callers use it when they need to inspect page elements, such as links, buttons, fields, or a specific referenced element.

**Data flow**: It reads a possible tab id and reference id from the input arguments. It converts the tab id into a number when possible, gets the matching browser tab, asks the page reader for a tree using the requested filter and reference, and returns the tree text. If the referenced element cannot be found, it returns a clear message saying so.

**Call relations**: BrowserContent.read_page calls this when returning a page tree to a caller. BrowserContent.find calls it before searching through the page. Inside, it uses _tab_id to normalize the tab id and the browser session to get the tab and reader.

*Call graph*: calls 1 internal fn (_tab_id); called by 2 (find, read_page); 1 external calls (get).


##### `BrowserContent.read_page`  (lines 49–53)

```
async def read_page(self, args: JsonDict) -> JsonDict
```

**Purpose**: This returns a structured view of a page, with an optional filter for what kind of elements to include. It also protects callers from receiving an overly large response.

**Data flow**: It reads the requested filter from the input arguments and only accepts known filter names: all, interactive, or viewport. It asks BrowserContent.tree for the matching page tree, cuts the result down to the maximum allowed size, and returns both the shortened tree and a flag saying whether anything was cut off.

**Call relations**: This is a higher-level wrapper around BrowserContent.tree. It is the path a caller would use when they want page structure directly, rather than raw browser access.

*Call graph*: calls 1 internal fn (tree); 1 external calls (get).


##### `BrowserContent.get_page_text`  (lines 55–62)

```
async def get_page_text(self, args: JsonDict) -> JsonDict
```

**Purpose**: This returns the readable text of a browser page. It is meant for situations where the caller cares about the article-like or document-like content, not the full element structure.

**Data flow**: It reads a possible tab id from the input arguments, converts it when needed, gets the matching browser tab, and asks the page reader for markdown text. It trims the text to the maximum allowed size, marks whether it was truncated, and adds tab information from the browser session.

**Call relations**: This function does not use the structured tree path. Instead, it goes directly from tab selection to markdown reading, then enriches the answer with tab_info so the caller can identify the source page.

*Call graph*: calls 1 internal fn (_tab_id); 1 external calls (get).


##### `BrowserContent.find`  (lines 64–77)

```
async def find(self, args: JsonDict, complete: FindCompleter | None=None) -> JsonDict
```

**Purpose**: This searches the page tree for items matching a user query. It can use a direct parser for simpler matching, or an optional completion helper for more flexible interpretation.

**Data flow**: It reads and validates the query from the input arguments, then gets the full page tree. If no completion helper is supplied, it parses the tree directly and decides whether there may be more matches. If a helper is supplied, it sends the query and a shortened page tree to that helper, then resolves the helper’s reply into real page matches. It returns the matches plus a human-readable summary.

**Call relations**: This function builds on BrowserContent.tree because searching needs the page structure first. It then hands off either to parse_tree_matches for direct searching or to the completion flow using resolve_find_reply, and finally uses format_matches to summarize the result.

*Call graph*: calls 1 internal fn (tree); 5 external calls (format_matches, parse_tree_matches, resolve_find_reply, get, as_str).


##### `_tab_id`  (lines 80–89)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: This small helper turns a JSON-style tab id value into a Python integer, or returns nothing when no usable tab id was supplied. It lets callers pass tab ids as numbers or numeric strings.

**Data flow**: It receives a value that may be an integer, float, string, or empty/missing value. Integers pass through, floats are converted to integers, non-empty strings are parsed as integers, and anything else becomes None.

**Call relations**: BrowserContent.tree and BrowserContent.get_page_text call this before asking the browser session for a page. That keeps tab-id cleanup in one place instead of repeating the same checks in each method.

*Call graph*: called by 2 (get_page_text, tree).


### `extensions/browser/ufo_ext_browser/bua/find.py`

`domain_logic` · `request handling`

This file is the “reader” for an accessibility-tree format produced elsewhere. An accessibility tree is like a simplified map of a web page for assistive tools: it lists things such as buttons, text fields, links, their names, internal references, and sometimes screen coordinates. Without this file, a user query like “submit button” could not be reliably connected to the page element that should be clicked or inspected.

The file does two kinds of searching. First, it can do a simple local search: split the user’s query into words, scan each parsed tree line, and return entries whose line contains all the query terms. Second, it can clean up a reply from a language model. The prompt in this file tells the model to answer with element references only. `resolve_find_reply` then checks those references against the real tree, so the system does not trust a made-up or misremembered reference. This is important because the reference is what later actions may use to target an element.

The main flow is: parse tree text into small records, build match records with role/name/coordinates, cap results at a safe maximum, and format the final matches for a person to read. It also preserves a “more matches exist” signal so users know when they should narrow the query.

#### Function details

##### `tree_entries`  (lines 31–50)

```
def tree_entries(tree: str) -> list[JsonDict]
```

*Call graph*: called by 2 (parse_tree_matches, resolve_find_reply).


##### `_match_payload`  (lines 53–60)

```
def _match_payload(entry: JsonDict, reason: str) -> JsonDict
```

*Call graph*: called by 2 (parse_tree_matches, resolve_find_reply).


##### `parse_tree_matches`  (lines 63–71)

```
def parse_tree_matches(tree: str, query: str) -> list[JsonDict]
```

*Call graph*: calls 2 internal fn (_match_payload, tree_entries); 1 external calls (findall).


##### `resolve_find_reply`  (lines 74–102)

```
def resolve_find_reply(reply: str, tree: str) -> tuple[list[JsonDict], bool]
```

*Call graph*: calls 2 internal fn (_match_payload, tree_entries).


##### `format_matches`  (lines 105–114)

```
def format_matches(matches: list[JsonDict], has_more: bool=False) -> str
```


### `extensions/browser/ufo_ext_browser/bua/page.py`

`domain_logic` · `request handling`

A browser page is messy: it has HTML, visual layout, accessibility information, scrolling, iframes, and sometimes separate frame processes. This file combines those pieces into one useful snapshot. Think of it like making a tourist map of a city: it does not copy every brick, but it marks the meaningful places, their labels, and where to find them.

It talks to Chrome through CDP, the Chrome DevTools Protocol, which is a command channel for asking the browser about the page. It collects two main views: a DOM snapshot, which gives element positions and attributes, and an accessibility tree, which gives human-facing roles like button, link, heading, and textbox. It joins those views using Chrome's backend node IDs.

The file then renders the joined data in two forms. `render_page` produces a structured tree with roles, names, stable refs, coordinates, and useful states such as checked or disabled. `render_markdown` produces a reading view with headings, paragraphs, links, lists, and images.

A key detail is iframe support. Normal iframes are spliced into the parent tree. Out-of-process iframes, which Chrome runs in a separate target, are attached to separately and merged back in. Without this file, the system would either see only raw browser data or lose the reliable refs needed to act on page elements later.

#### Function details

##### `split_ref`  (lines 116–120)

```
def split_ref(ref: str) -> tuple[str, int] | None
```

**Purpose**: Breaks a browser element reference, such as `e12` or `f1e3`, into its frame prefix and element ID. This matters because the system must know both which frame an element belongs to and which browser node to target.

**Data flow**: It receives a reference string. It checks whether the string matches the expected pattern, then returns the frame prefix and numeric backend node ID. If the text is not a real reference, it returns nothing.

**Call relations**: When rendering a specific subtree, `render_page` uses this to find where to start. When an action later uses a ref, `BrowserPage.resolve_ref` uses it to reject invented or stale-looking references.

*Call graph*: called by 2 (resolve_ref, render_page).


##### `Cdp.send`  (lines 124–126)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: Defines the shape of the method used to send commands to Chrome through CDP, the Chrome DevTools Protocol. It is a promise that any connection object must provide this command-sending ability.

**Data flow**: It takes a command name, optional command parameters, and an optional browser session ID. The implementing connection sends that request to Chrome and returns the JSON-like response.

**Call relations**: `fetch_target` relies on this method to collect page snapshots and accessibility trees. Other browser-support code, such as page-settling logic, can also call the same protocol method.

*Call graph*: called by 2 (fetch_target, _flush_page_tasks).


##### `PageTab.frame_seq`  (lines 161–161)

```
def frame_seq(self, frame_id: str) -> int
```

**Purpose**: Defines how a tab assigns a short sequence number to a browser frame. Those numbers become part of refs, so iframe elements can be named in a compact and repeatable way during a snapshot.

**Data flow**: It receives a browser frame ID and returns an integer sequence number for that frame. The caller uses that number to build prefixes like `f1`.

**Call relations**: `BrowserPage._snapshot_oop` calls this when it discovers an out-of-process iframe and needs to give that frame a prefix before rendering or registering refs.

*Call graph*: called by 1 (_snapshot_oop).


##### `BrowserPageSession.connection`  (lines 168–168)

```
def connection(self) -> Cdp
```

**Purpose**: Defines how the page code gets the active CDP connection for the browser session. This keeps snapshot and action code independent from the concrete browser connection implementation.

**Data flow**: It reads the browser session object and returns a connection object capable of sending CDP commands. Nothing is changed directly by this protocol method itself.

**Call relations**: Methods on `BrowserPage` use this connection whenever they need fresh browser data or need to resolve a ref into a screen point.


##### `BrowserPageSession.init_session`  (lines 170–170)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Defines the setup step for a newly attached browser session, especially for out-of-process iframes. It gives the rest of the system a chance to prepare that session before using it.

**Data flow**: It receives a CDP session ID for a newly attached target. The implementation initializes that session and returns when it is ready.

**Call relations**: `BrowserPage._oop_session` calls this after attaching to a separate iframe target, before storing and using that session for future snapshots.


##### `_float`  (lines 190–195)

```
def _float(value: Json | None, default: float=0.0) -> float
```

**Purpose**: Safely turns a JSON value into a floating-point number. It avoids crashes or bad assumptions when browser responses contain missing or non-number values.

**Data flow**: It receives a value and a default. If the value is an integer or decimal number, it returns it as a float; otherwise it returns the default.

**Call relations**: `_parse_document` uses it for coordinates, scroll offsets, and sizes. `fetch_target` uses it for device pixel ratio, which controls coordinate scaling.

*Call graph*: called by 2 (_parse_document, fetch_target).


##### `_attr`  (lines 198–203)

```
def _attr(strings: list[str], attrs: list[Json], name: str) -> str | None
```

**Purpose**: Finds one HTML attribute inside Chrome's compact snapshot format. Chrome stores attribute names and values as indexes into a shared string table, so this helper translates that format into a normal value.

**Data flow**: It receives the shared string table, a list of attribute indexes, and the attribute name to look for. It scans name-value pairs and returns the matching value text, or nothing if the attribute is absent.

**Call relations**: `_parse_document` uses this to read useful attributes from inputs, images, and iframes, such as input type, image source, and iframe source.

*Call graph*: called by 1 (_parse_document).


##### `_parse_document`  (lines 206–266)

```
def _parse_document(doc: JsonDict, strings: list[str], dpr: float) -> _RawDoc
```

**Purpose**: Extracts the useful layout and element details from one document inside Chrome's DOM snapshot. It turns Chrome's dense snapshot arrays into easier-to-use geometry and iframe data.

**Data flow**: It receives one raw document record, the shared string table, and the device pixel ratio. It reads backend node IDs, scroll offsets, element bounds, cursor style, and selected attributes, then returns a `_RawDoc` containing element geometry and document-to-iframe links.

**Call relations**: `parse_snapshot` calls this once for each document that Chrome returned. It uses helpers such as `_float` and `_attr` to make browser data safe and readable.

*Call graph*: calls 2 internal fn (_attr, _float); called by 1 (parse_snapshot); 6 external calls (__init__, __init__, get, as_int, as_list, as_map).


##### `parse_snapshot`  (lines 269–318)

```
def parse_snapshot(snapshot: JsonDict, dpr: float, base_origin: tuple[float, float]=(0.0, 0.0)) -> list[DocData]
```

**Purpose**: Turns Chrome's full DOM snapshot into per-frame document data with coordinates in one shared page space. This is what lets iframe contents line up with their parent page instead of floating separately.

**Data flow**: It receives the raw snapshot, device pixel ratio, and a starting origin. It parses each document, follows iframe document links, accumulates iframe offsets, filters ignored extension iframes, and returns `DocData` records for the reachable documents.

**Call relations**: `fetch_target` calls this after asking Chrome for a DOM snapshot. The returned documents are then paired with accessibility trees to build frame snapshots.

*Call graph*: calls 1 internal fn (_parse_document); called by 1 (fetch_target); 5 external calls (__init__, get, as_list, as_map, as_str).


##### `fetch_target`  (lines 321–383)

```
async def fetch_target(cdp: Cdp, session_id: str, *, root_prefix: str, prefix_for: Callable[[str], str], base_origin: tuple[float, float]=(0.0, 0.0)) -> FrameSnapshot
```

**Purpose**: Collects everything needed to describe one browser target, such as a page or frame session. It asks Chrome for both visual layout data and accessibility data, then joins them into frame snapshots.

**Data flow**: It receives a CDP connection, session ID, ref-prefix rules, and an origin. It enables the needed Chrome domains, captures the DOM snapshot, reads device pixel ratio, parses geometry, fetches accessibility trees for each document, and returns the root `FrameSnapshot` with child frames linked in.

**Call relations**: `BrowserPage._snapshot_target` calls this as the main data-gathering step. It sends CDP commands through `Cdp.send`, uses `parse_snapshot`, and produces the snapshot that renderers consume.

*Call graph*: calls 3 internal fn (send, _float, parse_snapshot); called by 1 (_snapshot_target); 5 external calls (__init__, gather, as_list, as_map, as_str).


##### `_ax_value`  (lines 386–390)

```
def _ax_value(value: Json | None) -> str
```

**Purpose**: Reads the plain value out of a Chrome accessibility-tree value object. Chrome wraps many values in small dictionaries, and this helper unwraps them consistently.

**Data flow**: It receives a JSON value. If it is the expected dictionary shape, it returns the contained value as text; otherwise it returns an empty string.

**Call relations**: `render_page.render_node` and `render_markdown.walk` use it to read accessibility roles and names before deciding what to display.

*Call graph*: called by 2 (walk, render_node); 1 external calls (get).


##### `_ax_property`  (lines 393–401)

```
def _ax_property(node: JsonDict, name: str) -> Json
```

**Purpose**: Looks up one named property on an accessibility node, such as `checked`, `disabled`, `url`, or heading `level`. This gives renderers access to useful state without repeating Chrome's property-scanning format everywhere.

**Data flow**: It receives an accessibility node and a property name. It scans the node's property list and returns the stored value if present, otherwise nothing.

**Call relations**: `_format_extras`, `render_page.render_node`, and `render_markdown.walk` call this when they need element state, visibility, link URLs, or heading levels.

*Call graph*: called by 3 (_format_extras, walk, render_node); 3 external calls (get, as_list, as_map).


##### `_should_skip`  (lines 404–410)

```
def _should_skip(node: JsonDict, role: str, name: str) -> bool
```

**Purpose**: Decides whether an accessibility node is just structural wrapping and can be hidden from the rendered page tree. This keeps the output focused on meaningful items instead of noisy containers.

**Data flow**: It receives a node, its role, and its name. If the role is a skippable container, the name is empty, and it has no important state properties, it returns true; otherwise false.

**Call relations**: `render_page.render_node` uses this while walking the tree. When a node is skipped, its children can still be shown, like removing an unnecessary folder but keeping the papers inside.

*Call graph*: called by 1 (render_node); 3 external calls (get, as_list, as_map).


##### `_truncate`  (lines 413–416)

```
def _truncate(text: str, max_len: int) -> str
```

**Purpose**: Shortens long labels or values so the rendered page stays compact. It prevents huge names, values, or URLs from overwhelming the model-readable output.

**Data flow**: It receives text and a maximum length. If the text already fits, it returns it unchanged; otherwise it cuts it and adds an ellipsis.

**Call relations**: `_image_name`, `_format_extras`, and `render_page.render_node` use this before placing names or values in the final page text.

*Call graph*: called by 3 (_format_extras, _image_name, render_node).


##### `_image_name`  (lines 419–426)

```
def _image_name(src: str | None) -> str
```

**Purpose**: Creates a useful fallback name for an image from its source URL. This helps identify images that do not have accessible alt text.

**Data flow**: It receives an image source URL. It extracts the last filename-like part of the path, truncates it if needed, and returns it only if it looks like a file name with an extension.

**Call relations**: `render_page.render_node` and `render_markdown.walk` call this when an image has no normal accessibility name but the source filename may still be informative.

*Call graph*: calls 1 internal fn (_truncate); called by 2 (walk, render_node); 1 external calls (urlparse).


##### `_format_extras`  (lines 429–459)

```
def _format_extras(node: JsonDict, geom: NodeGeom | None) -> str
```

**Purpose**: Builds the extra state text appended to a rendered page line. It exposes practical details like input type, current value, checked state, disabled state, placeholder text, and safe URLs.

**Data flow**: It receives an accessibility node and optional geometry data. It reads selected accessibility properties, formats truthy or meaningful values, trims long text, skips unsafe or noisy URL schemes, and returns a leading-space string of extras or an empty string.

**Call relations**: `render_page.render_node` calls this right before adding a visible node line. It depends on `_ax_property` and `_truncate` to extract and tame the state values.

*Call graph*: calls 2 internal fn (_ax_property, _truncate); called by 1 (render_node).


##### `_frame_by_prefix`  (lines 462–469)

```
def _frame_by_prefix(root: FrameSnapshot, prefix: str) -> FrameSnapshot | None
```

**Purpose**: Finds the frame snapshot that owns a given ref prefix. Prefixes identify iframe context, so this is needed before resolving an element ID inside that frame.

**Data flow**: It receives the root frame snapshot and a prefix. It checks the root, then searches child frames recursively, returning the matching frame or nothing.

**Call relations**: `render_page` uses this when asked to render only the subtree for a particular ref.

*Call graph*: called by 1 (render_page).


##### `_node_by_backend`  (lines 472–476)

```
def _node_by_backend(frame: FrameSnapshot, backend_id: int) -> str | None
```

**Purpose**: Finds an accessibility node ID from a browser backend node ID inside one frame. This bridges the element ID used in refs to the accessibility tree node used for rendering.

**Data flow**: It receives a frame snapshot and a backend node ID. It scans the frame's accessibility nodes until it finds one linked to that backend ID, then returns the accessibility node ID or nothing.

**Call relations**: `render_page` calls this after `_frame_by_prefix` when rendering from a specific ref instead of from the whole page root.

*Call graph*: called by 1 (render_page).


##### `render_page`  (lines 479–575)

```
def render_page(root: FrameSnapshot, *, viewport: Size, model_size: Size | None=None, filter_type: str='all', max_depth: int=DEFAULT_MAX_DEPTH, ref: str | None=None) -> str | None
```

**Purpose**: Renders a `FrameSnapshot` as a structured page tree that a model can read and act on. The output includes roles, names, refs, center coordinates, and selected element state.

**Data flow**: It receives the root snapshot, viewport size, optional model size, filter mode, depth limit, and optional starting ref. It calculates coordinate scaling, walks the accessibility tree, splices iframe trees into place, filters noise or offscreen items as requested, and returns text lines or nothing if the requested ref cannot be found.

**Call relations**: `BrowserPage.tree` calls this after taking a snapshot. Internally it uses `split_ref`, `_frame_by_prefix`, and `_node_by_backend` when rendering a single referenced element, and its nested helpers do the actual line building.

*Call graph*: calls 3 internal fn (_frame_by_prefix, _node_by_backend, split_ref); called by 1 (tree); 1 external calls (effective_model_size).


##### `render_page.coord_str`  (lines 494–498)

```
def coord_str(geom: NodeGeom | None) -> str
```

**Purpose**: Formats an element's center point as readable coordinates. This gives later code or a model a simple target point for visual actions.

**Data flow**: It receives optional geometry for a node. If bounds are known, it scales the center of the rectangle from browser viewport space into model space and returns text like `(x=...,y=...)`; otherwise it returns an empty string.

**Call relations**: It is used inside `render_page.render_node` while composing each visible line of the page tree.


##### `render_page.splice`  (lines 500–503)

```
def splice(frame: FrameSnapshot, backend_id: int | None, depth: int) -> None
```

**Purpose**: Inserts a child frame's accessibility tree under the iframe node that contains it. This makes iframe contents appear in the right place in the rendered page.

**Data flow**: It receives a frame, an optional backend node ID, and a depth. If that backend node has a child frame with a root node, it asks `render_node` to render that child frame starting at the same point in the overall walk.

**Call relations**: `render_page.render_node.descend` calls this after rendering normal children, so iframe documents are treated like part of the same page outline.


##### `render_page.render_node`  (lines 505–559)

```
def render_node(frame: FrameSnapshot, ax_id: str, depth: int, parent_name: str) -> None
```

**Purpose**: Walks one accessibility node and decides whether and how it should appear in the structured page output. This is the heart of the page renderer.

**Data flow**: It receives a frame, an accessibility node ID, current depth, and parent name. It skips hidden, repeated, too-deep, or unhelpful nodes; filters by interaction or viewport rules; builds a line with role, name, ref, coordinates, and extras; then visits children and any spliced iframe.

**Call relations**: `render_page` starts this walker at either the page root or a referenced node. It calls helpers such as `_ax_value`, `_ax_property`, `_should_skip`, `_image_name`, `_truncate`, and `_format_extras` to turn raw browser data into readable output.

*Call graph*: calls 6 internal fn (_ax_property, _ax_value, _format_extras, _image_name, _should_skip, _truncate); 2 external calls (as_list, as_str).


##### `render_page.render_node.descend`  (lines 521–524)

```
def descend(child_depth: int, child_parent_name: str) -> None
```

**Purpose**: Moves from a node to its children without duplicating traversal code. It also makes sure iframe contents are included after normal child nodes.

**Data flow**: It receives the child depth and the name to treat as the parent name. It calls `render_node` for each child ID, then calls `splice` to render any child frame attached to the current backend node.

**Call relations**: `render_page.render_node` uses this both when a node is transparent and when a node has been rendered normally.


##### `render_markdown`  (lines 583–651)

```
def render_markdown(root: FrameSnapshot) -> str
```

**Purpose**: Renders a page snapshot as a reading-friendly Markdown document. It keeps headings, links, list items, images, and paragraph-like blocks instead of dumping raw page text.

**Data flow**: It receives the root frame snapshot. It walks the accessibility tree, gathers inline text into paragraphs, emits block-level content, splices iframe content, and returns Markdown text separated into readable blocks.

**Call relations**: `BrowserPage.markdown` calls this after taking a snapshot. Its nested `walk`, `emit`, and `flush` helpers cooperate to turn tree-shaped browser data into readable document text.

*Call graph*: called by 1 (markdown).


##### `render_markdown.emit`  (lines 592–595)

```
def emit(text: str) -> None
```

**Purpose**: Adds one finished Markdown block to the output if it is not empty or an immediate duplicate. This keeps the reading view tidy.

**Data flow**: It receives text, trims surrounding whitespace, checks it against the latest block, and appends it to the block list when useful.

**Call relations**: `render_markdown.flush` and `render_markdown.walk` call this whenever a paragraph, heading, list item, image, or named block is ready.


##### `render_markdown.flush`  (lines 597–600)

```
def flush() -> None
```

**Purpose**: Turns the currently collected inline words into a finished Markdown paragraph. It marks the boundary between inline text and the next block.

**Data flow**: It reads the temporary inline text list. If there is anything in it, it joins the pieces with spaces, emits the result, and clears the temporary list.

**Call relations**: `render_markdown.walk` calls this before and after block-like content so paragraphs do not run into headings, lists, or iframe content.


##### `render_markdown.walk`  (lines 602–646)

```
def walk(frame: FrameSnapshot, ax_id: str, parent_name: str) -> None
```

**Purpose**: Walks the accessibility tree and chooses Markdown shapes for each meaningful node. It is the main translator from browser accessibility nodes to reading-view text.

**Data flow**: It receives a frame, node ID, and parent name. It skips hidden or repeated nodes, turns headings into `#` headings, links into Markdown links, list items into bullets, images into image markers, and other names into inline text, then visits children and child frames.

**Call relations**: `render_markdown` starts this walker at the root node. It uses `_ax_value`, `_ax_property`, and `_image_name` to understand each node before adding text through `emit` or `flush`.

*Call graph*: calls 3 internal fn (_ax_property, _ax_value, _image_name); 2 external calls (as_list, as_str).


##### `BrowserPage.tree`  (lines 660–668)

```
async def tree(self, tab: PageTab, filter_type: str, ref: str | None) -> str | None
```

**Purpose**: Provides the public way to read a page as an actionable structured tree. Callers use it when they need refs and coordinates, not just plain text.

**Data flow**: It receives a tab, a filter type, and an optional ref. It takes a fresh snapshot of the tab, renders it with `render_page`, and returns the resulting text or nothing if the ref cannot be resolved.

**Call relations**: Higher-level browser tools call this to implement page-reading or finding. It connects `BrowserPage.snapshot` to `render_page`.

*Call graph*: calls 2 internal fn (snapshot, render_page).


##### `BrowserPage.markdown`  (lines 670–671)

```
async def markdown(self, tab: PageTab) -> str
```

**Purpose**: Provides the public way to read a page as a clean Markdown document. This is useful when the caller wants the page's readable content rather than action targets.

**Data flow**: It receives a tab. It takes a fresh snapshot and passes it to `render_markdown`, returning the Markdown string.

**Call relations**: This method is the simple wrapper that connects live browser snapshotting with the reading-view renderer.

*Call graph*: calls 2 internal fn (snapshot, render_markdown).


##### `BrowserPage.resolve_ref`  (lines 673–686)

```
def resolve_ref(self, tab: PageTab, ref: str) -> tuple[FrameNode, int]
```

**Purpose**: Turns a rendered ref back into the frame and backend node ID needed for browser commands. It also protects the system from made-up refs by raising a clear `HallucinationError`.

**Data flow**: It receives a tab and a ref string. It parses the ref, looks up the frame prefix in the tab's registered frame map, and returns the matching `FrameNode` plus backend ID; invalid or unknown refs become user-facing errors.

**Call relations**: `BrowserPage.ref_point` calls this before asking Chrome for element geometry. It relies on refs previously registered by `BrowserPage.snapshot`.

*Call graph*: calls 1 internal fn (split_ref); called by 1 (ref_point); 1 external calls (__init__).


##### `BrowserPage.ref_point`  (lines 688–714)

```
async def ref_point(self, tab: PageTab, ref: str) -> tuple[int, int]
```

**Purpose**: Finds a clickable center point for a rendered browser ref. This lets later actions move from a text ref like `e12` to screen coordinates.

**Data flow**: It receives a tab and ref. It resolves the ref, asks Chrome to scroll the element into view, gets its content quadrilateral or box model, averages the four corner points, adds the frame origin, and returns integer x-y coordinates. If Chrome cannot resolve the node, it raises a `HallucinationError` telling the caller to re-read the page.

**Call relations**: Action code can call this after `tree` has produced refs. It uses `resolve_ref`, browser CDP commands, and `_coord_float_or_default` to safely turn Chrome's geometry into a point.

*Call graph*: calls 2 internal fn (resolve_ref, _coord_float_or_default); 3 external calls (__init__, as_list, as_map).


##### `BrowserPage.snapshot`  (lines 716–720)

```
async def snapshot(self, tab: PageTab) -> FrameSnapshot
```

**Purpose**: Takes a fresh full snapshot of a tab and records which frame prefix belongs to which browser session. This prepares both rendering and later ref resolution.

**Data flow**: It receives a tab. It snapshots the main target, clears the tab's old ref-frame map, registers every frame in the new snapshot, and returns the root `FrameSnapshot`.

**Call relations**: `BrowserPage.tree` and `BrowserPage.markdown` call this before rendering. It delegates snapshot collection to `_snapshot_target` and frame registration to `_register_frames`.

*Call graph*: calls 2 internal fn (_register_frames, _snapshot_target); called by 2 (markdown, tree).


##### `BrowserPage._snapshot_target`  (lines 722–739)

```
async def _snapshot_target(self, tab: PageTab, session_id: str, root_prefix: str, origin: tuple[float, float], depth: int) -> FrameSnapshot
```

**Purpose**: Snapshots one browser target and, if allowed, attaches any separate iframe targets beneath it. It is the recursive building block for full-page snapshots.

**Data flow**: It receives a tab, session ID, root prefix, origin, and current depth. It calls `fetch_target` to collect same-target documents, then asks `_attach_oop_frames` to add out-of-process iframe snapshots when the depth limit has not been reached.

**Call relations**: `BrowserPage.snapshot` uses this for the main page. `BrowserPage._snapshot_oop` uses it again for each separate iframe target.

*Call graph*: calls 2 internal fn (_attach_oop_frames, fetch_target); called by 2 (_snapshot_oop, snapshot).


##### `BrowserPage._attach_oop_frames`  (lines 741–749)

```
async def _attach_oop_frames(self, tab: PageTab, root: FrameSnapshot, depth: int) -> None
```

**Purpose**: Finds iframe placeholders that Chrome did not include as normal child documents and tries to attach their separate snapshots. These are out-of-process iframes, meaning Chrome runs them in another target.

**Data flow**: It receives a tab, a root frame snapshot, and the current depth. It walks through the existing frame tree, looks at each recorded out-of-process iframe backend ID, snapshots it if possible, and inserts the resulting child snapshot under that iframe.

**Call relations**: `BrowserPage._snapshot_target` calls this after fetching the main target data. It delegates each separate iframe to `_snapshot_oop`.

*Call graph*: calls 1 internal fn (_snapshot_oop); called by 1 (_snapshot_target).


##### `BrowserPage._snapshot_oop`  (lines 751–777)

```
async def _snapshot_oop(self, tab: PageTab, frame: FrameSnapshot, backend_id: int, depth: int) -> FrameSnapshot | None
```

**Purpose**: Snapshots one out-of-process iframe and returns it as a child frame snapshot. It is careful to fail softly because iframe targets can disappear or deny access.

**Data flow**: It receives the tab, parent frame, backend node ID of the iframe, and depth. It asks Chrome which frame ID belongs to that node, gets or creates a CDP session for that frame, calculates the child origin from iframe bounds, and calls `_snapshot_target` for the child. On expected CDP failures, it logs a warning and returns nothing.

**Call relations**: `BrowserPage._attach_oop_frames` calls this for each out-of-process iframe. It uses `PageTab.frame_seq` for prefixes, `_oop_session` for session setup, and `_snapshot_target` for the actual child capture.

*Call graph*: calls 3 internal fn (_oop_session, _snapshot_target, frame_seq); called by 1 (_attach_oop_frames); 1 external calls (as_map).


##### `BrowserPage._register_frames`  (lines 779–782)

```
def _register_frames(self, tab: PageTab, frame: FrameSnapshot) -> None
```

**Purpose**: Stores the mapping from rendered ref prefixes to real browser frame sessions. Without this map, later refs could not be turned back into browser commands.

**Data flow**: It receives a tab and a frame snapshot. It records the frame's prefix, frame ID, session ID, and origin in `tab.ref_frames`, then repeats this for every child frame.

**Call relations**: `BrowserPage.snapshot` calls this after collecting a fresh snapshot. `BrowserPage.resolve_ref` later reads the map it created.

*Call graph*: called by 1 (snapshot); 1 external calls (__init__).


##### `BrowserPage._oop_session`  (lines 784–797)

```
async def _oop_session(self, frame_id: str) -> str | None
```

**Purpose**: Gets a CDP session for an out-of-process iframe, reusing a cached one when possible. This avoids attaching to the same iframe target again and again.

**Data flow**: It receives a frame ID. It checks the browser session cache; if missing, it asks Chrome to attach to that target, initializes the new session, stores it in the cache, and returns the session ID. If attaching fails, it returns nothing.

**Call relations**: `BrowserPage._snapshot_oop` calls this before it can snapshot a separate iframe target. It uses the browser's connection and session initialization hook.

*Call graph*: called by 1 (_snapshot_oop); 1 external calls (as_str).


##### `_coord_float_or_default`  (lines 800–809)

```
def _coord_float_or_default(value: Json | None, default: float) -> float
```

**Purpose**: Converts one coordinate value from Chrome into a float. It accepts both numbers and numeric strings, while still rejecting unexpected data.

**Data flow**: It receives a coordinate-like value and a default. Numbers become floats, non-empty strings are parsed as floats, `None` becomes the default, and invalid types raise a validation error.

**Call relations**: `BrowserPage.ref_point` uses this when averaging Chrome's quadrilateral coordinates into a click point.

*Call graph*: called by 1 (ref_point); 1 external calls (__init__).
