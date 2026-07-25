# Built-in workspace, artifact, and conversation tools  `stage-11.1`

This stage gives the agent its built-in toolbox during a conversation. When the model decides it needs to inspect files, run a shell command, search the workspace, ask the user a question, or use a service, these tools turn that request into controlled action. It is part of the main work loop, but it also provides shared support that many tasks rely on.

The main file, `builtins.py`, is the bridge between the model and the outside working area. It defines tools for reading and editing files, running commands, sharing files, loading extra skills, collecting secrets safely, connecting accounts, and coordinating helper agents. In everyday terms, it is the agent’s workbench: each tool is a different instrument, and the file makes sure they are used through the project’s safe workspace and service rules.

The todo extension adds a simple checklist for longer requests. It lets the agent create tasks, mark progress, and keep that list tied to the current conversation, so work can continue clearly across later messages.

## Files in this stage

### Built-in agent tools
Core built-in tools expose workspace operations, user interaction, service integration, subagent coordination, and per-conversation todo tracking.

### `core/src/ufo/tools/builtins.py`

`domain_logic` · `tool execution during a turn`

This file is like the agent’s tool belt. Each tool gives the model a controlled way to do something useful without directly reaching into the host machine or private services. File work goes through the sandbox, which is an isolated workspace container. That matters because commands, reads, writes, searches, and file sharing must obey the same safety boundaries.

The file defines small input shapes for each tool, then one handler function per tool. A handler receives the current tool context and the user-provided arguments, performs the work, and returns a ToolResult containing text or images for the model to see. For example, read can return text, images, PDF pages, or slide renders. write and edit include a safety rule: an existing file must have been read earlier in the turn before it can be changed, so the model cannot blindly overwrite content it has not seen.

Some tools reach beyond files. share_file streams a workspace file to artifact storage and creates a temporary download link. load_sessions fetches older transcripts that the current audience is allowed to see. ask_user and request_credentials pause the workflow so the user can answer or privately enter secrets. The subagent tools start, wait for, cancel, or message child agent runs. At the bottom, BUILTIN_TOOLS registers all of these handlers so the rest of the system can expose them to the model.

#### Function details

##### `bash_handler`  (lines 272–282)

```
async def bash_handler(ctx: ToolContext, args: BashInput) -> ToolResult
```

**Purpose**: Runs a shell command inside the secure workspace sandbox. It gives the agent a way to use installed command-line programs while keeping the command away from the host machine.

**Data flow**: It receives a command and an optional timeout. It caps the timeout at the allowed maximum, asks the sandbox to run the command, combines standard output and error text, then returns that text. If the command failed, it also includes the exit code and marks the tool result as an error.

**Call relations**: This is the handler registered for the bash tool. When the model calls bash, the registry routes the call here; this function then hands the actual command execution to the sandbox and wraps the sandbox’s answer as tool content.

*Call graph*: 2 external calls (__init__, __init__).


##### `_require_str`  (lines 285–288)

```
def _require_str(value: object, field: str) -> str
```

**Purpose**: Checks that a value returned by the sandbox is a real, non-empty string. It is used when image or document rendering needs required fields such as a media type or encoded image data.

**Data flow**: It receives any value and the name of the field being checked. If the value is a usable string, it returns it unchanged. If not, it raises an error explaining that the sandbox returned an incomplete result.

**Call relations**: This helper is called by read_handler and _pdf_result after sandbox file reads. It acts as a small guardrail before building image content, so malformed sandbox output is caught early instead of being passed along as broken model content.

*Call graph*: called by 2 (_pdf_result, read_handler).


##### `_pdf_result`  (lines 291–336)

```
def _pdf_result(result: dict[str, object]) -> ToolResult
```

**Purpose**: Turns the sandbox’s PDF or PowerPoint read result into content the model can understand: extracted text plus rendered page or slide images. It lets the agent inspect visual documents without pulling the full file into the host process.

**Data flow**: It receives a dictionary from the sandbox. It collects extracted text, page or slide range notes, and any warnings into a text block, then converts each rendered page into an image block. It returns a ToolResult containing those blocks, or raises an error if the result is empty or malformed.

**Call relations**: read_handler calls this when the sandbox says the file is a PDF or PPTX. _pdf_result uses _require_str to validate image fields, then hands the finished text-and-image result back to read_handler’s caller.

*Call graph*: calls 1 internal fn (_require_str); called by 1 (read_handler); 3 external calls (__init__, __init__, __init__).


