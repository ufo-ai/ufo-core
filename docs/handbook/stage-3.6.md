# Connector, source, automation, and scheduled-job extension manifests  `stage-3.6`

This stage is part of startup and extension discovery. Each manifest is like a label on a plug-in box: it tells the host system what the extension can do, what permissions it needs, and what background work or web routes should be wired in.

The Composio and Pipedream manifests register external app connectors and the OAuth sign-in routes used to connect a user’s accounts. The connectors manifest defines shared connector tools, the connector object type, and prompt text that teaches the assistant how to talk about outside tools. The sources manifest adds connector-based content sources, the stored objects they create, the credentials they require, and a hook that reacts when synced page content changes. Page alerts build on that by registering chat tools for watching pages and a background hook that responds to page updates. Scheduled tasks declare a tool, object type, scheduling skill, and recurring job so work can run later. Self-improvement registers a scheduled evaluation job. The YC manifest adds authenticated YC and Bookface reading through tools, sources, onboarding, credentials, and skills.

## Files in this stage

### Connector registration
These manifests make external connector capabilities discoverable, including shared connector tools and OAuth-based account linking.

### `extensions/composio/ufo_ext_composio/manifest.py`

`config` · `startup / extension discovery`

Think of this file as the extension’s front-desk registration form. When the main application loads extensions, this file produces a manifest, which is a structured description of what Composio can do and how the rest of the system should talk to it.

Composio provides access to many external tools, such as GitHub or other services, while keeping each user’s real service tokens on Composio’s servers. That matters because this deployment does not need to store those secrets itself. Instead, it uses a shared broker, `ComposioBroker`, as the middleman that knows how to start and use Composio-backed connections.

The file also creates explicit connector entries for the special connectors listed in `CONNECTORS`. Each entry gets an OAuth provider, a human-facing label, optional command-line credentials, and allowed transfer hosts. OAuth means the familiar “sign in and grant access” flow. For connectors that need command-line credentials, it sets up a credential rule that reads a specific environment variable and forwards it in the `authorization` header.

Finally, the manifest registers a small browser route for the OAuth consent step. That route lets the connect flow complete when a user is redirected back from the provider.

#### Function details

##### `manifest`  (lines 24–53)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the Composio extension manifest, which is the object the host application reads to learn what this extension provides. It declares connector providers, a general resolver for Composio toolkits, and the web route needed for account-connection redirects.

**Data flow**: It starts with constants and imported connector definitions, then creates one shared `ComposioBroker` and one request forwarder. For every configured connector, it builds a connector provider with its OAuth setup, label, broker, allowed transfer hosts, and optional command-line credential forwarding. It then packages those providers, the broad Composio resolver, and the OAuth callback route into a `Manifest` object and returns it to the caller.

**Call relations**: The host application calls this when loading the extension. Inside, it creates the broker, OAuth providers, connector providers, resolver, route specification, and optional CLI credential objects so the rest of the system can later discover connectors, start connection flows, route OAuth callbacks, and forward command-line authentication when needed.

*Call graph*: 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, items).


### `extensions/connectors/ufo_ext_connectors/manifest.py`

`config` · `extension discovery and startup`

This file is the “front desk sign” for the connectors extension. When the larger system loads extensions, it needs a standard way to ask: what are you called, what tools do you add, what objects do you expose, and what instructions should be shown to the AI? This file answers those questions in one place.

The connector tools are deliberately declared here as a shared, workspace-wide set. They are not tied to one specific provider, such as one OAuth service or one broker. Instead, they work across whatever connector providers other broker extensions register. In plain terms, this file adds the generic buttons for “list connectors,” “describe a connector,” “search,” and “run connector actions,” while the actual connector catalogs can come from elsewhere.

It also reads a Markdown prompt section from `prompts/connectors_section.md`. That text becomes part of the instructions given to the AI under the section name `external_tools`. Without this file, the extension would not have a clean way to announce its tools, its connector object model, or its prompt guidance to the rest of the system.

