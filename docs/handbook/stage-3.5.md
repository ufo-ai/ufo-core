# Connector, source, and communication extension manifests  `stage-3.5`

This stage is shared start-up support for plugging outside services into the main system. Most files here are manifests, which are like sign-in sheets for extensions: they tell the host application what exists, what it is called, what tools or web routes it provides, and what secrets or credentials it needs.

The Composio and Pipedream manifests register connector providers and their OAuth sign-in routes, so users can authorize outside services. The connectors manifest declares the general connector tools, shared objects, and prompt text that make those connections usable. The gbrain manifest registers two source options for reading gbrain content, either from GitHub or a local folder. The sources manifest registers source backends, credential slots, hooks, object types, and a retry job so external content can be synced into the system. The iMessage and Slack manifests register communication surfaces: tools, routes, deployment secrets, hooks, and status checks for messaging integrations. The __init__.py files for Slack, sources, and source providers are simple package markers, letting Python import those folders as usable modules.

## Files in this stage

### Connector extension front doors
These manifests make connector-oriented extensions discoverable and declare their tool, object, prompt, and OAuth entry points.

### `extensions/composio/ufo_ext_composio/manifest.py`

`config` · `startup`

This file is the extension’s “front desk.” It does not run Composio tools itself. Instead, it declares the pieces the host system needs in order to offer Composio as a connector.

Composio is treated as a brokered connector: the actual account tokens stay on Composio’s side, so this deployment does not receive or store user secrets. The file creates a `ComposioBroker`, which is the object the rest of the connector system can talk to when it needs Composio-backed access. It then creates a `ComposioResolver`, which lets the system find Composio toolkits by their simple slug, or short name.

It also declares one web route for the OAuth flow. OAuth is the common “sign in and grant permission” process used by many services. Here, the route is a GET endpoint used during the consent redirect step, when a browser comes back after the user has approved access. The route is tied to workspace identification through `connect_bridge_workspace`, so the system can connect the returning browser request to the right workspace.

The result is a `Manifest`: a compact registration card containing the extension name, version, connector resolver, and route. The host reads this card to plug Composio into the wider connector framework.

#### Function details

##### `manifest`  (lines 18–32)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension manifest, which is the host system’s registration card for this Composio extension. It tells the host the extension’s name and version, how to resolve Composio connectors, and which OAuth browser route to install.

**Data flow**: It starts with no outside input. It creates a `ComposioBroker`, passes that broker into a `ComposioResolver`, wraps the OAuth callback route in a `RouteSpec`, and returns a `Manifest` containing all of that information. The main output is the finished manifest object; nothing else is changed directly in this file.

**Call relations**: The host application calls this function when loading the extension. Inside that setup moment, it creates the broker, hands it to the resolver so connector lookup can work, creates the route description for OAuth redirects, and then hands the complete manifest back to the host so the host can register the connector and route.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).


### `extensions/connectors/ufo_ext_connectors/manifest.py`

`config` · `startup`

This file defines the public manifest for the connectors extension. A manifest is like a label on a toolbox: it tells the main application what the toolbox is called, which tools are inside, and what extra instructions should be added when the system talks to an AI model.

The connectors extension provides a shared tool surface for external connectors. These tools are not tied to one specific provider. Instead, they work across all connector providers that other broker extensions register. That means this file declares the generic connector tools once, rather than repeating them for every service or broker.

The file also loads a prompt section from a Markdown file named `connectors_section.md`. That text becomes a named prompt section called `external_tools`, which helps explain to the AI how to think about or use these connector tools. Alongside the tools, the manifest also declares two shared object types: one representing a connection, and one representing a connector grant, which is permission or authorization for connector access.

Without this file, the host system would not know that the connectors extension exists, which tools it offers, what objects it introduces, or what prompt guidance should be included.

#### Function details

##### `manifest`  (lines 21–28)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension manifest for the connectors feature. The host application uses this to discover the extension’s name, version, tools, object types, and prompt text.

**Data flow**: It reads constants already prepared in the file: the extension name and version, the connector tool list, the connection-related object definitions, and the prompt section text loaded from disk. It packages those pieces into a `Manifest` object, with a `PromptSection` object holding the prompt name and body. The result is a single manifest value that the host can register.

