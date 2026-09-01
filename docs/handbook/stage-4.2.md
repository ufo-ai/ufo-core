# Extension-Provided Objects, Hooks, Backends, and Stores  `stage-4.2`

This stage is shared behind-the-scenes support for add-ons. It is how UFO learns what extra abilities are installed, and how those add-ons can react while the system is working. The core hooks file is the traffic controller: during a user turn, it runs extension callbacks before or after important actions, combines their answers, and decides whether a failed or slow callback should stop the action or just be recorded.

Several files are registration cards, called manifests. The Composio, connectors, Pipedream, Redis hub, and report-digest manifests tell the host what each extension provides, such as connector tools, login flows, web redirect routes, Redis-backed hubs, scheduled jobs, rebuild tools, and workspace object types. The Redis hub package file simply makes that extension importable.

The Slack hooks file adds special reactions around Slack connector use, such as setting the right bot identity and removing old connection buttons. The sample extension acts like a test model of the whole extension system, touching many public plug-in points so the project can prove they work together.

## Files in this stage

### Hook Runtime and Slack Reactions
Core hook dispatch is introduced first, followed by a concrete Slack extension that reacts to connector and account-link events.

### `core/src/ufo/runtime/ext/hooks.py`

`domain_logic` · `turn handling`

This file is the project’s “reaction chain” for extensions. An extension can register a hook, which is a small piece of code that runs when something happens, like “the user submitted a prompt” or “the agent is about to call a tool.” The HookChain keeps those hooks grouped by event and runs them in the pinned order, much like a line of reviewers who each get a chance to approve, edit, or add notes.

The main job is to turn many hook results into one clear decision. A hook may deny an action, change tool input, change tool output, or inject extra context text. Denials stop the chain immediately. Edits are folded from left to right, so each later hook sees the changes made by earlier hooks. Injected text is collected in order.

The file also protects the main turn from broken extensions. Each hook has a short timeout. For sensitive “gate” events, such as before tool use or user prompt submission, a failing non-best-effort hook fails closed, meaning the action is denied for safety. For less critical events, failures are swallowed after logging, so an extension bug does not crash the whole turn. It also checks that hooks only return outcomes allowed for their event, treating wrong outcomes as hook malfunctions.

#### Function details

##### `HookChain.__post_init__`  (lines 90–93)

```
def __post_init__(self) -> None
```

**Purpose**: This checks that every hook in the chain belongs to the same audience as the chain itself. The audience is the group or context the hook is allowed to speak to, so mixing audiences would risk sending extension behavior to the wrong place.

**Data flow**: It starts with the hook groups already stored on the HookChain. It flattens them into one list, compares each hook extension’s audience with the chain’s audience, and either leaves the object valid or raises an error if any hook does not match.

**Call relations**: This runs automatically after a HookChain is created. It acts as an early safety check before HookChain.fire is ever used, so the firing logic can assume that all bound hooks are meant for the same audience.


##### `HookChain.fire`  (lines 95–173)

```
async def fire(self, event: HookEvent, payload: HookPayload, turn: Turn | None, agent: Agent | None, speaker_member_id: UUID | None) -> HookResolution
```

**Purpose**: This runs all hooks registered for one event and combines their answers into a single result the engine can apply. It is used whenever the turn reaches a hookable moment, such as before tool use, after tool use, or when a prompt is submitted.

**Data flow**: It receives an event name, the event payload, optional turn and agent information, and the current speaker identity. It finds the hooks for that event, builds a HookContext for each one, gives each hook the latest version of the payload, waits up to the hook timeout, and checks whether the hook returned something allowed. A denial becomes an immediate denied result. Input or output changes replace the current value for later hooks. Injected text is collected. Hook failures either become a denial for strict gating events or are logged and skipped for best-effort or non-gating cases. At the end it returns a HookResolution containing the final denial, modified input or output, and injected text.

**Call relations**: The turn engine calls this at the exact moment an event needs extension reactions. For each hook, it creates a HookContext so the hook has the payload plus turn, agent, audience, and speaker information. It uses a timeout to prevent a hook from hanging the turn, uses dataclass replacement to pass modified payloads forward, and raises HookOutcomeNotAllowed internally when a hook gives an answer that does not make sense for that event. The returned HookResolution is what the surrounding engine uses to continue, block, modify, or enrich the turn.

*Call graph*: 5 external calls (__init__, __init__, __init__, timeout, replace).


### `extensions/slack/ufo_ext_slack/hooks.py`

`orchestration` · `cross-cutting: before connector tool use and after connection recording`

This file solves two user-facing polish and correctness problems for the Slack extension. First, when another connector sends a message into Slack, the generic connector code does not know which Slack bot user belongs to this workspace. This file looks up the bot user ID that the Slack surface previously saved, then rewrites the outgoing message text so the footer mentions the right bot. If that lookup is slow or fails, it deliberately does nothing, because a missing footer is better than blocking the message entirely.

Second, when a Slack user clicks a button to connect an outside service, they leave Slack, authorize on that service’s page, and may never return to the original Slack thread. After the connection is recorded, this hook finds the original Slack button message and changes it into a settled state that names the connected account. This is like replacing a “Sign up now” flyer with a receipt once the signup is done.

The important theme is safety. These hooks improve messages, but they should not take away the main action. Attribution failures fall back to the generic behavior, and connect-button cleanup happens only after the connection already exists.

#### Function details

##### `attribute_connector_send`  (lines 38–52)

```
async def attribute_connector_send(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook checks whether an upcoming external tool call is really a Slack send, and if so, tries to add a footer that mentions the workspace’s Slack bot user. It is used so Slack messages can clearly say which bot sent or assisted with them.

**Data flow**: It receives a hook context containing the event payload. If the payload is a pre-tool-use event for the connector tool and the target looks like a Slack send, it asks `_mirrored_self_user_id` for the saved Slack bot user ID. If no valid ID is available, it returns nothing and leaves the tool input unchanged. If an ID is found, it rewrites the call’s arguments with `mention_attributed`, wraps the changed tool input in `ModifyInput`, and returns that as the hook outcome.

**Call relations**: This is the outer decision point for Slack send attribution. It calls `is_slack_send` to avoid touching unrelated connector calls, then calls `_mirrored_self_user_id` to get the bot identity, and finally calls `mention_attributed` to produce the edited message arguments. The hook system can then pass the modified input onward to the connector tool.

*Call graph*: calls 1 internal fn (_mirrored_self_user_id); 3 external calls (__init__, is_slack_send, mention_attributed).


##### `_mirrored_self_user_id`  (lines 55–69)

```
async def _mirrored_self_user_id(ctx: HookContext) -> str | None
```

**Purpose**: This helper reads the Slack bot user ID that was previously saved in the extension’s own store. It exists so attribution can use a known local value instead of calling Slack during the send path.

**Data flow**: It receives the same hook context, then tries to read `SELF_USER_ID_STORE_KEY` from the extension store within a short time limit. If the read fails, times out, or returns something that is not a valid Slack bot user ID shape, it returns `None`. If the stored value is a valid-looking bot user ID, it returns that string.

**Call relations**: `attribute_connector_send` calls this when it needs to know which bot to mention in a Slack footer. Internally, it uses `asyncio.timeout` so the lookup cannot stall the hook for too long, `re.match` to check the stored ID format, and `log` to record failed reads without turning them into send-blocking errors.

*Call graph*: called by 1 (attribute_connector_send); 3 external calls (timeout, match, log).


##### `settle_connect_button`  (lines 72–101)

```
async def settle_connect_button(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook updates a Slack connect button after an external account connection has successfully been recorded. It helps the Slack thread reflect reality: the button should no longer invite the user to do something they have already done.

**Data flow**: It receives a hook context and expects the payload to describe a recorded connection, including the provider, account, label, and owning member. It uses the member and provider to look up the saved Slack message for that connect button. If no saved message exists, it returns without changing anything. If a message is found, it reads the Slack bot token, validates the saved message data as a `ConnectMessage`, calls Slack-facing code to update the message with the connected account name, and then deletes the saved message reference from the store.

**Call relations**: This function is meant to run after the system has already recorded a connection. It uses `connect_message_key` to find the remembered Slack message, `ConnectMessage.model_validate` to turn the stored data back into the expected message shape, and `settle_connect_message` to perform the Slack update. If the payload is not a connection-recorded event, it raises an error because this hook was wired for the wrong kind of event.

*Call graph*: 3 external calls (model_validate, connect_message_key, settle_connect_message).


### Connector Extension Manifests
These manifests register connector-oriented extensions and describe their tools, login flows, prompts, and browser callback routes.

### `extensions/composio/ufo_ext_composio/manifest.py`

`config` · `startup`

This is the extension’s “front desk” file. When the UFO system loads the Composio extension, it needs one clear description of what the extension can do. This file builds that description as a Manifest, which is like a menu plus setup instructions.

The Composio service runs tools on its own servers and keeps each user’s account tokens there. That matters because this deployment does not need to receive or store those secrets. Most Composio toolkits can be reached through a general resolver, ComposioResolver, which can look up a toolkit by its short name. A smaller set of explicit connectors is also listed from CONNECTORS, mainly for cases where command-line credentials or a real provider host are needed.

The manifest creates one shared ComposioBroker. A broker is the middle layer that knows how to carry out connector actions without exposing raw account secrets. For each known connector, the file creates a ConnectorProvider with its login provider, display label, transfer hosts, and optional command-line credential forwarding. It also creates a single browser route for the OAuth consent step, which is the “sign in and grant access” flow used by many services.

Without this file, the extension would have code for talking to Composio, but the host system would not know how to discover it, route login traffic to it, or present its connectors to users.

#### Function details

##### `manifest`  (lines 24–53)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the Manifest object that registers the Composio extension with the host system. Someone uses this when the extension is loaded so the system can discover Composio connectors, their login behavior, and the route needed for browser consent.

**Data flow**: It starts with fixed extension details such as the name, version, known connector list, OAuth route path, and transfer hosts. It creates a shared ComposioBroker, a request forwarder for command-line credentials, one ConnectorProvider for each explicit connector, a ComposioResolver for the broader Composio namespace, and a RouteSpec for the OAuth callback-style browser route. The result is a complete Manifest object; it does not directly perform a login or tool call, but it prepares the system to do those things later.

**Call relations**: During extension loading, the host is expected to call this function to learn what the extension provides. Inside, it calls constructors for the broker, request forwarder, OAuth providers, connector providers, resolver, route specification, command-line credentials where needed, and finally the Manifest itself. Those pieces are handed back together so later connection flows, catalog lookup, browser OAuth routing, and server-side Composio tool execution all have the setup information they need.

*Call graph*: 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, items).


