# Hosted sites and app homepage publishing  `stage-14.2`

This stage is shared support for publishing small websites and app homepages. It turns work made inside a safe sandbox into links that other people can open, while keeping ownership, privacy, and cleanup under control.

The site registry in store.py is the record book: it remembers who owns each site, what serves it, and who may view or edit it. tools.py gives builder agents controlled actions to preview, deploy, publish, test, and assign sites as homepages. application_builder.py runs the full build workflow, while application_audit.py checks the finished app against design, behavior, and product requirements. source.py moves saved source files into and out of the sandbox and supplies the standard page-building kit.

The ingress files are the public doorway. ingress_host.py and ingress_url.py create signed, temporary addresses; ingress_serve.py serves stored files or forwards traffic to a live sandbox port; site_report.py reports broken live sites without spamming repairs.

surface.py handles permanent share pages and visibility changes. conversation_slot.py shows conversation-owned sites in the chat view. share_card.py and site_previewer.py create preview images. main_homepage.py removes old seeded homepages when the built-in chat homepage should take over.

## Files in this stage

### Application builders and QA
Agent-facing builders and tools create hosted app pages, validate their product and design evidence, and manage editable source bundles.

### `extensions/sites/ufo_ext_sites/application_builder.py`

`orchestration` · `request handling`

This file is the safety rail and assembly line for generated application pages. A member can ask for an app, but the system does not let an agent freely write and deploy anything it wants. Instead, the agent must first create a visual SVG design, then write one fixed source file, then pass compilation, browser checks, product QA, and deployment ownership checks.

The file sets up a fixed project scaffold under `/workspace/ufo-app`, with `app.tsx` as the only app source. It validates that the SVG design is really an inert drawing, has the right page size, names visible regions, and identifies the UFO kit components it plans to use. Then it validates the React/TypeScript source: it may import only from `ufo/kit`, must mount into the root element, must directly render designed kit components, and must follow product styling rules.

It also defines the public tools the main agent and builder subagent can call: design, accept a wireframe, write source, read source excerpts for repair, edit source, and delegate a full build. Think of it like a workshop with locked stations: the design station must finish before the coding station opens, and the deployment station stays locked until QA leaves a signed receipt.

#### Function details

##### `_local_source_bindings`  (lines 508–525)

```
def _local_source_bindings(code: str) -> set[str]
```

**Purpose**: Finds names that are defined locally inside the app source, such as variables, functions, classes, destructured values, and function parameters. This matters because the validator needs to tell the difference between a real imported UI component and a local component with the same JSX-looking name.

**Data flow**: It receives the source code as text. It scans the text with regular expressions and collects locally declared names into a set. It returns that set so later checks can ignore those local names when looking for imported UFO kit components.

**Call relations**: It is used by `_rendered_application_components` while checking `app.tsx`. That caller first removes comments and strings, then asks this helper which names are local before deciding which JSX tags really came from `ufo/kit`.

*Call graph*: called by 1 (_rendered_application_components); 1 external calls (findall).


##### `ApplicationBuilderTask.source_is_the_scaffolds_app_tsx`  (lines 684–696)

```
def source_is_the_scaffolds_app_tsx(self) -> 'ApplicationBuilderTask'
```

**Purpose**: Checks that a builder task points to exactly the allowed source file: `app.tsx` directly inside the scaffold folder under `/workspace`. This prevents a build task from wandering into another path.

**Data flow**: It reads `scaffold_path` and `source_path` from the task. It safely normalizes both paths inside `/workspace`, then verifies that the source path equals the scaffold path plus `app.tsx`. It returns the same task if valid, or raises a validation error if not.

**Call relations**: This validation runs whenever an `ApplicationBuilderTask` is created or loaded from JSON. The tools that spawn the builder rely on it so every later file operation is aimed at the fixed app source location.

*Call graph*: 2 external calls (PurePosixPath, contained_relative).


##### `ApplicationWireframeResult.result_matches_status`  (lines 730–739)

```
def result_matches_status(self) -> 'ApplicationWireframeResult'
```

**Purpose**: Makes sure a wireframe result is internally consistent. A ready wireframe must include the shared filename and design digest, while a blocked wireframe must explain what stopped it.

**Data flow**: It reads the result status and related fields. It compares the fields against the meaning of the status. It returns the same result if the fields make sense, or raises a validation error if they contradict each other.

**Call relations**: This is used automatically by the data model when wireframe tool output is created or checked. It keeps callers like `design_ufo_application` from returning half-ready or misleading wireframe results.


##### `ApplicationBuilderResult.result_matches_status`  (lines 757–776)

```
def result_matches_status(self) -> 'ApplicationBuilderResult'
```

**Purpose**: Makes sure a full builder result says one clear thing: wireframe, deployed, or blocked. Each status has required evidence and forbidden extra fields.

**Data flow**: It reads fields such as source path, design path, site name, site URL, QA counts, and blocker text. It verifies that those fields match the stated status. It returns the validated result, or raises an error if the result is contradictory.

**Call relations**: This validation protects the boundary between the worker subagent and the rest of the product. Callers such as `build_ufo_application`, `design_ufo_application`, and `ApplicationBuildAcceptance.accept` can trust that a parsed result has the expected shape.


##### `ApplicationBuildAcceptance.accept`  (lines 786–872)

```
async def accept(self, result: ApplicationBuilderResult) -> ApplicationBuilderResult
```

**Purpose**: Performs the final acceptance checks before a worker-built app becomes the bound homepage. It verifies that the deployment, stored source, QA proof, and accepted source hash all match.

**Data flow**: It receives a worker result. It first rejects obvious wrong results, then reads QA proof from extension storage, reads the deployed site record, checks site ownership and freshness, checks the deployed `app.tsx` hash, reads the accepted source hash from the sandbox, and finally binds the site as the homepage. It returns either an updated deployed result or a blocked result explaining what failed.

**Call relations**: This is called by `build_ufo_application` after the builder subagent finishes. It uses `_initial_result`, `_proof`, `_blocked`, `_source_acceptance_path`, and `_runtime_root`, and also reads hosted site records, so it is the bridge between subagent output and product-owned acceptance.

*Call graph*: calls 5 internal fn (_blocked, _initial_result, _proof, _runtime_root, _source_acceptance_path); 6 external calls (__init__, __init__, model_copy, model_validate_json, authority_member_id, site_url).


##### `ApplicationBuildAcceptance._initial_result`  (lines 874–881)

```
def _initial_result(self, result: ApplicationBuilderResult) -> ApplicationBuilderResult | None
```

**Purpose**: Rejects worker results that are clearly not an acceptable deployed app. For example, it blocks a wireframe returned during a build or a result pointing at the wrong source path.

**Data flow**: It receives the worker result. It checks the status and source path. It returns `None` if the result can continue through deeper acceptance checks, or returns a blocked result if the basic shape is wrong.

**Call relations**: It is the first checkpoint inside `ApplicationBuildAcceptance.accept`. When it finds a problem, it hands off to `_blocked` to make a consistent blocked result.

*Call graph*: calls 1 internal fn (_blocked); called by 1 (accept).


##### `ApplicationBuildAcceptance._proof`  (lines 883–905)

```
async def _proof(self, result: ApplicationBuilderResult, extension: ExtensionContext) -> ApplicationQaProof | ApplicationBuilderResult
```

**Purpose**: Loads and validates the product QA proof for the builder turn. The deployment cannot be accepted unless this proof exists and the worker did not report browser errors.

**Data flow**: It receives the worker result and extension context. It looks up the QA proof in extension storage using the child turn id, validates it as `ApplicationQaProof`, and checks reported errors. It returns the proof if valid, or a blocked application result if proof is missing or browser errors were reported.

**Call relations**: It is called by `ApplicationBuildAcceptance.accept` after basic result checks. It may call `_blocked` to stop acceptance early, or hand the validated proof back so deployment and source hashes can be compared.

*Call graph*: calls 1 internal fn (_blocked); called by 1 (accept); 1 external calls (model_validate).


##### `ApplicationBuildAcceptance._blocked`  (lines 907–920)

```
def _blocked(self, result: ApplicationBuilderResult, reason: str, browser_batches: int) -> ApplicationBuilderResult
```

**Purpose**: Creates a standardized blocked build result with the right source path, QA count, controls checked, observed errors, and blocker reason.

**Data flow**: It receives the original result, a human-readable reason, and the number of browser QA batches completed. It copies relevant evidence and returns a new `ApplicationBuilderResult` with status `blocked`.

**Call relations**: It is used throughout `ApplicationBuildAcceptance` whenever acceptance fails. This keeps all failure exits shaped the same way for the caller.

*Call graph*: called by 3 (_initial_result, _proof, accept); 1 external calls (__init__).


##### `EditApplicationSourceInput.json_text_edits_are_objects`  (lines 974–996)

```
def json_text_edits_are_objects(cls, value: object) -> object
```

**Purpose**: Accepts a few convenient edit formats and converts them into structured edit objects. This lets callers provide edits as JSON strings, search/replace patch text, or pairs of old and new strings.

**Data flow**: It receives the raw `edits` input before normal validation. It walks each item, parses JSON strings when present, recognizes `<<<<<<< SEARCH` style replacement blocks, and converts even-length string lists into old/new pairs. It returns a tuple of normalized edit-like objects.

**Call relations**: This runs automatically when `EditApplicationSourceInput` is validated before `edit_application_source` applies repairs. It makes the edit tool tolerant of common agent output formats while still ending in exact replacements.

*Call graph*: 1 external calls (loads).


##### `_validate_application_imports`  (lines 999–1010)

```
def _validate_application_imports(source: str) -> None
```

**Purpose**: Enforces the app source import rules. The app must import named items from `ufo/kit` and must not import anything else or export declarations.

**Data flow**: It receives the full `app.tsx` source as text. It scans import and export statements, rejects missing kit imports, side-effect imports, non-kit modules, non-named imports, exports, and old `UfoAppKit` usage. It returns nothing if valid, or raises a clear repair message if invalid.

**Call relations**: It is called by `_validate_application_source` as the first source-code gate. Source-writing and source-editing tools rely on it before compiling or publishing the source.

*Call graph*: called by 1 (_validate_application_source).


##### `_rendered_application_components`  (lines 1013–1040)

```
def _rendered_application_components(source: str) -> set[str]
```

**Purpose**: Finds which imported UFO kit UI components are actually rendered in JSX. It also proves that the app mounts with `mountApp` into the page root.

**Data flow**: It receives source text. It checks for the required root mount, collects named imports from `ufo/kit`, strips comments and string literals, removes locally declared names, and compares JSX component tags to imported kit components. It returns the set of kit component exports that are actually rendered.

**Call relations**: It is called by `_validate_application_source`. It uses `_local_source_bindings` to avoid mistaking local components for imported kit components, then passes its result into the designed-component check.

*Call graph*: calls 1 internal fn (_local_source_bindings); called by 1 (_validate_application_source).


##### `_validate_designed_components`  (lines 1043–1054)

```
def _validate_designed_components(rendered_kit_components: set[str], designed_kit_components: tuple[str, ...]) -> None
```

**Purpose**: Checks that the source directly renders the kit components promised by the accepted design. This keeps the final page from ignoring the visual contract.

**Data flow**: It receives the set of rendered kit components from the source and the tuple of kit components named by the design. It finds any designed components missing from the source. It returns nothing if all are present, or raises an error naming what must be rendered.

**Call relations**: It is called by `_validate_application_source` after source components are discovered. The design evidence comes from `_require_application_design`, which is used before writing or editing source.

*Call graph*: called by 1 (_validate_application_source).


##### `_validate_application_styling`  (lines 1057–1087)

```
def _validate_application_styling(source: str) -> None
```

**Purpose**: Enforces product styling rules for generated app pages. It blocks raw CSS values, reserved attributes, style tags, unsafe class patterns, and spacing or component choices that violate the design system.

**Data flow**: It receives source text. It searches for forbidden patterns such as `<style>`, `data-slot`, raw color or size values in arbitrary Tailwind classes, disallowed gap sizes, and other product-specific mistakes. It returns nothing when styling is acceptable, or raises a repair-oriented error.

**Call relations**: It is the final source-code gate inside `_validate_application_source`. Both full writes and edits must pass it before compilation and build output are accepted.

*Call graph*: called by 1 (_validate_application_source).


##### `_validate_application_source`  (lines 1090–1096)

```
def _validate_application_source(source: str, designed_kit_components: tuple[str, ...]=()) -> None
```

**Purpose**: Runs all source-code validation checks in the right order. It is the main local rulebook for `app.tsx` before the build system is allowed to compile it.

**Data flow**: It receives the app source and, optionally, kit components required by the design. It validates imports, detects rendered kit components, checks that designed components are present, and enforces styling rules. It returns nothing if the source passes, or raises a specific error if it fails.

**Call relations**: It is called by `write_application_source` and `edit_application_source`. Those tools use it before compiling and copying the candidate source into the real scaffold.

*Call graph*: calls 4 internal fn (_rendered_application_components, _validate_application_imports, _validate_application_styling, _validate_designed_components); called by 2 (edit_application_source, write_application_source).


##### `_parse_application_design`  (lines 1099–1127)

```
def _parse_application_design(source: str) -> tuple[ElementTree.Element, tuple[float, ...]]
```

**Purpose**: Parses the SVG design and checks the outer page contract. The design must be a safe SVG with the required 305-pixel width and a valid integer height.

**Data flow**: It receives SVG text. It rejects XML entity declarations, parses the XML, checks the root is `svg`, reads the `viewBox`, width, and height, and verifies the exact required dimensions. It returns the SVG root element and parsed viewBox values.

**Call relations**: It is called by `_validate_application_design`, which performs deeper checks on all SVG elements and regions after this basic parsing succeeds.

*Call graph*: called by 1 (_validate_application_design); 3 external calls (isfinite, split, fromstring).


##### `_visible_design_element`  (lines 1130–1156)

```
def _visible_design_element(element: ElementTree.Element, tag: str, attributes: dict[str, str]) -> bool
```

**Purpose**: Decides whether a specific SVG drawing element would visibly draw something. This helps reject empty designs that technically contain tags but no visible content.

**Data flow**: It receives an SVG element, its simple tag name, and normalized attributes. It checks tag-specific visibility rules, such as nonzero radius for circles, nonempty path data for paths, and nonblank text for text elements. It returns `True` if the element appears visible, otherwise `False`.

**Call relations**: It is called by `_validate_design_element` while walking the SVG tree. Its result contributes to the total visible drawing count used by `_validate_application_design`.

*Call graph*: called by 1 (_validate_design_element); 1 external calls (itertext).


##### `_validate_design_attributes`  (lines 1159–1166)

```
def _validate_design_attributes(element: ElementTree.Element) -> None
```

**Purpose**: Blocks active or external content inside the SVG design. A design should be a drawing, not a script, link, or remote fetch.

**Data flow**: It receives one SVG element. It checks every attribute name and value for event handlers or URL schemes such as `javascript:`, `data:`, `http:`, or `https:`. It returns nothing if attributes are safe, or raises an error if unsafe content appears.

**Call relations**: It is called by `_validate_design_element` for every element in the SVG. This makes the broader design validator safe before the file is stored or previewed.

*Call graph*: called by 1 (_validate_design_element).


##### `_validate_design_element`  (lines 1169–1222)

```
def _validate_design_element(element: ElementTree.Element, ids: set[str], regions: list[ElementTree.Element], kit_components: list[str]) -> bool
```

**Purpose**: Checks one SVG element against the design rules and records important markers. It rejects scripts, filters, masks, duplicate ids, bad region markers, and invalid kit component names.

**Data flow**: It receives an SVG element plus shared collections for ids, regions, and kit components. It normalizes tag and attributes, checks forbidden effects and active content, records `data-app-region` groups and `data-kit-component` groups, and asks whether the element is visibly drawable. It returns a boolean saying whether this element counts as a visible drawing element.

**Call relations**: It is called repeatedly by `_validate_application_design` while walking the SVG. It calls `_validate_design_attributes` and `_visible_design_element`, and fills the region and component lists that the final design validator checks.

*Call graph*: calls 2 internal fn (_validate_design_attributes, _visible_design_element); called by 1 (_validate_application_design); 1 external calls (itertext).


##### `_validate_application_design`  (lines 1225–1252)

```
def _validate_application_design(source: str) -> tuple[tuple[str, ...], tuple[str, ...], int]
```

**Purpose**: Runs the full SVG design validation. It proves that the design is a safe, visible, correctly sized page with unique named regions and at least one visual UFO kit component.

**Data flow**: It receives SVG text. It parses the root and viewBox, walks every element, gathers region names and kit component names, checks drawing content exists, verifies region count and uniqueness, and rejects nested regions. It returns the region names, kit component names, and page height.

**Call relations**: It is used before accepting or reusing any design by `write_application_design`, `accept_application_wireframe`, `design_ufo_application`, `build_ufo_application`, and `_require_application_design`. It is the shared design gate for both wireframes and builds.

*Call graph*: calls 2 internal fn (_parse_application_design, _validate_design_element); called by 4 (_require_application_design, build_ufo_application, design_ufo_application, write_application_design).


##### `_build_application_project`  (lines 1255–1269)

```
async def _build_application_project(ctx: ToolContext, project: str, runtime_root: str | None=None) -> None
```

**Purpose**: Builds the app project with the required page kit installed. This catches TypeScript and bundling errors before a source file can be accepted.

**Data flow**: It receives a tool context, project path, and optionally a runtime root. It writes the project config, unpacks the page kit, runs `vite build`, and inspects the command result. It returns nothing on success, or raises an error with compiler output if the build fails.

**Call relations**: It is called by `_compile_application_source` for isolated candidate checks, and by `write_application_source` and `edit_application_source` for the real scaffold build after the candidate passes.

*Call graph*: called by 3 (_compile_application_source, edit_application_source, write_application_source); 1 external calls (unpack_page_kit).


##### `_compile_application_source`  (lines 1272–1285)

```
async def _compile_application_source(ctx: ToolContext, task: ApplicationBuilderTask, source: str) -> None
```

**Purpose**: Compiles a proposed `app.tsx` in a temporary runtime project before touching the real scaffold. This is a dry run that protects the working app from broken candidates.

**Data flow**: It receives the tool context, task, and source text. It creates a temporary project, copies the scaffold `index.html`, writes the candidate `app.tsx`, and calls `_build_application_project` there. It returns nothing if the candidate compiles, or raises an error if it does not.

**Call relations**: It is called by `write_application_source` and `edit_application_source` before those tools write the source to the actual scaffold and run the final build.

*Call graph*: calls 2 internal fn (_build_application_project, _runtime_root); called by 2 (edit_application_source, write_application_source).


##### `_source_claim_path`  (lines 1288–1292)

```
async def _source_claim_path(ctx: ToolContext, task: ApplicationBuilderTask, turn_id: UUID) -> str
```

**Purpose**: Builds the runtime path for the file that proves this turn has claimed the right to create the initial source candidate. It uses a hash so the path is stable but does not expose raw path text directly.

**Data flow**: It receives the tool context, task, and turn id. It hashes the task source path and combines it with the turn id inside the tool-output area. It returns the sandbox runtime path as a string.

**Call relations**: It is used by `write_application_source` when claiming first-write ownership, and by `_require_application_source` when read or edit tools need to confirm that a candidate already exists.

*Call graph*: called by 2 (_require_application_source, write_application_source); 1 external calls (sha256).


##### `_runtime_root`  (lines 1295–1296)

```
async def _runtime_root(ctx: ToolContext) -> str
```

**Purpose**: Finds the filesystem root used for runtime-owned tool output. Helper scripts need this root so their containment checks know what area they are allowed to touch.

**Data flow**: It receives the tool context. It asks the sandbox for the runtime path of `tool-output`, then returns that path's parent directory as a string. It changes nothing.

**Call relations**: Many tools and helpers call this before running small sandbox Python scripts, including source reads, claims, design acceptance, compile checks, and final build acceptance.

*Call graph*: called by 9 (accept, _compile_application_source, _require_application_design, _require_application_source, build_ufo_application, edit_application_source, read_application_source, write_application_design, write_application_source); 1 external calls (PurePosixPath).


##### `_design_path`  (lines 1299–1300)

```
def _design_path(task: ApplicationBuilderTask) -> str
```

**Purpose**: Returns the fixed design file path for a builder task. The design always lives beside `app.tsx` as `application-design.svg`.

**Data flow**: It receives the task. It combines `task.scaffold_path` with `application-design.svg` and returns that string. It does not read or write files.

**Call relations**: It is used wherever the design path must be consistent: design claiming, design requirement checks, wireframe acceptance, and writing a new design.

*Call graph*: called by 4 (_design_claim_path, _require_application_design, accept_application_wireframe, write_application_design).


##### `_design_claim_path`  (lines 1303–1307)

```
async def _design_claim_path(ctx: ToolContext, task: ApplicationBuilderTask, turn_id: UUID) -> str
```

**Purpose**: Builds the runtime path for the file that proves this turn has claimed the design. This prevents two different design writes from racing or silently replacing each other.

**Data flow**: It receives the tool context, task, and turn id. It computes the fixed design path, hashes it, and creates a runtime claim path under tool output. It returns that path as a string.

**Call relations**: It is used by `write_application_design` when sealing a design and by `_require_application_design` when source tools need proof that the design stage is complete.

*Call graph*: calls 1 internal fn (_design_path); called by 2 (_require_application_design, write_application_design); 1 external calls (sha256).


##### `application_design_acceptance_relative`  (lines 1310–1316)

```
def application_design_acceptance_relative(design_path: str, turn_id: UUID) -> str
```

**Purpose**: Returns the relative runtime path where an accepted SVG design is stored for one builder turn. This path is product-owned evidence, separate from the editable workspace file.

**Data flow**: It receives a design path and turn id. It hashes the design path and formats a stable accepted SVG filename under tool output. It returns that relative path.

**Call relations**: It is called by `write_application_design`, which uses the path while sealing the accepted design and later cleaning it up if something fails.

*Call graph*: called by 1 (write_application_design); 1 external calls (sha256).


