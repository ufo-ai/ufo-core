# Connector and external service extension manifests  `stage-3.2.2`

This stage is part of the system’s behind-the-scenes setup. Each manifest is like a registration card that tells the main UFO application what an external service extension can do and how to plug it in safely. These files do not usually run the main work themselves. Instead, they make services visible to the host so the rest of the system can use them.

The Composio and Pipedream manifests register connector providers, including OAuth sign-in routes, which are web paths used when a user grants access to another service. Pipedream also registers a broker, the part that runs actions through that provider. The connectors manifest declares shared connector tools, object types, and assistant prompt text. The gbrain and sources manifests describe external content sources, the credentials they may need, and the stored objects or sync jobs they support. The iMessage manifest registers a messaging surface, user action, and required deployment secrets. The Slack manifest registers incoming Slack messages, needed credentials, tools, and background hooks. Together, these manifests let UFO discover outside services at startup and route messages, credentials, syncing, and actions to the right extension.

## Files in this stage

### Connector Registries
Manifests that introduce connector-oriented extensions and their host-facing registration details.

### `extensions/composio/ufo_ext_composio/manifest.py`

`config` · `startup / extension load`

This file is the extension’s “front desk sign.” When the larger system discovers the Composio extension, it needs to know three things: what the extension is called, what version it is, and what pieces should be plugged into the host app. The `manifest()` function provides that bundle of information.

Composio is used here as a server-side connector provider. That means user account tokens stay with Composio rather than being copied into this deployment. The extension creates a `ComposioBroker`, which acts like the middleman that knows how to work with Composio’s connector grants. It then creates a `ComposioResolver`, which lets the host look up Composio toolkits by their slug, or short name.

The file also registers one HTTP route: a `GET` route for the OAuth consent redirect. OAuth is the common “sign in and grant access” flow used by services such as Google or Slack. This route is the doorway the browser comes back through after the user grants access. The route uses `connect_bridge_workspace` to identify the workspace involved in that connection flow.

Without this manifest, the host would not know that the Composio extension exists, how to resolve its connectors, or where to send the OAuth callback.

#### Function details

##### `manifest`  (lines 18–32)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension declaration that the host application reads when loading Composio. Someone would use it to plug Composio’s connector lookup and OAuth callback route into the larger system.

**Data flow**: It starts with no outside input. It creates a `ComposioBroker`, passes that broker into a `ComposioResolver`, builds a route description for the OAuth callback path, and packages everything with the extension name and version into a `Manifest`. The result is a complete manifest object that tells the host what this extension offers and how to wire it in.

**Call relations**: During extension loading, the host calls `manifest` to ask this file for its setup instructions. Inside that setup, it calls `ComposioBroker.__init__` to make the broker, `ComposioResolver.__init__` to make the connector resolver, `RouteSpec.__init__` to describe the OAuth web route, and `Manifest.__init__` to wrap all of those pieces into the final object the host can install.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).


### `extensions/connectors/ufo_ext_connectors/manifest.py`

`config` · `startup`

This file exists so the connectors extension can be discovered and plugged into the larger UFO system in a standard way. Without it, the application would not know that this extension provides connector-related tools, connection objects, grant objects, or extra prompt instructions.

The file names the extension as “connectors” and gives it a version. It also reads a Markdown prompt section from disk. That prompt text is used to teach or remind the assistant how external connector tools should appear in its working context.

The important idea is that these tools are declared once, at the workspace level. They are not tied to one specific connector provider. Instead, they work across whatever connector providers other broker extensions register. In everyday terms, this file puts a common tool shelf in the workshop, while other extensions can later add their own specialized tools or supplies to that shelf.

The `manifest` function packages all of this into a `Manifest` object: the extension name and version, the connector tools, the shared connection-related object definitions, and the prompt section. The host can then read that package during setup and make the extension available.

#### Function details

##### `manifest`  (lines 21–28)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension declaration that tells the host system what the connectors extension contributes. Someone would use this when loading extensions so the connector tools, objects, and prompt guidance become available.

