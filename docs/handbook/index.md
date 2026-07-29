# System Handbook — Stage Index

Each stage below links to its full page; the paragraph is the stage's role in the system.

## [Schema migration and database bootstrapping](stage-1.md) `stage-1` — 80 files

This stage happens before the system starts doing its normal work. It prepares the database, which is the system’s long-term filing cabinet, so later services can safely read and write stored state. It uses Alembic, a migration tool that applies database changes in order, like following numbered renovation instructions.

Core database migrations build and evolve the main filing cabinets for workspaces, users, agents, conversations, turns, messages, permissions, billing, scheduling, connections, memory, and other shared records. Extension database migrations do the same for add-on features, such as evaluation test data, searchable indexes, sample tables, memories, and user-created skills.

The direct source files provide the machinery around those migrations. The database helper opens safe connections, starts transactions tied to the right workspace, and runs upgrades. The schema setup for the control gateway checks that required tables exist before traffic is served. The row-level security setup adds PostgreSQL rules that keep one workspace from seeing another workspace’s rows. Together, these pieces make sure the database is ready, current, and safely separated before runtime code depends on it.

### [Core database migrations](stage-1.1.md) `stage-1.1` — 59 files

This stage is the database’s instruction manual and upgrade path. It works mostly during setup and system upgrades, not during the main conversation loop. Its job is to make sure the platform’s stored data has the right shape before other code depends on it.

The schema definitions describe the shared “turn” records and the database tables, then connect them to Alembic, the tool that applies database changes in order. The foundational migrations create the first filing cabinets: workspaces, members, agents, conversations, turns, credentials, proposals, and extension data. Conversation and inbound-delivery migrations add ways to store messages from Slack, the web, and other entry points, including pending replies and shared artifacts.

Other groups expand what the system can remember. Turn and runtime migrations track retries, subagent links, ownership, worker processes, and durable sandboxes. Scheduling migrations store recurring background tasks and their lifecycle. Billing migrations add ledgers, spend limits, exports, and seat counts. Source and memory migrations store synced content and clean up old knowledge data. Grant, connection, agent, and workspace-control migrations record permissions, external accounts, internet access, and workspace defaults.

#### [Schema definitions and Alembic wiring](stage-1.1.1.md) `stage-1.1.1` — 3 files

This stage is the system’s shared blueprint for stored data. It sits behind the scenes, especially during setup and upgrades, so every part of the project agrees on what a “turn” is and how turns are saved in the database.

The records.py file defines the in-memory shape of a turn, meaning the standard package of information passed between parts of the system when a user or system asks the assistant to do work. It gives turns stable identifiers, status values, and safety checks so one part of the code does not misread what another part produced.

The tables.py file describes the database version of that same world. It uses SQLAlchemy, a Python tool for describing databases in code, to list tables, columns, and rules for valid stored data.

The env.py file connects those table definitions to Alembic, the migration tool. When the database needs to be created or changed, it opens the connection and tells Alembic what schema to apply.

#### [Foundational platform tables and early extensions](stage-1.1.2.md) `stage-1.1.2` — 5 files

This stage is part of the system’s first startup setup. It builds the earliest database tables, which are the shared filing cabinets the rest of the platform depends on. Each migration is a small reversible database change, so the system can move forward or roll back safely.

The first migration lays the foundation: workspaces, members, agents, conversations, turns in those conversations, and usage cost records. This gives the platform a basic place to record who is working, what agent is involved, and what happened. The credentials migration adds a secure place to store workspace-level secrets or access details. The proposal migration adds a table for suggested changes, including who proposed them, what they would change, and whether they are still pending, approved, or rejected. The loop-depth migration lets one turn belong inside another turn, and lets conversations be run by subagents, not just from the command line. Finally, the extension store gives add-ons a simple per-workspace place to save small JSON-shaped data. Together, these tables form the platform’s first working skeleton.

#### [Conversation surfaces and inbound delivery migrations](stage-1.1.3.md) `stage-1.1.3` — 8 files

This stage is behind-the-scenes database preparation for conversations that arrive from outside places such as Slack and the web. A database migration is a controlled change to the database layout, like adding labeled drawers before the system can store new kinds of records.

The early migrations add Slack and then web as valid “surfaces,” meaning entry points where a conversation can happen. They also add information needed to send replies back to the right place after the system finishes a turn. Later, the surface seam migration creates storage for shared artifacts, such as files tied to a conversation turn. Workspace keys and surface installations then make delivery more precise, so Slack workspaces or other installed surfaces do not get mixed together, and pending replies can be found quickly.

The inbound-message migrations add a proper inbox table for messages waiting to be processed, with ordering, links, and duplicate protection. Rendered inbound content is then split into its own storage and the old field is removed. Finally, conversations gain an audience field, recording who should be able to see them.

#### [Turn execution, context, and runtime fleet migrations](stage-1.1.4.md) `stage-1.1.4` — 11 files

This stage is behind-the-scenes upgrade work for the database, the system’s long-term memory. It changes what information can be stored about turns, conversations, jobs, and runtime processes so newer engine behavior can work safely.

Several migrations improve turn execution. One adds fields that show which attempt is running a turn and whether resume has already been queued. Others add trace links for following subagent work, extra context such as sender or timezone, speaker and connect-authorization details, parent-turn lookup speed, and “on behalf of” ownership links. Together, these make each turn easier to resume, audit, connect to its relatives, and credit to the right person.

Other migrations support the runtime fleet, meaning the pool of running worker processes. They create a runtime-instance table, allow shared fleet instances that are not tied to one workspace, and later remove old shared-fleet columns.

Conversation and job support are upgraded too. Conversations can store a sandbox handle so they can reconnect to the same durable environment. Job-candidate indexes act like shortcuts, helping background sweeps find relevant work without searching every row.

#### [Scheduling and background task lifecycle migrations](stage-1.1.5.md) `stage-1.1.5` — 6 files

This stage is behind-the-scenes support for background work. It is not the work loop itself; it prepares the database so the rest of the system can safely remember and manage scheduled tasks over time. A database migration is a small upgrade script that changes what information the database can store, and usually how to undo that change.

The first migration creates the basic scheduled-task storage: what recurring job exists, when it should run next, and which worker has claimed it for the moment. Later migrations add specific pieces to that record. One adds support for scheduled pauses, so pauses can be stored as planned future events. Another lets a turn be marked as admitted because a schedule triggered it, not only because a person or internal process did. Another records the last turn where a scheduled task fired, like a bookmark. A later change adds an expiration time, so old scheduled tasks can stop being valid. The final migration adjusts task identity so different agents in the same workspace can use the same task name without colliding.

#### [Billing, ledger, spend, and seat migrations](stage-1.1.6.md) `stage-1.1.6` — 9 files

This stage is behind-the-scenes upgrade work for the database. It does not run the main product loop itself. Instead, it changes the stored data structures so billing, spending, exports, and seat counts can be recorded safely as the system grows.

The spend cap migration adds a place to store spending limits for a workspace, member, or agent. It also lets a work “turn” be marked as parked, meaning paused because a limit was hit. Several ledger migrations expand what the ledger can describe. The ledger is the system’s money and usage log. It learns new entry types such as egress, sandbox tokens, and price digests, and it becomes able to store workspace-level charges that are not tied to one turn. Export migrations add a table that tracks which ledger records have been sent to outside systems, then mark whether an export used BYOK, meaning “bring your own key.” The seat migrations add fields for paid or limited member seats, workspace seat caps, and included seats, so workspace membership can be counted for billing.

#### [Sources, pages, and memory-data migrations](stage-1.1.7.md) `stage-1.1.7` — 11 files

This stage is part of the behind-the-scenes upgrade path for the database. A database migration is a small step that changes stored data safely as the application grows. Here, the system learns how to store content sources, the pages collected from them, and older memory-style data.

