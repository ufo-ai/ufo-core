# Result delivery, portal rendering, objects, panels, and artifacts  `stage-15`

This stage is part of the main work loop and the end of a turn. It is where the system turns internal results into things people can actually see: replies, source lists, files, website links, automations, and other conversation panels. It acts like a display counter, taking finished work from the back room and arranging only the safe, allowed pieces for each viewer.

The shared slot model defines the kinds of items extensions may place in the conversation portal, such as sources, artifacts, tasks, sites, automations, and workspace changes, and rejects unsafe data before it reaches the screen. Research observations save web sources in the database and show them later in a Sources panel. Scheduled tasks and hosted sites add Automations and Sites panels, while filtering private details and keeping payloads small. Activity messages explain upcoming tool or skill use in user-friendly words. Reply handling strips hidden control tags from model text before showing replies. Artifacts manage shared files created by tools, and artifact routes provide secure signed downloads. Site objects let hosted websites be listed, inspected, permissioned, or removed.

## Files in this stage

### Conversation panel slots
These files populate conversation portal panels with safe, viewer-filtered sources, automations, hosted sites, and shared slot payload shapes.

### `extensions/research/ufo_ext_research/observations.py`

`domain_logic` · `during research turns and when conversation source panels are read`

When the research extension searches the web or fetches a page, it needs a durable memory of what it found. Without this file, sources would be temporary: the assistant might cite or use a page during one turn, but the conversation would not have a reliable saved list of those links afterward.

The file defines a database table for source observations. Each saved source belongs to a workspace and a conversation, and it is identified by a digest of its URL. A digest is a fixed-length fingerprint made from the URL, useful as a compact database key. The stored details include the URL, title, snippet, optional published date, rank, and timestamps.

The main path is simple: search results or fetched pages are converted into a shared `RetrievedSource` shape, then `record_sources` validates and trims the text, saves each source, and updates an existing row if the same URL was already seen. It keeps only the most recent 100 sources per conversation, like keeping a tidy clipboard instead of an endless pile of notes.

At the end, the file registers `SOURCES_SLOT`, a provider for the conversation UI or API. That provider can count saved sources and read them back as a `SourcesSlotPayload`, including whether the list was truncated.

#### Function details

##### `_bounded`  (lines 53–54)

```
def _bounded(value: str, limit: int) -> str
```

**Purpose**: Cuts a text value down to a maximum length. This protects the saved source data from becoming too large for display or storage expectations.

**Data flow**: It receives a string and a character limit. It returns the same string if it is already short enough, or the beginning of the string up to that limit if it is too long. It does not change anything outside itself.

**Call relations**: When `record_sources` prepares a source for saving, it calls `_bounded` on titles, snippets, and dates before validating and storing them. This is the small trimming step before the larger database-writing step.

*Call graph*: called by 1 (record_sources).


##### `record_sources`  (lines 57–130)

```
async def record_sources(ext: ExtensionContext, conversation_id: UUID, turn_id: UUID, sources: tuple[RetrievedSource, ...]) -> None
```

**Purpose**: Saves a batch of retrieved sources for one conversation turn. It validates the source data, writes it to the database, updates older entries for the same URL, and removes excess old entries beyond the saved-source limit.

**Data flow**: It receives the extension context, a conversation ID, a turn ID, and a tuple of `RetrievedSource` items. If the tuple is empty, it does nothing. Otherwise it opens a database transaction, trims long fields, validates each source as a `ConversationSource`, makes a SHA-256 URL fingerprint, and inserts or updates the row for that source. After saving, it selects the newest retained source fingerprints and deletes older rows so the conversation keeps only the configured number of sources.

**Call relations**: `record_search_hits` and `record_fetched_page` both hand their source information to this function after converting it into `RetrievedSource` objects. Inside, it relies on the extension context to open a transaction, `_bounded` to shorten text, `ConversationSource` to reject invalid source data, `datetime.now` to timestamp changes, `sha256` to build the URL key, and SQLAlchemy select/delete operations to keep the stored list current and bounded.

*Call graph*: calls 2 internal fn (transaction, _bounded); called by 2 (record_fetched_page, record_search_hits); 5 external calls (__init__, now, sha256, delete, select).


##### `record_search_hits`  (lines 133–152)

```
async def record_search_hits(ext: ExtensionContext, conversation_id: UUID, turn_id: UUID, hits: tuple[SearchHit, ...]) -> None
```

**Purpose**: Turns search result objects into saved research sources. It is the bridge between the search subsystem’s result format and this file’s durable source storage.

**Data flow**: It receives the extension context, conversation ID, turn ID, and a tuple of `SearchHit` results. For each hit, it copies the URL, title, result text, and published date into a `RetrievedSource`. It then passes the full converted tuple to `record_sources`, which performs validation and database storage.

**Call relations**: This function is used when the research extension has search results to preserve. It does not write the database itself; instead, it prepares the results in the common shape and hands them off to `record_sources`.

*Call graph*: calls 1 internal fn (record_sources); 1 external calls (__init__).


##### `record_fetched_page`  (lines 155–173)

```
async def record_fetched_page(ext: ExtensionContext, conversation_id: UUID, turn_id: UUID, page: FetchedPage) -> None
```

**Purpose**: Saves one fetched web page as a research source for the conversation. This lets a directly opened or fetched page appear in the same source list as search results.

**Data flow**: It receives the extension context, conversation ID, turn ID, and a `FetchedPage`. It builds one `RetrievedSource` using the page URL as both URL and title, and using the page summary if available or the full text otherwise. It then sends that one-item tuple to `record_sources` for validation and storage.

**Call relations**: This function is used after a page has been fetched. Like `record_search_hits`, it is an adapter: it converts the fetched-page format into the common `RetrievedSource` format, then delegates the real saving work to `record_sources`.

*Call graph*: calls 1 internal fn (record_sources); 1 external calls (__init__).


##### `_source_count`  (lines 176–186)

```
async def _source_count(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: Counts how many saved sources exist for a conversation, capped at the public display limit. It helps the conversation slot summarize whether there are sources to show.

**Data flow**: It receives a `ConversationSlotContext`, which contains the extension context and conversation ID. It opens a database transaction, counts rows in the source observation table for the current workspace and conversation, and returns `None` if there are no rows. If rows exist, it returns the smaller of the actual count and the source limit.

**Call relations**: This function is registered as the `summarize` callback in `SOURCES_SLOT`. When the host system wants a quick summary of the Sources slot, it can call this function instead of reading every saved source.

*Call graph*: 1 external calls (select).


##### `_read_sources`  (lines 189–217)

```
async def _read_sources(ctx: ConversationSlotContext) -> SourcesSlotPayload
```

**Purpose**: Reads the saved source list for a conversation and packages it for the Sources slot. It returns the newest sources first and tells the caller whether more sources existed than can be shown.

**Data flow**: It receives a `ConversationSlotContext`. It opens a database transaction, selects source rows for the current workspace and conversation, orders them by most recently updated and then by rank, and asks for one more row than the display limit. It converts up to the first 100 rows into `ConversationSource` objects and returns a `SourcesSlotPayload` containing those sources plus a `truncated` flag if an extra row proved there were more.

**Call relations**: This function is registered as the `read` callback in `SOURCES_SLOT`. When the host system opens or refreshes the Sources slot, `_read_sources` pulls the stored rows from the database and turns them back into the payload shape expected by the conversation interface.

*Call graph*: 3 external calls (__init__, __init__, select).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/conversation_slot.py`

