# Public SDK capability provider interfaces  `stage-19.2`

This stage is shared behind-the-scenes support for extensions. It defines the public “plug sockets” that outside code can use without depending on the project’s internal wiring. The core browser file defines the basic promise for getting a Chrome DevTools Protocol connection, which is a control channel for a browser, whether that browser is local, sandboxed, or remote. The runtime search file defines the common shape for web search and page fetching.

Most files in this stage are SDK doorways. They re-export approved internal tools through stable import paths, so extension authors can rely on them even if the internal layout changes. The browser, search, index, memory, models, objects, sandbox, skills, sources, and terminal SDK files each gather the public names for one capability area. Together they let add-ons provide or use browser access, search backends, indexing, memory lookup, model calls, object types, source syncing, sandbox tools, skills, and terminal support. Like labeled ports on a machine, they hide the inner machinery while making extension points clear and safe to use.

## Files in this stage

### Runtime provider contracts
Foundational internal contracts define how core code requests browser connections and web search/page fetches without binding to a concrete provider.

### `core/src/ufo/browser.py`

`io_transport` · `per-turn browser setup, recovery, and teardown`

This file is a boundary line between core logic and browser-specific extensions. The core system needs a Chrome browser to inspect and drive web pages, but it should not care where that browser lives. It might be running inside the task sandbox, or it might be a remote hosted browser service. This file describes the promises that any browser source must keep.

The main idea is a lease, like borrowing a library book for one turn. A CdpProvider can create a CdpLease. That lease gives the browser engine a CDP endpoint, meaning the web address and headers needed to connect to Chrome through the Chrome DevTools Protocol, which is Chrome’s remote-control interface.

The lease also answers practical questions that depend on where Chrome is running. If Chrome needs to open a workspace file, is the file already visible at the same path, or must it be uploaded first? If Chrome downloads a file, can the system read it from the sandbox filesystem, or must it fetch it from a remote provider? The lease hides those differences.

There is also a reattach path. If work is recovered after interruption, the system can use a saved token to reconnect to the same browser session. If that session has disappeared, SessionGone tells the caller to start fresh instead of pretending the old page still exists.

#### Function details

##### `CdpLease.endpoint`  (lines 59–59)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the Chrome connection details for this borrowed browser session. A browser engine uses this to know where to connect and which extra headers, such as authentication or routing tokens, it must send.

**Data flow**: It takes no direct input beyond the lease object itself. It reads the lease’s stored connection information and returns a CdpEndpoint containing a URL and optional headers. If a real implementation cannot provide a usable endpoint, it may fail instead of returning a bad connection.

**Call relations**: This is part of the lease contract that browser providers must implement. After CdpProvider.lease or CdpProvider.reattach gives the caller a lease, the browser-driving extension calls this method to connect to Chrome.


##### `CdpLease.token`  (lines 61–61)

```
async def token(self) -> str
```

**Purpose**: Returns a saved handle that can be used later to reconnect to the same browser session. This matters when a turn is interrupted and the system wants to resume instead of starting from a blank browser.

**Data flow**: It takes no direct input besides the lease. It turns the current browser session identity into a string, such as a remote session id or a stable endpoint name, and returns that string for storage.

**Call relations**: This works as the companion to CdpProvider.reattach. A browser extension can persist the token during a turn, and a later recovered run can hand that token back to the provider to try to reconnect.


##### `CdpLease.place_file`  (lines 63–63)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Makes a workspace file available to the leased Chrome and returns the path or location Chrome should use to open it. This hides whether Chrome can already see the file or whether the file must be copied or uploaded first.

**Data flow**: It receives the file’s sandbox path and a read function that can produce the file’s bytes. A local sandbox Chrome may simply return the same path without reading the bytes. A remote Chrome implementation may call the read function, upload the bytes somewhere the remote browser can access, and return that remote location.

**Call relations**: The browser engine calls this when it needs Chrome to open a file from the task workspace. The method belongs to the lease because only the chosen transport knows whether file paths are shared with Chrome.


##### `CdpLease.download_dir`  (lines 65–65)

```
async def download_dir(self) -> str
```

