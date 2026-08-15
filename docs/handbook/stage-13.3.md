# Page inspection, accessibility content, and element lookup  `stage-13.3`

This stage is shared support for the browser agent’s main work loop. Before the system can click, type, or answer questions about a web page, it must turn the live page into something readable and findable. The content.py tools inspect the current browser page, pull out text and page details, and keep the result small enough to safely send to higher-level commands. The page.py tools do the broader packaging: they turn the live page into a clean structured description for the AI, then later translate any chosen element back into a real place on the screen. The find.py tools work with the accessibility tree, which is the browser’s built-in outline of links, buttons, fields, and text. They search that outline, tidy up AI-written search results, and present matches clearly. The coordinate.py tools handle the ruler and map: they convert between screenshot coordinates seen by a vision model and the actual browser viewport, so a planned click lands where intended. Together, these parts let the system read, locate, and act on page elements reliably.

## Files in this stage

### Content inspection tools
Browser-facing utilities read page text and expose safe, size-limited content for higher-level commands.

### `extensions/browser/ufo_ext_browser/bua/content.py`

`domain_logic` · `request handling`

This file answers a simple question: “What is on the current browser page, and where can I find something?” It does not drive the browser directly. Instead, it defines small expectations for a browser session and page reader, then wraps them in BrowserContent methods that return useful JSON-style results.

There are two main kinds of page content here. One is a “tree,” which is a structured text view of page elements, useful for locating buttons, links, inputs, and other visible or interactive parts. The other is markdown text, which is a readable plain-text version of the page, useful when the caller wants the page’s article-like content.

BrowserContent first chooses the right browser tab, using an optional tab_id supplied by the caller. It then asks the session’s page reader for either the element tree or markdown. Large outputs are cut down to fixed limits so a huge webpage cannot overwhelm the rest of the system.

The find method searches the page tree for a user’s query. It can do this with a simple built-in matcher, or, if given a FindCompleter, with an outside completion service such as a language model. In both cases it returns matches plus a human-readable summary.

#### Function details

##### `BrowserContentPageReader.tree`  (lines 23–23)

```
async def tree(self, tab: PageTab, filter_type: str, ref: str | None) -> str | None
```

**Purpose**: This describes the method a page reader must provide to return a structured text view of a browser page. The tree is used when the system needs to understand page elements rather than just page words.

**Data flow**: It receives a browser tab, a filter choice such as all elements or only interactive ones, and an optional reference to a specific element. An implementation reads the page and returns tree text, or returns nothing if the requested referenced element cannot be found.

**Call relations**: BrowserContent relies on this promised method when its tree operation needs page structure. The actual work is supplied by another object that follows this protocol, so this file can stay independent of the browser-reading details.


##### `BrowserContentPageReader.markdown`  (lines 25–25)

```
async def markdown(self, tab: PageTab) -> str
```

**Purpose**: This describes the method a page reader must provide to turn a browser page into readable markdown text. Markdown is plain text with light formatting, useful for reading page content without browser layout details.

**Data flow**: It receives a browser tab. An implementation reads the tab’s page and returns a text version of the content.

**Call relations**: BrowserContent.get_page_text depends on this promised method when callers ask for the readable text of a page. The actual page extraction is done elsewhere by the concrete page reader.


##### `BrowserContentSession.page`  (lines 29–29)

```
async def page(self, tab_id: int | None=None) -> PageTab
```

**Purpose**: This describes how BrowserContent asks for a browser tab to work with. It lets callers either name a tab by ID or use the current/default tab.

**Data flow**: It receives an optional tab ID. An implementation looks up the matching tab, or chooses the active/default tab when no ID is provided, and returns a PageTab object.

**Call relations**: BrowserContent.tree and BrowserContent.get_page_text use this session method before they can read anything from the page. The session object supplies the browser-specific lookup behavior.


##### `BrowserContentSession.page_reader`  (lines 31–31)

```
def page_reader(self) -> BrowserContentPageReader
```

**Purpose**: This describes how BrowserContent gets the object that can read page contents. It separates “which tab are we using?” from “how do we extract content from that tab?”

**Data flow**: It takes no extra input beyond the session itself. It returns a page reader object that knows how to produce element trees and markdown text.

**Call relations**: BrowserContent calls this after it has chosen a tab, then asks the returned reader for either tree or markdown content. This keeps BrowserContent focused on shaping results rather than knowing browser internals.


##### `BrowserContentSession.tab_info`  (lines 33–33)

```
async def tab_info(self, tab: Any) -> JsonDict
```

**Purpose**: This describes how BrowserContent can attach basic tab details to a page-text response. Those details help the caller understand which browser tab the text came from.

**Data flow**: It receives a tab-like object. An implementation reads identifying information about that tab and returns it as a JSON-style dictionary.

**Call relations**: BrowserContent.get_page_text adds this information to its output after reading markdown. The session supplies the details because it owns knowledge of tabs.


##### `BrowserContent.tree`  (lines 40–47)

```
async def tree(self, args: JsonDict, filter_type: str='all') -> str
```

**Purpose**: This gets a structured element tree for a browser page or for a specific referenced element. Callers use it when they need a map of the page’s controls and visible structure.

**Data flow**: It receives request arguments, optionally including tab_id and ref_id, plus a filter type. It turns tab_id into a usable number, asks the browser session for that tab, asks the page reader for a tree, and returns the tree text. If a requested reference cannot be found, it returns a clear message saying so.

**Call relations**: BrowserContent.read_page calls this when returning a page tree to a caller, and BrowserContent.find calls it before searching the page. It uses _tab_id to normalize the caller’s tab ID before asking the browser session for the tab.

*Call graph*: calls 1 internal fn (_tab_id); called by 2 (find, read_page); 1 external calls (get).


##### `BrowserContent.read_page`  (lines 49–53)

