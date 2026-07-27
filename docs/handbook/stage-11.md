# Delegated subagents and specialized worker turns  `stage-11`

This stage is the system’s way of giving a main agent temporary helpers during a work turn. Instead of doing every task itself, the parent turn can start a child turn, send it a clear job, wait for its answer, send follow-up messages, or cancel it. The core subagents file is the switchboard: it connects a chosen helper profile to saved child-turn records and to the background queue that runs the work. The core profiles file supplies the default “general purpose” helper when no specialist is needed.

Extensions add specialist helpers. The browser files define a browser-capable subagent and delegation tools for one web session or many parallel visits. The research files define normal and deep research workers, plus a batch tool that runs many research jobs and saves their results together. The sites files define a website-building worker and a tool that starts it with a full website request. The brief pipeline files define outline, draft, and critique writing workers. Together, these pieces act like a workshop where the main agent assigns jobs to the right specialist and gathers the finished results.

## Files in this stage

### Core subagent framework
The built-in profile and orchestration layer define how parent turns create, run, communicate with, and stop delegated child turns.

### `core/src/ufo/loop/profiles.py`

`config` · `subagent setup`

This file is the default job description for a child agent. A child agent is like an assistant given one contained task by a parent agent. If the system needs to spawn a helper and no plugin or extension names a special profile, it falls back to this general-purpose profile.

The profile sets clear boundaries. The helper can work in the shared workspace, read and edit files, run shell commands, search where available, load skills, and save results. But it cannot ask the human user questions, create more subagents, message or cancel sibling agents, or approve account connections. In plain terms, it is meant to do the assigned work, not coordinate the whole team.

The file also defines the small data shapes for this exchange. `GeneralPurposeInput` contains the task text the parent gives the helper. `GeneralPurposeOutput` contains the result summary the helper reports back.

The long prompt is the helper’s instruction sheet. It tells the helper to be self-directed, avoid endless retry loops, load relevant skills first, use proper Office file formats when needed, save handoff files in `/workspace`, and return a brief result at the end. Finally, the file packages all of this into `GENERAL_PURPOSE_PROFILE` and exposes it through `CORE_SUBAGENT_PROFILES`, making it available as the core built-in subagent option.


### `core/src/ufo/loop/subagents.py`

`orchestration` · `turn execution`

A subagent is like sending a specialist assistant into a side room with its own instructions, tools, and expected answer shape. This file defines how those specialists are registered, how their prompts are built, and how a parent turn starts and follows their work.

First, `SubagentRegistry` holds the available subagent profiles and makes sure two profiles do not share the same name. A profile says what prompt to use, which tools are allowed, and what input and output data should look like.

`subagent_system_prompt` builds the instruction text the child agent will see. It fills in the skill list, adds any preloaded skill instructions, adds shared citation and formatting rules, and ends with a strict instruction: the child must finish by calling the `finish` tool with output matching its declared schema.

`Subagents` is the runtime helper bound to one parent turn. Its main job is `spawn`: validate the input, create a child conversation and first child turn in the database, enqueue that turn in DBOS (the durable workflow queue), and either return immediately for background work or wait until the child finishes. The child gets its own queue partition, so the parent can wait without blocking the child from running. The class also supports waiting for background children, sending follow-up messages, and canceling child turns safely.

#### Function details

##### `SubagentRegistry.__post_init__`  (lines 77–81)

```
def __post_init__(self) -> None
```

**Purpose**: Checks the registry as soon as it is created and rejects duplicate subagent names. This prevents later confusion where asking for one name could secretly match more than one profile.

**Data flow**: It reads the profile names stored in the registry → counts which names appear more than once → either leaves the registry unchanged or raises an error naming the duplicates.

**Call relations**: This runs automatically after `SubagentRegistry` is constructed. It protects later lookups, especially calls from `Subagents.spawn`, which depend on a profile name pointing to exactly one profile.