**Call relations**: When the extension is being discovered or loaded, the host calls `manifest` to ask, “What do you provide?” This function then creates a `PromptSection` for the connector prompt guidance and passes it, along with the tools and objects, into `Manifest.__init__` so the rest of the system can use the extension consistently.

*Call graph*: 2 external calls (__init__, __init__).


### Repository-backed content source
This manifest registers gbrain content readers for GitHub repositories and local folders, including optional private-repository credentials.

### `extensions/gbrain/ufo_ext_gbrain/manifest.py`

`config` · `startup`

A manifest is like a shop sign and catalog for an extension: it tells the main system what is available before the system tries to use it. This file declares the gbrain extension under the name "gbrain" and version "0.1.0". It says that gbrain provides a kind of object called a gbrain source, which represents one origin that can be synced. It also registers two content source backends. The Git backend reads Markdown pages from a GitHub repository, while the folder backend reads Markdown pages from a local directory, useful for serving local content during development or offline use. The file also declares a credential slot named for a GitHub token. A credential slot is a named place where the system can store a secret, such as an access token. Public repositories do not need it, but private ones do. Without this file, the host application would not know that the gbrain extension exists, which object type it contributes, how to build its source readers, or what credential it may need.

#### Function details

##### `manifest`  (lines 16–38)

```
def manifest() -> Manifest
```

**Purpose**: This function builds and returns the extension description that the host application reads. Someone would use it when loading the gbrain extension so the system can discover its source backends, object type, and credential needs.

**Data flow**: It starts from fixed values in this file, such as the extension name and version, plus imported pieces that define the Git backend, folder backend, credential name, and gbrain object kind. It packages those into a Manifest object. The result is a complete description saying: here is the gbrain extension, here are the source readers it can build, and here is the optional GitHub token it understands.

**Call relations**: When the extension system asks for this file's manifest, this function constructs the pieces the host needs. It creates SourceProvider entries that know how to build a Git-backed gbrain source or a folder-backed gbrain source, creates a CredentialSlot describing the GitHub token, and hands all of that to the Manifest constructor so the host can register the extension.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Messaging and OAuth service surfaces
These files register communication tools, message surfaces, deployment secrets, Slack package importability, and a Pipedream OAuth-backed connector surface.

### `extensions/imessage/ufo_ext_imessage/manifest.py`

`config` · `startup / extension loading`

This file is the front door for the iMessage extension. When the larger UFO system loads extensions, it needs a clear description of what each extension offers and how to call into it. This file provides that description as a Manifest, which is like a registration form for the extension.

The manifest says: this extension is called “imessage”, it has one user-facing action for connecting a member’s iMessage phone, and it provides one communication surface where messages can be listened for, posted, attached to, and spoken through. A “surface” here means a communication channel the system can use, similar to how email, Slack, or SMS might each be different surfaces.

The file also wires the extension to a cloud-backed provider called line_provider. That provider is passed into both the surface and the connect tool, so they use the same underlying service for phone-line behavior. The connect tool is marked as untrusted and side-effecting, meaning the system should treat its input carefully and understand that running it can change outside state, such as starting or completing a phone connection flow.

Finally, it lists two required deployment keys. Without this manifest, the UFO system would not know that the iMessage extension exists, what action it exposes, what surface it provides, or which secrets are needed to run it.

#### Function details

##### `manifest`  (lines 21–55)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the complete registration record for the iMessage extension. The larger system uses this record to discover the extension’s tool, communication surface, and required cloud credentials.

**Data flow**: It starts with no outside arguments. It creates an ImessageSurface and an ImessageConnect tool, both using the shared line_provider. It then packages those pieces into a Manifest: the extension name and version, a ToolDef describing the “Connect iMessage” action, a SurfaceSpec describing how to use the iMessage surface, and the environment variable names needed for deployment. The result is a Manifest object that the host system can read.

**Call relations**: During extension loading, the host calls manifest to ask, “What do you provide?” Inside that answer, this function constructs the ImessageSurface and ImessageConnect objects, then hands their methods to SurfaceSpec and ToolDef so the host can later call them when messages arrive or when a user asks to connect iMessage. It also creates small presentation and binding objects so the tool appears correctly to users and is tied to the iMessage surface.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__).


### `extensions/pipedream/ufo_ext_pipedream/manifest.py`

`config` · `startup / extension registration`

This file is the extension’s front desk. When the larger application loads extensions, it asks this file for a Manifest, which is a compact description of what the extension adds to the system. Here, the extension adds a set of Pipedream-backed connectors, such as GitHub, Gmail, Linear, and others listed in the shared CONNECTORS catalog.

