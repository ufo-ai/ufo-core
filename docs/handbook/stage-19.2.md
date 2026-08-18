# Model, connector, source, and search provider contracts  `stage-19.2`

This stage is shared behind-the-scenes support. It does not run the system by itself. Instead, it defines the “plugs and sockets” that outside providers and extensions must use so they fit safely into UFO.

The core model interface describes the common shape for talking to AI providers: messages sent to a model, streamed answers coming back, tool requests, images, reasoning notes, and usage counts. This lets different model services behave like interchangeable parts.

The SDK files are public doorways for extension authors. The models SDK re-exports the model objects from one stable place. The auth proxy, connectors, and sources SDK files expose the pieces needed to add authenticated feed syncing, connector services, and external content sources. The index SDK exposes the allowed pieces for adding search indexes or embedding backends, which turn content into searchable form. The memory SDK provides the public types for memory retrieval. The search SDK gathers the public request, result, fetch, and provider types. Together, these files keep internal code private while giving outside integrations a clear, consistent contract.

## Files in this stage

### Model contracts
Shared model-provider shapes and their stable SDK export surface define how extensions interact with AI model clients.

### `core/src/ufo/models/interface.py`

`data_model` · `cross-cutting request and response handling`

This file is the contract between UFO and any AI model service it uses. Without it, each model provider would speak a slightly different dialect, and the rest of the system would need provider-specific code everywhere. Instead, this file gives the project one common set of message and event types.

It defines message pieces such as text, images, tool calls, tool results, and reasoning blocks. A reasoning block is extra model thinking that some providers require the client to send back unchanged in later turns, almost like keeping a stamped receipt so the conversation can continue correctly. It also defines ModelRequest, the full package sent to a model: the model name, system instructions, conversation messages, token budget, available tools, optional forced tool choice, and reasoning setting.

The ModelClient protocol is the promise every provider client must keep: given a ModelRequest, it streams back ModelEvent items such as text chunks, tool-call chunks, reasoning blocks, and usage information.

The file also protects requests that contain inline images. Providers have limits on how many images and how much image data can be sent. trim_images keeps the newest images, replaces older or oversized ones with a short placeholder, and preserves the surrounding conversation so the model still knows something was omitted.

#### Function details

##### `ModelRequest._forced_choice_names_an_offered_tool_with_reasoning_off`  (lines 149–156)

```
def _forced_choice_names_an_offered_tool_with_reasoning_off(self) -> 'ModelRequest'
```

**Purpose**: This checks that a request forcing the model to use one specific tool is valid. It prevents two bad cases: naming a tool that was not offered, or forcing a tool while reasoning is still enabled, which some providers reject.

**Data flow**: It reads the ModelRequest after its fields have been filled in. If no forced tool is requested, it leaves the request unchanged. If a forced tool is requested, it compares that name with the offered tool list and checks that reasoning is set to off. A valid request comes out unchanged; an invalid one becomes a clear validation error.

**Call relations**: This runs automatically as part of building or validating a ModelRequest. It acts as a gatekeeper before any ModelClient sees the request, so provider-specific clients do not have to rediscover these invalid combinations later.


##### `ModelClient.complete`  (lines 203–203)

