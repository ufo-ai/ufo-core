# System Handbook — Stage Index

Each stage below links to its full page; the paragraph is the stage's role in the system.

## [Process entry, launch mode selection, and deployment checks](stage-1.md) `stage-1` — 4 files

This stage is the system’s front door and pre-flight checkpoint. It runs at the start of a process, when UFO decides what kind of job it is doing: serving web requests, setting up a workspace, running an administrative command, building a deployment package, or checking sandbox infrastructure before release.

The command entry point, mainly `ufoctl` in `core/src/ufo/cli.py`, acts like a receptionist. It reads the user’s terminal command, loads the needed settings, and sends the request to the right place. A run command moves toward server startup. An init command prepares a workspace. Other commands inspect or maintain the system.

The deployment pieces act like packers and inspectors. `core/src/ufo/bundle.py` gathers the application, settings, extension versions, runtime package, and sandbox client into a Docker build context, a folder Docker can turn into a runnable image. The sandbox scripts then check the isolated execution environment. They build matching hosted and local sandbox images and test proxy behavior so deployment fails early if the safety setup is wrong.

### [Server and CLI command entry points](stage-1.1.md) `stage-1.1` — 1 files

This stage is the front door of the system. It covers the commands a person runs in a terminal to start or manage a UFO workspace, such as initializing a project, running the server, checking status, or performing administrative tasks. These commands happen at the beginning of a workflow, before the deeper server machinery takes over, but they can also be used later for inspection and maintenance.

The main piece here is `core/src/ufo/cli.py`. It defines `ufoctl`, the command-line tool. A command-line tool is a text-based control panel: instead of clicking buttons, the user types commands. This file reads what the user asked for, gathers options and configuration, prepares the process state, and then sends the request to the right part of the system. For example, a “run” command hands off to server setup, while an “init” command helps create or prepare a workspace. In this way, `ufoctl` acts like a receptionist, translating human instructions into the internal actions the system needs to perform.

### [Deploy bundle and sandbox image validation](stage-1.2.md) `stage-1.2` — 3 files

This stage prepares UFO to be deployed safely and repeatably. It happens after the app has been built or configured, but before people rely on it in another environment. Think of it as packing a suitcase, checking the workshop it will run in, and testing the safety locks before travel.

`core/src/ufo/bundle.py` creates the suitcase. It builds a self-contained deployment bundle for `ufoctl`, the command-line tool. The bundle includes the app settings, the exact extension versions, the runtime package, and the sandbox client. This makes a Docker build context, meaning a folder Docker can use to build the same runnable image on another machine.

`sandbox/build_template.py` prepares the workshop where UFO runs untrusted or isolated code. It builds and checks both the hosted E2B sandbox template and the local Docker image, keeping them matched.

`sandbox/proxy_gate.py` is the safety inspection. It starts a temporary E2B sandbox, installs the trusted certificate, and confirms HTTPS proxy behavior fails in the expected controlled way before deployment is accepted.

## [Configuration, pack selection, and feature gates](stage-2.md) `stage-2` — 11 files

This stage is early startup and shared support. Before the system discovers and loads extensions, it first reads the rules that say how this deployment should behave. `core/src/ufo/config.py` defines the expected shape of the main `ufo.toml` settings file and rejects missing or invalid settings early, so problems are caught before real work begins. `core/src/ufo/proxy_serve.py` then helps services turn those settings and environment variables into clear connection rules for model providers and the database.

Next, pack composition chooses bundled sets of abilities, like picking a prepared toolkit. Different packs load different mixes of extensions, skills, prompts, onboarding steps, or safe evaluation tools for local development, hosted production, testing, or examples.

Finally, feature flags provide small on/off or choice switches. The core flag reader gives the rest of the system safe defaults if the flag service fails. The Flagship extension connects those reads to Cloudflare’s flag service and lets operators adjust behavior without redeploying. Together, these parts decide what the system is allowed and prepared to load.

### [Pack composition](stage-2.1.md) `stage-2.1` — 7 files

Pack composition is shared setup support. It decides which ready-made groups of features the system should load together, like choosing a toolbox before starting a job. A “pack” is just a named bundle of extensions, skills, prompt instructions, and onboarding steps, so users do not have to list each piece by hand.

The built-in assistant pack turns on the normal assistant extensions. The assistant billing pack adds billing support for local development, so developers can test the billing flow without using the full hosted setup. The assistant hosted pack is the production-style bundle for managed infrastructure, including its needed extensions, skills, and instructions. The assistant eval pack adapts the normal assistant for testing: it removes real outside broker connections and adds safe evaluation tools such as fake environments and Docker sandbox support.

The DSQA and GDPVal eval pack files define several evaluation tool combinations, from basic to search or browser enabled. The sample pack is a small proof that external users can define packs through the public SDK.

### [Feature flag reads and operator flag writes](stage-2.2.md) `stage-2.2` — 2 files

This stage is shared behind-the-scenes support for feature flags, which are small switches that turn behavior on or off without changing the code. Other parts of the system use these switches during normal work, for example to try a new feature only for certain workspaces.

The core file, `core/src/ufo/flags.py`, is the safe front door for reading those switches. Callers ask it for a flag value and also provide a default. If the flag service is not configured, is too slow, fails, or returns something the code cannot use, this layer returns the default instead. That keeps a user’s request from failing just because the flag system has a problem.

The extension file, `extensions/flagship/ufo_ext_flagship.py`, connects that safe front door to Cloudflare Flagship, the outside service used to store and serve flag choices. It also gives operators a way to change which variation a flag returns without redeploying the product. Together, these files act like a safe control panel: app code reads switches through a guardrail, while operators can adjust those switches in the backing service.

## [Extension discovery and capability registration](stage-3.md) `stage-3` — 59 files

This stage is the system’s plug-in intake desk. During startup, and sometimes during setup commands, the host looks for installed extensions, checks which ones are allowed by configuration, reads their manifest files, and turns those descriptions into features the rest of UFO can use. A manifest is like a registration card: it says “this extension provides these tools, apps, jobs, credentials, screens, agents, or backends.”

The core discovery infrastructure does the scanning, filtering, loading, and record keeping. It also chooses sandbox backends, which are isolated work areas where tasks can run safely. Provider and backend registration adds replaceable outside services, such as model providers, embedding search services, Redis message hubs, and remote terminals. Workspace app manifests register first-party apps like Chat, Code, Issues, Meetings, Metrics, Radar, Wiki, and Artifacts, including their agents, home screens, setup needs, and scheduled work.

Other manifest groups add agent skills, research and coding helpers, document and website tools, connectors, source syncing, Slack or iMessage surfaces, memory, monitors, objectives, scheduled tasks, report digests, debugging, web portals, and sample extensions. Together, they make UFO expandable without hardwiring every capability into the core.

### [Provider and backend registration](stage-3.1.md) `stage-3.1` — 6 files

This stage is part of the system’s plug-in setup. When UFO starts, these extension files tell the main program which outside services it can use, and how to connect to them. A “backend” here means a replaceable service that does one job, like running a model, making search embeddings, or passing live messages between servers.

The Bedrock extension registers Amazon Bedrock Mantle as a model source. It lists available Anthropic and OpenAI-compatible models, their costs, required credentials, and the client code needed to call them. The OpenAI embedding extension registers a service that turns text into numeric vectors, which are number lists used for search, and it splits large requests so they stay within safe limits.

The Redis hub package provides shared live communication. Its package marker only makes the code importable. Its manifest announces two optional Redis backends to the main system. The stream hub acts like a live notice board for turn updates, text chunks, costs, and completion messages. The stream terminal connects terminal users to remote work, using Redis for routing messages and blob storage for larger data.

### [Core extension discovery infrastructure](stage-3.2.md) `stage-3.2` — 4 files

This stage is the behind-the-scenes system that lets UFO find and use optional add-ons called extensions. An extension is a separate package that can add new tools, hooks, object types, skills, credentials, or sandbox backends to the core program. The manifest file defines the promise every extension must make: a clear description of what it provides, in a shape the runtime can understand. The loader is the loading dock. During startup or preparation, it looks for installed extensions, checks which ones are allowed, reads their manifests, and converts their declarations into usable parts for the rest of UFO. The store is the small catalog and lockfile manager. It lets commands search available extensions and record which ones are installed or remove them later, like keeping a shopping list and receipt. The sandbox selector connects this discovery work to execution. It takes sandbox backends registered by extensions, combines them with configuration, and chooses which sandbox carriers should stay available to open or resume isolated work areas.

### [Workspace app extension manifests](stage-3.3.md) `stage-3.3` — 15 files

This stage is the app catalog’s set of registration cards. It is mostly used during startup or installation, when the workspace host scans first-party extensions and decides what apps are available, what agents to create, what setup is needed, and what home screens or background jobs to enable. A manifest is a small configuration file that describes an app to the platform, rather like a label on a plug-in device telling the computer what it is.

Each app has a manifest for that job. Artifacts, Chat, Radar, and Wiki declare their agents and homepage skills, which are the pieces that show or control the app’s main screen. Code adds its packaged skills and GitHub setup needs. Issues and Meetings also describe scheduled work, meaning tasks the system should run on a timer. Metrics registers a workspace-wide reporting agent that can use connected accounts or keys.

The `__init__.py` files for Chat, Code, Issues, Meetings, Metrics, Radar, and Wiki do not run app logic. They simply mark folders as importable Python packages, so the host can find the extension code.

### [Agent skill and content-work extension manifests](stage-3.4.md) `stage-3.4` — 13 files

This stage is the system’s extension registry. It is mostly used during startup and shared behind the scenes, when UFO needs to discover what extra abilities are available. The small __init__.py files are like nameplates on folders: they mark brief_pipeline, coding, self_improvement, sites, and skill_create as importable Python packages, and sometimes add a short description.

The manifest.py files are the real sign-up sheets. Each one tells the host system what an extension adds and where to find its instructions. The brief pipeline manifest registers three helper agents for brief-making work. Browser adds a browsing subagent, web tools, and delegation prompts. Coding registers software-work child agents and coding guidance. Documents adds document skills and a writing agent. Research declares search tools, research agents, prompts, skills, and its required search backend. Sites registers website-building tools, agents, hooks, object types, surfaces, and background jobs. Skill creation enables members to save, edit, search, and index reusable skills. Self-improvement adds a scheduled evaluation job, like a timer that regularly checks and improves the system.

### [Connector, source, and communication extension manifests](stage-3.5.md) `stage-3.5` — 10 files

This stage is shared start-up support for plugging outside services into the main system. Most files here are manifests, which are like sign-in sheets for extensions: they tell the host application what exists, what it is called, what tools or web routes it provides, and what secrets or credentials it needs.

