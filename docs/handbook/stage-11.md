# Delegated subagents and multi-step workflows  `stage-11`

This stage is used during the main work loop, when the main agent decides a job is big or specialized enough to hand to a helper. A “subagent” is a smaller child session with its own instructions, tools, input, and expected output, like sending a specialist to do one part of a project.

The core profile file defines the fallback general-purpose helper, used when no more specific helper is available. The core subagents file is the dispatcher: it starts child turns, ties them back to the parent turn, tracks them, and allows them to be cancelled safely.

Each extension adds specialists. The browser files define a browser subagent and tools for sending one or many web-browsing tasks to it. The research files define normal and deep research helpers, plus a wide-research tool that runs many research jobs in parallel and saves the combined results. The sites files define a website-building helper and the tool that delegates a full site build to it. The brief pipeline defines three writing steps: outline, draft, and critique.

## Files in this stage

### Core subagent foundation
Defines the default helper profile and the orchestration machinery that lets parent turns launch, track, cancel, and collect child agent work.

### `core/src/ufo/loop/profiles.py`

`config` · `startup and subagent spawning`

This file is the system’s fallback recipe for creating a child agent. A child agent is like a coworker given one clear task by the main agent: it works in the same shared workspace, uses a limited set of tools, and reports back a short result.

The important idea is safety and focus. The general-purpose subagent can read and write files, search, edit, run shell commands, load skills, and use some optional extension tools if they exist. But it cannot ask the human user questions, create more subagents, message or cancel sibling subagents, wait on them, or approve account connections. In everyday terms, it can do assigned work, but it cannot recruit others, interrupt others, or make user-facing decisions.

The file also defines simple input and output shapes using Pydantic models, which are structured data definitions. The subagent receives a `task` string and returns a `result` string. Its prompt tells it how to behave: work independently, avoid repeated failed attempts, load relevant skills first, use proper Office formats for formal documents, share files through `/workspace`, and finish with a concise summary.

Finally, the file packages all of this into `GENERAL_PURPOSE_PROFILE` and exposes it through `CORE_SUBAGENT_PROFILES`, so the rest of the system has a standard built-in profile to use.


### `core/src/ufo/loop/subagents.py`

`orchestration` · `request handling`

A subagent is like asking a specialist to do a clearly defined side job: the parent gives it a named profile, a structured input, and expects a structured output. This file is the machinery that makes that safe and durable. It first keeps a registry of available subagent profiles, so a caller cannot ask for an unknown or duplicate specialist. It also builds the subagent's system prompt, combining the profile's instructions, optional skill information, shared citation rules, and a strict final instruction that the subagent must finish by calling the `finish` tool with data matching its output contract.

The `Subagents` class is bound to one parent turn. When the parent spawns a child, this file validates the input, creates a child conversation and first turn in the database, and puts that turn on the durable work queue. A durable queue means the work can survive retries or process restarts. If a deduplication key is supplied, the same parent request reconnects to the same child instead of creating a duplicate, which prevents double work and double billing.

The parent can wait for a child, start it in the background, send a follow-up message, or cancel it. The file also checks that a requested child really belongs to this parent and this requester before allowing control actions. Without this file, subagent work would be hard to validate, easy to duplicate, and unsafe to reconnect after failures.

#### Function details

##### `SubagentRegistry.__post_init__`  (lines 78–82)

```
def __post_init__(self) -> None
```

**Purpose**: Checks the list of subagent profiles as soon as the registry is created. It prevents two profiles from using the same name, because a name must point to exactly one subagent recipe.

**Data flow**: It reads the profile names from the registry's `profiles` tuple. If every name is unique, nothing changes and construction succeeds; if any name appears more than once, it raises an error naming the duplicates.

**Call relations**: This runs automatically after a `SubagentRegistry` is built. Later lookups rely on this check, because `get` can safely return the one matching profile instead of guessing between duplicates.


##### `SubagentRegistry.get`  (lines 84–91)

```
def get(self, name: str) -> SubagentProfile
```

