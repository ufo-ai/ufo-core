# Browser Page Modeling and Content Inspection  `stage-12.1.2`

This stage is shared behind-the-scenes support for working with a live browser page. Its job is to turn a page that humans see visually into safer, simpler forms that an AI system can read, search, and act on. The page.py file is the main translator. It reads the live page and builds a clean text view, using stable-looking element references so the model can talk about buttons, links, and fields. It can later map those references back to real browser targets for actions like clicking. The content.py file exposes safe inspection tools on top of the browser session, such as reading the page’s element tree, extracting visible text, or finding items. The find.py file works like a finder in a document: it parses lines from the accessibility tree, which is the browser’s structured description of page elements, searches them, and verifies suggested matches. The coordinate.py file bridges vision and action by converting screenshot coordinates into real browser pixels and choosing screenshot sizes that keep images reliable for AI models.

## Files in this stage

### Content Inspection Interface
Safe browser-content tools expose page text, element trees, and element-finding operations to outside callers.

### `extensions/browser/ufo_ext_browser/bua/content.py`

`domain_logic` · `request handling`

This file exists so other parts of the system can ask simple questions about the current browser page without knowing how tabs, page readers, or browser internals work. It is like a front desk for page content: callers hand it a small request, and it fetches the right tab, asks the browser reader for information, trims oversized results, and returns plain JSON-friendly data.

The main class is BrowserContent. It depends on a BrowserContentSession, which is an agreed interface for getting a page tab, getting a page reader, and collecting tab details. The page reader can return either a structured page tree, useful for locating buttons and links, or markdown-style page text, useful for reading the page like an article.

The file also protects the rest of the system from runaway page size. A page tree is capped before being returned, and full page text is capped separately. The find feature searches the page tree for a user query. It can do a direct local search, or, if a completion function is provided, ask a language-model-style helper to interpret the tree and return likely matches. In both cases, it formats the result into a short summary plus match data.

#### Function details

##### `BrowserContentPageReader.tree`  (lines 23–23)

```
async def tree(self, tab: PageTab, filter_type: str, ref: str | None) -> str | None
```

**Purpose**: This is a promised capability, not an implementation here: a page reader must be able to return a structured view of a browser page. The structure is used when the system needs to understand page elements, such as links, buttons, forms, or a specific referenced element.

**Data flow**: It receives a browser tab, a filter saying what kind of elements to include, and optionally a reference to one element. An implementation reads the page and returns a text tree, or returns nothing if the requested element cannot be found.

**Call relations**: BrowserContent relies on this contract when it needs a page tree. The actual work is done by whatever browser session supplies the page reader.


##### `BrowserContentPageReader.markdown`  (lines 25–25)

```
async def markdown(self, tab: PageTab) -> str
```

**Purpose**: This is a promised capability for turning a browser page into readable text. It is used when the caller wants the page’s content rather than its clickable structure.

**Data flow**: It receives a browser tab. An implementation reads that tab and returns markdown-style text, which is plain text with lightweight formatting such as headings and links.

**Call relations**: BrowserContent.get_page_text depends on this kind of reader to fetch the human-readable page content after it has chosen the right tab.


##### `BrowserContentSession.page`  (lines 29–29)

```
async def page(self, tab_id: int | None=None) -> PageTab
```

**Purpose**: This is the session’s way to provide a browser tab for content operations. If a tab id is supplied, it should return that tab; otherwise it should choose the current or default page.

**Data flow**: It receives an optional tab id. The session turns that into a PageTab object that later methods can inspect.

**Call relations**: BrowserContent.tree and BrowserContent.get_page_text use this session hook before asking for any content, because every content operation needs a specific tab to read from.


##### `BrowserContentSession.page_reader`  (lines 31–31)

```
def page_reader(self) -> BrowserContentPageReader
```

**Purpose**: This gives BrowserContent the object that knows how to read the page. It keeps BrowserContent independent from the exact browser automation library underneath.

**Data flow**: It takes no input beyond the session itself. It returns an object that can produce a page tree or markdown text for a tab.

**Call relations**: After BrowserContent has selected a tab, it asks the session for this reader and then calls the reader’s content-reading methods.


##### `BrowserContentSession.tab_info`  (lines 33–33)

```
async def tab_info(self, tab: Any) -> JsonDict
```

**Purpose**: This provides extra details about a tab, such as information useful to include alongside page text. The exact fields are decided by the session implementation.

**Data flow**: It receives a tab object. It returns a JSON-style dictionary of tab metadata, which is then merged into the response.

**Call relations**: BrowserContent.get_page_text calls this after reading markdown so the final answer includes both the page text and tab context.


##### `BrowserContent.tree`  (lines 40–47)

```
async def tree(self, args: JsonDict, filter_type: str='all') -> str
```

**Purpose**: This fetches a structured text tree for a browser page, optionally focused on one referenced element. It is the shared helper used by both page-reading and page-finding features.

**Data flow**: It receives request arguments and a filter type. It reads the optional tab id and reference id from the arguments, converts the tab id into a usable number with _tab_id, asks the browser session for the tab, then asks the page reader for the tree. It returns the tree text, or a clear message if the referenced element was not found.

**Call relations**: BrowserContent.read_page calls this when it needs a tree to return to the caller. BrowserContent.find calls it when it needs searchable page structure. It hands tab-id cleanup to _tab_id and gets request values from the JSON-style argument dictionary.

*Call graph*: calls 1 internal fn (_tab_id); called by 2 (find, read_page); 1 external calls (get).


##### `BrowserContent.read_page`  (lines 49–53)

```
async def read_page(self, args: JsonDict) -> JsonDict
```

**Purpose**: This returns the page’s structured element tree in a bounded response. It is useful when a caller needs to inspect what is on the page without receiving an enormous payload.

