# Artifacts, previews, hosted sites, and app pages  `stage-13.2`

This stage is shared support for things a user can see or download after the main agent work: files, previews, hosted web pages, sources, tasks, and small app pages. It is like the display shelf and shipping desk for the system.

For shared files, the artifact files define what a downloadable artifact is, create signed links that prove permission and expire, and serve the bytes only when the link is valid or safely refreshed. Image and document preview files check uploads, reject unsafe or oversized media, call outside preview services when needed, and store simple preview records.

For websites, the site store records who owns each hosted page, where it runs, what files it uses, and what preview or share card belongs to it. Source handling moves site files between storage and the sandbox. Site tools let agents build, serve, publish, audit, and bind sites. The application builder and audit guide generated app pages from plan to code to browser checks to deployment.

Finally, conversation slots expose the right items back to the portal: sites, research sources, and scheduled automations, but only for authorized conversations.

## Files in this stage

### Hosted site workflows
Guided builders, agent tools, previews, share cards, source movement, auditing, and the hosted-site registry work together to create, validate, publish, and present sites.

### `extensions/sites/ufo_ext_sites/application_builder.py`

`orchestration` · `request handling through builder subagent, validation, QA gate, and deployment`

This file is the control room for the UFO application builder. When a member asks for an app, the system does not let a general assistant freely edit and deploy anything. Instead, it starts a specialized worker with a fixed workspace, fixed files, and a strict order of steps. Think of it like a kitchen with marked stations: draw the recipe card first, cook only in the assigned pan, taste-test before serving, and only then put the dish on the table.

The file defines the task and result shapes, the tools the worker can call, and the checks around those tools. The worker must create an SVG design that matches strict size, safety, and layout rules. Then it must write `app.tsx`, importing only approved UI pieces from `ufo/kit`. The source is validated for allowed imports, required components, and styling rules, then compiled with Vite, a web build tool. If edits fail, the failed candidate is kept separately so the worker can repair it without breaking the served app.

Finally, deployment is accepted only if product QA left proof, the deployed source matches that proof, and the deployed site belongs to the right member. Hooks in this file also block wrong-phase actions, force app-creation requests through the right skill, limit repeated repair reads, and prevent deployment before QA passes.

#### Function details

##### `_local_source_bindings`  (lines 516–533)

```
def _local_source_bindings(code: str) -> set[str]
```

**Purpose**: Finds names that are defined locally inside the app source, such as functions, variables, destructured values, and function parameters. This matters because the validator must tell the difference between a UI component imported from `ufo/kit` and a component-like name the app defined itself.

**Data flow**: It receives the source code as text. It scans the text with simple patterns and gathers every local name it can recognize. It returns a set of those names, leaving the source unchanged.

**Call relations**: It is used by `_rendered_application_components` while checking whether the page really renders approved kit components. That caller subtracts these local names so locally defined JSX tags are not mistaken for imported UI kit pieces.

*Call graph*: called by 1 (_rendered_application_components); 1 external calls (findall).


##### `ApplicationBuilderTask.source_is_the_scaffolds_app_tsx`  (lines 692–704)

```
def source_is_the_scaffolds_app_tsx(self) -> 'ApplicationBuilderTask'
```

**Purpose**: Checks that a builder task points to exactly the app file inside its scaffold folder. This prevents a worker from being sent off to edit some other file in the workspace.

**Data flow**: It reads the task's scaffold path and source path, converts them into contained paths under `/workspace`, and compares them. If the source is not `app.tsx` directly inside the scaffold, validation fails; otherwise the same task object is returned.

**Call relations**: This runs automatically when `ApplicationBuilderTask` is validated. Later tools trust this check when they write, read, compile, or accept the fixed app source path.

*Call graph*: 2 external calls (PurePosixPath, contained_relative).


##### `ApplicationWireframeResult.result_matches_status`  (lines 738–747)

```
def result_matches_status(self) -> 'ApplicationWireframeResult'
```

**Purpose**: Makes sure a wireframe result is internally consistent. A ready result must include shared design evidence, while a blocked result must explain what stopped it.

**Data flow**: It reads the result fields after the model is built. Depending on the status, it either accepts the fields as a valid combination or raises a validation error. It returns the unchanged result when the combination makes sense.

**Call relations**: This protects results returned by `design_ufo_application`. Callers can rely on a `ready` wireframe having a filename and digest, and a `blocked` wireframe having a useful reason.


##### `ApplicationBuilderResult.result_matches_status`  (lines 765–784)

```
def result_matches_status(self) -> 'ApplicationBuilderResult'
```

**Purpose**: Checks that a worker's final build result tells a coherent story. For example, a deployed result must name the deployed site, while a blocked result must not pretend to have deployment identity.

**Data flow**: It reads the status and related fields such as source path, design path, site name, URL, and blocker text. It raises a validation error for impossible combinations and returns the result when the fields match the status.

**Call relations**: This model is used both for worker output and acceptance decisions. `build_ufo_application`, `design_ufo_application`, and `ApplicationBuildAcceptance` depend on it so they can safely interpret the worker's answer.


##### `ApplicationBuildAcceptance.accept`  (lines 794–880)

```
async def accept(self, result: ApplicationBuilderResult) -> ApplicationBuilderResult
```

**Purpose**: Decides whether a worker's claimed deployment should really be accepted and bound as the member's homepage. It is the final guard that checks ownership, QA proof, stored source, and source hashes before the app is treated as shipped.

**Data flow**: It receives a structured build result. It first rejects obvious wrong results, then reads QA proof from the extension store, looks up the deployed site, checks who owns it, reads its source manifest, compares source hashes against QA and accepted source records, and finally binds the site as the homepage. It returns either a corrected deployed result or a blocked result explaining why acceptance failed.

**Call relations**: `build_ufo_application` calls this after the subagent returns. It uses `_initial_result`, `_proof`, `_blocked`, `_source_acceptance_path`, and `_runtime_root`, and it talks to the hosted-site store before handing the accepted result back to the parent tool.

*Call graph*: calls 5 internal fn (_blocked, _initial_result, _proof, _runtime_root, _source_acceptance_path); 6 external calls (__init__, __init__, model_copy, model_validate_json, authority_member_id, site_url).


##### `ApplicationBuildAcceptance._initial_result`  (lines 882–889)

```
def _initial_result(self, result: ApplicationBuilderResult) -> ApplicationBuilderResult | None
```

**Purpose**: Performs the first quick checks on a worker result before deeper acceptance work. It catches cases like a worker returning a wireframe instead of a build, or naming the wrong source path.

**Data flow**: It receives the worker result. If the status or source path is wrong, it creates and returns a blocked result; if the worker already reported blocked, it returns that result; otherwise it returns nothing to mean deeper checks should continue.

**Call relations**: `accept` calls this at the start. When it finds a problem, it uses `_blocked` and stops the acceptance process early.

*Call graph*: calls 1 internal fn (_blocked); called by 1 (accept).


##### `ApplicationBuildAcceptance._proof`  (lines 891–913)

```
async def _proof(self, result: ApplicationBuilderResult, extension: ExtensionContext) -> ApplicationQaProof | ApplicationBuilderResult
```

**Purpose**: Loads and checks the product QA proof for a build. This proof is the record that browser-based checks passed for the exact source being deployed.

**Data flow**: It receives the worker result and extension context. It reads a QA proof from the store using the child turn id, validates its shape, and rejects the result if proof is missing or the worker reported browser errors. It returns either the validated proof or a blocked result.

**Call relations**: `accept` calls this before looking at the deployed site. It uses `_blocked` for missing or failed proof, and its returned proof supplies the browser batch count and source hash used by later acceptance checks.

*Call graph*: calls 1 internal fn (_blocked); called by 1 (accept); 1 external calls (model_validate).


##### `ApplicationBuildAcceptance._blocked`  (lines 915–928)

```
def _blocked(self, result: ApplicationBuilderResult, reason: str, browser_batches: int) -> ApplicationBuilderResult
```

**Purpose**: Builds a standardized blocked application result. This keeps all acceptance failures reported in the same format.

**Data flow**: It receives the original result, a reason, and a browser-check count. It creates a new `ApplicationBuilderResult` with blocked status, preserving useful checked controls and observed errors. The output is the blocked result.

**Call relations**: It is the common helper used by `accept`, `_initial_result`, and `_proof` whenever acceptance must stop with a clear reason.

*Call graph*: called by 3 (_initial_result, _proof, accept); 1 external calls (__init__).


##### `EditApplicationSourceInput.json_text_edits_are_objects`  (lines 982–1004)

```
def json_text_edits_are_objects(cls, value: object) -> object
```

**Purpose**: Accepts several convenient edit formats and turns them into the one structured format the tool expects. This helps callers provide edits as JSON strings, search/replace patch blocks, or alternating old/new strings.

**Data flow**: It receives the raw `edits` input before normal validation. It converts JSON-looking strings with `json.loads`, parses special `SEARCH`/`REPLACE` blocks, and pairs plain strings when possible. It returns a tuple of edit objects or values for later validation.

**Call relations**: This runs automatically when `EditApplicationSourceInput` is validated. `edit_application_source` then receives clean `old_text` and `new_text` pairs.

*Call graph*: 1 external calls (loads).


##### `_validate_application_imports`  (lines 1007–1018)

```
def _validate_application_imports(source: str) -> None
```

**Purpose**: Enforces the app source import rules. The generated app may import only named items from `ufo/kit`, and it may not export anything.

**Data flow**: It receives the full source text. It scans import and export declarations and raises a clear error if the source imports from another module, uses side-effect or default-style imports, omits `ufo/kit`, exports declarations, or uses the old `UfoAppKit` name. It returns nothing when the imports are acceptable.

**Call relations**: _validate_application_source calls this as the first source check before component and styling checks run.

*Call graph*: called by 1 (_validate_application_source).


##### `_rendered_application_components`  (lines 1021–1048)

```
def _rendered_application_components(source: str) -> set[str]
```

**Purpose**: Finds which approved `ufo/kit` visual components are actually rendered in JSX. This prevents an app from importing kit components only to satisfy the rule while drawing everything itself.

**Data flow**: It receives source text, checks that `mountApp` is used with the root element, collects named `ufo/kit` imports, removes comments and strings, finds JSX component names, subtracts local declarations, and returns the set of imported kit components that appear in the rendered markup. It raises an error if none are rendered.

**Call relations**: _validate_application_source calls it after import validation. It relies on `_local_source_bindings` to avoid false matches and passes its result to `_validate_designed_components`.

*Call graph*: calls 1 internal fn (_local_source_bindings); called by 1 (_validate_application_source).


##### `_validate_designed_components`  (lines 1051–1062)

```
def _validate_designed_components(rendered_kit_components: set[str], designed_kit_components: tuple[str, ...]) -> None
```

**Purpose**: Checks that the source directly renders the kit components promised by the SVG design. This ties the visual contract to the actual app code.

**Data flow**: It receives the set of kit components found in source and the tuple of components named in the design. If any designed component is missing from the rendered source, it raises an error listing the missing names. Otherwise it returns nothing.

**Call relations**: _validate_application_source calls this after `_rendered_application_components`. `_require_application_design` supplies the design-side component list used by source-writing tools.

*Call graph*: called by 1 (_validate_application_source).


##### `_validate_application_styling`  (lines 1065–1095)

```
def _validate_application_styling(source: str) -> None
```

**Purpose**: Enforces project-specific styling rules for generated app pages. These rules keep apps using the design system instead of raw CSS values, reserved attributes, or layout shortcuts the product does not allow.

**Data flow**: It receives source text. It searches for forbidden patterns such as `<style>` tags, reserved `data-slot`, raw Tailwind arbitrary values, unsupported spacing classes, dark-mode variants, and bordered `Stat` components. It raises a repair-oriented error on the first problem and returns nothing if the styling follows the rules.

**Call relations**: _validate_application_source calls this after import and component checks. `write_application_source` and `edit_application_source` surface its error messages to guide the builder's repairs.

*Call graph*: called by 1 (_validate_application_source).


##### `_validate_application_source`  (lines 1098–1104)

```
def _validate_application_source(source: str, designed_kit_components: tuple[str, ...]=()) -> None
```

**Purpose**: Runs the full source-code quality gate for `app.tsx`. It checks imports, rendered components, connection to the design, and styling rules in one place.

**Data flow**: It receives source text and, optionally, the kit components required by the accepted design. It delegates to the import, rendered-component, designed-component, and styling validators. It returns nothing if all checks pass; otherwise it raises a clear validation error.

**Call relations**: Both `write_application_source` and `edit_application_source` call this before compiling and serving source. It is the main source validator used inside the builder workflow.

*Call graph*: calls 4 internal fn (_rendered_application_components, _validate_application_imports, _validate_application_styling, _validate_designed_components); called by 2 (edit_application_source, write_application_source).


##### `_parse_application_design`  (lines 1107–1135)

```
def _parse_application_design(source: str) -> tuple[ElementTree.Element, tuple[float, ...]]
```

**Purpose**: Parses the SVG design and checks its outer frame. The builder must draw in a narrow app lane with an exact width and a bounded, integer height.

**Data flow**: It receives SVG text. It rejects unsafe XML entity declarations, parses the XML, confirms the root is `svg`, reads the `viewBox`, and verifies width and height rules. It returns the parsed root element and viewBox numbers.

**Call relations**: _validate_application_design calls this before inspecting individual SVG elements and regions.

*Call graph*: called by 1 (_validate_application_design); 3 external calls (isfinite, split, fromstring).


##### `_visible_design_element`  (lines 1138–1164)

```
def _visible_design_element(element: ElementTree.Element, tag: str, attributes: dict[str, str]) -> bool
```

**Purpose**: Decides whether a drawing element in the SVG would actually show something. Empty shapes should not count as proof that a design contains visible content.

**Data flow**: It receives an XML element, its tag name, and its attributes. It applies tag-specific checks, such as nonzero radius for circles or nonempty text for text elements. It returns true for visible drawing content and false otherwise.

**Call relations**: _validate_design_element calls this after checking that an element is allowed and safe. Its boolean result contributes to the design's visible drawing count.

*Call graph*: called by 1 (_validate_design_element); 1 external calls (itertext).


##### `_validate_design_attributes`  (lines 1167–1174)

```
def _validate_design_attributes(element: ElementTree.Element) -> None
```

**Purpose**: Rejects SVG attributes that could run code or fetch outside content. This keeps a design file as a passive drawing, not a hidden script or network request.

**Data flow**: It receives one SVG element. It checks each attribute name and value for event handlers or dangerous URL schemes such as `javascript:`, `data:`, `http:`, and `https:`. It raises an error if anything active or external appears, otherwise it returns nothing.

**Call relations**: _validate_design_element calls this for every SVG element while the full design is being validated.

*Call graph*: called by 1 (_validate_design_element).


##### `_validate_design_element`  (lines 1177–1230)

```
def _validate_design_element(element: ElementTree.Element, ids: set[str], regions: list[ElementTree.Element], kit_components: list[str]) -> bool
```

**Purpose**: Checks one SVG element against the design contract. It enforces safe SVG, unique ids, allowed region markers, allowed kit-component markers, and no clip, mask, or filter effects.

**Data flow**: It receives an element plus shared collections for ids, regions, and kit component names. It normalizes tag and attributes, updates those collections when it sees valid markers, rejects forbidden tags or effects, validates attributes, and returns whether this element is a visible drawing element.

**Call relations**: _validate_application_design calls this for every element in the SVG tree. It uses `_validate_design_attributes` and `_visible_design_element` as smaller checks.

*Call graph*: calls 2 internal fn (_validate_design_attributes, _visible_design_element); called by 1 (_validate_application_design); 1 external calls (itertext).


##### `_validate_application_design`  (lines 1233–1260)

```
def _validate_application_design(source: str) -> tuple[tuple[str, ...], tuple[str, ...], int]
```

**Purpose**: Runs the full static validation for an application SVG design. It confirms the file is a safe, visible, properly divided visual contract with named regions and at least one kit component.

**Data flow**: It receives SVG text. It parses the outer SVG, walks every element, counts visible drawing elements, collects named regions and kit components, rejects nested or duplicate regions, and returns the region names, kit component names, and page height.

**Call relations**: This is used by `write_application_design`, `_require_application_design`, `design_ufo_application`, and `build_ufo_application`. Its output feeds browser design auditing and source validation.

*Call graph*: calls 2 internal fn (_parse_application_design, _validate_design_element); called by 4 (_require_application_design, build_ufo_application, design_ufo_application, write_application_design).


##### `_build_application_project`  (lines 1263–1277)

```
async def _build_application_project(ctx: ToolContext, project: str, runtime_root: str | None=None) -> None
```

**Purpose**: Builds the app project and reports compiler failures in a concise way. This proves that the generated page can actually be bundled for the browser.

**Data flow**: It receives a tool context, a project path, and optionally a runtime root. It writes the project config, unpacks the page kit, runs `vite build`, and raises a validation error if the build command fails. It returns nothing when the project builds successfully.

