# External connector and source integration manifests  `stage-3.4`

This stage is behind-the-scenes setup work. It is made of manifest files, which are like labels on plug-in boxes. Each manifest tells the main UFO system what an extension can do, what it needs, and how the rest of the system should load it.

The Composio manifest registers connectors that go through Composio, the shared broker that talks to Composio, and the web route used when a user signs in. The Pipedream manifest does the same kind of job for Pipedream, listing external apps, authorization needs, and its sign-in route. The Slack manifest describes Slack-specific routes, credentials, tools, hooks, and setup instructions so Slack can be connected cleanly.

The connectors manifest registers shared connector tools, data objects, and prompt text that helps the assistant explain and use those tools. The sources manifest registers source connectors, how to build them, the credentials they require, and any related objects or hooks. Together, these files make outside services visible and usable to the core system.

## Files in this stage

### Connector Manifests
Registers third-party connector brokers and shared connector tooling so external integrations can be discovered and used by the host system.

### `extensions/composio/ufo_ext_composio/manifest.py`

`config` · `startup / extension load`

This file is the extension’s “front desk sign.” When the main application loads the Composio extension, it needs a clear list of what the extension can provide: which connectors exist, how users authorize them, where requests should be routed, and how command-line credentials are passed along when needed.

The `manifest` function builds that declaration. It creates one shared `ComposioBroker`, which is the object responsible for arranging work through Composio rather than storing user tokens locally. It also creates a request forwarder for command-line cases, where an authorization header may need to be passed through safely.

For each explicitly listed connector, such as special cases that need a real provider host like GitHub, it creates a `ConnectorProvider`. Each provider includes an OAuth provider, meaning the piece that describes the browser-based consent flow; a label shown to people; the shared broker; allowed transfer hosts; and, when configured, a command-line credential rule.

The manifest also registers a resolver. This resolver lets the system recognize many Composio toolkits by slug instead of requiring every one to be listed by hand. Finally, it adds a GET route for the OAuth bridge, which is the browser callback path used during connection setup.

#### Function details

##### `manifest`  (lines 24–53)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the full declaration of the Composio extension. The main application uses this declaration to know which connectors, sign-in route, broker, and resolver the extension provides.

**Data flow**: It starts with fixed extension information such as the name, version, OAuth route path, connector definitions, and allowed transfer hosts. It creates a shared Composio broker and a request forwarder, then turns each configured connector into a provider entry with its OAuth details and optional command-line credential rule. The result is a `Manifest` object that the rest of the system can read to plug this extension into connection setup and tool execution.

**Call relations**: When the extension is loaded, this function is the place that gathers the moving parts into one package. It creates the broker, forwarder, OAuth providers, connector providers, resolver, and route specification, then hands all of them to the `Manifest` constructor so the host application can register Composio’s connectors and OAuth bridge.

*Call graph*: 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, items).


### `extensions/connectors/ufo_ext_connectors/manifest.py`

`config` · `startup`

This file is the “front desk sign” for the connectors extension. It does not implement the connector tools themselves. Instead, it declares what this extension offers to the larger UFO system.

The extension provides a single, shared tool surface for working with external connectors. These tools can list, describe, search, and run actions across any connector that other broker extensions register. That is why this file declares the tools once in a generic way, instead of making separate tools for every provider or broker.

It also declares two object types that the system may need to understand: a connection object and a connector grant object. In plain terms, these represent saved access to an external service and the permission or approval behind that access.

Finally, the file loads a Markdown prompt section from `prompts/connectors_section.md`. A prompt section is a named piece of instruction text that gets added to the assistant’s context, so the assistant knows how to present and use connector-related capabilities. The `manifest()` function packages all of this into a `Manifest`, which is the standard bundle the host reads when loading an extension.

#### Function details

##### `manifest`  (lines 21–28)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension declaration that the host system reads when loading the connectors extension. It names the extension, gives its version, lists its tools and objects, and attaches the connector-specific prompt section.

**Data flow**: It starts from constants in this file: the extension name and version, the connector tool list imported from the tools module, the connector-related object definitions, and the prompt text read from a Markdown file. It wraps the prompt text in a `PromptSection`, then puts everything into a `Manifest`. The result is a complete description of what this extension contributes to the system.

