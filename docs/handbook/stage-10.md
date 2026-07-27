# Tool dispatch and sandboxed workspace actions  `stage-10`

This stage is the action arm of the system during the main work loop. When the model asks to do something, these pieces decide which tool to use, what it is allowed to touch, and where it may run. The tool package marker just makes the tool code importable. The context file is the permission envelope: it controls access to files, browsers, credentials, connectors, subagents, cleanup tasks, and returned results. The built-in tools provide everyday actions such as running commands, editing workspace files, sharing files, asking the user, loading skills, and connecting accounts.

The sandbox files provide the safe workspace. One defines the doorway into that workspace, one supports a simple local version, and Docker or E2B extensions can run the same work inside containers or cloud sandboxes. The coding package marker only enables imports.

Extra extensions add specialized tools: REPL runs remembered Python or JavaScript snippets, research searches and fetches pages, sites builds and serves small web apps, and todos keeps a conversation checklist. The document-skill scripts handle office files and PDFs. The browser automation pieces control Chrome for web tasks, clicks, downloads, and page reading.

## Sub-stages

- [Document, office, PDF, spreadsheet, and review skill scripts](stage-10.1.md) `stage-10.1` — 23 files
- [Browser automation during tool execution](stage-10.2.md) `stage-10.2` — 25 files

## Files in this stage

### Tool runtime and built-ins
These files define the tool package, the permission envelope every tool runs inside, and the built-in actions available to the agent.

### `core/src/ufo/tools/__init__.py`

`other` · `import time`

This is a small package marker file. In Python, an `__init__.py` file tells Python that a folder should be treated as an importable package. Here, it does not define any code of its own. Its main job is to give readers a short signpost: this package is where the project keeps the pieces related to “tools.” In this context, tools are likely callable capabilities the system can look up and run, much like items in a toolbox. The docstring names three important ideas found elsewhere in the package: a registry, which is like a catalog of available tools; a handler context, which is the information a tool needs while it runs; and the built-in tool set, which are the tools provided by the project out of the box. Without this file, older Python tooling or some import setups might not recognize the folder as a package, and newcomers would lose this quick orientation point.


### `core/src/ufo/tools/context.py`

`domain_logic` · `tool execution`

A tool in this system should not be able to reach everything directly. This file gives each tool a carefully scoped “work badge” called ToolContext. The context carries only the powers that tool should have for the current turn: a sandbox for files and shell commands, a blob store for artifacts, the current turn and agent, optional browser and search providers, connector access, credential helpers, subagent controls, and cleanup hooks.

The file also defines the shapes of tool output. A tool can return text or an image, wrapped in ToolResult, and it can mark output as an error or as untrusted. “Untrusted” means the text may have come from a webpage or third party and must be treated as data, not instructions.

Subagents are modeled as callable capabilities. A tool can spawn a child task, wait for it, cancel it, or send it another message, but only through the interfaces provided here.

A key part of the file is authorization. ToolContext checks who is speaking, who the turn acts for, whether that speaker owns the workspace, which credential slots an extension declared, and which connected accounts are available. Without this file, tools would either be too weak to do useful work or too powerful, risking leaks across users, agents, workspaces, or turns.

#### Function details

##### `Spawn.__call__`  (lines 122–128)

```
async def __call__(self, profile: str, payload: dict[str, Any], background: bool=False, dedup_key: str | None=None) -> SpawnResult
```

**Purpose**: Describes the operation for starting a named subagent task. A tool uses this when it wants to delegate a smaller job to another agent profile instead of doing everything itself.

**Data flow**: It receives the subagent profile name, an input payload, and options saying whether to run in the background and whether to reuse a previous child turn through a deduplication key. An implementation validates the payload, starts or reconnects to a child turn, and returns the child turn id plus any finished typed output.

**Call relations**: This is a protocol method, meaning this file defines the promise but another part of the system supplies the actual implementation. ToolContext carries a Spawn object so tool handlers can ask for child work without knowing how child turns are created.


##### `SubagentControl.wait`  (lines 137–137)

```
async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]
```

**Purpose**: Describes how a tool waits for one or more background subagents to finish. It is useful after a tool has started several child tasks and later needs their final answers.

**Data flow**: It receives child turn ids. An implementation waits until those turns reach an ending state, then returns a status record for each one, including the final text and whether that output is untrusted.

**Call relations**: This is part of the SubagentControl protocol attached to ToolContext. Background-subagent lifecycle tools call this kind of operation through the context, while the concrete subagent workflow provides the real waiting behavior.


##### `SubagentControl.cancel`  (lines 139–139)

```
async def cancel(self, turn_id: UUID) -> SubagentStatus
```

**Purpose**: Describes how a tool asks a running background subagent to stop. This matters when delegated work is no longer needed or should not continue spending resources.

**Data flow**: It receives one child turn id. An implementation requests cancellation for that child turn and returns its resulting terminal status and message.

**Call relations**: This method is a protocol promise, not the implementation. ToolContext exposes it so tools can control child turns without directly reaching into the turn-running machinery.


##### `SubagentControl.message`  (lines 141–141)

```
async def message(self, turn_id: UUID, text: str) -> SubagentStatus
```

**Purpose**: Describes how a tool sends a follow-up message to an existing background subagent. This lets a parent task continue a child task with new instructions or information.

**Data flow**: It receives a child turn id and a text message. An implementation submits that message as the child’s next turn and returns the child’s resulting status information.

**Call relations**: This sits on the SubagentControl interface carried by ToolContext. The context gives tools this narrow doorway, while the actual subagent system decides how the message becomes another child turn.


##### `TurnCleanup.register`  (lines 155–156)

```
def register(self, aclose: Callable[[], Awaitable[None]]) -> None
```

**Purpose**: Adds a cleanup action that should run when the current turn ends. Tools use this after opening something temporary, such as a browser connection, so it does not leak after the turn is over.

**Data flow**: It receives an async close function and stores it in the cleanup list. Nothing is returned; the registry is changed so the close function can be called later.

**Call relations**: Tool code registers close functions here as it creates per-turn resources. Later, the turn loop calls TurnCleanup.drain to run those registered close functions.


##### `TurnCleanup.drain`  (lines 158–164)

```
async def drain(self) -> None
```

**Purpose**: Runs all registered cleanup actions for a turn. It is the final sweep that closes temporary resources even if the turn ended with an error or cancellation.

**Data flow**: It reads the stored cleanup functions, removes them one by one in reverse order, and awaits each close operation. If one cleanup fails, it logs the failure and continues with the rest instead of stopping.

**Call relations**: This is called at the end of a turn by the surrounding runtime. When a close function throws an exception, it hands the failure to the observability logger so the problem is visible without preventing other cleanup work.

*Call graph*: 1 external calls (log).


##### `ToolContext.acting_member_id`  (lines 194–207)

```
def acting_member_id(self) -> UUID | None
```

**Purpose**: Figures out which workspace member this turn is acting for when using private resources. Usually this is the speaking member, but for scheduled or delegated work it can be the member carried forward as 'on behalf of'.

**Data flow**: It reads speaker_member_id first. If there is a speaker, it returns that id; otherwise it returns on_behalf_of_member_id, which may also be missing.

**Call relations**: Other permission checks in this file use this property to decide which private connector grants count. It keeps speakerless scheduled jobs and subagent chains connected to the member they represent.


##### `ToolContext.speaker_is_owner`  (lines 209–218)

```
async def speaker_is_owner(self) -> bool
```

**Purpose**: Checks whether the current speaking member is the workspace owner. Some actions, such as authorizing shared credentials, are only allowed for the owner.

**Data flow**: If there is no speaking member, it returns false. Otherwise it opens a workspace database transaction, looks up the owner member for the turn’s workspace, and compares that owner id with the speaker id.

**Call relations**: Credential authorization inside this file calls this before allowing secret storage. Other object and agent operations also call it when they need to gate owner-only actions; it relies on workspace_tx for database access and owner_member_id for the owner lookup.

*Call graph*: called by 16 (apply, delete, apply, delete, get, list, status, request_credentials_handler, _credential_authorization, connect_github (+6 more)); 2 external calls (workspace_tx, owner_member_id).


##### `ToolContext.begin_credential_authorization`  (lines 220–222)

```
async def begin_credential_authorization(self, slot: str, payload: str) -> str
```

**Purpose**: Starts an authorization flow for storing or using an extension credential. For example, an extension may need to ask the workspace owner to approve a credential slot.

**Data flow**: It receives a credential slot name and payload. It first runs the shared credential-authorization checks, then asks the CredentialRequests service to create a sealed authorization request and returns that sealed string.

**Call relations**: Extension code such as GitHub connection and Slack OAuth helpers call this when they need to begin credential setup. It delegates all permission checks to ToolContext._credential_authorization before creating the request.

*Call graph*: calls 1 internal fn (_credential_authorization); called by 2 (connect_github, _oauth_link).


##### `ToolContext.open_credential_authorization`  (lines 224–226)

```
async def open_credential_authorization(self, slot: str, sealed: str) -> str
```

**Purpose**: Opens and verifies an existing sealed credential authorization request. This lets the system confirm that a credential authorization belongs to the right workspace, member, and slot.

**Data flow**: It receives a slot name and sealed authorization string. It performs the shared credential-authorization checks, then asks CredentialRequests to open the sealed value and returns the decoded authorization payload.

**Call relations**: This is the read/check counterpart to beginning authorization. It uses ToolContext._credential_authorization so the same speaker, audience, extension, and owner rules apply consistently.

*Call graph*: calls 1 internal fn (_credential_authorization).


##### `ToolContext.fulfill_credential_authorization`  (lines 228–233)

```
async def fulfill_credential_authorization(self, slot: str, sealed: str, plaintext: str) -> None
```

**Purpose**: Completes a credential authorization by storing the provided secret. It is used after the authorization has been checked and the plaintext credential is ready to save.

**Data flow**: It receives a slot name, a sealed authorization string, and the plaintext secret. It verifies the authorization, opens the sealed request to confirm it matches, then writes the plaintext credential into the current workspace store.

**Call relations**: This function shares the same gatekeeping path as the other credential authorization methods. After ToolContext._credential_authorization approves the operation, it uses the current workspace object to save the credential.

*Call graph*: calls 1 internal fn (_credential_authorization); 1 external calls (ws_current).


##### `ToolContext._credential_authorization`  (lines 235–246)

```
async def _credential_authorization(self, slot: str) -> tuple[CredentialRequests, UUID]
```

**Purpose**: Centralizes all checks required before a tool can create, open, or fulfill a credential authorization. It protects secret storage from being used by the wrong speaker, audience, extension, or workspace role.

**Data flow**: It reads the context fields for speaker, audience, extension credential declarations, credential-request service, and workspace ownership. If any requirement fails, it raises an error; if all pass, it returns the CredentialRequests service and the authorized member id.

**Call relations**: The three public credential methods call this helper so they all enforce the same rules. It calls ToolContext.speaker_is_owner because only the workspace owner is allowed to authorize these credential slots.

*Call graph*: calls 1 internal fn (speaker_is_owner); called by 3 (begin_credential_authorization, fulfill_credential_authorization, open_credential_authorization).


##### `ToolContext.connector_account`  (lines 248–275)

```
async def connector_account(self, provider: str, account_id: str | None=None) -> str
```

**Purpose**: Chooses one connected account id that a connector tool may use for a provider such as an external service. It prevents tools from accidentally using another agent’s or member’s account.

**Data flow**: It receives a provider name and optionally a specific account id. It gathers private and shared account tiers, verifies the requested account if one was supplied, or otherwise chooses the only available account from the preferred tier; it raises a clear error if there are none or too many.

**Call relations**: Connector extensions call this before invoking an external tool through the broker. It relies on ToolContext._connector_account_tiers to separate accounts into private and shared choices, then returns exactly one safe account id.

*Call graph*: calls 1 internal fn (_connector_account_tiers); called by 2 (call_external_tool, _connector_execute).


##### `ToolContext.connector_accounts`  (lines 277–285)

```
async def connector_accounts(self, provider: str) -> tuple[str, ...]
```

**Purpose**: Lists all connected account ids this turn may use for one provider. This is useful when a tool needs to show or resolve among the available accounts instead of choosing one automatically.

**Data flow**: It receives a provider name, gathers the private and shared account tiers, merges them, removes duplicates, sorts them, and returns them as a tuple.

**Call relations**: Source-related extension code calls this when resolving which account is available. It uses the same ToolContext._connector_account_tiers helper as connector_account, so the same permission rules apply.

*Call graph*: calls 1 internal fn (_connector_account_tiers); called by 1 (_resolved_account).


##### `ToolContext._connector_account_tiers`  (lines 287–300)

```
async def _connector_account_tiers(self, provider: str) -> tuple[list[str], list[str]]
```

**Purpose**: Builds the allowed connector-account lists for the current turn, split into private accounts and shared accounts. This is the permission filter that keeps connector access scoped to the current workspace and agent.

**Data flow**: It checks that the grants system is available, then asks it for active grants for this workspace and agent. It filters those grants by provider: private grants must belong to the acting member, while shared grants are available through the agent; it returns both sorted lists.

**Call relations**: ToolContext.connector_account and ToolContext.connector_accounts call this helper before exposing account ids to tools. If no grant system is configured, it raises ConnectUnavailable so callers fail clearly rather than pretending no accounts exist.

*Call graph*: called by 2 (connector_account, connector_accounts); 1 external calls (__init__).


### `core/src/ufo/tools/builtins.py`

`domain_logic` · `request handling / tool execution`

This file is like the agent’s toolbox, plus the rules printed on each tool. It defines what inputs each tool accepts, what the tool does, and how the result is returned to the model. Most file and shell work goes through the sandbox, which is an isolated workspace container. That matters because it keeps the host system from directly reading huge or unsafe files, and it applies the same access rules whether the agent uses a shell command or a file tool.

