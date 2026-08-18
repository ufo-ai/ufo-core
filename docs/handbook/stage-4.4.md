# Connector, source, and integration provider manifests  `stage-4.4`

This stage is shared setup support. It does not do the user’s main task itself. Instead, it tells the host runtime what outside services are available and how to plug them in, like labels and sockets on a power strip.

The Composio manifest advertises Composio connectors, account connection steps, command-line credential forwarding, and the web address used when OAuth sign-in returns from the browser. The general connectors manifest names the connectors extension, its version, its tools, shared objects, and the guidance text shown to the assistant. The keyed connectors file covers simpler services that use API keys, such as Datadog, by defining where keys are stored, how they are safely added to requests, and what users should do.

The Pipedream manifest declares Pipedream-backed connectors and their OAuth start and return routes. The Slack manifest gathers Slack routes, credentials, tools, hooks, and assistant instructions. The sources manifest advertises source backends, credential slots, hooks, objects, an authentication proxy, and a background job. Finally, the sources registry maps names like Slack, Gmail, or Stripe to the actual connector code the system can run.

## Files in this stage

### Connector provider manifests
These manifests advertise general connector providers, brokered OAuth connectors, and API-key-based connector credentials to the host runtime.

### `extensions/composio/ufo_ext_composio/manifest.py`

`config` · `startup / extension load`

This file is the extension’s front desk. When the larger system loads the Composio extension, it asks this file for a manifest: a structured description of the extension’s name, version, connectors, and routes. Without it, the host would not know that Composio exists, which tools it can expose, or how to start the account-connection flow.

Composio is used here as a brokered connector service. In plain terms, that means Composio keeps the user’s real service tokens on its own side, and this deployment talks through Composio instead of storing those secrets locally. The file creates one shared ComposioBroker, which acts like the common service counter for connector grants, and one ComposioRequestForwarder, which can pass approved command-line credential traffic along when a connector needs it.

For each explicitly listed connector, such as special cases that need a real provider host, the manifest builds a ConnectorProvider. Each provider includes an OAuth provider, which describes how browser-based permission approval works; a user-facing label; the shared broker; allowed transfer hosts; and, when needed, a CliCredential that says which environment variable supplies command-line authentication and which HTTP header carries it.

The file also registers a general ComposioResolver. That resolver lets the system find other Composio toolkits by their slug, rather than requiring every one to be listed here. Finally, it adds a GET route for the OAuth bridge, so the browser consent step can return to the right workspace.

#### Function details

##### `manifest`  (lines 24–53)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the Manifest object that describes the Composio extension to the host application. Someone uses this when the system is loading extensions and needs to know what connectors, routes, and connection behavior this extension provides.

**Data flow**: It starts from constants and connector definitions already imported into the file: the extension name and version, the known connector list, transfer hosts, OAuth route path, and OAuth route handler. It creates a shared ComposioBroker, creates a ComposioRequestForwarder, then loops over the connector definitions to turn each one into a ConnectorProvider. If a connector has a command-line credential environment variable, it also creates a CliCredential that reads from that variable and forwards it using the authorization header. It then packages all of this into a Manifest and returns it to the caller.

**Call relations**: This is the file’s single assembly point. During extension loading, the host calls this function to get the extension description. Inside, it calls constructors for ComposioBroker, ComposioRequestForwarder, ComposioOAuthProvider, ConnectorProvider, CliCredential, ComposioResolver, RouteSpec, and Manifest, because its job is to wire those pieces together rather than perform the OAuth flow itself. The returned Manifest is what later lets the host route connection requests, resolve Composio toolkits, and serve the OAuth bridge route.

*Call graph*: 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, items).


### `extensions/connectors/ufo_ext_connectors/manifest.py`

`config` · `startup / extension discovery`

This is the “front desk card” for the connectors extension. When the larger system loads extensions, it needs a simple way to ask: What are you called? What tools do you provide? What extra information should be added to the assistant’s instructions? This file answers those questions.