##### `SubagentRegistry.get`  (lines 83–90)

```
def get(self, name: str) -> SubagentProfile
```

**Purpose**: Finds the subagent profile with the requested name. If the name is not known, it raises a clear error that also lists the valid choices.

**Data flow**: It receives a profile name → scans the registry’s stored profiles → returns the matching profile, or builds a helpful message and raises `UnknownSubagentProfile` if none match.

**Call relations**: This is the front door for turning a user-facing profile name into the actual profile object. `Subagents.spawn` uses it before starting a child, and `_untrusted_output` uses it to decide whether a completed child’s output should be treated as trusted.

*Call graph*: 1 external calls (__init__).


##### `subagent_system_prompt`  (lines 93–125)

```
def subagent_system_prompt(profile: SubagentProfile, *, skills: Sequence[tuple[str, str]]=CORE_SKILL_INDEX, preload: tuple[LoadedSkill, ...]=()) -> str
```

**Purpose**: Builds the full system prompt, meaning the instruction text that controls a subagent’s behavior. It combines the profile’s own instructions with skill information, shared citation rules, and the final output contract.

**Data flow**: It receives a subagent profile, an optional list of available skills, and optional preloaded skill content → fills the skill-index placeholder, checks that no prompt placeholders were left unresolved, adds preloaded skill text if it is not too large, and appends the shared output rules → returns one complete prompt string.

**Call relations**: This function is used when preparing the child agent’s run. It calls `render_skill_index` to make the readable skill list, `PROMPT_VAR_RE.findall` to catch unfilled template slots, and `loaded_context` to turn preloaded skills into prompt text.

*Call graph*: 3 external calls (findall, render_skill_index, loaded_context).


##### `Subagents.spawn`  (lines 137–181)

```
async def spawn(self, profile: str, payload: dict[str, Any], background: bool=False, dedup_key: str | None=None) -> SpawnResult
```

**Purpose**: Starts a child subagent turn from a parent turn. It can either wait for the child’s final answer or return immediately with the child turn id so the parent can continue other work.

**Data flow**: It receives a profile name, an input payload, a background flag, and an optional deduplication key → looks up the profile, validates the payload against the profile’s input schema, chooses a child conversation id, derives the child turn id, writes the child records, and queues the child turn → returns a `SpawnResult` with either just the child id or the validated final output. If the child fails or returns invalid trusted output, it raises an error.

**Call relations**: This is the main public entry point for creating subagents. It uses `_admit` to create or reconnect to the child database rows, `_enqueue` to ask the workflow queue to run the child, and `_await_terminal` when the caller wants foreground waiting. It uses `uuid5` when a deduplication key should reconnect to the same child on retry, and `uuid4` when each spawn should be fresh.

*Call graph*: calls 3 internal fn (_admit, _await_terminal, _enqueue); 5 external calls (__init__, __init__, turn_id_for, uuid4, uuid5).


##### `Subagents.wait`  (lines 183–202)

```
async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]
```

**Purpose**: Waits for one or more background subagents to finish and reports their final status. A parent uses this after it has started children in the background and later wants their results.

**Data flow**: It receives a tuple of child turn ids → first checks that each one really belongs to this parent turn, then polls each child until it has a terminal result → returns a tuple of `SubagentStatus` objects containing the child id, status, final text, and whether the output should be treated as untrusted.

**Call relations**: This is the companion to background `spawn`. It calls `_require_child` to prevent a parent from inspecting unrelated turns, `_await_terminal` to wait for each child’s durable final record, and `_untrusted_output` to label the child’s text safely.

*Call graph*: calls 3 internal fn (_await_terminal, _require_child, _untrusted_output); 1 external calls (__init__).


##### `Subagents.cancel`  (lines 204–221)

```
async def cancel(self, turn_id: UUID) -> SubagentStatus
```

**Purpose**: Cancels a running child subagent that was spawned by this parent. It is used when the parent no longer wants the child’s work or needs to stop it safely.

