# Generic Connector Objects and External Tool Surfaces  `stage-10.2.3`

This stage is shared behind-the-scenes support for letting agents work with outside services without the rest of the system needing to know each service in detail. It turns connected accounts and external tools into normal workspace items, so they can be inspected, shared, attached to agents, revoked, or disconnected in the same way as other objects.

The package marker file simply makes the connector code importable. The objects file is the “front desk” for connected accounts: it shows accounts like GitHub or Slack as workspace objects and checks who owns them and which agents are allowed to use them. The tools file is the “switchboard”: an agent can ask what connector tools are available, call one safely, move files between the workspace and the connector broker, trim oversized encoded results, and add clear attribution when posting to Slack. The MCP file adds another doorway, letting agents discover and call tools from workspace-configured MCP servers, which are outside services that publish tool lists in a standard format.

## Files in this stage

### Connector Package Setup
Package initialization makes the connector extension modules importable before their object and tool surfaces are used.

### `extensions/connectors/ufo_ext_connectors/__init__.py`

`other` · `import time`

This is an empty Python package marker file. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. That matters because other parts of the project may need to refer to code inside `extensions/connectors/ufo_ext_connectors` using normal Python import paths. Think of it like putting a label on a drawer: the drawer may contain useful tools, and the label lets the rest of the system find them reliably. Because this file contains no code, it does not run setup steps, expose helper functions, or change any state. Its value is structural: without it, depending on the Python version and import style, code that expects this directory to behave like a package could fail to import its connector modules.


### Connector Workspace Objects
Connected third-party accounts and agent permissions are represented as normal workspace objects with sharing, attachment, revocation, and ownership controls.

### `extensions/connectors/ufo_ext_connectors/objects.py`

`domain_logic` · `request handling`

A “connection” here means a member has linked an outside provider account, such as an external service account. A “connector grant” is the separate permission that lets one agent use that connection. This split matters: disconnecting the account should remove it everywhere, but revoking one grant should only stop one agent from using it.

This file turns those two ideas into object types the rest of the system can read and edit. It defines the shape of a connection record, the shape of a grant record, and two object stores: `ConnectionObjects` for member-owned accounts and `ConnectorGrantObjects` for per-agent access. The stores read summary rows from the grants layer, turn them into friendly object rows, and return detailed object views when asked.

The file also protects dangerous actions. Creating a real connection is refused here because linking an outside account requires third-party consent, so users must go through `connect_account`. Deleting a connection disconnects it everywhere, and deleting a grant revokes only that agent’s access. Changing a grant is limited mostly to flipping whether it is shared. A speaking member is required for these actions, meaning the system needs to know which real workspace member is taking responsibility.

#### Function details

##### `_AccountSummary.provider`  (lines 51–51)

```
def provider(self) -> str
```

**Purpose**: This is a small interface promise saying that any account summary used here must expose a provider name. The provider is the outside service the account belongs to.

**Data flow**: An account-like summary object is expected to have a `provider` value → code can read that provider without caring about the exact summary type → the provider string is used when building stable object names.

**Call relations**: This property is part of the `_AccountSummary` protocol used by `_named`. It lets `_named` work with both connection summaries and grant summaries as long as they provide the same basic account identity fields.


##### `_AccountSummary.account_id`  (lines 54–54)

```
def account_id(self) -> str
```

**Purpose**: This is a small interface promise saying that any account summary used here must expose the account’s identifier. Together with the provider, it uniquely names the connected account.

**Data flow**: An account-like summary object is expected to have an `account_id` value → code reads that value alongside the provider → the pair is used to build the object name shown to the workspace.

**Call relations**: This property supports `_named`, which is shared by both connection and grant listing code. It keeps the naming helper independent of the exact row class returned by the grants layer.


##### `_named`  (lines 57–58)

```
def _named(rows: tuple[SummaryT, ...]) -> dict[str, SummaryT]
```

**Purpose**: This helper gives account summary rows their workspace object names. It uses each row’s provider and account ID to make a consistent name, like putting a label on each file in a cabinet.

**Data flow**: It receives a tuple of summary rows → for each row it asks `account_object_name` to make the display/storage name from `provider` and `account_id` → it returns a dictionary where each name points back to its original row.

**Call relations**: Both `ConnectionObjects._member_rows` and `ConnectorGrantObjects._member_rows` call this when turning raw grant-layer summaries into object rows. It hands the naming detail off to `account_object_name` so both object kinds use the same naming convention.

*Call graph*: called by 2 (_member_rows, _member_rows); 1 external calls (account_object_name).


##### `ConnectionObjects._member_rows`  (lines 69–86)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: This builds the list of connection objects a member can see. Each row describes one connected outside account and records who owns it.

**Data flow**: It reads connection summaries from the grants layer → names them with `_named` → wraps each one as an `OwnedRow` with a readable summary and a generated owner record tied to the connection’s stored ID → returns all rows as a tuple.

**Call relations**: The member-readable object system calls this when it needs to list available `connection` objects. It relies on `connection_summaries` for the raw data and on `OwnedRow` and `GeneratedObjectOwner` to present that data in the project’s object format.

*Call graph*: calls 1 internal fn (_named); 3 external calls (__init__, __init__, connection_summaries).


