# Cross-cutting public SDK, extension API, and protocol types  `stage-17` (cross-cutting infrastructure)

This stage is shared support that sits around the whole system, not one single startup or work-loop step. It defines the stable public doors that extensions, surfaces, and integrations are supposed to use. The SDK façade modules re-export approved types for context, logging, HTTP, accounting, seats, objects, and other common areas, so outside code does not depend on fragile internal paths. The extension declaration pieces define manifests, which are fixed descriptions of what an extension offers, and include a sample extension that proves those public hooks work together. The provider, data, model, and security façades expose safe entry points for credentials, grants, connectors, search, memory, indexing, and model access.

The directly assigned files provide the shared “shapes” behind those doors. connectors.py defines how outside services authenticate and run actions without leaking secrets. memory.py, search.py, and models/interface.py define common request and reply types for remembered knowledge, web lookup, and AI model calls. The browser, sandbox, scheduling, and surfaces SDK files are stable shortcut imports for those public contracts.

## Sub-stages

- [Public SDK package and common façade modules](stage-17.1.md) `stage-17.1` — 9 files
- [Extension declaration, runtime SDK façades, and conformance sample](stage-17.2.md) `stage-17.2` — 6 files
- [Provider, data, model, and security SDK façades](stage-17.3.md) `stage-17.3` — 12 files

## Files in this stage

### Shared provider contracts
Core public interfaces define how UFO talks to connectors, memory backends, model providers, and search implementations without depending on specific vendors.

### `core/src/ufo/connectors.py`

`orchestration` · `cross-cutting: connector discovery, tool execution, feed sync credential resolution, and proxied request handling`

This file is the connector “border crossing” for the system. It describes how UFO talks to outside-provider backends without assuming where secrets live or how each provider works. The main idea is simple: a sync job or tool should be able to use a provider account, but it should not casually hold or expose the provider’s real token.

The file defines small value objects, such as Credential, BrokerTool, BrokerFile, and StagedUpload, that describe safe things to pass around: an authentication method, a tool description, or a temporary file reference. It also defines protocol interfaces, which are like promises that another part of the system must fulfill. AuthProxy promises it can turn a workspace, provider, and account into a safe Credential. ConnectorBroker promises it can list tools, run tools, stage files, and resolve credentials for accounts it owns.

ConnectorRegistry is the routing table. If a provider is explicitly installed, it sends requests to that provider’s broker. If an open resolver is installed, it can claim other provider names dynamically. For sync sources, SourceCredentialResolver binds credential use to the exact member-owned connection that created the source. Before using a brokered credential, the code checks the database to make sure that connection is still active. This is like checking a library card before every checkout, not just when the card was first issued.

#### Function details

##### `Credential.__repr__`  (lines 57–66)

```
def __repr__(self) -> str
```

**Purpose**: Returns a safe text representation of a Credential without showing any secret values. This matters because object representations can accidentally appear in logs, error messages, or debugging output.

**Data flow**: It reads which authentication path the Credential contains: a broker transport, a bearer token, custom headers, or nothing. It then returns a short string that names the shape of the credential but replaces any secret with “redacted”. Nothing outside the object is changed.

**Call relations**: This method is used automatically by Python when a Credential is printed or included in debugging output. Other code can pass Credential objects around normally, and this method acts as a safety net if one is accidentally displayed.


##### `AuthProxy.credential`  (lines 85–85)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Defines the promise that an authentication backend can produce a Credential for a workspace, provider, and account. A sync source uses this instead of knowing whether the secret comes from a broker or from a direct stored key.

**Data flow**: The caller provides a workspace ID, a provider name, and an account handle. An implementation looks up or prepares the right authentication method and returns a Credential. The protocol itself does not implement the lookup; it describes what implementers must provide.

**Call relations**: Source credential resolution depends on this promise. Connector-specific or direct-auth backends implement it, and higher-level code can call them through the same interface without caring which backend is underneath.


##### `stale_grant_guidance`  (lines 93–100)

```
def stale_grant_guidance(provider: str) -> str
```

**Purpose**: Builds a helpful error message for the case where a broker no longer recognizes a previously granted account. It tells the user that retrying is not enough and that the member should reconnect the account.