The first migration creates source and page records, so the app can remember where content came from, when to sync it, and which workspace owns each page. Later steps make sources more flexible: new backend types can be added, repeated errors can be counted, removed sources can be kept as “soft deleted” rows, and each source can be tied to a subject and optional member owner. Other steps improve pages: they add browsing details like title and stream, rename timestamps to clearer record-based names, and replace update-time ordering with a per-workspace revision number so clients can follow changes reliably.

The remaining migrations clean up old storage. One removes obsolete page alert data. One creates the early knowledge graph tables for things and links, while a later one removes those old graph tables from the main schema.

#### [Grants, connections, agents, and workspace control migrations](stage-1.1.8.md) `stage-1.1.8` — 6 files

This stage is behind-the-scenes setup work for the database. It runs when the system is upgraded, so older stored data can support newer features without losing meaning. The pieces act like remodels to the same filing cabinet.

First, 0014 creates the grant table, where the system can record that an agent has permission to use an external account in a workspace, and who approved it. 0043 adds a shared flag to those grants, so a permission can be marked as shared rather than only personal. 0050 adds an internet access setting to agents, making web use an explicit per-agent choice. 0051 then ties surface installations and conversations to a specific agent, filling old records with a sensible default before making the link required.

0056 adds workspace “control” choices: the admin member and main agent for that workspace, with a rule that there is only one main agent. Finally, 0057 refines the earlier grant model by separating reusable account connections from an agent’s permission to use them, and updates sources to point to the right connection.

### [Extension database migrations](stage-1.2.md) `stage-1.2` — 18 files

This stage is behind-the-scenes setup for extensions. A migration is a small, ordered database change, run when the system is installed or upgraded, and sometimes undone during rollback. The evaluation environment migration builds test email and calendar tables. The default index migrations create storage for searchable text chunks, including keyword and meaning-based search, then make chunk IDs safe by tying them to a workspace. The memory migrations build the memory tables and memory pages, add memory type and confidence, connect pages and memories to workspaces, add fast lookup indexes, record “as of” times, copy old time data forward, link memories back to their source pages and revisions, allow room-based audiences, and finally let one memory point to multiple source pages. The sample extension adds a simple per-workspace note table, useful as a model. The skill creation migrations create storage for user-made skills, then tighten ownership so each skill belongs to a specific agent. Together these files shape the database so each extension has the storage it needs.

## [Configuration, pack selection, and extension inventory](stage-2.md) `stage-2` — 60 files

This stage is startup preparation. Before UFO can answer requests, it decides what kind of deployment it is, what add-ons are allowed, and what services are available. The pack manifest selection is the first recipe choice: it picks a pack, such as a local assistant, hosted assistant, evaluation setup, or specialized workspace, and uses it to choose the right extensions, skills, and infrastructure.

Extension manifest loading then reads each extension’s “identity card.” These manifests declare what the extension contributes, such as tools, web pages, login routes, scheduled jobs, skills, storage backends, or helper agents. Model and backend registration builds shared catalogs for AI models and data services, so later code can look up costs, credentials, limits, and connection details in one place.

The direct files tie this together. config.py reads and checks ufo.toml, failing early if the deployment is unsafe or incomplete. store.py manages which extensions are actually installed by pinning or removing them. bundle.py freezes the chosen configuration and extensions into a deployable folder, so the same setup can run elsewhere.

### [Pack manifest selection](stage-2.1.md) `stage-2.1` — 10 files

Pack manifest selection is behind-the-scenes setup work. Before the system can run an assistant, an evaluation, or a specialized workspace, it must know which “pack” to load. A pack is like a recipe card: it names the extensions, skills, onboarding steps, and infrastructure choices that should be turned on together.

The local assistant pack is the full developer recipe for the normal assistant experience. The hosted assistant pack selects similar assistant features but points them toward managed cloud services. The billing pack lets developers test the hosted billing flow locally. The assistant evaluation pack replaces real outside integrations with fake ones so tests are repeatable.

Other manifests define specialized bundles. The chief of staff pack loads manager-support skills. The DSQA and GDPVal evaluation packs offer several preset tool combinations, from small core setups to fuller search, browser, document, or research setups. The sample pack is a simple proof that extensions, skills, and onboarding actions can be bundled correctly. The YC package marker makes the YC pack importable, while its manifest describes the founder-focused extensions and skill folders.

### [Extension manifest loading](stage-2.2.md) `stage-2.2` — 40 files

This stage is part of startup and shared behind-the-scenes support. Its job is to find optional extension packages and read their manifests, which are small “identity cards” that say what each package adds. The core loader is the gatekeeper: it checks which extensions are present and allowed, then turns their declarations into usable pieces such as tools, web pages, helper agents, credentials, skills, scheduled jobs, and data backends.

The core runtime declarations add surfaces like the debugger, web UI, and UFO shell. Agent workflow packages add work modes for browsing, coding, research, and writing pipelines. Content and business packages add document skills, website-building support, and YC-focused resources. Connector packages announce links to outside services such as Slack, Composio, Pipedream, and synced content sources, including login routes and required secrets. Automation and memory packages add remembering, scheduling, pausing, self-review, and skill creation. The many __init__.py files mostly mark folders as importable Python packages, while the manifest.py files provide the real catalog entries the system uses.

#### [Core loader and runtime surface declarations](stage-2.2.1.md) `stage-2.2.1` — 10 files

This stage is part of startup and shared support. It is where the system discovers optional extensions and learns what user-facing surfaces they add. The main worker is core/src/ufo/ext/loader.py. It acts like a gatekeeper at a building entrance: it finds installed extensions, checks whether each one is allowed, then converts its declared features into things the rest of UFO can use, such as tools, hooks, skills, credentials, and backends.

The manifest files are the extension “identity cards.” The debugger manifest names the debugger extension and says which debugger web surface to mount. The UFO manifest exposes the live UFO shell surface. The web manifest declares the web surface, its routes, and which tools the web side may call.

The __init__.py files for debugger, eval_env, redis_hub, repl, ufo, and web mostly serve as import markers. They tell Python that these folders are usable packages. Some add a short label, but they do not run the extension logic themselves.

#### [Agent workflow extension declarations](stage-2.2.2.md) `stage-2.2.2` — 8 files

This stage is shared startup support. It does not do the browsing, coding, writing, or research itself. Instead, it tells the main UFO system what extra abilities are available, like labels and menu cards placed on toolboxes before work begins.

Each extension has a small __init__.py file that marks its folder as a Python package, meaning other Python code can import it. These files are mostly front doors: the brief pipeline and browser ones also give a human-readable label, while the coding and research ones simply make the folders importable.

The manifest.py files do the real declaring. The brief pipeline manifest offers a writing workflow made of three subagent stages, moving from outline to draft to critique, plus a skill folder. The browser manifest adds browser and computer-use tools, a browser-focused helper agent, and prompt instructions. The coding manifest declares repository-working tools, skills, a coding subagent, and GitHub credentials. The research manifest registers web research tools, helper agent profiles, prompt text, and optional skills. Together, these files let the host system discover and wire in each extension cleanly.

#### [Content and business extension declarations](stage-2.2.3.md) `stage-2.2.3` — 6 files

This stage is shared startup support for optional feature bundles, called extensions. An extension is a folder of extra abilities that the main UFO system can discover and load when needed. The three empty __init__.py files act like “this is a usable box” labels for Python. They mark the documents, sites, and YC folders as importable packages, but they do not do any work themselves.

The real declarations are the manifest.py files. The documents manifest tells the system which document-focused skills are available, such as help for Word, PowerPoint, PDFs, spreadsheets, reviews, and design themes. The sites manifest advertises the website-building extension, including its tools, prompts, skills, and the subagent profile used for site work. The YC manifest describes a broader business bundle: tools, credentials, searchable source material, onboarding steps, and skill instructions for YC-oriented tasks. Together, these files are like catalog cards, letting the core system know what extra parts exist before it tries to use them.