##### `ConnectionObjects._member_object`  (lines 88–106)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[ConnectionSpec] | None
```

**Purpose**: This returns the detailed object view for one connected account. It turns the stored connection summary into a small, strict spec that says which provider and account ID it represents.

**Data flow**: It receives an object name and owner marker → reloads current connection summaries → finds the row whose stored generation ID matches the owner marker → returns an `ObjectDetail` with the connection spec and timestamps, or `None` if the row no longer exists.

**Call relations**: The object system calls this after a connection row has been selected or fetched by name. It hands back a `ConnectionSpec` so callers see only the safe object shape, not the full internal grants record.

*Call graph*: 3 external calls (__init__, __init__, connection_summaries).


##### `ConnectionObjects._status`  (lines 108–123)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: This provides extra live status for a connection, such as who owns it, whether it is shared, what host it belongs to, and which agents use it. It is meant for inspection rather than editing.

**Data flow**: It receives the current tool context, object name, and owner marker → reloads connection summaries → finds the matching connection by generation ID → returns a plain dictionary of status fields, or `None` if the connection disappeared.

**Call relations**: The object/tool layer calls this when it wants status information beyond the basic object spec. It reads from `connection_summaries` and does not change anything.

*Call graph*: 1 external calls (connection_summaries).


##### `ConnectionObjects._apply_owned`  (lines 125–133)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ConnectionSpec, old: ConnectionSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: This deliberately refuses to create or edit a connection through the generic object apply path. Connecting an outside account requires third-party authorization, so users must use the dedicated `connect_account` flow.

**Data flow**: It receives the requested connection spec plus any existing object information → ignores the requested change because this path is not allowed → raises `VerbNotSupported` with a message explaining the correct route.

**Call relations**: The object system would call this for create or update attempts on `connection` objects. Instead of handing off to the grants layer, it stops the action immediately to protect the consent process.

*Call graph*: 1 external calls (__init__).


##### `ConnectionObjects._delete_owned`  (lines 135–145)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: This disconnects a connected account. Because that can affect every agent using the account, it requires a real speaking member and the grants service to be available.

**Data flow**: It receives the tool context, object name, and owner marker → checks that grant operations are configured and that a speaker member is known → asks the grants layer to disconnect the connection identified by the owner’s generation ID → finishes if successful, or raises an error if the connection changed or could not be disconnected.

**Call relations**: The object system calls this when someone deletes a `connection` object. It hands the actual disconnect work to `ctx.grants.disconnect`, using the speaker member as the actor for permission and audit purposes.

*Call graph*: 1 external calls (__init__).


##### `ConnectorGrantObjects._admin_can_apply`  (lines 156–157)

```
def _admin_can_apply(self, old: ConnectorGrantSpec, spec: ConnectorGrantSpec) -> bool
```

**Purpose**: This answers a narrow permission question: may an admin apply this grant change? The only admin edit allowed here is making a shared grant private again.

**Data flow**: It receives the old grant spec and the requested new spec → checks whether the old grant was shared and whether the only requested change is `shared: false` → returns `true` for that one allowed case, otherwise `false`.

**Call relations**: The broader member-readable object framework can call this while deciding whether an admin is allowed to update a `connector_grant`. It uses the spec model’s copy helper to compare the requested edit against the old grant with only the shared flag changed.

*Call graph*: 1 external calls (model_copy).


##### `ConnectorGrantObjects._member_rows`  (lines 159–176)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: This builds the list of connector grant objects a member can see. Each row represents one agent’s access to one connected provider account.

**Data flow**: It reads grant summaries from the grants layer → gives each summary a stable account-based name using `_named` → wraps each result as an `OwnedRow` with owner information and whether the grant is shared → returns the rows as a tuple.

**Call relations**: The object system calls this when listing `connector_grant` objects. It mirrors `ConnectionObjects._member_rows`, but reads from `grant_summaries` because it is listing agent access edges rather than base account connections.

*Call graph*: calls 1 internal fn (_named); 3 external calls (__init__, __init__, grant_summaries).


##### `ConnectorGrantObjects._member_object`  (lines 178–219)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[ConnectorGrantSpec] | None
```

**Purpose**: This returns the detailed object view for one agent’s grant to use a connected account. It also adds links that explain what agent the grant belongs to and, when private, which connection it opens.

**Data flow**: It receives an object name and owner marker → reloads grant summaries → finds the matching grant by stored generation ID → builds a `ConnectorGrantSpec` with provider, account ID, and shared flag → returns an `ObjectDetail` with timestamps and object links, or `None` if the grant no longer exists.

**Call relations**: The object system calls this when fetching a specific `connector_grant`. It creates a `scoped_to` link to the agent that holds the grant, and for private grants it also creates an `access_to` link back to the underlying `connection` object.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, account_object_name, grant_summaries).


##### `ConnectorGrantObjects._status`  (lines 221–236)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: This provides extra live status for a connector grant, including the owner, host, target agent, and whether the grant is shared. It helps users understand who can use an account and why.

**Data flow**: It receives the current tool context, object name, and owner marker → reloads grant summaries → finds the matching grant by generation ID → returns a plain dictionary of status values, or `None` if the grant has disappeared.

**Call relations**: The object/tool layer calls this for status inspection of a `connector_grant`. It reads from `grant_summaries` and does not modify the grant.

*Call graph*: 1 external calls (grant_summaries).


##### `ConnectorGrantObjects._apply_owned`  (lines 238–288)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ConnectorGrantSpec, old: ConnectorGrantSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: This creates or updates a connector grant, but only within safe limits. It can attach an existing connection to an agent, or change whether an existing grant is shared; it cannot create a brand-new third-party connection.

**Data flow**: It receives the requested grant spec plus any old spec and owner marker → if this is a new grant, it checks for a grants service and a speaking member, then asks the grants layer to attach an existing account to the current conversation’s agent → if this is an existing grant, it reloads the current grant, refuses changes to provider or account ID, and only applies a changed `shared` flag → it returns nothing on success or raises a clear error if the operation is not allowed or the grant changed underneath it.

**Call relations**: The object system calls this for create or update operations on `connector_grant` objects. It reads current state through `grant_summaries`, uses `VerbNotSupported` to block attempts that should go through `connect_account`, and hands real changes to the grants layer through attach or share-setting operations.

*Call graph*: 5 external calls (__init__, __init__, __init__, model_copy, grant_summaries).


##### `ConnectorGrantObjects._delete_owned`  (lines 290–300)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: This revokes one agent’s access to a connected account. It leaves the underlying connection, and any other agents’ grants, in place.

**Data flow**: It receives the tool context, object name, and owner marker → checks that grant operations are configured and that a speaker member is known → asks the grants layer to revoke the grant identified by the owner’s generation ID → finishes if successful, or raises an error if the grant changed or could not be revoked.

**Call relations**: The object system calls this when someone deletes a `connector_grant` object. It delegates the actual revocation to `ctx.grants.revoke`, using the speaker member as the actor so ownership and admin rules can be enforced.

*Call graph*: 1 external calls (__init__).


### External Tool Invocation
Generic connector brokers and workspace-configured MCP servers expose discoverable external tools that agents can safely call.

### `extensions/connectors/ufo_ext_connectors/tools.py`

`orchestration` · `tool discovery and connector tool execution during a turn`