The file tools are deliberately careful. `read` records which paths the agent has seen. `write` and `edit` refuse to change an existing file unless it was read first, which helps prevent blind edits to unseen content. Searches and file matching also run inside the sandbox and only return bounded results.

Other tools let the agent safely interact with the outside world. `share_file` is the controlled path for sending a workspace file to a user: it streams the file to artifact storage and returns a temporary download link. `ask_user`, `connect_account`, and `request_credentials` do not expose private prompts or secrets in chat; they return structured instructions that the user interface can handle safely. Subagent tools let one turn delegate work, wait for background children, cancel them, or send follow-up messages. At the bottom, all of these handlers are registered as `ToolDef` objects so the rest of the system can offer them to the model.

#### Function details

##### `bash_handler`  (lines 275–285)

```
async def bash_handler(ctx: ToolContext, args: BashInput) -> ToolResult
```

**Purpose**: Runs a shell command inside the secure workspace sandbox and returns the combined output. It is used when the agent needs general command-line work that is not better served by a dedicated file tool.

**Data flow**: It receives a tool context and a command request, including an optional timeout. It caps the timeout, asks the sandbox to run the command, combines standard output and error text, and returns that text. If the command exits unsuccessfully, it marks the result as an error and includes the exit code.

**Call relations**: When the registered `bash` tool is invoked, this handler is the part that actually contacts the sandbox. It wraps the sandbox result into `TextContent` and `ToolResult` so the model can read it in the same format as other tool outputs.

*Call graph*: 2 external calls (__init__, __init__).


##### `_require_str`  (lines 288–291)

```
def _require_str(value: object, field: str) -> str
```

**Purpose**: Checks that a value from a sandbox file-read result is a real, non-empty string. It protects later code from quietly accepting malformed image or PDF data.

**Data flow**: It takes an unknown value and the name of the field being checked. If the value is a non-empty string, it returns it unchanged. If not, it raises an error explaining that the sandbox result was missing that field.

**Call relations**: `read_handler` and `_pdf_result` call this when they need fields such as image media type or encoded image data. It acts as a small gatekeeper before those values are placed into model-visible image content.

*Call graph*: called by 2 (_pdf_result, read_handler).


##### `_pdf_result`  (lines 294–339)

```
def _pdf_result(result: dict[str, object]) -> ToolResult
```

**Purpose**: Turns the sandbox’s PDF or PowerPoint read result into content the model can understand: text plus rendered page or slide images. It keeps document rendering details out of the main `read_handler`.

**Data flow**: It receives a dictionary returned by the sandbox reader. It collects extracted text, page or slide range notes, and any warnings, then adds image blocks for rendered pages if they are present. It returns a `ToolResult` containing text and image content, or raises an error if the sandbox result is malformed or empty.

**Call relations**: `read_handler` hands PDF and PowerPoint results to this helper. This helper then uses `_require_str` to validate image fields before building `ImageContent` and `TextContent` blocks for the final tool result.

*Call graph*: calls 1 internal fn (_require_str); called by 1 (read_handler); 3 external calls (__init__, __init__, __init__).


##### `read_handler`  (lines 342–379)

```
async def read_handler(ctx: ToolContext, args: ReadInput) -> ToolResult
```

**Purpose**: Reads a workspace file through the sandbox and returns an appropriate view of it. It supports text windows, images, PDFs, and PowerPoint slides without pulling whole files into the host process.

**Data flow**: It receives a path and optional offset and limit. It asks the sandbox file tool to read that slice, records the path as seen by this turn, and then formats the response depending on the file type. Text gets line-range footers, images become image content, PDFs and slides are passed to `_pdf_result`, and empty files get a simple empty-file message.

**Call relations**: This is the handler behind the registered `read` tool. It calls `_pdf_result` for paginated visual documents and `_require_str` for image fields, then returns a standard `ToolResult` for the model.

*Call graph*: calls 2 internal fn (_pdf_result, _require_str); 3 external calls (__init__, __init__, __init__).


##### `write_handler`  (lines 382–403)

```
async def write_handler(ctx: ToolContext, args: WriteInput) -> ToolResult
```

**Purpose**: Creates or overwrites a text file in the workspace, while preventing unsafe blind overwrites. It is meant for producing new files or replacing files the agent has already read in the same turn.

**Data flow**: It receives a path and text content. It asks whether the file already exists; if it does and the path has not been read this turn, it refuses. Otherwise it writes the encoded text, marks the path as read, counts bytes and lines, and returns a small JSON summary.

**Call relations**: This is the handler behind the registered `write` tool. It relies on the sandbox for file existence checks and writing, then packages the outcome as text JSON for the model.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `edit_handler`  (lines 406–414)

```
async def edit_handler(ctx: ToolContext, args: EditInput) -> ToolResult
```

**Purpose**: Applies exact text replacements to a workspace file, but only after the file has been read. This helps ensure the agent edits content it has actually inspected.

**Data flow**: It receives a file path and one or more edit instructions. If the path was not read earlier in the turn, it refuses. Otherwise it converts the edit objects into plain dictionaries, asks the sandbox file tool to apply them, and returns the sandbox’s JSON result.

**Call relations**: This is the handler behind the registered `edit` tool. The actual edit operation is delegated to the sandbox’s `sbxfs` command, while this function enforces the read-before-edit rule and formats the result.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `glob_handler`  (lines 417–423)

```
async def glob_handler(ctx: ToolContext, args: GlobInput) -> ToolResult
```

**Purpose**: Finds files in the workspace whose paths match a pattern, such as `**/*.py`. It gives the agent a safe replacement for ad hoc shell commands like `find` or `ls`.

**Data flow**: It receives a glob pattern and an optional starting directory. It defaults to the workspace root, asks the sandbox file tool to do the matching there, and returns the matched paths as JSON text.

**Call relations**: This is the handler behind the registered `glob` tool. It sends the search work into the sandbox and only brings back the bounded match list wrapped in a `ToolResult`.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `grep_handler`  (lines 426–444)

```
async def grep_handler(ctx: ToolContext, args: GrepInput) -> ToolResult
```

**Purpose**: Searches workspace file contents for a regular expression, meaning a text pattern that can match more than one exact string. It is the safe, bounded way for the agent to search code or documents.

**Data flow**: It receives the search pattern plus optional filters such as file glob, context lines, case-insensitive mode, output style, and result limit. It builds a request for the sandbox’s search tool, applies a default result cap if none is provided, and returns the search result as JSON text.

**Call relations**: This is the handler behind the registered `grep` tool. It delegates the scan to sandbox `sbxfs`, which keeps large file traversal inside the container, then wraps the returned result for the model.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `share_file_handler`  (lines 447–524)

```
async def share_file_handler(ctx: ToolContext, args: ShareFileInput) -> ToolResult
```

**Purpose**: Shares a produced workspace file with the user by copying it to artifact storage and returning a temporary download link. This is the controlled escape hatch for files leaving the sandbox.

**Data flow**: It receives a workspace file path, optional download name, and optional caption. It first checks that artifact sharing is configured, then runs a small sandbox preflight to compute size, digest, and whether the file looks like text. It chooses a safe filename, streams the file from the sandbox into blob storage, records a shared-artifact database row, creates a time-limited token, and returns JSON with the URL and file details.

**Call relations**: This is the handler behind the registered `share_file` tool. It works with the sandbox for file access, blob storage for durable file bytes, the database for artifact records, and artifact-token code for the downloadable URL.

*Call graph*: 15 external calls (__init__, __init__, now, dumps, loads, guess_type, PurePosixPath, quote, insert, select (+5 more)).


##### `spawn_subagent_handler`  (lines 527–538)

```
async def spawn_subagent_handler(ctx: ToolContext, args: SpawnSubagentInput) -> ToolResult
```

**Purpose**: Starts a named subagent to do a typed subtask, either waiting for its answer or letting it run in the background. It lets the main agent split off specialized work.

**Data flow**: It receives a subagent profile name, a payload dictionary, and a background flag. It asks the tool context to spawn the child. If the profile is unknown, it returns a recoverable error. If the child is running in the background, it returns the child turn id; otherwise it returns the child’s validated JSON output and marks it untrusted if needed.

**Call relations**: This is the handler behind the registered `spawn_subagent` tool. The actual child-turn creation happens through `ToolContext.spawn`; this function translates success or profile errors into normal tool output.

*Call graph*: 3 external calls (__init__, __init__, spawn).


##### `ask_user_handler`  (lines 609–619)

```
async def ask_user_handler(ctx: ToolContext, args: AskUserInput) -> ToolResult
```

**Purpose**: Prepares a structured question that the agent should ask in its normal chat reply. It is used when the agent needs missing information or confirmation before continuing.

**Data flow**: It receives a title and one or more question definitions. It builds a JSON payload saying the turn is awaiting a question answer, includes the question details, prefixes it with a directive telling the agent to ask and stop, and returns that as text content.

**Call relations**: This is the handler behind the registered `ask_user` tool. It does not open a separate prompt itself; instead it gives the model and chat surface structured content so the user’s next message can carry the answer.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `load_skill_handler`  (lines 622–633)

```
async def load_skill_handler(ctx: ToolContext, args: LoadSkillInput) -> ToolResult
```

**Purpose**: Loads a named skill, including any skills it depends on, into the workspace and returns their workflow instructions. A skill is a reusable bundle of guidance and files for a type of task.

**Data flow**: It receives a skill name. It asks the skill registry for the full dependency chain, mounts each skill’s files into the sandbox workspace, builds the combined instruction text while avoiding duplicate in-context workflows, and returns that text.

**Call relations**: This is the handler behind the registered `load_skill` tool. It relies on the skill system to resolve dependencies, `mount_skill` to place files in the workspace, and `loaded_context` to produce the model-readable instructions.

*Call graph*: 4 external calls (__init__, __init__, loaded_context, mount_skill).


##### `connect_account_handler`  (lines 642–650)

```
async def connect_account_handler(ctx: ToolContext, args: ConnectAccountInput) -> ToolResult
```

**Purpose**: Creates a safe handoff for the user to connect an external account, such as GitHub or Google, without putting an authorization URL in the chat. It is for OAuth, a common sign-in approval flow used by third-party services.

**Data flow**: It receives a provider name and whether the connection should be shared with the workspace. It verifies that there is a speaking member, validates that the provider is installed and known, builds a connection request, and returns a directive plus structured JSON telling the user interface to show a private connection control.

**Call relations**: This is the handler behind the registered `connect_account` tool. It calls the installed connection-flow validator before returning a `ConnectRequest` for the chat surface and model to act on.

*Call graph*: 4 external calls (__init__, __init__, __init__, installed_connect_flow).


##### `request_credentials_handler`  (lines 659–684)

```
async def request_credentials_handler(ctx: ToolContext, args: RequestCredentialsInput) -> ToolResult
```

**Purpose**: Asks the workspace owner to enter secrets, such as API keys, through a private prompt instead of chat. This keeps sensitive values out of the conversation transcript.

**Data flow**: It receives a reason and a small list of credential prompts. It checks that there is a speaking member, that the request is in that member’s private audience, that credential storage is configured, and that the speaker is the workspace owner. It seals the allowed credential slots into a signed request, builds a credential request object, and returns a directive plus JSON for the user interface.

**Call relations**: This is the handler behind the registered `request_credentials` tool. It uses `ToolContext.speaker_is_owner` for permission checking and the credential-sealing object in the context to create a safe private fulfillment request.

*Call graph*: calls 1 internal fn (speaker_is_owner); 3 external calls (__init__, __init__, __init__).


##### `wait_for_subagents_handler`  (lines 687–700)

```
async def wait_for_subagents_handler(ctx: ToolContext, args: WaitForSubagentsInput) -> ToolResult
```

**Purpose**: Waits for one or more background subagents to finish and reports their final status and answer. It is used when the main agent has delegated work and now needs the results.

**Data flow**: It receives subagent id strings and a user-facing description. It checks that subagent control is available, converts the ids into UUID values, waits for their terminal statuses, and returns JSON with each subagent id, status, and output. If any returned result is marked untrusted, the whole tool result carries that warning.

**Call relations**: This is the handler behind the registered `wait_for_subagents` tool. It hands the wait request to `ctx.subagents`, then converts the returned status objects into model-readable JSON.

*Call graph*: 4 external calls (__init__, __init__, dumps, UUID).


##### `cancel_subagent_handler`  (lines 703–715)

```
async def cancel_subagent_handler(ctx: ToolContext, args: CancelSubagentInput) -> ToolResult
```

**Purpose**: Cancels a running background subagent and reports what state it is in afterward. It gives the main agent a way to stop delegated work that is no longer needed.

**Data flow**: It receives a subagent id and a user-facing description. It checks that subagent control is available, converts the id into a UUID, asks the subagent controller to cancel it, and returns JSON with the subagent id and current status.

**Call relations**: This is the handler behind the registered `cancel_subagent` tool. It delegates the actual cancellation to `ctx.subagents` and wraps the resulting status for the model.

*Call graph*: 4 external calls (__init__, __init__, dumps, UUID).


##### `message_subagent_handler`  (lines 718–731)

```
async def message_subagent_handler(ctx: ToolContext, args: MessageSubagentInput) -> ToolResult
```

**Purpose**: Sends a follow-up message to a background subagent so it can continue with new instructions on its next turn. It is used when the main agent wants to steer or update delegated work.

**Data flow**: It receives a subagent id, a message, and a user-facing description. It checks that subagent control is available, converts the id into a UUID, queues the message through the subagent controller, and returns JSON with the resulting subagent turn id and status.

**Call relations**: This is the handler behind the registered `message_subagent` tool. It hands the follow-up to `ctx.subagents`, which schedules it for the child agent, then reports the status back as a normal tool result.

*Call graph*: 4 external calls (__init__, __init__, dumps, UUID).