#### Function details

##### `manifest`  (lines 21–28)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension’s manifest, which is the standard description the host system uses to discover what this extension provides. Someone would use this when loading the connectors extension so the system can register its tools, objects, and prompt section.

**Data flow**: It starts with constants already prepared in the file: the extension name and version, the list of connector tools, the connector object definition, and the prompt text read from disk. It packages those pieces into a `Manifest` object, including a `PromptSection` object for the `external_tools` instructions. The result is a complete manifest that the host can consume; the function does not modify external state itself.

**Call relations**: During extension loading, the host calls this function to ask the connectors extension what it contributes. The function creates a `PromptSection` to wrap the prompt text, then creates a `Manifest` to hold the whole declaration and hands that manifest back to the caller.

*Call graph*: 2 external calls (__init__, __init__).


### Page alert hooks
This manifest registers page-watch tools and the background hook that reacts to synced-page changes.

### `extensions/page_alerts/ufo_ext_page_alerts/manifest.py`

`config` · `startup / extension discovery`

This file is the extension’s “front desk sign.” It does not contain the alerting logic itself. Instead, it declares what the rest of the system can call. Without it, the platform would not know that this extension has tools like “watch pages,” “list watches,” or “cancel a watch,” and it would not know to run the page-change checker when a synced page is updated.

The manifest gives the extension a name and version, then builds a Manifest object. Inside that manifest are three ToolDef entries. A tool is something the chat agent can offer during a conversation. Each tool has a name, a plain-language description, an input model that says what information the tool expects, and a handler function that does the real work.

The file also declares one HookSpec for the page_change event. A hook is like asking the platform, “Please call this function whenever this kind of thing happens.” Here, when a page changes, the platform should call on_page_change, which can decide whether the change matches any saved watch and should alert the original conversation.

#### Function details

##### `manifest`  (lines 21–50)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension manifest, which is the platform-readable description of this extension’s tools and event hook. Someone would use it when loading the extension so the platform knows what the extension can do.

**Data flow**: It starts with the fixed extension name and version from this file. It creates three tool definitions, each connecting a chat-visible tool name to its expected input shape and its real handler function. It also creates a hook definition for page_change events. The result is one Manifest object that the platform can read to register all of these pieces.

**Call relations**: During extension loading, this function is the piece that gathers the extension’s public promises in one place. It calls ToolDef.__init__ to describe each chat tool, HookSpec.__init__ to describe the page-change event listener, and Manifest.__init__ to package everything together for the platform.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Pipedream authorization
This manifest declares Pipedream app connectors and the OAuth route used to authorize them.

### `extensions/pipedream/ufo_ext_pipedream/manifest.py`

`config` · `startup / extension discovery`

This file is the extension’s front desk. When the larger system loads extensions, it asks this file for a Manifest, which is a compact description of what the extension adds. Here, the extension adds Pipedream-backed connectors: integrations with outside services where Pipedream keeps the user’s access tokens on its own servers. That matters because the local deployment does not need to store or see those secrets.

The file builds one shared PipedreamBroker. A broker is the piece that knows how to use an already-connected account to run actions, do server-side work, or support sync jobs. Then it loops through the allowed connector catalog from CONNECTORS. For each catalog entry, it creates a ConnectorProvider with three important parts: an OAuth provider that knows how to start consent for that app, a human-facing label, and the shared broker that will later perform work through Pipedream.

It also registers a browser route for the OAuth bridge. OAuth is the common “sign in and allow access” process used by many services. This route is where the user’s browser is sent during that consent flow so the system can connect the right workspace. Without this manifest, the Pipedream extension would be invisible: its connectors would not appear, its broker would not be registered, and the OAuth redirect path would not exist.

#### Function details

##### `manifest`  (lines 23–45)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the Pipedream extension manifest: the system-readable list of connectors and routes this extension provides. The main application uses this to add Pipedream-backed app connections to its connector registry.