### `extensions/connectors/ufo_ext_connectors/manifest.py`

`config` · `startup / extension discovery`

This file is like the label and instruction card that comes with the connectors extension. Without it, the main system would not know that this extension exists, which connector tools it can expose, which connector-related objects it understands, or what guidance to add to the assistant prompt.

The connector tools are deliberately declared in one shared place. They are not tied to one provider, such as one specific service or account broker. Instead, they work across all connector providers that other broker extensions register. In plain terms, this file says: “Here is the common connector control panel: tools for listing, describing, searching, and running external connectors.”

At load time, the file reads a Markdown prompt section from `prompts/connectors_section.md`. That text becomes part of the assistant’s instructions under the section name `external_tools`. The file also imports two connector-related object definitions: one for a connection and one for a connector grant, which is permission or authorization information.

The main `manifest()` function packages all of this into a `Manifest` object. The host can then read that object and wire the extension into the larger system.

#### Function details

##### `manifest`  (lines 21–28)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the formal description of the connectors extension. The host uses this description to learn the extension’s name, version, available tools, known object types, and prompt instructions.

**Data flow**: It reads the constants already prepared in this file: the extension name and version, the connector tools, the connector object definitions, and the prompt text loaded from disk. It wraps the prompt text in a `PromptSection`, then places everything into a `Manifest`. The result is a single object that describes what this extension contributes to the system.

**Call relations**: When the extension is discovered, the host calls `manifest` to ask, “What do you provide?” This function creates a `PromptSection` for the connector prompt text and then creates a `Manifest` that hands the host the full connector extension declaration.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/pipedream/ufo_ext_pipedream/manifest.py`

`config` · `startup / extension discovery`

This file is the extension’s “front desk sign.” When the system loads extensions, it asks each one for a manifest: a compact description of its name, version, available connectors, and web routes. Without this file, the rest of the app would not know that Pipedream connectors exist, how to show them to users, or where to send browser-based connection callbacks.

The main job here is to turn the connector catalog from `CONNECTORS` into a set of `ConnectorProvider` entries. Each entry combines three things: an OAuth provider, which knows how to start the user consent flow; a human-facing label, which is what people see in the interface; and a shared `PipedreamBroker`, which is the server-side worker that actually talks to Pipedream when actions or sync jobs need credentials. OAuth means “let this app access another service without giving it your password.” In this setup, Pipedream keeps the account tokens on its own servers, so this deployment does not receive those secrets.

The file also declares one GET route for the browser bridge used during OAuth. That route is where the consent process redirects back after the user connects an account. In short, this manifest plugs Pipedream into the system’s connector registry, like adding a set of new service desks to a building directory and pointing visitors to the right callback window.

#### Function details

##### `manifest`  (lines 25–47)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the Pipedream extension manifest, which is the system’s summary of what this extension provides. It registers every supported Pipedream connector and the OAuth callback route needed to complete browser-based account connection.

**Data flow**: It starts with the connector catalog in `CONNECTORS` and creates one shared `PipedreamBroker`. For each catalog entry, it builds a `ConnectorProvider` with a Pipedream OAuth descriptor, a display label, the shared broker, and the allowed transfer hosts. It then packages all connector providers plus the OAuth bridge route into a `Manifest` object, which is returned to the host system.

**Call relations**: When the host system asks this extension what it contributes, `manifest` is the function that answers. It creates the broker with `PipedreamBroker`, turns each `CONNECTORS` entry into a `ConnectorProvider` using `PipedreamOAuthProvider`, and adds a `RouteSpec` that points to `oauth_route` while using `connect_bridge_workspace` to identify the workspace for the redirect.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, __init__, items).


### Backend and Object Registrations
These files register extension-provided packages, Redis hub backends, terminal features, scheduled jobs, tools, and workspace object types.

### `extensions/redis_hub/ufo_ext_redis_hub/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. That matters because the Redis Hub extension likely has other files in this directory that need to be imported using package-style names, such as `ufo_ext_redis_hub.some_module`.

There are no functions, classes, settings, or side effects here. Nothing is started, configured, or connected when this file is read. Its job is more like putting a label on a folder in a filing cabinet: the label does not contain the documents, but it lets the rest of the system find and refer to them reliably.

Without this file, depending on the Python version and packaging setup, imports for this extension could become less predictable or fail in environments that expect traditional package markers.


### `extensions/redis_hub/ufo_ext_redis_hub/manifest.py`

`config` · `startup`

The core system can normally keep live frame delivery and terminal connection routing inside one running server process. That is simple, but it breaks down when the service runs as several pods or instances: one user’s connection may be on one pod while the work that needs to reach them happens on another. This file offers Redis as the shared meeting place between those instances.

Redis is an external data store often used for fast shared queues and streams. Here, choosing `hub.backend = "redis"` replaces the in-memory hub with `RedisStreamHub`, so frames can be fanned out across multiple server instances. Choosing `terminal.backend = "redis"` replaces the local terminal rendezvous with `RedisTerminals`, so terminal messages can still reach the machine that actually holds the user’s connection.

Both pieces use the same `hub.url`, which is the Redis connection address. The file deliberately checks for that URL up front. If Redis is selected but no URL is configured, it raises a clear error immediately instead of failing later in a confusing place. The `manifest()` function packages these build instructions into a `Manifest`, which is how the host application discovers and enables this extension.

#### Function details

##### `_build_hub`  (lines 24–29)

```
def _build_hub(url: str | None) -> Hub
```

**Purpose**: This builds the Redis-backed live-frame hub when the main system asks for the `redis` hub backend. It also protects users from a missing Redis address by failing with a clear message before the hub is used.

**Data flow**: It receives a Redis URL, or `None` if no URL was configured. If the URL is missing, it stops with a clear runtime error explaining that `hub.url` is required. If the URL is present, it passes that address into `RedisStreamHub` and returns the ready-to-use hub object.

**Call relations**: This function is handed to `HubSpec` inside `manifest()`. Later, when the host application selects the Redis hub backend, that spec calls this builder; the builder then delegates the real Redis hub setup to `RedisStreamHub.__init__`.

*Call graph*: 1 external calls (__init__).


##### `_build_terminal`  (lines 32–37)

```
def _build_terminal(url: str | None, blob: BlobStore) -> TerminalTransport
```

