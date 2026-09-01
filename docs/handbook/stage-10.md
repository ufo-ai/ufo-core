# Tool Dispatch, Sandboxed Work, and External Actions  `stage-10`

This stage is the agent’s action dispatcher. It sits in the main work loop, after the model asks to do something, and turns that request into a safe, specific tool run. Built-in runtime tools handle local work such as shell commands, file edits, asking the user, sharing files, and spawning helper agents, while sandbox rules keep that work inside approved boundaries. Connector and credential tools let the agent use outside services without handing private access keys to ordinary code. Browser, research, site, and document tools provide a workbench for web pages, publishing, searches, PDFs, and Office files. Workspace object tools update saved records such as tasks, monitors, prompts, skills, and todos.

The bridge files connect these parts. tool_bridge.py lets code inside a sandbox request approved tools through the live parent turn, so permissions and history stay intact. runtime/tools/bridge.py defines the message format for listing, describing, and running those bridged tools. harness/tools.py batches ordered work safely. runtime/tools/__init__.py marks the shared tool package and its purpose.

## Sub-stages

- [Built-In Runtime Tools and Workspace Files](stage-10.1.md) `stage-10.1` — 10 files
- [Connectors, Credentials, and Provider Tool Brokers](stage-10.2.md) `stage-10.2` — 20 files
- [Browser, Research, Sites, and Document Automation](stage-10.3.md) `stage-10.3` — 55 files
- [Workspace Object Mutations and Domain Tools](stage-10.4.md) `stage-10.4` — 7 files

## Files in this stage

### Sandbox tool orchestration
The sandbox-facing bridge lets in-sandbox code request approved tool listing, inspection, and execution through the live parent turn.

### `core/src/ufo/runtime/tool_bridge.py`

`orchestration` · `request handling during a live sandbox turn`

A sandboxed run cannot simply call any system tool directly. It needs a safe bridge that checks what the current agent is allowed to use, records the request, sends it through the same turn loop as other work, and waits for the final answer. This file provides that bridge.

The main piece is `ToolBridge`. Given a sandbox run and a bridge request, it first looks up the parent turn and confirms it is still running. If the request only asks to list tools or fetch a tool schema, it answers directly after filtering by permissions. If the request asks to call a tool, it creates a new child conversation and turn in the database. That new turn contains the requested tool name and arguments, and it is created with a stable identifier so retrying the same request does not create duplicates.

After writing the turn, the bridge asks DBOS, the background workflow system, to run it. Then it tails, or watches, the turn until it finishes. If the child turn succeeds, its final text is parsed as JSON when possible and returned to the sandbox. If the turn fails or parks, the bridge returns a clear failure. In short, this file is like a front desk: it checks badges, writes down the request, sends it to the right queue, and waits for the official result.

#### Function details

##### `ToolBridge.request`  (lines 65–99)

```
async def request(self, run: RunToken, request: ToolBridgeRequest) -> ToolBridgeResponse
```

**Purpose**: This is the main entry point for a sandbox bridge request. It decides whether the sandbox is asking to list tools, inspect one tool's input shape, or actually run a tool, and it applies the required permission checks before doing anything.

**Data flow**: It receives a `RunToken`, which identifies the live sandbox run, and a `ToolBridgeRequest`, which says what the sandbox wants. It looks up the parent turn, filters or validates the requested tool, and either returns a success or failure immediately for listing/schema requests, or creates and queues a child turn for actual tool execution. The final output is a `ToolBridgeResponse` containing either a result or an error message.

**Call relations**: This function coordinates the whole bridge flow. It calls `_parent` to confirm the parent turn is live, `_allowed` to check access, `_admit` to record a callable tool request as a new turn, `_enqueue` to send that turn to the workflow queue, and `_terminal` to wait for the turn's final result.

*Call graph*: calls 5 internal fn (_admit, _allowed, _enqueue, _parent, _terminal); 5 external calls (__init__, __init__, __init__, __init__, __init__).


##### `ToolBridge._parent`  (lines 101–130)

```
async def _parent(self, run: RunToken) -> sa.Row[tuple[object, ...]] | None
```

**Purpose**: This function finds the currently running parent turn for the sandbox run. The bridge needs this because the parent turn carries the agent, conversation, and permission context used to decide what the sandbox may do.

