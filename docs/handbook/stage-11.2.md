# Hosted sites and preview delivery  `stage-11.2`

This stage turns a website made inside a workspace into something people can view safely in a browser. It sits in the main work loop, after an agent has created files and wants to preview or share them. The tools file gives the agent practical buttons: build a site, run a local server, publish that server as a hosted link, and set it as a homepage. The delegation file adds a specialist helper, the build_website subagent, so website work can be handed off while staying in the same sandbox, meaning the same files and server remain usable.

The store is the address book and rule book. It records which conversation owns each hosted site, what sandbox port serves it, and who may view or change it. The objects file makes these records appear as normal workspace “site” items that members can list, inspect, edit, or remove. The conversation slot builds the Sites panel, showing safe display details like URLs and visibility. Finally, the ingress server is the public doorway: it checks signed links or sessions, finds the right sandbox server, and relays browser traffic to it.

## Files in this stage

### Workspace site surfaces
Conversation and workspace object views expose hosted sites with safe metadata, links, permissions, and management actions.

### `extensions/sites/ufo_ext_sites/conversation_slot.py`

`orchestration` · `request handling`

This file exists so a conversation can show the user which hosted sites belong to it. Think of it like the code behind a small “Sites” shelf in the conversation view: it decides what belongs on the shelf, checks that each item is still allowed to be shown, and formats each site so the front end can display it.

The important safety step is authorization. The conversation context contains visible authorization items. The file first converts those visible items into expected site names and generations. A generation is like a version number; it helps make sure the permission being used still matches the stored site record. It then asks the hosted-sites store for matching sites in this conversation, allowing one extra result so it can tell whether the list had to be cut short.

After reading from storage, it filters the results again. A site is only kept if its stored name and generation match one of the visible authorization objects. Then it asks for agent visibility information, builds public URLs for each site, calculates the effective visibility, and returns a payload containing at most the configured maximum number of sites.

At the bottom, `SITES_SLOT` registers this behavior as a conversation slot provider. That is the object the wider system can call when it wants a quick summary or the full list of sites.

#### Function details

##### `_read`  (lines 13–48)

```
async def _read(ctx: ConversationSlotContext) -> SitesSlotPayload
```

**Purpose**: Reads the actual site list for a conversation and turns it into display-ready data. It is careful to include only sites that the current conversation context is allowed to see.

**Data flow**: It receives a conversation slot context, which includes the conversation ID, visible authorization items, extension storage access, transaction information, and the public base URL. It converts visible authorization item names into expected site names, reads matching hosted-site rows from storage, filters out anything whose authorization name or generation does not match, asks for agent visibility settings, and builds `ConversationSite` objects with names, URLs, visibility, timestamps, and authorization metadata. It returns a `SitesSlotPayload` containing the visible sites and a flag saying whether there were more sites than the allowed display limit.

**Call relations**: This function is handed to `SITES_SLOT` as the full read operation for the Sites conversation slot. When the wider conversation system asks to open or populate the Sites slot, this function does the deeper work: it uses `HostedSites` to fetch stored records, uses object-name helper functions to connect storage rows back to authorization objects, uses `site_url` to make a public link, uses `effective_visibility` to decide how visible each site should be, and finally packages everything into `SitesSlotPayload`.

*Call graph*: 7 external calls (__init__, __init__, __init__, effective_visibility, site_name_from_object, site_object_name, site_url).


##### `_summarize`  (lines 51–53)

