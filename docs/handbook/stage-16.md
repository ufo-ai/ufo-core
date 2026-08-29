# Output publication, artifacts, sites, panels, and user delivery  `stage-16`

This stage is where finished work becomes visible and usable. It is part of the delivery end of the system’s story: after something has been built, answered, scheduled, or hosted, these pieces help publish it safely to the places users can see.

The hosted-site registry keeps track of each public site: who owns it, which conversation it belongs to, where it is running, and whether it can be previewed or shared. The site source mover copies a site’s files between durable storage and a temporary work area, so the system can edit or rebuild it without losing the original. The media previewer visits a running sandbox site and captures a PNG screenshot, like taking a quick photo of the page. The share-card builder combines that screenshot with UFO branding so links look informative when pasted into chat or social apps. Finally, the conversation slot for scheduled tasks turns saved automation records into a short, safe “Automations” panel. Together, these parts turn internal results into user-facing artifacts, previews, sites, and panels.

## Files in this stage

### Site previews and share cards
Builds visual previews for hosted sites, including screenshot-backed social share cards.

### `extensions/sites/ufo_ext_sites/share_card.py`

`domain_logic` · `site deploy or share-card backfill`

A public hosted site needs a good “share card”: the image shown by apps like Slack when they unfurl a link. This file creates that image. The card is always 1200 by 630 pixels. The left side is a fixed UFO-branded panel with the site name. The right side is a screenshot of the site itself.

The important idea is that the file does not paste pixels together with a custom image editor. Instead, it builds a small HTML page that already contains the branded panel and the screenshot. Then it asks a headless browser, meaning Chrome or Chromium running without a visible window, to render that page and capture it as an image. This is like laying out a flyer in a browser, then taking a clean photograph of the finished flyer.

For a newly deployed site, it first visits the site on the sandbox’s local address and takes a screenshot sized exactly for the card. For older sites that only have an existing preview image, it reuses that stored image and crops it into the card area. Finally, it converts the browser’s PNG output into a progressive JPEG, stores it, and records the stored image on the site’s database row.

Share cards are treated as decoration. If any step fails, the failure is logged and the site keeps whatever card it already had.

#### Function details

##### `card_page`  (lines 486–511)

```
def card_page(name: str, drawn: str) -> str
```

**Purpose**: Builds the HTML page that the browser will render into the final share card. It puts together the left brand panel, the site name, and a placeholder where the site screenshot will later be inserted.

**Data flow**: It receives the site name and a small CSS snippet saying how the screenshot should be sized. It reads the bundled font and logo files, safely escapes the site name so member-provided text cannot become HTML code, fills all of that into the card template, and returns a complete HTML document as text.

**Call relations**: When `_compose` is ready to create a card, it asks `card_page` for the page markup. `card_page` relies on `_lockup` to provide the SVG logo, and uses standard encoding and escaping helpers so the browser can draw the font and name safely.

*Call graph*: calls 1 internal fn (_lockup); called by 1 (_compose); 2 external calls (b64encode, escape).


##### `_lockup`  (lines 514–518)

```
def _lockup() -> str
```

**Purpose**: Returns the UFO logo markup that belongs in the left panel of the card. It trims the SVG file down to the part that can be embedded inside another HTML page.

**Data flow**: It reads the bundled SVG logo file from disk, finds the beginning of the `<svg` element, and returns the SVG markup from that point onward. Nothing outside the returned string is changed.

**Call relations**: `card_page` calls `_lockup` while assembling the HTML page for the browser. This keeps logo loading in one small helper instead of mixing file-reading details into the page-building code.

*Call graph*: called by 1 (card_page).


##### `shot_command`  (lines 521–541)

```
def shot_command(*, url: str, width: int, height: int, scale: int, shot: str, root: str) -> str
```

**Purpose**: Creates the shell command that will take a browser screenshot inside the sandbox. It packages the browser driver script, dimensions, URL, output path, and safety options into one command string.

**Data flow**: It receives the URL to photograph, the desired image size and scale, the output screenshot path, and the sandbox root directory. It quotes paths and URLs so they are safe for the shell, inserts the embedded Python browser-driver program, and returns a command ready to run in the sandbox.

**Call relations**: `_shoot` calls `shot_command` whenever it needs Chromium to capture either a site page or the composed card page. `shot_command` hands `_shoot` the exact command text that the sandbox should execute.

*Call graph*: called by 1 (_shoot); 2 external calls (quote, shell_path).


##### `draw_from_page`  (lines 544–562)

```
async def draw_from_page(ctx: ToolContext, sites: HostedSites, conversation_id: UUID, name: str, port: int) -> None
```

**Purpose**: Creates a share card for a site that has just been deployed and can be reached locally in the sandbox. It uses a fresh screenshot of the actual front page at the size needed for the card.

**Data flow**: It receives the tool context, site store, conversation identifier, site name, and local port where the site is running. It chooses a runtime path for the temporary screenshot, asks `_shoot` to capture the page from `127.0.0.1`, and if that succeeds, passes the screenshot to `_compose` to build, store, and record the finished card.

**Call relations**: This is one of the main entry functions other deployment code would call when a newly published site needs a share image. It first delegates the browser screenshot work to `_shoot`, then delegates final card creation and database update to `_compose`.

*Call graph*: calls 2 internal fn (_compose, _shoot).


