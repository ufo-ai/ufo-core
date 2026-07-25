# Public SDK, protocol types, and extension contracts  `stage-16` (cross-cutting infrastructure)

This stage is shared behind-the-scenes support. It defines the public “contracts” that let extensions, connectors, models, browsers, search services, and user-facing screens work with UFO without depending on private internals. Think of it as the set of plugs, sockets, and label formats that let replaceable parts fit together.

The extension declaration and handler SDK contracts describe what an extension can offer, such as tools, routes, jobs, credentials, skills, and lifecycle hooks, plus the safe context and HTTP helpers its code may use. The backend and connector SDK doorways expose stable entry points for auth proxies, browsers, sources, search indexes, models, sandboxes, and connectors. The platform service import shims provide approved public paths to services like accounting, grants, memory, logging, seats, and operator authentication. Extension-specific protocol shapes define exact request and event formats for browser actions, browser-related errors, and memory events.

The directly assigned files add core shared shapes: browser connection requests, AI model messages and streams, turn status and result records, web search requests, and memory ownership labels. Together, these contracts keep the system flexible while making communication predictable.

## Sub-stages

- [Extension declaration and handler SDK contracts](stage-16.1.md) `stage-16.1` — 9 files
- [SDK backend and connector integration doorways](stage-16.2.md) `stage-16.2` — 8 files
- [SDK platform service import shims](stage-16.3.md) `stage-16.3` — 9 files
- [Extension-specific protocol shapes](stage-16.4.md) `stage-16.4` — 3 files

## Files in this stage

### Turn and model contracts
Shared public types for browser access, model-provider communication, and user-turn lifecycle records.

### `core/src/ufo/browser.py`

`data_model` · `cross-cutting; used when a turn needs a browser and again at turn end or recovery`

This file is a boundary line between the core system and any real browser service. The core needs a way to talk to Chrome, but it should not care where Chrome lives or how it was started. To keep that separation clean, this file defines simple shared shapes: a CDP endpoint, a lease on that endpoint, and a provider that creates or reconnects leases. CDP means Chrome DevTools Protocol, the control channel tools use to drive Chrome pages.

The everyday analogy is renting a meeting room. The provider is the front desk. A lease is your temporary claim on a room. The endpoint is the room address plus any badge or key needed to enter. At the end, you return the room unless it was a permanent shared room.

A browser extension supplies the real provider. One provider might point to Chrome running inside a sandbox for the current conversation. Another might create a remote hosted browser session. The core only receives a `CdpLease`, asks it for an endpoint, may save its reattach token, and closes it when the turn is over. If a saved token points to a browser session that no longer exists, `SessionGone` tells the caller to start fresh instead of pretending the old page is still there.

#### Function details

##### `CdpLease.endpoint`  (lines 45–45)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: This method gives the browser-driving code the actual Chrome connection details for the current turn. It returns the URL to connect to, plus any headers such as authorization or sandbox routing information.

**Data flow**: It starts with a lease that already represents a claim on some browser session. The method resolves that claim into a `CdpEndpoint`, which contains a URL and optional connection headers. The caller receives those details and uses them to connect to Chrome.

**Call relations**: A concrete `CdpLease` is created by `CdpProvider.lease` or `CdpProvider.reattach`. After that, the browser extension asks `endpoint` for the address it should connect to before it starts driving the page.


##### `CdpLease.token`  (lines 47–47)

```
async def token(self) -> str
```

**Purpose**: This method returns a stable text handle that can be saved and used later to reconnect to the same browser session if it is still alive. For a hosted browser this might be a session ID; for a fixed endpoint it might simply be the URL.

**Data flow**: It starts with the current lease and reads whatever durable identifier the provider assigned to that browser session. It turns that into a string. The caller can store that string outside the lease and later pass it back to reconnect.

**Call relations**: After a lease has been created by `CdpProvider.lease`, the browser extension can call `token` so recovery code has something to remember. That saved value is later handed to `CdpProvider.reattach`, which tries to rebuild a usable lease from it.