**Data flow**: It receives request arguments, reads an optional filter value, accepts only known filters, and falls back to all elements if the filter is invalid. It calls BrowserContent.tree, cuts the returned tree to the maximum allowed length, and returns the tree plus a flag saying whether it was shortened.

**Call relations**: This is a public-facing operation built on BrowserContent.tree. It uses the request dictionary for options, then delegates the actual page reading to tree so the tab selection and reference lookup logic stay in one place.

*Call graph*: calls 1 internal fn (tree); 1 external calls (get).


##### `BrowserContent.get_page_text`  (lines 55–62)

```
async def get_page_text(self, args: JsonDict) -> JsonDict
```

**Purpose**: This returns the readable text of a page, rather than its element tree. It is meant for cases where the caller wants to understand the page content like an article or document.

**Data flow**: It receives request arguments, extracts and converts an optional tab id with _tab_id, asks the browser session for that tab, and asks the page reader for markdown text. It trims the text to the maximum allowed length, marks whether trimming happened, and adds tab information from the browser session.

**Call relations**: This operation shares tab-id conversion with BrowserContent.tree through _tab_id, but it does not use the tree path. Instead, it goes directly from selected tab to markdown text, then enriches the result with session-provided tab details.

*Call graph*: calls 1 internal fn (_tab_id); 1 external calls (get).


##### `BrowserContent.find`  (lines 64–77)

```
async def find(self, args: JsonDict, complete: FindCompleter | None=None) -> JsonDict
```

**Purpose**: This searches the page tree for elements matching a user’s query. It can either use a simple built-in parser or a supplied completion helper, which is a language-model-style function that can interpret the tree more flexibly.

**Data flow**: It receives request arguments and optionally a completion function. It reads the query as a required string, fetches the full page tree through BrowserContent.tree, and then searches. Without a completion helper, it parses the tree directly for matches. With one, it sends the query and a shortened tree to the helper, then resolves the helper’s reply back against the real tree. It returns match items and a human-readable summary.

**Call relations**: This function builds on BrowserContent.tree because finding starts with the same structured page view used by read_page. It then chooses one of two search paths: local parsing with parse_tree_matches, or assisted interpretation with the completion function followed by resolve_find_reply. In both paths it finishes by calling format_matches to produce the summary.

*Call graph*: calls 1 internal fn (tree); 5 external calls (format_matches, parse_tree_matches, resolve_find_reply, get, as_str).


##### `_tab_id`  (lines 80–89)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: This small helper turns a tab id from incoming JSON into either an integer or no tab id at all. It smooths over the fact that outside callers may send ids as numbers or strings.

**Data flow**: It receives a JSON value that might be an integer, float, string, empty value, or something else. Integers are returned as-is, floats and non-empty strings are converted to integers, and anything missing or unsupported becomes None.

**Call relations**: BrowserContent.tree and BrowserContent.get_page_text call this before asking the browser session for a page. That keeps tab-id cleanup in one place instead of repeating it in each content operation.

*Call graph*: called by 2 (get_page_text, tree).


### Visual Coordinate Mapping
Coordinate utilities translate model-visible screenshot points into real browser pixel locations and safe screenshot dimensions.

### `extensions/browser/ufo_ext_browser/bua/coordinate.py`

`util` · `cross-cutting during screenshot capture and browser action dispatch`

Browser automation with a vision model has a practical mismatch: the model may look at a resized screenshot, but the browser still needs real viewport pixel coordinates. This file is the small ruler that keeps those two worlds aligned. It defines simple immutable data shapes, Size for width and height and Coord for x and y, then provides helpers to move between coordinate systems.

For Claude-style vision models, very large images may be downscaled by the provider before the model reads them. If the system ignored that, a point chosen by the model could land in the wrong place on the page. compute_screenshot_dimensions pre-shrinks screenshots to the largest size Claude can see without extra server-side shrinking. For Gemini, the model reports positions on a fixed 0-to-1000 grid, like using graph paper laid over any image, so model_coordinate_space returns that fixed square size.

The two conversion functions are the main practical tools. model_to_viewport turns a model-selected point into browser pixels for dispatching input. viewport_to_model does the reverse, useful when browser pixel positions need to be expressed in the model’s coordinate system. The important idea is proportional scaling: a point halfway across the model image should become halfway across the browser viewport, even if the two have different pixel sizes.

#### Function details

##### `compute_screenshot_dimensions`  (lines 22–33)

```
def compute_screenshot_dimensions(viewport: Size) -> Size
```

**Purpose**: Figures out the largest screenshot size that a Claude-style vision model can receive without the provider quietly shrinking it further. This helps keep visual coordinates predictable.

**Data flow**: It takes the browser viewport size as input. It first scales the image down if its longest side is too large, then shrinks it again if the total pixel count is still above the model limit. It returns a new Size containing the safe width and height.

**Call relations**: When no explicit model coordinate size is supplied, effective_model_size calls this function to decide what size the model is really reasoning over. Its result then becomes the basis for converting points between model space and browser space.

*Call graph*: called by 1 (effective_model_size); 1 external calls (__init__).


##### `effective_model_size`  (lines 36–38)

```
def effective_model_size(viewport: Size, model_size: Size | None=None) -> Size
```

**Purpose**: Decides which coordinate space should be treated as the model’s view of the page. It uses a caller-provided size when one exists, otherwise it computes the safe screenshot size.

**Data flow**: It receives the browser viewport size and, optionally, a model coordinate size. If the optional size is present, it passes that through unchanged. If not, it asks compute_screenshot_dimensions to create a safe screenshot size and returns that.

**Call relations**: Both model_to_viewport and viewport_to_model call this before doing their scaling. It acts like the shared checkpoint that makes sure both directions of conversion agree on the model’s coordinate system.

