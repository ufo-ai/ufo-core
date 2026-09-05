# Public SDK workspace, billing, audience, and surface helpers  `stage-19.4`

This stage is shared support for people writing code against the public SDK. It is not the main work loop itself. Instead, it is like a set of labeled front doors into deeper parts of the system, so extension authors do not have to import from changing internal paths.

The accounting and balance modules expose spending summaries, billing balances, and related functions used by workspace screens and command-line spending tools. The seats module does the same for seat types and helpers, which describe who can occupy or use workspace capacity. The audience module collects tools for describing who a conversation is meant for, while subjects provides standard names and helpers for saying who can see a piece of data. Delivery_register publishes fixed text constants used by delivery registration. Hub gathers public hub-related types. Listings exposes listing and paging helpers, which help callers fetch results in chunks. Surfaces is the broad doorway for building channel integrations, collecting the types, constants, errors, and helpers needed to let UFO communicate through outside places.

## Files in this stage

### Spending and entitlements
Public SDK doorways for accounting summaries, billing balance tools, and seat-related workspace access helpers.

### `core/src/ufo/sdk/accounting.py`

`data_model` · `cross-cutting; active when SDK users import accounting or when spending reports are displayed`

This module is a public doorway, not a place where new calculations happen. Its job is to make the accounting parts of the project easy and stable for outside code to use. Instead of asking SDK users to know the deeper internal paths where billing models live, it re-exports the important names from those internal modules.

In plain terms, it is like a labeled shelf in a library. The books are stored elsewhere, but this shelf tells visitors, “If you need spending reports, usage exports, totals by dimension, or the micro-dollar conversion constant, get them here.”

The objects exposed here are used to describe and report workspace spending. For example, a surface can show `SurfaceContext.spend_rollup`, and the command-line tool `ufoctl spend` can print the same kind of totals. Keeping these names available through `ufo.sdk.accounting` means callers do not need to depend on the project’s internal folder layout. If the internal implementation moves later, this public import path can stay the same.

There are no functions in this file. It simply imports selected accounting-related values and types, then makes them available as part of the SDK’s named public surface.


### `core/src/ufo/sdk/balance.py`

`other` · `cross-cutting import-time public API`

This module is like a signposted front desk for balance-related billing features. The real work lives deeper in `ufo.runtime.billing.balance`, where the system knows how to read a workspace's prepaid balance, add credit after a payment, count charges, track auto top-ups, and report recent purchases. Instead of asking outside code to import from that internal runtime location, this file exposes the same names through the public SDK package.

That matters because extensions should not need to know the project's internal folder layout. A billing extension can import `read_balance`, `credit`, `set_auto_topup`, or the `Balance` and `Purchase` types from `ufo.sdk.balance` and trust that this is the intended public doorway. If the internal implementation moves later, this file can keep the public import path steady.

There is no extra logic here, no validation, and no data transformation. Each imported name is re-exported exactly as itself. In plain terms, this file does not decide what a balance means or when to top up an account; it only makes the core billing rules available to code that is allowed to use them.


### `core/src/ufo/sdk/seats.py`

`util` · `cross-cutting`

This module is like a front desk for seat-related features. In this project, a “seat” appears to mean a recorded user or membership slot, along with facts such as their email, admin status, and workspace access. The actual rules and data structures live in `ufo.runtime.seats`, but external extension code is expected to import them through `ufo.sdk.seats` instead.

That separation matters because it gives the project a public doorway. If outside code imported deep runtime files directly, later internal reorganizations could break it. By re-exporting selected names here, the project can say, “these are the seat tools you may rely on,” while keeping the implementation owned by the core runtime.

The file exposes `SeatEntry`, `Seats`, and helper functions such as `member_by_email`, `member_is_admin`, `member_workspaces`, and `workspace_domain`. The comment at the top explains the design: the core system keeps the validation rules for seat state, while an extension or job decides when to apply those rules. There are no functions defined here, no calculations, and no side effects beyond making these names available under the SDK namespace.


### Audience and visibility
Stable imports for conversation audience tools and subject helpers that describe data visibility.

### `core/src/ufo/sdk/audience.py`

`data_model` · `cross-cutting`

This file is a public doorway into the project's audience system. An “audience” here means who a conversation turn is meant for or visible to, such as a shared room, a specific room, or a foreign room. Instead of asking outside code to import these pieces from the internal runtime path, this file re-exports them through the SDK path.

Think of it like a front desk in a building. The actual offices are elsewhere, but visitors should not need to learn the hallway map. They come to the front desk and get the service they need. In the same way, code using the SDK can import `Audience`, `parse_audience`, `room_audience`, and related helpers from `ufo.sdk.audience`.

There is no new behavior here. The file simply points each public name at the real implementation in `ufo.runtime.turns.audience`. That matters because it creates a stable boundary: the project can reorganize internal runtime files later while keeping the SDK import path steady for users.


### `core/src/ufo/sdk/subjects.py`