##### `application_design_evidence_relative`  (lines 1319–1325)

```
def application_design_evidence_relative(design_path: str, turn_id: UUID) -> str
```

**Purpose**: Returns the relative runtime path where accepted design evidence is stored for one builder turn. The evidence records the design hash, kit components, and rendered regions.

**Data flow**: It receives a design path and turn id. It hashes the design path and formats a stable accepted evidence JSON filename under tool output. It returns that relative path.

**Call relations**: It is called by `write_application_design` so the design and its proof are stored side by side in predictable runtime-owned locations.

*Call graph*: called by 1 (write_application_design); 1 external calls (sha256).


##### `_source_candidate_path`  (lines 1328–1334)

```
async def _source_candidate_path(ctx: ToolContext, task: ApplicationBuilderTask, turn_id: UUID) -> str
```

**Purpose**: Builds the runtime path for the current candidate `app.tsx`. The candidate is kept separately from the real scaffold until validation and compilation pass.

**Data flow**: It receives the tool context, task, and turn id. It hashes the source path, combines it with the turn id, and returns a runtime path ending in `candidate.tsx`.

**Call relations**: It is used by `write_application_source` to save the first candidate, by `read_application_source` to show repair excerpts, and by `edit_application_source` to read and update the candidate.

*Call graph*: called by 3 (edit_application_source, read_application_source, write_application_source); 1 external calls (sha256).


##### `_render_application_design`  (lines 1337–1388)

```
async def _render_application_design(ctx: ToolContext, candidate_path: str, preview_path: str, names: tuple[str, ...], page_height: int) -> tuple[ApplicationAuditRegion, ...]
```

**Purpose**: Runs a browser-based audit of the SVG design and returns the visible named regions. This catches layout problems that plain XML parsing cannot see, such as content outside the viewBox or overlapping regions.

**Data flow**: It receives the context, candidate SVG path, preview output path, expected region names, and page height. It writes an audit script, runs it with Node, interprets errors, validates the JSON output, checks the rendered region names and overlaps, and returns the rendered region objects.

**Call relations**: It is called by `write_application_design` after static SVG validation. Its returned regions become part of the accepted design evidence and are also checked for size and fold-position rules.

*Call graph*: called by 1 (write_application_design); 2 external calls (search, application_region_relation).


##### `_source_acceptance_path`  (lines 1391–1397)

```
async def _source_acceptance_path(ctx: ToolContext, task: ApplicationBuilderTask, turn_id: UUID) -> str
```

**Purpose**: Builds the runtime path where the accepted source hash is written. This file is the receipt proving which exact `app.tsx` passed validation and build checks.

**Data flow**: It receives the tool context, task, and turn id. It hashes the source path and returns a runtime path ending in `accepted`. It does not inspect the source itself.

**Call relations**: It is written by `write_application_source` and `edit_application_source` after successful validation and build. Later, `ApplicationBuildAcceptance.accept` reads it to verify that the deployed site used that same source.

*Call graph*: called by 3 (accept, edit_application_source, write_application_source); 1 external calls (sha256).


##### `_require_application_source`  (lines 1400–1409)

```
async def _require_application_source(ctx: ToolContext, task: ApplicationBuilderTask) -> None
```

**Purpose**: Ensures that an initial source candidate has already been claimed before repair tools run. This stops read and edit tools from operating before there is a source to repair.

**Data flow**: It receives the context and task. It locates the source claim path, runs a contained script to confirm the claim file exists and is a regular file, and returns nothing if present. It raises a user-facing error if source writing has not happened yet.

**Call relations**: It is called by `read_application_source` and `edit_application_source`. Those tools only proceed after this guard confirms that `write_application_source` has already created a candidate.

*Call graph*: calls 2 internal fn (_runtime_root, _source_claim_path); called by 2 (edit_application_source, read_application_source).


##### `_require_application_design`  (lines 1412–1433)

```
async def _require_application_design(ctx: ToolContext, task: ApplicationBuilderTask) -> tuple[str, ...]
```

**Purpose**: Ensures that the design stage is complete before source code can be written or repaired. It also returns the kit components the source must directly render.

**Data flow**: It receives the context and task. It checks for the design claim, reads the design SVG from the workspace, compares its hash to an accepted wireframe digest when one exists, validates the design, and returns the kit component names from the design evidence.

**Call relations**: It is called by `write_application_source` and `edit_application_source` before source validation. The returned component list flows into `_validate_application_source` so the implementation matches the visual contract.

*Call graph*: calls 4 internal fn (_design_claim_path, _design_path, _runtime_root, _validate_application_design); called by 2 (edit_application_source, write_application_source); 1 external calls (sha256).


##### `write_application_design`  (lines 1436–1590)

```
async def write_application_design(ctx: ToolContext, args: WriteApplicationDesignInput) -> ToolResult
```

**Purpose**: Writes and seals the SVG visual contract for the application before any source work begins. It validates the drawing, runs the browser audit, stores immutable accepted evidence, then copies the design into the scaffold.

**Data flow**: It receives the tool context and SVG content. It loads the task, checks any accepted digest, validates the SVG, writes a candidate file, renders and audits it, builds evidence JSON, claims the design, stores accepted design and evidence files, writes the workspace design, and returns JSON with the path, digest, size, height, and rendered regions. If a late failure happens, it runs cleanup so partial claims do not remain.

**Call relations**: It is a profile-only builder tool and is also called by `accept_application_wireframe`. It uses the design path helpers, `_validate_application_design`, `_render_application_design`, `_complete_application_design_cleanup`, and the acceptance/evidence path helpers.

*Call graph*: calls 8 internal fn (_complete_application_design_cleanup, _design_claim_path, _design_path, _render_application_design, _runtime_root, _validate_application_design, application_design_acceptance_relative, application_design_evidence_relative); called by 1 (accept_application_wireframe); 7 external calls (__init__, __init__, __init__, sha256, dumps, application_design_region_fold_failure, application_design_region_size_failure).


##### `accept_application_wireframe`  (lines 1593–1604)

```
async def accept_application_wireframe(ctx: ToolContext, _args: AcceptApplicationWireframeInput) -> ToolResult
```

**Purpose**: Accepts a previously staged member-approved wireframe as the design for this build. It does not generate or change the SVG; it reuses the stored file and sends it through the normal design sealing path.

**Data flow**: It receives the tool context and empty input. It loads the builder task, checks that the task has an accepted wireframe digest, reads `application-design.svg`, and calls `write_application_design` with that exact content. It returns whatever the design-writing tool returns.

**Call relations**: It is available only to the builder profile. It delegates the real validation and sealing to `write_application_design`, so accepted wireframes follow the same evidence rules as newly written designs.

*Call graph*: calls 2 internal fn (_design_path, write_application_design); 1 external calls (__init__).


##### `_complete_application_design_cleanup`  (lines 1607–1623)

```
async def _complete_application_design_cleanup(ctx: ToolContext, program: str, *args: str) -> tuple[ExecResult | None, tuple[str, ...]]
```

**Purpose**: Runs design cleanup even if the surrounding task is cancelled. This prevents stale claim or accepted-design files from being left behind after a failed design write.

**Data flow**: It receives the context, a cleanup script, and script arguments. It starts the sandbox Python cleanup as an asyncio task, shields it from cancellation, records interruption messages, then returns the execution result and any cleanup failure notes.

**Call relations**: It is called by `write_application_design` inside the exception path. If design acceptance partly succeeded and then failed, this helper gives the file a chance to release its claim or accepted evidence.

*Call graph*: called by 1 (write_application_design); 2 external calls (create_task, shield).


##### `read_application_source`  (lines 1626–1680)

```
async def read_application_source(ctx: ToolContext, args: ReadApplicationSourceInput) -> ToolResult
```

**Purpose**: Shows small, useful excerpts from the current candidate `app.tsx` during repair. It avoids dumping the whole file and instead gives line windows around requested search terms plus the beginning and end.

**Data flow**: It receives search terms. It verifies a source candidate exists, reads the candidate file, checks its size, counts matching lines for each term, builds compact line-numbered excerpts within a character budget, and returns the excerpts as tool text.

**Call relations**: It is a builder repair tool used after an edit mismatch or QA repair instruction. It calls `_require_application_source`, `_source_candidate_path`, and `_runtime_root` before formatting the source snippets for the agent.

*Call graph*: calls 3 internal fn (_require_application_source, _runtime_root, _source_candidate_path); 2 external calls (__init__, __init__).


##### `edit_application_source`  (lines 1683–1732)

```
async def edit_application_source(ctx: ToolContext, args: EditApplicationSourceInput) -> ToolResult
```

**Purpose**: Applies exact text replacements to the current candidate `app.tsx`, then revalidates and rebuilds the app. It is the controlled repair path after the initial full source write.

**Data flow**: It receives one or more edits with exact `old_text` and `new_text`. It confirms source and design claims, reads the candidate, verifies each old text appears exactly once and edits do not overlap, applies replacements, checks size, writes the updated candidate, validates source rules, compiles in a temporary project, writes the real scaffold source, builds the real project, records the accepted source hash, and returns a JSON summary.

**Call relations**: It is called as a builder profile tool during repair. It uses `_require_application_source`, `_require_application_design`, `_source_candidate_path`, `_validate_application_source`, `_compile_application_source`, `_build_application_project`, and `_source_acceptance_path`.

*Call graph*: calls 8 internal fn (_build_application_project, _compile_application_source, _require_application_design, _require_application_source, _runtime_root, _source_acceptance_path, _source_candidate_path, _validate_application_source); 4 external calls (__init__, __init__, sha256, dumps).


##### `write_application_source`  (lines 1735–1779)

```
async def write_application_source(ctx: ToolContext, args: WriteApplicationSourceInput) -> ToolResult
```

**Purpose**: Writes the first complete `app.tsx` candidate for the build. After this, further changes must go through exact edits rather than another full overwrite.

**Data flow**: It receives complete source text. It confirms the design is accepted, claims first-write ownership, stores the candidate, validates imports/components/styling, compiles it in a temporary project, writes it to the real scaffold, builds the real project, records the accepted source hash, and returns a JSON summary with path and size. If validation fails, it keeps the candidate for repair and tells the agent to use read/edit tools.

**Call relations**: It is a builder profile tool. It calls `_require_application_design`, `_source_claim_path`, `_source_candidate_path`, `_validate_application_source`, `_compile_application_source`, `_build_application_project`, and `_source_acceptance_path`.

*Call graph*: calls 8 internal fn (_build_application_project, _compile_application_source, _require_application_design, _runtime_root, _source_acceptance_path, _source_candidate_path, _source_claim_path, _validate_application_source); 4 external calls (__init__, __init__, sha256, dumps).


##### `design_ufo_application`  (lines 1782–1840)

```
async def design_ufo_application(ctx: ToolContext, args: DesignUfoApplicationInput) -> ToolResult
```

**Purpose**: Runs the builder in wireframe-only mode and shares the resulting SVG with the member. It stores the exact accepted wireframe so a later build can be forced to match it.

**Data flow**: It receives an application name, prompt, and optional revision. It ensures the scaffold exists, spawns the application builder subagent in `wireframe` phase, validates the worker output and SVG file, checks the digest, stores a preview and shared artifact, saves the wireframe content and digest in extension storage, and returns a ready or blocked wireframe result.

**Call relations**: This is the main-facing wireframe delegation tool. It calls `_ensure_application_scaffold` and `_validate_application_design`, then relies on the builder profile to produce `application-design.svg` through `write_application_design`.

*Call graph*: calls 4 internal fn (share_artifact, store_preview, _ensure_application_scaffold, _validate_application_design); 8 external calls (__init__, __init__, __init__, __init__, __init__, spawn, sha256, PurePosixPath).


##### `_ensure_application_scaffold`  (lines 1843–1851)

```
async def _ensure_application_scaffold(ctx: ToolContext) -> None
```

**Purpose**: Creates the fixed app scaffold files if they are missing. This gives the builder a predictable `index.html`, placeholder `app.tsx`, and preview wrapper.

**Data flow**: It receives the tool context. For each required scaffold file, it tries a tiny contained read; if the read fails, it writes the default content. It returns nothing after the scaffold is present.

**Call relations**: It is called by `design_ufo_application` and `build_ufo_application` before spawning the builder. Those flows need the same project structure whether they are designing or building.

*Call graph*: called by 2 (build_ufo_application, design_ufo_application).


##### `build_ufo_application`  (lines 1854–1941)

```
async def build_ufo_application(ctx: ToolContext, _args: BuildUfoApplicationInput) -> ToolResult
```

**Purpose**: Delegates one full application build to the controlled builder subagent and returns the accepted structured result. It also wires in any stored member-approved wireframe and runs final product acceptance.

**Data flow**: It receives the tool context and empty input. It claims that this parent turn has delegated only once, records redeploy and audit-contract information, ensures the scaffold exists, loads any stored wireframe, writes it into the design path, spawns the builder subagent with the build task, converts missing or failed worker output into blocked results, and otherwise passes the worker result through `ApplicationBuildAcceptance.accept`. If a stored wireframe was successfully deployed, it deletes that stored wireframe.

**Call relations**: This is the main build delegation tool. It calls `_ensure_application_scaffold`, `_runtime_root`, `_validate_application_design`, creates an `ApplicationBuilderTask`, spawns the `APPLICATION_BUILDER_PROFILE`, and then relies on `ApplicationBuildAcceptance` for the final gate.

*Call graph*: calls 3 internal fn (_ensure_application_scaffold, _runtime_root, _validate_application_design); 9 external calls (__init__, __init__, __init__, __init__, __init__, spawn, sha256, format, format).


##### `limit_application_builder_repair_reads`  (lines 1944–1979)

```
async def limit_application_builder_repair_reads(ctx: HookContext) -> Deny | None
```

**Purpose**: Limits how many source-read calls the builder can make after product QA has asked for repairs. This nudges the agent to stop rereading and actually edit, test, and deploy.

**Data flow**: It receives a hook context before tool use. It ignores unrelated turns and tools, checks whether a QA repair attempt exists, resets the read counter when an edit tool is used, increments the counter for read calls, and returns a denial once the limit is reached.

**Call relations**: It is a pre-tool-use guard for the application builder profile. It watches `read_application_source` and `edit_application_source` calls and can deny reads with a repair instruction.

*Call graph*: 2 external calls (__init__, format).


##### `enforce_application_builder_phase`  (lines 1982–1997)

```
async def enforce_application_builder_phase(ctx: HookContext) -> Deny | None
```

**Purpose**: Keeps the builder in the right phase. In wireframe mode it may only write the SVG design, and when a member-approved design already exists it may not redesign.

**Data flow**: It receives a hook context before tool use. It ignores unrelated turns, loads the builder task, checks the phase and accepted design digest, and returns a denial when the requested tool is not allowed for that phase. Otherwise it returns nothing.

**Call relations**: It protects the builder profile before tools run. It prevents wireframe-only delegations from writing source, running QA, or deploying, and prevents accepted wireframes from being overwritten by `write_application_design`.

*Call graph*: 1 external calls (__init__).


##### `is_application_creation_request`  (lines 2000–2004)

```
def is_application_creation_request(text: str) -> bool
```

**Purpose**: Detects member messages that look like requests to create an application, while excluding broader website or platform requests. This helps route the member to the right creation skill.

**Data flow**: It receives the member text. It checks one regular expression for app-creation language and another for excluded site or platform language. It returns `True` only when the text looks like a UFO application request.

**Call relations**: It is called by `enforce_application_creation_route`. That hook uses the boolean result to decide whether to force the main agent to load the application creation skill first.

*Call graph*: called by 1 (enforce_application_creation_route).


##### `enforce_application_creation_route`  (lines 2007–2030)

```
async def enforce_application_creation_route(ctx: HookContext) -> Deny | None
```

**Purpose**: Forces main-agent app-creation requests through the `create-application` skill before any other tool is used. This keeps application creation on the intended path.

**Data flow**: It receives a hook context. It ignores non-main-agent turns, subagents, non-member turns, and messages that do not look like app-creation requests. For matching turns, it watches tool use: it allows the first `load_skill` only if it loads `create-application`, records that the route is satisfied, and denies other tools until that happens.

**Call relations**: It calls `is_application_creation_request` as its classifier and returns `Deny` objects when routing is wrong. It runs before main-agent tool use, not inside the builder subagent itself.

*Call graph*: calls 1 internal fn (is_application_creation_request); 1 external calls (__init__).


##### `require_application_builder_qa`  (lines 2033–2046)

```
async def require_application_builder_qa(ctx: HookContext) -> Deny | None
```

**Purpose**: Blocks deployment unless product QA proof has already been stored for this builder turn. It is the deployment lock on the assembly line.

**Data flow**: It receives a hook context. For builder turns, it looks up the QA proof in extension storage, validates it as `ApplicationQaProof`, and returns nothing if proof is present and valid. If no proof exists, it returns a denial telling the agent to run and pass product QA first.

**Call relations**: It is a pre-deployment guard for the application builder profile. The final acceptance code later rechecks the same kind of proof, so deployment is protected both before the tool call and before homepage binding.

*Call graph*: 2 external calls (__init__, model_validate).


### `extensions/sites/ufo_ext_sites/tools.py`

`orchestration` · `tool execution during build, preview, deploy, publish, QA, and homepage binding`

This file is the control desk for website-related actions. Without it, an agent could still create files, but it would not have a safe, repeatable way to build them, start a server, prove the server is ready, make a permanent link, store static source files, create preview images, or bind a site as an agent homepage.

The main idea is: paths named by the model are first forced into the workspace, then commands run inside the sandbox, which is the contained environment for the task. Server tools clean old logs and stop anything already using the chosen port before starting a new background process. They then probe the port until it answers, so callers get a working URL instead of a race.

Deployment adds more. For static sites, the file tree is checked, size-limited, copied into the blob store, and recorded in a manifest. The served port is registered as a hosted site with a stable public URL. A preview image and share card are then generated. Publishing is similar, but keeps the app running from its own sandbox server instead of storing static source.

The file also contains special quality-audit and deploy rules for the UFO application builder profile, plus homepage binding rules. Those rules matter because changing visibility or homepage ownership can expose content to new people, so the code requires the right member or speaker at the right time.

#### Function details

##### `StartServerInput.validate_port`  (lines 454–459)

```
def validate_port(self) -> 'StartServerInput'
```

**Purpose**: This validates the inputs for starting a server. It makes sure a provided command is not just blank text and that a provided port number is in the usable network port range.

**Data flow**: It receives a filled-in StartServerInput object → checks the command and port fields → returns the same object if they are valid, or raises a validation error before any server is started.

**Call relations**: This runs automatically when start_server input is parsed. It protects the later start_server flow from receiving an empty server command or an impossible port.


##### `_json_result`  (lines 499–500)