*Call graph*: calls 1 internal fn (compute_screenshot_dimensions); called by 2 (model_to_viewport, viewport_to_model).


##### `model_to_viewport`  (lines 41–47)

```
def model_to_viewport(coord: Coord, viewport: Size, model_size: Size | None=None) -> Coord
```

**Purpose**: Converts a point chosen by the model into the real browser viewport pixel where an action, such as a click, should happen. This is needed because the model may not be using the same pixel dimensions as the browser.

**Data flow**: It receives a coordinate in model space, the real viewport size, and optionally the model’s coordinate size. It finds the effective model size, scales the x value by the viewport-to-model width ratio, scales the y value by the viewport-to-model height ratio, and returns a Coord in browser pixels.

**Call relations**: This function calls effective_model_size to learn what scale the model used. After that, it creates the converted Coord that can be handed to browser input code for action dispatch.

*Call graph*: calls 1 internal fn (effective_model_size); 1 external calls (__init__).


##### `model_coordinate_space`  (lines 50–55)

```
def model_coordinate_space(model: str | None) -> Size | None
```

**Purpose**: Returns the special coordinate space used by a given model family, when one is known. In particular, it recognizes Gemini models, which report points on a fixed 1000 by 1000 grid instead of screenshot pixels.

**Data flow**: It receives a model name, or nothing. If the name contains “gemini” in any letter case, it returns a Size of 1000 by 1000. Otherwise it returns None, meaning the rest of the code should use the computed screenshot dimensions instead.

**Call relations**: This helper is used before coordinate conversion to choose whether a fixed model grid should be supplied as an override. Its returned Size, when present, can be passed into model_to_viewport or viewport_to_model through their model_size input.

*Call graph*: 1 external calls (__init__).


##### `viewport_to_model`  (lines 58–64)

```
def viewport_to_model(coord: Coord, viewport: Size, model_size: Size | None=None) -> Coord
```

**Purpose**: Converts a real browser viewport pixel into the coordinate system the model uses. This is useful when browser positions need to be described back to the model or compared with model-space points.

**Data flow**: It receives a browser pixel coordinate, the viewport size, and optionally the model’s coordinate size. It finds the effective model size, scales x by the model-to-viewport width ratio, scales y by the model-to-viewport height ratio, and returns a Coord in model space.

**Call relations**: Like model_to_viewport, it first calls effective_model_size so it uses the same idea of model size as the rest of the file. It then creates the converted Coord for any later logic that expects model-space positions.

*Call graph*: calls 1 internal fn (effective_model_size); 1 external calls (__init__).


### Element Search and Page Modeling
Search helpers interpret accessibility-tree text while page modeling converts live browser pages into readable text and actionable element references.

### `extensions/browser/ufo_ext_browser/bua/find.py`

`domain_logic` · `request handling`

A browser accessibility tree is a text outline of what is on a web page, such as buttons, links, text fields, and their labels. This file is the “reader” for that outline. It knows the simple line format produced elsewhere, for example a role, a visible name, a stable reference like `e12`, and optional screen coordinates.

The main problem it solves is trust and usability. A search query may be answered either by a simple local text match or by an AI model reading the tree. But an AI reply cannot be trusted blindly: it might invent or mistype a reference. So this file always grounds results back in the actual tree. In plain terms, it checks the guest list before letting any suggested match through the door.

First, `tree_entries` scans the tree text and extracts only valid element lines into small dictionaries. `parse_tree_matches` uses that parsed list for a basic keyword search. `resolve_find_reply` takes a model’s reply, keeps only references that truly exist in the tree, removes duplicates, and fills in trusted role, name, and coordinate data from the tree itself. Finally, `format_matches` turns the structured matches into readable lines. The file also sets limits, such as returning at most 20 matches, so search results stay manageable.

#### Function details

##### `tree_entries`  (lines 31–50)

```
def tree_entries(tree: str) -> list[JsonDict]
```

**Purpose**: This function reads the text accessibility tree and extracts the element records that other search functions can safely use. It ignores lines that do not look like real tree elements with a valid reference.

**Data flow**: It takes the full tree as one string. For each line, it looks for the element role, optional name, required reference, and optional coordinates. It returns a list of dictionaries containing the reference, role, name, coordinates, and a lowercased copy of the original line for searching.

**Call relations**: This is the shared parser used before searching or validating results. `parse_tree_matches` calls it to get searchable entries, and `resolve_find_reply` calls it to build the trusted list of references that an outside reply must match.

*Call graph*: called by 2 (parse_tree_matches, resolve_find_reply).


##### `_match_payload`  (lines 53–60)

```
def _match_payload(entry: JsonDict, reason: str) -> JsonDict
```

**Purpose**: This small helper creates the standard result shape for a found element. It keeps only the useful public details: reference, role, name, coordinates, and the reason it matched.

**Data flow**: It receives one parsed tree entry and a reason string. It copies the element’s trusted data into a new dictionary and attaches the reason. The returned dictionary is what later code treats as one search match.

**Call relations**: Both `parse_tree_matches` and `resolve_find_reply` use this helper so their results look the same, whether the match came from local keyword search or from a validated model reply.

*Call graph*: called by 2 (parse_tree_matches, resolve_find_reply).


##### `parse_tree_matches`  (lines 63–71)

```
def parse_tree_matches(tree: str, query: str) -> list[JsonDict]
```

**Purpose**: This function performs a simple local search through the accessibility tree using words from the user’s query. It is useful when the desired element can be found by direct text matching.

**Data flow**: It takes the tree text and a query string. It breaks the query into lowercase words and keeps words longer than one character. Then it parses the tree with `tree_entries` and keeps entries whose original line contains every query term. It returns up to the configured maximum number of match dictionaries.

