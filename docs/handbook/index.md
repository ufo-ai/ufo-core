# System Handbook — Stage Index

Each stage below links to its full page; the paragraph is the stage's role in the system.

## [Deployment preflight and schema upgrade](stage-1.md) `stage-1` — 58 files

This stage happens during deployment, before the system starts serving users. Its job is to make sure the database and sandbox infrastructure are ready, so new code does not run against missing tables, old fields, or broken storage.

The Alembic runner is the upgrade engine. Alembic is a database change tool that applies numbered steps in order. The core migrations first lay down the basic records for workspaces, members, agents, conversations, turns, identities, proposals, and nested work. Other core migrations add the records needed for credentials, permissions, Slack and web entry points, imported content, incoming messages, spending ledgers, seats, runtime workers, scheduled jobs, and fast lookup indexes.

Extension migrations prepare extra feature areas. They create storage for evaluations, indexing and search, knowledge graphs, sample data, skill creation, and the memory system’s facts and pages.

Finally, the sandbox safety gates test the outside pieces that code will depend on at runtime. They start real sandboxes, verify workspace storage can be mounted and written to, and confirm secure proxy access works. Together, these checks turn an upgraded deployment into a safe starting line.

### [Alembic runner and core schema baseline](stage-1.1.md) `stage-1.1` — 4 files

This stage is part of setting up and evolving the database, which is where the system keeps its long-term records. It uses Alembic, a tool that applies database changes in a safe order, like following numbered renovation plans for a house.

The runner file, env.py, is the bridge between Alembic and the project. It reads the project’s database setup and table definitions, then tells Alembic how to connect and apply changes.

The first migration, 0001_heartbeat.py, lays the foundation. It creates the earliest tables for workspaces, members, agents, conversations, conversation turns, identities, and usage costs. These are the basic records the rest of the system builds on.

The proposal migration, 0003_proposal.py, adds a place to store proposals, and also describes how to remove it during a rollback.

The loop-depth migration, 0004_loop_depth.py, expands conversations so one turn can contain delegated or nested turns, including work done by subagents as well as command-line users.

### [Core identity, credentials, grants, and surfaces](stage-1.2.md) `stage-1.2` — 6 files

This stage is behind-the-scenes setup for the database, the place where the system keeps long-term records. These migrations are ordered changes that prepare the system to recognize users, store access details, and support different places where conversations can happen.

The first migration adds a secure credentials table, so encrypted login or provider secrets have a proper home. The Slack migration widens the system beyond command-line use: it lets conversations and identities come from Slack and stores reply-tracking data so Slack messages are not sent twice. The web migration does the same kind of doorway-opening for the web interface, allowing “web” as a valid source. The grant migration adds records for permissions, remembering when an agent may use a specific provider account in a workspace. The surface seam migration loosens older limits on conversation sources and adds storage for files or other shared artifacts attached to a turn. Finally, the shared-grant migration adds a flag showing whether a grant should be treated as shared. Together, these changes give the rest of the system reliable identity, access, and surface records to build on.

### [Core source and content-ingestion schema](stage-1.3.md) `stage-1.3` — 5 files

This stage is shared behind-the-scenes support for content ingestion: the part of the system that remembers external content sources and the pages brought in from them. It is made of database migrations, which are step-by-step changes to the database structure.

The first migration creates the basic machinery. It adds records for sources, such as places the system can import from, and pages that came from those sources. It also stores workspace membership and timing information, so the system knows what belongs where and when to sync again.

Later migrations refine that machinery. One opens the source “backend” field, meaning the type of importer, so extensions can add new kinds beyond the original folder-based source. Another adds an error counter, so repeated failures can be noticed and future retry behavior can slow down instead of hammering a broken source. Another adds a removal timestamp, letting the system mark a source as removed without losing its history. The final migration adds ownership, so a source can be shared or tied to a specific member. Together, these changes make imported content trackable, extensible, and safer to manage.

### [Core turn, conversation, and inbound-message schema](stage-1.4.md) `stage-1.4` — 11 files

This stage is behind-the-scenes database preparation. It changes the saved data layout so conversations, turns, and incoming messages can be tracked safely as the system grows. A “turn” is one step in a conversation, such as a user message or an agent response.

The turn migrations add guard fields so only the right run attempt owns a turn and resumes are not queued twice. They also add tracing links, stored surface context like sender or timezone, speaker and authorization details, parent-child lookup speed, and “on behalf of” fields for scheduled or automated work. Together these make each turn easier to resume, audit, connect, and search.

The conversation sandbox migration lets a conversation remember its working sandbox, like keeping the same workbench between sessions. The surface workspace migration makes delivery identifiers safe across different workspaces, preventing name collisions. The inbound-message migrations add a holding table for messages before the conversation engine consumes them, enforce ordering and uniqueness, and then adjust how rendered message content is stored as the design changes.

### [Core spending, ledger, and seat schema](stage-1.5.md) `stage-1.5` — 9 files

This stage is behind-the-scenes database setup. It changes the system’s record books so later features can track spending, usage, exports, and workspace membership limits correctly. The spend cap migration adds a table for rules like “this workspace or user can spend only this much,” and adds a “parked” state for turns, meaning work can be paused instead of finished. Several migrations expand the ledger, which is the system’s accounting log: it can now record egress, sandbox tokens, a price digest, and entries tied to a whole workspace instead of only one conversation turn. Another migration adds a ledger export table, so the system can remember when accounting data was exported, and a BYOK flag to mark exports made with a customer’s own encryption key. The seat migrations add workspace seat tracking: who took a paid or limited seat, how many seats a workspace may allow, and how many seats are included by default. Together, these migrations give the product reliable accounting and quota foundations.

### [Core runtime fleet, scheduling, and job schema](stage-1.6.md) `stage-1.6` — 8 files

This stage is behind-the-scenes groundwork for running the system at scale. It is made of database migrations, which are ordered changes to the database layout. Together they give workers a reliable “control panel” for knowing what is running, what should run later, and how to find work quickly.

The runtime-instance migrations add and refine records for live runtime processes. First, each runtime can be tied to a workspace and report when it started and last checked in. Later, shared fleet runtimes are allowed to exist without a workspace, and old dedicated-runtime columns are removed to match the newer shared-fleet model.

The scheduled-task migrations add a place to store jobs that should run in the future or repeat, plus safe claiming rules so two workers do not grab the same task. They also record the last turn that triggered a scheduled task. The scheduled-pause and scheduled-admission migrations let the system mark pauses and admit turns that came from the scheduler. Finally, job-candidate indexes act like a book index, helping background sweeps find eligible rows without reading everything.

### [Extension platform and non-memory extension schemas](stage-1.7.md) `stage-1.7` — 7 files

This stage prepares the database for extensions. It is part of setup and upgrade work: before the main system can use these features, these migrations create or change the tables they need. A migration is a small, ordered database change that can usually also be undone.

The core `ext_store` migration adds a shared key-value store for extensions. It lets an extension save JSON data for a particular workspace under its own name and key, like a labeled drawer. The evaluation environment migration creates test email and calendar tables, so fake data can be loaded and removed safely. The indexing migrations build storage for searchable content chunks and their vector embeddings, which are number lists used for “similar meaning” search. They also update chunks so each one belongs to a workspace. The knowledge graph migration adds tables for named things and the links between them. The sample extension creates a simple per-workspace note table. The skill-creation migration stores user-made skills by workspace and name. Together, these files give each extension its own reliable storage shape.

