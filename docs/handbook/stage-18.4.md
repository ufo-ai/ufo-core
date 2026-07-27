# External provider, credential, and connector manifests  `stage-18.4`

This stage is shared setup material. It does not do the main work itself. Instead, it tells the platform what outside services exist and how they can be used, like a directory of doors, keys, and sign-in desks.

The Bedrock file adds Amazon Bedrock model choices, including Anthropic and OpenAI-style models, with their costs, limits, and the client code needed to call them. The Composio and Pipedream manifests announce app connectors and the web routes used to finish user approval, such as an OAuth sign-in flow, which is a “log in and grant access” process. The keyed connectors file defines API-key connectors such as Datadog, including which secret is needed and where it may safely be sent. The Slack manifest declares Slack routes, required secrets, setup tools, and skills for receiving events and sending messages. The sources manifest registers content-source connectors, direct authentication, synced pages, and change notifications. The YC manifest adds YC-specific tools, credentials, onboarding, shared sources, and guidance files. Together, these files let the system discover integrations before anyone uses them.

## Files in this stage

### Model provider catalog
External model access begins with the Bedrock provider declaration that exposes hosted model choices, limits, pricing, and client construction.

### `extensions/bedrock/ufo_ext_bedrock.py`

`config` · `startup / extension load`

This extension is like a catalog card for Amazon Bedrock Mantle. The core UFO system already knows how to talk to Anthropic-style and OpenAI-style model APIs. This file does not translate requests itself. Instead, it says: “for these model names, use this endpoint, this API key, this region, and these model details.”

The file first defines shared settings, such as the provider name, the environment variable that holds the Bedrock API key, and the environment variables that may hold the AWS region. The region matters because Bedrock endpoints are regional; without it, the system cannot know where to send requests.

It then provides two client builders. One creates an Anthropic client pointed at Bedrock Mantle’s Anthropic endpoint. The other creates an OpenAI-compatible client pointed at the right Bedrock Mantle URL, choosing between the Chat Completions-style and Responses-style API paths.

Next, small helper functions build `ModelSpec` objects. A `ModelSpec` is the system’s description of one model: its id, provider, price, knowledge cutoff, context window, reasoning support, credential slot, and which client builder to use.

Finally, `manifest()` packages the credential requirement and all model specs into a `Manifest`, which is what the host system reads when loading this extension.

#### Function details

##### `bedrock_region`  (lines 42–48)

```
def bedrock_region() -> str
```

**Purpose**: Finds the AWS region that Bedrock Mantle requests should use. This is needed because Bedrock service addresses include a region, much like mailing a letter needs a city before it can be delivered.

**Data flow**: It reads the process environment, first looking for `AWS_REGION` and then `AWS_DEFAULT_REGION`. If it finds one, it returns that region string. If neither is set, it stops with an error explaining that a region must be configured.

**Call relations**: The Anthropic and OpenAI client builders call this before creating their network clients. It gives them the region needed to form or configure the Bedrock Mantle endpoint.

*Call graph*: called by 2 (_anthropic_client, _openai_client).


##### `_anthropic_client`  (lines 51–63)

```
def _anthropic_client(spec: ModelSpec, key: str) -> AnthropicClient
```

**Purpose**: Creates the runtime client used to call an Anthropic-style model through Bedrock Mantle. Someone uses this indirectly when a Bedrock Anthropic model is selected and the system needs an object that can actually send the request.

**Data flow**: It receives a model specification and an API key. It asks `bedrock_region` for the AWS region, builds an Anthropic Bedrock Mantle client with that key, region, no automatic retries, and a fixed timeout, then wraps it in UFO’s `AnthropicClient` along with the model spec. The result is a ready-to-use client object.

**Call relations**: This function is stored inside Anthropic `ModelSpec` entries by `_anthropic`. Later, when the core system needs to call one of those models, the spec can use this builder to produce the correct Bedrock-backed Anthropic client.

*Call graph*: calls 1 internal fn (bedrock_region); 3 external calls (__init__, AsyncAnthropicBedrockMantle, cast).


##### `_openai_client`  (lines 66–73)

