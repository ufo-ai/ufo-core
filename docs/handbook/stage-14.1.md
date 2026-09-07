# Connector action execution  `stage-14.1`

This stage is the system’s safe doorway to outside services during the main work loop. When the agent needs Slack, GitHub, Gmail, or another app, it does not get the user’s secret token. Instead, connector brokers act like reception desks: they list available accounts and tools, explain what each tool needs, run the chosen action, and pass files back and forth.

The core access file defines the trusted handoff points, including how tools are discovered, executed, and used by sync jobs without leaking secrets. The general connector tools extension gives the agent searchable commands for finding connectors, inspecting tool inputs, running actions, and moving files. Composio support includes a resolver that treats many Composio apps as one catalog, plus a broker, client, MCP short-call helper, and proxy that run tools or HTTP requests through Composio. Pipedream support mirrors this with its package entry point, broker, client, and proxy for Pipedream Connect actions and credentials. Slack hooks add finishing touches, such as bot attribution and updating connection buttons after a Slack account is linked.

## Files in this stage

### Connector surface
These files define the shared connector handoff model and expose safe agent-facing tools for discovering, inspecting, running, and transferring connector resources.

### `core/src/ufo/runtime/access/connectors.py`

`domain_logic` · `cross-cutting: connector discovery, tool execution, and feed sync credential resolution`

This file is the connector “front desk” for the system. Connectors let UFO talk to outside services, but those services need credentials. The important rule here is: tokens should not wander into logs, sandboxes, or agent-visible data. A credential is therefore represented as one of a few safe shapes: a proxy transport that adds the secret somewhere else, a bearer token read only inside the trusted process, or special HTTP headers.

The file also defines what a connector broker must provide. A broker is a server-side service that knows which tools a provider has, can run those tools for a connected account, can stage file uploads and downloads through temporary URLs, and can provide a feed-sync credential when needed.

The registry is the routing table. It maps provider names, like a service slug, to the broker that owns them. It can also ask an open resolver about providers that were not registered one by one.

The later helper classes add a safety check for feed sync sources. If a source was created for a specific member-owned connection, the code checks the database before every proxied request to make sure that connection still exists and still belongs to the same workspace, member, provider, and account. Like checking a library card before every checkout, this prevents an old sync job from continuing after its grant has been removed.

#### Function details

##### `Credential.__repr__`  (lines 57–66)

```
def __repr__(self) -> str
```

**Purpose**: Returns a safe text description of a credential without showing the actual secret. This matters because debug output and error reports can accidentally include object representations.

**Data flow**: It reads which authentication form the Credential contains: proxy transport, bearer token, headers, or nothing. It then returns a short string that says the shape of the credential while replacing any sensitive value with “redacted”. It does not change the credential.

**Call relations**: This is used automatically by Python whenever a Credential is printed or included in debugging text. It supports the whole connector flow by making accidental logging less dangerous.


##### `AuthProxy.credential`  (lines 85–85)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Defines the promise that an authentication backend must fulfill: given a workspace, provider, and account, return a safe Credential for provider HTTP requests. It is a protocol method, meaning this file describes the expected behavior but another class supplies the real code.

**Data flow**: The inputs are the workspace id, provider name, and account handle. An implementation uses those to locate or proxy the right authentication material, then returns a Credential object. The method itself has no body here because it is an interface.

**Call relations**: Feed sync code depends on this shape rather than knowing whether credentials come from a broker, a direct stored key, or another backend. SourceCredentialResolver.bind returns an object that follows this protocol.


##### `GrantUnusable.__init__`  (lines 113–115)

```
def __init__(self, reason: str, *, awaits_grant: bool=False) -> None
```

**Purpose**: Creates an error that means a connected account cannot currently be used, usually because the user must reconnect it. It also records whether the system should wait specifically for a new grant event before trying again.

**Data flow**: It receives a human-readable reason and an optional awaits_grant flag. It stores the reason in the normal exception machinery and saves the flag on the exception object. The result is an exception that callers can catch and interpret.

**Call relations**: Broker implementations such as Composio and Pipedream raise this when their account checks find a revoked, expired, or unhealthy grant. Downstream sync handling can treat it differently from a temporary broker outage, avoiding pointless retries.

*Call graph*: called by 4 (credential, _account, credential, _account).


##### `stale_grant_guidance`  (lines 118–125)

```
def stale_grant_guidance(provider: str) -> str
```

**Purpose**: Builds a clear error message for the case where a broker no longer recognizes a previously connected account. The message tells the operator what can actually fix the problem: ask the member to reconnect.

**Data flow**: It takes a provider name and inserts it into a standard explanatory sentence. It returns that sentence as plain text. It does not read or change any external state.

**Call relations**: Broker code can use this helper when it detects a stale or unknown grant. It keeps the user-facing guidance consistent across connector backends.


##### `ConnectorBroker.tools`  (lines 196–198)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Defines how a broker should list tools for a provider, optionally filtered by a search query. This is part of the broker interface, so implementations provide the actual lookup.

**Data flow**: The caller supplies a workspace id, provider name, and query text. An implementation should return a tuple of BrokerTool objects describing matching tools. No implementation logic lives here.

**Call relations**: Dynamic connector discovery calls this kind of broker method when it needs to show an agent or user which actions are available for a connected service.


##### `ConnectorBroker.schema`  (lines 200–200)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Defines how a broker should return the detailed input schema for one tool. The schema tells the agent what arguments the tool accepts.

**Data flow**: The caller gives the workspace id, provider name, and tool slug. An implementation returns a BrokerTool with its input schema filled in, or raises UnknownBrokerTool if that slug is not available. This protocol method only states the contract.

**Call relations**: Tool description flows use this before execution so the caller can build valid arguments. The UnknownBrokerTool error lets describe-style calls report unresolved tools instead of crashing everything.


##### `ConnectorBroker.execute`  (lines 202–210)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Defines how a broker should run one provider tool for a specific connected account. The broker, not the sandbox, owns and injects the real service token.

**Data flow**: Inputs are the workspace id, provider, tool slug, arguments, account id, and an optional idempotency key, which is a repeated-request safety key. An implementation sends the request to the broker/provider side and returns the tool response as a dictionary. This method is only the interface.

**Call relations**: Dynamic connector tools call through this shape when an agent asks to perform an action. File staging and credential secrecy rules around this file make sure execution can happen without exposing tokens.


##### `ConnectorBroker.file_outputs`  (lines 212–212)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Defines how a broker extracts downloadable files from a tool response. Files are represented as names and temporary URLs, not as bytes moving through this process.

**Data flow**: It receives the response dictionary from a broker execution. An implementation scans it for produced files and returns BrokerFile records. The protocol method itself does not perform the scan.

**Call relations**: After ConnectorBroker.execute returns, the tool layer can ask this method whether the result includes files. The sandbox can then fetch those files directly from the broker storage URL.


##### `ConnectorBroker.stage_upload`  (lines 214–222)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Defines how a broker prepares a place for a workspace file to be uploaded before a tool call uses it. This avoids routing file bytes through the main server process.

**Data flow**: The caller supplies workspace, provider, tool slug, filename, media type, and an md5 checksum, which is a content fingerprint. An implementation returns a StagedUpload containing where to PUT the file and what argument value to pass to the tool. If the broker cannot stage files this way, it may raise an error.

**Call relations**: Before executing tools that need file inputs, dynamic connector tooling asks the broker to stage each file. The sandbox then uploads directly to the broker’s storage and passes the returned reference into ConnectorBroker.execute.


##### `ConnectorBroker.search`  (lines 224–224)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Defines semantic search over a broker’s tools. Semantic search means searching by meaning or task intent, not only exact text matching.

**Data flow**: The caller provides a workspace id, provider name, and search query. An implementation returns a BrokerSearch with matching tools and optional advice such as a plan, guidance, or pitfalls. This protocol method has no local logic.

**Call relations**: Discovery and planning features can call this when they want the broker to recommend tools for a task. It complements the simpler tools listing method.


##### `ConnectorBroker.credential`  (lines 226–226)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Defines how a broker returns a feed-sync Credential for one connected account. For brokered accounts, this usually means returning a proxy transport so the secret stays on the broker side.

**Data flow**: Inputs are workspace id, provider name, and account handle. An implementation verifies the account and returns a Credential. The method here only states that every broker must offer this operation.

**Call relations**: _credential calls this when a source is not using the direct account path. _BoundSourceCredentials then wraps the returned transport with extra connection checks.


##### `GrantSecret.secret`  (lines 236–236)

```
async def secret(self, workspace_id: UUID, account_id: str) -> str
```

**Purpose**: Defines how trusted proxy code can retrieve the real token behind a connected account. This is a narrow interface for the special case where this deployment is allowed to hold that secret.

**Data flow**: It receives the workspace id and account id. An implementation confirms the account belongs to the workspace, then returns the secret token as a string or raises an error. The protocol does not include implementation details.

**Call relations**: CliCredential uses this interface so an egress proxy can swap a harmless sandbox sentinel for the real provider token. The sandbox still never receives the true secret.


##### `ConnectorResolver.transfer_hosts`  (lines 306–306)

```
def transfer_hosts(self) -> tuple[str, ...]
```

**Purpose**: Names the extra file-storage hosts that grants from an open broker namespace are allowed to contact. This is needed so staged uploads and downloads can pass through the network controls.

**Data flow**: An implementation returns a tuple of host names. There are no inputs beyond the resolver object itself. This property is a protocol requirement.

**Call relations**: Connector resolver implementations expose these hosts to the wider connector and egress setup. It supports file transfer without loosening network access more than necessary.


##### `ConnectorResolver.claims`  (lines 308–308)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: Answers whether an open broker namespace serves a given provider slug. This prevents the system from assuming that a broad resolver owns every unknown provider.

**Data flow**: It receives a provider name. An implementation may check a live broker catalog and returns true if it can serve that provider, false otherwise. The method here is only an interface.

**Call relations**: Code that must choose between a brokered provider and some other credential source can ask this before routing. It is part of the open-namespace connector story.


##### `ConnectorResolver.entry`  (lines 310–310)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: Builds a ConnectorEntry for a provider that is served by an open broker namespace. This gives the registry a normal routing object even when the provider was not explicitly registered.

**Data flow**: It takes a provider name and returns a ConnectorEntry containing that provider, a label, and the shared broker. It is expected to be a pure construction step, with real implementations elsewhere.

**Call relations**: ConnectorRegistry.entry and _broker call through this when a provider is not found in the explicit entries map but a resolver is installed.


##### `ConnectorResolver.catalog`  (lines 312–312)

```
async def catalog(self, query: str, limit: int, after: str | None) -> CatalogPage
```

**Purpose**: Returns one page of connectable services from an open broker catalog. A page is a limited batch plus a cursor that can be used to continue later.

**Data flow**: The caller supplies a query, a maximum number of results, and an optional after cursor. An implementation asks the broker catalog and returns a CatalogPage. This protocol method only defines the expected exchange.

**Call relations**: ConnectorRegistry.search_catalog and ConnectorRegistry.catalog use this to include services that were not statically registered in the application.


##### `ConnectorRegistry.entry`  (lines 329–335)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: Finds the routing entry for a provider. It first checks the explicitly registered connectors, then falls back to the open resolver if one exists.

**Data flow**: It receives a provider name and looks in the registry’s entries mapping. If found, it returns that ConnectorEntry. If not found but a resolver exists, it asks the resolver to create an entry. If neither path works, it raises a KeyError explaining that no connector is installed.

**Call relations**: Dynamic connector execution and discovery use this to decide which broker owns a provider. It is the registry’s main provider-to-broker lookup.


##### `ConnectorRegistry.search_catalog`  (lines 337–342)

```
async def search_catalog(self, query: str, limit: int) -> tuple[CatalogEntry, ...]
```

**Purpose**: Searches only the open resolver’s live catalog and returns matching connectable services. If there is no open resolver, it returns an empty result.

**Data flow**: It receives a query and limit. If a resolver exists, it asks the resolver for the first catalog page and returns that page’s entries. If no resolver exists, it returns an empty tuple. The registry itself is not modified.

**Call relations**: Discovery tools can call this when they want to append open-namespace services to the list of registered connectors.


##### `ConnectorRegistry.catalog`  (lines 344–364)

```
async def catalog(self, query: str, limit: int, after: str | None) -> CatalogPage
```

**Purpose**: Builds one combined catalog page from explicitly registered connectors plus the open broker namespace. It also removes duplicate provider entries.

**Data flow**: It receives search text, a limit, and an optional after cursor. On the first page, it filters registered entries by whether the query appears in the provider or label. It also asks the resolver, if present, for a catalog page. Then it merges both sets, keeps the first entry for each provider, and returns a CatalogPage with the resolver’s continuation cursor.

**Call relations**: Connector discovery uses this when listing services a member can connect. It creates CatalogEntry and CatalogPage objects as the plain data returned to callers.

*Call graph*: 2 external calls (__init__, __init__).


##### `_broker`  (lines 367–373)

```
def _broker(registry: ConnectorRegistry, provider: str) -> ConnectorBroker | None
```

**Purpose**: Finds the broker that should serve a provider, or returns nothing if no broker is known. It is a small internal helper for credential routing.

**Data flow**: It receives the registry and provider name. It checks explicit registry entries first, then asks the resolver for an entry if available. It returns the broker from the entry, or None if there is no match.

**Call relations**: _credential calls this when resolving credentials for brokered, non-direct accounts. Keeping this lookup separate makes the credential function easier to read.

*Call graph*: called by 1 (_credential).


##### `_credential`  (lines 376–389)

