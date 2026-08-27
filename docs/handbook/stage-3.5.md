# External Integration Extension Manifests  `stage-3.5`

This stage is the system’s set of “registration cards” for extensions that connect UFO to outside services. It is mostly used during startup and setup, when the host application needs to discover what each extension can do, what secrets it needs, and which background jobs or web routes to enable.

The Composio manifest advertises available connectors, sign-in rules, and the OAuth consent route, which is the web step where a user approves access. The shared connectors manifest packages common connector tools, data objects, and assistant instructions. The gbrain manifest registers a sync tool that can pull Markdown pages from GitHub or a local folder. The iMessage manifest declares the messaging tool, send and receive behavior, and required secret settings. The Slack manifest is a full setup sheet for Slack, including credentials, routes, hooks, tools, and setup help. The sources manifest registers source providers, their credentials, event reactions, and a background sync job. Together, these files let the main system plug in external services cleanly.

## Files in this stage

### Connector Registrations
Manifests that register external connector capabilities, shared tools, sign-in behavior, and assistant-facing instructions.

### `extensions/composio/ufo_ext_composio/manifest.py`

`config` · `startup`

This file is the extension’s “front desk.” When the host application loads the Composio extension, it asks this file for a manifest, which is a structured description of what the extension can do. The manifest names the extension, gives its version, lists explicit connector providers, installs a resolver for the wider Composio connector namespace, and registers a browser-facing OAuth route.

Composio is a service that can run tool actions on behalf of users while keeping account tokens on Composio’s servers. That matters because this deployment does not need to receive or store those secrets. Most Composio toolkits are served through `ComposioResolver`, which can resolve connectors by slug. A smaller set of explicit connectors is listed in `CONNECTORS`, mainly for cases where command-line authentication needs a real provider host, such as GitHub.

The file also creates shared helper objects. `ComposioBroker` is the shared bridge used when a connector grant is created. `ComposioRequestForwarder` forwards command-line credential requests when needed. The OAuth route is registered so browser consent can come back through the expected `/connect` bridge. In short, this file does not perform user actions itself; it wires together the pieces that let the rest of the system find, authenticate, and use Composio-backed connectors safely.

#### Function details

##### `manifest`  (lines 24–53)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension manifest that the host application uses to load Composio support. It describes the extension name and version, its connector providers, its dynamic connector resolver, and the web route needed for OAuth sign-in.

**Data flow**: It starts with constants and connector definitions imported from nearby modules. It creates one shared `ComposioBroker`, creates a request forwarder, then turns each configured connector spec into a `ConnectorProvider` with OAuth details, a user-facing label, optional command-line credentials, and allowed transfer hosts. It also creates a `ComposioResolver` for connectors that are not explicitly listed and adds a GET route for the OAuth callback. The result is a complete `Manifest` object returned to the host application.

**Call relations**: The host calls `manifest` when loading the extension. Inside that startup step, this function constructs the broker, forwarder, OAuth providers, connector providers, resolver, route specification, and final manifest. After it returns, the host uses those objects later during connector discovery, command-line credential forwarding, and browser-based OAuth consent.

*Call graph*: 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, items).


### `extensions/connectors/ufo_ext_connectors/manifest.py`

`config` · `startup / extension discovery`

This file is the extension's “front desk sign.” When the system loads extensions, it needs a standard way to ask each one: What is your name? What version are you? What tools and objects do you provide? What instructions should be added to the assistant prompt? This file answers those questions for the connectors extension.

The connectors extension is meant to expose external tools in a generic way. Instead of declaring separate tools for every possible provider, it declares one shared tool surface that can list, describe, search, and run tools registered elsewhere. That keeps this file focused on the common connector layer, while provider-specific details live in broker extensions.

At import time, the file reads a Markdown prompt section from `prompts/connectors_section.md`. That text becomes the `external_tools` prompt section, which is a named block of instructions added to the assistant's context. It also names two object types, connections and connector grants, so the broader system knows these are part of the extension's vocabulary.

The main result is a `Manifest`, which is like a shipping label for the extension: it names the package, lists what is inside, and tells the application how to present it.

#### Function details

##### `manifest`  (lines 21–28)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the official description of the connectors extension. The host application uses this to learn which connector tools, object types, and prompt instructions the extension contributes.

**Data flow**: It starts with constants already defined in the file: the extension name and version, the connector tool list, the connection-related object definitions, and the prompt text read from disk. It wraps the prompt text in a `PromptSection`, then puts everything into a `Manifest` object. The result is a single structured description of the extension that the rest of the system can consume.

