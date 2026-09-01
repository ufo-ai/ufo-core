# Hosted Site Source, Registry, and Publishing Tools  `stage-10.3.5`

This stage is the workshop for hosted websites. It supports the main work of taking site files, preparing them in a safe workspace, and turning them into preview or public links. The small __init__.py file simply makes this folder usable as a Python package, so the rest of the system can import its parts.

source.py is the moving crew. It copies a site’s saved source files from long-term storage into a sandbox, which is an isolated work area where code can be edited or built without touching the rest of the system. It also supplies the page-building toolkit needed by app pages, so they use the current extension version.

store.py is the registry desk. It records each hosted site’s name, owner, visibility rules, and where the site lives, such as a running port or stored static files.

tools.py is the control panel used by the agent. It builds, previews, deploys, publishes, and connects a site as an agent homepage. It checks ownership, safety, visibility, and whether the site is ready before creating stable hosted links.

## Files in this stage

### Package and Source Preparation
Package setup and source checkout utilities prepare hosted-site files and page-building support inside the sandbox.

### `extensions/sites/ufo_ext_sites/__init__.py`

`other` · `import time`

In Python, a folder often needs an `__init__.py` file to be treated as a package, which means other code can import files from that folder in an organized way. This file is empty, so it does not define settings, classes, functions, or startup behavior. Its value is structural: it tells Python and project tools that `extensions/sites/ufo_ext_sites` is a named package. Without it, imports involving this folder could fail or behave differently depending on the Python version and tooling. Think of it like a label on a drawer: the label does not do the work, but it lets the rest of the system find what belongs inside.


### `extensions/sites/ufo_ext_sites/source.py`

`io_transport` · `request handling`

A hosted site is not always just a finished folder of browser files. For app pages, the editable source, the generated page, and the build tools come from different places. This file is the bridge that assembles those pieces inside a sandbox, which is an isolated workspace used for safe editing and building.

When a site is read for editing, `materialize_source` checks the site's saved source manifest, which is a list of stored files and their sizes. It chooses a workspace folder under `sites/`, checks a small generation stamp to avoid downloading the same files again, and then safely clears and recreates the target files. This safety step matters because the sandbox can contain links or old files left by previous work; blindly writing through them could overwrite the wrong place or accidentally redeploy stale files.

The actual file movement has two shapes. In production-like blob storage, the sandbox downloads files directly using short-lived signed URLs. In a local filesystem development store, this Python process reads the bytes and writes them into the sandbox itself.

The file also packages and unpacks the page SDK kit. Instead of copying many individual toolkit files one by one, it makes a compressed archive once and unpacks it in the sandbox. Think of it like packing a toolbox into one suitcase before sending it across, then opening it at the job site.

#### Function details

##### `_page_kit_archive`  (lines 101–113)

```
def _page_kit_archive() -> bytes
```

**Purpose**: This function builds one compressed archive containing the page SDK kit files bundled with this extension. It is used so the sandbox can receive one archive instead of many separate files, which is faster and simpler.

**Data flow**: It starts with the kit directory on disk and an empty in-memory byte buffer. It walks through every regular file in the kit, adds each one to a gzip-compressed tar archive under the `sdk/` mount name, and returns the finished archive as bytes.

**Call relations**: This function runs when the module is loaded to create `PAGE_KIT_ARCHIVE`. Later, `unpack_page_kit` writes those already-prepared bytes into the sandbox and expands them there, avoiding repeated archive work for every read or build.

*Call graph*: 2 external calls (BytesIO, open).


##### `transfer`  (lines 120–130)

```
async def transfer(ctx: ToolContext, script: str, pairs: list[tuple[str, str]], total_bytes: int) -> None
```

**Purpose**: This function runs a sandbox-side upload or download script for a list of files and URLs. It batches the work so a large source tree is moved in bounded chunks rather than one huge command.

**Data flow**: It receives a tool context, a shell script, file-and-URL pairs, and the total byte count. It calculates a timeout based on expected transfer size, sends the pairs to the sandbox in groups, and checks whether each sandbox command succeeded. It returns nothing on success, but raises an error if any batch fails.

**Call relations**: This helper is called by `materialize_source` when the blob store can provide signed download URLs. `materialize_source` prepares the destination paths and URLs, then hands them to `transfer`, which asks the sandbox to run `curl` for the actual byte movement.

*Call graph*: called by 1 (materialize_source).


##### `materialize_source`  (lines 133–199)

```
async def materialize_source(ctx: ToolContext, site: HostedSite, object_name: str) -> tuple[str, list[str]]
```

**Purpose**: This function recreates a hosted site's stored source tree inside the current sandbox. It is what makes the latest deployed source available for editing or rebuilding instead of relying on whatever files happen to already be in the workspace.

**Data flow**: It receives a tool context, a hosted site record, and a workspace object name. It reads the site's source manifest, chooses a destination folder, and checks a generation stamp to see whether that exact version is already present. If not, it safely clears the destination and pre-claims the files, then either downloads them through signed URLs or writes them directly from the blob store, depending on the storage backend. Finally it writes the generation stamp and returns the destination path plus the list of materialized file paths.

**Call relations**: This is the main reader-side flow in the file. It uses `SourceManifest.model_validate_json` to understand the saved file list, `workspace_path` to place files under the sandbox workspace, `shlex.quote` to safely read the stamp path in a shell command, and `transfer` when the sandbox can fetch files directly from storage. Other parts of the site system call it when they need the deployed source to appear in a conversation's sandbox.

*Call graph*: calls 1 internal fn (transfer); 3 external calls (model_validate_json, quote, workspace_path).


##### `unpack_page_kit`  (lines 202–220)

```
async def unpack_page_kit(ctx: ToolContext, dest: str, runtime_root: str | None=None) -> None
```

**Purpose**: This function places the extension's current page SDK kit into a project directory inside the sandbox. App pages use that kit during builds, so this keeps old app-page forks building against today's components rather than frozen old copies.

**Data flow**: It receives a tool context, a destination directory, and optionally a runtime root used for stricter path containment. It writes the prebuilt kit archive into the destination, runs a sandbox Python unpacking program that checks every archive member before writing it, deletes the archive during unpacking, and raises an error if unpacking fails.

**Call relations**: This function uses the archive produced earlier by `_page_kit_archive`. It does not call `materialize_source`, but it complements it: after source files are present, this function can add the build kit beside them so an app page project has the local `sdk/` files its build configuration expects.