For each catalog entry, the file builds a ConnectorProvider. In plain terms, each provider says: “Here is the name users see, here is how OAuth sign-in works, here is the broker that can run actions for this service, here are safe transfer hosts, and here is any command-line credential support.” OAuth is the standard web sign-in process where a user grants access without handing the app their password.

The file also declares one browser route for the OAuth bridge. That route is the doorway used when a user is redirected back during the consent process. It identifies the correct workspace before handing the request to the Pipedream OAuth route handler.

A notable design choice is that one shared PipedreamBroker instance is created and reused across all declared connectors. Like one service counter serving many product lines, the broker is the common piece that knows how to talk to Pipedream for actions and credentials.

#### Function details

##### `manifest`  (lines 28–51)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension manifest that the main system uses to register Pipedream connectors and their OAuth callback route. Someone uses this function when the application is discovering what this extension contributes.

**Data flow**: It starts with the connector catalog in CONNECTORS and creates one shared PipedreamBroker. For each connector specification, it combines the provider name, host, app, display label, allowed transfer hosts, OAuth provider, broker, and optional command-line credential into a ConnectorProvider. It then adds a GET route for the OAuth bridge and returns a Manifest containing the extension name, version, connectors, and route.

**Call relations**: During extension loading, the wider system calls this function to learn what to register. Inside, it creates the PipedreamBroker, asks CONNECTORS for all catalog entries, creates a PipedreamOAuthProvider and ConnectorProvider for each one, calls cli_credential to attach command-line credential behavior where needed, and creates a RouteSpec that points OAuth callback traffic to oauth_route after the workspace is identified by connect_bridge_workspace.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, items, cli_credential).


### `extensions/slack/ufo_ext_slack/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. That matters because other parts of the project can then refer to the Slack extension using normal Python import paths, rather than treating the folder as just a loose collection of files. Think of it like putting a label on a drawer: the drawer may contain the useful tools elsewhere, but the label is what lets the system find and open it correctly. Since this file has no functions, classes, or setup code, it does not perform any work when imported beyond helping Python recognize the package structure.


### `extensions/slack/ufo_ext_slack/manifest.py`

`config` · `startup and extension registration`

Think of this file as the Slack extension’s registration form. It does not process Slack messages itself. Instead, it describes all the connection points the main UFO system needs in order to use Slack safely and correctly.

It declares two private workspace-level secret slots: the Slack bot token and, for bring-your-own Slack apps, the signing secret Slack uses to prove requests are real. It also declares the Slack “surface,” meaning the place where users can talk to the system. That surface has routes for incoming Slack events, interactive Slack actions like button clicks, and the OAuth callback used when someone installs Slack through the normal app-install flow.

The file also wires in Slack-specific behavior around a turn of conversation. For example, when a user prompt is submitted, Slack follow-up tasks are armed so progress updates and status messages can keep working even after a resumed run. It registers tools and setup skills so the agent can help a workspace configure Slack.

One important detail is that being partly installed is not enough. The workspace fact only says “Slack is installed and answers here” when there is both an installation record and the deployment can actually receive Slack traffic. This prevents the system from claiming Slack works while setup is still unfinished.

#### Function details

##### `_slack_answers`  (lines 73–79)

```
async def _slack_answers(ext: ExtensionContext) -> bool
```

**Purpose**: Checks whether Slack is genuinely usable for the current workspace, not merely halfway installed. It is used to decide whether the workspace should be told that UFO is installed and answering in Slack.

**Data flow**: It receives an extension context, which is the object the extension uses to read workspace-specific state. First it looks for a Slack installation record. If none exists, it returns false. If a record does exist, it asks the Slack surface code whether the installation is live, meaning Slack requests can actually reach this deployment, and returns that answer.

**Call relations**: This function is attached to the Slack workspace fact in `manifest`. When the core system wants to know whether to show the Slack-installed fact for a workspace, it calls this checker. The checker delegates the final real-world connectivity test to `ufo_ext_slack.surface.install_is_live(ext)` because the surface layer knows how to verify that Slack is actually reaching the deployment.

*Call graph*: 1 external calls (install_is_live).


##### `manifest`  (lines 82–127)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the complete description of the Slack extension for the core system. The core uses this description to know what secrets Slack needs, what web endpoints to expose, what tools and hooks to register, and how Slack should send and receive messages.

