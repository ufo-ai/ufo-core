# Page inspection and element mapping  `stage-12.1.4`

This stage is the system’s “eyes” for a live web page. It runs during the main work loop, whenever the system needs to understand what is on the screen before deciding what to click, type, or report. It reads the page, turns it into a simpler form an AI model can use, and then maps the model’s answer back to the real browser.

content.py is the command doorway. It accepts simple JSON-like requests such as “get the page text” or “find this element,” performs the lookup, and returns safe, limited-size results. page.py builds the main page snapshot. It reads the live browser state and creates a structured description of controls, text, and positions, then can turn an element reference back into a real place to act on. find.py searches that structured “accessibility tree,” a browser-made outline of visible text and controls, and can check whether an AI’s chosen element really exists. coordinate.py is the ruler. It converts between AI image coordinates and actual browser pixels so clicks land in the right spot.

## Files in this stage

### Content command interface
Browser-content commands expose page inspection, text extraction, and element search as safe JSON-style operations.

### `extensions/browser/ufo_ext_browser/bua/content.py`

`domain_logic` · `request handling`

This file is like a reading desk for an automated browser. Other parts of the system can ask, “What is on this page?”, “Give me the page text,” or “Find something matching this query,” without needing to know how browser tabs and page readers work underneath.

The file defines small interfaces, called protocols, for the things it expects from the browser session: it must be able to choose a tab, provide a page reader, and report tab details. The real work is grouped in BrowserContent. When a request comes in, BrowserContent first chooses the right tab using a tab id if one was supplied. It then asks the page reader for either a structured page tree or markdown-like page text.

Two safety limits matter here. Large pages can produce huge output, so read_page cuts the tree at 50,000 characters, and get_page_text cuts text at 100,000 characters. Each response says whether it was truncated, so the caller knows if it only received part of the page.

The find command searches the page tree. It can either use a simple built-in parser or, if given a completion function, ask a language-model-style helper to identify matches. In both cases it returns the matches plus a human-readable summary.

#### Function details

##### `BrowserContentPageReader.tree`  (lines 23–23)

```
async def tree(self, tab: PageTab, filter_type: str, ref: str | None) -> str | None
```

**Purpose**: This describes the method a page reader must provide to return a structured view of a browser page. The tree is a text representation of page elements, optionally narrowed to a certain kind of element or a referenced element.

**Data flow**: It receives a browser tab, a filter such as all or interactive, and an optional reference id. A concrete page reader uses those inputs to inspect the page and returns tree text, or returns nothing if the requested reference cannot be found.

**Call relations**: BrowserContent.tree relies on this protocol method through the session's page_reader. This file only defines the expected shape; another part of the browser extension supplies the real implementation.


##### `BrowserContentPageReader.markdown`  (lines 25–25)

```
async def markdown(self, tab: PageTab) -> str
```

**Purpose**: This describes the method a page reader must provide to turn a browser page into plain readable text. It is used when callers want the page contents rather than the element-by-element tree.

**Data flow**: It receives a browser tab. A concrete page reader reads that tab and returns markdown-like text representing the visible or meaningful page content.

**Call relations**: BrowserContent.get_page_text calls this through the page reader supplied by the browser session. The protocol lets BrowserContent stay independent from the exact browser-reading implementation.


##### `BrowserContentSession.page`  (lines 29–29)

```
async def page(self, tab_id: int | None=None) -> PageTab
```

**Purpose**: This describes how BrowserContent asks the browser session for a tab to work on. The caller may name a tab by id, or leave it blank to use the session's default current tab.

**Data flow**: It receives an optional tab id. The concrete browser session finds the matching tab, or chooses the current tab, and returns a PageTab object representing it.

**Call relations**: BrowserContent.tree and BrowserContent.get_page_text use this first before reading anything from the page. This protocol method is the doorway from request arguments into an actual browser tab.


##### `BrowserContentSession.page_reader`  (lines 31–31)

```
def page_reader(self) -> BrowserContentPageReader
```

**Purpose**: This describes how BrowserContent gets the helper that knows how to read page contents. It separates choosing a tab from extracting text or element trees from that tab.

**Data flow**: It takes no request data. The concrete browser session returns an object that follows BrowserContentPageReader, meaning it can produce a tree or markdown text for a tab.

**Call relations**: BrowserContent.tree and BrowserContent.get_page_text call this after they have a tab. The returned reader does the browser-specific extraction work that BrowserContent coordinates.


##### `BrowserContentSession.tab_info`  (lines 33–33)

```
async def tab_info(self, tab: Any) -> JsonDict
```

**Purpose**: This describes how BrowserContent asks for extra details about a tab, such as metadata the caller may need alongside page text. It is used to enrich the text response with context about where the text came from.

**Data flow**: It receives a tab-like object. The concrete browser session looks up information about that tab and returns it as a JSON-style dictionary.

**Call relations**: BrowserContent.get_page_text adds this information to its response after reading the page text. This keeps tab metadata close to the browser session, where that knowledge belongs.


##### `BrowserContent.tree`  (lines 40–47)

```
async def tree(self, args: JsonDict, filter_type: str='all') -> str
```

**Purpose**: This returns a structured text tree of a browser page or part of a page. Someone uses it when they need a machine-readable map of page elements, not just the visible text.

**Data flow**: It starts with request arguments, reads the optional tab_id and ref_id values, and converts the tab id into a number with _tab_id. It asks the browser session for that tab, asks the page reader for a tree using the requested filter and reference, and returns the tree text. If the referenced element is not found, it returns a clear message saying so.

**Call relations**: BrowserContent.read_page calls this to provide a capped page-tree response, and BrowserContent.find calls it before searching the page. It is the shared path for any feature that needs the page's element tree.