```
async def read_page(self, args: JsonDict) -> JsonDict
```

**Purpose**: This returns the page’s element tree in a compact response. It is meant for callers that want to inspect the page structure without receiving an unbounded amount of text.

**Data flow**: It receives request arguments, including an optional filter. It accepts only known filter values, falls back to all elements for anything unexpected, calls BrowserContent.tree, then cuts the result to the maximum allowed size. It returns the shortened tree and a flag saying whether anything was cut off.

**Call relations**: This is a public-facing wrapper around BrowserContent.tree. It prepares the filter choice, delegates the actual page reading, and then packages the result safely for the caller.

*Call graph*: calls 1 internal fn (tree); 1 external calls (get).


##### `BrowserContent.get_page_text`  (lines 55–62)

```
async def get_page_text(self, args: JsonDict) -> JsonDict
```

**Purpose**: This returns the readable text version of a browser page. It is useful when the caller wants page content, such as article text, rather than an element-by-element map.

**Data flow**: It receives request arguments with an optional tab_id. It normalizes the tab ID, gets the tab from the browser session, asks the page reader for markdown text, trims that text to the maximum allowed size, and adds tab information to the returned dictionary.

**Call relations**: This method goes directly through the browser session and page reader rather than using BrowserContent.tree, because it needs markdown instead of an element tree. It uses _tab_id for the same tab selection behavior as tree.

*Call graph*: calls 1 internal fn (_tab_id); 1 external calls (get).


##### `BrowserContent.find`  (lines 64–77)

```
async def find(self, args: JsonDict, complete: FindCompleter | None=None) -> JsonDict
```

**Purpose**: This searches the page’s element tree for a user’s query and returns matching page elements. It helps higher-level tools locate things like buttons or fields without manually scanning the whole page tree.

**Data flow**: It receives request arguments containing a query and optionally a completion function. It validates the query as text, gets the full page tree, then searches it. Without a completion function, it uses the built-in tree matcher. With one, it sends the query and a shortened page tree to that completer, then resolves the reply into matches. It returns the matches and a summary string.

**Call relations**: This method builds on BrowserContent.tree to get the searchable page structure. It then hands the tree to either parse_tree_matches for local matching or to a FindCompleter followed by resolve_find_reply for assisted matching, and finally uses format_matches to create a readable summary.

*Call graph*: calls 1 internal fn (tree); 5 external calls (format_matches, parse_tree_matches, resolve_find_reply, get, as_str).


##### `_tab_id`  (lines 80–89)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: This small helper turns a caller-provided tab ID into an integer, or into nothing if no usable ID was provided. It makes tab selection forgiving when IDs arrive as numbers or strings.

**Data flow**: It receives a JSON-style value. If the value is an integer, it returns it. If it is a float or non-empty string, it converts it to an integer. For missing or unsupported values, it returns None, which means the browser session should choose its default tab.

**Call relations**: BrowserContent.tree and BrowserContent.get_page_text call this before asking the browser session for a page. It keeps tab ID cleanup in one place so both methods behave the same way.

*Call graph*: called by 2 (get_page_text, tree).


### Lookup and coordinate helpers
Supporting utilities search accessibility-tree text and translate model-visible coordinates into real browser viewport positions.

### `extensions/browser/ufo_ext_browser/bua/coordinate.py`

`util` · `request handling`

A browser automation system often asks a vision model, “Where should I click?” The model answers with a point, but that point may not be in the same pixel grid as the live browser. This file is the small conversion toolkit that keeps those worlds lined up.

It defines two simple frozen data shapes: Size for width and height, and Coord for x and y positions. The main issue it solves is screenshot resizing. Claude-family vision inputs may be downscaled by the service if an image is too large, so this code pre-computes the largest screenshot size that Claude can see without extra server-side shrinking. That way, the system knows the exact coordinate space the model is reasoning in.

It also accounts for Gemini models, which report points on a fixed 0-to-1000 grid instead of screenshot pixels. Once the right model coordinate space is known, the file can convert a model’s point into browser viewport pixels for real input, or convert a browser point back into model space. Think of it like converting between a map’s grid and real street distances: the spot is the same, but the measuring ruler changes.

#### Function details

##### `compute_screenshot_dimensions`  (lines 22–33)

```
def compute_screenshot_dimensions(viewport: Size) -> Size
```

**Purpose**: This function works out the screenshot size to send to a Claude-family vision model without triggering extra downscaling by the model provider. It keeps the image as large as safely possible while respecting the provider’s limits.

**Data flow**: It takes the browser viewport size as input. First it shrinks the longer side if it is above the allowed maximum, then it checks the total pixel count and shrinks again if needed. It returns a new Size containing the final width and height that the model should effectively see.

**Call relations**: When no explicit model coordinate size is supplied, effective_model_size calls this function to decide the model’s working image size. It creates the Size object that later coordinate conversions rely on.

*Call graph*: called by 1 (effective_model_size); 1 external calls (__init__).


##### `effective_model_size`  (lines 36–38)

```
def effective_model_size(viewport: Size, model_size: Size | None=None) -> Size
```

**Purpose**: This function chooses the coordinate space the model is using. If the caller gives a specific model size, it uses that; otherwise, it computes the screenshot size from the browser viewport.

**Data flow**: It receives the viewport size and, optionally, a model-space size. If the optional size is present, it passes it through unchanged. If not, it asks compute_screenshot_dimensions to calculate the size and returns that result.

**Call relations**: Both model_to_viewport and viewport_to_model call this before doing their scaling. It is the shared decision point that keeps both conversion directions using the same idea of model space.

*Call graph*: calls 1 internal fn (compute_screenshot_dimensions); called by 2 (model_to_viewport, viewport_to_model).


##### `model_to_viewport`  (lines 41–47)