#### [Connector and external service declarations](stage-2.2.4.md) `stage-2.2.4` — 10 files

This stage is the signpost layer for external integrations. It is mostly used when the application starts up or loads extensions. Instead of doing the integration work itself, it tells the main UFO system what extra abilities are available and where to find them.

Each extension has a small __init__.py file. These files are like labels on folders: they make the folder importable as a Python package, but they do not run connector behavior. The real declarations live in the manifest.py files. The Composio manifest advertises Composio-backed connectors, account connection settings, and an OAuth login route, which is a web path used to let users safely sign in to another service. The general connectors manifest declares connector tools, related objects, and prompt text to add when enabled. The Pipedream manifest registers Pipedream connectors, its OAuth route, and a broker that runs actions while protecting secrets. The Slack manifest declares Slack routes, required secrets, setup steps, and messaging tools. The sources manifest registers synced content sources, credentials, direct authentication, page objects, and change notifications. Together, these files let the host discover integrations cleanly.

#### [Automation, memory, and self-improvement packages](stage-2.2.5.md) `stage-2.2.5` — 6 files

This stage is shared behind-the-scenes support for optional extensions. These extensions let the system remember useful facts, run work on a schedule, pause until later, review its own past work, and create new skills while it is running. The package files are like labels on drawers: each __init__.py tells Python that a folder is an importable package and gives a short statement of what that extension is for. The memory package label describes saved facts, recall during user prompts, page-based updates, and a background memory indexing job. The scheduled-tasks package label simply makes that extension loadable. Its manifest is the real instruction sheet: it tells the platform about scheduled task objects, a pause-and-wait tool, a recurring runner job, and agent skills. The self-improvement package label explains that it reviews past workspace activity offline and proposes prompt changes for human approval. Its manifest registers the scheduled evaluation job and defines what happens when it runs. The skill-creation package label introduces runtime agent-made skills.

### [Model and backend registration](stage-2.3.md) `stage-2.3` — 7 files

This stage is part of startup and shared setup. It builds the “catalogs” the rest of UFO consults when it needs a model or storage backend, like a phone book for services. The model specification file defines the standard information every AI model record must contain: name, provider, cost, limits, and special abilities. The built-in catalog fills that format with Anthropic and OpenAI models. The registry then gathers these records and gives other code one central place to look up pricing, required keys, and how to create a client connection.

Extensions add more entries to the same phone book. The Bedrock adapter registers Amazon-hosted models and their credentials. The OpenAI embedding extension registers a service that turns text into numeric vectors for meaning-based search. The Turbopuffer extension registers an index service that can store chunks and search them by meaning or by keywords. The Redis hub manifest tells UFO how to create a Redis-backed hub when that backend is selected.

## [Process startup, service lifespan, and fleet presence](stage-3.md) `stage-3` — 6 files

This stage covers how UFO comes to life, stays visible while running, and cleans up after trouble. It is mostly startup and behind-the-scenes support. The HTTP application startup part is the main “open the shop” step. Command-line tools let a person create or inspect a workspace and start the service. The server builder reads settings, connects shared pieces like the database, credentials, extensions, sandbox access, and background workers, then opens the web app and attaches the routes that receive requests.

The hosted control service has its own command-line front door in control/src/ufo_control/main.py. It starts the gateway web server and can also run admin jobs such as setting up the database, creating invites, retrying Slack deliveries, and preparing row-level security, which means database rules that limit which rows each user may access.

Runtime supervision is the “watchman” after startup. It records live processes in fleet state, cancels work safely, recovers work left behind by dead processes, and starts observability: logs, metrics, and traces that explain what happened without leaking secrets.

### [HTTP application startup](stage-3.1.md) `stage-3.1` — 2 files

This stage is the system’s front door for starting the HTTP application, the web service that other tools and browsers talk to. It happens during startup, before the main request-handling work begins. The command-line file, core/src/ufo/cli.py, provides ufoctl, a human-friendly control panel. A user can use it to create or inspect a workspace, run the service, contact it, add extensions, or package it.

The main builder is core/src/ufo/serve.py. It reads settings, prepares shared pieces such as the database, stored credentials, extension support, sandbox access, and background workers, then starts the web server. During this assembly, the application lifespan code in the control service runs startup and shutdown setup, like opening and later closing a shop. Routes are registered so incoming web requests know where to go: surfaces, OAuth sign-in callbacks, artifact downloads, operator tools, mounted extension pages, and the sandbox proxy. Together, these parts turn local configuration into a running UFO service ready to receive HTTP requests.

### [Runtime process supervision](stage-3.2.md) `stage-3.2` — 3 files

This stage is shared behind-the-scenes support for the running system. Its job is to make sure server processes can be seen, stopped safely, and cleaned up if they disappear. It is like the control room for work that is already in progress.

runtime_instance.py keeps each live server process registered so the rest of the fleet knows it exists. It also runs background checks that look for work owned by processes that have died. When it finds such work, it helps recover it so jobs do not stay stuck forever. It also spreads a cancellation from a parent turn to any child work that was started under it.

cancellation.py defines the safe way to cancel one turn of work. A “turn” is one unit of activity in a workflow. It first tells the running workflow to stop, then marks the database record as cancelled, so the stored state matches what actually happened.

o11y.py sets up observability: traces, metrics, and structured logs that explain what the system did. It also redacts sensitive text so secrets are not sent to monitoring tools.

## [Hosted onboarding and workspace provisioning](stage-4.md) `stage-4` — 10 files

This stage is the front door for hosted UFO. It runs during signup and first launch, before the normal workspace work begins. The gateway web server leads a new person through the path: prove an email address, check any invitation, find or create the right workspace, and return the token and workspace address the client will use next. The terminal and browser share the same conversation, but gateway_directives turns server instructions into small messages for the terminal, while gateway_web turns them into browser-friendly JSON.

Several parts protect the signup flow. gateway_claim creates short-lived email codes, stores only a safe hashed copy, and limits retries. gateway_invite tracks one-use invitations for company domains. gateway_email checks that addresses look like work emails and sends the verification and invite messages. gateway_store keeps the claim records in Postgres, the main database. gateway_shared maps each company domain to one shared workspace, creating it only when needed. gateway_token issues the temporary access token. Finally, core onboarding performs first-run setup inside the workspace, creating the first admin, the main assistant, and any extension-provided setup steps.

## [Surface ingress and conversation entry](stage-5.md) `stage-5` — 9 files

This stage is the system’s front gate for anything that starts or enters a conversation. It is used both in the main work loop, when members send messages or press buttons, and during setup, when members connect outside services. Its job is to take many different kinds of incoming requests and turn them into the same internal shape: a known workspace member, a conversation, a message, or an interaction.

The live user surfaces are the everyday doors. Slack verifies Slack signatures, the web portal checks signed-in browser sessions, the terminal accepts command-line posts, and the debugger lets operators inspect activity. All of them pass their cleaned-up requests to the shared surface entry point, which safely creates conversations, submits messages, and returns replies.

The OAuth and connection callbacks are the setup doors. After a member approves access in GitHub, Composio, Pipedream, or another provider, the callback files check the return visit and save the connection. Together, these parts make sure only trusted, well-identified traffic reaches the core conversation system.

### [Live user surfaces](stage-5.1.md) `stage-5.1` — 5 files

This stage is the system’s set of live “front doors.” It is used during the main work loop, when real people are talking to agents, watching replies, or inspecting activity. Each surface adapts a different user interface into the same core conversation machinery.

