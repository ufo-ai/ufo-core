# Page Content Extraction and Element Lookup  `stage-12.2.4`

This stage is the browser “reading and pointing” layer used during the main automation loop. Before the model can choose an action, the system must turn a live web page into clear text. After the model chooses something, the system must connect that text reference back to a real page element.

page.py is the main translator. It takes the current browser page and produces a clean, structured view the model can understand, then maps the model’s chosen element back to screen positions for actions like clicking or typing. content.py provides smaller tools for reading page content, extracting text, and searching elements, while keeping the results compact and safe for other code to use. find.py works with the accessibility tree, which is a browser-provided outline of visible controls and text. It parses tree lines, matches search requests, checks whether model answers really exist, and formats matches. errors.py defines the special error used when the model points to an element that is not actually on the page.

## Files in this stage

### Page content views
These files convert live browser pages and raw browser structures into concise, model-readable page text and reusable content results.

### `extensions/browser/ufo_ext_browser/bua/page.py`

`domain_logic` · `request handling`

A web page is messy: it has HTML nodes, accessibility nodes, frames inside frames, off-screen items, hidden text, and browser-only IDs. This file builds a simpler map of the page, more like a labeled floor plan. It asks Chrome for two views of the page: a DOM snapshot, which gives element positions and raw document structure, and an accessibility tree, which gives human-facing roles and names such as button, link, or heading. It joins those views together so each useful item can be shown with a stable reference and a center coordinate.

Frames are the tricky part. Normal iframes can be stitched into the parent page, but out-of-process iframes live in separate browser targets. This file detects those, attaches to them when allowed, snapshots them too, and splices them into the final tree. The result is a single readable page description even when the browser has split the page internally.

The file can render two outputs. `render_page` produces an action-oriented tree with roles, names, refs, coordinates, and states such as checked or disabled. `render_markdown` produces a calmer reading view with headings, links, images, and paragraphs. Without this file, the model would either see raw browser data that is too noisy, or it would not be able to reliably connect a text description like `[ref=e12]` back to an actual element on the screen.

#### Function details

##### `split_ref`  (lines 116–120)

```
def split_ref(ref: str) -> tuple[str, int] | None
```

**Purpose**: Breaks a browser element reference, such as `e12` or `f1e3`, into the frame part and the element ID part. This is how later actions know which frame and which browser node the model meant.

**Data flow**: It receives a reference string → checks that it matches the expected reference shape → returns the frame prefix and numeric backend node ID, or returns nothing if the string is not valid.

**Call relations**: When rendering a subtree, `render_page` uses this to understand a requested starting ref. When an action needs to use a model-provided ref, `BrowserPage.resolve_ref` calls it first so invented or malformed refs are rejected early.

*Call graph*: called by 2 (resolve_ref, render_page).


##### `Cdp.send`  (lines 124–126)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: Defines the expected shape of a Chrome DevTools Protocol sender. Chrome DevTools Protocol, or CDP, is the command channel used to ask the browser for page data and element geometry.

**Data flow**: It accepts a browser command name, optional command data, and an optional session ID → sends that command to Chrome → returns Chrome's JSON-like response.

**Call relations**: This is a protocol definition rather than an implementation. `fetch_target` depends on it to collect snapshots and accessibility trees, and other browser code such as settling page tasks can also send CDP commands through the same shape.

*Call graph*: called by 2 (fetch_target, _flush_page_tasks).


##### `PageTab.frame_seq`  (lines 161–161)

```
def frame_seq(self, frame_id: str) -> int
```

**Purpose**: Provides a short numeric sequence for a frame ID so references can include compact frame prefixes like `f1`. This keeps element refs readable while still distinguishing frames.

**Data flow**: It receives a browser frame ID → looks up or assigns that frame's sequence number → returns the number used in refs.

**Call relations**: `BrowserPage._snapshot_oop` calls this when it snapshots an out-of-process frame, so refs inside that frame get a unique prefix instead of colliding with refs from the main page.

*Call graph*: called by 1 (_snapshot_oop).


##### `BrowserPageSession.connection`  (lines 168–168)

```
def connection(self) -> Cdp
```

**Purpose**: Provides access to the browser command connection used by this page reader. The connection is what lets the file talk to Chrome.

**Data flow**: It reads the current browser session object → returns a CDP connection object that can send browser commands.

**Call relations**: Methods on `BrowserPage` call this whenever they need live browser data, such as taking snapshots, scrolling an element into view, or attaching to a frame.


##### `BrowserPageSession.init_session`  (lines 170–170)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Prepares a newly attached browser session so it can be used like the main page session. This matters for out-of-process iframes, which require their own CDP session.

**Data flow**: It receives a new session ID → performs whatever setup the browser layer needs for that session → leaves the session ready for later snapshot commands.

**Call relations**: `BrowserPage._oop_session` calls this after Chrome attaches to a separate frame target, before caching and reusing that session.


##### `_float`  (lines 190–195)

```
def _float(value: Json | None, default: float=0.0) -> float
```

**Purpose**: Safely turns a JSON value into a floating-point number, with a fallback when the value is missing or not numeric. It keeps snapshot parsing from crashing on harmless odd values.

**Data flow**: It receives a raw value and a default → returns the value as a float if it is an integer or decimal number → otherwise returns the default.

**Call relations**: `_parse_document` uses this while reading scroll positions and layout bounds. `fetch_target` uses it to read the browser's device pixel ratio.