The Composio and Pipedream manifests register connector providers and their OAuth sign-in routes, so users can authorize outside services. The connectors manifest declares the general connector tools, shared objects, and prompt text that make those connections usable. The gbrain manifest registers two source options for reading gbrain content, either from GitHub or a local folder. The sources manifest registers source backends, credential slots, hooks, object types, and a retry job so external content can be synced into the system. The iMessage and Slack manifests register communication surfaces: tools, routes, deployment secrets, hooks, and status checks for messaging integrations. The __init__.py files for Slack, sources, and source providers are simple package markers, letting Python import those folders as usable modules.

### [Platform service, object, and surface extension manifests](stage-3.6.md) `stage-3.6` — 11 files

This stage is mostly startup wiring. It is like the labels and plug shapes on add-on parts: the code for each extension may exist elsewhere, but these files tell the main UFO platform what can be loaded and used. The small __init__.py files for app artifacts, UFO, and web simply mark folders as importable Python packages, so other code can find them.

The manifest files are the real registration cards. The debugger manifest adds a debugging tool and its web view. The memory manifest connects long-term memory to tools, automatic recall, search, indexing, cleanup jobs, object types, and a user surface. The monitors manifest registers monitor objects, actions, and a recurring checker. The objectives manifest adds tools and prompt context so the agent keeps track of ongoing goals. Report digest and scheduled tasks declare their object types, tools, writing or skill files, and background jobs. The UFO manifest exposes the live shell surface. The web manifest mounts the browser portal, its tools, jobs, and feature switches.

## [First-run onboarding, workspace creation, and shipped-agent provisioning](stage-4.md) `stage-4` — 6 files

This stage happens near the end of startup, when the system is ready to turn an empty installation into something people can use. It is the “move-in day” for a new workspace. The main onboarding flow creates the first workspace, adds the first administrator, checks that required secrets are present, creates the main assistant agent, and lets installed extensions run their own setup steps.

A small package marker makes this onboarding code importable by the rest of the project. The private onboarding control API is the trusted doorway for workspace and sign-in setup. It can create or find workspaces, seat members, list login choices, count workspaces, and read invitations, so these rules are not scattered across edge services.

Shipped-agent provisioning then turns agent declarations from installed extensions into real workspace agents, without overwriting agents that users changed or made themselves. Agent setup describes what those agents still need, such as connected accounts, credentials, or schedules, and reports that status to setup screens. A demo seeder can also create a rich sample conversation for testing and demos.

## [Server assembly, route mounting, and surface ingress](stage-5.md) `stage-5` — 32 files

This stage is where the service is put together and its outside doors are opened. It is part of startup, but it also shapes the main request path after the server is running. The central file, core/src/ufo/serve.py, is like the building manager: it starts the web server, connects databases and background workers, loads extensions, and makes sure each request is matched to the correct workspace.

Once the host is assembled, the authentication pieces act as the badge desk. They sign people in, finish OAuth account-linking flows, and create signed tokens, which are small tamper-proof passes that prove a request belongs to a trusted user, workspace, or browser entry point.

The mounted surfaces are the actual doors people and systems use. The web app, Slack, iMessage, terminal, debugger, and related tools each receive events in their own format. Shared surface code translates those events into the system’s normal records: workspace, member, conversation, and message turns. Together, these parts let outside activity safely enter UFO and become work the runtime can understand.

### [Authentication, login, OAuth, and signed ingress tokens](stage-5.1.md) `stage-5.1` — 12 files

This stage is the system’s front door and badge-checking desk. It is shared support used when people sign in, connect outside accounts, or open browser routes that need a trusted identity. The core auth package starts with a simple package marker so other code can import it. Its token files create signed tokens: small pieces of data protected with a secret so the system can later tell they were made by UFO and were not changed. Bearer tokens identify a member and workspace. Surface tokens label public routes with the right workspace before cookies exist. Sandbox ingress tokens add expiry and keep different browser access uses from being mixed up.

The extension files handle real account-connection journeys. The web extension connects personal Anthropic or OpenAI credentials and checks them before saving. The CLI surface finishes OAuth redirects and shows the completion logo. Composio and Pipedream act as bridges to hosted consent pages. Slack tools help install and search Slack. The iMessage tool lets an approved member reserve and verify a phone number. Together, these pieces let UFO know who is arriving and safely link the outside services they want to use.

### [Mounted user-facing and operator-facing surfaces](stage-5.2.md) `stage-5.2` — 19 files

This stage is the system’s front desk. It sits in the main work path, where outside places like the web app, Slack, iMessage, the terminal, and operator tools enter the runtime. A “surface” means one of these user-facing doors.

The web portal serves the browser app, signs members in, lists agents, sends messages, streams replies, shows transcripts, connects accounts, and edits workspace data. Web panels turn button or form submissions into normal recorded tool actions, while starters prepare useful first-prompt suggestions.

Shared surface code turns outside requests into trusted workspace, member, and conversation records. The hub broadcasts live turn updates, and hub tail helps late or reconnecting viewers catch up and finish cleanly.

Slack code verifies Slack events, converts messages and clicks into turns, posts replies and files back, formats mentions, adds safe bot attribution, and runs Slack-specific hooks. iMessage code chooses a real Spectrum Cloud line or a safe local fake line, then converts iMessages to conversations and UFO replies back to texts. The terminal surface turns runtime events into simple directives for the command-line client.

Operator-only routes serve the debugger and memory viewer, with shared sign-in and workspace-picking. Package marker files only make these modules importable.

## [Conversation ingress, turn admission, and live turn control](stage-6.md) `stage-6` — 3 files

This stage is the front door and traffic controller for a conversation. It is used when something wants to start or continue work: a user message, a scheduled wake-up, an outside event, or an internal resume request. Before the main worker can run, the system must decide whether the new “turn” belongs in the queue and whether it is safe to run now.

The admission code checks the basics once at entry: who is allowed to act, whether there is an available seat, whether spending limits allow more work, whether this delivery is a duplicate, and whether the turn is in the right order. The ambient reply code is a cost-saving filter. If a thread message does not directly call on the agent, it decides whether the agent should stay quiet instead of creating a full turn. The dispatch code is the queue’s gatekeeper. It advances the conversation one turn at a time and only hands a queued turn to the workflow runner when no other turn for that conversation is already running.

## [Per-turn host environment assembly](stage-7.md) `stage-7` — 16 files

This stage runs just before each model turn. Its job is to set the table: decide exactly what the model may read, what tools it may use, which helper agents it may start, and what files are placed in its working sandbox. The host package marks this as the system’s “outside world” layer, where prompts, tools, skills, extensions, and files are gathered.

The main builder, `assemble.py`, acts like a careful dispatcher. It combines the system prompt, selected skills, seeded files, model choice, extension context, and tool access into one complete turn environment. It also enforces safety rules: environment documents may narrow or reshape what was already allowed, but cannot quietly grant extra power.

Two supporting areas feed this builder. Prompt and skill resolution prepares the instructions, reusable skill cards, model catalog, and reproducible environment documents. Spawn-target preparation builds the menu of allowed child agents or pipeline steps. Together, these parts make each turn predictable, traceable, and limited to the powers chosen for that moment.

### [Prompt, skill, and environment-document resolution](stage-7.1.md) `stage-7.1` — 8 files

This stage is shared behind-the-scenes support for each agent turn. It prepares the “reference material” the model will see and makes sure it can be reproduced safely later. The prompt renderer fills in prompt templates, checks that every blank was filled, and records a fingerprint, like a version stamp, so prompt changes are traceable. The delivery register supplies common writing rules for messages sent to users or other agents.

Skills are reusable instruction cards, sometimes with files. The skills runtime defines how they are read, registered, connected to dependencies, and copied into the sandbox, which is the agent’s working area. Skill selection chooses which saved skill cards to show the model on a given turn, so useful abilities are visible without flooding the conversation. The catalog skill builds a live table of available AI models, costs, limits, and features.

Environment documents capture changes to a turn’s prompt, tools, skills, model, and files. They are stored by cryptographic digest, a unique content fingerprint, so the same document can be loaded exactly again, while safety checks prevent documents from adding broader powers than the turn already allowed.

### [Spawn-target and subagent availability preparation](stage-7.2.md) `stage-7.2` — 6 files

This stage happens before the main agent can hand work to a child agent. It prepares the menu of “spawn targets,” meaning the helper agents or pipeline steps that the current turn is allowed to start. The spawn catalog is the central clerk. It gathers all available targets, checks what each one needs as input, and describes the result each one should return, so the main agent can choose safely.

Several files define the items that can appear on that menu. The runtime profiles file provides the default general-purpose helper, used when no more specialized helper fits. The browser extension adds a web-capable helper that can use browser tools. The documents extension adds a writing helper for drafting and editing prose. The sites extension adds a website-building helper. The brief pipeline extension adds three ordered writing steps: outline, draft, and critique. Together, these definitions let the system turn a vague need for help into a clear, callable target with known instructions, tools, inputs, and outputs.

## [Agent engine and model conversation loop](stage-8.md) `stage-8` — 14 files

This stage is the agent’s main work loop. After a user request or subagent task is placed in the queue, core/src/ufo/runtime/queue.py claims that work, prepares the needed model, tools, sandbox, and settings, and starts real execution. core/src/ufo/runtime/engine.py then runs the turn in a crash-safe way: it records progress, avoids repeating completed model calls or tool actions, tracks billing, and publishes the final result.

Inside that turn, core/src/ufo/harness/agent.py is the steering wheel. It asks the model what to do, runs any requested tools, sends the tool results back, and repeats until the model gives a final answer or the turn runs out. The model provider adapters are the plug converters that let this same loop talk to Anthropic, OpenAI, OpenRouter, and similar services. The streaming normalizer turns live, messy model output into clean text, tool calls, usage data, and final records. If the conversation gets too long, the compaction system summarizes older messages so the model can keep going. Extension hooks can approve, block, or modify actions, and activity labels turn tool calls into friendly user status messages.

### [Model provider request/stream adapters](stage-8.1.md) `stage-8.1` — 6 files

This stage is shared behind-the-scenes support for talking to AI model services. UFO has its own internal shape for a model request and for streamed reply events. These files translate between that common shape and the different outside providers, like plug adapters for different wall sockets.

The package file simply makes the models folder importable. The catalog is a built-in list of directly supported Anthropic and OpenAI models, including their names, limits, prices, and which API key setting to use. The registry is the main lookup desk: when the system needs a model, it finds the model ID, provider, price rules, and builds the right client.

The Anthropic adapter turns UFO messages into Claude Messages API calls, then converts Claude’s streaming output back into UFO events, including tool calls, images, reasoning text, retries, and usage counts. The OpenAI adapter does the same for OpenAI-style services, including Chat Completions, Responses, and Codex backends. The OpenRouter extension adds another provider bridge, including chat plus image and video generation tools.

### [Conversation compaction and context-window management](stage-8.2.md) `stage-8.2` — 2 files

This stage is behind-the-scenes support for long-running conversations. AI models can only read a limited amount of text at once; this limit is called the context window. When the transcript grows too large, the system must make room without losing the thread of the work.