**Purpose**: Finds a subagent profile by name. It gives callers a clear failure if they ask for a profile that was not registered.

**Data flow**: It receives a profile name, scans the stored profiles, and returns the matching `SubagentProfile` if found. If there is no match, it builds a readable list of valid names and raises `UnknownSubagentProfile`.

**Call relations**: The spawn path uses this before starting a child turn, so unknown subagents are rejected before any database rows or queue jobs are created. The trust-check path also uses it to decide whether a finished child's output should be treated as trusted.

*Call graph*: 1 external calls (__init__).


##### `subagent_system_prompt`  (lines 94–126)

```
def subagent_system_prompt(profile: SubagentProfile, *, skills: Sequence[tuple[str, str]]=CORE_SKILL_INDEX, preload: tuple[LoadedSkill, ...]=()) -> str
```

**Purpose**: Builds the full instruction text that a child subagent receives. It combines the profile's own prompt, skill information, shared citation and formatting rules, and the final output contract.

**Data flow**: It takes a subagent profile, an optional list of available skills, and optional preloaded skill bodies. It fills the skill-index placeholder, rejects leftover prompt placeholders, optionally appends preloaded skill instructions within a size limit, then returns one final prompt string ending with the required `finish` instruction.

**Call relations**: This is used when preparing a subagent turn for the model. It hands off to prompt-rendering helpers to create the skill index and to skill-runtime code to format loaded skill content, but it keeps the final output rule last so profile or skill text cannot override it.

*Call graph*: 3 external calls (findall, render_skill_index, loaded_context).


##### `Subagents.authorize`  (lines 140–141)

```
def authorize(self, requester_member_id: UUID | None) -> 'Subagents'
```

**Purpose**: Creates a copy of the subagent controller that is tied to a specific requesting audience member. This is used when the system needs later child operations to be checked against that requester.

**Data flow**: It receives an optional member ID and returns a new `Subagents` object with the same client, registry, parent turn, and audience, but with `requester_member_id` set to the supplied value. The original object is not changed.

**Call relations**: Callers use this before spawning or controlling subagents on behalf of a particular member. The copied value is later read by `acting_member_id` and enforced by `_require_child` and `_admit`.

*Call graph*: 1 external calls (replace).


##### `Subagents.acting_member_id`  (lines 144–145)

```
def acting_member_id(self) -> UUID | None
```

**Purpose**: Decides which audience member the subagent action is acting for. It prefers the explicitly authorized requester, and otherwise falls back to the parent turn's member.

**Data flow**: It reads `requester_member_id` and `parent.on_behalf_of_member_id`. It returns the requester ID if present; if not, it returns the parent turn's on-behalf-of member ID.

**Call relations**: This value is stamped onto child turns when they are admitted or messaged. It is also used by `_require_child` so a member cannot control a child turn that belongs to another member's request.


##### `Subagents.spawn`  (lines 147–191)

```
async def spawn(self, profile: str, payload: dict[str, Any], background: bool=False, dedup_key: str | None=None) -> SpawnResult
```

**Purpose**: Starts a child subagent turn, either returning immediately for background work or waiting for the child to finish and returning its validated output. This is the main entry point for asking a subagent to do work.

**Data flow**: It receives a profile name, input payload, optional background flag, and optional deduplication key. It looks up the profile, validates the payload against the profile's input model, chooses a child conversation ID, creates the child turn through `_admit`, queues it through `_enqueue` if needed, then either returns the child turn ID immediately or waits for the terminal result, checks success, validates the final text against the output model, and returns a `SpawnResult`.

**Call relations**: This is the top-level spawn flow used by tools or agent logic that need a specialist child turn. It relies on the registry for profile lookup, `_admit` for durable database creation, `_enqueue` for queue dispatch, and `_await_terminal` for foreground waiting.

*Call graph*: calls 3 internal fn (_admit, _await_terminal, _enqueue); 5 external calls (__init__, __init__, turn_id_for, uuid4, uuid5).