*Call graph*: called by 2 (_parse_document, fetch_target).


##### `_attr`  (lines 198–203)

```
def _attr(strings: list[str], attrs: list[Json], name: str) -> str | None
```

**Purpose**: Finds one named HTML attribute from Chrome's compact snapshot format. It is used for practical details like an input's type or an image's source URL.

**Data flow**: It receives the shared string table, a compact attribute list, and an attribute name → walks key/value pairs in that list → returns the matching attribute text or nothing.

**Call relations**: `_parse_document` calls this when it sees input, image, iframe, or frame elements and wants the small pieces of metadata needed later for rendering.

*Call graph*: called by 1 (_parse_document).


##### `_parse_document`  (lines 206–266)

```
def _parse_document(doc: JsonDict, strings: list[str], dpr: float) -> _RawDoc
```

**Purpose**: Turns one raw DOM snapshot document from Chrome into a usable record of element IDs, screen geometry, iframe links, and selected element metadata. This is the first cleanup step after receiving Chrome's dense snapshot data.

**Data flow**: It receives one document object, Chrome's shared string table, and the device pixel ratio → validates and decodes node lists, bounds, styles, scroll offsets, attributes, and iframe document links → returns a `_RawDoc` with browser node IDs and `NodeGeom` entries.

**Call relations**: `parse_snapshot` calls this for every document in a DOM snapshot. It relies on `_float` for coordinates and `_attr` for element attributes, then hands the normalized raw document data back for frame-origin stitching.

*Call graph*: calls 2 internal fn (_attr, _float); called by 1 (parse_snapshot); 6 external calls (__init__, __init__, get, as_int, as_list, as_map).


##### `parse_snapshot`  (lines 269–318)

```
def parse_snapshot(snapshot: JsonDict, dpr: float, base_origin: tuple[float, float]=(0.0, 0.0)) -> list[DocData]
```

**Purpose**: Combines all documents from a Chrome DOM snapshot into per-frame geometry data with correct page-relative positions. It also identifies which iframes are normal child documents and which likely need separate handling.

**Data flow**: It receives the full snapshot, the device pixel ratio, and a starting origin → parses each document → walks parent-to-child document links to accumulate iframe offsets → adjusts element bounds into shared page coordinates → returns a list of `DocData` records.

**Call relations**: `fetch_target` calls this after capturing a DOM snapshot. The returned `DocData` records are then joined with accessibility trees to build `FrameSnapshot` objects.

*Call graph*: calls 1 internal fn (_parse_document); called by 1 (fetch_target); 5 external calls (__init__, get, as_list, as_map, as_str).


##### `fetch_target`  (lines 321–383)

```
async def fetch_target(cdp: Cdp, session_id: str, *, root_prefix: str, prefix_for: Callable[[str], str], base_origin: tuple[float, float]=(0.0, 0.0)) -> FrameSnapshot
```

**Purpose**: Collects everything needed to describe one browser target, such as the main page or one out-of-process frame. It asks Chrome for both layout information and accessibility information, then joins them by frame.

**Data flow**: It receives a CDP connection, a session ID, frame-ref prefix rules, and an origin → enables the needed browser domains → captures a DOM snapshot and device pixel ratio → parses geometry → fetches full accessibility trees for each document → returns a root `FrameSnapshot` with child frames attached where possible.

**Call relations**: `BrowserPage._snapshot_target` uses this as the main data-gathering step. It calls `parse_snapshot`, `_float`, and CDP commands, then creates the frame snapshots that `render_page` and `render_markdown` later read.

*Call graph*: calls 3 internal fn (send, _float, parse_snapshot); called by 1 (_snapshot_target); 5 external calls (__init__, gather, as_list, as_map, as_str).


##### `_ax_value`  (lines 386–390)

```
def _ax_value(value: Json | None) -> str
```

**Purpose**: Extracts the plain text value from an accessibility-tree field. Accessibility data often wraps values inside small objects, and this hides that detail from renderers.

**Data flow**: It receives a raw accessibility value → if it is an object with a `value`, it converts that value to text → otherwise returns an empty string.

**Call relations**: Both `render_page.render_node` and `render_markdown.walk` use this to read roles and names before deciding what to show.

*Call graph*: called by 2 (walk, render_node); 1 external calls (get).


##### `_ax_property`  (lines 393–401)

```
def _ax_property(node: JsonDict, name: str) -> Json
```

**Purpose**: Looks up one named property on an accessibility node, such as `checked`, `disabled`, `url`, or heading `level`. These properties add meaning that is not always in the role or name.

**Data flow**: It receives an accessibility node and a property name → searches the node's property list → unwraps the property's value if needed → returns the value or nothing.

**Call relations**: `_format_extras`, `render_page.render_node`, and `render_markdown.walk` call this whenever they need state, visibility, heading level, link URL, or similar details.

*Call graph*: called by 3 (_format_extras, walk, render_node); 3 external calls (get, as_list, as_map).


##### `_should_skip`  (lines 404–410)

```
def _should_skip(node: JsonDict, role: str, name: str) -> bool
```

**Purpose**: Decides whether a structural accessibility node should be hidden from the action tree. It removes empty containers while keeping nodes that carry real state.

**Data flow**: It receives an accessibility node, its role, and its name → checks whether the role is one of the mostly structural roles and whether the node has no useful name or state → returns true when the renderer should skip the node but still consider its children.