*Call graph*: calls 1 internal fn (_tab_id); called by 2 (find, read_page); 1 external calls (get).


##### `BrowserContent.read_page`  (lines 49–53)

```
async def read_page(self, args: JsonDict) -> JsonDict
```

**Purpose**: This produces a safe response containing the page's element tree. It exists so callers can inspect a page without accidentally receiving an enormous amount of text.

**Data flow**: It reads the optional filter argument and accepts only known filter choices: all, interactive, or viewport. It asks BrowserContent.tree for the matching page tree, cuts the result to the maximum allowed length, and returns a dictionary containing the shortened tree plus a flag saying whether anything was cut off.

**Call relations**: This is a public-facing wrapper around BrowserContent.tree. It validates the requested kind of page view before handing off to tree, then shapes the result into a JSON-style response.

*Call graph*: calls 1 internal fn (tree); 1 external calls (get).


##### `BrowserContent.get_page_text`  (lines 55–62)

```
async def get_page_text(self, args: JsonDict) -> JsonDict
```

**Purpose**: This returns the readable text of a browser page, along with tab details. It is useful when a caller wants page content in prose form rather than an element map.

**Data flow**: It reads the optional tab_id from the request, converts it with _tab_id, and asks the browser session for the chosen tab. It then asks the page reader for markdown text, cuts that text to the maximum allowed length, adds a truncation flag, and merges in tab information from the browser session.

**Call relations**: This path does not use BrowserContent.tree because it wants plain page text instead of a structured element tree. It works directly with the browser session and page reader, then asks the session for tab metadata to complete the response.

*Call graph*: calls 1 internal fn (_tab_id); 1 external calls (get).


##### `BrowserContent.find`  (lines 64–77)

```
async def find(self, args: JsonDict, complete: FindCompleter | None=None) -> JsonDict
```

**Purpose**: This searches the page tree for elements matching a user's query. It can use either a built-in text-matching approach or an optional smarter completion helper, such as a language model callback.

**Data flow**: It reads and checks the query string from the request, then gets the full page tree through BrowserContent.tree. If no completion helper is supplied, it parses the tree locally for matches. If a helper is supplied, it sends the query and a limited slice of the tree to that helper, then resolves the helper's reply back against the original tree. Finally, it returns the matches and a summary written for humans.

**Call relations**: This function builds on BrowserContent.tree because searching needs the page's element map first. It then hands the tree to the find helpers: parse_tree_matches for local matching, or resolve_find_reply after an external completion call, and format_matches to explain the result.

*Call graph*: calls 1 internal fn (tree); 5 external calls (format_matches, parse_tree_matches, resolve_find_reply, get, as_str).


##### `_tab_id`  (lines 80–89)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: This small helper turns a tab id from a JSON-style request into a normal integer, or returns nothing if no usable id was provided. It lets callers send tab ids as numbers or strings.

**Data flow**: It receives a value that may be an integer, floating-point number, string, or something else. Integers are returned as-is, floats and non-empty strings are converted to integers, and missing or unsupported values become None.

**Call relations**: BrowserContent.tree and BrowserContent.get_page_text call this before asking the browser session for a tab. It keeps tab-id cleanup in one place so those higher-level functions can simply pass along either a valid id or no id.

*Call graph*: called by 2 (get_page_text, tree).


### Coordinate translation
Coordinate utilities map AI-visible screenshot points to real browser pixels and choose model-safe screenshot sizes.

### `extensions/browser/ufo_ext_browser/bua/coordinate.py`

`util` · `request handling`

Browser automation with vision has a simple but important mismatch: the AI model may describe a point in one coordinate system, while the browser needs a point in actual screen pixels. This file is the ruler that keeps those spaces aligned. It defines small plain data shapes for a size, with width and height, and a coordinate, with x and y. For Claude-family vision models, screenshots are resized before being sent so they stay within Anthropic's limits: no side longer than 1568 pixels and no more than about 1.15 million pixels total. That avoids hidden server-side downscaling, which would make the model's coordinates harder to trust. For Gemini, the model reports positions on a fixed 0-to-1000 grid, no matter how large the image is, so the file can use that fixed model space instead. The conversion functions then scale points back and forth. If the model says “click at this point,” the code can turn that into real browser viewport pixels. If the browser has a point and needs to express it in model terms, it can do the reverse. Without this file, clicks could land in the wrong place, especially when screenshots are resized or when different model families use different coordinate rules.

#### Function details

##### `compute_screenshot_dimensions`  (lines 22–33)

```
def compute_screenshot_dimensions(viewport: Size) -> Size
```

**Purpose**: This function finds the largest screenshot size that can be sent to Claude-style vision models without the provider shrinking it again behind the scenes. It keeps the image proportional, like resizing a photo while preserving its shape.

**Data flow**: It receives the browser viewport size. It first shrinks the width and height if either side is longer than the allowed maximum, then checks whether the total pixel count is still too large and shrinks again if needed. It returns a new Size containing the safe screenshot width and height.

**Call relations**: When no explicit model coordinate size is supplied, effective_model_size calls this function to decide the coordinate space the model will actually see. It creates and returns a Size object as the shared answer for later coordinate conversion.

*Call graph*: called by 1 (effective_model_size); 1 external calls (__init__).


##### `effective_model_size`  (lines 36–38)

```
def effective_model_size(viewport: Size, model_size: Size | None=None) -> Size
```

**Purpose**: This function decides which coordinate space should be used for the model. If another part of the system already knows the model's coordinate size, it uses that; otherwise it computes the safe screenshot size.

**Data flow**: It receives the browser viewport size and optionally a model size. If the model size is present, it returns it unchanged. If not, it sends the viewport size to compute_screenshot_dimensions and returns the computed screenshot size.