### Sandbox foundations
These files establish the sandbox package, the safe workspace session boundary, and the local host-backed sandbox implementation.

### `core/src/ufo/sandbox/__init__.py`

`other` · `import time`

This is an empty Python package marker. In Python projects, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. You can think of it like a label on a drawer: the drawer may contain useful tools, but this label is what lets the rest of the system find the drawer by name.

Because this file has no code inside it, it does not run setup steps, expose helper functions, or change program behavior directly. Its value is structural. Without it, depending on the Python version and packaging setup, imports that expect `ufo.sandbox` to be a regular package might fail or behave differently. Keeping the file also gives the project a clear place to add package-level setup later if that ever becomes necessary.


### `core/src/ufo/sandbox/session.py`

`domain_logic` · `per-turn sandbox use`

A sandbox is like a rented workbench for one conversation: tools can run programs there, write files there, and read results back, but they should not be able to rummage through the rest of the system. This file defines that boundary. It describes what every sandbox provider, called a carrier, must be able to do: create a sandbox, run a command, copy files in and out, destroy it, and expose a port if needed.

The file also defines small value objects that carry sandbox setup details, such as where the workspace is mounted, how the network proxy is reached, and which container has been opened. These objects let the rest of the system talk about sandboxes without caring whether the sandbox is local Docker, E2B, or another backend.

A key safety feature is path checking. Tool-supplied file paths are normalized and rejected if they try to climb out of `/workspace`, much like a guard stopping someone from leaving the allowed room. Another key feature is signed run tokens, which let the proxy recognize which workspace and turn a sandbox network request belongs to, without trusting arbitrary input.

The `SandboxSession` class is the practical tool-facing wrapper. It offers simple actions like `bash`, `write_file`, `file_exists`, `run_sbxfs`, `export_file`, and `host`, while applying the same workspace and carrier rules every time.

#### Function details

##### `RunTokenCodec.from_env`  (lines 45–49)

```
def from_env(cls) -> 'RunTokenCodec'
```

**Purpose**: Builds a token signer from the deployment secret stored in the environment. This matters because sandbox network requests must carry tokens that only this UFO deployment can create and verify.

**Data flow**: It reads the secret value from the environment variable named by `UFO_TOKEN_SECRET_ENV`. If the value is missing, it stops with an error because safe token signing cannot happen. If the value is present, it turns the text into bytes and returns a `RunTokenCodec` ready to sign and verify run tokens.

**Call relations**: Startup paths call this when they need token support: `core/src/ufo/proxy_serve.ProxyServe.serve` uses it for the proxy side, and `core/src/ufo/serve.run` uses it while setting up service execution. After this, other code can use the codec to mint or check per-turn sandbox tokens.

*Call graph*: called by 2 (serve, run).


##### `RunTokenCodec.encode`  (lines 51–53)

```
def encode(self, run: RunToken) -> str
```

**Purpose**: Turns a workspace-and-turn identity into a signed token string. The token can later prove that a sandbox request belongs to a specific conversation turn and was minted by this deployment.

**Data flow**: It receives a `RunToken` containing a workspace ID and a turn ID. It formats those IDs into a compact payload, signs that payload with the codec secret, and returns the signed string. The original IDs are not changed; they are packaged into a tamper-resistant token.

**Call relations**: `core/src/ufo/loop/queue._open_sandbox` calls this while opening a sandbox for a turn. The resulting token is put into the sandbox setup so later network egress can be attributed to the right workspace and turn.

*Call graph*: called by 1 (_open_sandbox); 1 external calls (sign_token).


##### `RunTokenCodec.from_proxy_auth`  (lines 55–66)

```
def from_proxy_auth(self, header: str) -> RunToken
```

**Purpose**: Reads a proxy authorization header and recovers the signed sandbox run identity from it. This lets the proxy accept only tokens created by this UFO deployment.

**Data flow**: It receives an HTTP-style authorization header. It checks that the header uses Basic authentication, decodes the base64 username, verifies the signed token, checks that it is a UFO run token, and converts the embedded workspace and turn strings into UUID objects. If any step fails, it raises a clear `ValueError`; if everything succeeds, it returns a `RunToken`.

**Call relations**: This is the decoding partner to `RunTokenCodec.encode`. The sandbox-opening flow creates the signed username, and the proxy side uses this method when a sandbox request arrives so it can learn which workspace and turn the request belongs to.

*Call graph*: 4 external calls (__init__, b64decode, verify_token, UUID).


##### `format_sandbox_handle`  (lines 146–150)

```
def format_sandbox_handle(backend: str, container_id: str) -> str
```

**Purpose**: Creates the durable sandbox handle string stored with a conversation. The backend name is included so a later deployment does not accidentally resume or clean up a sandbox that belongs to a different sandbox system.

**Data flow**: It receives a backend name and a container ID. It joins them with the sandbox handle separator and returns one string in the form `backend:id`. It does not inspect or modify the container itself.

**Call relations**: This helper is used when code needs a stable stored reference to a sandbox. Its companion, `sandbox_handle_id`, later checks whether such a stored reference belongs to the current backend before using it.


##### `sandbox_handle_id`  (lines 153–157)

```
def sandbox_handle_id(backend: str, value: str) -> str | None
```

**Purpose**: Extracts the container ID from a stored sandbox handle only if it belongs to the requested backend. This prevents one backend from taking over another backend's saved sandbox ID.

**Data flow**: It receives the backend currently in use and a stored handle string. If the string starts with that backend plus the separator, it returns the rest of the string as the container ID. Otherwise, it returns `None`, meaning this backend should ignore the handle.

**Call relations**: This is the inverse check for handles created by `format_sandbox_handle`. It supports safe resume and cleanup flows by making backend ownership explicit.


##### `Carrier.create`  (lines 172–172)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Defines the required operation for creating or reattaching to a sandbox. Each carrier backend implements this in its own way, but the rest of the system can call it through the same shape.

**Data flow**: It receives a `SandboxSpec`, which includes the conversation ID, image, workspace mount, proxy details, run token, optional resume ID, and environment variables. An implementation uses that information to start or resume a sandbox and returns a `SandboxHandle` that represents the live sandbox.

**Call relations**: `core/src/ufo/loop/queue._open_sandbox` calls this when a turn needs a sandbox. The returned handle is then carried by `SandboxSession` so tools can run commands and move files without knowing which backend created the sandbox.

*Call graph*: called by 1 (_open_sandbox).


##### `Carrier.exec`  (lines 174–176)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Defines the required operation for running a command inside a sandbox. This is the central way tools ask the sandbox to do work, such as running shell commands or helper programs.

**Data flow**: It receives a sandbox handle, the command as an argument tuple, and a timeout in seconds. An implementation runs that command inside the sandbox and returns an `ExecResult` containing standard output, standard error, and the exit code.

**Call relations**: This is part of the carrier contract. `SandboxSession` methods are built around this operation: they use it to run shell commands, check files, prepare the tool output directory, and call the in-sandbox `sbxfs` file helper.


##### `Carrier.write`  (lines 178–184)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Defines the required operation for copying bytes into a file in the sandbox workspace. It exists so large or binary content does not have to be squeezed into a shell command string.

**Data flow**: It receives a sandbox handle, an absolute workspace path, and bytes to write. An implementation creates parent directories if needed and writes the bytes into the sandbox's workspace. It returns nothing, but the file inside the sandbox is changed.

**Call relations**: This is the copy-in half of the carrier contract. `SandboxSession.write_file` checks the path first and then hands the safe path and content to this operation.


##### `Carrier.export`  (lines 186–191)

```
async def export(self, handle: SandboxHandle, path: str, blob: BlobStore, key: str) -> None
```

**Purpose**: Defines the required operation for streaming a sandbox workspace file into blob storage. This avoids loading a whole large file into the UFO process at once.

**Data flow**: It receives a sandbox handle, an absolute workspace path, a blob store, and a destination key. An implementation streams the file from the sandbox or mounted workspace into the blob store under that key. The result is stored externally; the function itself returns nothing.

**Call relations**: This is the copy-out half of the carrier contract. `SandboxSession.export_file` first applies workspace path protection, then asks the carrier to perform the actual streaming export.


##### `Carrier.destroy`  (lines 193–193)

```
async def destroy(self, handle: SandboxHandle) -> None
```

**Purpose**: Defines the required operation for reclaiming or removing a sandbox. The durable workspace should survive elsewhere, so the sandbox itself can be treated as disposable cache.

**Data flow**: It receives a sandbox handle. An implementation tears down or releases the sandbox represented by that handle. It returns nothing, but the carrier's live resources may be removed.

**Call relations**: This method is part of the backend contract used by cleanup and reaping flows. The protocol keeps teardown behavior behind the carrier boundary so different sandbox systems can clean themselves up correctly.


##### `Carrier.host`  (lines 195–201)

```
async def host(self, handle: SandboxHandle, port: int) -> str
```

**Purpose**: Defines the required operation for finding an externally reachable address for a service running inside the sandbox. This is needed when a tool starts something like a browser debugging port or a preview server inside the container.

**Data flow**: It receives a sandbox handle and an in-sandbox port number. An implementation maps that internal port to a host address that outside code can dial, or raises an error if that backend cannot expose ports. It returns the address as a string.

**Call relations**: This is the carrier-level operation behind `SandboxSession.host`. Browser-related extension code calls the session method when it needs to connect from the main service process to something running inside the sandbox.


##### `workspace_path`  (lines 204–212)

```
def workspace_path(path: str) -> str
```

**Purpose**: Turns a tool-supplied path into a safe absolute path under `/workspace`. It rejects paths that try to escape the workspace, such as paths using too many `..` parent-directory steps.

**Data flow**: It receives a path string, treats relative paths as being inside `/workspace`, normalizes `.` and `..` parts through `_resolve_parts`, and checks the result. If the final path is inside `/workspace`, it returns the normalized string; if not, it raises `ValueError`.

**Call relations**: `SandboxSession.write_file`, `SandboxSession.file_exists`, `SandboxSession.run_sbxfs`, and `SandboxSession.export_file` all call this before touching files. It is the shared guardrail that keeps all those operations inside the conversation workspace.

*Call graph*: calls 1 internal fn (_resolve_parts); called by 4 (export_file, file_exists, run_sbxfs, write_file); 1 external calls (PurePosixPath).


##### `_resolve_parts`  (lines 215–224)

```
def _resolve_parts(parts: tuple[str, ...]) -> list[str]
```

**Purpose**: Simplifies path pieces while detecting attempts to climb above the allowed root. It is a small helper used by `workspace_path` to make path safety decisions reliable.

**Data flow**: It receives the parts of a POSIX-style path. It builds a stack of meaningful path parts, skips empty pieces and `.` pieces, pops one level for `..`, and raises an error if `..` would escape above the root. It returns the cleaned list of path parts.

**Call relations**: `workspace_path` calls this during path normalization. It does the low-level path cleanup so `workspace_path` can focus on checking that the final path remains under `/workspace`.

*Call graph*: called by 1 (workspace_path).


##### `SandboxSession.bash`  (lines 236–241)