### [Memory extension schema migrations](stage-1.8.md) `stage-1.8` — 6 files

This stage is behind-the-scenes setup for the memory extension. It is not part of the daily work loop itself. Instead, it prepares and updates the database shape, much like adding labeled shelves and shortcuts in a filing room before people start using it.

The first migration creates the main memory table, where the system can store remembered facts, episodes, and shared knowledge. The second adds a separate table for memory pages, which organize memory into page-like units. The third improves each memory record by adding its kind, such as what type of memory it is, and a confidence value, meaning how sure the system is about it. The fourth makes every memory page point directly to a workspace, so ownership is clear and enforced. The fifth adds an index, a database shortcut, to quickly find old active facts ready for consolidation. The sixth adds another index so the inventory view can quickly show one workspace’s memory items from newest to oldest. Together, these migrations make memory storage organized, traceable, and faster to browse or clean up.

### [Sandbox deployment safety gates](stage-1.9.md) `stage-1.9` — 2 files

This stage is a set of deployment “preflight” checks. It runs before real traffic is allowed, like testing the brakes and lights before a vehicle leaves the garage. Its job is to prove that sandbox environments can use the same key infrastructure they will depend on in production-like use.

The mount gate checks workspace storage. It starts a real sandbox, connects the workspace storage the same way production does, writes a small test file, and reads it back. If the sandbox cannot mount the storage or the file does not survive the round trip, the deployment is stopped.

The proxy gate checks secure web access through the sandbox proxy. It creates a fresh sandbox outside the main cluster, installs the required trusted certificate, then uses HTTPS, the encrypted web protocol, to reach the proxy and confirm the expected response. Together, these gates catch broken storage or certificate routing before users can hit them.

## [Process startup and service bootstrap](stage-2.md) `stage-2` — 4 files

This stage is the system’s “turn the lights on” step. It happens before normal request handling begins. Its job is to read settings, connect to databases, prepare web servers, set up storage and monitoring, and install clean shutdown behavior so the service can stop safely later.

The main shared server starts in `core/src/ufo/serve.py`. It brings together the UFO configuration, database access, extensions, sandboxes, background jobs, web routes, and security boundaries into one running service. For local users and developers, `core/src/ufo/cli.py` provides `ufoctl`, the command-line front door used to create, run, inspect, extend, chat with, and package a workspace. For network access from sandboxes, `core/src/ufo/proxy_serve.py` starts the shared egress proxy, a controlled gateway that only allows approved outside connections. The hosted control service starts through `control/src/ufo_control/main.py`, which runs the web gateway, creates invitation emails, and prepares database security rules. Together, these entry points prepare the machinery before the rest of UFO can do useful work.

## [Pack selection and extension discovery](stage-3.md) `stage-3` — 33 files

This stage is part of startup. It decides which bundles of features, called packs, are active for a workspace, then discovers the extensions inside them. A pack is like a preset toolbox: the local assistant pack, hosted assistant pack, chief-of-staff pack, and YC founder pack each name a different collection of services, tools, and skills to load.

The extension store acts like a small app store. It reads the available extension catalog, shows what is pinned, and updates the lockfile, which is the saved record of installed choices. The loader is the gatekeeper. It finds installed extensions, checks that they are allowed, then turns their written declarations into usable system parts such as tools, hooks, skills, object types, and backend providers.

Capability registration is the sign-in desk for those declarations. Extensions announce what they offer before anything runs. Together, the packs choose the desired toolbox, the store records the choice, the loader validates it, and registration builds the capability graph the rest of the workspace can use.

### [Capability registration](stage-3.1.md) `stage-3.1` — 27 files

Capability registration is the system’s startup sign-in table. Before the agent can do useful work, extensions declare what they add: tools, web pages, helper agents, login methods, search sources, browser runners, long-term memory, scheduled jobs, and reusable skills. These declarations are called manifests, meaning small files that describe available features without running them yet.

The subagent profile and agentic manifest parts define specialist helpers, such as browser, coding, research, writing, and website-building agents, and say what instructions and tools each one gets. Connector, credential, and source manifests register outside services like Slack, Pipedream, Composio, and searchable content sources, including how users authenticate. Surfaces and runtime providers announce user-facing entry points like web, terminal, and debugger views, plus hosted browser access. Memory, knowledge, and page-change hooks add background knowledge features, such as graph search, long-term memory, and alerts when watched pages change. Skills and scheduled work manifests register document abilities, custom skills, recurring tasks, and daily evaluation jobs. Together, these parts build the catalog the main system uses later.

#### [Subagent profile implementations](stage-3.1.1.md) `stage-3.1.1` — 5 files

This stage defines the “job descriptions” for helper agents. A helper agent is a smaller agent started by the main agent to do a focused task. These profiles are shared support for the main work loop: when the system decides to delegate, it looks here to know what kind of helper to start, what instructions to give it, what tools it can use, and what input and output formats it must follow.

The core profile file provides the default general-purpose helper, used when no more specific extension fits. The brief pipeline extension adds a staged writing workflow: one helper makes an outline, another writes a draft, and another critiques it, with limits and data shapes for each step. The browser extension defines a web-using helper for browser tasks. The research extension defines standard and longer-running research helpers, including their tools and model choices. The sites extension defines a website-building helper that can create and serve sites. Together, these profiles act like labeled toolkits the main agent can pick from.

#### [Agentic extension manifests](stage-3.1.2.md) `stage-3.1.2` — 5 files

This stage is shared setup support for the agent system. It does not perform the work itself. Instead, each manifest file acts like a registration card that tells the main application which specialist helpers exist, what tools they can use, and what instructions or skills should be loaded for them. A “subagent” is a smaller expert agent that the main agent can delegate to when a task needs focus.

The brief-pipeline manifest registers a three-step writing workflow: outline, draft, then critique, plus skills used by the parent agent. The browser manifest registers a browser-focused subagent, its web tools, and guidance for when browsing should be handed off. The coding manifest registers the coding subagent, its tools, expected inputs and outputs, prompts, and skill folders. The research manifest loads research tools, research subagents, prompts, and optional skills. The sites manifest does the same for website-building work. Together, these files let the host discover and activate specialized extensions safely and consistently.

#### [Connector, credential, and source manifests](stage-3.1.3.md) `stage-3.1.3` — 6 files

This stage is the system’s registration desk for connector-based extensions. It runs behind the scenes, mostly during startup, so the main application knows which outside services exist, how users can sign in to them, and what tools or content sources they add. A “manifest” is a small declaration file, like a menu card, that tells the host what an extension offers.

The Composio manifest registers Composio-backed connectors, their login path, and the shared server-side broker that talks to Composio. The connectors manifest advertises the general connector extension, including its tools, object type, and prompt text. The Pipedream manifest lists Pipedream connectors and the browser sign-in and redirect routes used for OAuth, which is a standard “log in through another service” flow. The Slack manifest wires in Slack routes, credentials, setup skill, and tools. The sources manifest registers searchable content sources, their needed credential slots, and authentication proxy. The YC manifest adds YC tools, credentials, searchable sources, onboarding, and skills.

