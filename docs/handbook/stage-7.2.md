# Spawn-target and subagent availability preparation  `stage-7.2`

This stage happens before the main agent can hand work to a child agent. It prepares the menu of “spawn targets,” meaning the helper agents or pipeline steps that the current turn is allowed to start. The spawn catalog is the central clerk. It gathers all available targets, checks what each one needs as input, and describes the result each one should return, so the main agent can choose safely.

Several files define the items that can appear on that menu. The runtime profiles file provides the default general-purpose helper, used when no more specialized helper fits. The browser extension adds a web-capable helper that can use browser tools. The documents extension adds a writing helper for drafting and editing prose. The sites extension adds a website-building helper. The brief pipeline extension adds three ordered writing steps: outline, draft, and critique. Together, these definitions let the system turn a vague need for help into a clear, callable target with known instructions, tools, inputs, and outputs.

## Files in this stage

### Catalog and base profile
Core spawn preparation begins with the live spawn catalog and the default general-purpose subagent profile it can expose.

### `core/src/ufo/host/spawn_catalog.py`

`domain_logic` · `per turn, while preparing spawn tool information`

The `spawn` tool can dispatch work to two kinds of targets: fixed subagent profiles and workspace-defined agents. This file makes sure the list shown to the caller matches what `spawn` will actually accept right now. That matters because workspace agents can change over time, and because a member may only be allowed to see or use some agents. Without this file, callers could be told stale or incomplete information, or they might only learn the right payload shape after making failed calls.

The main flow is simple. First, it reads the current member from the execution authority, reads the subagent profiles from the live registry, and queries the workspace database for active agent rows that this member may use. Admin members can see every workspace agent; non-admin members only see their own. If a workspace agent has the same name as a built-in profile, the agent is shown with an `agent:` prefix so the two are not confused.

Each available target is represented as a `SpawnTarget`, which stores the name to use, whether it is a profile or agent, and the payload keys it expects. The file then uses that same target list in two places: a short description attached directly to the `spawn` payload field, and a fuller runtime skill named `spawn-catalog` that displays a table of all targets. The key idea is that both the dispatch behavior and the user-facing documentation come from the same live read.

#### Function details

##### `spawn_targets`  (lines 42–87)

```
async def spawn_targets(registry: SubagentRegistry, authority: ExecutionAuthority) -> tuple[SpawnTarget, ...]
```

**Purpose**: Builds the exact list of targets that `spawn` may dispatch to on this turn. It includes built-in subagent profiles and the workspace agents the current member is allowed to use, along with the payload keys each one requires.

**Data flow**: It takes a subagent registry and an execution authority. From the authority it finds the current member, from the registry it reads fixed profiles, and from the workspace database it reads active agent rows for the current workspace. It checks whether the member is an admin to decide whether to include all agents or only the member's own agents. It then turns each profile and agent into a `SpawnTarget`, including the target name, its kind, and the input keys expected by that target. The result is an ordered tuple of dispatchable targets.

**Call relations**: This is the live source of truth for the rest of the file. It calls into the workspace transaction layer to read agent rows, checks membership privileges, and uses the input-contract helpers to turn schemas into readable payload-key summaries. The target list it returns is meant to be passed to `spawn_payload_description` and `spawn_catalog_skill`, so the short tool description and the longer catalog skill match the same dispatch rules.

*Call graph*: 8 external calls (__init__, select, workspace_tx, authority_member_id, member_is_admin, input_contract, payload_keys, ws_current).


##### `spawn_payload_description`  (lines 90–99)

```
def spawn_payload_description(targets: tuple[SpawnTarget, ...]) -> str
```

**Purpose**: Creates the text used to describe the `payload` field of the `spawn` tool for this turn. Its job is to put the required payload keys right where a caller writes a spawn request, reducing wrong-payload attempts.

**Data flow**: It receives the already-built tuple of `SpawnTarget` objects. If there are no targets, it returns a generic sentence saying the payload should match the target's input schema. If targets exist, it joins them into a compact sentence such as each target name followed by the keys it takes. The output is plain description text.

**Call relations**: This function does not read the database or registry itself. It relies on `spawn_targets` having already collected the live target list, then turns that list into a short inline hint for the spawn tool's payload field.


##### `spawn_catalog_skill`  (lines 102–122)

```
def spawn_catalog_skill(targets: tuple[SpawnTarget, ...]) -> RuntimeSkill
```

**Purpose**: Builds a runtime skill named `spawn-catalog` that shows a table of every spawn target and the payload keys it expects. A runtime skill is an instruction document made available during the run, so the caller can load it when deciding what to spawn.

**Data flow**: It takes the tuple of `SpawnTarget` objects. It formats each target as a Markdown table row with the target name, whether it is a profile or agent, and the payload keys. It wraps that table in explanatory text and front matter containing the skill name and description. It returns a `RuntimeSkill` object containing the skill metadata, instructions, and raw Markdown.

**Call relations**: Like `spawn_payload_description`, this function depends on the live target list produced by `spawn_targets`. It hands the formatted catalog into `RuntimeSkill`, making the same information available as a loadable skill for callers that need a fuller reference before choosing a target.

*Call graph*: 1 external calls (__init__).


### `core/src/ufo/runtime/profiles.py`

`config` · `subagent setup and dispatch`

This file is the fallback recipe for creating a child agent. A child agent is like a temporary assistant sent off by the main agent to work on a contained task in the same workspace. Without this file, the system would not have a safe default helper to spawn when no specialized profile is available.

