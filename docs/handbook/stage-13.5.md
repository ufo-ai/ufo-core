# Generic Connector Objects, Agent Tools, and Evaluation Fakes  `stage-13.5`

This stage is shared behind-the-scenes support for working with outside services. It is the layer that turns connected accounts, like Slack or GitHub, into things the workspace and the agent can understand and use safely.

The objects file makes each connected third-party account appear as a workspace object. It also defines the rules for what users and agents may do with it. A key idea is separation: having a real account connected is not the same as giving an agent permission to act through it.

The tools file is the agent’s safe control panel for connectors. It lets the agent discover available tools and call them in a consistent way, no matter which service they come from. It also cleans up risky or awkward results, such as uploaded files, returned files, very large encoded blobs, or repeated JSON data.

The eval manifest provides a fake but predictable workplace for tests. It simulates connectors like email, calendar, Drive, GitHub, Stripe, HubSpot, and Greenhouse using seeded data, so evaluations can check changes without touching real services.

## Files in this stage

### Connector Workspace Surfaces
Expose connected third-party accounts as governed workspace objects and provide normalized agent access to connector tools.

### `extensions/connectors/ufo_ext_connectors/objects.py`

`domain_logic` · `request handling`

A connected account is sensitive: it belongs to a workspace member, may involve outside services, and may be shared with one or more agents. This file turns that idea into two readable object types. A `connection` is the member-owned account itself, such as a provider account that was connected after user consent. A `connector_grant` is one agent's access to that account, like a keycard issued to one person rather than ownership of the building.

The file lets the object system list these items, show their details, report live status, and apply allowed changes. It also blocks unsafe shortcuts. For example, creating a brand-new third-party connection is refused here because it must happen through the proper `connect_account` flow, where the outside provider can ask for consent. Deleting a connection disconnects the account for every agent. Deleting a grant only removes one agent's access.

The important safety rule is that ownership and sharing are checked at the object layer. Owners can share or make private; admins can make private or revoke, but cannot create consent on someone else's behalf. The result is a clean workspace-object view of connector access without hiding the fact that third-party accounts need special care.

#### Function details

##### `_AccountSummary.provider`  (lines 51–51)

```
def provider(self) -> str
```

**Purpose**: This protocol property says that any account summary used by this file must expose the provider name, such as the service or connector the account belongs to. It is a type-level promise rather than runtime work.

**Data flow**: An object that claims to be an account summary must provide a provider string. The code can then read that string without caring about the summary's exact class.

**Call relations**: The helper `_named` relies on this property when it builds stable object names for both connection summaries and grant summaries.


##### `_AccountSummary.account_id`  (lines 54–54)

```
def account_id(self) -> str
```

**Purpose**: This protocol property says that any account summary used here must expose the account's identifier at the provider. It gives the naming helper the second piece it needs to identify an account.

**Data flow**: An account summary supplies an account ID string. That value is combined with the provider name to form a workspace object name.

**Call relations**: The helper `_named` reads this property while preparing rows for the connection and connector-grant object listings.


##### `_named`  (lines 57–58)

```
def _named(rows: tuple[SummaryT, ...]) -> dict[str, SummaryT]
```

**Purpose**: This helper turns a group of account-like summaries into a dictionary keyed by the object name users will see. It gives connections and grants a consistent name based on provider plus account ID.

**Data flow**: It receives a tuple of summaries, each with a provider and account ID. For each summary, it asks `account_object_name` to make the display/object name, then returns a dictionary from that name to the original summary.

**Call relations**: Both `ConnectionObjects._member_rows` and `ConnectorGrantObjects._member_rows` call this when building the list of visible objects. It hands naming off to `account_object_name` so the same account is named the same way across object types.

*Call graph*: called by 2 (_member_rows, _member_rows); 1 external calls (account_object_name).


##### `ConnectionObjects._member_rows`  (lines 69–86)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: This builds the list of connected accounts that can be shown as `connection` objects. Each row includes a readable summary and ownership information used by the object permission system.

**Data flow**: It asks the grants layer for current connection summaries. It converts each summary into an `OwnedRow` with a stable name, a human-readable line showing provider, account, owner email, and shared/private state, plus a generated owner record tied to the real connection ID. It returns all of those rows as a tuple.

**Call relations**: The member-readable object framework calls this when it needs to list connection objects for a member or workspace view. It uses `_named` for consistent names and packages each result in SDK object-row types that the wider object system understands.

*Call graph*: calls 1 internal fn (_named); 3 external calls (__init__, __init__, connection_summaries).


##### `ConnectionObjects._member_object`  (lines 88–106)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[ConnectionSpec] | None
```

**Purpose**: This fetches the detailed object view for one connected account. It turns the stored connection summary into a small spec that says which provider and account ID the connection represents.

**Data flow**: It receives the requested object name and owner record. It reloads connection summaries, finds the one whose generated ID matches the owner record, and returns an `ObjectDetail` with the provider, account ID, creation time, and update time. If the connection no longer exists, it returns nothing.

**Call relations**: The object system calls this after a connection row has been selected for inspection. It depends on `connection_summaries` as the source of truth and returns an SDK detail object for display or object-get style reads.

*Call graph*: 3 external calls (__init__, __init__, connection_summaries).


##### `ConnectionObjects._status`  (lines 108–123)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: This reports live status information for a connected account, beyond the simple spec. It shows who owns it, whether it is shared, where it is hosted, and which agents currently use it.

**Data flow**: It receives the tool context, object name, and owner record. It reloads connection summaries, finds the matching connection by generated ID, and returns a JSON-friendly dictionary of status fields. If the connection disappeared, it returns nothing.

**Call relations**: The object framework calls this when a status view is requested for a `connection`. It reads from the same connection-summary source as the listing and detail methods, but returns operational facts rather than the editable spec.

*Call graph*: 1 external calls (connection_summaries).


##### `ConnectionObjects._apply_owned`  (lines 125–133)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ConnectionSpec, old: ConnectionSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: This deliberately refuses attempts to create or edit a connection object directly. A real third-party account connection must go through `connect_account`, because that flow includes provider consent.

**Data flow**: It receives the desired connection spec and any old object state, but does not use them to change anything. Instead it raises a `VerbNotSupported` error with a message explaining that account connection must use the proper connection flow.

**Call relations**: The object system calls this when someone tries to apply changes to a `connection`. Rather than handing off to the grants layer, it stops the operation immediately to protect the consent process.

*Call graph*: 1 external calls (__init__).


##### `ConnectionObjects._delete_owned`  (lines 135–145)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: This disconnects a connected account, which removes the underlying connection and therefore affects every agent using it. It requires both connector-grant access and a real speaking member so the action has an accountable actor.

**Data flow**: It receives the tool context, object name, and owner record. It checks that the grants service is available and that there is a speaker member ID. It then asks the grants service to disconnect the connection identified by the owner generation ID. If the stored connection changed or vanished during the operation, it raises an error instead of pretending success.

**Call relations**: The object system calls this when an allowed user deletes a `connection` object. It hands the actual disconnect work to `ctx.grants.disconnect`, using the speaker member as the actor for permission and audit purposes.


##### `ConnectorGrantObjects._admin_can_apply`  (lines 156–157)

```
def _admin_can_apply(self, old: ConnectorGrantSpec, spec: ConnectorGrantSpec) -> bool
```

**Purpose**: This defines the one edit an admin is allowed to make to someone else's connector grant: turning a shared grant back to private. It prevents admins from expanding access while still allowing them to reduce exposure.

**Data flow**: It receives the old grant spec and the requested new spec. It checks that the old grant was shared and that the only requested change is `shared` becoming false. It returns true only for that exact privacy-tightening edit.

**Call relations**: The member-readable object framework uses this as part of deciding whether an admin may apply an update. It relies on the grant spec's copy/update behavior to compare “same grant, but private” against the requested spec.

*Call graph*: 1 external calls (model_copy).


##### `ConnectorGrantObjects._member_rows`  (lines 159–176)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: This builds the list of `connector_grant` objects, where each object represents one agent's access to a connected account. The row summary tells the reader which provider account is involved and whether the grant is shared or private.

**Data flow**: It asks the grants layer for current grant summaries. It names each summary, creates a readable summary string, and wraps ownership details in a generated owner record that includes the real grant ID and shared flag. It returns the rows as a tuple.

**Call relations**: The object framework calls this when listing connector grants. It uses `_named` for consistent account-based names and `grant_summaries` as the live source of grant information.

*Call graph*: calls 1 internal fn (_named); 3 external calls (__init__, __init__, grant_summaries).


##### `ConnectorGrantObjects._member_object`  (lines 178–219)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[ConnectorGrantSpec] | None
```

