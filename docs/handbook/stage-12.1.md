# Specialist subagent profiles  `stage-12.1`

This stage is shared behind-the-scenes support for delegation. It defines “profiles,” which are recipes the main agent uses when it wants to spin up a temporary specialist helper. Each recipe says what the helper is allowed to do, which tools it can use, how much time or work budget it gets, and what its input and output should look like.

The core profile is the fallback helper. It can work in the shared workspace, but it cannot pass work to others, interrupt, or ask the user questions. The brief pipeline splits writing a brief into three small helpers: one makes an outline, one drafts, and one critiques. The browser profile creates a web-use helper with browser tools. The documents profile creates a prose-writing helper for drafting and editing. The research profiles create normal and deeper research helpers, with different budgets. The sites profile creates a website-building helper for making, testing, and preparing sites. Together, these profiles let the system delegate work safely and predictably.

## Files in this stage

### Default helper baseline
Defines the fallback helper-agent profile used when no extension supplies a more specialized subagent.

### `core/src/ufo/loop/profiles.py`

`config` · `subagent creation`

This file answers a simple question: when the main agent creates a child agent without naming a special kind, what should that child be allowed to do? The answer is the `general_purpose` profile. Think of it like the standard job description for a temporary assistant: it explains the assistant’s mission, gives it a safe set of tools, and says what kind of task it receives and what kind of result it must return.

The profile’s prompt tells the subagent to work independently, make reasonable assumptions, avoid getting stuck in repeated failed attempts, and save useful files in the shared `/workspace` directory. It also reminds the subagent to load relevant skills first, especially when producing Office documents such as `.docx`, `.pptx`, or `.xlsx` files.

The tool list is deliberately limited. The subagent can read, write, search, edit, run shell commands, load skills, share files, and use some optional extension tools if they exist. But it cannot ask the user questions, create more subagents, message or cancel sibling subagents, or approve account connections. Without this file, the system would lack a safe default for delegated work, so a plain `spawn` request would not know what kind of child agent to create.


### Brief-writing pipeline
Defines the outline, draft, and critique profiles that structure short-form brief creation.

### `extensions/brief_pipeline/ufo_ext_brief_pipeline/pipeline.py`

`config` · `extension load / pipeline setup`

This file is the recipe card for a simple multi-step writing workflow. Instead of asking one agent to do everything at once, the system splits the job into three focused subagents: one makes an outline, one turns that outline into a draft, and one reviews the draft. Like an assembly line, each station receives a clearly labeled package, does one job, and passes a clearly labeled result to the next station.

The file uses Pydantic models, which are Python classes that describe and check the shape of data. For example, a brief request must have a topic and may have an audience. An outline response must contain an outline. This matters because the parent agent can safely pass results between stages without guessing what fields exist.

It also creates three `SubagentProfile` objects. A profile tells the system the subagent’s name, the instruction prompt to load from disk, which tools it may use, what input it expects, what output it must return, and how many conversation rounds it can take. Here, all three stages are “toolless,” meaning they only write structured text and cannot call external tools or spawn more agents. That keeps the pipeline predictable: the parent agent controls the sequence and depth.


### Task-specific subagents
Defines specialized delegated-worker profiles for browser use, prose writing, research, and website building.

### `extensions/browser/ufo_ext_browser/subagent.py`

`config` · `subagent setup`

This file is like an ID card and job description for a specialized browser helper. The main agent can delegate web work to this helper instead of doing every browser step itself. Without this file, the system would not know how to start that browser-focused child agent, what prompt to give it, what tools it is allowed to touch, or how to package tasks and results.

The file first loads the browser subagent’s written instructions from a Markdown prompt file. It then builds the allowed tool list: the browser tool set, plus a few basic workspace tools such as reading, writing, editing files, and web search. That matters because the browser helper may need to save notes or screenshots in a shared place so the parent agent can inspect them later.

Two small data models describe the conversation contract. `BrowserTask` is what the parent sends in: the task text, an optional starting URL, an optional task name, and whether the child should get extended context. `BrowserResult` is what comes back: a freeform result string.

Finally, `BROWSER_PROFILE` ties everything together into a `SubagentProfile`. It gives the subagent its name, prompt, tools, input and output formats, model choice, and marks its output as untrusted, meaning the parent should treat it carefully rather than blindly accept it.


### `extensions/documents/ufo_ext_documents/subagent.py`

`config` · `subagent setup`

This file is like a job description and equipment list for a specialist writer. The wider system can create child agents for focused tasks; this one is designed for document work, not coding, browsing, or running commands. Without this profile, callers would have to rebuild the same safe writing setup every time, and might accidentally give the writing child tools or context it should not have.

The file names the profile “writing,” pins it to the `gpt-5.6-terra` model, and loads its instructions from `prompts/subagent_writing.md`. It also defines the small set of tools the child may use: reading, writing, editing, searching files, and loading skills. It deliberately does not include shell access, web access, or file-sharing tools. That keeps the child focused on prose in the workspace rather than becoming a second coding agent or fetching outside material on its own.

Two simple Pydantic models describe the contract for talking to this child. `WritingTask` is what the parent sends in, mainly a freeform objective plus skills to preload. By default it preloads the `writing-drafts` skill, so the child starts with the project’s drafting workflow already available. `WritingResult` is what comes back: a freeform result message. Finally, `WRITING_PROFILE` bundles all of these pieces into a `SubagentProfile`, which is the object the rest of the system can use to spawn this writing-focused assistant.


### `extensions/research/ufo_ext_research/subagent.py`

`config` · `startup / extension load`

This file is like a job description sheet for research helpers. The main system can delegate work to smaller specialist agents, called subagents, and this file defines two of them. The regular `research` profile is for focused research tasks. The `deep_research` profile uses the same basic setup but is allowed many more back-and-forth rounds, so it can work through larger, multi-source investigations.

The file first names the profiles and sets shared choices such as the language model to use and the list of tools the research agents may access. Those tools include web search, fetching web pages, browser-based tasks, file reading and writing, external tool calls, memory search, and spreadsheet work. This matters because it limits each research subagent to the tools appropriate for research, instead of giving it every possible capability in the system.

It then reads two prompt files from disk. These prompts are the detailed written instructions that guide each subagent’s behavior. Finally, it defines simple input and output data shapes using Pydantic, a library that checks structured data. A research task goes in as an `objective`, and the answer comes back as a `result`.

Without this file, the system would not know how to create these research subagents, what they are allowed to do, or how to communicate tasks and results with them.


### `extensions/sites/ufo_ext_sites/subagent.py`

`config` · `subagent setup during a website-building task`

This file is like a job description and tool belt for a specialist helper. When the main assistant wants a child agent to work on a website, it uses this profile to start that helper with the right instructions and limits.

The file loads a website-building prompt from a nearby Markdown file. That prompt contains the detailed working rules for the subagent. It also sets a round limit, which caps how many back-and-forth steps the subagent can take before it must stop.

A key part of the file is the tool list. The subagent is allowed to read, write, edit, search files, run shell commands, use local website build and hosting tools, and inspect pages with JavaScript or spreadsheet helpers. It deliberately excludes some tools. For example, it does not get `publish_website`, because publishing a full app is meant to be done by the parent agent after the child reports back. It also does not get `share_file`, because the child agent is not directly talking to the member; instead, it leaves work in the shared workspace for the parent to inspect.

The two small data models describe the task given to the subagent and the result it must return. Finally, everything is bundled into `WEBSITE_BUILDING_PROFILE`, which the broader system can use whenever it needs this website-building helper.