```
def model_to_viewport(coord: Coord, viewport: Size, model_size: Size | None=None) -> Coord
```

**Purpose**: This function converts a point reported by the model into real browser viewport pixels. It is what makes a model’s suggested click location usable for actual browser input.

**Data flow**: It takes a coordinate in model space, the real viewport size, and optionally an explicit model size. It first determines the model size with effective_model_size, then scales x and y by the ratio between viewport pixels and model-space pixels. It returns a new Coord in browser viewport coordinates.

**Call relations**: After a model identifies a point on the screenshot or model grid, this function is used to translate that point into the browser’s coordinate system. It depends on effective_model_size so it works correctly for both computed screenshot dimensions and caller-provided model sizes.

*Call graph*: calls 1 internal fn (effective_model_size); 1 external calls (__init__).


##### `model_coordinate_space`  (lines 50–55)

```
def model_coordinate_space(model: str | None) -> Size | None
```

**Purpose**: This function chooses whether a model needs a special coordinate grid. In particular, it recognizes Gemini models, which use a fixed 1000 by 1000 coordinate space rather than normal screenshot pixels.

**Data flow**: It receives the model name as text, or nothing. If the name contains “gemini” in any letter case, it returns a Size of 1000 by 1000. Otherwise it returns nothing, which signals that the normal screenshot-based coordinate space should be used.

**Call relations**: This is a helper for code that prepares coordinate conversion for different model families. When it returns a Size, that size can be passed into conversion functions as the explicit model_size; when it returns None, the rest of this file falls back to computed screenshot dimensions.

*Call graph*: 1 external calls (__init__).


##### `viewport_to_model`  (lines 58–64)

```
def viewport_to_model(coord: Coord, viewport: Size, model_size: Size | None=None) -> Coord
```

**Purpose**: This function converts a real browser viewport point back into the coordinate space used by the model. It is useful when the system needs to describe or compare browser positions in the same terms the model uses.

**Data flow**: It takes a browser viewport coordinate, the viewport size, and optionally an explicit model size. It determines the effective model size, then scales x and y from viewport pixels into model-space units. It returns a new Coord in model coordinates.

**Call relations**: This is the reverse partner of model_to_viewport. It calls effective_model_size for the same shared sizing rule, so positions can move back and forth between browser space and model space consistently.

*Call graph*: calls 1 internal fn (effective_model_size); 1 external calls (__init__).


### `extensions/browser/ufo_ext_browser/bua/find.py`

`domain_logic` · `request handling`

A browser page can be described as an accessibility tree: a structured list of visible and interactive things such as buttons, links, headings, and inputs. Elsewhere in the system, that tree is rendered as plain text lines like `- button "Save" [ref=e12] (x=34,y=56)`. This file is the reader for that format. It pulls useful facts out of each line: the element’s role, name, stable reference id, and screen coordinates.

The file supports two ways to find things. First, `parse_tree_matches` performs a basic text search: it breaks the user’s query into words and keeps tree entries whose line contains all those words. This is a simple fallback or fast local search.

Second, `resolve_find_reply` takes a reply from an AI model that was asked to choose matching refs. It does not blindly trust the reply. Instead, it checks every returned reference against the real tree, removes duplicates, and fills in role, name, and coordinates from the source tree. This is important because AI output can be mistaken; the code “grounds” the answer in known page data.

Finally, `format_matches` turns the internal match objects into readable lines. The result is a small safety layer between raw page structure, AI-assisted searching, and user-facing output.

#### Function details

##### `tree_entries`  (lines 31–50)

```
def tree_entries(tree: str) -> list[JsonDict]
```

**Purpose**: Reads the plain-text accessibility tree and extracts one clean record per usable element line. Each record contains the element reference, role, visible name, coordinates, and a lowercase copy of the line for searching.

**Data flow**: It receives the whole tree as a string. It reads the tree line by line, looks for lines with both an element shape and a `[ref=...]` marker, optionally reads coordinates, and skips anything that does not fit. It returns a list of dictionaries, one per recognized element, with missing coordinates treated as `0,0` and missing names treated as an empty string.

**Call relations**: This is the shared parser used before searching or checking AI output. `parse_tree_matches` calls it to get searchable entries, and `resolve_find_reply` calls it to build the trusted list of references that are actually present in the tree.

*Call graph*: called by 2 (parse_tree_matches, resolve_find_reply).


##### `_match_payload`  (lines 53–60)

```
def _match_payload(entry: JsonDict, reason: str) -> JsonDict
```

**Purpose**: Builds the standard result object for a found element. It copies the trusted element details and adds a short explanation of why it matched.

**Data flow**: It receives one parsed tree entry and a reason string. It takes the entry’s reference, role, name, and coordinates, attaches the reason, and returns a new dictionary shaped the way the rest of this file expects matches to look.

**Call relations**: This helper keeps match results consistent. `parse_tree_matches` uses it when a local text search finds an element, and `resolve_find_reply` uses it after validating an AI-suggested reference against the real tree.

*Call graph*: called by 2 (parse_tree_matches, resolve_find_reply).


##### `parse_tree_matches`  (lines 63–71)

```
def parse_tree_matches(tree: str, query: str) -> list[JsonDict]
```

**Purpose**: Performs a simple direct search through the accessibility tree using the user’s query words. It is useful when the system can answer from plain text without needing a model-generated selection.

**Data flow**: It receives the tree text and a query. It lowercases the query, extracts word-like terms longer than one character, parses the tree into entries, and checks whether every query term appears in each entry’s lowercase line. Matching entries are turned into match objects, up to the maximum result limit, and returned as a list.

**Call relations**: This function starts by asking `tree_entries` to turn the tree text into structured entries. For every entry that satisfies the query, it hands the entry to `_match_payload` to produce the output shape used by later display code.