**Purpose**: This returns the detailed object view for one agent's connector grant. It shows the account fields, whether the grant is shared, and links that explain what the grant is connected to.

**Data flow**: It receives the requested name and owner record. It reloads grant summaries, finds the matching grant by generated ID, and builds an `ObjectDetail` containing provider, account ID, shared flag, timestamps, and links. The links always point to the agent that holds the grant; if the grant is private, it also links back to the underlying connection object. If the grant no longer exists, it returns nothing.

**Call relations**: The object system calls this when someone inspects a `connector_grant`. It uses `grant_summaries` for current data, `account_object_name` to link private grants back to their connection, and SDK link/reference objects so other object tools can follow those relationships.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, account_object_name, grant_summaries).


##### `ConnectorGrantObjects._status`  (lines 221–236)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: This reports live status information for a connector grant. It tells who owns the connected account, which host and agent are involved, and whether the grant is shared.

**Data flow**: It receives the tool context, object name, and owner record. It reloads grant summaries, finds the matching grant by generated ID, and returns a JSON-friendly dictionary with owner, host, agent, and sharing fields. If the grant is gone, it returns nothing.

**Call relations**: The object framework calls this when a status view is requested for a `connector_grant`. It reads from `grant_summaries`, just like the row and detail methods, but returns operational facts rather than object-spec fields.

*Call graph*: 1 external calls (grant_summaries).


##### `ConnectorGrantObjects._apply_owned`  (lines 238–286)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ConnectorGrantSpec, old: ConnectorGrantSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: This creates or updates an agent's access to an already-connected account, but only within strict safety rules. It can attach an existing connection to an agent, or change the grant's shared/private setting; it refuses attempts to create a brand-new third-party connection here.

**Data flow**: It receives the requested grant spec, the old spec if there is one, and the owner record if there is one. For a new grant, it requires a grants service and a speaking member, then asks the grants service to attach an existing provider account to the current conversation's agent. For an existing grant, it reloads the current grant, verifies that provider and account ID are not being changed, and only updates the shared flag if needed. If the connection or grant is unavailable or changed underneath the operation, it raises an error.

**Call relations**: The object system calls this when someone applies a `connector_grant` object. It hands real changes to the grants service through attach or shared-setting operations, while using `grant_summaries` to confirm the current state before editing. When the requested edit would amount to connecting a new account or changing the account identity, it raises `VerbNotSupported` and points users back to the proper consent flow.

*Call graph*: 4 external calls (__init__, __init__, model_copy, grant_summaries).


##### `ConnectorGrantObjects._delete_owned`  (lines 288–298)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: This revokes one agent's access to a connected account without disconnecting the account itself. Other agents' grants and the underlying connection can remain in place.

**Data flow**: It receives the tool context, object name, and owner record. It checks that the grants service is available and that there is a speaking member. It then asks the grants service to revoke the grant identified by the owner generation ID. If the grant changed or disappeared during the revoke, it raises an error.

**Call relations**: The object system calls this when an allowed user deletes a `connector_grant` object. It hands the actual revocation to `ctx.grants.revoke`, using the speaker member ID so the action is tied to the person who requested it.


### `extensions/connectors/ufo_ext_connectors/tools.py`

`orchestration` · `request handling`

A connector is a bridge to an outside service. This file is the agent’s front desk for those bridges: it can list available connectors, ask a connector what tools it really supports, search those tools by goal, and run one tool on behalf of a connected account. Without this file, the agent would have to guess tool names, pass files unsafely, and dump large or unreadable provider data directly into the chat context.

The flow works like a careful shipping desk. Before a tool call, any argument that points to a workspace file is checked inside the sandbox, uploaded to the broker’s file store, and replaced with the broker’s own reference. The broker is the service-side middleman that owns the external API call and account token. After the call, any files the provider produced are downloaded back into a safe workspace folder.

The file also cleans up results. If a provider returns base64, meaning bytes encoded as text, small UTF-8 text is decoded inline, while large or binary data is saved as a workspace file reference. If a result repeats the same large object many times, later copies are replaced with a `same_as` pointer so the answer stays readable and less likely to be offloaded. Slack sends get one special rule: messages posted through a connector are marked with a small “Sent using ufo” attribution so readers know where the text came from.

#### Function details

##### `list_external_tools`  (lines 239–275)

```
async def list_external_tools(ctx: ToolContext, args: ListExternalToolsInput) -> ToolResult
```

**Purpose**: Searches for connector providers, such as GitHub or Slack, that are available in the current turn. It returns connector names, human labels, and any accounts already connected for each match.

**Data flow**: It receives the tool context and search queries. It reads the current connector registry and active account grants, checks local provider names and labels, then also asks the registry catalog for matches. It returns a JSON tool result containing matching connectors.

**Call relations**: This is one of the public tools exposed to the agent. It starts by using `_registry` to get the live connector list and `_connected_accounts` to add account details, then finishes through `_json_result` so the answer is returned in the normal tool-result format.

*Call graph*: calls 3 internal fn (_connected_accounts, _json_result, _registry); 1 external calls (gather).


##### `_connected_accounts`  (lines 278–292)

```
async def _connected_accounts(ctx: ToolContext) -> dict[str, list[JsonValue]]
```

**Purpose**: Builds a per-provider list of connected accounts the agent may use. This lets connector discovery say not just what service exists, but whose account is already available.

**Data flow**: It reads grant information from the tool context. If there are no grants, it returns an empty mapping. Otherwise it groups active grants by provider and records account ID, owner email, and whether the connection is shared.