**Data flow**: It receives a provider name and inserts it into a fixed guidance sentence. The result is a plain string that can be attached to broker errors. It does not read or change any outside state.

**Call relations**: Broker implementations can use this helper when an account grant is stale, such as after a broker migration or organization change. The message helps the agent or user choose the right recovery action.


##### `ConnectorBroker.tools`  (lines 170–172)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Defines the promise that a broker can list provider tools matching a search query. These are the actions an agent may later describe or execute.

**Data flow**: The caller supplies a workspace ID, provider name, and query text. An implementation searches its provider tool catalog and returns matching BrokerTool objects. The protocol only defines the shape of the call.

**Call relations**: Dynamic connector discovery uses this broker method when it needs available tools for a provider. The concrete broker extension supplies the real catalog lookup.


##### `ConnectorBroker.schema`  (lines 174–174)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Defines the promise that a broker can return the detailed input schema for one provider tool. The schema tells the agent what arguments the tool expects.

**Data flow**: The caller gives a workspace ID, provider name, and tool slug. An implementation returns a BrokerTool with its input schema filled in, or raises UnknownBrokerTool if that slug is not known. No behavior is implemented here; this is an interface contract.

**Call relations**: Tool description flows call this after a tool has been found or requested by name. The returned schema is what later tool calls use to build valid arguments.


##### `ConnectorBroker.execute`  (lines 176–184)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Defines the promise that a broker can run one provider tool against a connected account. The important safety point is that the broker injects the real provider token itself, so the token does not pass through the agent or sandbox.

**Data flow**: The caller supplies the workspace, provider, tool slug, argument values, account ID, and an optional idempotency key, which helps avoid duplicate effects on retries. An implementation sends the request to the broker’s execute API and returns the provider result as a dictionary. The protocol does not implement the execution itself.

**Call relations**: Dynamic connector tools call into this broker method when the agent has chosen a provider action to run. File staging and output extraction may happen before or after this call through the other broker methods.


##### `ConnectorBroker.file_outputs`  (lines 186–186)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Defines how a broker exposes files produced by a tool call as downloadable references. The bytes are not carried through the main server process.

**Data flow**: The caller passes the raw response dictionary from a broker execution. An implementation inspects it and returns BrokerFile objects, each with a filename and temporary URL. The protocol only states that such a projection must exist.

**Call relations**: After a brokered tool execution, caller code can ask the broker to identify any output files. The sandbox can then fetch those files directly through controlled network egress.


##### `ConnectorBroker.stage_upload`  (lines 188–196)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Defines how a broker prepares a workspace file so a provider tool can consume it. Instead of routing file bytes through the server, it returns a temporary upload target or a reference to an already-stored file.

**Data flow**: The caller provides the workspace, provider, tool slug, filename, MIME type, and MD5 checksum. An implementation returns a StagedUpload containing where to PUT the file, what content type to use, and what argument value to pass to the tool. If the broker does not support this style of file input, it may raise an error.

**Call relations**: Before executing a tool that needs a file, dynamic connector code can ask the broker to stage that file. The sandbox then uploads directly to the broker’s file store, and the later execute call receives only a reference.


##### `ConnectorBroker.search`  (lines 198–198)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Defines the promise that a broker can perform richer semantic search over its tools. Semantic search means matching by meaning or intent, not only by exact text.

**Data flow**: The caller provides a workspace ID, provider name, and query. An implementation returns BrokerSearch, which may include matching tools plus plan, guidance, or pitfalls. This interface does not provide the actual search logic.

**Call relations**: Tool discovery can use this when a broker supports smarter routing advice. Brokers without that feature can still return a simple or empty answer depending on their implementation.


##### `ConnectorBroker.credential`  (lines 200–200)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Defines the promise that a broker can produce a Credential for a provider account used by feed sync. For brokered accounts, this usually means a special HTTP transport that routes requests through the broker instead of exposing the token.

**Data flow**: The caller provides a workspace ID, provider name, and account handle. An implementation verifies or resolves the account and returns a Credential. The protocol itself only defines the required method.

**Call relations**: _credential calls this when a sync source uses a brokered account rather than the direct workspace key. _BoundSourceCredentials may then wrap the returned transport with extra connection checks.