The profile is deliberately limited. It can read, write, edit, search files, run shell commands, load skills, share files, and use some optional extension tools if those extensions are installed. But it cannot ask the user questions, create more subagents, message or cancel sibling subagents, or approve account connections. In plain terms, it can do work, but it cannot take over coordination or make sensitive permission decisions.

The prompt text tells the subagent how to behave: work independently, make reasonable assumptions, avoid repeating failed actions forever, load relevant skills first, use proper Office formats for formal documents, and save useful results into the shared workspace with clear names. The special skill index slot is inserted into the prompt so the subagent can see which skills are available for that turn.

Finally, the file packages all of this into a `SubagentProfile` named `general_purpose`, using the standard task input and concise result output contracts. Extension-provided profiles can add more specialized behavior, but this one is the dependable floor.


### Extension spawn profiles
Extension declarations add specialized spawnable stages and subagents for briefs, browsing, writing, and website building.

### `extensions/brief_pipeline/ufo_ext_brief_pipeline/pipeline.py`

`config` · `extension load / pipeline setup`

This file is like the recipe card for a small assembly line that writes a brief. The work is split into three focused subagents: one makes an outline, one turns that outline into a draft, and one reviews the draft. Each subagent is “toolless,” meaning it does not call extra tools or start more subagents; it only reads its instructions and returns a structured answer.

The file first names the three stages and sets a shared round limit, so each stage has only a small number of chances to respond. It then defines simple Pydantic models. Pydantic models are Python classes that describe the shape of data and check that the data matches that shape. For example, a brief request must include a topic, and may include an audience; an outline response must contain an outline string.

At the end, the file builds three SubagentProfile objects. A profile is the system’s description of a subagent: its name, its prompt file, its allowed tools, its input model, its output model, and its round limit. The prompt text is read from nearby Markdown files when this module is loaded. Without this file, the parent brief pipeline would not know how to start each stage or how to safely pass typed results from one stage to the next.


### `extensions/browser/ufo_ext_browser/subagent.py`

`config` · `agent setup and subagent launch`

This file is the “profile card” for a browser-focused subagent. A subagent is a smaller worker that the main agent can send off to do a specific job. Here, that job is web automation: opening pages, clicking through sites, extracting information, filling forms, and saving useful results back into the shared workspace.

The file first loads the browser subagent’s prompt from a Markdown file. That prompt is the instruction sheet the child agent follows. It then builds the list of tools the subagent is allowed to use: the browser tool set, plus a few basic file and web-search tools so it can read, write, edit, and save findings or screenshots for the parent agent.

Two small data shapes are defined with Pydantic, a library that checks and documents structured data. `BrowserTask` describes what the parent sends in: the task text, an optional URL, an optional task name, and whether the child should get extended context. Extended context is turned on by default because browser sessions often need continuity across many page steps. `BrowserResult` describes the freeform answer that comes back.

Finally, `BROWSER_PROFILE` ties everything together into a `SubagentProfile`: name, prompt, tools, input and output formats, model choice, and a warning that the output is untrusted. Without this file, the system would not have a clean, reusable definition for launching browser-only child agents safely and consistently.


### `extensions/documents/ufo_ext_documents/subagent.py`

`config` · `subagent setup`

This file is a small but important contract for a writing-focused child agent. In this system, a subagent is like a specialist coworker that the main agent can ask to do a bounded task. Here, that specialist is only for prose work: reading drafts, editing them, writing them back, and loading the writing workflow skill it needs.

The file names the profile “writing,” pins it to the model `gpt-5.6-terra`, and loads its main instructions from `prompts/subagent_writing.md`. It also gives the child a deliberately narrow toolbox: file reading, writing, editing, searching, and skill loading. It does not get shell access, programming tools, web access, or file-sharing tools. That matters because this child is meant to write documents, not run code, browse for facts, or act like a second general-purpose agent.

Two simple Pydantic models describe the data passed across the boundary. `WritingTask` is what the parent sends in, including the prose objective and the skills to preload. By default it preloads the `writing-drafts` skill, so the child starts with the right workflow already available. `WritingResult` is what comes back: a freeform result tied to the shared delivery process.

Finally, `WRITING_PROFILE` bundles all of this into a `SubagentProfile`, which the wider system can use when spawning this writing specialist.


### `extensions/sites/ufo_ext_sites/subagent.py`

`config` · `when a website-building subagent is created for a task`

This file is like a job description and tool belt list for a website-building subagent. A subagent is a smaller helper run inside a larger conversation, focused on one task instead of talking directly to the user. Here, that task is building and checking a website.

The file loads a website-building prompt from a nearby Markdown file. That prompt contains the detailed working instructions. It then names the tools this subagent is allowed to use: basic file tools for reading and editing code, build and local website tools, JavaScript and spreadsheet REPLs for testing things interactively, and optional web research tools if they are installed.

A key detail is what it deliberately leaves out. The subagent is not allowed to use `publish_website`, because publishing a full app with a backend belongs to the parent turn that is talking to the user. It also does not use `share_file`, because the subagent has no direct member to send files to. Instead, it leaves its work in the shared workspace so the parent can inspect it afterward.

The two small Pydantic models describe the shape of the handoff: `WebsiteBuildingTask` is the request sent into the subagent, and `WebsiteBuildingResult` is the report it sends back. Finally, `WEBSITE_BUILDING_PROFILE` packages the name, prompt, allowed tools, input and output formats, and round limit into one reusable profile.