**Call relations**: Both model_to_viewport and viewport_to_model call this before doing their scaling, because they need to know the model's working coordinate space. It acts as the small decision point between an explicit override, such as Gemini's fixed grid, and the computed screenshot dimensions used for Claude-style models.

*Call graph*: calls 1 internal fn (compute_screenshot_dimensions); called by 2 (model_to_viewport, viewport_to_model).


##### `model_to_viewport`  (lines 41–47)

```
def model_to_viewport(coord: Coord, viewport: Size, model_size: Size | None=None) -> Coord
```

**Purpose**: This function turns a point reported by the AI model into a real browser viewport point. It is used when the system needs to dispatch an input action, such as clicking where the model pointed.

**Data flow**: It receives a coordinate in model space, the real browser viewport size, and optionally the model coordinate size. It finds the effective model size, compares that size to the viewport, scales x and y into browser pixels, and returns a new Coord with the converted point.

**Call relations**: Before converting, it asks effective_model_size what coordinate system the model was using. After that, it creates the viewport Coord that browser input code can use to perform the actual action in the right place.

*Call graph*: calls 1 internal fn (effective_model_size); 1 external calls (__init__).


##### `model_coordinate_space`  (lines 50–55)

```
def model_coordinate_space(model: str | None) -> Size | None
```

**Purpose**: This function identifies models that use a special fixed coordinate grid. In particular, Gemini reports points on a 1000-by-1000 grid, so the rest of the code can convert those points correctly.

**Data flow**: It receives a model name, or no model name. If the name contains “gemini” in any letter case, it returns a Size of 1000 by 1000. Otherwise it returns None, meaning the normal screenshot-based coordinate size should be used.

**Call relations**: This function is a helper for callers that know the model name and need to choose the right coordinate space before converting points. When it returns a Size, that value can be passed into model_to_viewport or viewport_to_model as the explicit model size.

*Call graph*: 1 external calls (__init__).


##### `viewport_to_model`  (lines 58–64)

```
def viewport_to_model(coord: Coord, viewport: Size, model_size: Size | None=None) -> Coord
```

**Purpose**: This function converts a real browser viewport point back into the coordinate system used by the model. It is useful when browser pixel positions need to be described or compared in model terms.

**Data flow**: It receives a browser viewport coordinate, the viewport size, and optionally a model coordinate size. It finds the effective model size, scales the x and y values from viewport pixels into that model space, and returns a new Coord with the converted point.

**Call relations**: Like model_to_viewport, it first calls effective_model_size so it scales against the correct model coordinate system. It then hands back a model-space Coord that other vision or reasoning code can understand.

*Call graph*: calls 1 internal fn (effective_model_size); 1 external calls (__init__).


### Accessibility tree mapping
Element-search and page-snapshot logic turns accessibility-tree content into readable model context and maps model references back to browser elements.

### `extensions/browser/ufo_ext_browser/bua/find.py`

`domain_logic` · `request handling`

This file is the “trust but verify” layer for finding things on a web page. Elsewhere, the browser page is rendered as plain text lines such as a button, link, or textbox with a name, a reference id, and screen coordinates. This file reads those lines and turns them into small records that code can safely use.

The main idea is simple: every usable page element has a ref, like a ticket number at a deli counter. If a search or an AI reply points to a ref, this file checks that the ref really appears in the current tree before returning it. That matters because a made-up or stale ref could cause the browser automation to click or inspect the wrong thing.

It supports two matching paths. One path, `parse_tree_matches`, does a simple local text search: it splits the user’s query into words and returns tree lines containing all of them. The other path, `resolve_find_reply`, takes a reply from a language model, extracts refs from it, and grounds them back in the actual tree. Both paths return the same clean result shape: ref, role, name, coordinates, and an optional reason. `format_matches` then turns those records into readable output, including a note when more matches exist.

#### Function details

##### `tree_entries`  (lines 31–50)

```
def tree_entries(tree: str) -> list[JsonDict]
```

**Purpose**: This function reads the plain-text accessibility tree and pulls out the useful facts for each element: its reference id, role, visible name, coordinates, and original line text. It is the parser that turns human-readable tree lines into structured data the rest of the file can search safely.

**Data flow**: It takes the full tree as a string. It goes line by line, keeps only lines that look like accessibility-tree entries with a valid ref, reads optional coordinates, and builds a list of small dictionaries. The output is a list of element records; lines that do not match the expected format are ignored.

**Call relations**: Both search paths start here. `parse_tree_matches` uses it before doing local word matching, and `resolve_find_reply` uses it to build the list of real refs that an outside reply is allowed to mention.

*Call graph*: called by 2 (parse_tree_matches, resolve_find_reply).


##### `_match_payload`  (lines 53–60)

```
def _match_payload(entry: JsonDict, reason: str) -> JsonDict
```

**Purpose**: This helper creates the standard result shape for one matched element. It keeps the real element details from the parsed tree and adds the reason why it matched.

**Data flow**: It receives one parsed tree entry and a reason string. It copies the entry’s ref, role, name, and coordinates, attaches the reason, and returns a new dictionary ready to send back to callers.

**Call relations**: It is used by both `parse_tree_matches` and `resolve_find_reply` so that direct search results and AI-grounded results look the same to later code.

*Call graph*: called by 2 (parse_tree_matches, resolve_find_reply).


##### `parse_tree_matches`  (lines 63–71)

```
def parse_tree_matches(tree: str, query: str) -> list[JsonDict]
```

**Purpose**: This function performs a simple built-in search over the accessibility tree without needing an AI reply. It is useful when the query terms can be matched directly against the tree text.

**Data flow**: It takes the tree text and a user query. It lowers the query, extracts word-like terms longer than one character, parses the tree into entries, and keeps entries whose tree line contains every query term. It returns up to `MAX_RESULTS` standardized match records.

