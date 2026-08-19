# Page inspection, accessibility-tree parsing, and coordinate mapping  `stage-11.1.4`

This stage is the system’s page-reading layer. It sits behind the main work loop, helping the AI understand what is currently open in a browser tab and where actions should happen on the screen. It is like a translator between a living web page and the model’s simpler view of the world.

content.py is the front door for requests such as “read this page” or “search the page.” It asks the browser session for inspection data and turns it into structured page trees, readable text, or search results. page.py builds a snapshot of the page that the model can use. It gives visible elements stable references, so the model can say “click element 12” instead of guessing. It can also turn those references back into screen locations.

find.py works with the accessibility tree, a browser-made outline of buttons, links, fields, and text. It makes that outline searchable and checks that matches still point to real items. coordinate.py maps between the model’s screenshot coordinates and the browser’s real pixels, so clicks land where intended.

## Files in this stage

### Page reading entry points
High-level page content APIs turn live browser tabs into readable text, structured trees, and search-facing representations.

### `extensions/browser/ufo_ext_browser/bua/content.py`

`domain_logic` · `request handling`

This file solves a practical problem: browser pages are complex, but an outside tool usually needs a clean summary of what is on the page. It provides the BrowserContent class, which offers three main actions: read a page as a tree of elements, read the page as markdown-style text, and find items on the page that match a query.

The file depends on two small contracts, called Protocols. A Protocol is like a checklist: anything used as a browser session must be able to return a page, provide a page reader, and report tab information. Anything used as a page reader must be able to return either a page tree or markdown text. This keeps BrowserContent independent from the exact browser implementation.

The page tree is useful for automation because it includes page structure and element references. The markdown text is useful for plain reading. Search can work in two ways: a simple built-in text matcher, or an optional AI-style completer that receives the query and a shortened page tree, then returns likely matches. The file also protects callers from huge responses by cutting page trees and text at fixed size limits and reporting whether truncation happened. Without this layer, each browser command would need to repeat tab lookup, filtering, text limits, and search formatting on its own.

#### Function details

##### `BrowserContentPageReader.tree`  (lines 23–23)

```
async def tree(self, tab: PageTab, filter_type: str, ref: str | None) -> str | None
```

**Purpose**: This is a required method for any page reader used by this file. It should return a structured text view of a browser tab, optionally narrowed to a certain kind of element or a referenced part of the page.

**Data flow**: It receives a tab, a filter choice such as all elements or only interactive ones, and an optional reference id. An implementation looks inside the page and returns a tree as text, or returns nothing if the requested referenced element cannot be found.

**Call relations**: BrowserContent.tree relies on this contract after it has chosen the tab and cleaned up the reference id. The actual implementation lives elsewhere; this file only states what BrowserContent needs from it.


##### `BrowserContentPageReader.markdown`  (lines 25–25)

```
async def markdown(self, tab: PageTab) -> str
```

**Purpose**: This is a required method for turning a browser tab into plain readable text. It is meant for callers that want the page content, not the detailed element tree used for automation.

**Data flow**: It receives a browser tab. An implementation reads the tab and returns a markdown-style string, which is text with lightweight formatting such as headings or links.

**Call relations**: BrowserContent.get_page_text depends on this contract when serving a request for page text. The page reader implementation is supplied by the browser session outside this file.


##### `BrowserContentSession.page`  (lines 29–29)

```
async def page(self, tab_id: int | None=None) -> PageTab
```

**Purpose**: This is a required method for getting the browser tab that should be inspected. It lets callers ask for a specific tab by id, or use the current/default tab when no id is given.

**Data flow**: It receives an optional tab id. The browser session turns that into a PageTab object representing the tab that later reading operations will inspect.

**Call relations**: BrowserContent.tree and BrowserContent.get_page_text use this contract before reading anything from a page. The real browser session supplies the concrete behavior.


##### `BrowserContentSession.page_reader`  (lines 31–31)

```
def page_reader(self) -> BrowserContentPageReader
```

**Purpose**: This is a required method for obtaining the object that knows how to read page contents. It separates tab selection from the details of extracting trees or markdown.

**Data flow**: It takes no extra input beyond the session itself. It returns a page reader object that supports the tree and markdown operations expected by BrowserContent.

**Call relations**: BrowserContent.tree uses this to get a tree reader, and BrowserContent.get_page_text uses it to get a markdown reader. This file defines the need; another part of the system provides the reader.


##### `BrowserContentSession.tab_info`  (lines 33–33)

```
async def tab_info(self, tab: Any) -> JsonDict
```

**Purpose**: This is a required method for adding basic tab details to a page-text response. It lets the response include context such as which tab was read.

**Data flow**: It receives a tab object. The session looks up information about that tab and returns it as a JSON-style dictionary.

**Call relations**: BrowserContent.get_page_text calls this after collecting the page text, so the final response includes both the text and tab metadata. The exact fields come from the browser session implementation.


##### `BrowserContent.tree`  (lines 40–47)

```
async def tree(self, args: JsonDict, filter_type: str='all') -> str
```

**Purpose**: This returns a structured text tree for a browser tab. It is the shared helper used when the system needs a machine-friendly view of the page, either for reading or searching.

**Data flow**: It receives request arguments, looks for an optional tab_id and ref_id, converts the tab id into a number when possible, and asks the browser session for the chosen tab. It then asks the page reader for a tree using the requested filter and optional reference. If the referenced element is missing, it returns a clear “No element found” message; otherwise it returns the tree text.

**Call relations**: BrowserContent.read_page calls this when serving a page-tree read request, and BrowserContent.find calls it before searching the page. It uses _tab_id to normalize the tab id and hands the actual page reading off to the browser session’s page reader.

*Call graph*: calls 1 internal fn (_tab_id); called by 2 (find, read_page); 1 external calls (get).


##### `BrowserContent.read_page`  (lines 49–53)