**Call relations**: During extension discovery, the host calls this function to ask the extension what it provides. The function creates a prompt section and then hands that, along with the tools and objects, into the manifest constructor so the host can register the extension as one coherent unit.

*Call graph*: 2 external calls (__init__, __init__).


### Knowledge Sync Registration
Manifest that registers gbrain as a Markdown knowledge sync extension backed by GitHub or local folders.

### `extensions/gbrain/ufo_ext_gbrain/manifest.py`

`config` · `startup`

This is the extension’s “front desk” file. When the larger system loads extensions, it needs a simple summary of what each extension provides: what kinds of data it knows about, where that data can come from, and what credentials it may need. This file provides that summary for gbrain.

In plain terms, gbrain turns Markdown files into pages the system can sync and use. Those Markdown files can live in two places: a GitHub repository or a local folder served from disk. The file declares both of those as content-source backends, meaning they are possible ways to fetch source material. For GitHub, it also declares an optional GitHub token credential, so private repositories can be opened while public ones still work without a token.

The main thing exported here is the `manifest()` function. It builds a `Manifest`, which is like a labeled package insert for the extension. Inside it are the extension name and version, the object kind gbrain contributes, the source providers that know how to create GitHub or folder source readers, and the credential slot for GitHub access. Without this file, the host system would not know that gbrain exists or how to ask it to create sources for syncing.

#### Function details

##### `manifest`  (lines 16–38)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension description that the host application reads when loading gbrain. Someone uses this so the system can discover the gbrain object type, its GitHub and local-folder source options, and its optional GitHub credential.

**Data flow**: It starts with the extension’s fixed name and version, plus imported pieces that describe the gbrain object type, the Git backend, the folder backend, and the GitHub token name. It wraps the two backends in `SourceProvider` entries: one creates a `GbrainGitSource` using supplied credentials, and the other creates a `GbrainFolderSource` without needing credentials. It also creates a `CredentialSlot` explaining the GitHub token. The result is a complete `Manifest` object returned to the caller.

**Call relations**: When the extension system asks gbrain what it provides, this function is the answer. While building that answer, it creates `SourceProvider` objects for the GitHub and folder sources, creates a `CredentialSlot` for the GitHub token, and then hands all of that to `Manifest` so the host application can register and later use these capabilities during syncing.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Messaging Integrations
Manifests that register communication tools, credentials, routes, hooks, and deployment requirements for messaging services.

### `extensions/imessage/ufo_ext_imessage/manifest.py`

`config` · `startup / extension discovery`

This file is like the label and plug-in adapter for the iMessage extension. The rest of the project may contain the actual work of connecting phones and sending messages, but the host system needs one clear place to ask, “What does this extension provide, and how do I use it?” That is what this manifest supplies.

It names the extension as `imessage` and gives it a version. Then it creates two main pieces: an `ImessageSurface`, which is the messaging surface used to listen for incoming iMessages and post outgoing ones, and an `ImessageConnect`, which is the tool members use to connect their phone to the workspace.

The returned `Manifest` packages these pieces in a standard shape the UFO platform understands. It registers one tool, `imessage_connect`, with a plain description for the agent or caller explaining the connection flow. It also marks that tool as untrusted and side-effecting, meaning it should be treated carefully because it depends on outside input and can change real-world state. Finally, it registers the iMessage surface and lists the required deployment secrets for the Spectrum project provider. Without this file, the extension’s capabilities would exist in code but would not be discoverable or usable by the main system.

#### Function details

##### `manifest`  (lines 16–48)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the standard manifest object that describes the iMessage extension to the UFO platform. Someone uses this when the platform is loading extensions and needs to know what tools, message surfaces, and deployment secrets this extension requires.

**Data flow**: It starts with shared constants such as the extension name, version, iMessage surface name, and required environment variable names. It creates an iMessage surface and an iMessage connection tool, both using the Spectrum project provider. It then wraps their callable actions into a `Manifest`, so the outside system receives one complete description of how to use this extension.

**Call relations**: During extension loading, the host calls `manifest` to get the extension’s registration details. Inside, it creates `ImessageSurface` for message listening and sending, creates `ImessageConnect` for the phone-connection workflow, wraps the connect action in a `ToolDef`, wraps the messaging callbacks in a `SurfaceSpec`, and finally hands all of that to `Manifest` so the platform can register it.

*Call graph*: 5 external calls (__init__, __init__, __init__, __init__, __init__).


### `extensions/slack/ufo_ext_slack/manifest.py`

`config` · `startup / extension registration`