**Call relations**: It calls `tree_entries` to get searchable element records, then calls `_match_payload` for each accepted result. It is the local-search alternative to `resolve_find_reply`, which instead checks a model’s proposed refs.

*Call graph*: calls 2 internal fn (_match_payload, tree_entries); 1 external calls (findall).


##### `resolve_find_reply`  (lines 74–102)

```
def resolve_find_reply(reply: str, tree: str) -> tuple[list[JsonDict], bool]
```

**Purpose**: This function checks an AI-style find reply against the real accessibility tree. It prevents fake, duplicated, or no-longer-existing refs from being returned to the rest of the browser automation.

**Data flow**: It takes a reply string and the current tree text. First it parses the tree and builds a lookup table of valid refs. Then it reads the reply line by line, ignores empty or malformed lines, recognizes `NO_MATCHES` and `MORE`, extracts refs, and keeps only refs that exist in the tree and have not already been used. It returns the cleaned list of matches plus a true-or-false flag saying whether the reply claimed more matches exist.

**Call relations**: It calls `tree_entries` to learn what refs are actually valid, then uses `_match_payload` to return trusted match records. This is the safety step after an outside finder, such as a language model, suggests elements from the tree.

*Call graph*: calls 2 internal fn (_match_payload, tree_entries).


##### `format_matches`  (lines 105–114)

```
def format_matches(matches: list[JsonDict], has_more: bool=False) -> str
```

**Purpose**: This function turns match records into readable text. It gives callers a compact display of each element’s ref, role, name, coordinates, and optional reason.

**Data flow**: It receives a list of match dictionaries and an optional flag saying whether more matches exist. It formats each match into one line, appends the reason when present, and adds a final hint to refine the query if there are more results. It returns one joined string.

**Call relations**: It sits at the output end of the flow. After `parse_tree_matches` or `resolve_find_reply` has produced structured results, this function presents them in a form that can be shown to a user or sent as a plain-text response.


### `extensions/browser/ufo_ext_browser/bua/page.py`

`domain_logic` · `request handling`

A web page is easy for a person to see, but hard for a model to use directly. This file builds a cleaned-up map of the page from Chrome's own browser data: the DOM, which is the page's element tree, and the accessibility tree, which is the browser's simplified view used by screen readers. It combines those with element positions so the result can say things like: button "Submit" [ref=e42] (x=120,y=340).

The file also deals with frames, including iframes that belong to separate browser targets. An iframe is like a smaller page embedded inside the main page; Chrome sometimes reports it separately, so this code fetches those pieces and splices them back into one tree.

There are two main renderings. `render_page` creates an action-oriented outline with roles, names, references, coordinates, and useful state such as checked or disabled. `render_markdown` creates a reading-oriented view with headings, links, list items, and paragraphs. `BrowserPage` is the public wrapper: it takes snapshots, remembers which frame each reference belongs to, and resolves a reference back to a real point on the page. Without this file, the model would either see messy raw browser internals or have no reliable way to connect its chosen page element back to something Chrome can interact with.

#### Function details

##### `split_ref`  (lines 116–120)

```
def split_ref(ref: str) -> tuple[str, int] | None
```

**Purpose**: Checks and breaks apart a browser element reference such as `e12` or `f1e3`. The prefix says which frame the element came from, and the number identifies the browser node inside that frame.

**Data flow**: It receives a reference string. It matches it against the expected reference pattern, then returns the frame prefix and numeric backend node id; if the string does not fit, it returns nothing.

**Call relations**: When rendering a focused subtree, `render_page` uses this to understand the requested starting reference. `BrowserPage.resolve_ref` uses it before turning a model-provided reference into a real browser target.

*Call graph*: called by 2 (resolve_ref, render_page).


##### `Cdp.send`  (lines 124–126)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: Describes the browser connection method used to send Chrome DevTools Protocol commands. Chrome DevTools Protocol, or CDP, is Chrome's control-and-inspection API.

**Data flow**: It takes a command name, optional command data, and optionally a session id for a particular frame or target. It returns a JSON-like dictionary response from the browser.

**Call relations**: This is a protocol declaration rather than an implementation here. `fetch_target` relies on it to ask Chrome for snapshots and accessibility data, and another subsystem uses the same shape to flush page tasks.

*Call graph*: called by 2 (fetch_target, _flush_page_tasks).


##### `PageTab.frame_seq`  (lines 161–161)

```
def frame_seq(self, frame_id: str) -> int
```

**Purpose**: Provides a stable short number for a frame id so references can use compact prefixes like `f1`. This keeps model-facing references readable while still pointing to the right embedded page.

**Data flow**: It receives a browser frame id and returns that frame's sequence number for the current tab.

**Call relations**: `BrowserPage._snapshot_oop` calls it when naming an out-of-process iframe, so later rendered refs can be traced back to the correct frame.

*Call graph*: called by 1 (_snapshot_oop).


##### `BrowserPageSession.connection`  (lines 168–168)

```
def connection(self) -> Cdp
```

**Purpose**: Provides access to the active browser control connection. Other code uses that connection to ask Chrome for page data or element geometry.

**Data flow**: It reads the browser session object and returns something that can send CDP commands.

**Call relations**: This protocol method is called through `BrowserPage` methods whenever the file needs live information from Chrome.


##### `BrowserPageSession.init_session`  (lines 170–170)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Prepares a newly attached browser session so it is ready for this system's normal CDP use. This matters for separate iframe targets that are attached after the main page is already running.

**Data flow**: It receives a session id for a newly attached target. It performs setup work and returns when that session is ready.

**Call relations**: `BrowserPage._oop_session` calls this after attaching to an out-of-process iframe target, before caching and using that session.


##### `_float`  (lines 190–195)

