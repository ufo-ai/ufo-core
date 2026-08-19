# External connector discovery, credentials, and action execution  `stage-13`

This stage is shared behind-the-scenes support for letting the agent use outside services without directly handling private passwords or tokens. It is like a controlled service desk: the agent asks what tools are available, requests an action, and receives a cleaned-up result, while the sensitive account access stays behind a safe boundary.

The brokered and keyed connector backends connect to tool providers such as Composio, Pipedream, MCP servers, and simple API-key services. They discover available actions, choose allowed tools, proxy requests, run actions, and provide fake connectors for tests. The native integration parts do the same for familiar services: Slack message setup and search, iMessage through Spectrum, and GitHub credentials for coding work.

core/src/ufo/connectors.py defines the safety rules for connected accounts, making sure secrets do not leak into the agent, sandbox, logs, or unrelated code. extensions/connectors/ufo_ext_connectors/tools.py gives the agent the generic controls to find connector tools, describe them, run them, move files in and out, and trim bulky results into something useful.

## Sub-stages

- [Brokered and keyed connector backends](stage-13.1.md) `stage-13.1` — 12 files
- [Native communication and code-service integrations](stage-13.2.md) `stage-13.2` — 6 files

## Files in this stage

### Connector Boundaries and Tools
Defines the secure credential boundary for external connectors and exposes generic agent-facing tools to discover, describe, execute, and normalize connector actions.

### `core/src/ufo/connectors.py`

`domain_logic` · `cross-cutting`

This file is the connector “front desk” for the system. It defines how the rest of UFO asks for credentials, finds connector tools, runs brokered actions, and moves files without directly touching provider secrets. A credential here can take three forms: a special HTTP transport that sends requests through a broker, a bearer token, or provider-specific headers. The important rule is that secrets stay on the correct side of the boundary and are never logged.

There are two big paths. Feed-sync sources, which pull records from providers into UFO, ask an AuthProxy for a Credential. Dynamic connector tools, which let an agent call provider tools, go through a ConnectorBroker. The broker catalogs tools, supplies schemas, executes tool calls, stages uploads, and exposes generated file outputs as short-lived links rather than raw bytes.

ConnectorRegistry is the routing table. It knows which provider belongs to which broker, can ask an open resolver about providers not explicitly registered, and can fall back to direct workspace credentials for bring-your-own-key setups. SourceCredentialResolver adds an extra safety layer for sync jobs: if a source was tied to a member-owned connection, every credential use is checked against the database to make sure that connection is still valid. Think of it like checking that a badge has not been revoked before opening each locked door.

#### Function details

##### `Credential.__repr__`  (lines 57–66)

```
def __repr__(self) -> str
```

**Purpose**: Returns a safe text version of a Credential for debugging. It deliberately hides tokens, headers, and broker transports so an accidental log line does not leak a secret.

**Data flow**: It reads which authentication path is present on the Credential: transport, bearer token, headers, or none. It turns that into a short label such as “bearer: redacted” and returns that label, without including the actual secret value.

**Call relations**: This is used whenever Python needs to display a Credential, such as in debugging or exception output. Other code can pass Credential objects around safely because this method prevents their sensitive contents from being printed.


##### `AuthProxy.credential`  (lines 85–85)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Defines the common promise for anything that can provide authentication for a feed-sync source. A concrete backend may return a broker transport, a bearer token, or special headers.

**Data flow**: It receives a workspace ID, provider name, and account handle. An implementation uses those to find the right account or stored key, then returns a Credential describing how HTTP requests should be authenticated.

**Call relations**: This is a protocol method, meaning this file defines the shape but not the body. The registry and source credential code call objects through this interface so they do not need to know whether credentials come from a broker or from direct stored workspace keys.


##### `stale_grant_guidance`  (lines 93–100)

```
def stale_grant_guidance(provider: str) -> str
```

**Purpose**: Builds a clear error message for the case where a broker no longer recognizes an account grant. It tells the user that retrying is not enough and that the member should reconnect the account.

**Data flow**: It takes a provider name, inserts it into a human-readable explanation, and returns that explanation as a string. It does not read or change any outside state.

**Call relations**: Broker implementations can use this when a tool call or credential lookup points to an old or invalid broker-side account. It keeps the user-facing advice consistent across connector backends.


##### `ConnectorBroker.tools`  (lines 170–172)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Defines how a broker lists tools for a provider, optionally narrowed by a search query. These tools are what the agent may later describe or execute.

**Data flow**: It receives the workspace, provider, and query text. An implementation asks the broker’s catalog and returns matching BrokerTool entries.

**Call relations**: This is part of the ConnectorBroker protocol. Dynamic connector discovery code can call it through the registry without caring which broker, such as Composio or Pipedream, is behind the provider.


##### `ConnectorBroker.schema`  (lines 174–174)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Defines how to fetch the detailed input shape for one broker tool. The input schema tells the agent what arguments that tool accepts.

**Data flow**: It receives the workspace, provider, and tool slug. An implementation returns a BrokerTool with schema details, or raises UnknownBrokerTool if that slug is not valid for the provider.

**Call relations**: This method supports the describe phase of dynamic connector tools. Discovery or planning code asks for a schema before building a tool call.


##### `ConnectorBroker.execute`  (lines 176–184)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Defines how to run one provider tool through the broker under a connected account. The broker injects the real account credential, so UFO does not have to expose the secret.