**Call relations**: This is the straightforward search path. It relies on `tree_entries` to understand the tree format, then uses `_match_payload` to produce match records in the same format as other find results.

*Call graph*: calls 2 internal fn (_match_payload, tree_entries); 1 external calls (findall).


##### `resolve_find_reply`  (lines 74–102)

```
def resolve_find_reply(reply: str, tree: str) -> tuple[list[JsonDict], bool]
```

**Purpose**: This function checks a search reply, such as one produced by an AI model, against the actual accessibility tree. Its job is to prevent made-up or mistaken element references from being accepted.

**Data flow**: It takes the reply text and the original tree text. It first parses the tree into a lookup table by reference. Then it reads each reply line, skips empty or invalid lines, notices a `MORE` marker, stops on `NO_MATCHES`, extracts a reference when present, and keeps it only if that reference exists in the tree and has not already appeared. It returns the trusted matches plus a true-or-false flag saying whether more matches were reported.

**Call relations**: This is the safety gate for model-assisted search. It calls `tree_entries` to learn which references are real, and `_match_payload` to build results using tree-owned data rather than trusting the reply’s memory of the element.

*Call graph*: calls 2 internal fn (_match_payload, tree_entries).


##### `format_matches`  (lines 105–114)

```
def format_matches(matches: list[JsonDict], has_more: bool=False) -> str
```

**Purpose**: This function turns match dictionaries into a compact text display that a person or calling system can read easily. It also adds a note when more results exist than were shown.

**Data flow**: It takes a list of match dictionaries and an optional flag saying whether there are more matches. For each match, it builds a line with the reference, role, name, and coordinates, and adds the reason if one is present. It returns all lines joined into one string.

**Call relations**: This is the final presentation step after matching or reply validation has already happened. It does not search or verify anything itself; it simply turns the structured results from earlier functions into readable output.


### `extensions/browser/ufo_ext_browser/bua/page.py`

`domain_logic` · `request handling`

A browser page is visually rich, but an AI agent needs a simpler map: what is on the page, what can be clicked or typed into, where things are, and which frame they belong to. This file builds that map from Chrome’s debugging interface, called CDP (Chrome DevTools Protocol, a control channel for asking the browser about pages). It combines two browser views: the DOM snapshot, which gives element geometry and frame relationships, and the accessibility tree, which gives human-facing roles and names such as “button”, “textbox”, or “heading”.

Frames are important because a page can contain embedded pages, like documents inside windows. The file splices child frame trees under their iframe nodes so the result reads like one page. It also supports out-of-process iframes, which Chrome runs in separate debugging sessions.

The main output is either an ARIA-like tree with refs such as `e12` or `f1e3`, plus center coordinates and useful state, or a Markdown reading view. Those refs are like coat-check tickets: the model receives them when reading the page, and later hands one back so the system can find the real element. Without this file, the agent would see either raw browser data that is too noisy or no reliable way to connect page text to later actions.

#### Function details

##### `split_ref`  (lines 116–120)

```
def split_ref(ref: str) -> tuple[str, int] | None
```

**Purpose**: Checks and splits a browser element reference such as `e12` or `f1e3`. It is used to make sure the model is using a reference that came from the page view, not an invented one.

**Data flow**: It receives a reference string. If the string matches the expected pattern, it separates the frame prefix from the numeric backend element id and returns both. If it does not match, it returns nothing.

**Call relations**: When rendering only part of a page, `render_page` uses this to find the requested starting element. When an action needs to use a ref, `BrowserPage.resolve_ref` uses it first to reject malformed refs.

*Call graph*: called by 2 (resolve_ref, render_page).


##### `Cdp.send`  (lines 124–126)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: Defines the shape of the browser command channel used by this file. A real object implementing this method sends commands to Chrome and returns JSON-like results.

**Data flow**: It takes a CDP method name, optional parameters, and an optional session id. The real implementation sends that request to the browser and returns the browser’s response as a dictionary.

**Call relations**: `fetch_target` relies on this command channel to ask Chrome for snapshots and accessibility data. Other code, such as settling page tasks, can use the same interface.

*Call graph*: called by 2 (fetch_target, _flush_page_tasks).


##### `PageTab.frame_seq`  (lines 161–161)

```
def frame_seq(self, frame_id: str) -> int
```

**Purpose**: Defines how a tab assigns a short number to each frame. Those numbers become part of refs, so elements in different frames do not collide.

**Data flow**: It receives a browser frame id and returns a stable sequence number for that frame within the tab.

**Call relations**: `BrowserPage._snapshot_oop` calls this when it needs to give an out-of-process iframe its own frame prefix.

*Call graph*: called by 1 (_snapshot_oop).


##### `BrowserPageSession.connection`  (lines 168–168)

```
def connection(self) -> Cdp
```

**Purpose**: Defines how page code gets the live CDP connection for the browser session. This keeps snapshot code independent from the concrete browser connection class.

**Data flow**: It reads no explicit arguments beyond the session object and returns an object that can send CDP commands.

**Call relations**: Methods on `BrowserPage` call this whenever they need to query or control Chrome, especially while taking snapshots or resolving element positions.


##### `BrowserPageSession.init_session`  (lines 170–170)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Defines the setup step for a newly attached browser session. It lets the wider browser layer prepare a session before this file asks it for page data.

**Data flow**: It receives a CDP session id. The real implementation performs whatever initialization the browser system requires and returns when that session is ready.

**Call relations**: `BrowserPage._oop_session` uses this after attaching to an out-of-process iframe, before saving and reusing that iframe session.


##### `_float`  (lines 190–195)

```
def _float(value: Json | None, default: float=0.0) -> float
```

**Purpose**: Safely converts a JSON value into a floating-point number. It protects the snapshot code from missing or oddly typed browser fields.