**Call relations**: It is called by `list_external_tools` while preparing connector search results. Its output is folded into each connector row so the agent can choose the right connected account later.

*Call graph*: called by 1 (list_external_tools).


##### `describe_external_tools`  (lines 295–317)

```
async def describe_external_tools(ctx: ToolContext, args: DescribeExternalToolsInput) -> ToolResult
```

**Purpose**: Describes real tools inside one connector and helps recover when the caller guessed a wrong tool name. It can fetch exact schemas or run discovery by query.

**Data flow**: It receives a connector source ID, optional exact tool names, and an optional search query. It asks the matching broker for schemas, records names that were not found, and when needed asks for a catalog listing. It returns JSON with schemas, discovered tools, and unresolved names.

**Call relations**: This is a public discovery tool used before execution. It gets the registry through `_registry`, formats exact schemas with `_tool_json`, builds fallback search text with `_discovery_query`, normalizes listings through `_discovered_rows`, and returns through `_json_result`.

*Call graph*: calls 5 internal fn (_discovered_rows, _discovery_query, _json_result, _registry, _tool_json).


##### `attribution_stripped`  (lines 320–324)

```
def attribution_stripped(text: str) -> str
```

**Purpose**: Removes ufo’s Slack attribution text from a message before checking what the human actually wrote. This avoids treating the footer added by the system as meaningful user content.

**Data flow**: It receives message text. It applies the attribution-matching pattern anywhere in that text and removes all matches. It returns the cleaned string.

**Call relations**: This helper is available to code that reads Slack messages. It is the inverse of the attribution-writing path in this file, although the provided call graph shows no direct caller here.


##### `attributed_arguments`  (lines 327–354)

```
def attributed_arguments(arguments: dict[str, JsonValue], subject: str) -> dict[str, JsonValue]
```

**Purpose**: Adds a Slack-style “Sent using ufo” footer to outgoing message arguments when there is a message body to attach it to. It avoids adding a duplicate footer if one is already present.

**Data flow**: It receives connector arguments and an attribution subject. It first searches the arguments for an existing footer. If none is found, it appends a context block to existing Slack blocks, or turns text/markdown arguments into blocks followed by the footer. It returns updated arguments or the original ones if no safe body is found.

**Call relations**: It is called by `slack_attributed` for Slack send-like tools. It delegates body conversion to `_body_blocks`, existing-block appending to `_appended_blocks`, and duplicate detection to `_carries_attribution`.

*Call graph*: calls 3 internal fn (_appended_blocks, _body_blocks, _carries_attribution); called by 1 (slack_attributed).


##### `_body_blocks`  (lines 357–385)

```
def _body_blocks(arguments: dict[str, JsonValue]) -> list[JsonValue] | None
```

**Purpose**: Converts plain Slack message body arguments into Slack block objects so an attribution footer can follow them. It preserves the intended markup style instead of mixing Slack’s different text formats.

**Data flow**: It reads the `markdown_text` or `text` argument. Markdown text becomes one markdown block. Plain Slack mrkdwn text is split into section blocks small enough for Slack’s per-block limit. If there is no non-empty body, it returns nothing.

**Call relations**: It is used by `attributed_arguments` when the caller did not already provide a `blocks` argument. Its output becomes the body portion of the new block list.

*Call graph*: called by 1 (attributed_arguments).


##### `_appended_blocks`  (lines 388–409)

```
def _appended_blocks(value: JsonValue, footer: dict[str, JsonValue]) -> JsonValue | None
```

**Purpose**: Appends the attribution footer to an existing Slack `blocks` value when that value is understandable and non-empty. It supports both real lists and JSON strings, because brokers may accept either form.

**Data flow**: It receives a blocks value and a footer block. If the value is a non-empty list, it returns a new list with the footer appended. If it is a string, it tries to parse it as JSON, including URL-decoded JSON, checks for an existing attribution, then serializes it back in the same style. If none of that works, it returns nothing.

**Call relations**: It is called by `attributed_arguments` when message arguments already contain Slack blocks. It uses `_carries_attribution` to avoid stacking footers and uses JSON and URL quoting helpers to preserve serialized block formats.

*Call graph*: calls 1 internal fn (_carries_attribution); called by 1 (attributed_arguments); 4 external calls (dumps, loads, quote, unquote).


##### `_carries_attribution`  (lines 412–421)

```
def _carries_attribution(value: JsonValue) -> bool
```

**Purpose**: Checks whether a nested value already contains ufo’s Slack attribution as its own line. This is the guard that prevents repeated “Sent using ufo” footers.

**Data flow**: It receives any JSON-like value. Strings are searched with the attribution pattern; lists and dictionaries are searched recursively; other values are ignored. It returns true or false.

**Call relations**: It supports both `attributed_arguments` and `_appended_blocks`. Those callers use it before adding a footer to decide whether the outgoing Slack message is already marked.

*Call graph*: called by 2 (_appended_blocks, attributed_arguments); 1 external calls (values).


##### `slack_attributed`  (lines 424–438)