```
async def bash(self, command: str, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs a shell command inside the sandbox for this session. It gives tools a simple way to execute normal command-line work while still going through the carrier boundary.

**Data flow**: It receives a command string and an optional timeout. It wraps the command as `bash -lc <command>`, chooses the default timeout if none was supplied, and asks the carrier to execute it in this session's sandbox handle. It returns the carrier's `ExecResult` with output text and exit status.

**Call relations**: `extensions/sandbox_chrome/ufo_ext_sandbox_chrome._run` calls this when Chrome-related sandbox code needs to run a command. Internally, the method hands off to the carrier's `exec` operation so the backend decides how command execution actually happens.

*Call graph*: called by 1 (_run).


##### `SandboxSession.write_file`  (lines 243–244)

```
async def write_file(self, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes to a file in the sandbox workspace after making sure the path is safe. This is used when the host side needs to place content into the sandbox for a tool or skill.

**Data flow**: It receives a requested path and byte content. It converts the path into a protected `/workspace` path with `workspace_path`, then gives the safe path and bytes to the carrier's `write` operation. It returns nothing, but the sandbox workspace gains or updates the file.

**Call relations**: `core/src/ufo/skills/runtime.mount_skill` calls this when mounting skill-related content into the sandbox. This method sits between that higher-level skill setup and the carrier-specific file-writing implementation.

*Call graph*: calls 1 internal fn (workspace_path); called by 1 (mount_skill).


##### `SandboxSession.ensure_tool_output_dir`  (lines 246–268)

```
async def ensure_tool_output_dir(self) -> bool
```

**Purpose**: Makes sure the engine's private `.tool-output` directory exists inside the workspace. If something has occupied that exact name as a file or broken link, it removes that squatter so later offloaded tool outputs do not fail.

**Data flow**: It runs a small shell script in the sandbox against the fixed path `/workspace/.tool-output`. If the path is already a directory, nothing changes. If it is a file or link, the script removes it, prints `r`, and creates the directory. If the shell command fails, the method raises `OSError`; otherwise it returns `True` if something was reclaimed and `False` if not.

**Call relations**: No direct caller is shown in the provided graph, but this method belongs to the per-turn `SandboxSession` tool surface. It uses the carrier's `exec` operation because the directory check and possible cleanup must happen inside the sandbox filesystem.


##### `SandboxSession.file_exists`  (lines 270–275)

```
async def file_exists(self, path: str) -> bool
```

**Purpose**: Checks whether a regular file exists inside the sandbox workspace. It is a safe, narrow file test rather than a general shell escape hatch.

**Data flow**: It receives a path, first converts it to a protected workspace path, then runs `test -f` inside the sandbox through the carrier. If the command exits successfully, it returns `True`; otherwise it returns `False`.

**Call relations**: No direct caller is shown in the provided graph, but this method is part of the session API for tools and engine code. It relies on `workspace_path` for safety and on the carrier's `exec` operation for the actual check inside the sandbox.

*Call graph*: calls 1 internal fn (workspace_path).


##### `SandboxSession.run_sbxfs`  (lines 277–304)

```
async def run_sbxfs(self, op: str, args: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs one structured file operation through the in-sandbox `sbxfs` command and returns its JSON result. This keeps heavy file work, such as windowed reads or rendering, inside the sandbox instead of pulling entire files into the host process.

**Data flow**: It receives an operation name and a dictionary of arguments. If the arguments include a string `path`, it rewrites that path through `workspace_path`. It JSON-encodes the arguments, runs `sbxfs <op> <json>` in the sandbox, trims and parses the command output as JSON, and expects a JSON object. Empty output, invalid JSON, or a non-object result becomes a runtime error; a returned `error` string becomes a `ValueError`; otherwise the parsed dictionary is returned.

**Call relations**: No direct caller is shown in the provided graph, but this is the session's safe bridge to the sandbox file helper. It combines path protection, carrier execution, and JSON parsing so callers get a bounded structured result instead of raw command output.

*Call graph*: calls 1 internal fn (workspace_path); 2 external calls (dumps, loads).


##### `SandboxSession.export_file`  (lines 306–307)

```
async def export_file(self, path: str, blob: BlobStore, key: str) -> None
```

**Purpose**: Copies a file from the sandbox workspace into blob storage after checking that the path stays inside `/workspace`. This is the safe large-file export path.

**Data flow**: It receives a requested path, a blob store, and a destination key. It converts the path to a protected workspace path, then asks the carrier to stream that file into the blob store under the given key. It returns nothing, but the external blob store receives the file content.

**Call relations**: No direct caller is shown in the provided graph, but this method is the session-level wrapper around the carrier's `export` operation. It ensures every export gets the same workspace escape protection before backend-specific streaming begins.

*Call graph*: calls 1 internal fn (workspace_path).


##### `SandboxSession.host`  (lines 309–313)

```
async def host(self, port: int) -> str
```

**Purpose**: Gets the outside address for a service listening on a port inside the sandbox. This lets code outside the sandbox connect to things the turn started, such as a browser control endpoint or preview server.

**Data flow**: It receives an internal port number. It passes this session's sandbox handle and the port to the carrier, which returns the reachable host address according to that backend's networking rules. The method returns that address string.

**Call relations**: `extensions/sandbox_chrome/ufo_ext_sandbox_chrome.SandboxChromeCdpProvider.lease` calls this when it needs to connect to Chrome's debugging endpoint inside the sandbox. The session delegates the actual address mapping to the carrier because each backend exposes ports differently.

*Call graph*: called by 1 (lease).


##### `SandboxSession.traffic_token`  (lines 316–319)

```
def traffic_token(self) -> str | None
```

**Purpose**: Returns the sandbox's traffic token when the carrier requires one for connecting to exposed ports. Some backends use this as an extra access key for public per-port hosts.

**Data flow**: It reads the `traffic_token` field from this session's `SandboxHandle`. If the carrier supplied a token, that string is returned; otherwise the result is `None`. Nothing is changed.

**Call relations**: No direct caller is shown in the provided graph, but this property pairs with `SandboxSession.host`: code that dials a carrier-exposed host may also need this token as a connection header. The value originates from the carrier-created sandbox handle.


### `core/src/ufo/sandbox/local.py`

`io_transport` · `sandbox creation and command execution`

This file is a bridge between the project’s sandbox interface and the ordinary local computer. Instead of starting a real container, it treats a host folder as the conversation’s `/workspace` and runs commands as normal subprocesses with that folder as their current directory. Think of it like putting the work on a clearly marked desk, then asking tools to work at that desk, rather than locking them in a separate room.

At startup for the carrier, it creates a temporary scratch area. This scratch area holds helper programs named `sbx` and `sbxfs`, plus a fake home directory for tools that expect `$HOME`. The actual workspace is not kept there; it stays in the durable mounted folder supplied by the caller.

When a sandbox is created, the file prepares an environment for commands. It sets proxy variables such as `HTTP_PROXY` and `HTTPS_PROXY`, installs a certificate authority file, and fills model API keys with sentinel values. This means outbound network traffic still goes through the project’s proxy, so metering and key swapping behave like they do in a container.

The important warning is that this is not real isolation. Commands are ordinary host subprocesses. The code rewrites `/workspace` paths to the host workspace path, but the operating system is not preventing a command from accessing the rest of the machine.

#### Function details

##### `_provision_scratch`  (lines 40–53)

```
def _provision_scratch() -> Path
```

**Purpose**: Creates a temporary support folder for the local carrier. This folder holds helper binaries and a home directory that local subprocesses can use, while keeping these support files out of the real workspace.

**Data flow**: It starts with no caller-supplied input. It asks the operating system for a new temporary directory, creates `home` and `bin` folders inside it, copies the bundled `sbx` and `sbxfs` helper programs into `bin`, makes them executable, and returns the path to the scratch directory.

**Call relations**: This is used as the default factory for `LocalCarrier`’s `_scratch` field, so it runs when a carrier instance is made. Later, `LocalCarrier.create` uses the resulting folder to build the command environment.

*Call graph*: 2 external calls (Path, mkdtemp).


##### `LocalCarrier.create`  (lines 60–88)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Prepares a usable local sandbox handle for one conversation. It makes sure the workspace folder exists and builds the environment variables that every command should inherit.

**Data flow**: It receives a `SandboxSpec`, which contains the conversation identity, workspace mount, proxy details, run token, and extra environment variables. It turns the mount into a host folder path, creates that folder if needed, writes the proxy certificate into the scratch area, builds proxy URLs and tool paths, then returns a `SandboxHandle` containing the workspace mount and full command environment.

**Call relations**: This is called when the system wants to start using the local carrier for a conversation. It depends on `_root` to find the host workspace folder and produces the `SandboxHandle` that later calls such as `LocalCarrier.exec`, `LocalCarrier.write`, and `LocalCarrier.export` use.

*Call graph*: calls 1 internal fn (_root); 3 external calls (__init__, to_thread, Path).


##### `LocalCarrier.exec`  (lines 90–116)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs one command in the local workspace as a host subprocess. It makes local execution look like sandbox execution by translating `/workspace` paths and applying the prepared proxy-aware environment.

**Data flow**: It receives a sandbox handle, a command argument list, and a timeout. It finds the host workspace folder, rewrites any `/workspace` text in the arguments to the real host path, starts the subprocess with the workspace as its current directory, waits for output, and returns an `ExecResult` with standard output, standard error, and the exit code. If the command takes too long, it kills the process and returns a timeout result.

**Call relations**: This is used whenever the system asks the local sandbox to run a tool or shell command. It relies on `_root` for the workspace path and hands the command to Python’s subprocess machinery. Its result is the common sandbox execution result used by higher-level code, so callers do not need to know whether the command ran locally or in a remote sandbox.

*Call graph*: calls 1 internal fn (_root); 3 external calls (__init__, create_subprocess_exec, wait_for).


##### `LocalCarrier.write`  (lines 118–123)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Copies bytes into a file inside the local workspace. It is how higher-level code places input files where local sandbox commands can see them.

**Data flow**: It receives a sandbox handle, a logical workspace path, and raw file content. It converts the logical `/workspace/...` path into a real host path, creates parent folders if they do not already exist, and writes the bytes to disk. It returns nothing, but the workspace file is changed.

**Call relations**: This is called when the system needs to copy data into the sandbox before running commands. It uses `_host_path` to keep the write anchored under the mounted workspace, then performs the filesystem work off the main async loop so other async tasks are not blocked.

*Call graph*: calls 1 internal fn (_host_path); 1 external calls (to_thread).


##### `LocalCarrier.export`  (lines 125–129)

```
async def export(self, handle: SandboxHandle, path: str, blob: BlobStore, key: str) -> None
```

**Purpose**: Sends a file from the local workspace into the project’s blob store. This is used for larger outputs or attachments that should be stored outside the sandbox workspace.

**Data flow**: It receives a sandbox handle, a logical workspace path, a blob store, and a destination key. It converts the logical path to the real host file path, then asks the blob store to read that file and save it under the given key. The function returns nothing, but the blob store gains the exported file.

**Call relations**: This is called when higher-level code wants to copy a produced file out of the local sandbox. It uses `_host_path` to locate the file and hands the actual transfer to `BlobStore.put_file`, so this function does not need to load the whole file into memory.

*Call graph*: calls 2 internal fn (put_file, _host_path).


##### `LocalCarrier.destroy`  (lines 131–133)

```
async def destroy(self, handle: SandboxHandle) -> None
```

**Purpose**: Completes the carrier interface for tearing down a sandbox, but the local carrier has nothing per conversation to clean up here. The workspace is intentionally durable, and command environment data lives only on the handle.

**Data flow**: It receives a sandbox handle and does not read or change anything. It returns nothing because no local container, network namespace, or temporary per-conversation resource was created.

**Call relations**: This is called when the broader sandbox lifecycle says a sandbox should be destroyed. For this local implementation, the call is effectively a no-op, unlike container or cloud carriers that may need to shut down real resources.


##### `LocalCarrier.host`  (lines 135–143)

```
async def host(self, handle: SandboxHandle, port: int) -> str
```

**Purpose**: Reports that the local carrier cannot expose an in-sandbox service through a separate external host. If a workflow needs to reach a service running inside the sandbox by port, it must use a remote carrier such as E2B.

**Data flow**: It receives a sandbox handle and a port number, but it cannot turn them into a reachable external address. Instead of returning a host string, it raises a runtime error explaining the limitation.

**Call relations**: This is called by code that wants an address for a service running in the sandbox, such as a browser debugging endpoint or preview server. In the local carrier, there is no separate network-addressable sandbox, so the function stops the flow with a clear error rather than pretending such a host exists.


##### `_root`  (lines 146–149)

```
def _root(mount: MountSpec | None) -> Path
```

**Purpose**: Finds the real host folder that backs the sandbox workspace. It also enforces that the local carrier must be given a filesystem mount.

**Data flow**: It receives a mount description, checks that it exists and has a host path, and returns that host path as a `Path` object. If no usable host path is present, it raises an error instead of letting later file or command operations fail confusingly.

**Call relations**: This helper is used by `LocalCarrier.create`, `LocalCarrier.exec`, and `_host_path` whenever they need the workspace’s real location. It is the shared gatekeeper for the local carrier’s core assumption: there must be a host directory acting as `/workspace`.

*Call graph*: called by 3 (create, exec, _host_path); 1 external calls (Path).


##### `_host_path`  (lines 152–155)

```
def _host_path(handle: SandboxHandle, path: str) -> Path
```

**Purpose**: Converts a logical sandbox path like `/workspace/file.txt` into the matching real path on the host machine. This lets file operations use the same paths that sandbox tools talk about.

**Data flow**: It receives a sandbox handle and a logical path string. It gets the workspace root from the handle’s mount, strips the `/workspace` prefix from the logical path, joins the remaining relative path onto the host workspace root, and returns the resulting host path.

**Call relations**: This helper is used by `LocalCarrier.write` before writing files into the workspace and by `LocalCarrier.export` before reading files out. It builds on `_root`, so both copy-in and copy-out follow the same path translation rule.

*Call graph*: calls 1 internal fn (_root); called by 2 (export, write); 1 external calls (PurePosixPath).


### Container and cloud sandboxes
These extensions provide alternate sandbox backends for running conversation workspaces in Docker containers or E2B cloud sandboxes.

### `extensions/docker/ufo_ext_docker.py`

`io_transport` · `sandbox creation, command execution, file transfer, and teardown`

This file is the Docker-based sandbox adapter. A sandbox is the isolated place where an agent can run commands without touching the host system directly. Instead of running commands locally, this adapter starts a Docker container for a conversation and then uses `docker exec` to run each command inside it.

The main job is safety and repeatability. Each conversation gets a named container and its own Docker network. If the container is still running on a later turn, the code attaches to it again instead of starting over. The workspace, however, is durable: it is either bind-mounted from the host filesystem or mounted from S3 using `s3fs` (a tool that makes an S3 bucket look like a folder). That means the disposable container can die, but the conversation’s files can still come back.

Network access is also carefully controlled. Every command gets proxy environment variables for that specific turn. Those variables are not saved inside the container, because a long-lived container must not keep using an old run token. The proxy also receives placeholder API keys, so real credentials are not placed inside the sandbox.

The file also installs the proxy’s certificate, prepares S3 mounts when needed, streams file writes through standard input, exports produced files to blob storage, and cleans up containers and networks when the sandbox is destroyed.

#### Function details

##### `_docker`  (lines 63–77)

```
async def _docker(*argv: str, stdin: bytes=b'', timeout_s: int=60) -> tuple[int, bytes, bytes]
```

**Purpose**: Runs the Docker command-line tool asynchronously and returns its exit code, standard output, and standard error. It is the single low-level doorway this file uses to talk to Docker.

**Data flow**: It receives Docker arguments, optional bytes to send on standard input, and a timeout. It starts a `docker ...` subprocess, waits for it to finish, and collects its output. If it takes too long, it kills the process and returns a timeout-style result instead of hanging forever.

**Call relations**: The rest of the Docker carrier calls this helper whenever it needs Docker to do something: start a container, execute a command, create a network, check a mount, install a certificate, or remove resources. Internally it delegates the actual process launch and timed wait to Python’s asyncio subprocess tools.

*Call graph*: called by 9 (_ensure_network, _install_ca, _mount_healthy, _mount_s3, _running_id, create, destroy, exec, write); 2 external calls (create_subprocess_exec, wait_for).


##### `DockerCarrier.create`  (lines 84–147)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates a Docker sandbox for a conversation, or attaches to the existing running one if it is already there. It also prepares the per-turn proxy environment so commands run with the right network controls.

**Data flow**: It takes a sandbox specification containing the conversation id, image, mount settings, proxy details, run token, and environment variables. It builds the container name and proxy variables, checks whether the container is already running, and either returns a handle for it or creates a network and starts a new container. After startup it installs the proxy certificate, mounts S3 if needed, and returns a sandbox handle containing the container id, mount information, and per-exec environment.

**Call relations**: This is the main setup path for the Docker carrier. It asks `_running_id` whether it can reuse a container, uses `_network_name` and `_ensure_network` before a new run, calls `_install_ca` so HTTPS through the proxy is trusted, calls `_credential_url` and `_mount_s3` for S3-backed workspaces, and uses `_docker` for the actual Docker operations.

*Call graph*: calls 7 internal fn (_credential_url, _ensure_network, _install_ca, _mount_s3, _network_name, _running_id, _docker); 1 external calls (__init__).


##### `DockerCarrier.exec`  (lines 149–162)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs a command inside an existing sandbox container and returns what happened. This is how the rest of the system asks the Docker sandbox to do work.

**Data flow**: It receives a sandbox handle, a command argument list, and a timeout. It turns the handle’s proxy and secret-placeholder environment into Docker `--env` arguments, runs `docker exec` inside the container, decodes the output text, and returns an execution result with stdout, stderr, and the exit code.

**Call relations**: After `create` has produced a sandbox handle, higher-level sandbox code can call this method for each command. It hands the actual Docker call to `_docker` and wraps the returned bytes in an `ExecResult` for the caller.

*Call graph*: calls 1 internal fn (_docker); 1 external calls (__init__).


##### `DockerCarrier.write`  (lines 164–180)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes into a file inside the sandbox container. It is used when the host needs to place content into the sandbox without exposing that content on the command line.

**Data flow**: It receives a sandbox handle, a target path, and raw bytes. It runs a shell command inside the container that creates the parent folder and writes standard input into the target file. The bytes are streamed through standard input; if Docker reports failure, the function raises an error.

**Call relations**: This method is called when the carrier must copy content into the container. It relies on `_docker` to run `docker exec -i`, which gives the command a real input stream.

*Call graph*: calls 1 internal fn (_docker).


##### `DockerCarrier.export`  (lines 182–199)

```
async def export(self, handle: SandboxHandle, path: str, blob: BlobStore, key: str) -> None
```

**Purpose**: Copies a file produced in the sandbox workspace into the project’s blob store, without unnecessarily loading large files into memory. It is the path for saving sandbox outputs as durable attachments.

**Data flow**: It receives a sandbox handle, a workspace path, a blob store, and the destination blob key. It checks which kind of workspace mount is being used. For an S3 workspace, it asks the blob store to copy from the workspace prefix to the destination key inside storage. For a filesystem workspace, it asks the blob store to stream the file from the host-mounted path.

**Call relations**: This method is used after sandbox work has produced a file that must be stored elsewhere. It calls the blob store’s `copy` for S3-backed workspaces and `put_file` for filesystem-backed workspaces.

*Call graph*: calls 2 internal fn (copy, put_file); 2 external calls (Path, PurePosixPath).


##### `DockerCarrier._mount_s3`  (lines 201–267)

```
async def _mount_s3(self, handle: SandboxHandle, mount: MountSpec, credential_url: str) -> None
```

**Purpose**: Mounts an S3-backed workspace inside the Docker container at `/workspace`. It makes cloud storage look like a normal folder to programs running in the sandbox.

**Data flow**: It receives a sandbox handle, mount details, and a credential URL. If the mount is not S3, it does nothing. For S3, it verifies all needed settings, writes a private credential token into the container as root, checks whether the mount is already healthy, and if not, prepares and runs the mount commands. Finally it checks the mounted workspace again and raises an error if it is not usable.

**Call relations**: `DockerCarrier.create` calls this after attaching to or creating a container. This helper uses `_mount_healthy` to avoid remounting an already working workspace, `_docker` to run root commands in the container, and sandbox utility functions to build the safe token-install and `s3fs` mount scripts.

*Call graph*: calls 2 internal fn (_mount_healthy, _docker); called by 1 (create); 5 external calls (quote, install_token_command, mount_scripts, prepare_token_staging_command, s3fs_command).


##### `DockerCarrier._credential_url`  (lines 269–280)

```
def _credential_url(self, spec: SandboxSpec) -> str
```

**Purpose**: Builds the URL that the in-container S3 filesystem helper uses to fetch scoped storage credentials. It ensures that public credential access, when configured, uses encrypted HTTPS.

**Data flow**: It receives the sandbox specification and reads the proxy settings. If a public proxy URL is set, it parses and validates that it is an HTTPS URL with a hostname, then appends the credential endpoint path. If no public URL is set, it returns a local host-gateway URL pointing back to the proxy on the Docker host.

**Call relations**: `DockerCarrier.create` calls this before `_mount_s3` so the S3 mount code knows where to ask for temporary credentials. It uses URL parsing to reject unsafe public credential URLs.

*Call graph*: called by 1 (create); 2 external calls (rstrip, urlsplit).


##### `DockerCarrier._mount_healthy`  (lines 282–294)

```
async def _mount_healthy(self, handle: SandboxHandle) -> bool
```

**Purpose**: Checks whether the sandbox workspace mount is currently usable. It is a quick health probe before and after attempting an S3 mount.

**Data flow**: It receives a sandbox handle, runs a health-check shell script inside the container as root, and returns `true` if the command exits successfully. It does not return file contents; it only returns a yes-or-no status.

**Call relations**: `DockerCarrier._mount_s3` calls this first to skip unnecessary remounting and again after mounting to confirm success. It uses `_docker` to run the health-check command built by the shared sandbox utilities.

*Call graph*: calls 1 internal fn (_docker); called by 1 (_mount_s3); 1 external calls (mount_health_check).


##### `DockerCarrier.destroy`  (lines 296–302)

```
async def destroy(self, handle: SandboxHandle) -> None
```

**Purpose**: Removes the Docker container and Docker network for a conversation. It is the cleanup step for a Docker sandbox.

**Data flow**: It receives a sandbox handle, derives the stable container name from the conversation id, and asks Docker to force-remove that container. It also derives the conversation’s network name and asks Docker to remove that network. It does not fail if the container is already gone.

**Call relations**: Higher-level lifecycle code calls this when a sandbox should be reaped. It uses `_network_name` to match the same network naming scheme used during creation, and `_docker` to perform the Docker removals.

*Call graph*: calls 2 internal fn (_network_name, _docker).


##### `DockerCarrier.host`  (lines 304–311)

```
async def host(self, handle: SandboxHandle, port: int) -> str
```

**Purpose**: Reports that this Docker carrier cannot expose a sandbox port as an externally reachable host. It exists to give callers a clear error instead of pretending port publishing is supported.

**Data flow**: It receives a sandbox handle and a port number, but does not use them to build a route. It immediately raises an error explaining that Docker sandboxes in this carrier do not provide external per-port access.

**Call relations**: If higher-level code asks for a public address for a service running inside the sandbox, this method is the Docker carrier’s answer. Unlike carriers that support remote port access, it stops the flow with a clear message.


##### `DockerCarrier._running_id`  (lines 313–324)

```
async def _running_id(self, name: str) -> str | None
```

**Purpose**: Checks whether a named Docker container is currently running and returns its container id if found. It carefully distinguishes “not found” from “Docker failed.”

**Data flow**: It receives a container name, runs `docker ps` filtered to that exact running name, and reads the output. Empty output means no running container was found, while a non-zero Docker exit code becomes an error. If a match exists, it returns the container id text.

**Call relations**: `DockerCarrier.create` calls this before starting a new container. This prevents unnecessary recreation and avoids hiding Docker daemon problems as simple container-missing cases.

*Call graph*: calls 1 internal fn (_docker); called by 1 (create).


##### `DockerCarrier._network_name`  (lines 326–327)

```
def _network_name(self, conversation_id: UUID) -> str
```

**Purpose**: Creates the Docker network name for one conversation. The name combines the carrier’s network prefix with the conversation id.

**Data flow**: It receives a conversation UUID and converts it into a stable network name string. Nothing outside the function is changed.

**Call relations**: `DockerCarrier.create` uses this name when preparing a new sandbox network, and `DockerCarrier.destroy` uses the same naming rule to remove that network later.

*Call graph*: called by 2 (create, destroy).


##### `DockerCarrier._ensure_network`  (lines 329–337)

```
async def _ensure_network(self, network: str) -> None
```

**Purpose**: Makes sure the Docker network for a sandbox exists before the container is started. If it is already present, it leaves it alone.

**Data flow**: It receives a network name, asks Docker whether a network with that exact name exists, and returns immediately if it does. If none is found, it runs `docker network create`. Docker command failures become runtime errors with the Docker error text.

**Call relations**: `DockerCarrier.create` calls this before `docker run` so the new container can join the correct isolated network. It uses `_docker` for both the lookup and the create operation.

*Call graph*: calls 1 internal fn (_docker); called by 1 (create).


##### `DockerCarrier._install_ca`  (lines 339–352)

```
async def _install_ca(self, container_id: str, ca_cert: str) -> None
```

**Purpose**: Installs the egress proxy’s certificate authority inside a newly created container. This lets HTTPS tools inside the sandbox trust the proxy when traffic is inspected or rewritten.

**Data flow**: It receives a container id and certificate text. It streams the certificate into the container as root, writes it into the system certificate directory, and runs the container’s certificate update command. If that command fails, it raises an error.

**Call relations**: `DockerCarrier.create` calls this after a new container starts and before returning it for use. It delegates the container-side work to `_docker` with standard input carrying the certificate bytes.

*Call graph*: calls 1 internal fn (_docker); called by 1 (create).


##### `manifest`  (lines 355–360)

```
def manifest() -> Manifest
```

**Purpose**: Declares this file as a UFO extension named `docker`. It tells the host system that `DockerCarrier` is the factory to use for Docker-backed sandboxes.

**Data flow**: It takes no input. It builds and returns a manifest object containing the extension name, version, and a carrier specification that points to `DockerCarrier`.

**Call relations**: The extension loading system calls this to discover what this module provides. The returned manifest hands the Docker carrier class to the core system so it can create Docker sandboxes when the sandbox backend is configured as Docker.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/e2b/ufo_ext_e2b.py`

`io_transport` · `sandbox lifecycle and request handling`

A UFO conversation needs a private workspace where tools can run commands, write files, and start temporary services. This file is the adapter that makes that workspace live inside E2B, an external cloud sandbox provider. Without it, choosing `[sandbox] backend = "e2b"` would not work: the system would not know how to start the sandbox, reach it, mount its files, or clean it up.

The file registers an `e2b` carrier with the project manifest. A carrier is the piece that knows how to provide a sandbox. When a conversation starts or resumes, `E2BCarrier.create` either reuses an already-known sandbox, reconnects to a saved one, or creates a fresh E2B sandbox from a named template. It then installs the proxy certificate, mounts the conversation’s S3-backed workspace at `/workspace`, and returns a handle the rest of UFO can use.

Remote sandboxes need careful network control. Commands run with proxy environment variables so outgoing web requests go through UFO’s metered egress proxy, using a run token instead of exposing real model API keys. The file also supports writing files, exporting finished files to the artifact store, getting public hosts for sandbox services, and pausing sandboxes instead of destroying them outright so later turns can resume faster.

#### Function details

##### `_egress_env`  (lines 83–115)

```
def _egress_env(proxy: ProxyEndpoint, run_token: str) -> dict[str, str]
```

**Purpose**: Builds the environment variables that force commands inside the remote sandbox to send web traffic through UFO’s egress proxy. This matters because the sandbox is outside the cluster, so it needs a public HTTPS proxy address and must not receive real model API keys.

**Data flow**: It takes a proxy description and a per-run token. It checks that the proxy has a public HTTPS URL, turns that URL into proxy settings with the run token as the username, adds placeholder model keys and certificate settings, and returns a dictionary of environment variables. If the proxy URL is missing or unsafe, it raises an error before an unmetered sandbox can be created.

**Call relations**: When `E2BCarrier.create` prepares a sandbox handle, it calls this first so later command execution already has the right network rules attached. Internally it uses URL parsing to inspect the public proxy address.

*Call graph*: called by 1 (create); 1 external calls (urlsplit).


##### `E2BCommands.run`  (lines 128–136)

```
async def run(self, cmd: str, *, cwd: str | None=None, envs: dict[str, str] | None=None, user: str | None=None, timeout: float | None=None) -> E2BCommandResult
```

**Purpose**: Describes the E2B SDK method used to run a shell command in the sandbox. It is a protocol entry, meaning this file only states the shape of the SDK object it expects rather than implementing the command runner itself.

**Data flow**: Callers provide a command string plus optional working directory, environment variables, user, and timeout. The real E2B SDK sends that command to the sandbox and returns standard output, standard error, and an exit code.

**Call relations**: The carrier relies on this method during setup, mounting, health checks, certificate installation, and normal user command execution. The actual work is handed off to E2B’s SDK.


##### `E2BFiles.make_dir`  (lines 140–140)

```
async def make_dir(self, path: str, *, user: str | None=None) -> bool
```

**Purpose**: Describes the E2B SDK method used to create a directory inside the sandbox filesystem. This is needed when a fresh sandbox is first prepared with a workspace directory.

**Data flow**: It receives a path and optionally a user name. The real SDK asks the sandbox to create that directory and reports whether it succeeded.

**Call relations**: `E2BCarrier.create` uses this through the SDK when it creates a brand-new sandbox and needs `/workspace` to exist.


##### `E2BFiles.write`  (lines 142–142)

```
async def write(self, path: str, data: str | bytes, *, user: str | None=None) -> object
```

**Purpose**: Describes the E2B SDK method used to write text or bytes to a file in the sandbox. This is the safe path for sending raw file contents, because command execution only accepts a shell string.

**Data flow**: It receives a sandbox path, file contents, and optionally a user name. The real SDK uploads the data to that path and returns whatever write result the SDK provides.

**Call relations**: The carrier uses this to install the proxy certificate, stage S3 mount credentials, and upload files requested by the rest of the system.


##### `E2BSandbox.pause`  (lines 151–151)

```
async def pause(self, **opts: object) -> bool
```

**Purpose**: Describes the E2B SDK method used to pause a sandbox. Pausing keeps the sandbox resumable instead of treating it as a permanent active machine.

**Data flow**: It receives provider-specific options such as the API key. The real SDK asks E2B to pause the sandbox and returns whether that request succeeded.

**Call relations**: `E2BCarrier.destroy` calls this when UFO is finished with a sandbox or when an idle sandbox is being reclaimed.


##### `E2BSandbox.get_host`  (lines 153–153)

```
def get_host(self, port: int) -> str
```

**Purpose**: Describes the E2B SDK method that gives an outside-accessible host name for a port opened inside the sandbox. This lets UFO reach services started by tools, such as a preview web server.

**Data flow**: It takes a port number. The real SDK formats or returns the public host name that routes to that sandbox port.

**Call relations**: `E2BCarrier.host` calls this after finding the right sandbox, then hands the host string back to the caller.


##### `E2BSdk.create`  (lines 157–165)

```
async def create(self, *, template: str, timeout: int, metadata: dict[str, str], lifecycle: SandboxLifecycle, api_key: str) -> E2BSandbox
```

**Purpose**: Describes the E2B SDK method used to create a new sandbox from a template. This is how UFO starts a fresh remote workspace when there is no existing sandbox to resume.

**Data flow**: It receives the template name, timeout, metadata, lifecycle rules, and API key. The real SDK asks E2B to start the sandbox and returns an object representing it.

**Call relations**: `E2BCarrier.create` uses this path only when neither the in-memory cache nor the durable resume ID points to an existing sandbox.


##### `E2BSdk.connect`  (lines 167–173)

```
async def connect(self, sandbox_id: str, *, timeout: int, api_key: str) -> E2BSandbox
```

**Purpose**: Describes the E2B SDK method used to reconnect to an existing sandbox by ID. This is important because a later request, or even a different process, may need to continue using a sandbox created earlier.

**Data flow**: It receives a sandbox ID, timeout, and API key. The real SDK reconnects to that sandbox and returns the sandbox object, or raises an error if the sandbox no longer exists.

**Call relations**: `E2BCarrier.create`, `E2BCarrier.destroy`, and `E2BCarrier._sandbox` use this whenever they need to resume or reclaim a sandbox that is not already held in memory.


##### `E2BCarrier.create`  (lines 187–218)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates or resumes the E2B sandbox for a conversation and returns the handle UFO will use for later operations. It also prepares the sandbox so commands have trusted proxy certificates, mounted workspace storage, and controlled network access.

**Data flow**: It receives a `SandboxSpec`, which includes the conversation ID, optional resume ID, proxy information, run token, environment variables, and mount settings. It builds proxy environment variables, reuses or reconnects to an existing sandbox when possible, otherwise creates a new one, installs the proxy certificate, mounts the S3 workspace if needed, stores the sandbox in the in-memory live map, and returns a `SandboxHandle` containing the sandbox ID, mount details, traffic token, and command environment.

**Call relations**: This is the main setup path for the carrier. It calls `_egress_env` for network rules, `_install_ca` so the sandbox trusts the proxy, and `_mount_s3` so `/workspace` is backed by durable storage before handing the ready handle back to core sandbox code.

*Call graph*: calls 3 internal fn (_install_ca, _mount_s3, _egress_env); 3 external calls (__init__, cast, rstrip).


##### `E2BCarrier._install_ca`  (lines 220–228)

```
async def _install_ca(self, sandbox: E2BSandbox, ca_cert: str) -> None
```

**Purpose**: Installs UFO’s egress proxy certificate inside the sandbox. This lets HTTPS traffic pass through the proxy without tools inside the sandbox rejecting the connection as untrusted.

**Data flow**: It receives a sandbox object and the certificate text. It writes the certificate to a staging path as root, runs a root command that installs or updates the system certificate file, and returns nothing if successful. If the install command fails, it raises a clear runtime error with the command’s output.

**Call relations**: `E2BCarrier.create` calls this every time it prepares a sandbox, before normal commands run with proxy settings.

*Call graph*: called by 1 (create).


##### `E2BCarrier._mount_s3`  (lines 230–274)

```
async def _mount_s3(self, sandbox: E2BSandbox, mount: MountSpec, credential_url: str) -> None
```

**Purpose**: Mounts the conversation’s S3-backed workspace into the sandbox at `/workspace`. In plain terms, it makes remote object storage look like a normal folder inside the sandbox.

**Data flow**: It receives the sandbox, mount settings, and a credential URL. If the mount is not S3, it does nothing. For S3 mounts, it checks required credentials and endpoint details, stages a private token in the sandbox, checks whether the mount is already healthy, and if not runs preparation and mount commands. Afterward it runs another health check and raises an error if the workspace still is not usable.

**Call relations**: `E2BCarrier.create` calls this during sandbox preparation. This helper calls `_mount_healthy` to avoid remounting a working filesystem, and uses shared sandbox helpers to build the token, preparation, and `s3fs` mount commands.

*Call graph*: calls 1 internal fn (_mount_healthy); called by 1 (create); 4 external calls (install_token_command, mount_scripts, prepare_token_staging_command, s3fs_command).


##### `E2BCarrier._mount_healthy`  (lines 276–285)

```
async def _mount_healthy(self, sandbox: E2BSandbox) -> bool
```

**Purpose**: Checks whether the workspace mount inside the sandbox is working. This prevents unnecessary remounting and catches broken storage setup early.

**Data flow**: It receives a sandbox object. It runs a small health-check command against `/workspace` as root with a short timeout. If the command succeeds it returns `true`; if the command fails or times out it returns `false`.

**Call relations**: `E2BCarrier._mount_s3` calls this before and after attempting a mount, using it first as a skip check and later as proof that setup succeeded.

*Call graph*: called by 1 (_mount_s3); 1 external calls (mount_health_check).


##### `E2BCarrier.exec`  (lines 287–309)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs a command inside the E2B sandbox and returns its output and exit code in UFO’s standard format. It makes remote command execution look like local shell execution to the rest of the system.

**Data flow**: It receives a sandbox handle, command arguments, and a timeout. It finds or reconnects to the sandbox, quotes the argument list into one shell command string, runs it in `/workspace` with normal sandbox environment settings plus the proxy environment, and returns an `ExecResult`. If the command exits with an error, that exit code and output are returned; if it times out, it returns a timeout-style exit code.

**Call relations**: When the rest of UFO wants to run a tool command, it comes through this method. This method depends on `_sandbox` to get the live E2B object and then hands the actual execution to the E2B command API.

*Call graph*: calls 1 internal fn (_sandbox); 2 external calls (__init__, join).


##### `E2BCarrier.write`  (lines 311–316)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Uploads bytes into a file inside the sandbox. This is used when UFO needs to place file contents directly into the remote workspace rather than squeeze them through a shell command.

**Data flow**: It receives a sandbox handle, destination path, and byte content. It finds or reconnects to the sandbox and asks E2B’s file API to write the bytes to that path. It returns nothing after the upload completes.

**Call relations**: Callers use this for direct file transfer. It relies on `_sandbox` for the correct live sandbox and then hands the upload to the E2B files API.

*Call graph*: calls 1 internal fn (_sandbox).


##### `E2BCarrier.export`  (lines 318–330)

```
async def export(self, handle: SandboxHandle, path: str, blob: BlobStore, key: str) -> None
```

**Purpose**: Copies a finished workspace file into the artifact store without downloading it through the sandbox. This is faster and cleaner because the copy happens inside the storage backend.

**Data flow**: It receives a sandbox handle, a path inside `/workspace`, a blob store, and the destination artifact key. It checks that the workspace is backed by S3, turns the workspace path into a relative path, combines it with the mount’s S3 key prefix, and asks the blob store to copy that source object to the artifact key. It returns nothing when the copy is done.

**Call relations**: This is used after a tool has produced a file that should become an artifact. Instead of calling the E2B sandbox, it uses the mount information saved in the handle and delegates the actual server-side copy to `BlobStore.copy`.

*Call graph*: calls 1 internal fn (copy); 1 external calls (PurePosixPath).


##### `E2BCarrier.host`  (lines 332–339)

```
async def host(self, handle: SandboxHandle, port: int) -> str
```

**Purpose**: Returns the public host name for a service listening on a given port inside the sandbox. This lets outside code connect to things the sandbox starts, such as a browser debugging endpoint or development server.

**Data flow**: It receives a sandbox handle and port number. It finds or reconnects to the sandbox, asks E2B for the public host for that port, and returns the host string.

**Call relations**: Callers use this when they need inbound access to something running inside the remote sandbox. The method uses `_sandbox` to get the E2B sandbox object, then delegates host formatting or lookup to E2B.

*Call graph*: calls 1 internal fn (_sandbox).


##### `E2BCarrier.destroy`  (lines 341–358)

```
async def destroy(self, handle: SandboxHandle) -> None
```

**Purpose**: Pauses the sandbox for a conversation and removes it from this process’s live cache. Despite the name, this is more like putting the sandbox to sleep than deleting it immediately.

**Data flow**: It receives a sandbox handle. It removes any live sandbox for that conversation from the in-memory map; if none is present but the handle has a sandbox ID, it reconnects to that sandbox. If the sandbox exists, it asks E2B to pause it. If the sandbox ID is empty or E2B says the sandbox is already gone, it quietly does nothing.

**Call relations**: This is used during cleanup and idle reclaim. It can pause sandboxes created by the current process or by an earlier process, because the durable handle carries the provider’s sandbox ID.


##### `E2BCarrier._sandbox`  (lines 360–368)

```
async def _sandbox(self, handle: SandboxHandle) -> E2BSandbox
```

**Purpose**: Finds the E2B sandbox object for a handle, reconnecting if necessary. It is the small lookup helper that keeps normal operations from caring whether the sandbox was already cached in this process.

**Data flow**: It receives a sandbox handle. It first checks the in-memory live map by conversation ID. If found, it returns that sandbox. If not, it connects to E2B using the handle’s sandbox ID, stores the result in the live map, and returns it.

**Call relations**: `E2BCarrier.exec`, `E2BCarrier.write`, and `E2BCarrier.host` call this before doing their work, so each operation has a usable SDK sandbox object.

*Call graph*: called by 3 (exec, host, write).


##### `build_e2b_carrier`  (lines 371–380)

```
def build_e2b_carrier() -> E2BCarrier
```

**Purpose**: Constructs the E2B carrier when a deployment selects the `e2b` sandbox backend. It also fails early if the required E2B API key is missing.

**Data flow**: It reads the `E2B_API_KEY` environment variable. If the value is absent, it raises an error during startup. If present, it creates and returns an `E2BCarrier` configured with that key and the shared E2B template name.

**Call relations**: The manifest points to this as the factory for the `e2b` carrier. Startup code calls it once when the selected sandbox backend is E2B.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 383–388)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to UFO’s plugin system. It tells the system that there is a carrier named `e2b` and how to build it.

