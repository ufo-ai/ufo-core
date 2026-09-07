# BUA page understanding and element targeting  `stage-12.5`

This stage is the browser’s “page understanding” layer. It runs during the main work loop, after a page is open and before the system decides where to click, type, or read. Its job is to turn a complex live web page into a safer, simpler description that an AI can use.

The page module is the main scanner. It captures the page’s accessibility tree, which is a browser-made outline of visible controls and text, along with positions, frames, and stable element references. It can render this either as an action-focused tree for clicking and typing, or as markdown for reading.

The content module wraps this page data for higher-level tools. It extracts text or search results and keeps responses within safe size limits. The find module searches the rendered accessibility text for matching buttons, links, fields, and other elements, then checks that matches still point to real targets. The coordinate module acts like a map converter, translating screenshot points from an AI vision model into actual browser click positions and choosing screenshot sizes that avoid image distortion.

## Files in this stage

### Content access
High-level content helpers expose safe, size-limited page text and search results to callers.

### `extensions/browser/ufo_ext_browser/bua/content.py`

`domain_logic` · `request handling`

This file is the browser-content layer. Its job is to answer questions like “What is on this page?”, “Give me the page text,” and “Find the thing matching this query.” Without it, other parts of the system would have to know how to pick a browser tab, ask the page reader for data, trim very large responses, and format search results themselves.

The file separates the browser connection from the content operations. `BrowserContentSession` describes what a browser session must provide: a current or chosen tab, a page reader, and tab details. `BrowserContentPageReader` describes the two kinds of page views this layer needs: a structured “tree” of page elements and a markdown-like text version of the page.

`BrowserContent` then uses those pieces. For a structured read, it chooses the right tab, optionally focuses on a referenced element, asks the page reader for the tree, and reports if the element is missing. For a plain text read, it asks for markdown and adds tab metadata. For searching, it reads the page tree and either performs a local text-based match or asks an optional AI-style completer to interpret the query. Large outputs are capped so a huge web page does not overwhelm the rest of the system, much like photocopying only the first fixed number of pages from a giant book.

#### Function details

##### `BrowserContentPageReader.tree`  (lines 23–23)

```
async def tree(self, tab: PageTab, filter_type: str, ref: str | None) -> str | None
```

**Purpose**: This is a required shape for any page reader that can return a structured view of a browser page. The structure is a text tree of page elements, optionally filtered or focused on one referenced element.

**Data flow**: It receives a browser tab, a filter choice such as all elements or only interactive ones, and an optional element reference. A concrete implementation reads the page and returns the matching tree text, or returns nothing if the requested reference cannot be found.

**Call relations**: This protocol method is used indirectly by `BrowserContent.tree`. `BrowserContent` does not care which browser engine supplies the tree, only that the session’s page reader follows this contract.


##### `BrowserContentPageReader.markdown`  (lines 25–25)

```
async def markdown(self, tab: PageTab) -> str
```

**Purpose**: This is a required shape for any page reader that can return the readable text of a browser page. The text is in a markdown-like format, meaning it preserves simple document structure such as headings and links.

**Data flow**: It receives a browser tab. A concrete implementation reads that tab’s page and returns a single text string representing the page content.

**Call relations**: This protocol method is used by `BrowserContent.get_page_text`. It lets the content layer ask for page text without knowing the low-level browser details.


##### `BrowserContentSession.page`  (lines 29–29)

```
async def page(self, tab_id: int | None=None) -> PageTab
```

**Purpose**: This is the session contract for choosing which browser tab to work with. It can return a specific tab when given an ID, or the default/current tab when no ID is given.

**Data flow**: It receives an optional tab ID. A concrete browser session uses that ID, or its own current-tab logic, and returns a `PageTab` object representing the selected page.

**Call relations**: Both `BrowserContent.tree` and `BrowserContent.get_page_text` rely on this method before reading content. It is the first step that turns a caller’s request into a real browser page.


##### `BrowserContentSession.page_reader`  (lines 31–31)

```
def page_reader(self) -> BrowserContentPageReader
```

**Purpose**: This is the session contract for getting the object that knows how to read page contents. It keeps browser navigation and page-reading details separate.

**Data flow**: It takes no extra input beyond the session itself. It returns a page reader that can produce a page tree or markdown text.

**Call relations**: After `BrowserContent` has selected a tab, it asks the session for this reader. The returned reader then performs the actual page-content extraction.


##### `BrowserContentSession.tab_info`  (lines 33–33)

```
async def tab_info(self, tab: Any) -> JsonDict
```

**Purpose**: This is the session contract for collecting basic information about a tab, such as details useful to include with returned page text. It enriches content results with context about where the text came from.

**Data flow**: It receives a tab-like object. A concrete session looks up information for that tab and returns it as a JSON-style dictionary.

**Call relations**: `BrowserContent.get_page_text` calls this after reading markdown. The tab information is merged into the response so callers get both the text and its browser context.


##### `BrowserContent.tree`  (lines 40–47)

```
async def tree(self, args: JsonDict, filter_type: str='all') -> str
```

**Purpose**: This returns a structured text tree of the current or requested browser page. Callers use it when they need a machine-readable overview of page elements rather than just plain page text.

**Data flow**: It reads `tab_id` and optional `ref_id` from the input dictionary. It converts the tab ID into an integer when possible, asks the browser session for that tab, asks the page reader for a tree using the requested filter and reference, and returns the tree text. If a reference was requested but no matching element is found, it returns a clear message saying so.

**Call relations**: `BrowserContent.read_page` calls this to return a clipped page tree, and `BrowserContent.find` calls it before searching. It hands tab selection to `_tab_id` and the browser session, then hands page extraction to the session’s page reader.

*Call graph*: calls 1 internal fn (_tab_id); called by 2 (find, read_page); 1 external calls (get).


##### `BrowserContent.read_page`  (lines 49–53)

```
async def read_page(self, args: JsonDict) -> JsonDict
```