```
def _openai_client(spec: ModelSpec, key: str) -> OpenAIClient
```

**Purpose**: Creates the runtime client used to call an OpenAI-compatible model through Bedrock Mantle. It hides the small URL differences between OpenAI-style chat models and OpenAI-style responses models.

**Data flow**: It receives a model specification and an API key. It gets the AWS region, chooses the base Bedrock Mantle URL based on the spec’s API surface, builds an OpenAI SDK client pointed at that URL, and wraps it in UFO’s `OpenAIClient`. The output is a client object ready to send requests for that model.

**Call relations**: This function is attached to OpenAI-compatible `ModelSpec` entries by `_openai`. When the core system later runs one of those models, this builder supplies the correctly configured OpenAI-style client.

*Call graph*: calls 1 internal fn (bedrock_region); 2 external calls (__init__, openai_sdk_client).


##### `_anthropic`  (lines 76–94)

```
def _anthropic(id: str, price: ModelPrice, cutoff: str, *, context_window: int=ANTHROPIC_CONTEXT_WINDOW) -> ModelSpec
```

**Purpose**: Builds a complete model description for a Bedrock-hosted Anthropic model. It keeps the repeated Anthropic model setup in one place so each model entry only needs to provide its id, price, cutoff date, and optional context size.

**Data flow**: It takes a model id, pricing information, a knowledge cutoff string, and optionally a context window size. It combines those with fixed Bedrock provider settings, reasoning support, Anthropic chat API settings, the Bedrock credential slot, and the `_anthropic_client` builder. It returns a `ModelSpec` describing that model.

**Call relations**: The file calls this while constructing `BEDROCK_MODEL_SPECS`. Each returned spec becomes part of the manifest that the extension exposes to the rest of UFO.

*Call graph*: 1 external calls (__init__).


##### `_openai`  (lines 97–111)

```
def _openai(id: str, price: ModelPrice, cutoff: str, window: int, api_surface: ApiSurface) -> ModelSpec
```

**Purpose**: Builds a complete model description for a Bedrock-hosted OpenAI-compatible model. It lets the file define several OpenAI-style models without repeating the same provider, credential, reasoning, and client-builder details each time.

**Data flow**: It takes a model id, pricing information, knowledge cutoff, context window size, and API surface name. It combines those with the Bedrock provider settings, the shared credential slot, reasoning support, and the `_openai_client` builder. It returns a `ModelSpec` ready to be included in the provider’s model list.

**Call relations**: The file calls this while constructing `BEDROCK_MODEL_SPECS` for OpenAI-compatible model ids. Those specs are then handed out through `manifest()` so the core system can discover and use them.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 182–193)

```
def manifest() -> Manifest
```

**Purpose**: Returns the extension’s public description to the UFO host system. This is the main handshake: it says what the extension is called, what credential it needs, and which models it provides.

**Data flow**: It creates a credential slot named for the Bedrock API key and describes what that key is for. It then combines the extension name, version, credential requirement, and all Bedrock model specs into a `Manifest`. The returned manifest is what the host reads to register this provider.

**Call relations**: The extension loader calls this when discovering the Bedrock extension. It hands the loader the credential requirement and model catalog built earlier in the file.

*Call graph*: 2 external calls (__init__, __init__).


### Connector credential manifests
These manifests declare third-party app connectors, their OAuth or API-key credential requirements, and the routes needed to complete connection flows.

### `extensions/composio/ufo_ext_composio/manifest.py`

`config` · `startup / extension discovery`

This file is the extension’s front desk. When the main system loads the Composio extension, it asks this file for a manifest: a structured description of the extension’s name, version, available connectors, connection rules, and routes. Without it, the host would not know that Composio connectors exist or how to send users through the right authorization flow.

Composio is used here as a server-side broker. That means user account tokens stay with Composio instead of being copied into this deployment. The file creates one shared broker, which is like a single service counter that all Composio-backed connectors use. It also creates a request forwarder for command-line credentials, used only for connectors that need a real provider host and a credential passed through a request header.

