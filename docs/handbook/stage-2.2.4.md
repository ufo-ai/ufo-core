# Connector and external service declarations  `stage-2.2.4`

This stage is the signpost layer for external integrations. It is mostly used when the application starts up or loads extensions. Instead of doing the integration work itself, it tells the main UFO system what extra abilities are available and where to find them.

Each extension has a small __init__.py file. These files are like labels on folders: they make the folder importable as a Python package, but they do not run connector behavior. The real declarations live in the manifest.py files. The Composio manifest advertises Composio-backed connectors, account connection settings, and an OAuth login route, which is a web path used to let users safely sign in to another service. The general connectors manifest declares connector tools, related objects, and prompt text to add when enabled. The Pipedream manifest registers Pipedream connectors, its OAuth route, and a broker that runs actions while protecting secrets. The Slack manifest declares Slack routes, required secrets, setup steps, and messaging tools. The sources manifest registers synced content sources, credentials, direct authentication, page objects, and change notifications. Together, these files let the host discover integrations cleanly.

## Files in this stage

### Composio integration
Package and manifest declarations that make Composio-backed connectors and their OAuth route discoverable.

### `extensions/composio/ufo_ext_composio/__init__.py`

`other` · `import time`

This is an empty Python package marker. In Python, a folder often needs an `__init__.py` file so the interpreter treats that folder as an importable package, rather than just a plain directory. Think of it like a label on a box: the label does not contain the tools, but it tells the system that the box belongs on the shelf and can be opened by name. Here, the package is named `ufo_ext_composio`, under the broader `extensions/composio` area. Other files in or outside this extension can import modules from this package because this file exists. Since it is empty, it does not run startup code, expose shortcuts, or configure anything. If this file were missing in environments that still rely on explicit package markers, imports for this extension could fail or behave differently.


### `extensions/composio/ufo_ext_composio/manifest.py`

`config` · `startup`

This file is the extension’s front desk. When the host application loads the Composio extension, it asks for a manifest, which is a structured description of what the extension provides. The file builds that description in one place.

Composio is used here as a server-side tool provider. That means user account tokens stay with Composio instead of being stored in this deployment. The manifest sets up a shared broker, which is the piece that talks to Composio when a granted connector needs to run a tool. It also sets up a request forwarder for command-line credentials, used only for connectors that need a real provider host during CLI authentication.

For the small explicit connector list, the file creates a connector provider for each one. Each provider includes the user-facing label, the OAuth connector setup, the shared broker, allowed transfer hosts, and optional command-line credential forwarding. For every other Composio toolkit, the manifest also registers a resolver, which can expose connectors by their Composio slug instead of requiring each one to be listed manually.

Finally, it declares one GET route for the browser-based OAuth consent step. This route is how the connection flow returns through the app after the user approves access.

#### Function details

##### `manifest`  (lines 24–53)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the Composio extension manifest, which is the package of connector definitions, connector lookup behavior, and OAuth route information the host app needs. Someone would use this when loading the extension so the rest of the system can see what Composio supports.

**Data flow**: It starts with no caller-provided input and reads the file’s constants plus imported connector specifications. It creates one shared Composio broker, one request forwarder, a connector provider for each explicitly listed connector, a resolver for slug-based Composio connectors, and a route for the OAuth browser callback. The result is a Manifest object that the host can register; it does not directly connect any user account yet.

**Call relations**: When the extension is loaded, this function is the place that assembles the parts. It calls the broker, OAuth provider, CLI credential, connector provider, resolver, route, and manifest constructors so each piece is ready for the host app. The connector providers and resolver hand future connection and tool-running work to the shared broker, while the route points browser OAuth traffic to the OAuth route handler.

*Call graph*: 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, items).


### Core connectors
Package and manifest declarations for the shared connectors extension, including connector tools, objects, and prompt additions.

### `extensions/connectors/ufo_ext_connectors/__init__.py`

`other` · `import/package discovery`

This is an empty Python package file. In Python projects, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. Think of it like putting a label on a drawer: the drawer may contain useful tools, but this label is what lets the rest of the system find and open it by name.

Because the file has no code inside it, it does not create objects, run setup steps, or change program state. Its value is structural. Without it, depending on the Python version and how the project is packaged, imports such as `ufo_ext_connectors.some_module` might fail or behave differently. Keeping this file present makes the package boundary explicit and helps packaging tools include this connector extension area correctly.


### `extensions/connectors/ufo_ext_connectors/manifest.py`

`config` · `startup / extension loading`

This file is the “front desk sign” for the connectors extension. When the host system loads extensions, it needs a simple way to ask: What is this extension called? What version is it? What tools and data types does it add? What instructions should the assistant see? This file answers those questions in one place.

The connector tools are intentionally declared here in a general way. They are not tied to one provider, such as a specific OAuth service or broker. Instead, they work across all connector providers that other broker extensions register. In plain terms, this file gives the assistant one shared set of actions for listing, describing, searching, and running external connector tools, no matter where those connectors came from.