```
async def read_page(self, args: JsonDict) -> JsonDict
```

**Purpose**: This prepares a safe, bounded response for a request to read the page structure. It lets callers choose whether they want all elements, only interactive elements, or only what is visible in the viewport.

**Data flow**: It receives request arguments and reads the filter value. If the filter is not one of the allowed choices, it falls back to all elements. It asks BrowserContent.tree for the page tree, cuts the result to the maximum allowed length, and returns both the shortened tree and a flag saying whether anything was cut off.

**Call relations**: This is a public-facing content operation built on BrowserContent.tree. It adds request validation and response-size protection around the lower-level tree reading step.

*Call graph*: calls 1 internal fn (tree); 1 external calls (get).


##### `BrowserContent.get_page_text`  (lines 55–62)

```
async def get_page_text(self, args: JsonDict) -> JsonDict
```

**Purpose**: This returns the current page as readable text, along with tab information. It is useful when a caller wants the article-like content of a page rather than the automation-oriented element tree.

**Data flow**: It receives request arguments, extracts an optional tab_id, converts it with _tab_id, and asks the browser session for that tab. It then asks the page reader for markdown text, cuts the text to the maximum allowed length, marks whether it was truncated, and adds tab information from the session before returning the final dictionary.

**Call relations**: This function runs independently of the tree-reading path. It still uses the same browser session contract to choose a tab, get a reader, and enrich the response with tab details.

*Call graph*: calls 1 internal fn (_tab_id); 1 external calls (get).


##### `BrowserContent.find`  (lines 64–77)

```
async def find(self, args: JsonDict, complete: FindCompleter | None=None) -> JsonDict
```

**Purpose**: This searches the page tree for items matching a user’s query. It can use either a built-in matcher or an optional completer, which is an outside helper that can interpret the query more flexibly.

**Data flow**: It receives request arguments and an optional completer. It first requires a query string, then gets the full page tree through BrowserContent.tree. If no completer is provided, it parses the tree directly for matches and decides whether there may be more results. If a completer is provided, it sends the query plus a shortened page tree to that completer, then resolves the completer’s reply back into page-tree matches. Finally it returns the matches and a human-readable summary.

**Call relations**: This function builds on BrowserContent.tree because searching needs the page structure first. It then delegates matching and summary wording to the find helpers: parse_tree_matches, resolve_find_reply, and format_matches.

*Call graph*: calls 1 internal fn (tree); 5 external calls (format_matches, parse_tree_matches, resolve_find_reply, get, as_str).


##### `_tab_id`  (lines 80–89)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: This small helper turns a tab id from a JSON-style request into either an integer or no tab id at all. It keeps tab-id cleanup in one place so the main content functions stay easier to read.

**Data flow**: It receives a value that may be an integer, floating-point number, string, or something else. Integers pass through, floats are converted to integers, non-empty strings are parsed as integers, and anything missing or unsupported becomes None.

**Call relations**: BrowserContent.tree and BrowserContent.get_page_text call this before asking the browser session for a tab. It acts like a small front-desk clerk that turns different forms of “tab number” into the one form the browser session expects.

*Call graph*: called by 2 (get_page_text, tree).


### Coordinate mapping
Coordinate utilities translate between model screenshot space and real browser-window pixels.

### `extensions/browser/ufo_ext_browser/bua/coordinate.py`

`util` · `cross-cutting`

When an AI model looks at a browser screenshot and says “click here,” its “here” may not be measured in the same pixels as the actual browser window. This file is the small measuring tool that keeps those spaces aligned. Without it, clicks could land in the wrong place, especially when screenshots are shrunk before being sent to a model.

The file defines two simple data shapes: `Size`, meaning a width and height, and `Coord`, meaning an x and y point. It then uses known limits for different vision models. Claude-family models may downscale images that are too wide, too tall, or too many total pixels, so `compute_screenshot_dimensions` chooses the largest screenshot size that should avoid server-side shrinking. Gemini is different: it reports positions on a fixed 0-to-1000 grid, like using graph paper that is always the same size no matter how large the image is.

The conversion functions work like map scaling. If a model gives a point on its smaller “map,” `model_to_viewport` stretches that point back onto the real browser window. If the browser has a real pixel point and the code needs to describe it to the model, `viewport_to_model` scales it the other way. The important behavior is that the caller can provide a model size explicitly, but if it does not, the file computes the expected screenshot size from the viewport.

#### Function details

##### `compute_screenshot_dimensions`  (lines 22–33)

```
def compute_screenshot_dimensions(viewport: Size) -> Size
```

**Purpose**: This function works out the largest screenshot size that should reach the Claude vision model without being resized again by the service. Someone would use it before sending a browser screenshot, so later coordinate math matches what the model actually sees.

**Data flow**: It takes the browser viewport size as input. It first scales the image down if its longest side is over the model’s limit, then checks whether the total number of pixels is still too high and shrinks it again if needed. It returns a new `Size` with the final width and height.

**Call relations**: This is the fallback size calculator for `effective_model_size`. When no caller has already supplied the model’s coordinate size, the rest of the coordinate conversion flow depends on this function to decide the model-visible screenshot dimensions.

*Call graph*: called by 1 (effective_model_size); 1 external calls (__init__).


##### `effective_model_size`  (lines 36–38)

```
def effective_model_size(viewport: Size, model_size: Size | None=None) -> Size
```

**Purpose**: This function decides which coordinate space the model is using. It uses an explicit model size if one was given, otherwise it computes the screenshot size that the model is expected to see.

**Data flow**: It receives the browser viewport size and optionally a model size. If the model size is present, it returns that unchanged. If not, it sends the viewport size to `compute_screenshot_dimensions` and returns the computed screenshot size.

**Call relations**: Both `model_to_viewport` and `viewport_to_model` call this first, because they need a common answer to the question: “What size map is the model using?” It either passes through the caller’s override or hands off to `compute_screenshot_dimensions` to create one.

