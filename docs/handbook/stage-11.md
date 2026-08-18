# Credentialed connectors and external provider actions  `stage-11`

This stage is shared behind-the-scenes support for letting agents use outside services without handing them private keys. It comes into play when an agent needs Gmail, Slack, GitHub, Notion, or another provider during its normal work. The core connector contract defines how tools are found, called, and protected, while agent setup notices which agents still need a member to connect accounts and gives them wording to ask for that access.

The generic connector tools are the workbench: they search available tools, run them, move files in and out, and keep results small enough for chat. Connector objects make connected accounts visible as workspace items, with rules for who may share, revoke, or disconnect them.

Composio and Pipedream are two outside broker bridges. Their clients create consent links, check accounts, list actions, run actions, fetch files, and proxy web requests so real service tokens stay hidden. Their providers connect UFO’s account-linking flow to hosted approval pages. MCP support adds tools from workspace-configured MCP servers. Slack tools provide guided Slack setup and search. The package files simply make these modules importable.

## Files in this stage

### Connector contracts and setup
Core connector abstractions define safe credential routing and identify which agents need outside account connections.

### `core/src/ufo/agent_setup.py`

`domain_logic` · `turn handling`

An installed agent may need access to outside services, such as a provider account, before it can do its job. This file records that need as an AgentSetup: a list of connector provider names and human instructions for getting them connected. Think of it like a checklist taped to a new appliance: before it works, someone must plug in the required cables.

The main job here is to compare what an agent says it needs with what the workspace has already granted it. The pending_setup function reads the workspace database, finds shipped agents that declared setup requirements, then checks which provider connections have already been granted to each agent. It returns only the agents that are still missing something.

The setup_skill function turns that missing checklist into instructions the language model can use during a conversation. If the current agent is missing its own account grants, it gets told exactly what to ask the member for. If the current agent is the main agent, it can also be told about other agents that need setup, because the main agent can help grant accounts on their behalf. The skill only appears when a real member is speaking, because account connection requires member consent; background runs cannot grant it.

#### Function details

##### `pending_setup`  (lines 39–80)

```
async def pending_setup() -> tuple[tuple[UUID, str, AgentSetup], ...]
```

**Purpose**: Finds every installed agent in the current workspace that still lacks required account connections. It is used to build an up-to-date setup checklist instead of relying on a stored flag that could become wrong later.

**Data flow**: It reads the current workspace ID, then queries the database for agents that have setup requirements saved on their row. For those agents, it also reads the connector grants they already hold and the provider names for those connections. It compares required provider names with granted provider names, then returns a tuple of only the agents that are still missing at least one required provider.

**Call relations**: setup_skill calls this when deciding whether to show setup instructions on the current turn. pending_setup does the database work, then hands setup_skill a clean list of outstanding agent needs that can be turned into member-facing instructions.

*Call graph*: called by 1 (setup_skill); 4 external calls (__init__, select, workspace_tx, ws_current).


##### `_wants`  (lines 83–84)

```
def _wants(missing: AgentSetup) -> str
```

**Purpose**: Turns a missing setup checklist into a short human-readable phrase, such as needing a specific kind of account. It keeps the wording for setup instructions consistent.

**Data flow**: It receives an AgentSetup containing missing connector provider names. It formats each provider as 'a ... account' and joins them with commas. The output is plain text used inside the skill instructions.

**Call relations**: setup_skill calls this when writing the message that tells an agent, or the main agent, what accounts are still needed. It is a small wording helper in the larger flow that converts database state into conversation instructions.

*Call graph*: called by 1 (setup_skill).


##### `setup_skill`  (lines 103–143)

```
async def setup_skill(agent_id: UUID, is_main: bool, has_speaker: bool) -> RuntimeSkill | None
```

**Purpose**: Decides whether the current turn should include the agent setup skill, and if so, builds the instructions for it. This lets an agent ask a member to connect the accounts it needs, but only when that request can actually be acted on.

**Data flow**: It receives the current agent ID, whether that agent is the main agent, and whether a member is speaking. If no member is speaking, it returns nothing. Otherwise it asks pending_setup for all unfinished setup work, checks whether the current agent itself is missing grants, and builds a RuntimeSkill with instructions for either this agent or, if it is the main agent, for other unfinished agents. If there is nothing relevant to do, it returns None.

**Call relations**: This is the file's main decision point. It calls pending_setup to learn the current missing grants, uses _wants to phrase those needs clearly, and constructs a RuntimeSkill only when the conversation is allowed to perform account connection work.

*Call graph*: calls 2 internal fn (_wants, pending_setup); 1 external calls (__init__).


### `core/src/ufo/connectors.py`

`domain_logic` · `cross-cutting: connector discovery, tool execution, and feed-sync credential resolution`

This file is the connector “border crossing” for the system. Connectors let UFO use outside services, but those services need credentials. The hard part is deciding who is allowed to see those credentials and how a request should be authenticated without exposing secrets to the wrong place.

The file describes two main paths. One path is for feed-sync sources, which pull provider data into memory. They ask an AuthProxy for a Credential. That credential may be a special HTTP transport that sends requests through a broker, a bearer token, or custom headers. The other path is for dynamic connector tools, where a ConnectorBroker lists available tools, describes their inputs, runs them, and stages file uploads or downloads.

A ConnectorRegistry is the directory that says which broker owns which provider. If a provider was not registered one by one, an optional ConnectorResolver can claim providers from a live broker catalog. For direct credentials, where a member supplied an API key, the registry can fall back to a configured auth backend.

The most important safety rule is enforced for synced sources tied to a member-owned connection. Before returning or using brokered credentials, the code checks the database to confirm the connection still belongs to the workspace, owner, provider, and account. It also wraps broker transports so that check happens again on every request, like checking a badge not only at the door but before each sensitive action.

#### Function details

##### `Credential.__repr__`  (lines 57–66)

```
def __repr__(self) -> str
```

**Purpose**: Returns a safe text label for a Credential without showing the actual secret. This matters because object representations can accidentally appear in logs, error messages, or debugging output.

**Data flow**: It looks at which authentication route is present: a broker transport, a bearer token, custom headers, or nothing. It turns that into a short string such as “Credential(<bearer: redacted>)” and deliberately leaves out any token or header value.

**Call relations**: This is used whenever Python needs to display a Credential. It does not call other project code; its job is to make accidental displays safe while the rest of the connector flow passes Credential objects around.


##### `AuthProxy.credential`  (lines 85–85)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Defines the promise that any authentication provider must keep: given a workspace, provider, and account, return a safe Credential the connector can use. It is a protocol method, meaning other classes implement it rather than this file providing the body here.

**Data flow**: The caller provides the workspace ID, provider name, and account handle. An implementation uses that information to find the right authentication method and returns a Credential, such as a broker transport or a server-side token.

**Call relations**: Feed-sync code and the source credential resolver rely on this shape. The concrete implementation may be a broker-backed proxy or a direct credential backend selected at deployment.


##### `stale_grant_guidance`  (lines 93–100)

```
def stale_grant_guidance(provider: str) -> str
```

**Purpose**: Builds a helpful error message for a connected account that the current broker no longer recognizes. It tells the user what action can fix the problem: reconnect the account.

**Data flow**: It receives a provider name, inserts it into a plain-language explanation, and returns that explanation as a string. It does not change any state.

**Call relations**: Broker implementations can append this message when they detect an old or invalid grant. The message is meant for situations where retrying will not help because the stored connection points to the wrong broker state.


##### `ConnectorBroker.tools`  (lines 170–172)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Defines how a broker lists tools available for a provider, optionally filtered by a search query. This lets the system discover what actions a connected service can perform.

**Data flow**: The caller supplies a workspace ID, provider name, and query text. An implementation asks its broker-side catalog and returns a tuple of BrokerTool descriptions.

**Call relations**: Dynamic connector discovery calls this kind of broker method through the ConnectorRegistry entry for a provider. This file only defines the required interface; broker extensions provide the real behavior.


##### `ConnectorBroker.schema`  (lines 174–174)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Defines how a broker returns the input details for one specific provider tool. This lets an agent know what arguments it must provide before running the tool.

**Data flow**: The caller gives the workspace, provider, and tool slug. The broker implementation returns a BrokerTool with an input schema, or raises UnknownBrokerTool if the slug does not exist.

**Call relations**: Tool description flows use this after choosing or searching for a tool. It is part of the broker contract consumed through ConnectorRegistry routing.


##### `ConnectorBroker.execute`  (lines 176–184)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Defines how a broker runs one provider tool using an already granted account. The broker, not the agent or sandbox, injects the real provider credential.

**Data flow**: The caller provides workspace and provider identity, the tool slug, tool arguments, an account ID, and an optional idempotency key, which is a repeat-safe request key. The implementation sends the request to the broker and returns the provider/tool response as a dictionary.

**Call relations**: Dynamic connector tools call this through the broker selected by ConnectorRegistry.entry. The important security boundary is that execution happens through the broker, so the token does not cross into the agent-facing side.


##### `ConnectorBroker.file_outputs`  (lines 186–186)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Defines how a broker extracts files produced by a tool run from the broker response. It turns broker-specific response data into a standard list of downloadable file references.

**Data flow**: It receives the raw execute response dictionary. An implementation reads whatever fields name produced files and returns BrokerFile objects containing filenames and temporary URLs.

**Call relations**: After ConnectorBroker.execute returns, file-aware tool code can ask the broker to project file outputs in this standard form. The bytes themselves are not passed through this method.


##### `ConnectorBroker.stage_upload`  (lines 188–196)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Defines how a broker prepares a workspace file so a provider tool can use it. Instead of sending file bytes through the server, it returns a place where the sandbox can upload them directly.

**Data flow**: The caller provides workspace, provider, tool slug, filename, MIME type, and MD5 checksum. The broker implementation returns a StagedUpload with a temporary upload URL when needed, the content type, and the argument value to place into the tool call.

**Call relations**: Dynamic connector tools use this before execution when a tool argument points to a workspace file. Some brokers may reject this with ValueError if their tools expect URL inputs rather than staged uploads.


##### `ConnectorBroker.search`  (lines 198–198)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Defines semantic tool search: asking a broker for tools that match a user’s intent, plus any advice about how to use them. Semantic here means searching by meaning, not only by exact name.

**Data flow**: The caller gives a workspace, provider, and query. The implementation returns a BrokerSearch containing matching tools and optional plan, guidance, and pitfalls.

**Call relations**: Connector discovery or planning code can call this through the selected broker. It complements simple catalog listing by letting broker-specific routing logic help choose tools.


##### `ConnectorBroker.credential`  (lines 200–200)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Defines how a broker supplies a Credential for feed-sync access to a provider account. For brokered accounts, that credential is usually a transport that proxies requests through the broker.

**Data flow**: The caller provides workspace ID, provider name, and account handle. The implementation confirms the account is valid for that workspace and returns a Credential the sync source can use.

**Call relations**: _credential calls this after ConnectorRegistry routing chooses the right broker. _BoundSourceCredentials adds extra source-connection checks before and after this broker-level resolution.


##### `RequestForwarder.forward`  (lines 219–221)

```
async def forward(self, account_id: str, method: str, url: str, headers: Mapping[str, str], body: bytes) -> ForwardedResponse
```

**Purpose**: Defines how a captured provider HTTP request is forwarded through a broker under a connected account. This is used when a command-line tool in the sandbox sends a placeholder credential that must be exchanged server-side.

**Data flow**: It receives an account ID, HTTP method, URL, headers, and request body. An implementation sends the request through the broker with the real account credential injected and returns a ForwardedResponse containing status, headers, and body.

**Call relations**: CliCredential objects carry a RequestForwarder so the egress proxy can recognize and forward matching CLI traffic. This protocol keeps real tokens away from the sandbox and wire visible to it.


##### `ConnectorResolver.transfer_hosts`  (lines 268–268)

```
def transfer_hosts(self) -> tuple[str, ...]
```

**Purpose**: Defines the extra file-transfer hostnames allowed for providers served by an open connector namespace. These hosts are needed so staged uploads and downloads can pass through the egress proxy safely.

**Data flow**: An implementation returns a tuple of host strings. It reads whatever broker configuration it owns and exposes only the hosts that grants in this namespace should admit.

**Call relations**: The resolver supplies this alongside provider claiming and catalog lookup. The egress-related grant logic can use it when a broker serves many provider slugs through one namespace.


##### `ConnectorResolver.claims`  (lines 270–270)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: Defines how an open resolver answers whether its broker serves a provider slug. This avoids assuming the resolver owns every unknown provider name.

**Data flow**: The caller passes a provider name. The implementation may check a live broker catalog and returns true if that provider belongs to the broker namespace, otherwise false.

**Call relations**: Routing code can ask this before choosing between brokered providers and direct workspace credentials. This protocol method is implemented by broker extensions, not by this file.


##### `ConnectorResolver.entry`  (lines 272–272)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: Defines how a resolver builds a ConnectorEntry for a provider it serves. The entry is the routing record that points later calls to the right broker.

**Data flow**: It receives a provider name and returns a ConnectorEntry containing that provider, a label, and the shared broker. It should not need network access according to the file’s design comments.

**Call relations**: ConnectorRegistry.entry calls this when a provider is not in the fixed entries but a resolver exists. _credential also uses resolver.entry when finding brokered credentials for unregistered provider slugs.


##### `ConnectorResolver.catalog`  (lines 274–274)

```
async def catalog(self, query: str, limit: int) -> tuple[CatalogEntry, ...]
```

**Purpose**: Defines how a resolver searches the broker’s live catalog of connectable services. This lets discovery show providers that were not individually registered in the static connector list.

**Data flow**: The caller provides search text and a result limit. The implementation queries its broker catalog and returns CatalogEntry items with provider slugs and labels.

**Call relations**: ConnectorRegistry.search_catalog delegates to this method when a resolver is installed. Discovery tools can combine these live results with fixed registry entries.


##### `ConnectorRegistry.entry`  (lines 291–297)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: Finds the connector routing entry for a provider. It first checks explicitly installed connectors, then falls back to the open resolver if one exists.

**Data flow**: It receives a provider name. It looks in the registry’s entries map; if found, it returns that ConnectorEntry. If not found and a resolver is present, it asks the resolver to build an entry. If neither path works, it raises KeyError.

**Call relations**: Dynamic connector tool flows use this to decide which broker should list, describe, or execute a provider’s tools. It is the central directory lookup for connector dispatch.


##### `ConnectorRegistry.search_catalog`  (lines 299–304)

```
async def search_catalog(self, query: str, limit: int) -> tuple[CatalogEntry, ...]
```

**Purpose**: Returns live catalog search results from the open resolver, or an empty result when no resolver is installed. This keeps discovery simple for deployments that only use fixed connectors.

**Data flow**: It receives search text and a limit. If the registry has no resolver, it returns an empty tuple. If a resolver exists, it forwards the query and limit to resolver.catalog and returns those results.

**Call relations**: Discovery code calls this when it wants services beyond the statically registered connector entries. It delegates all live catalog knowledge to ConnectorResolver.catalog.


##### `_credential`  (lines 307–324)

