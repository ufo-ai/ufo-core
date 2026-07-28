# System Handbook — Stage Index

Each stage below links to its full page; the paragraph is the stage's role in the system.

## [Deployment preparation, sandbox image validation, and schema upgrade](stage-1.md) `stage-1` — 57 files

This stage happens before the service starts its normal work. It is the system’s pre-flight check and upgrade step. First, the deployment bundle and sandbox gates prepare what will be shipped, build the sandbox “workroom” image, and prove that workspace storage and HTTPS proxy traffic work in a real sandbox.

Next, the database migration entry points run Alembic, the tool that applies database changes in order. The baseline migrations create the first core records, such as workspaces, people, agents, conversations, turns, and charges. Other core migrations add the storage the running system will later rely on: credentials and grants for permissions, conversation surfaces like Slack and web chat, inbound-message queues, turn links, context, attribution, sources and pages, extension storage, ledgers, spending caps, exports, seats, runtime workers, and scheduled tasks.

Finally, installed extensions bring their own migrations. These add extension-specific tables, such as evaluation fixtures, sample notes, or user-created skills. Together, these parts make sure the shipped image is valid and the database is ready before live traffic begins.

### [Deployment bundle and sandbox validation gates](stage-1.1.md) `stage-1.1` — 4 files

This stage is part of deployment preparation. It freezes what will be shipped, then checks that the outside services the system depends on really work before the release is accepted. Think of it like packing a travel kit and testing the roads before starting the trip.

`core/src/ufo/bundle.py` builds the deploy bundle for UFO. It creates a Docker build folder, copies in the chosen configuration, writes a fresh lockfile, and adds a Dockerfile. The lockfile is a record of exact extension choices, so the same bundle should start the same way on another machine.

`sandbox/build_template.py` builds the sandbox image, which is the prepared workroom where UFO can run tools such as scripts, browser automation, PDF utilities, and office converters. It keeps the hosted E2B sandbox and the local Docker image based on the same recipe.

The two gate files are safety tests. `sandbox/mount_gate.py` starts a real sandbox and checks that `/workspace` storage can be mounted and used. `sandbox/proxy_gate.py` checks that HTTPS traffic can pass through the expected proxy route.

### [Database migration entrypoints and baseline core schema](stage-1.2.md) `stage-1.2` — 4 files

This stage is the database “ground floor” for the system. It is mostly used during startup and deployment, before the main application work begins. Its job is to make sure the PostgreSQL database has the right tables in place, like checking that a workshop has the needed shelves and ledgers before people start using it.

The Alembic runner in `env.py` is the migration engine. Alembic is the tool that applies database change scripts in order. This file connects to the configured database, compares it with the known table definitions, and runs any missing steps.

The first migration, `0001_heartbeat.py`, creates the core tables for the application’s early records: workspaces, people, agents, conversations, conversation turns, and usage charges. Later, `0003_proposal.py` adds a proposals table and also defines how to undo that change if needed.

The control gateway has its own readiness check in `schema.py`. It does not build tables during live traffic. Instead, it verifies that its required ledgers already exist before the gateway starts serving requests.

### [Core credentials, grants, and agent access migrations](stage-1.3.md) `stage-1.3` — 4 files

This stage is behind-the-scenes database setup for security and access control. It is not part of the everyday work loop an agent runs through. Instead, it changes the shape of the database as the system grows, like adding new labeled drawers to a filing cabinet.

The first migration, 0002_credentials.py, creates a place to store workspace credentials, meaning saved login or access details the system may need later. The 0014_grant.py migration adds a grant table. A grant is a recorded permission: it says that a workspace allowed an agent to use a provider account, and it remembers which member and conversation created that permission. The 0043_grant_shared.py migration extends those grants with a shared flag, so the system can tell whether a grant is meant for broader use or not. Finally, 0050_agent_internet_access.py adds a setting on each agent that says whether it may use the internet. Together, these migrations build the database foundations for stored access, permission tracking, sharing rules, and agent network limits.

### [Core conversation surfaces and inbound-message migrations](stage-1.4.md) `stage-1.4` — 9 files

This stage is behind-the-scenes setup for conversations that arrive through different “surfaces,” meaning places like Slack or a web chat where a user can talk to the system. These files are database migrations: ordered changes to the stored data layout, usually run during deployment or upgrade before the main application uses the new features.

The early migrations add Slack support, then web support, so conversations and identities can be saved with the right source. Another migration loosens old surface-name limits and adds storage for shared files or artifacts attached to a conversation turn. Later changes make external surface identities workspace-specific, so the same integration name can be safely used in different workspaces, and add a helper for finding pending writeback work faster.

Other migrations enrich the conversation record itself. They let each turn record who is speaking and whether authorization is complete. They add, adjust, and then simplify an inbound-message buffer, which is like a waiting tray for messages before the conversation engine consumes them. Finally, agent binding links every surface installation and conversation to the agent responsible for handling it.

### [Core turn linkage, context, and attribution migrations](stage-1.5.md) `stage-1.5` — 7 files

This stage is behind-the-scenes database preparation for richer conversation history. A “migration” is a small upgrade to the stored data layout, like adding labeled drawers to a filing cabinet so later code can find and connect records correctly.

The first change lets turns be nested: one turn can point to a parent turn, store which subagent profile handled it, and mark conversations that came from a subagent surface. Another change adds run-guard fields, which help the system track the current attempt and avoid scheduling the same resume work twice. Conversations also gain a sandbox handle, so a per-conversation working area can be reused later.

Several migrations improve linkage and context. Turns can store a trace parent, tying subagent work back to the turn that launched it. They can also store rendered context, such as sender or timezone details provided by the surface. An index makes parent-to-child turn lookups faster. Finally, attribution links record which member a turn acts on behalf of, and which member created a scheduled task.

### [Core source, page, and extension-storage migrations](stage-1.6.md) `stage-1.6` — 8 files

This stage is behind-the-scenes setup for the database. It is made of migrations, which are small upgrade steps that change what the database can store as the product grows. These steps usually run during install or upgrade, before the main system work depends on the new fields and tables.

The first migration creates an extension storage table, so add-ons can keep small JSON settings or state per workspace. The next adds the basic source and page records, letting the system remember where content came from, when to sync it again, and which pages belong to which workspace. Later steps make sources more flexible and track their condition: extensions can define new source types, repeated failures can be counted for retry backoff, removed sources can be marked with a removal time, and ownership can say whether a source is shared or tied to one member. The final page changes add browsing details such as stream, title, and original timestamps, then rename timestamp fields to describe page records more clearly.

### [Core ledger, spending, export, and seat migrations](stage-1.7.md) `stage-1.7` — 9 files