**Call relations**: `render_page.render_node` uses this while building the page tree, so the final output is not clogged with wrappers like unnamed groups and sections.

*Call graph*: called by 1 (render_node); 3 external calls (get, as_list, as_map).


##### `_truncate`  (lines 413–416)

```
def _truncate(text: str, max_len: int) -> str
```

**Purpose**: Shortens long text so page descriptions stay readable and do not overwhelm the model. It adds an ellipsis when text is cut.

**Data flow**: It receives text and a maximum length → returns the original text if it fits → otherwise returns a shortened version ending in `…`.

**Call relations**: `_image_name`, `_format_extras`, and `render_page.render_node` use this for element names, values, and image filenames.

*Call graph*: called by 3 (_format_extras, _image_name, render_node).


##### `_image_name`  (lines 419–426)

```
def _image_name(src: str | None) -> str
```

**Purpose**: Creates a reasonable fallback name for an image from its source URL when the accessibility tree does not provide one. For example, it can turn a URL path into a filename-like label.

**Data flow**: It receives an optional image source URL → extracts the path and final filename → returns a shortened filename if it looks useful, or an empty string otherwise.

**Call relations**: `render_page.render_node` and `render_markdown.walk` use this when rendering images that have no accessible name.

*Call graph*: calls 1 internal fn (_truncate); called by 2 (walk, render_node); 1 external calls (urlparse).


##### `_format_extras`  (lines 429–459)

```
def _format_extras(node: JsonDict, geom: NodeGeom | None) -> str
```

**Purpose**: Builds the extra state text shown after an element in the action-oriented page tree. This includes useful details such as input type, current value, checked state, disabled state, and safe URLs.

**Data flow**: It receives an accessibility node and optional geometry metadata → reads selected accessibility properties and element metadata → filters or shortens values where needed → returns a formatted suffix string, or an empty string if there is nothing useful to add.

**Call relations**: `render_page.render_node` calls this right before adding a line to the rendered tree, so each visible element includes important state without exposing every raw browser property.

*Call graph*: calls 2 internal fn (_ax_property, _truncate); called by 1 (render_node).


##### `_frame_by_prefix`  (lines 462–469)

```
def _frame_by_prefix(root: FrameSnapshot, prefix: str) -> FrameSnapshot | None
```

**Purpose**: Finds the frame snapshot that matches a reference prefix such as `f2`. This lets the renderer or action code locate the correct frame before looking for an element.

**Data flow**: It receives the root frame snapshot and a prefix → searches the root and its child frames recursively → returns the matching frame snapshot or nothing.

**Call relations**: `render_page` uses this when asked to render only the subtree under a specific ref.

*Call graph*: called by 1 (render_page).


##### `_node_by_backend`  (lines 472–476)

```
def _node_by_backend(frame: FrameSnapshot, backend_id: int) -> str | None
```

**Purpose**: Finds the accessibility node ID that corresponds to a Chrome backend DOM node ID. Backend DOM node IDs are stable browser-side element identifiers within a document.

**Data flow**: It receives a frame snapshot and a backend node ID → scans that frame's accessibility nodes → returns the matching accessibility node ID or nothing.

**Call relations**: `render_page` uses this after `split_ref` and `_frame_by_prefix` so it can start rendering from the element named by a ref.

*Call graph*: called by 1 (render_page).


##### `render_page`  (lines 479–575)

```
def render_page(root: FrameSnapshot, *, viewport: Size, model_size: Size | None=None, filter_type: str='all', max_depth: int=DEFAULT_MAX_DEPTH, ref: str | None=None) -> str | None
```

**Purpose**: Turns a `FrameSnapshot` into the compact action tree shown to the model. The output lists page items as lines with role, name, reference, center coordinate, and important state.

**Data flow**: It receives the root snapshot, viewport size, optional model coordinate size, filtering options, depth limit, and optional starting ref → computes coordinate scaling → walks the accessibility tree and stitched frames → returns the rendered text, or nothing if a requested ref cannot be found.

**Call relations**: `BrowserPage.tree` calls this after taking a snapshot. Inside, it uses helpers such as `split_ref`, `_frame_by_prefix`, and `_node_by_backend` when rendering a subtree, and nested functions do the actual walking and line creation.

*Call graph*: calls 3 internal fn (_frame_by_prefix, _node_by_backend, split_ref); called by 1 (tree); 1 external calls (effective_model_size).


##### `render_page.coord_str`  (lines 494–498)

```
def coord_str(geom: NodeGeom | None) -> str
```

**Purpose**: Formats an element's center point for the rendered action tree. It converts browser page coordinates into the coordinate size expected by the model.

**Data flow**: It receives optional geometry → if bounds exist, computes the rectangle center and applies the x/y scale → returns text like ` (x=123,y=45)`, or an empty string when no bounds are known.

**Call relations**: This helper is used inside `render_page.render_node` when a visible line is added to the page tree.


##### `render_page.splice`  (lines 500–503)

```
def splice(frame: FrameSnapshot, backend_id: int | None, depth: int) -> None
```

**Purpose**: Inserts a child frame's accessibility tree at the iframe element where it belongs. This makes nested documents appear in the output like part of one continuous page.

**Data flow**: It receives a frame, an optional iframe backend ID, and a depth → looks up the child frame attached to that backend ID → if found, starts rendering that child frame from its root node.