**Purpose**: This provides a safe, public-style read of a page’s element tree. It lets callers choose a broad filter but prevents oversized page trees from being returned in full.

**Data flow**: It reads the optional `filter` value from the input dictionary. If the filter is one of the allowed choices, it uses it; otherwise it falls back to `all`. It asks `BrowserContent.tree` for the page tree, cuts the result to `MAX_READ_CHARS`, and returns both the clipped tree and a `truncated` flag showing whether anything was left out.

**Call relations**: This function is a wrapper around `BrowserContent.tree`. It is used when a caller wants page structure, while this function adds input cleanup and output size protection around the lower-level tree read.

*Call graph*: calls 1 internal fn (tree); 1 external calls (get).


##### `BrowserContent.get_page_text`  (lines 55–62)

```
async def get_page_text(self, args: JsonDict) -> JsonDict
```

**Purpose**: This returns the page’s readable text rather than its element tree. It is useful when the caller wants the article-like content of a page, not every button and control.

**Data flow**: It reads an optional `tab_id` from the input dictionary and converts it with `_tab_id`. It asks the browser session for the tab, asks the page reader for markdown text, trims that text to `MAX_TEXT_CHARS`, marks whether it was truncated, and adds tab information from the browser session.

**Call relations**: This function follows the same tab-selection pattern as `BrowserContent.tree`, but then uses the page reader’s markdown view instead of the tree view. It also calls the session for tab metadata so the final response contains both content and context.

*Call graph*: calls 1 internal fn (_tab_id); 1 external calls (get).


##### `BrowserContent.find`  (lines 64–77)

```
async def find(self, args: JsonDict, complete: FindCompleter | None=None) -> JsonDict
```

**Purpose**: This searches the browser page for elements or text matching a user query. It can do a direct local search, or, if given a completion function, ask a smarter language-model-style helper to interpret the page tree.

**Data flow**: It reads and validates the `query` field from the input dictionary, then gets the full page tree through `BrowserContent.tree`. If no completer is supplied, it parses the tree locally for matches and decides whether there may be more results. If a completer is supplied, it sends the query and a shortened page tree to that completer, then resolves the reply back against the real tree. It returns a list of matches and a human-readable summary.

**Call relations**: This function builds on `BrowserContent.tree` because searching needs the page’s structured element view first. It then hands matching work either to `parse_tree_matches` for local searching or to the supplied completer plus `resolve_find_reply` for assisted searching, and finally uses `format_matches` to summarize what was found.

*Call graph*: calls 1 internal fn (tree); 5 external calls (format_matches, parse_tree_matches, resolve_find_reply, get, as_str).


##### `_tab_id`  (lines 80–89)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: This small helper turns a caller-provided tab ID into an integer the browser session can use. It accepts IDs sent as numbers or non-empty strings, and treats missing or unsupported values as “no specific tab.”

**Data flow**: It receives a JSON-style value. If the value is an integer, it returns it unchanged; if it is a float or non-empty string, it converts it to an integer; otherwise it returns `None`.

**Call relations**: `BrowserContent.tree` and `BrowserContent.get_page_text` call this before asking the browser session for a page. It keeps tab-ID cleanup in one place so those content functions can share the same behavior.

*Call graph*: called by 2 (get_page_text, tree).


### Coordinate mapping
Coordinate utilities align AI-visible screenshots with browser click and movement coordinates.

### `extensions/browser/ufo_ext_browser/bua/coordinate.py`

`util` · `request handling`

When an AI model looks at a browser screenshot and says “click at x=500, y=300,” those numbers may not match the browser’s real pixel grid. The screenshot may have been resized before being sent to the model, and some models use their own fixed coordinate system. This file is the small ruler-and-converter that keeps those worlds aligned.

It defines two simple frozen data shapes: Size, for width and height, and Coord, for x and y positions. The main job is to work out the “model coordinate space,” meaning the grid the model is reasoning in, and convert points back and forth between that grid and the browser viewport, which is the visible browser area in real pixels.

For Claude-family vision models, the file tries to keep screenshots under Anthropic’s limits: no side longer than 1568 pixels and no image above about 1.15 million pixels. This avoids server-side downscaling, which would make coordinate mapping less predictable. For Gemini, it knows that coordinates are reported on a fixed 0-to-1000 grid, regardless of image size.

Without this file, clicks could land in the wrong place: like using directions from a map printed at one scale on a street drawn at another scale.

#### Function details

##### `compute_screenshot_dimensions`  (lines 22–33)

```
def compute_screenshot_dimensions(viewport: Size) -> Size
```

**Purpose**: Chooses the largest screenshot size that should reach the vision model without being automatically shrunk by the provider. This keeps the image clear while preserving predictable coordinates.

**Data flow**: It receives the browser viewport size. It first scales the width and height down only if the longest side is too large, then checks whether the total pixel count is still too high and shrinks again if needed. It returns a new Size with the safe screenshot width and height, leaving the input unchanged.

**Call relations**: This is the fallback sizing rule used when no model-specific coordinate size is supplied. effective_model_size calls it when it needs to know what coordinate grid the model will see for ordinary screenshot-based models.

*Call graph*: called by 1 (effective_model_size); 1 external calls (__init__).


##### `effective_model_size`  (lines 36–38)

```
def effective_model_size(viewport: Size, model_size: Size | None=None) -> Size
```

**Purpose**: Decides which coordinate grid should be treated as the model’s working space. It uses an explicit model size if one is given; otherwise it computes the safe screenshot size from the viewport.

**Data flow**: It receives the real browser viewport size and optionally a model coordinate size. If the optional size exists, it returns that directly. If not, it passes the viewport to compute_screenshot_dimensions and returns the calculated screenshot size.

**Call relations**: This function sits between the conversion functions and the screenshot sizing rule. model_to_viewport and viewport_to_model both call it first so they can scale points using the right model-side dimensions.