```
async def _credential(registry: ConnectorRegistry, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Chooses the correct place to get credentials for a provider account. It separates brokered connected accounts from direct workspace credentials.

**Data flow**: It receives the registry, workspace ID, provider, and account handle. If the account is not the special direct-account marker, it looks for a registered broker or resolver-backed broker and asks that broker for credentials. If the account is the direct marker, it asks the fallback AuthProxy. If no valid route exists, it raises a runtime error.

**Call relations**: _BoundSourceCredentials.credential calls this after it has checked whether the source is allowed to use the requested account. This function then hands off to either ConnectorBroker.credential or AuthProxy.credential depending on the account type.

*Call graph*: called by 1 (credential).


##### `_require_source_connection`  (lines 327–351)

```
async def _require_source_connection(workspace_id: UUID, connection_id: UUID, owner_member_id: UUID, provider: str, account: str) -> None
```

**Purpose**: Checks that a feed-sync source is still tied to the exact member-owned connection it claims to use. This prevents an old or altered source from using a connection that no longer belongs to it.

**Data flow**: It receives workspace ID, connection ID, owner member ID, provider, and account. Inside the workspace context, it opens a workspace database transaction, runs a SQL query against the connection table, and looks for a row matching all those values. If it finds one, nothing is returned. If it finds none, it raises ValueError.

**Call relations**: _BoundSourceCredentials.credential calls this before returning brokered source credentials. _ConnectionTransport.handle_async_request calls it again before every proxied HTTP request, so authorization is rechecked during actual use, not only when the credential was first resolved.

*Call graph*: called by 2 (credential, handle_async_request); 3 external calls (select, workspace_tx, ws).


##### `_ConnectionTransport.handle_async_request`  (lines 363–371)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: Wraps a broker HTTP transport with a fresh connection authorization check before each request. This is a guardrail for long-running syncs where a connection might be removed or changed after the sync started.

**Data flow**: It receives an HTTP request. Before passing the request onward, it calls _require_source_connection using the stored workspace, connection, owner, provider, and account. If the check passes, it sends the request to the inner transport and returns the HTTP response. If the check fails, the request is stopped with an error.

**Call relations**: _BoundSourceCredentials.credential creates this wrapper when it receives a broker transport from _credential. The wrapper delegates the actual network behavior to the inner httpx transport after the safety check.

*Call graph*: calls 1 internal fn (_require_source_connection).


##### `_ConnectionTransport.aclose`  (lines 373–374)

```
async def aclose(self) -> None
```

**Purpose**: Closes the underlying HTTP transport when the wrapper is no longer needed. This lets network resources be cleaned up properly.

**Data flow**: It takes no outside input besides the wrapper’s stored inner transport. It calls the inner transport’s async close method and returns nothing.

**Call relations**: HTTP client cleanup calls this as part of normal transport shutdown. It does not perform authorization checks because no provider request is being sent; it simply passes cleanup through to the wrapped transport.


##### `_BoundSourceCredentials.credential`  (lines 383–413)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns credentials for a feed-sync source, but only if that source is allowed to use the requested account style. It enforces the difference between direct credentials and member-owned broker connections.

**Data flow**: It receives workspace ID, provider, and account. If the account is the direct marker, it refuses the request when this resolver was bound to a connection, then delegates to _credential. If the account is brokered, it requires a stored connection ID and owner member ID, verifies the connection with _require_source_connection, asks _credential for the broker credential, requires that credential to contain a proxy transport, and returns a new Credential whose transport is wrapped in _ConnectionTransport.

**Call relations**: This is the AuthProxy implementation created by SourceCredentialResolver.bind. It calls _credential for routing, _require_source_connection for ownership checks, and constructs _ConnectionTransport so each later request repeats the check.

*Call graph*: calls 2 internal fn (_credential, _require_source_connection); 2 external calls (__init__, __init__).


##### `SourceCredentialResolver.bind`  (lines 420–425)

```
def bind(self, connection_id: UUID | None, owner_member_id: UUID | None) -> AuthProxy
```

**Purpose**: Creates an AuthProxy that is bound to one source’s connection information. This turns the general connector registry into a source-specific credential resolver with the right safety checks baked in.

**Data flow**: It receives an optional connection ID and optional owner member ID. It packages those values together with the registry into a _BoundSourceCredentials object and returns it as an AuthProxy.

**Call relations**: The sync runner uses this when preparing a feed-sync source. The returned _BoundSourceCredentials object later performs the actual credential lookup and connection validation when its credential method is called.

*Call graph*: 1 external calls (__init__).


### Composio bridge
Composio integration files expose its hosted app catalog, account linking, proxied provider calls, MCP execution, and package import surface.

### `extensions/composio/ufo_ext_composio/broker.py`

`io_transport` · `request handling for Composio-backed connector operations`

ComposioBroker is the shared adapter used whenever this project wants to talk to a Composio-backed connector. Think of it like a front desk: the rest of UFO asks for “available tools,” “run this tool,” or “give me a safe credential,” and this file translates those requests into Composio API calls.

A key idea is that the broker fetches the Composio client fresh for each call. That means tests can swap the network transport, and long-lived connection state does not leak between calls. Tool discovery turns Composio’s catalog entries into the project’s simpler BrokerTool objects. Schema lookup also rewrites file-upload inputs into the vocabulary UFO expects.

Execution is careful about errors. If a tool slug is missing, it tries to help by listing real available tool slugs for that provider. If the problem is an old or missing connected account, it tells the agent that the user should reconnect instead of pretending the tool name was wrong.

The file also deals with files. It can find Composio file outputs buried anywhere in a response, and it can create a staged upload slot for file inputs. For credentials, it does not hand out provider tokens. Instead, it verifies the account belongs to this workspace’s broker user, then returns a Credential whose transport proxies HTTP through Composio.

#### Function details

##### `ComposioBroker.tools`  (lines 48–49)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Finds tools for a given Composio provider and search query, then returns them in UFO’s standard tool format. Someone would use this when an agent or user needs to know what actions are available.

**Data flow**: It receives a workspace id, provider name, and query text. It asks the current Composio client for matching tools, then passes the raw rows through the local converter that trims and normalizes them. It returns a tuple of BrokerTool objects.

**Call relations**: This is one of the broker’s public discovery paths. It relies on the shared Composio client for the catalog lookup, then hands the response to _discovered_tools so the rest of UFO sees a clean, consistent shape.

*Call graph*: calls 1 internal fn (_discovered_tools); 1 external calls (composio_client).


##### `ComposioBroker.schema`  (lines 51–64)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Fetches the detailed input schema for one tool slug. This tells the caller what arguments the tool expects, including file inputs rewritten into UFO’s workspace-file format.

**Data flow**: It receives a workspace id, provider, and tool slug. It asks Composio for that tool’s schema, converts file-upload fields into the project’s expected file shape, and returns a BrokerTool with the slug, description, and input schema. If Composio says the slug does not exist, it turns that into UFO’s UnknownBrokerTool error.

**Call relations**: This is used after discovery or when a caller already has a tool slug and needs exact input rules. It calls the Composio client for the raw schema and the schema conversion helper for file-aware cleanup before creating the BrokerTool result.

*Call graph*: 4 external calls (__init__, __init__, composio_client, workspace_file_schema).


##### `ComposioBroker.execute`  (lines 66–89)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Runs a Composio tool for a workspace and connected account. It also turns two common failure cases into more helpful messages: stale accounts and mistyped tool slugs.

**Data flow**: It receives the workspace, provider, tool slug, input arguments, connected account id, and optional idempotency key, which helps avoid duplicate work if a request is retried. It sends the execution request to Composio using a broker-user id derived from the workspace id. On success it returns Composio’s response dictionary. On failure it may replace the error with reconnect guidance or with a missing-slug error that includes real available tool names.

**Call relations**: This is the main run path for broker tools. It uses _stale_account to decide whether an error means the user must reconnect, _reconnect_error to build that guidance, and _slug_miss to improve a 404 tool-not-found response with better next-step information.

*Call graph*: calls 3 internal fn (_slug_miss, _reconnect_error, _stale_account); 1 external calls (composio_client).


##### `ComposioBroker.file_outputs`  (lines 91–96)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Extracts files produced by a tool run from a possibly nested response. This matters because a tool’s output may contain downloadable files at different depths inside the returned data.

**Data flow**: It receives a response dictionary from a tool execution. It creates an empty list, asks _collect_files to walk through the response and add any recognized file objects, then returns those files as an immutable tuple.

**Call relations**: This is called after tool execution when UFO needs to discover downloadable outputs. It delegates the recursive searching work to _collect_files, keeping this public method small and easy to use.

*Call graph*: calls 1 internal fn (_collect_files).


##### `ComposioBroker.stage_upload`  (lines 98–114)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Creates a temporary upload destination for a file that will be passed into a Composio tool. This lets UFO upload bytes to Composio’s file store before the tool is run.

**Data flow**: It receives workspace and provider context, the tool slug, filename, MIME type, and MD5 checksum. It asks Composio to create an upload slot, then returns a StagedUpload containing the URL to PUT the file to, the content type to use, and the argument object that should later be passed to the tool.

**Call relations**: This is used before executing tools that accept file inputs. It relies on the Composio client to mint the upload location, then packages the result into UFO’s standard staged-upload object.

*Call graph*: 2 external calls (__init__, composio_client).


##### `ComposioBroker.search`  (lines 116–119)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Searches Composio tools through the Tool Router, which is a higher-level search path than simple listing. It helps find relevant connector tools from a provider and query.

**Data flow**: It receives a workspace id, provider, and search query. It gets the current Composio client and passes everything to Composio’s connector-tool search helper. It returns the BrokerSearch result from that helper.

**Call relations**: This is another public discovery path on the broker. Rather than converting rows itself, it hands off to search_connector_tools, which owns the Tool Router search behavior.

*Call graph*: 2 external calls (composio_client, search_connector_tools).


##### `ComposioBroker.credential`  (lines 121–137)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a safe Credential for making provider HTTP requests through Composio, without exposing the provider’s real secret token. It first confirms the connected account belongs to this workspace’s broker user.

**Data flow**: It receives a workspace id, provider, and connected account id. It builds the broker-user id for the workspace, asks Composio to verify that account, and if valid creates a Credential with a ComposioProxyTransport. If the account is missing, it raises an error telling the user to reconnect.

**Call relations**: This is used when code needs to talk to the provider through an authenticated path, such as a feed sync, but must not hold raw secrets. It calls _reconnect_error for stale-account guidance and wraps the Composio proxy transport inside UFO’s Credential object.

*Call graph*: calls 1 internal fn (_reconnect_error); 4 external calls (__init__, __init__, AsyncHTTPTransport, composio_client).


##### `ComposioBroker._slug_miss`  (lines 139–160)

```
async def _slug_miss(self, client: composio.ComposioClient, provider: str, slug: str, error: composio.ComposioError) -> composio.ComposioError
```

**Purpose**: Improves a tool-not-found error by trying to add a list of real available tool slugs. This gives the agent useful correction hints instead of only saying “404 not found.”

**Data flow**: It receives a Composio client, provider, attempted slug, and original error. It turns the failed slug into a search query, asks Composio for matching tools, and if needed falls back to listing tools with an empty query. If it finds tools, it returns a new ComposioError whose message includes their slugs; otherwise it returns the original error.

**Call relations**: This helper is used by ComposioBroker.execute when Composio says a tool slug was not found. It uses _discovered_tools to normalize the possible alternatives before adding them to the error message.

*Call graph*: calls 2 internal fn (_discovered_tools, list_tools); called by 1 (execute); 2 external calls (sub, ComposioError).


##### `_collect_files`  (lines 163–172)

```
def _collect_files(value: object, found: list[BrokerFile]) -> None
```

**Purpose**: Walks through a nested value and finds Composio file objects. A file object is recognized by having a non-empty s3url plus name and mimetype fields.

**Data flow**: It receives any value and a list being built up by the caller. If the value looks like a Composio file, it adds a BrokerFile with the file name and download URL. If the value is a dictionary or list, it visits each contained item. It does not return a value; it changes the provided list.

**Call relations**: ComposioBroker.file_outputs uses this helper to search an execution response. The helper does the tree-walking so the public method can simply return the final collection of files.

*Call graph*: called by 1 (file_outputs); 1 external calls (__init__).


##### `_stale_account`  (lines 175–186)

```
def _stale_account(error: composio.ComposioError, account_id: str) -> bool
```

**Purpose**: Decides whether a Composio execution error probably means the connected account is gone or no longer owned by this broker. This avoids misleading the agent with tool-name suggestions when the real fix is reconnecting the account.

**Data flow**: It receives a Composio error and the connected account id that was used. It lowercases the error body and looks for narrow signs of a missing connected account, either Composio’s own “connected account not found” wording or the specific account id with “not found.” It returns true or false.

**Call relations**: ComposioBroker.execute calls this before treating a 404 as a missing tool slug. If it returns true, execute switches to reconnect guidance through _reconnect_error.

*Call graph*: called by 1 (execute).


##### `_reconnect_error`  (lines 189–190)

```
def _reconnect_error(error: composio.ComposioError, provider: str) -> composio.ComposioError
```

**Purpose**: Builds a Composio error message that tells the caller the user should reconnect the provider account. It preserves the original status code while adding human guidance.

**Data flow**: It receives the original Composio error and provider name. It asks the connector layer for stale-grant guidance for that provider, appends that guidance to the original error body, and returns a new ComposioError.

**Call relations**: Both ComposioBroker.execute and ComposioBroker.credential use this when an account appears missing or stale. It centralizes the wording so reconnect failures are explained consistently.

*Call graph*: called by 2 (credential, execute); 2 external calls (stale_grant_guidance, ComposioError).


##### `_discovered_tools`  (lines 193–213)

```
def _discovered_tools(rows: tuple[dict[str, object], ...]) -> tuple[BrokerTool, ...]
```

**Purpose**: Converts raw Composio tool-list rows into UFO’s BrokerTool objects. It keeps only usable tool names, shortens long descriptions, and converts file inputs into the workspace-file shape UFO expects.

**Data flow**: It receives a tuple of dictionaries from Composio. For each row, it chooses the slug from the row’s slug or name, skips rows without a valid string slug, trims the description to a fixed cap, converts input parameters with the workspace-file schema helper, and collects BrokerTool objects. It returns the tools as a tuple.

**Call relations**: ComposioBroker.tools uses this for normal discovery results, and ComposioBroker._slug_miss uses it when preparing helpful alternatives after a failed execution. It is the shared translator from Composio catalog data into UFO’s broker vocabulary.

*Call graph*: called by 2 (_slug_miss, tools); 2 external calls (__init__, workspace_file_schema).


### `extensions/composio/ufo_ext_composio/resolver.py`

`domain_logic` · `connector discovery and connection setup`

Composio acts like a marketplace for many outside services. Instead of this project keeping a separate built-in connector for every service, this resolver treats Composio as an open namespace: if a user asks for a provider slug, such as a toolkit name, the resolver checks whether Composio can connect to it and then routes it through one shared Composio broker.

The file’s main class, ComposioResolver, is small and mostly stateless. That matters because the Composio client and API key are fetched each time they are needed, so tests can swap in a fake transport and long-lived connections do not leak between runs.

The resolver does four main jobs. First, it exposes the Composio file-transfer hosts that are allowed through the sandbox, so tools can still move files safely. Second, it decides whether a provider slug belongs to Composio: it rejects locally banned names, then asks Composio’s live catalog if the toolkit is actually connectable. Third, it builds the OAuth description used for connection, but with no provider host, because the real account token stays with Composio and execution happens on Composio’s side. Finally, it searches Composio’s catalog and returns only connectable services for discovery, so users are not offered services they cannot later connect.

#### Function details

##### `ComposioResolver.transfer_hosts`  (lines 32–33)

```
def transfer_hosts(self) -> tuple[str, ...]
```

**Purpose**: This property tells the rest of the system which Composio file-storage hosts are trusted for transfers. It is used so files produced or consumed by Composio-run tools can pass through the sandbox without opening access to arbitrary hosts.

**Data flow**: It reads no outside input from the caller. It returns the fixed list of Composio transfer host names defined by the Composio client module, leaving the resolver unchanged.

**Call relations**: When the connector system needs to know which external hosts are allowed for file movement, it asks this resolver. The property simply hands back the shared Composio host list rather than doing any lookup.


##### `ComposioResolver.claims`  (lines 35–38)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: This function decides whether a provider name should be treated as a Composio-backed connector. It first blocks names that are banned locally, then checks Composio’s live catalog to see whether the toolkit can actually be connected.

**Data flow**: The input is a provider slug, which is a short machine-friendly name for a service. The function lowercases it for the local banned-name check; if it is banned, it returns false immediately. Otherwise it creates or fetches a Composio client, asks whether that toolkit is connectable, and returns true only if Composio reports a match.

**Call relations**: The connector registry uses this when no explicitly registered connector has already claimed the provider. To make the decision, this function calls the Composio client helper and delegates the live catalog check to that client.

*Call graph*: 1 external calls (composio_client).


##### `ComposioResolver.descriptor`  (lines 40–41)

```
def descriptor(self, provider: str) -> OAuthProvider
```

**Purpose**: This function builds the OAuth description for a Composio-backed provider. OAuth is the common web sign-in permission flow; here the descriptor says the connection goes through Composio rather than directly to the service’s own host.

**Data flow**: It receives the provider slug and creates a ComposioOAuthProvider object with that provider name and an empty host. The result is returned to the caller; no state inside the resolver is changed.

**Call relations**: After a provider has been accepted as Composio-backed, the connection flow asks for a descriptor. This function hands off to ComposioOAuthProvider to create the object the rest of the OAuth setup understands.

*Call graph*: 1 external calls (__init__).


##### `ComposioResolver.entry`  (lines 43–46)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: This function creates the connector entry that tells the system how to route a Composio-backed provider. It gives the provider a human-readable label and attaches it to the shared Composio broker that will run the tools.

**Data flow**: It receives a provider slug such as a name with underscores. It turns that slug into a title-style label by replacing underscores with spaces and capitalizing words, then returns a ConnectorEntry containing the original provider name, the label, and this resolver’s broker.

**Call relations**: Once the registry knows a provider belongs to Composio, it asks for an entry so the provider can be used like any other connector. This function constructs that entry and points it at the already supplied shared broker.

*Call graph*: 1 external calls (__init__).


##### `ComposioResolver.catalog`  (lines 48–52)

```
async def catalog(self, query: str, limit: int=TOOLKIT_SEARCH_LIMIT) -> tuple[CatalogEntry, ...]
```

**Purpose**: This function searches Composio’s toolkit catalog and returns services that can be offered to users for discovery. It helps ensure the discovery UI or tool shows services that can actually be connected afterward.

**Data flow**: It receives a search query and an optional maximum number of results. It asks the current Composio client for matching toolkits, then converts each returned slug and label pair into a CatalogEntry. It returns those entries as an immutable tuple and does not change resolver state.

**Call relations**: Discovery code calls this when it wants to show Composio-backed services matching a user’s search. The function gets a Composio client, asks it for catalog rows, then wraps each row in the standard CatalogEntry shape used by the rest of the connector system.

*Call graph*: 2 external calls (__init__, composio_client).


### `extensions/composio/ufo_ext_composio/client.py`

`io_transport` · `request handling and connector discovery/execution`

Composio acts like a central switchboard for third-party apps. Instead of this project writing and storing credentials for every provider, it asks Composio to create OAuth consent links, remember the connected account, list the available tools, and run those tools on Composio’s side. This file is the bridge to that switchboard.

The main piece is ComposioClient. It talks to Composio’s REST API, which means ordinary web requests returning JSON data. It can make an OAuth link for a user, verify that a returned connected account belongs to the expected workspace user and toolkit, list or search tools, execute one tool, and request upload slots for files. It also creates Tool Router sessions, which are Composio-hosted search sessions used to find the right tool for a natural-language task.

The file is careful about safety. It refuses toolkits that are banned, empty, or not supported by Composio-managed authentication. It validates user-supplied toolkit names before putting them in URLs. It bounds tool execution payload size so a huge request cannot be sent accidentally. It also turns bad or oddly shaped Composio responses into loud ComposioError failures rather than pretending nothing happened.

In short, this file is the trusted border crossing between this project and Composio.

#### Function details

##### `connectable`  (lines 122–146)

```
def connectable(slug: str, toolkit: Mapping[str, object]) -> bool
```

**Purpose**: Decides whether a Composio toolkit is something this deploy should offer to members. It checks that the toolkit is not on the project’s deny list, has Composio-managed sign-in support, and actually contains tools.

**Data flow**: It receives a toolkit slug, such as a provider name, and a catalog record from Composio. It reads the record’s managed authentication schemes and tool count, compares the slug against the banned list, and returns true only when the toolkit is usable here.

**Call relations**: This is the shared gatekeeper used before the system claims or lists a toolkit. ComposioClient.connectable_toolkit uses it for one named toolkit, and ComposioClient.list_toolkits uses it while filtering search results from Composio’s catalog.

*Call graph*: called by 2 (connectable_toolkit, list_toolkits).


##### `ComposioError.__init__`  (lines 153–156)

```
def __init__(self, status: int, body: str) -> None
```

**Purpose**: Builds a clear exception for failed or unusable Composio responses. It keeps both the HTTP status code and the response body so callers can report what went wrong.

**Data flow**: It receives a numeric status and a text body. It formats them into a readable error message, stores the status on the error object, stores the body too, and returns the constructed exception instance.

**Call relations**: Several client methods raise this when Composio gives a missing field, wrong owner, inactive account, bad upload answer, bad router session, or failed HTTP response. It is the common way this file turns external-service trouble into a loud local failure.

*Call graph*: called by 6 (_auth_config, connect_link, connected_account, create_upload, tool_router_session, _body).


##### `ComposioClient.connect_link`  (lines 173–182)

```
async def connect_link(self, toolkit: str, user_id: str, callback_url: str) -> str
```

**Purpose**: Creates the hosted consent link a member opens to connect a third-party account through Composio. This is the start of the OAuth flow, where OAuth means the standard browser-based permission handoff used by many services.

**Data flow**: It receives a toolkit name, a broker user id, and a callback URL. It first finds or creates an auth configuration, sends those details to Composio, reads the redirect URL from the answer, and returns that URL. If the URL is missing, it raises ComposioError.

**Call relations**: This method starts by relying on ComposioClient._auth_config to choose the right sign-in configuration. It then hands the request to ComposioClient._post, and uses ComposioError if Composio’s response cannot actually send the member anywhere.

*Call graph*: calls 3 internal fn (_auth_config, _post, __init__).


##### `ComposioClient.connected_account`  (lines 184–208)

```
async def connected_account(self, account_id: str, expected_user_id: str, expected_toolkit: str) -> OAuthAccount
```

**Purpose**: Confirms that a connected account id is valid for the expected workspace user and expected toolkit. This prevents accidentally or maliciously binding the wrong Composio account to a grant.

**Data flow**: It receives an account id, the user id that should own it, and the toolkit it should authenticate. It fetches the connected account record, checks the owner, checks that the account is active, checks the toolkit slug, and returns an OAuthAccount containing only the account id.

**Call relations**: It uses ComposioClient._get to read Composio’s account record. If any ownership, status, or toolkit check fails, it raises ComposioError; if all checks pass, it creates the OAuthAccount that the rest of the connector system can store safely.

*Call graph*: calls 2 internal fn (_get, __init__); 1 external calls (__init__).


##### `ComposioClient.account_label`  (lines 210–213)

```
async def account_label(self, account_id: str) -> str | None
```

**Purpose**: Looks up a human-friendly label for a connected account, if Composio has one. This helps show a member which account they connected.

**Data flow**: It receives a connected account id, fetches the account record, reads the alias field, and returns the alias only if it is a non-empty string. Otherwise it returns nothing.

**Call relations**: It is a small public lookup built on ComposioClient._get. Unlike the stricter account verification method, it only extracts display text and does not create or validate a grant.

*Call graph*: calls 1 internal fn (_get).


##### `ComposioClient.list_tools`  (lines 215–245)

```
async def list_tools(self, toolkit: str, query: str='') -> tuple[dict[str, object], ...]
```

**Purpose**: Lists the tools Composio exposes for one toolkit, optionally narrowed by a query. It follows Composio’s pagination so tools on later pages are not missed.

**Data flow**: It receives a toolkit slug and optional search text. It repeatedly requests pages from Composio, keeps only dictionary-shaped tool rows, stops at the last page or the project’s maximum listing size, and returns the collected rows as an immutable tuple.

**Call relations**: ComposioBroker._slug_miss calls this when it needs to discover whether a tool slug exists in a toolkit. Internally, this method repeatedly uses ComposioClient._get to walk through Composio’s paged results.

*Call graph*: calls 1 internal fn (_get); called by 1 (_slug_miss).


##### `ComposioClient.tool_schema`  (lines 247–248)

```
async def tool_schema(self, slug: str) -> dict[str, object]
```

**Purpose**: Fetches the full catalog entry for one Composio tool. A caller uses this when it needs the exact input shape and details for a known tool slug.

**Data flow**: It receives a tool slug, sends a GET request for that tool, and returns Composio’s JSON object as a Python dictionary.

**Call relations**: This is a thin public wrapper around ComposioClient._get. It gives higher-level connector code a simple method name instead of making it know the raw Composio URL path.

*Call graph*: calls 1 internal fn (_get).


##### `ComposioClient.connectable_toolkit`  (lines 250–267)

```
async def connectable_toolkit(self, slug: str) -> str | None
```

**Purpose**: Checks whether one user-supplied toolkit slug can be offered for connection, and returns its display name if so. It also blocks unsafe slug strings before they can become part of a URL.

**Data flow**: It receives a slug. It first rejects slugs with characters outside the allowed toolkit-name pattern, then fetches the toolkit record from Composio. If the toolkit is missing or not connectable, it returns nothing; otherwise it returns the toolkit’s name or the slug as a fallback.

**Call relations**: This method combines a safe URL check, ComposioClient._get for the catalog lookup, and connectable for the project’s availability rules. It is the one-toolkit version of the broader listing flow.

*Call graph*: calls 2 internal fn (_get, connectable).


##### `ComposioClient.list_toolkits`  (lines 269–287)

```
async def list_toolkits(self, query: str, limit: int) -> tuple[tuple[str, str], ...]
```

**Purpose**: Searches Composio’s toolkit catalog and returns only the services this deploy can actually connect. This powers discovery without exposing unusable or banned services.

**Data flow**: It receives search text and a result limit. It asks Composio for matching toolkits, walks the returned items, keeps only valid slugs that pass connectable, chooses a display label for each, and returns pairs of slug and label.

**Call relations**: It uses ComposioClient._get to query Composio’s toolkit catalog, then applies connectable to each candidate. This means the discovery surface and the actual connection rules stay aligned.

*Call graph*: calls 2 internal fn (_get, connectable).


##### `ComposioClient.execute_tool`  (lines 289–303)

```
async def execute_tool(self, slug: str, arguments: Mapping[str, object], user_id: str, connected_account_id: str | None=None, idempotency_key: str | None=None) -> dict[str, object]
```

**Purpose**: Runs a Composio tool on Composio’s server-side execute API. This lets the project use a connected account without handling the provider’s secret token itself.

**Data flow**: It receives a tool slug, input arguments, a user id, and optionally a connected account id and idempotency key. It builds the request body, checks that the JSON-encoded payload is not too large, adds the idempotency header when provided, sends the POST request, and returns Composio’s result object.

**Call relations**: This method hands the actual network call to ComposioClient._post. The idempotency key, when used, tells Composio to treat repeated identical requests as the same operation, which helps avoid duplicate side effects after retries.

*Call graph*: calls 1 internal fn (_post); 1 external calls (dumps).


##### `ComposioClient.create_upload`  (lines 305–329)

```
async def create_upload(self, toolkit: str, slug: str, filename: str, mimetype: str, md5: str) -> 'ComposioUpload'
```

**Purpose**: Asks Composio where a file should be uploaded before a tool uses it. It produces a store key for the tool argument and, when needed, a temporary upload URL.

**Data flow**: It receives toolkit and tool slugs plus file name, MIME type, and MD5 checksum. It sends those details to Composio, requires a non-empty storage key, reads the optional presigned PUT URL, and returns a ComposioUpload object. If the response is missing required pieces, it raises ComposioError.

**Call relations**: It uses ComposioClient._post to request the upload slot. It creates ComposioUpload for the successful result, and raises ComposioError when Composio’s answer cannot safely tell the sandbox where to put the file.

*Call graph*: calls 2 internal fn (_post, __init__); 1 external calls (__init__).


##### `ComposioClient.tool_router_session`  (lines 331–342)

```
async def tool_router_session(self, user_id: str, toolkits: list[str]) -> ToolRouterSession
```

**Purpose**: Opens a Composio Tool Router session for semantic tool search. A Tool Router session is a Composio search endpoint scoped to a user and a set of toolkits.

**Data flow**: It receives a user id and a list of toolkit slugs. It asks Composio to create a session with those toolkits enabled, reads the session id and MCP URL from the response, and returns a ToolRouterSession. If either value is missing or malformed, it raises ComposioError.

**Call relations**: search_connector_tools calls this when it needs a session and none is cached yet. The method uses ComposioClient._post for the HTTP request and returns the URL that later semantic search calls will use.

*Call graph*: calls 2 internal fn (_post, __init__); called by 1 (search_connector_tools); 1 external calls (__init__).


##### `ComposioClient._auth_config`  (lines 344–362)

```
async def _auth_config(self, toolkit: str) -> str
```

**Purpose**: Finds the authentication configuration that should be used for a toolkit, creating a Composio-managed one if none exists. This makes the consent flow work even when an operator has not pre-created a custom config.

**Data flow**: It receives a toolkit slug. It first asks Composio for existing auth configs, extracts the first id if present, and returns it. If there is no existing id, it posts a request to create a managed config, validates the returned id, and returns that new id.

**Call relations**: ComposioClient.connect_link calls this before it can mint an OAuth link. This method uses ComposioClient._get, _auth_config_id, and ComposioClient._post, and raises ComposioError if Composio creates something without an id.

*Call graph*: calls 4 internal fn (_get, _post, __init__, _auth_config_id); called by 1 (connect_link).


##### `ComposioClient._get`  (lines 364–366)

```
async def _get(self, path: str, params: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Sends a GET request to Composio and turns the response into a plain dictionary. GET is the usual web method for reading information.

**Data flow**: It receives a URL path and optional query parameters. It opens an HTTP client, sends the request, passes the response to _body, and returns the parsed dictionary.

**Call relations**: Most read-style methods in this file use this helper, including account lookup, tool listing, toolkit lookup, schema lookup, and auth config lookup. It relies on ComposioClient._http to build the client and _body to validate the response.

*Call graph*: calls 2 internal fn (_http, _body); called by 7 (_auth_config, account_label, connectable_toolkit, connected_account, list_toolkits, list_tools, tool_schema).


##### `ComposioClient._post`  (lines 368–372)

```
async def _post(self, path: str, body: dict[str, object], headers: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Sends a POST request to Composio and turns the response into a plain dictionary. POST is the usual web method for creating something or asking a service to perform an action.

**Data flow**: It receives a URL path, a JSON body, and optional headers. It opens an HTTP client, sends the body to Composio, passes the response to _body, and returns the parsed dictionary.

**Call relations**: Write or action methods use this helper, including auth config creation, link creation, tool execution, file upload slot creation, and Tool Router session creation. It shares the same HTTP setup and response validation path as _get.

*Call graph*: calls 2 internal fn (_http, _body); called by 5 (_auth_config, connect_link, create_upload, execute_tool, tool_router_session).


##### `ComposioClient._http`  (lines 374–380)

```
def _http(self) -> httpx.AsyncClient
```

**Purpose**: Creates the actual asynchronous HTTP client used to talk to Composio. It centralizes the base URL, API key header, timeout, and optional test transport.

**Data flow**: It reads the client’s API key and optional transport override. It builds an httpx AsyncClient configured for Composio’s API and returns it to the caller.

**Call relations**: ComposioClient._get and ComposioClient._post call this each time they need to make a request. By keeping this setup in one place, every request uses the same authentication and timeout behavior.

*Call graph*: called by 2 (_get, _post); 1 external calls (AsyncClient).


##### `_body`  (lines 383–391)

```
def _body(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Checks and parses an HTTP response from Composio. It turns failed status codes or unexpected response shapes into ComposioError instead of returning misleading data.

**Data flow**: It receives an HTTP response. If the status code means failure, it raises ComposioError with the response text. If the body is empty, it returns an empty dictionary. Otherwise it parses JSON and returns it only if it is an object-shaped dictionary.

**Call relations**: ComposioClient._get and ComposioClient._post both send all responses here. This makes response validation consistent for every Composio call in the file.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_get, _post); 1 external calls (json).


##### `workspace_file_schema`  (lines 394–420)

```
def workspace_file_schema(value: object) -> object
```

**Purpose**: Rewrites Composio tool input schemas so file inputs use this project’s workspace-file language. This keeps the model from seeing internal Composio storage details it should not have to build.

**Data flow**: It receives any schema value. If it finds a dictionary marked as file-uploadable, it replaces that part with an object requiring a workspace file path. For nested dictionaries and lists, it recursively rewrites their contents. Other values pass through unchanged.

**Call relations**: _search_result calls this while converting Tool Router results into BrokerTool objects. The result is a tool schema the agent can understand: provide a path in /workspace, and the broker will handle the upload details later.

*Call graph*: called by 1 (_search_result).


##### `_auth_config_id`  (lines 423–430)

```
def _auth_config_id(payload: dict[str, object]) -> str | None
```

**Purpose**: Extracts the first authentication config id from a Composio list response. It is a small helper that hides the response-shape checking from the main auth-config flow.

**Data flow**: It receives a dictionary payload, looks for an items list, scans for the first item that is a dictionary with a string id, and returns that id. If the shape is missing or no id is found, it returns nothing.

**Call relations**: ComposioClient._auth_config calls this after listing existing auth configs. A returned id lets that method reuse an existing configuration instead of creating another one.

*Call graph*: called by 1 (_auth_config).


##### `composio_client`  (lines 433–440)

```
def composio_client() -> ComposioClient
```

**Purpose**: Builds the deploy’s normal ComposioClient from the COMPOSIO_API_KEY environment variable. It fails immediately if the key is not configured.

**Data flow**: It reads COMPOSIO_API_KEY from the process environment. If the value is missing or empty, it raises RuntimeError; otherwise it returns a ComposioClient initialized with that key.

**Call relations**: This is the convenient factory for code that needs the real Composio client. It keeps API-key lookup in one place so connector flows do not silently run without broker credentials.

*Call graph*: 1 external calls (__init__).


##### `_dict`  (lines 447–448)

```
def _dict(value: object) -> dict[str, object]
```

**Purpose**: Safely treats a value as a dictionary only when it really is one. It avoids repeated type checks in result-conversion code.

**Data flow**: It receives any value. If the value is a dictionary, it returns it unchanged; otherwise it returns an empty dictionary.

**Call relations**: _search_result uses this helper while reading nested Tool Router response fields. That keeps malformed or missing parts from causing ordinary attribute errors.

*Call graph*: called by 1 (_search_result).


##### `_str_tuple`  (lines 451–454)

```
def _str_tuple(value: object) -> tuple[str, ...]
```

**Purpose**: Safely extracts non-empty strings from a list and returns them as an immutable tuple. It is used for fields that should be lists of text but may be absent or messy.

**Data flow**: It receives any value. If the value is not a list, it returns an empty tuple. If it is a list, it keeps only non-empty strings and returns them as a tuple.

**Call relations**: _search_result uses this helper for tool slugs, plan steps, execution guidance, and pitfalls. This lets the conversion keep useful text while ignoring badly shaped entries.

*Call graph*: called by 1 (_search_result).


##### `_search_result`  (lines 457–491)

```
def _search_result(result: dict[str, object]) -> BrokerSearch
```

**Purpose**: Converts a raw Composio Tool Router search response into the project’s BrokerSearch format. BrokerSearch is the project’s clean summary of suggested tools, plans, guidance, and warnings.

**Data flow**: It receives the raw result dictionary. It finds the nested result data, reads tool schemas, collects primary and related tool slugs without duplicates, rewrites file-upload schemas, builds BrokerTool entries, gathers recommended plan steps, guidance, and pitfalls, and returns a BrokerSearch.

**Call relations**: search_connector_tools calls this after the MCP tool search returns. Inside, it uses _dict, _str_tuple, and workspace_file_schema to safely shape Composio’s answer into data the dynamic connector tools can display and use.

*Call graph*: calls 3 internal fn (_dict, _str_tuple, workspace_file_schema); called by 1 (search_connector_tools); 2 external calls (__init__, __init__).


##### `search_connector_tools`  (lines 494–517)

```
async def search_connector_tools(client: ComposioClient, workspace_id: UUID, connector: str, query: str) -> BrokerSearch
```

**Purpose**: Runs semantic search for tools inside one connected toolkit. Instead of simply matching names, it asks Composio’s Tool Router what tools fit the user’s described use case.

**Data flow**: It receives a ComposioClient, workspace id, connector slug, and query text. It builds the broker user id, reuses or creates a cached Tool Router session for that user and connector, calls the Composio search tool over MCP, then converts the raw result into BrokerSearch.

**Call relations**: When no cached session exists, it calls ComposioClient.tool_router_session and stores the result behind a lock so concurrent searches do not create duplicate sessions. It then calls mcp_session.mcp_call_tool to ask Composio’s router, and hands the answer to _search_result for cleanup.

*Call graph*: calls 2 internal fn (tool_router_session, _search_result); 1 external calls (mcp_call_tool).


### `extensions/composio/ufo_ext_composio/proxy.py`

`io_transport` · `request handling`

Composio is used here as a safe middleman for credentials. Instead of this project storing a Google, Slack, or other provider token, it sends the intended request to Composio and asks Composio to make the real call using the connected account. This file is the adapter that makes that feel like ordinary HTTP to the rest of the code.

The main piece, ComposioProxyTransport, plugs into httpx, an HTTP client library. When code tries to send a provider request, the transport reads the method, full URL, selected headers, and body, wraps them in a JSON package, and sends that package to Composio's proxy endpoint. It deliberately skips headers such as authorization and content-length because those either belong to the real provider credential or must be recalculated.

When Composio replies, the transport rebuilds an httpx response with the provider's status code, headers, and body. If the provider returned large or non-JSON binary data, Composio may store it separately and return a link; this file turns that into a redirect response so the bytes can be fetched directly instead of filling the proxy's memory.

ComposioRequestForwarder provides the same idea for a broker forwarding path, with size and time limits so a slow or huge response cannot tie up the shared proxy process.

#### Function details

##### `ComposioProxyTransport.handle_async_request`  (lines 66–102)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: This is the main request rewrite step. It accepts a normal provider HTTP request, packages it as a Composio proxy-execute call, sends it through the inner transport, and returns a response shaped like the provider answered directly.

**Data flow**: It starts with an httpx request containing a method, URL, headers, body, and optional timeout. It reads the body, builds a JSON payload with the connected account id and request details, filters out headers that should not be forwarded, and sends a POST request to Composio. It then reads Composio's response, either returns a Composio error response as-is when the proxy call failed, or converts the proxy payload into a provider-style response.

**Call relations**: This is called by httpx when ComposioProxyTransport is used as the transport for a client, and it is also used directly by ComposioRequestForwarder.forward. During the flow it asks _read_bounded to safely read the broker response, then asks _provider_response to rebuild the provider response from Composio's JSON wrapper.

*Call graph*: calls 2 internal fn (_provider_response, _read_bounded); 4 external calls (Request, aread, Response, loads).


##### `ComposioProxyTransport._read_bounded`  (lines 104–119)

```
async def _read_bounded(self, response: httpx.Response) -> bytes
```

**Purpose**: This reads the full response from Composio while optionally enforcing a maximum size. The limit protects the shared proxy process from buffering an unexpectedly large response in memory.

**Data flow**: It receives an httpx response from the Composio broker. If no size cap is set, it simply reads and returns all bytes. If a cap is set, it reads chunk by chunk, adds each chunk to a growing buffer, and raises a ComposioError if the buffer grows past the allowed size, closing the response before failing.

**Call relations**: ComposioProxyTransport.handle_async_request calls this right after receiving the proxy-execute response. Its output is either the raw response bytes that the caller can inspect, or a clear failure when the broker response is too large.

*Call graph*: called by 1 (handle_async_request); 4 external calls (aclose, aiter_bytes, aread, ComposioError).


##### `ComposioProxyTransport._provider_response`  (lines 121–165)

```
def _provider_response(self, payload: dict[str, Any], request: httpx.Request) -> httpx.Response
```

**Purpose**: This turns Composio's proxy response format back into a normal HTTP response from the original provider. It hides Composio's wrapper so calling code can keep using provider-style status codes, headers, and bodies.

**Data flow**: It receives a decoded JSON payload from Composio and the original request. It peels away nested data wrappers, extracts the provider status and headers, removes body-specific headers that would no longer be reliable, and chooses a response body. JSON-like data is encoded as JSON, text data is encoded as text, missing data becomes an empty body, and binary_data becomes a redirect response pointing at Composio's stored file URL. If binary_data is malformed and has no usable URL, it raises a ComposioError.

**Call relations**: ComposioProxyTransport.handle_async_request calls this after a successful proxy-execute call. It is the final translation step before the rest of the connector sees the result, which matters because pagination and error handling often depend on provider headers and status codes.

*Call graph*: called by 1 (handle_async_request); 4 external calls (Response, dumps, cast, ComposioError).


##### `ComposioProxyTransport.aclose`  (lines 167–168)

```
async def aclose(self) -> None
```

**Purpose**: This closes the underlying HTTP transport. It is used to clean up network resources when the proxy transport is no longer needed.

**Data flow**: It takes no new request data. It calls close on the inner transport, which releases any connections or transport resources it owns, and returns nothing.

**Call relations**: ComposioRequestForwarder.forward calls this in its cleanup path after a forwarded request finishes or fails. It keeps the temporary transport from leaving open network resources behind.


##### `ComposioRequestForwarder.forward`  (lines 185–213)

```
async def forward(self, account_id: str, method: str, url: str, headers: Mapping[str, str], body: bytes) -> ForwardedResponse
```

**Purpose**: This forwards one outbound provider request through Composio for a CLI or broker path. It applies both a maximum response size and a wall-clock timeout so the shared proxy cannot be stuck by a huge or slowly trickling backend response.

**Data flow**: It receives an account id, HTTP method, URL, headers, and body bytes. It gets the configured Composio client and API key, builds a ComposioProxyTransport for that connected account, creates an httpx request with a timeout, and sends it through the transport. If the whole operation takes too long, it raises a ComposioError with a gateway-timeout style status. Otherwise it reads the response body, closes the transport, and returns a ForwardedResponse containing the final status, headers, and body.

**Call relations**: This is used by the broker forwarding path when a request needs to be executed under a Composio-granted account. It constructs ComposioProxyTransport for the actual proxy rewrite, relies on that transport to talk to Composio and reconstruct the provider response, then packages the result into ForwardedResponse for the caller.

*Call graph*: 8 external calls (__init__, __init__, timeout, AsyncHTTPTransport, Request, Timeout, ComposioError, composio_client).


### `extensions/composio/ufo_ext_composio/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder often needs an `__init__.py` file to be treated as an importable package, meaning other parts of the project can refer to code inside this folder using normal import paths. Think of it like a label on a drawer: the drawer may contain useful tools, but this label simply tells Python that the drawer exists and can be opened. Because the file has no code, it does not run setup steps, expose shortcuts, or change how the Composio extension works. Its importance is structural: without it, some Python environments or tools might not recognize `ufo_ext_composio` as a package, which could make imports fail.


### `extensions/composio/ufo_ext_composio/mcp_session.py`

`io_transport` · `request handling`

This file is a small bridge between this project and Composio's Tool Router. The Tool Router exposes searchable tools through MCP, which is a standard protocol for letting programs call tools through a client session. Here, the project only uses that route for searching tools, not for executing them.

The main job is simple: open a temporary HTTP-based MCP connection, call one named tool with the provided arguments, close the connection, and return the answer in a predictable form. This is like calling a help desk, asking one question, writing down the answer in a standard form, and hanging up.

The response from an MCP tool can arrive in a few different shapes. It may already contain parsed data, it may contain structured content, or it may contain a text block that happens to be JSON. This file checks those options in that order. If it finds a dictionary, it returns it directly. If it finds JSON text, it parses it. If the text is not JSON, it wraps the plain text in a dictionary. That fallback behavior matters because it keeps callers from crashing just because the remote service answered in a slightly different format.

The `Client` is imported at module level so tests can replace it with a fake client, avoiding real network calls.

#### Function details

##### `mcp_call_tool`  (lines 18–42)

```
async def mcp_call_tool(endpoint: str, tool: str, arguments: dict[str, Any], headers: dict[str, str], timeout_seconds: float) -> dict[str, object]
```

**Purpose**: Calls one MCP tool at a given HTTP endpoint and returns the result as a plain dictionary. Someone would use it when they want the rest of the code to receive a simple Python object instead of dealing with the details of MCP sessions and response formats.

**Data flow**: It takes an endpoint URL, a tool name, tool arguments, HTTP headers, and a timeout. It opens a streamable HTTP MCP client with those settings, sends the tool call, and waits for the result. After the session closes, it looks for the answer first as already-parsed data, then as structured content, then as JSON inside a text block. It returns a dictionary in all normal cases, wrapping plain text or non-dictionary results when needed.

**Call relations**: When higher-level Composio code needs to search through Tool Router, it can call this function as the network-facing step. Inside, this function creates a `StreamableHttpTransport` to describe how to reach the endpoint, passes that into `fastmcp.Client` to run the MCP session, and uses `json.loads` only if the answer comes back as text that might contain JSON.

*Call graph*: 3 external calls (Client, StreamableHttpTransport, loads).


### `extensions/composio/ufo_ext_composio/provider.py`

`io_transport` · `connect request handling`

OAuth is the common “Sign in and allow access” flow used by many services. ufo expects an OAuth provider to give it a normal authorization URL right away, but Composio needs an async API call to create that URL. This file solves that mismatch by sending the browser to a local extension route first.

The flow is like a reception desk forwarding a visitor. First, `ComposioOAuthProvider.authorize_url` does not send the user directly to Composio. Instead, it builds a URL to this extension’s `/ext/composio/oauth` route and includes the provider name, the protected state value, and ufo’s final callback URL.

When the browser reaches `oauth_route`, the route creates a Composio connect link for the current workspace and redirects the browser there. After the user approves, Composio redirects back to the same route with a `connected_account_id`. The route then forwards the browser to ufo’s real callback, passing that account id as the OAuth `code`.

Finally, `ComposioOAuthProvider.exchange` checks with Composio that the account belongs to this workspace’s Composio user and matches the expected provider before ufo records the connected account. The actual secret token stays inside Composio, so this extension stores only the account identity and optional label.

#### Function details

##### `ComposioOAuthProvider.authorize_url`  (lines 43–45)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds the first URL that the member’s browser should visit when starting a Composio-backed connection. Instead of going straight to Composio, it points to this extension’s own OAuth bridge route so the async Composio link can be created there.

**Data flow**: It receives ufo’s protected `state` value and final `redirect_uri`. It extracts the origin, meaning the scheme and host such as `https://example.com`, from the redirect URI, adds the provider name, state, and callback as query parameters, and returns a browser URL for `/ext/composio/oauth`.

**Call relations**: This is the start of the connect story. ufo’s connect registry calls it when it needs an authorization URL. It relies on `_origin` to keep the bridge URL on the same host as the callback, and the browser later lands on `oauth_route` with the values this function placed in the query string.

*Call graph*: calls 1 internal fn (_origin); 1 external calls (urlencode).


##### `ComposioOAuthProvider.exchange`  (lines 47–57)

```
async def exchange(self, code: str, _redirect_uri: str, workspace_id: UUID, _state: str) -> OAuthAccount
```

**Purpose**: Turns the returned Composio connected account id into ufo’s stored OAuth account record. It also verifies that the account id really belongs to the current workspace’s Composio user and to the expected provider, so a random or foreign account id cannot be silently bound.

**Data flow**: It receives the `code`, which in this flow is actually a Composio connected account id, plus the workspace id. It builds the expected Composio external user id for that workspace, asks the Composio client for the connected account, tries to fetch a human-friendly account label, and returns an `OAuthAccount` containing the account id and optional label. It does not read or store the provider’s secret token.

**Call relations**: This runs after `oauth_route` has forwarded the browser back to ufo’s core callback with the account id as the code. It asks `composio_client` to confirm the account and then hands ufo a safe account record to bind to the grant.

*Call graph*: 2 external calls (__init__, composio_client).


##### `oauth_route`  (lines 60–97)

```
async def oauth_route(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: Acts as the browser bridge for both halves of the Composio consent trip. It starts the Composio hosted consent page, and later receives Composio’s return redirect and forwards the result back into ufo’s normal callback flow.

**Data flow**: It reads query parameters from the incoming HTTP request. If `state` or `callback` is missing, it returns a bad-request response. If Composio has returned a `connected_account_id`, it redirects to the original callback with that id as `code` and keeps the same `state`. If Composio returned a failure `status` without an account id, it returns an error message instead of starting over. If this is the first visit, it reads the provider, builds a return URL back to this route, asks Composio for a connect link scoped to the current workspace, and redirects the browser to that Composio link.

**Call relations**: The browser reaches this route because `ComposioOAuthProvider.authorize_url` pointed it here. On the start leg, it calls the Composio client to create the hosted consent link. On the return leg, it sends the browser onward to ufo’s core callback, which will then lead to `ComposioOAuthProvider.exchange`.

*Call graph*: calls 1 internal fn (_origin); 3 external calls (Response, composio_client, urlencode).


##### `_origin`  (lines 100–104)

```
def _origin(url: str) -> str
```

**Purpose**: Extracts the safe base origin from a full URL, such as turning `https://host/path?x=1` into `https://host`. This is used so bridge URLs are built on the same scheme and host as the trusted callback.

**Data flow**: It receives a URL string, parses it, checks that it has an `http` or `https` scheme and a host name, and returns just `scheme://host`. If the URL is missing those required parts, it raises an error instead of building a broken or unsafe bridge URL.

**Call relations**: Both `ComposioOAuthProvider.authorize_url` and `oauth_route` call this helper before building redirect URLs. It is a small guardrail that keeps the OAuth bridge anchored to a valid web origin.

*Call graph*: called by 2 (authorize_url, oauth_route); 1 external calls (urlparse).


### Generic connector objects and tools
Generic connector extensions model connected accounts as workspace objects and give agents safe discovery, execution, file transfer, and revocation flows.

### `extensions/connectors/ufo_ext_connectors/__init__.py`

`other` · `import time`

This is an empty Python package marker file. In Python, a folder with an `__init__.py` file is treated as an importable package, meaning other parts of the project can refer to modules inside it using package-style names. Think of it like putting a label on a drawer: the drawer may contain useful tools elsewhere, but this label is what lets the rest of the system find and open it in an organized way. Because the file is empty, it does not run setup code, expose shortcuts, or change how the connector modules behave. Its value is structural: without it, depending on the Python version and packaging setup, imports involving `extensions.connectors.ufo_ext_connectors` might fail or behave differently.


### `extensions/connectors/ufo_ext_connectors/objects.py`

`domain_logic` · `request handling`

A connector is an outside account, such as an account with another provider, that a workspace member has authorized. This file turns those connections into two kinds of visible workspace objects. A `connection` is the underlying member-owned account link. A `connector_grant` is one agent's permission to use that connection. The split matters: disconnecting a connection removes the account for everyone, while deleting a grant only removes one agent's access.

The file is like a reception desk for account access. It does not perform the original account sign-in itself; that must happen through `connect_account`, because it involves a third party's consent flow. Instead, it lists existing connections and grants, shows their details, and routes safe changes to the grants service.

`ConnectionObjects` exposes connected accounts. It can list them, show details, provide status information, and disconnect them. It refuses attempts to create or edit a connection directly.

`ConnectorGrantObjects` exposes agent access edges. It can attach an existing connection to an agent, flip whether that grant is shared, or revoke it. It also creates links that explain what agent the grant belongs to and, when private, which connection it opens. The gate messages and speaker checks are important: they stop silent or unauthorized changes to credentials.

#### Function details

##### `_AccountSummary.provider`  (lines 51–51)

```
def provider(self) -> str
```

**Purpose**: This protocol property says that any account summary used here must expose the name of the outside provider. It lets helper code work with both connection summaries and grant summaries without caring which exact kind it received.

**Data flow**: An account-like summary object comes in conceptually → code reads its provider name → the provider string is available for building a stable object name.

**Call relations**: The `_named` helper relies on this property when it turns summary rows into object names. The property is part of the small shared shape that connection and grant summaries must fit.


##### `_AccountSummary.account_id`  (lines 54–54)

```
def account_id(self) -> str
```

**Purpose**: This protocol property says that any account summary used here must expose the account identifier from the outside provider. Together with the provider name, it uniquely names the account object in the workspace.

**Data flow**: An account-like summary object comes in conceptually → code reads its account ID → that ID is paired with the provider to form a workspace object name.

**Call relations**: The `_named` helper uses this property alongside `_AccountSummary.provider`. This is what allows both connection rows and grant rows to be named in the same way.


##### `_named`  (lines 57–58)

```
def _named(rows: tuple[SummaryT, ...]) -> dict[str, SummaryT]
```

**Purpose**: This helper gives account summary rows their workspace object names. It uses the provider and account ID to build the same kind of name everywhere, so connections and grants can be found consistently.

**Data flow**: It receives a tuple of summary rows, each with a provider and account ID → it asks `account_object_name` to turn each provider/account pair into a name → it returns a dictionary from that name to the original row.

**Call relations**: Both `ConnectionObjects._member_rows` and `ConnectorGrantObjects._member_rows` call this before presenting rows to the object system. It is the shared naming step that keeps the two object lists aligned.

*Call graph*: called by 2 (_member_rows, _member_rows); 1 external calls (account_object_name).


##### `ConnectionObjects._member_rows`  (lines 69–83)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: This function lists connected provider accounts as rows that the workspace object system can show to a member. Each row includes a human-readable summary and ownership information.

**Data flow**: It asks the grants layer for current connection summaries → turns each provider/account pair into a stable object name → wraps each result with a short description and a generated owner record → returns the finished tuple of rows.

**Call relations**: The member-readable object framework calls this when it needs to list `connection` objects. It uses `_named` for naming and the grant summary service for the source data, then hands back `OwnedRow` records for the broader object system to display.

*Call graph*: calls 1 internal fn (_named); 3 external calls (__init__, __init__, connection_summaries).


##### `ConnectionObjects._member_object`  (lines 85–103)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[ConnectionSpec] | None
```

**Purpose**: This function loads the detailed object view for one connected account. It confirms the stored generation still matches a real connection before returning provider, account ID, and timestamps.

**Data flow**: It receives an object name and owner record → fetches current connection summaries → finds the summary whose ID matches the owner generation → returns an `ObjectDetail` with a `ConnectionSpec`, or returns nothing if the connection no longer exists.

**Call relations**: The object system calls this after a particular `connection` row is selected or fetched. It depends on the same connection summary source as the listing function, but returns the richer detail view.

*Call graph*: 3 external calls (__init__, __init__, connection_summaries).


##### `ConnectionObjects._status`  (lines 105–118)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: This function returns operational status for a connected account, such as who owns it, what host it belongs to, and which agents currently use it. It is meant for status-style inspection rather than editing.

**Data flow**: It receives the tool context, object name, and owner record → looks up the current connection by its generation ID → if found, returns a plain dictionary of status values; if not found, returns nothing.

**Call relations**: The tool/object layer calls this when it needs live status for a `connection`. It reads from `connection_summaries` and does not hand off to mutation code, because it only reports the current state.

*Call graph*: 1 external calls (connection_summaries).


##### `ConnectionObjects._apply_owned`  (lines 120–128)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ConnectionSpec, old: ConnectionSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: This function deliberately refuses direct creation or editing of a connection object. That protects the consent flow, because connecting an outside account must go through `connect_account` rather than a normal object apply.

**Data flow**: It receives the requested connection spec and any old object state → ignores the requested change as unsafe for this path → raises a clear 'not supported' error explaining that account connection requires the dedicated flow.

**Call relations**: The object framework calls this when someone tries to apply a `connection` object. Instead of changing anything, it stops the flow immediately with `VerbNotSupported`.

*Call graph*: 1 external calls (__init__).


##### `ConnectionObjects._delete_owned`  (lines 130–140)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: This function disconnects a connected account. Deleting a `connection` is a broad action: it removes the account link and therefore cuts it off from every agent that had access.

**Data flow**: It receives the tool context, object name, and owner record → checks that the grants service is available and that there is a speaking member to act as the person making the request → asks the grants service to disconnect the connection by generation ID → finishes if successful, or raises an error if the connection changed or could not be disconnected.

**Call relations**: The object framework calls this when an authorized user deletes a `connection`. It hands the actual credential-side work to `ctx.grants.disconnect`, using the current speaker as the actor for auditing and permission checks.


##### `ConnectorGrantObjects._admin_can_apply`  (lines 151–152)

```
def _admin_can_apply(self, old: ConnectorGrantSpec, spec: ConnectorGrantSpec) -> bool
```

**Purpose**: This function defines the one grant edit an admin is allowed to make without being the connection owner: turning a shared grant back to private. It prevents admins from using the same path to share someone else's connection more widely.

**Data flow**: It receives the old grant spec and the requested new spec → checks whether the old grant was shared and whether the only requested change is `shared: false` → returns true only for that exact make-private change.

**Call relations**: The surrounding member-readable object framework uses this permission hook when deciding whether an admin may apply an update. It relies on `model_copy` to compare the requested spec against the old spec with only the sharing flag changed.

*Call graph*: 1 external calls (model_copy).


##### `ConnectorGrantObjects._member_rows`  (lines 154–171)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: This function lists agent access grants as workspace object rows. Each row says which provider account is involved and whether that access is shared or private.

**Data flow**: It asks the grants layer for current grant summaries → builds a stable account-style name for each row → creates a row with a readable summary and owner information, including whether it is shared → returns the tuple of rows.

**Call relations**: The object system calls this when it needs to list `connector_grant` objects. It uses `_named` for consistent names and `grant_summaries` as the live source of grant information.

*Call graph*: calls 1 internal fn (_named); 3 external calls (__init__, __init__, grant_summaries).


##### `ConnectorGrantObjects._member_object`  (lines 173–214)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[ConnectorGrantSpec] | None
```

**Purpose**: This function builds the detailed view of one agent's grant to use a connected account. It also adds links that show which agent the grant is scoped to and, for private grants, which connection it gives access to.

**Data flow**: It receives an object name and owner record → fetches current grant summaries → finds the matching grant by generation ID → returns a detail object containing provider, account ID, sharing state, timestamps, and relationship links; if the grant is gone, it returns nothing.

**Call relations**: The object framework calls this when a `connector_grant` is fetched in detail. It reads from `grant_summaries`, builds object references for the related agent and sometimes the related connection, and hands those links back as part of `ObjectDetail`.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, account_object_name, grant_summaries).