A connector broker is like a front desk for many outside services: the agent asks the broker what is available, then asks it to run one chosen tool using an already connected account. This file defines that front-desk interaction. It can list connectors, discover the real tool names and input formats for one connector, search for a tool by goal, and execute a selected tool.

The file also protects the workspace boundary. If a connector tool needs a workspace file, the code stages that file through the sandbox instead of letting the main server process read and upload the bytes itself. If a connector returns files, the sandbox downloads them into a safe `connector_files` folder. If a provider returns base64, which is text that hides raw bytes, this file decodes it into readable text when small, or writes it to a workspace file when large or binary.

Finally, it keeps results usable. Repeated JSON objects can be replaced with `same_as` pointers so the agent does not waste context rereading identical data. Slack sends get a small “Sent using ufo” footer, so messages posted through connectors are visibly attributed.

#### Function details

##### `list_external_tools`  (lines 239–275)

```
async def list_external_tools(ctx: ToolContext, args: ListExternalToolsInput) -> ToolResult
```

**Purpose**: Finds external connector sources that match the user’s search words, such as `github` or `slack`. It returns connector IDs, labels, and any connected accounts the current agent can already use.

**Data flow**: It receives the current tool context and search queries. It reads the connector registry from the context, reads active account grants, searches both local registry entries and the broker catalog, removes duplicates, and returns a JSON tool result containing matching connectors.

**Call relations**: This is one of the public connector tools. It starts by using `_registry` to get the live connector registry and `_connected_accounts` to add account information, asks broker catalogs in parallel through `asyncio.gather`, and finishes through `_json_result` so the caller receives normal tool output.

*Call graph*: calls 3 internal fn (_connected_accounts, _json_result, _registry); 1 external calls (gather).


##### `_connected_accounts`  (lines 278–292)

```
async def _connected_accounts(ctx: ToolContext) -> dict[str, list[JsonValue]]
```

**Purpose**: Collects the connector accounts that this agent is allowed to use. This lets discovery results say not just that Slack or GitHub exists, but whose account is connected and whether it is shared.

**Data flow**: It reads grants from the tool context. If there are no grants, it returns an empty mapping; otherwise it groups active grants by provider and records account ID, owner email, and sharing status.

**Call relations**: It is used by `list_external_tools` while building connector search results, so connector listings include account availability without a separate lookup.

*Call graph*: called by 1 (list_external_tools).


##### `describe_external_tools`  (lines 295–317)

```
async def describe_external_tools(ctx: ToolContext, args: DescribeExternalToolsInput) -> ToolResult
```

**Purpose**: Shows the real tools available inside one connector, and can fetch full input schemas for exact tool names. This prevents the agent from guessing tool names or parameters.

**Data flow**: It receives a connector source ID, optional exact tool names, and an optional search query. It asks the broker for schemas for named tools, records unresolved names, optionally searches the connector catalog, adds fallback listings when needed, and returns a JSON result.

**Call relations**: This public tool uses `_registry` to find the connector, `_tool_json` to format schemas, `_discovery_query` to turn unresolved guessed names into useful search words, `_discovered_rows` to prepare discovery rows, and `_json_result` to return the answer.

*Call graph*: calls 5 internal fn (_discovered_rows, _discovery_query, _json_result, _registry, _tool_json).


##### `attribution_stripped`  (lines 320–324)

```
def attribution_stripped(text: str) -> str
```

**Purpose**: Removes any Slack attribution footer that this deployment may have written. This helps inbound Slack text be read as the human’s message, not as the agent mentioning itself.

**Data flow**: It receives message text. It applies the attribution-matching pattern throughout the text and returns the text with those matching pieces removed.

**Call relations**: The function is a small utility for Slack message reading. It does not call other project functions, but it shares the attribution rules used by the Slack sending path in this file.


##### `attributed_arguments`  (lines 327–354)

```
def attributed_arguments(arguments: dict[str, JsonValue], subject: str) -> dict[str, JsonValue]
```

**Purpose**: Adds a Slack footer to outgoing message arguments when there is a real message body and no footer already present. The footer makes connector-posted Slack messages visibly say they were sent using ufo.

**Data flow**: It receives the argument dictionary for a Slack tool call and a footer subject. It checks for an existing attribution, then either appends a footer to existing blocks or converts `markdown_text` or `text` into Slack blocks and adds the footer. If it cannot find a message body, it returns the original arguments.

**Call relations**: `slack_attributed` calls this after deciding a connector call is a Slack send. It relies on `_carries_attribution` to avoid duplicate footers, `_appended_blocks` to add to existing block payloads, and `_body_blocks` to turn plain body arguments into Slack block form.

*Call graph*: calls 3 internal fn (_appended_blocks, _body_blocks, _carries_attribution); called by 1 (slack_attributed).


##### `_body_blocks`  (lines 357–385)

```
def _body_blocks(arguments: dict[str, JsonValue]) -> list[JsonValue] | None
```

**Purpose**: Turns a Slack message body written as `markdown_text` or `text` into Slack block objects so a footer block can follow it. It keeps each body in the Slack block type that renders its markup correctly.

**Data flow**: It reads the arguments dictionary. If it finds non-empty `markdown_text`, it returns one markdown block; if it finds non-empty `text`, it splits it into section blocks within Slack’s per-block size limit. If neither is present, it returns nothing.

**Call relations**: `attributed_arguments` uses this only when there are no existing `blocks`. It hands back the body blocks that `attributed_arguments` combines with the attribution footer.

*Call graph*: called by 1 (attributed_arguments).


##### `_appended_blocks`  (lines 388–409)

```
def _appended_blocks(value: JsonValue, footer: dict[str, JsonValue]) -> JsonValue | None
```

**Purpose**: Adds the attribution footer to an existing Slack `blocks` argument when that argument can be safely understood. It supports both a real list of blocks and a JSON string version of that list.

**Data flow**: It receives the existing `blocks` value and the footer block. For a non-empty list, it appends the footer. For a string, it tries to parse it as JSON, also trying URL-decoded JSON, checks that it is a list without attribution already, appends the footer, and serializes it back in the same style. If parsing fails, it returns nothing.

**Call relations**: `attributed_arguments` calls this when a Slack send already provides `blocks`. `_appended_blocks` uses `_carries_attribution` to avoid stacking footers and standard JSON and URL quoting helpers to preserve the original representation.

*Call graph*: calls 1 internal fn (_carries_attribution); called by 1 (attributed_arguments); 4 external calls (dumps, loads, quote, unquote).