##### `read_handler`  (lines 339–376)

```
async def read_handler(ctx: ToolContext, args: ReadInput) -> ToolResult
```

**Purpose**: Reads a workspace file through the sandbox and returns a bounded, model-friendly view of it. It supports text files, images, PDFs, and PowerPoint files, and it records that the path was read so later edits or writes can be allowed safely.

**Data flow**: It receives a file path plus optional offset and limit values. It asks the sandbox file tool to read that slice or render that file type. Depending on the returned type, it builds text content, image content, or delegates PDF/PPTX formatting to _pdf_result. It also adds the path to the turn’s read-path set.

**Call relations**: This is the handler for the read tool. It is the normal first step before edit_handler or write_handler can change an existing file, because those functions check the read-path record that this function updates.

*Call graph*: calls 2 internal fn (_pdf_result, _require_str); 3 external calls (__init__, __init__, __init__).


##### `write_handler`  (lines 379–400)

```
async def write_handler(ctx: ToolContext, args: WriteInput) -> ToolResult
```

**Purpose**: Creates or replaces a text file in the workspace. It protects existing files by requiring the agent to read them first before overwriting them.

**Data flow**: It receives a target path and text content. It asks the sandbox whether the file already exists; if it exists but was not read this turn, it stops with an error. Otherwise it writes the bytes, records the path as read, and returns JSON with the path, whether it was newly created, byte size, and line count.

**Call relations**: This is the handler for the write tool. It depends on read_handler’s read-path tracking for safety and hands the actual file write to the sandbox.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `edit_handler`  (lines 403–411)

```
async def edit_handler(ctx: ToolContext, args: EditInput) -> ToolResult
```

**Purpose**: Applies exact text replacements to a file in the workspace. It is meant for careful edits where the old text is known, rather than broad blind rewriting.

**Data flow**: It receives a file path and one or more replacement instructions. It first checks that the file was read earlier in the turn. It converts the edit objects into plain dictionaries, sends them to the sandbox file tool, and returns the sandbox’s JSON result.

**Call relations**: This is the handler for the edit tool. It relies on read_handler having recorded the file path first, then delegates the detailed replacement work to the sandbox’s sbxfs edit command.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `glob_handler`  (lines 414–420)

```
async def glob_handler(ctx: ToolContext, args: GlobInput) -> ToolResult
```

**Purpose**: Finds files in the workspace whose paths match a pattern, such as all Python files. It avoids using shell commands for file discovery and keeps the directory walk inside the sandbox.

**Data flow**: It receives a glob pattern and an optional search directory. If no directory is given, it searches from the workspace root. It asks the sandbox file tool to find matches and returns the bounded JSON result as text.

**Call relations**: This is the handler for the glob tool. The tool registry calls it when the model needs file names; it delegates the search to the sandbox and only brings back the matching paths.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `grep_handler`  (lines 423–441)

```
async def grep_handler(ctx: ToolContext, args: GrepInput) -> ToolResult
```

**Purpose**: Searches file contents in the workspace for a regular expression, which is a text pattern rule. It keeps the scan inside the sandbox and limits how much result data comes back.

**Data flow**: It receives the search pattern plus optional filters such as file glob, context lines, case sensitivity, output style, and result limit. It fills in safe defaults, calls the sandbox search tool, and returns the JSON search results as text.

**Call relations**: This is the handler for the grep tool. It is used instead of raw shell grep or ripgrep calls, so searching follows the same sandbox boundary and output limits as other file tools.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `share_file_handler`  (lines 444–521)

```
async def share_file_handler(ctx: ToolContext, args: ShareFileInput) -> ToolResult
```

**Purpose**: Makes a workspace file available outside the sandbox as a downloadable artifact link. This is the approved path for sending a produced file back to the user.

**Data flow**: It receives a workspace file path, optional download name, and optional caption. It first checks that artifact sharing is configured. It runs a small sandbox preflight program to measure the file, compute its SHA-256 fingerprint, and guess whether it is text without loading the whole file into memory. It chooses a safe filename, streams the file to blob storage, records the shared artifact in the database, creates a temporary signed download token, and returns JSON with the URL and file details.

**Call relations**: This is the handler for the share_file tool. It coordinates the sandbox for file access, blob storage for durable bytes, the database for artifact records, artifact-token code for the download link, and artifact naming code so user-facing surfaces can show the shared file consistently.