#### [Surfaces and runtime providers](stage-3.1.4.md) `stage-3.1.4` — 4 files

This stage is part of the system’s startup and discovery work. It tells UFO which “front doors” are available for people or tools to use, and which browser engine can run web tasks. A surface means a user-facing entry point, such as a web page, terminal, or debugger connection.

The web manifest is like a registration card for the web extension. It gives the extension a name and declares the web surface that UFO should mount. The UFO manifest does the same for the terminal-facing shell, including how a workspace is identified so the right session is reached. The debugger manifest registers a debugger surface and protects it with the usual workspace identity check, so only the right operator can access it.

The Browserbase provider is different: it supplies execution power rather than a visible surface. It reads a saved Chrome DevTools Protocol connection URL, which is a remote-control address for Chrome, and offers that hosted browser to the rest of the system instead of launching Chrome locally.

#### [Memory, knowledge, and page-change hooks](stage-3.1.5.md) `stage-3.1.5` — 3 files

This stage is shared behind-the-scenes support. It is about teaching the UFO platform what long-term knowledge features are available before the main work begins. Each file is a manifest, which is like a sign-up sheet that says, “Here are the tools and reactions this extension provides.”

The knowledge-graph manifest connects the app to graph search. A graph is a web of linked facts, like people, pages, and ideas connected by relationships. It also registers a prompt-time hook, which can add useful graph context before the assistant answers, and a page-change hook, which extracts new facts when synced pages change.

The memory manifest does the same for long-term memory. It declares tools, search, scheduled background jobs, a skill, and a small web surface so the system can store and retrieve remembered information over time.

The page-alerts manifest adds tools to create, list, and cancel watches on pages. Its change hook reacts when a watched page is updated, so the system can alert the user.

#### [Skills and scheduled work manifests](stage-3.1.6.md) `stage-3.1.6` — 4 files

This stage is the system’s sign-up sheet for extra abilities and timed work. It mostly runs behind the scenes when the host loads extensions, telling the rest of the system what tools, skills, and scheduled jobs are available.

The documents manifest registers ready-made document skills, so the agent can later create, review, or format Word files, slide decks, spreadsheets, and PDFs. The scheduled-tasks manifest adds the machinery for recurring work: a task object to describe what should repeat, a wait tool for pausing until the right time, a clock-driven runner that checks when jobs are due, and a scheduling skill the agent can use. The self-improvement manifest plugs in a daily evaluation job and defines what should happen when that job runs. The skill-create manifest lets workspace members build and manage their own reusable skills, then makes those saved skills available in later conversations.

Together, these manifests act like labels on drawers in a workshop: they tell the system what equipment exists and when to bring it out.

## [Onboarding, workspace creation, and member provisioning](stage-4.md) `stage-4` — 8 files

This stage is the front door for a hosted UFO user. It runs during startup for a new person or team: proving they are allowed in, creating or finding their workspace, adding them as a member, and handing back the address and bearer token, which is the secret pass they use to connect later.

The main web server is built in gateway.py. It guides the user through installing the client, entering an email, using an invite if required, and receiving their connection details. gateway_claim.py manages the email proof: it creates a short-lived code and rejects expired or repeatedly wrong attempts. gateway_email.py decides whether an address looks like a work email and sends the code, either through Amazon SES or to the local log in development. gateway_invite.py checks one-time invite codes. gateway_directives.py formats simple instructions sent to the terminal client. gateway_web.py presents the same flow in a browser. gateway_shared.py links a verified email domain to one shared workspace, creates it when needed, adds the member, and ensures a default agent exists. core/src/ufo/onboarding.py performs the deeper first-run workspace setup.

## [Identity, sessions, OAuth, and credential connection](stage-5.md) `stage-5` — 5 files

This stage is shared behind-the-scenes support for answering the question: “Who is this request allowed to act as, and which outside accounts may it use?” It is used when a user connects services like Gmail or Slack, and later when an agent needs permission to use them.

The grants file is the permission ledger. It records which agent may use which connected account, and helps limit which outside websites the proxy may contact. The CLI surface provides the return point for OAuth, which is the common “sign in with another service” browser flow. After the outside provider sends the user back, it finishes the connection.

The Composio and Pipedream provider files are bridges to hosted consent pages. They send the user to those services to approve access, so this project does not store long-lived secret tokens. The Pipedream client is the guarded doorway to Pipedream itself. It creates consent links, checks the connected account matches the right user or workspace, runs actions, and reports API errors in a clear way.

## [Surface routing and inbound event handling](stage-6.md) `stage-6` — 6 files

This stage is the system’s front door. It sits between people or external services and UFO’s core conversation engine during normal use. Each surface receives events in its own format, checks who is allowed in, and translates the event into a UFO conversation or message.

The core surface bridge is the common adapter. It helps surfaces identify users, create conversations, accept new messages, stream partial replies, fetch needed credentials, and deliver final replies back to durable places such as Slack. The web surface provides the browser chat page, live streamed answers, and recent workspace spending. The Slack surface verifies Slack requests, turns messages and button clicks into UFO turns, and sends responses, status updates, and files back to Slack. The terminal surface serves the `ufo` command-line client by turning conversation activity into simple text instructions a shell script can display. The debugger surface gives authorized operators read-only views into conversations, transcripts, files, and live events. The memory surface similarly gives operators a read-only page and API for inspecting stored workspace memories.

## [Turn admission, durable queuing, and live stream attachment](stage-7.md) `stage-7` — 5 files

This stage is the traffic controller for incoming conversation messages. It sits at the point where a user message arrives and the system must decide what happens next. The admission code is the front door. It checks rules and current state, then either starts a new agent turn, attaches the message to a turn already in progress, parks it behind spending limits or pauses, or rejects it.

If a turn should run, the durable queue records it safely and runs turns one at a time for each conversation, so replies do not overlap or get lost after a crash. It also prepares the needed tools, credentials, sandbox, and model before the agent begins work.

While the turn runs, the hub acts like a live radio channel for progress updates. Interfaces can listen, disconnect, and resume from a saved position. The Redis stream hub extends that channel across multiple server processes. Finally, the hub tail connects clients to the live stream and checks the database for final or parked states, so late or reconnecting clients see the correct ending.

## [Turn claiming and runtime assembly](stage-8.md) `stage-8` — 9 files

This stage is the handoff from “there is work waiting” to “one worker is ready to run it.” A worker first claims one queued turn, like taking the next ticket from a shared counter so two workers do not do the same job. It then builds the full per-turn environment: the conversation workspace, the user or team member, the chosen agent, the transcript so far, available tools, model choices, credentials, billing information, cleanup tasks, and extension hooks.

Two support stages supply the main parts of this setup. Sandbox workspace and egress setup creates the safe room where commands can run, with mounted files, controlled network access, and optional browser support. Skill and per-turn toolbox loading fills that room with the right working kit: selected skills, registered tools, and limited access to files, accounts, search, sources, subagents, and background jobs. Together, these pieces turn a queued request into a contained, well-equipped runtime where the agent can start the main work safely and with the right context.

### [Sandbox workspace and egress setup](stage-8.1.md) `stage-8.1` — 5 files

This stage prepares the safe “room” where a conversation can run commands. It is part of the setup before the main work begins. The goal is to give the command runner a workspace, tools, and controlled network access without letting it freely affect the host machine.