##### `ConnectorGrantObjects._status`  (lines 216–230)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: This function reports live status for a connector grant. It tells the caller who owns the underlying access, where it is hosted, which agent has the grant, and whether it is shared.

**Data flow**: It receives the tool context, object name, and owner record → searches current grant summaries for the matching generation ID → returns a simple status dictionary if found, or nothing if the grant no longer exists.

**Call relations**: The tool/object layer calls this for status inspection of a `connector_grant`. It reads from `grant_summaries` and returns plain values without changing the grant.

*Call graph*: 1 external calls (grant_summaries).


##### `ConnectorGrantObjects._apply_owned`  (lines 232–280)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ConnectorGrantSpec, old: ConnectorGrantSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: This function applies safe changes to an agent's connector grant. It can attach an already connected account to an agent, or change only the grant's shared/private flag; it refuses attempts to create a brand-new outside account through this path.

**Data flow**: It receives the requested grant spec, any old spec, and any owner record → if this is a new grant, it requires a grants service and a speaking member, then asks the grants service to attach an existing connection to the current conversation's agent access → if this is an update, it reloads the current grant, verifies that provider and account are not being changed, then updates only the sharing flag when needed → it returns no value, but it may change grant access through the grants service or raise an error if the request is unsafe or stale.

**Call relations**: The object framework calls this when someone applies a `connector_grant`. It may consult `grant_summaries` to check current state, uses `model_copy` to ensure only sharing changed, raises `VerbNotSupported` for connection-creation attempts, and hands real attach or share changes to the grants service on the tool context.

