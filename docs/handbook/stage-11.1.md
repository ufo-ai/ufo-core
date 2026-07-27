# External connector and brokered app tools  `stage-11.1`

This stage is shared behind-the-scenes support for letting the agent use outside apps safely. It is like a plug adapter and security desk between the agent and services such as GitHub, Slack, Gmail, YC, or test-only fake apps.

The core connector contract defines the common “plug shape”: how tools are discovered, run through a broker, and given credentials without exposing secret tokens. The generic connector tools let the agent find and call these tools, pass files in and out, and trim bulky results so they fit in a conversation.

Composio and Pipedream each provide a broker, client, and proxy. The client talks to the outside broker service, the broker presents its tools to UFO in the common format, and the proxy rewrites normal web requests so UFO never sees the user’s raw provider token. Composio also has a resolver and MCP search session for finding toolkits dynamically.

The MCP extension connects to workspace-configured tool servers. Slack tools guide setup and conversation lookup. YC tools safely call YC’s command-line system. The evaluation environment supplies predictable fake email, calendar, and code-search connectors for tests. Empty package files simply make these extension folders importable.

## Files in this stage

### Connector contract and generic tools
The shared connector interface and agent-facing tool wrapper define how external tools are discovered, invoked, and cleaned up safely.

### `core/src/ufo/connectors.py`

`data_model` · `startup and connector/tool/sync routing`

Connectors let this system talk to outside services like Gmail, GitHub, or other providers. The hard part is doing that without spreading private tokens through the app, the sandbox, or logs. This file draws the boundary. It says: a feed-sync job may ask for a Credential, but that credential must be one of a few controlled forms, such as a safe request transport, a bearer token, or special headers. It also says: dynamic connector tools should run through a ConnectorBroker, which owns the real provider token and injects it on the server side.

Think of this file like the rules for a hotel front desk. Guests do not get master keys; they ask the desk to open the right door. Here, brokers and auth proxies are the front desk. They know how to reach the outside provider, while the rest of the system only sees safe references and results.

The file also defines small value objects for connector tools, uploaded files, downloaded files, catalog entries, and forwarded HTTP responses. The main working piece is ConnectorRegistry. It keeps the installed connector entries, optionally delegates unknown provider names to an open resolver, and decides whether a credential request should go to a broker or to a fallback direct-auth backend. Without this file, connector extensions would not have a stable way to plug in, and secret-handling rules would be scattered and easier to break.

#### Function details

##### `Credential.__repr__`  (lines 52–61)

```
def __repr__(self) -> str
```

**Purpose**: Returns a safe text representation of a Credential without showing any secret value. This matters because object representations often appear in logs, error messages, or debugging output.

**Data flow**: It reads which credential path is present: a transport, a bearer token, headers, or nothing. It then returns a short string that names the kind of credential while replacing the actual secret with the word “redacted.” It does not change the Credential.

**Call relations**: This is used implicitly whenever Python needs to display a Credential, such as during debugging or accidental logging. It protects the rest of the connector flow by making the safe behavior automatic at the value-object level.


##### `AuthProxy.credential`  (lines 81–81)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Defines the promise that an authentication backend must fulfill: given a workspace, provider, and account, return the safe Credential a sync connector can use. It is a protocol method, so this file describes the shape but leaves the real lookup to a concrete backend.

**Data flow**: The caller supplies a workspace identifier, a provider name, and an account handle. An implementation uses that information to find or create the right Credential, such as a broker transport or direct headers, and returns it asynchronously.

**Call relations**: Feed-sync code and ConnectorRegistry call through this interface when they need provider authentication. Concrete auth backends implement it so the core code does not need to know where secrets are stored or how a broker injects them.


##### `stale_grant_guidance`  (lines 89–96)

```
def stale_grant_guidance(provider: str) -> str
```

**Purpose**: Builds a clear error message for a grant that points to an account the current broker does not recognize. It tells the user-facing side that retrying will not help and that the member should reconnect the account.

**Data flow**: It takes a provider name, inserts it into a short explanatory sentence, and returns that sentence. It does not read or change any external state.

**Call relations**: Broker implementations can use this helper when account lookup fails because a grant is old or belongs to a previous broker setup. The returned text is meant to travel with broker errors so the agent or user gets practical next steps.


##### `ConnectorBroker.tools`  (lines 166–168)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Defines how a broker lists the tools it can offer for a provider, optionally narrowed by a search query. It lets dynamic connector discovery show useful actions without exposing provider secrets.

**Data flow**: The caller provides the workspace, provider name, and query text. A broker implementation searches its own catalog and returns BrokerTool records describing matching tools.

**Call relations**: Dynamic connector tools call this through the broker chosen by ConnectorRegistry. The protocol keeps the caller independent from a specific broker service such as Composio, Pipedream, or another implementation.


##### `ConnectorBroker.schema`  (lines 170–170)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Defines how a broker returns the full input shape for one provider tool. This helps an agent know what arguments it must supply before asking the broker to execute the tool.

**Data flow**: The caller gives a workspace, provider, and tool slug. The broker implementation returns a BrokerTool with its input schema filled in, or raises UnknownBrokerTool if the slug is not valid for that provider.

**Call relations**: Tool-description flows call this after a tool has been selected or named. It feeds the agent-facing layer with the exact argument schema while keeping execution behind the broker boundary.


##### `ConnectorBroker.execute`  (lines 172–180)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Defines how a broker runs one provider tool using a connected account. The broker, not the sandbox or core agent code, applies the real provider credential.

**Data flow**: The caller sends the workspace, provider, tool slug, argument values, account id, and an optional idempotency key, which is a repeat-safe request identifier. The broker implementation runs the tool under that account and returns a dictionary response from the broker/provider side.

**Call relations**: Dynamic connector execution calls this after discovering a tool and preparing arguments. It is the main handoff point where core code asks the broker to act, while secrets remain inside the broker.


##### `ConnectorBroker.file_outputs`  (lines 182–182)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Defines how a broker points out files produced by a tool run. Instead of passing file bytes through the serve process, it returns names and temporary download URLs.

**Data flow**: The caller provides the raw response dictionary from a broker execution. The implementation inspects that response and returns BrokerFile entries for any produced files.

**Call relations**: After ConnectorBroker.execute returns, the connector tool layer can call this to find files the sandbox should fetch directly. This keeps large or sensitive file bytes out of the central process.


##### `ConnectorBroker.stage_upload`  (lines 184–192)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Defines how a broker prepares a workspace file so a provider tool can consume it. It creates a temporary upload target or reuses an existing staged object.

**Data flow**: The caller supplies workspace and provider details, the tool slug, a filename, MIME type, and an MD5 checksum, which is a content fingerprint. The broker returns a StagedUpload telling the sandbox where to PUT the file, what content type to use, and what argument value to pass into the tool call.

**Call relations**: Before executing a broker tool that needs a file input, the dynamic connector flow asks the broker to stage that file. The sandbox then transfers the bytes directly to broker storage, and execution later receives only the staged reference.


##### `ConnectorBroker.search`  (lines 194–194)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Defines a richer search operation for finding relevant broker tools. Besides matching tools, it may return a plan, guidance, or warnings that help an agent choose correctly.

**Data flow**: The caller gives a workspace, provider, and natural-language query. The implementation returns a BrokerSearch containing matching BrokerTool records plus optional planning notes.

**Call relations**: Agent-facing discovery can call this when plain tool listing is not enough. It lets broker-specific routing intelligence improve the tool-selection step without changing the core connector interface.


##### `ConnectorBroker.credential`  (lines 196–196)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Defines how a broker resolves the Credential needed by feed syncing for one granted account. The broker confirms and represents the account without exposing the underlying token.

**Data flow**: The caller provides a workspace id, provider name, and account handle. The broker implementation validates that the account belongs in that context and returns a Credential, often a transport that routes requests through the broker.

**Call relations**: ConnectorRegistry.credential calls this when a feed-sync source is tied to a broker-granted account rather than a direct workspace key. This keeps sync connectors using the same broker-owned secret boundary as tool execution.


##### `RequestForwarder.forward`  (lines 215–217)

```
async def forward(self, account_id: str, method: str, url: str, headers: Mapping[str, str], body: bytes) -> ForwardedResponse
```

**Purpose**: Defines how an intercepted provider HTTP request is forwarded through a broker under a granted account. This is used when a command-line tool in the sandbox sends a request carrying a safe sentinel instead of the real token.

**Data flow**: The caller provides the account id, HTTP method, URL, request headers, and body bytes. The implementation sends the request through the broker, where the real credential is injected, and returns a ForwardedResponse containing status, headers, and body.

**Call relations**: The egress proxy calls this through a CliCredential when it recognizes a request that should be broker-authenticated. The response then goes back to the sandbox as if it came from the provider.


##### `ConnectorResolver.transfer_hosts`  (lines 263–263)

```
def transfer_hosts(self) -> tuple[str, ...]
```

**Purpose**: Defines the extra file-transfer hostnames allowed for grants in an open connector namespace. These are hosts the sandbox may need to reach when uploading to or downloading from broker file storage.

**Data flow**: An implementation exposes a tuple of host strings. Callers read it to extend the network allowance for connector file transfers; no input is passed and no state is changed by the property itself.

**Call relations**: Grant and egress setup code can read this from the resolver when a broker claims many provider slugs through an open namespace. It supports the file-transfer paths used by stage_upload and file_outputs.


##### `ConnectorResolver.entry`  (lines 265–265)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: Defines how an open resolver builds a ConnectorEntry for a provider slug that was not explicitly registered. This lets one broker serve many providers without listing each one in the local registry.

**Data flow**: The caller supplies a provider name. The resolver returns a ConnectorEntry pointing that provider to the shared broker and a label suitable for routing or display.

**Call relations**: ConnectorRegistry.entry and ConnectorRegistry.credential call this when a provider is not found in the fixed entries but an open resolver exists. The returned entry lets the normal broker flow continue for that provider.


##### `ConnectorResolver.catalog`  (lines 267–267)

```
async def catalog(self, query: str, limit: int) -> tuple[CatalogEntry, ...]
```

**Purpose**: Defines how an open resolver searches its broker’s live catalog of connectable services. This lets discovery show providers that were not predeclared in the core registry.

**Data flow**: The caller provides query text and a maximum number of results. The implementation asks its broker-side catalog and returns CatalogEntry records for matching services.

**Call relations**: ConnectorRegistry.search_catalog delegates to this when a resolver is installed. Discovery tools can then combine fixed connector entries with live broker catalog results.


##### `ConnectorRegistry.entry`  (lines 286–292)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: Finds the ConnectorEntry that owns a provider name. It first checks explicitly installed connectors, then asks the open resolver, and fails clearly if neither can serve the provider.

**Data flow**: It receives a provider string and looks in the registry’s entries mapping. If found, it returns that entry; if not, it asks the resolver to create one; if there is no resolver, it raises a KeyError.

**Call relations**: Dynamic connector tools use this when they need to dispatch a provider request to the right broker. It is the registry’s basic routing step before tool listing, schema lookup, execution, or other broker calls.


##### `ConnectorRegistry.search_catalog`  (lines 294–299)

```
async def search_catalog(self, query: str, limit: int) -> tuple[CatalogEntry, ...]
```

**Purpose**: Searches the open connector catalog when an open resolver is installed. If there is no resolver, it safely returns no extra catalog entries.

**Data flow**: It receives a query and result limit. With no resolver, it returns an empty tuple; with a resolver, it awaits the resolver’s catalog search and returns those CatalogEntry results.

**Call relations**: Discovery flows call this to add live broker-backed services to the list of known connectors. It hands the search off to ConnectorResolver.catalog because only the resolver knows the broker’s open catalog.


##### `ConnectorRegistry.credential`  (lines 301–314)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Chooses where a feed-sync credential request should go. Broker-granted accounts go to the matching broker, while the special direct-account handle goes to the fallback auth backend.

**Data flow**: It receives a workspace id, provider, and account handle. If the account is not the direct-account marker, it tries an explicit connector broker, then the open resolver’s broker. If that does not apply, it asks the fallback AuthProxy. If no path can resolve the credential, it raises a RuntimeError.

**Call relations**: The sync runner uses the registry as its AuthProxy. This method is the key decision point that preserves the difference between “this source uses a broker grant” and “this source uses the workspace’s own direct key,” then hands off to ConnectorBroker.credential or AuthProxy.credential accordingly.


### `extensions/connectors/ufo_ext_connectors/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a directory can act like an importable package when it contains an `__init__.py` file. You can think of it like a label on a folder that says, “Python may treat everything inside this folder as part of one named module.” Without this file, some Python setups or tools might not reliably recognize `extensions/connectors/ufo_ext_connectors` as a package, which could make imports fail or behave differently. Because the file is empty, it does not run setup code, expose shortcuts, or change how connectors work. Its value is structural: it helps organize the connector extension code under a clear package name.


### `extensions/connectors/ufo_ext_connectors/tools.py`

`orchestration` · `request handling`

A connector broker is like a front desk for many outside services. The agent should not guess which tools exist or keep thousands of tool definitions loaded. Instead, this file exposes four general tools: find connectors, describe a connector’s tools, search within a connector, and run one chosen tool.

When a tool is run, the file does more than pass along JSON. If an argument points to a workspace file, it first uploads that file to the broker’s file storage and replaces the argument with the broker’s reference. After the broker runs the real external action, any files produced by the connector are downloaded back into the workspace so the agent can read them later.

It also protects the conversation from awkward result formats. Some providers return file contents as base64, which is text that represents raw bytes but is not useful to read directly. This file decodes marked base64 fields: small UTF-8 text stays inline, while large or binary data is written to the workspace and replaced with a file reference. Finally, it shrinks repeated objects in large JSON results by keeping the first copy and replacing later identical copies with a pointer. Without this file, connector access would be brittle, file-heavy calls would fail or leak huge blobs into context, and repeated data could make results too large to use.

#### Function details

##### `list_external_tools`  (lines 139–162)

```
async def list_external_tools(ctx: ToolContext, args: ListExternalToolsInput) -> ToolResult
```

**Purpose**: Searches the live connector registry for available connector providers, such as GitHub or Slack. It is used when the agent needs to discover which outside services can be accessed before looking for specific actions inside them.

**Data flow**: It receives a tool context and search queries. It reads the turn’s connector registry, matches query words against locally known connector IDs and labels, also asks the broker catalog for matching providers, removes duplicates, and returns a JSON result containing connector IDs and labels.

**Call relations**: This is exposed as the handler for the list_external_tools tool. It gets the registry through _registry, asks multiple catalog searches in parallel with asyncio.gather, and hands the final response to _json_result so it becomes normal tool output.

*Call graph*: calls 2 internal fn (_json_result, _registry); 1 external calls (gather).


##### `describe_external_tools`  (lines 165–187)

```
async def describe_external_tools(ctx: ToolContext, args: DescribeExternalToolsInput) -> ToolResult
```

**Purpose**: Describes real tools inside one connector and can also list likely matching tools. It prevents the agent from guessing tool names by asking the broker for the connector’s actual schema information.

**Data flow**: It receives a connector source ID, optional exact tool names, and an optional search query. It looks up the connector entry, asks the broker for schemas for exact names, records any names the broker does not know, optionally asks for nearby available tools, and returns JSON with schemas, available tools, and unresolved names when needed.

**Call relations**: This is the handler for describe_external_tools. It uses _registry to find the connector, _tool_json to turn broker tool objects into plain JSON, _discovery_query to build a fallback search from failed guessed names, and _json_result to return the answer.

*Call graph*: calls 4 internal fn (_discovery_query, _json_result, _registry, _tool_json).


##### `call_external_tool`  (lines 190–194)

```
async def call_external_tool(ctx: ToolContext, args: CallExternalToolInput) -> ToolResult
```

**Purpose**: Runs one real external connector tool after the agent has chosen a source and tool name. It also picks the correct connected account when the user has more than one account for that connector.

**Data flow**: It receives the connector source, tool name, account choice, and arguments. It looks up the connector, resolves the account ID through the tool context, creates a _ConnectorCall to do the full execution flow, and returns the call’s text as tool output.

**Call relations**: This is the handler for call_external_tool. It calls _registry first, asks ToolContext.connector_account for the account to use, then delegates the complicated staging, execution, fetching, decoding, and shrinking work to _ConnectorCall.run.

*Call graph*: calls 2 internal fn (connector_account, _registry); 3 external calls (__init__, __init__, __init__).


##### `_ConnectorCall.run`  (lines 222–235)

```
async def run(self, arguments: dict[str, JsonValue], account_id: str) -> str
```

**Purpose**: Performs one connector tool execution from beginning to end. It prepares input files, calls the broker, pulls back output files, translates base64 payloads, and shrinks repeated JSON before returning text.

**Data flow**: It starts with user-supplied arguments and an account ID. It walks every argument to stage workspace files, sends the transformed arguments to the broker’s execute API, downloads any broker-reported output files, rewrites marked base64 content into readable text or workspace file references, adds downloaded file information, and returns a serialized JSON string.

**Call relations**: call_external_tool creates the _ConnectorCall and invokes this method. Inside the method, _staged_value prepares inputs, _fetched_files brings produced files back, _translated_node cleans the broker response, and _deduped runs in a worker thread so large result cleanup does not block the main event loop.

*Call graph*: calls 3 internal fn (_fetched_files, _staged_value, _translated_node); 1 external calls (to_thread).


##### `_ConnectorCall._staged_value`  (lines 237–252)

```
async def _staged_value(self, value: object) -> object
```

**Purpose**: Walks through an argument value and replaces any workspace file marker with a broker-ready file reference. This lets connector tools receive files without the main service copying file bytes itself.

**Data flow**: It receives one argument value, which may be a plain value, list, dictionary, or special workspace_file dictionary. Plain values pass through unchanged, lists and dictionaries are recursively scanned, and exact workspace_file objects are sent to _stage_file and replaced with that result.

**Call relations**: _ConnectorCall.run calls this for each top-level argument before executing the broker tool. When it finds an actual workspace file marker, it hands off to _stage_file to do the upload preparation.

*Call graph*: calls 1 internal fn (_stage_file); called by 1 (run).


##### `_ConnectorCall._stage_file`  (lines 254–285)

```
async def _stage_file(self, path: str) -> dict[str, object]
```

**Purpose**: Uploads, or confirms the broker already has, one workspace file needed as input to a connector tool. It enforces a size limit and returns the broker’s argument value for that file.

**Data flow**: It receives a workspace path. It converts it to a safe workspace path, asks the sandbox to hash and measure the file, rejects unreadable or too-large files, guesses a content type from the filename, asks the broker for an upload location, optionally uses sandbox curl to PUT the file bytes there, and returns the broker-provided reference argument.

**Call relations**: _staged_value calls this when it sees a workspace_file argument. It uses sandbox shell commands and broker stage_upload so the file transfer happens outside the serve process, then passes the resulting reference back up to the eventual broker execute call.