`domain_logic` · `conversation slot rendering`

This file is the bridge between the scheduled-task system and the conversation interface. Without it, a conversation could not show its related automations in a clean, permission-aware way. Think of it like a display case: it does not create the scheduled tasks, but it chooses which ones belong in this conversation, verifies that they are still authorized, and formats them for display.

The main provider is `AUTOMATIONS_SLOT`, which tells the larger UFO system there is a conversation slot called “Automations.” When the slot is opened, `_read` gathers the scheduled tasks tied to the conversation and turns them into `ConversationAutomation` records. It compares stored tasks with `ctx.visible_items`, which is the current conversation’s list of items the viewer may see. This matters because a task might exist in storage, but should only be shown if it matches a visible item and the item’s generation matches the task ID.

The file also protects the interface from overly large content. Descriptions, schedules, last statuses, and last responses are shortened to fixed limits. If anything is omitted or cut down, the returned payload marks itself as `truncated`, meaning “there is more information than shown here.” If the viewer is not allowed to see content, descriptions and latest responses are hidden.

#### Function details

##### `_scheduler`  (lines 16–19)

```
def _scheduler(ctx: ConversationSlotContext) -> ScheduleStore
```

**Purpose**: This helper gets the scheduled-task storage object for the current conversation slot request. It makes sure the slot was given the extension context it needs before trying to read schedules.

**Data flow**: It receives a `ConversationSlotContext`, which includes request information and possibly an extension context. If the extension context is missing, it stops with an error because scheduled tasks cannot be read safely. If it is present, it creates and returns a `ScheduleStore`, which is the object used to read task records.

**Call relations**: Both `_conversation` and `_read` call this when they need access to stored scheduled tasks. It hands them a `ScheduleStore`, so the rest of the file can ask for task lists and task inspection details without knowing how the store is built.

*Call graph*: called by 2 (_conversation, _read); 1 external calls (__init__).


##### `_conversation`  (lines 22–28)

```
async def _conversation(ctx: ConversationSlotContext) -> tuple[ScheduledTask, ...]
```

**Purpose**: This function finds the scheduled tasks that belong to the current conversation and match the visible item names. It asks for one more than the maximum display limit so the caller can tell whether the result had to be cut off.

**Data flow**: It receives the conversation slot context, reads the names of `ctx.visible_items`, then asks the schedule store for tasks with this conversation ID and those names. It returns the matching scheduled task records as a tuple.

**Call relations**: `_read` calls `_conversation` as its first broad fetch of candidate automations. `_conversation` uses `_scheduler` to reach the schedule store, then gives `_read` the raw task rows that still need authorization checks and formatting.

*Call graph*: calls 1 internal fn (_scheduler); called by 1 (_read).


##### `_read`  (lines 31–90)

```
async def _read(ctx: ConversationSlotContext) -> AutomationsSlotPayload
```

**Purpose**: This is the main reader for the Automations conversation slot. It builds the final payload that the conversation interface can show, while enforcing visibility rules and size limits.

**Data flow**: It receives the conversation slot context. First it gets the schedule store and fetches candidate tasks for the conversation. It then builds a lookup of visible conversation items and keeps only tasks whose name is visible and whose stored task ID matches the visible item’s generation. Next it inspects those tasks to get runtime details such as next run time, last run time, latest status, and latest response. For each authorized task, it creates a `ConversationAutomation` with shortened text fields and with private content hidden when `content_visible` is false. It returns an `AutomationsSlotPayload` containing the formatted automations and a `truncated` flag if anything was skipped or shortened.

**Call relations**: The `AUTOMATIONS_SLOT` provider uses `_read` when the system needs the full contents of the Automations panel. `_read` calls `_conversation` to get candidate scheduled tasks, calls `_scheduler` so it can inspect them in detail, then packages everything into `ConversationAutomation` objects inside an `AutomationsSlotPayload`.

*Call graph*: calls 2 internal fn (_conversation, _scheduler); 2 external calls (__init__, __init__).


##### `_summarize`  (lines 93–95)