**Data flow**: It receives a child turn id → verifies that the turn is this parent’s child → asks the shared cancellation code to cancel the durable workflow → reads the child’s current database status and terminal text if present → returns a `SubagentStatus` summary.

**Call relations**: This public method relies on `_require_child` for safety before calling `cancel_one_turn`, the shared cancellation primitive. After cancellation, it reads the database through `workspace_tx` and turns any stored terminal frame into plain status text.

*Call graph*: calls 1 internal fn (_require_child); 5 external calls (__init__, model_validate, select, cancel_one_turn, workspace_tx).


##### `Subagents.message`  (lines 223–297)

```
async def message(self, turn_id: UUID, text: str) -> SubagentStatus
```

**Purpose**: Sends a follow-up message to a background child subagent by creating the next turn in that child’s own conversation. This lets a parent continue a child’s thread instead of starting a brand-new subagent.

**Data flow**: It receives the existing child turn id and message text → verifies the child belongs to this parent → reads the child conversation and profile, locks the conversation row, calculates the next sequence number, inserts a new queued turn with the message as inbound text, and enqueues it if no earlier turn is already waiting → returns a `SubagentStatus` for the newly queued follow-up.

**Call relations**: This method is used after a background subagent already exists and the parent wants to continue it. It calls `_require_child` first, writes the follow-up inside `workspace_tx`, and calls `_enqueue` only when the new turn is ready to be dispatched without cutting ahead of an earlier queued turn.

*Call graph*: calls 2 internal fn (_enqueue, _require_child); 8 external calls (__init__, exists, insert, select, update, workspace_tx, current_traceparent, turn_id_for).


##### `Subagents._untrusted_output`  (lines 299–305)

```
def _untrusted_output(self, profile: str) -> bool
```

**Purpose**: Decides whether a child subagent’s output should be marked untrusted. If the profile is missing, it chooses the safer answer and treats the output as untrusted.

**Data flow**: It receives a profile name → tries to find that profile in the registry → returns the profile’s `untrusted_output` flag, or returns `true` if the profile no longer exists.

**Call relations**: `Subagents.wait` uses this when reporting background child results. This keeps old or unknown child outputs from being accidentally treated as safe instructions.

*Call graph*: called by 1 (wait).


##### `Subagents._require_child`  (lines 307–318)

```
async def _require_child(self, turn_id: UUID) -> str
```

**Purpose**: Checks that a turn id belongs to a subagent spawned by the current parent turn. This prevents one turn from waiting on, canceling, or messaging someone else’s child.

**Data flow**: It receives a turn id → reads that turn’s parent id and subagent profile from the database → returns the profile name if the parent matches, or raises an error if the turn is missing or unrelated.

**Call relations**: This is a safety gate used by `wait`, `cancel`, and `message`. Those public methods call it before touching child state so their later database and queue actions are limited to the right parent-child relationship.

*Call graph*: called by 3 (cancel, message, wait); 2 external calls (select, workspace_tx).


##### `Subagents._admit`  (lines 320–389)

```
async def _admit(self, conversation_id: UUID, turn_id: UUID, profile: str, inbound: str) -> bool
```

**Purpose**: Creates the database records for a new child subagent conversation and its first turn, or reconnects to existing records if this spawn is being retried. This makes subagent spawning durable and safe to repeat.

**Data flow**: It receives a child conversation id, child turn id, profile name, and serialized input text → opens a workspace transaction, inserts the child conversation if missing, inserts the first queued child turn if missing, reads the child turn status, and marks it as ready for dispatch if it is still queued → returns `true` when the turn should be enqueued, or `false` when it already moved past the queued state.

**Call relations**: `Subagents.spawn` calls this before queueing work. It uses conflict-safe inserts so a retry with the same deduplication key attaches to the existing child instead of creating and billing a duplicate. It also records the current trace context so observability can connect the child run back to the parent run.