**Data flow**: It receives a value and a default number. If the value is already numeric, it returns it as a float; otherwise it returns the default.

**Call relations**: `_parse_document` uses it for scroll positions and bounds. `fetch_target` uses it to read the page’s device pixel ratio.

*Call graph*: called by 2 (_parse_document, fetch_target).


##### `_attr`  (lines 198–203)

```
def _attr(strings: list[str], attrs: list[Json], name: str) -> str | None
```

**Purpose**: Looks up one HTML attribute, such as `type` or `src`, inside Chrome’s compact snapshot format. Chrome stores attribute names and values as indexes into a shared string table, so this helper hides that awkwardness.

**Data flow**: It receives the shared strings list, an attribute index list, and the attribute name to find. It walks name-value pairs and returns the matching text value, or nothing if the attribute is absent.

**Call relations**: `_parse_document` calls this while collecting useful details for inputs, images, and iframes.

*Call graph*: called by 1 (_parse_document).


##### `_parse_document`  (lines 206–266)

```
def _parse_document(doc: JsonDict, strings: list[str], dpr: float) -> _RawDoc
```

**Purpose**: Turns one raw DOM snapshot document into easier local data about nodes, geometry, attributes, and iframe links. It is the first cleanup pass over Chrome’s compact snapshot data.

**Data flow**: It receives one document entry, the shared string table, and the device pixel ratio. It extracts backend node ids, converts bounds into CSS pixels, records scroll-adjusted positions, notes pointer cursors, input types, image sources, and iframe document relationships, then returns a `_RawDoc`.

**Call relations**: `parse_snapshot` calls this once for each document that Chrome included in the snapshot. It uses small helpers like `_float` and `_attr` to keep parsing safe and readable.

*Call graph*: calls 2 internal fn (_attr, _float); called by 1 (parse_snapshot); 6 external calls (__init__, __init__, get, as_int, as_list, as_map).


##### `parse_snapshot`  (lines 269–318)

```
def parse_snapshot(snapshot: JsonDict, dpr: float, base_origin: tuple[float, float]=(0.0, 0.0)) -> list[DocData]
```

**Purpose**: Combines the raw DOM snapshot documents into frame-level document data with correct page coordinates. This is where child documents are placed at the visual position of their iframe.

**Data flow**: It receives Chrome’s full DOM snapshot, a device pixel ratio, and a starting origin. It parses each document, calculates each child document’s origin through the iframe chain, adjusts node bounds into shared page coordinates, separates normal iframe documents from out-of-process iframe placeholders, and returns a list of `DocData` records.

**Call relations**: `fetch_target` calls this after capturing a DOM snapshot. The resulting documents are then matched with accessibility trees to make full frame snapshots.

*Call graph*: calls 1 internal fn (_parse_document); called by 1 (fetch_target); 5 external calls (__init__, get, as_list, as_map, as_str).


##### `fetch_target`  (lines 321–383)

```
async def fetch_target(cdp: Cdp, session_id: str, *, root_prefix: str, prefix_for: Callable[[str], str], base_origin: tuple[float, float]=(0.0, 0.0)) -> FrameSnapshot
```

**Purpose**: Fetches all page data for one CDP target or session and builds a connected frame snapshot. A target is one browser-controlled page or frame session.

**Data flow**: It receives a CDP connection, a session id, a root ref prefix, a function for making child prefixes, and a base origin. It enables browser snapshot features, captures DOM geometry, reads device pixel ratio, parses documents, fetches accessibility trees for each frame, builds `FrameSnapshot` objects, links child frames under iframe backend ids, and returns the root frame snapshot.

**Call relations**: `BrowserPage._snapshot_target` calls this as the core data-gathering step. It delegates DOM cleanup to `parse_snapshot` and sends several CDP commands through `Cdp.send`.

*Call graph*: calls 3 internal fn (send, _float, parse_snapshot); called by 1 (_snapshot_target); 5 external calls (__init__, gather, as_list, as_map, as_str).


##### `_ax_value`  (lines 386–390)

```
def _ax_value(value: Json | None) -> str
```

**Purpose**: Extracts the plain value from an accessibility tree field. Chrome wraps many accessibility values inside small objects, and this helper unwraps them.

**Data flow**: It receives a JSON value. If it is a dictionary with a `value` entry, it returns that entry as text; otherwise it returns an empty string.

**Call relations**: Both `render_page.render_node` and `render_markdown.walk` use this when reading a node’s role and human-visible name.

*Call graph*: called by 2 (walk, render_node); 1 external calls (get).


##### `_ax_property`  (lines 393–401)

```
def _ax_property(node: JsonDict, name: str) -> Json
```

**Purpose**: Finds one named accessibility property on a node, such as `checked`, `disabled`, `url`, or `level`. These properties describe state that plain text alone would miss.

**Data flow**: It receives an accessibility node and a property name. It scans the node’s property list and returns the property’s underlying value if present, or nothing if not present.

**Call relations**: `_format_extras` uses it to add state to rendered page lines. The page and Markdown renderers use it to skip hidden nodes and format headings, links, and other special cases.

*Call graph*: called by 3 (_format_extras, walk, render_node); 3 external calls (get, as_list, as_map).


##### `_should_skip`  (lines 404–410)

```
def _should_skip(node: JsonDict, role: str, name: str) -> bool
```

**Purpose**: Decides whether a structural accessibility node should be hidden in the page tree. This keeps empty wrappers from cluttering the model’s view.

**Data flow**: It receives an accessibility node, its role, and its name. If the role is a skippable wrapper, has no name, and carries no important state, it returns true; otherwise false.

**Call relations**: `render_page.render_node` uses this while deciding whether to print a node or pass through to its children.