##### `_carries_attribution`  (lines 412–421)

```
def _carries_attribution(value: JsonValue) -> bool
```

**Purpose**: Checks whether a nested Slack argument value already contains the ufo attribution footer. This prevents repeated sends or edits from piling up duplicate footers.

**Data flow**: It receives any JSON-like value. It searches strings directly, recursively checks lists item by item, recursively checks dictionary values, and returns true if any part contains a full attribution line.

**Call relations**: `attributed_arguments` uses it before adding any footer, and `_appended_blocks` uses it after parsing serialized blocks. It is the shared guard that keeps attribution idempotent.

*Call graph*: called by 2 (_appended_blocks, attributed_arguments); 1 external calls (values).


##### `slack_attributed`  (lines 424–438)

```
def slack_attributed(provider: str, slug: str, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Decides whether a connector call is the special case of sending a Slack message, and if so adds the ufo attribution footer. Non-Slack calls, Slack reads, listings, edits that are not sends, and unrelated tools are left untouched.

**Data flow**: It receives provider name, tool slug, and arguments. It checks that the provider is Slack and that the tool name looks like a message send, post, reply, or schedule action. If it matches, it returns attributed arguments; otherwise it returns the original arguments.

**Call relations**: `call_external_tool` calls this just before execution. When attribution is needed, it hands off to `attributed_arguments` to do the actual argument rewriting.

*Call graph*: calls 1 internal fn (attributed_arguments); called by 1 (call_external_tool).


##### `call_external_tool`  (lines 441–450)

```
async def call_external_tool(ctx: ToolContext, args: CallExternalToolInput) -> ToolResult
```

**Purpose**: Runs one real connector tool on the broker using a connected account. This is the execution entry point after discovery has found the correct tool name and schema.

**Data flow**: It receives the context and call request. It finds the connector, optionally enforces read-only mode by checking the broker schema, resolves which connected account to use, adds Slack attribution if applicable, creates a `_ConnectorCall`, and returns the broker result as text content inside a tool result.

**Call relations**: This public tool uses `_registry` to find the connector, `ToolContext.connector_account` to pick the account, `slack_attributed` for Slack sends, and `_ConnectorCall.run` through the `_ConnectorCall` instance for the full staging, execution, fetching, translation, and cleanup flow.

*Call graph*: calls 3 internal fn (connector_account, _registry, slack_attributed); 3 external calls (__init__, __init__, __init__).


##### `_ConnectorCall.run`  (lines 478–491)

```
async def run(self, arguments: dict[str, JsonValue], account_id: str) -> str
```

**Purpose**: Performs one connector tool execution from start to finish. It prepares file arguments, calls the broker, imports returned files, cleans up encoded content, and condenses repeated data.

**Data flow**: It receives tool arguments and an account ID. It recursively stages any workspace-file arguments, sends the staged arguments to the broker execute API, fetches produced files into the workspace, translates base64-like result content, adds a `workspace_files` list when needed, and returns serialized JSON text.

**Call relations**: `call_external_tool` creates the `_ConnectorCall` and calls this method. `run` coordinates `_staged_value`, `_fetched_files`, `_translated_node`, and then sends `_deduped` to a worker thread with `asyncio.to_thread` so heavier cleanup does not block the main event loop.

*Call graph*: calls 3 internal fn (_fetched_files, _staged_value, _translated_node); 1 external calls (to_thread).


##### `_ConnectorCall._staged_value`  (lines 493–508)

```
async def _staged_value(self, value: object) -> object
```

**Purpose**: Walks through a connector tool’s arguments and replaces any workspace-file marker with a broker-ready file reference. This lets tools receive files without exposing arbitrary server files.

**Data flow**: It receives any argument value. If the value is exactly a dictionary containing the workspace-file key, it validates the path and stages that file. If the value is a dictionary or list, it recursively processes children. Other values pass through unchanged.

**Call relations**: `_ConnectorCall.run` calls this for every top-level argument before broker execution. When it finds a file marker, it hands off to `_stage_file` to do the actual upload preparation.

*Call graph*: calls 1 internal fn (_stage_file); called by 1 (run).


##### `_ConnectorCall._stage_file`  (lines 510–546)

```
async def _stage_file(self, path: str) -> dict[str, object]
```

**Purpose**: Uploads one workspace file to the broker’s file store, or reuses an existing broker copy when the broker says it already has it. This gives external tools access to user-selected workspace files safely.

**Data flow**: It receives a workspace path. It converts it to a scoped workspace path, asks the sandbox to hash and size-check the file, rejects files over the transfer limit, guesses filename and media type, asks the broker for an upload location, and if needed uses sandboxed `curl` to PUT the file there. It returns the broker argument value that refers to the staged file.

**Call relations**: `_staged_value` calls this when it sees a workspace-file argument. The method relies on sandbox commands and path helpers so the main service does not directly read or stream the file bytes.

*Call graph*: called by 1 (_staged_value); 4 external calls (guess_type, PurePosixPath, quote, workspace_path).


##### `_ConnectorCall._fetched_files`  (lines 548–581)

```
async def _fetched_files(self, files: tuple[BrokerFile, ...]) -> list[dict[str, str]]
```

**Purpose**: Downloads files produced by a connector tool into the workspace. It gives the agent stable workspace paths for files that came back from an external service.

**Data flow**: It receives broker file records, each with a name and download URL. For each file it sanitizes the filename, creates a unique target path, claims that path safely inside the sandbox, downloads the URL with sandboxed `curl`, and returns a list of names and workspace paths.

**Call relations**: `_ConnectorCall.run` calls this after broker execution, using the broker’s reported file outputs. It uses containment helpers and unique IDs to avoid clobbering existing files or following unsafe links.

*Call graph*: called by 1 (run); 3 external calls (quote, contained_leaf, uuid4).


##### `_ConnectorCall._translated_node`  (lines 583–643)

```
async def _translated_node(self, node: Mapping[str, object], depth: int=0) -> dict[str, object]
```

**Purpose**: Rewrites result objects that explicitly say they contain base64 data. It turns hidden file-like content into readable text or workspace-file references before the result is shown to the agent.

**Data flow**: It receives a result dictionary and recursion depth. It first recursively translates child values, then looks for base64 marker fields and content fields such as `content`, `data`, or `body`. Decodable small UTF-8 text is put inline; large or binary bytes are offloaded to a file; marker fields are updated when all marked content was successfully translated.

**Call relations**: `_ConnectorCall.run` starts result translation here, and `_translated` calls it for nested dictionaries. It uses `_decoded_base64` to decode marked strings, `_translated_bytes` to decide inline versus file, and media-type guessing to name offloaded data sensibly.

*Call graph*: calls 3 internal fn (_translated, _translated_bytes, _decoded_base64); called by 2 (_translated, run); 1 external calls (guess_type).


##### `_ConnectorCall._translated`  (lines 645–664)

```
async def _translated(self, value: object, depth: int) -> object
```

**Purpose**: Recursively translates any value inside a connector result. It handles nested objects, lists, and standalone `data:` URLs that contain base64 bytes.

**Data flow**: It receives any result value plus a recursion depth. If the depth is too large, it leaves the value unchanged. Dictionaries go to `_translated_node`, lists are walked item by item, small enough `data:` strings go to `_translated_data_url`, and all other values pass through unchanged.

**Call relations**: `_translated_node` uses this to walk children. It calls back into `_translated_node` for dictionaries and calls `_translated_data_url` for self-contained base64 data URLs.

*Call graph*: calls 2 internal fn (_translated_data_url, _translated_node); called by 1 (_translated_node).


##### `_ConnectorCall._translated_data_url`  (lines 666–678)

```
async def _translated_data_url(self, value: str) -> object
```

**Purpose**: Converts a `data:<mime>;base64,...` string into readable text or a workspace-file reference. This handles providers that inline files as data URLs instead of separate fields.

**Data flow**: It receives a string. If it does not match the expected data-URL shape or does not decode as valid base64, it returns the original string. Otherwise it decodes the bytes, chooses a filename extension from the declared media type, and sends the bytes to `_translated_bytes`.

**Call relations**: `_translated` calls this only for strings that begin with `data:` and are within the decode size limit. It uses `_decoded_base64` for strict decoding and `_translated_bytes` for the inline-versus-file choice.

*Call graph*: calls 2 internal fn (_translated_bytes, _decoded_base64); called by 1 (_translated); 1 external calls (guess_extension).


##### `_ConnectorCall._translated_bytes`  (lines 680–689)

```
async def _translated_bytes(self, decoded: bytes, text: str | None, name: str, mimetype: str) -> object
```

**Purpose**: Decides how decoded bytes should appear in the final tool result. Small readable text stays inline; binary or large content becomes a workspace file reference.

**Data flow**: It receives raw bytes, optional decoded text, a suggested filename, and a media type. If the text exists and is short enough, it returns that text. Otherwise it writes the bytes through `_offloaded` and returns the resulting file reference.

**Call relations**: `_translated_node` and `_translated_data_url` both call this after successful base64 decoding. It calls `_offloaded` only when putting bytes inline would be too large or not meaningful.

*Call graph*: calls 1 internal fn (_offloaded); called by 2 (_translated_data_url, _translated_node).


##### `_ConnectorCall._offloaded`  (lines 691–733)

```
async def _offloaded(self, name: str, mimetype: str, data: bytes) -> dict[str, object]
```

**Purpose**: Writes decoded result bytes into the workspace and returns a small reference object. This keeps large or binary payloads out of the agent’s text context while still making them available.

**Data flow**: It receives a name, media type, and bytes. It sanitizes the filename, builds a content-addressed path using a SHA-256 hash of the bytes, writes a temporary file through the sandbox, safely moves it into place, and returns name, workspace path, media type, and byte count.

**Call relations**: `_translated_bytes` calls this whenever decoded data should become a file. It uses sandbox writing plus a guarded placement script so predictable paths cannot be abused with symlinks or partial writes.

*Call graph*: called by 1 (_translated_bytes); 3 external calls (sha256, contained_leaf, uuid4).


##### `_ConnectorCall._deduped`  (lines 735–793)

```
def _deduped(self, payload: dict[str, object]) -> str
```

**Purpose**: Serializes the final connector result and, when safe and worthwhile, replaces repeated large objects with `same_as` pointers. This keeps repeated boilerplate from pushing useful results out of context.

**Data flow**: It receives the translated result payload. It serializes it once, skips deduplication if the result is too large, too structurally dense, or already contains `same_as`, otherwise walks the payload to identify repeated objects and returns JSON text for the condensed version.

**Call relations**: `_ConnectorCall.run` invokes this in a worker thread after all network and translation work. It uses `_condensed` to walk and rewrite the structure and `_escaped` to build JSON Pointer paths.

*Call graph*: calls 2 internal fn (_condensed, _escaped); 1 external calls (dumps).


##### `_ConnectorCall._condensed`  (lines 795–871)

```
def _condensed(self, value: object, pointer: str, depth: int, first: dict[bytes, str]) -> tuple[object, bytes, int]
```

**Purpose**: Walks one piece of the result tree and identifies repeated dictionary objects by their full structure. Later repeated objects can be replaced with a pointer to the first copy.

**Data flow**: It receives a value, its JSON Pointer path, depth, and a record of first-seen object fingerprints. It recursively processes dictionaries and lists, computes secure hashes for structural identity, records large first-seen dictionaries, and returns either the original-shaped value or a `same_as` pointer, along with its fingerprint and size.

**Call relations**: `_deduped` calls this for each top-level payload item. `_condensed` calls itself recursively and uses `_escaped` for pointer path pieces and SHA-256 hashing to compare provider-controlled data safely.

*Call graph*: calls 1 internal fn (_escaped); called by 1 (_deduped); 1 external calls (sha256).


##### `_escaped`  (lines 874–877)

```
def _escaped(token: str) -> str
```

**Purpose**: Escapes one key so it can be used inside a JSON Pointer path. This makes pointers work even when object keys contain `/` or `~`.

**Data flow**: It receives a string key. It replaces `~` with `~0` and `/` with `~1`, then returns the escaped token.

**Call relations**: `_deduped` and `_condensed` use this while building `same_as` pointer paths. It is the small helper that keeps those paths unambiguous.

*Call graph*: called by 2 (_condensed, _deduped).


##### `_decoded_base64`  (lines 880–904)

```
def _decoded_base64(value: object) -> tuple[bytes, str | None] | None
```

**Purpose**: Strictly decodes a value that a provider has marked as base64. It avoids silently corrupting normal text that was mislabeled.

**Data flow**: It receives any value. If it is not a string or is over the decode limit, it returns nothing. Otherwise it removes whitespace, strictly base64-decodes the text, tries to decode the bytes as UTF-8, and returns the bytes plus decoded text when possible, or bytes plus no text for binary content.

**Call relations**: `_translated_node` and `_translated_data_url` use this before turning provider-encoded content into inline text or workspace files. It calls the standard base64 decoder and deliberately fails closed when data is invalid.

*Call graph*: called by 2 (_translated_data_url, _translated_node); 1 external calls (b64decode).


##### `search_connector_tools`  (lines 907–921)

```
async def search_connector_tools(ctx: ToolContext, args: SearchConnectorToolsInput) -> ToolResult
```

**Purpose**: Searches within one connector for tools that match a natural-language goal. It can return not only matching tools, but also broker-provided advice such as a plan, guidance, and pitfalls.

**Data flow**: It receives a connector source ID and query. It finds the connector, asks the broker’s search API for matching tools and guidance, prepares rows with fallback behavior and size limits, adds any note, and returns the result as JSON.

**Call relations**: This is a public discovery tool. It uses `_registry` to reach the connector, `_discovered_rows` to format and possibly fallback-fill the tools list, and `_json_result` to produce tool output.

*Call graph*: calls 3 internal fn (_discovered_rows, _json_result, _registry).


##### `_registry`  (lines 924–927)

```
def _registry(ctx: ToolContext) -> ConnectorRegistry
```

**Purpose**: Returns the connector registry for the current turn, or fails clearly if connector tools were invoked without one. The registry is the live map from connector source IDs to broker entries.

**Data flow**: It reads `ctx.connectors`. If the registry is present, it returns it; if not, it raises an error explaining that connector dispatch lacks the turn’s registry.

**Call relations**: `list_external_tools`, `describe_external_tools`, `search_connector_tools`, and `call_external_tool` all call this before they can look up or use connectors.

*Call graph*: called by 4 (call_external_tool, describe_external_tools, list_external_tools, search_connector_tools).


##### `_tool_json`  (lines 930–931)

```
def _tool_json(tool: BrokerTool) -> dict[str, object]
```

**Purpose**: Formats a broker tool into the compact JSON shape returned to the agent. It keeps only the tool slug, description, and input schema.

**Data flow**: It receives a broker tool object. It reads its slug, description, and input schema, and returns them in a plain dictionary.

**Call relations**: `describe_external_tools` uses this for exact schema lookups, and `_available_tools` uses it while building discovery lists.

*Call graph*: called by 2 (_available_tools, describe_external_tools).


##### `_discovered_rows`  (lines 934–951)

```
async def _discovered_rows(entry: ConnectorEntry, workspace_id: UUID, query: str, found: tuple[BrokerTool, ...]) -> tuple[list[dict[str, object]], str]
```

**Purpose**: Builds the tool rows for connector discovery and applies the “never a dead end” rule. If a non-empty query finds nothing, it falls back to the connector’s top tools and marks that they are not relevance-ranked matches.

**Data flow**: It receives a connector entry, workspace ID, query, and broker-found tools. If the query was non-empty and found nothing, it asks the broker for the unqueried top tools. It trims the rows through `_available_tools`, adds fallback and omission notes when needed, and returns both rows and note text.

**Call relations**: `describe_external_tools` and `search_connector_tools` both call this, so both discovery paths behave consistently. It delegates row sizing and formatting to `_available_tools`.

*Call graph*: calls 1 internal fn (_available_tools); called by 2 (describe_external_tools, search_connector_tools).


##### `_available_tools`  (lines 954–967)

```
def _available_tools(listed: tuple[BrokerTool, ...]) -> list[dict[str, object]]
```

**Purpose**: Turns a broker tool list into rows that fit within an inline response budget. This avoids returning so much catalog data that the engine would have to offload it to a file.

**Data flow**: It receives a tuple of broker tools. It converts each with `_tool_json`, counts the JSON size spent so far, stops once adding more would exceed the budget after at least one row, and returns the included rows.

**Call relations**: `_discovered_rows` uses this for both description-based discovery and semantic search results. It calls `_tool_json` for each row and JSON serialization only to estimate size.

*Call graph*: calls 1 internal fn (_tool_json); called by 1 (_discovered_rows); 1 external calls (dumps).


##### `_discovery_query`  (lines 970–977)

```
def _discovery_query(explicit: str, unresolved: list[str]) -> str
```

**Purpose**: Chooses the search query used when exact requested tool names could not be resolved. This helps turn guessed slugs into useful keywords for finding the real tool.

**Data flow**: It receives an explicit query and a list of unresolved tool names. If the explicit query is present, it returns that. Otherwise it lowercases unresolved names, replaces punctuation with spaces, removes duplicate words while preserving order, and returns the combined keyword string.

**Call relations**: `describe_external_tools` calls this when it needs to search the catalog after unresolved exact names or when tool names were omitted. It uses regular-expression substitution to split guessed slugs into readable words.

*Call graph*: called by 1 (describe_external_tools); 1 external calls (sub).


##### `_json_result`  (lines 980–981)

```
def _json_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: Wraps a dictionary as the standard text-based tool result used by these connector tools. It is the final packaging step for discovery responses.