*Call graph*: 15 external calls (__init__, __init__, now, dumps, loads, guess_type, PurePosixPath, quote, insert, select (+5 more)).


##### `spawn_subagent_handler`  (lines 524–535)

```
async def spawn_subagent_handler(ctx: ToolContext, args: SpawnSubagentInput) -> ToolResult
```

**Purpose**: Starts a child agent to work on a typed subtask. It can either wait for the child’s validated answer or start it in the background and return its turn ID immediately.

**Data flow**: It receives a subagent profile name, a payload, and a background flag. It asks the ToolContext to spawn the child. If the profile name is unknown, it returns a recoverable error. If the child is running in the background, it returns a short message with the new turn ID; otherwise it returns the child’s JSON output and preserves whether that output is considered untrusted.

**Call relations**: This is the handler for spawn_subagent. It hands the real child-run work to ToolContext.spawn. Later, wait_for_subagents_handler, cancel_subagent_handler, or message_subagent_handler can refer to background children created through this flow.

*Call graph*: 3 external calls (__init__, __init__, spawn).


##### `load_sessions_handler`  (lines 538–598)

```
async def load_sessions_handler(ctx: ToolContext, args: LoadSessionsInput) -> ToolResult
```

**Purpose**: Loads selected past conversation transcripts that the current audience is allowed to see. It lets the agent recall earlier sessions without giving it unrestricted history access.

**Data flow**: It receives up to a fixed number of session ID strings. It separates malformed IDs into a failed list, queries the database for conversations in the current workspace and audience scope, then fetches each matching transcript from blob storage. It decodes messages, keeps text content, and returns JSON containing loaded sessions plus IDs that failed for any reason.

**Call relations**: This is the handler for load_sessions. It uses the database transaction helper to check access, transcript_key to locate stored transcripts, decode to read them, and returns a per-session summary instead of aborting the whole call on one bad ID.

*Call graph*: 8 external calls (__init__, __init__, dumps, select, workspace_tx, decode, transcript_key, UUID).


##### `ask_user_handler`  (lines 606–616)

```
async def ask_user_handler(ctx: ToolContext, args: AskUserInput) -> ToolResult
```

**Purpose**: Packages one or more questions for the agent to ask in chat, then tells the agent to end its turn and wait. It keeps the interaction inside the normal conversation instead of opening a separate prompt.

**Data flow**: It receives a structured question request. It builds a payload containing the title and question objects, prefixes it with an instruction telling the model to ask and stop, and returns that as text content.

**Call relations**: This is the handler for ask_user. A chat surface can render the structured payload nicely, while the model uses the directive to present the question and wait for the user’s next message.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `load_skill_handler`  (lines 619–628)

```
async def load_skill_handler(ctx: ToolContext, args: LoadSkillInput) -> ToolResult
```

**Purpose**: Loads a named skill, including any skills it depends on, into the workspace and returns their instructions. A skill is a reusable bundle of guidance and files for a particular kind of task.

**Data flow**: It receives a skill name. It asks the skill tree for that skill and its dependency chain, mounts each skill’s files into the sandbox workspace, gathers their instruction text, and returns a combined message listing what was loaded.

**Call relations**: This is the handler for load_skill. It relies on the context’s skill registry to resolve the requested name and on mount_skill to place the skill files where the agent can use them.

*Call graph*: 3 external calls (__init__, __init__, mount_skill).


##### `connect_account_handler`  (lines 637–645)

```
async def connect_account_handler(ctx: ToolContext, args: ConnectAccountInput) -> ToolResult
```

**Purpose**: Starts a private account-connection handoff, such as connecting GitHub or Google through OAuth. OAuth is the common web flow where a user grants access without sharing their password with the app.

**Data flow**: It receives a provider name and whether the connection should be shared with the workspace. It checks that there is a speaking member, validates that the provider is supported, builds a ConnectRequest, and returns instructions telling the agent to direct the member to the private connection control rather than exposing any authorization link in chat.

**Call relations**: This is the handler for connect_account. It calls the installed connect-flow validator before returning the structured request that the surrounding chat surface can turn into a private connection UI.

*Call graph*: 4 external calls (__init__, __init__, __init__, installed_connect_flow).


##### `request_credentials_handler`  (lines 654–679)

```
async def request_credentials_handler(ctx: ToolContext, args: RequestCredentialsInput) -> ToolResult
```