The manifest includes two kinds of connector access. First, it lists a small explicit set of connectors from the local connector catalog. Each one gets an OAuth provider, a user-facing label, the shared broker, allowed transfer hosts, and optionally a command-line credential rule. Second, it installs a resolver that can recognize other Composio toolkits by their slug, so the system can discover and route them without every one being listed here.

Finally, the file registers a browser callback route for the OAuth consent step. In plain terms, this is the web doorway users return through after approving access.

#### Function details

##### `manifest`  (lines 24–53)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the Composio extension manifest: the package of information the host application needs to expose Composio connectors. It is used when the extension is loaded so the host can know what connectors, sign-in route, and broker behavior to install.

**Data flow**: It starts with fixed extension details such as the name, version, and authorization header name. It creates a shared Composio broker and a request forwarder, then loops over the known connector definitions to turn each one into a connector provider with OAuth settings, labels, transfer-host rules, and optional command-line credential forwarding. It also adds a resolver for dynamically found Composio connectors and a web route for the OAuth browser callback. The result is a complete Manifest object; it does not directly connect to user accounts by itself.

**Call relations**: When the host asks this extension what it provides, this function assembles the answer. During that assembly it creates the broker, OAuth provider objects, connector provider entries, optional CLI credential rules, the resolver, and the OAuth route specification. Those pieces are handed to the Manifest so the larger system can later use them during connector discovery, browser-based connection, command-line credential forwarding, and brokered tool execution.

*Call graph*: 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, items).


### `extensions/keyed_connectors/ufo_ext_keyed_connectors.py`

`config` · `startup / manifest load`

Some services do not use a brokered login flow. Instead, they expect a user-owned API key in a request header. This file covers that case. It is like a safe mailroom: the agent can prepare a request with a placeholder, but the real secret is only swapped in by the egress proxy when the request is going to an allowed destination.

The file defines small records for keyed providers. A provider says which headers it needs, which environment variable names the sandbox should see, and which API host is allowed. For providers such as Datadog, where different accounts use different regional API hosts, the file lists the allowed hosts and creates an extra credential slot for choosing one. This avoids letting a user type any arbitrary hostname, which would be risky.

From those provider declarations, the file builds credential slots. Each slot describes what the owner must fill in and how the proxy should inject the secret into an outgoing request. The sandbox receives only a sentinel value, meaning a harmless marker that stands in for the secret. The manifest also adds a prompt section explaining to the agent how to request these credentials and how to call the provider API correctly.

Without this file, keyed services such as Datadog would not appear as safe, fillable credentials, and agents would have no standard way to call them without asking users to paste secrets into chat.

#### Function details

##### `KeyedProvider.__post_init__`  (lines 69–78)

```
def __post_init__(self) -> None
```

**Purpose**: This checks that each provider declaration is well-formed as soon as it is created. It prevents unclear or unsafe configurations, such as a provider claiming both one fixed host and a list of selectable hosts.

**Data flow**: It reads the provider’s host settings after the provider object is built. If exactly one style is present, either a fixed host or a closed list of sites, it leaves the object unchanged. If the declaration is inconsistent, it stops immediately by raising an error, so the bad provider cannot become part of the manifest.

**Call relations**: This runs automatically when a KeyedProvider row is created in the provider table. It acts as the gatekeeper before later steps, such as building credential slots or usage text, rely on the provider’s host information being safe and unambiguous.


##### `KeyedProvider.target_host`  (lines 81–90)

```
def target_host(self) -> str | HostChoice
```

**Purpose**: This gives the rest of the file the provider’s allowed API host in one standard form. For a simple provider it returns the fixed hostname; for a regional provider it builds a controlled host choice.

**Data flow**: It reads the provider’s fixed host or its list of allowed sites. If there is a fixed host, that string comes out directly. If there are selectable sites, it creates a HostChoice object containing the slot name, user-facing description, allowed host list, default host, and environment variable name.

**Call relations**: The slot-building and usage-text functions ask this property where requests may go. When a selectable site is needed, it hands off to HostChoice so downstream code can treat the host choice as a declared credential-related item rather than free-form user input.

*Call graph*: 1 external calls (__init__).