##### `Subagents.wait`  (lines 193–212)

```
async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]
```

**Purpose**: Waits for one or more background subagent turns to finish and reports their final status and text. It lets the parent come back later after doing other work.

**Data flow**: It receives a tuple of child turn IDs. For each ID, it first verifies that the turn is truly this parent's child, then waits until that child has a terminal record, and finally returns a tuple of `SubagentStatus` objects containing each turn ID, status, final text, and whether the output should be treated as untrusted.

**Call relations**: This completes the background-spawn loop that starts in `spawn(background=True)`. It uses `_require_child` for ownership checks, `_await_terminal` for polling, and `_untrusted_output` to preserve the profile's trust setting in the returned status.

*Call graph*: calls 3 internal fn (_await_terminal, _require_child, _untrusted_output); 1 external calls (__init__).


##### `Subagents.cancel`  (lines 214–231)

```
async def cancel(self, turn_id: UUID) -> SubagentStatus
```

**Purpose**: Cancels a child subagent turn that belongs to this parent and reports what state it ended in. It gives the parent a safe way to stop background child work.

**Data flow**: It receives a child turn ID, verifies ownership, asks the shared cancellation system to cancel that turn, then reads the turn's status and terminal frame from the database. It returns a `SubagentStatus` with the turn ID, current status, and terminal text if there is one.

**Call relations**: This is called when a parent wants to stop a child. It delegates the actual durable cancellation to the shared `cancel_one_turn` helper, then reads the result itself so the caller gets a clear status back.

*Call graph*: calls 1 internal fn (_require_child); 5 external calls (__init__, model_validate, select, cancel_one_turn, workspace_tx).


##### `Subagents.message`  (lines 233–305)

```
async def message(self, turn_id: UUID, text: str) -> SubagentStatus
```

**Purpose**: Adds a follow-up message to a background subagent's own conversation. This lets a parent continue an existing child subagent instead of starting over.

**Data flow**: It receives an existing child turn ID and message text. It verifies the child belongs to this parent, locks the child's conversation, calculates the next sequence number, inserts a new queued turn with the message as inbound text, marks it ready for dispatch if no earlier queued turn is waiting, possibly enqueues it, and returns a queued `SubagentStatus` for the new follow-up turn.

**Call relations**: This is used after a background subagent already exists and needs another instruction. It shares the same ownership check as cancel and wait, and it hands the new follow-up turn to `_enqueue` only when it is safe for that conversation's ordering.

*Call graph*: calls 2 internal fn (_enqueue, _require_child); 8 external calls (__init__, exists, insert, select, update, workspace_tx, current_traceparent, turn_id_for).


##### `Subagents._untrusted_output`  (lines 307–313)

```
def _untrusted_output(self, profile: str) -> bool
```

**Purpose**: Decides whether a child's output should be treated as untrusted content. It errs on the side of caution if the child's profile can no longer be found.

**Data flow**: It receives a profile name, tries to look up that profile, and returns the profile's `untrusted_output` flag. If the lookup fails, it returns `true` so the output is not treated as safe instructions.

**Call relations**: The background wait path calls this while building `SubagentStatus`. It keeps trust decisions attached to the profile definition, but protects the system if old database turns refer to profiles that are no longer registered.

*Call graph*: called by 1 (wait).


##### `Subagents._require_child`  (lines 315–332)

```
async def _require_child(self, turn_id: UUID) -> str
```

**Purpose**: Confirms that a turn ID belongs to a subagent spawned by this exact parent turn and requester. It prevents one turn from cancelling, messaging, or waiting on someone else's child.

**Data flow**: It receives a turn ID, reads the turn's parent ID, profile name, and on-behalf-of member ID from the database, and compares them with the current parent and acting member. If everything matches, it returns the child profile name; otherwise it raises an error.

**Call relations**: Wait, cancel, and message call this before touching a child turn. It is the guardrail that makes later operations safe.

*Call graph*: called by 3 (cancel, message, wait); 2 external calls (select, workspace_tx).