The connector tools are meant to work across all registered connectors, not just one provider. For example, they can list, describe, search, and run tools from whatever connector brokers have been plugged into the workspace. That is why this manifest declares one shared connector tool surface instead of separate tools for each provider.

The file also registers two object types, for connections and connector grants. These are the shared shapes of data the rest of the system can recognize when talking about connector access. It reads a Markdown prompt section from disk, trims extra whitespace, and packages that text as a named prompt section called "external_tools". That prompt section is how the extension teaches the assistant about these external tools.

The main function, `manifest`, builds and returns a `Manifest` object. That object is the bundle the host application uses during extension discovery and setup.

#### Function details

##### `manifest`  (lines 21–28)

```
def manifest() -> Manifest
```

**Purpose**: Builds the extension’s manifest, which is the standard description of what this extension contributes to the host system. Someone uses it when the application is discovering extensions and needs to register the connector tools, object types, and prompt text.

**Data flow**: It starts with constants already defined in the file: the extension name and version, the connector tool list, the connection-related object definitions, and the prompt text read from the Markdown file. It wraps the prompt text in a `PromptSection`, then puts everything into a `Manifest`. The result is a complete manifest object that the host can read to enable this extension’s features.

**Call relations**: When the extension system asks this module for its declaration, `manifest` creates a `PromptSection` for the connector instructions and then creates the `Manifest` that contains it. It hands that finished manifest back to the caller so the wider system can register the tools, objects, and prompt section.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/keyed_connectors/ufo_ext_keyed_connectors.py`

`config` · `startup / manifest load`

Some services cannot be connected through an account broker because the user already owns an API key and the broker does not create it. This file covers that case. It describes each supported “keyed provider” as a row: what the provider is called, which secret keys it needs, which HTTP headers those keys belong in, and which API host is allowed.

The important safety idea is that the sandbox does not receive the real secret. Instead, it sees an environment variable containing a sentinel, which is a harmless placeholder. When the agent makes an outgoing web request, the egress proxy replaces that placeholder with the real stored key, but only for the declared host and header. This is like giving someone a hotel key card that only works at one door, instead of handing them the master key.

The file currently declares Datadog, including its two keys and its possible regional API hosts. For providers with multiple fixed hosts, the user must choose from the published list, so the system never sends a key to a hostname typed freely by a user. Finally, the `manifest` function packages these declarations into a `Manifest`, which is how the rest of UFO discovers the credential slots and the help text shown to agents.

#### Function details

##### `KeyedProvider.__post_init__`  (lines 69–78)

```
def __post_init__(self) -> None
```

**Purpose**: This checks that a provider row is valid as soon as it is created. It prevents unclear or unsafe declarations, such as saying both “use this one host” and “let the user choose a host,” or declaring selectable hosts without explaining how the choice is stored.

**Data flow**: A new `KeyedProvider` object has just been filled with its provider name, keys, and host information. This function reads those fields, checks the rules, and either lets the object exist unchanged or stops startup by raising an error with a clear message.

**Call relations**: It runs automatically after a `KeyedProvider` is constructed. In this file, that happens when the `KEYED_PROVIDERS` table is built, so bad provider declarations fail early before `manifest` can publish unsafe credential rules.


##### `KeyedProvider.target_host`  (lines 81–90)

```
def target_host(self) -> str | HostChoice
```

**Purpose**: This decides what host a provider’s secrets are allowed to be sent to. For a simple provider it returns one fixed hostname; for a provider with regional sites, it returns a controlled host choice that the user can select from.

**Data flow**: It reads the provider’s `host`, `sites`, `host_env`, and site description. If there are no selectable sites, it outputs the fixed host string. If there are selectable sites, it builds and returns a `HostChoice`, which records the credential slot for the choice, the allowed hostnames, the default host, and the environment variable used inside the sandbox.

**Call relations**: Both `KeyedProvider.slots` and `KeyedProvider.usage` ask this property where the provider should point. When selectable sites are used, it hands off to `HostChoice.__init__` to create the structured host-choice object used by the manifest system.

*Call graph*: 1 external calls (__init__).


##### `KeyedProvider.slots`  (lines 92–110)

```
def slots(self) -> tuple[CredentialSlot, ...]
```

**Purpose**: This turns one provider declaration into the credential slots the workspace must fill. Each slot says what secret is needed and exactly where the proxy may inject it on outgoing requests.

**Data flow**: It starts with a `KeyedProvider` and reads its secrets plus its target host. For every secret, it creates a `CredentialSlot` with an `InjectionTarget` that names the allowed host, HTTP header, sentinel placeholder, sandbox environment variable, and request dimension. If the provider also needs a host choice, it adds one extra credential slot for that choice. The result is a tuple of slots ready to go into the manifest.

**Call relations**: `manifest` calls this for every provider in `KEYED_PROVIDERS` when building the extension manifest. Inside, it calls `InjectionTarget.__init__` to describe the safe wire-level replacement rule, and `CredentialSlot.__init__` to expose each required value to the credential system.

*Call graph*: 2 external calls (__init__, __init__).


##### `KeyedProvider.usage`  (lines 112–122)

```
def usage(self) -> str
```

**Purpose**: This writes a short, practical help line showing an agent how to call the provider’s API from the sandbox. It names the needed slots and shows a sample `curl` command using the environment variables rather than raw secrets.

**Data flow**: It reads the provider name, label, secrets, headers, environment variable names, and host information. It formats those into a single human-readable string. If the provider uses a selectable host, the host slot is included in the listed slots and the example uses the host environment variable.

**Call relations**: The file uses this while building `SECTION_BODY`, the prompt text included in the manifest. Its output becomes part of the instructions agents see when they need to use keyed providers.


##### `manifest`  (lines 188–194)

```
def manifest() -> Manifest
```

**Purpose**: This is the file’s public entry point for the UFO extension system. It packages the provider table into a manifest containing credential definitions and guidance text.

**Data flow**: It reads the constants, the `KEYED_PROVIDERS` table, and the prepared `SECTION_BODY`. It asks each provider for its credential slots, combines them into one tuple, creates a prompt section, and returns a `Manifest` object with the extension name, version, credentials, and instructions.

**Call relations**: The larger UFO system calls `manifest` when loading this extension. The function gathers the slot declarations produced by `KeyedProvider.slots`, then hands them to `Manifest.__init__`; it also wraps the help text with `PromptSection.__init__` so agents can be told how to use these API-key-based connectors safely.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/pipedream/ufo_ext_pipedream/manifest.py`

