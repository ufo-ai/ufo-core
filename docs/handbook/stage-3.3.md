# Tool, Skill, Object, and Subagent Extension Registration  `stage-3.3`

This stage is the system’s plug-in registration desk. It runs behind the scenes so that, when an agent starts working on a turn, it knows which extra abilities are available and how to use them. Most files here are “manifests,” simple menu cards that describe an extension to the host system.

The browser, research, coding, documents, brief-pipeline, YC, memory, and scheduled-tasks manifests advertise bigger work packages: web browsing, deep research, repository work, document help, helper-agent pipelines, YC data access, remembered user context, and recurring tasks. The connectors, Composio, Pipedream, keyed connectors, and sources files register ways to reach outside services. Some use sign-in flows, some use API keys, and the sources registry maps short names like Slack or GitHub to the right connector. The MCP file adds a bridge to external tool servers and safely calls their tools.

Together, these files do not perform the main work themselves. They label the tools, agents, credentials, routes, and instructions so the main UFO system can discover them, load them, and offer them at the right moment.

## Files in this stage

### Delegated work packs
These manifests register agent-facing packs that add helper subagents, skills, and tools for briefs, browser automation, coding, documents, and research.

### `extensions/brief_pipeline/ufo_ext_brief_pipeline/manifest.py`

`config` · `startup or extension discovery`

This file is the extension’s “label on the box.” When the larger UFO system discovers or loads the brief-pipeline extension, it needs a simple description of what is inside: the extension name, its version, the subagents it offers, and where its skill files live. Without this manifest, the host would not know that this extension contains an outline maker, a draft writer, and a critic, or that they are meant to be used as a chain.

The extension is built around a common writing workflow: first create an outline, then turn that outline into a draft, then ask for critique. The file imports the three subagent profiles from the pipeline module and points to a skills folder on disk. A “skill” here means packaged guidance or instructions that teach the parent agent how to use the extension.

The main function, `manifest`, returns a `Manifest` object. That object is like a registration card handed to the system: it says “my name is brief_pipeline, my version is 0.1.0, these are my helper agents, and this is where my skill instructions are.”

#### Function details

##### `manifest`  (lines 15–21)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension description that the host system reads when loading this package. Someone would use it so the system can discover the brief-pipeline subagents and the skill instructions that explain how to chain them.

**Data flow**: It starts with the constants in this file: the extension name, version, and skill folder path. It also uses the imported outline, draft, and critic profiles. It packages those pieces into a `Manifest`, including a `SkillSpec` that points to the skill directory, and returns that finished manifest object to the caller.

**Call relations**: During extension loading, the host calls `manifest` to ask what this extension provides. Inside, it creates a `SkillSpec` for the on-disk skill folder and then creates a `Manifest` that includes that skill plus the three subagent profiles. The returned manifest is what lets the rest of the system know how to expose and use the brief-pipeline workflow.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/browser/ufo_ext_browser/manifest.py`

`config` · `startup/config load`

This file is like a packing list for the browser extension. It does not perform browser automation itself. Instead, it declares what should be loaded when the extension is installed or enabled.

The file names the extension as "browser" and gives it a version. It reads a Markdown prompt section from disk, which is text shown to the main agent so it knows how to ask for browser help. The important idea is separation: the main agent does not directly receive the low-level browser and computer-use tools. Those belong to a specialized browser subagent. The main agent gets delegation tools, such as tools that let it ask the browser subagent to do a web task.

The `manifest` function packages all of this into a `Manifest` object. That package includes the browser tools, delegation tools, browser subagent profile, prompt section, and a requirement called `cdp_providers`. CDP means Chrome DevTools Protocol, a way for software to control and inspect a browser. In plain terms, this file makes sure the system knows: “there is a browser worker available, here is how to talk to it, and here is what support it needs.”

#### Function details

##### `manifest`  (lines 23–31)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the browser extension's declaration for the UFO system. Someone would use this when loading extensions so the system can discover the browser tools, browser subagent, and prompt instructions.

**Data flow**: It starts with constants from this file, tool lists imported from the browser extension, the browser subagent profile, and prompt text read from `prompts/browser_section.md`. It wraps the prompt text in a `PromptSection`, then puts the name, version, tools, subagent, prompt section, and required browser-control support into a `Manifest`. The result is a single object that describes everything this extension contributes.

**Call relations**: During extension loading, the broader system calls this function to ask, “what do you provide?” The function creates a `PromptSection` for the main agent's instructions and a `Manifest` that collects the extension's tools and browser subagent profile, then hands that manifest back to the loader.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/coding/ufo_ext_coding/manifest.py`

`config` · `startup / extension registration`

This file packages up everything needed for a coding-focused child agent. That child can inspect a codebase, edit files, run commands, use coding skills, and report back to the main agent. Without this file, the core system would not know that the coding subagent exists, what tools it may use, what prompt guides it, or how to load the related “coding” and “code-review” skills.

It also describes how GitHub access works. The extension can use a GitHub App installation when the deployment is configured for one, or fall back to a member-provided GitHub token for repositories outside that setup. The credential is not simply handed into the sandbox as plain text. Instead, the file defines a sentinel value, like a placeholder ticket, which is swapped for the real token only when network traffic leaves through the controlled proxy.

The main output is the `manifest()` function. A manifest is like a menu card for the host system: it lists the subagent profile, skills, credential slots, a `connect_github` tool for starting GitHub App installation, and a callback route that GitHub can return to after installation.

#### Function details

##### `github_app_id`  (lines 96–109)

```
def github_app_id() -> str | None
```

**Purpose**: This function checks whether the deployment has been fully configured with GitHub App settings. It returns the App ID when all required settings are present, returns nothing when none are present, and raises an error if only some are present, because that half-configured state would lead to confusing GitHub behavior.

**Data flow**: It reads four environment variables: the GitHub App ID, client ID, client secret, and private key. If all four are empty, it returns `None`, meaning the extension should not use GitHub App token minting. If any are set but others are missing, it stops startup with a clear error listing the missing names. If all are present, it returns the App ID string.

**Call relations**: This check is used while the file builds the Git credential slot. That slot needs to know whether to use GitHub App-generated tokens or rely on a member-supplied token. By making the decision early, the manifest can advertise the right credential behavior to the rest of the system.


##### `manifest`  (lines 138–163)

```
def manifest() -> Manifest
```

**Purpose**: This function builds and returns the extension manifest, which is the object the UFO host reads to discover this coding pack. Someone uses it when registering the extension so the host can enable the coding subagent, load skills, expose the GitHub connection tool, and install the GitHub callback route.