**Data flow**: It creates a manifest object containing the extension name, version, and a carrier specification with the carrier factory and an off-cluster flag. It returns that manifest to the extension loader.

**Call relations**: The wider application reads this manifest during extension loading. The carrier specification it returns points back to `build_e2b_carrier`, which performs the actual construction when needed.

*Call graph*: 2 external calls (__init__, __init__).


### Extension tool suites
These extension modules add specialized tools for coding support, persistent REPLs, research, website publishing, and conversation todo tracking.

### `extensions/coding/ufo_ext_coding/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder often needs an `__init__.py` file to be treated as an importable package, meaning other code can refer to things inside it using names like `ufo_ext_coding.some_module`. Think of it like a label on a drawer: the label does not contain the tools, but it tells Python that the drawer is part of the organized workspace. Because this file is empty, it does not set up configuration, import helper functions, or run any code when the package is loaded. Its value is structural: without it, some Python environments or packaging tools might not recognize this directory as a package, and imports from this extension could fail or behave inconsistently.


### `extensions/repl/ufo_ext_repl/manifest.py`

`orchestration` · `tool registration and request handling`

This file gives the agent two interactive coding workbenches. One runs JavaScript with Node.js, mainly for browser automation and visual testing. The other runs Python for Excel work with openpyxl. “Persistent” means each successful code block is saved and replayed next time, so variables, imports, and opened files can carry forward like notes kept on a whiteboard. If a run fails, its code is not saved, so a bad experiment does not poison later runs.