##### `Subagents._admit`  (lines 334–400)

```
async def _admit(self, conversation_id: UUID, turn_id: UUID, profile: str, inbound: str) -> bool
```

**Purpose**: Creates the database records for a child subagent conversation and its first queued turn. It is written to be safe to run again without creating duplicates.

**Data flow**: It receives the child conversation ID, child turn ID, profile name, and validated inbound JSON text. It inserts the conversation and first turn if they do not already exist, checks that any existing turn belongs to the same acting member, marks a queued turn as dispatch-enqueued, and returns `true` if the caller should enqueue work now or `false` if the turn is already past the queued state.

**Call relations**: The spawn flow calls this before queueing a child. Its conflict-safe inserts are what make deduplication keys useful: a retry can reconnect to the same stored child instead of making a second one.

*Call graph*: called by 1 (spawn); 5 external calls (select, update, audience_member, workspace_tx, current_traceparent).


##### `Subagents._enqueue`  (lines 402–431)

```
async def _enqueue(self, turn_id: UUID, conversation_id: UUID) -> None
```

**Purpose**: Places a queued turn onto the durable turn-processing queue. If enqueueing fails, it clears the dispatch marker so another dispatcher can try later.

**Data flow**: It receives a turn ID and conversation ID, builds queue options including the workflow name, workflow ID, partition key, and app version, then asks the DBOS client to enqueue the work. If cancellation or an error interrupts enqueueing, it updates the database to remove the dispatch timestamp for that queued turn; non-cancellation errors are also logged.

**Call relations**: Spawn uses this for a new child turn, and message uses it for a follow-up turn. It is the bridge between database admission and actual background execution.

*Call graph*: called by 2 (message, spawn); 3 external calls (update, workspace_tx, log).


##### `Subagents._await_terminal`  (lines 433–443)

```
async def _await_terminal(self, turn_id: UUID) -> TerminalFrame
```

**Purpose**: Waits until a turn has a final terminal record in the database. A terminal record is the saved end state of a turn, such as done, failed, or cancelled.

**Data flow**: It receives a turn ID and repeatedly reads that turn's terminal field from the database. If the field is present, it converts it into a `TerminalFrame` and returns it; if not, it sleeps briefly and checks again.

**Call relations**: Foreground spawn uses this to wait for a child answer before returning to the parent. Background wait uses the same polling path, so already-finished children return quickly and still go through the same terminal parsing.

*Call graph*: called by 2 (spawn, wait); 4 external calls (sleep, model_validate, select, workspace_tx).


### Brief writing stages
Defines the outline, draft, and critique stages used to run a structured multi-step brief-writing workflow.

### `extensions/brief_pipeline/ufo_ext_brief_pipeline/pipeline.py`

`config` · `startup / extension load`

This file is the recipe card for a small writing assembly line. The goal is to produce a brief by splitting the work into three focused subagents: one makes an outline, one turns that outline into a draft, and one reviews the draft. Without this file, the parent agent would not know what each stage is called, what information to send into it, what kind of answer to expect back, or which instruction prompt to use.

The file uses Pydantic models, which are Python classes that describe and check structured data. For example, the outline stage receives a topic and audience, then must return an object containing an outline. The draft stage receives the topic and outline, then returns a draft. The critic stage receives the draft, then returns a verdict and optional improvements.

At the bottom, the file builds three `SubagentProfile` objects. A profile is like a job description for a subagent: its name, its prompt text, whether it can use tools, what input and output formats it must follow, and how many back-and-forth rounds it may take. Here, all three stages are “toolless,” meaning they only write structured text and do not call external tools. This keeps the pipeline shallow and predictable: the parent agent is the one that starts each stage and passes the result along.


### Browser subagent delegation
Provides tools for delegating one or many browsing tasks to a constrained browser-focused child agent.

### `extensions/browser/ufo_ext_browser/delegation.py`

`orchestration` · `request handling`

