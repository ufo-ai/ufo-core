# Connector, credential, and source manifests  `stage-3.1.3`

This stage is the system’s registration desk for connector-based extensions. It runs behind the scenes, mostly during startup, so the main application knows which outside services exist, how users can sign in to them, and what tools or content sources they add. A “manifest” is a small declaration file, like a menu card, that tells the host what an extension offers.

The Composio manifest registers Composio-backed connectors, their login path, and the shared server-side broker that talks to Composio. The connectors manifest advertises the general connector extension, including its tools, object type, and prompt text. The Pipedream manifest lists Pipedream connectors and the browser sign-in and redirect routes used for OAuth, which is a standard “log in through another service” flow. The Slack manifest wires in Slack routes, credentials, setup skill, and tools. The sources manifest registers searchable content sources, their needed credential slots, and authentication proxy. The YC manifest adds YC tools, credentials, searchable sources, onboarding, and skills.

## Files in this stage

### Connector Service Manifests
Registers the core connector-backed extensions and their server-side broker, OAuth, object, prompt, and tool declarations.

### `extensions/composio/ufo_ext_composio/manifest.py`

`config` · `extension startup and connector registration`

This file is the extension’s “business card” to the host application. When the system loads extensions, it needs a clear answer to questions like: What connectors do you provide? How should users sign in? What web routes should be added? This file answers those questions for Composio.

Composio is used here as a server-side bridge to many external services. Instead of this deployment receiving and storing each user’s service tokens directly, Composio keeps those secrets and this extension works through a broker. That matters because it keeps sensitive credentials out of this app while still allowing connector tools and sync jobs to run.

The main work happens in `manifest`. It creates one shared `ComposioBroker`, which is the object responsible for serving connector catalog information and running connector actions through Composio. It also creates a request forwarder for command-line credentials. Then it loops through the known connector catalog from `CONNECTORS` and turns each entry into a `ConnectorProvider`: a package containing the user-facing label, OAuth sign-in setup, broker, allowed transfer hosts, and optional command-line authentication details.

Finally, it adds a browser bridge route for the OAuth flow. This is the web stop that the consent process redirects through, so the system can connect the signed-in account back to the right workspace.

#### Function details

##### `manifest`  (lines 21–51)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension manifest, which is the host system’s structured description of everything this Composio extension provides. Someone would use it so the main app can discover the available connectors and the OAuth route needed to connect user accounts.

**Data flow**: It starts with the connector catalog in `CONNECTORS` and the allowed Composio transfer hosts. It creates a shared `ComposioBroker`, a `ComposioRequestForwarder`, and then turns each catalog entry into a `ConnectorProvider` with OAuth settings, a label, broker access, and optional command-line credential forwarding. It returns a `Manifest` containing the extension name, version, all connector providers, and the OAuth bridge route.

**Call relations**: During extension loading, the host calls `manifest` to learn what to install. Inside that call, this function asks `CONNECTORS` for every known Composio connector, builds each `ComposioOAuthProvider` and `ConnectorProvider`, adds optional `CliCredential` support when a connector has a command-line environment variable, and registers a `RouteSpec` that points browser OAuth redirects to `oauth_route` while using `connect_bridge_workspace` to identify the workspace.

*Call graph*: 8 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, items).


### `extensions/connectors/ufo_ext_connectors/manifest.py`

`config` · `startup`

This file is like the label and instruction card that comes with a plug-in. The connectors extension lets the system work with external connector providers through a shared set of tools: list available connectors, describe them, search them, and run them. Instead of each connector provider declaring its own separate tool surface, this file declares one workspace-wide set of connector tools that can work across all registered providers.

At import time, it defines the extension name and version, then reads a Markdown prompt snippet from `prompts/connectors_section.md`. That prompt section is the text the larger system can include when explaining to the model how external connector tools should be used.

The main function, `manifest`, packages all of this into a `Manifest`. A manifest is the extension’s official registration form: it names the extension, gives its version, lists the tools it contributes, lists the connector object it makes available, and attaches the prompt section. In plain terms, this file does not perform connector work itself. It makes sure the rest of the application knows the connector extension exists and knows how to present and expose it.

#### Function details

##### `manifest`  (lines 21–28)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension’s registration record. The system uses this record to discover the connector tools, the connector object type, and the prompt text that should be added for this extension.

**Data flow**: It starts with constants already defined in the file: the extension name, version, connector tools, connector object, and prompt section text read from disk. It wraps the prompt text in a `PromptSection`, then places everything into a `Manifest`. The result is a single manifest object that the host system can read to install or activate this extension’s contributions.