**Call relations**: `render_page.render_node.descend` calls this after walking normal children, so iframe contents are stitched into the same rendered tree.


##### `render_page.render_node`  (lines 505–559)

```
def render_node(frame: FrameSnapshot, ax_id: str, depth: int, parent_name: str) -> None
```

**Purpose**: Walks one accessibility node and decides whether and how it should appear in the action tree. It is the main rendering decision-maker for `render_page`.

**Data flow**: It receives a frame, an accessibility node ID, a depth, and the parent name → skips hidden, repeated, too-deep, or unhelpful wrapper nodes → optionally filters off-screen or non-interactive nodes → formats refs, coordinates, names, and extras → appends a line and then walks children.

**Call relations**: This nested function is started by `render_page` at either the page root or a requested ref. It calls helpers such as `_ax_value`, `_ax_property`, `_should_skip`, `_format_extras`, `_image_name`, and `_truncate` while building each line.

*Call graph*: calls 6 internal fn (_ax_property, _ax_value, _format_extras, _image_name, _should_skip, _truncate); 2 external calls (as_list, as_str).


##### `render_page.render_node.descend`  (lines 521–524)

```
def descend(child_depth: int, child_parent_name: str) -> None
```

**Purpose**: Continues rendering through a node's children without duplicating the child-walking logic. It also makes sure iframe contents are included after normal children.

**Data flow**: It receives the depth and parent-name context for children → calls `render_node` for each child accessibility ID → then calls `splice` to render any child frame attached to the current element.

**Call relations**: `render_page.render_node` uses this when a node is transparent and when a visible node has been added, so traversal stays consistent in both cases.


##### `render_markdown`  (lines 583–651)

```
def render_markdown(root: FrameSnapshot) -> str
```

**Purpose**: Turns the page snapshot into a reading-focused Markdown view. Instead of showing action refs and coordinates, it preserves human reading structure such as headings, paragraphs, links, lists, and images.

**Data flow**: It receives the root frame snapshot → walks the accessibility tree and stitched frames → collects text into Markdown blocks → returns one Markdown string separated by blank lines.

**Call relations**: `BrowserPage.markdown` calls this after taking a snapshot. Its nested helpers collect inline text, emit blocks, and walk nodes in a way that respects page structure.

*Call graph*: called by 1 (markdown).


##### `render_markdown.emit`  (lines 592–595)

```
def emit(text: str) -> None
```

**Purpose**: Adds one finished Markdown block to the output, while avoiding empty text and immediate duplicates. It is like placing a completed paragraph onto a page.

**Data flow**: It receives text → trims surrounding whitespace → appends it to the block list only if it is non-empty and not the same as the previous block.

**Call relations**: `render_markdown.flush` and `render_markdown.walk` use this whenever they finish a paragraph, heading, list item, image, or named block.


##### `render_markdown.flush`  (lines 597–600)

```
def flush() -> None
```

**Purpose**: Turns accumulated inline text into a completed Markdown block. This keeps words collected from several inline accessibility nodes together as one paragraph.

**Data flow**: It reads the current inline text buffer → joins the pieces with spaces → sends the result to `emit` → clears the buffer.

**Call relations**: `render_markdown.walk` calls this before starting block-level items, after finishing block-level nodes, and before entering child frames.


##### `render_markdown.walk`  (lines 602–646)

```
def walk(frame: FrameSnapshot, ax_id: str, parent_name: str) -> None
```

**Purpose**: Walks one accessibility node and adds its readable content to the Markdown output. It decides whether a node is a heading, link, list item, image, block, or plain inline text.

**Data flow**: It receives a frame, an accessibility node ID, and the parent name → skips hidden or already visited nodes → reads role, name, backend ID, and useful properties → appends text to inline content or emits Markdown blocks → walks children and splices child frames.

**Call relations**: This nested function is started by `render_markdown` at the root accessibility node. It uses `_ax_value`, `_ax_property`, and `_image_name` to translate browser accessibility data into reading-friendly Markdown.

*Call graph*: calls 3 internal fn (_ax_property, _ax_value, _image_name); 2 external calls (as_list, as_str).


##### `BrowserPage.tree`  (lines 660–668)

```
async def tree(self, tab: PageTab, filter_type: str, ref: str | None) -> str | None
```

**Purpose**: Produces the action-oriented page tree for a tab. This is the public method used when the system wants the model to inspect the page and choose elements by ref.

**Data flow**: It receives a tab, a filter type, and an optional ref → takes a fresh snapshot of the tab → renders that snapshot with viewport and model-size settings → returns the page tree text or nothing if the requested ref is invalid.

**Call relations**: This method ties together `BrowserPage.snapshot` and `render_page`. Higher-level browser tools call it when serving commands like reading or finding page elements.

*Call graph*: calls 2 internal fn (snapshot, render_page).


##### `BrowserPage.markdown`  (lines 670–671)

```
async def markdown(self, tab: PageTab) -> str
```

**Purpose**: Produces a reading-style Markdown version of the current page. This is useful when the model needs content more than clickable controls.

**Data flow**: It receives a tab → takes a fresh snapshot → passes that snapshot to `render_markdown` → returns the Markdown text.

**Call relations**: This is the public wrapper around `BrowserPage.snapshot` and `render_markdown`.

*Call graph*: calls 2 internal fn (snapshot, render_markdown).


##### `BrowserPage.resolve_ref`  (lines 673–686)