*Call graph*: calls 1 internal fn (compute_screenshot_dimensions); called by 2 (model_to_viewport, viewport_to_model).


##### `model_to_viewport`  (lines 41–47)

```
def model_to_viewport(coord: Coord, viewport: Size, model_size: Size | None=None) -> Coord
```

**Purpose**: This function converts a point reported by the model into real browser viewport pixels. It is used when the system needs to turn the model’s chosen click or target point into an actual browser input location.

**Data flow**: It takes a model-space coordinate, the real browser viewport size, and optionally the model’s coordinate size. It asks `effective_model_size` what coordinate area the model is using, then scales x and y from that area up or down to the viewport’s width and height. It returns a new `Coord` in browser pixel coordinates.

**Call relations**: This is used after the model has chosen a point. It relies on `effective_model_size` to understand the model’s map, then creates the viewport coordinate that browser input code can use.

*Call graph*: calls 1 internal fn (effective_model_size); 1 external calls (__init__).


##### `model_coordinate_space`  (lines 50–55)

```
def model_coordinate_space(model: str | None) -> Size | None
```

**Purpose**: This function identifies whether a named model uses a special fixed coordinate grid. In particular, Gemini models report points on a 1000 by 1000 grid, while Claude-family models use the screenshot pixel dimensions instead.

**Data flow**: It takes an optional model name. If the name exists and contains “gemini” in any capitalization, it returns a `Size` of 1000 by 1000. Otherwise, it returns `None`, meaning there is no special fixed grid and the normal screenshot-size calculation should be used.

**Call relations**: This function is a helper for callers that know the model name before doing coordinate conversion. It prepares the optional model size that can later be passed into `model_to_viewport` or `viewport_to_model`.

*Call graph*: 1 external calls (__init__).


##### `viewport_to_model`  (lines 58–64)

```
def viewport_to_model(coord: Coord, viewport: Size, model_size: Size | None=None) -> Coord
```

**Purpose**: This function converts a real browser pixel point into the coordinate space used by the model. It is useful when the system needs to describe a browser location back to the model in the model’s own measuring system.

**Data flow**: It takes a viewport coordinate, the full browser viewport size, and optionally the model’s coordinate size. It asks `effective_model_size` what size coordinate space the model uses, scales x and y from browser pixels into that space, and returns a new `Coord` with the converted point.

**Call relations**: This is the reverse of `model_to_viewport`. It also relies on `effective_model_size`, so both directions of conversion use the same understanding of screenshot size or fixed model grid.

*Call graph*: calls 1 internal fn (effective_model_size); 1 external calls (__init__).


### Accessibility-tree search
Search helpers parse accessibility-tree text into entries, validate matches, and format results.

### `extensions/browser/ufo_ext_browser/bua/find.py`

`domain_logic` · `request handling`

A browser accessibility tree is like a map of the page made for assistive technology: it says things such as “button,” “link,” or “textbox,” often with a visible name and a location. This file is the consumer for a simple text grammar used to describe that map. Without it, a search tool or language model could point to page elements that do not really exist, use the wrong label, or return results in an inconsistent shape.

The file does three main jobs. First, it reads tree lines and pulls out the important parts: the element reference, its role, its name, and optional screen coordinates. Second, it can do a basic local search by splitting the user’s query into words and keeping tree entries whose text contains all those words. Third, when an outside system replies with possible references, it “grounds” that reply by checking every reference against the original tree. That means the tree remains the source of truth, not the reply text.

There is also a shared output shape for matches and a formatter that turns matches into readable lines. The result is a small safety layer: it lets flexible search happen, but prevents made-up or stale element references from spreading further through the browser automation flow.

#### Function details

##### `tree_entries`  (lines 31–50)

```
def tree_entries(tree: str) -> list[JsonDict]
```

**Purpose**: This function reads the text accessibility tree and turns each usable line into a small structured record. Each record says which page element it refers to, what kind of element it is, what name it has, where it is on the page if known, and the original line in lowercase for searching.

**Data flow**: It takes the full tree as one text string. It walks through the tree line by line, keeps only lines that look like element lines and contain a reference, then extracts the role, name, reference, and optional x,y coordinates. It returns a list of dictionaries; lines that do not match the expected pattern are ignored.

**Call relations**: This is the parser that other search steps rely on. parse_tree_matches calls it before doing a simple text search, and resolve_find_reply calls it to build the trusted list of real references that a reply is allowed to use.

*Call graph*: called by 2 (parse_tree_matches, resolve_find_reply).


##### `_match_payload`  (lines 53–60)

```
def _match_payload(entry: JsonDict, reason: str) -> JsonDict
```

**Purpose**: This helper builds the standard match object returned by the search functions. It keeps the trusted element details together with a short explanation of why the element matched.

**Data flow**: It receives one parsed tree entry and a reason string. It copies the entry’s reference, role, name, and coordinates, adds the reason, and returns a new dictionary shaped like a search result. It does not change the original entry.

**Call relations**: Both parse_tree_matches and resolve_find_reply use this helper so their results look the same. That means later code can treat local matches and externally suggested matches in one consistent way.

*Call graph*: called by 2 (parse_tree_matches, resolve_find_reply).


##### `parse_tree_matches`  (lines 63–71)

```
def parse_tree_matches(tree: str, query: str) -> list[JsonDict]
```

**Purpose**: This function performs a simple built-in search over the accessibility tree. It is useful when the query can be matched directly against the tree text without needing a more flexible outside interpretation.

**Data flow**: It takes the tree text and a user query. It breaks the query into lowercase words made of letters and numbers, ignoring one-character terms, then parses the tree into entries. For each entry, it checks whether all query terms appear in that entry’s lowercase line. Matching entries are converted into standard match objects, up to the maximum result limit, and returned as a list.

