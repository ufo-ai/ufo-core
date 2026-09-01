# Surface Replies and Conversation Display Slots  `stage-13.2`

This stage is about what the user sees after the assistant has done its work. It sits near the end of the main work loop, when finished replies and related information are sent back to places like the web app, Slack, iMessage, a terminal, or other connectors. It also prepares “conversation slots,” which are side panels or display areas that hold useful extras.

The shared slot model defines the safe shapes that extensions may show, such as artifacts, sources, tasks, sites, automations, image previews, todos, and reports. The research file saves web sources found during research, trims them to a safe size, and lets the conversation later show a durable Sources panel. The scheduled tasks file turns stored future actions into a small Automations summary. The sites file checks what hosted sites the viewer may see, then exposes safe public links and counts.

Slack has one extra detail: connector messages can include a small footer saying where they came from. The attribution code adds and recognizes that footer without mistaking it for a new user message.

## Files in this stage

### Extension Slot Providers
Extension-owned providers prepare durable conversation panels for research sources, scheduled automations, and hosted sites.

### `extensions/research/ufo_ext_research/observations.py`

`domain_logic` · `request handling and conversation display`

When the assistant searches the web or fetches a page, the useful source information should not disappear after that one step finishes. This file solves that by turning search results and fetched pages into saved “observations” tied to a workspace and conversation. Think of it like keeping a small, tidy bibliography for each conversation.

The file defines the database table `research_source_observation`, where each saved source has a URL, title, snippet, optional date, rank, and timestamps. URLs are identified by a SHA-256 digest, which is a fixed-length fingerprint of the URL, so the same source can be updated instead of duplicated.

Before saving, source text is trimmed to size limits and checked with `ConversationSource`, a validation model that makes sure the source has the shape the rest of the system expects. Bad source records are skipped rather than allowed to break storage.

The main save path is `record_sources`. It inserts new rows or updates existing ones, then deletes older extras so each conversation keeps at most `SOURCE_LIMIT` sources. Two helper entry points, `record_search_hits` and `record_fetched_page`, adapt different research results into the same saved format.

At the end, `SOURCES_SLOT` exposes this stored bibliography as a conversation slot named “Sources”. The UI or runtime can ask how many sources exist and read the actual list.

#### Function details

##### `_bounded`  (lines 53–54)

```
def _bounded(value: str, limit: int) -> str
```

**Purpose**: This small helper trims a piece of text so it does not exceed a chosen length. It is used before saving titles, snippets, and dates, which protects the database and the user interface from unexpectedly huge strings.

**Data flow**: It receives a string and a maximum character count. It takes the start of the string up to that limit and returns the shortened string. It does not change anything outside itself.

**Call relations**: `record_sources` calls `_bounded` while preparing source data for storage. In that larger flow, `_bounded` acts like a paper cutter: it makes each text field fit the allowed space before validation and database writing happen.

*Call graph*: called by 1 (record_sources).


##### `record_sources`  (lines 57–130)

```
async def record_sources(ext: ExtensionContext, conversation_id: UUID, turn_id: UUID, sources: tuple[RetrievedSource, ...]) -> None
```

**Purpose**: This is the main saving function for retrieved sources. It records sources for one conversation turn, updates existing entries for the same URL, and keeps only the newest ranked set so the source list stays useful and bounded.

**Data flow**: It receives the extension context, a conversation ID, a turn ID, and a tuple of `RetrievedSource` items. If there are no sources, it stops immediately. Otherwise it opens a database transaction, trims and validates each source, creates a stable URL fingerprint with `sha256`, and inserts or updates a row in `research_source_observation`. After saving, it queries the currently retained source digests and deletes anything beyond the configured source limit. The result is no returned value, but the database now contains the cleaned, current source list for that conversation.

**Call relations**: `record_search_hits` and `record_fetched_page` both call `record_sources` after converting their own result types into `RetrievedSource`. Inside its work, `record_sources` uses `ExtensionContext.transaction` to safely group database changes, `_bounded` to trim text, `ConversationSource` to validate source shape, `datetime.now` for timestamps, `sha256` for URL fingerprints, and SQLAlchemy `select` and `delete` statements to prune old rows.

*Call graph*: calls 2 internal fn (transaction, _bounded); called by 2 (record_fetched_page, record_search_hits); 5 external calls (__init__, now, sha256, delete, select).