The workspace mount helper writes the instructions for attaching the conversation’s S3-backed files at /workspace inside the sandbox. S3 is cloud object storage, so this is like giving the room a locked filing cabinet with the right key. The Docker extension can create and manage a private local container for the conversation, while the E2B extension can use a remote cloud sandbox with the same general interface. The sandbox image builder keeps those local and cloud environments based on the same recipe, so commands see the same tools in either place. The sandbox Chrome extension starts a real headless Chrome browser inside the sandbox and exposes only a controlled debugging connection. Together, these pieces create a consistent, isolated workspace for executing code safely.

### [Skill and per-turn toolbox loading](stage-8.2.md) `stage-8.2` — 4 files

This stage prepares the agent’s working kit for a single turn or job. It is shared behind-the-scenes support that happens before tools or extensions run. First, the skill runtime defines a “skill” as a folder of packaged instructions or code. It can read those folders, list what is available, and copy only the selected skill into the sandbox, which is the agent’s isolated work area. Next, the tool registry acts like a menu. It records each tool’s name, description, expected input, how to run it, and any safety labels. The tool context then gives each tool a controlled doorway to the outside world: files, browser sessions, connected accounts, credentials, search, artifacts, subagents, and cleanup tasks, but only where allowed. Finally, the extension context builds a similar limited toolbox for extensions and background jobs. It keeps them inside the current workspace and exposes only approved storage, transcripts, models, sources, scheduling, and credential access. Together, these pieces let the agent work with useful abilities while keeping each action boxed in and workspace-safe.

## [Prompt, transcript, and retrieval context construction](stage-9.md) `stage-9` — 10 files

This stage prepares the “briefing packet” the model receives before it answers. It runs during the main conversation loop, just before a reply is generated. Its job is to gather the current conversation, the right instructions, and any useful background facts, then fit them into the model’s limited context window, meaning the amount of text the model can read at once.

The compaction file acts like an editor when the transcript gets too long. It keeps the newest messages as they are, turns older messages into a structured summary, and saves both versions so people can review what changed. The prompt rendering file builds the system prompt, which is the instruction sheet for the model. It fills in templates, checks that required pieces are present, and records a digest, like a fingerprint, so prompt versions can be tracked.

The recall sub-stage adds outside knowledge. It searches saved memories, synced source pages, search indexes, and linked knowledge-graph facts. Together, these parts turn a raw chat into a focused, traceable context bundle.

### [Memory, search, and graph recall](stage-9.1.md) `stage-9.1` — 8 files

This stage is the system’s recall machinery. During the main conversation loop, it helps find useful facts from past memories, synced pages, and connected knowledge before a response is made. It also does background upkeep when pages or memories change.

The indexing rules in core indexing split text into searchable chunks and define a common interface so the rest of the system does not need to know which search engine is used. The default index stores those chunks locally and searches by matching words or by meaning. The OpenAI embedding extension turns text into number lists, called vectors, so “similar meaning” can be searched even when the same words are not used. The Turbopuffer extension swaps in an outside search service for the same job.

The memory store saves and searches long-term memories, and also indexes source pages. The condenser turns raw pages into cleaner remembered facts and merges related facts over time. The shared memory interface lets callers search memory in a consistent way. The knowledge graph store adds another path: it links entities and relationships, then retrieves nearby connected facts.

## [Model selection, request dispatch, and streamed response normalization](stage-10.md) `stage-10` — 6 files

This stage is the system’s “call the right AI” layer. It sits in the main work loop, after the program has a prompt or tool request ready, and before the answer is handed back to the rest of the app. The registry is the switchboard: it looks up the requested model, chooses which provider can serve it, finds the needed key, and records pricing information.

The provider files are adapters, like plug shapes for different sockets. The OpenAI adapter sends chat messages to OpenAI or compatible services and converts the streaming reply into the project’s standard text, tool-call, and usage events. The Anthropic adapter does the same for Anthropic’s Messages API, including prompts, tools, and images. The OpenRouter extension routes OpenAI-style requests through OpenRouter’s many model providers. The Bedrock extension connects Amazon Bedrock Mantle models to the same common interface and defines their authentication and costs. The self-improvement extension uses this shared model doorway with extra limits and metering, so its experiments stay controlled and comparable.

## [Agent reasoning loop, tool dispatch, and subagent orchestration](stage-11.md) `stage-11` — 59 files

This stage is the agent’s main work loop for one user request. It is where a queued turn becomes real action: `engine.py` claims the turn, prepares the prompt, reads the model’s streamed decisions, runs requested tools, records costs, absorbs any new messages, and finishes, pauses, or fails safely.

The built-in tools are the basic workbench: they let the agent read and edit files, run commands, ask the user questions, manage artifacts, and keep a todo list. The sandbox, browser, and website tools add a safe workshop for running code and controlling web pages. External connector and research tools let the agent use web search and business services through protected adapters, so secrets are not exposed. Document and Office tools handle PDFs, Word, PowerPoint, and spreadsheets.

Subagents are helper workers. `subagents.py` starts, tracks, waits for, messages, or cancels child agent turns. Browser, research, and website delegation files package larger jobs into specialized child agents, so the main agent can split work and combine the results.

### [Built-in workspace, artifact, and conversation tools](stage-11.1.md) `stage-11.1` — 2 files

This stage gives the agent its built-in toolbox during a conversation. When the model decides it needs to inspect files, run a shell command, search the workspace, ask the user a question, or use a service, these tools turn that request into controlled action. It is part of the main work loop, but it also provides shared support that many tasks rely on.

The main file, `builtins.py`, is the bridge between the model and the outside working area. It defines tools for reading and editing files, running commands, sharing files, loading extra skills, collecting secrets safely, connecting accounts, and coordinating helper agents. In everyday terms, it is the agent’s workbench: each tool is a different instrument, and the file makes sure they are used through the project’s safe workspace and service rules.

The todo extension adds a simple checklist for longer requests. It lets the agent create tasks, mark progress, and keep that list tied to the current conversation, so work can continue clearly across later messages.

### [Sandboxed code, browser, and website automation](stage-11.2.md) `stage-11.2` — 21 files

This stage is the system’s safe workshop for doing things outside pure text chat. It is used during the main work loop, when the agent needs to run code, start a website, open a browser, inspect a page, or interact with it.

Sandboxed code and website execution provides the workbench. It can run commands in a limited project area, keep interactive Python or JavaScript sessions alive, and build or serve local websites so they can be tested.

Browser CDP session and lifecycle infrastructure is the engine room for Chrome. CDP, or Chrome DevTools Protocol, is Chrome’s remote-control channel. This part opens and manages the connection, tracks tabs, downloads, pop-ups, page loading, and safe page scripts.

Browser page inspection and content modeling gives the system eyes. It reads the live page, turns it into a compact description, and keeps hidden links to the exact buttons or fields.

Browser input, forms, and high-level tool actions gives the system hands. It turns requests like click, type, scroll, upload, and download into careful browser actions that land in the right place.

#### [Sandboxed code and website execution](stage-11.2.1.md) `stage-11.2.1` — 3 files

This stage is the workbench for running code and websites outside the browser. It supports the main work loop, when an agent needs to test an idea, run a command, keep a coding session alive, or start a local web app inside the sandbox workspace.