**Purpose**: Returns the place where Chrome should write downloaded files for this lease. This gives the browser engine one simple answer even though local and remote browsers store downloads differently.

**Data flow**: It takes no direct input besides the lease. It returns a directory or storage location that Chrome can use for downloads. In a sandbox-backed browser this may be a sandbox path; in a hosted browser it may represent provider-controlled storage.

**Call relations**: The browser engine asks this before enabling downloads in Chrome. Later, when Chrome reports a completed download by its identifier, CdpLease.fetch_download is used to retrieve the actual bytes.


##### `CdpLease.fetch_download`  (lines 67–67)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Retrieves the bytes of a completed browser download. The caller gives the download’s Chrome-generated identifier, and the lease knows where to fetch the file from.

**Data flow**: It receives a download guid, which is Chrome’s unique name for the completed download. The implementation looks in the right place for this kind of browser session, such as the sandbox filesystem or a remote provider API, then returns the downloaded file as bytes.

**Call relations**: This follows CdpLease.download_dir in the download flow. The browser engine first tells Chrome where downloads should go, then uses this method to bring the finished file back into the system.


##### `CdpLease.aclose`  (lines 69–69)

```
async def aclose(self) -> None
```

**Purpose**: Releases the borrowed browser session at the end of a turn. For a local or static browser this may do nothing, while a hosted provider may use it to free a remote session.

**Data flow**: It takes no direct input besides the lease. It performs whatever cleanup the provider requires and returns no value. The visible change is outside the object: the browser hold may be released, closed, or marked available for cleanup.

**Call relations**: This is called when the turn is finished or being cleaned up. It closes the lifecycle that began with CdpProvider.lease or CdpProvider.reattach.


##### `CdpProvider.lease`  (lines 83–83)