```
def slack_attributed(provider: str, slug: str, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Decides whether a connector call is a Slack message send and, if so, adds ufo attribution. Non-Slack tools, reads, deletes, and other non-message actions are left untouched.

**Data flow**: It receives a provider name, tool slug, and argument dictionary. It checks that the provider is Slack and that the tool name looks like a send/post/reply/schedule message action. Matching calls are passed to `attributed_arguments`; all others return the original arguments.

**Call relations**: It is called by `call_external_tool` just before execution. It hands real Slack send cases to `attributed_arguments` so the public execution path does not need to know Slack block details.

*Call graph*: calls 1 internal fn (attributed_arguments); called by 1 (call_external_tool).


##### `call_external_tool`  (lines 441–450)

```
async def call_external_tool(ctx: ToolContext, args: CallExternalToolInput) -> ToolResult
```

**Purpose**: Runs one external connector tool through its broker. It checks read-only mode, selects the right connected account, applies Slack attribution when needed, and returns the broker result as tool output.

**Data flow**: It receives the tool context plus source ID, tool name, optional account ID, and arguments. It finds the connector entry, blocks write tools if the context is read-only, resolves the account, adjusts Slack send arguments, then creates a `_ConnectorCall` to do the full execution. It returns a `ToolResult` containing the final text response.

**Call relations**: This is the public execution tool. It uses `_registry` for lookup, `ToolContext.connector_account` for account selection, `slack_attributed` for Slack message marking, and then hands the detailed staging/execution/result-cleanup flow to `_ConnectorCall.run`.

*Call graph*: calls 3 internal fn (connector_account, _registry, slack_attributed); 3 external calls (__init__, __init__, __init__).


##### `_ConnectorCall.run`  (lines 478–491)

```
async def run(self, arguments: dict[str, JsonValue], account_id: str) -> str
```

**Purpose**: Performs one connector execution from start to finish. It stages input files, calls the broker, brings back produced files, decodes provider-embedded data, and shrinks repeated objects.

**Data flow**: It receives already validated arguments and an account ID. It walks the arguments to replace workspace-file markers with broker upload references, calls the broker’s execute API, fetches any output files, translates base64 data in the response, adds workspace file listings when present, and returns serialized JSON text after deduplication.

**Call relations**: It is invoked by `call_external_tool`. It calls `_staged_value` before broker execution, `_fetched_files` after execution, `_translated_node` to make the result readable, and runs `_deduped` in a worker thread so heavy cleanup does not stall the main event loop.

*Call graph*: calls 3 internal fn (_fetched_files, _staged_value, _translated_node); 1 external calls (to_thread).


##### `_ConnectorCall._staged_value`  (lines 493–508)

```
async def _staged_value(self, value: object) -> object
```

**Purpose**: Walks an argument value and uploads any workspace files mentioned inside it. This lets connector tools receive broker-ready file references instead of raw local paths.

**Data flow**: It receives one argument value. If the value is exactly a `workspace_file` object, it validates the path and calls `_stage_file`. If it is a dictionary or list, it recursively processes children. Other values pass through unchanged.

**Call relations**: It is called by `_ConnectorCall.run` for every top-level argument before broker execution. It hands actual file staging to `_stage_file` and returns a staged argument tree.

*Call graph*: calls 1 internal fn (_stage_file); called by 1 (run).


##### `_ConnectorCall._stage_file`  (lines 510–546)

```
async def _stage_file(self, path: str) -> dict[str, object]
```

**Purpose**: Uploads one workspace file to the broker’s file storage in a guarded way. It makes sure the file is inside the workspace, within size limits, and sent from the sandbox rather than through the main server process.

**Data flow**: It receives a workspace path. Inside the sandbox it hashes and measures the file, rejects unreadable or oversized files, guesses a filename and MIME type, asks the broker for an upload destination, and if needed uses sandboxed `curl` to PUT the file there. It returns the broker’s argument value for that staged file.

**Call relations**: It is called by `_staged_value` whenever a connector argument contains a workspace-file marker. Its result is placed into the argument tree that `_ConnectorCall.run` sends to the broker.

*Call graph*: called by 1 (_staged_value); 4 external calls (guess_type, PurePosixPath, quote, workspace_path).


##### `_ConnectorCall._fetched_files`  (lines 548–581)

```
async def _fetched_files(self, files: tuple[BrokerFile, ...]) -> list[dict[str, str]]
```

**Purpose**: Downloads files produced by a connector tool into the workspace. It creates safe, unique target paths so provider-chosen filenames cannot overwrite or escape the workspace.

**Data flow**: It receives broker file records containing names and temporary download URLs. For each file, it sanitizes the name, claims a fresh workspace path through the sandbox guard, downloads the bytes with sandboxed `curl`, and records the saved name and workspace path. It returns a list of saved-file descriptions.

**Call relations**: It is called by `_ConnectorCall.run` after broker execution. Its returned file list is added to the final result under the workspace-files key so the agent knows where to read produced files.

*Call graph*: called by 1 (run); 3 external calls (quote, contained_leaf, uuid4).


##### `_ConnectorCall._translated_node`  (lines 583–643)

```
async def _translated_node(self, node: Mapping[str, object], depth: int=0) -> dict[str, object]
```

**Purpose**: Rewrites result objects that contain provider-marked base64 content into readable text or workspace file references. This keeps huge encoded blobs from filling the conversation.

**Data flow**: It receives a mapping from the broker response and a recursion depth. It first recursively translates children, then looks for marker fields such as `encoding: base64` and content fields such as `content` or `data`. Valid base64 is decoded and passed to `_translated_bytes`; marker fields are updated only when all marked fields were successfully translated.

**Call relations**: It is called first by `_ConnectorCall.run` on the broker response and later by `_translated` for nested dictionaries. It uses `_decoded_base64` for decoding and `_translated_bytes` to decide between inline text and file offload.

*Call graph*: calls 3 internal fn (_translated, _translated_bytes, _decoded_base64); called by 2 (_translated, run); 1 external calls (guess_type).


##### `_ConnectorCall._translated`  (lines 645–664)

```
async def _translated(self, value: object, depth: int) -> object
```

**Purpose**: Recursively translates any value inside a connector result. It handles nested dictionaries, lists, and standalone `data:...;base64,...` URLs.

**Data flow**: It receives a value and a depth counter. Deeply nested values past the safety limit are returned unchanged. Dictionaries go to `_translated_node`, lists are walked item by item, and short enough data URLs are passed to `_translated_data_url`. Other values pass through unchanged.

**Call relations**: It is called by `_translated_node` while walking a result object. It loops back to `_translated_node` for dictionaries and delegates data URL strings to `_translated_data_url`.

*Call graph*: calls 2 internal fn (_translated_data_url, _translated_node); called by 1 (_translated_node).


##### `_ConnectorCall._translated_data_url`  (lines 666–678)

```
async def _translated_data_url(self, value: str) -> object
```

**Purpose**: Decodes a standalone base64 data URL in a connector result. A data URL is a string that carries both a MIME type and encoded bytes.

**Data flow**: It receives a string beginning with `data:`. If it matches the expected base64 data URL shape, it decodes the payload, chooses a fallback filename from the MIME type, and sends the bytes to `_translated_bytes`. If parsing or decoding fails, it returns the original string.

**Call relations**: It is called by `_translated` for candidate data URL strings. It uses `_decoded_base64` for safe decoding and `_translated_bytes` for inline-versus-file handling.

*Call graph*: calls 2 internal fn (_translated_bytes, _decoded_base64); called by 1 (_translated); 1 external calls (guess_extension).


##### `_ConnectorCall._translated_bytes`  (lines 680–689)

```
async def _translated_bytes(self, decoded: bytes, text: str | None, name: str, mimetype: str) -> object
```

**Purpose**: Chooses how decoded bytes should appear in the final tool result. Small readable text stays inline; large text or binary data becomes a workspace file reference.

**Data flow**: It receives decoded bytes, optional decoded UTF-8 text, a filename, and a MIME type. If the text exists and is under the inline size limit, it returns the text. Otherwise it calls `_offloaded` to write the bytes into the workspace and returns that file reference.

**Call relations**: It is called by `_translated_node` for marked base64 fields and by `_translated_data_url` for base64 data URLs. It delegates file writing to `_offloaded` when inline text is not suitable.

*Call graph*: calls 1 internal fn (_offloaded); called by 2 (_translated_data_url, _translated_node).


##### `_ConnectorCall._offloaded`  (lines 691–733)

```
async def _offloaded(self, name: str, mimetype: str, data: bytes) -> dict[str, object]
```

**Purpose**: Writes decoded result bytes into the workspace and returns a reference to them. It uses content-based paths so the same bytes do not create endless duplicate files.

**Data flow**: It receives a suggested name, MIME type, and byte content. It sanitizes the filename, builds a path based on the SHA-256 hash of the bytes, writes a temporary file through the sandbox, then atomically places it at the final guarded path. It returns name, workspace path, MIME type, and byte count.

**Call relations**: It is called by `_translated_bytes` when decoded data should not be put directly into the chat context. Its returned object replaces the original base64 field in the final connector result.

*Call graph*: called by 1 (_translated_bytes); 3 external calls (sha256, contained_leaf, uuid4).


##### `_ConnectorCall._deduped`  (lines 735–793)

```
def _deduped(self, payload: dict[str, object]) -> str
```

**Purpose**: Serializes the final connector payload and, when it is safe and worthwhile, replaces repeated large objects with pointers to their first copy. This keeps long, repetitive API results easier for the model to keep in context.

**Data flow**: It receives the final result dictionary. It serializes it once, skips deduplication if the result is too large, too structurally dense, or already contains the reserved `same_as` key, and otherwise walks each top-level value through `_condensed`. It returns JSON text, either original or condensed.

**Call relations**: It is called from `_ConnectorCall.run` inside `asyncio.to_thread`, meaning the CPU-heavy walk runs away from the main async loop. It uses `_escaped` to build JSON Pointer paths and `_condensed` to find repeated structures.

*Call graph*: calls 2 internal fn (_condensed, _escaped); 1 external calls (dumps).


##### `_ConnectorCall._condensed`  (lines 795–871)

```
def _condensed(self, value: object, pointer: str, depth: int, first: dict[bytes, str]) -> tuple[object, bytes, int]
```

**Purpose**: Walks one node of a result and detects whether the same dictionary has appeared before. If a large object repeats, it replaces the later copy with a `same_as` pointer.

**Data flow**: It receives a value, its JSON Pointer path, current depth, and a table of first-seen object hashes. It recursively computes a structural hash and approximate original size. Dictionaries above the size floor are recorded the first time and replaced on later repeats; lists and scalar values help build hashes but are not themselves replaced.

**Call relations**: It is called by `_deduped` and recursively calls itself for child values. It uses `_escaped` when extending paths and SHA-256 hashes to identify structurally identical nodes.

*Call graph*: calls 1 internal fn (_escaped); called by 1 (_deduped); 1 external calls (sha256).


##### `_escaped`  (lines 874–877)

```
def _escaped(token: str) -> str
```

**Purpose**: Escapes one path token for a JSON Pointer. This makes keys containing `/` or `~` safe to use inside `same_as` references.

**Data flow**: It receives a string key. It replaces `~` with `~0` and `/` with `~1`. It returns the escaped token.

**Call relations**: It is used by `_deduped` and `_condensed` whenever they build a pointer to a location in the JSON result. Those pointers are what later repeated objects refer back to.

*Call graph*: called by 2 (_condensed, _deduped).


##### `_decoded_base64`  (lines 880–904)

```
def _decoded_base64(value: object) -> tuple[bytes, str | None] | None
```

**Purpose**: Safely decodes a provider-marked base64 string. It refuses values that are too large or not valid base64, instead of guessing and corrupting ordinary text.

**Data flow**: It receives any value. Non-strings and oversized strings return nothing. For strings, whitespace is removed, strict base64 decoding is attempted, and then UTF-8 text decoding is attempted. It returns bytes plus text when text is available, bytes plus `null` for binary data, or nothing on failure.

**Call relations**: It is called by `_translated_node` for fields marked as base64 and by `_translated_data_url` for data URLs. Its output decides whether `_translated_bytes` can inline text or must save bytes to a file.

*Call graph*: called by 2 (_translated_data_url, _translated_node); 1 external calls (b64decode).


##### `search_connector_tools`  (lines 907–921)

```
async def search_connector_tools(ctx: ToolContext, args: SearchConnectorToolsInput) -> ToolResult
```

**Purpose**: Runs a richer search for tools inside one connector using a natural-language goal. It returns matching tool schemas plus broker-supplied advice such as plan, guidance, and pitfalls.

**Data flow**: It receives a connector source ID and query. It finds the connector entry, asks its broker to search, normalizes the returned tools through `_discovered_rows`, and builds a JSON object with tools and advisory text. It returns that as a tool result.

**Call relations**: This is a public discovery tool. It uses `_registry` to find the connector, `_discovered_rows` to enforce the shared discovery fallback and budget behavior, and `_json_result` to package the response.

*Call graph*: calls 3 internal fn (_discovered_rows, _json_result, _registry).


##### `_registry`  (lines 924–927)

```
def _registry(ctx: ToolContext) -> ConnectorRegistry
```

**Purpose**: Returns the connector registry for the current turn. It fails loudly if connector tools were called without registry data.

**Data flow**: It receives the tool context. If `ctx.connectors` is present, it returns it. If not, it raises an error explaining that connector dispatch lacks the registry.

**Call relations**: It is the common entry point for connector lookup. `list_external_tools`, `describe_external_tools`, `search_connector_tools`, and `call_external_tool` all call it before talking to any broker.

*Call graph*: called by 4 (call_external_tool, describe_external_tools, list_external_tools, search_connector_tools).


##### `_tool_json`  (lines 930–931)

```
def _tool_json(tool: BrokerTool) -> dict[str, object]
```

**Purpose**: Turns a broker tool object into the compact JSON shape shown to the agent. It keeps the slug, description, and input schema.

**Data flow**: It receives a `BrokerTool`. It reads its identifying slug, human description, and input schema. It returns a plain dictionary suitable for JSON serialization.

**Call relations**: It is called by `describe_external_tools` for exact schemas and by `_available_tools` for catalog listings. This keeps tool rows consistent across discovery paths.

*Call graph*: called by 2 (_available_tools, describe_external_tools).


##### `_discovered_rows`  (lines 934–951)

```
async def _discovered_rows(entry: ConnectorEntry, workspace_id: UUID, query: str, found: tuple[BrokerTool, ...]) -> tuple[list[dict[str, object]], str]
```

**Purpose**: Builds the visible tool rows for discovery and adds a note when search had to fall back. This prevents a no-match search from looking like the connector has no useful tools.

**Data flow**: It receives a connector entry, workspace ID, query, and tools found by the broker. If a non-empty query found nothing, it asks for the connector’s unfiltered top tools instead. It trims rows through `_available_tools` and returns rows plus any explanatory note.

**Call relations**: It is shared by `describe_external_tools` and `search_connector_tools`. This centralizes the “never a dead end” behavior and the inline-result size budget for both discovery tools.

*Call graph*: calls 1 internal fn (_available_tools); called by 2 (describe_external_tools, search_connector_tools).


##### `_available_tools`  (lines 954–967)

```
def _available_tools(listed: tuple[BrokerTool, ...]) -> list[dict[str, object]]
```

**Purpose**: Selects as many tool rows as can fit in the inline discovery budget. This avoids returning such a huge catalog that the engine must move it to a file.

**Data flow**: It receives a tuple of broker tools. It converts each with `_tool_json`, counts the JSON size added by each row, and stops after the budget is exceeded, as long as at least one row is already included. It returns the chosen rows.

**Call relations**: It is called by `_discovered_rows` for both plain descriptions and semantic searches. `_discovered_rows` compares its output length to the broker listing to decide whether to add an omitted-tools note.

*Call graph*: calls 1 internal fn (_tool_json); called by 1 (_discovered_rows); 1 external calls (dumps).


##### `_discovery_query`  (lines 970–977)

```
def _discovery_query(explicit: str, unresolved: list[str]) -> str
```

**Purpose**: Creates a useful catalog search query when exact tool names failed. It turns guessed slugs into ordinary words so the broker can suggest real matching tools.

**Data flow**: It receives an explicit query and a list of unresolved tool names. If the explicit query exists, it returns it. Otherwise it lowercases unresolved names, replaces punctuation with spaces, removes duplicate words while keeping order, and returns the resulting phrase.

**Call relations**: It is called by `describe_external_tools` when unresolved exact names or missing tool names require discovery. Its query is then passed into the broker listing flow that `_discovered_rows` normalizes.

*Call graph*: called by 1 (describe_external_tools); 1 external calls (sub).


##### `_json_result`  (lines 980–981)

```
def _json_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: Wraps a dictionary as the standard JSON text returned by a tool. It is a small helper that keeps public discovery outputs formatted the same way.