`config` · `startup / extension load`

This file is the extension’s front desk. When the larger application loads extensions, it asks this file for a manifest, which is a simple declaration of what the extension adds to the system.

The main thing it adds is a set of connectors backed by Pipedream. A connector is an integration with an outside service, such as Gmail or another app. Pipedream keeps each user’s access token on its own servers, so this application does not have to store those secrets directly. That matters especially for services where ordinary shared OAuth clients cannot pass consent rules, such as restricted Gmail scopes.

The file builds one shared `PipedreamBroker`. A broker is the part that knows how to talk to Pipedream when the system needs to run connector actions, execute work on the server, or sync credentials. Then it loops through the connector catalog and turns each catalog entry into a `ConnectorProvider`, giving it a user-facing label, OAuth setup details, the shared broker, and the allowed transfer hosts.

It also declares one HTTP route: the browser bridge used during OAuth. OAuth is the “sign in and grant access” flow. After the user consents, the browser comes back through this route so the system can finish connecting the account.

#### Function details

##### `manifest`  (lines 23–45)

```
def manifest() -> Manifest
```

**Purpose**: Creates and returns the extension manifest that the main application reads to install the Pipedream extension. It lists all Pipedream-backed connectors and the OAuth callback route needed to finish account connection.

**Data flow**: It starts with the connector catalog in `CONNECTORS` and creates one shared `PipedreamBroker`. For each catalog entry, it builds a `PipedreamOAuthProvider` with the service’s host and app information, wraps that with a label, broker, and transfer-host allowlist into a `ConnectorProvider`, then gathers all providers into a `Manifest`. It also adds a GET route for the OAuth browser bridge. The result is a complete `Manifest` object; it does not directly connect to Pipedream or change user accounts by itself.