*Call graph*: called by 1 (_staged_value); 4 external calls (guess_type, PurePosixPath, quote, workspace_path).


##### `_ConnectorCall._fetched_files`  (lines 287–306)

```
async def _fetched_files(self, files: tuple[BrokerFile, ...]) -> list[dict[str, str]]
```

**Purpose**: Downloads files produced by a connector tool into the workspace. This gives the agent local paths to read instead of temporary broker download URLs.

**Data flow**: It receives broker file records, each with a name and URL. For each one, it chooses a safe filename, creates a unique workspace target directory, asks the sandbox to curl the file with a size cap, and returns a list of file names and workspace paths.

**Call relations**: _ConnectorCall.run calls this after broker execution, using the broker’s file_outputs view of the response. The returned list is added to the final tool result under the workspace_files key.

*Call graph*: called by 1 (run); 3 external calls (PurePosixPath, quote, uuid4).


##### `_ConnectorCall._translated_node`  (lines 308–368)

```
async def _translated_node(self, node: Mapping[str, object], depth: int=0) -> dict[str, object]
```

**Purpose**: Cleans one JSON object from a connector result by decoding provider-marked base64 fields and then walking its children. This turns unreadable encoded blobs into either readable text or file references.

**Data flow**: It receives a mapping-like JSON object and a recursion depth. It first recursively translates child values, then looks for fields such as encoding: base64 plus content-like fields, decodes valid base64 values, chooses a filename and MIME type when possible, rewrites decoded values through _translated_bytes, and updates the marker to show whether content is now inline text or offloaded.

**Call relations**: _ConnectorCall.run starts result translation here for the broker response. _translated calls it for nested objects, and it calls _translated, _decoded_base64, and _translated_bytes as it walks and rewrites the result.

*Call graph*: calls 3 internal fn (_translated, _translated_bytes, _decoded_base64); called by 2 (_translated, run); 1 external calls (guess_type).


##### `_ConnectorCall._translated`  (lines 370–389)

```
async def _translated(self, value: object, depth: int) -> object
```

**Purpose**: Translates any value inside a connector result: objects, lists, and base64 data URLs. It is the general recursive walker used after a connector returns JSON.

**Data flow**: It receives a value and a depth count. If the result is nested too deeply, it leaves it alone; dictionaries go to _translated_node, lists are translated item by item, short data:...base64 strings go to _translated_data_url, and everything else is returned unchanged.

**Call relations**: _translated_node calls this for every child value. It routes nested dictionaries back to _translated_node and data URL strings to _translated_data_url, keeping the cleanup logic consistent throughout the response.

*Call graph*: calls 2 internal fn (_translated_data_url, _translated_node); called by 1 (_translated_node).


##### `_ConnectorCall._translated_data_url`  (lines 391–403)

```
async def _translated_data_url(self, value: str) -> object
```

**Purpose**: Decodes a string that is explicitly a base64 data URL, such as an inline image or file. If the string only looks like it starts with data: but is not valid, it leaves it unchanged.

**Data flow**: It receives one string. It matches the data URL pattern, decodes the base64 payload if valid, chooses a MIME type and fallback filename extension, then sends the bytes to _translated_bytes so the result becomes readable text or a workspace file reference.

**Call relations**: _translated calls this when it sees a short string beginning with the data URL prefix. It relies on _decoded_base64 for safe decoding and _translated_bytes for deciding whether to inline or offload the decoded content.

*Call graph*: calls 2 internal fn (_translated_bytes, _decoded_base64); called by 1 (_translated); 1 external calls (guess_extension).


##### `_ConnectorCall._translated_bytes`  (lines 405–414)

```
async def _translated_bytes(self, decoded: bytes, text: str | None, name: str, mimetype: str) -> object
```

**Purpose**: Decides what to do with decoded bytes from a connector result. Small UTF-8 text is kept directly in the JSON; binary or large content is written to a workspace file.

**Data flow**: It receives raw bytes, an optional decoded text version, a filename, and a MIME type. If the text exists and is under the inline size cap, it returns that text; otherwise it calls _offloaded and returns a file reference object.

**Call relations**: _translated_node and _translated_data_url call this after successfully decoding base64. When content cannot safely or usefully stay inline, it hands the bytes to _offloaded.

*Call graph*: calls 1 internal fn (_offloaded); called by 2 (_translated_data_url, _translated_node).


##### `_ConnectorCall._offloaded`  (lines 416–448)

```
async def _offloaded(self, name: str, mimetype: str, data: bytes) -> dict[str, object]
```

**Purpose**: Writes decoded result bytes into the workspace and returns a small object that points to the saved file. This keeps large or binary data out of the conversation text while still making it available.

**Data flow**: It receives a suggested name, MIME type, and bytes. It sanitizes the filename, builds a path based on a SHA-256 content hash, writes to a temporary part file through the sandbox, atomically moves it into place, and returns name, workspace path, MIME type, and byte count.

**Call relations**: _translated_bytes calls this whenever decoded content should not be inline. It uses the sandbox write path and a final shell move so repeated identical payloads land at the same stable path without readers seeing half-written files.

*Call graph*: called by 1 (_translated_bytes); 4 external calls (sha256, PurePosixPath, quote, uuid4).


##### `_ConnectorCall._deduped`  (lines 450–508)

```
def _deduped(self, payload: dict[str, object]) -> str
```

**Purpose**: Serializes a connector result and, when safe, replaces repeated large objects with pointers to the first copy. This can keep useful results small enough to stay in context instead of being pushed into a separate file.

**Data flow**: It receives the final payload dictionary. It serializes it once, skips deduplication if the payload is too large, too structurally dense, or already uses the pointer key, otherwise walks the payload with _condensed and returns JSON text for the condensed version.

**Call relations**: _ConnectorCall.run calls this through asyncio.to_thread after file fetching and base64 translation are done. It uses _escaped to build JSON Pointer paths and _condensed to find repeated structures.

*Call graph*: calls 2 internal fn (_condensed, _escaped); 1 external calls (dumps).


##### `_ConnectorCall._condensed`  (lines 510–586)

```
def _condensed(self, value: object, pointer: str, depth: int, first: dict[bytes, str]) -> tuple[object, bytes, int]
```

**Purpose**: Walks a JSON-like value and detects repeated dictionary objects by their structure and contents. Later matching objects can be replaced with a same_as pointer to the first occurrence.

**Data flow**: It receives a value, its JSON Pointer path, depth, and a table of first-seen object hashes. It recursively processes dictionaries and lists, computes stable hashes for each node, tracks the original size of dictionary nodes, records the first large object with each hash, and returns either the original-shaped value or a pointer object, plus its hash and size.

**Call relations**: _deduped calls this for each top-level payload item. During recursion it calls itself for children, uses _escaped for pointer path pieces, and feeds results back upward so whole repeated objects can collapse even if their children were also examined.

*Call graph*: calls 1 internal fn (_escaped); called by 1 (_deduped); 1 external calls (sha256).


##### `_escaped`  (lines 589–592)

```
def _escaped(token: str) -> str
```

**Purpose**: Escapes one piece of a JSON Pointer path so special characters in keys do not change what the pointer means. JSON Pointer is a standard way to name a location inside a JSON document.

**Data flow**: It receives a dictionary key as text. It replaces ~ and / with their JSON Pointer escape forms and returns the safe token.

**Call relations**: _deduped and _condensed use this when building same_as paths. It makes sure a provider key containing a slash is treated as a key name, not as a path separator.

*Call graph*: called by 2 (_condensed, _deduped).


##### `_decoded_base64`  (lines 595–619)

```
def _decoded_base64(value: object) -> tuple[bytes, str | None] | None
```

**Purpose**: Safely decodes a value that a provider claimed is base64. It refuses invalid, non-string, or too-large values instead of guessing and corrupting data.

**Data flow**: It receives any value. If it is a reasonably sized string, it removes whitespace, strictly base64-decodes it, then tries to decode the bytes as UTF-8 text; it returns bytes plus text when possible, bytes plus None for binary data, or None when decoding should not happen.

**Call relations**: _translated_node uses this for marked content fields, and _translated_data_url uses it for data URL payloads. The callers then decide whether to inline the decoded text or save the bytes as a workspace file.

*Call graph*: called by 2 (_translated_data_url, _translated_node); 1 external calls (b64decode).


##### `search_connector_tools`  (lines 622–633)

```
async def search_connector_tools(ctx: ToolContext, args: SearchConnectorToolsInput) -> ToolResult
```

**Purpose**: Performs a richer search for tools inside one connector based on a natural-language goal. It can return matching tool schemas plus broker-provided advice about how to use them.

**Data flow**: It receives a connector source ID and a query. It looks up the connector, asks the broker’s search API for matching tools and guidance, converts each tool to plain JSON, and returns connector ID, tools, plan, guidance, and pitfalls as JSON.

**Call relations**: This is the handler for search_connector_tools. It uses _registry to find the connector entry, _tool_json to format returned tools, and _json_result to wrap the final answer as tool output.

*Call graph*: calls 3 internal fn (_json_result, _registry, _tool_json).


##### `_registry`  (lines 636–639)

```
def _registry(ctx: ToolContext) -> ConnectorRegistry
```

**Purpose**: Retrieves the connector registry from the current tool context. It fails clearly if connector tools were invoked without the registry they need.

**Data flow**: It receives a ToolContext. If the context has a connector registry, it returns it; otherwise it raises an error explaining that connector dispatch is missing its registry.

**Call relations**: The public connector handlers list_external_tools, describe_external_tools, call_external_tool, and search_connector_tools all call this first or near the start. It is the common gate that connects tool requests to the live set of available brokers.

*Call graph*: called by 4 (call_external_tool, describe_external_tools, list_external_tools, search_connector_tools).


##### `_tool_json`  (lines 642–643)

```
def _tool_json(tool: BrokerTool) -> dict[str, object]
```

**Purpose**: Turns a broker tool object into the small JSON shape returned to the agent. It keeps the important pieces: slug, description, and input schema.

**Data flow**: It receives a BrokerTool. It reads its slug, description, and input schema and returns them in a plain dictionary.

**Call relations**: describe_external_tools uses this when returning exact schemas, and search_connector_tools uses it for search results. It keeps the public output format consistent between both discovery paths.

*Call graph*: called by 2 (describe_external_tools, search_connector_tools).


##### `_discovery_query`  (lines 646–653)

```
def _discovery_query(explicit: str, unresolved: list[str]) -> str
```

**Purpose**: Builds a useful fallback search phrase when exact tool names were not found. This helps turn a guessed slug into ordinary words that can discover the real tool name.

**Data flow**: It receives an explicit query and a list of unresolved tool names. If the explicit query is present, it returns that; otherwise it lowercases the unresolved names, replaces non-alphanumeric runs with spaces, removes duplicate words while keeping order, and returns the joined phrase.

**Call relations**: describe_external_tools calls this when it needs to ask the broker for available tools after unresolved exact names or missing tool_names. It uses the result as the broker catalog discovery query.

*Call graph*: called by 1 (describe_external_tools); 1 external calls (sub).


##### `_json_result`  (lines 656–657)