The Slack surface checks that requests really came from Slack, then turns messages and button clicks into agent turns. It sends answers, files, and updates back into Slack threads. The web surface provides the browser portal, where members sign in, pick an agent, chat, watch live responses, read history, and access admin or spending pages when allowed. The terminal surface powers the ufo command-line client by accepting HTTP posts from the shell and streaming back simple display commands. The debugger surface is read-only: it serves a page, JSON data, and live event streams so operators can see what happened inside one workspace.

At the center, the core surface file is the trusted doorway. It gives these interfaces safe ways to identify members, create conversations, submit messages, read workspace views, and deliver replies.

### [OAuth and connection callbacks](stage-5.2.md) `stage-5.2` — 4 files

This stage is the doorway back into UFO after a user has visited an outside service to approve access. It is not the main work loop. It is a setup and support step used when someone links an account, installs an app, or checks a connection. The outside service sends the browser back to a special web address, called a callback URL, and these files turn that return visit into a trusted connection record.

The core CLI surface exposes UFO’s general OAuth callback address and finishes the connection when the browser returns. The Composio and Pipedream providers do the same job for their hosted approval pages: they send the user out to approve access, then translate the result back into the form UFO expects, making sure the account just approved is the one UFO binds. The coding connection file handles GitHub App setup for a workspace. It builds the install link, checks GitHub’s return message, and saves only installations the signed-in member is allowed to use.

## [Membership, workspace objects, credentials, and grants](stage-6.md) `stage-6` — 14 files

This stage is the system’s permission desk. It runs behind the scenes whenever a person or agent tries to view workspace data, use an outside account, or change shared settings. The central object system defines a common way to list, read, create, update, and delete workspace records, while agent and object scope keep track of “who is acting” so the wrong agent cannot accidentally use the wrong powers.

Several files turn important workspace things into safe, inspectable objects. Agents, members, conversations, artifacts, credential slots, memories, synced pages, and external sources each get clear rules about what can be seen or changed. Sensitive areas are locked down: secrets are never shown, artifacts cannot be invented directly, conversations and memories are mostly read-only, and membership cannot lose its last admin.

Other parts decide access. Seats control which members an agent may answer. Web audience rules decide who can use agents in the portal. Connector account objects manage connected services, separating the account itself from an agent’s permission to use it. Together, these pieces act like guards, labels, and filing cabinets for workspace power.

## [Conversation admission and durable turn queueing](stage-7.md) `stage-7` — 1 files

This stage is the front door to the system’s work loop. Whenever a person sends a message, a scheduled task fires, or another part of the system asks the agent to do something, it is first turned into a “turn”: one saved step in a conversation. The word “durable” means the turn is written to storage, so it is not lost if the process restarts.

The file core/src/ufo/surfaces/admission.py makes all entry paths use the same gate. It checks whether the conversation is still allowed to continue, including whether spending limits have been reached and whether the conversation has been cancelled. It also records any context supplied by the “surface,” meaning the outside place the request came from, such as a chat interface or scheduler.

If the request is accepted, this stage creates the turn record and queues exactly one piece of agent work. Later, a worker can claim that queued item and run the agent safely.

## [Turn setup, context assembly, and prompt preparation](stage-8.md) `stage-8` — 11 files

This stage happens just before the agent starts working on a user’s next message. It takes a waiting turn from the queue, gathers everything needed, and turns it into a ready-to-run job. The queue code is the coordinator. It safely claims one queued turn, loads model settings, credentials, sandbox access, tool choices, member context, and subagent options, then either runs the turn or records a controlled failure if setup cannot finish.

The transcript and prompt part prepares the words the model will read. If the conversation is too long, it summarizes older parts while keeping recent messages exact. It also adds recalled memories and renders the final prompt from templates.

The skill and toolbox part prepares what the agent can do. It copies needed skill files into the safe workspace, builds the catalog of callable tools, and gives extensions a restricted way to act. Together, these pieces act like packing a workbench before a repair: instructions, tools, workspace, and permissions are all ready before the first model call.

### [Transcript compaction and prompt construction](stage-8.1.md) `stage-8.1` — 3 files

This stage is behind-the-scenes support for the main conversation loop. Its job is to prepare what the language model sees before it answers, while staying within the model’s context limit, meaning the maximum amount of text it can read at once.

The compaction code acts like a careful editor. When a conversation gets too long, it summarizes older messages into a structured record and leaves the most recent messages untouched, so the model still sees the latest details exactly as written. It also saves both the original and compacted forms, so information is not simply thrown away.

The memory event file defines the small, structured note used when the memory extension brings back stored memories before a reply. It also limits how much extra recall detail can be attached, preventing memory data from crowding out the current conversation.

The prompt rendering code then assembles the final instruction prompt. It fills in template blanks, checks that required pieces are present, and creates a fingerprint so prompt versions can be recognized and tracked.

### [Skill and toolbox preparation](stage-8.2.md) `stage-8.2` — 7 files

This stage prepares the agent’s “skills” and tools before the main work begins, and also supports them while work is running. A skill is a packaged ability, usually stored as files, that the agent can copy into its workspace and use. The runtime code reads skill folders, understands which skills depend on others, and copies the needed files into the agent’s sandbox, which is its safe working area.

The tool registry is the catalog of callable tools. It gives each tool a name, describes its inputs so the AI model knows how to ask for it, and routes each request to the right code. The extension context builds a safer toolbox for extensions and background jobs, so they can do approved tasks without direct access to databases, secrets, or other workspaces.

Several pieces add skills to this system. The model catalog skill turns the live model list into a readable table of available AI models, costs, and features. The skill_create extension lets users save, inspect, update, delete, and reload their own skills, while its store enforces name rules, limits, and collision checks. The sample probe is a simple health check that confirms a sample skill can run.

## [Model interaction and streaming tool-call loop](stage-9.md) `stage-9` — 1 files

This stage is the main work loop for one agent turn. A “turn” means one cycle of responding to a user request. After earlier startup and preparation steps have chosen the model and built the message history, this stage sends that material to the model provider and watches the answer arrive piece by piece as a stream.

The central file, `engine.py`, acts like the conductor. It first claims the turn so two workers do not answer the same request. It builds the final prompt, including the available tool descriptions, then calls the model. As the model streams back text, the engine collects it. If the model asks to use a tool, the engine runs that tool, adds the tool’s result to the conversation, and sends the updated messages back to the model. This loop continues until there is a final assistant answer, a safe parked state for later continuation, or a recorded failure. Along the way it tracks cost and commits the outcome so the system has a reliable record.

## [Sandboxed tool execution](stage-10.md) `stage-10` — 74 files

Sandboxed tool execution is the part of the main work loop where the agent stops just talking and safely does things. A sandbox is a controlled workspace, like a fenced workbench, where commands can run and files can change without exposing the whole computer.

The built-in tools are the basic hands: they run shell commands, read and edit files, share artifacts, ask the user for missing input, load skills, and coordinate helpers. The tool context acts like a permission slip, giving each tool only the access it needs. Sandbox session code creates or resumes the conversation’s workspace and records the work.

The carrier and proxy parts provide the workshop and its guarded internet door. Work can run in Docker locally or in a remote E2B sandbox. Network traffic is checked against policy, credentials are added only when allowed, and browser tools can click, type, download, upload, and preview sites.

Connector, research, coding, REPL, todo, and external-app tools let the agent use outside services and run repeated code safely. Document, spreadsheet, presentation, and PDF scripts handle office files inside the same protected setup.

### [Built-in shell, file, artifact, and coordination tools](stage-10.1.md) `stage-10.1` — 5 files

This stage is the agent’s practical toolbox during the main work loop. It lets the model do real tasks without freely touching the whole computer. Instead, work happens inside a sandbox, a controlled workspace like a fenced-off workbench.