*Call graph*: 4 external calls (__init__, __init__, model_copy, grant_summaries).


##### `ConnectorGrantObjects._delete_owned`  (lines 282–292)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: This function revokes one agent's access to a connected account. Unlike deleting a connection, it leaves the underlying account connection and other agents' grants in place.

**Data flow**: It receives the tool context, object name, and owner record → checks that the grants service exists and that a speaking member is present → asks the grants service to revoke this grant by generation ID → finishes if successful, or raises an error if the grant changed or could not be revoked.

**Call relations**: The object framework calls this when an authorized user deletes a `connector_grant`. It delegates the actual revocation to `ctx.grants.revoke`, using the speaker member as the actor for permission and audit purposes.


### `extensions/connectors/ufo_ext_connectors/tools.py`

`orchestration` · `request handling`

A connector is a bridge to an outside service. This file is the agent’s public control panel for those bridges: search for available connectors, discover what a connector can do, and run one of its tools using a connected account. Without it, the agent would either need thousands of fixed tool definitions up front, or it could not safely call outside services at all.

The file works like a reception desk for many specialist offices. First, it checks the current turn’s connector registry, which says which providers are available. It can list matching connectors, ask a provider’s broker for real tool schemas, and execute a chosen tool through that broker. The broker keeps the user’s account token, so this code does not inject credentials itself.

It also handles the messy parts around files and large results. If an argument points at a workspace file, the file is uploaded from inside the sandbox to the broker’s file store. If a connector returns files, they are downloaded back into a safe workspace folder. If a provider returns base64 text, meaning bytes encoded as printable text, this file decodes it: small UTF-8 text stays inline, while large or binary data is written to a workspace file. Finally, it collapses repeated JSON objects into references, so bulky repeated records do not push useful results out of context.

One special case is Slack: messages sent through a connector get a small “Sent using ufo” attribution footer, unless one is already present.

#### Function details

##### `list_external_tools`  (lines 254–277)

```
async def list_external_tools(ctx: ToolContext, args: ListExternalToolsInput) -> ToolResult
```

**Purpose**: Searches the connectors available in the current turn and returns matching connector providers, not the individual tools inside them. It lets the agent ask “do I have a GitHub-like connector?” before trying to discover exact actions.

**Data flow**: It receives the tool context and search queries. It reads the connector registry from the context, compares the queries with local provider IDs and labels, also asks the broker catalog for matches, removes duplicates, and returns a JSON tool result containing connector IDs and labels.

**Call relations**: This is one of the public connector tools. It starts by using _registry to get the live connector list, uses asyncio.gather to query broker catalogs in parallel, and finishes by handing the response to _json_result.

*Call graph*: calls 2 internal fn (_json_result, _registry); 1 external calls (gather).


##### `describe_external_tools`  (lines 280–302)

```
async def describe_external_tools(ctx: ToolContext, args: DescribeExternalToolsInput) -> ToolResult
```

**Purpose**: Describes real tools inside one connector. It is used before execution so the agent can learn exact tool names and input shapes instead of guessing.

**Data flow**: It receives a connector ID, optional exact tool names, and/or a discovery query. It asks the connector broker for schemas for named tools, records any names that are not found, optionally searches the connector catalog, and returns JSON with schemas, available tools, notes, and unresolved names.

**Call relations**: This public discovery tool uses _registry to find the connector entry, _tool_json to simplify broker tool objects, _discovery_query to turn failed guesses into search terms, _discovered_rows to prepare a useful listing, and _json_result to return the final payload.

*Call graph*: calls 5 internal fn (_discovered_rows, _discovery_query, _json_result, _registry, _tool_json).


##### `attribution_stripped`  (lines 305–309)

```
def attribution_stripped(text: str) -> str
```

**Purpose**: Removes any ufo Slack attribution text from a message body. This is useful when reading Slack messages back in, so the agent does not mistake its own footer for something a person wrote.

**Data flow**: It receives a text string. It applies the attribution-matching pattern and returns the same text with matching footer fragments removed.

**Call relations**: This helper stands on its own in this file. It complements the Slack attribution-writing path by cleaning up messages when they are read elsewhere.


##### `attributed_arguments`  (lines 312–339)

```
def attributed_arguments(arguments: dict[str, JsonValue], subject: str) -> dict[str, JsonValue]
```

**Purpose**: Adds a small attribution footer to Slack message-send arguments. It preserves the caller’s existing message format where possible and avoids adding a second footer if one is already present.

