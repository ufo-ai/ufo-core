# Subagent profile implementations  `stage-3.1.1`

This stage defines the “job descriptions” for helper agents. A helper agent is a smaller agent started by the main agent to do a focused task. These profiles are shared support for the main work loop: when the system decides to delegate, it looks here to know what kind of helper to start, what instructions to give it, what tools it can use, and what input and output formats it must follow.

The core profile file provides the default general-purpose helper, used when no more specific extension fits. The brief pipeline extension adds a staged writing workflow: one helper makes an outline, another writes a draft, and another critiques it, with limits and data shapes for each step. The browser extension defines a web-using helper for browser tasks. The research extension defines standard and longer-running research helpers, including their tools and model choices. The sites extension defines a website-building helper that can create and serve sites. Together, these profiles act like labeled toolkits the main agent can pick from.

## Files in this stage

### Default subagent profile
Defines the baseline helper-agent profile used when no specialized extension profile applies.

### `core/src/ufo/loop/profiles.py`

`config` · `startup and subagent dispatch`

This file is the system’s default recipe for creating a child agent. A child agent, or subagent, is like asking a capable assistant to work on one contained task while the main agent keeps overall control. Without this file, the system would not have a safe fallback subagent profile to use when a task does not match a specialized extension-provided profile.

The profile is intentionally limited. The general-purpose subagent can read and write files, search, edit, run shell commands, load skills, and use some optional extension tools if those extensions are installed. But it cannot ask the user questions, create more subagents, contact sibling subagents, cancel them, or approve account connections. In plain terms, it can do work, but it cannot widen the conversation or take over coordination.

The file also defines the simple data shapes for talking to this subagent: it receives a task string, and it returns a result string. Its prompt tells it how to behave: work independently, make reasonable assumptions, avoid repeated failing actions, load relevant skills first, save useful files in the shared workspace, and report back briefly. The final `GENERAL_PURPOSE_PROFILE` bundles all of that together so the rest of the system can register and use it as the core fallback profile.


### Extension delegation profiles
Defines specialized extension-provided subagents and staged workflows for writing, browsing, research, and site-building tasks.

### `extensions/brief_pipeline/ufo_ext_brief_pipeline/pipeline.py`

`config` · `startup/config load`

This file is the recipe card for a simple writing assembly line. The extension wants to turn a topic into a useful brief, but it does that in three focused steps instead of asking one agent to do everything at once. First, an outline agent plans the brief. Then a draft agent writes from that outline. Finally, a critic agent reviews the draft and suggests improvements.

The file defines small typed data shapes using Pydantic, a library that checks that data has the expected fields. For example, `BriefRequest` says the outline stage needs a `topic` and an `audience`, while `BriefDraft` says the draft stage must return a `draft` string. These typed shapes make the handoff between stages predictable, like using labeled forms instead of loose notes.

It also creates three `SubagentProfile` objects. A subagent profile is the setup sheet for one child agent: its name, the prompt text it should follow, whether it can use tools, what input it accepts, what output it must produce, and how many back-and-forth rounds it may take. Here all stages are “toolless,” meaning they only write text and cannot call outside tools or start more agents. That keeps the pipeline shallow and controlled: only the parent agent decides the order and passes each stage’s result to the next one.


### `extensions/browser/ufo_ext_browser/subagent.py`

`config` · `startup or subagent registration`

This file is like an ID card and instruction packet for a browser-focused helper agent. The main system can delegate a web task to this subagent instead of doing everything itself. Without this file, the system would not know what the browser subagent is called, what instructions it should follow, which tools it may use, or what kind of request and response format to expect.

When the file is loaded, it reads a Markdown prompt file called `subagent_browser.md`. That prompt contains the working instructions for the browser subagent. It then builds a tool list from the browser extension's browser tools, plus a few basic file and web-search tools so the subagent can save notes, screenshots, or findings into the shared workspace.

The file also defines two small data shapes using Pydantic, a library that checks that data has the expected fields and types. `BrowserTask` is what the parent sends in: the task text, and optionally a starting URL and task name. `BrowserResult` is what comes back: a text result.

Finally, `BROWSER_PROFILE` ties everything together into a `SubagentProfile`. This is the object the wider system can register or use when it wants to spin up the browser subagent. The profile marks the subagent's output as untrusted, meaning the parent should treat returned text cautiously rather than blindly accepting it.


### `extensions/research/ufo_ext_research/subagent.py`

`config` · `startup / subagent registration`

This file is like a job description and equipment list for research-focused child agents. The main system can hand a research task to one of these subagents instead of doing everything itself. Without this file, the system would not know how to create those research workers, what they are allowed to do, or how to talk to them in a predictable format.

The file first names the two profiles: `research` for focused work and `deep_research` for larger jobs that may need many steps. Both use the same set of tools. These include web search, fetching web pages, vertical search, a controlled browser task tool, external tool access, file reading and writing, shell commands, memory search, and spreadsheet-style work. The raw browser surface is intentionally not included here; that belongs to a different browser-focused agent.

It then loads the instruction text for each profile from Markdown prompt files. These prompts are the written guidance the agent follows while working.

The `ResearchInput` and `ResearchOutput` classes define a simple contract: a research agent receives an `objective` and returns a `result`. Finally, the file builds two `SubagentProfile` objects. The deep version is almost the same as the normal one, but it has a much higher round limit, giving it more time for multi-source investigations.


### `extensions/sites/ufo_ext_sites/subagent.py`

`config` · `subagent setup`

This file is like a job description and tool belt for a website-building subagent. A subagent is a smaller, focused assistant started by the main system to work on one kind of task. Here, that task is building a website, editing its files, running it, and checking it in a browser-like JavaScript environment.

The file first loads a written prompt from a nearby Markdown file. That prompt contains the detailed working instructions for the website builder. It then lists the tools this subagent is allowed to use: basic file tools such as reading, writing, editing, searching, and shell commands; site-specific build and serve tools; a JavaScript REPL for trying code against a running page; and optional web research tools if that extension is available.

It also defines two small data shapes using Pydantic, a library that checks that data has the expected fields. `WebsiteBuildingTask` describes what the parent assistant gives the subagent, such as the objective and optional setup details. `WebsiteBuildingResult` describes what the subagent gives back: a final result string.

Finally, `WEBSITE_BUILDING_PROFILE` bundles all of this together under the name `website_building`. Without this file, the system would not know how to launch this focused website-building worker or what boundaries to give it.