**Data flow**: It receives a payload dictionary. It serializes the payload to JSON text, puts that text into a `TextContent` object, and returns a `ToolResult` containing it.

**Call relations**: `list_external_tools`, `describe_external_tools`, and `search_connector_tools` all use this to produce consistent JSON tool output. Execution results from `call_external_tool` are packaged separately because `_ConnectorCall.run` already returns serialized result text.

*Call graph*: called by 3 (describe_external_tools, list_external_tools, search_connector_tools); 3 external calls (__init__, __init__, dumps).


### `extensions/mcp/ufo_ext_mcp.py`

`io_transport` · `request handling`

This extension is a bridge between the agent and external MCP servers. A workspace can store a named list of MCP servers in a credential slot, including each server's URL and optional bearer token. The agent can then ask one of those servers what tools it offers, inspect the exact input shape for a chosen tool, and call that tool.

The file is careful about trust and size. MCP servers are outside systems, so their descriptions and results may contain attacker-controlled text. The manifest marks both exposed tools as untrusted, so the rest of the system can treat their output with caution. It also limits request and response bodies to about one megabyte, so a remote server cannot flood the agent with oversized data.

The flow is like using a restaurant menu before ordering. First, `list_mcp_tools` gives a short menu: tool names, summaries, and parameter names. If the agent wants to use a tool, it asks again for that tool's full schema, meaning the detailed rules for its inputs. Then `call_mcp_tool` sends the arguments to the selected server. A helper builds the HTTP MCP client, another helper finds the named server from credentials, and small formatting helpers turn MCP responses into normal tool results.