```
def _json_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: Wraps a dictionary as the standard text-based tool result used by this file. It is a small helper that keeps all discovery responses formatted the same way.

**Data flow**: It receives a payload dictionary. It JSON-serializes the dictionary, puts that text into a TextContent object, then returns a ToolResult containing it.

**Call relations**: list_external_tools, describe_external_tools, and search_connector_tools call this at the end of their work. call_external_tool builds its ToolResult directly because _ConnectorCall.run already returns serialized text.

*Call graph*: called by 3 (describe_external_tools, list_external_tools, search_connector_tools); 3 external calls (__init__, __init__, dumps).


### Composio broker integration
The Composio package resolves live toolkits, brokers tool execution, and proxies provider requests without exposing raw service tokens.

### `extensions/composio/ufo_ext_composio/__init__.py`

`other` · `import time`

This file has no code of its own. Its job is to tell Python that the surrounding folder should be treated as an importable package. Think of it like a label on a folder: the label does not contain the documents, but it lets people and tools refer to the folder by name.

In this project, the folder name suggests this package contains a Composio extension for UFO. Without this `__init__.py` file, some Python environments or packaging tools might not recognize the directory as a proper package, which could make imports fail or make the extension harder to discover. Since the file is empty, it does not run setup logic, define public shortcuts, or expose helper functions. It simply enables the package structure.


### `extensions/composio/ufo_ext_composio/resolver.py`

`orchestration` · `connector discovery and connection setup`

Composio offers many outside services, called toolkits, and this project does not want to hard-code every possible one. This file is the bridge for that “open namespace”: if a user asks for a toolkit by its slug, such as a short machine-friendly name, the resolver asks Composio whether that toolkit can really be connected. If it can, the resolver creates the small pieces the rest of the connector system expects.

The main class, ComposioResolver, is deliberately simple. It keeps only a shared broker, which is the object that later runs the actual Composio-backed connector work. For each request, it reads the Composio client fresh, so tests and configuration changes can swap the underlying transport without stale connections hanging around.

The resolver also exposes Composio’s approved file-transfer hosts. That matters because tools may need to move files in and out of the sandbox, and only known hosts should be allowed. For discovery, it can search Composio’s catalog and return friendly catalog entries. For connection, it turns a validated provider slug into an OAuth-style provider description, but with no direct provider host, because the user’s account token stays with Composio and tool execution happens through Composio.

#### Function details

##### `ComposioResolver.transfer_hosts`  (lines 32–33)

```
def transfer_hosts(self) -> tuple[str, ...]
```

**Purpose**: This property tells the rest of the system which Composio file-storage hosts are allowed for file transfer. It is a safety boundary, so brokered tools can pass files through approved Composio locations rather than arbitrary internet hosts.

**Data flow**: It takes no extra input beyond the resolver instance. It reads the fixed list of Composio transfer host names from the client module and returns them as a tuple, without changing anything.

**Call relations**: When the connector system needs to know what remote hosts a Composio-backed grant may use for file movement, it asks this property. The property does not call other project code; it simply hands back the shared allow-list used by the Composio extension.


##### `ComposioResolver.claims`  (lines 35–36)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: This async method answers the question, “Can this Composio resolver take responsibility for the requested provider slug?” It checks the slug against Composio’s live toolkit catalog so the system does not offer a connector that Composio cannot actually broker.

**Data flow**: It receives a provider name as text. It gets a Composio client, asks whether that provider is a connectable toolkit, and turns the answer into true or false: true when Composio returns a matching toolkit, false when it returns nothing.

**Call relations**: During connector lookup, the wider registry or connection flow can ask this resolver whether it claims a provider that no explicitly registered connector handled first. To answer, it calls the Composio client factory and then relies on that client’s catalog check.

*Call graph*: 1 external calls (composio_client).


##### `ComposioResolver.descriptor`  (lines 38–39)

```
def descriptor(self, provider: str) -> OAuthProvider
```

**Purpose**: This method builds the connection description for a validated Composio toolkit. The description looks like an OAuth provider record, meaning a standard description for an account-authorization flow, but Composio remains the place where the real account token is held.

**Data flow**: It receives the provider slug. It creates and returns a ComposioOAuthProvider using that slug and an empty host, because the connection is not made directly to the outside service’s own website from this system.

**Call relations**: After a provider slug has been accepted, the connection setup code can ask for this descriptor so it knows what kind of authorization record to use. This method hands off to ComposioOAuthProvider to create that record.

*Call graph*: 1 external calls (__init__).


##### `ComposioResolver.entry`  (lines 41–44)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: This method creates the connector entry that routes a Composio toolkit slug to the shared Composio broker. It also turns the slug into a readable label, so a name like “google_drive” can be shown as “Google Drive.”

**Data flow**: It receives a provider slug. It builds a display label by replacing underscores with spaces and title-casing the words, then returns a ConnectorEntry containing the provider slug, that label, and this resolver’s broker.

**Call relations**: When the connector registry needs a concrete entry for a provider this resolver accepts, it calls this method. The method packages the slug together with the shared broker by constructing a ConnectorEntry, so later tool execution can go through the common Composio broker path.

*Call graph*: 1 external calls (__init__).


##### `ComposioResolver.catalog`  (lines 46–48)

```
async def catalog(self, query: str, limit: int=TOOL_SEARCH_LIMIT) -> tuple[CatalogEntry, ...]
```

**Purpose**: This async method searches Composio’s toolkit catalog and returns choices the system can show during discovery. It helps users find services without the project needing a built-in list of every Composio toolkit.

**Data flow**: It receives a search query and an optional maximum number of results. It asks a fresh Composio client for matching toolkits, then turns each returned slug-and-label pair into a CatalogEntry and returns all of them as an immutable tuple.

**Call relations**: When a discovery tool or catalog view needs Composio-backed connector suggestions, it calls this method. The method gets results from the Composio client and wraps each one in the connector catalog format expected by the rest of the system.

*Call graph*: 2 external calls (__init__, composio_client).


### `extensions/composio/ufo_ext_composio/broker.py`

`io_transport` · `request handling and connector tool use`

This file defines ComposioBroker, the shared adapter used whenever a Composio-backed connector is involved. Think of it like a front desk for an office building: the rest of UFO asks the front desk for available rooms, directions, access checks, or delivery slots, and the front desk talks to the building systems behind the scenes.

The broker can discover tools for a provider, fetch a tool's input schema, run a tool, search for matching tools, prepare file uploads, and extract file links from tool results. It also creates a Credential object that does not expose a real provider token. Instead, requests are routed through Composio's proxy, so downstream code can call provider APIs without holding secrets.

A few safety details matter. Each method asks for the current Composio client at call time, rather than storing one forever. That keeps tests and transport overrides honest and avoids stale connections. When a tool slug is wrong, the broker tries to improve the error by listing real available tool slugs. When an account is missing or stale, it tells the user to reconnect instead of pretending the problem is a missing tool. For file inputs, it stages uploads in Composio's file store; for file outputs, it walks through nested responses and finds Composio's presigned file URLs.

#### Function details

##### `ComposioBroker.tools`  (lines 48–50)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Lists tools available for a provider, optionally filtered by a search query. It returns them in UFO's common BrokerTool shape so the rest of the system does not need to understand Composio's raw response format.

**Data flow**: It receives a workspace ID, provider name, and query text. It asks the current Composio client for matching tools, then passes the raw listing through _discovered_tools to keep only usable slugs and short descriptions. The result is a tuple of BrokerTool objects.

**Call relations**: When connector discovery needs to show the model or user what Composio tools exist, this method is the entry point. It calls composio_client to get the active client and hands the returned catalog to _discovered_tools for cleanup.

*Call graph*: calls 1 internal fn (_discovered_tools); 1 external calls (composio_client).


##### `ComposioBroker.schema`  (lines 52–65)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Fetches the input schema for one specific Composio tool. This tells UFO what arguments the tool expects, including special rewriting for file-upload fields.

**Data flow**: It receives a workspace ID, provider name, and tool slug. It asks Composio for that tool's schema, turns Composio file-upload parameters into UFO's workspace-file vocabulary, and returns a BrokerTool with the slug, description, and input schema. If Composio says the slug does not exist, it raises UnknownBrokerTool.

**Call relations**: This is used after a tool has been selected and UFO needs the exact shape of its inputs. It relies on composio_client for the request and workspace_file_schema for translating Composio's file-input format into the connector system's expected format.

*Call graph*: 4 external calls (__init__, __init__, composio_client, workspace_file_schema).


##### `ComposioBroker.execute`  (lines 67–90)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Runs a Composio tool for this workspace and connected account. It also turns common Composio failures into more helpful errors, especially wrong tool slugs and stale connected accounts.

**Data flow**: It receives the workspace ID, provider, tool slug, argument values, connected account ID, and optional idempotency key. It builds the broker-user ID from the workspace, sends the tool execution request to Composio, and returns the response dictionary. If the account looks stale, it raises a reconnect-guidance error; if the slug is missing, it asks _slug_miss to enrich the error with available tool names.

**Call relations**: This is the central run path for Composio tools. It calls the current Composio client to perform the remote execution, uses _stale_account to recognize dead grants, _reconnect_error to explain reconnection, and _slug_miss to help recover from an invalid tool name.

*Call graph*: calls 3 internal fn (_slug_miss, _reconnect_error, _stale_account); 1 external calls (composio_client).


##### `ComposioBroker.file_outputs`  (lines 92–97)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Finds files produced by a tool execution response. Composio can place file objects deep inside nested data, so this method gives callers one clean list of downloadable files.

**Data flow**: It receives the response dictionary from a tool run. It creates an empty list, asks _collect_files to walk through the whole response, and returns the found file names and URLs as BrokerFile objects in a tuple.

**Call relations**: After execute returns, code that needs to surface or transfer output files calls this method. It delegates the recursive search to _collect_files so the public method stays simple.

*Call graph*: calls 1 internal fn (_collect_files).


##### `ComposioBroker.stage_upload`  (lines 99–115)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Creates a temporary upload slot for a file that will be passed into a Composio tool. This lets the system upload file bytes first, then pass a small file reference as a tool argument.

**Data flow**: It receives the workspace ID, provider, tool slug, filename, MIME type, and MD5 checksum. It asks Composio to create an upload destination, then returns a StagedUpload containing the PUT URL, content type, and the argument object that should later be sent to the tool.

**Call relations**: This is used before running tools that accept file inputs. It calls the current Composio client to reserve storage, then packages the returned upload key into the connector system's StagedUpload format.

*Call graph*: 2 external calls (__init__, composio_client).


##### `ComposioBroker.search`  (lines 117–120)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Searches Composio connector tools through Composio's tool-router search feature. It is used when the system needs a more semantic or routed search than a simple tool listing.

**Data flow**: It receives a workspace ID, provider name, and query. It gets the active Composio client and passes everything to search_connector_tools. The result is returned as a BrokerSearch object.

**Call relations**: This method sits on the discovery/search path. Instead of doing its own filtering, it hands the request to Composio's search_connector_tools helper, using the same per-call client lookup as the rest of the broker.

*Call graph*: 2 external calls (composio_client, search_connector_tools).


##### `ComposioBroker.credential`  (lines 122–138)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Creates a safe Credential for making provider HTTP calls through Composio's proxy. It first checks that the requested connected account belongs to this workspace's broker user, which prevents using someone else's account by mistake.

**Data flow**: It receives a workspace ID, provider, and connected account ID. It builds the broker-user ID, asks Composio to confirm that account is connected for that user and provider, and then returns a Credential whose transport routes requests through ComposioProxyTransport. If the account is missing, it raises a reconnect-guidance error instead of returning a credential.

**Call relations**: This is used when connector code needs provider API access without receiving raw secrets. It calls composio_client for account verification, uses _reconnect_error for missing accounts, and builds a Credential around ComposioProxyTransport, falling back to an httpx AsyncHTTPTransport when the client has no custom inner transport.

*Call graph*: calls 1 internal fn (_reconnect_error); 4 external calls (__init__, __init__, AsyncHTTPTransport, composio_client).


##### `ComposioBroker._slug_miss`  (lines 140–161)

```
async def _slug_miss(self, client: composio.ComposioClient, provider: str, slug: str, error: composio.ComposioError) -> composio.ComposioError
```

**Purpose**: Improves a 'tool not found' error by adding real tool slugs that are available for the provider. This helps the next attempt use a valid tool name instead of leaving the caller with a bare 404.

**Data flow**: It receives the Composio client, provider, missing slug, and original error. It turns the failed slug into a simple search query, asks Composio for matching tools, and, if needed, falls back to listing tools without a query. If tools are found, it returns a new ComposioError whose message includes available slugs; otherwise it returns the original error.

**Call relations**: ComposioBroker.execute calls this only after Composio reports a missing tool slug. This helper calls the client's list_tools method and _discovered_tools to turn the catalog into readable suggestions, but it is deliberately best-effort: if discovery fails, execute keeps the original error.

*Call graph*: calls 2 internal fn (_discovered_tools, list_tools); called by 1 (execute); 2 external calls (sub, ComposioError).


##### `_collect_files`  (lines 164–173)

```
def _collect_files(value: object, found: list[BrokerFile]) -> None
```

**Purpose**: Walks through any nested response data and collects Composio file objects. A file object is recognized by having a non-empty s3url plus name and mimetype fields.

**Data flow**: It receives any value and a list that is being filled. If the value looks like a Composio file object, it appends a BrokerFile with the name and URL. If the value is a dictionary or list, it recursively checks each child item. It returns nothing, but it mutates the found list.

**Call relations**: ComposioBroker.file_outputs starts the file search by calling this helper. The helper does the deep walk so file_outputs can return a simple tuple of all discovered files.

*Call graph*: called by 1 (file_outputs); 1 external calls (__init__).


##### `_stale_account`  (lines 176–187)

```
def _stale_account(error: composio.ComposioError, account_id: str) -> bool
```

**Purpose**: Decides whether a Composio execution error probably means the connected account is gone or no longer belongs to this broker. This matters because the right fix is reconnecting the account, not trying different tool slugs.

**Data flow**: It receives a ComposioError and the connected account ID that was used. It lowercases the error body and checks for narrow signs of a missing connected account, either Composio's own 'connected account not found' wording or the account ID itself with 'not found'. It returns true only for those stale-account patterns.

**Call relations**: ComposioBroker.execute calls this when Composio rejects a tool run. If it returns true, execute routes the error through _reconnect_error; otherwise execute continues with the normal error handling path, including possible slug-miss handling.

*Call graph*: called by 1 (execute).


##### `_reconnect_error`  (lines 190–191)

```
def _reconnect_error(error: composio.ComposioError, provider: str) -> composio.ComposioError
```

**Purpose**: Builds a clearer Composio error that tells the agent or user to reconnect the provider account. It preserves the original status code and message, but adds provider-specific stale-grant guidance.

**Data flow**: It receives the original ComposioError and provider name. It calls stale_grant_guidance to get a human-facing reconnection hint, appends that hint to the original error body, and returns a new ComposioError.

**Call relations**: ComposioBroker.execute uses this when a tool run points to a stale account, and ComposioBroker.credential uses it when account verification fails with not found. It is the shared place that turns account-ownership failures into reconnect instructions.

*Call graph*: called by 2 (credential, execute); 2 external calls (stale_grant_guidance, ComposioError).


##### `_discovered_tools`  (lines 194–216)

```
def _discovered_tools(listed: dict[str, object]) -> tuple[BrokerTool, ...]
```

**Purpose**: Converts Composio's raw list_tools response into UFO's BrokerTool objects. It filters out malformed entries and keeps descriptions short so discovery output stays readable.

**Data flow**: It receives a dictionary from Composio. It looks for an items list, reads each item's slug or name, skips entries without a usable slug, trims long descriptions to the discovery cap, and returns a tuple of BrokerTool objects. If the response shape is not usable, it returns an empty tuple.

**Call relations**: ComposioBroker.tools uses this for normal tool discovery, and ComposioBroker._slug_miss uses it when building suggestions after a missing-slug error. It is the small translator between Composio's catalog format and the connector system's common tool format.

*Call graph*: called by 2 (_slug_miss, tools); 1 external calls (__init__).


### `extensions/composio/ufo_ext_composio/client.py`

`io_transport` · `connector OAuth, tool discovery, tool execution, and file staging during request handling`

Composio is used here like a secure switchboard for third-party services. Instead of this project building and storing a separate integration for every app, Composio keeps the user’s service tokens and offers one API for connecting accounts, searching tools, and running those tools. This file is the code that talks to that API.

The file defines a small allow/deny layer for toolkits, meaning app integrations. A toolkit is only offered if Composio can manage its login, it has real tools, and it is not on a hand-written banned list of integrations that look available but are not useful or safe enough for this system.

The main class, ComposioClient, wraps the Composio REST API over HTTP. It creates login links, checks that a completed account belongs to the expected workspace user and expected toolkit, lists tool schemas, runs tools, and asks Composio where files should be uploaded. It also opens Tool Router sessions for smarter, meaning semantic, tool search.

A key safety idea runs through the file: the real OAuth token stays inside Composio. This project stores only a connected account id. The file also validates response shapes and raises ComposioError loudly when Composio returns an error or a response that cannot be trusted.

#### Function details

##### `connectable`  (lines 120–144)

```
def connectable(slug: str, toolkit: Mapping[str, object]) -> bool
```

**Purpose**: Decides whether a Composio toolkit should be offered to users through this deployment. It blocks known-bad toolkits and rejects catalog entries that cannot create a managed login or have no usable tools.

**Data flow**: It receives a toolkit slug, which is the toolkit’s short name, and a catalog record from Composio. It checks the local banned list, then reads whether Composio has managed authentication schemes and a positive tool count. It returns true only when all those checks pass.

**Call relations**: ComposioClient.connectable_toolkit uses this when checking one requested toolkit, and ComposioClient.list_toolkits uses it while showing search results. It acts as the gatekeeper before the rest of the connector flow can claim that a toolkit is usable.

*Call graph*: called by 2 (connectable_toolkit, list_toolkits).


##### `ComposioError.__init__`  (lines 151–154)

```
def __init__(self, status: int, body: str) -> None
```

**Purpose**: Creates a clear exception when Composio fails or returns data this client cannot safely use. The exception keeps both the status code and response text so callers can tell what went wrong.

**Data flow**: It receives an HTTP-like status number and a body string. It builds a readable error message, stores the status and body on the exception, and then the exception can be raised by higher-level code.

**Call relations**: The client methods call this whenever a required field is missing, ownership checks fail, or HTTP responses are bad. It is the common failure language used by connect_link, connected_account, upload creation, Tool Router setup, auth config creation, and response parsing.

*Call graph*: called by 6 (_auth_config, connect_link, connected_account, create_upload, tool_router_session, _body).


##### `ComposioClient.connect_link`  (lines 171–180)

```
async def connect_link(self, toolkit: str, user_id: str, callback_url: str) -> str
```

**Purpose**: Creates the web link a user opens to approve access to a third-party service through Composio. This starts the OAuth consent flow, where OAuth means the standard web login-and-permission handoff used by many apps.

**Data flow**: It receives a toolkit name, a broker user id, and a callback URL. It first finds or creates an authentication configuration, then posts those details to Composio. It returns the redirect URL that the user should visit, or raises an error if Composio does not provide one.

**Call relations**: This is used at the start of a connector authorization flow. It depends on _auth_config to choose the login setup, then uses _post to ask Composio for the link, and ComposioError if the answer is unusable.

*Call graph*: calls 3 internal fn (_auth_config, _post, __init__).


##### `ComposioClient.connected_account`  (lines 182–206)

```
async def connected_account(self, account_id: str, expected_user_id: str, expected_toolkit: str) -> OAuthAccount
```

**Purpose**: Confirms that a Composio connected account is valid for the workspace and toolkit the system expected. This prevents one user’s account id, or an account for the wrong service, from being accepted accidentally or maliciously.

**Data flow**: It receives an account id, the expected user id, and the expected toolkit slug. It fetches the account from Composio, checks the owner, checks that the account is active, and checks that it belongs to the requested toolkit. If everything matches, it returns an OAuthAccount containing the safe account id.

**Call relations**: This is used after OAuth completes, when the system needs to turn Composio’s account id into a grant it can store. It uses _get for the lookup and raises ComposioError when the account is foreign, inactive, or for the wrong toolkit.

*Call graph*: calls 2 internal fn (_get, __init__); 1 external calls (__init__).


##### `ComposioClient.list_tools`  (lines 208–214)

```
async def list_tools(self, toolkit: str, query: str='', limit: int=TOOL_SEARCH_LIMIT) -> dict[str, object]
```

**Purpose**: Asks Composio for tools available in one toolkit, optionally narrowed by a search query. It gives the broker a catalog to inspect when matching tool names or discovering what can be run.

**Data flow**: It receives a toolkit slug, optional search text, and a result limit. It builds URL query parameters and sends a GET request to Composio’s tools endpoint. It returns Composio’s response as a dictionary.

**Call relations**: ComposioBroker._slug_miss calls this when it needs to look up tools for a connector. Internally, it delegates the HTTP work and response parsing to _get.

*Call graph*: calls 1 internal fn (_get); called by 1 (_slug_miss).


##### `ComposioClient.tool_schema`  (lines 216–217)

```
async def tool_schema(self, slug: str) -> dict[str, object]
```

**Purpose**: Fetches the detailed schema for a single Composio tool. A schema describes what inputs the tool accepts, so the rest of the system can explain or validate tool calls.

**Data flow**: It receives a tool slug. It requests that tool’s detail endpoint and returns the parsed response dictionary from Composio.

**Call relations**: This is a direct catalog lookup helper. It relies on _get to make the HTTP request and to turn the response into a usable Python dictionary.

*Call graph*: calls 1 internal fn (_get).


##### `ComposioClient.connectable_toolkit`  (lines 219–236)

```
async def connectable_toolkit(self, slug: str) -> str | None
```

**Purpose**: Checks whether a user-supplied toolkit slug is safe and usable, and returns its display name if it is. It also prevents unsafe path-like input from being placed into a Composio URL.

**Data flow**: It receives a slug string. It first checks that the slug contains only normal toolkit-name characters, then fetches the toolkit record from Composio. If the toolkit is missing, banned, unauthenticatable, or empty, it returns None; otherwise it returns the toolkit’s name or the slug as a fallback.

**Call relations**: This is used by resolver-style code that decides whether this Composio extension can claim a requested connector. It calls _get for the catalog detail and connectable for the policy decision.

*Call graph*: calls 2 internal fn (_get, connectable).


##### `ComposioClient.list_toolkits`  (lines 238–256)

```
async def list_toolkits(self, query: str, limit: int) -> tuple[tuple[str, str], ...]
```

**Purpose**: Searches Composio’s catalog for toolkits that this deployment is willing to offer. It is what powers discovery of the open-ended set of Composio integrations.

**Data flow**: It receives search text and a maximum number of results. It asks Composio for matching toolkits, walks through the returned items, filters out malformed or non-connectable entries, and returns pairs of slug and user-facing label.

**Call relations**: This function supports discovery screens or discovery tools. It uses _get to fetch catalog results and connectable to apply the local safety and usefulness rules before anything is shown.

*Call graph*: calls 2 internal fn (_get, connectable).


##### `ComposioClient.execute_tool`  (lines 258–272)

```
async def execute_tool(self, slug: str, arguments: Mapping[str, object], user_id: str, connected_account_id: str | None=None, idempotency_key: str | None=None) -> dict[str, object]
```

**Purpose**: Runs a Composio tool on Composio’s server side for a broker user and, when supplied, a specific connected account. This is the point where an integration action actually happens.

**Data flow**: It receives a tool slug, argument values, a user id, and optional connected account and idempotency key. It builds the request body, refuses to send it if it is larger than the configured limit, adds the idempotency header if present, and posts the request to Composio. It returns the parsed execution result.

**Call relations**: Higher-level broker code uses this when an agent has chosen a concrete tool to run. It uses json.dumps to measure payload size and _post to call Composio’s execute endpoint.

*Call graph*: calls 1 internal fn (_post); 1 external calls (dumps).


##### `ComposioClient.create_upload`  (lines 274–298)

```
async def create_upload(self, toolkit: str, slug: str, filename: str, mimetype: str, md5: str) -> 'ComposioUpload'
```

**Purpose**: Asks Composio to reserve a place for a file that will be passed into a tool. The actual file bytes are uploaded by the sandbox to a presigned URL, while this client only obtains the instructions.

**Data flow**: It receives the toolkit, tool slug, filename, MIME type, and MD5 checksum. It posts those details to Composio’s upload-request endpoint. It returns a ComposioUpload containing the storage key and, if needed, the URL where bytes should be PUT; it raises an error if key or URL data is malformed.

**Call relations**: This is used before executing tools that need files. It calls _post for the upload slot request and creates a ComposioUpload object that later code can use to stage the file.

*Call graph*: calls 2 internal fn (_post, __init__); 1 external calls (__init__).


##### `ComposioClient.tool_router_session`  (lines 300–311)

```
async def tool_router_session(self, user_id: str, toolkits: list[str]) -> ToolRouterSession
```

**Purpose**: Opens a Composio Tool Router session for semantic tool search. A Tool Router session is a temporary search endpoint that can recommend relevant tools and plans for a toolkit.

**Data flow**: It receives a broker user id and a list of toolkit slugs. It posts a session request to Composio, reads the returned session id and MCP URL, and returns them as a ToolRouterSession. If either part is missing, it raises a ComposioError.

**Call relations**: search_connector_tools calls this when there is no cached search session for a user and connector. It uses _post to create the session and then hands the URL to later MCP tool-search calls.

*Call graph*: calls 2 internal fn (_post, __init__); called by 1 (search_connector_tools); 1 external calls (__init__).


##### `ComposioClient._auth_config`  (lines 313–331)

```
async def _auth_config(self, toolkit: str) -> str
```

**Purpose**: Finds the authentication configuration that should be used for a toolkit, or creates a Composio-managed one if none exists. This lets operator-created custom configs take priority while still working out of the box.

**Data flow**: It receives a toolkit slug. It asks Composio for existing auth configs for that toolkit, extracts the first id if present, and returns it. If none exists, it posts a request to create a managed auth config and returns the new id, or raises an error if the id is absent.

**Call relations**: connect_link calls this before creating an OAuth link. It uses _get to search, _auth_config_id to extract an existing id, _post to create a missing one, and ComposioError if Composio’s response cannot be used.

*Call graph*: calls 4 internal fn (_get, _post, __init__, _auth_config_id); called by 1 (connect_link).


##### `ComposioClient._get`  (lines 333–335)

```
async def _get(self, path: str, params: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Performs a GET request to Composio and returns the response body as a dictionary. It centralizes the common pattern of opening an HTTP client, sending a request, and parsing the answer.

**Data flow**: It receives a path and optional query parameters. It opens a short-lived HTTP client configured for Composio, sends the GET request, passes the response to _body, and returns the parsed dictionary.

**Call relations**: Most read-only client methods call this, including auth config lookup, account verification, toolkit checks, toolkit search, tool listing, and schema lookup. It gets the configured HTTP client from _http and delegates response validation to _body.

*Call graph*: calls 2 internal fn (_http, _body); called by 6 (_auth_config, connectable_toolkit, connected_account, list_toolkits, list_tools, tool_schema).


##### `ComposioClient._post`  (lines 337–341)

```
async def _post(self, path: str, body: dict[str, object], headers: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Performs a POST request to Composio with a JSON body and optional headers. It is the shared helper for actions that create links, sessions, uploads, auth configs, or tool executions.

**Data flow**: It receives a path, a body dictionary, and optional headers. It opens a configured HTTP client, sends the POST request as JSON, passes the response to _body, and returns the parsed dictionary.

**Call relations**: Action-oriented methods call this, including connect_link, _auth_config, execute_tool, create_upload, and tool_router_session. It uses _http for the transport setup and _body for error handling and response shape checks.

*Call graph*: calls 2 internal fn (_http, _body); called by 5 (_auth_config, connect_link, create_upload, execute_tool, tool_router_session).


##### `ComposioClient._http`  (lines 343–349)

```
def _http(self) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to Composio. It applies the base API URL, API key header, timeout, and optional test transport in one place.

**Data flow**: It reads the client’s API key and optional transport. It creates and returns an httpx.AsyncClient configured for Composio requests.

**Call relations**: _get and _post call this every time they need to make a request. Keeping this setup here ensures all Composio calls use the same address, authentication header, timeout, and test override behavior.

*Call graph*: called by 2 (_get, _post); 1 external calls (AsyncClient).


##### `_body`  (lines 352–360)

```
def _body(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Turns a raw HTTP response from Composio into a usable dictionary, or raises a clear error. It prevents the rest of the system from quietly accepting failed or strangely shaped responses.

**Data flow**: It receives an HTTP response. If the status is an error, it raises ComposioError with the status and text. If the body is empty, it returns an empty dictionary. Otherwise it parses JSON and returns it only if it is an object-like dictionary.

**Call relations**: _get and _post call this after every Composio HTTP request. It uses ComposioError as the shared failure type and httpx’s JSON parsing for valid response bodies.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_get, _post); 1 external calls (json).


##### `workspace_file_schema`  (lines 363–389)

```
def workspace_file_schema(value: object) -> object
```

**Purpose**: Rewrites Composio tool input schemas so file inputs use this project’s workspace-file language. Instead of exposing Composio’s storage details to the model, it asks for an absolute path inside /workspace.

**Data flow**: It receives any schema-like value. If it finds a dictionary marked as file-uploadable, it replaces that part with a small object requiring a workspace file path. For nested dictionaries and lists, it walks through them recursively and rewrites file-uploadable parts; all other values pass through unchanged.

**Call relations**: _search_result calls this when turning Tool Router search results into BrokerTool objects. It ensures discovered tools describe file inputs in the same way the sandbox can later stage them.

*Call graph*: called by 1 (_search_result).


##### `_auth_config_id`  (lines 392–399)

```
def _auth_config_id(payload: dict[str, object]) -> str | None
```

**Purpose**: Extracts the first authentication configuration id from a Composio list response. It is a small helper that keeps _auth_config focused on the larger find-or-create flow.

**Data flow**: It receives a parsed response dictionary. It looks for an items list, scans for the first dictionary item with a string id, and returns that id. If the shape is not right or no id exists, it returns None.

**Call relations**: _auth_config calls this after asking Composio for existing auth configs. A returned id lets _auth_config reuse an existing setup; None tells it to create a new managed config.

*Call graph*: called by 1 (_auth_config).


##### `composio_client`  (lines 402–409)

```
def composio_client() -> ComposioClient
```

**Purpose**: Creates the deployment’s default ComposioClient using the API key from the environment. It fails early if the key is missing because connector authorization cannot work without it.

**Data flow**: It reads COMPOSIO_API_KEY from environment variables. If the value is missing or empty, it raises RuntimeError. Otherwise it returns a ComposioClient initialized with that key.

**Call relations**: Other parts of the extension call this when they need a real Composio client. It is the bridge between deployment configuration and the client methods in this file.

*Call graph*: 1 external calls (__init__).


##### `_dict`  (lines 416–417)

```
def _dict(value: object) -> dict[str, object]
```

**Purpose**: Safely treats a value as a dictionary only when it really is one. This avoids repeated type checks while parsing flexible Composio search results.

**Data flow**: It receives any value. If the value is a dictionary, it returns it unchanged; otherwise it returns an empty dictionary.

**Call relations**: _search_result uses this throughout its parsing. It keeps malformed or missing nested data from causing crashes while still producing a sensible empty result where possible.

*Call graph*: called by 1 (_search_result).


##### `_str_tuple`  (lines 420–423)

```
def _str_tuple(value: object) -> tuple[str, ...]
```

**Purpose**: Safely extracts non-empty strings from a list and returns them as an immutable tuple. It filters out missing or wrongly typed values from Composio search result fields.

**Data flow**: It receives any value. If the value is not a list, it returns an empty tuple. If it is a list, it keeps only items that are non-empty strings and returns them as a tuple.

**Call relations**: _search_result uses this for tool slugs, plan steps, guidance, and pitfalls. It lets that parser accept partial search responses without trusting every field blindly.

*Call graph*: called by 1 (_search_result).


##### `_search_result`  (lines 426–460)

```
def _search_result(result: dict[str, object]) -> BrokerSearch
```

**Purpose**: Converts a raw Tool Router search answer into the project’s BrokerSearch format. That format contains matched tools plus practical advice like plan steps, guidance, and pitfalls.

**Data flow**: It receives a result dictionary from the Tool Router. It finds the inner data, reads tool schemas and result items, collects primary and related tool slugs without duplicates, rewrites file-upload schemas, and builds BrokerTool objects. It also gathers recommended plan steps, execution guidance, and known pitfalls, then returns one BrokerSearch object.

**Call relations**: search_connector_tools calls this after receiving the MCP search response. It relies on _dict and _str_tuple for safe parsing and workspace_file_schema to make file inputs understandable to the rest of the system.

*Call graph*: calls 3 internal fn (_dict, _str_tuple, workspace_file_schema); called by 1 (search_connector_tools); 2 external calls (__init__, __init__).


##### `search_connector_tools`  (lines 463–486)

```
async def search_connector_tools(client: ComposioClient, workspace_id: UUID, connector: str, query: str) -> BrokerSearch
```

**Purpose**: Runs semantic search for tools inside one connector. Instead of only matching exact names, it asks Composio’s Tool Router which tools fit the user’s use case and returns those tools with guidance.

**Data flow**: It receives a ComposioClient, workspace id, connector slug, and search query. It turns the workspace id into a Composio broker user id, reuses or creates a cached Tool Router session for that user and connector, calls the router’s COMPOSIO_SEARCH_TOOLS tool over MCP, and converts the raw answer into BrokerSearch.

**Call relations**: Higher-level dynamic connector search code calls this when an agent needs to discover which Composio tool to use. It calls ComposioClient.tool_router_session when no cached session exists, mcp_session.mcp_call_tool to ask the router, and _search_result to shape the answer for the broker.

*Call graph*: calls 2 internal fn (tool_router_session, _search_result); 1 external calls (mcp_call_tool).


### `extensions/composio/ufo_ext_composio/proxy.py`

`io_transport` · `request handling`

Composio keeps provider credentials on its own side, so this project cannot simply take a token and call services like Google, Slack, or another provider itself. This file is the bridge. It takes an ordinary HTTP request aimed at a provider and wraps it in a request to Composio's proxy endpoint. Composio then adds the correct account credential on the server side and performs the real provider call.

The main piece, ComposioProxyTransport, acts like a custom HTTP transport. A transport is the layer that actually sends requests over the network. Here, instead of sending the request directly to the provider, it records the original method, URL, query parameters, safe headers, and body, then sends that information to Composio's proxy-execute endpoint. When Composio replies, the transport turns Composio's response back into a normal HTTP response with the provider's status, headers, and body.

The file is careful about two practical dangers. First, it skips headers that should not be forwarded, such as authorization and content length, because Composio is responsible for credentials and the body may be repackaged. Second, it can cap response size so a shared proxy process cannot be forced to buffer huge replies. If Composio stores a large binary response elsewhere, this code returns a redirect to that stored file instead of pulling all bytes through the proxy.

#### Function details

##### `ComposioProxyTransport.handle_async_request`  (lines 63–102)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: This is the main request-rewriting step. It receives a normal provider HTTP request, turns it into a Composio proxy-execute request, sends that to Composio, and returns a response shaped like the provider answered directly.

**Data flow**: It starts with an incoming HTTP request containing a method, URL, headers, query parameters, timeout information, and possibly a body. It reads the body, builds a JSON payload that names the connected Composio account and the provider endpoint, copies query parameters and allowed headers, and includes the body when present. It sends that payload to Composio using the inner transport, reads the reply with size protection, and then either returns Composio's error response as-is or converts the successful proxy payload into a provider-style response.

**Call relations**: This function is the front door for ComposioProxyTransport. During a proxied provider call, callers hand it the original request. It delegates response reading to ComposioProxyTransport._read_bounded so large replies can be limited, and it delegates successful response reconstruction to ComposioProxyTransport._provider_response so the rest of the system receives a normal HTTP response.

*Call graph*: calls 2 internal fn (_provider_response, _read_bounded); 4 external calls (Request, aread, Response, loads).


##### `ComposioProxyTransport._read_bounded`  (lines 104–119)

```
async def _read_bounded(self, response: httpx.Response) -> bytes
```

**Purpose**: This reads Composio's response body while optionally enforcing a maximum size. It exists to prevent the proxy process from accidentally buffering a very large response into memory.

**Data flow**: It receives an HTTP response from Composio. If no size limit is configured, it reads the whole response normally. If a limit is configured, it reads the response chunk by chunk, keeps a running byte count, and stops with an error if the total grows beyond the allowed size. The output is the response body as bytes, unless the size cap is exceeded, in which case it closes the response and raises a Composio error.

**Call relations**: ComposioProxyTransport.handle_async_request calls this right after Composio replies. It is the safety valve in the request flow: before any response is parsed or returned, this function makes sure the proxy is not consuming more memory than allowed.

*Call graph*: called by 1 (handle_async_request); 4 external calls (aclose, aiter_bytes, aread, ComposioError).


##### `ComposioProxyTransport._provider_response`  (lines 121–165)

```
def _provider_response(self, payload: dict[str, Any], request: httpx.Request) -> httpx.Response
```

**Purpose**: This turns Composio's proxy-execute result into a normal HTTP response that looks like it came from the original provider. It also handles the special case where the provider returned binary data that Composio stored separately.

**Data flow**: It receives a decoded JSON payload from Composio and the original request. It unwraps nested data envelopes until it reaches the provider's status, headers, and data. It removes body-related headers that would no longer be trustworthy after repackaging. If Composio reports binary data, it creates a 302 redirect response pointing to the presigned storage URL. Otherwise, it converts JSON objects, lists, strings, or other simple values into response bytes and returns an HTTP response with the reconstructed status, headers, and body.

**Call relations**: ComposioProxyTransport.handle_async_request calls this only after a successful Composio proxy call. This function is the adapter that lets provider-facing code keep working normally, including code that relies on response headers for pagination or status checks.

*Call graph*: called by 1 (handle_async_request); 4 external calls (Response, dumps, cast, ComposioError).


##### `ComposioProxyTransport.aclose`  (lines 167–168)

```
async def aclose(self) -> None
```

**Purpose**: This closes the underlying network transport used to talk to Composio. It is used when the proxy transport is no longer needed, so open connections and other resources can be cleaned up.

**Data flow**: It receives no new data. It calls the inner transport's close method, which releases any network resources held underneath. Nothing is returned.

**Call relations**: This is part of the cleanup path for ComposioProxyTransport. ComposioRequestForwarder.forward calls it in a finally block, meaning it runs even if the forward succeeds, fails, or times out.


##### `ComposioRequestForwarder.forward`  (lines 185–213)

```
async def forward(self, account_id: str, method: str, url: str, headers: Mapping[str, str], body: bytes) -> ForwardedResponse
```

**Purpose**: This forwards one provider request through Composio for a command-line or egress-proxy use case. It wraps the same Composio proxy transport in a bounded, timed operation and returns a simple forwarded response.

**Data flow**: It receives a Composio account id, HTTP method, URL, headers, and body bytes. It gets the configured Composio client and API key, builds a ComposioProxyTransport with a maximum response size, creates an HTTP request carrying the caller's data and timeout settings, and runs the request under a wall-clock timeout. If the call finishes, it reads the response body and returns a ForwardedResponse containing status, headers, and body. If the whole operation takes too long, it raises a Composio timeout error. In all cases, it closes the transport afterward.

**Call relations**: This is the higher-level entry point for code that needs to forward a single request through the broker rather than using an httpx client directly. It creates and uses ComposioProxyTransport.handle_async_request for the actual rewrite-and-send work, then packages the result into ForwardedResponse for the surrounding proxy system.

*Call graph*: 8 external calls (__init__, __init__, timeout, AsyncHTTPTransport, Request, Timeout, ComposioError, composio_client).


### `extensions/composio/ufo_ext_composio/mcp_session.py`

`io_transport` · `request handling during Composio tool search`

This file is a small bridge between this project and Composio's Tool Router, which exposes search through an MCP endpoint. MCP means “Model Context Protocol,” a standard way for a client to talk to tool servers. In everyday terms, this file opens a temporary phone line to Composio, asks one question, listens for the answer, and then hangs up.

The main job is to call a named tool at a given HTTP endpoint with headers, arguments, and a timeout. The endpoint is reached through FastMCP's streamable HTTP transport, which is the network connection style used here. After the remote call finishes, the file normalizes the response. Composio or the MCP library may return structured data in a few different places: directly as parsed data, as structured content, or as a text block containing JSON. This file checks those forms in order and returns a dictionary either way.

That normalization matters because the rest of the extension can work with one simple shape instead of knowing every possible MCP response format. If the text cannot be parsed as JSON, it is still preserved as plain text rather than being thrown away. The file also deliberately keeps `Client` as a module-level import, so tests can replace it with a fake client and avoid making real network calls.

#### Function details

##### `mcp_call_tool`  (lines 18–42)

```
async def mcp_call_tool(endpoint: str, tool: str, arguments: dict[str, Any], headers: dict[str, str], timeout_seconds: float) -> dict[str, object]
```

**Purpose**: Calls one tool on a remote MCP endpoint and returns the result as a plain dictionary. It is meant for Composio Tool Router search calls, where callers want a clean Python result instead of raw protocol objects.

**Data flow**: It receives the endpoint URL, tool name, argument dictionary, HTTP headers, and timeout. It opens a FastMCP streamable HTTP session, sends the tool call with a copy of the arguments, then closes the session when the call is done. It then looks through the returned object: first for already-parsed dictionary data, then for structured dictionary content, then for a text block that may contain JSON. The output is always a dictionary, even if the best available result is plain text or a non-dictionary value.

**Call relations**: When this function is used, it creates a FastMCP `Client` around a `StreamableHttpTransport` so the call can travel over HTTP to the MCP server. After the remote tool responds, it may use `json.loads` to turn a JSON-looking text reply into normal Python data. No other functions are defined in this file; this function is the file's single bridge between the local code and the remote MCP search endpoint.

*Call graph*: 3 external calls (Client, StreamableHttpTransport, loads).


### Pipedream broker integration
The Pipedream package exposes catalog actions, connected-account execution, file retrieval, and safe proxying through Pipedream Connect.

### `extensions/pipedream/ufo_ext_pipedream/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can contain an `__init__.py` file to say, “treat this folder as an importable package.” That matters because other parts of the project may need to load code from `extensions/pipedream/ufo_ext_pipedream` using normal Python import paths. Think of it like a label on a drawer: the drawer may hold many useful tools, but this label simply makes the drawer recognizable to the system. Since the file has no code, it does not configure anything, start anything, or change any data. Its value is structural: without it, depending on the Python version and import style, code that expects this directory to behave like a package might fail to import it cleanly.


### `extensions/pipedream/ufo_ext_pipedream/broker.py`

`domain_logic` · `request handling`

A Pipedream action is like a remote tool: it lives on Pipedream’s servers and can do things in apps such as Gmail, Slack, or Google Drive. This file wraps those remote tools in UFO’s common “broker” shape, so the rest of the system does not need to know Pipedream’s details.

The main class, PipedreamBroker, is deliberately stateless. Each method asks for a fresh Pipedream client when it runs. That matters because tests or callers may swap in a different network transport, and holding an old client could accidentally bypass that.

The broker can search for actions, turn an action definition into an input schema, and execute an action. When building a schema, it hides Pipedream’s internal fields, including the app account field. That account field is filled in by the broker at execution time using the user’s connected account. Before running an action, it checks that the account belongs to the right workspace and authenticates the right app.

It also improves error messages. If an action key is unknown, it tries to include real available action keys. If Pipedream says the connected account is stale or missing, it adds guidance telling the user to reconnect. For files, it reads Pipedream’s File Stash output and returns download URLs.

#### Function details

##### `PipedreamBroker.tools`  (lines 59–67)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Finds Pipedream actions for a provider, such as the available actions for a particular app. It returns them in UFO’s standard tool format so an agent can choose what to run.

**Data flow**: It receives a workspace id, provider name, and search text. It looks up the provider’s Pipedream app name, asks Pipedream for matching actions, converts the response into BrokerTool objects, and if a non-empty search found nothing, retries with an empty search so the caller still gets useful top actions.

**Call relations**: This is the main action lookup path. PipedreamBroker.search calls it when the broader broker interface asks for search results, and it relies on _spec to identify the app and _listed_tools to clean up Pipedream’s catalog response.

*Call graph*: calls 2 internal fn (_listed_tools, _spec); called by 1 (search); 1 external calls (pipedream_client).


##### `PipedreamBroker.schema`  (lines 69–75)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Builds a plain input description for one Pipedream action. This tells the agent which arguments it may provide when it later runs the action.

**Data flow**: It receives a workspace id, provider name, and action slug. It fetches the action definition, extracts the configurable properties, removes fields the broker must fill itself, converts the remaining fields into a JSON schema, and returns a BrokerTool with the action slug, description, and input schema.

**Call relations**: This is used when the system needs details for a specific action rather than a search list. It calls _definition to fetch the action, _props to read its configurable fields, _input_schema to translate those fields, and _str to safely read text.

*Call graph*: calls 4 internal fn (_definition, _input_schema, _props, _str); 1 external calls (__init__).


##### `PipedreamBroker.execute`  (lines 77–112)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Runs a selected Pipedream action using a user’s connected account. It is the core path that turns an agent’s chosen tool and arguments into a real remote action run.

**Data flow**: It receives the workspace, provider, action slug, user arguments, connected account id, and an optional idempotency key. It fetches the action definition, adds the connected account into the action’s hidden app slot, checks that the account belongs to the workspace and correct app, runs the action on Pipedream, and returns the response. If the action is unknown, the account is stale, or the action reports an error, it raises a clearer PipedreamError instead.

**Call relations**: This is called when the system has already selected a Pipedream action and wants it performed. It depends on _definition, _app_slot, and _spec before calling Pipedream’s run API, and uses _stale_account and _reconnect_error to turn account-related failures into reconnect guidance.

*Call graph*: calls 7 internal fn (_definition, _key_miss, _app_slot, _reconnect_error, _spec, _stale_account, __init__); 2 external calls (dumps, pipedream_client).


##### `PipedreamBroker.file_outputs`  (lines 114–132)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Finds files that a Pipedream action saved during its run and exposes them as downloadable broker files. This lets the sandbox or caller fetch generated files without knowing Pipedream’s File Stash format.

**Data flow**: It receives the action response dictionary. It looks inside the response exports for Pipedream’s file upload list, skips malformed entries, takes each valid download URL, derives a friendly filename from the local path when possible, and returns BrokerFile objects.

**Call relations**: This is used after an action run when the caller wants to collect files produced by the remote action. It does not call other project helpers, but it understands the File Stash field that Pipedream places in action responses.

*Call graph*: 2 external calls (__init__, PurePosixPath).


##### `PipedreamBroker.stage_upload`  (lines 134–146)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Rejects staged uploads for Pipedream actions. Pipedream expects file inputs as URLs, so this method tells callers to share the workspace file and pass its download link instead.

**Data flow**: It receives details for a file that someone wanted to stage for upload. Instead of creating an upload target, it immediately raises a ValueError explaining the correct URL-based path.

**Call relations**: This satisfies the broker interface but intentionally refuses the operation. The larger file flow should use a shared file URL rather than asking this broker to prepare a staged upload.


##### `PipedreamBroker.search`  (lines 148–149)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Wraps action lookup in UFO’s standard search result object. Pipedream does not provide extra planning or routing guidance here, so the result is just the matching tools.

**Data flow**: It receives a workspace id, provider, and query. It calls PipedreamBroker.tools to find matching actions and places those tools into a BrokerSearch result.

**Call relations**: This is the public search-shaped entry for callers that expect BrokerSearch. It delegates the real catalog lookup to PipedreamBroker.tools.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `PipedreamBroker.credential`  (lines 151–172)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Creates a credential transport that lets the system make proxied requests through a connected Pipedream account. It also verifies that the requested account belongs to the workspace and app.

**Data flow**: It receives a workspace id, provider, and account id. It looks up the provider spec, fetches the connected account from Pipedream, turns missing accounts into reconnect guidance, rejects accounts for the wrong app, and returns a Credential containing a PipedreamProxyTransport.

**Call relations**: This is used when code needs to talk to an app through Pipedream’s Connect Proxy rather than run a catalog action. It relies on _spec for the expected app and _reconnect_error for missing or stale account messages, then hands the verified account to PipedreamProxyTransport.

*Call graph*: calls 3 internal fn (_reconnect_error, _spec, __init__); 4 external calls (__init__, __init__, AsyncHTTPTransport, pipedream_client).


##### `PipedreamBroker._definition`  (lines 174–182)

```
async def _definition(self, slug: str) -> dict[str, object]
```

**Purpose**: Fetches the full definition for a Pipedream action. This definition is needed to build schemas and to know where to bind the connected account before execution.

**Data flow**: It receives an action slug. It asks the current Pipedream client for the action definition, turns a 404 not-found response into UnknownBrokerTool, and returns the definition data dictionary.

**Call relations**: PipedreamBroker.schema calls this to describe an action’s inputs, and PipedreamBroker.execute calls it before running an action. It is the shared fetch-and-normalize step for action definitions.

*Call graph*: called by 2 (execute, schema); 2 external calls (__init__, pipedream_client).


##### `PipedreamBroker._key_miss`  (lines 184–198)

```
async def _key_miss(self, client: pipedream.PipedreamClient, provider: str, slug: str) -> PipedreamError
```

**Purpose**: Creates a helpful error when an action slug is unknown. Instead of only saying “not found,” it tries to include the real action keys available for that app.

**Data flow**: It receives a Pipedream client, provider, and missing slug. It looks up the provider’s app, asks Pipedream for the app’s action list, converts that list into tools, and returns a PipedreamError that names the missing action and, when possible, the available actions.

**Call relations**: PipedreamBroker.execute uses this after _definition reports an unknown action. It depends on _spec to identify the app and _listed_tools to read Pipedream’s action list.

*Call graph*: calls 4 internal fn (_listed_tools, _spec, list_actions, __init__); called by 1 (execute).


##### `_stale_account`  (lines 201–208)

```
def _stale_account(error: PipedreamError, account_id: str) -> bool
```

**Purpose**: Checks whether a Pipedream error probably means the connected account is no longer valid. This prevents confusing low-level account errors from reaching the user without advice.

**Data flow**: It receives a PipedreamError and the account id being used. It lowercases the error body and looks for narrow signs such as “external user not found” or the same account id plus “not found,” then returns true or false.

**Call relations**: PipedreamBroker.execute calls this when Pipedream rejects a run or when the action response contains an error. If it returns true, execute uses _reconnect_error to add reconnect instructions.

*Call graph*: called by 1 (execute).


##### `_reconnect_error`  (lines 211–212)

```
def _reconnect_error(error: PipedreamError, provider: str) -> PipedreamError
```

**Purpose**: Adds user-facing reconnect guidance to a Pipedream error. It keeps the original status and message but appends advice for refreshing the app connection.

**Data flow**: It receives a PipedreamError and provider name. It builds a new PipedreamError with the same status, the old body, and extra guidance from stale_grant_guidance.

**Call relations**: PipedreamBroker.execute uses this for stale account failures during action runs, and PipedreamBroker.credential uses it when a requested connected account is not found.

*Call graph*: calls 1 internal fn (__init__); called by 2 (credential, execute); 1 external calls (stale_grant_guidance).


##### `_spec`  (lines 215–219)

```
def _spec(provider: str) -> ConnectorSpec
```

**Purpose**: Looks up UFO’s registered Pipedream connector information for a provider name. This is how the broker translates a provider into Pipedream’s app slug.

**Data flow**: It receives a provider string. It reads the CONNECTORS registry, returns the matching ConnectorSpec, or raises a KeyError if the provider is not registered.

**Call relations**: The broker calls this before searching, executing, creating credentials, or building missing-key errors. It is the small gatekeeper that ensures all provider names are known to the Pipedream extension.

*Call graph*: called by 4 (_key_miss, credential, execute, tools).


##### `_listed_tools`  (lines 222–234)

```
def _listed_tools(listed: dict[str, object]) -> tuple[BrokerTool, ...]
```

**Purpose**: Turns Pipedream’s action-list response into UFO BrokerTool objects. It keeps only usable action keys and short descriptions.

**Data flow**: It receives a dictionary from Pipedream’s list-actions API. It reads the data list, skips malformed entries or blank keys, safely extracts each description, and returns a tuple of BrokerTool objects.

**Call relations**: PipedreamBroker.tools uses this after catalog searches, and PipedreamBroker._key_miss uses it when building a helpful not-found message. It calls _str to avoid treating non-text descriptions as text.

*Call graph*: calls 1 internal fn (_str); called by 2 (_key_miss, tools); 1 external calls (__init__).


##### `_props`  (lines 237–239)

```
def _props(definition: dict[str, object]) -> list[dict[str, object]]
```

**Purpose**: Extracts the configurable property list from an action definition. These properties are the raw material for input schemas and account binding.

**Data flow**: It receives an action definition dictionary. It reads configurable_props, keeps only entries that are dictionaries, and returns them as a list; if the shape is not a list, it returns an empty list.

**Call relations**: PipedreamBroker.schema calls this before building the user-facing schema, and _app_slot calls it to find the hidden app account field.

*Call graph*: called by 2 (schema, _app_slot).


##### `_app_slot`  (lines 242–249)

```
def _app_slot(definition: dict[str, object], slug: str) -> str
```

**Purpose**: Finds the special action input where the connected app account must be inserted. Without this slot, the broker cannot run the action on behalf of the user.

**Data flow**: It receives an action definition and slug. It scans the configurable properties for a property whose type is Pipedream’s app/account type and whose name is valid, then returns that name. If no such slot exists, it raises a PipedreamError.

**Call relations**: PipedreamBroker.execute calls this before running an action so it knows where to place the account’s authProvisionId. It relies on _props to read the action’s configurable fields.

*Call graph*: calls 2 internal fn (_props, __init__); called by 1 (execute).


##### `_input_schema`  (lines 252–275)

```
def _input_schema(props: list[dict[str, object]]) -> dict[str, object]
```

**Purpose**: Builds the JSON schema that describes which action inputs the agent may provide. JSON schema is a standard machine-readable way to describe fields, types, and required values.

**Data flow**: It receives a list of Pipedream configurable properties. It skips internal fields such as the app account slot, service fields that start with "$.", and directory fields, maps known Pipedream types to JSON types, adds descriptions when available, records required fields, and returns an object schema.

**Call relations**: PipedreamBroker.schema calls this after fetching and extracting an action’s properties. It uses _str to safely read property type names before deciding whether to include each field.

*Call graph*: calls 1 internal fn (_str); called by 1 (schema).


##### `_str`  (lines 278–279)

```
def _str(value: object) -> str
```

**Purpose**: Safely turns a value into text only when it is already a string. It avoids accidentally exposing non-text values as descriptions or type names.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: PipedreamBroker.schema, _input_schema, and _listed_tools use this as a small safety helper when reading optional text from Pipedream responses.

*Call graph*: called by 3 (schema, _input_schema, _listed_tools).


### `extensions/pipedream/ufo_ext_pipedream/client.py`

`io_transport` · `cross-cutting: used during OAuth connection, account validation, action lookup, and connector action execution`

This file is the bridge between this project and Pipedream Connect. Pipedream acts like a secure middle desk: users sign in to a provider such as Gmail through Pipedream, Pipedream keeps the real provider token, and this project stores only a connected-account ID. That matters because leaking or mishandling a provider token would be much more dangerous than storing an ID that only Pipedream can use.

The file defines which connectors this Pipedream broker is allowed to offer. Right now, Gmail is listed, including support for a deploy-specific Google OAuth app when needed. It also defines small result objects, such as a Connect token and a connected account, plus a custom error type used when Pipedream replies with a failure or an unexpected response.

The main piece is `PipedreamClient`. It signs into Pipedream using client credentials from the deployment environment, caches that short-lived Pipedream access token, and then uses it for later API calls. It can mint a hosted consent link, read accounts, search action catalogs, fetch action definitions, and run an action server-side. Before trusting an account, it checks that the account belongs to the expected external user or workspace. That ownership check is a safety guard: the project-wide Pipedream token can read many accounts, so this code must make sure it never lets one workspace use another workspace’s connection.

#### Function details

##### `PipedreamError.__init__`  (lines 79–82)

```
def __init__(self, status: int, body: str) -> None
```

**Purpose**: Creates a clear exception for a failed or unusable Pipedream response. It records both the HTTP-style status number and the response text so callers can report or react to the exact failure.

**Data flow**: It receives a status code and a body string. It turns them into a readable error message like `pipedream 403: ...`, stores the original pieces on the error object, and raises no error by itself until a caller chooses to throw it.

**Call relations**: This error is used throughout the Pipedream broker and this client whenever a request fails, a response is missing required fields, or an ownership check blocks access. It is the shared way the Pipedream path fails loudly instead of pretending an account or action is usable.

*Call graph*: called by 12 (_key_miss, credential, execute, _app_slot, _reconnect_error, access_token, connect_token, newest_account, workspace_account, _account (+2 more)).


##### `PipedreamClient.access_token`  (lines 120–141)

```
async def access_token(self) -> str
```

**Purpose**: Gets the access token this deployment needs in order to call Pipedream’s API. It reuses a cached token while it is still fresh, so the system does not ask Pipedream for a new token on every request.

**Data flow**: It starts with the client ID and secret stored in the `PipedreamClient`. It first checks the process-wide token cache; if the cached token has enough time left, it returns it. Otherwise it posts the credentials to Pipedream’s OAuth token endpoint, reads the JSON response, validates that an access token is present, stores the token with its expiry time, and returns the token string.

**Call relations**: `_get` and `_post` call this before making authenticated Pipedream requests. It uses `_http` to open the HTTP client and `_body` to turn the HTTP response into a safe dictionary, and it raises `PipedreamError` if Pipedream does not return a usable token.

*Call graph*: calls 3 internal fn (_http, __init__, _body); called by 2 (_get, _post); 1 external calls (monotonic).


##### `PipedreamClient.connect_token`  (lines 143–158)

```
async def connect_token(self, external_user_id: str, success_redirect_uri: str, error_redirect_uri: str) -> ConnectToken
```

**Purpose**: Creates a short-lived Pipedream Connect token and hosted consent link for a user. A caller uses this when it needs to send the user to Pipedream’s web page to connect an account.

**Data flow**: It receives an external user ID plus success and error redirect URLs. It sends those to Pipedream, expects back a token and a connect-link URL, checks both are non-empty strings, and returns them as a `ConnectToken` object.

**Call relations**: This is part of the OAuth connection flow. It delegates the HTTP work to `_post`, and if Pipedream’s answer does not include the link and token needed to continue, it raises `PipedreamError` so the connection flow stops clearly.

*Call graph*: calls 2 internal fn (_post, __init__); 1 external calls (__init__).


##### `PipedreamClient.connected_account`  (lines 160–167)

```
async def connected_account(self, account_id: str, external_user_id: str) -> ConnectedAccount
```

**Purpose**: Reads one connected account and confirms it belongs to the exact external user expected. This prevents the system from accidentally using an account connected by someone else.

**Data flow**: It receives a Pipedream account ID and an expected external user ID. It fetches the account record from Pipedream, extracts the actual account data, and passes it through `_owned_account`, which checks the owner and account health. It returns a `ConnectedAccount` if the account is safe to use.

**Call relations**: This function is used when the flow already knows the exact external user that should own the account. It relies on `_get` for the API request, `_dict` to safely unpack the response shape, and `_owned_account` for the security check.

*Call graph*: calls 3 internal fn (_get, _dict, _owned_account).


##### `PipedreamClient.workspace_account`  (lines 169–179)

```
async def workspace_account(self, account_id: str, workspace_id: UUID) -> ConnectedAccount
```

**Purpose**: Reads a connected account and confirms it belongs to the given workspace. This is used when execution should be allowed for any valid connection user under that workspace, not just one exact external user string.

**Data flow**: It receives an account ID and a workspace UUID. It fetches the account record, turns it into a `ConnectedAccount`, then checks whether the account’s external user ID matches the naming pattern for that workspace. If the pattern does not match, it raises a forbidden error; otherwise it returns the account.

**Call relations**: This is an execution-time safety gate. It uses `_get` to read from Pipedream, `_account` to validate the account record itself, and `_workspace_owns_external_user` to decide whether the external user ID really belongs to the workspace.

*Call graph*: calls 5 internal fn (_get, __init__, _account, _dict, _workspace_owns_external_user).


##### `PipedreamClient.newest_account`  (lines 181–195)

```
async def newest_account(self, external_user_id: str, app: str) -> ConnectedAccount
```

**Purpose**: Finds the most recently created connected account for a specific external user and Pipedream app. This is useful right after a user finishes the consent page, when the system needs to discover which account was just connected.

**Data flow**: It receives an external user ID and an app slug such as `gmail`. It asks Pipedream for matching accounts, keeps only dictionary-shaped records, fails if none exist, chooses the record with the latest creation timestamp, checks that it has an ID, verifies ownership, and returns a `ConnectedAccount`.

**Call relations**: This sits at the end of the connection flow after Pipedream redirects the user back. It uses `_get` to query accounts, `_dict` to normalize records, and `_owned_account` to make sure the newest account really belongs to the external user from the flow.

*Call graph*: calls 4 internal fn (_get, __init__, _dict, _owned_account).


##### `PipedreamClient.list_actions`  (lines 197–203)

```
async def list_actions(self, app: str, query: str='', limit: int=ACTION_SEARCH_LIMIT) -> dict[str, object]
```

**Purpose**: Searches Pipedream’s catalog of available actions for a given app. This lets the broker discover what ready-made operations, such as Gmail actions, can be offered as tools.

**Data flow**: It receives an app name, an optional search query, and a maximum result count. It builds query parameters, sends them to Pipedream, and returns Pipedream’s response dictionary unchanged after normal response validation.

**Call relations**: The Pipedream broker calls this when it needs to resolve or suggest an action key. This function does not interpret the catalog deeply; it uses `_get` to fetch the catalog data from Pipedream.

*Call graph*: calls 1 internal fn (_get); called by 1 (_key_miss).


##### `PipedreamClient.action_definition`  (lines 205–206)

```
async def action_definition(self, key: str) -> dict[str, object]
```

**Purpose**: Fetches the detailed definition for one Pipedream action. A caller uses this to learn what inputs an action expects before trying to run it.

**Data flow**: It receives an action or component key. It requests that component from Pipedream and returns the response dictionary after the standard HTTP and JSON checks.

**Call relations**: This is a lookup helper for code that needs to describe or prepare a Pipedream action. It hands the actual network request to `_get`.

*Call graph*: calls 1 internal fn (_get).


##### `PipedreamClient.run_action`  (lines 208–226)

```
async def run_action(self, key: str, external_user_id: str, configured_props: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs one Pipedream action on the server side for a connected user. It also asks Pipedream to create a fresh file stash so files produced by the action can be returned as downloadable URLs instead of unreadable temporary paths.

**Data flow**: It receives an action key, an external user ID, and configured input values. It builds the Pipedream run request, adds `stash_id` set to `NEW`, checks that the JSON request is not larger than the configured limit, posts it to Pipedream, and returns the response dictionary.

**Call relations**: This is the final execution step after an account and action have been chosen. It uses `_post` for the authenticated API call, and it blocks oversized requests locally before sending them to Pipedream.

*Call graph*: calls 1 internal fn (_post); 1 external calls (dumps).


##### `PipedreamClient._get`  (lines 228–231)

```
async def _get(self, path: str, params: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Performs an authenticated GET request to Pipedream and returns a checked JSON object. It is the shared read path for account, action list, and action definition calls.

**Data flow**: It receives an API path and optional query parameters. It obtains a Pipedream access token, opens an HTTP client with that token, sends the GET request, passes the response through `_body`, and returns the resulting dictionary.

**Call relations**: Higher-level methods such as `connected_account`, `workspace_account`, `newest_account`, `list_actions`, and `action_definition` call this instead of repeating the same authentication and response-checking steps.

*Call graph*: calls 3 internal fn (_http, access_token, _body); called by 5 (action_definition, connected_account, list_actions, newest_account, workspace_account).


##### `PipedreamClient._post`  (lines 233–236)

```
async def _post(self, path: str, body: dict[str, object]) -> dict[str, object]
```

**Purpose**: Performs an authenticated POST request to Pipedream and returns a checked JSON object. It is the shared write or command path for creating connect tokens and running actions.

**Data flow**: It receives an API path and a dictionary body. It gets an access token, opens an authenticated HTTP client, sends the body as JSON, checks the response through `_body`, and returns the response dictionary.

**Call relations**: `connect_token` and `run_action` call this when they need to send data to Pipedream. It centralizes the token lookup, HTTP setup, and response validation.

*Call graph*: calls 3 internal fn (_http, access_token, _body); called by 2 (connect_token, run_action).


##### `PipedreamClient._http`  (lines 238–251)

```
def _http(self, token: str | None=None) -> httpx.AsyncClient
```

**Purpose**: Builds the temporary HTTP client used for one Pipedream request. If a token is supplied, it adds the authorization header and the Pipedream environment header.

**Data flow**: It receives either a token string or nothing. With a token, it creates headers for bearer-token authentication and the selected Pipedream environment; without a token, it creates no headers for the OAuth token request. It returns an `httpx.AsyncClient`, which is an asynchronous web client.

**Call relations**: `access_token`, `_get`, and `_post` call this whenever they need to talk to Pipedream. The returned client is opened and closed around each call, which also makes test transports easy to plug in.

*Call graph*: called by 3 (_get, _post, access_token); 1 external calls (AsyncClient).


##### `_dict`  (lines 254–255)

```
def _dict(value: object) -> dict[str, object]
```

**Purpose**: Safely treats a value as a dictionary only if it really is one. This avoids crashes when Pipedream returns a field in an unexpected shape.

**Data flow**: It receives any value. If the value is a dictionary, it returns that same dictionary; otherwise it returns an empty dictionary.

**Call relations**: Account-reading functions use this before looking inside nested response data. It is a small guardrail used by `connected_account`, `workspace_account`, `newest_account`, and `_account`.

*Call graph*: called by 4 (connected_account, newest_account, workspace_account, _account).


##### `_owned_account`  (lines 258–271)

```
def _owned_account(record: dict[str, object], account_id: str, external_user_id: str) -> ConnectedAccount
```

**Purpose**: Checks that an account record is healthy and owned by one exact external user. This is an important safety check that stops one user’s connection from being used for another user.

**Data flow**: It receives a raw account record, the expected account ID, and the expected external user ID. It first turns the record into a `ConnectedAccount` with `_account`, then compares the account’s recorded owner to the expected owner. If they match, it returns the account; if not, it raises a forbidden `PipedreamError`.

**Call relations**: `connected_account` and `newest_account` use this after fetching account data from Pipedream. It builds on `_account`, adding the stricter exact-owner check needed for connection flows.

*Call graph*: calls 2 internal fn (__init__, _account); called by 2 (connected_account, newest_account).


##### `_account`  (lines 274–286)

```
def _account(record: dict[str, object], account_id: str) -> ConnectedAccount
```

**Purpose**: Turns a raw Pipedream account record into this project’s simple `ConnectedAccount` object, while rejecting unusable records. It checks that the account has an owner and is not marked unhealthy.

**Data flow**: It receives a raw account record and the account ID being read. It looks for the `external_id` owner, rejects the record if the owner is missing, rejects it if Pipedream says the account is unhealthy, extracts the app slug if present, and returns a `ConnectedAccount` containing the account ID, app, and owner.

**Call relations**: `workspace_account` uses this directly when it will check workspace ownership afterward. `_owned_account` also uses it before checking exact external-user ownership.

*Call graph*: calls 2 internal fn (__init__, _dict); called by 2 (workspace_account, _owned_account); 1 external calls (__init__).


##### `workspace_user_prefix`  (lines 289–290)

```
def workspace_user_prefix(workspace_id: UUID) -> str
```

**Purpose**: Creates the standard prefix used for external user IDs that belong to a workspace. This gives the code a predictable way to recognize workspace-scoped Pipedream connections.

**Data flow**: It receives a workspace UUID. It converts the UUID to its compact hexadecimal form and returns a string beginning with `ufo_`, followed by that workspace value and an underscore.

**Call relations**: `connection_user_id` uses this when creating a new external user ID, and `_workspace_owns_external_user` uses it when checking whether an existing external user ID belongs to a workspace.

*Call graph*: called by 2 (_workspace_owns_external_user, connection_user_id).


##### `_workspace_owns_external_user`  (lines 293–302)

```
def _workspace_owns_external_user(workspace_id: UUID, external_user_id: str) -> bool
```

**Purpose**: Decides whether a Pipedream external user ID belongs to a particular workspace. This is the workspace-level version of the account ownership guard.

**Data flow**: It receives a workspace UUID and an external user ID string. It first accepts an older direct workspace format, then checks for the newer workspace prefix plus a 32-character lowercase hexadecimal connection ID. It returns `true` only when the ID matches one of those accepted workspace-owned forms.

**Call relations**: `workspace_account` calls this after reading an account from Pipedream. It uses `workspace_user_prefix` to know what prefix a valid connection user should have.

*Call graph*: calls 1 internal fn (workspace_user_prefix); called by 1 (workspace_account).


##### `connection_user_id`  (lines 305–307)

```
def connection_user_id(workspace_id: UUID, state: str) -> str
```

**Purpose**: Creates a stable Pipedream external user ID for a workspace connection flow. It uses the connection state to produce a short, deterministic ID without storing the raw state in the user ID.

**Data flow**: It receives a workspace UUID and a state string. It hashes the state with SHA-256, takes the first 32 hexadecimal characters, prefixes that with the workspace user prefix, and returns the full external user ID.

**Call relations**: Connection setup code can use this to name the Pipedream external user for one consent flow. It shares the same prefix format that `_workspace_owns_external_user` later recognizes during account validation.

*Call graph*: calls 1 internal fn (workspace_user_prefix); 1 external calls (sha256).


##### `_body`  (lines 310–318)

```
def _body(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Converts an HTTP response from Pipedream into a safe dictionary, or raises a clear error if the response is a failure or not shaped as expected.

**Data flow**: It receives an HTTP response. If the status code is 400 or higher, it raises `PipedreamError` with the response text. If the response has no content, it returns an empty dictionary. Otherwise it parses JSON, confirms the parsed value is a dictionary, and returns it.

**Call relations**: `access_token`, `_get`, and `_post` all use this immediately after receiving a response from Pipedream. This keeps response checking consistent across token minting, reads, and command calls.

*Call graph*: calls 1 internal fn (__init__); called by 3 (_get, _post, access_token); 1 external calls (json).


##### `pipedream_client`  (lines 321–339)

```
def pipedream_client() -> PipedreamClient
```

**Purpose**: Builds a `PipedreamClient` from deployment environment variables. It fails immediately if the required Pipedream credentials or project ID are missing.

**Data flow**: It reads the client ID, client secret, project ID, and optional environment name from environment variables. If any required value is missing, it raises a runtime error. Otherwise it returns a configured `PipedreamClient` ready to call Pipedream.

**Call relations**: Other parts of the Pipedream extension call this when they need the default real client for the current deployment. It is the handoff point from configuration stored in the environment to the API client used by the broker.

*Call graph*: 1 external calls (__init__).


### `extensions/pipedream/ufo_ext_pipedream/proxy.py`

`io_transport` · `request handling`

Pipedream keeps provider credentials, such as Gmail or Slack tokens, on its own servers. That is safer, but it creates a problem: a connector still needs to make ordinary HTTP requests to the provider. This file solves that by acting like a translator in the middle. A connector asks for a normal URL on the provider. The transport rewrites that request into a special Pipedream proxy URL that says, in effect, “please call this real provider URL using this account’s stored credential.”

The original URL is encoded into the proxy path so it can safely travel inside another URL. The account id and external user id are added as query values so Pipedream knows which stored account to use. Request headers are treated carefully: headers that belong to the local HTTP connection, such as content length or host, are dropped, while useful provider headers are renamed with Pipedream’s required `x-pd-proxy-` prefix. Pipedream removes that prefix before forwarding them upstream.

The important result is that the connector can keep acting like it is talking directly to the provider. Response body, headers, and status code come back unchanged. That matters because many connectors rely on exact provider behavior, such as a 404 meaning an expired cursor or a 401 meaning access is no longer allowed.

#### Function details

##### `PipedreamProxyTransport.handle_async_request`  (lines 54–73)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: This is the main translation step for each outgoing provider request. It builds a Pipedream proxy request that carries the original provider URL, request body, and allowed headers, while using a Pipedream access token instead of a provider token.

**Data flow**: It receives an HTTP request meant for the real provider. It asks the Pipedream client for an access token, reads the request body, filters and renames the request headers, encodes the original URL, and creates a new request to Pipedream’s proxy endpoint with the account and external user attached. It then sends that new request through the inner transport and returns the response it gets back, preserving the provider-like result for the caller.

**Call relations**: This method is called by the HTTP client whenever a connector sends a request through this transport. During that moment, it uses standard HTTP tools to read the original request, build the proxy URL, and create the replacement request. It then hands the rewritten request to the wrapped inner transport, which performs the actual network sending.

*Call graph*: 4 external calls (urlsafe_b64encode, Request, aread, URL).


##### `PipedreamProxyTransport.aclose`  (lines 75–76)

```
async def aclose(self) -> None
```

**Purpose**: This shuts down the wrapped transport when the proxy transport is no longer needed. It ensures any underlying network resources are cleaned up properly.

**Data flow**: It receives no new request data. It forwards the close instruction to the inner transport, which releases its own open connections or other resources. Nothing is returned.

**Call relations**: This is called during cleanup when the HTTP client or transport is being closed. Rather than doing separate cleanup itself, it delegates to the inner transport because that is the part that actually owns the lower-level network resources.


### Additional app tool packs
These adapters add workspace-configured MCP tools plus guided Slack and Y Combinator access paths for specific external services.

### `extensions/mcp/ufo_ext_mcp.py`

`io_transport` · `request handling`

This file is the bridge between the agent and external MCP servers. A workspace can name one or more MCP servers in a private credential setting, each with a URL and optional access token. The agent does not know the tools ahead of time. Instead, it first asks a named server what tools it offers, then later calls one of those tools with JSON arguments.

The file exposes two agent-facing tools. `list_mcp_tools` is like asking a shop for its menu: it returns each available tool's name, description, expected input shape, and whether the tool appears safe to repeat. `call_mcp_tool` then invokes one exact tool from that menu. Both use a FastMCP HTTP client, which takes care of the MCP connection details such as the initial handshake, session ID, streamed responses, and paginated tool lists.

Because MCP servers are outside this system, their responses are treated as untrusted. The file also puts hard size limits on requests and responses, so a server cannot accidentally or maliciously push huge data through the tool call. If a remote tool fails, the failure is returned as an error result that the model can see, rather than being hidden.

#### Function details

##### `McpServer._http_url`  (lines 70–73)

```
def _http_url(cls, value: str) -> str
```

**Purpose**: This validates that a configured MCP server URL starts with `http://` or `https://`. It prevents the system from trying to use unsupported or surprising address types.

**Data flow**: It receives the URL string from the server configuration. It checks the string against a simple web-address pattern. If the URL is acceptable, the same string comes back; if not, validation fails with a clear error.

**Call relations**: This runs automatically when an `McpServer` configuration object is built. It acts as the gatekeeper before any later code can create a client or contact the configured server.


##### `mcp_client`  (lines 95–101)

```
def mcp_client(server: McpServer) -> Client
```

**Purpose**: This creates a FastMCP client connected to one configured MCP server. It also attaches the server's bearer token, if one was configured, so authenticated servers can be reached.

**Data flow**: It takes an `McpServer` object containing a URL and optional auth token. It turns the token into an HTTP `authorization` header when present, builds a streamable HTTP transport for the URL, and returns a client with a fixed timeout.

**Call relations**: The list and call tool flows both ask this function for a client after `_server` has found the right configuration. Once created, the FastMCP client takes over the low-level MCP conversation with the remote server.

*Call graph*: called by 2 (_call_mcp_tool, _list_mcp_tools); 2 external calls (Client, StreamableHttpTransport).


##### `_server`  (lines 104–116)

```
async def _server(ctx: ToolContext, name: str) -> McpServer
```

**Purpose**: This looks up a named MCP server from the workspace's private `mcp_servers` credential. It makes sure tool calls only go to servers the workspace explicitly configured.

**Data flow**: It receives the current tool context and the requested server name. It reads the credential JSON, validates it into named server entries, and searches for the requested name. It returns the matching `McpServer`, or raises an error if the extension context or server name is missing.

**Call relations**: Both `_list_mcp_tools` and `_call_mcp_tool` start by using this lookup. This keeps the rest of the flow from making a blind network call to an unknown or unconfigured destination.

*Call graph*: called by 2 (_call_mcp_tool, _list_mcp_tools).


##### `_list_mcp_tools`  (lines 119–135)

```
async def _list_mcp_tools(ctx: ToolContext, args: ListMcpToolsInput) -> ToolResult
```

**Purpose**: This implements the agent-facing `list_mcp_tools` tool. It asks a configured MCP server what tools it offers, so the agent can use exact tool names and argument shapes instead of guessing.

**Data flow**: It receives a tool context and input containing a server name. It resolves that server, opens an MCP client connection, asks for the tool list, and converts each remote tool into a plain JSON-friendly summary. It returns those summaries as a tool result.

**Call relations**: This is the discovery step before `_call_mcp_tool`. It relies on `_server` for the configured endpoint, `mcp_client` for the connection, and `_json_result` to package the response in the standard tool-result format.

*Call graph*: calls 3 internal fn (_json_result, _server, mcp_client).


##### `_call_mcp_tool`  (lines 138–150)

```
async def _call_mcp_tool(ctx: ToolContext, args: CallMcpToolInput) -> ToolResult
```

**Purpose**: This implements the agent-facing `call_mcp_tool` tool. It sends JSON arguments to one exact tool on a configured MCP server and returns the remote result to the agent.

**Data flow**: It receives a server name, tool name, and JSON argument object. It finds the server, checks that the serialized arguments are not larger than the request limit, opens a client, and calls the remote tool. If the remote call reports an error, it returns an error result with bounded text; otherwise it prefers structured JSON content and falls back to joined text content.

**Call relations**: This is normally used after `_list_mcp_tools` has revealed the correct remote tool name and input schema. It uses `_server` and `mcp_client` to reach the server, `_joined_text` to extract readable text from MCP content blocks, `_bounded` to enforce response size limits, and `_json_result` to package successful output.

*Call graph*: calls 5 internal fn (_bounded, _joined_text, _json_result, _server, mcp_client); 4 external calls (__init__, __init__, __init__, dumps).


##### `_joined_text`  (lines 153–154)

```
def _joined_text(content: list[object]) -> str
```

**Purpose**: This extracts readable text from a list of MCP content blocks. It ignores non-text blocks and joins all text blocks with line breaks.

**Data flow**: It receives a list of content objects returned by an MCP call. It keeps only items that are MCP text content, reads their `text` fields, and combines them into one string. The result is a single plain-text message.

**Call relations**: _call_mcp_tool` uses this when a remote tool fails or when the result does not contain structured JSON. It provides a simple fallback so the agent still receives human-readable output.

*Call graph*: called by 1 (_call_mcp_tool).


##### `_bounded`  (lines 157–160)

```
def _bounded(text: str) -> str
```

**Purpose**: This enforces the maximum response size for text returned through this extension. It fails loudly instead of silently cutting off data.

**Data flow**: It receives a text string and measures its encoded byte size. If the text is within the allowed limit, it returns the same text unchanged. If it is too large, it raises an MCP-specific error.

**Call relations**: _call_mcp_tool` uses this for error text from remote tools, and `_json_result` uses it for JSON responses. It is the shared safety check that keeps oversized MCP results from passing through.

*Call graph*: called by 2 (_call_mcp_tool, _json_result); 1 external calls (__init__).


##### `_json_result`  (lines 163–164)

```
def _json_result(payload: dict[str, JsonValue]) -> ToolResult
```

**Purpose**: This turns a Python dictionary into the standard tool-result shape used by the agent. It is the common wrapper for successful JSON-like MCP responses.

**Data flow**: It receives a dictionary payload. It serializes that payload to JSON text, checks the size with `_bounded`, wraps the text in a `TextContent` object, and returns a `ToolResult` containing it.

**Call relations**: _list_mcp_tools` uses this to return discovered tool catalogs, and `_call_mcp_tool` uses it for successful remote tool output. It centralizes the final packaging step so both tools return results in the same format.

*Call graph*: calls 1 internal fn (_bounded); called by 2 (_call_mcp_tool, _list_mcp_tools); 3 external calls (__init__, __init__, dumps).


##### `manifest`  (lines 167–198)

```
def manifest() -> Manifest
```

**Purpose**: This declares the extension to the host system. It tells the host the extension's name, version, available tools, input models, handlers, and required credential slot.

**Data flow**: It builds a manifest containing two tool definitions and one credential definition. The tool definitions point to `_list_mcp_tools` and `_call_mcp_tool`, and the credential definition explains where configured MCP server details come from. The finished `Manifest` object is returned to the extension loader.

**Call relations**: This is the registration point for the file. When the host loads the extension, it calls `manifest` to learn which tools exist and what credential data must be available before the request-time functions can run.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/slack/ufo_ext_slack/tools.py`

`orchestration` · `Slack setup and Slack tool calls`

Slack integration needs several moving parts to line up: a Slack app, a bot token, a signing secret, the right callback URL, and proof that Slack can reach this UFO deployment. This file packages that into three tools the agent can call: one to connect Slack, one to generate a Slack app manifest, and one to search Slack channels and direct messages.

There are two setup paths. The preferred path is OAuth, which is the familiar “Add to Slack” button: if this deployment has its own Slack app configured, the owner gets a short-lived install link. The other path is “manifest,” where the member creates their own Slack app using a ready-made YAML manifest, then privately supplies the bot token and signing secret. In both paths, the code tries to prove the Slack team and bot identity, then reports a simple state such as not configured, not installed, pending, or connected.

A key detail is the difference between “pending” and “connected.” Pending means the bot identity is known, but Slack has not yet successfully sent a signature-verified request to this deployment. Connected means Slack has contacted the deployment and the signing secret matched. This is like checking not only that you have a key, but that it actually opens the door.

Once connected, the channel search tool uses the bot token to page through Slack conversations and return matching channels or DMs, so the agent can find a place by name or people instead of needing a raw Slack ID.

#### Function details

##### `_events_url`  (lines 127–128)

```
def _events_url(public_base_url: str) -> str
```

**Purpose**: Builds the public web address Slack should send events to. This is the callback URL Slack needs so messages, mentions, and setup checks can reach this deployment.

**Data flow**: It takes the deployment’s public base URL, removes any trailing slash, then adds the Slack surface path. The result is a clean URL like the front door Slack should knock on.

**Call relations**: The Slack connection flow uses it when reporting setup status, and the manifest generator uses it when filling in the Slack app manifest. It is a small shared helper so both paths point Slack at the same endpoint.

*Call graph*: called by 2 (slack_connect_handler, slack_manifest_handler).


##### `_state`  (lines 131–133)

```
def _state(state: str, hint: str, events_url: str | None, **extra: object) -> ToolResult
```

**Purpose**: Packages a Slack setup status into a tool response the agent can show or use. It keeps statuses consistent by always returning JSON with a state, a human hint, the event URL, and any extra details.

**Data flow**: It receives a state name, an explanation, an optional Slack events URL, and extra fields. It turns those into a JSON string, wraps the string as text content, and returns it as a tool result.

**Call relations**: The main setup flow and its helper paths call this whenever they need to explain where installation stands. It is the common response builder for OAuth setup, manifest setup, and final connected or pending states.

*Call graph*: called by 3 (_derive_manifest_identity, _oauth_link, slack_connect_handler); 3 external calls (__init__, __init__, dumps).


##### `slack_connect_handler`  (lines 136–181)

```
async def slack_connect_handler(ctx: ToolContext, args: SlackConnectInput) -> ToolResult
```

**Purpose**: Runs the main Slack connection check and setup flow. Someone can call it repeatedly before, during, and after installation, and it will report the current state or give the next action.

**Data flow**: It reads the workspace’s stored Slack bot token if one exists, then tries to read the saved Slack identity. If there is no identity, it either creates an OAuth install link or tries the manifest-based identity setup. Once it has an identity, it binds that Slack team to the current UFO workspace, checks whether Slack has successfully reached this deployment, and returns a JSON status such as pending or connected.

**Call relations**: This is the handler behind the slack_connect tool. It asks _events_url for Slack’s callback address, delegates to _oauth_link or _derive_manifest_identity when identity is missing, uses read_identity and slack_installation_id from the Slack surface layer, checks final reachability with _verified, and formats all user-facing states through _state.

*Call graph*: calls 5 internal fn (_derive_manifest_identity, _events_url, _oauth_link, _state, _verified); 2 external calls (read_identity, slack_installation_id).


##### `_oauth_link`  (lines 184–214)

```
async def _oauth_link(ctx: ToolContext, events_url: str | None) -> ToolResult
```

**Purpose**: Creates the one-click “Add to Slack” install link for deployments that have their own Slack app configured. It also protects installation so only the workspace owner can start it.

**Data flow**: It first checks environment variables for the Slack app’s client ID and client secret. If they are missing, it tells the caller to use the manifest path instead. If the speaker is not the owner, it asks them to get the owner. If everything is ready, it creates a sealed authorization handoff, builds the Slack authorization URL, and returns it in a setup status response.

**Call relations**: The main slack_connect_handler calls this when the OAuth path is requested and no Slack identity exists yet. It uses ToolContext to confirm ownership and start credential authorization, then relies on Slack surface helpers to build the correct Slack URL before handing the result back through _state.

*Call graph*: calls 3 internal fn (begin_credential_authorization, speaker_is_owner, _state); called by 1 (slack_connect_handler); 3 external calls (slack_authorize_url, slack_client_id, slack_oauth_redirect_uri).


##### `_derive_manifest_identity`  (lines 217–251)

```
async def _derive_manifest_identity(ctx: ToolContext, events_url: str | None) -> SlackIdentity | ToolResult
```

**Purpose**: Completes the bring-your-own-Slack-app setup path. It waits until the needed secrets are present, then asks Slack to prove what team and bot the token belongs to.

**Data flow**: It checks whether the bot token and signing secret credential slots have been filled. If any are missing, it returns a not_configured status listing what is still needed. Once both exist, it requires the speaker to be the owner, uses the bot token to resolve the Slack identity, and either returns that identity or turns Slack token errors into clear setup advice.

**Call relations**: slack_connect_handler calls this when the manifest path is selected and no identity has been saved yet. It formats waiting or error states with _state, uses _token_diagnosis for clearer token-failure messages, and relies on SlackIdentityResolver to perform the Slack identity proof.

*Call graph*: calls 3 internal fn (speaker_is_owner, _state, _token_diagnosis); called by 1 (slack_connect_handler); 1 external calls (__init__).


##### `_verified`  (lines 254–273)

```
async def _verified(ctx: ToolContext) -> bool
```

**Purpose**: Checks whether Slack has actually reached this deployment using the current signing secret. This is the final proof that setup works end to end, not just that credentials were entered.

**Data flow**: It looks for a stored verification marker in blob storage for the current workspace. If the marker is missing, unreadable, or malformed, it returns false. It then reads the current signing secret and compares its fingerprint with the fingerprint saved in the marker; only a match returns true.

**Call relations**: slack_connect_handler calls this after identity is known. The Slack surface writes the marker when it receives a valid signed request, and this function reads that marker to decide whether the setup state should be pending or connected.

*Call graph*: called by 1 (slack_connect_handler); 3 external calls (loads, signing_secret_fingerprint, url_verified_blob_key).


##### `slack_manifest_handler`  (lines 276–291)

```
async def slack_manifest_handler(ctx: ToolContext, args: SlackManifestInput) -> ToolResult
```

**Purpose**: Creates the ready-to-paste Slack app manifest for the manual setup path. This saves users from hand-building a Slack app and accidentally choosing the wrong permissions or event settings.

**Data flow**: It receives the desired bot display name, checks that it is short and plain enough for Slack, then reads the deployment’s public base URL. It builds the Slack events URL, fills the manifest template with the bot name and request URLs, and returns the YAML text as a tool result.

**Call relations**: This is the handler behind the slack_app_manifest tool. It uses _events_url so the generated Slack app sends events to the same endpoint used by the connection flow, and it returns the manifest directly for the agent to show to the member.

*Call graph*: calls 1 internal fn (_events_url); 3 external calls (__init__, __init__, match).


##### `slack_channels_handler`  (lines 294–317)

```
async def slack_channels_handler(ctx: ToolContext, args: SlackChannelsInput) -> ToolResult
```

**Purpose**: Searches the connected Slack workspace for conversations such as channels, group DMs, and one-to-one DMs. It lets the agent find a Slack destination by name, topic, purpose, or people involved, instead of needing an exact Slack ID upfront.

**Data flow**: It reads the stored Slack bot token, then reads the saved Slack identity to learn the bot user ID. It runs a Slack conversation search with the query text, converts the found conversations into JSON, includes whether the result was truncated, and returns that JSON as untrusted content because it comes from Slack workspace data written by people.

**Call relations**: This is the handler behind the slack_channels tool. It depends on Slack having already been connected by slack_connect_handler, uses read_identity to confirm that setup is complete, and hands the actual Slack paging and matching work to SlackConversationSearch.

*Call graph*: 5 external calls (__init__, __init__, __init__, dumps, read_identity).


##### `_token_diagnosis`  (lines 320–326)

```
def _token_diagnosis(error: str) -> str
```

**Purpose**: Turns a Slack authentication error code into a clearer explanation for the user. It is there so setup failures say what to fix instead of exposing only a short Slack error string.

**Data flow**: It receives an error code from Slack. If the code means the bot token was missing, revoked, inactive, or rejected, it returns advice to re-copy the Bot User OAuth Token. For other errors, it returns a generic Slack auth.test failure message with the error included.

**Call relations**: _derive_manifest_identity calls this when SlackIdentityResolver fails for the manifest path. The resulting message is then sent back through _state as part of the setup diagnosis.

*Call graph*: called by 1 (_derive_manifest_identity).


### `extensions/yc/ufo_ext_yc/cli.py`

`io_transport` · `request handling`

This file solves two linked problems. First, YC data is reached through an external program called the YC CLI, so the project needs a careful wrapper that can run that program, give it credentials, collect its output, and clean up afterward. Second, users need a way to authorize that CLI without pasting secrets into the chat, so the file also implements YC’s device login flow, where a user visits a YC web page and enters a code.

The main flow is like a temporary workbench. `YcCli` takes stored YC credentials, writes them into a short-lived private home directory, runs the `yc` executable there, captures its output, saves any refreshed token back to the credential store, and then deletes the temporary directory. This keeps credentials away from the normal machine environment.

`YcAuth` runs the login side. It can start an authorization by asking YC for a device code, sealing the pending authorization in the credential system, and returning the URL and code to show the user. It can also complete the authorization by exchanging the device code for real credentials once the user has approved it.

`YcRead` turns higher-level tool actions like “ask”, “search”, or “skills list” into concrete YC CLI commands. The top-level `yc_read` and `yc_auth` functions are the tool-facing entry points.

#### Function details

##### `YcDeviceAuthorization.validate_verification_url`  (lines 59–63)

```
def validate_verification_url(self) -> 'YcDeviceAuthorization'
```

**Purpose**: Checks that the verification link returned by YC really points to Y Combinator’s account site. This prevents the system from showing a user a login link hosted somewhere unexpected.

**Data flow**: It reads the parsed authorization response, chooses the complete verification URL if present or the basic verification URL otherwise, and checks its host name. If the host is `account.ycombinator.com`, the authorization object is accepted unchanged; otherwise validation fails.

**Call relations**: This runs automatically when `_start` parses YC’s device authorization response. It acts as a safety gate before the URL and user code are stored and shown back to the user.


##### `YcRunner.run`  (lines 93–93)

```
async def run(self, args: tuple[str, ...], session: str) -> str
```

**Purpose**: Defines the shape of any object that can run YC CLI-style commands. It is a promise that a runner accepts command arguments plus a session label and returns text output.

**Data flow**: A caller supplies a tuple of command words and a session string. Any concrete implementation must run the command somehow and produce a string result.

**Call relations**: `YcRead` depends on this interface instead of depending directly on one implementation. In normal use, `YcCli` is the runner, but this shape also makes testing or swapping the runner easier.


##### `YcAuth.run`  (lines 101–116)

```
async def run(self, action: Literal['start', 'complete'], session: str) -> YcAuthResult
```

**Purpose**: Starts or completes YC account authorization after checking that the request is allowed. It protects YC credential setup so only the workspace owner, in their private audience, can authorize the account.

**Data flow**: It receives an action, either `start` or `complete`, plus a session label. It checks that the YC extension context exists, credentials can be stored, the right credential slot is declared, the speaker is acting privately, and the speaker owns the workspace. After those checks, it sends the request to `_start` or `_complete` and returns their authorization status.

**Call relations**: `yc_auth` creates a `YcAuth` object and calls this function when the auth tool is invoked. This function is the guard at the front door; only after it passes does the work continue into `_start` or `_complete`.

*Call graph*: calls 2 internal fn (_complete, _start).


##### `YcAuth._start`  (lines 118–176)

```
async def _start(self, session: str) -> YcAuthResult
```

**Purpose**: Begins the YC device authorization flow, or reuses an already-started one when the same request is repeated. It returns the web link and user code that the person must use to approve access.

**Data flow**: It looks for an existing pending authorization in the extension store. If the same request already started one, it reopens the sealed pending data and returns the same URL and code. If credentials have already appeared since then, it clears the pending record and reports that YC is connected. Otherwise it asks YC’s authorization endpoint for a new device code, checks the response size and URL host, seals the pending data in the credential system, stores a small reference record, and returns `authorization_required` with the URL and code.

**Call relations**: `YcAuth.run` calls this when the requested action is `start`. It uses `_credential_digest` to notice whether credentials changed, `_headers` to label the YC HTTP request, and `_bound` to reject unexpectedly large responses before parsing them.

*Call graph*: calls 3 internal fn (_bound, _credential_digest, _headers); called by 1 (run); 5 external calls (__init__, __init__, __init__, __init__, time).


##### `YcAuth._complete`  (lines 178–230)

```
async def _complete(self, session: str) -> YcAuthResult
```

**Purpose**: Finishes the YC device authorization flow after the user has approved the code on YC’s website. It either stores the new credentials, reports that approval is still pending, or explains why the flow cannot continue.

**Data flow**: It reads the pending authorization record from the extension store. If no pending record exists, it checks whether valid credentials are already stored and returns `connected` if so. If a pending record exists, it reopens the sealed device code, rejects expired codes, and asks YC’s token endpoint for credentials. A successful response is validated and saved into the credential slot; a still-waiting response becomes `pending`; expired or failed responses clear the pending record and raise an error.

**Call relations**: `YcAuth.run` calls this when the requested action is `complete`. It relies on `_credential_digest` to detect credentials that were completed elsewhere, `_headers` to prepare the HTTP request, and `_bound` to keep token responses within a safe size.

*Call graph*: calls 3 internal fn (_bound, _credential_digest, _headers); called by 1 (run); 4 external calls (__init__, __init__, loads, time).


##### `YcAuth._credential_digest`  (lines 232–240)

```
async def _credential_digest(self) -> str | None
```

**Purpose**: Creates a safe fingerprint of the currently stored YC credentials. The fingerprint lets the code tell whether credentials changed without storing or comparing the secret text in the extension store.

**Data flow**: It tries to read the YC credential slot. If the slot is unset, it returns `None`. If credentials exist, it validates that they have the expected shape, hashes the raw credential text with SHA-256, and returns the hash string.

**Call relations**: `_start` and `_complete` call this when they need to decide whether an authorization is still pending or has already been completed by another attempt. It supports idempotency, meaning repeated requests do not accidentally create confusing duplicate login flows.

*Call graph*: called by 2 (_complete, _start); 1 external calls (sha256).


##### `YcAuth._headers`  (lines 242–247)

```
def _headers(self, session: str) -> dict[str, str]
```

**Purpose**: Builds the identifying HTTP headers used when talking to YC’s authorization service. These headers say which CLI version is acting and which UFO conversation session it belongs to.

**Data flow**: It receives a session string and returns a small dictionary of header names and values, including the fixed YC CLI version and the session identifier.

**Call relations**: `_start` uses this for the device-code request, and `_complete` uses it for the token-exchange request. It keeps the repeated header formatting in one place.

*Call graph*: called by 2 (_complete, _start).


##### `YcAuth._bound`  (lines 249–251)

```
def _bound(self, response: httpx.Response) -> None
```

**Purpose**: Rejects YC authorization HTTP responses that are too large. This is a safety limit so a bad or unexpected server response cannot make the process read or parse an oversized body.

**Data flow**: It receives an HTTP response and checks the length of its content. If the content is within the configured limit, nothing changes; if it is too large, it raises a YC CLI error.

**Call relations**: `_start` and `_complete` call this immediately after YC replies and before they parse the response body. It is the size guard for the HTTP side of this file.

*Call graph*: called by 2 (_complete, _start); 1 external calls (__init__).


##### `_read_bounded`  (lines 254–262)

```
async def _read_bounded(stream: asyncio.StreamReader, limit: int) -> bytes
```

**Purpose**: Reads output from a running process while enforcing a maximum size. It prevents the YC CLI from flooding memory with too much standard output or error text.

**Data flow**: It receives an asynchronous byte stream and a byte limit. It reads the stream in chunks, counts the total size, and stores the chunks. If the total grows past the limit, it raises an error; otherwise, when the stream ends, it returns all bytes joined together.

**Call relations**: `YcCli._execute` starts one copy of this helper for standard output and another for standard error. Together they keep both output channels under control while the external `yc` command runs.

*Call graph*: called by 1 (_execute); 2 external calls (__init__, read).


##### `YcCli.run`  (lines 270–280)

```
async def run(self, args: tuple[str, ...], session: str) -> str
```

**Purpose**: Runs the real YC CLI with stored credentials in a private temporary environment. It is the main safe wrapper around the external `yc` executable.

**Data flow**: It reads the YC credentials from the credential store and validates them. It creates a temporary home directory containing those credentials, runs the requested command there, then tries to save back any refreshed credentials that the CLI wrote. Finally, it deletes the temporary home directory and returns the command’s text output.

**Call relations**: `YcRead.run` calls this through the `YcRunner` interface. This function coordinates `_prepare_home`, `_execute`, and `_persist_refresh`, making sure cleanup happens even if the command fails.

*Call graph*: calls 2 internal fn (_execute, _persist_refresh); 1 external calls (to_thread).


##### `YcCli._prepare_home`  (lines 282–289)

```
def _prepare_home(self, raw: str) -> Path
```

**Purpose**: Creates a private temporary home directory for one YC CLI run and writes the credentials file into it. This keeps YC credentials isolated from the host machine’s normal home directory.

**Data flow**: It receives the raw credential JSON text. It creates a new temporary directory, locks down its permissions, creates `.yc/credentials.json` inside it, writes the credentials there, locks down that file, and returns the temporary directory path.

**Call relations**: `YcCli.run` calls this before launching the external CLI. The directory it returns is later passed to `_execute`, checked by `_persist_refresh`, and removed by `YcCli.run` during cleanup.

*Call graph*: 2 external calls (Path, mkdtemp).


##### `YcCli._execute`  (lines 291–332)

```
async def _execute(self, home: Path, args: tuple[str, ...], session: str) -> str
```

**Purpose**: Actually launches the `yc` command, waits for it, and turns its output into a string or a clear error. It also enforces time and output-size limits.

**Data flow**: It receives the temporary home directory, command arguments, and a session label. It builds a minimal environment with that home directory and session, starts the configured executable, reads standard output and standard error with size limits, and waits within a timeout. If the executable is missing, times out, writes too much, or exits with a nonzero status, it raises an error. Otherwise it decodes and returns standard output.

**Call relations**: `YcCli.run` calls this after preparing the temporary home. It delegates stream reading to `_read_bounded`, and if anything goes wrong while waiting, it kills the subprocess so no stray YC command keeps running.

*Call graph*: calls 1 internal fn (_read_bounded); called by 1 (run); 5 external calls (__init__, create_subprocess_exec, create_task, gather, timeout).


##### `YcCli._persist_refresh`  (lines 334–353)

```
async def _persist_refresh(self, home: Path, expected: str) -> None
```

**Purpose**: Saves refreshed YC credentials back to secure storage if the CLI updated them during the command. It tries to do this without overwriting a newer credential written by another concurrent run.

**Data flow**: It reads the credentials file from the temporary home and validates it. If it is unchanged, it does nothing. If it changed, it tries to rotate the credential store from the expected old value to the refreshed value. If that direct update fails, it reads the current stored credentials and only keeps trying while the refreshed copy is newer than the current copy. If the store cannot be reconciled, it raises an error.

**Call relations**: `YcCli.run` calls this after `_execute` finishes, even when the command raised an error. It is the bridge that carries token refreshes made by the external YC CLI back into UFO’s credential store.

*Call graph*: called by 1 (run); 2 external calls (__init__, to_thread).


##### `YcReadInput.validate_action`  (lines 366–373)

```
def validate_action(self) -> 'YcReadInput'
```

**Purpose**: Checks that a requested YC read action has the information it needs. It catches invalid tool inputs before they become malformed CLI commands.

**Data flow**: It reads the selected action plus optional query, entity, and name fields. It requires a query for `ask` and `search`, requires a name for `skills_read`, and only allows `entity` with `search`. If the fields fit the action, the input object is accepted; otherwise validation fails.

**Call relations**: This runs automatically when tool input is parsed into `YcReadInput`. It protects `YcRead.run`, which can then build commands knowing the required pieces are present.


##### `YcRead.run`  (lines 380–395)

```
async def run(self, args: YcReadInput, session: str) -> str
```

**Purpose**: Turns a high-level YC read request into the exact YC CLI command needed to answer it. It supports asking the YC agent, searching, listing skills, reading a skill, and retrieving tool context.

**Data flow**: It receives validated `YcReadInput` and a session label. Based on the action, it builds a tuple of command words such as `agent ... --json` or `skills list --json`. It then passes that command to its runner and returns the runner’s text output.

**Call relations**: `yc_read` creates `YcRead` with a `YcCli` runner and calls this function. This function does not run processes itself; it translates the tool request and hands the command to the runner.


##### `yc_read`  (lines 398–404)

```
async def yc_read(ctx: ToolContext, args: YcReadInput) -> ToolResult
```

**Purpose**: Provides the tool-facing entry point for reading YC information. It connects the UFO tool call to the authenticated YC CLI wrapper and returns the CLI output as tool text.

**Data flow**: It receives the tool context and validated read arguments. It checks that the YC extension context exists, builds a `YcCli` using the extension’s credential access, wraps it in `YcRead`, and runs the requested action with a conversation-based session string. The resulting text is placed into a `ToolResult` as text content.

**Call relations**: The tool system calls this when a YC read tool is invoked. It hands the real work to `YcRead.run`, which then uses `YcCli.run` to execute the external YC command.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).


##### `yc_auth`  (lines 407–414)

```
async def yc_auth(ctx: ToolContext, args: YcAuthInput) -> ToolResult
```

**Purpose**: Provides the tool-facing entry point for YC account authorization. It starts or completes the device login flow and returns a small JSON status message for the caller to show or act on.

**Data flow**: It receives the tool context and auth input. It checks that the YC extension context exists, opens a short-lived HTTP client, creates `YcAuth`, and runs either the start or complete action with a conversation-based session string. The resulting authorization status is serialized to JSON and returned as text content in a `ToolResult`.

**Call relations**: The tool system calls this when a user asks to connect or finish connecting YC. It hands off to `YcAuth.run`, which performs permission checks and then calls `_start` or `_complete`.

*Call graph*: 4 external calls (__init__, __init__, __init__, AsyncClient).


### Evaluation connectors
The evaluation environment provides deterministic fake mailbox, calendar, and code-search connectors that exercise the same connector pathway safely.

### `extensions/eval_env/ufo_ext_eval_env/__init__.py`

`other` · `test and evaluation setup`

This file is the entry point marker for the `ufo_ext_eval_env` Python package. It does not define any code itself, but its short module note tells readers what this package is for: a deterministic evaluation environment. “Deterministic” means it behaves the same way every time, which is important when testing or comparing system behavior. Instead of talking to real email inboxes or real calendars, this package provides fake connector providers for a mailbox and a calendar. That is like using a practice driving course instead of real city traffic: the system can exercise the same skills, but the surroundings are controlled and repeatable. Without this package marker, Python would not treat the directory as a normal importable package in the same way, and newcomers would also lose the quick explanation of why this folder exists.


### `extensions/eval_env/ufo_ext_eval_env/manifest.py`

`domain_logic` · `evaluation connector discovery and tool-call handling`

This file builds a small fake-but-realistic outside world for evaluating an agent. Instead of letting an evaluation touch a real email account or calendar, it provides test-only connector providers for email, calendar, and code search. The important point is that these are not bypass mocks: the agent still discovers tools, asks for schemas, and calls tools through the same connector broker route used in production. The difference is only the backing data: emails and calendar events live in workspace-scoped tables owned by this extension, and code-search answers are seeded as stored fixtures.

Think of it like a driving simulator connected to the real steering wheel and pedals. The road is controlled, but the driver still uses the real controls. That lets graders seed an inbox, run a conversation, and then inspect the exact rows the agent changed.

The file defines input shapes for each tool, a catalog of available tools, and an EvalEnvBroker that performs the actual actions. Sending email inserts a sent message. Listing email reads matching rows. Calendar tools create, list, update, or cancel events. Code search returns a pre-seeded response exactly as written, and fails loudly if no fixture exists. At the end, manifest() registers the three connector providers so the evaluation pack can expose them.

#### Function details

##### `_transaction`  (lines 171–175)

```
def _transaction()
```

**Purpose**: Opens a workspace-aware storage transaction for this extension. The broker uses it whenever it needs to read or write the evaluation email and calendar tables safely.

**Data flow**: It takes no direct input. It creates an extension context with this extension’s scoped store and no declared credential access, then returns a transaction object. Callers use that transaction to run database statements, and the transaction commits or rolls back as the surrounding context decides.

**Call relations**: The email and calendar operations call this before touching stored rows. It is the shared doorway into durable evaluation storage for sending email, listing email, creating events, listing events, and changing events.

*Call graph*: called by 5 (_change_event, _create_event, _list_emails, _list_events, _send_email); 3 external calls (__init__, __init__, __init__).


##### `_moment`  (lines 178–182)

```
def _moment(value: str) -> datetime
```

**Purpose**: Turns a text timestamp into a timezone-aware datetime value. This keeps calendar event times consistent even if the input leaves out a timezone.

**Data flow**: It receives an ISO 8601 time string. It parses the string into a datetime; if the parsed value has no timezone, it treats it as UTC. It returns the resulting datetime object for storage or update.

**Call relations**: Calendar creation and update call this when accepting start and end times from tool arguments. It prepares human-readable tool input for the database fields used by event records.

*Call graph*: called by 2 (_create_event, _update_event); 1 external calls (fromisoformat).


##### `EvalEnvBroker.tools`  (lines 190–198)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Returns the tools available for one eval provider, optionally filtered by a search phrase. This is how the connector can answer “what can this provider do?”

**Data flow**: It receives a workspace id, provider name, and query text. It looks up that provider’s tool catalog, trims and lowercases the query, then returns either all tools or only tools whose slug or description matches. If filtering finds nothing, it falls back to the full catalog.

**Call relations**: EvalEnvBroker.search calls this when a caller searches for tools. It sits at the discovery stage before any actual email, calendar, or code-search action is run.

*Call graph*: called by 1 (search).


##### `EvalEnvBroker.schema`  (lines 200–204)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Finds the detailed description and input schema for a named tool. If the requested tool does not exist, it reports that clearly.

**Data flow**: It receives a workspace id, provider name, and tool slug. It scans the provider’s catalog for a matching slug. It returns the matching BrokerTool, or raises UnknownBrokerTool if none is found.

**Call relations**: This is used during tool-description lookup in the connector flow. It validates that callers only ask for tools that the eval provider actually exposes.

*Call graph*: 1 external calls (__init__).


##### `EvalEnvBroker.execute`  (lines 206–245)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Routes an actual tool call to the correct email, calendar, or code-search implementation. It is the central switchboard for all eval environment tool execution.

**Data flow**: It receives the workspace, provider, tool slug, raw argument mapping, account id, and optional idempotency key. It validates the raw arguments against the right input model, then calls the matching private method. It returns that method’s result as a plain dictionary, or raises UnknownBrokerTool when the provider/slug combination is not recognized.

**Call relations**: The real connector dispatch calls this when the agent invokes a tool. This function then hands off to _send_email, _list_emails, _create_event, _list_events, _update_event, _cancel_event, or _search_code depending on the requested action.

*Call graph*: calls 7 internal fn (_cancel_event, _create_event, _list_emails, _list_events, _search_code, _send_email, _update_event); 1 external calls (__init__).


##### `EvalEnvBroker._search_code`  (lines 247–255)

```
async def _search_code(self, args: SearchCodeArgs) -> dict[str, object]
```

**Purpose**: Returns the exact code-search response that an evaluation previously seeded for a query. This lets tests control not only the search results, but also the exact size and shape of the response.

**Data flow**: It receives validated search arguments containing a query. It reads scoped storage using a key made from the code-search fixture prefix plus that query. If the stored value is a dictionary, it copies and returns it; otherwise it raises an error so a missing fixture cannot silently look like an empty result.

**Call relations**: EvalEnvBroker.execute calls this for the search_code tool. Unlike email and calendar tools, it does not mutate tables; it reads a prepared fixture from ScopedStore.

*Call graph*: called by 1 (execute); 1 external calls (__init__).


##### `EvalEnvBroker._send_email`  (lines 257–272)

```
async def _send_email(self, workspace_id: UUID, args: SendEmailArgs) -> dict[str, object]
```

**Purpose**: Simulates sending an email by writing a new message into the evaluation mailbox’s sent folder. This gives graders a durable record of what the agent chose to send.

**Data flow**: It receives a workspace id and validated email fields: recipients, subject, and body. It creates a new id, records the sender as the eval assistant address, stores the message in the sent folder with the current UTC time, and returns the new id, sent status, and recipient list.

**Call relations**: EvalEnvBroker.execute calls this for the send_email tool. It uses _transaction to write the row so later grading code or list_emails can see the same sent message.

*Call graph*: calls 1 internal fn (_transaction); called by 1 (execute); 3 external calls (now, insert, uuid4).


##### `EvalEnvBroker._list_emails`  (lines 274–309)

```
async def _list_emails(self, workspace_id: UUID, args: ListEmailsArgs) -> dict[str, object]
```

**Purpose**: Reads emails from the evaluation mailbox, newest first, with optional text filtering. This lets the agent inspect seeded inbox messages or its own sent messages.

**Data flow**: It receives a workspace id and validated listing options: folder, query, and limit. It builds database conditions for the workspace and folder, adds a case-insensitive sender/subject/body search if requested, reads matching rows, and returns them as simple email dictionaries.

**Call relations**: EvalEnvBroker.execute calls this for the list_emails tool. It uses _transaction for the database read and returns data in the shape the connector tool response expects.

*Call graph*: calls 1 internal fn (_transaction); called by 1 (execute); 2 external calls (or_, select).


##### `EvalEnvBroker._create_event`  (lines 311–325)

```
async def _create_event(self, workspace_id: UUID, args: CreateEventArgs) -> dict[str, object]
```

**Purpose**: Creates a confirmed calendar event in the evaluation calendar. This records the event so later tool calls and graders can observe it.

**Data flow**: It receives a workspace id and validated event fields: title, start, end, and attendees. It creates a new id, converts the start and end strings into datetimes, inserts a confirmed event row, and returns the event id with confirmed status.

**Call relations**: EvalEnvBroker.execute calls this for the create_event tool. It relies on _moment to normalize times and _transaction to make the inserted event durable.

*Call graph*: calls 2 internal fn (_moment, _transaction); called by 1 (execute); 2 external calls (insert, uuid4).


##### `EvalEnvBroker._list_events`  (lines 327–340)

```
async def _list_events(self, workspace_id: UUID, args: ListEventsArgs) -> dict[str, object]
```

**Purpose**: Reads calendar events for the evaluation workspace, optionally filtered by title. It lets the agent see both active and cancelled events in chronological order.

**Data flow**: It receives a workspace id and validated listing options: query and limit. It selects matching event rows for that workspace, adds a title search if requested, orders by start time, limits the result count, and converts each row into a response dictionary.

**Call relations**: EvalEnvBroker.execute calls this for the list_events tool. It uses _transaction to read rows and _event_json to turn each database row into the public response format.

*Call graph*: calls 2 internal fn (_event_json, _transaction); called by 1 (execute); 1 external calls (select).


##### `EvalEnvBroker._update_event`  (lines 342–354)

```
async def _update_event(self, workspace_id: UUID, args: UpdateEventArgs) -> dict[str, object]
```

**Purpose**: Applies requested changes to an existing calendar event. It supports changing only the fields the caller provides, leaving the rest untouched.

**Data flow**: It receives a workspace id and validated update arguments. It builds a changes dictionary from any provided title, start, end, or attendees fields, converting times as needed. If nothing was provided to change, it raises an error; otherwise it passes the changes to _change_event and returns the updated event.

**Call relations**: EvalEnvBroker.execute calls this for the update_event tool. This function prepares the patch, while _change_event performs the shared database update and read-back.

*Call graph*: calls 2 internal fn (_change_event, _moment); called by 1 (execute).


##### `EvalEnvBroker._cancel_event`  (lines 356–357)

```
async def _cancel_event(self, workspace_id: UUID, args: CancelEventArgs) -> dict[str, object]
```

**Purpose**: Marks an existing calendar event as cancelled instead of deleting it. Keeping the row visible mirrors many real calendars and lets tests verify that cancellation happened.

**Data flow**: It receives a workspace id and validated cancel arguments containing an event id. It creates a small change saying the status should become cancelled, then returns the updated event from _change_event.

**Call relations**: EvalEnvBroker.execute calls this for the cancel_event tool. It reuses _change_event so cancellation follows the same workspace checks and read-back behavior as other event edits.

*Call graph*: calls 1 internal fn (_change_event); called by 1 (execute).


##### `EvalEnvBroker._change_event`  (lines 359–378)

```
async def _change_event(self, workspace_id: UUID, event_id: str, changes: dict[str, object]) -> dict[str, object]
```

**Purpose**: Performs the common database work for updating or cancelling an event. It also protects workspace boundaries by only changing an event that belongs to the calling workspace.

**Data flow**: It receives a workspace id, event id text, and a dictionary of field changes. It converts the event id to a UUID, updates the matching row in the event table, checks that exactly one row changed, then reads the row back and returns it as event JSON. If no matching event exists in that workspace, it raises an error.

**Call relations**: _update_event and _cancel_event both call this after deciding what should change. It uses _transaction for the write/read sequence and _event_json to format the final response.

*Call graph*: calls 2 internal fn (_event_json, _transaction); called by 2 (_cancel_event, _update_event); 3 external calls (select, update, UUID).


##### `EvalEnvBroker._event_json`  (lines 380–388)

```
def _event_json(self, row: sa.Row) -> dict[str, object]
```

**Purpose**: Turns a stored calendar event row into the plain dictionary returned by calendar tools. This keeps event responses consistent across listing, updating, and cancelling.

**Data flow**: It receives a database row with event fields. It converts the id to text, formats start and end times as ISO strings, and copies the title, attendees, and status into a dictionary. The result is ready to send back as a tool response.

**Call relations**: _list_events calls this for every event it returns, and _change_event calls it after an update or cancellation. It is the final formatting step for calendar event output.

*Call graph*: called by 2 (_change_event, _list_events).


##### `EvalEnvBroker.file_outputs`  (lines 390–391)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Declares that these eval tools never produce downloadable files. This satisfies the broker interface while making the behavior explicit.

**Data flow**: It receives a tool response dictionary but does not inspect it. It always returns an empty tuple, meaning there are no file attachments or generated files to expose.

**Call relations**: The connector framework can ask a broker for file outputs after tool execution. For this eval environment, the answer is always none.


##### `EvalEnvBroker.stage_upload`  (lines 393–402)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Rejects file uploads for all eval environment providers. Email, calendar, and code search in this test world are text-and-row based only.

**Data flow**: It receives upload details such as workspace, provider, tool slug, filename, media type, and checksum. Instead of creating an upload target, it raises a runtime error saying uploads are not accepted.

**Call relations**: This is present because the broker interface includes upload staging. If something tries to upload a file to an eval provider, this function stops it immediately rather than pretending upload support exists.


##### `EvalEnvBroker.search`  (lines 404–405)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Wraps tool discovery results in the broker search response shape. This is used when a caller searches available external tools for a provider.

**Data flow**: It receives a workspace id, provider name, and query. It asks tools() for the matching tool list, then returns a BrokerSearch object containing those tools.

**Call relations**: This is part of the connector discovery flow before execution. It delegates the actual matching rules to EvalEnvBroker.tools and packages the answer for the connector system.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `EvalEnvBroker.credential`  (lines 407–408)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a simple synthetic bearer credential for an eval account. It gives the connector framework something credential-shaped without contacting a real auth service.

**Data flow**: It receives a workspace id, provider name, and account id. It builds a Credential whose bearer token is the string eval-env: followed by the account id, and returns it.

**Call relations**: The connector system can request credentials before calling a provider. In this eval extension, the credential is deterministic and local, matching the controlled-test design.

*Call graph*: 1 external calls (__init__).


##### `_EvalEnvOAuth.authorize_url`  (lines 419–420)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds a placeholder authorization URL for the eval provider. The normal connect flow is not expected during evaluations, but the provider still needs an OAuth-like descriptor.

**Data flow**: It receives a state value and redirect URI. It combines them with the provider host into a URL that looks like an authorization endpoint, then returns that string.

**Call relations**: Connector registration includes this OAuth object. If code asks where to send a user for authorization, this method supplies a harmless eval-domain URL.


##### `_EvalEnvOAuth.exchange`  (lines 422–425)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Returns the fixed eval account after an OAuth exchange request. It keeps the interface honest without relying on a real OAuth server.

**Data flow**: It receives an authorization code, redirect URI, workspace id, and state. It ignores the real-world meaning of those values and returns an OAuthAccount with the known eval account id.

**Call relations**: Although evaluations usually seed grants directly and do not drive this flow, the connector registry requires an exchange method. This method provides the minimal successful handoff if it is ever called.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 428–450)

```
def manifest() -> Manifest
```

**Purpose**: Creates the extension manifest that registers the eval email, calendar, and code-search providers. This is the file’s public entry point for the extension loader.

**Data flow**: It creates one EvalEnvBroker, then builds three ConnectorProvider entries with labels, OAuth stubs, and the shared broker. It returns a Manifest containing the extension name, version, and provider list.

**Call relations**: The extension system calls this to discover what the eval_env extension offers. The returned manifest is what makes the three eval providers available to the assistant_eval pack.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).