**Data flow**: It receives a payload dictionary. It serializes the dictionary to JSON text, puts that text into a `TextContent`, and wraps it in a `ToolResult`. It returns the tool result.

**Call relations**: It is called by `list_external_tools`, `describe_external_tools`, and `search_connector_tools`. `call_external_tool` builds its result directly because `_ConnectorCall.run` already returns serialized response text.

*Call graph*: called by 3 (describe_external_tools, list_external_tools, search_connector_tools); 3 external calls (__init__, __init__, dumps).


### Evaluation Connector Fakes
Define deterministic fake connector manifests and seeded services for reliable evaluation runs.

### `extensions/eval_env/ufo_ext_eval_env/manifest.py`

`domain_logic` · `eval runs and connector/tool request handling`

This file is the extension manifest and working engine for the evaluation environment. Its job is to give agents realistic tools, while keeping every result controlled and repeatable. Think of it like a practice office building: the doors, mailboxes, calendars, and filing cabinets work through the same entrances as production, but the contents are staged for a test.

The file declares connector providers, their tool catalogs, and the small data shapes used to validate tool inputs. Email and calendar get their own database tables because agents can change them during a conversation. Sending an email writes a row. Creating, updating, or cancelling a calendar event changes durable stored rows. Other read-only services, such as code search or GitHub fixture lists, read exact seeded responses from the extension store. If a needed fixture is missing, the code raises an error instead of quietly returning empty data, so an evaluation cannot accidentally pass without the right setup.