**Data flow**: It starts with constants from this file: the extension name, version, prompt section name, and prompt text read from the Markdown file. It also uses imported connector tools and object definitions. It wraps the prompt text in a `PromptSection`, then puts everything into a `Manifest`, which is returned to the caller.

**Call relations**: When the extension system asks this module what it provides, this function creates the answer. Inside that answer, it hands the prompt text to `PromptSection` so it has a named place in the assistant prompt, then hands that section plus the tools and object types to `Manifest` so the host can register them together.

*Call graph*: 2 external calls (__init__, __init__).


### Content and Message Surfaces
Manifests that register external content sources, stored object types, user actions, and message-facing surfaces.

### `extensions/gbrain/ufo_ext_gbrain/manifest.py`

`config` · `startup`

This is the extension’s “front desk” file. When UFO loads the gbrain extension, it needs a clear summary of what the extension can do. This file provides that summary as a Manifest, which is a small declaration of the extension’s name, version, supported source types, needed credentials, and registered object kind.

The extension offers two ways to read gbrain pages. One source reads Markdown files from a GitHub repository. The other reads Markdown files from a local folder, useful for serving or testing content from disk. The GitHub source can use a GitHub token so private repositories can be opened; public repositories do not need it.

The file also registers the gbrain object type, which tells the larger system what kind of synced item this extension contributes. In everyday terms, this file is like a sign-up sheet: it says, “I am the gbrain extension, here are the content sources I support, here is the optional key I may need, and here is the kind of content I produce.” Without it, the system would not know how to discover or start the gbrain backends.

#### Function details

##### `manifest`  (lines 16–38)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the gbrain extension’s declaration for the UFO system. Someone uses this when the system is discovering extensions and needs to know what gbrain provides.

**Data flow**: It starts with fixed information from this file and imported gbrain modules: the extension name and version, the gbrain object type, the Git and folder backend identifiers, and the GitHub token credential name. It packages those into a Manifest. The result is a complete description of the extension, including two source providers and one credential slot.