**Purpose**: Asks the workspace owner to enter secrets, such as API keys, through a private prompt instead of in the chat transcript. It prevents sensitive values from being stored in conversation history.

**Data flow**: It receives a reason and a small set of credential prompts. It checks that there is a speaking member, that the conversation is private to that member, that credential storage is configured, and that the speaker is the workspace owner. It then seals the requested slot names into a signed request, builds a CredentialRequest, and returns instructions for the agent to ask the member to fill the private prompts and end the turn.

**Call relations**: This is the handler for request_credentials. It calls ToolContext.speaker_is_owner for permission checking and uses the context’s credential-sealing service so a later private fulfillment can prove which slots were requested.

*Call graph*: calls 1 internal fn (speaker_is_owner); 3 external calls (__init__, __init__, __init__).


##### `wait_for_subagents_handler`  (lines 682–695)

```
async def wait_for_subagents_handler(ctx: ToolContext, args: WaitForSubagentsInput) -> ToolResult
```

**Purpose**: Waits for one or more background subagents to finish and reports their final statuses and answers. It is used when the main agent has delegated work and needs the results before continuing.

**Data flow**: It receives subagent ID strings and converts them to UUIDs, which are standard unique identifiers. It asks the subagent controller to wait for those child runs, then returns JSON with each subagent ID, status, and output text. If any result is marked untrusted, the returned ToolResult carries that warning.

**Call relations**: This is the handler for wait_for_subagents. It only works when the current context has subagent control available, and it is normally used after spawn_subagent_handler has started background children.

*Call graph*: 4 external calls (__init__, __init__, dumps, UUID).


##### `cancel_subagent_handler`  (lines 698–710)

```
async def cancel_subagent_handler(ctx: ToolContext, args: CancelSubagentInput) -> ToolResult
```

**Purpose**: Requests cancellation of a running background subagent and reports its status. If the subagent has already finished, the cancellation does not rewrite its completed result.

**Data flow**: It receives one subagent ID string and converts it to a UUID. It asks the subagent controller to cancel that child run, then returns JSON with the subagent ID and current status.

**Call relations**: This is the handler for cancel_subagent. It talks to the same subagent controller used by spawned background children, and it refuses to operate if subagent control is not available in the current context.

*Call graph*: 4 external calls (__init__, __init__, dumps, UUID).


##### `message_subagent_handler`  (lines 713–726)

```
async def message_subagent_handler(ctx: ToolContext, args: MessageSubagentInput) -> ToolResult
```

**Purpose**: Sends a follow-up message to a background subagent. The message becomes the subagent’s next turn after its current work reaches a stopping point.

**Data flow**: It receives a subagent ID, a message, and a user-facing description. It converts the ID to a UUID, asks the subagent controller to queue the message, and returns JSON with the new or updated subagent turn ID and status.

**Call relations**: This is the handler for message_subagent. It is used after spawn_subagent_handler creates a background child, and its returned ID can later be passed to wait_for_subagents_handler to collect the follow-up result.

*Call graph*: 4 external calls (__init__, __init__, dumps, UUID).


### `extensions/todos/ufo_ext_todos.py`

`domain_logic` · `request handling`

This file gives the agent a visible progress board, like a checklist on the side of a workbench. Without it, the agent could still do tasks, but there would be no durable, structured way for the system or user interface to show what has been planned, what is underway, and what is finished.

The file defines two tools. The first, `update_todo_list`, creates or replaces the whole checklist. The second, `update_todo_status`, changes the status of individual items, such as moving a task from `pending` to `in_progress` or `completed`. The checklist is stored in the extension’s own store, keyed by the conversation ID, so each conversation gets its own saved board.

The data shapes are defined with Pydantic models, which are validation classes that check incoming data has the expected fields and allowed values. The stored board has a title and a list of tasks. Status updates use 1-based task numbers, meaning the first task is index 1, matching how people usually number lists.

A key safety behavior is that status updates are refused if no list exists yet, or if the requested task number is outside the list. Each tool returns the current board as JSON text, so the caller immediately sees the latest checklist state.

#### Function details

##### `_require_ext`  (lines 82–85)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This helper makes sure the tool call has access to the todos extension context. That context is needed because it contains the extension’s saved store.

**Data flow**: It receives the tool call context. If the extension context is missing, it stops with an error; otherwise it returns the extension context so the caller can read or write the todo board.

**Call relations**: `update_todo_list` and `update_todo_status` call this before touching saved todo data. It acts like checking that the toolbox is actually available before trying to use tools from it.