The file also defines an application action object used by app benchmarks. Applying one of these actions mutates a seeded connector fixture in a fixed, checkable way. Finally, it registers a private repair agent and a safety hook that tightly limits which file that agent can read or edit and how much it can edit.

#### Function details

##### `_transaction`  (lines 340–344)

```
def _transaction()
```

**Purpose**: Creates a database transaction tied to this evaluation extension. The broker uses it whenever it needs to read or change the eval email or calendar tables safely.

**Data flow**: It takes no direct input. It builds an extension context with this extension's scoped store and no declared credentials, then returns a transaction object. Callers use that transaction to run database statements, and the changes become part of the extension's workspace-scoped state.

**Call relations**: Email and calendar methods call this before touching their tables. It is the shared doorway that `_send_email`, `_list_emails`, `_create_event`, `_list_events`, and `_change_event` use so they all write and read through the same extension storage path.

*Call graph*: called by 5 (_change_event, _create_event, _list_emails, _list_events, _send_email); 3 external calls (__init__, __init__, __init__).


##### `_moment`  (lines 347–351)

```
def _moment(value: str) -> datetime
```

**Purpose**: Turns an ISO-formatted time string into a real datetime value. If the string has no timezone, it treats it as UTC, meaning Coordinated Universal Time.

**Data flow**: It receives a text timestamp. It parses the text into a datetime object, adds UTC if the parsed value had no timezone, and returns the normalized datetime. Nothing else is changed.

**Call relations**: Calendar creation and update both call this before storing event times. It keeps event time handling consistent for `_create_event` and `_update_event`.

*Call graph*: called by 2 (_create_event, _update_event); 1 external calls (fromisoformat).


##### `EvalEnvBroker.tools`  (lines 359–367)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Returns the list of tools available for one provider, optionally filtered by a search phrase. This lets the connector system answer questions like, “What email tools can I use?”

**Data flow**: It receives a workspace id, provider name, and search text. It looks up that provider's fixed tool catalog, filters by tool name or description when a query is present, and returns matching tools. If no filtered tools match, it falls back to the full catalog.

**Call relations**: The broker's `search` method calls this when the connector registry asks for discoverable tools. It reads the in-file catalog and does not call out to any external service.

*Call graph*: called by 1 (search).


##### `EvalEnvBroker.schema`  (lines 369–373)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Finds the definition for one named tool, including its input shape. This is used before a tool is called so the system knows what arguments are valid.

**Data flow**: It receives a workspace id, provider name, and tool slug. It scans the provider's catalog for that slug and returns the matching tool definition. If the slug is unknown, it raises an unknown-tool error.

**Call relations**: `execute` calls this for fixture-backed providers before returning seeded data. That means even simple read-only fixture tools still have their names checked against the registered catalog.

*Call graph*: called by 1 (execute); 1 external calls (__init__).


##### `EvalEnvBroker.execute`  (lines 375–428)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Runs a requested eval connector tool. It is the main dispatch point that turns a provider name, tool name, and arguments into an email, calendar, code search, or seeded fixture response.

**Data flow**: It receives the workspace, provider, tool slug, argument map, account id, and optional idempotency key. It validates arguments using the matching input model, routes the call to the correct helper, reads seeded data for read-only app providers, or raises an error for an unknown tool or missing fixture. It returns a plain dictionary response for the connector call.

**Call relations**: The connector runtime calls this when an agent invokes an external tool. It hands email calls to `_send_email` or `_list_emails`, calendar calls to event helpers, code search to `_search_code`, and read-only business app calls through `schema` plus the scoped store.

*Call graph*: calls 8 internal fn (_cancel_event, _create_event, _list_emails, _list_events, _search_code, _send_email, _update_event, schema); 2 external calls (__init__, __init__).


##### `EvalEnvBroker._search_code`  (lines 430–438)

```
async def _search_code(self, args: SearchCodeArgs) -> dict[str, object]
```

**Purpose**: Returns the exact code-search response that the evaluation seeded for a query. It intentionally fails if no response was seeded.

**Data flow**: It receives validated search arguments containing a query string. It looks in the extension store under a key made from that query. If it finds a dictionary, it copies and returns it; otherwise it raises an error saying the fixture is missing.

**Call relations**: `execute` calls this for the code search provider. This keeps code search deterministic: the agent sees only the prepared response for that exact query.

*Call graph*: called by 1 (execute); 1 external calls (__init__).


##### `EvalEnvBroker._send_email`  (lines 440–455)

```
async def _send_email(self, workspace_id: UUID, args: SendEmailArgs) -> dict[str, object]
```

**Purpose**: Simulates sending an email by writing it into the eval environment's sent-mail table. This gives graders a durable record of what the agent sent.

**Data flow**: It receives the workspace id and validated email fields: recipients, subject, and body. It creates a new email id, stores a sent email row with the assistant's fixed address and current time, then returns the id, sent status, and recipients.

**Call relations**: `execute` calls this when the email connector's `send_email` tool is used. It uses `_transaction` so the inserted email is visible later to eval checks and list calls.