This file exists so the core application can discover and wire in Slack support without hard-coding Slack details elsewhere. In plain terms, it says: “Here is how Slack talks to us, here is how we talk back, here are the secrets we need, and here are the moments when Slack-specific follow-up work should run.”

The manifest declares two per-workspace secret slots: a Slack bot token and, for people who bring their own Slack app, a signing secret. It also declares one Slack “surface,” meaning one communication channel where users can interact with the agent. That surface has web routes for incoming Slack events, interactive Slack actions, and the OAuth callback used when a workspace installs the app.

It also connects Slack-specific behavior into the wider system. For example, before a connector-send tool runs, a hook can mark that send as coming through Slack. When a user prompt is submitted, another hook starts Slack thread-following work so progress and status updates can continue during that turn. When a connection is recorded, another hook can settle the Slack connect button. Finally, it points to a setup skill that helps someone configure a bring-your-own Slack app. Without this file, the Slack code could exist, but the core system would not know when or how to use it.

#### Function details

##### `manifest`  (lines 55–97)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the Slack extension’s manifest, which is the object the core system reads to understand how to install, contact, and run Slack support. Someone uses this indirectly when the extension is loaded.

**Data flow**: It starts with constants and imported Slack handlers, tools, and hook functions. It packages them into structured manifest pieces: credential slots for Slack secrets, surface routes for Slack web requests, surface callbacks for posting and identifying Slack workspaces, hook rules for important system events, and a setup skill path. The result is one Manifest object that describes the Slack extension to the rest of the application; it does not itself contact Slack or process a message.

**Call relations**: When the Slack extension is registered, this function is the place that assembles the extension contract. Inside it, the function creates CredentialSlot entries for the secrets, SurfaceRoute entries for the Slack HTTP endpoints, a SurfaceSpec for the Slack communication surface, HookSpec entries for system events that need Slack behavior, a SkillSpec for the setup instructions, and finally a Manifest that ties all of those pieces together.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, __init__, __init__).


### Source Provider Registration
Manifest that registers source providers, their credentials, event reactions, and background synchronization jobs.

### `extensions/sources/ufo_ext_sources/manifest.py`

`config` · `startup/config load`

This file does not sync pages itself. Instead, it declares the pieces that make page syncing possible, much like a shop directory tells visitors which counters exist and what each one is for. The extension supports multiple registered connectors, such as external services that can provide content. For each connector, it creates a source backend, which is the part the sync runner can call to fetch data. It also declares credential slots for “bring your own key” access, meaning a user or deploy can provide an API key directly instead of going through another account broker. The file registers a direct authentication proxy, which is the small bridge that reads those stored credentials and gives connectors the access they need. It also registers object kinds for sources, pages, and source triggers, plus hooks that react when pages change or when an external account connection is recorded. Finally, it defines a scheduled retry job for connected sources, so if automatic source creation failed the first time, the system can try again later. Without this manifest, the rest of the platform would not know that this extension exists, which connectors it offers, or which hooks and jobs belong to it.

#### Function details

##### `ConnectorSourceFactory.__call__`  (lines 41–42)

```
def __call__(self, _credentials: CredentialAccess) -> ConnectorBackend
```

**Purpose**: This turns a connector class into a ready-to-use source backend. The system uses it when it needs to build the syncing backend for one registered provider.

**Data flow**: It receives credential access information, although this factory does not read it directly. It creates a fresh connector object from the connector class stored on the factory, wraps that connector in a ConnectorBackend, and returns the backend to the caller.

**Call relations**: The manifest creates one ConnectorSourceFactory for each registered connector and gives it to SourceProvider. Later, when the platform wants a backend for that provider, it calls this factory, which hands back a ConnectorBackend ready for the sync runner to use.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 45–82)

```
def manifest() -> Manifest
```

**Purpose**: This builds and returns the full extension manifest, which is the system-readable description of everything the sources extension contributes. It is the central place where providers, credentials, hooks, jobs, objects, and authentication proxy support are advertised.

**Data flow**: It starts from constants and the connector registry. It turns each registered connector into a SourceProvider, turns each connector name into a CredentialSlot for direct API-key access, adds object definitions for sources and pages, adds event hooks, adds the retry job and its workspace candidates, and adds the direct authentication proxy. The result is one Manifest object that the host application can load.

**Call relations**: This function is called when the extension is being discovered or loaded. It gathers pieces from other source-extension modules, such as page objects, source objects, page-change handling, connection-recorded handling, retry logic, and the direct authentication proxy, then hands the completed Manifest to the platform so those pieces become active.

*Call graph*: 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, connection_workspaces, items).