The context helper checks the conversation size before sending it to the model. If it is too big, it chooses an older section to shorten while leaving the newest messages untouched, like keeping the latest pages of a notebook open and filing older pages into a brief report. It also checks that the shortened version is actually smaller, and it can retry safely if even the request to summarize is too large.

The compaction logic does the actual shrinking. It asks for a structured summary of the older transcript, validates that the summary has the expected shape, then replaces that older text with the summary. It also records what was replaced, so the system has a durable trail of the change.

### [Streaming model output normalization](stage-8.3.md) `stage-8.3` — 1 files

This stage sits in the main work loop, during one conversation turn with a model. Model providers send their answers as a live stream, but that stream can be uneven: pieces of text may arrive out of order, tool requests may be split across messages, and accounting details may only appear at the end. This stage turns that messy feed into clean events the rest of the system can trust.

The key file, core/src/ufo/harness/rounds.py, runs one full “round” with the provider. It listens to each incoming stream item, separates ordinary text from special events like tool calls and reasoning blocks, and only shows safe text to the user as it becomes available. At the same time, it keeps careful notes: what the model said, which tools it asked for, how much usage was reported, and how the turn ended. When the stream finishes, it packages all of this into a durable final record. In effect, it acts like a filter and recorder between a noisy live broadcast and a clean transcript.

## [Tool dispatch and sandboxed command/file execution](stage-9.md) `stage-9` — 23 files

This stage is part of the system’s main work loop. It is used whenever the model asks to use a tool, such as running a command, editing a file, asking the user a question, or starting a helper agent. First, the tool contract and registry act like a rulebook and catalog. They check that the requested tool exists, that the request has the right shape, and that the tool only gets the permissions it needs. The tool context is the safe doorway the tool works through, with path checks and limits to prevent accidental or unsafe file access. Long-running task journals let slow commands continue and be checked later.

The sandbox carrier lifecycle provides the protected workspace where risky work happens. It may be local, Docker-based, cloud-based, or connected to a user terminal, but the rest of the system sees one common interface. The bridge lets sandboxed code ask the main runtime to run approved tools without bypassing normal rules. Built-in tools provide the everyday abilities. The REPL extension adds persistent Python and JavaScript scratchpads, while the batching helper helps the harness run ordered work safely.

### [Tool contract, context, and long-running task journals](stage-9.1.md) `stage-9.1` — 8 files

This stage is shared support for the system’s tool use. It sets the rules for how the main runtime, sandbox, and individual tools talk to each other, and it keeps that work safe while the system is running. The bridge contract defines the “wire format,” meaning the exact shape of messages sent between the runtime and the sandbox, plus the tool names that are allowed. The registry is the catalog: it names tools, checks that their descriptions are valid, and finds the right tool when the model asks for one.

The tool context is the controlled doorway every tool receives. Through it, a tool can access files, connected accounts, child agents, shared artifacts, and paid media metering, but only with the permissions it has been given. Containment adds a guardrail around file paths so untrusted input cannot escape the intended folder, even with tricks like symlinks. File-change limits keep path sizes consistent. Task journals let long shell commands keep running after the caller stops waiting. The package files simply mark and describe these tool areas.

### [Sandbox carrier lifecycle](stage-9.2.md) `stage-9.2` — 10 files

This stage is the backstage workshop for each conversation. It provides the place where commands run, files are stored, and temporary services can be reached. The rest of the system talks to it through a common “sandbox session,” which is a safe doorway for running commands, reading and writing files, loading skills, and dialing ports without caring whether the workspace is local, Docker, E2B, or a user’s terminal.

conversation.py makes sure a conversation keeps returning to the same private workspace. local.py runs work directly on the host for simple development. terminal.py sends work to a connected user terminal. The Docker and E2B extensions provide stronger carriers: they create or reconnect to containers or cloud sandboxes, prepare them, move files, run commands, route network traffic through the proxy, and stop or restart them when needed.

Supporting pieces keep setup predictable. cache.py points sandbox traffic at approved caches. client_binary.py finds the prebuilt UFO client without building it. exec_env.py prepares safe environment variables, using placeholders instead of real secrets. __init__.py simply makes this sandbox code importable.

## [Browser automation and hosted browser sessions](stage-10.md) `stage-10` — 25 files

This stage gives the assistant a working web browser during its main work loop. When a task needs the web, it either starts, rents, or connects to Chrome, then controls pages through the Chrome DevTools Protocol, a standard “remote control” channel for Chrome.

The in-process browser tool surface is the part the assistant talks to directly. It turns requests such as open a page, click a button, type text, upload a file, read page content, switch tabs, or take a screenshot into careful browser actions. It also waits for pages to settle, handles pop-ups, tracks downloads, and cleans up at the end of the turn.

The remote and sandbox-hosted Chrome providers supply the actual browser. They can run Chrome inside the sandbox, connect to Browserbase, or delegate work to Browser Use, depending on what the task needs.

The cdp.py file is the message pipe to Chrome. It sends commands over a WebSocket, waits for replies, and routes browser events back to the right waiting code.

### [In-process browser tool surface](stage-10.1.md) `stage-10.1` — 21 files

This stage is the browser control layer used during the assistant’s main work loop. It is the “tool surface” inside the running process: the assistant asks to open a page, click, type, read text, manage tabs, or save a download, and this layer turns that request into safe Chrome actions.

tools.py exposes the tools, while backend.py creates the per-turn bridge to Chrome and cleans it up afterward. session.py coordinates the live browser connection. tabs.py manages pages and loading, and downloads.py watches saved files, including PDFs. computer.py runs basic actions like click, scroll, wait, and screenshot; keys.py sends real keyboard events; coordinate.py maps screenshot points to browser pixels.

page.py, content.py, and find.py turn web pages into readable text and stable element references. forms.py fills fields and uploads files. settle.py waits until an action is ready to continue. dialogs.py handles pop-ups. fixup.py repairs small instruction mistakes before they reach Chrome.

actions.py, errors.py, wire.py, and runtime.py define shared message shapes, errors, JavaScript execution, and Chrome message checks. The __init__.py files simply make these folders importable packages.

### [Remote and sandbox-hosted Chrome providers](stage-10.2.md) `stage-10.2` — 3 files

This stage is behind-the-scenes support for giving the system a Chrome browser to work with, even when that browser is not running on the same machine. It covers three ways to provide a browser.

The Browser Use file connects UFO to the hosted Browser Use service. Instead of UFO driving every click and page check itself, it can hand off one browsing task, or many tasks in parallel, to that service. Sensitive API keys stay outside the sandbox, so the isolated work area does not need to hold them.

The Browserbase file connects UFO to Browserbase, a remote Chrome hosting service. It can start a new browser session, reconnect to an existing one, move files in and out, and clean up the rented session when finished.

The sandbox Chrome file starts Chrome inside the sandbox for a single turn. It exposes Chrome through the Chrome DevTools Protocol, a standard remote-control doorway for browsers. It keeps ports, downloads, proxy settings, and cleanup separate so parallel turns do not interfere.

## [External connector, research, and service-tool execution](stage-11.md) `stage-11` — 21 files

This stage is the system’s controlled doorway to the outside world. It is shared behind-the-scenes support used while an agent is doing its main work and needs something beyond the local sandbox, such as calling Gmail or Slack, searching the web, fetching a page, or using a service-specific tool. Its main safety job is to let those calls happen without handing secret keys or account tokens to the agent.

One part is the connector broker system. Think of it like a reception desk for outside apps. It helps agents discover available services, check which actions they offer, confirm which connected account may be used, and run actions through brokers such as Composio, Pipedream, keyed API connectors, or MCP servers. The broker talks to the real service and returns only the approved result or files.

The other part is the research desk. It gives agents safe tools for web search, page reading, Perplexity-backed answers, broader multi-topic research, and source tracking. Together, these parts let agents use external knowledge and services while keeping access controlled and auditable.

### [Brokered connector discovery and action calls](stage-11.1.md) `stage-11.1` — 15 files

This stage is the system’s brokered doorway to outside apps. It is shared behind-the-scenes support used when an agent needs to find a connector, link an account, inspect available actions, run an action, or move files safely. The key idea is that the agent can use services like Gmail, GitHub, Slack, Datadog, or an MCP server without directly seeing private tokens or passwords.

The core access files set the rules. connectors.py decides how tools and feed-sync jobs may talk to outside services. grants.py records who connected which account, which agent may use it, and how access can be shared, revoked, or disconnected.

The connector tools extension gives agents a searchable front door: find connectors, inspect real tools, run them through a broker, and stage files in or out. Composio and Pipedream each add a broker plus a client. The clients talk to those platforms; the brokers translate UFO’s requests into provider calls and return results and files. Composio also supports proxy HTTP calls and one-shot MCP tool calls. Keyed connectors declare API-key services. The MCP extension discovers and runs tools from workspace-configured MCP servers.

### [Research and web search tools](stage-11.2.md) `stage-11.2` — 6 files

This stage is the system’s research desk. It is shared support used when an agent needs outside information during its work: searching the web, opening pages, looking in special areas like images or academic papers, and keeping track of sources for later citation.

At the center is `core/src/ufo/runtime/search.py`, which defines the common shape of a search request and a page-fetch request. This lets the rest of the system ask for information without caring which search company is actually used. `extensions/perplexity/ufo_ext_perplexity.py` is one bridge for that contract. It translates the system’s requests into Perplexity API calls, checks the answers, and converts them back into the project’s normal result format.

`extensions/research/ufo_ext_research/tools.py` exposes these abilities as tools an agent can safely use, while hiding service credentials. `delegation.py` adds a bigger “wide research” tool, like sending several assistants to research many subjects at once, then saving their combined results and progress. `observations.py` records the sources found, tied to the current workspace and conversation turn. `__init__.py` simply makes the research extension importable.

## [Subagents, delegation, and child-turn orchestration](stage-12.md) `stage-12` — 5 files

This stage is about delegation during the main work loop. When the main agent has a job that is better done by a specialist, it can start a child agent, give it a clear task, wait for the answer, and fold that answer back into the parent turn. It works like a manager assigning a focused task to a teammate.

The core piece is `subagents.py`. It checks that the requested helper is allowed, prepares the child run, sends the right input, and accepts only a validated result. `turns/contracts.py` defines those input and output shapes, called contracts: agreed data formats that prevent confusing or unsafe messages from being passed around.

The extension files add concrete kinds of helpers. The browser delegation code lets the parent ask a browser agent to visit one site or many sites in parallel. The research subagent configuration defines normal and deep research helpers, including their tools, prompts, expected data, and model choices. The sites delegation code adds a website-building helper, so larger site work can happen in a focused child session while sharing the same project workspace.

## [Workspace object system and domain object handling](stage-13.md) `stage-13` — 29 files

