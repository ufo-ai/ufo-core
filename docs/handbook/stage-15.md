# Artifacts, media, hosted sites, and document outputs  `stage-15`

This stage is shared support for turning an agent’s work into things people can see, open, download, or share. It sits after tools create results, and it also supports the main work loop when sites or document previews need to stay available.

One part handles hosted websites. The site tools start servers, publish static or server-backed apps, and set a homepage. The audit code checks that an app is safe and matches its design before deployment. Source helpers move site files in and out of storage, while the site store records names, owners, permissions, ports, and saved files. Ingress files build signed links, route browser traffic to the right sandbox or stored site, isolate each site’s web address, and report broken sites back to the agent. Preview and share-card code captures screenshots for thumbnails and shared links. Conversation-slot code shows allowed sites inside the chat.

Another part handles artifacts and media. Artifact objects wrap shared files with metadata and permissions. Signed URL and download-route code let people fetch them safely for a limited time. Preview records describe stored images. Document rendering and office-file helper scripts turn PDFs, Word, PowerPoint, and Excel files into previews, comments, repaired files, or annotated outputs.

## Sub-stages

- [Document and office-file helper scripts](stage-15.1.md) `stage-15.1` — 22 files

## Files in this stage

### Hosted site publication
Agent-facing site tools validate applications, move saved source, and register deployed sites for workspace-safe access.

### `extensions/sites/ufo_ext_sites/tools.py`

`orchestration` · `tool invocation / request handling`

This file is the control room for website publishing. Without it, an agent could run a server in the sandbox, but users might get broken links, stale ports, unsafe file paths, missing source records, or sites exposed to the wrong audience. The file first treats every user-provided path as a workspace path, so a model cannot accidentally or deliberately point commands at arbitrary host files. When starting a server, it clears old logs safely, stops anything already using the target port, starts the new command in the background, and keeps checking until the port is actually reachable. For static deployments, it can also copy the served files into the system’s blob store, like putting the finished site in a permanent filing cabinet. For special UFO application pages, it does more than serve files: it validates the source, builds it, and runs a browser audit before allowing it to become a hosted site. Hosting then registers the live sandbox port under a stable site URL, optionally captures a preview image, and records who may open it. The homepage tool links one hosted site to an agent, where the agent’s visibility rules decide who can view it.

#### Function details

##### `_lifecycle_state`  (lines 185–200)

```
def _lifecycle_state(snapshot: _ApplicationLifecycleSnapshot) -> str
```

**Purpose**: Turns a detailed application lifecycle snapshot into a short human-readable reason, such as the page never mounting or work never finishing. It helps explain why a page failed the readiness audit.

**Data flow**: It receives a snapshot of the page’s runtime state. It checks whether the page mounted, whether it is idle, and what kinds of blocking work remain. It returns one short text summary of the problem.

**Call relations**: ApplicationPageGate._lifecycle_reason calls this when an audit says the page lifecycle failed. The text it returns becomes part of the refusal message shown to the builder.

*Call graph*: called by 1 (_lifecycle_reason).


##### `_lifecycle_joined`  (lines 203–205)

```
def _lifecycle_joined(reason: str, named: tuple[str, ...], dropped: bool) -> str
```

**Purpose**: Combines a main lifecycle reason with a list of named details into one readable sentence. It also adds an ellipsis when some details had to be left out.

**Data flow**: It receives a reason, detail strings, and a flag saying whether anything was dropped. It joins the details with semicolons and returns one message.

**Call relations**: _lifecycle_message uses this as its formatter while it decides how much detail can fit into the allowed message length.

*Call graph*: called by 1 (_lifecycle_message).


##### `_lifecycle_message`  (lines 208–230)

```
def _lifecycle_message(reason: str, named: tuple[str, ...]) -> str
```

**Purpose**: Builds a lifecycle failure message that fits inside the audit issue size limit. It keeps whole useful lines where possible, so the builder sees the most helpful repair clue instead of a chopped-up message.

**Data flow**: It receives a reason and a tuple of detail lines. It tries adding lines until the message would be too long, then trims only the next line if needed. It returns a capped message.

**Call relations**: ApplicationPageGate._lifecycle_reason calls this after gathering the lifecycle state and problem lines. It relies on _lifecycle_joined to produce the final sentence.

*Call graph*: calls 1 internal fn (_lifecycle_joined); called by 1 (_lifecycle_reason).


##### `StartServerInput.validate_port`  (lines 519–524)

```
def validate_port(self) -> 'StartServerInput'
```

**Purpose**: Checks that the start-server tool input makes sense before any command runs. It rejects an empty command and ports outside the valid network port range.

**Data flow**: It reads the already-parsed input model. If the command is blank or the port is invalid, it raises a validation error. Otherwise it returns the same input object unchanged.

**Call relations**: Pydantic, the input validation library, calls this automatically after creating StartServerInput for the start_server tool.


##### `_json_result`  (lines 565–566)