This stage is behind-the-scenes database setup. It changes the shapes of the tables that the rest of the system depends on, so later code can record spending, usage, exports, and workspace seats safely.

The spending-cap migration adds a place to store rules like “stop work after this limit” and allows work to enter a parked, or paused, state. Several ledger migrations then widen what the ledger can record. The ledger is the system’s accounting book. It can now track not only token use, but also egress, meaning data leaving the system, and sandbox token use. It also gains a price digest field, which is a small audit note explaining what price data was used. Another change lets ledger rows belong directly to a workspace, not only to a single turn.

Export migrations add a logbook for ledger exports, so the system knows which ranges were sent out and acknowledged. They also mark whether an export used BYOK, or “bring your own key.” Finally, seat migrations let workspaces track seated members, seat limits, and included seats.

### [Core runtime fleet and scheduled-work migrations](stage-1.8.md) `stage-1.8` — 9 files

This stage is behind-the-scenes database upkeep. It changes the stored records that the running system depends on, so newer runtime and scheduling features have a safe place to live. The early runtime migration creates a table for active runtime instances, like a sign-in sheet for worker processes. Later changes loosen that table so workers can belong to a shared fleet instead of one workspace, then remove old columns from the older dedicated-worker model.

The scheduled-work migrations build the memory for tasks that should run later or repeat. They add the scheduled task table, make scheduled pauses easier to represent, allow turns to be marked as coming from a schedule, record the last turn a scheduled task created, and add an optional expiration time so stale tasks can be ignored.

One migration adds lookup indexes, which are like a book’s index: they help background jobs find candidate records without reading every row. Together, these changes let the system track shared workers and scheduled work efficiently as it grows.

### [Installed extension schema migrations](stage-1.9.md) `stage-1.9` — 3 files

This stage is behind-the-scenes setup for extensions that add their own database needs. A database migration is a small, ordered change to the database structure, like adding or removing a drawer in a filing cabinet. These migrations are shipped with installed extensions, so when an extension is enabled, the main system can prepare the right storage for it.

The evaluation environment migration creates tables for test fixtures: a fake email inbox and a calendar. These let the system run evaluations without using real user email or calendar data. The sample extension migration adds a simple table that stores one text note for each workspace, showing how an extension can keep its own data. The skill creation migration adds a table for skills made by users, so those custom skills can be saved and reused.

Each file also includes a rollback path. That means the same migration can undo its change by removing the table it created, keeping extension setup and cleanup predictable.

## [Hosted control-plane onboarding and workspace provisioning](stage-2.md) `stage-2` — 13 files

This stage is the front door for hosted UFO onboarding. It is used when the control service starts, when a new person signs up, and when operators run maintenance commands. The main command file starts the public web gateway, prepares the database, creates invite emails, retries Slack Connect setup, and installs database rules. The package file simply makes these pieces importable.

The gateway is the small public server that guides new users. It serves the terminal client and browser sign-in page, sends simple instructions back to the client, and decides whether a new curl | sh install should be suggested. It checks work email addresses, sends short-lived verification codes, stores only safe hashed proof in Postgres, and confirms the code later. Invite code logic does the same for one-time workspace creation invites.

Once a user is verified, shared-domain logic maps their company email domain to an existing workspace or safely creates one. The onboarding setup creates the workspace basics and lets extensions prepare themselves. Token code signs the user in for 30 days. Slack Connect jobs then create customer Slack channels, with retries and operator visibility if something fails.

## [Main server startup, configuration, pack loading, and extension assembly](stage-3.md) `stage-3` — 50 files

This stage is the system’s startup workshop. It begins when a person runs ufoctl in cli.py or starts the server through serve.py. The command-line tool is the control panel for setup, inspection, packaging, and talking to a workspace. The server entry point then builds the running service: it reads settings, connects storage, loads add-ons, sets up web routes, starts background workers, and applies workspace safety rules. config.py supplies the rulebook by loading one TOML settings file and stopping early if required settings are missing or wrong.

The rest of the stage decides what abilities the service will have. Pack discovery chooses a pack, which is a recipe for a particular assistant setup. Extension discovery finds and pins add-ons so the same chosen features can be loaded again later. Skill loading prepares built-in and user-created skill folders for safe use. The many extension manifests act like plug-in instruction cards. They register web pages, Slack, live hubs, browser and coding helpers, document and research skills, connectors, content sources, scheduled tasks, credentials, and jobs. Together they turn a plain server process into a configured UFO assistant.

### [Pack and extension discovery](stage-3.1.md) `stage-3.1` — 21 files

This stage is part of startup and setup. Before the assistant can do useful work, the system must know which “pack” to load. A pack is a named recipe: it lists the extensions, skills, and setup steps that should be turned on together. The assistant, hosted assistant, billing, evaluation, chief-of-staff, YC, GDPVal, DSQA, and sample pack files are these recipes. Some build a normal local assistant, some use cloud-style services, and others create controlled test setups that avoid real outside accounts and secrets.

The extension package files are the door labels for Python. Files like the REPL, research, scheduled tasks, Slack, web, sources, sites, YC, and UFO extension initializers make their folders importable so the rest of the system can find their code. A few also describe their extension’s role, such as creating skills or reviewing past activity for approved prompt improvements.

Together, these files act like a plug-in shelf and a set of shopping lists. The shelf makes tools discoverable; the lists choose which tools are used for each assistant setup.

### [Skill and user-authored skill loading](stage-3.2.md) `stage-3.2` — 5 files

This stage is behind-the-scenes setup for reusable “skills,” which are small folders of instructions and helper files the agent can use later during a work turn. The empty __init__.py file simply tells Python that the built-in skills folder is importable, like putting a label on a drawer. The runtime.py file does the main loading work: it defines what counts as a skill, reads skill folders, checks their names and contents, sorts them when one skill depends on another, and prepares their instructions and files so the agent can copy them into its safe working area. The sample probe.py is a tiny test tool for a sample skill; it prints a known success message so people or automated checks can confirm the skill can run. The skill_create extension adds workspace-authored skills. Its manifest.py connects save, view, update, delete, and load operations to the larger system. Its store.py is the gatekeeper: it keeps these user skills private to one workspace, validates them, uses safe names, and prevents them from overwriting built-in skills.

### [Core extension discovery and pinning](stage-3.3.md) `stage-3.3` — 2 files

This stage is shared behind-the-scenes support for UFO’s extension system. Extensions are add-on packages that teach UFO new commands, object types, tools, hooks, skills, credential rules, or storage backends. Before the CLI or server can use those additions, UFO must know which extensions are installed, which ones are allowed, and what they provide.