**Data flow**: It receives a dictionary of connector tool arguments and the attribution subject to write. It checks whether any existing value already carries the footer, then either appends a footer block to existing Slack blocks or converts text/markdown arguments into blocks followed by the footer. It returns a new argument dictionary, or the original one if no safe message body is found.

**Call relations**: slack_attributed calls this when a connector call looks like a Slack message send. It delegates format-specific work to _appended_blocks and _body_blocks, and uses _carries_attribution as the guard against stacked footers.

*Call graph*: calls 3 internal fn (_appended_blocks, _body_blocks, _carries_attribution); called by 1 (slack_attributed).


##### `_body_blocks`  (lines 342–370)

```
def _body_blocks(arguments: dict[str, JsonValue]) -> list[JsonValue] | None
```

**Purpose**: Turns Slack text-style message arguments into Slack block objects that can be followed by the attribution footer. It keeps Markdown and Slack mrkdwn in the block types that render them correctly.

**Data flow**: It reads the arguments dictionary. If markdown_text is present, it creates one markdown block; if text is present, it splits it into section blocks small enough for Slack’s per-block limit. If there is no usable body, it returns None.

**Call relations**: attributed_arguments calls this when there are no existing blocks to append to. The result becomes the message body immediately before the attribution footer is added.

*Call graph*: called by 1 (attributed_arguments).


##### `_appended_blocks`  (lines 373–394)

```
def _appended_blocks(value: JsonValue, footer: dict[str, JsonValue]) -> JsonValue | None
```

**Purpose**: Adds an attribution footer to an existing Slack blocks argument when that argument can be understood safely. It supports both a real list of blocks and a JSON string version of the list.

**Data flow**: It receives the existing blocks value and a footer block. If the value is a non-empty list, it returns a new list with the footer added. If it is a string, it tries to parse it as JSON, including URL-decoded JSON, checks it is a list without an attribution already, then returns it serialized in the same style. If parsing fails, it returns None.

**Call relations**: attributed_arguments calls this before trying to build blocks from text. It uses _carries_attribution to avoid duplicate footers and JSON/URL helpers to preserve the broker’s accepted spelling.

*Call graph*: calls 1 internal fn (_carries_attribution); called by 1 (attributed_arguments); 4 external calls (dumps, loads, quote, unquote).


##### `_carries_attribution`  (lines 397–406)

```
def _carries_attribution(value: JsonValue) -> bool
```

**Purpose**: Checks whether a nested value already contains a ufo attribution footer. This prevents repeated sends or edits from stacking the same footer over and over.

**Data flow**: It receives any JSON-like value. It searches strings directly, walks lists item by item, walks dictionaries through their values, and returns true as soon as it finds a matching footer.

**Call relations**: attributed_arguments uses it before changing arguments, and _appended_blocks uses it after parsing block strings. It is the shared “already marked?” test for the Slack attribution flow.

*Call graph*: called by 2 (_appended_blocks, attributed_arguments); 1 external calls (values).


##### `slack_attributed`  (lines 409–423)

```
def slack_attributed(provider: str, slug: str, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Decides whether a connector call should get Slack attribution. Only Slack tools whose names look like message sends are changed; reads, edits, listings, and non-Slack providers are left alone.

**Data flow**: It receives a provider name, tool slug, and arguments. It checks for the Slack provider and for message-send words in the slug. If the call qualifies, it returns attributed arguments; otherwise it returns the original arguments.

**Call relations**: call_external_tool calls this just before executing a connector tool. When a footer is needed, it hands the actual rewriting to attributed_arguments.

*Call graph*: calls 1 internal fn (attributed_arguments); called by 1 (call_external_tool).


##### `call_external_tool`  (lines 426–431)

```
async def call_external_tool(ctx: ToolContext, args: CallExternalToolInput) -> ToolResult
```

**Purpose**: Runs one external connector tool using the correct connected account. This is the execution endpoint after discovery has found a real tool name and schema.

**Data flow**: It receives the context and execution arguments. It finds the connector entry, resolves which connected account to use, adds Slack attribution when needed, builds a _ConnectorCall helper, runs it, and wraps the returned text in a ToolResult.

**Call relations**: This is the public execution tool. It uses _registry to find the connector, ToolContext.connector_account to choose the account, slack_attributed for Slack sends, and _ConnectorCall.run for the full upload-execute-download-result-cleanup flow.

*Call graph*: calls 3 internal fn (connector_account, _registry, slack_attributed); 3 external calls (__init__, __init__, __init__).


##### `_ConnectorCall.run`  (lines 459–472)

```
async def run(self, arguments: dict[str, JsonValue], account_id: str) -> str
```

**Purpose**: Carries out one connector tool call from start to finish. It prepares file arguments, executes the broker tool, brings back produced files, decodes embedded data, and shrinks repeated result objects.

**Data flow**: It receives tool arguments and an account ID. It walks arguments to stage workspace files, sends the staged arguments to the broker execute API, fetches any returned file outputs, translates base64-heavy result content, optionally adds workspace file listings, deduplicates the payload in a worker thread, and returns a JSON string.

**Call relations**: call_external_tool creates the _ConnectorCall and invokes this method. This method coordinates _staged_value, _fetched_files, _translated_node, and _deduped in the order needed for a safe connector execution.

*Call graph*: calls 3 internal fn (_fetched_files, _staged_value, _translated_node); 1 external calls (to_thread).


##### `_ConnectorCall._staged_value`  (lines 474–489)

```
async def _staged_value(self, value: object) -> object
```

**Purpose**: Walks an argument value and replaces workspace-file references with broker-ready uploaded-file references. It lets connector tools receive files without exposing local paths directly.

**Data flow**: It receives one argument value. If the value is exactly a dictionary containing the workspace_file key, it validates the path and stages that file. If the value is a dictionary or list, it recursively processes its children. Other values pass through unchanged.

**Call relations**: _ConnectorCall.run calls this on every top-level argument before broker execution. When it finds an actual workspace file reference, it hands off to _stage_file.

*Call graph*: calls 1 internal fn (_stage_file); called by 1 (run).


##### `_ConnectorCall._stage_file`  (lines 491–527)

```
async def _stage_file(self, path: str) -> dict[str, object]
```

**Purpose**: Uploads one workspace file to the broker’s file store and returns the argument value the broker expects. It also checks size and path safety before upload.

**Data flow**: It receives a workspace path. It normalizes it, asks the sandbox to hash and measure the file through a containment guard, rejects missing or oversized files, asks the broker for an upload location, uploads the bytes with curl from inside the sandbox when needed, and returns the broker-supplied argument reference.

**Call relations**: _staged_value calls this whenever it finds a workspace_file argument. It relies on sandbox commands for file access and upload so the serve process does not stream the file bytes itself.

*Call graph*: called by 1 (_staged_value); 4 external calls (guess_type, PurePosixPath, quote, workspace_path).


##### `_ConnectorCall._fetched_files`  (lines 529–562)

```
async def _fetched_files(self, files: tuple[BrokerFile, ...]) -> list[dict[str, str]]
```

**Purpose**: Downloads files produced by a connector tool into the workspace. It gives the agent safe local paths for files that live temporarily behind broker-provided URLs.

**Data flow**: It receives broker file records containing names and URLs. For each one, it cleans the filename, creates a unique safe target path, claims that path through the sandbox, downloads the file with curl under a size limit, and returns a list of saved file names and workspace paths.

**Call relations**: _ConnectorCall.run calls this after broker execution, using the broker’s file_outputs result. It is the inbound file-transfer counterpart to _stage_file.

*Call graph*: called by 1 (run); 3 external calls (quote, contained_leaf, uuid4).


##### `_ConnectorCall._translated_node`  (lines 564–624)

```
async def _translated_node(self, node: Mapping[str, object], depth: int=0) -> dict[str, object]
```

**Purpose**: Looks inside one result object for provider-marked base64 fields and converts them into readable text or workspace file references. This keeps huge encoded blobs from filling the conversation.

**Data flow**: It receives a mapping from the connector result and a recursion depth. It first recursively translates child values, then checks whether the object declares base64 encoding, tries to decode marked content fields, chooses a filename and mimetype, converts each decoded field through _translated_bytes, and updates the encoding marker when all marked fields were converted.

**Call relations**: _ConnectorCall.run starts result translation here, and _translated calls it for nested dictionaries. It uses _decoded_base64 to verify encoded fields and _translated_bytes to decide whether decoded data stays inline or becomes a file.

*Call graph*: calls 3 internal fn (_translated, _translated_bytes, _decoded_base64); called by 2 (_translated, run); 1 external calls (guess_type).


##### `_ConnectorCall._translated`  (lines 626–645)

```
async def _translated(self, value: object, depth: int) -> object
```

**Purpose**: Recursively translates any value inside a connector result. It walks dictionaries and lists, and also recognizes standalone data URLs that contain base64 data.

**Data flow**: It receives a value and the current depth. If the depth limit is reached, it returns the value unchanged. Dictionaries go to _translated_node, lists are walked item by item, data URL strings go to _translated_data_url, and all other values pass through.

**Call relations**: _translated_node calls this for children, making it the general recursive walker. It hands special dictionary work back to _translated_node and data URL work to _translated_data_url.

*Call graph*: calls 2 internal fn (_translated_data_url, _translated_node); called by 1 (_translated_node).


##### `_ConnectorCall._translated_data_url`  (lines 647–659)

```
async def _translated_data_url(self, value: str) -> object
```

**Purpose**: Converts a single data URL containing base64 payload into readable text or a workspace file reference. A data URL is a string that embeds content directly, like an inline file.

**Data flow**: It receives a string. It checks whether it matches the expected data:<mime>;base64,<payload> shape, decodes the payload if valid, chooses a fallback filename extension from the MIME type, and passes the bytes to _translated_bytes. If anything does not match or decode, it returns the original string.

**Call relations**: _translated calls this when it sees a short enough string beginning with data:. It uses _decoded_base64 for strict decoding and _translated_bytes for the final inline-or-file choice.

*Call graph*: calls 2 internal fn (_translated_bytes, _decoded_base64); called by 1 (_translated); 1 external calls (guess_extension).


##### `_ConnectorCall._translated_bytes`  (lines 661–670)

```
async def _translated_bytes(self, decoded: bytes, text: str | None, name: str, mimetype: str) -> object
```

**Purpose**: Chooses how decoded bytes should appear in the tool result. Small UTF-8 text stays readable inline; binary or large content is written to a workspace file.

**Data flow**: It receives decoded bytes, optional decoded text, a filename, and a mimetype. If text exists and is within the inline character limit, it returns the text. Otherwise it offloads the bytes and returns the workspace file reference.

**Call relations**: _translated_node and _translated_data_url call this after successful base64 decoding. When content should become a file, it delegates to _offloaded.

*Call graph*: calls 1 internal fn (_offloaded); called by 2 (_translated_data_url, _translated_node).


##### `_ConnectorCall._offloaded`  (lines 672–714)

```
async def _offloaded(self, name: str, mimetype: str, data: bytes) -> dict[str, object]
```

**Purpose**: Writes decoded result bytes into the workspace and returns a compact reference to them. This is used for large text or binary content that should not be pasted into the model’s context.

**Data flow**: It receives a name, mimetype, and bytes. It cleans the filename, builds a content-addressed path using a SHA-256 hash of the bytes, writes to a temporary file through the sandbox, safely places it at the final path, and returns name, workspace path, mimetype, and byte count.

**Call relations**: _translated_bytes calls this whenever decoded content is not suitable to inline. It uses the sandbox write path and placement guard to avoid unsafe overwrites or symlink tricks.

*Call graph*: called by 1 (_translated_bytes); 3 external calls (sha256, contained_leaf, uuid4).


##### `_ConnectorCall._deduped`  (lines 716–774)

```
def _deduped(self, payload: dict[str, object]) -> str
```

**Purpose**: Serializes a connector result and replaces repeated large objects with references to their first copy. This keeps denormalized API responses smaller without deleting information.

**Data flow**: It receives the final payload dictionary. It serializes it once, skips deduplication if it is too large, too structurally dense, or already contains the reserved same_as key, otherwise walks the payload and replaces later repeated objects with JSON Pointer references, then returns the JSON string.

**Call relations**: _ConnectorCall.run calls this at the end inside asyncio.to_thread so the main event loop is not held up. It uses _condensed for the structural walk and _escaped to build safe JSON Pointer paths.

*Call graph*: calls 2 internal fn (_condensed, _escaped); 1 external calls (dumps).


##### `_ConnectorCall._condensed`  (lines 776–852)

```
def _condensed(self, value: object, pointer: str, depth: int, first: dict[bytes, str]) -> tuple[object, bytes, int]
```

**Purpose**: Walks one part of a result and identifies repeated dictionary objects by their structure and contents. It returns either the original shape, a condensed shape, or a same_as pointer for repeated objects.

**Data flow**: It receives a value, its JSON Pointer path, current depth, and a table of first-seen object digests. It recursively computes hashes and estimated JSON sizes for dictionaries, lists, strings, and other leaves. Large repeated dictionaries become same_as references; lists and small objects are not replaced.

**Call relations**: _deduped calls this for each top-level payload value. It calls itself recursively, uses _escaped for child pointer paths, and hashes content with SHA-256 so repeated structures can be recognized safely.

*Call graph*: calls 1 internal fn (_escaped); called by 1 (_deduped); 1 external calls (sha256).


##### `_escaped`  (lines 855–858)

```
def _escaped(token: str) -> str
```

**Purpose**: Escapes one path segment for a JSON Pointer. This makes keys containing slash or tilde still point to the exact original field.

**Data flow**: It receives a string key. It replaces ~ with ~0 and / with ~1, then returns the escaped token.

**Call relations**: _deduped and _condensed use this while building same_as pointer paths. It is a small helper that keeps deduplication references unambiguous.

*Call graph*: called by 2 (_condensed, _deduped).


##### `_decoded_base64`  (lines 861–885)

```
def _decoded_base64(value: object) -> tuple[bytes, str | None] | None
```

**Purpose**: Strictly decodes a value that a provider claims is base64. It refuses invalid or oversized input rather than guessing, so ordinary strings are not accidentally corrupted.

**Data flow**: It receives any value. If it is not a string or is too long, it returns None. Otherwise it removes whitespace, tries strict base64 decoding, then tries to decode the bytes as UTF-8 text. It returns the bytes plus text when text is possible, or bytes plus None for binary data.

**Call relations**: _translated_node and _translated_data_url call this before rewriting connector results. It is the safety gate for all base64 translation in this file.

*Call graph*: called by 2 (_translated_data_url, _translated_node); 1 external calls (b64decode).


##### `search_connector_tools`  (lines 888–902)

```
async def search_connector_tools(ctx: ToolContext, args: SearchConnectorToolsInput) -> ToolResult
```

**Purpose**: Runs a richer search for tools inside one connector using a natural-language goal. It can return matching tools plus broker-provided advice such as suggested plans, guidance, and pitfalls.

**Data flow**: It receives a connector ID and query. It finds the connector entry, asks the broker search API for matching tools and advice, formats the tool rows with fallback behavior, adds any note, and returns the whole result as JSON.

**Call relations**: This is a public discovery tool alongside describe_external_tools. It uses _registry to get the connector, _discovered_rows to keep discovery useful even when no match is found, and _json_result to produce the tool result.

*Call graph*: calls 3 internal fn (_discovered_rows, _json_result, _registry).


##### `_registry`  (lines 905–908)

```
def _registry(ctx: ToolContext) -> ConnectorRegistry
```

**Purpose**: Fetches the current turn’s connector registry from the tool context. It fails loudly if connector tools were invoked without a registry.

**Data flow**: It receives the tool context. If ctx.connectors exists, it returns it; otherwise it raises a runtime error explaining that the registry is missing.

**Call relations**: All public connector tools call this before doing connector work: list_external_tools, describe_external_tools, search_connector_tools, and call_external_tool. It is the common doorway into live connector state.

*Call graph*: called by 4 (call_external_tool, describe_external_tools, list_external_tools, search_connector_tools).


##### `_tool_json`  (lines 911–912)

```
def _tool_json(tool: BrokerTool) -> dict[str, object]
```

**Purpose**: Turns a broker tool object into the plain JSON shape returned to the agent. It keeps only the slug, description, and input schema.

**Data flow**: It receives a BrokerTool. It reads its slug, description, and input_schema fields and returns them in a dictionary.

**Call relations**: describe_external_tools uses this for exact schema results, and _available_tools uses it while building discovery listings.

*Call graph*: called by 2 (_available_tools, describe_external_tools).


##### `_discovered_rows`  (lines 915–932)

```
async def _discovered_rows(entry: ConnectorEntry, workspace_id: UUID, query: str, found: tuple[BrokerTool, ...]) -> tuple[list[dict[str, object]], str]
```

**Purpose**: Builds the list of discovered tool rows and any note explaining fallback or omission. It makes sure a search that finds nothing can still show the connector’s top tools rather than becoming a dead end.

**Data flow**: It receives a connector entry, workspace ID, query, and broker-found tools. If a non-empty query found no tools, it asks the broker for the unfiltered top tools instead. It trims the listing through _available_tools and returns rows plus notes about fallback or omitted rows.

**Call relations**: describe_external_tools and search_connector_tools both call this, so both discovery paths follow the same fallback rule. It delegates row-size budgeting to _available_tools.

*Call graph*: calls 1 internal fn (_available_tools); called by 2 (describe_external_tools, search_connector_tools).


##### `_available_tools`  (lines 935–948)

```
def _available_tools(listed: tuple[BrokerTool, ...]) -> list[dict[str, object]]
```

**Purpose**: Chooses how many discovered tool rows can fit comfortably inline. This avoids returning such a large catalog that the engine has to move it out to a file.

**Data flow**: It receives a tuple of broker tools. It converts each tool with _tool_json, estimates the JSON size, keeps adding rows until the character budget would be exceeded after at least one row, and returns the selected rows.

**Call relations**: _discovered_rows calls this for both normal search results and fallback top-tool listings. It uses json.dumps only to estimate how much inline space each row costs.

*Call graph*: calls 1 internal fn (_tool_json); called by 1 (_discovered_rows); 1 external calls (dumps).


##### `_discovery_query`  (lines 951–958)

```
def _discovery_query(explicit: str, unresolved: list[str]) -> str
```

**Purpose**: Chooses the query used for discovery when exact tool names were not found. It turns guessed slugs into readable search words so the broker can suggest real tools.

**Data flow**: It receives an explicit query and a list of unresolved tool names. If the explicit query is present, it returns that. Otherwise it lowercases unresolved names, replaces non-alphanumeric separators with spaces, removes duplicate words while keeping order, and returns the resulting search string.

**Call relations**: describe_external_tools calls this when it needs to search after unresolved names or when tool names were omitted. It uses regular-expression cleanup to turn machine-like slugs into human-like keywords.

*Call graph*: called by 1 (describe_external_tools); 1 external calls (sub).


##### `_json_result`  (lines 961–962)

```
def _json_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: Wraps a dictionary as a JSON text ToolResult. It is the common final step for discovery-style connector tools.

**Data flow**: It receives a payload dictionary. It serializes it with json.dumps, places the text in a TextContent object, wraps that in a ToolResult, and returns it.

**Call relations**: list_external_tools, describe_external_tools, and search_connector_tools all call this to return their responses in the same format. call_external_tool builds its ToolResult directly because _ConnectorCall.run already returns serialized text.

*Call graph*: called by 3 (describe_external_tools, list_external_tools, search_connector_tools); 3 external calls (__init__, __init__, dumps).


### MCP server tools
Workspace-configured MCP servers are queried for available tools and invoked with bounded responses and clear errors.

### `extensions/mcp/ufo_ext_mcp.py`

`io_transport` · `request handling`

MCP, or Model Context Protocol, is a way for outside services to publish tools that an agent can discover and use. This file is the bridge between UFO and those outside MCP servers. Without it, a workspace could not plug in its own MCP tool server and have the agent browse or call those tools at runtime.