```
def _json_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: Wraps a Python dictionary as the JSON text returned by a tool. It gives all these website tools a consistent machine-readable response format.

**Data flow**: It receives a dictionary, turns it into a JSON string, places that string in a text content object, and returns a ToolResult.

**Call relations**: start_server, deploy_website, publish_website, _redeploy_homepage, and set_homepage all call this at the end of successful work.

*Call graph*: called by 5 (_redeploy_homepage, deploy_website, publish_website, set_homepage, start_server); 3 external calls (__init__, __init__, dumps).


##### `_free_log`  (lines 569–589)

```
async def _free_log(ctx: ToolContext, log_path: str) -> None
```

**Purpose**: Safely clears the chosen server log filename before a new server starts. This protects against a common file trick where a log path is secretly a link to something else.

**Data flow**: It receives the tool context and a log path. It chooses the allowed root, runs a guarded sandbox script to remove that file name, and raises an error if the guard refuses.

**Call relations**: _serve calls this before starting a server, so the later shell redirect creates a fresh log file instead of overwriting an unsafe target.

*Call graph*: called by 1 (_serve); 1 external calls (PurePosixPath).


##### `_stop_server`  (lines 592–597)

```
async def _stop_server(ctx: ToolContext, port: int) -> None
```

**Purpose**: Stops any process already listening on a given port inside the sandbox. This prevents a new deployment from racing or mixing with an old server.

**Data flow**: It receives a port number, runs a sandbox Python program that finds listeners on that port and terminates them, and raises an error if cleanup fails.

**Call relations**: _serve calls this near the start of every serve attempt, before launching the new server command.

*Call graph*: called by 1 (_serve).


##### `_stop_server_task`  (lines 600–617)

```
async def _stop_server_task(ctx: ToolContext, command: str, base: str, pid: str) -> None
```

**Purpose**: Stops a background server task that this tool started but that failed readiness. It avoids leaving a half-started process behind.

**Data flow**: It receives the original command, the task journal location, and the recorded process id. It asks the sandbox to stop that exact task and waits for its supervisor to finish. It raises an error if the task will not stop.

**Call relations**: _serve calls this when the readiness probe fails or when the probe path is interrupted, so failed starts do not keep running silently.

*Call graph*: called by 1 (_serve).


##### `_reset_server_task`  (lines 620–638)

```
async def _reset_server_task(ctx: ToolContext, base: str) -> None
```

**Purpose**: Clears the saved task journal for a background server launch. This makes sure a new launch really starts a new server instead of reattaching to an old recorded process.

**Data flow**: It receives the task journal base path. It stops any still-recorded process, waits briefly, and removes the journal files. It raises an error if the old task cannot be cleared.

**Call relations**: _serve calls this after freeing the port and before starting a detached task.

*Call graph*: called by 1 (_serve).


##### `_log_tail`  (lines 641–646)

```
async def _log_tail(ctx: ToolContext, log_path: str) -> str
```

**Purpose**: Reads the last few lines of a server log. It is used only when something went wrong, so the tool can report the useful ending of the log.

**Data flow**: It receives a log path, quotes it safely for the shell, runs tail inside the sandbox, and returns the captured standard output text.

**Call relations**: _serve calls this after a failed start or failed readiness check to turn log output into a useful failure message.

*Call graph*: called by 1 (_serve); 1 external calls (shell_path).


##### `ServeFailed.__init__`  (lines 658–660)

```
def __init__(self, failure: ToolFailure) -> None
```

**Purpose**: Creates an exception that carries a full ToolFailure. This lets lower-level serving code stop normal flow while preserving the exact user-facing failure report.

**Data flow**: It receives a ToolFailure, stores it on the exception, and uses the failure summary as the exception message.

**Call relations**: _serve_failed constructs this exception when _serve needs to report that a server could not be brought up.

*Call graph*: called by 1 (_serve_failed).


##### `_serve_failed`  (lines 663–682)

```
def _serve_failed(summary: str, result: ExecResult, project: str, port: int) -> ServeFailed
```

**Purpose**: Builds the standard failure object for a server start that did not succeed. It clearly states that the target port was already freed, so nothing is serving there now.

**Data flow**: It receives a short summary, the sandbox command result, the project path, and the port. It packages command output, exit status, timeout information, and the port side effect into a ToolFailure wrapped in ServeFailed.

**Call relations**: _serve calls this for each failure path. It hands off to ServeFailed.__init__ so callers can catch the error and return the stored tool result.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_serve); 3 external calls (__init__, __init__, __init__).


##### `_serve`  (lines 685–777)

```
async def _serve(ctx: ToolContext, command: str, project: str, port: int, log_path: str) -> dict[str, object]
```

**Purpose**: Starts a server in the sandbox and returns only after it is actually reachable. It is the shared engine behind starting, deploying, publishing, and homepage redeploys.

**Data flow**: It receives a command, project directory, port, and log path. It clears the log, frees the port, resets task bookkeeping, starts the command in the background with PORT set, probes the port until ready, and returns the local URL, port, and log path. On failure it stops partial work and raises ServeFailed.

**Call relations**: start_server, deploy_website, publish_website, and _redeploy_homepage call this whenever they need a live server. It calls the cleanup helpers, reads logs through _log_tail, and uses _serve_failed to create consistent failures.

*Call graph*: calls 6 internal fn (_free_log, _log_tail, _reset_server_task, _serve_failed, _stop_server, _stop_server_task); called by 4 (_redeploy_homepage, deploy_website, publish_website, start_server); 3 external calls (sha256, quote, shell_path).


##### `_site_media_type`  (lines 780–782)

```
def _site_media_type(path: str) -> str
```

**Purpose**: Guesses the web content type for a site file from its filename extension. This tells browsers whether a file is HTML, JavaScript, CSS, an image, and so on.

**Data flow**: It receives a path string, looks up the extension in a media-type table, falls back to a generic binary type, and adds UTF-8 charset information for text files.

**Call relations**: _promote_source calls this while building the stored source manifest for a static deployment.

*Call graph*: called by 1 (_promote_source).


##### `_source_listing`  (lines 785–806)

```
async def _source_listing(ctx: ToolContext, project: str) -> dict[str, dict[str, object]]
```

**Purpose**: Lists the files in a static site directory, including each file’s size and SHA-256 digest. A digest is a fingerprint used to verify that bytes are exactly what they should be.

**Data flow**: It receives a project path, runs a sandbox script that walks the directory while skipping caches and dependency folders, enforces file and total-size limits, parses the JSON result, and returns a file map.

**Call relations**: _served_directory calls this to inspect both ordinary static folders and built application output before source promotion.

*Call graph*: called by 1 (_served_directory); 1 external calls (loads).


##### `_promote_source`  (lines 809–850)

```
async def _promote_source(ctx: ToolContext, project: str, conversation_id: UUID, name: str, listing: dict[str, dict[str, object]]) -> str
```

**Purpose**: Stores the files of a static deployment as the site’s permanent source record. This lets the system later re-materialize or serve the exact deployed version.

**Data flow**: It receives the project path, conversation id, site name, and file listing. It creates a manifest with sizes, media types, and hashes, then uploads the files to blob storage either through presigned upload URLs or direct streams. It returns the manifest as JSON.

**Call relations**: deploy_website and _redeploy_homepage call this after deciding which directory should be hosted. It uses _site_media_type for each file and transfer for bulk upload when presigned URLs are available.

*Call graph*: calls 1 internal fn (_site_media_type); called by 2 (_redeploy_homepage, deploy_website); 4 external calls (__init__, __init__, transfer, uuid4).


##### `_pictured`  (lines 853–874)

```
async def _pictured(ctx: ToolContext, name: str, port: int, conversation_id: UUID) -> str | None
```

**Purpose**: Tries to capture preview images for a hosted site without turning preview failure into deployment failure. The site can still be live even if the picture could not be drawn.

**Data flow**: It receives the context, site name, port, and conversation id. It calls _illustrate, lets serious sandbox or terminal failures escape, and converts ordinary preview errors into a short preview_error string. If all goes well, it returns None.

**Call relations**: deploy_website, publish_website, and _redeploy_homepage call this after hosting or updating a site. It delegates the actual preview work to _illustrate.

*Call graph*: calls 1 internal fn (_illustrate); called by 3 (_redeploy_homepage, deploy_website, publish_website); 1 external calls (clipped).


##### `_illustrate`  (lines 877–897)

```
async def _illustrate(ctx: ToolContext, name: str, port: int, conversation_id: UUID) -> None
```

**Purpose**: Captures the visual preview and share-card image for a hosted site. These images are decorative records used by the portal and link previews.

**Data flow**: It receives the site name, port, and owning conversation id. It asks the platform to render a preview image, saves it on the site row if present, and then asks draw_from_page to create the share card.

**Call relations**: _pictured calls this after registration is complete. It uses _sites_registry to write preview data and draw_from_page for the share-card path.

*Call graph*: calls 2 internal fn (render_site_preview, _sites_registry); called by 1 (_pictured); 1 external calls (draw_from_page).


##### `_site_extension`  (lines 900–903)

```
def _site_extension(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Fetches the website extension context from the tool context. The extension context gives access to the workspace store and transaction needed for site records.

**Data flow**: It reads ctx.ext. If it is missing, it raises an internal error; otherwise it returns the extension context.

**Call relations**: _site_actor, _sites_registry, _redeploy_homepage, and set_homepage call this before touching site or agent storage.

*Call graph*: called by 4 (_redeploy_homepage, _site_actor, _sites_registry, set_homepage).


##### `_site_actor`  (lines 906–922)

```
def _site_actor(ctx: ToolContext, visibility: Visibility | None) -> tuple[ExtensionContext, UUID]
```

**Purpose**: Checks who is allowed to create or modify a hosted site record. It requires an acting member, and if visibility is being explicitly changed, it requires a live speaker.

**Data flow**: It receives the tool context and optional visibility choice. It reads the extension context and authority member id, checks speaker requirements, and returns the extension context plus creator member id.

**Call relations**: _refuse_before_serving and _host both call this, once before risky server changes and again at write time.

*Call graph*: calls 1 internal fn (_site_extension); called by 2 (_host, _refuse_before_serving); 2 external calls (__init__, authority_member_id).


##### `_refuse_before_serving`  (lines 925–959)

```
async def _refuse_before_serving(ctx: ToolContext, raw_name: str, port: int, visibility: Visibility | None) -> tuple[str, HostedSite | None]
```

**Purpose**: Checks whether a deploy or publish would be allowed before stopping the old server. This avoids taking a user’s current site down only to discover that the new action is forbidden.

**Data flow**: It receives the raw site name, target port, and optional visibility. It normalizes the name, validates that a URL can be formed, asks the hosted-sites store whether the write may proceed, and returns the normalized name plus any site that would be displaced.

**Call relations**: deploy_website and publish_website call this before building or serving. Later, _host repeats the authority check when it actually writes the hosted-site row.

*Call graph*: calls 1 internal fn (_site_actor); called by 2 (deploy_website, publish_website); 3 external calls (__init__, site_name, site_url).


##### `_host`  (lines 962–1000)

```
async def _host(ctx: ToolContext, raw_name: str, port: int, visibility: Visibility | None, manifest: str | None) -> dict[str, object]
```

**Purpose**: Registers a live sandbox port as a hosted site and returns the public-facing link information. This is the moment a reachable local server becomes a durable site URL.

**Data flow**: It receives the raw name, port, visibility, and optional source manifest. It checks the actor, normalizes the name, builds the site URL, registers the site in storage, computes effective visibility, and returns site metadata.

**Call relations**: deploy_website and publish_website call this after _serve has proven the server is reachable. It relies on _site_actor for permission checks and HostedSites for the actual registration.

*Call graph*: calls 1 internal fn (_site_actor); called by 2 (deploy_website, publish_website); 5 external calls (__init__, effective_visibility, site_object_name, site_name, site_url).


##### `_build_failed`  (lines 1003–1023)

```
def _build_failed(command: str, project: str, result: ExecResult) -> ToolFailure
```

**Purpose**: Creates a clear ToolFailure for an install or build command that failed before publishing. It distinguishes a timeout from a normal non-zero exit.

**Data flow**: It receives the command, project path, and command result. It packages the exit code, output streams, and timeout into command diagnostics and returns a ToolFailure saying nothing was served or hosted.

**Call relations**: publish_website calls this when an optional install command fails, then returns the failure result to the tool caller.

*Call graph*: called by 1 (publish_website); 2 external calls (__init__, __init__).


##### `start_server`  (lines 1026–1039)

```
async def start_server(ctx: ToolContext, args: StartServerInput) -> ToolResult
```

**Purpose**: Implements the start_server tool. It starts either a custom app server or a simple static file server for testing inside the sandbox, without registering a hosted public site.

**Data flow**: It receives validated input, chooses a port, turns paths into workspace-scoped paths, chooses a default command and log if needed, and calls _serve. It returns JSON with the sandbox-local URL, port, log, and project path, or a failure result.

**Call relations**: This is a tool handler exposed through SITES_TOOLS. It delegates the hard server lifecycle work to _serve and formats success through _json_result.

*Call graph*: calls 2 internal fn (_json_result, _serve); 1 external calls (workspace_path).


##### `_unhosted`  (lines 1042–1050)

```
def _unhosted(displaced: HostedSite | None, conversation_id: UUID) -> dict[str, object]
```

**Purpose**: Describes the site that was displaced by a deployment, if any. This lets the final tool response warn that another site name on the same port was taken down.

**Data flow**: It receives an optional displaced HostedSite and the conversation id. If there is no displaced site, it returns an empty dictionary. Otherwise it returns the object name of the unhosted site.

**Call relations**: deploy_website and publish_website merge this into their JSON result after hosting the new site.

*Call graph*: called by 2 (deploy_website, publish_website); 1 external calls (site_object_name).


##### `deploy_website`  (lines 1053–1081)

```
async def deploy_website(ctx: ToolContext, args: DeployWebsiteInput) -> ToolResult
```

**Purpose**: Implements the deploy_website tool for static sites. It validates, possibly builds, stores the source, starts a static server, registers a stable hosted link, and captures a preview.

**Data flow**: It receives a project path, site name, entry point, and optional visibility. It resolves the source directory, handles the special homepage redeploy case, checks permission before serving, gets the directory to serve, promotes source bytes, starts an HTTP server, registers the hosted site, and returns JSON with URLs and metadata.

**Call relations**: This tool handler coordinates _refuse_before_serving, _served_directory, _promote_source, _serve, _host, _pictured, and _unhosted. If the requested name matches a bound homepage from another conversation, it hands off to _redeploy_homepage.

*Call graph*: calls 10 internal fn (_host, _json_result, _pictured, _promote_source, _redeploy_homepage, _refuse_before_serving, _serve, _served_directory, _sites_registry, _unhosted); 4 external calls (serve_port, workspace_path, site_object_name, site_name).


##### `_verdict`  (lines 1084–1085)

```
def _verdict(code: AuditIssueCode, message: str) -> ApplicationAuditVerdict
```

**Purpose**: Creates a one-issue application audit verdict. It is a small helper for turning a single validation, build, or audit problem into the standard refusal shape.

**Data flow**: It receives an issue code and message. It creates an ApplicationAuditIssue, wraps it in an ApplicationAuditVerdict, and returns that verdict.

**Call relations**: ApplicationPageGate uses this when design validation, source validation, build, or audit failure should refuse hosting with a repairable explanation.

*Call graph*: called by 4 (_build, _design, _gate_source, _refuse_or_raise); 2 external calls (__init__, __init__).


##### `_last_words`  (lines 1088–1099)

```
def _last_words(output: str) -> str
```

**Purpose**: Keeps the end of a long command output, because fatal errors usually appear last. This helps show the builder the real failure instead of earlier warnings.

**Data flow**: It receives an output string, trims surrounding whitespace, and if it is too long, returns only the final allowed characters with a leading ellipsis.

**Call relations**: ApplicationPageGate._build, ApplicationPageGate._audit, and ApplicationPageGate._refuse_or_raise call this when turning command output into a refusal or runtime error.

*Call graph*: called by 3 (_audit, _build, _refuse_or_raise).


##### `ApplicationPageRefused.__init__`  (lines 1114–1119)

```
def __init__(self, verdict: ApplicationAuditVerdict) -> None
```

**Purpose**: Creates a repairable refusal for an application page that cannot be hosted yet. The exception message is written as a checklist the builder can act on.

**Data flow**: It receives an ApplicationAuditVerdict, formats each issue as a bullet under a fixed heading, stores the verdict, and initializes the runtime error.

**Call relations**: ApplicationPageGate raises this from design, source, build, audit, and lifecycle checks whenever the problem belongs to the page rather than the infrastructure.

*Call graph*: called by 5 (_audit, _build, _design, _gate_source, _refuse_or_raise).


##### `ApplicationPageGate.built_page`  (lines 1138–1152)

```
async def built_page(self) -> str
```

**Purpose**: Runs the full gate for a UFO application page and returns the built output directory if it passes. It is the bridge between editable app source and deployable static files.

**Data flow**: It reads any design file, validates source against the rules, builds the page, optionally audits the rendered page against the design, and returns the dist directory path.

**Call relations**: _served_directory calls this when it detects an app source file. This method coordinates _design, _gate_source, _build, and _audit.

*Call graph*: calls 4 internal fn (_audit, _build, _design, _gate_source).


##### `ApplicationPageGate._read`  (lines 1154–1162)

```
async def _read(self, name: str, maximum: int) -> str | None
```

**Purpose**: Reads a named project file from the sandbox with a maximum size. It cleanly distinguishes a missing optional file from a read failure.

**Data flow**: It receives a filename and size limit. It runs a sandbox reader script, returns None if the file is absent, returns the text if successful, or raises an error if the read failed.

**Call relations**: ApplicationPageGate._design uses this for the optional design file, and ApplicationPageGate._gate_source uses it for the required source file.

*Call graph*: called by 2 (_design, _gate_source).


##### `ApplicationPageGate._design`  (lines 1164–1171)

```
async def _design(self) -> ApplicationDesign | None
```

**Purpose**: Loads and validates the optional design description for an application page. A valid design gives the later audit something concrete to compare the rendered page against.

**Data flow**: It reads the design file if present. If absent, it returns None. If present, it validates the design text and returns an ApplicationDesign, or raises ApplicationPageRefused with a design verdict.

**Call relations**: ApplicationPageGate.built_page calls this first. It uses _read for file access and _verdict plus ApplicationPageRefused for repairable design errors.

*Call graph*: calls 3 internal fn (_read, __init__, _verdict); called by 1 (built_page); 1 external calls (validate_application_design).


##### `ApplicationPageGate._gate_source`  (lines 1173–1180)

```
async def _gate_source(self, design: ApplicationDesign | None) -> None
```

**Purpose**: Validates the application source before paying the cost of a build. It catches source that does not follow the page kit rules or does not match the design.

**Data flow**: It reads the required source file. If missing, it raises an internal read error. If validation fails, it raises ApplicationPageRefused with a source verdict. Otherwise it returns nothing.

**Call relations**: ApplicationPageGate.built_page calls this after reading the design. It uses _read and the application audit validation helpers.

*Call graph*: calls 3 internal fn (_read, __init__, _verdict); called by 1 (built_page); 1 external calls (validate_application_source).


##### `ApplicationPageGate._build`  (lines 1182–1197)

```
async def _build(self) -> None
```

**Purpose**: Builds the application page into static files using the expected page kit setup. Browsers cannot directly serve the editable TSX source, so this creates the deployable output.

**Data flow**: It writes project config and preview files, unpacks the page kit, runs the Vite build command in the sandbox, and raises ApplicationPageRefused if the build fails.

**Call relations**: ApplicationPageGate.built_page calls this after source validation. It uses _last_words and _verdict to turn build output into a useful repair message.

*Call graph*: calls 3 internal fn (__init__, _last_words, _verdict); called by 1 (built_page); 2 external calls (quote, unpack_page_kit).


##### `ApplicationPageGate._audit`  (lines 1199–1231)

```
async def _audit(self, design: ApplicationDesign | None) -> None
```

**Purpose**: Runs a browser-based audit on the built application page. This checks that the page becomes ready and, when a design exists, that the rendered layout satisfies the design.

**Data flow**: It writes the audit script into runtime storage, runs it with paths for reports and screenshots, handles non-zero exits through _refuse_or_raise, reads the JSON report, validates it, and raises ApplicationPageRefused if the audit verdict fails.

**Call relations**: ApplicationPageGate.built_page calls this only when there is a design. It hands failed audit runs to _refuse_or_raise and uses audit_application for the final report verdict.

*Call graph*: calls 3 internal fn (_refuse_or_raise, __init__, _last_words); called by 1 (built_page); 2 external calls (model_validate_json, audit_application).


##### `ApplicationPageGate._refuse_or_raise`  (lines 1233–1254)

```
async def _refuse_or_raise(self, run: ExecResult, report_path: str) -> None
```

**Purpose**: Decides whether a failed audit run is a page problem the builder can fix or an infrastructure problem that should surface as an error. This prevents telling the builder to edit code when the browser or sandbox is broken.

**Data flow**: It receives the audit command result and report path. It examines the exit code, converts design and lifecycle failures into ApplicationPageRefused, reads lifecycle details when needed, and raises RuntimeError for non-repairable audit failures.

**Call relations**: ApplicationPageGate._audit calls this when the audit process exits non-zero. It uses _lifecycle_reason for readiness failures and _last_words for command output.

*Call graph*: calls 4 internal fn (_lifecycle_reason, __init__, _last_words, _verdict); called by 1 (_audit).


##### `ApplicationPageGate._lifecycle_reason`  (lines 1256–1266)

```
async def _lifecycle_reason(self, report_path: str) -> str
```

**Purpose**: Reads the audit’s lifecycle diagnostic and turns it into a concise repair reason. This explains why the page never became ready.

**Data flow**: It receives the main report path, reads the companion lifecycle diagnostic JSON with a size limit, validates it, summarizes the snapshot, combines problems, and returns a message. If the diagnostic cannot be read, it returns an empty string.

**Call relations**: ApplicationPageGate._refuse_or_raise calls this for lifecycle exit codes. It uses _lifecycle_state and _lifecycle_message to produce the final text.

*Call graph*: calls 2 internal fn (_lifecycle_message, _lifecycle_state); called by 1 (_refuse_or_raise).


##### `_served_directory`  (lines 1269–1291)

```
async def _served_directory(ctx: ToolContext, project: str) -> tuple[str, dict[str, dict[str, object]]]
```

**Purpose**: Decides what directory should actually be served for a static deployment. A plain static folder is served as-is, while an app source folder is built and audited first.

**Data flow**: It receives a project path, lists its files, and checks whether the special app source file is present. If not, it returns the original project and listing. If present, it runs ApplicationPageGate, then lists and returns the built output directory.

**Call relations**: deploy_website and _redeploy_homepage call this before source promotion and serving. It uses _source_listing and ApplicationPageGate.

*Call graph*: calls 1 internal fn (_source_listing); called by 2 (_redeploy_homepage, deploy_website); 1 external calls (__init__).


##### `_sites_registry`  (lines 1294–1296)

```
def _sites_registry(ctx: ToolContext) -> HostedSites
```

**Purpose**: Creates a HostedSites store helper for the current workspace and transaction. This is the file’s common doorway to hosted-site records.

**Data flow**: It reads the extension context, pulls out the workspace id and transaction, and returns a HostedSites instance.

**Call relations**: deploy_website, _illustrate, and _redeploy_homepage call this when they need to read or update hosted-site storage.

*Call graph*: calls 1 internal fn (_site_extension); called by 3 (_illustrate, _redeploy_homepage, deploy_website); 1 external calls (__init__).


##### `_redeploy_homepage`  (lines 1299–1359)

```
async def _redeploy_homepage(ctx: ToolContext, args: DeployWebsiteInput, bound: HostedSite, scratch_port: int) -> ToolResult
```

**Purpose**: Updates an agent’s already-bound homepage in place from a new build, without changing the public homepage link. This supports editing a homepage from another conversation while preserving its identity.

**Data flow**: It checks that a live speaker requested the change and that visibility is not being overridden. It validates possible displacement on the scratch port, builds or selects the served directory, promotes source under the original homepage row, starts the server, updates the existing site row, unregisters any displaced scratch-port site, captures a preview, and returns JSON.

**Call relations**: deploy_website calls this for the special case where the requested site name matches the acting agent’s bound homepage from another conversation. It coordinates _served_directory, _promote_source, _serve, _pictured, _sites_registry, _site_extension, and _json_result.

*Call graph*: calls 8 internal fn (agent_visibility, _json_result, _pictured, _promote_source, _serve, _served_directory, _site_extension, _sites_registry); called by 1 (deploy_website); 5 external calls (__init__, authority_member_id, workspace_path, site_object_name, site_url).


##### `publish_website`  (lines 1362–1388)

```
async def publish_website(ctx: ToolContext, args: PublishWebsiteInput) -> ToolResult
```

**Purpose**: Implements the publish_website tool for dynamic web apps that run their own server. Unlike deploy_website, it does not store static source as the site’s served record.

**Data flow**: It receives the app project path, app name, visibility, run command, and optional install command. It checks permission before serving, optionally runs installation, starts the app server with _serve, registers the hosted site without a manifest, captures a preview, and returns JSON.

**Call relations**: This tool handler uses _refuse_before_serving before any port is killed, _build_failed for install errors, _serve for the running app, _host for registration, _pictured for preview, _unhosted for displacement reporting, and _json_result for success.

*Call graph*: calls 7 internal fn (_build_failed, _host, _json_result, _pictured, _refuse_before_serving, _serve, _unhosted); 3 external calls (quote, serve_port, workspace_path).


##### `set_homepage`  (lines 1391–1448)

```
async def set_homepage(ctx: ToolContext, args: SetHomepageInput) -> ToolResult
```

**Purpose**: Implements the set_homepage tool, which binds an existing hosted site as the homepage for the targeted agent. The homepage then follows the agent’s visibility rules rather than the site’s own visibility.

**Data flow**: It reads the target agent, checks that the caller may alter that agent, finds the named hosted site, verifies the caller is the site creator, requires a live speaker unless the site was just deployed in this turn, writes the homepage binding, and returns the site URL, effective visibility, and agent id.

**Call relations**: This is a tool handler exposed through SITES_TOOLS. It uses _site_extension for storage access, HostedSites for site lookup and binding, speaker/admin checks from ToolContext, and _json_result for the final response.

*Call graph*: calls 3 internal fn (speaker_is_admin, _json_result, _site_extension); 5 external calls (__init__, __init__, authority_member_id, site_object_name, site_url).


### `extensions/sites/ufo_ext_sites/application_audit.py`

`domain_logic` · `deploy-time validation and product audit`

This file acts like a strict building inspector for interactive application pages. Without it, a generated page could ship with unreadable text, broken layouts, missing required facts, unsafe imports, hidden browser errors, or a design that does not match what was promised.

The file defines typed records for audit inputs and outputs: browser views, contrast problems, overlapping elements, clickable controls, design regions, final verdicts, and repair feedback. These records use validation rules so bad audit data is rejected early.

The main audit path starts with browser measurements in an ApplicationAuditReport. The audit checks that all required views exist: light and dark mode, desktop and narrow phone width. It then looks for empty pages, weak text contrast, horizontal overflow, clipped content, accidental overlaps, console errors, too few accessible controls, too few proven interactions, missing required facts, and facts that appear too low on the desktop page.

A second set of rules validates the page before it is even built. The source must import only allowed things, mount into the expected root element, directly use approved UFO kit components, avoid unsafe or off-theme styling, and mark any designed regions. A matching SVG design is also checked for safe, simple drawing content, valid size, named regions, and declared kit components. Together, these checks make deployment deterministic: the same inputs lead to the same pass or repair list.

#### Function details

##### `AcceptedApplicationDesignEvidence.regions_are_unique`  (lines 152–158)

```
def regions_are_unique(self) -> 'AcceptedApplicationDesignEvidence'
```

**Purpose**: This model check makes sure accepted design evidence does not contain duplicate region names or duplicate kit component names. It keeps later comparisons simple and trustworthy because each named area or component means exactly one thing.

**Data flow**: It reads the regions and kit_components already placed in the evidence object. It extracts their names, compares each list with its unique version, and either returns the same object unchanged or raises an error explaining the duplicate problem.

**Call relations**: This runs automatically when AcceptedApplicationDesignEvidence is created. It does not hand work to other project functions; it protects any later deploy step that relies on the evidence being unambiguous.


##### `ApplicationAuditReport.views_are_unique`  (lines 223–227)

```
def views_are_unique(self) -> 'ApplicationAuditReport'
```

**Purpose**: This model check makes sure the browser report has at most one result for each colour scheme and screen width pair. That prevents two competing measurements from claiming to describe the same view.

**Data flow**: It reads the report's views, turns each one into a key made from its scheme and width, and checks for duplicates. If all keys are unique it returns the report; otherwise it raises an error.

**Call relations**: This runs automatically when an ApplicationAuditReport is created. The main audit later builds a lookup table from these same scheme-and-width pairs, so this check prevents confusing overwrite behavior.


##### `ApplicationAuditVerdict.passed`  (lines 248–251)

```
def passed(self) -> bool
```

**Purpose**: This property answers the simple question: did the audit pass? It is true only when there are no repair issues.

**Data flow**: It reads the verdict's issues tuple. If the tuple is empty it returns true; if any issue exists it returns false. It changes nothing.

**Call relations**: Callers can use this after audit_application returns a verdict. It is the friendly yes-or-no wrapper around the detailed issue list.


##### `_needed_ratio`  (lines 308–313)

```
def _needed_ratio(item: ApplicationAuditText) -> float
```

**Purpose**: This helper decides how much colour contrast a piece of text needs. Larger text is allowed a lower contrast threshold, while small body text needs a stricter one; special quiet kit text has its own floor.

**Data flow**: It receives one measured text item, reads its slot, pixel size, and font weight, and chooses the required contrast ratio. It returns that number without changing anything.

**Call relations**: _contrast_failures calls this for each measured text style. It supplies the standard that the measured contrast ratio is compared against.

*Call graph*: called by 1 (_contrast_failures).


##### `_issue`  (lines 316–323)

```
def _issue(code: AuditIssueCode, message: str, terms: tuple[str, ...]=()) -> ApplicationAuditIssue
```

**Purpose**: This helper creates one bounded audit issue with a fixed code, a message, and optional search terms. It trims long messages and long term lists so feedback stays small and predictable.

**Data flow**: It receives an issue code, a human repair message, and optional terms. It cuts the message to the maximum allowed length, keeps only the first ten terms, and returns an ApplicationAuditIssue.

**Call relations**: audit_application uses this whenever it finds a problem. The helper centralizes the shape and size limits for all issues before they are placed into the final verdict.

*Call graph*: called by 1 (audit_application); 1 external calls (__init__).


##### `application_first_screen_scale`  (lines 326–336)

```
def application_first_screen_scale(page_height: int) -> float
```

**Purpose**: This helper converts the fixed first-screen height into a fraction of the whole measured page. It lets region size and spacing checks stay fair even when the full page is taller than the first visible screen.

**Data flow**: It receives a page height in pixels. It divides the fixed fold height by that page height and returns the resulting scale factor.

**Call relations**: application_region_relation and application_design_region_size_failure call this when they compare region positions and sizes. It keeps their thresholds tied to real pixels rather than accidentally growing with page length.

*Call graph*: called by 2 (application_design_region_size_failure, application_region_relation).


##### `application_region_relation`  (lines 339–359)

```
def application_region_relation(first: ApplicationAuditRegion, second: ApplicationAuditRegion, page_height: int=APPLICATION_DESIGN_FOLD) -> tuple[Literal['horizontal', 'vertical'], int] | None
```

**Purpose**: This function decides whether two named regions are separated vertically or horizontally, and in which order. If they overlap or are too close to separate cleanly, it returns no relation.

**Data flow**: It receives two region boxes and the page height those box fractions belong to. It computes a small allowance, checks whether one box is above, below, left, or right of the other, and returns the axis plus order, or returns null when no clean separation exists.

**Call relations**: application_design_fidelity uses this first on the design regions to learn the expected layout, then on rendered app regions to see if the app kept the same order. It relies on application_first_screen_scale for vertical tolerance.

*Call graph*: calls 1 internal fn (application_first_screen_scale); called by 1 (application_design_fidelity).


##### `application_design_region_size_failure`  (lines 362–380)

```
def application_design_region_size_failure(regions: tuple[ApplicationAuditRegion, ...], page_height: int=APPLICATION_DESIGN_FOLD) -> str | None
```

**Purpose**: This function finds the first design region that is too small to count as a meaningful screen area. It prevents tiny marks from being used as fake required regions.

**Data flow**: It receives a tuple of design regions and a page height. For each region it compares width, height, and area against minimum useful sizes, scaled to the first screen, then returns a repair message for the first failure or null if all regions are large enough.

**Call relations**: application_design_fidelity calls this before doing deeper design comparisons. It uses application_first_screen_scale so the minimums remain consistent across accepted design heights.

*Call graph*: calls 1 internal fn (application_first_screen_scale); called by 1 (application_design_fidelity).


##### `application_design_region_fold_failure`  (lines 383–405)

```
def application_design_region_fold_failure(regions: tuple[ApplicationAuditRegion, ...], page_height: int=APPLICATION_DESIGN_FOLD) -> str | None
```

**Purpose**: This function checks whether a design region visibly crosses the first-screen boundary. That matters because the design rules expect each region to live clearly above or below the fold, not straddle it.

**Data flow**: It receives design regions and a page height. It converts each region's fractional top and bottom into pixel rows, allows a tiny slop around the fold line, and returns a message for the first region that paints across both sides; otherwise it returns null.

**Call relations**: No caller is shown in the provided graph, but it is a focused design-rule helper. It is ready to be used wherever the deploy path wants to reject regions that blur the boundary between first-screen and below-fold content.


##### `application_design_fidelity`  (lines 408–497)

```
def application_design_fidelity(report: ApplicationAuditReport) -> ApplicationDesignFidelity
```

**Purpose**: This function scores how closely the rendered narrow-page app matches its accepted design regions. It checks names, visibility above the fold, useful region sizes, lack of design overlap, and preserved region order.

**Data flow**: It receives a full audit report, reads the design regions and the narrow 360-pixel light and dark browser views, and compares the intended layout with what the browser measured. It returns an ApplicationDesignFidelity object containing passed checks, total checks, and failure messages.

**Call relations**: audit_application calls this as part of the overall verdict. Inside, it uses application_design_region_size_failure and application_region_relation to turn geometry into clear pass-or-fail design feedback.

*Call graph*: calls 2 internal fn (application_design_region_size_failure, application_region_relation); called by 1 (audit_application); 1 external calls (__init__).


##### `_contrast_failures`  (lines 500–515)

```
def _contrast_failures(views: tuple[ApplicationAuditView, ...]) -> list[str]
```

**Purpose**: This helper lists all measured text styles whose colour contrast is too low. It turns raw contrast measurements into repair-ready sentences.

**Data flow**: It receives the measured browser views. For each text item, it asks _needed_ratio what the required contrast is, compares that with the measured ratio, and gathers readable failure messages for items that fall short.

**Call relations**: audit_application calls this after it has selected the measured views. The returned messages are folded into one contrast issue for the final verdict.

*Call graph*: calls 1 internal fn (_needed_ratio); called by 1 (audit_application).


##### `audit_application`  (lines 518–645)

```
def audit_application(report: ApplicationAuditReport, contract: ApplicationAuditContract | None=None) -> ApplicationAuditVerdict
```

**Purpose**: This is the main product audit for a built application page. It turns a browser report, and optionally a required fact contract, into a final pass-or-repair verdict.

**Data flow**: It receives an ApplicationAuditReport and optional ApplicationAuditContract. It checks required views, text presence, contrast, overflow, clipping, overlap, design fidelity, console errors, accessible controls, interaction proof, required facts, and above-fold fact placement. It returns an ApplicationAuditVerdict containing up to the maximum number of issues.

**Call relations**: This is the central checker used by deployment. It calls _contrast_failures for readability checks, application_design_fidelity for design matching, and _issue each time it needs to add a bounded repair item before constructing the final verdict.

*Call graph*: calls 3 internal fn (_contrast_failures, _issue, application_design_fidelity); 1 external calls (__init__).


##### `_local_source_bindings`  (lines 691–708)

```
def _local_source_bindings(code: str) -> set[str]
```

**Purpose**: This helper finds names that are defined locally inside the app source code. It is used to avoid mistaking a locally defined React component for a kit component imported from ufo/kit.

**Data flow**: It receives source code with comments and string literals already removable by the caller. It searches for class, function, variable, destructured, and function-parameter names, then returns a set of local bindings.

**Call relations**: _rendered_application_components calls this while deciding which JSX tags are truly imported kit components. It also uses regular expression matching to collect the names.

*Call graph*: called by 1 (_rendered_application_components); 1 external calls (findall).


##### `_validate_application_imports`  (lines 848–864)

```
def _validate_application_imports(source: str) -> None
```

**Purpose**: This function enforces the import rules for app.tsx. The page must use ufo/kit, may only import very limited sibling assets, and must not export its own declarations.

**Data flow**: It receives the source text, scans import and export statements, and raises a repair-style ValueError if the file imports from the wrong place, uses side-effect or non-named imports where forbidden, exports declarations, or refers to the old UfoAppKit name. If everything is allowed, it returns nothing.

**Call relations**: validate_application_source calls this first. It acts as the gate that keeps the page inside the supported runtime and build environment before any component or styling checks run.

*Call graph*: called by 1 (validate_application_source).


##### `_rendered_application_components`  (lines 867–894)

```
def _rendered_application_components(source: str) -> set[str]
```

**Purpose**: This function proves that the app actually renders at least one approved visual component from ufo/kit. It also verifies that the app mounts into the expected root element.

**Data flow**: It receives source text. It checks for the expected mountApp call, reads named imports from ufo/kit, removes comments and literals, subtracts locally declared names from JSX component tags, and returns the set of imported kit components that are truly rendered. It raises an error if none are rendered or the mount is wrong.

**Call relations**: validate_application_source calls this after import validation. It calls _local_source_bindings so local code does not get confused with kit-provided UI.

*Call graph*: calls 1 internal fn (_local_source_bindings); called by 1 (validate_application_source).


##### `_validate_designed_components`  (lines 897–908)

```
def _validate_designed_components(rendered_kit_components: set[str], designed_kit_components: tuple[str, ...]) -> None
```

**Purpose**: This function checks that the source directly renders every kit component promised by the design. It prevents an app from claiming a design component and then omitting it in code.

**Data flow**: It receives the set of kit components found in the source and the tuple of kit components named by the design. It compares them and raises a repair message naming any missing component; otherwise it returns nothing.

**Call relations**: validate_application_source calls this only when a design is supplied. It connects the source-code audit with the earlier design validation.

*Call graph*: called by 1 (validate_application_source).


##### `_validate_designed_regions`  (lines 911–918)

```
def _validate_designed_regions(source: str, designed_regions: tuple[str, ...]) -> None
```

**Purpose**: This function checks that every region named in the design is marked in the source with data-app-region. These markers let the browser audit compare the intended layout with the rendered page.

**Data flow**: It receives the source text and the tuple of designed region names. It scans for data-app-region markers, finds any designed names that are missing, and raises a repair message if needed. If all are present, it returns nothing.

**Call relations**: validate_application_source calls this when design information is available. It makes the later browser fidelity check possible because the rendered page exposes the same region names as the SVG.

*Call graph*: called by 1 (validate_application_source).


##### `_validate_application_styling`  (lines 921–951)

```
def _validate_application_styling(source: str) -> None
```

**Purpose**: This function enforces the styling rules that keep app pages consistent with the UFO kit theme. It rejects raw CSS values, reserved attributes, style tags, unsupported spacing choices, and a few specific patterns known to break the design system.

**Data flow**: It receives the source text, searches for forbidden patterns, and raises a repair-style ValueError for the first problem it finds. If no styling rule is broken, it returns nothing.

**Call relations**: validate_application_source calls this after imports, rendering, and optional design checks. It is the final source-code filter before the page is considered worth building.

*Call graph*: called by 1 (validate_application_source).


##### `validate_application_source`  (lines 954–966)

```
def validate_application_source(source: str, design: ApplicationDesign | None=None) -> None
```

**Purpose**: This is the public source-code validator for an application page. It raises clear repair instructions before the system spends time building or browser-testing a page that breaks known rules.

**Data flow**: It receives the app source text and optionally a validated ApplicationDesign. It checks imports, discovers rendered kit components, checks design component and region promises when a design exists, and then checks styling. It returns nothing on success and raises an error on the first violation.

**Call relations**: This function orchestrates the source validation helpers: _validate_application_imports, _rendered_application_components, _validate_designed_components, _validate_designed_regions, and _validate_application_styling. It is the source-side companion to the browser audit.

*Call graph*: calls 5 internal fn (_rendered_application_components, _validate_application_imports, _validate_application_styling, _validate_designed_components, _validate_designed_regions).


##### `_parse_application_design`  (lines 969–998)

```
def _parse_application_design(source: str) -> tuple[ElementTree.Element, tuple[float, ...]]
```

**Purpose**: This helper parses the SVG design and checks its outer size rules. It ensures the design is a safe SVG with the exact narrow-lane width and an accepted integer height.

**Data flow**: It receives the SVG source as text. It rejects XML entity declarations, parses the XML, confirms the root is svg, reads and validates the viewBox, width, and height, then returns the root XML element and parsed viewBox numbers. It raises clear errors for invalid design files.

**Call relations**: validate_application_design calls this before inspecting individual SVG elements. It uses XML parsing plus numeric checks so later validation can work with a trusted SVG structure.

*Call graph*: called by 1 (validate_application_design); 3 external calls (isfinite, split, fromstring).


##### `_visible_design_element`  (lines 1001–1027)

```
def _visible_design_element(element: ElementTree.Element, tag: str, attributes: dict[str, str]) -> bool
```

**Purpose**: This helper decides whether a supported SVG drawing element actually draws something visible. For example, a rectangle with zero width is not counted as real drawing content.

**Data flow**: It receives an XML element, its tag name, and simplified attributes. Based on the element type, it checks the attributes or text content that make it visible, and returns true or false.

**Call relations**: _validate_design_element calls this after deciding an element is of a drawing type. The result contributes to the final count of real drawing elements in validate_application_design.

*Call graph*: called by 1 (_validate_design_element); 1 external calls (itertext).


##### `_validate_design_attributes`  (lines 1030–1037)

```
def _validate_design_attributes(element: ElementTree.Element) -> None
```

**Purpose**: This helper rejects SVG attributes that could make a design active or external, such as event handlers, JavaScript links, data URLs, or web URLs. The design must be a static drawing, not a hidden program or network fetch.

**Data flow**: It receives one SVG element, loops through its attributes, and checks each attribute name and value. It raises an error if it finds active or external content; otherwise it returns nothing.

**Call relations**: _validate_design_element calls this for every SVG element it inspects. It is the safety check that runs alongside the visual structure checks.

*Call graph*: called by 1 (_validate_design_element).


##### `_validate_design_element`  (lines 1040–1093)

```
def _validate_design_element(element: ElementTree.Element, ids: set[str], regions: list[ElementTree.Element], kit_components: list[str]) -> bool
```

**Purpose**: This function validates one SVG element against the design contract. It rejects unsafe effects and active content, records named design regions and kit components, checks ID uniqueness, and reports whether the element is visible drawing content.

**Data flow**: It receives an SVG element plus shared collections for seen IDs, region elements, and kit component names. It reads the tag and attributes, may add region or component information to those collections, raises errors for invalid content, and returns true if the element is a visible drawing element.

**Call relations**: validate_application_design calls this for every element in the SVG tree. It uses _validate_design_attributes for safety and _visible_design_element to decide whether the element counts as actual drawing.

*Call graph*: calls 2 internal fn (_validate_design_attributes, _visible_design_element); called by 1 (validate_application_design); 1 external calls (itertext).


##### `validate_application_design`  (lines 1096–1123)

```
def validate_application_design(source: str) -> ApplicationDesign
```

**Purpose**: This is the public validator for an application SVG design. It proves that the design is a safe, static, correctly sized SVG with enough unique named regions and at least one recognized kit component.

**Data flow**: It receives SVG source text. It parses the design, walks every SVG element through validation, checks that drawing content exists, verifies the number and uniqueness of regions, rejects nested regions, requires at least one kit component marker, and returns an ApplicationDesign with region names, kit component names, and height.

**Call relations**: This function coordinates design validation by calling _parse_application_design and _validate_design_element. The ApplicationDesign it returns can later be passed into validate_application_source so the source code is checked against the design promises.

*Call graph*: calls 2 internal fn (_parse_application_design, _validate_design_element); 1 external calls (__init__).


### `extensions/sites/ufo_ext_sites/source.py`

`io_transport` · `site source materialization and app page build preparation`

A site is not edited directly inside the permanent blob store. Instead, when someone needs to inspect or change it, its source files are copied into a sandbox, which is a safe temporary workspace. This file is the bridge that moves those bytes.

The main job is `materialize_source`: it reads a stored manifest, which is a list of source files and their sizes, then recreates those files under a `sites/` folder in the sandbox. It first checks a small “generation stamp.” This is like a label saying “this folder already matches deploy number 42.” If the stamp is current, it avoids downloading everything again and leaves any in-progress work alone.

Before downloading, it carefully clears and “claims” the destination files through a containment guard. That guard makes sure paths cannot escape the workspace, even through symbolic links, which are shortcuts that can point somewhere unexpected. This matters because blindly writing files could overwrite things outside the project.

The file supports two kinds of storage. In production-style storage, it asks for temporary download links and has the sandbox fetch files itself with `curl`. In a local filesystem-backed store, it streams bytes through this process instead. It also packages and unpacks the page SDK kit as one compressed archive, so many small files can be installed with one write and one safe unpack step.

#### Function details

##### `_page_kit_archive`  (lines 125–137)

```
def _page_kit_archive() -> bytes
```

**Purpose**: Builds one compressed archive containing the page SDK kit files that app pages need while building. This avoids copying many individual files into the sandbox one by one.

**Data flow**: It starts with the kit directory that lives beside this file. It walks through that directory, adds each regular file into an in-memory gzip tar archive, and names the files under the `sdk/` folder inside the archive. It returns the archive as raw bytes, ready to write into a sandbox.

**Call relations**: This runs when the module is loaded so `PAGE_KIT_ARCHIVE` is ready for later use. It relies on `BytesIO` as an in-memory file and `tarfile.open` to create the compressed archive. Later, `unpack_page_kit` writes these prepared bytes into the sandbox instead of rebuilding the archive each time.

*Call graph*: 2 external calls (BytesIO, open).


##### `transfer`  (lines 145–155)

```
async def transfer(ctx: ToolContext, script: str, pairs: list[tuple[str, str]], total_bytes: int) -> None
```

**Purpose**: Runs a sandbox-side upload or download script for a list of file-and-URL pairs. It keeps each batch small and sets a timeout based on how much data is expected to move.

**Data flow**: It receives a tool context, a shell script, pairs such as local path plus temporary URL, and the total number of bytes. It splits the pairs into batches, asks the sandbox to run the script with those arguments, and watches the result. If the sandbox command fails, it turns the command's error text into a Python exception; otherwise it returns nothing after all batches finish.

**Call relations**: `materialize_source` calls this when the blob store can provide temporary download links. In that flow, `materialize_source` prepares the destination paths and URLs, then hands them to `transfer`, which performs the actual sandbox-side `curl` work.

*Call graph*: called by 1 (materialize_source).


##### `materialize_source`  (lines 158–224)

```
async def materialize_source(ctx: ToolContext, site: HostedSite, object_name: str) -> tuple[str, list[str]]
```

**Purpose**: Recreates a site's stored source tree inside the current sandbox and returns where it was placed. This lets a read, edit, or rebuild start from the latest deployed source rather than from a stale local copy.

**Data flow**: It takes a tool context, a hosted site record, and the sandbox object name to use. It reads the site's source manifest, chooses a destination under the workspace, and compares a generation stamp with the site's deploy generation. If the stamp already matches, it returns the existing directory and file list. Otherwise it safely empties and prepares the destination, downloads or streams every manifest file into place, writes the new generation stamp, and returns the destination plus the relative paths written.

**Call relations**: This is the main entry point in this file for bringing stored source into the sandbox. It validates the manifest with `SourceManifest.model_validate_json`, builds safe workspace paths with `workspace_path`, quotes the stamp path with `shlex.quote` for a shell read, and calls `transfer` when files can be fetched through temporary URLs. If the storage backend does not support those URLs, it falls back to reading each blob through the tool context and writing it into the sandbox.

*Call graph*: calls 1 internal fn (transfer); 3 external calls (model_validate_json, quote, workspace_path).


##### `unpack_page_kit`  (lines 227–245)

```
async def unpack_page_kit(ctx: ToolContext, dest: str, runtime_root: str | None=None) -> None
```

**Purpose**: Installs the page SDK kit into a project directory in the sandbox. App pages import this kit during their build, so this step makes sure they build against the current extension-provided components.

**Data flow**: It receives a tool context, a destination directory, and optionally a runtime root that changes which sandbox root should be treated as safe. It writes the prepared kit archive into the destination, either as a normal sandbox file or as a runtime path. Then it runs a small Python unpack program in the sandbox, which extracts only regular files through the containment guard and deletes the archive afterward. If unpacking fails, it raises an exception with the sandbox's error message.

**Call relations**: This function is used by higher-level page build preparation code when an app page project needs its `sdk/` folder. It does not call the Python helper directly in-process; instead it sends `UNPACK_KIT_PROG` to the sandbox so the safety checks happen where the files are being written.


### `extensions/sites/ufo_ext_sites/store.py`

`domain_logic` · `site deploy, site lookup, visibility changes, homepage binding, and preview/share-card updates`

A hosted site here is identified by a workspace, a conversation, and a short name. That matters because two different conversations can both have a site called “dashboard” without stealing each other’s links. This file defines the database row for that registry and the rules for changing it.

The main class, HostedSites, is a small service around a database transaction. It always filters by workspace, because the database connection itself can see the whole database. Think of it like a receptionist who must check the building name before looking up any room number.

When a deploy happens, register records the site name, the port serving it, the creator, visibility, preview image details, share-card details, and, for static sites, a manifest describing the files saved in blob storage. It also protects people from accidental damage: a deploy cannot silently take over another member’s site, cannot change another creator’s visibility choice, and cannot replace an application homepage under a new name.

The file also supplies lookup and update operations: read one site, list visible sites, change visibility, attach or release a homepage binding, save previews and share cards, and unregister a site. Small validators keep names, visibility values, source roots, paths, and timestamps safe and predictable.

#### Function details

##### `SourceManifest._rooted`  (lines 96–99)

```
def _rooted(cls, root: str) -> str
```

**Purpose**: This validator checks that the stored-source file prefix is in one of the allowed storage areas and ends like a folder prefix. It prevents a manifest from pointing at an unexpected part of blob storage.

**Data flow**: It receives a root string from a SourceManifest. If the string starts with “sites/” or “apps/” and ends with “/”, it passes the same string through unchanged; otherwise it raises an error before the manifest can be accepted.

**Call relations**: Pydantic, the data validation library used for these models, calls this while building a SourceManifest. It is an early guard before any serving code trusts the manifest’s storage location.


##### `SourceManifest._pathed`  (lines 103–118)

```
def _pathed(cls, files: dict[str, SiteFile]) -> dict[str, SiteFile]
```

**Purpose**: This validator checks that every file path in a static site manifest is a plain path inside the site, not an absolute path or an escape attempt. It keeps a site’s stored files from referring outside their intended folder.

**Data flow**: It receives the manifest’s file dictionary. For each path, it rejects leading slashes, backslashes, control characters, and paths that would normalize to somewhere else; it uses contained_relative to confirm the path stays under the site-source anchor. If all paths are safe, it returns the original dictionary.

**Call relations**: Pydantic calls it while validating SourceManifest. It hands each candidate path to ufo.sdk.sandbox.contained_relative, which performs the containment check, and turns any escape into a manifest validation error.

*Call graph*: 1 external calls (contained_relative).


##### `site_name`  (lines 172–180)

```
def site_name(raw: str) -> str
```

**Purpose**: This turns a member-supplied site name into the safe stored name used in links and object names. It makes names predictable and rejects names that contain no letters or digits at all.

**Data flow**: It takes raw text, lowercases it, replaces runs of non-letter-or-number characters with hyphens, trims extra hyphens, and cuts it to the maximum allowed length. If nothing usable remains, it raises InvalidSiteName; otherwise it returns the cleaned name.

**Call relations**: Deploy-facing code uses this before registering a site, because register expects the name to already be safe. When the name cannot become a valid slug, it creates an InvalidSiteName error so the caller can report a clear tool failure.

*Call graph*: 1 external calls (__init__).


##### `default_visibility`  (lines 183–191)

```
def default_visibility(audience: Audience) -> Visibility
```

**Purpose**: This chooses the starting visibility for a brand-new site from the conversation’s audience. It keeps private or externally shared conversations from accidentally publishing a site to the whole workspace.

**Data flow**: It receives an Audience value, parses it, and checks whether it represents a direct member audience or an outside/foreign audience. Those become “private”; ordinary internal or workspace-shared conversations become “workspace”.

**Call relations**: HostedSites.register calls this only when inserting a new site and no explicit visibility was provided. It relies on parse_audience and audience_member to understand what kind of audience the conversation has.

*Call graph*: called by 1 (register); 2 external calls (audience_member, parse_audience).


##### `visibility_level`  (lines 194–202)

```
def visibility_level(value: str) -> Visibility
```

**Purpose**: This accepts only the three visibility values the site system understands: private, workspace, and public. It prevents unknown database or submitted values from being treated as valid permissions.

**Data flow**: It receives a string. If it matches one of the allowed visibility words, it returns that same value as a trusted visibility value; otherwise it raises a ValueError explaining the valid choices.

**Call relations**: _site calls this when converting a database row into a HostedSite object. That means every row read from storage is checked before the rest of the code uses its visibility.

*Call graph*: called by 1 (_site).


##### `HostedSites.register`  (lines 236–329)

```
async def register(self, conversation_id: UUID, name: str, port: int, creator_member_id: UUID, visibility: Visibility | None, audience: Audience, may_unhost: bool, *, manifest: str | None) -> HostedSi
```

**Purpose**: This records the result of a deploy: the site name, port, owner, visibility, and optional static-source manifest. It also enforces the safety rules that stop a deploy from taking over someone else’s site or homepage.

**Data flow**: It receives the conversation, cleaned site name, port, creator, optional requested visibility, audience, permission to unhost, and optional manifest. Inside one database transaction it checks refusal rules, deletes any same-conversation site that would be displaced from the port, updates an existing row or inserts a new one, stamps a fresh deploy generation, then reads and returns the registered HostedSite.

**Call relations**: Deploy code calls this after a site is ready to be hosted. It delegates safety checks to _refuse, reads existing rows through _read, uses default_visibility for new sites without explicit visibility, and uses SQL update/insert/delete operations to make the registry match the deploy.

*Call graph*: calls 3 internal fn (_read, _refuse, default_visibility); 6 external calls (case, delete, insert, update, time_ns, uuid4).


##### `HostedSites.redeploy`  (lines 331–358)

```
async def redeploy(self, conversation_id: UUID, name: str, manifest: str) -> HostedSite | None
```

**Purpose**: This replaces the stored static source for an existing site without changing its identity, owner, visibility, port, or homepage binding. It is used when the same public page should get new files rather than become a new site.

**Data flow**: It receives a conversation, site name, and new manifest string. It updates that row’s source manifest, updated time, and deploy generation in the database, then reads the row back; if the site no longer exists, the final result is None.

**Call relations**: Callers use this when updating an already-bound site in place. It uses the same deploy-generation idea as register so viewers can notice that the page changed, and it hands the final lookup to _read.

*Call graph*: calls 1 internal fn (_read); 3 external calls (case, update, time_ns).


##### `HostedSites.homepage`  (lines 360–372)

```
async def homepage(self, agent_id: UUID) -> HostedSite | None
```

**Purpose**: This finds the site currently bound as a given agent’s homepage. It answers None if that agent has no hosted homepage in this workspace.

**Data flow**: It receives an agent ID, queries the hosted_site table for a row in this workspace with that homepage binding, and converts the row into a HostedSite if one is found.

**Call relations**: Homepage-opening code calls this when it needs to know what page an agent should show. It builds the shared select list with _columns and turns the database row into the plain HostedSite object with _site.

*Call graph*: calls 2 internal fn (_columns, _site).


##### `HostedSites.set_preview`  (lines 374–394)

```
async def set_preview(self, conversation_id: UUID, name: str, preview: StoredPreview) -> None
```

**Purpose**: This saves the preview image captured after a deploy. The preview is separate from registration so the link can exist even if taking the screenshot is slow or fails.

**Data flow**: It receives a conversation, site name, and StoredPreview containing a blob key and byte size. It updates only the matching site row’s preview fields and updated time; it returns nothing.

**Call relations**: A deploy flow calls this after register, once rendering has produced a preview. If the site was removed or renamed before the preview finishes, the database update matches nothing and the old preview remains.

*Call graph*: 1 external calls (update).


##### `HostedSites.set_share_card`  (lines 396–420)

```
async def set_share_card(self, conversation_id: UUID, name: str, blob_key: str, digest: str) -> None
```

**Purpose**: This stores the share-card image made from a site page and the digest that identifies that exact card. The digest lets public card URLs change naturally when the card changes.

**Data flow**: It receives the conversation, site name, blob key, and digest. It writes those share-card fields and an updated time to the matching row, or changes nothing if the row is gone.

**Call relations**: extensions/sites/ufo_ext_sites/share_card._compose calls this after composing a card. Like set_preview, it is a later write after registration, so slow card generation does not block the site from being registered.

*Call graph*: called by 1 (_compose); 1 external calls (update).


##### `HostedSites.read`  (lines 422–424)

```
async def read(self, conversation_id: UUID, name: str) -> HostedSite | None
```

**Purpose**: This fetches one hosted site by conversation and name within the current workspace. It is the public lookup wrapper around the lower-level database read helper.

**Data flow**: It receives a conversation ID and site name, opens a transaction, asks _read to query the database, and returns either a HostedSite or None.

**Call relations**: Other parts of the site system call this when they need one registry row. It keeps transaction opening in the public method and leaves the actual row conversion to _read.

*Call graph*: calls 1 internal fn (_read).


##### `HostedSites.all`  (lines 426–436)

```
async def all(self) -> tuple[HostedSite, ...]
```

**Purpose**: This lists every hosted site in the workspace, ordered from oldest to newest. Higher-level permission gates can then decide which of these the current viewer may see.

**Data flow**: It opens a transaction, queries all rows with this workspace ID, orders them by creation time and name, converts each row to HostedSite, and returns them as a tuple.

**Call relations**: Workspace-level listing code can call this when it needs the full registry view. It uses _columns to keep the selected fields consistent and _site to build the returned objects.

*Call graph*: calls 2 internal fn (_columns, _site).


##### `HostedSites.conversation`  (lines 438–455)

```
async def conversation(self, conversation_id: UUID, names: tuple[str, ...], limit: int) -> tuple[HostedSite, ...]
```

**Purpose**: This lists selected hosted sites from one conversation, in oldest-first order. It is useful when a caller already knows which site names it is interested in.

**Data flow**: It receives a conversation ID, a tuple of names, and a limit. It queries rows in this workspace and conversation whose names are in that set, applies the limit, converts rows into HostedSite objects, and returns them.

**Call relations**: Conversation-level views call this to load a bounded set of sites. It shares the common column list from _columns and row conversion from _site.

*Call graph*: calls 2 internal fn (_columns, _site).


##### `HostedSites.visible_conversation`  (lines 457–487)

```
async def visible_conversation(self, conversation_id: UUID, member_id: UUID, limit: int, *, admin: bool, homepage_agents: frozenset[UUID]) -> tuple[HostedSite, ...]
```

**Purpose**: This lists the sites in a conversation that a particular member is allowed to open. It applies the basic visibility rules before returning rows.

**Data flow**: It receives a conversation, member ID, limit, whether the member is an admin, and homepage agents visible to that member. It builds a database filter that allows the creator’s own sites, non-private sites, and sites bound to visible homepage agents; admins skip that filter. It returns matching rows as HostedSite objects.

**Call relations**: UI or frame-opening code calls this when showing a member the sites they can access. It uses SQLAlchemy’s or_ to build the permission condition, _columns for the query shape, and _site for conversion.

*Call graph*: calls 2 internal fn (_columns, _site); 1 external calls (or_).


##### `HostedSites.set_visibility`  (lines 489–504)

```
async def set_visibility(self, conversation_id: UUID, name: str, visibility: Visibility) -> HostedSite | None
```

**Purpose**: This changes one site’s visibility level and returns the updated site. It also changes the site generation so old authorization decisions can be treated as stale.

**Data flow**: It receives a conversation, site name, and new visibility. It updates the matching row’s visibility, generation, and updated time, then reads the row back; if the row disappeared, it returns None.

**Call relations**: Visibility-changing flows call this after deciding the actor is allowed to make the change. It uses uuid4 to create a new generation marker and _read to return the current row after the update.

*Call graph*: calls 1 internal fn (_read); 2 external calls (update, uuid4).


##### `HostedSites.set_homepage`  (lines 506–532)

```
async def set_homepage(self, agent_id: UUID, conversation_id: UUID, name: str) -> HostedSite | None
```

**Purpose**: This binds a named site as an agent’s homepage. It first clears any previous homepage for that agent so there is only one current homepage.

**Data flow**: It receives an agent ID, conversation ID, and site name. In one transaction it removes that agent ID from any existing homepage row, sets it on the requested row, then reads and returns that row or None if it was not found.

**Call relations**: Agent-homepage setup code calls this when a site becomes the page members open for an agent. It performs both clearing and binding with database updates, then uses _read to return the bound site.

*Call graph*: calls 1 internal fn (_read); 1 external calls (update).


##### `HostedSites.release_homepage`  (lines 534–558)

```
async def release_homepage(self, conversation_id: UUID, name: str, visibility: Visibility) -> None
```

**Purpose**: This removes a site’s homepage binding and restores the site’s own visibility setting. It matters because while a site is bound as a homepage, access is governed by the agent rather than only by the site row.

**Data flow**: It receives a conversation, site name, and visibility level to resume. It clears homepage_agent_id, writes the supplied visibility, gives the row a new generation, updates the timestamp, and returns nothing.

**Call relations**: Homepage-unbinding flows call this when a page stops being an agent homepage. It uses uuid4 so any held permission tied to the old generation no longer silently applies.

*Call graph*: 2 external calls (update, uuid4).


##### `HostedSites.unregister`  (lines 560–571)

```
async def unregister(self, conversation_id: UUID, name: str) -> None
```

**Purpose**: This removes a site from the registry so its permanent link no longer resolves. It does not itself stop the sandbox process; it only removes the hosted-site record.

**Data flow**: It receives a conversation ID and site name, opens a transaction, and deletes the matching row for this workspace. No object is returned.

**Call relations**: Unhost flows call this when a member intentionally removes a site. It uses a database delete scoped by workspace, conversation, and name.

*Call graph*: 1 external calls (delete).


##### `HostedSites.refuse_or_pass`  (lines 573–593)

```
async def refuse_or_pass(self, conversation_id: UUID, name: str, port: int, creator_member_id: UUID, visibility: Visibility | None, may_unhost: bool) -> HostedSite | None
```

**Purpose**: This runs the same safety checks as register but writes nothing. It lets a deploy flow ask, before disrupting a running port, whether the registration would be allowed.

**Data flow**: It receives the same key details register needs for conflict checks: conversation, name, port, creator, optional visibility, and whether unhosting is allowed. It opens a transaction, calls _refuse, and returns the site that would be displaced, or raises the same error register would raise.

**Call relations**: Deploy orchestration calls this before serving new bytes on a port, because serving may already knock down the old site. register calls _refuse again later inside the real write, so the check is both previewed and enforced.

*Call graph*: calls 1 internal fn (_refuse).


##### `HostedSites._refuse`  (lines 595–628)

```
async def _refuse(self, connection: AsyncConnection, conversation_id: UUID, name: str, port: int, creator_member_id: UUID, visibility: Visibility | None, may_unhost: bool) -> HostedSite | None
```

**Purpose**: This is the central rule checker for whether a site registration is allowed. It protects visibility ownership, port ownership, live homepage bindings, and cases where no real member is allowed to request an unhost.

**Data flow**: It receives an open database connection and the proposed registration details. It reads the existing same-name site, checks whether a non-creator is trying to change visibility, looks for a different site already on the same port, and raises a specific error for forbidden takeover cases. If allowed, it returns the site that would be displaced or None.

**Call relations**: HostedSites.refuse_or_pass uses this for a dry run, and HostedSites.register uses it before writing. It calls _read to inspect the named site and _on_port to find a port conflict, then raises HomepageHoldsThePort, NotTheSiteCreator, or UnhostNeedsASpeaker when the rules forbid the change.

*Call graph*: calls 2 internal fn (_on_port, _read); called by 2 (refuse_or_pass, register); 3 external calls (__init__, __init__, __init__).


##### `HostedSites._on_port`  (lines 630–645)

```
async def _on_port(self, connection: AsyncConnection, conversation_id: UUID, port: int, name: str) -> HostedSite | None
```

**Purpose**: This finds whether the same conversation already has a different site registered on the target port. That matters because one port can only serve one origin, so a new registration may displace an old name.

**Data flow**: It receives an open database connection, conversation ID, port, and the new site name to exclude. It queries for a row in the same workspace and conversation with that port but a different name, then returns it as HostedSite or returns None.

**Call relations**: _refuse calls this while deciding whether a registration would unhost another site. It uses _columns to build a consistent select query and _site to convert the row.

*Call graph*: calls 2 internal fn (_columns, _site); called by 1 (_refuse); 1 external calls (execute).


##### `HostedSites._read`  (lines 647–659)

```
async def _read(self, connection: AsyncConnection, conversation_id: UUID, name: str) -> HostedSite | None
```

**Purpose**: This is the internal helper for reading one site row by conversation and name. It assumes the caller has already opened the database transaction.

**Data flow**: It receives an open database connection, conversation ID, and site name. It queries the workspace-scoped table for exactly that row and returns a HostedSite if found, otherwise None.

**Call relations**: register, redeploy, read, set_visibility, set_homepage, and _refuse all use this to avoid duplicating the same lookup. It uses _columns for the query fields and _site for row conversion.

*Call graph*: calls 2 internal fn (_columns, _site); called by 6 (_refuse, read, redeploy, register, set_homepage, set_visibility); 1 external calls (execute).


##### `HostedSites._columns`  (lines 661–678)

```
def _columns(self) -> sa.Select
```

**Purpose**: This builds the standard list of database columns needed to construct a HostedSite. It keeps all read queries selecting the same shape of data.

**Data flow**: It takes no external input beyond the HostedSites instance. It returns a SQL select object containing the hosted-site fields that _site expects.

**Call relations**: Read-style methods such as homepage, all, conversation, visible_conversation, _on_port, and _read call this before adding their own filters. This keeps those queries aligned with the HostedSite data object.

*Call graph*: called by 6 (_on_port, _read, all, conversation, homepage, visible_conversation); 1 external calls (select).


##### `_aware`  (lines 681–684)

```
def _aware(moment: datetime) -> datetime
```

**Purpose**: This makes sure a stored timestamp clearly says it is in UTC time. It fixes the common database issue where SQLite can return a timestamp without timezone information.

**Data flow**: It receives a datetime. If it already has timezone information, it returns it unchanged; otherwise it returns a copy marked as UTC.

**Call relations**: _site calls this for created_at and updated_at while building HostedSite objects. That means callers comparing times do not have to guess what timezone a database value meant.

*Call graph*: called by 1 (_site); 1 external calls (replace).


##### `_site`  (lines 687–704)

```
def _site(row: sa.Row) -> HostedSite
```

**Purpose**: This converts a raw database row into the HostedSite data object used by the rest of the code. It is the border between database-shaped data and application-shaped data.

**Data flow**: It receives a SQLAlchemy row, pulls out each hosted-site field, validates the visibility string with visibility_level, normalizes timestamps with _aware, and returns a HostedSite instance.

**Call relations**: All read paths that return HostedSite objects use this: _read, _on_port, all, conversation, homepage, and visible_conversation. It centralizes conversion so those callers do not each repeat the same field mapping.

*Call graph*: calls 2 internal fn (_aware, visibility_level); called by 6 (_on_port, _read, all, conversation, homepage, visible_conversation); 1 external calls (__init__).


### Sandbox site ingress
Sandbox ingress helpers create stable site origins, generate authorized URLs, connect to preview infrastructure, and report dead hosted sites.

### `core/src/ufo/harness/sandbox/ingress_serve.py`

`entrypoint` · `startup and request handling`

This file is the front door for sites built inside workspaces. Each site gets its own hostname, and this server uses that hostname to know which conversation and port the browser is trying to reach. Before it serves anything, it checks a signed session cookie, like a ticket at a venue door. Without this file, site links could not safely open in the product, static deployed sites would not be served, and live sandbox sites would not be reachable from the browser.

The flow has two main paths. First, a special view URL containing a short-lived signed token is opened. The server verifies that token, confirms it belongs to the site named by the hostname, and sets a host-only session cookie for that site. Then normal requests arrive with that cookie. If the site was stored as files, the server streams those files from the blob store. If the site is still live in a sandbox, it dials the right sandbox container and proxies HTTP or WebSocket traffic to it.

A lot of the code protects boundaries between sites. It strips private ingress cookies before forwarding requests, prevents sites from setting cookies in UFO’s reserved namespace, removes cache headers that could leak private bytes through shared caches, rewrites framing policy so the product can embed the site safely, and refuses cross-site WebSocket attempts.

#### Function details

##### `IngressServe.app`  (lines 368–400)

```
def app(self) -> FastAPI
```

**Purpose**: Builds the FastAPI web application and declares which paths belong to ingress itself versus which paths should be proxied to a site. This is the routing map for both HTTP requests and WebSocket connections.

**Data flow**: It starts with the configured IngressServe object → creates a FastAPI app → registers special routes for view-token opening, ordinary HTTP proxying, and WebSocket proxying → returns the ready-to-run app.

**Call relations**: The process startup code in run creates an IngressServe and hands server.app() to uvicorn. After that, FastAPI calls the registered methods when requests arrive.

*Call graph*: 1 external calls (FastAPI).


##### `IngressServe._no_view_token`  (lines 402–408)

```
async def _no_view_token(self, request: Request) -> Response
```

**Purpose**: Answers requests to the view-token path when no token was provided. It prevents an empty or malformed site-opening URL from accidentally being treated as valid.

**Data flow**: It receives an HTTP request → checks whether the method is GET or HEAD → returns either a 405 for unsupported methods or a 403 saying the link is not valid.

**Call relations**: IngressServe.app wires this method to the bare view path. It is the safe dead-end before _open is allowed to process real token-bearing links.

*Call graph*: 1 external calls (Response).


##### `IngressServe._open`  (lines 410–463)

```
async def _open(self, request: Request, view_path: str) -> Response
```

**Purpose**: Turns a valid signed view token into this site’s session cookie, then redirects the browser to the requested site path. This is how a portal link becomes an authorized visit to a per-site origin.

**Data flow**: It receives the request and the path segment containing the token → finds the site from the hostname → verifies the token and checks it matches that exact site → optionally checks that a sibling framing site belongs to the same workspace → mints a session token → sets it as a secure session cookie → returns a redirect to the real site path.

**Call relations**: FastAPI calls this for token-bearing view URLs. It relies on _site to interpret the hostname and _framer_belongs when a token names an allowed sibling framer, then hands later requests off to _proxy or _socket through the cookie it sets.

*Call graph*: calls 2 internal fn (_framer_belongs, _site); 9 external calls (replace, now, RedirectResponse, Response, mint_ingress_token, verify_ingress_token, cookie_secure, set_session_cookie, quote).


##### `IngressServe._site`  (lines 465–476)

```
def _site(self, request: HTTPConnection) -> tuple[UUID, int] | None
```

**Purpose**: Figures out which site a request is addressing by reading the Host name. The site identity is encoded in the subdomain, so this is how the server knows which conversation and port are being requested.

**Data flow**: It reads the request hostname → checks that it ends with this ingress server’s base host → parses the remaining label → returns a conversation ID and port, or returns None if the host is not a valid site host.

**Call relations**: _open uses this before accepting a view token, and _authorized uses it before accepting a session cookie. It is the shared hostname decoder for the server’s security checks.

*Call graph*: called by 2 (_authorized, _open); 1 external calls (parse_site_label).


##### `IngressServe._authorized`  (lines 478–500)

```
def _authorized(self, connection: HTTPConnection) -> IngressClaims | SiteRefusal
```

**Purpose**: Checks whether an HTTP request or WebSocket handshake is allowed to reach the site it names. It gives both protocols the same gate, so there is not one stricter door and one weaker door.

**Data flow**: It reads the target site from the hostname and the ingress session cookie from the connection → verifies the signed cookie and checks it matches the hostname’s conversation and port → returns the token claims if valid, or a SiteRefusal explaining the failure.

**Call relations**: _proxy calls this before serving HTTP traffic, and _socket calls it before opening a WebSocket. It uses _site for hostname decoding and returns a refusal that can be translated into either an HTTP response or a WebSocket denial.

*Call graph*: calls 1 internal fn (_site); called by 2 (_proxy, _socket); 3 external calls (__init__, now, verify_ingress_token).


##### `IngressServe._stored_manifest`  (lines 502–540)

```
async def _stored_manifest(self, claims: IngressClaims) -> dict[str, StoredFile] | None
```

**Purpose**: Finds out whether the requested site should be served as saved files instead of dialed live from a sandbox. For stored sites, it returns the file map that tells the server where each path’s bytes live.

**Data flow**: It receives verified ingress claims → if the claims refer to a shipped app, it asks _shipped_manifest for the deploy-wide file list → otherwise it reads the hosted_site row for that workspace, conversation, and port → parses the stored manifest JSON → returns a path-to-StoredFile map, or None if this is not a stored site.

**Call relations**: _proxy uses this to decide between blob serving and live proxying. _socket also uses it to refuse WebSockets for static stored sites, because saved files cannot speak a socket protocol.

*Call graph*: calls 1 internal fn (_shipped_manifest); called by 2 (_proxy, _socket); 4 external calls (__init__, loads, select, workspace_tx).


##### `IngressServe._shipped_manifest`  (lines 542–574)

```
async def _shipped_manifest(self, shipped: ShippedClaim) -> dict[str, StoredFile] | None
```

**Purpose**: Builds or reuses the file map for a shipped app bundle stored in the fleet-wide blob store. Shipped apps are shared deploy code, not workspace-owned generated site files.

**Data flow**: It receives a shipped-app claim containing a digest and slug → checks an in-memory cache → lists files under the digest in fleet storage → maps those stored keys to request paths and media types → returns the manifest, or None if the bundle is gone.

**Call relations**: _stored_manifest calls this when session claims point to a shipped app. Later _serve_stored reads the files described here from the fleet blob store.

*Call graph*: called by 1 (_stored_manifest); 3 external calls (__init__, __init__, guess_type).


##### `IngressServe._dial_site`  (lines 576–606)

```
async def _dial_site(self, claims: IngressClaims) -> DialTarget | SiteRefusal
```

**Purpose**: Finds the live network address for a sandbox site’s port. This is used when the site is not stored as static files and must be contacted in its running container.

**Data flow**: It receives authorized claims → reads the stored sandbox handle for the conversation → chooses the right carrier backend, including older resume backends when needed → asks the carrier to dial the requested port → returns a DialTarget, or a SiteRefusal if no container is available or reachable.

**Call relations**: _proxy calls this for live HTTP forwarding, and _socket calls it for live WebSocket forwarding. It depends on _stored_handle to find the saved sandbox identity.

*Call graph*: calls 1 internal fn (_stored_handle); called by 2 (_proxy, _socket); 6 external calls (__init__, __init__, warn, sandbox_handle_backend, sandbox_handle_id, ws).


##### `IngressServe._proxy`  (lines 608–682)

```
async def _proxy(self, request: Request, path: str) -> Response
```

**Purpose**: Serves ordinary HTTP requests for a hosted site. It either returns stored files from blob storage or forwards the request to a live sandbox server and streams the response back.

**Data flow**: It receives an HTTP request and path → authorizes the session → checks for a stored manifest → serves a stored file if one exists → otherwise dials the sandbox → builds an upstream request with cleaned headers and streaming body → streams the upstream response back with safe cache, cookie, and framing headers.

**Call relations**: FastAPI routes all normal HTTP site paths here. It coordinates many helpers: _authorized gates access, _stored_manifest and _serve_stored handle static sites, _dial_site and _upstream_url contact live sites, _upstream_headers sanitizes forwarding, _body streams bytes, and response helpers clean cookies and policies.

*Call graph*: calls 11 internal fn (_authorized, _body, _confined_cookie, _dial_site, _frame_ancestors, _not_answering, _serve_stored, _stored_manifest, _unframed_policy, _upstream_headers (+1 more)); 8 external calls (stream, Response, StreamingResponse, Request, BackgroundTask, log_error, warn, ws).


##### `IngressServe._not_answering`  (lines 684–711)

```
def _not_answering(self, request: HTTPConnection, claims: IngressClaims) -> Response
```

**Purpose**: Creates the user-facing response shown when a live site cannot be reached. For full page loads, it shows a small waiting page that reloads and also reports the outage.

**Data flow**: It receives the original connection and site claims → builds a content security policy allowing the same framers as the real site → if the request is not a document load, returns plain text → if it is a page load, returns an HTML waiting page and schedules a background report.

**Call relations**: _proxy calls this when dialing or reading the live upstream fails, or when the upstream returns a status that would be replaced by the edge proxy. It uses _frame_ancestors so the fallback page is embeddable in the same places as the real site.

*Call graph*: calls 1 internal fn (_frame_ancestors); called by 1 (_proxy); 2 external calls (Response, BackgroundTask).


##### `IngressServe._serve_stored`  (lines 713–779)

```
async def _serve_stored(self, request: Request, claims: IngressClaims, files: dict[str, StoredFile], path: str) -> Response
```

**Purpose**: Serves one file from a stored static site or shipped app bundle. It gives stored sites a fast path that does not need the sandbox to still be running.

**Data flow**: It receives the request, claims, a file manifest, and the requested path → allows only GET and HEAD → looks for the exact file or an index.html under the path → prepares cache, ETag, content type, and framing headers → returns 304 if the browser already has the right version → otherwise streams the bytes from workspace or fleet blob storage.

**Call relations**: _proxy calls this after _stored_manifest proves the site has saved files. It uses _frame_ancestors for embedding policy and _stored_body to stream after confirming the blob exists.

*Call graph*: calls 2 internal fn (_frame_ancestors, _stored_body); called by 1 (_proxy); 5 external calls (__init__, __init__, Response, StreamingResponse, ws).


##### `IngressServe._stored_body`  (lines 781–787)

```
async def _stored_body(self, first: bytes, rest: AsyncIterator[bytes]) -> AsyncIterator[bytes]
```

**Purpose**: Streams a stored file after the first chunk has already been read. Reading the first chunk early lets the caller turn a missing blob into a clean 404 before sending a 200 response.

**Data flow**: It receives the first byte chunk and an async iterator for the rest → yields the first chunk → yields each remaining chunk in order → produces a streaming response body.

**Call relations**: _serve_stored calls this only after it has successfully read the first blob chunk. It is the small bridge between blob storage streaming and FastAPI’s StreamingResponse.

*Call graph*: called by 1 (_serve_stored).


##### `IngressServe._framer_belongs`  (lines 789–824)

```
async def _framer_belongs(self, workspace_id: UUID, conversation_id: UUID, port: int) -> bool
```

**Purpose**: Checks whether a site named as an extra allowed framer really belongs to the same workspace. This stops a token from granting framing rights to an unrelated site.

**Data flow**: It receives a workspace ID, conversation ID, and port → looks for a matching hosted_site row → if not found, checks provisioned shipped apps in that workspace → returns true only when the framer can be tied to that workspace.

**Call relations**: _open calls this when a view token includes a framer claim. It supports the later _frame_ancestors behavior by making sure any sibling origin added to the policy was proven first.

*Call graph*: called by 1 (_open); 5 external calls (select, workspace_tx, serve_port, shipped_anchor, shipped_app_slug).


##### `IngressServe._frame_ancestors`  (lines 826–834)

```
def _frame_ancestors(self, claims: IngressClaims) -> str
```

**Purpose**: Builds the Content-Security-Policy frame-ancestors value for a response. In plain terms, it decides which pages are allowed to put this site inside a frame.

**Data flow**: It receives ingress claims → starts with the configured app origin or 'none' → if the claims include an approved sibling framer, it adds that sibling site origin → returns the policy value as text.

**Call relations**: _proxy, _serve_stored, and _not_answering call this before returning site content or fallback content. It uses the site-label helper to render sibling site origins consistently.

*Call graph*: called by 3 (_not_answering, _proxy, _serve_stored); 1 external calls (site_label).


##### `IngressServe._upstream_url`  (lines 836–839)

```
def _upstream_url(self, scheme: str, host: str, path: str, query_string: bytes) -> str
```

**Purpose**: Builds the exact URL used to contact a live sandbox server. It preserves the requested path and query string while safely quoting path characters.

**Data flow**: It receives a scheme, upstream host, request path, and raw query string → quotes the path for use in a URL → appends the query string if present → returns the full upstream URL.

**Call relations**: _proxy uses this for HTTP forwarding and _socket uses it for WebSocket forwarding. It is the shared URL builder for live sandbox traffic.

*Call graph*: called by 2 (_proxy, _socket); 1 external calls (quote).


##### `IngressServe._stored_handle`  (lines 841–851)

```
async def _stored_handle(self, workspace_id: UUID, conversation_id: UUID) -> str | None
```

**Purpose**: Reads the saved sandbox handle for a conversation. The handle is the remembered identity needed to reconnect to the right running or resumable sandbox container.

**Data flow**: It receives a workspace ID and conversation ID → queries the conversation table under that workspace → returns the sandbox_handle string if found, otherwise None.

**Call relations**: _dial_site calls this before asking a carrier to reach a live site port. Without it, the dialer would not know which container belongs to the conversation.

*Call graph*: called by 1 (_dial_site); 2 external calls (select, workspace_tx).


##### `IngressServe._upstream_headers`  (lines 853–887)

```
def _upstream_headers(self, request: HTTPConnection, dial_headers: Mapping[str, str]) -> list[tuple[str, str]]
```

**Purpose**: Creates the cleaned list of request headers that a live sandbox server is allowed to see. It removes proxy-only headers, WebSocket handshake internals, the ingress session cookie, and conflicting dial-supplied headers.

**Data flow**: It receives the viewer connection and headers required by the dial target → walks through incoming headers → drops unsafe or irrelevant names → removes the ingress cookie from Cookie headers while keeping site cookies → appends dial target headers → returns the final header list for the upstream request.

**Call relations**: _proxy uses this when building an HTTP request to the sandbox, and _socket uses it when opening an upstream WebSocket. It is a key privacy boundary between the platform and agent-authored site code.

*Call graph*: called by 2 (_proxy, _socket).


##### `IngressServe._unframed_policy`  (lines 889–901)

```
def _unframed_policy(self, policy: str) -> str
```

**Purpose**: Removes a site’s own frame-ancestors directive from a Content-Security-Policy header while preserving the rest of that policy. This lets the platform decide framing rules without discarding the site’s other browser protections.

**Data flow**: It receives one policy header string → splits it into directives → filters out any directive named frame-ancestors → rejoins the remaining directives → returns the cleaned policy, or an empty string if nothing remains.

**Call relations**: _proxy calls this while copying response headers from a live sandbox. The proxy then adds its own frame-ancestors policy separately.

*Call graph*: called by 1 (_proxy).


##### `IngressServe._confined_cookie`  (lines 903–930)

```
def _confined_cookie(self, header: str) -> str | None
```

**Purpose**: Cleans one Set-Cookie header from a site before sending it to the browser. It keeps the site’s own cookies but prevents them from escaping their site hostname or using UFO’s reserved cookie names.

**Data flow**: It receives a Set-Cookie header → parses the cookie name and attributes → drops invalid, nameless, or reserved-prefix cookies → removes any Domain attribute so the browser scopes the cookie to this host only → returns the safe header, or None to drop it.

**Call relations**: _proxy calls this for every Set-Cookie header from a live upstream response. It protects the app and sibling sites from cookies planted by agent-authored code.

*Call graph*: called by 1 (_proxy).


##### `IngressServe._body`  (lines 932–942)

```
async def _body(self, upstream: httpx.Response) -> AsyncIterator[bytes]
```

**Purpose**: Streams raw response bytes from a live upstream server and makes sure the upstream connection is closed when streaming ends. This avoids holding network connections open after failures or disconnects.

**Data flow**: It receives an httpx response → yields each raw chunk from the upstream body → always closes the upstream response in a final cleanup step → produces the bytes consumed by StreamingResponse.

**Call relations**: _proxy passes this to StreamingResponse when relaying live HTTP responses. It complements the response background close, covering mid-stream failures too.

*Call graph*: called by 1 (_proxy); 2 external calls (aclose, aiter_raw).


##### `IngressServe._no_socket_view`  (lines 944–950)

```
async def _no_socket_view(self, websocket: WebSocket) -> None
```

**Purpose**: Rejects WebSocket attempts to the special view-token path. That path is only for exchanging a token over HTTP, not for giving token text to sandbox code.

**Data flow**: It receives a WebSocket handshake → creates a refusal saying the link is not valid → sends an HTTP-style WebSocket denial response.

**Call relations**: IngressServe.app wires this to the view-token WebSocket routes. It delegates the actual denial formatting to _refuse.

*Call graph*: calls 1 internal fn (_refuse); 1 external calls (__init__).


##### `IngressServe._socket`  (lines 952–1008)

```
async def _socket(self, websocket: WebSocket, path: str) -> None
```

**Purpose**: Relays an authorized WebSocket connection between the browser and a live sandbox site. This is needed for things like live reload or apps that push messages instead of only answering HTTP requests.

**Data flow**: It receives a WebSocket handshake and path → checks the Origin header matches the addressed site → authorizes the session cookie → refuses static stored sites → dials the live sandbox port → opens an upstream WebSocket with cleaned headers and offered subprotocols → accepts the viewer connection only after upstream accepts → relays messages both ways.

**Call relations**: FastAPI routes catch-all WebSocket site traffic here. It coordinates _same_origin, _authorized, _stored_manifest, _dial_site, _upstream_url, _upstream_headers, _refuse, _relay, and _end.

*Call graph*: calls 9 internal fn (_authorized, _dial_site, _end, _refuse, _relay, _same_origin, _stored_manifest, _upstream_headers, _upstream_url); 6 external calls (__init__, accept, log_error, ws, connect, Subprotocol).


##### `IngressServe._same_origin`  (lines 1010–1022)

```
def _same_origin(self, websocket: WebSocket) -> bool
```

**Purpose**: Checks that a WebSocket was opened by the same site hostname it is connecting to. This blocks one hosted site from using the browser’s cookies to open a socket into another site.

**Data flow**: It reads the Origin header and the requested WebSocket hostname → parses the origin hostname → returns true only when both hostnames match and Origin is present.

**Call relations**: _socket calls this before any authorization or dialing. It is a WebSocket-specific protection because browser cross-origin rules for normal HTTP reads do not protect WebSocket handshakes in the same way.

*Call graph*: called by 1 (_socket); 1 external calls (urlsplit).


##### `IngressServe._refuse`  (lines 1024–1037)

```
async def _refuse(self, websocket: WebSocket, refusal: SiteRefusal) -> None
```

**Purpose**: Rejects a WebSocket handshake with the same kind of status, body, and cache policy that an HTTP request would have received. This keeps failure behavior consistent across protocols.

**Data flow**: It receives a WebSocket object and a SiteRefusal → builds a Response with the refusal message, status, media type, and no-store cache header → sends it as a WebSocket denial response.

**Call relations**: _no_socket_view and _socket call this whenever a WebSocket must not be accepted. It turns the shared SiteRefusal object into the protocol-specific denial.

*Call graph*: called by 2 (_no_socket_view, _socket); 2 external calls (send_denial_response, Response).


##### `IngressServe._relay`  (lines 1039–1056)

```
async def _relay(self, viewer: WebSocket, upstream: ClientConnection) -> None
```

**Purpose**: Runs both halves of a WebSocket relay at the same time: browser to site and site to browser. When either side finishes, it stops the other side too.

**Data flow**: It receives the accepted viewer WebSocket and upstream site connection → starts one task for each direction → waits until one task completes → cancels the other task → raises any real failure from the completed side.

**Call relations**: _socket calls this after both WebSockets are open. It delegates the actual message copying to _viewer_to_site and _site_to_viewer.

*Call graph*: calls 2 internal fn (_site_to_viewer, _viewer_to_site); called by 1 (_socket); 3 external calls (create_task, gather, wait).


##### `IngressServe._viewer_to_site`  (lines 1058–1067)

```
async def _viewer_to_site(self, viewer: WebSocket, upstream: ClientConnection) -> None
```

**Purpose**: Copies WebSocket messages from the browser to the site without changing text messages into binary messages or the reverse. The message type is part of many WebSocket protocols.

**Data flow**: It repeatedly receives messages from the viewer → stops when the viewer disconnects → sends text as text and bytes as bytes to the upstream site connection.

**Call relations**: _relay runs this as one of its two concurrent directions. Its paired direction is _site_to_viewer.

*Call graph*: called by 1 (_relay); 2 external calls (receive, send).


##### `IngressServe._site_to_viewer`  (lines 1069–1082)

```
async def _site_to_viewer(self, upstream: ClientConnection, viewer: WebSocket) -> None
```

**Purpose**: Copies WebSocket messages from the site back to the browser and then closes the browser side with an appropriate close code. This lets the site’s client know why the socket ended when possible.

**Data flow**: It reads messages from the upstream site connection → sends strings as text frames and bytes as binary frames to the viewer → when upstream closes, chooses a safe close code and reason → asks _end to close the viewer socket.

**Call relations**: _relay runs this as the site-to-browser direction. It calls _end to finish the viewer connection safely.

*Call graph*: calls 1 internal fn (_end); called by 1 (_relay); 3 external calls (suppress, send_bytes, send_text).


##### `IngressServe._end`  (lines 1084–1094)

```
async def _end(self, viewer: WebSocket, code: int, reason: str) -> None
```

**Purpose**: Closes the viewer WebSocket without letting close-time errors hide the original problem. If the browser already disappeared, that is treated as harmless.

**Data flow**: It receives the viewer WebSocket, close code, and reason → attempts to close the connection → suppresses any exception raised because the connection is already gone.

**Call relations**: _site_to_viewer uses this after the upstream site closes, and _socket uses it when relay failure needs to end the viewer side.

*Call graph*: called by 2 (_site_to_viewer, _socket); 2 external calls (suppress, close).


##### `ingress_base_host`  (lines 1097–1108)

```
def ingress_base_host(configured: str | None) -> str
```

**Purpose**: Extracts the wildcard base host that all site subdomains live under. It refuses to start if this host is missing because the ingress server would not be able to identify any site.

**Data flow**: It receives the configured ingress public URL → parses out the hostname → returns it if present → raises a startup error if no hostname exists.

**Call relations**: run calls this while building IngressServe. The result is later used by _site to decide whether an incoming Host belongs to this ingress deployment.

*Call graph*: called by 1 (run); 1 external calls (urlsplit).


##### `ingress_frame_ancestor`  (lines 1111–1121)

```
def ingress_frame_ancestor(configured: str | None) -> str
```

**Purpose**: Builds the browser policy value for the product page that is allowed to frame hosted sites. If the product public URL is not configured, it returns 'none' so no embedding is allowed by default.

**Data flow**: It receives the configured connect public URL → parses its scheme, hostname, and optional port → returns an origin string like https://host:port, or 'none' if no valid origin is configured.

**Call relations**: run calls this during startup and stores the result on IngressServe. Response paths later use _frame_ancestors to include it in Content-Security-Policy headers.

*Call graph*: called by 1 (run); 1 external calls (urlsplit).


##### `upstream_client`  (lines 1124–1147)

```
def upstream_client() -> httpx.AsyncClient
```

**Purpose**: Creates the shared HTTP client used to contact live sandbox servers. It is deliberately cookie-blind so one site’s cookies cannot be stored by the ingress process and replayed to another site.

**Data flow**: It creates timeout and connection-limit settings → creates a cookie jar whose policy allows no domains → builds and returns an httpx AsyncClient using those limits and the empty, non-storing cookie jar.

**Call relations**: run calls this once at startup. The resulting client is passed to IngressServe for live proxying and to SiteReporter for outage reports.

*Call graph*: called by 1 (run); 4 external calls (CookieJar, DefaultCookiePolicy, AsyncClient, Limits).


##### `run`  (lines 1150–1179)

```
def run() -> None
```

**Purpose**: Starts the ingress service process. It loads configuration, prepares dependencies, constructs the server object, and hands the FastAPI app to uvicorn, the ASGI web server.

**Data flow**: It loads config → initializes observability and database access → verifies the database and ingress secret → loads extension manifests and selects sandbox carriers → creates the upstream client, blob store, reporter, and IngressServe → logs startup → runs uvicorn on the configured host and port.

**Call relations**: This is the file’s top-level entrypoint. It calls ingress_base_host, ingress_frame_ancestor, and upstream_client during setup, then runs the application returned by IngressServe.app.

*Call graph*: calls 3 internal fn (ingress_base_host, ingress_frame_ancestor, upstream_client); 15 external calls (__init__, __init__, run, blob_store_for, load_config, init_db, verify_db_reachable, init_o11y, log, ingress_secret (+5 more)).


### `core/src/ufo/harness/sandbox/ingress_host.py`

`domain_logic` · `request handling and sandbox site routing`

A browser treats different hostnames as different “origins,” meaning each one gets its own cookies and local storage. This file turns a conversation ID and a sandbox port into a short DNS label, like an address on an envelope. That label is stable, so bookmarks and stored browser state can keep working across redeploys.

The label is not the main security permission. Real access is still checked elsewhere by an ingress token or session cookie. Instead, this label prevents random guessed hostnames from even pointing at a conversation unless they were made by this deployment. It does that by adding a small HMAC signature, which is a tamper-check made with a shared secret. If someone changes the conversation or port in the label, the signature will no longer match.

The file also enforces one exact spelling for each label. Base32 encoding can otherwise leave a few unused bits, which would allow several different-looking hostnames to decode to the same address. Since browsers would treat those hostnames as separate sites, this code rejects non-canonical spellings. In short: one conversation and port become one hostname label, and only that exact label is accepted.

#### Function details

##### `serve_port`  (lines 57–63)

```
def serve_port(conversation_id: UUID) -> int
```

**Purpose**: Chooses the stable sandbox port for a conversation. This avoids needing to store a separate port number while still giving each conversation a repeatable place to serve its site.

**Data flow**: It takes a conversation UUID as input. It uses the numeric value of that UUID to pick a port inside the configured application port range. It returns that port number and does not change any outside state.

**Call relations**: Other parts of the sandbox hosting flow use this when they need the port that belongs to a conversation. The returned port can later be included in a site label by `site_label`, so everyone derives the same address instead of inventing or storing one separately.


##### `shipped_app_slug`  (lines 66–74)

```
def shipped_app_slug(provisioned_by: str | None) -> str | None
```

**Purpose**: Extracts the public page slug from the provision name of a shipped app extension. This gives the system a stable app identity even if a visible agent name changes because of naming collisions.

**Data flow**: It receives a provision name, or nothing. If there is no name, it returns nothing. If the name exactly matches the expected form, such as `app_something`, it returns the `something` part; otherwise it returns nothing.

**Call relations**: This is used when shipped app pages need stable origins or bundle paths. It does not call other project functions; it simply checks the naming pattern and hands back the slug that later steps can use.


##### `shipped_anchor`  (lines 77–82)

```
def shipped_anchor(workspace_id: UUID, slug: str) -> UUID
```

**Purpose**: Creates a stable synthetic UUID for a shipped app page inside one workspace. This gives a shipped page its own browser cookies and storage even though it is not backed by a normal conversation row.

**Data flow**: It takes a workspace UUID and an app slug. It combines them with a fixed label and feeds that text into UUID version 5 generation, which deterministically creates the same UUID from the same name every time. It returns that UUID.

**Call relations**: When a shipped app page needs an origin-like identity, this function supplies one. It calls `uuid.uuid5` to make a repeatable UUID, so later code can treat the shipped page much like a conversation-owned hosted site.

*Call graph*: 1 external calls (uuid5).


##### `site_label`  (lines 85–90)

```
def site_label(conversation_id: UUID, port: int) -> str
```

**Purpose**: Builds the DNS label for one conversation’s sandbox port. This is the label that can be placed in a hostname to route a browser to the right sandbox site.

**Data flow**: It receives a conversation UUID and a port number. First it rejects ports outside the valid TCP port range. Then it joins the UUID bytes and the two-byte port, signs that address with `_signature`, encodes the address plus signature with `_encode`, and returns the lowercase base32 label.

**Call relations**: This is the label-making side of the pair with `parse_site_label`. It relies on `_signature` to add the deploy-specific tamper-check and `_encode` to turn raw bytes into a DNS-friendly string.

*Call graph*: calls 2 internal fn (_encode, _signature).


##### `parse_site_label`  (lines 93–105)

```
def parse_site_label(label: str) -> tuple[UUID, int]
```

**Purpose**: Reads a DNS label back into the conversation UUID and port it claims to name, but only if the label is well-formed, signed by this deployment, and written in its one accepted spelling.

**Data flow**: It takes a label string from a hostname. It base32-decodes it, re-encodes the bytes with `_encode` to check that the spelling is canonical, separates the address from the signature, and compares the signature with a freshly computed `_signature`. If anything is wrong, it raises `SiteLabelError`; if all checks pass, it returns the UUID and port.

**Call relations**: This is used on the receiving side when a request arrives for a sandbox hostname. Before any sandbox is contacted or conversation data is read, it verifies the label. It calls base32 decoding, `_encode`, `_signature`, `hmac.compare_digest` for safe signature comparison, and `uuid.UUID` to reconstruct the conversation ID.

*Call graph*: calls 2 internal fn (_encode, _signature); 4 external calls (__init__, b32decode, compare_digest, UUID).


##### `_encode`  (lines 108–109)

```
def _encode(raw: bytes) -> str
```

**Purpose**: Turns raw address bytes into the compact DNS-safe spelling used for site labels. It keeps labels lowercase and removes base32 padding characters that are not needed in DNS names.

**Data flow**: It receives bytes. It base32-encodes them, converts the result to text, strips trailing `=` padding, lowercases the spelling by construction, and returns that string.

**Call relations**: `site_label` uses this when creating labels, and `parse_site_label` uses it again to confirm that an incoming label uses the single canonical spelling. It delegates the actual base32 conversion to Python’s `base64.b32encode`.

*Call graph*: called by 2 (parse_site_label, site_label); 1 external calls (b32encode).


##### `_signature`  (lines 112–114)

```
def _signature(address: bytes) -> bytes
```

**Purpose**: Creates the short tamper-check attached to a site label. It proves that the address bytes were produced with this deployment’s ingress secret, without making the label itself an access token.

**Data flow**: It receives the raw address bytes made from a conversation UUID and port. It reads the deploy secret through `ingress_secret`, combines that secret with a label-specific prefix and the address, computes an HMAC using SHA-256, and returns only the first four bytes of the digest.

**Call relations**: `site_label` calls this to attach a signature to a new label. `parse_site_label` calls it again to compute what the signature should be for an incoming label, then compares that expected value with the label’s included signature.

*Call graph*: called by 2 (parse_site_label, site_label); 2 external calls (new, ingress_secret).


### `core/src/ufo/harness/sandbox/ingress_url.py`

`io_transport` · `request handling`

A sandbox may run a web server on an internal port, but a browser needs a public URL to reach it safely. This file is the small bridge that turns “workspace X, conversation Y, port Z, path P” into a real browser link. Without it, the system would not have a consistent way to create expiring, scoped links for sandbox web previews.

The main function starts with a configured public base URL, such as the outside address for the ingress service. It then creates a signed token containing the important facts: which workspace and conversation the request belongs to, which port should be reached, and when the link should stop working. If the preview is tied to a shipped artifact, it can include that identity too. The token is like a temporary visitor badge: it tells the gateway what the browser is allowed to see, and it expires after a fixed time.

The URL also includes a site label in the hostname. That label identifies the conversation and port before the request even reaches the path. Finally, the requested entry path is safely quoted so unusual characters do not break the URL.

A helper, `_framer_claim`, checks whether the new URL is being opened from another sandbox preview under the same public host. If so, it extracts that parent preview’s conversation and port, so the system can understand the framing relationship.

#### Function details

##### `mint_ingress_view_url`  (lines 17–50)

```
def mint_ingress_view_url(public_url: str | None, workspace_id: UUID, conversation_id: UUID, port: int, entry_path: str, *, framed_from: str | None=None, shipped_slug: str | None=None, shipped_digest:
```

**Purpose**: Creates the public, expiring browser URL for one sandbox port. Someone uses it when they want to show or open a web app running inside a workspace sandbox.

**Data flow**: It receives the public ingress base URL, workspace and conversation IDs, a sandbox port, and the path the browser should open. If there is no public base URL, it returns nothing. Otherwise it breaks the base URL into parts, optionally builds a shipped-artifact claim, asks `_framer_claim` whether this view is being opened from another sandbox frame, mints a signed ingress token with an expiry time, creates a hostname label for the conversation and port, safely quotes the entry path, and returns the final URL string.

**Call relations**: This is the main function in the file. It relies on `urlsplit` to understand the public base URL, `site_label` to create the subdomain-style label, `mint_ingress_token` to create the temporary access badge, and `quote` to make the path safe for a URL. During that process it calls `_framer_claim` so any parent sandbox frame can be recorded inside the token.

*Call graph*: calls 1 internal fn (_framer_claim); 7 external calls (__init__, __init__, now, site_label, mint_ingress_token, quote, urlsplit).


##### `_framer_claim`  (lines 53–73)

```
def _framer_claim(base: SplitResult, framed_from: str | None) -> FramerClaim | None
```

**Purpose**: Figures out whether the requested preview is being framed by another sandbox preview on the same ingress host. If it is, it returns a small claim describing that parent preview.

**Data flow**: It receives the parsed public base URL and an optional `framed_from` URL. It parses the framing URL, compares its scheme, port, and hostname against the base ingress URL, and rejects it if it is missing, malformed, or from the wrong place. If the framing host looks like another valid sandbox hostname, it extracts the site label, turns that label back into a conversation ID and port, and returns a `FramerClaim`. If any check fails, it returns nothing.

**Call relations**: `mint_ingress_view_url` calls this helper while building the signed token. The helper delegates the label decoding to `parse_site_label`, then hands the resulting `FramerClaim` back so it can be included in the ingress claims before the token is minted.

*Call graph*: called by 1 (mint_ingress_view_url); 3 external calls (__init__, parse_site_label, urlsplit).


### `core/src/ufo/harness/sandbox/preview.py`

`config` · `startup/config load`

The preview service is an internal service that can render shared files and document reads for a sandbox. This file gives the rest of the system one agreed name for that service: `preview.ufo.internal`. Think of it like putting the service’s room number on a building map, so every other part of the system points to the same place.

It also defines the authorization header name and a fake placeholder token, called a sentinel. A sentinel is a stand-in value. The sandbox can send this placeholder, and the proxy can replace it with the real deploy token only when talking to the trusted preview host. This matters because the real secret token never has to enter the sandbox.

The only behavior in the file is `parse_preview_service`, which reads a deploy configuration value written as `host:port`. If the deploy has no preview service, the value can be missing and the function returns `None`. If a value is present but malformed, the function raises an error instead of silently disabling preview. That is intentional: a bad preview address is treated as a deployment mistake that should be noticed early.

#### Function details

##### `parse_preview_service`  (lines 16–25)

```
def parse_preview_service(value: str | None) -> tuple[str, int] | None
```

**Purpose**: This function turns the preview service configuration string into a usable host and port pair. It is used so deployment setup code can tell the proxy or sandbox machinery where the internal preview service lives.

**Data flow**: It receives either a string like `some-host:443` or `None`. If it gets `None`, it returns `None`, meaning no preview service is configured. If it gets a string, it splits it at the last colon, checks that there is a host and a separator, converts the port text into a number, and returns `(host, port)`. If the string is missing the required `host:port` shape, it raises `ValueError` so the bad deploy setting is caught clearly.

**Call relations**: No direct callers are shown in the provided graph, but this function is the small gatekeeper for the preview service address. Configuration-reading code would call it when loading the deploy value, then pass the parsed host and port onward to the parts that route preview traffic.


### `core/src/ufo/harness/sandbox/site_report.py`

`io_transport` · `request handling, when a hosted site fails to answer`

A hosted site can fail in a place where the normal conversation engine is not running: the ingress, which is the reverse proxy that receives browser traffic and forwards it to the sandbox. This file is the bridge between that proxy and the main app. Think of it like a front desk clerk who notices a room’s phone is dead and sends a signed note to the repair dispatcher.

The ingress side, `SiteReporter`, receives the same signed claims that were already used to decide whether the browser may reach the site. If reporting is enabled, it makes a short-lived report token and posts it to the serve process. The token is both the proof and the message: it says which workspace, conversation, and port failed, and it is signed so outsiders cannot forge it.

The serve side, `SiteReports`, exposes a private FastAPI route for that post. It verifies the token, finds the conversation’s agent, and asks the turn system to admit a message saying the site on that port did not answer. Reports are deliberately idempotent: repeated browser reloads within the same time bucket use the same idempotency key, so they collapse into one repair turn instead of flooding the agent. If the conversation has no agent or the agent is archived, the report is quietly accepted or ignored as appropriate.

#### Function details

##### `SiteReporter.report`  (lines 74–105)

```
async def report(self, claims: IngressClaims) -> None
```

**Purpose**: This is the ingress side of the report. When the reverse proxy sees that a conversation’s hosted site is not answering, this function sends a signed, short-lived notice to the serve process.

**Data flow**: It starts with `IngressClaims`, which identify the workspace, conversation, port, and related access details for the site request. If there is no serve URL configured, or if the claims describe a shipped site rather than an active conversation sandbox, it does nothing. Otherwise it copies the claims with a fresh expiration time and with fields removed that should not travel on this report, turns that into a signed report token, and sends an HTTP POST to the internal report path. If the network call fails or serve rejects the report, it writes a warning; it does not retry, because the browser’s waiting page will report again on its next reload.

**Call relations**: This function is used by the ingress process after it has detected that the target site is down. It relies on `mint_ingress_token` to make the signed report, uses the async HTTP client to deliver it to the serve app, and uses `warn` only when that one-hop delivery fails or is refused. The matching receiver is `SiteReports._report`, which verifies the token and turns it into a conversation message.

*Call graph*: 4 external calls (replace, now, warn, mint_ingress_token).


##### `SiteReports.router`  (lines 125–128)

```
def router(self) -> APIRouter
```

**Purpose**: This builds the small FastAPI router that exposes the internal endpoint used by the ingress report. It is how the serve app learns which URL should receive site-down reports.

**Data flow**: It creates a new `APIRouter`, attaches one POST route at the internal site-report path, and points that route at `SiteReports._report`. The result is a router object that the larger serve application can mount into its web API.

**Call relations**: The serve application calls this during setup when it is assembling its HTTP routes. Once mounted, FastAPI calls `SiteReports._report` whenever the ingress posts a report to the configured path.

*Call graph*: 1 external calls (APIRouter).


##### `SiteReports._report`  (lines 130–154)

```
async def _report(self, authorization: Annotated[str, Header()]='') -> Response
```

**Purpose**: This is the serve-side endpoint that receives a site-down report, checks that it is genuine, and asks the conversation’s agent to investigate. It is private protocol glue between ingress and the turn system.

**Data flow**: It reads the `Authorization` header, removes the `Bearer` prefix, and verifies the signed ingress token for the special site-report kind. If the token is missing, expired, wrong, or forged, it returns an HTTP 401 error. If the token is valid, it enters the named workspace, looks up the agent for the conversation, and returns 404 if there is no such agent. Otherwise it calculates a time bucket, builds a stable idempotency key from the conversation, port, and bucket, and invokes the conversation with a plain instruction telling the agent that the site on that port did not answer. If the agent is archived, it treats the report as harmless and returns 204. On success it also returns 204, meaning there is no response body.

**Call relations**: FastAPI calls this when the route created by `SiteReports.router` receives a POST from `SiteReporter.report`. It hands the token to `verify_ingress_token`, uses `ws` to run inside the correct workspace, asks `conversation_agent_id` which agent owns the conversation, and uses `authority_from_member_id(None)` so the report is treated as a system observation rather than a particular member’s command. The actual repair work is not done here; this function hands the message to the workspace’s `TurnInvoker`, which folds it into the conversation flow.

*Call graph*: 7 external calls (now, HTTPException, Response, verify_ingress_token, authority_from_member_id, conversation_agent_id, ws).


### Site previews and surfacing
Hosted sites become visible in conversations and shared links through stored previews and safe user-facing presentation helpers.

### `core/src/ufo/runtime/media/site_previewer.py`

`io_transport` · `request handling`

This file exists so the system can show a visual preview of a web page created inside a sandbox. Think of it like sending a photographer to a temporary web address, asking for one picture at a specific size, and then filing that picture in storage.

The main class, SitePreviewer, is given a blob store, the preview service address, an access token, and the public ingress address used to reach sandboxed sites. When asked to render a preview, it first checks that the requested filename and image dimensions are safe and sensible. It then creates a temporary public URL for the sandbox page using the current workspace, the conversation id, and the requested port.

Next it decides how the preview image should come back. If the blob store is backed by S3, it gives the preview service a short-lived upload URL so the service can write the image directly to storage. Otherwise, it asks the service to return the PNG bytes inline in the HTTP response. In both cases it carefully limits response size, checks for errors, verifies that the returned image matches the requested dimensions, and makes sure inline data is really a PNG.

If anything goes wrong, it logs a short failure message and returns no preview instead of crashing the caller.

#### Function details

##### `SitePreviewer.render`  (lines 47–124)

```
async def render(self, conversation_id: UUID, port: int, name: str, width: int, height: int) -> StoredPreview | None
```

**Purpose**: This asynchronous function captures one screenshot of a sandbox-hosted web page and stores it as a preview artifact. Callers use it when they have a conversation id, sandbox port, filename, and desired image size, and they want back a stored preview record or a clean failure.

**Data flow**: It receives a conversation id, port, preview name, width, and height. It validates the name and size, builds a public URL for the sandbox page, creates a unique storage key, and sends a request to the external preview service. If the storage backend supports direct S3 upload, it sends the service a temporary upload URL and expects small JSON metadata back. Otherwise, it expects the PNG image bytes in the response and then uploads those bytes itself. On success it returns a StoredPreview containing the blob key and size. On HTTP errors, bad data, oversized responses, invalid PNG content, or mismatched dimensions, it logs the problem and returns null.

**Call relations**: This is the file’s main working method. It calls ws_current to learn the active workspace, then mint_ingress_view_url to turn the sandbox port into a public page URL. It uses json.dumps to package the preview request, httpx.Timeout and httpx.AsyncClient to talk to the preview service, and StoredPreview to describe the saved result. If the preview cannot be drawn safely, it reports that through the project logger rather than handing an exception back to its caller.

*Call graph*: 9 external calls (__init__, AsyncClient, Timeout, dumps, PurePosixPath, log, mint_ingress_view_url, ws_current, uuid4).


### `extensions/sites/ufo_ext_sites/conversation_slot.py`

`domain_logic` · `request handling`

A conversation may have site objects attached to it, but the user interface needs a clean way to show them: names, links, timestamps, and proof that the user is authorized to view them. This file provides that bridge.

Think of it like a receptionist checking a guest list before handing out room keys. The conversation context says which items are visible. The file translates those visible item names into site names, asks the hosted-sites store for matching records, and then double-checks that each returned record still matches the expected name and generation. A generation is a version number; checking it helps avoid showing a stale or replaced authorization.

The main read path builds `ConversationSite` objects. Each one contains the site name, a public URL, creation and update times, and the matching authorization object name and generation. The file also enforces a maximum number of sites using `CONVERSATION_SITES_MAX`; if there are more than can be shown, it marks the result as truncated.

At the bottom, `SITES_SLOT` registers this behavior as a conversation slot provider. In plain terms, it tells the larger system: “There is a slot called Sites; here is how to count it and here is how to read its full contents.”

#### Function details

##### `_read`  (lines 13–46)

```
async def _read(ctx: ConversationSlotContext) -> SitesSlotPayload
```

**Purpose**: Builds the full payload for the conversation’s Sites slot. It finds which visible conversation items correspond to hosted sites, checks them against stored site records, and returns display-ready site entries with safe public URLs.

**Data flow**: It receives a conversation slot context containing the conversation id, visible items, workspace information, transaction, and public base URL. It first converts visible item names into expected site names and generations. It then asks the hosted-sites store for those site records, checks that each returned record still matches the expected authorization name and generation, converts the approved records into `ConversationSite` entries, and returns a `SitesSlotPayload`. If more authorized sites exist than the allowed maximum, the payload says it was truncated.

**Call relations**: This function is plugged into `SITES_SLOT` as the full read operation for the Sites section. During that read, it relies on the site object naming helpers to connect conversation-visible authorization objects to real site names, uses `HostedSites` to fetch the stored site rows, uses `site_url` to turn each stored site into a public link, and packages the result into SDK payload objects for the rest of the conversation system to display.

*Call graph*: 6 external calls (__init__, __init__, __init__, site_name_from_object, site_object_name, site_url).


##### `_summarize`  (lines 49–51)

```
async def _summarize(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: Provides a quick count for the Sites slot without loading full site details. It reports how many visible site-related items there are, capped at the maximum number the slot can show.

**Data flow**: It receives the conversation slot context and looks only at `ctx.visible_items`. It counts them up to `CONVERSATION_SITES_MAX`; if the count is zero, it returns `None` so the slot can appear empty or absent. Otherwise, it returns the count as a simple integer summary.

**Call relations**: This function is plugged into `SITES_SLOT` as the lightweight summary operation. The larger conversation system can call it when it only needs a badge or preview count, while `_read` is used later when the full list of site links is needed.


### `extensions/sites/ufo_ext_sites/share_card.py`

`domain_logic` · `site deploy and share-card generation`

When a site link is pasted into Slack, Discord, social media, or other apps, those apps often show a preview card. This file makes that card feel specific to the site. It creates a fixed-size image: a branded panel on the left, and a screenshot of the site on the right.

The work happens inside the site’s sandbox, which is the isolated environment where the hosted site already runs. That matters because the site can be reached locally there, and the browser needed to take screenshots is also there. The file does not use an image editor to paste pieces together. Instead, it writes a small HTML page that contains the brand panel and the site screenshot, asks headless Chrome or Chromium to draw that page, and then uses Pillow, an image library, only for the final JPEG encoding.

It is careful about timing. A page may not be ready at the browser’s normal “loaded” signal, so the screenshot code waits briefly for repeated frames and quiet network activity, with strict time limits so a stuck page cannot block deployment.

Failures are deliberately non-fatal. A share card is decoration. If anything goes wrong, the problem is logged and the site keeps whatever card it already had.

#### Function details

##### `card_page`  (lines 486–511)

```
def card_page(name: str, drawn: str) -> str
```

**Purpose**: Builds the HTML page that will become the final share card image. It includes the left branding panel, the site name, and a placeholder where the screenshot will later be inserted.

**Data flow**: It receives the site name and instructions for how the screenshot should be sized. It reads the bundled font and logo files, safely escapes the site name so it cannot break the HTML, fills the card template, and returns the finished HTML text with a screenshot token still in place.

**Call relations**: This is called by `_compose` when the system is ready to build a card from an existing screenshot. While building the page, it calls `_lockup` to get the logo markup, and uses base64 encoding and HTML escaping so the browser can draw the page safely and self-contained.

*Call graph*: calls 1 internal fn (_lockup); called by 1 (_compose); 2 external calls (b64encode, escape).


##### `_lockup`  (lines 514–518)

```
def _lockup() -> str
```

**Purpose**: Extracts the UFO logo SVG markup so it can be embedded directly into the card page. This avoids depending on a separate logo file at drawing time.

**Data flow**: It reads the bundled SVG logo file from disk, removes anything before the `<svg>` element, and returns just the drawable SVG markup.

**Call relations**: This is a helper for `card_page`. The card page asks for the logo body, then places it into the left branding panel.

*Call graph*: called by 1 (card_page).


##### `shot_command`  (lines 521–541)

```
def shot_command(*, url: str, width: int, height: int, scale: int, shot: str, root: str) -> str
```

**Purpose**: Builds the shell command that runs a headless browser screenshot job inside the sandbox. It packages the browser driver script, target URL, output path, size, and safety boundaries into one command string.

**Data flow**: It receives a URL, image dimensions, scale factor, output screenshot path, and sandbox root. It quotes paths and the URL so the shell reads them correctly, inserts the browser-driving Python code, and returns a command ready for the sandbox to run.

**Call relations**: `_shoot` calls this when it needs a screenshot. The returned command is handed to the sandbox shell, where it finds Chrome or Chromium, opens the page, waits for it to settle, and writes the screenshot.

*Call graph*: called by 1 (_shoot); 2 external calls (quote, shell_path).


##### `draw_from_page`  (lines 544–562)

```
async def draw_from_page(ctx: ToolContext, sites: HostedSites, conversation_id: UUID, name: str, port: int) -> None
```

**Purpose**: Creates a share card from the site’s live front page during a fresh deploy. This is the normal path for newly published sites.

**Data flow**: It receives the tool context, site storage object, site identifier, site name, and local port. It chooses a runtime screenshot path, asks `_shoot` to photograph the live site at the card’s right-side size, and if that succeeds asks `_compose` to build and store the final card.

**Call relations**: This is an outer entry point for card creation from a currently running page. It first relies on `_shoot` to capture the site, then passes that screenshot to `_compose` for final assembly and database update.

*Call graph*: calls 2 internal fn (_compose, _shoot).


##### `draw_from_stored_shot`  (lines 565–580)

```
async def draw_from_stored_shot(ctx: ToolContext, sites: HostedSites, conversation_id: UUID, name: str, blob_key: str) -> None
```

**Purpose**: Creates a share card for an older site that already has a stored page preview but does not yet have a share card. It is a compatibility path for sites deployed before this card feature existed.

**Data flow**: It receives the tool context, site storage object, site identifier, site name, and blob key for an existing screenshot. It downloads that stored screenshot into the sandbox. If the download or write fails, it logs the failure; otherwise it asks `_compose` to build a card using that older screenshot style.

**Call relations**: This function feeds `_compose` with an existing image instead of taking a new live screenshot. If it cannot place the stored image into the sandbox, it calls `_undrawn` so the failure is recorded without stopping the site.

*Call graph*: calls 2 internal fn (_compose, _undrawn).


##### `_shoot`  (lines 583–612)

```
async def _shoot(ctx: ToolContext, name: str, shot: str, *, url: str, width: int, height: int, scale: int) -> bool
```

**Purpose**: Takes one screenshot with a headless browser and reports whether it succeeded. It is used both for photographing the site itself and for photographing the final HTML card page.

**Data flow**: It receives the sandbox context, site name, output path, URL or file path, image size, and scale. It first empties the output file so old screenshots cannot be mistaken for new ones, runs the browser command from `shot_command`, checks the result, and returns `True` only if the shot was written successfully.

**Call relations**: `draw_from_page` uses `_shoot` to capture the live site. `_compose` uses it again to capture the finished card HTML. If either browser run fails, `_shoot` calls `_undrawn` to log the problem and lets the caller keep the existing card.

*Call graph*: calls 2 internal fn (_undrawn, shot_command); called by 2 (_compose, draw_from_page).


##### `_compose`  (lines 615–665)

```
async def _compose(ctx: ToolContext, sites: HostedSites, conversation_id: UUID, name: str, shot: str, drawn: str) -> None
```

**Purpose**: Turns a screenshot into a finished share card, stores it, and records it on the hosted site’s database row. This is the central assembly line for the feature.

**Data flow**: It receives the sandbox context, hosted-site store, site identifier, site name, screenshot path, and screenshot sizing rule. It writes the card HTML, inserts the screenshot into that HTML as a data URI, screenshots the completed card page, converts that PNG into a progressive JPEG, stores the JPEG as a preview artifact, and finally saves the blob key and digest on the site record.

**Call relations**: Both `draw_from_page` and `draw_from_stored_shot` hand their screenshots to `_compose`. Inside, it calls `card_page` to create the HTML, `_shoot` to render the card image, `ToolContext.store_preview` to store the final file, and `HostedSites.set_share_card` only after every earlier step has succeeded. On any problem, it calls `_undrawn` and leaves the existing site card unchanged.

*Call graph*: calls 5 internal fn (store_preview, _shoot, _undrawn, card_page, set_share_card); called by 2 (draw_from_page, draw_from_stored_shot).


##### `_undrawn`  (lines 668–669)

```
def _undrawn(name: str, detail: object) -> None
```

**Purpose**: Records that a share card could not be drawn. It keeps failures visible to operators while avoiding a hard failure for the hosted site.

**Data flow**: It receives the site name and an error detail. It turns the detail into text, trims it to a safe length, and writes a structured log event.

**Call relations**: This is the shared failure-reporting helper for `draw_from_stored_shot`, `_shoot`, and `_compose`. Those functions call it whenever card generation cannot continue, instead of raising an error that would block the site from being hosted.

*Call graph*: called by 3 (_compose, _shoot, draw_from_stored_shot); 1 external calls (log).


### Artifact storage and downloads
Shared artifact objects, preview metadata, signed URLs, and download routes turn stored blobs into controlled user-visible files.

### `core/src/ufo/host/kinds/artifacts.py`

`domain_logic` · `request handling`

An artifact is a file produced during a conversation and shared with others through `share_file`. This file is the read-and-delete doorway for those shared files. Without it, shared files would still exist in storage, but users and agents would not have a consistent way to find them, tell versions apart, download them, copy them back into a workspace, or delete all their stored bytes.

The main idea is simple: one artifact object means “this filename from this conversation.” If the same conversation shares the same filename again, that becomes a new version of the same artifact. If another conversation shares the same filename, it is a different artifact. The file creates stable names by combining a short conversation prefix with a cleaned-up filename, like labeling folders by both “which meeting” and “which document.”

Most of the work lives in `ArtifactObjects`. It builds database queries that stay inside the current workspace, selected agent, and reader’s allowed audience. It can list artifacts with filters, fetch one artifact’s details, copy small files back into the active workspace during status checks, mint short-lived signed download links, and delete every version plus any preview blobs. It deliberately refuses create and update, because artifacts are only born by sharing an existing workspace file.

#### Function details

##### `artifact_media`  (lines 84–98)

```
def artifact_media(media_type: str) -> str
```

**Purpose**: Sorts a file’s MIME type, which is the standard label for file format, into a broad category: image, document, or other. This gives listings a simple filter such as `media=image` instead of making callers understand many detailed file types.

**Data flow**: It receives a media type string, lowers its case, and checks it against known image, text, office document, PDF, and Word patterns. It returns one short label: `image`, `document`, or `other`.

**Call relations**: When `ArtifactObjects._row` builds a listing row, it asks this helper for the friendly category that goes into the row’s searchable fields.

*Call graph*: called by 1 (_row); 1 external calls (is_text_media).


##### `_document_media`  (lines 101–107)

```
def _document_media() -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database version of the “is this a document?” test. It is used when filtering artifact listings without first loading every row into Python.

**Data flow**: It reads the `media_type` column from the shared artifact table, lowercases it in the database query, and produces a true-or-false SQL condition for text, office, PDF, and Word document types. The output is not a Python boolean yet; it is a condition the database can apply.

**Call relations**: `ArtifactObjects._groups` uses this when a listing asks for `media=document` or when it needs to exclude documents for `media=other`.

*Call graph*: called by 1 (_groups); 1 external calls (or_).


##### `_member_participated`  (lines 110–122)

```
def _member_participated() -> sa.ColumnElement[bool]
```

**Purpose**: Creates a database condition that says: only show shares made after a signed-in member had entered that conversation. This protects people from seeing earlier files from a conversation they were not yet part of.

**Data flow**: It compares turns in the same workspace and conversation, looking for a member-admission turn whose sequence number is not later than the artifact’s turn. It returns a SQL existence check that can be added to a larger query.

**Call relations**: `ArtifactObjects._member_shares` adds this condition to the normal share query whenever the portal is building member-facing artifact views.

*Call graph*: called by 1 (_member_shares); 2 external calls (literal, select).


##### `artifact_object_names`  (lines 125–145)

```
def artifact_object_names(shares: Iterable[tuple[UUID, str]]) -> dict[tuple[UUID, str], str]
```

**Purpose**: Creates the public object name for each distinct artifact identity, where identity means conversation plus filename. It keeps names readable while still avoiding mix-ups when two identities would otherwise get the same name.

**Data flow**: It receives conversation-and-filename pairs, removes duplicates, makes a base name from the conversation UUID prefix and a cleaned filename slug, then counts collisions. If two different identities land on the same base name, it appends a short digest so each final name is unique.

**Call relations**: `ArtifactObjects._identities` calls this after reading all visible artifact identities from the database, so later list and get operations can use the same stable names.

*Call graph*: calls 2 internal fn (_identity_digest, _slug); called by 1 (_identities); 1 external calls (Counter).


##### `_slug`  (lines 148–150)

```
def _slug(filename: str) -> str
```

**Purpose**: Turns a filename into a short, URL-like name part. This makes object names easier to read and safer to display.

**Data flow**: It receives a filename, lowercases it, replaces runs of non-letter-or-number characters with hyphens, trims extra hyphens, and cuts it to the maximum length. If nothing usable remains, it returns the fallback word `artifact`.

**Call relations**: `artifact_object_names` uses this helper while building the readable part of each artifact object name.

*Call graph*: called by 1 (artifact_object_names).


##### `_identity_digest`  (lines 153–155)

```
def _identity_digest(identity: tuple[UUID, str]) -> str
```

**Purpose**: Makes a stable fingerprint for a conversation-and-filename identity. It is only used when two readable names collide and need a small extra suffix.

**Data flow**: It receives a conversation UUID and filename, combines them into a string, hashes that string with SHA-256, and returns the hexadecimal hash text. Callers usually use only the first few characters.

**Call relations**: `artifact_object_names` calls this when a collision means the plain conversation-prefix-and-slug name is not enough.

*Call graph*: called by 1 (artifact_object_names); 1 external calls (sha256).


##### `ArtifactObjects.list`  (lines 180–187)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists artifact objects visible to the current tool or agent context. It is the normal object-list operation for artifacts.

**Data flow**: It reads the caller’s allowed subjects and member identity from the tool context, builds the base share query, gathers matching rows, and wraps them into a paged result. It returns an `ObjectPage` containing artifact rows and paging information.

**Call relations**: The object system calls this when someone lists artifacts. It delegates the real row building to `_rows`, gets the database query from `_shares`, and uses `object_page` to shape the final page.

*Call graph*: calls 2 internal fn (_rows, _shares); 2 external calls (authority_member_id, object_page).


##### `ArtifactObjects.member_page`  (lines 189–208)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Builds the member-facing Artifacts page for one signed-in member. It applies the extra rule that members only see files shared after they entered the relevant conversation.

**Data flow**: It starts from the member ID, computes the audience subjects tied to that member’s conversation, builds a member-restricted share query, turns the matches into rows, and returns them as a page. The `admin` argument is accepted, but this function still keeps the same audience fence.

**Call relations**: Portal-facing code calls this for the Artifacts index. It uses `conversation_audience` and `audience_subjects` to find the allowed subjects, `_member_shares` for the protected query, `_rows` for row construction, and `object_page` for paging.

*Call graph*: calls 2 internal fn (_member_shares, _rows); 3 external calls (object_page, audience_subjects, conversation_audience).


##### `ArtifactObjects.get`  (lines 210–212)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ArtifactSpec] | None
```

**Purpose**: Returns the detailed description of one artifact by name for the normal object interface. It does not copy bytes into the workspace; it only returns metadata.

**Data flow**: It receives a context and artifact name, searches visible shares for that name, and if found turns the group of versions into an object detail. If the name is not visible or does not exist, it returns `None`.

**Call relations**: The object system calls this for an artifact get operation. It relies on `_find` to resolve the name through `_shares`, then hands the result to `_detail`.

*Call graph*: calls 3 internal fn (_find, _shares, _detail).


##### `ArtifactObjects.member_detail`  (lines 214–231)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[ArtifactSpec] | None
```

**Purpose**: Returns one member-visible artifact with both its listing row and detailed metadata. It is the single-artifact companion to the member Artifacts page.

**Data flow**: It computes the member’s allowed subjects, finds the named artifact within member-visible shares, looks up the source link for that conversation, and builds a `MemberObject`. If the artifact is not visible, it returns `None`.

**Call relations**: Portal code calls this when a signed-in member opens one artifact. It uses the same audience helpers as `member_page`, `_member_shares` and `_find` for visibility, `_sources` for origin information, `_row` for the list-style data, and `_detail` for the full object detail.

*Call graph*: calls 5 internal fn (_find, _member_shares, _row, _sources, _detail); 3 external calls (__init__, audience_subjects, conversation_audience).


##### `ArtifactObjects.status`  (lines 233–277)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports the current state of an artifact and, for small enough files, copies its latest bytes back into the active workspace. This is how a later turn can reuse a file that an earlier turn shared.

**Data flow**: It receives the context and artifact name, finds all visible versions, reads the newest file bytes from blob storage if the file is below the materialization size limit, double-checks that the conversation is still visible, writes the bytes to `artifacts/<name>/<filename>` when possible, and optionally mints a fresh signed download link. It returns size, share time, turn ID, version count, download URL, and workspace path, or `None` if no artifact is found.

**Call relations**: The object system calls this during status or object-get flows that need a workspace copy. It uses `_find` and `_shares` to locate the artifact, `_unchanged_visible` to guard against permission changes, the blob store to read bytes, the sandbox to write a file, and artifact URL helpers to create a temporary link.

*Call graph*: calls 3 internal fn (_find, _shares, _unchanged_visible); 6 external calls (__init__, now, workspace_tx, artifact_url_expiry, mint_artifact_url, ws_current).


##### `ArtifactObjects.apply`  (lines 279–288)

```
async def apply(self, ctx: ToolContext, name: str, spec: ArtifactSpec, old: ArtifactSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to create or update artifacts through the object interface. Artifacts must come from sharing a file, not from directly writing an artifact object.

**Data flow**: It receives the requested name, new spec, old spec, and generation information, but does not use them to change anything. It always raises `VerbNotSupported` with guidance to use `share_file` instead.

**Call relations**: The object system would call this for create or update operations. This store intentionally stops that path immediately rather than handing work to any lower-level writer.

*Call graph*: 1 external calls (__init__).


##### `ArtifactObjects.delete`  (lines 290–316)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Deletes an artifact completely, including every version of the shared file and any preview blobs. This makes old signed links stop working because the underlying blobs are gone.

**Data flow**: It receives a context and name, finds all visible versions, locks and checks that the artifact’s latest visible conversation has not changed, deletes the matching database rows, verifies the expected number of versions were removed, then deletes each stored blob and preview blob. It returns nothing unless something is missing or changes during deletion, in which case it raises an error.

**Call relations**: The object system calls this for artifact delete operations. It uses `_find`, `_shares`, and `_unchanged_visible` to make sure it is deleting the intended visible object, SQL deletion inside `workspace_tx` for the records, and the blob service for the stored bytes.

*Call graph*: calls 3 internal fn (_find, _shares, _unchanged_visible); 3 external calls (delete, workspace_tx, ws_current).


##### `ArtifactObjects._unchanged_visible`  (lines 318–325)

```
def _unchanged_visible(self, ctx: ToolContext, latest: sa.Row) -> sa.Select
```

**Purpose**: Builds a database check that the latest artifact version still belongs to the same visible conversation. This prevents acting on a stale view if audience or conversation details changed mid-operation.

**Data flow**: It receives the current context and the latest share row, then creates a SQL query matching the current workspace, selected agent, conversation ID, audience, and allowed subjects. The output is a select statement that returns a row only if the artifact is still visible in the same way.

**Call relations**: `status` uses this before writing bytes into the workspace, and `delete` uses it with a lock before removing rows, so both operations confirm visibility at the moment they act.

*Call graph*: called by 2 (delete, status); 3 external calls (select, object_agent_id, ws_current).


##### `ArtifactObjects._rows`  (lines 327–338)

```
async def _rows(self, subjects: frozenset[str], viewer: UUID | None, query: ObjectListQuery, shares: sa.Select) -> tuple[ObjectRow, ...]
```

**Purpose**: Turns a filtered set of shares into the rows shown in artifact listings. It also adds each conversation’s opening source when available.

**Data flow**: It receives allowed subjects, an optional viewer member ID, listing filters, and a base share query. It groups matching shares, finds source links for the involved conversations, converts each group into an `ObjectRow`, and returns the rows as a tuple.

**Call relations**: `list` and `member_page` call this after choosing the correct visibility rules. It delegates filtering and grouping to `_groups`, source lookup to `_sources`, and row formatting to `_row`.

*Call graph*: calls 3 internal fn (_groups, _row, _sources); called by 2 (list, member_page).


##### `ArtifactObjects._find`  (lines 340–360)

```
async def _find(self, subjects: frozenset[str], name: str, shares: sa.Select) -> tuple[sa.Row, ...] | None
```

**Purpose**: Resolves one artifact name to all visible versions of that artifact. It searches by the stable object name rather than by raw database ID.

**Data flow**: It receives allowed subjects, an object name, and a share query. It first builds the full map of visible conversation-and-filename identities to names, finds the identity with the requested name, queries all matching shares, and sorts versions newest first. It returns the version rows or `None` if the name does not resolve or has no rows.

**Call relations**: `get`, `member_detail`, `status`, and `delete` all rely on this before doing their specific work. It calls `_identities` so name resolution stays consistent with listing names, then applies the supplied share projection.

*Call graph*: calls 1 internal fn (_identities); called by 4 (delete, get, member_detail, status); 2 external calls (where, workspace_tx).


##### `ArtifactObjects._groups`  (lines 362–429)

```
async def _groups(self, subjects: frozenset[str], viewer: UUID | None, query: ObjectListQuery, shares: sa.Select) -> Sequence[tuple[str, tuple[sa.Row, ...]]]
```

**Purpose**: Applies listing search and filters, scans recent matching shares, and groups them into artifact objects by conversation plus filename. This is the bridge between many share rows and one row per artifact object.

**Data flow**: It starts with a base share query and narrows it by text search, conversation ID, ownership, attachment status, surface, and broad media category. It orders by newest share, limits the scan, reads rows from the database, groups them by identity, attaches stable names from `_identities`, sorts each group by newest version, and returns named groups sorted by name.

**Call relations**: `_rows` calls this whenever a listing is being built. It uses `_document_media` for document filtering, `_identities` for stable names, and `workspace_tx` to run the final database query.

*Call graph*: calls 2 internal fn (_identities, _document_media); called by 1 (_rows); 5 external calls (false, not_, or_, workspace_tx, UUID).


##### `ArtifactObjects._identities`  (lines 431–455)

```
async def _identities(self, subjects: frozenset[str]) -> dict[tuple[UUID, str], str]
```

**Purpose**: Finds every distinct visible artifact identity and assigns each one its stable object name. This makes sure names are based on the whole visible set, not just the current listing page.

**Data flow**: It receives allowed subjects, queries the current workspace for distinct conversation IDs and filenames belonging to the selected agent and allowed audiences, and passes those pairs to `artifact_object_names`. It returns a mapping from each identity to its object name.

**Call relations**: `_find` uses this to resolve a name to the right file, and `_groups` uses it to label listing groups. It is the shared naming source that keeps get and list in agreement.

*Call graph*: calls 1 internal fn (artifact_object_names); called by 2 (_find, _groups); 4 external calls (select, workspace_tx, object_agent_id, ws_current).


##### `ArtifactObjects._shares`  (lines 457–494)

```
def _shares(self, subjects: frozenset[str]) -> sa.Select
```

**Purpose**: Builds the base database query for visible shared artifact file rows. It includes the metadata needed later for listing, details, downloads, previews, and ownership display.

**Data flow**: It receives allowed audience subjects and creates a SQL select joining shared artifacts to their turn, conversation, and optional member owner. The query is restricted to the current workspace, actual file shares, the selected agent, and allowed audiences. It returns the query object, not the rows themselves.

**Call relations**: `list`, `get`, `status`, and `delete` call this directly for normal object access. `_member_shares` builds on it by adding the extra member-participation rule.

*Call graph*: called by 5 (_member_shares, delete, get, list, status); 3 external calls (select, object_agent_id, ws_current).


##### `ArtifactObjects._member_shares`  (lines 496–497)

```
def _member_shares(self, subjects: frozenset[str]) -> sa.Select
```

**Purpose**: Builds the base share query for member-facing artifact views. It starts with normal visibility and adds the rule that the member must have already joined the conversation.

**Data flow**: It receives allowed subjects, calls `_shares` to build the usual query, then adds the `_member_participated` database condition. It returns the narrowed query.

**Call relations**: `member_page` and `member_detail` use this so the portal’s member view does not expose files from before the member’s admission to a conversation.

*Call graph*: calls 2 internal fn (_shares, _member_participated); called by 2 (member_detail, member_page).


##### `ArtifactObjects._sources`  (lines 499–536)

```
async def _sources(self, conversation_ids: Sequence[UUID]) -> dict[UUID, str | None]
```

**Purpose**: Looks up the opening source for each conversation, such as the permalink or place where the conversation began. This helps listings explain where an artifact came from.

**Data flow**: It receives conversation IDs, finds the earliest turn for each conversation in the current workspace, reads its stored context, validates that context as a `TurnContext`, and extracts the `source` field. It returns a dictionary from conversation ID to source string or `None`.

**Call relations**: `_rows` calls this for all conversations in a listing, and `member_detail` calls it for the one conversation being opened. `_row` then places the source value into the visible fields.

*Call graph*: called by 2 (_rows, member_detail); 5 external calls (model_validate, and_, select, workspace_tx, ws_current).


##### `ArtifactObjects._row`  (lines 538–567)

```
def _row(self, name: str, shares: tuple[sa.Row, ...], viewer: UUID | None, sources: dict[UUID, str | None]) -> ObjectRow
```

**Purpose**: Formats one artifact group into the compact row shown in listings. It gathers the newest version’s visible facts, friendly labels, ownership flags, and optional links.

**Data flow**: It receives the object name, all version rows for that artifact, the viewer member ID if any, and source values by conversation. It picks the newest share, builds a summary, fills fields such as filename, subject, conversation, media category, size, owner email, origin, source, whether it is mine, attachment status, download URL, and preview URL, then returns an `ObjectRow`.

**Call relations**: `_rows` uses this for each listed artifact group, and `member_detail` uses it for the member object row. It calls `_summary`, `artifact_media`, `_download_url`, and `_preview_url` to fill specific pieces.

*Call graph*: calls 4 internal fn (_download_url, _preview_url, _summary, artifact_media); called by 2 (_rows, member_detail); 1 external calls (__init__).


##### `ArtifactObjects._download_url`  (lines 569–578)

```
def _download_url(self, latest: sa.Row) -> str | None
```

**Purpose**: Creates a public signed download URL for the latest artifact version, when this deployment is configured to make such links. A signed URL is a temporary link that proves permission without exposing the secret itself.

**Data flow**: It receives the latest share row. If either the signing secret or public base URL is missing, it returns `None`; otherwise it mints a path for the blob with an expiry time and prefixes it with the public base URL.

**Call relations**: `_row` calls this while building listing fields, so portals can show a download link beside each artifact when link minting is enabled.

*Call graph*: called by 1 (_row); 4 external calls (now, artifact_url_expiry, mint_artifact_url, ws_current).


##### `ArtifactObjects._preview_url`  (lines 580–600)

```
def _preview_url(self, latest: sa.Row) -> str | None
```

**Purpose**: Creates a signed image preview URL when the artifact has a safe raster image preview. Raster means a pixel-based image, such as PNG or JPEG, rather than an editable document format.

**Data flow**: It chooses the preview blob if one exists, otherwise the original file blob. It checks that the blob key’s image type matches the declared media type; if not, it returns `None`. If the check passes, it mints and returns an image preview URL using the blob key, size, and workspace.

**Call relations**: `_row` calls this so listing rows can include a preview thumbnail link. It hands off to image-preview and artifact-URL helpers for type checking and URL creation.

*Call graph*: called by 1 (_row); 3 external calls (mint_image_preview_url, raster_image_media_type, ws_current).


##### `_detail`  (lines 603–619)

```
def _detail(shares: tuple[sa.Row, ...]) -> ObjectDetail[ArtifactSpec]
```

**Purpose**: Builds the full object detail for an artifact group. It describes the latest version while preserving when the artifact was first created and last updated.

**Data flow**: It receives all version rows sorted newest first, picks the newest row for the spec, uses the oldest row’s creation time as `created_at`, uses the newest time as `updated_at`, and adds a link back to the conversation where the artifact was created. It returns an `ObjectDetail` containing an `ArtifactSpec`.

**Call relations**: `ArtifactObjects.get` and `ArtifactObjects.member_detail` call this after `_find` has located the artifact versions.

*Call graph*: called by 2 (get, member_detail); 4 external calls (__init__, __init__, __init__, __init__).


##### `_summary`  (lines 622–628)

```
def _summary(shares: tuple[sa.Row, ...]) -> str
```

**Purpose**: Creates a short human-readable summary for an artifact listing row. It lets people quickly recognize the file without opening its full details.

**Data flow**: It receives the artifact’s version rows, reads the newest filename, media type, size, share date, and version count, formats them into one sentence, and trims the result to the maximum summary length.

**Call relations**: `ArtifactObjects._row` calls this when constructing each `ObjectRow` for listings and member detail rows.

*Call graph*: called by 1 (_row).


##### `artifact_object`  (lines 631–693)

```
def artifact_object(*, public_base_url: str | None=None, artifact_token_secret: str='') -> ObjectKind
```

**Purpose**: Registers the artifact object kind with the object system. It tells the rest of the application what artifacts are, what fields they expose, which actions are allowed, and which store object performs those actions.

**Data flow**: It receives optional deployment settings for public URLs and signing secrets, constructs an `ArtifactObjects` store with them, and returns an `ObjectKind` containing the name, description, user guidance, spec model, list fields, and allowed agent verbs. The resulting object kind can list, get, and delete artifacts, but not create or update them.

**Call relations**: Startup or object-kind registration code calls this when assembling available object kinds. It is the factory that connects the `ArtifactObjects` implementation in this file to the broader runtime object system.

*Call graph*: 2 external calls (__init__, __init__).


### `core/src/ufo/runtime/media/__init__.py`

`other` · `import/package discovery`

This file is intentionally empty, but it still has a job. In Python, an `__init__.py` file tells the interpreter that a folder should be treated as an importable package. You can think of it like a label on a drawer: the drawer may not contain instructions itself, but the label lets the rest of the system find what is stored inside. Here, the drawer is `ufo.runtime.media`, which likely contains code related to media handling elsewhere in this directory. Without this file, depending on the Python version and packaging setup, imports that expect `ufo.runtime.media` to be a normal package could fail or behave differently. Because it contains no code, it does not run any setup, define any helpers, or change program behavior directly. Its value is structural: it keeps the package layout clear and import-friendly.


### `core/src/ufo/runtime/media/artifact_url.py`

`domain_logic` · `artifact sharing and download request handling`

This file is the gatekeeper for artifact download URLs. An artifact is a stored file-like object, and the URL for it carries both an address, such as which artifact and filename to fetch, and a signed grant, which is proof that the server created the link. The signature is made with a secret known only to the deployment, much like a wax seal on a letter: if someone changes the workspace, expiry time, artifact id, or preview permission, the seal no longer matches and the file is not served.

The file also decides safe details around delivery. It chooses a media type, which tells browsers what kind of file they are receiving. It has its own fixed list for important file extensions because the operating system’s built-in guesses can differ between a developer laptop and production. It also marks which media types are safe to show as text.

A key design choice is that expiry times are rounded into buckets. That means repeated links for the same artifact during the same time window are byte-for-byte identical, so browsers and edge caches can reuse them instead of refetching every time. Image previews get extra signed claims saying the file is a raster image and exactly how large it should be, so the serving route can validate the bytes before showing them inline.

#### Function details

##### `is_text_media`  (lines 72–79)

```
def is_text_media(media_type: str) -> bool
```

**Purpose**: Decides whether a media type represents text that can reasonably be shown inline to a reader. It covers all normal text types plus common code and data formats that browsers may label as application files.

**Data flow**: It receives a media type string, lowercases it, then checks whether it starts with `text/` or exactly matches one of the approved application-style text types. It returns `true` for readable text formats and `false` for everything else.

**Call relations**: Other parts of the product can use this as the shared source of truth when deciding whether artifact bytes should be displayed as text instead of treated as a download or binary file.


##### `artifact_url_expiry`  (lines 86–93)

```
def artifact_url_expiry(now: datetime) -> int
```

**Purpose**: Calculates the expiry timestamp to place in a signed artifact URL. Instead of expiring at the exact second, it rounds up to a shared time bucket so repeated links can stay identical and cache-friendly.

**Data flow**: It receives the current time, converts it to seconds since the Unix epoch, adds the minimum allowed lifetime, then rounds up to the next expiry bucket. It returns that rounded timestamp as an integer.

**Call relations**: When `mint_image_preview_url` needs a fresh preview link, it calls this first to choose the expiry time before handing that time to `mint_artifact_url` for signing.

*Call graph*: called by 1 (mint_image_preview_url); 1 external calls (timestamp).


##### `ArtifactUrlExpired.__init__`  (lines 115–117)

```
def __init__(self, claims: ArtifactClaims) -> None
```

**Purpose**: Creates a special error for a URL whose signature is valid but whose time has run out. It keeps the verified claims attached so a caller can decide whether a signed-in workspace member is allowed to refresh the link.

**Data flow**: It receives already-verified artifact claims, builds an error message saying the artifact URL is expired, and stores those claims on the exception object. The output is an exception instance that carries useful recovery information.

**Call relations**: `verify_artifact_url` uses this when a link is either past its expiry time or lacks a workspace claim from an older URL format. The caller can catch this more specific error instead of treating the URL as simply forged or malformed.

*Call graph*: called by 1 (verify_artifact_url).


##### `artifact_media_type`  (lines 120–135)

```
def artifact_media_type(filename: str) -> str
```

**Purpose**: Chooses the media type to serve for a file based on its filename. It avoids relying only on the host machine’s MIME database, because that database can vary and lead to different behavior in development and production.

**Data flow**: It receives a filename, looks first in the project’s own extension-to-media-type table, and returns that value if present. Otherwise it asks Python’s `mimetypes` library for a guess, but only accepts it when there is no separate compression encoding; if nothing safe is known, it returns `application/octet-stream`, meaning generic binary data.

**Call relations**: Serving and preview code can call this when they need a stable answer for what kind of artifact is being delivered. Internally it uses path suffix parsing and Python’s media-type guessing only after the project’s fixed overrides have had first chance.

*Call graph*: 2 external calls (guess_type, PurePosixPath).


##### `mint_artifact_url`  (lines 138–161)

```
def mint_artifact_url(secret: str, blob_key: str, expires_at: int, *, workspace_id: UUID, preview: ImagePreviewGrant | None=None) -> str
```

**Purpose**: Builds a signed, relative download URL for one artifact blob inside a workspace. Someone would use it when they want to give a browser a temporary link that can be fetched anonymously but cannot be edited or reused for another artifact.

**Data flow**: It receives the signing secret, the artifact blob key, an expiry timestamp, the workspace id, and optionally an image preview grant. It checks that a secret exists, splits and validates the blob key, encodes any preview claim, signs the workspace id, artifact id, expiry, and preview value, then returns a URL path with query parameters containing the expiry, workspace id, signature, and optional preview claim.

**Call relations**: `mint_image_preview_url` calls this after it has decided an image is eligible for inline preview. This function relies on `_split_key` to ensure the address is really inside the artifact namespace, `_parsed_preview` to double-check preview claims, `_signed_message` to create the exact bytes being signed, and `sign_detached` to produce the tamper-proof signature.

*Call graph*: calls 3 internal fn (_parsed_preview, _signed_message, _split_key); called by 1 (mint_image_preview_url); 3 external calls (__init__, sign_detached, quote).


##### `mint_image_preview_url`  (lines 164–192)

```
def mint_image_preview_url(secret: str, public_base_url: str | None, blob_key: str, size_bytes: int | None, *, workspace_id: UUID) -> str | None
```

**Purpose**: Creates an absolute signed URL for showing a stored raster image preview inline, or returns nothing if preview delivery is not allowed. It is a safe front door for preview links because it checks file type, file size, and public URL configuration before minting anything.

**Data flow**: It receives the signing secret, the public base URL, a blob key, the stored file size, and the workspace id. If the secret or public base URL is missing, the file is not a supported raster image, the size is unknown, or the size is too large, it returns `null`. Otherwise it calculates a bucketed expiry, creates an image preview grant, asks `mint_artifact_url` for the signed path, prefixes the public base URL, and returns the full URL.

**Call relations**: This is the convenience path for producers that want image previews. It calls `raster_image_media_type` to identify eligible images, `artifact_url_expiry` to choose the expiration time, and `mint_artifact_url` to do the actual signing.

*Call graph*: calls 2 internal fn (artifact_url_expiry, mint_artifact_url); 3 external calls (__init__, now, raster_image_media_type).


##### `verify_artifact_url`  (lines 195–236)

```
def verify_artifact_url(secret: str, artifact_id: str, filename: str, expires_at: str, signature: str, preview: str, workspace: str, now: datetime) -> ArtifactClaims
```

**Purpose**: Checks whether an artifact URL proves what it claims: which artifact, which workspace, when it expires, and whether it includes a valid preview permission. It rejects malformed, forged, expired, or unsafe artifact addresses before bytes are served.

**Data flow**: It receives the secret, artifact id, filename, expiry string, signature, preview string, workspace string, and current time. It validates the id, filename, expiry, workspace id, and preview claim, rebuilds the exact signed message, and verifies the signature. If everything is authentic, it builds `ArtifactClaims`; if the URL has no workspace claim or has expired, it raises `ArtifactUrlExpired` with those claims, otherwise it returns the claims for serving.

**Call relations**: The artifact download route calls this when a request arrives. Inside, it uses `_is_canonical_uuid` and `_is_filename` for safe shape checks, `_parsed_preview` for preview claims, `_signed_message` to reconstruct what should have been signed, and `verify_detached` to confirm the signature matches.

*Call graph*: calls 5 internal fn (__init__, _is_canonical_uuid, _is_filename, _parsed_preview, _signed_message); 5 external calls (__init__, __init__, timestamp, verify_detached, UUID).


##### `_signed_message`  (lines 239–241)

```
def _signed_message(workspace: str, artifact_id: str, expires_at: str, preview_value: str) -> bytes
```

**Purpose**: Creates the exact byte string that is signed when minting a URL and later reconstructed when verifying it. This matters because signing only works if both sides agree on the message character for character.

**Data flow**: It receives the workspace string, artifact id, expiry string, and preview value. It joins them in a fixed colon-separated format, omitting the workspace prefix for older claim-less URLs, then encodes the result as bytes.

**Call relations**: `mint_artifact_url` uses this before creating a signature, and `verify_artifact_url` uses it again before checking that signature. It is the shared recipe that keeps signing and verification in lockstep.

*Call graph*: called by 2 (mint_artifact_url, verify_artifact_url).


##### `_parsed_preview`  (lines 244–255)

```
def _parsed_preview(value: str) -> ImagePreviewGrant | None
```

**Purpose**: Turns a preview claim string into a structured image preview grant, but only if the claim names an allowed raster image type and an acceptable byte size. This prevents a URL from pretending that arbitrary or oversized content is safe to render inline.

**Data flow**: It receives a string shaped like `media/type:size`, splits it at the final colon, checks that the media type is one of the supported raster image types, checks that the size is numeric and below the preview limit, and returns an `ImagePreviewGrant`. If any check fails, it returns `null`.

**Call relations**: `mint_artifact_url` calls this to reject an invalid preview grant before signing it, and `verify_artifact_url` calls it to understand and validate the preview value that arrived in a request.

*Call graph*: called by 2 (mint_artifact_url, verify_artifact_url); 3 external calls (__init__, cast, values).


##### `_split_key`  (lines 258–267)

```
def _split_key(blob_key: str) -> tuple[str, str]
```

**Purpose**: Checks that a blob key is exactly an artifact address and separates it into artifact id and filename. It prevents callers from signing paths outside the intended `artifacts/<id>/<filename>` area.

**Data flow**: It receives a blob key string, removes the artifact prefix, splits the remaining text into an artifact id and filename, then checks that the original key had the right prefix, the id is a canonical UUID, and the filename is safe. It returns the artifact id and filename, or raises an artifact URL error if the key is not valid.

**Call relations**: `mint_artifact_url` calls this before signing any URL. `_split_key` delegates the precise id and filename checks to `_is_canonical_uuid` and `_is_filename`, so only well-formed artifact addresses can be minted.

*Call graph*: calls 2 internal fn (_is_canonical_uuid, _is_filename); called by 1 (mint_artifact_url); 1 external calls (__init__).


##### `_is_canonical_uuid`  (lines 270–274)

```
def _is_canonical_uuid(value: str) -> bool
```

**Purpose**: Checks whether a string is a UUID in the project’s canonical written form. This avoids accepting alternate spellings that might sign or compare differently.

**Data flow**: It receives a string, tries to parse it as a UUID, then converts it back to a string and compares that with the original. It returns `true` only when parsing succeeds and the original already matched the canonical form; otherwise it returns `false`.

**Call relations**: `_split_key` uses this while minting links, and `verify_artifact_url` uses it when checking incoming URL parts. It is a small shared guardrail for artifact ids and workspace ids.

*Call graph*: called by 2 (_split_key, verify_artifact_url); 1 external calls (UUID).


##### `_is_filename`  (lines 277–278)

```
def _is_filename(value: str) -> bool
```

**Purpose**: Checks whether a filename is a simple single path segment. It rejects empty names, nested paths, and the special `.` and `..` names that could otherwise confuse path handling.

**Data flow**: It receives a filename string and checks that it is not empty, does not contain a slash, and is not `.` or `..`. It returns `true` for safe single filenames and `false` otherwise.

**Call relations**: `_split_key` uses this before minting a signed URL, and `verify_artifact_url` uses it before accepting a requested filename. Together, those checks keep artifact URLs from addressing anything outside the one intended blob.

*Call graph*: called by 2 (_split_key, verify_artifact_url).


### `core/src/ufo/runtime/media/previews.py`

`data_model` · `cross-cutting`

This file gives the rest of the system a clear, shared way to talk about a saved preview image. A preview is not stored here directly. Instead, the file defines a simple record, `StoredPreview`, that points to the preview's blob key, which is its workspace-relative storage name or path, and records its exact size in bytes. Think of it like a claim ticket for a coat: the ticket is not the coat, but it tells you where to find it and confirms what was stored. The `@dataclass` decorator means Python automatically creates the usual boilerplate for this kind of plain data object, such as construction and comparison. The class is marked as frozen, so once a `StoredPreview` is made, its fields cannot be changed. That helps prevent accidental mistakes, such as changing the storage key after another part of the system has already used it. Without this file, code that passes preview information around would have to use loose dictionaries or separate values, which would be easier to mix up and harder to understand.


### `core/src/ufo/runtime/surfaces/artifacts.py`

`io_transport` · `request handling`

This file is the gatekeeper for shared file downloads. A shared artifact is not served just because someone asks for its path. The request must carry a signed URL: a link containing proof, made with the server’s secret, that names the file, workspace, expiry time, and sometimes a preview option. Without this route, links produced by the app, chat surfaces, or Slack for shared files would have no safe way to turn into bytes.

The main route checks the signature first. If the link is invalid, it refuses the request. If the link is valid and current, it looks in the correct workspace’s blob store, which is the project’s storage area for file bytes. Normal downloads are streamed in chunks, so a large file does not have to sit fully in memory before being sent. The response also includes safe browser headers, including a rule that tells browsers not to guess the file type.

There is one helpful exception for expired links. If an expired link was originally real, a browser with a valid portal session cookie can prove the user is a member of the workspace that owns the file. In that case the code sends them to a freshly signed version of the same link. Think of it like an expired office badge: strangers stay locked out, but a current employee can get a new badge at the front desk.

#### Function details

##### `download`  (lines 59–117)

```
async def download(request: Request, artifact_id: str, filename: str, exp: str='', sig: str='', preview: str='', workspace: Annotated[str, Query(alias='ws')]='') -> Response
```

**Purpose**: This is the HTTP endpoint that turns a signed artifact link into the actual file response. It verifies that the link is allowed, finds the file in the right workspace storage, and either streams the download or returns a checked image preview.

**Data flow**: A web request comes in with an artifact id, filename, expiry value, signature, optional preview token, and workspace value. The function reads the blob store and artifact signing secret from the app, verifies the link, checks that the file exists, and then either validates and returns a preview image or streams the original file with download headers. If the link is expired, it does not serve bytes directly; it asks the refresh helper to decide whether the user can get a new link. If anything is invalid or missing, the output is an HTTP error instead of file data.

**Call relations**: This function is the front door for artifact downloads. It calls the artifact URL verifier before touching storage, calls `_refreshed_for_member` only when the verifier says the grant has expired, uses image preview validation when the signed claim asks for an inline preview, and uses media-type detection plus a streaming response when sending the normal file.

*Call graph*: calls 1 internal fn (_refreshed_for_member); 9 external calls (now, HTTPException, Response, StreamingResponse, artifact_media_type, verify_artifact_url, validated_image_preview, ws, quote).


##### `_refreshed_for_member`  (lines 120–171)

```
async def _refreshed_for_member(request: Request, claims: ArtifactClaims, secret: str) -> RedirectResponse
```

**Purpose**: This helper gives expired artifact links a safe second chance for signed-in workspace members. It refreshes the link only when the browser session proves the person belongs to the workspace that owns the shared file.

**Data flow**: It receives the current request, the already-verified expired artifact claims, and the signing secret. It reads the session cookie, checks the cookie claims, converts the workspace id, then queries the database to confirm two things: the user is a member of that workspace, and the requested blob belongs to a shared artifact in that workspace. If both checks pass, it creates a new expiry time, mints a fresh signed artifact URL, and returns a redirect to that URL. If any check fails, it returns the refusal behavior instead.

**Call relations**: This helper is called by `download` when a signed link is genuine but too old. It relies on `_refusal` whenever the browser is not signed in, the session is malformed, membership is missing, or the artifact belongs somewhere else. When the user is allowed, it hands control back to the browser through a redirect to a newly minted URL.

*Call graph*: calls 1 internal fn (_refusal); called by 1 (download); 10 external calls (now, RedirectResponse, or_, select, workspace_tx, verified_claims, artifact_url_expiry, mint_artifact_url, ws, UUID).


##### `_refusal`  (lines 174–179)

```
def _refusal(request: Request) -> HTTPException
```

**Purpose**: This small helper decides how to refuse an expired artifact link when it cannot be refreshed. Browser page requests are sent toward sign-in, while non-browser clients get a plain forbidden error.

**Data flow**: It receives the request and looks at the Accept header to see whether the client wants HTML. If it looks like a browser page, it builds a login URL that carries the original artifact URL as the target, then returns an HTTP redirect-style exception. Otherwise, it returns a forbidden HTTP exception with the expired-link message.

**Call relations**: This function is used by `_refreshed_for_member` whenever refresh is not currently allowed. It is the final branch in the expired-link flow: either the user is guided to sign in and come back, or the caller is told that the link can no longer be used.

*Call graph*: called by 1 (_refreshed_for_member); 2 external calls (HTTPException, quote).


### Document rendering support
Document preview plumbing renders uploaded files into inspectable pages and exposes the document extension package for import.

### `core/src/ufo/harness/document_renderer.py`

`io_transport` · `request handling`

Documents can be large, messy, and risky to process directly. This file acts like a careful mailroom clerk: it accepts document bytes, refuses anything too large, asks an external rendering service to turn the document into page images and extracted text, then checks the returned package before handing it to the rest of the system.

The main class is DocumentRenderer. Its public render method builds a request for the rendering service, including the document type, the requested page range, and image size limits. It sends the file over HTTP, using a bearer token for authorization. If the service reports an error, or if the returned bundle grows beyond a safe limit, it stops and raises an error.

The service returns a zip bundle. The private _unpack method opens that bundle and checks it very strictly. It validates the manifest, which is a small JSON file describing the pages, then confirms that every expected PNG image is present, correctly named, within size limits, and actually looks like a PNG file. It base64-encodes the images, meaning it turns the raw image bytes into text safe to place in a JSON-style result. The final output includes page images, extracted text, page counts, the next page to request, and a reminder to visually check document quality.

#### Function details

##### `DocumentRenderer.render`  (lines 60–107)

```
async def render(self, path: str, kind: str, content: bytes, start_page: int, limit: int) -> dict[str, object]
```

**Purpose**: This is the main entry point for rendering a document. It checks that the input document is not too large, asks the preview service to render a bounded range of pages, and returns the cleaned result.

**Data flow**: It receives a file path, document kind, raw document bytes, a starting page, and a page limit. It clamps the page range to safe values, sends the document and render request to the configured service URL, collects the returned zip bundle, and rejects oversized or failed responses. After the bundle is received, it passes the bytes to _unpack in a background thread and returns the dictionary that _unpack produces.

**Call relations**: When another part of the system needs document pages, it calls this method rather than talking to the preview service directly. This method uses httpx.AsyncClient and httpx.Timeout to make the HTTP request, json.dumps to encode the render request, and asyncio.to_thread to hand the CPU/blocking zip work off to _unpack without stalling the async flow.

*Call graph*: 4 external calls (to_thread, AsyncClient, Timeout, dumps).


##### `DocumentRenderer._unpack`  (lines 109–196)

```
def _unpack(self, path: str, kind: str, start_page: int, limit: int, bundle: bytes) -> dict[str, object]
```

**Purpose**: This function opens and verifies the renderer's returned zip bundle, then converts its page images and text into the final result format. It is deliberately strict so a bad, mismatched, or oversized renderer response cannot silently pass through.

**Data flow**: It receives the original path and document kind, the requested page range, and the zip bundle bytes. It opens the bundle, reads and validates manifest.json, checks that the listed page files are exactly the files present, confirms page numbers and dimensions make sense, enforces image and total size caps, verifies each page is a PNG, and base64-encodes each image. It returns a dictionary containing document metadata, joined extracted text, rendered page images, pagination information, and a quality reminder.

**Call relations**: This is called by DocumentRenderer.render after the HTTP response has been fully downloaded. It uses io.BytesIO and zipfile.ZipFile to read the in-memory zip archive, then base64.b64encode to turn binary PNG images into text suitable for the returned data structure.

*Call graph*: 3 external calls (b64encode, BytesIO, ZipFile).


### `extensions/documents/ufo_ext_documents/__init__.py`

`other` · `import/package discovery`

This is an empty Python package initializer. In Python, a file named `__init__.py` tells the language, tools, and readers that the surrounding folder should be treated as an importable package. You can think of it like a label on a drawer: the drawer may contain useful files, but the label itself does not do the work.

Because this file contains no code, it does not define functions, load settings, start services, or change program state. Its value is structural. Without it, some Python environments or packaging tools might not recognize `extensions/documents/ufo_ext_documents` as a package, which could make imports less predictable or break code that expects this package layout.

So this file matters not because of what it runs, but because of what it allows: it gives the document extension a clear package boundary and makes room for other modules in the same folder to be imported under the `ufo_ext_documents` name.

## 📊 State Registers Touched

- `reg-member-session-auth` — The signed tokens and browser/session identity state that prove who is making a request.
- `reg-sandbox-handles` — The remembered sandbox workspaces and conversation sandbox handles used to resume or clean up execution.
- `reg-egress-sandbox-policy` — The shared rules for sandbox network access, proxy behavior, internet permission, and exposed secrets.
- `reg-browser-sessions` — The active browser automation workbench for a turn, including Chrome sessions, tabs, and downloads.
- `reg-artifact-blob-store` — The shared files, media blobs, previews, metadata, and signed-download records created by agent work.
- `reg-hosted-site-registry` — The saved hosted-site names, owners, visibility, ports, files, previews, and ingress routing state.