The loader is the doorway. It searches the installed Python packages for UFO extension declarations, reads what each one offers, and activates those declarations so the rest of the system can use them. In practice, it turns “this extension provides a tool” into an actual tool UFO can call.

The store manages the extension catalog and lockfile for the `ufoctl ext` command. The catalog is like a shelf of available add-ons. The lockfile is the saved shopping list of the exact extensions UFO should load later, including a digest, or fingerprint, to prove which installed package was pinned. Together, the store chooses and records extensions, while the loader brings the recorded extensions to life.

### [Web, shell, communication, and live-surface extension manifests](stage-3.4.md) `stage-3.4` — 5 files

This stage is part of startup and shared setup. It is where add-on features introduce themselves to the UFO service before users can reach them. Each manifest is like an ID card plus a set of mounting instructions. It says what the extension is called, which version it is, and what doors it wants the main system to open.

The web manifest registers the general web routes, so browser-facing pages can be attached. The UFO manifest registers the UFO shell surface, the route used by the shell client to talk to the service. The debugger manifest adds debugging web routes and tells the host how to connect them to the right workspace. The Slack manifest registers Slack routes, declares the private workspace credentials it needs, and points to its setup tools and skills. The Redis hub manifest registers a live-frame hub backend named “redis” and checks that a Redis address is present before it is built. Together, these files let the core load optional user-facing and communication features safely and predictably.

### [Agent, skill, document, research, and creation extension manifests](stage-3.5.md) `stage-3.5` — 6 files

This stage is behind-the-scenes setup. It is made of “manifest” files, which are like menu cards that tell the main UFO system what extra abilities are available before work begins. Each manifest names the tools, helper agents, prompts, and skill folders that should be loaded when an extension is turned on.

The browser manifest adds a browser helper agent and the instructions for sending web-browsing tasks to it. The coding manifest adds a coding helper, programming skills, GitHub access, routes, and needed credentials. The documents manifest lists skills for working with Word, PowerPoint, PDFs, spreadsheets, themes, and reviews. The research manifest adds research tools, research-focused helper agents, prompts, and skills. The sites manifest declares website-building tools, prompts, skills, and its site-building helper profile. The brief-pipeline manifest ties several helper agent stages together and adds a skill that teaches a parent agent to run them in sequence. Together, these files let the system discover and assemble specialist capabilities without hard-coding them into the core.

### [Connector, source, automation, and scheduled-job extension manifests](stage-3.6.md) `stage-3.6` — 7 files

This stage is part of startup and extension discovery. Each manifest is like a label on a plug-in box: it tells the host system what the extension can do, what permissions it needs, and what background work or web routes should be wired in.

The Composio and Pipedream manifests register external app connectors and the OAuth sign-in routes used to connect a user’s accounts. The connectors manifest defines shared connector tools, the connector object type, and prompt text that teaches the assistant how to talk about outside tools. The sources manifest adds connector-based content sources, the stored objects they create, the credentials they require, and a hook that reacts when synced page content changes. Scheduled tasks declare a tool, object type, scheduling skill, and recurring job so work can run later. Self-improvement registers a scheduled evaluation job. The YC manifest adds authenticated YC and Bookface reading through tools, sources, onboarding, credentials, and skills.

## [User-facing surfaces receive and normalize inbound requests](stage-4.md) `stage-4` — 5 files

This stage is the system’s set of front desks after startup. It is where messages first arrive from Slack, the browser chat, the terminal client, operator tools, or debugger routes. Each front desk checks that the request is allowed, figures out who the outside user is, connects that user to the right workspace and conversation, and reshapes the message into the common form the core conversation engine understands.

The Slack, web, terminal, and operator routes are the visible doors. Slack verifies Slack requests and handles messages, installs, button clicks, replies, and files. The terminal route accepts command-line messages and streams simple updates back. The web route lets signed-in users send chat messages, receive live answers, and view recent spending.

The small surfaces package file only makes this folder importable. The important shared piece is the surface bridge in core/src/ufo/ext/surface.py. It gives these trusted doors controlled access to core abilities: finding people, opening conversations, admitting messages, streaming responses, and sending final results back out.

### [Slack, web, terminal, and operator routes](stage-4.1.md) `stage-4.1` — 3 files

This stage is the set of front doors where people reach the system from different places before their requests become normal conversation work. It sits at the edge of the main work loop: outside messages come in, are checked and reshaped, then passed inward to the shared conversation engine.

The Slack surface is the Slack doorway. It confirms that a request really came from Slack, accepts messages, installs, and button clicks, turns them into agent “turns” meaning one step in a conversation, and sends replies or generated files back to Slack.

The UFO terminal surface serves the command-line client. When a member posts a message from their shell, it starts a conversation turn and streams back small text instructions that the terminal can display.

The web surface does the same job for the browser chat. It lets a signed-in member send a message, watch the answer arrive live, and see recent spending. Together, these files act like adapters: each speaks its own outside language, then hands clean, standard conversation requests to the core system.

## [Authentication, account connection, credential grants, and workspace authorization](stage-5.md) `stage-5` — 8 files

This stage is the identity gate for parts of the system that need to know who the user is or need permission to use an outside account. It sits behind normal requests and setup flows, checking sessions, guiding account connection, and storing proof of permission without exposing secret access tokens.

Several parts share the job. Bearer tokens are signed “passes” that say which email and workspace a request belongs to. Operator rules protect internal web tools by checking the user’s company email domain and deciding which workspace they may inspect. Grants power the chat-based /connect flow: a user approves an outside account, and the system records that permission for later tool use.

OAuth callback handling finishes the round trip after an outside service redirects the browser back. Pipedream and Composio act as bridges to hosted consent pages that keep the real tokens. The GitHub App connector creates and verifies installation links for workspaces. Slack tools turn Slack setup and channel lookup into guided chat actions. Together, these pieces let users safely connect accounts and let agents use them only where authorized.

## [Conversation admission, policy checks, and durable turn enqueueing](stage-6.md) `stage-6` — 2 files

This stage is the gatehouse for a new conversation turn. After a message has been cleaned into a standard shape, it comes here before the system spends effort answering it. The goal is to accept only work that is allowed, affordable, not a repeat, and ready to process safely in order.

The main front door is `admission.py`. It runs the incoming message through a fixed checklist: is the workspace allowed to use the agent, is the spending limit okay, has this delivery already been seen, is the conversation paused, and can the turn be queued? If the answer is yes, it writes a durable turn record, meaning a saved record that survives restarts, and places the turn onto the background work queue.

