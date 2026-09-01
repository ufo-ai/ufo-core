# Public SDK Runtime Capability Facades  `stage-18.3`

This stage is shared support for extensions and outside tools. It is not the main work loop itself. Instead, it acts like a row of clearly labeled doors into runtime features. Each file keeps a stable ufo.sdk import path, so extension code does not need to know where the real internal code lives.

The model, search, index, and memory doors expose the shapes and interfaces for talking to AI models, search services, embedding indexes, and memory search. Browser and terminal expose the public types for connecting to a browser or terminal session. Sandbox exposes the safe-execution boundary tools. Accounting and balance expose spending reports, prices, usage exports, and prepaid balance helpers. Flags exposes feature-flag helpers, which are switches for turning behavior on or off. Scheduled_fire exposes helpers for cron-like scheduled runs. O11y, short for observability, exposes logging and metrics so extensions can report what happened. Delivery_register shares the standard result-format prompt text. Untrusted exposes the shared wrapper for marking outside text as not fully trusted. Together, these files make the SDK steady even while the internal machinery can move around.

## Files in this stage

### Billing and spend facades
Stable SDK import points for prepaid balances, accounting reports, usage exports, and pricing-related spending objects.

### `core/src/ufo/sdk/accounting.py`

`data_model` · `cross-cutting`

This module does not create new accounting rules itself. Its job is to present a clean public shelf of names that other code can safely use. Think of it like a storefront display: the actual goods are made and stored elsewhere, but this file decides which ones are visible to customers.

The objects it re-exports describe workspace spending: totals by dimension, spending by member or agent, full spend reports, and exported usage data. It also exposes the conversion constant `MICRO_USD_PER_USD`, which represents one US dollar in millionths of a dollar, and `metered_workspaces`, which comes from the billing accounting code.

This matters because `ufo.sdk` is meant to be the public interface for people building on top of the system. If callers imported directly from deep internal modules, even a small file move could break them. By routing imports through `ufo.sdk.accounting`, the project can reorganize its internals while keeping the public import path steady. A surface can use these names to show the same workspace spending summary that the `ufoctl spend` command prints.


### `core/src/ufo/sdk/balance.py`

`other` · `cross-cutting, when SDK users import balance features`

This is a thin doorway into the billing balance system. Extensions or outside callers may need to read a workspace's prepaid balance, add credit after a payment succeeds, check whether automatic top-up is configured, or list recent purchases. Instead of asking those callers to import from the deeper internal runtime path, this file re-exports the approved names through `ufo.sdk.balance`.

That matters because it separates the public interface from the internal layout of the codebase. Think of it like a reception desk in a large building: visitors go to one known desk, even if the staff behind the scenes move offices later. Code that imports from this SDK module can stay stable while the project keeps its billing implementation elsewhere.

The imported names include balance-related data types such as `Balance`, `Purchase`, `Headroom`, and `AutoTopup`, plus actions such as reading a balance, adding credit, counting a charge, configuring auto top-up, and marking a top-up as verified. The file also exposes a billing screen fragment, likely used by user-interface or extension code. There is no extra behavior here: the real work is done by `ufo.runtime.billing.balance`.


### Runtime configuration contracts
Public facades for runtime-facing setup contracts such as browser transport access, delivery-format prompts, and feature-flag helpers.

### `core/src/ufo/sdk/browser.py`

`data_model` · `cross-cutting`

This file solves a boundary problem. The project has internal browser-connection code in `ufo.browser`, but extension authors need a stable, friendly place to import the pieces they are allowed to use. This module provides that place by re-exporting selected names.

The browser connection here is based on CDP, the Chrome DevTools Protocol, which is the control channel used to drive Chrome-like browsers. An extension can provide a `CdpProvider`, which creates a short-lived `CdpLease` for a turn of work. That lease gives access to a `CdpEndpoint`, meaning the browser address and any needed connection headers. When the turn ends, the lease is released. If work is resumed later, a saved lease token can be used to reattach, unless the browser session is gone.

The file also exposes `FileBytes`, used when a remote browser needs the contents of a local workspace file, and `FindCompleter`, a hook for helping rank or complete browser element searches. Like a reception desk, this file does not do the work itself; it directs callers to the approved internal services while keeping the public SDK surface clean and stable.


### `core/src/ufo/sdk/delivery_register.py`

`util` · `cross-cutting`

This is a very small bridge between the public SDK and the project’s internal runtime code. The “delivery register” is a shared block of prompt text that tells a model where and how to write its final delivered result, like giving every worker the same labeled inbox. Without this shared block, an extension making a direct model call might ask for results in a different shape than the shell or subagent prompts expect, which could make responses harder to collect or interpret consistently.

The file does not define new behavior. Instead, it imports two constants from the runtime delivery-register module and exposes them under the SDK namespace. This matters because the package avoids putting code in `__init__.py` files, so public SDK features are offered through named modules like this one. In practice, an extension can import `DELIVERY_REGISTER_BLOCK` or `SUBAGENT_RESULT_DESCRIPTION` from `ufo.sdk.delivery_register` without depending directly on the deeper internal path. That keeps user code cleaner and gives the project room to reorganize internals later while preserving the public import path.