```
def resolve_ref(self, tab: PageTab, ref: str) -> tuple[FrameNode, int]
```

**Purpose**: Checks that a model-provided browser ref is valid and maps it to the frame information needed for browser commands. It protects the system from acting on invented references.

**Data flow**: It receives a tab and a ref string → parses the ref → looks up the frame prefix in the tab's registered frame map → returns the frame node and backend element ID, or raises a `HallucinationError` with guidance if the ref cannot be used.

**Call relations**: `BrowserPage.ref_point` calls this before trying to scroll to or measure an element. It depends on refs registered by `BrowserPage.snapshot`.

*Call graph*: calls 1 internal fn (split_ref); called by 1 (ref_point); 1 external calls (__init__).


##### `BrowserPage.ref_point`  (lines 688–714)

```
async def ref_point(self, tab: PageTab, ref: str) -> tuple[int, int]
```

**Purpose**: Finds the screen point at the center of an element reference. This is what turns a text ref from the page tree into coordinates suitable for a click or pointer action.

**Data flow**: It receives a tab and a ref → resolves the ref to a frame and backend node ID → asks Chrome to scroll the element into view → reads its content quadrilateral or box model → averages the four corners and adds frame origin offset → returns integer x and y coordinates.

**Call relations**: This method uses `BrowserPage.resolve_ref` first, then sends CDP commands through the browser connection. If Chrome cannot find the element, it raises `HallucinationError` so callers know to re-read the page.

*Call graph*: calls 2 internal fn (resolve_ref, _coord_float_or_default); 3 external calls (__init__, as_list, as_map).


##### `BrowserPage.snapshot`  (lines 716–720)

```
async def snapshot(self, tab: PageTab) -> FrameSnapshot
```

**Purpose**: Takes a fresh structured snapshot of a tab and rebuilds the tab's map from ref prefixes to frames. This keeps later refs tied to the current page state.

**Data flow**: It receives a tab → snapshots the main target starting at the top-left origin → clears old frame-ref registrations → recursively registers every frame found → returns the root frame snapshot.

**Call relations**: `BrowserPage.tree` and `BrowserPage.markdown` call this before rendering. It delegates the actual browser capture to `BrowserPage._snapshot_target` and frame registration to `BrowserPage._register_frames`.

*Call graph*: calls 2 internal fn (_register_frames, _snapshot_target); called by 2 (markdown, tree).


##### `BrowserPage._snapshot_target`  (lines 722–739)

```
async def _snapshot_target(self, tab: PageTab, session_id: str, root_prefix: str, origin: tuple[float, float], depth: int) -> FrameSnapshot
```

**Purpose**: Snapshots one browser target and, when allowed by depth limits, adds any out-of-process iframe snapshots beneath it. A target is a browser-controlled page or frame that has its own CDP session.

**Data flow**: It receives the tab, session ID, root ref prefix, origin, and current frame depth → calls `fetch_target` to capture the target's normal documents → optionally attaches out-of-process frames → returns the completed root `FrameSnapshot` for that target.

**Call relations**: `BrowserPage.snapshot` uses this for the main tab, and `BrowserPage._snapshot_oop` uses it recursively for separate iframe targets.

*Call graph*: calls 2 internal fn (_attach_oop_frames, fetch_target); called by 2 (_snapshot_oop, snapshot).


##### `BrowserPage._attach_oop_frames`  (lines 741–749)

```
async def _attach_oop_frames(self, tab: PageTab, root: FrameSnapshot, depth: int) -> None
```

**Purpose**: Walks a captured frame tree and fills in out-of-process iframe children. These frames are separate browser targets, so they cannot be captured by the normal in-document snapshot alone.

**Data flow**: It receives a tab, root frame snapshot, and current depth → traverses the frame tree → for each marked out-of-process iframe backend ID, tries to snapshot it → attaches the child snapshot when successful.

**Call relations**: `BrowserPage._snapshot_target` calls this after `fetch_target` if the configured frame-depth limit has not been reached. It delegates each separate iframe to `BrowserPage._snapshot_oop`.

*Call graph*: calls 1 internal fn (_snapshot_oop); called by 1 (_snapshot_target).


##### `BrowserPage._snapshot_oop`  (lines 751–777)

```
async def _snapshot_oop(self, tab: PageTab, frame: FrameSnapshot, backend_id: int, depth: int) -> FrameSnapshot | None
```

**Purpose**: Snapshots one out-of-process iframe if Chrome allows it. It discovers the iframe's frame ID, gets or creates a CDP session for it, and captures it as a child frame tree.

**Data flow**: It receives the parent tab, parent frame snapshot, iframe backend ID, and depth → asks Chrome to describe the iframe node → extracts its frame ID → obtains a session for that frame → computes the child's origin from iframe bounds → calls `_snapshot_target` for the child → returns the child snapshot or nothing if any step fails.

**Call relations**: `BrowserPage._attach_oop_frames` calls this for each out-of-process iframe marker. It uses `BrowserPage._oop_session`, `PageTab.frame_seq`, and then recurses through `BrowserPage._snapshot_target`.

*Call graph*: calls 3 internal fn (_oop_session, _snapshot_target, frame_seq); called by 1 (_attach_oop_frames); 1 external calls (as_map).


##### `BrowserPage._register_frames`  (lines 779–782)

```
def _register_frames(self, tab: PageTab, frame: FrameSnapshot) -> None
```