**Call relations**: _compile_application_source`, `write_application_source`, and `edit_application_source` call this. It relies on `unpack_page_kit` to place the shared app runtime files before building.

*Call graph*: called by 3 (_compile_application_source, edit_application_source, write_application_source); 1 external calls (unpack_page_kit).


##### `_compile_application_source`  (lines 1280–1293)

```
async def _compile_application_source(ctx: ToolContext, task: ApplicationBuilderTask, source: str) -> None
```

**Purpose**: Compiles a candidate source file in a temporary runtime project before it is served. This lets the builder catch broken code without replacing the live app file.

**Data flow**: It receives the context, task, and source text. It creates a runtime check project, copies the scaffold's `index.html`, writes the candidate `app.tsx`, and asks `_build_application_project` to build it. It returns nothing if compilation succeeds.

**Call relations**: `write_application_source` and `edit_application_source` call this after source validation. It uses `_runtime_root` and `_build_application_project` so candidate builds stay separate from the served scaffold.

*Call graph*: calls 2 internal fn (_build_application_project, _runtime_root); called by 2 (edit_application_source, write_application_source).


##### `_source_claim_path`  (lines 1296–1300)

```
async def _source_claim_path(ctx: ToolContext, task: ApplicationBuilderTask, turn_id: UUID) -> str
```

**Purpose**: Creates the runtime path used as the ownership marker for a source-writing turn. The marker prevents repeated initial writes from trampling the same candidate.

**Data flow**: It receives the context, task, and turn id. It hashes the source path and combines it with the turn id to make a stable claim-file path under tool output. It returns that runtime path.

**Call relations**: `write_application_source` uses this to claim the initial write. `_require_application_source` uses the same path to confirm that a candidate source exists before read or edit repair tools run.

*Call graph*: called by 2 (_require_application_source, write_application_source); 1 external calls (sha256).


##### `_runtime_root`  (lines 1303–1304)

```
async def _runtime_root(ctx: ToolContext) -> str
```

**Purpose**: Finds the filesystem root used for runtime-owned tool-output files. This gives containment scripts a safe base directory.

**Data flow**: It receives a tool context. It asks the sandbox for the runtime path of `tool-output`, takes its parent directory, and returns that as a string.

**Call relations**: Many tools and helpers use this when running small containment scripts, including design acceptance, source claims, source reads, build delegation claims, and final acceptance checks.

*Call graph*: called by 9 (accept, _compile_application_source, _require_application_design, _require_application_source, build_ufo_application, edit_application_source, read_application_source, write_application_design, write_application_source); 1 external calls (PurePosixPath).


##### `_design_path`  (lines 1307–1308)

```
def _design_path(task: ApplicationBuilderTask) -> str
```

**Purpose**: Returns the fixed location of the design SVG inside the application scaffold. Keeping this path fixed is part of the builder contract.

**Data flow**: It receives a builder task and appends `application-design.svg` to the scaffold path. It returns that full workspace path as text.

**Call relations**: Design-writing and design-requiring helpers call this whenever they need the canonical design file path.

*Call graph*: called by 4 (_design_claim_path, _require_application_design, accept_application_wireframe, write_application_design).


##### `_design_claim_path`  (lines 1311–1315)

```
async def _design_claim_path(ctx: ToolContext, task: ApplicationBuilderTask, turn_id: UUID) -> str
```

**Purpose**: Creates the runtime path used to claim ownership of a design-writing turn. This stops competing or repeated design writes from silently replacing the accepted design.

**Data flow**: It receives the context, task, and turn id. It hashes the fixed design path and combines that hash with the turn id to form a claim-file path under tool output. It returns the runtime path.

**Call relations**: `write_application_design` uses this while sealing a design. `_require_application_design` uses it to confirm that design work completed before source work begins.

*Call graph*: calls 1 internal fn (_design_path); called by 2 (_require_application_design, write_application_design); 1 external calls (sha256).


##### `application_design_acceptance_relative`  (lines 1318–1324)

```
def application_design_acceptance_relative(design_path: str, turn_id: UUID) -> str
```

**Purpose**: Builds the relative runtime path where an accepted design SVG is sealed for one builder turn. This is a product-owned copy separate from the editable workspace file.

**Data flow**: It receives a design path and turn id. It hashes the design path and formats a relative `accepted.svg` path. It returns that relative path string.

**Call relations**: `write_application_design` calls this before converting the path into a runtime path and publishing the sealed SVG.

*Call graph*: called by 1 (write_application_design); 1 external calls (sha256).


##### `application_design_evidence_relative`  (lines 1327–1333)

```
def application_design_evidence_relative(design_path: str, turn_id: UUID) -> str
```

**Purpose**: Builds the relative runtime path where accepted design evidence is stored. The evidence records details such as the design hash, regions, and kit components.

**Data flow**: It receives a design path and turn id. It hashes the design path and formats a relative JSON evidence path. It returns that relative path string.

**Call relations**: `write_application_design` calls this while preparing the sealed evidence that later source validation can trust.

*Call graph*: called by 1 (write_application_design); 1 external calls (sha256).


##### `_source_candidate_path`  (lines 1336–1342)

```
async def _source_candidate_path(ctx: ToolContext, task: ApplicationBuilderTask, turn_id: UUID) -> str
```

**Purpose**: Creates the runtime path for the current candidate `app.tsx`. Candidate source is where failed or in-progress edits live before they are compiled and served.

**Data flow**: It receives the context, task, and turn id. It hashes the source path and combines it with the turn id to make a stable candidate `.tsx` path under tool output. It returns that runtime path.

**Call relations**: `write_application_source` writes the first candidate there, `read_application_source` reads from it, and `edit_application_source` updates it during repairs.

*Call graph*: called by 3 (edit_application_source, read_application_source, write_application_source); 1 external calls (sha256).


##### `_render_application_design`  (lines 1345–1396)

```
async def _render_application_design(ctx: ToolContext, candidate_path: str, preview_path: str, names: tuple[str, ...], page_height: int) -> tuple[ApplicationAuditRegion, ...]
```

**Purpose**: Runs a browser-based audit of the SVG design. Static XML checks cannot reliably know visual bounds and overlaps, so this renders the design and measures what is actually visible.

**Data flow**: It receives the context, candidate SVG path, preview image path, expected region names, and page height. It writes the audit script, runs Node in the sandbox, parses the JSON region measurements, checks names and overlaps, and returns the measured regions. It raises clear errors for unsafe content, out-of-bounds drawing, accidental overlap, or malformed audit output.

**Call relations**: `write_application_design` calls this after static design validation. The returned regions are used for size, fold-position, and evidence records.

*Call graph*: called by 1 (write_application_design); 2 external calls (search, application_region_relation).


##### `_source_acceptance_path`  (lines 1399–1405)

```
async def _source_acceptance_path(ctx: ToolContext, task: ApplicationBuilderTask, turn_id: UUID) -> str
```

**Purpose**: Creates the runtime path that records the hash of the accepted source. This lets final deployment acceptance compare what was built, what passed QA, and what was deployed.

**Data flow**: It receives the context, task, and turn id. It hashes the source path and combines it with the turn id to make an `accepted` marker path. It returns that runtime path.

**Call relations**: `write_application_source` and `edit_application_source` write the accepted source hash there after a successful build. `ApplicationBuildAcceptance.accept` reads it during final deployment acceptance.

*Call graph*: called by 3 (accept, edit_application_source, write_application_source); 1 external calls (sha256).


##### `_require_application_source`  (lines 1408–1417)

```
async def _require_application_source(ctx: ToolContext, task: ApplicationBuilderTask) -> None
```

**Purpose**: Checks that the initial source candidate has been claimed before repair tools run. This prevents reading or editing a source candidate that does not exist yet.

**Data flow**: It receives the context and task. It runs a containment script against the source claim path. If the claim is missing, it raises a user-facing validation error; if the check itself fails, it raises a runtime error; otherwise it returns nothing.

**Call relations**: `read_application_source` and `edit_application_source` call this before touching the candidate source. It uses `_source_claim_path` and `_runtime_root`.

*Call graph*: calls 2 internal fn (_runtime_root, _source_claim_path); called by 2 (edit_application_source, read_application_source).


##### `_require_application_design`  (lines 1420–1441)

```
async def _require_application_design(ctx: ToolContext, task: ApplicationBuilderTask) -> tuple[str, ...]
```

**Purpose**: Checks that a valid design has been accepted before source code is written or edited. It also returns the kit components the source must render.

**Data flow**: It receives the context and task. It verifies the design claim, reads the design SVG from the scaffold, optionally checks its hash against a member-accepted wireframe, validates the SVG, and returns the tuple of kit components named by the design.

**Call relations**: `write_application_source` and `edit_application_source` call this before source validation. It ties the source phase back to `write_application_design` or `accept_application_wireframe`.

*Call graph*: calls 4 internal fn (_design_claim_path, _design_path, _runtime_root, _validate_application_design); called by 2 (edit_application_source, write_application_source); 1 external calls (sha256).


##### `write_application_design`  (lines 1444–1598)

```
async def write_application_design(ctx: ToolContext, args: WriteApplicationDesignInput) -> ToolResult
```

**Purpose**: Writes and seals the SVG visual contract for the application. This is the required design step before source code can be written.

**Data flow**: It receives tool context and SVG content. It validates the SVG, renders it through the browser audit, checks region size and fold rules, creates evidence JSON, atomically claims and publishes accepted design files, writes the design into the scaffold, and returns JSON with path, digest, size, height, and rendered region details. If something fails after claiming, it tries to clean up the claim or accepted files.

**Call relations**: This is the handler for the builder's design tool and is also called by `accept_application_wireframe`. It uses the design validators, render audit, claim-path helpers, accepted-path helpers, and cleanup helper.

*Call graph*: calls 8 internal fn (_complete_application_design_cleanup, _design_claim_path, _design_path, _render_application_design, _runtime_root, _validate_application_design, application_design_acceptance_relative, application_design_evidence_relative); called by 1 (accept_application_wireframe); 7 external calls (__init__, __init__, __init__, sha256, dumps, application_design_region_fold_failure, application_design_region_size_failure).


##### `accept_application_wireframe`  (lines 1601–1612)

```
async def accept_application_wireframe(ctx: ToolContext, _args: AcceptApplicationWireframeInput) -> ToolResult
```

**Purpose**: Turns a member-approved staged wireframe into the build turn's accepted design. It validates and seals the existing SVG instead of generating a new one.

**Data flow**: It reads the current task, confirms the task has an accepted design digest, reads `application-design.svg` from the scaffold, and passes that SVG into `write_application_design`. The output is the same design-writing tool result.

**Call relations**: This is available only inside the builder profile. It calls `_design_path` to find the staged SVG and delegates the real validation and sealing work to `write_application_design`.

*Call graph*: calls 2 internal fn (_design_path, write_application_design); 1 external calls (__init__).


##### `_complete_application_design_cleanup`  (lines 1615–1631)

```
async def _complete_application_design_cleanup(ctx: ToolContext, program: str, *args: str) -> tuple[ExecResult | None, tuple[str, ...]]
```

**Purpose**: Runs design cleanup even if the surrounding operation is cancelled or interrupted. This reduces the chance of leaving stale claim files or sealed files behind after a failed design write.

**Data flow**: It receives a context, a small cleanup program, and its arguments. It starts the cleanup script as an asynchronous task, shields it from cancellation until it finishes, collects interruption notes, and returns either the script result or no result plus failure messages.

**Call relations**: `write_application_design` calls this from its error path. It is the helper that tries to undo either a design claim or an accepted design publication.

*Call graph*: called by 1 (write_application_design); 2 external calls (create_task, shield).


##### `read_application_source`  (lines 1634–1688)

```
async def read_application_source(ctx: ToolContext, args: ReadApplicationSourceInput) -> ToolResult
```

**Purpose**: Returns small, targeted excerpts from the current candidate `app.tsx` during repair. It avoids dumping the whole file while still giving enough context to fix failed exact replacements.

**Data flow**: It receives search terms. It confirms a source candidate exists, reads the candidate, counts matching lines for each term, builds windows around the file start, file end, and selected matches, stays under a maximum excerpt size, and returns the excerpt text.

**Call relations**: This is the read tool for the builder's source repair loop. It uses `_require_application_source`, `_source_candidate_path`, and `_runtime_root`; `limit_application_builder_repair_reads` can restrict repeated calls after audit failures.

*Call graph*: calls 3 internal fn (_require_application_source, _runtime_root, _source_candidate_path); 2 external calls (__init__, __init__).


##### `edit_application_source`  (lines 1691–1774)

```
async def edit_application_source(ctx: ToolContext, args: EditApplicationSourceInput) -> ToolResult
```

**Purpose**: Applies exact text replacements to the current candidate source, validates and compiles the result, and promotes it to the served app only if everything passes. This makes repairs precise and safe.

**Data flow**: It receives one or more old/new text edits. It confirms source and design exist, reads the candidate, verifies each old text occurs exactly once and edits do not overlap, applies replacements, writes the candidate, validates source rules, and compiles it. If validation or candidate compilation fails, it returns a tool failure explaining that only the candidate changed. If all checks pass, it writes the served `app.tsx`, builds the real project, records the accepted source hash, and returns JSON with path, replacement count, and size.

**Call relations**: This is the builder's repair write tool. It uses source/design requirement helpers, candidate and acceptance paths, source validation, candidate compilation, and final project build.

*Call graph*: calls 8 internal fn (_build_application_project, _compile_application_source, _require_application_design, _require_application_source, _runtime_root, _source_acceptance_path, _source_candidate_path, _validate_application_source); 6 external calls (__init__, __init__, __init__, __init__, sha256, dumps).


##### `write_application_source`  (lines 1777–1821)

```
async def write_application_source(ctx: ToolContext, args: WriteApplicationSourceInput) -> ToolResult
```

**Purpose**: Writes the initial complete `app.tsx` for the build turn. It is allowed only once, after the design is fixed.

**Data flow**: It receives full source text. It confirms the design exists, claims the source write, stores the text as the candidate, validates the source, compiles the candidate, writes it to the served scaffold, builds the real project, records the accepted source hash, and returns JSON with path and size. If validation or compilation fails, the candidate is retained for repair instead of allowing another full rewrite.

**Call relations**: This is the first source-writing tool in the builder profile. Later repairs should use `read_application_source` and `edit_application_source`, not call this again.

*Call graph*: calls 8 internal fn (_build_application_project, _compile_application_source, _require_application_design, _runtime_root, _source_acceptance_path, _source_candidate_path, _source_claim_path, _validate_application_source); 4 external calls (__init__, __init__, sha256, dumps).


##### `design_ufo_application`  (lines 1824–1882)

```
async def design_ufo_application(ctx: ToolContext, args: DesignUfoApplicationInput) -> ToolResult
```

**Purpose**: Runs the application builder in wireframe-only mode and shares the resulting SVG with the member. It stores the exact approved SVG so a later build can use the same design.

**Data flow**: It receives an application name, prompt, and optional revision. It ensures the scaffold exists, spawns the builder subagent in wireframe phase, validates the worker result and SVG digest, stores a preview, shares the SVG artifact, saves the wireframe content and digest in the extension store, and returns a ready or blocked wireframe result.

**Call relations**: This is the public wireframe delegation tool. It calls `_ensure_application_scaffold` and `_validate_application_design`, then uses artifact sharing and store APIs so `build_ufo_application` can pick up the accepted wireframe later.

*Call graph*: calls 4 internal fn (share_artifact, store_preview, _ensure_application_scaffold, _validate_application_design); 8 external calls (__init__, __init__, __init__, __init__, __init__, spawn, sha256, PurePosixPath).


##### `_ensure_application_scaffold`  (lines 1885–1893)

```
async def _ensure_application_scaffold(ctx: ToolContext) -> None
```

**Purpose**: Creates the minimal app scaffold files if they are missing. These files give the builder a stable place to write source and preview the app.

**Data flow**: It receives a tool context. For `index.html`, placeholder `app.tsx`, and `preview.html`, it checks whether the file can be read; if not, it writes the default content. It returns nothing.

**Call relations**: `design_ufo_application` and `build_ufo_application` call this before spawning the builder so the worker always starts from the expected project structure.

*Call graph*: called by 2 (build_ufo_application, design_ufo_application).


##### `build_ufo_application`  (lines 1896–1983)

```
async def build_ufo_application(ctx: ToolContext, _args: BuildUfoApplicationInput) -> ToolResult
```

**Purpose**: Delegates one full application build to the specialized builder worker and returns the accepted result. It is the main tool used when a member asks to create an app.

**Data flow**: It receives tool context and no meaningful input fields. It claims the parent turn so delegation happens once, stores redeploy and audit contract details when needed, ensures the scaffold exists, loads any stored accepted wireframe, spawns the builder subagent with the objective and fixed paths, and then either reports a blocked result or passes the worker output through `ApplicationBuildAcceptance.accept`. If a stored wireframe led to a deployed app, it deletes that spent wireframe. It returns the final result as JSON text.

**Call relations**: This is the public build delegation tool. It calls `_ensure_application_scaffold`, `_runtime_root`, `_validate_application_design`, and `ApplicationBuildAcceptance`, and it starts the `ufo_application_builder` profile.

*Call graph*: calls 3 internal fn (_ensure_application_scaffold, _runtime_root, _validate_application_design); 9 external calls (__init__, __init__, __init__, __init__, __init__, spawn, sha256, format, format).


##### `limit_application_builder_repair_reads`  (lines 1986–2021)

```
async def limit_application_builder_repair_reads(ctx: HookContext) -> Deny | None
```

**Purpose**: Limits how many source-read calls the builder can make after product audit has requested repairs. This pushes the worker to edit, retest, and deploy instead of endlessly inspecting.

**Data flow**: It receives a hook context before a tool runs. If the turn is not the application builder, or the tool is not source read/edit, it does nothing. If audit attempts exist, it resets the read counter on edits, increments it on reads, and returns a denial after the configured read limit.

**Call relations**: This hook runs around builder tool use. It coordinates with audit attempt records and affects `read_application_source` and `edit_application_source` calls.

*Call graph*: 2 external calls (__init__, format).


##### `enforce_application_builder_phase`  (lines 2024–2039)

```
async def enforce_application_builder_phase(ctx: HookContext) -> Deny | None
```

**Purpose**: Prevents the builder from doing the wrong kind of work in the wrong phase. In wireframe mode, it may only write the design; after a member accepted a wireframe, it may not redesign it.

**Data flow**: It receives a hook context before a tool call. It ignores non-builder turns, reads the builder task, and returns a denial if the requested tool conflicts with the task phase or accepted design digest. Otherwise it returns nothing.

**Call relations**: This hook protects the tools defined in the builder profile. It keeps `design_ufo_application` wireframe runs from drifting into source, QA, or deployment work.

*Call graph*: 1 external calls (__init__).


##### `is_application_creation_request`  (lines 2042–2046)

```
def is_application_creation_request(text: str) -> bool
```

**Purpose**: Detects whether a member message looks like a request to create an application rather than a general website or other platform app. It is a routing helper for the main assistant.

**Data flow**: It receives message text. It checks one pattern for app-creation language and another pattern for excluded site/platform terms. It returns true only when the text looks like a UFO app creation request.

**Call relations**: `enforce_application_creation_route` calls this before deciding whether the main assistant must load the app-creation skill.

*Call graph*: called by 1 (enforce_application_creation_route).


##### `enforce_application_creation_route`  (lines 2049–2072)

```
async def enforce_application_creation_route(ctx: HookContext) -> Deny | None
```

**Purpose**: Forces likely app-creation requests through the `create-application` skill before any other tool is used. This keeps app builds on the safe, intended workflow.

**Data flow**: It receives a hook context before tool use. It ignores turns that are not main-member requests or do not look like app creation. For matching requests, it allows the first `load_skill` only if it loads `create-application`, records that routing happened, and denies other tools until then.

**Call relations**: This hook uses `is_application_creation_request` and the extension store. It shapes the main assistant's behavior before `build_ufo_application` or related tools become relevant.

*Call graph*: calls 1 internal fn (is_application_creation_request); 1 external calls (__init__).


##### `require_application_builder_qa`  (lines 2075–2088)

```
async def require_application_builder_qa(ctx: HookContext) -> Deny | None
```

**Purpose**: Blocks application deployment until product QA proof exists and is valid. This is the deployment gate for builder-created apps.

**Data flow**: It receives a hook context. For application-builder turns, it looks up QA proof for the turn, denies deployment if proof is missing, validates the proof if present, and returns nothing when deployment may proceed.

**Call relations**: This hook is used before deployment tools. `ApplicationBuildAcceptance.accept` later checks the same proof more deeply against the deployed source.

*Call graph*: 2 external calls (__init__, model_validate).


### `extensions/sites/ufo_ext_sites/tools.py`

`orchestration` · `tool invocation / request handling`

This file exists so an agent can turn project files into something a member can actually open in a browser. Without it, an agent might run a build command, but there would be no safe, repeatable way to clean up old servers, prove the new server is ready, create a permanent link, save the site's source files, or enforce who is allowed to publish or re-share it.

The file works like a careful stage manager. First it keeps all file paths inside the workspace or tool-output area, so a model-provided path cannot accidentally point somewhere dangerous. When starting a server, it clears the chosen log file safely, stops anything already using the port, resets the background task record, launches the command, and then checks the port until it is truly listening. This avoids returning a URL before the page is ready.

For static deployments, it also lists and hashes the files, uploads them into the blob store as the site's source of record, registers the port as a hosted site, and asks the preview service to take screenshots for cards and artifacts. For dynamic published apps, it hosts the running sandbox server but does not store static source. The file also contains stricter flows for UFO application builders: product QA must pass before deployment, and homepage updates have extra permission checks because binding a homepage changes who can view the page.

#### Function details

##### `StartServerInput.validate_port`  (lines 476–481)

```
def validate_port(self) -> 'StartServerInput'
```

**Purpose**: Checks that a start-server request is sensible before any server is launched. It prevents an empty command from being treated as a real command and rejects invalid port numbers.

**Data flow**: It reads the parsed input fields: command and port. If the command is blank, or the port is outside the normal 1 to 65535 range, it raises a validation error; otherwise it returns the same input object unchanged.

**Call relations**: This validation runs as part of creating a StartServerInput object. That means start_server receives already-checked arguments before it calls the lower-level serving flow.


##### `_json_result`  (lines 521–522)

```
def _json_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: Turns a plain Python dictionary into the standard tool result shape returned to the agent. It is a small helper so all successful tools return JSON text in the same way.

**Data flow**: It takes a dictionary, converts it to a JSON string, wraps that string as text content, and returns a ToolResult containing it. It does not change any outside state.

**Call relations**: The public tool handlers use this at the end of successful work, including website, start_server, qa_ufo_application, deploy_website, publish_website, set_homepage, and the homepage redeploy path.

*Call graph*: called by 7 (_redeploy_homepage, deploy_website, publish_website, qa_ufo_application, set_homepage, start_server, website); 3 external calls (__init__, __init__, dumps).


##### `_free_log`  (lines 525–545)

```
async def _free_log(ctx: ToolContext, log_path: str) -> None
```

**Purpose**: Safely clears the chosen log filename before a new background server writes to it. This matters because a log path can be chosen or predicted by a model, and a careless redirect could follow a planted link and overwrite the wrong file.

**Data flow**: It receives the tool context and a log path. It decides whether the path belongs under the runtime output area or the workspace, then runs a guarded cleanup script inside the sandbox; on failure it raises an error.

**Call relations**: _serve calls this before launching any server. It prepares the log slot so the later shell redirect can create the log safely rather than overwrite through a link.

*Call graph*: called by 1 (_serve); 1 external calls (PurePosixPath).


##### `_stop_server`  (lines 548–553)

```
async def _stop_server(ctx: ToolContext, port: int) -> None
```

**Purpose**: Stops any process already listening on the chosen port inside the sandbox. This makes a new deployment take ownership of the port instead of colliding with an old server.

**Data flow**: It receives a context and port number, runs a sandbox Python program that finds listeners on that port and terminates them, and raises an error if the cleanup fails.

**Call relations**: _serve calls this near the start of every server launch. It is part of the reset sequence before the new command is started.

*Call graph*: called by 1 (_serve).


##### `_stop_server_task`  (lines 556–573)

```
async def _stop_server_task(ctx: ToolContext, command: str, base: str, pid: str) -> None
```

**Purpose**: Stops a background server task that this file previously launched. It is used when a server started but then failed the readiness check, so the system does not leave a half-working process behind.

**Data flow**: It receives the context, the server command, the task journal base path, and the expected process id. It asks the sandbox to stop that exact task and waits for its task record to finish; if it does not stop cleanly, it raises an error.

**Call relations**: _serve calls this on failure paths after a detached server task has begun. It cleans up before _serve reports that serving failed.

*Call graph*: called by 1 (_serve).


##### `_reset_server_task`  (lines 576–594)

```
async def _reset_server_task(ctx: ToolContext, base: str) -> None
```

**Purpose**: Clears the saved task journal used for a background server. This prevents a new launch from accidentally reattaching to an old recorded task instead of starting a fresh server.

**Data flow**: It receives a context and task base path, runs a sandbox shell reset script, waits a short time for any old task to stop, then removes the task record files. If the reset cannot complete, it raises an error.

**Call relations**: _serve calls this after freeing the port and before starting the detached task. It makes repeated tool calls with the same identity safe and predictable.

*Call graph*: called by 1 (_serve).


##### `_log_tail`  (lines 597–602)

```
async def _log_tail(ctx: ToolContext, log_path: str) -> str
```

**Purpose**: Reads the last few lines of a server log so a failure message can include what the server said. This helps the agent see the real error instead of a vague 'server did not start'.