Both tools work inside the project sandbox, which is the controlled workspace where files and internet access are limited by the surrounding system. Before running code, the file builds a temporary script from the saved history plus the new code. It then executes that script with a timeout. If the script exits successfully, the combined code becomes the new saved state.

The JavaScript tool has extra support for images. It injects a small helper called emitImage, so user code can return generated screenshots or plots as inline image results. It also creates links to globally installed Node packages, because modern JavaScript modules do not automatically search some global package paths.

The manifest at the end is the extension’s registration form. It tells the host system what tools exist, what inputs they expect, which functions run them, what skills are bundled, and that sandbox internet access is allowed.

#### Function details

##### `global_modules_link`  (lines 54–72)

```
def global_modules_link(roots: tuple[str, ...]=GLOBAL_MODULE_ROOTS) -> str
```

**Purpose**: Builds a shell command that makes globally installed Node.js packages visible to the JavaScript REPL. This matters because JavaScript ES modules do not automatically look in every global package location, so imports like Playwright could fail without these links.

**Data flow**: It takes a list of possible global Node package folders, or uses the default list. It produces one shell command string that creates a local `.repl/node_modules` folder, removes a stale whole-folder link if needed, and adds per-package symbolic links into it. The function does not run the command itself; it only prepares the text of the command.