This file is the bridge between a general agent and a special browser agent. Instead of giving the main agent direct control of a browser, it asks a separate “browser” subagent to do the web work and report back. That matters because browser automation can get stuck on slow sites, popups, or loops. This file puts clear limits around those jobs so one bad web session does not block the whole parent turn forever.

There are two user-facing tools here. `browser_task` starts one fresh browser session for a specific web objective, such as searching a site or filling a form. It waits for the browser subagent to finish, but only up to a fixed time budget. If the time runs out, it cancels the child task and returns an error message.

`wide_browse` is the batch version. It reads a workspace file containing URLs or names, removes blank lines and duplicates, then sends each item to browser subagents. It limits how many run at once, like opening only a sensible number of checkout lanes instead of flooding the store. Each result is collected into `wide_browse.json` so later steps can inspect the full batch output.

The file also defines the input shapes for both tools, including the fields users must provide and the allowed timeout range.

#### Function details

##### `_browser_task`  (lines 93–118)

```
async def _browser_task(ctx: ToolContext, args: BrowserTaskInput) -> ToolResult
```

**Purpose**: Runs one browser automation job through the browser subagent and returns the subagent’s final summary. It exists so the parent agent can request web work without directly controlling a browser session.

**Data flow**: It receives a tool context and a `BrowserTaskInput` containing the starting URL, task instructions, task name, timeout, and user-facing description. It starts a background browser subagent with the task details, waits for that child turn within the allowed number of minutes, and then validates the child’s text as a `BrowserResult`. If the child finishes normally, it returns that result as text inside a `ToolResult`; if time runs out, it cancels the child and returns an error result explaining the timeout.

**Call relations**: This is the handler behind the `browser_task` tool definition. When the tool is invoked, it uses the context’s spawn ability to create a browser-profile child turn, wraps the wait in `asyncio.timeout` so the parent cannot hang forever, and formats the final answer with `TextContent` and `ToolResult`.

*Call graph*: 5 external calls (__init__, __init__, timeout, spawn, model_validate_json).


##### `_read_lines`  (lines 121–134)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: Reads a workspace text file and turns it into a clean list of unique, non-empty lines. `wide_browse` uses this to turn an entities file into the set of sites or names it should browse.

**Data flow**: It receives the tool context and a file path. It safely quotes the path for a shell command, reads the file with `cat`, and raises an error if the read fails. Then it walks through the file line by line, trims extra spaces, skips empty lines, removes duplicates while preserving first-seen order, and returns the cleaned list.

**Call relations**: `_wide_browse` calls this first, before it starts any browser work. Its output becomes the list of entities that `_wide_browse` fans out across browser subagents.

*Call graph*: called by 1 (_wide_browse); 1 external calls (quote).


##### `_wide_browse`  (lines 137–164)

```
async def _wide_browse(ctx: ToolContext, args: WideBrowseInput) -> ToolResult
```

**Purpose**: Runs many browser tasks from a file of URLs or site names, collects their results, and writes a combined JSON output file. It is useful when the same extraction or research prompt must be applied to many targets.

**Data flow**: It receives a tool context and a `WideBrowseInput` containing an entities file, a prompt template, a JSON schema file path, and a user-facing description. It reads and deduplicates the entities, refuses batches larger than the configured maximum, reads the optional output schema, and creates a semaphore, which is a small gate that limits how many child tasks run at once. It launches one `visit` task per entity with `asyncio.gather`, writes the collected rows to `wide_browse.json`, and returns a JSON message containing both the rows and the output file name.

**Call relations**: This is the handler behind the `wide_browse` tool definition. It starts by calling `_read_lines`, then uses its inner `visit` helper for each entity, gathers all visits in parallel, serializes the final rows with `json.dumps`, and returns them through `TextContent` and `ToolResult`.

*Call graph*: calls 1 internal fn (_read_lines); 6 external calls (__init__, __init__, Semaphore, gather, dumps, quote).


##### `_wide_browse.visit`  (lines 145–158)

```
async def visit(entity: str) -> dict[str, object]
```