##### `CdpLease.aclose`  (lines 49–49)

```
async def aclose(self) -> None
```

**Purpose**: This method releases the browser claim when the turn is finished. For some providers it may shut down or release a remote hosted session; for a static local endpoint it may do nothing.

**Data flow**: It starts with an active lease. The method performs whatever cleanup that provider requires, such as telling a remote service the session is no longer needed. It returns no value, but after it runs the caller should treat the lease as finished.

**Call relations**: A caller receives a lease from `CdpProvider.lease` or `CdpProvider.reattach` and uses it during the turn. At turn end, the caller calls `aclose` so the provider can clean up resources instead of leaving browser sessions hanging around.


##### `CdpProvider.lease`  (lines 62–62)

```
async def lease(self, sandbox: SandboxSession | None=None) -> CdpLease
```

**Purpose**: This method creates a fresh browser lease for a turn. It may use the current sandbox session if the browser lives inside that sandbox, or ignore it if the browser is remote or fixed.

**Data flow**: It receives an optional `SandboxSession`, which represents the isolated environment for the current work. The provider uses that information, if relevant, to find or create a Chrome connection. It returns a new `CdpLease` that the caller can use to get the endpoint, save a token, and eventually close.

**Call relations**: This is the normal starting point when a turn needs a browser. The core or browser extension asks the selected provider for a lease, then uses the returned lease through `CdpLease.endpoint`, `CdpLease.token`, and `CdpLease.aclose`.


##### `CdpProvider.reattach`  (lines 64–64)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: This method tries to reconnect to a browser session that was created earlier. It is used when a saved token exists and the system wants to continue with the same browser instead of starting over.

**Data flow**: It receives a saved token string. The provider looks up or resolves the browser session named by that token. If the session is still alive, it returns a fresh `CdpLease` for it; if the session is gone, it raises `SessionGone` so the caller knows to create a new lease instead.

**Call relations**: This is the recovery path that pairs with `CdpLease.token`. A previous run saves the token, and a later run gives it to `reattach`. If reattachment succeeds, the returned lease is used like any other lease; if it fails with `SessionGone`, the caller falls back to `CdpProvider.lease`.


### `core/src/ufo/models/interface.py`

`data_model` · `cross-cutting; used when building and sending model requests`

This file is the contract between the rest of the application and any AI model service. It defines small data shapes, using Pydantic models, for messages, text, images, tool calls, tool results, usage records, and full model requests. Pydantic is a library that checks and organizes data, a bit like a form that refuses invalid answers before they travel further through the system.

The file also defines ModelClient, a protocol. A protocol is a promise: any real model client must provide a complete method that takes a ModelRequest and streams back model events, such as text appearing piece by piece, a tool call starting, tool call input arriving, or token usage information.

One important bit of protection here is image trimming. Model providers limit how many inline images can be sent, and how large the request can be. The trim_images function keeps the newest images, drops older or oversized ones, and replaces removed images with a short text note. That way the conversation stays valid and the model knows something was omitted instead of receiving a broken or silently changed message.

Without this file, different parts of the project could disagree about the shape of model messages, forced tool calls could be sent in invalid ways, and image-heavy requests could fail at the provider boundary.

#### Function details

##### `ModelRequest._forced_choice_names_an_offered_tool_with_reasoning_off`  (lines 87–94)

```
def _forced_choice_names_an_offered_tool_with_reasoning_off(self) -> 'ModelRequest'
```

**Purpose**: This checks that a request forcing the model to use a specific tool is valid. The chosen tool must actually be one of the tools offered in the request, and extra model reasoning must be turned off because at least one provider rejects forced tool choice with extended reasoning enabled.

**Data flow**: It reads the ModelRequest after its fields have been filled in. If there is no forced tool choice, it leaves the request unchanged. If there is a forced choice, it compares that name with the offered tools and checks the reasoning setting; valid requests pass through, while invalid ones raise a clear error before they can be sent to a provider.

