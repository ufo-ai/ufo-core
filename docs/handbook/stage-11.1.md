# Credentialed connector and external API tools  `stage-11.1`

This stage is the system’s safe doorway to outside services. It is shared support used when the agent needs to find or run tools from apps like Gmail, GitHub, Slack, Notion, or web search, without seeing private passwords or access tokens. The core connector boundary decides which tools exist, how they are called, and how background sync jobs get temporary access safely.

Several adapters plug into that boundary. Composio and Pipedream clients talk to their hosted services, create login links, check connected accounts, list available actions, upload files, and run tools. Their brokers translate UFO’s standard tool requests into each service’s format. Their proxy files act like privacy screens: UFO sends a normal request, the proxy routes it through Composio or Pipedream, and the real secret stays hidden.

The connector tools file presents these outside actions to the agent in a searchable, inspectable form. The MCP extension adds tools published by workspace-configured MCP servers, a standard tool-sharing protocol. Perplexity provides web search and page reading through an API key. Slack tools guide workspace setup and conversation search. Together, these pieces let the agent use external services safely and consistently.

## Files in this stage

### Connector broker entry points
Composio and Pipedream brokers present external app catalogs as safe, discoverable connector tools.

### `extensions/composio/ufo_ext_composio/broker.py`

`io_transport` · `request handling`

A broker is the shared doorway the UFO system uses to talk to a provider through Composio. This file defines that doorway for Composio. Without it, the agent could not reliably find Composio tools, run them for the right workspace, pass files in and out, or safely proxy provider HTTP requests.

The main class, ComposioBroker, is intentionally stateless. Instead of keeping one long-lived client, each method asks for the current Composio client when it runs. That matters for tests and for safe connection behavior: a changed transport or API setup is honored immediately.

The broker turns Composio's raw tool listings into UFO's BrokerTool objects, including short descriptions, input schemas, and a read-only hint. It also rewrites Composio file-upload parameters into the workspace-file shape UFO expects. When executing a tool, it runs the request as the workspace's broker user. If Composio says a tool slug was not found, the broker tries to make the error more helpful by listing real available slugs. If the problem is an old or missing connected account, it gives reconnect guidance instead, so the agent does not mistakenly suggest different tools.

For files, it can find Composio's file-result objects inside nested responses and can create an upload slot before a tool call. For credentials, it verifies that the connected account belongs to this workspace's broker user and returns a proxy transport, not a raw token.

#### Function details

##### `ComposioBroker.tools`  (lines 49–50)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Finds tools for a provider that match a user's search text. It returns them in UFO's common tool format, so the rest of the system does not need to know Composio's raw response shape.

**Data flow**: It receives a workspace id, provider name, and query text. It asks the current Composio client for matching tools, then passes the raw rows through the local converter that trims and normalizes them. It returns a tuple of BrokerTool objects.

**Call relations**: This is used when the system is discovering what a connector can do. It calls the shared Composio client for the live catalog, then hands the response to _discovered_tools so discovery results look the same as other brokers' results.

*Call graph*: calls 1 internal fn (_discovered_tools); 1 external calls (composio_client).


##### `ComposioBroker.schema`  (lines 52–66)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Fetches the full input schema for one Composio tool. This tells the agent what arguments the tool accepts before trying to run it.

**Data flow**: It receives a workspace id, provider, and tool slug. It asks Composio for that slug's schema, rewrites file-upload fields into UFO's workspace-file format, checks whether the tool is marked read-only, and returns a BrokerTool. If Composio says the slug does not exist, it raises UnknownBrokerTool.

**Call relations**: This sits between planning and execution: after a possible tool is chosen, the system can call this to learn the exact input shape. It uses _read_only for the safety hint and the Composio client's schema and file-schema helpers to translate external data into UFO's internal tool description.

*Call graph*: calls 1 internal fn (_read_only); 4 external calls (__init__, __init__, composio_client, workspace_file_schema).


##### `ComposioBroker.execute`  (lines 68–91)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Runs a selected Composio tool for a workspace and connected account. It also turns confusing Composio failures into more useful errors for the agent and user.

**Data flow**: It receives the workspace id, provider, tool slug, argument values, connected account id, and optional idempotency key, which helps avoid duplicate side effects on retries. It builds the Composio external user id from the workspace id and sends the execution request. On success it returns Composio's response dictionary. On failure it either adds reconnect guidance for stale accounts, augments a missing-tool error with real slugs, or re-raises the original error.

**Call relations**: This is the broker's main run step. It calls the current Composio client to execute the tool. If execution fails, it consults _stale_account to decide whether the account needs reconnecting, _reconnect_error to build that message, or _slug_miss to help recover from a bad tool slug.

*Call graph*: calls 3 internal fn (_slug_miss, _reconnect_error, _stale_account); 1 external calls (composio_client).


##### `ComposioBroker.file_outputs`  (lines 93–98)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Finds files produced by a Composio tool inside a tool response. This lets later parts of the system download or present generated files without knowing exactly where Composio nested them.

**Data flow**: It receives the full response dictionary from a tool execution. It creates an empty list, asks _collect_files to walk through the nested response, and returns every discovered file as BrokerFile objects in a tuple.

**Call relations**: This is called after tool execution when the system wants to extract file results. It delegates the recursive searching to _collect_files, keeping the public method simple.

*Call graph*: calls 1 internal fn (_collect_files).


##### `ComposioBroker.stage_upload`  (lines 100–116)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Creates a temporary upload destination for a file that will be passed into a Composio tool. Think of it as reserving a labeled drop box before the tool is called.

**Data flow**: It receives the workspace id, provider, tool slug, filename, MIME type, and MD5 checksum. It asks Composio to create an upload slot, then returns a StagedUpload containing the URL to upload bytes to, the content type to use, and the argument object that should later be passed to the tool.

**Call relations**: This runs before a tool call that needs a file input. It relies on the Composio client to create the remote slot, then packages the result in UFO's standard StagedUpload shape so the dynamic connector tools know what to upload and what argument to send.

*Call graph*: 2 external calls (__init__, composio_client).


##### `ComposioBroker.search`  (lines 118–121)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Searches Composio's tool router for connector tools relevant to a query. This is broader than a plain listing and is meant to help choose useful tools from natural search text.

**Data flow**: It receives a workspace id, provider, and query. It gets the current Composio client and passes everything to Composio's search helper. It returns a BrokerSearch result.

**Call relations**: This supports discovery and planning when the agent is looking for a good tool. The method is a thin bridge to search_connector_tools, ensuring the search uses the current Composio client and the current workspace.

*Call graph*: 2 external calls (composio_client, search_connector_tools).


##### `ComposioBroker.credential`  (lines 123–139)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Creates a safe credential object for provider HTTP access through Composio's proxy. It confirms the connected account belongs to this workspace's broker user and never hands out the provider's real secret token.

**Data flow**: It receives the workspace id, provider, and connected account id. It builds the broker-user id, asks Composio to verify that the account is connected for that user and provider, and if valid returns a Credential whose transport sends HTTP through Composio's proxy. If the account is not found, it raises GrantUnusable with guidance to reconnect.

**Call relations**: This is used when another part of the system needs provider-like HTTP access without holding secrets itself. It calls the Composio client to check ownership, then builds ComposioProxyTransport and wraps it in Credential. If the grant is stale, it uses stale_grant_guidance so the user gets the right next step.

*Call graph*: calls 1 internal fn (__init__); 5 external calls (__init__, __init__, AsyncHTTPTransport, stale_grant_guidance, composio_client).


##### `ComposioBroker._slug_miss`  (lines 141–162)

```
async def _slug_miss(self, client: composio.ComposioClient, provider: str, slug: str, error: composio.ComposioError) -> composio.ComposioError
```

**Purpose**: Improves a missing-tool error by adding likely valid tool slugs for the same provider. This helps the agent recover from using a wrong or outdated slug.

**Data flow**: It receives the Composio client, provider, bad slug, and original error. It turns the bad slug into search words, asks Composio for matching tools, and if needed falls back to listing tools without a query. If useful tools are found, it returns a new ComposioError with the original message plus available slugs; otherwise it returns the original error.

**Call relations**: ComposioBroker.execute calls this only after Composio reports a not-found error for a tool run. It uses _discovered_tools to normalize the suggested tools before building the clearer error message.

*Call graph*: calls 2 internal fn (_discovered_tools, list_tools); called by 1 (execute); 2 external calls (sub, ComposioError).


##### `_collect_files`  (lines 165–174)

```
def _collect_files(value: object, found: list[BrokerFile]) -> None
```

**Purpose**: Walks through a nested tool response and picks out Composio file objects. It recognizes files by the presence of a file URL, MIME type, and name.

**Data flow**: It receives any value from a response and a list that is collecting found files. If the value looks like a Composio file object, it appends a BrokerFile with the name and URL. If the value is a dictionary or list, it searches inside each child value. It changes the supplied list in place and returns nothing.

**Call relations**: ComposioBroker.file_outputs starts the collection process with the full response. _collect_files does the recursive search, like checking every folder and subfolder for documents, and creates BrokerFile entries for anything that matches Composio's file shape.

*Call graph*: called by 1 (file_outputs); 1 external calls (__init__).


##### `_stale_account`  (lines 177–188)

```
def _stale_account(error: composio.ComposioError, account_id: str) -> bool
```

**Purpose**: Decides whether a Composio execution error means the connected account is no longer usable. This prevents the system from treating a dead account like a missing tool.

**Data flow**: It receives a Composio error and the account id that was used. It lowercases the error text and checks for narrow signs that Composio could not find the connected account: either Composio's own phrase "connected account" with "not found", or the exact account id with "not found". It returns true or false.

**Call relations**: ComposioBroker.execute calls this when execution fails. If it returns true, execute builds a reconnect-focused error instead of trying to suggest tool slugs, which would send the user in the wrong direction.

*Call graph*: called by 1 (execute).


##### `_reconnect_error`  (lines 191–192)

```
def _reconnect_error(error: composio.ComposioError, provider: str) -> composio.ComposioError
```

**Purpose**: Builds an error message that tells the user the connected account should be reconnected. It keeps Composio's original error status while adding practical next-step guidance.

**Data flow**: It receives the original Composio error and provider name. It combines the original error body with provider-specific stale-grant guidance, then returns a new ComposioError with the same status code.

**Call relations**: ComposioBroker.execute calls this after _stale_account identifies an unusable connected account. The new error is raised back to the caller so the agent can ask the member to reconnect rather than pretending the tool choice was wrong.

*Call graph*: called by 1 (execute); 2 external calls (stale_grant_guidance, ComposioError).


##### `_discovered_tools`  (lines 195–216)

```
def _discovered_tools(rows: tuple[dict[str, object], ...]) -> tuple[BrokerTool, ...]
```

**Purpose**: Converts raw Composio tool-list rows into UFO's BrokerTool objects. It keeps only usable tool names, shortens long descriptions, translates file inputs, and carries over the read-only hint.

**Data flow**: It receives a tuple of dictionaries from Composio. For each row, it chooses the slug from the row's slug or name, skips rows without a valid name, trims the description to a fixed length, rewrites input parameters into UFO's workspace-file schema, checks read-only tags, and returns all valid tools as a tuple.

**Call relations**: ComposioBroker.tools uses this for normal discovery results, and ComposioBroker._slug_miss uses it when preparing helpful suggestions after a bad slug. It calls _read_only for the safety hint and Composio's workspace_file_schema helper for file-input translation.

*Call graph*: calls 1 internal fn (_read_only); called by 2 (_slug_miss, tools); 2 external calls (__init__, workspace_file_schema).


##### `_read_only`  (lines 219–221)

```
def _read_only(payload: dict[str, object]) -> bool
```

**Purpose**: Checks whether Composio marked a tool as read-only. A read-only tool is expected to look things up rather than change outside data.

**Data flow**: It receives a tool payload dictionary. It reads the tags field and returns true only if that field is a list containing the tag "readOnlyHint". Otherwise it returns false.

**Call relations**: ComposioBroker.schema and _discovered_tools call this whenever they build a BrokerTool. Its result becomes the read_only flag that later planning and safety checks can use.

*Call graph*: called by 2 (schema, _discovered_tools).


### `extensions/pipedream/ufo_ext_pipedream/broker.py`

`domain_logic` · `tool discovery, action execution, credential use, and response processing`

Pipedream offers many ready-made actions, such as sending an email or creating a calendar event. This file wraps those actions so UFO can treat them like normal tools. Without it, UFO would not know how to list Pipedream actions, turn their settings into a usable input form, attach the right connected account, or interpret errors and file outputs.

The central piece is PipedreamBroker. Think of it like a travel adapter: UFO speaks one standard connector shape, while Pipedream has its own action catalog and run API. The broker translates between them. When asked for tools, it asks Pipedream for available actions and converts each action into a BrokerTool. When asked for a schema, it reads an action’s configurable properties and hides internal Pipedream fields, including the connected-account slot that the broker fills in itself.

When running an action, the broker first loads the action definition, inserts the chosen account into the action’s app slot, checks that the account belongs to the requested workspace and app, then asks Pipedream to run the action. It also turns certain failures into helpful messages, especially when an old saved connection no longer works and the user needs to reconnect.

The file also knows how to pull generated file links out of Pipedream responses. It deliberately refuses staged uploads because Pipedream actions expect file inputs as URLs instead.

#### Function details

##### `PipedreamBroker.tools`  (lines 62–64)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Finds Pipedream actions for a given provider and search query, then presents them as UFO broker tools. Someone uses this when the system or an agent needs to know what actions are available for an app.

**Data flow**: It receives a workspace ID, provider name, and search text. It looks up the Pipedream app slug for that provider, asks a fresh Pipedream client for matching actions, then converts the returned action rows into BrokerTool objects. The output is a tuple of tools ready for UFO to show or use.

**Call relations**: This is the discovery path. PipedreamBroker.search calls it when a broader search result is needed, and it relies on _spec to identify the right app and _listed_tools to translate Pipedream’s raw action list into UFO’s tool shape.

*Call graph*: calls 2 internal fn (_listed_tools, _spec); called by 1 (search); 1 external calls (pipedream_client).


##### `PipedreamBroker.schema`  (lines 66–73)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Builds the input description for one Pipedream action. This tells the agent which arguments it may provide when calling that action.

**Data flow**: It receives a provider context and an action slug. It fetches the action definition, extracts its configurable properties, removes fields that are internal to Pipedream, turns the remaining fields into a JSON schema, and returns a BrokerTool containing the slug, description, input schema, and read-only hint.

**Call relations**: This is used when UFO needs details for one specific action rather than a search result. It depends on _definition for the Pipedream action data, then uses _props, _input_schema, _read_only, and _str to clean and shape that data.

*Call graph*: calls 5 internal fn (_definition, _input_schema, _props, _read_only, _str); 1 external calls (__init__).


##### `PipedreamBroker.execute`  (lines 75–110)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Runs a Pipedream action with the selected connected account. It is the main path from “the agent chose this tool” to “Pipedream executed it and returned a result.”

**Data flow**: It receives the workspace, provider, action slug, user-supplied arguments, connected account ID, and an optional idempotency key. It loads the action definition, copies the arguments, fills in the hidden account slot with the account’s authorization ID, verifies that the account belongs to the workspace and matches the expected Pipedream app, and then calls Pipedream’s run API. It returns the response dictionary, or raises a clearer error if the action is unknown, the action reports an error, or the account appears stale.

**Call relations**: This is the broker’s execution core. It calls _definition to understand the action, _app_slot to find where the account should be inserted, _spec to confirm the app, _key_miss when the slug is unknown, and _stale_account plus _reconnect_error when Pipedream’s error suggests the user must reconnect.

*Call graph*: calls 7 internal fn (_definition, _key_miss, _app_slot, _reconnect_error, _spec, _stale_account, __init__); 2 external calls (dumps, pipedream_client).


##### `PipedreamBroker.file_outputs`  (lines 112–130)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Extracts files produced by a Pipedream action and turns them into downloadable broker files. This lets the sandbox fetch files that the action saved during its run.

**Data flow**: It receives a Pipedream response dictionary. It looks inside the response exports for File Stash upload records, ignores malformed entries, takes each valid download URL, derives a display name from the local file path when possible, and returns BrokerFile objects.

**Call relations**: This runs after an action response is available. It does not call back into Pipedream; it simply interprets the response format and packages file links for the rest of UFO.

*Call graph*: 2 external calls (__init__, PurePosixPath).


##### `PipedreamBroker.stage_upload`  (lines 132–144)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Rejects staged uploads for Pipedream actions. This matters because Pipedream expects file inputs to be normal URLs, not files uploaded through UFO’s staged-upload flow.

**Data flow**: It receives details about a file the system might want to upload. Instead of preparing an upload destination, it immediately raises an error explaining that the workspace file should be shared and passed as a download URL.

**Call relations**: This protects callers from using the wrong file-transfer method. It stands apart from the normal execution flow and points users toward share_file-style URL passing.


##### `PipedreamBroker.search`  (lines 146–147)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Returns a search result for Pipedream actions. Since Pipedream does not provide a separate routing or planning layer here, the search result is simply the matching tools.

**Data flow**: It receives workspace, provider, and query information. It calls PipedreamBroker.tools to get matching BrokerTool objects, then wraps them in a BrokerSearch result. The output contains the tools and no extra planning guidance.

**Call relations**: This is a thin wrapper around tools. It exists because UFO’s connector interface expects a search-shaped answer, even though Pipedream’s useful search data is just its action catalog.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `PipedreamBroker.credential`  (lines 149–170)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Creates a credential object that can send requests through Pipedream’s proxy for a connected account. It first verifies that the saved account is usable for this workspace and provider.

**Data flow**: It receives a workspace ID, provider name, and account ID. It looks up the provider’s expected app, asks Pipedream for the workspace account, turns a missing account into a reconnect-friendly error, checks that the account belongs to the correct app, and returns a Credential containing a PipedreamProxyTransport.