### Site Registry
The registry records hosted-site ownership, visibility, names, ports, and stored static-file bindings.

### `extensions/sites/ufo_ext_sites/store.py`

`domain_logic` · `site deploy, site lookup, sharing, and homepage binding`

A hosted site here is like a forwarding card: within one workspace and conversation, a name such as “dashboard” points either to a sandbox port or to a stored set of static files. Without this registry, links to sites would not know where to go, old names could accidentally serve new bytes, and one person could overwrite or expose another person’s site.

The file defines the database table for hosted sites and the Python shapes used to read and validate its data. It also contains the rules around deployment. A new deployment can update an existing site, create a new one, or replace another site using the same port. But replacing a port is treated as unhosting the old site, so the code checks that the acting member is allowed to do that. Visibility is protected too: only the original creator may change whether a site is private, workspace-visible, or public.

Static deployments also store a source manifest, which is a list of files safely placed in blob storage. The validators make sure stored file paths cannot escape their expected folder. The main `HostedSites` class wraps all database reads and writes for one workspace, so every query is scoped correctly even though the database connection can see everything.

#### Function details

##### `SourceManifest._rooted`  (lines 96–99)

```
def _rooted(cls, root: str) -> str
```

**Purpose**: Checks that the stored source folder for a static site is in an allowed area and looks like a folder prefix. This prevents a manifest from pointing at unrelated storage keys.

**Data flow**: It receives the manifest root string → verifies that it starts with either `sites/` or `apps/` and ends with `/` → returns the same string if it is safe, or raises an error if it is not.

**Call relations**: Pydantic calls this validator while building a `SourceManifest`. It runs before the manifest is trusted as the source location for a static hosted site.


##### `SourceManifest._pathed`  (lines 103–118)

```
def _pathed(cls, files: dict[str, SiteFile]) -> dict[str, SiteFile]
```

**Purpose**: Checks every file path listed in a static site manifest. It makes sure each path is a plain path inside the site, not a trick path that could escape to another folder.

**Data flow**: It receives a dictionary of site-relative paths to file records → rejects leading slashes, backslashes, control characters, dot-segment escapes, and paths that normalize to something different → returns the original dictionary if every path is safe.

**Call relations**: Pydantic calls this validator when a `SourceManifest` is created. It relies on `contained_relative`, a path-safety helper, to confirm that each manifest path stays under the expected site-source area.

*Call graph*: 1 external calls (contained_relative).


##### `site_name`  (lines 149–157)

```
def site_name(raw: str) -> str
```

**Purpose**: Turns a user-supplied site name into the safe stored name used in links and object names. It also rejects names that contain no usable letters or digits.

**Data flow**: It receives raw text → lowercases it, replaces runs of non-letter-or-number characters with hyphens, trims extra hyphens, and cuts it to the maximum length → returns the cleaned name, or raises `InvalidSiteName` if nothing meaningful remains.

**Call relations**: Deployment-facing code uses this before registering a site, because the registry expects the name to already be safe. If the name cannot become a valid slug, this function stops the deploy with a clear error.

*Call graph*: 1 external calls (__init__).


##### `default_visibility`  (lines 160–168)

```
def default_visibility(audience: Audience) -> Visibility
```

**Purpose**: Chooses the starting visibility for a brand-new site based on the conversation audience. Private or externally shared contexts default to private; normal internal shared contexts default to workspace-visible.

**Data flow**: It receives an audience value → parses it and checks whether it represents a direct member audience or a foreign/external audience → returns `private` for those cases, otherwise `workspace`.

**Call relations**: `HostedSites.register` calls this only when inserting a new site and no explicit visibility was provided. It uses audience helpers to avoid accidentally making externally shared work visible to the whole workspace.

*Call graph*: called by 1 (register); 2 external calls (audience_member, parse_audience).


##### `visibility_level`  (lines 171–179)

```
def visibility_level(value: str) -> Visibility
```

**Purpose**: Accepts only the three visibility words the system understands: private, workspace, and public. It protects the rest of the code from unexpected text stored in or submitted to the visibility field.

**Data flow**: It receives a string → compares it with the allowed visibility values → returns the same value typed as a valid visibility, or raises an error for anything else.

**Call relations**: `_site` calls this while turning a database row into a `HostedSite`. That means every read from the registry re-checks that the stored visibility value is one the application can safely use.

*Call graph*: called by 1 (_site).


##### `HostedSites.register`  (lines 213–306)

```
async def register(self, conversation_id: UUID, name: str, port: int, creator_member_id: UUID, visibility: Visibility | None, audience: Audience, may_unhost: bool, *, manifest: str | None) -> HostedSi
```

**Purpose**: Creates or updates the registry row for a deployed site. It enforces the important ownership rules: only allowed members can change visibility or take over a port already used by another site.

**Data flow**: It receives the conversation, safe site name, port, creator, optional visibility, audience, unhost permission, and optional static-source manifest → opens a database transaction, checks for refusals, deletes any same-port displaced site if allowed, updates an existing row or inserts a new one, and stamps a fresh deploy generation → returns the registered `HostedSite`.

**Call relations**: This is the main write path after a deploy has produced something to serve. It calls `_refuse` for safety checks, `_read` to inspect and return rows, and `default_visibility` when creating a new site without an explicit setting.

*Call graph*: calls 3 internal fn (_read, _refuse, default_visibility); 6 external calls (case, delete, insert, update, time_ns, uuid4).


##### `HostedSites.redeploy`  (lines 308–335)

```
async def redeploy(self, conversation_id: UUID, name: str, manifest: str) -> HostedSite | None
```

**Purpose**: Replaces the stored static source for an existing site without changing its name, port, creator, or visibility. This is used when the same public link should now show newer static files.

**Data flow**: It receives the target conversation, site name, and new manifest string → updates the site’s `source_manifest`, timestamp, and deploy generation in the database → returns the updated `HostedSite`, or `None` if the row no longer exists.

**Call relations**: Code that wants to refresh an already registered static site uses this instead of creating a new registration. It calls `_read` after the update so callers get the current row or learn that the site disappeared.

*Call graph*: calls 1 internal fn (_read); 3 external calls (case, update, time_ns).


##### `HostedSites.homepage`  (lines 337–349)

```
async def homepage(self, agent_id: UUID) -> HostedSite | None
```

**Purpose**: Finds the site currently bound as an agent’s homepage. If the agent has no homepage site in this workspace, it returns nothing.

