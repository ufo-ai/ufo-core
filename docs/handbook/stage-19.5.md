# SDK package shell and operational utility re-exports  `stage-19.5`

This stage is shared behind-the-scenes support for people writing code against the SDK. An SDK is the public “toolbox” the project offers to extensions and outside code. These files mostly do not create new behavior. Instead, they act like labeled doors in a building: stable import paths that lead to the real machinery elsewhere, even if the internal layout changes later.

The package marker, __init__.py, simply tells Python that ufo.sdk is a package that can contain importable modules. accounting.py opens a public door to spending and usage records, such as reports and totals. listings.py exposes tools for paged lists, where large result sets are delivered in manageable chunks. o11y.py gives extensions approved logging and metrics tools, so they can report what happened and how much work was done. scheduled_fire.py re-exports helpers for cron-like scheduled runs. jobs.py exposes supported names for declaring background jobs. skills.py provides the public path to skill-related tools. Together, these files keep extension code simple, stable, and separated from private internals.

## Files in this stage

### SDK package entrypoint
The package marker establishes the public SDK namespace before any stable re-export modules are used.

### `core/src/ufo/sdk/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means other parts of the project can write imports that start with `ufo.sdk` and then reach the real modules inside this directory. Think of it like a label on a drawer: the label does not contain the tools, but it tells Python that the drawer belongs in the organized set of project code. Because this file is empty, it does not run setup code, expose shortcut names, or change how the SDK works. Its value is structural: without it, depending on the Python version and packaging setup, imports for this part of the project could fail or behave differently.


### Operational utility re-exports
These modules expose stable SDK import paths for cross-cutting runtime concerns such as accounting, listings, observability, and scheduled execution.

### `core/src/ufo/sdk/accounting.py`

`io_transport` · `cross-cutting`

This file is a small public doorway into the accounting part of the system. The real accounting definitions live in `ufo.accounting`, but users of the SDK are meant to import them from `ufo.sdk.accounting`. That keeps the public interface tidy and stable, like a shop counter where customers pick up approved items instead of walking into the storeroom.

The objects re-exported here describe spending and usage information: reports for members and agents, totals by subject or dimension, a usage export shape, and a constant for converting dollars into micro-dollars. The file also exposes `metered_workspaces`, which is likely used to identify or work with workspaces whose usage is measured for billing or reporting.

Nothing new is calculated here. There are no functions or classes defined in this file. Its job is to clearly say, “these accounting names are part of the supported SDK surface.” Without this file, code using the SDK would either fail to import these names from `ufo.sdk.accounting`, or would have to depend directly on internal modules, making future refactors more likely to break users.


### `core/src/ufo/sdk/listings.py`

`util` · `cross-cutting`

Portal listings can be too large to return all at once, so the system uses keyset paging: a way to return one page of results plus a cursor that says where the next page should start. This file is a small public-facing doorway for that feature. Instead of asking extension code to import directly from the internal `ufo.listings` location, it exposes the important pieces through `ufo.sdk.listings`.

That matters because SDK users need a clear and stable import path. If the internal code is later moved or reorganized, this file can keep the public API looking the same. It is like a front desk: visitors do not need to know which office a tool really lives in; they can ask at the public desk and get the right thing.

The names it makes available are `ListingCursor`, which represents a position in a paged result set; `ListingPage`, which represents one returned page; `MalformedCursor`, an error for an invalid cursor; and the helpers `page_of` and `page_query`, which support building paged listing responses. There are no local functions or classes here, only re-exports.


### `core/src/ufo/sdk/o11y.py`

`io_transport` · `cross-cutting`

This file is a small but important boundary between extension code and the core observability system. “Observability” means the signals a system gives off so people can understand what it is doing, such as logs, warnings, timing profiles, and metrics counters.

Instead of implementing logging or metrics itself, this file re-exports a few approved tools from `ufo.o11y`: `log`, `warn`, `emit_metric`, and `turn_profile`. That makes them available through the SDK, which is the public surface intended for extensions.

The key idea is control. Extensions can emit metrics, but only by using names that the core system already knows about. This prevents every extension from creating its own surprise metric names. Without that rule, dashboards and alerts could become messy and unreliable, like a warehouse where everyone invents their own labels for the same boxes.

So this file acts like a clearly marked service counter. Extensions come here to record what happened, while the core system keeps ownership of the official logging and metric machinery.


### `core/src/ufo/sdk/scheduled_fire.py`

`io_transport` · `cross-cutting`

This file is a small public doorway. The real scheduled-fire logic lives in `ufo.ext.scheduled_fire`, but outside users should not have to know that internal location. Instead, they can import from `ufo.sdk.scheduled_fire`, which is a cleaner and more stable SDK-facing path.

A “scheduled fire” is a run triggered by a schedule, similar to how a cron job starts work at a set time. These runs need an admission key: a predictable identifier that ties the scheduled run back to the task it belongs to. This file exposes two pieces of that process. `scheduled_fire_key` builds the key used for a scheduled run, and `scheduled_fire_task_id` reads such a key back to find the task identifier.

The important point is separation. The extension module can contain the real implementation, while this SDK file acts like a signposted front desk. If internal code moves later, callers using the SDK path can keep working as long as this re-export is updated. Without this file, users would need to import from a deeper internal module, making their code more fragile and harder to understand.


### Extension capability re-exports
These modules provide supported public doorways for extension authors to declare jobs and use skill-related SDK tools.

### `core/src/ufo/sdk/jobs.py`

`other` · `extension definition`

This module is like a small, clearly labeled shelf in a workshop: extension authors come here to pick up the official tools for background jobs, instead of rummaging through private core code. It does not define new behavior itself. Its job is to make the public API stable and easy to find.

The most important idea here is workspace discovery. A background job may only need to run for certain workspaces. The exported `owner_candidates` helper lets an extension describe how to find those workspaces from its own database tables. Core can then run that query in the special place where it is allowed to look across workspaces, and use the result to decide where to dispatch the job.

The file also re-exports several ready-made workspace candidate helpers, such as workspaces connected to an integration, workspaces with seated members, workspaces with unseeded agents, and workspaces with untitled conversations. Finally, it exposes `JobSpec`, the public shape used to describe a job. Without this file, extension code would either depend on internal module paths, which are more likely to change, or duplicate knowledge that should stay centralized.


### `core/src/ufo/sdk/skills.py`

`io_transport` · `cross-cutting`

This module is like a clearly labeled front desk for the SDK’s skill features. The actual work lives in `ufo.skills.runtime`, but outside code should not need to know that internal location. Instead, it can import `RuntimeSkill`, `parse_skill_content`, and `skill_mount_root` from this public SDK module.

That matters because internal code can move around over time. If every extension imported directly from the deeper runtime package, a refactor could break them. By re-exporting these names here, the project offers a stable doorway: extension code can say, in effect, “give me the public skill API,” without depending on the project’s internal folder layout.

The file is deliberately tiny. It imports the skill value object, the parser that reads skill definitions from in-memory content, and the helper that identifies where skills are mounted. The comment also explains a project rule: `ufo.sdk` keeps its package initializer empty, so public SDK features are exposed through named modules like this one rather than through `__init__.py`.
