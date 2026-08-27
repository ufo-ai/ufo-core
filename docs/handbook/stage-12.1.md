# Browser Automation and Hosted Site Building  `stage-12.1`

This stage is the web-working part of the system. It is used during the main work loop when an agent must open Chrome, inspect a page, press buttons, fill forms, download files, or build and publish a small hosted site.

The Chrome session layer is the engine. It starts Chrome, talks to it through the Chrome DevTools Protocol, which is Chrome’s remote-control channel, manages tabs, waits for pages to settle, and watches downloads. The page modeling layer turns the visible page into text and stable references, so the agent can understand and act on buttons, links, and fields. The action layer is the control panel: tools accept requests like click, type, upload, or screenshot, check and fix them, then send them to Chrome. Provider code decides where the browser comes from, such as a sandbox, a remote browser, or a delegated browser worker.

The site-building files add publishing. They move source files into a sandbox, guide a safe build-test-repair-deploy workflow, audit the finished app in the browser, and register hosted sites with ownership, visibility, port, and homepage rules.

## Sub-stages

- [Chrome Session and CDP Infrastructure](stage-12.1.1.md) `stage-12.1.1` — 8 files
- [Browser Page Modeling and Content Inspection](stage-12.1.2.md) `stage-12.1.2` — 4 files
- [Browser Action Execution and Tool Surface](stage-12.1.3.md) `stage-12.1.3` — 8 files
- [Browser Providers and Delegated Browser Agents](stage-12.1.4.md) `stage-12.1.4` — 5 files

## Files in this stage

### Site Tool Entrypoints
Agent-facing workflows for building, previewing, publishing, and binding hosted sites and application pages.

### `extensions/sites/ufo_ext_sites/tools.py`

`orchestration` · `request handling`

This file is the website workshop for the system. It lets an agent run a build, start a server, publish a static site or app, take a preview image, and make one hosted site the agent’s homepage. Without it, an agent might start a server that is not ready yet, overwrite another process on the same port, leave a dead hosted link, expose a site to the wrong audience, or lose the source files needed to edit a deployed static site later.

The main idea is: work happens inside a sandbox, then a proven live port is registered as a public-facing site. Like checking that a shop is open before putting its address on a flyer, the code starts the server and polls the port before returning a URL. It also clears old server tasks and log files safely, so stale processes and risky file links do not interfere.

For static deployments, it lists the served directory, records file sizes and hashes, uploads those files to the blob store, and saves a manifest as the site’s source of record. For dynamic published apps, it registers the running sandbox server instead. After hosting, it asks a preview service to capture the page for cards and artifacts. It also has special rules for the UFO application builder: product QA must pass before deploy, and the source cannot change after QA. Homepage binding is treated as a visibility-changing act, so only the creator, usually while a member is actively speaking, can do it.

#### Function details

##### `StartServerInput.validate_port`  (lines 408–413)

```
def validate_port(self) -> 'StartServerInput'
```

**Purpose**: Checks that a server start request is sensible before the tool runs. It rejects an empty command and rejects port numbers outside the valid internet port range.

**Data flow**: It reads the parsed input fields: command and port. If the command is blank or the port is invalid, it raises a validation error; otherwise it returns the same input object unchanged.

**Call relations**: This runs automatically when a StartServerInput is created for the start_server tool. It protects start_server from receiving values that would fail later in a less clear way.


##### `_json_result`  (lines 450–451)