```
def _float(value: Json | None, default: float=0.0) -> float
```

**Purpose**: Safely turns a JSON value into a floating-point number. It protects the snapshot parser from unexpected missing or non-number values.

**Data flow**: It receives a value and a default number. If the value is an integer or float, it returns it as a float; otherwise it returns the default.

**Call relations**: `_parse_document` uses it for scroll offsets and element bounds. `fetch_target` uses it to read the browser's device pixel ratio.

*Call graph*: called by 2 (_parse_document, fetch_target).


##### `_attr`  (lines 198–203)

```
def _attr(strings: list[str], attrs: list[Json], name: str) -> str | None
```

**Purpose**: Looks up one HTML attribute from Chrome's compact snapshot format. Chrome stores attribute names and values as indexes into a shared string table, so this helper turns that packed form into a normal value.

**Data flow**: It receives the shared string list, an attribute list, and the attribute name to find. It scans name/value pairs and returns the matching string value, or nothing if absent.

**Call relations**: `_parse_document` uses it to read useful attributes such as input type, image source, and iframe source.

*Call graph*: called by 1 (_parse_document).


##### `_parse_document`  (lines 206–266)

```
def _parse_document(doc: JsonDict, strings: list[str], dpr: float) -> _RawDoc
```

**Purpose**: Extracts useful geometry and element hints from one raw document snapshot. It turns Chrome's packed DOM snapshot data into a simpler per-document record.

**Data flow**: It receives one raw document, the snapshot string table, and the device pixel ratio. It reads frame id, backend node ids, scroll offsets, element bounds, cursor style, selected attributes, and child-document links, then returns a `_RawDoc` with normalized CSS-pixel positions.

**Call relations**: `parse_snapshot` calls this once for each document Chrome returned. It uses small helpers like `_float` and `_attr` to keep the parsing safe and readable.

*Call graph*: calls 2 internal fn (_attr, _float); called by 1 (parse_snapshot); 6 external calls (__init__, __init__, get, as_int, as_list, as_map).


##### `parse_snapshot`  (lines 269–318)

```
def parse_snapshot(snapshot: JsonDict, dpr: float, base_origin: tuple[float, float]=(0.0, 0.0)) -> list[DocData]
```

**Purpose**: Turns Chrome's full DOM snapshot into document records whose coordinates line up across the main page and same-process iframes. It is the bridge between raw browser data and the frame snapshots used for rendering.

**Data flow**: It receives the snapshot JSON, device pixel ratio, and a starting origin. It parses each document, calculates where child documents sit inside parent iframes, adjusts element bounds into shared page coordinates, and returns `DocData` entries.

**Call relations**: `fetch_target` calls this after capturing a DOM snapshot. The resulting document data is then joined with accessibility-tree data to build `FrameSnapshot` objects.

*Call graph*: calls 1 internal fn (_parse_document); called by 1 (fetch_target); 5 external calls (__init__, get, as_list, as_map, as_str).


##### `fetch_target`  (lines 321–383)

```
async def fetch_target(cdp: Cdp, session_id: str, *, root_prefix: str, prefix_for: Callable[[str], str], base_origin: tuple[float, float]=(0.0, 0.0)) -> FrameSnapshot
```

**Purpose**: Fetches all page data for one browser target, such as the main page or a separately attached iframe. It combines element geometry from the DOM snapshot with accessibility nodes from Chrome.

**Data flow**: It receives a CDP connection, session id, frame-prefix rules, and a base origin. It enables needed browser domains, captures a DOM snapshot, reads device pixel ratio, parses documents, asks Chrome for each frame's accessibility tree, then returns a `FrameSnapshot` tree for that target.

**Call relations**: `BrowserPage._snapshot_target` calls this as the main data-gathering step. It hands off raw snapshot parsing to `parse_snapshot` and uses `Cdp.send` for the live browser calls.

*Call graph*: calls 3 internal fn (send, _float, parse_snapshot); called by 1 (_snapshot_target); 5 external calls (__init__, gather, as_list, as_map, as_str).


##### `_ax_value`  (lines 386–390)

```
def _ax_value(value: Json | None) -> str
```

**Purpose**: Extracts the plain value from an accessibility-tree field. Chrome often wraps values in small objects, and this helper unwraps them consistently.

**Data flow**: It receives a JSON value that may be a dictionary with a `value` field. It returns that inner value as text, or an empty string if there is no usable value.

**Call relations**: Both `render_page.render_node` and `render_markdown.walk` use it to read roles and names from accessibility nodes.

*Call graph*: called by 2 (walk, render_node); 1 external calls (get).


##### `_ax_property`  (lines 393–401)

```
def _ax_property(node: JsonDict, name: str) -> Json
```

**Purpose**: Finds a named property on an accessibility node, such as checked, disabled, level, or URL. These properties explain the state and behavior of page elements.

**Data flow**: It receives an accessibility node and a property name. It scans the node's property list and returns the property's real value, or nothing if it is not present.

**Call relations**: `_format_extras` uses it to build state text. The page and markdown renderers use it to skip hidden nodes, read heading levels, and include link targets.

*Call graph*: called by 3 (_format_extras, walk, render_node); 3 external calls (get, as_list, as_map).


##### `_should_skip`  (lines 404–410)

```
def _should_skip(node: JsonDict, role: str, name: str) -> bool
```

**Purpose**: Decides whether a structural accessibility node can be hidden from the action-oriented page outline. It removes empty wrapper roles while keeping nodes that carry important state.

**Data flow**: It receives a node, its role, and its name. If the role is a skip-worthy wrapper, has no name, and has no important state property, it returns true; otherwise false.

**Call relations**: `render_page.render_node` calls this while deciding whether to print a node or pass through to its children.

*Call graph*: called by 1 (render_node); 3 external calls (get, as_list, as_map).