`seats.py` supplies one important part of that checklist. It decides which workspace members the agent may answer, based on seat limits, automatic seat assignment, owner rules, and refusals when no seat is available. Together, these files prevent unwanted or duplicate work from entering the main processing loop.

## [Turn dispatch, recovery, cancellation, and process presence](stage-7.md) `stage-7` — 5 files

This stage is the system’s traffic controller for conversation work. It sits in the main work loop and also runs behind-the-scenes safety checks. A “turn” is one unit of agent work in a conversation. TurnEngine.run starts that work, while the durable queue decides who may run it and records how it ends.

The queue is the central conveyor belt. Workers claim waiting turns, process each conversation in order, and write a final success or failure result. If a worker crashes, the queue can make the abandoned turn available again instead of leaving it stuck.

runtime_instance is like a roll call for live serve processes. Each process keeps its presence visible, while background checks recover lost work and clean up cancellations, including child turns left behind by cancelled parent turns.

cancellation.py provides the safe stop procedure: tell the running workflow to stop first, then mark the database record cancelled. candidates.py is the guarded doorway for finding which workspaces have queued work, returning only workspace IDs. __init__.py simply makes the loop code importable.

## [Per-turn setup: context window, prompts, skills, sandbox lease, and tool catalog](stage-8.md) `stage-8` — 5 files

This stage runs near the start of every claimed turn, before the model is asked what to do. It gathers the material the model needs, trims it to fit, and prepares the safe working area and tool list.

The conversation can be longer than the model’s memory window, so compaction.py acts like an editor: it keeps the newest messages as they are and replaces older parts with a structured summary. render.py then builds the final system prompt, which is the instruction sheet sent to the model. It combines the base rules, enabled skills, capability notes, citation rules, and the model’s knowledge cutoff, and adds a fingerprint so the exact prompt can be identified later. The prompts package file simply makes that prompt code importable.

registry.py is the tool catalog. It describes each tool, checks that tool names do not collide, and provides the input shape the model must use. fs_mount.py prepares the sandbox workspace by creating commands that connect `/workspace` to S3-backed storage, place credentials, and check that the mount is healthy. Together, these parts give each turn its context, instructions, tools, and safe workspace.

## [Model selection, request construction, streaming, and accounting](stage-9.md) `stage-9` — 12 files

This stage is the turn engine’s connection to AI models during the main work loop. It decides which model to use, builds a neutral request that can include chat messages, tools, images, and metadata, sends it to the right provider, then converts the provider’s streamed answer into UFO’s standard event format.

The engine is the traffic controller. It claims a turn, gathers context, calls the model, runs any requested tools, records cost, and saves the final transcript in order. The model interface and spec files define the shared shapes for requests, streams, tool calls, images, errors, and model facts. The catalog, Bedrock plug-in, and OpenRouter plug-in list available models, prices, limits, credentials, and connection details. The registry combines these into one live lookup table.

Provider bridges for OpenAI-style APIs and Anthropic translate UFO’s request into each provider’s format and translate streamed replies back. Pricing turns token usage into recorded cost with a fingerprint of the price table. The catalog skill exposes the live model list to users. The package file simply makes these modules importable.

## [Tool dispatch and sandboxed workspace actions](stage-10.md) `stage-10` — 61 files

This stage is the action arm of the system during the main work loop. When the model asks to do something, these pieces decide which tool to use, what it is allowed to touch, and where it may run. The tool package marker just makes the tool code importable. The context file is the permission envelope: it controls access to files, browsers, credentials, connectors, subagents, cleanup tasks, and returned results. The built-in tools provide everyday actions such as running commands, editing workspace files, sharing files, asking the user, loading skills, and connecting accounts.

The sandbox files provide the safe workspace. One defines the doorway into that workspace, one supports a simple local version, and Docker or E2B extensions can run the same work inside containers or cloud sandboxes. The coding package marker only enables imports.

Extra extensions add specialized tools: REPL runs remembered Python or JavaScript snippets, research searches and fetches pages, sites builds and serves small web apps, and todos keeps a conversation checklist. The document-skill scripts handle office files and PDFs. The browser automation pieces control Chrome for web tasks, clicks, downloads, and page reading.

### [Document, office, PDF, spreadsheet, and review skill scripts](stage-10.1.md) `stage-10.1` — 23 files

This stage is a toolbox for working with common office files inside the workspace. It is not the main agent loop; it is shared support the agent or a user can call when a document needs to be inspected, changed, checked, or exported.

The package marker files simply make these folders importable by Python. The document-review tools keep a review organized: constants name the state and log files, models define what an “issue” contains, manage_state records review progress in JSON and a log, and the PDF, PowerPoint, and Excel annotators turn saved findings into visible highlights, comments, or cell notes.

The Word tools unpack a DOCX into editable XML, add comments to the unpacked package, accept tracked changes through LibreOffice, and pack the folder back into a DOCX. The PowerPoint tools similarly unpack and repack PPTX files, repair known presentation-format problems, and help add or preview slides. The spreadsheet tools run LibreOffice invisibly and force Excel formulas to recalculate. The PDF tools detect and fill real form fields, analyze page layout for non-fillable forms, and render pages as PNG images for easier viewing.

### [Browser automation during tool execution](stage-10.2.md) `stage-10.2` — 25 files

This stage is the system’s browser driver during the main work loop. When the agent needs to use the web, these pieces connect to Chrome, control pages, read what changed, and clean up afterward.

The entrypoint and provider parts decide where Chrome comes from. They either connect to a hosted browser or start and reuse a sandboxed Chrome, then expose the control address. The transport and lifecycle parts keep that control link alive using Chrome DevTools Protocol, a remote-control channel for Chrome, and rebuild or close sessions when needed.

Once connected, the page state parts track tabs, page content, and visible elements, like a map of the current website. The action execution parts turn the agent’s commands into real clicks, typing, scrolling, waits, and screenshots, while fixing small input mistakes and waiting for the page to settle. The forms, downloads, and interruption parts handle file uploads, saved downloads, PDFs, and pop-up dialogs.

The two package marker files simply label these browser folders so Python can import them.

#### [Browser tool entrypoints and endpoint providers](stage-10.2.1.md) `stage-10.2.1` — 4 files

This stage is the doorway between the agent and a real Chrome browser. It is used during the main work loop whenever the agent needs to look at a web page or act on it. The core browser file defines a simple promise: for one turn of work, provide a Chrome DevTools Protocol endpoint. That endpoint is the control address Chrome exposes so other software can inspect pages, click, type, and read results.

Different provider files fulfill that promise in different environments. The Browserbase adapter points the system at a hosted browser using a fixed remote connection URL, so the project does not need to run Chrome locally. The sandbox Chrome adapter starts or reuses Chrome inside the same sandbox as the conversation, then safely shares its WebSocket control address with the browser engine.