*Call graph*: called by 1 (spawn); 4 external calls (select, update, workspace_tx, current_traceparent).


##### `Subagents._enqueue`  (lines 391–420)

```
async def _enqueue(self, turn_id: UUID, conversation_id: UUID) -> None
```

**Purpose**: Asks DBOS, the durable workflow queue, to run a queued turn. It also cleans up the database marker if enqueueing is interrupted or fails, so another dispatcher can try again later.

**Data flow**: It receives a turn id and conversation id → builds queue options including the queue name, workflow name, workflow id, partition key, and app version → calls the DBOS client to enqueue the workflow. If the call is canceled or errors, it clears the turn’s dispatch timestamp for still-queued turns; on ordinary errors it also logs that enqueueing was deferred.

**Call relations**: `Subagents.spawn` uses this for the first child turn, and `Subagents.message` uses it for follow-up turns. It is the handoff point between database admission and actual background execution.

*Call graph*: called by 2 (message, spawn); 3 external calls (update, workspace_tx, log).


##### `Subagents._await_terminal`  (lines 422–432)

```
async def _await_terminal(self, turn_id: UUID) -> TerminalFrame
```

**Purpose**: Waits until a child turn has a final terminal record in the database. A terminal record is the durable note saying the turn is done, failed, canceled, or otherwise finished.

**Data flow**: It receives a turn id → repeatedly reads the turn’s terminal field from the database → if the field is present, validates it as a `TerminalFrame` and returns it; if not, sleeps briefly and tries again.

**Call relations**: `Subagents.spawn` uses this when the parent wants a foreground result, and `Subagents.wait` uses it for background children. It is intentionally simple polling: it keeps checking the durable database record until the child has truly finished.

*Call graph*: called by 2 (spawn, wait); 4 external calls (sleep, model_validate, select, workspace_tx).


### Brief writing pipeline profiles
The brief pipeline extension registers staged outline, draft, and critique worker profiles for structured writing workflows.

### `extensions/brief_pipeline/ufo_ext_brief_pipeline/__init__.py`

`other` · `startup/import time`

This is the package’s front door. In Python, an `__init__.py` file tells the language that a folder should be treated as an importable package, meaning other code can refer to it by name. Here, the file only contains a short documentation string: “Brief pipeline extension.” That acts like a label on a folder, helping readers and tools understand what this package is meant to contain.

There is no setup code, no configuration, and no functions here. Its value is structural: without it, depending on the Python version and packaging setup, the extension might not be recognized or imported in the expected way. Think of it like a sign on a room in a building. The sign does not do the work inside the room, but it tells people and systems what the room is for and helps them find it.


### `extensions/brief_pipeline/ufo_ext_brief_pipeline/pipeline.py`

`config` · `extension load`

This file is the blueprint for a simple writing assembly line. The parent agent can ask one subagent to make an outline, pass that outline to another subagent to write a draft, and then pass the draft to a third subagent for critique. Each subagent is “toolless,” meaning it cannot call extra tools or start more agents; it only reads its prompt and produces a structured answer. That keeps the pipeline predictable, like three people at fixed stations on a production line.

The file first names the three stages and points to the folder that holds their prompt text. It then defines small Pydantic models, which are structured data shapes that say exactly what information must go in and what must come out. For example, the outline stage receives a topic and audience, and returns an outline. The draft stage receives the topic and outline, and returns a draft. The critic receives a draft, and returns a verdict plus optional improvements.

Finally, the file builds three SubagentProfile objects. A profile is the full instruction card for a subagent: its name, prompt, allowed tools, input type, output type, and round limit. Without this file, the brief pipeline would not know what stages exist, what prompts to use, or how to safely pass work from one stage to the next.


### Browser delegation workers
Browser delegation tools start focused or parallel browser-capable subagents using a reusable browser worker profile.

### `extensions/browser/ufo_ext_browser/delegation.py`