**Data flow**: It starts from constants and objects already defined in the file: the extension name and version, the coding subagent profile, skill folders, credential slots, GitHub connection tool details, and callback route details. It wraps these into a `Manifest` object. The result is a complete description of what this extension contributes to the system.

**Call relations**: When the host asks this extension what it provides, `manifest()` assembles the answer. In doing so, it creates `SkillSpec` entries for the skill folders, a `ToolDef` for `connect_github`, a `RouteSpec` for the GitHub installation callback, and finally the `Manifest` that contains them all.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).


### `extensions/documents/ufo_ext_documents/manifest.py`

`config` · `startup or extension discovery`

This file is like a packing list for the documents extension. The extension contains several “skills,” which are reusable instruction-and-tool bundles stored in folders. Each skill teaches the agent how to do a particular kind of document work, such as creating a Word document, editing a PowerPoint deck, reading PDFs, reviewing office files, or building a shared visual theme.

The file gives the extension a name and version, points to the folder where its skills live, and lists the exact skill folder names that belong to this pack. When the larger UFO system asks this extension what it offers, the `manifest` function returns a `Manifest` object. That object contains one `SkillSpec` for each skill path, so the skill loader can later parse those folders and make the skills available.

This matters because the system should not have to guess what is inside the extension. Without this manifest, the document skills might exist on disk but never be registered, so the agent would not know how to load them. The dependency relationships mentioned in the file comment also matter: some document skills rely on shared design foundations, so loading them can bring along the visual baseline they need.

#### Function details

##### `manifest`  (lines 28–33)

```
def manifest() -> Manifest
```

**Purpose**: This function builds and returns the official description of the documents extension. The system uses it to learn the extension name, version, and which skill folders should be registered.

**Data flow**: It starts with the constants in this file: the extension name, version, root skills folder, and list of skill names. For each skill name, it creates a `SkillSpec`, which is a small description pointing to that skill’s folder. It then gathers those skill descriptions into a `Manifest` and returns it to the caller.

**Call relations**: When the extension is being discovered, the wider system calls `manifest` to ask, “What do you provide?” In response, this function creates `SkillSpec` entries for the listed folders and hands them to `Manifest`, which packages everything into the form the UFO skill loader expects.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/research/ufo_ext_research/manifest.py`

`config` · `startup / extension loading`

This file does not perform research itself. Instead, it tells the larger system what pieces belong to the research pack and what must be available for those pieces to work. Think of it like the label on a toolbox: it lists the tools inside, the instructions to include, and the outside supplies needed before anyone opens it.

At load time, it reads a Markdown prompt section from `prompts/web_section.md`. That text becomes the “web” prompt section, meaning it can be added to the agent’s instructions when web research is available. It also points to two skill folders, `research-assistant` and `research-report`, which the agent can load when needed.

The `manifest()` function gathers these declarations into a `Manifest` object. That object says: this extension is named `research`, it has a version, it contributes several research tools, it provides two research-focused subagent profiles, it adds one prompt section, and it exposes two skills.

One important detail is `requires=("search_providers",)`. The research pack does not own search credentials itself. It depends on a separately configured search backend. This makes the system fail early during startup if research is enabled without search support, instead of failing later when the first search is attempted.

#### Function details

##### `manifest`  (lines 27–36)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the official declaration for the research extension. The system uses this declaration to know which research tools, subagents, prompt text, and skills should become available.

**Data flow**: It starts with constants defined in this file, plus imported research tools and subagent profiles. It wraps the web prompt text in a `PromptSection`, turns each skill folder path into a `SkillSpec`, and then packages everything into a `Manifest`. The result is a single object that describes the whole research pack and its requirement for a configured search provider.

**Call relations**: When the extension system asks this file what it provides, `manifest` is the answer. Inside that answer-building step, it creates a `PromptSection` for the web instructions, creates `SkillSpec` entries for the research skills, and finally creates the `Manifest` object that the rest of the application can load.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Connector ecosystems
These files register external-service connector frameworks, credentialed integrations, MCP tools, shared source registries, and YC-specific data access.

### `extensions/composio/ufo_ext_composio/manifest.py`

`config` · `startup`

This file is the extension’s “front desk sign.” When UFO loads the Composio extension, it calls this manifest to learn what connectors exist and how people should connect to them. Composio is an outside service that can run tools for many apps, such as GitHub, while keeping each user’s account tokens on Composio’s servers instead of inside this deployment.

The file builds one shared ComposioBroker, which is the object UFO uses when it needs to create or use a Composio connection. It also builds a ComposioRequestForwarder, used when command-line credentials need to be forwarded safely to Composio. For each explicitly listed connector in CONNECTORS, it creates a ConnectorProvider. That provider includes the OAuth login description, a human-facing label, transfer host rules, and, when needed, command-line credential settings. OAuth means “sign in by sending the user to the provider and receiving permission back,” rather than asking for a password directly.

Most Composio toolkits do not need to be listed one by one. Instead, a ComposioResolver can recognize them by their slug, like a catalog lookup. Finally, the manifest exposes one GET route for the browser consent step, so the user can be redirected through the connect bridge during login.

#### Function details

##### `manifest`  (lines 24–53)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the Manifest object that describes this extension to UFO. Someone would use it when loading the extension so the host system knows which Composio connectors, login route, broker, and resolver to install.

**Data flow**: It starts with fixed module settings such as the extension name, version, authorization header name, connector list, and OAuth route path. It creates a shared ComposioBroker and request forwarder, then turns each configured connector spec into a ConnectorProvider with OAuth details and optional command-line credential forwarding. It returns a Manifest containing those providers, the general ComposioResolver for catalog-style connector lookup, and the browser route used during OAuth login.

**Call relations**: During extension startup, the host calls this function to get the extension’s registration package. Inside that package, it creates the broker, OAuth providers, connector providers, resolver, route spec, and optional CLI credentials, so later connection requests and OAuth callbacks can be routed to the right Composio pieces.

*Call graph*: 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, items).


### `extensions/connectors/ufo_ext_connectors/manifest.py`

`config` · `extension load`

This file declares the public shape of the connectors extension. In this project, a manifest is like a label on a plugin box: it names the plugin, gives its version, and lists the things the main system should make available when the plugin is loaded.

The connectors extension is not tied to one specific outside service. Instead, it exposes a common tool surface for all connector providers that other broker extensions may register. That means the tools here are generic actions such as listing, describing, searching, and executing connector capabilities across the whole workspace.

The file also registers two object types used by the connector system: one for a connection and one for a connector grant. These describe the kinds of structured data the extension can place into the larger system.

Finally, it reads a Markdown prompt section from disk. That prompt text is added under the name "external_tools" so the assistant or runtime can explain connector behavior in its instructions. Without this file, the host would not know this extension’s name, version, tools, objects, or prompt guidance, so the connector feature would not be properly advertised or wired into the system.

#### Function details

##### `manifest`  (lines 21–28)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the manifest object for the connectors extension. The host uses this to discover the extension’s name, version, available connector tools, connector data objects, and prompt instructions.

**Data flow**: It uses constants already prepared in the file: the extension name and version, the connector tool list, the connector object definitions, and the prompt text read from the Markdown file. It wraps the prompt text in a prompt section, then packages everything into a Manifest object and returns it. It does not modify outside state.

**Call relations**: When the extension system asks this file what it provides, this function is the answer. It creates a PromptSection for the connector guidance text, then creates the Manifest that the host can load to expose the connector tools and objects to the rest of the application.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/keyed_connectors/ufo_ext_keyed_connectors.py`