**Data flow**: It receives an agent ID → queries this workspace for a hosted site whose homepage binding points to that agent → converts the row into a `HostedSite` if one is found.

**Call relations**: Homepage display or routing code calls this when it needs the page associated with an agent. It uses `_columns` to build the safe select list and `_site` to convert the database result.

*Call graph*: calls 2 internal fn (_columns, _site).


##### `HostedSites.set_preview`  (lines 351–371)

```
async def set_preview(self, conversation_id: UUID, name: str, preview: StoredPreview) -> None
```

**Purpose**: Stores the preview image information captured after a site deploy. This lets other parts of the product show a thumbnail of the hosted page.

**Data flow**: It receives the conversation, site name, and a stored preview record containing a blob key and size → updates those preview fields and the update timestamp on the matching row → returns nothing.

**Call relations**: This happens after registration because preview rendering can take time or fail. If the site was removed while the preview was being made, the update matches no row and safely leaves the registry unchanged.

*Call graph*: 1 external calls (update).


##### `HostedSites.set_share_card`  (lines 373–397)

```
async def set_share_card(self, conversation_id: UUID, name: str, blob_key: str, digest: str) -> None
```

**Purpose**: Stores the generated share-card image for a site, along with a digest that identifies that exact card. This supports public sharing without relying on cache invalidation.

**Data flow**: It receives the conversation, site name, blob key, and digest → writes the share-card fields and update timestamp to the matching hosted-site row → returns nothing.

**Call relations**: `extensions/sites/ufo_ext_sites/share_card._compose` calls this after composing a card from the page. Like previews, it is a follow-up write, so a site removed during card generation simply will not be updated.

*Call graph*: called by 1 (_compose); 1 external calls (update).


##### `HostedSites.read`  (lines 399–401)

```
async def read(self, conversation_id: UUID, name: str) -> HostedSite | None
```

**Purpose**: Looks up one hosted site by conversation and name inside this workspace. It is the simple public read method for the registry.

**Data flow**: It receives a conversation ID and site name → opens a transaction and asks `_read` for that exact row → returns a `HostedSite` or `None`.

**Call relations**: Other code calls this when it needs a single site record. It delegates the actual query and row conversion to `_read` so all single-row lookups behave the same way.

*Call graph*: calls 1 internal fn (_read).


##### `HostedSites.all`  (lines 403–413)

```
async def all(self) -> tuple[HostedSite, ...]
```

**Purpose**: Lists every hosted site in the workspace, ordered from oldest to newest. Filtering for who may see which object is expected to happen elsewhere.

**Data flow**: It receives no site-specific input beyond the `HostedSites` workspace → queries all rows for that workspace ordered by creation time and name → returns them as a tuple of `HostedSite` objects.

**Call relations**: Workspace-wide listing code calls this when it needs the full registry view. It uses `_columns` for the selected fields and `_site` to turn each row into the application’s data object.

*Call graph*: calls 2 internal fn (_columns, _site).


##### `HostedSites.conversation`  (lines 415–432)

```
async def conversation(self, conversation_id: UUID, names: tuple[str, ...], limit: int) -> tuple[HostedSite, ...]
```

**Purpose**: Lists selected hosted sites belonging to one conversation. It is useful when the caller already has a set of names and wants the matching registry rows.

**Data flow**: It receives a conversation ID, a tuple of names, and a limit → queries this workspace and conversation for names in that set, ordered by creation time and name → returns up to the requested number of `HostedSite` objects.

**Call relations**: Conversation-focused views call this to fetch known site names in bulk. It builds its query with `_columns` and converts rows through `_site`.

*Call graph*: calls 2 internal fn (_columns, _site).


##### `HostedSites.visible_conversation`  (lines 434–464)

```
async def visible_conversation(self, conversation_id: UUID, member_id: UUID, limit: int, *, admin: bool, homepage_agents: frozenset[UUID]) -> tuple[HostedSite, ...]
```

**Purpose**: Lists the sites in a conversation that a particular member is allowed to open. It includes a member’s own sites, non-private sites, sites exposed through visible homepage agents, and all sites for admins.

**Data flow**: It receives the conversation, member, result limit, admin flag, and visible homepage-agent IDs → builds a visibility gate unless the member is an admin → queries matching rows in order → returns the allowed `HostedSite` objects.

**Call relations**: User-facing conversation views use this when they must show only accessible sites. It combines the registry’s stored visibility data with caller-provided admin and homepage-agent context, then uses `_columns` and `_site` for the database read.

*Call graph*: calls 2 internal fn (_columns, _site); 1 external calls (or_).


##### `HostedSites.set_visibility`  (lines 466–481)

```
async def set_visibility(self, conversation_id: UUID, name: str, visibility: Visibility) -> HostedSite | None
```

**Purpose**: Changes the visibility level of one site and returns the updated row. It also changes the site generation so old permission checks tied to the previous setting no longer silently remain valid.

**Data flow**: It receives a conversation ID, site name, and new visibility → updates the visibility, generation ID, and timestamp for that row → reads the row back and returns it, or returns `None` if the site was gone.

**Call relations**: Sharing controls call this after they have decided the member is allowed to change the setting. It performs the write and then relies on `_read` to return the current registry record.

*Call graph*: calls 1 internal fn (_read); 2 external calls (update, uuid4).


##### `HostedSites.set_homepage`  (lines 483–509)

```
async def set_homepage(self, agent_id: UUID, conversation_id: UUID, name: str) -> HostedSite | None
```

**Purpose**: Binds a hosted site as an agent’s homepage. It first clears any previous homepage binding for that agent so there is only one homepage at a time.

**Data flow**: It receives an agent ID, conversation ID, and site name → clears existing rows in the workspace that point to that agent → sets the requested row’s homepage agent ID → reads and returns the bound site, or `None` if it was not found.

**Call relations**: Agent-homepage setup calls this when a site should become the page for an agent. It uses `_read` afterward so the caller can confirm which row now holds the binding.

*Call graph*: calls 1 internal fn (_read); 1 external calls (update).


##### `HostedSites.release_homepage`  (lines 511–535)

```
async def release_homepage(self, conversation_id: UUID, name: str, visibility: Visibility) -> None
```

**Purpose**: Removes a site’s homepage binding and restores the site’s own visibility level. This matters because a bound homepage may be shown according to the agent’s visibility instead of the site’s stored visibility.

