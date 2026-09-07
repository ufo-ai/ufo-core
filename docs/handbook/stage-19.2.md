# Extension authoring SDK contracts  `stage-19.2`

This stage is the public “front desk” for people writing extensions. It is shared support rather than part of startup or the main work loop. Its job is to give extension authors stable import paths, so they can use approved pieces of the system without depending on private internal files that may change.

Each file is a doorway for one kind of extension work. context.py exposes the safe context objects an extension may use while it runs. manifest.py provides the types and helpers for describing an extension’s identity and capabilities. jobs.py offers the tools for declaring background jobs. skills.py re-exports the public skill helpers and classes. tools.py gathers the approved building blocks for defining or running tools. connectors.py exposes connector and OAuth pieces for linking outside services. sources.py provides the blocks for syncing external content like mail, chat, repositories, or REST services. surfaces.py exposes the pieces for building user-facing surface extensions. Together, they act like labeled shelves in a workshop: extension authors know where to pick up the right parts.

## Files in this stage

### Foundational SDK contracts
Stable public imports for extension manifests and approved execution context objects.

### `core/src/ufo/sdk/context.py`

`other` · `cross-cutting import-time SDK access`

This file is like the front desk for extension context tools. The real classes and functions live deeper inside the project, in runtime and schema modules, but this file re-exports them under the public `ufo.sdk.context` path. That matters because extension authors should not need to know the project’s internal folder layout, and the project can reorganize its internals later without breaking code that imports from the SDK.

The exported names describe what an extension can learn or do while it is running. For example, `ExtensionContext` represents the current working context given to an extension handler. `ConversationFacts`, `Trajectory`, `TurnOutcome`, and related records describe conversation state and history. `CredentialAccess`, `ModelAccess`, and store/source helpers provide controlled access to credentials, models, saved data, and source material. Agent-related names such as `WorkspaceAgent`, `agent_current`, and `AgentArchived` help code understand which agent is active and whether it is still usable.

There is no new behavior here. Importing this file simply makes selected internal objects available through a public SDK namespace. Without this file, extension code would either import from private internals directly, which is fragile, or would lack a single obvious place to find the context API.


### `core/src/ufo/sdk/manifest.py`

`other` · `cross-cutting; active when extension code imports SDK manifest types`

This file does not create new behavior of its own. Instead, it gathers many existing names from deeper parts of the system and re-exports them under `ufo.sdk.manifest`, which is meant to be the safe public import path for extension code.

The problem it solves is stability. Without this file, outside extensions would need to import things like manifest models, hook types, setup definitions, conversation slot payloads, image preview helpers, and workspace change limits from internal module paths. Those internal paths can change as the project is reorganized. This file acts like a front desk: extension authors ask for the public name here, and the project can keep the inside layout private.

The repeated `as SameName` imports are intentional. They make it clear that these names are part of the public surface of the SDK. The module also follows a project rule that package `__init__.py` files should stay empty, so named modules like this one carry the public API instead.

Important to know: importing this file mainly makes names available. It does not validate manifests, run hooks, read files, or start any runtime process.


### Executable capabilities
Public SDK doorways for declaring background jobs, skills, and tools that extensions can provide or run.

### `core/src/ufo/sdk/jobs.py`

`util` · `cross-cutting`

This module is a doorway, not a workshop. It does not define new behavior itself. Instead, it re-exports a small set of job-related names from deeper runtime modules so extensions can use a stable, supported import path: `ufo.sdk.jobs`.

The problem it solves is API safety. Without this file, extension authors would need to import from internal modules such as `ufo.runtime.ext.manifest` or `ufo.runtime.candidates`. That would tightly couple extensions to the project’s private layout. If the internals moved, those extensions could break. This file acts like a front desk: it gives outsiders the names they are allowed to use, while the building behind the desk can be rearranged later.

The exported pieces cover three main needs. `JobSpec` describes a background job. `owner_candidates` and `WorkspaceCandidates` help a job say which workspaces may have work to do. Several workspace helper functions expose common workspace selections, such as workspaces with live agents or seated members. `JobFault` lets a job fail with a clear human-written reason instead of only a low-level stack trace. `PAGE_CHANGE_CURSOR_KEY` exposes the approved key prefix for resetting a page-change job’s saved cursor without copying core’s private naming rule.

So this file matters because it defines the clean public contract between extension code and the job system.


### `core/src/ufo/sdk/skills.py`

`util` · `cross-cutting import-time public API`

This file is like a front desk for the skill system. The real work lives deeper in the project, under runtime modules, but users of the public SDK should not need to know that internal layout. Instead, they can import skill-related pieces from `ufo.sdk.skills`.

It exposes the value objects used to describe skills, such as `RuntimeSkill` and `SkillCard`, plus helpers for reading skill content, finding the root folder for skills, and scoring text matches during skill search. A “re-export” means this file imports something from another module and then makes it available again under this simpler public name.