**Purpose**: This builds the Redis-backed terminal transport when the main system asks for the `redis` terminal backend. It lets terminal traffic cross server instances while still using the system’s blob store for larger shared data.

**Data flow**: It receives a Redis URL and a `BlobStore`, which is shared storage for data that should not be carried directly through the terminal channel. If the URL is missing, it raises a clear error. If the URL is present, it gives both the Redis address and blob store to `RedisTerminals` and returns the constructed terminal transport.

**Call relations**: This function is registered in `manifest()` through `TerminalTransportSpec`. When the application selects the Redis terminal transport, the spec calls this builder, which hands off the actual setup work to `RedisTerminals.__init__`.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 40–48)

```
def manifest() -> Manifest
```

**Purpose**: This creates the extension manifest, which is the object the host application reads to learn what this extension provides. It advertises one hub backend named `redis` and one terminal transport backend also named `redis`.

**Data flow**: It starts from fixed extension metadata such as the name, version, and backend names. It wraps `_build_hub` in a `HubSpec` and `_build_terminal` in a `TerminalTransportSpec`, then places both specs into a `Manifest`. The returned manifest is the complete description of how the extension should be discovered and built.

**Call relations**: This is the file’s public registration point. The extension loader calls it during startup; it constructs the needed spec objects with `HubSpec.__init__`, `TerminalTransportSpec.__init__`, and `Manifest.__init__`, then hands the finished manifest back to the host system so the Redis backends can be selected by configuration.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/report_digest/ufo_ext_report_digest/manifest.py`

`config` · `startup and scheduled background work`

The report-digest extension creates short digest entries for published reports. This file does not write those entries itself. Instead, it declares the pieces the larger system should wire in, like putting a sign-up sheet on the wall that says: “load this writing standard, run this job every ten minutes, expose this admin-only button, and allow access to report blobs.”

There are two main flows. First, the scheduled job `write_digests` runs in the background. It checks that the extension was given a model, meaning the text-generating system it needs, and blob access, meaning access to the stored report content. Then it asks `DigestWriter` to find reports that still need digest entries and write them.

Second, the tool `rebuild_report_digest_handler` lets a workspace admin request that recent digest entries be written again. It does not rewrite them immediately. It marks reports from the last seven days as due, then the normal scheduled job rewrites them in batches. This prevents one user action from doing a large amount of work all at once.

The `manifest` function ties everything together. It declares the extension name and version, the shared skill text, the admin tool, the repeating job schedule, the report object, and the need to read member context.

#### Function details

##### `write_digests`  (lines 38–43)

```
async def write_digests(ctx: ExtensionContext) -> None
```

**Purpose**: This is the scheduled job entry point for creating missing report digest entries. It makes sure the needed services are present, then delegates the real writing work to `DigestWriter`.

**Data flow**: It receives an `ExtensionContext`, which is the bundle of services and permissions the extension has been given. It checks for a background model and report blob access; if either is missing, it stops with a clear error because it cannot read reports or generate entries. If both are present, it creates a `DigestWriter` with the context, model, and blob store, then runs it so pending reports can receive digest entries.

**Call relations**: The job declared by `manifest` calls this function every ten minutes for workspaces that have undigested reports. `write_digests` is the bridge from the host scheduler into the report-digest writing code: it prepares the required inputs and hands the task to `DigestWriter`.

*Call graph*: 1 external calls (__init__).


##### `rebuild_report_digest_handler`  (lines 50–73)

```
async def rebuild_report_digest_handler(ctx: ToolContext, args: RebuildReportDigestInput) -> ToolResult
```

**Purpose**: This is the handler behind the admin tool that asks the system to rewrite recent report digest entries. It is used when the existing entries are judged poor and the last seven days of reports should be put back in the queue.

**Data flow**: It receives a tool context and an empty validated input object. It first checks that an extension context is available, then asks whether the speaker is a workspace admin. If the speaker is not an admin, it rejects the request. If allowed, it runs `DigestRebuild`, which marks recent reports as due for rewriting. It returns a `ToolResult` containing either a message that nothing could be rebuilt or a message saying how many reports were queued for the scheduled digest job.

**Call relations**: The tool definition created in `manifest` points user requests to this handler. The handler checks permission through `ToolContext.speaker_is_admin`, then hands the actual requeueing work to `DigestRebuild`. It formats the user-facing answer with `TextContent` and `ToolResult`, while leaving the later writing work to the scheduled digest job.

*Call graph*: calls 1 internal fn (speaker_is_admin); 3 external calls (__init__, __init__, __init__).


##### `manifest`  (lines 76–114)

```
def manifest() -> Manifest
```

**Purpose**: This function returns the extension manifest, which is the complete description of what this extension contributes to the host system. The host uses it to discover the skill, tool, scheduled job, object type, and required permissions.

**Data flow**: It takes no input. It builds a `Manifest` containing the extension name and version, the path to the shared writing skill, a tool definition for rebuilding digest entries, a scheduled job definition for writing entries, the report object declaration, and a flag saying member context can be read. The output is the manifest object that the host application can load at startup.

**Call relations**: This is the file’s central registration point. It creates `SkillSpec`, `ToolDef`, `ActionPresentation`, `ObjectBinding`, and `JobSpec` objects so the host knows what to show to users, what handler to call for rebuild requests, and what background job to schedule. It also uses `owner_candidates(undigested_workspaces)` so the scheduler knows which workspaces are worth running the digest job for.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, owner_candidates).


### Sample Extension Coverage
The sample extension demonstrates broad end-to-end coverage of the public extension API across many registration points.

### `extensions/sample/ufo_ext_sample.py`

`domain_logic` · `cross-cutting`

This file is like a practice plug board for the UFO extension system. Instead of talking to real outside services, it provides small predictable versions of tools, jobs, web routes, object stores, search, memory, model calls, browser leases, connector accounts, feature flags, surfaces, and sandbox carriers. The point is not to be useful to an end user; the point is to prove that an extension can use only the public SDK and still connect to all the places a real extension would need.

Most handlers write a short record into the extension's durable store. That matters because tests can later read those records back through the same public paths the real product uses, rather than checking private mocks. For example, the echo tool records its input, the scheduled job records that it ran, hooks record which event reached them, and surface routes record admitted turns.

The `manifest()` function is the centerpiece. It declares everything this extension contributes: tools, object kinds, hooks, onboarding, routes, connectors, sources, indexes, model backends, browser providers, carriers, and more. Without this file, the project would lack one compact, real extension that exercises the public contract and catches breaking changes at the boundary between core and extensions.

#### Function details

##### `_echo`  (lines 346–350)

```
async def _echo(ctx: ToolContext, args: EchoInput) -> ToolResult
```

**Purpose**: Runs the sample echo tool. It records the message it was given, then returns the same message back as tool output.

**Data flow**: It receives a tool context and an input object containing `message`. It checks that the extension context is present, writes the input into the extension store, and returns a tool result containing the message as text.

**Call relations**: This function is registered as the sample echo tool in `manifest()`. When core dispatches that tool, this is the handler; a pre-tool hook may deny the call before it reaches this function.

*Call graph*: 3 external calls (__init__, __init__, model_dump).


##### `_note`  (lines 353–375)

```
async def _note(ctx: ToolContext, args: NoteInput) -> ToolResult
```

**Purpose**: Writes a note into the sample extension's own database table and reads it back. It proves that an extension-owned migration table can be used safely inside a workspace-scoped transaction.

**Data flow**: It receives note text and the current tool context. It finds the workspace id, updates or inserts that workspace's note row, reads the stored note back, and returns it as text.

**Call relations**: This is registered as the sample note tool in `manifest()`. The scheduled job uses the same table to find workspaces, so this tool also seeds data for that flow.

*Call graph*: 5 external calls (__init__, __init__, insert, select, update).


##### `_tick`  (lines 378–404)

```
async def _tick(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the sample scheduled job. It records that the job ran, then optionally inspects agent trajectories, proposes a prompt change, writes a workspace file, and probes that file.

**Data flow**: It receives an extension context. It writes a job marker, reads available trajectories if the corpus exists, proposes a prompt update for the first trajectory, writes a file if file access exists, runs a probe command if probes exist, and stores each result.

**Call relations**: This function is registered as the `sample_tick` job in `manifest()`. Core calls it from the jobs system when a candidate workspace is selected.

*Call graph*: calls 2 internal fn (propose_change, trajectories); 1 external calls (__init__).


##### `_hook`  (lines 407–410)

```
async def _hook(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: Serves the sample HTTP route. It echoes the request body while recording that the route was reached and what home URL the extension sees.

**Data flow**: It receives an extension context and an HTTP request. It reads the request body, stores the body and extension home URL, and returns the body as plain text.

**Call relations**: This handler is attached to the sample POST route in `manifest()`. The route resolver first identifies the workspace, then core calls this handler.

*Call graph*: calls 1 internal fn (home_url); 2 external calls (PlainTextResponse, body).


##### `WidgetStore.list`  (lines 449–460)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists the sample widgets stored for the workspace. It turns raw stored rows into object-list rows that the object surface can show.

**Data flow**: It reads all extension-store keys with the widget prefix, validates each stored value, extracts display fields, and returns a paged object list.

**Call relations**: Core calls this through the object-kind registration in `manifest()` when a user or agent lists sample widgets. It relies on `WidgetStore._ext` to get the extension store.

*Call graph*: calls 1 internal fn (_ext); 2 external calls (__init__, object_page).


##### `WidgetStore.get`  (lines 462–472)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[WidgetSpec] | None
```

**Purpose**: Fetches one sample widget by name. It returns the widget's spec plus timestamps and generation if it exists.

**Data flow**: It receives a widget name, reads that widget key from the extension store, validates the stored row, and returns object detail or `None` if missing.

**Call relations**: Core calls this through the object system when someone reads a widget. It uses `WidgetStore._ext` to reach the workspace-scoped extension store.

*Call graph*: calls 1 internal fn (_ext); 1 external calls (__init__).


##### `WidgetStore.status`  (lines 474–485)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Checks whether a widget still matches the generation a caller expects. This protects edits from silently overwriting a newer change.

**Data flow**: It reads the named widget. If it exists, it compares its current generation to the expected generation and raises an error if they differ.

**Call relations**: Core calls this as part of object status checks. It delegates the generation comparison to `WidgetStore._require_current`.

*Call graph*: calls 2 internal fn (_ext, _require_current).


##### `WidgetStore.apply`  (lines 487–512)

```
async def apply(self, ctx: ToolContext, name: str, spec: WidgetSpec, old: WidgetSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Creates or updates a sample widget. It keeps the original creation time but gives every successful write a fresh generation id.

**Data flow**: It receives a widget name, new spec, optional old spec, and expected generation. It reads any existing row, checks for stale edits, builds a new stored widget with current time and a new UUID, and saves it.

**Call relations**: Core calls this when the object apply verb is used. It uses `WidgetStore._ext` for storage and `WidgetStore._require_current` when updating an existing widget.

*Call graph*: calls 2 internal fn (_ext, _require_current); 3 external calls (__init__, now, uuid4).


##### `WidgetStore.delete`  (lines 514–527)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Deletes a sample widget, but only if the speaker is a workspace admin. It also refuses stale deletes when the generation no longer matches.

**Data flow**: It reads the widget, checks its generation if present, asks the tool context whether the speaker is an admin, raises an admin-required error if not, and deletes the stored key if allowed.

**Call relations**: Core calls this through the object delete verb. It uses `WidgetStore._require_current` for edit fencing and `speaker_is_admin` for the permission gate.

*Call graph*: calls 3 internal fn (speaker_is_admin, _ext, _require_current); 1 external calls (__init__).


##### `WidgetStore._require_current`  (lines 529–533)

```
def _require_current(self, name: str, stored: StoredWidget, expected_generation: UUID | None) -> None
```

**Purpose**: Checks that a stored widget is the same version the caller expected. It is the small guard that prevents stale object edits.

**Data flow**: It receives a widget name, the stored widget row, and an expected generation id. If the stored generation differs, it raises an error; otherwise it returns normally.

**Call relations**: The widget status, apply, and delete paths call this whenever they need to make sure the row did not change between read and write.

*Call graph*: called by 3 (apply, delete, status).


##### `WidgetStore._ext`  (lines 535–538)

```
def _ext(self, ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Extracts the extension context from a tool context. It fails loudly if the object store was invoked without extension state.

**Data flow**: It receives a tool context, checks `ctx.ext`, and returns it if present. If not, it raises a runtime error.

**Call relations**: All widget store operations call this before touching the extension store, so they share one consistent safety check.

*Call graph*: called by 5 (apply, delete, get, list, status).


##### `RelicStore.list`  (lines 546–550)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists the one built-in sample relic. Relics are used to prove read-only object kinds work.

**Data flow**: It ignores stored data and returns a page containing one fixed object row for the sample relic.

**Call relations**: Core calls this through the relic object-kind registration when listing relics.

*Call graph*: 2 external calls (__init__, object_page).


##### `RelicStore.get`  (lines 552–557)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[RelicSpec] | None
```

**Purpose**: Reads the fixed sample relic by name. It returns details only for the known relic name.

**Data flow**: It receives a name. If the name matches the sample relic, it returns an object detail with a fixed inscription; otherwise it returns `None`.

**Call relations**: Core calls this through the object system when a relic detail is requested.

*Call graph*: 2 external calls (__init__, __init__).


##### `RelicStore.status`  (lines 559–566)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports simple live status for the sample relic. It shows that read-only objects can still have status information.

**Data flow**: It receives object context and returns a small dictionary saying the relic was excavated.

**Call relations**: Core calls this through the object status path for relics.


##### `RelicStore.apply`  (lines 568–577)

```
async def apply(self, ctx: ToolContext, name: str, spec: RelicSpec, old: RelicSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to create or update a relic. This models a system-produced object that users cannot author.

**Data flow**: It receives an apply request and immediately raises a not-supported error with the sample refusal message.

**Call relations**: Core calls this if someone tries the apply verb on a relic; the function proves mutation refusal is surfaced cleanly.

*Call graph*: 1 external calls (__init__).


##### `RelicStore.delete`  (lines 579–586)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to delete a relic. This keeps the sample relic strictly read-only.

**Data flow**: It receives a delete request and immediately raises a not-supported error with the sample refusal message.

**Call relations**: Core calls this if someone tries the delete verb on a relic; the behavior mirrors `RelicStore.apply`.

*Call graph*: 1 external calls (__init__).


##### `_target_record`  (lines 626–637)

```
def _target_record(target: ObjectActionTarget | None) -> dict[str, JsonValue] | None
```

**Purpose**: Turns an object-action target into a simple JSON-friendly record. This makes action logs easy for tests and readers to inspect.

**Data flow**: It receives an optional target. If there is no target it returns `None`; otherwise it copies the kind, name, agent name, generation, and expected generation into plain values.

**Call relations**: Most sample object actions and bless hooks call this before writing their observations into the extension store.

*Call graph*: called by 9 (_audit, _beseech, _bless, _bless_fold, _bless_replace, _calibrate, _divine, _engrave, _polish).


##### `_action_ext`  (lines 640–643)

```
def _action_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Gets the extension context for object action tools. It prevents action handlers from silently running without their durable store.

**Data flow**: It receives a tool context, returns `ctx.ext` when present, and raises a runtime error when it is missing.

**Call relations**: The audit, polish, engrave, divine, calibrate, bless, and beseech tools call this at the start of their work.

*Call graph*: called by 7 (_audit, _beseech, _bless, _calibrate, _divine, _engrave, _polish).


##### `_audit`  (lines 646–656)

```
async def _audit(ctx: ToolContext, args: AuditInput) -> ToolResult
```

**Purpose**: Records an audit action against the workspace or target object. It returns a short confirmation message.

**Data flow**: It receives audit input and tool context, stores the subject, extension name, and target record, then returns text saying what was audited.

**Call relations**: Registered as a workspace collection action in `manifest()`. It uses `_action_ext` and `_target_record` to produce a durable action record.

*Call graph*: calls 2 internal fn (_action_ext, _target_record); 2 external calls (__init__, __init__).


##### `_polish`  (lines 659–662)

```
async def _polish(ctx: ToolContext, args: PolishInput) -> ToolResult
```

**Purpose**: Records that a widget was polished with a given number of coats. It is a simple instance action for the object surface.

**Data flow**: It receives the coat count and current target, stores both, and returns a text confirmation.

**Call relations**: Registered as an instance-bound widget action in `manifest()`. Core dispatches it when that action is invoked.

*Call graph*: calls 2 internal fn (_action_ext, _target_record); 2 external calls (__init__, __init__).


##### `_engrave`  (lines 665–701)

```
async def _engrave(ctx: ToolContext, args: EngraveInput) -> ToolResult
```

**Purpose**: Performs a side-effecting widget action that changes the widget generation and records an engraving. It also demonstrates idempotency, meaning the same request key will not repeat the side effect.

**Data flow**: It receives engraving text, an optional interrupt flag, and target details. It checks the target, returns early for a repeated idempotency key, verifies the widget still exists and is current, writes a new generation, stores the engraving record, optionally raises cancellation once, and returns confirmation text.

**Call relations**: Registered as the widget engrave action in `manifest()`. It works with target data produced by the object system and records the result for hook and retry tests.

*Call graph*: calls 2 internal fn (_action_ext, _target_record); 5 external calls (__init__, __init__, __init__, now, uuid4).


##### `_divine`  (lines 704–707)

```
async def _divine(ctx: ToolContext, args: DivineInput) -> ToolResult
```

**Purpose**: Returns a fixed piece of untrusted text about widgets. It proves the system can mark some tool output as third-party data rather than instructions.

**Data flow**: It receives a query and target, stores them, and returns the fixed divination text.

**Call relations**: Registered as an untrusted collection action in `manifest()`. Core dispatches it like any other tool but treats its output according to that declaration.

*Call graph*: calls 2 internal fn (_action_ext, _target_record); 2 external calls (__init__, __init__).


##### `_calibrate`  (lines 710–715)

```
async def _calibrate(ctx: ToolContext, args: CalibrateInput) -> ToolResult
```

**Purpose**: Records a calibration offset for the widget collection. It is used to test tools that are meant only for certain profiles.

**Data flow**: It receives an offset, stores it with the target record, and returns a confirmation with the offset.

**Call relations**: Registered in `manifest()` as a profile-only action. It uses the shared action helper functions before writing to the store.

*Call graph*: calls 2 internal fn (_action_ext, _target_record); 2 external calls (__init__, __init__).


##### `_bless`  (lines 718–723)

```
async def _bless(ctx: ToolContext, args: BlessInput) -> ToolResult
```

**Purpose**: Blesses a widget unless asked to fail. It exists mainly so hooks can alter its input, replace its output, and observe failures.

**Data flow**: It receives a phrase and fail flag. If failure is requested it raises an error; otherwise it stores the phrase and target and returns a blessing message.

**Call relations**: Registered as a widget action in `manifest()`. The bless-specific pre, post, and failure hooks are attached to this action.

*Call graph*: calls 2 internal fn (_action_ext, _target_record); 2 external calls (__init__, __init__).


##### `_beseech`  (lines 726–734)

```
async def _beseech(ctx: ToolContext, args: BeseechInput) -> ToolResult
```

**Purpose**: Creates a final-act response that asks the member a question. It demonstrates a tool result that carries structured user-input instructions.

**Data flow**: It receives a question, stores it with target information, builds an `AskUserInput` payload, and returns text containing the directive and serialized payload.

**Call relations**: Registered as a widget collection action in `manifest()` with a final-act model, so core knows the output is meant to prompt the user.

*Call graph*: calls 2 internal fn (_action_ext, _target_record); 4 external calls (__init__, __init__, __init__, __init__).


##### `_bless_fold`  (lines 737–751)

```
async def _bless_fold(ctx: HookContext) -> HookOutcome
```

**Purpose**: Runs before the bless action and rewrites the blessing phrase by adding a suffix. It proves hooks can modify tool input before dispatch.

**Data flow**: It receives hook context. If the payload is a pre-use event for a bless input, it stores the call and target, returns a modified input, and otherwise does nothing.

**Call relations**: Registered as a pre-tool hook for the canonical bless action. Its modified input is handed back to core, which then calls the bless tool with the changed phrase.

*Call graph*: calls 1 internal fn (_target_record); 2 external calls (__init__, __init__).


##### `_bless_replace`  (lines 754–762)

```
async def _bless_replace(ctx: HookContext) -> HookOutcome
```

**Purpose**: Runs after a successful bless action and replaces the tool output with a fixed message. It proves post hooks can rewrite successful results.

**Data flow**: It receives hook context. For a post-tool-use payload, it stores the call, original output, and target, then returns a modified output.

**Call relations**: Registered as a post-tool hook for the bless action. Core calls it after `_bless` succeeds and uses the replacement output it returns.

*Call graph*: calls 1 internal fn (_target_record); 1 external calls (__init__).


##### `_bless_failure`  (lines 765–769)

```
async def _bless_failure(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records failed bless attempts. It proves failure hooks receive error results separately from success hooks.

**Data flow**: It receives hook context. If the payload is a tool-failure event, it stores the call and failure output, then returns no change.

**Call relations**: Registered as a post-tool-use-failure hook for the bless action. Core calls it when `_bless` raises an error.


##### `SampleSource.fetch`  (lines 788–797)

```
async def fetch(self, config: SampleSourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Produces one predictable content page from the source configuration. It lets the source-sync pipeline run without an external content service.

**Data flow**: It receives typed source config, an optional cursor, and auth information. It turns the configured topic into a page and returns it with no next cursor.

**Call relations**: The onboarding handler registers this source. Later, core's source sync calls `fetch` through the source provider declared in `manifest()`.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleIndex.upsert`  (lines 810–812)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: Adds or replaces indexed chunks in the in-memory sample index. A chunk is a small searchable piece of text plus metadata.

**Data flow**: It receives chunks and stores each one in a dictionary keyed by its digest, replacing any older chunk with the same digest.

**Call relations**: Core calls this through the sample index backend when it wants to index source or memory content.


##### `SampleIndex.delete`  (lines 814–816)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: Deletes all chunks that belong to a given index scope. A scope is the owner kind and owner id whose data should be removed.

**Data flow**: It receives a scope, finds stored chunks inside that scope, and removes them from the in-memory dictionary.

**Call relations**: Core calls this through the index backend for cleanup. It uses `_in_scope` to decide which chunks match.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.has_chunks`  (lines 818–819)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: Answers whether any chunks exist for a given scope. It is a quick presence check for indexed data.

**Data flow**: It receives a scope, scans the stored chunks, and returns true if at least one chunk belongs to that scope.

**Call relations**: Core calls this through the index backend. It shares the same scope test as delete and prune through `_in_scope`.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.prune`  (lines 821–827)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: Removes stale chunks from a scope while keeping a named set. This models refreshing an index without deleting current content.

**Data flow**: It receives a scope and a set of digests to keep. It deletes chunks that are in the scope but not in the keep set.

**Call relations**: Core calls this during index maintenance. It uses `_in_scope` to restrict pruning to the requested owner.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.lexical`  (lines 829–838)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches stored chunks by counting query words in their text. It is a simple text-search backend for conformance tests.

**Data flow**: It receives a query, allowed subjects, owner kind, and limit. It filters chunks to that owner and subject set, scores each by word counts, sorts highest first, and returns hits.

**Call relations**: Core calls this through the index backend for keyword-style retrieval. It uses `_scoped` to filter chunks and `_hit` to shape results.

*Call graph*: calls 2 internal fn (_scoped, _hit).


##### `SampleIndex.vector`  (lines 840–848)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches stored chunks by comparing vectors. A vector is a list of numbers used to represent meaning for similarity search.

**Data flow**: It receives an embedding vector, allowed subjects, owner kind, and limit. It filters chunks, scores each with a dot product, sorts positive scores, and returns hits.

**Call relations**: Core calls this through the index backend for embedding-style retrieval. It uses `_scoped`, `_dot`, and `_hit`.

*Call graph*: calls 3 internal fn (_scoped, _dot, _hit).


##### `SampleIndex._scoped`  (lines 850–855)

```
def _scoped(self, subjects: frozenset[str], owner_kind: str) -> list[Chunk]
```

**Purpose**: Filters chunks to the owner kind and subject set requested by a search. This prevents searches from crossing data boundaries.

**Data flow**: It receives subjects and an owner kind, scans the in-memory chunks, and returns only chunks matching both.

**Call relations**: The lexical and vector search methods call this before scoring results.

*Call graph*: called by 2 (lexical, vector).


##### `SampleEmbed.embed`  (lines 864–865)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: Returns the same fixed embedding vector for every input text. It gives the system a predictable embedding backend.

**Data flow**: It receives a tuple of texts and returns one fixed numeric vector for each text.

**Call relations**: Core calls this through the embed backend declared in `manifest()` when it needs embeddings for tests.


##### `_in_scope`  (lines 868–869)

```
def _in_scope(chunk: Chunk, scope: IndexScope) -> bool
```

**Purpose**: Checks whether a chunk belongs to a particular index scope. It compares the chunk's owner fields to the scope's owner fields.

**Data flow**: It receives a chunk and scope, compares owner kind and owner id, and returns a boolean.

**Call relations**: Sample index delete, has-chunks, and prune use this helper to make the same ownership decision.

*Call graph*: called by 3 (delete, has_chunks, prune).


##### `_dot`  (lines 872–875)

```
def _dot(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: Computes a dot product for two numeric vectors. This is the sample index's simple similarity score.

**Data flow**: It receives two tuples of numbers. If either is empty it returns zero; otherwise it multiplies matching positions and sums the products.

**Call relations**: Sample vector search calls this to score each candidate chunk before turning it into a hit.

*Call graph*: called by 1 (vector).


##### `_hit`  (lines 878–887)

```
def _hit(chunk: Chunk, score: float) -> Hit
```

**Purpose**: Converts a stored chunk and score into a search hit. It copies the chunk metadata needed by callers.

**Data flow**: It receives a chunk and numeric score, then returns a `Hit` with digest, owner, subject, text, ordinal, and score.

**Call relations**: The lexical and vector search methods call this after scoring chunks.

*Call graph*: called by 2 (lexical, vector); 1 external calls (__init__).


##### `_setup`  (lines 890–897)

```
async def _setup(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the sample onboarding step. It marks onboarding complete and registers the sample content source for the workspace.

**Data flow**: It receives an extension context, writes an onboarding marker into the store, builds a source config, and asks core to register that source.

**Call relations**: Registered as an onboarding step in `manifest()`. Core calls it when the workspace completes that setup step.

*Call graph*: calls 1 internal fn (register_source); 1 external calls (__init__).


##### `_deny_echo`  (lines 900–903)

```
async def _deny_echo(ctx: HookContext) -> HookOutcome
```

**Purpose**: Always denies the sample echo tool when attached as a pre-tool hook. It proves a hook can stop a tool before the handler runs.

**Data flow**: It receives hook context and returns a deny outcome with a fixed reason.

**Call relations**: Registered as a pre-tool hook for the echo tool in `manifest()`. Core uses its denial to short-circuit `_echo`.

*Call graph*: 1 external calls (__init__).


##### `_record_post`  (lines 906–914)

```
async def _record_post(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records successful tool-use events. It proves that post-use hooks run after non-denied, non-failing tools.

**Data flow**: It receives hook context. If the payload is a successful tool-use event, it stores the tool name, then returns no modification.

**Call relations**: Registered as a broad post-tool hook in `manifest()`. Core calls it after successful tool dispatches.


##### `_record_post_failure`  (lines 917–923)

```
async def _record_post_failure(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records failed tool-use events. It proves failures go to a separate hook path from successes.

**Data flow**: It receives hook context. If the payload is a tool failure, it stores the tool name, then returns no modification.

**Call relations**: Registered as a broad post-tool-use-failure hook in `manifest()`. Core calls it when a tool dispatch ends in an error.


##### `_record_stop`  (lines 926–932)

```
async def _record_stop(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records the final answer at the end of a turn. It proves stop hooks receive the answer before it is committed.

**Data flow**: It receives hook context. If the payload is a stop event, it stores the answer and returns no change.

**Call relations**: Registered as a stop hook in `manifest()`. Core calls it near turn completion.


##### `_record_pre_compact`  (lines 935–942)

```
async def _record_pre_compact(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records information before conversation compaction. Compaction means shrinking stored conversation context to fit within model limits.

**Data flow**: It receives hook context. If the payload is a pre-compaction event, it stores the reason and estimated token count before compaction.

**Call relations**: Registered as a pre-compact hook in `manifest()`. Core calls it before compacting context.


##### `_record_post_compact`  (lines 945–957)

```
async def _record_post_compact(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records information after conversation compaction. It captures the summary and token counts before and after.

**Data flow**: It receives hook context. If the payload is a post-compaction event, it stores the summary and both token counts.

**Call relations**: Registered as a post-compact hook in `manifest()`. Core calls it after compaction finishes.


##### `_record_page_change`  (lines 960–973)

```
async def _record_page_change(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records delivered page-change events and whether a model was available in the off-turn context. It proves the data-change hook path is wired.

**Data flow**: It receives hook context. If the payload contains page changes, it stores their page ids and a boolean saying whether `ctx.ext.model` is present.

**Call relations**: Registered as a page-change hook in `manifest()`. Core calls it when source pages change.


##### `_SampleConnectorOAuth.authorize_url`  (lines 986–987)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds a predictable OAuth authorization URL for the sample connector. OAuth is the common web flow where a user grants account access.

**Data flow**: It receives a state value and redirect URI, inserts them into a fixed sample authorization URL, and returns the URL string.

**Call relations**: Core calls this through the connector provider when starting the sample account connection flow.


##### `_SampleConnectorOAuth.exchange`  (lines 989–992)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Completes the fake OAuth exchange and returns a fixed connected account id. It does not return any secret token.

**Data flow**: It receives the code, redirect URI, workspace id, and state, ignores their contents, and returns an `OAuthAccount` for the sample account.

**Call relations**: Core calls this after the connector callback. The resulting account id is later used by connector tools and broker credentials.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.tools`  (lines 1005–1006)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Returns the sample connector's one available broker tool. A broker is the connector-side service that describes and runs provider tools.

**Data flow**: It receives workspace, provider, and query information, and returns a tuple containing one fixed tool description.

**Call relations**: Core can call this when discovering connector tools, and `_SampleBroker.search` also calls it to include tools in search results.

*Call graph*: called by 1 (search); 1 external calls (__init__).


##### `_SampleBroker.schema`  (lines 1008–1015)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Returns the input schema for the sample broker tool. It rejects unknown tool slugs.

**Data flow**: It receives a tool slug. If it is the known slug, it returns a tool description with a small JSON schema; otherwise it raises an unknown-tool error.

**Call relations**: Core calls this when it needs the broker tool's argument shape before execution.

*Call graph*: 2 external calls (__init__, __init__).


##### `_SampleBroker.execute`  (lines 1017–1034)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Runs the fake broker tool by echoing the call details back as data. It proves execution wiring without contacting a real provider.

**Data flow**: It receives workspace, provider, slug, arguments, account id, and idempotency key. It rejects unknown slugs and otherwise returns those values in a dictionary.

**Call relations**: Core calls this through dynamic connector tooling when executing the broker-provided tool.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.file_outputs`  (lines 1036–1047)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Turns echoed output URLs into produced file records. It lets tests drive the connector file-output bridge.

**Data flow**: It receives a broker response, looks for an `arguments.file_output_urls` list, and returns one broker file per string URL using the URL's final path part as the name.

**Call relations**: Core calls this after broker execution to discover files the broker says it produced.

*Call graph*: 2 external calls (__init__, PurePosixPath).


##### `_SampleBroker.stage_upload`  (lines 1049–1075)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Creates a staged upload target for connector file inputs. Staging means reserving a place where bytes can be uploaded before a tool call.

**Data flow**: It receives file metadata, builds a content-addressed key from the md5 and filename, returns a file URL on first staging, and returns no upload URL for repeated staging of the same key.

**Call relations**: Core calls this before broker execution when connector arguments include file uploads.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.search`  (lines 1077–1080)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Returns a simple broker search result containing the sample tool and a suggested plan. It models connector tool discovery by query.

**Data flow**: It receives workspace, provider, and query, calls `tools` to get the canned tool list, and wraps it with a fixed plan.

**Call relations**: Core calls this when searching connector capabilities. It delegates tool listing to `_SampleBroker.tools`.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `_SampleBroker.credential`  (lines 1082–1083)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a fake bearer credential for a connected account. A bearer credential is a token sent as proof of access.

**Data flow**: It receives workspace, provider, and account id, prefixes the account id with the sample token prefix, and returns it as a credential.

**Call relations**: Core calls this when it needs a connector-backed credential, such as for an egress proxy or provider request.

*Call graph*: 1 external calls (__init__).


##### `_connector_execute`  (lines 1090–1108)

```
async def _connector_execute(ctx: ToolContext, args: ConnectorExecuteInput) -> ToolResult
```

**Purpose**: Runs the sample connector's server-side tool. It resolves the connected account bound to the current agent and records the call.

**Data flow**: It receives a tool context and input tool name. It asks the context for the connector account, stores the account, tool name, and idempotency key, and returns the account as text.

**Call relations**: Registered as a connector-provided tool in `manifest()`. Core dispatches it only when the agent has the proper connector grant.

*Call graph*: calls 1 internal fn (connector_account); 2 external calls (__init__, __init__).


##### `_one_chunk`  (lines 1118–1119)

```
async def _one_chunk(data: bytes) -> AsyncIterator[bytes]
```

**Purpose**: Wraps a byte string as a one-piece async stream. It is a tiny adapter for APIs that expect streamed file content.

**Data flow**: It receives bytes and yields those exact bytes once.

**Call relations**: `_surface_ingest` uses this when writing an inbound text attachment into the workspace.

*Call graph*: called by 1 (_surface_ingest).


##### `_surface_model`  (lines 1125–1131)

```
async def _surface_model(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Reports which model is wired into a surface route. It proves surface handlers can see the deployed model client information.

**Data flow**: It receives a surface context and request, reads `ctx.model`, and returns JSON with either the model id or null.

**Call relations**: Registered as a GET route on the durable sample surface in `manifest()`. Core calls it during surface request handling.

*Call graph*: 1 external calls (JSONResponse).


##### `_surface_ingest`  (lines 1134–1161)

```
async def _surface_ingest(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Accepts an inbound durable-surface message. It links an external identity, finds or creates a conversation, optionally writes an inbound file, and admits a turn.

**Data flow**: It reads JSON from the request, resolves or creates a member, gets a conversation for that external id, streams optional inbound text into a workspace file, admits the message with an idempotency key, and returns turn and conversation ids plus whether a run opened.

**Call relations**: Registered as the POST route for the durable sample surface. It uses `_one_chunk` for file streaming and several `SurfaceContext` methods for identity, conversation, and turn admission.

*Call graph*: calls 6 internal fn (admit, conversation_for, link_member, linked_member, write_workspace_file, _one_chunk); 3 external calls (conversation_audience, JSONResponse, body).


##### `_surface_post`  (lines 1164–1165)

```
async def _surface_post(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Returns a fixed reference for a posted surface writeback. A writeback is the system delivering an answer back to an outside surface.

**Data flow**: It receives the surface context and writeback payload, ignores the details, and returns the fixed sample post reference.

**Call relations**: Registered as the durable surface's post callback in `manifest()`. Core calls it when a turn result should be delivered back to that surface.


##### `_surface_attach`  (lines 1168–1173)

```
async def _surface_attach(ctx: SurfaceContext, writeback: Writeback, reply_ref: str) -> None
```

**Purpose**: Copies writeback artifact files into delivered blob keys. This proves streamed blob reads and writes work during surface attachment delivery.

**Data flow**: It receives a writeback and reply reference. For each artifact, it opens a stream from the artifact blob key and writes that stream to a delivered key that includes the turn id and filename.

**Call relations**: Registered as the durable surface's attachment callback. Core calls it after posting when artifacts must be attached.


##### `_surface_live_admit`  (lines 1176–1198)

```
async def _surface_live_admit(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Accepts an inbound message on the live sample surface. Live mode admits a turn without durable writeback polling, then reports owner and spend information.

**Data flow**: It reads request JSON, resolves or adopts a member identity, gets a conversation, admits the message, reads the turn owner, asks for recent spend rollup, and returns those values as JSON.

**Call relations**: Registered as the POST route for the live surface. Core calls it during live-surface request handling, and clients can later stream the turn through `_surface_live_stream`.

*Call graph*: calls 6 internal fn (admit, adopt_identity, conversation_for, linked_member, spend_rollup, turn_owner); 3 external calls (conversation_audience, JSONResponse, body).


##### `_surface_live_stream`  (lines 1201–1205)

```
async def _surface_live_stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Streams live turn frames as newline-delimited JSON. This lets a client follow a running turn until it reaches a final state.

**Data flow**: It reads the turn id from the route path, builds a streaming response around `_surface_frames`, and sets the response media type for newline-separated JSON.

**Call relations**: Registered as the live surface's stream route. It hands the actual frame iteration to `_surface_frames`.

*Call graph*: calls 1 internal fn (_surface_frames); 2 external calls (StreamingResponse, UUID).


##### `_surface_frames`  (lines 1208–1211)

```
async def _surface_frames(ctx: SurfaceContext, turn_id: UUID) -> AsyncIterator[bytes]
```

**Purpose**: Reads live frames for one turn from the surface hub and yields them as bytes. The hub is the in-process channel where live updates appear.

**Data flow**: It receives a surface context and turn id, opens a tail stream, converts each frame to JSON, appends a newline, and yields the bytes.

**Call relations**: `_surface_live_stream` calls this to supply the body of the streaming HTTP response.

*Call graph*: calls 1 internal fn (tail); called by 1 (_surface_live_stream).


##### `SampleModelClient.complete`  (lines 1222–1225)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Streams a fixed model response. It gives the model registry a real client object without calling an outside model provider.

**Data flow**: It receives a model request, yields a stream-start event, yields one text delta with the canned reply, then yields fixed token usage.

**Call relations**: Core calls this through the model spec declared in `manifest()` when the sample model is selected.

*Call graph*: 3 external calls (__init__, __init__, __init__).


##### `SampleCdpLease.endpoint`  (lines 1235–1236)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the fixed browser debugging endpoint for the sample CDP lease. CDP means Chrome DevTools Protocol, a way to control a browser.

**Data flow**: It returns a `CdpEndpoint` containing the sample WebSocket URL.

**Call relations**: Core's browser machinery calls this after leasing the sample CDP provider.

*Call graph*: 1 external calls (__init__).


##### `SampleCdpLease.token`  (lines 1238–1239)

```
async def token(self) -> str
```

**Purpose**: Returns a reattach token for the sample browser lease. In this sample, the token is simply the fixed endpoint URL.

**Data flow**: It returns the sample CDP URL string.

**Call relations**: Core can store this token and later pass it to the provider's reattach path.


##### `SampleCdpLease.place_file`  (lines 1241–1242)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Pretends to place a file for browser use by returning the same path unchanged. It models a browser already sharing the sandbox path.

**Data flow**: It receives a path and a byte-reading callback, ignores the callback, and returns the original path.

**Call relations**: Core calls this through the CDP lease protocol when a browser interaction needs a file path.


##### `SampleCdpLease.download_dir`  (lines 1244–1245)

```
async def download_dir(self) -> str
```

**Purpose**: Reports the fixed directory where sample browser downloads would appear.

**Data flow**: It returns the sample download directory path string.

**Call relations**: Core calls this through the CDP lease when it needs to locate downloads.


##### `SampleCdpLease.fetch_download`  (lines 1247–1248)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Reads a downloaded file from the sample download directory. The actual disk read is moved to a worker thread so the async loop is not blocked.

**Data flow**: It receives a download guid, builds a path under the sample download directory, reads the bytes, and returns them.

**Call relations**: Core calls this through the CDP lease when it wants the bytes for a browser download.

*Call graph*: 2 external calls (to_thread, Path).


##### `SampleCdpLease.aclose`  (lines 1250–1251)

```
async def aclose(self) -> None
```

**Purpose**: Closes the sample browser lease. There is nothing real to release, so it is a no-op.

**Data flow**: It receives no meaningful input and returns `None` without changing state.

**Call relations**: Core calls this when it is done with the lease, matching the normal cleanup shape for real providers.


##### `SampleCdpProvider.lease`  (lines 1261–1262)

```
async def lease(self, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: Creates a new sample CDP lease. It gives browser code a lease object without launching a real browser.

**Data flow**: It receives an optional sandbox and returns a new `SampleCdpLease`.

**Call relations**: Core calls this through the CDP provider spec declared in `manifest()` when it needs a browser lease.

*Call graph*: 1 external calls (__init__).


##### `SampleCdpProvider.reattach`  (lines 1264–1265)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: Reattaches to a sample CDP lease from a stored token. In the sample, every token leads back to the same fixed lease.

**Data flow**: It receives a token string, ignores its contents, and returns a new `SampleCdpLease`.

**Call relations**: Core calls this when resuming browser access from a prior lease token.

*Call graph*: 1 external calls (__init__).


##### `SampleAuthProxy.credential`  (lines 1276–1277)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a fixed bearer credential for the sample auth proxy backend. It proves auth-proxy selection works without a real secret provider.

**Data flow**: It receives workspace, provider, and account information, ignores the details, and returns the fixed sample bearer credential.

**Call relations**: Core calls this through the auth proxy spec declared in `manifest()`.

*Call graph*: 1 external calls (__init__).


##### `SampleSearchProvider.search`  (lines 1289–1297)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: Returns a canned web-search result and direct answer. It exercises the search provider seam without external HTTP calls.

**Data flow**: It receives a search query and returns one fixed hit plus a fixed answer string.

**Call relations**: Core research tools call this through the search provider spec in `manifest()` when the sample backend is selected.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleSearchProvider.fetch`  (lines 1299–1300)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: Returns a canned fetched page for a URL. It proves the provider's fetch capability is available.

**Data flow**: It receives a fetch request, copies the requested URL, and returns fixed page text.

**Call relations**: Core calls this through the search provider when it needs to fetch a result page.

*Call graph*: 1 external calls (__init__).


##### `build_flag_provider`  (lines 1303–1316)

```
def build_flag_provider(_cache_ttl_seconds: float) -> InMemoryProvider
```

**Purpose**: Builds an in-memory feature-flag provider with one true flag and one false flag. Feature flags let code paths be switched on or off by key.

**Data flow**: It receives a cache time value, constructs two in-memory flags with fixed variants, and returns an in-memory provider containing them.

**Call relations**: Core calls this through the flag-provider spec in `manifest()` when setting up sample feature flags.

*Call graph*: 2 external calls (InMemoryFlag, InMemoryProvider).


##### `SampleMemorySearch.search`  (lines 1325–1343)

```
async def search(self, queries: tuple[str, ...], reader: SourceReader, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: Records a scoped memory search and returns one fixed memory match. It proves custom memory search providers receive queries and subject boundaries.

**Data flow**: It receives query strings, a source reader, and optional start/end times. It stores the queries, subjects, and time filters, then returns one fixed memory match.

**Call relations**: Core calls this through the memory search provider declared in `manifest()` when memory search is requested.

*Call graph*: 2 external calls (__init__, isoformat).


##### `SampleMemorySearch.listable_kinds`  (lines 1345–1346)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: Reports which memory kinds this provider can list. In the sample there is only one kind.

**Data flow**: It returns a tuple containing the fixed sample memory kind.

**Call relations**: Core calls this before or during recent-memory listing to know what categories are available.


##### `SampleMemorySearch.list_recent`  (lines 1348–1374)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: Records a recent-memory listing request and returns one fixed memory match. It proves listing receives subject, kind, limit, and cursor data.

**Data flow**: It receives subjects, a limit, optional kinds, and optional cursor. It stores those request details in the extension store and returns a listing page with one memory row.

**Call relations**: Core calls this through the memory search provider when listing recent memory items.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleCarrier.__init__`  (lines 1386–1387)

```
def __init__(self) -> None
```

**Purpose**: Initializes the sample sandbox carrier's in-memory file map. A carrier is the backend that creates and talks to execution sandboxes.

**Data flow**: It creates an empty dictionary where later writes store file bytes by path.

**Call relations**: Core constructs this class through the carrier spec declared in `manifest()` when selecting the sample carrier.


##### `SampleCarrier.create`  (lines 1389–1395)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Pretends to create a sandbox and returns a handle for it. No real container is started.

**Data flow**: It receives a sandbox spec and returns a handle with the conversation id, fixed container id, run token, and a runtime root path.

**Call relations**: Core calls this through the carrier protocol when it wants a new sandbox from the sample backend.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.attach`  (lines 1397–1405)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Pretends to attach to an existing sandbox. It succeeds only when the spec includes a resume id.

**Data flow**: It receives a sandbox spec. If there is no resume id it returns `None`; otherwise it returns a handle using the resume id as the container id.

**Call relations**: Core calls this through the carrier protocol when resuming a sandbox.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.exec`  (lines 1407–1410)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Pretends to run a command in the sandbox by echoing the command arguments. It gives tests a clear proof of which carrier ran.

**Data flow**: It receives a sandbox handle, argument tuple, and timeout, joins the arguments with spaces, and returns that as stdout with exit code zero.

**Call relations**: Core calls this through the carrier protocol for sandbox command execution.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.write`  (lines 1412–1413)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Stores bytes at a sandbox path in memory. It models copying a file into a sandbox.

**Data flow**: It receives a handle, path, and bytes, then saves the bytes under that path in the carrier's dictionary.

**Call relations**: Core calls this through the carrier protocol when it writes files into the sample sandbox.


##### `SampleCarrier.read`  (lines 1415–1418)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams bytes previously written to a sandbox path. It raises an error if the path was never written.

**Data flow**: It receives a handle and path, checks the in-memory dictionary, and yields the stored bytes once.

**Call relations**: Core calls this through the carrier protocol when reading files back from the sample sandbox.


##### `SampleCarrier.file_op`  (lines 1420–1423)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Delegates generic filesystem operations to the SDK helper. It lets the sample carrier support the shared file-operation vocabulary.

**Data flow**: It receives a handle, operation name, and parameters, passes them to `ufo_fs_file_op`, and returns that helper's result.

**Call relations**: Core calls this for structured sandbox file operations. The helper calls back into the carrier's read/write-style methods as needed.

*Call graph*: 1 external calls (ufo_fs_file_op).


##### `SampleCarrier.dial`  (lines 1425–1426)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Returns a target address for reaching a port inside the sample sandbox. Dialing means asking how to connect to a service in the sandbox.

**Data flow**: It receives a handle and port, builds a host string from the fixed container name and port, marks TLS as false, and returns it.

**Call relations**: Core calls this through the carrier protocol when it needs to connect to a sandbox port.

*Call graph*: 1 external calls (__init__).


##### `resolve_workspace`  (lines 1429–1437)

```
def resolve_workspace(request: Request) -> UUID | None
```

**Purpose**: Identifies the workspace from a bearer token in an HTTP request. If the request lacks a valid bearer token, it rejects identification by returning `None`.

**Data flow**: It reads the Authorization header, checks for the `Bearer` scheme and a non-empty token, then asks `workspace_claim` to extract the workspace id.

**Call relations**: Used as the route identifier for the sample route, and called by `resolve_surface_workspace` for surface routes.

*Call graph*: called by 1 (resolve_surface_workspace); 1 external calls (workspace_claim).


##### `resolve_surface_workspace`  (lines 1440–1442)

```
async def resolve_surface_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: Asynchronously identifies the workspace for a surface request. It reuses the same bearer-token logic as normal routes.

**Data flow**: It receives a request and surface auth object, ignores the auth object, calls `resolve_workspace`, and returns that result.

**Call relations**: Registered as the identify function for both sample surfaces in `manifest()`.

*Call graph*: calls 1 internal fn (resolve_workspace).


##### `_conversation_slot_summary`  (lines 1445–1446)

```
async def _conversation_slot_summary(_ctx: ConversationSlotContext) -> None
```

**Purpose**: Provides the summarize hook for the sample conversation slot. This sample slot has nothing to summarize.

**Data flow**: It receives conversation-slot context and returns `None` without changing anything.

**Call relations**: Registered in `manifest()` as the conversation slot summarizer. Core may call it when preparing slot content.


##### `_conversation_slot_read`  (lines 1449–1450)

```
async def _conversation_slot_read(_ctx: ConversationSlotContext) -> WorkspaceChanges
```

**Purpose**: Reads the sample conversation slot content. It returns an empty workspace-change set.

**Data flow**: It receives conversation-slot context and returns `WorkspaceChanges` with no changes and `truncated` set to false.

**Call relations**: Registered in `manifest()` as the conversation slot reader. Core calls it when assembling the slot's displayed or prompt content.

*Call graph*: 1 external calls (__init__).


##### `_workspace_fact_held`  (lines 1453–1457)

```
async def _workspace_fact_held(ext: ExtensionContext) -> bool
```

**Purpose**: Checks whether the sample workspace fact currently applies. A workspace fact is a line that can be added to the agent prompt when true.

**Data flow**: It receives an extension context, reads a fixed key from the extension store, and returns true only if the value is exactly true.

**Call relations**: Registered as the hold-check for the sample workspace fact in `manifest()`. Core calls it while assembling prompt facts.


##### `manifest`  (lines 1460–1743)

```
def manifest() -> Manifest
```

**Purpose**: Declares everything the sample extension contributes to UFO. This is the entry point core reads to learn which tools, hooks, routes, backends, and other capabilities exist.

**Data flow**: It creates the sample broker and returns a `Manifest` filled with tool definitions, object kinds, jobs, routes, onboarding, agents, credentials, connectors, hooks, surfaces, sources, indexes, embeds, models, hubs, terminals, skills, browser providers, carriers, auth proxies, search providers, flags, memory search, and conversation slots.

**Call relations**: The extension loader calls this function when installing or starting the extension. Nearly every handler and helper in this file is referenced from the returned manifest so core can call it at the right time.

*Call graph*: 43 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+15 more)).