##### `draw_from_stored_shot`  (lines 565–580)

```
async def draw_from_stored_shot(ctx: ToolContext, sites: HostedSites, conversation_id: UUID, name: str, blob_key: str) -> None
```

**Purpose**: Creates a share card for an older site that already has a stored preview image but does not yet have a share card. It is a backfill path for sites deployed before this card feature existed.

**Data flow**: It receives the tool context, site store, conversation identifier, site name, and blob key for the old preview image. It downloads that image from blob storage, writes it into the sandbox as a temporary file, and then asks `_compose` to build the card from that file. If the image cannot be written into the sandbox, it logs the failure and stops.

**Call relations**: This is the second main entry function for share-card creation. Instead of calling `_shoot` to make a fresh page screenshot, it starts from an existing stored image, then hands off to `_compose` for the common card-building path. If the copy-in fails, it reports through `_undrawn`.

*Call graph*: calls 2 internal fn (_compose, _undrawn).


##### `_shoot`  (lines 583–612)

```
async def _shoot(ctx: ToolContext, name: str, shot: str, *, url: str, width: int, height: int, scale: int) -> bool
```

**Purpose**: Takes one screenshot with a headless browser and reports whether it succeeded. It is used both for photographing the hosted site and for photographing the finished HTML card page.

**Data flow**: It receives the sandbox context, site name, output path, URL, image size, and scale factor. It first empties the output file so a later non-empty file proves the command really produced something. Then it builds a browser command with `shot_command`, runs that command in the sandbox with a timeout, and returns `true` if the command succeeds. On write or browser failure, it logs the problem and returns `false`.

**Call relations**: `draw_from_page` calls `_shoot` to capture the site front page. `_compose` calls `_shoot` again to capture the assembled share-card HTML page. `_shoot` depends on `shot_command` for the command it runs, and uses `_undrawn` whenever screenshot creation fails.

*Call graph*: calls 2 internal fn (_undrawn, shot_command); called by 2 (_compose, draw_from_page).


##### `_compose`  (lines 615–665)

```
async def _compose(ctx: ToolContext, sites: HostedSites, conversation_id: UUID, name: str, shot: str, drawn: str) -> None
```

**Purpose**: Turns an existing screenshot into a complete share card, stores the JPEG, and records it on the site. This is the central assembly line for the final card.

**Data flow**: It receives the sandbox context, site store, conversation identifier, site name, screenshot path, and sizing rule for that screenshot. It writes the card HTML page into the sandbox, runs a small Python script to replace the screenshot placeholder with a data URI, asks `_shoot` to render that page into a PNG, converts the PNG into a progressive JPEG, stores the JPEG as a preview artifact, and finally writes the blob key and digest onto the hosted site record. If any step fails, it logs the issue and leaves the existing card unchanged.

**Call relations**: Both `draw_from_page` and `draw_from_stored_shot` feed screenshots into `_compose`. `_compose` asks `card_page` for the card markup, reuses `_shoot` to render the composed page, stores the result through the tool context, and finishes by telling `HostedSites.set_share_card` where the new card lives.

*Call graph*: calls 5 internal fn (store_preview, _shoot, _undrawn, card_page, set_share_card); called by 2 (draw_from_page, draw_from_stored_shot).


##### `_undrawn`  (lines 668–669)

```
def _undrawn(name: str, detail: object) -> None
```

**Purpose**: Records that a share card could not be drawn for a site. It keeps failures visible without stopping the site from being hosted.

**Data flow**: It receives the site name and an error detail object. It turns the detail into text, trims it to a safe length, and writes a structured log entry. It does not return a useful value and does not change the site record.

**Call relations**: `draw_from_stored_shot`, `_shoot`, and `_compose` call `_undrawn` when a non-essential card step fails. This gives the rest of the flow a consistent way to report problems while preserving the rule that a failed card should not break the site.

*Call graph*: called by 3 (_compose, _shoot, draw_from_stored_shot); 1 external calls (log).


### `core/src/ufo/media/site_previewer.py`

`domain_logic` · `request handling`

This file exists so the system can show a visual preview of a website or app running inside a sandbox. Without it, a user might have a live web server on a sandbox port, but the system would have no safe, standard way to capture that page as an image artifact.

The main piece is `SitePreviewer`. It is given access to blob storage, the URL and token for a preview-rendering service, and the public ingress URL used to reach sandbox ports. When asked to render a preview, it first checks that the requested filename and image size are safe and reasonable. It then creates a temporary public viewing URL for the sandbox page, like giving the camera operator a valid doorway into the site.

Next it chooses how the screenshot should be delivered. If the blob store is backed by S3, it gives the preview service a short-lived upload link so the service can write the PNG directly to storage. Otherwise, it asks the service to send the PNG bytes back inline, and this file uploads them itself.

The code is careful about trust. It limits response sizes, checks HTTP status codes, verifies PNG content and dimensions, and logs failures instead of crashing the caller. On success it returns a `StoredPreview`, which is a small record pointing to the saved image.

#### Function details

##### `SitePreviewer.render`  (lines 47–124)

```
async def render(self, conversation_id: UUID, port: int, name: str, width: int, height: int) -> StoredPreview | None
```

**Purpose**: Captures one screenshot of a sandboxed website port and stores it as a PNG preview. It returns a reference to the stored image when everything checks out, or `None` if the preview cannot be made safely.