The builtins file defines the tools the agent can call: run shell commands, read and edit files, share finished artifacts, ask the user for input, load extra skills, request secrets, connect accounts, and coordinate subagents. The tool context is the permission slip each tool receives. It exposes only the outside resources that tool is allowed to use, such as files, browser access, credentials, or cleanup callbacks.

The sandbox session file provides one standard doorway into sandbox work. It creates sandboxes, runs commands, moves files, and ties each action to the right conversation turn. The conversation sandbox file manages the private workspace for one conversation: opening it, resuming it, listing files, reading, writing, and cleaning up. The local sandbox file is a development-friendly version that runs commands in a real local folder, useful but not secure like a true isolated sandbox.

### [Sandbox carriers, proxying, browser, and website automation](stage-10.2.md) `stage-10.2` — 31 files

This stage is the system’s “workshop and internet front door” for web-based tasks. It is mostly shared support used during the main work loop, after a conversation needs a safe place to run code, browse sites, or preview a web project.

The sandbox carriers provide that safe place. Docker runs a conversation’s workspace in a local container, while E2B can run it in a remote cloud sandbox. The sandbox build script keeps both environments made from the same recipe, so tools behave the same in either place. The proxy files act like a guarded doorway to the internet: sandbox traffic goes through them, allowed hosts are checked, credentials are added only when permitted, and usage is recorded. The proxy gate script tests that this doorway is reachable and trusted before deployment.

On top of that safe base, the browser stages provide Chrome sessions, connect to Chrome’s control channel, understand pages, and perform actions like clicking, typing, downloading, and uploading. The website tools build, preview, and publish projects, using the same sandbox and browser machinery to check that pages are actually live.

#### [Browser providers and sandbox-hosted website automation adapters](stage-10.2.1.md) `stage-10.2.1` — 6 files

This stage is the system’s doorway to web browsers and website previews. It is shared behind-the-scenes support used when the system needs to open a page, test a site, upload or download files, or ask a hosted browsing service to do web work.

The core browser contract defines a simple promise: “give me a Chrome debugging connection for this turn.” A debugging connection is the control address that lets software drive Chrome, like a remote control. Sandbox Chrome fulfills that promise by starting or reusing Chrome inside the conversation’s sandbox and safely exposing its control socket. Browserbase fulfills the same promise with a Chrome session hosted in the cloud, including session setup, file transfer, reconnection, and cleanup.

The browser backend sits between tools and the actual browser. It opens the connection only when needed, reuses it during the turn, moves files safely, and closes things afterward. Browser Use adds higher-level cloud browsing tools for delegated tasks. The sites tools build, preview, and publish sandbox web projects, waiting until servers are reachable before browser checks begin.

#### [Chrome DevTools browser session and protocol plumbing](stage-10.2.2.md) `stage-10.2.2` — 8 files

This stage is the browser automation “plumbing” used while the system is doing its main work. It gives the rest of the project one controlled way to talk to Chrome. The session file is the central coordinator: it opens the debugging connection to Chrome, gathers the tools, and keeps track of tabs and downloads. The cdp file is the wire to Chrome DevTools Protocol, Chrome’s automation control channel. It sends commands, waits for replies, and routes events. The wire file checks the JSON messages at the border, so bad or unexpected data is caught early.

Around that core, smaller helpers handle common browser problems. Tabs opens, closes, switches, and navigates pages. Downloads notices files being saved, waits for them, and turns Chrome’s PDF viewer pages into real PDF downloads. Dialogs prevents pop-up boxes from freezing the run by accepting or dismissing them safely. Settle decides when an action has finished enough to move on, ignoring unrelated background noise. Runtime runs small JavaScript snippets in the page and reports browser errors as normal Python errors.

#### [Browser page understanding and action execution tools](stage-10.2.3.md) `stage-10.2.3` — 11 files

This stage is the browser “hands and eyes” of the system. It is part of the main work loop, where an agent needs to understand a web page and then act on it. The tools file is the front desk: it offers actions like open a page, read content, click, fill forms, upload files, and gather downloads, while sharing one browser connection for the turn.

The action definitions say exactly what requests are allowed and what information they must include. Before an action runs, the fixup code corrects small model mistakes, such as typing before focusing a field. The page and content code turn a live web page into readable text or a structured map of buttons, fields, links, and coordinates. The find code helps match a user’s description to a real page element.

Once the target is known, computer, forms, keys, and coordinate code turn the request into real browser events: clicks, scrolling, typing, shortcuts, uploads, and coordinate-correct mouse moves. The errors file gives a clear failure when the model asks for something impossible.

### [Connector, MCP, research, coding, REPL, todo, and external-app tools](stage-10.3.md) `stage-10.3` — 16 files

This stage is the toolbox layer used during the agent’s main work, with some shared support behind the scenes. It lets the agent reach outside services, run code, search, and keep track of work without exposing private credentials.

The connector tools are the front desk: they list connected services, show what each can do, run chosen tools, and pass files safely. Behind that, Pipedream and Composio brokers translate UFO’s tool calls into calls to those outside platforms. Their clients create login links, check connected accounts, run actions, and handle errors. Their proxy helpers let UFO call Gmail, GitHub, or similar services through the connector, so secret tokens stay with the connector provider. Composio also has a resolver for finding toolkits by name and an MCP session for one remote tool search.

MCP support lets workspace-configured tool servers advertise and run tools. Research tools use Exa or another search provider to search the web and fetch pages. The REPL extension runs repeated Python or JavaScript snippets. Slack and YC tools guide setup and queries. Todos store task checklists. The evaluation environment provides test email, calendar, and code-search tools through the same connector path.

### [Document, spreadsheet, presentation, and PDF skill scripts](stage-10.4.md) `stage-10.4` — 22 files

This stage is a toolbox of one-shot command-line helpers for office documents and PDFs. It is not the main app loop. Instead, sandboxed skills call these tools when they need to open, change, check, or export a user file safely.

The document review tools keep review state in a JSON file, which is structured plain text, then turn findings into visible comments or highlights in PDFs, PowerPoint files, and spreadsheets. The Word DOCX tools treat a Word file as a zipped bundle of XML text files. They can accept tracked changes, unpack the bundle, prepare comments, and pack it back into a working document. The PowerPoint PPTX tools do the same kind of package work for presentations, with extra helpers to add slides, render thumbnails, remove unused parts, or repair known file problems. The Excel XLSX tools use LibreOffice in the background to recalculate formulas and save updated results. The PDF tools fill real form fields, place text on static page layouts, and render pages as images for preview or inspection.

#### [Document review state and annotation scripts](stage-10.4.1.md) `stage-10.4.1` — 7 files

This stage is shared support for the document-review skill, mostly used after or during a review rather than at app startup. It keeps the review’s memory in order and turns hidden review results into visible notes inside real documents.

The package marker file simply makes the scripts folder importable by Python, like putting a label on a toolbox. constants.py defines the standard filenames for the saved review state and review log, so every script looks in the same place. models.py defines what a review issue looks like, such as its location and message, and can format that issue as clear comment text.

manage_state.py is the control desk. It records the review phase, sections, claims, issues, and final summary in a JSON file, which is a plain text data file with structured fields. The annotation scripts then use that saved information. annotate_pdf.py highlights matching text and adds PDF comments. annotate_pptx.py writes findings as PowerPoint comments. annotate_xlsx.py adds comments to the right spreadsheet cells in a copied Excel file.

#### [Word DOCX packaging and comment helpers](stage-10.4.2.md) `stage-10.4.2` — 4 files

This stage is a set of behind-the-scenes command-line tools for working with Word DOCX files. A DOCX file is really a zipped package of XML files, where XML is structured text that describes the document. The tools let the system open that package, make safe edits, and close it again.