**Call relations**: This is used when code needs authenticated transport through a user’s connected Pipedream account. It uses _spec to know which app is expected, Pipedream’s client to verify the account, and PipedreamProxyTransport to build the actual request path.

*Call graph*: calls 3 internal fn (__init__, _spec, __init__); 5 external calls (__init__, __init__, AsyncHTTPTransport, stale_grant_guidance, pipedream_client).


##### `PipedreamBroker._definition`  (lines 172–180)

```
async def _definition(self, slug: str) -> dict[str, object]
```

**Purpose**: Fetches the full definition for a Pipedream action. This definition is the source of truth for its description, inputs, read-only hint, and account-binding slot.

**Data flow**: It receives an action slug. It asks a fresh Pipedream client for that action’s definition. If Pipedream says the action does not exist, it raises UnknownBrokerTool; otherwise it returns the definition dictionary, unwrapping a nested data field when needed.

**Call relations**: PipedreamBroker.schema uses this to describe an action, and PipedreamBroker.execute uses it before running one. It is the shared lookup step for both inspection and execution.

*Call graph*: called by 2 (execute, schema); 2 external calls (__init__, pipedream_client).


##### `PipedreamBroker._key_miss`  (lines 182–201)

```
async def _key_miss(self, client: pipedream.PipedreamClient, provider: str, slug: str) -> PipedreamError
```

**Purpose**: Builds a helpful error when an action slug is not found. Instead of only saying “not found,” it tries to suggest nearby real action keys.

**Data flow**: It receives a Pipedream client, provider, and missing slug. It looks up the provider’s app, tries to list all actions for that app, compares the missing slug to real slugs, and returns a PipedreamError that includes either close matches or advice to search the app’s actions.

**Call relations**: PipedreamBroker.execute calls this when _definition reports an unknown action. It uses _spec to identify the app and _listed_tools to turn the catalog into comparable tool slugs before using close-match logic.

*Call graph*: calls 4 internal fn (_listed_tools, _spec, list_actions, __init__); called by 1 (execute); 1 external calls (get_close_matches).


##### `_stale_account`  (lines 204–211)

```
def _stale_account(error: PipedreamError, account_id: str) -> bool
```

**Purpose**: Decides whether a Pipedream error likely means the saved connected account is no longer valid. This helps the system tell the user to reconnect instead of showing a confusing raw failure.

**Data flow**: It receives a Pipedream error and the account ID that was used. It lowercases the error body and checks for narrow signs such as “external user not found” or the specific account ID appearing with “not found.” It returns true only when the wording strongly points to a stale grant.

**Call relations**: PipedreamBroker.execute calls this after run failures and action-level errors. When it returns true, execute passes the error to _reconnect_error so the final message includes reconnect guidance.

*Call graph*: called by 1 (execute).


##### `_reconnect_error`  (lines 214–215)

```
def _reconnect_error(error: PipedreamError, provider: str) -> PipedreamError
```

**Purpose**: Adds user-friendly reconnect guidance to a Pipedream error. It preserves the original status while making the next step clearer.

**Data flow**: It receives a Pipedream error and provider name. It combines the original error body with standard stale-grant guidance for that provider, then returns a new PipedreamError with the same status code.

**Call relations**: PipedreamBroker.execute uses this after _stale_account identifies an account problem. It is the small formatting step that turns a technical account failure into an instruction the agent can act on.

*Call graph*: calls 1 internal fn (__init__); called by 1 (execute); 1 external calls (stale_grant_guidance).


##### `_spec`  (lines 218–222)

```
def _spec(provider: str) -> ConnectorSpec
```

**Purpose**: Looks up UFO’s registered Pipedream connector information for a provider name. This is how the broker finds the Pipedream app slug it should speak for.

**Data flow**: It receives a provider string. It searches Pipedream’s connector registry for that provider and returns the ConnectorSpec if found. If the provider is not registered, it raises a KeyError.

**Call relations**: Several broker paths call this before talking to Pipedream: tools, execute, credential, and _key_miss. It keeps provider-to-app lookup in one place so all those paths agree about which Pipedream app is expected.

*Call graph*: called by 4 (_key_miss, credential, execute, tools).


##### `_listed_tools`  (lines 225–242)

```
def _listed_tools(rows: tuple[dict[str, object], ...]) -> tuple[BrokerTool, ...]
```

**Purpose**: Converts Pipedream’s raw action-list rows into UFO BrokerTool objects. This makes action discovery usable by the rest of the connector system.

**Data flow**: It receives action rows from Pipedream. For each row with a valid action key, it extracts a description, builds an input schema from configurable properties, reads the read-only hint, and creates a BrokerTool. Invalid or keyless rows are skipped, and the result is returned as a tuple.

**Call relations**: PipedreamBroker.tools uses this for normal catalog discovery, and _key_miss uses it when preparing suggestions for an unknown action. It relies on _props, _input_schema, _read_only, and _str to clean up Pipedream’s fields.

*Call graph*: calls 4 internal fn (_input_schema, _props, _read_only, _str); called by 2 (_key_miss, tools); 1 external calls (__init__).


##### `_read_only`  (lines 245–247)

```
def _read_only(definition: dict[str, object]) -> bool
```

**Purpose**: Checks whether a Pipedream action is marked as read-only. A read-only action is expected to inspect or fetch information rather than change something.

**Data flow**: It receives an action definition dictionary. It looks for an annotations dictionary and returns true only when the readOnlyHint flag is exactly true. Otherwise it returns false.

**Call relations**: PipedreamBroker.schema and _listed_tools call this when creating BrokerTool objects. It contributes one safety-related hint to the tool description.

*Call graph*: called by 2 (schema, _listed_tools).


##### `_props`  (lines 250–252)

```
def _props(definition: dict[str, object]) -> list[dict[str, object]]
```

**Purpose**: Safely extracts the list of configurable properties from a Pipedream action definition. These properties are the action inputs that may become schema fields.

**Data flow**: It receives a definition dictionary. It reads configurable_props, keeps only entries that are dictionaries, and returns them as a list. If the field is missing or not a list, it returns an empty list.

**Call relations**: PipedreamBroker.schema and _listed_tools use this before building input schemas, and _app_slot uses it to find the hidden connected-account field. It is the shared cleanup step for Pipedream’s property data.

*Call graph*: called by 3 (schema, _app_slot, _listed_tools).


##### `_app_slot`  (lines 255–262)

```
def _app_slot(definition: dict[str, object], slug: str) -> str
```

**Purpose**: Finds the special Pipedream input field where the connected account must be placed. The agent should not fill this field; the broker fills it so the action runs under the right user account.

**Data flow**: It receives an action definition and slug. It scans the action’s configurable properties for the app-type property with a valid name and returns that name. If no such property exists, it raises a PipedreamError because the broker cannot safely run the action with a connected account.

**Call relations**: PipedreamBroker.execute calls this just before running an action. It uses _props to inspect the definition and provides the field name that execute uses to insert the account authorization.

*Call graph*: calls 2 internal fn (_props, __init__); called by 1 (execute).


##### `_input_schema`  (lines 265–288)

```
def _input_schema(props: list[dict[str, object]]) -> dict[str, object]
```

**Purpose**: Turns Pipedream action properties into a JSON schema, which is a plain description of the arguments an agent may send. It hides Pipedream-only fields so the agent only sees meaningful user-settable inputs.

**Data flow**: It receives a list of property dictionaries. It skips missing names, the hidden app account field, directory fields, and internal service fields that start with $. For the remaining fields, it maps Pipedream’s type names to JSON schema types, adds descriptions when available, records required fields, and returns an object-shaped schema.

**Call relations**: PipedreamBroker.schema uses this for one action’s detailed schema, and _listed_tools uses it while building search results. It calls _str to safely interpret property type values.

*Call graph*: calls 1 internal fn (_str); called by 2 (schema, _listed_tools).


##### `_str`  (lines 291–292)

```
def _str(value: object) -> str
```

**Purpose**: Safely converts a value into a string only when it already is one. This avoids accidentally turning non-string data into misleading text.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string. It does not change anything outside itself.

**Call relations**: PipedreamBroker.schema, _listed_tools, and _input_schema use this when reading optional Pipedream fields such as descriptions and type names. It is a small guardrail around data that may be missing or oddly shaped.

*Call graph*: called by 3 (schema, _input_schema, _listed_tools).


### Broker clients and credential proxies
Provider clients, proxies, and Composio MCP search support execute connector work while keeping raw credentials outside UFO.

### `extensions/composio/ufo_ext_composio/client.py`

`io_transport` · `connector OAuth, catalog browsing, tool discovery, and tool execution`

Composio acts like a trusted middle desk between this project and services such as GitHub or other app toolkits. Instead of this system keeping a separate integration for every provider, it asks Composio what tools exist, helps the user connect an account through Composio’s hosted OAuth flow, and later asks Composio to run a chosen tool. OAuth is the browser-based consent flow where a user grants access to an app.

This file wraps Composio’s HTTP API in a small async client. It reads the deploy-wide Composio API key from the environment, builds authenticated requests, checks that responses are shaped as expected, and raises clear errors when something is wrong. It also protects the system from unsafe or useless connectors: toolkits with no managed authentication, no tools, or known catalog gaps are refused before a user gets a broken grant.

A second part supports tool discovery. It can list catalog tools directly, or open a Composio Tool Router session and ask a semantic search tool for the best matching actions. When Composio marks an input as file-uploadable, this file rewrites that raw storage shape into a simple “workspace file path” input so the agent can talk in terms of files it can see. Without this file, connector grants, discovery, execution, and file staging would all lack a safe common doorway to Composio.

#### Function details

##### `connectable`  (lines 130–154)

```
def connectable(slug: str, toolkit: Mapping[str, object]) -> bool
```

**Purpose**: Decides whether this deployment should offer a Composio toolkit to users. It filters out toolkits that are banned by project judgement, have no Composio-managed login method, or have no usable tools.

**Data flow**: It receives a toolkit slug and a catalog record from Composio. It checks the slug against the local ban list, then reads the record for managed authentication schemes and a positive tool count. It returns true only when all checks pass.

**Call relations**: When the client checks one toolkit or builds a page of the toolkit catalog, those flows call this function as the gatekeeper. Its answer determines whether the toolkit is shown or claimed by this connector layer.

*Call graph*: called by 2 (connectable_toolkit, list_toolkits).


##### `ComposioError.__init__`  (lines 161–164)

```
def __init__(self, status: int, body: str) -> None
```

**Purpose**: Creates a clear exception for Composio failures. It preserves both the HTTP status number and the response body so callers can report or inspect what went wrong.

**Data flow**: It receives a status code and text body. It formats them into a readable error message and stores the original pieces on the exception object. The output is an exception ready to be raised.

**Call relations**: Higher-level client methods use this whenever Composio returns an error or an unusable response shape. It is the common loud failure path instead of silently treating bad remote data as empty or valid.

*Call graph*: called by 6 (_account, _auth_config, connect_link, create_upload, tool_router_session, _body).


##### `ComposioClient.connect_link`  (lines 181–190)

```
async def connect_link(self, toolkit: str, user_id: str, callback_url: str) -> str
```

**Purpose**: Creates the hosted Composio login link that a user opens to connect an external account. This starts the consent handoff for a specific toolkit and workspace user.

**Data flow**: It receives a toolkit slug, a broker user id, and a callback URL. It first finds or creates the right Composio auth configuration, then posts those details to Composio’s link endpoint. It returns the redirect URL, or raises an error if Composio does not provide one.

**Call relations**: This is used during connector setup when the system needs to send the member to Composio for login. It relies on `_auth_config` for the login configuration and `_post` for the actual HTTP call.

*Call graph*: calls 3 internal fn (_auth_config, _post, __init__).


##### `ComposioClient.connected_account`  (lines 192–196)

```
async def connected_account(self, account_id: str, expected_user_id: str, expected_toolkit: str) -> OAuthAccount
```

**Purpose**: Turns a Composio connected-account id into the project’s simple OAuth account record, but only after verifying it is safe to use. It confirms ownership, active status, and the expected toolkit.

**Data flow**: It receives an account id, the expected user id, and the expected toolkit slug. It asks `_account` to validate the remote account details. If validation succeeds, it returns an OAuthAccount containing the connected-account id.

**Call relations**: This sits after the OAuth callback or grant exchange step. It delegates the detailed safety checks to `_account`, then hands the rest of the connector system the small account object it expects.

*Call graph*: calls 1 internal fn (_account); 1 external calls (__init__).


##### `ComposioClient._account`  (lines 198–227)

```
async def _account(self, account_id: str, expected_user_id: str, expected_toolkit: str) -> dict[str, object]
```

**Purpose**: Fetches and validates a connected account from Composio. It prevents one workspace from using another user’s account or an account connected for the wrong toolkit.

**Data flow**: It receives the account id plus the expected owner and toolkit. It downloads the account record, checks the owner, checks that the status is ACTIVE, and checks the toolkit slug. It returns the raw account payload when all checks pass, raises a ComposioError for mismatches, or raises GrantUnusable when the account exists but needs reconnecting.

**Call relations**: The public `connected_account` method calls this before creating an OAuthAccount. It is the security checkpoint between a raw Composio account id and a grant this project will trust.

*Call graph*: calls 3 internal fn (__init__, _get, __init__); called by 1 (connected_account).


##### `ComposioClient.account_label`  (lines 229–232)

```
async def account_label(self, account_id: str) -> str | None
```

**Purpose**: Looks up a human-friendly label for a connected account, if Composio has one. This can make account displays clearer to users.

**Data flow**: It receives a connected-account id and fetches that account record. If the record has a non-empty string alias, it returns that alias. Otherwise it returns null.

**Call relations**: Other connector-facing code can call this when it wants a display name. It uses the shared `_get` helper so response checking and authentication stay consistent.

*Call graph*: calls 1 internal fn (_get).


##### `ComposioClient.list_tools`  (lines 234–264)

```
async def list_tools(self, toolkit: str, query: str='') -> tuple[dict[str, object], ...]
```

**Purpose**: Lists tools in one Composio toolkit, optionally matching a query. It follows Composio’s pagination so tools beyond the first page are not missed.

**Data flow**: It receives a toolkit slug and optional query text. It repeatedly asks Composio for pages of tools, collects dictionary-shaped rows, stops at the final page or the project’s maximum listing size, and returns the collected rows as a tuple.

**Call relations**: The Composio broker uses this when a requested tool slug is not already known and it needs to search the toolkit catalog. Internally, each page is fetched through `_get`.

*Call graph*: calls 1 internal fn (_get); called by 1 (_slug_miss).


##### `ComposioClient.tool_schema`  (lines 266–267)

```
async def tool_schema(self, slug: str) -> dict[str, object]
```

**Purpose**: Fetches the detailed schema for one Composio tool. A schema describes what inputs the tool accepts and what the tool is for.

**Data flow**: It receives a tool slug. It sends a GET request to Composio’s tool detail endpoint and returns the response object. It does not reshape the schema here.

**Call relations**: This is a direct catalog lookup used by higher-level code that needs to describe or validate a particular tool. It relies on `_get` for the HTTP request and response validation.

*Call graph*: calls 1 internal fn (_get).


##### `ComposioClient.connectable_toolkit`  (lines 269–286)

```
async def connectable_toolkit(self, slug: str) -> str | None
```

**Purpose**: Checks whether a single toolkit slug is valid, safe to put in a URL, present in Composio, and connectable by this deployment. If it is, it returns the toolkit’s display name.

**Data flow**: It receives a slug from outside the system. It first rejects characters outside the allowed toolkit identifier pattern, then fetches the toolkit from Composio. A missing toolkit becomes null; other Composio errors still fail loudly. If `connectable` approves the payload, it returns the toolkit name or falls back to the slug.

**Call relations**: Resolver code uses this when deciding whether this extension can claim a provider slug. It calls `_get` to read Composio’s catalog and `connectable` to apply this project’s eligibility rules.

*Call graph*: calls 2 internal fn (_get, connectable).


##### `ComposioClient.list_toolkits`  (lines 288–315)

```
async def list_toolkits(self, query: str, limit: int, after: str | None) -> CatalogPage
```

**Purpose**: Returns one page of Composio toolkits that this deployment is willing to offer. It is used to build a browseable connector catalog.

**Data flow**: It receives search text, a page size, and an optional cursor for the next page. It fetches a toolkit page from Composio, skips malformed or non-connectable items, converts the rest into catalog entries, and returns those entries with a continuation cursor if there is one.

**Call relations**: Catalog browsing calls this to show available providers. It uses `_get` for the remote page, `connectable` for filtering, and then creates the project’s CatalogEntry and CatalogPage objects.

*Call graph*: calls 2 internal fn (_get, connectable); 2 external calls (__init__, __init__).


##### `ComposioClient.execute_tool`  (lines 317–331)

```
async def execute_tool(self, slug: str, arguments: Mapping[str, object], user_id: str, connected_account_id: str | None=None, idempotency_key: str | None=None) -> dict[str, object]
```

**Purpose**: Asks Composio to run a specific tool on its server side. This keeps the external account token inside Composio instead of exposing it to this project.

**Data flow**: It receives a tool slug, arguments, a broker user id, and optionally a connected-account id and idempotency key. It builds the execute request, rejects it if the JSON body is larger than the configured safety limit, adds the idempotency header when provided, and returns Composio’s response object.

**Call relations**: Higher-level dynamic connector tools call this when the agent has chosen an action to run. It hands the final request to `_post`, which performs the authenticated HTTP call.

*Call graph*: calls 1 internal fn (_post); 1 external calls (dumps).


##### `ComposioClient.create_upload`  (lines 333–357)

```
async def create_upload(self, toolkit: str, slug: str, filename: str, mimetype: str, md5: str) -> 'ComposioUpload'
```