The file exposes two UFO tools. The first, list_mcp_tools, asks a named MCP server for its tool catalog. To avoid flooding the agent with huge JSON schemas, it first returns a small menu: tool names, short summaries, parameter names, required parameters, and whether each tool is marked safe to repeat. If the agent wants to call a tool, it can ask again for the full schema of just that tool.

The second tool, call_mcp_tool, invokes one named tool with JSON arguments. Before sending anything, it checks that the request is not too large. After receiving a response, it also checks the size before returning it. This is like a receptionist who first checks the directory, then places a call, but refuses packages that are too large to carry safely.

Server details come from a workspace credential named mcp_servers. URLs are validated to be HTTP or HTTPS, and optional authentication is sent as a Bearer token. Results are treated as untrusted because they come from external systems.

#### Function details

##### `McpServer._http_url`  (lines 77–80)

```
def _http_url(cls, value: str) -> str
```

**Purpose**: This validates that a configured MCP server address starts with http:// or https://. It prevents the extension from trying to use unsupported or surprising kinds of URLs.

**Data flow**: It receives the URL string from the workspace's MCP server configuration. It checks the string against the allowed HTTP/HTTPS pattern. If the URL is valid, the same string continues into the saved server configuration; if not, validation stops with an error.

**Call relations**: This runs automatically when a McpServer configuration is built by the data validation layer. It protects later steps, such as _server and mcp_client, from receiving a server address they should not connect to.


##### `mcp_client`  (lines 115–121)

```
def mcp_client(server: McpServer) -> Client
```

**Purpose**: This builds a client object that knows how to talk to one MCP server over HTTP. If the server has an auth token, it adds that token to the request headers.

**Data flow**: It receives an McpServer object containing a URL and maybe an auth token. It turns that into a Streamable HTTP transport with optional Authorization headers, then wraps it in a FastMCP Client with a timeout. The result is a ready-to-use client for listing or calling tools.

**Call relations**: _list_mcp_tools and _call_mcp_tool call this after _server has resolved the configured server name. The FastMCP library then takes care of protocol details such as initialization, sessions, streamed responses, and pagination.

*Call graph*: called by 2 (_call_mcp_tool, _list_mcp_tools); 2 external calls (Client, StreamableHttpTransport).


##### `_server`  (lines 124–136)

```
async def _server(ctx: ToolContext, name: str) -> McpServer
```

**Purpose**: This looks up one named MCP server from the workspace credential. It makes sure the extension has context, the credential is present and valid, and the requested server name actually exists.

**Data flow**: It receives the current tool context and a server name. It reads the mcp_servers credential from the extension context, parses it as validated JSON, and searches the configured server map. It returns the matching McpServer object, or raises a clear error if the setup is missing or the name is unknown.

**Call relations**: Both _list_mcp_tools and _call_mcp_tool start here before any network call. This keeps those functions from blindly contacting an arbitrary or nonexistent server.

*Call graph*: called by 2 (_call_mcp_tool, _list_mcp_tools).


##### `_list_mcp_tools`  (lines 139–160)

```
async def _list_mcp_tools(ctx: ToolContext, args: ListMcpToolsInput) -> ToolResult
```

**Purpose**: This is the handler for the list_mcp_tools UFO tool. It lets the agent browse a server's tools first, then request full input schemas only for selected tools.

**Data flow**: It receives the tool context and the user's list request, including the configured server name and optionally specific tool names. It resolves the server, connects to it, asks for its tools, and then either builds a compact catalog or full schema entries for the requested names. It returns a ToolResult containing JSON text, or raises an error if a requested tool name is not exposed by that server.

**Call relations**: This is one of the two tool handlers registered by manifest. It uses _server to find the server, mcp_client to contact it, _catalog_entry for the compact menu, _schema_entry for detailed tool definitions, _bounded_schemas when returning several schemas, and _json_result to package the answer for the agent.

*Call graph*: calls 6 internal fn (_bounded_schemas, _catalog_entry, _json_result, _schema_entry, _server, mcp_client).


##### `_idempotent`  (lines 163–165)

```
def _idempotent(tool: McpTool) -> bool
```

**Purpose**: This reads whether an MCP tool says it is idempotent, meaning repeating the same call should not change things further. That hint helps describe how risky or repeatable a tool may be.

**Data flow**: It receives one MCP tool description. It checks the tool's annotations for an idempotent hint. It returns true or false, defaulting to false when the hint is missing.

**Call relations**: _catalog_entry and _schema_entry call this while preparing tool information for the agent. It adds the same repeatability hint to both the short catalog and the full schema view.

*Call graph*: called by 2 (_catalog_entry, _schema_entry).


##### `_catalog_entry`  (lines 168–184)

```
def _catalog_entry(tool: McpTool) -> JsonValue
```

**Purpose**: This turns one full MCP tool description into a small catalog item. The goal is to help the agent choose a tool without showing a large input schema yet.

**Data flow**: It receives an MCP tool object. It reads the tool name, description, input schema properties, required fields, and idempotent hint. It returns a small JSON-friendly dictionary with the name, short summary, parameter names, required parameter names, and idempotent flag.

**Call relations**: _list_mcp_tools calls this when the agent asks to browse all tools without requesting full schemas. It relies on _summary to shorten the description and _idempotent to include the repeatability hint.

*Call graph*: calls 2 internal fn (_idempotent, _summary); called by 1 (_list_mcp_tools).


##### `_summary`  (lines 187–193)

```
def _summary(description: str) -> str
```

**Purpose**: This makes a short, readable summary from a longer tool description. It is meant to keep the tool catalog compact and easy to scan.

**Data flow**: It receives a description string. It takes the first line, then keeps only the first sentence-like part, and cuts it to a fixed maximum length. It returns that shortened text.

**Call relations**: _catalog_entry calls this while building the compact tool list. This avoids putting long documentation blocks into the first browsing response.

*Call graph*: called by 1 (_catalog_entry).


##### `_schema_entry`  (lines 196–202)

```
def _schema_entry(tool: McpTool) -> JsonValue
```

**Purpose**: This prepares the detailed view of one MCP tool, including its full input schema. The agent needs this before calling a tool so it can use the exact parameter names and shapes.

**Data flow**: It receives an MCP tool object. It copies the name, full description, input schema, and idempotent hint into a JSON-friendly dictionary. That dictionary is returned for inclusion in the tool result.

**Call relations**: _list_mcp_tools calls this when the agent asks for specific tool names. It uses _idempotent so the detailed schema view carries the same repeatability information as the catalog.

*Call graph*: calls 1 internal fn (_idempotent); called by 1 (_list_mcp_tools).


##### `_call_mcp_tool`  (lines 205–217)

```
async def _call_mcp_tool(ctx: ToolContext, args: CallMcpToolInput) -> ToolResult
```

**Purpose**: This is the handler for the call_mcp_tool UFO tool. It sends a JSON argument object to one named tool on one configured MCP server and returns the server's response.

**Data flow**: It receives the tool context and a call request containing the server name, tool name, and arguments. It resolves the server, checks that the serialized arguments are no larger than the request limit, opens an MCP client, and calls the remote tool. If the remote tool reports an error, it returns an error ToolResult with bounded text. If the call succeeds, it returns structured JSON when available, or joined text content otherwise.

**Call relations**: This is registered by manifest as the second public tool in the extension. It uses _server before connecting, mcp_client for the network conversation, _joined_text to collect text blocks, _bounded and _json_result to enforce response limits and package the result.

*Call graph*: calls 5 internal fn (_bounded, _joined_text, _json_result, _server, mcp_client); 4 external calls (__init__, __init__, __init__, dumps).


##### `_joined_text`  (lines 220–221)

```
def _joined_text(content: list[object]) -> str
```

**Purpose**: This extracts readable text from an MCP response that may contain several content blocks. It ignores non-text blocks.

**Data flow**: It receives a list of response content objects. It keeps only MCP text content objects, pulls out their text, joins them with newline characters, and returns the combined string.

**Call relations**: _call_mcp_tool uses this when a remote tool fails or when the server does not return structured JSON. It gives the agent a plain text version of the response.

*Call graph*: called by 1 (_call_mcp_tool).


##### `_bounded`  (lines 224–227)

```
def _bounded(text: str) -> str
```

**Purpose**: This enforces the maximum allowed size for text returned from MCP calls. It fails loudly instead of silently cutting off data.

**Data flow**: It receives a text string. It measures its encoded byte size. If the text fits, it returns the original string; if it is too large, it raises McpError.

**Call relations**: _call_mcp_tool uses this for error text from remote tools, and _json_result uses it for JSON results. This is the main guard against oversized MCP responses entering the agent loop.

*Call graph*: called by 2 (_call_mcp_tool, _json_result); 1 external calls (__init__).


##### `_bounded_schemas`  (lines 230–247)

```
def _bounded_schemas(payload: dict[str, JsonValue], tools: int) -> ToolResult
```

**Purpose**: This protects the agent from receiving too many large tool schemas at once. It asks the agent to narrow the request when several schemas would not fit usefully in a normal tool result.

**Data flow**: It receives a JSON payload of schema entries and the number of tools included. If there is more than one requested schema and the rendered payload is over the listing size limit, it raises a helpful error telling the caller to ask for fewer tools. Otherwise, it passes the payload to _json_result and returns the packaged ToolResult.

**Call relations**: _list_mcp_tools calls this only when returning requested full schemas. It sits between _schema_entry and _json_result to prevent a partial, misleading schema preview when the agent really needs exact details.

*Call graph*: calls 1 internal fn (_json_result); called by 1 (_list_mcp_tools); 1 external calls (dumps).


##### `_json_result`  (lines 250–251)

```
def _json_result(payload: dict[str, JsonValue]) -> ToolResult
```

**Purpose**: This turns a JSON-friendly dictionary into the standard ToolResult shape used by UFO tools. It also applies the response size limit.

**Data flow**: It receives a dictionary payload. It serializes the payload to a JSON string, checks that the string is within the response byte limit through _bounded, wraps it in TextContent, and returns a ToolResult.

**Call relations**: _list_mcp_tools, _bounded_schemas, and _call_mcp_tool all use this as the final packaging step for successful JSON responses. It provides one consistent path from internal Python data to a tool result visible to the agent.

*Call graph*: calls 1 internal fn (_bounded); called by 3 (_bounded_schemas, _call_mcp_tool, _list_mcp_tools); 3 external calls (__init__, __init__, dumps).


##### `manifest`  (lines 254–285)

```
def manifest() -> Manifest
```

**Purpose**: This declares the extension to UFO: its name, version, tools, input models, handlers, and required credential slot. It is how the rest of the system learns what this MCP extension offers.

**Data flow**: It takes no input. It builds a Manifest containing two ToolDef entries, one for listing MCP tools and one for calling them, plus a CredentialSlot named mcp_servers. It returns that Manifest to the extension loader.

**Call relations**: The extension system calls this during setup or discovery. The returned manifest wires outside requests for list_mcp_tools to _list_mcp_tools and call_mcp_tool to _call_mcp_tool, and tells the system where the workspace's MCP server configuration must be stored.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Pipedream bridge
Pipedream integration files connect hosted account setup, action discovery, action execution, file retrieval, and token-safe provider proxying.

### `extensions/pipedream/ufo_ext_pipedream/broker.py`

`io_transport` · `request handling`

A Pipedream action is like a prebuilt remote tool: send an email, update a spreadsheet, create a ticket, and so on. UFO needs a standard way to offer those tools to an agent without making the agent understand Pipedream’s own API details. This file provides that standard wrapper through `PipedreamBroker`.

When asked what tools exist, the broker looks up the Pipedream app behind a provider name and turns Pipedream’s action list into `BrokerTool` objects. When asked for one tool’s schema, it reads the action definition and builds a JSON schema, which is a machine-readable description of allowed inputs. It deliberately hides the connected-account field, because the broker fills that in itself.

When running an action, the broker checks that the chosen account belongs to the current workspace and matches the requested Pipedream app. It then inserts the account into the action’s special app slot and calls Pipedream’s server-side run API. If the action key is wrong, it returns a clearer error with real available action names. If a saved account is stale or missing, it adds reconnect guidance.

The file also converts Pipedream File Stash outputs into downloadable file links, refuses staged uploads because Pipedream expects file URLs instead, and creates proxy credentials for direct API calls through a connected account.

#### Function details

##### `PipedreamBroker.tools`  (lines 59–61)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Finds Pipedream actions for a given provider and search query, then presents them in UFO’s standard tool format. Someone uses this when the agent needs to discover what actions are available, such as “what can I do with Gmail?”

**Data flow**: It receives a workspace ID, a provider name, and search text. It gets a fresh Pipedream client, translates the provider into Pipedream’s app name, asks Pipedream for matching actions, and converts the returned rows into `BrokerTool` records. The output is a tuple of tools the agent can inspect or choose from.

**Call relations**: This is the main discovery step. `PipedreamBroker.search` calls it when building a search result, and it relies on `_spec` to find the app slug and `_listed_tools` to cleanly translate Pipedream’s raw action rows into UFO’s tool shape.

*Call graph*: calls 2 internal fn (_listed_tools, _spec); called by 1 (search); 1 external calls (pipedream_client).


##### `PipedreamBroker.schema`  (lines 63–69)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Builds the input description for one specific Pipedream action. This tells the agent which arguments it may provide and which ones are required.

**Data flow**: It receives a workspace ID, provider name, and action slug. It fetches the action definition, extracts its configurable input fields, removes internal fields, and turns the rest into a JSON schema. It returns a `BrokerTool` containing the action slug, description, and input schema.

**Call relations**: This is used when the system already knows which action it wants and needs its exact input shape. It asks `_definition` for the action details, `_props` for the raw configurable fields, `_input_schema` for the cleaned user-facing schema, and `_str` to safely read text fields.

*Call graph*: calls 4 internal fn (_definition, _input_schema, _props, _str); 1 external calls (__init__).


##### `PipedreamBroker.execute`  (lines 71–106)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Runs a Pipedream action using a connected account that belongs to the current workspace. It is the point where a chosen tool call becomes an actual remote Pipedream run.

**Data flow**: It receives the workspace, provider, action slug, user-supplied arguments, connected account ID, and an optional idempotency key. It fetches the action definition, copies the arguments, inserts the connected account into the hidden app slot, verifies the account belongs to the right Pipedream app, and calls Pipedream to run the action. It returns the action response, or raises a clear error if the action is unknown, the account is wrong or stale, or the action reports a failure.

**Call relations**: This is the broker’s central execution path. It uses `_definition` to understand the action, `_key_miss` to improve unknown-action errors, `_app_slot` to find where the account must be inserted, `_spec` to verify the provider’s app, and `_stale_account` plus `_reconnect_error` to turn stale saved-account failures into guidance the agent can act on.

*Call graph*: calls 7 internal fn (_definition, _key_miss, _app_slot, _reconnect_error, _spec, _stale_account, __init__); 2 external calls (dumps, pipedream_client).


##### `PipedreamBroker.file_outputs`  (lines 108–126)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Finds files produced by a Pipedream action and turns them into downloadable UFO file records. This matters because the action may have written files inside Pipedream’s temporary runtime, not directly into UFO’s workspace.

**Data flow**: It receives the action response dictionary. It looks under the response exports for Pipedream File Stash upload entries, skips malformed entries, extracts each download URL, derives a friendly filename from the saved local path when possible, and returns `BrokerFile` objects. It does not download the files itself; it only reports where they can be fetched.

**Call relations**: This runs after `PipedreamBroker.execute` has returned a response. It does not call other broker helpers, but it creates the standard file objects that the surrounding sandbox or workspace code can later fetch.

*Call graph*: 2 external calls (__init__, PurePosixPath).


##### `PipedreamBroker.stage_upload`  (lines 128–140)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Rejects staged file uploads for Pipedream actions. Pipedream actions expect file inputs as URLs, so this broker tells callers to share the workspace file and pass its download link instead.

**Data flow**: It receives details for a file that someone wants to upload ahead of time, including filename, MIME type, and checksum. Instead of preparing an upload destination, it immediately raises an error explaining the supported path. Nothing is uploaded and no staged-upload object is returned.

**Call relations**: This is part of the generic connector interface, but Pipedream does not use this upload style. It stands alone and acts as a guardrail so callers do not try an unsupported file-transfer route.


##### `PipedreamBroker.search`  (lines 142–143)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Wraps tool discovery in a standard search result object. Pipedream does not provide extra routing guidance here, so the result mainly contains matching tools.

**Data flow**: It receives a workspace ID, provider, and query. It asks `PipedreamBroker.tools` for matching tools and places them into a `BrokerSearch` result. The output is a search response the rest of UFO can understand.

**Call relations**: This is a thin wrapper around `PipedreamBroker.tools`. When a caller asks the broker to search rather than directly list tools, this function performs the same catalog lookup and packages it in the broader connector search shape.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `PipedreamBroker.credential`  (lines 145–166)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Creates a credential object that lets UFO proxy API calls through a connected Pipedream account. It also checks that the account is real, belongs to the workspace, and matches the requested provider.

**Data flow**: It receives a workspace ID, provider, and connected account ID. It looks up the provider’s Pipedream app, fetches the connected account from Pipedream, turns a missing account into reconnect guidance, rejects accounts for the wrong app, and returns a `Credential` containing a `PipedreamProxyTransport`. That transport is the path future HTTP calls use to travel through Pipedream with the account attached.

**Call relations**: This supports code that needs direct authenticated transport rather than running a catalog action. It uses `_spec` to know the expected app and `_reconnect_error` when the saved account is gone, then hands the verified account to `PipedreamProxyTransport`.

*Call graph*: calls 3 internal fn (_reconnect_error, _spec, __init__); 4 external calls (__init__, __init__, AsyncHTTPTransport, pipedream_client).


##### `PipedreamBroker._definition`  (lines 168–176)

```
async def _definition(self, slug: str) -> dict[str, object]
```

**Purpose**: Fetches the full Pipedream definition for one action slug. It hides the small difference between Pipedream responses that wrap the definition in `data` and responses that return it directly.

**Data flow**: It receives an action slug. It asks a fresh Pipedream client for that action’s definition, turns a not-found response into `UnknownBrokerTool`, and otherwise returns the action definition dictionary. If Pipedream returns a wrapped payload, it unwraps the `data` field when that field is a dictionary.

**Call relations**: Both `PipedreamBroker.schema` and `PipedreamBroker.execute` depend on this before they can describe or run an action. It is the shared lookup step that converts Pipedream’s action-definition API into a simple dictionary for the rest of the broker.

*Call graph*: called by 2 (execute, schema); 2 external calls (__init__, pipedream_client).


##### `PipedreamBroker._key_miss`  (lines 178–192)

```
async def _key_miss(self, client: pipedream.PipedreamClient, provider: str, slug: str) -> PipedreamError
```

**Purpose**: Builds a more helpful error when execution asks for an action key Pipedream does not know. Instead of only saying “not found,” it tries to include the real action keys available for that app.

**Data flow**: It receives a Pipedream client, provider, and missing slug. It looks up the provider’s app and tries to list all actions for that app. If listing succeeds, it returns a not-found `PipedreamError` that includes available action names; if listing fails, it returns a simpler not-found error.

**Call relations**: `PipedreamBroker.execute` calls this only after `_definition` says the action slug is unknown. It uses `_spec` to find the app and `_listed_tools` to turn the catalog rows into readable tool slugs for the error message.

*Call graph*: calls 4 internal fn (_listed_tools, _spec, list_actions, __init__); called by 1 (execute).


##### `_stale_account`  (lines 195–202)

```
def _stale_account(error: PipedreamError, account_id: str) -> bool
```

**Purpose**: Decides whether a Pipedream failure probably means the saved connected account no longer exists or no longer works. This lets the broker give reconnect instructions only when the error really points to a stale grant.

**Data flow**: It receives a `PipedreamError` and the account ID that was used. It lowercases the error body and checks for narrow signs such as “external user not found” or the account ID appearing with “not found.” It returns `true` when the error looks like a stale account and `false` otherwise.

**Call relations**: `PipedreamBroker.execute` uses this after Pipedream run failures and action-level errors. If it returns true, execution passes the error to `_reconnect_error`; otherwise the original Pipedream error is allowed to surface unchanged.

*Call graph*: called by 1 (execute).