**Call relations**: When the extension is loaded, this function is the place that assembles its public registration information. It creates SourceProvider entries for the GitHub and local-folder sources, creates a CredentialSlot for the optional GitHub token, and hands all of that into the Manifest constructor so the wider system can later build the right source backend when a gbrain origin is synced.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/imessage/ufo_ext_imessage/manifest.py`

`config` · `startup`

This file is the front door for the iMessage extension. A UFO extension needs a manifest, which is like a registration card: it says what the extension is called, what it can do, how the main system can talk to it, and what secret settings must exist before it can run.

Here, the extension offers one main action: connecting a member’s iMessage phone to a workspace. It also registers an iMessage “surface,” meaning a communication channel where the system can listen for messages, send messages, attach content, and speak back to users. In plain terms, the surface is the bridge between UFO and iMessage.

The file wires these pieces to a shared provider called `line_provider`, which likely knows how to work with the underlying messaging service and assigned phone lines. It also marks the connect action as untrusted and side-effecting. That means the system should treat user input carefully, and it should expect the action to change something outside the program, such as starting or completing a phone connection.

Without this file, the iMessage code might exist, but the larger UFO system would not know how to discover it, call its connect action, route iMessage traffic through it, or require the needed cloud credentials.

#### Function details

##### `manifest`  (lines 21–55)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the iMessage extension’s manifest, which is the object the UFO platform reads to learn what this extension provides. Someone would use it when loading the extension so the platform can register the iMessage surface and the phone-connection tool.

**Data flow**: It starts with fixed extension details such as the name and version, plus the shared `line_provider` used to reach the messaging backend. It creates an `ImessageSurface` for message traffic and an `ImessageConnect` tool for phone connection. It then packages those into a `Manifest` that lists the tool, the surface, and the required deployment environment variables for the cloud project ID and secret.

**Call relations**: When the UFO platform asks this extension what it offers, this function assembles the answer. It creates the surface object, the connect-action object, the tool definition, the object binding that ties the tool to the iMessage surface, the presentation label shown to users, and the surface specification that tells the platform which methods to call for listening, posting, attaching, and speaking.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__).


### Provider Action Brokers
Manifest for provider-backed connector execution, OAuth routing, and action brokering.

### `extensions/pipedream/ufo_ext_pipedream/manifest.py`

`config` · `startup / extension registration`

This file is the extension’s sign-up sheet. When the larger application loads extensions, it asks this file for a manifest: a clear declaration of “here are the connectors I provide, here is how users authorize them, and here is the web route needed to finish that authorization.”

The problem it solves is that Pipedream connectors are not just simple labels. Each one needs several coordinated pieces: an OAuth provider, which is the login-and-consent flow that lets a user connect an outside service; a user-facing label; a broker, which is the object that later carries out actions through Pipedream; allowed transfer hosts; and sometimes a command-line credential, such as for GitHub tools in a sandbox. Without this manifest, the rest of the system would not know these Pipedream connectors exist or how to connect users to them.

The file reads a catalog of connector definitions from `CONNECTORS`. For every catalog entry, it builds a `ConnectorProvider`, using the shared `PipedreamBroker` and a `PipedreamOAuthProvider` tailored to that connector. It also registers one browser bridge route. That route is the doorway the OAuth consent flow redirects through, like a reception desk that receives the user after they approve access and sends them back to the right workspace.

#### Function details

##### `manifest`  (lines 28–51)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the Pipedream extension manifest, which is the package of information the main system needs to load this extension. It declares all Pipedream-backed connectors and the web route used during OAuth connection.

**Data flow**: It starts with no caller-provided input. It creates one shared `PipedreamBroker`, then reads every connector specification from `CONNECTORS`. For each connector, it combines the provider name, host, app, label, transfer-host rules, and optional command-line credential into a `ConnectorProvider`. It then wraps all of those connector declarations plus the OAuth bridge route into a `Manifest` object and returns it.

**Call relations**: When the extension system asks what this package contributes, this function is the answer. Inside that answer, it creates the broker that later serves connector actions, builds a `PipedreamOAuthProvider` for each catalog connector, calls `cli_credential` to add any needed command-line token support, and creates a `RouteSpec` so the OAuth browser redirect has a known place to land.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, items, cli_credential).


### Ingress and Source Sync
Manifests that wire external messages and synced content providers into the system with credentials, hooks, and jobs.

### `extensions/slack/ufo_ext_slack/manifest.py`

`config` · `startup and extension registration`

This file is the Slack extension’s manifest, meaning it describes Slack to the core application in one place. Without it, the system would not know which web routes belong to Slack, where to store Slack secrets, how to post replies back to Slack, or when to start Slack-specific helper work during a conversation.

The manifest covers two ways to connect Slack. In the normal path, a workspace installs the deploy’s Slack app through OAuth, which is a standard permission flow where Slack sends back a token after approval. In the alternative path, a user brings their own Slack app and privately provides a bot token and signing secret. The file declares those per-workspace credential slots so the core knows what secrets may be stored.

It also declares the Slack “surface,” meaning the place where users interact with the agent. That surface has routes for incoming Slack events, interactive Slack button/menu actions, and the OAuth callback. It also points to the functions that send messages, attach files or context, identify the workspace, and discover the bot’s own Slack user ID.

Finally, it wires Slack into the system’s event hooks. For example, when a user submits a prompt, Slack starts following the turn so it can report progress in the right thread. It also declares a workspace fact: only workspaces where Slack is truly installed and reachable should be told that Slack is available.

#### Function details

##### `_slack_answers`  (lines 73–79)

```
async def _slack_answers(ext: ExtensionContext) -> bool
```

**Purpose**: Checks whether Slack is not just partly installed, but actually working for this workspace. This matters because a saved installation record alone can mean setup was started but Slack messages may still not reach this deploy.

**Data flow**: It receives an extension context, which gives access to stored installation information for the current workspace. It first asks whether there is any Slack installation record; if not, it returns false. If there is a record, it asks the Slack surface whether the installation is live and returns that answer.

**Call relations**: The workspace fact declared in `manifest` uses this function as its truth test. When the core wants to decide whether to show the line saying Slack is installed, this function checks the stored installation and then hands off to `ufo_ext_slack.surface.install_is_live(ext)` for the deeper reachability check.

*Call graph*: 1 external calls (install_is_live).


##### `manifest`  (lines 82–127)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the full Slack extension manifest. The core system uses this manifest to learn what Slack credentials, routes, tools, hooks, skills, and workspace facts the extension provides.

**Data flow**: It takes no input. It gathers constants and imported Slack functions, then creates a `Manifest` object containing credential slots, Slack web routes, send and identify callbacks, tool definitions, event hooks, a setup skill, and a workspace fact. The finished manifest is returned to the extension loader.

**Call relations**: This is the main entry the core calls when loading the Slack extension. Inside it, the function constructs smaller declaration objects such as credential slots, surface routes, hook specs, a skill spec, and a workspace fact, then packages them into one `Manifest` so the rest of the system can route Slack traffic and run Slack-specific behavior at the right times.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__).


### `extensions/sources/ufo_ext_sources/manifest.py`

`config` · `startup / extension registration`

Think of this file as the extension’s front desk sign-in sheet. It does not do the syncing itself. Instead, it declares all the pieces the larger system should know about when the sources extension is installed.

The file names the extension, lists the object types it adds, and connects system events to code that should run when those events happen. For example, when a synced page changes, the manifest points the system to the page-change hook that delivers that update where it belongs. When a user connects an account, it points to a hook that creates the standard source streams for that account, so the user does not need an extra manual setup step.

It also registers a scheduled retry job. This is a safety net for cases where connected sources were supposed to be created but did not land correctly the first time.

For each connector in the shared connector registry, the file creates a matching source backend. A backend is the adapter the sync runner can call to talk to a particular provider. It also creates one credential slot per connector for “BYOK” credentials, meaning “bring your own key”: a user-provided API key. Finally, it registers a “direct” auth proxy, which lets the system read those stored keys without relying on an outside broker service.

#### Function details

##### `ConnectorSourceFactory.__call__`  (lines 41–42)

```
def __call__(self, _credentials: CredentialAccess) -> ConnectorBackend
```

**Purpose**: This turns a connector class into a ready-to-use source backend. The system can call the factory when it needs a backend for a particular provider.

**Data flow**: It receives a credential access object, though this factory does not use it directly. It creates a fresh connector instance from the stored connector class, wraps it in a ConnectorBackend, and returns that backend for the sync system to use.

**Call relations**: The manifest installs one ConnectorSourceFactory for each registered connector. Later, when the host needs to build a source backend, it calls this factory, which hands off the new connector instance to ConnectorBackend so the normal source-sync machinery can use it.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 45–82)

```
def manifest() -> Manifest
```

**Purpose**: This builds the complete declaration for the sources extension. The host application calls it to learn what objects, hooks, jobs, source providers, credential slots, and authentication proxies this extension contributes.

**Data flow**: It starts with constants and imported extension pieces, then assembles them into a Manifest object. It reads the connector registry to create one SourceProvider and one CredentialSlot for each connector. It also adds event hooks, a scheduled retry job, and the direct auth proxy builder. The result is a single Manifest value that the host can register.

**Call relations**: This is the central handoff point from the extension to the host system. While building the manifest, it creates HookSpec entries for page-change and connection-recorded events, a JobSpec for retrying connected-source creation, SourceProvider entries backed by ConnectorSourceFactory, CredentialSlot entries for direct API keys, and an AuthProxySpec that builds DirectAuthProxy when direct credentials are needed.

*Call graph*: 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, connection_workspaces, items).