*Call graph*: calls 1 internal fn (compute_screenshot_dimensions); called by 2 (model_to_viewport, viewport_to_model).


##### `model_to_viewport`  (lines 41–47)

```
def model_to_viewport(coord: Coord, viewport: Size, model_size: Size | None=None) -> Coord
```

**Purpose**: Converts a point from the model’s coordinate system into real browser viewport pixels. This is what lets the browser click where the model intended.

**Data flow**: It receives a model-space coordinate, the real viewport size, and optionally a model coordinate size. It gets the effective model size, compares model width to viewport width and model height to viewport height, then scales x and y into browser pixels. It returns a new Coord in viewport coordinates.

**Call relations**: This is used after a model has chosen a point on the screenshot or model grid. It relies on effective_model_size to know the scale, then creates the browser-ready coordinate that input dispatch can use.

*Call graph*: calls 1 internal fn (effective_model_size); 1 external calls (__init__).


##### `model_coordinate_space`  (lines 50–55)

```
def model_coordinate_space(model: str | None) -> Size | None
```

**Purpose**: Reports whether a model uses a special fixed coordinate grid. In particular, it marks Gemini models as using a 1000 by 1000 coordinate space.

**Data flow**: It receives a model name, or nothing. If the name exists and contains “gemini” ignoring case, it returns Size(1000, 1000). For all other names, it returns None, which means the code should use screenshot dimensions instead.

**Call relations**: This function is a small model-specific rule that other browser-automation code can call before converting coordinates. Its returned Size can be passed into model_to_viewport or viewport_to_model as the explicit model size.

*Call graph*: 1 external calls (__init__).


##### `viewport_to_model`  (lines 58–64)

```
def viewport_to_model(coord: Coord, viewport: Size, model_size: Size | None=None) -> Coord
```

**Purpose**: Converts a real browser pixel position back into the model’s coordinate system. This is useful when code needs to describe a browser location in the same scale the model understands.

**Data flow**: It receives a viewport coordinate, the viewport size, and optionally a model coordinate size. It finds the effective model size, scales x and y from browser pixels into model-space units, and returns a new Coord with those converted values.

**Call relations**: This is the reverse path of model_to_viewport. It calls effective_model_size for the same shared sizing rule, then produces a coordinate suitable for model-facing descriptions or comparisons.

*Call graph*: calls 1 internal fn (effective_model_size); 1 external calls (__init__).


### Element lookup and page rendering
Element search routines and page rendering logic turn accessibility data into actionable text views with stable target references.

### `extensions/browser/ufo_ext_browser/bua/find.py`

`domain_logic` · `request handling`

This file is the “find things on the page” helper for a browser automation system. The browser page is represented as an accessibility tree: a plain-text list of visible or interactive items, each with a role like button or link, a human-readable name, a reference ID, and sometimes screen coordinates. Without this file, a user query like “submit button” would be harder to turn into the exact element reference needed for clicking or inspection.

The file works in two ways. First, it can do a simple local search: it breaks the query into words, scans each parsed tree line, and returns entries whose line contains all those words. Second, it can clean up a reply from a language model. The model is instructed to answer with element references, but this file does not blindly trust that answer. It rebuilds the list of valid references from the original tree and keeps only references that really exist there. This is like checking a shopping assistant’s suggested aisle numbers against the actual store map before sending someone there.

The output is small and practical: reference, role, name, coordinates, and an optional reason. Results are capped, and a “more matches exist” note can be shown when the search should be narrowed.

#### Function details

##### `tree_entries`  (lines 31–50)

```
def tree_entries(tree: str) -> list[JsonDict]
```

**Purpose**: Turns the plain-text accessibility tree into a list of structured element records. Each record contains the element reference, its role, its visible name, its coordinates if present, and the original line in lowercase for searching.

**Data flow**: It receives the whole tree as one string. It reads it line by line, keeps only lines that look like tree elements and include a reference ID, extracts the useful pieces, fills in missing coordinates as "0,0", and returns a list of simple dictionaries.

**Call relations**: This is the shared parser used before searching or validating results. parse_tree_matches calls it to get searchable entries, and resolve_find_reply calls it to build the list of real references that a reply is allowed to use.

*Call graph*: called by 2 (parse_tree_matches, resolve_find_reply).


##### `_match_payload`  (lines 53–60)

```
def _match_payload(entry: JsonDict, reason: str) -> JsonDict
```

**Purpose**: Builds the standard result shape for one matched element. It keeps only the fields that callers need to show or use a match: reference, role, name, coordinates, and the reason it matched.

**Data flow**: It receives one parsed tree entry and a reason string. It copies the element’s main details into a new dictionary, adds the reason, and returns that new match object without changing the original entry.

**Call relations**: This helper keeps match output consistent. parse_tree_matches uses it for direct text-search matches, and resolve_find_reply uses it after it has confirmed that a suggested reference really exists in the tree.

*Call graph*: called by 2 (parse_tree_matches, resolve_find_reply).


##### `parse_tree_matches`  (lines 63–71)

```
def parse_tree_matches(tree: str, query: str) -> list[JsonDict]
```

**Purpose**: Performs a simple built-in search over the accessibility tree using the user’s query words. It is useful when matching can be done directly from the text without relying on a language-model reply.

**Data flow**: It receives the tree text and a query string. It lowercases the query, pulls out word-like terms longer than one character, parses the tree into entries, and keeps entries whose full tree line contains every query term. It returns up to the maximum allowed number of match dictionaries.

**Call relations**: This function starts by asking tree_entries to turn the tree text into records. For each record that passes the word test, it asks _match_payload to package the result in the common format.

*Call graph*: calls 2 internal fn (_match_payload, tree_entries); 1 external calls (findall).


##### `resolve_find_reply`  (lines 74–102)

```
def resolve_find_reply(reply: str, tree: str) -> tuple[list[JsonDict], bool]
```

**Purpose**: Checks a proposed set of matches against the real accessibility tree. This protects the system from using a made-up, misspelled, repeated, or stale element reference.