`config` · `startup / extension manifest load`

Some outside services cannot be connected through an account broker because the user already owns a plain API key. This file gives those services a safe path into the system. Instead of letting the sandbox see the real secret, it creates credential slots that a workspace admin can fill privately. The sandbox receives only a harmless placeholder value, called a sentinel, and the outbound network proxy swaps that placeholder for the real key only when the request is going to the approved API host.

Think of it like giving a courier a sealed envelope instead of the key itself. The courier can deliver it to the right front desk, but cannot open or copy what is inside.

The main table is `KEYED_PROVIDERS`. Each row describes one provider: its name, the HTTP headers where its keys belong, the environment variable names the sandbox should use, and either one fixed API host or a fixed list of allowed hosts. Datadog is currently declared here, including its different regional API hosts. If a provider has multiple possible hosts, the user must choose one from the approved list, so the system never accepts an arbitrary hostname.

The `manifest` function turns these provider rows into a `Manifest`, which is the extension’s public declaration. That manifest includes the credential slots and the prompt text explaining how agents should request credentials and call the provider safely.

#### Function details

##### `KeyedProvider.__post_init__`  (lines 69–78)

```
def __post_init__(self) -> None
```

**Purpose**: This checks that each provider row is written in a safe and complete way. A provider must either have one fixed host or a fixed list of possible hosts, but not both and not neither.

**Data flow**: After a `KeyedProvider` is created, it reads its own fields. If the host information is contradictory or incomplete, it stops creation by raising an error. If everything is valid, it returns nothing and leaves the provider object ready to use.

**Call relations**: This runs automatically when each `KeyedProvider` row is constructed, such as the Datadog entry in `KEYED_PROVIDERS`. It protects later steps, like building credential slots, from working with an unsafe or unclear provider definition.


##### `KeyedProvider.target_host`  (lines 81–90)

```
def target_host(self) -> str | HostChoice
```

**Purpose**: This decides what API host a provider’s secrets may be sent to. For a simple provider it returns the fixed host; for a provider with regional sites it builds a controlled host choice.

**Data flow**: It reads the provider’s `host`, `sites`, `host_env`, and site description. If there is one fixed host, that host string comes out. If there are several approved hosts, it creates a `HostChoice` describing the allowed options, the default choice, the credential slot used to store the choice, and the environment variable that will expose it.

**Call relations**: The `slots` and `usage` methods call on this property when they need to know where requests may go. When there are multiple allowed sites, it hands off to `HostChoice` so the manifest can represent a safe user-selected host.

*Call graph*: 1 external calls (__init__).


##### `KeyedProvider.slots`  (lines 92–110)

```
def slots(self) -> tuple[CredentialSlot, ...]
```

**Purpose**: This turns one provider row into the credential slots the workspace can fill. Each slot says what secret is needed and exactly how the proxy should inject it into outgoing requests.

**Data flow**: It reads the provider’s secrets and resolved target host. For each secret, it creates a `CredentialSlot` with a name, a user-facing description, and an `InjectionTarget` that says the approved host, HTTP header, sentinel value, sandbox environment variable, and request dimension. If the provider also needs the user to choose a host, it adds one extra credential slot for that host choice. The result is a tuple of credential slots.

**Call relations**: The top-level `manifest` function gathers the slots produced by every provider. Inside this method, `InjectionTarget` describes the safe wire-level substitution, and `CredentialSlot` packages that instruction as something the wider system can request, store, and report.

*Call graph*: 2 external calls (__init__, __init__).


##### `KeyedProvider.usage`  (lines 112–122)

```
def usage(self) -> str
```

**Purpose**: This writes a short help line showing how an agent should call the provider’s REST API from the sandbox. It names the needed slots and gives a `curl` example using environment variables rather than real secrets.

**Data flow**: It reads the provider name, label, secrets, headers, environment variable names, and target host. It formats those details into one human-readable bullet. If the host is chosen from a list, it uses the host environment variable in the example and includes the host-choice slot in the named slots.

**Call relations**: The file uses this when building `SECTION_BODY`, the prompt text included in the extension manifest. That text later guides agents so they request credentials properly and avoid asking users to paste secrets into chat.


##### `manifest`  (lines 188–194)

```
def manifest() -> Manifest
```

**Purpose**: This is the extension’s public declaration to the host system. It says the extension’s name and version, lists all credential slots, and supplies the prompt guidance agents should read.

**Data flow**: It reads constants such as the extension name and version, walks through every provider in `KEYED_PROVIDERS`, and collects the slots returned by each provider. It also wraps the prepared help text in a `PromptSection`. It returns a `Manifest` object containing all of that information.

