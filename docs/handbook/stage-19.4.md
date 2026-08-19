# SDK extension authoring and execution facades  `stage-19.4`

This stage is shared behind-the-scenes support for people who write UFO extensions. It is not the main work loop itself. Instead, it provides stable “front doors” into the system, so extension code can use approved names without depending on private internal files that may move or change.

The manifest module is where an extension describes itself: its declared features, constants, and helper rules. The tools and skills modules expose the building blocks an extension can offer to the system, such as callable actions and higher-level abilities. The jobs module lets an extension declare background work and choose which workspaces it should run in. The http module supports extension web routes by providing request and response types, plus a safe helper for setting session cookies. The context module gives running extension code access to its execution context, meaning the useful information and services available while it runs. The scheduled_fire module exposes helpers for scheduled triggers. Together, these files act like a clean control panel over deeper machinery.

## Files in this stage

### Runtime Access Facades
Stable public imports for extension runtime context and HTTP route handling.

### `core/src/ufo/sdk/context.py`

`other` · `cross-cutting import-time API surface`

This file is like a front desk for the SDK's context API. Extensions often need to know things such as who the current agent is, what conversation facts are available, what pages or members are involved, how to read sources, or how to access declared credentials. Those pieces are defined in several internal modules, but this file gathers them under one stable public path: `ufo.sdk.context`.

There is no new logic here. It does not calculate, store, or transform anything. Instead, it re-exports names from lower-level modules. A re-export means “make this imported thing available from here too.” That matters because extension code can rely on a clean, documented SDK surface instead of reaching into internal package paths such as `ufo.ext.context` or `ufo.credentials` directly.

Without this file, extension handlers would have to import context types from many different places, and any internal reorganization could break them. With it, the project can move internal code around later while keeping the public import path steady. In short, this file protects users from the project’s internal layout and makes extension code easier to read.


### `core/src/ufo/sdk/http.py`

`io_transport` · `request handling`

This file is like a small front desk for HTTP code. Instead of making extensions reach directly into Starlette, the web framework underneath, it re-exports the request, response, file-upload, and form-data classes that route handlers are expected to use. That keeps the public interface simple: a route receives a Request and returns a Response, such as HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, or StreamingResponse.

The file also defines one important safety rule for session cookies. Cookies can be risky because a poorly scoped cookie may leak across subdomains or environments. The helper set_session_cookie deliberately does not accept a domain setting. That means cookies set through it are “host-only”: the browser sends them only back to the exact host that set them, not to a wider parent domain. It also always turns on HttpOnly, which prevents browser JavaScript from reading the cookie, and Secure, which tells the browser to send it only over HTTPS.

Only the SameSite setting is left for the caller to choose. SameSite is a browser rule that controls when cookies are sent during cross-site navigation. In short, this file both defines the HTTP vocabulary extensions should use and enforces a project-wide safer way to attach session cookies.

#### Function details

##### `set_session_cookie`  (lines 23–34)

```
def set_session_cookie(response: Response, name: str, token: str, *, samesite: Literal['lax', 'strict', 'none']) -> None
```

**Purpose**: Sets a session cookie on an HTTP response using the project’s required safety defaults. It is meant to be the only normal way UFO code creates its own session cookies, so cookies stay host-only, HTTPS-only, and hidden from browser JavaScript.

**Data flow**: It receives a response object, a cookie name, a token value, and a SameSite choice. It passes those to the underlying response’s cookie-setting method, while always adding HttpOnly and Secure and never adding a domain. The response is changed in place so that, when sent to the browser, it includes the correct Set-Cookie header; the function itself returns nothing.

**Call relations**: Route or session-related code calls this when it needs to attach a session token to a response. The function then hands the actual low-level cookie writing to Starlette’s Response.set_cookie, but wraps it with UFO’s stricter rules so callers do not accidentally create a broader or less secure cookie.

*Call graph*: 1 external calls (set_cookie).


### Extension Declaration Facades
Public SDK doorways for declaring extension manifests, jobs, skills, and tools without depending on internal modules.

### `core/src/ufo/sdk/jobs.py`

`util` · `extension import and job registration`

This module is intentionally small: it does not create new behavior of its own. Instead, it re-exports a few job-related tools from internal parts of the system under the stable public name `ufo.sdk.jobs`. Think of it like a clearly marked service counter: extension authors come here for the approved tools, rather than wandering into the warehouse behind it.