##### `_truncate`  (lines 413–416)

```
def _truncate(text: str, max_len: int) -> str
```

**Purpose**: Shortens long text so page snapshots stay readable and do not overwhelm the model. It adds an ellipsis when text is cut.

**Data flow**: It receives text and a maximum length. It returns the original text if short enough, or a shortened version ending in `…`.

**Call relations**: `_image_name`, `_format_extras`, and `render_page.render_node` use it for element names, values, filenames, and extra state text.

*Call graph*: called by 3 (_format_extras, _image_name, render_node).


##### `_image_name`  (lines 419–426)

```
def _image_name(src: str | None) -> str
```

**Purpose**: Creates a fallback name for an image from its file name when the accessibility tree has no better label. This helps the model distinguish otherwise unnamed images.

**Data flow**: It receives an image source URL. It extracts the path's final filename, returns a shortened filename if it looks like a real file, or returns an empty string.

**Call relations**: `render_page.render_node` and `render_markdown.walk` call this when rendering images that do not have accessible names.

*Call graph*: calls 1 internal fn (_truncate); called by 2 (walk, render_node); 1 external calls (urlparse).


##### `_format_extras`  (lines 429–459)

```
def _format_extras(node: JsonDict, geom: NodeGeom | None) -> str
```

**Purpose**: Builds the extra state text shown after an element in the rendered page outline. This includes practical details like input type, current value, checked state, disabled state, and safe URLs.

**Data flow**: It receives an accessibility node and optional geometry metadata. It reads selected accessibility properties, filters out empty or unsafe values, truncates long strings, and returns a formatted suffix or an empty string.

**Call relations**: `render_page.render_node` calls this right before adding a visible line to the action-oriented page snapshot.

*Call graph*: calls 2 internal fn (_ax_property, _truncate); called by 1 (render_node).


##### `_frame_by_prefix`  (lines 462–469)

```
def _frame_by_prefix(root: FrameSnapshot, prefix: str) -> FrameSnapshot | None
```

**Purpose**: Finds the frame snapshot that matches a reference prefix. This lets the renderer start from a specific element reference inside the right iframe.

**Data flow**: It receives the root frame and a prefix string. It searches the root and child frames recursively, returning the matching frame or nothing.

**Call relations**: `render_page` uses this when a caller asks to render only the subtree under a specific reference.

*Call graph*: called by 1 (render_page).


##### `_node_by_backend`  (lines 472–476)

```
def _node_by_backend(frame: FrameSnapshot, backend_id: int) -> str | None
```

**Purpose**: Finds the accessibility node that corresponds to a browser backend node id. The backend id is what the reference stores, while rendering needs the accessibility node id.

**Data flow**: It receives a frame snapshot and a backend node id. It scans accessibility nodes until it finds one with that backend id and returns its accessibility id, or nothing.

**Call relations**: `render_page` uses this after `split_ref` and `_frame_by_prefix` so it can render from the referenced node.

*Call graph*: called by 1 (render_page).


##### `render_page`  (lines 479–575)

```
def render_page(root: FrameSnapshot, *, viewport: Size, model_size: Size | None=None, filter_type: str='all', max_depth: int=DEFAULT_MAX_DEPTH, ref: str | None=None) -> str | None
```

**Purpose**: Converts a `FrameSnapshot` into the compact, action-oriented text view shown to the model. The output includes roles, names, references, coordinates, and useful states.

**Data flow**: It receives a frame snapshot, viewport size, optional model size, filter options, depth limit, and optional starting ref. It scales coordinates, walks accessibility nodes, splices in child frames, filters noise, and returns the rendered lines as one string, or nothing if a requested ref cannot be found.

**Call relations**: `BrowserPage.tree` calls this after taking a fresh snapshot. Inside it, helper functions find frames and nodes for focused refs, and nested rendering functions do the tree walk.

*Call graph*: calls 3 internal fn (_frame_by_prefix, _node_by_backend, split_ref); called by 1 (tree); 1 external calls (effective_model_size).


##### `render_page.coord_str`  (lines 494–498)

```
def coord_str(geom: NodeGeom | None) -> str
```

**Purpose**: Formats an element's center point for the rendered page outline. The center is easier for a model or automation step to click than raw rectangle corners.

**Data flow**: It receives optional element geometry from the surrounding render. If bounds exist, it scales the rectangle center into model coordinates and returns text like `(x=10,y=20)`; otherwise it returns an empty string.

**Call relations**: `render_page.render_node` uses this when it writes each visible node line.


##### `render_page.splice`  (lines 500–503)

```
def splice(frame: FrameSnapshot, backend_id: int | None, depth: int) -> None
```

**Purpose**: Inserts a child frame's accessibility tree at the iframe node where it belongs. This makes embedded pages appear in the right place instead of as disconnected snapshots.

**Data flow**: It receives the current frame, an optional iframe backend id, and a render depth. If that backend id has a child frame with a root node, it starts rendering that child frame at the same place.

**Call relations**: `render_page.render_node.descend` calls this after walking normal children, so iframe contents are woven into the page outline.


##### `render_page.render_node`  (lines 505–559)

```
def render_node(frame: FrameSnapshot, ax_id: str, depth: int, parent_name: str) -> None
```

**Purpose**: Renders one accessibility node and then its children. It is the main tree-walking worker inside `render_page`.

**Data flow**: It receives a frame, accessibility node id, indentation depth, and parent name. It skips hidden or repeated nodes, reads role/name/state/geometry, decides whether the node should be printed or treated as transparent, writes a line when appropriate, and continues into children and child frames.

**Call relations**: `render_page` starts this at either the page root or a referenced node. It calls helpers such as `_ax_value`, `_ax_property`, `_should_skip`, `_format_extras`, and `_image_name` to turn raw accessibility data into useful text.