This stage is shared support used whenever the workspace needs to show or change its “objects,” which are durable named records such as agents, members, artifacts, connectors, sites, or todos. It is not a startup or shutdown step. It is the behind-the-scenes switchboard for list, read, explain, create, update, delete, and action requests.

The central switchboard is objects.py. It checks that each request has the right shape, finds the correct built-in or extension handler, and applies safety rules such as visibility, workspace membership, admin rights, and the agent being acted for. object_name.py gives every record a standard address like kind/name, so all parts speak the same language. listings.py keeps long lists paged cleanly. object_scope.py carries the current agent target safely during dispatch. object_views.py turns available actions into safe descriptions for the model or user interface.

The built-in object stage covers core workspace records such as members, agents, conversations, artifacts, credentials, and extensions. The extension-owned object stage lets add-ons expose their own records through the same rules. The package marker files simply make these object modules importable.

### [Built-in workspace, member, agent, conversation, artifact, and credential objects](stage-13.1.md) `stage-13.1` — 9 files

This stage is shared behind-the-scenes support for the workspace. It defines the built-in objects, meaning records that chat tools can list, inspect, or sometimes change. Together they are the system’s “control panel” for a workspace.

The workspace object shows basic workspace facts, such as members and seats, but cannot be edited directly. Member objects say who belongs, who is an admin, and who has access; admins can also add a person before they first sign in. Agent objects represent the assistants in the workspace and control who may create, edit, archive, or restore them. Prompt governance adds a safety gate: prompt changes become proposals and are applied only after approval, and only if the prompt has not changed meanwhile.

Conversation objects let the system find past chats and show visible transcripts, while artifact objects manage files shared from those chats with access checks. Credential objects show which secret slots extensions need, but never reveal the secret values. Surface objects show connected chat entry points, and extension objects show what loaded extensions contribute without allowing install or removal through these tools.

### [Extension-owned objects](stage-13.2.md) `stage-13.2` — 13 files

This stage is shared behind-the-scenes support for “extension-owned objects.” These are workspace items that belong to add-on features, but still appear through the same object system users and agents already use. It is like giving many different tools the same kind of label, shelf, and access rules.

The source files define outside content to sync, synced read-only pages, and triggers that wake conversations when watched content changes. Connector objects represent linked third-party accounts and guard actions like sharing, revoking, or disconnecting them. GBrain source objects register places where Markdown knowledge pages come from. Memory objects expose stored memories and member profiles for reading, while leaving updates to memory jobs.

Other files make monitors visible as stoppable watches, report digests visible as read-only records of scheduled runs, and hosted sites manageable through list, share, publish, privatize, or unhost actions. The skill store saves and protects user-created skills. The todo extension manages per-conversation checklists. Small package files simply make extension modules importable. Together, these parts let many extensions plug into one common workspace experience.

## [Artifacts, media previews, hosted sites, and generated app outputs](stage-14.md) `stage-14` — 22 files

This stage is the system’s sharing and publishing workshop. It comes after work has been created, and turns that work into things people can open: downloadable files, document previews, live site links, app homepages, screenshots, and signed downloads. Much of it runs behind the scenes, making sure shared output is useful but still controlled.

The shared file storage part is the guarded file counter. It creates temporary signed links, which are web addresses that prove who may fetch a file and for how long. It serves downloads only when that proof checks out, makes safe previews for documents and images, and retries preview jobs that failed earlier.

The hosted sites and app homepage part is the publishing desk. It records who owns each site, builds and audits app pages, moves source files into sandboxes, creates temporary or permanent public links, serves site traffic, reports broken sites, and makes preview cards or screenshots.

The media package marker simply makes the media runtime folder importable by the rest of the codebase.

### [Shared file storage and signed download links](stage-14.1.md) `stage-14.1` — 7 files

This stage is shared behind-the-scenes support for files that the system stores or shares. It is about letting the right people download files, making previews safely, and repairing previews that failed earlier. artifact_url.py creates short-lived signed links, meaning web addresses with a built-in proof that says which file, workspace, and time limit they are valid for. artifacts.py is the download doorway: it serves files only when that proof is valid, and lets signed-in workspace members refresh an expired link.

The preview side works like a cautious inspection station. document_renderer.py sends uploaded documents to a preview service, then checks that the returned page images and text are safe before using them. sandbox/preview.py tells the sandbox where that preview service lives without exposing its secret access token. image_previews.py checks small preview images for honest type, size, dimensions, animation frames, and memory cost. previews.py defines the simple record that stores a preview’s storage key and size. preview_renderer.py retries recent shared files whose previews failed, asks the preview service for a PNG, and saves the result.

### [Hosted sites and app homepage publishing](stage-14.2.md) `stage-14.2` — 14 files

This stage is shared support for publishing small websites and app homepages. It turns work made inside a safe sandbox into links that other people can open, while keeping ownership, privacy, and cleanup under control.

The site registry in store.py is the record book: it remembers who owns each site, what serves it, and who may view or edit it. tools.py gives builder agents controlled actions to preview, deploy, publish, test, and assign sites as homepages. application_builder.py runs the full build workflow, while application_audit.py checks the finished app against design, behavior, and product requirements. source.py moves saved source files into and out of the sandbox and supplies the standard page-building kit.

The ingress files are the public doorway. ingress_host.py and ingress_url.py create signed, temporary addresses; ingress_serve.py serves stored files or forwards traffic to a live sandbox port; site_report.py reports broken live sites without spamming repairs.

surface.py handles permanent share pages and visibility changes. conversation_slot.py shows conversation-owned sites in the chat view. share_card.py and site_previewer.py create preview images. main_homepage.py removes old seeded homepages when the built-in chat homepage should take over.

## [Source sync, indexing, memory, and enrichment pipelines](stage-15.md) `stage-15` — 76 files

This stage is the system’s background knowledge pipeline. It runs after a member connects an account, during scheduled syncs, or when stored knowledge is needed for a later prompt. First, external source connectors poll outside tools like Slack, GitHub, or Stripe and translate their records into a common “page” format. The core sync code then saves those pages in the database and blob storage, and keeps an ordered feed so indexers can replay changes safely.

Not all sources are remote services. The gbrain files let a local folder of Markdown notes act like a source too. They check file safety, read valid text, and choose useful page titles.

Next, the indexing and memory parts split pages into searchable chunks, make optional “embeddings” or meaning fingerprints, store them in a local or Turbopuffer-backed index, and recall relevant facts later. The memory condenser cleans raw notes into summaries and durable facts.

Finally, the enrichment extension adds consent-based profile lookup. Its manifest defines the feature, providers fetch or replay person and company data, and the store records consent, results, and rate-limit pauses.

### [External source connector polling](stage-15.1.md) `stage-15.1` — 60 files

This stage is the system’s behind-the-scenes intake layer for outside services. It runs during syncing, after an account is connected, and repeatedly asks external APIs, or software “front desks,” what data is available. The many connector groups cover workplace tools, project trackers, developer tools, CRMs, support systems, marketing platforms, finance apps, HR tools, scheduling services, and document-signing systems. Each connector knows the quirks of one service, such as Slack, GitHub, Stripe, HubSpot, Google Sheets, or Linear, and turns that service’s records into a common shape.

The shared files are the engine under these adapters. The registry is the address book that maps a source name to the right connector. The connector code defines the common rules, including cursors, which are bookmarks that let a sync resume safely. The REST helper makes web requests, retries temporary failures, and follows page-by-page results. The backend turns connector output into internal pages and guards against bad records or runaway backfills. The connected-account helper creates the first feed rows when a member links an account, and can repair missing rows later.

#### [Workspace, communications, and office-suite sources](stage-15.1.1.md) `stage-15.1.1` — 10 files

This stage is part of the system’s behind-the-scenes intake work. It connects to workplace tools, checks what has changed, and turns emails, meetings, files, chats, and calendars into steady “source streams,” meaning ordered records the rest of the product can store, search, and update.

The Google pieces cover the main Google Workspace apps. Gmail reads mailbox changes and avoids rereading everything each time. Google Calendar reads events and also records attendees separately. Google Docs, Drive, Meet, and Sheets fetch documents, files, meeting notes or transcripts, and spreadsheet rows, then convert them into searchable text or structured records. The shared Google helper decides whether an API error means “you do not have access” or “try again later because Google is limiting requests.”

The Microsoft pieces do the same kind of translation for Outlook and Teams through Microsoft Graph, Microsoft’s web doorway into mail, calendars, contacts, teams, chats, channels, and messages. Slack reads users, channels, messages, threads, and participants. Together, these connectors act like adapters for different office tools, making them all look alike to the rest of the system.

#### [Work management, knowledge, developer, and incident sources](stage-15.1.2.md) `stage-15.1.2` — 12 files

This stage is the system’s set of “adapters” for outside work and knowledge tools. It runs behind the scenes during syncing, when the product pulls information from other services so it can later search, remember, or summarize it. Each file knows how to speak one service’s web API, meaning its online interface for reading data.

The Git connector treats Markdown files in a GitHub repository as pages, checking for changes before downloading. The Asana, ClickUp, Linear, monday.com, Wrike, and Jira connectors read project-management data such as tasks, issues, comments, users, boards, folders, goals, and sprints. The Confluence and Notion connectors turn wiki pages, databases, comments, and blocks into readable searchable text. The GitHub connector reads repositories, issues, comments, and users across organizations and repositories. PagerDuty and Sentry bring in operational data, such as incidents, on-call schedules, services, error issues, events, and releases.

Together, these connectors act like translators at many doors: each opens a different service, walks through its pages safely, and turns what it finds into common records the rest of the system can store and use.

#### [CRM, sales, and customer-support sources](stage-15.1.3.md) `stage-15.1.3` — 7 files

This stage is a set of read-only connectors for tools used by sales teams, marketers, and support teams. It is behind-the-scenes support for the main sync work: when the product needs customer or conversation data, these files know how to ask each outside service for it and turn the answers into a common stream of records.

Each connector is like an adapter for a different plug shape. Apollo reads contacts and accounts from its search APIs. Attio reads CRM data such as companies, people, deals, tasks, notes, meetings, and call recordings. Freshdesk reads support records and carefully follows its different page-by-page result formats. HubSpot covers a wide range of CRM and marketing data, including contacts, companies, deals, conversations, analytics, and custom objects. Intercom reads conversations, contacts, companies, tickets, teams, tags, and related details. Salesforce reads common business objects like accounts, contacts, opportunities, and cases. Zendesk reads tickets, users, organizations, Help Center articles, and community posts. Together, they make many outside systems look consistent to the rest of the product.

#### [Marketing, ads, social, forms, and tabular-data sources](stage-15.1.4.md) `stage-15.1.4` — 8 files

This stage is part of the system’s data intake work. It is a set of connectors, meaning small adapters that know how to talk to outside services and turn their replies into the project’s standard record format. Together, they let the system bring in marketing, advertising, social, form, and table-based data so it can be stored, synced, and searched later.