*Call graph*: calls 2 internal fn (_match_payload, tree_entries); 1 external calls (findall).


##### `resolve_find_reply`  (lines 74–102)

```
def resolve_find_reply(reply: str, tree: str) -> tuple[list[JsonDict], bool]
```

**Purpose**: Checks and cleans a model’s answer to a find request. It keeps only references that truly exist in the current accessibility tree, so a mistaken or invented AI reference cannot be passed on as if it were real.

**Data flow**: It receives an AI-style reply string and the original tree text. It first parses the tree into a lookup table keyed by reference id. Then it reads the reply line by line, ignoring empty lines, stopping on `NO_MATCHES`, noticing a `MORE` marker, extracting a reference from each candidate line, removing duplicates, and rejecting refs not found in the tree. For each accepted ref, it returns trusted tree data plus the reply’s reason. It outputs both the match list and a boolean saying whether more matches were reported.

**Call relations**: This function sits between AI output and the rest of the browser automation flow. It calls `tree_entries` so it can verify refs against the real page tree, then uses `_match_payload` to create safe match records that can be formatted or acted on.

*Call graph*: calls 2 internal fn (_match_payload, tree_entries).


##### `format_matches`  (lines 105–114)

```
def format_matches(matches: list[JsonDict], has_more: bool=False) -> str
```

**Purpose**: Turns match records into a readable text summary for a person or caller. It shows each element’s reference, role, name, coordinates, and optional reason.

**Data flow**: It receives a list of match dictionaries and a flag saying whether more matches exist. It builds one display line per match, appending the reason when present. If the flag says there are more results, it adds a final hint asking the user to refine the query. It returns the combined text as one string.

**Call relations**: This is the final presentation step for matches created by `parse_tree_matches` or `resolve_find_reply`. It does not search or validate anything itself; it simply turns already-prepared results into user-friendly output.


### Page serialization and element mapping
Live browser pages are converted into structured model-readable descriptions and returned element references are mapped back to actionable screen points.

### `extensions/browser/ufo_ext_browser/bua/page.py`

`domain_logic` · `request handling, when reading a page or resolving a page element reference`

A web page is messy: it has visible layout, hidden accessibility data, nested frames, and sometimes frames running in separate browser processes. This file combines all of that into one usable map. It asks Chrome, through the Chrome DevTools Protocol (a browser control API), for two views of the page: the DOM snapshot, which gives element positions, and the accessibility tree, which gives human-facing roles and names such as “button” or “search box.” It joins those views together by browser node IDs, then splices iframe documents into the right place so the result feels like one page instead of scattered pieces. The main output is either an ARIA-style tree with stable references like `e12` or `f1e3`, or a simpler Markdown reading view. Those references matter because later actions can point back to the same browser element instead of guessing from text. The file also protects against made-up references by validating their format and checking that the frame is still known. In short, it is the bridge between “what Chrome knows about the page” and “what the model can safely read and interact with.”

#### Function details

##### `split_ref`  (lines 116–120)

```
def split_ref(ref: str) -> tuple[str, int] | None
```

**Purpose**: Breaks a browser element reference, such as `e12` or `f1e3`, into its frame prefix and browser node number. This is how the system tells which frame an element belongs to.

**Data flow**: It receives a reference string. It checks whether the string matches the expected pattern, then returns the frame prefix and numeric backend node ID; if the string is not shaped like a real reference, it returns nothing.

**Call relations**: When rendering only part of a page, `render_page` uses this to find the requested starting element. When an action wants to use a reference, `BrowserPage.resolve_ref` uses it first so invented or malformed references are rejected early.

*Call graph*: called by 2 (resolve_ref, render_page).


##### `Cdp.send`  (lines 124–126)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: Defines the expected shape of a Chrome DevTools Protocol send method. Chrome DevTools Protocol, or CDP, is the browser control channel used to ask Chrome for page data or perform browser actions.

**Data flow**: It takes a CDP method name, optional parameters, and an optional browser session ID. It sends that request to Chrome and returns a JSON-like dictionary response.

**Call relations**: This is a protocol contract rather than an implementation here. `fetch_target` relies on it to collect snapshots and accessibility data, and another part of the browser layer uses the same contract to flush page work.

*Call graph*: called by 2 (fetch_target, _flush_page_tasks).


##### `PageTab.frame_seq`  (lines 161–161)

```
def frame_seq(self, frame_id: str) -> int
```

**Purpose**: Provides a short stable number for a frame ID so references can include frame prefixes like `f1`. This keeps references readable while still separating elements from different frames.

**Data flow**: It receives a browser frame ID. It returns the sequence number assigned to that frame by the tab object.

**Call relations**: `BrowserPage._snapshot_oop` calls this when it discovers a separate-process iframe and needs to give that frame its own reference prefix.

*Call graph*: called by 1 (_snapshot_oop).


##### `BrowserPageSession.connection`  (lines 168–168)

```
def connection(self) -> Cdp
```

**Purpose**: Defines how a browser page session exposes its CDP connection. Other code uses this connection to talk to Chrome.

**Data flow**: It reads the session object and returns an object that can send CDP commands.

**Call relations**: Methods on `BrowserPage` call this whenever they need to fetch snapshots, attach to frames, scroll an element into view, or get element geometry.


##### `BrowserPageSession.init_session`  (lines 170–170)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Defines the setup step needed after attaching to a new browser session. This is especially important for iframes that run in separate browser targets.

**Data flow**: It receives a session ID and performs whatever initialization the broader browser session requires. It does not return page content; it prepares the session for later CDP commands.

**Call relations**: `BrowserPage._oop_session` calls this after attaching to an out-of-process iframe so later snapshot calls can safely use that session.


##### `_float`  (lines 190–195)

```
def _float(value: Json | None, default: float=0.0) -> float
```

