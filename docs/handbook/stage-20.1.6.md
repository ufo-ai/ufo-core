# SDK extension manifest, jobs, hub, and sandbox exports  `stage-20.1.6`

This stage is shared support for extension authors. It does not run the system’s main work itself. Instead, it provides stable “front doors” into the SDK, so outside code can import approved names without depending on internal folders that may change.

The manifest module exposes the types and constants used to describe an extension’s metadata, such as what the extension is and what it needs. The jobs module exposes the small set of names extensions use to declare background jobs, meaning work the system can run outside the immediate request flow. The scheduled_fire module provides helpers for working with scheduled task runs, or “fires,” including how they are named and read. The hub module republishes public hub-related types, giving code a clean way to talk about the hub-facing parts of the SDK. The sandbox module exposes sandbox capabilities: types, constants, and helpers for features that run in a controlled environment.

Together, these files act like a reception desk for the SDK: they guide users to safe, public tools while hiding the deeper machinery.

## Files in this stage

### Hub and Manifest Exports
Stable public import points for hub-facing SDK types and extension manifest metadata.

### `core/src/ufo/sdk/hub.py`

`other` · `import time / public SDK use`

This module is like a front desk for hub features. The real hub code lives elsewhere, mainly under `ufo.runtime.hub`, but users of the SDK should not have to know that internal layout. Instead, they can import names such as `Hub`, `InProcessHub`, `LiveFrame`, or `Reply` from `ufo.sdk.hub`.

The “hub” is the part of the system that represents live activity flowing through the runtime: messages arriving, replies being produced, activity updates, cost updates, terminal events, and similar frames. A frame is a small structured piece of information about something that happened. This file also re-exports `TextDelta`, which represents a chunk of text as it streams in or out.

The comment at the top explains an important design choice: the SDK package keeps its `__init__.py` empty because project rules forbid code there. So instead of putting public imports in the package root, the project exposes them through named modules like this one. Without this file, SDK users would need to import directly from internal runtime paths, making their code more fragile if the project reorganizes its internals later.


### `core/src/ufo/sdk/manifest.py`

`data_model` · `extension import time`

This file does not create new behavior. Instead, it gathers many manifest-related names from deeper parts of the codebase and re-publishes them in one stable place. Think of it like a front desk: extension authors should come here to ask for the official manifest types, rather than wandering through private back rooms where internal code may change.

The manifest is the structured description of what an extension provides: agents, tools, hooks, prompts, credential needs, setup steps, search providers, conversation slots, image previews, workspace changes, and similar pieces. Many of those pieces are defined in runtime modules because the system itself needs them while running. But outside extensions should not depend directly on those internal paths. This file protects that boundary by making `ufo.sdk.manifest` the public import path.

It also re-exports limits such as maximum text lengths and item counts. Those limits help extensions build valid manifests before the runtime rejects them. If this file were missing, extension code would either break or be forced to import from internal modules, making it more fragile when the project is reorganized.


### Job and Schedule Helpers
Public SDK doorways for declaring background jobs and working with scheduled task fires.

### `core/src/ufo/sdk/jobs.py`

`other` · `extension development and job registration`

This module is intentionally thin. It does not define new behavior of its own. Instead, it acts like a labeled shelf in a hardware store: extension authors can come here for the job-building pieces they are allowed to use, without needing to know where those pieces are stored inside the core system.

The main idea is to keep the public extension interface stable. Internally, job support lives in runtime modules such as candidate selection, extension context helpers, and manifest definitions. This file gathers the relevant names and re-exports them from `ufo.sdk.jobs`, which is easier and safer for outside extension code to depend on.

The re-exported pieces cover two important needs. First, they help an extension say which workspaces may have work to do. A workspace is a tenant-like area of data, and background jobs often need to run only where relevant data exists. Second, they expose job declaration types and constants, including a cursor key prefix used by page-change consumers to remember how far they have read.

Without this file, extension authors would import from core internals directly. That would make extensions more fragile, because an internal refactor could break them even if the intended public behavior had not changed.


### `core/src/ufo/sdk/scheduled_fire.py`

`util` · `cross-cutting`

This is a small doorway file. The real scheduled-fire logic lives deeper in the system, under the runtime extension code, but outside users should not need to know that internal layout. Instead, this file re-exports two public helpers: one that builds the key used to identify a scheduled run, and one that reads that key back to find the task it belongs to.

A “scheduled fire” is a run triggered by a schedule, like a cron job. Cron is a common way to say “run this task at fixed times,” such as every hour or every night. Each scheduled run needs a consistent admission key, which is like a labeled ticket: it tells the system what scheduled task this run is connected to. Without this public wrapper, callers would have to reach into runtime internals, making their code more fragile if the project reorganizes its folders later.

The file does not add new behavior. Its job is to provide a stable, friendly import path in the SDK. Think of it like a front desk: it does not create the forms itself, but it gives users the right official forms without making them search through the back office.


### Sandbox Capability Exports
Stable sandbox-related SDK exports for extension authors.

### `core/src/ufo/sdk/sandbox.py`

`other` · `cross-cutting import-time public API`

This module does not create new sandbox behavior itself. Instead, it gathers the pieces that an outside extension needs when it wants to work with UFO’s sandbox system and re-exports them under `ufo.sdk.sandbox`. A sandbox is an isolated place where code can run with controlled files, network access, user IDs, temporary folders, and other limits. This file is like a reception desk: it does not do the work in the back rooms, but it tells users where to pick up the official forms and tools.

The imports cover several groups of public items. Some protect file paths so sandboxed code cannot wander outside its allowed area, such as contained path helpers and `ContainmentError`. Some describe or operate the sandbox session itself, such as `Sandbox`, `SandboxSession`, `SandboxSpec`, `ExecResult`, and network proxy settings. Others expose fixed paths and environment names used inside sandbox runs, such as workspace, temporary directory, certificate, Playwright browser, and module locations. It also exposes carrier-related pieces, including `Carrier` and `CarrierSpec`, which are the seam where deployments can plug in different sandbox backends.

The important behavior is stability: callers can depend on this SDK module even if the internal implementation files move around later. Without this file, extension code would need to import from deeper implementation modules, making it more fragile and more tightly coupled to UFO’s internals.
