# Public SDK extension capability facades  `stage-18.3`

This stage is shared support for extension writers. It is not where jobs run, schedules tick, or tools execute. Instead, it provides stable “front doors” into the SDK, so outside extension code can import approved names without depending on the project’s private folder layout. This is like giving users one clear service desk instead of sending them through staff-only corridors.

Each file is a small facade, meaning it mostly re-publishes selected types, constants, errors, or helper classes from deeper internal modules. jobs.py exposes the public job-related tools. scheduling.py exposes scheduling names from the internal scheduling system. objects.py gathers the object API names that extensions are allowed to use. skills.py provides the import point for skill-related SDK pieces, while the real behavior lives elsewhere. surfaces.py collects the main building blocks for “surfaces,” the places where extensions interact with users or workspaces. tools.py exposes public tool-related types. Together, these files keep extension code clean, stable, and insulated from internal refactors.

## Files in this stage

### Work and object facades
Stable SDK entry points for extension code that declares or consumes job and object-related public types.

### `core/src/ufo/sdk/jobs.py`

`other` · `cross-cutting`

This is a small “front desk” module for background jobs in the SDK. Instead of asking extension authors to import directly from deeper core files, it re-exports the job pieces they are meant to use: `JobSpec`, `WorkspaceCandidates`, and `owner_candidates`.

The main idea is to keep the public contract clean. If core internals move around later, extensions can keep importing from `ufo.sdk.jobs` without breaking. That is like giving visitors one official reception desk instead of making them learn the building’s back hallways.

The exported job tools help an extension describe background work. A `JobSpec` describes a job. `owner_candidates` is used when a job needs to say which workspaces currently have work waiting. It builds a database query each time the scheduler checks for work, so time-based rules can use the current time. `WorkspaceCandidates` represents the resulting workspace choices.

The comments also mention RLS, or row-level security, which is a database rule system that normally limits which rows can be seen. Core performs one controlled read that bypasses those rules so it can safely discover which workspaces need job dispatching.


### `core/src/ufo/sdk/objects.py`

`other` · `cross-cutting; used whenever extension or SDK code imports object-related public API names`

This file exists to make the project’s public interface clearer and safer. Extensions need to talk about “objects” in the UFO system: things with kinds, owners, references, list pages, detail views, and stores. The real definitions live deeper in modules such as `ufo.objects` and `ufo.conversations`, but extension authors should not have to know or depend on those internal locations.

Think of this file like a front desk. The actual offices are elsewhere, but visitors are told to come here because this is the stable, official entrance. If the internals move later, this file can keep presenting the same names to extensions.

There is no active logic here. It does not create objects, read files, call services, or transform data. It simply imports selected names and re-exports them under the SDK path. The repeated `as same_name` style makes the export explicit: these names are intentionally part of the public surface. Without this file, extension code would likely import from internal modules directly, making it more fragile when the core project changes.


### Scheduling and skill facades
Public import paths for scheduling utilities and skill-related SDK names backed by internal implementations.

### `core/src/ufo/sdk/scheduling.py`

`other` · `import time / cross-cutting SDK access`

This file is like a labeled front desk for scheduling features. The real scheduling code lives elsewhere, in `ufo.scheduling`, but outside users should not have to know that internal layout. Instead, they can import from `ufo.sdk.scheduling`, which is part of the public SDK surface.

The file re-exports a small set of scheduling-related pieces: a one-time schedule constant, a scheduled-task value object, the schedule store that extensions use through their extension context, task inspection information, and a helper for finding workspaces with tasks that are due. “Re-export” means it imports something from one module and makes it available under another module name, without changing it.

This matters because it gives the project room to reorganize its internal code later without breaking extension authors. If extensions were told to import directly from `ufo.scheduling`, any internal move or rename could break them. By keeping this thin public layer, the SDK can act as a stable doorway while the rooms behind it can change.


### `core/src/ufo/sdk/skills.py`

`io_transport` · `cross-cutting`

This is a small “front desk” module for the SDK. The real skill code lives in `ufo.skills.runtime`, but outside users should not have to know that internal path. Instead, they can import `RuntimeSkill` and `parse_skill_content` from `ufo.sdk.skills`, which is a cleaner and more stable public address.

A skill here means a runtime capability contributed by an extension. `RuntimeSkill` is the value object that represents that capability, and `parse_skill_content` reads in-memory skill text and turns it into that structured form. This file re-exports both names without changing them.

The comment explains an important project rule: package `__init__.py` files are kept empty, so public SDK names are exposed through small named modules like this one. Without this file, extension authors would either need to import from deeper internal modules, making their code more fragile, or the SDK would have no clear public skill import path.

An everyday analogy: this file is like a signpost in a building lobby. It does not do the work of the office upstairs, but it tells visitors the official door to use.


### Surface and tool facades
SDK doorways for user-facing surfaces and extension-declared tool types without exposing private module paths.

### `core/src/ufo/sdk/surfaces.py`

`other` · `cross-cutting import-time SDK access`

This file does not create new behavior. Instead, it acts like a labeled shelf in a toolbox: it collects tools from several deeper parts of the project and makes them available from one public place, `ufo.sdk.surfaces`. That matters because extension authors should not need to know the project’s internal folder layout just to write a surface. They can import the official names from this module and avoid depending on private implementation details.

A “surface” here means an integration point where UFO can deliver conversation turns, ask questions, request credentials, or write results back to some durable place. The file re-exports types such as `SurfaceSpec`, which describes what a surface offers; `SurfaceRoute`, which describes where messages go; `SurfaceContext`, which gives privileged context to handlers; and `Writeback`, which represents the two-step process of saving results. It also exposes related records such as turns, terminal frames, credential prompts, questions, transcript summaries, and errors.

The repeated `as SameName` imports are intentional. They make the public API explicit: these are the names this module promises to provide. If this file disappeared, users could still hunt through internal modules, but the clean SDK boundary for surface extensions would be gone.


### `core/src/ufo/sdk/tools.py`

`util` · `import time / extension development`

This module is like a front desk for the tool system. Instead of asking extension authors to know where every tool-related class lives inside the core code, it re-exports the small set of names they are meant to use: tool definitions, tool context, text and image content, tool results, and a connection-unavailable error. That matters because internal files can move or be reorganized over time, while this public import path can stay the same. Without this file, extensions would likely import directly from deeper internal modules such as `ufo.tools.context` or `ufo.tools.registry`, making them more fragile when the core project changes. There is no runtime logic here beyond normal Python imports. Its job is to define a clean boundary: extensions should write handlers against `ufo.sdk.tools`, not against core internals. The comment also explains why this lives in a named module rather than in `__init__.py`: the project disallows code in package initializer files, so public SDK surfaces are exposed through explicit files like this one.