**Data flow**: It receives a context and log path, runs a short tail command in the sandbox, and returns the captured standard output as a string. Missing logs are treated as empty output.

**Call relations**: _serve calls this when startup or readiness fails. The returned text is passed into the failure summary when available.

*Call graph*: called by 1 (_serve); 1 external calls (shell_path).


##### `ServeFailed.__init__`  (lines 614–616)

```
def __init__(self, failure: ToolFailure) -> None
```

**Purpose**: Creates an exception that carries a ready-made tool failure. It lets the lower-level serving code stop normal control flow while preserving the exact message and diagnostics that should be returned to the agent.

**Data flow**: It receives a ToolFailure, stores its summary as the exception message, and keeps the full failure object on the exception. Nothing else is changed.

**Call relations**: _serve_failed constructs this exception for _serve. The public tools catch ServeFailed and return the embedded failure result instead of crashing.

*Call graph*: called by 1 (_serve_failed).


##### `_serve_failed`  (lines 619–638)

```
def _serve_failed(summary: str, result: ExecResult, project: str, port: int) -> ServeFailed
```

**Purpose**: Builds a clear failure report for a server that could not be brought up. It explicitly says that the port was already freed, so the member and agent understand that the old server is no longer running.

**Data flow**: It receives a human summary, the sandbox execution result, the project path, and the port. It packages the exit code, output streams, timeout, and port-cleanup effect into a ToolFailure, then wraps that in ServeFailed.

**Call relations**: _serve uses this on every failed startup or readiness path. It hands the resulting ServeFailed exception to the public tool handlers, which turn it into the tool response.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_serve); 3 external calls (__init__, __init__, __init__).


##### `_serve`  (lines 641–733)

```
async def _serve(ctx: ToolContext, command: str, project: str, port: int, log_path: str) -> dict[str, object]
```

**Purpose**: Starts a website or app server safely and only returns once the port is reachable. It is the core serving routine used by preview, deploy, publish, and homepage redeploy flows.

**Data flow**: It receives a command, project directory, port, and log path. It clears the log name, frees the port, resets the background task record, launches the command with PORT set, probes the port until it opens, and returns the sandbox-local URL, port, and log path. If anything goes wrong, it stops what it started and raises ServeFailed with diagnostics.

**Call relations**: start_server, deploy_website, publish_website, and _redeploy_homepage all rely on this. Inside, it delegates cleanup to _free_log, _stop_server, _reset_server_task, _stop_server_task, reads logs through _log_tail, and formats failures through _serve_failed.

*Call graph*: calls 6 internal fn (_free_log, _log_tail, _reset_server_task, _serve_failed, _stop_server, _stop_server_task); called by 4 (_redeploy_homepage, deploy_website, publish_website, start_server); 3 external calls (sha256, quote, shell_path).


##### `_site_media_type`  (lines 736–738)

```
def _site_media_type(path: str) -> str
```

**Purpose**: Chooses the browser content type for a source file based on its filename extension. This helps hosted static files be served as HTML, CSS, images, JavaScript, and so on instead of as unknown bytes.

**Data flow**: It receives a path string, looks at the part after the final dot, and returns a media type string. Text types get a UTF-8 charset added; unknown extensions become a generic binary type.

**Call relations**: _promote_source calls this while building the source manifest for a deployed static site. The chosen value is stored with each file record.

*Call graph*: called by 1 (_promote_source).


##### `_source_listing`  (lines 741–762)

```
async def _source_listing(ctx: ToolContext, project: str) -> dict[str, dict[str, object]]
```

**Purpose**: Lists the files that make up a static site and records their sizes and SHA-256 hashes, which are digital fingerprints of the bytes. It also rejects directories that are too large or empty to host as a static site.

**Data flow**: It receives a context and project path, runs a sandbox script that walks the directory while skipping things like .git and node_modules, and parses the JSON file map it prints. On script failure or bad output, it raises an error explaining that a larger or dynamic app should use publish_website.

**Call relations**: _served_directory calls this to understand either the given site directory or the built output directory. Its listing later feeds _promote_source.

*Call graph*: called by 1 (_served_directory); 1 external calls (loads).


##### `_promote_source`  (lines 765–806)

```
async def _promote_source(ctx: ToolContext, project: str, conversation_id: UUID, name: str, listing: dict[str, dict[str, object]]) -> str
```

**Purpose**: Copies the deployed static site's files into the workspace blob store and creates the manifest that records them. This makes the deployed site reproducible later without needing the sandbox server.

**Data flow**: It receives the context, project directory, conversation id, site name, and file listing. It creates a unique storage prefix, builds a SourceManifest with file sizes, media types, and hashes, uploads the files either through presigned upload URLs or direct streams, and returns the manifest as JSON.

**Call relations**: deploy_website and _redeploy_homepage call this before registering or updating a static site. It uses _site_media_type for each file and transfer when the blob store provides upload URLs.

*Call graph*: calls 1 internal fn (_site_media_type); called by 2 (_redeploy_homepage, deploy_website); 4 external calls (__init__, __init__, transfer, uuid4).


##### `_pictured`  (lines 809–830)

```
async def _pictured(ctx: ToolContext, name: str, port: int, conversation_id: UUID) -> str | None
```

**Purpose**: Attempts to create preview imagery for a hosted site without making the whole deployment fail if the picture cannot be drawn. The site link is the important deliverable; the image is decoration.

**Data flow**: It receives the context, site name, port, and conversation id. It calls _illustrate; if normal rendering fails, it returns a shortened error string, but if the sandbox or terminal itself is unavailable, it lets that serious infrastructure error rise. On success it returns None.

**Call relations**: deploy_website, publish_website, and _redeploy_homepage call this after the site has already been registered or updated. It hands the actual preview work to _illustrate.

*Call graph*: calls 1 internal fn (_illustrate); called by 3 (_redeploy_homepage, deploy_website, publish_website); 1 external calls (clipped).


##### `_illustrate`  (lines 833–855)

```
async def _illustrate(ctx: ToolContext, name: str, port: int, conversation_id: UUID) -> None
```

**Purpose**: Takes screenshots of the newly hosted page and saves them as the site's preview and share card imagery. These images are what users see in artifact cards or link previews.

**Data flow**: It receives the context, site name, port, and the conversation id of the site row. It asks the rendering service to photograph the hosted port, stores the preview if one is returned, and then asks the share-card code to draw the link card.

**Call relations**: _pictured calls this and catches ordinary rendering errors around it. This function talks to the hosted-site store and the share card renderer after registration has made the site live.

*Call graph*: calls 1 internal fn (render_site_preview); called by 1 (_pictured); 2 external calls (__init__, draw_from_page).


##### `_refuse_before_serving`  (lines 858–898)

```
async def _refuse_before_serving(ctx: ToolContext, raw_name: str, port: int, visibility: Visibility | None) -> tuple[str, HostedSite | None]
```

**Purpose**: Checks whether a site is allowed to be hosted before stopping anything currently on the port. This protects an existing live site from being taken down by a deployment that would later be refused for permission or visibility reasons.

**Data flow**: It receives the context, requested site name, port, and optional visibility. It finds the acting member, normalizes the site name, validates that a public URL can be formed, asks the hosted-site registry whether the write may proceed, and returns the normalized name plus any site that would be displaced.

**Call relations**: deploy_website and publish_website call this before they start serving. Later, _host repeats the checks at write time to guard against races, but this early check avoids unnecessary downtime.

*Call graph*: called by 2 (deploy_website, publish_website); 5 external calls (__init__, __init__, authority_member_id, site_name, site_url).


##### `_host`  (lines 901–945)

```
async def _host(ctx: ToolContext, raw_name: str, port: int, visibility: Visibility | None, manifest: str | None) -> dict[str, object]
```

**Purpose**: Registers a listening sandbox port as a permanent hosted site link. This is the step that turns a local server into a shareable site object.

**Data flow**: It receives the context, raw site name, port, optional visibility, and optional static-source manifest. It confirms ownership and speaker rules, computes the hosted URL, writes the registration row, and returns the public details: site name, effective visibility, object name, and site URL.

**Call relations**: deploy_website and publish_website call this after _serve proves the port is listening. Preview creation through _pictured happens afterward so the hosted row already exists.

*Call graph*: called by 2 (deploy_website, publish_website); 7 external calls (__init__, __init__, authority_member_id, effective_visibility, site_object_name, site_name, site_url).


##### `_build_failed`  (lines 948–968)

```
def _build_failed(command: str, project: str, result: ExecResult) -> ToolFailure
```

**Purpose**: Creates a clear tool failure for a build or install command that did not succeed. It keeps both standard output and standard error because build tools often put useful messages in either place.

**Data flow**: It receives the command, project path, and execution result. It decides whether the command failed by timeout or exit code, packages command diagnostics, and returns a ToolFailure that says nothing was served or hosted.

**Call relations**: website uses this when a build command fails, and publish_website uses it when an install command fails. The caller converts the failure into the final tool result.

*Call graph*: called by 2 (publish_website, website); 2 external calls (__init__, __init__).


##### `website`  (lines 971–980)

```
async def website(ctx: ToolContext, args: WebsiteInput) -> ToolResult
```

**Purpose**: Runs a build command in a project directory and reports the files now present there. It is useful when an agent needs to compile or prepare a website before serving or deploying it.

**Data flow**: It receives the tool context and WebsiteInput. It resolves the project path into the workspace, runs the requested command in the sandbox with a build timeout, returns a build failure if needed, otherwise lists the directory and returns the project path plus file names as JSON.

**Call relations**: This is a public tool handler registered in SITES_TOOLS. It relies on _build_failed for bad builds and _json_result for the success response.

*Call graph*: calls 2 internal fn (_build_failed, _json_result); 2 external calls (quote, workspace_path).


##### `start_server`  (lines 983–1006)

```
async def start_server(ctx: ToolContext, args: StartServerInput) -> ToolResult
```

**Purpose**: Starts a scratch server in the sandbox and returns a local URL once it is reachable. Unlike deploying, it does not create a permanent hosted site link.

**Data flow**: It receives the context and StartServerInput, chooses a port and log path, resolves the project directory, chooses either the supplied command or a simple static-file server, then calls _serve. On serving failure it returns the embedded failure; on success it returns the URL, port, log, and project path as JSON.

**Call relations**: This is a public tool handler registered in SITES_TOOLS. It is a thin, user-facing wrapper around _serve, with extra restrictions for the special UFO application builder profile.

*Call graph*: calls 2 internal fn (_json_result, _serve); 1 external calls (workspace_path).


##### `_application_audit_attempts`  (lines 1009–1017)

```
async def _application_audit_attempts(ctx: ToolContext) -> int
```

**Purpose**: Reads how many product-audit attempts have already been used in the current turn. This enforces the limit on repeated UFO application QA attempts.

**Data flow**: It receives the context, reads a keyed value from the extension store, returns zero if none is stored, returns the integer if valid, and raises an error if the stored value has the wrong type.

**Call relations**: _audit_builder_application calls this at the start of an audit. The result decides whether another audit may run.

*Call graph*: called by 1 (_audit_builder_application); 1 external calls (format).


##### `_application_audit_feedback`  (lines 1020–1032)

```
async def _application_audit_feedback(ctx: ToolContext, issues: tuple[ApplicationAuditIssue, ...], attempts: int) -> ApplicationAuditFeedback
```

**Purpose**: Records a failed audit attempt and formats the issues into feedback the builder can act on. It tells the builder how many repair attempts remain.

**Data flow**: It receives the context, a tuple of audit issues, and the previous attempt count. It increments and stores the count, creates an ApplicationAuditFeedback object with the new attempt number, remaining attempts, and issues, and returns it.

**Call relations**: _audit_builder_application calls this whenever the browser audit fails, produces an unreadable report, or finds product problems. qa_ufo_application then returns the feedback JSON to the builder.

*Call graph*: called by 1 (_audit_builder_application); 2 external calls (__init__, format).


##### `_accepted_application_design`  (lines 1035–1070)

```
async def _accepted_application_design(ctx: ToolContext) -> tuple[AcceptedApplicationDesignEvidence, str, str]
```

**Purpose**: Loads the design document and evidence that were previously accepted for the UFO application. It verifies that the evidence really matches the design by checking a SHA-256 hash.

**Data flow**: It computes the runtime paths for the accepted design and evidence, reads both files with size limits, parses the evidence JSON, hashes the design text, and compares the hash with the evidence. It returns the parsed evidence plus both file paths, or raises an error if anything is missing or inconsistent.

**Call relations**: _audit_builder_application calls this before running the browser audit. The audit uses these paths and evidence to ensure the built app is judged against the accepted design.

*Call graph*: called by 1 (_audit_builder_application); 4 external calls (model_validate_json, sha256, application_design_acceptance_relative, application_design_evidence_relative).


##### `_audit_builder_application`  (lines 1073–1186)

```
async def _audit_builder_application(ctx: ToolContext, project: str) -> ApplicationAuditReport | ApplicationAuditFeedback
```

**Purpose**: Runs the deterministic browser-based product audit for the UFO application builder. It checks whether the built app behaves and looks as required before deployment is allowed.

**Data flow**: It receives the context and project path. It checks remaining attempts, prepares report and screenshot paths, loads accepted design evidence, writes the audit script into the sandbox, runs it with Node, reads and validates the report, compares it to the saved contract, and returns either a full passing report or structured feedback with repair issues.

**Call relations**: qa_ufo_application calls this as its main work. It uses _application_audit_attempts, _accepted_application_design, _application_audit_feedback, and the application_audit module's validation and verdict logic.

*Call graph*: calls 3 internal fn (_accepted_application_design, _application_audit_attempts, _application_audit_feedback); called by 1 (qa_ufo_application); 5 external calls (__init__, model_validate, model_validate_json, format, audit_application).


##### `_application_source_sha256`  (lines 1189–1195)

```
async def _application_source_sha256(ctx: ToolContext) -> str
```

**Purpose**: Computes a fingerprint of the UFO application's main source file. This proves whether the source has changed since QA passed.

**Data flow**: It reads the fixed application source file from inside the sandbox. If the read succeeds, it hashes the file contents with SHA-256 and returns the hex digest; if not, it raises an error.

**Call relations**: qa_ufo_application stores this hash after a passing audit. _require_current_application_qa later compares the current hash to the stored proof before deployment.

*Call graph*: called by 2 (_require_current_application_qa, qa_ufo_application); 1 external calls (sha256).


##### `_require_current_application_qa`  (lines 1198–1210)

```
async def _require_current_application_qa(ctx: ToolContext) -> ApplicationQaProof
```

**Purpose**: Blocks a UFO application deployment unless product QA has passed for the exact current source code. This prevents a builder from passing QA, changing the app, and deploying untested code.

**Data flow**: It receives the context, reads the stored QA proof for the turn, validates its shape, computes the current source hash, compares it to the proof, and returns the proof if everything matches. It raises an error when proof is missing, invalid, or stale.

**Call relations**: deploy_website calls this when the active subagent is the UFO application builder. It depends on _application_source_sha256 to detect source changes.

*Call graph*: calls 1 internal fn (_application_source_sha256); called by 1 (deploy_website); 2 external calls (model_validate, format).


##### `qa_ufo_application`  (lines 1213–1255)

```
async def qa_ufo_application(ctx: ToolContext, args: QaUfoApplicationInput) -> ToolResult
```

**Purpose**: Runs the special product QA tool for the UFO application builder profile. It gives either bounded repair feedback or a compact summary of what the browser audit verified.

**Data flow**: It confirms the caller is the application builder, checks and increments the QA call count, runs _audit_builder_application, and if the audit passes, builds a product QA result from the report. It then hashes the current source, stores QA proof for deployment, and returns the result as JSON.

**Call relations**: This is a profile-only public tool handler registered in SITES_TOOLS. It calls _audit_builder_application for the real audit, _application_source_sha256 for the deployment proof, and _json_result for the response.

*Call graph*: calls 3 internal fn (_application_source_sha256, _audit_builder_application, _json_result); 4 external calls (__init__, __init__, format, format).


##### `deploy_ufo_application`  (lines 1258–1268)

```
async def deploy_ufo_application(ctx: ToolContext, args: DeployUfoApplicationInput) -> ToolResult
```

**Purpose**: Deploys the fixed UFO application scaffold after the builder-specific requirements are met. It is a profile-only convenience wrapper around the normal static website deploy flow.

**Data flow**: It receives the context and a site name, confirms the caller is the UFO application builder, creates a DeployWebsiteInput that points at the fixed scaffold and index.html, and returns whatever deploy_website returns.

**Call relations**: This public profile-only tool handler delegates to deploy_website. deploy_website then performs the QA proof check, source promotion, serving, hosting, and preview work.

*Call graph*: calls 1 internal fn (deploy_website); 1 external calls (__init__).


##### `_unhosted`  (lines 1271–1279)

```
def _unhosted(displaced: HostedSite | None, conversation_id: UUID) -> dict[str, object]
```

**Purpose**: Reports which existing site was taken down by a new deployment, if any. This makes replacement behavior visible instead of silently leaving an old link broken.

**Data flow**: It receives an optional displaced HostedSite and the conversation id. If there is no displaced site, it returns an empty dictionary; otherwise it returns a dictionary naming the unhosted site object.

**Call relations**: deploy_website and publish_website merge this into their final JSON response after hosting the new site. It uses the site object naming helper to describe the displaced site.

*Call graph*: called by 2 (deploy_website, publish_website); 1 external calls (site_object_name).


##### `deploy_website`  (lines 1282–1320)

```
async def deploy_website(ctx: ToolContext, args: DeployWebsiteInput) -> ToolResult
```

**Purpose**: Publishes a built static website folder at a permanent hosted link and stores its files as the source of record. Re-deploying the same name updates the site behind the same link.

**Data flow**: It resolves the source directory, enforces UFO builder restrictions and QA proof when relevant, chooses the conversation's serving port, checks for homepage redeploy special cases, performs permission checks, prepares or builds the served directory, uploads the source manifest, starts the static server, registers the hosted site, draws preview imagery, and returns the local and public site details as JSON.

**Call relations**: This is a public side-effecting tool handler registered in SITES_TOOLS, and deploy_ufo_application delegates to it. It coordinates _agent_homepage, _redeploy_homepage, _refuse_before_serving, _served_directory, _promote_source, _serve, _host, _pictured, _unhosted, and _json_result.

*Call graph*: calls 12 internal fn (_agent_homepage, _host, _json_result, _pictured, _promote_source, _redeploy_homepage, _refuse_before_serving, _require_current_application_qa, _serve, _served_directory (+2 more)); called by 1 (deploy_ufo_application); 4 external calls (serve_port, workspace_path, site_object_name, site_name).


##### `_served_directory`  (lines 1323–1349)

```
async def _served_directory(ctx: ToolContext, project: str) -> tuple[str, dict[str, dict[str, object]]]
```

**Purpose**: Determines the actual directory that should be hosted for a static deploy. If the input is already built output, it uses it; if it is a source page project containing app.tsx, it builds it first and hosts the generated dist folder.

**Data flow**: It receives the context and project path, lists the source files, and checks whether the special project source file is present. If not, it returns the project and its listing. If present, it writes config, unpacks the page kit, runs the Vite build, then lists and returns the dist directory.

**Call relations**: deploy_website and _redeploy_homepage call this before promoting source and serving. It uses _source_listing for file maps and unpack_page_kit for source-page builds.

*Call graph*: calls 1 internal fn (_source_listing); called by 2 (_redeploy_homepage, deploy_website); 2 external calls (quote, unpack_page_kit).


##### `_agent_homepage`  (lines 1352–1355)

```
async def _agent_homepage(ctx: ToolContext) -> HostedSite | None
```

**Purpose**: Finds the hosted site currently bound as the acting agent's homepage, if one exists. This lets deployment detect when a request should update an existing homepage instead of creating a second site.

**Data flow**: It receives the context, gets the hosted-site registry, asks for the homepage of the current turn's agent id, and returns the HostedSite or None.

**Call relations**: deploy_website calls this before normal deployment. It delegates registry construction to _sites_registry.

*Call graph*: calls 1 internal fn (_sites_registry); called by 1 (deploy_website).


##### `_sites_registry`  (lines 1358–1361)

```
def _sites_registry(ctx: ToolContext) -> HostedSites
```

**Purpose**: Creates the HostedSites registry object for the current workspace transaction. This is a small helper that keeps store access consistent across website flows.

**Data flow**: It receives the context, checks that the extension context exists, and returns a HostedSites object tied to the workspace id and transaction. It does not itself read or write a site.

**Call relations**: _agent_homepage, _redeploy_homepage, and deploy_website use this when they need to inspect or update hosted-site rows.

*Call graph*: called by 3 (_agent_homepage, _redeploy_homepage, deploy_website); 1 external calls (__init__).


##### `_redeploy_homepage`  (lines 1364–1438)

```
async def _redeploy_homepage(ctx: ToolContext, args: DeployWebsiteInput, bound: HostedSite, scratch_port: int) -> ToolResult
```

**Purpose**: Updates an already-bound agent homepage in place from a build made in the current conversation. The homepage link and row stay the same, but the stored source and displayed page are refreshed.

**Data flow**: It checks that a real speaker, or an approved application-builder redeploy request, authorized the change. It rejects visibility changes because homepage visibility comes from the agent, checks for displaced sites on the scratch port, builds or selects the served directory, promotes the new source under the existing homepage identity, serves it on the scratch port, updates the existing row's manifest, removes any displaced site row, redraws preview imagery, and returns the updated link details as JSON.