It also reads a Markdown prompt section from disk. That text becomes an instruction block named `external_tools`, which helps teach the assistant how to think about and use these connector tools. Without this file, the extension might still have tool code elsewhere, but the main system would not know to expose it, include its connection objects, or add its prompt guidance during a run.

#### Function details

##### `manifest`  (lines 21–28)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension’s manifest, which is the package of information the host system uses to register this extension. Someone would use it when loading the connectors extension so the system can discover its tools, objects, name, version, and prompt instructions.

**Data flow**: It starts with constants already prepared in the file: the extension name and version, the connector tool list, the connection-related object definitions, and the prompt text read from a Markdown file. It wraps the prompt text in a `PromptSection`, then places everything into a `Manifest`. The result is a complete manifest object that tells the host system what this extension contributes.

**Call relations**: During extension loading, the host system calls `manifest` to ask this file what should be registered. Inside, it creates a `PromptSection` for the connector guidance and then creates a `Manifest` containing that section plus the connector tools and objects. The returned manifest is handed back to the extension system so these pieces can become available in the wider run.

*Call graph*: 2 external calls (__init__, __init__).


### Pipedream integration
Package and manifest declarations for Pipedream-backed connectors, OAuth login, and brokered connector execution.

### `extensions/pipedream/ufo_ext_pipedream/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder often needs an `__init__.py` file so Python treats that folder as an importable package, much like putting a label on a drawer so the rest of the program knows it can look inside. Here, the package is for the Pipedream extension area of the project. Because the file contains no code, it does not start anything, configure anything, or expose any functions directly. Its value is structural: without it, some Python environments or tools might not recognize `ufo_ext_pipedream` as a proper package, which could make imports fail or make packaging and discovery less reliable.


### `extensions/pipedream/ufo_ext_pipedream/manifest.py`

`config` · `startup / extension load`

This file is the extension’s “front desk sign.” When the project loads extensions, it needs a clear declaration of what this Pipedream extension provides: which outside apps can be connected, how users start the permission flow, and what component should later perform work through those connections.

The file builds one shared PipedreamBroker, which is the part that talks to Pipedream when the system needs to run an action or use a saved credential. It then loops through the known Pipedream connector catalog, called CONNECTORS, and turns each catalog entry into a ConnectorProvider. Each provider combines a user-facing label, an OAuth provider description, allowed transfer hosts, and the shared broker.

OAuth means “a permission handoff,” where a user grants access to an outside service without giving this app their password. Here, Pipedream keeps the actual access tokens on its own servers, so this deployment does not receive or store those secrets.

The manifest also declares a browser route for the consent redirect step. After a user approves access, their browser comes back through this route so the system can finish linking the account to the right workspace. Without this file, the rest of the system would not know these Pipedream connectors exist or how to start and finish their connection flow.

#### Function details

##### `manifest`  (lines 23–45)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the formal extension description for Pipedream. The larger system uses this description to discover available connectors and the route needed to complete OAuth connection setup.

**Data flow**: It starts with the fixed extension name and version, reads the CONNECTORS catalog, and creates one shared PipedreamBroker. For every connector in the catalog, it makes a ConnectorProvider with a PipedreamOAuthProvider, a label, the shared broker, and the allowed Pipedream transfer hosts. It also creates a GET route for the OAuth bridge. The result is a Manifest object that the host application can plug into its connector registry and web routing.

**Call relations**: When the extension is loaded, the system calls this function to ask, “What do you provide?” Inside, it creates the PipedreamBroker, walks through CONNECTORS.items to build each connector entry, creates PipedreamOAuthProvider objects for the permission flow, wraps them in ConnectorProvider objects, and adds a RouteSpec pointing to oauth_route. The returned Manifest is then what lets other parts of the application offer these connectors and route users through the browser-based connection step.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, __init__, items).


### Slack messaging
Package and manifest declarations that register Slack routes, required secrets, setup support, and messaging tools.

### `extensions/slack/ufo_ext_slack/__init__.py`

`other` · `import time`

Python uses special files named `__init__.py` to recognize a folder as an importable package. This one belongs to the Slack extension package. Even though it is empty, it still matters: without it, some Python setups or tools might not treat `ufo_ext_slack` as a proper package, which could make imports fail or make packaging less predictable. Think of it like a label on a box. The label does not contain the tools, but it tells Python, packaging tools, and developers that the box is meant to be opened as one named unit. Any actual Slack behavior, such as connecting to Slack or processing Slack messages, lives in other files under this package, not here.


### `extensions/slack/ufo_ext_slack/manifest.py`

`config` · `startup / extension registration`

This is the Slack extension’s “front desk” declaration. It does not do the Slack work itself. Instead, it describes the parts that the main UFO system should connect: secret fields, web endpoints, message-sending hooks, identity lookup, tools, and setup instructions.

The file names two credential slots. A credential slot is a private place where a workspace can store a secret. For Slack, those are the bot token, which lets the system act as the Slack bot, and a signing secret for people who bring their own Slack app. The comments explain an important split: OAuth installs use deployment-level Slack app secrets from environment variables, while bring-your-own installs ask each workspace to provide its own bot token and signing secret.