`util` · `cross-cutting`

This file exists so outside code can talk about disclosure subjects without needing to know the internal runtime module where they are implemented. A “subject” here means an audience label: for example, something shared with the whole workspace, or something meant for one specific member. Think of it like putting a mailing label on each row of data so the system knows who is allowed to receive it.

The file does not create new behavior of its own. Instead, it re-exports four items from the runtime: the shared workspace subject, the prefix used for member-specific subjects, a helper that builds a member subject, and a helper that checks whether a subject means “shared.” This matters because it gives callers a stable SDK-level import path. If every caller reached into the runtime package directly, internal reorganizing would be harder and more fragile.

The comment at the top explains the larger idea: conversation audiences and disclosed rows use the same subject atoms. That lets other parts of the system decide whether a row belongs to a broad shared source or a narrower conversation thread by reading these standard audience labels.


### Workspace registries and catalogs
SDK re-exports for delivery-register constants, hub-facing types, and listing or paging helpers.

### `core/src/ufo/sdk/delivery_register.py`

`util` · `cross-cutting import time`

This is a very small “front desk” file. The real definitions live deeper inside the runtime, in `ufo.runtime.turns.delivery_register`, but outside code should not have to reach into that internal area directly. Instead, this file re-exports the public names `DELIVERY_REGISTER_BLOCK` and `SUBAGENT_RESULT_DESCRIPTION` from a simpler SDK path.

The delivery register is a shared instruction block used when a direct model call needs to write its output into the same place that the shell and subagent prompts already use. In plain terms, it helps keep results arriving in the expected mailbox. Without this public re-export, extensions would either duplicate that wording or import it from an internal module, making them more fragile if the project’s internal layout changes.

There is no runtime logic here beyond importing and re-naming the same objects. Its value is in setting a boundary: `ufo.sdk.delivery_register` is the supported public doorway, while the runtime module remains the internal storage room.


### `core/src/ufo/sdk/hub.py`

`other` · `import time and public SDK use`

This module exists to make the project’s public interface cleaner and safer. Instead of asking users to know where hub classes and event types live inside the internal package layout, it lets them import from `ufo.sdk.hub`. Think of it like a reception desk: visitors do not need to wander through the building to find the right office; the desk points them to the right people.

The hub appears to be the part of the system that reports or carries live activity frames, such as replies, text changes, terminal output, cost updates, and agent activity. This file re-exports those public building blocks from `ufo.runtime.hub` and also re-exports `TextDelta` from the harness interface models. A re-export means the name is imported here and then made available to users of this module as if it belonged here.

The comment at the top explains an important design rule: `ufo.sdk` uses named modules like this one for its public surface because package `__init__.py` files are intentionally kept empty. Without this file, extension authors would need to import directly from deeper internal paths, which would make their code more tightly tied to the project’s internal layout and more likely to break if files are reorganized.


### `core/src/ufo/sdk/listings.py`

`data_model` · `extension development and request handling`

When an extension answers a portal listing, it may need to return results in pages rather than all at once. This is like showing search results one screen at a time, with a bookmark that says where to continue next. This file exists so extension code can import the needed paging pieces from `ufo.sdk.listings` instead of reaching into deeper runtime modules.

The file does not define new behavior itself. Instead, it re-exports selected names from `ufo.runtime.listings`: `ListingCursor`, `ListingPage`, `MalformedCursor`, `page_of`, and `page_query`. Re-exporting means it imports something from another module and exposes it again under this module’s public name.

That matters because it creates a cleaner boundary. The runtime module can contain the actual implementation, while the SDK module acts as the official front desk for extension developers. If the internal layout changes later, callers that use this public SDK path may not need to change. Without this file, extensions would either need to know internal module paths or duplicate paging logic, both of which would make the system more fragile.


### Surface extensions
The public import surface for extension authors building integrations that connect UFO to outside channels.

### `core/src/ufo/sdk/surfaces.py`

`other` · `import time and extension development`

This file does not define new behavior of its own. Instead, it acts like a clearly labeled shelf in a toolbox: if someone is writing a surface extension, they can import the tools they need from `ufo.sdk.surfaces` without knowing where each tool lives inside the deeper runtime code.

A “surface” is an outer communication layer, such as a chat app, inbox, terminal-like workspace, or another place where users and agents exchange messages. Surface extensions need shared vocabulary: what a route is, what context a handler receives, how writeback works, how attachments and transcript records are represented, and what errors mean when credentials, connections, or workspaces are missing or invalid.

The project intentionally keeps `ufo.sdk` modules thin. The package `__init__.py` is empty by rule, so named modules like this one become the public import points. That matters because it separates the public SDK from the internal file layout. If the runtime later moves `SurfaceSpec` or `Writeback` to another internal module, extension code can keep importing from this file.

Without this file, extension authors would have to import from many internal modules directly. That would make extensions more fragile and harder to understand, because they would depend on implementation details instead of a stable public interface.