**Purpose**: Runs the browser subagent for one entity in a `wide_browse` batch. It turns a single URL or site name into one browser task and packages that task’s result into one row.

**Data flow**: It receives one entity string from the batch. It waits for permission from the semaphore so only a limited number of visits run at the same time, substitutes the entity into the prompt template, appends the output schema if one was read, and spawns a browser subagent for that entity. It returns a dictionary containing the original entity and the browser result as JSON text, or an empty string if there was no output.

**Call relations**: `_wide_browse` creates and runs this helper once for each cleaned entity. Each `visit` does the per-entity child-agent work, and `_wide_browse` later gathers all returned rows into the final `wide_browse.json` file.


### `extensions/browser/ufo_ext_browser/subagent.py`

`config` · `startup / subagent registration`

This file is like an ID card and instruction packet for a specialized helper agent whose job is web automation. Instead of letting the main agent directly do every browser task, the system can delegate a focused job to this browser subagent. That keeps browser work scoped and gives it its own prompt, tools, and expected request and response format.

At load time, the file reads a Markdown prompt called `subagent_browser.md`. That prompt tells the browser subagent how to behave. It then builds a tool list from the browser extension's browser tools, plus a few core file and search tools such as reading, writing, editing, and web search. This means the subagent can browse, collect information, and save notes or screenshots into the shared workspace for the parent agent to inspect later.

The file also defines two small data shapes using Pydantic, a library that checks that data has the expected fields. `BrowserTask` describes what the parent sends in: the task, and optionally a starting URL and task name. `BrowserResult` describes what comes back: a text result. Finally, `BROWSER_PROFILE` ties everything together as a `SubagentProfile`, marking its output as untrusted so the parent system knows to treat returned web content carefully.


### Research subagent delegation
Provides batch research delegation backed by normal and deep research subagent profiles and consolidated JSON output.

### `extensions/research/ufo_ext_research/delegation.py`

`orchestration` · `tool invocation`

This file solves a practical bottleneck: researching many companies, people, topics, or other entities one at a time is slow and repetitive. The `wide_research` tool lets a user provide a plain text file with one entity per line, then spreads the work across several research subagents at once. Think of it like giving the same worksheet to a small team, where each person fills it out for a different company, then one coordinator collects the answers into a single folder.

The file first defines what inputs the tool needs: an entity list, a prompt template, an optional output schema file, and a user-facing description. When the tool runs, it reads and cleans the entity list, removes duplicates, and refuses to continue if the list is too large. It then optionally reads a schema, which is a description of the shape the returned data should follow.

For each entity, it builds a research objective by replacing `{entity}` in the prompt template. It starts research subagents through `ctx.spawn`, but limits how many run at the same time so the system is not overloaded. Each child run gets a deterministic deduplication key, meaning that if the parent run is retried after a crash, already-started or completed child jobs can be reused instead of repeated. Finally, it writes the collected rows to `wide_research.json` and returns a short result pointing to that file.

#### Function details

##### `_read_lines`  (lines 42–55)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: This helper reads the user’s entity file from the sandbox and turns it into a clean list. It removes blank lines and duplicates so each entity is researched only once.

**Data flow**: It receives a tool context and a file path. It safely quotes the path, asks the sandbox shell to run `cat` on that file, and checks whether the read succeeded. It then walks through the file line by line, trims extra spaces, skips empty entries, remembers which names it has already seen, and returns a list of unique entities in their original order. If the file cannot be read, it raises an error instead of letting the wider research job continue with bad input.

**Call relations**: This is the first step used by `_wide_research` when a batch research request begins. It relies on shell quoting through `shlex.quote` so that file paths are treated as file paths, not as accidental shell commands. Once it returns the cleaned entity list, `_wide_research` decides whether the list is small enough and then fans out the work.

*Call graph*: called by 1 (_wide_research); 1 external calls (quote).


##### `_wide_research`  (lines 58–85)

```
async def _wide_research(ctx: ToolContext, args: WideResearchInput) -> ToolResult
```