**Data flow**: It receives a `RunToken` with a workspace ID and turn ID. It opens a database transaction, joins the turn, agent, and conversation records, and only accepts the turn if its status is `RUNNING`. It returns the matching database row, or `None` if the parent turn is no longer running.

**Call relations**: `request` calls this first. If `_parent` returns nothing, the bridge stops early, because there is no live authority under which to list or run tools.

*Call graph*: called by 1 (request); 2 external calls (select, workspace_tx).


##### `ToolBridge._allowed`  (lines 132–144)

```
def _allowed(self, parent: sa.Row[tuple[object, ...]], tool: ToolDef) -> bool
```

**Purpose**: This function answers the question, “Is this specific tool visible and callable for this parent turn?” It protects the system from exposing tools that the current agent or subagent should not have.

**Data flow**: It receives the parent turn's database row and a tool definition. It checks special action-related tools first, then checks the parent agent's tool list or the subagent profile's allowed tools, including any implied grants. It returns `True` if the tool is allowed and `False` otherwise.

**Call relations**: `request` uses this while listing tools, fetching a schema, and before admitting a call. For action-related tools it delegates to `_any_action_granted`, because those tools depend on whether at least one bound action is available.

*Call graph*: calls 1 internal fn (_any_action_granted); called by 1 (request); 1 external calls (with_implied_grants).


##### `ToolBridge._any_action_granted`  (lines 146–162)

```
def _any_action_granted(self, parent: sa.Row[tuple[object, ...]]) -> bool
```

**Purpose**: This function checks whether the parent agent or subagent has access to any registered object action. Object actions are special callable operations, so the bridge only exposes action tools when there is at least one real action the caller could use.

**Data flow**: It reads the bridge's bound action registry and the parent turn's tool or subagent profile settings. It expands those tool names with implied permissions, then compares them with the canonical action IDs. It returns a simple yes-or-no answer.

**Call relations**: `_allowed` calls this when deciding whether to show or permit the general object-action tool and related read tools. This keeps action permission logic in one place instead of mixing it into every tool check.

*Call graph*: called by 1 (_allowed); 1 external calls (with_implied_grants).


##### `ToolBridge._admit`  (lines 164–259)

```
async def _admit(self, run: RunToken, parent: sa.Row[tuple[object, ...]], request: ToolBridgeRequest) -> tuple[UUID, UUID] | None
```

**Purpose**: This function records a real bridge tool call as a new child turn in the database. “Admit” here means the request has passed initial checks and is now officially entered into the turn-processing system.

**Data flow**: It receives the sandbox run, the parent turn row, and the request. It creates stable IDs for a child conversation and child turn, packages the tool name and arguments into an intent, verifies again that the parent turn is still running, inserts the conversation and turn if they do not already exist, and marks the queued turn as ready to dispatch. It returns the new turn ID and conversation ID, or `None` if the parent turn stopped before admission finished.

**Call relations**: `request` calls this only for actual tool calls, after permission checks pass. It uses database transactions to make retries safe: if the same request ID is reused with different contents, it raises an error rather than silently mixing up two calls.

*Call graph*: called by 1 (request); 8 external calls (__init__, TypeAdapter, select, update, workspace_tx, current_traceparent, turn_id_for, uuid5).


##### `ToolBridge._enqueue`  (lines 261–289)

```
async def _enqueue(self, workspace_id: UUID, turn_id: UUID, conversation_id: UUID) -> None
```

**Purpose**: This function asks the background workflow system to run the child turn created by `_admit`. It is the handoff from “the request is written down” to “the worker should process it.”

**Data flow**: It receives the workspace ID, turn ID, and conversation ID. It builds enqueue options naming the queue, workflow, workflow ID, and app version, then asks DBOS to start the workflow. If the enqueue is cancelled or fails, it clears the turn's dispatch timestamp so another dispatcher can try later; on ordinary failure it also logs what happened.

**Call relations**: `request` calls this after `_admit` has created the queued turn. It does not produce the tool result itself; it only starts or schedules the work, then `request` moves on to `_terminal` to wait for the outcome.

*Call graph*: called by 1 (request); 3 external calls (update, workspace_tx, log).


##### `ToolBridge._terminal`  (lines 291–300)