```
async def _credential(registry: ConnectorRegistry, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Chooses the right authentication backend for a feed-sync source. Connected accounts go through their connector broker, while direct accounts go through the configured fallback auth backend.

**Data flow**: It receives the registry, workspace id, provider, and account handle. If the account is not the special direct account, it finds a broker and asks it for a Credential. If the account is direct, it asks the fallback AuthProxy. If the needed route does not exist, it raises a RuntimeError.

**Call relations**: _BoundSourceCredentials.credential calls this after doing source-specific checks. _credential itself calls _broker to locate brokered providers.

*Call graph*: calls 1 internal fn (_broker); called by 1 (credential).


##### `_require_source_connection`  (lines 392–416)

```
async def _require_source_connection(workspace_id: UUID, connection_id: UUID, owner_member_id: UUID, provider: str, account: str) -> None
```

**Purpose**: Checks that a feed-sync source is still allowed to use the exact member-owned connection it was bound to. This prevents old or altered sources from silently using a grant that no longer belongs to them.

**Data flow**: It receives workspace id, connection id, owner member id, provider, and account. It opens a workspace-scoped database transaction and queries the connection table for a row matching all of those fields. If a matching row exists, it returns normally. If not, it raises ValueError saying the connection is no longer active for the source.

**Call relations**: _BoundSourceCredentials.credential calls this before resolving a brokered credential, and _ConnectionTransport.handle_async_request calls it before every proxied HTTP request. It uses the workspace context and database transaction helpers to make the check against the correct workspace data.

*Call graph*: called by 2 (credential, handle_async_request); 3 external calls (select, workspace_tx, ws).


##### `_ConnectionTransport.handle_async_request`  (lines 428–436)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: Sends an HTTP request through an inner transport, but only after re-checking that the source’s connection is still valid. This adds a guardrail around every broker-proxied request.

**Data flow**: It receives an outgoing HTTP request. Before forwarding it, it calls _require_source_connection with the saved workspace, connection, member, provider, and account information. If the check passes, it delegates the request to the inner transport and returns the HTTP response. If the check fails, the request is not sent.

**Call relations**: _BoundSourceCredentials.credential creates this wrapper around a broker-provided transport. During feed sync, the HTTP client uses this method whenever it makes a provider request.

*Call graph*: calls 1 internal fn (_require_source_connection).


##### `_ConnectionTransport.aclose`  (lines 438–439)

```
async def aclose(self) -> None
```

**Purpose**: Closes the wrapped HTTP transport when the client is finished. This lets network resources be cleaned up in the same way as the original transport would.

**Data flow**: It receives no new data beyond the wrapper object. It calls aclose on the inner transport and returns when that close operation completes. It does not perform extra checks or produce a value.

**Call relations**: HTTP client cleanup calls this as part of normal shutdown for the transport. It simply passes the close operation through to the broker-provided transport.


##### `_BoundSourceCredentials.credential`  (lines 448–478)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Resolves credentials for one feed-sync source after enforcing whether that source is direct-key based or bound to a specific member connection. It is the main safety gate for source credential access.

**Data flow**: It receives workspace id, provider, and account. If the account is the direct account, it refuses to continue if the source was bound to a connection, then asks _credential for the direct credential. If the account is brokered, it requires stored connection and owner ids, verifies the database connection, asks _credential for a broker credential, checks that the credential contains a proxy transport, and returns a new Credential whose transport is wrapped in _ConnectionTransport for ongoing checks.

**Call relations**: SourceCredentialResolver.bind creates this object for the sync runner. It calls _require_source_connection to verify ownership, _credential to get the backend credential, and wraps broker transports so _ConnectionTransport.handle_async_request can keep enforcing the rule later.

*Call graph*: calls 2 internal fn (_credential, _require_source_connection); 2 external calls (__init__, __init__).


##### `SourceCredentialResolver.bind`  (lines 485–490)

```
def bind(self, connection_id: UUID | None, owner_member_id: UUID | None) -> AuthProxy
```

**Purpose**: Creates an AuthProxy tied to the connection information for a particular feed-sync source. Binding means later credential requests remember whether the source is direct or tied to a specific member-owned connection.

**Data flow**: It receives an optional connection id and optional owner member id. It combines them with the registry stored on the SourceCredentialResolver and returns a _BoundSourceCredentials object. It does not contact the database yet.

**Call relations**: The sync runner uses this before asking a source to fetch provider records. The returned object later performs the real credential routing and connection checks in _BoundSourceCredentials.credential.

*Call graph*: 1 external calls (__init__).


### `extensions/connectors/ufo_ext_connectors/tools.py`

`orchestration` · `request handling and tool execution`

A broker is a middle layer that knows how to talk to many outside services. Instead of giving the agent thousands of fixed tools, this file gives it a small set of discovery and execution tools. First the agent can search for available connectors. Then it can ask one connector what tools it really offers. Finally it can call one tool with arguments.

The file also deals with the messy parts that happen around a call. If an argument points to a workspace file, the file is uploaded from inside the sandbox to the broker's file store, and the argument is replaced with the broker's file reference. If the external tool returns files, they are downloaded into a safe folder under the workspace. If a provider returns base64 text, which is encoded data that is unreadable as-is, this file decodes it. Small text is kept inline; large or binary content is written to a workspace file.

Slack gets one special rule: if the agent sends a Slack message through a connector, this file appends a small "Sent using ufo" attribution so published text is marked. Finally, repeated large objects in results can be replaced with a pointer to the first copy, like saying "same as item 1" instead of printing the same page again.

#### Function details

##### `list_external_tools`  (lines 246–282)

```
async def list_external_tools(ctx: ToolContext, args: ListExternalToolsInput) -> ToolResult
```

**Purpose**: Searches the live connector registry for external services the agent may use. It returns connector IDs, human labels, and any accounts already connected for each connector.

**Data flow**: It receives the tool context and a list of search queries. It reads the current connector registry, asks for connected accounts, checks local connector names and labels, then also asks the registry catalog for matches. It returns a JSON tool result containing matching connectors.

**Call relations**: This is one of the public connector tools. It starts by calling _registry to get the current connector list, calls _connected_accounts so the answer says which accounts are usable, uses asyncio.gather to search catalog queries in parallel, and finishes through _json_result.

*Call graph*: calls 3 internal fn (_connected_accounts, _json_result, _registry); 1 external calls (gather).


##### `_connected_accounts`  (lines 285–299)

```
async def _connected_accounts(ctx: ToolContext) -> dict[str, list[JsonValue]]
```

**Purpose**: Collects the connected accounts that the current agent is allowed to use. This helps a connector listing answer not just "Slack exists" but "this Slack account is already available."

**Data flow**: It reads grants from the tool context. If there are no grants, it returns an empty mapping. Otherwise it groups active grants by provider and records account ID, owner email, and whether the connection is shared.

**Call relations**: list_external_tools calls this while building connector search results, so each returned connector can include its usable accounts without a second lookup.

*Call graph*: called by 1 (list_external_tools).


##### `describe_external_tools`  (lines 302–324)

```
async def describe_external_tools(ctx: ToolContext, args: DescribeExternalToolsInput) -> ToolResult
```

**Purpose**: Describes real tools offered by one connector. It is used before execution so the agent can learn exact tool names and input shapes instead of guessing.

**Data flow**: It receives a connector ID, optional exact tool names, and an optional discovery query. It asks the connector's broker for schemas for named tools, records any unknown names, and may also ask for catalog tools matching a query. It returns JSON with schemas, discovered tools, notes, and unresolved names.

**Call relations**: This public tool gets the connector through _registry, formats individual schemas with _tool_json, builds fallback search text with _discovery_query when needed, passes discovered catalog rows through _discovered_rows, and wraps the final answer with _json_result.

*Call graph*: calls 5 internal fn (_discovered_rows, _discovery_query, _json_result, _registry, _tool_json).


##### `attribution_stripped`  (lines 327–331)

```
def attribution_stripped(text: str) -> str
```

**Purpose**: Removes ufo's Slack attribution text from a message that was read back from Slack. This lets the system tell the difference between a real user mention and the footer that this deployment added itself.

**Data flow**: It receives message text. It applies the attribution pattern anywhere in that text and returns the text with matching attribution fragments removed.

**Call relations**: This helper is not shown as called by another function in this file's call graph, but it belongs to the same Slack attribution story as slack_attributed and attributed_arguments.


##### `attributed_arguments`  (lines 334–361)

```
def attributed_arguments(arguments: dict[str, JsonValue], subject: str) -> dict[str, JsonValue]
```

**Purpose**: Adds a Slack footer to outgoing message arguments when there is a body to mark. It avoids adding a second footer if one is already present.

**Data flow**: It receives a dictionary of connector arguments and the attribution subject to show. It first checks whether the arguments already carry attribution. If they use Slack blocks, it appends a footer block. If they use markdown_text or text, it converts the body into suitable Slack blocks and adds the footer. If there is no readable message body, it returns the arguments unchanged.

**Call relations**: slack_attributed calls this only for likely Slack send operations. Inside, it uses _carries_attribution to avoid stacking footers, _appended_blocks for existing block payloads, and _body_blocks for plain text or markdown bodies.

*Call graph*: calls 3 internal fn (_appended_blocks, _body_blocks, _carries_attribution); called by 1 (slack_attributed).


##### `_body_blocks`  (lines 364–392)

```
def _body_blocks(arguments: dict[str, JsonValue]) -> list[JsonValue] | None
```

**Purpose**: Turns Slack message text arguments into Slack block objects so a footer block can be placed after them. It preserves the difference between Slack markdown styles instead of mixing them.

**Data flow**: It reads the arguments dictionary. If markdown_text is present, it returns one markdown block. If text is present, it splits the text into section blocks that fit Slack's per-block size limit. If neither body exists, it returns None.

**Call relations**: attributed_arguments calls this when the outgoing Slack arguments do not already contain a blocks value but may contain a text body that needs a footer.

*Call graph*: called by 1 (attributed_arguments).


##### `_appended_blocks`  (lines 395–416)

```
def _appended_blocks(value: JsonValue, footer: dict[str, JsonValue]) -> JsonValue | None
```

**Purpose**: Adds a footer block to an existing Slack blocks argument when that argument can be safely understood. It supports both normal lists and JSON strings because brokers may accept either spelling.

**Data flow**: It receives a blocks value and a footer block. If the value is a non-empty list, it returns a new list with the footer appended. If it is a string, it tries to parse it as JSON, including URL-decoded JSON, checks it is a non-empty list without existing attribution, and returns the same form with the footer added. If it cannot safely parse or append, it returns None.

**Call relations**: attributed_arguments calls this for Slack sends that already provide blocks. It calls _carries_attribution to avoid duplicating a footer and uses JSON and URL quoting helpers to preserve the original representation.

*Call graph*: calls 1 internal fn (_carries_attribution); called by 1 (attributed_arguments); 4 external calls (dumps, loads, quote, unquote).


##### `_carries_attribution`  (lines 419–428)

```
def _carries_attribution(value: JsonValue) -> bool
```

**Purpose**: Checks whether a value already contains a ufo attribution footer. This is the guard that stops repeated sends or edits from piling up multiple footers.

**Data flow**: It receives any JSON-like value. It searches strings directly, recursively checks lists, and recursively checks dictionary values. It returns true as soon as it finds attribution text.

**Call relations**: attributed_arguments uses it before adding any footer, and _appended_blocks uses it after parsing serialized Slack blocks.

*Call graph*: called by 2 (_appended_blocks, attributed_arguments); 1 external calls (values).


##### `slack_attributed`  (lines 431–445)

```
def slack_attributed(provider: str, slug: str, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Applies the Slack attribution rule only to connector calls that look like message sends. Reads, listings, edits without send-like names, and non-Slack providers are left unchanged.

**Data flow**: It receives the provider name, tool slug, and argument dictionary. It checks that the provider is Slack and that the tool name contains message-related and send-related words. If so, it returns arguments with attribution added; otherwise it returns the original arguments.

**Call relations**: call_external_tool calls this just before execution. When the call is a Slack send, it hands the work to attributed_arguments.

*Call graph*: calls 1 internal fn (attributed_arguments); called by 1 (call_external_tool).


##### `call_external_tool`  (lines 448–465)

```
async def call_external_tool(ctx: ToolContext, args: CallExternalToolInput) -> ToolResult
```

**Purpose**: Runs one external connector tool through its broker. It also enforces read-only mode and applies Slack attribution before the broker call.

**Data flow**: It receives a connector ID, tool name, optional account ID, and tool arguments. It finds the connector, checks whether a write is forbidden in read-only mode, gets the chosen connected account, adjusts Slack send arguments if needed, creates a _ConnectorCall, and returns the execution text as a tool result. If a forbidden write is attempted, it returns a clear failure instead.

**Call relations**: This is the public execution tool. It calls _registry to find the connector, asks the ToolContext for a connector connection, calls slack_attributed, then delegates the full staged-execute-fetch-translate flow to _ConnectorCall.run.

*Call graph*: calls 3 internal fn (connector_connection, _registry, slack_attributed); 4 external calls (__init__, __init__, __init__, __init__).


##### `_ConnectorCall.run`  (lines 493–507)

```
async def run(self, arguments: dict[str, JsonValue], connection: ConnectorConnection) -> str
```

**Purpose**: Performs one connector tool execution from start to finish. It is the main pipeline for staging input files, calling the broker, fetching output files, cleaning up encoded data, and shrinking repeated result objects.

**Data flow**: It receives already prepared arguments and a connector connection. It stages any workspace-file arguments, confirms the connection is allowed, asks the broker to execute the tool, downloads any broker-reported output files, translates base64 content in the response, adds workspace file references when present, and returns a JSON string with repeated objects condensed.

**Call relations**: call_external_tool creates _ConnectorCall and invokes this method. This method calls _staged_value before execution, _fetched_files and _translated_node after execution, and runs _deduped in a worker thread so the main event loop is not blocked by result shrinking.

*Call graph*: calls 3 internal fn (_fetched_files, _staged_value, _translated_node); 1 external calls (to_thread).


##### `_ConnectorCall._staged_value`  (lines 509–524)

```
async def _staged_value(self, value: object) -> object
```

**Purpose**: Walks through tool arguments and replaces any workspace file marker with a broker-ready file reference. This lets tools receive files without the main server reading and uploading the bytes itself.

**Data flow**: It receives any argument value. If the value is exactly a workspace_file object, it validates the path and stages that file. If it is a dictionary or list, it recursively processes its children. Other values pass through unchanged.

**Call relations**: _ConnectorCall.run calls this for each top-level argument before broker execution. When it finds a real file marker, it hands the path to _stage_file.

*Call graph*: calls 1 internal fn (_stage_file); called by 1 (run).


##### `_ConnectorCall._stage_file`  (lines 526–562)

```
async def _stage_file(self, path: str) -> dict[str, object]
```

**Purpose**: Uploads one workspace file to the broker's file storage in a sandbox-safe way. It returns the broker's argument value that represents that uploaded file.

**Data flow**: It receives a workspace path. It normalizes the path, asks the sandbox to measure the file and compute an MD5 hash, rejects unreadable or too-large files, guesses a content type, asks the broker for an upload location, and if needed tells the sandbox to PUT the file there with curl. It returns the broker-provided argument reference.

**Call relations**: _staged_value calls this when an argument contains a workspace_file marker. It uses workspace_path, mimetype guessing, shell quoting, and sandbox commands so file bytes travel from the sandbox to the broker rather than through the serving process.

*Call graph*: called by 1 (_staged_value); 4 external calls (guess_type, PurePosixPath, quote, workspace_path).


##### `_ConnectorCall._fetched_files`  (lines 564–597)

```
async def _fetched_files(self, files: tuple[BrokerFile, ...]) -> list[dict[str, str]]
```

**Purpose**: Downloads files produced by an external tool into the workspace. It gives each fetched file a safe, unique location so provider-chosen names cannot overwrite something important.

**Data flow**: It receives broker file records, each with a name and download URL. For each one, it reduces the name to a safe leaf filename, claims a fresh workspace path under connector_files, downloads the URL into that path from inside the sandbox, and records the saved name and workspace path. It returns the list of saved file references.

**Call relations**: _ConnectorCall.run calls this after broker execution, using the broker's file_outputs view of the response. It relies on contained_leaf, uuid4, shell quoting, and sandbox commands for safe placement.

*Call graph*: called by 1 (run); 3 external calls (quote, contained_leaf, uuid4).


##### `_ConnectorCall._translated_node`  (lines 599–659)

```
async def _translated_node(self, node: Mapping[str, object], depth: int=0) -> dict[str, object]
```

**Purpose**: Looks at one result object and decodes fields that the provider explicitly marked as base64. This turns unreadable encoded blobs into useful text or workspace file references.

**Data flow**: It receives a mapping from the broker response and a recursion depth. It first recursively translates child values. If the object contains a base64 marker and known content fields, it tries to decode those fields. Decoded small UTF-8 text replaces the encoded string; binary or large content is offloaded to a workspace file reference. The object's encoding marker is updated only when all marked fields were successfully translated.

**Call relations**: _ConnectorCall.run starts response translation here, and _translated calls it for nested dictionaries. It calls _translated for children, _decoded_base64 for marked fields, _translated_bytes to choose inline text versus file, and mimetype guessing to label offloaded content.

*Call graph*: calls 3 internal fn (_translated, _translated_bytes, _decoded_base64); called by 2 (_translated, run); 1 external calls (guess_type).


##### `_ConnectorCall._translated`  (lines 661–680)

```
async def _translated(self, value: object, depth: int) -> object
```

**Purpose**: Recursively translates any value inside a broker result. It handles nested objects, lists, and standalone data URLs while leaving ordinary values alone.

**Data flow**: It receives a value and a depth counter. If nesting is too deep, it returns the value unchanged. Dictionaries go to _translated_node, lists are walked item by item, and short data: base64 URLs are decoded through _translated_data_url. Everything else is returned as-is.

**Call relations**: _translated_node calls this for child values. It calls back into _translated_node for dictionaries and calls _translated_data_url for self-contained base64 data URLs.

*Call graph*: calls 2 internal fn (_translated_data_url, _translated_node); called by 1 (_translated_node).


##### `_ConnectorCall._translated_data_url`  (lines 682–694)

```
async def _translated_data_url(self, value: str) -> object
```

**Purpose**: Decodes a standalone data URL that contains base64 payload data. This covers results where the encoded file is packed into a single string rather than a marked JSON object.

**Data flow**: It receives a string beginning with data:. If it matches the expected data URL shape and the payload is valid base64, it decodes the bytes, derives a fallback filename from the MIME type, and returns either text or a file reference. If parsing or decoding fails, it returns the original string.

**Call relations**: _translated calls this for short strings that start with data:. This helper uses _decoded_base64 and then hands decoded bytes to _translated_bytes.

*Call graph*: calls 2 internal fn (_translated_bytes, _decoded_base64); called by 1 (_translated); 1 external calls (guess_extension).


##### `_ConnectorCall._translated_bytes`  (lines 696–705)

```
async def _translated_bytes(self, decoded: bytes, text: str | None, name: str, mimetype: str) -> object
```

**Purpose**: Chooses how decoded bytes should appear in the tool result. Small readable text stays inline; large text and binary data are written to a workspace file.

**Data flow**: It receives decoded bytes, optional decoded UTF-8 text, a filename, and a MIME type. If text exists and is below the inline size limit, it returns the text. Otherwise it calls _offloaded and returns that file reference.

**Call relations**: _translated_node and _translated_data_url call this after successful base64 decoding. It delegates file writing to _offloaded when inline text would be too large or impossible.

*Call graph*: calls 1 internal fn (_offloaded); called by 2 (_translated_data_url, _translated_node).


##### `_ConnectorCall._offloaded`  (lines 707–749)

```
async def _offloaded(self, name: str, mimetype: str, data: bytes) -> dict[str, object]
```

**Purpose**: Writes decoded result bytes into the workspace and returns a reference to that file. This keeps large or binary content out of the chat context while still making it available to the agent.

**Data flow**: It receives a suggested name, MIME type, and bytes. It makes the filename safe, builds a content-addressed path using a SHA-256 hash, writes the bytes to a temporary part file through the sandbox, then atomically places it at the final path. It returns a dictionary with name, workspace path, MIME type, and byte count.

**Call relations**: _translated_bytes calls this whenever decoded data should not be kept inline. It uses contained_leaf, sha256, uuid4, and sandbox file operations to avoid unsafe overwrites and duplicate copies.

*Call graph*: called by 1 (_translated_bytes); 3 external calls (sha256, contained_leaf, uuid4).


##### `_ConnectorCall._deduped`  (lines 751–809)

```
def _deduped(self, payload: dict[str, object]) -> str
```

**Purpose**: Serializes the result and, when safe and worthwhile, replaces repeated large objects with same_as pointers. This keeps bulky repeated data from pushing useful results out of the model's immediate context.

**Data flow**: It receives the final payload dictionary. It serializes it once, skips deduplication if the payload is too large, too structurally dense, or already contains the same_as key, and otherwise walks the payload to find repeated objects. It returns a JSON string, either unchanged or condensed.

**Call relations**: _ConnectorCall.run calls this in a worker thread after file fetching and base64 translation. It calls _condensed to walk the data and _escaped to build JSON Pointer paths.

*Call graph*: calls 2 internal fn (_condensed, _escaped); 1 external calls (dumps).


##### `_ConnectorCall._condensed`  (lines 811–887)

```
def _condensed(self, value: object, pointer: str, depth: int, first: dict[bytes, str]) -> tuple[object, bytes, int]
```

**Purpose**: Walks one node of the result tree and detects repeated dictionary objects by their structure and contents. Later copies of a large repeated object become a pointer to the first copy.

**Data flow**: It receives a value, its JSON Pointer path, the current depth, and a map of first-seen object hashes. It recursively processes dictionaries and lists, computes a SHA-256 digest for each node, estimates the original size, records large first-seen dictionaries, and replaces later identical dictionaries with a same_as pointer. It returns the possibly changed value, its digest, and its original size estimate.

**Call relations**: _deduped calls this for each top-level payload value. During recursion it calls itself for children, uses _escaped when extending pointer paths, and uses sha256 to compare content without repeatedly serializing whole subtrees.

*Call graph*: calls 1 internal fn (_escaped); called by 1 (_deduped); 1 external calls (sha256).


##### `_escaped`  (lines 890–893)

```
def _escaped(token: str) -> str
```

**Purpose**: Escapes one path segment for a JSON Pointer. This makes pointers work even when an object key contains special characters like / or ~.

**Data flow**: It receives a string key. It replaces ~ with ~0 and / with ~1, then returns the escaped token.

**Call relations**: _deduped and _condensed use this while building same_as pointer paths that accurately name where the first full copy of an object appears.

*Call graph*: called by 2 (_condensed, _deduped).


##### `_decoded_base64`  (lines 896–920)

```
def _decoded_base64(value: object) -> tuple[bytes, str | None] | None
```

**Purpose**: Strictly decodes a value that is supposed to be base64. It refuses invalid, non-string, or too-large values rather than guessing and possibly corrupting provider data.

**Data flow**: It receives any value. If it is a short enough string, it removes whitespace, tries strict base64 decoding, and then tries to decode the bytes as UTF-8 text. It returns bytes plus optional text, or None if decoding should not happen.

**Call relations**: _translated_node calls this for fields marked by a provider as base64, and _translated_data_url calls it for data URL payloads. It uses base64.b64decode for the actual decoding.

*Call graph*: called by 2 (_translated_data_url, _translated_node); 1 external calls (b64decode).


##### `search_connector_tools`  (lines 923–937)

```
async def search_connector_tools(ctx: ToolContext, args: SearchConnectorToolsInput) -> ToolResult
```

**Purpose**: Performs richer tool discovery inside one connector using a natural-language goal. It can return matching tool schemas plus broker-supplied planning advice, guidance, and warnings.

**Data flow**: It receives a connector ID and search query. It finds the connector, asks its broker to search within that connector, trims and annotates the returned tools, and returns JSON with tools, plan, guidance, pitfalls, and any note.

**Call relations**: This is a public discovery tool. It calls _registry to find the connector, _discovered_rows to apply fallback and size-budget rules, and _json_result to package the answer.

*Call graph*: calls 3 internal fn (_discovered_rows, _json_result, _registry).


##### `_registry`  (lines 940–943)

```
def _registry(ctx: ToolContext) -> ConnectorRegistry
```

**Purpose**: Fetches the connector registry from the current tool context. It fails loudly if connector tools are being used in a turn that has no registry.

**Data flow**: It receives the tool context. If the context has a connector registry, it returns it. If not, it raises a runtime error explaining that connector dispatch lacks the needed registry.

**Call relations**: list_external_tools, describe_external_tools, search_connector_tools, and call_external_tool all call this at their start because every connector operation depends on the live registry for the current turn.

*Call graph*: called by 4 (call_external_tool, describe_external_tools, list_external_tools, search_connector_tools).


##### `_tool_json`  (lines 946–947)

```
def _tool_json(tool: BrokerTool) -> dict[str, object]
```

**Purpose**: Converts a broker tool object into the simple JSON shape returned to the agent. It exposes the slug, description, and input schema.

**Data flow**: It receives a BrokerTool. It reads its slug, description, and input_schema fields and returns them in a plain dictionary.

**Call relations**: describe_external_tools uses this for exact schema lookups, and _available_tools uses it while building discovery rows.

*Call graph*: called by 2 (_available_tools, describe_external_tools).


##### `_discovered_rows`  (lines 950–967)

```
async def _discovered_rows(entry: ConnectorEntry, workspace_id: UUID, query: str, found: tuple[BrokerTool, ...]) -> tuple[list[dict[str, object]], str]
```

**Purpose**: Builds the tool list shown by discovery calls and adds notes when the list is a fallback or was shortened. This prevents an empty search result from misleading the agent into thinking a connector has no useful tools.

**Data flow**: It receives a connector entry, workspace ID, query, and broker-found tools. If a non-empty query found nothing, it asks the broker for the connector's unfiltered top tools instead. It trims the rows to the inline budget and returns the rows plus an explanatory note when fallback or omission happened.

**Call relations**: describe_external_tools and search_connector_tools both call this so both discovery paths follow the same "never a dead end" behavior. It calls _available_tools to format and limit the visible rows.

*Call graph*: calls 1 internal fn (_available_tools); called by 2 (describe_external_tools, search_connector_tools).


##### `_available_tools`  (lines 970–983)

```
def _available_tools(listed: tuple[BrokerTool, ...]) -> list[dict[str, object]]
```

**Purpose**: Formats a connector's tool list while keeping the answer small enough to remain useful in context. It stops before the listing becomes so large that the engine would likely offload it to a file.

**Data flow**: It receives broker tools in catalog order. For each tool, it converts it with _tool_json, counts the JSON size spent so far, and stops once the budget is exceeded after at least one row. It returns the rows that fit.

**Call relations**: _discovered_rows calls this for both normal search results and fallback top-tool listings. It uses json.dumps only to estimate how much space each row will take.

*Call graph*: calls 1 internal fn (_tool_json); called by 1 (_discovered_rows); 1 external calls (dumps).


##### `_discovery_query`  (lines 986–993)

```
def _discovery_query(explicit: str, unresolved: list[str]) -> str
```

**Purpose**: Chooses the catalog query used when exact tool names could not be resolved. It turns guessed or wrong slugs into useful search words.

**Data flow**: It receives an explicit query and a list of unresolved tool names. If the explicit query is present, it returns that. Otherwise it lowercases unresolved names, replaces non-letter and non-number runs with spaces, removes duplicate words while preserving order, and returns the resulting search phrase.

**Call relations**: describe_external_tools calls this when it needs to discover alternatives, especially after a caller provided tool names that the broker did not recognize.

*Call graph*: called by 1 (describe_external_tools); 1 external calls (sub).


##### `_json_result`  (lines 996–997)

```
def _json_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: Wraps a dictionary payload as the text content of a ToolResult. It is the common final step for the discovery-style connector tools.

**Data flow**: It receives a dictionary. It serializes it to JSON text, puts that text in a TextContent object, and returns a ToolResult containing it.

**Call relations**: list_external_tools, describe_external_tools, and search_connector_tools call this after building their response payloads.

*Call graph*: called by 3 (describe_external_tools, list_external_tools, search_connector_tools); 3 external calls (__init__, __init__, dumps).


### Composio execution path
These files route Composio-backed providers into a shared broker, discover and run Composio tools, handle MCP tool calls, and proxy provider HTTP requests without exposing secrets.

### `extensions/composio/ufo_ext_composio/resolver.py`

`orchestration` · `connector discovery and connect flow`

Composio can connect to many outside services, called toolkits. Rather than listing every toolkit inside this project, this file provides a resolver: a small decision-maker that answers, “Is this provider something Composio can connect to, and if so, how should UFO connect through it?”

The main class, ComposioResolver, is deliberately simple. It keeps only one shared ConnectorBroker, which is the part that later runs Composio-backed tools. Everything else is looked up when needed, so tests or runtime settings can swap the Composio client without stale connections hanging around.

When a provider name is checked, the resolver first blocks names that are locally banned. If the name is not banned, it asks Composio’s live catalog whether that toolkit is connectable. This matters because the system should not offer a connection that the user cannot actually complete.

If the provider is valid, the resolver can create an OAuthProvider description. OAuth is the common “sign in with this service” flow, but here the token stays with Composio and tools run server-side through Composio, so no provider host is stored here. The resolver can also create a ConnectorEntry, which gives the provider a friendly label and routes it to the shared broker. Finally, it can search Composio’s catalog for discovery, returning only services Composio says are connectable.

#### Function details

##### `ComposioResolver.transfer_hosts`  (lines 32–33)

```
def transfer_hosts(self) -> tuple[str, ...]
```

**Purpose**: This property tells the rest of the system which Composio file-transfer hosts are allowed. It matters because Composio-backed tools may need to pass files in and out through approved sandbox paths rather than arbitrary network locations.

**Data flow**: It takes no input beyond the resolver instance. It reads the fixed COMPOSIO_TRANSFER_HOSTS list from the Composio client module and returns it as a tuple of host names; it does not change anything.

**Call relations**: When the connector system needs to know which external file-store hosts are safe for Composio grants, it asks this resolver. This property simply hands back the shared Composio transfer-host list for the surrounding sandbox and broker logic to use.


##### `ComposioResolver.claims`  (lines 35–38)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: This function answers the question, “Does Composio claim this provider name?” It prevents banned provider names from being accepted, then checks Composio’s live catalog to make sure the requested toolkit really exists and can be connected.

**Data flow**: It receives a provider name as text. First it lowercases the name and compares it with Composio’s local banned list; if it is banned, the answer is false immediately. Otherwise it creates or fetches the current Composio client, asks whether the toolkit is connectable, and returns true only when Composio returns a matching toolkit.

**Call relations**: During connector resolution, this is the gatekeeper for Composio’s open namespace. It calls ufo_ext_composio.client.composio_client to get the current Composio API client, then relies on that client’s connectable-toolkit lookup before later steps build a descriptor or entry.

*Call graph*: 1 external calls (composio_client).


##### `ComposioResolver.descriptor`  (lines 40–41)

```
def descriptor(self, provider: str) -> OAuthProvider
```

**Purpose**: This function builds the OAuth connection description for a Composio-backed provider. It gives the connect flow enough information to start a Composio OAuth connection without pretending the outside provider is contacted directly by this system.

**Data flow**: It receives the provider name. It creates a ComposioOAuthProvider with that provider name and an empty host string, then returns that provider description; no outside service is contacted and no stored state changes.

**Call relations**: After a provider has been accepted as Composio-connectable, the connect flow can ask this resolver for the connection descriptor. This function hands off to ComposioOAuthProvider.__init__, which packages the provider name in the Composio-specific OAuth shape.

*Call graph*: 1 external calls (__init__).


##### `ComposioResolver.entry`  (lines 43–46)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: This function creates the registry entry that tells the system how to route a Composio-backed provider. It also makes a human-friendly label from the provider slug, such as turning underscores into spaces and title-casing the words.

**Data flow**: It receives a provider name. It builds a ConnectorEntry containing the original provider slug, a display label derived from that slug, and the shared broker stored on the resolver; then it returns that entry without changing the resolver.

**Call relations**: Once the resolver has claimed a provider, the connector registry can ask for an entry. This function calls ConnectorEntry.__init__ to package the provider together with the shared Composio broker, so later tool execution is routed through that broker.

*Call graph*: 1 external calls (__init__).


##### `ComposioResolver.catalog`  (lines 48–51)

```
async def catalog(self, query: str, limit: int=TOOLKIT_SEARCH_LIMIT, after: str | None=None) -> CatalogPage
```

**Purpose**: This function searches Composio’s toolkit catalog for connectable services. It supports discovery tools, so users can search for services without the project hard-coding every possible Composio integration.

**Data flow**: It receives a search query, an optional result limit, and an optional cursor called after, which means “continue after this point” for paging through results. It gets the current Composio client, asks it to list matching toolkits, and returns a CatalogPage containing the search results and paging information.

**Call relations**: When a discovery flow needs to show possible Composio connectors, it calls this catalog function. The function calls ufo_ext_composio.client.composio_client to use the current Composio API client, then delegates the actual catalog search to that client.

*Call graph*: 1 external calls (composio_client).


### `extensions/composio/ufo_ext_composio/broker.py`

`io_transport` · `request handling`

This file defines ComposioBroker, the shared doorway used when UFO talks to Composio-backed connectors. A connector broker is like a front desk: the rest of the system asks for available tools, tool schemas, uploads, execution, search, or credentials, and the broker translates those requests into Composio API calls.

A key safety idea here is that the broker fetches the Composio client fresh for each call. That means tests can swap in a fake transport, and long-lived objects do not accidentally keep stale network settings. When tools are listed or inspected, Composio's raw tool records are turned into UFO's BrokerTool shape, including a rewritten file-upload schema so dynamic tools know how to stage files first.

When a tool is executed, the broker runs it as the workspace's broker user. It adds helpful guidance for two common failures: if the tool slug is wrong, it tries to show real available slugs; if the connected account is stale or no longer owned by this broker, it tells the agent to ask the member to reconnect instead of guessing another tool.

For files, the broker can find Composio file outputs hidden anywhere inside a nested response, and it can create upload slots for file inputs. For credentials, it does not return provider tokens. Instead, it returns a Credential whose network transport proxies provider HTTP through Composio, which is the guardrail that keeps secrets out of the feed-sync code.

#### Function details

##### `ComposioBroker.tools`  (lines 49–50)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Finds Composio tools for one provider and search phrase, then converts them into the broker's standard tool format. Someone uses this when the system needs to show or choose what actions are available.

**Data flow**: It receives a workspace id, provider name, and query text. It asks the current Composio client for matching tools, then passes the returned rows through the local converter so descriptions, slugs, schemas, and read-only hints are in UFO's expected shape. It returns a tuple of BrokerTool objects.

**Call relations**: This is a discovery entry point for the broker. It gets the active Composio client for the call, relies on Composio's list API for raw results, and hands those results to _discovered_tools so the rest of UFO does not need to understand Composio's raw catalog format.

*Call graph*: calls 1 internal fn (_discovered_tools); 1 external calls (composio_client).


##### `ComposioBroker.schema`  (lines 52–66)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Fetches the detailed schema for one Composio tool slug. This tells UFO what inputs the tool accepts, including rewriting file-upload inputs into the workspace-file language UFO expects.

**Data flow**: It receives a workspace id, provider, and tool slug. It asks Composio for that tool's schema, turns file-upload fields into UFO's workspace file schema, reads the description and read-only marker, and returns a BrokerTool. If Composio says the slug does not exist, it raises UnknownBrokerTool so callers can treat it as a bad tool name.

**Call relations**: This is called when UFO needs exact instructions for one tool rather than a search listing. It uses the active Composio client, delegates file-schema rewriting to the Composio client helper, uses _read_only to interpret tags, and converts a Composio 404 into the broker-level UnknownBrokerTool error.

*Call graph*: calls 1 internal fn (_read_only); 4 external calls (__init__, __init__, composio_client, workspace_file_schema).


##### `ComposioBroker.execute`  (lines 68–91)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Runs a Composio tool on behalf of a workspace and a connected account. It also turns confusing Composio failures into more useful guidance for the agent.

**Data flow**: It receives the workspace id, provider, tool slug, input arguments, connected account id, and an optional idempotency key, which is a repeat-safe request key. It builds the workspace's Composio broker-user id, sends the tool execution request, and returns Composio's response dictionary. If the account looks stale, it raises a reconnect-oriented error; if the slug is missing, it tries to augment the error with real available tool slugs.

**Call relations**: This is the main execution path after a tool has been selected. It calls the current Composio client to run the tool, checks failures with _stale_account, creates reconnect guidance through _reconnect_error, and asks _slug_miss to improve a missing-tool error before handing the result or exception back to the caller.

*Call graph*: calls 3 internal fn (_slug_miss, _reconnect_error, _stale_account); 1 external calls (composio_client).


##### `ComposioBroker.file_outputs`  (lines 93–98)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Extracts files produced by a Composio tool response. It matters because files may be buried inside nested response data rather than placed in one predictable field.

**Data flow**: It receives a response dictionary from a tool execution. It creates an empty collection, asks _collect_files to walk through the whole response, and returns every discovered file as BrokerFile objects. It does not change the response itself.

**Call relations**: This is used after a tool runs, when UFO needs to know whether the result contains downloadable files. It delegates the recursive searching work to _collect_files and returns a clean list-like tuple to the caller.

*Call graph*: calls 1 internal fn (_collect_files).


##### `ComposioBroker.stage_upload`  (lines 100–116)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Creates a temporary upload destination in Composio's file store for a file that will be passed into a tool. This is the preparation step before the sandbox uploads bytes for a file input.

**Data flow**: It receives workspace and provider context, the target tool slug, filename, MIME type, and MD5 checksum. It asks Composio to create an upload slot, then returns a StagedUpload containing the URL to PUT the file to, the content type to use, and the argument object that should later be sent to the tool.

**Call relations**: This is called before executing a tool that needs a file input. It uses the active Composio client to reserve storage, then packages Composio's upload key into the broker's standard staged-upload form so later tool execution can refer to the uploaded file.

*Call graph*: 2 external calls (__init__, composio_client).


##### `ComposioBroker.search`  (lines 118–121)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Runs a broader tool search through Composio's Tool Router. This is useful when a plain catalog lookup is not enough and the system wants Composio's search service to suggest relevant tools.

**Data flow**: It receives a workspace id, provider, and query text. It gets the active Composio client and passes the search request to the Composio search helper. It returns a BrokerSearch result.

**Call relations**: This broker method is a thin bridge into Composio's search flow. Rather than interpreting results locally, it hands the request to search_connector_tools with the current client and workspace context.

*Call graph*: 2 external calls (composio_client, search_connector_tools).


##### `ComposioBroker.credential`  (lines 123–139)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a safe Credential for making provider HTTP requests through Composio's proxy, not by exposing a real provider token. It first checks that the connected account belongs to this workspace's broker user, which prevents one workspace from using another workspace's grant.

**Data flow**: It receives a workspace id, provider, and connected account id. It builds the broker-user id and asks Composio to confirm that this account is connected for that user and provider. If the account is missing, it raises GrantUnusable with reconnection guidance; otherwise it returns a Credential whose transport sends requests through Composio's proxy using the Composio API key and connected account id.

**Call relations**: This is used when code needs to talk to a provider through an existing Composio connection. It verifies ownership through the Composio client, turns stale grants into a clear broker error, and then builds ComposioProxyTransport inside a Credential so downstream code can make HTTP calls without receiving secrets.

*Call graph*: calls 1 internal fn (__init__); 5 external calls (__init__, __init__, AsyncHTTPTransport, stale_grant_guidance, composio_client).


##### `ComposioBroker._slug_miss`  (lines 141–162)

```
async def _slug_miss(self, client: composio.ComposioClient, provider: str, slug: str, error: composio.ComposioError) -> composio.ComposioError
```

**Purpose**: Improves a missing-tool error by adding nearby real tool slugs for the same provider. This gives the agent useful next-step information instead of only saying the requested slug was not found.

**Data flow**: It receives the Composio client, provider, bad slug, and original Composio error. It turns the bad slug into search words, asks Composio for matching tools, and if needed falls back to listing tools without a query. If it finds tools, it returns a new ComposioError whose message includes available slugs; if anything goes wrong or no tools are found, it returns the original error.

**Call relations**: This helper is called by ComposioBroker.execute only after Composio reports a missing slug. It reuses _discovered_tools to normalize listed tools, and it deliberately treats the extra lookup as best effort so error handling never masks the original Composio problem with a secondary discovery failure.

*Call graph*: calls 2 internal fn (_discovered_tools, list_tools); called by 1 (execute); 2 external calls (sub, ComposioError).


##### `_collect_files`  (lines 165–174)

```
def _collect_files(value: object, found: list[BrokerFile]) -> None
```

**Purpose**: Walks through nested response data and finds Composio file objects. It recognizes the small pattern Composio uses for produced files: a name, MIME type, and presigned download URL.

**Data flow**: It receives any value and a list where found files should be added. If the value looks like a Composio file object with a non-empty s3url, it appends a BrokerFile with the file name and URL. If the value is a dictionary or list, it recursively checks each child; otherwise it does nothing.

**Call relations**: This is the worker behind ComposioBroker.file_outputs. The public method sets up the empty list, and this helper searches every nested branch so callers get all file outputs without caring where Composio placed them in the response.

*Call graph*: called by 1 (file_outputs); 1 external calls (__init__).


##### `_stale_account`  (lines 177–188)

```
def _stale_account(error: composio.ComposioError, account_id: str) -> bool
```

**Purpose**: Decides whether a Composio execution error is really saying the connected account is gone or no longer valid. This helps the system tell the user to reconnect instead of wrongly treating the problem as a missing tool.

**Data flow**: It receives a Composio error and the connected account id that was used. It lowercases the error message and looks for narrow signs of a missing connected account: Composio's own words, or the specific account id, together with 'not found'. It returns true if the error matches that stale-account pattern, otherwise false.

**Call relations**: ComposioBroker.execute calls this before checking for a missing tool slug. That order matters: a dead account may also appear as a 404, and this helper keeps that from being turned into a misleading list of possible tool names.

*Call graph*: called by 1 (execute).


##### `_reconnect_error`  (lines 191–192)

```
def _reconnect_error(error: composio.ComposioError, provider: str) -> composio.ComposioError
```

**Purpose**: Builds a Composio error message that includes clear reconnect guidance for a stale grant. It keeps the original status and body, but adds the instruction the agent should pass along.

**Data flow**: It receives the original Composio error and provider name. It asks the connector SDK for provider-specific stale-grant guidance, appends that guidance to the original error text, and returns a new ComposioError with the same status.

**Call relations**: ComposioBroker.execute calls this after _stale_account says the failure is about a missing connected account. The helper centralizes the wording so stale-grant failures are reported consistently.

*Call graph*: called by 1 (execute); 2 external calls (stale_grant_guidance, ComposioError).


##### `_discovered_tools`  (lines 195–216)

```
def _discovered_tools(rows: tuple[dict[str, object], ...]) -> tuple[BrokerTool, ...]
```

**Purpose**: Converts raw tool rows from Composio's catalog into UFO's standard BrokerTool objects. It filters out unusable rows and keeps discovery results compact and directly callable.

**Data flow**: It receives a tuple of dictionaries from Composio. For each row, it chooses the slug from the slug field or name field, skips rows without a valid slug, trims long descriptions, rewrites file-upload schemas into workspace-file form, reads the read-only marker, and returns all valid tools as a tuple.

**Call relations**: ComposioBroker.tools uses this for normal discovery, and ComposioBroker._slug_miss uses it when building better missing-slug errors. It calls _read_only for tag interpretation and the Composio schema helper for file-input rewriting, so callers receive BrokerTool objects instead of raw Composio rows.

*Call graph*: calls 1 internal fn (_read_only); called by 2 (_slug_miss, tools); 2 external calls (__init__, workspace_file_schema).


##### `_read_only`  (lines 219–221)

```
def _read_only(payload: dict[str, object]) -> bool
```

**Purpose**: Checks whether a Composio tool is marked as read-only. A read-only tool is one that should only fetch information and not change outside state.

**Data flow**: It receives one raw tool or schema dictionary. It looks at the tags field, verifies it is a list, and returns true only when the tag 'readOnlyHint' is present. Otherwise it returns false.

**Call relations**: ComposioBroker.schema and _discovered_tools both call this while building BrokerTool objects. It keeps the read-only rule in one small place so detailed schemas and discovery listings interpret Composio tags the same way.

*Call graph*: called by 2 (schema, _discovered_tools).


### `extensions/composio/ufo_ext_composio/client.py`

`io_transport` · `request handling and connector operations`

Composio acts like a switchboard for third-party services. Instead of this project writing and storing separate integrations for hundreds of providers, it asks Composio what tools exist, helps the user connect an account, and then asks Composio to run the chosen tool. This file is the client for that switchboard.

The central piece is `ComposioClient`. It talks to Composio's web API using HTTP requests. It can create an OAuth consent link, which is the familiar “sign in and allow access” flow. After the user finishes, it verifies that the returned connected account belongs to the expected workspace user, is active, and matches the requested provider. That matters because a wrong or inactive account would make later tool calls fail, or worse, use the wrong person's access.

The file also protects the rest of the system from messy outside responses. It turns bad HTTP answers or malformed data into clear `ComposioError` failures. It filters out providers that this project has judged unusable, pages through long tool lists, reshapes file-upload fields into a safe workspace-file format, and caches Composio Tool Router sessions so repeated searches do not reopen the same session again and again.

A key security idea is that Composio keeps the real provider tokens. This project stores only a connected-account id, like a claim ticket, not the secret itself.

#### Function details

##### `connectable`  (lines 110–134)

```
def connectable(slug: str, toolkit: Mapping[str, object]) -> bool
```

**Purpose**: Decides whether this deployment should offer a Composio toolkit to users. It checks that the toolkit is not on the local banned list, has Composio-managed login support, and actually contains tools.

**Data flow**: It receives a provider slug and a toolkit record from Composio. It reads the record's managed authentication schemes and tool count, compares the slug against the banned list, and returns `True` only when the provider is usable here.

**Call relations**: When the client checks a single provider or builds a catalog page, those flows ask `connectable` to make the final yes-or-no decision before showing or accepting that toolkit.

*Call graph*: called by 2 (connectable_toolkit, list_toolkits).


##### `ComposioError.__init__`  (lines 141–144)

```
def __init__(self, status: int, body: str) -> None
```

**Purpose**: Creates a clear error for a failed Composio call or a response that does not contain the expected information. It preserves both the status code and the response body so the caller can understand what went wrong.

**Data flow**: It receives a numeric status and a text body. It builds a readable message like `composio 502: ...`, stores the status and body on the exception, and raises nothing by itself until another function throws it.

**Call relations**: Higher-level client methods use this error when Composio rejects a request, omits an important field, or returns data in an unsafe shape. `_body` also uses it as the common error for bad HTTP responses.

*Call graph*: called by 6 (_account, _auth_config, connect_link, create_upload, tool_router_session, _body).


##### `ComposioClient.connect_link`  (lines 161–170)

```
async def connect_link(self, toolkit: str, user_id: str, callback_url: str) -> str
```

**Purpose**: Creates the web link a user opens to connect an outside account through Composio. This is the start of the consent handoff.

**Data flow**: It receives a toolkit name, a broker user id, and a callback URL. It first finds or creates an authentication configuration, posts those details to Composio, then returns the redirect URL Composio provides. If the redirect URL is missing, it raises a clear error instead of returning a broken link.

**Call relations**: This method is used when the system needs to start account connection. It relies on `_auth_config` to choose the login setup and `_post` to make the API request.

*Call graph*: calls 3 internal fn (_auth_config, _post, __init__).


##### `ComposioClient.connected_account`  (lines 172–176)

```
async def connected_account(self, account_id: str, expected_user_id: str, expected_toolkit: str) -> OAuthAccount
```

**Purpose**: Confirms that a connected account id is safe and usable, then wraps it in the project's `OAuthAccount` record. This keeps the grant as an account reference, not as a secret token.

**Data flow**: It receives an account id plus the expected user and toolkit. It asks `_account` to validate ownership, status, and provider match. If validation succeeds, it returns an `OAuthAccount` containing the account id.

**Call relations**: This is the public validation step after a user has completed connection. It delegates the detailed checks to `_account` and hands a clean account object back to the connector layer.

*Call graph*: calls 1 internal fn (_account); 1 external calls (__init__).


##### `ComposioClient._account`  (lines 178–207)

```
async def _account(self, account_id: str, expected_user_id: str, expected_toolkit: str) -> dict[str, object]
```

**Purpose**: Looks up a Composio connected account and proves it is the right one before the project uses it. It protects against inactive grants, wrong users, and accounts connected for a different toolkit.

**Data flow**: It receives an account id, expected user id, and expected toolkit. It fetches the account from Composio, compares the owner, checks that the status is `ACTIVE`, and confirms the toolkit slug. It returns the raw account payload when everything matches, or raises an error when it does not.

**Call relations**: `connected_account` calls this as its safety check. It uses `_get` to read Composio and raises either `ComposioError` for mismatches or `GrantUnusable` when the user must reconnect an inactive grant.

*Call graph*: calls 3 internal fn (__init__, _get, __init__); called by 1 (connected_account).


##### `ComposioClient.account_label`  (lines 209–212)

```
async def account_label(self, account_id: str) -> str | None
```

**Purpose**: Fetches a friendly label for a connected account, if Composio has one. This can help show users which account they connected.

**Data flow**: It receives an account id, fetches the account record, reads the `alias` field, and returns that alias only if it is a non-empty string. Otherwise it returns `None`.

**Call relations**: This is a small read-only helper over `_get`. Other parts of the connector flow can call it when they need display text rather than validation.

*Call graph*: calls 1 internal fn (_get).


##### `ComposioClient.list_tools`  (lines 214–244)

```
async def list_tools(self, toolkit: str, query: str='') -> tuple[dict[str, object], ...]
```

**Purpose**: Lists tools available inside one Composio toolkit, optionally filtered by a search query. It follows Composio's pages so tools beyond the first page are not missed.

**Data flow**: It receives a toolkit slug and optional query. It repeatedly requests pages of tool rows, keeps only dictionary-shaped tool records, stops at the end or at the configured maximum, and returns the collected rows as a tuple.

**Call relations**: The broker uses this when it needs to recover from a missing tool slug or inspect available tools. Internally it relies on `_get` for each page of Composio's `/tools` listing.

*Call graph*: calls 1 internal fn (_get); called by 1 (_slug_miss).


##### `ComposioClient.tool_schema`  (lines 246–247)

```
async def tool_schema(self, slug: str) -> dict[str, object]
```

**Purpose**: Fetches the full schema for one Composio tool. A schema describes what inputs the tool expects and what the tool is for.

**Data flow**: It receives a tool slug, requests that tool's record from Composio, and returns the response dictionary.

**Call relations**: This is a direct catalog lookup built on `_get`. It is used when another part of the system already knows the tool slug and needs its details.

*Call graph*: calls 1 internal fn (_get).


##### `ComposioClient.connectable_toolkit`  (lines 249–266)

```
async def connectable_toolkit(self, slug: str) -> str | None
```

**Purpose**: Checks whether a provider slug is valid, exists in Composio, and is safe for this deployment to offer. It returns the user-facing provider name when the provider can be connected.

**Data flow**: It receives a slug. It first rejects strings with unsafe characters, then fetches the toolkit from Composio, treats a not-found response as unavailable, applies `connectable`, and returns the toolkit name or slug. If the toolkit is not usable, it returns `None`.

**Call relations**: This is the single-provider version of catalog filtering. It calls `_get` to read Composio and `connectable` to apply the local availability rules.

*Call graph*: calls 2 internal fn (_get, connectable).


##### `ComposioClient.list_toolkits`  (lines 268–295)

```
async def list_toolkits(self, query: str, limit: int, after: str | None) -> CatalogPage
```

**Purpose**: Reads one page of providers that users may connect through Composio. It filters out catalog entries this deployment should not offer.

**Data flow**: It receives a search query, page limit, and optional cursor for the next page. It asks Composio for toolkits, keeps only valid and connectable items, turns each into a `CatalogEntry`, and returns a `CatalogPage` with entries and a next cursor if one exists.

**Call relations**: This supports provider browsing or search. It uses `_get` for the remote catalog and `connectable` as the local gate before handing structured catalog results to the rest of the system.

*Call graph*: calls 2 internal fn (_get, connectable); 2 external calls (__init__, __init__).


##### `ComposioClient.execute_tool`  (lines 297–311)

```
async def execute_tool(self, slug: str, arguments: Mapping[str, object], user_id: str, connected_account_id: str | None=None, idempotency_key: str | None=None) -> dict[str, object]
```

**Purpose**: Runs a Composio tool on Composio's server side for a broker user and, optionally, a specific connected account. This is where an available tool becomes an actual action.

**Data flow**: It receives a tool slug, tool arguments, user id, optional connected account id, and optional idempotency key. It builds the request body, checks that the serialized request is not too large, adds the idempotency header when provided, posts to Composio, and returns Composio's result dictionary.

**Call relations**: Connector execution flows call this when they are ready to run a tool. It hands the network work to `_post`; Composio uses its stored account token, so this project does not send one.

*Call graph*: calls 1 internal fn (_post); 1 external calls (dumps).


##### `ComposioClient.create_upload`  (lines 313–337)

```
async def create_upload(self, toolkit: str, slug: str, filename: str, mimetype: str, md5: str) -> 'ComposioUpload'
```

**Purpose**: Asks Composio for a place to stage a file before a tool uses it. This lets the sandbox upload bytes directly to Composio's storage without the client carrying the file contents.

**Data flow**: It receives toolkit and tool slugs, filename, MIME type, and MD5 checksum. It posts an upload request to Composio, checks for a storage key, reads an optional presigned upload URL, and returns a `ComposioUpload`. If Composio says the file already exists, the returned upload has no URL because no new upload is needed.

**Call relations**: Tool execution that needs file inputs uses this before running the tool. It relies on `_post` for the API call and raises `ComposioError` if Composio's upload response is missing required fields.

*Call graph*: calls 2 internal fn (_post, __init__); 1 external calls (__init__).


##### `ComposioClient.tool_router_session`  (lines 339–350)

```
async def tool_router_session(self, user_id: str, toolkits: list[str]) -> ToolRouterSession
```

**Purpose**: Opens a Composio Tool Router session for semantic tool search. The Tool Router is Composio's search helper that can suggest relevant tools and plans for a use case.

**Data flow**: It receives a user id and a list of toolkit slugs. It posts a session request, extracts the session id and MCP URL, and returns a `ToolRouterSession`. If either required value is missing, it raises a clear error.

**Call relations**: `search_connector_tools` calls this when no cached search session exists for a user and connector. The returned URL is then used for the actual search call.

*Call graph*: calls 2 internal fn (_post, __init__); called by 1 (search_connector_tools); 1 external calls (__init__).


##### `ComposioClient._auth_config`  (lines 352–370)

```
async def _auth_config(self, toolkit: str) -> str
```

**Purpose**: Finds the authentication configuration used for a user's consent flow, creating a Composio-managed one if the project has none. This ensures account connection can proceed without requiring every provider to be preconfigured by hand.

**Data flow**: It receives a toolkit slug. It asks Composio for an existing auth config, extracts the first id if present, and returns it. If none exists, it posts a request to create a managed auth config, verifies that the response contains an id, and returns that id.

**Call relations**: `connect_link` calls this before creating a connection link. It uses `_get`, `_post`, and `_auth_config_id` to turn either an existing or new Composio record into the single id the link request needs.

*Call graph*: calls 4 internal fn (_get, _post, __init__, _auth_config_id); called by 1 (connect_link).


##### `ComposioClient._get`  (lines 372–374)

```
async def _get(self, path: str, params: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Performs a GET request to Composio and turns the HTTP response into a dictionary. It is the shared read path for the client.

**Data flow**: It receives an API path and optional query parameters. It opens an HTTP client, sends the GET request, passes the response to `_body`, and returns the parsed response dictionary.

**Call relations**: Most read methods use this instead of opening HTTP connections themselves. It gets its configured HTTP client from `_http` and uses `_body` for consistent error and JSON handling.

*Call graph*: calls 2 internal fn (_http, _body); called by 7 (_account, _auth_config, account_label, connectable_toolkit, list_toolkits, list_tools, tool_schema).


##### `ComposioClient._post`  (lines 376–380)

```
async def _post(self, path: str, body: dict[str, object], headers: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Performs a POST request to Composio and turns the HTTP response into a dictionary. It is the shared write-or-action path for the client.

**Data flow**: It receives an API path, a JSON body, and optional headers. It opens an HTTP client, sends the POST request, passes the response to `_body`, and returns the parsed response dictionary.

**Call relations**: Connection links, tool execution, uploads, auth config creation, and Tool Router sessions all use this helper. Like `_get`, it centralizes HTTP setup through `_http` and response parsing through `_body`.

*Call graph*: calls 2 internal fn (_http, _body); called by 5 (_auth_config, connect_link, create_upload, execute_tool, tool_router_session).


##### `ComposioClient._http`  (lines 382–388)

```
def _http(self) -> httpx.AsyncClient
```

**Purpose**: Builds the configured asynchronous HTTP client used to talk to Composio. An asynchronous client lets the program wait for network replies without blocking other work.

**Data flow**: It reads the client's API key and optional test transport, then creates an `httpx.AsyncClient` with Composio's base URL, API key header, timeout, and transport override.

**Call relations**: `_get` and `_post` call this for every request. That keeps network configuration in one place and allows tests to swap in a mock transport.

*Call graph*: called by 2 (_get, _post); 1 external calls (AsyncClient).


##### `_body`  (lines 391–399)

```
def _body(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Checks and parses a Composio HTTP response. It turns bad status codes or unexpected response shapes into clear exceptions.

**Data flow**: It receives an HTTP response. If the status code shows failure, it raises `ComposioError`. If the body is empty, it returns an empty dictionary. Otherwise it parses JSON and returns it only if it is an object-like dictionary.

**Call relations**: `_get` and `_post` pass every Composio response through this helper. This means higher-level methods can work with dictionaries and do not each need their own HTTP error parsing.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_get, _post); 1 external calls (json).


##### `workspace_file_schema`  (lines 402–428)

```
def workspace_file_schema(value: object) -> object
```

**Purpose**: Rewrites Composio file-upload input schemas into the simpler file format this project wants the agent to use. Instead of asking the agent for storage keys, it asks for a `/workspace` file path.

**Data flow**: It receives any schema value. If it finds a dictionary marked as file-uploadable, it replaces that part with an object containing `workspace_file`. If it sees nested dictionaries or lists, it rewrites them recursively. Other values pass through unchanged.

**Call relations**: `_search_result` uses this while preparing tools discovered by semantic search. The result is a safer schema for the model: the broker builds Composio's real file reference later.

*Call graph*: called by 1 (_search_result).


##### `_auth_config_id`  (lines 431–438)

```
def _auth_config_id(payload: dict[str, object]) -> str | None
```

**Purpose**: Extracts the first authentication configuration id from a Composio list response. It is a small helper for the connect-link setup path.

**Data flow**: It receives a response dictionary. It looks for an `items` list, scans for the first dictionary item with a string `id`, and returns that id. If no usable id exists, it returns `None`.

**Call relations**: `_auth_config` calls this after asking Composio for existing auth configs. A found id lets `_auth_config` reuse the existing setup instead of creating a new one.

*Call graph*: called by 1 (_auth_config).


##### `composio_client`  (lines 441–448)

```
def composio_client() -> ComposioClient
```

**Purpose**: Creates the default Composio client for this deployment using the API key stored in the environment. It fails loudly if the key is missing.

**Data flow**: It reads `COMPOSIO_API_KEY` from environment variables. If the value is present, it returns a `ComposioClient` with that key. If not, it raises a runtime error explaining that connector OAuth cannot be brokered.

**Call relations**: Other parts of the extension can call this when they need the real deployed client. It is the bridge from process configuration to the `ComposioClient` object.

*Call graph*: 1 external calls (__init__).


##### `_dict`  (lines 455–456)

```
def _dict(value: object) -> dict[str, object]
```

**Purpose**: Safely treats a value as a dictionary only when it really is one. This prevents messy remote data from causing type errors during search result parsing.

**Data flow**: It receives any value. If the value is a dictionary, it returns it. Otherwise it returns an empty dictionary.

**Call relations**: `_search_result` calls this repeatedly while walking Composio Tool Router results that may or may not contain the expected nested objects.

*Call graph*: called by 1 (_search_result).


##### `_str_tuple`  (lines 459–462)

```
def _str_tuple(value: object) -> tuple[str, ...]
```

**Purpose**: Safely extracts non-empty strings from a list and returns them as an immutable tuple. It ignores anything that is not a string.

**Data flow**: It receives any value. If the value is a list, it keeps only non-empty string items and returns them as a tuple. If not, it returns an empty tuple.

**Call relations**: `_search_result` uses this to read tool slugs, plan steps, guidance, and pitfalls from Tool Router output without trusting every field blindly.

*Call graph*: called by 1 (_search_result).


##### `_search_result`  (lines 465–499)

```
def _search_result(result: dict[str, object]) -> BrokerSearch
```

**Purpose**: Turns Composio Tool Router output into the project's standard search result format. It gathers suggested tools, their schemas, plan steps, guidance, and warnings.

**Data flow**: It receives a raw result dictionary from the Tool Router. It finds tool schemas and result items, deduplicates tool slugs, rewrites file-upload fields with `workspace_file_schema`, builds `BrokerTool` objects, collects plan and advice text, and returns a `BrokerSearch`.

**Call relations**: `search_connector_tools` calls this after the remote MCP search completes. The helper functions `_dict` and `_str_tuple` keep parsing safe while `workspace_file_schema` adapts schemas for this project.

*Call graph*: calls 3 internal fn (_dict, _str_tuple, workspace_file_schema); called by 1 (search_connector_tools); 2 external calls (__init__, __init__).


##### `search_connector_tools`  (lines 502–525)

```
async def search_connector_tools(client: ComposioClient, workspace_id: UUID, connector: str, query: str) -> BrokerSearch
```

**Purpose**: Searches for useful Composio tools inside one connector using Composio's semantic Tool Router. It returns not just matching tools, but also suggested plan steps and cautions.

**Data flow**: It receives a client, workspace id, connector slug, and natural-language query. It builds the Composio broker user id, reuses or creates a cached Tool Router session for that user and connector, calls the Tool Router search tool over MCP, then converts the raw result with `_search_result`.

**Call relations**: This is the high-level search flow for dynamic connector tools. It calls `tool_router_session` only when the cache has no session, uses `mcp_session.mcp_call_tool` for the search request, and hands the response to `_search_result` for project-friendly output.

*Call graph*: calls 2 internal fn (tool_router_session, _search_result); 1 external calls (mcp_call_tool).


### `extensions/composio/ufo_ext_composio/mcp_session.py`

`io_transport` · `request handling`

This file is a small bridge between this project and Composio's Tool Router. Composio exposes tool search through MCP, the Model Context Protocol, which is a standard way for an app to talk to external tools. Here, the code opens a temporary HTTP-based MCP session, calls one named tool with the given arguments, then closes the session.

The important job is not only making the network call, but also normalizing the answer. MCP tool results can come back in a few different forms: already parsed data, structured content, or text blocks that contain JSON. The rest of the project should not have to care about those details. This file checks those possibilities in order and always returns a simple dictionary.

An everyday analogy: it is like asking a clerk a question and accepting the answer whether they hand you a filled form, a typed summary, or a note that needs to be read and copied into a form. Without this file, callers would need to know how to open MCP sessions, how to pass headers and timeouts, and how to decode several possible response formats. The module keeps that protocol-specific work in one place.

#### Function details

##### `mcp_call_tool`  (lines 18–42)

```
async def mcp_call_tool(endpoint: str, tool: str, arguments: dict[str, Any], headers: dict[str, str], timeout_seconds: float) -> dict[str, object]
```

**Purpose**: Calls one tool on a Composio MCP endpoint and returns the result as a plain dictionary. It is used when the project needs a clean, predictable answer from a Tool Router search call without exposing the rest of the code to MCP response details.

**Data flow**: It receives an endpoint URL, a tool name, tool arguments, HTTP headers, and a timeout. It opens a streamable-HTTP MCP client session, sends the tool call, then closes the session. After the response comes back, it first uses parsed dictionary data if available, then structured dictionary content, then tries to read the first text response as JSON. If none of those produce a dictionary, it wraps the remaining value in a dictionary so the caller always gets the same basic shape back.

**Call relations**: This function is the file's one public action. In its flow, it creates a FastMCP client using a streamable HTTP transport, asks that client to call the named tool, and uses JSON parsing only if the tool's answer arrived as text. Tests can replace the module-level FastMCP client with a stub, so this behavior can be checked without contacting a live Composio endpoint.

*Call graph*: 3 external calls (Client, StreamableHttpTransport, loads).


### `extensions/composio/ufo_ext_composio/proxy.py`

`io_transport` · `request handling and teardown`

Some connected services, such as Google or other SaaS providers, require credentials. In this setup, Composio keeps those credentials hidden and injects them on the server side. This file is the adapter that makes that invisible to the rest of the code. A connector can still make a normal HTTP request to the provider, but this transport quietly repackages it as a POST to Composio's proxy-execute endpoint, including the target URL, method, selected headers, body, and connected account id.

Think of it like sending a sealed instruction card to a trusted courier: the connector says where to go and what to ask for, while Composio adds the private key that the connector is not allowed to hold. When Composio replies, this file rebuilds an httpx response with the provider's status, headers, and body so features like pagination based on response headers still work.

It also draws an important line between two kinds of failure. If the provider returns an error, that is passed back as the provider's response. But if Composio itself fails to reach or run the proxy request, this file raises a proxy transport error, so higher-level retry logic can try again. For large non-JSON bodies, Composio may store the bytes elsewhere and return a download URL; this transport exposes that as a redirect rather than pulling big files through the event loop.

#### Function details

##### `ComposioProxyTransport.handle_async_request`  (lines 70–108)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: This is the main bridge from a normal provider request to a Composio proxy-execute request. It lets connector code behave as if it is calling the provider directly, while Composio supplies the hidden credential.

**Data flow**: It receives an httpx request meant for the provider. It reads the request body, builds a JSON payload containing the connected account id, target URL, HTTP method, safe forwarded headers, and body, then sends that payload to Composio's proxy endpoint using the inner transport. If Composio itself returns an error, it turns the response body into a short readable message and raises a proxy error. Otherwise, it decodes Composio's JSON reply and returns a reconstructed provider-style response.

**Call relations**: This method is called by httpx when a connector sends a request through this custom transport. During the flow it creates a new httpx request for Composio, asks the inner transport to send it, uses _proxy_fault when the Composio hop fails, and hands successful proxy payloads to ComposioProxyTransport._provider_response so the rest of the connector receives a normal-looking response.

*Call graph*: calls 2 internal fn (_provider_response, _proxy_fault); 4 external calls (ProxyError, Request, aread, loads).


##### `ComposioProxyTransport._provider_response`  (lines 110–153)

```
def _provider_response(self, payload: dict[str, Any], request: httpx.Request) -> httpx.Response
```

**Purpose**: This function turns Composio's proxy-execute payload back into an ordinary HTTP response from the provider's point of view. It exists so callers can keep using normal response status codes, headers, and bodies even though the request actually traveled through Composio.

**Data flow**: It receives Composio's decoded JSON payload and the original provider request. It peels away nested data envelopes, reads the provider status and headers, removes body-specific headers that would no longer be trustworthy, and builds the response content. If Composio reports binary data stored elsewhere, it returns a redirect response pointing to the provided download URL. If the binary URL is missing, it raises a Composio-specific error because the proxy reply is incomplete.

**Call relations**: ComposioProxyTransport.handle_async_request calls this after Composio successfully answers the proxy request. This function is the final conversion step: it takes Composio's format and hands back an httpx response that downstream connector code can treat like the provider's own answer.

*Call graph*: called by 1 (handle_async_request); 4 external calls (Response, dumps, cast, ComposioError).


##### `ComposioProxyTransport.aclose`  (lines 155–156)

```
async def aclose(self) -> None
```

**Purpose**: This function closes the underlying transport when the proxy transport is no longer needed. It is the cleanup step that releases network resources held by the inner HTTP layer.

**Data flow**: It takes no new data from the caller beyond the transport object itself. It forwards the close request to the inner transport, which does the actual shutdown work. Nothing is returned.

**Call relations**: This is called during client or transport teardown. It does not transform requests or responses; it simply passes cleanup through to the wrapped transport so the proxy layer does not leave open connections behind.


##### `_proxy_fault`  (lines 159–167)

```
def _proxy_fault(content: bytes) -> str
```

**Purpose**: This helper turns a failed Composio proxy response body into a short human-readable error message. It keeps raised proxy errors useful without dumping a large or messy response body.

**Data flow**: It receives raw response bytes from Composio. It decodes them as text, tries to parse JSON, and if the usual error message field is present, uses that message. If parsing fails or no structured message exists, it falls back to the decoded text. In all cases, it trims the result to a fixed maximum length.

**Call relations**: ComposioProxyTransport.handle_async_request calls this only when Composio's own proxy-execute endpoint returns an error status. The resulting text becomes part of the proxy error that higher-level retry and logging code will see.

*Call graph*: called by 1 (handle_async_request); 1 external calls (loads).


### Pipedream execution path
These files package the Pipedream extension, broker action execution, talk to Pipedream Connect, and proxy provider requests through Pipedream-held credentials.

### `extensions/pipedream/ufo_ext_pipedream/__init__.py`

`other` · `import time`

In Python projects, an `__init__.py` file tells Python, “this folder is a package you can import from.” This file does not contain code, settings, or functions. Its job is structural: it lets the rest of the system refer to `extensions.pipedream.ufo_ext_pipedream` as an importable module.

A simple analogy is a labeled folder in a filing cabinet. The folder may be empty at the front, but its label still matters because it tells people and tools where related papers belong. Without this file, depending on the Python version and import style, code that expects `ufo_ext_pipedream` to be a regular package could fail to find it or treat it differently.

Because the file is empty, it has no runtime decisions to make. It becomes relevant only when Python loads or searches for modules inside this package. The actual Pipedream extension behavior lives in other files under this package.


### `extensions/pipedream/ufo_ext_pipedream/broker.py`

`domain_logic` · `request handling`

Pipedream offers many ready-made actions for apps such as Gmail, Slack, and others. This file turns those actions into the common “broker” shape that UFO expects, so the agent does not need to know Pipedream’s raw API details. Think of it like a travel adapter: UFO speaks one connector language, Pipedream speaks another, and this file makes the plugs fit safely.

The main class, PipedreamBroker, is deliberately stateless. Each method asks for a fresh Pipedream client when it runs, so tests and temporary transport settings are respected and old connections are not accidentally reused. For discovery, it lists actions for a provider and converts them into BrokerTool objects with a plain input schema. It hides Pipedream-only fields, especially the app account slot, because the broker fills that in itself using the granted account.

When executing an action, it first fetches the action definition, inserts the connected account into the right slot, checks that the account belongs to the expected app, and then calls Pipedream’s server-side run API. It turns common failure cases into useful guidance: unknown action names get close suggestions, and stale or missing account grants tell the user to reconnect. It also extracts files saved by actions through Pipedream’s file stash and exposes them as download URLs.

#### Function details

##### `PipedreamBroker.tools`  (lines 62–64)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Finds available Pipedream actions for one provider and turns them into UFO-readable tools. Someone uses this when the agent needs to know what actions an app can perform.

**Data flow**: It receives a workspace id, a provider name, and a search query. It looks up the Pipedream app for that provider, asks the Pipedream client for matching actions, converts the raw action rows into BrokerTool objects, and returns them as a tuple.

**Call relations**: This is the discovery path for Pipedream actions. PipedreamBroker.search calls it when a broader search result is requested, and it relies on _spec to translate the provider name and _listed_tools to shape Pipedream’s response for UFO.

*Call graph*: calls 2 internal fn (_listed_tools, _spec); called by 1 (search); 1 external calls (pipedream_client).


##### `PipedreamBroker.schema`  (lines 66–73)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Builds the detailed input description for one Pipedream action. This tells the agent what arguments it may provide before trying to run the action.

**Data flow**: It receives a workspace id, provider name, and action slug. It fetches the action definition, extracts configurable fields, removes internal fields, converts the remaining fields into a JSON-style input schema, adds the description and read-only hint, and returns a BrokerTool.

**Call relations**: This is used when the system already knows the exact action and needs its calling instructions. It asks _definition for the raw action details, then uses _props, _input_schema, _read_only, and _str to turn those details into the broker’s standard tool shape.

*Call graph*: calls 5 internal fn (_definition, _input_schema, _props, _read_only, _str); 1 external calls (__init__).


##### `PipedreamBroker.execute`  (lines 75–110)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Runs a chosen Pipedream action using a specific connected account. It is the main path from an agent’s tool call to a real action happening on Pipedream’s servers.

**Data flow**: It receives the workspace, provider, action slug, user-supplied arguments, account id, and an optional idempotency key. It fetches the action definition, adds the connected account into the hidden app slot, checks that the account belongs to the expected Pipedream app, calls Pipedream to run the action, watches for action-level errors, and returns the response dictionary if the run succeeds. If the action name is wrong, it returns a more helpful not-found error; if the account grant appears stale, it adds reconnect guidance.

**Call relations**: This is the broker’s execution center. It uses _definition to understand the action, _app_slot to know where to bind the account, _spec to verify the provider, _key_miss to improve unknown-action errors, and _stale_account plus _reconnect_error to turn certain account failures into user-facing reconnect instructions.

*Call graph*: calls 7 internal fn (_definition, _key_miss, _app_slot, _reconnect_error, _spec, _stale_account, __init__); 2 external calls (dumps, pipedream_client).


##### `PipedreamBroker.file_outputs`  (lines 112–130)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Finds files that a Pipedream action saved during its run and exposes them as downloadable broker files. This matters because actions may create attachments, reports, images, or other files that the sandbox needs to fetch afterward.

**Data flow**: It receives the action response dictionary. It looks inside the response exports for Pipedream’s file-stash upload list, skips malformed entries, takes each valid presigned download URL and local file path, derives a simple filename, and returns BrokerFile objects.

**Call relations**: This runs after an action response is available. It does not call back into Pipedream; it only reads the response and packages the file URLs so the rest of UFO can retrieve the bytes directly.

*Call graph*: 2 external calls (__init__, PurePosixPath).


##### `PipedreamBroker.stage_upload`  (lines 132–144)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Rejects staged uploads for Pipedream actions. Pipedream expects file inputs as URLs, not as files uploaded through this broker.

**Data flow**: It receives the usual upload details, such as workspace, provider, action slug, filename, MIME type, and checksum. Instead of creating an upload target, it raises an error explaining that the caller should share the workspace file and pass the resulting download URL.

**Call relations**: This protects callers from using the wrong upload method. Unlike brokers that prepare a temporary upload slot, this broker points users toward the existing share_file flow because that is what Pipedream actions can consume.


##### `PipedreamBroker.search`  (lines 146–147)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Searches for matching Pipedream tools and wraps the results in the broker’s standard search result object. Pipedream does not provide extra routing guidance here, so the result is just the tools.

**Data flow**: It receives a workspace id, provider name, and query. It calls PipedreamBroker.tools to get matching actions, wraps those actions in a BrokerSearch object, and returns it.

**Call relations**: This is a thin wrapper around tools. When the rest of the connector system asks for a catalog search, this method delegates the real listing work to PipedreamBroker.tools and packages the answer in the expected search format.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `PipedreamBroker.credential`  (lines 149–170)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Creates a proxy credential that lets UFO make authenticated requests through Pipedream for a connected account. It also checks that the account really belongs to the requested provider.

**Data flow**: It receives a workspace id, provider name, and account id. It looks up the provider’s Pipedream app, fetches the connected account from Pipedream, turns a missing account into reconnect guidance, rejects accounts connected to the wrong app, and returns a Credential containing a PipedreamProxyTransport.

**Call relations**: This is used when code needs an authenticated transport rather than a one-off action run. It relies on _spec for the expected app, the Pipedream client for account lookup, and PipedreamProxyTransport to build the request path that will attach the account identity.

*Call graph*: calls 3 internal fn (__init__, _spec, __init__); 5 external calls (__init__, __init__, AsyncHTTPTransport, stale_grant_guidance, pipedream_client).


##### `PipedreamBroker._definition`  (lines 172–180)

```
async def _definition(self, slug: str) -> dict[str, object]
```

**Purpose**: Fetches the full definition for one Pipedream action. The definition is the source of truth for inputs, descriptions, and the account-binding slot.

**Data flow**: It receives an action slug. It asks the current Pipedream client for that action’s definition, converts a 404 not-found response into UnknownBrokerTool, and returns the definition data dictionary when present, otherwise the whole payload.

**Call relations**: PipedreamBroker.schema uses this to describe an action, and PipedreamBroker.execute uses it before running an action. It is the shared lookup step that keeps those two paths working from the same Pipedream definition.

*Call graph*: called by 2 (execute, schema); 2 external calls (__init__, pipedream_client).


##### `PipedreamBroker._key_miss`  (lines 182–201)

```
async def _key_miss(self, client: pipedream.PipedreamClient, provider: str, slug: str) -> PipedreamError
```

**Purpose**: Builds a helpful error when an action slug is unknown during execution. Instead of only saying “not found,” it tries to suggest nearby real action keys.

**Data flow**: It receives a Pipedream client, provider name, and missing slug. It looks up the provider’s app, tries to list all actions for that app, compares the missing slug with real slugs, and returns a PipedreamError that either includes close matches or tells the caller to search the app’s actions.

**Call relations**: PipedreamBroker.execute calls this after _definition reports that an action is unknown. It uses _spec and _listed_tools to get candidate action names, then get_close_matches to make the next model attempt less like a blind guess.

*Call graph*: calls 4 internal fn (_listed_tools, _spec, list_actions, __init__); called by 1 (execute); 1 external calls (get_close_matches).


##### `_stale_account`  (lines 204–211)

```
def _stale_account(error: PipedreamError, account_id: str) -> bool
```

**Purpose**: Checks whether a Pipedream error looks like it came from an old or missing connected account grant. This helps the system tell the user to reconnect only when that is likely the real fix.

**Data flow**: It receives a PipedreamError and an account id. It lowercases the error body and looks for narrow signs such as “external user not found” or the specific account id appearing with “not found,” then returns true or false.

**Call relations**: PipedreamBroker.execute uses this when a run or action-level error occurs. If this function says the account looks stale, execute passes the error through _reconnect_error; otherwise it leaves the original error alone.

*Call graph*: called by 1 (execute).


##### `_reconnect_error`  (lines 214–215)

```
def _reconnect_error(error: PipedreamError, provider: str) -> PipedreamError
```

**Purpose**: Adds clear reconnect instructions to a Pipedream error. It keeps the original error status but makes the message more useful for the agent or user.

**Data flow**: It receives a PipedreamError and provider name. It combines the original error body with provider-specific stale-grant guidance and returns a new PipedreamError with that expanded message.

**Call relations**: PipedreamBroker.execute calls this only after _stale_account identifies an account-related failure. It is the final step that turns a low-level Pipedream failure into actionable guidance.

*Call graph*: calls 1 internal fn (__init__); called by 1 (execute); 1 external calls (stale_grant_guidance).


##### `_spec`  (lines 218–222)

```
def _spec(provider: str) -> ConnectorSpec
```

**Purpose**: Looks up UFO’s registered Pipedream connector information for a provider name. This is how the broker knows which Pipedream app belongs to a provider.

**Data flow**: It receives a provider string. It searches the registered Pipedream connector map, returns the matching ConnectorSpec if found, and raises a KeyError if the provider is not registered.

**Call relations**: Several broker paths depend on this translation: tools uses it before listing actions, execute and credential use it to verify app identity, and _key_miss uses it to search the right action catalog.

*Call graph*: called by 4 (_key_miss, credential, execute, tools).


##### `_listed_tools`  (lines 225–242)

```
def _listed_tools(rows: tuple[dict[str, object], ...]) -> tuple[BrokerTool, ...]
```

**Purpose**: Converts raw Pipedream action listings into UFO BrokerTool objects. This makes catalog results immediately useful to the agent.

**Data flow**: It receives raw action rows from Pipedream. For each row with a valid action key, it extracts the description, configurable fields, read-only hint, and input schema, then returns a tuple of BrokerTool objects.

**Call relations**: PipedreamBroker.tools uses this for normal action discovery. PipedreamBroker._key_miss also uses it when building suggestions for a mistyped action slug.

*Call graph*: calls 4 internal fn (_input_schema, _props, _read_only, _str); called by 2 (_key_miss, tools); 1 external calls (__init__).


##### `_read_only`  (lines 245–247)

```
def _read_only(definition: dict[str, object]) -> bool
```

**Purpose**: Reads whether an action claims to be read-only. A read-only action is one that should only look at data, not change it.

**Data flow**: It receives an action definition dictionary. It checks the annotations section for a true readOnlyHint value and returns a boolean.

**Call relations**: PipedreamBroker.schema and _listed_tools call this while building BrokerTool objects. Its result helps downstream planning understand whether a tool is likely safe to call without making changes.

*Call graph*: called by 2 (schema, _listed_tools).


##### `_props`  (lines 250–252)

```
def _props(definition: dict[str, object]) -> list[dict[str, object]]
```

**Purpose**: Extracts the usable configurable property dictionaries from a Pipedream action definition. These properties describe the action’s possible inputs.

**Data flow**: It receives an action definition dictionary. It reads configurable_props, keeps only entries that are dictionaries, and returns them as a list; if the field is missing or not a list, it returns an empty list.

**Call relations**: PipedreamBroker.schema and _listed_tools use this before creating input schemas. _app_slot also uses it to find the hidden account-binding field needed during execution.

*Call graph*: called by 3 (schema, _app_slot, _listed_tools).


##### `_app_slot`  (lines 255–262)

```
def _app_slot(definition: dict[str, object], slug: str) -> str
```

**Purpose**: Finds the special Pipedream input field where the connected account must be inserted. Without this slot, the broker cannot run the action as the user’s granted account.

**Data flow**: It receives an action definition and slug. It scans the action’s configurable properties for the Pipedream app property type, returns that property’s name, and raises a PipedreamError if no valid slot exists.

**Call relations**: PipedreamBroker.execute calls this just before running an action. The returned field name is where execute places the account’s authProvisionId so Pipedream knows which connection to use.

*Call graph*: calls 2 internal fn (_props, __init__); called by 1 (execute).


##### `_input_schema`  (lines 265–288)

```
def _input_schema(props: list[dict[str, object]]) -> dict[str, object]
```

**Purpose**: Builds a simple JSON-style input schema from Pipedream configurable properties. This tells the agent which fields it may fill in and which are required.

**Data flow**: It receives a list of property dictionaries. It skips internal Pipedream fields, the connected-account slot, and service-only fields, converts known Pipedream types into JSON schema types, adds descriptions from labels or descriptions, tracks required fields, and returns an object schema.

**Call relations**: PipedreamBroker.schema uses this for one detailed action definition, and _listed_tools uses it for catalog listings. It calls _str to safely treat non-string type values as empty strings.

*Call graph*: calls 1 internal fn (_str); called by 2 (schema, _listed_tools).


##### `_str`  (lines 291–292)

```
def _str(value: object) -> str
```

**Purpose**: Safely turns a value into a string only when it already is one. This avoids accidentally showing Python representations of non-string data as user-facing text.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: PipedreamBroker.schema, _listed_tools, and _input_schema use this while reading optional text fields from Pipedream data. It is a small guardrail around data that may be missing or shaped unexpectedly.

*Call graph*: called by 3 (schema, _input_schema, _listed_tools).


### `extensions/pipedream/ufo_ext_pipedream/client.py`

`io_transport` · `connector OAuth, account lookup, and connector action execution`

This file solves a sensitive problem: how to let the system act on a user’s connected third-party accounts without becoming a bucket of secrets. Pipedream hosts the login and consent screen, stores or injects the real provider credentials, and exposes safe API endpoints this code can call.

The file has three main jobs. First, it defines the connector allowlist, such as GitHub, Gmail, Linear, Attio, and others. Each entry says what Pipedream app to use, what provider host it belongs to, and whether this deployment’s own OAuth app is needed. OAuth is the standard “let this app access my account” flow.

Second, `PipedreamClient` wraps Pipedream’s HTTP API. It gets a short-lived Pipedream access token, creates hosted connection links, checks whether an account belongs to the right user or workspace, lists available provider actions, and runs those actions server-side through Pipedream.

Third, it contains guardrails. A project-level Pipedream token can read many connected accounts, so this code repeatedly checks ownership before using an account or reading a provider token. Think of it like a hotel front desk: having a master key is not enough; the clerk still checks that the guest belongs to the room before handing over access.

#### Function details

##### `PipedreamError.__init__`  (lines 120–123)

```
def __init__(self, status: int, body: str) -> None
```

**Purpose**: Creates a clear exception when Pipedream returns an error or an answer this client cannot safely use. It keeps the original status code and response body so callers can report or debug the failure.

**Data flow**: It receives an HTTP-style status number and a text body. It builds a readable error message from them, stores both values on the exception, and returns an exception object ready to be raised.

**Call relations**: This is the common failure signal for this extension. The client raises it when token creation, account reads, action lookup, or response parsing fails, and the broker layer also raises it when a connector request cannot be satisfied safely.

*Call graph*: called by 13 (_key_miss, credential, execute, _app_slot, _reconnect_error, access_token, account_token, connect_token, newest_account, workspace_account (+3 more)).


##### `PipedreamClient.access_token`  (lines 161–182)

```
async def access_token(self) -> str
```

**Purpose**: Gets the deployment’s own Pipedream access token, which is needed before calling most Pipedream Connect endpoints. It reuses a cached token until it is close to expiring, avoiding unnecessary authentication calls.

**Data flow**: It starts with the client id and secret stored on the `PipedreamClient`. If a still-valid token is already cached, it returns that. Otherwise it posts the credentials to Pipedream, checks the response, stores the new token with its expiry time, and returns the token string.

**Call relations**: Lower-level request helpers call this before authenticated GET or POST requests. It uses `_http` to make the unauthenticated token request and `_body` to turn the HTTP response into a safe Python dictionary.

*Call graph*: calls 3 internal fn (_http, __init__, _body); called by 2 (_get, _post); 1 external calls (monotonic).


##### `PipedreamClient.connect_token`  (lines 184–199)

```
async def connect_token(self, external_user_id: str, success_redirect_uri: str, error_redirect_uri: str) -> ConnectToken
```

**Purpose**: Creates a short-lived Pipedream Connect token and hosted login link for a user. This is what lets the user open a browser page and approve access to an outside app.

**Data flow**: It receives an external user id plus success and error redirect URLs. It sends those to Pipedream, expects back both a token and a connect-link URL, and returns them as a `ConnectToken`. If either piece is missing, it raises an error instead of continuing with a broken login flow.

**Call relations**: This is used during the connector consent flow. It hands the actual HTTP work to `_post`, and uses `PipedreamError` when Pipedream’s answer is not usable.

*Call graph*: calls 2 internal fn (_post, __init__); 1 external calls (__init__).


##### `PipedreamClient.connected_account`  (lines 201–208)

```
async def connected_account(self, account_id: str, external_user_id: str) -> ConnectedAccount
```

**Purpose**: Reads one connected account and proves that it belongs to the expected external user. This prevents one user’s account id from being used to access another user’s connection.

**Data flow**: It receives a Pipedream account id and an expected external user id. It fetches the account record, unwraps the `data` field if needed, checks ownership and health through `_owned_account`, and returns a clean `ConnectedAccount` object.

**Call relations**: This is part of the safety check after a connection or before using a grant. It relies on `_get` for the API call, `_dict` for safe response shaping, and `_owned_account` for the ownership guard.

*Call graph*: calls 3 internal fn (_get, _dict, _owned_account).


##### `PipedreamClient.account_label`  (lines 210–214)

```
async def account_label(self, account_id: str) -> str | None
```

**Purpose**: Fetches a human-readable name for a connected account, if Pipedream has one. This is useful for showing users which account they connected.

**Data flow**: It receives an account id, asks Pipedream for that account, unwraps the record, and looks for a non-empty `name` field. It returns the name string when present, otherwise `None`.

**Call relations**: This is a small read-only helper used when the system needs display text rather than credentials or execution access. It uses `_get` for the API call and `_dict` to avoid crashing on unexpected response shapes.

*Call graph*: calls 2 internal fn (_get, _dict).


##### `PipedreamClient.workspace_account`  (lines 216–226)

```
async def workspace_account(self, account_id: str, workspace_id: UUID) -> ConnectedAccount
```

**Purpose**: Reads a connected account and confirms that it belongs to the requested workspace. A workspace is a project or tenant boundary, so this check stops cross-workspace access.

**Data flow**: It receives an account id and workspace id. It fetches the account, turns the raw record into a `ConnectedAccount`, checks whether the account’s external user id matches that workspace’s allowed pattern, and returns the account only if the check passes. If not, it raises a forbidden error.

**Call relations**: This is used before workspace-scoped connector execution. It builds on `_get`, `_dict`, `_account`, and `_workspace_owns_external_user`, with `PipedreamError` used when ownership does not match.

*Call graph*: calls 5 internal fn (_get, __init__, _account, _dict, _workspace_owns_external_user).


##### `PipedreamClient.account_token`  (lines 228–252)

```
async def account_token(self, account_id: str, workspace_id: UUID) -> str
```

**Purpose**: Reads the real provider access token for a connected account, but only after proving the account belongs to the workspace. This is used for special cases like command-line GitHub tools that need an actual token rather than a proxied API call.

**Data flow**: It receives an account id and workspace id. It fetches the account with credentials included, checks the account is healthy, checks workspace ownership, then extracts `oauth_access_token` from the credentials. It returns the token string or raises an error if the token is absent or the account is not allowed.

**Call relations**: This is one of the most sensitive paths in the file. It uses the same account and workspace guards as other reads before exposing a secret, relying on `_get`, `_account`, `_dict`, and `_workspace_owns_external_user`.

*Call graph*: calls 5 internal fn (_get, __init__, _account, _dict, _workspace_owns_external_user).


##### `PipedreamClient.newest_account`  (lines 254–268)

```
async def newest_account(self, external_user_id: str, app: str) -> ConnectedAccount
```

**Purpose**: Finds the most recently connected account for a specific external user and app. This helps match a just-finished browser consent flow to the account Pipedream created.

**Data flow**: It receives an external user id and Pipedream app slug. It asks Pipedream for matching accounts, keeps valid dictionary records, selects the one with the latest creation time, checks it has an id, verifies ownership, and returns a `ConnectedAccount`.

**Call relations**: This is used after consent completes, when the system needs to discover which account was just connected. It calls `_get` for the list, `_dict` to normalize records, and `_owned_account` to keep the result tied to the expected user.

*Call graph*: calls 4 internal fn (_get, __init__, _dict, _owned_account).


##### `PipedreamClient.list_actions`  (lines 270–299)

```
async def list_actions(self, app: str, query: str='') -> tuple[dict[str, object], ...]
```

**Purpose**: Lists the Pipedream actions available for an app, optionally filtered by a search query. Actions are prebuilt operations like sending a Discord message or searching Linear issues.

**Data flow**: It receives an app slug and optional query. It repeatedly asks Pipedream for pages of actions, adds valid action rows to a list, follows the next-page cursor when available, and stops at the end or at the configured maximum. It returns the collected rows as an immutable tuple.

**Call relations**: The broker uses this when it cannot immediately map a requested tool key and needs to search Pipedream’s catalog. It uses `_get` for each page and `_dict` to safely read pagination information.

*Call graph*: calls 2 internal fn (_get, _dict); called by 1 (_key_miss).


##### `PipedreamClient.action_definition`  (lines 301–302)

```
async def action_definition(self, key: str) -> dict[str, object]
```

**Purpose**: Fetches the full definition of one Pipedream action. The definition tells the rest of the system what inputs the action expects.

**Data flow**: It receives an action key, calls Pipedream’s component endpoint for that key, and returns the response dictionary. Any HTTP or shape errors are handled by the lower-level request path.

**Call relations**: This is called when the system needs details for one selected action rather than a search listing. It delegates the HTTP work entirely to `_get`.

*Call graph*: calls 1 internal fn (_get).


##### `PipedreamClient.run_action`  (lines 304–322)

```
async def run_action(self, key: str, external_user_id: str, configured_props: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs one Pipedream action on behalf of a connected user. The action executes on Pipedream’s side, with Pipedream binding the right account credentials.

**Data flow**: It receives an action key, an external user id, and configured input values. It builds a run request, adds a fresh file stash so output files can be retrieved later, checks that the JSON payload is not too large, posts it to Pipedream, and returns Pipedream’s response dictionary.

**Call relations**: This is the execution path after the broker has chosen an action and prepared its inputs. It uses `_post` for the API call and relies on Pipedream to run the provider operation server-side.

*Call graph*: calls 1 internal fn (_post); 1 external calls (dumps).


##### `PipedreamClient._get`  (lines 324–327)

```
async def _get(self, path: str, params: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Performs an authenticated GET request to Pipedream and returns a checked dictionary response. It centralizes the repeated steps needed for safe reads.

**Data flow**: It receives an API path and optional query parameters. It first gets a Pipedream access token, opens an HTTP client with that token, sends the GET request, then passes the response to `_body`. It returns the parsed response dictionary.

**Call relations**: Most read operations in this client go through this helper, including account lookup, action listing, and action definition fetches. It connects higher-level methods to `access_token`, `_http`, and `_body`.

*Call graph*: calls 3 internal fn (_http, access_token, _body); called by 7 (account_label, account_token, action_definition, connected_account, list_actions, newest_account, workspace_account).


##### `PipedreamClient._post`  (lines 329–332)

```
async def _post(self, path: str, body: dict[str, object]) -> dict[str, object]
```

**Purpose**: Performs an authenticated POST request to Pipedream and returns a checked dictionary response. It centralizes the repeated steps needed for safe writes or commands.

**Data flow**: It receives an API path and a request body. It gets an access token, opens an authenticated HTTP client, sends the body as JSON, parses and checks the response with `_body`, and returns the resulting dictionary.

**Call relations**: The connect-token creation and action execution paths use this helper. It sits between the high-level client methods and the lower-level HTTP setup and response parsing.

*Call graph*: calls 3 internal fn (_http, access_token, _body); called by 2 (connect_token, run_action).


##### `PipedreamClient._http`  (lines 334–347)

```
def _http(self, token: str | None=None) -> httpx.AsyncClient
```

**Purpose**: Creates an HTTP client configured for Pipedream. When given a token, it adds the authorization header and the selected Pipedream environment.

**Data flow**: It receives an optional token. If the token is present, it builds request headers for authenticated Connect calls; if not, it builds no headers for the OAuth token request. It returns an `httpx.AsyncClient` with the base URL, timeout, headers, and optional test transport.

**Call relations**: Every network call in `PipedreamClient` goes through this factory. `access_token` uses it without a token, while `_get` and `_post` use it with a token.

*Call graph*: called by 3 (_get, _post, access_token); 1 external calls (AsyncClient).


##### `_dict`  (lines 350–351)

```
def _dict(value: object) -> dict[str, object]
```

**Purpose**: Safely treats a value as a dictionary only if it really is one. This keeps unexpected response shapes from causing accidental attribute errors.

**Data flow**: It receives any value. If the value is a dictionary, it returns it unchanged; otherwise it returns an empty dictionary.

**Call relations**: Many account and listing helpers use this before reading fields from Pipedream responses. It is a small safety adapter used by higher-level validation code.

*Call graph*: called by 7 (account_label, account_token, connected_account, list_actions, newest_account, workspace_account, _account).


##### `_owned_account`  (lines 354–367)

```
def _owned_account(record: dict[str, object], account_id: str, external_user_id: str) -> ConnectedAccount
```

**Purpose**: Checks that a Pipedream account record belongs to one exact external user. This is a key protection against using someone else’s connected account by mistake or by attack.

**Data flow**: It receives a raw account record, the account id, and the expected external user id. It first turns the record into a clean `ConnectedAccount` through `_account`, then compares the recorded owner to the expected owner. It returns the account if they match or raises a forbidden error if they do not.

**Call relations**: `connected_account` and `newest_account` call this when they need user-level ownership, not just a valid account record. It relies on `_account` for health and shape checks, and raises `PipedreamError` for mismatched ownership.

*Call graph*: calls 2 internal fn (__init__, _account); called by 2 (connected_account, newest_account).


##### `_account`  (lines 370–393)

```
def _account(record: dict[str, object], account_id: str) -> ConnectedAccount
```

**Purpose**: Turns a raw Pipedream account record into the small account object this system uses, while refusing records that cannot safely authenticate. It treats an unhealthy grant as something the user must reconnect, not as a normal retryable service failure.

**Data flow**: It receives a raw account record and account id. It reads the external owner, rejects the record if the owner is missing, raises `GrantUnusable` if Pipedream says the account is unhealthy, extracts the app slug when present, and returns a `ConnectedAccount`.

**Call relations**: This is the shared account validation step under `_owned_account`, `workspace_account`, and `account_token`. It uses `_dict` for the nested app record, `PipedreamError` for malformed records, and `GrantUnusable` when the user’s connection needs repair.

*Call graph*: calls 3 internal fn (__init__, __init__, _dict); called by 3 (account_token, workspace_account, _owned_account); 1 external calls (__init__).


##### `workspace_user_prefix`  (lines 396–397)

```
def workspace_user_prefix(workspace_id: UUID) -> str
```

**Purpose**: Builds the standard text prefix used for Pipedream external user ids belonging to a workspace. The prefix is the anchor for later ownership checks.

**Data flow**: It receives a workspace UUID. It converts the UUID to its compact hexadecimal form and returns a string starting with `ufo_`, followed by that workspace id and an underscore.

**Call relations**: `connection_user_id` uses this when creating a new external user id for a connection flow, and `_workspace_owns_external_user` uses it when checking whether an existing external user id belongs to a workspace.

*Call graph*: called by 2 (_workspace_owns_external_user, connection_user_id).


##### `_workspace_owns_external_user`  (lines 400–409)

```
def _workspace_owns_external_user(workspace_id: UUID, external_user_id: str) -> bool
```

**Purpose**: Decides whether a Pipedream external user id belongs to a workspace. This lets the client enforce workspace boundaries before using an account or token.

**Data flow**: It receives a workspace id and an external user id. It accepts an older direct workspace form, otherwise checks that the id starts with the workspace prefix and ends with a 32-character lowercase hexadecimal connection id. It returns `true` only when the format proves the workspace relationship.

**Call relations**: `workspace_account` and `account_token` call this before allowing workspace-scoped access. It uses `workspace_user_prefix` so the creation and checking rules stay consistent.

*Call graph*: calls 1 internal fn (workspace_user_prefix); called by 2 (account_token, workspace_account).


##### `connection_user_id`  (lines 412–414)

```
def connection_user_id(workspace_id: UUID, state: str) -> str
```

**Purpose**: Creates a stable Pipedream external user id for one workspace connection flow. It hides the raw state value by hashing it before placing it in the id.

**Data flow**: It receives a workspace id and a state string. It hashes the state with SHA-256, takes the first 32 hexadecimal characters, attaches that to the workspace prefix, and returns the finished external user id.

**Call relations**: This is used when starting or correlating a connection flow. Its output is later recognized by `_workspace_owns_external_user`, which checks the same prefix and connection-id format.

*Call graph*: calls 1 internal fn (workspace_user_prefix); 1 external calls (sha256).


##### `_body`  (lines 417–425)

```
def _body(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Turns an HTTP response from Pipedream into a safe dictionary or raises a clear error. It prevents callers from accidentally treating error pages or odd JSON values as valid data.

**Data flow**: It receives an HTTP response. If the status code means failure, it raises `PipedreamError` with the status and text. If there is no content, it returns an empty dictionary. Otherwise it parses JSON and returns it only if it is a dictionary.

**Call relations**: All request paths use this after receiving a response: token minting, `_get`, and `_post`. It is the common checkpoint between raw network responses and the rest of the client logic.

*Call graph*: calls 1 internal fn (__init__); called by 3 (_get, _post, access_token); 1 external calls (json).


##### `pipedream_client`  (lines 428–446)

```
def pipedream_client() -> PipedreamClient
```

**Purpose**: Builds a `PipedreamClient` from environment variables. It fails early if the deployment is missing the Pipedream credentials or project id needed to broker OAuth.

**Data flow**: It reads the client id, client secret, project id, and optional environment name from process environment variables. If required values are missing, it raises a runtime error. Otherwise it returns a configured `PipedreamClient` instance.

**Call relations**: Other parts of the extension call this when they need the deployment’s real Pipedream client. It is the bridge between deployment configuration and the API wrapper defined in this file.

*Call graph*: 1 external calls (__init__).


### `extensions/pipedream/ufo_ext_pipedream/proxy.py`

`io_transport` · `request handling and teardown`

A connector usually wants to call a provider, such as Gmail or another API, as if it had a normal access token. With Pipedream, it does not get that token. Instead, Pipedream offers a proxy endpoint: the connector sends its intended provider request to Pipedream, and Pipedream adds the saved credential on the server side.

`PipedreamProxyTransport` is the bridge that makes this feel ordinary to the rest of the code. It plugs into `httpx`, the HTTP client library, as a custom transport. When the connector tries to make a request to the provider, this transport reads that request, wraps it into a new request to Pipedream’s proxy URL, and sends it onward using an inner transport.

The original provider URL is put into the proxy path after being base64-url encoded, which is a safe way to place a full URL inside another URL. The Pipedream account and external user are added as query values, so Pipedream knows which saved credential to inject.

Headers get special treatment. Pipedream only forwards headers that start with `x-pd-proxy-`, so useful provider headers are renamed with that prefix. Unsafe or transport-level headers, like `authorization`, `host`, and `content-length`, are deliberately left out. The provider’s response then comes back unchanged, including status code, body, and headers, so connector logic that depends on provider behavior still works.

#### Function details

##### `PipedreamProxyTransport.handle_async_request`  (lines 55–74)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: This is the main rewrite step. It takes an ordinary HTTP request meant for a provider and turns it into a request to Pipedream’s proxy, so Pipedream can add the real credential and return the provider’s response.

**Data flow**: It receives an `httpx.Request` aimed at the provider. It first asks the Pipedream client for an access token, reads the request body, and builds new headers for Pipedream, including the bearer token and environment. It copies allowed original headers, adding the `x-pd-proxy-` prefix when needed, and skips headers that should not be forwarded. It then encodes the original URL, builds the Pipedream proxy URL with the account and external user information, creates a new request to that proxy URL, and sends it through the inner transport. The result is the HTTP response returned by Pipedream, which is passed back to the caller.

**Call relations**: This method is called by `httpx` whenever an async client using this transport sends a request. During that request path, it uses the Pipedream client to get an access token, uses `httpx.URL` and `httpx.Request` to build the proxy request, and hands the finished request to the inner transport, which performs the actual network work.

*Call graph*: 4 external calls (urlsafe_b64encode, Request, aread, URL).


##### `PipedreamProxyTransport.aclose`  (lines 76–77)

```
async def aclose(self) -> None
```

**Purpose**: This closes the underlying HTTP transport when the proxy transport is no longer needed. It prevents network resources, such as open connections, from being left around.

**Data flow**: It takes no extra input beyond the transport object itself. It calls the inner transport’s async close method, letting that lower-level transport clean up its resources. It returns nothing.

**Call relations**: This is called during cleanup, usually when the surrounding `httpx.AsyncClient` is being closed. It does not do its own cleanup work directly; it passes the shutdown request to the inner transport that actually owns the network connections.


### Slack connector hooks
This file adds Slack-specific behavior around connector execution and connection completion events.

### `extensions/slack/ufo_ext_slack/hooks.py`

`domain_logic` · `event handling`

This file is the Slack extension’s event listener. It solves two small but important user-facing problems.

First, when the system sends a Slack message through the generic connector tool, that tool does not know which Slack bot user belongs to this workspace. Without help, it can only add generic attribution. This file looks up the Slack bot user ID that the Slack surface previously saved, then rewrites the outgoing message so the footer mentions the actual bot. It does this gently: if the lookup is slow, broken, missing, or invalid, it gives up and lets the normal generic footer happen. That matters because this hook runs before a tool is allowed to execute; a failure here must not accidentally block a real Slack send just because a cosmetic footer could not be added.

Second, after a user finishes connecting an external account, Slack may still show a button inviting them to connect that same account. This file finds the stored Slack message for that connection prompt, updates it to show the completed account instead of the stale button, and then removes the stored reminder. In everyday terms, it is like replacing a “Please sign here” sticky note with “Signed by Alice” once the paperwork is complete.

#### Function details

##### `attribute_connector_send`  (lines 38–52)

```
async def attribute_connector_send(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook runs before a tool call and checks whether the call is a Slack message being sent through the connector tool. If it is, and the workspace has a known Slack bot user ID, it rewrites the message arguments so the footer mentions that bot.

**Data flow**: It receives a hook context containing the pending tool call. If the tool call is a Slack send, it asks `_mirrored_self_user_id` for the saved bot user ID. When a valid ID comes back, it passes the original message arguments and that ID to `mention_attributed`, then returns a `ModifyInput` result containing a copied tool input with the updated arguments. If the call is not a Slack send, or no bot ID is available, it returns nothing and leaves the tool call unchanged.

**Call relations**: This is called by the hook system before the external connector tool runs. It uses `is_slack_send` to decide whether the pending connector call is relevant, calls `_mirrored_self_user_id` to safely read the Slack bot identity, and hands the actual message rewriting to `mention_attributed`. When it returns `ModifyInput`, the larger tool flow continues with the edited input instead of the original.

*Call graph*: calls 1 internal fn (_mirrored_self_user_id); 3 external calls (__init__, is_slack_send, mention_attributed).


##### `_mirrored_self_user_id`  (lines 55–69)

```
async def _mirrored_self_user_id(ctx: HookContext) -> str | None
```

**Purpose**: This helper safely reads the Slack bot user ID that was previously saved for this workspace. It is deliberately cautious: if the read fails, takes too long, or returns something that does not look like a Slack bot user ID, it returns `None` instead of causing the outgoing message to be denied.

**Data flow**: It receives the hook context and uses the extension’s scoped store to read the saved value under `SELF_USER_ID_STORE_KEY`. The read is wrapped in a one-second timeout, so it cannot stall the pre-tool hook for too long. If an error happens, it logs the error type and returns `None`. If the saved value is a string matching the expected Slack bot user ID pattern, it returns that string; otherwise it returns `None`.

**Call relations**: This helper is used by `attribute_connector_send` whenever a connector Slack send might need a bot-mention footer. It calls `asyncio.timeout` to enforce its own time limit, uses `re.match` to validate the stored ID, and logs failures through the project’s observability logger. Its main job is to protect the surrounding hook from turning a cosmetic lookup problem into a blocked Slack message.

*Call graph*: called by 1 (attribute_connector_send); 3 external calls (timeout, match, log).


##### `settle_connect_button`  (lines 72–101)

```
async def settle_connect_button(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook runs after the system records that a user successfully connected an external account. It updates the Slack message that originally showed the connect button, replacing the stale prompt with information about the account that is now connected.

**Data flow**: It receives a hook context whose payload should describe a recorded connection, including the provider, account ID, optional account label, and owning member. It builds the store key for that member and provider, reads the held Slack connect message, and exits quietly if none exists. If a held message is found, it gets the Slack bot token, validates the stored message data as a `ConnectMessage`, calls `settle_connect_message` to update Slack, and then deletes the stored message key. If the hook is invoked with the wrong kind of payload, it raises an error because that would mean the hook was wired incorrectly.

**Call relations**: This is called by the hook system after a connection has been recorded, so the account connection itself is already complete before Slack is updated. It uses `connect_message_key` to find the stored Slack prompt, `ConnectMessage.model_validate` to turn stored data back into the expected message shape, and `settle_connect_message` to perform the Slack-side update. Its work is cleanup and user feedback: the finished connection remains valid even if the Slack message update fails.

*Call graph*: 3 external calls (model_validate, connect_message_key, settle_connect_message).
