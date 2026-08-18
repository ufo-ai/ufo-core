# Creation workflows for sites, documents, code, and research  `stage-13.2`

This stage is part of the system’s main work loop, where the assistant stops just talking and starts making things. It covers several creation paths: building websites, publishing them, delegating web work to a specialist, and running broad research jobs.

The site surface is the front door for a hosted website. When someone opens a link, it checks whether they are allowed in, shows the site safely inside a protected frame, and lets the creator adjust viewing permissions. The site tools are the workshop controls: they build site files, start a local web server, publish it as a stable hosted link, and record that link. They also add guardrails, such as keeping file paths inside the workspace and checking that servers are actually running. The site store is the address book behind this, remembering which conversation owns each site and who can access it. The delegation file lets the main assistant hand website work to a child agent. The research tool similarly splits a list of research targets into parallel jobs and saves the combined answers as JSON.

## Files in this stage

### Website creation and hosting
Site-facing tools and pages delegate website builds, serve and publish local work, enforce access rules, and persist hosted-site ownership and visibility records.

### `extensions/sites/ufo_ext_sites/surface.py`

`domain_logic` · `request handling`

A hosted site link is meant to be shareable, but the link itself is not treated as proof that someone may view the site. This file is the gatekeeper for those links. It reads the token in the URL, finds which workspace and site the token points to, checks the viewer's session cookie, and then applies the site's visibility rule: public, workspace-only, or private to the creator.

If the token is bad, the site is missing, or the viewer is not allowed, the response is deliberately bland: usually the same “no such site” message. That matters because the page should not become a guessing tool that reveals whether private sites exist. If a viewer is not signed in and the site is not public, it shows a simple sign-in page instead.

The actual site files are not served here. This file returns a wrapper page with an iframe, which is like a window inside the page, pointing at the site's separate serving origin. That separation helps keep the embedded site's scripts away from the app's own cookies and controls. When the creator is viewing the page, the wrapper also shows a visibility selector. Changing that setting requires a CSRF token, which is a signed proof tied to the viewer's own session so another website cannot silently submit the form.

#### Function details

##### `site_token`  (lines 96–105)

```
def site_token(workspace_id: UUID, conversation_id: UUID, name: str) -> str
```

**Purpose**: Creates the permanent signed token used in a hosted site URL. The token names a workspace, a conversation, and a site name, so the system can later find the right site from the link alone.

**Data flow**: It takes a workspace ID, conversation ID, and site name → puts them into signed token claims for the sites surface → returns the token string that can be placed in a URL.

**Call relations**: When a full link is being built, site_url calls this helper first. It hands the real signing work to mint_surface_token, which produces the tamper-resistant token.

*Call graph*: called by 1 (site_url); 1 external calls (mint_surface_token).


##### `site_url`  (lines 108–118)

```
def site_url(public_base_url: str | None, workspace_id: UUID, conversation_id: UUID, name: str) -> str
```

**Purpose**: Builds the shareable web address for a hosted site. It refuses to guess if the deployment has no public base URL, because a site link would not actually be openable.

**Data flow**: It receives the deployment's public base URL plus the workspace, conversation, and site name → checks that the base URL exists → creates a site token → returns a URL made from the base path, the sites frame path, and the token. If no base URL is configured, it raises SiteHostingUnconfigured instead.

**Call relations**: This is the producer of hosted site links. It calls site_token to make the address token, then combines that token with the configured public URL.

*Call graph*: calls 1 internal fn (site_token); 1 external calls (__init__).


##### `site_address`  (lines 121–132)

```
def site_address(token: str) -> SiteAddress | None
```

**Purpose**: Turns a site token back into the site address it represents, if the token is valid. It protects the rest of the code from forged, damaged, or incomplete tokens.

**Data flow**: It receives a token string → verifies that it is a signed sites-surface token → reads the workspace ID, conversation ID, and site name claims → returns a SiteAddress object. If verification fails or the claims are missing or malformed, it returns None.

**Call relations**: Both resolve_workspace and _resolve use this as the first step when a request arrives. It relies on verify_surface_token for signature checking and UUID parsing to make sure IDs are real UUID values.

*Call graph*: called by 2 (_resolve, resolve_workspace); 3 external calls (__init__, verify_surface_token, UUID).


##### `resolve_workspace`  (lines 135–140)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: Finds which workspace a site-link request belongs to before the system reads any site row. This is important because public viewers may not have a session cookie that would otherwise identify a workspace.

**Data flow**: It reads the token from the request path → asks site_address to decode it → returns the workspace ID if the token is valid. If the token is invalid, it returns the same not-found response used for missing sites.

**Call relations**: The surface routing layer calls this while identifying the request's workspace. It uses site_address for token decoding and _not_found to hide bad tokens behind a normal 404-style response.

*Call graph*: calls 2 internal fn (_not_found, site_address).


##### `frame`  (lines 143–167)