**Purpose**: This is the main engine behind the `wide_research` tool. It reads the requested entities, launches bounded parallel research jobs for them, collects the child results, writes a JSON output file, and returns a summary to the caller.

**Data flow**: It receives the tool context and the validated tool arguments. First it asks `_read_lines` for the cleaned entity list, then rejects the request if there are more than 128 entities. It tries to read the requested output schema file from the sandbox, creates a semaphore, which is a limit on how many child tasks may run at once, and starts one visit task per entity. After all visits finish through `asyncio.gather`, it writes the combined rows to `wide_research.json` in the workspace. It returns a `ToolResult` containing JSON text with the rows and the output file name.

**Call relations**: The tool definition at the bottom of the file uses this function as its handler, so it runs whenever someone calls `wide_research`. It calls `_read_lines` to prepare the input list, creates the parallel work using its nested `visit` function, uses `asyncio.Semaphore` and `asyncio.gather` to coordinate that work, and wraps the final response with `TextContent` and `ToolResult`.

*Call graph*: calls 1 internal fn (_read_lines); 6 external calls (__init__, __init__, Semaphore, gather, dumps, quote).


##### `_wide_research.visit`  (lines 66–79)

```
async def visit(entity: str) -> dict[str, object]
```

**Purpose**: This nested helper performs the research work for one entity. It builds that entity’s prompt, starts a research subagent, and formats the child’s answer as one row in the final output.

**Data flow**: It receives one entity string from the cleaned list. Before doing work, it waits for permission from the semaphore so only a limited number of subagents run at the same time. It replaces `{entity}` in the prompt template with the actual entity name, appends the output schema if one was successfully read, and spawns a research-profile child task with a deduplication key based on the parent call and entity. It returns a small dictionary containing the entity name and the child result as JSON text, or an empty string if the child produced no output.

**Call relations**: `_wide_research` creates one `visit` task for each entity and runs them together with `asyncio.gather`. Each `visit` hands its prepared objective to `ctx.spawn`, which starts the actual research subagent. Its returned row is later collected by `_wide_research` into the final `wide_research.json` file.


### `extensions/research/ufo_ext_research/subagent.py`

`config` · `startup / subagent registration`

This file is like a job description and tool badge for the project’s research assistants. Without it, the larger agent system would not know how to start a focused research child agent, what prompt to give it, or which tools it is allowed to touch.

It creates two profiles. The regular `research` profile is for scoped research tasks. The `deep_research` profile uses a larger round limit, meaning it can spend more back-and-forth steps on harder, multi-source work. Both profiles use the same basic input and output: they receive an `objective`, which is the research goal, and return a `result`, which is the finished answer.

The file also sets boundaries. These subagents get web search and fetch tools, some browser-task access, file tools, shell access, spreadsheet help, memory search, and external-tool access. But they do not get every possible browser control; the comment explains that raw browser control belongs to a separate browser subagent. This separation matters because it keeps each helper focused, like giving one worker a research desk and another the full browser workstation.

Finally, the file loads the actual instruction text from Markdown prompt files and packages everything into `SubagentProfile` objects. Other parts of the system can then register or launch these profiles by name.


### Website-building delegation
Provides a website-building delegation tool and the specialized child-agent profile used to implement focused site creation tasks.

### `extensions/sites/ufo_ext_sites/delegation.py`

`orchestration` · `request handling`

This file is a delegation doorway. When the main agent needs a website, web app, dashboard, or web game built, it can call the `build_website` tool rather than doing every step itself. The tool starts a fresh child session with a specialized website-building agent. Think of it like asking a contractor to take a complete project brief, build the site, test it, and report back.

The input model, `BuildWebsiteInput`, describes what the child needs before it starts. The most important field is `objective`, which must be self-contained because the child does not inherit the main conversation history. Other fields give the task a friendly name, optionally preload useful skills so the child starts with instructions already available, and optionally allow a larger round budget for bigger builds. The `user_description` is meant for the activity timeline, but it is not sent to the child.