```
async def _terminal(self, turn_id: UUID) -> ToolBridgeResponse
```

**Purpose**: This function watches the child turn until it reaches an ending state and converts that ending into a bridge response. It is how the sandbox gets a synchronous-looking answer from work that actually ran through the turn loop.

**Data flow**: It receives a turn ID and opens a tail, meaning a live stream of updates for that turn. As frames arrive, it waits for either a terminal frame, meaning the turn finished, or a parked frame, meaning it cannot continue without intervention. A terminal frame is passed to `_response`; a parked frame causes the turn to be cancelled and returns a failure message.

**Call relations**: `request` calls this after enqueueing the child turn. It delegates final result formatting to `_response`, and it calls `cancel_one_turn` if the child turn parks so the bridge call does not leave stuck work behind.

*Call graph*: calls 1 internal fn (_response); called by 1 (request); 2 external calls (__init__, cancel_one_turn).


##### `ToolBridge._response`  (lines 302–314)

```
def _response(self, terminal: TerminalFrame) -> ToolBridgeResponse
```

**Purpose**: This function turns a finished child turn into the success or failure object returned to the sandbox. It hides the internal terminal-frame shape and gives the caller a clean bridge response.

**Data flow**: It receives a terminal frame. If the frame status is not `done`, it builds an error message from the terminal's error fields or text and returns a failure. If the status is `done`, it tries to parse the terminal text as JSON, falls back to plain text if parsing fails, validates that the result is a JSON-compatible value, and returns success.

**Call relations**: `_terminal` calls this when the watched turn finishes. This is the last step in the bridge flow before the result goes back through `request` to the sandbox caller.

*Call graph*: called by 1 (_terminal); 4 external calls (__init__, __init__, loads, TypeAdapter).


### Work batching utility
The harness helper preserves ordered work while safely batching neighboring items that can run together.

### `core/src/ufo/harness/tools.py`

`util` · `cross-cutting`

This file solves a common coordination problem: some tasks can be run side by side, but others must be kept separate, and the original order still matters. Think of it like sorting people into elevator rides: groups can ride together only if everyone in that stretch is allowed, the elevator has a size limit, and anyone who needs a private ride gets one.

The main helper, `dispatch_segments`, walks through a tuple of items from left to right. For each item, it asks a caller-provided test function whether that item is “parallel-safe,” meaning it is allowed to be grouped with other safe items. Consecutive safe items are collected into batches, but each batch is capped by a `limit`. When the helper reaches an unsafe item, it first gives back any waiting safe batch, then gives back the unsafe item by itself.

The important behavior is that it never reorders anything. It only decides where to place batch boundaries. Without this helper, callers that want parallel execution would each need to carefully duplicate this batching logic, increasing the risk of accidentally changing call order or grouping something that should run alone.

#### Function details

##### `dispatch_segments`  (lines 4–23)

```
def dispatch_segments(items: tuple[ItemT, ...], *, parallel_safe: Callable[[ItemT], bool], limit: int) -> Iterator[tuple[ItemT, ...]]
```

**Purpose**: Splits an ordered tuple of items into smaller tuples that can be dispatched safely. Consecutive items that pass the `parallel_safe` test are grouped together up to the given `limit`, while unsafe items are returned alone.

**Data flow**: It receives a tuple of items, a `parallel_safe` function that answers yes or no for each item, and a maximum batch size. It scans the items in order, builds a temporary batch of safe items, emits that batch when it is full or when an unsafe item appears, and emits unsafe items as one-item batches. The result is an iterator that yields tuples, preserving the original item order; if `limit` is less than 1, it raises an error instead of producing invalid batches.

**Call relations**: This is a standalone helper that other parts of the harness can call when they are preparing work for dispatch. It does not call into the rest of the project; instead, it relies on the caller’s `parallel_safe` test to decide what may be grouped, then hands back ready-to-use ordered segments for the caller to run or schedule.


### Runtime bridge contracts
The runtime tools package defines the bridge-facing package boundary, shared JSON request shapes, and exposed tool-name set.

### `core/src/ufo/runtime/tools/__init__.py`

`other` · `import time / cross-cutting`