```
async def _summarize(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: Provides a quick count for the Sites slot without reading all site details. It tells the interface how many visible site-related items there are, capped at the maximum number the slot is designed to show.

**Data flow**: It receives the conversation slot context and looks only at `ctx.visible_items`. It counts those visible items, caps the count at `CONVERSATION_SITES_MAX`, and returns that number. If there are no visible items, it returns `None`, which lets the system treat the slot as empty rather than showing a zero count.

**Call relations**: This function is handed to `SITES_SLOT` as the lightweight summary operation. The wider conversation system can call it when it only needs a badge or preview count, before deciding whether to call `_read` for the full site list.


### `extensions/sites/ufo_ext_sites/objects.py`

`domain_logic` · `request handling`

A hosted site is not just a running port in a sandbox. People also need to find it later, know who owns it, and control who can open its link. This file is the bridge between the site hosting registry and the workspace object system, so sites can appear beside other workspace objects in chat and tools.

Each site object gets a stable name made from the site name plus a short digest of the conversation ID. That matters because two different conversations may both deploy a site called `dashboard`; the digest keeps those from colliding, like adding an apartment number to the same street address.

The file defines the rules for site visibility. A normal site uses its own visibility setting: private, workspace, or public. A site used as an agent homepage is different: its visibility follows the agent instead, and this file refuses direct visibility changes on the site in that case.

The `SiteObjects` class supplies the object-system actions: list visible sites, show one site’s details, return status information such as URL and port, apply a visibility change, and delete by unregistering the site. Creation is deliberately refused here, because a site only exists after a deploy knows which sandbox port is serving it.

#### Function details

##### `site_object_name`  (lines 71–75)

```
def site_object_name(conversation_id: UUID, name: str) -> str
```

**Purpose**: Builds the workspace object name for a hosted site. It combines the human site name with a short fingerprint of the conversation, so same-named sites from different conversations stay separate.

**Data flow**: It receives a conversation ID and a site name. It turns the conversation ID into a SHA-256 hash, keeps the first 12 hexadecimal characters, and appends that to the site name with a dash. The result is a unique-looking object name such as `dashboard-9f21c0a4e3b7`.

**Call relations**: This is the naming helper used whenever this file needs to present or look up sites as objects. `_named` uses it to build a lookup table, `member_conversation_rows` uses it when granting conversation-visible objects, and `site_name_from_object` uses it to verify that a proposed object name really matches the expected pattern.

*Call graph*: called by 3 (member_conversation_rows, _named, site_name_from_object); 1 external calls (sha256).


##### `site_name_from_object`  (lines 78–84)

```
def site_name_from_object(conversation_id: UUID, object_name: str) -> str | None
```

**Purpose**: Extracts the original site name from a site object name, but only if the name belongs to the given conversation. It is a safety check against accidentally treating a lookalike string as a real site object name.

**Data flow**: It receives a conversation ID and an object name. It recreates the digest suffix for that conversation, checks whether the object name ends with that suffix, removes it, and then rebuilds the full object name to confirm the match. It returns the original site name when valid, or `None` when the object name does not fit.

**Call relations**: It relies on the same hashing rule as `site_object_name`, and calls that helper for final verification. This keeps parsing and naming tied to one rule, so future changes are less likely to split the two behaviors.

*Call graph*: calls 1 internal fn (site_object_name); 1 external calls (sha256).


##### `_named`  (lines 87–88)

```
def _named(sites: Iterable[HostedSite]) -> dict[str, HostedSite]
```

**Purpose**: Turns a collection of hosted site records into a dictionary keyed by their workspace object names. This makes later lookup by object name quick and consistent.

**Data flow**: It receives hosted site records. For each site, it calculates the object name from that site’s conversation ID and site name, then stores the site under that key. The result is a name-to-site map.

**Call relations**: `_member_rows` uses this when preparing a full listing, and `_find` uses it to locate one site by object name. It depends on `site_object_name` so every listing and lookup uses the same naming convention.

*Call graph*: calls 1 internal fn (site_object_name); called by 2 (_find, _member_rows).


##### `_workspace`  (lines 91–94)

```
def _workspace(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Requires and returns the extension context, which is the bundle of workspace-specific services this file needs. If no context is provided, it fails loudly instead of guessing.

**Data flow**: It receives an optional extension context. If the context is present, it returns it unchanged. If it is missing, it raises a runtime error explaining that the site object kind must read through its extension context.

**Call relations**: Most operations in `SiteObjects` call this before reading workspace data such as the store, public base URL, or agent visibility map. `_sites` also calls it before creating the site registry helper.

*Call graph*: called by 6 (_apply_owned, _member_object, _member_rows, _status, member_conversation_rows, _sites).


##### `_sites`  (lines 97–99)

```
def _sites(ext: ExtensionContext | None) -> HostedSites
```

**Purpose**: Creates access to the hosted-site registry for the current workspace. The registry is where deployed sites are recorded and updated.

**Data flow**: It receives an optional extension context, first checks it through `_workspace`, then uses the workspace ID and current transaction from that context to create a `HostedSites` store helper. The output is that helper, ready to query or change site records.

**Call relations**: The listing, conversation grant, lookup, visibility-change, and delete paths all call `_sites` when they need the persisted site registry. It calls `_workspace` so all store access is tied to the active workspace context.

*Call graph*: calls 1 internal fn (_workspace); called by 5 (_apply_owned, _delete_owned, _find, _member_rows, member_conversation_rows); 1 external calls (__init__).


##### `effective_visibility`  (lines 102–108)

```
def effective_visibility(site: HostedSite, agents: Mapping[UUID, str]) -> Visibility
```

**Purpose**: Decides the visibility level that actually controls access to a site. For ordinary sites this is the site’s own setting; for agent homepages it is the agent’s visibility instead.

**Data flow**: It receives a hosted site record and a mapping from agent IDs to their visibility levels. If the site is not bound to an agent homepage, it returns the site’s own visibility. If it is bound to an agent, it looks up that agent and converts the agent’s level into the site visibility value used by this feature.

**Call relations**: `_member_rows`, `_member_object`, and `_apply_owned` call this before showing or comparing visibility. That keeps the special homepage rule in one place, so listing, detail display, and mutation all agree.

*Call graph*: called by 3 (_apply_owned, _member_object, _member_rows); 1 external calls (visibility_level).


##### `_summary`  (lines 111–112)

```
def _summary(site: HostedSite, visibility: Visibility) -> str
```

**Purpose**: Creates a short human-readable summary line for a site. It includes the site name, the visibility level being shown, and the sandbox port serving it.

**Data flow**: It receives a hosted site and a visibility value. It formats those into one compact string. The returned text is used as the summary in object listings.

**Call relations**: `_member_rows` calls this while building each listed row. It is intentionally small: the listing function gathers the facts, and `_summary` turns the key facts into readable text.

*Call graph*: called by 1 (_member_rows).


##### `SiteObjects._admin_can_apply`  (lines 128–129)

```
def _admin_can_apply(self, old: SiteSpec, spec: SiteSpec) -> bool
```

**Purpose**: Defines the one visibility change a workspace admin is allowed to make on someone else’s site: narrowing it to private. Admins may reduce exposure, but they may not widen access.

**Data flow**: It receives the old site specification and the requested new specification. It returns `true` only when the old visibility was not private and the new visibility is private. It does not change data itself; it answers a permission question.

**Call relations**: This method is part of the object-store permission flow inherited from `MemberReadableObjects`. The wider object system asks it when deciding whether an admin may apply a change, before `_apply_owned` performs the actual update.


##### `SiteObjects._member_rows`  (lines 131–171)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Builds the list of site objects a member can read in the workspace object system. Each row contains the object name, owner information, visibility, creation details, and optionally the public URL.

**Data flow**: It receives the extension context and, optionally, the current member ID. It reads all hosted sites, names them, fetches agent visibilities, fetches owner email addresses, computes effective visibility for each site, and packages everything into owned object rows. The output is a tuple of rows ready for listing and filtering.

**Call relations**: This is the main listing path for the `site` object kind. It calls `_workspace` and `_sites` to reach workspace data, `_named` and `_summary` to shape site records for display, `effective_visibility` to apply the homepage rule, `owner_emails` to add creator emails, and `site_url` when a public base URL exists.

*Call graph*: calls 5 internal fn (_named, _sites, _summary, _workspace, effective_visibility); 4 external calls (__init__, __init__, owner_emails, site_url).


##### `SiteObjects.member_conversation_rows`  (lines 173–198)

```
async def member_conversation_rows(self, ext: ExtensionContext | None, conversation_id: UUID, *, member_id: UUID, admin: bool, limit: int) -> tuple[ConversationObjectGrant, ...]
```

**Purpose**: Reports which hosted sites from a specific conversation should be visible as conversation objects to a member. This lets chat and conversation views see the same site visibility rules as the site frame.

**Data flow**: It receives the extension context, conversation ID, member ID, admin flag, and a limit. It reads agent visibility settings, asks the site registry for sites visible in that conversation, converts each site to its object name, and returns grants that mark the content as visible.

**Call relations**: The conversation object system calls this when it needs objects connected to one conversation. This method uses `_workspace` for agent visibility, `_sites` for the filtered site query, and `site_object_name` so each grant names the same object that the general listing uses.

*Call graph*: calls 3 internal fn (_sites, _workspace, site_object_name); 1 external calls (__init__).


##### `SiteObjects._member_object`  (lines 200–223)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[SiteSpec] | None
```

**Purpose**: Returns detailed information for one site object, if it exists. The detail includes the site’s effective visibility and a link back to the conversation where it was created.

**Data flow**: It receives the extension context, object name, owner record, and optional member ID. It looks up the hosted site by name; if none exists, it returns `None`. Otherwise it computes effective visibility, builds a `SiteSpec`, adds timestamps and a `created_in` link, and returns an object detail record.

**Call relations**: This is used when the object system needs to inspect one site rather than list many. It calls `_find` for lookup, `_workspace` for agent visibility, and `effective_visibility` so homepage-bound sites display the agent-controlled level.

*Call graph*: calls 3 internal fn (_find, _workspace, effective_visibility); 4 external calls (__init__, __init__, __init__, __init__).


##### `SiteObjects._status`  (lines 225–241)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Provides tool-facing status information for a hosted site, such as the actual site name, sandbox port, creator ID, and public URL. This is the practical “where is it and who made it?” view.

**Data flow**: It receives a tool context, object name, and owner record. It finds the site; if missing, it returns `None`. If found, it builds a dictionary with basic site facts and constructs the hosted URL from the public base URL, workspace ID, conversation ID, and site name.

**Call relations**: Tool status requests call this after an object has been identified. It uses `_find` to resolve the object name, `_workspace` to get the workspace ID, and `site_url` to produce the link a user can open.

*Call graph*: calls 2 internal fn (_find, _workspace); 1 external calls (site_url).


##### `SiteObjects._apply_owned`  (lines 243–261)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SiteSpec, old: SiteSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: Applies a visibility change to an existing hosted site. It refuses creation, refuses changes to homepage-bound sites, and only updates the site registry when the requested visibility is genuinely different.

**Data flow**: It receives a tool context, object name, requested site spec, old spec, and owner record. If there is no old object, it raises an error because sites must be created by deployment. It finds the current site, compares the requested visibility with the effective current visibility, refuses direct changes for agent homepages, and otherwise writes the new visibility to the hosted-site registry.

**Call relations**: The object system calls this when a user applies a manifest to a site object. It calls `_find` to confirm the site still exists, `_workspace` and `effective_visibility` to enforce the homepage rule correctly, and `_sites` to persist the visibility update.

*Call graph*: calls 4 internal fn (_find, _sites, _workspace, effective_visibility); 1 external calls (__init__).


##### `SiteObjects._delete_owned`  (lines 263–267)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: Unhosts an existing site by removing it from the site registry. After this, the hosted link should stop resolving through the registry.

**Data flow**: It receives a tool context, object name, and owner record. It looks up the site; if it has already disappeared, it raises an error. If found, it asks the hosted-site registry to unregister that conversation-and-site-name pair.

**Call relations**: The object system calls this when a permitted user deletes a site object. It uses `_find` to translate the object name back to a hosted site record, then `_sites` to perform the unregister operation.

*Call graph*: calls 2 internal fn (_find, _sites).


##### `SiteObjects._find`  (lines 269–270)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> HostedSite | None
```

**Purpose**: Finds one hosted site by its workspace object name. It is the shared lookup helper for detail, status, apply, and delete operations.

**Data flow**: It receives the extension context and an object name. It reads all hosted sites from the registry, converts them into a name-to-site dictionary, and returns the matching site if present. If there is no match, it returns `None`.

**Call relations**: `_member_object`, `_status`, `_apply_owned`, and `_delete_owned` all call this before acting on one site. It calls `_sites` for the current registry contents and `_named` so lookup uses exactly the same object-name rule as listing.

*Call graph*: calls 2 internal fn (_named, _sites); called by 4 (_apply_owned, _delete_owned, _member_object, _status).


### Agent publishing tools
Agent-facing tools build, serve, publish, and assign hosted sites while enforcing path, port, ownership, log, and visibility safety.

### `extensions/sites/ufo_ext_sites/tools.py`

`orchestration` · `tool execution during build, serve, publish, and homepage-binding requests`

This file is the bridge between an agent saying “build and show this website” and the sandbox actually running commands to make that happen. A sandbox is an isolated working area where commands can run without freely touching the host machine. The tools here build projects, start background web servers, wait until the server is really listening, and then, when needed, register that live port as a hosted site with a stable link.

A key concern is safety. Every user-supplied path is converted into a workspace-scoped path before it reaches the shell, so a model cannot accidentally or deliberately point commands outside the allowed work area. Server logs are written under the tool-output area, not mixed into the user’s project files. Before a server starts, the log filename is cleared in a guarded way, and the shell redirect is set to fail rather than overwrite an unexpected link.

The file also protects people’s access choices. Publishing a site is not just a technical act; it may decide who can open the link. So visibility changes require the right acting member and, in some cases, a live speaker. The same care applies to homepages, because binding a site as a homepage changes which audience rules apply to it.

#### Function details

##### `StartServerInput.validate_port`  (lines 144–147)

```
def validate_port(self) -> 'StartServerInput'
```

**Purpose**: This checks that a requested server port is a real TCP port number. It prevents impossible or unsafe values before any command tries to start a server.

**Data flow**: It reads the port value from the start-server input. If no port was supplied, it leaves the input alone. If a port was supplied, it must be between 1 and 65535; otherwise the input is rejected with a clear error.

**Call relations**: This runs automatically as part of validating StartServerInput before the start_server tool uses the data. It acts like a bouncer at the door, stopping bad port numbers before _serve is asked to launch anything.


##### `_json_result`  (lines 186–187)

```
def _json_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: This turns a plain Python dictionary into the standard tool response format. The other tools use it so their answers all come back as JSON text.

**Data flow**: It receives a dictionary of result information, converts that dictionary to a JSON string, wraps the string as text content, and then wraps that content in a ToolResult. Nothing else is changed.

**Call relations**: The public tool functions call this at the end, after they have built a site, started a server, hosted a link, or bound a homepage. It is the shared final packaging step before the caller receives the tool output.

*Call graph*: called by 5 (deploy_website, publish_website, set_homepage, start_server, website); 3 external calls (__init__, __init__, dumps).


##### `_free_log`  (lines 190–206)

```
async def _free_log(ctx: ToolContext, log: str) -> None
```

**Purpose**: This safely clears the chosen log filename before a background server writes to it. Its main job is to avoid a planted link or old file causing the server log redirect to overwrite something unintended.

**Data flow**: It receives the tool context and the log path. It runs a small Python program inside the sandbox that checks the path is contained in the workspace area, creates needed parent folders, and removes any existing file at that exact safe location. If that cleanup fails, it raises an error instead of starting the server.

**Call relations**: _serve calls this before launching any background server. That means start_server, deploy_website, and publish_website all get the same log-safety behavior through _serve.

*Call graph*: called by 1 (_serve).


##### `_serve`  (lines 209–259)

```
async def _serve(ctx: ToolContext, command: str, project: str, port: int, log: str) -> dict[str, object]
```

**Purpose**: This starts a web server in the sandbox and waits until it is actually reachable. It avoids returning too early, which would leave the caller with a URL that may not work yet.

**Data flow**: It receives a command, project directory, port, and log path. First it safely clears the log name. Then it kills any old process using the same port, starts the new command in the background with its output going to the log, and repeatedly tries to connect to the port. If the server answers, it returns the sandbox-local URL, port, and log path. If it fails, it reads the end of the log and raises an error with useful details.

**Call relations**: start_server uses this for scratch servers, while deploy_website and publish_website use it before registering a hosted link. It hands off log cleanup to _free_log and returns the proven live server details to the higher-level tool.

*Call graph*: calls 1 internal fn (_free_log); called by 3 (deploy_website, publish_website, start_server); 1 external calls (quote).


##### `_refuse_before_serving`  (lines 262–301)

```
async def _refuse_before_serving(ctx: ToolContext, raw_name: str, port: int, visibility: Visibility | None) -> str
```

**Purpose**: This checks whether hosting a site would be allowed before the tool kills or replaces anything on the serving port. It is a guardrail that prevents a refused publish from breaking an existing live site.

**Data flow**: It receives the requested site name, port, and optional visibility choice. It checks that extension state exists, that an acting member owns the action, and that visibility changes have a live speaker when required. It normalizes the site name, verifies a URL can be formed, asks the hosted-site store whether registration would be refused, and returns the safe normalized name if everything is allowed.

**Call relations**: deploy_website and publish_website call this before _serve. Only after this preflight passes do they take over the port and start serving. Later, _host repeats the important checks when it actually writes the hosted-site record.

*Call graph*: called by 2 (deploy_website, publish_website); 3 external calls (__init__, site_name, site_url).


##### `_host`  (lines 304–341)

```
async def _host(ctx: ToolContext, raw_name: str, port: int, visibility: Visibility | None) -> dict[str, object]
```

**Purpose**: This records a just-started server port as a hosted site and builds the stable public link for it. It turns a running sandbox server into something a member can open through the site system.

**Data flow**: It receives a raw site name, port, and optional visibility. It confirms there is extension state and an acting owner, checks speaker rules for explicit visibility, normalizes the name, builds the hosted URL, and writes the registration to the hosted-site store. It returns the final site name, visibility, object name, and public site URL.

**Call relations**: deploy_website and publish_website call this only after _serve has proved the port is live. It works with the same hosted-site rules checked earlier by _refuse_before_serving, but this time it performs the actual registration.

*Call graph*: called by 2 (deploy_website, publish_website); 4 external calls (__init__, site_object_name, site_name, site_url).


##### `website`  (lines 344–353)

```
async def website(ctx: ToolContext, args: WebsiteInput) -> ToolResult
```

**Purpose**: This runs a build command for a website project inside the sandbox. It is used when the agent needs to produce the site files but not necessarily serve or publish them yet.

**Data flow**: It receives a build command, an optional project path, and a plain-language description. It converts the project path into a safe workspace path, runs the command there with a build timeout, and stops with an error if the command fails. If the build succeeds, it lists the files in the project directory and returns the project path plus that file list as JSON.

**Call relations**: This is one of the exported site tools. Unlike deploy_website and publish_website, it does not call _serve or _host; it only builds and reports what was produced, then uses _json_result to format the answer.

*Call graph*: calls 1 internal fn (_json_result); 2 external calls (quote, workspace_path).


##### `start_server`  (lines 356–361)

```
async def start_server(ctx: ToolContext, args: StartServerInput) -> ToolResult
```

**Purpose**: This starts a background server in the sandbox without publishing it as a hosted site. It is useful for previewing or testing something at a local sandbox URL.

**Data flow**: It receives a command, project path, optional port, optional log file, and description. It chooses a default port and log path if needed, converts paths into workspace-safe paths, asks _serve to launch the server and wait for readiness, then returns the server URL, port, log path, and project path as JSON.

**Call relations**: This public tool is a thin wrapper around _serve. It deliberately does not call _host, because a temporary scratch server should not become a permanent deliverable link.

*Call graph*: calls 2 internal fn (_json_result, _serve); 1 external calls (workspace_path).


##### `deploy_website`  (lines 364–371)

```
async def deploy_website(ctx: ToolContext, args: DeployWebsiteInput) -> ToolResult
```

**Purpose**: This serves an already-built static website folder and registers it as a hosted site with a stable link. It is meant for turning files like index.html into a member-openable deliverable.

**Data flow**: It receives a project path, site name, entry file, optional visibility, and description. First it asks _refuse_before_serving whether hosting would be allowed. Then it serves the directory with Python’s simple HTTP server on the standard app port, waits for readiness through _serve, registers the live port through _host, and returns both the sandbox-local serving details and the hosted-site details as JSON.

**Call relations**: This tool ties together the main deployment path: preflight permission check, safe server launch, hosted registration, and result packaging. It calls _refuse_before_serving before taking over the port, then _serve, then _host, then _json_result.

*Call graph*: calls 4 internal fn (_host, _json_result, _refuse_before_serving, _serve); 1 external calls (workspace_path).


##### `publish_website`  (lines 374–388)

```
async def publish_website(ctx: ToolContext, args: PublishWebsiteInput) -> ToolResult
```

**Purpose**: This publishes a fuller web app, optionally installing dependencies and optionally running a backend command, then hosting the result at a stable link. It covers cases where serving the final files may require setup or a custom run command.

**Data flow**: It receives project and build-output paths, an app name, optional visibility, optional install command, optional run command, and description. It first checks hosting permission. If an install command was supplied, it runs that in the project directory and stops on failure. It then chooses either the custom run command or a simple static-file server, picks the correct directory to run from, starts and probes the server through _serve, registers it through _host, and returns the combined result as JSON.

**Call relations**: This is the more flexible publishing flow beside deploy_website. It uses the same permission, serving, hosting, and JSON packaging helpers, but adds an optional install step and supports a custom server command.

*Call graph*: calls 4 internal fn (_host, _json_result, _refuse_before_serving, _serve); 2 external calls (quote, workspace_path).


##### `set_homepage`  (lines 391–434)

```
async def set_homepage(ctx: ToolContext, args: SetHomepageInput) -> ToolResult
```

**Purpose**: This makes an existing hosted site the homepage for the acting agent. The homepage is the site the portal shows for that agent, so changing it also changes which audience rules control access.

**Data flow**: It receives a site object name and description. It loads all hosted sites, finds the one with the matching object name, and rejects the request if the site does not exist. It checks that the acting member is the site creator and, unless the site was deployed in this same turn, that a live speaker is present. If allowed, it stores the homepage pointer and returns the site name, site URL, effective agent visibility, and homepage agent ID as JSON.

**Call relations**: This public tool works with the hosted-site store rather than starting a server. It uses site_object_name to match the caller’s site reference, site_url to report the link, ToolContext.agent_visibility to explain the resulting access rule, and _json_result to return the final answer.

*Call graph*: calls 2 internal fn (agent_visibility, _json_result); 3 external calls (__init__, site_object_name, site_url).


### Preview ingress delivery
The public ingress server validates signed access and proxies browser HTTP and WebSocket traffic to the correct sandbox-hosted site.

### `core/src/ufo/ingress_serve.py`

`entrypoint` · `startup and request handling`

This file is the front door for sites served out of sandboxes. Each sandbox site gets a special hostname that encodes which conversation and port it belongs to. When a browser visits that hostname, this server reads the hostname, checks a signed cookie or link token, looks up the live sandbox, and forwards traffic to the correct internal address.

The file matters because sandbox sites are agent-authored code. They cannot be trusted to protect the platform’s cookies, caching rules, framing rules, or other sites. So this ingress acts like a careful doorman and mailroom at once: it only lets authorized visitors in, removes dangerous headers, prevents shared caches from storing private site bytes, keeps the platform’s own cookies away from sandbox code, and stops one hosted site from opening a WebSocket into another.

It supports both ordinary HTTP requests and WebSockets. HTTP bodies and responses are streamed rather than buffered all at once, so large uploads or downloads do not have to fit in memory. WebSockets are relayed in both directions until either side closes. Startup code loads configuration, database access, observability, extension manifests, the sandbox carrier, and finally starts Uvicorn, the ASGI web server.

#### Function details

##### `IngressServe.app`  (lines 220–252)

```
def app(self) -> FastAPI
```

**Purpose**: Builds the FastAPI application and declares which URLs belong to the ingress server itself and which should be forwarded to the sandbox site. It protects the special token-opening path so tokens are not accidentally passed through to untrusted sandbox code.

**Data flow**: It starts with the configured IngressServe object → creates a FastAPI app → adds HTTP routes for opening view tokens, proxying normal site paths, and refusing invalid token paths → adds matching WebSocket routes → returns the ready-to-run application.

**Call relations**: This is used when the process starts serving traffic. The run function creates an IngressServe and hands this app to Uvicorn, after which incoming HTTP and WebSocket requests are dispatched to the route methods declared here.

*Call graph*: 1 external calls (FastAPI).


##### `IngressServe._no_view_token`  (lines 254–260)

```
async def _no_view_token(self, request: Request) -> Response
```

**Purpose**: Answers visits to the special view-link path when no token was provided. This prevents an empty or malformed open-site URL from being treated as a real site request.

**Data flow**: It receives an HTTP request → if the method is not GET or HEAD, it returns a 405 response saying only those methods are allowed → otherwise it returns a plain 403 message saying the link is not valid.

**Call relations**: The app route table sends bare view-path requests here before the catch-all proxy can see them. It produces a final response itself and does not forward anything to the sandbox.

*Call graph*: 1 external calls (Response).


##### `IngressServe._open`  (lines 262–304)

```
async def _open(self, request: Request, view_path: str) -> Response
```

**Purpose**: Turns a one-time-style view link into a short-lived site session cookie for this exact site origin. This is how a viewer who clicks a signed link gets access to the hosted site without exposing the token to the sandbox path.

**Data flow**: It receives the request and the path after the view marker → splits out the token and optional entry path → reads the site identity from the hostname → verifies the token and checks it matches that hostname’s conversation and port → mints a session token with a fresh expiry → returns a redirect to the intended site path and sets the session cookie.

**Call relations**: The app routes call this for token-opening HTTP requests. It relies on _site to understand the hostname, token helpers to verify and mint tokens, and set_session_cookie to attach the new session before handing the browser back to the normal proxy path.

*Call graph*: calls 1 internal fn (_site); 8 external calls (replace, now, RedirectResponse, Response, mint_ingress_token, verify_ingress_token, set_session_cookie, quote).


##### `IngressServe._site`  (lines 306–317)

```
def _site(self, request: HTTPConnection) -> tuple[UUID, int] | None
```

**Purpose**: Figures out which sandbox site a request is addressing by reading its hostname. If the hostname is not one of this ingress server’s signed site names, it returns nothing.

**Data flow**: It receives an HTTP or WebSocket connection → takes the request hostname → checks that it ends with the configured base host → parses the remaining label into a conversation ID and port → returns that pair, or returns None if anything does not match.

**Call relations**: _open uses this to make sure a view token is being opened on the right site address. _dial_site uses it as the first gate before checking cookies and finding the sandbox.

*Call graph*: called by 2 (_dial_site, _open); 1 external calls (parse_site_label).


##### `IngressServe._dial_site`  (lines 319–365)

```
async def _dial_site(self, connection: HTTPConnection) -> DialedSite | SiteRefusal
```

**Purpose**: Performs the shared access check and sandbox lookup for both HTTP and WebSocket traffic. It decides whether the connection is allowed and, if so, where inside the sandbox network to send it.

**Data flow**: It receives a request-like connection → reads the addressed site from the hostname → verifies the ingress session cookie → checks that the cookie’s conversation and port match the hostname → looks up the stored sandbox handle in the workspace database → asks the carrier to dial the requested port → returns either a DialedSite with claims and target address, or a SiteRefusal with a status and message.

**Call relations**: Both _proxy and _socket call this before contacting sandbox code. It calls _site for hostname parsing, _stored_handle for database lookup, and the carrier for the live network target; failures become clear refusal responses instead of leaked internal errors.

*Call graph*: calls 2 internal fn (_site, _stored_handle); called by 2 (_proxy, _socket); 8 external calls (__init__, __init__, __init__, now, warn, verify_ingress_token, sandbox_handle_id, ws).


##### `IngressServe._proxy`  (lines 367–419)

```
async def _proxy(self, request: Request, path: str) -> Response
```

**Purpose**: Forwards an authorized ordinary HTTP request to the sandbox site and streams the sandbox’s response back to the browser. It also rewrites headers so the sandbox cannot break framing, poison caches, or steal platform cookies.

**Data flow**: It receives a browser request and path → calls _dial_site to authorize and locate the sandbox → builds the upstream URL and safe request headers → streams the request body when needed → sends the request with the shared HTTP client → streams the response body back → drops or rewrites dangerous response headers, confines cookies, adds no-store caching and frame rules → returns the final response.

**Call relations**: This is the catch-all HTTP route installed by app. It depends on _dial_site for permission, _upstream_url and _upstream_headers to build the sandbox request, _body to stream bytes safely, and _unframed_policy/_confined_cookie to sanitize what comes back.

*Call graph*: calls 6 internal fn (_body, _confined_cookie, _dial_site, _unframed_policy, _upstream_headers, _upstream_url); 7 external calls (stream, Response, StreamingResponse, Request, BackgroundTask, log_error, ws).


##### `IngressServe._upstream_url`  (lines 421–424)

```
def _upstream_url(self, scheme: str, host: str, path: str, query_string: bytes) -> str
```

**Purpose**: Builds the exact URL used to contact the sandbox service. It preserves the requested path and query string while safely quoting path characters.

**Data flow**: It receives a scheme such as http or ws, an upstream host, a path, and raw query bytes → quotes the path using the allowed safe characters → appends the decoded query string if present → returns the full upstream URL string.

**Call relations**: _proxy uses this for HTTP forwarding, and _socket uses it for WebSocket forwarding. It is the small shared step that keeps both protocols addressing the sandbox path in the same way.

*Call graph*: called by 2 (_proxy, _socket); 1 external calls (quote).


##### `IngressServe._stored_handle`  (lines 426–436)

```
async def _stored_handle(self, workspace_id: UUID, conversation_id: UUID) -> str | None
```

**Purpose**: Looks up the saved sandbox handle for a conversation in a workspace. This tells the ingress which running sandbox container or session should receive traffic.

**Data flow**: It receives a workspace ID and conversation ID → opens a workspace database transaction → selects the matching conversation row’s sandbox handle → returns the handle string, or None if no row is found.

**Call relations**: _dial_site calls this after token checks pass. Its result is turned into a backend-specific sandbox handle before the carrier is asked to dial the port.

*Call graph*: called by 1 (_dial_site); 2 external calls (select, workspace_tx).


##### `IngressServe._upstream_headers`  (lines 438–472)

```
def _upstream_headers(self, request: HTTPConnection, dial_headers: Mapping[str, str]) -> list[tuple[str, str]]
```

**Purpose**: Creates the header list that the sandbox site is allowed to see. It forwards the viewer’s real headers where appropriate, removes transport-only or sensitive fields, strips the ingress session cookie, and adds headers required by the sandbox dial target.

**Data flow**: It receives the incoming connection and dial-supplied headers → walks through request headers → skips hop-by-hop headers, Host, WebSocket handshake headers, and names overridden by the dial target → removes the reserved ingress session cookie from Cookie headers → appends the dial headers → returns the final list of header pairs.

**Call relations**: _proxy uses this when sending HTTP requests upstream, and _socket uses it when opening an upstream WebSocket. It is a key safety boundary between the browser/platform side and agent-authored sandbox code.

*Call graph*: called by 2 (_proxy, _socket).


##### `IngressServe._unframed_policy`  (lines 474–486)

```
def _unframed_policy(self, policy: str) -> str
```

**Purpose**: Removes only the frame-control part from a site’s Content-Security-Policy header. This lets the platform decide where hosted sites may be embedded while preserving the site’s other browser protections.

**Data flow**: It receives one Content-Security-Policy header value → splits it into directives → drops any frame-ancestors directive → joins the remaining directives → returns the rewritten policy, or an empty string if nothing remains.

**Call relations**: _proxy calls this while copying response headers from the sandbox. The proxy then adds its own frame-ancestors rule so every hosted site follows the platform’s framing rule.

*Call graph*: called by 1 (_proxy).


##### `IngressServe._confined_cookie`  (lines 488–515)

```
def _confined_cookie(self, header: str) -> str | None
```

**Purpose**: Rewrites or rejects a Set-Cookie header from the sandbox so the site can only set cookies for its own host. This stops sandbox code from setting platform cookies or parent-domain cookies that would affect other sites.

**Data flow**: It receives one Set-Cookie header value → reads the cookie name → rejects missing, empty, or reserved ufo-prefixed names → removes any Domain attribute → returns the safer cookie header, or None if the cookie must be dropped.

**Call relations**: _proxy calls this for each Set-Cookie response header from the sandbox. Only cookies that pass this confinement step are sent on to the viewer’s browser.

*Call graph*: called by 1 (_proxy).


##### `IngressServe._body`  (lines 517–527)

```
async def _body(self, upstream: httpx.Response) -> AsyncIterator[bytes]
```

**Purpose**: Streams raw response bytes from the sandbox to the browser and makes sure the upstream response is closed afterward. This avoids loading the whole response into memory and avoids leaking pooled connections if streaming stops early.

**Data flow**: It receives an httpx upstream response → yields each raw byte chunk as it arrives → in all cases, whether the stream finishes or fails, closes the upstream response.

**Call relations**: _proxy gives this iterator to StreamingResponse. It works together with the response background close, providing an extra guarantee that the upstream connection is released.

*Call graph*: called by 1 (_proxy); 2 external calls (aclose, aiter_raw).


##### `IngressServe._no_socket_view`  (lines 529–535)

```
async def _no_socket_view(self, websocket: WebSocket) -> None
```

**Purpose**: Refuses WebSocket attempts to the special view-token path. Tokens are meant to be exchanged over HTTP, not forwarded to sandbox WebSocket code.

**Data flow**: It receives a WebSocket handshake → creates a refusal saying the link is invalid → sends that refusal as an HTTP-style denial response instead of accepting the socket.

**Call relations**: The app’s WebSocket routes send token-path handshakes here. It delegates the actual denial formatting to _refuse so socket refusals look like the HTTP refusals.

*Call graph*: calls 1 internal fn (_refuse); 1 external calls (__init__).


##### `IngressServe._socket`  (lines 537–588)

```
async def _socket(self, websocket: WebSocket, path: str) -> None
```

**Purpose**: Relays an authorized WebSocket between the browser and the sandbox site. This lets hosted apps support live reload, push updates, and other two-way protocols while keeping the same access checks as HTTP.

**Data flow**: It receives a WebSocket handshake and path → checks that the Origin header matches the addressed host → calls _dial_site to authorize and locate the sandbox → builds the upstream WebSocket URL and safe headers → connects to the sandbox WebSocket with size limits and no compression → accepts the viewer socket using the upstream-selected subprotocol → relays messages both ways until one side ends or fails.

**Call relations**: This is the catch-all WebSocket route installed by app. It uses _same_origin for the WebSocket-only browser-origin check, _dial_site for the shared site gate, _upstream_url and _upstream_headers to open the sandbox connection, _relay for bidirectional copying, and _refuse/_end for clean failure paths.

*Call graph*: calls 7 internal fn (_dial_site, _end, _refuse, _relay, _same_origin, _upstream_headers, _upstream_url); 6 external calls (__init__, accept, log_error, ws, connect, Subprotocol).


##### `IngressServe._same_origin`  (lines 590–602)

```
def _same_origin(self, websocket: WebSocket) -> bool
```

**Purpose**: Checks that a WebSocket was opened by the same site hostname it is trying to reach. This closes a browser loophole where one hosted site could otherwise open a socket to another hosted site while the browser attaches that other site’s cookie.

**Data flow**: It receives a WebSocket connection → reads the Origin header → parses its hostname → compares it with the WebSocket request hostname → returns true only when they match and the Origin header exists.

**Call relations**: _socket calls this before any session or sandbox dial work. A mismatch is immediately refused, because WebSockets do not get the same read-protection rules that normal cross-origin HTTP responses get from browsers.

*Call graph*: called by 1 (_socket); 1 external calls (urlsplit).


##### `IngressServe._refuse`  (lines 604–611)

```
async def _refuse(self, websocket: WebSocket, refusal: SiteRefusal) -> None
```

**Purpose**: Rejects a WebSocket handshake with a clear HTTP-style status and message. This lets a denied socket show the same kind of explanation as a denied HTTP request.

**Data flow**: It receives a WebSocket object and a SiteRefusal → builds a plain text Response with the refusal’s status and message → sends it as a WebSocket denial response without accepting the socket.

**Call relations**: _no_socket_view and _socket use this whenever a WebSocket should not be opened. It centralizes refusal behavior so both protocols share the same user-facing messages.

*Call graph*: called by 2 (_no_socket_view, _socket); 2 external calls (send_denial_response, Response).


##### `IngressServe._relay`  (lines 613–630)

```
async def _relay(self, viewer: WebSocket, upstream: ClientConnection) -> None
```

**Purpose**: Runs the two halves of a WebSocket relay at the same time: browser to sandbox, and sandbox to browser. It stops the whole relay as soon as either half finishes, because an open socket is only useful while both sides are present.

**Data flow**: It receives the viewer WebSocket and upstream WebSocket connection → starts one task for each direction → waits until the first task completes → cancels the other task → raises any real failure from the completed direction.

**Call relations**: _socket calls this after both WebSocket connections are established. It coordinates _viewer_to_site and _site_to_viewer so neither side is left waiting forever after the other side goes away.

*Call graph*: calls 2 internal fn (_site_to_viewer, _viewer_to_site); called by 1 (_socket); 3 external calls (create_task, gather, wait).


##### `IngressServe._viewer_to_site`  (lines 632–641)

```
async def _viewer_to_site(self, viewer: WebSocket, upstream: ClientConnection) -> None
```

**Purpose**: Copies WebSocket messages from the browser to the sandbox site. It preserves whether each message is text or bytes, because many WebSocket protocols care about that distinction.

**Data flow**: It repeatedly receives messages from the viewer → if the viewer disconnects, it returns → otherwise it sends either the text payload or byte payload to the upstream sandbox connection.

**Call relations**: _relay starts this as one of the two relay tasks. It is paired with _site_to_viewer to provide full two-way WebSocket communication.

*Call graph*: called by 1 (_relay); 2 external calls (receive, send).


##### `IngressServe._site_to_viewer`  (lines 643–656)

```
async def _site_to_viewer(self, upstream: ClientConnection, viewer: WebSocket) -> None
```

**Purpose**: Copies WebSocket messages from the sandbox site back to the browser and forwards an appropriate close code when the site ends the connection. It avoids sending WebSocket close codes that the standard reserves for local-only observations.

**Data flow**: It reads messages from the upstream sandbox connection → sends text messages as text and binary messages as bytes to the viewer → when upstream closes, chooses a safe close code and reason → asks _end to close the viewer socket.

**Call relations**: _relay starts this as the return direction of the WebSocket bridge. It calls _end for the final close so shutdown is tolerant of viewers that have already disappeared.

*Call graph*: calls 1 internal fn (_end); called by 1 (_relay); 3 external calls (suppress, send_bytes, send_text).


##### `IngressServe._end`  (lines 658–668)

```
async def _end(self, viewer: WebSocket, code: int, reason: str) -> None
```

**Purpose**: Closes the viewer WebSocket without letting close-time errors create a second failure. This is important because disconnected browsers can be reported in several different ways by the server stack.

**Data flow**: It receives the viewer socket, a close code, and a reason → tries to send a WebSocket close frame → suppresses any exception because the connection is already effectively over.

**Call relations**: _site_to_viewer uses this for normal upstream endings, and _socket uses it after relay failures. It is the final cleanup step for WebSocket sessions.

*Call graph*: called by 2 (_site_to_viewer, _socket); 2 external calls (suppress, close).


##### `ingress_base_host`  (lines 671–682)

```
def ingress_base_host(configured: str | None) -> str
```

**Purpose**: Extracts the wildcard base hostname under which all hosted site names must live. It refuses startup if this required public ingress URL is missing or unusable.

**Data flow**: It receives the configured ingress public URL or None → parses it → reads the hostname → returns that hostname, or raises a runtime error if no hostname exists.

**Call relations**: run calls this during startup before creating IngressServe. The resulting base host is later used by _site to decide whether each request belongs to this ingress deployment.

*Call graph*: called by 1 (run); 1 external calls (urlsplit).


##### `ingress_frame_ancestor`  (lines 685–693)

```
def ingress_frame_ancestor(configured: str | None) -> str
```

**Purpose**: Builds the single allowed page origin that may frame hosted sites. If the main app public URL is not configured, it returns a browser policy value that allows no framing.

**Data flow**: It receives the configured connect public base URL or None → parses scheme, hostname, and optional port → returns an origin like https://example.com, or the special value 'none' when no valid origin is configured.

**Call relations**: run calls this during startup and stores the result on IngressServe. _proxy later uses it in every proxied response’s Content-Security-Policy header.

*Call graph*: called by 1 (run); 1 external calls (urlsplit).


##### `upstream_client`  (lines 696–719)

```
def upstream_client() -> httpx.AsyncClient
```

**Purpose**: Creates the shared HTTP client used to contact sandbox sites, with timeouts, connection limits, and a cookie jar that stores nothing. This prevents cookies from one sandbox response from being replayed to another sandbox.

**Data flow**: It creates an empty cookie jar with a policy that allows no domains → creates an async HTTP client with configured timeout and connection-pool limits → returns that client for reuse.

**Call relations**: run calls this once at startup and passes the client into IngressServe. _proxy uses that client for upstream HTTP requests, while request construction and the no-store cookie policy keep sandbox cookies from becoming process-wide state.

*Call graph*: called by 1 (run); 4 external calls (CookieJar, DefaultCookiePolicy, AsyncClient, Limits).


##### `run`  (lines 722–743)

```
def run() -> None
```

**Purpose**: Starts the ingress server process. It loads configuration, prepares logging and database access, creates the ingress service, and hands its web app to Uvicorn.

**Data flow**: It loads config → initializes observability → loads extension manifests → initializes and checks the database → ensures the ingress signing secret is available → selects the sandbox carrier and creates the upstream HTTP client → constructs IngressServe → logs startup → runs Uvicorn on the configured ingress port with WebSocket size and compression settings.

**Call relations**: This is the process entry function for the file. It wires together the helper functions ingress_base_host, ingress_frame_ancestor, and upstream_client with outside services such as config loading, database setup, manifest loading, carrier selection, and the ASGI server.

*Call graph*: calls 3 internal fn (ingress_base_host, ingress_frame_ancestor, upstream_client); 12 external calls (__init__, run, load_config, init_db, verify_db_reachable, load_manifests, init_o11y, log, owner_dsn, ingress_secret (+2 more)).


### Delegation and registry backing
Website-building delegation and the hosted-site registry keep builds tied to the originating sandbox while preserving ownership, routing, and privacy records.

### `extensions/sites/ufo_ext_sites/delegation.py`

`orchestration` · `request handling`

This file exists so the main agent does not have to build and validate a website by itself. Instead, it can describe the desired site and delegate the work to a specialized child agent focused on web builds. Think of it like asking a specialist contractor to build a room in the same house: the contractor has their own notes and work session, but the room they build is still in your house.

The main pieces are the tool name and description, the input shape, and the actual handoff function. `BuildWebsiteInput` spells out what the main agent must provide: a complete objective, an optional friendly task name, optional skills to preload, whether the child should get more working time, and a plain user-facing description for the activity timeline.

When the tool runs, `_build_website` calls `ctx.spawn`, which starts the website-building profile as a child turn. It passes along the build details, but leaves out `user_description` because that is for display rather than for the child’s build instructions. The spawn uses the current call’s idempotency key, meaning if the system retries after a crash, it can reconnect to the same delegated build instead of accidentally starting a duplicate one.

The file then exposes this as `DELEGATION_TOOLS`, marking it as side-effecting because it can create files, start hosting, and register a site.

#### Function details

##### `_build_website`  (lines 57–64)

```
async def _build_website(ctx: ToolContext, args: BuildWebsiteInput) -> ToolResult
```

**Purpose**: This function performs the actual delegation when the `build_website` tool is used. It starts the website-building subagent, waits for its result, and wraps that result in the standard tool response format.

**Data flow**: It receives the current tool context and a `BuildWebsiteInput` object containing the website objective and build options. It turns the input into plain data, leaving out empty fields and excluding `user_description`, then sends that data to `ctx.spawn` to start the website-building profile. When the child finishes, it converts the child’s output to JSON text if there is any output, places that text inside a `TextContent` object, and returns it as a `ToolResult`.

**Call relations**: This function is registered as the handler for the `build_website` tool in `DELEGATION_TOOLS`. When the main agent calls that tool, the tool system invokes `_build_website`; `_build_website` then hands the job to `ToolContext.spawn`, using the website-building profile name and the call’s idempotency key so retries reconnect to the same spawned work.

*Call graph*: 4 external calls (__init__, __init__, spawn, model_dump).


### `extensions/sites/ufo_ext_sites/store.py`

`domain_logic` · `request handling`

A hosted site here is like a forwarding card at a front desk: for a given workspace, conversation, and site name, it says which running sandbox port should receive visitors. This file defines that card, stores it in the database, and enforces the rules around changing it.

The main class, HostedSites, is scoped to one workspace. That matters because the database connection can see the whole system, so every query must deliberately include the workspace ID. The registry lets callers create or update a site, read it back, list sites, change visibility, bind a site as an agent homepage, or remove the link.

The most important behavior is protection against accidental takeovers. A port can only serve one site for a conversation. If a new deploy would reuse a port already claimed by another site name, the old site may need to be unhosted. This file refuses that unless the acting member is allowed to do it. It also protects visibility settings: only the original creator can change who may open the site. Redeploying the same name updates the port but normally keeps the existing visibility, so a teammate’s deploy does not silently reset the creator’s privacy choice.

#### Function details

##### `site_name`  (lines 90–98)

```
def site_name(raw: str) -> str
```

**Purpose**: Turns a member’s proposed site name into a safe, link-friendly name. It lowercases the text, replaces runs of non-letter-or-number characters with hyphens, trims it to the allowed length, and rejects names that contain no usable letters or digits.

**Data flow**: It receives raw text from a user or caller. It cleans that text into a compact slug suitable for links and stored object names. It returns the slug, or raises InvalidSiteName if nothing meaningful remains.

**Call relations**: This is used before a site is stored or linked, so the rest of the registry can assume names have a consistent shape. If the cleaned name is empty, it raises the file’s InvalidSiteName error to stop the deploy early.

*Call graph*: 1 external calls (__init__).


##### `default_visibility`  (lines 101–109)

```
def default_visibility(audience: Audience) -> Visibility
```

**Purpose**: Chooses the starting privacy level for a new site based on the audience of the conversation that created it. Direct messages and external rooms default to private, while normal internal or workspace-shared conversations default to workspace visibility.

**Data flow**: It receives an Audience value. It parses that audience, checks whether it represents a single member or an outside audience, and returns either "private" or "workspace".

**Call relations**: HostedSites.register calls this only when inserting a brand-new site and no explicit visibility was supplied. It relies on audience parsing helpers from ufo.sdk.audience to avoid making an externally shared conversation accidentally visible to the whole company.

*Call graph*: called by 1 (register); 2 external calls (audience_member, parse_audience).


##### `visibility_level`  (lines 112–120)

```
def visibility_level(value: str) -> Visibility
```

**Purpose**: Checks that a visibility string is one of the three supported values: private, workspace, or public. This protects the rest of the code from treating arbitrary stored text as a valid privacy level.

**Data flow**: It receives a string, compares it against the allowed visibility names, and returns the same value typed as a valid Visibility. If the string is not allowed, it raises ValueError.

**Call relations**: _site calls this while converting database rows into HostedSite objects. That means bad data is caught at the boundary where raw database text becomes application data.

*Call graph*: called by 1 (_site).


##### `HostedSites.register`  (lines 146–210)

```
async def register(self, conversation_id: UUID, name: str, port: int, creator_member_id: UUID, visibility: Visibility | None, audience: Audience, may_unhost: bool) -> HostedSite
```

**Purpose**: Creates or updates the registry row for a site after a deploy. It also removes any other site name in the same conversation that was using the same port, but only if the permission checks say that is allowed.

**Data flow**: It receives the conversation ID, cleaned site name, sandbox port, creator member ID, optional requested visibility, audience, and whether this action may unhost an existing site. Inside one database transaction, it checks for refusals, deletes a displaced same-port site if needed, updates an existing same-name site or inserts a new one, then reads back and returns the final HostedSite.

**Call relations**: This is the main write path for the registry. It asks _refuse to enforce ownership and unhosting rules, uses default_visibility for new sites without an explicit choice, and uses _read to fetch the finished row. Callers get either a registered site record or an exception explaining why registration was not allowed.

*Call graph*: calls 3 internal fn (_read, _refuse, default_visibility); 4 external calls (delete, insert, update, uuid4).


##### `HostedSites.read`  (lines 212–214)

```
async def read(self, conversation_id: UUID, name: str) -> HostedSite | None
```

**Purpose**: Looks up one site by conversation and name within this workspace. It is the simple read path used when a caller already knows exactly which permanent link entry it wants.

**Data flow**: It receives a conversation ID and site name. It opens a transaction, asks _read for the matching database row, and returns a HostedSite if found or None if the link is not registered.

**Call relations**: This is a public wrapper around the private _read helper. It gives outside callers a safe workspace-scoped lookup without exposing the raw database connection details.

*Call graph*: calls 1 internal fn (_read).


##### `HostedSites.all`  (lines 216–226)

```
async def all(self) -> tuple[HostedSite, ...]
```

**Purpose**: Returns every hosted site registered in the current workspace, ordered from oldest to newest. Higher-level code can then apply its own display or access rules.

**Data flow**: It reads all rows whose workspace ID matches this HostedSites instance, sorted by creation time and name. Each database row is converted into a HostedSite object, and the function returns them as a tuple.

**Call relations**: It builds its select statement through _columns and converts rows through _site. The method is a workspace-wide listing tool, while access gates elsewhere decide which of these sites a viewer should actually see.

*Call graph*: calls 2 internal fn (_columns, _site).


##### `HostedSites.conversation`  (lines 228–245)

```
async def conversation(self, conversation_id: UUID, names: tuple[str, ...], limit: int) -> tuple[HostedSite, ...]
```

**Purpose**: Returns selected sites from one conversation, limited to a provided set of names and a maximum count. This is useful when a caller wants to resolve or show only known site names from a conversation.

**Data flow**: It receives a conversation ID, a tuple of names, and a limit. It queries matching rows in the current workspace and conversation, sorts them oldest first, converts each row to a HostedSite, and returns a tuple.

**Call relations**: Like other list methods, it uses _columns for the shared column list and _site for row conversion. It is narrower than all because it stays inside one conversation and one requested name set.

*Call graph*: calls 2 internal fn (_columns, _site).


##### `HostedSites.visible_conversation`  (lines 247–277)

```
async def visible_conversation(self, conversation_id: UUID, member_id: UUID, limit: int, *, admin: bool, homepage_agents: frozenset[UUID]) -> tuple[HostedSite, ...]
```

**Purpose**: Lists the sites in one conversation that a particular member is allowed to open. It includes the member’s own sites, non-private sites, sites exposed through visible homepage agents, and all sites if the member is an admin.

**Data flow**: It receives a conversation ID, member ID, limit, admin flag, and a set of homepage agent IDs. It builds a database filter for the allowed visibility cases, skips that filter for admins, reads matching rows, converts them to HostedSite objects, and returns them.

**Call relations**: This is the visibility-aware listing path for frames or surfaces that need to show a member what they can access. It uses SQLAlchemy’s or_ to combine the access conditions, then relies on _columns and _site like the other query methods.

*Call graph*: calls 2 internal fn (_columns, _site); 1 external calls (or_).


##### `HostedSites.set_visibility`  (lines 279–294)

```
async def set_visibility(self, conversation_id: UUID, name: str, visibility: Visibility) -> HostedSite | None
```

**Purpose**: Changes a site’s visibility level and marks it as a new generation. The new generation ID gives readers a way to notice that something meaningful about the site’s access state changed.

**Data flow**: It receives a conversation ID, site name, and new visibility. It updates the matching row in this workspace with the new visibility, a fresh generation UUID, and a new update time, then reads and returns the updated HostedSite or None if the site no longer exists.

**Call relations**: This method performs the actual visibility write after higher-level code has decided the caller may do it. It uses SQLAlchemy update for the write and _read to return the current row shape.

*Call graph*: calls 1 internal fn (_read); 2 external calls (update, uuid4).


##### `HostedSites.set_homepage`  (lines 296–322)

```
async def set_homepage(self, agent_id: UUID, conversation_id: UUID, name: str) -> HostedSite | None
```

**Purpose**: Marks one hosted site as the homepage for an agent. It first clears any previous homepage binding for that agent in the workspace, so one agent has at most one homepage site.

**Data flow**: It receives an agent ID, conversation ID, and site name. In one transaction, it removes that agent ID from any existing hosted_site row in the workspace, sets it on the chosen site, then reads back and returns the chosen HostedSite or None if it was not found.

**Call relations**: This method supports the agent-homepage feature without changing the site’s own visibility field. It uses two database updates and then _read, so callers receive the final binding state.

*Call graph*: calls 1 internal fn (_read); 1 external calls (update).


##### `HostedSites.unregister`  (lines 324–335)

```
async def unregister(self, conversation_id: UUID, name: str) -> None
```

**Purpose**: Removes a site from the registry so its permanent link stops resolving. It does not stop the sandbox process itself; it only removes the named routing entry.

**Data flow**: It receives a conversation ID and site name. It deletes the matching row for this workspace, conversation, and name. It returns nothing after the database delete completes.

**Call relations**: This is the explicit unhost path. Other code may call it when a member asks to take a site down, while register may also delete a displaced row when a port is safely reused.

*Call graph*: 1 external calls (delete).


##### `HostedSites.refuse_or_pass`  (lines 337–355)

```
async def refuse_or_pass(self, conversation_id: UUID, name: str, port: int, creator_member_id: UUID, visibility: Visibility | None, may_unhost: bool) -> None
```

**Purpose**: Runs the same safety checks that registration would run, but without writing anything. It lets a caller test whether a deploy would be allowed before the deploy actually takes over a port.

**Data flow**: It receives the proposed conversation, name, port, member, visibility, and unhost permission. It opens a transaction and calls _refuse; if a rule is violated an exception is raised, and otherwise nothing is returned or changed.

**Call relations**: This is a preflight check. HostedSites.register repeats the same _refuse check during the real write, so the system gets both an early warning and a final enforced decision.

*Call graph*: calls 1 internal fn (_refuse).


##### `HostedSites._refuse`  (lines 357–386)

```
async def _refuse(self, connection: AsyncConnection, conversation_id: UUID, name: str, port: int, creator_member_id: UUID, visibility: Visibility | None, may_unhost: bool) -> HostedSite | None
```

**Purpose**: Centralizes the rules that can block a site registration. It protects creator-owned visibility choices and prevents one member from unhosting another member’s site by taking its port.

**Data flow**: It receives an open database connection plus the proposed site details. It reads the existing same-name site, checks whether a non-creator is trying to change visibility, looks for a different site already on the same port, and either raises an error or returns the site that would be displaced.

**Call relations**: Both refuse_or_pass and register call this so they share exactly the same refusal logic. It delegates same-name lookup to _read and same-port lookup to _on_port, then raises NotTheSiteCreator or UnhostNeedsASpeaker when the action should not proceed.

*Call graph*: calls 2 internal fn (_on_port, _read); called by 2 (refuse_or_pass, register); 2 external calls (__init__, __init__).


##### `HostedSites._on_port`  (lines 388–403)

```
async def _on_port(self, connection: AsyncConnection, conversation_id: UUID, port: int, name: str) -> HostedSite | None
```

**Purpose**: Finds whether another site name in the same conversation is already registered on the proposed port. This matters because one port can only serve one set of site bytes.

**Data flow**: It receives an open connection, conversation ID, port, and the current site name to exclude. It queries for a row in the same workspace and conversation with that port but a different name, and returns that HostedSite or None.

**Call relations**: _refuse calls this when deciding whether registration would displace another site. It uses _columns to build the shared selection and _site to turn the database row into the application’s HostedSite object.

*Call graph*: calls 2 internal fn (_columns, _site); called by 1 (_refuse); 1 external calls (execute).


##### `HostedSites._read`  (lines 405–417)

```
async def _read(self, connection: AsyncConnection, conversation_id: UUID, name: str) -> HostedSite | None
```

**Purpose**: Reads exactly one hosted site row by conversation and name within the current workspace. It is the shared low-level lookup used by the public methods and safety checks.

**Data flow**: It receives an open connection, conversation ID, and site name. It queries the database for that exact workspace-scoped row, converts it to HostedSite if present, and returns None if no row exists.

**Call relations**: HostedSites.read exposes this lookup publicly, while register, set_visibility, set_homepage, and _refuse use it internally. Keeping the lookup here ensures every path applies the same workspace and conversation filters.

*Call graph*: calls 2 internal fn (_columns, _site); called by 5 (_refuse, read, register, set_homepage, set_visibility); 1 external calls (execute).


##### `HostedSites._columns`  (lines 419–430)

```
def _columns(self) -> sa.Select
```

**Purpose**: Builds the common database select list for hosted site rows. This keeps all readers asking for the same fields in the same shape.

**Data flow**: It takes no outside data beyond the HostedSites instance. It creates a SQL select statement containing the hosted site fields needed to build a HostedSite object, and returns that statement for callers to add filters and ordering.

**Call relations**: The query methods all start with _columns, then add their own where clauses. _site expects rows shaped by this selection, so this helper keeps row reading consistent across all lookup and listing paths.

*Call graph*: called by 5 (_on_port, _read, all, conversation, visible_conversation); 1 external calls (select).


##### `_aware`  (lines 433–436)

```
def _aware(moment: datetime) -> datetime
```

**Purpose**: Ensures a stored timestamp clearly says it is in UTC time. This avoids bugs when comparing database times with other timestamps that already include timezone information.

**Data flow**: It receives a datetime from the database. If the datetime already has timezone information, it returns it unchanged; if it is missing that information, it adds UTC and returns the corrected value.

**Call relations**: _site calls this for created_at and updated_at while building HostedSite objects. This is especially important for databases such as SQLite that may return timezone-aware columns as plain, timezone-less datetimes.

*Call graph*: called by 1 (_site); 1 external calls (replace).


##### `_site`  (lines 439–450)

```
def _site(row: sa.Row) -> HostedSite
```

**Purpose**: Turns a raw database row into a HostedSite data object that the rest of the code can use safely. It validates visibility and normalizes timestamps as part of that conversion.

**Data flow**: It receives a SQLAlchemy row with hosted site fields. It reads each field, checks the visibility through visibility_level, converts created and updated times through _aware, and returns a HostedSite instance.

**Call relations**: All read and list paths use this as the final step after fetching rows. It is the small adapter between database-shaped data and the file’s domain object, HostedSite.

*Call graph*: calls 2 internal fn (_aware, visibility_level); called by 5 (_on_port, _read, all, conversation, visible_conversation); 1 external calls (__init__).