On top of those connection pieces, the browser tools file exposes practical actions to the agent: open a page, read content, fill forms, click, take screenshots, and save downloads. Together, these files separate “how to reach Chrome” from “what the agent can do with Chrome.”

#### [CDP transport, runtime, and session lifecycle](stage-10.2.2.md) `stage-10.2.2` — 5 files

This stage is the behind-the-scenes plumbing that lets the project use a real Chrome browser during each work turn. It starts only when a browser tool needs it, keeps the connection alive while actions are being taken, and cleans it up when the turn ends. If Chrome crashes or the link breaks, it can rebuild the session so the task can continue.

backend.py is the outer workbench used by browser tools. It opens pages, reads content, clicks, types, handles tabs, uploads, and downloads. session.py is the central live browser session: one connection to Chrome plus shared state for pages, forms, dialogs, and mouse or keyboard actions.

cdp.py is the message pipe to Chrome DevTools Protocol, Chrome’s remote-control interface. It sends commands through a WebSocket, waits for replies, and routes browser events to listeners. wire.py checks that the JSON messages coming back have the expected shape. runtime.py safely runs JavaScript inside a page and turns browser-side failures into normal Python errors.

#### [Page state, tabs, and element lookup](stage-10.2.3.md) `stage-10.2.3` — 4 files

This stage is the browser’s working memory during the main interaction loop. It keeps track of which tabs exist, what is on each page, and how a requested page element can be found again when it is time to click or type.

The tab controller keeps the “tab list” current. It opens, closes, and navigates tabs, and listens for browser events so the rest of the system knows when pages change. The content tools are the safe front door for reading a page: they fetch page text and provide simple search functions without exposing the lower-level browser machinery directly.

The page mapper turns a live browser page into a structured map that an AI model can understand, like turning a messy webpage into a labeled floor plan. The finder works with the accessibility tree, a plain-text view of visible controls such as buttons, links, and fields. It searches that tree, builds clean matches, and checks that any element name invented by the model points to a real target before an action is taken.

#### [Browser action execution and input normalization](stage-10.2.4.md) `stage-10.2.4` — 7 files

This stage is part of the main work loop, where an automated agent’s plans become real browser actions. It starts with actions.py, which acts like a menu of permitted commands: click, type, scroll, wait, screenshot, and so on. Each command has an expected shape, so bad requests can be caught early. If the model invents an impossible value, errors.py provides a clear custom error for that case.

Before anything reaches the browser, fixup.py tidies common mistakes, such as adding a needed focus step before typing or filling in a missing wait time. coordinate.py then translates the model’s idea of “where” into the browser’s actual pixel positions, accounting for screenshot scaling and model-specific coordinate rules.

computer.py is the main driver. It takes the cleaned action, sends the right mouse, keyboard, scroll, or wait operation, and returns the new page state, screenshot, and safety notes. For keyboard work, keys.py converts text and shortcuts into Chrome’s low-level input messages. Finally, settle.py waits until the page has meaningfully finished reacting, so the next step sees a stable browser.

#### [Forms, downloads, and browser interruptions](stage-10.2.5.md) `stage-10.2.5` — 3 files

This stage supports the browser while the agent is doing its main work on websites. It covers the awkward moments that can interrupt normal browsing: forms that need typing, files that need uploading, downloads that must be captured, PDFs that Chrome wants to display instead of save, and pop-up dialogs that can freeze progress.

The forms part takes a simple instruction, such as “enter this text here” or “upload this file,” checks that the target page element exists and can accept the action, then sends the right low-level browser command to do it. These low-level commands use Chrome DevTools Protocol, which is a control channel for driving Chrome from software.

The downloads part watches for a download to start, waits until it is complete, and returns the downloaded data to the rest of the system. It also changes PDF behavior so a PDF link is saved as a file instead of opened in Chrome’s built-in viewer.

The dialogs part acts like a quick receptionist for browser pop-ups. It accepts, dismisses, or answers them so automation does not get stuck waiting.

## [Delegated subagents and specialized worker turns](stage-11.md) `stage-11` — 10 files

This stage is the system’s way of giving a main agent temporary helpers during a work turn. Instead of doing every task itself, the parent turn can start a child turn, send it a clear job, wait for its answer, send follow-up messages, or cancel it. The core subagents file is the switchboard: it connects a chosen helper profile to saved child-turn records and to the background queue that runs the work. The core profiles file supplies the default “general purpose” helper when no specialist is needed.

Extensions add specialist helpers. The browser files define a browser-capable subagent and delegation tools for one web session or many parallel visits. The research files define normal and deep research workers, plus a batch tool that runs many research jobs and saves their results together. The sites files define a website-building worker and a tool that starts it with a full website request. The brief pipeline files define outline, draft, and critique writing workers. Together, these pieces act like a workshop where the main agent assigns jobs to the right specialist and gathers the finished results.

## [External connector tools, provider APIs, and source objects](stage-12.md) `stage-12` — 23 files

This stage is part of the agent’s main work during a turn. It is the set of “adapters” that let the agent use outside services without directly holding private tokens. The core object system gives extensions a common way to expose durable named things, such as connected accounts, the workspace agent, conversations, sources, and synced pages. Some are editable, like the agent’s settings by the owner; others are read-only, like conversation records and synced pages.

The connector layer is the switchboard. It finds available provider tools, sends calls through a broker, and keeps secrets away from agent code. The connector tools let the agent list connected accounts, inspect or revoke them, call services like GitHub or Gmail, and move files between workspace storage and providers. MCP support adds another route for tool discovery from configured tool servers.

Composio and Pipedream provide two broker backends: their clients create login links and run actions, their brokers describe and execute tools, and their proxy helpers forward HTTP requests safely. Source and page tools track synced external content and notify conversations about changes. Exa supplies web search, and the YC bridge safely wraps the Y Combinator command-line tool. Package files simply make these extension folders importable.

## [Source synchronization, page change processing, indexing, search, memory, and graph enrichment](stage-13.md) `stage-13` — 72 files

This stage is the system’s data intake and recall workshop. It runs mostly behind the scenes, either on scheduled source-sync jobs or when a page-change hook fires. Its job is to bring in information from outside tools, notice what changed, store the latest document text, remove deleted items, and prepare everything for search, memory, alerts, and graph-based lookup later.

The provider-specific source connectors are the many “plugs” for services like Google, Microsoft, Slack, GitHub, Notion, Salesforce, and others. They translate each service’s records into one common format. The package marker file simply makes the source code importable. The backend file is the adapter bridge: it turns connector output into UFO pages, progress cursors, deletes, and snapshots. The sync file is the main engine: it pulls configured sources, saves changed text, records additions and removals, and hands changes onward.