**Purpose**: Records every frame prefix in the tab so later refs can be resolved. This is the lookup table that connects rendered refs back to CDP sessions and frame origins.

**Data flow**: It receives a tab and a frame snapshot → stores a `FrameNode` under the frame's prefix → repeats the same for every child frame.

**Call relations**: `BrowserPage.snapshot` calls this after taking a fresh snapshot. `BrowserPage.resolve_ref` later relies on the map it builds.

*Call graph*: called by 1 (snapshot); 1 external calls (__init__).


##### `BrowserPage._oop_session`  (lines 784–797)

```
async def _oop_session(self, frame_id: str) -> str | None
```

**Purpose**: Gets a usable CDP session for an out-of-process frame, reusing a cached one when possible. This avoids repeatedly attaching to the same frame target.

**Data flow**: It receives a frame ID → checks the browser session cache → if missing, asks Chrome to attach to that target → initializes the new session → stores it in the cache → returns the session ID, or nothing if attaching fails.

**Call relations**: `BrowserPage._snapshot_oop` calls this before it can snapshot an out-of-process iframe. It uses the browser connection and `BrowserPageSession.init_session` to prepare newly attached sessions.

*Call graph*: called by 1 (_snapshot_oop); 1 external calls (as_str).


##### `_coord_float_or_default`  (lines 800–809)

```
def _coord_float_or_default(value: Json | None, default: float) -> float
```

**Purpose**: Converts coordinate values returned by Chrome into floating-point numbers. Chrome may return numbers or numeric strings, so this helper accepts both.

**Data flow**: It receives a raw coordinate value and a default → returns a float for numeric values or non-empty numeric strings → returns the default for missing values → raises a validation error for unsupported values.

**Call relations**: `BrowserPage.ref_point` uses this when averaging element quadrilateral coordinates from Chrome.

*Call graph*: called by 1 (ref_point); 1 external calls (__init__).


### `extensions/browser/ufo_ext_browser/bua/content.py`

`domain_logic` · `request handling`

This file is a small “content desk” for browser automation. Other parts of the system can ask it questions like: “What is on this tab?”, “Give me the readable text from the page,” or “Find the button or link matching this query.” Without it, callers would need to know how to pick the right browser tab, ask the page reader for the right representation, limit oversized responses, and interpret search results themselves.

The main class is BrowserContent. It does not directly scrape the browser. Instead, it depends on a browser session object that can provide a PageTab, a page reader, and basic tab information. This is like asking a librarian for a book, then asking a reading assistant to summarize or search it.

There are two page views. A “tree” is a structured view of the page, useful for finding clickable or visible elements. “Markdown” is plain readable page text, useful for summaries or extraction. The file also protects the rest of the system from very large pages by cutting page-tree reads to 50,000 characters and full text to 100,000 characters, while reporting whether truncation happened.

For searching, BrowserContent.find can either use a simple built-in text matcher or an optional AI-style completer. In both cases it returns matches plus a human-readable summary.

#### Function details

##### `BrowserContentPageReader.tree`  (lines 23–23)

```
async def tree(self, tab: PageTab, filter_type: str, ref: str | None) -> str | None
```

**Purpose**: This describes the page-reader method that returns a structured view of a browser page. The structure can be filtered, for example to include all elements, only interactive elements, or only visible viewport elements.

**Data flow**: It receives a browser tab, a filter name, and optionally a reference to a specific element. An implementation reads the page structure for that tab and filter, then returns it as text, or returns nothing if the requested referenced element cannot be found.

**Call relations**: BrowserContent.tree relies on an object matching this protocol when it needs the page’s structured content. This file only states the shape of the method; the actual browser-reading work is supplied by another component.


##### `BrowserContentPageReader.markdown`  (lines 25–25)

```
async def markdown(self, tab: PageTab) -> str
```

**Purpose**: This describes the page-reader method that returns the page as readable text in markdown form. Markdown is plain text with simple markers for things like headings and links.

**Data flow**: It receives a browser tab. An implementation reads the page contents and turns them into markdown text, which is returned to the caller.

**Call relations**: BrowserContent.get_page_text uses this method after it has selected the correct tab. The protocol lets BrowserContent stay independent from the exact browser-reading implementation.


##### `BrowserContentSession.page`  (lines 29–29)

```
async def page(self, tab_id: int | None=None) -> PageTab
```

**Purpose**: This describes how BrowserContent asks the browser session for a tab to work with. The caller may request a specific tab, or leave it blank to use the session’s default or current tab.

**Data flow**: It receives an optional tab id. An implementation finds the matching browser tab, or chooses the default tab, and returns a PageTab object representing it.

**Call relations**: BrowserContent.tree and BrowserContent.get_page_text call this before reading page content. This protocol method is the handoff point between content operations and the live browser session.


##### `BrowserContentSession.page_reader`  (lines 31–31)

```
def page_reader(self) -> BrowserContentPageReader
```

**Purpose**: This describes how BrowserContent gets the helper that can read page structure and page text. It keeps the content logic separate from the low-level browser-reading code.

**Data flow**: It receives no extra input beyond the session object. It returns a page reader that supports tree and markdown reads.

**Call relations**: BrowserContent.tree calls this before asking for a structured page tree, and BrowserContent.get_page_text uses the returned reader to get markdown. The session supplies the right reader for its browser environment.


##### `BrowserContentSession.tab_info`  (lines 33–33)

