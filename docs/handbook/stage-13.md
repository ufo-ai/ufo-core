# Higher-level agent workflows and extension domain features  `stage-13`

This stage sits above the basic tools and helps the assistant run larger jobs that take planning, waiting, delegation, or repeated checking. It is part main work loop and part behind-the-scenes support, like a project manager layer on top of simple actions.

The planning and automation pieces give agents a shared notebook for objectives, an alarm clock for scheduled work, and a watchman for monitors. They record plans, blockers, delegated tasks, paused conversations, and recurring jobs so progress survives beyond one chat message. The creation workflow pieces turn plans into outputs: they help build and publish websites, manage who can view them, hand web work to specialist agents, and split research across parallel workers.

The brief pipeline adds a simple writing assembly line: outline first, draft second, critique third, with clear input and output rules for each step. The todos extension gives a conversation a visible checklist, so both the assistant and user can see what is done, in progress, or still waiting.

## Sub-stages

- [Planning, delegation, automations, and monitors](stage-13.1.md) `stage-13.1` — 13 files
- [Creation workflows for sites, documents, code, and research](stage-13.2.md) `stage-13.2` — 6 files

## Files in this stage

### Brief Writing Pipeline
Defines the brief pipeline package and its structured outline, drafting, and critique workflow.

### `extensions/brief_pipeline/ufo_ext_brief_pipeline/__init__.py`

`other` · `import time`

This is the package marker for the `ufo_ext_brief_pipeline` extension. In Python, an `__init__.py` file tells Python that a folder should be treated as an importable package, like putting a label on a drawer so the rest of the program knows it can look inside. Here, the only content is a short documentation string saying this is the “Brief pipeline extension.” That means the actual behavior of the extension lives in other files, while this file provides the package identity. Without it, some Python environments or tooling might not recognize this directory as a package, which could make imports fail or make the extension harder to discover.


### `extensions/brief_pipeline/ufo_ext_brief_pipeline/pipeline.py`

`config` · `extension load / pipeline setup`

This file is like a recipe card for a small writing assembly line. The goal is to turn a topic into a useful brief by passing work through three specialist subagents: one creates an outline, one writes the draft, and one reviews the result. A subagent is a smaller AI worker with its own instructions and expected input and output.

The file first names the three stages and sets a shared round limit, which keeps each stage from going back and forth forever. It then points to a local prompts folder, where the written instructions for each subagent live.

The small Pydantic models define the “forms” each stage must receive and return. Pydantic is a Python library that checks data has the expected fields. For example, the outline stage receives a topic and audience, then returns an outline. The draft stage receives the topic plus outline, then returns a draft. The critic stage receives the draft, then returns a verdict and optional improvements.

At the bottom, the file builds three SubagentProfile objects. Each profile combines a name, a prompt file, no tools, an input model, an output model, and the round limit. These profiles do not run the pipeline by themselves; they describe the workers so a parent agent can start them in order and pass each result to the next stage.


### Todo Checklist Extension
Implements conversation-visible todo lists so agents can create, update, and display task progress.

### `extensions/todos/ufo_ext_todos.py`

`domain_logic` · `extension load, tool calls, and conversation state display`

This extension gives the agent a simple shared checklist, like a small whiteboard beside a project. Without it, a long multi-step request would have no durable task board: the agent could still talk about plans, but the UI would not have a reliable list of tasks and statuses to show.

The file defines the shape of todo data: each task has text and a status, and a board has a title plus a list of tasks. It also defines two tools the agent can call. The first, update_todo_list, creates or fully replaces the board. The second, update_todo_status, changes the status of existing tasks by their 1-based position in the list.

The board is stored in the extension's scoped store, keyed by conversation ID. That means the checklist is not just temporary text in one answer; later turns in the same conversation can read the same board back. Each tool returns the full current board as JSON, so the agent sees exactly what was saved.

The file also exposes the checklist as a conversation slot, which is a structured piece of conversation state the product can display. When reading for display, it trims overly long titles, descriptions, or task lists and marks the result as truncated so the UI knows it is not showing everything.

#### Function details

##### `_require_ext`  (lines 93–96)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This helper makes sure a tool call has an extension context available. The extension context is the object that gives access to the extension's private stored data.

**Data flow**: It receives a tool context. If that context contains an extension context, it returns it. If not, it stops the call by raising an error, because the todo tools cannot save or read their board without that storage access.

