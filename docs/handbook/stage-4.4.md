# Connector framework and authentication broker registrations  `stage-4.4`

This stage is behind-the-scenes setup work. It tells the UFO system which outside-service connectors exist, how users can sign in to them, and where the system should send connector-related requests. Think of it like installing labeled sockets before any appliances are plugged in.

The Composio manifest registers the Composio extension, including its connector lookup rules and the web address used when a user returns from an OAuth consent screen. OAuth is the common “sign in with another service” flow. The main connectors manifest adds shared connector tools, connector objects, and extra prompt instructions used when connector features are active. The keyed connectors file covers simpler services that use API keys. It creates private credential slots so owners can store secrets without exposing them to the sandbox. The Pipedream manifest registers Pipedream-backed connectors, their sign-in route, and the broker that runs their actions. The sources package marker only makes its folder importable. The sources manifest registers sync-related connectors, credentials, hooks, retries, and default authentication. Finally, the sources registry is the address book mapping names like Slack or GitHub to the code that talks to them.

## Files in this stage

### Composio OAuth registration
Registers the Composio extension metadata, connector lookup behavior, and OAuth consent redirect route.

### `extensions/composio/ufo_ext_composio/manifest.py`

`config` · `startup / extension discovery`

This file is the Composio extension’s “front desk sign.” When the main system discovers the extension, it asks this file for a manifest, which is a small declaration of what the extension offers and how the system should reach it. Without this file, UFO would not know that Composio connectors exist, how to resolve them by name, or where to send browser-based OAuth consent callbacks.

The manifest creates a ComposioBroker, which is the bridge to Composio’s server-side tool execution and account-token storage. It then gives that broker to a ComposioResolver, which lets the system find Composio toolkits by their slug, meaning a short identifier such as a product or toolkit name. This matters because the connector registry can then search and route requests without needing a separate provider host for every tool.

The file also registers one HTTP route: a GET endpoint for the OAuth consent step. OAuth is the common “approve this app to access my account” flow. The route points to oauth_route, and uses connect_bridge_workspace to identify the workspace involved in that browser redirect. A key safety detail is that Composio keeps account tokens on its own server side, so this deployment does not receive or store those secrets.

#### Function details

##### `manifest`  (lines 18–32)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the Composio extension manifest, which is the object the host system reads to learn what this extension provides. Someone would use it when loading extensions so Composio connectors and the OAuth redirect route become available.

**Data flow**: It starts with no outside arguments. It creates a ComposioBroker, passes that broker into a ComposioResolver, creates a route description for the OAuth callback path, and packages all of that with the extension name and version into a Manifest object. The result is a complete declaration the host system can register.

**Call relations**: During extension loading, the host system calls this function to get Composio’s declaration. Inside, it calls ComposioBroker.__init__ to create the broker, ComposioResolver.__init__ so connector names can be resolved through that broker, RouteSpec.__init__ to describe the OAuth browser route, and Manifest.__init__ to bundle everything into the final extension manifest.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).


### Shared and keyed connectors
Declares the shared connector framework and API-key-based credential slots for services that do not use brokered login flows.

### `extensions/connectors/ufo_ext_connectors/manifest.py`

`config` · `startup / extension discovery`

This file is the extension’s “label and contents card.” When the system loads extensions, it needs a standard way to ask each one: What is your name? What version are you? What tools and objects do you add? This file answers those questions for the connectors extension.

The connectors extension is meant to expose external tools in a generic way. Instead of declaring separate tools for every outside service or provider, it declares one shared tool surface that can list, describe, search, and run connector tools across all registered connector providers. In plain terms, it is like adding one universal remote control rather than a different remote for every device.

At load time, the file reads a Markdown prompt section from `prompts/connectors_section.md`. That text becomes an extra instruction section named `external_tools`, so the model or agent knows how to think about and use these connector tools. The file also includes two connector-related object definitions: one for a connection and one for a connector grant. Together, these declarations let the rest of the system discover the extension cleanly without hard-coding connector details elsewhere.

#### Function details

##### `manifest`  (lines 21–28)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the formal description of the connectors extension. The host application uses this description to learn the extension’s name, version, available tools, object types, and prompt instructions.

**Data flow**: It starts from constants already loaded in the file: the extension name and version, the shared connector tools, the connection-related object definitions, and the Markdown prompt text read from disk. It wraps the prompt text in a `PromptSection`, then places everything into a `Manifest`. The result is a complete manifest object that the extension system can consume.