The most important idea here is workspace selection. A background job should not blindly run everywhere. It needs to say, “these are the workspaces where I currently have work to do.” The exported `owner_candidates` helper and `WorkspaceCandidates` type support that pattern. An extension can build a database query that finds distinct workspace IDs from its own tables, and the core system can use that to decide where to dispatch the job.

The file also re-exports workspace helper functions from the extension context, such as workspaces connected to a connection, seated members, unseeded agents, or untitled conversations. Finally, it exposes `JobSpec`, the public description of a job. Without this file, extension code would have to import from core internals directly, making extensions more fragile when the project is reorganized.


### `core/src/ufo/sdk/manifest.py`

`data_model` · `extension import and manifest definition`

This file exists to keep the project’s public extension interface clean and stable. A manifest is the structured description of what an extension provides: agents, hooks, tools, credentials, conversation slots, image previews, workspace changes, and other capabilities. The real definitions live in several internal modules, but extension code should not depend on those internal paths because they may change.

Think of this file like a reception desk in a large building. Visitors do not need to know which back office contains each form; they ask at the desk and receive the official version. Here, the “forms” are imported names such as `Manifest`, `HookSpec`, `CredentialSlot`, `ConversationTask`, and `WorkspaceChange`.

There is no new logic here. The file simply re-exports selected names from other parts of the system under one supported location. This matters because it gives extension developers one reliable place to import from, while allowing the core project to reorganize its internal code later without breaking those extensions. The comment at the top also explains a project rule: package `__init__.py` files are kept empty, so public API surfaces are provided through named modules like this one.


### `core/src/ufo/sdk/skills.py`

`util` · `cross-cutting import-time SDK surface`

This module is like a signposted doorway into the skill system. The real skill logic lives deeper in the project, under `ufo.skills.runtime`, but outside code should not have to know that internal layout. Instead, it can import from `ufo.sdk.skills`, which is a cleaner and more stable public address.

It exposes three things: `RuntimeSkill`, the value object that represents a skill available at runtime; `parse_skill_content`, a helper that reads in-memory skill text and turns it into the project’s skill representation; and `skill_mount_root`, a helper related to where contributed skills are mounted or rooted.

The comment at the top explains an important design choice: the SDK package keeps its `__init__.py` files empty because project rules forbid code there. So instead of putting public imports at the package root, the SDK uses small named modules like this one. If this file disappeared, extension code would either lose this convenient public import path or be forced to depend directly on internal modules, making future refactors harder and more likely to break users.


### `core/src/ufo/sdk/tools.py`

`other` · `cross-cutting; used when extensions import SDK tool APIs`

This module is like a front desk for extension authors. Instead of asking outside code to know where every tool type, task helper, or content class lives inside the core package, it gathers those names in one safe public place: `ufo.sdk.tools`.

The file does not define new behavior. It re-exports selected objects from deeper modules. That matters because internal code can move around later, while extensions can keep importing the same public names. Without this layer, plugin and extension code would depend on internal paths, making the system harder to change without breaking users.

The exports cover the main pieces needed to declare and run tools: tool definitions, tool contexts, text and image result content, file-change limits, task-running helpers, and timeout/task-handle support. A notable part is `run_task`, which gives tools a shared way to start longer-running detached work. For example, the built-in bash tool can keep a command running after the immediate caller’s time budget is exceeded, while still reporting it through consistent task handles.

The file also reflects a project rule: package `__init__.py` files stay empty, so named modules like this one provide the public SDK surface.


### Scheduled Fire Helpers
Stable public access to helpers that trigger scheduled extension work.

### `core/src/ufo/sdk/scheduled_fire.py`

`util` · `cross-cutting`

This file is a small public doorway. The real scheduled-fire logic lives in `ufo.ext.scheduled_fire`, but this SDK file re-exports the two pieces that callers are meant to use: one helper that builds the key used to identify a scheduled run, and one helper that reads such a key back into the task it belongs to.

A “scheduled fire” is a run that is triggered by a schedule, like a cron job. Cron is a common way to say “run this task at this time or interval.” Each scheduled run needs a dependable admission key, much like a ticket at a door: the system can use the ticket to recognize what scheduled task is trying to enter and later trace the run back to its source task.

Without this file, users would need to import these helpers from the deeper `ufo.ext` package. That would make the internal layout part of the public contract and make future refactoring harder. By re-exporting the names here, the project gives users a stable SDK import path while keeping the implementation elsewhere.
