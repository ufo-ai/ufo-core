# Public SDK package and common façade modules  `stage-17.1`

This stage is shared behind-the-scenes support for people who build on top of UFO. It creates the public SDK, meaning the stable set of import paths that outside extensions are meant to use. Instead of forcing extension authors to reach into deep internal files that may change, these modules act like a front desk: they point callers to the right tools while hiding the building’s back corridors.

The package marker, __init__.py, simply makes ufo.sdk importable. The context module exposes context tools, which carry request or runtime information through the system. The http module publishes safe request and response types and includes the approved helper for setting session cookies with the right limits. The o11y module exposes structured logging, a way to record events in a machine-readable form. Accounting, audience, hub, objects, and seats each re-export their area’s public types, errors, and helper functions. Together, these files form a stable façade over internal code, so extensions can depend on clear public doors rather than fragile implementation details.

## Files in this stage

### SDK package root
The package marker establishes the stable `ufo.sdk` import namespace used by the public façade modules.

### `core/src/ufo/sdk/__init__.py`

`other` · `import time`

In Python, a folder can act like an importable package when it contains an `__init__.py` file. This file is that marker for the `core/src/ufo/sdk` folder. Its job is quiet but important: it lets other parts of the project refer to code under this folder using package-style imports, such as importing something from `ufo.sdk`. At the moment, the file is empty, so it does not run setup code, expose shortcut names, or define any functions or classes. Think of it like a label on a drawer: the label does not contain the tools, but it tells Python that the drawer belongs to the organized toolbox. Without this file, depending on the Python version and packaging setup, imports from this directory could be less reliable or fail in environments that expect traditional package markers.


### Common extension utilities
These public modules expose cross-cutting SDK helpers for context access, HTTP handling, and structured observability.

### `core/src/ufo/sdk/context.py`

`other` · `cross-cutting import surface for extension code`

This file does not create new behavior. Its job is to make the project easier and safer to use from the outside. Instead of asking extension code to import context, credential, page, trajectory, and agent identity objects from many different internal locations, it presents them through one clear SDK path: `ufo.sdk.context`.

Think of it like a front desk in a large building. The people and services are located in many rooms, but visitors should not need to know the building map. They go to the front desk, and the front desk points them to the right thing.

The exported items cover the main information an extension handler may need while it is running: who the current agent is, what page or source records are involved, how to read or store scoped data, how to access declared credentials, how to inspect model or surface installation access, and how to refer to proposals or agent changes. It also exposes error types for cases such as trying to use a credential slot that was not set or not declared.

This matters because public SDK imports should stay stable even if the internal project layout changes. Without this file, extension authors would need to depend on internal module paths, making their code more fragile.


### `core/src/ufo/sdk/http.py`

`io_transport` · `request handling`

This file is a small public doorway for HTTP-related code in the SDK. Instead of asking route authors to import web framework classes directly from Starlette, it re-exports the few request and response classes they are meant to use: normal responses, HTML pages, JSON, redirects, streaming output, and plain text. That keeps the SDK surface stable and easy to understand. If the project later changes how it exposes HTTP tools, extension code can keep importing from `ufo.sdk` rather than reaching into lower-level libraries.

The file’s only real behavior is about session cookies. A cookie is a small piece of text the browser stores and sends back on later requests, often used to remember that a user has a session. Setting cookies carelessly can create security problems. This helper deliberately does not allow a `domain` setting, so the browser treats the cookie as “host-only.” In plain terms, a cookie set for one host cannot silently spread to a parent domain or sibling subdomain. The helper also always turns on `HttpOnly`, which blocks JavaScript from reading the cookie, and `Secure`, which means the browser only sends it over HTTPS. The only choice callers get is the `SameSite` policy, which controls when browsers send the cookie during cross-site navigation.

#### Function details

##### `set_session_cookie`  (lines 17–25)

```
def set_session_cookie(response: Response, name: str, token: str, *, samesite: Literal['lax', 'strict', 'none']) -> None
```

**Purpose**: Sets a session cookie on an HTTP response in the one approved, security-conscious way. Callers use it when they need the browser to remember a session token, but should not be allowed to loosen important cookie protections.

**Data flow**: It receives a response object, a cookie name, a session token, and a chosen `SameSite` value. It writes that cookie onto the response with `HttpOnly` and `Secure` forced on, and without any domain value. Nothing is returned; the response object is changed so that, when sent to the browser, it will ask the browser to store the cookie.

**Call relations**: When route code needs to attach a session to a response, this helper is the intended path. It hands the actual low-level work to Starlette’s `Response.set_cookie`, but supplies the safety settings itself so callers do not repeat or weaken them.

*Call graph*: 1 external calls (set_cookie).


### `core/src/ufo/sdk/o11y.py`

`io_transport` · `cross-cutting`

This is a small doorway file for observability, often shortened to “o11y,” which means tools that help people understand what a running system is doing. Its job is not to create new logging behavior. Instead, it exposes two existing logging functions, `log` and `warn`, through the public SDK path.

That matters because extensions should use stable, supported entry points. If an extension imported directly from the internal `ufo.o11y` module, it would be tied to an implementation detail that might change later. By importing from `ufo.sdk.o11y`, extension code can say, in effect, “I want the official SDK logging tools,” while the core project remains free to reorganize its internals.