**Data flow**: It receives the workspace, provider, tool slug, argument values, account ID, and optional idempotency key. An implementation sends that request to the broker and returns the provider tool’s response as a dictionary.

**Call relations**: Dynamic connector execution reaches brokers through this method. It sits at the point where an agent’s chosen tool call becomes a real external-provider action, while keeping the account token broker-side.


##### `ConnectorBroker.file_outputs`  (lines 186–186)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Defines how a broker extracts file results from a tool response. It turns broker-specific response details into standard BrokerFile records.

**Data flow**: It receives the raw response dictionary from a brokered execution. An implementation finds any produced files and returns their names and short-lived download URLs.

**Call relations**: After ConnectorBroker.execute returns, caller code can use this method to discover files the tool produced. The actual bytes are not carried through this seam; the sandbox downloads them from the provided URLs.


##### `ConnectorBroker.stage_upload`  (lines 188–196)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Defines how to prepare a workspace file so a brokered tool can consume it. It gives the sandbox a place to upload the file directly to the broker’s storage.

**Data flow**: It receives the workspace, provider, tool slug, filename, MIME type, and MD5 checksum. An implementation returns upload instructions, including a PUT URL when bytes must be uploaded and the argument value to pass to the tool.

**Call relations**: Tool-call setup code uses this before executing broker tools that accept files. This keeps file bytes out of the serve process: the sandbox uploads directly to the broker’s file store.


##### `ConnectorBroker.search`  (lines 198–198)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Defines semantic search over a broker’s tools. Instead of only matching names, a broker can return useful tools plus a plan, guidance, and warnings.

**Data flow**: It receives a workspace, provider, and natural-language query. An implementation returns a BrokerSearch containing matching tools and optional advice.

**Call relations**: Planning and discovery flows can call this when an agent asks for capabilities in everyday language. Brokers that know more about their services can provide richer guidance through the same interface.


##### `ConnectorBroker.credential`  (lines 200–200)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Defines how a broker supplies feed-sync authentication for one connected account. For brokered accounts, this should usually be a proxy transport rather than a raw token.

**Data flow**: It receives the workspace, provider, and account handle. An implementation confirms the account is valid for that workspace and returns a Credential that lets sync code reach the provider safely.

**Call relations**: _credential calls this when a source is using a brokered account instead of direct workspace credentials. It is the broker-side path for sync authentication.


##### `RequestForwarder.forward`  (lines 219–221)

```
async def forward(self, account_id: str, method: str, url: str, headers: Mapping[str, str], body: bytes) -> ForwardedResponse
```

**Purpose**: Defines how to forward one HTTP request through a broker using a connected account. This lets command-line tools in the sandbox appear authenticated without seeing the real token.

**Data flow**: It receives an account ID, HTTP method, URL, request headers, and request body bytes. An implementation sends the request through the broker and returns a ForwardedResponse with status, headers, and body.

**Call relations**: The egress proxy uses implementations of this interface when it intercepts a request carrying a broker sentinel instead of a real credential. The broker performs the real authenticated request and hands the response back.


##### `ConnectorResolver.transfer_hosts`  (lines 268–268)

```
def transfer_hosts(self) -> tuple[str, ...]
```

**Purpose**: Names the extra file-transfer hosts that grants in an open connector namespace are allowed to contact. This is needed when brokers use their own storage hosts for uploads and downloads.

**Data flow**: An implementation returns a tuple of host names. It does not take arguments because these hosts apply to the resolver’s broker namespace.

**Call relations**: Egress or grant setup code can read this property when deciding which broker file-store hosts the sandbox may access. It complements the dynamic provider routing supplied by the resolver.


##### `ConnectorResolver.claims`  (lines 270–270)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: Answers whether this resolver’s broker can serve a provider slug that was not explicitly registered. This prevents the open namespace from blindly claiming every unknown provider.

**Data flow**: It receives a provider name. An implementation checks the broker’s live catalog or routing rules and returns true or false.

**Call relations**: Code choosing between a broker namespace and direct workspace credentials can ask this before routing a provider. The method is part of the open-provider extension point.


##### `ConnectorResolver.entry`  (lines 272–272)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: Builds a ConnectorEntry for a provider served by the resolver. This gives the rest of the system the same shape it would get for an explicitly registered connector.

**Data flow**: It receives a provider name and returns a ConnectorEntry with that provider, a label, and the shared broker. It is described as pure: it should not need network or database work.

**Call relations**: ConnectorRegistry.entry uses this when a provider is not found in the fixed entries but a resolver exists. _credential also reaches broker credentials through resolver.entry for non-direct accounts.


##### `ConnectorResolver.catalog`  (lines 274–274)

```
async def catalog(self, query: str, limit: int) -> tuple[CatalogEntry, ...]
```

**Purpose**: Searches the broker’s live catalog for connectable services. This lets discovery show providers beyond the ones hard-coded into the registry.

**Data flow**: It receives search text and a maximum number of results. An implementation asks the broker catalog and returns CatalogEntry records with provider slugs and labels.

**Call relations**: ConnectorRegistry.search_catalog delegates to this method when an open resolver is installed. Discovery tools can then combine fixed connectors with live broker catalog results.


##### `ConnectorRegistry.entry`  (lines 291–297)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: Finds the routing entry for a provider. It first checks explicitly installed connectors, then falls back to the open resolver if one exists.