##### `record_search_hits`  (lines 133–152)

```
async def record_search_hits(ext: ExtensionContext, conversation_id: UUID, turn_id: UUID, hits: tuple[SearchHit, ...]) -> None
```

**Purpose**: This function saves a batch of web search results as conversation sources. It is a translator from the search system’s `SearchHit` objects into the common saved-source format used by this file.

**Data flow**: It receives the extension context, conversation ID, turn ID, and a tuple of search hits. For each hit, it copies the URL, title, result text, and published date into a `RetrievedSource`. It then passes the complete tuple to `record_sources`, which performs validation and database storage. It returns nothing directly; its effect is that the search hits become saved sources.

**Call relations**: When the research extension has search results to preserve, it calls `record_search_hits`. This function does not write to the database itself; it prepares the data and hands it to `record_sources`, which does the shared persistence work.

*Call graph*: calls 1 internal fn (record_sources); 1 external calls (__init__).


##### `record_fetched_page`  (lines 155–173)

```
async def record_fetched_page(ext: ExtensionContext, conversation_id: UUID, turn_id: UUID, page: FetchedPage) -> None
```

**Purpose**: This function saves a single fetched web page as a conversation source. It is used when the system has opened or read a page directly, not just received a search result.

**Data flow**: It receives the extension context, conversation ID, turn ID, and a `FetchedPage`. It builds one `RetrievedSource` using the page URL as both URL and title, and using the page summary if available, otherwise the page text. It leaves the published date empty because a fetched page may not provide one. It then sends that one-item tuple to `record_sources`, which stores it. The function returns nothing, but the page becomes part of the saved source list.

**Call relations**: When a fetched page should be remembered, `record_fetched_page` adapts it into the same format used for search results. It then calls `record_sources`, so fetched pages and search hits follow the same validation, upsert, and pruning rules.

*Call graph*: calls 1 internal fn (record_sources); 1 external calls (__init__).


##### `_source_count`  (lines 176–186)

```
async def _source_count(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: This function counts how many saved sources a conversation currently has, up to the configured display limit. It supports the “Sources” conversation slot by providing a quick summary number.

**Data flow**: It receives a `ConversationSlotContext`, which includes the extension context and conversation ID. It opens a database transaction, counts rows in `research_source_observation` for the current workspace and conversation, and returns `None` if there are no saved sources. If sources exist, it returns the count, capped at `SOURCE_LIMIT`.

**Call relations**: `SOURCES_SLOT` uses `_source_count` as its summary function. When the conversation interface or runtime wants to know whether the Sources slot has content, this function performs the database count using SQLAlchemy `select` and gives back a simple number or `None`.

*Call graph*: 1 external calls (select).


##### `_read_sources`  (lines 189–217)

```
async def _read_sources(ctx: ConversationSlotContext) -> SourcesSlotPayload
```

**Purpose**: This function reads the saved source list for a conversation and packages it for the conversation slot system. It turns database rows back into validated source objects that can be displayed to the user.

**Data flow**: It receives a `ConversationSlotContext`. It opens a database transaction, selects source rows for the current workspace and conversation, orders newer observations first and then by rank, and reads one more than the display limit so it can detect truncation. It converts up to `SOURCE_LIMIT` rows into `ConversationSource` objects and wraps them in a `SourcesSlotPayload`. The returned payload contains the source list and a flag saying whether more rows existed than were included.

**Call relations**: `SOURCES_SLOT` uses `_read_sources` when something needs the full Sources content, not just the count. `_read_sources` queries the database with SQLAlchemy `select`, rebuilds each `ConversationSource`, and hands the final `SourcesSlotPayload` back to the slot system for display or downstream use.

*Call graph*: 3 external calls (__init__, __init__, select).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/conversation_slot.py`

`domain_logic` · `conversation display or slot reading`

A conversation may have scheduled tasks attached to it, such as reminders or recurring automations. This file is the bridge between the stored schedule data and the conversation view. Without it, the system could still store automations, but the conversation would not know how to list them, count them, or show their latest status.

The main idea is simple: the conversation context says which automation items are allowed to be seen, and this file fetches the matching scheduled tasks from the schedule store. It then checks that each task is still authorized by comparing the visible item’s generation number with the task’s current id. This is like checking that a visitor badge still matches the current guest list before letting someone into a room.