The local sandbox carrier runs commands directly on the host computer, but limits them to a chosen project folder. It is the lightweight option for development, useful when you want the same tool behavior and network routing as a sandbox without starting Docker or a remote sandbox.

The persistent REPL extension adds long-lived code sessions. A REPL is an interactive “try a line, see the result” programming tool. One tool runs JavaScript with Node.js, and another runs Python for Excel-style work. Because the session remembers earlier successful code, later attempts can build on it.

The site tools prepare and run web projects. They build the site, clear any old process using the needed port, start the server, and wait until it can actually be reached.

#### [Browser CDP session and lifecycle infrastructure](stage-11.2.2.md) `stage-11.2.2` — 9 files

This stage is the browser automation engine. It sits behind the tools that browse the web, fill forms, click buttons, upload files, and wait for results. It is used during each work turn: it opens a connection to Chrome only when needed, keeps it alive while actions run, then cleans it up afterward.

The backend is the front desk for one turn. It hands browser tools a usable browser surface. The session is the main control room: it owns the live Chrome connection and coordinates tabs, downloads, dialogs, page state, and user actions. The CDP pipe talks to Chrome DevTools Protocol, Chrome’s remote-control interface, by sending commands over a WebSocket and receiving replies and events. Tabs manages opening, closing, switching, and navigating pages. Runtime safely runs small JavaScript snippets inside pages and turns results into normal Python values. Downloads watches for files, waits for them, and prevents paused network requests from freezing pages. Dialogs quickly handles pop-ups. Settle decides when a page has finished enough to continue. Wire checks the raw JSON data coming back from Chrome.

#### [Browser page inspection and content modeling](stage-11.2.3.md) `stage-11.2.3` — 3 files

This stage is the system’s “eyes” for a live browser page. It is used during the main work loop, when the system needs to understand what is on a page before deciding what to click, type, or inspect next. It turns messy browser data into short, safe, model-friendly descriptions.

content.py provides the basic reading tools. It can pull plain text from a page, inspect page content, and search for elements. It also limits result size and avoids exposing raw browser details when other parts only need usable facts.

page.py builds the fuller page model. It reads the live page and creates a clean description of visible content and interactive items. At the same time, it keeps the browser’s internal element IDs, so a later step can act on the exact button, link, or input that was described.

find.py helps locate specific elements in the accessibility tree, which is the browser’s structured map of usable page parts. It cleans up matches so search results are consistent and safe to use.

#### [Browser input, forms, and high-level tool actions](stage-11.2.4.md) `stage-11.2.4` — 6 files

This stage is part of the system’s main work loop, when an agent is using a browser to get tasks done. It sits between the agent’s simple requests and Chrome’s detailed control interface. The tools file is the front counter: it defines actions the agent can ask for, such as opening a page, reading content, clicking, typing, uploading a file, or saving a download. Those requests then move into helpers that make them safe and precise. The computer file turns broad actions like “click” or “scroll” into browser commands. The coordinate file maps positions from what the AI sees in a screenshot to the real pixels in the browser window, so clicks land in the right place. The fixup file tidies common mistakes in model-made actions before they reach Chrome. The keys file converts human keyboard ideas, like “Ctrl+A”, into exact key events. The forms file handles filling fields and file uploads. Together, these pieces act like translators and proofreaders for browser control.

### [External connector, research, and business-tool execution](stage-11.3.md) `stage-11.3` — 12 files

This stage is the system’s “outside world” workbench. It is used during the main work loop when the assistant must search the web, call a business app, or use a connected account. The key idea is safety: outside services are reached through brokers and adapters, so tools can work without exposing raw passwords or tokens.

The generic connector tools are the front desk. They let the agent discover available tools, run them, and pass input or output files between the workspace and connector brokers. Composio support provides account consent, tool discovery, tool execution, file upload handling, MCP-style calls, and proxy HTTP calls where Composio holds the real provider secret. Pipedream support does a similar job for Pipedream actions. MCP support lets the agent list and call tools from workspace-configured MCP servers, which are services that publish callable tools.

Research tools route web search and page fetching through Exa or another selected provider. Slack tools help connect a workspace, create an app manifest, and search messages. YC tools safely talk to Y Combinator’s command-line system. The evaluation environment supplies fake email and calendar connectors for tests, so the same paths can run without contacting real services.

### [Document, Office, PDF, and skill-script execution](stage-11.4.md) `stage-11.4` — 19 files

This stage is a toolbox used during the agent’s main work or during human review, especially when the work involves office files and PDFs. It is not the system’s startup or shutdown; it is the set of helpers the agent calls when documents need to be opened, changed, checked, or marked up.

The document-review scripts keep the review organized. constants.py fixes the filenames for saved state and logs. models.py defines what a review issue looks like. manage_state.py moves the review through steps and records an audit trail. annotate_pdf.py, annotate_pptx.py, and annotate_xlsx.py then turn saved issues into visible comments in PDFs, PowerPoint slides, and Excel sheets.

The Office tools act like unpacking and repacking stations. DOCX and PPTX unpack scripts turn Word and PowerPoint files into editable folders of XML, while pack scripts rebuild them. Word comments, tracked-change acceptance, PowerPoint slide cleanup, contact sheets, and PPTX repair are handled by focused helpers. Spreadsheet recalculation uses LibreOffice in the background, with shared setup in _soffice.py. The PDF tools detect and fill real form fields, place text on non-fillable forms, and render pages as PNG images.

## [Source synchronization and page-change replay](stage-12.md) `stage-12` — 54 files

This stage is the system’s intake and replay line. It runs behind the scenes after a source is connected. Its job is to fetch records from outside tools, page through long result lists safely, save the raw content, notice what changed or was deleted, and then replay those changes to search indexing, alerts, and other follow-up work.

The connector groups are the many “front doors” to outside services. They cover communications and meetings, documents and workspaces, project and developer tools, CRM and support systems, marketing tools, HR and recruiting, and finance or commerce products. Each connector speaks one service’s web API, then translates that service’s records into the same internal shape.

The shared source files are the machinery behind those doors. The connector contract defines the rules every connector follows. The REST base supplies safe web requests, retries, and pagination. The backend converts connector records into stored sync results. The sync core stores raw bodies and records page changes or deletions, then replays them in order. Page alerts watch those changes and notify chats when watched pages match.

### [Communications, calendar, and meeting source connectors](stage-12.1.md) `stage-12.1` — 7 files

This stage is the set of read-only “connectors” that bring communication and meeting data into the system. It sits in the source-sync part of the system: the system reaches out to outside services, reads what changed, and turns it into standard records that can later be stored, searched, or recalled. It does not send messages or edit calendars.

Each file is a bridge to one service. Gmail reads mailbox changes and converts emails into plain searchable text. Outlook uses Microsoft Graph, Microsoft’s web API, to read mail, threads, contacts, calendar events, and folders as steady change streams. Google Calendar reads calendar events and separately records attendees, so the system knows both what happened and who was invited. Calendly reads scheduling data such as event types, groups, scheduled events, and invitees. Google Meet pulls transcripts and generated meeting notes as readable pages. Microsoft Teams reads teams, channels, chats, and messages. Slack reads users, channels, messages, threads, and senders. Together, these connectors act like intake clerks, translating many outside formats into one syncable shape.