**Call relations**: When the extension system asks this module what it provides, this function creates the answer. It calls `PromptSection.__init__` to package the prompt text under the `external_tools` section name, then calls `Manifest.__init__` to assemble the final extension declaration.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/keyed_connectors/ufo_ext_keyed_connectors.py`

`config` · `startup / extension manifest load`

Some integrations cannot be connected through a broker such as Composio or Pipedream, because the provider expects the user to bring their own API key. This file is the catalog for those “keyed” providers, such as Datadog, PostHog, Mercury, and others. Without it, the agent would not know what credential fields to ask for, which HTTP header each key belongs in, or which API host is safe to send it to.

The main idea is simple: each provider row says, “This service lives at this host, needs these secret keys, and those keys should be sent in these headers.” For providers with several official regional hosts, such as Datadog, the user chooses from a fixed list. That prevents a secret from being sent to a made-up or wrong hostname.

The sandbox does not receive the real key. It receives an environment variable containing a sentinel value, which is like a placeholder ticket. When the sandbox makes an outbound HTTP request, the egress proxy replaces that placeholder with the real secret only on the approved host and header. This keeps secrets out of chat, files, logs, and sandbox memory.

The file also builds a manifest, which is the extension’s public declaration: its credential slots and a help section explaining how an agent should use these providers safely.

#### Function details

##### `KeyedSecret.__post_init__`  (lines 56–61)

```
def __post_init__(self) -> None
```

**Purpose**: This checks that a declared API key uses only an authorization scheme the proxy knows how to safely replace. It protects the system from accepting a provider declaration that looks valid but cannot actually be swapped onto outgoing requests.

**Data flow**: After a KeyedSecret is created, it reads its own scheme field. If there is no scheme, it accepts the secret as a plain header value. If there is a scheme, it must be one of the approved forms, such as Bearer, API-Key, or Token; otherwise the function stops creation by raising an error.

**Call relations**: This runs automatically whenever the provider table creates a KeyedSecret. Later, KeyedProvider.slots relies on these secrets being safe to turn into injection rules for the egress proxy.


##### `KeyedProvider.__post_init__`  (lines 79–88)

```
def __post_init__(self) -> None
```

**Purpose**: This checks that each provider describes its API host in exactly one safe way. A provider must either have one fixed host or a fixed menu of allowed hosts, but not both and not neither.

**Data flow**: After a KeyedProvider is created, it reads its host, sites, host_env, and site_description fields. If the provider has both a single host and site choices, or has neither, it raises an error. If it uses site choices but does not say how to describe and export that choice, it also raises an error. A valid provider object is left unchanged.

**Call relations**: This runs automatically while the static KEYED_PROVIDERS table is being built. Its checks make later steps safer: KeyedProvider.target_host can assume the host information is complete, and KeyedProvider.slots can build credential slots without guessing.


##### `KeyedProvider.target_host`  (lines 91–100)

```
def target_host(self) -> str | HostChoice
```

**Purpose**: This returns the host rule for a provider: either one fixed hostname or a controlled host choice the user may select from. It is what keeps secrets tied to approved destinations.

**Data flow**: It reads the provider’s host-related fields. If the provider has a fixed host, it returns that hostname as text. If the provider has multiple allowed sites, it creates and returns a HostChoice object containing the slot name, user-facing description, allowed host list, default host, and environment variable used in the sandbox.

**Call relations**: KeyedProvider.slots calls this when building credential injection rules, and KeyedProvider.usage calls it when writing the user-facing usage instructions. When multiple sites are available, this function hands off to HostChoice.__init__ to package the safe choices.

*Call graph*: 1 external calls (__init__).


##### `KeyedProvider.slots`  (lines 102–120)

```
def slots(self) -> tuple[CredentialSlot, ...]
```

**Purpose**: This turns one provider declaration into the credential slots the platform will show and fill. Each slot says what secret is needed and exactly how the proxy should inject it into an outgoing request.

**Data flow**: It starts with a provider and its secrets. For each secret, it creates a CredentialSlot with a name, description, and InjectionTarget. The injection target records the approved host, HTTP header, sentinel placeholder, sandbox environment variable, and request dimension. If the provider has a selectable host, it also adds a separate credential slot for that host choice. The result is a tuple of slots ready to include in the manifest.

**Call relations**: The top-level manifest function calls this for every provider in KEYED_PROVIDERS. Inside, it uses KeyedProvider.target_host to get the safe destination and hands details to CredentialSlot.__init__ and InjectionTarget.__init__ so the wider credential and proxy system can understand them.

*Call graph*: 2 external calls (__init__, __init__).


##### `KeyedProvider.usage`  (lines 122–135)

```
def usage(self) -> str
```

**Purpose**: This writes a short help line showing how an agent should call the provider’s REST API from the sandbox. It names the needed slots and shows a curl-style example with environment variables instead of real secrets.

**Data flow**: It reads the provider name, label, secrets, host information, and environment variable names. It builds example HTTP headers using the right header names and optional schemes. It also lists the credential slot names, including the host-choice slot when needed. The output is a human-readable string used in the prompt section.

**Call relations**: SECTION_BODY calls this once for each provider while building the extension’s instructional text. It depends on KeyedProvider.target_host to know whether the example should use a fixed hostname or the selected host environment variable.


##### `manifest`  (lines 284–290)

```
def manifest() -> Manifest
```

**Purpose**: This is the file’s public export: it builds the extension manifest that tells the platform what this extension is, which credentials it needs, and what guidance to add to the agent prompt.

**Data flow**: It reads the extension name, version, provider table, and prepared prompt text. It gathers all credential slots by asking each provider for its slots. Then it creates a Manifest containing those slots and a PromptSection containing the keyed-provider instructions. The returned Manifest is what the rest of the system consumes.

**Call relations**: The extension loader calls this when it needs the extension declaration. This function gathers the work done by KeyedProvider.slots and packages it through Manifest.__init__ and PromptSection.__init__ for the platform.

*Call graph*: 2 external calls (__init__, __init__).


### Pipedream broker registration
Registers Pipedream-backed connectors, their sign-in route, and the broker responsible for running connector actions.

### `extensions/pipedream/ufo_ext_pipedream/manifest.py`

`config` · `startup / extension discovery`

This file is the extension’s front desk. When the larger UFO system loads extensions, it needs a clear list of what each extension adds: which services users can connect to, how sign-in should work, and where requests should go afterward. This file provides that list for Pipedream.

Pipedream is used here as an outside service that can hold OAuth tokens. OAuth is the common “sign in with Google/GitHub/etc.” flow that lets an app act with a user’s permission without seeing their password. For each connector listed in the Pipedream connector catalog, this manifest creates a connector provider. Each provider gets a user-facing label, a Pipedream OAuth provider for login, a shared Pipedream broker that actually runs actions, allowed transfer hosts, and sometimes a command-line credential for tools such as GitHub.

It also adds a browser route for the OAuth bridge. That route is the doorway users come back through after consenting in the browser. Without this file, the rest of the system would not know that these Pipedream connectors exist, how to start their login flow, or which broker should execute their actions.

#### Function details

##### `manifest`  (lines 28–51)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the Pipedream extension manifest, which is the package of declarations the main system reads to enable this extension. It lists all Pipedream-backed connectors and the route needed for the browser sign-in bridge.

**Data flow**: It starts by creating one shared PipedreamBroker, which is the object responsible for carrying out connector actions through Pipedream. It then reads every configured connector from CONNECTORS, turns each catalog entry into a ConnectorProvider with OAuth settings, label, broker, transfer-host allowlist, and any command-line credential, and finally wraps everything in a Manifest. The result is a complete description of the extension that the host application can register.

**Call relations**: During extension loading, the host expects this function to provide the extension’s declaration. Inside that declaration, it creates PipedreamOAuthProvider objects for user consent, ConnectorProvider objects for each available connector, a RouteSpec for the OAuth callback route, and a Manifest that ties them together. It also asks cli_credential for any connector-specific command-line credential before handing the finished manifest back to the host.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, items, cli_credential).


### Source synchronization registry
Sets up the sources package, registers source synchronization extension capabilities, and maps supported external providers to connector classes.

### `extensions/sources/ufo_ext_sources/__init__.py`

`other` · `import/package discovery`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may contain useful tools in other files, but this label is what lets the rest of the program refer to the drawer by name. Without this file, some Python setups or tooling might not reliably recognize `extensions/sources/ufo_ext_sources` as a package, which could make imports fail or make extension discovery less predictable. Because the file is empty, it does not run setup code, expose shortcuts, or change any data. Its value is structural: it helps organize source-extension code under a clear package name.


### `extensions/sources/ufo_ext_sources/manifest.py`

`config` · `startup and extension loading`

This file is the extension’s front desk. When UFO starts or loads extensions, it asks this file for a manifest: a structured declaration of everything the sources extension contributes. Without it, the rest of the code might exist, but UFO would not know that these connectors, hooks, jobs, or authentication options are available.

The extension supports several content providers through registered connectors. A connector is the provider-specific piece that knows how to read from an outside service. This file wraps each connector in a small factory so the sync system can build a matching backend when needed.

It also declares three object kinds: sources, source triggers, and pages. In plain terms, these are the stored records for what to sync, how to notify conversations about changes, and what synced content can be read back.

The hooks are event listeners. For example, when a page changes, the extension can deliver that change to conversations. When a new account connection is recorded, it can create the usual synced sources automatically. When a user prompt or tool result includes a link, it can notice that link and offer a trigger.

Finally, the file declares a scheduled retry job for connected sources that did not get created successfully the first time, plus credential slots for “bring your own key” API keys and a built-in direct auth proxy that reads from those slots.

#### Function details

##### `ConnectorSourceFactory.__call__`  (lines 48–49)

```
def __call__(self, _credentials: CredentialAccess) -> ConnectorBackend
```

**Purpose**: This turns a registered connector class into a usable source backend. The sync system can call the factory when it needs a backend for a provider, and it receives a fresh connector wrapped in the common connector backend interface.

**Data flow**: It receives credential access, though this factory does not read it directly. It creates a new instance of the connector class stored on the factory, puts that connector inside a ConnectorBackend, and returns the backend ready for syncing.

**Call relations**: The manifest creates one ConnectorSourceFactory for each registered connector and gives it to a SourceProvider. Later, when the source system needs to build that provider’s backend, it calls this factory, which hands off to ConnectorBackend so the connector can be used by the shared sync machinery.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 52–91)

```
def manifest() -> Manifest
```

**Purpose**: This builds the full declaration for the sources extension. UFO uses it to learn which objects, event hooks, scheduled jobs, source backends, credential slots, and auth proxies this extension provides.

**Data flow**: It starts with constants and the connector registry. It turns the known objects into manifest entries, wraps event handlers as hook specifications, defines a scheduled retry job, creates one source provider and one credential slot per connector, and registers the direct authentication proxy. The result is a Manifest object that the host application can load.

**Call relations**: This is the main function the extension system calls when it wants to load the sources extension. While building the Manifest, it constructs HookSpec, JobSpec, SourceProvider, CredentialSlot, AuthProxySpec, and ConnectorSourceFactory objects, and uses connection_workspaces to define where the retry job should run. The returned Manifest is then used by the wider UFO runtime to wire these pieces into syncing, event handling, credentials, and background jobs.

*Call graph*: 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, connection_workspaces, items).


### `extensions/sources/ufo_ext_sources/registry.py`

`config` · `startup`

This file answers a simple but important question: when a source says its backend is “slack” or “github”, which connector code should the system use? Instead of searching the codebase at startup, it imports every supported connector explicitly and puts them into one registry. This is like a printed directory at a front desk: the system can look up a provider by name and immediately know which specialist to call.

The registry is built from connector classes. Each connector class has a `name`, which is the short label used elsewhere in the system. That same label connects several pieces together: the source row’s backend name, the place credentials are read from, and the backend entry that the sync driver can run.

The helper function checks that no two connectors claim the same name. That matters because duplicate names would make a source ambiguous: if two different classes both said they were “stripe”, the system would not know which one to use. If a new provider is added, this file must import it and include it in the connector list. Without this file, the rest of the source-sync system would have no reliable way to turn a saved backend name into working connector code.

#### Function details

##### `_connector_registry`  (lines 66–74)

```
def _connector_registry(connector_types: tuple[type[Connector], ...]) -> dict[str, type[Connector]]
```

**Purpose**: Builds the lookup table from connector name to connector class. It also protects the system from accidentally registering two connectors with the same name.

**Data flow**: It receives a tuple of connector classes. It starts with an empty dictionary, reads each class’s `name`, and stores the class under that name. If a name is already present, it stops with an error instead of silently choosing one. The result is a dictionary that lets the rest of the system find the right connector class by a short backend name.

**Call relations**: This function is used when the module is loaded to create the `CONNECTORS` registry from the long explicit list of provider connector classes. After that, other parts of the source system can use `CONNECTORS` as the shared directory of available source backends.