```
def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This is the common promise that all model clients must follow. A client receives one complete model request and streams back model events, such as text, tool calls, reasoning records, and usage information.

**Data flow**: A ModelRequest goes in. The concrete provider client translates it to that provider's network format, receives the provider's response stream, and yields normalized ModelEvent objects. The protocol itself does not implement that work; it defines the shape every implementation must provide.

**Call relations**: Other parts of the system can call complete without caring whether the backing provider is Anthropic, OpenAI, OpenRouter, or another implementation. Provider-specific clients supply the real method behind this protocol.


##### `trim_images`  (lines 211–239)

```
def trim_images(messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This reduces a conversation's inline images until the request fits provider limits. It keeps the most recent images because they are usually most relevant, and replaces removed images with a short note instead of silently erasing them.

**Data flow**: It takes the tuple of conversation messages. First it asks _image_positions where all images are, including images nested inside tool results. It decides which images survive the per-message count limit, the whole-request count limit, and the whole-request byte limit. For images that must be removed, it asks _trim_message to rebuild the affected messages with placeholder text. It returns either the original messages if nothing changed, or a new tuple with oversized image content removed.

**Call relations**: This is used before model-specific clients translate messages for a provider. It coordinates the helper functions: _image_positions finds candidates, _image_data_len measures their base64 data size, and _trim_message performs the safe replacement.

*Call graph*: calls 3 internal fn (_image_data_len, _image_positions, _trim_message).


##### `_image_data_len`  (lines 242–253)

```
def _image_data_len(messages: tuple[Message, ...], position: tuple[int, int, int | None]) -> int
```

**Purpose**: This measures the stored size of one image at a known position in the message list. It is used to enforce the total image byte budget for a request.

**Data flow**: It receives the full messages tuple and a position pointing to either a top-level image block or an image inside a tool result. It follows that position, reads the image's base64 data string, and returns its length. If the position does not actually point to an image, it raises an error because the caller's bookkeeping is wrong.

**Call relations**: trim_images calls this while walking kept images from newest to oldest. The returned sizes decide how many images fit under the request-wide byte limit.

*Call graph*: called by 1 (trim_images).


##### `_image_positions`  (lines 256–276)

```
def _image_positions(messages: tuple[Message, ...]) -> list[tuple[int, int, int | None]]
```

**Purpose**: This finds every inline image in the conversation and records where it lives. It covers both normal image blocks and images embedded inside a tool result.

**Data flow**: It receives the full messages tuple. It skips plain string messages, scans structured message content block by block, and records each image as a small coordinate: message index, block index, and optionally the nested index inside a tool result. It returns these coordinates in oldest-to-newest order.

**Call relations**: trim_images calls this first to get the map of all images that might need trimming. The order it returns matters because trim_images keeps newer images when limits are exceeded.

*Call graph*: called by 1 (trim_images).


##### `_trim_message`  (lines 279–303)

```
def _trim_message(message_index: int, message: Message, drop: set[tuple[int, int, int | None]]) -> Message
```

**Purpose**: This rebuilds one message with selected images replaced by a standard placeholder. It keeps the message structure intact so the conversation remains understandable even after image data is removed.

**Data flow**: It receives one message, that message's index in the conversation, and the set of image positions that should be dropped. If the message is plain text, it returns it unchanged. If it has structured blocks, it walks through them and replaces dropped top-level images, or dropped nested tool-result images, with TextBlock placeholders saying the image was omitted. It returns a copied message with updated content.

**Call relations**: trim_images calls this for each message after deciding which image positions to remove. This helper performs the actual rewrite while trim_images owns the larger policy about limits and recency.

*Call graph*: called by 1 (trim_images); 2 external calls (__init__, model_copy).


### `core/src/ufo/sdk/models.py`

`other` · `import time / cross-cutting SDK use`

This file acts like a front desk for the project's model API. The real classes and helper functions live in deeper modules such as `ufo.models.interface`, `ufo.models.openai`, and `ufo.models.anthropic`. Rather than asking extension authors to know those internal paths, this file re-exports the approved pieces under `ufo.sdk.models`.

That matters because it creates a stable boundary between the public SDK and the project's private layout. If outside code imported directly from internal modules, a folder rename or internal cleanup could break extensions. With this file, the project can keep the public names steady while changing the implementation behind them.

The exported items cover the main building blocks for talking to language models: message and content block types, streaming event types, tool-use types, model client interfaces, OpenAI and Anthropic client adapters, model specification and pricing types, and usage records. It also exposes helpers such as `trim_images` and OpenAI-specific helpers.

There is no active logic here beyond import-time re-exporting. Its job is packaging and clarity: it says, “These are the model-related things extension code is allowed and expected to use.”


### Connector and source SDKs
Public SDK doorways expose authentication, connector, OAuth, and external source-sync contracts for extension authors.

### `core/src/ufo/sdk/authproxy.py`

`other` · `cross-cutting`

This file does not implement authentication itself. Instead, it re-exports a few names from internal modules so outside extensions have a clear, supported place to import them from. Think of it like a reception desk: the real offices are elsewhere, but visitors are told to come through this desk so the building can change internally without confusing them.

The main idea is an “auth proxy,” which is a plugin-style credential provider. A connector may need a credential, such as a token or account secret, to talk to an external service. An extension can declare an AuthProxySpec and provide an AuthProxy backend that knows how to turn a source’s account handle into a usable Credential.

The comments explain an important rule: if there is only one authentication backend, the system can choose it automatically. If there are several, configuration selects one. Sources marked with DIRECT_ACCOUNT use this selected backend directly. Sources connected through a broker get their credential through that broker instead.

Without this file, extension authors would need to import from internal connector modules directly. That would make the extension API more fragile and harder to understand.


### `core/src/ufo/sdk/connectors.py`

`other` · `cross-cutting; used when extension or core code imports the public connector SDK`

This file does not implement connector behavior itself. Instead, it acts like a clearly labeled front desk: outside extensions can come here to find the official names they need, without knowing where those names live deeper inside the project.

The problem it solves is API stability. Connectors involve several moving parts: an OAuth provider, which helps a user authorize access to an outside service; a connector broker, which knows what tools, files, searches, or catalog entries that service offers; and registry or resolver objects, which help core code find the right connector at runtime. Those concrete pieces live in internal modules such as `ufo.connectors` and `ufo.grants`. By re-exporting them here, the project gives extension developers one public place to import from: `ufo.sdk.connectors`.

That matters because it keeps extensions from depending directly on internal file layout. If the project later reorganizes its internal modules, this SDK file can keep the public names steady. Without it, every extension would need to know the project’s internal structure, and small refactors could break outside integrations.

In short, this file is a compatibility seam. It says, “these are the connector-related building blocks you are allowed to rely on,” while keeping the actual connector mechanics elsewhere.


### `core/src/ufo/sdk/sources.py`

`other` · `cross-cutting; used when extensions import the source SDK and during source sync setup`

This file does not define new behavior itself. Instead, it acts like a clearly labeled toolbox shelf for people writing UFO extensions. Without it, extension authors would need to know the project’s internal module layout and import source-sync pieces from many lower-level files. That would make extensions more fragile, because internal files can move or change.

The concepts exposed here are the pieces needed to turn an outside service, such as a REST API, into searchable UFO pages. An extension implements or wraps a `SourceBackend`, which is the part that fetches records from a provider and turns them into `Page` documents. The sync result tells core whether the run was a full snapshot, where missing pages should be removed, or an incremental update, where only explicitly named deletes are removed.

The file also exposes error signals with specific meanings. `CursorExpired` means a saved resume position no longer works, so core should retry fresh. `StreamSkipped` means the provider refused this stream for an expected reason, such as missing permissions, so the run is skipped rather than treated as a failure. `StreamFault` means the provider returned data in a shape the connector cannot understand.

For REST-based sources, it re-exports connector tools such as `RestConnector`, pagination helpers, stream definitions, and partition-walking utilities. In short, this file is the SDK’s friendly front door for source connectors.


### Indexing and retrieval SDKs
Stable SDK exports cover indexing backends, memory retrieval types, and general search provider APIs.

### `core/src/ufo/sdk/index.py`

`other` · `cross-cutting`

This file is a public doorway into the project’s indexing system. Instead of making extension authors import directly from the deeper internal module, it re-exports the important names from `ufo.indexing` under the SDK path. That matters because an SDK is like a front desk: outsiders should have a clear, stable place to ask for what they need, even if the offices behind the desk move around later.

The concepts exposed here are about storing and finding text. A `Chunk` is a piece of text small enough to index. A `Hit` is a search result. `IndexBackend` is the interface an extension implements when it wants to provide a storage and search engine for those chunks. `EmbedClient` is the interface for turning text into embeddings, which are numeric representations used for similarity search. `IndexScope` is used when deleting or limiting indexed content. The owner-kind constants identify what kind of thing a chunk came from, such as a memory item or a page.

There is no new logic in this file. Its job is boundary-setting: it says, “these are the indexing tools extensions may rely on.” Without it, extension code would need to depend on internal module paths, making future refactors more likely to break external integrations.


### `core/src/ufo/sdk/memory.py`

`data_model` · `cross-cutting`

This file exists to make the project’s memory-search feature easier and safer for outside provider extensions to use. Instead of asking extension code to reach into the internal `ufo.memory` module directly, it re-exports the important public names from one SDK location. An SDK, or software development kit, is the part of a project meant for other developers to build against.

The three exported names are the default memory search provider setting, the `MemoryMatch` type, and the `MemorySearchProvider` type. In plain terms, these describe: which provider is used by default, what a search result looks like, and what shape a memory-search provider must have.

This is like putting clearly labeled tools on a public workbench. The tools may be stored elsewhere in the workshop, but users are encouraged to pick them up from this predictable place. If this file were missing, extension authors might depend on deeper internal paths, making their code more likely to break if the internal layout changes.


### `core/src/ufo/sdk/search.py`

`data_model` · `cross-cutting`

This file is a small public doorway into the project's search system. Instead of asking outside code to import directly from the internal `ufo.search` module, it exposes the search pieces through `ufo.sdk.search`, which is the safer, intended path for extension writers and tools.

The problem it solves is stability. Internal modules can move or change as the project evolves, but an SDK path is a promise to outside users. Think of it like a building reception desk: visitors should go to the front desk, not wander into staff-only corridors looking for the right person.

The exported names describe the contract between the main system and a search backend. A `SearchProvider` is the component that answers searches. A `SearchQuery` is the question being asked. `SearchResults` and `SearchHit` describe the answers. If a provider supports fetching full pages, `FetchRequest` asks for a page and `FetchedPage` carries the returned content.

There is no extra behavior here. The file deliberately imports these names and republishes them under the SDK namespace. That keeps extension code clean and helps the project control what it treats as public API.