**Purpose**: Safely turns a JSON value into a floating-point number. It gives a default when the browser data is missing or not numeric.

**Data flow**: It receives a value and a default. If the value is an integer or float, it returns it as a float; otherwise it returns the default.

**Call relations**: `_parse_document` uses it for coordinates and scroll offsets, and `fetch_target` uses it for the browser device pixel ratio.

*Call graph*: called by 2 (_parse_document, fetch_target).


##### `_attr`  (lines 198–203)

```
def _attr(strings: list[str], attrs: list[Json], name: str) -> str | None
```

**Purpose**: Finds one named HTML attribute in Chrome’s compact snapshot format. This is used for details such as input type, image source, and iframe source.

**Data flow**: It receives the shared string table, an attribute list, and an attribute name. It walks the list in key-value pairs and returns the matching text value, or nothing if it is absent.

**Call relations**: `_parse_document` calls this while reading DOM snapshot nodes that need extra information beyond their position.

*Call graph*: called by 1 (_parse_document).


##### `_parse_document`  (lines 206–266)

```
def _parse_document(doc: JsonDict, strings: list[str], dpr: float) -> _RawDoc
```

**Purpose**: Reads one document from Chrome’s DOM snapshot and extracts the geometry and special node details the rest of the file needs. It turns Chrome’s dense snapshot data into a simpler per-document record.

**Data flow**: It receives one raw document, the snapshot string table, and the device pixel ratio. It reads node IDs, layout bounds, scroll offsets, element attributes, and iframe links, then returns a `_RawDoc` containing node geometry and iframe bookkeeping.

**Call relations**: `parse_snapshot` calls this once for each document in the snapshot. It uses `_float` for safe numeric conversion and `_attr` to pull useful HTML attributes from compact browser data.

*Call graph*: calls 2 internal fn (_attr, _float); called by 1 (parse_snapshot); 6 external calls (__init__, __init__, get, as_int, as_list, as_map).


##### `parse_snapshot`  (lines 269–318)

```
def parse_snapshot(snapshot: JsonDict, dpr: float, base_origin: tuple[float, float]=(0.0, 0.0)) -> list[DocData]
```

**Purpose**: Turns Chrome’s full DOM snapshot into a list of documents with real page coordinates. It also identifies normal iframes and possible out-of-process iframes.

**Data flow**: It receives the raw snapshot, the device pixel ratio, and a starting origin. It parses each document, works out where child documents sit inside parent iframe boxes, adjusts element bounds into page coordinates, and returns `DocData` objects.

**Call relations**: `fetch_target` calls this after capturing the DOM snapshot. It depends on `_parse_document` to read each document before it links them together.

*Call graph*: calls 1 internal fn (_parse_document); called by 1 (fetch_target); 5 external calls (__init__, get, as_list, as_map, as_str).


##### `fetch_target`  (lines 321–383)

```
async def fetch_target(cdp: Cdp, session_id: str, *, root_prefix: str, prefix_for: Callable[[str], str], base_origin: tuple[float, float]=(0.0, 0.0)) -> FrameSnapshot
```

**Purpose**: Collects everything needed to describe one browser target: layout positions, accessibility nodes, frame relationships, and reference prefixes. A browser target is a page or frame session Chrome can talk to directly.

**Data flow**: It receives a CDP connection, a session ID, frame-prefix helpers, and an origin. It enables browser snapshot features, captures DOM geometry, reads the device pixel ratio, fetches the accessibility tree for each document, and returns a `FrameSnapshot` tree for that target.

**Call relations**: `BrowserPage._snapshot_target` calls this as the core snapshot step. Inside, it uses `Cdp.send` for browser requests, `parse_snapshot` to interpret DOM layout, and then joins that with accessibility-tree data.

*Call graph*: calls 3 internal fn (send, _float, parse_snapshot); called by 1 (_snapshot_target); 5 external calls (__init__, gather, as_list, as_map, as_str).


##### `_ax_value`  (lines 386–390)

```
def _ax_value(value: Json | None) -> str
```

**Purpose**: Extracts the plain value from a Chrome accessibility field. Accessibility fields often wrap the useful value inside a small dictionary.

**Data flow**: It receives a JSON value. If it is a dictionary with a `value` entry, it returns that entry as text; otherwise it returns an empty string.

**Call relations**: Both `render_page.render_node` and `render_markdown.walk` use this to read roles and names from accessibility nodes before deciding how to display them.

*Call graph*: called by 2 (walk, render_node); 1 external calls (get).


##### `_ax_property`  (lines 393–401)

```
def _ax_property(node: JsonDict, name: str) -> Json
```

**Purpose**: Looks up one named property on an accessibility node, such as `checked`, `disabled`, `url`, or heading `level`.

**Data flow**: It receives an accessibility node and a property name. It scans the node’s property list and returns the property’s usable value, or nothing if the property is absent.

**Call relations**: `_format_extras`, `render_page.render_node`, and `render_markdown.walk` use this whenever they need state or extra meaning from an accessibility node.

*Call graph*: called by 3 (_format_extras, walk, render_node); 3 external calls (get, as_list, as_map).


##### `_should_skip`  (lines 404–410)

```
def _should_skip(node: JsonDict, role: str, name: str) -> bool
```

**Purpose**: Decides whether a structural accessibility node should be hidden from the rendered page tree. This keeps empty containers from cluttering the model’s view.

**Data flow**: It receives a node, its role, and its name. It skips only certain container-like roles that have no name and no important state properties.

**Call relations**: `render_page.render_node` calls this while deciding whether to print a node or pass through to its children.

*Call graph*: called by 1 (render_node); 3 external calls (get, as_list, as_map).


##### `_truncate`  (lines 413–416)

```
def _truncate(text: str, max_len: int) -> str
```