**Purpose**: Asks Composio for a place to stage a file that a tool will later use. This lets the sandbox upload bytes directly to Composio’s storage slot while the tool argument only names the resulting key.

**Data flow**: It receives the toolkit, tool slug, filename, MIME type, and MD5 checksum. It posts those facts to Composio’s upload-request endpoint, checks for a returned storage key, and returns a ComposioUpload containing the key and possibly a presigned PUT URL. If Composio says the file already exists, the URL can be null.

**Call relations**: Tool execution flows use this before running tools that need file inputs. It uses `_post` for the request and raises ComposioError if Composio’s upload response is missing essential pieces.

*Call graph*: calls 2 internal fn (_post, __init__); 1 external calls (__init__).


##### `ComposioClient.tool_router_session`  (lines 359–370)

```
async def tool_router_session(self, user_id: str, toolkits: list[str]) -> ToolRouterSession
```

**Purpose**: Opens a Composio Tool Router session for semantic tool search. The returned session gives an MCP endpoint, which is a tool-calling endpoint used to ask Composio’s router for matching tools.

**Data flow**: It receives a broker user id and a list of toolkit slugs to enable. It posts that scope to Composio, reads the session id and MCP URL from the response, and returns them as a ToolRouterSession. If either piece is missing, it raises an error.

**Call relations**: The `search_connector_tools` function calls this when there is no cached search session for a user and connector. It is the setup step before sending the actual search query through MCP.

*Call graph*: calls 2 internal fn (_post, __init__); called by 1 (search_connector_tools); 1 external calls (__init__).


##### `ComposioClient._auth_config`  (lines 372–390)

```
async def _auth_config(self, toolkit: str) -> str
```

**Purpose**: Finds the Composio authentication configuration to use for a toolkit, creating a Composio-managed one if none exists. This is what the user’s login link rides on.

**Data flow**: It receives a toolkit slug. It first asks Composio for an existing auth config and extracts its id if present. If none is found, it posts a request to create a managed auth config and returns the new id, raising an error if no id comes back.

**Call relations**: `connect_link` calls this before creating a login URL. It uses `_get`, `_post`, and `_auth_config_id` so the public flow does not need to know the details of Composio auth config records.

*Call graph*: calls 4 internal fn (_get, _post, __init__, _auth_config_id); called by 1 (connect_link).


##### `ComposioClient._get`  (lines 392–394)