```
async def tab_info(self, tab: Any) -> JsonDict
```

**Purpose**: This describes how BrowserContent asks for basic information about a tab, such as metadata that should travel with extracted page text. It makes text results more useful by including context about where they came from.

**Data flow**: It receives a tab object. An implementation reads tab-level details and returns them as a JSON-style dictionary.

**Call relations**: BrowserContent.get_page_text calls this after reading markdown text. The returned tab information is merged into the final response sent back to the caller.


##### `BrowserContent.tree`  (lines 40–47)

```
async def tree(self, args: JsonDict, filter_type: str='all') -> str
```

**Purpose**: This gets a structured text view of a browser page, optionally focused on a referenced element. It is the shared helper used when the system needs to inspect or search the page structure.

**Data flow**: It receives request arguments and a filter type. It reads the optional tab_id and ref_id from the arguments, converts the tab id into a number if possible, asks the browser session for that tab, and asks the page reader for a tree. It returns the tree text, or a clear “No element found” message if the reference did not match anything.

**Call relations**: BrowserContent.read_page calls this when a caller wants to read the page tree, and BrowserContent.find calls it before searching. It uses _tab_id to normalize the tab id and then hands the selected tab to the session’s page reader.

*Call graph*: calls 1 internal fn (_tab_id); called by 2 (find, read_page); 1 external calls (get).


##### `BrowserContent.read_page`  (lines 49–53)

```
async def read_page(self, args: JsonDict) -> JsonDict
```

**Purpose**: This returns a structured snapshot of the current or requested page, with optional filtering. It is meant for callers that need to understand what elements are present without receiving an unlimited amount of text.

**Data flow**: It receives request arguments. It reads the requested filter, accepts only known filter values, falls back to “all” if the value is unknown, then asks BrowserContent.tree for the page structure. It returns a dictionary containing the tree cut to the maximum allowed length and a flag saying whether anything was cut off.

**Call relations**: This is a public-facing content operation built on BrowserContent.tree. It does not read the browser directly; it prepares safe input, delegates the page-tree work, then shapes the result for the caller.

*Call graph*: calls 1 internal fn (tree); 1 external calls (get).


##### `BrowserContent.get_page_text`  (lines 55–62)

```
async def get_page_text(self, args: JsonDict) -> JsonDict
```

**Purpose**: This returns the readable text of a browser page, along with information about the tab it came from. It is useful when the caller wants the article-like contents of a page rather than a map of buttons and fields.

**Data flow**: It receives request arguments, reads and normalizes an optional tab_id, asks the browser session for that tab, and asks the page reader for markdown text. It returns the text cut to the maximum allowed length, a truncation flag, and the tab information supplied by the browser session.

**Call relations**: This method uses _tab_id to understand the requested tab and then works through the session’s page and page_reader methods. After the markdown is read, it calls tab_info so the final response includes both content and page context.

*Call graph*: calls 1 internal fn (_tab_id); 1 external calls (get).


##### `BrowserContent.find`  (lines 64–77)

```
async def find(self, args: JsonDict, complete: FindCompleter | None=None) -> JsonDict
```

**Purpose**: This searches the page tree for elements matching a user’s query. It can use a simple built-in matcher, or, if provided, a completer that interprets the query more flexibly.

**Data flow**: It receives request arguments and optionally a completer. It extracts the required query string, gets the full page tree, and then searches it. Without a completer, it parses matches directly from the tree. With a completer, it sends a prompt containing the query and a shortened tree, then resolves the completer’s reply back against the real tree. It returns the found matches and a short summary.

**Call relations**: This method builds on BrowserContent.tree because searching needs a structured page view first. It then hands work to the find helpers: parse_tree_matches for direct searching, resolve_find_reply for completer-based searching, and format_matches to produce the final summary.

*Call graph*: calls 1 internal fn (tree); 5 external calls (format_matches, parse_tree_matches, resolve_find_reply, get, as_str).


##### `_tab_id`  (lines 80–89)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: This small helper turns a tab id from JSON-style input into an integer the browser session can use. It also treats missing or unusable values as “no specific tab requested.”

**Data flow**: It receives a value that may be an integer, float, string, or something else. Integers are returned as-is, floats and non-empty strings are converted to integers, and all other values become None.

**Call relations**: BrowserContent.tree and BrowserContent.get_page_text call this before asking the browser session for a page. It keeps tab-id cleanup in one place so those higher-level methods can focus on reading content.

*Call graph*: called by 2 (get_page_text, tree).


### Element lookup validation
These files define invalid element-reference reporting and provide helpers for finding, validating, and formatting elements from accessibility-tree text.

### `extensions/browser/ufo_ext_browser/bua/errors.py`

`data_model` · `request handling`

This file is small, but it names an important failure case. In browser automation, an AI model may be asked to choose or refer to something on a page, such as a button, link, or form field. Sometimes the model may invent a reference to an element that is not actually present. This is often called a “hallucination,” meaning the model confidently produced something unsupported by the real data.

The file defines `HallucinationError`, a custom error type. It inherits from `ValidationError`, which means it is treated as a kind of validation failure: the system checked the model’s answer against what is actually valid and found that it cannot be accepted.

The practical value is clarity. Without this named error, invented browser element references might be reported as generic validation problems, making them harder to diagnose or respond to. With this class, other parts of the system can distinguish “the model gave a value in the wrong shape” from “the model referred to something that cannot exist.” It is like a clerk checking a ticket number and saying not just “invalid,” but “this ticket was never issued.”