#### Function details

##### `McpServer._http_url`  (lines 79–82)

```
def _http_url(cls, value: str) -> str
```

**Purpose**: This validator makes sure a configured MCP server URL starts with `http://` or `https://`. It prevents the extension from trying to connect to unsupported or surprising address types.

**Data flow**: A URL string comes in while an `McpServer` is being built. The function checks it against the allowed web URL pattern. If it matches, the same URL comes out; if not, validation fails with a clear error.

**Call relations**: Pydantic, the data validation library, calls this automatically when server configuration is parsed. Its result affects later calls that use `mcp_client`, because only validated server URLs are accepted.


##### `McpServerUpdate._name`  (lines 97–101)

```
def _name(cls, value: str) -> str
```

**Purpose**: This validator cleans and checks the name used when adding or updating one MCP server. It makes sure the name is not just spaces.

**Data flow**: A submitted server name comes in. The function trims surrounding whitespace, rejects an empty result, and returns the cleaned name for storage.

**Call relations**: Pydantic calls this while `merge_mcp_server` validates an update request. The cleaned name becomes the key used to add or replace a server in the saved configuration.


##### `McpServerRemoval._name`  (lines 110–114)

```
def _name(cls, value: str) -> str
```

**Purpose**: This validator cleans and checks the name used when removing an MCP server. It prevents a blank removal request from accidentally meaning something unclear.