**Purpose**: Shortens long text so page renderings stay readable. It uses an ellipsis to show that text was cut.

**Data flow**: It receives text and a maximum length. It returns the text unchanged if it fits, or a shortened version if it is too long.

**Call relations**: `_image_name`, `_format_extras`, and `render_page.render_node` use this before putting names and values into the model-facing page output.

*Call graph*: called by 3 (_format_extras, _image_name, render_node).


##### `_image_name`  (lines 419–426)

```
def _image_name(src: str | None) -> str
```

**Purpose**: Creates a fallback name for an image from its file name when the accessibility tree does not provide useful alt text.

**Data flow**: It receives an image source URL. It extracts the last path segment, keeps it only if it looks like a file name with an extension, truncates it if needed, and returns that text.

**Call relations**: `render_page.render_node` and `render_markdown.walk` call this when an image needs a human-readable label but no explicit name is available.

*Call graph*: calls 1 internal fn (_truncate); called by 2 (walk, render_node); 1 external calls (urlparse).


##### `_format_extras`  (lines 429–459)

```
def _format_extras(node: JsonDict, geom: NodeGeom | None) -> str
```

**Purpose**: Builds the extra state text shown beside an element in the page tree, such as checked state, disabled state, input type, or safe URL.

**Data flow**: It receives an accessibility node and optional geometry details. It reads known properties, filters out empty or unsafe values, truncates long values, and returns a suffix string ready to append to a rendered line.

**Call relations**: `render_page.render_node` calls this after deciding to show a node, so the line includes useful state without overwhelming the main role-and-name view.

*Call graph*: calls 2 internal fn (_ax_property, _truncate); called by 1 (render_node).


##### `_frame_by_prefix`  (lines 462–469)

```
def _frame_by_prefix(root: FrameSnapshot, prefix: str) -> FrameSnapshot | None
```

**Purpose**: Finds the frame snapshot that owns a given reference prefix. This lets the system locate `f1e23` inside frame `f1` instead of searching the wrong document.

**Data flow**: It receives the root frame snapshot and a prefix. It searches the frame tree recursively and returns the matching frame, or nothing if no frame has that prefix.

**Call relations**: `render_page` uses this when asked to render from a specific reference rather than from the whole page root.

*Call graph*: called by 1 (render_page).


##### `_node_by_backend`  (lines 472–476)

```
def _node_by_backend(frame: FrameSnapshot, backend_id: int) -> str | None
```

**Purpose**: Finds the accessibility node that matches a browser backend DOM node ID. This connects an action/reference ID back to the readable accessibility tree.

**Data flow**: It receives a frame snapshot and a backend node ID. It scans the frame’s accessibility nodes and returns the matching accessibility node ID, or nothing if not found.

**Call relations**: `render_page` uses this after `split_ref` and `_frame_by_prefix` when it needs to start rendering at one referenced element.

*Call graph*: called by 1 (render_page).


##### `render_page`  (lines 479–575)

```
def render_page(root: FrameSnapshot, *, viewport: Size, model_size: Size | None=None, filter_type: str='all', max_depth: int=DEFAULT_MAX_DEPTH, ref: str | None=None) -> str | None
```

**Purpose**: Renders a `FrameSnapshot` as a compact tree that an AI model can read and use for interaction. The output includes roles, names, references, center coordinates, and selected element state.

**Data flow**: It receives a snapshot, viewport size, optional model size, filter settings, depth limit, and optional starting reference. It scales coordinates if needed, walks the accessibility tree, skips noisy nodes, splices child frames into iframe positions, and returns a newline-separated page tree or nothing if a requested reference cannot be found.

**Call relations**: `BrowserPage.tree` calls this after taking a fresh snapshot. It uses `split_ref`, `_frame_by_prefix`, and `_node_by_backend` for reference-limited rendering, while its nested helpers do the actual line-building walk.

*Call graph*: calls 3 internal fn (_frame_by_prefix, _node_by_backend, split_ref); called by 1 (tree); 1 external calls (effective_model_size).


##### `render_page.coord_str`  (lines 494–498)

```
def coord_str(geom: NodeGeom | None) -> str
```

**Purpose**: Formats an element’s center point for the rendered page tree. This gives the model a rough coordinate target for visible elements.

**Data flow**: It receives optional geometry. If bounds are present, it computes the center, scales it into model space, and returns text like `(x=...,y=...)`; otherwise it returns an empty string.

**Call relations**: `render_page.render_node` calls this while building each displayed line.


##### `render_page.splice`  (lines 500–503)

```
def splice(frame: FrameSnapshot, backend_id: int | None, depth: int) -> None
```

**Purpose**: Inserts a child frame’s accessibility tree at the iframe element where it appears. This makes nested documents read like part of the same page.

**Data flow**: It receives a frame, an optional backend node ID, and the current depth. If that backend node has a child frame with a root accessibility node, it starts rendering that child at the same place in the tree.

**Call relations**: `render_page.render_node.descend` calls this after rendering normal child accessibility nodes, so iframe contents are included at the correct point.


##### `render_page.render_node`  (lines 505–559)

```
def render_node(frame: FrameSnapshot, ax_id: str, depth: int, parent_name: str) -> None
```

**Purpose**: Walks one accessibility node and decides whether and how it should appear in the page tree. This is the main filtering and formatting step inside `render_page`.

**Data flow**: It receives a frame, an accessibility node ID, a depth, and the parent name. It reads role, name, backend ID, children, geometry, and state; skips hidden or noisy nodes; optionally filters by viewport or interactivity; appends a formatted line; then continues to children.

**Call relations**: This nested function is driven by `render_page` from either the page root or a referenced node. It calls helper functions such as `_ax_value`, `_ax_property`, `_format_extras`, `_should_skip`, `_truncate`, and `_image_name` to keep each line useful and concise.