### [Documents, knowledge bases, and structured workspace content connectors](stage-12.2.md) `stage-12.2` — 7 files

This stage is the system’s set of “adapters” for structured work content. It sits behind the scenes during syncing: when a user connects an outside service, these connectors know how to visit that service, read what is allowed, and turn it into source pages that the rest of the system can search and reuse.

Each connector speaks to a different tool. Airtable finds bases, tables, and records, then presents them as synced items. Google Sheets reads spreadsheets, tabs, and rows from a Google account. Notion gathers pages, blocks, databases, comments, and user details, then flattens them into readable text. Confluence does the same for spaces, pages, blog posts, comments, groups, and audit records, converting its stored web content into plain text. Google Docs reads documents only, without changing them. Google Drive covers a wider file space, including shared drives, permissions, comments, and revisions. The Y Combinator connector brings in selected YC guidance and directory-style results, such as companies, founders, jobs, and forum posts. Together, they turn many outside work libraries into one searchable memory.

### [Work management, development, and operations source connectors](stage-12.3.md) `stage-12.3` — 9 files

This stage is shared behind-the-scenes support for bringing outside work systems into the project’s memory. Each connector knows how to talk to one service’s web API, meaning the service’s online doorway for requesting data. Because these services return results in pages, like search results spread over many screens, the connectors keep asking for the next page and turn everything into a steady stream of standard records.

The Asana, ClickUp, Jira, Linear, monday.com, and Wrike connectors gather project-management data such as tasks, issues, projects, comments, users, boards, folders, and custom fields. ClickUp also has to walk a nested structure of teams, spaces, folders, lists, and tasks. The GitHub connector brings in engineering work: organizations, repositories, issues, commits, events, comments, and users. PagerDuty adds operations data such as services, incidents, schedules, notes, and on-call records. Sentry adds error-tracking data such as projects, issues, events, members, and releases. Together, these files act like adapters for different plug shapes, making many tools feed the same sync and search system.

### [CRM, sales, support, and customer-success source connectors](stage-12.4.md) `stage-12.4` — 6 files

This stage is part of the system’s data intake work. It sits behind the scenes when a sync runs, reaching out to customer-facing tools and reshaping their data into one common stream of records that the rest of the product can store, search, and recall.

Each file is like an adapter for a different outside service. The Attio connector reads companies, people, deals, tasks, notes, meetings, and call recordings, including transcripts when they exist. The HubSpot connector covers a wide range of CRM, marketing, analytics, conversation, list, association, custom-object, and deletion data. The Salesforce connector reads common sales records such as accounts, contacts, opportunities, and cases through Salesforce’s web API, meaning its online data access interface. Freshdesk brings in support tickets, contacts, agents, help articles, and forum content. Zendesk does the same for support, help-center, and community data, including comments and votes. Intercom reads conversations, contacts, companies, teams, tags, and activity logs. Together, these adapters turn many different service formats into the same sync-friendly shape.

### [Marketing, ads, social, and forms source connectors](stage-12.5.md) `stage-12.5` — 7 files

This stage is part of the system’s data intake work. It provides “source connectors,” which are small adapters that know how to talk to outside services, sign in, request data in pages, and reshape the answers into records the rest of the system can store.

Each file is one adapter for a marketing or audience tool. ActiveCampaign reads CRM and marketing collections while handling login and safe paging. Facebook Ads and Google Ads fetch advertising structures such as accounts, campaigns, ad groups or ad sets, ads, and performance metrics. Instagram reads Pages, linked business accounts, media, stories, and analytics through Meta’s API. Klaviyo covers a broad marketing storehouse, including profiles, lists, campaigns, events, catalog items, forms, and webhooks. Mailchimp reads audiences, subscribers, campaigns, reports, tags, segments, and email activity. Typeform brings in forms, responses, workspaces, themes, images, and webhooks.

Together, these files act like plug adapters for different outlets: each service has its own shape, but the system receives steady, named streams of records.

### [HR and recruiting source connectors](stage-12.6.md) `stage-12.6` — 6 files

This stage is the system’s set of “adapters” for HR and recruiting tools. It runs during data syncing, when the system reaches out to outside services, reads their records, and turns them into a common stream of data that the rest of the code can store, search, or process. Each connector knows the rules of one vendor’s web API, meaning the online doorway used to request data.

The Ashby connector reads recruiting records such as candidates, jobs, applications, interviews, and offers. The Greenhouse connector does the same for Greenhouse Harvest, covering many hiring endpoints page by page. The Recruitee connector brings in candidates, job offers, and departments from Recruitee lists. BambooHR focuses on employee and HR records. Deel reads workforce data such as contracts, forms, payslips, timesheets, and tasks. Rippling reads company, worker, and team data.

Together, these files act like translators at different service desks. Each speaks to one external system, handles its paging, and hands back orderly batches for the shared sync machinery.

### [Finance, billing, accounting, and commerce source connectors](stage-12.7.md) `stage-12.7` — 7 files

This stage is the system’s set of “adapters” for money-related services. It is used during source sync, when the system is pulling data in from outside products. Each connector knows how to talk to one service’s web API, which is the service’s online doorway for requesting data, and reshapes the answers into the common stream format the rest of the system understands.

Brex reads spend-management records such as transactions, expenses, users, vendors, budgets, and departments. Chargebee and Recurly bring in subscription-billing data like customers, subscriptions, invoices, items, and transactions. QuickBooks Online and Xero cover accounting records, including accounts, contacts, invoices, payments, and other bookkeeping objects. Square reads commerce data such as customers, payments, locations, catalog items, orders, refunds, and inventory counts. Stripe handles many payment and billing records, including customers, invoices, subscriptions, payments, and connected-account data.

Together, these files act like plug adapters: each one fits a different outside system, but all deliver records in the same shape for syncing.

## [Scheduled jobs, periodic maintenance, and autonomous follow-up](stage-13.md) `stage-13` — 13 files

This stage is the system’s “night shift.” It runs work that should happen outside a live user turn: scheduled prompts, delayed follow-ups, cleanup, syncing, and experiments to improve agents. At startup, jobs.py registers built-in and extension jobs as dependable workflows, while runtime_instance.py lets processes report that they are alive and cleans up work left behind by dead or cancelled processes.

Scheduling.py is the durable calendar inside each workspace. It stores tasks, lets workers claim them safely, and prevents two workers from doing the same job. candidates.py safely finds which workspaces have pending work, then makes sure the actual work happens inside the right workspace. The scheduled-tasks extension adds cron.py to validate repeating time rules, tools.py so users or agents can create recurring tasks or pause until a reply or timer, and runner.py to fire due tasks and advance repeats.

The self-improvement extension is a background learning loop. corpus.py gathers failed past conversations, proposer.py suggests prompt changes, replay.py retests old tasks safely, evaluation.py judges results, gate.py decides if evidence is strong enough, and cron.py runs this check over time.

## [Result commit, outbound delivery, cleanup, and shutdown](stage-14.md) `stage-14` — 2 files

This stage is the system’s “finish the job and tidy the room” step. It runs after a turn of work has reached an answer, failed, been parked for later, or been cancelled. Its job is to make the final state durable, send the last visible updates to users, make any produced files available, and clean up resources that were only needed during the turn.