The marketing connectors read customer and campaign tools: ActiveCampaign, Klaviyo, and Mailchimp. They know which objects exist, such as profiles, lists, campaigns, events, and nested collections, and how to move through paged API results. The advertising connectors read Facebook Ads and Google Ads, including accounts, campaigns, ad groups, ads, and performance numbers. Instagram reads business accounts, posts, stories, and analytics through Facebook’s API. Airtable discovers bases, tables, and rows, acting like a bridge to flexible spreadsheet-like data. Typeform reads forms, responses, workspaces, and related assets. Each file handles login, API calls, paging, and record shaping for its own service.

#### [Finance, billing, accounting, and spend sources](stage-15.1.5.md) `stage-15.1.5` — 9 files

This stage is shared behind-the-scenes support for the system’s data syncing work. Its job is to connect to finance services, ask them for records through their web APIs, and turn the answers into a common stream of records the rest of the system can store, search, and reuse. An API is a service’s “front desk” for software requests.

Each file is an adapter for one outside service. Brex, Ramp, and Mercury bring in company spending and banking data, such as cards, expenses, transfers, bank accounts, and transactions. Chargebee, Recurly, and Stripe bring in subscription billing data, including customers, invoices, subscriptions, coupons, and payments. Square brings in commerce data such as orders, refunds, inventory, locations, and catalog items. QuickBooks and Xero bring in accounting records like accounts, contacts, invoices, and payments.

Together, these adapters work like different plug shapes for one power strip: each understands its own service’s login, paging, and record format, then outputs data in the system’s standard shape.

#### [People operations, recruiting, scheduling, and agreement sources](stage-15.1.6.md) `stage-15.1.6` — 9 files

This stage is shared behind-the-scenes support for bringing people-related business data into the system. Each file is a connector, like an adapter plug, for a different outside service. The connectors call each service’s web API, meaning its internet-facing data doorway, and turn the results into steady streams of records that the rest of the system can store, search, and recall.

The recruiting connectors cover Ashby, Greenhouse, and Recruitee. They pull details such as candidates, jobs, applications, interviews, offers, departments, and lookup lists, while handling pages of results and any extra nested records. The HR and workforce connectors cover BambooHR, Deel, and Rippling. They fetch employee, company, team, contract, payslip, timesheet, task, and form data. Calendly adds scheduling data, including users, event types, groups, scheduled events, and invitees. DocuSign and PandaDoc bring in agreement data such as envelopes, documents, templates, and contacts. DocuSign also first discovers the correct regional server for the account before syncing.

### [Indexing, embeddings, and recall](stage-15.2.md) `stage-15.2` — 8 files

This stage is shared behind-the-scenes support for memory and search. Its job is to turn saved text into pieces that can be found later, either by matching words or by matching meaning. “Embeddings” are the meaning fingerprints made from text, so similar ideas can be found even when the same words are not used.

The shared indexing code defines the common recipe for splitting long text into smaller chunks and preparing them for storage. The default index is the built-in option: it stores chunks locally with optional embeddings and searches them. The Turbopuffer extension does the same job through an outside search service.

The memory extension builds on these indexes. Its store records durable memories, searches them, and keeps indexed facts and synced pages up to date. The condenser acts like an editor, turning raw notes into cleaner facts, summaries, page sections, profiles, and curated pages. The runtime memory interface gives the rest of the system one simple way to search or browse recent memories, no matter how they are stored. The package file describes this extension’s role, and the events file defines the safe, limited recall event used before a reply.

## [Scheduled, recurring, and long-running background work](stage-16.md) `stage-16` — 29 files

This stage is the system’s background crew. It runs during normal operation, after startup, and keeps working even when no person is actively clicking. Its timer and monitor parts act like alarm clocks and watchmen: they fire scheduled conversations, resume paused work when a deadline passes, and poll outside signals until something changes.

The self-improvement and objective parts keep longer efforts on track. Objective tools record plans, steps, evidence, and blockers so progress is based on real state, not guesses. The self-improvement loop studies past failures, proposes prompt changes, tests them on old cases, and only offers safer improvements for human approval.

The runtime files provide the machinery that makes all this safe. candidates.py finds which workspaces may have pending work without breaking workspace boundaries. jobs.py turns job definitions into scheduled runs and prevents runaway piles of work. runtime_instance.py records which server processes are alive and cleans up work left stuck by crashes. product.py reports product usage metrics. The report digest files summarize scheduled reports into short feed entries and avoid reprocessing reports with no useful changes.

### [Timers, pauses, monitors, and scheduled turns](stage-16.1.md) `stage-16.1` — 13 files

This stage is the system’s alarm clock and watchman. It runs behind the scenes after startup, during normal operation, to make sure future work happens at the right time and only once.

Scheduled task files store reminders and recurring prompts. schedules.py keeps them in the database and lets users create, edit, cancel, and list them. cron.py understands “cron” rules, a compact five-part way to say times like “every weekday morning.” runner.py wakes up on a clock tick, claims due tasks safely, fires them, and sets the next run time. scheduled_fire.py gives each planned run a shared name so different code agrees about what is firing.

Pause files support “wait until a person replies, or until time runs out.” tools.py exposes scheduling and pause actions to agents. pauses.py stores paused conversations and prevents double resumes. pause_runner.py wakes expired pauses and resumes the workflow if no human already did.

Monitor files handle one-time watches on outside state. monitor_tool.py creates a watch after testing its command. monitors.py stores and claims watches. monitor_runner.py checks them until they change, fail, expire, or stop. The __init__.py files simply make these extensions importable.

### [Self-improvement and objective maintenance](stage-16.2.md) `stage-16.2` — 10 files

This stage is shared behind-the-scenes support for two long-running jobs: keeping objectives honest, and helping the system improve its own prompts with human approval. The objectives package marker lets the rest of the project import this extension cleanly. Its store is the notebook: it records objectives, steps, attempts, blockers, and checks so later turns know the real history. Its tools are the controls: they let users plan, inspect, delegate, and mark progress, but they verify evidence against actual state before accepting that a step is done.

The self-improvement side is like a careful test kitchen. The model wrapper gives all parts one simple way to ask the language model for text. The corpus builder finds past failed tool uses and turns them into training and test examples. The proposer asks for a better prompt based on real failures. Replay reruns old conversations with a new prompt while keeping tool results fixed. Evaluation compares old and new answers, using a judge model. The gate checks whether the change truly helps without obvious harm. Cron runs this loop on a schedule and opens only tested proposals for humans to approve.

## [Packaged skills and document automation utilities](stage-17.md) `stage-17` — 25 files

This stage is a toolbox of packaged “skills,” meaning add-on abilities, and small command-line scripts that agents can run when documents need extra work. It is mostly shared behind-the-scenes support, not the main work loop. Think of it as a workshop beside the main system.

The document review scripts keep review findings in a saved state file, then turn those findings into visible comments or highlights in PDFs, PowerPoint files, and spreadsheets. The DOCX, PPTX, and XLSX utilities work with Microsoft Office files without opening the normal apps for a user. They can unpack files into editable parts, add comments, repair presentations, recalculate spreadsheet formulas, and pack files back up. The PDF helpers cover common PDF jobs: filling real form fields, placing text on form-like pages, and rendering pages as images.

Finally, the skill discovery and sample probe pieces help the system recognize skill packages, list community skills, fetch their descriptions, and check that a sample skill is installed and runnable. Together, these parts let agents inspect, modify, validate, and present documents safely inside the sandbox.

### [Document review state and annotation scripts](stage-17.1.md) `stage-17.1` — 7 files

This stage is the document-review skill’s backstage workbench. It does not review the document by itself. Instead, it stores what the review has found and turns those findings into comments that people can see in the final files.

The small __init__.py file simply makes the scripts folder importable by Python. constants.py keeps the agreed file names for the saved review state and the review log, so every script looks in the same place. models.py defines what a review issue looks like, such as the problem found and where it belongs, and can format an issue as a readable comment.

manage_state.py is the record keeper. It updates a JSON state file, which is a plain text data file, with sections, claims, issues, progress, summaries, and an audit trail. The annotation scripts then use that saved state. annotate_pdf.py highlights matching PDF text and adds notes. annotate_pptx.py writes findings as PowerPoint comments. annotate_xlsx.py copies a spreadsheet and adds comments to cells. Together, they turn review data into visible feedback.

### [Office DOCX document utilities](stage-17.2.md) `stage-17.2` — 4 files

This stage provides command-line tools for working with Microsoft Word DOCX files without opening Word. A DOCX file is really a zipped bundle of XML files, where XML is structured text that stores the document’s content and settings. These helpers support behind-the-scenes document editing and cleanup.

The unpack tool opens a .docx file into a normal folder, exposing its XML parts so other tools or people can inspect and edit them. It also cleans the main document XML to remove distracting formatting noise. The comment tool works on that unpacked folder and adds the extra XML records Word needs for a proper comment, then tells the user where to place the matching comment markers in the document text. The pack tool reverses the unpack step: it tidies XML spacing and zips the folder back into a usable .docx file. Finally, the accept changes tool makes a clean version of a document by accepting all tracked edits through LibreOffice in the background. Together, these scripts form a small workshop for taking DOCX files apart, adjusting them, and putting them back safely.

### [Office PPTX presentation utilities](stage-17.3.md) `stage-17.3` — 5 files

This stage is shared behind-the-scenes support for working with PowerPoint presentations. A `.pptx` file is really a zipped bundle of many smaller files, mostly XML, which is a text format that stores structured data. These utilities let the system open that bundle, edit it, put it back together, and fix common problems.

`unpack.py` is the front door for inspection and editing. It turns a `.pptx` into a normal folder and formats the XML so people and tools can read it more easily. After changes are made, `pack.py` reverses the process: it compresses the folder back into a usable `.pptx` and removes extra XML whitespace without changing the visible slide text. `repair.py` is a cleanup step for generated decks, fixing known issues that might make PowerPoint show repair warnings or lose important spaces. `slides.py` provides practical slide operations, such as removing unused files, adding a slide, or making thumbnail contact sheets. `__init__.py` simply makes these scripts importable by other project code.

### [Office XLSX spreadsheet utilities](stage-17.4.md) `stage-17.4` — 3 files

This stage is behind-the-scenes support for working with Excel .xlsx files. It is not part of the main application loop by itself. Instead, other tools can call it when a spreadsheet needs to be checked or updated without opening a visible office window.

The package marker, __init__.py, is like a label on a toolbox. It tells Python that the scripts folder can be imported by other code, but it does not do any work on its own.

The _soffice.py file holds shared helpers for starting LibreOffice in “headless” mode, meaning LibreOffice runs in the background with no desktop window. It also knows where LibreOffice keeps user macros on Linux and macOS, so scripts do not have to repeat that platform-specific knowledge.

The recalc.py script is the worker. It opens an .xlsx spreadsheet in LibreOffice, forces formulas to calculate again, saves the updated file, and then looks for any formula errors that remain. Together, these files provide a small, reusable way to refresh and validate spreadsheets automatically.