*Call graph*: calls 6 internal fn (_ax_property, _ax_value, _format_extras, _image_name, _should_skip, _truncate); 2 external calls (as_list, as_str).


##### `render_page.render_node.descend`  (lines 521–524)

```
def descend(child_depth: int, child_parent_name: str) -> None
```

**Purpose**: Continues rendering from a node into its children and any iframe content attached to it. It is the small “go deeper” helper for the tree walk.

**Data flow**: It receives the next depth and the parent name to compare against. It renders each child accessibility node and then asks `splice` to include a child frame if the current node is an iframe.

**Call relations**: `render_page.render_node` calls this when a node should be transparent, and again after printing a node so its children appear underneath it.


##### `render_markdown`  (lines 583–651)

```
def render_markdown(root: FrameSnapshot) -> str
```

**Purpose**: Renders the page snapshot as a reading-oriented Markdown document. This is useful when the model needs content and structure more than clickable references.

**Data flow**: It receives a root frame snapshot. It walks the accessibility tree, converts headings, links, list items, images, and paragraphs into Markdown blocks, includes child frames, and returns the joined Markdown text.

**Call relations**: `BrowserPage.markdown` calls this after taking a snapshot. Its nested `walk`, `emit`, and `flush` helpers cooperate to turn accessibility nodes into readable blocks instead of a raw text dump.

*Call graph*: called by 1 (markdown).


##### `render_markdown.emit`  (lines 592–595)

```
def emit(text: str) -> None
```

**Purpose**: Adds one completed Markdown block if it is not empty and not a duplicate of the previous block.

**Data flow**: It receives text, trims surrounding whitespace, and appends it to the block list only when it contains content and is not the same as the last emitted block.

**Call relations**: `render_markdown.flush` and `render_markdown.walk` call this whenever a paragraph, heading, list item, image, or named block is ready.


##### `render_markdown.flush`  (lines 597–600)

```
def flush() -> None
```

**Purpose**: Turns collected inline text into a completed Markdown block. Inline text is text gathered while walking through smaller nodes inside a paragraph-like area.

**Data flow**: It reads the current inline text list. If there is any text, it joins the pieces with spaces, emits the result, and clears the inline list.

**Call relations**: `render_markdown.walk` calls this before and after block-level structures so paragraphs do not accidentally run together.


##### `render_markdown.walk`  (lines 602–646)

```
def walk(frame: FrameSnapshot, ax_id: str, parent_name: str) -> None
```

**Purpose**: Walks the accessibility tree and translates nodes into Markdown meaning. It preserves structure such as headings, links, bullet items, and images.

**Data flow**: It receives a frame, an accessibility node ID, and the parent name. It reads role, name, backend ID, children, and properties, updates inline text or emitted blocks, then walks child nodes and child frames.

**Call relations**: `render_markdown` starts this walk at the root accessibility node. It uses `_ax_value`, `_ax_property`, and `_image_name` to interpret browser accessibility data in human-readable form.

*Call graph*: calls 3 internal fn (_ax_property, _ax_value, _image_name); 2 external calls (as_list, as_str).


##### `BrowserPage.tree`  (lines 660–668)

```
async def tree(self, tab: PageTab, filter_type: str, ref: str | None) -> str | None
```

**Purpose**: Produces the interactive page tree for one tab. This is the high-level method used when the system wants a model-readable view with element references.

**Data flow**: It receives a tab, a filter type, and an optional reference. It takes a fresh snapshot, renders it with viewport and model-size information, and returns the tree text or nothing if the requested reference is invalid.

**Call relations**: This is a public-facing method on `BrowserPage`. It calls `BrowserPage.snapshot` to collect current browser state, then hands the result to `render_page`.

*Call graph*: calls 2 internal fn (snapshot, render_page).


##### `BrowserPage.markdown`  (lines 670–671)

```
async def markdown(self, tab: PageTab) -> str
```

**Purpose**: Produces a reading view of the current tab as Markdown. This is useful for understanding page content without interaction details.

**Data flow**: It receives a tab. It takes a fresh snapshot and converts it into Markdown text.

**Call relations**: This method calls `BrowserPage.snapshot` first, then passes the snapshot to `render_markdown`.

*Call graph*: calls 2 internal fn (snapshot, render_markdown).


##### `BrowserPage.resolve_ref`  (lines 673–686)

```
def resolve_ref(self, tab: PageTab, ref: str) -> tuple[FrameNode, int]
```

**Purpose**: Validates an element reference and finds the frame information needed to use it. It prevents the system from acting on made-up or stale references.

**Data flow**: It receives a tab and a reference string. It parses the reference, looks up the frame prefix in the tab’s registered frames, and returns the matching `FrameNode` plus backend node ID; if anything is wrong, it raises a `HallucinationError` with instructions to re-read the page.

**Call relations**: `BrowserPage.ref_point` calls this before asking Chrome for an element’s coordinates. It relies on `split_ref` and on frame registrations created by `BrowserPage.snapshot`.

*Call graph*: calls 1 internal fn (split_ref); called by 1 (ref_point); 1 external calls (__init__).


##### `BrowserPage.ref_point`  (lines 688–714)

```
async def ref_point(self, tab: PageTab, ref: str) -> tuple[int, int]
```

**Purpose**: Turns a valid browser reference into a clickable point on the page. It scrolls the element into view before measuring it.

**Data flow**: It receives a tab and reference. It resolves the reference to a frame and backend node ID, asks Chrome to scroll the node into view, asks for its content quadrilateral or box model, averages the four corners, adds the frame origin, and returns integer x/y coordinates.

**Call relations**: This method builds on `BrowserPage.resolve_ref`. It talks to Chrome through the browser connection and uses `_coord_float_or_default` to safely read coordinate values; if Chrome says the node is gone, it raises a `HallucinationError`.