**Call relations**: This function uses tree_entries to get searchable records and _match_payload to package each result. It also uses regular-expression word finding to split the query into search terms. It is the straightforward local search path in this file.

*Call graph*: calls 2 internal fn (_match_payload, tree_entries); 1 external calls (findall).


##### `resolve_find_reply`  (lines 74–102)

```
def resolve_find_reply(reply: str, tree: str) -> tuple[list[JsonDict], bool]
```

**Purpose**: This function verifies and cleans up a proposed list of matches, usually from an outside search step such as a language model response. Its main job is to stop invented or incorrect element references from being trusted.

**Data flow**: It takes a reply string and the original tree text. First it parses the tree into a lookup table by reference. Then it reads the reply line by line, ignoring empty lines, stopping on NO_MATCHES, noticing a MORE marker, and extracting element references from result lines. For each reference, it keeps the match only if that reference truly exists in the tree and has not already been used. It returns two things: the trusted match list and a true-or-false flag saying whether more matches were reported.

**Call relations**: This function depends on tree_entries to know which references are real and on _match_payload to return matches in the standard shape. It is the safety checkpoint between a free-form reply and the rest of the browser automation system.

*Call graph*: calls 2 internal fn (_match_payload, tree_entries).


##### `format_matches`  (lines 105–114)

```
def format_matches(matches: list[JsonDict], has_more: bool=False) -> str
```

**Purpose**: This function turns structured match objects into readable text. It is used when results need to be shown to a person or passed along in a compact human-friendly form.

**Data flow**: It takes a list of match dictionaries and an optional flag saying whether more results exist. For each match, it builds a line containing the reference, role, name, and coordinates, and adds the reason if there is one. If the more-results flag is true, it appends a note suggesting that the query should be refined. It returns one joined text block.

**Call relations**: This is the final presentation step for matches produced elsewhere in the file. Unlike the search and validation functions, it does not inspect the tree; it simply formats already prepared match data.


### Page snapshots and references
Page inspection logic builds structured snapshots, assigns stable element references, and resolves those references back to actionable screen points.

### `extensions/browser/ufo_ext_browser/bua/page.py`

`domain_logic` · `request handling`

A browser page is messy: it has visible boxes, hidden nodes, accessibility labels, nested frames, and sometimes iframes that run in separate browser processes. This file brings those pieces together into one usable view. It asks Chrome, through the Chrome DevTools Protocol or CDP (a browser control API), for two main things: the page layout with element positions, and the accessibility tree, which is Chrome’s description of what a screen reader would see. It then joins those views using Chrome’s backend node ids, so each useful item can be printed with a role, a name, a reference, and often a center coordinate.

The rendered tree is meant for action: lines look like accessible objects, such as buttons, links, text boxes, and images, with refs like `e12` or `f1e3`. Those refs are like claim tickets: later code can hand one back to find the same browser node, as long as the document has not navigated. The file also offers a markdown reading view, which is less about clicking and more about understanding page content.

A key complication is frames. Normal iframes and out-of-process iframes are stitched into the same tree so the model does not have to reason about browser internals. Without this file, the agent would either see a flat, unreliable page dump or have no safe way to connect what it read to what it should click.

#### Function details

##### `split_ref`  (lines 116–120)

```
def split_ref(ref: str) -> tuple[str, int] | None
```

**Purpose**: Breaks a browser element reference, such as `e12` or `f1e3`, into its frame prefix and numeric browser node id. This is used to check whether a model-provided ref is shaped like a real ref from the page view.

**Data flow**: It receives a ref string. It matches it against the allowed pattern, then returns the frame prefix and backend node number, or returns nothing if the string is not a valid ref.

**Call relations**: When rendering a subtree, `render_page` uses this to understand the requested starting ref. When acting on a ref, `BrowserPage.resolve_ref` uses it first so bad or invented refs can be rejected clearly.

*Call graph*: called by 2 (resolve_ref, render_page).


##### `Cdp.send`  (lines 124–126)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: Defines the expected shape of a CDP command sender. CDP is Chrome DevTools Protocol, the API used here to ask the browser for page data or perform browser-side actions.

**Data flow**: Callers provide a method name, optional parameters, and optionally a browser session id. The implementation sends that command to Chrome and returns the JSON-like response.

**Call relations**: `fetch_target` relies on this interface to gather snapshots and accessibility data. Other browser subsystems, such as settling page tasks, can use the same connection shape.

*Call graph*: called by 2 (fetch_target, _flush_page_tasks).


##### `PageTab.frame_seq`  (lines 161–161)

```
def frame_seq(self, frame_id: str) -> int
```

**Purpose**: Provides a short, stable sequence number for a frame id within a tab. That number becomes part of refs for elements inside frames.

**Data flow**: It receives a browser frame id and returns an integer assigned by the tab. That integer is later formatted into prefixes like `f1`.

**Call relations**: `BrowserPage._snapshot_oop` calls this when it discovers a separate-process frame and needs to give that frame its own ref prefix.

*Call graph*: called by 1 (_snapshot_oop).


##### `BrowserPageSession.connection`  (lines 168–168)

```
def connection(self) -> Cdp
```

**Purpose**: Provides access to the browser’s CDP connection. The rest of this file uses it whenever it needs to ask Chrome for page structure, geometry, or element positions.

**Data flow**: It reads the browser session object and returns a CDP sender. It does not itself transform page data.

**Call relations**: Methods on `BrowserPage` call this before sending browser commands, especially during snapshots and ref-to-point lookup.


##### `BrowserPageSession.init_session`  (lines 170–170)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Prepares a newly attached browser session so it can be used safely by this page snapshot code. This matters for out-of-process iframes, which require their own CDP session.

**Data flow**: It receives a session id and performs whatever setup the browser layer requires. Its visible result is readiness rather than a returned value.

**Call relations**: `BrowserPage._oop_session` calls this after attaching to an out-of-process frame, before storing and reusing that session.