It also defines one Slack “surface.” A surface is a way the outside world talks to the agent, like a doorway into the system. This surface has routes for normal Slack events, interactive Slack actions such as button clicks, and the OAuth callback used during installation. It also points to helper functions that post messages back to Slack, attach files or context, identify the workspace, and find the bot’s own Slack user ID.

Finally, it registers Slack tools and a setup skill stored in the extension’s skills folder. In everyday terms, this file is the label on the box that tells the host application what Slack parts are inside and how to wire them up.

#### Function details

##### `manifest`  (lines 37–69)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the Slack extension manifest, which is the object the main system uses to discover what this extension offers. Someone would use it when loading the extension so the core application can register Slack routes, secrets, tools, and setup skills.

**Data flow**: It starts with constants and imported Slack functions: names for credential slots, route paths, handlers for incoming Slack traffic, message posting helpers, tool definitions, and the local setup-skill folder path. It packages those into credential-slot descriptions, route descriptions, a Slack surface description, and a skill description. The result is one Manifest object that says, in one place, how Slack should be connected to the rest of the system.

**Call relations**: When the extension is being registered, this function creates the pieces the core expects: credential slots for Slack secrets, surface routes for Slack HTTP callbacks, a surface specification for Slack behavior, and a skill specification for setup guidance. It hands those pieces into the Manifest constructor so the host system receives one complete declaration instead of many scattered settings.

*Call graph*: 5 external calls (__init__, __init__, __init__, __init__, __init__).


### Synced sources
Package and manifest declarations for source connectors, credentials, direct authentication, synced objects, and change hooks.

### `extensions/sources/ufo_ext_sources/__init__.py`

`other` · `import/package discovery`

Python uses `__init__.py` files as a signpost: this folder is meant to be treated as an importable package, not just a plain directory. In this case, the file is empty, so it does not define any functions, classes, settings, or startup behavior. Its value is structural. Without it, depending on the Python version and import style, other parts of the system might not be able to reliably import modules from `extensions/sources/ufo_ext_sources`. Think of it like a labeled folder in a filing cabinet: the label does not do the work, but it tells everyone where that group of files belongs and how to refer to it.


### `extensions/sources/ufo_ext_sources/manifest.py`

`config` · `startup / extension load`

This file is the front desk for the sources extension. When the larger system loads extensions, it asks this file for a manifest: a clear list of what this extension adds and how those pieces should be built. Without it, the system would not know which content-source backends exist, where to look for bring-your-own-key API credentials, how to create the direct authentication proxy, or which objects and hooks belong to synced source content.

The file starts with simple names: the extension is called “sources”, its version is “0.1.0”, and its built-in authentication backend is called “direct”. It then defines `ConnectorSourceFactory`, a small wrapper that knows how to turn a connector class into a ready-to-use `ConnectorBackend`. A connector is the provider-specific piece that knows how to talk to an outside service; the backend is the standard shape the sync system expects.

The `manifest` function gathers everything into one package. It includes two object kinds: one for sources and one for pages. It registers a `page_change` hook so subscribers can be notified when synced content changes. It creates one source provider per registered connector. It also creates one credential slot per connector, so direct API keys have named places to live. Finally, it adds the `direct` auth proxy, which reads those credentials and lets source syncing work even without an external broker.

#### Function details

##### `ConnectorSourceFactory.__call__`  (lines 34–35)

```
def __call__(self, _credentials: CredentialAccess) -> ConnectorBackend
```

**Purpose**: This turns a stored connector class into a live source backend. The credentials argument is accepted because factories in this system receive credential access, but this factory only needs the connector class it already carries.

**Data flow**: It starts with a `ConnectorSourceFactory` that contains a connector class. When called, it creates a new connector object from that class, wraps it in a `ConnectorBackend`, and returns that backend to the caller. It does not change stored state.

**Call relations**: The manifest gives this factory to each source provider it registers. Later, when the sync system needs a backend for a particular connector name, it calls the factory; the factory then hands back a standard `ConnectorBackend` built around that provider’s connector.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 38–64)

```
def manifest() -> Manifest
```

**Purpose**: This builds the full declaration for the sources extension. The host application uses it to learn what this extension offers and how to construct its source backends, credential slots, authentication proxy, objects, and hook.

**Data flow**: It reads the registered connectors from `CONNECTORS`. For each connector name and class, it creates a source-provider entry and a matching credential slot. It also includes the source and page object definitions, the page-change hook, and the direct authentication proxy builder. The result is a single `Manifest` object that the host can load.

**Call relations**: This is the main function the extension loader relies on at startup. It pulls connector registrations from the registry, wraps connector classes with `ConnectorSourceFactory`, describes credential slots for direct API keys, attaches the page-change notification hook, and supplies a builder for `DirectAuthProxy` so the sync system can authenticate direct-account sources.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, items).
