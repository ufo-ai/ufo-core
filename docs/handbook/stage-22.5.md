# Public SDK workspace, surface, visibility, and object facades  `stage-22.5`

This stage is the public front door of the SDK, the software kit used by extensions and outside code. It is shared support, not a main work loop. Its job is to hide the project’s internal layout and give users steady import paths that do not change when the inside is reorganized.

Each file is a small facade, like a labeled service window. audience.py exposes audience tools, while subjects.py exposes the standard names used to describe who can see a piece of data. seats.py re-exports seat objects and helpers. hub.py gathers the hub protocol, live event types, and in-process hub, so extensions can talk to the event system. listings.py provides listing and paging helpers for returning results in chunks. objects.py exposes object types and helpers. scheduled_fire.py publishes helpers for scheduled actions. surface_token.py offers tools for creating and checking permanent surface link tokens. surfaces.py gathers the main contracts for surface extensions. untrusted.py lets extensions mark outside text as untrusted in the same way the core system does.

## Files in this stage

### Visibility and participants
Public SDK facades for audience, seat, and subject-visibility names used by workspace-facing extensions.

### `core/src/ufo/sdk/audience.py`

`data_model` · `cross-cutting`

This file does not create new audience logic itself. Instead, it re-exports selected constants, types, and helper functions from the internal `ufo.audience` module so outside code can import them through `ufo.sdk.audience`.

An “audience” here means the intended visibility or membership boundary for a conversation or room: who can read it, who it belongs to, or whether it is shared. The imported names include the `Audience` value, constants such as `SHARED_AUDIENCE`, and helper functions for building, parsing, and reading audience labels.

The reason this file matters is API stability. Internal modules can be reorganized later, but users of the SDK should not have to change their imports every time the project moves files around. This file is like a public signpost: it says, “these are the audience tools the SDK promises you can use.”

Without this file, SDK users would need to import directly from internal project paths. That would make their code more fragile and blur the boundary between the public SDK and the private implementation.


### `core/src/ufo/sdk/seats.py`

`data_model` · `cross-cutting`

This module is like a clearly labeled front desk for seat-reporting features. The real seat logic lives in `ufo.seats`, but outside code should not need to know that internal location. Instead, it can import from `ufo.sdk.seats`, which is part of the public software development kit, or SDK — the supported interface meant for people building on top of this project.

The objects exposed here describe seat state and safe ways to work with it. A “seat” is likely a counted user or workspace membership used for reporting, licensing, or access tracking. `SeatEntry`, `Seats`, and `SeatSnapshot` are re-exported so callers can describe current or saved seat information. `member_workspaces` is also re-exported as a helper for building or interpreting seat candidates.

Nothing is calculated in this file. There are no functions or classes defined here. Its value is stability and clarity: if the core implementation moves around later, extension code can keep importing from this same SDK path. Without this file, outside integrations would have to reach into internal modules directly, which would make them more fragile when the project changes.


### `core/src/ufo/sdk/subjects.py`

`util` · `cross-cutting`

In this project, a “subject” is a label for an audience. For example, a row may be disclosed to the whole shared workspace, or only to a particular member. This file does not invent those labels itself. Instead, it re-exports them from the internal `ufo.subjects` module under the SDK path.

That matters because outside code should not need to know where the internal implementation lives. It can import `SHARED_SUBJECT`, `MEMBER_SUBJECT_PREFIX`, `member_subject`, and `subject_shared` from `ufo.sdk.subjects` and use them as the official vocabulary for visibility. Think of it like a public signpost: the actual road is elsewhere, but this is the sign people are meant to follow.

The imported pieces cover two common jobs. One is building or recognizing the special shared-workspace audience. The other is building or recognizing member-specific audience labels. Other parts of the system can then compare these labels when deciding whether data belongs to a source-wide audience or to a conversation/thread audience. Without this file, SDK callers might import private internals directly, making future refactors harder and breaking the promise of a stable public interface.


### Workspace objects and activity
Stable import doorways for hub events, object helpers, listings, paging, and scheduled-fire utilities.

### `core/src/ufo/sdk/hub.py`

`other` · `cross-cutting import time`

This module does not define new behavior of its own. Instead, it gathers a set of hub-related names from deeper inside the project and re-exports them as part of the public `ufo.sdk` interface. That matters because outside users should not have to know the project’s internal folder layout to build a hub extension or listen to live activity. They can import from this named SDK module instead.

The hub is the part of the system that reports or receives live progress information, such as tool calls, cost updates, terminal output, skill loading, and text streamed from a model. Think of this file like a labeled shelf at the front of a workshop: the tools are made elsewhere, but this shelf is where visitors are told to pick them up.

The comment explains an important packaging rule: the SDK package keeps its `__init__.py` empty, so public exports live in specific modules like this one. This avoids putting code in package initialization while still giving users a clear and supported import path.


### `core/src/ufo/sdk/listings.py`

`other` · `cross-cutting`

When an extension answers a portal listing, it may need to return results in pages instead of all at once. This file exists so extension code can import the needed paging tools from `ufo.sdk.listings`, rather than reaching directly into the project’s internal `ufo.listings` module. That matters because an SDK, or software development kit, is meant to be the safe public surface outsiders build against. If the internal layout changes later, this file can keep the public import path steady.