This file does not define any executable code. Its job is to give the surrounding package a clear identity. In Python, an `__init__.py` file tells Python that a folder should be treated as an importable package. Here, the short module comment explains that this package is the home for the runtime tool contract: the shared rules for what a tool looks like, the context needed when a tool is dispatched, and the registry that maps tool names or wire-level messages to actual tool behavior. Think of it like the label on a drawer: the drawer may contain several important parts, and this label tells readers what kind of parts belong there. Without this file, the package would be less self-documenting, and depending on the Python packaging setup, imports of this tools package could be less clear or less reliable.


### `core/src/ufo/runtime/tools/bridge.py`

`io_transport` · `live-turn tool bridge setup and request handling`

This file is the rulebook for a small doorway between a live run sandbox and the system’s tools. The sandbox cannot just call any Python function directly. Instead, it sends a structured request, like “list tools” or “execute this tool with these arguments,” and receives a structured success or failure response. These request and response shapes are defined here with Pydantic models, which are Python classes that check incoming data before the rest of the system trusts it.

The file also names the bridge’s built-in tools, such as object listing, object editing, and gateway tools for external connectors. Think of this like a service desk menu: the caller may ask for only the services printed on the menu, and the request must include the right details for that service.

`ToolBridgeRequest` makes sure each request makes sense. A list request must not sneak in a tool name or arguments, while schema and execute requests must say which tool they mean. `ToolBridgeRequester` describes the interface for something that can perform these requests under a signed live-run identity. Finally, `bridge_tools` assembles the actual callable tools by combining object-related verbs with allowed unbound tools from extension manifests, then validates that set by putting it through the tool registry.

#### Function details

##### `ToolBridgeRequest._matches_action`  (lines 52–58)

```
def _matches_action(self) -> 'ToolBridgeRequest'
```

**Purpose**: This checks that a bridge request is internally consistent before it is accepted. It prevents confusing requests, such as asking to list all tools while also naming one specific tool.

**Data flow**: It starts with a parsed `ToolBridgeRequest`, including its action, optional tool name, and argument dictionary. If the action is `list`, it confirms there is no tool name and no arguments; if the action is `get_schema` or `execute`, it confirms a tool name is present. It returns the same request when valid, or raises an error when the fields do not match the action.

**Call relations**: Pydantic calls this validator automatically after it has read the request fields. Its job is to stop bad bridge messages at the boundary, before any later code tries to look up or run a tool.


##### `ToolBridgeRequester.request`  (lines 92–92)

```
async def request(self, run: RunToken, request: ToolBridgeRequest) -> ToolBridgeResponse
```

**Purpose**: This defines the promise that any bridge requester must keep: given a live run identity and a tool bridge request, it must return either a success result or a failure message. It is an interface, not an implementation.

**Data flow**: The inputs are a `RunToken`, which represents the signed authority for one live run, and a `ToolBridgeRequest`, which says what the sandbox wants. A concrete implementation will use those inputs to contact or perform the bridge action, then produce a `ToolBridgeSuccess` with a JSON result or a `ToolBridgeFailure` with an error string.

**Call relations**: Other code can depend on this protocol without caring which concrete requester is used underneath. The real requester implementation supplies the body; this file only states the shape of the call and the kind of answer callers should expect.


##### `bridge_tools`  (lines 95–111)

```
def bridge_tools(manifests: tuple[Manifest, ...]) -> tuple[ToolDef, ...]
```

**Purpose**: This builds the list of tools that the bridge is allowed to expose. It combines the system’s object tools with approved connector gateway tools from extension manifests, while deliberately excluding bound action tools that must be reached through `object_action` instead.

**Data flow**: It receives a tuple of extension `Manifest` objects, each of which may declare tools and connector tools. It first asks `ObjectVerbs` for the standard object-related tools. Then it walks through the manifests and keeps only tools that are unbound and whose names are part of the bridge’s approved name set. It creates a combined tuple, passes it into `ToolRegistry` so the set is checked as a valid registry, and returns the tuple.

**Call relations**: This function is used when the bridge’s callable menu is being assembled. It calls `ObjectVerbs.__init__` to create the object-tool provider and `ToolRegistry.__init__` as a validation step, so the final bridge tool list is both complete and acceptable to the normal tool registry rules.

*Call graph*: 2 external calls (__init__, __init__).

## 📊 State Registers Touched