**Call relations**: This function is called when the extension system wants to know what this package contributes. During that build, it calls the Pipedream broker constructor once, walks through `CONNECTORS.items()` to create each connector provider, creates OAuth provider objects for the consent flow, and creates a route specification pointing to `oauth_route`. The returned manifest is then used by the wider connector registry and routing system so users can connect accounts and later use those connectors.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, __init__, items).


### Slack integration manifest
This manifest declares Slack-specific routes, credentials, tools, hooks, and assistant guidance as a standalone integration package.

### `extensions/slack/ufo_ext_slack/manifest.py`

`config` · `startup / extension discovery`

This file is the Slack extension’s “front desk sign.” When the main system discovers the extension, it needs a simple answer to questions like: What web requests should Slack send here? What secrets does each workspace need? What tools and setup instructions should the agent expose? This file answers those questions by building a Manifest.

It names the extension and version, then defines two private credential slots: a Slack bot token and, for bring-your-own Slack apps, a Slack signing secret. OAuth-based installs use deployment-wide Slack app secrets from environment variables elsewhere, so those are deliberately not listed as workspace credentials here.

The manifest also declares the Slack surface, meaning the Slack-facing part of the system. It registers routes for incoming Slack events, interactive button or form actions, and the OAuth callback. It connects the surface to functions that post messages, attach to conversations, speak as the bot, identify the workspace, and find the bot’s own Slack user ID.

Beyond web traffic, the file adds Slack tools, a short prompt section that teaches the agent how to write human-readable Slack mentions, hooks that run around tool use and prompt submission, and a setup skill for users who create their own Slack app. Without this file, the core system would not know how to expose or run the Slack extension.

#### Function details

##### `manifest`  (lines 54–96)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the Slack extension’s complete registration record for the core system. Someone would use it when loading the extension so the platform knows which Slack routes, credentials, tools, hooks, prompts, and setup skills are available.

**Data flow**: It starts with constants and imported Slack functions, such as route handlers and credential slot names. It packages them into manifest pieces: credential slots for workspace secrets, surface routes for Slack web requests, prompt text read from a Markdown file, hook definitions for important moments in a run, and a setup skill path. The output is one Manifest object that the rest of the system can read to wire Slack into the application.

**Call relations**: When the extension is loaded, the platform calls this function to get Slack’s declared shape. Inside, it creates CredentialSlot objects for the private Slack secrets, SurfaceRoute objects for Slack HTTP endpoints, a SurfaceSpec tying those routes to Slack behavior, a PromptSection for model guidance, HookSpec objects for lifecycle callbacks, a SkillSpec for setup instructions, and finally wraps everything in a Manifest that is handed back to the core system.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__).


### Source backend declarations
These files publish the sources extension and enumerate the source connector backends available to the system.

### `extensions/sources/ufo_ext_sources/manifest.py`

`config` · `startup / extension registration`

This file acts like the extension’s sign-up sheet. When the larger UFO system starts, it needs to know what this extension can do: which external content providers it can sync from, what credentials it needs, what objects it adds, and which events or scheduled jobs it wants to respond to. Without this file, the source connectors may exist in code, but the host would not know to load or use them.

The file declares a small factory, `ConnectorSourceFactory`, which knows how to turn a connector class into a runnable source backend. A connector is the provider-specific piece that talks to an outside service. A backend is the wrapper the sync system can run.