The file works like a signposted counter at a reception desk: it does not manufacture the service itself, but it tells outsiders where to ask. `log` is for recording normal structured events, and `warn` is for recording warnings. “Structured” means the messages can carry organized fields, not just plain text, making them easier for machines and people to search, filter, and understand.


### Domain façades
These modules provide stable public import points for SDK domain concepts such as accounting, audiences, hubs, objects, and seats.

### `core/src/ufo/sdk/accounting.py`

`data_model` · `cross-cutting SDK import`

This file is like a clearly labeled shelf at the front of a store. The actual accounting tools live deeper inside the project, in `ufo.accounting`, but users of the public SDK should not have to know that internal layout. Instead, they can import the approved accounting pieces from `ufo.sdk.accounting`.

The objects exposed here describe and summarize spending information. For example, a surface can show a workspace spend rollup through `SurfaceContext.spend_rollup`, and the command-line tool `ufoctl spend` prints the same kind of totals. The file re-exports value objects such as `SpendReport`, `SubjectTotal`, and `DimensionTotal`, plus constants and helper functions like `MICRO_USD_PER_USD` and `metered_workspaces`.

There is no new logic here. Nothing is transformed, validated, saved, or fetched. The important job is API stability: code outside the project can depend on this named SDK module, while the project remains freer to organize its internal modules. Without this file, SDK users would need to import from internal accounting modules directly, which would make their code more fragile if the internal structure changed.


### `core/src/ufo/sdk/audience.py`

`data_model` · `cross-cutting`

This file does not define new behavior itself. Instead, it re-exports audience-related names from the deeper `ufo.audience` module so SDK users can reach them through `ufo.sdk.audience`. In plain terms, it is like a front desk: the real work happens elsewhere, but this is the address people are meant to use.

The audience tools describe who a message, room, or conversation is meant for. That matters because systems that deal with conversations often need clear boundaries: who can see something, who is included, and how to interpret an audience string or value. Without a stable public module like this, outside users would need to know the project’s internal layout and import from lower-level files directly. That would make future refactoring risky, because moving internal code could break user code.

The file exposes the `Audience` value, a shared audience constant, and helper functions for building or parsing audiences for rooms, foreign rooms, conversations, members, and subjects. Its main purpose is API stability and clarity: it gathers the audience vocabulary into one easy-to-find SDK surface.


### `core/src/ufo/sdk/hub.py`

`other` · `import time / public API access`

This file does not define new behavior. Its job is to make the SDK easier and safer to use from the outside. Think of it like a reception desk: the real offices are elsewhere, but visitors get the names they need from one clear place.

The project keeps `ufo.sdk` as a set of small named modules because its package initializer, `__init__.py`, is intentionally empty. That means public SDK names need to live in files like this one. Here, `hub.py` re-exports hub-related objects from their internal homes.

The exported names describe the pieces a hub extension may need: `Hub` is the protocol, or expected shape, for hub implementations; `InProcessHub` is a ready-made hub that runs inside the same program; and types like `LiveFrame`, `ToolCall`, `Terminal`, `Parked`, `CostTick`, and `SkillLoad` represent live events or states moving through the hub. It also exposes `TextDelta`, a text update type used by model output.

Without this file, SDK users would have to import these names from deeper internal paths such as `ufo.hub` or `ufo.models.interface`. That would make outside code more tightly tied to the project’s internal layout, so later refactors would be more likely to break users.


### `core/src/ufo/sdk/objects.py`

`other` · `cross-cutting import-time SDK surface`

This file exists to keep a clean boundary between outside extension code and the core internals of the project. Instead of telling extension authors to import directly from places like `ufo.objects` or `ufo.conversations`, it gathers the approved object-related names and re-publishes them from one stable SDK module. Think of it like a front desk: visitors do not need to know which back office stores each form; they ask at the desk and receive the official version.

The file does not create new behavior. It imports constants, data types, error classes, and helper functions from the internal object system, then exposes them under the same names. These include object identifiers, ownership models, list and detail shapes, object store interfaces, and errors such as needing admin rights or asking for an unsupported action.

This matters because extensions can depend on this public surface without being tied to the internal layout of the codebase. If the core team later reorganizes internal files, they can keep `ufo.sdk.objects` stable and avoid breaking extension code. The comment at the top also explains why this lives in a named module rather than `__init__.py`: the project forbids code in package initializer files, so public SDK exports are placed in explicit modules like this one.


### `core/src/ufo/sdk/seats.py`

`other` · `cross-cutting import time`

This file is like a clearly labeled shelf in a toolbox. The actual seat logic lives elsewhere, in `ufo.seats`, but outside code should not have to know that internal location. Instead, extension authors can import from `ufo.sdk.seats`, which is meant to be part of the public software development kit, or SDK: the supported interface other code is expected to use.

The items re-exported here describe and work with “seats,” meaning membership or access slots in a workspace-like system. They include data shapes such as `SeatEntry` and `SeatSnapshot`, the main `Seats` object, and error types such as `SeatLimitReached`, `UnknownMember`, and `LastAdminSeatRevocation`. It also exposes helper functions such as `admin_conversation` and `member_workspaces`, which come from the core seat rules.

The important design choice is separation. Core code owns the rules about seats, while SDK users get a clean import path. If this file disappeared, existing extensions that import seat tools from `ufo.sdk.seats` would break, even though the underlying logic still exists.