##### `_float`  (lines 190–195)

```
def _float(value: Json | None, default: float=0.0) -> float
```

**Purpose**: Safely turns a JSON value into a floating-point number. It protects snapshot parsing from missing or oddly typed values by using a default.

**Data flow**: It receives a value and a default. If the value is already numeric, it returns it as a float; otherwise it returns the default.

**Call relations**: `_parse_document` uses it for scroll offsets and layout bounds. `fetch_target` uses it to read the browser’s device pixel ratio.

*Call graph*: called by 2 (_parse_document, fetch_target).


##### `_attr`  (lines 198–203)

```
def _attr(strings: list[str], attrs: list[Json], name: str) -> str | None
```

**Purpose**: Looks up one HTML attribute from Chrome’s compact snapshot format. Chrome stores attributes as indexes into a shared string table, so this helper turns that compact form back into a readable value.

**Data flow**: It receives the shared string list, an attribute list, and the wanted attribute name. It scans name-value pairs and returns the matching string value, or nothing if the attribute is absent.

**Call relations**: `_parse_document` uses this when it wants useful details from input, image, and iframe elements, such as input type, image source, or iframe source.

*Call graph*: called by 1 (_parse_document).


##### `_parse_document`  (lines 206–266)

```
def _parse_document(doc: JsonDict, strings: list[str], dpr: float) -> _RawDoc
```

**Purpose**: Converts one raw DOM snapshot document from Chrome into a smaller, easier structure focused on element geometry and frame links. It is where browser layout data becomes usable page coordinates.

**Data flow**: It receives one document section, Chrome’s shared strings, and the device pixel ratio. It reads backend node ids, layout bounds, scroll offsets, element attributes, and iframe document links, then returns a raw document record with positions and special element details.

**Call relations**: `parse_snapshot` calls this for each document in the browser snapshot. It uses `_float` for numeric cleanup and `_attr` for decoding attributes.

*Call graph*: calls 2 internal fn (_attr, _float); called by 1 (parse_snapshot); 6 external calls (__init__, __init__, get, as_int, as_list, as_map).


##### `parse_snapshot`  (lines 269–318)

```
def parse_snapshot(snapshot: JsonDict, dpr: float, base_origin: tuple[float, float]=(0.0, 0.0)) -> list[DocData]
```

**Purpose**: Turns Chrome’s full DOM snapshot into per-frame document data with coordinates in the top page’s coordinate space. This is the step that makes nested iframe contents line up with the visible page.

**Data flow**: It receives the snapshot, device pixel ratio, and an optional starting origin. It parses each document, follows iframe document links, accumulates iframe offsets, adjusts all element bounds, and returns `DocData` records for reachable documents.

**Call relations**: `fetch_target` calls this after asking Chrome for `DOMSnapshot.captureSnapshot`. It builds the geometry side of the later `FrameSnapshot` tree.

*Call graph*: calls 1 internal fn (_parse_document); called by 1 (fetch_target); 5 external calls (__init__, get, as_list, as_map, as_str).


##### `fetch_target`  (lines 321–383)

```
async def fetch_target(cdp: Cdp, session_id: str, *, root_prefix: str, prefix_for: Callable[[str], str], base_origin: tuple[float, float]=(0.0, 0.0)) -> FrameSnapshot
```

**Purpose**: Fetches a complete snapshot for one browser target or session. It combines layout information and accessibility information into frame snapshots that can later be rendered.

**Data flow**: It receives a CDP connection, a session id, frame-prefix rules, and an origin. It enables browser snapshot features, captures DOM layout, reads device pixel ratio, parses geometry, asks for accessibility trees for each frame, and returns a root `FrameSnapshot` with children linked where possible.

**Call relations**: `BrowserPage._snapshot_target` calls this as the core snapshot operation. It delegates geometry parsing to `parse_snapshot` and browser communication to `Cdp.send`.

*Call graph*: calls 3 internal fn (send, _float, parse_snapshot); called by 1 (_snapshot_target); 5 external calls (__init__, gather, as_list, as_map, as_str).


##### `_ax_value`  (lines 386–390)

```
def _ax_value(value: Json | None) -> str
```

**Purpose**: Extracts the plain value from an accessibility-tree value object. Accessibility nodes often wrap useful text inside a small dictionary, and this helper unwraps it.

**Data flow**: It receives a JSON value. If it is the expected object shape, it returns its inner `value` as text; otherwise it returns an empty string.

**Call relations**: Both `render_page.render_node` and `render_markdown.walk` use it to read roles and names from accessibility nodes before deciding what to print.

*Call graph*: called by 2 (walk, render_node); 1 external calls (get).


##### `_ax_property`  (lines 393–401)

```
def _ax_property(node: JsonDict, name: str) -> Json
```

**Purpose**: Finds a named property on an accessibility node, such as `checked`, `disabled`, `url`, or heading level. These properties add meaning beyond the node’s role and name.

**Data flow**: It receives an accessibility node and a property name. It scans the node’s property list and returns the property’s usable value, or nothing if it is not present.

**Call relations**: `_format_extras`, `render_page.render_node`, and `render_markdown.walk` call this whenever they need state, hidden status, links, heading levels, or other accessibility details.

*Call graph*: called by 3 (_format_extras, walk, render_node); 3 external calls (get, as_list, as_map).


##### `_should_skip`  (lines 404–410)

```
def _should_skip(node: JsonDict, role: str, name: str) -> bool
```

**Purpose**: Decides whether a container-like accessibility node can be left out of the rendered tree. This keeps the output readable by skipping empty wrapper nodes that do not add useful meaning.

**Data flow**: It receives a node, its role, and its name. If the role is a skippable wrapper, has no name, and has no important state, it returns true; otherwise false.

**Call relations**: `render_page.render_node` uses this while walking the tree. If a node is skipped, its children are still visited, so useful content is not lost.

