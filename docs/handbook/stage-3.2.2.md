# External connector and communication manifests  `stage-3.2.2`

This stage is part of startup and behind-the-scenes setup. It is made of “manifest” files, which are like sign-up sheets for extensions. Each manifest tells the main UFO system what an outside service can do, what credentials or secrets it needs, and which web addresses or background jobs should be wired in.

The Composio and Pipedream manifests register collections of third-party app connectors. They describe how a user starts browser-based sign-in, often through OAuth, a standard way to grant access without sharing a password, and where the browser should return after consent. The general connectors manifest adds shared connector tools, connector object types, and prompt text so the agent knows how to use them. The iMessage manifest registers a messaging tool, its message surface, and required cloud credentials. The Slack manifest connects incoming Slack messages, installation routes, reply tools, secrets, and background hooks. The sources manifest registers external content providers, authentication choices, sync hooks, and retry work so connected content can be kept up to date.

## Files in this stage

### Connector broker manifests
These manifests register shared and brokered connector capabilities, sign-in flows, request mediation, object types, and prompt additions.

### `extensions/composio/ufo_ext_composio/manifest.py`

`config` · `startup / extension discovery`

This file is like the front desk card for the Composio extension. When the main system loads extensions, it needs a clear answer to questions such as: What is this extension called? What connectors does it offer? How do users connect an account? Where should browser sign-in callbacks go? This file answers those questions by building a Manifest, which is the package of information the host application uses to plug the extension in.

Most Composio tools are served through a shared resolver, meaning the system can look up a tool by its Composio slug instead of requiring every connector to be written out one by one. A shared ComposioBroker is created so connector grants can be checked and used without sending account secrets into this deployment. Composio keeps the real user tokens on its own server side.

For the connectors that do need explicit declarations, this file creates ConnectorProvider objects. Each provider gets an OAuth provider, which describes the browser sign-in flow; a human-facing label; the shared broker; allowed transfer hosts; and, when needed, a command-line credential rule. That CLI rule says which environment variable to read and which HTTP header should carry it.

Finally, the file registers one GET route for the OAuth bridge. That route is the doorway the browser uses during consent, and it identifies the current workspace before finishing the connection.

#### Function details

##### `manifest`  (lines 24–53)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the Manifest object that describes the Composio extension to the host application. Someone would use it when loading the extension so the system knows its connectors, sign-in route, broker, resolver, and optional command-line credential behavior.

**Data flow**: It starts with no outside arguments, then creates a shared ComposioBroker for account-grant decisions and a ComposioRequestForwarder for forwarding command-line credentials when needed. It reads the configured CONNECTORS and COMPOSIO_TRANSFER_HOSTS lists, turns each connector entry into a ConnectorProvider with OAuth and optional CLI credential details, adds a ComposioResolver for lookup-by-slug connectors, and adds the OAuth browser bridge route. The result is a complete Manifest object returned to the extension loader.

**Call relations**: This function is the assembly point for the file. During extension loading, the host calls it to get the extension declaration. Inside, it creates the broker, request forwarder, OAuth providers, connector providers, resolver, route specification, and final Manifest, then hands that finished Manifest back to the host so the Composio extension can be registered.

*Call graph*: 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, items).


### `extensions/connectors/ufo_ext_connectors/manifest.py`

`config` · `startup / extension load`

This is the extension’s front desk. When the UFO system loads extensions, it needs a simple answer to: “What does this extension add?” This file provides that answer for connectors.

The connectors extension is meant to expose outside services or tools through a common interface. Instead of declaring separate tools for every possible provider, this file declares one generic set of connector tools. Those tools can list, describe, search, and run whatever connectors have been registered elsewhere. In everyday terms, it is like putting one universal remote on the table, rather than a different remote for every device.

The file also declares two object types the system should understand: connection objects and connector grant objects. These represent connector-related data the rest of the system may refer to. Finally, it reads a Markdown prompt section from disk and packages it as a prompt section named `external_tools`. That prompt text teaches the assistant how to talk about or use these connector tools.

Without this file, the extension could contain useful connector code, but the main system would not know to expose its tools, recognize its objects, or include its guidance in the assistant prompt.

#### Function details

##### `manifest`  (lines 21–28)

```
def manifest() -> Manifest
```

**Purpose**: This function builds the extension declaration that the UFO system reads when loading the connectors extension. It says the extension’s name and version, which tools it adds, which connector-related object types it supports, and which prompt text should be included.

**Data flow**: It starts from constants already prepared in the file: the extension name and version, the connector tool list, the connector object definitions, and the prompt section text read from the Markdown file. It wraps the prompt text in a `PromptSection`, then puts everything into a `Manifest`. The result is a single manifest object that the host system can inspect and use.