*Call graph*: calls 6 internal fn (_ax_property, _ax_value, _format_extras, _image_name, _should_skip, _truncate); 2 external calls (as_list, as_str).


##### `render_page.render_node.descend`  (lines 521–524)

```
def descend(child_depth: int, child_parent_name: str) -> None
```

**Purpose**: Continues rendering from a node into its child accessibility nodes and any iframe content attached to that node. It keeps the tree walk in the correct visual order.

**Data flow**: It receives the child indentation depth and the name to treat as the parent name. It renders each child id, then asks `splice` to include a child frame if the current node is an iframe.

**Call relations**: `render_page.render_node` calls this both when a node is transparent and after printing a visible node.


##### `render_markdown`  (lines 583–651)

```
def render_markdown(root: FrameSnapshot) -> str
```

**Purpose**: Creates a reading-oriented Markdown view of the page. Unlike the action outline, this focuses on content structure such as headings, paragraphs, links, images, and list items.

**Data flow**: It receives a root frame snapshot. It walks the accessibility tree, collects inline text into paragraphs, emits Markdown blocks, splices child frames, and returns the final Markdown string.

**Call relations**: `BrowserPage.markdown` calls this after taking a fresh snapshot. Nested helpers collect text blocks and walk the tree.

*Call graph*: called by 1 (markdown).


##### `render_markdown.emit`  (lines 592–595)

```
def emit(text: str) -> None
```

**Purpose**: Adds one finished Markdown block to the output, while avoiding empty or immediately duplicated blocks. It is like placing a clean paragraph onto the page.

**Data flow**: It receives a text block, trims whitespace, checks whether it is non-empty and not the same as the previous block, then appends it to the block list.

**Call relations**: `render_markdown.flush` and `render_markdown.walk` use this whenever inline text or a structural item is ready to become output.


##### `render_markdown.flush`  (lines 597–600)

```
def flush() -> None
```

**Purpose**: Turns accumulated inline words into a finished Markdown block. This prevents scattered text nodes from becoming a messy flat stream.

**Data flow**: It reads the current inline text list. If there is content, it joins the pieces with spaces, emits the result, and clears the inline list.

**Call relations**: `render_markdown.walk` calls this at boundaries such as headings, list items, images, block roles, and frame transitions.


##### `render_markdown.walk`  (lines 602–646)

```
def walk(frame: FrameSnapshot, ax_id: str, parent_name: str) -> None
```

**Purpose**: Walks the accessibility tree and decides how each node should appear in Markdown. It preserves reading structure instead of showing browser internals.

**Data flow**: It receives a frame, accessibility node id, and parent name. It skips hidden or duplicate text, turns headings, links, list items, images, and normal text into Markdown-friendly pieces, walks children, flushes at block boundaries, and splices child frames.

**Call relations**: `render_markdown` starts this at the root node. It uses `_ax_value`, `_ax_property`, and `_image_name` to read meaningful content from each accessibility node.

*Call graph*: calls 3 internal fn (_ax_property, _ax_value, _image_name); 2 external calls (as_list, as_str).


##### `BrowserPage.tree`  (lines 660–668)

```
async def tree(self, tab: PageTab, filter_type: str, ref: str | None) -> str | None
```

**Purpose**: Produces the action-oriented page tree for a tab. This is the method callers use when they want a model-readable map of clickable or visible page elements.

**Data flow**: It receives a tab, a filter type, and an optional reference. It takes a fresh snapshot, passes it to `render_page` with viewport and model sizing information, and returns the rendered text or nothing if the requested ref is invalid.

**Call relations**: It is a public method on `BrowserPage`. It combines `BrowserPage.snapshot` for data collection with `render_page` for presentation.

*Call graph*: calls 2 internal fn (snapshot, render_page).


##### `BrowserPage.markdown`  (lines 670–671)

```
async def markdown(self, tab: PageTab) -> str
```

**Purpose**: Produces a reading view of the current tab as Markdown. Callers use this when they care more about page content than click targets.

**Data flow**: It receives a tab, takes a fresh snapshot, renders that snapshot with `render_markdown`, and returns the Markdown text.

**Call relations**: It is the public wrapper around the snapshot pipeline and the Markdown renderer.

*Call graph*: calls 2 internal fn (snapshot, render_markdown).


##### `BrowserPage.resolve_ref`  (lines 673–686)

```
def resolve_ref(self, tab: PageTab, ref: str) -> tuple[FrameNode, int]
```

**Purpose**: Turns a model-facing reference string into the frame and backend node id needed for browser commands. It also catches invented or stale references and reports them as model mistakes.

**Data flow**: It receives a tab and a reference string. It parses the string, looks up the frame prefix in the tab's registered frame map, and returns the matching `FrameNode` plus backend id; invalid refs raise `HallucinationError` with guidance to re-read the page.

**Call relations**: `BrowserPage.ref_point` calls this before asking Chrome for an element's position. It relies on `split_ref` and on frame registrations created during `BrowserPage.snapshot`.

*Call graph*: calls 1 internal fn (split_ref); called by 1 (ref_point); 1 external calls (__init__).


##### `BrowserPage.ref_point`  (lines 688–714)

```
async def ref_point(self, tab: PageTab, ref: str) -> tuple[int, int]
```

**Purpose**: Finds a clickable center point for a referenced page element. This lets later automation act on the same element the model chose from the page tree.

**Data flow**: It receives a tab and reference. It resolves the reference, scrolls the element into view, asks Chrome for its content quadrilateral or box model, averages the four corners, adds the frame origin, and returns integer x/y coordinates. If Chrome cannot resolve the element, it raises `HallucinationError`.

**Call relations**: It builds on `BrowserPage.resolve_ref` and uses the browser connection for live geometry. `_coord_float_or_default` cleans up coordinate values returned by Chrome.