`orchestration` · `request handling`

This file is the bridge between a non-browser agent and a specialized browser subagent. The main agent does not directly receive a raw browser window to control. Instead, it hands over a self-contained assignment, like “go to this site and extract pricing,” and waits for the browser agent to report back. That separation matters because browser work can be slow, unpredictable, or get stuck on a bad website. The single-task tool starts a fresh browser session every time, waits up to a caller-specified time limit, and cancels the child task if it runs too long. The batch tool, called wide_browse, reads a workspace file containing URLs or site names, removes blank lines and duplicates, and sends each item to a browser child task. It limits how many children run at once, like letting only a fixed number of shoppers enter a store at a time, so the system is not overwhelmed. It can also append a requested JSON output shape to each browser prompt, then writes all collected results to wide_browse.json. The file ends by registering these two capabilities as tool definitions, including their input shapes and safety markings.

#### Function details

##### `_browser_task`  (lines 86–111)

```
async def _browser_task(ctx: ToolContext, args: BrowserTaskInput) -> ToolResult
```

**Purpose**: Runs one complete browser assignment by starting a browser subagent and waiting for its final report. It is used when the parent agent needs a fresh, isolated web session for one multi-step task.

**Data flow**: It receives the tool context and a structured request containing a starting URL, task instructions, a short task name, and a timeout. It checks that subagent control is available, starts a browser child turn in the background, and waits for that child to finish within the allowed number of minutes. If the child times out, it cancels the browser run and returns an error message. If the child finishes successfully, it reads the child’s text as a BrowserResult, turns it back into JSON, and returns that JSON as the tool output.

**Call relations**: This function is registered as the handler for the browser_task tool. When that tool is invoked, it uses the context’s spawn ability to create the browser child, waits through the subagent control interface, and formats the child’s BrowserResult into a normal ToolResult for the parent agent.

*Call graph*: 5 external calls (__init__, __init__, timeout, spawn, model_validate_json).


##### `_read_lines`  (lines 114–125)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: Reads a workspace text file and turns it into a clean list of unique non-empty lines. In this file, each line is meant to be a URL or site name for batch browsing.

**Data flow**: It receives the tool context and a file path. It asks the sandbox to run a safe cat command for that path, checks whether reading succeeded, then walks through the file line by line. It trims spaces, skips empty lines, removes duplicates while keeping the first occurrence, and returns the resulting list of entities.

**Call relations**: This helper is called by _wide_browse before any browser children are started. It gives _wide_browse a clean work list, so the batch process does not waste browser sessions on blank lines or repeated entries.

*Call graph*: called by 1 (_wide_browse); 1 external calls (dumps).


##### `_wide_browse`  (lines 128–155)

```
async def _wide_browse(ctx: ToolContext, args: WideBrowseInput) -> ToolResult
```

**Purpose**: Runs the same kind of browser extraction task across many URLs or site names in parallel. It is useful when the agent needs structured information from a list of targets rather than from just one website.

**Data flow**: It receives the tool context and a request naming an entities file, a prompt template, an output schema file, and a user-facing description. It reads and deduplicates the entities, rejects lists that are too large, tries to read the JSON schema text, and creates a limit on how many browser child tasks may run at once. It then launches one visit for each entity, waits for all visits to finish, writes the collected rows to wide_browse.json in the workspace, and returns a short JSON response containing both the rows and the output file name.

**Call relations**: This function is registered as the handler for the wide_browse tool. It first relies on _read_lines to prepare the input list, then uses its inner visit function for each entity. It gathers all those visits together so the parent receives one combined result rather than many separate browser outputs.

*Call graph*: calls 1 internal fn (_read_lines); 5 external calls (__init__, __init__, Semaphore, gather, dumps).


##### `_wide_browse.visit`  (lines 136–149)

```
async def visit(entity: str) -> dict[str, object]
```