**Call relations**: When the extension loader asks this file what the connectors extension provides, `manifest` creates the answer. Inside that answer, it calls `PromptSection.__init__` to package the prompt guidance, then calls `Manifest.__init__` to package the whole extension declaration for the rest of the system.

*Call graph*: 2 external calls (__init__, __init__).


### Message surface manifest
This manifest registers the iMessage communication surface, its tool, version, and required cloud credentials.

### `extensions/imessage/ufo_ext_imessage/manifest.py`

`config` · `startup / extension discovery`

This file is the iMessage extension's front desk. When the larger UFO system wants to discover what this extension can do, it calls the manifest function and receives a complete description of the extension.

The file names the extension as "imessage" and gives it a version. It then connects together three main pieces. First, it creates an iMessage surface, which is the part that can listen for iMessage activity, send messages, attach files, and speak through the iMessage channel. Second, it creates an iMessage connection tool. This tool lets a member prove that their phone can use the shared iMessage line, including the special case where the member must text the line first before the line is allowed to reply. Third, it lists the cloud environment variables the extension needs so deployment can check that the Spectrum project credentials are present.

A useful analogy is a restaurant menu plus setup checklist: it says what service is available, who cooks each item, and what supplies must exist before opening. Without this file, the system would not know that the iMessage extension exists, how to call its connection tool, or how to route incoming and outgoing iMessage conversations.

#### Function details

##### `manifest`  (lines 16–49)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the official manifest for the iMessage extension. This manifest is the package label the host system reads to learn the extension's name, version, available tool, message surface, and required deployment secrets.

**Data flow**: It starts with no caller-provided input. It uses the shared Spectrum cloud provider, creates an iMessage surface for message traffic, creates an iMessage connection tool for phone setup, wraps those in tool and surface definitions, adds the required credential names, and returns one Manifest object containing all of that information.

**Call relations**: When this function runs, it first creates the concrete iMessage pieces: ImessageSurface for communication and ImessageConnect for member phone connection. It then hands their methods to SurfaceSpec and ToolDef so the host system can call them later in a standard way. Finally, it packages everything into Manifest, which is the object the rest of the system uses to recognize and run this extension.

*Call graph*: 5 external calls (__init__, __init__, __init__, __init__, __init__).


### OAuth app routes
These manifests expose OAuth-driven app integrations and communication routes, including browser redirects, installation callbacks, secrets, tools, and hooks.

### `extensions/pipedream/ufo_ext_pipedream/manifest.py`

`config` · `startup / extension discovery`

This file is the extension’s front desk. When the larger system loads extensions, it asks this file for a manifest: a compact description of the extension’s name, version, connectors, and web routes.

The main problem it solves is registration. Pipedream can hold OAuth tokens for outside services, such as Gmail, on its own servers. That matters because some providers need special OAuth setup, and because this deployment does not want raw user secrets passing through it. Instead, the system can ask Pipedream to broker the connection and later run actions or sync data through that broker.

The file builds one shared PipedreamBroker, which is the worker that knows how to talk to Pipedream for actions, server-side execution, and feed-sync credentials. Then it loops through the connector catalog from CONNECTORS. For each catalog entry, it creates a ConnectorProvider with three main parts: an OAuth provider that knows how to start the Pipedream connection flow, a human-facing label, and the shared broker that will serve the connector after it is connected.

It also registers one browser bridge route. This route is used during the consent flow, when the user’s browser is sent back after approving access. In short, without this file, the rest of the system would not know that the Pipedream-backed connectors exist or where to send their OAuth callback traffic.

#### Function details

##### `manifest`  (lines 23–45)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension manifest that the host system uses to discover Pipedream-backed connectors and the OAuth bridge route. Someone would use it when loading the extension so the connectors become available to agents and user connection flows.

**Data flow**: It starts with the connector catalog in CONNECTORS and creates one shared PipedreamBroker. For each connector entry, it turns the provider name and its settings, such as host, app, and label, into a ConnectorProvider with a PipedreamOAuthProvider attached. It also creates a GET route for the OAuth redirect path. The result is a Manifest containing the extension name, version, all connector declarations, and the browser callback route.

**Call relations**: When the extension system asks this module what it provides, this function assembles the answer. It calls the connector catalog’s items method to visit every configured connector, creates the Pipedream OAuth provider and connector provider objects for each one, creates the shared broker they all use, adds the OAuth RouteSpec, and hands the finished Manifest back to the host for registration.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, __init__, items).


### `extensions/slack/ufo_ext_slack/manifest.py`

`config` · `startup and extension registration`

This is the Slack extension’s “registration card” for the larger UFO system. It does not do the Slack work itself. Instead, it names the pieces that do the work and hands them to the core framework in one structured object called a manifest.