The usual flow starts with accept_changes.py when a document has tracked edits. It asks LibreOffice to run invisibly in the background and save a clean version with all changes accepted. Next, unpack.py opens the DOCX package into a folder and tidies Word’s noisy XML so people or other tools can read and change it more reliably. If comments need to be added, comment.py creates or updates the hidden Word comment records and relationship files that Word requires, though another step must still place the visible comment markers in the main document XML. Finally, pack.py zips the folder back into a usable DOCX and removes extra XML spacing to keep the file neat.

#### [PowerPoint PPTX package, slide, and repair tools](stage-10.4.3.md) `stage-10.4.3` — 5 files

This stage is a set of behind-the-scenes workshop tools for PowerPoint presentations. It is used around the main presentation workflow: before editing, after editing, or when a file needs fixing. A PPTX file is really a zipped package of many XML files, where XML is a text format that stores the slides, layouts, and relationships.

The package marker file, __init__.py, simply lets Python treat the scripts folder as importable code. unpack.py opens a .pptx and spreads its contents into a folder, then formats the XML so people and tools can read it more easily. slides.py works with that unpacked material or the presentation itself: it can remove unused parts, add a slide, or render slide thumbnails like a contact sheet. pack.py does the reverse of unpack.py: it takes the folder and builds a valid .pptx again, trimming unnecessary XML spacing while preserving slide text. repair.py is the emergency kit. It fixes known package and text-spacing problems from pptxgenjs so PowerPoint opens the file cleanly.

#### [Excel XLSX LibreOffice recalculation helpers](stage-10.4.4.md) `stage-10.4.4` — 3 files

This stage is behind-the-scenes support for working with Excel .xlsx spreadsheets. Its job is to use LibreOffice, a free office suite, as a quiet helper process to refresh spreadsheet formulas when the system needs trustworthy saved results.

The scripts folder is made importable by __init__.py. That file does not do work itself, but it lets other Python code find and reuse the tools in this folder.

The shared helper file, _soffice.py, is like the power switch and map for LibreOffice. It provides common code for starting LibreOffice “headlessly,” meaning without showing its normal desktop window. It also knows where LibreOffice keeps user macro files on Linux and macOS, so related scripts can find the right support locations.

The main worker is recalc.py. It opens an Excel file in LibreOffice, tells LibreOffice to recalculate every formula, saves the updated file, and checks for remaining spreadsheet error values. Together, these pieces turn LibreOffice into an automatic calculator for spreadsheets.

#### [PDF form, layout, and rendering tools](stage-10.4.5.md) `stage-10.4.5` — 3 files

This stage is shared behind-the-scenes support for working with PDFs. It is not the main application loop. Instead, it provides small command-line tools that other workflows can call when they need to inspect, fill, or preview documents.

The formfill tool works with PDFs that already contain real form fields, like text boxes built into the file. It can check whether those fields exist, list them in JSON, which is a simple structured text format, and then fill the PDF using values from that JSON.

The layout tool is for PDFs that look like forms but do not have real fillable fields. It helps inspect the page, preview where answers should appear, and place text at chosen coordinates, like putting labels onto a printed form.

The render tool turns each PDF page into a PNG image. These page images can be shown to a user, checked visually, or passed to other tools. Together, the three tools cover real forms, static forms, and visual inspection.

## [Delegated subagents and multi-step workflows](stage-11.md) `stage-11` — 9 files

This stage is used during the main work loop, when the main agent decides a job is big or specialized enough to hand to a helper. A “subagent” is a smaller child session with its own instructions, tools, input, and expected output, like sending a specialist to do one part of a project.

The core profile file defines the fallback general-purpose helper, used when no more specific helper is available. The core subagents file is the dispatcher: it starts child turns, ties them back to the parent turn, tracks them, and allows them to be cancelled safely.

Each extension adds specialists. The browser files define a browser subagent and tools for sending one or many web-browsing tasks to it. The research files define normal and deep research helpers, plus a wide-research tool that runs many research jobs in parallel and saves the combined results. The sites files define a website-building helper and the tool that delegates a full site build to it. The brief pipeline defines three writing steps: outline, draft, and critique.

## [External source sync, indexing, and durable memory work](stage-12.md) `stage-12` — 58 files

This stage is the system’s background intake and memory workshop. It is not the chat loop itself. Instead, it runs sync jobs that fetch outside pages and records, notice what changed, store safe copies, make them searchable, and turn useful facts into long-term memory.

The core source backend lets each connector behave like a normal UFO source. It takes a connector’s stream of records and packages it so syncing can resume, store results, and handle deletions safely. The sync bridge then writes fetched document bodies into the internal page database and produces a clean list of page changes for later steps.

The many source connector groups are the “adapters” for outside tools: work tracking, documents, mail, chat, CRM, marketing, HR, finance, YC data, and evaluation feeds. Each one reads its service and translates records into a common shape. The registry helps the system find and name these connectors consistently. Search indexing slices changed text into chunks and updates the search store. Memory tools save important facts, recall them later, and condense duplicates into clearer summaries.

### [Search indexing and default index backend](stage-12.1.md) `stage-12.1` — 2 files

This stage is shared behind-the-scenes support for search. It is used when the system needs to make text searchable, keep the search data up to date, or answer a user’s search later. The core indexing file sets the common rules. It takes long text and cuts it into smaller chunks, like slicing a book into searchable paragraphs. It also defines how those chunks are handed off to whatever search storage is being used, so the rest of the app does not need to know the details of each backend.

The default index extension is the built-in backend used when no custom one is configured. It saves those chunks, updates them when the original content changes, and deletes old chunks that no longer match the source. When searching, it can match ordinary words and also use vector similarity, which means comparing numeric “meaning fingerprints” of text to find related content even when the exact words differ. Together, these parts turn raw text into a maintained, searchable index.

### [Durable memory extension](stage-12.2.md) `stage-12.2` — 4 files

This stage adds long-term memory to the system. It is shared behind-the-scenes support that helps agents remember useful facts across work, instead of relying only on the current conversation. The manifest is the wiring panel. It tells the wider system which memory tools exist, when automatic recall should run, which background jobs should start, how page changes should be noticed, and where the memory viewing page lives.

The store is the main filing cabinet and search desk. It saves memory records, finds the most relevant ones when an agent needs context, keeps links to source pages searchable, and does slower indexing work in the background so normal saves stay quick. The condenser is the cleaner. It looks at changed pages, pulls out facts worth keeping, and later combines related older facts into broader summaries so memory does not become a pile of duplicates. The surface is the read-only window for operators. It lets authorized people inspect stored memories for a workspace without changing them.

### [Source connector registry and YC feeds](stage-12.3.md) `stage-12.3` — 2 files

This stage is shared behind-the-scenes support for bringing outside knowledge into the system. It helps the rest of the code find the right “connector,” meaning the small adapter that knows how to talk to a particular information source.

The registry file is the central address book. It lists the supported source connectors and gives the system one consistent way to name a connection. That name is built from the provider, account, and base web address, so the same source gets the same stable label each time. This keeps configuration predictable and avoids guessing which connector should be used.

The YC source file is a special connector for Y Combinator material that is not just a normal software-as-a-service account. It can load fixed guidance collections, such as manuals and startup library content, into workspace memory. It can also run limited searches over directory-style YC data, such as company and founder information. Together, the registry says how to find and name the connector, while the YC connector does the actual fetching.

### [Work, engineering, and incident source connectors](stage-12.4.md) `stage-12.4` — 9 files

This stage is a set of read-only “source connectors”: small adapters that know how to visit outside work tools, ask for data, and translate the answers into the project’s common record format. It is behind-the-scenes support for syncing and search. Nothing here changes data in those outside systems.