### `core/src/ufo/sdk/flags.py`

`io_transport` · `cross-cutting`

Feature flags are on/off switches that let the system expose or hide parts of its behavior without changing the public code that callers use. This file is a small public doorway for those switches. Instead of making outside code reach into the internal `ufo.flags` module, it re-exports three names through `ufo.sdk.flags`: `flag_enabled`, which checks whether a flag is active, and `SERVED_TRUE` / `SERVED_FALSE`, which are the two backend spellings used to represent enabled and disabled states. The value of this file is stability and clarity. If the internal module layout changes later, SDK users can keep importing from this public SDK path. Without this file, callers would either depend on deeper internal paths or duplicate knowledge about how the backend says “true” and “false.” It is like a reception desk: it does not do the work itself, but it points users to the approved tools and shields them from the building’s private corridors.


### Model and knowledge capabilities
Stable import surfaces for indexing, memory search, model integration, and search-related extension contracts.

### `core/src/ufo/sdk/index.py`

`data_model` · `cross-cutting`

This file is a small public doorway into the project’s indexing system. Indexing is what lets the system break text into chunks, turn those chunks into searchable forms, store them, search them later, and delete them when needed. Extensions can provide their own index backend, meaning their own storage and search engine, or their own embedding backend, meaning the service that turns text into numeric vectors for similarity search.

The important idea is separation. The real definitions live deeper inside `ufo.runtime.indexing`, but outside extensions should not have to depend on that internal path. Instead, they import names from `ufo.sdk.index`. This is like a reception desk in a building: visitors do not need to know which office holds each form; the desk gives them the approved forms from one predictable place.

The file re-exports types such as `Chunk`, `Hit`, `IndexBackend`, `IndexScope`, `EmbedClient`, and `TextChunker`, plus constants that identify what kind of thing owns indexed text, such as a page or a memory item. It also re-exports `chunk_embed_upsert`, a helper for chunking text, embedding it, and saving it into an index. If this file disappeared, extension code would either break or start depending directly on internal runtime modules, making future changes harder and riskier.


### `core/src/ufo/sdk/memory.py`

`data_model` · `cross-cutting`

This file does not define new behavior of its own. Instead, it re-exports three memory-search names from the runtime layer so that outside code, such as provider extensions, can use them through the public SDK path. In plain terms, it is like a reception desk: the actual work happens elsewhere, but this desk tells outsiders which official doorway to use.

The memory-search pieces it exposes are a default memory search provider, a `MemoryMatch` type that represents a found match, and a `MemorySearchProvider` type that describes the provider interface. By importing them here, the project can keep a cleaner boundary between public extension-facing code and internal implementation details.

Without this file, extension authors might need to import directly from `ufo.runtime.memory`. That would make their code depend on internal paths, which are more likely to change. This SDK wrapper makes the public contract clearer and safer: extensions can import from `ufo.sdk.memory`, while the project remains free to reorganize the runtime code later as long as this public doorway keeps working.


### `core/src/ufo/sdk/models.py`

`other` · `cross-cutting; active when SDK users import model-related public API names`

This file does not define new behavior. Its job is to gather many model-related names from deeper inside the project and re-export them through `ufo.sdk.models`, which is meant to be a safe public surface for extension authors.

Think of it like a reception desk in a large office building. The real teams sit in different rooms, but visitors do not need to know every hallway. They go to the desk and ask for the person or form they need. Here, the “visitors” are outside extensions, and the “rooms” are internal modules such as OpenAI support, Anthropic support, shared message formats, pricing, and authorization grants.

The exported items include model clients such as `OpenAIClient` and `AnthropicClient`, message and content block types such as `Message`, `TextBlock`, and `ImageBlock`, streaming event types such as `TextDelta` and `ToolCallStart`, helper functions for image trimming or omission, model specification types, usage records, and constants used for authentication.

This matters because internal code can move around over time. If extensions imported directly from those internal locations, small refactors could break them. By importing from this file instead, extensions rely on a stable contract: “these are the model-building pieces the SDK promises to expose.”


### `core/src/ufo/sdk/search.py`

`other` · `cross-cutting`

This file is a thin public wrapper around the search system. Its job is not to perform searches itself. Instead, it re-exports the main search building blocks from `ufo.runtime.search` under the friendlier public path `ufo.sdk.search`.

That matters because extension authors and tool code should not need to know where the internal runtime implementation lives. They can import names like `SearchProvider`, `SearchQuery`, and `SearchResults` from this SDK module and rely on that path staying stable. Think of it like a reception desk: visitors go to one obvious place, even though the actual work happens in offices behind the scenes.

The re-exported types describe the contract between the main system and a search backend. A backend implements `SearchProvider`, receives a `SearchQuery`, and returns `SearchResults` made up of `SearchHit` entries. If the backend can also fetch full pages, it uses `FetchRequest` and returns a `FetchedPage`.

Without this file, outside code would have to import directly from the runtime layer. That would make extensions more tightly tied to internal layout decisions and make future refactoring harder.