*Call graph*: called by 1 (render_node); 3 external calls (get, as_list, as_map).


##### `_truncate`  (lines 413–416)

```
def _truncate(text: str, max_len: int) -> str
```

**Purpose**: Shortens long text so the rendered page stays compact. It avoids flooding the model with huge names or values.

**Data flow**: It receives text and a maximum length. If the text is too long, it cuts it and adds an ellipsis; otherwise it returns the original text.

**Call relations**: `_format_extras`, `_image_name`, and `render_page.render_node` use this before adding names, values, and image filenames to the text view.

*Call graph*: called by 3 (_format_extras, _image_name, render_node).


##### `_image_name`  (lines 419–426)

```
def _image_name(src: str | None) -> str
```

**Purpose**: Creates a useful fallback label for an image when the accessibility tree has no image name. It uses the image file name from the URL if that looks meaningful.

**Data flow**: It receives an image source URL or nothing. It extracts the path’s final filename, truncates it if needed, and returns it only if it looks like a real file name.

**Call relations**: `render_page.render_node` uses it for unnamed image rows. `render_markdown.walk` uses it for image markdown when alt text is missing.

*Call graph*: calls 1 internal fn (_truncate); called by 2 (walk, render_node); 1 external calls (urlparse).


##### `_format_extras`  (lines 429–459)

```
def _format_extras(node: JsonDict, geom: NodeGeom | None) -> str
```

**Purpose**: Builds the extra state text shown after a rendered page item. This includes useful details like input type, current value, checked state, disabled state, URL, and similar properties.

**Data flow**: It receives an accessibility node and optional geometry/details for its DOM node. It reads selected properties, filters unsafe or noisy URL forms, truncates long values, and returns a formatted suffix string.

**Call relations**: `render_page.render_node` calls this right before adding a line to the page tree, so each visible node can carry the state a user would care about.

*Call graph*: calls 2 internal fn (_ax_property, _truncate); called by 1 (render_node).


##### `_frame_by_prefix`  (lines 462–469)

```
def _frame_by_prefix(root: FrameSnapshot, prefix: str) -> FrameSnapshot | None
```

**Purpose**: Finds the frame snapshot that owns a ref prefix. For example, it can find the frame represented by `f1`.

**Data flow**: It receives the root frame snapshot and a prefix. It searches the root and child frames recursively and returns the matching frame, or nothing if no frame has that prefix.

**Call relations**: `render_page` uses this when the caller asks to render only the subtree under a specific ref.

*Call graph*: called by 1 (render_page).


##### `_node_by_backend`  (lines 472–476)

```
def _node_by_backend(frame: FrameSnapshot, backend_id: int) -> str | None
```

**Purpose**: Finds an accessibility node id from a Chrome backend DOM node id inside one frame. This bridges the action-oriented DOM id back to the accessibility tree.

**Data flow**: It receives a frame snapshot and backend node number. It scans accessibility nodes until it finds one with the same backend DOM node id, then returns that accessibility node id.

**Call relations**: `render_page` uses this after `split_ref` and `_frame_by_prefix` when rendering from a specific element reference.

*Call graph*: called by 1 (render_page).


##### `render_page`  (lines 479–575)

```
def render_page(root: FrameSnapshot, *, viewport: Size, model_size: Size | None=None, filter_type: str='all', max_depth: int=DEFAULT_MAX_DEPTH, ref: str | None=None) -> str | None
```

**Purpose**: Renders the snapshot as a concise action-oriented tree for the model. The output shows accessible roles, names, refs, center coordinates, and important state.

**Data flow**: It receives a root frame snapshot, viewport size, optional model-size scaling, filter choice, depth limit, and optional starting ref. It walks accessibility nodes, stitches in child frames, filters or skips noise, scales coordinates, and returns a multiline string, or nothing if a requested ref cannot be found.

**Call relations**: `BrowserPage.tree` calls this after taking a snapshot. Internally it uses `split_ref`, `_frame_by_prefix`, and `_node_by_backend` when starting from a specific ref, then relies on nested helper functions to walk and print the tree.

*Call graph*: calls 3 internal fn (_frame_by_prefix, _node_by_backend, split_ref); called by 1 (tree); 1 external calls (effective_model_size).


##### `render_page.coord_str`  (lines 494–498)

```
def coord_str(geom: NodeGeom | None) -> str
```

**Purpose**: Formats an element’s center point for the rendered page tree. This lets the model know roughly where an item is on screen.

**Data flow**: It receives optional geometry. If bounds exist, it computes the center, scales it from browser viewport size to model coordinate size, and returns text like `(x=...,y=...)`; otherwise it returns an empty string.

**Call relations**: `render_page.render_node` calls this when it is building one output line for a visible accessibility node.


##### `render_page.splice`  (lines 500–503)

```
def splice(frame: FrameSnapshot, backend_id: int | None, depth: int) -> None
```

**Purpose**: Inserts a child frame’s accessibility tree at the iframe node where it belongs. This makes iframe content appear in the same rendered tree as the parent page.

**Data flow**: It receives a frame, an optional backend iframe id, and a depth. If that backend id has a child frame with a root node, it asks `render_page.render_node` to render that child frame from its root.

**Call relations**: `render_page.render_node.descend` calls this after walking normal child nodes, so frame contents are stitched into the page at the right place.


##### `render_page.render_node`  (lines 505–559)

```
def render_node(frame: FrameSnapshot, ax_id: str, depth: int, parent_name: str) -> None
```

**Purpose**: Walks one accessibility node and decides whether and how it should appear in the page tree. It is the main decision-maker for readable, useful output.

**Data flow**: It receives a frame, an accessibility node id, current depth, and parent name. It checks for loops, hidden nodes, duplicate static text, ignored roles, filters, viewport bounds, refs, names, coordinates, and extras, then appends a line and continues into children when appropriate.