##### `_reconnect_error`  (lines 205–206)

```
def _reconnect_error(error: PipedreamError, provider: str) -> PipedreamError
```

**Purpose**: Adds plain reconnect guidance to a Pipedream error. It keeps the original status and message but appends instructions that tell the agent or user to reconnect the provider account.

**Data flow**: It receives a `PipedreamError` and provider name. It asks the shared connector code for stale-grant guidance text and creates a new `PipedreamError` with the same status and an expanded body. The output is an error that is more useful to a human or agent.

**Call relations**: `PipedreamBroker.execute` uses this when a run failure appears to come from a stale account, and `PipedreamBroker.credential` uses it when a requested account cannot be found. It is the common path for turning low-level account failures into actionable reconnect advice.

*Call graph*: calls 1 internal fn (__init__); called by 2 (credential, execute); 1 external calls (stale_grant_guidance).


##### `_spec`  (lines 209–213)

```
def _spec(provider: str) -> ConnectorSpec
```

**Purpose**: Looks up the Pipedream connector specification for a UFO provider name. In practice, this answers “which Pipedream app slug belongs to this provider?”

**Data flow**: It receives a provider string. It searches Pipedream’s registered connector map and returns the matching `ConnectorSpec`. If the provider is not registered, it raises a `KeyError` so the mistake is caught loudly.

**Call relations**: Several broker paths call this before talking to Pipedream: tool listing, execution, credential creation, and unknown-key error building. It is the shared translation point between UFO’s provider names and Pipedream’s app names.

*Call graph*: called by 4 (_key_miss, credential, execute, tools).


##### `_listed_tools`  (lines 216–232)

```
def _listed_tools(rows: tuple[dict[str, object], ...]) -> tuple[BrokerTool, ...]
```

**Purpose**: Converts Pipedream catalog rows into UFO broker tools. It filters out unusable rows and gives each tool a slug, description, and input schema.

**Data flow**: It receives raw action rows from Pipedream. For each row, it reads the action key, skips rows without a valid key, extracts a safe description, builds an input schema from configurable properties, and creates a `BrokerTool`. It returns all valid tools as a tuple.

**Call relations**: `PipedreamBroker.tools` uses this for normal tool discovery, and `PipedreamBroker._key_miss` uses it to list real action names in a helpful error. It relies on `_props`, `_input_schema`, and `_str` to clean up Pipedream’s raw fields.

*Call graph*: calls 3 internal fn (_input_schema, _props, _str); called by 2 (_key_miss, tools); 1 external calls (__init__).


##### `_props`  (lines 235–237)

```
def _props(definition: dict[str, object]) -> list[dict[str, object]]
```

**Purpose**: Extracts the configurable property list from a Pipedream action definition or catalog row. These properties are the action inputs Pipedream says can be configured.

**Data flow**: It receives a dictionary that may contain `configurable_props`. If that value is a list, it keeps only entries that are dictionaries; otherwise it returns an empty list. The result is a clean list of property dictionaries for later schema or account-slot logic.

**Call relations**: `PipedreamBroker.schema` and `_listed_tools` use this before building user-facing input schemas. `_app_slot` also uses it to find the hidden account field needed when executing an action.

*Call graph*: called by 3 (schema, _app_slot, _listed_tools).


##### `_app_slot`  (lines 240–247)

```
def _app_slot(definition: dict[str, object], slug: str) -> str
```

**Purpose**: Finds the special Pipedream input field where the connected account must be placed. Without this slot, the broker cannot run the action on behalf of the user’s account.

**Data flow**: It receives an action definition and action slug. It scans the action’s configurable properties for the one whose type marks it as the Pipedream app/account property, then returns that property’s name. If no such property exists, it raises a `PipedreamError` explaining that the action cannot be bound to an account.

**Call relations**: `PipedreamBroker.execute` calls this just before running an action. The returned field name is where execution inserts the account’s `authProvisionId`, so Pipedream knows which connected account to use.

*Call graph*: calls 2 internal fn (_props, __init__); called by 1 (execute).


##### `_input_schema`  (lines 250–273)

```
def _input_schema(props: list[dict[str, object]]) -> dict[str, object]
```

**Purpose**: Builds the JSON schema shown to the agent for a Pipedream action’s inputs. It hides fields that belong to Pipedream or the broker, so the agent only sees inputs it is allowed to provide.

**Data flow**: It receives a list of Pipedream property dictionaries. It skips invalid names, the account/app slot, directory fields, and Pipedream-internal fields whose types start with `$.`. For each remaining property, it maps Pipedream’s type into a JSON schema type, adds a description when available, and marks fields required unless Pipedream says they are optional. It returns a schema dictionary with properties and, when needed, required fields.

**Call relations**: `PipedreamBroker.schema` uses this for one action’s detailed schema, and `_listed_tools` uses it during tool discovery so listed tools already include usable input shapes. It calls `_str` to safely read type values.

*Call graph*: calls 1 internal fn (_str); called by 2 (schema, _listed_tools).


##### `_str`  (lines 276–277)

```
def _str(value: object) -> str
```

**Purpose**: Safely turns a value into text only when it is already a string. It prevents non-text values from accidentally appearing in descriptions or type names.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string. Nothing else is changed.

**Call relations**: This small helper is used by `PipedreamBroker.schema`, `_listed_tools`, and `_input_schema` whenever they read optional text from Pipedream’s raw data. It keeps schema-building and description-building predictable even when Pipedream returns missing or unexpected values.

*Call graph*: called by 3 (schema, _input_schema, _listed_tools).


### `extensions/pipedream/ufo_ext_pipedream/client.py`

`io_transport` · `request handling and connector execution`

This file is the bridge between this project and Pipedream Connect. Pipedream acts like a trusted vault and switchboard: users grant access to a provider such as Gmail through Pipedream, and this app stores only a Pipedream account id, not the provider’s secret token. Without this file, the Pipedream connector could not start OAuth consent, verify that a returned account belongs to the right workspace or user, discover provider actions, or execute those actions.

The file starts by defining the allowed Pipedream-backed connectors. At the moment, that allowlist contains Gmail, including a place to use this deployment’s own OAuth client when Pipedream’s shared one is not enough. It then defines small data shapes for a connect token and a connected account, plus a custom error type that makes Pipedream failures loud instead of silently pretending nothing happened.

The main piece is `PipedreamClient`. It talks to Pipedream’s REST API using `httpx`, an HTTP library. Before most calls, it gets a short-lived Pipedream access token using the deployment’s client id and secret, then reuses that token until it is close to expiring. The client also contains important safety checks: Pipedream project credentials can read many accounts, so the code confirms that an account belongs to the expected external user or workspace before using it. That is the guardrail that stops one user’s connected account from being used on behalf of another.

#### Function details

##### `PipedreamError.__init__`  (lines 80–83)

```
def __init__(self, status: int, body: str) -> None
```

**Purpose**: This creates a clear exception when Pipedream fails or returns data this client cannot safely use. It keeps the original status code and response text so callers can report or react to the exact failure.

**Data flow**: It receives a numeric status and a response body string. It turns them into a readable error message, stores both values on the error object, and returns the initialized exception for Python to raise.

**Call relations**: Many parts of this connector raise this error when something is unsafe or unexpected. The broker uses it for connector-level failures, and this client uses it when token minting, account checks, or response parsing cannot continue safely.

*Call graph*: called by 12 (_key_miss, credential, execute, _app_slot, _reconnect_error, access_token, connect_token, newest_account, workspace_account, _account (+2 more)).


##### `PipedreamClient.access_token`  (lines 121–142)

```
async def access_token(self) -> str
```

**Purpose**: This gets the Pipedream access token that proves this deployment is allowed to call the Pipedream API. It also caches the token so the app does not ask Pipedream for a new one on every request.

**Data flow**: It looks in a process-wide cache using the client id. If a still-valid token is there, it returns it. Otherwise it sends the client id and secret to Pipedream’s OAuth token endpoint, checks the response, stores the new token with its expiry time, and returns the token string.

**Call relations**: `_get` and `_post` call this before making authenticated Pipedream requests. It uses `_http` to create the unauthenticated HTTP client for the token call and `_body` to turn the HTTP response into a checked dictionary.

*Call graph*: calls 3 internal fn (_http, __init__, _body); called by 2 (_get, _post); 1 external calls (monotonic).


##### `PipedreamClient.connect_token`  (lines 144–159)

```
async def connect_token(self, external_user_id: str, success_redirect_uri: str, error_redirect_uri: str) -> ConnectToken
```

**Purpose**: This creates a short-lived Pipedream Connect token and browser link for a user to approve access to an external account. It is used at the start of the hosted consent flow.

**Data flow**: It receives an external user id plus success and error redirect URLs. It posts those to Pipedream, expects back a token and a connect-link URL, and returns them wrapped in a `ConnectToken`; if either is missing, it raises a Pipedream error.

**Call relations**: This function hands off the real HTTP work to `_post`. It is the client-side step that lets higher-level OAuth routes send a user to Pipedream’s hosted consent page.

*Call graph*: calls 2 internal fn (_post, __init__); 1 external calls (__init__).


##### `PipedreamClient.connected_account`  (lines 161–168)

```
async def connected_account(self, account_id: str, external_user_id: str) -> ConnectedAccount
```

**Purpose**: This reads one connected account and proves it belongs to the expected external user. It prevents this service from accidentally using an account owned by someone else.

**Data flow**: It receives a Pipedream account id and the external user id that should own it. It fetches the account record, normalizes the response shape, checks ownership and health, and returns a `ConnectedAccount` if everything matches.

**Call relations**: It uses `_get` to fetch from Pipedream, `_dict` to safely read nested data, and `_owned_account` to enforce the ownership guard. Higher-level connector code can rely on the returned account being safe to bind.

*Call graph*: calls 3 internal fn (_get, _dict, _owned_account).


##### `PipedreamClient.account_label`  (lines 170–174)

```
async def account_label(self, account_id: str) -> str | None
```

**Purpose**: This fetches a human-friendly name for a connected Pipedream account, if Pipedream has one. It is useful for showing users which account they connected.

**Data flow**: It receives an account id, fetches the account record, looks for a non-empty string in the `name` field, and returns that string or `None` if no usable label exists.

**Call relations**: It uses `_get` for the Pipedream request and `_dict` to handle responses that may wrap the account under a `data` field. Unlike the stricter account methods, it only reads a display label.

*Call graph*: calls 2 internal fn (_get, _dict).


##### `PipedreamClient.workspace_account`  (lines 176–186)

```
async def workspace_account(self, account_id: str, workspace_id: UUID) -> ConnectedAccount
```

**Purpose**: This reads a connected account and confirms it belongs to the given workspace. It is another safety check before running actions under a stored account id.

**Data flow**: It receives an account id and a workspace UUID. It fetches the account, converts it into a `ConnectedAccount`, checks whether the account’s external user id matches the workspace’s allowed naming pattern, and returns the account or raises a forbidden error.

**Call relations**: It uses `_get` and `_dict` to read Pipedream’s response, `_account` to validate account health and owner information, and `_workspace_owns_external_user` to decide whether the workspace is allowed to use it.

*Call graph*: calls 5 internal fn (_get, __init__, _account, _dict, _workspace_owns_external_user).


##### `PipedreamClient.newest_account`  (lines 188–202)

```
async def newest_account(self, external_user_id: str, app: str) -> ConnectedAccount
```

**Purpose**: This finds the most recently created connected account for a specific external user and Pipedream app. It is used after a user finishes consent, when the system needs to identify the account that was just connected.

**Data flow**: It receives an external user id and app slug. It asks Pipedream for matching accounts, chooses the record with the latest creation time, checks that it has an id and belongs to the expected user, and returns it as a `ConnectedAccount`.

**Call relations**: It calls `_get` to list accounts, `_dict` to ignore malformed list items, and `_owned_account` for the final ownership check. If there are no matches or the newest record is unusable, it raises a Pipedream error instead of guessing.

*Call graph*: calls 4 internal fn (_get, __init__, _dict, _owned_account).


##### `PipedreamClient.list_actions`  (lines 204–233)

```
async def list_actions(self, app: str, query: str='') -> tuple[dict[str, object], ...]
```

**Purpose**: This lists the Pipedream actions available for an app, optionally filtered by a search query. It follows Pipedream’s pages so discovery does not miss actions that are not on the first page.

**Data flow**: It receives an app slug and optional query text. It repeatedly fetches pages of actions, adds valid action dictionaries to a growing list, follows the next-page cursor when needed, stops at the last page or a fixed maximum, and returns the collected actions as a tuple.

**Call relations**: The broker calls this when it is trying to resolve or search for an action key. This function uses `_get` for each page and `_dict` to safely read page information from Pipedream’s response.

*Call graph*: calls 2 internal fn (_get, _dict); called by 1 (_key_miss).


##### `PipedreamClient.action_definition`  (lines 235–236)

```
async def action_definition(self, key: str) -> dict[str, object]
```

**Purpose**: This fetches the full definition for one Pipedream component or action. Callers use it when they need the action’s detailed shape, not just its catalog row.

**Data flow**: It receives an action key, sends a GET request for that component, and returns Pipedream’s response dictionary.

**Call relations**: It is a thin wrapper around `_get`. Higher-level code can call it after finding an action key to learn what inputs the action expects.

*Call graph*: calls 1 internal fn (_get).


##### `PipedreamClient.run_action`  (lines 238–256)

```
async def run_action(self, key: str, external_user_id: str, configured_props: dict[str, object]) -> dict[str, object]
```

**Purpose**: This runs one Pipedream action on the server side using the provided configured properties. It also asks Pipedream to create a fresh file stash so files produced by the action can be returned as downloadable links.

**Data flow**: It receives an action key, an external user id, and a dictionary of configured properties. It builds a run request, checks that the JSON payload is not too large, posts it to Pipedream, and returns the action result dictionary.

**Call relations**: Higher-level broker execution code uses this to actually perform a connector action. It delegates the HTTP call to `_post`; before doing so, it protects Pipedream and the app from oversized argument payloads.

*Call graph*: calls 1 internal fn (_post); 1 external calls (dumps).


##### `PipedreamClient._get`  (lines 258–261)