*Call graph*: called by 1 (render_node); 3 external calls (get, as_list, as_map).


##### `_truncate`  (lines 413–416)

```
def _truncate(text: str, max_len: int) -> str
```

**Purpose**: Shortens long text so rendered page output stays compact. This prevents huge names or values from overwhelming the model.

**Data flow**: It receives text and a maximum length. If the text fits, it returns it unchanged; otherwise it cuts it down and adds an ellipsis.

**Call relations**: `_image_name`, `_format_extras`, and `render_page.render_node` use it before including names or values in the page view.

*Call graph*: called by 3 (_format_extras, _image_name, render_node).


##### `_image_name`  (lines 419–426)

```
def _image_name(src: str | None) -> str
```

**Purpose**: Creates a readable fallback name for an image from its URL. This helps when an image has no accessibility name but its filename is useful.

**Data flow**: It receives an optional image source URL. It extracts the last path segment, keeps it only if it looks like a filename with an extension, truncates it if needed, and returns that text or an empty string.

**Call relations**: `render_page.render_node` and `render_markdown.walk` use this when formatting image nodes with missing alt text.

*Call graph*: calls 1 internal fn (_truncate); called by 2 (walk, render_node); 1 external calls (urlparse).


##### `_format_extras`  (lines 429–459)

```
def _format_extras(node: JsonDict, geom: NodeGeom | None) -> str
```

**Purpose**: Builds the extra state text that appears after a rendered page node. This is where details like checked status, input type, current value, disabled state, and safe URLs are added.

**Data flow**: It receives an accessibility node and optional geometry/details from the DOM snapshot. It reads selected accessibility properties, trims long values, filters unsafe or unhelpful URL forms, and returns a space-prefixed suffix string or an empty string.

**Call relations**: `render_page.render_node` calls this immediately before adding a line to the page tree.

*Call graph*: calls 2 internal fn (_ax_property, _truncate); called by 1 (render_node).


##### `_frame_by_prefix`  (lines 462–469)

```
def _frame_by_prefix(root: FrameSnapshot, prefix: str) -> FrameSnapshot | None
```

**Purpose**: Finds the frame snapshot that owns a given ref prefix. For example, an empty prefix means the main frame, while `f1` points to a child frame.

**Data flow**: It receives the root frame and a prefix. It checks the root, then searches child frames recursively, returning the matching frame or nothing.

**Call relations**: `render_page` uses this when asked to render a subtree starting from a specific ref.

*Call graph*: called by 1 (render_page).


##### `_node_by_backend`  (lines 472–476)

```
def _node_by_backend(frame: FrameSnapshot, backend_id: int) -> str | None
```

**Purpose**: Finds the accessibility node that corresponds to a DOM backend node id. This bridges the browser’s DOM identity and the accessibility tree identity.

**Data flow**: It receives a frame snapshot and a backend element id. It scans accessibility nodes until it finds one whose backend DOM node id matches, then returns that accessibility node id or nothing.

**Call relations**: `render_page` uses this after `split_ref` and `_frame_by_prefix` so it can start rendering at the referenced element.

*Call graph*: called by 1 (render_page).


##### `render_page`  (lines 479–575)

```
def render_page(root: FrameSnapshot, *, viewport: Size, model_size: Size | None=None, filter_type: str='all', max_depth: int=DEFAULT_MAX_DEPTH, ref: str | None=None) -> str | None
```

**Purpose**: Turns a frame snapshot into the main machine-readable page tree. The output resembles Playwright’s ARIA snapshot format, extended with refs, center coordinates, and useful state.

**Data flow**: It receives the root snapshot, viewport size, optional model size, filter choice, depth limit, and optional starting ref. It computes coordinate scaling, chooses the root or referenced node, walks the accessibility tree, splices child frames at iframe nodes, filters noise, and returns newline-separated text or nothing if the requested ref cannot be found.

**Call relations**: `BrowserPage.tree` calls this after taking a snapshot. Inside it, helper functions find frame prefixes and backend nodes, while nested functions format coordinates and walk the tree.

*Call graph*: calls 3 internal fn (_frame_by_prefix, _node_by_backend, split_ref); called by 1 (tree); 1 external calls (effective_model_size).


##### `render_page.coord_str`  (lines 494–498)

```
def coord_str(geom: NodeGeom | None) -> str
```

**Purpose**: Formats an element’s center point for the rendered page tree. The center is easier for an agent to click than a full rectangle.

**Data flow**: It receives optional geometry captured from the DOM snapshot. If bounds are present, it scales the center into model-space coordinates and returns text like `(x=...,y=...)`; otherwise it returns an empty string.

**Call relations**: `render_page.render_node` calls this when building each visible line of the page tree.


##### `render_page.splice`  (lines 500–503)

```
def splice(frame: FrameSnapshot, backend_id: int | None, depth: int) -> None
```

**Purpose**: Inserts a child frame’s accessibility tree at the iframe node where it visually belongs. This makes embedded pages read as part of the surrounding page.

**Data flow**: It receives a frame, an optional iframe backend id, and a render depth. If that backend id has a child frame with a root node, it asks `render_node` to render the child frame there.

**Call relations**: `render_page.render_node.descend` calls this after walking normal child nodes, so iframe contents appear in the right spot.


##### `render_page.render_node`  (lines 505–559)

```
def render_node(frame: FrameSnapshot, ax_id: str, depth: int, parent_name: str) -> None
```

**Purpose**: Walks one accessibility node and decides whether to print it, skip it, or pass through to its children. This is the heart of the page tree renderer.

**Data flow**: It receives a frame, an accessibility node id, the current indentation depth, and the parent’s name. It avoids loops, ignores hidden or noisy nodes, applies filters such as interactive-only or viewport-only, builds refs and display names, adds coordinates and extra state, appends a line when appropriate, then walks children and any spliced frame.