The cancellation module gives the system one safe way to stop a turn. It first asks the durable workflow engine, the part that keeps long-running work reliable, to stop the running work. Then it records in the database that the turn was cancelled, so later readers see the correct final state.

The artifacts route is the file pickup counter. If a turn produced a shared file, this route lets someone download it only when they present a valid token, like a claim ticket. This works independently of chat, Slack, or other user interfaces, so artifact delivery stays available even if those surfaces are not installed.

## [Persistence, workspace objects, and blob storage](stage-15.md) `stage-15` · (cross-cutting) — 13 files

This stage is the system’s storage backbone. It is shared behind the scenes by startup, the main work loop, and admin flows whenever they need to remember something safely. The database map in tables.py defines the shared table layout. db.py is the guarded doorway that opens database connections, runs updates to the schema, and keeps each workspace’s data separated. gateway_store.py keeps temporary signup claims before a workspace exists.

Large files use a separate “blob” store through blob.py, so the rest of the code can save bytes without knowing whether they are on disk or in S3-style storage. transcript.py and loop/transcript.py define and safely update saved conversations, including compacted summaries.

The workspace object layer in objects.py turns stored records into named things people can list, inspect, change, or delete, with validation and permission checks. Specific object types plug into it: agents, shared artifacts, connected accounts, synced source pages, external content sources, and user-created skills. Together, these pieces act like labeled shelves and locked cabinets for the project’s durable data.

## [Public SDK, protocol types, and extension contracts](stage-16.md) `stage-16` · (cross-cutting) — 34 files

This stage is shared behind-the-scenes support. It defines the public “contracts” that let extensions, connectors, models, browsers, search services, and user-facing screens work with UFO without depending on private internals. Think of it as the set of plugs, sockets, and label formats that let replaceable parts fit together.

The extension declaration and handler SDK contracts describe what an extension can offer, such as tools, routes, jobs, credentials, skills, and lifecycle hooks, plus the safe context and HTTP helpers its code may use. The backend and connector SDK doorways expose stable entry points for auth proxies, browsers, sources, search indexes, models, sandboxes, and connectors. The platform service import shims provide approved public paths to services like accounting, grants, memory, logging, seats, and operator authentication. Extension-specific protocol shapes define exact request and event formats for browser actions, browser-related errors, and memory events.

The directly assigned files add core shared shapes: browser connection requests, AI model messages and streams, turn status and result records, web search requests, and memory ownership labels. Together, these contracts keep the system flexible while making communication predictable.

### [Extension declaration and handler SDK contracts](stage-16.1.md) `stage-16.1` — 9 files

This stage is shared behind-the-scenes support for people who build extensions. It defines the public “contract” between an extension and the UFO core: what an extension can declare, and what kinds of objects its code is allowed to use when UFO calls it.

The main declaration piece is `ext/manifest.py`. It describes the extension “menu”: tools, web routes, background jobs, credentials, model and search backends, skills, schedules, surfaces, and startup or shutdown hooks. The SDK files then provide stable front doors for extension authors. `sdk/manifest.py`, `sdk/jobs.py`, `sdk/scheduling.py`, `sdk/skills.py`, `sdk/surfaces.py`, and `sdk/tools.py` re-export approved types so outside code does not rely on private internal paths. `sdk/context.py` exposes the context objects passed into handlers, which are like the work order and toolbox for a request. `sdk/http.py` supplies safe request, response, and session-cookie helpers, keeping web behavior and security rules consistent. Together, these files make extensions predictable to load, call, and maintain.

### [SDK backend and connector integration doorways](stage-16.2.md) `stage-16.2` — 8 files

This stage is shared support for people who extend the system. It is not where the main work happens. Instead, it provides “front doors” into the codebase: stable import paths that extension authors can rely on, even if the internal folders change later.

Each file opens one door to a different kind of plug-in point. authproxy.py exposes the approved authentication-proxy types, so connectors can receive credentials safely. browser.py exposes browser connection types without revealing the engine’s private browser code. connectors.py gathers the pieces needed to add connector providers, including OAuth, which is the common web sign-in handoff. sources.py gathers tools for adding content sources, including syncing, REST helpers, pagination, and sync results. index.py exposes the types needed to plug in search indexes and embedding backends. search.py re-exports the public search interface for tools that need to ask questions over indexed content. models.py collects model message, client, usage, and helper types. sandbox.py exposes controlled execution tools. Together, these files act like labeled sockets where outside integrations can plug in cleanly.

### [SDK platform service import shims](stage-16.3.md) `stage-16.3` — 9 files

This stage is shared behind-the-scenes support for people who build on top of the SDK. These files are “import shims”: small doorway modules that give users a stable, public path to approved tools, while hiding where the real code lives inside the project. They do not do the main work themselves. They make the system easier and safer to use from extensions and outside code.

The accounting shim exposes accounting and spending summary objects. The bearer shim exposes only token verification helpers, so extensions can check bearer tokens without gaining access to token creation secrets. The grants shim opens access to grant audit summaries and the function that produces them. The hub shim re-exports hub-related types. The memory shim exposes memory-search types. The observability shim, named o11y, gives access to structured logging, meaning logs with consistent fields machines can read. The objects shim exposes approved object tools. The operator shim provides public access to operator web-session authentication helpers. The seats shim re-exports seat classes and helper functions. Together, these files form a tidy public counter in front of deeper internal shelves.

### [Extension-specific protocol shapes](stage-16.4.md) `stage-16.4` — 3 files

This stage defines the “message shapes” used by specific extensions, rather than by the core system. It is shared behind-the-scenes support: other code relies on these definitions when it starts asking an extension to do work or when it reads events coming back.

The browser actions file is like a form book for browser control. It lists the actions an automated agent may request, such as click, type, scroll, wait, or take a screenshot, and describes what information each request must contain. That lets the system check a request before trying to drive the browser.

The browser errors file defines one special failure case: the AI model pointed to something in the browser that does not really exist, such as an invented button or page element. Naming this error separately helps the system respond to bad model output without confusing it with other problems.

The memory events file gives common event names and limits for memory recall. Together, these files keep extension communication predictable and easy to validate.

## [Security boundaries, secrets, grants, and policy enforcement](stage-17.md) `stage-17` · (cross-cutting) — 16 files

This stage is shared safety plumbing, used during startup, normal work, and operator access. It is like the locks, badges, and guarded doors in a building. Bearer, gateway, and artifact token code creates signed short-lived “passes” that prove a member, hosted user, or file download is allowed. Operator session code checks special admin-style tools and limits which workspace they may view. Workspace context and database row-level security keep every request tied to the right workspace, so one workspace cannot read another’s rows.

Sandbox safety is handled by a locked workspace folder, short-lived storage credentials, and an outgoing web proxy. The sandbox session code blocks access to private transcripts and bookkeeping files. Proxy rules decide which websites may be contacted, which requests need broker forwarding, and how usage is counted; the proxy server enforces those rules.

Credential code seals secrets, shows only safe credential status, and unseals values only in trusted paths. Connectors, direct source access, and Pipedream proxy support outside providers without leaking provider tokens. Governance adds a final policy gate by requiring proposed prompt changes to be approved before they take effect.