##### `KeyedProvider.slots`  (lines 92–110)

```
def slots(self) -> tuple[CredentialSlot, ...]
```

**Purpose**: This turns one provider declaration into the credential slots the platform should expose. Each secret becomes a fillable slot, and each slot says exactly which request header and host the secret is allowed to be injected into.

**Data flow**: It starts with the provider’s declared secrets and target host. For each secret, it creates a CredentialSlot with a description for the owner and an InjectionTarget that names the allowed host, header, sentinel value, sandbox environment variable, and request dimension. If the provider also needs the owner to choose a host from a fixed list, it adds one more slot for that host choice. The result is a tuple of credential slot declarations.

**Call relations**: The manifest function calls this for every provider when assembling the extension manifest. Inside, it creates InjectionTarget records to describe safe on-the-wire substitution, then wraps them in CredentialSlot records so the broader credential system can display and fill them.

*Call graph*: 2 external calls (__init__, __init__).


##### `KeyedProvider.usage`  (lines 112–122)

```
def usage(self) -> str
```

**Purpose**: This creates a short human-readable instruction line for the agent prompt. It tells the agent which slots belong to the provider and shows the shape of a curl command using the sandbox environment variables.

**Data flow**: It reads the provider name, label, secrets, environment variable names, and host information. It formats those into a sentence that names the slots and shows an example HTTPS request with headers like ordinary command-line usage. The result is a string added to the prompt section.

**Call relations**: This is used while building the shared prompt text for keyed providers. It depends on the same provider declaration as the slot builder, so the instructions shown to the agent match the credential slots that the manifest exposes.


##### `manifest`  (lines 188–194)

```
def manifest() -> Manifest
```

**Purpose**: This is the file’s public entry point for the extension system. It packages the keyed-provider credential slots and the explanatory prompt text into a Manifest object the host application can load.

**Data flow**: It reads the extension name, version, provider table, and prepared prompt body. It asks each provider for its credential slots, combines them into one collection, creates a PromptSection containing the guidance text, and returns a Manifest containing all of that. It does not contact any external service or read actual secrets.