**Call relations**: This validator runs as part of creating or validating a ModelRequest. It acts as a gatekeeper before any ModelClient implementation receives the request, preventing impossible or provider-rejected combinations from reaching the model service.


##### `ModelClient.complete`  (lines 127–127)

```
def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This is the shared method every model client must provide. It takes one complete model request and returns an asynchronous stream of events, meaning the caller can receive pieces of the model's answer as they arrive instead of waiting for everything at once.

**Data flow**: A ModelRequest goes in, containing the model name, system prompt, conversation messages, token budget, tools, and reasoning settings. A stream comes out, yielding events such as text fragments, tool-call starts, tool-call JSON fragments, and usage information. The protocol itself does not change data; it defines what real clients must do.

**Call relations**: Other code can call complete on anything that follows the ModelClient protocol without knowing which provider is underneath. Provider-specific clients implement this method and translate the common request and event shapes into that provider's own network format.


##### `trim_images`  (lines 137–165)

```
def trim_images(messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This prepares a conversation for model providers by removing inline images that would exceed provider limits. It keeps the newest images first, because recent screenshots or visual results are usually most relevant to the current turn.

**Data flow**: It receives a tuple of Message objects. It first asks _image_positions where all images are, then decides which images survive the per-message count limit, the whole-request count limit, and the total image byte budget. If nothing must be removed, it returns the original messages; otherwise it calls _trim_message for each message and returns a new tuple where dropped images have been replaced by a short placeholder text.

**Call relations**: This is the public helper in this file for request cleanup. Before a provider client translates messages into Anthropic, OpenAI, or another service's format, this function can be used to make the canonical message list fit the strictest known image limits. It relies on _image_positions to find images, _image_data_len to measure them, and _trim_message to rewrite affected messages.

*Call graph*: calls 3 internal fn (_image_data_len, _image_positions, _trim_message).


##### `_image_data_len`  (lines 168–179)

```
def _image_data_len(messages: tuple[Message, ...], position: tuple[int, int, int | None]) -> int
```

**Purpose**: This finds the size of one specific inline image's base64 data. Base64 is the text encoding commonly used to carry binary image data inside JSON requests.

**Data flow**: It receives the full message list and a position that points to one image. That position says which message, which content block, and, for tool results, which nested part contains the image. The function follows that address, reads the image data string, and returns its length; if the address does not actually point to an image, it raises an error.

**Call relations**: trim_images calls this while spending the total image byte budget. It uses the returned lengths to keep the newest images that fit and stop once adding another image would exceed the provider's request-size limit.

*Call graph*: called by 1 (trim_images).


##### `_image_positions`  (lines 182–202)

```
def _image_positions(messages: tuple[Message, ...]) -> list[tuple[int, int, int | None]]
```

**Purpose**: This scans the conversation and records where every inline image lives. It includes both ordinary image blocks and images nested inside tool results, such as a browser screenshot returned by a tool.

**Data flow**: It receives the tuple of messages. It skips plain string messages, walks through structured content blocks, and builds a list of image addresses in oldest-to-newest order. Each address identifies the message, the block inside that message, and, if needed, the nested index inside a tool result.

**Call relations**: trim_images calls this first, because it cannot decide what to keep or drop until it knows where all images are. The oldest-to-newest ordering is important because trim_images later keeps images from the tail of this list, favoring the most recent context.

*Call graph*: called by 1 (trim_images).


##### `_trim_message`  (lines 205–229)

```
def _trim_message(message_index: int, message: Message, drop: set[tuple[int, int, int | None]]) -> Message
```

**Purpose**: This rewrites one message by replacing selected images with a clear placeholder text. It preserves the rest of the message so the conversation remains readable and valid.

**Data flow**: It receives a message index, a Message object, and a set of image positions that should be dropped. If the message is plain text, it returns it unchanged. If the message has structured blocks, it walks through them, swaps any dropped top-level image for a TextBlock saying the image was omitted, and does the same for dropped images nested inside tool results. It returns a copied Message with updated content rather than mutating the original object.

**Call relations**: trim_images calls this after deciding the final drop set. This function performs the actual rewrite: it creates placeholder TextBlock objects for omitted images and uses Message.model_copy to produce updated message objects safely.

*Call graph*: called by 1 (trim_images); 2 external calls (__init__, model_copy).


### `core/src/ufo/schema/records.py`

`data_model` · `cross-cutting: used when admitting turns, queueing work, running workers, and recording final results`

This file is like the printed form that every part of the system agrees to use when a message enters the system. A “turn” is one unit of conversation work: someone says something, the system queues it, a worker runs it, and the turn eventually finishes, fails, or is cancelled. Without these shared records, the Slack or web side, the queue, the worker, and the billing code could all disagree about what a turn looks like.

The file defines fixed status words, names for queues and workflows, and small data records using Pydantic, a library that checks data when objects are created. It also defines deterministic ID helpers. “Deterministic” means the same workspace, conversation, and sequence number always produce the same identifier, which is important when work may be retried: a replay should point to the same turn, not create a duplicate.

Several records describe special final outcomes. A turn can end with plain text, an error, a question for the user, a private credential request, or an account connection request. The `Turn` model ties everything together and enforces an important rule: unfinished turns must not have a terminal result, and finished turns must have one that matches their status. The validators also clean unsafe sender text, reject unknown time zones early, and make timestamps safe to compare as UTC times.

#### Function details

##### `turn_id_for`  (lines 42–44)

```
def turn_id_for(workspace_id: UUID, conversation_id: UUID, seq: int) -> UUID
```

**Purpose**: Creates the unique ID for a conversation turn from the workspace, conversation, and turn number. Someone would use it when they need retries or replays to refer to the same turn instead of accidentally making a new one.

**Data flow**: It takes a workspace ID, a conversation ID, and a sequence number. It combines them into one stable text path and feeds that into UUID version 5, which makes the same UUID every time for the same input. The result is returned as the turn ID.

**Call relations**: This is a small identity helper used by code that admits or reconstructs turns. It hands off to the standard `uuid5` function to do the actual stable UUID creation, so the rest of the system can treat the returned value as the one true ID for that turn.

*Call graph*: 1 external calls (uuid5).


##### `ledger_id_for`  (lines 47–52)

```
def ledger_id_for(workspace_id: UUID, turn_id: UUID, dimension: str, attempt: str='') -> UUID
```

**Purpose**: Creates a stable ID for one billing ledger entry connected to a turn. It prevents the same run attempt from being billed twice while still allowing a resumed attempt to be recorded separately.

**Data flow**: It takes the workspace ID, turn ID, billing dimension, and an optional attempt ID. It joins those pieces into a stable text key and turns that key into a UUID. The returned UUID identifies exactly one billing write for that turn, dimension, and attempt.

**Call relations**: Billing-related code can call this before writing usage records. Like `turn_id_for`, it relies on `uuid5` for stable UUID creation, which makes replayed work collapse onto the same ledger row instead of producing duplicates.

*Call graph*: 1 external calls (uuid5).


##### `TurnContext._tag_safe_line`  (lines 163–167)

```
def _tag_safe_line(cls, value: str | None) -> str | None
```

**Purpose**: Cleans the reported sender name so it can be safely placed inside structured context text. This matters because sender names may come from outside systems and should not be able to fake markup-like tags.

**Data flow**: It receives an optional sender string. If there is no sender, it leaves it as `None`. Otherwise it removes angle brackets, collapses whitespace into a single line, and returns either the cleaned sender or `None` if nothing useful remains.

**Call relations**: Pydantic calls this automatically when a `TurnContext` is built or validated. It runs before the context is later rendered for the engine, so downstream code can trust that the sender is a simple safe line rather than raw outside text.


##### `TurnContext._known_zone`  (lines 171–178)

```
def _known_zone(cls, value: str | None) -> str | None
```

**Purpose**: Checks that a provided time zone name is real. This catches bad time zone data at the edge of the system instead of letting it cause confusing failures later during a turn.

**Data flow**: It receives an optional time zone string. If the value is missing, it passes it through. If present, it asks the system time zone database to load that zone; success returns the original string, while failure becomes a clear validation error saying the time zone is unknown.

**Call relations**: Pydantic calls this automatically for `TurnContext.timezone`. It hands the lookup to `ZoneInfo`, the standard time zone tool, and either lets a valid context continue into the turn record or stops invalid input early.

*Call graph*: 1 external calls (ZoneInfo).


##### `Turn._aware_utc`  (lines 202–207)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Ensures turn timestamps are treated as UTC times even if a database driver returns them without time zone information. This avoids accidentally interpreting stored UTC times as the server’s local time.

**Data flow**: It receives either a timestamp or `None`. If there is no timestamp, it returns `None`. If the timestamp already has time zone information, it returns it unchanged. If it is missing that marker, it adds the UTC marker and returns the corrected timestamp.

**Call relations**: Pydantic calls this automatically for `Turn.created_at` and `Turn.updated_at`. It uses `datetime.replace` only to attach the UTC marker when needed, so later code that compares or displays turn times sees consistent UTC-aware values.

*Call graph*: 1 external calls (replace).


##### `Turn._terminal_matches_status`  (lines 210–215)

```
def _terminal_matches_status(self) -> 'Turn'
```

**Purpose**: Enforces the rule that a turn’s final-result record must match its status. Unfinished turns should not carry final output, and finished turns must carry final output with the same status.

**Data flow**: It receives the fully built `Turn` object after field validation. It checks whether the status is still active, such as queued or running, and compares that with whether a terminal frame is present. It also checks that the terminal frame’s own status matches the turn status. If the rules hold, it returns the turn; otherwise it raises a validation error.

**Call relations**: Pydantic runs this after constructing a `Turn`. It protects all later code that reads turns from contradictory states, such as a running turn with a final answer or a failed turn whose terminal frame says it was done.


### Search and ownership contracts
Small shared vocabularies for web search operations and memory subject ownership.

### `core/src/ufo/search.py`

`data_model` · `startup and request handling`

This file is a boundary, or “seam,” between the core system and whatever web search provider has been plugged in. The core does not include its own search engine and does not keep provider API keys. Instead, an extension supplies a SearchProvider at startup, and the rest of the system talks to it through the simple shapes defined here.

The file defines small frozen data objects, meaning their contents are meant to be created once and not changed afterward. SearchQuery describes what someone wants to search for: the words, result count, optional time window, allowed domains, and optional category. SearchResults is what comes back: a ranked set of SearchHit items, plus sometimes a direct answer. FetchRequest describes a request to read one web page, and FetchedPage is the extracted text and optional summary that comes back.

The SearchProvider protocol is the key piece. A protocol is like a checklist: any backend can be used if it provides the expected property and methods. Some providers can fetch full pages and some cannot, so supports_fetch tells callers whether fetch is safe to use. If code tries to fetch from a provider that does not support it, SearchUnsupported is the loud failure used to signal that mistake. This keeps search flexible while keeping secrets and provider-specific details outside the core.

#### Function details

##### `SearchProvider.supports_fetch`  (lines 87–87)

```
def supports_fetch(self) -> bool
```

**Purpose**: This property tells callers whether the selected search provider can fetch and extract the contents of a specific web page. It exists so tools can avoid asking for page text from a provider that only supports search results.

**Data flow**: A caller reads this property from the active provider. The provider returns a true-or-false answer. Nothing is changed; the answer simply guides whether a later fetch request should be attempted.

**Call relations**: In the wider flow, research tools check this before calling fetch. If it is false, the tool can stop or hide the fetch option instead of reaching a provider method that is expected to fail.


##### `SearchProvider.search`  (lines 89–89)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: This asynchronous method runs a web search for a SearchQuery and returns SearchResults. “Asynchronous” means the program can wait for the outside search service without blocking other work.

**Data flow**: A caller gives it a SearchQuery containing the search text and options such as result count or allowed domains. The provider sends that request to its own backend and turns the response into SearchResults: search hits and possibly a direct answer. The result comes back to the caller; the core does not need to know provider-specific details.

**Call relations**: During a turn, research tools use the selected provider through the tool context and call this method when they need web results. The actual backend implementation does the outside API work, while this protocol defines the shape that all backends must follow.


##### `SearchProvider.fetch`  (lines 91–91)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: This asynchronous method fetches and extracts text from one URL when the provider supports page fetching. It returns a FetchedPage with the page text and sometimes a summary.

**Data flow**: A caller gives it a FetchRequest containing the URL and optional instructions such as a prompt, maximum text length, or whether to bypass cache. The provider retrieves and extracts the page, then returns a FetchedPage. If a provider does not support fetching, the contract says it should raise SearchUnsupported.

**Call relations**: This is normally called only after supports_fetch has been checked. The research fetch_url tool is expected to guard that path, so SearchUnsupported is mainly a fail-loud safety net for callers that skip the check.


### `core/src/ufo/subjects.py`

`data_model` · `cross-cutting`

This file solves a simple but important coordination problem: different parts of the system need to agree on the names used for memory visibility. Some memories are shared by the whole conversation. Others are private to one member. If each part of the code invented its own wording, one part might store a memory under one name while another part looks for it under a different name.

The file defines two building blocks. `SHARED_SUBJECT` is the plain label for memory that belongs to the shared conversation space. `MEMBER_SUBJECT_PREFIX` is the prefix used for member-specific memory. The helper function `member_subject` then turns a member's UUID, which is a unique identifier, into the standard text form for that member's private subject.

An everyday analogy is labeling folders in a filing cabinet. There is one folder called “shared” that everyone can use, and there are private folders labeled like “member:123...”. This file makes sure every part of the system writes those folder labels the same way.

#### Function details

##### `member_subject`  (lines 15–16)

```
def member_subject(member_id: UUID) -> str
```

**Purpose**: This function builds the standard subject name for one member's private memory space. Someone uses it when they have a member's unique ID and need the exact label under which that member's memories should be stored or recalled.

**Data flow**: It receives a `member_id`, which is a UUID, meaning a globally unique identifier. It converts that ID into text and places it after the fixed `member:` prefix. The result is a string such as `member:<id>`, and the function does not change any outside state.

**Call relations**: This is the small shared helper for any code that needs to name a member-private subject. It does not call other project functions; it simply applies the shared naming rule so other parts of the system can store and look up memory using the same label.

## 📊 State Registers Touched

- `reg-capability-registry` — The loaded menu of extension-provided routes, tools, skills, hooks, jobs, credentials, models, and search backends.
- `reg-tool-catalog` — The shared list of tools the agent may call, including their names, inputs, safety labels, and handlers.
- `reg-model-catalog` — The model switchboard that maps model names to providers, credentials, request formats, and prices.
- `reg-connector-catalog` — The shared directory of external service connectors and broker-backed provider access.
- `reg-search-index-state` — The searchable content indexes, chunks, embeddings, and search backend choices used for recall and source replay.
- `reg-browser-session` — The browser connection state used when a turn needs a Chrome endpoint or computer-use actions.
- `reg-memory-store` — The workspace memory facts, episodes, ownership labels, confidence, and consolidation indexes used for recall.
- `reg-knowledge-graph` — The stored people, companies, things, and relationships used as structured background knowledge.
- `reg-workspace-object-catalog` — The named workspace objects and object-type registry used to list, inspect, validate, change, or delete stored things.
- `reg-background-job-registry` — The registered set of built-in and extension background workflows that the scheduler can run.