```
def _json_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: This wraps a plain Python dictionary as the standard tool response. It is the small adapter that turns internal results into JSON text the caller can read.

**Data flow**: It takes a dictionary → converts it to a JSON string → places that string inside text content and then inside a ToolResult.

**Call relations**: Most public tool handlers call this at the end, after they have built, served, deployed, audited, or bound something. It is the final packaging step before returning to the tool caller.

*Call graph*: called by 7 (_redeploy_homepage, deploy_website, publish_website, qa_ufo_application, set_homepage, start_server, website); 3 external calls (__init__, __init__, dumps).


##### `_free_log`  (lines 503–523)

```
async def _free_log(ctx: ToolContext, log_path: str) -> None
```

**Purpose**: This clears a chosen log-file name safely before a server writes to it. It exists to stop a malicious or accidental symbolic link from making the server overwrite some other file.

**Data flow**: It receives the tool context and a log path → decides which safe root the log must live under → runs a guarded sandbox script that removes only that contained file name → returns nothing, or raises an error if the path is unsafe.

**Call relations**: _serve calls this before launching any background server. It prepares the log slot so the later shell redirect can create a fresh file instead of following a planted link.

*Call graph*: called by 1 (_serve); 1 external calls (PurePosixPath).


##### `_stop_server`  (lines 526–531)

```
async def _stop_server(ctx: ToolContext, port: int) -> None
```

**Purpose**: This frees a network port inside the sandbox before a new server starts. It prevents an older process from keeping the port and making the new deploy appear to start when it did not.

**Data flow**: It receives a port number → runs a sandbox Python script that finds listening processes on that port and terminates them → returns nothing if the port is cleared, or raises an error if cleanup fails.

**Call relations**: _serve calls this as part of every server start. It happens before the new detached task is launched.

*Call graph*: called by 1 (_serve).


##### `_stop_server_task`  (lines 534–551)

```
async def _stop_server_task(ctx: ToolContext, command: str, base: str, pid: str) -> None
```

**Purpose**: This stops a specific background server task that was just launched but failed readiness checks or was interrupted. It cleans up the task instead of leaving a broken server running.

**Data flow**: It receives the context, server command, task journal location, and process id → asks the sandbox to stop the matching task → waits for the task command to finish → returns nothing or raises if it cannot stop cleanly.

**Call relations**: _serve calls this only on failure paths after a detached server was started. It is the emergency brake for a launch that did not become reachable.

*Call graph*: called by 1 (_serve).


##### `_reset_server_task`  (lines 554–572)

```
async def _reset_server_task(ctx: ToolContext, base: str) -> None
```

**Purpose**: This clears the saved task journal used for background server launches. It makes sure a new start really starts a new server instead of accidentally reconnecting to an old recorded task.

**Data flow**: It receives a task journal base path → runs a sandbox script that stops any recorded process and removes journal files → returns nothing if the journal is empty afterward.

**Call relations**: _serve calls this after freeing the port and before starting the new task. It keeps repeated deploys and retries from inheriting stale task state.

*Call graph*: called by 1 (_serve).


##### `_serve`  (lines 575–643)

```
async def _serve(ctx: ToolContext, command: str, project: str, port: int, log_path: str) -> dict[str, object]
```

**Purpose**: This is the shared engine for starting a web server in the sandbox and proving it is reachable. It handles log safety, port cleanup, background launch, readiness polling, and useful failure messages.

**Data flow**: It receives a command, project directory, port, and log path → clears the log, frees the port, resets the task record, launches the command in the background, and repeatedly tries to connect to the port → returns the sandbox-local URL, port, and log path, or raises an error with log output if the server never becomes ready.

**Call relations**: start_server, deploy_website, publish_website, and _redeploy_homepage all rely on this. It calls the lower-level cleanup helpers first, then hands back a proven running server to the hosting or preview steps.

*Call graph*: calls 4 internal fn (_free_log, _reset_server_task, _stop_server, _stop_server_task); called by 4 (_redeploy_homepage, deploy_website, publish_website, start_server); 3 external calls (sha256, quote, shell_path).


##### `_site_media_type`  (lines 646–648)

```
def _site_media_type(path: str) -> str
```

**Purpose**: This chooses the correct internet content type for a file based on its extension. That tells browsers whether a file is HTML, JavaScript, CSS, an image, and so on.

**Data flow**: It receives a file path → looks at the part after the last dot → returns a media type string, adding a UTF-8 character set for text files.

**Call relations**: _promote_source uses this while building the static-site manifest. The manifest later tells the hosting layer how to serve each stored file.

*Call graph*: called by 1 (_promote_source).


##### `_source_listing`  (lines 651–672)

```
async def _source_listing(ctx: ToolContext, project: str) -> dict[str, dict[str, object]]
```

**Purpose**: This inspects a static site directory inside the sandbox and records which files it contains, how large they are, and their SHA-256 hashes. A SHA-256 hash is a fingerprint of file contents.

**Data flow**: It receives a project directory → runs a sandbox walker that skips folders like .git and node_modules, rejects oversized sites, and computes file sizes and hashes → returns a dictionary describing the files.

**Call relations**: _served_directory calls this before deciding what to host. It supplies the file list that _promote_source later turns into a stored manifest.

*Call graph*: called by 1 (_served_directory); 1 external calls (loads).


##### `_promote_source`  (lines 675–716)

```
async def _promote_source(ctx: ToolContext, project: str, conversation_id: UUID, name: str, listing: dict[str, dict[str, object]]) -> str
```

**Purpose**: This copies a static site’s served files into the project’s blob store and creates the manifest that records them as the site’s source of record. The blob store is durable storage outside the running sandbox.

**Data flow**: It receives the context, project directory, conversation id, site name, and file listing → builds storage keys under a new unique prefix, assigns media types, and uploads the files either by presigned upload links or direct streams → returns the manifest as JSON text.

**Call relations**: deploy_website and _redeploy_homepage call this after they know which directory will be served. It uses _site_media_type for each file, then passes its manifest result into site registration or redeployment.

*Call graph*: calls 1 internal fn (_site_media_type); called by 2 (_redeploy_homepage, deploy_website); 4 external calls (__init__, __init__, transfer, uuid4).


##### `_illustrate`  (lines 719–741)

```
async def _illustrate(ctx: ToolContext, name: str, port: int, conversation_id: UUID) -> None
```

**Purpose**: This creates visual previews for a hosted site. It asks the preview service to take a screenshot and also builds a share card image for link previews.

**Data flow**: It receives the context, site name, served port, and conversation id → opens the hosted site through the preview system → stores the preview image if one is produced → asks the share-card helper to draw a card → returns nothing.

**Call relations**: deploy_website, publish_website, and _redeploy_homepage call this after the site has already been registered. That order matters because registration retires any previous site on the port before the screenshot is taken.

*Call graph*: calls 1 internal fn (render_site_preview); called by 3 (_redeploy_homepage, deploy_website, publish_website); 2 external calls (__init__, draw_from_page).


##### `_refuse_before_serving`  (lines 744–784)

```
async def _refuse_before_serving(ctx: ToolContext, raw_name: str, port: int, visibility: Visibility | None) -> tuple[str, HostedSite | None]
```

**Purpose**: This checks whether a deploy is allowed before it kills anything currently using the port. It protects an existing hosted site from being taken down by a deploy that would later be rejected.

**Data flow**: It receives the requested site name, port, and optional visibility → confirms there is an acting member, checks whether a live speaker is needed for visibility changes, normalizes the site name, and asks the hosted-site store whether registration would be allowed → returns the normalized name and any site that would be displaced.

**Call relations**: deploy_website and publish_website call this before starting the new server. _host repeats the important checks later when it actually writes the registration, but this early check avoids destructive work when refusal is already known.

*Call graph*: called by 2 (deploy_website, publish_website); 5 external calls (__init__, __init__, authority_member_id, site_name, site_url).


##### `_host`  (lines 787–831)

```
async def _host(ctx: ToolContext, raw_name: str, port: int, visibility: Visibility | None, manifest: str | None) -> dict[str, object]
```

**Purpose**: This registers a running sandbox port as a hosted site with a stable public link. It is the moment when a working server becomes a deliverable site.

**Data flow**: It receives a raw site name, port, optional visibility, and optional source manifest → confirms ownership and speaker rules, normalizes the name, builds the public URL, writes the hosted-site row, and returns the site name, effective visibility, object name, and public URL.

**Call relations**: deploy_website and publish_website call this after _serve has proved the server is running. _illustrate then uses the registered site to create previews.

*Call graph*: called by 2 (deploy_website, publish_website); 7 external calls (__init__, __init__, authority_member_id, effective_visibility, site_object_name, site_name, site_url).


##### `website`  (lines 834–843)

```
async def website(ctx: ToolContext, args: WebsiteInput) -> ToolResult
```

**Purpose**: This tool runs a build command for a website project in the sandbox and reports what files are present afterward. It is useful for turning source files into build output before serving or deploying.

**Data flow**: It receives a build command and optional project path → scopes the path to the workspace, runs the command there, lists the resulting directory entries → returns JSON with the project path and file names.

**Call relations**: This is a public tool handler. It does not host anything; it stops after building and reporting, using _json_result to return the response.

*Call graph*: calls 1 internal fn (_json_result); 2 external calls (quote, workspace_path).


##### `start_server`  (lines 846–866)

```
async def start_server(ctx: ToolContext, args: StartServerInput) -> ToolResult
```

**Purpose**: This tool starts a temporary server in the sandbox and returns a sandbox-local URL once it is actually listening. It is for previews and debugging, not for creating a permanent hosted site.

**Data flow**: It receives a project path, optional command, optional port, and optional log file → validates special UFO application-builder restrictions, chooses defaults, scopes paths safely, and calls _serve → returns JSON with the URL, port, log, and project path.

**Call relations**: This is a public tool handler. It delegates the difficult server lifecycle work to _serve and then formats the result with _json_result.

*Call graph*: calls 2 internal fn (_json_result, _serve); 1 external calls (workspace_path).


##### `_application_audit_attempts`  (lines 869–877)

```
async def _application_audit_attempts(ctx: ToolContext) -> int
```

**Purpose**: This reads how many product-audit attempts have already been used for the current turn. It enforces the rule that the UFO application builder only gets a limited number of audit tries.

**Data flow**: It receives the tool context → reads a stored counter keyed by the turn id → returns zero if missing, the stored integer if valid, or raises an error if the stored value is malformed.

**Call relations**: _audit_builder_application calls this before running the browser audit. It decides whether another audit attempt is still allowed.

*Call graph*: called by 1 (_audit_builder_application); 1 external calls (format).


##### `_application_audit_feedback`  (lines 880–892)

```
async def _application_audit_feedback(ctx: ToolContext, issues: tuple[ApplicationAuditIssue, ...], attempts: int) -> ApplicationAuditFeedback
```

**Purpose**: This records a failed audit attempt and turns audit issues into structured feedback. It tells the builder what to fix and how many attempts remain.

**Data flow**: It receives the context, a tuple of audit issues, and the previous attempt count → increments and stores the count → creates an ApplicationAuditFeedback object → returns that feedback.

**Call relations**: _audit_builder_application calls this whenever the audit cannot run, cannot be read, is invalid, or finds product problems. qa_ufo_application then returns that feedback to the builder.

*Call graph*: called by 1 (_audit_builder_application); 2 external calls (__init__, format).


##### `_accepted_application_design`  (lines 895–930)

```
async def _accepted_application_design(ctx: ToolContext) -> tuple[AcceptedApplicationDesignEvidence, str, str]
```

**Purpose**: This loads the previously accepted application design and its evidence, then proves they match. It prevents auditing or deploying against a design file that has been changed after acceptance.

**Data flow**: It receives the context → builds runtime paths for the accepted design and evidence files, reads both with size limits, parses the evidence JSON, hashes the design text, and compares the hash to the evidence → returns the evidence and both paths if valid.

**Call relations**: _audit_builder_application calls this before running the browser audit. The audit script receives these paths so it can compare the built app to the accepted design.

*Call graph*: called by 1 (_audit_builder_application); 4 external calls (model_validate_json, sha256, application_design_acceptance_relative, application_design_evidence_relative).


##### `_audit_builder_application`  (lines 933–1046)

```
async def _audit_builder_application(ctx: ToolContext, project: str) -> ApplicationAuditReport | ApplicationAuditFeedback
```

**Purpose**: This runs the full browser-based product audit for the special UFO application builder. It checks whether the app behaves and looks like the accepted design and contract require.

**Data flow**: It receives the context and project path → checks remaining attempts, prepares audit script and output paths, loads accepted design evidence, runs the Node audit script, reads and validates the report, compares it to the stored contract, and applies the audit rules → returns either a passing report or repair feedback.

**Call relations**: qa_ufo_application calls this when the builder asks for product QA. It uses _application_audit_attempts, _accepted_application_design, and _application_audit_feedback to control attempts and produce clear outcomes.

*Call graph*: calls 3 internal fn (_accepted_application_design, _application_audit_attempts, _application_audit_feedback); called by 1 (qa_ufo_application); 5 external calls (__init__, model_validate, model_validate_json, format, audit_application).


##### `_application_source_sha256`  (lines 1049–1055)

```
async def _application_source_sha256(ctx: ToolContext) -> str
```

**Purpose**: This computes a fingerprint of the UFO application source file. It is used to prove that the source has not changed after QA passed.

**Data flow**: It receives the context → reads the fixed application source file from the sandbox → hashes its text with SHA-256 → returns the hash string.

**Call relations**: qa_ufo_application stores this hash after a successful audit. _require_current_application_qa compares the current hash to the stored proof before allowing deployment.

*Call graph*: called by 2 (_require_current_application_qa, qa_ufo_application); 1 external calls (sha256).


##### `_require_current_application_qa`  (lines 1058–1070)

```
async def _require_current_application_qa(ctx: ToolContext) -> ApplicationQaProof
```

**Purpose**: This blocks UFO application deployment unless the current source file has already passed product QA. It prevents a builder from passing QA, changing the app, and deploying the untested version.

**Data flow**: It receives the context → reads stored QA proof for the turn, validates it, recomputes the current source hash, and compares the two → returns the proof if they match, or raises an error if proof is missing or stale.

**Call relations**: deploy_website calls this when the active profile is the UFO application builder. It sits between building and deployment as a safety gate.

*Call graph*: calls 1 internal fn (_application_source_sha256); called by 1 (deploy_website); 2 external calls (model_validate, format).


##### `qa_ufo_application`  (lines 1073–1115)

```
async def qa_ufo_application(ctx: ToolContext, args: QaUfoApplicationInput) -> ToolResult
```

**Purpose**: This public tool runs product QA for the UFO application builder profile. It either returns bounded repair feedback or records proof that the current source passed.

**Data flow**: It receives the context and empty QA input → confirms the caller is the right profile, checks and increments the QA call count, runs _audit_builder_application, and either returns feedback or stores a proof containing the source hash and audit batch count → returns JSON with the audit result summary.

**Call relations**: This is a profile-only tool handler. It calls _audit_builder_application for the main audit work, _application_source_sha256 for deploy proof, and _json_result to return the result.

*Call graph*: calls 3 internal fn (_application_source_sha256, _audit_builder_application, _json_result); 4 external calls (__init__, __init__, format, format).


##### `deploy_ufo_application`  (lines 1118–1128)

```
async def deploy_ufo_application(ctx: ToolContext, args: DeployUfoApplicationInput) -> ToolResult
```

**Purpose**: This public profile-only tool deploys the fixed UFO application scaffold after the required QA flow. It is a narrow wrapper around the normal static deploy path.

**Data flow**: It receives the context and an application site name → creates a DeployWebsiteInput pointing at the fixed scaffold with index.html as the entry point → calls deploy_website and returns its result.

**Call relations**: This tool is available only to the UFO application builder profile. It reuses deploy_website so all normal hosting, source storage, preview, and permission rules still apply.

*Call graph*: calls 1 internal fn (deploy_website); 1 external calls (__init__).


##### `deploy_website`  (lines 1131–1162)

```
async def deploy_website(ctx: ToolContext, args: DeployWebsiteInput) -> ToolResult
```

**Purpose**: This tool deploys a built static website folder to a permanent hosted link. Reusing the same site name updates the site behind the same link.

**Data flow**: It receives a project path, site name, entry point, and optional visibility → scopes and checks the path, applies special application-builder QA rules, chooses the serving port, detects homepage redeploy cases, checks hosting permission, prepares the served directory, uploads source, starts a static file server, registers the site, creates previews, and returns JSON with server and hosted-link details.

**Call relations**: This is the main static-site deploy handler. It coordinates _refuse_before_serving, _served_directory, _promote_source, _serve, _host, and _illustrate; deploy_ufo_application also calls it.

*Call graph*: calls 11 internal fn (_agent_homepage, _host, _illustrate, _json_result, _promote_source, _redeploy_homepage, _refuse_before_serving, _require_current_application_qa, _serve, _served_directory (+1 more)); called by 1 (deploy_ufo_application); 4 external calls (serve_port, workspace_path, site_object_name, site_name).


##### `_served_directory`  (lines 1165–1191)

```
async def _served_directory(ctx: ToolContext, project: str) -> tuple[str, dict[str, dict[str, object]]]
```

**Purpose**: This decides which directory should actually be hosted and returns its file listing. If the input is an editable UFO page source directory, it builds it first and hosts the generated dist folder instead.

**Data flow**: It receives a project directory → lists its files; if it does not contain the special source file, returns that directory and listing; if it does, writes build config, unpacks the page kit, runs the build, and lists the dist output → returns the final served directory and listing.

**Call relations**: deploy_website and _redeploy_homepage call this before uploading source and starting the server. It calls _source_listing and may call the page-kit unpacking helper for source-style projects.

*Call graph*: calls 1 internal fn (_source_listing); called by 2 (_redeploy_homepage, deploy_website); 2 external calls (quote, unpack_page_kit).


##### `_agent_homepage`  (lines 1194–1197)

```
async def _agent_homepage(ctx: ToolContext) -> HostedSite | None
```

**Purpose**: This finds the hosted site currently bound as the acting agent’s homepage, if there is one. It helps decide whether a deploy should update that homepage instead of creating a new site.

**Data flow**: It receives the context → opens the hosted-site registry → asks for the homepage bound to the current turn’s agent id → returns a HostedSite or None.

**Call relations**: deploy_website calls this early. If the requested deploy name points at a homepage from another conversation, deploy_website may route into _redeploy_homepage.

*Call graph*: calls 1 internal fn (_sites_registry); called by 1 (deploy_website).


##### `_sites_registry`  (lines 1200–1203)

```
def _sites_registry(ctx: ToolContext) -> HostedSites
```

**Purpose**: This creates the HostedSites store helper for the current workspace transaction. It is a small convenience function for reading and writing hosted-site records.

**Data flow**: It receives the context → checks that extension context is present → builds a HostedSites object using the workspace id and transaction → returns that registry helper.

**Call relations**: _agent_homepage, deploy_website, and _redeploy_homepage use this when they need to read or update hosted-site state.

*Call graph*: called by 3 (_agent_homepage, _redeploy_homepage, deploy_website); 1 external calls (__init__).


##### `_redeploy_homepage`  (lines 1206–1276)

```
async def _redeploy_homepage(ctx: ToolContext, args: DeployWebsiteInput, bound: HostedSite, scratch_port: int) -> ToolResult
```

**Purpose**: This updates an already-bound agent homepage in place from a new build, while keeping the same homepage link. It is used when a member edits an agent’s homepage from another conversation.

**Data flow**: It receives deploy arguments, the currently bound site, and a scratch port → verifies that a live member request allows the homepage change, rejects explicit visibility changes, checks permissions, prepares and uploads the new source under the existing site identity, serves it on the scratch port, updates the existing site row, unregisters any displaced scratch-port site, refreshes previews, and returns JSON with the unchanged public link.

**Call relations**: deploy_website calls this when it recognizes a deploy aimed at the acting agent’s bound homepage. It uses _served_directory, _promote_source, _serve, _sites_registry, _illustrate, and _json_result to perform the update.

*Call graph*: calls 7 internal fn (agent_visibility, _illustrate, _json_result, _promote_source, _serve, _served_directory, _sites_registry); called by 1 (deploy_website); 5 external calls (authority_member_id, workspace_path, format, site_object_name, site_url).


##### `publish_website`  (lines 1279–1296)

```
async def publish_website(ctx: ToolContext, args: PublishWebsiteInput) -> ToolResult
```

**Purpose**: This tool publishes a web app from the sandbox to a permanent hosted link. Unlike static deploy, it can run the app’s own server command and does not store the app source as the hosted source of record.

**Data flow**: It receives project, dist, app name, optional visibility, optional install command, and optional run command → chooses the serving port, checks hosting permission, optionally installs dependencies, chooses whether to run a backend command or a static file server, starts the server, registers the port as a hosted site without a source manifest, creates previews, and returns JSON.

**Call relations**: This is a public tool handler. It shares the same safety and hosting path as deploy_website through _refuse_before_serving, _serve, _host, _illustrate, and _json_result.

*Call graph*: calls 5 internal fn (_host, _illustrate, _json_result, _refuse_before_serving, _serve); 3 external calls (quote, serve_port, workspace_path).


##### `set_homepage`  (lines 1299–1357)

```
async def set_homepage(ctx: ToolContext, args: SetHomepageInput) -> ToolResult
```

**Purpose**: This tool binds an existing hosted site as the homepage for the target agent. It is careful because binding changes who may open the page: homepage access follows the agent’s visibility, not the site’s own visibility.

**Data flow**: It receives a site object name → loads the target agent, checks whether the caller may edit that agent, finds the hosted site by object name, confirms the acting member is the site creator, requires a live speaker unless the site was deployed in this same turn, writes the homepage pointer, and returns the homepage URL, visibility, and agent id.

**Call relations**: This is a public side-effecting tool bound to an agent instance. It talks directly to the HostedSites registry, checks speaker/admin authority through the context, and uses _json_result for the final response.

*Call graph*: calls 2 internal fn (speaker_is_admin, _json_result); 5 external calls (__init__, __init__, authority_member_id, site_object_name, site_url).


### `extensions/sites/ufo_ext_sites/application_audit.py`

`domain_logic` · `quality gate after browser measurement`

This file is the application quality gate. A browser has already opened the staged application and collected facts such as text contrast, page width, clipped content, visible regions, console errors, and whether controls actually change the page. This file gives that raw evidence a strict shape using Pydantic models, which are data objects that validate their fields when created, and then applies fixed checks to it.

The audit looks at four required views: light and dark colour schemes, each at desktop and narrow widths. It checks that those views exist, contain text, do not overflow sideways, do not clip or overlap content, and meet contrast rules. It also checks that the final application still matches the accepted design: named regions must exist, be large enough, stay visible when expected, and keep the same relative order on desktop. Finally, it verifies basic product behaviour: enough accessible controls, enough distinct interactions, no browser console errors, and any required facts from a contract are actually shown, including above the first screen on desktop.

Without this file, the builder could produce an application that looks fine in one screenshot but fails in dark mode, on a narrow screen, with assistive technology, or after interaction. The main public result is an `ApplicationAuditVerdict`: either no issues, meaning the app passed, or a bounded list of repair messages the builder can act on.

#### Function details

##### `AcceptedApplicationDesignEvidence.regions_are_unique`  (lines 131–137)

```
def regions_are_unique(self) -> 'AcceptedApplicationDesignEvidence'
```

**Purpose**: This validation step makes sure the accepted design evidence does not contain duplicate region names or duplicate Kit component names. It protects later comparison code from guessing which duplicate item is the real one.

**Data flow**: It reads the region names and Kit component names already stored in the design evidence object. If every name is unique, the object is accepted unchanged. If a duplicate is found, creation of the object fails with a clear error.

**Call relations**: This runs automatically when `AcceptedApplicationDesignEvidence` is created. It prepares trusted design evidence for later audit work, so downstream checks can compare regions by name without ambiguity.


##### `ApplicationAuditReport.views_are_unique`  (lines 202–206)

```
def views_are_unique(self) -> 'ApplicationAuditReport'
```

**Purpose**: This validation step makes sure an audit report has at most one measurement for each combination of colour scheme and viewport width. That matters because the audit expects one clear result for, for example, dark desktop or light narrow.

**Data flow**: It reads the scheme and width from every measured view in the report. If no pair is repeated, the report is accepted unchanged. If the same scheme-and-width pair appears twice, report creation fails.

**Call relations**: This runs automatically when an `ApplicationAuditReport` is created. Later, `audit_application` turns the views into a lookup table by scheme and width, and this validator prevents duplicate entries from silently overwriting one another.


##### `ApplicationAuditVerdict.passed`  (lines 227–230)

```
def passed(self) -> bool
```

**Purpose**: This property answers the simple question: did the application pass every deterministic check? It is true only when the verdict contains no repair issues.

**Data flow**: It reads the verdict's `issues` tuple. If the tuple is empty, it returns `True`; otherwise it returns `False`. It does not change the verdict.

**Call relations**: Code that receives an `ApplicationAuditVerdict` can call this property instead of inspecting the issue list itself. It is the final yes-or-no summary after `audit_application` has built the verdict.


##### `_needed_ratio`  (lines 277–282)

```
def _needed_ratio(item: ApplicationAuditText) -> float
```

**Purpose**: This helper decides the minimum contrast ratio required for one piece of rendered text. Contrast ratio is a number that describes how easy text is to distinguish from its background.

**Data flow**: It takes one text measurement, looks at whether it belongs to a quieter Kit slot, whether it is large, and whether it is bold. It returns the required contrast floor: a relaxed floor for approved quiet text, a lower accessibility floor for large text, or the normal body-text floor.

**Call relations**: `_contrast_failures` calls this for each measured text style. The returned threshold is then compared with the browser-measured contrast ratio to decide whether that text needs repair.

*Call graph*: called by 1 (_contrast_failures).


##### `_issue`  (lines 285–292)

```
def _issue(code: AuditIssueCode, message: str, terms: tuple[str, ...]=()) -> ApplicationAuditIssue
```

**Purpose**: This helper creates one audit issue while enforcing the audit's size limits. It keeps repair messages and search terms short enough to be safely returned to the builder.

**Data flow**: It receives an issue code, a message, and optional terms. It trims the message to the maximum allowed length and keeps only the first allowed terms. It returns an `ApplicationAuditIssue` object.

**Call relations**: `audit_application` calls this whenever it finds a problem, such as missing views, poor contrast, or failed interactions. The helper centralizes the formatting limits so each check does not have to repeat them.

*Call graph*: called by 1 (audit_application); 1 external calls (__init__).


##### `application_first_screen_scale`  (lines 295–305)

```
def application_first_screen_scale(page_height: int) -> float
```

**Purpose**: This function converts the fixed first-screen height of 844 pixels into a fraction of the measured page height. It lets region checks work correctly even when the full page is taller than the first screen.

**Data flow**: It receives the full measured page height. It divides the fixed first-screen height by that page height and returns the resulting scale factor. It does not read or change any audit report directly.

**Call relations**: `application_region_relation` and `application_design_region_size_failure` use this scale when comparing vertical sizes and gaps. It keeps pixel-based design rules consistent after regions have been stored as page fractions.

*Call graph*: called by 2 (application_design_region_size_failure, application_region_relation).


##### `application_region_relation`  (lines 308–328)

```
def application_region_relation(first: ApplicationAuditRegion, second: ApplicationAuditRegion, page_height: int=APPLICATION_DESIGN_FOLD) -> tuple[Literal['horizontal', 'vertical'], int] | None
```

**Purpose**: This function decides whether two visible regions are separated vertically or horizontally, and which one comes first. If they overlap too much to have a clear relationship, it returns no relation.

**Data flow**: It receives two regions and the page height their positions were measured against. It adjusts the vertical near-touch allowance using `application_first_screen_scale`, then compares top, height, left, and width values. It returns a pair such as vertical-before or horizontal-after, or `None` if the regions overlap.

**Call relations**: `application_design_fidelity` uses this twice: first to reject overlapping design regions, and later to check whether the application keeps the same ordering as the accepted design. It is the small geometric ruler behind the design-matching check.

*Call graph*: calls 1 internal fn (application_first_screen_scale); called by 1 (application_design_fidelity).


##### `application_design_region_size_failure`  (lines 331–349)

```
def application_design_region_size_failure(regions: tuple[ApplicationAuditRegion, ...], page_height: int=APPLICATION_DESIGN_FOLD) -> str | None
```

**Purpose**: This function finds the first design region that is too small to count as a meaningful screen section. It prevents tiny marks, decorative slivers, or accidental fragments from being treated as real design regions.

**Data flow**: It receives a group of regions and the page height. It scales height and area thresholds to match the first screen, then checks each region's width, height, and area. It returns a human-readable failure message for the first too-small region, or `None` if all regions are large enough.

**Call relations**: `application_design_fidelity` calls this before doing deeper design matching. If this function finds a size problem, the fidelity check stops early and reports that failure.

*Call graph*: calls 1 internal fn (application_first_screen_scale); called by 1 (application_design_fidelity).


##### `application_design_region_fold_failure`  (lines 352–374)

```
def application_design_region_fold_failure(regions: tuple[ApplicationAuditRegion, ...], page_height: int=APPLICATION_DESIGN_FOLD) -> str | None
```

**Purpose**: This function checks whether a design region crosses the first-screen boundary in a way the design rules reject. The “fold” is the bottom edge of the first visible screen before scrolling.

**Data flow**: It receives regions and a page height. For each region, it converts the stored fractional top and height back into pixel rows, then sees whether the region paints meaningfully both above and below the fold beyond a small tolerance. It returns a message for the first crossing region, or `None` if no region crosses the boundary.

**Call relations**: This function is a standalone design-rule helper in this file. It is not called by the listed audit flow here, but it expresses the same kind of deterministic region rule used by the design audit helpers.


##### `application_design_fidelity`  (lines 377–460)

```
def application_design_fidelity(report: ApplicationAuditReport) -> ApplicationDesignFidelity
```

**Purpose**: This function scores how closely the application keeps the accepted design's named regions. It checks region count, names, size, overlap, visibility above the fold, and desktop ordering in both light and dark modes.

**Data flow**: It receives the full audit report. It reads the accepted design regions, validates their basic shape, compares them with desktop application region measurements, and counts passed checks against total checks. It returns an `ApplicationDesignFidelity` object containing the score and any failure messages.

**Call relations**: `audit_application` calls this as the design portion of the audit. Inside, it uses `application_design_region_size_failure` to catch unusably small regions and `application_region_relation` to compare design and application layout order.

*Call graph*: calls 2 internal fn (application_design_region_size_failure, application_region_relation); called by 1 (audit_application); 1 external calls (__init__).


##### `_contrast_failures`  (lines 463–478)

```
def _contrast_failures(views: tuple[ApplicationAuditView, ...]) -> list[str]
```

**Purpose**: This helper gathers all text contrast failures from the measured views. It turns low-level text measurements into clear sentences that explain what text needs better foreground or background colours.

**Data flow**: It receives the measured views. For each text item, it asks `_needed_ratio` what contrast ratio is required, compares that with the measured ratio, and builds a failure message when the text falls short. It returns a list of those messages.

**Call relations**: `audit_application` calls this after it has selected the required measured views. The returned messages become one contrast repair issue if any text is too hard to read.

*Call graph*: calls 1 internal fn (_needed_ratio); called by 1 (audit_application).


##### `audit_application`  (lines 481–604)

```
def audit_application(report: ApplicationAuditReport, contract: ApplicationAuditContract | None=None) -> ApplicationAuditVerdict
```

**Purpose**: This is the main audit function. It takes one browser report, optionally compares it with a contract of required facts, and returns a verdict saying either the application passed or what must be repaired.

**Data flow**: It receives an `ApplicationAuditReport` and, optionally, an `ApplicationAuditContract`. It builds a lookup of measured views, checks for missing or empty views, contrast failures, overflow, clipping, overlaps, design mismatch, console errors, too few controls, too few successful interactions, missing required facts, and required facts that are not visible above the first desktop screen. Each discovered problem is converted into a bounded issue, and the function returns an `ApplicationAuditVerdict` containing at most the allowed number of issues.

**Call relations**: This function is the point where all the smaller checks come together. It calls `_contrast_failures` for readability checks, `application_design_fidelity` for design matching, and `_issue` to package each repair item before constructing the final verdict.

*Call graph*: calls 3 internal fn (_contrast_failures, _issue, application_design_fidelity); 1 external calls (__init__).


### `extensions/sites/ufo_ext_sites/source.py`

`io_transport` · `site source read, edit setup, and page build preparation`

A hosted site’s source code is not kept as one big folder inside the running app. It is stored as separate blobs, named by a manifest, which is like a packing list. This file reads that packing list, pulls the named files into a sandbox, and later helps move batches of files by using either web upload/download links or a local development blob store.

The important job is to make the sandbox copy trustworthy. Before downloading files, the code clears the target directory and “claims” each path through a containment guard. In plain terms, it checks that every file really stays inside the workspace and cannot sneak out through a dangerous path or a symbolic link, which is a shortcut file that can point somewhere else. Without this, a downloaded file name could overwrite something outside the intended site folder.

For app pages, the file also packages a local SDK kit into a compressed archive. That kit is unpacked into the sandbox beside the page source. This means an old edited app page can still build using the current components shipped with this extension, instead of being frozen to whatever existed when the page was first created.

A small generation stamp avoids repeated downloads. If the sandbox already has the source for the site’s current deploy generation, the file returns it as-is, which saves time and avoids disturbing edits in progress.

#### Function details

##### `_page_kit_archive`  (lines 101–113)

```
def _page_kit_archive() -> bytes
```

**Purpose**: This function packages the extension’s page SDK kit into one compressed tar archive. It exists to turn many small kit files into a single blob that can be written into the sandbox quickly.

**Data flow**: It reads files from the local kit directory in this Python package, adds each regular file to an in-memory compressed archive, and returns the archive as bytes. The result is stored for reuse so the kit does not need to be rebuilt every time a site is read.

**Call relations**: At module load time, this function prepares PAGE_KIT_ARCHIVE. Later, unpack_page_kit writes that prebuilt archive into the sandbox and unpacks it, so page builds receive the SDK kit with only one sandbox write plus one unpack step.

*Call graph*: 2 external calls (BytesIO, open).


##### `transfer`  (lines 120–130)

```
async def transfer(ctx: ToolContext, script: str, pairs: list[tuple[str, str]], total_bytes: int) -> None
```

**Purpose**: This function copies many files between the sandbox and blob storage using a small shell script. It batches the work so large source trees do not require one separate sandbox command per file.

**Data flow**: It receives a sandbox tool context, a transfer script, a list of file-and-URL pairs, and the total number of bytes expected. It calculates a timeout based on size, sends the pairs to the sandbox in batches, and raises an error if any batch command fails. It returns nothing when all transfers succeed.

**Call relations**: materialize_source calls this when the blob store can provide presigned download links, meaning temporary URLs the sandbox can fetch directly. transfer then hands the actual moving work to the sandbox shell, using the script supplied by its caller.

*Call graph*: called by 1 (materialize_source).


##### `materialize_source`  (lines 133–199)

```
async def materialize_source(ctx: ToolContext, site: HostedSite, object_name: str) -> tuple[str, list[str]]
```

**Purpose**: This function recreates a site’s stored source tree inside the current sandbox. Someone uses it when they need the latest deployed source available for editing, rebuilding, or redeploying.

**Data flow**: It takes a tool context, a hosted site record, and the object name that should become the sandbox folder name. It reads and validates the site’s source manifest, chooses a destination under the workspace, checks a generation stamp to avoid unnecessary work, safely clears and claims the target paths, then downloads or streams each stored file into place. Finally, it writes the deploy generation stamp and returns the destination path plus the list of relative files that were materialized.

**Call relations**: This is the main reader for stored site source. It uses SourceManifest.model_validate_json to understand the stored file list, workspace_path to choose safe workspace locations, shlex.quote when reading the stamp through the shell, and transfer when downloads can happen through presigned URLs. If the blob store is a local development-style store that does not support those URLs, it falls back to reading each blob through this process and writing it into the sandbox.

*Call graph*: calls 1 internal fn (transfer); 3 external calls (model_validate_json, quote, workspace_path).


##### `unpack_page_kit`  (lines 202–220)

```
async def unpack_page_kit(ctx: ToolContext, dest: str, runtime_root: str | None=None) -> None
```

**Purpose**: This function places the current page-building SDK kit into a sandbox project. It makes sure an app page builds against the extension’s current kit rather than an old copy captured when the page was first forked.

**Data flow**: It receives a tool context, a destination directory, and optionally a runtime root used for containment checks. It writes the prebuilt SDK archive into the destination, runs a guarded Python unpacking program inside the sandbox, and deletes the archive as part of unpacking. If unpacking fails, it raises an error; otherwise, the destination gains an sdk folder containing the kit files.

**Call relations**: This function is used by the page materialization/build preparation flow, although no direct caller is shown in the provided graph. It relies on the PAGE_KIT_ARCHIVE prepared by _page_kit_archive and hands the risky archive extraction step to a sandbox Python program that checks every path before writing it.


### Sandbox ingress serving
Sandbox ingress code creates signed browser routes, serves stored or live sandbox content, and reports unreachable sites for repair.

### `core/src/ufo/harness/sandbox/ingress_serve.py`

`entrypoint` · `startup and request handling`

This file is the front door for hosted sandbox sites. Think of it like a hotel receptionist: the hostname tells it which room you are trying to visit, the cookie proves you were invited, and then it either gives you a stored copy of the room’s contents or connects you to the live room.

A site is identified from its subdomain, which encodes a conversation and a port. A special view link first lands on a token path, where the server verifies the signed token and turns it into a short-lived host-only session cookie. After that, normal page, asset, API, and WebSocket requests are allowed only if that cookie matches the exact site.

There are two serving paths. Stored sites are static files recorded in a manifest and streamed from blob storage, with careful cache rules and content security headers. Live sites are reached by dialing the sandbox carrier and streaming the request and response without buffering the whole body. WebSockets are relayed in both directions for live apps such as development servers.

The file is also defensive. It strips dangerous headers, prevents sites from setting UFO-owned cookies, blocks cross-site WebSocket use, hides tokens from sandbox code, and replaces confusing upstream failures with clear user-facing pages.

#### Function details

##### `IngressServe.app`  (lines 368–400)

```
def app(self) -> FastAPI
```

**Purpose**: Builds the FastAPI web application and declares which URLs belong to ingress itself versus which should be forwarded to a hosted site. This is the routing map for HTTP and WebSocket traffic.

**Data flow**: It takes the already configured IngressServe object → creates a FastAPI application → attaches routes for token-opening paths, ordinary proxy paths, and WebSocket paths → returns the ready application for the web server to run.

**Call relations**: The process startup code calls this when handing the server to Uvicorn. The routes it installs lead later requests to _no_view_token, _open, _proxy, _no_socket_view, or _socket depending on the path and protocol.

*Call graph*: 1 external calls (FastAPI).


##### `IngressServe._no_view_token`  (lines 402–408)

```
async def _no_view_token(self, request: Request) -> Response
```

**Purpose**: Answers the special view-token path when no token was actually supplied. It prevents an empty or malformed view URL from being treated as a real site request.

**Data flow**: It receives an HTTP request → checks whether the method is GET or HEAD → returns either a 405 method refusal or a 403 plain-text message saying the link is not valid.

**Call relations**: IngressServe.app routes the bare view path here. This keeps token-opening separate from _open, so query-string tricks cannot smuggle in a token.

*Call graph*: 1 external calls (Response).


##### `IngressServe._open`  (lines 410–463)

```
async def _open(self, request: Request, view_path: str) -> Response
```

**Purpose**: Turns a signed one-time-style view link into a session cookie for the site’s own origin. This is how a browser becomes authorized to load the site and its assets.

**Data flow**: It receives the request and the path after the view prefix → extracts the token and optional landing path → identifies the addressed site from the hostname → verifies the token, site match, expiry, and allowed framer → mints a short-lived session token → sets it as a secure site cookie → redirects the browser to the requested site path.

**Call relations**: IngressServe.app sends token URLs here. It uses _site to identify the host and _framer_belongs when the token allows a sibling site to frame this one, then hands the browser back to normal site loading through _proxy.

*Call graph*: calls 2 internal fn (_framer_belongs, _site); 9 external calls (replace, now, RedirectResponse, Response, mint_ingress_token, verify_ingress_token, cookie_secure, set_session_cookie, quote).


##### `IngressServe._site`  (lines 465–476)

```
def _site(self, request: HTTPConnection) -> tuple[UUID, int] | None
```

**Purpose**: Reads the hostname and decides which site, if any, it names. It is the small parser that turns a signed subdomain label into a conversation ID and port.

**Data flow**: It receives an HTTP or WebSocket connection → compares its host with the configured base host → parses the subdomain label if it fits → returns the site identity or None if the host is outside this ingress or invalid.

**Call relations**: _open calls it before accepting a view token, and _authorized calls it before accepting normal HTTP or WebSocket traffic. That makes the hostname the source of truth for which site is being accessed.

*Call graph*: called by 2 (_authorized, _open); 1 external calls (parse_site_label).


##### `IngressServe._authorized`  (lines 478–500)

```
def _authorized(self, connection: HTTPConnection) -> IngressClaims | SiteRefusal
```

**Purpose**: Checks whether a request or WebSocket handshake is allowed to reach the site named by its host. It gives HTTP and WebSocket traffic the same front-door security check.

**Data flow**: It receives a connection → uses _site to find the addressed site → reads the ingress session cookie → verifies the signed session token and expiry → compares the token’s site to the hostname’s site → returns valid claims or a SiteRefusal explaining why access is denied.

**Call relations**: _proxy and _socket both call this before doing anything with stored files or live sandboxes. By sharing this function, both protocols use the same authorization rules.

*Call graph*: calls 1 internal fn (_site); called by 2 (_proxy, _socket); 3 external calls (__init__, now, verify_ingress_token).


##### `IngressServe._stored_manifest`  (lines 502–540)

```
async def _stored_manifest(self, claims: IngressClaims) -> dict[str, StoredFile] | None
```

**Purpose**: Finds out whether the authorized site is a stored static site and, if so, returns the list of files it contains. It also supports shipped application bundles that live in shared fleet storage.

**Data flow**: It receives verified ingress claims → if they name a shipped bundle, delegates to _shipped_manifest → otherwise reads the hosted-site row for that workspace, conversation, and port → parses the stored JSON manifest → returns a path-to-file map, or None if the site should be dialed live instead.

**Call relations**: _proxy calls it to decide between static serving and live proxying. _socket also calls it so static sites can refuse WebSockets instead of pretending to support them.

*Call graph*: calls 1 internal fn (_shipped_manifest); called by 2 (_proxy, _socket); 4 external calls (__init__, loads, select, workspace_tx).


##### `IngressServe._shipped_manifest`  (lines 542–574)

```
async def _shipped_manifest(self, shipped: ShippedClaim) -> dict[str, StoredFile] | None
```

**Purpose**: Builds or retrieves the file list for a deployed built-in app bundle. These files are shared deploy-wide rather than owned by one workspace’s sandbox.

**Data flow**: It receives a shipped-app claim with a digest and slug → checks an in-memory cache → lists blob entries under the app digest if needed → converts each entry into a StoredFile with size, guessed media type, and digest → caches and returns the manifest, or returns None if the bundle is gone.

**Call relations**: _stored_manifest calls this when claims point at shipped content. Later _serve_stored uses the returned file records to stream the actual bytes.

*Call graph*: called by 1 (_stored_manifest); 3 external calls (__init__, __init__, guess_type).


##### `IngressServe._dial_site`  (lines 576–606)

```
async def _dial_site(self, claims: IngressClaims) -> DialTarget | SiteRefusal
```

**Purpose**: Locates the live network address for a sandbox site that is not served from stored files. It turns a conversation and port into a dial target the proxy can contact.

**Data flow**: It receives authorized claims → reads the stored sandbox handle for the conversation → chooses the right carrier, including older resume carriers when needed → builds a sandbox handle → asks the carrier to dial the requested port → returns a DialTarget or a SiteRefusal saying the site is gone.

**Call relations**: _proxy calls it for live HTTP requests, and _socket calls it for live WebSocket handshakes. It uses _stored_handle to find the sandbox identity before talking to the carrier.

*Call graph*: calls 1 internal fn (_stored_handle); called by 2 (_proxy, _socket); 6 external calls (__init__, __init__, warn, sandbox_handle_backend, sandbox_handle_id, ws).


##### `IngressServe._proxy`  (lines 608–682)

```
async def _proxy(self, request: Request, path: str) -> Response
```

**Purpose**: Serves normal HTTP traffic for a hosted site. It is the main request handler for pages, assets, and API calls under a site hostname.

**Data flow**: It receives a request and requested path → authorizes the session → checks whether the site has stored files → either streams a stored file, returns a shipped 404 when needed, or dials the live sandbox → forwards sanitized headers and body upstream → streams the upstream response back while rewriting cache, security, cookie, and framing headers.

**Call relations**: IngressServe.app routes almost all HTTP site paths here. It coordinates _authorized, _stored_manifest, _serve_stored, _dial_site, _upstream_url, _upstream_headers, _body, _confined_cookie, _unframed_policy, _frame_ancestors, and _not_answering.

*Call graph*: calls 11 internal fn (_authorized, _body, _confined_cookie, _dial_site, _frame_ancestors, _not_answering, _serve_stored, _stored_manifest, _unframed_policy, _upstream_headers (+1 more)); 8 external calls (stream, Response, StreamingResponse, Request, BackgroundTask, log_error, warn, ws).


##### `IngressServe._not_answering`  (lines 684–711)

```
def _not_answering(self, request: HTTPConnection, claims: IngressClaims) -> Response
```

**Purpose**: Creates the user-facing response shown when a live sandbox site cannot be reached. For page loads, it shows a waiting page and asks the owning conversation to revive the site.

**Data flow**: It receives the request and authorized claims → builds the same frame-ancestor policy the site would have used → checks whether this request is a document load → returns either a plain 503 message or an HTML waiting page with a background report task.

**Call relations**: _proxy calls this when dialing or reading the upstream site fails, or when an upstream error status would be replaced by the edge. It uses _frame_ancestors so the fallback page can appear in the same frame as the real site.

*Call graph*: calls 1 internal fn (_frame_ancestors); called by 1 (_proxy); 2 external calls (Response, BackgroundTask).


##### `IngressServe._serve_stored`  (lines 713–779)

```
async def _serve_stored(self, request: Request, claims: IngressClaims, files: dict[str, StoredFile], path: str) -> Response
```

**Purpose**: Serves one file from a stored static site or shipped app bundle. It lets deployed site bytes be read directly from blob storage without starting or contacting the sandbox.

**Data flow**: It receives a request, claims, a file manifest, and a path → allows only GET and HEAD → maps the path to a file or index.html → prepares cache, ETag, content-type, and frame headers → honors matching If-None-Match with 304 → streams the blob bytes from fleet or workspace storage, or returns 404 if the blob vanished.

**Call relations**: _proxy calls this whenever _stored_manifest returns files. It uses _frame_ancestors for browser framing rules and _stored_body to stream after the first chunk has proved the blob exists.

*Call graph*: calls 2 internal fn (_frame_ancestors, _stored_body); called by 1 (_proxy); 5 external calls (__init__, __init__, Response, StreamingResponse, ws).


##### `IngressServe._stored_body`  (lines 781–787)

```
async def _stored_body(self, first: bytes, rest: AsyncIterator[bytes]) -> AsyncIterator[bytes]
```

**Purpose**: Streams a stored file after the first chunk has already been safely read. This avoids sending a successful response before discovering the blob is missing.

**Data flow**: It receives the first bytes and an async iterator for the remaining bytes → yields the first chunk → yields each later chunk unchanged → produces a byte stream for the HTTP response.

**Call relations**: _serve_stored calls this only after it has already read the first chunk. The resulting stream is passed into StreamingResponse so large files do not need to be loaded all at once.

*Call graph*: called by 1 (_serve_stored).


##### `IngressServe._framer_belongs`  (lines 789–824)

```
async def _framer_belongs(self, workspace_id: UUID, conversation_id: UUID, port: int) -> bool
```

**Purpose**: Checks whether a site named as an allowed framer belongs to the same workspace. This prevents a token from letting an unrelated site wrap another site in a frame.

**Data flow**: It receives a workspace, conversation, and port → looks for a matching hosted-site row → if not found, checks provisioned shipped apps in that workspace → returns true only when the requested framer is a known site or shipped app for that workspace.

**Call relations**: _open calls this while validating a view token that includes a sibling framer. If it returns false, _open rejects the link instead of setting a session cookie.

*Call graph*: called by 1 (_open); 5 external calls (select, workspace_tx, serve_port, shipped_anchor, shipped_app_slug).


##### `IngressServe._frame_ancestors`  (lines 826–834)

```
def _frame_ancestors(self, claims: IngressClaims) -> str
```

**Purpose**: Builds the browser security rule that says who may embed this site in a frame. This is important because all site labels share one parent domain, so sibling sites need explicit limits.

**Data flow**: It receives ingress claims → starts with the configured app origin or 'none' → if the claims name an allowed framer, adds that exact sibling site origin → returns the source list for a Content-Security-Policy frame-ancestors directive.

**Call relations**: _proxy, _serve_stored, and _not_answering call this when writing response headers. It uses site_label to render the sibling site origin in the same hostname format ingress serves.

*Call graph*: called by 3 (_not_answering, _proxy, _serve_stored); 1 external calls (site_label).


##### `IngressServe._upstream_url`  (lines 836–839)

```
def _upstream_url(self, scheme: str, host: str, path: str, query_string: bytes) -> str
```

**Purpose**: Builds the exact URL used to contact a live sandbox site. It preserves the requested path and query string while safely escaping the path.

**Data flow**: It receives a scheme, host, path, and raw query string → quotes unsafe path characters but leaves normal URL path characters alone → appends the decoded query string if present → returns the upstream URL.

**Call relations**: _proxy uses it for live HTTP forwarding, and _socket uses it for WebSocket forwarding. It is the bridge from public site URL to private sandbox address.

*Call graph*: called by 2 (_proxy, _socket); 1 external calls (quote).


##### `IngressServe._stored_handle`  (lines 841–851)

```
async def _stored_handle(self, workspace_id: UUID, conversation_id: UUID) -> str | None
```

**Purpose**: Reads the saved sandbox handle for a conversation. The handle tells the carrier which running or resumable sandbox container belongs to that conversation.

**Data flow**: It receives a workspace ID and conversation ID → queries the conversation table inside a workspace transaction → returns the sandbox_handle value, or None if no matching conversation exists.

**Call relations**: _dial_site calls this before asking a carrier to connect to a live port. Without a handle, _dial_site cannot safely know which sandbox to dial.

*Call graph*: called by 1 (_dial_site); 2 external calls (select, workspace_tx).


##### `IngressServe._upstream_headers`  (lines 853–887)

```
def _upstream_headers(self, request: HTTPConnection, dial_headers: Mapping[str, str]) -> list[tuple[str, str]]
```

**Purpose**: Creates the headers that a live sandbox server is allowed to see. It removes proxy-only, handshake-only, host, and UFO session cookie data, then adds carrier-supplied dial headers.

**Data flow**: It receives the viewer connection and dial headers → walks through the viewer’s headers → drops unsafe or inappropriate names → removes the ingress session cookie from Cookie headers while keeping site-owned cookies → appends the dial headers → returns a clean list of headers for the upstream request or WebSocket.

**Call relations**: _proxy and _socket call this before contacting live sandbox servers. It protects the ingress session token from agent-authored code and avoids inventing headers the viewer did not send.

*Call graph*: called by 2 (_proxy, _socket).


##### `IngressServe._unframed_policy`  (lines 889–901)

```
def _unframed_policy(self, policy: str) -> str
```

**Purpose**: Removes a site’s own frame-ancestors rule from a Content Security Policy while preserving the rest. This lets core decide framing rules without stripping the site’s other browser protections.

**Data flow**: It receives one Content-Security-Policy header string → splits it into directives → filters out any frame-ancestors directive → joins the remaining directives → returns the rewritten policy, or an empty string if nothing remains.

**Call relations**: _proxy calls this while copying live upstream response headers. It pairs with _frame_ancestors, which writes the authoritative framing rule.

*Call graph*: called by 1 (_proxy).


##### `IngressServe._confined_cookie`  (lines 903–930)

```
def _confined_cookie(self, header: str) -> str | None
```

**Purpose**: Rewrites or rejects a Set-Cookie header from a hosted site so the cookie cannot escape that exact site origin. It also blocks cookies in UFO’s reserved namespace.

**Data flow**: It receives a raw Set-Cookie header → parses the cookie name → drops nameless cookies, malformed cookies, and names starting with the reserved UFO prefix → removes any Domain attribute → returns the confined header or None to suppress it.

**Call relations**: _proxy calls this for every Set-Cookie header from a live upstream response. Accepted cookies are then appended to the response sent to the browser.

*Call graph*: called by 1 (_proxy).


##### `IngressServe._body`  (lines 932–942)

```
async def _body(self, upstream: httpx.Response) -> AsyncIterator[bytes]
```

**Purpose**: Streams raw bytes from a live upstream HTTP response and guarantees the upstream connection is closed afterward. This prevents leaked pooled connections when a stream ends early or fails.

**Data flow**: It receives an httpx upstream response → yields each raw response chunk as it arrives → always closes the upstream response in a finally step → produces an async byte stream for the browser response.

**Call relations**: _proxy wraps this in a StreamingResponse for live HTTP traffic. It works alongside the response background close, covering both normal completion and mid-stream failure.

*Call graph*: called by 1 (_proxy); 2 external calls (aclose, aiter_raw).


##### `IngressServe._no_socket_view`  (lines 944–950)

```
async def _no_socket_view(self, websocket: WebSocket) -> None
```

**Purpose**: Rejects WebSocket attempts to the special token-opening path. That path is only for HTTP token exchange, not for sandbox code to receive credentials.

**Data flow**: It receives a WebSocket handshake → creates a refusal saying the link is not valid → sends that refusal as an HTTP denial response instead of accepting the socket.

**Call relations**: IngressServe.app routes WebSocket handshakes on the view path here. It delegates the actual denial response formatting to _refuse.

*Call graph*: calls 1 internal fn (_refuse); 1 external calls (__init__).


##### `IngressServe._socket`  (lines 952–1008)

```
async def _socket(self, websocket: WebSocket, path: str) -> None
```

**Purpose**: Relays a WebSocket between the browser and a live sandbox site. This supports site features that need two-way live communication, such as hot reload or push updates.

**Data flow**: It receives a WebSocket and path → checks the Origin header is the same host → authorizes the session → refuses static or shipped sites → dials the live sandbox → opens an upstream WebSocket with safe headers and offered subprotocols → accepts the viewer socket only after upstream accepts → relays messages until one side closes or fails.

**Call relations**: IngressServe.app routes catch-all WebSocket traffic here. It coordinates _same_origin, _authorized, _stored_manifest, _dial_site, _upstream_url, _upstream_headers, _refuse, _relay, and _end.

*Call graph*: calls 9 internal fn (_authorized, _dial_site, _end, _refuse, _relay, _same_origin, _stored_manifest, _upstream_headers, _upstream_url); 6 external calls (__init__, accept, log_error, ws, connect, Subprotocol).


##### `IngressServe._same_origin`  (lines 1010–1022)

```
def _same_origin(self, websocket: WebSocket) -> bool
```

**Purpose**: Checks that a WebSocket was opened by the same site host it is trying to reach. This closes a gap where browser CORS rules do not protect WebSocket handshakes.

**Data flow**: It receives a WebSocket handshake → reads the Origin header → compares the origin hostname with the requested WebSocket hostname → returns true only when they match and the Origin is present.

**Call relations**: _socket calls this before session authorization or dialing. If it fails, _socket refuses the handshake rather than creating a cross-site channel.

*Call graph*: called by 1 (_socket); 1 external calls (urlsplit).


##### `IngressServe._refuse`  (lines 1024–1037)

```
async def _refuse(self, websocket: WebSocket, refusal: SiteRefusal) -> None
```

**Purpose**: Turns a SiteRefusal into a WebSocket handshake denial response. This lets denied WebSocket attempts receive the same status and body an HTTP request would have received.

**Data flow**: It receives a WebSocket and refusal object → builds a Response with the refusal’s message, status, media type, and no-store cache header → sends it as a denial response without accepting the socket.

**Call relations**: _no_socket_view and _socket call this whenever a WebSocket must not be opened. It is the shared exit door for failed WebSocket handshakes.

*Call graph*: called by 2 (_no_socket_view, _socket); 2 external calls (send_denial_response, Response).


##### `IngressServe._relay`  (lines 1039–1056)

```
async def _relay(self, viewer: WebSocket, upstream: ClientConnection) -> None
```

**Purpose**: Runs the two halves of a WebSocket relay at the same time. When either side finishes, it stops the other side too.

**Data flow**: It receives the viewer WebSocket and upstream connection → starts one task for viewer-to-site messages and one for site-to-viewer messages → waits until the first task completes → cancels the other task → raises any real failure from the completed side.

**Call relations**: _socket calls this after both WebSockets are open. It delegates direction-specific message copying to _viewer_to_site and _site_to_viewer.

*Call graph*: calls 2 internal fn (_site_to_viewer, _viewer_to_site); called by 1 (_socket); 3 external calls (create_task, gather, wait).


##### `IngressServe._viewer_to_site`  (lines 1058–1067)

```
async def _viewer_to_site(self, viewer: WebSocket, upstream: ClientConnection) -> None
```

**Purpose**: Copies WebSocket messages from the browser to the site. It preserves whether each message is text or binary because that can matter to the site protocol.

**Data flow**: It receives the viewer socket and upstream socket → repeatedly reads viewer messages → stops on viewer disconnect → sends text messages as text and byte messages as bytes to the upstream site.

**Call relations**: _relay starts this as one of its two concurrent tasks. It is cancelled when the site-to-viewer direction finishes first.

*Call graph*: called by 1 (_relay); 2 external calls (receive, send).


##### `IngressServe._site_to_viewer`  (lines 1069–1082)

```
async def _site_to_viewer(self, upstream: ClientConnection, viewer: WebSocket) -> None
```

**Purpose**: Copies WebSocket messages from the site back to the browser and then closes the browser side with an appropriate close code. It avoids sending close codes that the WebSocket standard forbids on the wire.

**Data flow**: It receives the upstream socket and viewer socket → reads each upstream message → sends strings as text and bytes as binary → when upstream closes, chooses a safe close code and reason → asks _end to close the viewer socket.

**Call relations**: _relay starts this as the other concurrent task. It calls _end for the final close and may be cancelled if the viewer-to-site direction ends first.

*Call graph*: calls 1 internal fn (_end); called by 1 (_relay); 3 external calls (suppress, send_bytes, send_text).


##### `IngressServe._end`  (lines 1084–1094)

```
async def _end(self, viewer: WebSocket, code: int, reason: str) -> None
```

**Purpose**: Closes the viewer WebSocket without letting close-time errors hide the original problem. It treats an already-gone browser as a normal terminal state.

**Data flow**: It receives a viewer socket, close code, and reason → attempts to close the socket → suppresses any exception raised because the connection is already gone → returns nothing.

**Call relations**: _site_to_viewer calls it after the upstream socket closes, and _socket calls it after relay failures. It is the safe final cleanup step for WebSocket handling.

*Call graph*: called by 2 (_site_to_viewer, _socket); 2 external calls (suppress, close).


##### `ingress_base_host`  (lines 1097–1108)

```
def ingress_base_host(configured: str | None) -> str
```

**Purpose**: Extracts the wildcard base hostname that all served site subdomains must sit under. It refuses startup if that public ingress URL is missing or malformed.

**Data flow**: It receives the configured ingress public URL or None → parses out the hostname → returns the hostname when present → raises a runtime error when no usable host exists.

**Call relations**: run calls this while building IngressServe. The result is later used by _site to decide whether a request host belongs to this ingress.

*Call graph*: called by 1 (run); 1 external calls (urlsplit).


##### `ingress_frame_ancestor`  (lines 1111–1121)

```
def ingress_frame_ancestor(configured: str | None) -> str
```

**Purpose**: Builds the deploy-wide browser framing source for hosted sites from the main app’s public URL. If no app URL is configured, it returns a rule that allows no framing.

**Data flow**: It receives the configured app public base URL or None → parses scheme, host, and optional port → returns an origin string such as https://example.com, or 'none' if the URL is not usable.

**Call relations**: run calls this during startup and stores the result on IngressServe. Later _frame_ancestors uses it as the base framing permission on every served response.

*Call graph*: called by 1 (run); 1 external calls (urlsplit).


##### `upstream_client`  (lines 1124–1147)

```
def upstream_client() -> httpx.AsyncClient
```

**Purpose**: Creates the shared HTTP client used to contact live sandbox sites. It is deliberately configured not to remember or replay cookies between sites.

**Data flow**: It creates timeout and connection-limit settings → creates a cookie jar whose policy accepts no domains → builds and returns an httpx AsyncClient using those limits and cookie rules.

**Call relations**: run creates this once and passes it into IngressServe and SiteReporter. _proxy later uses the client to send live HTTP requests.

*Call graph*: called by 1 (run); 4 external calls (CookieJar, DefaultCookiePolicy, AsyncClient, Limits).


##### `run`  (lines 1150–1179)

```
def run() -> None
```

**Purpose**: Starts the ingress server process. It loads configuration, prepares dependencies, constructs IngressServe, and hands its app to Uvicorn.

**Data flow**: It loads config → initializes observability and database access → verifies the database and ingress secret → loads extension manifests and selects sandbox carriers → creates the HTTP client, blob store, reporter, and IngressServe → logs startup → runs Uvicorn on the configured port with WebSocket size and compression settings.

**Call relations**: This is the top-level entry function for the file. It calls ingress_base_host, ingress_frame_ancestor, upstream_client, and many setup helpers before the request-time routes in IngressServe.app become active.

*Call graph*: calls 3 internal fn (ingress_base_host, ingress_frame_ancestor, upstream_client); 15 external calls (__init__, __init__, run, blob_store_for, load_config, init_db, verify_db_reachable, init_o11y, log, ingress_secret (+5 more)).


### `core/src/ufo/harness/sandbox/ingress_host.py`

`domain_logic` · `site provisioning and request handling`

A hosted sandbox site needs its own web origin, meaning its own hostname, so that browser cookies, storage, and root-relative links stay separate from every other site. This file is the address-making tool for that. It takes a conversation identity and the port where that conversation’s sandbox should be served, packs them into bytes, signs them with a shared deploy secret, and encodes the result as a DNS-safe label.

The signature is not the main permission check. It is more like a tamper-evident seal on an envelope: it proves the label was minted by this deployment, but later token and cookie checks still decide whether a visitor may enter. This matters because a guessed hostname should be rejected before the system even reads conversation data or contacts a sandbox.

The file also makes sure there is exactly one spelling for each site label. Base32 encoding can leave unused bits, which could otherwise allow several different-looking labels to decode to the same bytes. Browsers would treat those as different origins, splitting cookies and storage. To prevent that, parsing decodes the label, re-encodes it, and rejects anything that is not the canonical lowercase spelling.

Besides labels for conversation-backed sites, it includes helpers for shipped app pages: finding a stable app slug and creating a synthetic UUID anchor when no conversation row exists.

#### Function details

##### `serve_port`  (lines 57–63)

```
def serve_port(conversation_id: UUID) -> int
```

**Purpose**: Chooses the stable local port assigned to a conversation’s hosted sandbox site. This keeps the same conversation on the same port without needing to store a separate port number anywhere.

**Data flow**: It receives a conversation UUID → turns the UUID’s large integer value into a number inside a fixed port range → returns a valid application port between the configured floor and span.

**Call relations**: Other parts of the sandbox hosting flow can call this when they need to know where a conversation’s site should live. By deriving the port from the conversation ID, every caller gets the same answer without coordinating through storage.


##### `shipped_app_slug`  (lines 66–74)

```
def shipped_app_slug(provisioned_by: str | None) -> str | None
```

**Purpose**: Extracts the stable page slug from a shipped app’s provision name. It uses the extension’s identity rather than a user-visible name, because visible names can change or gain suffixes after collisions.

**Data flow**: It receives a provision name string, or nothing → if there is no name, it returns nothing → if the name exactly matches the expected pattern like `app_something`, it returns the `something` part → otherwise it returns nothing.

**Call relations**: This is used when the system needs a dependable identifier for a shipped app page. It prepares the slug that can later be used to build stable origins or bundle paths.


##### `shipped_anchor`  (lines 77–82)

```
def shipped_anchor(workspace_id: UUID, slug: str) -> UUID
```

**Purpose**: Creates a stable synthetic UUID for a shipped app page inside one workspace. This gives a shipped page its own browser cookies and storage even though it is not backed by a normal conversation row.

**Data flow**: It receives a workspace UUID and an app slug → combines them with a fixed label into one namespace string → uses UUID version 5, which deterministically creates the same UUID from the same text → returns that UUID.

**Call relations**: When a shipped app page needs an origin-like identity, this function supplies it. It calls the standard UUID generator for name-based UUIDs so the same workspace and slug always produce the same anchor.

*Call graph*: 1 external calls (uuid5).


##### `site_label`  (lines 85–90)

```
def site_label(conversation_id: UUID, port: int) -> str
```

**Purpose**: Builds the DNS label for one conversation’s sandbox port. Someone uses it when creating the hostname that a browser will visit for that hosted site.

**Data flow**: It receives a conversation UUID and a port number → first rejects ports outside the normal addressable TCP port range → joins the conversation bytes and two port bytes into an address → signs that address → base32-encodes the address plus signature → returns a short lowercase DNS-safe label.

**Call relations**: This is the minting side of the label flow. It relies on `_signature` to add the tamper-evident seal and `_encode` to turn raw bytes into a hostname-friendly string. Later, `parse_site_label` performs the reverse check when a request arrives.

*Call graph*: calls 2 internal fn (_encode, _signature).


##### `parse_site_label`  (lines 93–105)

```
def parse_site_label(label: str) -> tuple[UUID, int]
```

**Purpose**: Checks a DNS label and recovers the conversation UUID and port it names. It rejects labels that are malformed, not signed by this deployment, or not written in the one allowed canonical spelling.

**Data flow**: It receives a label string from a hostname → base32-decodes it while accepting DNS-style case differences → re-encodes the bytes and compares that to the lowercase label to catch alternate spellings → splits the bytes into address and signature → recomputes the expected signature and compares it safely → returns the UUID and port if everything matches, or raises `SiteLabelError` if anything is wrong.

**Call relations**: This is the checking side of the label flow, used before trusting a hostname as pointing at a sandbox site. It calls `_encode` to enforce canonical spelling, `_signature` to verify the seal, and standard library tools for base32 decoding, constant-time signature comparison, and UUID reconstruction.

*Call graph*: calls 2 internal fn (_encode, _signature); 4 external calls (__init__, b32decode, compare_digest, UUID).


##### `_encode`  (lines 108–109)

```
def _encode(raw: bytes) -> str
```

**Purpose**: Turns raw label bytes into the lowercase base32 text used inside DNS names. It is a small shared helper so label creation and label verification spell bytes the same way.

**Data flow**: It receives bytes → base32-encodes them → removes padding characters that are not needed in the DNS label → lowercases the result → returns the final text.

**Call relations**: Both `site_label` and `parse_site_label` call this. During creation it produces the label, and during parsing it proves the incoming label is the exact canonical spelling of the decoded bytes.

*Call graph*: called by 2 (parse_site_label, site_label); 1 external calls (b32encode).


##### `_signature`  (lines 112–114)

```
def _signature(address: bytes) -> bytes
```

**Purpose**: Creates the short cryptographic signature attached to a site address. The signature proves that the conversation-and-port bytes were produced with this deployment’s secret, not invented by a random caller.

**Data flow**: It receives the raw address bytes → reads the ingress secret for this deployment → combines a fixed label with the address and hashes it using HMAC-SHA256, a standard keyed hash used to detect tampering → keeps only the first four bytes → returns those signature bytes.

**Call relations**: This helper is used by `site_label` when minting a label and by `parse_site_label` when checking one. Because both sides call the same function, a label is accepted only if its embedded signature matches what this deployment would have produced.

*Call graph*: called by 2 (parse_site_label, site_label); 2 external calls (new, ingress_secret).


### `core/src/ufo/harness/sandbox/ingress_url.py`

`io_transport` · `when creating browser access links for sandbox ports`

A sandbox may run a web app on an internal port, but a user needs a normal browser link to open it. This file turns the project’s public ingress address into that link. Think of it like writing a temporary visitor badge: it says which workspace, which conversation, which port, and when access ends.

The main function first checks whether a public ingress base URL exists. If not, there is no browser link to make. It then creates a signed ingress token, which is a tamper-resistant string containing the routing details and expiry time. If the sandbox content came from a shipped artifact, it can also include that artifact’s slug and digest so the receiving side knows exactly what was published.

The file also supports a framing case, where one sandbox view is opened from inside another. The helper checks whether the referring page is on the same ingress domain and has a valid sandbox site label. If so, it records that parent sandbox as a framer claim. This lets later code understand the relationship without trusting arbitrary outside websites.

Finally, the URL is assembled as a subdomain based on the conversation and port, followed by the ingress token and the requested entry path.

#### Function details

##### `mint_ingress_view_url`  (lines 17–50)

```
def mint_ingress_view_url(public_url: str | None, workspace_id: UUID, conversation_id: UUID, port: int, entry_path: str, *, framed_from: str | None=None, shipped_slug: str | None=None, shipped_digest:
```

**Purpose**: Creates the temporary public URL that a browser can use to open one sandbox port. It packages the workspace, conversation, port, expiry time, optional shipped-artifact details, and optional framing information into a signed token.

**Data flow**: It receives a base public URL, workspace and conversation IDs, a sandbox port, and the path the user should open. If there is no base URL, it returns nothing. Otherwise it parses the base URL, builds optional shipped-artifact information, asks `_framer_claim` whether the page was opened from another trusted sandbox view, creates signed ingress claims, turns the conversation and port into a subdomain label, safely quotes the path, and returns the final browser URL string.

**Call relations**: This is the file’s public workhorse. Code that needs to show or hand out a sandbox browser link calls it. During URL creation it delegates the parent-frame check to `_framer_claim`, uses ingress token code to mint the signed token, and uses ingress host code to create the subdomain label that routes the request to the right sandbox port.

*Call graph*: calls 1 internal fn (_framer_claim); 7 external calls (__init__, __init__, now, site_label, mint_ingress_token, quote, urlsplit).


##### `_framer_claim`  (lines 53–73)

```
def _framer_claim(base: SplitResult, framed_from: str | None) -> FramerClaim | None
```

**Purpose**: Figures out whether a sandbox view was opened from another trusted sandbox view, and if so records which conversation and port that parent view belongs to. This prevents the system from accepting framing information from unrelated or malicious websites.

**Data flow**: It receives the parsed public ingress base URL and an optional `framed_from` URL. It parses the `framed_from` value, compares its scheme, port, and host against the trusted base domain, and rejects it if it does not look like a matching sandbox subdomain. If the subdomain label can be decoded into a conversation ID and port, it returns a `FramerClaim`; otherwise it returns nothing.

**Call relations**: It is called only by `mint_ingress_view_url` while that function is building the signed ingress token. Its result is folded into the token claims, so later ingress code can know about a trusted parent sandbox frame without having to re-check the original referring URL.

*Call graph*: called by 1 (mint_ingress_view_url); 3 external calls (__init__, parse_site_label, urlsplit).


### `core/src/ufo/harness/sandbox/site_report.py`

`io_transport` · `request handling when a hosted sandbox site fails to answer`

A hosted site runs inside a sandbox, while the ingress is the front door that browser traffic reaches first. If the ingress sees that nothing is listening on the site’s port, it cannot fix the problem itself: it is a proxy, not the conversation engine. This file defines the small bridge between those two worlds.

On the ingress side, `SiteReporter` creates a short-lived signed token saying, in effect, “for this workspace, this conversation, and this port, the site did not answer.” It posts that token to an internal endpoint on the serve process. If posting fails, it logs a warning and stops; the browser’s waiting page will reload and try again later.

On the serve side, `SiteReports` exposes that internal endpoint. It verifies the signed token, finds the conversation’s agent, and asks that agent to investigate and restart the site. The report is not treated as a user action. It uses workspace authority instead, because the browser visitor may not be the person who built the page.

A key detail is idempotency: repeated reloads should not create a flood of duplicate repair turns. The file groups reports into fixed time buckets, so many reloads during the same bucket become one agent invocation. Like a doorbell that only rings once every few minutes no matter how often it is pressed, this keeps the repair signal useful instead of noisy.

#### Function details

##### `SiteReporter.report`  (lines 74–105)

```
async def report(self, claims: IngressClaims) -> None
```

**Purpose**: This is the ingress-side sender. When the ingress has already decided that a conversation’s hosted site is down, this function creates a short-lived internal report and sends it to the serve process.

**Data flow**: It receives ingress claims, which identify the workspace, conversation, port, and related access information. If reporting is disabled or the request is for a shipped site rather than a live conversation sandbox, it does nothing. Otherwise it copies the claims, sets a near-future expiry time, removes fields that should not travel on this report, signs them into a token, and posts that token to the internal report endpoint. Nothing is returned. If the HTTP request fails or serve rejects it, the function writes a warning and leaves the next page reload to try again.

**Call relations**: This function is used by the ingress after it has served the member a waiting page because the site did not answer. It relies on the token-minting helper to make the report trustworthy, uses the current time to limit how long the report is valid, and uses warning logging when the hop to serve fails or is refused.

*Call graph*: 4 external calls (replace, now, warn, mint_ingress_token).


##### `SiteReports.router`  (lines 125–128)

```
def router(self) -> APIRouter
```

**Purpose**: This builds the small FastAPI router that serve mounts so it can receive site-down reports from the ingress. A router is a bundle of web routes, like a small address book for HTTP requests.

**Data flow**: It creates a new router, attaches the internal site-report path as a POST endpoint, points that endpoint at `SiteReports._report`, and returns the router to whoever is assembling the web application.

**Call relations**: This is the setup step for the serve-side half of the bridge. During application wiring, serve calls it to expose the endpoint that `SiteReporter.report` posts to later.

*Call graph*: 1 external calls (APIRouter).


##### `SiteReports._report`  (lines 130–154)

```
async def _report(self, authorization: Annotated[str, Header()]='') -> Response
```

**Purpose**: This is the serve-side receiver for a site-down report. It checks that the report is authentic, finds the right conversation agent, and asks that agent to repair the non-answering site.

**Data flow**: It reads the HTTP `Authorization` header, removes the `Bearer` prefix, and verifies the signed ingress token using the current time and the special site-report token kind. If verification fails, it rejects the request with an unauthorized error. If the token is valid, it enters the named workspace, looks up the agent for the reported conversation, and returns 404 if that conversation is not present. If an agent exists, it builds a time-bucketed idempotency key so repeated reloads in the same period count as the same report, then invokes the agent with a plain message saying which port stopped answering. If the agent has been archived, it treats the report as harmless and returns success. In normal successful cases it returns an empty 204 response.

**Call relations**: This function is called by FastAPI when the internal report endpoint receives a POST. It hands token checking to the ingress-token verifier, uses the workspace context while looking up and invoking the conversation’s agent, and gives the invocation workspace-level authority rather than acting as a particular member. The idempotency key it creates is what lets the broader turn-invocation system fold many identical reports into one repair turn.

*Call graph*: 7 external calls (now, HTTPException, Response, verify_ingress_token, authority_from_member_id, conversation_agent_id, ws).


### Conversation homepage bindings
Conversation and workspace bindings expose owned sites in chat views and retire seeded homepages when the built-in chat homepage should take over.

### `extensions/sites/ufo_ext_sites/conversation_slot.py`

`orchestration` · `request handling`

This file is the bridge between stored hosted sites and the conversation sidebar or slot that lists them. A “slot” here means a small named section of conversation data, like a shelf in the interface. Without this file, the system might still have hosted sites in storage, but the conversation view would not know how to count them or safely show their links.

The main exported piece is `SITES_SLOT`, a `ConversationSlotProvider`. It gives the slot an id, label, icon, payload type, and two callback functions: one to summarize the slot and one to read its full contents.

The careful part is authorization. The code does not simply return every site attached to the conversation. First, it looks at `ctx.visible_items`, which are the conversation objects the current viewer is allowed to see. It converts those object names into site names, then asks `HostedSites` for matching site rows. After that, it checks the returned rows against both the expected site name and its generation number. That generation check helps ensure the visible authorization object still matches the stored site version, like checking that a ticket is not only for the right event but also for the right date.

Finally, it builds public URLs for the allowed sites, caps the result at `CONVERSATION_SITES_MAX`, and marks the payload as truncated if there were more sites than can be shown.

#### Function details

##### `_read`  (lines 13–46)

```
async def _read(ctx: ConversationSlotContext) -> SitesSlotPayload
```

**Purpose**: This function returns the actual list of sites that should appear in the conversation’s “Sites” slot. It is careful to include only sites backed by visible authorization objects, so a viewer does not see site links they are not allowed to see.

**Data flow**: It receives a conversation slot context, which includes the conversation id, visible conversation items, workspace/store information, transaction, and public base URL. It turns visible item names into expected site names, reads matching rows from `HostedSites`, filters out any row whose authorization object or generation does not match, then turns the remaining rows into `ConversationSite` entries with public URLs. It returns a `SitesSlotPayload` containing at most the configured maximum number of sites, plus a flag saying whether extra authorized sites were left out.

**Call relations**: When the `SITES_SLOT` provider needs full content for the slot, it uses this function as its reader. Inside that flow, `_read` asks `site_name_from_object` to interpret visible authorization objects, uses `HostedSites` to fetch stored site records, checks names with `site_object_name`, builds display links with `site_url`, wraps each allowed row as a `ConversationSite`, and returns everything inside a `SitesSlotPayload`.

*Call graph*: 6 external calls (__init__, __init__, __init__, site_name_from_object, site_object_name, site_url).


##### `_summarize`  (lines 49–51)

```
async def _summarize(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: This function gives a quick count for the Sites slot without reading full site details. It is used when the interface only needs to know whether there are sites to mention, and roughly how many can be shown.

**Data flow**: It receives the conversation slot context and looks only at `ctx.visible_items`. It counts them up to `CONVERSATION_SITES_MAX`; if the count is zero, it returns `None` so the slot can appear empty or absent, otherwise it returns the count.

**Call relations**: The `SITES_SLOT` provider uses this function as its summary callback. Unlike `_read`, it does not contact storage or build URLs; it simply gives the slot system a lightweight number before or instead of loading the full site list.


### `extensions/sites/ufo_ext_sites/main_homepage.py`

`domain_logic` · `background scheduled cleanup`

This file exists because the chat app has its own built-in homepage, but older workspace setup may have attached a separate hosted page to the workspace's main agent. If that old attachment stays in place, the system finds it first and shows it instead of the chat screen. Think of it like removing an old forwarding sign from a front door so visitors reach the actual lobby again.

The cleanup runs as a recurring sweep, not as a database migration, because not every workspace is ready at migration time. Some workspaces become owned by the chat app later, during normal provisioning or rolling deploys. The sweep looks for workspaces where the main agent has already been claimed by the chat app, still has a hosted homepage bound to it, and has not already been marked as cleaned up.

When it finds one, it releases the binding between the main agent and the hosted page. It does not delete the page. Instead, the page goes back to using its own visibility setting, but capped so it does not become visible to more people than it was before. Finally, the file records a marker saying this workspace has been released. That marker matters because users may later choose their own homepage; the sweep should not undo a real user choice after the one-time cleanup is finished.

#### Function details

##### `_the_chat_main_agent`  (lines 67–75)

```
def _the_chat_main_agent() -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database test for the exact agent this cleanup cares about: the workspace's main agent after it has been taken over by the chat app. It prevents the sweep from touching an agent too early, before the chat app is actually in charge.

**Data flow**: It takes no outside input. It reads the predefined database columns for the agent table and produces a combined condition: the agent must be marked as main, must have been provisioned by the chat extension, and must have the chat app's declared name.

**Call relations**: The candidate search uses this condition to find only relevant workspaces. The release step uses the same condition again before changing anything, so the sweep and the actual cleanup agree on what counts as the chat main agent.

*Call graph*: called by 2 (release_main_homepage, with_a_bound_main_homepage); 1 external calls (and_).


##### `unreleased_main_homepage_workspaces`  (lines 78–97)

```
def unreleased_main_homepage_workspaces(extension: str) -> WorkspaceCandidates
```

**Purpose**: This declares which workspaces should be offered to the scheduled cleanup job. A workspace qualifies when it still has a hosted homepage bound to the chat-owned main agent and has not yet been marked as released.

**Data flow**: It receives the extension name used for the marker record. Inside, it defines a database query that finds matching workspace IDs, then wraps that query in the job system's workspace-candidate format. The result is not the cleanup itself, but a way for the job runner to know which workspaces need attention.

**Call relations**: The job framework calls this when it needs candidates for the sweep. It hands the framework a query-building helper, and the framework turns those workspace IDs into per-workspace job runs.

*Call graph*: 1 external calls (owner_candidates).


##### `unreleased_main_homepage_workspaces.with_a_bound_main_homepage`  (lines 84–95)

```
def with_a_bound_main_homepage() -> sa.Select[tuple[UUID]]
```

**Purpose**: This builds the actual database query for workspaces that still need the one-time homepage release. It looks for a bound hosted page on the chat-owned main agent and excludes workspaces that already have the release marker.

**Data flow**: It reads from the hosted-site table, the agent table, and the extension store table. It joins hosted pages to their homepage agent, checks that the agent is the chat main agent, checks that no release marker exists for that workspace, and outputs workspace IDs grouped so each workspace appears once.

**Call relations**: This helper is passed to the workspace-candidate machinery by `unreleased_main_homepage_workspaces`. It relies on `_the_chat_main_agent` so that the candidate list uses the same definition of 'chat main agent' as the cleanup function itself.

*Call graph*: calls 1 internal fn (_the_chat_main_agent); 4 external calls (exists, literal, select, join).


##### `released_visibility`  (lines 100–104)

```
def released_visibility(site: str, agent: str) -> Visibility
```

**Purpose**: This decides how visible a hosted page should be after it is no longer bound to the main agent. It chooses the more restrictive of the page's own stored visibility and the agent's visibility, so the cleanup does not accidentally expose the page to more people.

**Data flow**: It receives two visibility values: one from the hosted page and one from the agent. It converts each into an ordered level, compares them, and returns the narrower level. The output is the safe visibility to write back when releasing the homepage binding.

**Call relations**: The release step calls this only when it has found a bound homepage to release. Its result is handed to the hosted-site store so the page resumes normal visibility without becoming broader than before.

*Call graph*: called by 1 (release_main_homepage); 1 external calls (visibility_level).


##### `release_main_homepage`  (lines 107–130)

```
async def release_main_homepage(ctx: ExtensionContext) -> None
```

**Purpose**: This is the scheduled job's per-workspace cleanup action. It finds the workspace's chat-owned main agent, releases any hosted homepage bound to it, and writes a marker so the release is not repeated later.

**Data flow**: It receives an extension context, which gives access to the current workspace, database transactions, and the extension's small key-value store. First it looks up the chat-owned main agent in this workspace. If there is none, it stops. If there is one, it asks the hosted-site store whether that agent has a bound homepage. When a bound page exists, it releases the binding using a safe visibility value. Finally, it writes a release marker containing the main agent ID.

**Call relations**: The job runner calls this for each workspace selected by the candidate query. It uses `_the_chat_main_agent` to verify the target agent, `HostedSites` to read and change homepage bindings, and `released_visibility` to avoid widening access. If the process crashes after releasing but before writing the marker, the next run can safely come back: the page is already unbound, and the marker can then be written.

*Call graph*: calls 3 internal fn (transaction, _the_chat_main_agent, released_visibility); 2 external calls (__init__, select).


### Sharing surfaces and previews
Public hosted-site surfaces enforce visibility, frame share links safely, and generate preview imagery for unfurls and social cards.

### `extensions/sites/ufo_ext_sites/share_card.py`

`domain_logic` · `site deploy or share-card backfill`

When a public hosted site is shared in Slack, Twitter, or another app, those apps often show a large “link card” image. Without this file, every hosted site would fall back to a generic brand image, so different sites would look the same when shared. This module creates a custom card for each site: a dark branded panel on the left, and the site’s own front page on the right.

The work happens inside the site’s sandbox, which is the isolated environment where the site already runs. That matters because the sandbox has access to the local site URL and to Chrome or Chromium, the browser used to take screenshots. First, the file asks the browser to photograph the site at the exact size needed for the card. Then it writes a small HTML page that lays out the left brand panel and places the screenshot on the right. A second browser pass renders that HTML page into an image. Finally, Pillow, an image library, converts the result into a progressive JPEG and computes a digest, a fingerprint used to name or verify the image.

The code is deliberately cautious. A share card is decorative, not required for the site to work. If the browser is missing, the screenshot is blank, the image is too large, or storage fails, the error is logged and the site keeps whatever card it already had.

#### Function details

##### `card_page`  (lines 486–511)

```
def card_page(name: str, drawn: str) -> str
```

**Purpose**: Builds the HTML page that will become the final share card. It includes the UFO brand panel, the site name, the embedded font and logo, and a placeholder where the site screenshot will later be inserted.

**Data flow**: It receives the site name and instructions for how the screenshot should be sized. It reads the local font and logo files, encodes the font so it can be placed directly inside the page, escapes the site name so user text cannot break the HTML, and returns one complete HTML string with a screenshot placeholder still inside it.

**Call relations**: This is used by `_compose` when it is ready to draw the final card. While building that page, it asks `_lockup` for the logo markup and uses standard encoding and escaping helpers so the browser can safely render the result.

*Call graph*: calls 1 internal fn (_lockup); called by 1 (_compose); 2 external calls (b64encode, escape).


##### `_lockup`  (lines 514–518)

```
def _lockup() -> str
```

**Purpose**: Extracts the SVG logo markup used in the left brand panel. It removes the document wrapper before the logo is placed inside another HTML page.

**Data flow**: It reads the bundled UFO logo file from disk. It finds the start of the `<svg>` markup and returns only that part, so the logo can be embedded directly into the share-card page.

**Call relations**: It is a small helper called by `card_page`. The larger card-building flow does not call it directly; it only needs the finished HTML page that `card_page` produces.

*Call graph*: called by 1 (card_page).


##### `shot_command`  (lines 521–541)

```
def shot_command(*, url: str, width: int, height: int, scale: int, shot: str, root: str) -> str
```

**Purpose**: Creates the shell command that will run Chromium inside the sandbox and take a screenshot. It packages the browser driver script, target URL, image size, scale, and output path into one command string.

**Data flow**: It receives the URL to photograph, the desired width and height, the device scale factor, the screenshot output path, and the sandbox root. It quotes paths and URLs so the shell reads them safely, inserts the embedded Python browser driver, and returns a command ready to run in the sandbox.

**Call relations**: `_shoot` calls this when it needs a browser screenshot. This function does not take the picture itself; it prepares the exact command that the sandbox will execute.

*Call graph*: called by 1 (_shoot); 2 external calls (quote, shell_path).


##### `draw_from_page`  (lines 544–562)

```
async def draw_from_page(ctx: ToolContext, sites: HostedSites, conversation_id: UUID, name: str, port: int) -> None
```

**Purpose**: Creates a new share card from the site’s currently running front page. This is the normal path during a fresh deploy, when the live local site can be photographed directly.

**Data flow**: It receives the tool context, the hosted-sites store, the conversation ID, the site name, and the local port where the site is running. It chooses a runtime path for the screenshot, asks `_shoot` to photograph `http://127.0.0.1:<port>`, and, if that succeeds, passes the screenshot to `_compose` to build and store the final card.

**Call relations**: This is one of the public entry points for this module’s work. It first relies on `_shoot` to capture the site and then hands the successful screenshot to `_compose`, which finishes the card and records it on the site.

*Call graph*: calls 2 internal fn (_compose, _shoot).


##### `draw_from_stored_shot`  (lines 565–580)

```
async def draw_from_stored_shot(ctx: ToolContext, sites: HostedSites, conversation_id: UUID, name: str, blob_key: str) -> None
```

**Purpose**: Creates a share card for an older site that already has a stored page preview but no share card yet. It lets existing deployments gain cards without needing to re-run the original page capture.

**Data flow**: It receives the tool context, hosted-sites store, conversation ID, site name, and blob key for an existing preview image. It downloads that preview from blob storage, writes it into the sandbox, and then asks `_compose` to turn it into a share card. If the write fails, it logs the failure and stops.

**Call relations**: This is the backfill path beside `draw_from_page`. Instead of calling `_shoot` for the first screenshot, it restores an already stored screenshot and then uses the same `_compose` function as the normal flow.

*Call graph*: calls 2 internal fn (_compose, _undrawn).


##### `_shoot`  (lines 583–612)

```
async def _shoot(ctx: ToolContext, name: str, shot: str, *, url: str, width: int, height: int, scale: int) -> bool
```

**Purpose**: Takes one browser screenshot and reports whether it succeeded. It is used both for photographing the site itself and for rendering the finished card page.

**Data flow**: It receives the sandbox context, site name, output path, target URL, size, and scale. It first empties the output file so a stale screenshot cannot be mistaken for a new one. Then it builds a browser command with `shot_command`, runs it in the sandbox with a timeout, and returns `True` only if the command exits successfully. On failure, it logs the problem and returns `False`.

**Call relations**: `draw_from_page` calls `_shoot` to capture the live site. `_compose` calls it again to capture the HTML page that represents the finished card. When anything goes wrong, `_shoot` delegates to `_undrawn` so the failure is recorded without stopping site hosting.

*Call graph*: calls 2 internal fn (_undrawn, shot_command); called by 2 (_compose, draw_from_page).


##### `_compose`  (lines 615–665)

```
async def _compose(ctx: ToolContext, sites: HostedSites, conversation_id: UUID, name: str, shot: str, drawn: str) -> None
```

**Purpose**: Turns an existing screenshot into the final stored share card and updates the site record. This is the central assembly step: build the card page, render it, encode it, store it, and save its reference.

**Data flow**: It receives the sandbox context, hosted-sites store, conversation ID, site name, path to the screenshot, and rules for how that screenshot should be drawn. It writes the card HTML from `card_page`, replaces the screenshot placeholder with a data URI, asks `_shoot` to render the card page to PNG, converts that PNG into a JPEG, stores the JPEG as a preview artifact, and finally writes the blob key and digest onto the hosted site row. If any step fails, it logs the issue and leaves the previous card unchanged.

**Call relations**: Both `draw_from_page` and `draw_from_stored_shot` feed screenshots into `_compose`. Inside the flow, `_compose` uses `card_page` to create the layout, `_shoot` to render it with the browser, `ToolContext.store_preview` to save the finished image, and `HostedSites.set_share_card` to attach the stored card to the site.

*Call graph*: calls 5 internal fn (store_preview, _shoot, _undrawn, card_page, set_share_card); called by 2 (draw_from_page, draw_from_stored_shot).


##### `_undrawn`  (lines 668–669)

```
def _undrawn(name: str, detail: object) -> None
```

**Purpose**: Logs that a share card could not be drawn. It keeps failures visible to operators while allowing the site itself to keep working.

**Data flow**: It receives the site name and a detail object describing what went wrong. It turns the detail into text, trims it to a safe length, and writes a structured log event named `site_card.undrawn`.

**Call relations**: This is the shared failure-reporting helper for `draw_from_stored_shot`, `_shoot`, and `_compose`. Those functions call it whenever they decide to abandon card generation instead of raising an error to the user-facing site flow.

*Call graph*: called by 3 (_compose, _shoot, draw_from_stored_shot); 1 external calls (log).


### `extensions/sites/ufo_ext_sites/surface.py`

`io_transport` · `request handling`

A hosted site link is treated like an address, not like a password. The link contains a signed token that says which workspace, conversation, and site name it points to. On every visit, this file checks that token, finds the site, identifies the browser from the `ufo_session` cookie when needed, and applies the site's visibility rule: public, workspace-only, or private. If the site is an agent homepage, the agent's own visibility rules take over instead.

The actual site files are not served here. This page is more like a secure picture frame: it creates an iframe that points at the site's own ingress origin, so the site's code runs away from the main portal and its cookies. For portal-only app pages, it redirects the viewer into the portal when needed, because those pages need the portal's bridge to work.

The file also protects privacy in link previews. Public sites may expose their name and a generated share card image. Non-public sites use generic UFO preview text and artwork, so a chat crawler with no session does not learn private names. The one anonymous image route rechecks that the site is still public before serving the card.

Finally, creators can post a form to change visibility. That form uses a CSRF token, a signed value tied to the viewer's session, so another website cannot silently change a creator's site settings.

#### Function details

##### `site_token`  (lines 174–183)

```
def site_token(workspace_id: UUID, conversation_id: UUID, name: str) -> str
```

**Purpose**: Creates the permanent signed token that names one hosted site. Someone uses it when they need a stable link to a site without putting database access or login state into the URL.

**Data flow**: It receives a workspace id, conversation id, and site name → packages those values as signed token claims for the sites surface → returns the token string that can be placed in a URL.

**Call relations**: When `site_url` builds a complete public link, it asks `site_token` for the address part of that link. The actual signing work is handed to the shared surface-token helper.

*Call graph*: called by 1 (site_url); 1 external calls (mint_surface_token).


##### `site_url`  (lines 186–196)

```
def site_url(public_base_url: str | None, workspace_id: UUID, conversation_id: UUID, name: str) -> str
```

**Purpose**: Builds the full browser URL for a hosted site. It refuses to invent a link if the deployment has no public base URL, because such a link could not actually be opened.

**Data flow**: It receives the deployment's public base URL plus the site identity → checks that the base URL exists → creates a site token → returns a URL under the hosted-site frame path, or raises a clear configuration error if hosting is not configured.

**Call relations**: This is the producer of ordinary hosted-site links. It relies on `site_token` for the signed address and raises `SiteHostingUnconfigured` when the surrounding deployment cannot host public links.

*Call graph*: calls 1 internal fn (site_token); 1 external calls (__init__).


##### `shipped_homepage_url`  (lines 208–223)

```
def shipped_homepage_url(public_base_url: str | None, workspace_id: UUID, slug: str, digest: str) -> str | None
```

**Purpose**: Builds a stable URL for a deploy-wide shipped app bundle that is meant to open inside the portal. Unlike a normal site URL, it points to an app slug and bundle digest rather than a hosted-site row.

**Data flow**: It receives a public base URL, workspace id, app slug, and digest → if the base URL is missing, it returns nothing → otherwise signs those claims into a token marked as a portal embed → returns the frame URL containing that token.

**Call relations**: This is used by code that publishes shipped app pages. Later, `shipped_address` and `frame` understand this token shape and route it through the shipped-app path instead of looking for a hosted site.

*Call graph*: 1 external calls (mint_surface_token).


##### `shipped_address`  (lines 226–240)

```
def shipped_address(token: str) -> ShippedAddress | None
```

**Purpose**: Reads and validates a shipped-app token. It answers only when the token has the special portal-embed shape for a shipped bundle.

**Data flow**: It receives a token string → verifies the signature and checks the expected claims → converts the workspace id text into a UUID → returns a `ShippedAddress`, or returns `None` if anything is missing, malformed, or not a shipped token.

**Call relations**: `resolve_workspace` uses this when a token is not a normal site token, so the request can still be assigned to the right workspace. `frame` uses it to decide whether to send the visit into `_shipped_frame` instead of normal site loading.

*Call graph*: called by 2 (frame, resolve_workspace); 3 external calls (__init__, verify_surface_token, UUID).


##### `site_card_url`  (lines 243–252)

```
def site_card_url(public_base_url: str | None, token: str, digest: str) -> str | None
```

**Purpose**: Builds the public URL for a site's share-card image. This URL is safe to put in Open Graph tags because the image route will still recheck that the site is public.

**Data flow**: It receives a public base URL, the site token, and the card digest → returns no URL if the deployment has no public base URL → otherwise returns an image URL containing the token and digest.

**Call relations**: `frame` calls this only for sites that are currently public and have a card hash. The returned URL points back to this same file's `share_card` route.

*Call graph*: called by 1 (frame).


##### `site_address`  (lines 255–274)

```
def site_address(token: str) -> SiteAddress | None
```

**Purpose**: Reads and validates a normal hosted-site token. It turns the signed URL token back into the site identity the rest of the file can use.

**Data flow**: It receives a token string → verifies that it belongs to the sites surface and has valid workspace, conversation, and name claims → notes whether it was marked as a portal embed → returns a `SiteAddress`, or `None` if the token is invalid.

**Call relations**: `resolve_workspace`, `frame`, `_resolve`, and `homepage_embed_url` all depend on this as the first proof that a request names a real kind of site address. Shipped-app tokens are deliberately not accepted here.

*Call graph*: called by 4 (_resolve, frame, homepage_embed_url, resolve_workspace); 3 external calls (__init__, verify_surface_token, UUID).


##### `homepage_embed_url`  (lines 277–292)

```
def homepage_embed_url(url: str) -> str
```

**Purpose**: Converts a normal hosted-site URL into the signed version used when the portal embeds an agent homepage. It keeps the same site address but marks the token as a portal embed.

**Data flow**: It receives a hosted-site URL → splits out the final token → validates that the URL really points at the hosted-site frame → signs a new token with the same site claims plus the portal-embed marker → returns the replacement URL.

**Call relations**: It calls `site_address` to avoid rewriting arbitrary URLs. The resulting URL is later recognized by `frame`, which allows homepage content to redirect into ingress only when the request truly came through the portal iframe.

*Call graph*: calls 1 internal fn (site_address); 1 external calls (mint_surface_token).


##### `resolve_workspace`  (lines 295–305)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: Finds which workspace a surface request belongs to before reading any site row. This matters because public visitors may have no login cookie, so the workspace must come from the signed URL itself.

**Data flow**: It reads the token from the request path → tries to decode it first as a normal site address and then as a shipped-app address → returns the workspace id if either succeeds, returns a 404 response if the token is invalid, or returns nothing when appropriate.

**Call relations**: The surface framework calls this before running route handlers. It uses `site_address`, `shipped_address`, and `_not_found` so bad tokens look like missing sites rather than authorization clues.

*Call graph*: calls 3 internal fn (_not_found, shipped_address, site_address).


##### `frame`  (lines 308–384)

```
async def frame(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This is the main GET handler for opening a hosted site link. It checks the token, checks the site or shipped app, applies visibility rules, and either returns a safe frame page or redirects the viewer where the app must run.

**Data flow**: It reads the token and optional deep path from the request → handles shipped-app tokens separately → resolves the site row → builds privacy-safe share tags → identifies the viewer from the session cookie → applies homepage, public, workspace, private, creator, and admin rules → creates an ingress URL for the actual site bytes → returns HTML, a redirect, a sign-in prompt, an unconfigured-hosting page, or the same 404 used for unknown sites.

**Call relations**: This is the central route handler. It delegates shipped bundles to `_shipped_frame`, site lookup to `_sites`, viewer identity to `_viewer`, admin checks to `_viewer_is_admin`, portal redirection to `_into_the_portal`, iframe HTML to `_frame_page`, and small request details to `_is_portal_iframe_request` and `_framed_from`.

*Call graph*: calls 18 internal fn (ingress_url, list_agents, _frame_page, _framed_from, _into_the_portal, _is_portal_iframe_request, _not_found, _page, _session_digest, _share_tags (+8 more)); 3 external calls (HTMLResponse, RedirectResponse, mint_surface_token).


##### `_shipped_frame`  (lines 387–426)

```
async def _shipped_frame(ctx: SurfaceContext, request: Request, shipped: ShippedAddress) -> Response
```

**Purpose**: Opens a deploy-wide shipped app bundle using the same surface frame path. These bundles are public code, but they still need to run inside the correct portal or ingress context.

**Data flow**: It receives the surface context, request, and decoded shipped address → creates generic share tags → if the request is not already a portal iframe, finds the matching agent and redirects to that agent's portal page → if it is framed, builds an ingress URL for the shipped bundle using the slug and digest → returns a redirect to ingress, a no-portal/no-site 404, or an unconfigured page.

**Call relations**: `frame` calls this when `shipped_address` recognizes the token. It uses `_into_the_portal` for cold visits, `_is_portal_iframe_request` and `_framed_from` to preserve frame context, and `ctx.ingress_url` to mint the actual temporary app address.

*Call graph*: calls 8 internal fn (ingress_url, list_agents, _framed_from, _into_the_portal, _is_portal_iframe_request, _not_found, _share_tags, _unconfigured_page); called by 1 (frame); 5 external calls (HTMLResponse, RedirectResponse, serve_port, shipped_anchor, shipped_app_slug).


##### `_is_portal_iframe_request`  (lines 429–430)

```
def _is_portal_iframe_request(request: Request) -> bool
```

**Purpose**: Detects whether the browser request looks like it came from an iframe. It does this by checking the standard fetch-destination header.

**Data flow**: It reads the request headers → compares `sec-fetch-dest` to `iframe` → returns `True` for iframe loads and `False` otherwise.

**Call relations**: `frame`, `_shipped_frame`, and `_framed_from` use this small check to decide whether to redirect a viewer into the portal or proceed with an embedded ingress load.

*Call graph*: called by 3 (_framed_from, _shipped_frame, frame).


##### `_framed_from`  (lines 433–436)

```
def _framed_from(request: Request) -> str | None
```

**Purpose**: Returns the referring page only when the current request is an iframe load. That lets ingress know what page framed it without trusting referrers from ordinary top-level visits.

**Data flow**: It receives the request → first asks `_is_portal_iframe_request` whether this is an iframe request → returns the `referer` header for iframe loads, otherwise returns `None`.

**Call relations**: `frame` and `_shipped_frame` pass this value into `ctx.ingress_url`, so the temporary ingress address can record where the frame came from.

*Call graph*: calls 1 internal fn (_is_portal_iframe_request); called by 2 (_shipped_frame, frame).


##### `_into_the_portal`  (lines 439–452)

```
def _into_the_portal(ctx: SurfaceContext, agent_id: UUID, share: str) -> Response
```

**Purpose**: Sends a viewer to an agent's page inside the member portal. This is needed because some app pages only work when the portal opens a bridge to them.

**Data flow**: It receives the context, agent id, and share tags → asks the context for the portal URL for that agent → returns a redirect if the portal exists → otherwise returns a simple HTML page explaining that no portal is installed.

**Call relations**: `frame` uses this for agent homepages opened outside the required portal iframe. `_shipped_frame` uses it for shipped app links opened cold. It relies on `_page` for the fallback HTML.

*Call graph*: calls 2 internal fn (home_url, _page); called by 2 (_shipped_frame, frame); 2 external calls (HTMLResponse, RedirectResponse).


##### `_unconfigured_page`  (lines 455–463)

```
def _unconfigured_page(title: str, share: str) -> str
```

**Purpose**: Builds the small HTML page shown when this deployment has no ingress/public hosting configured for the site content. It says plainly that the site cannot be embedded here.

**Data flow**: It receives a title and share tags → escapes the title so it is safe for HTML → combines frame styling with a short unconfigured-hosting message → returns the page HTML string.

**Call relations**: `frame` and `_shipped_frame` call this when they cannot mint an ingress URL. It uses `_page` as the shared HTML wrapper.

*Call graph*: calls 1 internal fn (_page); called by 2 (_shipped_frame, frame); 1 external calls (escape).


##### `share_card`  (lines 466–505)

```
async def share_card(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves a public site's generated preview image to anonymous crawlers and chat unfurlers. It refuses everything that is not still public, so old image URLs stop working soon after visibility changes.

**Data flow**: It resolves the site from the token in the URL → checks that the site exists, is not homepage-bound, is public, has a stored card, and that the URL digest matches the row's current card hash → reads the image bytes from blob storage → returns the JPEG response with cache headers, or returns the generic 404 body.

**Call relations**: The image URL is produced by `site_card_url` and placed into head tags by `frame`. `share_card` uses `_resolve` for row lookup and `_not_found` for every refusal, so the route does not reveal which private sites exist.

*Call graph*: calls 2 internal fn (_not_found, _resolve); 1 external calls (Response).


##### `set_visibility`  (lines 508–529)

```
async def set_visibility(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Processes the creator's form submission to change a site's visibility. It only lets the original creator do this, and it protects the form with a CSRF token tied to the current session.

**Data flow**: It resolves the site → identifies the viewer → rejects missing viewers, non-creators, homepage-bound sites, bad CSRF tokens, and invalid visibility values → writes the new visibility to the hosted-sites store → redirects back to the site's frame page.

**Call relations**: This is the POST route paired with the selector rendered by `_frame_page` and `_selector`. It uses `_resolve`, `_viewer`, `_csrf_holds`, `_sites`, and shared HTTP response classes to either save the change or explain the refusal.

*Call graph*: calls 5 internal fn (_csrf_holds, _not_found, _resolve, _sites, _viewer); 4 external calls (PlainTextResponse, RedirectResponse, form, visibility_level).


##### `_resolve`  (lines 532–536)

```
async def _resolve(ctx: SurfaceContext, request: Request) -> HostedSite | None
```

**Purpose**: Looks up the hosted-site row named by the request token. It is a small shared helper for routes that need a site but do not run the full frame flow.

**Data flow**: It reads the token from the request path → decodes it with `site_address` → if valid, opens the hosted-sites store for the current workspace and reads the row by conversation and name → returns the site or `None`.

**Call relations**: `share_card` and `set_visibility` both call this before applying their own rules. It uses `_sites` to create the store object.

*Call graph*: calls 2 internal fn (_sites, site_address); called by 2 (set_visibility, share_card).


##### `_sites`  (lines 539–540)

```
def _sites(ctx: SurfaceContext) -> HostedSites
```

**Purpose**: Creates the hosted-sites store object for the current workspace. This gives the rest of the file one short way to read and write site rows.

**Data flow**: It receives the surface context → takes the workspace id and transaction provider from it → returns a `HostedSites` store bound to that workspace.

**Call relations**: `frame`, `_resolve`, and `set_visibility` use this helper whenever they need to read a site row or update visibility.

*Call graph*: called by 3 (_resolve, frame, set_visibility); 1 external calls (__init__).


##### `_viewer_is_admin`  (lines 543–548)

```
async def _viewer_is_admin(ctx: SurfaceContext, viewer: UUID | None) -> bool
```

**Purpose**: Checks whether the current viewer is an active workspace admin. Admins are allowed to view private sites and private agent homepages even when they are not the creator or owner.

**Data flow**: It receives the context and a possible viewer id → immediately returns `False` if there is no viewer → reads the current seat snapshot inside a transaction → returns `True` only if the viewer is listed as seated and admin.

**Call relations**: `frame` calls this when private visibility rules need an admin exception. It reads membership information through the seats subsystem.

*Call graph*: calls 1 internal fn (transaction); called by 1 (frame); 1 external calls (__init__).


##### `_viewer`  (lines 551–564)

```
async def _viewer(ctx: SurfaceContext, request: Request) -> UUID | None
```

**Purpose**: Identifies the workspace member represented by the `ufo_session` cookie. If the cookie is missing, invalid, or belongs to someone without workspace access, it returns no viewer.

**Data flow**: It reads the session cookie → verifies the bearer token for this workspace and gets an email → finds or creates the linked member for that email → checks that the member still has access → returns the member id or `None`.

**Call relations**: `frame` uses this to decide whether a visitor may see non-public content. `set_visibility` uses it to prove the form submitter is the creator.

*Call graph*: calls 3 internal fn (link_member, linked_member, member_has_access); called by 2 (frame, set_visibility); 1 external calls (verify_token).


##### `_csrf_holds`  (lines 567–569)

```
def _csrf_holds(request: Request, submitted: str) -> bool
```

**Purpose**: Checks that a submitted visibility form token belongs to this browser session. This prevents another website from tricking a signed-in creator's browser into changing a site.

**Data flow**: It receives the request and submitted token → verifies the token signature → compares the token's stored session digest with the digest of the current request's session cookie → returns `True` only when they match.

**Call relations**: `set_visibility` calls this before accepting a visibility change. It relies on `_session_digest` for the session fingerprint and the shared surface-token verifier for signature checking.

*Call graph*: calls 1 internal fn (_session_digest); called by 1 (set_visibility); 1 external calls (verify_surface_token).


##### `_session_digest`  (lines 572–576)

```
def _session_digest(request: Request) -> str
```

**Purpose**: Creates a one-way fingerprint of the current session cookie for CSRF protection. A one-way hash lets the form token be tied to the session without storing the raw cookie value inside it.

**Data flow**: It reads the `ufo_session` cookie from the request, or an empty string if missing → hashes it with SHA-256 → returns the hexadecimal digest text.

**Call relations**: `frame` uses this when minting a creator's CSRF token for the visibility form. `_csrf_holds` uses it later to check the submitted token against the current session.

*Call graph*: called by 2 (_csrf_holds, frame); 1 external calls (sha256).


##### `_not_found`  (lines 579–580)

```
def _not_found() -> Response
```

**Purpose**: Returns the standard 404 response body for missing, invalid, or unauthorized site access. Using the same response makes it harder to use the frame as a way to discover private sites.

**Data flow**: It takes no input → creates a plain-text response with the shared not-found body and 404 status → returns that response.

**Call relations**: `resolve_workspace`, `frame`, `_shipped_frame`, `share_card`, and `set_visibility` all use this for refusals that should look the same as an unknown site.

*Call graph*: called by 5 (_shipped_frame, frame, resolve_workspace, set_visibility, share_card); 1 external calls (PlainTextResponse).


##### `_page`  (lines 583–588)

```
def _page(title: str, style: str, body: str, share: str) -> str
```

**Purpose**: Builds the common HTML shell used by this surface. It keeps all small pages consistent: document header, viewport, title, share tags, style, and body.

**Data flow**: It receives a title, CSS style text, body HTML, and share-tag HTML → concatenates them into a complete minimal HTML document string → returns that string.

**Call relations**: `frame`, `_frame_page`, `_into_the_portal`, and `_unconfigured_page` use this instead of repeating the same document wrapper.

*Call graph*: called by 4 (_frame_page, _into_the_portal, _unconfigured_page, frame).


##### `_share_tags`  (lines 591–625)

```
def _share_tags(name: str | None, canonical: str | None, card: str | None) -> str
```

**Purpose**: Builds the Open Graph and Twitter preview tags that chat apps and social sites read when a link is pasted. It only names the actual site when the caller has decided that the site is public.

**Data flow**: It receives an optional site name, optional canonical URL, and optional card image URL → escapes values for safe HTML → chooses a site-specific or generic title and image → returns the meta-tag HTML string.

**Call relations**: `frame` calls this for normal site pages after deciding whether the site is public enough to publish its name and card. `_shipped_frame` calls it with generic values for shipped app pages.

*Call graph*: called by 2 (_shipped_frame, frame); 1 external calls (escape).


##### `_frame_page`  (lines 628–667)

```
def _frame_page(site: HostedSite, embedded: str | None, frame_path: str, csrf: str, share: str) -> str
```

**Purpose**: Builds the visible hosted-site frame page. It shows the site name, either a creator visibility selector or a viewer badge, and the iframe that loads the real site content.

**Data flow**: It receives the site row, optional ingress URL, frame path, optional CSRF token, and share tags → creates the visibility control or badge → creates the sandboxed iframe when an ingress URL exists, otherwise an unconfigured-hosting message → wraps everything in `_page` → returns the HTML string.

**Call relations**: `frame` calls this after all viewing rules have passed. It delegates the creator's form to `_selector`, uses `_page` for the document shell, and escapes site values before putting them into HTML.

*Call graph*: calls 2 internal fn (_page, _selector); called by 1 (frame); 1 external calls (escape).


##### `_selector`  (lines 670–680)

```
def _selector(current: Visibility, frame_path: str, csrf: str) -> str
```

**Purpose**: Builds the small form that lets a site creator choose private, workspace, or public visibility. It includes the CSRF token needed for `set_visibility` to trust the submission.

**Data flow**: It receives the current visibility, the canonical frame path, and CSRF token → creates option elements with the current level selected → builds a POST form pointing at the visibility route → returns the form HTML string.

**Call relations**: `_frame_page` calls this only when the viewer is the creator and has a CSRF token. The form it returns posts back to `set_visibility`.

*Call graph*: called by 1 (_frame_page); 1 external calls (escape).


### `core/src/ufo/runtime/media/site_previewer.py`

`domain_logic` · `preview generation`

This file exists so the system can show a visual snapshot of a web site that is running inside a sandbox. Think of it like sending a photographer to a temporary web address: the photographer opens the page, takes one screenshot, and gives back either a saved image or a clear failure.

The main class, SitePreviewer, knows three things: where to store the image, where the preview service lives, and how to reach the sandbox from the outside. Its render method first checks that the requested file name and image size are sensible. It then creates a public viewing URL for the sandbox port using the current workspace and conversation. If there is no usable URL, it gives up quietly.

Next it chooses how the preview service should return the image. If storage is backed by S3, it gives the service a temporary upload link, so the service can write the PNG directly to storage. Otherwise, it asks the service to return the PNG bytes in the HTTP response, then this code uploads them itself.

The file is careful about trust. It limits response size, checks HTTP errors, verifies PNG content when receiving bytes inline, and confirms the returned dimensions match the requested dimensions. If anything goes wrong, it logs a short diagnostic message and returns None instead of crashing the larger workflow.

#### Function details

##### `SitePreviewer.render`  (lines 47–124)

```
async def render(self, conversation_id: UUID, port: int, name: str, width: int, height: int) -> StoredPreview | None
```

**Purpose**: This function creates one screenshot preview of a hosted sandbox site and stores it as an artifact. A caller gives it the conversation, port, output name, and target dimensions; it returns a StoredPreview when the image is successfully saved, or None when the preview cannot be drawn.

**Data flow**: It starts with a conversation ID, sandbox port, image name, width, and height. It checks that the name is just a filename and that the dimensions are within allowed bounds. It then builds a public URL for the sandbox page, creates a unique storage key, and sends a render request to the preview service. If S3 storage is available, the preview service uploads directly to a temporary upload URL and returns JSON metadata; this function validates that metadata and returns the stored blob key and size. If direct upload is not available, the service returns PNG bytes; this function checks the content type, PNG signature, dimensions, uploads the bytes to blob storage, and then returns the stored blob key and size. Network errors, bad service responses, oversized responses, and validation failures are logged and become a None result.

**Call relations**: This is the file's main working step: other preview-generation code would call it when a sandbox page needs a thumbnail or artifact image. Inside the flow it asks ws_current for the current workspace, mint_ingress_view_url to make the sandbox reachable, json.dumps to package the preview request, httpx.Timeout and httpx.AsyncClient to call the external preview service, StoredPreview to describe the saved result, and log to record why a preview was not created.

*Call graph*: 9 external calls (__init__, AsyncClient, Timeout, dumps, PurePosixPath, log, mint_ingress_view_url, ws_current, uuid4).


### Hosted site registry
The shared site registry records ownership, serving targets, access rights, and mutation permissions for all hosted site links.

### `extensions/sites/ufo_ext_sites/store.py`

`domain_logic` · `site deploy, site lookup, visibility changes, homepage binding, and share-card/preview updates`

A hosted site here is like a numbered mailbox for a web page: the public link uses a stable name, while the actual contents may come from a sandbox port or from files saved in blob storage. This file defines the database row for that mailbox and the rules for changing it safely. Every lookup is scoped to one workspace, because the database connection can see the whole database and must not accidentally cross tenant boundaries.

The main class, HostedSites, is a workspace-specific store. It can register a new deploy, update stored source, attach preview images and share cards, list sites, change visibility, bind a site as an agent homepage, and unregister a site. It also enforces important ownership rules. Only the creator can change who may view a site. Taking over a port can remove another existing site, so the code refuses that unless the same member is allowed to unhost it.

The file also validates site names and static-source manifests. A source manifest is a map of safe relative paths to stored file details; suspicious paths such as absolute paths or directory escapes are rejected. In short, this file is both the site address book and the gatekeeper that keeps links, ownership, visibility, previews, and stored source in sync.

#### Function details

##### `SourceManifest._rooted`  (lines 96–99)

```
def _rooted(cls, root: str) -> str
```

**Purpose**: Checks that a stored-source manifest uses an allowed blob-storage prefix. This prevents a site from claiming files outside the expected site or app storage areas.

**Data flow**: It receives the manifest root string → checks that it starts with either "sites/" or "apps/" and ends with a slash → returns the same root if it is valid, or raises an error if it is not.

**Call relations**: This is called automatically by Pydantic, the data validation library, when a SourceManifest is built or parsed. It runs before the manifest is trusted by code that serves static site files.


##### `SourceManifest._pathed`  (lines 103–118)

```
def _pathed(cls, files: dict[str, SiteFile]) -> dict[str, SiteFile]
```

**Purpose**: Checks that every file path in a stored-source manifest is a plain path inside the site. This blocks paths that could escape the site folder, such as paths with leading slashes or hidden directory jumps.

**Data flow**: It receives the manifest's file map → examines each path for unsafe characters, backslashes, absolute-path form, or path traversal → asks contained_relative to confirm the path stays under the site-source anchor → returns the original file map if every path is safe, or raises an error if any path is unsafe.

**Call relations**: Pydantic calls this while validating SourceManifest. It hands each candidate path to ufo.sdk.sandbox.contained_relative, which is the shared safety check for keeping paths inside an allowed directory.

*Call graph*: 1 external calls (contained_relative).


##### `site_name`  (lines 149–157)

```
def site_name(raw: str) -> str
```

**Purpose**: Turns a user-supplied site name into the safe, short slug stored in the registry and used in links. It gives the system one consistent naming rule for chat objects and permanent site URLs.

**Data flow**: It receives raw text → lowercases it, replaces runs of non-letter-or-number characters with hyphens, trims extra hyphens, and limits the length → returns the cleaned name, or raises InvalidSiteName if nothing usable remains.

**Call relations**: Callers use this before writing a site row or minting its link. If the cleanup leaves no letters or digits, it raises InvalidSiteName so the deploying tool can tell the user or agent that the name cannot identify a site.

*Call graph*: 1 external calls (__init__).


##### `default_visibility`  (lines 160–168)

```
def default_visibility(audience: Audience) -> Visibility
```

**Purpose**: Chooses the starting visibility for a newly created site based on the conversation audience. It is deliberately cautious for direct messages and external rooms, so private or externally shared conversations do not accidentally publish to the whole workspace.

**Data flow**: It receives an Audience value → parses it into a simpler audience form → checks whether it represents a single member or a foreign/external audience → returns "private" for those cases, otherwise returns "workspace".

**Call relations**: HostedSites.register calls this only when inserting a brand-new site and no explicit visibility was supplied. It relies on parse_audience and audience_member from the audience SDK to understand what kind of conversation produced the site.

*Call graph*: called by 1 (register); 2 external calls (audience_member, parse_audience).


##### `visibility_level`  (lines 171–179)

```
def visibility_level(value: str) -> Visibility
```

**Purpose**: Accepts only the three visibility values the site registry understands: private, workspace, or public. This keeps database values and submitted values from drifting into unsupported states.

**Data flow**: It receives a string → compares it with the allowed visibility words → returns the same value as a typed visibility value, or raises an error for anything else.

**Call relations**: _site calls this when turning a database row into a HostedSite object. That means bad stored visibility data is caught at the boundary where rows become application objects.

*Call graph*: called by 1 (_site).


##### `HostedSites.register`  (lines 213–306)

```
async def register(self, conversation_id: UUID, name: str, port: int, creator_member_id: UUID, visibility: Visibility | None, audience: Audience, may_unhost: bool, *, manifest: str | None) -> HostedSi
```

**Purpose**: Records that a deploy now owns a site name and port, or updates the existing record for that name. It also protects against accidentally taking over another member's site or changing someone else's visibility choice.

**Data flow**: It receives the conversation, safe site name, port, creator, optional visibility, audience, unhost permission, and optional source manifest → opens a database transaction → checks refusal rules → deletes any same-conversation site displaced from the port → updates the existing named row or inserts a new one → stamps a new deploy generation so viewers know the page changed → reads back and returns the registered HostedSite.

**Call relations**: Deploy code calls this after a site has been served or static source has been promoted. Inside the transaction it delegates safety checks to _refuse, reads rows through _read, uses default_visibility for new sites without explicit visibility, and uses SQLAlchemy update, insert, delete, and case expressions to make the database change atomically.

*Call graph*: calls 3 internal fn (_read, _refuse, default_visibility); 6 external calls (case, delete, insert, update, time_ns, uuid4).


##### `HostedSites.redeploy`  (lines 308–335)

```
async def redeploy(self, conversation_id: UUID, name: str, manifest: str) -> HostedSite | None
```

**Purpose**: Replaces the stored source manifest for an existing site without changing its name, port, owner, or visibility. This lets a link keep pointing to the same site while its saved static files are updated.

**Data flow**: It receives the target conversation, site name, and new manifest string → opens a transaction → updates the source_manifest, updated time, and deploy_generation for that row → reads the row back → returns the updated HostedSite, or None if the site no longer exists.

**Call relations**: This is used when an existing hosted site should receive new stored static source in place. It uses the same strictly increasing deploy-generation pattern as register, then calls _read to return the current row.

*Call graph*: calls 1 internal fn (_read); 3 external calls (case, update, time_ns).


##### `HostedSites.homepage`  (lines 337–349)

```
async def homepage(self, agent_id: UUID) -> HostedSite | None
```

**Purpose**: Finds the site currently bound as a particular agent's homepage. The registry expects at most one such site per agent.

**Data flow**: It receives an agent ID → opens a transaction → selects the workspace row whose homepage_agent_id matches → returns None if there is no match, or converts the row into a HostedSite.

**Call relations**: Homepage lookup code calls this when it needs to resolve an agent's homepage. It builds the shared column selection with _columns and converts the result with _site.

*Call graph*: calls 2 internal fn (_columns, _site).


##### `HostedSites.set_preview`  (lines 351–371)

```
async def set_preview(self, conversation_id: UUID, name: str, preview: StoredPreview) -> None
```

**Purpose**: Stores the preview image captured for a site after deploy. This allows the site listing or frame to show a snapshot without delaying the main registration step.

**Data flow**: It receives the conversation, site name, and StoredPreview containing a blob key and byte size → opens a transaction → updates the matching row's preview fields and updated time → returns nothing.

**Call relations**: Deploy-preview code calls this after a page render succeeds. It writes directly with SQLAlchemy update; if the site was removed or renamed while the preview was being made, the update simply matches no row.

*Call graph*: 1 external calls (update).


##### `HostedSites.set_share_card`  (lines 373–397)

```
async def set_share_card(self, conversation_id: UUID, name: str, blob_key: str, digest: str) -> None
```

**Purpose**: Stores the generated share-card image and its digest for a site. The digest lets public share-card URLs change when the card changes, avoiding stale crawler caches.

**Data flow**: It receives the conversation, site name, blob key, and digest → opens a transaction → writes the share-card blob key, hash, and updated time onto the matching row → returns nothing.

**Call relations**: extensions/sites/ufo_ext_sites/share_card._compose calls this after composing a card. Like set_preview, it writes with SQLAlchemy update and harmlessly does nothing if the row disappeared before the card was ready.

*Call graph*: called by 1 (_compose); 1 external calls (update).


##### `HostedSites.read`  (lines 399–401)

```
async def read(self, conversation_id: UUID, name: str) -> HostedSite | None
```

**Purpose**: Looks up one site by conversation and name inside this workspace. It is the simple public read path for code that already knows exactly which site it wants.

**Data flow**: It receives a conversation ID and site name → opens a transaction → asks _read to fetch and convert the row → returns a HostedSite or None.

**Call relations**: Other parts of the site system call this for direct resolution. It is a small wrapper around _read that supplies the transaction boundary.

*Call graph*: calls 1 internal fn (_read).


##### `HostedSites.all`  (lines 403–413)

```
async def all(self) -> tuple[HostedSite, ...]
```

**Purpose**: Returns every hosted site in the current workspace, ordered from oldest to newest. Higher-level code can then apply its own object or permission filters.

**Data flow**: It opens a transaction → selects all rows scoped to this workspace → orders them by creation time and name → converts each row into a HostedSite → returns them as a tuple.

**Call relations**: Workspace listing code uses this when it needs the full site set. It reuses _columns for the select list and _site for row conversion.

*Call graph*: calls 2 internal fn (_columns, _site).


##### `HostedSites.conversation`  (lines 415–432)

```
async def conversation(self, conversation_id: UUID, names: tuple[str, ...], limit: int) -> tuple[HostedSite, ...]
```

**Purpose**: Returns selected hosted sites that belong to one conversation. It is useful when a caller wants a bounded list of known site names for that conversation.

**Data flow**: It receives a conversation ID, a tuple of names, and a limit → opens a transaction → selects matching rows in the workspace and conversation whose names are in the requested set → orders and limits them → converts rows into HostedSite objects → returns a tuple.

**Call relations**: Conversation-level listing code calls this for targeted site retrieval. It uses _columns to build the query shape and _site to turn database rows into application objects.

*Call graph*: calls 2 internal fn (_columns, _site).


##### `HostedSites.visible_conversation`  (lines 434–464)

```
async def visible_conversation(self, conversation_id: UUID, member_id: UUID, limit: int, *, admin: bool, homepage_agents: frozenset[UUID]) -> tuple[HostedSite, ...]
```

**Purpose**: Returns the sites in a conversation that a specific member is allowed to open. It combines ownership, visibility level, homepage binding, and admin status into one database query.

**Data flow**: It receives a conversation ID, member ID, limit, admin flag, and set of homepage agent IDs visible to the member → builds a permission condition unless the member is an admin → selects matching rows in the workspace and conversation → orders and limits them → converts rows into HostedSite objects → returns a tuple.

**Call relations**: UI or frame code uses this when showing a member the sites they may access. It uses SQLAlchemy's or_ to express the non-admin gate, _columns to select fields, and _site to convert rows.

*Call graph*: calls 2 internal fn (_columns, _site); 1 external calls (or_).


##### `HostedSites.set_visibility`  (lines 466–481)

```
async def set_visibility(self, conversation_id: UUID, name: str, visibility: Visibility) -> HostedSite | None
```

**Purpose**: Changes a site's own visibility level and returns the updated site if it still exists. It also changes the site's generation token so old permission grants tied to the prior setting do not silently remain valid.

**Data flow**: It receives a conversation, site name, and new visibility → opens a transaction → updates the row's visibility, generation, and updated time → reads the row back → returns the updated HostedSite or None.

**Call relations**: Visibility-changing flows call this after they have decided the actor may make the change. It writes with SQLAlchemy update, creates a fresh UUID generation, and uses _read to return the new state.

*Call graph*: calls 1 internal fn (_read); 2 external calls (update, uuid4).


##### `HostedSites.set_homepage`  (lines 483–509)

```
async def set_homepage(self, agent_id: UUID, conversation_id: UUID, name: str) -> HostedSite | None
```

**Purpose**: Binds a named site as an agent's homepage while clearing any previous homepage binding for that agent. This keeps the rule that an agent has at most one homepage site.

**Data flow**: It receives an agent ID, conversation ID, and site name → opens a transaction → clears homepage_agent_id from any existing row for that agent → sets homepage_agent_id on the named row → reads the named row back → returns the bound HostedSite or None if it was not found.

**Call relations**: Homepage-setting code calls this when an agent's homepage should point at a site. It performs both database updates in one transaction, then delegates to _read for the returned row.

*Call graph*: calls 1 internal fn (_read); 1 external calls (update).


##### `HostedSites.release_homepage`  (lines 511–535)

```
async def release_homepage(self, conversation_id: UUID, name: str, visibility: Visibility) -> None
```

**Purpose**: Removes a site's homepage binding and restores the site's own visibility level. This matters because while a site is bound as a homepage, access follows the agent's visibility rather than the site's stored visibility.

**Data flow**: It receives a conversation, site name, and visibility to resume → opens a transaction → clears homepage_agent_id, writes the supplied visibility, changes the generation token, and updates the timestamp → returns nothing.

**Call relations**: Homepage-unbinding code calls this when a site should stop acting as an agent homepage. It uses SQLAlchemy update and uuid4 so permission checks can tell that the access generation changed.

*Call graph*: 2 external calls (update, uuid4).


##### `HostedSites.unregister`  (lines 537–548)

```
async def unregister(self, conversation_id: UUID, name: str) -> None
```

**Purpose**: Removes a site registration so its permanent link no longer resolves. The underlying sandbox process may still exist, but the registry stops advertising that port as this named site.

**Data flow**: It receives a conversation ID and site name → opens a transaction → deletes the matching workspace-scoped row → returns nothing.

**Call relations**: Unhost or cleanup flows call this when a site should be removed from the registry. It uses SQLAlchemy delete to remove only the row for the current workspace, conversation, and name.

*Call graph*: 1 external calls (delete).


##### `HostedSites.refuse_or_pass`  (lines 550–570)

```
async def refuse_or_pass(self, conversation_id: UUID, name: str, port: int, creator_member_id: UUID, visibility: Visibility | None, may_unhost: bool) -> HostedSite | None
```

**Purpose**: Runs the same safety checks as register but does not write anything. It lets deploy code discover whether a registration would be refused before it performs an action that might stop an existing server.

**Data flow**: It receives the proposed registration details → opens a transaction → delegates to _refuse → returns the site that would be displaced, or None, or raises the same error register would raise.

**Call relations**: Deploy orchestration calls this as a preflight check. It shares _refuse with register so the dry run and the real write enforce the same ownership and unhost rules.

*Call graph*: calls 1 internal fn (_refuse).


##### `HostedSites._refuse`  (lines 572–601)

```
async def _refuse(self, connection: AsyncConnection, conversation_id: UUID, name: str, port: int, creator_member_id: UUID, visibility: Visibility | None, may_unhost: bool) -> HostedSite | None
```

**Purpose**: Centralizes the rules that can block a site registration. It prevents non-creators from changing visibility and prevents a deploy from unhosting another member's site or unhosting without a live member request.

**Data flow**: It receives an open database connection plus the proposed site details → reads the existing same-name site → checks whether a visibility change is allowed → looks for a different site already using the target port → checks whether that displacement is allowed → returns the displaced HostedSite if one exists, or None, or raises a refusal error.

**Call relations**: HostedSites.register calls this inside the real transaction before writing. HostedSites.refuse_or_pass calls it for a no-write preflight. It uses _read to inspect the named site, _on_port to find port conflicts, and raises NotTheSiteCreator or UnhostNeedsASpeaker when the rules fail.

*Call graph*: calls 2 internal fn (_on_port, _read); called by 2 (refuse_or_pass, register); 2 external calls (__init__, __init__).


##### `HostedSites._on_port`  (lines 603–618)

```
async def _on_port(self, connection: AsyncConnection, conversation_id: UUID, port: int, name: str) -> HostedSite | None
```

**Purpose**: Finds whether another site in the same conversation is already registered on the requested port. That matters because one port can only serve one origin, so reusing it may effectively unhost the older site.

**Data flow**: It receives an open connection, conversation ID, port, and the current site name to exclude → queries for a different name in the same workspace and conversation with that port → returns the matching HostedSite or None.

**Call relations**: _refuse calls this while checking a proposed registration. It builds its select list with _columns, executes through the async database connection, and converts a found row with _site.

*Call graph*: calls 2 internal fn (_columns, _site); called by 1 (_refuse); 1 external calls (execute).


##### `HostedSites._read`  (lines 620–632)

```
async def _read(self, connection: AsyncConnection, conversation_id: UUID, name: str) -> HostedSite | None
```

**Purpose**: Fetches one exact site row and turns it into the in-code HostedSite form. It is the shared internal lookup used after writes and by public read methods.

**Data flow**: It receives an open connection, conversation ID, and site name → queries the current workspace for that exact row → returns None if absent, or a HostedSite if present.

**Call relations**: register, redeploy, read, set_visibility, set_homepage, and _refuse all call this whenever they need the current row. It uses _columns to keep the selected fields consistent and _site to perform conversion.

*Call graph*: calls 2 internal fn (_columns, _site); called by 6 (_refuse, read, redeploy, register, set_homepage, set_visibility); 1 external calls (execute).


##### `HostedSites._columns`  (lines 634–651)

```
def _columns(self) -> sa.Select
```

**Purpose**: Builds the standard database select statement for hosted-site rows. Keeping this in one place ensures every reader asks for the same fields needed to build a HostedSite.

**Data flow**: It takes no outside data beyond the table definition → creates a SQL select containing all HostedSite fields except the workspace key → returns that unfinished select so callers can add filters and ordering.

**Call relations**: Read-style methods such as homepage, all, conversation, visible_conversation, _read, and _on_port call this before adding their own where clauses. It uses SQLAlchemy select to describe the query.

*Call graph*: called by 6 (_on_port, _read, all, conversation, homepage, visible_conversation); 1 external calls (select).


##### `_aware`  (lines 654–657)

```
def _aware(moment: datetime) -> datetime
```

**Purpose**: Makes sure a timestamp has timezone information. This avoids comparing a timezone-less database timestamp with timezone-aware timestamps elsewhere and guessing the wrong meaning.

**Data flow**: It receives a datetime → if it already has timezone information, returns it unchanged → otherwise marks it as UTC → returns the timezone-aware datetime.

**Call relations**: _site calls this for created_at and updated_at when converting database rows. It compensates for databases such as SQLite that may return timezone-less datetime objects.

*Call graph*: called by 1 (_site); 1 external calls (replace).


##### `_site`  (lines 660–677)

```
def _site(row: sa.Row) -> HostedSite
```

**Purpose**: Turns a raw database row into a HostedSite data object with validated visibility and safe timestamps. This is the boundary where stored database values become normal application data.

**Data flow**: It receives a SQLAlchemy row → copies each site field into a HostedSite → validates the visibility string with visibility_level → normalizes created_at and updated_at with _aware → returns the completed HostedSite.

**Call relations**: All row-reading paths call this, including _read, _on_port, homepage, all, conversation, and visible_conversation. It relies on visibility_level for the visibility column and _aware for timestamp cleanup before constructing HostedSite.

*Call graph*: calls 2 internal fn (_aware, visibility_level); called by 6 (_on_port, _read, all, conversation, homepage, visible_conversation); 1 external calls (__init__).