**Data flow**: It starts with a conversation id, sandbox port, desired filename, and target width and height. It validates the filename and dimensions, builds a public sandbox page URL, chooses a storage path, and sends a JSON request to the preview service. If the storage backend can accept a direct upload, the preview service uploads the image and sends back metadata; otherwise, the service returns the PNG bytes and this function writes them to blob storage. Before returning, it checks size limits, content type, PNG signature, and image dimensions. The result is either a `StoredPreview` containing the blob key and byte size, or `None` after logging what went wrong.

**Call relations**: When some higher-level part of the media system needs a site preview, it calls this method. The method asks `ws_current` for the current workspace, uses `mint_ingress_view_url` to create a reachable URL for the sandbox page, uses `json.dumps` to package the render request, and sends it through `httpx.AsyncClient` with an `httpx.Timeout`. On success it creates a `StoredPreview`; on network or validation failure it reports the problem through `ufo.o11y.log` and hands back `None` so the rest of the system can continue without a preview.

*Call graph*: 9 external calls (__init__, AsyncClient, Timeout, dumps, PurePosixPath, log, mint_ingress_view_url, ws_current, uuid4).


### Conversation automations panel
Publishes scheduled task records as a readable Automations panel within a conversation.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/conversation_slot.py`

`orchestration` · `request handling`

This file is the bridge between the scheduled-tasks extension and the conversation view. A conversation may have visible automation items attached to it, but the app still needs to look up the real schedule records, check that those records are still the same authorized ones, and return only the information the viewer is allowed to see.

The main provider is `AUTOMATIONS_SLOT`. Think of it like a small display window in the conversation: when the app asks for a quick badge count, it uses `_summarize`; when it asks for full contents, it uses `_read`.

The file first creates a `ScheduleStore`, which is the storage doorway for scheduled tasks. It then lists tasks for the current conversation and only keeps tasks that match the visible items supplied by the conversation context. That match includes a “generation” check, meaning the task ID must match the authorization record. This prevents showing stale or no-longer-authorized automations.

When building the output, the file also protects the user interface and privacy. Long descriptions, schedules, statuses, and responses are cut to fixed limits. If the conversation item says its content is not visible, the automation description and latest response are hidden. A `truncated` flag tells the caller when anything was omitted, cut short, or could not be inspected.

#### Function details

##### `_scheduler`  (lines 16–19)

```
def _scheduler(ctx: ConversationSlotContext) -> ScheduleStore
```

**Purpose**: This function opens the scheduled-task storage for the current conversation-slot request. It also checks that the request has the extension context needed to access that storage.

**Data flow**: It receives a `ConversationSlotContext`. If the context has no extension data, it stops with an error because there is nowhere safe to read schedules from. Otherwise, it uses that extension context to create and return a `ScheduleStore`.

**Call relations**: _conversation` and `_read` call this when they need access to saved scheduled tasks. It hands them a `ScheduleStore`, which is the object they use to list tasks or inspect their run status.

*Call graph*: called by 2 (_conversation, _read); 1 external calls (__init__).


##### `_conversation`  (lines 22–28)

```
async def _conversation(ctx: ConversationSlotContext) -> tuple[ScheduledTask, ...]
```

**Purpose**: This function fetches the scheduled tasks that belong to the current conversation and are named among the items the conversation says are visible. It asks for one extra item beyond the display limit so the caller can tell whether the results were cut off.

**Data flow**: It receives a `ConversationSlotContext`, pulls the visible item names from it, opens the schedule store through `_scheduler`, and asks the store for tasks in the current conversation with those names. It returns the matching scheduled task records as a tuple.