**Data flow**: It takes no input and builds a `Manifest` object from constants and imported Slack handlers. It fills that object with credential definitions, route definitions, surface behavior, tools, event hooks, setup skills, and a workspace fact. The returned manifest is the single package of instructions the host application uses to activate the Slack extension.

**Call relations**: This is the main registration function for the file. During extension loading, the core calls it to obtain the Slack extension’s contract. Inside, it creates credential slots, surface routes, hook specifications, a skill specification, and a workspace fact, then hands all of them to `Manifest` so the rest of the system can wire Slack into request handling, setup, tool use, and workspace status display.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__).


### Service source synchronization package
These package markers and manifest expose source backends, provider imports, credential slots, hooks, object types, and retry sync jobs.

### `extensions/sources/ufo_ext_sources/__init__.py`

`other` · `import/package discovery`

This is an empty package initializer. In Python, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may contain useful tools in other files, but this label lets the system find and open it correctly.

Because the file is empty, it does not run setup code, define settings, or expose helper functions. Its value is structural. Without it, some Python import styles or packaging tools might not recognize `extensions/sources/ufo_ext_sources` as a normal package, which could make the extension source modules harder or impossible to load in certain environments.


### `extensions/sources/ufo_ext_sources/manifest.py`

`config` · `startup and extension registration; scheduled retry during normal operation`

This file is the extension’s front desk. When the application starts, it asks the extension for a manifest, which is a structured declaration of everything the extension brings with it. Without this file, the host would not know that this extension can sync content from registered connectors, read direct API keys, react when pages change, or create sources automatically after a user connects an account.

The manifest names the extension, lists the object kinds it adds, and wires events to small pieces of code called hooks. A hook is a function the system runs when something specific happens, like a page changing or a connection being recorded. It also declares a scheduled job that periodically retries source creation for connections that did not finish cleanly the first time.

The file also turns each registered connector into a source provider. A connector is the provider-specific code that knows how to talk to a service, while a source provider is how the larger sync system sees it. The small ConnectorSourceFactory class is the adapter between those two worlds. Finally, the file declares credential slots for “bring your own key” API keys and registers a direct authentication proxy, which lets deployments use stored keys directly when no external broker is involved.

#### Function details

##### `ConnectorSourceFactory.__call__`  (lines 41–42)

```
def __call__(self, _credentials: CredentialAccess) -> ConnectorBackend
```

**Purpose**: This turns a connector class into a ready-to-use source backend. The system uses it when it needs a backend object that can sync content for one registered provider.

**Data flow**: It receives a credential access object, though this factory does not read it directly. It creates a fresh connector instance from the stored connector class, wraps that connector in a ConnectorBackend, and returns the backend to the sync system.

**Call relations**: The manifest creates one ConnectorSourceFactory for each registered connector and gives it to a SourceProvider. Later, when the sync system needs that provider’s backend, it calls this factory, which hands back a ConnectorBackend built around the provider-specific connector.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 45–82)

```
def manifest() -> Manifest
```

**Purpose**: This builds the full declaration for the sources extension. The host application calls it to discover what this extension adds and how to wire those pieces into the rest of the system.

**Data flow**: It starts from constants in this file and the connector registry. It builds object declarations, event hooks, a scheduled retry job, one source provider per connector, one credential slot per connector, and a direct authentication proxy. It packages all of that into a Manifest object and returns it to the host.

**Call relations**: At extension load time, the host calls manifest to learn what to install. Inside it, hooks are tied to page-change and connection-recorded events, the retry job is given candidate workspaces through connection_workspaces, registered connectors are wrapped with ConnectorSourceFactory, and DirectAuthProxy is registered as the built-in direct authentication option.

*Call graph*: 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, connection_workspaces, items).


### `extensions/sources/ufo_ext_sources/providers/__init__.py`

`other` · `import time`

This is an empty Python package marker file. In Python projects, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. Here, it makes `extensions/sources/ufo_ext_sources/providers` available as a package, so code elsewhere can refer to provider-related modules using normal Python import paths. Think of it like a label on a drawer: the drawer may contain useful tools, but this label simply makes the drawer easy to find and open. Because the file is empty, it does not define any settings, functions, classes, or side effects. Nothing would run from this file during normal execution except Python recognizing the package during imports.