Each file speaks to one service. The Asana connector reads workspaces, projects, tasks, stories, and users, stepping through API pages like turning pages in a catalog. ClickUp walks its hierarchy from teams down to spaces, folders, lists, tasks, comments, fields, and goals. GitHub brings in organizations, repositories, issues, commits, pull requests, users, and activity. Jira reads projects, issues, comments, users, boards, and sprints. Linear uses GraphQL, a query language for APIs, to stream issues, projects, teams, labels, and comments. monday.com covers users, teams, workspaces, boards, items, updates, logs, and tags. PagerDuty reads incident-response data such as incidents, schedules, services, and on-call records. Sentry reads error-tracking projects, issues, events, members, and releases. Wrike reads contacts, folders, tasks, comments, workflows, and custom fields.

### [Knowledge, document, and database source connectors](stage-12.5.md) `stage-12.5` — 6 files

This stage is shared behind-the-scenes support for bringing outside knowledge into the system. It is like a set of adapters for different filing cabinets. Each adapter knows how to open one service, read what the account is allowed to see, and turn it into steady streams of text and records that the rest of the system can sync, store, search, or update later.

The Airtable connector reads bases, tables, and records, translating Airtable’s layered structure into pages of data. The Confluence connector reads spaces, pages, blog posts, comments, groups, and audit records, then makes the content searchable without changing Confluence. The Google Docs connector reads allowed documents and converts them to plain text. The Google Drive connector covers files, shared drives, permissions, comments, and revisions, and helps decide what to store, update, delete, skip, or render. The Google Sheets connector finds spreadsheets, opens their tabs and cells, and formats them as readable content. The Notion connector reads users, pages, databases, comments, and blocks, turning workspace content into searchable prose.

### [Mail, calendar, chat, and meeting source connectors](stage-12.6.md) `stage-12.6` — 7 files

This stage is the system’s set of communication “adapters.” It runs during the sync work, reaching out to outside services and translating their different data formats into the system’s common records, so they can be stored, searched, and recalled later.

Each file connects to one service. Calendly reads scheduling data such as event types, groups, scheduled events, and invitees. Gmail reads mailbox changes since the last run and unwraps Gmail’s nested message structure into readable text. Google Calendar imports calendar events and also records attendees separately, so the system can understand who was invited. Google Meet turns meeting transcripts and AI-generated notes into searchable pages. Microsoft Teams reads teams, channels, chats, and messages through Microsoft Graph, Microsoft’s access layer for workplace data. Outlook uses the same Microsoft Graph bridge for mail, threads, contacts, folders, and calendar events. Slack reads workspace users, channels, messages, threads, and authors. Together, these connectors act like translators at the system’s front door, turning scattered conversations and schedules into one searchable memory.

### [CRM, sales, and support source connectors](stage-12.7.md) `stage-12.7` — 6 files

This stage is the set of “adapters” that let the system bring in customer and support data from outside services. It is part of the main sync work: when the system needs fresh records, these connectors log in to each service, ask for data through that service’s API, and reshape the replies into standard records that the rest of the system can store, search, and recall.

Each file speaks one service’s language. The Attio connector reads companies, people, deals, tasks, notes, meetings, and call recordings. HubSpot covers a wide range, including CRM records, marketing content, conversations, analytics, custom objects, links between records, and deletion markers. Salesforce reads common CRM items like accounts, contacts, opportunities, and tasks, without writing anything back. Freshdesk handles support objects and its different page-by-page result formats. Intercom gathers conversations, contacts, companies, tickets, admins, tags, and related details. Zendesk reads tickets, users, comments, help articles, and community posts. Together they act like translators feeding one common indexing pipeline.

### [Marketing, advertising, social, and feedback source connectors](stage-12.8.md) `stage-12.8` — 7 files

This stage is a set of source connectors: small adapters that let the system fetch data from outside marketing and advertising tools during a sync run. Each connector knows how to sign in to a service, ask its web API for data, split large results into pages, and reshape the answers into steady “streams” of records that the rest of the system can store.

ActiveCampaign brings in marketing and customer-relationship data. Facebook Ads reads Meta ad accounts, campaigns, ad sets, ads, and daily performance results. Google Ads does the same for Google customer accounts, campaigns, ad groups, ads, client accounts, and metrics. Instagram uses Meta’s API to fetch business Pages, linked Instagram accounts, posts, stories, and analytics. Klaviyo reads marketing data and flattens nested records into simpler ones. Mailchimp collects audiences, subscribers, campaigns, reports, and activity logs despite their varied formats. Typeform adds form-related data, including forms, responses, workspaces, themes, images, and webhooks. Together, these files act like translators at the system’s front door.

### [HR and recruiting source connectors](stage-12.9.md) `stage-12.9` — 6 files

This stage is the system’s set of “adapters” for HR and recruiting tools. It is used during the main sync work, when the system reaches out to outside services and copies their records into a common form it can store, search, or use later. Each connector knows the rules of one service’s web API, meaning the doorway the service provides for software to request data.

Ashby and Greenhouse focus on recruiting pipelines, pulling candidates, jobs, applications, interviews, offers, users, and lookup lists. Recruitee covers similar hiring data, such as candidates, job offers, and departments. BambooHR reads broader employee and HR datasets from BambooHR. Deel brings in payroll-adjacent workforce records like contracts, forms, payslips, timesheets, and tasks. Rippling reads companies, workers, and teams.

Together, these files act like translators at different service desks. Each one asks its service for data page by page, turns the replies into steady streams of records, and hands them to the wider sync system in a consistent shape.

### [Finance, billing, accounting, and commerce source connectors](stage-12.10.md) `stage-12.10` — 7 files

This stage is part of the system’s behind-the-scenes data intake work. It is a set of connectors, which are small adapters that know how to talk to outside services and translate their answers into a common shape the rest of the system can store, search, and recall.

Each file covers a different finance or commerce tool. Brex brings in spend-management records like expenses, vendors, budgets, and departments. Chargebee and Recurly read subscription-billing data such as customers, subscriptions, invoices, coupons, and transactions. Stripe handles a wide range of payment and billing records, including customers, invoices, subscriptions, payments, and connected accounts. QuickBooks Online and Xero read accounting records like ledgers, accounts, contacts, invoices, and payments. Square reads commerce data such as locations, catalog items, orders, inventory, customers, and payments.

Together, these connectors act like translators at a loading dock: each service speaks differently, but the system receives steady streams of usable records.

## [Live streaming, reply delivery, and artifact download](stage-13.md) `stage-13` — 4 files

This stage is the system’s live delivery layer. It runs during an agent’s turn and after it finishes, making sure people watching in Slack, the web app, or a terminal can see progress, receive the final answer, and download any shared files. It is like the broadcast booth for the work loop.

The central piece is the live message hub. It publishes updates such as generated text, tool activity, costs, and the final result while a turn is running. It also keeps a short history, so a screen that disconnects can resume from its last known point instead of losing the stream. The hub tail builds on this by combining those live messages with saved database state. That lets a late or reconnecting client catch up and still see a clean ending.

For larger deployments, the Redis stream hub moves the same live updates through Redis Streams, a shared message pipe that multiple server processes can read from. Finally, the artifacts route protects file downloads with signed tokens, so shared links can fetch the actual stored file only when the link is valid.

## [Scheduled jobs, billing operations, and self-improvement loops](stage-14.md) `stage-14` — 16 files

This stage is behind-the-scenes machinery. It runs work that should happen later or on a schedule, rather than during one user request. The shared job engine finds background jobs at startup, chooses which workspaces need them, and runs each job inside the right workspace. A candidate finder safely looks across workspaces only to collect IDs, so private work stays scoped.

The scheduling pieces act like an alarm clock. Agents can create repeating tasks or “wait until” pauses. A cron parser checks timing rules and computes the next run. A runner wakes up, claims due tasks so they do not fire twice, invokes them, records results, and reschedules repeats.