**Call relations**: _read` calls this as the first step in building the full Automations slot. `_conversation` relies on `_scheduler` to get the storage object before asking that object for the conversation’s scheduled tasks.

*Call graph*: calls 1 internal fn (_scheduler); called by 1 (_read).


##### `_read`  (lines 31–90)

```
async def _read(ctx: ConversationSlotContext) -> AutomationsSlotPayload
```

**Purpose**: This function builds the full data shown in the conversation’s Automations slot. It filters out unauthorized or stale tasks, checks each remaining task’s run information, hides private content when required, trims long text, and returns a compact payload for display.

**Data flow**: It receives a `ConversationSlotContext`. It opens the schedule store, reads candidate tasks for the conversation, and compares each task against the visible items in the context. Only tasks whose name and authorization generation match are kept. It inspects those tasks for run details such as next run, last run, latest status, and latest response. It then creates `ConversationAutomation` entries with limited-length fields and privacy rules applied. Finally, it returns an `AutomationsSlotPayload` containing those entries plus a `truncated` flag that says whether anything was omitted, hidden because inspection failed, or shortened.

**Call relations**: This is the main read callback used by `AUTOMATIONS_SLOT` when the app wants the slot contents. It calls `_conversation` to find relevant tasks and `_scheduler` to inspect them. It then hands the finished `AutomationsSlotPayload` back to the slot system for presentation.

*Call graph*: calls 2 internal fn (_conversation, _scheduler); 2 external calls (__init__, __init__).


##### `_summarize`  (lines 93–95)

```
async def _summarize(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: This function gives a quick count for the Automations slot without loading full task details. It is used when the app only needs a small summary, such as whether to show a badge or section count.

**Data flow**: It receives a `ConversationSlotContext`, counts the visible automation items, caps that count at the maximum number the slot can show, and returns the count. If there are no visible items, it returns `None` instead of zero, which lets the slot appear empty or absent.

**Call relations**: This is the summary callback registered in `AUTOMATIONS_SLOT`. Unlike `_read`, it does not open the schedule store or inspect tasks; it only uses the visible items already present in the conversation context.


### Hosted site source and registry
Moves hosted site source files through sandbox storage and records the ownership, visibility, preview, and binding metadata needed to serve them.

### `extensions/sites/ufo_ext_sites/source.py`

`io_transport` · `site source materialization and deploy-time build preparation`

A hosted site has source files stored in a blob store, which is a place for keeping file-like data outside the running program. When someone opens or edits a site, those files must be copied into a sandbox, meaning an isolated working folder where tools can safely run. This file does that copying carefully.

It supports two storage shapes. In production-style storage, it asks for short-lived signed web links and has the sandbox download or upload files with curl. In a local development store, it streams the bytes through this Python process instead. Either way, the goal is the same: recreate the exact source tree named by the site's manifest, which is the record of which files exist and how large they are.

The file is careful about safety. Before writing files, it clears old contents and creates fresh empty files through a containment guard, which checks that paths stay inside the workspace. This matters because a sandbox can contain user-written files, including symbolic links, and blindly writing through them could overwrite something outside the intended folder.

For app pages, it also packages and unpacks a small SDK kit. That kit is not stored with every site. Instead, the current extension supplies it during materialization, so an old fork can build using today’s page components rather than being frozen to the components from its first deploy.

#### Function details

##### `_page_kit_archive`  (lines 101–113)

```
def _page_kit_archive() -> bytes
```

**Purpose**: Builds one compressed archive containing the page SDK kit files bundled with this extension. This lets the system send many small kit files into the sandbox as one package instead of making a separate sandbox write for every file.

**Data flow**: It starts with the kit directory on disk. It walks through that directory, adds each regular file into a gzip-compressed tar archive in memory, and returns the finished archive as raw bytes. It does not write the archive to disk itself.

**Call relations**: This runs when the module is imported to create PAGE_KIT_ARCHIVE. Later, unpack_page_kit uses those prepared bytes when it needs to place the SDK kit into a sandbox project.

*Call graph*: 2 external calls (BytesIO, open).


##### `transfer`  (lines 120–130)

```
async def transfer(ctx: ToolContext, script: str, pairs: list[tuple[str, str]], total_bytes: int) -> None
```

**Purpose**: Copies many files between the sandbox and blob storage using a shell script, in small batches. It exists so large transfers do not need one command per file, while still avoiding one enormous command that could be too big or too slow.

**Data flow**: It receives a sandbox tool context, a shell script, pairs of file paths and URLs, and the total number of bytes expected. It calculates a timeout based on size, splits the work into batches, asks the sandbox to run the script for each batch, and raises an error if any batch fails. It returns nothing when the transfer succeeds.

**Call relations**: materialize_source calls this when the blob store can provide presigned download links. transfer then hands the actual movement to the sandbox shell, which runs curl against those links.

*Call graph*: called by 1 (materialize_source).


##### `materialize_source`  (lines 133–199)

```
async def materialize_source(ctx: ToolContext, site: HostedSite, object_name: str) -> tuple[str, list[str]]
```

**Purpose**: Recreates a hosted site's stored source tree inside the current sandbox and tells the caller where it was placed. This is what makes edits start from the latest deployed source instead of from an old or incomplete working copy.

**Data flow**: It receives a tool context, a hosted site record, and an object name used for the sandbox folder. It reads the site's source manifest, decides the destination path, and checks a small generation stamp to see whether the sandbox already has the right version. If not, it safely clears and claims the destination files, downloads or streams each manifest file into place, writes the new generation stamp, and returns the destination directory plus the relative file paths that were materialized.

**Call relations**: This is the main entry point in this file for reading site source into a sandbox. It uses SourceManifest to understand what files should exist, workspace_path to choose safe workspace locations, shlex.quote when reading the stamp through the shell, and transfer when downloads can happen through presigned URLs. Callers elsewhere use its returned directory as the editable or rebuildable site source.

*Call graph*: calls 1 internal fn (transfer); 3 external calls (model_validate_json, quote, workspace_path).


##### `unpack_page_kit`  (lines 202–220)

```
async def unpack_page_kit(ctx: ToolContext, dest: str, runtime_root: str | None=None) -> None
```

**Purpose**: Places the current page SDK kit into a sandbox project so an app page can build against this extension’s current components. It also removes the temporary archive after unpacking so the archive itself does not accidentally become part of the site.

**Data flow**: It receives a tool context, a destination directory, and optionally a different runtime root used for containment checks. It writes the prepared kit archive into the destination, asks the sandbox to run a small Python unpacking program, and raises an error if unpacking fails. After success, the destination contains an sdk folder with the kit files, and the temporary archive has been deleted inside the sandbox.

**Call relations**: This uses the PAGE_KIT_ARCHIVE bytes created by _page_kit_archive. It is used when preparing an app page project for building, after the project source has been placed in the sandbox and before the build expects to import files from the local sdk path.


### `extensions/sites/ufo_ext_sites/store.py`

`domain_logic` · `deploy, site lookup, sharing, and visibility changes`

A hosted site is like a signpost: when someone opens a permanent site link, the system needs to know where that link should go, who is allowed to see it, and what preview image or share card belongs to it. This file keeps that signpost in the database. Without it, links could point at the wrong app, private pages could become visible by accident, and one member could overwrite or unhost another member’s site.

The file defines the database table for hosted sites, small data shapes for stored source files, and a `HostedSites` service that reads and changes rows for one workspace. Every query is explicitly limited to that workspace, because the database connection itself is not workspace-aware.

The main action is registering a deploy. A deploy can update an existing site name, create a new one, or replace another site that was using the same port. Before doing that, the code checks ownership rules: only the creator can change visibility, and taking over a port counts as unhosting the old site. Static deploys store a manifest describing files in blob storage; server-style deploys instead serve from a sandbox port.

The file also supports later updates: setting screenshots, writing share-card data, listing visible sites, changing visibility, binding a site as an agent homepage, releasing that binding, and unregistering a site.

#### Function details

##### `SourceManifest._rooted`  (lines 96–99)

```
def _rooted(cls, root: str) -> str
```

**Purpose**: Checks that a stored source manifest uses a safe blob-storage prefix. The prefix must look like a site or app storage area and must end with a slash so files can be placed under it.

**Data flow**: It receives a proposed root string → checks that it starts with `sites/` or `apps/` and ends with `/` → returns the same root if it is valid, or raises an error if it is not.

**Call relations**: This is called by Pydantic, the data validation library, when a `SourceManifest` is created. It protects later readers from trusting a manifest that points outside the expected storage area.


##### `SourceManifest._pathed`  (lines 103–118)

```
def _pathed(cls, files: dict[str, SiteFile]) -> dict[str, SiteFile]
```

**Purpose**: Checks that every file path in a stored static site is a plain path inside the site. This prevents a manifest from containing paths that escape the site folder, use backslashes, start at the filesystem root, or contain hidden control characters.

**Data flow**: It receives the manifest’s dictionary of file paths to file metadata → inspects each path → asks `contained_relative` to confirm the path stays under the site-source anchor → returns the original dictionary if every path is safe, or raises an error when one is unsafe.

**Call relations**: This is also run by Pydantic when building a `SourceManifest`. It hands each path to the sandbox path-safety helper so stored-source serving can later use manifest keys without reinterpreting dangerous paths.

*Call graph*: 1 external calls (contained_relative).


##### `site_name`  (lines 149–157)

```
def site_name(raw: str) -> str
```

**Purpose**: Turns a member-supplied site name into the safe, short name stored in the registry and used in links. It makes names predictable by lowercasing them and replacing runs of non-letter-or-number characters with hyphens.

**Data flow**: It receives a raw name such as a title typed by a user → converts it into a slug, trims it to the maximum length, and removes dangling hyphens → returns the slug, or raises `InvalidSiteName` if nothing usable remains.

**Call relations**: Callers use this before registering a site, because they may need the final name to build a link before writing the row. If the name cannot produce any letters or digits, it stops the deploy with a clear tool-facing error.

*Call graph*: 1 external calls (__init__).


##### `default_visibility`  (lines 160–168)

```
def default_visibility(audience: Audience) -> Visibility
```

**Purpose**: Chooses the starting visibility for a new site from the audience of the conversation that created it. Private direct messages and external rooms default to private, while normal workspace conversations default to workspace-visible.

**Data flow**: It receives an `Audience`, meaning the people or room connected to the conversation → parses it and checks whether it represents one member or an outside audience → returns `private` or `workspace` as the site’s initial visibility.

**Call relations**: `HostedSites.register` calls this only when inserting a brand-new site and no explicit visibility was supplied. It relies on audience parsing helpers so site visibility follows the conversation’s sharing context.

*Call graph*: called by 1 (register); 2 external calls (audience_member, parse_audience).


##### `visibility_level`  (lines 171–179)

```
def visibility_level(value: str) -> Visibility
```

**Purpose**: Accepts only the three visibility words the registry understands: private, workspace, and public. It prevents bad text from being treated as a real access level.

**Data flow**: It receives a string from storage or input → compares it with the allowed values → returns it as a valid visibility value, or raises an error if it is anything else.

**Call relations**: `_site` calls this while turning a database row into a `HostedSite` object. That means invalid stored visibility is caught at the boundary where raw database text becomes trusted application data.

*Call graph*: called by 1 (_site).


##### `HostedSites.register`  (lines 213–306)

```
async def register(self, conversation_id: UUID, name: str, port: int, creator_member_id: UUID, visibility: Visibility | None, audience: Audience, may_unhost: bool, *, manifest: str | None) -> HostedSi
```

**Purpose**: Creates or updates the registry row for a site after a deploy. It decides what link name points to which port or static source, while preserving important choices like existing visibility unless the caller is allowed to change them.

**Data flow**: It receives the conversation, safe site name, port, creator, optional requested visibility, audience, whether this turn may unhost, and optional static-source manifest → opens a database transaction → checks refusal rules, deletes any same-conversation site displaced from the port, updates an existing row or inserts a new one, and stamps a deploy generation number → reads back and returns the final `HostedSite`.

**Call relations**: Deploy code calls this when a site has just been served or static files have been promoted. Inside, it asks `_refuse` to enforce ownership and unhosting rules, uses `default_visibility` for new rows, and reads the row back with `_read` so the caller gets the exact stored state.

*Call graph*: calls 3 internal fn (_read, _refuse, default_visibility); 6 external calls (case, delete, insert, update, time_ns, uuid4).


##### `HostedSites.redeploy`  (lines 308–335)

```
async def redeploy(self, conversation_id: UUID, name: str, manifest: str) -> HostedSite | None
```

**Purpose**: Replaces the stored static source for an existing site without changing its identity, owner, port, or visibility. This is used when a deploy should update the page behind an existing link rather than create a separate site.

**Data flow**: It receives a conversation, site name, and new manifest string → opens a transaction → updates the row’s source manifest, update time, and deploy generation stamp → reads the row back and returns it, or returns `None` if the site no longer exists.

**Call relations**: This follows the same generation-stamping idea as `register`, so viewers can notice that the page changed. It hands final lookup to `_read`, which converts the database row into the normal `HostedSite` object.

*Call graph*: calls 1 internal fn (_read); 3 external calls (case, update, time_ns).


##### `HostedSites.homepage`  (lines 337–349)

```
async def homepage(self, agent_id: UUID) -> HostedSite | None
```

**Purpose**: Finds the site currently bound as a particular agent’s homepage. There should be at most one such site for an agent.

**Data flow**: It receives an agent ID → queries this workspace for a row whose homepage binding points to that agent → returns a `HostedSite` if one exists, otherwise `None`.

**Call relations**: Homepage display or routing code can call this when it needs the site for an agent. It builds its database query through `_columns` and turns the result into application data through `_site`.

*Call graph*: calls 2 internal fn (_columns, _site).


##### `HostedSites.set_preview`  (lines 351–371)

```
async def set_preview(self, conversation_id: UUID, name: str, preview: StoredPreview) -> None
```

**Purpose**: Stores the screenshot-like preview captured for a site after deployment. Registration does not wait for the picture, so this lets the preview arrive later.

**Data flow**: It receives a conversation, site name, and `StoredPreview` with a blob key and byte size → opens a transaction → updates the matching row’s preview fields and update time → returns nothing.

**Call relations**: Deploy or rendering code calls this after a page preview has been captured. If the site was unregistered or renamed while the picture was being made, the update simply matches no row, leaving any previous preview alone.

*Call graph*: 1 external calls (update).


##### `HostedSites.set_share_card`  (lines 373–397)

```
async def set_share_card(self, conversation_id: UUID, name: str, blob_key: str, digest: str) -> None
```

**Purpose**: Stores the share-card image information for a site, including a digest of the card bytes. The digest helps public share-card URLs change when the card changes, avoiding stale cached cards.

**Data flow**: It receives a conversation, site name, blob key, and digest → opens a transaction → writes those values onto the matching site row and updates the timestamp → returns nothing.

**Call relations**: The share-card composition flow calls this after it has rendered and stored the card. Like preview updates, it is deliberately separate from registration, so a slow or failed card render does not block the site link itself.

*Call graph*: called by 1 (_compose); 1 external calls (update).


##### `HostedSites.read`  (lines 399–401)

```
async def read(self, conversation_id: UUID, name: str) -> HostedSite | None
```

**Purpose**: Looks up one site by conversation and name inside this workspace. It is the simple public read method for callers that already know the site identity.

**Data flow**: It receives a conversation ID and site name → opens a transaction → delegates the actual query to `_read` → returns the matching `HostedSite` or `None`.

**Call relations**: Other parts of the site system use this when resolving or inspecting a specific site. It keeps transaction setup outside `_read`, so `_read` can also be reused by larger operations already inside a transaction.

*Call graph*: calls 1 internal fn (_read).


##### `HostedSites.all`  (lines 403–413)

```
async def all(self) -> tuple[HostedSite, ...]
```

**Purpose**: Lists every hosted site in the workspace, ordered from oldest to newest. Higher-level permission checks can decide which of these a caller is allowed to see.

**Data flow**: It opens a transaction → selects all site rows scoped to the workspace and orders them by creation time and name → converts each row to `HostedSite` → returns them as a tuple.

**Call relations**: Workspace-wide listing code can call this when it needs the full registry view. It uses `_columns` for a consistent selected shape and `_site` for consistent conversion from database rows.

*Call graph*: calls 2 internal fn (_columns, _site).


##### `HostedSites.conversation`  (lines 415–432)

```
async def conversation(self, conversation_id: UUID, names: tuple[str, ...], limit: int) -> tuple[HostedSite, ...]
```

**Purpose**: Lists selected hosted sites belonging to one conversation. It narrows by a provided set of names and applies a limit.

**Data flow**: It receives a conversation ID, a tuple of names, and a maximum count → queries matching rows in this workspace and conversation → orders them consistently → converts them into `HostedSite` objects and returns a tuple.

**Call relations**: Conversation-level views can call this when they need known site names from that conversation. It shares the same `_columns` and `_site` path as other read methods.

*Call graph*: calls 2 internal fn (_columns, _site).


##### `HostedSites.visible_conversation`  (lines 434–464)

```
async def visible_conversation(self, conversation_id: UUID, member_id: UUID, limit: int, *, admin: bool, homepage_agents: frozenset[UUID]) -> tuple[HostedSite, ...]
```

**Purpose**: Lists the sites in a conversation that a particular member is allowed to open. It includes the member’s own private sites, non-private sites, sites visible through homepage agent bindings, and all sites for admins.

**Data flow**: It receives a conversation, member, limit, admin flag, and the set of homepage agents that are visible to the member → builds a visibility filter unless the member is an admin → queries matching rows → converts them to `HostedSite` objects and returns them.

**Call relations**: UI or frame code can call this when showing a member the sites they can access in a conversation. It uses SQL’s `or` condition to express the access choices, then uses `_columns` and `_site` for the common read path.

*Call graph*: calls 2 internal fn (_columns, _site); 1 external calls (or_).


##### `HostedSites.set_visibility`  (lines 466–481)

```
async def set_visibility(self, conversation_id: UUID, name: str, visibility: Visibility) -> HostedSite | None
```

**Purpose**: Changes one site’s visibility level and returns the updated row. It also changes the site generation token so old authorization decisions tied to the previous visibility do not silently remain valid.

**Data flow**: It receives a conversation, name, and new visibility → opens a transaction → updates the matching row’s visibility, generation, and update time → reads the row back → returns the updated `HostedSite` or `None` if it disappeared.

**Call relations**: Visibility-changing flows call this after they have decided the caller is allowed to make the change. It uses `_read` afterward so callers see the new stored state.

*Call graph*: calls 1 internal fn (_read); 2 external calls (update, uuid4).


##### `HostedSites.set_homepage`  (lines 483–509)

```
async def set_homepage(self, agent_id: UUID, conversation_id: UUID, name: str) -> HostedSite | None
```

**Purpose**: Binds a named site as an agent’s homepage. Before setting the new binding, it clears any older homepage binding for that same agent so there is only one.

**Data flow**: It receives an agent ID, conversation ID, and site name → opens a transaction → removes that agent ID from any current homepage row in the workspace → writes the agent ID onto the named site → reads and returns the bound site, or `None` if the named site is gone.

**Call relations**: Agent-homepage setup code calls this when assigning a page to an agent. It uses `_read` after the updates to return the actual bound row.

*Call graph*: calls 1 internal fn (_read); 1 external calls (update).


##### `HostedSites.release_homepage`  (lines 511–535)

```
async def release_homepage(self, conversation_id: UUID, name: str, visibility: Visibility) -> None
```

**Purpose**: Removes a site’s homepage binding and restores the site’s own visibility level. While bound, visibility comes from the agent; after release, the site needs its own access setting again.

**Data flow**: It receives a conversation, site name, and visibility to resume → opens a transaction → clears the homepage agent, writes the supplied visibility, creates a new generation token, and updates the timestamp → returns nothing.

**Call relations**: Homepage-unbinding flows call this when a site stops serving as an agent homepage. It uses a new generation value so any saved permission grant tied to the old state must be reconsidered.

*Call graph*: 2 external calls (update, uuid4).


##### `HostedSites.unregister`  (lines 537–548)

```
async def unregister(self, conversation_id: UUID, name: str) -> None
```

**Purpose**: Removes a site from the registry so its permanent link no longer resolves. This is the database-side act of unhosting, even though the sandbox process may keep running until its own lifecycle ends.

**Data flow**: It receives a conversation ID and site name → opens a transaction → deletes the matching row in this workspace → returns nothing.

**Call relations**: Unhost flows call this when a member intentionally removes a site link. It uses a direct delete because there is no remaining registry state to return.

*Call graph*: 1 external calls (delete).


##### `HostedSites.refuse_or_pass`  (lines 550–570)

```
async def refuse_or_pass(self, conversation_id: UUID, name: str, port: int, creator_member_id: UUID, visibility: Visibility | None, may_unhost: bool) -> HostedSite | None
```

**Purpose**: Runs the same safety checks that registration would run, but without writing anything. It lets a caller find out early whether a deploy would be refused or which existing site would be displaced from the port.

**Data flow**: It receives the proposed registration details → opens a transaction → delegates to `_refuse` → returns the site that would be displaced, or `None`, unless `_refuse` raises an ownership or speaker-related error.

**Call relations**: Deploy code can call this before serving on a port, while the old site is still alive. Later, `register` calls `_refuse` again inside the write transaction, so the same rules are enforced at the moment the row is actually changed.

*Call graph*: calls 1 internal fn (_refuse).


##### `HostedSites._refuse`  (lines 572–601)

```
async def _refuse(self, connection: AsyncConnection, conversation_id: UUID, name: str, port: int, creator_member_id: UUID, visibility: Visibility | None, may_unhost: bool) -> HostedSite | None
```

**Purpose**: Applies the refusal rules for registering a site. It protects creator-only visibility choices and prevents a deploy from taking over another member’s port-backed site without permission.

**Data flow**: It receives an open database connection plus proposed registration details → reads any existing same-name site → checks whether a non-creator is trying to change visibility → looks for a different site already on the requested port → checks whether that displacement is allowed → returns the displaced site or `None`, or raises a clear error.

**Call relations**: `refuse_or_pass` uses this as a dry run, and `register` uses it inside the real transaction. It calls `_read` to inspect the named site and `_on_port` to find any port conflict, then raises `NotTheSiteCreator` or `UnhostNeedsASpeaker` when the rules say no.

*Call graph*: calls 2 internal fn (_on_port, _read); called by 2 (refuse_or_pass, register); 2 external calls (__init__, __init__).


##### `HostedSites._on_port`  (lines 603–618)

```
async def _on_port(self, connection: AsyncConnection, conversation_id: UUID, port: int, name: str) -> HostedSite | None
```

**Purpose**: Finds whether another site in the same conversation is already registered on the requested port. This matters because one port can only serve one origin, so registering the new site would effectively unhost the old one.

**Data flow**: It receives an open connection, conversation ID, port, and the current site name to ignore → queries this workspace for a row in that conversation with the same port but a different name → returns that `HostedSite` or `None`.

**Call relations**: `_refuse` calls this while deciding whether a registration would displace an existing site. It uses `_columns` for the selected fields and `_site` to convert the database row.

*Call graph*: calls 2 internal fn (_columns, _site); called by 1 (_refuse); 1 external calls (execute).


##### `HostedSites._read`  (lines 620–632)

```
async def _read(self, connection: AsyncConnection, conversation_id: UUID, name: str) -> HostedSite | None
```

**Purpose**: Reads one hosted-site row using an existing database connection. It is the shared low-level lookup used by both public methods and multi-step transactions.

**Data flow**: It receives an open connection, conversation ID, and site name → queries the workspace-scoped table for exactly that row → returns a `HostedSite` object or `None`.

**Call relations**: `register`, `redeploy`, `read`, `set_visibility`, `set_homepage`, and `_refuse` all call this when they need a single site row. It uses `_columns` to keep the selected fields consistent and `_site` to build the application object.

*Call graph*: calls 2 internal fn (_columns, _site); called by 6 (_refuse, read, redeploy, register, set_homepage, set_visibility); 1 external calls (execute).


##### `HostedSites._columns`  (lines 634–651)

```
def _columns(self) -> sa.Select
```

**Purpose**: Builds the standard database select statement for hosted-site rows. It keeps all read methods selecting the same set of fields in the same shape.

**Data flow**: It takes no outside data besides the table definition → creates a SQL select for the columns needed to build `HostedSite` → returns that select object so callers can add filters and ordering.

**Call relations**: Read-style methods such as `_read`, `_on_port`, `all`, `conversation`, `homepage`, and `visible_conversation` call this before adding their own `where` conditions. This avoids each method hand-writing a slightly different column list.

*Call graph*: called by 6 (_on_port, _read, all, conversation, homepage, visible_conversation); 1 external calls (select).


##### `_aware`  (lines 654–657)

```
def _aware(moment: datetime) -> datetime
```

**Purpose**: Ensures a stored timestamp knows it is in UTC. Some databases, especially SQLite, may return a time without timezone information, and this function fixes that ambiguity.

**Data flow**: It receives a `datetime` value → checks whether it already has timezone information → returns it unchanged if it does, or returns a copy marked as UTC if it does not.

**Call relations**: `_site` calls this while converting database rows into `HostedSite` objects. That keeps later comparisons with other timezone-aware timestamps safe and predictable.

*Call graph*: called by 1 (_site); 1 external calls (replace).


##### `_site`  (lines 660–677)

```
def _site(row: sa.Row) -> HostedSite
```

**Purpose**: Turns a raw database row into a `HostedSite` data object used by the rest of the code. It is the boundary where database values become checked, typed application values.

**Data flow**: It receives a SQL row → validates the visibility text, fixes timestamp timezone information, and copies all relevant fields into a `HostedSite` instance → returns that instance.

**Call relations**: All read paths that fetch hosted-site rows use this, including `_read`, `_on_port`, `all`, `conversation`, `homepage`, and `visible_conversation`. It calls `visibility_level` and `_aware` so every returned site has valid visibility and clear UTC timestamps.

*Call graph*: calls 2 internal fn (_aware, visibility_level); called by 6 (_on_port, _read, all, conversation, homepage, visible_conversation); 1 external calls (__init__).

## 📊 State Registers Touched

- `reg-auth-tokens` — The signed login, surface, artifact, and SDK tokens used to prove that a caller or link is allowed to act.
- `reg-sandbox-handles` — The remembered sandbox or workspace handle for each conversation so tools can resume the same isolated files, terminals, browsers, and services.
- `reg-execution-environment` — The controlled runtime environment given to commands, files, terminals, browsers, and documents, including safe environment variables and containment rules.
- `reg-artifact-blob-store` — The shared file, blob, attachment, artifact, signed download, and media-preview storage used to publish and recover produced work.
- `reg-scheduled-jobs` — The durable background-job state for scheduled tasks, pauses, monitor checks, report writing, thumbnail repair, product metrics, and self-improvement runs.
- `reg-hosted-site-registry` — The hosted-site records that remember who owns each site, which conversation created it, where it runs, and how previews or sharing are allowed.
- `reg-object-kind-action-registry` — Process-local registry of built-in and extension object kinds, schemas, actions, visibility rules, and handlers used by the portal object APIs.
- `reg-conversation-slot-provider-registry` — Registered providers that summarize and read extension conversation slots such as artifacts, sources, sites, automations, and task panels.
- `reg-turn-created-reference-index` — Durable per-turn list of objects, artifacts, sites, files, or other references created during a turn for later transcript display, panels, delivery, and recovery.