The recall and enrichment parts then make the saved knowledge useful. Indexing splits text into searchable chunks and embeddings. Memory condenses important facts. The knowledge graph extracts people, places, and links. Alerts watch changed pages for topics that matter.

### [Provider-specific source connectors](stage-13.1.md) `stage-13.1` — 52 files

This stage is the system’s big set of source adapters. It sits behind the scenes in the data intake and sync process, not in the user-facing main work. Its job is to talk to many outside services through their APIs, which are web doorways for software to request data, and turn each service’s different shape into the system’s common shape: streams of records, pages of results, saved progress markers, readable text, updates, and deletes.

The framework and registry provide the shared plug shape and the address book for all connectors. The productivity connectors bring in Google and Microsoft mail, calendars, files, chats, and meetings. Project, developer, CRM, marketing, finance, HR, knowledge, scheduling, and community connectors each cover their own families of tools, such as Jira, GitHub, Salesforce, Stripe, BambooHR, Notion, Slack, and YC content. Together they work like many translators feeding one conveyor belt, so the rest of the system can sync, store, search, and recall data without needing to understand every provider’s quirks.

#### [Source connector framework and registry](stage-13.1.1.md) `stage-13.1.1` — 3 files

This stage is shared behind-the-scenes support for bringing outside data into the system. It does not collect one specific source by itself. Instead, it provides the common “plug shape” that all source connectors must fit, plus helpers for common web API work and a registry that says which connectors exist.

The connector framework defines the basic terms: a stream is a sequence of records from one place, a page is one batch of records, and saved progress is the bookmark that lets the system resume later without starting over. It also defines how raw source records are turned into readable text, and how to move safely through partitioned sources, such as many repositories or chat channels.

The REST helper supports connectors that read from web services. It makes authenticated HTTP requests, retries temporary failures, and follows paginated results.

The registry is the address book. It maps provider accounts to supported connectors and creates stable binding names so the rest of the system can refer to each source consistently.

#### [Google and Microsoft productivity connectors](stage-13.1.2.md) `stage-13.1.2` — 8 files

This stage is a set of read-only “connectors,” or adapters, that let the system bring work data in from Google Workspace and Microsoft 365. It is behind-the-scenes support for the main sync loop: each connector talks to an outside service, notices what is new or changed, and turns that data into plain records the rest of the system can store, search, and recall.

The Google connectors cover the main Workspace tools. Gmail reads mailbox changes and formats emails. Google Calendar fetches events and attendees. Google Docs and Sheets turn documents and spreadsheets into readable text. Google Drive streams files, shared drives, permissions, comments, and revisions. Google Meet brings in transcripts and generated meeting notes.

The Microsoft connectors do the same job for Microsoft Graph, Microsoft’s API for work data. Teams reads teams, channels, chats, and messages. Outlook reads email, threads, contacts, calendar events, and folders. Together, these files act like translators between cloud productivity apps and the project’s common sync format.

#### [Project and work-management connectors](stage-13.1.3.md) `stage-13.1.3` — 6 files

This stage is shared behind-the-scenes support for bringing work-management data into the system. It is not the main user-facing work loop. Instead, it acts like a set of adapters for different project tools, so the rest of the product can read tasks, comments, teams, boards, and activity in one consistent way.

Each file knows how to talk to one outside service. The Asana connector reads projects, tasks, stories, users, and workspace details from Asana’s web API, which is a structured way for software to request data. ClickUp’s connector walks through its nested setup of teams, spaces, folders, lists, tasks, comments, fields, and goals. Jira gathers projects, issues, users, comments, boards, and sprints across accessible Jira sites. Linear reads issues, projects, teams, comments, and workflow information through GraphQL, another style of data request. monday.com brings in users, boards, items, updates, activity logs, and tags. Wrike covers contacts, folders, tasks, comments, workflows, and custom fields. Together, these connectors turn many different tool shapes into steady streams of records the system can store, search, and recall.

#### [Developer operations and incident connectors](stage-13.1.4.md) `stage-13.1.4` — 3 files

This stage is shared behind-the-scenes support for bringing developer and operations data into the system. It is like a set of adapters for different tools that engineering teams already use. Each adapter talks to an outside web service through its API, meaning a structured way for software to request data, then reshapes the answers into records the rest of the system can sync, store, search, and recall.

The GitHub connector reads software-development activity: organizations, repositories, issues, comments, users, and related objects. It helps the system discover what code projects a user has access to and keep their GitHub activity up to date.

The PagerDuty connector reads operations data: services, incidents, notes, schedules, users, and on-call entries. This lets the system understand outages, responsibilities, and response history.

The Sentry connector reads application-monitoring data: organizations, projects, issues, events, members, and releases. Together, these connectors give the system a fuller picture of code, incidents, and production errors.

#### [CRM, sales, and customer-support connectors](stage-13.1.5.md) `stage-13.1.5` — 6 files

This stage is part of the shared data-gathering layer. It connects the system to tools that teams use to manage customers, sales, and support. Each connector knows how to talk to one outside service, ask for the right records, handle login, follow paged results, and reshape the replies into a common form the rest of the system can store, search, and sync.

The Attio connector reads companies, people, deals, tasks, notes, meetings, and call recordings. HubSpot covers a wide range of CRM and marketing data, including standard records, custom objects, relationships, email events, analytics, and consent information. Salesforce reads accounts, contacts, opportunities, tasks, and related objects, and also detects records deleted since the last sync. Freshdesk gathers support tickets, contacts, conversations, knowledge-base articles, and forum content. Intercom reads conversations, contacts, companies, admins, tags, and activity logs. Zendesk brings in tickets, users, organizations, Help Center articles, and community posts.

Together, these files act like adapters for different plug shapes, making many platforms feed one steady sync pipeline.

#### [Marketing, advertising, social, and forms connectors](stage-13.1.6.md) `stage-13.1.6` — 7 files

This stage is part of the system’s data intake work. It does not run the main product by itself. Instead, it acts like a set of adapters that let the sync engine talk to outside marketing and social platforms. Each adapter knows that service’s API, meaning its web doorway for requesting data, and reshapes the results into regular “streams” of records the rest of the system can store, search, or process.