**Data flow**: It receives the conversation, site name, and visibility to resume → clears `homepage_agent_id`, writes the supplied visibility, refreshes the generation ID, and updates the timestamp → returns nothing.

**Call relations**: Homepage-unbinding code calls this when a site stops serving as an agent homepage. It does not read the row back; it simply applies the release state in one database update.

*Call graph*: 2 external calls (update, uuid4).


##### `HostedSites.unregister`  (lines 537–548)

```
async def unregister(self, conversation_id: UUID, name: str) -> None
```

**Purpose**: Deletes a site’s registry row so its permanent link no longer resolves. The running sandbox may still exist, but it is no longer advertised as that hosted site.

**Data flow**: It receives the conversation ID and site name → deletes the matching row scoped to this workspace → returns nothing.

**Call relations**: Unhost or cleanup flows call this when a site should stop being reachable by its registered name. It uses a direct database delete because there is no row to return afterward.

*Call graph*: 1 external calls (delete).


##### `HostedSites.refuse_or_pass`  (lines 550–570)

```
async def refuse_or_pass(self, conversation_id: UUID, name: str, port: int, creator_member_id: UUID, visibility: Visibility | None, may_unhost: bool) -> HostedSite | None
```

**Purpose**: Runs the same safety checks that registration would run, but without writing anything. It tells the caller whether registration would displace an existing site on the same port.

**Data flow**: It receives the proposed registration details → opens a transaction and calls `_refuse` → either raises the same errors registration would raise or returns the site that would be displaced, if any.

**Call relations**: Deploy code can call this before serving on a port, while the old site is still alive. `register` calls `_refuse` again later inside the final write so the check is also enforced at the moment the registry changes.

*Call graph*: calls 1 internal fn (_refuse).


##### `HostedSites._refuse`  (lines 572–601)

```
async def _refuse(self, connection: AsyncConnection, conversation_id: UUID, name: str, port: int, creator_member_id: UUID, visibility: Visibility | None, may_unhost: bool) -> HostedSite | None
```

**Purpose**: Centralizes the rules that can block a site registration. It protects visibility changes and port takeovers from being done by the wrong member or by a turn that is not allowed to unhost.

**Data flow**: It receives an open database connection and proposed registration details → reads the same-name site, checks whether a visibility change is allowed, finds any different-name site already on the requested port, and checks unhost permission → returns the displaced site if allowed, or raises a clear error.

**Call relations**: Both `refuse_or_pass` and `register` call this so preflight checks and real registration use identical rules. It calls `_read` for the target name and `_on_port` for possible port conflicts.

*Call graph*: calls 2 internal fn (_on_port, _read); called by 2 (refuse_or_pass, register); 2 external calls (__init__, __init__).


##### `HostedSites._on_port`  (lines 603–618)

```
async def _on_port(self, connection: AsyncConnection, conversation_id: UUID, port: int, name: str) -> HostedSite | None
```

**Purpose**: Finds whether another site in the same conversation is already registered on a given port. This is how the registry detects that a deploy would take over an existing hosted origin.

**Data flow**: It receives an open connection, conversation ID, port, and the current site name to ignore → queries for a row in this workspace and conversation with the same port but a different name → returns that `HostedSite` or `None`.

**Call relations**: `_refuse` calls this while deciding whether a registration would displace another site. It uses `_columns` to shape the query and `_site` to convert the row if one exists.

*Call graph*: calls 2 internal fn (_columns, _site); called by 1 (_refuse); 1 external calls (execute).


##### `HostedSites._read`  (lines 620–632)

```
async def _read(self, connection: AsyncConnection, conversation_id: UUID, name: str) -> HostedSite | None
```

**Purpose**: Reads one exact hosted-site row using an already-open database connection. It is the shared helper for all methods that need a single site by conversation and name.

**Data flow**: It receives an open connection, conversation ID, and site name → queries the workspace-scoped table for that exact identity → returns a converted `HostedSite` or `None`.

**Call relations**: `register`, `redeploy`, `read`, `set_visibility`, `set_homepage`, and `_refuse` all use this helper. Keeping the lookup here ensures they select the same fields and apply the same row conversion.

*Call graph*: calls 2 internal fn (_columns, _site); called by 6 (_refuse, read, redeploy, register, set_homepage, set_visibility); 1 external calls (execute).


##### `HostedSites._columns`  (lines 634–651)

```
def _columns(self) -> sa.Select
```

**Purpose**: Builds the standard database select statement for hosted-site rows. It ensures every read asks for the same set of fields needed to build a `HostedSite`.

**Data flow**: It receives no extra input → creates a SQL select expression containing the hosted-site columns used by the application → returns that expression for callers to add filters and ordering.

**Call relations**: Read helpers and list methods call this before adding their own `where`, order, or limit clauses. The returned select is later executed and passed to `_site` for conversion.

*Call graph*: called by 6 (_on_port, _read, all, conversation, homepage, visible_conversation); 1 external calls (select).


##### `_aware`  (lines 654–657)

```
def _aware(moment: datetime) -> datetime
```

**Purpose**: Makes sure a stored timestamp has timezone information. This avoids confusing comparisons between timezone-aware and timezone-naive datetimes.

**Data flow**: It receives a datetime from the database → if it already has timezone information, returns it unchanged; otherwise marks it as UTC → returns the safe datetime.

**Call relations**: `_site` calls this for `created_at` and `updated_at` while converting database rows. It mainly protects against databases such as SQLite returning timestamps without timezone labels.

*Call graph*: called by 1 (_site); 1 external calls (replace).


##### `_site`  (lines 660–677)

```
def _site(row: sa.Row) -> HostedSite
```

**Purpose**: Turns a raw database row into a `HostedSite` data object the rest of the code can use. It also validates visibility and fixes timestamp timezone information during conversion.

**Data flow**: It receives a SQL row → copies each selected column into a `HostedSite`, converts the visibility through `visibility_level`, and normalizes timestamps through `_aware` → returns the finished `HostedSite`.

**Call relations**: All registry read paths use this after fetching rows, including `_read`, `_on_port`, `all`, `conversation`, `visible_conversation`, and `homepage`. It is the boundary between database-shaped data and application-shaped data.

*Call graph*: calls 2 internal fn (_aware, visibility_level); called by 6 (_on_port, _read, all, conversation, homepage, visible_conversation); 1 external calls (__init__).