**Purpose**: Runs one browser subtask for one entity inside the larger wide_browse batch. It turns a single URL or site name into a browser-agent prompt and records the result in a row.

**Data flow**: It receives one entity from the cleaned list. Before starting, it waits for the shared concurrency slot so only a limited number of browser tasks run at the same time. It fills the prompt template by replacing {entity}, appends the requested output schema if one was read successfully, and spawns a browser child using a deterministic deduplication key. It returns a dictionary with the entity name and the browser child’s JSON output, or an empty string if there was no output.

**Call relations**: This inner function is created and used only by _wide_browse. _wide_browse starts many visit calls through asyncio.gather, and each visit hands one prepared task to the browser subagent through the context’s spawn mechanism.


### `extensions/browser/ufo_ext_browser/subagent.py`

`config` · `startup / subagent setup`

This file is like a job description and tool badge for a browser-focused assistant. The larger system can delegate web work to a child agent, and this file says what that child agent is called, what instructions it should follow, what tools it may use, and what kind of request and answer it should exchange with the parent agent.

When the file is loaded, it reads a Markdown prompt file named `subagent_browser.md`. That prompt contains the detailed working instructions for the browser subagent. The file also gathers the browser tool names from the browser extension and adds a few basic workspace tools, such as reading and writing files, so the subagent can save notes, findings, or screenshots where the parent agent can later inspect them.

Two small data shapes are defined with Pydantic, a library that checks whether data has the expected fields and types. `BrowserTask` describes what the parent can ask for: a task, an optional starting URL, and an optional task name. `BrowserResult` describes what comes back: a text result.

Finally, `BROWSER_PROFILE` combines all of this into a `SubagentProfile`. The `untrusted_output=True` setting is important: it marks the browser subagent’s answer as something that may come from the open web, so the parent system should treat it carefully rather than blindly trusting it.


### Research delegation workers
Research delegation runs batches of normal or deep research subagents and collects their structured results.

### `extensions/research/ufo_ext_research/delegation.py`

`orchestration` · `request handling`

This file solves the problem of doing the same research task many times, once for each company, person, topic, or other entity in a list. Instead of asking one research agent to work through the list slowly, it fans the work out to several research subagents at the same time, like opening several checkout lanes instead of making everyone wait in one line.

The main tool is called `wide_research`. A user gives it an entities file, a prompt template, an optional output schema file, and a description. The tool reads the entities file from the sandbox, which is the controlled workspace where tool commands can read and write files. It removes blank lines and duplicates so the same entity is not researched twice.

It then limits the list to a safe maximum size and starts a bounded number of research jobs in parallel. “Bounded” matters: it prevents the system from launching too many child agents at once. For each entity, the prompt template is filled in by replacing `{entity}` with the actual name. If an output schema file can be read, the schema text is added to the child objective so each subagent knows what shape of answer to return.

Each child run uses a deterministic deduplication key based on the parent tool call and the entity. That means if the parent run is retried after a crash, already-started or completed child work can be reconnected to instead of repeated. Finally, the collected results are written to `wide_research.json` and also returned in the tool response.

#### Function details

##### `_read_lines`  (lines 41–52)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: This helper reads a text file from the sandbox and turns it into a clean list of unique entities. It is used so the batch research tool starts from a tidy, duplicate-free list.

**Data flow**: It receives a tool context and a file path. It asks the sandbox to run `cat` on that path, checks whether the read succeeded, then splits the file into lines. Each line is trimmed, blank lines are ignored, and repeated entries are skipped. It returns the cleaned list of entity strings, or raises an error if the file cannot be read.

**Call relations**: The main `_wide_research` function calls `_read_lines` at the start of the workflow. This helper hands `_wide_research` the list of entities that will be sent to child research agents.

*Call graph*: called by 1 (_wide_research); 1 external calls (dumps).


##### `_wide_research`  (lines 55–82)

```
async def _wide_research(ctx: ToolContext, args: WideResearchInput) -> ToolResult
```