##### `RequestForwarder.forward`  (lines 219–221)

```
async def forward(self, account_id: str, method: str, url: str, headers: Mapping[str, str], body: bytes) -> ForwardedResponse
```

**Purpose**: Defines the promise that a broker can forward one HTTP request to a provider under a granted account. This is used when the sandbox sends a request carrying a harmless sentinel instead of the real secret.

**Data flow**: The caller provides the account ID, HTTP method, URL, headers, and request body bytes. An implementation forwards the request through the broker, where the real credential is injected, and returns a ForwardedResponse with status, headers, and body. The interface itself contains no forwarding code.

**Call relations**: The egress proxy calls this for intercepted provider requests that should be authenticated by a broker. The broker returns a response that can be written back to the sandbox as if it came from the provider.


##### `ConnectorResolver.transfer_hosts`  (lines 267–267)

```
def transfer_hosts(self) -> tuple[str, ...]
```

**Purpose**: Defines the promise that an open connector resolver can report extra file-transfer hostnames allowed for its broker. These hosts are needed so sandbox file uploads and downloads can reach the broker’s file store.

**Data flow**: An implementation returns a tuple of hostnames. There are no inputs beyond the resolver instance. The protocol property only describes what data must be available.

**Call relations**: When a resolver claims a broad connector namespace, its grants may need these transfer hosts added to egress permissions. Other connector routing code can read this property without knowing the broker’s details.


##### `ConnectorResolver.entry`  (lines 269–269)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: Defines how an open resolver turns an arbitrary provider slug into a ConnectorEntry. This lets one broker serve providers that were not explicitly registered one by one.

**Data flow**: The caller gives a provider name. An implementation returns a ConnectorEntry pointing that provider to the shared broker and a label. The protocol does not decide whether the broker truly supports the provider; broker calls are expected to fail clearly if it does not.

**Call relations**: ConnectorRegistry.entry and _credential call this when a provider is not in the fixed entries map but a resolver exists. It is the catch-all path for open connector namespaces.


##### `ConnectorResolver.catalog`  (lines 271–271)

```
async def catalog(self, query: str, limit: int) -> tuple[CatalogEntry, ...]
```

**Purpose**: Defines how an open resolver searches the broker’s live service catalog. This lets discovery show connectable services beyond the closed list of registered providers.

**Data flow**: The caller provides search text and a maximum number of results. An implementation asks the broker’s catalog and returns CatalogEntry objects. The protocol itself only states the required call shape.

**Call relations**: ConnectorRegistry.search_catalog delegates to this when a resolver is installed. Discovery tools can then combine fixed connector entries with live catalog results.


##### `ConnectorRegistry.entry`  (lines 288–294)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: Finds the ConnectorEntry responsible for a provider. It first checks explicitly installed connectors, then falls back to the open resolver if one exists.

**Data flow**: It receives a provider name. It looks in the registry’s entries map; if found, it returns that entry. If not found and a resolver exists, it asks the resolver to build an entry. If neither path works, it raises a KeyError explaining that no connector is installed for that provider.

**Call relations**: Dynamic connector tools use this as the main routing step before asking a broker to describe or execute tools. It keeps callers from duplicating the “fixed entry first, open resolver second” decision.


##### `ConnectorRegistry.search_catalog`  (lines 296–301)

```
async def search_catalog(self, query: str, limit: int) -> tuple[CatalogEntry, ...]
```

**Purpose**: Searches the open connector catalog, if one is installed. If there is no open resolver, it safely returns no extra catalog results.

**Data flow**: It receives query text and a result limit. If the registry has no resolver, it returns an empty tuple. If a resolver exists, it delegates the search to the resolver and returns its CatalogEntry results.

**Call relations**: Discovery flows call this when building a list of services a member can connect. The method appends live open-namespace results without affecting explicitly registered connectors.


##### `_credential`  (lines 304–321)