### [PDF form, layout, and rendering helpers](stage-17.5.md) `stage-17.5` — 3 files

This stage provides practical command-line helpers for working with PDFs. It is shared support rather than the main work loop: other tools or people can call these scripts when they need to inspect a PDF, fill it in, mark it up, or turn it into images.

The formfill tool works with true fillable PDFs, meaning files that contain built-in form fields such as text boxes or checkboxes. It can check whether those fields exist, export a JSON field map that names them, and create a filled PDF from JSON values. JSON is a simple text format for structured data.

The layout tool is for “fake” forms: PDFs that look like forms but have no real fields. It scans the page layout, helps preview where text should go, and writes text annotations onto the PDF, like placing labels on top of a printed page.

The render tool converts each PDF page into a PNG image. Together, these tools cover three common PDF paths: fill real forms, annotate visual forms, and make page images for visual review or later processing.

### [Skill discovery, package markers, and sample probes](stage-17.6.md) `stage-17.6` — 3 files

This stage is shared support around “skills,” which are packaged add-ons the system can discover, show, or test. It is not the main document conversion work. Instead, it provides small pieces that help the larger system recognize packages, browse available skills, and confirm that a sample skill can run.

The documents package marker, `__init__.py`, is like a label on a folder. It tells Python, the programming language, that `ufo_ext_documents` is an importable package. It adds no actions of its own, but without the label other code may not be able to find the package cleanly.

The sample skill probe is a simple test button. When run, it prints a fixed success message, letting the system check that the sample skill is installed and reachable.

The web community reader connects the web app to the public `skills.sh` directory. It can list skills, search them, and fetch a skill’s full `SKILL.md` description for the Community skills page.

## [Turn completion, cancellation, cleanup, and teardown](stage-18.md) `stage-18` — 5 files

This stage is the system’s end-of-turn safety net. It runs when a model reply is finishing, when a user presses stop, or when leftover work must be cleaned up after an interruption. Its job is to make sure results are saved, the right people see the right replies, and no hidden work keeps running.

The replies module looks through a model’s text for special hidden “reply-to” sections. These mark which member should receive part of the answer. It delivers those parts to the correct place and removes the hidden markers, even while text is still streaming in small pieces. The delivery sweep is a backup messenger. If a delegated child turn finished but its result was not reported to the parent conversation, it finds and delivers it later. The stop surface handles a member pressing “stop”: it checks the request, cancels the running turn, and sends a final live update. Shared cancellation code tells the active workflow to stop before marking it cancelled. Workspace change tracking scans the conversation’s files and saves a final snapshot of what changed.

## [Persistent schema, migrations, durable storage, and blob storage](stage-19.md) `stage-19` · (cross-cutting) — 199 files

This stage is the system’s long-term memory and storage foundation. It is used during startup and upgrades to prepare the database, and then quietly supports the main work loop whenever conversations, jobs, files, settings, or results must be saved.

The migration part is like a building inspector and renovation crew. It uses Alembic, a tool that runs ordered database change scripts, to create tables and update older installations without losing data. Core migrations maintain the main platform records, while extension migrations add storage for plug-in features such as memory, web, research, coding, reports, and scheduled tasks.

The durable records and binary objects part defines what gets saved and how. It describes shared records for agents, turns, transcripts, credentials, artifacts, and job state, then maps them into database tables. It also stores larger file-like data, called blobs, in local storage or cloud storage.

The `db.py` file is the safe doorway into all of this. It opens database connections, runs migrations, keeps each workspace’s data separate, wraps changes in transactions, and cleans up afterward.

### [Core and extension database migration commands](stage-19.1.md) `stage-19.1` · (cross-cutting) — 192 files

This stage is the database change control room. It runs during setup, upgrades, and sometimes rollback, before the main application can safely use the database. The tool involved is Alembic, a migration runner: it applies small ordered scripts that add, change, fill, or remove database tables and columns.

The core migration groups are the main assembly line. They start with the first platform tables for workspaces, members, agents, conversations, turns, sources, permissions, and costs. Later groups add billing, scheduling, queues, artifacts, transcripts, media usage, app visibility, listener claims, auditing, and cleanup of retired integrations. Timestamped and branch migrations keep older installations moving forward, including side histories such as Daily Brief, Sweep, and knowledge graph tables.

The extension migrations are plug-in lanes beside the core line. They create and update storage for memory, sites, skills, web chats, coding reviews, enrichment, indexing, reports, research, tests, and sample extensions.

The env.py file is the conductor. It connects Alembic to the project database and table definitions, then tells it which migration steps to run up or down.

#### [Core migrations 0001-0019: foundational schema and early surfaces](stage-19.1.1.md) `stage-19.1.1` — 16 files

This stage is part of setup and long-term maintenance of the database. It contains early Alembic migrations, which are step-by-step scripts that change the database layout as the application grows. Migration 0001 lays the foundation: workspaces, members, agents, conversations, turns, and cost records. Later scripts add the first “rooms and cupboards” around that core: encrypted credentials, proposals, extension-owned JSON storage, imported sources and pages, and grants for permissions.

Other migrations widen where work can happen. They add support for subagents, Slack, and the web, then relax older surface rules so new entry points fit more easily. Several scripts make running work safer and more controlled: spend caps, egress usage tracking, parent turns, parked turn states, duplicate-run guards, runtime heartbeat records, and scheduled tasks. Shared artifacts let files be attached to a conversation turn. Finally, the source backend rule is opened so extensions can provide new kinds of content sources. Together, these migrations turn a basic conversation database into the project’s first usable platform.

#### [Core migrations 0020-0039: ledger, turns, scheduling, inbound queues, and seats](stage-19.1.2.md) `stage-19.1.2` — 20 files

This stage is part of the system’s behind-the-scenes database evolution. Each file is a migration, meaning a small ordered change to the database structure, with a matching undo step where needed. Together they prepare the system for richer accounting, safer background work, scheduled activity, inbound message queues, and early workspace membership limits.

The ledger changes add price details, a new sandbox token spending type, workspace-level spending not tied to one turn, and export progress tracking. Source changes record repeated failures for retry backoff and allow a source to be marked removed without erasing its history. Conversation and turn changes remember sandbox handles, tracing links, extra context like sender and timezone, speaker details, connection authorization, scheduled admission, origin information, and the last turn used by a scheduled task.

Other migrations strengthen the machinery around this work. New indexes make job searches faster. Runtime rows can represent shared fleet processes, not just workspace-owned ones. Surface keys become safer across multiple workspaces. Inbound messages get their own queue table, briefly gain and then lose an old rendered-text field. Finally, seats let workspaces track seated members and optional seat limits.

#### [Core migrations 0040-0059: permissions, pages, agents, and source grants](stage-19.1.3.md) `stage-19.1.3` — 20 files

This stage is a set of database migrations, meaning ordered upgrade steps that reshape stored data as the product evolves. They run behind the scenes during deployment or startup, before normal work continues. Together they refine permissions, ownership, agents, pages, and scheduled work.

The first changes add new workspace and export settings, including BYOK export marking and included seat limits. Several steps make lookups and history safer: faster parent-turn searches, page browsing fields, renamed page timestamps, page revision numbers, and cleanup of old page alert data. Source and grant changes add sharing flags, source ownership, split old grants into clearer connections and connector grants, and finally record which agents may read each source.

Other migrations clarify who is acting. Turns and scheduled tasks can be credited to a member, scheduled tasks can expire, and task names become unique per agent. Agent-related steps add internet-access settings, bind conversations and surface installs to agents, choose each workspace’s main member and agent, and remove obsolete memory-surface tables. Conversation audiences are made explicit, with safeguards for old Slack data.

#### [Core migrations 0060-0079: admissions, artifacts, transcripts, media usage, and connections](stage-19.1.4.md) `stage-19.1.4` — 18 files

This stage is part of the system’s upgrade path. It changes the database, which is the system’s long-term memory, so newer application code has the fields and rules it expects. Several migrations widen what conversations can record: turns can be admitted from an intent, agents get a controlled reasoning setting, scheduled tasks can be paused, conversations can point to sandbox runs, show the surface label where they began, and store Git-reported workspace changes. Artifact sharing is strengthened by adding stable artifact IDs and complete preview records. Usage accounting becomes more detailed by splitting prompt and cache-read token counts and allowing image and video usage in the ledger. Access and safety are improved with an audit table for admin transcript reads, plus cleanup of an unused index. Operational fixes repoint old Bedrock model choices, remove retired YC extension data, track subagent results owed back to parent turns, speed member lookup by email, and move connection sharing onto the connection itself with an optional account label. Together, these migrations prepare stored data for newer product behavior.

#### [Core migrations 0080-0099: billing, balances, agent provisioning, and conversation metadata](stage-19.1.5.md) `stage-19.1.5` — 20 files

This stage is part of database upgrading, the behind-the-scenes work that changes stored data safely as the product grows. These migrations reshape how conversations, agents, billing, members, and network rules are recorded.

Several changes make conversations easier to find and display: spoken-turn indexes speed up searches, first-message text becomes a stored conversation title, titles get a “already summarized” marker, subagent turns keep their chosen display name, mid-turn replies are saved once for reliable delivery, and turns remember BYOK, or “bring your own key,” details for retries.

Agent records also get richer. They now store sandbox size, provisioning source, tool policy, setup data, expected inputs and outputs, and the owning member.

Billing and balances gain their own machinery. Ledger lookups become faster, workspaces get prepaid balance and purchase records, debits record how much money was actually removed, and auto-top-up settings can be saved.

Other migrations simplify membership, store member time zones, retire the old scheduled-pause design, fix file media types, and add automatic egress-rule version bumps so cached network-access rules stay fresh.

#### [Core migrations 0100-0113: integration cleanup, ledger usage, listener claims, icons, and refs](stage-19.1.6.md) `stage-19.1.6` — 14 files

This stage is part of upgrading the system’s database near the end of the simple numbered core migrations. A database migration is a small step that changes stored data or table shapes so newer code can run safely on older installations.

These files mostly tidy old integrations and add small pieces of information the newer product needs. They remove retired Exa records, old seat-shipping marks, and obsolete iMessage claim-code or project-binding entries. Several migrations improve agent records: they add workspace/private visibility, add and update agent icons, and fix saved Fable model settings and model IDs so agents point to valid services. Others expand money and usage tracking: the ledger gains detailed token-use totals and bring-your-own-key flags, while workspaces get a timestamp proving their first verified paid top-up. One migration adds listener claims, a table that lets one running server instance “claim” a named surface so two workers do not listen in the same place. Another adds created references to turns, so conversation steps can remember which reference records they produced. Together, these migrations clean house while preparing stored data for newer behavior.

#### [Core timestamped and branch migrations through 2026-08-24](stage-19.1.7.md) `stage-19.1.7` — 17 files