**Purpose**: This is the main implementation of the `wide_research` tool. It reads the input list, launches parallel research subagents for each entity, collects their answers, writes a JSON results file, and returns a summary to the caller.

**Data flow**: It receives the tool context and structured input arguments. It reads entities from the requested file, rejects the request if there are too many, tries to read the output schema file, and creates a semaphore, which is a simple gate that allows only a fixed number of jobs to run at once. It then starts one `visit` task per entity, waits for all of them to finish, writes the resulting rows to `wide_research.json`, and returns a tool result containing the rows and the output file name.

**Call relations**: This function is registered as the handler for `WIDE_RESEARCH_TOOL`, so the tool system calls it when a user invokes `wide_research`. It relies on `_read_lines` for input cleanup, uses its nested `visit` function to run each child research job, waits for all visits with `asyncio.gather`, and wraps the final answer in `TextContent` and `ToolResult` for the wider tool framework.

*Call graph*: calls 1 internal fn (_read_lines); 5 external calls (__init__, __init__, Semaphore, gather, dumps).


##### `_wide_research.visit`  (lines 63–76)

```
async def visit(entity: str) -> dict[str, object]
```

**Purpose**: This inner function performs the research work for one entity. It builds the exact objective for that entity, runs a research subagent, and turns the subagent’s answer into one row of the final JSON output.

**Data flow**: It receives a single entity string from the cleaned list. It waits for permission from the semaphore so the system does not run too many subagents at the same time. Then it fills the prompt template with the entity name, appends the output schema if one was available, and asks the context to spawn a child using the research profile. It returns a dictionary with the entity name and the child agent’s serialized result text, or an empty string if there was no output.

**Call relations**: `_wide_research` creates and calls this function once per entity while gathering all work in parallel. Each `visit` hands the actual research task off to `ctx.spawn`, using the research profile and a stable deduplication key so retries can reconnect to previous child work instead of duplicating it.


### `extensions/research/ufo_ext_research/subagent.py`

`config` · `startup / subagent registration`

This file is like a job description for research-focused child agents. A child agent is a smaller assistant that the main agent can delegate work to, so the main agent does not have to do every search, page read, and note-taking step itself.

The file sets up two profiles. The regular `research` profile is for focused research tasks. The `deep_research` profile uses the same kind of tools and input/output format, but gets a much larger round limit, meaning it can spend more back-and-forth steps gathering and checking information from multiple sources.

Both profiles are limited to a research-safe tool set. They can search the web, fetch pages, use vertical search, ask a browser task to inspect pages, call external tools, read and write files, search memory, and use spreadsheet-style helpers. They do not get every possible browser control directly; that boundary keeps browser-specific work in the browser subagent.

The prompt text is loaded from nearby Markdown files. Those prompts are the detailed instructions that shape how each subagent behaves. The file also defines simple input and output data models: a research task comes in as an `objective`, and the answer comes back as a `result`. Without this file, the system would not know how to create these research subagents or what tools and limits they should have.


### Website building workers
Website delegation hands complete site-building jobs to a specialized website-building child assistant profile.

### `extensions/sites/ufo_ext_sites/delegation.py`

`orchestration` · `request handling`

This file exists so the main agent does not have to build websites directly inside its own conversation. Instead, it can delegate the whole job to a focused helper agent that knows how to build, serve, and check websites in the sandbox. Think of it like a project manager handing a complete brief to a specialist contractor, then waiting for the contractor’s report.

The file defines the public tool name and description shown to the agent. It also defines `BuildWebsiteInput`, the shape of the information the tool accepts. The most important field is `objective`, which must contain the full website brief because the child agent does not inherit the parent conversation history. Optional fields give the build a friendly name, preload helpful skills before the child starts, or allow a larger round budget for unusually big builds.