**Call relations**: `render_page` starts rendering by calling this. It uses helpers such as `_ax_value`, `_ax_property`, `_should_skip`, `_format_extras`, `_image_name`, and `_truncate` while producing the output.

*Call graph*: calls 6 internal fn (_ax_property, _ax_value, _format_extras, _image_name, _should_skip, _truncate); 2 external calls (as_list, as_str).


##### `render_page.render_node.descend`  (lines 521–524)

```
def descend(child_depth: int, child_parent_name: str) -> None
```

**Purpose**: Continues rendering through a node’s children without repeating the surrounding decision logic. It is used both for normal descent and for transparent wrapper nodes.

**Data flow**: It receives the depth to use for child nodes and the name children should compare against. It renders each listed child node, then asks `splice` to insert any child frame attached to the current backend id.

**Call relations**: `render_page.render_node` calls this when a node should be skipped but its children should remain visible, and again after printing a node to render its contents.


##### `render_markdown`  (lines 583–651)

```
def render_markdown(root: FrameSnapshot) -> str
```

**Purpose**: Turns a frame snapshot into a cleaner reading view in Markdown. It is meant for understanding page content rather than choosing exact clickable elements.

**Data flow**: It receives the root frame snapshot. It walks the accessibility tree, collects text into paragraphs, preserves headings, links, bullets, and images where possible, splices iframe content, and returns Markdown blocks separated by blank lines.

**Call relations**: `BrowserPage.markdown` calls this after taking a snapshot. Its nested helpers collect and flush text while the walker interprets accessibility roles.

*Call graph*: called by 1 (markdown).


##### `render_markdown.emit`  (lines 592–595)

```
def emit(text: str) -> None
```

**Purpose**: Adds one completed Markdown block if it has real content and is not a duplicate of the previous block. This keeps the reading view tidy.

**Data flow**: It receives text, trims whitespace, checks it against the existing block list, and appends it when useful.

**Call relations**: `render_markdown.flush` and `render_markdown.walk` call this whenever they finish a paragraph, heading, list item, image, or named block.


##### `render_markdown.flush`  (lines 597–600)

```
def flush() -> None
```

**Purpose**: Turns accumulated inline text into a Markdown block. It is like ending a paragraph before starting a new section.

**Data flow**: It reads the current inline text buffer. If there is text, it joins the pieces with spaces, emits the result, and clears the buffer.

**Call relations**: `render_markdown.walk` calls this before headings, blocks, list items, images, and frame boundaries so unrelated text does not run together.


##### `render_markdown.walk`  (lines 602–646)

```
def walk(frame: FrameSnapshot, ax_id: str, parent_name: str) -> None
```

**Purpose**: Walks the accessibility tree and translates roles into Markdown structure. It turns headings into `#` lines, links into Markdown links, list items into bullets, and ordinary names into inline text.

**Data flow**: It receives a frame, an accessibility node id, and the parent name. It skips repeated or hidden nodes, reads role/name/state, appends or emits Markdown content, recursively visits children, flushes at block boundaries, and enters child frames when an iframe has one.

**Call relations**: `render_markdown` starts this walker at the root accessibility node. It uses `_ax_value`, `_ax_property`, and `_image_name` to interpret browser data.

*Call graph*: calls 3 internal fn (_ax_property, _ax_value, _image_name); 2 external calls (as_list, as_str).


##### `BrowserPage.tree`  (lines 660–668)

```
async def tree(self, tab: PageTab, filter_type: str, ref: str | None) -> str | None
```

**Purpose**: Provides the high-level API for getting the rendered page tree for a tab. This is what other parts of the agent use when they need refs and coordinates.

**Data flow**: It receives a tab, a filter type, and an optional ref. It first takes a fresh snapshot of the tab, then passes that snapshot to `render_page` along with viewport and model-size information, and returns the rendered text.

**Call relations**: This method ties together live browser capture through `BrowserPage.snapshot` and text rendering through `render_page`.

*Call graph*: calls 2 internal fn (snapshot, render_page).


##### `BrowserPage.markdown`  (lines 670–671)

```
async def markdown(self, tab: PageTab) -> str
```

**Purpose**: Provides the high-level API for getting a Markdown reading view of a tab. It is useful when the agent needs page content more than action targets.

**Data flow**: It receives a tab, takes a fresh snapshot, renders that snapshot as Markdown, and returns the Markdown string.

**Call relations**: This method connects `BrowserPage.snapshot` with `render_markdown`.

*Call graph*: calls 2 internal fn (snapshot, render_markdown).


##### `BrowserPage.resolve_ref`  (lines 673–686)

```
def resolve_ref(self, tab: PageTab, ref: str) -> tuple[FrameNode, int]
```

**Purpose**: Checks that a model-provided ref is valid for the current tab and finds the frame information behind it. It raises a clear “do not invent refs” error when the ref is bad.

**Data flow**: It receives a tab and a ref string. It parses the ref, looks up the frame prefix in the tab’s registered frame map, and returns the matching `FrameNode` plus backend element id. If parsing or lookup fails, it raises `HallucinationError`.

**Call relations**: `BrowserPage.ref_point` calls this before asking Chrome for an element’s position.

*Call graph*: calls 1 internal fn (split_ref); called by 1 (ref_point); 1 external calls (__init__).


##### `BrowserPage.ref_point`  (lines 688–714)

```
async def ref_point(self, tab: PageTab, ref: str) -> tuple[int, int]
```

**Purpose**: Finds the clickable center point for an element reference. It scrolls the element into view first, then asks Chrome for its shape.