## [Observability, accounting, spend limits, and billing integration](stage-18.md) `stage-18` · (cross-cutting) — 4 files

This stage is shared behind-the-scenes support that keeps the system measurable, billable, and safe from runaway spending. It is not one step in the main chat loop; it watches and records work across the whole system, especially when the agent uses models or serves a workspace.

The observability toolbox records what happened as logs, metrics, and traces. A trace is a step-by-step record of a request. Before details leave the process, it strips sensitive data so operators can debug problems without exposing private content.

The accounting center turns model usage, such as tokens, into dollars. It writes those costs into a ledger, checks spend limits, and prepares exports for outside billing systems. This is the cash register and spending guardrail.

The seats code decides who in a workspace the agent may answer, based on grants, automatic rules, and billing limits. The Metronome integration connects those seats and usage numbers to the external billing service, and gives workspace owners chat tools to grant, revoke, and inspect seats. Together, these pieces make usage visible, chargeable, and controlled.

## [Configuration, adapters, utilities, and conformance scaffolding](stage-19.md) `stage-19` · (cross-cutting) — 53 files

This stage is shared support used across the whole system, especially during startup and testing. It is the wiring and labeling layer that tells UFO what settings to use, what outside services it can talk to, and where different extension code lives.

The configuration file loader reads ufo.toml and checks that important choices are clear and safe before the program runs. The bundle builder creates a repeatable Docker deployment package, freezing which extensions and settings should be used on another machine. Adapter manifests and registries act like a front-desk directory, mapping provider names to the code for Redis hubs, source connectors, models, search, sandbox, browser, and related services.

The evaluation pack definitions provide safe, repeatable tool bundles for tests, replacing real services with controlled fake ones when needed. The sample extension and pack scaffolding proves that the public SDK can load tools, skills, routes, hooks, connectors, and setup actions correctly. The many package marker files are small but important: they make folders importable in Python, like labels on drawers, so later stages can find core code, platform integrations, and user-facing extensions.

### [Adapter manifests and provider registries](stage-19.1.md) `stage-19.1` — 2 files

This stage is shared startup support. It does not move data itself. Instead, it tells UFO what outside systems it can connect to and which piece of code should be used for each choice. It is like a directory at the front desk: when the configuration says “use this provider,” UFO looks here to find the right worker.

The Redis hub manifest announces that a Redis-backed frame hub is available. A frame hub is the place UFO uses to pass units of work or data frames between parts of the system. If the user selects Redis in the configuration, this manifest lets UFO create the correct Redis hub object instead of some other hub.

The source connector registry is the address book for input sources. It links short backend names, such as Slack or Stripe, to the Python classes that know how to read from those services. Together, these files make external adapters discoverable, so the rest of UFO can stay generic and choose the right connector or hub at startup.

### [Evaluation pack definitions](stage-19.2.md) `stage-19.2` — 3 files

This stage defines special “packs” used when the system is run for evaluation instead of normal real-world use. A pack is a named bundle of tools and abilities that the UFO system loads at the start of a run. Here, the goal is to make tests repeatable and safe, so the system gets controlled tools instead of unpredictable outside services.

The assistant evaluation file builds an evaluation version of the normal assistant setup. It keeps the usual assistant tools, adds fake email and calendar-style tools for testing, and removes real broker connections that would not behave reliably in an evaluation.

The DSQA evaluation file offers three preset bundles: a basic core setup, one with search, and one with browser access. These let evaluators choose how much outside information access the run should have.

The GDPVal evaluation file works the same way, but for GDPVal tasks. It provides core, document-focused, research-focused, and all-in-one packs. Together, these files act like a menu of safe, repeatable test configurations.

### [Sample extension and pack conformance scaffolding](stage-19.3.md) `stage-19.3` — 3 files

This stage is a proving ground for the public SDK, which is the supported toolkit outsiders use to add features. It is not part of the main product work loop. It is behind-the-scenes scaffolding used during development, testing, and startup checks to make sure extensions, packs, skills, and loading rules still work together.

The sample extension is the main “demo machine.” It uses only the public ufo.sdk package and tries many extension points: tools, background jobs, web routes, hooks, object types, connectors, search, model, sandbox, browser, and user-facing surface features. This shows that an extension can plug into all these places without private shortcuts.

The sample pack wraps that extension into a pack, which is a bundle that can install related pieces together. It also adds a skill and an onboarding action that writes a real stored record, proving packs can do useful setup work.

The skill probe is the smallest check: run it, and it prints success. Together, these files act like test plugs that confirm the whole extension-and-pack path is wired correctly.

### [Core, control, and pack import package markers](stage-19.4.md) `stage-19.4` — 15 files

This stage is quiet behind-the-scenes support. It does not start the program, run the main loop, or shut anything down. Instead, it sets up the “addresses” Python uses to find code. In Python, an __init__.py file marks a folder as a package, meaning other code can import from it by name.

The root markers create the main import doors: ufo_control for control code, ufo for the core system, and ufo_pack_yc for the YC pack. Inside the core tree, more markers divide the project into stable neighborhoods. ufo.ext is for extensions. ufo.loop and ufo.loop.prompts support the main loop and its prompt text. ufo.models holds model-related code. ufo.sandbox and ufo.sandbox.proxy mark isolated execution and proxy areas. ufo.schema, ufo.sdk, ufo.skills, ufo.sources, and ufo.surfaces mark their own import areas. ufo.tools also names the tools area and briefly says it contains the tool registry, handler context, and built-in tools.

### [User-facing capability extension package markers](stage-19.5.md) `stage-19.5` — 16 files

This stage is shared behind-the-scenes support. It does not run the main product by itself. Instead, it puts “front doors” on many extension folders so Python can recognize and import them as packages. A package is simply a folder that Python treats as a named bundle of code.

Most files here are __init__.py markers. The browser marker identifies the browser extension and points to browser, computer-use, and browser-agent tools. Its bua marker opens a smaller browser-use area for imports. Coding, documents, knowledge graph, research, sites, Slack, UFO, web, and YC each have similar markers so their tools can be found by the rest of the system. The documents extension also marks script folders for document review, PowerPoint, and spreadsheet skills, making those helper scripts importable. The memory marker also describes its role: storing long-term facts, recalling them during prompts, updating memory from pages, and indexing memory. The skill-create marker describes support for making and exposing user-created skills. Together, these files act like labels on tool drawers, letting later stages open the right drawer when needed.

### [Platform and integration extension package markers](stage-19.6.md) `stage-19.6` — 12 files

This stage is shared behind-the-scenes support for the extension system. It does not run the app, process messages, or shut anything down. Instead, these files are small “door signs” for Python. In Python, an __init__.py file tells the language that a folder is a package, meaning other code can import files from it.

Each marker gives a stable import home to a different extension area. brief_pipeline labels the brief pipeline extension. composio, pipedream, redis_hub, connectors, sources, page_alerts, scheduled_tasks, debugger, and repl make their matching extension folders available to the rest of the project. Their real behavior lives in nearby modules, not in these marker files.

Two markers also summarize more clearly what their extension is for. eval_env points to fake mailbox and calendar connectors used for repeatable testing. self_improvement points to offline review tools that suggest prompt changes for a human to approve. Together, these files act like labeled shelves in a workshop: they organize the tools so the system can find them when needed.