There is no new paging logic here. It simply points a few public names to their real implementations: `ListingCursor`, `ListingPage`, `MalformedCursor`, `page_of`, and `page_query`. In everyday terms, it is like a reception desk that sends visitors to the right room, while still giving them one familiar address to remember.

Without this file, extension authors would either have to know the internal module structure or use less stable imports. That would make plugins more fragile when the project reorganizes its code.


### `core/src/ufo/sdk/objects.py`

`data_model` · `cross-cutting`

This file solves a boundary problem. The project has internal modules where object concepts are actually defined, but outside code should not depend on those internal paths. If extensions imported directly from the internals, a future refactor could break them even if the public behavior stayed the same. This module acts like a shop window: it displays the approved names that outside users are meant to use.

There is no new business logic here. Every line imports a type, constant, exception, or helper from another module and exposes it under the same name. These include object kinds, object references, owners, list pages, store interfaces, access-related concepts, and errors such as unsupported object actions. It also re-exports related concepts from agents, conversations, credentials, and object scoping, because object code often needs those names together.

The opening comment explains why this exists as a named module rather than putting imports in `__init__.py`: the project forbids executable code in package `__init__.py` files. So `ufo.sdk.objects` becomes the intentional public import path. Without this file, extension authors would either lack a clear SDK entry point or would need to use internal modules that are not meant to be stable.


### `core/src/ufo/sdk/scheduled_fire.py`

`other` · `cross-cutting import-time SDK surface`

This file is a small public doorway. The actual scheduled-fire logic lives in `ufo.ext.scheduled_fire`, but users of the SDK can import the key-building and key-reading helpers from this simpler SDK path instead. A “scheduled fire” is a timed trigger, like a cron job, that starts work at a planned time. Each such trigger needs a consistent admission key so the system can recognize which scheduled run belongs to which task. This file re-exports two helpers: one that builds that key, and one that reads a task id back out of it. The important point is stability. If the internal location of the real implementation changes later, code using the SDK does not have to change as long as this public re-export keeps the same names. Without this file, callers would need to import from the extension module directly, which ties them to internal project layout and makes future refactoring riskier.


### Surface extension boundaries
Public SDK wrappers for surface tokens, surface extension contracts, and marking untrusted third-party content.

### `core/src/ufo/sdk/surface_token.py`

`util` · `cross-cutting`

This module exists to make the public software development kit, or SDK, easier and safer to use. An SDK is the set of functions outside code is meant to call. Instead of asking users to know the internal file layout of the project, this file re-exports two token functions from `ufo.surface_token`: one to mint, meaning create, a surface token, and one to verify, meaning check, such a token.

The comment at the top explains an important design rule: a surface can mint and verify its own permanent link addresses here, but it does not hold the deploy's token secret directly. In plain terms, this keeps the public-facing layer from becoming the place where sensitive secret material is stored.

There is no new algorithm in this file. It is like a clearly labeled front desk that points visitors to the right service behind the scenes. That still matters: if this file were missing, callers using the public SDK path would lose a stable import location, even though the deeper implementation might still exist.


### `core/src/ufo/sdk/surfaces.py`

`other` · `import time / extension development`

A “surface” is an outside-facing integration point: a place where an extension can receive events, show information, or write results back into UFO. Extension authors should not have to know where every internal type lives. This file solves that by acting like a tidy front desk. Instead of importing from many internal modules, users can import the supported surface API from `ufo.sdk.surfaces`.

The file does not create new behavior of its own. It re-exports names from the real implementation modules. These include the main building blocks for registering a surface, such as `SurfaceSpec` and `SurfaceRoute`; privileged context objects such as `SurfaceContext`; writeback-related types such as `Writeback` and `SharedArtifact`; view objects that describe conversations, agents, installations, credentials, and connections; transcript and record types; and the errors an extension may need to catch.

This matters because it draws a clear line between the public SDK and the project’s internal layout. Internal files can move or be reorganized, while extension code can keep importing from this stable module. The comment also explains why this module exists as a named file: package `__init__.py` files are intentionally kept empty, so public SDK entry points live in explicit modules like this one.


### `core/src/ufo/sdk/untrusted.py`

`util` · `cross-cutting`

This file exists to keep one shared meaning for “untrusted” text across the project. Untrusted text is content that came from outside the system, such as a tool’s output, a provider response, or another agent’s message. That kind of text can contain misleading instructions, so the system needs to clearly fence it off instead of treating it like trusted program guidance.

The file does not define new behavior. It imports `wall` from `ufo.untrusted` and exposes it again as `ufo.sdk.untrusted.wall`. In plain terms, it is like putting the same warning label dispenser at the SDK counter that the core system already uses behind the scenes. Extension authors can use this public SDK path, while the project still has only one real definition of what the warning label means.

Without this file, SDK users might need to import from a more internal module, or worse, create their own slightly different way to mark unsafe outside text. That would make it harder for the rest of the system to recognize and treat fenced-off content consistently.