### `extensions/browser/ufo_ext_browser/bua/find.py`

`domain_logic` · `request handling`

A browser accessibility tree is a text map of what is on a page: buttons, links, text fields, labels, and so on. This file is the consumer side of that map. Its job is to make element search reliable, especially when another component, such as a language model, is asked to choose matching elements.

The file expects tree lines in a specific shape, such as a role, an optional visible name, a reference like `e12`, and optional screen coordinates. It first parses those lines into small records containing the element reference, role, name, coordinates, and a lowercase copy of the full line for searching.

There are two search paths. The simple path, `parse_tree_matches`, looks for query words directly in the parsed tree lines and returns up to 20 matches. The safer validation path, `resolve_find_reply`, takes a text reply that claims certain references match, then checks every claimed reference against the actual tree. This prevents a guessed or hallucinated reference from being used. In other words, the tree is treated as the source of truth.

Finally, `format_matches` turns the structured matches back into readable lines. Together, these helpers act like a careful librarian: they read the catalog, accept suggested book IDs only if they really exist, and then present a clean list to the user.

#### Function details

##### `tree_entries`  (lines 31–50)

```
def tree_entries(tree: str) -> list[JsonDict]
```

**Purpose**: This function reads the plain-text accessibility tree and extracts the useful facts from each valid element line. It gives the rest of the file a dependable, structured view of the tree instead of making every caller parse raw text itself.

**Data flow**: It takes the full tree text as input. For each line, it looks for an element role, a reference ID, an optional name, and optional x/y coordinates. Lines that do not look like element lines are skipped. The output is a list of records, each containing the element reference, role, name, coordinates, and a lowercase copy of the original line for easy searching.

**Call relations**: This is the shared first step for both search flows. `parse_tree_matches` uses it before doing a direct keyword search, and `resolve_find_reply` uses it to build the trusted list of references that actually exist in the tree.

*Call graph*: called by 2 (parse_tree_matches, resolve_find_reply).


##### `_match_payload`  (lines 53–60)

```
def _match_payload(entry: JsonDict, reason: str) -> JsonDict
```

**Purpose**: This small helper builds the standard match record returned by the search functions. It keeps result shape consistent, so callers always receive the same fields.

**Data flow**: It takes one parsed tree entry and a reason string. It copies the element reference, role, name, and coordinates from the entry, adds the reason, and returns that as a new match record.

**Call relations**: Both `parse_tree_matches` and `resolve_find_reply` call this when they have decided that an element should be included in the results. It is the final packaging step before a match leaves the search logic.

*Call graph*: called by 2 (parse_tree_matches, resolve_find_reply).


##### `parse_tree_matches`  (lines 63–71)

```
def parse_tree_matches(tree: str, query: str) -> list[JsonDict]
```

**Purpose**: This function performs a simple built-in search over the accessibility tree. It is useful when the query can be matched by checking whether all meaningful query words appear in a tree line.

**Data flow**: It takes the tree text and a user query. It lowercases the query, pulls out word-like terms longer than one character, then parses the tree into entries using `tree_entries`. For each entry, it checks whether every query term appears in the lowercase tree line. Matching entries are converted into result records with `_match_payload`, up to the maximum result count. The output is a list of matching element records.

**Call relations**: This is the direct, non-AI search path. It depends on `tree_entries` to understand the tree text and on `_match_payload` to return results in the same format used elsewhere in the file.

*Call graph*: calls 2 internal fn (_match_payload, tree_entries); 1 external calls (findall).


##### `resolve_find_reply`  (lines 74–102)

```
def resolve_find_reply(reply: str, tree: str) -> tuple[list[JsonDict], bool]
```

**Purpose**: This function checks a proposed find result against the real accessibility tree. It is designed to make AI-assisted searching safe by accepting only element references that truly appear in the tree.

**Data flow**: It takes a text reply and the original tree text. First it parses the tree and builds a lookup table from reference ID to real element data. Then it reads the reply line by line, ignoring empty lines, stopping on `NO_MATCHES`, noticing a `MORE` marker, and extracting reference IDs from result lines. If a claimed reference exists in the tree and has not already been used, it creates a match record using the tree’s own role, name, and coordinates, plus the reply’s reason text. It returns two things: the list of verified matches and a true-or-false flag saying whether more matches were claimed.

**Call relations**: This is the trust boundary for language-model search replies. It calls `tree_entries` so it can compare the reply with the actual tree, and it calls `_match_payload` only after a reference has passed that check. Its output can then be shown to a user, commonly after being turned into text by `format_matches`.

*Call graph*: calls 2 internal fn (_match_payload, tree_entries).


##### `format_matches`  (lines 105–114)

```
def format_matches(matches: list[JsonDict], has_more: bool=False) -> str
```

**Purpose**: This function turns structured match records into a human-readable result list. It is the presentation step after matching has already been done.

**Data flow**: It takes a list of match records and an optional flag saying whether more matches exist. For each match, it writes a line with the reference, role, name, and coordinates, and appends the reason if one is present. If the more flag is true, it adds a final note asking the user to refine the query. The output is one formatted string.

**Call relations**: This sits at the end of the find flow. Search functions such as `parse_tree_matches` or `resolve_find_reply` produce structured matches, and `format_matches` converts those records into text suitable for display or response.