**Call relations**: When the extension system asks this module what it provides, this function is the answer. It creates a prompt section first, then hands that section, the tools, and the object declaration to the manifest constructor so the broader application can wire the connector extension into the workspace.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/pipedream/ufo_ext_pipedream/manifest.py`

`config` · `startup / extension load`

This file is the extension’s “registration card.” When the application loads extensions, it asks this file for a Manifest, which is a compact description of what the extension provides. Here, the extension declares a set of Pipedream-backed connectors, one for each entry in the CONNECTORS catalog. Each connector gets three important pieces: an OAuth provider, which starts the user consent flow; a human-facing label, which is what people see; and a shared PipedreamBroker, which is the object the system uses later to run actions and work with stored credentials.

Pipedream is acting like a secure middle office. It keeps each connected account’s token on its own server, so this deployment does not receive or store those secrets directly. That matters especially for providers such as Gmail, where OAuth permissions can be restricted and hard to support through a shared client.

The file also declares one browser bridge route. This is the path the user’s browser comes back through during the OAuth consent process. The route is tied to a workspace-identification helper so the system can connect the returning consent result to the right workspace. In short, this file does not run connector actions itself; it makes the connectors and their login route visible to the wider application.

#### Function details

##### `manifest`  (lines 21–43)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the Pipedream extension manifest, which is the object the host application reads to discover this extension’s connectors and routes. It is used when the application is starting up or loading extensions.

**Data flow**: It starts with the connector catalog in CONNECTORS and creates one shared PipedreamBroker. For each catalog entry, it builds a ConnectorProvider using the right OAuth settings, label, broker, and allowed transfer hosts. It also creates a RouteSpec for the OAuth browser return path. The result is a Manifest containing the extension name, version, all connector declarations, and the OAuth route.

**Call relations**: When called by the extension-loading system, this function assembles the pieces supplied by the Pipedream extension. It calls on CONNECTORS.items to walk through the available connector definitions, creates PipedreamOAuthProvider objects for login, wraps them in ConnectorProvider entries, creates the shared PipedreamBroker used later for connector work, and adds a RouteSpec pointing to oauth_route with connect_bridge_workspace used to identify the workspace during the browser bridge step.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, __init__, items).


### Slack Integration Manifest
Declares the Slack-specific routes, credentials, setup skill, and tools needed to expose Slack as an integration.

### `extensions/slack/ufo_ext_slack/manifest.py`

`config` · `startup / extension discovery`

This file is like the Slack extension’s registration card. When the core system discovers the extension, it calls this file to learn what Slack needs and what Slack can do. Without it, the rest of the Slack code might exist, but the system would not know which web requests belong to Slack, which secrets a workspace must store, or which setup tools should be available.

The manifest separates two ways of installing Slack. In the preferred path, the deployment owns a Slack app and uses OAuth, which is the standard “approve this app” flow. The deployment-level Slack client settings come from environment variables, not from per-workspace credential slots. After OAuth succeeds, the system receives a workspace-specific bot token. In the alternative “bring your own app” path, a user creates their own Slack app and privately supplies two workspace secrets: the bot token and the app signing secret.

The manifest also defines Slack as a “surface,” meaning an outside place where users can interact with the agent. It registers routes for incoming Slack messages, Slack interactive actions, and the OAuth callback. It connects those routes to the Slack surface functions that receive messages, post replies, attach context, and identify the workspace.

#### Function details

##### `manifest`  (lines 36–67)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the Slack extension manifest, which is the object the core system reads to know how to install and run Slack support. It describes required workspace secrets, web endpoints, posting behavior, workspace identification, setup skills, and Slack-related tools.

**Data flow**: It starts with constants and imported Slack functions: the extension name and version, credential slot names, route handlers, posting helpers, setup tools, and the path to the Slack setup skill. It packages those into credential entries, surface route entries, a surface definition, and a skill entry. The result is one complete Manifest object that the core system can use to wire Slack into the application.

**Call relations**: During extension discovery, the core system calls this function to ask, “What does the Slack extension provide?” The function answers by constructing the smaller manifest pieces first, such as credential slots for Slack secrets, route definitions for Slack web traffic, a surface definition for Slack interactions, and a skill definition for setup. It then hands all of those pieces to the Manifest constructor so the core system receives one organized description of the extension.

*Call graph*: 5 external calls (__init__, __init__, __init__, __init__, __init__).


### Source and Search Manifests
Registers source connectors, credential slots, authentication proxies, searchable sources, onboarding, hooks, skills, and related tools.

### `extensions/sources/ufo_ext_sources/manifest.py`

`config` · `startup / extension registration`

This file is like the label and wiring diagram on a plug-in module. The rest of the application needs to know what this extension can do before it can use it: which outside services it can sync from, what credentials those services need, what data objects it creates, and what should happen when synced content changes.

The file declares a sources extension named "sources". It registers two object kinds: a source object, which represents something that can be synced, and a page object, which represents synced readable content. It also registers a page-change hook, so subscribers can be notified when a synced page changes.

For each connector in the connector registry, the file creates a source provider. A connector is the service-specific piece that knows how to talk to one outside system. The small `ConnectorSourceFactory` wraps that connector class so the sync system can ask for a ready-to-use backend when it needs one.

The file also creates one credential slot per connector. These slots are where bring-your-own-key API keys are read from when the built-in `direct` authentication proxy is used. In short, this file does not perform syncing itself. It publishes the extension’s menu of capabilities so startup code can load them and the sync runner can later use them.

#### Function details

##### `ConnectorSourceFactory.__call__`  (lines 33–34)

```
def __call__(self, _credentials: CredentialAccess) -> ConnectorBackend
```

**Purpose**: This turns a connector class into a working connector backend. The sync system can call the factory when it needs a fresh backend for one registered content source.

**Data flow**: It receives a credential access object, though this factory does not read from it directly. It creates a new connector instance from the stored connector class, wraps that connector in a `ConnectorBackend`, and returns the backend to the caller.

**Call relations**: The manifest builds one of these factories for each registered connector. Later, when the source system needs to sync using that connector, it calls this factory, which hands back the backend object that does the actual connector-based work.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 37–63)

```
def manifest() -> Manifest
```

**Purpose**: This builds the extension manifest, which is the formal description of everything this sources extension contributes to the application. Other parts of the system use this manifest to discover source backends, credential needs, auth proxy support, objects, and hooks.

**Data flow**: It reads the connector registry, then creates a set of source providers from those connectors. It also creates matching credential slots, registers the source and page object types, adds the page-change hook, and declares a `direct` authentication proxy that can read the configured credentials. It returns one complete `Manifest` object containing all of that information.

**Call relations**: This is the main entry point for the extension’s declaration. Extension-loading code calls it during startup, then uses the returned manifest to add these source backends, credential slots, object definitions, hooks, and authentication proxy options to the wider system.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, items).


### `extensions/yc/ufo_ext_yc/manifest.py`

`config` · `extension load and onboarding`

This file does not do the YC work itself. Instead, it explains to the host application how the YC extension should be plugged in. Think of it like the label and setup sheet that comes with a new appliance: it names the appliance, lists what buttons it has, says what power source it needs, and gives setup instructions.

The file defines three tools. `yc_auth` lets a workspace owner connect a YC account through a browser approval flow. `yc_read` lets the system read YC and Bookface information through an authenticated command-line tool, but only in a read-only way. `yc_index` saves a bounded YC or Bookface search into shared workspace memory so it can be refreshed and reused later.

It also declares one credential slot, meaning one named place where the encrypted YC login information is stored. The descriptions are careful to say that credentials stay outside chat and the sandbox. The file also registers source providers, which are ways for the system to build searchable YC content sources, and an onboarding step that pre-registers standard YC guidance collections. Finally, it points to a skills directory containing YC research guidance. Without this file, the extension’s pieces might exist in code, but the main system would not know how to discover, configure, or safely expose them.

#### Function details

##### `setup_sources`  (lines 77–84)

```
async def setup_sources(ctx: ExtensionContext) -> None
```

**Purpose**: This function prepares the shared YC guidance sources during onboarding. It makes sure each known YC guidance collection is registered as a source that the workspace can use.

**Data flow**: It receives an extension context, which is the host system’s setup interface for this extension. It reads the list of YC guidance collections, creates a `YcSourceConfig` for each one, and asks the context to register that source under the shared workspace subject. It does not return a value; the important result is that the context now knows about these sources.

**Call relations**: This function is handed to an onboarding step by `manifest`. When that onboarding step runs, it calls into the extension context’s `register_source` method for each YC guidance collection, using `YcSourceConfig` to describe which collection should be connected.

*Call graph*: calls 1 internal fn (register_source); 1 external calls (__init__).


##### `manifest`  (lines 87–110)

```
def manifest() -> Manifest
```

**Purpose**: This function builds and returns the extension manifest, which is the main description the host system uses to load the YC extension. It gathers the extension name, version, credential needs, tools, sources, onboarding step, and skills into one object.

**Data flow**: It starts from constants and tool definitions already declared in the file. It creates a credential slot for the YC account, source provider information for building YC sources, an onboarding step that points to `setup_sources`, and a skill specification pointing to the YC research skills folder. It returns a `Manifest` object containing all of that setup information.

**Call relations**: The extension system calls this function when it needs to discover what the YC extension offers. Inside, it creates the objects that describe credentials, source building, onboarding, and skills; the onboarding object it creates is what later leads to `setup_sources` being run.

*Call graph*: 5 external calls (__init__, __init__, __init__, __init__, __init__).