The file declares two private credential slots for each Slack workspace: a bot token, which lets the system post and read as the Slack bot, and a signing secret, which is used when someone brings their own Slack app. It also describes the Slack “surface,” meaning the place where users interact with the agent. That surface has routes for incoming Slack events, Slack interactive actions, and the OAuth callback used when installing the app.

The manifest also connects Slack-specific actions to the rest of the system. It supplies functions for posting messages, attaching to conversations, speaking back, identifying the workspace, and finding the bot’s own Slack user ID. It registers Slack tools, a setup skill for people configuring their own Slack app, and hooks that run at important moments. One hook marks outgoing connector sends before tool use. Another starts Slack thread-following behavior when a user submits a prompt, so progress and status updates can follow the current run.

An everyday analogy: this file is like the front desk checklist for opening a Slack branch office. It says which doors exist, which keys are required, which staff to call for each job, and which routines must happen when a customer walks in.

#### Function details

##### `manifest`  (lines 51–92)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the Slack extension manifest, which is the core system’s complete description of how Slack should be installed, reached, and used. The system calls this so it can discover Slack routes, credentials, tools, hooks, and setup skills in a standard shape.

**Data flow**: It starts with constants and imported Slack functions, such as route handlers, credential slot names, tool definitions, and setup-skill paths. It packages those into credential descriptions, surface route descriptions, hook descriptions, and a skill description. The result is one Manifest object that the larger system can read to wire Slack into the running application.

**Call relations**: During extension loading, the core asks this function for the Slack manifest. The function creates the smaller manifest pieces, such as credential slots, surface routes, hook specs, and the setup skill entry, then hands them to the Manifest constructor. After that, the core framework uses the returned manifest to route Slack web requests, expose Slack tools, run Slack hooks, and know which workspace secrets must be stored.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, __init__, __init__).


### Source synchronization manifest
This manifest registers external content source connectors, authentication options, object types, hooks, and retry jobs for syncing providers.

### `extensions/sources/ufo_ext_sources/manifest.py`

`config` · `startup / extension load`

This file is the extension’s sign-up sheet. When the application starts or loads extensions, it asks this file for a Manifest, which is a structured description of everything the sources extension contributes. Without it, the rest of the system would not know that these connectors exist, what credentials they need, which events they listen to, or which background job should repair missed setup work.

The extension supports one source backend for each registered connector in CONNECTORS. A connector is the provider-specific piece that knows how to talk to an outside service, while ConnectorBackend wraps it in the common syncing shape expected by the platform. The file also declares credential slots for “BYOK” keys, meaning “bring your own key”: a user or deployment supplies an API key directly instead of going through another broker service.

It also registers a “direct” authentication proxy, which reads those stored credential slots and gives connectors the credentials they need. Beyond connector setup, the manifest declares three object kinds: sources, pages, and source triggers. These are the shapes the system uses to create synced sources, expose synced pages, and react to shared-source changes. Finally, it wires in event hooks for page changes and recorded connections, plus a scheduled retry job that periodically tries to create connected sources that did not get created the first time.

#### Function details

##### `ConnectorSourceFactory.__call__`  (lines 41–42)

```
def __call__(self, _credentials: CredentialAccess) -> ConnectorBackend
```

**Purpose**: This turns a connector class into a ready-to-use source backend. It is used when the system needs to build the syncing backend for one registered provider.

**Data flow**: It receives a credential access object, though this particular factory does not read from it. It creates a fresh connector instance from the connector class stored in the factory, wraps that connector in a ConnectorBackend, and returns the backend that the sync system can use.

**Call relations**: The manifest creates one ConnectorSourceFactory for each registered connector and gives it to a SourceProvider. Later, when the platform needs that provider’s backend, it calls this factory; the factory then hands back a ConnectorBackend built around the provider-specific connector.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 45–82)

```
def manifest() -> Manifest
```

**Purpose**: This builds the complete declaration for the sources extension. The host application uses it to discover what the extension adds: source providers, credential slots, authentication proxy, object kinds, hooks, and a scheduled retry job.

**Data flow**: It starts from constants in this file and the connector registry. It turns each registered connector into a source provider, creates a matching credential slot for direct API-key access, adds the direct authentication proxy, adds event hooks, adds the retry job and its candidate workspaces, and returns one Manifest object containing all of that information.

**Call relations**: The extension loader calls this function when it is collecting capabilities from installed extensions. Inside this function, the code gathers pieces from the connected-account logic, page logic, source tools, direct authentication proxy, and connector registry, then packages them into the Manifest so the wider system can call the right handlers and build the right backends at runtime.

*Call graph*: 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, connection_workspaces, items).