The actual tool function, `_build_website`, calls `ctx.spawn`, which is the safe project mechanism for starting a subagent. It passes only the relevant build settings, waits for the child to finish, turns the child’s structured output into text, and wraps that text as a tool result. Finally, `DELEGATION_TOOLS` registers this behavior as a callable tool named `build_website`.

#### Function details

##### `_build_website`  (lines 50–55)

```
async def _build_website(ctx: ToolContext, args: BuildWebsiteInput) -> ToolResult
```

**Purpose**: This function runs the `build_website` tool. It starts a specialized website-building subagent with the build instructions, waits for its result, and returns that result as plain tool output.

**Data flow**: It receives a tool context and a `BuildWebsiteInput` object. It turns the input into a dictionary, leaving out empty fields and also leaving out `user_description`, then sends that dictionary to a child agent named for website building. When the child returns, it converts the child’s output to JSON text if there is any output, or to an empty string if not. It then packages that text into a `TextContent` item inside a `ToolResult`.

**Call relations**: This function is the handler attached to the registered `build_website` tool. When the main agent calls that tool, `_build_website` asks `ToolContext.spawn` to create the website-building child session. After the child finishes, `_build_website` hands the summary back to the caller in the standard tool-result format.

*Call graph*: 4 external calls (__init__, __init__, spawn, model_dump).


### `extensions/sites/ufo_ext_sites/subagent.py`

`config` · `startup or subagent registration`

This file is like a job description for a specialist helper. The main system can ask a subagent, which is a smaller agent working inside a focused task, to build a website. To do that safely and consistently, the system needs to know what that helper is called, what instructions it should follow, what tools it may use, and what kind of answer it should return.

The file first loads a website-building prompt from a nearby Markdown file. That prompt contains the detailed workflow the subagent should follow. It then sets a maximum number of rounds, so the helper cannot keep working forever.

It also lists the tools this subagent is allowed to use. These include basic file tools for reading and editing code, site-specific tools for building and serving the site, a JavaScript REPL for checking a running page, and optional web research tools if those are installed. This tool list matters because it limits the subagent to the abilities needed for website work, rather than giving it unrestricted access to everything.

Two small data models describe the conversation boundary: `WebsiteBuildingTask` is the shape of the request sent in, and `WebsiteBuildingResult` is the shape of the answer sent back. Finally, all of this is packaged into `WEBSITE_BUILDING_PROFILE`, which the rest of the system can register or launch when it needs this kind of specialist task.

## 📊 State Registers Touched

- `reg-agent-identity` — The saved identity and settings of each agent, including its main workspace role and whether it may use the internet.
- `reg-conversation-state` — The durable conversation record that ties a surface, agent, audience, sandbox handle, and message history together.
- `reg-turn-queue` — The durable queue of conversation turns waiting to be claimed, run, completed, cancelled, or retried.
- `reg-transcript-state` — The stored conversation transcript, including exact recent messages and compact summaries of older content.
- `reg-tool-catalog` — The shared catalog of tools the model is allowed to see and call during a turn.
- `reg-skill-store` — The shared set of built-in, extension-provided, and user-created skills available to agents.
- `reg-live-stream-hub` — The live stream of turn updates that lets clients watch progress and reconnect without losing recent events.
- `reg-background-jobs` — The shared registry and saved queue of scheduled, recurring, delayed, and administrative background work.
- `reg-fleet-presence` — The shared record of live runtime processes used for supervision, cancellation, and recovery after crashes.
- `reg-observability-context` — The shared logging, metrics, tracing, and trace-link state used to understand work across requests and subagents.
- `reg-subagent-delegation-state` — The parent-child turn and conversation links plus in-flight child-task tracking used to coordinate delegated subagents, cancellation, and result collection.
- `reg-interactive-tool-session-state` — Live state for interactive sandbox tools such as browser contexts, page/element references, REPL kernels, and long-running app sessions reused across tool calls or delegated browser work.