When building the visible payload, the file is careful about privacy and size. If an item’s content is not visible, it hides the description and latest response. It also cuts long descriptions, schedules, statuses, and responses down to fixed limits, and marks the result as truncated when anything was left out. Finally, it registers an `AUTOMATIONS_SLOT`, which tells the larger UFO system that there is a conversation slot called “Automations” with functions for summarizing and reading its content.

#### Function details

##### `_scheduler`  (lines 16–19)

```
def _scheduler(ctx: ConversationSlotContext) -> ScheduleStore
```

**Purpose**: This helper creates access to the scheduled-task storage for the current conversation slot request. It also makes sure the scheduled-tasks extension context is present, because the store cannot work without it.

**Data flow**: It receives a conversation slot context. If that context has no extension information, it stops with an error. Otherwise, it uses the extension context to create and return a `ScheduleStore`, which is the object used to read scheduled-task records.

**Call relations**: Both `_conversation` and `_read` call this when they need to talk to the schedule store. It is the small doorway from the conversation-slot code into the scheduled-task storage layer.

*Call graph*: called by 2 (_conversation, _read); 1 external calls (__init__).


##### `_conversation`  (lines 22–28)

```
async def _conversation(ctx: ConversationSlotContext) -> tuple[ScheduledTask, ...]
```

**Purpose**: This function fetches the scheduled tasks that belong to the current conversation and are named among the visible items. It asks for one more than the allowed maximum so the caller can tell whether there were too many to show fully.

**Data flow**: It reads the visible item names and the conversation id from the context. It opens the schedule store through `_scheduler`, asks the store for matching tasks, and returns the scheduled-task records it gets back.

**Call relations**: `_read` calls this as its first main fetch step. `_conversation` delegates storage access to `_scheduler`, then hands the resulting task list back to `_read` so it can filter, inspect, and format the tasks for display.

*Call graph*: calls 1 internal fn (_scheduler); called by 1 (_read).


##### `_read`  (lines 31–90)

```
async def _read(ctx: ConversationSlotContext) -> AutomationsSlotPayload
```

**Purpose**: This is the main reader for the Automations conversation slot. It builds the final payload that says which automations are visible, when they last or next run, whether they are paused, and whether any information had to be hidden or shortened.

**Data flow**: It receives the conversation slot context, opens the schedule store, and fetches matching tasks through `_conversation`. It compares each task with the visible items to keep only tasks that are still authorized. It then inspects those tasks for run details such as last run, next run, latest status, and latest response. For each automation, it applies privacy rules and length limits, creates a `ConversationAutomation` entry, tracks whether anything was omitted, and finally returns an `AutomationsSlotPayload` containing the entries and a truncation flag.

**Call relations**: The `AUTOMATIONS_SLOT` provider uses this as its `read` callback when the system needs the full Automations slot content. Inside that flow, `_read` calls `_scheduler` for storage access, `_conversation` for the matching task list, and then uses the manifest payload classes to hand a clean result back to the conversation system.

*Call graph*: calls 2 internal fn (_conversation, _scheduler); 2 external calls (__init__, __init__).


##### `_summarize`  (lines 93–95)