*Call graph*: calls 1 internal fn (_transaction); called by 1 (execute); 3 external calls (now, insert, uuid4).


##### `EvalEnvBroker._list_emails`  (lines 457–492)

```
async def _list_emails(self, workspace_id: UUID, args: ListEmailsArgs) -> dict[str, object]
```

**Purpose**: Lists emails from the eval mailbox, optionally searching by sender, subject, or body. This lets agents inspect seeded inbox mail or their own sent messages.

**Data flow**: It receives the workspace id plus folder, query text, and limit. It builds database filters for the workspace and folder, adds a case-insensitive text search if requested, reads matching rows newest-first, and returns them as simple email dictionaries.

**Call relations**: `execute` calls this for the email connector's `list_emails` tool. It uses `_transaction` for the read and returns data shaped for the broker response.

*Call graph*: calls 1 internal fn (_transaction); called by 1 (execute); 2 external calls (or_, select).


##### `EvalEnvBroker._create_event`  (lines 494–508)

```
async def _create_event(self, workspace_id: UUID, args: CreateEventArgs) -> dict[str, object]
```

**Purpose**: Creates a calendar event in the eval calendar. The event is stored durably so later tool calls and graders can see it.

**Data flow**: It receives the workspace id and validated event fields. It generates an event id, converts start and end time strings into datetime values, inserts a confirmed event row, and returns the new id and status.

**Call relations**: `execute` calls this when the calendar connector's `create_event` tool is used. It relies on `_moment` for time parsing and `_transaction` for the database write.

*Call graph*: calls 2 internal fn (_moment, _transaction); called by 1 (execute); 2 external calls (insert, uuid4).


##### `EvalEnvBroker._list_events`  (lines 510–523)

```
async def _list_events(self, workspace_id: UUID, args: ListEventsArgs) -> dict[str, object]
```

**Purpose**: Lists calendar events for the workspace, optionally filtered by title. Cancelled events remain visible with their cancelled status.

**Data flow**: It receives the workspace id, optional title query, and limit. It reads matching event rows ordered by start time, converts each row to a response dictionary, and returns them under an `events` key.

**Call relations**: `execute` calls this for the calendar connector's `list_events` tool. It delegates row formatting to `_event_json` so list and update responses use the same shape.

*Call graph*: calls 2 internal fn (_event_json, _transaction); called by 1 (execute); 1 external calls (select).


##### `EvalEnvBroker._update_event`  (lines 525–537)

```
async def _update_event(self, workspace_id: UUID, args: UpdateEventArgs) -> dict[str, object]
```

**Purpose**: Updates selected fields on an existing calendar event. It refuses empty updates so a caller must actually change something.

**Data flow**: It receives the workspace id and validated update arguments. It builds a change set from any provided title, start, end, or attendees fields, converting times with `_moment`. It then passes those changes to `_change_event` and returns the updated event.

**Call relations**: `execute` calls this for the calendar connector's `update_event` tool. It prepares the requested changes, while `_change_event` performs the database update and final lookup.

*Call graph*: calls 2 internal fn (_change_event, _moment); called by 1 (execute).


##### `EvalEnvBroker._cancel_event`  (lines 539–540)

```
async def _cancel_event(self, workspace_id: UUID, args: CancelEventArgs) -> dict[str, object]
```

**Purpose**: Marks an event as cancelled instead of deleting it. This mirrors calendars where cancelled meetings can still appear in history.

**Data flow**: It receives the workspace id and event id. It asks `_change_event` to set the event status to `cancelled`, then returns the updated event dictionary.

**Call relations**: `execute` calls this for the calendar connector's `cancel_event` tool. It is a small wrapper around `_change_event` for the specific cancellation case.

*Call graph*: calls 1 internal fn (_change_event); called by 1 (execute).


##### `EvalEnvBroker._change_event`  (lines 542–561)

```
async def _change_event(self, workspace_id: UUID, event_id: str, changes: dict[str, object]) -> dict[str, object]
```

**Purpose**: Applies a set of changes to one calendar event and returns the fresh event state. It also checks that exactly one event in the workspace was changed.

**Data flow**: It receives the workspace id, event id text, and a dictionary of column changes. It converts the event id to a UUID, updates the matching row, errors if no matching event exists, reads the updated row, and returns it as a plain event dictionary.

**Call relations**: `_update_event` and `_cancel_event` both call this after deciding what needs to change. It uses `_transaction` for the database work and `_event_json` for the final response shape.

*Call graph*: calls 2 internal fn (_event_json, _transaction); called by 2 (_cancel_event, _update_event); 3 external calls (select, update, UUID).


##### `EvalEnvBroker._event_json`  (lines 563–571)

```
def _event_json(self, row: sa.Row) -> dict[str, object]
```

**Purpose**: Converts a database calendar row into the public response shape used by connector tools. This keeps event output consistent.

**Data flow**: It receives a database row. It pulls out the id, title, start and end times, attendees, and status, turns ids and times into text, and returns a dictionary. It does not change storage.

**Call relations**: `_list_events` calls it for every listed row, and `_change_event` calls it after an update or cancellation. It is the shared formatter for calendar responses.

*Call graph*: called by 2 (_change_event, _list_events).


##### `EvalEnvBroker.file_outputs`  (lines 573–574)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: States that eval connector calls do not produce downloadable files. The broker interface needs this method even though these providers return only data.

**Data flow**: It receives a tool response dictionary and ignores it. It always returns an empty tuple, meaning there are no file outputs to attach.

**Call relations**: The connector framework may ask the broker for file outputs after a tool call. This implementation closes that path for eval providers.


##### `EvalEnvBroker.stage_upload`  (lines 576–585)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Rejects file uploads for all eval environment providers. These deterministic connectors are data-only and do not accept uploaded files.

**Data flow**: It receives workspace, provider, tool, filename, MIME type, and checksum information. Instead of staging anything, it raises an error explaining that uploads are not supported.

**Call relations**: This satisfies the broker upload interface. If any caller tries to upload through an eval provider, this method stops it immediately.


##### `EvalEnvBroker.search`  (lines 587–588)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Wraps the provider's matching tools in the broker search response format. This supports connector tool discovery.

**Data flow**: It receives a workspace id, provider name, and search query. It asks `tools` for the matching tool list, puts that list into a broker search object, and returns it.

**Call relations**: The connector runtime uses this when searching available external tools. It delegates the actual filtering to `tools`.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `EvalEnvBroker.credential`  (lines 590–591)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a simple fake bearer credential for an eval account. It lets the connector machinery proceed without using a real OAuth token.

**Data flow**: It receives workspace, provider, and account identifiers. It creates a credential whose bearer token is predictable text based on the account and returns it. It does not read or write external secrets.

**Call relations**: The broker interface expects credentials when calling providers. This method supplies a harmless deterministic credential for the eval environment.

*Call graph*: 1 external calls (__init__).


##### `_EvalEnvOAuth.authorize_url`  (lines 602–603)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds a placeholder OAuth authorization URL for the eval provider. OAuth is the common web flow where a user grants an app access, but evals usually seed grants directly.

**Data flow**: It receives state text and a redirect URI. It combines them with the provider's fake host into an authorization URL string and returns it.