*Call graph*: calls 2 internal fn (resolve_ref, _coord_float_or_default); 3 external calls (__init__, as_list, as_map).


##### `BrowserPage.snapshot`  (lines 716–720)

```
async def snapshot(self, tab: PageTab) -> FrameSnapshot
```

**Purpose**: Takes a complete fresh snapshot of the tab and records which frame each reference prefix belongs to. This is the shared starting point for both page-tree and Markdown rendering.

**Data flow**: It receives a tab. It snapshots the main target, clears the tab's old reference-frame map, registers all frames from the new snapshot, and returns the root `FrameSnapshot`.

**Call relations**: `BrowserPage.tree` and `BrowserPage.markdown` call this before rendering. It delegates snapshot collection to `_snapshot_target` and reference bookkeeping to `_register_frames`.

*Call graph*: calls 2 internal fn (_register_frames, _snapshot_target); called by 2 (markdown, tree).


##### `BrowserPage._snapshot_target`  (lines 722–739)

```
async def _snapshot_target(self, tab: PageTab, session_id: str, root_prefix: str, origin: tuple[float, float], depth: int) -> FrameSnapshot
```

**Purpose**: Snapshots one browser target and, if allowed, attaches any separate iframe targets below it. A target is a separately controlled browser context, often used for out-of-process iframes.

**Data flow**: It receives a tab, session id, root prefix, origin, and recursion depth. It fetches the target's own frame data with `fetch_target`, then attaches out-of-process frames if the depth limit has not been reached, and returns the completed root frame snapshot.

**Call relations**: `BrowserPage.snapshot` uses it for the main page, and `_snapshot_oop` uses it for nested out-of-process frames. It hands separate-frame discovery to `_attach_oop_frames`.

*Call graph*: calls 2 internal fn (_attach_oop_frames, fetch_target); called by 2 (_snapshot_oop, snapshot).


##### `BrowserPage._attach_oop_frames`  (lines 741–749)

```
async def _attach_oop_frames(self, tab: PageTab, root: FrameSnapshot, depth: int) -> None
```

**Purpose**: Finds iframe placeholders that Chrome reported as separate targets and tries to fill them with real child snapshots. This keeps the final page tree from having blank holes where embedded pages should be.

**Data flow**: It receives a tab, root frame snapshot, and current depth. It walks the frame tree, checks each recorded out-of-process iframe backend id, snapshots the child if possible, and attaches it under the iframe node.

**Call relations**: `BrowserPage._snapshot_target` calls this after fetching a target. It delegates the work for each separate iframe to `_snapshot_oop`.

*Call graph*: calls 1 internal fn (_snapshot_oop); called by 1 (_snapshot_target).


##### `BrowserPage._snapshot_oop`  (lines 751–777)

```
async def _snapshot_oop(self, tab: PageTab, frame: FrameSnapshot, backend_id: int, depth: int) -> FrameSnapshot | None
```

**Purpose**: Snapshots one out-of-process iframe, if Chrome allows it. Out-of-process means the iframe is controlled through a different browser target than its parent page.

**Data flow**: It receives the tab, parent frame, iframe backend id, and depth. It asks Chrome which frame id belongs to the iframe, gets or creates a CDP session for that frame, computes the child origin from iframe bounds, and snapshots the child target. On CDP failures it logs a warning and returns nothing.

**Call relations**: `BrowserPage._attach_oop_frames` calls this for each separate iframe placeholder. It uses `_oop_session` to attach to the target and `_snapshot_target` to gather the child page data.

*Call graph*: calls 3 internal fn (_oop_session, _snapshot_target, frame_seq); called by 1 (_attach_oop_frames); 1 external calls (as_map).


##### `BrowserPage._register_frames`  (lines 779–782)

```
def _register_frames(self, tab: PageTab, frame: FrameSnapshot) -> None
```

**Purpose**: Records every frame prefix in the tab so future refs can be resolved. This is the lookup table that connects rendered references back to browser sessions.

**Data flow**: It receives a tab and a frame snapshot. It stores the frame id, session id, and origin under the frame prefix, then recursively registers child frames.

**Call relations**: `BrowserPage.snapshot` calls this after creating a fresh snapshot. `BrowserPage.resolve_ref` later depends on this registration.

*Call graph*: called by 1 (snapshot); 1 external calls (__init__).


##### `BrowserPage._oop_session`  (lines 784–797)

```
async def _oop_session(self, frame_id: str) -> str | None
```

**Purpose**: Gets a CDP session for an out-of-process iframe, reusing an existing one when possible. Caching avoids repeatedly attaching to the same iframe target.

**Data flow**: It receives a frame id. It first checks the browser session cache; if absent, it asks Chrome to attach to the target, initializes the new session, stores it in the cache, and returns the session id. If attachment fails, it returns nothing.

**Call relations**: `BrowserPage._snapshot_oop` calls this before snapshotting a separate iframe target. It uses the browser connection and then calls `BrowserPageSession.init_session` through the session object.

*Call graph*: called by 1 (_snapshot_oop); 1 external calls (as_str).


##### `_coord_float_or_default`  (lines 800–809)

```
def _coord_float_or_default(value: Json | None, default: float) -> float
```

**Purpose**: Converts coordinate values from Chrome into floats, accepting both numbers and numeric strings. It provides a default for missing values but rejects truly invalid data.

**Data flow**: It receives a coordinate value and a default. Numbers become floats, non-empty strings are parsed as floats, `None` becomes the default, and other types raise `ValidationError`.

**Call relations**: `BrowserPage.ref_point` uses this while averaging the four corners returned by Chrome for an element.

*Call graph*: called by 1 (ref_point); 1 external calls (__init__).