**Data flow**: It receives a provider name. It looks in the registry’s entries mapping; if found, it returns that ConnectorEntry. If not found and a resolver exists, it asks the resolver to build an entry. If neither path works, it raises a KeyError.

**Call relations**: Dynamic connector tools use this as the main routing lookup before talking to a broker. It is the registry’s “which broker owns this provider?” decision point.


##### `ConnectorRegistry.search_catalog`  (lines 299–304)

```
async def search_catalog(self, query: str, limit: int) -> tuple[CatalogEntry, ...]
```

**Purpose**: Returns live catalog search results from the open resolver, if the deployment has one. If there is no resolver, it returns no extra catalog entries.

**Data flow**: It receives query text and a result limit. It checks whether resolver is present; without one it returns an empty tuple, and with one it awaits resolver.catalog and returns those results.

**Call relations**: Discovery flows call this to append open broker catalog results to the known registered connectors. It delegates the real search to ConnectorResolver.catalog.


##### `_credential`  (lines 307–324)

```
async def _credential(registry: ConnectorRegistry, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Chooses the correct credential backend for a source request. Brokered accounts go to the provider’s broker, while the special direct account goes to the configured fallback auth backend.

**Data flow**: It receives the registry, workspace ID, provider, and account handle. If the account is not the direct account, it looks for a registered broker entry or resolver entry and asks that broker for a Credential. If the account is direct, it asks the fallback AuthProxy. If no suitable path exists, it raises a RuntimeError.

**Call relations**: _BoundSourceCredentials.credential calls this after deciding whether the source is direct or connection-bound. This helper centralizes the routing decision so source credential binding does not duplicate registry lookup rules.

*Call graph*: called by 1 (credential).


##### `_require_source_connection`  (lines 327–351)

```
async def _require_source_connection(workspace_id: UUID, connection_id: UUID, owner_member_id: UUID, provider: str, account: str) -> None
```

**Purpose**: Checks that a source’s saved connection still belongs to the same workspace, member, provider, and account. It prevents a sync source from continuing to use a connection after it has been removed or no longer matches.

**Data flow**: It receives workspace ID, connection ID, owner member ID, provider, and account. Inside the workspace context, it opens a database transaction, queries the connection table for an exact match, and returns nothing if found. If no matching row exists, it raises ValueError.

**Call relations**: _BoundSourceCredentials.credential calls this before issuing credentials for a brokered source. _ConnectionTransport.handle_async_request calls it again before each proxied HTTP request, so a connection revoked after credential creation is still caught.

*Call graph*: called by 2 (credential, handle_async_request); 3 external calls (select, workspace_tx, ws).


##### `_ConnectionTransport.handle_async_request`  (lines 363–371)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: Wraps an HTTP transport with a fresh connection-validity check before every outgoing request. This is a safety gate for brokered feed-sync traffic.

**Data flow**: It receives an httpx request object. It first calls _require_source_connection using the stored workspace, connection, member, provider, and account details. If the check passes, it forwards the request to the inner transport and returns the resulting HTTP response.

**Call relations**: _BoundSourceCredentials.credential creates this wrapper when it returns brokered transport credentials. During actual HTTP use, httpx calls this method, which verifies the source connection before handing off to the broker transport.

*Call graph*: calls 1 internal fn (_require_source_connection).


##### `_ConnectionTransport.aclose`  (lines 373–374)

```
async def aclose(self) -> None
```

**Purpose**: Closes the wrapped HTTP transport. This releases whatever network resources the underlying transport owns.

**Data flow**: It takes no new data beyond the stored inner transport. It calls the inner transport’s asynchronous close method and returns nothing.

**Call relations**: HTTP client cleanup code calls this through the standard httpx transport interface. It simply passes shutdown through to the real transport that _ConnectionTransport protects.


##### `_BoundSourceCredentials.credential`  (lines 383–413)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Provides credentials for a specific sync source, while enforcing whether that source is allowed to use direct credentials or must use a member-owned connection. It is the main guardrail around source authentication.

**Data flow**: It receives workspace ID, provider, and account. If the account is the direct account, it rejects the request when this source is connection-bound, otherwise it delegates to _credential. For brokered accounts, it requires stored connection and member IDs, verifies the connection in the database, gets the broker credential through _credential, confirms that credential uses a proxy transport, then returns a new Credential whose transport is wrapped in _ConnectionTransport.

**Call relations**: SourceCredentialResolver.bind creates instances of this class for the sync runner. This method calls _require_source_connection for the initial authorization check, calls _credential to get the actual backend credential, and creates _ConnectionTransport so later HTTP requests keep checking authorization too.

*Call graph*: calls 2 internal fn (_credential, _require_source_connection); 2 external calls (__init__, __init__).


##### `SourceCredentialResolver.bind`  (lines 420–425)

```
def bind(self, connection_id: UUID | None, owner_member_id: UUID | None) -> AuthProxy
```

**Purpose**: Creates an AuthProxy view for one source, optionally tied to a specific member-owned connection. This lets the sync runner ask for credentials without repeatedly passing connection metadata around.

**Data flow**: It receives an optional connection ID and optional owner member ID. It packages those with the registry into a _BoundSourceCredentials object and returns it as the credential resolver for that source.

**Call relations**: The sync runner uses this at setup time for a source. After binding, calls to credential go through _BoundSourceCredentials.credential, which enforces the direct-versus-connection rules.

*Call graph*: 1 external calls (__init__).


### `extensions/connectors/ufo_ext_connectors/tools.py`

`orchestration` · `tool request handling`

A connector broker is like a front desk for many outside services. Instead of teaching the agent one fixed tool for every service action, this file lets the agent search the live connector registry, inspect real tool schemas, and then execute the chosen tool through the right broker. Without it, the agent would have to guess tool names, could not use connected accounts safely, and would struggle with files or huge encoded results.

The file defines four public tool handlers: list connectors, describe connector tools, search tools inside one connector, and call a connector tool. During a call, it first checks which connected account should be used. If the call is a Slack message send, it adds a small “Sent using ufo” attribution footer so text posted into someone else’s Slack is marked.

File arguments get special care. If an argument points at a workspace file, the sandbox hashes and uploads it to the broker’s file store, so the main service process does not handle the bytes directly. Files produced by the connector are downloaded back into a safe workspace folder. If a connector returns base64-encoded data, small UTF-8 text is decoded inline, while binary or large data is written to workspace files. Finally, repeated large JSON objects are replaced with same_as pointers, preserving the facts while keeping the result small enough to stay readable.

#### Function details

##### `list_external_tools`  (lines 254–277)

```
async def list_external_tools(ctx: ToolContext, args: ListExternalToolsInput) -> ToolResult
```

**Purpose**: Searches for available connector providers, such as Slack or GitHub, using the current turn’s connector registry and broker catalog. It is used before choosing a specific connector, so the agent does not rely on hard-coded assumptions.

**Data flow**: It receives the tool context and search queries. It reads the connector registry, matches provider IDs and labels locally, asks broker catalogs for additional matches, removes duplicates, and returns a JSON tool result containing connector source IDs and labels.

**Call relations**: This is one of the public connector tools. It starts by getting the registry through _registry, asks multiple catalog searches at once with asyncio.gather, and wraps the final connector list with _json_result.

*Call graph*: calls 2 internal fn (_json_result, _registry); 1 external calls (gather).


##### `describe_external_tools`  (lines 280–302)

```
async def describe_external_tools(ctx: ToolContext, args: DescribeExternalToolsInput) -> ToolResult
```

**Purpose**: Describes real tools inside one connector and can also discover matching tools by query. It prevents the agent from guessing tool slugs by returning actual names and input schemas from the broker.

**Data flow**: It receives a connector source ID, optional exact tool names, and an optional search query. It looks up the connector, asks the broker for schemas for exact names, records any missing names, optionally searches for available tools, and returns JSON containing schemas, discovery rows, and unresolved names if any.

**Call relations**: This public tool sits between connector selection and execution. It uses _registry to find the connector, _tool_json to shape schema data, _discovery_query when guessed names fail, _discovered_rows for safe discovery output, and _json_result to return the answer.

*Call graph*: calls 5 internal fn (_discovered_rows, _discovery_query, _json_result, _registry, _tool_json).


##### `attribution_stripped`  (lines 305–309)

```
def attribution_stripped(text: str) -> str
```

**Purpose**: Removes any ufo Slack attribution text from a message before deciding what the human actually wrote. This matters because an attribution footer should not be mistaken for a user mention or instruction.

**Data flow**: It receives a text string. It applies the attribution pattern anywhere in the text and returns the same text with those attribution fragments removed.

**Call relations**: This is a small helper for inbound Slack-style text interpretation. It does not call other project functions; it relies on the compiled attribution pattern defined in this file.


##### `attributed_arguments`  (lines 312–339)

```
def attributed_arguments(arguments: dict[str, JsonValue], subject: str) -> dict[str, JsonValue]
```

**Purpose**: Adds a ufo attribution footer to Slack send arguments when there is a message body to mark. It avoids adding a second footer if one is already present.

**Data flow**: It receives a connector argument dictionary and the attribution subject text. It checks whether the body already carries attribution, builds a Slack context block footer, appends it to existing blocks when possible, or converts text or markdown arguments into blocks with the footer added. It returns either the original arguments or a modified copy.

**Call relations**: slack_attributed calls this when a connector call looks like a Slack message send. It uses _carries_attribution to avoid stacking footers, _appended_blocks for existing block payloads, and _body_blocks to turn plain text or markdown into Slack blocks.

*Call graph*: calls 3 internal fn (_appended_blocks, _body_blocks, _carries_attribution); called by 1 (slack_attributed).


##### `_body_blocks`  (lines 342–370)

```
def _body_blocks(arguments: dict[str, JsonValue]) -> list[JsonValue] | None
```

**Purpose**: Turns a Slack message body supplied as plain text or markdown into Slack block objects that can be followed by an attribution footer. It keeps each body in the Slack block type that renders its markup correctly.

**Data flow**: It reads the arguments dictionary. If markdown_text is present, it returns one markdown block. If text is present, it splits it into section blocks small enough for Slack’s per-section limit. If neither body is present, it returns None.

**Call relations**: attributed_arguments calls this only when there are no existing blocks to append to. Its output becomes the body portion of a new blocks argument, followed by the attribution footer.

*Call graph*: called by 1 (attributed_arguments).


##### `_appended_blocks`  (lines 373–394)

```
def _appended_blocks(value: JsonValue, footer: dict[str, JsonValue]) -> JsonValue | None
```

**Purpose**: Adds an attribution footer to an existing Slack blocks argument when that argument is understandable and non-empty. It supports both normal block lists and serialized JSON strings, including URL-encoded strings.

**Data flow**: It receives a blocks value and a footer block. If the value is a non-empty list, it returns a new list with the footer appended. If it is a string, it tries to parse it as JSON, checks that it is a block list without existing attribution, appends the footer, and serializes it back in the same style. If it cannot safely read or append, it returns None.

**Call relations**: attributed_arguments calls this when the caller already provided Slack blocks. It uses _carries_attribution so a serialized message that already has a footer is left untouched.

*Call graph*: calls 1 internal fn (_carries_attribution); called by 1 (attributed_arguments); 4 external calls (dumps, loads, quote, unquote).


##### `_carries_attribution`  (lines 397–406)

```
def _carries_attribution(value: JsonValue) -> bool
```

**Purpose**: Checks whether a value already contains a full-line ufo attribution. This protects Slack messages from getting repeated footers on retries, edits, or reposts.

**Data flow**: It receives any JSON-like value. It searches strings directly, walks through lists item by item, and walks through dictionary values. It returns true as soon as it finds an attribution marker, otherwise false.

**Call relations**: attributed_arguments uses it before changing any arguments, and _appended_blocks uses it after parsing serialized blocks. It is the guard that keeps attribution idempotent, meaning safe to apply more than once.

*Call graph*: called by 2 (_appended_blocks, attributed_arguments); 1 external calls (values).


##### `slack_attributed`  (lines 409–423)

```
def slack_attributed(provider: str, slug: str, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Decides whether a connector call is a Slack message send and, if so, adds ufo attribution to its arguments. Reads, deletes, listings, non-Slack tools, and non-message tools are left unchanged.

**Data flow**: It receives the provider name, tool slug, and arguments. It checks for the Slack provider and for message-send-like words in the slug. If the call qualifies, it returns the arguments after attributed_arguments adds the footer; otherwise it returns the original arguments.

**Call relations**: call_external_tool calls this just before execution. It delegates the actual footer insertion to attributed_arguments, so the Slack-specific decision is separate from the block-editing details.

*Call graph*: calls 1 internal fn (attributed_arguments); called by 1 (call_external_tool).


##### `call_external_tool`  (lines 426–431)

```
async def call_external_tool(ctx: ToolContext, args: CallExternalToolInput) -> ToolResult
```

**Purpose**: Runs one real connector tool through its broker using the user’s connected account. It is the public execution path after the agent has discovered and described the tool schema.

**Data flow**: It receives context plus a source ID, tool name, optional account ID, and tool arguments. It looks up the connector, resolves the connected account, adds Slack attribution when needed, creates a _ConnectorCall, runs it, and returns the resulting text inside a ToolResult.

**Call relations**: This is the public side-effecting connector tool. It uses _registry for lookup, ToolContext.connector_account to choose the account, slack_attributed for Slack sends, and _ConnectorCall.run for the full upload-execute-download-cleanup flow.

*Call graph*: calls 3 internal fn (connector_account, _registry, slack_attributed); 3 external calls (__init__, __init__, __init__).


##### `_ConnectorCall.run`  (lines 459–472)

```
async def run(self, arguments: dict[str, JsonValue], account_id: str) -> str
```

**Purpose**: Executes one connector call from start to finish. It stages input files, calls the broker, fetches output files, translates encoded result data, and shrinks repeated objects.

**Data flow**: It receives already prepared arguments and an account ID. It recursively replaces workspace file references with broker upload references, sends the staged arguments to the broker execute API, downloads any broker file outputs into the workspace, translates base64-like result content, adds workspace file listings when present, and returns the final JSON string.

**Call relations**: call_external_tool creates the _ConnectorCall object and calls this method. It hands work to _staged_value, _fetched_files, _translated_node, and finally runs _deduped in a worker thread so the main async loop is not held up by result shrinking.

*Call graph*: calls 3 internal fn (_fetched_files, _staged_value, _translated_node); 1 external calls (to_thread).


##### `_ConnectorCall._staged_value`  (lines 474–489)

```
async def _staged_value(self, value: object) -> object
```

**Purpose**: Walks through an argument value and finds any workspace file references that must be uploaded before the connector tool runs. This lets connector tools receive files without the model or broker directly reading arbitrary workspace paths.

**Data flow**: It receives one argument value. If it is exactly a workspace_file object, it validates the path and sends it to _stage_file. If it is a dictionary or list, it recursively processes its children. Other values pass through unchanged.

**Call relations**: _ConnectorCall.run calls this for every top-level argument before broker execution. It delegates the actual upload preparation for a single file to _stage_file.

*Call graph*: calls 1 internal fn (_stage_file); called by 1 (run).


##### `_ConnectorCall._stage_file`  (lines 491–527)

```
async def _stage_file(self, path: str) -> dict[str, object]
```

**Purpose**: Uploads one workspace file to the broker’s file store, or reuses an existing broker copy if the broker says it already has the file. It uses the sandbox to keep file reading and network transfer outside the main service process.

**Data flow**: It receives a workspace path. Inside the sandbox it checks the path, computes an MD5 digest, and measures size. It rejects unreadable or oversized files, guesses a filename and media type, asks the broker for an upload location, optionally uses curl in the sandbox to PUT the file bytes there, and returns the broker argument value that refers to the staged file.

**Call relations**: _staged_value calls this whenever it sees a workspace_file argument. It relies on sandbox helpers for path scoping and shell quoting, and it returns data that _ConnectorCall.run includes in the broker execute request.

*Call graph*: called by 1 (_staged_value); 4 external calls (guess_type, PurePosixPath, quote, workspace_path).


##### `_ConnectorCall._fetched_files`  (lines 529–562)

```
async def _fetched_files(self, files: tuple[BrokerFile, ...]) -> list[dict[str, str]]
```

**Purpose**: Downloads files produced by a connector tool into the workspace. It creates safe, unique destinations so provider-supplied filenames cannot overwrite or redirect other workspace files.

**Data flow**: It receives broker file records, each with a name and download URL. For each file, it reduces the name to a safe leaf filename, creates a fresh connector_files path, claims it through the sandbox containment guard, downloads the URL with curl inside the sandbox, and returns a list of file names and workspace paths.

**Call relations**: _ConnectorCall.run calls this after broker execution, using the broker’s file_outputs view of the response. The returned list is added to the final tool result under the workspace files key.

*Call graph*: called by 1 (run); 3 external calls (quote, contained_leaf, uuid4).


##### `_ConnectorCall._translated_node`  (lines 564–624)

```
async def _translated_node(self, node: Mapping[str, object], depth: int=0) -> dict[str, object]
```

**Purpose**: Looks at one result object and decodes provider-marked base64 fields into something the agent can actually use. Small text becomes readable text; large or binary content becomes a workspace file reference.

**Data flow**: It receives a dictionary-like result node and a recursion depth. It finds marker fields saying the node contains base64, recursively translates child values, decodes marked content fields when valid, chooses a filename and media type, turns bytes into inline text or offloaded files, and updates the marker to say whether content is now UTF-8 text or offloaded.

**Call relations**: _ConnectorCall.run starts result translation here, and _translated calls it for nested dictionaries. It uses _decoded_base64 for safe decoding and _translated_bytes to decide whether decoded content stays inline or becomes a file.

*Call graph*: calls 3 internal fn (_translated, _translated_bytes, _decoded_base64); called by 2 (_translated, run); 1 external calls (guess_type).


##### `_ConnectorCall._translated`  (lines 626–645)

```
async def _translated(self, value: object, depth: int) -> object
```

**Purpose**: Recursively translates any value inside a connector result. It handles nested objects, lists, and data URLs while leaving ordinary values unchanged.

**Data flow**: It receives a value and the current depth. If nesting is too deep, it returns the value unchanged. For dictionaries it calls _translated_node; for lists it translates each item; for short enough data: URLs it calls _translated_data_url; otherwise it returns the original value.

**Call relations**: _translated_node calls this for child values, so this method is the recursive walker. It hands special cases back to _translated_node or _translated_data_url depending on the shape of the value.

*Call graph*: calls 2 internal fn (_translated_data_url, _translated_node); called by 1 (_translated_node).


##### `_ConnectorCall._translated_data_url`  (lines 647–659)

```
async def _translated_data_url(self, value: str) -> object
```

**Purpose**: Decodes a data URL that embeds base64 content directly in a string. This prevents large unreadable data URL text from filling the conversation when it can be shown as text or saved as a file.

**Data flow**: It receives a string starting with data:. It checks whether it matches the expected base64 data URL pattern, decodes the payload if valid, uses the declared media type or a fallback type, chooses a reasonable fallback filename, and passes the bytes to _translated_bytes. If anything does not match or decode, it returns the original string.

**Call relations**: _translated calls this only for strings that look like bounded-size data URLs. It uses _decoded_base64 for validation and _translated_bytes for the final inline-versus-file decision.

*Call graph*: calls 2 internal fn (_translated_bytes, _decoded_base64); called by 1 (_translated); 1 external calls (guess_extension).


##### `_ConnectorCall._translated_bytes`  (lines 661–670)

```
async def _translated_bytes(self, decoded: bytes, text: str | None, name: str, mimetype: str) -> object
```

**Purpose**: Chooses the most useful form for decoded bytes from a connector result. Readable small text stays directly in the JSON result; binary or large data is saved to a workspace file.

**Data flow**: It receives raw bytes, optional decoded UTF-8 text, a filename, and a media type. If the text exists and is within the inline limit, it returns that text. Otherwise it calls _offloaded and returns a file reference object.

**Call relations**: _translated_node and _translated_data_url both call this after decoding content. It delegates file writing to _offloaded only when inline text would be unsuitable.

*Call graph*: calls 1 internal fn (_offloaded); called by 2 (_translated_data_url, _translated_node).


##### `_ConnectorCall._offloaded`  (lines 672–714)

```
async def _offloaded(self, name: str, mimetype: str, data: bytes) -> dict[str, object]
```

**Purpose**: Writes decoded result bytes into the workspace and returns a reference to the saved file. It is used for binary data or text too large to keep inline.

**Data flow**: It receives a name, media type, and byte content. It makes the filename safe, builds a content-addressed path using a SHA-256 digest, writes the bytes to a temporary part file through the sandbox, atomically places that file at the final path, and returns metadata including name, workspace path, media type, and byte count.

**Call relations**: _translated_bytes calls this when decoded content should not be inline. It uses the sandbox’s write path and placement guard so repeated identical payloads share one destination safely.

*Call graph*: called by 1 (_translated_bytes); 3 external calls (sha256, contained_leaf, uuid4).


##### `_ConnectorCall._deduped`  (lines 716–774)

```
def _deduped(self, payload: dict[str, object]) -> str
```

**Purpose**: Serializes the connector result and, when it is safe and worthwhile, replaces repeated large objects with pointers to their first copy. This keeps denormalized results readable without losing information.

**Data flow**: It receives the final payload dictionary. It serializes it once, checks size, structural complexity, and whether same_as already appears in provider data. If any guard says not to rewrite, it returns the original JSON string. Otherwise it walks each top-level value through _condensed and returns JSON for the condensed payload.

**Call relations**: _ConnectorCall.run calls this in a worker thread after all transfers and translations are complete. It uses _condensed for the structural comparison and _escaped to build JSON Pointer paths.

*Call graph*: calls 2 internal fn (_condensed, _escaped); 1 external calls (dumps).


##### `_ConnectorCall._condensed`  (lines 776–852)

```
def _condensed(self, value: object, pointer: str, depth: int, first: dict[bytes, str]) -> tuple[object, bytes, int]
```

**Purpose**: Walks one part of a result and detects repeated dictionary objects by structural identity. Later copies of large identical objects become same_as references to the first copy.

**Data flow**: It receives a value, its JSON Pointer path, the current depth, and a map of first-seen object digests. It recursively processes dictionaries and lists, computes a SHA-256 digest that represents each node’s structure and contents, records large first-seen dictionaries, and replaces later matching dictionaries with a pointer object. It returns the possibly changed value, its digest, and its original size estimate.

**Call relations**: _deduped calls this for each top-level payload item. The method calls itself recursively and uses _escaped when extending pointer paths through dictionary keys.

*Call graph*: calls 1 internal fn (_escaped); called by 1 (_deduped); 1 external calls (sha256).


##### `_escaped`  (lines 855–858)

```
def _escaped(token: str) -> str
```

**Purpose**: Escapes one path token for use in a JSON Pointer, which is a standard way to point to a location inside JSON. This makes keys containing slash or tilde characters point to the correct place.

**Data flow**: It receives a string token. It replaces ~ with ~0 and / with ~1, then returns the escaped token.

**Call relations**: _deduped and _condensed use this when building same_as pointer paths. It is small, but without it a pointer could name the wrong JSON field.

*Call graph*: called by 2 (_condensed, _deduped).


##### `_decoded_base64`  (lines 861–885)

```
def _decoded_base64(value: object) -> tuple[bytes, str | None] | None
```

**Purpose**: Safely decodes a value that a provider marked as base64. It is strict, bounded, and leaves mislabeled values untouched instead of turning them into garbage.

**Data flow**: It receives any value. If it is not a string or is too long, it returns None. Otherwise it removes whitespace, strictly base64-decodes the string, then tries to decode the bytes as UTF-8 text. It returns bytes plus text when available, or bytes plus None for binary data; invalid base64 returns None.

**Call relations**: _translated_node and _translated_data_url call this before converting connector result content. It is the validation gate that decides whether a marked field really can be translated.

*Call graph*: called by 2 (_translated_data_url, _translated_node); 1 external calls (b64decode).


##### `search_connector_tools`  (lines 888–902)

```
async def search_connector_tools(ctx: ToolContext, args: SearchConnectorToolsInput) -> ToolResult
```

**Purpose**: Performs richer tool discovery inside one connector using a natural-language goal. It can return matching tools plus broker-provided advice such as plan steps, guidance, and pitfalls.

**Data flow**: It receives a source ID and query. It looks up the connector, asks the broker search API for matches and advice, formats the tool rows with _discovered_rows, adds plan, guidance, and pitfalls, and returns the result as JSON.

**Call relations**: This is a public discovery tool alongside describe_external_tools. It uses _registry for connector lookup, _discovered_rows to apply fallback and budget rules, and _json_result for the final ToolResult.

*Call graph*: calls 3 internal fn (_discovered_rows, _json_result, _registry).


##### `_registry`  (lines 905–908)

```
def _registry(ctx: ToolContext) -> ConnectorRegistry
```

**Purpose**: Returns the connector registry for the current turn, or raises a clear error if connector tools were invoked without one. This avoids later failures that would be harder to understand.

**Data flow**: It receives the tool context. If ctx.connectors is present, it returns that registry. If it is missing, it raises a runtime error explaining that connector dispatch lacked the turn’s registry.

**Call relations**: The public connector handlers call this at their start: list_external_tools, describe_external_tools, search_connector_tools, and call_external_tool. It is the common doorway from a tool request into the live connector setup.

*Call graph*: called by 4 (call_external_tool, describe_external_tools, list_external_tools, search_connector_tools).


##### `_tool_json`  (lines 911–912)

```
def _tool_json(tool: BrokerTool) -> dict[str, object]
```

**Purpose**: Converts a broker tool object into the simple JSON shape returned to the agent. It keeps the fields the agent needs: slug, description, and input schema.

**Data flow**: It receives a BrokerTool. It reads its slug, description, and input_schema fields, and returns them in a dictionary.

**Call relations**: describe_external_tools uses this for exact schema lookups, and _available_tools uses it when preparing discovery rows. It keeps tool descriptions consistent across both paths.

*Call graph*: called by 2 (_available_tools, describe_external_tools).


##### `_discovered_rows`  (lines 915–932)

```
async def _discovered_rows(entry: ConnectorEntry, workspace_id: UUID, query: str, found: tuple[BrokerTool, ...]) -> tuple[list[dict[str, object]], str]
```

**Purpose**: Builds the tool rows for discovery answers and adds notes when the answer is a fallback or was shortened. It makes sure a failed query is not a dead end.

**Data flow**: It receives a connector entry, workspace ID, query, and broker-found tools. If the query was non-empty and found nothing, it asks the broker for the connector’s unqueried top tools instead. It trims the rows through _available_tools and returns the rows plus a note explaining fallback or omissions.

**Call relations**: describe_external_tools and search_connector_tools both use this, so both discovery routes share the same fallback behavior and result-size limits.

*Call graph*: calls 1 internal fn (_available_tools); called by 2 (describe_external_tools, search_connector_tools).


##### `_available_tools`  (lines 935–948)

```
def _available_tools(listed: tuple[BrokerTool, ...]) -> list[dict[str, object]]
```

**Purpose**: Selects as many tool catalog rows as will fit within the inline result budget. This helps keep discovery output in the conversation instead of being offloaded to a file.

**Data flow**: It receives a tuple of BrokerTool objects. It converts each one with _tool_json, estimates the JSON size, stops once adding more would exceed the budget after at least one row, and returns the rows kept.

**Call relations**: _discovered_rows calls this for both normal search results and fallback top-tool lists. It uses json serialization only to estimate how much room the row will take.

*Call graph*: calls 1 internal fn (_tool_json); called by 1 (_discovered_rows); 1 external calls (dumps).


##### `_discovery_query`  (lines 951–958)

```
def _discovery_query(explicit: str, unresolved: list[str]) -> str
```

**Purpose**: Chooses the search query used when describe_external_tools needs discovery. If the caller gave explicit keywords, it uses them; otherwise it turns unresolved guessed slugs into useful words.

**Data flow**: It receives an explicit query string and a list of unresolved tool names. If the explicit query is non-empty, it returns it. Otherwise it lowercases the unresolved names, replaces non-alphanumeric runs with spaces, removes duplicate words while preserving order, and returns the resulting search phrase.

**Call relations**: describe_external_tools calls this when exact requested tool names were missing or when discovery is needed. It helps turn a bad guessed slug into a broker catalog search that may reveal the real slug.

*Call graph*: called by 1 (describe_external_tools); 1 external calls (sub).


##### `_json_result`  (lines 961–962)

```
def _json_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: Wraps a dictionary payload as a text-based ToolResult containing JSON. It is the common final packaging step for non-execution connector tools.

**Data flow**: It receives a dictionary. It serializes it with json.dumps, puts that text into a TextContent object, and returns a ToolResult containing that content.

**Call relations**: list_external_tools, describe_external_tools, and search_connector_tools call this after building their response payloads. call_external_tool does its own wrapping because _ConnectorCall.run already returns serialized text.

*Call graph*: called by 3 (describe_external_tools, list_external_tools, search_connector_tools); 3 external calls (__init__, __init__, dumps).

## 📊 State Registers Touched

- `reg-extension-registry` — The loaded list of installed extensions, packs, routes, tools, skills, jobs, credentials, backends, and migrations.
- `reg-database-store` — The shared database connection and tables where workspaces, users, agents, turns, files, jobs, costs, and extension data are saved.
- `reg-workspace-principals` — The current workspace, members, agents, controlling users, and ownership identities used to decide who is acting.
- `reg-session-auth` — The login sessions, signed tokens, protected links, callback state, and request identities proving who a visitor or service is.
- `reg-visibility-boundaries` — The saved rules for who may see each conversation, agent, transcript, source, memory, artifact, or workspace object.
- `reg-credential-vault` — The encrypted store of API keys, OAuth tokens, and other secrets that can be injected only into approved places.
- `reg-connection-grants` — The saved account connections and per-agent permissions that say which outside accounts an agent may use.
- `reg-model-usage-accounting` — The recorded token, image, video, embedding, sandbox, egress, and cost usage used for billing and audit trails.
- `reg-spend-controls` — The workspace spending caps, prepaid balances, top-up settings, BYOK flags, and billing export state.
- `reg-tool-catalog` — The shared catalog of tools the model can call, including built-in tools, extension tools, connector tools, and their safety labels.
- `reg-tool-execution-context` — The per-turn but shared rulebook passed through tools, saying who the tool acts for, what files, accounts, sandboxes, and subagents it may use.
- `reg-sandbox-runtime` — The remembered sandbox handles, workspace directories, terminals, command sessions, ports, and cleanup state used for safe code execution.
- `reg-egress-policy` — The network access rules and proxy authorization state that decide what sandboxed code may contact outside the system.
- `reg-source-sync-catalog` — The saved catalog of external sources, pages, sync cursors, deletion marks, retry backoff, and indexing needs.
- `reg-search-index` — The shared keyword and embedding indexes that let conversations, tools, and background jobs find relevant stored documents.
- `reg-connector-action-cache` — Dynamic connector/MCP action schemas, allowed-action listings, and runtime client/session caches reused when exposing and executing external-service actions.