This stage is part of database upgrade work: the system is reshaping stored data while keeping old installations able to move forward. It starts by clearing stale iMessage phone-claim records, then speeds up common conversation “turn” searches with indexes, which are like labels that help the database find rows faster. It adds invitation details to members, archive flags to agents, and safer routing for shared message surfaces by using sender addresses such as phone numbers. It records when a turn’s connection request arrived, retires unusable QuickBooks sources, and adds an object change journal so edits can be audited later. Agents gain a setting for using shared workspace skills, built-in app agents get correct icons, and archived agents have their old names stored separately so names can be reused. The stage also tidies old branches of migration history: it closes the Daily Brief/Sweep side path, removes retired Daily Brief tables, and parks sources that repeatedly refuse work. Finally, it includes older branch migrations that created the first knowledge graph tables and the original Sweep/Daily Brief tracking tables.

#### [Core timestamped migrations from 2026-08-25 onward](stage-19.1.8.md) `stage-19.1.8` — 17 files

This stage is a set of later database migrations, which are versioned changes that update stored data and table shapes as the product evolves. It is behind-the-scenes maintenance, usually run during deployment or startup before the app resumes normal work.

Together, these migrations teach old workspaces the newer rules. They add descriptive purpose text to agents, move the coding agent to the newer app identity, archive the retired Tasks app, and make chat the main agent. They store more stable turn information, including billing identity, runtime settings, spawn request identity, and a safety rule about who is speaking. They tighten app visibility by making wiki private, and they rewrite saved tool allowlists so old Slack, iMessage, object, and surface action names match the current registry. They add source identities to pages, record fulfilled credential requests, and let shared artifacts remember both the request and content they came from. They also correct media types for text-like artifacts and remove obsolete GitHub connector records. The result is old data fitting the newer system without losing history.

#### [Memory extension migrations](stage-19.1.9.md) `stage-19.1.9` — 16 files

This stage is part of behind-the-scenes setup and upgrading. It is a chain of database migrations, meaning small ordered changes that reshape stored data as the memory extension grows. The first steps create the basic storage: a table for memory items, then a table for memory pages. Later changes add useful details to each item, such as its kind, confidence, workspace, and the time the information describes. Other steps make the system faster by adding indexes, like labels in a filing cabinet, so old facts ready for consolidation and workspace inventory pages can be found quickly.

The middle of the chain improves traceability. It links memories back to source pages, records the exact page revision, backfills old timestamps, and later allows one memory item to point to multiple source pages. Audience rules are widened so memories can belong to rooms as well as members or everyone. Final changes add safe retirement of memories, new item classes such as section and overview, and a separate shared profile table for one current member profile per workspace.

#### [Workspace app, site, skill, source trigger, and web extension migrations](stage-19.1.10.md) `stage-19.1.10` — 20 files

This stage is part of upgrade and startup housekeeping. It is a set of database migrations, which are small ordered changes that reshape stored data when the system moves to a newer version. Together they prepare the workspace features that users see around agents, sites, chats, skills, and timed work.

The monitor, objective, and scheduled-pause migrations create durable places to remember future work: checks to run later, goals with steps and progress evidence, and conversations that should resume at a set time. The sites migrations build up hosted-site records, adding generations, homepage agents, workspace visibility fixes, previews, share cards, deployment counters, and source manifests. The skill migrations first store user-created skills, then move their ownership from workspace to agent and back to workspace, adding routing information and cleaning duplicates along the way. The source migrations give conversation subscriptions their own structured trigger table and record how each trigger should be delivered. The web migrations copy old chat metadata into the right places, then move chat titles into the main conversation record so there is one clear source of truth.

#### [Specialized extension storage migrations](stage-19.1.11.md) `stage-19.1.11` — 13 files

This stage is behind-the-scenes setup work for optional extensions. Each file is a database migration, meaning a small instruction script that changes the stored data layout when an extension is installed or upgraded, and can usually undo that change if rolled back.

The coding migrations build the review feature’s storage step by step: first an inbox and review-run history, then a link to the conversation used for a run, then a newer agent-binding shape, and finally a move from old review tables into the shared trigger and conversation system. The enrichment migrations add member profile enrichment data, then consent and retry records so the system knows who allowed enrichment and when to try again. The eval environment migration creates fake email and calendar tables for testing. The indexing migrations create searchable text chunks and adjust them so chunk IDs are safe across workspaces. Report digest migrations store read report summaries and remember checks that found no changes. Research stores sources shown during conversations. The sample extension adds a simple per-workspace note table.

### [Durable records and binary objects](stage-19.2.md) `stage-19.2` · (cross-cutting) — 6 files

This stage is shared behind-the-scenes support for keeping important data safe and readable over time. It defines the system’s long-term records: conversations, agent settings, questions, credentials, job state, artifacts, and stored files.

The schema records file is the common language for live data such as agents, turns, runtime settings, and turn results, so the user interface, workers, billing, and runtime engine all mean the same thing. The schema tables file turns that language into database tables, with columns, links, defaults, and safety rules that work in both local SQLite and deployed PostgreSQL.

Conversation history gets special care. The turns transcript model defines the saved transcript format, including compacted versions used when long conversations are shortened. The runtime transcript file reads and writes those transcripts in shared storage, while guarding against an older or smaller copy replacing a newer one.

Binary objects, or “blobs,” such as artifacts and source files, go through one storage layer that can use local files or S3-style cloud storage. Durability support makes old saved workflow data still load correctly after code changes.

## [Public SDK, protocol types, and extension contracts](stage-20.md) `stage-20` · (cross-cutting) — 75 files

This stage is shared behind-the-scenes support for the whole system. It is not the startup, main work loop, or shutdown. It is the set of public “agreement papers” that let extensions, model code, browser code, and generated services fit together without guessing each other’s shapes.

The SDK re-export surface is the front counter for extension authors. It exposes approved imports for browsers, tools, credentials, subjects, jobs, manifests, search, sandboxes, and other platform features. The generated protocol definitions are the fixed message formats used when services talk across process or network boundaries, including the iMessage extension APIs. The package marker files make the main Python folders importable so these contracts can be found.

The direct files fill in key agreements. Model specs and model interfaces define what AI models can do, how requests and streamed replies look, and how billing and credentials work. The extension context gives running extensions a limited toolbox. The browser contract hides where Chrome comes from. Conversation slots define side panels beside chats. The iMessage provider defines common message-source and attachment shapes.

### [SDK re-export surface for extension authors](stage-20.1.md) `stage-20.1` · (cross-cutting) — 38 files

This stage is the public shelf of the SDK for extension authors. It is not the startup path, main work loop, or shutdown path. Instead, it is shared support that gives outside code stable import points, so extensions can use approved tools without reaching into private internal files.

Its parts cover the main areas an extension may need. The web, browser, terminal, and surface seams expose safe ways to connect to browsers, pages, terminals, operator sessions, and user-facing surfaces. The agent context, models, tools, and prompt-safety exports provide the shapes and helpers for conversations, model calls, tool actions, permissions, audiences, and unsafe outside text. The credentials, grants, subjects, and seats exports cover identity, access checks, stored credentials, and visibility rules. The package root and operational exports provide common accounting, balance, feature flag, and observability tools. The content, connector, search, and object exports support outside data, indexing, search, and stored objects. The manifest, jobs, hub, and sandbox exports let extensions describe themselves, schedule background work, and use controlled runtime features.

#### [SDK web, browser, terminal, and surface seams](stage-20.1.1.md) `stage-20.1.1` — 7 files

This stage is a set of public doorways for people building on the SDK. It is not the main work loop itself. Instead, it sits at the edges of the system, where extension code talks to browsers, web pages, terminals, operator sessions, and user-facing “surfaces” without depending on hidden internal modules.

The browser module exposes the safe browser connection pieces, including the Chrome DevTools Protocol, a browser control channel used to attach to and reuse browser sessions. The callback page module creates the small confirmation page shown after sign-in, install, or consent steps. The HTTP module gives extensions standard request, response, upload, form, and cookie helpers, so route code does not need to touch the lower-level web framework. The operator module re-exports web session tools meant only for operator use. The surface token module exposes stable helpers for creating and checking link-like surface addresses. The surfaces module gathers the main surface extension types and helpers. The terminal module does the same for terminal transport, errors, timing, and blob storage.

#### [SDK agent context, models, tools, and prompt-safety exports](stage-20.1.2.md) `stage-20.1.2` — 8 files

This stage is shared support for extension authors. It is not the main work loop itself. Instead, it provides stable “front doors” into parts of the system that outside code is allowed to use, so extensions do not have to depend on deeper internal files that may change.

The context module gathers the pieces an extension needs to understand the current run, such as the agent, conversation, pages, credentials, models, and saved state. The models module exposes the shapes of messages, model clients, tool calls, pricing details, and access grants. The tools and skills modules provide the approved building blocks for adding actions and higher-level abilities. The authority module exposes objects that describe what an extension is allowed to execute. The audience module gives shared names and helpers for who a message is meant for. The delivery register module exports standard prompt text used when speaking to the model. Finally, untrusted provides a common way to mark outside text as unsafe to trust directly. Together, these files form the public SDK shelf of labeled tools.

#### [SDK credentials, grants, subjects, and seat access exports](stage-20.1.3.md) `stage-20.1.3` — 6 files

This stage is shared behind-the-scenes support for extension developers. It does not run the main product flow itself. Instead, it provides safe public “front doors” into internal access and identity tools, so extensions do not need to depend on hidden module paths that may change.

The credentials module exposes approved helpers and error types for working with stored credentials. The authproxy module exposes the credential shapes and constants used by feed-sync connectors that get their login material through an authentication proxy. The bearer module exposes only the checking side of bearer tokens, which are login tokens carried with a request; extensions can verify a token made by the gateway, but cannot create one themselves. The grants module exports tools for inspecting connections and auditing grants, meaning records of who was allowed to access what. The subjects module exports standard visibility “subjects,” the labels used to decide which people can see a disclosed row. The seats module exports helpers for seat-related access, such as user entitlement or capacity checks. Together, these files form a stable SDK surface over the runtime access system.

#### [SDK package root and cross-cutting operational exports](stage-20.1.4.md) `stage-20.1.4` — 5 files

This stage is shared support for the public SDK, not part of the main work loop itself. It acts like the front desk of a large building: extension code can ask here for common tools without needing to know the internal room numbers where those tools are built.

The package marker file, __init__.py, simply tells Python that ufo.sdk is an importable package. It adds no behavior, but it makes the rest of the SDK doorway usable. accounting.py provides one public place to import accounting and spending value types, while leaving the real calculations to deeper billing code. balance.py does the same for balance checks and balance-related helpers used by extensions. flags.py exposes feature flags, which are switches used to turn behavior on or off, by re-exporting the internal flag helper and constants. o11y.py, short for observability, gives extensions safe access to logging, warnings, metrics, and turn timing so their activity can be seen and measured consistently.