The actual work is done by `_build_website`. It asks the current tool context to spawn the website-building subagent, sends the cleaned input along, then turns the child’s result into plain text content inside a standard tool result. Finally, `DELEGATION_TOOLS` exposes this as a `ToolDef`, so the rest of the system can register and call it like any other tool.

#### Function details

##### `_build_website`  (lines 47–50)

```
async def _build_website(ctx: ToolContext, args: BuildWebsiteInput) -> ToolResult
```

**Purpose**: This function runs when the `build_website` tool is used. It starts a fresh website-building subagent with the user’s build brief and returns the subagent’s summary as the tool’s answer.

**Data flow**: It receives a tool context, which is the object that knows how to start child agents, and a `BuildWebsiteInput` object containing the website brief and optional build settings. It converts that input into a plain data dictionary while leaving out missing optional values, sends it to the website-building subagent, waits for the subagent to finish, then serializes the subagent’s output into text. It returns a `ToolResult` containing that text, so the caller gets a normal tool response.

**Call relations**: This function is attached to the `build_website` tool definition, so the tool system calls it when an agent chooses that tool. Inside, it hands the job to `ToolContext.spawn`, using the configured website-building subagent name, and wraps the returned output with `TextContent` and `ToolResult` so it fits the standard tool-response format.

*Call graph*: 4 external calls (__init__, __init__, spawn, model_dump).


### `extensions/sites/ufo_ext_sites/subagent.py`

`config` · `startup / subagent setup`

This file is like an ID card and rule sheet for a specialized helper. The helper’s job is website building, so the file gives it a name, loads its written instructions from a nearby prompt file, sets a limit on how many back-and-forth steps it may take, and lists the tools it is allowed to use.

The tool list combines basic file-editing abilities, such as reading and writing files, with site-specific tools used to build and serve pages. It also includes a JavaScript REPL, which is an interactive JavaScript runner the subagent can use to test a live page, plus optional web research tools if those are installed.

The file also defines two simple data shapes using Pydantic, a library that checks whether data has the expected fields. `WebsiteBuildingTask` describes what the parent system gives the subagent, mainly the goal to accomplish. `WebsiteBuildingResult` describes what the subagent returns: a text result.

Finally, all of this is bundled into `WEBSITE_BUILDING_PROFILE`, a `SubagentProfile` object. Without this file, the system would not have a clear recipe for launching the website-building subagent safely and consistently.

## 📊 State Registers Touched

- `reg-extension-pack-manifest` — The installed pack and extension menu that says what tools, routes, jobs, skills, credentials, and backends exist.
- `reg-agent-profile-settings` — The saved assistant settings for a workspace, including which agent is used and what it is allowed to do.
- `reg-conversation-thread-state` — The saved conversation identity and history that let the system continue the same thread over time.
- `reg-turn-record-lifecycle` — The durable record of each unit of agent work, including who started it, its status, parent links, and final result.
- `reg-durable-work-queue` — The shared queue of conversation and job work waiting to be claimed, retried, resumed, or completed by workers.
- `reg-cancellation-state` — The shared stop signal and saved cancellation status for turns, child turns, and paused work.
- `reg-tool-catalog` — The live list of tools the model can call, including their names, descriptions, schemas, and dispatch targets.
- `reg-prompt-skill-library` — The enabled instructions, skill folders, helper profiles, and prompt versions that shape how the agent behaves.
- `reg-browser-session-provider` — The shared way to obtain a browser automation endpoint for a turn, regardless of where the browser runs.
- `reg-live-stream-hub` — The live stream of turn updates that clients can watch and replay after reconnecting.
- `reg-observability-trace-context` — The trace, metric, and log context that follows requests and turns so operators can understand what happened.
- `reg-site-preview-serving-state` — The extension-maintained mapping from generated site/app outputs to served preview routes or shareable live site handles.
- `reg-browser-runtime-session-state` — Mutable browser automation session state such as active CDP sessions, tabs, cookies, screenshots, and downloads used while browser tools and subagents operate.