**Call relations**: Both update_todo_list and update_todo_status call this at the start. It acts like checking that the notebook is on the desk before trying to write or edit the checklist.

*Call graph*: called by 2 (update_todo_list, update_todo_status).


##### `_board_key`  (lines 99–100)

```
def _board_key(conversation_id: UUID) -> str
```

**Purpose**: This helper builds the storage key for one conversation's todo board. It keeps each conversation's checklist separate from every other conversation.

**Data flow**: It receives a conversation ID. It combines a fixed prefix with that ID and returns a string key that can be used to read or write the board in the extension store.

**Call relations**: The write, update, summarize, and read paths all use this helper before touching stored todo data. That keeps every part of the file using the same naming rule for saved boards.

*Call graph*: called by 4 (_read_tasks, _summarize_tasks, update_todo_list, update_todo_status).


##### `_board_result`  (lines 103–104)

```
def _board_result(board: TodoBoard) -> ToolResult
```

**Purpose**: This helper turns a todo board into the standard tool response returned to the agent. It makes sure tool callers receive the current checklist after each change.

**Data flow**: It receives a TodoBoard object. It converts the board into JSON text, wraps that text in a TextContent object, then wraps that content in a ToolResult and returns it.

**Call relations**: update_todo_list and update_todo_status call this after saving changes. It is the final packaging step that hands the updated board back to the model.

*Call graph*: called by 2 (update_todo_list, update_todo_status); 3 external calls (__init__, __init__, model_dump_json).


##### `_read_board`  (lines 107–109)

```
async def _read_board(ext: ExtensionContext, key: str) -> TodoBoard | None
```

**Purpose**: This helper reads a saved todo board from the extension store. It returns a validated board object if one exists, or nothing if the conversation has no board yet.

**Data flow**: It receives the extension context and a storage key. It asks the store for the saved value at that key. If nothing is stored there, it returns None; otherwise, it checks the saved data against the TodoBoard shape and returns the resulting board object.

**Call relations**: update_todo_status uses this before editing tasks, while _summarize_tasks and _read_tasks use it when preparing conversation state for display. It is the shared doorway from stored data back into usable todo objects.

*Call graph*: called by 3 (_read_tasks, _summarize_tasks, update_todo_status).


##### `update_todo_list`  (lines 112–116)

```
async def update_todo_list(ctx: ToolContext, args: UpdateTodoListInput) -> ToolResult
```

**Purpose**: This is the tool that creates or replaces the whole checklist for a conversation. An agent uses it at the start of multi-step work, or when the plan needs to be rewritten.

**Data flow**: It receives the tool context and input containing a title, tasks, and a short user-facing description. It checks that extension storage is available, builds a new TodoBoard, stores it under the current conversation's key, and returns the full saved board as JSON in a tool result.

**Call relations**: This is one of the public tools registered by manifest. It calls _require_ext to get storage access, _board_key to choose where to save the board, and _board_result to return the saved checklist to the agent.

*Call graph*: calls 3 internal fn (_board_key, _board_result, _require_ext); 2 external calls (__init__, loads).


##### `update_todo_status`  (lines 119–130)

```
async def update_todo_status(ctx: ToolContext, args: UpdateTodoStatusInput) -> ToolResult
```

**Purpose**: This is the tool that changes the status of one or more existing tasks. An agent uses it to mark work as pending, in progress, or completed as the request moves forward.

**Data flow**: It receives the tool context and a set of requested status updates. It checks for extension storage, finds the current conversation's board, refuses to continue if no board exists, validates that each requested task number is within the list, changes the selected task statuses, saves the updated board, and returns the full updated board as JSON.

**Call relations**: This is the second public tool registered by manifest. It relies on _read_board to load the current checklist, _board_key to locate it, and _board_result to send the updated state back. Its validation protects the stored board from edits to missing or nonexistent tasks.

*Call graph*: calls 4 internal fn (_board_key, _board_result, _read_board, _require_ext); 1 external calls (loads).


##### `_summarize_tasks`  (lines 133–135)