```
async def _get(self, path: str, params: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: This is the shared helper for authenticated GET requests to Pipedream. It keeps token fetching, HTTP setup, and response checking in one place.

**Data flow**: It receives a path and optional query parameters. It gets an access token, opens an HTTP client with that token, sends the GET request, parses and validates the response through `_body`, and returns a dictionary.

**Call relations**: Account lookup, action listing, action definition lookup, and related read operations all call this. It relies on `access_token` for authentication, `_http` for the configured HTTP client, and `_body` for safe response handling.

*Call graph*: calls 3 internal fn (_http, access_token, _body); called by 6 (account_label, action_definition, connected_account, list_actions, newest_account, workspace_account).


##### `PipedreamClient._post`  (lines 263–266)

```
async def _post(self, path: str, body: dict[str, object]) -> dict[str, object]
```

**Purpose**: This is the shared helper for authenticated POST requests to Pipedream. It is used whenever the client needs to create something or run something.

**Data flow**: It receives a path and a request body dictionary. It gets an access token, opens an authenticated HTTP client, sends the body as JSON, validates the response with `_body`, and returns the response dictionary.

**Call relations**: `connect_token` uses it to create hosted consent links, and `run_action` uses it to execute actions. Like `_get`, it centralizes the repeated steps of authentication, HTTP setup, and response validation.

*Call graph*: calls 3 internal fn (_http, access_token, _body); called by 2 (connect_token, run_action).


##### `PipedreamClient._http`  (lines 268–281)

```
def _http(self, token: str | None=None) -> httpx.AsyncClient
```

**Purpose**: This creates the actual asynchronous HTTP client used to call Pipedream. When a token is provided, it adds the authorization and environment headers Pipedream expects.

**Data flow**: It receives an optional access token. If a token is present, it builds headers with a bearer token and the selected Pipedream environment; otherwise it uses no special headers. It returns a configured `httpx.AsyncClient` with the base URL, timeout, and optional test transport.

**Call relations**: `access_token` calls it without a token for the OAuth token request. `_get` and `_post` call it with a token for authenticated Connect API requests.

*Call graph*: called by 3 (_get, _post, access_token); 1 external calls (AsyncClient).


##### `_dict`  (lines 284–285)

```
def _dict(value: object) -> dict[str, object]
```

**Purpose**: This small helper safely treats a value as a dictionary only when it really is one. It prevents crashes when Pipedream returns a field in an unexpected shape.

**Data flow**: It receives any value. If the value is a dictionary, it returns it unchanged; otherwise it returns an empty dictionary.

**Call relations**: Many parsing functions use this before reading nested fields from Pipedream responses. It supports account reads, action listing, and account validation by making malformed pieces behave like missing data.

*Call graph*: called by 6 (account_label, connected_account, list_actions, newest_account, workspace_account, _account).


##### `_owned_account`  (lines 288–301)

```
def _owned_account(record: dict[str, object], account_id: str, external_user_id: str) -> ConnectedAccount
```

**Purpose**: This verifies that a Pipedream account record belongs to one exact external user. It is a key protection against using someone else’s connected account.

**Data flow**: It receives an account record, the account id being checked, and the expected external user id. It first turns the record into a validated `ConnectedAccount`, then compares the account’s owner to the expected owner, returning the account only if they match.

**Call relations**: `connected_account` and `newest_account` call this after fetching account records. It builds on `_account` for basic validation and raises `PipedreamError` if the ownership check fails.

*Call graph*: calls 2 internal fn (__init__, _account); called by 2 (connected_account, newest_account).


##### `_account`  (lines 304–316)

```
def _account(record: dict[str, object], account_id: str) -> ConnectedAccount
```

**Purpose**: This converts a raw Pipedream account record into the project’s simple `ConnectedAccount` shape. It also rejects records that are missing an owner or are marked unhealthy.

**Data flow**: It receives a raw account dictionary and the expected account id. It reads the external owner, checks that the account is not unhealthy, extracts the app slug if present, and returns a `ConnectedAccount` containing the account id, app, and owner.

**Call relations**: `_owned_account` uses this before checking an exact user match, and `workspace_account` uses it before checking workspace ownership. It raises `PipedreamError` when the raw record is not safe to use.

*Call graph*: calls 2 internal fn (__init__, _dict); called by 2 (workspace_account, _owned_account); 1 external calls (__init__).


##### `workspace_user_prefix`  (lines 319–320)

```
def workspace_user_prefix(workspace_id: UUID) -> str
```

**Purpose**: This builds the standard prefix used in Pipedream external user ids for a workspace. That prefix lets the system later recognize which connected accounts belong under the workspace.

**Data flow**: It receives a workspace UUID. It converts the UUID to its compact hexadecimal form, adds the project’s external-user prefix and a trailing underscore, and returns the resulting string.

**Call relations**: `connection_user_id` uses it when creating a new external user id for a consent flow. `_workspace_owns_external_user` uses it when checking whether an existing external user id belongs to a workspace.

*Call graph*: called by 2 (_workspace_owns_external_user, connection_user_id).


##### `_workspace_owns_external_user`  (lines 323–332)

```
def _workspace_owns_external_user(workspace_id: UUID, external_user_id: str) -> bool
```

**Purpose**: This decides whether a Pipedream external user id is valid for a given workspace. It supports both an older direct workspace id form and the newer workspace-plus-connection-id form.

**Data flow**: It receives a workspace UUID and an external user id string. It first checks for the legacy exact match, then checks the standard workspace prefix, then verifies that the remaining connection id is 32 lowercase hexadecimal characters. It returns `True` or `False`.

**Call relations**: `workspace_account` calls this before allowing a workspace to use a connected account. It uses `workspace_user_prefix` to keep the naming rule consistent with id creation.

*Call graph*: calls 1 internal fn (workspace_user_prefix); called by 1 (workspace_account).


##### `connection_user_id`  (lines 335–337)

```
def connection_user_id(workspace_id: UUID, state: str) -> str
```

**Purpose**: This creates a stable Pipedream external user id for a specific workspace and connection state. It lets consent flows be scoped so the returned account can be tied back to the right workspace-owned connection.

**Data flow**: It receives a workspace UUID and a state string. It hashes the state with SHA-256, takes the first 32 hexadecimal characters as a connection id, attaches that to the workspace prefix, and returns the full external user id.

**Call relations**: This function uses `workspace_user_prefix` so generated ids match what `_workspace_owns_external_user` later accepts. It is used by higher-level consent flow code when preparing a Pipedream Connect session.

*Call graph*: calls 1 internal fn (workspace_user_prefix); 1 external calls (sha256).


##### `_body`  (lines 340–348)

```
def _body(response: httpx.Response) -> dict[str, object]
```

**Purpose**: This turns an HTTP response from Pipedream into a checked dictionary. It makes failures obvious and rejects response shapes the rest of the client cannot safely understand.

**Data flow**: It receives an `httpx.Response`. If the status code means failure, it raises `PipedreamError` with the status and text. If the response is empty, it returns an empty dictionary. Otherwise it parses JSON and returns it only if the JSON is an object.

**Call relations**: `access_token`, `_get`, and `_post` all pass Pipedream responses through this helper. That means every API call gets the same error handling and shape checking before higher-level code reads fields from it.

*Call graph*: calls 1 internal fn (__init__); called by 3 (_get, _post, access_token); 1 external calls (json).


##### `pipedream_client`  (lines 351–369)

```
def pipedream_client() -> PipedreamClient
```

**Purpose**: This builds the deployment’s default `PipedreamClient` from environment variables. It fails immediately if the required Pipedream credentials or project id are missing.

**Data flow**: It reads the client id, client secret, project id, and optional environment name from process environment variables. If the required values are absent, it raises a runtime error; otherwise it returns a configured `PipedreamClient`.

**Call relations**: Higher-level connector and OAuth code can call this when they need the real Pipedream client for the current deployment. It is the configuration bridge between environment settings and the client methods that call Pipedream.

*Call graph*: 1 external calls (__init__).


### `extensions/pipedream/ufo_ext_pipedream/provider.py`

`io_transport` · `request handling during connector OAuth setup`

OAuth is the common “approve this app to access my account” flow. ufo expects that flow to start with a normal web address, but Pipedream first requires an asynchronous server call to create a temporary Connect token. This file solves that mismatch.

Think of it like a receptionist. ufo sends the user's browser to this extension first, not directly to Pipedream. The extension checks which connector is being requested, creates a Pipedream Connect token for this exact workspace and sealed state, then redirects the browser to Pipedream's hosted consent page. When Pipedream sends the browser back, this file either reports failure clearly or finds the newest connected account for that specific temporary Pipedream user and redirects back to ufo with that account id.

The important safety idea is ownership. Each OAuth state is mapped to a distinct Pipedream external user id, so two overlapping connection attempts cannot accidentally pick up each other's account. Later, `exchange` asks Pipedream for that exact account and verifies it belongs to the expected Pipedream app before returning an `OAuthAccount` to core ufo. The actual secret token stays inside Pipedream; ufo stores only the Pipedream account id and an optional label.

#### Function details

##### `PipedreamOAuthProvider.authorize_url`  (lines 50–52)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds the first URL that ufo should send the user's browser to when starting connector authorization. Instead of pointing straight at Pipedream, it points at this extension's bridge route so the async Pipedream token can be created first.

**Data flow**: It receives a sealed `state` value and a `redirect_uri` where ufo wants the browser to return later. It packages the provider name, state, and callback into query parameters, uses `_origin` to keep the same scheme and host as the callback, and returns a full bridge URL. It does not contact Pipedream or change stored data.

**Call relations**: This is the start of the flow for a registered `PipedreamOAuthProvider`. It relies on `_origin` to make sure the callback has a real web origin, and on URL encoding so the state and callback survive safely inside the browser redirect.

*Call graph*: calls 1 internal fn (_origin); 1 external calls (urlencode).


##### `PipedreamOAuthProvider.exchange`  (lines 54–68)

```
async def exchange(self, code: str, _redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Turns the account id returned from the browser flow into the `OAuthAccount` object that ufo core can attach to a grant. It also double-checks that the account belongs to the expected Pipedream app before trusting it.

**Data flow**: It receives the returned `code`, which in this bridge is really a Pipedream account id, plus the workspace id and sealed state. It rebuilds the Pipedream external user id for that workspace and state, asks the Pipedream client for the connected account, rejects it if the app does not match this provider, tries to fetch a human-friendly label, and returns an `OAuthAccount` containing the account id and optional label. It does not receive or store the underlying OAuth secret token.

**Call relations**: This runs after `oauth_route` has redirected back to core with the account id. It uses the Pipedream client and `connection_user_id` helper to reassert that the account belongs to this exact connection attempt, then hands the safe account reference back to ufo core.

*Call graph*: 4 external calls (__init__, PipedreamError, connection_user_id, pipedream_client).


##### `oauth_route`  (lines 71–114)

```
async def oauth_route(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: Serves as the browser bridge for both halves of the Pipedream connection flow: sending the user to Pipedream and receiving them back afterward. It keeps the original ufo state and callback intact across those redirects.

**Data flow**: It reads query parameters from the incoming HTTP request: provider, state, callback, and optionally an outcome from Pipedream. If state or callback is missing, it returns a bad request response; if the provider is unknown, it returns not found. If Pipedream reports success, it finds the newest account for this workspace-and-state-specific external user and redirects back to ufo with the state and account id. If Pipedream reports failure, it returns a clear error response. If this is the start of the flow, it creates a Pipedream Connect token, builds a hosted Connect Link for the requested app, optionally adds a custom OAuth app id from the environment, and redirects the browser there.

**Call relations**: This route is the middle stop between ufo core and Pipedream. It calls `_origin` when it needs to build return URLs on the same host, looks up connector details in `CONNECTORS`, uses the Pipedream client to mint tokens or find accounts, and returns HTTP `Response` objects that either redirect the browser or explain why the flow cannot continue.

*Call graph*: calls 1 internal fn (_origin); 5 external calls (Response, get, connection_user_id, pipedream_client, urlencode).


##### `_origin`  (lines 117–121)

```
def _origin(url: str) -> str
```

**Purpose**: Extracts the scheme and host from a URL, such as turning `https://example.com/path` into `https://example.com`. This is used so redirect URLs are built on the correct web origin.

**Data flow**: It receives a URL string, parses it, and checks that it has an `http` or `https` scheme and a host name. If the URL is valid, it returns just the scheme and host; if not, it raises an error explaining that the OAuth bridge needs a real callback host.

**Call relations**: Both `PipedreamOAuthProvider.authorize_url` and `oauth_route` call this helper before building bridge URLs. It is a small guardrail that prevents the OAuth flow from constructing redirects from incomplete or unsafe callback URLs.

*Call graph*: called by 2 (authorize_url, oauth_route); 1 external calls (urlparse).


### `extensions/pipedream/ufo_ext_pipedream/proxy.py`

`io_transport` · `request handling`

Some connected services, like Gmail or Slack, require private credentials. In this project, Pipedream keeps those credentials hidden and injects them on the server side. That is safer, but it creates a problem: existing connector code expects to make ordinary HTTP calls to the provider. This file is the bridge between those two worlds.

The main class, PipedreamProxyTransport, is an httpx transport. A transport is the low-level piece that actually sends an HTTP request. Instead of sending the request straight to the provider, this transport rewrites it into a call to Pipedream’s proxy endpoint. It puts the original provider URL into the proxy path after encoding it safely, adds the Pipedream account and user identifiers as query values, and attaches a Pipedream access token so the proxy accepts the request.

It is careful with headers. Pipedream only forwards headers that start with x-pd-proxy-, so this file adds that prefix to useful provider headers. It also drops transport-level headers such as host, content-length, and authorization, because forwarding those could be wrong or unsafe.

The important behavior is that the answer from Pipedream is treated as the provider’s answer. Status code, headers, and body are preserved, so connector logic that depends on provider errors or pagination headers still works.

#### Function details

##### `PipedreamProxyTransport.handle_async_request`  (lines 54–73)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: This is the core request-rewriting step. It takes a normal provider HTTP request and converts it into a Pipedream Connect Proxy request so Pipedream can add the hidden provider credential before forwarding it.

**Data flow**: It receives an httpx request that was meant for the provider. It asks the Pipedream client for an access token, reads the original request body, filters and renames headers so Pipedream will forward the useful ones, encodes the original URL into a safe text form, and builds a new request to Pipedream’s proxy endpoint with the account and external user attached. The result is whatever response the inner transport gets back from Pipedream, which should mirror the provider’s real response.

**Call relations**: When an httpx client using this transport sends a request, this method is the place where the request is intercepted. It uses httpx.Request.aread to capture the body, base64.urlsafe_b64encode to safely place the original URL inside the proxy path, httpx.URL to build the proxy address, and httpx.Request to create the rewritten request. It then hands that rewritten request to the inner transport, which does the actual network work.

*Call graph*: 4 external calls (urlsafe_b64encode, Request, aread, URL).


##### `PipedreamProxyTransport.aclose`  (lines 75–76)

```
async def aclose(self) -> None
```

**Purpose**: This shuts down the underlying transport cleanly. It exists so closing the proxy transport also closes whatever real network transport it wraps.

**Data flow**: It takes no new data. When called, it forwards the close request to the inner transport. Nothing is returned, but any open resources owned by the wrapped transport can be released.

**Call relations**: This is used during cleanup, usually when the surrounding httpx client is being closed. Rather than doing its own shutdown work, it passes the request straight through to the inner transport so the lower-level connection machinery can finish properly.


### Slack connector actions
Slack-specific chat tools guide workspace connection and let agents search Slack conversations.

### `extensions/slack/ufo_ext_slack/tools.py`

`orchestration` · `Slack setup and Slack tool calls during conversations`

Slack needs several things before this system can work there: a bot token, a signing secret, the bot’s Slack identity, and proof that Slack can reach this deployment’s public web address. This file provides the chat-facing tools that guide that setup and report a simple status such as not configured, pending, or connected.

There are two setup paths. In the one-click OAuth path, the deploy already has its own Slack app configured, so the tool creates an “Add to Slack” link for an admin. In the manifest path, the user creates their own Slack app from a ready-made Slack manifest, then privately supplies the bot token and signing secret. Both paths end in the same place: the system stores a Slack identity for the workspace and waits for Slack to send a real, signature-checked request.

The file also includes a runtime tool, slack_channels, which searches Slack conversations using the bot token. This helps the agent find a channel or direct message by name, topic, or people involved instead of requiring the user to provide a Slack conversation ID.

A useful analogy: this file is the front desk for Slack setup. It tells the user which paperwork is missing, prints the right forms, checks the submitted credentials, and finally says whether the door is open.

#### Function details

##### `_events_url`  (lines 139–140)

```
def _events_url(public_base_url: str) -> str
```

**Purpose**: Builds the public web address where Slack should send events for this deployment. Slack needs this URL so messages, mentions, and setup verification requests can reach the system.

**Data flow**: It receives the deployment’s public base URL, removes any trailing slash, adds the Slack surface path, and returns the finished Slack events URL as text.

**Call relations**: slack_connect_handler uses it when reporting setup status so the user can see where Slack should call back. slack_manifest_handler uses it when filling in the Slack app manifest, so the generated app points at the right endpoint.

*Call graph*: called by 2 (slack_connect_handler, slack_manifest_handler).


##### `_state`  (lines 143–145)

```
def _state(state: str, hint: str, events_url: str | None, **extra: object) -> ToolResult
```

**Purpose**: Packages a Slack setup status into a tool result that the agent can show to the user. It gives every setup answer the same shape: state, hint, event URL, and any extra details.

**Data flow**: It receives a state name, a human-readable hint, an optional Slack events URL, and extra fields. It turns those values into JSON text, wraps that text in tool output objects, and returns the completed ToolResult.

**Call relations**: The setup flow calls this whenever it needs to explain where Slack setup stands. _oauth_link, _derive_manifest_identity, and slack_connect_handler all use it so their different branches still return one consistent status format.

*Call graph*: called by 3 (_derive_manifest_identity, _oauth_link, slack_connect_handler); 3 external calls (__init__, __init__, dumps).


##### `slack_connect_handler`  (lines 148–189)

```
async def slack_connect_handler(ctx: ToolContext, args: SlackConnectInput) -> ToolResult
```

**Purpose**: Runs the main Slack connection check and setup flow. It can start installation, verify stored credentials, bind the Slack team to this workspace, and report whether Slack is ready to use.

**Data flow**: It receives the tool context and the user’s chosen setup method. It looks for a stored bot token, tries to read the Slack identity, follows either the OAuth path or manifest path if identity is missing, binds the Slack team to this UFO workspace, checks whether Slack has successfully called back, and returns a JSON status such as not_configured, not_installed, pending, or connected.

**Call relations**: This is the main handler behind the slack_connect tool. It calls _events_url to name the callback URL, _oauth_link when the user wants one-click install, _derive_manifest_identity when the user brings their own Slack app, _verified to check whether Slack has reached the deploy, and _state to explain the result.

*Call graph*: calls 5 internal fn (_derive_manifest_identity, _events_url, _oauth_link, _state, _verified); 2 external calls (read_identity, slack_installation_id).


##### `_oauth_link`  (lines 192–222)

```
async def _oauth_link(ctx: ToolContext, events_url: str | None) -> ToolResult
```

**Purpose**: Creates the one-click “Add to Slack” link for deployments that have their own Slack app configured. It also blocks the flow when the deploy is missing app settings or when the speaker is not an admin.

**Data flow**: It receives the tool context and the Slack events URL. It checks environment settings for the Slack app, confirms the current speaker is an admin, requires a public base URL, starts a sealed credential authorization handoff, builds the Slack authorization URL, and returns a setup state containing that link.

**Call relations**: slack_connect_handler calls this when no Slack identity is stored and the requested method is OAuth. It uses ToolContext methods to check admin rights and create the credential handoff, then asks Slack surface helpers to build the final Slack authorization URL.

*Call graph*: calls 3 internal fn (begin_credential_authorization, speaker_is_admin, _state); called by 1 (slack_connect_handler); 3 external calls (slack_authorize_url, slack_client_id, slack_oauth_redirect_uri).


##### `_derive_manifest_identity`  (lines 225–259)

```
async def _derive_manifest_identity(ctx: ToolContext, events_url: str | None) -> SlackIdentity | ToolResult
```

**Purpose**: Completes the bring-your-own-Slack-app setup path after the user has privately supplied the bot token and signing secret. It proves the bot token works by resolving the Slack team and bot identity.

**Data flow**: It receives the tool context and events URL. It checks whether the required secret slots are filled; if not, it returns a not_configured state listing what is missing. If secrets exist, it checks that the speaker is an admin, calls Slack identity resolution using the bot token, stores or returns the identity when successful, and returns a clear not_configured diagnosis when Slack rejects the token.

**Call relations**: slack_connect_handler calls this for manifest-based setup. This function uses _state to return user-facing setup messages and _token_diagnosis to turn Slack token errors into plain instructions.

*Call graph*: calls 3 internal fn (speaker_is_admin, _state, _token_diagnosis); called by 1 (slack_connect_handler); 1 external calls (__init__).


##### `_verified`  (lines 262–281)

```
async def _verified(ctx: ToolContext) -> bool
```

**Purpose**: Checks whether Slack has already reached this deployment using the current signing secret. This matters because a bot is only truly connected after Slack sends a request that passes signature verification.

**Data flow**: It reads a verification marker from blob storage. If the marker is missing, unreadable, or malformed, it returns false. It then reads the current Slack signing secret, fingerprints it, compares that fingerprint to the stored marker, and returns true only when they match.

**Call relations**: slack_connect_handler calls this after identity is known and the Slack team is bound. A true result lets slack_connect_handler report connected; a false result means setup remains pending until Slack sends a verified request.

*Call graph*: called by 1 (slack_connect_handler); 2 external calls (loads, signing_secret_fingerprint).


##### `slack_manifest_handler`  (lines 284–299)

```
async def slack_manifest_handler(ctx: ToolContext, args: SlackManifestInput) -> ToolResult
```

**Purpose**: Creates a ready-to-paste Slack app manifest for users who want to bring their own Slack app. The manifest contains the needed permissions, event subscriptions, and callback URLs so the user does not have to assemble them by hand.

**Data flow**: It receives the tool context and requested bot display name. It validates that the name is short and plain, checks that a public base URL is configured, builds the Slack events and interactivity URLs, fills the manifest template, and returns the manifest as text.

**Call relations**: This is the handler behind the slack_app_manifest tool. It calls _events_url to place the correct request URL into the manifest and returns the finished YAML-like manifest directly to the agent for display.

*Call graph*: calls 1 internal fn (_events_url); 3 external calls (__init__, __init__, match).


##### `slack_channels_handler`  (lines 302–325)

```
async def slack_channels_handler(ctx: ToolContext, args: SlackChannelsInput) -> ToolResult
```

**Purpose**: Searches the connected Slack workspace for conversations such as channels, group direct messages, and one-to-one direct messages. It lets the agent find a place to read from or post to using human clues like a channel name or a person’s name.

**Data flow**: It receives the tool context and a search query. It reads the stored Slack bot token, reads the stored Slack identity to know the bot user ID, runs a Slack conversation search with that information, converts the matches into JSON, and returns them as untrusted text because the names and topics come from Slack users.

**Call relations**: This is the handler behind the slack_channels tool. It depends on read_identity to confirm Slack setup is complete and on SlackConversationSearch to page through Slack’s conversation list and prepare the search results.

*Call graph*: 5 external calls (__init__, __init__, __init__, dumps, read_identity).


##### `_token_diagnosis`  (lines 328–334)

```
def _token_diagnosis(error: str) -> str
```

**Purpose**: Turns a Slack token error code into a helpful setup message. It helps users understand whether they should re-copy the bot token or report a more general Slack authentication failure.

**Data flow**: It receives Slack’s error text. If the error is one of the known token rejection cases, it returns a message telling the user to collect the Bot User OAuth Token again. Otherwise it returns a generic message naming the Slack auth.test failure.

**Call relations**: _derive_manifest_identity calls this when Slack identity resolution fails for the manifest path. Its output becomes the hint inside the not_configured state returned to the user.

*Call graph*: called by 1 (_derive_manifest_identity).

## 📊 State Registers Touched

- `reg-extension-catalog` — The shared list of installed extensions and the capabilities they registered for this deployment.
- `reg-db-session` — The active database connection, transaction, and workspace-safe persistence context used while work is running.
- `reg-workspace-roster` — The saved list of workspaces, members, admins, seats, and membership rules.
- `reg-agent-definitions` — The saved assistant agents for each workspace, including their settings, tools, model choices, and provisioning source.
- `reg-identity-context` — The current answer to who is acting, in which workspace, and on behalf of which member or agent.
- `reg-credential-vault` — The encrypted store of workspace secrets and API keys that tools and connectors can request through guarded paths.
- `reg-connection-grants` — The saved outside-service account connections and the grants saying which agents may use them.
- `reg-tool-registry` — The shared catalog of tools the model is allowed to call and the input rules for each tool.
- `reg-connector-tool-catalog` — The discovered connector actions from systems like Gmail, Slack, GitHub, Composio, Pipedream, and MCP servers.
- `reg-object-store` — The durable named workspace objects owned by extensions, with their names, data, permissions, and owner routing.
- `reg-source-feeds` — The registered external content sources, sync cursors, backoff state, ownership, grants, and wake-up triggers.
- `reg-usage-ledger` — The shared cost and usage records for model calls, tools, sandboxes, connectors, network use, and generated media.
- `reg-visibility-policy` — The shared audience, sharing, governance, and permission rules that decide who may see or change private data.
- `reg-evaluation-fixture-store` — Controlled non-production fixture data such as fake email inboxes, calendars, sample notes, and deterministic connector data used by demos, evaluations, and conformance tests.
- `reg-tool-execution-context` — The per-turn tool runtime context carrying permitted workspace handles, account/credential accessors, cleanup callbacks, sandbox/browser handles, and helper-agent hooks across tool calls.
- `reg-connector-link-state` — Pending external-account consent/OAuth linking state between generated approval links, callbacks, completion markers, and eventual saved connections.