**Call relations**: The extension loader calls this when it needs to discover what this extension contributes. It hands provider-derived credential declarations to `Manifest`, and hands the instructional text to `PromptSection`, so the rest of the system can request credentials and guide agents consistently.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/mcp/ufo_ext_mcp.py`

`io_transport` · `tool discovery and tool call handling`

MCP means Model Context Protocol: a standard way for an outside service to publish tools that an AI agent can discover and use. This file is the bridge between UFO’s tool system and those outside MCP servers. Without it, a workspace could not plug in its own MCP server and have the agent browse or use its tools.

The file exposes two agent-facing tools. The first, list_mcp_tools, connects to a named server and returns either a short catalog of available tools or the full input schema for selected tools. This two-step design matters because a server may publish very large tool descriptions, and a single response can become too big for the agent loop. The second, call_mcp_tool, sends arguments to one chosen tool and returns either structured JSON data or plain text.

The server list comes from a credential slot named mcp_servers. Each configured server has a URL and, optionally, an auth token. The file validates that URLs are HTTP or HTTPS, adds the auth token as a Bearer token when needed, and uses fastmcp’s HTTP client to do the MCP handshake and calls.

Because MCP servers are external, their output is treated as untrusted content. The code also refuses requests or responses over one mebibyte, like a clerk rejecting an overstuffed envelope instead of silently cutting pages out.

#### Function details

##### `McpServer._http_url`  (lines 77–80)

```
def _http_url(cls, value: str) -> str
```

**Purpose**: This validates that a configured MCP server address uses HTTP or HTTPS. It prevents the extension from trying to connect to unsupported or surprising kinds of addresses.

**Data flow**: It receives a URL string from the server configuration. It checks the string against the allowed web-address pattern. If the URL is acceptable, the same string continues into the configuration model; if not, validation fails with a clear message.

**Call relations**: This is used automatically by the McpServer data model when workspace credentials are parsed. It acts before any network client is built, so later functions such as mcp_client only receive server records with web URLs.


##### `mcp_client`  (lines 115–121)

```
def mcp_client(server: McpServer) -> Client
```

**Purpose**: This builds a ready-to-use MCP client for one configured server. Someone uses it when they need to list tools or call a tool on that server.

**Data flow**: It takes an McpServer object containing a URL and optional auth token. It turns the token into an Authorization header when present, creates a Streamable HTTP transport for the URL, and wraps that transport in a fastmcp Client with a timeout. The result is a client object that can speak MCP over HTTP.

**Call relations**: _list_mcp_tools and _call_mcp_tool call this after _server has found the correct server configuration. It hands off the low-level HTTP and MCP protocol work to fastmcp.Client and StreamableHttpTransport, so the rest of the file can ask for tools or call a tool without manually handling sessions and protocol details.

*Call graph*: called by 2 (_call_mcp_tool, _list_mcp_tools); 2 external calls (Client, StreamableHttpTransport).


##### `_server`  (lines 124–136)

```
async def _server(ctx: ToolContext, name: str) -> McpServer
```

**Purpose**: This looks up one named MCP server from the workspace’s stored credentials. It makes sure the tool call is aimed only at a server the workspace actually configured.

**Data flow**: It receives the current tool context and a server name. It reads the mcp_servers credential value from the extension context, parses it as validated JSON, and searches for the requested name. It returns the matching McpServer object, or raises an error if the extension context is missing, the credential cannot be used, or the name is not present.

**Call relations**: _list_mcp_tools and _call_mcp_tool both call this before opening any MCP connection. It is the gatekeeper between an agent request that says “use server X” and the network client that can actually contact server X.

*Call graph*: called by 2 (_call_mcp_tool, _list_mcp_tools).


##### `_list_mcp_tools`  (lines 139–160)

```
async def _list_mcp_tools(ctx: ToolContext, args: ListMcpToolsInput) -> ToolResult
```

**Purpose**: This is the implementation of the agent-facing tool that discovers what an MCP server can do. It either returns a compact catalog of all tools or detailed schemas for a selected few tools.

**Data flow**: It receives the tool context and parsed input containing a server name and optional tool names. It looks up the server, connects to it, asks the server for its tool list, and then chooses the response shape. With no tool names, it converts every tool into a small catalog entry. With tool names, it verifies those names exist and returns full schema entries only for them. The output is a ToolResult containing JSON text, or an error if requested names are unknown or the response is too large.

**Call relations**: This function is registered in manifest as list_mcp_tools. During a tool-discovery request, it calls _server to resolve credentials, mcp_client to connect, _catalog_entry for browseable summaries, _schema_entry for full selected definitions, and _json_result or _bounded_schemas to package the answer safely.

*Call graph*: calls 6 internal fn (_bounded_schemas, _catalog_entry, _json_result, _schema_entry, _server, mcp_client).


##### `_idempotent`  (lines 163–165)

```
def _idempotent(tool: McpTool) -> bool
```

**Purpose**: This reads whether an MCP tool claims it is safe to repeat without changing anything extra. In plain terms, it tells the agent whether retrying the tool is expected to be like re-reading a page rather than placing a second order.

**Data flow**: It receives an MCP tool object. It checks the tool’s optional annotations for an idempotentHint flag. It returns true only when that flag is present and true; otherwise it returns false.

**Call relations**: _catalog_entry and _schema_entry call this while preparing tool information for the agent. Its answer becomes part of both the short catalog view and the full schema view.

*Call graph*: called by 2 (_catalog_entry, _schema_entry).


##### `_catalog_entry`  (lines 168–184)

```
def _catalog_entry(tool: McpTool) -> JsonValue
```

**Purpose**: This turns one MCP tool definition into a small, browseable summary. It is used so the agent can see what tools exist without being flooded by full JSON schemas.

**Data flow**: It receives one MCP tool object. It reads the tool name, description, input schema, required fields, and idempotency hint. It trims the description to a short summary, extracts the parameter names and required parameter names, and returns a JSON-friendly dictionary with those facts.

**Call relations**: _list_mcp_tools calls this for each tool when the agent is browsing a server without asking for full schemas. It relies on _summary to shorten the description and _idempotent to include the repeat-safety hint.

*Call graph*: calls 2 internal fn (_idempotent, _summary); called by 1 (_list_mcp_tools).


##### `_summary`  (lines 187–193)

```
def _summary(description: str) -> str
```

**Purpose**: This makes a long MCP tool description short enough for a catalog. It keeps the opening idea instead of returning a whole docstring or argument block.

**Data flow**: It receives a description string. It strips surrounding whitespace, takes the first line, keeps text up to the first sentence break, and caps it at the configured maximum length. It returns that shortened string.

**Call relations**: _catalog_entry calls this while building the compact tool list. It helps keep list_mcp_tools useful when a server has many tools with long descriptions.

*Call graph*: called by 1 (_catalog_entry).


##### `_schema_entry`  (lines 196–202)

```
def _schema_entry(tool: McpTool) -> JsonValue
```

**Purpose**: This turns one MCP tool definition into the detailed form the agent needs before calling that tool. It includes the full input schema so the agent can use the exact parameter names and shapes.

**Data flow**: It receives one MCP tool object. It copies the tool name, full description, input schema, and idempotency hint into a JSON-friendly dictionary. That dictionary becomes part of the tool result returned to the agent.

**Call relations**: _list_mcp_tools calls this when the agent asks for full schemas for specific tool names. It calls _idempotent so the detailed view carries the same repeat-safety information as the catalog.

*Call graph*: calls 1 internal fn (_idempotent); called by 1 (_list_mcp_tools).


##### `_call_mcp_tool`  (lines 205–217)

```
async def _call_mcp_tool(ctx: ToolContext, args: CallMcpToolInput) -> ToolResult
```

**Purpose**: This is the implementation of the agent-facing tool that invokes one tool on an MCP server. It sends the user-supplied arguments to the external server and turns the server’s answer into a UFO ToolResult.

**Data flow**: It receives the tool context and parsed input containing the server name, tool name, and argument object. It looks up the server, serializes the arguments to make sure the request is not over the byte limit, connects to the server, and calls the named MCP tool. If the MCP server reports an error, it returns an error ToolResult with bounded text. If the call succeeds and structured JSON content is available, it returns that JSON. Otherwise it joins any text blocks and returns them under a text field.

**Call relations**: This function is registered in manifest as call_mcp_tool. It calls _server before network access, mcp_client for the actual MCP connection, _joined_text when it needs plain text from an MCP response, _bounded to enforce response size, and _json_result to package successful output.

*Call graph*: calls 5 internal fn (_bounded, _joined_text, _json_result, _server, mcp_client); 4 external calls (__init__, __init__, __init__, dumps).


##### `_joined_text`  (lines 220–221)

```
def _joined_text(content: list[object]) -> str
```

**Purpose**: This collects plain text from an MCP response that may contain several content blocks. It ignores non-text blocks and joins the text blocks into one readable string.

**Data flow**: It receives a list of response content objects. It scans the list, keeps only MCP TextContent blocks, pulls out their text, and joins those pieces with newline characters. It returns the combined text string.

**Call relations**: _call_mcp_tool uses this when an MCP call fails with text details, or when a successful call has no structured JSON result. It provides the plain-text fallback that can be placed into a ToolResult.

*Call graph*: called by 1 (_call_mcp_tool).


##### `_bounded`  (lines 224–227)

```
def _bounded(text: str) -> str
```

**Purpose**: This enforces the maximum allowed size for text returned through this extension. It fails loudly instead of silently cutting off data, which avoids misleading the agent with partial results.

**Data flow**: It receives a text string. It measures the text after encoding it as bytes. If the text is within the response byte limit, it returns the original string; if it is too large, it raises McpError.

**Call relations**: _call_mcp_tool calls this for error text from MCP tools, and _json_result calls it for serialized JSON results. It is the shared safety check just before data leaves this file as a tool result.

*Call graph*: called by 2 (_call_mcp_tool, _json_result); 1 external calls (__init__).


##### `_bounded_schemas`  (lines 230–247)

```
def _bounded_schemas(payload: dict[str, JsonValue], tools: int) -> ToolResult
```

**Purpose**: This checks whether a request for multiple full tool schemas is too large to be useful. It nudges the agent to ask for fewer schemas instead of receiving a partial, offloaded result that may hide the exact details it needs.

**Data flow**: It receives a JSON-like payload and the number of tool schemas inside it. If there is more than one schema and the serialized payload exceeds the listing-size limit, it raises a ValueError telling the agent to ask for fewer tools. Otherwise it passes the payload to _json_result and returns the resulting ToolResult.

**Call relations**: _list_mcp_tools calls this when the agent asks for full schemas by name. It sits between schema collection and _json_result, making the two-step discovery flow practical for large MCP servers.

*Call graph*: calls 1 internal fn (_json_result); called by 1 (_list_mcp_tools); 1 external calls (dumps).


##### `_json_result`  (lines 250–251)

```
def _json_result(payload: dict[str, JsonValue]) -> ToolResult
```

**Purpose**: This packages a JSON-like dictionary as a UFO tool result. It is the common exit path for successful JSON responses from listing tools or calling tools.

**Data flow**: It receives a payload dictionary. It serializes the payload to JSON text, checks the text with _bounded, wraps it in TextContent, and returns a ToolResult containing that text. If the serialized JSON is too large, the size check raises an error instead.

**Call relations**: _list_mcp_tools uses this for compact catalogs, _bounded_schemas uses it after schema-size checks, and _call_mcp_tool uses it for successful MCP call results. It centralizes the final formatting step for JSON output.

*Call graph*: calls 1 internal fn (_bounded); called by 3 (_bounded_schemas, _call_mcp_tool, _list_mcp_tools); 3 external calls (__init__, __init__, dumps).


##### `manifest`  (lines 254–285)

```
def manifest() -> Manifest
```

**Purpose**: This declares the extension to the UFO platform: its name, version, tools, input models, handlers, and required credential slot. It is how the rest of the system learns that this MCP tool pack exists.

**Data flow**: It takes no input. It builds a Manifest containing two ToolDef entries, one for listing MCP tools and one for calling an MCP tool, plus a CredentialSlot describing the mcp_servers configuration. It returns that Manifest to the extension loader.

**Call relations**: The platform calls this when loading the extension. The returned manifest connects the public tool names to _list_mcp_tools and _call_mcp_tool, and tells the platform that the workspace must provide the mcp_servers credential data.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/pipedream/ufo_ext_pipedream/manifest.py`