```
async def _summarize_tasks(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: This helper gives a quick summary of whether a conversation has tasks and how many there are. It is used for the conversation slot summary rather than for editing the board.

**Data flow**: It receives a conversation slot context, builds the storage key for that conversation, and reads the board. If there is no board, it returns None. If a board exists, it returns the number of tasks on it.

**Call relations**: TASKS_SLOT uses this as its summarize function. It shares the same _board_key and _read_board helpers as the tool paths, so the summary reflects the same stored checklist the tools edit.

*Call graph*: calls 2 internal fn (_board_key, _read_board).


##### `_read_tasks`  (lines 138–168)

```
async def _read_tasks(ctx: ConversationSlotContext) -> TasksSlotPayload
```

**Purpose**: This helper prepares the todo board for display as structured conversation state. It turns the stored board into a UI-friendly payload with counts, task statuses, and truncation information.

**Data flow**: It receives a conversation slot context, reads the board for that conversation, and returns an empty task payload if none exists. If a board exists, it copies up to the allowed number of tasks, shortens any title or description that is too long, counts completed tasks, notes whether anything was trimmed, and returns a TasksSlotPayload.

**Call relations**: TASKS_SLOT uses this as its read function when the system wants the current task list for display. It calls _board_key and _read_board to get the stored board, then creates ConversationTask and TasksSlotPayload objects for the UI-facing version.

*Call graph*: calls 2 internal fn (_board_key, _read_board); 2 external calls (__init__, __init__).


##### `manifest`  (lines 181–203)

```
def manifest() -> Manifest
```

**Purpose**: This function tells the host application what this extension provides. It registers the todo tools, the prompt instructions, and the conversation slot used to show tasks.

**Data flow**: It takes no input. It builds and returns a Manifest containing the extension name and version, two tool definitions, one prompt section loaded from a markdown file, and the tasks conversation slot.

**Call relations**: The host calls this when loading the extension. The manifest points tool calls to update_todo_list and update_todo_status, includes the prompt text that teaches the agent how to use the checklist, and exposes TASKS_SLOT so the stored board can appear in conversation state.

*Call graph*: 3 external calls (__init__, __init__, __init__).

## 📊 State Registers Touched

- `reg-extension-catalog` — The shared list of installed extensions and the capabilities they registered for this deployment.
- `reg-db-session` — The active database connection, transaction, and workspace-safe persistence context used while work is running.
- `reg-agent-definitions` — The saved assistant agents for each workspace, including their settings, tools, model choices, and provisioning source.
- `reg-identity-context` — The current answer to who is acting, in which workspace, and on behalf of which member or agent.
- `reg-tool-registry` — The shared catalog of tools the model is allowed to call and the input rules for each tool.
- `reg-conversation-state` — The durable conversation records, titles, audience, surface labels, sandbox links, and visible thread metadata.
- `reg-transcript-state` — The saved message history and transcript snapshots that are read, compacted, updated, audited, and shown later.
- `reg-turn-queue` — The durable queue of conversation turns, including admitted work, claimed work, failures, retries, and completion state.
- `reg-portal-slots` — The safe display state for conversation panels such as sources, artifacts, tasks, sites, automations, and workspace changes.
- `reg-source-feeds` — The registered external content sources, sync cursors, backoff state, ownership, grants, and wake-up triggers.
- `reg-page-index` — The stored pages, revisions, chunks, embeddings, and search indexes used to find synced knowledge later.
- `reg-memory-store` — The durable remembered facts and notes that agents can search, browse, update, consolidate, and show with provenance.
- `reg-scheduled-work` — The saved jobs, scheduled tasks, pauses, monitors, due times, retry state, and duplicate-run guards.
- `reg-workflow-plans` — The longer-running goals, objective steps, blockers, todos, delegated work, and progress evidence that survive across turns.
- `reg-subagent-delivery` — The parent-child turn links and pending result records used when helper agents run work and report back.
- `reg-prompt-governance` — The saved prompt proposals, approval status, evaluation results, and safety checks for changing agent instructions.
- `reg-extension-kv-store` — Private per-workspace JSON/key-value state owned by extensions for setup, feature bookkeeping, and small durable extension data that is not a user-visible object.
- `reg-coding-review-state` — The coding extension’s durable review inbox and review-run records, including links to the agent, conversation, and turn that handle review automation.
- `reg-hosted-site-state` — Durable hosted website records, publication metadata, permissions, and homepage-agent bindings used to build, serve, list, and remove sites.
- `reg-research-observations` — Durable per-conversation web/search source observations and retrieval metadata saved by research tools for later citation and Sources-panel rendering.
- `reg-skill-workflow-catalog` — The registered agent skills, helper subagent profiles, workflow profiles, and related prompt/activity metadata injected into turns and surfaced to users.