- `reg-selected-pack-services` — The chosen product pack and the shared service objects it wires up for the rest of the app.
- `reg-workspace-member-agent-state` — The saved list of workspaces, people, memberships, seats, and agents.
- `reg-agent-configuration` — Each agent’s saved settings, such as model choice, reasoning mode, tools, visibility, internet access, sandbox size, and setup needs.
- `reg-extension-registry` — The loaded set of extensions and the routes, tools, hooks, jobs, skills, agents, and backends they contribute.
- `reg-extension-install-store` — The saved record of which extensions are installed, removed, or holding extension-specific data.
- `reg-credential-connections` — The encrypted accounts, secrets, connection grants, and credential fulfillments that let agents use outside services safely.
- `reg-access-permissions-audience` — The shared rules for who may read, use, share, or act on workspace content and conversations.
- `reg-egress-policy-proxy` — The network allowlist and proxy state that decide which outside hosts sandboxed work may contact.
- `reg-billing-spend-ledger` — The shared accounting state for spend caps, usage charges, prepaid balances, BYOK billing, and ledger exports.
- `reg-feature-flags` — The rollout switches that turn product and infrastructure behavior on or off across the system.
- `reg-search-provider-catalog` — The common search and page-fetching service state used when the system needs outside web information.
- `reg-memory-index-state` — The stored knowledge, embeddings, chunks, and memory indexes that agents can search later.
- `reg-source-config-sync-state` — The configured external sources plus their sync progress, errors, backoff, ownership, and access grants.
- `reg-tool-catalog-allowlists` — The shared list of tools and actions an agent may see or run, including extension tools and sandbox bridge tools.
- `reg-skill-prompt-library` — The reusable instructions, skills, prompt rules, and agent setup guidance loaded into turns.
- `reg-conversation-turn-queue` — The durable state of conversations and turns, including admission, ordering, current runner, lifecycle status, and queued work.
- `reg-live-turn-stream` — The live event feed that lets clients and other processes watch a running turn and learn how it ended.
- `reg-sandbox-runtime` — The durable sandbox and browser workspace handles where agent commands, files, web browsing, and hosted previews run safely.
- `reg-blob-artifact-store` — The shared file, blob, artifact, preview, download, and hosted media storage used by turns and surfaces.
- `reg-presentation-slots` — The shared conversation display slots for showing artifacts, sources, tasks, sites, automations, image previews, and other side-panel content.
- `reg-delegation-workflows` — The saved state for subagents, parent-child turns, objectives, workflow checkpoints, pending deliveries, and recovery.
- `reg-object-journal` — The shared naming and change history for workspace objects such as tasks, prompts, skills, monitors, memories, and reports.
- `reg-observability-trace` — The logs, metrics, traces, health signals, and trace links used to understand what the system is doing.
- `reg-active-turn-cancellation-handles` — The in-process registry of currently running turn/workflow tasks and cancellation handles used to stop active work before marking it cancelled durably.
- `reg-conversation-workspace-change-state` — The persisted record of file/workspace changes detected for a conversation sandbox, used for commit summaries, artifact presentation, recovery, and debugging.
- `reg-workspace-object-store` — The current persisted workspace object records, such as tasks, monitors, todos, reports, prompts, and site metadata, read and mutated through object APIs, tools, jobs, and slots.
- `reg-turn-runtime-snapshot` — The per-turn frozen runtime configuration and generated references used to run, recover, bill, and debug a turn consistently after settings change.
- `reg-execution-step-log` — The structured persisted model, tool, and workflow execution records that power debugger timelines and post-run inspection beyond the user transcript.
- `reg-sandbox-template-build-cache` — The local Docker image and E2B template build/version state that sandbox launch code relies on to create compatible runtimes.
- `reg-support-feedback-reports` — The buffered debugger/support reports emitted by agents or operators and later delivered to or inspected by engineering.
- `reg-proposal-approval-state` — Persisted proposed changes with before/after payloads, authoring information, and pending/approved/rejected status used for review and application workflows.
- `reg-provider-rate-limit-budgets` — Shared per-provider throttle, retry, and backoff budget state for model, search, connector, and external API calls so workers avoid overrunning provider limits.
- `reg-evaluation-fixture-backends` — Deterministic fake connector/backend data for evaluation packs, such as mailbox, calendar, code-search, and business records used across test routes and tools.