`config` · `startup / extension load`

This file is the extension’s “menu card” for the rest of the application. Pipedream is used here as a broker: it keeps users’ account tokens on Pipedream’s servers, while this system gets a safe way to trigger actions and sync data without directly storing those secrets.

The file builds a manifest, which is a structured description of an extension. For every connector listed in the Pipedream connector catalog, it creates a connector entry with three main parts: an OAuth provider, which knows how to start the user consent flow; a human-facing label, so the connector can be shown clearly in the product; and a shared broker, which knows how to talk to Pipedream when the connector is used.

It also registers one browser route for the OAuth bridge. OAuth is the common “sign in and grant permission” flow used by services like Google. After the user approves access, the browser needs a route to return through, and this file declares that route.

Without this file, the wider system would not know that these Pipedream connectors exist, how to display them, how to begin their authorization flow, or which route should receive the consent redirect.

#### Function details

##### `manifest`  (lines 23–45)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension manifest: the formal description of the Pipedream extension’s connectors and OAuth callback route. The system uses this to make Pipedream-backed connectors available through the normal connector machinery.

**Data flow**: It starts with the connector catalog imported as CONNECTORS and creates one shared PipedreamBroker. For each catalog entry, it turns the provider details into a ConnectorProvider with an OAuth provider, label, broker, and allowed transfer hosts. It also creates a RouteSpec for the OAuth bridge route. The result is a Manifest object containing the extension name, version, connector list, and route list.

**Call relations**: When the extension is loaded, the system calls this function to ask what the extension contributes. Inside, it creates the PipedreamBroker, loops through CONNECTORS.items(), builds PipedreamOAuthProvider and ConnectorProvider objects for each connector, creates the OAuth RouteSpec, and hands everything to Manifest so the rest of the application can register the connectors and route.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, __init__, items).