ActiveCampaign brings in marketing data such as contacts, campaigns, deals, lists, tags, users, and custom fields. Facebook Ads reads ad accounts, campaigns, ad sets, ads, and daily performance numbers. Google Ads does the same kind of job for Google advertiser accounts, campaigns, ad groups, ads, and metrics. Instagram uses Facebook’s Graph API to collect business pages, linked Instagram accounts, posts, stories, and engagement statistics. Klaviyo reads marketing objects and supports paging and resuming from the last synced point. Mailchimp normalizes audiences, subscribers, campaigns, reports, and email activity. Typeform fetches forms, responses, and related assets, but only reads data and never changes it.

#### [Finance, billing, accounting, and commerce connectors](stage-13.1.7.md) `stage-13.1.7` — 7 files

This stage is shared behind-the-scenes support for bringing money-related business data into the system. It is not the place where invoices are paid or accounts are changed. Instead, it acts like a set of adapters that read from outside finance tools and turn their data into steady “streams” of records, meaning ordered batches the rest of the system can store, sync, and look up later.

Each file is one adapter for a different service. Brex reads spend-management data such as transactions, expenses, users, vendors, budgets, and departments. Chargebee, Recurly, and Stripe read subscription and billing data, including customers, subscriptions, invoices, payments, and related detail records. QuickBooks and Xero read accounting records such as accounts, bills, contacts, invoices, and payments, while hiding each service’s API quirks from the rest of the code. Square reads commerce data like customers, locations, orders, catalog items, payments, and inventory counts. Together, these connectors make many different finance systems look consistent to the main sync machinery.

#### [HR, payroll, and recruiting connectors](stage-13.1.8.md) `stage-13.1.8` — 6 files

This stage is a set of behind-the-scenes connectors for people and hiring tools. A connector is a translator: it knows how to talk to one outside service, ask for data, and reshape the answers into a common stream of records the rest of the system can sync, store, and search.

The recruiting connectors cover the hiring pipeline. Ashby reads candidates, jobs, applications, interviews, offers, and lookup lists from Ashby’s page-by-page API. Greenhouse does the same for Greenhouse Harvest, including sign-in, paging, and nested items such as interviews inside applications. Recruitee pulls candidates, job offers, departments, and other hiring records.

The HR and payroll connectors cover employee operations after hiring. BambooHR reads employee details, time off, timesheets, reports, and metadata, even though BambooHR returns these in several different shapes. Deel reads contracts, payslips, timesheets, tasks, and forms. Rippling reads companies, workers, and teams. Together, these files act like adapters for different plugs, making many systems feed one shared sync pipeline.

#### [Knowledge, collaboration, scheduling, and community content connectors](stage-13.1.9.md) `stage-13.1.9` — 6 files

This stage is behind-the-scenes support for bringing outside team knowledge into the system. It does not create the main product experience by itself. Instead, it acts like a set of translators that visit other services, read what is there, and reshape it into clean records the rest of the system can store, search, and show.

The Airtable connector reads bases, tables, and records, but only in read-only mode, so it never changes the original Airtable data. The Calendly connector reads scheduling information such as users, event types, groups, scheduled meetings, and invitees. The Confluence connector pulls Atlassian spaces, pages, blog posts, comments, groups, and audit records, turning them into readable text. The Notion connector does the same for pages, databases, blocks, comments, and users. The Slack connector reads workspace people, channels, messages, threads, and participants. The Y Combinator connector imports selected YC guidance and limited directory-style searches, such as companies, founders, jobs, and posts, as shared searchable memory.

### [Recall, indexing, memory consolidation, and graph extraction](stage-13.2.md) `stage-13.2` — 15 files

This stage is shared behind-the-scenes support for remembering, searching, and reacting to changed pages. When text changes, the indexing rules split it into chunks, create embeddings, which are number lists that capture meaning, and store them for later search. The OpenAI embedding extension makes those vectors. The default index keeps them locally, while the Turbopuffer extension can send them to an external search service.

On top of that search base, the memory extension turns useful page content into longer-lasting facts. Its manifest plugs in recall hooks, tools, and background jobs. Its store saves and searches memories, subjects label who each memory belongs to, and the core memory doorway gives all providers the same search shape. The condenser turns raw pages into memories and later merges older memories into summaries. Memory objects let callers read saved memories safely.

The knowledge-graph extension adds another view: it extracts entities and links from pages, then lets queries follow those connections.

## [Scheduled, recurring, billing, evaluation, and self-improvement jobs](stage-14.md) `stage-14` — 16 files

This stage is the system’s behind-the-scenes clockwork. It runs work that should happen later, repeatedly, or without a person waiting on the screen. The scheduled-tasks files let agents create recurring tasks, pause a workflow until a reply or timeout, check simple “cron” schedules, meaning repeating time rules, and safely wake due tasks without firing the same one twice. Core scheduling stores those tasks, claims them, and advances or removes them. Core jobs turns extension-declared background work into real queued runs in the right workspace.

Billing is handled by the Metronome extension, which connects usage and seat counts to Metronome and Stripe, and lets owners manage billing through chat. Evaluation support provides fake but realistic mail, calendar, and code-search connectors, so tests can run safely without touching real services.

The self-improvement pieces form a cautious improvement loop. They collect past failures into a test corpus, ask a model to propose a better prompt, replay old conversations without rerunning real tools, judge the results, and use statistical gates before opening an approval proposal. Governance then makes sure prompt changes are reviewed and not applied over newer edits.

## [Reply streaming, writeback, artifact sharing, and final turn commit](stage-15.md) `stage-15` — 8 files

This stage is the system’s “delivery belt” while a conversation turn is running and when it finishes. As the model produces text, tool updates, costs, and final results, core/src/ufo/hub.py publishes those small live messages, called frames, to any screen that is watching. It also keeps recent frames so a reconnecting client can catch up. core/src/ufo/surfaces/hub_tail.py lets a client join late, replay what it missed, and know for sure when the turn is done by checking both the live hub and the database.

For setups with more than one server process, extensions/redis_hub/ufo_ext_redis_hub/stream_hub.py sends the same live frames through Redis Streams, a shared message pipe. Its __init__.py just makes that extension importable.

When the answer is accepted, core/src/ufo/loop/transcript.py writes the transcript durably to blob storage and prevents older saves from replacing newer ones. Files produced by the turn become artifacts through core/src/ufo/artifacts.py. core/src/ufo/artifact_token.py creates safe, short-lived download passes, and core/src/ufo/surfaces/artifacts.py checks those passes before serving the private file bytes.

## Turn teardown, cleanup, cancellation propagation, and sandbox disposal `stage-16` — 0 files
## [Cross-cutting data model, persistence, blob storage, and database safety](stage-17.md) `stage-17` · (cross-cutting) — 19 files