```
async def _summarize(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: This function gives a quick count for the Automations slot without reading every detail. It returns how many visible automation items can be summarized, capped at the maximum the conversation slot is allowed to show.

**Data flow**: It reads the number of visible items from the context, limits that number to the configured maximum, and returns the count. If there are no visible items, it returns `None` instead of zero, which lets the surrounding UI or system treat the slot as empty.

**Call relations**: The `AUTOMATIONS_SLOT` provider uses this as its `summarize` callback when the system only needs a lightweight summary. Unlike `_read`, it does not go to the schedule store; it relies only on what the conversation context already says is visible.


### `extensions/sites/ufo_ext_sites/conversation_slot.py`

`domain_logic` · `conversation rendering`

A conversation can have related hosted sites, but the system must not simply show every site it can find. This file is the gatekeeper for that conversation sidebar or slot. It checks the visible authorization items in the conversation, uses those to find matching site records in storage, and only returns sites whose name and generation still match the authorization. The “generation” is like a version number; it helps make sure an old permission does not accidentally unlock a newer or different site.

The main work happens in `_read`. It first builds a list of site names that the current conversation is allowed to expose. It then asks `HostedSites` for those sites from the workspace’s stored data. After that, it filters the returned rows again against the visible permissions, builds `ConversationSite` objects with public URLs, and caps the result at `CONVERSATION_SITES_MAX`. If more sites exist than can be shown, it marks the payload as truncated, meaning “there are more, but this slot is only showing the first set.”

The smaller `_summarize` function gives the interface a quick number to display without loading the full site details. Finally, `SITES_SLOT` registers all of this as a conversation slot named “Sites,” with a link icon and a known payload shape.

#### Function details

##### `_read`  (lines 13–46)

```
async def _read(ctx: ConversationSlotContext) -> SitesSlotPayload
```

**Purpose**: This function builds the full “Sites” payload for a conversation. It returns only the hosted sites that are both present in storage and currently authorized by visible conversation items.

**Data flow**: It receives a conversation slot context, which includes the conversation id, visible authorization items, workspace store, transaction, and public base URL. It converts visible item names into expected site names, asks `HostedSites` for matching stored site rows, keeps only rows whose authorization name and generation still match, turns each approved row into a `ConversationSite` with a public URL from `site_url`, and returns a `SitesSlotPayload`. The output contains up to the configured maximum number of sites and a flag saying whether extra authorized sites were left out.

**Call relations**: When the registered `SITES_SLOT` needs real content, it uses this function as its read callback. `_read` relies on `site_name_from_object` to understand authorization item names, `HostedSites` to fetch saved site records, `site_object_name` to compare records back to their authorization objects, and `site_url` to produce a link that a user can open. It then packages the result into `ConversationSite` and `SitesSlotPayload` objects for the conversation UI or API.

*Call graph*: 6 external calls (__init__, __init__, __init__, site_name_from_object, site_object_name, site_url).


##### `_summarize`  (lines 49–51)

```
async def _summarize(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: This function gives a quick count-style summary for the Sites slot. It is useful when the system wants to show that sites exist without loading all site details.

**Data flow**: It receives the conversation slot context and looks at the number of visible items in it. It caps that count at the same maximum used for displayed sites, then returns the count; if there are no visible items, it returns `None` so the slot can appear empty or be omitted.

**Call relations**: The registered `SITES_SLOT` uses this as its summarize callback. Unlike `_read`, it does not fetch from storage or build URLs; it only gives the lightweight number needed for a compact conversation summary.


### Slot Data Contracts
The runtime defines the typed conversation slot shapes and provider interfaces consumed by extension panels.

### `core/src/ufo/runtime/ext/conversation_slots.py`

`data_model` · `cross-cutting during extension registration and conversation slot reading`

A conversation can have side panels or portal areas that show useful extra information: files the conversation produced, web sources, a task list, connected sites, automations, or workspace changes. This file is the rulebook for what that information is allowed to look like before it is sent to the portal UI.

Most of the file is made of Pydantic models, which are Python data classes with built-in checking. They reject unexpected fields, enforce size limits, and make the objects frozen so they cannot be changed after creation. That matters because this data may be displayed in a browser. Bad or overly large data could break the interface, leak credentials, or create security risks.

The URL checks are especially important. Artifact, source, site, and image-preview links must be normal HTTP or HTTPS links and must not hide usernames or passwords inside the URL. Image preview URLs are checked even more strictly because they are drawn directly by the portal, like putting a picture frame on a web page: the frame must not contain a dangerous address.

The file also defines callback types for slot providers. A provider can summarize how much content exists and read the full payload when needed. In short, this file gives extensions a safe contract for adding conversation-scoped content to the user interface.

#### Function details

##### `ImagePreview.drawable_url`  (lines 52–69)

```
def drawable_url(cls, value: str) -> str
```

**Purpose**: This validator checks that an image preview URL is safe enough to be placed into the portal and drawn by a browser. It rejects links that are not HTTP or HTTPS, links without a real host, links with embedded credentials, fragments, backslashes, or hidden control characters.

**Data flow**: A URL string comes in while an ImagePreview is being created. The function breaks it into URL parts, decodes escaped characters, scans for unsafe characters, and either returns the original URL unchanged or raises an error explaining that the image preview URL is not acceptable.

**Call relations**: This is not usually called by application code directly. Pydantic calls it automatically when someone builds an ImagePreview. Inside the check, it relies on standard URL parsing, URL decoding, and Unicode character classification so it can catch both obvious and disguised unsafe URLs before the preview reaches the portal.

*Call graph*: 3 external calls (category, unquote, urlsplit).


##### `ConversationArtifact.http_url`  (lines 85–96)

```
def http_url(cls, value: str | None) -> str | None
```

**Purpose**: This validator checks the optional download or viewing URL for a conversation artifact, such as a generated file. It allows no URL at all, but if a URL is present it must be a normal HTTP or HTTPS address without embedded username or password information.

**Data flow**: The artifact URL value comes in as either a string or None. If it is None, it passes through unchanged. If it is a string, the function parses it, checks its scheme, host, and credentials, then returns the URL unchanged or raises an error if it is unsafe.

**Call relations**: Pydantic runs this when a ConversationArtifact is created. The artifact model uses the result as part of the slot payload that may later be shown in the conversation UI, so this function acts as a gate before a file link can be displayed.

*Call graph*: 1 external calls (urlsplit).


##### `ConversationSource.http_url`  (lines 117–126)

```
def http_url(cls, value: str) -> str
```

**Purpose**: This validator checks that a source citation URL is safe and usable. A source must point to an HTTP or HTTPS page with a host, and it must not contain embedded credentials.

**Data flow**: A source URL string comes in during ConversationSource creation. The function parses the URL, checks that it has an allowed web scheme, a hostname, and no username or password, then returns the original value or raises a validation error.

**Call relations**: Pydantic calls this automatically when source data is turned into a ConversationSource. That means source links are checked before they can appear in a sources slot, protecting the portal from malformed or credential-bearing citations.

*Call graph*: 1 external calls (urlsplit).


##### `TasksSlotPayload.consistent_progress`  (lines 155–169)

```
def consistent_progress(self) -> 'TasksSlotPayload'
```

**Purpose**: This validator makes sure the task-list summary numbers agree with the actual visible tasks. It prevents impossible states, such as saying 10 tasks are completed when only 5 tasks exist, or showing more visible tasks than the declared total.

**Data flow**: A fully built TasksSlotPayload comes in after its basic fields have been checked. The function compares total_count, completed_count, the number of visible tasks, each task’s status, and the truncated flag. If the numbers tell a consistent story, it returns the same payload; otherwise it raises an error.

**Call relations**: Pydantic runs this after creating a TasksSlotPayload. It does not hand off to other helper functions; it is the final consistency check before a task slot can be used by the rest of the conversation slot system.


##### `ConversationSite.http_url`  (lines 184–193)

```
def http_url(cls, value: str) -> str
```

**Purpose**: This validator checks that a connected site’s URL is a safe web address. The site must use HTTP or HTTPS, include a host, and must not hide credentials in the URL.

**Data flow**: A site URL string comes in while ConversationSite is being created. The function parses the string, checks the allowed pieces, and returns the original URL if valid. If the URL is missing a host, uses another scheme, or includes credentials, it raises an error.

**Call relations**: Pydantic calls this automatically for ConversationSite objects. Its result feeds into site slot payloads, so unsafe site links are rejected before they can be included in the conversation’s portal content.

*Call graph*: 1 external calls (urlsplit).


### Slack Attribution Footer
Slack connector replies gain a safe attribution footer that identifies the bot without being mistaken for user intent.

### `extensions/slack/ufo_ext_slack/attribution.py`

`domain_logic` · `Slack connector sending and Slack event intake`

When this system sends a Slack message through a connector, the message is not rendered by the normal Slack surface code. That means the connector needs its own way to mark, “this came from UFO.” This file supplies that mark as a Slack mention of the bot user, so a reader can click or recognize the agent behind the message.

The tricky part is that a Slack mention is also how people address the bot. If the system adds a footer like “via <@bot>,” Slack may later report the message as mentioning the app. Without this file’s checks, the system could mistake its own footer for a user asking the bot something, or hide the wrong message from a digest. Think of it like putting a return address on a letter: useful, but it should not be confused with the letter’s main message.

The file has two halves. For outgoing connector calls, it decides whether the call is really a Slack message send and, if so, adds an attribution footer unless one is already present. For incoming Slack events, it gathers all the possible text areas where a mention might appear, including nested Slack blocks, and checks whether the bot was mentioned after removing any attribution footer the system itself wrote.

#### Function details

##### `is_slack_send`  (lines 36–45)

```
def is_slack_send(provider: str, slug: str) -> bool
```

**Purpose**: This function answers one narrow question: does this connector call look like it sends a Slack message? It keeps the Slack extension aligned with the connector tool’s own rule for deciding which calls should receive attribution.

**Data flow**: It takes a connector provider name and an action slug. It lowercases the slug, then checks that the provider is Slack, the slug refers to a message, and the slug contains one of the known send-related words. It returns true when all of those signs match, otherwise false.

**Call relations**: This is the gatekeeper used before adding Slack-specific attribution. It mirrors the connector attribution test so the Slack extension and the connector tool do not disagree about which outgoing calls should carry a footer.


##### `mention_attributed`  (lines 48–55)

```
def mention_attributed(arguments: dict[str, JsonValue], bot_user_id: str) -> dict[str, JsonValue]
```

**Purpose**: This function adds the attribution footer that mentions the workspace’s bot user. It is used when sending a Slack message through a connector, so the sent message visibly points back to the agent.

**Data flow**: It receives the connector arguments for a Slack send and the bot user ID. It builds the footer subject by placing that bot user ID into the shared attribution mention template, then passes the arguments and subject to the connector attribution helper. The result is a new or updated argument dictionary with the footer appended, unless the message was already attributed.

**Call relations**: After code has decided a connector call is a Slack send, this function supplies the Slack-specific subject for the generic connector attribution machinery. It hands off to `UFO_ATTRIBUTION_MENTION_SUBJECT.format` to create the mention text, then to `attributed_arguments` to shape the footer the same way the connector tool expects.

*Call graph*: 2 external calls (format, attributed_arguments).


##### `addressing_mention`  (lines 58–68)

```
def addressing_mention(text: str, bot_user_id: str) -> bool
```

**Purpose**: This function checks whether a piece of text truly mentions the bot, ignoring attribution footers written by this system. It prevents the bot from treating its own “via <bot>” footer as if a person had addressed it.

**Data flow**: It receives some text and a bot user ID. Before looking for the Slack mention form, it removes known attribution text from the input. It then returns true only if the cleaned text still contains the bot mention.

**Call relations**: This function is part of the inbound decision about whether a Slack message is addressed to the agent. It relies on `attribution_stripped` to remove this system’s own footer first, so downstream address detection sees the real message body rather than the label added during sending.

*Call graph*: 1 external calls (attribution_stripped).


##### `message_bodies`  (lines 71–77)

```
def message_bodies(event: Mapping[str, object]) -> tuple[str, ...]
```

**Purpose**: This function pulls out every text string in a Slack message event where a bot mention might be hiding. It checks not only the top-level message text but also nested Slack blocks, because Slack messages can store visible text in several places.

**Data flow**: It receives a Slack event-like mapping. It reads the normal `text` field, defaulting to an empty string if it is missing, then reads the message `blocks` field and asks `_nested_strings` to find every string inside it. It returns all of those strings as one tuple.

**Call relations**: Inbound Slack logic can use this function before checking for mentions. It delegates the recursive searching of nested block data to `_nested_strings`, then gives callers a simple list-like result: all possible message body strings to examine.

*Call graph*: calls 1 internal fn (_nested_strings).


##### `_nested_strings`  (lines 80–89)

```
def _nested_strings(value: object) -> Iterator[str]
```

**Purpose**: This helper walks through nested Slack block data and yields every string it can find. It exists because Slack block content can be layered like boxes inside boxes: lists contain dictionaries, dictionaries contain lists, and the text may be deep inside.

**Data flow**: It receives any value. If the value is a string, it yields that string. If it is a mapping, it searches all values inside it. If it is a list, it searches each item. Other kinds of values are ignored.

**Call relations**: This is the low-level scanner used by `message_bodies`. `message_bodies` gives it the Slack blocks from an event, and `_nested_strings` returns the raw strings that higher-level mention checks can inspect.

*Call graph*: called by 1 (message_bodies).