### Publishing Tools
Agent-facing tools build, preview, deploy, publish, expose, and bind hosted websites while enforcing safety and readiness checks.

### `extensions/sites/ufo_ext_sites/tools.py`

`domain_logic` · `tool invocation during build, preview, deploy, publish, and homepage binding`

This file is the control panel for website delivery. Without it, an agent could still run shell commands, but it would not get the safer behavior people expect: paths kept inside the workspace, old servers cleaned off ports, logs written safely, ports checked before success is reported, and hosted links registered in the site store.

The file exposes several tools. `website` runs a build command and reports the resulting files. `start_server` starts a temporary server for testing. `deploy_website` serves a static site folder and gives it a permanent URL. `publish_website` does the same for a fuller app that may need install steps or a backend command. `set_homepage` points an agent’s homepage at one hosted site.

A lot of the file is guardrail work. Before starting a server it clears any old process on the chosen port, removes stale task records, creates logs in a way that avoids link tricks, and waits until the port really accepts connections. Before hosting, it checks whether the acting member is allowed to create or replace that site and whether a live speaker is needed to change who can see it.

For static deploys, it also records the served files in blob storage with a manifest, like putting the exact shipped box on a shelf for later edits. After hosting, it asks the preview service to take a screenshot and share-card image.

#### Function details

##### `StartServerInput.validate_port`  (lines 453–458)

```
def validate_port(self) -> 'StartServerInput'
```

**Purpose**: Checks that a requested server start makes sense before any command runs. It rejects an empty command and rejects ports outside the valid network port range.

**Data flow**: It reads the parsed input fields on `StartServerInput` → checks the optional command text and optional port number → returns the same input object if everything is valid, or raises a validation error before the tool can run.

**Call relations**: This runs automatically as part of input validation for the `start_server` tool. It protects `start_server` from being handed an unusable command or impossible port.


##### `_json_result`  (lines 498–499)