*Call graph*: called by 2 (update_todo_list, update_todo_status).


##### `_board_key`  (lines 88–89)

```
def _board_key(ctx: ToolContext) -> str
```

**Purpose**: This helper builds the storage key used to save and find the todo board for the current conversation. It keeps different conversations from overwriting each other’s checklists.

**Data flow**: It reads the conversation ID from the tool context, adds the `todo/` prefix, and returns the resulting string as the store key.

**Call relations**: `update_todo_list` uses this key when saving a new board, and `update_todo_status` uses it when looking up and saving changes to an existing board.

*Call graph*: called by 2 (update_todo_list, update_todo_status).


##### `_board_result`  (lines 92–93)

```
def _board_result(board: TodoBoard) -> ToolResult
```

**Purpose**: This helper turns a todo board into the standard tool result returned to the agent. It packages the board as JSON text so the current checklist state is visible after each tool call.

**Data flow**: It receives a `TodoBoard`, converts it into a JSON string, wraps that string as text content, and returns it inside a tool result object.

**Call relations**: Both `update_todo_list` and `update_todo_status` call this at the end. After they create or change the board, this function prepares the response that shows the updated board.

*Call graph*: called by 2 (update_todo_list, update_todo_status); 3 external calls (__init__, __init__, model_dump_json).


##### `_read_board`  (lines 96–98)

```
async def _read_board(ext: ExtensionContext, key: str) -> TodoBoard | None
```

**Purpose**: This helper loads the saved todo board from the extension store, if one exists. It also validates the saved data back into the expected board shape.

**Data flow**: It receives the extension context and a storage key. It asks the store for the saved value; if nothing is found, it returns `None`. If data is found, it converts that data into a `TodoBoard` object and returns it.

**Call relations**: `update_todo_status` calls this before applying status changes. That update flow needs the existing board first, because it edits individual tasks rather than replacing the whole list.

*Call graph*: called by 1 (update_todo_status).


##### `update_todo_list`  (lines 101–105)

```
async def update_todo_list(ctx: ToolContext, args: UpdateTodoListInput) -> ToolResult
```

**Purpose**: This is the tool that creates or fully replaces the checklist for a conversation. It is meant to be called at the start of a multi-step task, or whenever the plan needs to be rewritten.

**Data flow**: It receives the tool context and the requested list title, tasks, and user-facing description. It checks that the extension store is available, builds a new `TodoBoard`, saves it under the conversation-specific key, and returns the saved board as JSON text.

**Call relations**: This is one of the public tools exposed by `manifest`. In its work, it calls `_require_ext` to get storage access, `_board_key` to choose where to save the board, and `_board_result` to send the current board back to the caller.

*Call graph*: calls 3 internal fn (_board_key, _board_result, _require_ext); 2 external calls (__init__, loads).


##### `update_todo_status`  (lines 108–119)

```
async def update_todo_status(ctx: ToolContext, args: UpdateTodoStatusInput) -> ToolResult
```

**Purpose**: This is the tool that changes the status of one or more existing checklist items. It is used while work is happening, for example to mark a task as started or completed.

**Data flow**: It receives the tool context and one or more status updates. It checks for the extension store, builds the conversation-specific key, reads the saved board, rejects the request if no board exists, checks each task number is valid, changes the matching task statuses, saves the board again, and returns the updated board as JSON text.

**Call relations**: This is the second public tool exposed by `manifest`. It depends on `_read_board` to fetch the existing checklist, `_board_key` to find the right conversation’s board, `_require_ext` to access storage, and `_board_result` to return the new state.

*Call graph*: calls 4 internal fn (_board_key, _board_result, _read_board, _require_ext); 1 external calls (loads).


##### `manifest`  (lines 122–141)

```
def manifest() -> Manifest
```

**Purpose**: This function describes the extension to the host system. It says what the extension is called, which tools it offers, what input each tool expects, and what prompt guidance should be added.

**Data flow**: It uses the file’s constants, tool descriptions, input models, handler functions, and markdown prompt section text to build and return a `Manifest` object. The manifest is the registration card the host reads to know how to use this extension.

**Call relations**: The host system calls this when loading the extension. Through the manifest, it learns about `update_todo_list` and `update_todo_status` and can later call those functions when the agent uses the todo tools.

*Call graph*: 3 external calls (__init__, __init__, __init__).