**Call relations**: `render_page` starts the walk with this helper. It calls helpers such as `_ax_value`, `_ax_property`, `_should_skip`, `_image_name`, `_truncate`, and `_format_extras` to turn raw browser data into clean text.

*Call graph*: calls 6 internal fn (_ax_property, _ax_value, _format_extras, _image_name, _should_skip, _truncate); 2 external calls (as_list, as_str).


##### `render_page.render_node.descend`  (lines 521–524)

```
def descend(child_depth: int, child_parent_name: str) -> None
```

**Purpose**: Continues walking from the current node into its children and any frame content attached to it. It keeps traversal rules in one small helper inside the node renderer.

**Data flow**: It receives the child depth and the name to treat as the parent name. It renders each listed accessibility child, then asks `render_page.splice` to add iframe content if this node owns a child frame.

**Call relations**: `render_page.render_node` calls this both when a node is transparent and after a node has been printed, so children are not lost.


##### `render_markdown`  (lines 583–651)

```
def render_markdown(root: FrameSnapshot) -> str
```

**Purpose**: Renders the page snapshot as a reading-oriented markdown document. This is for understanding content, not for choosing exact click targets.

**Data flow**: It receives a root frame snapshot. It walks the accessibility tree, turns headings into markdown headings, links into markdown links, list items into bullets, images into image markers, and text into paragraphs, then returns the joined markdown blocks.

**Call relations**: `BrowserPage.markdown` calls this after taking a snapshot. It uses nested helpers to collect text and recursively includes child frames in reading order.

*Call graph*: called by 1 (markdown).


##### `render_markdown.emit`  (lines 592–595)

```
def emit(text: str) -> None
```

**Purpose**: Adds one finished markdown block if it is non-empty and not an immediate duplicate. This keeps the reading output cleaner.

**Data flow**: It receives text, trims it, checks it against the most recent block, and appends it to the block list when useful.

**Call relations**: `render_markdown.flush` and `render_markdown.walk` call this whenever a paragraph, heading, list item, block label, or image marker is ready.


##### `render_markdown.flush`  (lines 597–600)

```
def flush() -> None
```

**Purpose**: Turns accumulated inline words into one markdown paragraph. It is like emptying a small notepad of text before starting a new block.

**Data flow**: It reads the current inline text list. If there is any text, it joins it with spaces, emits it as a block, and clears the inline list.

**Call relations**: `render_markdown.walk` calls this before headings, blocks, list items, images, and frame splices so text does not run together.


##### `render_markdown.walk`  (lines 602–646)

```
def walk(frame: FrameSnapshot, ax_id: str, parent_name: str) -> None
```

**Purpose**: Recursively reads accessibility nodes and converts them into markdown-like content. It preserves broad document structure instead of dumping raw page text.

**Data flow**: It receives a frame, an accessibility node id, and the parent name. It skips hidden or repeated text, interprets roles such as heading, link, list item, image, and paragraph, collects or emits text, then visits children and child frames.

**Call relations**: `render_markdown` starts this walk at the root accessibility node. It uses `_ax_value`, `_ax_property`, and `_image_name` to interpret browser accessibility data.

*Call graph*: calls 3 internal fn (_ax_property, _ax_value, _image_name); 2 external calls (as_list, as_str).


##### `BrowserPage.tree`  (lines 660–668)

```
async def tree(self, tab: PageTab, filter_type: str, ref: str | None) -> str | None
```

**Purpose**: Provides the public way to get the action-oriented page tree for a tab. Callers use it when they want refs and coordinates for possible browser actions.

**Data flow**: It receives a tab, a filter type, and optionally a starting ref. It first takes a fresh snapshot, then passes that snapshot to `render_page` with the browser’s viewport and model size, and returns the rendered text or nothing if the ref is invalid.

**Call relations**: This is a high-level entry into the file’s snapshot-and-render flow. It calls `BrowserPage.snapshot` for live browser data and `render_page` for text output.

*Call graph*: calls 2 internal fn (snapshot, render_page).


##### `BrowserPage.markdown`  (lines 670–671)

```
async def markdown(self, tab: PageTab) -> str
```

**Purpose**: Provides the public way to get a readable markdown version of a tab. Callers use it when they want page content and structure rather than click targets.

**Data flow**: It receives a tab, takes a fresh snapshot, renders that snapshot with `render_markdown`, and returns the markdown string.

**Call relations**: This mirrors `BrowserPage.tree`, but hands the snapshot to the reading renderer instead of the action renderer.

*Call graph*: calls 2 internal fn (snapshot, render_markdown).


##### `BrowserPage.resolve_ref`  (lines 673–686)

```
def resolve_ref(self, tab: PageTab, ref: str) -> tuple[FrameNode, int]
```

**Purpose**: Checks and resolves an element ref back to the frame information and backend node id needed for browser actions. It also gives clear errors when the model invents or reuses an old ref.

**Data flow**: It receives a tab and ref string. It parses the ref, looks up the frame prefix in the tab’s registered frame map, and returns the matching frame node plus backend id; if anything fails, it raises a `HallucinationError` with guidance to re-read the page.

**Call relations**: `BrowserPage.ref_point` calls this before trying to locate an element on screen. It relies on `split_ref` and on frame registrations created by `BrowserPage.snapshot`.

*Call graph*: calls 1 internal fn (split_ref); called by 1 (ref_point); 1 external calls (__init__).


##### `BrowserPage.ref_point`  (lines 688–714)

```
async def ref_point(self, tab: PageTab, ref: str) -> tuple[int, int]
```

**Purpose**: Turns a valid element ref into a screen point near the center of that element. This is what lets later code click or move to something the model selected from the page tree.

**Data flow**: It receives a tab and ref. It resolves the ref, asks Chrome to scroll the node into view, reads its content quadrilateral or box model, averages the four corners, adds the frame origin, and returns integer x and y coordinates; failed browser lookups become a clear ref error.