**Data flow**: It receives a reply string and the original tree text. It parses the tree into a lookup table of valid references, then reads the reply line by line. It ignores empty lines, stops on NO_MATCHES, notes whether MORE was reported, extracts references from result lines, drops unknown or repeated references, and returns the cleaned match list plus a true-or-false flag saying whether more matches exist.

**Call relations**: This is used after an outside matcher, such as a language model, suggests element references. It calls tree_entries to know what references are real, and it calls _match_payload to rebuild each accepted match from trusted tree data rather than from the reply text.

*Call graph*: calls 2 internal fn (_match_payload, tree_entries).


##### `format_matches`  (lines 105–114)

```
def format_matches(matches: list[JsonDict], has_more: bool=False) -> str
```

**Purpose**: Turns match dictionaries into a human-readable text summary. This is what makes search results easy to display back to a user or another part of the system.

**Data flow**: It receives a list of matches and an optional flag saying whether more matches exist. For each match, it creates a line with the reference, role, name, coordinates, and optional reason. If the more flag is set, it adds a final note asking the user to refine the query, then returns all lines joined into one string.

**Call relations**: This function sits at the end of the find flow. After parse_tree_matches or resolve_find_reply has produced structured matches, format_matches can present those matches in a compact readable form.


### `extensions/browser/ufo_ext_browser/bua/page.py`

`domain_logic` · `request handling`

A browser page is complicated: it has visible elements, hidden elements, nested frames, screen coordinates, and browser-only IDs. This file builds a cleaner “map” of that page. Without it, the model would have to guess what is on the screen or invent element references, which would make clicking, typing, and reading unreliable.

The file asks Chrome, through the Chrome DevTools Protocol (CDP, a browser control API), for two views of the page. One view is a DOM snapshot, which gives element geometry such as boxes and positions. The other is an accessibility tree, which describes what a user-facing element is: button, link, checkbox, heading, image, and so on. The code joins these two views using Chrome’s backend node IDs, like matching a shop directory to a floor plan.

Frames are an important part of the work. Normal iframes are stitched into the parent page. Out-of-process iframes, which Chrome runs separately for security, are attached to and captured separately, then spliced back into the same tree.

The result can be rendered as a Playwright-like page tree with refs such as `e12` or `f1e3`, coordinates, and state details, or as markdown for easier reading. Those refs are later resolved back to real browser nodes when the system needs to click or inspect a target.

#### Function details

##### `split_ref`  (lines 116–120)

```
def split_ref(ref: str) -> tuple[str, int] | None
```

**Purpose**: Checks and splits a browser element reference such as `e12` or `f1e3`. The prefix identifies the frame, and the number identifies the browser node inside that frame.

**Data flow**: It receives a text ref. If the text matches the expected shape, it returns the frame prefix and numeric backend node ID; if not, it returns nothing.

**Call relations**: The page renderer uses this when asked to render only one referenced subtree. BrowserPage.resolve_ref also uses it before turning a model-provided ref back into a real browser target.

*Call graph*: called by 2 (resolve_ref, render).


##### `Cdp.send`  (lines 124–126)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: Defines the expected shape of a CDP send method. CDP is the browser control channel used to ask Chrome for snapshots, node boxes, frame details, and similar data.

**Data flow**: It accepts a browser command name, optional command parameters, and an optional session ID for a particular frame or target. It returns a JSON-like response from the browser.

**Call relations**: This file depends on that contract in fetch_target and other browser-page operations. Settle._flush_page_tasks elsewhere also calls the same kind of CDP connection.

*Call graph*: called by 2 (fetch_target, _flush_page_tasks).


##### `PageTab.frame_seq`  (lines 161–161)

```
def frame_seq(self, frame_id: str) -> int
```

**Purpose**: Provides a stable short number for a frame ID so refs can use compact prefixes like `f1`. This keeps nested-frame references readable for the model.

**Data flow**: It receives a browser frame ID and returns that frame’s sequence number for the current tab.

**Call relations**: BrowserPage._snapshot_oop calls it when it discovers a separate iframe and needs to assign that frame a reference prefix.

*Call graph*: called by 1 (_snapshot_oop).


##### `BrowserPageSession.connection`  (lines 168–168)

```
def connection(self) -> Cdp
```

**Purpose**: Defines how BrowserPage gets the active CDP connection. This lets the page logic ask the browser for snapshots and node locations without owning the connection itself.

**Data flow**: It reads the browser session object and returns the CDP connection object used for browser commands.

**Call relations**: BrowserPage methods call on this connection whenever they need fresh page data, resolve a ref to coordinates, or attach to an out-of-process frame.


##### `BrowserPageSession.init_session`  (lines 170–170)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Defines the setup step for a newly attached browser target session. This is needed when the code connects to an out-of-process iframe.

**Data flow**: It receives a CDP session ID and prepares that session for later browser commands. It does not produce page text directly, but it changes the browser session state.

**Call relations**: BrowserPage._oop_session calls it after attaching to a frame target, before that frame is captured like the main page.


##### `_float`  (lines 190–195)

```
def _float(value: Json | None, default: float=0.0) -> float
```

**Purpose**: Safely turns a JSON value into a floating-point number, with a fallback when the value is missing or not numeric.

**Data flow**: It receives a JSON value and a default number. Numeric values become floats; anything else becomes the default.

**Call relations**: _parse_document uses it for scroll offsets and layout bounds. fetch_target uses it to read the browser’s device pixel ratio.

*Call graph*: called by 2 (_parse_document, fetch_target).


##### `_attr`  (lines 198–203)

```
def _attr(strings: list[str], attrs: list[Json], name: str) -> str | None
```

**Purpose**: Finds one HTML attribute, such as `type` or `src`, inside Chrome’s compact snapshot format.

**Data flow**: It receives the shared string table, a packed list of attribute indexes, and the desired attribute name. It walks pairs of key/value indexes and returns the matching value text, if present.