### `extensions/sources/ufo_ext_sources/registry.py`

`config` · `startup / import time`

This file works like a phone book for external data sources. Each connector class knows how to talk to one outside service, such as Airtable, Gmail, Jira, or Salesforce. Instead of searching the codebase at runtime to discover available connectors, this file imports them explicitly and lists them in one place. That makes startup simpler and more predictable: adding a new source means adding its connector to this registry.

The important output is `CONNECTORS`, a dictionary that maps a connector's `name` to the connector class itself. For example, when a saved source row says its backend is a certain name, the sync system can use this registry to find the matching connector class and start pulling data from that service. The file also defines `SOURCE_KIND`, which labels these connectors as sources.

The small helper `_connector_registry` builds the dictionary and checks for duplicate names. That check matters because the connector name is used in several places: as the backend name, as the credential slot, and as part of how source bindings are named. If two connectors reused the same name, the system could silently pick the wrong service. Instead, this file fails early with a clear error.

#### Function details

##### `_connector_registry`  (lines 61–69)

```
def _connector_registry(connector_types: tuple[type[Connector], ...]) -> dict[str, type[Connector]]
```

**Purpose**: Builds the connector lookup table from a list of connector classes. It also protects the system from two connectors using the same name, which would make source selection ambiguous.

**Data flow**: It receives a tuple of connector classes. For each class, it reads the class's `name`, uses that name as the dictionary key, and stores the class as the value. If a name has already been used, it stops and raises an error; otherwise it returns the completed name-to-connector dictionary.

**Call relations**: This helper is called once while the module is being imported, to create the module-level `CONNECTORS` registry. The rest of the source-sync system can then use `CONNECTORS` later to look up the connector class that matches a source backend name.


### `extensions/yc/ufo_ext_yc/manifest.py`

`config` · `extension load and onboarding`

Think of this file as the YC extension’s front desk sign and setup checklist. It does not do the actual YC reading or indexing itself. Instead, it describes those abilities to the larger UFO system in a standard format called a manifest, which is a package of metadata and wiring instructions.

The file defines three tools. `yc_auth` lets a workspace admin connect a YC account through a browser approval flow. `yc_read` gives read-only access to YC and Bookface information through an authenticated command-line client. `yc_index` saves a bounded YC or Bookface search into shared workspace memory so it can be refreshed and reused. The descriptions are intentionally specific because they guide both the system and the assistant about when each tool is safe to use.

It also defines one credential slot, which is a named place where encrypted YC credentials are stored. The credential stays outside chat and outside the sandbox. For shared knowledge, the file registers YC guidance collections during onboarding, using a source backend that knows how to build a `YcSource` from stored credentials. Finally, it points the system to a directory of YC research skills. In short, this file is the contract between the YC extension and the host application.

#### Function details

##### `setup_sources`  (lines 77–84)

```
async def setup_sources(ctx: ExtensionContext) -> None
```

**Purpose**: This function sets up the shared YC guidance sources for a workspace during onboarding. It registers each known YC guidance collection so the system can later search or refresh that shared knowledge.

**Data flow**: It receives an extension context, which is the object the host system gives extensions so they can register their pieces. It reads the list of YC guidance collections, creates a `YcSourceConfig` for each collection, and asks the context to register that source under the shared workspace subject. Nothing is returned; the result is that the host system now knows these YC sources exist.

**Call relations**: This function is handed to an onboarding step in `manifest`. When that onboarding step runs, the host calls `setup_sources`; `setup_sources` then creates source configurations and passes them to `ExtensionContext.register_source` so the main system can include the YC guidance sources in shared workspace memory.

*Call graph*: calls 1 internal fn (register_source); 1 external calls (__init__).


##### `manifest`  (lines 87–110)

```
def manifest() -> Manifest
```

**Purpose**: This function builds and returns the YC extension manifest, which is the complete description of what the extension adds to the system. The host calls it to discover the extension’s tools, credential requirements, source provider, onboarding step, and skill files.

**Data flow**: It starts from constants and tool definitions already declared in the file. It creates a credential slot for the shared YC identity, lists the authentication, reading, and indexing tools, defines how to build a YC source from credentials, attaches the onboarding step that registers shared sources, and points to the skill directory. It returns one `Manifest` object containing all of that information.

**Call relations**: This is the main entry the host uses when loading the extension. Inside it, the file creates `CredentialSlot`, `SourceProvider`, `OnboardingStep`, and `SkillSpec` objects, then packages them into a `Manifest`. The onboarding step it creates refers back to `setup_sources`, which will be called later when the workspace is being prepared.

*Call graph*: 5 external calls (__init__, __init__, __init__, __init__, __init__).


### Persistent runtime services
These manifests register cross-turn services that recall or learn memory and schedule recurring or delayed work.

### `extensions/memory/ufo_ext_memory/manifest.py`

`orchestration` · `startup registration, then active during tool calls, prompt hooks, page-change handling, and scheduled jobs`

This file is the front desk for the memory extension. It tells the host system, “Here are the memory tools, here are the events I listen to, here are the scheduled jobs I need, and here is how other code can search memory.”

The extension has two direct tools for the agent. `memory_search` looks up remembered facts and related source-document snippets. `memory_update` writes a durable fact, such as a user preference or project detail, so it can be recalled later.

It also works automatically. When a user submits a prompt, `recall_hook` tries to find relevant memory and inject it into the model’s context before the answer is generated. This is deliberately best-effort: if memory search is slow or broken, the user’s turn is allowed to continue instead of failing.

The file also connects page changes to memory. When source pages change, one hook indexes them for search, and another uses a model to derive durable facts from them. Separate scheduled jobs later index newly written memories and merge older related facts into broader summaries. Think of it like a library system: new notes are filed, pages are scanned, useful facts are extracted, and old related notes are summarized so the shelves stay useful.

#### Function details

##### `_date_bound`  (lines 149–160)

```
def _date_bound(value: str | None, *, end: bool) -> datetime | None
```

**Purpose**: Turns an optional date string from the search tool into a timezone-aware UTC date-time boundary. It lets users search memory within a date window while treating a plain end date as the whole day, not just midnight.

**Data flow**: It receives a string such as `2026-01-31` or a full date-time, plus a flag saying whether this is an end boundary. If the value is missing, it returns nothing. If the value is present, it parses it, assumes UTC when no timezone is given, and for a plain end date moves the boundary to the next midnight. The result is a `datetime` value used to limit search results.