*Call graph*: calls 2 internal fn (resolve_ref, _coord_float_or_default); 3 external calls (__init__, as_list, as_map).


##### `BrowserPage.snapshot`  (lines 716–720)

```
async def snapshot(self, tab: PageTab) -> FrameSnapshot
```

**Purpose**: Captures the current tab as a full frame snapshot tree and refreshes the tab’s reference-to-frame map. This is the central “read the page now” operation.

**Data flow**: It receives a tab. It snapshots the main target, clears old reference-frame mappings, registers every frame in the new snapshot, and returns the root `FrameSnapshot`.

**Call relations**: `BrowserPage.tree` and `BrowserPage.markdown` call this before rendering. It delegates collection to `_snapshot_target` and bookkeeping to `_register_frames`.

*Call graph*: calls 2 internal fn (_register_frames, _snapshot_target); called by 2 (markdown, tree).


##### `BrowserPage._snapshot_target`  (lines 722–739)

```
async def _snapshot_target(self, tab: PageTab, session_id: str, root_prefix: str, origin: tuple[float, float], depth: int) -> FrameSnapshot
```

**Purpose**: Snapshots one CDP target and, when allowed, attaches any separate-process iframes beneath it. This keeps the frame tree complete across browser process boundaries.

**Data flow**: It receives a tab, session ID, reference prefix, origin, and recursion depth. It fetches the target snapshot, then optionally searches for out-of-process iframes and attaches their snapshots, returning the completed root frame for that target.

**Call relations**: `BrowserPage.snapshot` uses this for the main page, and `_snapshot_oop` uses it recursively for child targets. It calls `fetch_target` for the basic target data and `_attach_oop_frames` for special iframe cases.

*Call graph*: calls 2 internal fn (_attach_oop_frames, fetch_target); called by 2 (_snapshot_oop, snapshot).


##### `BrowserPage._attach_oop_frames`  (lines 741–749)

```
async def _attach_oop_frames(self, tab: PageTab, root: FrameSnapshot, depth: int) -> None
```

**Purpose**: Looks through a snapshot for iframes that Chrome did not include as normal child documents because they run in separate targets. It tries to snapshot each one and attach it in place.

**Data flow**: It receives a tab, a root frame snapshot, and the current depth. It walks the frame tree, checks each recorded out-of-process iframe backend ID, snapshots any reachable child, and inserts it under the iframe node.

**Call relations**: `BrowserPage._snapshot_target` calls this after `fetch_target` has identified possible out-of-process iframes. It delegates each individual iframe to `_snapshot_oop`.

*Call graph*: calls 1 internal fn (_snapshot_oop); called by 1 (_snapshot_target).


##### `BrowserPage._snapshot_oop`  (lines 751–777)

```
async def _snapshot_oop(self, tab: PageTab, frame: FrameSnapshot, backend_id: int, depth: int) -> FrameSnapshot | None
```

**Purpose**: Snapshots one out-of-process iframe if Chrome can describe and attach to it. Out-of-process means the iframe is controlled through a different browser target/session.

**Data flow**: It receives the tab, parent frame, iframe backend node ID, and depth. It asks Chrome for the iframe’s frame ID, gets or creates a CDP session for that frame, computes the child origin from iframe bounds, and returns the child snapshot; on recoverable failures it logs a warning and returns nothing.

**Call relations**: `BrowserPage._attach_oop_frames` calls this for each out-of-process iframe candidate. It uses `PageTab.frame_seq` for the child prefix, `_oop_session` for the browser session, and `_snapshot_target` to collect the child frame.

*Call graph*: calls 3 internal fn (_oop_session, _snapshot_target, frame_seq); called by 1 (_attach_oop_frames); 1 external calls (as_map).


##### `BrowserPage._register_frames`  (lines 779–782)

```
def _register_frames(self, tab: PageTab, frame: FrameSnapshot) -> None
```

**Purpose**: Records every frame prefix in the tab so later references can be resolved. This is what makes references like `f1e3` actionable after a page read.

**Data flow**: It receives a tab and a frame snapshot. It stores the frame ID, session ID, and origin under the frame’s prefix, then repeats the same for all child frames.

**Call relations**: `BrowserPage.snapshot` calls this after building a fresh frame tree. `BrowserPage.resolve_ref` later depends on this map to find the correct frame for a reference.

*Call graph*: called by 1 (snapshot); 1 external calls (__init__).


##### `BrowserPage._oop_session`  (lines 784–797)

```
async def _oop_session(self, frame_id: str) -> str | None
```

**Purpose**: Gets a CDP session for an out-of-process frame, reusing a cached one when possible. This avoids attaching to the same frame repeatedly.

**Data flow**: It receives a frame ID. It first checks the browser’s cached sessions; if missing, it asks Chrome to attach to that target, initializes the new session, caches it, and returns the session ID, or returns nothing if attach fails.

**Call relations**: `BrowserPage._snapshot_oop` calls this before trying to snapshot an out-of-process iframe. It works with the broader browser session through `connection`, `init_session`, and the `oop_sessions` cache.

*Call graph*: called by 1 (_snapshot_oop); 1 external calls (as_str).


##### `_coord_float_or_default`  (lines 800–809)

```
def _coord_float_or_default(value: Json | None, default: float) -> float
```

**Purpose**: Converts coordinate values from Chrome into floats, accepting numbers and numeric strings. It gives a default for missing values but rejects invalid non-numeric data.

**Data flow**: It receives a JSON value and a default. It returns a float for numbers or non-empty strings, returns the default for `None`, and raises a validation error for anything else.

**Call relations**: `BrowserPage.ref_point` uses this while averaging the corners of an element’s measured box.

*Call graph*: called by 1 (ref_point); 1 external calls (__init__).