**Call relations**: _parse_document uses it while extracting useful details from inputs, images, and iframe elements.

*Call graph*: called by 1 (_parse_document).


##### `_parse_document`  (lines 206–266)

```
def _parse_document(doc: JsonDict, strings: list[str], dpr: float) -> _RawDoc
```

**Purpose**: Converts one raw DOM snapshot document from Chrome into the local shape this file can use. It extracts node IDs, element boxes, pointer cursor hints, input types, image sources, and iframe links.

**Data flow**: It receives one document record, Chrome’s shared string table, and the device pixel ratio. It validates and decodes the packed arrays, normalizes coordinates to CSS pixels, and returns a _RawDoc with geometry and frame relationship clues.

**Call relations**: parse_snapshot calls this for each document returned by Chrome before combining those documents into full page data.

*Call graph*: calls 2 internal fn (_attr, _float); called by 1 (parse_snapshot); 6 external calls (__init__, __init__, get, as_int, as_list, as_map).


##### `parse_snapshot`  (lines 269–318)

```
def parse_snapshot(snapshot: JsonDict, dpr: float, base_origin: tuple[float, float]=(0.0, 0.0)) -> list[DocData]
```

**Purpose**: Turns Chrome’s full DOM snapshot into per-frame page data with correct on-screen positions. It also identifies which iframes are normal child documents and which must be captured separately.

**Data flow**: It receives the raw snapshot, the device pixel ratio, and a base origin for the frame. It parses each document, accumulates child-frame offsets through iframe boxes, adjusts element bounds into page coordinates, and returns DocData records.

**Call relations**: fetch_target calls it right after asking Chrome for a DOM snapshot. Its output is then joined with accessibility-tree data.

*Call graph*: calls 1 internal fn (_parse_document); called by 1 (fetch_target); 5 external calls (__init__, get, as_list, as_map, as_str).


##### `fetch_target`  (lines 321–383)

```
async def fetch_target(cdp: Cdp, session_id: str, *, root_prefix: str, prefix_for: Callable[[str], str], base_origin: tuple[float, float]=(0.0, 0.0)) -> FrameSnapshot
```

**Purpose**: Captures one browser target, such as the main page or one separately running iframe. It combines layout information with the accessibility tree into FrameSnapshot objects.

**Data flow**: It receives a CDP connection, a session ID, frame-prefix helpers, and an origin. It enables needed browser domains, captures DOM geometry, reads device pixel ratio, parses the snapshot, fetches accessibility trees for each frame, and returns the root FrameSnapshot with child frames linked in.

**Call relations**: BrowserPage._snapshot_target calls it as the main capture step. It relies on Cdp.send for browser communication and parse_snapshot for decoding DOM geometry.

*Call graph*: calls 3 internal fn (send, _float, parse_snapshot); called by 1 (_snapshot_target); 5 external calls (__init__, gather, as_list, as_map, as_str).


##### `_ax_value`  (lines 386–390)

```
def _ax_value(value: Json | None) -> str
```

**Purpose**: Extracts the plain text value from an accessibility-tree field. Chrome wraps many accessibility values in small objects, and this hides that detail from the renderers.

**Data flow**: It receives a JSON value. If it is an object with a `value`, that value is converted to text; otherwise it returns an empty string.

**Call relations**: Both _PageRenderer._render_node and _MarkdownRenderer._walk use it when reading roles and names from accessibility nodes.

*Call graph*: called by 2 (_walk, _render_node); 1 external calls (get).


##### `_ax_property`  (lines 393–401)

```
def _ax_property(node: JsonDict, name: str) -> Json
```

**Purpose**: Looks up one named accessibility property on a node, such as `checked`, `expanded`, `url`, or `hidden`.

**Data flow**: It receives an accessibility node and a property name. It scans the node’s properties and returns the stored value if present, otherwise nothing.

**Call relations**: The page renderer uses it to decide visibility and state text. The markdown renderer uses it for headings and links. _format_extras uses it to build readable suffixes.

*Call graph*: called by 4 (_render_content, _walk, _render_node, _format_extras); 3 external calls (get, as_list, as_map).


##### `_should_skip`  (lines 404–410)

```
def _should_skip(node: JsonDict, role: str, name: str) -> bool
```

**Purpose**: Decides whether a structural accessibility node should be hidden from the action-oriented page tree. Empty containers like generic groups often add noise without helping the model.

**Data flow**: It receives a node, its role, and its name. If the node is a skippable container with no name and no important state, it returns true; otherwise false.

**Call relations**: _PageRenderer._render_node calls it while deciding whether to print a node or pass through to its children.

*Call graph*: called by 1 (_render_node); 3 external calls (get, as_list, as_map).


##### `_truncate`  (lines 413–416)

```
def _truncate(text: str, max_len: int) -> str
```

**Purpose**: Shortens long labels or values so the rendered page stays compact. This prevents huge names, URLs, or input values from flooding the model context.

**Data flow**: It receives text and a maximum length. Text within the limit is returned unchanged; longer text is cut and ends with an ellipsis.

**Call relations**: _PageRenderer._render_node, _format_extras, and _image_name use it before adding text to the page output.

*Call graph*: called by 3 (_render_node, _format_extras, _image_name).


##### `_image_name`  (lines 419–426)

```
def _image_name(src: str | None) -> str
```

**Purpose**: Creates a fallback image label from an image URL when the accessibility tree has no useful name. It uses the filename part of the URL if it looks like a real file.

**Data flow**: It receives an image source URL or nothing. It extracts the URL path, takes the final filename, truncates it if needed, and returns that as a name only when it has a file extension.

**Call relations**: The page renderer uses it for unnamed images. The markdown renderer uses it for image alt-like text.

*Call graph*: calls 1 internal fn (_truncate); called by 2 (_render_content, _render_node); 1 external calls (urlparse).


##### `_format_extras`  (lines 429–459)

```
def _format_extras(node: JsonDict, geom: NodeGeom | None) -> str
```