```
async def _get(self, path: str, params: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Performs an authenticated GET request to Composio and returns a checked response body. It is the shared read path for the client.

**Data flow**: It receives a path and optional query parameters. It opens a short-lived HTTP client, sends the GET request, passes the response to `_body`, and returns the parsed dictionary that `_body` approves.

**Call relations**: Most read-style methods call this: account lookup, auth config lookup, toolkit lookup, toolkit listing, tool listing, and schema lookup. It delegates client construction to `_http` and response interpretation to `_body`.

*Call graph*: calls 2 internal fn (_http, _body); called by 7 (_account, _auth_config, account_label, connectable_toolkit, list_toolkits, list_tools, tool_schema).


##### `ComposioClient._post`  (lines 396–400)

```
async def _post(self, path: str, body: dict[str, object], headers: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Performs an authenticated POST request to Composio and returns a checked response body. It is the shared write-or-action path for the client.

**Data flow**: It receives a path, a JSON body, and optional headers. It opens a short-lived HTTP client, sends the POST request, passes the response to `_body`, and returns the parsed dictionary that `_body` approves.

**Call relations**: Methods that create links, create auth configs, run tools, request upload slots, or open Tool Router sessions all use this. Like `_get`, it centralizes HTTP setup and response checking.

*Call graph*: calls 2 internal fn (_http, _body); called by 5 (_auth_config, connect_link, create_upload, execute_tool, tool_router_session).


##### `ComposioClient._http`  (lines 402–408)

```
def _http(self) -> httpx.AsyncClient
```

**Purpose**: Builds the underlying asynchronous HTTP client for Composio requests. It attaches the base URL, API key header, timeout, and optional test transport.

**Data flow**: It reads the ComposioClient’s API key and optional transport. It creates and returns an httpx AsyncClient configured for Composio’s API. The caller then uses that client inside a short-lived context.

**Call relations**: `_get` and `_post` call this every time they make a request. Keeping it here means all Composio calls use the same authentication and timeout rules.

*Call graph*: called by 2 (_get, _post); 1 external calls (AsyncClient).


##### `_body`  (lines 411–419)

```
def _body(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Turns a raw HTTP response from Composio into a usable dictionary, or raises a clear error. It protects the rest of the code from bad status codes and unexpected response shapes.

**Data flow**: It receives an HTTP response. If the status code signals failure, it raises ComposioError with the response text. If the response is empty, it returns an empty dictionary. Otherwise it parses JSON and returns it only if it is an object.

**Call relations**: Both `_get` and `_post` call this after every Composio request. It is the common checkpoint that keeps remote API surprises from leaking into business logic.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_get, _post); 1 external calls (json).


##### `workspace_file_schema`  (lines 422–448)

```
def workspace_file_schema(value: object) -> object
```

**Purpose**: Rewrites Composio file-upload input schemas into the simpler form this agent can understand: a workspace file path. This hides Composio’s internal storage fields from the model.

**Data flow**: It receives any schema value. If it finds a dictionary marked as file-uploadable, it replaces that part with an object requiring the workspace file key. For nested dictionaries or lists, it walks through each child and rewrites them too. Non-container values pass through unchanged.

**Call relations**: `_search_result` uses this while turning Tool Router search results into broker tools. That means tools shown to the agent ask for files in the project’s own file vocabulary, not Composio’s raw upload vocabulary.

*Call graph*: called by 1 (_search_result).


##### `_auth_config_id`  (lines 451–458)

```
def _auth_config_id(payload: dict[str, object]) -> str | None
```

**Purpose**: Extracts the first auth configuration id from a Composio list response. It is a small helper for the auth setup flow.

**Data flow**: It receives a parsed response dictionary. It looks for an `items` list and scans for the first dictionary with a string `id`. It returns that id or null if none is found.

**Call relations**: `_auth_config` calls this after asking Composio for existing auth configurations. The helper keeps the list-scanning detail out of the main connect-link setup.

*Call graph*: called by 1 (_auth_config).


##### `composio_client`  (lines 461–468)

```
def composio_client() -> ComposioClient
```

**Purpose**: Creates the deployment’s default Composio client from the environment. It fails immediately if the required API key is missing.

**Data flow**: It reads the COMPOSIO_API_KEY environment variable. If the variable is empty or absent, it raises a runtime error. Otherwise it returns a ComposioClient configured with that key.

**Call relations**: Code that needs the standard live Composio client can call this instead of reading environment variables itself. It provides one loud, consistent failure point for missing deployment configuration.

*Call graph*: 1 external calls (__init__).


##### `_dict`  (lines 475–476)

```
def _dict(value: object) -> dict[str, object]
```

**Purpose**: Safely treats a value as a dictionary only when it really is one. This keeps search-result parsing tolerant of missing or malformed fields.

**Data flow**: It receives any value. If the value is a dictionary, it returns it unchanged. Otherwise it returns an empty dictionary.

**Call relations**: `_search_result` calls this repeatedly while walking nested Tool Router output. It lets that parser keep going without type errors when optional fields are absent.

*Call graph*: called by 1 (_search_result).


##### `_str_tuple`  (lines 479–482)

```
def _str_tuple(value: object) -> tuple[str, ...]
```

**Purpose**: Safely extracts non-empty strings from a list and returns them as an immutable tuple. It filters out missing, empty, or non-string values.

**Data flow**: It receives any value. If the value is not a list, it returns an empty tuple. If it is a list, it keeps only non-empty strings and returns them as a tuple.

**Call relations**: `_search_result` uses this for tool slugs, plan steps, guidance, and pitfalls from Tool Router output. It turns loose remote data into clean text lists for the broker response.

*Call graph*: called by 1 (_search_result).


##### `_search_result`  (lines 485–519)

```
def _search_result(result: dict[str, object]) -> BrokerSearch
```

**Purpose**: Converts Composio Tool Router output into the project’s BrokerSearch format. The result includes matching tool slugs, descriptions, cleaned input schemas, suggested plan steps, guidance, and known pitfalls.

**Data flow**: It receives the raw Tool Router result dictionary. It finds the nested data and tool schemas, walks each result item, gathers primary and related tool slugs without duplicates, rewrites file-upload schemas into workspace-file schemas, and collects advice text. It returns a BrokerSearch object containing all of that in project-friendly form.

**Call relations**: `search_connector_tools` calls this after the MCP search tool answers. It uses `_dict`, `_str_tuple`, and `workspace_file_schema` to make the remote result safe and useful for the dynamic connector tools.

*Call graph*: calls 3 internal fn (_dict, _str_tuple, workspace_file_schema); called by 1 (search_connector_tools); 2 external calls (__init__, __init__).


##### `search_connector_tools`  (lines 522–545)

```
async def search_connector_tools(client: ComposioClient, workspace_id: UUID, connector: str, query: str) -> BrokerSearch
```

**Purpose**: Runs semantic search for tools inside a connected Composio toolkit. Instead of only matching exact names, it asks Composio’s Tool Router which tools fit the user’s use case.

**Data flow**: It receives a Composio client, workspace id, connector slug, and query. It builds the broker user id, reuses or creates a cached Tool Router session for that user and connector, calls the router’s search tool over MCP with the query, and converts the answer into BrokerSearch.

**Call relations**: Dynamic tool discovery calls this when it needs smart recommendations for a connector. It may call `tool_router_session` to open the session, then hands the search request to `mcp_session.mcp_call_tool`, and finally passes the raw answer to `_search_result`.

*Call graph*: calls 2 internal fn (tool_router_session, _search_result); 1 external calls (mcp_call_tool).


### `extensions/composio/ufo_ext_composio/proxy.py`

`io_transport` · `request handling`

Composio keeps provider credentials on its own servers, so this project cannot simply attach a token and call Google, Slack, or another provider directly. This file solves that by acting like a mail forwarder: callers write a normal HTTP request addressed to the provider, but the transport repackages it and sends it to Composio’s proxy endpoint. Composio adds the secret credential server-side, calls the provider, and returns the provider’s status, headers, and body.

The main class, ComposioProxyTransport, is an httpx transport. A transport is the low-level part of an HTTP client that actually sends the request. Here, it reads the original request, copies safe headers, includes the connected Composio account id, and posts that package to Composio. It deliberately skips headers such as Authorization and Host, because those either contain secrets or belong to the original connection rather than the proxy call.

When Composio replies, the transport unwraps Composio’s response format and rebuilds a normal HTTP response for the caller. JSON bodies are returned directly. Large or non-JSON binary data may be stored by Composio elsewhere; in that case this file returns a redirect pointing to the stored bytes instead of pulling them through the shared proxy process.

ComposioRequestForwarder uses the same machinery for one-off forwarded CLI requests. It also adds size and time limits so a slow or huge provider response cannot tie up the proxy indefinitely.

#### Function details

##### `ComposioProxyTransport.handle_async_request`  (lines 66–102)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: This is the main request-rewriting step. It takes a normal provider HTTP request, packages it as a Composio proxy-execute call, sends it through the inner transport, and returns a response that looks like it came from the provider.

**Data flow**: It starts with an httpx request containing a method, URL, headers, optional body, and optional timeout. It reads the body, builds a JSON payload with the connected account id, provider endpoint, method, allowed headers, and body, then sends that payload to Composio’s proxy endpoint using the API key. It reads Composio’s reply with a size-aware helper. If Composio itself reports an error, it returns that error as-is. Otherwise it decodes the proxy payload and turns it into a provider-style response.

**Call relations**: This is called by httpx when this transport is used to send a request, and it is also called directly by ComposioRequestForwarder.forward for broker forwarding. During the flow it asks _read_bounded to safely collect the Composio response, then hands the decoded successful payload to _provider_response so the rest of the system can keep working with ordinary HTTP responses.

*Call graph*: calls 2 internal fn (_provider_response, _read_bounded); 4 external calls (Request, aread, Response, loads).


##### `ComposioProxyTransport._read_bounded`  (lines 104–119)

```
async def _read_bounded(self, response: httpx.Response) -> bytes
```

**Purpose**: This reads the full response body from Composio, with an optional maximum size. The size limit protects shared proxy processes from buffering a response that is too large.

**Data flow**: It receives an httpx response from the Composio proxy call. If no size limit is configured, it simply reads and returns all bytes. If a limit is set, it reads the response chunk by chunk, counting the accumulated bytes. When the body grows past the allowed size, it closes the response and raises a ComposioError instead of continuing to store more data in memory.

**Call relations**: ComposioProxyTransport.handle_async_request calls this right after the proxy-execute request comes back. Its output is either the raw bytes that handle_async_request will inspect next, or a clear failure that stops the forwarding path before memory use can grow without bound.

*Call graph*: called by 1 (handle_async_request); 4 external calls (aclose, aiter_bytes, aread, ComposioError).


##### `ComposioProxyTransport._provider_response`  (lines 121–165)

```
def _provider_response(self, payload: dict[str, Any], request: httpx.Request) -> httpx.Response
```

**Purpose**: This converts Composio’s proxy response shape back into a normal HTTP response from the original provider. It hides Composio’s wrapping so caller code can keep using status codes, headers, and bodies in the usual way.

**Data flow**: It receives a decoded JSON payload from Composio and the original request. It unwraps nested data envelopes until it reaches the provider-level status, headers, body, and possible binary-data reference. It filters out body-specific headers that would be wrong after rebuilding the response. If Composio reports binary data with a stored file URL, it returns a redirect response pointing to that URL. Otherwise it serializes JSON data, encodes string data, or returns an empty body when there is no data, then builds an httpx response.

**Call relations**: ComposioProxyTransport.handle_async_request calls this after a successful proxy-execute response has been read and decoded. It is the final translation step before the caller receives what appears to be a provider response. If Composio gives malformed binary metadata, it raises ComposioError so the problem is visible instead of returning a misleading response.

*Call graph*: called by 1 (handle_async_request); 4 external calls (Response, dumps, cast, ComposioError).


##### `ComposioProxyTransport.aclose`  (lines 167–168)

```
async def aclose(self) -> None
```

**Purpose**: This closes the underlying transport used to contact Composio. It is the cleanup hook that releases network resources when the proxy transport is no longer needed.

**Data flow**: It takes no new data from the caller. It forwards the close request to the inner transport, which can then close open network connections or other resources. Nothing is returned.

**Call relations**: ComposioRequestForwarder.forward calls this in its cleanup step after a forwarded request finishes or fails. This keeps each forwarding exchange from leaving its underlying HTTP transport open.


##### `ComposioRequestForwarder.forward`  (lines 185–213)

```
async def forward(self, account_id: str, method: str, url: str, headers: Mapping[str, str], body: bytes) -> ForwardedResponse
```

**Purpose**: This forwards one provider request through Composio for a CLI or broker-style request path. It uses the same proxy transport as feed syncing, but wraps the whole exchange in size and time limits.

**Data flow**: It receives a Composio account id, HTTP method, URL, headers, and raw request body. It gets the configured Composio client and API key, builds a ComposioProxyTransport for that account, creates an httpx request with a timeout, and sends it through the transport. It reads the resulting response body, closes the transport, and returns a ForwardedResponse containing the status, headers, and body. If the whole operation takes too long, it raises a ComposioError with a gateway-timeout-style message.

**Call relations**: This is used by the broker forwarding path when a granted Composio credential needs to execute one outbound provider request. It constructs ComposioProxyTransport, calls its handle_async_request method to do the actual rewrite-and-send work, and then packages the result as ForwardedResponse for the caller.

*Call graph*: 8 external calls (__init__, __init__, timeout, AsyncHTTPTransport, Request, Timeout, ComposioError, composio_client).


### `extensions/pipedream/ufo_ext_pipedream/client.py`

`io_transport` · `connector OAuth, account lookup, and action execution`

This file is the bridge between UFO and Pipedream Connect. Pipedream hosts the OAuth consent flow, stores the real provider credentials, and runs provider actions on its own servers. That means UFO only keeps a connected-account id, not the user’s private access token. Without this file, the Pipedream-backed connectors could not create consent links, find the account a user just connected, inspect available actions, or run those actions safely.

The file starts by listing supported providers in CONNECTORS. Each entry says the friendly name, the Pipedream app slug, and the provider API host that a grant can represent. Some providers are here because another broker cannot handle their consent or does not offer the needed managed credentials.

PipedreamClient is the main working part. It gets a Pipedream access token using client credentials, caches it briefly, and then uses it to call Pipedream’s REST API. It can mint a Connect Link for browser consent, read connected accounts, list and describe actions, and run an action with the chosen connected account bound server-side.

A major theme is ownership safety. A Pipedream project token can read many accounts in the project, so this code checks the account’s external user id before accepting it. Think of it like checking the name on a coat-check ticket before handing over the coat. If the account is unhealthy or belongs to the wrong user or workspace, the code refuses to proceed.

#### Function details

##### `PipedreamError.__init__`  (lines 105–108)

```
def __init__(self, status: int, body: str) -> None
```

**Purpose**: This creates a clear error when Pipedream returns a failure or a response the client cannot safely use. It keeps the HTTP status code and response body so callers can understand what went wrong.

**Data flow**: It receives a numeric status and a text body from a failed or unusable Pipedream response. It turns them into a readable exception message and stores both pieces on the error object. The result is an exception that can be raised and caught by higher-level connector code.

**Call relations**: Many parts of the Pipedream extension raise this error when they must fail loudly instead of pretending nothing happened. In this file, it is used after bad token responses, missing Connect Link data, wrong account ownership, missing account ids, malformed response bodies, and failed HTTP calls.

*Call graph*: called by 12 (_key_miss, credential, execute, _app_slot, _reconnect_error, access_token, connect_token, newest_account, workspace_account, _account (+2 more)).


##### `PipedreamClient.access_token`  (lines 146–167)

```
async def access_token(self) -> str
```

**Purpose**: This gets the access token that lets this deployment call Pipedream’s API. It reuses a still-valid cached token when possible, so normal calls do not request a new token every time.

**Data flow**: It starts with the client id and looks in the process-wide token cache. If the cached token is still safely before expiry, it returns that token. Otherwise it opens an HTTP client, posts the client id and client secret to Pipedream’s OAuth token endpoint, checks the response body, stores the new token with its expiry time, and returns the token string.

**Call relations**: The private request helpers _get and _post call this before talking to authenticated Pipedream endpoints. It uses _http to create the temporary HTTP client and _body to turn the HTTP response into a checked dictionary.

*Call graph*: calls 3 internal fn (_http, __init__, _body); called by 2 (_get, _post); 1 external calls (monotonic).


##### `PipedreamClient.connect_token`  (lines 169–184)

```
async def connect_token(self, external_user_id: str, success_redirect_uri: str, error_redirect_uri: str) -> ConnectToken
```

**Purpose**: This creates a short-lived Pipedream Connect token and browser link for starting a user consent flow. A caller uses the returned link to send the user to Pipedream’s hosted connection page.

**Data flow**: It receives an external user id plus success and error redirect URLs. It posts those values to Pipedream, then checks that the answer contains both a token and a connect_link_url. It returns a ConnectToken object containing those two strings, or raises an error if either is missing.

**Call relations**: This is used during connector connection setup, when the system needs to create a consent link. It delegates the actual authenticated POST request to _post, which handles access-token retrieval and response checking.

*Call graph*: calls 2 internal fn (_post, __init__); 1 external calls (__init__).


##### `PipedreamClient.connected_account`  (lines 186–193)

```
async def connected_account(self, account_id: str, external_user_id: str) -> ConnectedAccount
```

**Purpose**: This reads one connected account and confirms that it belongs to the expected external user. It prevents code from accidentally using an account owned by someone else.

**Data flow**: It receives a Pipedream account id and the external user id that should own it. It fetches the account record from Pipedream, unwraps the data field if present, and passes the record through ownership and health checks. It returns a ConnectedAccount when the account is valid and owned by the expected user.

**Call relations**: Higher-level broker code can call this before binding a grant or using an account. Internally it uses _get to fetch the account, _dict to safely treat nested values as dictionaries, and _owned_account to enforce ownership.

*Call graph*: calls 3 internal fn (_get, _dict, _owned_account).


##### `PipedreamClient.account_label`  (lines 195–199)

```
async def account_label(self, account_id: str) -> str | None
```

**Purpose**: This looks up the human-readable name of a connected account, if Pipedream provides one. It is useful for showing users which account they connected.

**Data flow**: It receives an account id, fetches the account record from Pipedream, unwraps the data field if present, and reads the name field. It returns the non-empty name string, or null if there is no usable name.

**Call relations**: This is a small lookup helper built on _get and _dict. Unlike the account ownership methods, it only extracts a label and does not return a full ConnectedAccount.

*Call graph*: calls 2 internal fn (_get, _dict).


##### `PipedreamClient.workspace_account`  (lines 201–211)

```
async def workspace_account(self, account_id: str, workspace_id: UUID) -> ConnectedAccount
```

**Purpose**: This reads a connected account and confirms that it belongs to the given workspace. It is a safety gate before a workspace is allowed to execute through a Pipedream account.

**Data flow**: It receives an account id and a workspace UUID. It fetches the account record, turns it into a ConnectedAccount, then checks whether the account’s external user id matches the workspace’s allowed naming pattern. It returns the account if the workspace owns it, or raises a 403-style PipedreamError if not.

**Call relations**: This is used when execution must be limited to accounts connected under the current workspace. It uses _get and _dict to read the account, _account to validate account shape and health, and _workspace_owns_external_user to enforce the workspace boundary.

*Call graph*: calls 5 internal fn (_get, __init__, _account, _dict, _workspace_owns_external_user).


##### `PipedreamClient.newest_account`  (lines 213–227)

```
async def newest_account(self, external_user_id: str, app: str) -> ConnectedAccount
```

**Purpose**: This finds the most recently connected account for one external user and one Pipedream app. It is used after a consent flow finishes, when the system needs to identify the account that was just created or reconnected.

**Data flow**: It receives an external user id and an app slug. It asks Pipedream for matching accounts, filters the response to dictionary records, chooses the record with the latest created_at value, checks that it has an id, and confirms ownership. It returns the newest valid ConnectedAccount, or raises an error if none can be found or the record is incomplete.

**Call relations**: This fits the OAuth return flow: after the browser consent step, higher-level code can call this to correlate the Pipedream account back to the state-scoped external user. It uses _get for the listing, _dict for safe record handling, and _owned_account for the final safety check.

*Call graph*: calls 4 internal fn (_get, __init__, _dict, _owned_account).


##### `PipedreamClient.list_actions`  (lines 229–258)

```
async def list_actions(self, app: str, query: str='') -> tuple[dict[str, object], ...]
```

**Purpose**: This lists the Pipedream actions available for an app, optionally filtered by a search query. It follows Pipedream’s pages so discovery can see actions beyond the first page.

**Data flow**: It receives an app slug and optional query text. It repeatedly asks Pipedream for action pages, collects dictionary-shaped action rows, follows the end cursor when there may be another page, and stops when the page is short, the cursor is missing, or the configured maximum number of rows is reached. It returns a tuple of action dictionaries, capped at the maximum.

**Call relations**: The broker calls this when it needs to search Pipedream’s action catalog, especially when a requested tool key is missing and it wants to suggest or discover matching actions. It relies on _get for each page and _dict for reading pagination information safely.

*Call graph*: calls 2 internal fn (_get, _dict); called by 1 (_key_miss).


##### `PipedreamClient.action_definition`  (lines 260–261)

```
async def action_definition(self, key: str) -> dict[str, object]
```

**Purpose**: This fetches the detailed definition for one Pipedream component or action key. A caller uses it to understand what inputs the action expects and how it should be described.

**Data flow**: It receives an action/component key. It makes an authenticated GET request to Pipedream’s component endpoint for that key and returns the response dictionary. It does not reshape the data beyond the shared response checks done by _get.

**Call relations**: This is a direct catalog lookup helper. It hands the work to _get, which takes care of getting an access token, making the HTTP request, and validating the response body.

*Call graph*: calls 1 internal fn (_get).


##### `PipedreamClient.run_action`  (lines 263–281)

```
async def run_action(self, key: str, external_user_id: str, configured_props: dict[str, object]) -> dict[str, object]
```

**Purpose**: This runs one Pipedream action on Pipedream’s servers using configured properties, including the connected account binding. It also asks Pipedream to create a fresh file stash so files produced by the action can come back as downloadable links instead of unreadable temporary paths.

**Data flow**: It receives an action key, an external user id, and configured action properties. It builds the run request body, adds stash_id set to NEW, checks that the JSON payload is not larger than the allowed one-megabyte bound, and posts it to Pipedream. It returns Pipedream’s run result dictionary, or raises ValueError before sending if the arguments are too large.

**Call relations**: Higher-level connector execution code uses this when it is time to actually perform a provider action. It delegates the authenticated HTTP POST to _post after doing the local payload-size safety check.

*Call graph*: calls 1 internal fn (_post); 1 external calls (dumps).


##### `PipedreamClient._get`  (lines 283–286)

```
async def _get(self, path: str, params: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: This is the shared helper for authenticated GET requests to Pipedream. It keeps the public methods from repeating token and response-checking code.

**Data flow**: It receives a path and optional query parameters. It first gets an access token, opens an HTTP client configured with that token, performs the GET request, and passes the response through _body. It returns a checked dictionary response.

**Call relations**: Account lookup, account labels, newest-account discovery, action listing, and action-definition lookup all call this. It in turn calls access_token, _http, and _body to perform the common request flow.

*Call graph*: calls 3 internal fn (_http, access_token, _body); called by 6 (account_label, action_definition, connected_account, list_actions, newest_account, workspace_account).


##### `PipedreamClient._post`  (lines 288–291)

```
async def _post(self, path: str, body: dict[str, object]) -> dict[str, object]
```

**Purpose**: This is the shared helper for authenticated POST requests to Pipedream. It centralizes how JSON bodies are sent and checked.

**Data flow**: It receives a path and a dictionary body. It gets an access token, opens an authenticated HTTP client, posts the body as JSON, and turns the HTTP response into a checked dictionary using _body. It returns that dictionary to the caller.

**Call relations**: connect_token uses this to create Connect Links, and run_action uses it to execute actions. Like _get, it relies on access_token, _http, and _body for the repeated request steps.

*Call graph*: calls 3 internal fn (_http, access_token, _body); called by 2 (connect_token, run_action).


##### `PipedreamClient._http`  (lines 293–306)

```
def _http(self, token: str | None=None) -> httpx.AsyncClient
```

**Purpose**: This creates a temporary asynchronous HTTP client for talking to Pipedream. For authenticated calls, it adds the bearer token and Pipedream environment header; for the token request, it leaves headers empty.

**Data flow**: It receives either an access token string or null. If a token is present, it builds headers with authorization and environment values; otherwise it uses no special headers. It returns an httpx AsyncClient configured with Pipedream’s base URL, timeout, optional test transport, and those headers.

**Call relations**: access_token calls this without a token for the OAuth token endpoint. _get and _post call it with a token for normal Connect API calls.

*Call graph*: called by 3 (_get, _post, access_token); 1 external calls (AsyncClient).


##### `_dict`  (lines 309–310)

```
def _dict(value: object) -> dict[str, object]
```

**Purpose**: This tiny helper turns “maybe a dictionary” data into a safe dictionary. It avoids crashes when Pipedream returns a missing, null, or unexpected nested value.

**Data flow**: It receives any value. If the value is already a dictionary, it returns it unchanged; otherwise it returns an empty dictionary. It does not modify anything.

**Call relations**: Several methods use this when reading optional nested Pipedream response fields such as data, app, or page_info. It is a guardrail around response parsing.

*Call graph*: called by 6 (account_label, connected_account, list_actions, newest_account, workspace_account, _account).


##### `_owned_account`  (lines 313–326)

```
def _owned_account(record: dict[str, object], account_id: str, external_user_id: str) -> ConnectedAccount
```

**Purpose**: This checks that a connected account record belongs to one exact external user. It is one of the main protections against using the wrong person’s connected account.

**Data flow**: It receives a raw account record, the expected account id, and the expected external user id. It first turns the record into a validated ConnectedAccount with _account, then compares the account’s external_user_id with the expected one. It returns the account when they match, or raises PipedreamError when they do not.

**Call relations**: connected_account and newest_account call this after fetching records from Pipedream. It builds on _account’s basic account validation and adds the stricter per-user ownership check.

*Call graph*: calls 2 internal fn (__init__, _account); called by 2 (connected_account, newest_account).


##### `_account`  (lines 329–352)

```
def _account(record: dict[str, object], account_id: str) -> ConnectedAccount
```

**Purpose**: This converts a raw Pipedream account record into the project’s ConnectedAccount shape, while refusing accounts that cannot be safely used. It treats an unhealthy account as a grant problem that needs the member to reconnect, not as a transient server glitch.

**Data flow**: It receives a raw account record and the account id being read. It checks that the record includes a non-empty external owner, rejects records marked healthy: false by raising GrantUnusable, reads the app slug if present, and returns a ConnectedAccount with the id, app, and owner. If the owner is missing, it raises PipedreamError.

**Call relations**: workspace_account calls this directly before checking workspace ownership. _owned_account also calls it before checking exact external-user ownership.

*Call graph*: calls 3 internal fn (__init__, __init__, _dict); called by 2 (workspace_account, _owned_account); 1 external calls (__init__).


##### `workspace_user_prefix`  (lines 355–356)

```
def workspace_user_prefix(workspace_id: UUID) -> str
```

**Purpose**: This builds the standard prefix used for Pipedream external user ids that belong to a workspace. It gives the rest of the file one consistent naming pattern to use.

**Data flow**: It receives a workspace UUID. It converts the UUID to its compact hexadecimal form and places it after the ufo_ prefix with a trailing underscore. It returns that prefix string.

**Call relations**: _workspace_owns_external_user uses this to recognize workspace-scoped external user ids. connection_user_id uses it when creating a new state-scoped external user id.

*Call graph*: called by 2 (_workspace_owns_external_user, connection_user_id).


##### `_workspace_owns_external_user`  (lines 359–368)

```
def _workspace_owns_external_user(workspace_id: UUID, external_user_id: str) -> bool
```

**Purpose**: This decides whether a Pipedream external user id belongs to a specific workspace. It accepts both an older workspace-level form and the newer form that includes a derived connection id.

**Data flow**: It receives a workspace UUID and an external user id string. It first checks for the direct legacy form, then checks for the workspace prefix. If the prefix matches, it verifies that the remaining connection id is exactly 32 lowercase hexadecimal characters. It returns true only when the id fits one of the allowed workspace ownership patterns.

**Call relations**: workspace_account calls this after reading an account from Pipedream. The check is the final gate that decides whether the current workspace may use that connected account.

*Call graph*: calls 1 internal fn (workspace_user_prefix); called by 1 (workspace_account).


##### `connection_user_id`  (lines 371–373)

```
def connection_user_id(workspace_id: UUID, state: str) -> str
```

**Purpose**: This creates a stable Pipedream external user id for one workspace and one connection state value. It lets the OAuth return flow tie a connected account back to the exact workspace-scoped connection attempt.

**Data flow**: It receives a workspace UUID and a state string. It hashes the state with SHA-256, takes the first 32 hexadecimal characters as a connection id, and appends that to the workspace user prefix. It returns the resulting external user id string.

**Call relations**: This helper is used when starting or correlating a connection flow. It shares the same prefix format that _workspace_owns_external_user later recognizes when checking account ownership.

*Call graph*: calls 1 internal fn (workspace_user_prefix); 1 external calls (sha256).


##### `_body`  (lines 376–384)

```
def _body(response: httpx.Response) -> dict[str, object]
```

**Purpose**: This turns an HTTP response from Pipedream into a safe dictionary or raises a clear error. It is the common checkpoint that stops bad status codes and malformed response bodies from leaking into the rest of the code.

**Data flow**: It receives an httpx response. If the status code is 400 or higher, it raises PipedreamError with the status and text. If the body is empty, it returns an empty dictionary. Otherwise it parses JSON and returns it only if the parsed value is a dictionary; non-dictionary JSON raises PipedreamError.

**Call relations**: access_token, _get, and _post all call this immediately after receiving HTTP responses. Because those helpers sit under nearly every Pipedream operation, this function enforces response safety across the client.

*Call graph*: calls 1 internal fn (__init__); called by 3 (_get, _post, access_token); 1 external calls (json).


##### `pipedream_client`  (lines 387–405)

```
def pipedream_client() -> PipedreamClient
```

**Purpose**: This constructs the deployment’s default PipedreamClient from environment variables. It fails immediately if the required Pipedream credentials or project id are missing.

**Data flow**: It reads PIPEDREAM_CLIENT_ID, PIPEDREAM_CLIENT_SECRET, and PIPEDREAM_PROJECT_ID from the process environment, plus an optional PIPEDREAM_ENVIRONMENT value. If any required value is absent or empty, it raises RuntimeError. Otherwise it returns a PipedreamClient configured with those values.

**Call relations**: Higher-level code can call this when it needs the normal production client instead of a test-injected one. It is the bridge from deployment configuration into the PipedreamClient object used by the rest of the extension.

*Call graph*: 1 external calls (__init__).


### `extensions/composio/ufo_ext_composio/mcp_session.py`

`io_transport` · `request handling`

This file is a small bridge between this project and Composio’s Tool Router. The Tool Router speaks MCP, which means “Model Context Protocol”: a standard way for an AI app to talk to external tools. Here, the connection is made over streamable HTTP, which is simply an HTTP connection designed for tool-style messages.

The main job is: open a temporary session to a Composio MCP endpoint, call one named tool with some arguments, then close the session. Think of it like walking up to an information desk, asking one question, writing down the answer in a simple format, and leaving.

The file is careful about the shape of the answer. Some MCP responses already contain parsed dictionary data. If so, it returns that directly. If not, it checks another structured field. If that also is not available, it looks through text response blocks and tries to read the first text block as JSON. If the text is not valid JSON, it still preserves the text instead of throwing it away. As a final fallback, it returns whatever raw data was present under a generic result key.

This matters because the rest of the project wants a predictable plain dictionary, even though MCP responses can arrive in several different forms.

#### Function details

##### `mcp_call_tool`  (lines 18–42)

```
async def mcp_call_tool(endpoint: str, tool: str, arguments: dict[str, Any], headers: dict[str, str], timeout_seconds: float) -> dict[str, object]
```

**Purpose**: This function calls one tool on a Composio MCP endpoint and returns the result as a plain dictionary. It is useful when the rest of the system needs a clean, predictable answer from a remote tool-search service.

**Data flow**: It receives an endpoint URL, a tool name, input arguments, HTTP headers, and a timeout. It opens a fastmcp client using a streamable HTTP transport, sends the tool call, and waits for the response. It then checks the response in order: parsed data, structured content, JSON text content, plain text content, and finally raw data. The output is always a dictionary, even when the remote response was text or another simple value.

**Call relations**: When another part of the Composio extension needs to ask the Tool Router a question, it calls this function for one short-lived MCP session. Inside that session, this function builds the HTTP transport, creates the fastmcp client, calls the remote tool, and uses json.loads only if it has to turn a text response into structured data.

*Call graph*: 3 external calls (Client, StreamableHttpTransport, loads).


### `extensions/pipedream/ufo_ext_pipedream/proxy.py`

`io_transport` · `request handling and transport teardown`

Pipedream keeps provider credentials on its own servers, so this project cannot simply take a token and call Gmail, Slack, or another provider directly. This file solves that problem by acting like a mail forwarding service: a connector writes an ordinary HTTP request to the provider, and `PipedreamProxyTransport` repackages it as a request to Pipedream's proxy endpoint. Pipedream then adds the real credential on the server side and forwards the request to the provider.

The key idea is that the original provider URL is encoded into the Pipedream proxy URL. The account id and external user id are added as query values, so Pipedream knows which connected account to use. The original request body is preserved. Most original headers are also preserved, but Pipedream only forwards headers with a special `x-pd-proxy-` prefix, so this file adds that prefix to useful headers. It deliberately drops transport-level or sensitive headers such as `authorization`, `host`, and `content-length`, because those belong to the hop to Pipedream, not to the provider request.

The response is not translated into a custom shape. Pipedream returns the provider's status code, body, and headers, and this transport gives that response straight back to the caller. That matters because higher-level connectors may rely on provider-specific status codes, such as treating a 404 as an expired cursor.

#### Function details

##### `PipedreamProxyTransport.handle_async_request`  (lines 55–74)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: This is the main request path. It takes an ordinary HTTP request meant for a provider, rewrites it into a Pipedream Connect Proxy request, and sends that rewritten request using the underlying HTTP transport.

**Data flow**: It starts with an incoming `httpx.Request`, reads its body, and asks the Pipedream client for an access token. It builds new headers for Pipedream, copies useful original headers with the required proxy prefix, encodes the original provider URL with URL-safe Base64, and creates a new proxy URL containing the project id, external user id, and account id. It then creates a new `httpx.Request` pointed at Pipedream and passes it to the inner transport; the returned `httpx.Response` is passed back unchanged.

**Call relations**: When an `httpx.AsyncClient` uses this transport, this function is called for each outgoing provider request. Inside that flow it uses `request.aread` to capture the body, `base64.urlsafe_b64encode` to safely place the provider URL in the proxy path, and `httpx.URL` plus `httpx.Request` to build the Pipedream-bound request before handing it off to the wrapped inner transport.

*Call graph*: 4 external calls (urlsafe_b64encode, Request, aread, URL).


##### `PipedreamProxyTransport.aclose`  (lines 76–77)

```
async def aclose(self) -> None
```

**Purpose**: This closes the underlying HTTP transport when the proxy transport is no longer needed. It helps release network resources cleanly.

**Data flow**: It receives no new data from the caller. It simply tells the inner transport to close itself, which may shut down open connections or other transport resources. It returns nothing.

**Call relations**: This is used during cleanup, usually when the owning HTTP client is being closed. Rather than doing its own shutdown work, it delegates directly to the wrapped inner transport so the lower-level HTTP machinery can clean up correctly.


### Connector tool surfaces
Shared connector boundaries and agent-facing tools expose safe discovery, execution, file exchange, credential resolution, and MCP server calls.

### `core/src/ufo/runtime/access/connectors.py`

`domain_logic` · `cross-cutting: connector discovery, tool execution, and feed-sync credential lookup`

This file is the connector “front desk” for the system. It describes two related paths. First, a feed-sync job needs a way to read data from an outside provider. It asks for a Credential, which may be a hidden broker transport, a bearer token, or custom headers. The important rule is that secrets stay on the server side and must not be logged or sent to the agent sandbox. Second, dynamic connector tools need a broker that can list tools, describe their input, run them against a connected account, and stage file uploads or downloads by URL reference rather than by moving bytes through the main process.

The file also contains the registry that routes a provider name to the right broker. Some providers are explicitly registered. Others may belong to an “open” broker namespace that can answer for many provider slugs from its live catalog. For direct, bring-your-own-key providers, the registry can fall back to a selected auth backend.

A key safety feature is connection binding for feed sources. Before a source uses a brokered account, the code checks the database to confirm the connection still belongs to the workspace, owner member, provider, and account. For brokered HTTP traffic, it wraps the transport so this check happens again before every request. Like checking a badge at every door, this prevents an old source from continuing to use a connection after it has been removed or changed.

#### Function details

##### `Credential.__repr__`  (lines 57–66)

```
def __repr__(self) -> str
```

**Purpose**: Returns a safe text representation of a credential without revealing any secret token or header value. This matters because object representations can accidentally appear in logs or error reports.

**Data flow**: It reads which authentication path the Credential contains: broker transport, bearer token, custom headers, or nothing. It then returns a short redacted string that shows only the shape of the credential, not the secret itself.

**Call relations**: This is used automatically by Python whenever a Credential is printed or included in debugging output. It supports the wider connector rule in this file: credentials may be used inside the process, but their contents must not leak.


##### `AuthProxy.credential`  (lines 85–85)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Defines the promise that any auth backend must keep: given a workspace, provider, and account handle, return one usable Credential. It is a protocol method, meaning this file describes the required shape while other backends provide the real behavior.

**Data flow**: A caller provides the workspace ID, provider name, and account name. A concrete implementation uses those details to find or proxy the right secret and returns a Credential object that the connector can use to make provider requests.

**Call relations**: The registry and source credential resolver call objects through this interface when they need direct credentials or broker-provided feed credentials. Implementations outside this file decide whether the result is a hidden transport, a bearer token, or provider-specific headers.


##### `GrantUnusable.__init__`  (lines 113–115)

```
def __init__(self, reason: str, *, awaits_grant: bool=False) -> None
```

**Purpose**: Creates an error that means a connected account cannot currently be used and probably needs member action, such as reconnecting. It separates “this grant is bad” from temporary broker or provider outages.

**Data flow**: It receives a human-readable reason and an optional flag saying whether the system should wait specifically for the member to reconnect the grant. It stores the reason as the exception message and saves the flag on the exception object.

**Call relations**: Composio and Pipedream broker/client code raises this when they discover a grant is expired, revoked, unhealthy, or otherwise not usable. Downstream sync code can then park or skip the feed in a controlled way instead of retrying forever as if it were a temporary fault.

*Call graph*: called by 4 (credential, _account, credential, _account).


##### `stale_grant_guidance`  (lines 118–125)

```
def stale_grant_guidance(provider: str) -> str
```

**Purpose**: Builds a clear message for the case where a broker no longer recognizes an account grant. The message tells the reader the likely cause and the practical repair: ask the member to reconnect.

**Data flow**: It receives a provider name and inserts it into a fixed guidance sentence. It returns that sentence as plain text.

**Call relations**: Broker implementations can use this helper when they need consistent wording for stale or unknown grants. It supports the same repair path as GrantUnusable: avoid pointless retries and direct people toward reconnecting the account.


##### `ConnectorBroker.tools`  (lines 196–198)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Defines how a broker lists tools for one provider, optionally narrowed by a search query. These are the actions an agent may later ask the broker to run.

**Data flow**: A caller supplies a workspace ID, provider name, and query string. A concrete broker returns a tuple of BrokerTool objects, each describing an available tool at a high level.

**Call relations**: Dynamic connector discovery uses this broker-facing method through the registry. The protocol keeps core code independent from specific broker services such as Composio or Pipedream.


##### `ConnectorBroker.schema`  (lines 200–200)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Defines how a broker returns the detailed input shape for a specific tool. This lets an agent know what arguments it must provide before execution.

**Data flow**: A caller provides workspace ID, provider name, and tool slug. The broker returns a BrokerTool with its input schema filled in, or raises UnknownBrokerTool if that slug is not known for the provider.

**Call relations**: Tool-description flows call this through a ConnectorBroker implementation after a provider and tool have been selected. The UnknownBrokerTool path lets callers report unresolved tool names cleanly.


##### `ConnectorBroker.execute`  (lines 202–210)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Defines how a broker runs one provider tool against a granted account. The broker, not the agent sandbox, injects the provider credential.

**Data flow**: A caller sends workspace ID, provider, tool slug, argument values, account ID, and an optional idempotency key used to avoid duplicate side effects. The broker performs the provider action and returns a dictionary response.

**Call relations**: Dynamic connector tools dispatch execution through this method after resolving the provider in the registry. Because the broker owns the token, this call preserves the file’s main safety rule: secrets do not cross into the sandbox.


##### `ConnectorBroker.file_outputs`  (lines 212–212)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Defines how a broker extracts produced files from a tool response. It turns broker-specific response details into a common list of downloadable file references.

**Data flow**: It receives the dictionary response from a broker execution. A concrete broker reads that response and returns BrokerFile objects containing names and short-lived URLs.

**Call relations**: After tool execution, connector tooling can call this to find files that should be made available to the sandbox. The main process passes references, not file bytes.


##### `ConnectorBroker.stage_upload`  (lines 214–222)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Defines how a broker prepares for a workspace file to be used as an input to a connector tool. It creates an upload target or tells the caller the broker already has the file.

**Data flow**: A caller gives workspace ID, provider, tool slug, filename, MIME type, and MD5 hash. The broker returns a StagedUpload containing a PUT URL if bytes must be uploaded, the required content type, and the argument value to place in the tool call.

**Call relations**: Dynamic connector tools use this before executing broker tools that accept files. The sandbox uploads directly to broker storage, so large file contents do not flow through the serve process.


##### `ConnectorBroker.search`  (lines 224–224)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Defines semantic tool search for brokers that can return not just matching tools, but also an execution plan and warnings. This helps agents choose the right connector tool more intelligently.

**Data flow**: A caller provides workspace ID, provider, and a natural-language query. The broker returns a BrokerSearch containing matching tools and optional guidance, plan steps, and pitfalls.

**Call relations**: Search-based connector discovery can use this method through the broker. Brokers without richer routing can return an empty or simple answer while still fitting the same interface.


##### `ConnectorBroker.credential`  (lines 226–226)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Defines how a broker supplies a feed-sync credential for a connected account. The expected safe form is usually a transport that forwards requests through the broker so the token stays hidden.

**Data flow**: A caller provides workspace ID, provider name, and account handle. The broker verifies or resolves the account and returns a Credential for provider HTTP requests.

**Call relations**: _credential calls this when a feed source uses a brokered account rather than direct credentials. _BoundSourceCredentials then requires the returned credential to include a proxy transport for brokered sources.


##### `RequestForwarder.forward`  (lines 245–247)

```
async def forward(self, account_id: str, method: str, url: str, headers: Mapping[str, str], body: bytes) -> ForwardedResponse
```

**Purpose**: Defines how a matched HTTP request from the sandbox is forwarded through a broker under a connected account. The broker adds the real provider credential on the server side.

**Data flow**: It receives the account ID, HTTP method, URL, headers, and request body. A concrete forwarder sends the request through the broker and returns a ForwardedResponse with status, headers, and body.

**Call relations**: The egress proxy uses implementations of this interface when it sees a connector CLI request carrying a sentinel credential. The sandbox thinks it made a normal authenticated request, but the real secret never entered the sandbox.


##### `ConnectorResolver.transfer_hosts`  (lines 302–302)

```
def transfer_hosts(self) -> tuple[str, ...]
```

**Purpose**: Defines which broker file-storage hosts should be allowed for file transfers in an open connector namespace. These hosts are needed when tools upload or download files by presigned URL.

**Data flow**: A concrete resolver exposes a tuple of host names. Callers use those host names to permit broker file traffic for grants in that namespace.

**Call relations**: Connector resolver implementations provide this property to the registry-side connector system. It supports the file-transfer path described by ConnectorBroker.stage_upload and ConnectorBroker.file_outputs.


##### `ConnectorResolver.claims`  (lines 304–304)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: Defines how an open connector resolver says whether it serves a provider slug. This prevents the system from assuming the resolver owns every unknown provider name.

**Data flow**: It receives a provider slug and performs whatever lookup the resolver needs, often against a broker catalog. It returns true if the resolver can serve that provider and false otherwise.

**Call relations**: Code choosing between an open broker namespace and other credential options can ask this method before routing. The method belongs to resolver implementations outside this file.


##### `ConnectorResolver.entry`  (lines 306–306)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: Builds a ConnectorEntry for a provider served by an open connector namespace. This gives the registry a normal routing object even when the provider was not explicitly registered at startup.

**Data flow**: It receives a provider slug. The resolver returns a ConnectorEntry containing that provider, a label, and the shared broker that will serve it.

**Call relations**: ConnectorRegistry.entry and the helper _broker call this when a provider is not found among explicit entries but a resolver is installed. It is the bridge from an open broker catalog into the normal broker routing path.


##### `ConnectorResolver.catalog`  (lines 308–308)

```
async def catalog(self, query: str, limit: int, after: str | None) -> CatalogPage
```

**Purpose**: Defines paged search over a broker’s live catalog of connectable services. This lets discovery show services that were not hard-coded into the registry.

**Data flow**: A caller provides a query string, a page size, and an optional cursor telling where to continue. The resolver returns a CatalogPage containing matching provider entries and the next cursor, if any.

**Call relations**: ConnectorRegistry.search_catalog and ConnectorRegistry.catalog call this when an open namespace exists. This keeps the discovery list fresh without requiring every possible provider to be registered in code.


##### `ConnectorRegistry.entry`  (lines 325–331)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: Finds the routing entry for a provider. It first looks for an explicitly installed connector, then falls back to the open resolver if one exists.

**Data flow**: It receives a provider name. It checks the registry’s explicit entries, otherwise asks the resolver to build an entry, and returns the resulting ConnectorEntry. If neither path exists, it raises a KeyError.

**Call relations**: Dynamic connector tools use this kind of lookup when they need to send a provider request to the correct broker. The helper _broker follows the same routing idea when resolving feed-sync credentials.


##### `ConnectorRegistry.search_catalog`  (lines 333–338)

```
async def search_catalog(self, query: str, limit: int) -> tuple[CatalogEntry, ...]
```

**Purpose**: Returns the open resolver’s first page of catalog search results, or nothing when no open resolver is installed. It is a lightweight way to add live broker services to discovery.

**Data flow**: It receives a query and limit. If there is no resolver, it returns an empty tuple; otherwise it asks the resolver for the first catalog page and returns that page’s entries.

**Call relations**: Discovery tools can use this alongside explicitly registered providers. The actual live lookup is handed off to ConnectorResolver.catalog.


##### `ConnectorRegistry.catalog`  (lines 340–360)

```
async def catalog(self, query: str, limit: int, after: str | None) -> CatalogPage
```

**Purpose**: Builds one combined catalog page from explicitly registered connectors and the open resolver catalog. It also removes duplicate provider entries so the same service is not shown twice.

**Data flow**: It receives a query, page size, and optional cursor. On the first page, it filters explicit registry entries by provider or label text; on later pages, it skips explicit entries. It then asks the resolver for its page if available, combines both sets, keeps the first entry for each provider, and returns a CatalogPage.

**Call relations**: Connector discovery calls this when listing connectable services. It constructs CatalogEntry and CatalogPage values and delegates the live, paged portion to the resolver when one is installed.

*Call graph*: 2 external calls (__init__, __init__).


##### `_broker`  (lines 363–369)

```
def _broker(registry: ConnectorRegistry, provider: str) -> ConnectorBroker | None
```

**Purpose**: Finds the broker responsible for a provider, if any. It is the registry’s internal shortcut for credential routing.

**Data flow**: It receives the ConnectorRegistry and provider name. It checks explicit entries first, then asks the resolver for an entry if available, and returns that entry’s broker. If no broker can be found, it returns None.

**Call relations**: _credential calls this when a feed source uses a non-direct account. That keeps the credential path aligned with the same provider-to-broker routing used elsewhere.

*Call graph*: called by 1 (_credential).


##### `_credential`  (lines 372–385)

```
async def _credential(registry: ConnectorRegistry, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Chooses the correct way to obtain a feed-sync Credential. Brokered accounts go through a connector broker; direct accounts go through the fallback auth backend.

**Data flow**: It receives the registry, workspace ID, provider, and account handle. If the account is not the special direct account, it looks up a broker and asks that broker for credentials. If the account is direct, it asks the fallback AuthProxy. If the needed route is missing, it raises a RuntimeError.

**Call relations**: _BoundSourceCredentials.credential calls this after applying source-binding checks. _credential itself calls _broker for brokered accounts, keeping the lower-level credential lookup separate from connection authorization.

*Call graph*: calls 1 internal fn (_broker); called by 1 (credential).


##### `_require_source_connection`  (lines 388–412)

```
async def _require_source_connection(workspace_id: UUID, connection_id: UUID, owner_member_id: UUID, provider: str, account: str) -> None
```

**Purpose**: Verifies that a feed source is still allowed to use a specific member-owned connection. This stops stale or changed sources from silently continuing to use an account they no longer own.

**Data flow**: It receives workspace ID, connection ID, owner member ID, provider, and account. Inside the workspace context, it opens a workspace database transaction and searches for a connection row matching all those values. If the row exists, it returns normally; if not, it raises ValueError.

**Call relations**: _BoundSourceCredentials.credential calls this before giving a source brokered credentials. _ConnectionTransport.handle_async_request calls it again before each proxied HTTP request, so authorization is checked not just once but repeatedly while the source runs.

*Call graph*: called by 2 (credential, handle_async_request); 3 external calls (select, workspace_tx, ws).


##### `_ConnectionTransport.handle_async_request`  (lines 424–432)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: Sends one HTTP request through an inner broker transport, but only after rechecking that the source’s connection is still valid. It is a safety wrapper around the real transport.

**Data flow**: It receives an httpx request object. Before forwarding, it calls _require_source_connection with the bound workspace, connection, owner, provider, and account. If the check passes, it hands the request to the inner transport and returns the resulting HTTP response.

**Call relations**: _BoundSourceCredentials.credential creates this wrapper when returning brokered source credentials. Every provider request made through that credential passes through this method before reaching the broker transport.

*Call graph*: calls 1 internal fn (_require_source_connection).


##### `_ConnectionTransport.aclose`  (lines 434–435)

```
async def aclose(self) -> None
```

**Purpose**: Closes the wrapped HTTP transport when the client is done. This frees any network resources held by the inner transport.

**Data flow**: It takes no new input beyond the wrapper object. It calls the inner transport’s close method and returns when that cleanup is complete.

**Call relations**: HTTP client cleanup calls this through the httpx transport interface. The method simply forwards teardown to the real transport that _ConnectionTransport is protecting.


##### `_BoundSourceCredentials.credential`  (lines 444–474)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns credentials for a specific feed source while enforcing whether that source is direct-key based or connection based. It prevents a source from switching to a different authentication mode than the one it was bound to.

**Data flow**: It receives workspace ID, provider, and account. For the direct account, it rejects any source that was bound to a connection, then delegates to _credential. For brokered accounts, it requires stored connection and owner IDs, verifies the connection in the database, fetches the broker credential, requires that it contain a transport, and wraps that transport in _ConnectionTransport before returning a new Credential.

**Call relations**: SourceCredentialResolver.bind creates this object for a sync source. This method then sits between the source runner and _credential, adding connection ownership checks before the underlying broker or fallback backend is used.

*Call graph*: calls 2 internal fn (_credential, _require_source_connection); 2 external calls (__init__, __init__).


##### `SourceCredentialResolver.bind`  (lines 481–486)

```
def bind(self, connection_id: UUID | None, owner_member_id: UUID | None) -> AuthProxy
```

**Purpose**: Creates an AuthProxy that is tied to one feed source’s connection information. This gives the sync runner a credential resolver that remembers what connection, if any, the source is allowed to use.

**Data flow**: It receives an optional connection ID and optional owner member ID. It packages those values with the registry into a _BoundSourceCredentials object and returns it as the AuthProxy for that source.

**Call relations**: The sync runner uses this binding step before resolving source credentials. Later, calls to _BoundSourceCredentials.credential use the saved connection details to enforce the source’s authorization.

*Call graph*: 1 external calls (__init__).


### `extensions/connectors/ufo_ext_connectors/tools.py`

`domain_logic` · `request handling`

A connector broker is like a front desk for many outside services. The agent cannot keep a hard-coded list of every possible GitHub or Slack action, so this file gives it a small set of general tools: search for available connectors, discover the tools inside one connector, and call one of those tools. Without this file, the agent would either have to guess tool names or could not safely pass files and results between the workspace and external services.

The file also protects the surrounding system from several practical problems. If a connector call needs a workspace file, the file is first checked inside the sandbox, uploaded through the broker’s approved upload route, and replaced with the broker’s own file reference. If the connector returns files, they are downloaded into a dedicated workspace folder using safe names. If a provider returns base64 text, which is encoded bytes often too large or unreadable for chat context, this file decodes it: small text is shown inline, while large or binary data is written to a workspace file.

Slack gets one special rule. When the agent sends a Slack message through a connector, this file adds a small “Sent using ufo” attribution footer unless one is already present. Finally, repeated large objects in results are replaced with pointers to the first copy, so the answer stays readable instead of wasting space on duplicates.

#### Function details

##### `list_external_tools`  (lines 239–275)

```
async def list_external_tools(ctx: ToolContext, args: ListExternalToolsInput) -> ToolResult
```

**Purpose**: Searches the connectors available in the current turn, such as Slack or GitHub, and reports which connected accounts can already be used. This is the agent’s first step when it needs to know what outside services are reachable.

**Data flow**: It receives the current tool context and one or more search queries. It reads the connector registry and active account grants, matches connectors by provider ID or label, also asks the broker catalog for matching providers, then returns a JSON tool result containing connector rows and connected account details.

**Call relations**: This is a public tool handler. It asks _registry for the turn’s connector registry, asks _connected_accounts for account information, searches broker catalogs in parallel, and hands the final payload to _json_result for packaging.

*Call graph*: calls 3 internal fn (_connected_accounts, _json_result, _registry); 1 external calls (gather).


##### `_connected_accounts`  (lines 278–292)

```
async def _connected_accounts(ctx: ToolContext) -> dict[str, list[JsonValue]]
```

**Purpose**: Builds a simple summary of the connected accounts the agent can use for each provider. It includes both accounts owned by the user and shared connections.

**Data flow**: It receives the tool context. If no grant service is present, it returns an empty map; otherwise it reads active grants and groups account ID, owner email, and shared status by provider.

**Call relations**: list_external_tools calls this while preparing connector search results, so connector rows can say not only what service exists but also whose account is connected.

*Call graph*: called by 1 (list_external_tools).


##### `describe_external_tools`  (lines 295–317)

```
async def describe_external_tools(ctx: ToolContext, args: DescribeExternalToolsInput) -> ToolResult
```

**Purpose**: Describes real tools inside a selected connector. It can fetch exact schemas for known tool names, or search/discover likely tools when the caller only has a goal or guessed name.

**Data flow**: It receives a source ID, optional exact tool names, and an optional query. It gets the connector entry, asks the broker for schemas for exact names, records names the broker does not know, optionally searches the broker catalog, adds available tool rows and notes, then returns a JSON result.

**Call relations**: This is a public discovery handler. It uses _registry to find the connector, _tool_json to simplify broker tool objects, _discovery_query to turn unresolved guesses into search text, _discovered_rows to prepare a safe discovery list, and _json_result to return it.

*Call graph*: calls 5 internal fn (_discovered_rows, _discovery_query, _json_result, _registry, _tool_json).


##### `attribution_stripped`  (lines 320–324)

```
def attribution_stripped(text: str) -> str
```

**Purpose**: Removes any ufo Slack attribution footer from text that came back from Slack. This helps the system decide what a human actually wrote, rather than treating the bot’s own footer as user content.

**Data flow**: It receives a text string, searches it for attribution text in any allowed shape, removes those matches, and returns the cleaned string.

**Call relations**: This helper is available to Slack-reading code. It is the inverse of the attribution-writing helpers in this file: outbound messages may be marked, and inbound text can later be cleaned before interpretation.


##### `attributed_arguments`  (lines 327–354)

```
def attributed_arguments(arguments: dict[str, JsonValue], subject: str) -> dict[str, JsonValue]
```

**Purpose**: Adds a ufo attribution footer to Slack send arguments when there is a message body to mark. It avoids adding a second footer if one is already present.

**Data flow**: It receives a connector argument dictionary and the attribution subject text. It checks for existing attribution, creates a Slack context footer block, appends it to existing blocks when possible, or turns text/markdown arguments into Slack blocks with the footer after them; if it cannot safely find a message body, it returns the original arguments.

**Call relations**: slack_attributed calls this after deciding that a connector call is really a Slack message send. This function delegates body conversion to _body_blocks, block-list appending to _appended_blocks, and duplicate-footer detection to _carries_attribution.

*Call graph*: calls 3 internal fn (_appended_blocks, _body_blocks, _carries_attribution); called by 1 (slack_attributed).


##### `_body_blocks`  (lines 357–385)

```
def _body_blocks(arguments: dict[str, JsonValue]) -> list[JsonValue] | None
```

**Purpose**: Turns plain Slack message body arguments into Slack block objects so a footer block can be appended. Blocks are Slack’s structured message pieces.

**Data flow**: It receives the connector arguments. If markdown_text is present, it creates one markdown block; if text is present, it splits long text into section blocks that fit Slack’s per-block size limit; if neither exists, it returns nothing.

**Call relations**: attributed_arguments calls this when the send did not already provide a blocks argument. It prepares the message body in the same block list that will receive the attribution footer.

*Call graph*: called by 1 (attributed_arguments).


##### `_appended_blocks`  (lines 388–409)

```
def _appended_blocks(value: JsonValue, footer: dict[str, JsonValue]) -> JsonValue | None
```

**Purpose**: Appends the attribution footer to an existing Slack blocks argument, including when the blocks were supplied as a JSON string. It leaves the value alone if it cannot safely understand it.

**Data flow**: It receives a blocks value and a footer block. If the value is a non-empty list, it returns a new list with the footer added; if it is a string, it tries to parse it as JSON, optionally URL-decoded, checks for existing attribution, then returns the same style of string with the footer added; otherwise it returns null.

**Call relations**: attributed_arguments uses this for messages that already have Slack blocks. It calls _carries_attribution to avoid stacking footers and uses JSON and URL encoding helpers to preserve the caller’s original representation.

*Call graph*: calls 1 internal fn (_carries_attribution); called by 1 (attributed_arguments); 4 external calls (dumps, loads, quote, unquote).


##### `_carries_attribution`  (lines 412–421)

```
def _carries_attribution(value: JsonValue) -> bool
```

**Purpose**: Checks whether a nested value already contains a ufo attribution line. This prevents repeated sends or edits from collecting multiple footers.

**Data flow**: It receives any JSON-like value. It searches strings directly, walks lists item by item, walks dictionary values, and returns true as soon as it finds the attribution text.

**Call relations**: attributed_arguments uses it before changing anything, and _appended_blocks uses it after parsing existing block data. It is the shared guard against duplicate Slack attribution.

*Call graph*: called by 2 (_appended_blocks, attributed_arguments); 1 external calls (values).


##### `slack_attributed`  (lines 424–438)

```
def slack_attributed(provider: str, slug: str, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Applies the Slack attribution rule only to connector calls that appear to send, post, reply with, or schedule a Slack message. Non-Slack tools and non-send Slack tools are left untouched.

**Data flow**: It receives the provider name, tool slug, and arguments. It checks whether the provider is Slack and whether the slug looks like a message-sending action; if so, it returns arguments with attribution added, otherwise it returns the original arguments.

**Call relations**: call_external_tool calls this just before execution. When the call qualifies, it hands the detailed block-editing work to attributed_arguments.

*Call graph*: calls 1 internal fn (attributed_arguments); called by 1 (call_external_tool).


##### `call_external_tool`  (lines 441–450)

```
async def call_external_tool(ctx: ToolContext, args: CallExternalToolInput) -> ToolResult
```

**Purpose**: Runs one real connector tool through its broker. This is the execution path after the agent has discovered the exact tool name and prepared the required arguments.

**Data flow**: It receives source ID, tool name, optional account ID, and tool arguments. It finds the connector entry, enforces read-only mode when enabled, chooses the correct connected account, adds Slack attribution when needed, creates a _ConnectorCall, and returns the call’s serialized result as tool output.

**Call relations**: This is the public execution handler. It uses _registry to find the connector, ToolContext.connector_account to resolve which account to use, slack_attributed for Slack sends, and _ConnectorCall.run for the full staging, broker execution, result cleanup, and file transfer flow.

*Call graph*: calls 3 internal fn (connector_account, _registry, slack_attributed); 3 external calls (__init__, __init__, __init__).


##### `_ConnectorCall.run`  (lines 478–491)

```
async def run(self, arguments: dict[str, JsonValue], account_id: str) -> str
```

**Purpose**: Carries out one connector execution from start to finish. It stages input files, calls the broker, fetches output files, decodes embedded data, and shrinks repeated result objects.

**Data flow**: It receives already-approved arguments and an account ID. It rewrites any workspace-file arguments into broker file references, calls the broker execute API, downloads any files named by the broker response, translates base64 data in the response, adds workspace file listings if present, then returns a JSON string with repeated objects condensed.

**Call relations**: call_external_tool creates a _ConnectorCall and calls this method. It hands work to _staged_value before execution, _fetched_files and _translated_node after execution, and runs _deduped in a worker thread so the main async loop is not held by a large cleanup pass.

*Call graph*: calls 3 internal fn (_fetched_files, _staged_value, _translated_node); 1 external calls (to_thread).


##### `_ConnectorCall._staged_value`  (lines 493–508)

```
async def _staged_value(self, value: object) -> object
```

**Purpose**: Walks through an argument value and replaces every workspace file marker with a broker-ready file argument. This lets connector tools consume local workspace files without the main process moving file bytes itself.

**Data flow**: It receives any nested argument value. If it finds exactly a {"workspace_file": path} object, it validates the path string and stages that file; if it finds a dictionary or list, it recursively rewrites children; other values pass through unchanged.

**Call relations**: _ConnectorCall.run calls this for every top-level argument before broker execution. When a real file marker is found, it hands the upload work to _stage_file.

*Call graph*: calls 1 internal fn (_stage_file); called by 1 (run).


##### `_ConnectorCall._stage_file`  (lines 510–546)

```
async def _stage_file(self, path: str) -> dict[str, object]
```

**Purpose**: Uploads one workspace file to the broker’s file store, or skips upload if the broker says it already has the same bytes. This is how local files become safe connector arguments.

**Data flow**: It receives a workspace path. It converts it to a scoped workspace path, asks the sandbox to hash and measure the file through containment checks, rejects missing or oversized files, guesses the file type, asks the broker for an upload location, uploads with curl inside the sandbox when needed, and returns the broker’s argument value.

**Call relations**: _staged_value calls this when it sees a workspace_file argument. It talks to the sandbox for safe file reading and network upload, and to the broker for the presigned upload location and final argument reference.

*Call graph*: called by 1 (_staged_value); 4 external calls (guess_type, PurePosixPath, quote, workspace_path).


##### `_ConnectorCall._fetched_files`  (lines 548–581)

```
async def _fetched_files(self, files: tuple[BrokerFile, ...]) -> list[dict[str, str]]
```

**Purpose**: Downloads files produced by a connector tool into the workspace. It gives each fetch a fresh folder so returned files do not overwrite each other.

**Data flow**: It receives broker file records containing names and download URLs. For each one, it chooses a safe leaf filename, claims a unique workspace target through the sandbox containment guard, downloads the URL with curl inside the sandbox, and returns a list of saved file names and workspace paths.

**Call relations**: _ConnectorCall.run calls this after broker execution, using the broker’s file_outputs view of the response. The returned list is later added to the tool result under workspace_files.

*Call graph*: called by 1 (run); 3 external calls (quote, contained_leaf, uuid4).


##### `_ConnectorCall._translated_node`  (lines 583–643)

```
async def _translated_node(self, node: Mapping[str, object], depth: int=0) -> dict[str, object]
```

**Purpose**: Looks inside one result object for provider-marked base64 content and turns it into readable text or workspace file references. This keeps giant encoded blobs out of the agent’s context.

**Data flow**: It receives a mapping from the broker result. It checks for fields that declare base64 encoding, recursively translates child values, tries to decode marked content fields, chooses a filename and type, replaces decoded fields with text or file references, and updates the encoding marker when every marked field was successfully translated.

**Call relations**: _ConnectorCall.run starts result translation here, and _translated calls it for nested objects. It relies on _decoded_base64 to verify and decode content, _translated_bytes to decide inline versus file storage, and _translated for recursive walking.

*Call graph*: calls 3 internal fn (_translated, _translated_bytes, _decoded_base64); called by 2 (_translated, run); 1 external calls (guess_type).


##### `_ConnectorCall._translated`  (lines 645–664)

```
async def _translated(self, value: object, depth: int) -> object
```

**Purpose**: Recursively translates any value in a connector result. It handles nested objects, lists, and self-contained data URLs while stopping at a maximum depth for safety.

**Data flow**: It receives a value and its nesting depth. If the value is too deep, it returns it unchanged; dictionaries go to _translated_node, lists are walked element by element, short data: base64 URLs go to _translated_data_url, and other values pass through.

**Call relations**: _translated_node calls this for child values. It loops back to _translated_node for nested objects and uses _translated_data_url for strings that carry their own base64 marker.

*Call graph*: calls 2 internal fn (_translated_data_url, _translated_node); called by 1 (_translated_node).


##### `_ConnectorCall._translated_data_url`  (lines 666–678)

```
async def _translated_data_url(self, value: str) -> object
```

**Purpose**: Decodes a data URL that contains base64 bytes, such as data:image/png;base64,.... If the string only looks like a data URL but is not valid, it is preserved.

**Data flow**: It receives one string. It matches the data URL pattern, decodes the payload if valid, chooses a filename extension from the MIME type, then returns either inline decoded text or a workspace file reference.

**Call relations**: _translated calls this when it sees a short string starting with data:. It uses _decoded_base64 for safe decoding and _translated_bytes for the inline-or-file decision.

*Call graph*: calls 2 internal fn (_translated_bytes, _decoded_base64); called by 1 (_translated); 1 external calls (guess_extension).


##### `_ConnectorCall._translated_bytes`  (lines 680–689)

```
async def _translated_bytes(self, decoded: bytes, text: str | None, name: str, mimetype: str) -> object
```

**Purpose**: Decides how decoded bytes should appear in the result. Small UTF-8 text stays readable inline; large text or binary data is written to the workspace.

**Data flow**: It receives raw bytes, optional decoded text, a filename, and a MIME type. If there is text and it is below the inline size cap, it returns the text; otherwise it writes the bytes out through _offloaded and returns that file reference.

**Call relations**: _translated_node and _translated_data_url call this after base64 decoding. It is the small decision point between keeping useful text in context and moving bulky data to a file.

*Call graph*: calls 1 internal fn (_offloaded); called by 2 (_translated_data_url, _translated_node).


##### `_ConnectorCall._offloaded`  (lines 691–733)

```
async def _offloaded(self, name: str, mimetype: str, data: bytes) -> dict[str, object]
```

**Purpose**: Writes decoded large or binary content into the workspace and returns a reference to it. The path is based on the content hash, so identical decoded bytes reuse the same location.

**Data flow**: It receives a proposed name, MIME type, and bytes. It sanitizes the filename, builds a content-addressed target path, writes bytes to a temporary part file through the sandbox, atomically places that file at the final path through containment checks, and returns name, workspace path, MIME type, and byte count.

**Call relations**: _translated_bytes calls this when content should not be placed inline. It uses sandbox writing and a guarded placement script so predictable hash paths cannot be abused with symlinks or unsafe filesystem tricks.

*Call graph*: called by 1 (_translated_bytes); 3 external calls (sha256, contained_leaf, uuid4).


##### `_ConnectorCall._deduped`  (lines 735–793)

```
def _deduped(self, payload: dict[str, object]) -> str
```

**Purpose**: Serializes the connector result and, when safe and useful, replaces repeated large objects with JSON Pointer references to their first occurrence. This keeps repeated boilerplate from pushing useful results out of context.

**Data flow**: It receives the cleaned result payload. It serializes it once, skips deduplication if the payload is too large, too structurally dense, or already uses the same_as key, otherwise walks top-level values through _condensed and returns a JSON string of the condensed payload.

**Call relations**: _ConnectorCall.run calls this at the end in a worker thread. It uses _condensed for the structural walk and _escaped to build valid JSON Pointer paths.

*Call graph*: calls 2 internal fn (_condensed, _escaped); 1 external calls (dumps).


##### `_ConnectorCall._condensed`  (lines 795–871)

```
def _condensed(self, value: object, pointer: str, depth: int, first: dict[bytes, str]) -> tuple[object, bytes, int]
```

**Purpose**: Walks one part of a result and detects whether a large object has appeared before. Repeated objects become {"same_as": "..."} pointers while the first copy stays complete.

**Data flow**: It receives a value, its JSON Pointer path, current depth, and a map of first-seen object hashes. It recursively processes dictionaries and lists, computes a stable hash for each node, estimates the original size, records large first-seen objects, replaces later identical dictionaries with pointers, and returns the rewritten value, digest, and size.

**Call relations**: _deduped calls this while building the final compact result. It uses _escaped for pointer path pieces and SHA-256 hashing to compare provider-chosen content without relying on field names.

*Call graph*: calls 1 internal fn (_escaped); called by 1 (_deduped); 1 external calls (sha256).


##### `_escaped`  (lines 874–877)

```
def _escaped(token: str) -> str
```

**Purpose**: Escapes one path token for a JSON Pointer, which is a standard way to point at a location inside JSON. It makes keys containing / or ~ still point to the exact original key.

**Data flow**: It receives a dictionary key or path token string. It replaces ~ with ~0 and / with ~1, then returns the escaped token.

**Call relations**: _deduped and _condensed use this whenever they build same_as pointer paths. It makes the deduplication references unambiguous for any provider field name.

*Call graph*: called by 2 (_condensed, _deduped).


##### `_decoded_base64`  (lines 880–904)

```
def _decoded_base64(value: object) -> tuple[bytes, str | None] | None
```

**Purpose**: Safely decodes a value that a provider explicitly marked as base64. It refuses oversized or invalid values instead of guessing and possibly corrupting normal strings.

**Data flow**: It receives any value. If it is not a string or is above the decode size cap, it returns null; otherwise it removes whitespace, strictly base64-decodes it, tries to decode the bytes as UTF-8 text, and returns bytes plus text when possible.

**Call relations**: _translated_node and _translated_data_url call this before replacing provider content. It is the gate that decides whether a marked field really can be translated.

*Call graph*: called by 2 (_translated_data_url, _translated_node); 1 external calls (b64decode).


##### `search_connector_tools`  (lines 907–921)

```
async def search_connector_tools(ctx: ToolContext, args: SearchConnectorToolsInput) -> ToolResult
```

**Purpose**: Performs richer tool discovery inside one connector for a natural-language goal. It returns matching tools plus broker-provided planning advice, guidance, and pitfalls.

**Data flow**: It receives a source ID and query. It finds the connector, asks the broker search endpoint for matching tools and advice, formats the tool rows with fallback behavior, adds any note, and returns a JSON result.

**Call relations**: This is a public discovery handler. It uses _registry to find the connector, _discovered_rows to prepare a bounded list of tools, and _json_result to package the final answer.

*Call graph*: calls 3 internal fn (_discovered_rows, _json_result, _registry).


##### `_registry`  (lines 924–927)

```
def _registry(ctx: ToolContext) -> ConnectorRegistry
```

**Purpose**: Fetches the connector registry from the current tool context. The registry is the turn-specific directory of available connector providers and their brokers.

**Data flow**: It receives the tool context. If no connector registry is present, it raises an error; otherwise it returns the registry object.

**Call relations**: All public connector handlers call this before doing registry-based work: list_external_tools, describe_external_tools, search_connector_tools, and call_external_tool.

*Call graph*: called by 4 (call_external_tool, describe_external_tools, list_external_tools, search_connector_tools).


##### `_tool_json`  (lines 930–931)

```
def _tool_json(tool: BrokerTool) -> dict[str, object]
```

**Purpose**: Converts a broker tool object into the simple JSON shape returned to the agent. It keeps the tool slug, description, and input schema.

**Data flow**: It receives a BrokerTool. It reads its slug, description, and input schema, then returns a plain dictionary with those fields.

**Call relations**: describe_external_tools uses this for exact schema results, and _available_tools uses it when building discovery rows.

*Call graph*: called by 2 (_available_tools, describe_external_tools).


##### `_discovered_rows`  (lines 934–951)

```
async def _discovered_rows(entry: ConnectorEntry, workspace_id: UUID, query: str, found: tuple[BrokerTool, ...]) -> tuple[list[dict[str, object]], str]
```

**Purpose**: Builds the list of tool rows for discovery answers and adds notes when the answer is a fallback or has been shortened. This prevents a failed search from looking like proof that a connector cannot help.

**Data flow**: It receives a connector entry, workspace ID, query, and broker-found tools. If a non-empty query found nothing, it asks for the connector’s top tools instead, marks that as a fallback, trims rows to the inline budget, and returns rows plus a human-readable note.

**Call relations**: describe_external_tools and search_connector_tools both use this shared path, so both discovery tools follow the same “never a dead end” rule. It hands row trimming to _available_tools.

*Call graph*: calls 1 internal fn (_available_tools); called by 2 (describe_external_tools, search_connector_tools).


##### `_available_tools`  (lines 954–967)

```
def _available_tools(listed: tuple[BrokerTool, ...]) -> list[dict[str, object]]
```

**Purpose**: Chooses how many discovered tool rows can be returned inline without making the result too large. This helps keep discovery useful in the conversation instead of offloading it to a file.

**Data flow**: It receives a tuple of broker tools. It converts each to a simple row, counts the serialized size spent so far, stops after the budget is exceeded once at least one row is present, and returns the rows that fit.

**Call relations**: _discovered_rows calls this after it has decided which broker listing should be shown. It uses _tool_json for each row and JSON serialization to estimate size.

*Call graph*: calls 1 internal fn (_tool_json); called by 1 (_discovered_rows); 1 external calls (dumps).


##### `_discovery_query`  (lines 970–977)

```
def _discovery_query(explicit: str, unresolved: list[str]) -> str
```

**Purpose**: Creates a broker search query for describe_external_tools when exact tool names were guessed incorrectly. It turns missed slugs into useful search words.

**Data flow**: It receives an explicit query and a list of unresolved names. If the explicit query is non-empty, it returns that; otherwise it lowercases unresolved names, replaces punctuation with spaces, removes duplicate words while keeping order, and returns the resulting query.

**Call relations**: describe_external_tools calls this when it needs to search after unresolved exact tool names, helping the broker surface real slugs that resemble the caller’s guess.

*Call graph*: called by 1 (describe_external_tools); 1 external calls (sub).


##### `_json_result`  (lines 980–981)

```
def _json_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: Wraps a dictionary payload as the standard text-based tool result used by these connector tools.

**Data flow**: It receives a payload dictionary, serializes it to JSON text, puts that text in a TextContent object, and returns a ToolResult containing it.

**Call relations**: list_external_tools, describe_external_tools, and search_connector_tools call this at the end of their public handler flows. call_external_tool builds its ToolResult directly because _ConnectorCall.run already returns serialized text.

*Call graph*: called by 3 (describe_external_tools, list_external_tools, search_connector_tools); 3 external calls (__init__, __init__, dumps).


### `extensions/mcp/ufo_ext_mcp.py`

`io_transport` · `request handling`

This extension is a bridge between the agent and external MCP servers. A workspace can store a private list of named servers, each with a URL and optional bearer token, in the `mcp_servers` credential slot. The agent does not have a fixed list of those external tools ahead of time. Instead, it first asks a server what tools it offers, then calls one by exact name with JSON arguments.

The file exposes two tools to the agent. `list_mcp_tools` is the “menu”: it returns a short catalog first, with each tool’s name, summary, parameter names, required fields, and whether it is meant to be safe to repeat. If the agent wants to use a tool, it can ask again for the full input schema for selected tools. This two-step flow matters because a whole server catalog can be too large to fit safely in one tool result.

`call_mcp_tool` is the “place the order” step. It checks that the outgoing arguments are not bigger than 1 MiB, sends the request over FastMCP’s streamable HTTP client, and checks that the returned text is not bigger than 1 MiB. External MCP output is treated as untrusted, because the remote server controls it. If the server reports an error, the result is passed back as an error rather than hidden.

#### Function details

##### `McpServer._http_url`  (lines 77–80)

```
def _http_url(cls, value: str) -> str
```

**Purpose**: This validates that a configured MCP server URL starts with `http://` or `https://`. It prevents the extension from trying to call unsupported or surprising kinds of addresses.

**Data flow**: It receives the URL string from the workspace configuration. It checks the string against the allowed web URL pattern. If it matches, the same URL is kept; if not, validation fails with a clear error.

**Call relations**: This runs automatically when an `McpServer` configuration object is built. That matters before `_server` returns a server to `_list_mcp_tools` or `_call_mcp_tool`, so later network calls only use validated HTTP-style endpoints.


##### `mcp_client`  (lines 107–113)

```
def mcp_client(server: McpServer) -> Client
```

**Purpose**: This builds the actual FastMCP client used to talk to one MCP server. It also adds the server’s bearer token, if the workspace configured one.

**Data flow**: It receives a validated `McpServer` with a URL and optional auth token. It turns the token into an `authorization` header when present, creates a streamable HTTP transport for that URL, and wraps it in a FastMCP client with a timeout. The result is a ready-to-use client object.

**Call relations**: _list_mcp_tools` and `_call_mcp_tool` both ask this function for a client after `_server` has found the named server. From there, FastMCP takes over the protocol details, such as the MCP startup handshake and paged tool listing.

*Call graph*: called by 2 (_call_mcp_tool, _list_mcp_tools); 2 external calls (Client, StreamableHttpTransport).


##### `_server`  (lines 116–128)

```
async def _server(ctx: ToolContext, name: str) -> McpServer
```

**Purpose**: This looks up one named MCP server from the workspace’s private credentials. It is the gatekeeper that turns a simple name like `docs` into the URL and token needed to contact that server.

**Data flow**: It receives the current tool context and a server name. It reads the `mcp_servers` credential value from the extension context, parses and validates it as JSON, then searches for the requested name. It returns the matching `McpServer`, or raises an error if the extension context is missing, the credential is invalid, or the name is unknown.

**Call relations**: _list_mcp_tools` and `_call_mcp_tool` both start by calling this function. This keeps credential reading in one place, so the rest of the file can work with a validated server object instead of raw secret JSON.

*Call graph*: called by 2 (_call_mcp_tool, _list_mcp_tools).


##### `_list_mcp_tools`  (lines 131–152)

```
async def _list_mcp_tools(ctx: ToolContext, args: ListMcpToolsInput) -> ToolResult
```

**Purpose**: This is the handler behind the `list_mcp_tools` tool. It lets the agent browse what an MCP server offers, and then request full schemas only for the specific tools it plans to use.

**Data flow**: It receives a tool context plus input containing a server name and optional tool names. It resolves the server, connects to it, and asks for the server’s tool list. If no tool names were requested, it returns a compact catalog. If tool names were requested, it checks they exist and returns full schema entries for just those tools, with size protection for multi-schema results.

**Call relations**: This is one of the two tool handlers registered by `manifest`. It relies on `_server` for credentials, `mcp_client` for the network connection, `_catalog_entry` for compact listings, `_schema_entry` for detailed listings, `_bounded_schemas` for oversized schema protection, and `_json_result` to return JSON text to the agent.

*Call graph*: calls 6 internal fn (_bounded_schemas, _catalog_entry, _json_result, _schema_entry, _server, mcp_client).


##### `_idempotent`  (lines 155–157)

```
def _idempotent(tool: McpTool) -> bool
```

**Purpose**: This reads whether an MCP tool says it is idempotent, meaning it should be safe to repeat without changing the outcome more than once. For example, reading a document is usually idempotent; charging a credit card is not.

**Data flow**: It receives an MCP tool object. It looks at the tool’s annotations, and specifically the `idempotentHint` flag if annotations exist. It returns a simple true or false value.

**Call relations**: _catalog_entry` and `_schema_entry` call this so both short and detailed tool descriptions carry the same safety hint. The hint helps the agent reason about whether retrying a tool is likely to be safe.

*Call graph*: called by 2 (_catalog_entry, _schema_entry).


##### `_catalog_entry`  (lines 160–176)

```
def _catalog_entry(tool: McpTool) -> JsonValue
```

**Purpose**: This turns one full MCP tool definition into a small catalog item. It gives the agent enough information to choose a tool without flooding the result with the full schema.

**Data flow**: It receives a tool object from the MCP server. It reads the tool name, description, input schema properties, required fields, and idempotency hint. It produces a compact JSON-like dictionary with the name, short summary, parameter names, required parameter names, and idempotent flag.

**Call relations**: _list_mcp_tools` calls this when the agent asks to browse a server without requesting full schemas. It delegates summary trimming to `_summary` and safety flag reading to `_idempotent`.

*Call graph*: calls 2 internal fn (_idempotent, _summary); called by 1 (_list_mcp_tools).


##### `_summary`  (lines 179–185)

```
def _summary(description: str) -> str
```

**Purpose**: This makes a short, readable summary from a longer MCP tool description. It is used so the browsing catalog stays useful and small.

**Data flow**: It receives a description string. It takes the first line, then keeps only the first sentence-like part, and finally limits it to the configured maximum number of characters. It returns that shortened text.

**Call relations**: _catalog_entry` calls this while building compact catalog entries. That keeps `list_mcp_tools` from returning long docstrings when the agent only needs a quick menu.

*Call graph*: called by 1 (_catalog_entry).


##### `_schema_entry`  (lines 188–194)

```
def _schema_entry(tool: McpTool) -> JsonValue
```

**Purpose**: This turns one MCP tool into a detailed entry that includes its full input schema. The agent needs this before calling a tool so it can use the exact parameter names and expected shapes.

**Data flow**: It receives an MCP tool object. It copies out the name, full description, input schema, and idempotent flag. It returns those as a JSON-like dictionary.

**Call relations**: _list_mcp_tools` calls this when the agent asks for full schemas for selected tools. It uses `_idempotent` so detailed schema responses preserve the same repeat-safety hint as the compact catalog.

*Call graph*: calls 1 internal fn (_idempotent); called by 1 (_list_mcp_tools).


##### `_call_mcp_tool`  (lines 197–209)

```
async def _call_mcp_tool(ctx: ToolContext, args: CallMcpToolInput) -> ToolResult
```

**Purpose**: This is the handler behind the `call_mcp_tool` tool. It invokes a named tool on a named MCP server and returns the server’s answer in the agent’s expected tool-result format.

**Data flow**: It receives a tool context plus input containing a server name, tool name, and JSON arguments. It resolves the server, checks that the serialized arguments are no larger than 1 MiB, calls the MCP tool, and then converts the result. Server-side errors become error tool results; structured dictionary output becomes JSON; otherwise text content is joined and returned as JSON.

**Call relations**: This is registered by `manifest` as the actual MCP tool-calling entry. It uses `_server` for configuration lookup, `mcp_client` for the connection, `_joined_text` to combine text blocks, `_bounded` to enforce response size limits, and `_json_result` to package successful JSON output.

*Call graph*: calls 5 internal fn (_bounded, _joined_text, _json_result, _server, mcp_client); 4 external calls (__init__, __init__, __init__, dumps).


##### `_joined_text`  (lines 212–213)

```
def _joined_text(content: list[object]) -> str
```

**Purpose**: This collects text blocks from an MCP response into one plain string. It ignores non-text blocks because this extension returns text or JSON text to the agent.

**Data flow**: It receives a list of content blocks from an MCP result. It keeps only blocks that are MCP text content, takes their text fields, and joins them with newline characters. It returns the combined string.

**Call relations**: _call_mcp_tool` uses this when an MCP call returns an error or when it returns normal content without structured JSON. It is the small adapter between MCP’s block-based response shape and the agent’s text-based tool result.

*Call graph*: called by 1 (_call_mcp_tool).


##### `_bounded`  (lines 216–219)

```
def _bounded(text: str) -> str
```

**Purpose**: This enforces the maximum allowed size for returned MCP content. It fails loudly instead of silently cutting off data, because a partial tool result could mislead the agent.

**Data flow**: It receives a text string. It measures the string as encoded bytes and compares it with the 1 MiB response limit. If it fits, it returns the same text; if not, it raises an `McpError`.

**Call relations**: _call_mcp_tool` uses this for error text from remote tools, and `_json_result` uses it for JSON responses. This makes size checking a shared rule for all outgoing tool results from this extension.

*Call graph*: called by 2 (_call_mcp_tool, _json_result); 1 external calls (__init__).


##### `_bounded_schemas`  (lines 222–239)

```
def _bounded_schemas(payload: dict[str, JsonValue], tools: int) -> ToolResult
```

**Purpose**: This protects the agent from receiving too many full tool schemas at once. It encourages the agent to ask for only the few schemas it is about to use.

**Data flow**: It receives a JSON-like payload and the number of tool schemas inside it. If more than one schema was requested and the rendered payload is larger than the listing limit, it raises a clear error asking for fewer tools. Otherwise it passes the payload to `_json_result` and returns the packaged tool result.

**Call relations**: _list_mcp_tools` calls this only for detailed schema responses. It sits between `_schema_entry` and `_json_result`, deciding whether the requested group of schemas is small enough to return directly.

*Call graph*: calls 1 internal fn (_json_result); called by 1 (_list_mcp_tools); 1 external calls (dumps).


##### `_json_result`  (lines 242–243)

```
def _json_result(payload: dict[str, JsonValue]) -> ToolResult
```

**Purpose**: This packages a dictionary as a tool result containing JSON text. It is the common exit path for successful list and call responses.

**Data flow**: It receives a JSON-like dictionary. It serializes it to a JSON string, checks the string with `_bounded`, wraps it in a text content object, and returns a tool result. The visible output is a tool result whose content is JSON text.

**Call relations**: _list_mcp_tools`, `_bounded_schemas`, and `_call_mcp_tool` all use this when they need to return structured information to the agent. By sharing it, they all get the same JSON formatting and response-size protection.

*Call graph*: calls 1 internal fn (_bounded); called by 3 (_bounded_schemas, _call_mcp_tool, _list_mcp_tools); 3 external calls (__init__, __init__, dumps).


##### `manifest`  (lines 246–277)

```
def manifest() -> Manifest
```

**Purpose**: This declares the extension to the host system: its name, version, tools, input models, handlers, and credential needs. Without this, the agent would not know that `list_mcp_tools` and `call_mcp_tool` exist.

**Data flow**: It takes no input. It builds a manifest containing two tool definitions and one credential slot definition for `mcp_servers`. The returned manifest tells the host how to validate tool inputs, which functions to call, and what secret configuration the workspace must provide.

**Call relations**: This is the file’s registration point. It connects user-visible tool names to `_list_mcp_tools` and `_call_mcp_tool`, and it tells the wider extension system that the output should be treated as untrusted because it comes from external MCP servers.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Specialized service adapters
Standalone adapters add credentialed access to Perplexity web retrieval and guided Slack workspace integration.

### `extensions/perplexity/ufo_ext_perplexity.py`

`io_transport` · `extension registration and search/fetch request handling`

This extension is the bridge between UFO’s search interface and Perplexity’s hosted search service. Without it, the system could not use Perplexity as one of its search backends, and requests for Perplexity-based search or page extraction would have nowhere to go.

The file defines a `PerplexitySearchProvider`, which has two main jobs. First, it turns the project’s normal search request into the JSON shape Perplexity expects. Second, it sends that request over HTTPS, checks that the reply looks right, and converts the reply back into the project’s own result objects.

For normal search, it accepts a query, optional result limits, domains, dates, and search “verticals” such as academic, image, video, shopping, or people. A vertical is just a category of search. For page fetching, it asks Perplexity to search only within the page’s domain, then checks that the returned result really matches the requested URL. This check uses a “canonical” form of the URL, meaning small differences like a trailing slash or `.html` suffix are ignored.

The file is careful about limits: very long queries, URLs, prompts, or bad result counts are rejected before calling the API. It also wraps bad HTTP responses, invalid JSON, and unexpected response shapes in a clear `PerplexityError`.

#### Function details

##### `PerplexitySearchProvider.search`  (lines 72–85)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: Runs a web search through Perplexity and returns the results in UFO’s standard search-result format. Someone uses this when they want Perplexity to answer a normal search query.

**Data flow**: It receives a `SearchQuery`, which contains the search text and optional filters such as result count, dates, domains, or category. It turns that query into a Perplexity request body, sends it to Perplexity, validates the response, then converts each returned item into a `SearchHit`. The output is a `SearchResults` object containing those hits.

**Call relations**: This is one of the public actions of the provider. It relies on `_search_body` to prepare the request, `_post` to talk to Perplexity over the network, and `_response` to make sure the reply has the expected shape before building the final search results.

*Call graph*: calls 3 internal fn (_post, _response, _search_body); 2 external calls (__init__, __init__).


##### `PerplexitySearchProvider.fetch`  (lines 87–131)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: Extracts text for one specific web page by using Perplexity’s search API in a tightly constrained way. It is used when the system wants content from an exact URL, not just a list of search results.

**Data flow**: It receives a `FetchRequest` with a URL, and possibly an extraction prompt and a maximum character count. It checks that the URL, prompt, and size limits are safe, extracts the domain from the URL, asks Perplexity to search only that domain, then looks for a returned result matching the requested page. If it finds one, it trims the text to the requested limit and returns a `FetchedPage`; if not, it raises a clear error.

**Call relations**: This is the provider’s page-fetching path. It uses `_post` for the API call and `_response` for validation. It uses `_canonical_page` to compare the requested URL with Perplexity’s returned URLs in a forgiving but controlled way, so minor URL formatting differences do not cause false mismatches.

*Call graph*: calls 3 internal fn (_post, _response, _canonical_page); 3 external calls (__init__, __init__, urlsplit).


##### `PerplexitySearchProvider._search_body`  (lines 134–159)

```
def _search_body(query: SearchQuery) -> dict[str, Json]
```

**Purpose**: Builds the JSON request body that Perplexity expects for a search. It also enforces the provider’s search limits before any network call is made.

**Data flow**: It receives a `SearchQuery`. It checks the requested result count, adds a plain-language category hint for certain verticals, applies result and token limits, and includes optional filters such as allowed domains and published-date bounds. It returns a dictionary ready to be sent as JSON to Perplexity.

**Call relations**: This is a helper used by `PerplexitySearchProvider.search` before the API request is sent. When date filters are present, it hands each date to `_api_date` so Perplexity receives dates in the format it expects.

*Call graph*: calls 1 internal fn (_api_date); called by 1 (search); 1 external calls (__init__).


##### `PerplexitySearchProvider._response`  (lines 162–166)

```
def _response(payload: object) -> _PerplexitySearchResponse
```

**Purpose**: Checks that Perplexity’s response has the expected structure before the rest of the code trusts it. This protects the provider from confusing or incomplete API replies.

**Data flow**: It receives raw decoded JSON from Perplexity, represented as a general Python object. It validates that the object contains a `results` list with items that have fields such as URL, title, snippet, and optional date. It returns a typed response object, or raises `PerplexityError` if the shape is wrong.

**Call relations**: Both `search` and `fetch` call this after `_post` returns data. It is the gatekeeper between the outside API and the internal code that builds `SearchResults` or `FetchedPage` objects.

*Call graph*: called by 2 (fetch, search); 1 external calls (__init__).


##### `PerplexitySearchProvider._post`  (lines 168–188)

```
async def _post(self, body: dict[str, Json]) -> object
```

**Purpose**: Sends one request to Perplexity’s `/search` endpoint and returns the decoded JSON reply. It is the single place in this file that actually performs the HTTP network call.

**Data flow**: It receives a JSON-ready request body. It asks the credential store for the Perplexity API key, opens an asynchronous HTTP client, sends the body to `https://api.perplexity.ai/search` with an authorization header, and reads the response. If Perplexity reports an error or returns non-JSON text, it raises `PerplexityError`; otherwise it returns the decoded JSON data.

**Call relations**: `search` and `fetch` both depend on this helper whenever they need to contact Perplexity. It centralizes the API host, timeout, authentication header, error truncation, and JSON parsing so those details are not repeated elsewhere.

*Call graph*: called by 2 (fetch, search); 2 external calls (__init__, AsyncClient).


##### `_api_date`  (lines 191–192)

```
def _api_date(value: date) -> str
```

**Purpose**: Formats a Python date into the month/day/year string format used by Perplexity’s date filters.

**Data flow**: It receives a `date` value. It converts that date into a string like `03/15/2024`. The output is used directly in the JSON request sent to Perplexity.

**Call relations**: `PerplexitySearchProvider._search_body` calls this when a search query includes start or end published-date filters. It is a small adapter between the project’s date type and Perplexity’s expected API format.

*Call graph*: called by 1 (_search_body); 1 external calls (strftime).


##### `_canonical_page`  (lines 195–202)

```
def _canonical_page(value: str) -> tuple[str | None, str, str]
```

**Purpose**: Creates a simplified version of a URL so two versions of the same page can be compared fairly. It helps the fetch path confirm that Perplexity returned the exact page the user asked for.

**Data flow**: It receives a URL string. It splits the URL into parts, removes a trailing slash from the path, and also removes common page suffixes such as `.html`, `.htm`, or `.txt`. It returns a tuple containing the hostname, simplified path, and query string.

**Call relations**: `PerplexitySearchProvider.fetch` uses this for both the requested URL and each returned result URL. That lets fetch accept harmless differences in page spelling while still rejecting results from the wrong page.

*Call graph*: called by 1 (fetch); 1 external calls (urlsplit).


##### `manifest`  (lines 205–225)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the host system so it can be discovered and used. It declares the needed Perplexity API-key credential and registers the Perplexity search provider.

**Data flow**: It takes no input. It builds a `Manifest` containing the extension name, version, one credential slot for the Perplexity API key, and one search provider specification that knows how to create a `PerplexitySearchProvider`. The output is that manifest object.

**Call relations**: The host calls this during extension discovery or startup. The returned manifest tells the host what secret to ask for and how to construct this provider when a Perplexity-backed search service is needed.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/slack/ufo_ext_slack/tools.py`

`orchestration` · `Slack setup and later Slack conversation lookup`

Slack setup has several moving parts: app credentials, a bot token, a signing secret, a public URL Slack can call, and proof that Slack has actually reached this UFO deploy. This file packages those steps as tools attached to the Slack surface, so an admin can ask the agent to connect Slack instead of manually stitching everything together.

There are two installation paths. The preferred path is OAuth, which is the familiar “Add to Slack” button. If this UFO deploy has its own Slack app configured, the tool creates a short-lived install link for an admin. The other path is “manifest”, where the user creates their own Slack app from a ready-made YAML manifest, then privately supplies the bot token and signing secret. Both paths end at the same place: a stored Slack identity for the connected workspace and a bot token UFO can use.

The file also checks connection progress. It reports states like not configured, not installed, pending, and connected. “Pending” means the identity is known, but Slack has not yet sent a verified request to this deploy. “Connected” means Slack reached the public endpoint and the request signature matched the stored signing secret.

Finally, it includes a runtime search tool for Slack conversations, so the agent can find channels or direct messages by names, topics, purposes, or people rather than needing exact Slack IDs.

#### Function details

##### `_events_url`  (lines 144–145)

```
def _events_url(public_base_url: str) -> str
```

**Purpose**: Builds the public web address Slack should call when it sends events to this UFO deploy. This keeps the Slack callback path consistent everywhere the tools need it.

**Data flow**: It receives the deploy’s public base URL, removes any trailing slash, adds the fixed Slack surface path, and returns the full events URL as text.

**Call relations**: The Slack connection flow uses this when reporting where Slack should send events, and the manifest generator uses it when filling in the Slack app manifest. It is a small shared helper so both paths point Slack at the same endpoint.

*Call graph*: called by 2 (slack_connect_handler, slack_manifest_handler).


##### `_state`  (lines 148–150)

```
def _state(state: str, hint: str, events_url: str | None, **extra: object) -> ToolResult
```

**Purpose**: Creates a standard tool response that says where Slack setup currently stands. It gives the caller a machine-readable JSON message with a state, a human hint, and optional extra details.

**Data flow**: It receives a state name, an explanatory hint, an events URL, and any extra fields. It bundles them into a dictionary, converts that dictionary to JSON text, wraps the text in tool content, and returns a ToolResult.

**Call relations**: The main connection handler and its two install helpers call this whenever they need to report progress or explain what the user should do next. It is the common “status card” builder for the Slack setup flow.

*Call graph*: called by 3 (_derive_manifest_identity, _oauth_link, slack_connect_handler); 3 external calls (__init__, __init__, dumps).


##### `slack_connect_handler`  (lines 153–194)

```
async def slack_connect_handler(ctx: ToolContext, args: SlackConnectInput) -> ToolResult
```

**Purpose**: Runs the main Slack connection workflow. Someone can call it repeatedly before, during, and after setup, and it will report the current state or move setup forward when possible.

**Data flow**: It reads the requested install method, the public base URL, stored Slack credentials, and any saved Slack identity. If identity is missing, it either starts the OAuth install link flow or tries the manifest-based identity check. Once identity exists, it binds this Slack team to the UFO workspace, checks whether Slack has sent a verified request, and returns a JSON status such as pending or connected.

**Call relations**: This is the handler behind the slack_connect tool. It calls _events_url to compute Slack’s callback address, _oauth_link for the one-click install path, _derive_manifest_identity for the bring-your-own-app path, _verified to decide whether Slack has actually reached the deploy, and _state to return clear setup status messages.

*Call graph*: calls 5 internal fn (_derive_manifest_identity, _events_url, _oauth_link, _state, _verified); 2 external calls (read_identity, slack_installation_id).


##### `_oauth_link`  (lines 197–227)

```
async def _oauth_link(ctx: ToolContext, events_url: str | None) -> ToolResult
```

**Purpose**: Creates an “Add to Slack” installation link for admins when this deploy has its own Slack app configured. If OAuth cannot be used, it explains which setup path to use instead.

**Data flow**: It checks environment variables for the Slack client ID and secret, checks whether the speaker is an admin, and checks that a public base URL exists. If all requirements are met, it starts a sealed credential authorization handoff, builds the Slack authorization URL, and returns it inside a setup status result.

**Call relations**: slack_connect_handler calls this when the user chooses or defaults to the OAuth install method and no Slack identity is stored yet. It hands off to the core credential authorization system to protect the install payload, then uses Slack URL helpers to form the final link.

*Call graph*: calls 3 internal fn (begin_credential_authorization, speaker_is_admin, _state); called by 1 (slack_connect_handler); 3 external calls (slack_authorize_url, slack_client_id, slack_oauth_redirect_uri).


##### `_derive_manifest_identity`  (lines 230–264)

```
async def _derive_manifest_identity(ctx: ToolContext, events_url: str | None) -> SlackIdentity | ToolResult
```

**Purpose**: Completes the bring-your-own-Slack-app setup path. It waits until the needed secrets are stored, then asks Slack to prove what workspace and bot those credentials belong to.

**Data flow**: It checks whether the bot token and signing secret credential slots are filled. If anything is missing, it returns a not_configured status naming the missing slots. If the secrets exist, it confirms the speaker is an admin, uses the bot token to resolve the Slack identity through Slack’s auth.test-style check, stores or returns that identity, and turns Slack token errors into user-friendly setup messages.

**Call relations**: slack_connect_handler calls this when the manifest method is used and no identity has been resolved yet. It uses _state for progress messages and _token_diagnosis to explain rejected token errors in plain language.

*Call graph*: calls 3 internal fn (speaker_is_admin, _state, _token_diagnosis); called by 1 (slack_connect_handler); 1 external calls (__init__).


##### `_verified`  (lines 267–286)

```
async def _verified(ctx: ToolContext) -> bool
```

**Purpose**: Checks whether Slack has successfully called this deploy using the currently stored signing secret. This is what separates “we know the workspace” from “Slack can actually reach us securely.”

**Data flow**: It looks for a saved URL-verification marker in blob storage. If the marker is missing, unreadable, or malformed, it returns false. It then reads the current signing secret, fingerprints it, and compares that fingerprint with the marker. A match returns true; anything else returns false.

**Call relations**: slack_connect_handler calls this after identity is known. The Slack surface writes the marker when it receives a signature-verified Slack request; this helper reads that marker later to decide whether setup should be shown as pending or connected.

*Call graph*: called by 1 (slack_connect_handler); 2 external calls (loads, signing_secret_fingerprint).


##### `slack_manifest_handler`  (lines 289–304)

```
async def slack_manifest_handler(ctx: ToolContext, args: SlackManifestInput) -> ToolResult
```

**Purpose**: Produces a ready-to-paste Slack app manifest for this deploy. This helps users create a Slack app with the exact permissions, events, and callback URLs UFO expects.

**Data flow**: It receives a requested bot display name, checks that the name uses allowed plain characters and length, reads the deploy’s public base URL, builds the Slack events and interactivity URLs, fills those values into the manifest template, and returns the YAML text as a tool result.

**Call relations**: This is the handler behind the slack_app_manifest tool. It uses _events_url so the manifest points at the same Slack endpoint as the connection flow, and it returns the manifest directly for the agent or user to copy into Slack’s app creation page.

*Call graph*: calls 1 internal fn (_events_url); 3 external calls (__init__, __init__, match).


##### `slack_channels_handler`  (lines 307–330)

```
async def slack_channels_handler(ctx: ToolContext, args: SlackChannelsInput) -> ToolResult
```

**Purpose**: Searches the connected Slack workspace for conversations the bot can see. This lets the agent find a channel or direct message by human clues, such as a channel name or the people in a DM.

**Data flow**: It reads the stored Slack bot token, reads the saved Slack identity, and uses the query text to search Slack conversations. It converts the found conversations into JSON, includes whether the result was truncated, and returns that JSON as untrusted content because names, topics, and purposes come from Slack users.

**Call relations**: This is the handler behind the slack_channels tool. It relies on read_identity to confirm Slack is connected, then hands the actual paging and matching work to SlackConversationSearch before packaging the result for the caller.

*Call graph*: 5 external calls (__init__, __init__, __init__, dumps, read_identity).


##### `_token_diagnosis`  (lines 333–339)

```
def _token_diagnosis(error: str) -> str
```

**Purpose**: Turns Slack token error codes into messages a person can act on. It is mainly used to explain why the manifest setup path could not prove the bot identity.

**Data flow**: It receives Slack’s error code as text. If the code is one of the common “token is bad or unusable” errors, it returns instructions to re-copy and re-enter the bot token. Otherwise, it returns a general auth.test failure message with the error included.

**Call relations**: _derive_manifest_identity calls this when Slack rejects the token while trying to resolve identity. This keeps the setup flow’s error messages helpful instead of exposing only terse Slack API codes.

*Call graph*: called by 1 (_derive_manifest_identity).