**Call relations**: Connector registration needs an OAuth descriptor. This method fulfills that contract even though normal eval runs do not exercise the full connect flow.


##### `_EvalEnvOAuth.exchange`  (lines 605–608)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Completes the fake OAuth exchange by returning the fixed eval account id. It keeps the interface honest without contacting a real service.

**Data flow**: It receives an authorization code, redirect URI, workspace id, and state. It ignores the external-looking values and returns an OAuth account object with the fixed account id.

**Call relations**: If the connector registry ever drives the OAuth exchange for this eval provider, this method supplies the account result expected by the framework.

*Call graph*: 1 external calls (__init__).


##### `AppActionStore.list`  (lines 615–633)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists applied eval application actions as objects. This lets the object system show which fixed benchmark actions have already been taken.

**Data flow**: It receives a tool context and list query. It reads stored action records with the app-action key prefix, validates each record, turns them into object rows with summary fields, and returns a paged object list.

**Call relations**: The object framework calls this when listing the eval app-action object kind. It uses `_ext` to get the extension context and `object_page` to apply the requested paging or filtering.

*Call graph*: calls 1 internal fn (_ext); 2 external calls (__init__, object_page).


##### `AppActionStore.get`  (lines 635–643)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[AppActionSpec] | None
```

**Purpose**: Retrieves the saved specification for one applied application action. It gives callers the original requested action and its timestamps.

**Data flow**: It receives a tool context and object name. It loads the stored record by name; if none exists, it returns null. Otherwise it returns an object detail containing the action spec and creation/update times.

**Call relations**: The object framework calls this when a user or tool asks for one app-action object. It delegates storage lookup to `_stored` and wraps the result in an object detail.

*Call graph*: calls 1 internal fn (_stored); 1 external calls (__init__).


##### `AppActionStore.status`  (lines 645–655)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports whether an application action has been applied and what result it produced. This gives a compact status view without returning the full spec.

**Data flow**: It receives a tool context, object name, and optional expected generation. It loads the stored record; if missing, it returns null. If present, it returns state `applied` and the recorded result message.

**Call relations**: The object framework calls this to check action state. It relies on `_stored` for the read and does not change the fixture or object record.

*Call graph*: calls 1 internal fn (_stored).


##### `AppActionStore.apply`  (lines 657–681)

```
async def apply(self, ctx: ToolContext, name: str, spec: AppActionSpec, old: AppActionSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Applies one fixed benchmark application action, but only if it has not already been applied differently. This makes app actions safe to retry while preventing conflicting rewrites.

**Data flow**: It receives a context, action name, requested spec, old spec, and optional generation check. It looks for an existing record. If the same action already exists, it returns quietly; if a different request exists under the same name, it errors. Otherwise it mutates the relevant seeded fixture, records the result and timestamps, and stores the action record.

**Call relations**: The object framework calls this when an app-action object is applied. It uses `_stored` to detect prior work, `_apply_fixture` to change the seeded connector data, and `_ext` to save the resulting action record.

*Call graph*: calls 3 internal fn (_apply_fixture, _ext, _stored); 2 external calls (__init__, now).


##### `AppActionStore.delete`  (lines 683–690)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects deletion of eval application actions. Once applied, these actions are immutable so the evaluation history cannot be erased through the object API.

**Data flow**: It receives a context, action name, and optional generation check. It does not inspect or alter storage. It always raises a not-supported error.

**Call relations**: The object framework may call this if deletion is requested. This method deliberately blocks that path for app-action objects.

*Call graph*: 1 external calls (__init__).


##### `AppActionStore._apply_fixture`  (lines 692–756)

```
async def _apply_fixture(self, ctx: ToolContext, name: str, spec: AppActionSpec) -> str
```

**Purpose**: Performs the actual fixed mutation behind an application action. It changes a seeded GitHub-style fixture in a small, predictable way and returns a human-readable result.

**Data flow**: It receives a context, action name, and action spec. It chooses the right seeded fixture based on the benchmark case, deep-copies the stored response, finds or adds the required issue or pull request data, stores the mutated fixture under an action-specific key, and returns the result message. If the fixture or requested action does not match the allowed cases, it raises an error.

**Call relations**: `apply` calls this only for new actions. It uses `_ext` to reach extension storage and is the place where benchmark actions such as creating issue #900, assigning issue #521, or enabling a PR babysitter become visible to later grading.

*Call graph*: calls 1 internal fn (_ext); called by 1 (apply); 2 external calls (dumps, loads).


##### `AppActionStore._stored`  (lines 758–760)

```
async def _stored(self, ctx: ToolContext, name: str) -> StoredAppAction | None
```

**Purpose**: Loads one stored application action record by name. It centralizes the key format and validation for action records.

**Data flow**: It receives a context and action name. It reads the extension store under the app-action prefix. If nothing is stored, it returns null; otherwise it validates the saved value as a stored action record and returns it.

**Call relations**: `get`, `status`, and `apply` call this whenever they need to know whether an action already exists. It uses `_ext` to get the extension context.

*Call graph*: calls 1 internal fn (_ext); called by 3 (apply, get, status).


##### `AppActionStore._ext`  (lines 762–765)

```
def _ext(self, ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Gets the extension context from a tool context and fails clearly if it is missing. The extension context is what provides access to this extension's scoped storage.

**Data flow**: It receives a tool context. If the context contains an extension context, it returns it. If not, it raises an error because app actions cannot work without extension storage.

**Call relations**: The app-action store helpers and list/apply paths call this before reading or writing extension data. It is a guardrail that prevents these objects from running outside their expected environment.

*Call graph*: called by 4 (_apply_fixture, _stored, apply, list).


##### `bound_app_qa_repair_tools`  (lines 778–833)

```
async def bound_app_qa_repair_tools(ctx: HookContext)
```

**Purpose**: Enforces strict tool limits for the private app QA repair agent. It lets that agent read and edit only one source file, forbids broad replacements, and tracks an edit budget.

**Data flow**: It receives a hook context just before a tool is used. If the current agent is not the repair agent, it does nothing. For the repair agent, it inspects the requested tool and input: allowed reads pass, allowed edits are counted by call count and byte size, and disallowed tools, paths, oversized edits, replace-all edits, or concurrent budget changes return a denial reason.

**Call relations**: The manifest registers this as a `pre_tool_use` hook for read and edit tools. It runs before the repair agent's tool call reaches the tool itself, acting like a gatekeeper at the door.

*Call graph*: 2 external calls (__init__, __init__).


##### `manifest`  (lines 853–909)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension manifest that the UFO platform loads. The manifest advertises the eval connectors, object kind, private repair agent, and safety hook.

**Data flow**: It creates one eval broker, wraps each provider with a fake OAuth descriptor and label, includes the app-action object and repair agent, registers the pre-tool-use hook, and returns the completed manifest object.

**Call relations**: The extension loader calls this at startup or registration time. It wires together the broker, OAuth stubs, object store, agent provision, and hook so the rest of the system can discover and use this eval environment.

*Call graph*: 5 external calls (__init__, __init__, __init__, __init__, __init__).