**Purpose**: Builds the extra state text shown after a rendered page-tree line. This includes useful details like input type, value, checked state, disabled state, URL, placeholder, and similar properties.

**Data flow**: It receives an accessibility node and optional geometry details. It reads selected accessibility properties, filters unsafe or noisy URL forms, truncates long values, and returns a formatted suffix string.

**Call relations**: _PageRenderer._render_node calls it just before appending a node line to the action-oriented page tree.

*Call graph*: calls 2 internal fn (_ax_property, _truncate); called by 1 (_render_node).


##### `_frame_by_prefix`  (lines 462–469)

```
def _frame_by_prefix(root: FrameSnapshot, prefix: str) -> FrameSnapshot | None
```

**Purpose**: Finds the captured frame that owns a ref prefix. For example, an empty prefix means the main frame, while `f1` points to a child frame.

**Data flow**: It receives the root frame snapshot and a prefix. It searches the frame tree recursively and returns the matching FrameSnapshot, if one exists.

**Call relations**: _PageRenderer.render uses it when rendering from a specific ref instead of from the whole page root.

*Call graph*: called by 1 (render).


##### `_node_by_backend`  (lines 472–476)

```
def _node_by_backend(frame: FrameSnapshot, backend_id: int) -> str | None
```

**Purpose**: Finds the accessibility node that corresponds to a Chrome backend DOM node ID. This bridges from a stable DOM ref back to the accessibility tree.

**Data flow**: It receives a frame snapshot and a backend node ID. It scans accessibility nodes until it finds one with that backend ID and returns its accessibility node ID.

**Call relations**: _PageRenderer.render uses it after split_ref and _frame_by_prefix locate the frame for a requested ref.

*Call graph*: called by 1 (render).


##### `_PageRenderer.render`  (lines 489–504)

```
def render(self, root: FrameSnapshot, ref: str | None) -> str | None
```

**Purpose**: Starts rendering an action-oriented page tree. It can render either the full page or only the subtree under a supplied ref.

**Data flow**: It receives the root FrameSnapshot and an optional ref. If a ref is given, it parses the ref, finds the frame and node, and renders from there; otherwise it renders from the root accessibility node. It returns newline-separated tree text, or nothing if the ref cannot be found.

**Call relations**: render_page creates a _PageRenderer and calls this. It hands actual traversal to _PageRenderer._render_node.

*Call graph*: calls 4 internal fn (_render_node, _frame_by_prefix, _node_by_backend, split_ref).


##### `_PageRenderer._coord_str`  (lines 506–510)

```
def _coord_str(self, geom: NodeGeom | None) -> str
```

**Purpose**: Formats an element’s center point for the page-tree output. Coordinates are scaled into the model’s working screen size.

**Data flow**: It receives optional geometry. If bounds exist, it computes the rectangle center, applies x/y scaling, and returns text like `(x=...,y=...)`; otherwise it returns an empty string.

**Call relations**: _PageRenderer._render_node calls it when building each visible line.

*Call graph*: called by 1 (_render_node).


##### `_PageRenderer._splice`  (lines 512–515)

```
def _splice(self, frame: FrameSnapshot, backend_id: int | None, depth: int) -> None
```

**Purpose**: Inserts a child frame’s accessibility tree at the iframe node where it belongs. This makes nested pages appear in the right place instead of as separate disconnected trees.

**Data flow**: It receives the current frame, an optional iframe backend ID, and a render depth. If that backend ID has a captured child frame, it renders the child frame’s root at the same point.

**Call relations**: _PageRenderer._descend calls it after rendering normal children, so iframe contents are stitched into the output.

*Call graph*: calls 1 internal fn (_render_node); called by 1 (_descend).


##### `_PageRenderer._render_node`  (lines 517–567)

```
def _render_node(self, frame: FrameSnapshot, ax_id: str, depth: int, parent_name: str) -> None
```

**Purpose**: Renders one accessibility node and then walks onward through its children. This is the main rulebook for what appears in the page tree and what gets skipped.

**Data flow**: It receives a frame, an accessibility node ID, depth, and parent name. It avoids loops, ignores hidden nodes, reads role/name/state/geometry, filters noisy containers or offscreen items when requested, writes a line with ref and coordinates when appropriate, then descends into children.

**Call relations**: _PageRenderer.render starts with this function. _PageRenderer._descend and _PageRenderer._splice call it repeatedly to cover the whole frame tree.

*Call graph*: calls 8 internal fn (_coord_str, _descend, _ax_property, _ax_value, _format_extras, _image_name, _should_skip, _truncate); called by 3 (_descend, _splice, render); 2 external calls (as_list, as_str).


##### `_PageRenderer._descend`  (lines 569–579)

```
def _descend(self, frame: FrameSnapshot, backend_id: int | None, child_ids: list[str], depth: int, parent_name: str) -> None
```

**Purpose**: Continues rendering through a node’s children and then any child frame attached to that node. It keeps traversal order close to the browser’s accessibility order.

**Data flow**: It receives the current frame, possible backend ID, child accessibility IDs, depth, and parent name. It renders each child node, then tries to splice in an iframe child at that location.

**Call relations**: _PageRenderer._render_node calls it whenever traversal should continue below the current node.

*Call graph*: calls 2 internal fn (_render_node, _splice); called by 1 (_render_node).


##### `render_page`  (lines 582–599)

```
def render_page(root: FrameSnapshot, *, viewport: Size, model_size: Size | None=None, filter_type: str='all', max_depth: int=DEFAULT_MAX_DEPTH, ref: str | None=None) -> str | None
```

**Purpose**: Public helper that renders a FrameSnapshot as the action-oriented page tree. This is the text view used for finding elements, reading refs, and choosing click targets.

**Data flow**: It receives the root snapshot, viewport size, optional model size, filter choice, depth limit, and optional ref. It computes coordinate scaling, creates a _PageRenderer, and returns that renderer’s text.