Other jobs handle business operations. The Metronome extension sends usage and seat data to billing systems and connects Stripe payment setup to chat tools. A Slack Connect job turns completed signups into one invitation without slowing signup.

The self-improvement loop mines past failures into a test corpus, asks a model for prompt rewrites, replays old tasks without re-running real tools, evaluates old versus new prompts, applies safety gates, and only then opens governed proposals. Governance applies changes only if the original prompt has not changed meanwhile.

## [Cross-cutting persistence and domain schemas](stage-16.md) `stage-16` · (cross-cutting) — 2 files

This stage is shared behind-the-scenes support for keeping important system facts in storage. It is not one single step in startup or shutdown. Instead, many parts of the system use these definitions while they do their work, such as admission, conversation turns, tools, billing, memory, syncing, onboarding, and the user interface.

The two files here focus on saved conversations. core/src/ufo/transcript.py defines the common “shape” of a transcript: the agreed format for stored conversations and for compacted summaries of older conversation content. This is like a standard form everyone must fill out the same way, so readers and writers do not misunderstand each other.

core/src/ufo/loop/transcript.py uses that format to actually read and write transcript data durably. It also protects newer saved data from being replaced by stale or repeated updates. Together, one file defines the contract, and the other safely applies it while the system runs.

## [Cross-cutting public SDK, extension API, and protocol types](stage-17.md) `stage-17` · (cross-cutting) — 35 files

This stage is shared support that sits around the whole system, not one single startup or work-loop step. It defines the stable public doors that extensions, surfaces, and integrations are supposed to use. The SDK façade modules re-export approved types for context, logging, HTTP, accounting, seats, objects, and other common areas, so outside code does not depend on fragile internal paths. The extension declaration pieces define manifests, which are fixed descriptions of what an extension offers, and include a sample extension that proves those public hooks work together. The provider, data, model, and security façades expose safe entry points for credentials, grants, connectors, search, memory, indexing, and model access.

The directly assigned files provide the shared “shapes” behind those doors. connectors.py defines how outside services authenticate and run actions without leaking secrets. memory.py, search.py, and models/interface.py define common request and reply types for remembered knowledge, web lookup, and AI model calls. The browser, sandbox, scheduling, and surfaces SDK files are stable shortcut imports for those public contracts.

### [Public SDK package and common façade modules](stage-17.1.md) `stage-17.1` — 9 files

This stage is shared behind-the-scenes support for people who build on top of UFO. It creates the public SDK, meaning the stable set of import paths that outside extensions are meant to use. Instead of forcing extension authors to reach into deep internal files that may change, these modules act like a front desk: they point callers to the right tools while hiding the building’s back corridors.

The package marker, __init__.py, simply makes ufo.sdk importable. The context module exposes context tools, which carry request or runtime information through the system. The http module publishes safe request and response types and includes the approved helper for setting session cookies with the right limits. The o11y module exposes structured logging, a way to record events in a machine-readable form. Accounting, audience, hub, objects, and seats each re-export their area’s public types, errors, and helper functions. Together, these files form a stable façade over internal code, so extensions can depend on clear public doors rather than fragile implementation details.

### [Extension declaration, runtime SDK façades, and conformance sample](stage-17.2.md) `stage-17.2` — 6 files

This stage is the public front door for people who write UFO extensions. It is mostly shared support used before and during startup, when the system reads extension declarations and wires their features into the main application. The core piece is `manifest.py`, which defines the “manifest”: a set of fixed data objects, meaning values that describe an extension but are not changed later. An extension uses these objects to announce what it provides, such as tools, routes, jobs, search backends, browser providers, hooks, skills, and credentials.

The `ufo.sdk.*` files are safe import doorways. They let extension authors use names like jobs, manifests, skills, and tools without depending on the project’s internal folder layout. They are like labeled sockets on the outside of a machine: the inside can be rearranged, while outside code keeps plugging into the same place.

The sample extension ties it together. It declares small examples of many extension points, giving tests and new developers a working model of the public API end to end.

### [Provider, data, model, and security SDK façades](stage-17.3.md) `stage-17.3` — 12 files

This stage is shared support for people adding integrations to the system. It is not the main work loop itself. Instead, it provides public “front doors” to internal features, so extension authors can import stable SDK names without depending on private file locations that may change.

The security-facing doors are authproxy, bearer, credentials, and grants. They expose approved pieces for authentication backends, bearer-token checking, credential objects, and grant or connection audit helpers. Connectors and sources cover outside systems: connectors gathers connector and OAuth tools, while sources gathers source-sync interfaces, REST connector helpers, pagination, and related errors. The lower-level sources connector file defines the common contract every external data connector must follow, including how to move through partitioned data such as many repositories or channels.

The data and model doors are index, memory, search, and models. They expose types for indexing, embeddings, memory search, search interfaces, and model clients. Operator adds public helpers for web login and sessions. Together these files act like a well-labeled tool cabinet for extension builders.

## [Cross-cutting security, secrets, grants, and egress policy](stage-18.md) `stage-18` · (cross-cutting) — 13 files

This stage is shared behind-the-scenes safety machinery. It keeps work, secrets, and outside-service access from leaking between customers or people. The workspace helper sets a clear “current workspace” boundary, so billing, database reads, and credentials stay in the right place. Token tools create signed, tamper-evident strings for member login and artifact downloads, so the system can trust short messages without saving every token in a database. Audience checks decide who may see conversation memory or context.

Credential code encrypts secrets, verifies private handoffs, chooses provider hosts, and supports direct source connectors that use member-supplied API keys. Keyed connector settings describe which API keys owners must provide and where agents may use them. GitHub App support turns a sealed installation into a short-lived git token.

Grant code lets a member connect an OAuth account and give one agent limited, revocable use of it. Operator session rules protect admin-only tools. Composio proxying lets requests use external accounts without exposing the real token. Finally, sandbox egress rules act like a network bouncer, allowing only approved outbound calls and injecting secrets only where policy permits.

## [Cross-cutting observability, accounting, metering, and spend control](stage-19.md) `stage-19` · (cross-cutting) — 2 files

This stage is shared behind-the-scenes support for knowing what the system did and what it cost. It is not one single work step. Instead, it is used throughout the main work loop, background jobs, and admin reporting whenever the system calls a model, serves a proxy request, or needs to decide whether more work is allowed.

The accounting file is the central record keeper. When model usage happens, it writes token counts into a ledger, which is like a checkbook for usage. It can also prepare those records for an outside billing service, check spending limits before a task starts, and build summaries that humans can read on admin or spend pages.

The pricing file is the price tag machine. It knows how to turn token usage into money for each model. It also stamps each price table with a stable version, so later reports can prove which exact prices were used. Together, pricing calculates the charge, and accounting records, limits, exports, and summarizes it.

## [Cross-cutting configuration, provider adapters, and reusable utilities](stage-20.md) `stage-20` · (cross-cutting) — 20 files

This stage is shared behind-the-scenes support. It is not one step in a single request path. Instead, many parts of the system consult it when they need common services, like talking to AI model providers, reading external data, or storing large files.

The model adapter files act like plug converters. `anthropic.py` translates UFO’s internal message format into Anthropic’s Messages API and streams back text, tool calls, and token counts. `openai.py` does the same for OpenAI-compatible servers, including both chat and newer response formats. `ufo_ext_openrouter.py` connects the same style of request to OpenRouter, which can route it to many models.

`blob.py` gives the system one simple way to read and write large byte objects, whether they are on disk or in S3-style cloud storage. `rest.py` gives data connectors a reusable HTTP client, retries, and pagination helpers for REST APIs, meaning web APIs that expose data through standard URLs.

`subjects.py` keeps shared labels consistent. The many `__init__.py` files are package signposts: they make folders importable and show where major areas like tools, models, prompts, schema, sandbox, and extensions live.