**Data flow**: It starts with the static extension name and version, reads the connector catalog from CONNECTORS, and creates one PipedreamBroker to serve all listed connectors. For every connector entry, it turns the catalog information into a ConnectorProvider with an OAuth setup, label, broker, and allowed transfer hosts. It also adds a GET route for the OAuth bridge. The output is a Manifest object that the host system can load.

**Call relations**: During extension loading, the host calls manifest to discover what this package contributes. Inside that call, it creates the shared PipedreamBroker, walks through CONNECTORS.items to build each ConnectorProvider, creates PipedreamOAuthProvider objects for the consent flow, and creates a RouteSpec for oauth_route so browser redirects can be received through connect_bridge_workspace.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, __init__, items).


### Scheduled automation
These manifests register recurring-task support and self-improvement evaluation jobs that run in the background.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/manifest.py`

`config` · `startup`

This file is like the front desk card for the scheduled-tasks extension. It does not contain the full scheduling logic itself. Instead, it announces what pieces exist and how the main system should plug them in.

The extension adds a scheduled task object, which lets an agent create, edit, list, or delete recurring tasks using the system’s usual object commands. It also adds a pause-and-wait tool, which is meant for durable waiting: the agent can pause work in a way that survives beyond a single short-running call. The file also registers a background job named scheduled_task_runner. That job runs on a clock-based schedule, once a minute, and asks the scheduled task runner to look for work that is due.

A key detail is that the runner is triggered by time, not directly by the task records it creates or updates. When it runs, it works through the extension context, which gives it access to the right workspace and schedule storage. The manifest also points the system to a skill folder named task-scheduling, which gives the agent instructions or abilities it should load before scheduling tasks. Finally, it declares that this extension depends on memory_search being available.

#### Function details

##### `_run`  (lines 28–29)

```
async def _run(ctx: ExtensionContext) -> None
```

**Purpose**: This is the small job handler that runs when the scheduled-task background job fires. It creates a ScheduledTaskRunner using the current extension context, then asks it to do the actual scheduled-task work.

**Data flow**: It receives an ExtensionContext, which is the system’s bundle of workspace-specific services and settings for this extension. It passes that context into ScheduledTaskRunner, then awaits the runner’s run process. It does not return a useful value; its effect is that due scheduled tasks may be found and invoked by the runner.

**Call relations**: The manifest registers this function as the handler for the recurring job. When the system’s job scheduler decides the scheduled_task_runner job should run, it calls _run. _run immediately hands off to ScheduledTaskRunner, keeping this file focused on wiring rather than task execution details.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 32–48)

```
def manifest() -> Manifest
```

**Purpose**: This function builds and returns the extension manifest, which is the system’s official description of what this extension contributes. The host application uses it to discover the scheduled-task tool, object type, background job, skill files, and dependency on memory search.

**Data flow**: It starts from constants in this file, such as the extension name, version, job name, cron-style schedule string, and skill folder location. It creates a JobSpec for the recurring runner, asks due_task_workspaces for the set of workspaces that may have due tasks, creates SkillSpec entries for the skill folders, and packages everything into a Manifest. The output is a Manifest object that the larger system can load.

**Call relations**: This is the main entry point for the host when it loads the extension. During startup or extension discovery, the system calls manifest to learn what to register. Inside that setup, manifest creates the job specification, points the job at _run, asks the scheduling helper for candidate workspaces, and wraps the tool, object, job, skill, and dependency declarations into one manifest.

*Call graph*: 4 external calls (__init__, __init__, __init__, due_task_workspaces).


### `extensions/self_improvement/ufo_ext_self_improvement/manifest.py`

`config` · `startup registration, then scheduled evaluation ticks`

This file is the extension’s sign-up sheet and alarm clock. At startup, the host asks the extension for its manifest, which is a small declaration saying: “my name is self_improvement, this is my version, and I have one scheduled job.” That job is called eval_cron and uses a cron schedule, meaning it runs because time passed, not because a proposal or database write happened.

When the clock triggers the job, _tick is called with an ExtensionContext. The context is the extension’s doorway into the rest of the system: it carries things like model access and the workspace being evaluated. The first safety check is important: self-improvement cannot work without a model, so the function fails fast if no model was wired in.

If model access is available, the file wraps it in ModelAccessLeg. That wrapper gives the proposer, replay step, and judge a shared, scoped way to use the model. Then it builds the three main parts of the self-improvement run: PromptProposer suggests possible prompt changes, CandidateEvaluation tests and judges them, and ImproveCron ties the whole evaluation pass together. Without this file, the extension would not be registered, and its periodic improvement loop would never start.

#### Function details

##### `_tick`  (lines 21–29)

```
async def _tick(ctx: ExtensionContext) -> None
```

**Purpose**: This is the scheduled job’s actual work function. Each time the self-improvement clock fires, it checks that model access exists, builds the proposer and evaluator, and starts one improvement run.

**Data flow**: It receives an ExtensionContext from the host system. If the context has no model, it stops with an error because the improvement process needs model calls to propose and judge changes. If a model is present, it wraps that model in ModelAccessLeg, gives the wrapper to PromptProposer and CandidateEvaluation, places both inside ImproveCron, and then runs the cron process. The output is no direct return value; the effect is that the scheduled self-improvement evaluation is carried out.

**Call relations**: The job scheduler calls this function because manifest names it as the handler for the scheduled job. Inside, it creates ModelAccessLeg first so all later pieces share the same controlled model access. It then creates PromptProposer for suggesting candidates, CandidateEvaluation for replaying and judging them, and ImproveCron as the coordinator that performs the full scheduled pass.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).


##### `manifest`  (lines 32–44)

```
def manifest() -> Manifest
```

**Purpose**: This function returns the extension declaration that the host system reads at startup. It gives the extension its name, version, scheduled job, schedule, handler, and the set of workspaces the job can run against.

**Data flow**: It takes no input. It builds a JobSpec using the job name, cron schedule, _tick as the function to call, and trajectory_workspaces() as the source of candidate workspaces. It then packages that job into a Manifest with the extension name and version, and returns that Manifest to the host.

**Call relations**: The host system calls this during extension discovery or startup. The function asks trajectory_workspaces() for the workspaces that should be considered, creates a JobSpec describing the scheduled evaluation job, and places that job inside a Manifest so the host knows when and how to call _tick later.

*Call graph*: 3 external calls (__init__, __init__, trajectory_workspaces).


### Content sources
These manifests register connector-backed content sources, credentials, searchable YC data, onboarding, and synced-page update hooks.

### `extensions/sources/ufo_ext_sources/manifest.py`

`config` · `startup / extension registration`

Think of this file as the extension’s sign-up sheet. When the application starts, it asks the extension, “What can you do?” The `manifest` function answers with a complete description: this extension is named `sources`, it has source providers built from every connector in the connector registry, and it exposes two object types: a source object and a page object. A source is the configured feed to sync from, while a page is the synced content made available for reading.

The file also declares credential slots. These are named places where users can provide their own API keys, sometimes called BYOK or “bring your own key.” Those keys are used by the built-in `direct` authentication proxy, which is the fallback way to talk to providers without going through another broker-style authentication extension.

For each registered connector, the manifest creates a source provider. The small `ConnectorSourceFactory` class turns a connector class into a real `ConnectorBackend`, which is the object the sync system can run. Finally, the manifest registers a `page_change` hook. That hook is the extension’s alarm bell: when synced page content changes, it can notify subscribers who care about that source.

#### Function details

##### `ConnectorSourceFactory.__call__`  (lines 34–35)

```
def __call__(self, _credentials: CredentialAccess) -> ConnectorBackend
```

**Purpose**: This turns a stored connector class into a ready-to-use connector backend. The sync system uses it when it needs an actual backend object for a registered source provider.

**Data flow**: It receives a credential access object, though this factory does not use it directly. It creates a new connector instance from the connector class saved on the factory, wraps that connector in a `ConnectorBackend`, and returns the backend so the sync runner can use it.

**Call relations**: The `manifest` function creates one `ConnectorSourceFactory` for each connector in the registry and gives it to a `SourceProvider`. Later, when the application needs to build that provider’s backend, the provider calls this factory, which hands back a `ConnectorBackend`.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 38–64)

```
def manifest() -> Manifest
```

**Purpose**: This builds the official manifest for the sources extension: its name, version, source backends, credential slots, authentication proxy, object types, and page-change hook. The host application reads this manifest to know how to install and run the extension.

**Data flow**: It reads the connector registry, then creates a source provider for each connector name and class. It also creates matching credential slots, declares the `direct` authentication proxy, attaches the source and page object definitions, and registers the page-change hook. The output is one `Manifest` object containing the extension’s full public declaration.

**Call relations**: This is the main entry the host calls when loading the extension. As part of building the manifest, it creates `ConnectorSourceFactory` objects for connector backends, `CredentialSlot` objects for API-key storage, a `HookSpec` for page-change notifications, `SourceProvider` entries for syncing, and an `AuthProxySpec` that builds `DirectAuthProxy` when direct credentials are needed.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, items).


### `extensions/yc/ufo_ext_yc/manifest.py`

`config` · `extension load and onboarding`

This file is the extension’s front desk. When UFO wants to know what the YC extension offers, this file returns a manifest, which is a structured description of the extension’s name, tools, credentials, background sources, and onboarding setup.

The extension provides three main tools. One connects a workspace owner’s YC account through browser-based authorization. One reads YC and Bookface information in a read-only way. One saves a bounded YC or Bookface search into shared workspace memory so it can be refreshed later. The file also declares a credential slot, which is the secure place where the owner-approved YC identity is stored. The important safety point is that the credential is used by host-side code and is not exposed to chat or to the sandbox.

The file also wires in source indexing. During onboarding, it registers a set of YC guidance collections as shared sources. You can think of these as pre-labeled shelves in a shared library: the workspace can later search or refresh them without each user having to set them up by hand.

Without this file, the rest of the YC code could exist but UFO would not know how to expose it. The tools would not appear, the credential would not be requested, the source backend would not be connected, and the YC research skill folder would not be advertised.

#### Function details

##### `setup_sources`  (lines 77–84)

```
async def setup_sources(ctx: ExtensionContext) -> None
```

**Purpose**: This function prepares the shared YC guidance sources during onboarding. It registers each known YC guidance collection so the workspace has common YC reference material available as a shared source.

**Data flow**: It receives an extension context, which is the object UFO gives extensions so they can register things with the host system. It reads the list of YC guidance collections, creates a source configuration for each one, and asks the context to register that source under the shared workspace subject. It does not return a value; its result is that the host now knows about these shared YC sources.

**Call relations**: This function is attached to the manifest as the handler for the YC onboarding step. When UFO runs that onboarding step, it calls this function, and the function hands each collection to the extension context’s source registration method so the source system can track it.

*Call graph*: calls 1 internal fn (register_source); 1 external calls (__init__).


##### `manifest`  (lines 87–110)

```
def manifest() -> Manifest
```

**Purpose**: This function builds the official description of the YC extension for UFO. It says what the extension is called, which credentials it needs, which tools it offers, which source backend it can build, what onboarding work to run, and where its skill files live.

**Data flow**: It takes no input. It uses constants and imported tool, source, credential, and skill definitions from this module and nearby YC modules. It packages them into a Manifest object and returns that object to the host system. Nothing is changed directly at this moment; it is more like handing UFO a complete menu of what the extension can provide.

**Call relations**: UFO calls this when loading the extension. Inside, it creates the credential slot, source provider, onboarding step, and skill specification that the host will later use. The source provider includes a small builder that turns stored credentials into a YC source backed by the YC command-line client.

*Call graph*: 5 external calls (__init__, __init__, __init__, __init__, __init__).