**Call relations**: deploy_website calls this when a deployment name matches the acting agent's bound homepage from another conversation. It coordinates _sites_registry, _served_directory, _promote_source, _serve, _pictured, and _json_result.

*Call graph*: calls 7 internal fn (agent_visibility, _json_result, _pictured, _promote_source, _serve, _served_directory, _sites_registry); called by 1 (deploy_website); 5 external calls (authority_member_id, workspace_path, format, site_object_name, site_url).


##### `publish_website`  (lines 1441–1467)

```
async def publish_website(ctx: ToolContext, args: PublishWebsiteInput) -> ToolResult
```

**Purpose**: Publishes a web app at a permanent hosted link while it is served by its own sandbox process. This is for larger or dynamic apps where the running server, not stored static files, is the source of responses.

**Data flow**: It chooses the conversation's serving port, checks hosting permission before disturbing anything, optionally runs an install command, chooses either the backend run command or a static file server for the dist path, starts the server with _serve, registers the hosted link without a source manifest, draws preview imagery, and returns the hosted details as JSON.

**Call relations**: This is a public side-effecting tool handler registered in SITES_TOOLS. It uses _refuse_before_serving, _build_failed, _serve, _host, _pictured, _unhosted, and _json_result.

*Call graph*: calls 7 internal fn (_build_failed, _host, _json_result, _pictured, _refuse_before_serving, _serve, _unhosted); 3 external calls (quote, serve_port, workspace_path).


##### `set_homepage`  (lines 1470–1528)

```
async def set_homepage(ctx: ToolContext, args: SetHomepageInput) -> ToolResult
```

**Purpose**: Binds an existing hosted site as the homepage for the target agent. This changes who can open the page, because a homepage follows the agent's visibility rather than the site's own visibility.

**Data flow**: It receives the context and site object name, confirms the target agent exists, checks that the acting member may edit that agent, loads hosted sites, finds the named site, confirms the acting member created it, requires a live speaker unless the site was just deployed in this same turn, writes the homepage binding, and returns the site URL, effective visibility, and homepage agent id as JSON.

**Call relations**: This is a public side-effecting tool handler registered in SITES_TOOLS and bound to an agent instance. It uses the hosted-site store directly, permission helpers from the context, site object naming and URL helpers, SpeakerRequired for disclosure checks, and _json_result for the final response.

*Call graph*: calls 2 internal fn (speaker_is_admin, _json_result); 5 external calls (__init__, __init__, authority_member_id, site_object_name, site_url).


### `core/src/ufo/runtime/media/site_previewer.py`

`io_transport` · `request handling`

A conversation can have a web app running inside a sandbox, and the system may want to show a small visual preview of that app. This file makes that happen safely. It builds a public viewing URL for the sandbox port, sends that URL to a separate service called ufo-preview, checks that the answer really is the PNG image it asked for, and stores the result.

The main class, SitePreviewer, is given a blob store, the preview service address, an access token, and the public ingress address used to reach sandboxes. Think of it like a photographer with a delivery address: it is told which web page to photograph, what size the photo should be, and where to put the finished picture.

There are two storage paths. If the blob store is backed by S3, the preview service can upload the image directly using a temporary upload link, and this file only receives small metadata back. If not, the image bytes come back inline in the HTTP response, and this file writes them to blob storage itself. In both cases it enforces size limits, checks dimensions, verifies PNG content where needed, and logs failures instead of crashing the wider flow. If anything looks wrong, it returns no preview.

#### Function details

##### `SitePreviewer.render`  (lines 47–124)

```
async def render(self, conversation_id: UUID, port: int, name: str, width: int, height: int) -> StoredPreview | None
```

**Purpose**: This method captures one sandbox web page as a PNG preview and stores it. A caller uses it when it wants a visual snapshot of a conversation’s hosted site, or no preview if the page cannot be reached or the preview service fails.

**Data flow**: It starts with a conversation ID, sandbox port, desired file name, and requested image width and height. It first rejects unsafe names and unreasonable dimensions, then asks the workspace and ingress-url helper to build a public URL for the sandbox page. It creates a unique blob key for the future image. If storage supports direct S3 upload, it gives the preview service a temporary upload URL; otherwise it asks the service to return the PNG bytes directly. It sends the render request over HTTP, reads the response with strict size limits, checks for success, verifies metadata or PNG headers and dimensions, then stores or records the image. On success it returns a StoredPreview containing the blob key and size. On network errors or validation problems, it logs a short diagnostic message and returns None.

**Call relations**: This is the file’s main action. It calls the ingress URL helper to turn workspace and conversation details into a browser-accessible sandbox URL, uses the current workspace to identify where that sandbox belongs, uses JSON and httpx to talk to the external preview service, and creates a StoredPreview when the image has been safely accepted. If something goes wrong, it hands the event to the logging system so the rest of the application can continue without a preview.

*Call graph*: 9 external calls (__init__, AsyncClient, Timeout, dumps, PurePosixPath, log, mint_ingress_view_url, ws_current, uuid4).


### `extensions/sites/ufo_ext_sites/conversation_slot.py`

`orchestration` · `conversation display`

This file is the bridge between stored hosted sites and the conversation screen that wants to display them. A “conversation slot” is a small panel or section of conversation-related information. Here, that slot is called “Sites,” and it shows links to websites connected to the current conversation.

The important safety idea is that the file does not simply list every site in storage. First, it looks at the conversation’s visible authorization items. Those items act like tickets: only sites with a matching ticket name and generation are allowed through. The “generation” is a version number, so an old ticket cannot accidentally authorize a newer or changed site.

The main read function turns visible objects into expected site names, asks the site store for matching rows, filters out anything whose authorization does not match exactly, builds public URLs for the approved sites, and returns them in a payload the UI understands. It also asks for one more site than the maximum display limit, so it can say whether the list was cut short.

The summary function is lighter. It just reports how many visible site-related items there are, capped at the display limit. At the bottom, `SITES_SLOT` registers the whole thing: its id, label, icon, payload type, and the two functions used to summarize and read it.

#### Function details

##### `_read`  (lines 13–46)

```
async def _read(ctx: ConversationSlotContext) -> SitesSlotPayload
```

**Purpose**: Builds the full data shown in the conversation’s “Sites” slot. It finds which site objects the conversation is allowed to see, reads those sites from storage, filters them for exact authorization matches, and returns display-ready site entries with public URLs.

**Data flow**: It receives a conversation slot context containing the conversation id, visible authorization items, workspace/store access, transaction, and public base URL. It converts visible item names into expected site names and generations, asks `HostedSites` for matching stored site rows, keeps only rows whose authorization object name and generation still match, then turns each approved row into a `ConversationSite`. The result is a `SitesSlotPayload` containing up to the configured maximum number of sites plus a `truncated` flag that says whether more authorized sites existed than could be shown.

**Call relations**: When the registered `SITES_SLOT` is asked to read its content, this function does the real work. It uses `site_name_from_object` to understand authorization item names, `HostedSites` to fetch possible site records, `site_object_name` to check the matching authorization identity, and `site_url` to make browser-ready links before packaging everything as `ConversationSite` objects inside a `SitesSlotPayload`.

*Call graph*: 6 external calls (__init__, __init__, __init__, site_name_from_object, site_object_name, site_url).


##### `_summarize`  (lines 49–51)