**Call relations**: `memory_search_handler` calls this before running a memory search. If parsing fails, that error becomes a recoverable tool error for the model instead of silently searching the wrong time window.

*Call graph*: called by 1 (memory_search_handler); 2 external calls (fromisoformat, timedelta).


##### `MemorySearchService.search`  (lines 169–239)

```
async def search(self, queries: tuple[str, ...], reader: SourceReader, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: Runs the shared memory search workflow used by the memory tool and by other extensions. It searches both stored memory items and indexed source pages, then merges the results fairly across multiple focused queries.

**Data flow**: It receives one to three search queries, a source reader that says whose memory and sources may be read, and optional start and end dates. It asks the memory store to recall durable memory items and to search source snippets for each query in parallel. It then interleaves the per-query answers, removes duplicates, limits the result count, and returns a tuple of `MemoryMatch` objects that point either to memory records or source pages.

**Call relations**: The search tool creates this service when the agent asks to search memory. The manifest also exposes this service as the default memory search provider so other parts of the system can use the same behavior instead of inventing their own search flow.

*Call graph*: 5 external calls (__init__, __init__, gather, zip_longest, store_for).


##### `MemorySearchService.listable_kinds`  (lines 241–244)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: Reports which memory item classes can be listed or filtered. It keeps the answer tied to the central item-class type so new classes do not need to be added in two places.

**Data flow**: It reads the allowed values from `ItemClass` and returns them as a tuple of strings. It does not change storage or perform a database lookup.

**Call relations**: This supports consumers that browse memory by kind. It complements `list_recent`, which uses these kinds as filters when showing recent memory items.

*Call graph*: 1 external calls (get_args).


##### `MemorySearchService.list_recent`  (lines 246–293)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: Returns a page of the newest live memory items for a set of readable subjects. This is for browsing memory directly, not for semantic search.

**Data flow**: It receives readable subjects, a maximum number of items, an optional set of item kinds, and an optional paging cursor. It builds a database query for non-superseded memory rows in the current workspace, applies the kind filter if present, and uses the shared listing helper to fetch one page. It returns a `ListingPage` whose entries are `MemoryMatch` objects with text, kind, reference, and creation time.

**Call relations**: Other listing or surface code can call this when a user wants to browse recent memories. It uses the project’s standard paging helpers so memory listings behave like other paged lists in the system.

*Call graph*: 3 external calls (select, page_of, page_query).


##### `match_line`  (lines 296–303)

```
def match_line(match: MemoryMatch) -> str
```

**Purpose**: Formats one memory search result into a readable line of text for the agent. It includes the result type, snippet, object reference, and date when available.

**Data flow**: It receives a `MemoryMatch`. It starts with a bullet containing the match kind and text. If the match has a reference, it appends that reference and, when present, the creation date. It returns the final string.

**Call relations**: `memory_search_handler` uses this after search results come back, turning structured matches into the plain text shown to the model.

*Call graph*: called by 1 (memory_search_handler).


##### `memory_search_handler`  (lines 306–326)

```
async def memory_search_handler(ctx: ToolContext, args: MemorySearchInput) -> ToolResult
```

**Purpose**: Implements the `memory_search` tool that an agent can call during a conversation. It searches durable memory and source snippets, then returns a compact text summary of what was found.

**Data flow**: It receives the tool context and validated search arguments. It checks that extension context is available, parses the optional date window, builds a source reader for the current request, and calls `MemorySearchService.search`. If no matches are found, it returns `No matching memory.` Otherwise it formats each match with `match_line` and returns the lines as tool output.

**Call relations**: The manifest registers this as the handler for the `memory_search` tool. It sits between the model’s tool call and the lower-level search service, translating user-facing arguments into the service call and translating results back into tool text.

*Call graph*: calls 3 internal fn (source_reader, _date_bound, match_line); 3 external calls (__init__, __init__, __init__).


##### `memory_update_handler`  (lines 329–343)

```
async def memory_update_handler(ctx: ToolContext, args: MemoryUpdateInput) -> ToolResult
```

**Purpose**: Implements the `memory_update` tool that records a durable memory item. Agents use it when they learn something persistent about the user or shared conversation audience.

**Data flow**: It receives the tool context and validated memory-write arguments. It finds the effective audience for the current conversation, builds a `MemoryWrite` record with the body, class, kind, confidence, and optional source reference, and commits it to the memory store. It returns a short confirmation naming the subject that received the memory.

**Call relations**: The manifest registers this as the handler for the `memory_update` tool. Later, indexing jobs and recall searches can find the item this handler writes.

*Call graph*: 4 external calls (__init__, __init__, __init__, store_for).


##### `recall_hook`  (lines 346–388)

```
async def recall_hook(ctx: HookContext) -> HookOutcome
```

**Purpose**: Automatically adds relevant memory to a user turn before the model answers. It is designed to help without ever blocking the conversation if memory recall fails.

**Data flow**: It receives a hook context. If the event is not a user prompt, or there is no active turn, it returns nothing. Otherwise it determines which subjects may be recalled, builds a source reader, and asks the store to recall matching memories using the prompt text. It runs under a short timeout and catches all errors. Successful non-topic memories are logged and injected into the turn as a short `Relevant memory` block; failures or empty results return nothing.

**Call relations**: The manifest attaches this to the `user_prompt_submit` event. It runs before the model response, feeding useful remembered context forward, but it deliberately does not hand errors back in a way that would deny the user’s turn.

*Call graph*: 6 external calls (__init__, __init__, timeout, log, recall_subjects, store_for).


##### `index_memory`  (lines 391–400)

```
async def index_memory(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the scheduled job that indexes committed memory items so they can be searched by meaning. It requires both an index backend and an embedding backend, where an embedding is a numeric representation of text used for similarity search.

**Data flow**: It receives the extension context. It first checks that indexing and embedding services are wired. It then creates a `MemoryIndexer` with the index, embedder, database transaction function, text chunker, and page-state tracking, and runs it. The output is not a return value; the important change is that memory items become indexed and searchable.

**Call relations**: The manifest registers this as the `memory_index` scheduled job. Candidate selection comes from `_items_awaiting_index`, so the job is aimed at workspaces with memory items that still need embeddings.

*Call graph*: 2 external calls (__init__, __init__).


##### `index_pages`  (lines 403–419)

```
async def index_pages(ctx: HookContext) -> HookOutcome
```

**Purpose**: Responds to source-page changes by indexing page content for later memory search. It turns changed pages into searchable chunks and keeps a mirror record for memory-related page lookup.

**Data flow**: It receives a hook context. If the payload is not a page-change batch, it returns nothing. Otherwise it checks that index and embedding services exist, builds a `PageIndexer`, and applies it to the changed pages in the batch. It returns nothing because its effect is stored in the index and database.

**Call relations**: The manifest attaches this to the `page_change` event. The core runner delivers batches and owns the cursor; this function processes each delivered batch for the memory extension’s search index.

*Call graph*: 2 external calls (__init__, __init__).


##### `derive_facts`  (lines 422–433)

```
async def derive_facts(ctx: HookContext) -> HookOutcome
```

**Purpose**: Responds to source-page changes by extracting durable facts from changed pages. This is how synced documents can become remembered facts rather than only searchable text.

**Data flow**: It receives a hook context. If the payload is not a page-change batch, it returns nothing. If no model service is available, it raises an error because facts cannot be safely derived. Otherwise it builds a `FactDeriver` with the memory store and model, applies it to the page changes, and lets that deriver write replacement facts and retire outdated page-derived facts.

**Call relations**: The manifest attaches this as a second `page_change` consumer, separate from page indexing. It intentionally has its own cursor path, so fact derivation can fail loudly without pretending pages were processed.

*Call graph*: 2 external calls (__init__, store_for).


##### `consolidate_memory`  (lines 436–444)

```
async def consolidate_memory(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the scheduled job that merges older related facts into broader semantic summaries. This keeps memory useful by reducing clutter while preserving the meaning of repeated or related facts.

**Data flow**: It receives the extension context. It checks that the embedding backend exists, then creates a `MemoryConsolidator` with embedding, database transaction access, workspace identity, and the optional model service. Running it may create summary memories and mark original facts as superseded.

**Call relations**: The manifest registers this as the `memory_consolidate` scheduled job. Its candidate workspaces are selected by `_consolidatable_workspaces`, which looks for enough old live facts to make consolidation worthwhile.

*Call graph*: 1 external calls (__init__).


##### `_items_awaiting_index`  (lines 447–452)

```
def _items_awaiting_index() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the database query that finds workspaces with memory items still missing embeddings. It helps the scheduler avoid running the memory indexing job where there is no indexing work to do.

**Data flow**: It creates a SQL query selecting distinct workspace IDs from memory items whose embedding digest is missing. It returns the query object, not the query results.

**Call relations**: `manifest` passes this query builder to `owner_candidates` for the memory indexing job. The scheduler can then choose job owners based on workspaces that actually have unindexed memory.

*Call graph*: 1 external calls (select).


##### `_consolidatable_workspaces`  (lines 455–471)

```
def _consolidatable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the database query that finds workspaces where memory consolidation could produce a useful cluster. It filters out places with too few facts, facts that are too new, page-derived facts, or already superseded facts.

**Data flow**: It calculates an age cutoff from the current UTC time and the minimum fact age. It builds a grouped SQL query for workspaces with at least the required number of old live fact memories. It returns the query object so the job scheduler can use it.

**Call relations**: `manifest` passes this query builder to `owner_candidates` for the consolidation job. This keeps the hourly job from running in workspaces that cannot yet form a valid consolidation cluster.

*Call graph*: 2 external calls (now, select).


##### `manifest`  (lines 474–549)

```
def manifest() -> Manifest
```

**Purpose**: Creates the extension declaration that the host system reads at startup. It is the single place where this memory extension advertises its tools, hooks, jobs, object type, skill files, search provider, and surface routes.

**Data flow**: It creates and returns a `Manifest` object. Inside that object it defines two tools, one memory object kind, three hooks, two scheduled jobs with candidate selectors, a skill directory, the default memory search provider, and the memory surface routes. It does not execute those features immediately; it describes how the system should wire and call them later.

**Call relations**: The host loads this function when discovering the extension. Everything else in this file becomes reachable through the returned manifest: tool calls go to the tool handlers, prompt and page events go to the hooks, scheduled work goes to the job handlers, and dependent extensions can build the memory search service.

*Call graph*: 8 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, owner_candidates).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/manifest.py`

`config` · `extension discovery and scheduled job execution`

This file is the extension’s “front desk.” When the application discovers extensions, it asks each one for a manifest, which is a plain declaration of what the extension provides. Here, the extension declares its name and version, then lists the features it wants the host to install.

The main feature is recurring scheduled tasks. The file registers a `scheduled_task` object kind, so agents can create, change, list, and delete scheduled tasks using the system’s generic object actions. It also registers a durable pause tool, which lets work stop and resume later without relying on a fragile in-memory sleep.

It also defines a clock-based runner job. Think of this like a mail carrier checking the mailbox at fixed times: the runner wakes up on a regular schedule and looks for due workspaces, rather than being triggered directly by each task row. When the job fires, `_run` builds a `ScheduledTaskRunner` with the current extension context and tells it to do the real work.

Finally, the manifest points to the task-scheduling skill files, which teach the agent how to schedule tasks, and declares that this extension depends on `memory_search` being available.

#### Function details

##### `_run`  (lines 28–29)

```
async def _run(ctx: ExtensionContext) -> None
```

**Purpose**: This is the job handler that runs when the scheduled-task runner job fires. It creates a `ScheduledTaskRunner`, giving it the current extension context, and starts the runner so due scheduled tasks can be found and invoked.

**Data flow**: It receives an `ExtensionContext`, which is the bundle of workspace-specific services and capabilities the extension is allowed to use. It passes that context into `ScheduledTaskRunner`, then waits for the runner to finish its run. It returns nothing directly; the useful result is that due scheduled work may be claimed and executed through the runner.

**Call relations**: The manifest registers `_run` as the handler for the recurring job. When the host’s job system decides this job should fire, it calls `_run`; `_run` then hands control to `ScheduledTaskRunner` by constructing it with the extension context.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 32–48)

```
def manifest() -> Manifest
```

**Purpose**: This function builds the extension manifest, which is the host application’s checklist of everything this extension contributes. It is used during extension loading so the host can register the tool, object type, recurring job, skill files, and dependency.

**Data flow**: It reads the constants in this file, such as the extension name, version, runner schedule, skill folder, and skill names. It creates a `JobSpec` for the recurring runner, asks `due_task_workspaces()` to choose which workspaces are candidates for that job, creates `SkillSpec` entries for the skill paths, and returns a complete `Manifest` object.

**Call relations**: The host calls `manifest` when it is discovering or loading this extension. Inside that setup step, `manifest` creates the `JobSpec`, `SkillSpec`, and final `Manifest`, and it uses `due_task_workspaces()` so the registered runner job is aimed only at workspaces that may have scheduled tasks due.

*Call graph*: 4 external calls (__init__, __init__, __init__, due_task_workspaces).