**Call relations**: When `js_repl` is about to run JavaScript, it calls `global_modules_link` to get the setup command. `js_repl` then runs that command in the sandbox before starting Node, so user code can import installed packages by name.

*Call graph*: called by 1 (js_repl).


##### `_candidate_source`  (lines 161–167)

```
async def _candidate_source(ctx: ToolContext, path: str, code: str, reset: bool) -> str
```

**Purpose**: Builds the full source code that should be tried for the next REPL run. It combines the previously saved successful code with the new code, unless the caller asked for a reset.

**Data flow**: It receives the sandbox context, the path to the saved REPL state file, the new code, and a reset flag. If reset is true, it deletes the saved state file. If there is no saved state, it returns just the new code with a trailing newline. If saved state exists, it reads that old code and appends the new code. The result is a single source string ready to write to a temporary run file.

**Call relations**: `js_repl` and `xlsx_repl` both call this before execution. It is the shared step that gives both REPLs their memory: each tool asks it, “What complete program should I run this time?”

*Call graph*: called by 2 (js_repl, xlsx_repl); 1 external calls (quote).


##### `_repl_result`  (lines 170–181)

```
def _repl_result(stdout: str, stderr: str, exit_code: int, images: tuple[ImageContent, ...]=()) -> ToolResult
```

**Purpose**: Packages a REPL execution result into the standard tool response format. It turns raw output, error text, exit status, and optional images into something the host system can return to the agent.

**Data flow**: It receives standard output, standard error, an exit code, and optionally image objects. It creates a text item containing a JSON string with those three run details, adds any images after that text, and marks the overall result as an error if the exit code is not zero. It returns a `ToolResult` object.

**Call relations**: Both `js_repl` and `xlsx_repl` call this after their sandbox command finishes. The JavaScript path may also pass in images collected by `_emitted_images`; the Python Excel path only passes text output and status.

*Call graph*: called by 2 (js_repl, xlsx_repl); 3 external calls (__init__, __init__, dumps).


##### `_emitted_images`  (lines 189–200)

```
async def _emitted_images(ctx: ToolContext) -> tuple[ImageContent, ...]
```

**Purpose**: Collects images that JavaScript code asked to return with `emitImage`. It reads the sandbox file where the JavaScript prelude records emitted images and turns valid entries into image response objects.

**Data flow**: It receives the sandbox context. If the image log file does not exist, it returns an empty tuple. If it exists, it reads the file, looks at the most recent non-empty lines up to the image limit, parses each line as an emitted image record, skips malformed lines, and returns the valid images as `ImageContent` objects.

**Call relations**: `js_repl` calls this after Node finishes running. The images it returns are handed straight to `_repl_result`, so screenshots or generated graphics appear alongside the text output in the final tool result.

*Call graph*: called by 1 (js_repl); 2 external calls (__init__, quote).


##### `js_repl`  (lines 203–215)

```
async def js_repl(ctx: ToolContext, args: JsReplInput) -> ToolResult
```

**Purpose**: Runs a piece of JavaScript in a persistent Node.js REPL inside the sandbox. It is meant for browser automation, website or game testing, and other JavaScript tasks where state should carry across successful calls.

**Data flow**: It receives the tool context and validated JavaScript input. First it asks `_candidate_source` for the full program made from saved state plus new code. It writes a temporary `.mjs` run file that starts with the image-emitting helper, clears the previous image log, links global Node packages, and runs Node with a timeout. If the run succeeds, it saves the candidate source as the new persistent state. Finally it reads any emitted images and returns stdout, stderr, exit code, and images through `_repl_result`.

**Call relations**: This is the handler registered for the `js_repl` tool in `manifest`. During a tool call, it coordinates the helper functions: `_candidate_source` builds the code history, `global_modules_link` prepares package imports, `_emitted_images` gathers image outputs, and `_repl_result` formats the final answer.

*Call graph*: calls 4 internal fn (_candidate_source, _emitted_images, _repl_result, global_modules_link); 1 external calls (quote).


##### `xlsx_repl`  (lines 218–226)

```
async def xlsx_repl(ctx: ToolContext, args: XlsxReplInput) -> ToolResult
```

**Purpose**: Runs a piece of Python in a persistent REPL for Excel spreadsheet work. It is designed for openpyxl-based tasks where loaded workbooks, variables, and helper code can remain available across successful calls.

**Data flow**: It receives the tool context and validated Python input. It builds the full candidate program with `_candidate_source`, writes a temporary Python run file, and appends a small footer that prints `result` as JSON if the user defined `result`. It runs Python in the sandbox with a timeout. If the run succeeds, it saves the candidate program as the new persistent state. It returns stdout, stderr, and exit code through `_repl_result`.

**Call relations**: This is the handler registered for the `xlsx_repl` tool in `manifest`. It shares the same persistence and result-formatting helpers as `js_repl`, but it does not use the JavaScript-only image or Node package setup steps.

*Call graph*: calls 2 internal fn (_candidate_source, _repl_result); 1 external calls (quote).


##### `manifest`  (lines 229–249)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the host system. It declares the extension name and version, the two available tools, their input models, their handler functions, the bundled skills, and the sandbox internet setting.

**Data flow**: It reads the module-level constants and classes defined in this file. It creates tool definitions for `js_repl` and `xlsx_repl`, creates skill specifications for each listed skill folder, and returns one `Manifest` object containing all of that registration information.

**Call relations**: The host system calls `manifest` when loading the extension. The returned manifest is what lets the system expose `js_repl` and `xlsx_repl` as callable tools and know which function to run when each tool is used.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/research/ufo_ext_research/tools.py`

`domain_logic` · `request handling`

This file is the bridge between an agent asking for web research and the actual search service that does the work. Without it, the agent would not have a standard way to say “search these queries” or “read this URL,” and each search backend would have to be used directly.

The file defines three tool inputs using Pydantic models, which are small schemas that check the shape of incoming arguments before the tool runs. `search_web` accepts up to five short queries and optional filters. `fetch_url` accepts a public HTTP or HTTPS URL plus options like whether to bypass cached content. `search_vertical` searches a specific kind of content, such as images, videos, shopping results, people, or academic papers.

The actual work is done through a `SearchProvider`, which is supplied by the current tool context. That matters because API keys and crawler behavior stay on the host side, not inside the sandbox where the agent runs. For fetched pages, the file explicitly warns that the content came from the search provider’s crawler session, not from the user’s workspace or account. In everyday terms, it is like sending a librarian’s browser to fetch a page, not your own logged-in browser.

At the bottom, the file packages the three handlers into `RESEARCH_TOOLS`, so the larger system can expose them as callable tools.

#### Function details

##### `_provider`  (lines 116–119)

```
def _provider(ctx: ToolContext) -> SearchProvider
```

**Purpose**: This function gets the search provider for the current tool call. If no provider was configured, it stops immediately with a clear error instead of letting the tool fail later in a confusing way.

**Data flow**: It receives a `ToolContext`, which is the bundle of information available during a tool call. It reads `ctx.search_provider`; if that value exists, it returns it. If it is missing, it raises an error saying there is no search provider for this turn.

**Call relations**: The three research handlers call this first before doing any search or fetch work. It acts like the front desk: `_search_web`, `_fetch_url`, and `_search_vertical` all ask it where to send the request, and it either gives them the configured provider or refuses the call loudly.

*Call graph*: called by 3 (_fetch_url, _search_vertical, _search_web).


##### `_results_json`  (lines 122–136)

```
def _results_json(hits: list[SearchHit], answer: str | None) -> str
```

**Purpose**: This function turns search results into a JSON string that the agent can read consistently. It keeps only the useful public-facing fields: URL, title, snippet text, published date, highlights, and an optional direct answer.

**Data flow**: It receives a list of search hits and an optional answer string. It walks through the hits, copies their important fields into plain dictionaries, adds the answer if one was supplied, and converts the whole payload into JSON text. The output is a single string ready to place inside a tool response.

**Call relations**: Both `_search_web` and `_search_vertical` use this after their provider returns results. Those handlers gather or request the hits, then hand them to `_results_json` so the final response has the same shape no matter which kind of search was used.

*Call graph*: called by 2 (_search_vertical, _search_web); 1 external calls (dumps).


##### `_search_web`  (lines 139–154)

```
async def _search_web(ctx: ToolContext, args: SearchWebInput) -> ToolResult
```

**Purpose**: This is the handler behind the `search_web` tool. It runs one normal web search for each requested query, combines the results, and returns them to the agent as JSON.

**Data flow**: It receives the tool context and validated `SearchWebInput` arguments. It gets the configured provider, then loops through each query. For each one, it builds a `SearchQuery` with the requested recency and domain filters, asks the provider for results, adds those hits to a combined list, and keeps the first provider-supplied answer if there is one. It returns a `ToolResult` containing JSON text with the merged results.

**Call relations**: When the system invokes the `search_web` tool, this function is the main worker. It first calls `_provider` to find the backend, then calls the provider’s search operation for each query, and finally calls `_results_json` to format the combined results before wrapping them in `TextContent` and `ToolResult` for the tool system.

*Call graph*: calls 2 internal fn (_provider, _results_json); 3 external calls (__init__, __init__, __init__).


##### `_fetch_url`  (lines 157–176)

```
async def _fetch_url(ctx: ToolContext, args: FetchUrlInput) -> ToolResult
```

**Purpose**: This is the handler behind the `fetch_url` tool. It asks the configured search provider to retrieve a public web page, optionally using a prompt to extract specific information from it.

**Data flow**: It receives the tool context and validated `FetchUrlInput` arguments. It gets the configured provider and first checks whether that provider can fetch URLs at all. If not, it returns an error message telling the agent to use another method. If fetching is supported, it builds a `FetchRequest`, sends it to the provider, and returns JSON containing the final URL, page text, crawler provenance warning, and optional summary.

**Call relations**: When the system invokes the `fetch_url` tool, this function controls the whole fetch path. It relies on `_provider` for the backend, hands a `FetchRequest` to that backend, and then wraps the fetched page in a `ToolResult`. Unlike the search handlers, it formats its own JSON because fetched pages have different fields and need the extra provenance warning.

*Call graph*: calls 1 internal fn (_provider); 4 external calls (__init__, __init__, __init__, dumps).


##### `_search_vertical`  (lines 179–186)

```
async def _search_vertical(ctx: ToolContext, args: SearchVerticalInput) -> ToolResult
```

**Purpose**: This is the handler behind the `search_vertical` tool. It searches a specialized category, such as images, people, academic papers, videos, or shopping listings, instead of doing a general web search.

**Data flow**: It receives the tool context and validated `SearchVerticalInput` arguments. It gets the configured provider, builds a `SearchQuery` that includes both the user’s query and the selected vertical, asks the provider for results, and returns those results as JSON inside a `ToolResult`.

**Call relations**: When the system invokes the `search_vertical` tool, this function asks `_provider` for the backend, sends that backend a category-specific search request, and then uses `_results_json` to produce the same result format used by normal web search. This keeps specialized search results easy for the agent to consume.

*Call graph*: calls 2 internal fn (_provider, _results_json); 3 external calls (__init__, __init__, __init__).


### `extensions/sites/ufo_ext_sites/tools.py`

`orchestration` · `tool request handling`

This file gives the system a safe, repeatable way to work with websites inside a sandbox, which is an isolated environment where commands can run without freely touching the host machine. Without it, callers would have to run raw shell commands, manually kill old servers on the same port, and hope the new server was ready before trying to open it.

The file defines input shapes for each tool, such as what command to run, which folder to use, and which port to check. The main shared helper is `_serve`. Think of it like a careful stage manager: before starting a new show, it clears anyone already using the stage, launches the server in the background, writes its output to a log file, and keeps checking the door until the server answers.

The `website` tool runs a build command and returns a simple list of files in the project folder. The `start_server` tool runs an arbitrary server command. The `deploy_website` tool serves a folder of static website files using Python’s built-in web server. The `publish_website` tool can optionally install dependencies, then either run a backend command or serve built static files. All successful tool responses are returned as JSON text so the rest of the system can read them consistently.

#### Function details

##### `StartServerInput.validate_port`  (lines 63–66)

```
def validate_port(self) -> 'StartServerInput'
```

**Purpose**: This checks that a requested server port is a real TCP port number. It prevents impossible or unsafe values, such as 0 or numbers above 65535, from reaching the server-starting code.

**Data flow**: It reads the `port` value from a `StartServerInput` object. If no port was provided, it leaves the input alone. If a port was provided, it checks that it is between 1 and 65535; valid input passes through unchanged, while invalid input becomes a clear validation error.

**Call relations**: This runs automatically when the `StartServerInput` data model is created. It protects the later `start_server` flow, which depends on a valid port before handing the work to `_serve`.


##### `_json_result`  (lines 89–90)

```
def _json_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: This turns a plain Python dictionary into the standard tool response format used by the rest of the system. It is the common finishing step for these tools when they need to return structured information like a URL, port, or file list.

**Data flow**: It takes a dictionary, converts it into a JSON string, wraps that string as text content, and then wraps the text in a tool result object. The output is a `ToolResult` that callers can pass back through the tool system.

**Call relations**: The higher-level tools call this after their real work is done. `website` uses it to return build output details, while `start_server`, `deploy_website`, and `publish_website` use it to return the live server route and related information.

*Call graph*: called by 4 (deploy_website, publish_website, start_server, website); 3 external calls (__init__, __init__, dumps).


##### `_serve`  (lines 93–130)

```
async def _serve(ctx: ToolContext, command: str, project: str, port: int, log: str) -> dict[str, object]
```

**Purpose**: This is the shared helper that starts a server safely in the sandbox and waits until it is reachable. It solves the common race where a command returns before the server is ready to accept connections.