The main `manifest` function builds a `Manifest`, which is the extension’s package label and instruction card. It registers source-related object kinds, hooks for page changes and newly recorded connections, a retry job for connected sources that failed to be created, one source provider for every connector in the connector registry, one credential slot for each connector, and a `direct` authentication proxy. That direct proxy is the fallback way to read user-provided API keys, often called BYOK, meaning “bring your own key.”

#### Function details

##### `ConnectorSourceFactory.__call__`  (lines 41–42)

```
def __call__(self, _credentials: CredentialAccess) -> ConnectorBackend
```

**Purpose**: This turns a stored connector class into a source backend the sync system can run. It gives the system a standard backend object while hiding the small detail of constructing the provider-specific connector.

**Data flow**: It receives credential access information, although this factory does not use it directly. It creates a fresh connector instance from the connector class saved on the factory, wraps that connector in a `ConnectorBackend`, and returns the backend. Nothing else is changed.

**Call relations**: The `manifest` function creates one `ConnectorSourceFactory` for each registered connector and gives it to a `SourceProvider`. Later, when the source system needs to run that provider, it calls this factory, which hands off to `ConnectorBackend` so the connector can participate in the normal sync flow.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 45–82)

```
def manifest() -> Manifest
```

**Purpose**: This builds and returns the extension manifest: the complete list of things the sources extension contributes to the host system. It is the single place where connectors, credentials, hooks, jobs, objects, and the direct auth proxy are registered.

**Data flow**: It starts from constants in this file and the connector registry imported as `CONNECTORS`. It creates hook declarations for page changes and recorded connections, a scheduled retry job with workspace candidates from `connection_workspaces`, source provider declarations for every connector, credential slot declarations for those same connector names, and an auth proxy declaration for the `direct` backend. It packages all of that into a `Manifest` object and returns it.

**Call relations**: When the host loads this extension, it calls `manifest` to learn what to install. Inside that build step, it creates `HookSpec`, `JobSpec`, `SourceProvider`, `CredentialSlot`, and `AuthProxySpec` entries. The source providers use `ConnectorSourceFactory`; the hooks point to `on_page_change` and `on_connection_recorded`; the retry job points to `retry_connected_sources`; and the auth proxy builds `DirectAuthProxy` when direct API-key access is needed.

*Call graph*: 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, connection_workspaces, items).


### `extensions/sources/ufo_ext_sources/registry.py`

`config` · `startup`

This file solves a simple but important problem: when a source row says its backend is, for example, "slack" or "stripe", the system needs a reliable way to find the matching connector code. Instead of searching the whole codebase at startup, this file keeps an explicit registry: a hand-written list of every supported connector class.

Think of it like a phone book. The short backend name is the person's name, and the connector class is the phone number. Other parts of the system can look up the name and get exactly the connector they need.

The file imports every connector provider, such as GitHub, Google Drive, HubSpot, Jira, Notion, Zendesk, and so on. It then passes those connector classes into a small helper, `_connector_registry`, which builds the final `CONNECTORS` dictionary. That dictionary maps each connector's declared `name` to its class.

There is one important safety check: no two connectors may use the same name. If they do, the file raises an error while the registry is being built. This prevents confusing situations where a source backend name could point to two different integrations. Without this file, the sync driver and source system would not have a central, predictable way to know which integrations are available.

#### Function details

##### `_connector_registry`  (lines 61–69)

```
def _connector_registry(connector_types: tuple[type[Connector], ...]) -> dict[str, type[Connector]]
```

**Purpose**: This function builds the lookup table from connector name to connector class. It also protects the system from accidentally registering two connectors with the same name, which would make source lookup ambiguous.

**Data flow**: It receives a tuple of connector classes. For each class, it reads the class's `name` value, checks whether that name has already been used, and then stores the class under that name in a dictionary. It returns the completed dictionary, or raises an error if it finds a duplicate name.

**Call relations**: This function is called in this file when `CONNECTORS` is created. The file hands it the full list of imported connector classes, and it hands back the registry that other source-sync code can use to choose the right connector for a source backend name.