```
async def lease(self, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: Creates a fresh browser lease for one turn of work. It is the standard way to obtain a Chrome connection when there is no existing session to resume.

**Data flow**: It may receive a Sandbox, which is the isolated workspace for the turn. A sandbox-based provider can use that sandbox to find Chrome running inside it, while a remote provider may ignore it and create a hosted browser session. It returns a CdpLease that the rest of the browser flow uses.

**Call relations**: This is called near the start of a turn by orchestration code that needs browser access. The returned lease then supplies endpoint, file placement, download, token, and cleanup behavior to the browser extension.


##### `CdpProvider.reattach`  (lines 85–85)

```
async def reattach(self, token: str, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: Attempts to reconnect to a browser session that was created earlier. This supports recovery after interruption, so the system can continue from the same browser state when possible.

**Data flow**: It receives a saved token and may also receive the recovered turn’s Sandbox. The provider uses those inputs to find the old session and returns a new CdpLease over it. If the session no longer exists or cannot be reached, it raises SessionGone so the caller knows to create a new lease instead.

**Call relations**: This is the recovery counterpart to CdpLease.token. A previous lease provides the token, later startup code passes it here, and the provider either hands back a usable lease or signals that the normal CdpProvider.lease path should be used.


### `core/src/ufo/runtime/search.py`

`data_model` · `cross-cutting`

This file is the contract between the core runtime and any web-search backend. The core project does not include a built-in search engine and does not want to know the details of each provider’s API, keys, or network rules. Instead, it defines a small common language: a search request, a list of search hits, a page-fetch request, and a fetched page.

The data classes are simple containers. `SearchQuery` says what the user wants searched for, how many results to return, and optional limits such as date range or allowed websites. `SearchResults` returns ranked `SearchHit` items, and may include a direct answer if the backend can produce one. `FetchRequest` asks for the text of one web page, optionally with a prompt for extraction or summarization. `FetchedPage` returns the page text and possibly a summary.

The `SearchProvider` protocol is the key seam. A protocol is like a job description: any backend can be used if it offers the listed abilities. At startup, the chosen provider is built elsewhere from configuration. During a turn, research tools call it through the tool context. This keeps provider credentials on the host side and out of the sandbox, which is important for security.

#### Function details

##### `SearchProvider.supports_fetch`  (lines 82–82)

```
def supports_fetch(self) -> bool
```

**Purpose**: This tells callers whether the selected search provider can fetch and extract the contents of a specific web page, not just return search results. Tools use it as a safety check before trying to fetch a URL.

**Data flow**: The caller reads this property from a provider object. The provider returns a true or false value. Nothing is changed; the result simply tells the caller whether fetching is available.

**Call relations**: Research tools consult this property before calling `SearchProvider.fetch`. If it says fetching is not supported, the tool can avoid making a request that the provider cannot fulfill.


##### `SearchProvider.search`  (lines 84–84)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: This is the standard way to ask the selected provider to run a web search. Someone would use it when they have a natural-language research question and need ranked web results in the system’s common format.

**Data flow**: A `SearchQuery` goes in, containing the query text and optional limits such as result count, dates, allowed domains, or category. The provider sends that request to its own backend service and translates the response into `SearchResults`. The output is a set of `SearchHit` records, possibly plus a direct answer.

**Call relations**: Research tools call this method through the turn’s tool context when a user or agent needs web results. Concrete provider extensions implement the actual network call and return data in the shared shape defined by this file.


##### `SearchProvider.fetch`  (lines 86–86)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: This is the standard way to ask a provider to retrieve readable text from one URL. It is used when the system needs the contents of a specific page, not just a search listing.

**Data flow**: A `FetchRequest` goes in, containing the URL and optional instructions such as a prompt, a maximum text length, or whether to bypass cache. The provider retrieves or extracts the page content and returns a `FetchedPage` with the URL, text, and possibly a summary. The provider may contact an outside service, but the core code only sees the common result object.

**Call relations**: The research `fetch_url` tool calls this only after checking `SearchProvider.supports_fetch`. Concrete search-provider extensions supply the real implementation, including any API calls and credential use on the host side.


### Foundational SDK facades
Stable SDK import paths expose browser, indexing, memory, model, object, sandbox, and search capability interfaces to extension authors.

### `core/src/ufo/sdk/browser.py`

`io_transport` · `cross-cutting; used when extensions or the engine need to connect to a browser session`

This file is a small but important boundary marker. The project has browser machinery elsewhere, in `ufo.browser`, but this file chooses which pieces are part of the public software development kit, or SDK. An SDK is the set of tools and types outside extensions are expected to build against.

The browser connection here is based on CDP, the Chrome DevTools Protocol. CDP is the remote-control interface Chrome exposes so another program can inspect pages, click things, read the document, and so on. Extensions can provide or connect to a browser through a `CdpProvider`. For each turn of work, that provider creates a `CdpLease`, like borrowing a browser session for a limited time. The lease gives a `CdpEndpoint`, which contains the address and headers needed to connect. When the turn is over, the lease can be released.

The file also exposes supporting ideas: a saved lease token for reconnecting later, `SessionGone` for the case where that saved browser session no longer exists, `FileBytes` for lazily reading local file contents only when needed, and `FindCompleter`, a hook used to rank page elements during browser automation.

Without this file, extensions would have to depend directly on internal browser modules. That would make the public contract less clear and easier to break.


### `core/src/ufo/sdk/index.py`

`data_model` · `cross-cutting`

This file does not define new behavior. Instead, it acts like a clean front desk for the project’s indexing system. Extensions can use this module to learn the shapes they must provide when they add a search backend or an embedding backend.

In this project, an index backend is the part that stores and searches text chunks. It can support ordinary word-based search, vector search, or both. Vector search means comparing pieces of text by meaning, using numeric representations called embeddings. An embed client is the part that creates those numeric representations from text.

The actual implementations and type definitions live deeper in `ufo.runtime.indexing`. This SDK file re-exports only the pieces extension authors are expected to use, such as `IndexBackend`, `EmbedClient`, `Chunk`, `Hit`, and `IndexScope`. That keeps extensions from depending directly on internal runtime paths. If the runtime internals move later, this SDK file can preserve the public import location.

Without this file, extension authors would have to import from the runtime package directly, which would blur the line between stable public API and internal project wiring. This module is therefore small but important: it protects the project’s plugin boundary.


### `core/src/ufo/sdk/memory.py`

`data_model` · `cross-cutting`

This file is a public doorway for memory search support. In this project, a “memory search provider” is the piece that can look through stored memory or context and return matching results. Rather than asking extension authors to import those pieces from the deeper runtime package, this file re-exports them from the SDK area.

That matters because an SDK is meant to be the friendly front door. If the internal project structure changes later, code outside the project can keep importing from this file, and the project can adjust the bridge behind the scenes. It is like giving visitors one official reception desk instead of sending them through staff-only corridors.

The file exposes three names: the default memory search provider setting, the `MemoryMatch` type that represents one search result, and the `MemorySearchProvider` type that describes what a provider must look like. There is no extra behavior here and no hidden setup. Its job is simply to make these memory-search concepts available as part of the public SDK surface.


### `core/src/ufo/sdk/models.py`

`data_model` · `cross-cutting; active when SDK users import model-related API pieces`

This module is a thin public “shop window” for the project’s model layer. It does not create new behavior itself. Instead, it gathers model-related building blocks from internal modules and re-exports them under `ufo.sdk.models`, which is the safer place for outside code to import from.

The problem it solves is stability. Without this file, extension authors would need to reach into internal paths such as `ufo.harness.models.openai` or `ufo.harness.models.interface`. Those internal paths may change as the project evolves. By offering this file as a public seam, the project can reorganize its internals while keeping the outside-facing API steadier.

The exported names cover several related areas: model clients for providers like OpenAI and Anthropic, shared request and response shapes, message and tool-call blocks, pricing and model specification types, authentication grant helpers, provider-specific constants, and small utilities for removing or trimming images from model messages. In plain terms, this file collects the vocabulary and connectors that outside code needs when talking to language models through UFO.

An everyday analogy: this file is like a hotel reception desk. Guests should ask reception for services, not wander into staff-only corridors. The reception desk may call many back-office departments, but the guest gets one clear, supported place to go.


### `core/src/ufo/sdk/objects.py`

`data_model` · `cross-cutting; used whenever extensions or SDK users import object-related public types and constants`

This module exists to keep the public SDK simple and safe. Extensions need to talk about shared things like agents, conversations, workspaces, members, credentials, and artifacts. Those ideas are defined deeper inside the system, but extension authors should not have to know where every internal definition lives. This file gathers those names into one clear place: `ufo.sdk.objects`.

It works like a reception desk in a large building. Instead of sending visitors through back corridors to find each office, the reception desk points them to the right services through one official entrance. Here, the “visitors” are extensions and store implementations, and the “services” are object kind constants, object reference types, list/query/page shapes, ownership types, permission-related classes, and a few object helper functions.

There is no new business logic in this file. It imports names from host, runtime, and schema modules, then re-exports them under the same names. That matters because it creates a stable boundary: internal files can be reorganized later, while extension code can keep importing from the SDK path. The comment also notes an important project rule: package `__init__.py` files are kept empty, so named modules like this one are where the public SDK surface is intentionally exposed.


### `core/src/ufo/sdk/sandbox.py`

`other` · `cross-cutting; active when SDK users import sandbox APIs`

This module does not create new sandbox behavior. Instead, it gathers sandbox tools from deeper internal modules and re-exports them under the public `ufo.sdk.sandbox` name. In plain terms, it is like a front desk: users do not need to know which back office contains each form; they can ask here and get the right public item.

The sandbox is the controlled environment where UFO can run code, copy files, open ports, and talk to outside services in a safer, more predictable way. Extension authors need to describe sandbox backends, connect to running sandboxes, execute commands, refer to special paths, and report sandbox-specific failures. This file exposes those building blocks: value objects such as `SandboxSpec`, `SandboxHandle`, and `ExecResult`; protocols such as `Carrier`; constants for well-known sandbox paths and environment variables; and helper functions for safe file paths, ports, proxy settings, and workspace locations.

The important design choice is that this is a thin public API layer. The actual implementation stays in `ufo.harness` and `ufo.runtime`, while this file gives outside code a stable import path. Without it, extensions would have to import from internal locations, making them more fragile if the project reorganizes its internals.


### `core/src/ufo/sdk/search.py`

`data_model` · `cross-cutting`

This file is like a front desk for the search system. The actual search types live deeper in the project, in `ufo.runtime.search`, but outside code should not need to know that internal path. Instead, extensions and tools can import from `ufo.sdk.search`, which is part of the public software development kit, or SDK — the stable set of names the project offers to other code.

The search system has a few shared pieces. A `SearchProvider` is the backend that knows how to answer searches. A `SearchQuery` is the question being asked. `SearchResults` and `SearchHit` describe the answers. If a provider can fetch a full page after finding it, `FetchRequest` describes what to fetch and `FetchedPage` describes what came back.

Nothing is computed here. There are no functions or classes newly created in this file. Its job is to keep a clean boundary: callers use the SDK import path, while the project remains free to organize the runtime internals behind that boundary. Without this file, extension authors would have to import from internal runtime modules directly, making their code more fragile if the project layout changes.


### Extension workflow facades
Higher-level SDK doorways gather public interfaces for skills, source synchronization, and terminal support used by pluggable extensions.

### `core/src/ufo/sdk/skills.py`

`other` · `cross-cutting`

This file is like a clearly labeled service counter at the front of a larger workshop. The real skill code lives deeper inside the project, under runtime modules, but extension authors and other users should not have to know that internal layout. Instead, they can import skill objects and helper functions from `ufo.sdk.skills`.

The file exposes a small public surface: `RuntimeSkill` and `SkillCard`, which represent skill information; `parse_skill_content`, which turns in-memory skill text into those structured objects; `skill_root`, which helps identify where skills belong; and `lexical_score` plus `SKILL_LINE_MAX_CHARS`, which support text-based skill searching and ranking.

The important idea is stability. Internal files can be reorganized later, but callers can keep using this public SDK module. The project also keeps package `__init__.py` files empty, so named files like this one are where the official import paths live. Without this file, outside code would either need to import from internal runtime paths directly, making it more fragile, or duplicate knowledge about where these skill tools are stored.


### `core/src/ufo/sdk/sources.py`

`other` · `cross-cutting; used when extensions are written, imported, and run`

This file does not implement new behavior itself. Instead, it works like a clearly labeled toolbox at the front of the project: extension authors can import everything they need for source connectors from `ufo.sdk.sources` without knowing where the internal runtime code lives.

A “source” here means an outside system that can provide records, such as chat messages, email, repository issues, or other provider data. Extensions implement a `SourceBackend`, whose job is to fetch provider records and turn them into `Page` documents that UFO can store and later search or embed. The file also exposes `RestConnector` and related connector pieces for the common case where the outside system is a REST API, meaning data is fetched over ordinary web requests.

The long module comment explains the contract between an extension and core UFO. A backend can report a full snapshot, where old pages not seen anymore should be tombstoned, or an incremental update, where only explicitly deleted pages should be removed. It also names special error signals, such as `CursorExpired` for an outdated resume marker, `StreamSkipped` for a provider stream that is not available for this account, and `StreamFault` for provider data that has an unreadable shape.

Without this file, extension code would have to import from deeper internal modules, making extensions more fragile when the project is reorganized.


### `core/src/ufo/sdk/terminal.py`

`io_transport` · `cross-cutting`

This module is like a clearly labeled front desk for terminal support in the SDK. The real terminal machinery lives elsewhere, mainly in the sandbox terminal code and the blob storage code. Instead of making extension authors know those internal paths, this file exposes the pieces they are expected to use through `ufo.sdk.terminal`.

The re-exported items describe the shared language for terminal work: `TerminalTransport` is the interface a terminal transport extension is expected to implement, `Terminals` is the in-process backend it may reuse, and `TerminalWorkspace` and `TerminalOp` describe terminal workspaces and operations. The file also exposes terminal-specific error types such as `TerminalAbsent`, `TerminalGone`, and `TerminalOpFailed`, so callers can react to common failure cases in a consistent way. `BlobStore` and `BlobNotFound` are included because terminal transport code may need to reach the fleet's blob store, which is storage for larger pieces of data passed around by the system.

The comment explains an important design rule: this SDK package keeps its `__init__.py` empty, so public imports are gathered in named modules like this one. Without this file, users would have to import from internal implementation paths, making their code more fragile if the project is reorganized.