**Call relations**: When the extension is loaded, the surrounding system calls this function to discover what the extension contributes. It hands off the finished data to Manifest and PromptSection constructors, giving the host application both the credential declarations and the instructions agents should follow.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/pipedream/ufo_ext_pipedream/manifest.py`

`config` · `startup`

This file is the extension’s “sign on the door.” It does not run connector actions itself. Instead, it declares what should be registered when the Pipedream extension is loaded.

Pipedream is used here as a broker, meaning it keeps users’ app tokens on its own servers and lets this system ask for work to be done without storing those secrets locally. That matters for apps where ordinary shared connector services cannot safely or successfully complete the login approval step. Gmail is one example mentioned in the file comments: Google restricts some Gmail permission scopes, so the deployment’s own OAuth client is used through Pipedream Connect.

The main `manifest` function builds a `Manifest`, which is a package of extension metadata. It gives the extension a name and version, then creates one connector entry for each item in the Pipedream connector catalog. Each entry says: this is the OAuth provider to use for login, this is the label members will see, this broker will run the connector work, and these are the allowed transfer hosts.

It also declares one browser-facing route. This route is used during the OAuth consent trip, when a user is redirected back after approving access. In plain terms, this file connects the catalog, the login bridge, and the broker into something the host application can register at startup.

#### Function details

##### `manifest`  (lines 23–45)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the Pipedream extension manifest, which is the object the host system reads to register this extension. It describes all available Pipedream-backed connectors and the route used to complete browser-based OAuth approval.

**Data flow**: It starts with the connector catalog from `CONNECTORS` and creates one shared `PipedreamBroker`. For each catalog entry, it turns the provider details into a `ConnectorProvider` with an OAuth provider, a user-facing label, the shared broker, and the allowed transfer hosts. It then adds a GET route for the OAuth bridge and returns a complete `Manifest` containing the extension name, version, connectors, and route.

**Call relations**: When the host application loads extensions, it calls `manifest` to ask what this extension contributes. Inside that build step, `manifest` creates the `PipedreamBroker`, reads `CONNECTORS.items()`, creates each `PipedreamOAuthProvider` and `ConnectorProvider`, and wraps the OAuth callback route in a `RouteSpec`. The returned `Manifest` is then used by the wider system to add these connectors to the connect registry and make the OAuth bridge route available.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, __init__, items).


### Service extension manifests
Service-specific manifests describe richer integrations with their required secrets, setup flows, routes, tools, skills, and shared data access.

### `extensions/slack/ufo_ext_slack/manifest.py`

`config` · `startup / extension discovery`

Think of this file as the Slack extension’s registration card. It does not process Slack messages itself. Instead, it names the Slack extension and describes the pieces the main application should connect.

It declares two private credential slots. One is for a Slack bot token, which lets the system act as the installed Slack bot. The other is for a Slack signing secret, used when someone brings their own Slack app instead of using the preferred OAuth install flow. OAuth means Slack redirects the installer back to this system so it can create the right workspace-specific bot token.

The file also declares the Slack “surface,” meaning the outside-facing contact point where Slack talks to the system. It lists three web routes: one for normal Slack events, one for interactive actions such as button clicks, and one for the OAuth callback used during installation. It also points to helper functions that know how to post back to Slack, attach Slack context, and identify which Slack workspace a request belongs to.

Finally, it advertises Slack-specific tools and a setup skill for bring-your-own app installation. In short, this file is the bridge between the Slack extension and the project’s extension framework.

#### Function details

##### `manifest`  (lines 36–67)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the Slack extension manifest, which is the structured description the core system reads to wire Slack into the application. Someone uses it when the extension is loaded so the system can learn Slack’s routes, credentials, tools, and setup skill.

**Data flow**: It starts with constants and imported Slack functions, such as the extension name, version, credential slot names, route handlers, posting helper, and setup skill path. It packages those into credential slot objects, route objects, a surface description, tool references, and a skill description. The result is one Manifest object that the rest of the system can read; this function does not directly send network requests or change external state.

**Call relations**: During extension loading, the core system calls this function to ask, “What does the Slack extension provide?” The function creates CredentialSlot entries for private Slack secrets, SurfaceRoute entries for Slack-facing HTTP endpoints, a SurfaceSpec tying those routes to Slack behavior, and a SkillSpec for setup instructions. It hands the finished Manifest back to the extension framework, which can then expose the routes and make the tools and credentials available where needed.

*Call graph*: 5 external calls (__init__, __init__, __init__, __init__, __init__).


### `extensions/sources/ufo_ext_sources/manifest.py`

`config` · `startup / extension registration`

This file is the extension’s “catalog card.” When the larger system starts up and looks for installed extensions, this manifest explains what the sources extension can do and how to plug it in.

The main idea is simple: different external services can act as content sources, and each one is represented by a connector. This file takes every registered connector from `CONNECTORS` and turns it into a `SourceProvider`, which is the system’s standard way to say, “this backend can sync content from this kind of account.” It also declares credential slots, one per connector, so a user or deployment can provide a bring-your-own-key API key for direct access.

It registers two object kinds: a source object, which represents a configured source that can be synced, and a page object, which represents synced content that can later be read. It also registers a `page_change` hook, so subscribers can be notified when synced page content changes.

Finally, it declares a `direct` authentication proxy. An authentication proxy is a small bridge that supplies credentials to connectors. If no broker-style provider is involved, this direct proxy reads the configured credential slots and lets the sync system talk to the external service directly. Without this file, the extension’s connectors, credentials, objects, and hooks would exist in code but would not be advertised to the application.

#### Function details

##### `ConnectorSourceFactory.__call__`  (lines 34–35)

```
def __call__(self, _credentials: CredentialAccess) -> ConnectorBackend
```

**Purpose**: This turns a connector class into a ready-to-use source backend. The system uses it when it needs to build the backend that will actually sync content for a registered connector.

**Data flow**: It receives a credential access object, though this factory does not read it directly. It creates a fresh connector instance from the connector class stored on the factory, wraps that connector in a `ConnectorBackend`, and returns the backend to the caller.

**Call relations**: The manifest creates one `ConnectorSourceFactory` for each registered connector and gives it to a `SourceProvider`. Later, when the source system needs a backend for that provider, it calls this factory. The factory then hands off to `ConnectorBackend`, which is the standard wrapper used by the sync framework to run connector-based sources.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 38–64)

```
def manifest() -> Manifest
```

**Purpose**: This builds the complete manifest object for the sources extension. The host application calls it to discover the extension’s name, version, object types, hooks, source providers, credential slots, and authentication proxy.

**Data flow**: It reads the registered connector map from `CONNECTORS`. For each connector, it creates a source provider and a matching credential slot. It also adds the source and page object definitions, the page-change hook, and the `direct` authentication proxy builder. The result is one `Manifest` object that describes everything this extension contributes.

**Call relations**: This is the file’s main entry for the extension system. During extension loading, the host calls `manifest`, and this function assembles smaller pieces such as `SourceProvider`, `CredentialSlot`, `HookSpec`, and `AuthProxySpec` into one declaration. The auth proxy builder inside the manifest creates `DirectAuthProxy` when the system needs direct credential-based access.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, items).


### `extensions/yc/ufo_ext_yc/manifest.py`

`config` · `extension load and onboarding`

This file is the extension’s “front desk sign.” It does not do the YC reading or authentication itself. Instead, it declares what is available and how the rest of the system should wire it in.

It defines three tools. `yc_auth` lets the workspace owner connect a YC account through browser-based device authorization. `yc_read` lets the assistant read YC and Bookface information through that authorized account, while warning that the results are external and should not be blindly trusted. `yc_index` saves a bounded YC or Bookface search into shared workspace memory so it can be refreshed and reused later.

The file also declares one credential slot: a named place where the encrypted YC login information lives. This is important because the credential is used by host-side code but is not exposed to chat or the sandbox.

For longer-term knowledge, the manifest registers a source backend that can build a `YcSource` using a `YcCli` client. During onboarding, `setup_sources` adds predefined YC guidance collections to shared workspace memory. Finally, it points the platform at a skills folder, which contains guidance the assistant can use when doing YC research. In short, this file is the map that lets the platform discover and safely activate the YC extension.

#### Function details

##### `setup_sources`  (lines 77–84)

```
async def setup_sources(ctx: ExtensionContext) -> None
```

**Purpose**: This function adds the extension’s built-in YC guidance collections as shared sources for the workspace. It is used during onboarding so the workspace starts with useful YC reference material available.

**Data flow**: It receives an extension context, which is the platform object used to register extension resources. For each predefined YC guidance collection, it creates a `YcSourceConfig` describing that collection and asks the context to register it under the YC source backend as shared workspace content. It does not return a value; its effect is that those sources become known to the platform.

**Call relations**: This function is attached to the manifest as the handler for the onboarding step named `yc_sources`. When the platform runs that onboarding step, it calls this function, and the function hands each collection to `ExtensionContext.register_source` so the source system can track and refresh it.

*Call graph*: calls 1 internal fn (register_source); 1 external calls (__init__).


##### `manifest`  (lines 87–110)

```
def manifest() -> Manifest
```

**Purpose**: This function builds and returns the extension manifest, which is the platform-readable description of everything this YC extension offers. The platform uses it to discover the extension’s tools, credential needs, source provider, onboarding work, and skills.

**Data flow**: It takes no input. It gathers the constants and tool definitions from this file, creates a credential slot for the YC account, creates a source provider that can build `YcSource` objects from stored credentials, adds the onboarding step that points to `setup_sources`, and includes the YC research skill directory. It returns a `Manifest` object containing all of that information.

**Call relations**: This is the main declaration point for the file. When the extension is loaded, the platform calls this function to learn what to install. Inside the returned manifest, it wires together `yc_auth`, `yc_read`, and `yc_index`, connects stored credentials to `YcCli` and `YcSource`, and names `setup_sources` as the onboarding action to run later.

*Call graph*: 5 external calls (__init__, __init__, __init__, __init__, __init__).