```
def _json_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: Turns a Python dictionary into the standard tool response format. Tool callers get one JSON text block instead of raw Python data.

**Data flow**: A dictionary goes in. The function serializes it to JSON text, wraps that text as tool content, and returns a ToolResult.

**Call relations**: Most public tools call this at the end, after they have built, served, hosted, audited, or bound something. It is the common exit door for successful tool replies.

*Call graph*: called by 7 (_redeploy_homepage, deploy_website, publish_website, qa_ufo_application, set_homepage, start_server, website); 3 external calls (__init__, __init__, dumps).


##### `_free_log`  (lines 454–474)

```
async def _free_log(ctx: ToolContext, log_path: str) -> None
```

**Purpose**: Safely clears the chosen log-file name before a background server writes to it. This avoids following a planted symbolic link, which is a file shortcut that could otherwise redirect logs into an unintended file.

**Data flow**: It receives the tool context and a log path. It decides which safe root the path belongs under, asks the sandbox to remove that exact file through a containment guard, and raises an error if the guard refuses.

**Call relations**: _serve calls this before starting any server. It prepares the log path so the later shell redirect creates a fresh file instead of overwriting something unsafe.

*Call graph*: called by 1 (_serve); 1 external calls (PurePosixPath).


##### `_stop_server`  (lines 477–482)

```
async def _stop_server(ctx: ToolContext, port: int) -> None
```

**Purpose**: Frees a port by stopping any process that is already listening on it. This keeps a new preview or deployment from accidentally talking to an old server.

**Data flow**: It receives a port number. It runs a sandbox Python program that finds listeners on that port and terminates them; if that program fails, it raises an error.

**Call relations**: _serve uses this before launching a server. _audit_builder_application also uses it after an audit so the temporary audit server does not stay running.

*Call graph*: called by 2 (_audit_builder_application, _serve).


##### `_stop_server_task`  (lines 485–501)

```
async def _stop_server_task(ctx: ToolContext, command: str, base: str, pid: str) -> None
```

**Purpose**: Stops a background server task that this tool just started, especially when readiness checks fail or are interrupted. It makes cleanup deliberate instead of leaving an orphaned process.

**Data flow**: It receives the context, the server command, the task journal base path, and the task process id. It asks the sandbox to signal that task and then waits for the detached task record to finish; errors become RuntimeErrors.

**Call relations**: _serve calls this only on failure paths after a background task was launched. It is the emergency brake for starts that do not become usable servers.

*Call graph*: called by 1 (_serve).


##### `_reset_server_task`  (lines 504–522)

```
async def _reset_server_task(ctx: ToolContext, base: str) -> None
```

**Purpose**: Clears the saved task record for a server slot before starting a new one. This prevents the sandbox task runner from reconnecting to an old task instead of launching the new server.

**Data flow**: It receives a task base path. Inside the sandbox, it stops any still-running task recorded there and removes the related pid, log, exit, and lock files.

**Call relations**: _serve calls this after freeing the log and port, just before starting a detached server. It gives every serve attempt a clean task journal.

*Call graph*: called by 1 (_serve).


##### `_serve`  (lines 525–592)

```
async def _serve(ctx: ToolContext, command: str, project: str, port: int, log_path: str) -> dict[str, object]
```

**Purpose**: Starts a server in the sandbox and waits until it is actually reachable. It returns only after the port answers, so callers do not race ahead with a dead or still-starting server.

**Data flow**: It receives a command, project directory, port, and log path. It clears the log, frees the port, resets the task journal, launches the command in the background with PORT set, probes localhost until the port accepts connections, and returns the sandbox URL, port, and log path. On failure, it stops the new task and reports useful log output.

**Call relations**: This is the central serving helper. start_server uses it for scratch previews, deploy_website and publish_website use it before hosting, _redeploy_homepage uses it for homepage updates, and _audit_builder_application uses it for the temporary audit server.

*Call graph*: calls 4 internal fn (_free_log, _reset_server_task, _stop_server, _stop_server_task); called by 5 (_audit_builder_application, _redeploy_homepage, deploy_website, publish_website, start_server); 3 external calls (sha256, quote, shell_path).


##### `_site_media_type`  (lines 595–597)

```
def _site_media_type(path: str) -> str
```

**Purpose**: Chooses the web content type for a file based on its extension. This tells browsers whether a file is HTML, JavaScript, CSS, an image, and so on.

**Data flow**: It receives a file path string. It looks at the part after the final dot, finds a known media type or uses a safe generic default, and adds a text character-set marker for text files.

**Call relations**: _promote_source calls this while building the source manifest for a static site. The manifest later tells the hosting layer how to serve each stored file.

*Call graph*: called by 1 (_promote_source).


##### `_source_listing`  (lines 600–621)

```
async def _source_listing(ctx: ToolContext, project: str) -> dict[str, dict[str, object]]
```

**Purpose**: Creates a safe inventory of the files in a static site directory. It records file sizes and SHA-256 hashes, which are digital fingerprints used to verify exact bytes.

**Data flow**: It receives a sandbox project path. A sandbox script walks the directory, skips folders like .git and node_modules, refuses oversized sites, and returns JSON describing each regular file; the function parses that JSON into a dictionary.

**Call relations**: _served_directory calls this to understand what can be hosted. If the directory is too large or empty, deployment stops before anything is registered.

*Call graph*: called by 1 (_served_directory); 1 external calls (loads).


##### `_promote_source`  (lines 624–665)

```
async def _promote_source(ctx: ToolContext, project: str, conversation_id: UUID, name: str, listing: dict[str, dict[str, object]]) -> str
```

**Purpose**: Copies a static site’s served files into the blob store and creates the manifest that records them. This makes the deployed site recoverable later without dialing back into the sandbox.

**Data flow**: It receives the project path, conversation id, site name, and file listing. It creates a new storage prefix, builds a manifest with size, media type, and hash for each file, uploads the bytes either through presigned upload URLs or direct streams, and returns the manifest as JSON.

**Call relations**: deploy_website and _redeploy_homepage call this before registering or updating a static site. It relies on _site_media_type to label files and hands the manifest to _host or the homepage redeploy write.

*Call graph*: calls 1 internal fn (_site_media_type); called by 2 (_redeploy_homepage, deploy_website); 4 external calls (__init__, __init__, transfer, uuid4).


##### `_illustrate`  (lines 668–690)

```
async def _illustrate(ctx: ToolContext, name: str, port: int, conversation_id: UUID) -> None
```

**Purpose**: Creates visual previews for a hosted site after it has been registered. These images are used for artifact cards and share cards.

**Data flow**: It receives the context, site name, serving port, and the conversation id of the site row. It asks the preview service to render the site, saves the preview if one is returned, and then asks the share-card code to draw a card from the page.

**Call relations**: deploy_website, publish_website, and _redeploy_homepage call this after hosting is already written. It runs after registration so the preview captures the current official site, not the one it displaced.

*Call graph*: calls 1 internal fn (render_site_preview); called by 3 (_redeploy_homepage, deploy_website, publish_website); 2 external calls (__init__, draw_from_page).


##### `_refuse_before_serving`  (lines 693–733)

```
async def _refuse_before_serving(ctx: ToolContext, raw_name: str, port: int, visibility: Visibility | None) -> tuple[str, HostedSite | None]
```

**Purpose**: Checks whether a site is allowed to be hosted before the tool kills anything on the target port. This protects existing hosted sites from being knocked offline by a deploy that would later be refused.

**Data flow**: It receives the requested site name, port, and optional visibility. It confirms there is an acting member, checks that visibility changes have a live speaker, normalizes the site name, validates the future URL shape, and asks the site registry whether registration would be refused or what site would be displaced.

**Call relations**: deploy_website and publish_website call this before starting their server. _host repeats the key checks when it writes, but this early pass keeps failed requests from causing damage.

*Call graph*: called by 2 (deploy_website, publish_website); 3 external calls (__init__, site_name, site_url).


##### `_host`  (lines 736–780)

```
async def _host(ctx: ToolContext, raw_name: str, port: int, visibility: Visibility | None, manifest: str | None) -> dict[str, object]
```

**Purpose**: Registers a live sandbox port as a hosted site and returns the stable link details. This is the step that turns a local server into a deliverable URL.

**Data flow**: It receives a raw name, port, optional visibility, and optional source manifest. It confirms ownership and speaker rules, normalizes the name, builds the public URL, writes the site row through HostedSites.register, computes the effective visibility, and returns site metadata.

**Call relations**: deploy_website and publish_website call this after _serve proves the port is reachable. _illustrate then uses the registered site to capture previews.

*Call graph*: called by 2 (deploy_website, publish_website); 5 external calls (__init__, effective_visibility, site_object_name, site_name, site_url).


##### `website`  (lines 783–792)

```
async def website(ctx: ToolContext, args: WebsiteInput) -> ToolResult
```

**Purpose**: Runs a website build command inside the sandbox and reports what files are now in the project directory. It is a simple build tool, not a hosting tool.

**Data flow**: It receives a build command and optional project path. It scopes the path to the workspace, runs the command there, raises an error if the build fails, lists the directory, and returns the project path plus file names as JSON.

**Call relations**: This is one of the exported website tools. It calls _json_result for its response but does not call the serving or hosting helpers.

*Call graph*: calls 1 internal fn (_json_result); 2 external calls (quote, workspace_path).


##### `start_server`  (lines 795–815)

```
async def start_server(ctx: ToolContext, args: StartServerInput) -> ToolResult
```

**Purpose**: Starts a temporary server in the sandbox and returns its local URL. It is meant for previews and validation, not for permanent hosted links.

**Data flow**: It receives a project path, optional command, optional port, and optional log file. It validates special UFO application-builder restrictions, chooses defaults, scopes paths, starts the server through _serve, adjusts the preview URL for the application builder when needed, and returns the details as JSON.

**Call relations**: This exported tool is the direct user-facing wrapper around _serve. Unlike deploy_website and publish_website, it does not call _host, so no permanent site is registered.

*Call graph*: calls 2 internal fn (_json_result, _serve); 1 external calls (workspace_path).


##### `_application_audit_attempts`  (lines 818–826)

```
async def _application_audit_attempts(ctx: ToolContext) -> int
```

**Purpose**: Reads how many product-audit attempts have already been used in the current turn. This enforces a small retry budget for the UFO application builder.

**Data flow**: It receives the tool context. It reads a store key based on the turn id, returns zero if nothing is stored, returns the stored integer if valid, and errors if the stored value has the wrong type.

**Call relations**: _audit_builder_application calls this before running an audit. The count controls whether another audit may proceed.

*Call graph*: called by 1 (_audit_builder_application); 1 external calls (format).


##### `_application_audit_feedback`  (lines 829–841)

```
async def _application_audit_feedback(ctx: ToolContext, issues: tuple[ApplicationAuditIssue, ...], attempts: int) -> ApplicationAuditFeedback
```

**Purpose**: Records a failed audit attempt and packages the issues into feedback for the builder. It tells the builder what to fix and how many tries remain.

**Data flow**: It receives audit issues and the previous attempt count. It increments and stores the count, builds an ApplicationAuditFeedback object with remaining attempts and issues, and returns it.

**Call relations**: _audit_builder_application calls this whenever the browser audit cannot run, cannot produce a valid report, or finds product problems. qa_ufo_application returns this feedback to the caller as JSON.

*Call graph*: called by 1 (_audit_builder_application); 2 external calls (__init__, format).


##### `_audit_builder_application`  (lines 844–942)

```
async def _audit_builder_application(ctx: ToolContext, project: str) -> ApplicationAuditReport | ApplicationAuditFeedback
```

**Purpose**: Runs the deterministic browser-based product audit for a UFO application. It checks whether the app behaves and displays correctly before deployment is allowed.

**Data flow**: It receives the context and project path. It checks attempt limits, writes audit scripts into runtime storage, starts a temporary preview server with _serve, runs the Node audit script, reads and validates the report, compares it with the stored contract, and returns either a full report or repair feedback. It always stops the audit port afterward.

**Call relations**: qa_ufo_application calls this as its main work. It uses _application_audit_attempts and _application_audit_feedback to enforce retries, and _serve plus _stop_server to control the temporary audit server.

*Call graph*: calls 4 internal fn (_application_audit_attempts, _application_audit_feedback, _serve, _stop_server); called by 1 (qa_ufo_application); 8 external calls (__init__, model_validate, model_validate_json, quote, shell_path, format, audit_application, application_design_acceptance_relative).


##### `_application_source_sha256`  (lines 945–951)

```
async def _application_source_sha256(ctx: ToolContext) -> str
```

**Purpose**: Computes a fingerprint of the UFO application source file. This proves whether the source changed after QA passed.

**Data flow**: It asks the sandbox to read the application source file safely. If reading succeeds, it hashes the source text with SHA-256 and returns the hex digest; otherwise it raises an error.

**Call relations**: qa_ufo_application stores this hash after a successful audit. _require_current_application_qa later compares the current hash against the stored proof before deployment.

*Call graph*: called by 2 (_require_current_application_qa, qa_ufo_application); 1 external calls (sha256).


##### `_require_current_application_qa`  (lines 954–966)

```
async def _require_current_application_qa(ctx: ToolContext) -> ApplicationQaProof
```

**Purpose**: Enforces that the UFO application builder cannot deploy without a current passing QA proof. It also blocks deployment if the source changed after QA.

**Data flow**: It reads the stored QA proof for the current turn, validates its shape, recomputes the source hash, compares the two, and returns the proof if everything matches. Missing, invalid, or stale proof becomes an error.

**Call relations**: deploy_website calls this when the special application-builder profile is deploying. It connects the qa_ufo_application step to the later deploy step.

*Call graph*: calls 1 internal fn (_application_source_sha256); called by 1 (deploy_website); 2 external calls (model_validate, format).


##### `qa_ufo_application`  (lines 969–1011)

```
async def qa_ufo_application(ctx: ToolContext, args: QaUfoApplicationInput) -> ToolResult
```

**Purpose**: Runs product QA for the special UFO application builder and records proof when it passes. This is the gate before that builder may deploy.

**Data flow**: It checks that the caller is the application-builder profile, reads and increments the QA call count, runs _audit_builder_application, and either returns repair feedback or stores a proof containing the current source hash and audit batch count. On success, it returns a summary of checked views and controls.

**Call relations**: This is an exported profile-only tool. It depends on _audit_builder_application for the browser checks, _application_source_sha256 for the proof, and _json_result for the response.

*Call graph*: calls 3 internal fn (_application_source_sha256, _audit_builder_application, _json_result); 4 external calls (__init__, __init__, format, format).


##### `deploy_ufo_application`  (lines 1014–1024)

```
async def deploy_ufo_application(ctx: ToolContext, args: DeployUfoApplicationInput) -> ToolResult
```

**Purpose**: Deploys the special UFO application builder’s fixed scaffold under a chosen site name. It is a narrow wrapper that prevents the builder from choosing arbitrary deploy inputs.

**Data flow**: It checks that the current profile is the UFO application builder. Then it creates a DeployWebsiteInput for the fixed scaffold and index.html, calls deploy_website, and returns that result.

**Call relations**: This exported profile-only deploy tool delegates the actual hosting flow to deploy_website. The QA requirement is enforced inside deploy_website for this profile.

*Call graph*: calls 1 internal fn (deploy_website); 1 external calls (__init__).


##### `deploy_website`  (lines 1027–1058)

```
async def deploy_website(ctx: ToolContext, args: DeployWebsiteInput) -> ToolResult
```

**Purpose**: Hosts a built static website at a stable link and stores its source files for future retrieval. Re-deploying the same site name updates the link instead of creating a new one.

**Data flow**: It receives a static output path, site name, entry point, and optional visibility. It scopes the path, applies application-builder restrictions and QA checks, chooses the conversation’s serve port, handles homepage redeploys when appropriate, checks hosting permissions, prepares or builds the served directory, uploads source files, starts a static server, registers the site, captures previews, and returns serving plus hosted-link details.

**Call relations**: This is the main exported static-site deployment tool. It ties together _refuse_before_serving, _served_directory, _promote_source, _serve, _host, _illustrate, and sometimes _redeploy_homepage.

*Call graph*: calls 11 internal fn (_agent_homepage, _host, _illustrate, _json_result, _promote_source, _redeploy_homepage, _refuse_before_serving, _require_current_application_qa, _serve, _served_directory (+1 more)); called by 1 (deploy_ufo_application); 4 external calls (serve_port, workspace_path, site_object_name, site_name).


##### `_served_directory`  (lines 1061–1087)

```
async def _served_directory(ctx: ToolContext, project: str) -> tuple[str, dict[str, dict[str, object]]]
```

**Purpose**: Decides what directory should actually be served for a static deployment. If the input is source for an editable app page, it builds it first and serves the generated dist folder.

**Data flow**: It receives a project path. It lists the files; if the special source file is absent, it returns the original project and listing. If the source file is present, it writes needed config, unpacks the page kit, runs the build, then lists and returns the dist directory.

**Call relations**: deploy_website and _redeploy_homepage call this before promoting source. It prevents raw source directories from being served as if browsers could run them directly.

*Call graph*: calls 1 internal fn (_source_listing); called by 2 (_redeploy_homepage, deploy_website); 2 external calls (quote, unpack_page_kit).


##### `_agent_homepage`  (lines 1090–1093)

```
async def _agent_homepage(ctx: ToolContext) -> HostedSite | None
```

**Purpose**: Finds the hosted site currently bound as the acting agent’s homepage, if any. This lets deployment know whether a request is really updating an existing homepage.

**Data flow**: It receives the context, gets the site registry, asks for the homepage attached to the current agent id, and returns either a HostedSite or None.

**Call relations**: deploy_website calls this near the start. If the requested name matches a homepage from another conversation, deploy_website may route into _redeploy_homepage instead of creating a separate site.

*Call graph*: calls 1 internal fn (_sites_registry); called by 1 (deploy_website).


##### `_sites_registry`  (lines 1096–1099)

```
def _sites_registry(ctx: ToolContext) -> HostedSites
```

**Purpose**: Creates the HostedSites registry object for the current workspace transaction. It is a small helper so site database access is constructed consistently.

**Data flow**: It reads the extension context for the workspace id and transaction. It returns a HostedSites object connected to that store context, or raises if extension context is missing.

**Call relations**: _agent_homepage, deploy_website, and _redeploy_homepage use this when they need to read or write hosted-site records.

*Call graph*: called by 3 (_agent_homepage, _redeploy_homepage, deploy_website); 1 external calls (__init__).


##### `_redeploy_homepage`  (lines 1102–1171)

```
async def _redeploy_homepage(ctx: ToolContext, args: DeployWebsiteInput, bound: HostedSite, scratch_port: int) -> ToolResult
```

**Purpose**: Updates an agent’s already-bound homepage in place from a new build. The public homepage link stays the same while its stored source and preview are refreshed.

**Data flow**: It checks that the redeploy was requested by a live speaker or an approved builder flow, rejects explicit visibility changes, checks ownership, prepares and promotes the new static source, serves it on a scratch port, updates the existing homepage row’s manifest, unregisters any site displaced by the scratch port, captures previews, and returns the unchanged hosted link details.

**Call relations**: deploy_website calls this when a deploy name matches the acting agent’s bound homepage from another conversation. It uses _served_directory, _promote_source, _serve, _sites_registry, _illustrate, and _json_result.

*Call graph*: calls 7 internal fn (agent_visibility, _illustrate, _json_result, _promote_source, _serve, _served_directory, _sites_registry); called by 1 (deploy_website); 4 external calls (workspace_path, format, site_object_name, site_url).


##### `publish_website`  (lines 1174–1191)

```
async def publish_website(ctx: ToolContext, args: PublishWebsiteInput) -> ToolResult
```

**Purpose**: Publishes a web app at a stable link, optionally running install and backend commands first. Unlike static deploy, it does not store source files as the site’s record.

**Data flow**: It receives project and dist paths, app name, visibility, and optional install or run commands. It chooses the conversation’s port, checks hosting permission, optionally runs installation, decides whether to serve the project with a custom command or serve the dist folder statically, starts the server, registers the live port without a manifest, captures previews, and returns the URLs.

**Call relations**: This exported tool shares the hosting path with deploy_website through _refuse_before_serving, _serve, _host, _illustrate, and _json_result, but skips _promote_source because the running app remains the source of responses.

*Call graph*: calls 5 internal fn (_host, _illustrate, _json_result, _refuse_before_serving, _serve); 3 external calls (quote, serve_port, workspace_path).


##### `set_homepage`  (lines 1194–1237)

```
async def set_homepage(ctx: ToolContext, args: SetHomepageInput) -> ToolResult
```

**Purpose**: Makes one hosted site the acting agent’s homepage. This changes which audience rules apply to that site, so it is restricted to the site creator and usually requires a live speaker.

**Data flow**: It receives the site object name. It loads all hosted sites, finds the named one, checks that the acting member created it, allows speakerless binding only for a site deployed in this same turn, writes the homepage pointer, and returns the homepage URL, agent visibility, and agent id.

**Call relations**: This exported tool uses HostedSites directly rather than _host because it is not starting or registering a server. It finishes through _json_result after writing the homepage binding.

*Call graph*: calls 2 internal fn (agent_visibility, _json_result); 3 external calls (__init__, site_object_name, site_url).


### `extensions/sites/ufo_ext_sites/application_builder.py`

`orchestration` · `application preview and build request handling`

This file is the rulebook and toolbelt for the application builder. A parent conversation can ask for an app, and this file creates a fixed workspace with an `index.html`, an `app.tsx`, and a preview page. Then it delegates the work to a special subagent profile with only the tools it needs.

The workflow is deliberately strict. First, the worker must write an SVG design contract, which is a drawing of the first screen. The file checks that the SVG is safe, visible, and divided into named regions. Only after that can the worker write `app.tsx`. The source must import only from `ufo/kit`, mount into the expected page root, avoid exports, and compile successfully. If the first source write is wrong, the worker cannot simply overwrite it; it must read small excerpts and apply exact text replacements. This is like requiring tracked edits on a draft instead of allowing a new mystery document.

Before deployment, separate product QA must pass and leave proof. After the worker returns, this file verifies that the deployed site really matches the accepted source and belongs to the right member. It also includes a lightweight image preview renderer, so users can approve a direction before the full app is built.

#### Function details

##### `ApplicationBuilderTask.source_is_the_scaffolds_app_tsx`  (lines 324–336)

```
def source_is_the_scaffolds_app_tsx(self) -> 'ApplicationBuilderTask'
```

**Purpose**: This validation step makes sure the build task points to exactly one allowed source file: `app.tsx` directly inside the scaffold folder under `/workspace`. It prevents a worker from being sent to edit some other file or escape the intended project folder.

**Data flow**: It reads the task's scaffold path and source path, converts both into safe workspace-relative paths, and compares them. If the source is exactly the scaffold's `app.tsx`, the task is accepted unchanged; otherwise validation fails with a clear error.

**Call relations**: This runs when an `ApplicationBuilderTask` is created or validated, including before spawning the builder worker and during source/design tool calls. It relies on the sandbox path checker so later file-writing functions can trust the task's paths.

*Call graph*: 2 external calls (PurePosixPath, contained_relative).


##### `ApplicationBuilderResult.result_matches_status`  (lines 373–382)

```
def result_matches_status(self) -> 'ApplicationBuilderResult'
```

**Purpose**: This validation step checks that a worker's final answer is internally consistent. A deployed app must include a site name and URL, while a blocked app must explain what stopped it.

**Data flow**: It reads the result status and related fields. If the status is `deployed`, it requires deployment identity and forbids a blocker message; if the status is `blocked`, it requires a blocker and forbids site identity. It returns the same result if everything fits.

**Call relations**: This runs when the worker result is parsed or created. It protects `build_ufo_application` and `ApplicationBuildAcceptance.accept` from acting on contradictory deployment evidence.


##### `ApplicationBuildAcceptance.accept`  (lines 392–492)

```
async def accept(self, result: ApplicationBuilderResult) -> ApplicationBuilderResult
```

**Purpose**: This is the final gatekeeper for a worker's deployed app. It accepts the result only if QA proof, deployed source, ownership, and accepted source all line up.

**Data flow**: It takes the worker's `ApplicationBuilderResult`, reads stored QA proof, checks the deployed site record, compares source hashes, and reads the accepted source hash from the sandbox. If every check passes, it updates the result with the bound homepage URL; if any check fails, it returns a blocked result explaining why.

**Call relations**: After `build_ufo_application` receives the worker's output, it hands that output here. This function calls helper path functions and uses `_blocked` whenever a safety or ownership check fails, and it consults hosted-site storage before binding the accepted site as the homepage.

*Call graph*: calls 3 internal fn (_blocked, _runtime_root, _source_acceptance_path); 6 external calls (__init__, __init__, model_validate, model_copy, model_validate_json, site_url).


##### `ApplicationBuildAcceptance._blocked`  (lines 494–507)

```
def _blocked(self, result: ApplicationBuilderResult, reason: str, browser_batches: int) -> ApplicationBuilderResult
```

**Purpose**: This helper turns a failed acceptance check into a clean blocked build result. It preserves useful evidence, such as controls checked and observed errors, while replacing deployment identity with a blocker reason.

**Data flow**: It receives the worker result, a human-readable reason, and the number of browser QA batches. It creates a new `ApplicationBuilderResult` with status `blocked`, the fixed source path, preserved evidence, and the supplied blocker message.

**Call relations**: `ApplicationBuildAcceptance.accept` calls this whenever the worker's claimed deployment cannot be trusted. It keeps all rejection paths shaped the same way.

*Call graph*: called by 1 (accept); 1 external calls (__init__).


##### `EditApplicationSourceInput.json_text_edits_are_objects`  (lines 547–569)

```
def json_text_edits_are_objects(cls, value: object) -> object
```

**Purpose**: This input normalizer lets the source-edit tool accept a few convenient edit formats while converting them into one standard shape. It is there because model-generated tool input may arrive as JSON strings, patch-style strings, or pairs of plain strings.

**Data flow**: It receives the raw `edits` value before validation. It leaves already-structured objects alone, parses JSON-looking strings, converts search/replace patch text into `old_text` and `new_text`, and can pair an even list of strings into edit objects. The output is a tuple of normalized edit entries.

**Call relations**: This runs before `edit_application_source` sees its arguments. It makes the edit tool more forgiving at the boundary while still requiring exact replacements later.

*Call graph*: 1 external calls (loads).


##### `_validate_application_source`  (lines 572–587)

```
def _validate_application_source(source: str) -> None
```

**Purpose**: This function checks that `app.tsx` follows the builder's strict source rules. It keeps the app tied to the expected UFO kit runtime and blocks risky or unsupported code patterns.

**Data flow**: It reads the full source text, scans imports, exports, the root mount call, and a known bad color pattern. If the source imports only from `ufo/kit`, mounts correctly, and avoids forbidden patterns, it returns nothing; otherwise it raises a clear validation error.

**Call relations**: `write_application_source` uses this on the first full source write, and `edit_application_source` uses it after repairs. Passing this check is required before compilation and acceptance.

*Call graph*: called by 2 (edit_application_source, write_application_source).


##### `_validate_application_design`  (lines 590–682)

```
def _validate_application_design(source: str) -> tuple[str, ...]
```

**Purpose**: This function checks that the SVG design contract is safe, valid, visible, and divided into the required named regions. It prevents the design file from hiding scripts, links, duplicate IDs, malformed SVG, or unusable layout regions.

**Data flow**: It receives SVG text, rejects dangerous XML features, parses the SVG, checks its `viewBox`, walks every element, counts visible drawing elements, validates region markers, and rejects active or external content. It returns the ordered region names when the design is acceptable.

**Call relations**: `write_application_design` calls this before accepting a design, and `_require_application_design` calls it before source writing. The returned names are later compared with the browser-rendered region audit.

*Call graph*: called by 2 (_require_application_design, write_application_design); 3 external calls (isfinite, split, fromstring).


##### `_build_application_project`  (lines 685–699)

```
async def _build_application_project(ctx: ToolContext, project: str, runtime_root: str | None=None) -> None
```

**Purpose**: This function runs the real build step for the app project. It writes the project configuration, unpacks the shared page kit, and asks Vite, the JavaScript build tool, to compile the app.

**Data flow**: It receives a tool context, a project path, and optionally a runtime root. It writes configuration files in the right place, installs the page kit files, runs `vite build`, and either finishes silently or raises a shortened compiler error.

**Call relations**: `_compile_application_source` uses this for isolated compile checks, while `write_application_source` and `edit_application_source` use it to build the actual scaffold after accepted source changes.

*Call graph*: called by 3 (_compile_application_source, edit_application_source, write_application_source); 1 external calls (unpack_page_kit).


##### `_compile_application_source`  (lines 702–715)

```
async def _compile_application_source(ctx: ToolContext, task: ApplicationBuilderTask, source: str) -> None
```

**Purpose**: This function checks whether a proposed `app.tsx` can compile before it is allowed into the real scaffold. It gives the worker a safe trial build area.

**Data flow**: It reads the scaffold's existing `index.html`, writes that and the proposed source into a temporary runtime project, and calls `_build_application_project`. Nothing is returned if the source compiles; a failure becomes an error before the real app is overwritten.

**Call relations**: `write_application_source` and `edit_application_source` call this after source validation. It uses `_runtime_root` and then delegates the actual build to `_build_application_project`.

*Call graph*: calls 2 internal fn (_build_application_project, _runtime_root); called by 2 (edit_application_source, write_application_source).


##### `_source_claim_path`  (lines 718–722)

```
async def _source_claim_path(ctx: ToolContext, task: ApplicationBuilderTask, turn_id: UUID) -> str
```

**Purpose**: This helper computes the hidden runtime file used as the ownership marker for the current source-write attempt. The marker stops repeated full writes after an initial candidate exists.

**Data flow**: It takes the context, task, and turn ID, hashes the source path, and builds a runtime path ending in `.claimed`. The result is a safe internal path, not a user-facing source path.

**Call relations**: `write_application_source` uses this to claim the first source write. `_require_application_source` checks the same marker before allowing reads or repairs.

*Call graph*: called by 2 (_require_application_source, write_application_source); 1 external calls (sha256).


##### `_runtime_root`  (lines 725–726)

```
async def _runtime_root(ctx: ToolContext) -> str
```

**Purpose**: This helper finds the sandbox runtime root used by the small containment scripts. It keeps internal claim and candidate files separate from normal workspace files.

**Data flow**: It asks the sandbox for the runtime path to `tool-output`, then returns that path's parent as a string. The output is used as a base directory for contained file operations.

**Call relations**: Many source and design helpers call this before running sandbox Python snippets. It is a common bridge between high-level tool logic and low-level safe file access.

*Call graph*: called by 8 (accept, _compile_application_source, _require_application_design, _require_application_source, edit_application_source, read_application_source, write_application_design, write_application_source); 1 external calls (PurePosixPath).


##### `_design_path`  (lines 729–730)

```
def _design_path(task: ApplicationBuilderTask) -> str
```

**Purpose**: This helper gives the fixed workspace path for the design SVG belonging to a build task. It makes sure all design-writing code points to the same expected filename.

**Data flow**: It reads the scaffold path from the task and appends `application-design.svg`. The result is the workspace path where the visible design contract is stored.

**Call relations**: `write_application_design`, `_design_claim_path`, and `_require_application_design` all use this so the design claim, validation, and final write refer to one shared location.

*Call graph*: called by 3 (_design_claim_path, _require_application_design, write_application_design).


##### `_design_claim_path`  (lines 733–737)

```
async def _design_claim_path(ctx: ToolContext, task: ApplicationBuilderTask, turn_id: UUID) -> str
```

**Purpose**: This helper computes the hidden runtime file used to mark that the design has been claimed for this build turn. It prevents a worker from replacing the design after it has been fixed.

**Data flow**: It takes the context, task, and turn ID, gets the design path, hashes it, and returns a runtime `.claimed` path. The output is an internal ownership marker.

**Call relations**: `write_application_design` creates this marker, and `_require_application_design` checks it before source writing can begin.

*Call graph*: calls 1 internal fn (_design_path); called by 2 (_require_application_design, write_application_design); 1 external calls (sha256).


##### `application_design_acceptance_relative`  (lines 740–746)

```
def application_design_acceptance_relative(design_path: str, turn_id: UUID) -> str
```

**Purpose**: This function creates the runtime-relative path where an accepted design copy is stored. That accepted copy acts like a sealed receipt for the design used in this builder turn.

**Data flow**: It receives a design path and turn ID, hashes the design path, and returns a predictable relative path ending in `.accepted.svg`. It does not touch the file system itself.

**Call relations**: `write_application_design` uses this path while staging and accepting a candidate SVG. Other code can use the same formula to find the accepted design artifact for the turn.

*Call graph*: called by 1 (write_application_design); 1 external calls (sha256).


##### `_source_candidate_path`  (lines 749–755)

```
async def _source_candidate_path(ctx: ToolContext, task: ApplicationBuilderTask, turn_id: UUID) -> str
```

**Purpose**: This helper computes the hidden runtime path for the current candidate `app.tsx`. The candidate is the draft source kept for repair even if the first validation or compile attempt fails.

**Data flow**: It receives the context, task, and turn ID, hashes the source path, and returns a runtime path ending in `.candidate.tsx`. The path is stable for that task and turn.

**Call relations**: `write_application_source` writes the first candidate there. `read_application_source` and `edit_application_source` use the same path during repair.

*Call graph*: called by 3 (edit_application_source, read_application_source, write_application_source); 1 external calls (sha256).


##### `_render_application_design`  (lines 758–792)

```
async def _render_application_design(ctx: ToolContext, candidate_path: str, names: tuple[str, ...]) -> tuple[ApplicationAuditRegion, ...]
```

**Purpose**: This function runs a browser-based audit of the SVG design to confirm its named regions are actually visible and do not overlap. It catches problems that plain XML parsing cannot see.

**Data flow**: It writes an audit script into the runtime area, runs it with Node.js against the candidate SVG, parses the JSON region output, compares it with the expected names, and checks each region pair for overlap. It returns the measured region records if everything is valid.

**Call relations**: `write_application_design` calls this after `_validate_application_design`. The plain SVG check verifies structure, and this function verifies what the design looks like when rendered.

*Call graph*: called by 1 (write_application_design); 1 external calls (application_region_relation).


##### `_source_acceptance_path`  (lines 795–801)

```
async def _source_acceptance_path(ctx: ToolContext, task: ApplicationBuilderTask, turn_id: UUID) -> str
```

**Purpose**: This helper computes the hidden runtime path where the accepted source hash is stored. That hash is later used to prove the deployed app matches the checked source.

**Data flow**: It receives the context, task, and turn ID, hashes the source path, and returns a runtime path ending in `.accepted`. The path holds the SHA-256 digest, which is a fingerprint of the accepted source.

**Call relations**: `write_application_source` and `edit_application_source` write to this path after successful validation and build. `ApplicationBuildAcceptance.accept` reads it before accepting the final deployment.

*Call graph*: called by 3 (accept, edit_application_source, write_application_source); 1 external calls (sha256).


##### `_require_application_source`  (lines 804–813)

```
async def _require_application_source(ctx: ToolContext, task: ApplicationBuilderTask) -> None
```

**Purpose**: This guard makes sure an initial source candidate exists before the worker can read or repair it. It prevents repair tools from being used before there is anything to repair.

**Data flow**: It looks up the source claim marker for the current turn through a sandbox containment script. If the marker is missing, it raises a user-facing error; if the check itself fails, it raises a runtime error; otherwise it returns nothing.

**Call relations**: `read_application_source` and `edit_application_source` call this first. It depends on `_source_claim_path` and `_runtime_root` to find the internal claim marker.

*Call graph*: calls 2 internal fn (_runtime_root, _source_claim_path); called by 2 (edit_application_source, read_application_source).


##### `_require_application_design`  (lines 816–829)

```
async def _require_application_design(ctx: ToolContext, task: ApplicationBuilderTask) -> None
```

**Purpose**: This guard makes sure a valid design has been written before any source code can be written. It enforces the workflow order: design first, code second.

**Data flow**: It checks for the design claim marker, reads the design SVG from the workspace, and validates the SVG again. If the design is missing or invalid, it raises an error; otherwise it returns nothing.

**Call relations**: `write_application_source` calls this before accepting the initial `app.tsx`. It uses the design path and claim helpers plus `_validate_application_design`.

*Call graph*: calls 4 internal fn (_design_claim_path, _design_path, _runtime_root, _validate_application_design); called by 1 (write_application_source).


##### `write_application_design`  (lines 832–924)

```
async def write_application_design(ctx: ToolContext, args: WriteApplicationDesignInput) -> ToolResult
```

**Purpose**: This tool writes and fixes the SVG design contract for the app's first screen. Once it succeeds, the design is considered locked for that build.

**Data flow**: It reads the build task from the turn, validates the SVG text, writes a candidate design, runs the browser design audit, claims ownership, stores an accepted copy, and writes the visible workspace file. It returns JSON with the design path, digest, and size.

**Call relations**: This is exposed as the builder-only `write_application_design` tool. It calls the design validators, path helpers, browser audit, and cleanup helper; later `write_application_source` requires this work to have completed.

*Call graph*: calls 7 internal fn (_complete_application_design_cleanup, _design_claim_path, _design_path, _render_application_design, _runtime_root, _validate_application_design, application_design_acceptance_relative); 4 external calls (__init__, __init__, sha256, dumps).


##### `_complete_application_design_cleanup`  (lines 927–943)

```
async def _complete_application_design_cleanup(ctx: ToolContext, program: str, *args: str) -> tuple[ExecResult | None, tuple[str, ...]]
```

**Purpose**: This helper tries hard to finish cleanup for design claim or accepted-design files, even if cancellation happens. It reduces the chance of leaving stale locks behind.

**Data flow**: It starts a sandbox Python cleanup program as an asynchronous task, shields it while waiting, records interruption messages, and finally returns either the execution result or no result plus failure notes.

**Call relations**: `write_application_design` calls this only when an error occurs after claiming or accepting a design. It supports the rollback path rather than the normal success path.

*Call graph*: called by 1 (write_application_design); 2 external calls (create_task, shield).


##### `read_application_source`  (lines 946–1000)

```
async def read_application_source(ctx: ToolContext, args: ReadApplicationSourceInput) -> ToolResult
```

**Purpose**: This tool gives the worker small, targeted excerpts from the current candidate source during repair. It avoids dumping the whole file while still showing enough context to make exact edits.

**Data flow**: It validates the task, requires an existing source claim, reads the candidate `app.tsx`, counts matches for requested search terms, selects line windows around the start, end, and matches, and returns a bounded text report.

**Call relations**: The builder uses this after an edit fails because the old text did not match. It calls `_require_application_source`, `_source_candidate_path`, and `_runtime_root`, and its use is limited by `limit_application_builder_repair_reads`.

*Call graph*: calls 3 internal fn (_require_application_source, _runtime_root, _source_candidate_path); 2 external calls (__init__, __init__).


##### `edit_application_source`  (lines 1003–1051)

```
async def edit_application_source(ctx: ToolContext, args: EditApplicationSourceInput) -> ToolResult
```

**Purpose**: This tool applies exact source repairs to the current candidate `app.tsx`. It requires each old text snippet to appear exactly once so the change cannot land in the wrong place.

**Data flow**: It reads the candidate source, checks every requested replacement for uniqueness and non-overlap, applies replacements, validates the new source, compiles it in a trial project, writes it to the real scaffold, builds the scaffold, stores the accepted source hash, and returns JSON about the edit.

**Call relations**: The builder uses this after the first full source write has created a candidate. It calls the source guard, candidate and acceptance path helpers, source validation, compile check, and final build step.

*Call graph*: calls 7 internal fn (_build_application_project, _compile_application_source, _require_application_source, _runtime_root, _source_acceptance_path, _source_candidate_path, _validate_application_source); 4 external calls (__init__, __init__, sha256, dumps).


##### `write_application_source`  (lines 1054–1098)

```
async def write_application_source(ctx: ToolContext, args: WriteApplicationSourceInput) -> ToolResult
```

**Purpose**: This tool performs the one allowed full write of `app.tsx` for the build. If the source is invalid, the draft is kept and the worker must repair it with exact edits instead of rewriting from scratch.

**Data flow**: It reads the task, requires a valid design, claims source ownership, writes the candidate source, validates and compiles it, then writes it into the real scaffold, builds the project, records the accepted source hash, and returns JSON with path and size.

**Call relations**: This is the builder's main source-writing tool. It must follow `write_application_design`, and if it fails validation it pushes the worker toward `read_application_source` and `edit_application_source`.

*Call graph*: calls 8 internal fn (_build_application_project, _compile_application_source, _require_application_design, _runtime_root, _source_acceptance_path, _source_candidate_path, _source_claim_path, _validate_application_source); 4 external calls (__init__, __init__, sha256, dumps).


##### `_preview_font`  (lines 1101–1102)

```
def _preview_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont
```

**Purpose**: This small helper returns the font used in generated preview images. It keeps the preview renderer from repeating font-loading details everywhere.

**Data flow**: It receives a font size and asks Pillow, the image library, for the default font at that size. It returns the font object for drawing text.

**Call relations**: `_application_preview` and `_preview_card` call this whenever they draw labels or descriptions in the preview PNG.

*Call graph*: called by 2 (_application_preview, _preview_card); 1 external calls (load_default).


##### `_preview_text`  (lines 1105–1111)

```
def _preview_text(value: str, width: int, lines: int) -> str
```

**Purpose**: This helper wraps and shortens text so it fits inside the preview image. It adds an ellipsis when text is too long, like a card title in a user interface.

**Data flow**: It receives raw text, a target line width, and a maximum number of lines. It trims and wraps the text, cuts it to the allowed lines, adds `…` if needed, and returns the display string.

**Call relations**: `_application_preview` and `_preview_card` use this before drawing user-provided labels and descriptions into fixed-size areas.

*Call graph*: called by 2 (_application_preview, _preview_card); 1 external calls (wrap).


##### `_preview_card`  (lines 1114–1147)

```
def _preview_card(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], title: str, detail: str) -> None
```

**Purpose**: This helper draws one region card in the preview image. Each card represents one planned area of the app screen.

**Data flow**: It receives a drawing surface, a rectangle, a title, and detail text. It draws a rounded box, writes the wrapped title, draws a divider line, and adds wrapped detail text when there is room.

**Call relations**: `_application_preview` calls this once per requested region after `_preview_boxes` has chosen where each card should sit.

*Call graph*: calls 2 internal fn (_preview_font, _preview_text); called by 1 (_application_preview); 3 external calls (line, rounded_rectangle, text).


##### `_preview_boxes`  (lines 1150–1194)

```
def _preview_boxes(layout: ApplicationLayout, count: int) -> tuple[tuple[int, int, int, int], ...]
```

**Purpose**: This helper calculates where region cards should appear in the preview image for different layout styles. It turns abstract layout choices such as `timeline` or `metrics` into actual rectangles.

**Data flow**: It receives the layout name and region count, then computes card boxes within the fixed preview canvas. It returns a tuple of rectangle coordinates sized and arranged for that layout.

**Call relations**: `_application_preview` calls this before drawing cards. The returned boxes are paired with region names and passed to `_preview_card`.

*Call graph*: called by 1 (_application_preview).


##### `_application_preview`  (lines 1197–1264)

```
def _application_preview(args: RenderApplicationPreviewInput) -> bytes
```

**Purpose**: This function draws the full PNG preview image from a small design contract. It gives the user a quick visual sketch without running the app builder or browser.

**Data flow**: It receives preview input, creates a blank image, draws a header, purpose, first-screen priority panel, region cards, and design direction note, then saves the image into memory as PNG bytes.

**Call relations**: `_PillowApplicationPreview.render` calls this as the actual rendering implementation. It uses the preview text, font, box, and card helpers to assemble the image.

*Call graph*: calls 4 internal fn (_preview_boxes, _preview_card, _preview_font, _preview_text); called by 1 (render); 3 external calls (new, Draw, BytesIO).


##### `_PillowApplicationPreview.render`  (lines 1269–1270)

```
def render(args: RenderApplicationPreviewInput) -> bytes
```

**Purpose**: This wrapper exposes preview rendering through a simple static method. It makes the renderer easy to call from an asynchronous tool without tying that tool directly to the drawing function.

**Data flow**: It receives the typed preview input and returns the PNG bytes produced by `_application_preview`. It does not change any files or state itself.

**Call relations**: `render_application_preview` calls this inside a background thread so image generation does not block the async request flow.

*Call graph*: calls 1 internal fn (_application_preview).


##### `render_application_preview`  (lines 1273–1284)

```
async def render_application_preview(ctx: ToolContext, args: RenderApplicationPreviewInput) -> ToolResult
```

**Purpose**: This tool creates and shares a preview PNG for a proposed application design. It also returns a digest, a stable fingerprint of the preview contract, so the conversation can refer back to exactly what was previewed.

**Data flow**: It receives typed preview fields, renders the PNG in a worker thread, shares the image artifact, serializes the preview contract, hashes it, and returns JSON containing the shared filename and digest.

**Call relations**: This is exposed as the public `render_application_preview` tool. It calls `_PillowApplicationPreview.render`, uses the tool context to share the artifact, and returns a structured tool result.

*Call graph*: calls 1 internal fn (share_artifact); 7 external calls (__init__, __init__, __init__, to_thread, model_dump, sha256, dumps).


##### `_ensure_application_scaffold`  (lines 1287–1295)

```
async def _ensure_application_scaffold(ctx: ToolContext) -> None
```

**Purpose**: This helper makes sure the fixed application workspace exists before the builder worker starts. It creates the basic page files only when they are missing.

**Data flow**: It checks for `index.html`, placeholder `app.tsx`, and `preview.html` by trying to read each file. For any missing file, it writes the built-in default content.

**Call relations**: `build_ufo_application` calls this before spawning the application builder subagent. It prepares the known scaffold that later source and build tools rely on.

*Call graph*: called by 1 (build_ufo_application).


##### `build_ufo_application`  (lines 1298–1350)

```
async def build_ufo_application(ctx: ToolContext, _args: BuildUfoApplicationInput) -> ToolResult
```

**Purpose**: This is the main delegation tool that starts one full application build. It prepares the request, spawns the restricted builder worker, and then verifies the worker's result before returning it.

**Data flow**: It records redeploy and audit-contract information when available, ensures the scaffold exists, builds an objective from the agent prompt and user request, spawns the builder profile, parses the returned result, runs acceptance checks, and returns a structured deployed-or-blocked result.

**Call relations**: This is exposed as `build_ufo_application` to the parent flow. It calls `_ensure_application_scaffold`, spawns the `APPLICATION_BUILDER_PROFILE`, and hands the worker output to `ApplicationBuildAcceptance.accept`.

*Call graph*: calls 1 internal fn (_ensure_application_scaffold); 9 external calls (__init__, __init__, __init__, __init__, __init__, spawn, sha256, format, format).


##### `limit_application_builder_repair_reads`  (lines 1353–1388)

```
async def limit_application_builder_repair_reads(ctx: HookContext) -> Deny | None
```

**Purpose**: This hook limits how many consecutive source-read calls the builder can make after product QA has asked for repair. It nudges the worker to stop inspecting and actually edit, test, and redeploy.

**Data flow**: It checks whether the current turn belongs to the application builder and whether the tool about to run is source read or edit. If repair attempts are active, it increments a read counter, resets it on edit, or returns a denial once the read limit is reached.

**Call relations**: The tool system calls this before tool use. It only affects the builder profile and works with audit-attempt state written elsewhere in the application audit flow.

*Call graph*: 2 external calls (__init__, format).


##### `require_application_builder_qa`  (lines 1391–1404)

```
async def require_application_builder_qa(ctx: HookContext) -> Deny | None
```

**Purpose**: This hook blocks deployment until deterministic product QA has passed. Deterministic QA means a repeatable check, not just the worker saying the app looks fine.

**Data flow**: It checks whether the current turn belongs to the application builder, looks for stored QA proof for that turn, and validates that proof. If proof is missing, it returns a denial; if proof is malformed, it raises an error; otherwise deployment may continue.

**Call relations**: The tool system calls this as a deployment guard for the builder. `ApplicationBuildAcceptance.accept` later reuses the stored proof to verify the deployed source matches what QA checked.

*Call graph*: 2 external calls (__init__, model_validate).


### Application QA Rules
Pass/fail audit logic that translates browser measurements into actionable repair guidance.

### `extensions/sites/ufo_ext_sites/application_audit.py`

`domain_logic` · `QA audit after browser measurement`

This file is a quality gate for an application built by the system. A browser has already opened the app and recorded what it saw: text contrast, page width, clipped content, visible regions, console errors, accessible controls, and interaction results. This file gives that report a fixed, repeatable judgment.

It also defines the shapes of the data used in that judgment. For example, an audit report contains several measured views: light and dark mode, desktop and narrow phone width. A contract can list facts that the app must display. A verdict contains a short list of issues the builder should fix.

The main audit works like a checklist. It asks: did all required views get measured? Is there readable text? Does text meet accessibility contrast rules? Does the page overflow sideways? Are important design regions present, visible, and in the same relative order as the accepted design? Did the browser report errors? Are there enough accessible controls and real interactions? Are required facts shown, and are they visible early on the desktop page?

The important behavior is that this is deterministic: the same report should produce the same verdict every time. It limits issue counts and message lengths so feedback stays bounded and usable.

#### Function details

##### `ApplicationAuditReport.views_are_unique`  (lines 172–176)

```
def views_are_unique(self) -> 'ApplicationAuditReport'
```

**Purpose**: This validation step makes sure an audit report does not contain two measurements for the same color scheme and screen width. Without this, later checks could silently pick one duplicate and ignore another, making the verdict unreliable.

**Data flow**: It reads the report's list of views, turns each view into a pair like light mode plus 1440 pixels, and checks whether any pair appears more than once. If all pairs are unique, the report is accepted unchanged; if not, report creation fails with an error.

**Call relations**: This runs automatically when an ApplicationAuditReport is created by the data validation library. The later audit code depends on this promise when it builds a lookup table of views by scheme and width.


##### `ApplicationAuditVerdict.passed`  (lines 197–200)

```
def passed(self) -> bool
```

**Purpose**: This property answers the simple question: did the application pass every audit check? It gives callers a plain yes/no result instead of making them inspect the issue list themselves.

**Data flow**: It reads the verdict's issues. If the issue list is empty, it returns true; if there is even one issue, it returns false. It does not change the verdict.

**Call relations**: Code that receives an ApplicationAuditVerdict can call this after audit_application has built the verdict. It is the small final interpretation layer over the detailed repair messages.


##### `_needed_ratio`  (lines 247–250)

```
def _needed_ratio(px: float, weight: int) -> float
```

**Purpose**: This helper decides what text contrast ratio is required for a piece of text. Larger or bold text is allowed a lower contrast threshold; ordinary body text must meet a stricter one.

**Data flow**: It receives a text size in pixels and a font weight. If the text counts as large, or large-and-bold, it returns the large-text accessibility threshold; otherwise it returns the normal body-text threshold.

**Call relations**: audit_application calls this while checking every measured text item. It supplies the standard used to decide whether a browser-reported contrast ratio is acceptable.

*Call graph*: called by 1 (audit_application).


##### `_issue`  (lines 253–260)

```
def _issue(code: AuditIssueCode, message: str, terms: tuple[str, ...]=()) -> ApplicationAuditIssue
```

**Purpose**: This helper creates one audit issue in a safe, bounded format. It trims long messages and too many search terms so feedback cannot grow without limit.

**Data flow**: It receives an issue code, a human-readable message, and optional terms related to the problem. It shortens the message to the maximum allowed length, keeps only the first ten terms, and returns an ApplicationAuditIssue object.

**Call relations**: audit_application uses this every time it finds a problem, such as missing views, poor contrast, browser errors, or missing facts. It centralizes the rules for making repair messages consistent.

*Call graph*: called by 1 (audit_application); 1 external calls (__init__).


##### `application_region_relation`  (lines 263–277)

```
def application_region_relation(first: ApplicationAuditRegion, second: ApplicationAuditRegion) -> tuple[Literal['horizontal', 'vertical'], int] | None
```

**Purpose**: This function compares two named screen regions and says whether one is clearly above, below, left of, or right of the other. It is used to check whether the application keeps the same layout order as the accepted design.

**Data flow**: It receives two rectangular regions whose positions and sizes are expressed as fractions of the screen. It compares their edges with a small allowance for near-touching regions. It returns the separation direction and order, or nothing if the regions overlap or cannot be cleanly ordered.

**Call relations**: application_design_fidelity calls this first on design regions and then on matching application regions. It acts like a ruler for deciding whether layout relationships have been preserved.

*Call graph*: called by 1 (application_design_fidelity).


##### `application_design_fidelity`  (lines 280–354)

```
def application_design_fidelity(report: ApplicationAuditReport) -> ApplicationDesignFidelity
```

**Purpose**: This function scores how closely the running application matches the accepted design's named regions. It checks that the same regions exist, are visible early on the desktop page, and keep the same relative order.

**Data flow**: It receives a full audit report. It reads the accepted design regions, verifies their count and names, rejects overlapping design regions, then compares those regions against each desktop measurement in light and dark mode. It returns a score object containing how many checks passed, how many were attempted, and the specific failures.

**Call relations**: audit_application calls this as the design-matching part of the overall audit. Inside, it repeatedly calls application_region_relation to compare pairs of regions, then hands back failures that become a design issue in the final verdict.

*Call graph*: calls 1 internal fn (application_region_relation); called by 1 (audit_application); 1 external calls (__init__).


##### `audit_application`  (lines 357–473)

```
def audit_application(report: ApplicationAuditReport, contract: ApplicationAuditContract | None=None) -> ApplicationAuditVerdict
```

**Purpose**: This is the main checker for the file. It turns a browser audit report, and optionally a list of required facts, into a bounded verdict telling the builder what must be repaired.

**Data flow**: It receives the measured browser report and an optional contract of facts that must appear. It checks required views, empty pages, text contrast, horizontal overflow, clipped content, design fidelity, console errors, accessible controls, interaction success, required facts, and whether those facts appear above the fold on desktop. It collects problems as audit issues and returns an ApplicationAuditVerdict containing at most the allowed number of issues.

**Call relations**: This function is the place other parts of the system call when they need an accept-or-repair decision. It uses _needed_ratio for contrast rules, application_design_fidelity for layout matching, and _issue to package each problem into consistent feedback before returning the final verdict.

*Call graph*: calls 3 internal fn (_issue, _needed_ratio, application_design_fidelity); 1 external calls (__init__).


### Source Staging
Movement and preparation of saved site source files between persistent storage and editable build sandboxes.

### `extensions/sites/ufo_ext_sites/source.py`

`io_transport` · `request handling`

A hosted site’s source code is not kept as one neat folder in the sandbox forever. It lives in blob storage, described by a manifest, and is copied into a sandbox only when a conversation or tool needs to read or edit it. This file is the bridge that does that copying safely.

The main job is to “materialize” a site: recreate the saved source tree under the sandbox’s sites directory. Before writing files, it checks a generation stamp. If the sandbox already has the same deployed version, it skips the download so ordinary reads, like asking for a link or visibility, do not waste time or overwrite in-progress edits.

When it must copy files, it first clears and “claims” the destination paths using containment checks. In plain terms, it makes sure every file being touched really stays inside the intended workspace folder. This matters because the sandbox is writable by other agents or tools, and unsafe paths or symbolic links could otherwise cause a download to overwrite the wrong file.

The file supports two storage shapes. In production-like storage, it asks for temporary download or upload web links and runs curl inside the sandbox. In development filesystem storage, it streams bytes through this process. It also packages and unpacks a shared page SDK, so app pages can rebuild using today’s components rather than the version from their first deployment.

#### Function details

##### `_page_kit_archive`  (lines 101–113)

```
def _page_kit_archive() -> bytes
```

**Purpose**: This function bundles the page SDK files into one compressed archive. It is used so the sandbox can receive one archive and unpack it, instead of copying many small files one by one.

**Data flow**: It starts with the kit directory on disk. It walks through the files, adds each regular file into an in-memory gzip tar archive, and returns the archive as bytes. Nothing is written to disk by this function; it produces a ready-to-send package.

**Call relations**: This runs when the module is imported to create the shared PAGE_KIT_ARCHIVE value. Later, unpack_page_kit uses that prepared archive when it needs to place the SDK into a sandbox project.

*Call graph*: 2 external calls (BytesIO, open).


##### `transfer`  (lines 120–130)

```
async def transfer(ctx: ToolContext, script: str, pairs: list[tuple[str, str]], total_bytes: int) -> None
```

**Purpose**: This function copies many files by running a small shell script in the sandbox, usually using temporary web links. It batches the work so large source trees do not require one separate sandbox command per file.

**Data flow**: It receives a sandbox tool context, a script, pairs of local paths and remote URLs, and the total byte count. It calculates a timeout based on the amount of data, sends the pairs to the sandbox in chunks, and waits for each chunk to finish. If any chunk fails, it raises an error with the sandbox’s message; otherwise it returns nothing after the files have been moved.

**Call relations**: materialize_source calls this when blob storage can provide presigned download links, meaning temporary URLs that grant short-lived access. transfer is the lower-level mover: materialize_source decides what should move, and transfer carries out the batched copy inside the sandbox.

*Call graph*: called by 1 (materialize_source).


##### `materialize_source`  (lines 133–199)

```
async def materialize_source(ctx: ToolContext, site: HostedSite, object_name: str) -> tuple[str, list[str]]
```

**Purpose**: This function recreates a site’s stored source files inside the current sandbox. A tool uses it when it needs the real editable project tree for a hosted site, instead of just metadata about the site.

**Data flow**: It receives the tool context, the site record, and the sandbox object name to write under. It reads the site’s source manifest, chooses a destination folder, and checks a generation stamp to see whether the same deploy version is already present. If not, it safely empties and prepares the destination files, downloads or streams each manifest file from blob storage, writes the new generation stamp, and returns the destination path plus the list of restored relative file paths.

**Call relations**: This is the main public flow in the file. It validates the stored manifest, uses workspace_path to place files inside the sandbox workspace, uses shlex.quote when safely reading the stamp path through a shell command, and calls transfer when downloads happen through temporary URLs. If the storage backend does not support those URLs, it falls back to reading each blob through this process and writing it into the sandbox.

*Call graph*: calls 1 internal fn (transfer); 3 external calls (model_validate_json, quote, workspace_path).


##### `unpack_page_kit`  (lines 202–220)

```
async def unpack_page_kit(ctx: ToolContext, dest: str, runtime_root: str | None=None) -> None
```

**Purpose**: This function installs the current page SDK into a sandbox project folder. That SDK is what app pages import while building, so adding it at build time lets old app-page source use the current components.

**Data flow**: It receives the tool context, a destination folder, and optionally a runtime root used for containment checks. It writes the prebuilt SDK archive into the destination, either as a normal sandbox file or as a runtime path, then runs a guarded Python unpack script inside the sandbox. The script expands only regular files into the intended destination and deletes the archive afterward. If unpacking fails, this function raises an error.

**Call relations**: This function uses the archive produced earlier by _page_kit_archive. It is called by higher-level site build or deployment code when preparing an app page project, placing the SDK beside the project files before the build runs.


### Hosted Site Registry
Persistent registry and ownership enforcement for hosted site ports, static files, visibility, and replacement rules.

### `extensions/sites/ufo_ext_sites/store.py`

`domain_logic` · `request handling`

A hosted site is like a forwarding card in a directory: when someone opens a permanent site link, the system looks up this card to learn what page to serve, who owns it, and who is allowed to see it. This file defines that card in the database and provides the safe ways to create, update, read, and remove it.

The main table, `hosted_site`, stores the site identity, the sandbox port it comes from, its visibility, its creator, preview images, share-card data, homepage binding, and, for static sites, a source manifest that points to files saved in blob storage. Blob storage means durable file storage outside the live sandbox.

The `HostedSites` class is the main doorway. Callers give it a workspace and a database transaction factory, and every query is scoped to that workspace so one workspace cannot accidentally see or edit another workspace’s sites. Its methods enforce rules before writing: a site name is unique within a conversation, one port cannot silently serve two different site names, only the creator can change disclosure settings, and taking over someone else’s hosted port is blocked.

A subtle but important detail is the `deploy_generation` value. It works like a fresh version stamp so the web frame knows when to reload, even if a site was deleted and later recreated under the same name.

#### Function details

##### `SourceManifest._rooted`  (lines 96–99)

```
def _rooted(cls, root: str) -> str
```

**Purpose**: Checks that the saved source-file prefix lives in an allowed storage area. This prevents a manifest from pointing at an unexpected part of blob storage.

**Data flow**: It receives a root string from a `SourceManifest`. If the string starts with `sites/` or `apps/` and ends with `/`, it is returned unchanged. Otherwise, validation fails with an error before the manifest can be accepted.

**Call relations**: This runs automatically when a `SourceManifest` is built. It is an early guard before any code trusts the manifest’s root path.


##### `SourceManifest._pathed`  (lines 103–118)

```
def _pathed(cls, files: dict[str, SiteFile]) -> dict[str, SiteFile]
```

**Purpose**: Checks that every file path in a static site manifest is a plain relative path inside the site. It blocks paths that try to escape the site folder, such as paths with leading slashes or parent-directory tricks.

**Data flow**: It receives the manifest’s file map. For each path, it rejects unsafe characters, backslashes, absolute paths, and paths that do not resolve cleanly under the fixed site-source anchor. If every path is safe, the same file map comes out unchanged.

**Call relations**: This runs during `SourceManifest` validation. It calls `ufo.sdk.sandbox.contained_relative`, which is the shared path-safety checker, to make sure reads later answer only with files from the intended site.

*Call graph*: 1 external calls (contained_relative).


##### `site_name`  (lines 149–157)

```
def site_name(raw: str) -> str
```

**Purpose**: Turns a user-provided site name into the safe stored name used in links and object names. It makes names predictable and URL-friendly.

**Data flow**: It takes raw text, lowercases it, turns runs of non-letter-or-digit characters into hyphens, trims extra hyphens, and limits the length. If nothing usable remains, it raises `InvalidSiteName`; otherwise it returns the cleaned slug.

**Call relations**: Callers use this before registering a site, because registration assumes the name is already safe. When the name cannot produce a real slug, it creates an `InvalidSiteName` error so the caller can report a useful tool failure.

*Call graph*: 1 external calls (__init__).


##### `default_visibility`  (lines 160–168)

```
def default_visibility(audience: Audience) -> Visibility
```

**Purpose**: Chooses the starting visibility for a newly hosted site based on the conversation audience. This avoids accidentally sharing externally sensitive work with a whole workspace.

**Data flow**: It receives an audience description, parses it, and checks whether it names a single member or an external audience. Member-only and foreign audiences become `private`; internal shared audiences become `workspace`.

**Call relations**: This is used by `HostedSites.register` only when a new site is created without an explicit visibility choice. It relies on the audience helpers to understand what kind of room produced the site.

*Call graph*: called by 1 (register); 2 external calls (audience_member, parse_audience).


##### `visibility_level`  (lines 171–179)

```
def visibility_level(value: str) -> Visibility
```

**Purpose**: Validates a visibility string from storage or input. It ensures the rest of the code only works with the three supported choices: private, workspace, or public.

**Data flow**: It receives a string. If it is one of the accepted visibility words, the same value comes out as a typed visibility value. Anything else raises a `ValueError`.

**Call relations**: `_site` uses this while turning database rows into `HostedSite` objects. That means a bad database value is caught at the boundary before it spreads through the system.

*Call graph*: called by 1 (_site).


##### `HostedSites.register`  (lines 213–306)

```
async def register(self, conversation_id: UUID, name: str, port: int, creator_member_id: UUID, visibility: Visibility | None, audience: Audience, may_unhost: bool, *, manifest: str | None) -> HostedSi
```

**Purpose**: Creates or updates the registry row for a site after a deploy. It is the main write path that decides what link points to what port or static manifest.

**Data flow**: It receives the conversation, safe site name, port, creator, optional visibility, audience, unhost permission, and optional static-source manifest. It opens a transaction, checks whether the write is allowed, removes any same-conversation site that would be displaced from the port, updates an existing row or inserts a new one, stamps a fresh deploy generation, and returns the final `HostedSite`.

**Call relations**: This is the central registration flow. It calls `_refuse` to enforce ownership and unhosting rules, `_read` to inspect and return rows, `default_visibility` for new sites without an explicit choice, and SQLAlchemy update/insert/delete helpers to make the database changes.

*Call graph*: calls 3 internal fn (_read, _refuse, default_visibility); 6 external calls (case, delete, insert, update, time_ns, uuid4).


##### `HostedSites.redeploy`  (lines 308–335)

```
async def redeploy(self, conversation_id: UUID, name: str, manifest: str) -> HostedSite | None
```

**Purpose**: Replaces the stored static source for an existing site without changing its identity, owner, port, or visibility. This lets a known link show new bytes while staying the same site.

**Data flow**: It receives a conversation id, site name, and new manifest string. It updates the matching row’s source manifest, updated time, and deploy generation, then reads the row back. If the row disappeared, it returns `None`.

**Call relations**: This is a narrower update path than `register`. It uses the same generation-stamp idea as registration and finishes through `_read` so callers get the current `HostedSite` view.

*Call graph*: calls 1 internal fn (_read); 3 external calls (case, update, time_ns).


##### `HostedSites.homepage`  (lines 337–349)

```
async def homepage(self, agent_id: UUID) -> HostedSite | None
```

**Purpose**: Finds the site currently bound as a specific agent’s homepage. If the agent has no bound homepage, it returns nothing.

**Data flow**: It receives an agent id, queries this workspace for a row whose homepage binding matches that agent, and converts the row into a `HostedSite`. If no row is found, it returns `None`.

**Call relations**: This is a read path for homepage lookup. It builds the common column selection with `_columns` and hands any result to `_site` for conversion into the in-code data object.

*Call graph*: calls 2 internal fn (_columns, _site).


##### `HostedSites.set_preview`  (lines 351–371)

```
async def set_preview(self, conversation_id: UUID, name: str, preview: StoredPreview) -> None
```

**Purpose**: Stores the screenshot-like preview captured for a site after deployment. It keeps the registry row linked to the preview blob and its exact size.

**Data flow**: It receives the site identity and a `StoredPreview` containing a blob key and byte size. It updates the matching row’s preview fields and updated time. It returns no value.

**Call relations**: This runs after `register`, because the site can be registered before its preview image finishes rendering. If the site has been removed or renamed by then, the update simply matches nothing.

*Call graph*: 1 external calls (update).


##### `HostedSites.set_share_card`  (lines 373–397)

```
async def set_share_card(self, conversation_id: UUID, name: str, blob_key: str, digest: str) -> None
```

**Purpose**: Stores the social/share-card image made from a site, along with a digest of that image. The digest lets public share-card URLs refer to an exact version.

**Data flow**: It receives the site identity, the share-card blob key, and the digest string. It writes those values plus an updated time to the matching row. It returns no value.

**Call relations**: This is called by `extensions/sites/ufo_ext_sites/share_card._compose` after a card has been generated. Like preview writing, it happens after registration and safely does nothing if the site no longer matches.

*Call graph*: called by 1 (_compose); 1 external calls (update).


##### `HostedSites.read`  (lines 399–401)

```
async def read(self, conversation_id: UUID, name: str) -> HostedSite | None
```

**Purpose**: Looks up one hosted site by conversation and name. It is the simple public read method for one registry entry.

**Data flow**: It receives a conversation id and site name, opens a transaction, and delegates to `_read`. The result is either a `HostedSite` or `None`.

**Call relations**: This wraps `_read` with transaction handling so callers do not need to pass a database connection themselves.

*Call graph*: calls 1 internal fn (_read).


##### `HostedSites.all`  (lines 403–413)

```
async def all(self) -> tuple[HostedSite, ...]
```

**Purpose**: Returns every hosted site in the workspace, ordered from oldest to newest. Other parts of the system can then apply their own access rules or display them.

**Data flow**: It opens a transaction, selects all rows for the workspace, orders them by creation time and name, converts each row into a `HostedSite`, and returns them as a tuple.

**Call relations**: This uses `_columns` to keep the selected fields consistent and `_site` to convert raw database rows into application objects.

*Call graph*: calls 2 internal fn (_columns, _site).


##### `HostedSites.conversation`  (lines 415–432)

```
async def conversation(self, conversation_id: UUID, names: tuple[str, ...], limit: int) -> tuple[HostedSite, ...]
```

**Purpose**: Returns selected hosted sites from one conversation. It is useful when a caller already knows which names it wants to inspect.

**Data flow**: It receives a conversation id, a tuple of names, and a limit. It queries matching rows in the workspace and conversation, orders them oldest first, limits the result count, converts rows to `HostedSite` objects, and returns them.

**Call relations**: This is a filtered list read. It shares the common selection shape through `_columns` and row conversion through `_site`.

*Call graph*: calls 2 internal fn (_columns, _site).


##### `HostedSites.visible_conversation`  (lines 434–464)

```
async def visible_conversation(self, conversation_id: UUID, member_id: UUID, limit: int, *, admin: bool, homepage_agents: frozenset[UUID]) -> tuple[HostedSite, ...]
```

**Purpose**: Returns the sites in a conversation that a particular member is allowed to open. It applies the main visibility rules for private sites, shared sites, homepage-bound sites, and admins.

**Data flow**: It receives a conversation id, member id, result limit, admin flag, and the set of homepage agents visible to that member. It builds a database condition: creators can see their own sites, non-private sites are visible, homepage-bound sites may be visible through their agent, and admins bypass this gate. It returns the matching rows as `HostedSite` objects.

**Call relations**: This is the access-filtered list path. It uses SQLAlchemy’s `or_` to combine visibility checks, then uses `_columns` and `_site` like the other read methods.

*Call graph*: calls 2 internal fn (_columns, _site); 1 external calls (or_).


##### `HostedSites.set_visibility`  (lines 466–481)

```
async def set_visibility(self, conversation_id: UUID, name: str, visibility: Visibility) -> HostedSite | None
```

**Purpose**: Changes one site’s visibility setting and returns the updated site. It also changes the site generation so existing permission checks can notice the change.

**Data flow**: It receives a conversation id, site name, and new visibility. It updates the matching row’s visibility, assigns a new generation id, updates the timestamp, then reads the row back. If the row is gone, it returns `None`.

**Call relations**: This is the direct visibility update path. It uses a fresh `uuid4` value for the generation and `_read` to provide the caller with the final row.

*Call graph*: calls 1 internal fn (_read); 2 external calls (update, uuid4).


##### `HostedSites.set_homepage`  (lines 483–509)

```
async def set_homepage(self, agent_id: UUID, conversation_id: UUID, name: str) -> HostedSite | None
```

**Purpose**: Binds a named site as an agent’s homepage. It first clears any older homepage binding for that same agent so there is only one current homepage.

**Data flow**: It receives an agent id, conversation id, and site name. In one transaction, it removes that agent id from any existing homepage row, writes the agent id onto the named site, and reads that site back. If the named site no longer exists, it returns `None`.

**Call relations**: This coordinates two database updates before using `_read` for the result. The database’s uniqueness rule and the explicit clearing step work together to keep one homepage per agent.

*Call graph*: calls 1 internal fn (_read); 1 external calls (update).


##### `HostedSites.release_homepage`  (lines 511–535)

```
async def release_homepage(self, conversation_id: UUID, name: str, visibility: Visibility) -> None
```

**Purpose**: Removes a site’s homepage binding and restores the site’s own visibility level. This matters because a homepage-bound site follows the agent’s visibility while it is bound.

**Data flow**: It receives the site identity and the visibility that should apply after release. It clears the homepage agent id, writes the supplied visibility, assigns a new generation id, updates the timestamp, and returns nothing.

**Call relations**: This is the unbinding counterpart to `set_homepage`. It uses `uuid4` so any held authorization tied to the old generation will no longer silently apply.

*Call graph*: 2 external calls (update, uuid4).


##### `HostedSites.unregister`  (lines 537–548)

```
async def unregister(self, conversation_id: UUID, name: str) -> None
```

**Purpose**: Removes a site from the registry so its permanent link stops resolving. The sandbox process may still exist, but the site is no longer hosted through this directory.

**Data flow**: It receives a conversation id and site name. It deletes the matching row for this workspace and returns nothing.

**Call relations**: This is the explicit unhost path. It uses SQLAlchemy delete directly and does not call the read helpers because it does not need to return the removed row.

*Call graph*: 1 external calls (delete).


##### `HostedSites.refuse_or_pass`  (lines 550–570)

```
async def refuse_or_pass(self, conversation_id: UUID, name: str, port: int, creator_member_id: UUID, visibility: Visibility | None, may_unhost: bool) -> HostedSite | None
```

**Purpose**: Checks whether a future registration would be allowed, without writing anything. It also tells the caller which existing site would be displaced from the port.

**Data flow**: It receives the same key permission inputs used for registration: conversation, name, port, creator, optional visibility, and unhost permission. It opens a transaction and runs `_refuse`. If rules are violated, an error is raised; otherwise the displaced `HostedSite` or `None` is returned.

**Call relations**: This is a dry-run companion to `register`. The deploy flow can call it before serving on a port, while the old site is still alive, and `register` later repeats the same check during the actual write.

*Call graph*: calls 1 internal fn (_refuse).


##### `HostedSites._refuse`  (lines 572–601)

```
async def _refuse(self, connection: AsyncConnection, conversation_id: UUID, name: str, port: int, creator_member_id: UUID, visibility: Visibility | None, may_unhost: bool) -> HostedSite | None
```

**Purpose**: Enforces the rules that can block site registration. It protects visibility ownership and prevents a deploy from silently unhosting another member’s site.

**Data flow**: It receives an open database connection plus the proposed registration details. It reads the existing same-name site, checks whether a non-creator is trying to change visibility, finds any different site already using the port, and checks whether the actor owns it and is allowed to unhost. It returns the displaced site if registration may proceed, or raises an error if not.

**Call relations**: Both `register` and `refuse_or_pass` call this so the same refusal logic is used for dry runs and real writes. It uses `_read` for the same-name row and `_on_port` to find a port conflict, then raises `NotTheSiteCreator` or `UnhostNeedsASpeaker` when the rules say no.

*Call graph*: calls 2 internal fn (_on_port, _read); called by 2 (refuse_or_pass, register); 2 external calls (__init__, __init__).


##### `HostedSites._on_port`  (lines 603–618)

```
async def _on_port(self, connection: AsyncConnection, conversation_id: UUID, port: int, name: str) -> HostedSite | None
```

**Purpose**: Finds whether a different site in the same conversation is already registered on a given port. This is how the code detects that a new registration would displace an old name.

**Data flow**: It receives a database connection, conversation id, port, and current site name to exclude. It queries for another row in the same workspace and conversation with that port and a different name. It returns that site as a `HostedSite`, or `None` if no conflict exists.

**Call relations**: `_refuse` calls this during registration checks. It uses `_columns` for the shared select list and `_site` to turn any raw row into the normal data object.

*Call graph*: calls 2 internal fn (_columns, _site); called by 1 (_refuse); 1 external calls (execute).


##### `HostedSites._read`  (lines 620–632)

```
async def _read(self, connection: AsyncConnection, conversation_id: UUID, name: str) -> HostedSite | None
```

**Purpose**: Reads one site row using an already-open database connection. It is the shared low-level lookup used by many public methods.

**Data flow**: It receives a connection, conversation id, and site name. It selects the matching row within the workspace, converts it to a `HostedSite` if found, and returns `None` otherwise.

**Call relations**: This is called by `_refuse`, `read`, `redeploy`, `register`, `set_homepage`, and `set_visibility`. It centralizes the single-site lookup so all those paths use the same workspace scoping and row conversion.

*Call graph*: calls 2 internal fn (_columns, _site); called by 6 (_refuse, read, redeploy, register, set_homepage, set_visibility); 1 external calls (execute).


##### `HostedSites._columns`  (lines 634–651)

```
def _columns(self) -> sa.Select
```

**Purpose**: Builds the standard database select statement for hosted-site rows. It keeps every read method asking for the same fields in the same shape.

**Data flow**: It takes no outside data beyond the `HostedSites` object. It returns a SQLAlchemy select object containing the hosted-site columns needed to build a `HostedSite`.

**Call relations**: Read helpers and list methods call this before adding their own filters. `_on_port`, `_read`, `all`, `conversation`, `homepage`, and `visible_conversation` all depend on it so `_site` receives rows with the expected fields.

*Call graph*: called by 6 (_on_port, _read, all, conversation, homepage, visible_conversation); 1 external calls (select).


##### `_aware`  (lines 654–657)

```
def _aware(moment: datetime) -> datetime
```

**Purpose**: Makes sure a database timestamp includes a timezone. This avoids mistakes when comparing times from databases that return timezone-less values.

**Data flow**: It receives a `datetime`. If it already has timezone information, it returns it unchanged. If not, it marks it as UTC and returns the corrected value.

**Call relations**: `_site` calls this for `created_at` and `updated_at` while converting database rows. It uses `datetime.replace` only to attach UTC when the database omitted the timezone.

*Call graph*: called by 1 (_site); 1 external calls (replace).


##### `_site`  (lines 660–677)

```
def _site(row: sa.Row) -> HostedSite
```

**Purpose**: Converts a raw database row into a `HostedSite` object that the rest of the code can use safely. It is the boundary between database-shaped data and application-shaped data.

**Data flow**: It receives a SQLAlchemy row, validates the visibility string, fixes timestamp timezone awareness, copies all relevant fields, and returns a frozen `HostedSite` dataclass instance.

**Call relations**: All read paths that return hosted sites use this converter, including `_on_port`, `_read`, `all`, `conversation`, `homepage`, and `visible_conversation`. It calls `visibility_level` and `_aware` so every returned object has checked visibility and consistent timestamps.

*Call graph*: calls 2 internal fn (_aware, visibility_level); called by 6 (_on_port, _read, all, conversation, homepage, visible_conversation); 1 external calls (__init__).