```
async def _credential(registry: ConnectorRegistry, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Chooses the right backend for resolving a feed-sync Credential. Brokered accounts go to a connector broker, while the special direct account goes to the configured fallback authentication backend.

**Data flow**: It receives a registry, workspace ID, provider, and account handle. If the account is not the direct account, it looks for a registered broker or open resolver and asks that broker for a Credential. If the account is the direct account, it asks the registry’s fallback AuthProxy. If no suitable route exists, it raises a runtime error.

**Call relations**: _BoundSourceCredentials.credential calls this after deciding whether the source is direct or connection-bound. This helper centralizes the routing choice so the binding code can focus on safety checks.

*Call graph*: called by 1 (credential).


##### `_require_source_connection`  (lines 324–348)

```
async def _require_source_connection(workspace_id: UUID, connection_id: UUID, owner_member_id: UUID, provider: str, account: str) -> None
```

**Purpose**: Checks that a sync source is still allowed to use a specific member-owned connection. This prevents an old source from continuing to use an account after the connection was removed or changed.

**Data flow**: It receives the workspace, connection ID, owner member ID, provider, and account. It opens a workspace-scoped database transaction, searches the connection table for an active row matching all those details, and returns nothing if the row exists. If no matching row is found, it raises ValueError.

**Call relations**: _BoundSourceCredentials.credential calls this before issuing brokered credentials, and _ConnectionTransport.handle_async_request calls it again before each proxied HTTP request. It uses the workspace context, database transaction helper, and SQL query builder to make the authorization check against stored connection records.

*Call graph*: called by 2 (credential, handle_async_request); 3 external calls (select, workspace_tx, ws).


##### `_ConnectionTransport.handle_async_request`  (lines 360–368)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: Adds a last-minute connection validity check before sending a brokered provider HTTP request. It makes sure the connection is still active at the moment the request is made.

**Data flow**: It receives an outgoing HTTP request. Before forwarding it, it calls _require_source_connection with the bound workspace, connection, owner, provider, and account. If the check passes, it passes the same request to the inner HTTP transport and returns that transport’s response. If the check fails, the request is stopped with an error.

**Call relations**: _BoundSourceCredentials.credential creates this wrapper around a broker-provided transport. During feed sync HTTP calls, this wrapper sits between the sync code and the broker transport, acting like a guard at the door.

*Call graph*: calls 1 internal fn (_require_source_connection).


##### `_ConnectionTransport.aclose`  (lines 370–371)

```
async def aclose(self) -> None
```

**Purpose**: Closes the wrapped HTTP transport when the client is done using it. This releases any network resources held by the underlying transport.

**Data flow**: It receives no new data beyond the wrapper instance. It calls aclose on the inner transport and returns nothing. The wrapper itself does not add extra cleanup.

**Call relations**: HTTP clients call this as part of normal shutdown or cleanup. It passes the close request down to the real transport so wrapping it does not leak resources.


##### `_BoundSourceCredentials.credential`  (lines 380–410)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Resolves a Credential for a sync source while enforcing whether that source is allowed to use direct credentials or a specific brokered connection. It is the safety-aware version of credential lookup used by feed sync.

**Data flow**: It receives a workspace ID, provider, and account. If the account is the direct account, it rejects the request if this resolver was bound to a connection, then delegates to _credential. If the account is brokered, it requires a connection ID and owner member ID, checks that the connection is still active, resolves the broker Credential, confirms it contains a proxy transport, and returns a new Credential whose transport is wrapped in _ConnectionTransport for repeated checks.

**Call relations**: SourceCredentialResolver.bind creates instances of this class for a particular source. Feed-sync code then calls its credential method. It calls _require_source_connection for authorization, _credential for backend routing, and wraps broker transports so each later HTTP request is guarded too.

*Call graph*: calls 2 internal fn (_credential, _require_source_connection); 2 external calls (__init__, __init__).


##### `SourceCredentialResolver.bind`  (lines 417–422)

```
def bind(self, connection_id: UUID | None, owner_member_id: UUID | None) -> AuthProxy
```

**Purpose**: Creates an AuthProxy-like credential resolver tied to one source’s connection details. This gives each sync source a credential resolver that remembers whether it is direct-key based or connected-account based.

**Data flow**: It receives an optional connection ID and optional owner member ID. It packages those values together with the registry into a _BoundSourceCredentials object and returns it. No database lookup happens here; checks happen later when credentials or requests are used.

**Call relations**: The sync runner calls this when preparing a source. The returned object is then used wherever an AuthProxy is expected, so source code can request credentials through the normal interface while still getting source-specific safety checks.

*Call graph*: 1 external calls (__init__).


### `core/src/ufo/memory.py`

`data_model` · `cross-cutting during memory recall/search`

This file is a small but important agreement between memory search providers and the code that uses them. Think of it like a standard plug shape: any extension can provide memory search, as long as it fits this shape, and consumers can use it without caring what is behind the wall.

The main shared result type is `MemoryMatch`. It describes one search hit in a provider-neutral way: what kind of memory it is, the text snippet to show, an optional durable object reference that can be opened later, and an optional creation time so results can be judged by age. The comment makes clear that search and opening are separate steps: search finds a candidate, while `object_get` would later open the underlying object.

`MemorySearchProvider` is a protocol, which means it describes what a provider must be able to do rather than implementing it itself. Any memory extension that has an async `search` method with the right inputs and outputs can act as a provider.

`MemorySearch` is the simple wrapper consumers use. It receives a chosen provider and forwards searches to it. This keeps the rest of the system talking to one stable interface, even if the actual memory backend changes.

#### Function details

##### `MemorySearchProvider.search`  (lines 28–34)

```
async def search(self, queries: tuple[str, ...], subjects: frozenset[str], start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: This defines the promise every memory search provider must keep: given search phrases, subject categories, and optional time limits, it must return matching memory results. It is not the actual search code; it is the shared contract that real providers implement.

**Data flow**: The caller provides one or more query strings, a frozen set of subject names to search within, and optional start and end times. A real provider uses those inputs to look through its memory store and returns a tuple of `MemoryMatch` results. This protocol method itself does not change data; it describes what the implemented method must do.

**Call relations**: Code that wants recall talks to a `MemorySearchProvider` through this method. `MemorySearch.search` relies on this promise when it forwards a search request to whichever provider has been selected.


##### `MemorySearch.search`  (lines 43–50)

```
async def search(self, subjects: frozenset[str], queries: tuple[str, ...], start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: This is the consumer-facing search call. It takes a clear set of subjects and query text, then delegates the actual lookup to the configured provider.

**Data flow**: The caller gives subjects, queries, and optional date boundaries. `MemorySearch.search` passes those values to its stored provider, using the provider's expected argument order, waits for the answer, and returns the provider's tuple of `MemoryMatch` objects unchanged. It does not filter, rank, or rewrite the results itself.

**Call relations**: When another part of the system needs memory recall, it calls `MemorySearch.search` instead of calling a backend directly. This method then hands the work to `MemorySearchProvider.search`, so different memory extensions can be swapped in while callers keep using the same simple doorway.


### `core/src/ufo/models/interface.py`

`data_model` · `model request construction and provider call preparation`

This file is the contract between UFO and whichever AI model service is being used, such as Anthropic or OpenAI. Instead of letting every provider invent its own shape for messages, tools, images, and responses, this file defines one shared set of Python data models. Other code can build a ModelRequest once, and each provider-specific client can translate it into that provider's API format.

The file also defines the small stream of events a model can send back: text chunks, the start of a tool call, pieces of tool-call JSON, and usage information. ModelClient is a protocol, meaning it is like a promise: any real model client must provide a complete method that accepts a ModelRequest and streams those events back.

A key practical job here is image trimming. AI providers limit how many images and how much image data can be sent in one request. trim_images acts like packing a suitcase with strict airline limits: it keeps the newest images first, removes older or oversized ones, and leaves a short text note where an image was omitted. This matters because without it, a normal conversation with screenshots or visual tool output could suddenly fail when sent to the provider.

#### Function details

##### `ModelRequest._forced_choice_names_an_offered_tool_with_reasoning_off`  (lines 87–94)

```
def _forced_choice_names_an_offered_tool_with_reasoning_off(self) -> 'ModelRequest'
```

**Purpose**: This validation step makes sure a forced tool choice is safe and meaningful. If the request says the model must call one specific tool, that tool must actually be included in the request, and extra reasoning mode must be turned off because some providers reject that combination.

**Data flow**: A newly built ModelRequest goes in. The function checks the tool_choice field, compares it with the offered tools, and checks the reasoning setting. If everything is valid, the same request comes out unchanged; if not, request creation fails with a clear error message.

**Call relations**: Pydantic, the data validation library used for these models, calls this automatically after a ModelRequest is created. It protects later provider clients from receiving a request that cannot be translated or would be rejected by the model service.


##### `ModelClient.complete`  (lines 127–127)

```
def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This is the required shape of a model client. Any provider-specific client must offer this method so the rest of the system can ask for a model completion without caring which provider is underneath.

**Data flow**: A ModelRequest goes in. A real implementation sends it to a model provider and returns an asynchronous stream, meaning events arrive over time rather than all at once. The stream yields text, tool-call information, and usage records.

**Call relations**: This file only declares the method through a protocol; it does not implement provider-specific behavior. Other parts of the system can call complete on any object that follows this protocol, and concrete Anthropic, OpenAI, or other clients supply the actual network behavior.


##### `trim_images`  (lines 135–163)

```
def trim_images(messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This function reduces image-heavy conversations so they fit within provider limits. It keeps the most recent images where possible and replaces dropped images with a text placeholder so the model still knows something was removed.

**Data flow**: A tuple of messages goes in. The function finds every inline image, decides which images survive the per-message limit, the whole-request image-count limit, and the total image-data budget, then returns either the original messages or a copied set of messages with omitted images replaced by text.

**Call relations**: Before messages are translated for a specific model provider, this function prepares the shared message format so it is less likely to be rejected. It asks _image_positions to locate images, _image_data_len to count their data size, and _trim_message to build the cleaned messages.

*Call graph*: calls 3 internal fn (_image_data_len, _image_positions, _trim_message).


##### `_image_data_len`  (lines 166–177)

```
def _image_data_len(messages: tuple[Message, ...], position: tuple[int, int, int | None]) -> int
```

**Purpose**: This helper measures the stored data size of one image at a known position in the message list. It is used when deciding whether the request is still within the image data budget.

**Data flow**: The full message tuple and one image position go in. The function follows that position to either a top-level image block or an image nested inside a tool result, reads the base64 image data string, and returns its length. If the position does not point to an image, it raises an error because the caller's bookkeeping is wrong.

**Call relations**: trim_images calls this while spending the request-wide image budget from newest image backward. It depends on positions previously discovered by _image_positions, so under normal flow those positions should always point to real images.

*Call graph*: called by 1 (trim_images).


##### `_image_positions`  (lines 180–200)

```
def _image_positions(messages: tuple[Message, ...]) -> list[tuple[int, int, int | None]]
```

**Purpose**: This helper makes a map of where every inline image lives inside the messages. It includes both direct image blocks and images nested inside tool results.

**Data flow**: A tuple of messages goes in. The function walks through each message in order, skips plain text messages, records each image's message index and block index, and adds a nested index for images inside tool-result content. It returns the positions from oldest to newest.

**Call relations**: trim_images uses this as its first step. Once it knows every image location, it can apply the count limits and later pass those same locations to _image_data_len and _trim_message.

*Call graph*: called by 1 (trim_images).


##### `_trim_message`  (lines 203–227)

```
def _trim_message(message_index: int, message: Message, drop: set[tuple[int, int, int | None]]) -> Message
```

**Purpose**: This helper creates a cleaned copy of one message, replacing selected images with a standard '[image omitted]' text block. It preserves the rest of the message so the conversation remains readable and structurally valid.

**Data flow**: One message, its index, and a set of image positions to remove go in. If the message is plain text, it comes back unchanged. If it contains blocks, the function copies each block, swaps dropped images for TextBlock placeholders, updates nested tool-result content when needed, and returns a copied Message with the new content.

**Call relations**: trim_images calls this only when at least one image must be dropped. This helper performs the final rewrite after trim_images has already decided which image positions exceed the limits.

*Call graph*: called by 1 (trim_images); 2 external calls (__init__, model_copy).


### `core/src/ufo/search.py`

`data_model` · `startup and research tool request handling`

This file is a boundary, or “seam,” between the core system and outside web search services. The core does not contain a built-in search engine and does not keep search API keys itself. Instead, a search extension supplies a provider at startup, and the rest of the system talks to it through the shapes defined here.

The file defines small immutable data objects for the main pieces of information that move across this boundary. A SearchQuery is the question being searched for, including details like how many results to return or whether to limit results to certain domains. SearchResults is what comes back: ranked SearchHit items and, optionally, a direct answer from the backend. FetchRequest describes asking for the text of one web page, and FetchedPage is the extracted page content that comes back.

The SearchProvider protocol is the key contract. A protocol is like a promise: any backend that provides these properties and methods can be used, even though this file does not implement the backend itself. Some providers can fetch full pages and some cannot, so supports_fetch tells callers whether fetch is safe to use. If a caller ignores that flag, SearchUnsupported is the clear failure signal.

#### Function details

##### `SearchProvider.supports_fetch`  (lines 87–87)

```
def supports_fetch(self) -> bool
```

**Purpose**: This property tells callers whether the chosen search provider can fetch and extract the contents of a specific web page. It prevents the system from asking a backend to do something it does not support.

**Data flow**: A concrete search provider supplies this value from its own capabilities. Callers read the value before fetching; if it is true, they may ask for page content, and if it is false, they should avoid calling fetch.

**Call relations**: This file only declares the property; actual providers implement it. Research tools use this check before attempting a page fetch, so unsupported providers can still offer search results without breaking the fetch tool path.


##### `SearchProvider.search`  (lines 89–89)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: This method is the common way to run a web search. A caller gives it a SearchQuery, and the provider returns SearchResults in the standard format used by the rest of the system.

**Data flow**: A SearchQuery goes in with the search text and optional limits such as result count, recency, allowed domains, or search category. The provider sends that request to its own backend and converts the answer into SearchResults containing SearchHit entries and possibly a direct answer.

**Call relations**: This file declares the method but does not perform the search itself. During a research turn, tools call the selected provider through the turn context, and the provider implementation does the outside API work before handing standardized results back to core code.


##### `SearchProvider.fetch`  (lines 91–91)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: This method is the common way to fetch the readable text from one web page, when the provider supports that feature. It can also request a summary or limit how much text is returned.

**Data flow**: A FetchRequest goes in with a URL and optional instructions such as an extraction prompt, maximum character count, or whether to bypass cache. The provider retrieves and extracts the page, then returns a FetchedPage with the URL, text, and possibly a summary; if fetching is unsupported, the provider is expected to raise SearchUnsupported.

**Call relations**: This method is declared here as part of the provider contract, while concrete backends supply the real behavior. The research fetch tool is expected to check supports_fetch first, so fetch is only reached for providers that claim they can retrieve page contents.


### Public SDK facades
Stable SDK modules re-export approved browser, sandbox, scheduling, and surface APIs for extension authors and external integrations.

### `core/src/ufo/sdk/browser.py`

`io_transport` · `cross-cutting browser connection setup and per-turn browser use`

This file exists so extension authors do not have to import directly from the internal browser module. Instead, they import browser-related building blocks from `ufo.sdk.browser`, which is part of the public software development kit. In plain terms, it is like a front desk: the actual workers are elsewhere, but this is the approved place to ask for them.

The concepts it re-exports are about connecting the engine to a Chrome browser through CDP, the Chrome DevTools Protocol. CDP is the control channel that lets software inspect pages, click elements, read state, and automate browser behavior. A `CdpProvider` can create a temporary `CdpLease` for a turn of work. That lease gives a `CdpEndpoint`, which contains the browser connection address and any needed headers. When the turn ends, the lease can be released. Some providers may create a fresh hosted browser session each time.

The file also exposes support pieces: `SessionGone` tells callers that a saved browser session can no longer be resumed, `FileBytes` lets a remote browser fetch file contents only when needed, and `FindCompleter` lets the host help rank or complete element-finding results. There is no new logic here; its importance is in keeping the public API clean and stable while the concrete implementation stays in `ufo.browser`.


### `core/src/ufo/sdk/sandbox.py`

`other` · `cross-cutting import-time API surface`

This file does not define new behavior. Its job is to gather and re-publish the important sandbox building blocks that outside code is expected to use. In plain terms, it is like a clearly labeled service counter: the real items live in the back rooms, but users should come here to ask for them.

The sandbox is the controlled environment where work can run. Extensions can describe what kind of sandbox carrier they provide with `CarrierSpec`, implement the `Carrier` protocol, and interact with session objects such as `SandboxSession`, `SandboxHandle`, and `ExecResult`. The file also exposes shared constants like `WORKSPACE_DIR`, `NO_PROXY_HOSTS`, and `SENTINEL_MODEL_KEY`, plus the helper `workspace_path`.

This matters because it creates a stable public API. If callers imported directly from internal modules such as `ufo.sandbox.session`, later reorganizing the project could break them. By re-exporting names here, the project can keep the outside-facing import path steady while still moving internal code if needed. The comment also notes an important project rule: package `__init__.py` files stay empty, so public SDK surfaces live in named modules like this one.


### `core/src/ufo/sdk/scheduling.py`

`io_transport` · `cross-cutting`

This module is like a clearly labeled shelf in a toolbox. The actual tools are made and stored in `ufo.scheduling`, but this file makes selected ones available under `ufo.sdk.scheduling`, which is the public place extension code is meant to use.

That matters because extensions should not need to know the internal layout of the project. If every extension imported directly from deeper internal modules, changing the project structure later could break outside users. By re-exporting these names here, the SDK gives users a stable doorway while the internals remain free to move.

The exported items cover the pieces an extension needs to work with scheduled tasks: a `ScheduleStore`, which is the object used to create or inspect scheduled work; a `ScheduledTask`, which represents a task that has been scheduled; `TaskInspection`, which describes what is known about scheduled work; `ONE_TIME_SCHEDULE`, a ready-made value for tasks that should run once; and `due_task_workspaces`, a helper for finding workspaces with tasks ready to run.

There are no functions or classes defined in this file. Its job is deliberately small: keep the SDK surface explicit and safe without putting code in `__init__.py` files.


### `core/src/ufo/sdk/surfaces.py`

`other` · `cross-cutting: active when SDK users import surface integration types`

This file does not create new behavior of its own. Its job is to be a clean front door. Instead of asking extension authors to import pieces from many internal modules, it re-exports the important building blocks for surface integrations from one named SDK module.

A “surface” is the place where UFO meets an outside product or user interface, such as a chat app, workspace, or other integration point. A surface extension needs to describe its routes with `SurfaceSpec` and `SurfaceRoute`, receive privileged request context through `SurfaceContext`, and sometimes send results back through `Writeback` and `SharedArtifact`. This file makes those names available from one public path.

It also exposes related record types, such as turns in a conversation, questions to ask a user, credential prompts, terminal frames, and transcript summaries. Errors and state types are included too, so callers can catch or describe expected problems like missing credentials, invalid connection requests, unknown workspaces, or delivery failures.

The important design choice is that `ufo.sdk` uses thin named modules rather than putting code in `__init__.py`. Think of this file like a labeled shelf in a workshop: it does not build the tools, but it puts the right tools in one place so extension authors do not need to rummage through the whole codebase.

## 📊 State Registers Touched

- `reg-effective-configuration` — The chosen runtime settings that tell the service how this deployment should behave.
- `reg-pack-selection` — The selected product pack that decides which bundle of extensions, skills, and infrastructure is enabled.
- `reg-extension-inventory` — The installed extension set and their declared capabilities, such as tools, routes, jobs, skills, and storage.
- `reg-extension-store` — Per-workspace saved extension data that add-ons use to remember their own small pieces of state.
- `reg-model-catalog` — The shared list of available AI models, their limits, features, provider names, and calling rules.
- `reg-model-provider-adapters` — The shared provider clients that translate internal model requests into Anthropic, OpenAI, OpenRouter, or similar APIs.
- `reg-tool-catalog` — The shared catalog of tools the model is allowed to see and call during a turn.
- `reg-skill-store` — The shared set of built-in, extension-provided, and user-created skills available to agents.
- `reg-search-index` — The shared searchable index and chunk store built from synced or fetched content.
- `reg-memory-store` — The long-term memory store of remembered facts and recall results used to inform later responses.
- `reg-data-backend-catalog` — The resolved registry of non-model service backends such as search, indexing, memory, source, browser, sandbox, and blob providers made available by configuration and extensions.