This stage is the system’s shared filing cabinet. It sits behind every phase: startup uses it to prepare the database, the main work loop uses it to save and read state, and shutdown uses it to close connections safely. The database gateway opens connections, runs migrations, wraps work in safe transactions, and cleans up afterward. The table blueprint defines the common database shape, while the schema package groups these rules. Record models define stable conversation turns and IDs, and the transcript format keeps saved conversations readable over time, including compacted versions.

Safety is a major part of this stage. The row-level security setup keeps each workspace’s data separate, like locked drawers in the same cabinet, and creates the limited database role used during normal service. Blob storage provides one interface for large files, whether stored locally or in S3.

The migration files are upgrade instructions. They add and adjust storage for searchable chunks, memory records, memory pages, timestamps, provenance links, fast lookup indexes, and knowledge-graph entities and relationships. Together they let the data model grow without breaking old installations.

## [Cross-cutting public SDK, extension interfaces, manifests, types, and conformance samples](stage-18.md) `stage-18` · (cross-cutting) — 30 files

This stage is shared behind-the-scenes support for people who build extensions. It is not the main engine loop. It provides the public “front door” that extensions should use, so their code does not depend on private internal files that may move or change.

The SDK facades expose safe import paths for security, credentials, accounting, operator tools, logging, browser links, connectors, model access, search, memory, jobs, schedules, objects, skills, surfaces, and tools. These pieces act like labeled counters in a service center: each one points extension code to an approved part of the system.

The package marker files for ufo.ext and ufo.sdk simply make those folders importable. The manifest files define and expose the menu of things an extension or pack can declare, such as tools, routes, jobs, hooks, credentials, skills, and backends. The context file exposes the request-time information extension handlers receive. The HTTP file exposes request and response types plus a safe cookie helper. The sample extension then uses these public paths end to end, proving that real extensions can register features and be tested through the same supported interface.

### [Public SDK auth, credentials, accounting, and operator utilities](stage-18.1.md) `stage-18.1` — 7 files

This stage is shared behind-the-scenes support for extension authors and administrators. It provides stable “front doors” in the public SDK, so outside code can use approved security, accounting, and operator tools without reaching into private core modules that may change.

The accounting file exposes the project’s existing spending and accounting summary types. Grants does the same for grant audit summaries and helper access, giving a safe way to inspect who was allowed to do what. Seats exposes the core seat-related types and helpers, so code can read seat or usage information through a predictable SDK path.

Bearer provides token verification helpers. A bearer token is a secret string used to prove a request is allowed; this file only exposes checking, not creating, tokens. Credentials exposes the credential tools extensions are permitted to use. Operator gathers helpers for protected operator-only web sessions, such as debug or admin tools. Finally, o11y exposes structured logging helpers, so extensions can write consistent logs and warnings.

### [Public SDK backend, connector, model, and search integration facades](stage-18.2.md) `stage-18.2` — 10 files

This stage is the public front door for extension authors. It is not the main work loop itself. Instead, it is shared support that keeps outside code safely connected to the engine without depending on the engine’s private folder layout. Each file acts like a labeled counter in a service desk, forwarding users to the right internal tools.

The browser file exposes the allowed browser connection types. The connectors and authproxy files provide stable imports for feed-sync connectors, OAuth, and credential handling. The sources file gathers the pieces needed to build a content source, including sync, pagination, REST connector helpers, and subject types. The hub file exposes hub and model-interface types used to coordinate work. The models file gathers model clients, messages, tools, pricing records, and API descriptions. The sandbox file exposes sandbox backend types and helpers. The index file provides search and embedding backend interfaces, while memory and search re-export the project’s memory-search and search interfaces. Together, these files form a stable SDK layer that lets extensions plug in without reaching into internal code.

### [Public SDK extension capability facades](stage-18.3.md) `stage-18.3` — 6 files

This stage is shared support for extension writers. It is not where jobs run, schedules tick, or tools execute. Instead, it provides stable “front doors” into the SDK, so outside extension code can import approved names without depending on the project’s private folder layout. This is like giving users one clear service desk instead of sending them through staff-only corridors.

Each file is a small facade, meaning it mostly re-publishes selected types, constants, errors, or helper classes from deeper internal modules. jobs.py exposes the public job-related tools. scheduling.py exposes scheduling names from the internal scheduling system. objects.py gathers the object API names that extensions are allowed to use. skills.py provides the import point for skill-related SDK pieces, while the real behavior lives elsewhere. surfaces.py collects the main building blocks for “surfaces,” the places where extensions interact with users or workspaces. tools.py exposes public tool-related types. Together, these files keep extension code clean, stable, and insulated from internal refactors.

## [Cross-cutting security, credentials, network egress policy, and authorization boundaries](stage-19.md) `stage-19` · (cross-cutting) — 13 files

This stage is shared security plumbing that runs behind the scenes whenever work happens in a workspace. A workspace is a customer or project boundary. The code makes sure secrets, files, network calls, and charges stay inside that boundary.

workspace.py labels each job with the right workspace, while ext/context.py gives extensions a limited toolbox instead of direct access to raw storage or credentials. credentials.py encrypts saved secrets and checks whether a request may use them. credential_kind.py shows which secret slots exist without revealing values. token_signing.py creates signed strings that can be trusted later because changes are detectable.

The sandbox network proxy is the gate at the edge. proxy_serve.py starts the shared proxy, server.py enforces each outbound request, and rules.py turns grants, manifests, models, and credentials into allow-or-deny rules. It can add secrets to approved requests without putting them inside the sandbox. fs_creds.py similarly gives short-lived access only to allowed workspace files.

The extension files plug in real providers: GitHub App tokens, API-key connectors such as Datadog, and direct source-sync credentials. Together they let useful integrations work while keeping raw secrets hidden.

## [Cross-cutting observability, usage accounting, operator inspection, and generic utilities](stage-20.md) `stage-20` · (cross-cutting) — 5 files

This stage is shared behind-the-scenes support for running the system safely and understanding what it is doing. It is not the main conversation engine. Instead, it acts like the control room: it watches activity, records costs, and gives operators safe read-only windows into workspace state.

The observability file, o11y.py, sets up traces, metrics, and structured logs. In plain terms, traces show the path of a request, metrics are counts and timings, and logs are detailed event notes. It also strips sensitive data before sending records outward.

The accounting file records token use, turns that into billing-ready usage data, and checks spend limits for workspaces, members, or agents. This helps prevent unexpected overuse.

The debugger package marker only makes the debugger extension importable. Its surface file provides the actual operator web page and JSON API for viewing conversations, turns, files, transcripts, compactions, and live events for one workspace. The memory surface similarly offers a read-only memory explorer. Together, these pieces help operators inspect problems without changing user data.