**Data flow**: It receives a tab and a ref. It resolves the ref to a frame and backend id, sends CDP commands to scroll and fetch content quads or a box model, converts coordinate values to floats, averages the four corners, adds the frame origin, and returns an `(x, y)` point. If Chrome cannot resolve the element, it raises `HallucinationError`.

**Call relations**: Action code can call this after the model chooses a ref from `BrowserPage.tree`. It uses `resolve_ref` for validation and `_coord_float_or_default` for numeric cleanup.

*Call graph*: calls 2 internal fn (resolve_ref, _coord_float_or_default); 3 external calls (__init__, as_list, as_map).


##### `BrowserPage.snapshot`  (lines 716–720)

```
async def snapshot(self, tab: PageTab) -> FrameSnapshot
```

**Purpose**: Captures a fresh, connected snapshot of the tab and records which frame prefixes are currently valid. This is the foundation for both page rendering and later ref resolution.

**Data flow**: It receives a tab. It snapshots the main target, clears the tab’s old ref-frame map, registers every frame in the new snapshot, and returns the root `FrameSnapshot`.

**Call relations**: `BrowserPage.tree` and `BrowserPage.markdown` call this before rendering. It delegates target capture to `_snapshot_target` and frame registration to `_register_frames`.

*Call graph*: calls 2 internal fn (_register_frames, _snapshot_target); called by 2 (markdown, tree).


##### `BrowserPage._snapshot_target`  (lines 722–739)

```
async def _snapshot_target(self, tab: PageTab, session_id: str, root_prefix: str, origin: tuple[float, float], depth: int) -> FrameSnapshot
```

**Purpose**: Captures one browser target and, if allowed by depth limits, attaches any out-of-process child frames. It is the recursive snapshot driver.

**Data flow**: It receives a tab, session id, frame prefix, origin, and current depth. It calls `fetch_target` to capture the target, then scans for out-of-process iframes if the maximum frame depth has not been reached, and returns the completed root snapshot for that target.

**Call relations**: `BrowserPage.snapshot` calls this for the main tab. `_snapshot_oop` calls it again for child iframe sessions.

*Call graph*: calls 2 internal fn (_attach_oop_frames, fetch_target); called by 2 (_snapshot_oop, snapshot).


##### `BrowserPage._attach_oop_frames`  (lines 741–749)

```
async def _attach_oop_frames(self, tab: PageTab, root: FrameSnapshot, depth: int) -> None
```

**Purpose**: Walks a snapshot looking for iframes that Chrome did not include in the same target because they run separately. It tries to snapshot each one and attach it in place.

**Data flow**: It receives a tab, a root frame snapshot, and the current depth. It traverses the frame tree, asks `_snapshot_oop` for each out-of-process iframe backend id, and stores successful child snapshots under that iframe.

**Call relations**: `BrowserPage._snapshot_target` calls this after the ordinary snapshot is built.

*Call graph*: calls 1 internal fn (_snapshot_oop); called by 1 (_snapshot_target).


##### `BrowserPage._snapshot_oop`  (lines 751–777)

```
async def _snapshot_oop(self, tab: PageTab, frame: FrameSnapshot, backend_id: int, depth: int) -> FrameSnapshot | None
```

**Purpose**: Captures one out-of-process iframe, if Chrome can describe and attach to it. Out-of-process means Chrome runs that iframe under a separate debugging session.

**Data flow**: It receives the parent tab, parent frame snapshot, iframe backend id, and depth. It asks Chrome which frame id belongs to the iframe, obtains or creates a CDP session for that frame, calculates the child origin from the iframe bounds, then snapshots the child target. If any required step fails, it logs a warning or returns nothing.

**Call relations**: `BrowserPage._attach_oop_frames` calls this for each out-of-process iframe. It uses `_oop_session` to get a session and `_snapshot_target` to capture the child.

*Call graph*: calls 3 internal fn (_oop_session, _snapshot_target, frame_seq); called by 1 (_attach_oop_frames); 1 external calls (as_map).


##### `BrowserPage._register_frames`  (lines 779–782)

```
def _register_frames(self, tab: PageTab, frame: FrameSnapshot) -> None
```

**Purpose**: Records every frame prefix from a snapshot into the tab’s ref lookup table. This makes refs like `f1e3` usable after the page tree is rendered.

**Data flow**: It receives a tab and a frame snapshot. It stores the frame id, session id, and origin under the frame prefix, then repeats the same process for child frames.

**Call relations**: `BrowserPage.snapshot` calls this after capturing the full frame tree so later calls to `resolve_ref` can find the right frame.

*Call graph*: called by 1 (snapshot); 1 external calls (__init__).


##### `BrowserPage._oop_session`  (lines 784–797)

```
async def _oop_session(self, frame_id: str) -> str | None
```

**Purpose**: Gets a CDP session for an out-of-process iframe, reusing a cached one when possible. This avoids repeatedly attaching to the same frame.

**Data flow**: It receives a frame id. It first checks the browser session cache; if absent, it asks Chrome to attach to the target, initializes the new session, stores it in the cache, and returns the session id. If attachment fails, it returns nothing.

**Call relations**: `BrowserPage._snapshot_oop` calls this before it can snapshot an out-of-process iframe.

*Call graph*: called by 1 (_snapshot_oop); 1 external calls (as_str).


##### `_coord_float_or_default`  (lines 800–809)

```
def _coord_float_or_default(value: Json | None, default: float) -> float
```

**Purpose**: Converts coordinate values from Chrome into floats while allowing a default for missing values. It accepts both numeric values and numeric strings.

**Data flow**: It receives a JSON value and a default. Numbers become floats, non-empty strings are parsed as floats, `None` becomes the default, and any other type raises a validation error.

**Call relations**: `BrowserPage.ref_point` uses this when averaging Chrome’s returned element corner coordinates.

*Call graph*: called by 1 (ref_point); 1 external calls (__init__).