```
def _json_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: Wraps a Python dictionary as the standard tool response. It is the small adapter that turns internal results into JSON text the caller can read.

**Data flow**: It receives a dictionary → converts it to a JSON string → places that string inside a text content object and then inside a tool result.

**Call relations**: All user-facing tool handlers use this at the end of their work, including build, server start, deploy, publish, QA, homepage redeploy, and homepage binding. It is the shared final packaging step.

*Call graph*: called by 7 (_redeploy_homepage, deploy_website, publish_website, qa_ufo_application, set_homepage, start_server, website); 3 external calls (__init__, __init__, dumps).


##### `_free_log`  (lines 502–522)

```
async def _free_log(ctx: ToolContext, log_path: str) -> None
```

**Purpose**: Clears the chosen server log path safely before a new background server writes to it. This matters because a log filename can be predictable, and unsafe clearing could accidentally follow a planted link to another file.

**Data flow**: It receives the tool context and a log path → decides whether the path belongs under the runtime output area or the workspace → runs a guarded sandbox script that removes that file name only if it is contained safely → returns nothing, or raises an error if the path cannot be cleared.

**Call relations**: `_serve` calls this before launching any server. It prepares the log file name so the later shell redirect creates a fresh file instead of overwriting something unintended.

*Call graph*: called by 1 (_serve); 1 external calls (PurePosixPath).


##### `_stop_server`  (lines 525–530)

```
async def _stop_server(ctx: ToolContext, port: int) -> None
```

**Purpose**: Frees a port by stopping any process already listening on it inside the sandbox. This prevents a new server from accidentally sharing or losing to an old one.

**Data flow**: It receives a port number → runs a sandbox Python program that finds listeners on that port and sends them stop signals → returns nothing if the port is freed, or raises an error if cleanup fails.

**Call relations**: `_serve` calls this before every server launch. It is the port cleanup step in the larger start-and-wait sequence.

*Call graph*: called by 1 (_serve).


##### `_stop_server_task`  (lines 533–549)

```
async def _stop_server_task(ctx: ToolContext, command: str, base: str, pid: str) -> None
```

**Purpose**: Stops a background server task that was just started but failed to become usable. It is cleanup for the failure path, so failed starts do not leave stray processes behind.

**Data flow**: It receives the tool context, the server command, the task journal base path, and the task process id → asks the sandbox task system to stop that exact recorded task → waits for the task to finish → returns nothing or raises if the task will not stop.

**Call relations**: `_serve` calls this when readiness probing fails or when the probe is interrupted. It undoes a start that did not produce a working server.

*Call graph*: called by 1 (_serve).


##### `_reset_server_task`  (lines 552–570)

```
async def _reset_server_task(ctx: ToolContext, base: str) -> None
```

**Purpose**: Clears the saved task record for a server before starting a new one. This stops the task runner from reattaching to an old journal instead of launching a fresh server.

**Data flow**: It receives the tool context and the task journal base path → runs a sandbox shell script that stops any recorded task and removes its pid, log, exit, and lock files → returns nothing or raises if the journal cannot be cleared.

**Call relations**: `_serve` calls this after freeing the log and port. It makes the upcoming detached task start cleanly instead of inheriting stale state.

*Call graph*: called by 1 (_serve).


##### `_serve`  (lines 573–640)

```
async def _serve(ctx: ToolContext, command: str, project: str, port: int, log_path: str) -> dict[str, object]
```

**Purpose**: Starts a server in the sandbox and only reports success once it is actually reachable. It is the dependable replacement for “run a command in the background and hope it is ready.”

**Data flow**: It receives a command, project directory, port, and log path → clears the log, kills old listeners, resets the task record, starts the command as a detached sandbox task, and repeatedly tries to connect to the port → returns the local URL, port, and log path when ready. If startup fails, it stops the task and includes recent log output in the error when possible.

**Call relations**: `start_server`, `deploy_website`, `publish_website`, and `_redeploy_homepage` all rely on this to create the live server. It calls the log, port, task reset, and failed-task cleanup helpers as its internal safety sequence.

*Call graph*: calls 4 internal fn (_free_log, _reset_server_task, _stop_server, _stop_server_task); called by 4 (_redeploy_homepage, deploy_website, publish_website, start_server); 3 external calls (sha256, quote, shell_path).


##### `_site_media_type`  (lines 643–645)

```
def _site_media_type(path: str) -> str
```

**Purpose**: Chooses the web content type for a file based on its extension. This tells browsers whether a stored file is HTML, CSS, JavaScript, an image, a font, or plain bytes.

**Data flow**: It receives a file path → looks at the part after the last dot → returns a media type string, adding UTF-8 charset for text files and using a safe default for unknown extensions.

**Call relations**: `_promote_source` uses this while building the manifest for a deployed static site. The manifest then tells the hosted site service how to serve each file.

*Call graph*: called by 1 (_promote_source).


##### `_source_listing`  (lines 648–669)

```
async def _source_listing(ctx: ToolContext, project: str) -> dict[str, dict[str, object]]
```

**Purpose**: Builds a safe inventory of the files in a static site directory. It refuses oversized sites and skips things that should not be hosted, such as `.git` history, installed packages, and caches.

**Data flow**: It receives the tool context and a project path → runs a sandbox script that walks the directory, ignores skipped names and links, measures each regular file, and computes a SHA-256 fingerprint → returns a mapping of relative file paths to size and digest data, or raises if the directory is empty or too large.

**Call relations**: `_served_directory` calls this to understand what can be served and later stored. Its output feeds `_promote_source` during deploy flows.

*Call graph*: called by 1 (_served_directory); 1 external calls (loads).


##### `_promote_source`  (lines 672–713)

```
async def _promote_source(ctx: ToolContext, project: str, conversation_id: UUID, name: str, listing: dict[str, dict[str, object]]) -> str
```

**Purpose**: Copies the exact files of a static deployment into the project’s blob store and creates the manifest that records them. This makes the deployed site recoverable later without relying on the sandbox still having the files.

**Data flow**: It receives a project path, conversation id, site name, and file listing → creates a unique storage prefix, builds a manifest with each file’s size, media type, and digest → uploads the files either through pre-signed upload URLs or direct streams → returns the manifest as JSON.

**Call relations**: `deploy_website` and `_redeploy_homepage` call this before registering or updating a hosted static site. It uses `_site_media_type` for each manifest entry and hands the manifest to hosting code.

*Call graph*: calls 1 internal fn (_site_media_type); called by 2 (_redeploy_homepage, deploy_website); 4 external calls (__init__, __init__, transfer, uuid4).


##### `_illustrate`  (lines 716–738)

```
async def _illustrate(ctx: ToolContext, name: str, port: int, conversation_id: UUID) -> None
```

**Purpose**: Creates visual previews for a newly hosted or updated site. These images are used for the site’s card in the product and for link sharing.

**Data flow**: It receives a site name, serving port, and the conversation id that owns the site row → asks the preview service to render the hosted page → stores the preview if one was produced → asks the share-card helper to draw a separate card image → returns nothing.

**Call relations**: `deploy_website`, `publish_website`, and `_redeploy_homepage` call this after the site has already been registered or updated. It deliberately runs after hosting so the preview sees the current public route.

*Call graph*: calls 1 internal fn (render_site_preview); called by 3 (_redeploy_homepage, deploy_website, publish_website); 2 external calls (__init__, draw_from_page).


##### `_refuse_before_serving`  (lines 741–781)

```
async def _refuse_before_serving(ctx: ToolContext, raw_name: str, port: int, visibility: Visibility | None) -> tuple[str, HostedSite | None]
```

**Purpose**: Checks whether a deploy or publish would be allowed before it kills whatever is currently using the port. This avoids breaking an existing site only to discover afterward that the new one cannot be registered.

**Data flow**: It receives the requested site name, target port, and optional visibility → confirms there is an acting member, checks whether a live speaker is required for visibility changes, normalizes the site name, validates the future URL, and asks the site store whether the operation would be refused → returns the normalized name and any site that would be displaced.

**Call relations**: `deploy_website` and `publish_website` call this before serving. `_host` repeats the important checks when it writes, but this earlier pass protects existing live sites from avoidable disruption.

*Call graph*: called by 2 (deploy_website, publish_website); 4 external calls (__init__, __init__, site_name, site_url).


##### `_host`  (lines 784–828)

```
async def _host(ctx: ToolContext, raw_name: str, port: int, visibility: Visibility | None, manifest: str | None) -> dict[str, object]
```

**Purpose**: Registers a live sandbox port as a hosted site and returns the permanent link details. This is the moment a running server becomes a user-openable deliverable.

**Data flow**: It receives a raw site name, port, optional visibility, and optional static-source manifest → checks ownership and speaker rules, normalizes the name, builds the public URL, writes or updates the hosted-site row, computes the effective visibility, and returns site name, object name, visibility, and URL.

**Call relations**: `deploy_website` and `publish_website` call this after `_serve` has proven the port is live. `_illustrate` usually follows it to update the preview images.

*Call graph*: called by 2 (deploy_website, publish_website); 6 external calls (__init__, __init__, effective_visibility, site_object_name, site_name, site_url).


##### `website`  (lines 831–840)

```
async def website(ctx: ToolContext, args: WebsiteInput) -> ToolResult
```

**Purpose**: Runs a website build command inside the sandbox and reports what files are in the project afterward. It is a simple build tool, not a hosting tool.

**Data flow**: It receives a build command and optional project path → scopes the path to the workspace, runs the command in that directory with a build timeout, then lists the directory contents → returns JSON containing the project path and file names. If the build command fails, it raises the command output as an error.

**Call relations**: This is a direct tool handler registered as `website`. It uses `_json_result` for its response but does not start a server or register a hosted site.

*Call graph*: calls 1 internal fn (_json_result); 2 external calls (quote, workspace_path).


##### `start_server`  (lines 843–863)

```
async def start_server(ctx: ToolContext, args: StartServerInput) -> ToolResult
```

**Purpose**: Starts a temporary server for previewing work inside the sandbox. It returns only after the server is actually listening, so browser tools can use the URL immediately.

**Data flow**: It receives a project path, optional command, optional port, and optional log path → chooses defaults, applies special restrictions for the application-builder profile, scopes paths to the workspace or runtime area, chooses a static file server when no command is given, then calls `_serve` → returns JSON with URL, port, log, and project path.

**Call relations**: This is the tool handler for `start_server`. It delegates the hard startup work to `_serve` and packages the final answer with `_json_result`.

*Call graph*: calls 2 internal fn (_json_result, _serve); 1 external calls (workspace_path).


##### `_application_audit_attempts`  (lines 866–874)

```
async def _application_audit_attempts(ctx: ToolContext) -> int
```

**Purpose**: Reads how many product-audit repair attempts have already been used in the current turn. This enforces the small fixed number of audit retries.

**Data flow**: It receives the tool context → reads a stored value keyed by the turn id → returns zero if absent, returns the integer if present, or raises if the stored value is not an integer.

**Call relations**: `_audit_builder_application` calls this before running the browser audit. It decides whether another audit attempt is still allowed.

*Call graph*: called by 1 (_audit_builder_application); 1 external calls (format).


##### `_application_audit_feedback`  (lines 877–889)

```
async def _application_audit_feedback(ctx: ToolContext, issues: tuple[ApplicationAuditIssue, ...], attempts: int) -> ApplicationAuditFeedback
```

**Purpose**: Records a failed product-audit attempt and packages the issues as repair feedback. It tells the application builder what to fix and how many attempts remain.

**Data flow**: It receives audit issues and the previous attempt count → increments and stores the count → creates an `ApplicationAuditFeedback` object with the new attempt number, remaining attempts, and issues → returns that feedback.

**Call relations**: `_audit_builder_application` calls this whenever the audit cannot run, cannot be read, is invalid, or finds product problems. The returned feedback is passed back by `qa_ufo_application`.

*Call graph*: called by 1 (_audit_builder_application); 2 external calls (__init__, format).


##### `_accepted_application_design`  (lines 892–927)

```
async def _accepted_application_design(ctx: ToolContext) -> tuple[AcceptedApplicationDesignEvidence, str, str]
```

**Purpose**: Loads the design that was previously accepted for a UFO application and verifies that its evidence matches the design text. This prevents auditing one design while deploying another.

**Data flow**: It receives the tool context → computes runtime paths for the accepted design and evidence files → reads both with size limits → parses the evidence JSON → checks that the design file’s SHA-256 digest matches the digest recorded in the evidence → returns the evidence object plus both file paths.

**Call relations**: `_audit_builder_application` calls this before running the browser audit. The audit script receives these files so it can compare the built app against the accepted design.

*Call graph*: called by 1 (_audit_builder_application); 4 external calls (model_validate_json, sha256, application_design_acceptance_relative, application_design_evidence_relative).


##### `_audit_builder_application`  (lines 930–1043)

```
async def _audit_builder_application(ctx: ToolContext, project: str) -> ApplicationAuditReport | ApplicationAuditFeedback
```

**Purpose**: Runs the deterministic browser-based product audit for the special UFO application builder. It either returns a passed audit report or bounded feedback telling the builder what to repair.

**Data flow**: It receives the tool context and project path → checks attempt limits, loads accepted design evidence, writes the audit script into the sandbox, runs it with output paths for reports and screenshots, reads and validates the report, loads the parent-turn audit contract, and applies the audit rules → returns a full audit report if passed, or feedback if something failed.

**Call relations**: `qa_ufo_application` calls this as the core QA step. Internally it uses the attempt counter, design loader, feedback builder, report models, and `audit_application` rule checker.

*Call graph*: calls 3 internal fn (_accepted_application_design, _application_audit_attempts, _application_audit_feedback); called by 1 (qa_ufo_application); 5 external calls (__init__, model_validate, model_validate_json, format, audit_application).


##### `_application_source_sha256`  (lines 1046–1052)

```
async def _application_source_sha256(ctx: ToolContext) -> str
```

**Purpose**: Computes the fingerprint of the UFO application source file. This proves whether the app changed after QA passed.

**Data flow**: It receives the tool context → reads the fixed `app.tsx` source from the sandbox with a guarded script → hashes the text with SHA-256 → returns the hex digest, or raises if the file cannot be read.

**Call relations**: `qa_ufo_application` stores this digest after a successful audit. `_require_current_application_qa` compares the current digest against the stored one before deployment.

*Call graph*: called by 2 (_require_current_application_qa, qa_ufo_application); 1 external calls (sha256).


##### `_require_current_application_qa`  (lines 1055–1067)

```
async def _require_current_application_qa(ctx: ToolContext) -> ApplicationQaProof
```

**Purpose**: Blocks a UFO application deploy unless product QA passed for the exact current source. This prevents a builder from passing QA, changing the code, and deploying untested work.

**Data flow**: It receives the tool context → reads the stored QA proof for the turn → validates its shape → recomputes the current source digest → returns the proof if the digests match, or raises if QA is missing, invalid, or stale.

**Call relations**: `deploy_website` calls this when the current subagent profile is the UFO application builder. It is the deployment gate for that profile.

*Call graph*: calls 1 internal fn (_application_source_sha256); called by 1 (deploy_website); 2 external calls (model_validate, format).


##### `qa_ufo_application`  (lines 1070–1112)

```
async def qa_ufo_application(ctx: ToolContext, args: QaUfoApplicationInput) -> ToolResult
```

**Purpose**: Runs product QA for the special UFO application builder profile. It returns either repair feedback or a concise record of what the audit checked and verified.

**Data flow**: It receives an empty QA input → confirms the caller is the application builder, checks and increments the allowed QA call count, runs `_audit_builder_application`, and then either returns feedback or records a QA proof containing the current source digest and audit batch count → returns JSON with audit feedback or passed QA results.

**Call relations**: This is registered as a profile-only tool. It calls `_audit_builder_application` for the real audit work, `_application_source_sha256` to freeze the approved source version, and `_json_result` to return the result.

*Call graph*: calls 3 internal fn (_application_source_sha256, _audit_builder_application, _json_result); 4 external calls (__init__, __init__, format, format).


##### `deploy_ufo_application`  (lines 1115–1125)

```
async def deploy_ufo_application(ctx: ToolContext, args: DeployUfoApplicationInput) -> ToolResult
```

**Purpose**: Deploys the special UFO application builder output through the normal static website deploy path. It gives that profile a narrow deploy tool with fixed project and entry settings.

**Data flow**: It receives an application site name → confirms the caller is the application builder profile → creates a `DeployWebsiteInput` for the fixed scaffold path and `index.html` → returns whatever `deploy_website` returns.

**Call relations**: This profile-only deploy handler is a thin wrapper around `deploy_website`. The normal deploy path then applies QA checks, source promotion, serving, hosting, and previewing.

*Call graph*: calls 1 internal fn (deploy_website); 1 external calls (__init__).


##### `deploy_website`  (lines 1128–1159)

```
async def deploy_website(ctx: ToolContext, args: DeployWebsiteInput) -> ToolResult
```

**Purpose**: Turns a static website directory into a permanent hosted site link. For the application builder, it also enforces the fixed scaffold location and required QA proof.

**Data flow**: It receives a project path, site name, entry file, and optional visibility → scopes the project path, checks application-builder restrictions, chooses a serving port, detects whether this should update a bound homepage, checks permissions before serving, prepares the served directory, promotes files to blob storage, starts a static server, registers the hosted site, creates previews, and returns JSON with server and hosted-link details.

**Call relations**: This is the main static deploy tool and is also called by `deploy_ufo_application`. It coordinates `_agent_homepage`, `_redeploy_homepage`, `_refuse_before_serving`, `_served_directory`, `_promote_source`, `_serve`, `_host`, `_illustrate`, and `_json_result`.

*Call graph*: calls 11 internal fn (_agent_homepage, _host, _illustrate, _json_result, _promote_source, _redeploy_homepage, _refuse_before_serving, _require_current_application_qa, _serve, _served_directory (+1 more)); called by 1 (deploy_ufo_application); 4 external calls (serve_port, workspace_path, site_object_name, site_name).


##### `_served_directory`  (lines 1162–1188)

```
async def _served_directory(ctx: ToolContext, project: str) -> tuple[str, dict[str, dict[str, object]]]
```

**Purpose**: Decides what directory should actually be served for a static deploy. If the input is already built output, it uses it; if it is an editable app source directory, it builds the app and serves the generated `dist` folder.

**Data flow**: It receives a project path → lists its files → if the special source file is absent, returns that project and listing → otherwise writes the project config, unpacks the page kit, runs a Vite build, then lists and returns the generated dist directory.

**Call relations**: `deploy_website` and `_redeploy_homepage` call this before source promotion. It calls `_source_listing` both before and after a build and uses the page-kit unpacker for source-style app projects.

*Call graph*: calls 1 internal fn (_source_listing); called by 2 (_redeploy_homepage, deploy_website); 2 external calls (quote, unpack_page_kit).


##### `_agent_homepage`  (lines 1191–1194)

```
async def _agent_homepage(ctx: ToolContext) -> HostedSite | None
```

**Purpose**: Finds the hosted site currently bound as the acting agent’s homepage, if any. This lets deploy logic decide whether a request should update that homepage in place.

**Data flow**: It receives the tool context → obtains the hosted-sites registry → asks for the homepage bound to the current turn’s agent id → returns the hosted site or `None`.

**Call relations**: `deploy_website` calls this early. It uses `_sites_registry` to access the site store.

*Call graph*: calls 1 internal fn (_sites_registry); called by 1 (deploy_website).


##### `_sites_registry`  (lines 1197–1200)

```
def _sites_registry(ctx: ToolContext) -> HostedSites
```

**Purpose**: Creates the store helper used to read and write hosted-site records for the current workspace transaction. It is the shared doorway to the hosted-site database logic.

**Data flow**: It receives the tool context → checks that extension context is present → builds a `HostedSites` registry using the workspace id and active transaction → returns that registry.

**Call relations**: `_agent_homepage`, `_redeploy_homepage`, and `deploy_website` use this when they need hosted-site records. It keeps registry construction consistent.

*Call graph*: called by 3 (_agent_homepage, _redeploy_homepage, deploy_website); 1 external calls (__init__).


##### `_redeploy_homepage`  (lines 1203–1272)

```
async def _redeploy_homepage(ctx: ToolContext, args: DeployWebsiteInput, bound: HostedSite, scratch_port: int) -> ToolResult
```

**Purpose**: Updates an agent’s already-bound homepage without changing its public link. This is used when a build from another conversation should replace the page behind the same homepage URL.

**Data flow**: It receives deploy input, the currently bound site, and a scratch port → verifies that a live speaker or approved application-builder request authorized the replacement, rejects visibility changes, checks possible displacement, prepares and stores the new source, starts a scratch server, updates the existing homepage site row, unregisters any site displaced from the scratch port, refreshes preview images, and returns JSON with the unchanged hosted link details.

**Call relations**: `deploy_website` calls this instead of the normal deploy path when the requested name matches a homepage bound in another conversation. It reuses `_served_directory`, `_promote_source`, `_serve`, `_sites_registry`, `_illustrate`, and `_json_result`.

*Call graph*: calls 7 internal fn (agent_visibility, _illustrate, _json_result, _promote_source, _serve, _served_directory, _sites_registry); called by 1 (deploy_website); 4 external calls (workspace_path, format, site_object_name, site_url).


##### `publish_website`  (lines 1275–1292)

```
async def publish_website(ctx: ToolContext, args: PublishWebsiteInput) -> ToolResult
```

**Purpose**: Publishes a web app at a permanent hosted link, optionally installing dependencies and running an app server. Unlike static deploy source promotion, this hosts a live sandbox server and does not store the app source as the site’s source of record.

**Data flow**: It receives project, dist, app name, optional visibility, optional run command, and optional install command → chooses the conversation serving port, checks hosting permissions before touching the port, optionally runs the install command, chooses either the backend command or a static file server, starts the server with `_serve`, registers the hosted site without a manifest, creates previews, and returns JSON with server and hosted-link details.

**Call relations**: This is the tool handler for `publish_website`. Its flow mirrors `deploy_website` for serving, hosting, illustrating, and JSON output, but it skips `_promote_source` because the running app remains the source of responses.

*Call graph*: calls 5 internal fn (_host, _illustrate, _json_result, _refuse_before_serving, _serve); 3 external calls (quote, serve_port, workspace_path).


##### `set_homepage`  (lines 1295–1352)

```
async def set_homepage(ctx: ToolContext, args: SetHomepageInput) -> ToolResult
```

**Purpose**: Binds an existing hosted site as the homepage for the target agent. This changes which page the portal shows for that agent and makes the homepage follow the agent’s visibility rules.

**Data flow**: It receives a site object name → checks there is an extension context and target agent, loads the target agent, verifies the acting member is allowed to alter that agent, looks up the hosted site, verifies the acting member created it, requires a live speaker unless the site was deployed in the same turn for the same agent, writes the homepage binding, and returns JSON with the site URL, effective visibility, and agent id.

**Call relations**: This is the handler for the `set_homepage` tool bound to an agent object. It reads hosted sites directly through `HostedSites`, checks admin status through the tool context, and uses `_json_result` for the final response.

*Call graph*: calls 2 internal fn (speaker_is_admin, _json_result); 4 external calls (__init__, __init__, site_object_name, site_url).