**Data flow**: A submitted removal name comes in. The function strips whitespace, rejects it if nothing remains, and returns the cleaned name.

**Call relations**: Pydantic calls this while `merge_mcp_server` validates a removal request. The returned name is then used to find the server that should be deleted.


##### `merge_mcp_server`  (lines 117–155)

```
def merge_mcp_server(current: str | None, submitted: str) -> str
```

**Purpose**: This function updates the stored MCP server credential value. It supports three user actions: replace the whole server map, add or update one named server, or remove one named server.

**Data flow**: It receives the current stored JSON, if any, and a newly submitted JSON string. It parses and validates the submitted data, combines it with the existing saved servers when needed, preserves an old token if an update omits `auth`, and returns a clean JSON string to store. If the data is malformed or refers to a missing server during removal, it raises a credential-specific error.

**Call relations**: The manifest registers this as the merge function for the `mcp_servers` credential slot. During credential changes, the surrounding credential system calls it before any tool call happens; later `_server` reads the JSON that this function produced.

*Call graph*: 4 external calls (__init__, __init__, __init__, loads).


##### `mcp_client`  (lines 175–181)

```
def mcp_client(server: McpServer) -> Client
```

**Purpose**: This function builds the HTTP client used to talk to one MCP server. If the workspace configured a token, it attaches it as a bearer authorization header.

**Data flow**: A validated `McpServer` object comes in, containing a URL and maybe an auth token. The function creates a streamable HTTP transport for that URL, adds headers if needed, sets a timeout, and returns a FastMCP client ready to open a session.

**Call relations**: `_list_mcp_tools` and `_call_mcp_tool` both call this after `_server` finds the configured server. The FastMCP client it returns performs the actual MCP handshake, tool listing, and tool calling.

*Call graph*: called by 2 (_call_mcp_tool, _list_mcp_tools); 2 external calls (Client, StreamableHttpTransport).


##### `_server`  (lines 184–196)

```
async def _server(ctx: ToolContext, name: str) -> McpServer
```

**Purpose**: This helper looks up one named MCP server from the workspace's stored credentials. It stops the operation early if the extension context or requested server is missing.

**Data flow**: It receives the current tool context and a server name. It reads the `mcp_servers` credential value, validates the saved JSON into a server map, finds the requested name, and returns that server's URL and token. If the name is absent, it raises an error that includes the configured names.

**Call relations**: `_list_mcp_tools` and `_call_mcp_tool` call this before making any network request. It is the gate between a model-supplied server name and the actual saved workspace configuration.

*Call graph*: called by 2 (_call_mcp_tool, _list_mcp_tools).


##### `_list_mcp_tools`  (lines 199–220)

```
async def _list_mcp_tools(ctx: ToolContext, args: ListMcpToolsInput) -> ToolResult
```

**Purpose**: This is the handler behind the `list_mcp_tools` tool. It lets the agent browse a server's available tools first, then request full input schemas only for the few tools it plans to use.

**Data flow**: The tool context and parsed input come in. The function finds the named server, opens an MCP client, asks the server for its tool list, and then returns either a compact catalog or detailed schema entries for requested tool names. If a requested tool name is unknown, it raises a helpful error.

**Call relations**: The manifest exposes this as a callable tool. It relies on `_server` to resolve credentials, `mcp_client` to talk over MCP, `_catalog_entry` and `_schema_entry` to shape the response, `_bounded_schemas` to avoid over-large schema replies, and `_json_result` to package the answer as a normal tool result.

*Call graph*: calls 6 internal fn (_bounded_schemas, _catalog_entry, _json_result, _schema_entry, _server, mcp_client).


##### `_idempotent`  (lines 223–225)

```
def _idempotent(tool: McpTool) -> bool
```

**Purpose**: This small helper reads whether an MCP tool claims it is idempotent, meaning it can be repeated without changing things further after the first successful run. That hint helps the agent understand whether retrying a tool is safer.

**Data flow**: An MCP tool description comes in. The function checks its annotations, looks for the `idempotentHint`, and returns `true` or `false`.

**Call relations**: `_catalog_entry` and `_schema_entry` call this when building tool information for the agent. The result is included beside each tool's name and schema details.

*Call graph*: called by 2 (_catalog_entry, _schema_entry).


##### `_catalog_entry`  (lines 228–244)

```
def _catalog_entry(tool: McpTool) -> JsonValue
```