**Call relations**: BrowserPage.tree calls it after taking a fresh snapshot.

*Call graph*: called by 1 (tree); 2 external calls (__init__, effective_model_size).


##### `_MarkdownRenderer.render`  (lines 613–617)

```
def render(self, root: FrameSnapshot) -> str
```

**Purpose**: Starts rendering the captured page as markdown. This view is meant for reading content rather than selecting controls.

**Data flow**: It receives a root FrameSnapshot. If a root accessibility node exists, it walks the tree, flushes any remaining inline text, and returns blocks joined by blank lines.

**Call relations**: render_markdown creates a _MarkdownRenderer and calls this entry point.

*Call graph*: calls 2 internal fn (_flush, _walk).


##### `_MarkdownRenderer._emit`  (lines 619–622)

```
def _emit(self, text: str) -> None
```

**Purpose**: Adds one finished markdown block, while avoiding empty text and immediate duplicates.

**Data flow**: It receives text, trims whitespace, checks that it is non-empty and not the same as the previous block, then appends it to the block list.

**Call relations**: _MarkdownRenderer._flush and _MarkdownRenderer._render_content use it whenever inline or structured content becomes a complete block.

*Call graph*: called by 2 (_flush, _render_content).


##### `_MarkdownRenderer._flush`  (lines 624–627)

```
def _flush(self) -> None
```

**Purpose**: Turns accumulated inline text into a markdown paragraph. It is used when the walker reaches a block boundary such as a paragraph, article, or frame boundary.

**Data flow**: It checks the current inline text list. If there is text waiting, it joins it with spaces, emits it as a block, and clears the inline list.

**Call relations**: _MarkdownRenderer.render, _MarkdownRenderer._walk, and _MarkdownRenderer._render_content call it to keep paragraphs separated cleanly.

*Call graph*: calls 1 internal fn (_emit); called by 3 (_render_content, _walk, render).


##### `_MarkdownRenderer._walk`  (lines 629–649)

```
def _walk(self, frame: FrameSnapshot, ax_id: str, parent_name: str) -> None
```

**Purpose**: Walks the accessibility tree and builds a reading-order markdown document. It preserves useful structure such as headings, links, images, list items, and frame contents.

**Data flow**: It receives a frame, accessibility node ID, and parent name. It skips already-seen or hidden nodes, reads role/name/backend ID, renders the current content, walks children, flushes at block boundaries, and then walks any child frame attached to the node.

**Call relations**: _MarkdownRenderer.render starts the walk. The walk delegates formatting decisions to _MarkdownRenderer._render_content.

*Call graph*: calls 4 internal fn (_flush, _render_content, _ax_property, _ax_value); called by 1 (render); 2 external calls (as_list, as_str).


##### `_MarkdownRenderer._render_content`  (lines 651–681)

```
def _render_content(self, frame: FrameSnapshot, backend_id: int | None, role: str, name: str, node: JsonDict) -> None
```

**Purpose**: Decides how one accessibility node should appear in markdown. A heading becomes `# Heading`, a link can become `[text](url)`, a list item becomes a bullet, and images become image markers.

**Data flow**: It receives frame context, backend ID, role, name, and the node. It checks the role, reads needed properties such as heading level or URL, flushes paragraphs when needed, and appends or emits the right markdown text.

**Call relations**: _MarkdownRenderer._walk calls it for each visible node before walking that node’s children.

*Call graph*: calls 4 internal fn (_emit, _flush, _ax_property, _image_name); called by 1 (_walk).


##### `render_markdown`  (lines 684–689)

```
def render_markdown(root: FrameSnapshot) -> str
```

**Purpose**: Public helper that renders a FrameSnapshot into a reading-focused markdown view. It gives the model page content with structure instead of a flat text dump.

**Data flow**: It receives the root frame snapshot, creates a _MarkdownRenderer, and returns its markdown output.

**Call relations**: BrowserPage.markdown calls it after taking a fresh snapshot.

*Call graph*: called by 1 (markdown); 1 external calls (__init__).


##### `BrowserPage.tree`  (lines 698–706)

```
async def tree(self, tab: PageTab, filter_type: str, ref: str | None) -> str | None
```

**Purpose**: Returns the current page as an action-oriented tree. This is the high-level method used when the system needs refs, coordinates, roles, and states.

**Data flow**: It receives a tab, a filter type, and an optional ref. It captures a fresh snapshot, renders that snapshot with render_page, and returns the resulting text or nothing if the requested ref cannot be rendered.

**Call relations**: It combines BrowserPage.snapshot with render_page, so callers do not need to know the capture and rendering steps separately.

*Call graph*: calls 2 internal fn (snapshot, render_page).


##### `BrowserPage.markdown`  (lines 708–709)

```
async def markdown(self, tab: PageTab) -> str
```

**Purpose**: Returns the current page as markdown for reading. This is useful when the system wants page content more than click targets.

**Data flow**: It receives a tab, captures a fresh snapshot, renders it with render_markdown, and returns the markdown string.

**Call relations**: It combines BrowserPage.snapshot with render_markdown.

*Call graph*: calls 2 internal fn (snapshot, render_markdown).


##### `BrowserPage.resolve_ref`  (lines 711–724)

```
def resolve_ref(self, tab: PageTab, ref: str) -> tuple[FrameNode, int]
```

**Purpose**: Turns a model-visible ref back into the frame and backend node ID it points to. It also rejects invented or stale refs with a clear error message.

**Data flow**: It receives a tab and ref text. It parses the ref, looks up the frame prefix in the tab’s registered frames, and returns the FrameNode plus backend ID; invalid or unknown refs raise HallucinationError.

**Call relations**: BrowserPage.ref_point calls it before asking the browser for the referenced element’s location.

*Call graph*: calls 1 internal fn (split_ref); called by 1 (ref_point); 1 external calls (__init__).


##### `BrowserPage.ref_point`  (lines 726–752)