### Execution and operations facades
SDK doorways for observability, sandbox execution, scheduled work, and terminal-facing runtime interfaces.

### `core/src/ufo/sdk/o11y.py`

`util` · `cross-cutting`

This file is a thin public wrapper around the system’s observability tools. “Observability” means the signals a running system gives humans and monitoring tools, such as logs, warnings, metrics, and timing profiles. Extensions can import `log`, `warn`, `emit_metric`, and `turn_profile` from this SDK path instead of depending on the deeper internal module where those tools are implemented.

The important idea is control. Metrics are not invented freely by extensions here. The comment explains that the counter registry lives in core code, so an extension must emit a metric name that core already knows about. If it tries to use an unknown one, the system fails loudly. This keeps the project’s metric list predictable and reviewable, like using an approved set of dashboard labels instead of letting every team create new labels with slightly different spellings.

There is no new behavior in this file. It simply re-exports selected functions from `ufo.harness.o11y` under a stable, public SDK location. Without this file, extensions would either need to import internal harness paths, which makes them more fragile, or duplicate logging and metrics behavior, which would make monitoring less consistent.


### `core/src/ufo/sdk/sandbox.py`

`other` · `cross-cutting`

This file does not define new behavior. Instead, it gathers many sandbox tools from deeper inside the project and re-exports them under `ufo.sdk.sandbox`, which is the public name outsiders are meant to use. Think of it like a reception desk: the actual teams sit elsewhere, but visitors only need one clear place to ask for them.

The sandbox is the controlled environment where code can run with known paths, users, network settings, copied files, browser support, and proxy settings. Extension authors may need to describe a backend using `CarrierSpec`, implement the `Carrier` protocol, work with a `SandboxSession`, inspect command results through `ExecResult`, or use helpers for safe filesystem paths such as `workspace_path` and `contained_file`.

Keeping these imports here matters because it protects callers from the internal layout of the codebase. If the project later moves `ufo.harness.sandbox.session` or splits it up, code that imports from `ufo.sdk.sandbox` can stay the same. The opening comment also explains a project rule: package `__init__.py` files must stay empty, so public API names live in explicit modules like this one.


### `core/src/ufo/sdk/scheduled_fire.py`

`other` · `cross-cutting`

This file is a small public doorway. In this project, a “scheduled fire” means a task run that is started by a schedule, like a cron job. Each scheduled run needs a predictable key so the system can recognize what task it belongs to later. Without a stable SDK import path like this, user code might have to import directly from deeper runtime internals, which would make future refactoring harder and more likely to break users.

The file re-exports two helper names from `ufo.runtime.ext.scheduled_fire`. One builds the key used to identify a scheduled run, and the other reads that key back to recover the task id. Think of it like putting a label on a package and later reading the label to know where the package came from.

There is no extra behavior here: no validation, storage, scheduling, or network work happens in this file. Its value is in API design. It marks these helpers as part of the public SDK surface, meaning other code can depend on this import location even if the internal runtime file changes later.


### `core/src/ufo/sdk/terminal.py`

`other` · `cross-cutting import-time public API`

This module does not create new behavior of its own. Instead, it gathers several terminal-related names from deeper inside the project and re-publishes them under `ufo.sdk.terminal`, which is meant to be a clean public interface for extension authors.

The problem it solves is stability and convenience. Code outside the project should not need to know the internal folder layout, such as where sandbox terminal code or blob storage code lives. By importing from this file, a terminal transport extension can depend on a small, named SDK surface instead of reaching into private-looking internals.

The file re-exports terminal concepts such as `TerminalTransport`, which represents the communication layer for terminal access, `Terminals`, the in-process backend that can be reused, and `TerminalWorkspace`, which describes the working area for terminal activity. It also exposes terminal error types like `TerminalAbsent`, `TerminalGone`, and `TerminalOpFailed`, so callers can react to common failure cases in a consistent way.

It also re-exports `BlobStore` and `BlobNotFound`, because terminal extensions may need to read or write larger data through the fleet’s blob store, meaning shared storage for chunks of data. The timing constants give extensions the same grace periods and deadline padding used by the built-in terminal system.

In short, this file is like a labeled service counter: it does not manufacture the parts, but it tells outside users exactly where to pick them up.


### Untrusted text boundary
A shared SDK wrapper for marking externally sourced text as untrusted across extensions and core runtime code.

### `core/src/ufo/sdk/untrusted.py`

`util` · `cross-cutting`

Some text shown by the system may come from places that should not be fully trusted, such as a tool’s output, a provider response, or a subagent’s returned message. This file exists so everyone uses the same marker for that kind of content. Think of it like putting suspicious mail into a clear protective sleeve before passing it around: the contents can still be read, but the system remembers that they came from outside.

The file does not create new behavior itself. Instead, it re-exports `wall` from `ufo.harness.untrusted`. That means code using the SDK can import `wall` from this SDK path, while still getting the core system’s official definition. This matters because renderers and safety boundaries need a consistent signal. If extensions used a different wrapper than core code, one part of the system might recognize untrusted content while another part might miss it.

In short, this file is a small bridge. It keeps third-party extension code and internal core paths speaking the same language when they mark content as untrusted.