**Call relations**: It starts with `BrowserPage.resolve_ref`, then talks to the browser through the session connection. It uses `_coord_float_or_default` to safely read coordinate values from Chrome’s response.

*Call graph*: calls 2 internal fn (resolve_ref, _coord_float_or_default); 3 external calls (__init__, as_list, as_map).


##### `BrowserPage.snapshot`  (lines 716–720)

```
async def snapshot(self, tab: PageTab) -> FrameSnapshot
```

**Purpose**: Takes a fresh full-page snapshot for a tab and records which frame prefixes are valid for later refs. It is the common first step before rendering a tree or markdown.

**Data flow**: It receives a tab. It snapshots the main session, clears the tab’s old ref-frame map, registers every frame in the new snapshot, and returns the root frame snapshot.

**Call relations**: `BrowserPage.tree` and `BrowserPage.markdown` both call this. It delegates the actual capture to `BrowserPage._snapshot_target` and bookkeeping to `BrowserPage._register_frames`.

*Call graph*: calls 2 internal fn (_register_frames, _snapshot_target); called by 2 (markdown, tree).


##### `BrowserPage._snapshot_target`  (lines 722–739)

```
async def _snapshot_target(self, tab: PageTab, session_id: str, root_prefix: str, origin: tuple[float, float], depth: int) -> FrameSnapshot
```

**Purpose**: Snapshots one browser target/session and optionally expands separate-process iframe content below it. This keeps the frame tree complete without letting recursion run forever.

**Data flow**: It receives a tab, session id, ref prefix, origin, and current frame depth. It calls `fetch_target` to capture that session; if the allowed depth is not reached, it attaches out-of-process frames, then returns the root snapshot for that target.

**Call relations**: `BrowserPage.snapshot` uses this for the main tab. `BrowserPage._snapshot_oop` uses it recursively for out-of-process iframe targets.

*Call graph*: calls 2 internal fn (_attach_oop_frames, fetch_target); called by 2 (_snapshot_oop, snapshot).


##### `BrowserPage._attach_oop_frames`  (lines 741–749)

```
async def _attach_oop_frames(self, tab: PageTab, root: FrameSnapshot, depth: int) -> None
```

**Purpose**: Finds iframe placeholders that were not included in the normal DOM snapshot and tries to attach their separate frame snapshots. These are out-of-process iframes, meaning Chrome runs them through a different target/session.

**Data flow**: It receives a tab, a root frame snapshot, and current depth. It walks existing frames, checks each recorded out-of-process iframe backend id, snapshots it if possible, and inserts the child snapshot into the parent’s children map.

**Call relations**: `BrowserPage._snapshot_target` calls this after `fetch_target`. For each separate iframe, it delegates to `BrowserPage._snapshot_oop`.

*Call graph*: calls 1 internal fn (_snapshot_oop); called by 1 (_snapshot_target).


##### `BrowserPage._snapshot_oop`  (lines 751–777)

```
async def _snapshot_oop(self, tab: PageTab, frame: FrameSnapshot, backend_id: int, depth: int) -> FrameSnapshot | None
```

**Purpose**: Snapshots one out-of-process iframe and returns it as a child frame snapshot. If the browser cannot describe or attach to the frame, it quietly skips it with a warning where appropriate.

**Data flow**: It receives the tab, parent frame, iframe backend id, and depth. It asks Chrome which frame id belongs to the iframe node, gets or creates a CDP session for that frame, computes the child origin from iframe bounds, and calls `_snapshot_target` for the child session.

**Call relations**: `BrowserPage._attach_oop_frames` calls this for each out-of-process iframe. It uses `PageTab.frame_seq` for frame prefixes and `BrowserPage._oop_session` for session attachment.

*Call graph*: calls 3 internal fn (_oop_session, _snapshot_target, frame_seq); called by 1 (_attach_oop_frames); 1 external calls (as_map).


##### `BrowserPage._register_frames`  (lines 779–782)

```
def _register_frames(self, tab: PageTab, frame: FrameSnapshot) -> None
```

**Purpose**: Records every frame prefix from a snapshot so refs can later be resolved. This is the lookup table that connects text refs back to browser sessions.

**Data flow**: It receives a tab and frame snapshot. It stores a `FrameNode` under the frame’s prefix, then repeats the same work for every child frame.

**Call relations**: `BrowserPage.snapshot` calls this after taking a fresh snapshot. `BrowserPage.resolve_ref` later depends on the resulting `tab.ref_frames` map.

*Call graph*: called by 1 (snapshot); 1 external calls (__init__).


##### `BrowserPage._oop_session`  (lines 784–797)

```
async def _oop_session(self, frame_id: str) -> str | None
```

**Purpose**: Gets a usable CDP session for an out-of-process frame, reusing one if it was already attached. This avoids repeatedly attaching to the same frame.

**Data flow**: It receives a frame id. It checks the browser session cache, otherwise asks Chrome to attach to that target, initializes the new session, stores it in the cache, and returns the session id; if attach fails, it returns nothing.

**Call relations**: `BrowserPage._snapshot_oop` calls this before it can snapshot a separate-process iframe. It uses `BrowserPageSession.init_session` through the browser object after attachment.

*Call graph*: called by 1 (_snapshot_oop); 1 external calls (as_str).


##### `_coord_float_or_default`  (lines 800–809)

```
def _coord_float_or_default(value: Json | None, default: float) -> float
```

**Purpose**: Safely reads a coordinate value from Chrome’s response. Chrome may return numbers or numeric strings, and missing values can use a default.

**Data flow**: It receives a JSON value and a default. It returns a float for numeric values or numeric strings, returns the default for missing values, and raises a validation error for anything else.

**Call relations**: `BrowserPage.ref_point` uses this when averaging the four corners returned by Chrome for an element’s content box.

*Call graph*: called by 1 (ref_point); 1 external calls (__init__).