```
async def ref_point(self, tab: PageTab, ref: str) -> tuple[int, int]
```

**Purpose**: Finds a clickable point for a previously rendered ref. It scrolls the element into view and computes the center of its content box.

**Data flow**: It receives a tab and ref. It resolves the ref, sends CDP commands to scroll and read content quads or box model data, converts coordinate values to floats, adds the frame origin, and returns integer x/y coordinates. If the browser cannot resolve the node, it raises HallucinationError.

**Call relations**: It depends on BrowserPage.resolve_ref and _coord_float_or_default. It is the bridge from text refs in the rendered page to actual screen coordinates for browser actions.

*Call graph*: calls 2 internal fn (resolve_ref, _coord_float_or_default); 3 external calls (__init__, as_list, as_map).


##### `BrowserPage.snapshot`  (lines 754–758)

```
async def snapshot(self, tab: PageTab) -> FrameSnapshot
```

**Purpose**: Captures the whole page, including frames, into a FrameSnapshot tree. It also refreshes the tab’s ref-to-frame registry for later actions.

**Data flow**: It receives a tab. It snapshots the main target, clears old ref frame mappings, registers every captured frame, and returns the root snapshot.

**Call relations**: BrowserPage.tree and BrowserPage.markdown call it before rendering. It delegates the capture work to BrowserPage._snapshot_target.

*Call graph*: calls 2 internal fn (_register_frames, _snapshot_target); called by 2 (markdown, tree).


##### `BrowserPage._snapshot_target`  (lines 760–777)

```
async def _snapshot_target(self, tab: PageTab, session_id: str, root_prefix: str, origin: tuple[float, float], depth: int) -> FrameSnapshot
```

**Purpose**: Captures one CDP target and, if allowed by the frame-depth limit, attaches any separate iframe targets under it.

**Data flow**: It receives tab context, a session ID, a root prefix, an origin, and current depth. It calls fetch_target for that session, then optionally asks _attach_oop_frames to add out-of-process iframe snapshots, and returns the completed root frame.

**Call relations**: BrowserPage.snapshot uses it for the main page. BrowserPage._snapshot_oop uses it recursively for out-of-process iframe sessions.

*Call graph*: calls 2 internal fn (_attach_oop_frames, fetch_target); called by 2 (_snapshot_oop, snapshot).


##### `BrowserPage._attach_oop_frames`  (lines 779–787)

```
async def _attach_oop_frames(self, tab: PageTab, root: FrameSnapshot, depth: int) -> None
```

**Purpose**: Finds iframe placeholders that Chrome did not include as normal child documents and tries to capture them separately. These are out-of-process iframes, meaning Chrome runs them in separate targets.

**Data flow**: It receives a tab, a root frame snapshot, and depth. It walks the already captured frame tree, tries to snapshot each listed out-of-process iframe, and attaches successful child snapshots to the right backend node.

**Call relations**: BrowserPage._snapshot_target calls it after fetch_target. It delegates each individual iframe to BrowserPage._snapshot_oop.

*Call graph*: calls 1 internal fn (_snapshot_oop); called by 1 (_snapshot_target).


##### `BrowserPage._snapshot_oop`  (lines 789–815)

```
async def _snapshot_oop(self, tab: PageTab, frame: FrameSnapshot, backend_id: int, depth: int) -> FrameSnapshot | None
```

**Purpose**: Captures one out-of-process iframe and returns it as a child FrameSnapshot if possible. Failures are logged and skipped so one bad frame does not break the whole page capture.

**Data flow**: It receives tab context, the parent frame, an iframe backend ID, and depth. It asks Chrome which frame ID belongs to that DOM node, gets or creates a CDP session for that frame, computes the child origin from the iframe bounds, and snapshots the child target.

**Call relations**: BrowserPage._attach_oop_frames calls it for each out-of-process iframe. It uses BrowserPage._oop_session to attach to the frame and BrowserPage._snapshot_target to capture it.

*Call graph*: calls 3 internal fn (_oop_session, _snapshot_target, frame_seq); called by 1 (_attach_oop_frames); 1 external calls (as_map).


##### `BrowserPage._register_frames`  (lines 817–820)

```
def _register_frames(self, tab: PageTab, frame: FrameSnapshot) -> None
```

**Purpose**: Records which frame each ref prefix belongs to. This registry is what later lets `f1e3` be resolved back to a browser session and screen origin.

**Data flow**: It receives a tab and a frame snapshot. It stores a FrameNode for the frame’s prefix, then recursively registers all child frames.

**Call relations**: BrowserPage.snapshot calls it after capture so BrowserPage.resolve_ref can validate and resolve refs from the rendered output.

*Call graph*: called by 1 (snapshot); 1 external calls (__init__).


##### `BrowserPage._oop_session`  (lines 822–835)

```
async def _oop_session(self, frame_id: str) -> str | None
```

**Purpose**: Gets a CDP session for an out-of-process iframe, reusing a cached one when available. This avoids repeatedly attaching to the same frame target.

**Data flow**: It receives a frame ID. It first checks the browser’s session cache; if missing, it asks Chrome to attach to that target, initializes the new session, caches it, and returns the session ID. If attaching fails, it returns nothing.

**Call relations**: BrowserPage._snapshot_oop calls it before capturing a separate iframe target.

*Call graph*: called by 1 (_snapshot_oop); 1 external calls (as_str).


##### `_coord_float_or_default`  (lines 838–847)

```
def _coord_float_or_default(value: Json | None, default: float) -> float
```

**Purpose**: Safely converts coordinate values returned by Chrome into floats. Chrome may return numbers or numeric strings, so this accepts both.

**Data flow**: It receives a JSON value and a default. Numbers become floats, non-empty strings are parsed as floats, missing values become the default, and other types raise a validation error.

**Call relations**: BrowserPage.ref_point uses it when averaging the four corners of an element’s content quad.

*Call graph*: called by 1 (ref_point); 1 external calls (__init__).