**Call relations**: When the extension is discovered during startup, the host calls `manifest()` to ask, “What do you provide?” This function creates a `PromptSection` for the assistant-facing instructions, then creates a `Manifest` that hands the host the tools, objects, and prompt material it should register.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/pipedream/ufo_ext_pipedream/manifest.py`

`config` · `startup / extension load`

This file is the extension’s front desk. When UFO loads extensions, it asks each one for a manifest, which is a small declaration of what the extension provides. Here, the Pipedream extension declares a set of connector providers, one for each supported app listed in the Pipedream connector catalog. A connector is a bridge to an outside service, such as Gmail or another app, and Pipedream acts as the trusted middle service that keeps the user’s access token on its own servers instead of exposing it to this deployment.

The file creates one shared PipedreamBroker. A broker is the part that knows how to run actions and server-side connector work through Pipedream after a user has connected an account. For each catalog entry, the manifest pairs three things: an OAuth provider, which starts and describes the sign-in/permission flow; a human-facing label; and the shared broker that will later perform work for that connector. It also marks which transfer hosts are allowed for Pipedream-backed operations.

Finally, it registers one browser route for the OAuth bridge. OAuth is the common “let this app access my account” permission flow. This route is the doorway the browser passes through when the consent process redirects back into UFO. In short, this file makes Pipedream connectors discoverable, connectable, and routable inside the larger application.

#### Function details

##### `manifest`  (lines 23–45)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the Pipedream extension manifest, which is the package of information UFO needs to enable this extension. It declares all Pipedream-backed connectors and the web route used during the browser-based authorization flow.

**Data flow**: It starts with the connector catalog imported from the Pipedream client module. It creates one Pipedream broker, then walks through each connector entry and turns it into a connector provider with an OAuth description, a display label, the shared broker, and the allowed transfer hosts. It also creates a route description for the OAuth bridge page. The result is a Manifest object that the host system can read to register the extension’s connectors and route.

**Call relations**: During extension loading, the host system calls this function to ask what the Pipedream extension contributes. The function hands off connector details to ConnectorProvider objects, creates PipedreamOAuthProvider objects for the sign-in flow, uses PipedreamBroker for later connector execution, and wraps everything in a Manifest. It also includes a RouteSpec so the main web routing layer knows to send the OAuth bridge path to oauth_route and identify the workspace through connect_bridge_workspace.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, __init__, items).


### Communication Integration
Declares the Slack-specific routes, credentials, tools, hooks, and setup instructions needed to wire the communication service into UFO.

### `extensions/slack/ufo_ext_slack/manifest.py`

`config` · `startup / extension discovery`

This file is the Slack extension’s front desk. When the main UFO system wants to know how Slack fits in, this manifest answers: what secrets are needed, what web addresses Slack can call, what tools the agent may use, and what background work should start during a user turn.

It defines two credential slots. One is the Slack bot token, which lets the extension act as the bot inside a Slack workspace. The other is a signing secret for people who bring their own Slack app; a signing secret is used to prove that incoming Slack requests really came from Slack. The comments make an important distinction: OAuth app secrets for the deployed Slack app come from environment variables, not from per-workspace credential slots.

The manifest also defines one Slack “surface,” meaning one outside communication channel. That surface accepts Slack event posts, interactive Slack actions, and OAuth callback requests. It also names helper functions for posting messages, attaching to conversations, identifying the workspace, and finding the bot’s own Slack user ID.

Finally, it registers tools and hooks. A hook is code that runs at a particular moment, like a reminder placed on a calendar. Here, one hook marks certain connector tool calls as Slack sends, and another starts Slack thread-following work whenever a user prompt is submitted.

#### Function details

##### `manifest`  (lines 44–84)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the Slack extension manifest, which is the object the core system reads to learn how this extension works. Someone would use it indirectly when the UFO runtime loads installed extensions.

**Data flow**: It starts with constants and imported Slack functions, such as route handlers, credential slot names, tools, and the skill folder path. It packages those pieces into credential definitions, surface route definitions, hook definitions, and a setup skill definition. The result is one Manifest object that describes the Slack extension to the rest of the system.

**Call relations**: When the extension is loaded, this function is the place that assembles all Slack-facing parts into one description. Inside that assembly it creates CredentialSlot objects for workspace secrets, SurfaceRoute and SurfaceSpec objects for Slack web traffic, HookSpec objects for events that should trigger Slack-related work, SkillSpec for the setup skill, and finally the Manifest object that contains them all.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, __init__, __init__).


### Source Connectors
Defines available source connectors, their construction, required credentials, exposed objects, and hooks for source integration.

### `extensions/sources/ufo_ext_sources/manifest.py`

`config` · `startup / extension load`

This file is the extension’s “label on the box.” When the application starts, it needs to know what this sources extension can do before it can use it. The manifest answers that in one place.

The extension supports multiple content-source connectors, such as the ones registered in `CONNECTORS`. For each connector, it creates a source provider: a small recipe that says, “if someone asks for this backend, build a connector backend from this connector class.” It also declares credential slots, which are named places where users can provide their own API keys. This is often called BYOK, meaning “bring your own key.”

The file also registers the object types this extension works with: sources, source triggers, and synced pages. It adds a `page_change` hook, which is a callback the system runs when a page changes so the change can be delivered where it belongs.

Finally, it declares a fallback authentication proxy named `direct`. An authentication proxy is a small layer that supplies credentials to connectors. Here, the direct proxy reads from the credential slots declared in this same manifest. Without this file, the host would not know these connectors exist, what credentials to ask for, or how page changes should be routed.

#### Function details

##### `ConnectorSourceFactory.__call__`  (lines 35–36)

```
def __call__(self, _credentials: CredentialAccess) -> ConnectorBackend
```

**Purpose**: This turns a connector class into a ready-to-use connector backend. It is used when the system wants to start syncing from one registered source provider.

**Data flow**: It receives a credential access object, but this factory does not read it directly. Instead, it creates a fresh connector instance from the connector class stored on the factory, wraps that connector in a `ConnectorBackend`, and returns the backend for the sync system to use.

**Call relations**: The manifest gives one of these factories to each `SourceProvider`. Later, when the host chooses a source backend, the provider calls this factory. The factory then hands off to `ConnectorBackend`, which is the shared wrapper that lets the rest of the sync machinery talk to the connector in a standard way.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 39–65)

```
def manifest() -> Manifest
```

**Purpose**: This builds and returns the complete declaration for the sources extension. The host application calls it to discover the extension’s name, version, objects, hooks, source backends, credential slots, and authentication proxy.

**Data flow**: It reads the connector registry, `CONNECTORS`, which maps backend names to connector classes. For each entry, it creates a source provider and a matching credential slot. It also adds the source/page object definitions, the page-change hook, and the `direct` authentication proxy. The result is a single `Manifest` object containing everything the host needs to install and run this extension.

**Call relations**: This is the main registration point for the file. During extension loading, the host calls `manifest`. Inside it, the code builds helper records such as `SourceProvider`, `CredentialSlot`, `HookSpec`, and `AuthProxySpec`, then packages them into `Manifest`. Those records later guide the sync runner, credential reader, auth proxy, and page-change delivery flow.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, items).