```
async def _summarize(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: This function gives a quick count for the Automations slot without loading all the detailed automation data. It is useful when the interface only needs a small summary, such as a badge count.

**Data flow**: It receives the conversation slot context and counts the visible items, capped at the maximum number of automations the slot can show. If the count is zero, it returns `None`; otherwise it returns the count.

**Call relations**: The `AUTOMATIONS_SLOT` provider uses `_summarize` when the system asks for a lightweight summary of the slot. Unlike `_read`, it does not call into storage or inspect tasks; it relies only on the visible items already present in the context.


### `extensions/sites/ufo_ext_sites/conversation_slot.py`

`domain_logic` · `request handling`

This file is the bridge between stored hosted sites and the conversation user interface. A “slot” is a small panel or section of conversation-related information. Here, the slot is for Sites: web pages connected to the current conversation.

The important problem it solves is safety. The database may contain many sites, but this slot should only show sites that are visible through the conversation’s current authorization items. Think of it like a guest list at a door: a site is only let into the display if its matching permission card is present and still has the same version number.

The main reader, `_read`, looks at the conversation’s visible items, translates the authorization object names into site names, fetches matching hosted site records, then double-checks that each returned site still matches the visible authorization name and generation. It then builds `ConversationSite` entries, including a public URL created from the workspace, conversation, and site name. It only returns up to `CONVERSATION_SITES_MAX` items and marks the result as truncated if more were available.

The smaller `_summarize` function gives the slot system a quick count to show before loading full details. Finally, `SITES_SLOT` registers this behavior with the wider conversation-slot framework.

#### Function details

##### `_read`  (lines 13–47)

```
async def _read(ctx: ConversationSlotContext) -> SitesSlotPayload
```

**Purpose**: This function builds the full Sites slot content for one conversation. It returns only the hosted sites that match currently visible authorization items, so the user does not see sites they should not see.

**Data flow**: It receives a conversation slot context containing the conversation id, visible items, workspace information, a database transaction, and the public base URL. It first turns visible authorization object names into expected site names and generation numbers. It then asks `HostedSites` for matching site rows, checks that each row still matches a visible authorization object and generation, converts the approved rows into `ConversationSite` objects with public URLs, and returns a `SitesSlotPayload`. If there are more approved sites than the configured maximum, the output says the list was truncated.

**Call relations**: The conversation-slot system calls this function through `SITES_SLOT` when it needs the actual Sites content. `_read` uses `site_name_from_object` and `site_object_name` to move between authorization object names and site names, uses `HostedSites` to read site records from storage, uses `site_url` to build links users can open, and finally wraps the result in SDK payload objects such as `ConversationSite` and `SitesSlotPayload`.

*Call graph*: 6 external calls (__init__, __init__, __init__, site_name_from_object, site_object_name, site_url).


##### `_summarize`  (lines 50–52)

```
async def _summarize(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: This function gives a quick count for the Sites slot without loading all site details. It helps the interface decide whether there is anything to show and how many items to hint at.

**Data flow**: It receives the conversation slot context and looks only at `ctx.visible_items`. It counts them, caps the count at `CONVERSATION_SITES_MAX`, and returns that number. If the count is zero, it returns `None`, which means there is no summary to show.

**Call relations**: The conversation-slot system calls this function through `SITES_SLOT` when it wants a lightweight summary instead of the full site list. Unlike `_read`, it does not call storage or URL helpers; it simply uses the visible items already present in the context.


### `core/src/ufo/ext/conversation_slots.py`

`data_model` · `conversation slot validation and rendering`

A conversation can have side panels or “slots” that show useful extra information alongside messages: files created during the chat, links used as sources, task lists, deployed sites, scheduled automations, or workspace changes. This file is the contract for those slots. It says exactly what each kind of slot data must look like, how long text fields may be, what URLs are allowed, and which payload types the system supports.

Most of the file is made of Pydantic models. Pydantic is a validation library: it checks incoming data and turns it into predictable Python objects. These models are frozen, meaning they cannot be changed after creation, and they reject unknown extra fields. That makes slot data safer and easier to trust, like using a fixed form instead of accepting scribbled notes in any format.

The file also defines provider and context objects. A slot provider describes an extension’s slot: its id, label, icon, content type, and the async functions used to summarize and read its contents. A slot context carries the conversation, audience, messages, visible items, and extension context needed when those functions run.

Important safety behavior lives in the validators. Image preview URLs must be same-origin and root-relative, while artifact, source, and site URLs must be plain HTTP or HTTPS links without embedded usernames or passwords.

#### Function details

##### `ImagePreview.same_origin_url`  (lines 52–65)

```
def same_origin_url(cls, value: str) -> str
```

**Purpose**: This checks that an image preview URL points to a safe path on the same website, rather than to an outside site or a suspicious URL. It protects the portal from loading preview images from unexpected places.

**Data flow**: It receives the URL string from an ImagePreview. It parses and decodes the URL, then rejects it if it is not root-relative, starts like a network URL, contains a scheme such as http, names another host, includes a fragment, contains backslashes, or contains hidden control characters. If the URL passes all checks, the same string is returned unchanged.

**Call relations**: Pydantic calls this validator automatically when an ImagePreview is created. Inside the check, it uses standard URL parsing, URL decoding, and character-category lookup so it can spot unsafe forms before the preview data is accepted.

*Call graph*: 3 external calls (category, unquote, urlsplit).


##### `ConversationArtifact.http_url`  (lines 81–92)

```
def http_url(cls, value: str | None) -> str | None
```

**Purpose**: This checks that an artifact’s optional download or view URL is a normal HTTP or HTTPS web link. It rejects links with missing hosts or embedded login details, which could be unsafe or misleading.

**Data flow**: It receives either a URL string or None from a ConversationArtifact. If the value is None, it leaves it as None. Otherwise it parses the URL and verifies that the scheme is http or https, that a hostname exists, and that no username or password is included. A valid URL is returned unchanged; an invalid one raises a validation error.

**Call relations**: Pydantic runs this when a ConversationArtifact is built. The function hands the raw string to the standard URL parser so the artifact model only accepts links that are safe enough for the conversation portal to display.

*Call graph*: 1 external calls (urlsplit).


##### `ConversationSource.http_url`  (lines 113–122)

```
def http_url(cls, value: str) -> str
```

**Purpose**: This checks that a cited source link is a valid HTTP or HTTPS URL without embedded credentials. It helps ensure source cards shown to users are ordinary web links, not malformed or credential-bearing URLs.

**Data flow**: It receives the source URL string from a ConversationSource. It parses the string, checks for an http or https scheme, confirms there is a hostname, and rejects any URL containing a username or password. If everything is acceptable, it returns the original URL.

**Call relations**: This validator is called automatically during ConversationSource creation. It relies on the standard URL parser, then gives Pydantic either a clean accepted value or an error that stops the bad source from being stored in the slot payload.

*Call graph*: 1 external calls (urlsplit).


##### `TasksSlotPayload.consistent_progress`  (lines 151–165)

```
def consistent_progress(self) -> 'TasksSlotPayload'
```

**Purpose**: This checks that a task slot’s counts make sense together. It prevents the user interface from showing impossible task progress, such as more completed tasks than total tasks.

**Data flow**: It receives the fully built TasksSlotPayload object. It compares completed_count, total_count, the number of visible tasks, and the visible tasks’ statuses. It rejects impossible combinations, such as visible completed tasks exceeding the completed total, visible incomplete tasks exceeding the incomplete total, or an untruncated list whose visible task count does not match the total. If the numbers are consistent, it returns the same payload object.

**Call relations**: Pydantic calls this after the individual task payload fields have been read. It acts as the final sanity check for task slot data before that data can be used by the conversation portal.


##### `ConversationSite.http_url`  (lines 181–190)

```
def http_url(cls, value: str) -> str
```

**Purpose**: This checks that a site URL is a normal HTTP or HTTPS web address without embedded username or password information. It protects site cards from carrying unsafe or confusing links.

**Data flow**: It receives the URL string from a ConversationSite. It parses the URL, verifies that the scheme is http or https, confirms that a hostname is present, and rejects URLs that include credentials. If the URL is valid, it returns the original string.

**Call relations**: This validator runs automatically when a ConversationSite is created. It uses standard URL parsing so that only acceptable site links become part of a sites slot payload.

*Call graph*: 1 external calls (urlsplit).


### Turn-facing messages
These files turn internal activity and hidden reply sections into clean member-facing status updates and responses.

### `core/src/ufo/activity.py`

`domain_logic` · `request handling`

When the system prepares a tool call, the raw input can be detailed, structured, and meant for machines rather than people. This file creates a cleaner public-facing version of that action, like turning a kitchen order ticket into a short note a customer can understand.

Its main job is to produce one activity frame for a bound tool call. A “tool call” here means a request for the system to use some named capability with a bundle of input data. If the tool is the special skill-loading tool, the file reports that as a skill load and extracts the skill name. For any other tool, it creates a tool activity record with the tool name, an optional human-written description, and a compact preview of the input.

The preview is deliberately shortened. Tool inputs can be large, so this file converts the input to compact JSON text and cuts it off after a fixed number of characters, adding an ellipsis when needed. Without this step, activity messages could become noisy, leak too much detail, or overwhelm the interface.

#### Function details

##### `tool_activity`  (lines 12–25)

```
def tool_activity(call: ToolUseBlock) -> ToolCall | SkillLoad
```

**Purpose**: Creates the one visible activity item for a tool call. It gives members a concise explanation of what the system is doing, while keeping raw tool input short and readable.

**Data flow**: It receives a ToolUseBlock, which contains a tool name and an input dictionary. If the tool name is the special load-skill tool, it reads the requested skill name and returns a SkillLoad record. Otherwise, it reads any user-facing description, converts the full input into compact JSON text, trims that preview if it is too long, and returns a ToolCall record containing the tool name, preview, and description.

**Call relations**: This function is the translation point between internal tool-use data and the activity objects shown to members. When it builds a skill-loading activity, it creates a SkillLoad object. For normal tool calls, it creates a ToolCall object, using JSON conversion to make the tool input displayable as short text.

*Call graph*: 3 external calls (__init__, __init__, dumps).


### `core/src/ufo/loop/replies.py`

`domain_logic` · `live streaming and round completion`

The model can mark part of its output as a direct reply to a particular earlier message, using tags like `<reply-to message="..."> ... </reply-to>`. Those tags are instructions for the system, not words a person should see. This file is the filter and extractor for that hidden instruction language.

After a round is complete, `marked_replies` scans the full text, pulls out each closed reply span, records who it was meant for if the message id is valid, and returns a cleaned version of the text with the tags removed. The spoken words stay in the cleaned text, because the round still really said them.

During live streaming, text arrives in small chunks, and a tag may be split across chunks. `ReplyRedaction` works like a cautious curtain: it shows safe text immediately, hides anything inside a reply span, and waits when the end of the current chunk might be the start of a tag. If a tag is malformed, nested, or never closed, the unsafe markup is not shown. This matters because without it, users could see internal control tags or see reply-only text twice: once in the live narration and again as the final delivered reply.

#### Function details

##### `marked_replies`  (lines 42–51)

```
def marked_replies(text: str) -> tuple[tuple[MarkedReply, ...], str]
```

**Purpose**: This function reads a completed round of text and finds every properly closed `<reply-to ...>` section. It returns both the member-facing reply records and the same text with all reply markup removed.

**Data flow**: It takes the full text of a round as input. It searches for complete reply spans, strips any reply tags from the words inside, trims extra whitespace, and turns non-empty spans into `MarkedReply` objects. For each span, it asks `_named_message` to turn the tag's `message` value into a real UUID if possible. It outputs a tuple of found replies and a cleaned copy of the original text with the markup removed.

**Call relations**: This is used after the model has finished a round, when the system can safely look at the whole output at once. It relies on `_named_message` to validate the named message id, then creates `MarkedReply` records that later parts of the system can deliver as separate replies.

*Call graph*: calls 1 internal fn (_named_message); 1 external calls (__init__).


##### `_named_message`  (lines 54–58)

```
def _named_message(named: str) -> UUID | None
```

**Purpose**: This small helper checks whether the message name written in a reply tag is a valid UUID, which is a standard unique identifier format. If it is not valid, the reply is still kept, but it is not tied to a specific message.

**Data flow**: It receives the raw text from the tag's `message` field. It trims surrounding spaces and tries to build a UUID from it. If that succeeds, the UUID comes out; if it fails, the function returns `None` instead of raising an error.

**Call relations**: `marked_replies` calls this while turning discovered reply spans into `MarkedReply` records. This keeps bad or misspelled message ids from breaking the whole parsing step.

*Call graph*: called by 1 (marked_replies); 1 external calls (UUID).


##### `ReplyRedaction.feed`  (lines 77–99)

```
def feed(self, chunk: str) -> str
```

**Purpose**: This method filters one incoming stream chunk at a time so hidden reply spans and their tags are not shown in the live output. It publishes only text that is known to be safe right now.

**Data flow**: It receives the next piece of generated text and adds it to any text that was being held back from earlier chunks. If it is currently inside a reply span, it waits until it sees the closing tag and discards the hidden span. If it is outside a span, it publishes normal text before an opener, enters hidden mode when it finds an opener, or publishes only the settled part of the held text when the end might still become a tag. It returns the safe text to show now and updates its own stored state for the next chunk.

**Call relations**: This method is used during live output, before the full round is complete. It calls `_growing_suffix` when it is waiting for a closing tag split across chunks, and `_settled_chars` when it needs to decide how much ordinary-looking text is safe to release. At the end, it also strips any stray reply markup from what it publishes.

*Call graph*: calls 2 internal fn (_growing_suffix, _settled_chars); 1 external calls (find).


##### `_growing_suffix`  (lines 102–107)

```
def _growing_suffix(text: str, token: str) -> str
```

**Purpose**: This helper keeps only the tail end of text that might become a specific token when the next stream chunk arrives. It is used to recognize a closing tag even if that tag is split between chunks.

**Data flow**: It takes some current text and a target token, such as `</reply-to>`. It looks for the longest ending slice of the current text that matches the beginning of that token. It returns that possible unfinished token fragment, or an empty string if nothing at the end could grow into the token.

**Call relations**: `ReplyRedaction.feed` calls this while it is inside a hidden reply span and has not yet seen the full closing tag. This lets the redactor drop already-hidden content while preserving just enough characters to detect the closer in a later chunk.

*Call graph*: called by 1 (feed).


##### `_settled_chars`  (lines 110–119)

```
def _settled_chars(text: str) -> int
```

**Purpose**: This helper decides how many characters are safe to publish from text that is not currently inside a reply span. It protects against showing the beginning of a tag before the rest of the tag has arrived.

**Data flow**: It receives held text and looks for the last `<` character, because that might start a reply opener or closer. If there is no `<`, all text is safe. If the trailing text could still grow into `<reply-to` or `</reply-to>`, or looks like a partial opener, it returns the cutoff before that risky part. Otherwise, it says the whole text is safe to publish.

**Call relations**: `ReplyRedaction.feed` calls this whenever it has not found a complete opener but still needs to stream safe text. It gives `feed` a conservative boundary, so the live stream does not accidentally leak half-written markup.

*Call graph*: called by 1 (feed).


### Shared artifacts
These files manage produced conversation artifacts and deliver them through protected signed download routes.

### `core/src/ufo/artifacts.py`

`domain_logic` · `request handling`

An artifact is a saved file from a conversation, such as a report, image, or data export. This file turns those saved files into workspace objects that people and tools can find later. The important rule is identity: the same filename shared again in the same conversation becomes a new version of the same artifact, but the same filename from a different conversation is a different artifact. To make that visible, object names combine a short conversation prefix with a cleaned-up filename.

The file reads artifact records from the database and groups them by conversation plus filename. For each group, the newest share is treated as the current version. Listing shows a short row for each artifact. Getting detail shows the latest filename, media type, caption, timestamps, and a link back to the conversation that created it.

The `status` path does extra practical work. If the file is small enough, it fetches the bytes from blob storage and writes them back into the current sandbox workspace under `artifacts/<name>/<filename>`, so a later turn can reuse the file. It can also mint a temporary download URL. Deleting removes every stored version and its blobs. Creating or updating is refused, because the only correct way to make an artifact is to write a file and share it.

#### Function details

##### `artifact_object_names`  (lines 67–87)

```
def artifact_object_names(shares: Iterable[tuple[UUID, str]]) -> dict[tuple[UUID, str], str]
```

**Purpose**: Builds the public object names for shared files. It makes names stable and readable while still keeping files from different conversations separate.

**Data flow**: It receives conversation-and-filename pairs. It cleans each filename into a short slug, adds the conversation’s short ID prefix, checks whether any names collide, and adds a short hash only when needed. It returns a dictionary from each original conversation-and-filename pair to its final object name.

**Call relations**: ArtifactObjects._groups calls this after it has gathered raw shared-file rows from the database. This function delegates filename cleanup to _slug and collision-proof hashing to _identity_digest, then hands back names that the rest of the artifact object system can show to users.

*Call graph*: calls 2 internal fn (_identity_digest, _slug); called by 1 (_groups); 1 external calls (Counter).


##### `_slug`  (lines 90–92)

```
def _slug(filename: str) -> str
```

**Purpose**: Turns a filename into a safe, short, lowercase piece of an object name. This avoids awkward names with spaces, punctuation, or very long text.

**Data flow**: It receives a filename string. It lowercases it, replaces runs of non-letter-or-number characters with dashes, trims extra dashes, and limits the length. If nothing usable remains, it returns the fallback word “artifact.”

**Call relations**: artifact_object_names calls this while building the readable part of each artifact name. It is the small helper that makes names predictable before collision handling is considered.

*Call graph*: called by 1 (artifact_object_names).


##### `_identity_digest`  (lines 95–97)

```
def _identity_digest(identity: tuple[UUID, str]) -> str
```

**Purpose**: Creates a hash for a specific conversation-and-filename identity. The hash is used only as a short tie-breaker when two distinct artifacts would otherwise get the same name.

**Data flow**: It receives a conversation ID and filename. It combines them into text, runs SHA-256 over that text, and returns the hexadecimal digest string.

**Call relations**: artifact_object_names calls this only for names that collide. In that bigger flow, the digest is like a house number added to two otherwise identical street addresses.

*Call graph*: called by 1 (artifact_object_names); 1 external calls (sha256).


##### `ArtifactObjects.list`  (lines 115–117)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists artifact objects visible to the current tool run. It shows one row per conversation-and-filename artifact, using the latest version for the visible details.

**Data flow**: It receives the tool context and a list query. It reads the context’s allowed audience subjects, asks _groups for visible artifact groups, converts each group into a display row with _row, and passes the rows through object_page to apply listing behavior such as filtering, ordering, or paging.

**Call relations**: This is the artifact object’s normal listing entry. It relies on _groups to do the database reading and version grouping, then on _row to turn each group into a simple row suitable for the object system.

*Call graph*: calls 2 internal fn (_groups, _row); 1 external calls (object_page).


##### `ArtifactObjects.member_page`  (lines 119–133)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists artifact objects for a signed-in member outside an active tool turn. It uses the member’s own conversation audience rules rather than a turn’s context.

**Data flow**: It receives member information, admin flag, optional extension context, and a list query. It computes the audience subjects for that member, asks _groups for artifacts visible under those subjects, converts them to rows, and returns a paged object list.

**Call relations**: This mirrors ArtifactObjects.list for the portal or member-facing view. It uses conversation_audience and audience_subjects to decide what the member may see, then follows the same _groups → _row → object_page path.

*Call graph*: calls 2 internal fn (_groups, _row); 3 external calls (audience_subjects, conversation_audience, object_page).


##### `ArtifactObjects.get`  (lines 135–137)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ArtifactSpec] | None
```

**Purpose**: Returns the detailed description of one artifact visible to the current tool run. It does not copy the file bytes into the workspace; that extra work happens in status.

**Data flow**: It receives the tool context and an artifact name. It searches visible artifact groups with _find. If nothing matches, it returns null; otherwise it turns the matching versions into an ObjectDetail with _detail.

**Call relations**: This is the read-detail side of the object API. It depends on _find to resolve the user-facing name back to the grouped database rows, then hands those rows to _detail for the structured result.

*Call graph*: calls 2 internal fn (_find, _detail).


##### `ArtifactObjects.member_detail`  (lines 139–153)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[ArtifactSpec] | None
```

**Purpose**: Returns both the list-row summary and detail view for one artifact as seen by a signed-in member. It deliberately does not create a workspace copy or download link.

**Data flow**: It receives the artifact name and member information. It computes the member’s readable audience subjects, searches with _find, and returns null if absent. If found, it builds a MemberObject containing a row from _row and detail from _detail.

**Call relations**: This is the member-facing counterpart to get. It uses the same audience calculation as member_page, then combines the two presentation helpers, _row and _detail, so the portal can show a compact listing item and a fuller detail view together.

*Call graph*: calls 3 internal fn (_find, _detail, _row); 3 external calls (__init__, audience_subjects, conversation_audience).


##### `ArtifactObjects.status`  (lines 155–199)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Prepares the practical “use this artifact now” status for one artifact. For small files, it copies the latest bytes back into the sandbox workspace, and it can also create a temporary download link.

**Data flow**: It receives the tool context, artifact name, and an expected generation value. It finds the visible artifact group, chooses the newest version, optionally reads its bytes from blob storage if it is small enough, checks in the database that the artifact is still visible and unchanged in the important ways, writes the file into the sandbox if bytes were loaded, mints a time-limited URL if signing is configured, and returns size, share time, turn ID, version count, download URL, and workspace path. If the artifact is missing it returns null; if the blob is unexpectedly missing or visibility changed, it raises an error.

**Call relations**: Object get flows call this status path when they need the side effects that plain get avoids. It uses _find to resolve the artifact, _unchanged_visible to guard against stale or no-longer-visible records, workspace_tx for the database check, ws_current for workspace identity, and mint_artifact_url when a signed download link can be issued.

*Call graph*: calls 2 internal fn (_find, _unchanged_visible); 5 external calls (__init__, now, mint_artifact_url, workspace_tx, ws_current).


##### `ArtifactObjects.apply`  (lines 201–210)

```
async def apply(self, ctx: ToolContext, name: str, spec: ArtifactSpec, old: ArtifactSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to create or update an artifact through the object API. Artifacts must be produced by writing a workspace file and sharing it with `share_file`.

**Data flow**: It receives the desired artifact spec, old spec, context, name, and expected generation. It does not inspect or save the new spec; it immediately raises VerbNotSupported with guidance explaining the correct path.

**Call relations**: This is the write/update hook required by the object interface, but for artifacts it is intentionally a dead end. It points callers back to the sharing flow instead of allowing hand-made artifact records.

*Call graph*: 1 external calls (__init__).


##### `ArtifactObjects.delete`  (lines 212–238)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Deletes an artifact and all of its versions. This removes both the database records and the stored file bytes, so old download links stop working.

**Data flow**: It receives the tool context, artifact name, and expected generation. It finds the matching visible artifact group, checks under a database transaction that the latest version is still visible, deletes all matching shared-artifact rows for the current workspace, verifies the expected number of versions were removed, and then deletes each version’s blob and preview blob from storage.

**Call relations**: The object delete operation enters here. It uses _find to collect every version under the artifact name, _unchanged_visible to avoid deleting a changed or no-longer-visible artifact, workspace_tx and ws_current for the database work, and SQL deletion for the shared-artifact rows before cleaning blob storage.

*Call graph*: calls 2 internal fn (_find, _unchanged_visible); 3 external calls (delete, workspace_tx, ws_current).


##### `ArtifactObjects._unchanged_visible`  (lines 240–247)

```
def _unchanged_visible(self, ctx: ToolContext, latest: sa.Row) -> sa.Select
```

**Purpose**: Builds a database check that asks, “Is this artifact’s conversation still the same one, in the same workspace, for the same agent, and visible to this caller?” It is a safety guard before status or delete acts on a previously found artifact.

**Data flow**: It receives the tool context and the latest artifact row. It creates a SQL select query constrained by current workspace, conversation ID, selected agent, stored audience, and the caller’s readable subjects. It returns the query; it does not run it itself.

**Call relations**: ArtifactObjects.status and ArtifactObjects.delete call this after _find has already located an artifact. Those methods then execute the returned query inside a transaction to make sure the object did not effectively disappear or change audience before they copy bytes or delete records.

*Call graph*: called by 2 (delete, status); 3 external calls (select, object_agent_id, ws_current).


##### `ArtifactObjects._find`  (lines 249–253)

```
async def _find(self, subjects: frozenset[str], name: str) -> tuple[sa.Row, ...] | None
```

**Purpose**: Finds one artifact group by its public object name. It is the bridge from a user-facing name to the database rows that represent all versions of that artifact.

**Data flow**: It receives a set of readable audience subjects and an object name. It asks _groups for all visible named groups, searches for the matching name, and returns that group’s version rows or null if no group matches.

**Call relations**: get, member_detail, status, and delete all use this helper before doing their specific work. _find keeps name resolution in one place by reusing _groups rather than repeating grouping and naming rules.

*Call graph*: calls 1 internal fn (_groups); called by 4 (delete, get, member_detail, status).


##### `ArtifactObjects._groups`  (lines 255–297)

```
async def _groups(self, subjects: frozenset[str]) -> Sequence[tuple[str, tuple[sa.Row, ...]]]
```

**Purpose**: Loads visible shared-artifact rows from the database and groups them into artifact objects. Each group represents one conversation plus one filename, ordered with the newest version first.

**Data flow**: It receives readable audience subjects. It opens a workspace database transaction, selects shared-artifact rows joined to their turns and conversations, filters them to the current workspace, selected agent, and visible audiences, then groups rows by conversation ID and filename. It assigns public names with artifact_object_names, sorts each group by newest share first, sorts groups by name, and returns the named groups.

**Call relations**: This is the main data-gathering helper for the file. list and member_page use it directly to build pages, while _find uses it to resolve a single name. It calls artifact_object_names after the database read so the same naming rules apply everywhere.

*Call graph*: calls 1 internal fn (artifact_object_names); called by 3 (_find, list, member_page); 4 external calls (select, workspace_tx, object_agent_id, ws_current).


##### `_row`  (lines 300–311)

```
def _row(name: str, shares: tuple[sa.Row, ...]) -> ObjectRow
```

**Purpose**: Builds the compact list-row view for an artifact. This is the short version shown in listings and member pages.

**Data flow**: It receives an artifact name and its version rows, with the latest row first. It reads the latest filename, caption, conversation, and share time, creates a short summary with _summary, and returns an ObjectRow containing those display fields.

**Call relations**: ArtifactObjects.list, ArtifactObjects.member_page, and ArtifactObjects.member_detail call this when they need a listing-style representation. It sits between raw database rows and the object UI shape.

*Call graph*: calls 1 internal fn (_summary); called by 3 (list, member_detail, member_page); 1 external calls (__init__).


##### `_detail`  (lines 314–330)

```
def _detail(shares: tuple[sa.Row, ...]) -> ObjectDetail[ArtifactSpec]
```

**Purpose**: Builds the fuller detail view for an artifact. It describes the latest version and records when the artifact was first created and last updated.

**Data flow**: It receives all version rows for one artifact. It uses the newest row for the current filename, media type, caption, and conversation link, uses the oldest row as the creation time, uses the newest row as the update time, and returns an ObjectDetail containing an ArtifactSpec and a link to the creating conversation.

**Call relations**: ArtifactObjects.get and ArtifactObjects.member_detail call this after _find has found the artifact. It turns version history into the structured detail object expected by the object system.

*Call graph*: called by 2 (get, member_detail); 4 external calls (__init__, __init__, __init__, __init__).


##### `_summary`  (lines 333–339)

```
def _summary(shares: tuple[sa.Row, ...]) -> str
```

**Purpose**: Creates a short human-readable sentence for an artifact list row. It gives enough information to recognize the file at a glance.

**Data flow**: It receives the artifact’s version rows. It reads the latest filename, media type, size, and share date, adds a version count only if there is more than one version, trims the text to the maximum summary length, and returns the string.

**Call relations**: _row calls this while building ObjectRow values for listings. It is the small formatting helper that keeps list summaries consistent.

*Call graph*: called by 1 (_row).


### `core/src/ufo/surfaces/artifacts.py`

`io_transport` · `request handling`

This file is the guarded doorway for shared file downloads. A shared file is not served just because someone knows its path. The URL must carry a valid signature, which is like a tamper-proof stamp made with a server secret. If the stamp is missing, wrong, expired, or not tied to a workspace, the file bytes are not sent.

When a request comes in, the route checks the signed URL, finds the blob in the workspace’s file store, and streams it back. Streaming means the server sends the file in pieces instead of loading the whole thing into memory, so large downloads do not overwhelm the process. Downloads are sent as attachments, and browser caching is disabled so an old cached copy cannot bypass the signature check.

The file also supports image previews. A preview is only returned if the signed link specifically allows one, and the bytes are checked to make sure they really are the expected kind and size of image.

A useful special case is expired links. If a teammate clicks an old link while signed in, the code checks their session cookie and workspace membership. If they belong to the workspace that shared the file, it redirects them to a fresh signed URL. If not, it sends them to sign in or returns a clear forbidden response.

#### Function details

##### `download`  (lines 47–101)

```
async def download(request: Request, artifact_id: str, filename: str, exp: str='', sig: str='', preview: str='', workspace: Annotated[str, Query(alias='ws')]='') -> Response
```

**Purpose**: This is the HTTP endpoint that serves a shared artifact file or image preview. It protects the file by requiring a valid signed URL before reading any bytes from the workspace blob store.

**Data flow**: It receives the web request, path pieces such as the artifact id and filename, and signed URL fields like expiry time, signature, preview request, and workspace id. It reads the blob store and signing secret from the application state, verifies the URL, and then looks for the requested blob inside the signed workspace. If the request is for a preview, it validates and returns safe image bytes; otherwise it streams the file back as a download with headers that prevent sniffing and caching. If the signature is expired, it does not serve the file directly; it asks the refresh helper whether the current user may receive a fresh link.

**Call relations**: This is the public route FastAPI calls when a browser or integration opens an artifact link. It relies on the artifact URL verifier to decide whether the link is trustworthy, uses the image preview validator when an inline preview is requested, and hands expired-but-authentic links to _refreshed_for_member so workspace members can be redirected to a new grant.

*Call graph*: calls 1 internal fn (_refreshed_for_member); 9 external calls (now, HTTPException, Response, StreamingResponse, artifact_media_type, verify_artifact_url, validated_image_preview, ws, quote).


##### `_refreshed_for_member`  (lines 104–154)

```
async def _refreshed_for_member(request: Request, claims: ArtifactClaims, secret: str) -> RedirectResponse
```

**Purpose**: This helper decides whether an expired artifact link can be safely renewed for the person opening it. It lets signed-in teammates keep using old shared links while still blocking strangers.

**Data flow**: It receives the current request, the claims recovered from the expired signed URL, and the signing secret. It reads the session cookie, checks that the cookie proves a signed-in user, turns the session’s workspace id into a real UUID, and queries the database to confirm two things: the user is a member of that workspace, and the artifact belongs to that same workspace. If both checks pass, it creates a new expiry time, mints a fresh signed artifact URL, and returns a redirect to it. If anything does not check out, it raises the refusal response instead.

**Call relations**: download calls this only when the artifact URL was genuinely signed but has expired. This helper then consults the session verifier, the workspace database transaction, and the artifact URL minting code. When renewal is not allowed, it delegates the exact denial behavior to _refusal.

*Call graph*: calls 1 internal fn (_refusal); called by 1 (download); 9 external calls (now, RedirectResponse, or_, select, mint_artifact_url, verified_claims, workspace_tx, ws, UUID).


##### `_refusal`  (lines 157–162)

```
def _refusal(request: Request) -> HTTPException
```

**Purpose**: This small helper builds the response used when an expired link cannot be refreshed. It either sends a browser to the login page or returns a forbidden error for non-browser clients.

**Data flow**: It receives the current request and looks at the Accept header to see whether the client wants HTML. If it looks like a browser, it URL-encodes the current artifact link and creates a redirect to the login page with that link as the intended target. Otherwise, it creates a plain 403 forbidden error explaining that the download link expired.

**Call relations**: _refreshed_for_member calls this whenever there is no usable session, the session workspace id is invalid, the user is not a member, or the artifact does not belong to that workspace. It is the final branch that tells the caller what to do next without ever serving file bytes.

*Call graph*: called by 1 (_refreshed_for_member); 2 external calls (HTTPException, quote).


### Hosted site objects
This file exposes hosted websites as workspace objects that can be listed, inspected, shared, or removed.

### `extensions/sites/ufo_ext_sites/objects.py`

`domain_logic` · `request handling`

A deployed website is not just a running port; it also needs a stable workspace record so people and tools can find it later. This file is that bridge. It turns entries from the hosted-sites registry into normal workspace objects, so the chat/object system and the in-page site controls both talk about the same site and the same visibility setting.

Each site object gets a name made from the site name plus a short fingerprint of the conversation that created it. This matters because two different conversations can both deploy a site named `dashboard`, and they must not collide. The file refuses normal object creation because a site only exists after deployment knows which sandbox port is serving it.

The main class, `SiteObjects`, teaches the object system how to read sites, show useful fields such as owner email and URL, return detailed information for one site, update visibility, and delete the site record when it is unhosted. Visibility is the access rule: private means only the creator, workspace means signed-in workspace members, and public means anyone with the link. The creator may widen or narrow access. A workspace admin has a special safety power: they may make a site private, but may not make it more public.

#### Function details

##### `site_object_name`  (lines 57–61)

```
def site_object_name(conversation_id: UUID, name: str) -> str
```

**Purpose**: Builds the unique workspace object name for a hosted site. It combines the human site name with a short digest, meaning a fingerprint, of the conversation id so sites with the same name in different conversations stay separate.

**Data flow**: It receives a conversation id and a site name. It hashes the conversation id, keeps the first short piece of that hash, and appends it to the site name with a dash. The result is a stable object name such as `dashboard-9f21c0a4e3b7`.

**Call relations**: This is the shared naming rule. Listing code uses it when turning hosted-site records into object rows, conversation-grant code uses it when exposing sites from one conversation, and `site_name_from_object` uses it to verify that an object name really matches the expected pattern.

*Call graph*: called by 3 (member_conversation_rows, _named, site_name_from_object); 1 external calls (sha256).


##### `site_name_from_object`  (lines 64–70)

```
def site_name_from_object(conversation_id: UUID, object_name: str) -> str | None
```

**Purpose**: Tries to recover the original site name from a workspace object name. It is useful when code has an object-style name and needs to know which hosted site it refers to.

**Data flow**: It receives a conversation id and an object name. It computes the digest that should appear at the end, checks whether the object name ends with that suffix, removes it, and verifies the rebuilt name matches exactly. It returns the plain site name, or `null` if the object name does not belong to that conversation.

**Call relations**: It relies on the same hash rule as `site_object_name`, so parsing and creating names stay consistent. In this file it is a helper for converting object names back toward site names.

*Call graph*: calls 1 internal fn (site_object_name); 1 external calls (sha256).


##### `_named`  (lines 73–74)

```
def _named(sites: Iterable[HostedSite]) -> dict[str, HostedSite]
```

**Purpose**: Turns a collection of hosted-site records into a lookup table keyed by their workspace object names. This lets later code find a site by the name the object system uses.

**Data flow**: It receives many hosted-site records. For each record, it computes the object name from the site's conversation id and site name. It returns a dictionary where each object name points to its hosted-site record.

**Call relations**: This helper sits between the hosted-sites registry and the object system. `_member_rows` uses it to prepare listings, and `_find` uses it to locate one site by object name.

*Call graph*: calls 1 internal fn (site_object_name); called by 2 (_find, _member_rows).


##### `_workspace`  (lines 77–80)

```
def _workspace(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Checks that the site object code has an extension context, which is the request-scoped bundle of workspace information it needs. Without that context, the code cannot know which workspace store to read.

**Data flow**: It receives an optional extension context. If the context is missing, it raises an error. If present, it returns the context unchanged.

**Call relations**: Other helpers and methods call this before touching workspace-specific data. `_sites` uses it to build the hosted-sites registry, while `_member_rows` and `_status` use it when they need workspace settings such as the public base URL or workspace id.

*Call graph*: called by 3 (_member_rows, _status, _sites).


##### `_sites`  (lines 83–85)

```
def _sites(ext: ExtensionContext | None) -> HostedSites
```

**Purpose**: Opens the hosted-sites registry for the current workspace. The registry is the storage layer that knows which sites are deployed, their ports, owners, and visibility.

**Data flow**: It receives an optional extension context, confirms it is present through `_workspace`, then takes the workspace id and current transaction from that context. It returns a `HostedSites` object scoped to that workspace and transaction.

**Call relations**: This is the main doorway from object operations into site storage. Listing, conversation visibility checks, lookup, visibility changes, and unregistering all call `_sites` before reading or changing hosted-site records.

*Call graph*: calls 1 internal fn (_workspace); called by 5 (_apply_owned, _delete_owned, _find, _member_rows, member_conversation_rows); 1 external calls (__init__).


##### `_summary`  (lines 88–89)

```
def _summary(site: HostedSite) -> str
```

**Purpose**: Creates a short human-readable summary for a site row. It gives a quick glance at the site name, who can see it, and which sandbox port serves it.

**Data flow**: It receives one hosted-site record. It reads the site's name, visibility, and port, then returns them as a compact text string.

**Call relations**: `_member_rows` uses this when building list rows so the object listing has an easy-to-scan summary instead of only raw fields.

*Call graph*: called by 1 (_member_rows).


##### `SiteObjects._admin_can_apply`  (lines 104–105)

```
def _admin_can_apply(self, old: SiteSpec, spec: SiteSpec) -> bool
```

**Purpose**: Defines the one visibility change a workspace admin is allowed to make without being the site's creator. Admins may make a non-private site private, but they may not make a site more widely visible.

**Data flow**: It receives the old site specification and the requested new specification. It compares their visibility values and returns true only when the old value is not private and the new value is private.

**Call relations**: This method plugs into the broader object permission system supplied by the parent class. When an apply request comes from an admin rather than the owner, this rule decides whether the request is allowed before the actual update work proceeds.


##### `SiteObjects._member_rows`  (lines 107–146)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Builds the list of site objects a workspace member can see. Each row includes ownership, visibility, timestamps, URL information, and other fields that make the listing useful.

**Data flow**: It receives the extension context and the member id. It reads all hosted sites for the workspace, gives each one an object name, looks up owner emails, and adds fields such as conversation id, creation time, visibility, whether the site is mine, URL, and homepage-agent id when present. It returns object-list rows for the workspace object system.

**Call relations**: This is called by the generic object listing flow for member-readable objects. It pulls records through `_sites`, formats names through `_named`, creates summaries through `_summary`, adds owner information, and uses `site_url` when the workspace has a public base URL.

*Call graph*: calls 4 internal fn (_named, _sites, _summary, _workspace); 4 external calls (__init__, __init__, owner_emails, site_url).


##### `SiteObjects.member_conversation_rows`  (lines 148–165)

```
async def member_conversation_rows(self, ext: ExtensionContext | None, conversation_id: UUID, *, member_id: UUID, admin: bool, limit: int) -> tuple[ConversationObjectGrant, ...]
```

**Purpose**: Reports which site objects from a specific conversation are visible to a particular member. This helps conversation-scoped object discovery show the right deployed sites.

**Data flow**: It receives a conversation id, member id, admin flag, and limit. It asks the hosted-sites registry for sites in that conversation that this member may see, then turns each one into a conversation object grant containing the object name, generation number, and a flag saying the content is visible. It returns those grants.

**Call relations**: The conversation object system calls this when it needs objects connected to one conversation. The method delegates visibility filtering to the hosted-sites store and uses `site_object_name` so the grants use the same names as normal site listings.

*Call graph*: calls 2 internal fn (_sites, site_object_name); 1 external calls (__init__).


##### `SiteObjects._member_object`  (lines 167–188)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[SiteSpec] | None
```

**Purpose**: Builds the detailed object view for one site. It returns the site's editable specification and a link back to the conversation where the site was created.

**Data flow**: It receives an object name, owner information, and the requesting member id. It looks up the hosted site by name. If no site exists, it returns `null`; otherwise it returns details containing the site's visibility, creation and update times, and a `created_in` link to the conversation object.

**Call relations**: The object-get flow calls this after permissions have identified an object. It uses `_find` to translate the object name back to a hosted-site record, then packages the result in the standard object-detail shape expected by the rest of the SDK.

*Call graph*: calls 1 internal fn (_find); 4 external calls (__init__, __init__, __init__, __init__).


##### `SiteObjects._status`  (lines 190–206)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns live status information for one hosted site, especially the URL and sandbox port needed to open or diagnose it.

**Data flow**: It receives a tool context and object name. It finds the hosted site, and if found, builds a small dictionary containing the plain site name, port, creator member id, and full site URL. If the site is missing, it returns `null`.

**Call relations**: The object status flow calls this when a tool or user asks for operational details. It uses `_find` for the record, `_workspace` for the workspace id, and `site_url` to form the link people can open.

*Call graph*: calls 2 internal fn (_find, _workspace); 1 external calls (site_url).


##### `SiteObjects._apply_owned`  (lines 208–223)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SiteSpec, old: SiteSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: Changes a site's visibility after the object permission system has allowed the request. It deliberately refuses creation, because sites must be created by deployment, not by writing an object manifest.

**Data flow**: It receives the tool context, object name, requested site spec, previous spec, and owner. If there is no previous object or no owner, it raises a “verb not supported” error explaining that sites are deployed. It then finds the site, checks whether the requested visibility is different, and if so writes the new visibility to the hosted-sites registry. It returns nothing, but the stored visibility may change.

**Call relations**: The object apply flow calls this for allowed updates. It uses `_find` to protect against a site disappearing mid-change, and `_sites` to save the new visibility. Permission details, including creator-only changes and the admin-private exception, are handled around this method by the object framework and `_admin_can_apply`.

*Call graph*: calls 2 internal fn (_find, _sites); 1 external calls (__init__).


##### `SiteObjects._delete_owned`  (lines 225–229)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: Unhosts a site by unregistering it from the hosted-sites registry. After this, the permanent link no longer resolves through the site system.

**Data flow**: It receives the tool context, object name, and owner. It finds the hosted-site record; if it is already gone, it raises an error. Otherwise it unregisters that conversation/site-name pair from storage. It returns nothing, but the site record is removed.

**Call relations**: The object delete flow calls this once deletion has been authorized. It uses `_find` to translate the object name to the hosted-site record and `_sites` to perform the unregister operation.

*Call graph*: calls 2 internal fn (_find, _sites).


##### `SiteObjects._find`  (lines 231–232)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> HostedSite | None
```

**Purpose**: Looks up one hosted site by its workspace object name. It is the common helper used whenever a single object operation needs the underlying site record.

**Data flow**: It receives the extension context and object name. It reads all hosted sites for the workspace, builds the object-name lookup table with `_named`, and returns the matching hosted-site record if present. If no match exists, it returns `null`.

**Call relations**: Detailed reads, status checks, visibility updates, and deletes all call `_find` first. It connects the object-system name used by callers to the hosted-sites storage records used for the actual work.

*Call graph*: calls 2 internal fn (_named, _sites); called by 4 (_apply_owned, _delete_owned, _member_object, _status).

## 📊 State Registers Touched

- `reg-extension-catalog` — The shared list of installed extensions and the capabilities they registered for this deployment.
- `reg-db-session` — The active database connection, transaction, and workspace-safe persistence context used while work is running.
- `reg-identity-context` — The current answer to who is acting, in which workspace, and on behalf of which member or agent.
- `reg-auth-tokens` — The signed tickets and login tokens used to prove access to sessions, downloads, sandbox links, and hosted onboarding.
- `reg-conversation-state` — The durable conversation records, titles, audience, surface labels, sandbox links, and visible thread metadata.
- `reg-transcript-state` — The saved message history and transcript snapshots that are read, compacted, updated, audited, and shown later.
- `reg-turn-queue` — The durable queue of conversation turns, including admitted work, claimed work, failures, retries, and completion state.
- `reg-live-delivery` — The live stream and delivery state for partial replies, tool updates, terminal output, final status, and missed messages.
- `reg-object-store` — The durable named workspace objects owned by extensions, with their names, data, permissions, and owner routing.
- `reg-blob-artifacts` — The shared file and artifact storage for large bytes, generated files, previews, and signed downloads.
- `reg-portal-slots` — The safe display state for conversation panels such as sources, artifacts, tasks, sites, automations, and workspace changes.
- `reg-surface-ingress` — The shared records that connect external surfaces like web, Slack, shell, OAuth, and inbound messages to conversations and replies.
- `reg-source-feeds` — The registered external content sources, sync cursors, backoff state, ownership, grants, and wake-up triggers.
- `reg-page-index` — The stored pages, revisions, chunks, embeddings, and search indexes used to find synced knowledge later.
- `reg-memory-store` — The durable remembered facts and notes that agents can search, browse, update, consolidate, and show with provenance.
- `reg-scheduled-work` — The saved jobs, scheduled tasks, pauses, monitors, due times, retry state, and duplicate-run guards.
- `reg-workflow-plans` — The longer-running goals, objective steps, blockers, todos, delegated work, and progress evidence that survive across turns.
- `reg-subagent-delivery` — The parent-child turn links and pending result records used when helper agents run work and report back.
- `reg-visibility-policy` — The shared audience, sharing, governance, and permission rules that decide who may see or change private data.
- `reg-coding-review-state` — The coding extension’s durable review inbox and review-run records, including links to the agent, conversation, and turn that handle review automation.
- `reg-workspace-change-log` — Durable records of file/Git workspace changes attached to conversations so side effects can be recovered, summarized, and rendered safely in portal panels.
- `reg-hosted-site-state` — Durable hosted website records, publication metadata, permissions, and homepage-agent bindings used to build, serve, list, and remove sites.
- `reg-research-observations` — Durable per-conversation web/search source observations and retrieval metadata saved by research tools for later citation and Sources-panel rendering.
- `reg-skill-workflow-catalog` — The registered agent skills, helper subagent profiles, workflow profiles, and related prompt/activity metadata injected into turns and surfaced to users.
- `reg-listing-cursors` — Opaque pagination and browsing cursor state used to resume stable listings across objects, pages, memory, artifacts, usage records, and portal panels.