**Purpose**: This function turns one full MCP tool description into a short catalog entry. It keeps only the information needed to choose a tool without overwhelming the tool-result size limit.

**Data flow**: One MCP tool object comes in. The function reads its input schema, extracts parameter names and required parameter names, creates a short summary from the description, adds the idempotency hint, and returns a plain JSON-friendly object.

**Call relations**: `_list_mcp_tools` calls this for every tool when the agent asks to browse the catalog. It uses `_summary` to shorten descriptions and `_idempotent` to include the retry-safety hint.

*Call graph*: calls 2 internal fn (_idempotent, _summary); called by 1 (_list_mcp_tools).


##### `_summary`  (lines 247–253)

```
def _summary(description: str) -> str
```

**Purpose**: This helper makes a short, readable summary from a longer tool description. It is designed for docstring-like descriptions where the first line usually says what the tool does.

**Data flow**: A description string comes in. The function trims it, takes the first line, keeps only the first sentence-like part, cuts it to the maximum summary length, and returns that short text.

**Call relations**: `_catalog_entry` calls this while building the compact tool catalog. It keeps catalog results small enough to be useful before the agent asks for full schemas.

*Call graph*: called by 1 (_catalog_entry).


##### `_schema_entry`  (lines 256–262)

```
def _schema_entry(tool: McpTool) -> JsonValue
```

**Purpose**: This function returns the detailed information the agent needs before calling a specific MCP tool. It includes the full input schema, which describes the exact argument names and shapes.

**Data flow**: One MCP tool object comes in. The function copies its name, description, input schema, and idempotency hint into a JSON-friendly object.

**Call relations**: `_list_mcp_tools` calls this only for tool names the agent specifically requested. It uses `_idempotent` so the detailed view carries the same retry-safety hint as the catalog.

*Call graph*: calls 1 internal fn (_idempotent); called by 1 (_list_mcp_tools).


##### `_call_mcp_tool`  (lines 265–277)

```
async def _call_mcp_tool(ctx: ToolContext, args: CallMcpToolInput) -> ToolResult
```

**Purpose**: This is the handler behind the `call_mcp_tool` tool. It sends arguments to a selected tool on a selected MCP server and turns the server's reply into the system's normal tool-result format.

**Data flow**: The tool context and parsed call request come in: server name, tool name, and argument object. The function finds the server, checks the serialized arguments are not too large, opens an MCP client, calls the remote tool, and returns either structured JSON output or joined text output. If the remote tool reports an error, it returns an error tool result instead of pretending the call succeeded.

**Call relations**: The manifest exposes this as a callable tool. It depends on `_server` for credential lookup, `mcp_client` for the MCP connection, `_joined_text` to extract text blocks, `_bounded` to enforce response size, and `_json_result` to package successful responses.

*Call graph*: calls 5 internal fn (_bounded, _joined_text, _json_result, _server, mcp_client); 4 external calls (__init__, __init__, __init__, dumps).


##### `_joined_text`  (lines 280–281)

```
def _joined_text(content: list[object]) -> str
```

**Purpose**: This helper pulls readable text out of an MCP response that may contain several content blocks. It ignores non-text blocks.

**Data flow**: A list of response content objects comes in. The function selects only MCP text content blocks, takes their text, joins them with newline characters, and returns one string.

**Call relations**: `_call_mcp_tool` uses this when a tool call returns plain text or when an error response needs to be shown to the model.

*Call graph*: called by 1 (_call_mcp_tool).


##### `_bounded`  (lines 284–287)

```
def _bounded(text: str) -> str
```

**Purpose**: This function enforces the maximum allowed MCP response size. It fails loudly rather than silently cutting off data.

**Data flow**: A text string comes in. The function measures its encoded byte size; if it is within the limit, the same text comes out, and if it is too large, it raises an MCP-specific error.

**Call relations**: `_call_mcp_tool` uses this for error text, and `_json_result` uses it for JSON-formatted results. It is the final guard against oversized data reaching the tool loop.

*Call graph*: called by 2 (_call_mcp_tool, _json_result); 1 external calls (__init__).


##### `_bounded_schemas`  (lines 290–307)

```
def _bounded_schemas(payload: dict[str, JsonValue], tools: int) -> ToolResult
```

**Purpose**: This function prevents a request for several full tool schemas from producing an unusably large response. It asks the caller to request fewer schemas when there is a smaller, practical alternative.

**Data flow**: A schema payload and the number of requested tools come in. If more than one schema was requested and the rendered JSON would exceed the listing-size threshold, it raises a clear error. Otherwise, it passes the payload on to be returned as JSON.

**Call relations**: `_list_mcp_tools` calls this after building detailed schema entries. When the payload is acceptable, `_bounded_schemas` hands it to `_json_result` for normal tool-result packaging.

*Call graph*: calls 1 internal fn (_json_result); called by 1 (_list_mcp_tools); 1 external calls (dumps).


##### `_json_result`  (lines 310–311)

```
def _json_result(payload: dict[str, JsonValue]) -> ToolResult
```

**Purpose**: This helper converts a JSON-friendly payload into the system's standard text-based tool result. It also applies the response size limit.

**Data flow**: A dictionary payload comes in. The function serializes it to JSON text, checks the text with `_bounded`, wraps it in a `TextContent` object, and returns a `ToolResult` containing that text.

**Call relations**: `_list_mcp_tools`, `_bounded_schemas`, and `_call_mcp_tool` use this whenever they need to return successful JSON data to the agent.

*Call graph*: calls 1 internal fn (_bounded); called by 3 (_bounded_schemas, _call_mcp_tool, _list_mcp_tools); 3 external calls (__init__, __init__, dumps).


##### `manifest`  (lines 314–345)

```
def manifest() -> Manifest
```

**Purpose**: This function declares the extension to the host system. It names the extension, lists the two tools it provides, and declares the credential slot used to store MCP server configuration.

**Data flow**: No runtime input is needed. The function builds and returns a manifest object containing tool definitions for listing and calling MCP tools, plus a credential definition that uses `merge_mcp_server` to update saved server settings.

**Call relations**: The extension loader calls this to learn what this file contributes. The returned manifest connects user-facing tool names to `_list_mcp_tools` and `_call_mcp_tool`, and connects credential updates to `merge_mcp_server`.

*Call graph*: 3 external calls (__init__, __init__, __init__).