```
async def frame(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Renders the wrapper page for a hosted site. It verifies the link, checks whether the viewer may enter, and then embeds the actual site in an iframe.

**Data flow**: It receives the surface context and web request → resolves the site from the token → identifies the viewer from the session cookie if possible → applies the site's visibility rule → asks the context for an ingress URL for the embedded site → optionally creates a CSRF token for the creator's visibility form → returns an HTML page. If anything fails or access is denied, it returns either a sign-in page or a not-found response.

**Call relations**: This is the GET route for opening a site link or a deep link into a site. It calls _resolve to find the site, _viewer to identify the visitor, SurfaceContext.ingress_url to get the embedded-site address, _session_digest when minting a form token, _frame_page to build the final HTML, and _not_found or _page for special responses.

*Call graph*: calls 7 internal fn (ingress_url, _frame_page, _not_found, _page, _resolve, _session_digest, _viewer); 2 external calls (HTMLResponse, mint_surface_token).


##### `set_visibility`  (lines 170–188)

```
async def set_visibility(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lets the creator change a hosted site's visibility between private, workspace, and public. It only accepts the change from the creator and only when the form includes a valid session-bound CSRF token.

**Data flow**: It receives a POST request → resolves the site → identifies the viewer → rejects anyone who is not the creator → reads the submitted form → checks the CSRF token → validates the requested visibility value → writes the new value to the site store → redirects back to the site's frame page. Bad CSRF returns 403, and an invalid visibility value returns 400.

**Call relations**: This is the POST route behind the visibility selector shown by _frame_page. It calls _resolve and _viewer for authorization, _csrf_holds for form safety, _sites to get the storage helper, and then redirects the browser back to the normal frame route.

*Call graph*: calls 5 internal fn (_csrf_holds, _not_found, _resolve, _sites, _viewer); 4 external calls (PlainTextResponse, RedirectResponse, form, visibility_level).


##### `_resolve`  (lines 191–195)

```
async def _resolve(ctx: SurfaceContext, request: Request) -> HostedSite | None
```

**Purpose**: Looks up the hosted site named by the URL token. It is the shared helper for both viewing a site and changing its visibility.

**Data flow**: It reads the token from the request path → decodes it with site_address → if valid, opens the hosted-sites store for the current workspace → reads the site by conversation ID and name → returns the HostedSite object or None.

**Call relations**: frame and set_visibility both call this before doing anything else with a site. It delegates token parsing to site_address and storage access to _sites.

*Call graph*: calls 2 internal fn (_sites, site_address); called by 2 (frame, set_visibility).


##### `_sites`  (lines 198–199)

```
def _sites(ctx: SurfaceContext) -> HostedSites
```

**Purpose**: Creates the storage helper used to read or update hosted-site records for the current workspace. It keeps the rest of the file from repeating how to connect the workspace and transaction to the store.

**Data flow**: It receives the surface context → takes the workspace ID and active transaction from it → returns a HostedSites store object tied to that workspace and transaction.

**Call relations**: _resolve uses this to read a site, and set_visibility uses it to save a new visibility level.

*Call graph*: called by 2 (_resolve, set_visibility); 1 external calls (__init__).


##### `_viewer`  (lines 202–212)

```
async def _viewer(ctx: SurfaceContext, request: Request) -> UUID | None
```

**Purpose**: Identifies the workspace member behind the request's session cookie, if there is one. If the session names a valid email that is not linked yet, it links that email to a member record.

**Data flow**: It reads the ufo_session cookie from the request → verifies the bearer token for the current workspace → gets an email if the token is valid → finds the linked member for that email or creates the link → returns the member ID. If there is no cookie or the token is invalid, it returns None.

**Call relations**: frame calls this to decide whether the visitor may view the site and whether to show creator controls. set_visibility calls it to prove the requester is the creator. It relies on verify_token for cookie verification and the SurfaceContext member-linking methods to map an email to a workspace member.

*Call graph*: calls 2 internal fn (link_member, linked_member); called by 2 (frame, set_visibility); 1 external calls (verify_token).


##### `_csrf_holds`  (lines 215–217)

```
def _csrf_holds(request: Request, submitted: str) -> bool
```

**Purpose**: Checks whether a submitted visibility form token belongs to this browser session. This helps stop another website from tricking the creator's browser into changing a site's visibility.

**Data flow**: It receives the request and the submitted token → verifies the token as a sites-surface token → compares the token's stored session digest with a fresh digest of the current session cookie → returns true only if they match.

**Call relations**: set_visibility calls this before accepting a visibility change. It uses verify_surface_token to check the signed token and _session_digest to calculate what this request's session should look like.

*Call graph*: calls 1 internal fn (_session_digest); called by 1 (set_visibility); 1 external calls (verify_surface_token).


##### `_session_digest`  (lines 220–224)

```
def _session_digest(request: Request) -> str
```

**Purpose**: Creates a safe fingerprint of the current session cookie for CSRF protection. It uses a hash so the CSRF token can be tied to the session without storing the raw cookie value in the token.

**Data flow**: It reads the ufo_session cookie from the request, or an empty string if missing → hashes it with SHA-256 → returns the hexadecimal hash text.

**Call relations**: frame uses this when creating the creator's CSRF token, and _csrf_holds uses it when checking a submitted token. Both sides must produce the same digest for the form to be accepted.

*Call graph*: called by 2 (_csrf_holds, frame); 1 external calls (sha256).


##### `_not_found`  (lines 227–228)

```
def _not_found() -> Response
```

**Purpose**: Builds the standard not-found response for unknown or inaccessible sites. Using one shared response helps avoid revealing whether a site exists but is private.

**Data flow**: It takes no input → creates a plain-text response with the body “no such site” and status code 404 → returns that response.

**Call relations**: resolve_workspace, frame, and set_visibility call this whenever a bad token, missing site, or unauthorized access should look the same to the viewer.

*Call graph*: called by 3 (frame, resolve_workspace, set_visibility); 1 external calls (PlainTextResponse).


##### `_page`  (lines 231–236)

```
def _page(title: str, style: str, body: str) -> str
```

**Purpose**: Wraps a title, CSS style text, and body HTML into a complete basic HTML document. It provides the common shell for the sign-in notice and the hosted-site frame page.

**Data flow**: It receives a title, style text, and body HTML → combines them with document metadata such as character set and viewport → returns the full HTML string.

**Call relations**: frame uses this for the not-signed-in page. _frame_page uses it to wrap the full hosted-site page after building the header and iframe.

*Call graph*: called by 2 (_frame_page, frame).


##### `_frame_page`  (lines 239–269)

```
def _frame_page(site: HostedSite, embedded: str | None, frame_path: str, csrf: str) -> str
```

**Purpose**: Builds the actual HTML for the hosted-site wrapper page. It shows the site name, either a creator visibility form or a viewer badge, and the iframe that points to the site's own serving origin.

**Data flow**: It receives the site record, the embedded-site URL if one is available, the frame path, and an optional CSRF token → chooses a visibility selector for the creator or a badge for other viewers → creates either an iframe or an unconfigured-hosting message → escapes user-controlled text for safety → returns a complete HTML page.

**Call relations**: frame calls this after access has been approved and an embedded ingress URL has been minted. It calls _selector when creator controls should be shown, _page for the document shell, and html.escape to keep names and URLs from being interpreted as unsafe HTML.

*Call graph*: calls 2 internal fn (_page, _selector); called by 1 (frame); 1 external calls (escape).


##### `_selector`  (lines 272–282)

```
def _selector(current: Visibility, frame_path: str, csrf: str) -> str
```

**Purpose**: Creates the small form that lets a site creator choose who can see the site. The form includes the current visibility choice and a hidden CSRF token.

**Data flow**: It receives the current visibility level, the frame path to post back to, and the CSRF token → builds one option for each allowed visibility level → marks the current level as selected → escapes the form action and token → returns the HTML form string.

**Call relations**: _frame_page calls this only when the current viewer is the creator and a CSRF token was minted. The submitted form is later received by set_visibility.

*Call graph*: called by 1 (_frame_page); 1 external calls (escape).


### `extensions/sites/ufo_ext_sites/tools.py`

`orchestration` · `tool execution during build, serve, deploy, publish, and homepage binding`

This file is the bridge between an agent saying “build and show this website” and the system actually doing it safely. It defines the input shapes for several tools, then implements the steps behind them: run a build command, start a server inside the sandbox, wait until the server answers, and optionally register that port as a permanent hosted site link.

The sandbox is the controlled environment where commands run, like a workshop with walls around it. Every path supplied to these tools is passed through `workspace_path`, so a path named by the model is treated as a workspace path rather than an arbitrary host-machine path. Server logs are written under the tool output directory, not mixed into the user’s project files.

A key job here is avoiding broken or unsafe servers. Before starting a server, `_serve` frees the chosen port, clears the log name safely, launches the command in the background, and probes the port until it is reachable. That means callers get back a URL only after the server is actually listening.

For deploy and publish, the file also checks whether hosting is allowed before disturbing an existing site, then registers the live port under a stable site name. Re-deploying the same name can update the site behind the same link. Finally, `set_homepage` points an agent’s homepage at one already-hosted site without changing who is allowed to view it.

#### Function details

##### `StartServerInput.validate_port`  (lines 131–134)

```
def validate_port(self) -> 'StartServerInput'
```

**Purpose**: This checks that a requested server port is a real TCP port number. It prevents impossible values, such as zero or numbers above 65535, from reaching the server-starting code.

**Data flow**: It receives a `StartServerInput` object after the input fields have been filled in. If no port was supplied, it leaves the input alone. If a port was supplied, it checks the number and either returns the unchanged input or raises an error explaining that the port must be between 1 and 65535.

**Call relations**: This is part of the input model for the `start_server` tool. It runs as validation before the tool handler uses the port, so `start_server` can assume any explicit port is in the valid range.


##### `_json_result`  (lines 173–174)

```
def _json_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: This wraps a plain Python dictionary into the standard tool response format. Tool handlers use it so their results come back as JSON text that the rest of the system can read consistently.

**Data flow**: It takes a dictionary, converts it to a JSON string, places that string in a text content object, and then places the content object inside a tool result. The output is a `ToolResult` ready to return to the caller.

**Call relations**: The public tool handlers call this at the end of successful work. `website`, `start_server`, `deploy_website`, `publish_website`, and `set_homepage` each gather their result details first, then hand them to `_json_result` to package the final answer.

*Call graph*: called by 5 (deploy_website, publish_website, set_homepage, start_server, website); 3 external calls (__init__, __init__, dumps).


##### `_free_log`  (lines 177–193)

```
async def _free_log(ctx: ToolContext, log: str) -> None
```

**Purpose**: This safely clears the chosen server log filename before a background server writes to it. It exists to avoid a dangerous case where a log path could be replaced by a link to some other file and then overwritten by shell redirection.

**Data flow**: It receives the tool context and a log path. It runs a small Python program inside the sandbox that checks containment, creates parent directories if needed, and removes the existing log file name. If that cleanup fails, it raises an error; otherwise it changes only the log path by making sure the name is free for a new file.

**Call relations**: `_serve` calls this immediately before launching a server. That puts the log path into a safe state so the later shell redirect can create a fresh file instead of truncating an unexpected target.

*Call graph*: called by 1 (_serve).


##### `_serve`  (lines 196–246)

```
async def _serve(ctx: ToolContext, command: str, project: str, port: int, log: str) -> dict[str, object]
```

**Purpose**: This starts a server command in the sandbox and waits until the chosen port is actually reachable. It turns a fragile background command into a reliable “the server is up” operation.

**Data flow**: It receives a command, project directory, port, and log path. It first clears the log safely, then kills any old process using the same port, starts the new command with its output going to the log, and repeatedly tries to connect to the port. If the server answers in time, it returns the sandbox-local URL, port, and log path. If not, it reads the end of the log when possible and raises a useful error.

**Call relations**: `start_server`, `deploy_website`, and `publish_website` all rely on `_serve` when they need a running server. `_serve` delegates log safety to `_free_log`, then gives its callers proof that the port is listening before they return or host the site.

*Call graph*: calls 1 internal fn (_free_log); called by 3 (deploy_website, publish_website, start_server); 1 external calls (quote).


##### `_refuse_before_serving`  (lines 249–288)

```
async def _refuse_before_serving(ctx: ToolContext, raw_name: str, port: int, visibility: Visibility | None) -> str
```

**Purpose**: This checks whether a site is allowed to be hosted before the code kills or replaces anything on the serving port. It protects existing live sites from being disrupted by a deploy that would later be refused.

**Data flow**: It receives the requested site name, target port, and optional visibility setting. It checks that the tool has extension state, that an acting member owns the site, and that visibility changes have a live speaker. It normalizes the site name, verifies that a URL can be formed, asks the hosted-site store whether registration would be refused, and returns the normalized name if everything is allowed.

**Call relations**: `deploy_website` and `publish_website` call this before starting their server. If the check passes, they continue to `_serve`; if it fails, the existing site remains untouched because serving has not yet begun.

*Call graph*: called by 2 (deploy_website, publish_website); 3 external calls (__init__, site_name, site_url).


##### `_host`  (lines 291–328)

```
async def _host(ctx: ToolContext, raw_name: str, port: int, visibility: Visibility | None) -> dict[str, object]
```

**Purpose**: This records a running sandbox port as a hosted website and returns the public-facing details for it. It is the step that turns “a server is listening on this port” into “there is a stable site link people can open.”

**Data flow**: It receives the raw site name, port, and optional visibility. It verifies the needed context and ownership, normalizes the name, builds the hosted URL, and writes the registration through the hosted-site store. It returns the stored site name, visibility, object name, and public site URL.

**Call relations**: `deploy_website` and `publish_website` call `_host` only after `_serve` has proved the server is running. `_host` repeats the permission checks at write time, because another deploy could have changed the situation since `_refuse_before_serving` ran.

*Call graph*: called by 2 (deploy_website, publish_website); 4 external calls (__init__, site_object_name, site_name, site_url).


##### `website`  (lines 331–340)

```
async def website(ctx: ToolContext, args: WebsiteInput) -> ToolResult
```

**Purpose**: This is the simple build tool for websites. It runs a build command in a project directory and reports what files are present afterward.

**Data flow**: It receives tool context and build input, including a command and optional project path. It turns the project path into a safe workspace path, runs the build command in the sandbox with a long timeout, and raises an error if the command fails. On success, it lists the project directory and returns the project path plus the file names as JSON.

**Call relations**: This tool does not start or host a server. It uses `workspace_path` before running shell commands, quotes the directory for the shell, and finishes by passing its result through `_json_result`.

*Call graph*: calls 1 internal fn (_json_result); 2 external calls (quote, workspace_path).


##### `start_server`  (lines 343–348)

```
async def start_server(ctx: ToolContext, args: StartServerInput) -> ToolResult
```

**Purpose**: This starts a scratch server inside the sandbox and returns the local URL once it is ready. It is for testing or previewing a site without creating a permanent hosted link.

**Data flow**: It receives a server command, project path, optional port, and optional log file. It chooses a default port and log path when needed, converts paths into workspace-safe paths, and asks `_serve` to start the command and wait for readiness. It returns the serving details plus the project path as JSON.

**Call relations**: `start_server` is the public tool handler for temporary servers. It hands the hard work of cleanup, launch, logging, and readiness probing to `_serve`, then packages the result with `_json_result`.

*Call graph*: calls 2 internal fn (_json_result, _serve); 1 external calls (workspace_path).


##### `deploy_website`  (lines 351–358)

```
async def deploy_website(ctx: ToolContext, args: DeployWebsiteInput) -> ToolResult
```

**Purpose**: This serves a built static website folder and registers it as a hosted site with a stable link. It is meant for the common case where the site is already built and can be served by a simple static file server.

**Data flow**: It receives the static output directory, site name, entry point, optional visibility, and description. It first asks `_refuse_before_serving` whether hosting is allowed, then converts the project path safely, starts Python’s built-in static web server on the app serving port, and waits for it through `_serve`. Once the server is live, it registers the site through `_host` and returns the server details, hosted-site details, and entry point as JSON.

**Call relations**: `deploy_website` strings together the full deploy path: permission check, serving, hosting, and result packaging. `_refuse_before_serving` protects existing sites before the port is touched, `_serve` proves the new server is reachable, `_host` records the stable link, and `_json_result` formats the final tool response.

*Call graph*: calls 4 internal fn (_host, _json_result, _refuse_before_serving, _serve); 1 external calls (workspace_path).


##### `publish_website`  (lines 361–375)

```
async def publish_website(ctx: ToolContext, args: PublishWebsiteInput) -> ToolResult
```

**Purpose**: This publishes a fuller web app, optionally installing dependencies and optionally running a custom backend command, then hosts it at a stable site link. It covers apps that need more than just pointing a static server at a folder.

**Data flow**: It receives a project directory, built output directory, app name, optional visibility, optional install command, and optional run command. It first checks hosting permission. If an install command is present, it runs it in the project directory and stops on failure. Then it chooses either the custom run command in the project directory or a default static server in the dist directory, starts it through `_serve`, registers it through `_host`, and returns the combined serving and hosting details as JSON.

**Call relations**: `publish_website` uses the same protected hosting flow as `deploy_website`, but adds an optional install step and supports a custom server command. It calls `_refuse_before_serving` before touching the port, `_serve` to launch and verify the app, `_host` to create or update the hosted link, and `_json_result` to return the result.

*Call graph*: calls 4 internal fn (_host, _json_result, _refuse_before_serving, _serve); 2 external calls (quote, workspace_path).


##### `set_homepage`  (lines 378–402)

```
async def set_homepage(ctx: ToolContext, args: SetHomepageInput) -> ToolResult
```

**Purpose**: This makes one already-hosted site the homepage for the acting agent. It changes the homepage pointer, not the site’s visibility or contents.

**Data flow**: It receives the site object name from an earlier deploy result. It loads all hosted sites, builds a lookup from object names to site records, and finds the requested one. If the name is unknown, it raises an error telling the caller to deploy first. If found, it asks the store to bind that site as the agent’s homepage, then returns the site name, URL, visibility, and homepage agent ID as JSON.

**Call relations**: `set_homepage` works after a site has already been deployed or published. It uses the hosted-site store to find and bind the site, uses `site_object_name` and `site_url` to match and describe it, and uses `_json_result` to package the final confirmation.

*Call graph*: calls 1 internal fn (_json_result); 3 external calls (__init__, site_object_name, site_url).


### `extensions/sites/ufo_ext_sites/delegation.py`

`orchestration` · `request handling`

This file is a delegation bridge. When the main agent needs a website, web app, dashboard, or small web game built, it can call `build_website` instead of trying to do the whole job directly. Think of it like handing a clear work order to a specialist contractor: the main agent writes down the full objective, and the website-building subagent carries out the build in the same workspace.

The file defines the shape of that work order with `BuildWebsiteInput`. The most important field is `objective`, which must include all needed context because the child agent does not inherit the parent conversation history. Other fields let the caller give the task a friendly name, preload useful skills, request a larger work budget for bigger builds, and describe the activity in plain language for the user-facing timeline.

The actual tool handler, `_build_website`, calls `ctx.spawn` to start the specialized `website_building` profile. The child agent works in the same filesystem sandbox, so any files it creates remain available after it finishes. The tool is marked as side-effecting because it changes the workspace and may deploy or register a site. It also uses an idempotency key, which helps reconnect to the same spawned build if a crash recovery or retry repeats the call.

#### Function details

##### `_build_website`  (lines 57–64)

```
async def _build_website(ctx: ToolContext, args: BuildWebsiteInput) -> ToolResult
```

**Purpose**: This is the worker function behind the `build_website` tool. It starts the website-building child agent, gives it the build instructions, and returns the child agent’s summary as tool output.

**Data flow**: It receives a tool context and a validated `BuildWebsiteInput` object. It turns the input into plain data, leaving out empty values and the user-facing `user_description`, then passes that data to `ctx.spawn` to start the `website_building` profile. When the child finishes or reconnects, it takes the child result, converts the result output to JSON text if present, wraps that text in `TextContent`, and returns it inside a `ToolResult`.

**Call relations**: This function is registered as the handler for the `build_website` tool, so it runs when the agent chooses that tool. Its main handoff is to `ToolContext.spawn`, which creates or reconnects to the specialized website-building child using the current call’s idempotency key. After the child returns, `_build_website` packages the child’s output into the normal tool-result format for the calling agent to read.

*Call graph*: 4 external calls (__init__, __init__, spawn, model_dump).


### `extensions/sites/ufo_ext_sites/store.py`

`domain_logic` · `request handling`

A hosted site here is like a signpost: a stable name such as “dashboard” points to a live sandbox port where the site’s files are being served. This file defines the signpost table and the rules for changing it. Sites are scoped by workspace and conversation, so two conversations can both have a site called “dashboard” without colliding.

The important job is not just saving rows. It also protects ownership and privacy. A site has a creator, a visibility level, and a generation ID that changes when visibility changes so viewers can notice the update. Re-deploying an existing site updates the port, but normally keeps the existing visibility so a teammate’s deploy does not silently reset who can open the site.

The file also handles port conflicts. Since one port can only serve one live origin, registering a new name on a port may remove the old name. That counts as unhosting, so the code checks that the acting member owns the displaced site and is allowed to unhost it. The HostedSites class is the main access point. Callers give it a workspace and a database transaction maker, and it performs all reads and writes with that workspace filter so records from other workspaces are never mixed in.

#### Function details

##### `site_name`  (lines 90–98)

```
def site_name(raw: str) -> str
```

**Purpose**: Turns a user-supplied site name into a safe, short, link-friendly name. It lowercases the text, replaces runs of non-letter-or-number characters with hyphens, trims it, and refuses names that contain no usable letters or digits.

**Data flow**: It receives raw text from a member → normalizes it into a compact slug that can be used in links and object names → returns that slug, or raises InvalidSiteName if nothing usable remains.

**Call relations**: This is used before storing a site name so later database operations can assume the name is already safe. If the name cannot become a real slug, it stops the deploy early by creating an InvalidSiteName error.

*Call graph*: 1 external calls (__init__).


##### `default_visibility`  (lines 101–109)

```
def default_visibility(audience: Audience) -> Visibility
```

**Purpose**: Chooses the starting visibility for a newly registered site based on the audience of the conversation that created it. Private direct messages and external rooms default to private, while internal shared conversations default to workspace visibility.

**Data flow**: It receives an Audience value → parses it and checks whether it represents a single member or an outside audience → returns either "private" or "workspace" as the default visibility.

**Call relations**: HostedSites.register calls this only when inserting a brand-new site and the caller did not explicitly choose a visibility. It relies on audience parsing helpers to avoid accidentally exposing externally shared work.

*Call graph*: called by 1 (register); 2 external calls (audience_member, parse_audience).


##### `visibility_level`  (lines 112–120)

```
def visibility_level(value: str) -> Visibility
```

**Purpose**: Checks that a visibility string is one of the three supported choices: private, workspace, or public. It protects the rest of the code from bad or unexpected values coming from storage or input.

**Data flow**: It receives a string → compares it against the allowed visibility names → returns the same value as a valid Visibility, or raises ValueError if it is not allowed.

**Call relations**: _site calls this while converting database rows into HostedSite objects. That means every loaded site gets validated before the rest of the app uses it.

*Call graph*: called by 1 (_site).


##### `HostedSites.register`  (lines 146–210)

```
async def register(self, conversation_id: UUID, name: str, port: int, creator_member_id: UUID, visibility: Visibility | None, audience: Audience, may_unhost: bool) -> HostedSite
```

**Purpose**: Creates or updates the registry record for a site after a deploy. It enforces the rules around ownership, visibility changes, and port takeovers before writing the final site record.

**Data flow**: It receives the conversation, site name, sandbox port, creator member, optional requested visibility, conversation audience, and whether this action may unhost another site → checks whether the operation is allowed, deletes any same-port site that must be displaced, updates an existing same-name site or inserts a new one → returns the registered HostedSite row.

**Call relations**: This is the main write path for deployments. It first asks _refuse to catch forbidden changes, may use _read to compare existing visibility, uses default_visibility for new sites without an explicit setting, then reads the finished row back with _read before returning it.

*Call graph*: calls 3 internal fn (_read, _refuse, default_visibility); 4 external calls (delete, insert, update, uuid4).


##### `HostedSites.read`  (lines 212–214)

```
async def read(self, conversation_id: UUID, name: str) -> HostedSite | None
```

**Purpose**: Looks up one hosted site by conversation and name. It is the simple public read method for resolving a known site link or checking whether a site exists.

**Data flow**: It receives a conversation ID and site name → opens a transaction and searches the current workspace’s registry → returns the HostedSite if found, otherwise None.

**Call relations**: This wraps the private _read helper so callers do not need to provide a database connection. _read does the actual query and row conversion.

*Call graph*: calls 1 internal fn (_read).


##### `HostedSites.all`  (lines 216–226)

```
async def all(self) -> tuple[HostedSite, ...]
```

**Purpose**: Returns every hosted site in the current workspace, ordered from oldest to newest. This is useful when another layer will apply its own viewing rules or build a workspace-wide list.

**Data flow**: It reads the workspace ID from the HostedSites object → queries all matching registry rows ordered by creation time and name → converts each row into a HostedSite and returns them as a tuple.

**Call relations**: It builds its select list with _columns and converts rows with _site. Unlike visible_conversation, it does not filter by member visibility; that gate is expected elsewhere.

*Call graph*: calls 2 internal fn (_columns, _site).


##### `HostedSites.conversation`  (lines 228–245)

```
async def conversation(self, conversation_id: UUID, names: tuple[str, ...], limit: int) -> tuple[HostedSite, ...]
```

**Purpose**: Returns selected hosted sites for one conversation, limited to specific names and a maximum count. It gives callers a focused way to load known site records from a conversation.

**Data flow**: It receives a conversation ID, a tuple of site names, and a limit → queries matching rows in the current workspace and conversation → returns matching HostedSite objects in oldest-first order.

**Call relations**: It uses _columns to build the database query and _site to turn each row into the project’s HostedSite data object. It is a narrower list operation than all.

*Call graph*: calls 2 internal fn (_columns, _site).


##### `HostedSites.visible_conversation`  (lines 247–266)

```
async def visible_conversation(self, conversation_id: UUID, member_id: UUID, limit: int) -> tuple[HostedSite, ...]
```

**Purpose**: Returns the sites in one conversation that a particular member is allowed to see in a basic list. Private sites are included only for their creator; non-private sites are included for others.

**Data flow**: It receives a conversation ID, member ID, and limit → queries current-workspace rows where the member created the site or the site is not private → returns the visible HostedSite objects in oldest-first order.

**Call relations**: It uses _columns for the shared list of selected fields and _site for row conversion. It adds a database OR condition so the visibility filtering happens inside the query.

*Call graph*: calls 2 internal fn (_columns, _site); 1 external calls (or_).


##### `HostedSites.set_visibility`  (lines 268–283)

```
async def set_visibility(self, conversation_id: UUID, name: str, visibility: Visibility) -> HostedSite | None
```

**Purpose**: Changes the visibility of an existing site and marks it as a new generation. The generation change gives other parts of the system a clear signal that access rules changed.

**Data flow**: It receives a conversation ID, site name, and new visibility → updates the matching row in the current workspace with the new visibility, a fresh generation ID, and a new update time → returns the updated HostedSite, or None if the site no longer exists.

**Call relations**: After issuing the update, it calls _read to fetch the result in the same transaction. Ownership checks are not done here, so callers are expected to enforce who may request the change before calling it.

*Call graph*: calls 1 internal fn (_read); 2 external calls (update, uuid4).


##### `HostedSites.set_homepage`  (lines 285–309)

```
async def set_homepage(self, agent_id: UUID, conversation_id: UUID, name: str) -> HostedSite | None
```

**Purpose**: Marks one hosted site as an agent’s homepage and clears any previous homepage for that same agent. This keeps the rule that an agent can have at most one homepage site.

**Data flow**: It receives an agent ID plus the conversation and name of the chosen site → first removes that agent ID from any existing homepage row in the workspace, then writes it onto the chosen site → returns the chosen HostedSite, or None if it was gone.

**Call relations**: It performs both updates in one transaction so the database never keeps two homepage bindings for the same agent. It then calls _read to return the final bound row.

*Call graph*: calls 1 internal fn (_read); 1 external calls (update).


##### `HostedSites.unregister`  (lines 311–322)

```
async def unregister(self, conversation_id: UUID, name: str) -> None
```

**Purpose**: Removes a site from the registry so its permanent link no longer resolves. It does not stop the sandbox process itself; it only removes the public signpost.

**Data flow**: It receives a conversation ID and site name → deletes the matching row for the current workspace → returns nothing after the registration is gone.

**Call relations**: This is the direct unhost operation. Other paths, such as register, may also delete a row when a same-port site is displaced, but this method is for explicitly dropping a named site.

*Call graph*: 1 external calls (delete).


##### `HostedSites.refuse_or_pass`  (lines 324–342)

```
async def refuse_or_pass(self, conversation_id: UUID, name: str, port: int, creator_member_id: UUID, visibility: Visibility | None, may_unhost: bool) -> None
```

**Purpose**: Checks whether a future registration would be refused, without changing the database. Callers use it before serving a new deploy so they can avoid taking over a port if the registry rules would reject the action.

**Data flow**: It receives the same key facts as registration: conversation, name, port, creator, optional visibility, and whether unhosting is allowed → runs the refusal checks inside a transaction → returns nothing if allowed, or raises the same error register would raise.

**Call relations**: This is a dry run for HostedSites.register. Both methods use _refuse, so the pre-check and the actual write enforce the same rules.

*Call graph*: calls 1 internal fn (_refuse).


##### `HostedSites._refuse`  (lines 344–373)

```
async def _refuse(self, connection: AsyncConnection, conversation_id: UUID, name: str, port: int, creator_member_id: UUID, visibility: Visibility | None, may_unhost: bool) -> HostedSite | None
```

**Purpose**: Centralizes the rules that can block a site registration. It decides whether the caller is trying to change another creator’s visibility choice or displace a site they are not allowed to unhost.

**Data flow**: It receives a database connection and proposed registration details → reads the existing same-name site, checks creator ownership for visibility changes, looks for another site already using the port, and checks unhosting permission → returns the displaced HostedSite if one would be removed, or None if no site is displaced; it raises an error when the action is not allowed.

**Call relations**: HostedSites.register calls this before writing, and HostedSites.refuse_or_pass calls it for a no-write check. It uses _read to inspect the target name and _on_port to find a same-port conflict.

*Call graph*: calls 2 internal fn (_on_port, _read); called by 2 (refuse_or_pass, register); 2 external calls (__init__, __init__).


##### `HostedSites._on_port`  (lines 375–390)

```
async def _on_port(self, connection: AsyncConnection, conversation_id: UUID, port: int, name: str) -> HostedSite | None
```

**Purpose**: Finds whether another site in the same conversation is already registered on the proposed sandbox port. This matters because a port can only represent one live origin, so a new registration may need to retire the old name.

**Data flow**: It receives a database connection, conversation ID, port, and the name being registered → searches the current workspace and conversation for a different site on that port → returns that HostedSite if found, otherwise None.

**Call relations**: _refuse calls this while deciding whether registration would displace another site. It builds the query with _columns and turns the row into a HostedSite with _site.

*Call graph*: calls 2 internal fn (_columns, _site); called by 1 (_refuse); 1 external calls (execute).


##### `HostedSites._read`  (lines 392–404)

```
async def _read(self, connection: AsyncConnection, conversation_id: UUID, name: str) -> HostedSite | None
```

**Purpose**: Performs the actual database lookup for one site record. It is the shared helper behind public reads and post-update fetches.

**Data flow**: It receives a database connection, conversation ID, and site name → queries the current workspace for that exact site → returns a HostedSite object if a row exists, otherwise None.

**Call relations**: HostedSites.read, register, set_visibility, set_homepage, and _refuse all rely on this helper. It uses _columns for a consistent selected shape and _site for conversion.

*Call graph*: calls 2 internal fn (_columns, _site); called by 5 (_refuse, read, register, set_homepage, set_visibility); 1 external calls (execute).


##### `HostedSites._columns`  (lines 406–417)

```
def _columns(self) -> sa.Select
```

**Purpose**: Builds the common database select statement for hosted-site rows. It keeps all read queries asking for the same fields in the same order.

**Data flow**: It reads no outside input beyond the table definition → creates a SQLAlchemy select object containing the columns needed to build a HostedSite → returns that select object so callers can add their own filters and ordering.

**Call relations**: All list and lookup helpers use this as their starting query: _read, _on_port, all, conversation, and visible_conversation. This avoids each method hand-writing the column list.

*Call graph*: called by 5 (_on_port, _read, all, conversation, visible_conversation); 1 external calls (select).


##### `_site`  (lines 420–431)

```
def _site(row: sa.Row) -> HostedSite
```

**Purpose**: Converts a raw database row into a HostedSite data object. This gives the rest of the code a clear Python object instead of a database-specific row.

**Data flow**: It receives a SQL row with hosted-site fields → validates the stored visibility string and copies each field into a HostedSite → returns the finished HostedSite object.

**Call relations**: Every read path that returns site records calls this after the database query. It calls visibility_level so bad stored visibility values are caught during conversion.

*Call graph*: calls 1 internal fn (visibility_level); called by 5 (_on_port, _read, all, conversation, visible_conversation); 1 external calls (__init__).


### Coding extension package
The coding extension package marker enables the rest of the system to import coding-related extension modules.

### `extensions/coding/ufo_ext_coding/__init__.py`

`other` · `import/package discovery`

In Python, a folder often needs an `__init__.py` file to be treated as a package: a named bundle of code that can be imported elsewhere. This file is empty, so it is like a label on a drawer rather than a tool inside the drawer. Its presence tells Python and project tooling that `extensions/coding/ufo_ext_coding` is an importable module namespace. Without it, some import styles or older Python tooling might not recognize this directory as part of the package structure. There are no functions, classes, settings, or startup actions here. The useful code for this extension lives in other files under the same package.


### Batch research delegation
Research delegation lets the main agent fan out many entity investigations to a specialist subagent and save the combined results as an artifact.

### `extensions/research/ufo_ext_research/delegation.py`

`orchestration` · `request handling`

This file exists to turn one research request into many smaller research jobs. A user gives it a file containing entities, companies, or topics, one per line, plus a prompt template such as “Research {entity}.” The tool reads the file, removes blank lines and duplicates, and then launches a separate research subagent for each entity. Think of it like giving a classroom one worksheet template, then assigning each student a different company to investigate.

To avoid overwhelming the system, it only lets a fixed number of child research jobs run at the same time. It also refuses very large batches, with a limit of 128 entities. If the user provides an output schema file, the schema text is added to each child’s research objective so the returned information is shaped consistently.

A key detail is crash recovery. The tool is marked as side-effecting, meaning it creates lasting work and files. Each child research job gets a stable deduplication key based on the parent call and the entity name. If the parent is restarted after a crash, already-started or completed child jobs can be reconnected to instead of being duplicated.

When all child jobs finish, their results are collected into `wide_research.json` in the workspace, and the tool also returns a compact summary telling the caller where that file is.

#### Function details

##### `_read_lines`  (lines 42–55)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: This helper reads an entities file from the sandbox and turns it into a clean list of unique, non-empty lines. It is used so the rest of the tool can work with a simple list of entity names instead of raw file text.

**Data flow**: It receives the tool context and a file path. It safely quotes the path for a shell command, asks the sandbox to run `cat` on that file, and checks whether the read succeeded. It then splits the file into lines, trims extra spaces, skips empty lines, removes duplicates while keeping the original order, and returns the cleaned list.

**Call relations**: The main `_wide_research` function calls this first, before launching any child research jobs. It uses `shlex.quote` so the file path is treated as a literal path by the shell, rather than accidentally running special shell characters as commands.

*Call graph*: called by 1 (_wide_research); 1 external calls (quote).


##### `_wide_research`  (lines 58–85)

```
async def _wide_research(ctx: ToolContext, args: WideResearchInput) -> ToolResult
```

**Purpose**: This is the main body of the `wide_research` tool. It reads the requested entities, launches bounded parallel research jobs for them, writes the combined results to a JSON file, and returns a tool response pointing to that output.

**Data flow**: It receives the tool context and validated user input: the entities file, prompt template, schema file path, and description. First it asks `_read_lines` for the cleaned entity list. It rejects the request if there are too many entities. It then reads the optional schema file, creates a semaphore, which is a gate that limits how many jobs can run at once, and starts one `visit` task per entity. After all visits finish, it writes the collected rows to `wide_research.json` and returns a `ToolResult` containing JSON with the rows and output filename.

**Call relations**: This function is registered as the handler for `WIDE_RESEARCH_TOOL`, so it runs when someone invokes the tool. It delegates file cleanup to `_read_lines`, uses `asyncio.gather` to wait for all per-entity research tasks, uses `visit` for the actual per-entity child spawn, and wraps the final answer in `TextContent` and `ToolResult` so the tool system can return it to the caller.

*Call graph*: calls 1 internal fn (_read_lines); 6 external calls (__init__, __init__, Semaphore, gather, dumps, quote).


##### `_wide_research.visit`  (lines 66–79)

```
async def visit(entity: str) -> dict[str, object]
```

**Purpose**: This inner helper performs the research work for one entity. It builds that entity’s specific research objective, launches the research subagent, and packages the subagent’s answer into one result row.

**Data flow**: It receives one entity name from the outer `_wide_research` loop. It waits for permission from the semaphore so only a limited number of entities are researched at the same time. It replaces `{entity}` in the prompt template with the actual entity, appends the output schema if one was read, and calls `ctx.spawn` to run the research profile. The result is converted into a dictionary containing the entity name and the child agent’s output as JSON text, or an empty string if there was no output.

**Call relations**: `_wide_research` creates one `visit` task for each entity and waits for all of them together. Each `visit` hands work off to the research subagent through `ctx.spawn`, using a deterministic deduplication key so repeated recovery runs can reconnect to the same child work instead of starting duplicate research.