The comment at the top explains why this exists: the `ufo.sdk` package keeps its `__init__.py` empty, so named modules like this one become the public entry points. Without this file, extension authors or SDK users would have to import from internal runtime paths, which would make their code more fragile if the project later reorganized its internals.


### `core/src/ufo/sdk/tools.py`

`other` · `cross-cutting`

This file exists to make the tool API stable and easy to find. Instead of asking extension authors to import classes and helpers from many internal modules, it re-exports the small set of names they are meant to use from `ufo.sdk.tools`. Think of it like a front desk: the real offices are elsewhere, but outsiders should come here so the building can be reorganized later without changing their directions.

The exported names cover the main things a tool author needs: definitions for tools and actions, the context object a tool receives while running, result and failure shapes, text and image content types, diagnostics, connector access, file-change limits, and task-running helpers. The comments call out one important example: `run_task`, which lets long-running command work continue in a detached task journal. That means a command can keep running even if the original caller’s time budget is too short, and later be reported through consistent task handles.

There is no new logic in this file. Its value is in protecting the public boundary of the project. Without it, extensions would reach into internal runtime modules directly, making them more likely to break when the core code is refactored.


### External integrations
Stable contracts for plugging in external connectors and synchronizing external content sources.

### `core/src/ufo/sdk/connectors.py`

`other` · `cross-cutting import-time API surface`

This file does not define new behavior itself. Its job is to give outsiders and extensions one stable place to import the connector interface from. A connector is the bridge between this project and some outside brokered service, such as a provider that offers tools, files, search results, credentials, or feed-sync access.

Without this file, extension authors would need to know the project’s internal folder layout and import directly from runtime modules. That would make extensions more fragile, because internal code can move around. This file acts like a front desk: it points people to the right forms and names, while hiding where those forms are stored behind the scenes.

The names re-exported here cover two closely related areas. The connector side includes things like broker tools, catalog entries, staged uploads, grant secrets, and registries that collect available connectors. The OAuth side covers the login-and-permission flow, where a user authorizes access and the system receives credentials it can later use.

The opening comment explains the larger design: an extension bundles an OAuth provider with a connector broker into a connector provider manifest. Core code can then drive connection flows, attach the merged connector registry to a tool context for dynamic tools, and route feed-sync credentials through the same seam, without needing to know each broker’s private mechanics.


### `core/src/ufo/sdk/sources.py`

`other` · `cross-cutting SDK import surface`

This file does not implement new behavior itself. Instead, it acts like a clearly labeled toolbox shelf for source connectors. An extension author can import from this single SDK module instead of knowing the deeper internal paths where the real classes live.

The ideas exposed here are the contract between UFO core and an extension. A `SourceBackend` is the piece an extension writes to fetch records from an outside service and turn them into `Page` documents. A `SyncResult` tells core whether the run was a full snapshot or an incremental update, and whether any old pages should be removed. Special errors such as `CursorExpired`, `StreamSkipped`, and `StreamFault` let a connector explain common sync outcomes in a controlled way: retry from scratch, skip without failing, or report a provider response the connector cannot understand.

For REST APIs, the file also exposes reusable connector machinery: `RestConnector`, pagination helpers, stream definitions, partition walking, and backfill window constants. These are the shared parts that keep every provider from rewriting the same paging, cursor, and record-shaping code. Without this file, extension code would either depend on private runtime paths or duplicate the connector framework, making the SDK harder to use and easier to break.


### Surface extensions
Public surface-related types, constants, errors, and helpers for building user-facing extension surfaces.

### `core/src/ufo/sdk/surfaces.py`

`other` · `cross-cutting, when surface extensions or SDK users import public surface APIs`

A “surface” is an outside place where UFO can meet users or other systems, such as a portal, inbox, or integration. Surface extensions need many shared building blocks: route definitions, context objects, writeback objects, transcript types, credential request types, terminal events, and small helper functions for formatting or recognizing messages. Without this file, extension authors would have to know the project’s internal folder layout and import pieces from many different modules. That would make extensions more fragile, because an internal file move could break outside code.

This module does not create new behavior of its own. It works like a clearly labeled front desk. It imports selected names from deeper internal modules and re-exports them under `ufo.sdk.surfaces`, which is the public import path. For example, an extension can import `SurfaceSpec`, `SurfaceRoute`, `SurfaceContext`, and `Writeback` from here instead of reaching into `ufo.runtime.ext.surface` directly.

The long list is intentional. It defines what the SDK promises to expose for surface work: conversation summaries, queued arrivals, credential and connection request shapes, terminal frame types, transcript access helpers, workspace file support, and related constants. This keeps the public API explicit while respecting the project rule that package `__init__.py` files stay empty.