#### [SDK content, connector, search, and object exports](stage-20.1.5.md) `stage-20.1.5` — 7 files

This stage is shared behind-the-scenes support for people building extensions on top of UFO. It does not do the main work itself. Instead, it provides stable “front doors” into deeper parts of the system, so extension code can import approved names without depending on internal files that may change.

The connector module exposes the public pieces needed to plug in connectors, which are adapters that let UFO talk to outside systems. The sources module does the same for content sources, such as a REST API that can feed data into UFO for syncing. The index module gathers the allowed indexing tools, which prepare content so it can be searched later. The search module exposes the public search interfaces and types, while the memory module exposes types used for memory-style search, where stored information can be found again by meaning or context. The objects module collects names for stored objects, their kinds, ownership rules, and views. The listings module provides helpers for listing results in pages, like showing one screen of items at a time. Together, these files form the SDK’s clean public surface.

#### [SDK extension manifest, jobs, hub, and sandbox exports](stage-20.1.6.md) `stage-20.1.6` — 5 files

This stage is shared support for extension authors. It does not run the system’s main work itself. Instead, it provides stable “front doors” into the SDK, so outside code can import approved names without depending on internal folders that may change.

The manifest module exposes the types and constants used to describe an extension’s metadata, such as what the extension is and what it needs. The jobs module exposes the small set of names extensions use to declare background jobs, meaning work the system can run outside the immediate request flow. The scheduled_fire module provides helpers for working with scheduled task runs, or “fires,” including how they are named and read. The hub module republishes public hub-related types, giving code a clean way to talk about the hub-facing parts of the SDK. The sandbox module exposes sandbox capabilities: types, constants, and helpers for features that run in a controlled environment.

Together, these files act like a reception desk for the SDK: they guide users to safe, public tools while hiding the deeper machinery.

### [Generated and wire protocol definitions](stage-20.2.md) `stage-20.2` · (cross-cutting) — 24 files

This stage is shared behind-the-scenes support. It defines the fixed “language” that separate parts of the system use to talk to each other, especially across process or network boundaries. Most of it is generated from Protocol Buffers, a format that describes messages in a strict, reusable way so both sides agree on the shape and names of the data.

The protocol scaffolding files provide the foundation. They mark Python packages so generated code can be imported, define Google-style HTTP annotations, and describe sandbox bridge messages for requests such as running a command or reading a file in an isolated work area.

The iMessage service API files define the remote-call contracts. Their message files describe requests and replies, while their gRPC bindings provide client and server wiring for attachments, chats, events, and messages.

The iMessage domain type files define the actual iMessage nouns: addresses, attachments, chats, groups, messages, polls, and streaming heartbeats. Together, these pieces act like a shared dictionary and plug shape for the rest of the system.

#### [Protocol scaffolding and shared support schemas](stage-20.2.1.md) `stage-20.2.1` — 9 files

This stage is shared behind-the-scenes support. It does not send iMessages itself. Instead, it provides the basic wiring that other parts of the system rely on.

The sandbox protocol file defines a small command language for talking to a safe, isolated work area called a sandbox. Through it, the rest of the code can ask for simple actions such as “run this shell command” or “read this file” without caring which sandbox system is used underneath. It is like a common remote control for different safe workrooms.

The many __init__.py files are package markers. They tell Python that folders such as proto, google.api, photon, and photon.imessage.v1 can be imported as code. Most of them do no work at runtime, but they keep the generated protocol files easy to find.

The generated google.api files describe Google-style HTTP annotations for Protocol Buffers, a structured message format. They let other generated code understand how service calls correspond to HTTP methods and paths.

#### [iMessage generated service APIs and gRPC bindings](stage-20.2.2.md) `stage-20.2.2` — 8 files

This stage is shared behind-the-scenes support for the iMessage extension. It is not the main business logic itself. Instead, it provides the “contract” that different parts of the system use when they talk over the network. The files are generated from Protocol Buffers, a format that defines data shapes clearly so both sides agree on what a request or response looks like. gRPC is the calling system that uses those shapes to make remote method calls, like calling a local function that actually runs on another process.

Each service has a pair of files. The pb2 file defines the vocabulary: attachment, chat, event, or message requests and replies, plus the service description. The matching pb2_grpc file provides the wiring: client stubs for making calls and server hooks for implementing them. Together, the attachment files cover upload and download work, chat files cover conversations, event files cover catching up on missed activity, and message files cover sending, editing, fetching, reacting to, and subscribing to message changes.

#### [iMessage generated domain message types](stage-20.2.3.md) `stage-20.2.3` — 7 files

This stage is shared behind-the-scenes support for the iMessage extension. It does not start the system or run the main work by itself. Instead, it provides the standard data shapes that other code uses when it talks about iMessage records. These files are generated from Protocol Buffers, a format for turning structured data into compact messages that programs can store or send.

Each file covers one part of the iMessage world. The address types describe people or contact addresses. Attachment types describe shared files, images, and related details. Chat types describe conversations and chat events. Group types record group-chat changes, such as members joining, leaving, or names changing. Message types are the largest vocabulary, covering texts, reactions, stickers, edits, read receipts, deleted messages, and attachments. Poll types describe poll options, votes, and changes. The streaming file defines a small heartbeat message, like a regular “still here” signal, to keep a live connection open. Together, these files act like labeled containers so the rest of the system can exchange iMessage data consistently.

### [stage-20.3](stage-20.3.md) `stage-20.3` · (cross-cutting) — 6 files

This stage is quiet behind-the-scenes support. It is not part of startup, the main work loop, or shutdown. Instead, it makes sure Python can find the project’s main code areas when other files ask to use them. In Python, a package is a folder that can be imported, meaning other code can refer to modules inside it by name. These __init__.py files act like labels on storage boxes: they tell Python “this folder is part of the importable code structure.”

The top-level ufo marker opens the main package. The harness marker makes the testing or execution harness area importable. The host.ext marker exposes extension-related host code. The runtime marker opens the area used for execution-time logic, and runtime.turns marks the sub-area for turn-based runtime pieces. The schema marker makes schema-related definitions available. None of these files run meaningful code or change behavior. They simply connect the folder layout to Python’s import system so the real implementation files can be found cleanly.

## [Security, authorization, credentials, access policy, and billing](stage-21.md) `stage-21` · (cross-cutting) — 23 files

This stage is shared safety support that runs around the main work of the system. Before an agent acts, it helps answer four practical questions: who is allowed to do this, which secrets may be used, what outside services may be contacted, and whether the workspace can pay for the work.

Identity and authorization checks are the “door locks.” They track whether work is being done for a person, a workspace, or an agent, then decide who may chat with agents, view transcripts, or read private scheduled tasks. Secrets, network egress, and connector access are the “customs checkpoint.” They let sandboxed code reach approved outside hosts, attach hidden credentials only when allowed, and keep API keys from leaking into chat or agent code. Usage accounting and prepaid billing are the “meter and wallet.” They price model calls and other resources, enforce limits, deduct prepaid credit, and connect to billing services.

The untrusted text wrapper marks outside content as evidence, not instructions. The workspace helper keeps identity, credentials, model accounts, and charges tied to the correct workspace.

### [Identity and authorization checks](stage-21.1.md) `stage-21.1` · (cross-cutting) — 8 files

This stage is shared behind-the-scenes support for answering a basic question before work happens: “Who is allowed to do or see this?” It protects the system from mixing up people, workspaces, agents, and private content.

The access package marker simply lets this group of code be imported. The authority model defines whether a job is acting as a real workspace member or only as the workspace itself, without anyone’s private permissions. The agent scope tracker records which agent is currently acting, so agent code can safely ask who it represents.

Several parts then apply these identities to real decisions. Seats are the on/off access switch that decides which members may talk to an agent in a workspace. Web audience rules decide which members can see or chat with agents, and reserve sensitive actions, like opening someone else’s transcript, for admins with an audit trail. Turn audience and subject labels give conversations strict names for “everyone,” one member, private, shared, or room-based access. Scheduled task visibility uses these same ideas to decide who may read private task details.

### [Secrets, network egress, and connector access](stage-21.2.md) `stage-21.2` · (cross-cutting) — 8 files

This stage is shared safety plumbing for anything that must leave the sandbox and talk to the outside world. A sandbox is the locked-down place where an agent’s code runs. Before it can call an API, download from a host, or use a private connector, this stage decides what is allowed and how secrets are protected.

The egress resolver reads a signed, short-lived token and turns it into fresh network rules for one run. The egress rules builder combines model choices, extensions, connector grants, artifact storage, and saved credentials into exact proxy instructions: which hosts may be reached and when a hidden credential should be inserted into a request. The credentials code stores API keys safely and supports sealed requests so users can provide secrets without exposing them in chat or the sandbox.

The private egress control API is the gate the Rust proxy asks before letting traffic out, fetching secrets, or recording usage. Grant code keeps connected OpenAI or Anthropic accounts refreshed. Pipedream proxy and token support let external accounts be used without exposing their tokens. The direct source connector path does the same for member-supplied API keys.

### [Usage accounting and prepaid billing](stage-21.3.md) `stage-21.3` · (cross-cutting) — 5 files

This stage is the money meter for the system’s main work loop. As workspaces use paid resources, it tracks what was used, checks whether spending is allowed, and records charges against prepaid credit.

The pricing file defines how model use becomes a cost, such as turning token counts into dollars. It also stamps each price table version, so later reports can show which prices were applied. The balance file is like a wallet manager. It tracks credit added, credit spent, required reserve money, and automatic refills, and it decides whether paid work may continue. The accounting file is the trusted ledger. It records model calls, sandbox network use, images, videos, and other charges, applies spend limits, deducts prepaid balance when needed, and builds usage summaries.

The Metronome extension connects this internal ledger to outside billing tools. It exports usage to Metronome, connects prepaid payments through Stripe, and provides admin-facing billing controls and status pages. The package marker file simply makes the billing code importable by the rest of the system.

## [Development, evaluation, debugging, and conformance infrastructure](stage-22.md) `stage-22` · (cross-cutting) — 7 files

This stage is shared support for building, testing, and operating the system safely. It is not the main user-facing work loop. Instead, it is the test bench, dashboard, and emergency phone for the rest of the code.

The observability toolbox sets up tracing, metrics, health checks, and structured logs. In plain terms, it helps operators see what the system did and whether it is healthy, while filtering sensitive content so private data does not leak into diagnostics. The runtime step reader turns raw saved workflow history into a clear timeline for one conversation turn, so engineers can inspect model calls, tool calls, and other steps in order.

The debugger package exposes a report_problem tool. When an agent hits a serious workspace issue it cannot fix, this records a warning with enough context to find the exact turn.

The evaluation environment provides fake but predictable workplace services, such as mail, calendar, Drive, GitHub, and business tools. The sample extension goes wider: it exercises the full extension boundary with fake tools, jobs, routes, stores, search, models, browser, connectors, and surfaces, proving extensions can plug in correctly without live services.