```
async def _summarize(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: Provides a quick count for the “Sites” slot without loading full site details. This is useful when the UI only needs a small badge or preview number.

**Data flow**: It receives the same conversation slot context and reads the number of visible items from it. It caps that number at the maximum number of sites the slot can display, then returns the count; if the count is zero, it returns `None` so there is no summary number to show.

**Call relations**: The registered `SITES_SLOT` calls this when it needs a lightweight summary instead of the full site list. Unlike `_read`, it does not fetch from storage or build URLs; it only uses the visible items already present in the context.


### `extensions/sites/ufo_ext_sites/share_card.py`

`domain_logic` · `site deploy and public sharing metadata generation`

A shared link often gets “unfurled”: the app showing the link fetches a title and image to display as a card. This file creates that image for hosted sites. Without it, public site links would either show a generic brand card or keep an older card, so each site would look less distinct when shared.

The card is a fixed 1200 by 630 pixel image. The left panel contains the “Made with” text, the UFO logo, and the site name. The right side contains a picture of the site’s front page. The file avoids drawing text over the site screenshot, so the site’s own design stays untouched.

Most of the work happens inside the site’s sandbox, meaning the isolated environment where the site is built and served. The code asks Chrome or Chromium to take screenshots in headless mode, which means the browser runs without a visible window. It waits for the page to settle instead of trusting the normal page load event, because animations or late content can make a too-early screenshot blank or misleading.

After the browser draws the composed card as a PNG image, Pillow, an image library, converts it to a progressive JPEG and computes a digest, a fingerprint used to name and cache the result. If anything fails, the error is logged and the site keeps its existing card.

#### Function details

##### `card_page`  (lines 486–511)

```
def card_page(name: str, drawn: str) -> str
```

**Purpose**: Builds the HTML page that Chrome will render into the final share card. It combines the fixed brand panel, the site name, and a placeholder where the site screenshot will later be inserted.

**Data flow**: It receives the site name and a small CSS sizing rule that says how the screenshot should fit. It reads the bundled font and logo files, turns the font into text-safe base64 data, escapes the site name so it cannot break the HTML, and returns one complete HTML document with a screenshot placeholder still inside it.

**Call relations**: _compose calls this when it is ready to create the card page in the sandbox. While building that page, it calls _lockup to get the logo markup, and it uses base64 encoding and HTML escaping so the generated page is self-contained and safe.

*Call graph*: calls 1 internal fn (_lockup); called by 1 (_compose); 2 external calls (b64encode, escape).


##### `_lockup`  (lines 514–518)

```
def _lockup() -> str
```

**Purpose**: Reads the bundled UFO logo SVG and returns only the actual SVG markup that can be embedded inside another HTML page.

**Data flow**: It opens the logo asset file, finds where the <svg> tag begins, removes anything before that, and returns the remaining SVG text. It does not change files or talk to outside services.

**Call relations**: card_page calls this while assembling the left brand panel. It supplies the logo markup that becomes part of the HTML card Chrome later draws.

*Call graph*: called by 1 (card_page).


##### `shot_command`  (lines 521–541)

```
def shot_command(*, url: str, width: int, height: int, scale: int, shot: str, root: str) -> str
```

**Purpose**: Creates the shell command that will run Chrome or Chromium in the sandbox and take a screenshot. It packages a Python browser-driver script into the command so the sandbox can do the work locally.

**Data flow**: It receives the URL or file path to capture, the desired image size and scale, the output screenshot path, and the sandbox root path. It quotes paths and URLs safely for the shell, fills in browser settings and timing limits, and returns a command string ready to run.

**Call relations**: _shoot calls this just before asking the sandbox to take a screenshot. The returned command includes the embedded driver code that talks to the browser and writes the screenshot file.

*Call graph*: called by 1 (_shoot); 2 external calls (quote, shell_path).


##### `draw_from_page`  (lines 544–562)

```
async def draw_from_page(ctx: ToolContext, sites: HostedSites, conversation_id: UUID, name: str, port: int) -> None
```

**Purpose**: Creates a fresh share card from the site’s live front page during a deploy. This is the normal path for a newly published site because the site is already running locally inside the sandbox.

**Data flow**: It receives the tool context, site store, conversation ID, site name, and local port where the site is being served. It chooses a runtime path for the screenshot, asks _shoot to capture the local site at the card’s right-side dimensions, and, if that works, passes the screenshot to _compose to make and store the final card.

**Call relations**: This is called by higher-level site publishing code when a site has just been deployed. It first relies on _shoot for the page picture, then hands off to _compose to build the complete branded card and record it on the hosted site.

*Call graph*: calls 2 internal fn (_compose, _shoot).


##### `draw_from_stored_shot`  (lines 565–580)

```
async def draw_from_stored_shot(ctx: ToolContext, sites: HostedSites, conversation_id: UUID, name: str, blob_key: str) -> None
```

**Purpose**: Creates a share card for an older site that already has a stored page preview but did not yet have a share card. It is a backfill path for sites published before this feature existed.

**Data flow**: It receives a blob key for an existing preview image. It downloads that image from blob storage, writes it into the sandbox, and then asks _compose to place it into the card. If the preview cannot be written into the sandbox, it logs the failure and stops.

**Call relations**: Higher-level publishing or migration code can call this when it finds an existing site preview but no card. It does not take a new live screenshot; instead it prepares the stored screenshot and hands it to _compose. On write failure it calls _undrawn.

*Call graph*: calls 2 internal fn (_compose, _undrawn).


##### `_shoot`  (lines 583–612)

```
async def _shoot(ctx: ToolContext, name: str, shot: str, *, url: str, width: int, height: int, scale: int) -> bool
```

**Purpose**: Takes one browser screenshot in the sandbox and reports whether it succeeded. It is used both for photographing the site page and for photographing the finished card HTML.

**Data flow**: It receives the sandbox context, site name, destination screenshot path, URL or file path to capture, image size, and scale. It first empties the destination file, builds a browser command with shot_command, runs that command with a timeout, and returns true only if the command exits successfully. On any write or browser failure, it logs the problem and returns false.

**Call relations**: draw_from_page calls this to capture the live site. _compose calls it again to capture the generated card page. It delegates command construction to shot_command and uses _undrawn whenever the screenshot cannot be produced.

*Call graph*: calls 2 internal fn (_undrawn, shot_command); called by 2 (_compose, draw_from_page).


##### `_compose`  (lines 615–665)

```
async def _compose(ctx: ToolContext, sites: HostedSites, conversation_id: UUID, name: str, shot: str, drawn: str) -> None
```

**Purpose**: Turns an existing site screenshot into the final branded share card, stores the card, and updates the hosted site record to point at it.

**Data flow**: It receives a screenshot path already present in the sandbox, plus the site identity and sizing rule. It writes the generated card HTML, inserts the screenshot into that page as a data URI, asks _shoot to render the card page into a PNG, converts that PNG into a JPEG, stores the JPEG as a preview artifact, and finally writes the blob key and digest into the hosted sites store. If any step fails, it logs the failure and leaves the existing card unchanged.

**Call relations**: draw_from_page and draw_from_stored_shot both hand screenshots to this function. It calls card_page to make the HTML, _shoot to render it, ToolContext.store_preview to save the finished image, and HostedSites.set_share_card to attach the saved card to the site. It calls _undrawn on recoverable failures.

*Call graph*: calls 5 internal fn (store_preview, _shoot, _undrawn, card_page, set_share_card); called by 2 (draw_from_page, draw_from_stored_shot).


##### `_undrawn`  (lines 668–669)

```
def _undrawn(name: str, detail: object) -> None
```

**Purpose**: Records that a share card could not be drawn for a site. The failure is treated as non-fatal because a missing or stale card should not stop the site from being hosted.

**Data flow**: It receives the site name and any error detail. It turns the detail into text, trims it to a fixed maximum length, and writes a structured log event. It returns nothing and does not change the site record.

**Call relations**: _shoot, _compose, and draw_from_stored_shot call this whenever a screenshot, file write, encode, or storage preparation step fails. It is the shared quiet-failure path that preserves the current site state while leaving a trail for debugging.

*Call graph*: called by 3 (_compose, _shoot, draw_from_stored_shot); 1 external calls (log).


### `extensions/sites/ufo_ext_sites/application_audit.py`

`domain_logic` · `application QA/audit run`

This file is the quality gate for an application built by the UFO system. Think of it like a building inspector: it does not create the application, but it checks whether the finished page is readable, usable, faithful to the accepted design, and actually shows the facts it promised to show. Without this file, a broken or misleading application could be treated as complete even if text was unreadable, content overflowed off-screen, controls did not work, or important facts were hidden below the first screen.

Most of the file is made of Pydantic models, which are structured data shapes with validation rules. They describe what an audit report must contain: measured browser views in light and dark mode, text contrast readings, clipped or overlapping elements, accessible controls, interaction results, design regions, and required facts. These models are frozen, meaning once created they are not meant to change, which helps make the audit deterministic and repeatable.

The main work happens in audit_application. It checks four expected browser views, looks for empty pages, poor contrast, horizontal overflow, clipping, accidental overlap, console errors, too few controls, too few successful interactions, missing required facts, and facts that are not visible above the first screen. It also compares the live application layout against accepted design regions. The result is an ApplicationAuditVerdict: either no issues, meaning it passed, or a short bounded list of repair instructions.

#### Function details

##### `AcceptedApplicationDesignEvidence.regions_are_unique`  (lines 131–137)

```
def regions_are_unique(self) -> 'AcceptedApplicationDesignEvidence'
```

**Purpose**: This validation step makes sure the accepted design evidence does not contain duplicate region names or duplicate design kit component names. It protects later comparisons from ambiguity, because two regions with the same name would make it unclear which part of the design the application is supposed to match.

**Data flow**: It reads the region names and kit component names already placed into an AcceptedApplicationDesignEvidence object. If every name is unique, the object is allowed to exist unchanged. If any name repeats, it raises a validation error instead of producing accepted evidence.

**Call relations**: This runs automatically when AcceptedApplicationDesignEvidence is created. Later audit code can then trust that each named design region and each listed kit component refers to one clear thing, not several competing entries.


##### `ApplicationAuditReport.views_are_unique`  (lines 202–206)

```
def views_are_unique(self) -> 'ApplicationAuditReport'
```

**Purpose**: This validation step makes sure an audit report has at most one measurement for each combination of colour scheme and screen width. That matters because the audit expects one clear answer for, for example, dark mode on desktop.

**Data flow**: It reads the scheme and width from every view in the report. If no pair repeats, the report is kept as-is. If a pair appears more than once, it raises a validation error rather than allowing conflicting measurements into the audit.

**Call relations**: This runs automatically when an ApplicationAuditReport is created. audit_application later builds a lookup table from these same scheme-and-width pairs, so this validator prevents duplicate data from silently replacing other data.


##### `ApplicationAuditVerdict.passed`  (lines 227–230)

```
def passed(self) -> bool
```

**Purpose**: This property gives a simple yes-or-no answer for the audit result. It is true only when the verdict contains no repair issues.

**Data flow**: It reads the verdict's issues tuple. If that tuple is empty, it returns true. If there is even one issue, it returns false and changes nothing.

**Call relations**: Code that receives an ApplicationAuditVerdict can call this property after audit_application finishes. It turns the detailed issue list into the plain decision: accepted or needs repair.


##### `_needed_ratio`  (lines 277–282)

```
def _needed_ratio(item: ApplicationAuditText) -> float
```

**Purpose**: This helper decides how much colour contrast a piece of text needs. Contrast means the visible difference between text colour and background colour; low contrast can make text hard or impossible to read.

**Data flow**: It receives one measured text item. If the text belongs to a special quiet design slot, it uses a lower project-specific threshold. If the text is large, or large and bold enough, it uses the accessibility threshold for large text. Otherwise it uses the stricter normal-body-text threshold. It returns the required contrast number.

**Call relations**: _contrast_failures calls this for each text measurement. The returned threshold is then compared with the browser-measured contrast ratio to decide whether that text should be reported as a problem.

*Call graph*: called by 1 (_contrast_failures).


##### `_issue`  (lines 285–292)

```
def _issue(code: AuditIssueCode, message: str, terms: tuple[str, ...]=()) -> ApplicationAuditIssue
```

**Purpose**: This helper creates one audit issue in a safe, bounded form. It trims overly long messages and limits the number of search terms attached to the issue, so repair feedback stays small and predictable.

**Data flow**: It receives an issue code, a human-readable message, and optional terms connected to the problem. It shortens the message to the maximum allowed length, keeps only the first ten terms, and returns an ApplicationAuditIssue object.

**Call relations**: audit_application uses this whenever it finds a problem, such as missing views, poor contrast, or failed interactions. This keeps every issue formatted consistently before it is placed into the final verdict.

*Call graph*: called by 1 (audit_application); 1 external calls (__init__).


##### `application_first_screen_scale`  (lines 295–305)

```
def application_first_screen_scale(page_height: int) -> float
```

**Purpose**: This helper converts the fixed first-screen height into a fraction of the full measured page height. It lets the audit compare layout regions fairly even when the full page is taller than the first visible screen.

**Data flow**: It receives a page height in pixels. It divides the fixed first-screen height, 844 pixels, by that page height and returns the resulting scale factor. It does not change any stored data.

**Call relations**: application_region_relation and application_design_region_size_failure call this when they need vertical measurements to mean the same real number of pixels on pages of different heights.

*Call graph*: called by 2 (application_design_region_size_failure, application_region_relation).


##### `application_region_relation`  (lines 308–328)

```
def application_region_relation(first: ApplicationAuditRegion, second: ApplicationAuditRegion, page_height: int=APPLICATION_DESIGN_FOLD) -> tuple[Literal['horizontal', 'vertical'], int] | None
```

**Purpose**: This function decides whether two named visual regions sit above/below each other or left/right of each other, and in which order. If they overlap too much, it returns no relationship.

**Data flow**: It receives two regions whose positions are stored as fractions of a page, plus the page height those fractions came from. It adjusts the vertical tolerance so tiny one-pixel differences do not count as meaningful layout changes. It then compares their top, bottom, left, and right edges and returns a direction and order, or returns null if the regions overlap or cannot be cleanly separated.

**Call relations**: application_design_fidelity uses this first on the accepted design to understand the intended order of regions, then on the live application view to see whether the application kept that same order. It calls application_first_screen_scale to keep its vertical tolerance fair across page heights.

*Call graph*: calls 1 internal fn (application_first_screen_scale); called by 1 (application_design_fidelity).


##### `application_design_region_size_failure`  (lines 331–349)

```
def application_design_region_size_failure(regions: tuple[ApplicationAuditRegion, ...], page_height: int=APPLICATION_DESIGN_FOLD) -> str | None
```

**Purpose**: This function checks whether any accepted design region is too small to count as a useful visible screen region. It prevents tiny decorative fragments from being treated as meaningful design sections.

**Data flow**: It receives a tuple of design regions and the page height they were measured against. For each region, it compares width, height, and total area against minimum limits, scaling the vertical and area limits for the first-screen height. It returns the first failure message it finds, or null if all regions are large enough.

**Call relations**: application_design_fidelity calls this early. If a design region is too small, fidelity checking stops with that failure because later layout matching would be based on weak or misleading design evidence.

*Call graph*: calls 1 internal fn (application_first_screen_scale); called by 1 (application_design_fidelity).


##### `application_design_region_fold_failure`  (lines 352–374)

```
def application_design_region_fold_failure(regions: tuple[ApplicationAuditRegion, ...], page_height: int=APPLICATION_DESIGN_FOLD) -> str | None
```

**Purpose**: This function detects a design region that crosses the boundary of the first visible screen. The rule is meant to keep important named regions clearly on one side of the first-screen fold rather than straddling it.

**Data flow**: It receives design regions and a page height. For each region, it converts the stored fractional top and height back into pixel rows, then checks whether the region paints meaningfully above and below the 844-pixel first-screen line. It allows a small tolerance for hairline borders or antialiased edges. It returns the first failure message, or null if no region crosses the boundary.

**Call relations**: This function is available as a design-rule check, but it is not called by the listed functions in this file. It fits the same family of design-region checks as application_design_region_size_failure.


##### `application_design_fidelity`  (lines 377–460)

```
def application_design_fidelity(report: ApplicationAuditReport) -> ApplicationDesignFidelity
```

**Purpose**: This function scores how closely the live application matches the accepted design structure. It focuses on named regions: whether they exist, are visible where expected, do not overlap in the design, and keep the same relative order on desktop in light and dark mode.

**Data flow**: It receives a full ApplicationAuditReport. It first checks that the design has the right number of unique named regions. It then rejects designs with regions that are too small or overlapping. For each desktop colour scheme, it looks for measured application regions, compares their names with the design names, checks above-the-fold visibility, and verifies that region order matches the design. It returns an ApplicationDesignFidelity object with a passed count, a total count, and failure messages.

**Call relations**: audit_application calls this as the design portion of the audit. Internally it calls application_design_region_size_failure to validate region usefulness and application_region_relation to compare layout order between the accepted design and the measured application.

*Call graph*: calls 2 internal fn (application_design_region_size_failure, application_region_relation); called by 1 (audit_application); 1 external calls (__init__).


##### `_contrast_failures`  (lines 463–478)

```
def _contrast_failures(views: tuple[ApplicationAuditView, ...]) -> list[str]
```

**Purpose**: This helper finds all text measurements whose colour contrast is below the required level. It turns raw browser measurements into readable repair messages.

**Data flow**: It receives the measured browser views. For every text item in every view, it asks _needed_ratio what threshold applies, compares that threshold with the measured contrast ratio, and builds a message for each failing item. It returns a list of failure strings.

**Call relations**: audit_application calls this after it has selected the measured views it cares about. _contrast_failures delegates the threshold decision to _needed_ratio so the contrast rules stay in one place.

*Call graph*: calls 1 internal fn (_needed_ratio); called by 1 (audit_application).


##### `audit_application`  (lines 481–604)

```
def audit_application(report: ApplicationAuditReport, contract: ApplicationAuditContract | None=None) -> ApplicationAuditVerdict
```

**Purpose**: This is the main audit function. It takes the browser's report about a staged application and returns a verdict saying either that the application passed or that specific repairs are required.

**Data flow**: It receives an ApplicationAuditReport and, optionally, a contract listing facts the application must show. It builds a view lookup, checks for missing or empty required views, asks _contrast_failures for unreadable text, checks overflow, clipping, overlap, design fidelity, browser console errors, accessible controls, successful interactions, missing contract facts, and facts not visible above the fold. Each problem becomes a bounded ApplicationAuditIssue through _issue. It returns an ApplicationAuditVerdict containing at most the configured maximum number of issues.

**Call relations**: This function is the file's central decision point. It calls _contrast_failures for readability checks, application_design_fidelity for layout matching, and _issue to package each repair item. At the end it constructs the ApplicationAuditVerdict that other parts of the system can use to accept the application or send repair feedback back to the builder.

*Call graph*: calls 3 internal fn (_contrast_failures, _issue, application_design_fidelity); 1 external calls (__init__).


### `extensions/sites/ufo_ext_sites/source.py`

`io_transport` · `site source materialization and page build preparation`

A hosted site has two homes: its saved source files live in a blob store, while editing and building happen inside a sandbox, which is an isolated working folder for running tools safely. This file is the bridge between those homes. Without it, a user opening a site for editing could see an old or incomplete copy, and a redeploy could accidentally carry stale files back onto the live site. The main flow reads a site's source manifest, which is a saved list of file names and sizes, then recreates exactly those files under the sandbox's sites directory. Before writing, it clears the old tree and “claims” each destination path through a containment check, meaning it makes sure no file name or symbolic link can escape the workspace and overwrite something else. For cloud-style blob stores, the sandbox downloads files itself using temporary signed URLs. For local development stores, this process reads the bytes and writes them into the sandbox. A generation stamp acts like a freshness label: if the sandbox already has the same deploy generation, the file avoids downloading everything again. The file also packages and unpacks the page SDK kit, so old app-page forks build using today’s shared components instead of whatever existed when they were first created.

#### Function details

##### `_page_kit_archive`  (lines 101–113)

```
def _page_kit_archive() -> bytes
```

**Purpose**: This function bundles the page SDK kit files into one compressed tar archive, which is a single package of many files. It is used so the sandbox only needs one write and one unpack step instead of many small file transfers.

**Data flow**: It starts with the kit directory on disk. It walks through every regular file in that directory, adds each one to an in-memory compressed archive under the sdk/ folder name, then returns the archive as raw bytes.

**Call relations**: At module load time, the file uses this function to create PAGE_KIT_ARCHIVE once. Inside the function, BytesIO provides the in-memory container, and tarfile.open writes the compressed archive into it.

*Call graph*: 2 external calls (BytesIO, open).


##### `transfer`  (lines 120–130)

```
async def transfer(ctx: ToolContext, script: str, pairs: list[tuple[str, str]], total_bytes: int) -> None
```

**Purpose**: This function asks the sandbox to upload or download many files using a small shell script. It batches the work so one command does not become too large, and it gives larger transfers more time to finish.

**Data flow**: It receives a sandbox tool context, a shell script, a list of file-and-URL pairs, and the total byte count. It calculates a timeout, sends the pairs to the sandbox in batches, and raises an error if any batch fails. It returns nothing when all transfers succeed.

**Call relations**: materialize_source calls this when the blob store can provide temporary download links. transfer then hands each batch to the sandbox shell command, so the actual network copy happens from inside the sandbox rather than through this Python process.

*Call graph*: called by 1 (materialize_source).


##### `materialize_source`  (lines 133–199)

```
async def materialize_source(ctx: ToolContext, site: HostedSite, object_name: str) -> tuple[str, list[str]]
```

**Purpose**: This function recreates a hosted site's stored source tree inside the current sandbox. It is what makes the editable workspace match the latest deployed source instead of a stale local copy.

**Data flow**: It receives a tool context, a hosted site record, and the object name used for the sandbox directory. It reads the site's saved source manifest, chooses the destination under the workspace, checks a generation stamp, safely clears and prepares the destination if needed, downloads or writes each source file, updates the stamp, and returns the destination path plus the list of relative file paths.

**Call relations**: This is the main worker in the file. It validates the saved manifest with SourceManifest.model_validate_json, builds safe workspace paths with workspace_path, quotes the stamp path with shlex.quote for a shell read, and calls transfer when presigned blob-store URLs are available. If the blob store does not support those URLs, it falls back to reading bytes through ctx.blob and writing them into the sandbox.

*Call graph*: calls 1 internal fn (transfer); 3 external calls (model_validate_json, quote, workspace_path).


##### `unpack_page_kit`  (lines 202–220)

```
async def unpack_page_kit(ctx: ToolContext, dest: str, runtime_root: str | None=None) -> None
```

**Purpose**: This function installs the page SDK kit into a sandbox project directory so an app page can build against the current shared page components. It also removes the temporary archive after unpacking so that archive does not accidentally become part of the site.

**Data flow**: It receives a tool context, a destination directory, and optionally a runtime root used for containment checks. It writes the prebuilt kit archive into the destination, runs a sandbox Python unpacker that checks every archive member before writing it under dest/sdk/, and raises an error if unpacking fails. It returns nothing on success but changes the sandbox directory by adding the kit files.

**Call relations**: There is no listed caller inside this file; it is a helper for the broader page build flow. It relies on the already-created PAGE_KIT_ARCHIVE and hands the sensitive unpacking step to a sandbox Python program that enforces the workspace boundary.


### `extensions/sites/ufo_ext_sites/store.py`

`domain_logic` · `active during site deploys, site reads, visibility changes, homepage binding, preview/share-card updates`

Think of this file as the front desk for hosted sites. A sandbox may be serving bytes on a port, and static deploys may have files in blob storage, but users need stable links like “this conversation’s dashboard.” This registry is the table that turns that stable name into the current place to fetch the site from.

The file defines the database row for a hosted site, plus small data shapes for stored source files and manifests. It also contains the rules for safe changes. A site belongs to a workspace and conversation, has a name, has a creator, and has a visibility level: private, workspace, or public. If a deploy reuses an existing name, the row is updated. If a deploy would take over a port already used by another site, this file decides whether that is allowed. It refuses dangerous cases, such as taking over another member’s site or replacing an agent’s homepage under a different name.

The `HostedSites` class is the main doorway. It reads and writes rows inside caller-provided database transactions, always scoped to one workspace because the database connection itself can see everything. It also stores preview images, share cards, homepage bindings, and generation stamps. Those stamps act like version tags so other parts of the system can notice that a site changed and refresh what users see.

#### Function details

##### `SourceManifest._rooted`  (lines 96–99)

```
def _rooted(cls, root: str) -> str
```

**Purpose**: Checks that a stored static site’s root path is in an allowed blob-storage area and looks like a folder prefix. This prevents a manifest from pointing at arbitrary storage keys.

**Data flow**: It receives a root string from a `SourceManifest`. It verifies that the string starts with `sites/` or `apps/` and ends with `/`. If the root is valid, it returns it unchanged; if not, it raises an error before the manifest can be accepted.

**Call relations**: This is run automatically by Pydantic, the validation library used for these data models, when a `SourceManifest` is built. It works before any serving code trusts the manifest’s root.


##### `SourceManifest._pathed`  (lines 103–118)

```
def _pathed(cls, files: dict[str, SiteFile]) -> dict[str, SiteFile]
```

**Purpose**: Checks that every file path in a static site manifest is a safe, plain path inside the site. This prevents paths like absolute paths, backslash tricks, hidden control characters, or `..` escapes from being used to read outside the site.

**Data flow**: It receives the manifest’s dictionary of site-relative file paths to file metadata. For each path, it rejects suspicious text, then asks `contained_relative` to prove the path stays under a fake site-source anchor. If every path is already in the exact safe form, the original dictionary comes out; otherwise an error is raised.

**Call relations**: Like `_rooted`, this is automatic validation when a `SourceManifest` is created. It hands each path to `ufo.sdk.sandbox.contained_relative`, which is the shared path-safety checker.

*Call graph*: 1 external calls (contained_relative).


##### `site_name`  (lines 172–180)

```
def site_name(raw: str) -> str
```

**Purpose**: Turns a member’s requested site name into a safe short slug used in links and object names. For example, a messy title becomes lowercase words separated by hyphens.

**Data flow**: It takes raw text, lowercases it, replaces runs of non-letter-or-number characters with `-`, trims extra hyphens, and limits the length. If nothing usable remains, it raises `InvalidSiteName`; otherwise it returns the cleaned name.

**Call relations**: Callers use this before registering a site, because they need the final safe name to build the link and database key. If the name cannot become a usable slug, this function stops the deploy path early with a clear tool-style error.

*Call graph*: 1 external calls (__init__).


##### `default_visibility`  (lines 183–191)

```
def default_visibility(audience: Audience) -> Visibility
```

**Purpose**: Chooses the starting visibility for a new site based on the conversation’s audience. Private direct messages and externally shared rooms default to private; normal internal shared spaces default to workspace-visible.

**Data flow**: It receives an audience value, parses it into a standard form, and checks whether it represents an individual member or a foreign/external audience. It returns `private` for those sensitive cases and `workspace` otherwise.

**Call relations**: `HostedSites.register` calls this only when creating a new site without an explicit visibility choice. It relies on the shared audience helpers `parse_audience` and `audience_member` to understand what kind of room produced the site.

*Call graph*: called by 1 (register); 2 external calls (audience_member, parse_audience).


##### `visibility_level`  (lines 194–202)

```
def visibility_level(value: str) -> Visibility
```

**Purpose**: Accepts only the three visibility values the system knows how to enforce: private, workspace, and public. It keeps bad stored or submitted values from silently becoming permissions.

**Data flow**: It receives a string. If the string is one of the allowed visibility words, it returns that same value as a trusted visibility value. Otherwise it raises an error naming the allowed choices.

**Call relations**: The row-conversion helper `_site` calls this whenever database text is turned into a `HostedSite`. That means every read from the registry rechecks that the stored visibility is meaningful.

*Call graph*: called by 1 (_site).


##### `HostedSites.register`  (lines 236–329)

```
async def register(self, conversation_id: UUID, name: str, port: int, creator_member_id: UUID, visibility: Visibility | None, audience: Audience, may_unhost: bool, *, manifest: str | None) -> HostedSi
```

**Purpose**: Creates or updates the registry row for a site after a deploy. It decides what link now points to the deployed bytes, while preserving ownership and safe visibility rules.

**Data flow**: It receives the conversation, safe site name, port, creator, optional visibility, audience, whether the current turn may unhost another site, and an optional static-source manifest. It opens a database transaction, asks `_refuse` whether the change is allowed, deletes any same-conversation site displaced from the port, updates an existing row or inserts a new one, and finally reads back the registered site. The result is a `HostedSite` record, or a runtime error if the row somehow disappeared during registration.

**Call relations**: This is the main write path used after deployment. It calls `_refuse` for safety checks, `_read` to inspect existing rows and return the final row, `default_visibility` for new sites without explicit visibility, and SQLAlchemy update/insert/delete helpers to change the database.

*Call graph*: calls 3 internal fn (_read, _refuse, default_visibility); 6 external calls (case, delete, insert, update, time_ns, uuid4).


##### `HostedSites.redeploy`  (lines 331–358)

```
async def redeploy(self, conversation_id: UUID, name: str, manifest: str) -> HostedSite | None
```

**Purpose**: Replaces the stored source manifest for an existing site without changing its identity, owner, visibility, port, or homepage binding. This is for updating the bytes behind an existing link rather than creating a new site.

**Data flow**: It receives the target conversation, site name, and new manifest string. It stamps a new deploy generation, updates the row’s `source_manifest`, `updated_at`, and `deploy_generation`, then reads the row back. It returns the updated `HostedSite`, or `None` if the site no longer exists.

**Call relations**: This is a narrower update path than `register`. It uses the same strictly-increasing generation-stamp idea as `register` so the portal can notice the deploy changed, and it delegates final row loading to `_read`.

*Call graph*: calls 1 internal fn (_read); 3 external calls (case, update, time_ns).


##### `HostedSites.homepage`  (lines 360–372)

```
async def homepage(self, agent_id: UUID) -> HostedSite | None
```

**Purpose**: Finds the one site currently bound as a particular agent’s homepage. If the agent has no homepage site, it returns nothing.

**Data flow**: It receives an agent ID, opens a transaction, queries this workspace for a row with that homepage binding, and converts the row into a `HostedSite` if found. The output is either that site or `None`.

**Call relations**: Other code can call this when it needs to open or inspect an agent’s homepage. It uses `_columns` to build the common site-select query and `_site` to convert the database row into the plain data object.

*Call graph*: calls 2 internal fn (_columns, _site).


##### `HostedSites.set_preview`  (lines 374–394)

```
async def set_preview(self, conversation_id: UUID, name: str, preview: StoredPreview) -> None
```

**Purpose**: Stores the preview image captured for a site after deployment. This lets the site row remember the blob key and size of the screenshot-like preview.

**Data flow**: It receives a conversation ID, site name, and `StoredPreview` containing a blob key and size. It updates the matching row in this workspace with those preview fields and a fresh update time. It returns no value; if the site was removed before the preview finished, no row is changed.

**Call relations**: This is intentionally separate from `register`, because the site can be registered before the page screenshot is ready. Preview-rendering code calls it after the image has been stored.

*Call graph*: 1 external calls (update).


##### `HostedSites.set_share_card`  (lines 396–420)

```
async def set_share_card(self, conversation_id: UUID, name: str, blob_key: str, digest: str) -> None
```

**Purpose**: Stores the share-card image made from a site and the digest, or content fingerprint, for that image. The digest lets public card URLs change when the card bytes change and disappear when they no longer match the row.

**Data flow**: It receives a conversation ID, site name, blob key, and digest. It updates the matching site row with the share-card storage key, hash, and new update time. It returns no value; if the site is gone or renamed, nothing is written.

**Call relations**: The share-card composition flow calls this after creating the card image. In the provided graph, `extensions/sites/ufo_ext_sites/share_card._compose` calls it to attach the finished card to the registry row.

*Call graph*: called by 1 (_compose); 1 external calls (update).


##### `HostedSites.read`  (lines 422–424)

```
async def read(self, conversation_id: UUID, name: str) -> HostedSite | None
```

**Purpose**: Looks up one site by conversation and name inside this workspace. It is the simple public read method for callers that already know the site’s identity.

**Data flow**: It receives a conversation ID and site name, opens a transaction, and asks `_read` to query the database. It returns a `HostedSite` if the row exists, otherwise `None`.

**Call relations**: This is a thin wrapper around `_read` that supplies the transaction boundary. Callers use it when resolving or inspecting a single site.

*Call graph*: calls 1 internal fn (_read).


##### `HostedSites.all`  (lines 426–436)

```
async def all(self) -> tuple[HostedSite, ...]
```

**Purpose**: Lists every hosted site in this workspace, ordered from oldest to newest. Separate permission gates can decide which of these a viewer is allowed to see.

**Data flow**: It opens a transaction, selects all site rows for the workspace, orders them by creation time and name, converts each row to a `HostedSite`, and returns them as an immutable tuple.

**Call relations**: This is a broad workspace listing. It shares the common column list from `_columns` and the row conversion from `_site` so it returns the same shape as other read methods.

*Call graph*: calls 2 internal fn (_columns, _site).


##### `HostedSites.conversation`  (lines 438–455)

```
async def conversation(self, conversation_id: UUID, names: tuple[str, ...], limit: int) -> tuple[HostedSite, ...]
```

**Purpose**: Lists selected hosted sites from one conversation. It is useful when a caller has a set of names and wants the corresponding rows in a stable oldest-first order.

**Data flow**: It receives a conversation ID, a tuple of names, and a limit. It queries rows in this workspace and conversation whose names are in that tuple, orders them by creation time and name, applies the limit, converts rows to `HostedSite` objects, and returns a tuple.

**Call relations**: This read method uses `_columns` for the shared select shape and `_site` for conversion. It sits between single-site lookup and full workspace listing.

*Call graph*: calls 2 internal fn (_columns, _site).


##### `HostedSites.visible_conversation`  (lines 457–487)

```
async def visible_conversation(self, conversation_id: UUID, member_id: UUID, limit: int, *, admin: bool, homepage_agents: frozenset[UUID]) -> tuple[HostedSite, ...]
```

**Purpose**: Lists the sites in a conversation that a particular member is allowed to open. It applies the main visibility rules for private sites, shared sites, homepage-bound sites, and admins.

**Data flow**: It receives a conversation ID, member ID, result limit, an admin flag, and the set of homepage agents visible to the member. It builds a permission condition: the member can see their own sites, non-private sites, and sites bound to visible homepage agents; admins skip that filter. It queries matching rows, orders and limits them, converts them to `HostedSite` objects, and returns a tuple.

**Call relations**: This is the permission-aware conversation listing. It uses SQLAlchemy’s `or_` to build the database-side gate, `_columns` for the selected fields, and `_site` to return normal site objects.

*Call graph*: calls 2 internal fn (_columns, _site); 1 external calls (or_).


##### `HostedSites.set_visibility`  (lines 489–504)

```
async def set_visibility(self, conversation_id: UUID, name: str, visibility: Visibility) -> HostedSite | None
```

**Purpose**: Changes one site’s visibility level and returns the updated row. It also changes the site’s generation ID so any cached permission decision tied to the old generation will no longer apply.

**Data flow**: It receives a conversation ID, site name, and new visibility. It updates the matching row’s visibility, generation, and update time, then reads the row back. It returns the updated `HostedSite`, or `None` if the row no longer exists.

**Call relations**: Callers use this after they have already decided the actor is allowed to change visibility. It uses SQLAlchemy update, creates a new UUID for the generation, and delegates final loading to `_read`.

*Call graph*: calls 1 internal fn (_read); 2 external calls (update, uuid4).


##### `HostedSites.set_homepage`  (lines 506–532)

```
async def set_homepage(self, agent_id: UUID, conversation_id: UUID, name: str) -> HostedSite | None
```

**Purpose**: Binds a named site as an agent’s homepage, while clearing any previous homepage binding for that same agent. This keeps each agent tied to at most one hosted homepage site.

**Data flow**: It receives an agent ID, conversation ID, and site name. In one transaction, it first clears `homepage_agent_id` from any row in the workspace already bound to that agent, then sets the binding on the requested site. It reads back and returns the bound site, or `None` if the requested site was missing.

**Call relations**: Homepage setup code calls this to move an agent’s homepage pointer safely. It uses `_read` at the end so callers see the row after the binding change.

*Call graph*: calls 1 internal fn (_read); 1 external calls (update).


##### `HostedSites.release_homepage`  (lines 534–558)

```
async def release_homepage(self, conversation_id: UUID, name: str, visibility: Visibility) -> None
```

**Purpose**: Removes a site’s homepage binding and restores the site’s own visibility setting. This matters because while a site is bound as a homepage, access follows the agent’s visibility rather than only the row’s visibility column.

**Data flow**: It receives a conversation ID, site name, and visibility level to resume. It updates the matching row to clear `homepage_agent_id`, set the visibility, create a new generation ID, and update the timestamp. It returns no value.

**Call relations**: Callers use this when a homepage binding is being removed. It uses a new UUID generation so old authorization grants tied to the previous state do not stay valid.

*Call graph*: 2 external calls (update, uuid4).


##### `HostedSites.unregister`  (lines 560–571)

```
async def unregister(self, conversation_id: UUID, name: str) -> None
```

**Purpose**: Removes a site’s registry row so its permanent link stops resolving. The sandbox process may still exist separately, but the hosted-site name is no longer published.

**Data flow**: It receives a conversation ID and site name. It opens a transaction and deletes the matching row scoped to this workspace. It returns no value whether or not a row was present.

**Call relations**: Unhosting code calls this when a site should no longer be reachable by its registered name. It performs the database delete directly.

*Call graph*: 1 external calls (delete).


##### `HostedSites.refuse_or_pass`  (lines 573–593)

```
async def refuse_or_pass(self, conversation_id: UUID, name: str, port: int, creator_member_id: UUID, visibility: Visibility | None, may_unhost: bool) -> HostedSite | None
```

**Purpose**: Runs the same safety checks as registration without writing anything. This lets a caller ask, before disrupting a running port, whether the later registration would be allowed.

**Data flow**: It receives the same key information used for registration: conversation, name, port, creator, optional visibility, and whether unhosting is allowed. It opens a transaction and delegates to `_refuse`. If the change is unsafe, an exception is raised; if safe, it returns the site that would be displaced, or `None`.

**Call relations**: Deploy flow can call this before it starts serving new bytes on a port. `register` calls `_refuse` again inside the actual write transaction, so the rule is both previewed and enforced.

*Call graph*: calls 1 internal fn (_refuse).


##### `HostedSites._refuse`  (lines 595–628)

```
async def _refuse(self, connection: AsyncConnection, conversation_id: UUID, name: str, port: int, creator_member_id: UUID, visibility: Visibility | None, may_unhost: bool) -> HostedSite | None
```

**Purpose**: Centralizes the rules that can block a registration. It protects creator-only visibility changes, prevents taking over another member’s site, refuses unhosting when there is no proper speaker, and blocks replacing an agent homepage under a different name.

**Data flow**: It receives an open database connection plus the proposed registration details. It reads the existing same-name site, checks whether a non-creator is trying to change its visibility, then looks for another site on the same port. If the port holder is a homepage, someone else’s site, or cannot be unhosted in this turn, it raises the matching error. If everything is safe, it returns the displaced site or `None`.

**Call relations**: `register` uses this inside the write transaction, and `refuse_or_pass` uses it for a no-write precheck. It calls `_read` for the same-name row and `_on_port` for the port conflict row, then raises the purpose-specific refusal errors when needed.

*Call graph*: calls 2 internal fn (_on_port, _read); called by 2 (refuse_or_pass, register); 3 external calls (__init__, __init__, __init__).


##### `HostedSites._on_port`  (lines 630–645)

```
async def _on_port(self, connection: AsyncConnection, conversation_id: UUID, port: int, name: str) -> HostedSite | None
```

**Purpose**: Finds whether another site in the same conversation is already registered on the proposed port. This matters because one port can only represent one current origin, so registering a different name there would effectively unhost the old one.

**Data flow**: It receives an open database connection, conversation ID, port, and the proposed site name. It queries for a row in the same workspace and conversation with that port but a different name. It returns the matching `HostedSite`, or `None` if no different site holds the port.

**Call relations**: `_refuse` calls this while deciding whether registration would displace another site. It uses `_columns` for the query and `_site` to turn the database row into the standard site object.

*Call graph*: calls 2 internal fn (_columns, _site); called by 1 (_refuse); 1 external calls (execute).


##### `HostedSites._read`  (lines 647–659)

```
async def _read(self, connection: AsyncConnection, conversation_id: UUID, name: str) -> HostedSite | None
```

**Purpose**: Reads one hosted-site row using an already-open database connection. It is the shared low-level lookup used by public methods that need one row.

**Data flow**: It receives an open connection, conversation ID, and site name. It queries the hosted-site table for the row in this workspace and conversation. It returns a converted `HostedSite` if found, otherwise `None`.

**Call relations**: Many methods rely on this after updates or for safety checks: `register`, `redeploy`, `read`, `set_visibility`, `set_homepage`, and `_refuse`. It uses `_columns` for consistent selected fields and `_site` for conversion.

*Call graph*: calls 2 internal fn (_columns, _site); called by 6 (_refuse, read, redeploy, register, set_homepage, set_visibility); 1 external calls (execute).


##### `HostedSites._columns`  (lines 661–678)

```
def _columns(self) -> sa.Select
```

**Purpose**: Builds the standard database select statement for hosted-site rows. This keeps all read paths selecting the same fields in the same shape.

**Data flow**: It takes no outside data beyond the class and returns a SQLAlchemy select object containing the columns needed to build a `HostedSite`. It does not execute the query itself.

**Call relations**: Read helpers and listing methods call this, then add their own filters and ordering. It feeds `_read`, `_on_port`, `homepage`, `all`, `conversation`, and `visible_conversation`.

*Call graph*: called by 6 (_on_port, _read, all, conversation, homepage, visible_conversation); 1 external calls (select).


##### `_aware`  (lines 681–684)

```
def _aware(moment: datetime) -> datetime
```

**Purpose**: Makes sure a timestamp read from the database has timezone information. This avoids later comparisons where one time has a timezone and the other does not.

**Data flow**: It receives a `datetime`. If the timestamp already has timezone information, it returns it unchanged. If it is missing a timezone, it marks it as UTC and returns the adjusted value.

**Call relations**: `_site` calls this for `created_at` and `updated_at` when converting database rows. It exists because some databases, such as SQLite, can return timezone-aware columns as plain naive timestamps.

*Call graph*: called by 1 (_site); 1 external calls (replace).


##### `_site`  (lines 687–704)

```
def _site(row: sa.Row) -> HostedSite
```

**Purpose**: Turns a database row into the `HostedSite` data object used by the rest of the code. It also revalidates visibility and normalizes timestamps during conversion.

**Data flow**: It receives a SQLAlchemy row with hosted-site columns. It reads each field, passes visibility through `visibility_level`, passes timestamps through `_aware`, and constructs a `HostedSite`. The output is a plain immutable object representing one registered site.

**Call relations**: All read paths use this after fetching rows: `_read`, `_on_port`, `homepage`, `all`, `conversation`, and `visible_conversation`. It is the boundary between raw database results and the safer application-level site record.

*Call graph*: calls 2 internal fn (_aware, visibility_level); called by 6 (_on_port, _read, all, conversation, homepage, visible_conversation); 1 external calls (__init__).


### Artifact downloads
Artifact definitions, signed links, and download routes let shared files be listed, refreshed, authorized, served, or deleted safely.

### `core/src/ufo/runtime/media/artifact_url.py`

`domain_logic` · `artifact link creation and download request handling`

This file is the gatekeeper for artifact download URLs. An artifact is stored under a workspace-owned key like `artifacts/<artifact-id>/<filename>`. The file turns that storage key into a URL with a signed query string. The signature is like a tamper-proof wax seal: if someone changes the artifact id, expiry time, workspace, or preview permission, the seal no longer matches and the file is not served.

It also decides what kind of file an artifact is, using a fixed list for important file extensions so behavior stays the same on laptops and servers. For images, it can add a special preview claim that allows safe inline display, but only for known raster image types and only below a size limit.

A key detail is expiry bucketing. Instead of making every URL expire at a unique second, `artifact_url_expiry` rounds expiry up to a shared boundary. That means repeated links for the same artifact can be byte-for-byte identical for a while, which lets browsers and edge caches reuse downloads instead of fetching again.

Verification is strict. The artifact id must be a proper UUID, the filename cannot contain path separators, and the blob path must stay inside the artifact namespace. Old URLs without a workspace claim can still be recognized, but they are not served directly; they must be refreshed by a signed-in workspace member.

#### Function details

##### `is_text_media`  (lines 70–77)

```
def is_text_media(media_type: str) -> bool
```

**Purpose**: This function answers whether a media type represents text that can reasonably be shown as readable characters. It includes all `text/*` types plus selected `application/*` types such as JSON, YAML, TOML, shell scripts, XML, and TypeScript.

**Data flow**: It takes a media type string, lowercases it, and compares it with the known text-like categories. It returns `true` when the type should be treated as readable text, and `false` otherwise. It does not change any outside state.

**Call relations**: Other parts of the product can use this as the shared rule for deciding whether an artifact should be displayed inline as text. It does not call other project functions; it is a small policy helper used wherever artifact media needs to be classified.


##### `artifact_url_expiry`  (lines 84–91)

```
def artifact_url_expiry(now: datetime) -> int
```

**Purpose**: This function chooses the expiry timestamp to put into a newly minted artifact URL. It deliberately rounds expiry up to a fixed time bucket so repeated links stay identical long enough for caching to work.

**Data flow**: It takes the current time, converts it to seconds, adds the configured time-to-live, then rounds up to the next bucket boundary. It returns that future expiry as an integer timestamp. Nothing else is modified.

**Call relations**: When `mint_image_preview_url` needs to create a fresh preview link, it asks this function for the expiry time. The result is later signed by `mint_artifact_url`, so the same expiry value becomes part of the tamper-proof URL grant.

*Call graph*: called by 1 (mint_image_preview_url); 1 external calls (timestamp).


##### `ArtifactUrlExpired.__init__`  (lines 113–115)

```
def __init__(self, claims: ArtifactClaims) -> None
```

**Purpose**: This constructor creates an error that specifically means: the URL was authentic, but it is no longer fresh enough to use directly. It keeps the verified claims attached so another trusted path, such as a signed-in member refresh, can decide what to do next.

**Data flow**: It receives already-verified artifact claims, stores a standard error message, and attaches those claims to the exception object. The output is an exception instance carrying both the reason and the artifact details.

**Call relations**: `verify_artifact_url` calls this when a URL has a valid signature but is expired, or when it is an older workspace-less URL that must not be served directly. This lets callers distinguish expired-but-real links from malformed or tampered ones.

*Call graph*: called by 1 (verify_artifact_url).


##### `artifact_media_type`  (lines 118–131)

```
def artifact_media_type(filename: str) -> str
```

**Purpose**: This function decides the MIME type, meaning the browser-facing file type label, for an artifact filename. It uses a project-controlled list for important extensions so production and development machines do not disagree.

**Data flow**: It receives a filename, looks at its final suffix, and first checks the project's known artifact type table. If there is no project rule, it asks Python's MIME guessing library. If the guess involves compression encoding or no safe answer exists, it returns the generic binary fallback `application/octet-stream`.

**Call relations**: Artifact serving and preview decisions can use this function to label stored bytes consistently. Internally it relies on `PurePosixPath` to read the suffix and `mimetypes.guess_type` only as a secondary source, avoiding host-specific surprises for key file types.

*Call graph*: 2 external calls (guess_type, PurePosixPath).


##### `mint_artifact_url`  (lines 134–157)

```
def mint_artifact_url(secret: str, blob_key: str, expires_at: int, *, workspace_id: UUID, preview: ImagePreviewGrant | None=None) -> str
```

**Purpose**: This function creates the signed, relative download URL for one artifact blob in one workspace. It is used when the system wants to hand someone a link that can be fetched without logging in, but only if the link has not been altered or expired.

**Data flow**: It takes the signing secret, blob key, expiry timestamp, workspace id, and optional image preview grant. It checks that a secret exists, splits and validates the blob key, encodes the optional preview permission, signs the workspace id, artifact id, expiry, and preview value, then builds a URL path and query string. The output is a relative URL such as `/artifacts/...?...&sig=...`; invalid inputs raise an artifact URL error.

**Call relations**: `mint_image_preview_url` calls this after deciding an image is eligible for inline preview. Inside, it uses `_split_key` to prove the blob key is a safe artifact address, `_parsed_preview` to sanity-check preview claims, `_signed_message` to build the exact bytes to seal, and `sign_detached` to make the signature.

*Call graph*: calls 3 internal fn (_parsed_preview, _signed_message, _split_key); called by 1 (mint_image_preview_url); 3 external calls (__init__, sign_detached, quote).


##### `mint_image_preview_url`  (lines 160–188)

```
def mint_image_preview_url(secret: str, public_base_url: str | None, blob_key: str, size_bytes: int | None, *, workspace_id: UUID) -> str | None
```

**Purpose**: This function creates an absolute signed URL for displaying a stored raster image inline, or returns nothing if the image is not safe or eligible for preview. Raster images are pixel-based formats such as PNG, JPEG, or GIF.

**Data flow**: It receives the signing secret, public base URL, blob key, optional file size, and workspace id. It first refuses to proceed if signing or public delivery is not configured. It then checks whether the blob key names a supported raster image and whether the size is known and below the preview limit. If all checks pass, it chooses a rounded expiry, creates an image preview grant, mints a signed artifact path, and prefixes it with the public base URL.

**Call relations**: This is a convenience path for callers that want preview-ready image links. It asks `raster_image_media_type` what kind of image the blob appears to be, uses `artifact_url_expiry` for cache-friendly timing, and delegates the actual signing work to `mint_artifact_url`.

*Call graph*: calls 2 internal fn (artifact_url_expiry, mint_artifact_url); 3 external calls (__init__, now, raster_image_media_type).


##### `verify_artifact_url`  (lines 191–232)

```
def verify_artifact_url(secret: str, artifact_id: str, filename: str, expires_at: str, signature: str, preview: str, workspace: str, now: datetime) -> ArtifactClaims
```

**Purpose**: This function checks whether an incoming artifact URL proves permission to fetch a specific artifact. It rejects malformed, tampered, out-of-scope, and expired links, while returning clear claims for valid links.

**Data flow**: It receives the signing secret and the pieces of the URL: artifact id, filename, expiry, signature, preview claim, workspace id, and current time. It validates the shapes of the id, filename, workspace, and expiry; parses the preview claim if present; rebuilds the exact signed message; and checks the signature. If everything is authentic, it creates artifact claims containing the workspace, blob key, filename, expiry, and preview permission. If the URL is expired or lacks a workspace claim, it raises `ArtifactUrlExpired` with those claims; otherwise it returns the claims.

**Call relations**: The artifact download route is the natural caller of this function during request handling. It uses `_is_canonical_uuid`, `_is_filename`, and `_parsed_preview` for input checks, `_signed_message` to reconstruct what should have been signed, and `verify_detached` to test the signature. When the link is real but no longer directly usable, it hands the verified details to `ArtifactUrlExpired`.

*Call graph*: calls 5 internal fn (__init__, _is_canonical_uuid, _is_filename, _parsed_preview, _signed_message); 5 external calls (__init__, __init__, timestamp, verify_detached, UUID).


##### `_signed_message`  (lines 235–237)

```
def _signed_message(workspace: str, artifact_id: str, expires_at: str, preview_value: str) -> bytes
```

**Purpose**: This helper builds the exact byte string that is signed when creating a URL and checked when verifying it. Having one shared builder prevents the minter and verifier from disagreeing about what the signature covers.

**Data flow**: It takes the workspace id as text, artifact id, expiry text, and preview text. If a workspace is present, it includes it at the front; otherwise it uses the older workspace-less form. It joins the parts with colons and returns the encoded bytes.

**Call relations**: `mint_artifact_url` calls this before signing a new link, and `verify_artifact_url` calls it again before checking an incoming link. Because both sides use the same helper, a valid round trip matches by construction.

*Call graph*: called by 2 (mint_artifact_url, verify_artifact_url).


##### `_parsed_preview`  (lines 240–251)

```
def _parsed_preview(value: str) -> ImagePreviewGrant | None
```

**Purpose**: This helper validates and decodes the optional image preview claim from a URL. The claim says what raster image type may be shown inline and exactly how many bytes are expected.

**Data flow**: It receives a preview string in the form `media/type:size`. It splits at the last colon, checks that the media type is one of the supported raster image types, checks that the size is numeric, and rejects anything above the preview byte limit. On success it returns an `ImagePreviewGrant`; on failure it returns `None`.

**Call relations**: `mint_artifact_url` uses this as a guard before signing a preview claim, and `verify_artifact_url` uses it to interpret a preview claim from an incoming URL. This keeps preview creation and preview checking aligned.

*Call graph*: called by 2 (mint_artifact_url, verify_artifact_url); 3 external calls (__init__, cast, values).


##### `_split_key`  (lines 254–263)

```
def _split_key(blob_key: str) -> tuple[str, str]
```

**Purpose**: This helper proves that a blob key is a valid artifact storage address and separates it into artifact id and filename. It prevents callers from minting signed URLs for paths outside the artifact area.

**Data flow**: It receives a blob key string. It requires the key to start with `artifacts/`, contain a UUID artifact id followed by one filename segment, and use a safe filename that is not empty, `.` or `..`, and has no slash. It returns the artifact id and filename, or raises an artifact URL error if the key is not a valid artifact address.

**Call relations**: `mint_artifact_url` calls this before it signs anything. Inside, it relies on `_is_canonical_uuid` and `_is_filename` for the two main safety checks, so only well-shaped artifact keys can become download URLs.

*Call graph*: calls 2 internal fn (_is_canonical_uuid, _is_filename); called by 1 (mint_artifact_url); 1 external calls (__init__).


##### `_is_canonical_uuid`  (lines 266–270)

```
def _is_canonical_uuid(value: str) -> bool
```

**Purpose**: This helper checks whether a string is a UUID in the project's exact normal form. A UUID is a standard unique identifier; the canonical form avoids accepting lookalike or differently formatted ids.

**Data flow**: It receives a string, tries to parse it as a UUID, and then compares the parsed UUID's normal string form back to the original. It returns `true` only when they match exactly; parsing failures return `false`.

**Call relations**: `_split_key` uses this when minting URLs from blob keys, and `verify_artifact_url` uses it when checking incoming artifact and workspace ids. It is one of the basic guards that keeps signed links tied to well-formed identifiers.

*Call graph*: called by 2 (_split_key, verify_artifact_url); 1 external calls (UUID).


##### `_is_filename`  (lines 273–274)

```
def _is_filename(value: str) -> bool
```

**Purpose**: This helper checks whether a string is a single safe filename segment. It prevents path tricks such as slashes or `..` from being treated as normal artifact filenames.

**Data flow**: It receives a filename string and returns `true` only if it is non-empty, contains no slash, and is not `.` or `..`. It does not read or change anything else.

**Call relations**: `_split_key` uses this before minting a URL, and `verify_artifact_url` uses it before trusting a filename from an incoming request. Together with the artifact id check, it ensures artifact URLs can only point to `artifacts/<id>/<filename>`.

*Call graph*: called by 2 (_split_key, verify_artifact_url).


### `core/src/ufo/host/kinds/artifacts.py`

`domain_logic` · `request handling`

This file turns previously shared files into workspace objects that people and agents can browse safely. Think of it like a library catalog for files produced during conversations: the bytes live in blob storage, while this code gives each file a stable shelf label, shows useful details, and controls who can see it.

An artifact is identified by both the conversation it came from and its filename. If the same conversation shares the same filename again, that becomes a new version of the same artifact. If another conversation shares a file with the same filename, it is a different artifact. The visible name combines a short conversation prefix with a cleaned-up filename, so related files sort together.

The main class, ArtifactObjects, provides the object-kind operations. It can list visible artifacts, fetch details, produce status information, copy small files back into the current workspace for reuse, mint short-lived download and preview links, and delete all versions of an artifact. It deliberately refuses create and update requests, because artifacts are meant to come only from share_file.

A large part of the file is about safety and visibility. Queries stay inside the current workspace, selected agent, and allowed audience. Member-facing views are narrower: a signed-in member only sees files shared after a member joined that conversation.

#### Function details

##### `artifact_media`  (lines 83–97)

```
def artifact_media(media_type: str) -> str
```

**Purpose**: Sorts a file’s MIME type, which is a standard label like image/png or application/pdf, into a simple category: image, document, or other. This lets listings filter by a friendly field instead of needing callers to understand every possible file type.

**Data flow**: It receives a media type string, lowercases it, checks whether it looks like an image, readable text, office document, PDF, or Word file, and returns one of three plain category names.

**Call relations**: ArtifactObjects._row calls this while building each listing row, so every artifact shown to a reader carries an easy-to-filter media category.

*Call graph*: called by 1 (_row); 1 external calls (is_text_media).


##### `_document_media`  (lines 100–106)

```
def _document_media() -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database test for deciding whether an artifact is a document. It mirrors artifact_media’s document rules, but in a form the database can use while filtering rows.

**Data flow**: It reads the shared_artifact media_type column, lowercases it inside the database query, and produces a SQL condition that is true for text, office, PDF, and Word-like types.

**Call relations**: ArtifactObjects._groups uses this when a listing asks for media=document or media=other, so the database can narrow the result before rows are loaded.

*Call graph*: called by 1 (_groups); 1 external calls (or_).


##### `_member_participated`  (lines 109–121)

```
def _member_participated() -> sa.ColumnElement[bool]
```

**Purpose**: Builds a database condition that says: only include shares made after a member had entered that conversation. This protects member views from showing files that predate member participation.

**Data flow**: It creates a query against turns in the same workspace and conversation, looks for a member-admission turn at or before the artifact’s turn, and returns an exists-style database condition.

**Call relations**: ArtifactObjects._member_shares adds this condition to the normal artifact query whenever the portal asks for a member-facing page or detail.

*Call graph*: called by 1 (_member_shares); 2 external calls (literal, select).


##### `artifact_object_names`  (lines 124–144)

```
def artifact_object_names(shares: Iterable[tuple[UUID, str]]) -> dict[tuple[UUID, str], str]
```

**Purpose**: Creates stable object names for artifact identities. Each identity is the pair of conversation ID and filename, so the same filename in two conversations does not accidentally become the same object.

**Data flow**: It receives many conversation-and-filename pairs, removes duplicates, turns each filename into a short safe slug, prefixes it with part of the conversation ID, and adds a short digest only if two names still collide. It returns a mapping from each identity to its object name.

**Call relations**: ArtifactObjects._identities calls this after reading all distinct visible artifact identities from the database. It uses _slug for the readable part and _identity_digest for rare collision suffixes.

*Call graph*: calls 2 internal fn (_identity_digest, _slug); called by 1 (_identities); 1 external calls (Counter).


##### `_slug`  (lines 147–149)

```
def _slug(filename: str) -> str
```

**Purpose**: Turns a filename into a short, safe, lowercase name fragment. This makes artifact names readable and usable in object references.

**Data flow**: It receives a filename, replaces runs of non-letter-or-number characters with hyphens, trims extra hyphens, limits the length, and falls back to artifact if nothing readable remains.

**Call relations**: artifact_object_names calls this for every distinct filename while constructing the public artifact object names.

*Call graph*: called by 1 (artifact_object_names).


##### `_identity_digest`  (lines 152–154)

```
def _identity_digest(identity: tuple[UUID, str]) -> str
```

**Purpose**: Creates a deterministic shortable fingerprint for an artifact identity. It is used only when two different artifacts would otherwise get the same readable name.

**Data flow**: It receives a conversation ID and filename, joins them into a string, hashes that string with SHA-256, and returns the hexadecimal digest.

**Call relations**: artifact_object_names calls this when it needs to add a collision-breaking suffix to an artifact name.

*Call graph*: called by 1 (artifact_object_names); 1 external calls (sha256).


##### `ArtifactObjects.list`  (lines 179–186)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists artifact objects visible to the current tool context. This is the agent-side listing path.

**Data flow**: It reads the caller’s allowed subjects and member identity from the context, builds the normal shares query, asks _rows to turn matching shares into object rows, and wraps those rows into a paged result.

**Call relations**: The object system calls this for artifact list requests. It hands the heavy work to _shares and _rows, then uses object_page to apply the standard page shape.

*Call graph*: calls 2 internal fn (_rows, _shares); 2 external calls (authority_member_id, object_page).


##### `ArtifactObjects.member_page`  (lines 188–207)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Builds the member-facing Artifacts page for one signed-in member. It shows only files inside that member’s audience fence and only after member participation in the source conversation.

**Data flow**: It derives the subjects visible to the member’s conversation, builds the stricter member shares query, turns the result into rows, and returns a paged object list.

**Call relations**: Portal-style member browsing calls this instead of the agent list path. It uses conversation_audience and audience_subjects to find the member’s visibility boundary, then relies on _member_shares and _rows.

*Call graph*: calls 2 internal fn (_member_shares, _rows); 3 external calls (object_page, audience_subjects, conversation_audience).


##### `ArtifactObjects.get`  (lines 209–211)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ArtifactSpec] | None
```

**Purpose**: Fetches the object detail for one artifact name in the current tool context. It returns metadata, not the file bytes.

**Data flow**: It receives a context and object name, searches visible shares for that named artifact, and returns None if not found. If found, it converts the artifact’s versions into an ObjectDetail.

**Call relations**: The object system calls this for object_get-style detail reads. It uses _find to resolve the name and _detail to package the latest spec and creation/update times.

*Call graph*: calls 3 internal fn (_find, _shares, _detail).


##### `ArtifactObjects.member_detail`  (lines 213–230)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[ArtifactSpec] | None
```

**Purpose**: Fetches one artifact detail for a signed-in member’s portal view. It includes both the detail record and the row fields used by member-facing UI.

**Data flow**: It computes the member’s allowed subjects, finds the named artifact within member-visible shares, and returns None if absent. If present, it reads the conversation source, builds a row with links and fields, and pairs it with the detail object.

**Call relations**: Member artifact detail pages call this. It follows the same visibility rules as member_page, using _member_shares, _find, _sources, _row, and _detail.

*Call graph*: calls 5 internal fn (_find, _member_shares, _row, _sources, _detail); 3 external calls (__init__, audience_subjects, conversation_audience).


##### `ArtifactObjects.status`  (lines 232–276)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns live status for an artifact and, when the file is small enough, copies its latest bytes back into the current conversation workspace. This is how a later turn can reuse a file created by an earlier turn.

**Data flow**: It receives a context and object name, finds the latest visible version, optionally reads its bytes from blob storage if it is below the materialization size limit, rechecks that the artifact is still visible, writes the bytes into artifacts/<name>/<filename> in the sandbox when possible, mints a fresh download link when configured, and returns size, time, turn, version count, link, and workspace path.

**Call relations**: The object-get flow calls status when it needs the usable runtime state. It depends on _find and _unchanged_visible for safe lookup, uses blob storage and sandbox writing for bytes, and calls the artifact URL helpers for temporary download links.

*Call graph*: calls 3 internal fn (_find, _shares, _unchanged_visible); 6 external calls (__init__, now, workspace_tx, artifact_url_expiry, mint_artifact_url, ws_current).


##### `ArtifactObjects.apply`  (lines 278–287)

```
async def apply(self, ctx: ToolContext, name: str, spec: ArtifactSpec, old: ArtifactSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to create or update an artifact through the object API. Artifacts must be produced by writing a workspace file and sharing it with share_file.

**Data flow**: It receives the requested artifact name and spec but does not store anything. It immediately raises VerbNotSupported with guidance explaining the correct path.

**Call relations**: The object system would call this for create or update verbs, but artifact_object does not advertise those as agent target verbs. If reached, it stops the flow clearly.

*Call graph*: 1 external calls (__init__).


##### `ArtifactObjects.delete`  (lines 289–315)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Deletes an artifact and all of its versions. It removes both the database records and the stored blobs, including preview blobs when present.

**Data flow**: It receives a context and object name, resolves all visible versions, locks and rechecks the latest conversation visibility to avoid deleting a changed object, deletes matching shared_artifact rows, verifies the expected number disappeared, then deletes each stored blob key.

**Call relations**: The object system calls this for artifact delete requests. It uses _find and _unchanged_visible before deleting database rows, then hands off to the blob store to remove the actual bytes.

*Call graph*: calls 3 internal fn (_find, _shares, _unchanged_visible); 3 external calls (delete, workspace_tx, ws_current).


##### `ArtifactObjects._unchanged_visible`  (lines 317–324)

```
def _unchanged_visible(self, ctx: ToolContext, latest: sa.Row) -> sa.Select
```

**Purpose**: Builds a database query that confirms a previously found artifact still belongs to the same visible conversation. This protects status and delete from acting on stale or no-longer-visible data.

**Data flow**: It takes the current context and the latest artifact row, then creates a select query constrained by workspace, conversation ID, selected agent, audience value, and the caller’s readable subjects.

**Call relations**: ArtifactObjects.status uses this before writing bytes into the workspace. ArtifactObjects.delete uses it, with a row lock, before deleting records and blobs.

*Call graph*: called by 2 (delete, status); 3 external calls (select, object_agent_id, ws_current).


##### `ArtifactObjects._rows`  (lines 326–337)

```
async def _rows(self, subjects: frozenset[str], viewer: UUID | None, query: ObjectListQuery, shares: sa.Select) -> tuple[ObjectRow, ...]
```

**Purpose**: Turns a filtered set of artifact shares into the row objects used by listings. It also enriches rows with source information from their conversations.

**Data flow**: It receives visibility subjects, an optional viewer member ID, a list query, and a shares query. It groups shares by artifact, fetches each conversation’s source link, converts every group into an ObjectRow, and returns the rows.

**Call relations**: ArtifactObjects.list and ArtifactObjects.member_page call this after choosing the right visibility query. It coordinates _groups, _sources, and _row.

*Call graph*: calls 3 internal fn (_groups, _row, _sources); called by 2 (list, member_page).


##### `ArtifactObjects._find`  (lines 339–359)

```
async def _find(self, subjects: frozenset[str], name: str, shares: sa.Select) -> tuple[sa.Row, ...] | None
```

**Purpose**: Resolves one artifact object name into all of its share versions within a given visibility projection. This is the precise lookup path for get, status, delete, and member detail.

**Data flow**: It receives allowed subjects, a public object name, and a base shares query. It first builds the full name map for visible identities, finds which conversation and filename the name means, loads matching rows, sorts them newest first, and returns the tuple or None.

**Call relations**: ArtifactObjects.get, member_detail, status, and delete all call this before doing their own work. It uses _identities so names are consistent with listings even when a particular listing was limited by scan size.

*Call graph*: calls 1 internal fn (_identities); called by 4 (delete, get, member_detail, status); 2 external calls (where, workspace_tx).


##### `ArtifactObjects._groups`  (lines 361–426)

```
async def _groups(self, subjects: frozenset[str], viewer: UUID | None, query: ObjectListQuery, shares: sa.Select) -> Sequence[tuple[str, tuple[sa.Row, ...]]]
```

**Purpose**: Applies listing filters and groups recent share rows into artifact objects. This is where search text, conversation, owner, surface, and media filters take effect.

**Data flow**: It starts with a shares query, adds filters from ObjectListQuery, orders newest shares first, limits the scan, loads rows from the database, groups them by conversation and filename, assigns stable names from the full identity set, sorts versions newest first inside each group, and returns groups sorted by name.

**Call relations**: ArtifactObjects._rows calls this during list and member_page. It uses _document_media for document filtering, _identities for stable names, and workspace_tx to read from the database.

*Call graph*: calls 2 internal fn (_identities, _document_media); called by 1 (_rows); 5 external calls (false, not_, or_, workspace_tx, UUID).


##### `ArtifactObjects._identities`  (lines 428–451)

```
async def _identities(self, subjects: frozenset[str]) -> dict[tuple[UUID, str], str]
```

**Purpose**: Finds every distinct artifact identity visible to the current audience and assigns each one its stable object name. This keeps naming consistent across listing and direct lookup.

**Data flow**: It receives allowed subjects, queries the database for distinct conversation-and-filename pairs in the current workspace and selected agent, then passes those pairs to artifact_object_names.

**Call relations**: ArtifactObjects._find uses this to interpret a requested object name. ArtifactObjects._groups uses it to name grouped listing results.

*Call graph*: calls 1 internal fn (artifact_object_names); called by 2 (_find, _groups); 4 external calls (select, workspace_tx, object_agent_id, ws_current).


##### `ArtifactObjects._shares`  (lines 453–488)

```
def _shares(self, subjects: frozenset[str]) -> sa.Select
```

**Purpose**: Builds the base database query for artifact shares visible to an agent-side reader. It joins artifact records to turns, conversations, and optional member information.

**Data flow**: It receives allowed audience subjects and returns a SQL select that includes artifact metadata, conversation details, owner information, and only rows from the current workspace, selected agent, and permitted audiences.

**Call relations**: ArtifactObjects.list, get, status, delete, and _member_shares start from this query. Later helpers add filters, group rows, or look up a single named artifact.

*Call graph*: called by 5 (_member_shares, delete, get, list, status); 3 external calls (select, object_agent_id, ws_current).


##### `ArtifactObjects._member_shares`  (lines 490–491)

```
def _member_shares(self, subjects: frozenset[str]) -> sa.Select
```

**Purpose**: Builds the stricter base query for member-facing artifact reads. It starts from the normal visibility query and adds the rule that the member must have participated by that point in the conversation.

**Data flow**: It receives allowed subjects, calls _shares to get the normal query, adds the _member_participated database condition, and returns the narrowed query.

**Call relations**: ArtifactObjects.member_page and ArtifactObjects.member_detail use this so member portal reads are narrower than agent reads.

*Call graph*: calls 2 internal fn (_shares, _member_participated); called by 2 (member_detail, member_page).


##### `ArtifactObjects._sources`  (lines 493–530)

```
async def _sources(self, conversation_ids: Sequence[UUID]) -> dict[UUID, str | None]
```

**Purpose**: Reads the opening source for each conversation, such as the permalink or origin recorded on the first turn. Listing rows use this to show where an artifact’s conversation began.

**Data flow**: It receives conversation IDs, returns an empty mapping if there are none, otherwise finds the first turn sequence per conversation, loads that turn’s context, validates it as TurnContext, and returns a mapping from conversation ID to source string or None.

**Call relations**: ArtifactObjects._rows calls this to enrich listing rows. ArtifactObjects.member_detail calls it when building the single member-facing row.

*Call graph*: called by 2 (_rows, member_detail); 5 external calls (model_validate, and_, select, workspace_tx, ws_current).


##### `ArtifactObjects._row`  (lines 532–560)

```
def _row(self, name: str, shares: tuple[sa.Row, ...], viewer: UUID | None, sources: dict[UUID, str | None]) -> ObjectRow
```

**Purpose**: Builds one listing row for an artifact group. It presents the latest version’s useful facts plus links and ownership fields.

**Data flow**: It receives the object name, all versions of that artifact sorted newest first, an optional viewer member ID, and conversation sources. It reads the latest row, creates a summary, fills fields such as filename, subject, media, owner, source, mine, download URL, and preview URL, and returns an ObjectRow.

**Call relations**: ArtifactObjects._rows uses this for listings, and ArtifactObjects.member_detail uses it for a single member object. It calls _summary, artifact_media, _download_url, and _preview_url.

*Call graph*: calls 4 internal fn (_download_url, _preview_url, _summary, artifact_media); called by 2 (_rows, member_detail); 1 external calls (__init__).


##### `ArtifactObjects._download_url`  (lines 562–571)

```
def _download_url(self, latest: sa.Row) -> str | None
```

**Purpose**: Creates a short-lived signed download URL for an artifact version when this deployment is configured to serve such links. A signed URL is a link that proves permission without exposing the secret itself.

**Data flow**: It receives the latest artifact row. If either the token secret or public base URL is missing, it returns None. Otherwise it mints a temporary artifact path for the blob key and prefixes it with the public base URL.

**Call relations**: ArtifactObjects._row calls this while preparing listing rows, so UI clients can show a direct download link when available.

*Call graph*: called by 1 (_row); 4 external calls (now, artifact_url_expiry, mint_artifact_url, ws_current).


##### `ArtifactObjects._preview_url`  (lines 573–593)

```
def _preview_url(self, latest: sa.Row) -> str | None
```

**Purpose**: Creates a signed image-preview link when a safe raster image preview is available. Raster means a pixel-based image such as PNG or JPEG.

**Data flow**: It chooses the preview blob if one exists, otherwise the original blob. It checks that the blob key’s image type matches the declared media type, and returns None if not. If valid, it mints a preview URL using the blob key, size, workspace, base URL, and secret.

**Call relations**: ArtifactObjects._row calls this while preparing listing rows, so portal or object browsers can show thumbnails for images and generated previews.

*Call graph*: called by 1 (_row); 3 external calls (mint_image_preview_url, raster_image_media_type, ws_current).


##### `_detail`  (lines 596–612)

```
def _detail(shares: tuple[sa.Row, ...]) -> ObjectDetail[ArtifactSpec]
```

**Purpose**: Builds the detailed object record for an artifact. It describes the latest version and links the artifact back to the conversation that created it.

**Data flow**: It receives all versions of one artifact, reads the newest and oldest timestamps, creates an ArtifactSpec from the latest filename, media type, and subject, and returns an ObjectDetail with a created_in conversation link.

**Call relations**: ArtifactObjects.get and ArtifactObjects.member_detail call this after _find has resolved the requested artifact name.

*Call graph*: called by 2 (get, member_detail); 4 external calls (__init__, __init__, __init__, __init__).


##### `_summary`  (lines 615–621)

```
def _summary(shares: tuple[sa.Row, ...]) -> str
```

**Purpose**: Creates a short human-readable summary for a listing row. It gives the filename, media type, size, share date, and version count when there is more than one version.

**Data flow**: It receives all versions of an artifact, reads the latest version, formats a sentence-like summary, adds a versions note if needed, and trims it to the maximum summary length.

**Call relations**: ArtifactObjects._row calls this whenever it builds an ObjectRow for a list or member detail view.

*Call graph*: called by 1 (_row).


##### `artifact_object`  (lines 624–685)

```
def artifact_object(*, public_base_url: str | None=None, artifact_token_secret: str='') -> ObjectKind
```

**Purpose**: Registers the artifact object kind with the larger object system. It defines what artifacts are, which fields listings expose, which verbs are allowed, and which ArtifactObjects store instance should serve requests.

**Data flow**: It receives optional public URL and token secret settings, constructs an ArtifactObjects store with those settings, and returns an ObjectKind containing the name, description, guidance text, spec model, list fields, and allowed agent verbs.

**Call relations**: Startup or object-kind registration code calls this to make artifacts available. The returned ObjectKind routes later list, get, and delete requests into ArtifactObjects.

*Call graph*: 2 external calls (__init__, __init__).


### `core/src/ufo/runtime/surfaces/artifacts.py`

`io_transport` · `request handling`

This file is the gatekeeper for shared file downloads. A shared artifact link is like a temporary ticket: it contains a signature proving that the server created it, an expiry time, the file identity, and the workspace it belongs to. Without this route, links created by the system for downloads, previews, or oversized attachments would have nowhere safe and consistent to resolve.

When a request arrives, the route checks the signed URL before touching the file store. If the signature is wrong, the request is refused. If the link is valid, it opens the correct workspace’s blob store and either streams the file as a download or returns a carefully checked image preview. Streaming matters because large files are sent in pieces instead of being loaded fully into memory.

The file also has a useful recovery path for expired links. If a teammate opens an old link, the server checks their normal portal session cookie and verifies that they are a member of the workspace that owns the artifact. If so, it redirects them to a freshly signed URL. If not, a browser is sent to sign in, while other clients get a clear refusal. Successful file responses may be briefly cached, but failures and redirects are marked private and not stored.

#### Function details

##### `download`  (lines 59–117)

```
async def download(request: Request, artifact_id: str, filename: str, exp: str='', sig: str='', preview: str='', workspace: Annotated[str, Query(alias='ws')]='') -> Response
```

**Purpose**: This is the HTTP endpoint that serves an artifact link. It verifies that the URL is genuinely signed, then either streams the original file as a download or returns an approved image preview.

**Data flow**: It receives the web request, the artifact id, filename, signature fields, optional preview request, and workspace id from the URL. It reads the blob store and artifact signing secret from the application state, checks the URL claims, and uses the workspace named by those claims to look up the stored bytes. If everything is valid, it returns either a normal response containing preview bytes or a streaming response that sends the file in chunks; if the link is bad, missing scope, expired without recovery, or the file is gone, it returns an appropriate refusal.

**Call relations**: This is the front door for artifact downloads. It calls the artifact URL verifier before reading any bytes, asks `_refreshed_for_member` for help when the only problem is expiry, uses image-preview validation when a preview was requested, and uses the media-type helper so the browser is told what kind of file it is receiving.

*Call graph*: calls 1 internal fn (_refreshed_for_member); 9 external calls (now, HTTPException, Response, StreamingResponse, artifact_media_type, verify_artifact_url, validated_image_preview, ws, quote).


##### `_refreshed_for_member`  (lines 120–171)

```
async def _refreshed_for_member(request: Request, claims: ArtifactClaims, secret: str) -> RedirectResponse
```

**Purpose**: This helper tries to turn an expired but otherwise authentic artifact link into a new working link for someone who is already signed in and allowed to see it. It keeps old team links useful without letting outsiders revive them.

**Data flow**: It receives the current request, the expired link’s verified claims, and the signing secret. It reads the session cookie, checks the session claims, turns the workspace id into a real UUID, then queries the database to confirm two things: the signed-in user is a member of that workspace, and that workspace owns the artifact. If both checks pass, it creates a new expiry time, mints a fresh signed artifact URL, and returns a redirect to it; otherwise it raises a refusal.

**Call relations**: The main `download` route calls this only after `verify_artifact_url` says the URL is expired but still has trustworthy claims. This helper relies on `_refusal` for the not-allowed path, uses the database transaction helper to read membership and artifact ownership, and hands the user back to `download` through a redirect to a newly minted URL.

*Call graph*: calls 1 internal fn (_refusal); called by 1 (download); 10 external calls (now, RedirectResponse, or_, select, workspace_tx, verified_claims, artifact_url_expiry, mint_artifact_url, ws, UUID).


##### `_refusal`  (lines 174–179)

```
def _refusal(request: Request) -> HTTPException
```

**Purpose**: This small helper builds the correct denial response when an expired artifact link cannot be refreshed. It treats browsers differently from non-browser clients so people can be guided to sign in.

**Data flow**: It receives the request and looks at the `Accept` header, which hints what kind of response the client wants. If the client accepts HTML, it URL-encodes the current artifact path and query string and returns an HTTP redirect-style exception pointing to the login page with this artifact link as the post-login target. Otherwise, it returns a plain forbidden error explaining that the link expired.

**Call relations**: `_refreshed_for_member` calls this whenever there is no valid signed-in workspace member to refresh the link for. It is the final branch of the recovery flow: either the caller gets a fresh signed URL, or this helper tells them to sign in or refuses access.

*Call graph*: called by 1 (_refreshed_for_member); 2 external calls (HTTPException, quote).


### Preview rendering
Document rendering, image validation, and preview metadata protect and standardize stored visual previews.

### `core/src/ufo/harness/document_renderer.py`

`io_transport` · `request handling`

This file is the bridge between the main system and an outside document preview service called ufo-preview. Its job is to take raw document bytes, ask the service to render a small page range, then return a safe, predictable result: page text plus PNG images encoded as text so they can travel in JSON-like tool output.

The process is deliberately guarded. Before sending anything, it rejects documents over a fixed input size. It also normalizes the requested page range, so callers cannot ask for page zero or too many pages at once. The render request is sent over HTTP with a bearer token, which is like showing a pass at the door.

The preview service replies with a zip bundle. Think of this as a small folder in a single package: one manifest file describing the pages, plus one PNG image per page. The file then opens that package, checks that the manifest matches what was requested, confirms page numbers are sensible and consecutive, and verifies that no surprise files are present. It also enforces caps for the manifest, each image, all images together, and their base64 form.

The final result includes the original path, document type, combined text, page count, next page hint, rendered images, and a quality reminder telling the caller to inspect the visual output for rendering problems.

#### Function details

##### `DocumentRenderer.render`  (lines 60–107)

```
async def render(self, path: str, kind: str, content: bytes, start_page: int, limit: int) -> dict[str, object]
```

**Purpose**: This is the public entry point for rendering a document. It checks the document size, builds a safe rendering request, sends the document to the preview service, collects the returned bundle, and starts unpacking it.

**Data flow**: It receives a file path, document kind, raw document bytes, a starting page, and a page limit. It first rejects overly large input, then clamps the page request to a safe range. It sends the document and a small JSON request to the render service over HTTP. If the service returns an error, it reads a short error message and raises a clear exception. If the service succeeds, it gathers the zip bundle while enforcing a maximum bundle size, then passes the finished bundle to the unpacking step. The output is a dictionary containing text, images, page metadata, and a quality reminder.

**Call relations**: This function is called when the system needs a document preview for a limited page range. It relies on httpx to make the HTTP request, json.dumps to prepare the render instructions, and asyncio.to_thread to run the heavier zip-reading work without blocking the async event loop. After the network part finishes, it hands the bundle to DocumentRenderer._unpack to turn the service response into the final structured result.

*Call graph*: 4 external calls (to_thread, AsyncClient, Timeout, dumps).


##### `DocumentRenderer._unpack`  (lines 109–196)

```
def _unpack(self, path: str, kind: str, start_page: int, limit: int, bundle: bytes) -> dict[str, object]
```

**Purpose**: This function opens and verifies the zip bundle returned by the preview service. It protects the rest of the system by refusing bundles with missing files, unexpected files, mismatched page information, oversized images, or non-PNG page data.

**Data flow**: It receives the original path and request details, plus the raw zip bundle bytes. It opens the bytes as a zip archive, reads manifest.json, and validates that the manifest matches the requested document kind and page range. It checks that the archive contains exactly the manifest and the expected page image files. For each page, it verifies the file name, size, PNG signature, page dimensions, and total image-size limits. It base64-encodes each PNG image and gathers any extracted page text. It returns a dictionary with the document path, type, combined text, total page count, returned pages, next page number if available, image data, and the quality reminder.

**Call relations**: DocumentRenderer.render calls this after it has successfully downloaded a render bundle from the preview service. This function uses zipfile.ZipFile and io.BytesIO to read the returned package, and base64.b64encode to convert binary PNG images into text-safe strings. It is the final safety checkpoint before rendered document content is handed back to the caller.

*Call graph*: 3 external calls (b64encode, BytesIO, ZipFile).


### `core/src/ufo/runtime/media/image_previews.py`

`domain_logic` · `request handling`

Image previews are small enough to seem harmless, but image files can still cause trouble. A file can lie about its type, be cut off halfway, contain too many animation frames, or expand into a huge number of pixels when decoded. This file acts like a careful border guard for raster images such as JPEG, PNG, GIF, and WebP.

First, it can guess the expected image media type from a filename suffix, such as “.png” becoming “image/png”. More importantly, it validates image bytes that arrive through an asynchronous stream, meaning chunks of data that may arrive over time. The caller must provide an ImagePreviewGrant, which is a signed-style claim saying what media type and exact byte size are expected. The validator checks that the stream delivers exactly that many bytes and never exceeds the global size limit.

After the bytes are collected, the heavier image checks are run in a worker thread so the main async flow is not blocked. The validator checks simple container endings first, then asks Pillow, the Python image library, to verify and decode the image. It rejects images with impossible dimensions, too many frames, too many total decoded pixels, invalid bytes, or a real format that does not match the claimed media type.

#### Function details

##### `raster_image_media_type`  (lines 43–44)

```
def raster_image_media_type(path: str) -> RasterImageMediaType | None
```

**Purpose**: This function looks at a file path and returns the image media type suggested by its filename ending. For example, it treats “photo.jpg” as a JPEG image, while an unknown suffix returns nothing.

**Data flow**: It receives a path string → extracts the final suffix using a POSIX-style path parser, lowercases it, and looks it up in the supported image suffix table → returns the matching media type, or null if the suffix is not one of the accepted raster image types.

**Call relations**: This is the lightweight first check in the image-preview flow. When another part of the system needs to decide whether a path looks like a supported raster image, it can call this before any expensive byte-level validation happens.

*Call graph*: 1 external calls (PurePosixPath).


##### `validated_image_preview`  (lines 47–61)

```
async def validated_image_preview(stream: AsyncIterator[bytes], grant: ImagePreviewGrant) -> bytes
```

**Purpose**: This function reads an incoming image preview stream and proves that it matches its promised size and type. It is the main public safety gate before the raw preview bytes are accepted.

**Data flow**: It receives an asynchronous stream of byte chunks and an ImagePreviewGrant containing the claimed media type and exact byte count → rejects the request if the claimed size is negative or above the maximum → reads chunks one by one, counting bytes and stopping if the stream grows beyond the claim or the hard limit → joins the chunks into one byte string → sends those bytes to the deeper image validator in a background thread → returns the same bytes if every check passes, or raises InvalidImagePreview if anything is wrong.

**Call relations**: This function sits between the outside data source and the rest of the application. It calls _ImagePreviewValidator.validate through asyncio.to_thread so CPU-heavy image checking does not freeze the async caller. If the bytes are bad, it raises InvalidImagePreview instead of handing unsafe data onward.

*Call graph*: 2 external calls (__init__, to_thread).


##### `_ImagePreviewValidator.validate`  (lines 66–109)

```
def validate(data: bytes, media_type: RasterImageMediaType) -> None
```

**Purpose**: This function performs the deep inspection of an image preview. It confirms that the bytes form a real image, that the decoded image stays within safe limits, and that the real image format matches the claimed media type.

**Data flow**: It receives the complete image bytes and the media type they are supposed to be → turns Pillow decompression-bomb warnings into errors, so suspiciously huge images are rejected → checks the file container for signs of being incomplete → opens the image from memory, verifies its format, then opens it again to decode each frame → counts frames, dimensions, and total decoded pixels → raises InvalidImagePreview on malformed data, oversized images, too many frames, or a type mismatch; otherwise it returns nothing, meaning validation succeeded.

**Call relations**: validated_image_preview calls this after it has gathered the full stream and checked the byte count. This function delegates the simple file-ending and container-shape checks to _ImagePreviewValidator._validate_container, then uses Pillow’s Image.open and verification tools for the deeper image-level checks.

*Call graph*: 5 external calls (__init__, open, BytesIO, catch_warnings, simplefilter).


##### `_ImagePreviewValidator._validate_container`  (lines 112–125)

```
def _validate_container(data: bytes, media_type: RasterImageMediaType) -> None
```

**Purpose**: This function performs quick format-specific completeness checks before the image library does deeper work. It catches obviously incomplete JPEG, GIF, PNG, and WebP files using their expected endings or headers.

**Data flow**: It receives raw image bytes and the claimed media type → checks the simple structural markers expected for that format, such as a JPEG ending marker or a PNG IEND chunk → raises InvalidImagePreview if the bytes look cut off or inconsistent; otherwise it returns nothing and lets deeper validation continue.

**Call relations**: _ImagePreviewValidator.validate calls this early as a cheap first pass. If this check fails, validation stops immediately; if it passes, validate continues by opening and decoding the image with Pillow.

*Call graph*: 1 external calls (__init__).


### `core/src/ufo/runtime/media/previews.py`

`data_model` · `cross-cutting`

This file is a tiny data model for saved picture previews. A preview image itself is stored somewhere else as raw bytes, often in a blob store, which is a place for keeping file-like chunks of data. Code that works with previews still needs two simple facts: the key that points to the stored blob, and the exact number of bytes saved. `StoredPreview` bundles those facts together.

Think of it like a coat-check ticket for an image: the ticket does not contain the coat, but it tells you which hook it is on and enough detail to verify what was stored. Here, `blob_key` is the workspace-relative address for finding the preview, and `size_bytes` is the stored image's exact size.

The class is marked as a dataclass, so Python automatically supplies the usual record-like behavior, such as construction and readable representation. It is also frozen, meaning once a `StoredPreview` is created, its fields cannot be changed. That matters because preview metadata should be a reliable fact after storage, not something that can be accidentally edited later.


### Conversation resource panels
Research sources and scheduled automations are collected and exposed as compact authorized conversation UI slots.

### `extensions/research/ufo_ext_research/observations.py`

`domain_logic` · `request handling and conversation sidebar reading`

This file is the research extension’s memory for retrieved web sources. When a search result or fetched web page is used, the file stores a cleaned, shortened version of its URL, title, snippet, and optional date in a database table tied to the workspace and conversation. Think of it like a notebook clipped to each conversation: every source the research tool consults gets written down there so the user can inspect it later.

The database key is based on the URL, using a SHA-256 digest, which is a fixed-length fingerprint of the URL. This lets the code update an existing source if the same URL appears again instead of creating duplicates. It also records which conversation turn found the source and its rank in the result list.

The file keeps the list bounded. It stores at most the most recent 100 sources per conversation and deletes older extras, so the database cannot grow endlessly from repeated research.

At the end, it defines a conversation slot provider named `SOURCES_SLOT`. A conversation slot is a structured piece of information the application can show alongside a conversation. Here, the slot can quickly summarize how many sources exist and can read back the source list as validated `ConversationSource` objects.

#### Function details

##### `_bounded`  (lines 53–54)

```
def _bounded(value: str, limit: int) -> str
```

**Purpose**: This small helper cuts a text value down to a maximum length. It is used before saving source details so very long titles, snippets, or dates do not overflow the expected size.

**Data flow**: It receives a string and a character limit. It returns the same string if it is short enough, or only the first part of it if it is too long. It does not change anything outside itself.

**Call relations**: When `record_sources` prepares source information for storage, it calls `_bounded` to trim fields before validation and database writing.

*Call graph*: called by 1 (record_sources).


##### `record_sources`  (lines 57–130)

```
async def record_sources(ext: ExtensionContext, conversation_id: UUID, turn_id: UUID, sources: tuple[RetrievedSource, ...]) -> None
```

**Purpose**: This is the main writer for remembered research sources. It validates and saves a batch of retrieved sources for a specific conversation turn, updating old entries for the same URL and pruning the list so only the newest allowed sources remain.

**Data flow**: It receives the extension context, a conversation ID, a turn ID, and a tuple of `RetrievedSource` items. If the tuple is empty, it stops immediately. Otherwise it opens a database transaction, shortens each source’s text fields, validates them as `ConversationSource` records, creates a URL fingerprint, and inserts or updates each row in the source observation table. After writing, it finds the most recent source entries for that conversation and deletes anything beyond the configured source limit.

**Call relations**: `record_search_hits` and `record_fetched_page` both convert their own input shapes into `RetrievedSource` objects and hand them to `record_sources`. Inside this function, `_bounded` prepares safe text lengths, the extension context provides the database transaction, and SQLAlchemy builds the insert, select, and delete database commands.

*Call graph*: calls 2 internal fn (transaction, _bounded); called by 2 (record_fetched_page, record_search_hits); 5 external calls (__init__, now, sha256, delete, select).


##### `record_search_hits`  (lines 133–152)

```
async def record_search_hits(ext: ExtensionContext, conversation_id: UUID, turn_id: UUID, hits: tuple[SearchHit, ...]) -> None
```

**Purpose**: This function records normal search results as remembered conversation sources. It is a convenience wrapper that turns search hits into the common `RetrievedSource` format.

**Data flow**: It receives the extension context, conversation ID, turn ID, and a tuple of `SearchHit` results. For each hit, it copies the URL, title, result text, and published date into a `RetrievedSource`. It then passes the whole converted tuple to `record_sources`, which does the actual validation and database saving.

**Call relations**: Code that has just received search results can call `record_search_hits` without knowing the storage details. This function then hands off to `record_sources`, which is the shared path for persisting sources.

*Call graph*: calls 1 internal fn (record_sources); 1 external calls (__init__).


##### `record_fetched_page`  (lines 155–173)

```
async def record_fetched_page(ext: ExtensionContext, conversation_id: UUID, turn_id: UUID, page: FetchedPage) -> None
```

**Purpose**: This function records a single fetched web page as a remembered conversation source. It is used when the system has opened or read a page directly, not just seen it in search results.

**Data flow**: It receives the extension context, conversation ID, turn ID, and a `FetchedPage`. It builds one `RetrievedSource` using the page URL as both URL and title, and using the page summary if available, otherwise the page text. It leaves the published date empty, then passes that one-item tuple to `record_sources` for storage.

**Call relations**: When a page-fetching step wants the page to appear in the conversation’s source list, it calls `record_fetched_page`. This function adapts the fetched-page shape into the shared source-recording flow handled by `record_sources`.

*Call graph*: calls 1 internal fn (record_sources); 1 external calls (__init__).


##### `_source_count`  (lines 176–186)

```
async def _source_count(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: This function gives a quick count of how many sources are stored for a conversation, capped at the display limit. It is used to summarize the Sources slot without loading every source detail.

**Data flow**: It receives a conversation slot context, which includes the extension context and conversation ID. It opens a database transaction, counts matching rows for the current workspace and conversation, and returns `None` if there are no sources. If sources exist, it returns the count as a number, but never higher than the source limit.

**Call relations**: The `SOURCES_SLOT` provider uses `_source_count` as its summary function. When the application needs a lightweight summary for the Sources panel, this function queries the database and reports the count.

*Call graph*: 1 external calls (select).


##### `_read_sources`  (lines 189–217)

```
async def _read_sources(ctx: ConversationSlotContext) -> SourcesSlotPayload
```

**Purpose**: This function reads the stored sources for a conversation and packages them for display in the Sources slot. It returns the newest sources first and notes whether the stored list was longer than what can be shown.

**Data flow**: It receives a conversation slot context. It opens a database transaction, selects source rows for the current workspace and conversation, orders them by most recently updated and then by rank, and fetches one more than the display limit so it can detect truncation. It turns the rows into `ConversationSource` objects and wraps them in a `SourcesSlotPayload`, with a flag saying whether extra sources were left out.

**Call relations**: The `SOURCES_SLOT` provider uses `_read_sources` when the application needs the full content of the Sources panel. This function reads from the same table written by `record_sources` and returns data in the structured payload format expected by the conversation UI.

*Call graph*: 3 external calls (__init__, __init__, select).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/conversation_slot.py`

`domain_logic` · `request handling`

This file is the bridge between stored scheduled tasks and the “Automations” panel or slot shown for a conversation. A scheduled task may contain a description, a schedule, run history, and the latest response. Not all of that should always be shown, and too much text could make the conversation context too large. This file decides what is visible and packages it into the standard shape expected by the UFO conversation slot system.

The flow is like a receptionist preparing a short daily agenda. First it opens the schedule store using the extension context. Then it looks at the conversation’s visible items, which act like permission slips: a task is only included if its stored task id matches the visible item’s generation. That check helps avoid showing stale or unauthorized automation data.

For each allowed task, the file asks for inspection details such as next run time, last run time, latest status, and latest response. It then builds a `ConversationAutomation` object, trimming long descriptions, schedules, statuses, and responses to fixed limits. If anything is skipped or shortened, it marks the result as `truncated`, so the caller knows this is not the full raw data. Finally, `AUTOMATIONS_SLOT` registers these read and summary functions under the “Automations” slot.

#### Function details

##### `_scheduler`  (lines 16–19)

```
def _scheduler(ctx: ConversationSlotContext) -> ScheduleStore
```

**Purpose**: This function creates the schedule-store object used to read scheduled tasks. It also checks that the conversation slot has the extension context it needs; without that context, the scheduled-task storage cannot be reached.

**Data flow**: It receives a conversation slot context. If the context has no extension data, it stops with an error. Otherwise it takes the extension context and uses it to create a `ScheduleStore`, which is returned to the caller.

**Call relations**: Both `_conversation` and `_read` call this when they need access to stored schedules. It hands them the storage doorway they need before they can list tasks or inspect their run details.

*Call graph*: called by 2 (_conversation, _read); 1 external calls (__init__).


##### `_conversation`  (lines 22–28)

```
async def _conversation(ctx: ConversationSlotContext) -> tuple[ScheduledTask, ...]
```

**Purpose**: This function fetches scheduled tasks that belong to the current conversation and match the names of items currently visible to the conversation. It asks for one more than the display limit so the code can tell whether there were too many to show.

**Data flow**: It receives the conversation slot context, pulls the visible item names out of it, opens the schedule store through `_scheduler`, and asks the store for matching tasks for this conversation. It returns the tasks as a tuple.

**Call relations**: `_read` calls `_conversation` as its first broad lookup step. `_conversation` delegates storage access to `_scheduler`, then gives `_read` the raw candidate tasks that still need authorization checks and formatting.

*Call graph*: calls 1 internal fn (_scheduler); called by 1 (_read).


##### `_read`  (lines 31–90)

```
async def _read(ctx: ConversationSlotContext) -> AutomationsSlotPayload
```

**Purpose**: This is the main reader for the Automations slot. It turns stored scheduled tasks into safe, short `ConversationAutomation` entries that can be shown in the conversation context.

**Data flow**: It receives the conversation slot context. It opens the schedule store, fetches candidate tasks with `_conversation`, and compares each task against the visible items in the context. Only tasks whose name and generation match are treated as authorized. It then inspects those tasks for run status, trims long fields, hides descriptions and latest responses when content is not visible, records whether anything was omitted or shortened, and returns an `AutomationsSlotPayload` containing the final automation list.

**Call relations**: The `AUTOMATIONS_SLOT` provider uses `_read` when the full Automations slot content is requested. `_read` calls `_scheduler` for storage access, `_conversation` to get candidate tasks, and then builds `ConversationAutomation` objects before wrapping them in an `AutomationsSlotPayload`.

*Call graph*: calls 2 internal fn (_conversation, _scheduler); 2 external calls (__init__, __init__).


##### `_summarize`  (lines 93–95)

```
async def _summarize(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: This function gives a quick count for the Automations slot without loading full task details. It returns how many visible automation items can be summarized, capped at the maximum the slot is allowed to show.

**Data flow**: It receives the conversation slot context and counts the visible items. It caps that number at the configured maximum. If the count is zero, it returns `None`; otherwise it returns the count.

**Call relations**: The `AUTOMATIONS_SLOT` provider uses `_summarize` when only a lightweight summary is needed. Unlike `_read`, it does not call the schedule store or inspect tasks; it only uses the visible items already present in the context.