**Data flow**: It receives the tool context, a server command, a project folder, a port, and a log file path. Inside the sandbox, it moves into the project folder, kills any old process using that port, starts the new command in the background with output going to the log file, and repeatedly tries to connect to the port. If the port opens in time, it returns a dictionary with the local URL, port, and log path. If startup fails, it reads the end of the log and raises an error with useful details.

**Call relations**: `start_server`, `deploy_website`, and `publish_website` all rely on this helper instead of duplicating server-startup steps. It is the central bridge between the friendly tool interface and the lower-level sandbox shell commands.

*Call graph*: called by 3 (deploy_website, publish_website, start_server); 1 external calls (quote).


##### `website`  (lines 133–142)

```
async def website(ctx: ToolContext, args: WebsiteInput) -> ToolResult
```

**Purpose**: This builds a website project inside the sandbox and reports what files are present afterward. It is useful when a caller wants to run a build command and quickly see the resulting project contents.

**Data flow**: It receives a tool context and build arguments. It chooses the project folder, defaulting to `/workspace` when none is provided, then runs the requested build command there with a longer build timeout. If the command fails, it raises an error using the command output. If it succeeds, it lists the files in the project folder and returns the project path plus that file list as JSON.

**Call relations**: This is one of the registered site tool handlers. Unlike the serving tools, it does not call `_serve`; it only runs a build command and then hands its final response to `_json_result`.

*Call graph*: calls 1 internal fn (_json_result); 1 external calls (quote).


##### `start_server`  (lines 145–149)

```
async def start_server(ctx: ToolContext, args: StartServerInput) -> ToolResult
```

**Purpose**: This starts a caller-provided server command in the background and returns a URL once the server is ready. It is meant to be safer than asking users or agents to run a long-lived server directly through a shell.

**Data flow**: It receives a command, project folder, optional port, and optional log file. It fills in defaults for the port and log path when needed, then asks `_serve` to clear the port, launch the command, and wait for readiness. It returns the server details plus the project path as JSON.

**Call relations**: This tool is registered under the `start_server` name. Its main job is to prepare friendly defaults and then delegate the careful startup work to `_serve`, before formatting the result through `_json_result`.

*Call graph*: calls 2 internal fn (_json_result, _serve).


##### `deploy_website`  (lines 152–156)

```
async def deploy_website(ctx: ToolContext, args: DeployWebsiteInput) -> ToolResult
```

**Purpose**: This serves a folder of already-built static website files from the sandbox. It is for cases where the site output, such as an `index.html` file and related assets, already exists and just needs to be made reachable.

**Data flow**: It receives the folder to serve, a site name, and an entry file name. It builds a Python static-file server command on the standard app-serving port, chooses a deployment log file, and calls `_serve` from the provided folder. When the server is reachable, it returns the URL, port, log path, site name, and entry point as JSON.

**Call relations**: This tool is registered under the `deploy_website` name. It uses `_serve` for the real server startup and `_json_result` for the final response, adding website-specific labels so the caller knows what was deployed.

*Call graph*: calls 2 internal fn (_json_result, _serve).


##### `publish_website`  (lines 159–171)

```
async def publish_website(ctx: ToolContext, args: PublishWebsiteInput) -> ToolResult
```

**Purpose**: This publishes a web app by optionally installing dependencies and then starting either a backend command or a static file server. It is a higher-level convenience tool for making an app reachable from the sandbox.

**Data flow**: It receives the project folder, built-output folder, app name, and optional install and run commands. If an install command is provided, it runs that first in the project folder and stops with an error if installation fails. Then it chooses either the provided backend run command or a default static-file server command. If there is no backend command, it serves the built output folder; otherwise it runs from the project folder. Finally, it starts the server through `_serve` and returns the app name plus server details as JSON.

**Call relations**: This tool is registered under the `publish_website` name. It combines a setup step with the same serving path used by `start_server` and `deploy_website`, relying on `_serve` for readiness checking and `_json_result` for the response.

*Call graph*: calls 2 internal fn (_json_result, _serve); 1 external calls (quote).


### `extensions/todos/ufo_ext_todos.py`

`domain_logic` · `during conversation tool calls`

This file gives the agent a simple progress board for multi-step work. Without it, the agent could still think through tasks, but there would be no durable checklist for the UI to show and no reliable saved record of which steps are pending, underway, or done.

The file defines two tools. The first, `update_todo_list`, creates or replaces the whole checklist. The second, `update_todo_status`, changes the status of one or more existing tasks. Think of it like a shared whiteboard: one tool rewrites the whole board, and the other moves sticky notes from “pending” to “in progress” or “completed.”

The checklist is not only kept in the model’s text context. It is saved in the extension’s scoped store, using the conversation ID as part of the key. That means a later turn in the same conversation can read back the same board. The file also defines the expected shapes of task data using Pydantic models, which are validation classes that check data has the right fields and values.

Each tool returns the current board as JSON text, so the agent immediately sees the latest state after every change. The `manifest` function exposes the tools and a prompt section to the host system, making this file the registration point for the todo extension.

#### Function details

##### `_require_ext`  (lines 82–85)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This helper makes sure the tool call has access to the todo extension’s context. The extension context is needed because it provides the saved store where todo boards are kept.

**Data flow**: It receives the current tool context. If the context contains an extension context, it returns that extension context. If not, it stops the call by raising an error, because the todo tools cannot save or read boards without it.

**Call relations**: `update_todo_list` and `update_todo_status` both call this at the start of their work. It acts like checking that you have the key to the storage room before trying to put away or fetch the checklist.

*Call graph*: called by 2 (update_todo_list, update_todo_status).


##### `_board_key`  (lines 88–89)

```
def _board_key(ctx: ToolContext) -> str
```

**Purpose**: This helper builds the storage key used to save and find the todo board for the current conversation. It keeps different conversations from overwriting each other’s checklists.

**Data flow**: It reads the conversation ID from the tool context, adds the todo key prefix to it, and returns that combined string as the store key.

**Call relations**: `update_todo_list` uses this key when saving a new board, and `update_todo_status` uses it when reading and saving an existing board. This is the naming rule that lets both tools talk about the same board for the same conversation.

*Call graph*: called by 2 (update_todo_list, update_todo_status).


##### `_board_result`  (lines 92–93)

```
def _board_result(board: TodoBoard) -> ToolResult
```

**Purpose**: This helper turns a todo board into the standard result format returned by a tool. It makes sure the caller receives the latest checklist after every create or update operation.

**Data flow**: It receives a `TodoBoard`, converts it to JSON text, wraps that text in a text content object, and then wraps that in a tool result. The output is what the tool system sends back to the agent.

**Call relations**: `update_todo_list` and `update_todo_status` both call this after saving changes. It is the final packaging step: once the board has been written, this helper hands back a clean copy of the current state.

*Call graph*: called by 2 (update_todo_list, update_todo_status); 3 external calls (__init__, __init__, model_dump_json).


##### `_read_board`  (lines 96–98)

```
async def _read_board(ext: ExtensionContext, key: str) -> TodoBoard | None
```

**Purpose**: This helper reads a saved todo board from the extension store. It returns a validated board object if one exists, or nothing if the conversation does not yet have a checklist.

**Data flow**: It receives the extension context and a storage key. It asks the store for the saved data at that key. If there is no saved data, it returns `None`; otherwise, it validates the raw stored data as a `TodoBoard` and returns that board.

**Call relations**: `update_todo_status` calls this before changing task statuses. That status tool depends on it because it can only update tasks after a checklist has already been created.

*Call graph*: called by 1 (update_todo_status).


##### `update_todo_list`  (lines 101–105)

```
async def update_todo_list(ctx: ToolContext, args: UpdateTodoListInput) -> ToolResult
```

**Purpose**: This is the tool that creates or replaces the full todo checklist. An agent uses it at the start of complex work, or when the plan changes enough that the whole task list should be rewritten.

**Data flow**: It receives the tool context and the user-supplied checklist input: a title, a complete set of tasks, and a short user-facing description. It checks that extension storage is available, builds a `TodoBoard`, saves it under the current conversation’s key, and returns the saved board as JSON in a tool result. Because the input tasks replace the entire existing list, anything not included in the new task list is removed.

**Call relations**: This function is registered as the `update_todo_list` tool in `manifest`. When the agent calls that tool, this function uses `_require_ext` to get storage access, `_board_key` to choose where to save the board, and `_board_result` to return the new state.

*Call graph*: calls 3 internal fn (_board_key, _board_result, _require_ext); 2 external calls (__init__, loads).


##### `update_todo_status`  (lines 108–119)

```
async def update_todo_status(ctx: ToolContext, args: UpdateTodoStatusInput) -> ToolResult
```

**Purpose**: This is the tool that changes the status of existing todo items. The agent uses it to mark work as started or finished as progress happens, instead of waiting until the end.

**Data flow**: It receives the tool context and one or more status updates, each using a 1-based task number and a new status. It checks that extension storage is available, reads the current board for the conversation, refuses to continue if no list exists, checks that every requested task number is inside the list, updates the matching tasks, saves the board back to storage, and returns the updated board as JSON.

**Call relations**: This function is registered as the `update_todo_status` tool in `manifest`. It relies on `_read_board` to fetch the existing checklist, `_board_key` to find the right conversation’s board, `_require_ext` for storage access, and `_board_result` to hand the updated state back to the agent.

*Call graph*: calls 4 internal fn (_board_key, _board_result, _read_board, _require_ext); 1 external calls (loads).


##### `manifest`  (lines 122–141)

```
def manifest() -> Manifest
```

**Purpose**: This function tells the host system what this extension is and what tools it offers. Without this manifest, the todo tools and their prompt guidance would not be discoverable by the rest of the system.

**Data flow**: It creates and returns a manifest containing the extension name and version, two tool definitions, and one prompt section loaded from a nearby markdown file. Each tool definition includes the public tool name, the description shown to the model, the input shape it must provide, and the Python function that should run.

**Call relations**: The host system calls this when loading the extension. It connects the outside-facing tool names to `update_todo_list` and `update_todo_status`, and it attaches the todo prompt section so the agent is instructed when and how to use the checklist.

*Call graph*: 3 external calls (__init__, __init__, __init__).

## 📊 State Registers Touched

- `reg-extension-pack-manifest` — The installed pack and extension menu that says what tools, routes, jobs, skills, credentials, and backends exist.
- `reg-workspace-tenant-record` — The customer workspace record that all users, conversations, data, tools, and billing are kept under.
- `reg-agent-profile-settings` — The saved assistant settings for a workspace, including which agent is used and what it is allowed to do.
- `reg-conversation-thread-state` — The saved conversation identity and history that let the system continue the same thread over time.
- `reg-credential-secret-store` — The encrypted store of workspace secrets and credential kinds used without exposing raw tokens to agents.
- `reg-authorization-grants` — The saved permissions showing which user-approved outside accounts an agent may use.
- `reg-tool-catalog` — The live list of tools the model can call, including their names, descriptions, schemas, and dispatch targets.
- `reg-prompt-skill-library` — The enabled instructions, skill folders, helper profiles, and prompt versions that shape how the agent behaves.
- `reg-sandbox-workspace-handle` — The saved handle and lease for the safe workspace where a conversation can run commands and keep files.
- `reg-blob-storage-backend` — The shared large-file storage used for workspace files, transcripts, source snapshots, and artifacts.
- `reg-network-egress-policy` — The allow-or-deny rules for outbound network calls, including when approved secrets may be attached.
- `reg-browser-session-provider` — The shared way to obtain a browser automation endpoint for a turn, regardless of where the browser runs.
- `reg-connector-broker-catalog` — The known external service brokers and provider actions that let agents use connected services safely.
- `reg-mcp-server-connections` — The configured MCP tool-server connections used to discover and call extra provider tools.
- `reg-search-index-memory-graph` — The shared recall stores for searchable chunks, remembered facts, memory pages, and knowledge-graph links.
- `reg-extension-object-store` — The durable per-workspace storage and named objects that extensions expose or update over time.
- `reg-live-stream-hub` — The live stream of turn updates that clients can watch and replay after reconnecting.
- `reg-artifact-download-tokens` — The short-lived signed passes that let private files produced by a turn be downloaded safely.
- `reg-observability-trace-context` — The trace, metric, and log context that follows requests and turns so operators can understand what happened.
- `reg-artifact-share-registry` — Durable artifact metadata linking produced files to workspaces, conversations, turns, blob keys, and sharing visibility.
- `reg-conversation-todo-goal-state` — Persistent per-conversation checklist, goal, or task-progress state maintained by todo/planning tools across turns.
- `reg-sandbox-image-artifact-state` — The validated sandbox/workroom image identity and preflight health result used later when creating sandbox runtimes.
- `reg-turn-context-token-budget` — The per-turn context-window and token-budget state used to compact history, construct model requests, and constrain model/tool work.
- `reg-extension-request-context-envelope` — The request-time workspace, member, conversation, turn, capability, and cleanup context passed from core into extension handlers and tools.
- `reg-site-preview-serving-state` — The extension-maintained mapping from generated site/app outputs to served preview routes or shareable live site handles.
- `reg-user-created-skill-store` — Persistent user-authored skill definitions and metadata that are loaded into the skill library and made available to prompts and tools across turns.
- `reg-repl-snippet-state` — Remembered Python or JavaScript REPL snippets and scratch execution context retained by the REPL extension for reuse across tool calls or turns.
- `reg-browser-runtime-session-state` — Mutable browser automation session state such as active CDP sessions, tabs, cookies, screenshots, and downloads used while browser tools and subagents operate.
- `reg-workspace-file-access-leases` — Short-lived scoped credentials or signed grants that let sandboxes mount or access only approved workspace blob/file paths.
- `reg-web-search-fetch-backend` — The configured web-search and page-fetch provider backend, client settings, and availability used by research, browsing, source, and SDK search calls.
