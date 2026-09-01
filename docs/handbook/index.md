# System Handbook — Stage Index

Each stage below links to its full page; the paragraph is the stage's role in the system.

## [Operator Entry Points, Packaging, and Process Launch](stage-1.md) `stage-1` — 7 files

This stage is the system’s front door. It covers the tools people or deployment scripts use before the main server and workers settle into normal work. The main entry point is ufoctl, defined in cli.py. It lets an operator create and maintain a workspace: run setup, migrate the database, open the web portal, manage billing and credentials, repair conversations, load demo data, and package the app. The small __init__.py file simply tells Python that ufo is an importable package.

For deployment, bundle.py gathers the exact ingredients needed to run UFO elsewhere, like packing a travel kit with the right configuration, lockfile, runtime wheel, and sandbox client. product.py reads database facts and turns them into product funnel measurements, such as whether a workspace has members, tools, chats, or payments.

The sandbox scripts are pre-flight checks. build_template.py keeps the local Docker sandbox image and the E2B cloud template built from the same recipe. proxy_gate.py verifies that sandbox web traffic is being intercepted safely. The sample skill probe is a tiny “does this run?” test.

## [Configuration, Pack Selection, and Service Wiring](stage-2.md) `stage-2` — 9 files

This stage is part of startup and behind-the-scenes setup. It decides what kind of UFO system is being launched, checks that the needed settings are safe and present, then turns those settings into real services the rest of the program can use. The main configuration file defines the expected shape of the deploy config, usually from ufo.toml, and stops early if something important is missing. The proxy startup helpers combine that config with environment variables, which are operating-system settings, to create model-provider and database connections.

The pack files are like preset toolboxes. The local assistant pack enables the normal assistant features. The billing variant adds billing for local testing. The hosted assistant pack chooses cloud-ready services and prompts for managed workspaces. The evaluation packs adjust the toolbox for tests: assistant_eval removes live broker dependencies and adds deterministic test tools; DSQA and GDPVal packs offer different mixes of core, search, browser, document, and research tools. The sample pack is a small proof that pack loading and onboarding work end to end.

## [Database Migration and First-Run Bootstrapping](stage-3.md) `stage-3` — 192 files

This stage happens before the system can safely do normal work. Its job is to make sure the database, which is the system’s long-term memory, has the right shape for the current version of the code. It also fills in the first useful records so a fresh installation is not empty.

The migration runner, env.py, is the switchboard for Alembic, the tool that applies database changes in order. It connects Alembic to the application’s database and runs any missing upgrade steps. The Core and Extension Schema Changes are those ordered steps. They create and adjust tables for workspaces, users, agents, conversations, billing, permissions, jobs, content sources, and feature-specific extensions, like adding labeled drawers to a filing cabinet without losing what is already inside.

After the storage is ready, Workspace Onboarding and Seating creates the first workspace, first administrator, and initial assistant. It also manages who gets a “seat,” meaning permission to use the assistant, and can add demo conversation data so the product has something realistic to show and test.

### [Core and Extension Schema Changes](stage-3.1.md) `stage-3.1` — 186 files

This stage is the project’s database renovation plan. It runs during install or upgrade, before normal work, so the current code can read old saved data safely. The first migration, 0001_heartbeat.py, lays the foundation: workspaces, people, agents, conversations, turns, and usage charges.

The later migrations expand that foundation in many directions. Conversation and turn migrations record richer chat history, execution state, subagent work, titles, surfaces, and workspace changes. Scheduling and runtime migrations track delayed jobs and live workers. Surface, inbound-message, source, page, and artifact migrations store where messages and content come from. Agent, app, visibility, identity, access, audit, billing, and cleanup migrations keep permissions, built-in apps, costs, and retired data consistent.

Extension migrations add feature-owned tables for memory, search indexes, publishing, skills, source triggers, web chat, workflows, objectives, reports, research, eval fixtures, samples, scheduled pauses, and code review. Together, these files act like careful numbered construction steps, adding shelves, relabeling boxes, and removing old rooms without losing the records the system depends on.

#### [Core Conversation Record and Surface Schema Migrations](stage-3.1.1.md) `stage-3.1.1` — 7 files

This stage is behind-the-scenes database upkeep. It uses Alembic migrations, which are versioned steps that change the database shape when the system is upgraded. Together, these files make the conversation record richer, so a conversation can be reopened, shown in the right place, searched, and connected to workspace changes.

The sandbox migrations add memory for where a conversation runs its work: one field stores a durable sandbox handle, and another stores the separate sandbox conversation used for running turns. The audience migration records who a conversation is meant for, and stops the upgrade if old Slack data cannot be safely sorted. The surface label migration stores the friendly name of the place where the conversation began. The conversation change migration adds a table for Git workspace changes linked to a conversation. The title migration stores conversation titles directly and backfills old ones. The final title migration records whether a title has already been summarized and removes older web-only tracking.

#### [Core Turn Execution, Admission, and Subagent Migrations](stage-3.1.2.md) `stage-3.1.2` — 20 files

This stage is behind-the-scenes upgrade work for the database, the system’s long-term memory. These migrations reshape how conversation turns are stored so the main work loop can run safely as features grow. Early changes let turns sit under parent turns, come from subagents, and carry loop depth, trace links, surface context, speaker details, “on behalf of” member data, and subagent display names. Other changes widen how turns can enter the system, adding scheduled and intent-based admission sources. Several migrations add guardrails for execution: they record the currently running attempt, prevent duplicate resume jobs, track whether a delegated child task still owes a result, and store replies sent before a turn fully finishes so they can be delivered once. BYOK, meaning “bring your own key,” runtime configuration, created references, connection landing time, and original spawn intent are also saved on turns for accurate recovery and billing. The index migrations are like adding labels to filing cabinets: they speed up finding parent-child turns, spoken turns by speaker, pending subagent results, and an agent’s live or recent work.

#### [Core Scheduling and Runtime Fleet Migrations](stage-3.1.3.md) `stage-3.1.3` — 11 files

This stage is behind-the-scenes database setup for the system’s scheduling and runtime tracking. A database migration is a small upgrade script that changes what the database can store. Together, these migrations teach the system how to remember work that should happen later, which runtime processes are alive, and how to find old or ready work efficiently.

First, it adds a runtime instance table, like a sign-in sheet for running workers, recording their workspace and last check-in. Later changes let shared fleet workers exist without belonging to one workspace, then remove old shared-fleet columns that are no longer needed. Scheduling starts with a scheduled task table for repeat or delayed work. Later upgrades add the last turn that triggered a task, an expiration time, agent-specific task identity, and a pause switch. Pause behavior then moves out of the core task table into an extension table, keeping the core model simpler. Another migration records where a turn came from, supporting scheduled pause and resume. Finally, job-sweeping indexes act like book indexes, helping cleanup or scanning jobs quickly find candidate records without reading everything.

#### [Core Surface, Inbound Message, and Shared Artifact Migrations](stage-3.1.4.md) `stage-3.1.4` — 13 files

This stage is behind-the-scenes database setup for the places where conversations happen, the messages that arrive there, and the files or other items people share. A database migration is a step-by-step change to the stored data layout, like adding new labeled shelves to a filing room.

The early Slack and web migrations let the system recognize Slack and the web interface as valid conversation sources, and store Slack conversations safely for later replies. The surface “seam” and workspace-key changes loosen old surface rules, connect surfaces more clearly to workspaces, and speed up finding replies ready to send. Listener-claim and address-routing migrations help coordinate who is allowed to listen on a surface, and route shared channels such as iMessage to the right workspace by address.

The inbound-message migrations add a staging table for new messages, keep them ordered and duplicate-safe, then move rendered message text into its own place and remove the old field. The shared-artifact migrations give shared items stable IDs, previews, corrected media types, and links to the request and content they came from.

#### [Core Source, Page, Connection, and Memory-Surface Migrations](stage-3.1.5.md) `stage-3.1.5` — 14 files

This stage is behind-the-scenes setup for the database, the place where the system stores its long-term records. These migration files are like renovation plans: each one changes the database shape while keeping existing data usable.

It starts by creating the basic idea of sources and pages: where content comes from, what pages were found, and which workspaces they belong to. Later changes make sources more flexible, so extensions can add new backend types, and safer, so sources can track repeated errors, be marked removed without deleting them, be owned by a member or shared, and grant read access to specific agents. Other migrations add source backoff details, including refusal counts and temporary parking, and retire broken old QuickBooks sources that cannot sync.

The page-related changes add browsing fields, clearer record timestamps, per-workspace revision numbers for reliable syncing, and source-provided page identities to prevent duplicates. Finally, this stage shows a storage direction change: it first creates old knowledge-graph tables for entities and links, then removes them when the system moves to one unified “memory surface” for stored knowledge.

#### [Core Agent Configuration, Model, and Spawn Migrations](stage-3.1.6.md) `stage-3.1.6` — 15 files

This stage is behind-the-scenes database upkeep. It runs during upgrades, not during an agent’s normal work. Each file is a migration, meaning a small ordered change to saved data so old agent records still match what the newer system expects. Some migrations add new agent settings: internet access, reasoning mode, sandbox size, provision source, setup details, spawn input and output descriptions, owner, icon, workspace-skill use, and a plain-language purpose. Others clean up existing data. Several model migrations move agents away from old or unsupported model names, including Bedrock and Fable IDs, so they still point to models the system can actually call. One Fable migration removes a reasoning setting that model cannot use. Icon migrations give agents and built-in apps the right visual labels, and one changes the default icon for future agents. Together, these files act like careful renovation steps: they add new shelves to the database, relabel old boxes, and keep stored agents usable as the product grows.

#### [Core Agent Binding, App Lifecycle, Visibility, and Tool-Allowlist Migrations](stage-3.1.7.md) `stage-3.1.7` — 12 files

This stage is a set of database migrations, meaning upgrade steps that reshape saved data as the product changes. It is behind-the-scenes support for keeping old workspaces usable with newer agent rules. First, agent bindings make every surface installation and conversation point to an agent, so ownership is always clear. Control principals then give each workspace a controlling member and a main agent. Later migrations add agent visibility, let agents be archived without deleting history, and free archived names so active agents can reuse them.

Several steps update built-in app agents to match newer product behavior. Code review agents are moved to the newer code app identity. The old Tasks app is archived because Tasks now appears directly as a workspace tab. Chat becomes the official main agent, and old extra chat rows are cleaned up. Wiki agents are made private when users have not changed the old default.

The final migrations rewrite stored tool allowlists, which are saved lists of actions an agent may use. They rename old object, Slack, and iMessage action names so existing agents keep the right permissions after the code changes.

#### [Core Identity, Access, Workspace Metadata, and Audit Migrations](stage-3.1.8.md) `stage-3.1.8` — 20 files

This stage is behind-the-scenes upgrade work for the database, the system’s long-term memory. These migrations add and reshape the tables and fields that define identity, access, workspace membership, and audit history. Early steps create storage for encrypted workspace credentials, proposed changes, extension data, and grants, which are records saying an agent may use an account or connection. Later steps split grants into reusable connections and per-agent permissions, then move sharing settings onto the connection itself.

Other migrations track how workspaces and members are managed. They add member email lookup, time zone, invitation history, and the old “seat” billing model, then remove or simplify seat fields when membership becomes unlimited. Audit and safety pieces add records for admin transcript access, object changes, and fulfilled credential requests, so important actions can be traced and not duplicated. Network access is supported by a workspace rule version counter that updates when egress, or outbound network, rules change. Cleanup migrations remove obsolete indexes, markers, and the old Exa extension data.

#### [Core Ledger, Balance, Spend, and Usage Billing Migrations](stage-3.1.9.md) `stage-3.1.9` — 17 files

This stage is behind-the-scenes database setup for billing and usage tracking. A database migration is a versioned change to the shape of stored data. Together, these migrations turn the ledger into a more complete “cash register” for the system.

Early changes add spend caps for workspaces, members, and agents, and add a parked turn state for paused work. The ledger then learns more kinds of usage it can record: outbound traffic, sandbox tokens, images, and videos. Other changes make ledger rows more flexible, so costs can attach to a workspace instead of only to one turn, and add audit details such as price digests, BYOK flags, billing identity, and exact debited amounts.

Several migrations improve billing reports and exports. They track export progress, add lookup indexes by workspace and time, and split token usage into clearer buckets like input, output, prompt, cache-read, and cache-write tokens. The balance migrations add prepaid workspace balances, credit history, auto top-up settings, and the timestamp for the first verified card top-up. Together, these changes support charging, limits, exports, and audits.

#### [Core Legacy Branch and Retired Surface Data Cleanup Migrations](stage-3.1.10.md) `stage-3.1.10` — 9 files

This stage is behind-the-scenes database housekeeping. It runs as part of the project’s migration history, the ordered set of database changes applied during deployment so the current code finds the data layout it expects. Most of these files remove old records from retired features, like clearing abandoned boxes from a storeroom.

The early cleanup migrations delete saved extension data that should no longer be used: page alerts, the retired YC extension, and several old iMessage records for project bindings, claim codes, confirmation replies, phone opt-ins, and receipts. The YC cleanup goes further by removing credentials and permissions and marking YC pages and sources as removed, so the rest of the system no longer treats them as active.

The Sweep and Daily Brief files deal with an old side branch of migration history. One creates the original table for daily brief sweep editions. Another moves that model toward a newer application table after deleting old linked data safely. A later migration merges that old branch back into the main history without changing tables. The final one drops the now-unused Daily Brief tables, while keeping rollback instructions.

#### [Memory and Default Index Extension Migrations](stage-3.1.11.md) `stage-3.1.11` — 18 files

This stage is behind-the-scenes setup for data storage. It is made of database migrations, which are step-by-step instructions for creating or changing tables when the system is installed or upgraded, and undoing those changes if needed. The default index migrations create the chunk table for searchable text pieces and their vector embeddings, then update it so the same chunk can exist in different workspaces. The memory migrations build the memory system in layers. They start with tables for facts and memory pages, then add memory kind, confidence, workspace ownership, and faster lookup indexes for consolidation and inventory screens. Later migrations add “as of” time, copy page times onto old records, and create clearer provenance links back to pages and page revisions. Other steps widen who a memory can be for, separate a fact from its source pages, and add a retired marker so old memories can stay deliberately inactive. The final migrations expand memory classes with sections and overviews, then add shared member profiles for what a workspace knows about people.

#### [Publishing, Skill, Source Trigger, and Web Extension Migrations](stage-3.1.12.md) `stage-3.1.12` — 16 files

This stage is behind-the-scenes upgrade work. It is made of database migrations, which are step-by-step instructions for changing stored data safely when the system is updated. The hosted-site migrations build and extend the table for published sites: first storing each site with its workspace and conversation, then adding generation counters, homepage-agent links, safer workspace visibility for old homepages, preview image details, share-card image details, deploy generation numbers, and an optional source manifest.

The skill migrations do the same for user-created skills. They create the skill table, move skills from workspace ownership to agent ownership, add routing-card metadata read from existing skill files, then move skills back to workspace-level storage while removing duplicates.

The source migrations create a structured source-trigger table, move old subscription data into it, and add a delivery setting so each trigger knows how it should send updates.

The web migrations clean up old chat data by adding missing chat rows and moving chat titles into the main conversation table, so the core system has one shared place to read them.

#### [Workflow, Objective, Report, Research, Eval, and Sample Extension Migrations](stage-3.1.13.md) `stage-3.1.13` — 9 files

This stage is behind-the-scenes setup for optional extensions. It is made of database migrations: small instructions that create or change database tables so features have places to save their information. They usually run during installation or upgrade, before the main work can use those features.

The evaluation environment migration builds storage for fixture-like email inbox and calendar data. The monitor migration adds scheduled checks connected to a workspace, conversation, and agent. The objectives migrations create storage for goals, steps, proof that steps were completed or blocked, and later step checks; the second one adds an “independent” flag so a step can be marked as able to run in parallel with others. The report digest migrations store summaries of published reports, plus records of reports that were checked and found unchanged. The research migration records web sources seen or fetched during a conversation. The sample migration gives the sample extension one note per workspace. The scheduled tasks migration stores pauses, so an agent conversation can resume later.

#### [Coding Extension Review Migration History](stage-3.1.14.md) `stage-3.1.14` — 4 files

This stage is the coding extension’s database change history for code review features. It is behind-the-scenes support, used when the system is installed or upgraded so old stored data still matches the current code. A database migration is a numbered instruction that changes the shape or contents of the database step by step.

The first migration creates the original review inbox model. It adds places to store pending review items and each review run, and makes sure a review run can point safely to one specific turn in a workspace. The second migration connects a review run to the conversation used during that review, so the system can find the discussion that produced it. The third migration cleans up the inbox table by removing its direct conversation link, because that link no longer belongs there. The fourth migration moves older inbox records into the newer source-trigger and conversation flow, then removes the old review tables. Together, these files show the feature moving from a custom review inbox toward the newer shared conversation-based design.

### [Workspace Onboarding and Seating](stage-3.2.md) `stage-3.2` — 5 files

This stage is part of startup and early workspace use. It prepares a new installation so people have a place to work, and it controls who is allowed to sit in that workspace and use the assistant. The package marker simply makes the onboarding folder importable by the rest of the program.

The main setup file runs first-time onboarding. It creates the first workspace, the first administrator, and the main assistant agent, then lets installed extensions add their own setup steps. The control file is the trusted doorway used by the Rust control plane, the outer service that coordinates the system. Through it, that layer can create or find workspaces, list sign-in choices, count workspaces, and read invitations without copying sensitive rules.

The seats file is the gatekeeper. It creates members, grants or removes seats, decides which members the agent may answer, and prevents the last seated administrator from being removed. The seed file adds a realistic demo conversation to permanent storage, like a showroom display, so the web portal can be tested with rich sample data.

## [Extension Discovery, Manifest Loading, and App Registration](stage-4.md) `stage-4` — 52 files

This stage is shared setup that happens before the system can use optional apps and extensions. It is like opening a toolbox, checking which tools are allowed, reading each label, and putting the tools in the right drawers. The manifest model defines the label format: how an extension declares tools, web routes, jobs, credentials, hooks, agents, skills, backends, and object types. The loader is the main doorway. It finds installed extensions, filters them, reads their manifests, and registers what they add. The store is the small app-store layer that searches the extension catalog and records installs or removals in the lockfile.

Built-in workspace apps and packaged agents seed new workspaces with known apps such as Chat, Code, Wiki, Issues, and helper agents. Extension-provided hooks, backends, objects, and stores let add-ons react during work and add services such as connectors or Redis-backed storage.

The individual manifest files are registration forms for concrete extensions: Slack, web portal, sites, documents, monitors, scheduled tasks, sources, iMessage, debugger, gbrain, self-improvement, skill creation, and UFO shell. Small __init__ files simply make some extension packages importable.

### [Built-In Workspace Apps and Packaged Agents](stage-4.1.md) `stage-4.1` — 24 files

This stage is shared setup for the apps and helper agents that come built into a workspace. It is not where chat messages are sent, code is edited, or research is performed. Instead, it is the catalog and installer layer that tells the host system what should exist when a workspace is created or extended.

The collaboration and knowledge app packages register user-facing tools such as Chat, Meetings, Radar, Wiki, Artifacts, and Documents-style shared spaces. Their manifest files act like ID cards, describing each app’s name, home screen, agent, permissions, and setup needs. The engineering and operations packages do the same for Code, Issues, and Metrics, so repository work, issue tracking, and operational reporting can be installed consistently.

The delegated worker packages add specialist helpers, such as browser, coding, research, and brief-writing agents, along with the tools and instructions they need. Finally, extension provisioning turns installed extension promises into real workspace agents, while keyed connector declarations describe API-key-based service access. Together, these pieces seed a workspace with ready-known apps, agents, tools, and safe connection rules.

#### [Collaboration and Knowledge Workspace App Packages](stage-4.1.1.md) `stage-4.1.1` — 10 files

This stage is shared setup for the built-in workspace apps. It is not the main work loop of the apps themselves. Instead, it provides the labels and registration cards the host system reads when a workspace is being assembled, so these apps can appear in the right place with the right abilities.

Each app folder has two simple parts. The __init__.py file is a package marker: it tells Python, the programming language used here, that the folder can be imported by other code. It does not run the app. The manifest.py file is the important “ID card” for the app. It describes how the host should install and show that app.

The Artifacts manifest registers a searchable shelf for shared files and hosted sites. Chat registers the workspace chat home screen and its agent. Meetings declares its account needs, scheduled work, and home screen skill. Radar registers its workspace overview surface. Wiki declares the wiki agent, its permissions, and homepage skill. Together, these files make the collaboration tools discoverable and installable.

#### [Engineering and Operations Workspace App Packages](stage-4.1.2.md) `stage-4.1.2` — 6 files

This stage is behind-the-scenes setup for three built-in workspace apps: Code, Issues, and Metrics. These apps are not the main work loop by themselves. Instead, they tell the UFO host what tools are available when a workspace is installed or prepared.

Each app package has two simple parts. The __init__.py file is just a marker that tells Python, the programming language used here, “this folder is a package you can import.” It is like putting a label on a drawer so the rest of the system can find it. It does not run app logic.

The manifest.py file is the important registration card. The Code manifest declares the app for repository work, including its agent, skills, and setup needs. The Issues manifest declares an agent for issue tracking, its permissions, schedule, and setup. The Metrics manifest declares an operational metrics agent, its purpose, schedule, setup, and skill files. Together, these manifests let the host discover and install the workspace apps in a consistent way.

#### [Delegated Worker and Workflow Extension Packages](stage-4.1.3.md) `stage-4.1.3` — 6 files

This stage is shared behind-the-scenes support. It does not run the main app by itself. Instead, it packages optional “extensions,” which are add-on bundles that teach the system how to hand work to specialized helper agents. A helper agent is like a delegated worker: the main agent stays in charge, but can ask a browser, coding, research, or brief-writing worker to do a focused job.

The brief pipeline package has an __init__.py file that simply makes the folder importable and gives it a short description. Its manifest.py is the real setup card: it declares three brief-writing helpers and a skill folder that explains how to use them in sequence. The browser manifest declares a browser subagent, browser tools, and prompt instructions for when browsing should be delegated. The coding package also has a simple __init__.py marker, while its manifest lists coding agents, tools, skills, GitHub access settings, and a web route for repository work. The research manifest adds research tools, agents, prompts, saved skills, and shared conversation data. Together, these files let the host system discover and plug in extra workers.

#### [Extension Provisioning and Connector Declarations](stage-4.1.4.md) `stage-4.1.4` — 2 files

This stage is shared setup support for extensions, which are add-on packages that give a workspace new abilities. It sits between “an extension is installed” and “the workspace can actually use it.” The provisioning file is the installer’s careful hand. It reads the agents promised by installed extensions and creates matching real agents inside the workspace. It also avoids trampling over local edits. If a member has changed an agent, an extension update should not blindly replace that work.

The keyed connectors file declares a special kind of connector: services reached with plain API keys. An API key is a secret text token used to prove access to another service. This file tells the system what keys are required, where those keys should be sent, and what safe instructions the resulting agent should follow. Together, these pieces let packaged extension capabilities become usable workspace agents, while keeping service access clear and user changes protected.

### [Extension-Provided Objects, Hooks, Backends, and Stores](stage-4.2.md) `stage-4.2` — 9 files

This stage is shared behind-the-scenes support for add-ons. It is how UFO learns what extra abilities are installed, and how those add-ons can react while the system is working. The core hooks file is the traffic controller: during a user turn, it runs extension callbacks before or after important actions, combines their answers, and decides whether a failed or slow callback should stop the action or just be recorded.

Several files are registration cards, called manifests. The Composio, connectors, Pipedream, Redis hub, and report-digest manifests tell the host what each extension provides, such as connector tools, login flows, web redirect routes, Redis-backed hubs, scheduled jobs, rebuild tools, and workspace object types. The Redis hub package file simply makes that extension importable.

The Slack hooks file adds special reactions around Slack connector use, such as setting the right bot identity and removing old connection buttons. The sample extension acts like a test model of the whole extension system, touching many public plug-in points so the project can prove they work together.

## [Server Startup, Route Mounting, and Control Surfaces](stage-5.md) `stage-5` — 1 files

This stage is the front door for the Python service. It belongs to startup: the moment when the service reads its settings, connects to the things it depends on, and makes its web addresses available. The main file, `core/src/ufo/serve.py`, is the entry point, meaning it is the place the process starts from.

During startup, it loads configuration, opens database connections, and starts or connects to background workers that do jobs outside the main request flow. It then wires in extensions and mounts HTTP routes. A route is the rule that says, “when a request comes to this web address, send it to this code.” These routes make many surfaces reachable: the main web app, Slack and iMessage integrations, terminal and debugger tools, memory exploration, billing, artifact downloads, OAuth login callbacks, and private operator control APIs. In short, this stage assembles the service’s control panel and public doorways before normal traffic begins.

## [Inbound Request, Surface, and Session Handling](stage-6.md) `stage-6` — 34 files

This stage is the live system’s set of front doors. After the server has started, it receives people and outside services through many paths: the browser portal, Slack, iMessage, terminal clients, shared site links, object APIs, sign-in flows, and downloads. Its job is to turn those incoming requests into safe, recognized workspace actions.

The web portal and workspace APIs serve the browser app. They sign members in, load chats and settings, stream agent replies, check who may access which agents, and support admin-style pages. Chat, terminal, and external messaging surfaces do the same kind of translation for Slack, iMessage, and command-line clients: they verify the sender, map outside messages into workspace conversations, and send replies back out. Workspace object request handling is the common front desk for things like agents, members, credentials, sources, memories, monitors, reports, and hosted sites, with permission checks for each.

The surfaces package file simply lets this group of code be imported. The hosted-sites surface handles public share links, shows allowed pages safely in frames, and lets creators control link access.

### [Web Portal and Workspace APIs](stage-6.1.md) `stage-6.1` — 8 files

This stage is the web-facing front door for a workspace. It runs during normal use, after the system is started, and provides the authenticated HTTP routes that the browser app calls. In simple terms, these routes are the guarded doors between a signed-in member and the workspace’s chats, agents, files, settings, memory, usage, and admin screens.

The main hub is surface.py. It serves the web app, signs members in, lets them chat with agents, streams replies as they arrive, and exposes the portal panels. panels.py connects web actions like button clicks and form submits to the project’s usual chat-style action system, and prepares safe settings data for editing agents. audience.py is the rulekeeper: it decides which members may see or talk to which agents, and gives admins tools to change access. listings.py provides reliable paging for long lists, so browsing does not skip or repeat items. community.py fetches public skill listings from skills.sh and reshapes them for display. openai_login.py guides members through OpenAI device-code sign-in and stores their credential. starters.py creates cached personalized start-screen suggestions. __init__.py simply makes this folder importable.

### [Chat, Terminal, and External Messaging Surfaces](stage-6.2.md) `stage-6.2` — 10 files

This stage is the system’s set of doorways to the outside world. It is mostly shared, behind-the-scenes support for the main work loop: people type in Slack, iMessage, or a terminal, and this code turns those outside events into the system’s normal ideas of members, conversations, messages, files, mentions, and replies.

The core surface bridge is the trusted gatekeeper. It lets an external app prove who is speaking, open or find a conversation, stream an active response, and deliver the final answer safely. The Slack surface checks Slack requests, accepts messages, handles installs and buttons, and sends replies or files back. Slack mentions are cleaned up so stored messages are readable, then changed back into Slack’s special codes when notifications are needed. The iMessage cloud code talks to Spectrum Cloud, refreshes access, and converts remote events into simple messages; the iMessage surface maps those into UFO conversations and sends replies back. The UFO terminal surface turns server events into simple commands for a shell client. Redis terminal streaming lets terminal sessions and work running on different servers find each other and exchange control messages or larger data.

### [Workspace Object Request Handling](stage-6.3.md) `stage-6.3` — 14 files

This stage is the system’s front desk for workspace objects. It is used during normal operation whenever a person, tool, or agent asks to list, read, create, change, delete, or run an action on something in the workspace. Each file teaches the shared object system how one kind of item behaves and who may touch it.

Core files expose built-in objects: agents can be created, edited, archived, or restored; members can be viewed or changed with permission checks; the workspace summary is read-only; conversations can be inspected when allowed; credential slots show whether secrets are filled without revealing them; extensions and chat surfaces are visible but not editable.

Extension files add more object kinds to the same front desk. Sources, source triggers, synced pages, and gbrain sources describe outside content and how it is refreshed or shared. Memory records and profiles are readable only by allowed users. Monitors show scheduled watches that can wake an agent and can be stopped. Reports expose digest runs. Hosted sites can be inspected, shared, changed, or unhosted. Together, these files make many different features feel like one consistent workspace inventory.

## [Turn Admission, Conversation Queueing, and Live Attachment](stage-7.md) `stage-7` — 9 files

This stage is the system’s intake and live-observation area for agent work. It begins when a person or internal event asks for something, then turns that request into a durable “turn,” which means one cycle of agent work that can be stored, claimed, run, and finished safely.

Admission and Queue Coordination works like traffic control. It decides whether a new request should start a new turn, join a turn already in progress, wait in line, pause because spending limits were reached, or be rejected. It keeps conversation turns ordered, prevents two workers from running the same turn, and wakes the next queued turn when it is safe to run.

Live Turn Streaming is the viewing window while that work runs. It sends live updates such as text, tool activity, costs, and final status to connected clients. A local hub handles updates inside one process, while Redis Streams let other processes follow along too. Late or reconnecting viewers can still learn how the turn ended.

The two package files simply make these runtime folders importable; they add no behavior themselves.

### [Admission and Queue Coordination](stage-7.1.md) `stage-7.1` — 4 files

This stage is the traffic control system for conversation work. It sits at the front of the runtime and also supports the main work loop. Its job is to make sure each incoming message becomes the right kind of “turn,” meaning one agent work cycle, and that turns run safely, in order, and only once.

The admission file is the front door. Under a lock, so two requests cannot make conflicting choices, it decides whether to create a new turn, attach the message to one already running, pause because spending limits were reached, or reject it. The ambient reply file adds a courtesy check: if a message in a thread does not clearly ask for the agent, it can avoid starting unnecessary work.

The dispatch file is the turnstile. It starts the next waiting turn for a conversation only when that conversation has no turn already running. The queue runner is the worker. It claims queued turns, prepares the runtime, runs the agent engine, records failures, and wakes whatever should run next.

### [Live Turn Streaming](stage-7.2.md) `stage-7.2` — 3 files

Live Turn Streaming is the system’s “watch it happen” layer during the main work loop, when an agent is producing a response. It sends small live updates, like new text, tool activity, cost changes, replies, and final status, to any user interface that is watching.

The core hub is the local message room for one running turn. Publishers drop updates into it, and readers can join, leave, and rejoin without blocking the work or missing the most recent events. The Redis stream hub extends this across multiple server processes. Redis Streams act like a shared conveyor belt, so one process can publish live frames and another can read them. These frames are temporary live signals, not the permanent final answer.

The hub tail ties everything together for a client watching one turn. It listens to the live hub, but also checks the database so late subscribers or reconnected terminal clients can see the correct ending, even if the turn completed, failed, or paused elsewhere.

## [Per-Turn Runtime Environment Assembly](stage-8.md) `stage-8` — 31 files

This stage is part of the main work loop. It happens before each agent turn, like setting a workbench before someone starts a task. The system decides what the agent can read, what tools it may use, which model will answer, what skills are loaded, and what files or browser access are available.

Prompt, Skill, and Tool Catalog Construction builds the agent’s instruction pack. It renders the system prompt, adds shared reply rules, declares safe tools, loads reusable skills, lists available models, and prepares the catalog for spawning helper agents. It also keeps these pieces traceable so the same turn can be understood later.

Sandbox and Browser Session Preparation builds the safe place where actions happen. It creates or resumes the conversation workspace, chooses where commands run, and sets up browser access without exposing real secrets or crossing sandbox boundaries.

The agent setup file checks whether an installed agent is still missing things, such as connected accounts, credentials, or scheduled-task setup. If something is missing, it prepares clear guidance so the assistant can ask the user for what is needed.

### [Prompt, Skill, and Tool Catalog Construction](stage-8.1.md) `stage-8.1` — 15 files

This stage prepares the “workspace desk” an agent uses for a single turn. Before the model starts working, the host environment is assembled: prompts, tools, skills, setup files, and any safe changes from environment documents. These documents are stored by hash, meaning the exact same contents can be replayed later.

The prompt renderer fills in the final system message and fingerprints it so changes are traceable. Delivery rules add shared writing guidance for replies and subagent reports. The tool registry checks that every callable tool has a clear name, safe description, and valid declaration, while object views decide which actions are safe to show.

The skill system reads reusable instruction folders, registers them, loads selected ones, and avoids duplicates. User-created skills are saved in a persistent skill store, while skill selection ranks or shortens saved skills so the prompt does not overflow. A model-catalog skill lists available AI models from live registry data. The spawn catalog creates up-to-date help text for delegating work to subagents. Package marker files simply make these areas importable by the rest of the system.

### [Sandbox and Browser Session Preparation](stage-8.2.md) `stage-8.2` — 15 files

This stage prepares the safe workspace and browser access a tool needs before it starts doing work. The conversation workspace code is the front door to the private /workspace folder, so files can be opened, saved, listed, resumed, and cleaned without mixing conversations. The session code defines the common sandbox “doorway” for commands, files, skills, and network routing, while the selector chooses the carrier: local machine, Docker, E2B cloud sandbox, or a user’s connected terminal. Each carrier knows how to create or resume a workspace and run commands there.

The browser side uses a common browser contract so the rest of the system can ask for Chrome without caring where it lives. Chrome may run inside the sandbox, through Browserbase as a hosted browser, or through another backend. Proxy, cache, and preview settings keep network access on approved paths and avoid exposing real secrets. The execution environment adds only safe placeholder credentials. The client-binary helper finds the built UFO client to copy or run. File path and package setup pieces make these modules importable and keep all tool activity inside the intended sandbox boundary.

## [Model Harness and Agent Execution Loop](stage-9.md) `stage-9` — 10 files

This stage is the main work loop for one agent turn. It is the part that takes a request, asks the AI model what to do, streams the reply as it arrives, runs any tools the model asks for, and repeats until there is a real final answer.

The agent loop is the basic machine: ask the model, show safe visible text, collect tool requests, run those tools, then ask again with the new results. The engine wraps that loop in stronger guardrails. It makes sure a turn is not run twice after a crash, does not lose queued messages, does not spend past limits, and does not answer before all needed input is included.

Model Round Streaming is the adapter layer. It talks to different model services and turns their varied streaming formats into one standard event flow. Transcript Compaction and Long Conversation Control keeps long chats within the model’s reading limit by summarizing older history while preserving recent detail. The package marker simply lets the harness code be imported by the rest of the system.

### [Model Round Streaming](stage-9.1.md) `stage-9.1` — 5 files

This stage is part of the main work loop: it is what happens when UFO asks a model for one answer and receives it piece by piece as it is being written. Different model companies speak slightly different “streaming” languages, so this stage acts like a set of adapters that turn them into UFO’s single standard stream of events.

The Anthropic adapter talks to Claude, manages credentials, retries, prompt caching, reasoning blocks, tool requests, and usage counts. The OpenAI adapter does the same for OpenAI-style services, including both Chat Completions and the newer Responses API. The OpenRouter extension adds another doorway to many hosted models, and can also expose image and video generation tools that save files and record cost.

While text is arriving, the replies helper watches for special hidden “reply-to” markup and prevents it from leaking to the user. Finally, the rounds runner is the coordinator. It starts one model reply, displays safe text as it streams in, gathers tool calls, reasoning, timing, and usage, then hands the rest of the system one clean result.

### [Transcript Compaction and Long Conversation Control](stage-9.2.md) `stage-9.2` — 2 files

This stage is behind-the-scenes support for long-running conversations. Language models can only read a limited amount of text at once, called the context window. When a conversation grows too large, the system must shrink the older history without losing the thread.

The harness context file acts like the traffic controller. It watches the transcript size, decides when it is getting too long, picks which older messages are safe to compress, and starts the replacement process. It makes sure the newest messages stay untouched, because they are usually the most important for the next response.

The runtime compaction file does the careful shrinking work. It turns selected older parts of the transcript into a summary, checks that the summary is acceptable, and stores it in a form the rest of the system can use as part of the conversation. Together, these files let the system keep talking to outside AI models during long sessions while preserving recent detail and keeping earlier facts available in shorter form.

## [Tool Dispatch, Sandboxed Work, and External Actions](stage-10.md) `stage-10` — 96 files

This stage is the agent’s action dispatcher. It sits in the main work loop, after the model asks to do something, and turns that request into a safe, specific tool run. Built-in runtime tools handle local work such as shell commands, file edits, asking the user, sharing files, and spawning helper agents, while sandbox rules keep that work inside approved boundaries. Connector and credential tools let the agent use outside services without handing private access keys to ordinary code. Browser, research, site, and document tools provide a workbench for web pages, publishing, searches, PDFs, and Office files. Workspace object tools update saved records such as tasks, monitors, prompts, skills, and todos.

The bridge files connect these parts. tool_bridge.py lets code inside a sandbox request approved tools through the live parent turn, so permissions and history stay intact. runtime/tools/bridge.py defines the message format for listing, describing, and running those bridged tools. harness/tools.py batches ordered work safely. runtime/tools/__init__.py marks the shared tool package and its purpose.

### [Built-In Runtime Tools and Workspace Files](stage-10.1.md) `stage-10.1` — 10 files

This stage is shared behind-the-scenes support for the agent’s built-in tools. It is the layer that lets an agent safely act on the real workspace instead of only talking. The main bridge is builtins.py, which offers tools for shell commands, file edits, file sharing, questions to the user, helper agents, skills, and account setup. Each tool runs through a controlled tool context from context.py, so it only gets approved access to sandboxes, files, credentials, billing, and previews.

Several parts keep this safe and understandable. containment.py checks that file paths stay inside allowed folders, even with tricky shortcuts called symlinks. sandbox/protocol.py defines the simple command messages used to run shell or Python work in an isolated sandbox. tasks.py keeps a journal for long-running commands, so users can check, follow, or stop them later. workspace_changes.py records file changes after a turn, while file_changes.py defines a shared path length limit. activity.py turns raw tool calls into friendly user-facing labels. The REPL extension adds persistent JavaScript and Python workbenches, and __init__.py simply identifies the tools package.

### [Connectors, Credentials, and Provider Tool Brokers](stage-10.2.md) `stage-10.2` — 20 files

This stage is the system’s bridge to outside services such as GitHub, Slack, iMessage, and many app platforms. It supports setup, when a user connects an account, and the main work loop, when agents use approved tools. Its main job is to keep credentials, meaning private access keys, out of ordinary code paths while still letting agents act on a user’s behalf.

The Composio and Pipedream brokers are like service desks for large catalogs of third-party tools. They create login links, check connections, discover available actions, run them, proxy requests, and handle files returned by those tools. The generic connector layer turns connected accounts and external tools into normal workspace objects, so they can be listed, shared, attached to agents, revoked, or safely called. It also supports MCP servers, which are outside tool providers that publish a standard tool list. The app-specific setup pieces handle direct integrations: verifying GitHub App installations and creating short-lived tokens, guiding Slack setup and search, and connecting iMessage numbers with proper ownership checks.

#### [Composio Connector Broker and Dynamic Providers](stage-10.2.1.md) `stage-10.2.1` — 7 files

This stage is shared behind-the-scenes support for using Composio, an external service that connects users to many third-party apps and runs their tools. It sits between the project’s normal connector system and Composio’s hosted login, tool catalog, and tool execution features.

The client is the main doorway to Composio. It creates connection links, checks whether an account is connected, searches available tools, uploads files, and runs tools. The provider adapts the project’s usual sign-in flow to Composio’s hosted, sometimes delayed account-connection process. The broker is the central switchboard: other parts of the system ask it to discover tools, execute them, manage files, or send safe provider requests. The resolver lets the system use Composio toolkits by name, without registering each one in advance, while still checking that the requested provider is allowed. The proxy turns normal web requests into Composio proxy calls, so secret provider tokens stay hidden. The MCP session makes a single tool-router call over HTTP and returns a simple dictionary. The package file only makes these pieces importable.

#### [Pipedream Connector Broker and Proxy](stage-10.2.2.md) `stage-10.2.2` — 5 files

This stage is shared behind-the-scenes support for connecting UFO to outside apps through Pipedream. Pipedream acts like a trusted middle desk: users approve access on Pipedream’s hosted pages, and UFO can use that access without storing private tokens itself.

The package marker file only makes this extension importable. The broker is the main control point. It translates Pipedream actions into tools the rest of UFO can discover, shows what inputs they need, runs them for the right connected account, and makes any output files available afterward. The client is the low-level messenger to Pipedream Connect. It creates consent links, verifies which user owns a connected account, lists available actions, and sends action-run requests. The provider adapts UFO’s normal “connect an account” flow to Pipedream’s redirect-based approval process. The proxy supports direct-style calls to outside services: UFO sends a request to Pipedream, Pipedream adds the secret token, and UFO receives the response as if it had called the service itself.

#### [Generic Connector Objects and External Tool Surfaces](stage-10.2.3.md) `stage-10.2.3` — 4 files

This stage is shared behind-the-scenes support for letting agents work with outside services without the rest of the system needing to know each service in detail. It turns connected accounts and external tools into normal workspace items, so they can be inspected, shared, attached to agents, revoked, or disconnected in the same way as other objects.

The package marker file simply makes the connector code importable. The objects file is the “front desk” for connected accounts: it shows accounts like GitHub or Slack as workspace objects and checks who owns them and which agents are allowed to use them. The tools file is the “switchboard”: an agent can ask what connector tools are available, call one safely, move files between the workspace and the connector broker, trim oversized encoded results, and add clear attribution when posting to Slack. The MCP file adds another doorway, letting agents discover and call tools from workspace-configured MCP servers, which are outside services that publish tool lists in a standard format.

#### [App-Specific Connect and Credential Setup](stage-10.2.4.md) `stage-10.2.4` — 4 files

This stage is the “connect the outside services” part of the system. It usually happens during workspace setup, before the main work can use GitHub, Slack, or iMessage safely. Its job is to prove that the right person is connecting the right account, then store or create the credentials the rest of the system will use.

The GitHub connection file links a workspace to the UFO GitHub App. It does not trust a typed installation number by itself. Instead, it asks GitHub to confirm that the signed-in user really has access to that App installation. The GitHub App helper then turns that approved installation link into a short-lived GitHub access token, which is safer than keeping a permanent key. If no App installation is connected, it can fall back to a member’s saved token.

The iMessage tool connects a member’s phone number by checking the requester, reserving the number, assigning a shared texting line, and giving opt-in steps. The Slack tools guide an administrator through Slack setup and provide Slack search once connected.

### [Browser, Research, Sites, and Document Automation](stage-10.3.md) `stage-10.3` — 55 files

This stage is the system’s outside-world workbench. It is used mostly during the main work loop, with some shared support behind the scenes, whenever an agent needs to browse the web, research information, publish a site, or inspect and modify documents. The browser pieces form a full remote-control stack: entry-point files define allowed actions, DevTools session code talks to Chrome through its control channel, and page-reading/input code finds buttons, fields, and text before clicking, typing, scrolling, or downloading. Cloud browser and research providers add hosted browsing, web search, and page fetching when a local browser is not the right tool.

The site tools are like a small publishing shop. They copy source files into a safe workspace, track site records, then build, preview, deploy, and publish pages. The document tools cover both viewing and editing. Renderers turn documents or PDFs into page images and text, PDF utilities fill or place form content, and review scripts save issues and write comments back into PDFs, PowerPoints, or spreadsheets. Separate DOCX, PPTX, and XLSX command-line tools unpack, repair, recalculate, annotate, and rebuild Office files. A writing subagent supplies focused prose help when needed.

#### [Browser Extension Entry Points and Action Contracts](stage-10.3.1.md) `stage-10.3.1` — 5 files

This stage is the front door between an agent and the local browser engine. It is shared support used whenever the agent wants to control or inspect a browser, rather than a one-time startup or shutdown step. The package marker files, __init__.py in ufo_ext_browser and bua, make these folders importable by Python and describe where browser-use tools and the browser subagent pieces live. The actions.py file is the rulebook for browser moves. It lists the actions the agent is allowed to request, such as click, type, scroll, wait, or screenshot, and defines what information each request must include. This helps catch unclear or invalid commands before they reach the browser. The errors.py file defines a browser-specific error for made-up references, such as an element that does not exist. The tools.py file is the working adapter. It exposes useful tools like opening pages, reading content, uploading files, and saving downloads, then translates those tool calls into browser-engine requests and returns results in a form the agent can use.

#### [Browser DevTools Connection and Session Lifecycle](stage-10.3.2.md) `stage-10.3.2` — 9 files

This stage is the browser control room. It is used during the main work loop, whenever the agent needs to open pages, inspect them, click, type, switch tabs, run page scripts, or collect files. It also handles setup and cleanup of the live connection to Chrome.

At the outside, backend.py offers the per-turn browser tools and opens the browser connection only when needed. session.py is the central desk for one Chrome session, coordinating all browser abilities through that connection. tabs.py manages the tab list and turns actions like open, close, switch, and navigate into Chrome DevTools Protocol messages. Chrome DevTools Protocol is Chrome’s built-in remote-control channel.

cdp.py runs that channel over one WebSocket, sending commands, receiving replies, and routing browser events. wire.py checks the JSON messages at the boundary so bad data is caught early. runtime.py safely runs JavaScript inside the page and reports script failures as normal errors. settle.py waits until a page action has really finished, while ignoring unrelated background noise. downloads.py watches and completes downloads, including PDFs. dialogs.py prevents pop-up dialogs from blocking the automation.

#### [Browser Page Reading, Targeting, and Input Execution](stage-10.3.3.md) `stage-10.3.3` — 8 files

This stage is the browser automation “eyes and hands” used during the main work loop. It reads what is on a web page, identifies the right target, and turns model instructions into safe browser actions. page.py and content.py make a live page understandable by converting it into readable text or an accessibility tree, which is a structured outline of controls like buttons, links, and fields. find.py helps search that outline and checks that chosen element labels really exist before anything is clicked or typed.

Once a target is known, coordinate.py translates between screenshot positions seen by the AI model and real browser pixels, so actions land in the right place. computer.py performs the actual input, such as clicks, typing, and scrolling, then returns a fresh screenshot and safety notes. keys.py builds accurate keyboard events, including shortcuts and special characters. forms.py handles form filling and file uploads through Chrome’s debugging connection. fixup.py acts like a proofreader, cleaning up small mistakes in model-issued actions before they reach the browser.

#### [Cloud Browser and Web Research Providers](stage-10.3.4.md) `stage-10.3.4` — 4 files

This stage is shared behind-the-scenes support for agents that need the web. It does not run the main reasoning loop itself. Instead, it gives that loop safe “tool handles” for outside services, much like adding a phone book and a remote-controlled browser to a workspace.

The Browser Use extension connects the project to a hosted cloud browser. Through its browser_task and wide_browse tools, the system can ask an external service to open sites, click around, and gather results without launching a local browser on the user’s machine.

The Perplexity extension connects to Perplexity as a search and page-reading service. With a user-provided API key, it can run web searches or pull readable text from a specific web page.

The research package marker simply makes the research extension importable by Python. The research tools file is the main switchboard. It defines tools for web search, page fetching, and specialized searches like images or academic papers, then routes each request to the configured backend.

#### [Hosted Site Source, Registry, and Publishing Tools](stage-10.3.5.md) `stage-10.3.5` — 4 files

This stage is the workshop for hosted websites. It supports the main work of taking site files, preparing them in a safe workspace, and turning them into preview or public links. The small __init__.py file simply makes this folder usable as a Python package, so the rest of the system can import its parts.

source.py is the moving crew. It copies a site’s saved source files from long-term storage into a sandbox, which is an isolated work area where code can be edited or built without touching the rest of the system. It also supplies the page-building toolkit needed by app pages, so they use the current extension version.

store.py is the registry desk. It records each hosted site’s name, owner, visibility rules, and where the site lives, such as a running port or stored static files.

tools.py is the control panel used by the agent. It builds, previews, deploys, publishes, and connects a site as an agent homepage. It checks ownership, safety, visibility, and whether the site is ready before creating stable hosted links.

#### [Document Rendering and PDF Form/Layout Utilities](stage-10.3.6.md) `stage-10.3.6` — 4 files

This stage is shared document support. It sits behind the scenes when the system needs to look at a document, show its pages, or prepare a PDF form for automated use. The document renderer is the safety gate. It sends a document to a separate rendering service, asks for only a limited number of page images and text, and checks that the returned images are valid. This keeps oversized, damaged, or strange files from disrupting the rest of the system.

The PDF tools are small command-line helpers, meaning they can be run as standalone programs. The render tool converts PDF pages into PNG images, like taking a clear picture of each page. The form-fill tool works with PDFs that already contain fillable fields: it can find the fields, save their names and positions to JSON, and fill them using JSON values. The layout tool helps with PDFs that are not truly fillable. It inspects page layout, draws preview boxes, and places text at chosen spots so answers appear in the right places.

#### [Document Review State and Annotation Scripts](stage-10.3.7.md) `stage-10.3.7` — 7 files

This stage is the document review “memory and feedback” area. It supports the review process while it runs, and then helps write the results back into the original files. The scripts folder is made importable by its __init__.py file, which is just a marker with no real work of its own. constants.py keeps the agreed file names for the saved review state and the review log, so every script looks in the same place. models.py defines what a review issue looks like, such as where the problem is and what comment should be shown, and turns it into readable feedback.

manage_state.py is the tracker. It is run from the command line and records the review’s progress, from outline through checking, issue finding, and final submission. It saves state in JSON, a simple structured text format, and writes a log of actions. The annotation scripts then act like delivery tools: annotate_pdf.py adds PDF highlights and notes, annotate_pptx.py inserts PowerPoint comments, and annotate_xlsx.py adds Excel cell comments.

#### [Document Writing Subagent Definition](stage-10.3.8.md) `stage-10.3.8` — 2 files

This stage is a small behind-the-scenes setup point for the document extension. It does not do the main document processing itself. Instead, it makes sure the system has a clearly defined helper available when it needs prose written or edited.

The package marker file, `__init__.py`, is like putting a label on a folder so Python can recognize it as part of the program. Because of that label, other parts of the system can import document-extension code from this folder.

The main working piece is `subagent.py`. It defines a dedicated writing subagent, which is a smaller assistant started by the larger system for one focused job. In this case, the job is drafting and improving text. The file spells out the subagent’s name, which model it should use, what instructions it follows, what tools it may use, and what its inputs and outputs should look like. Keeping this definition separate helps the system call on a prose-writing helper reliably without mixing it up with document review or command-line processing tools.

#### [Office DOCX Command-Line Utilities](stage-10.3.9.md) `stage-10.3.9` — 4 files

This stage is a set of command-line tools for working on Microsoft Word DOCX files behind the scenes. It is not the main user interface. It supports document automation by turning a Word file into parts that can be edited safely, changing those parts, and building the Word file again.

The process often starts with accept_changes.py, which makes a clean copy of a document by accepting all tracked edits. It uses LibreOffice “headless,” meaning LibreOffice runs invisibly in the background. Next, unpack.py opens the DOCX package like a zip file and expands it into a folder of XML files. XML is structured text that Word uses internally. This script also simplifies messy Word markup so later steps are easier.

comment.py works on that unpacked folder. It adds the hidden records Word needs for a new comment or a reply, then reports the small marker that still must be placed in the document text. Finally, pack.py gathers the folder back into a DOCX file and removes extra whitespace so the package stays neat.

#### [Office PPTX Command-Line Utilities](stage-10.3.10.md) `stage-10.3.10` — 5 files

This stage provides small command-line tools for working with PowerPoint .pptx files outside the main application flow. A .pptx file is really a zip package full of XML files, images, and links; these tools let developers inspect, edit, rebuild, and fix that package safely. The empty __init__.py file simply makes the scripts folder importable as normal Python code. unpack.py opens a .pptx like a suitcase: it unzips the contents into a folder, formats the XML so humans can read it, and protects curly quotes in a form XML will not misread. pack.py does the reverse, turning that folder back into a usable .pptx while compacting the XML without changing slide text. repair.py fixes known problems made by pptxgenjs, a library that generates presentations, so PowerPoint is less likely to complain or alter spacing. slides.py is the workbench tool: it can remove unused parts, add a slide, or make thumbnail contact sheets for quick visual review.

#### [Office XLSX LibreOffice Utilities](stage-10.3.11.md) `stage-10.3.11` — 3 files

This stage is behind-the-scenes support for working with Excel .xlsx files through LibreOffice, without showing the normal LibreOffice window. It is used when the system needs a spreadsheet to be updated and saved before another tool reads it.

The package marker file, __init__.py, is like a label on a toolbox. It tells Python that the scripts folder can be imported by other code, but it does not do any work itself. The _soffice.py helper is the shared adapter for LibreOffice. It supplies the common settings needed to run LibreOffice in “headless” mode, meaning in the background with no desktop interface. It also knows where LibreOffice keeps user macros on Linux and macOS.

The recalc.py script is the active worker. It opens a workbook in LibreOffice, tells it to recalculate all formulas, saves the updated file, and checks whether any spreadsheet errors remain. Together, these files let the system refresh spreadsheets reliably before later processing or validation.

### [Workspace Object Mutations and Domain Tools](stage-10.4.md) `stage-10.4` — 7 files

This stage is shared behind-the-scenes support for changing “workspace objects,” meaning saved records such as tasks, monitors, prompts, skills, and todos. The central piece is the object system, which acts like a front desk. It checks that each record has the right shape, applies visibility rules, and sends each create, update, delete, read, or action request to the extension that owns that object type.

Several domain tools plug into that front desk. Scheduled tasks expose reminders and timed workflows as normal objects, and can pause work until a person replies or a timer runs out. The monitor tool lets an agent watch an outside thing, like a build or inbox, by running a command once, saving that first result, and only continuing if it works. The todo extension gives conversations a visible checklist the agent can update.

Prompt governance protects agent instructions by requiring proposed changes and approval, and by blocking stale approvals from overwriting newer prompts. Object scope quietly records which agent an action is acting as. The skill package introduces runtime skills that agents can create, own, and use.

## [Delegation, Subagents, Objectives, and Multi-Step Workflows](stage-11.md) `stage-11` — 16 files

This stage is for work that cannot be finished with one simple tool call. It sits behind the main work loop and helps the agent break big jobs into smaller jobs, track them, and recover if work pauses or takes a long time.

One half is delegation. The system can start “subagents,” which are smaller worker agents with their own instructions and tools. A dispatcher checks that the worker is allowed, packages the task, starts the child conversation, waits for the answer when needed, and returns it to the main agent. A recovery piece looks for finished child work that was saved but not delivered, so results are not lost. Special workers handle browsing, research, deep research, wide parallel research, and website building.

The other half is objective and workflow tracking. It acts like a project notebook and checklist. The agent can define a goal, record steps and evidence, run promised checks, delegate parts, and only mark work complete after verification. Specialized workflows reuse this machinery for building apps, auditing them, and producing written briefs through outline, draft, and critique steps.

### [Subagent Dispatch and Recovery](stage-11.1.md) `stage-11.1` — 9 files

This stage is shared behind-the-scenes support for delegation. It lets the main agent hand off focused jobs to smaller “subagents,” which are child agents with their own instructions, tools, and expected input and output. The fallback profile in profiles.py is the default worker plan when no special one is supplied. subagents.py is the dispatcher: it checks that a child agent is allowed, validates the data being sent, creates the child conversation, queues the work, waits if needed, and returns the result to the parent. delivery.py is the safety net, sweeping for completed child turns whose results were saved but not delivered, so the parent does not wait forever.

The extension files plug in specialized workers. The browser files define a browser subagent and tools for sending one browsing session or many parallel visits. The research files define normal and deep research workers, plus wide_research, which fans out many research tasks and safely collects results into one JSON file. The sites files define a website-building worker and the build_website tool that sends site creation work to it.

### [Objective and Workflow State Machines](stage-11.2.md) `stage-11.2` — 7 files

This stage is the project’s “job tracker” for work that takes more than one agent turn. It supports the main work loop and also helps after a pause, when the agent wakes up and must remember unfinished goals. The objectives manifest tells UFO to load this extension and to bring long-running objectives back into view at the start of each turn. The objectives tools let an agent plan a goal, record step evidence, run checks, delegate parts to other workers, and mark progress. They do not accept “I’m done” by itself; they re-run the promised checks first. The objectives store is the durable notebook that keeps goals, steps, attempts, evidence, and verification results.

Several specialized workflows use the same idea. The application builder guides a worker from a member’s request to a deployable UFO app page, with tools for design, writing, checking, repair, and publishing. The application audit is its quality gate, deciding pass or repair from browser, layout, accessibility, and product checks. The brief pipeline defines a simpler writing machine: outline, draft, then critique.

## [Source Sync, Memory, Indexing, and Knowledge Ingestion](stage-12.md) `stage-12` — 77 files

This stage is the system’s knowledge intake and recall pipeline. It runs mostly behind the scenes after accounts, folders, or API keys are connected. Its job is to bring in outside or workspace information, keep it fresh, and store it so agents can search it later.

External Source Connectors and Feed Sync are the front gate. Each connector knows how to talk to one service, such as Slack, Google, Git, or a CRM, and converts that service’s records into a common page-like format. Shared helpers handle routine web work like logins, retries, and “pagination,” which means fetching long result lists a page at a time.

The sync worker in `core/src/ufo/runtime/sources/sync.py` is the pump. It pulls documents from configured sources, saves the newest content, notes what changed, and hands those changes to indexing.

Memory, Embeddings, and Search Index Backends are the library shelves. They clean and condense information, turn text into meaning-based search numbers, and store chunks in searchable databases or external index services.

### [External Source Connectors and Feed Sync](stage-12.1.md) `stage-12.1` — 65 files

This stage is the system’s intake hub for outside information. It runs mostly behind the scenes during sync work, after a user connects an account or provides an API key. Its job is to reach into many services, read their records, and turn them into the same kind of searchable pages.

The connector groups are the provider-specific “adapters.” They cover local Markdown folders and Git pages, Google and Microsoft workspaces, Slack, CRMs, sales and ads tools, project trackers, developer tools, HR systems, finance platforms, forms, scheduling, documents, and support desks. Each one knows that service’s API, which is its internet doorway for data, and translates provider-specific records into a common flow.

The shared source runtime is the engine under those adapters. The connector contract defines what every adapter must provide. The REST helper handles repeated web tasks like authentication, retries, and paged results. The backend runs connectors as sync jobs, tracks where to resume, and limits runaway fetches. The registry is the address book of known providers. The connected-account logic creates private feeds when accounts are linked, while the direct-auth path supports connectors that use user-provided API keys.

#### [gbrain Markdown, Folder, and Git Page Sources](stage-12.1.1.md) `stage-12.1.1` — 3 files

This stage is the intake point for gbrain pages that live outside the system as Markdown files. It is shared support used when the system needs to sync or import pages, rather than part of the user-facing work loop. Its job is to find Markdown files, read them safely, and turn them into standard page records that the rest of gbrain can search and sync.

The folder source treats a local directory like a small library. It walks through the chosen folder and makes one page from each Markdown file it finds. The Git source does the same idea for a GitHub repository, which is a remote code-and-file storage place. It fetches the repository’s Markdown files, but checks whether the repository has changed so it does not download the same content again unnecessarily.

The pages module is the shaping tool. It decides which files count as Markdown pages, confirms they are readable text, and picks friendly titles, so both folder and Git sources produce consistent page records.

#### [Google, Microsoft, and Chat Workspace Connectors](stage-12.1.2.md) `stage-12.1.2` — 10 files

This stage is part of the system’s data-gathering work loop. It connects to workplace tools and turns emails, chats, meetings, documents, calendars, and files into records the rest of the system can store, search, and recall later. The Google helper separates two different API refusals: “you do not have permission” and “try again later because the quota is full.” That lets the system skip only what it truly cannot read.

The Gmail, Google Calendar, Docs, Drive, Meet, and Sheets connectors each read one Google workspace area. Gmail formats messages, Calendar reads events and attendees, Docs extracts document text, Drive reads files plus details like comments and permissions, Meet gathers transcripts and notes, and Sheets reads spreadsheet tabs and rows.

On the Microsoft side, Outlook reads mail, contacts, folders, conversations, and calendar changes, while Teams reads teams, channels, chats, and messages through Microsoft Graph, Microsoft’s API gateway. Slack does the same for Slack users, channels, messages, threads, and authors. Together, these connectors act like adapters, making many different services look like one steady stream of searchable content.

#### [CRM, Sales, Marketing, and Advertising Connectors](stage-12.1.3.md) `stage-12.1.3` — 10 files

This stage is a set of behind-the-scenes connectors that let the system bring in customer, sales, marketing, and advertising data from outside services. Each connector knows how to talk to one provider’s web API, meaning the provider’s online doorway for requesting data, and how to turn the answers into steady “pages” of records the rest of the sync system can store and search.

ActiveCampaign, HubSpot, Attio, Salesforce, Apollo, Klaviyo, and Mailchimp cover CRM and marketing records such as contacts, companies, deals, campaigns, lists, events, email activity, tasks, notes, and conversations. Salesforce also tracks deleted records so the local copy can be cleaned up. Facebook Ads, Google Ads, and Instagram cover advertising and social data, including accounts, campaigns, ads, media, stories, and performance numbers.

Together, these files act like adapters for different plug shapes. Each outside platform has its own layout and paging style, but these connectors translate them into a common stream of clean records for the larger system to recall later.

#### [Project, Product, Developer, and Operations Connectors](stage-12.1.4.md) `stage-12.1.4` — 11 files

This stage is a set of behind-the-scenes connectors. Their job is to visit outside tools used by engineering, product, and operations teams, read their data through each tool’s API, and turn it into a common stream of records the rest of the system can store, search, and recall. An API is a structured doorway that software uses to ask another service for information.

The work-tracking connectors cover Asana, ClickUp, Jira, Linear, monday.com, and Wrike. They read things like projects, tasks, issues, comments, users, boards, teams, folders, goals, workflows, and custom fields. The knowledge and collaboration connectors read Confluence spaces and pages, Notion pages and databases, and GitHub repositories, issues, commits, users, and organizations. The operations connectors read PagerDuty incidents, services, schedules, and on-call data, plus Sentry projects, errors, events, members, and releases. Together, they act like adapters for different power outlets, making many tools feed one shared memory system.

#### [HR, Recruiting, and Workforce Connectors](stage-12.1.5.md) `stage-12.1.5` — 6 files

This stage is the set of “readers” for HR and recruiting tools. It is used during the main sync work, when the system reaches out to outside services and copies their latest people-related records into a common flow. Each file is an adapter, like a plug shape for one vendor’s socket. Ashby, Greenhouse, and Recruitee focus on hiring data: candidates, jobs, applications, interviews, offers, departments, and users. BambooHR focuses on employee operations, including employee profiles, time off, timesheets, field definitions, and reports. Deel reads workforce and contractor data such as contracts, forms, payslips, timesheets, and tasks. Rippling reads company, worker, and team records. These services expose data through web APIs, meaning structured requests over the internet. The connector files know which API addresses to call, how to request the next page when results are split into chunks, and how to turn each service’s different response format into steady batches of records that the wider sync system can process the same way.

#### [Finance, Billing, Banking, and Commerce Connectors](stage-12.1.6.md) `stage-12.1.6` — 9 files

This stage is part of the system’s behind-the-scenes syncing work. Its job is to connect to finance and commerce services, fetch business records from their web APIs, and reshape them into common “streams,” meaning steady lists of records the rest of the product can store, search, and revisit.

Each file is a connector for one outside service. Brex and Ramp read spend-management data such as card transactions, expenses, users, vendors, budgets, bills, and receipts. Chargebee, Recurly, and Stripe cover subscription and payment systems, pulling records like customers, invoices, subscriptions, payouts, and checkout sessions. Mercury reads banking data, especially accounts and transactions. QuickBooks and Xero read accounting records such as accounts, bills, payments, customers, vendors, and invoices, while handling each service’s own paging and update rules. Square reads commerce data, including customers, payments, orders, catalog items, locations, and inventory.

Together, these connectors act like adapters for different plug shapes: each provider has its own API style, but this stage turns them all into the same kind of syncable record flow.

#### [Structured Apps, Documents, Forms, Scheduling, and Support Connectors](stage-12.1.7.md) `stage-12.1.7` — 8 files

This stage is a set of behind-the-scenes connectors that let the system read from structured business tools during a sync. A connector is like an adapter plug: each outside service has its own shape, but the rest of the system expects a steady stream of simple records it can store and search.

Airtable reads bases, tables, and records from spreadsheet-like databases. Calendly brings in scheduling data such as users, event types, bookings, invitees, groups, and memberships. DocuSign reads envelopes and templates, finds the correct regional service address, and gives documents useful titles. PandaDoc reads documents, templates, and contacts without changing anything in PandaDoc. Typeform gathers forms, responses, workspaces, themes, images, and webhook settings. Freshdesk, Intercom, and Zendesk cover customer support systems, turning tickets, conversations, contacts, companies, agents, help articles, forums, and activity logs into the same kind of syncable pages. Together, these files translate many different APIs, or web service interfaces, into one common flow for later recall.

### [Memory, Embeddings, and Search Index Backends](stage-12.2.md) `stage-12.2` — 11 files

This stage is shared behind-the-scenes support for long-term memory and search. It lets the system save useful information, turn it into searchable form, and bring it back later when an agent or operator needs it. The memory extension manifest is the front door: it registers the recall tools, automatic hooks, background jobs, and the web surface. The core memory and indexing files define common “contracts,” meaning standard shapes that any memory store or search index must follow.

The memory store records memories, searches them, tracks source-page snippets, and runs background indexing jobs. The condenser cleans raw saved material into clearer facts, summaries, profiles, and tidy pages, so memory does not become a pile of duplicates. The OpenAI embedding extension turns text into number lists, called vectors, that help search by meaning. The default index stores and searches chunks in SQLite or PostgreSQL, while the Turbopuffer extension can send the same kind of chunks to an outside search service. Events keeps shared event names and limits consistent. The surface file gives operators a read-only memory explorer.

## [Commit, Presentation, Media, and Outgoing Delivery](stage-13.md) `stage-13` — 15 files

This stage happens near the end of a turn, after the assistant has produced results. Its job is to make those results durable, visible, and deliverable. It is like the publishing desk: it saves the record, prepares the attachments, and sends the finished reply to the right place.

The transcript file is the safety lock for the conversation record. It reads and writes the saved transcript, but only lets the record move forward. That prevents an older or incomplete version from overwriting a newer one.

Artifacts, Files, Previews, and Hosted Media handles the things a reply may point to: files, images, downloads, and hosted sandbox websites. It checks access, creates safe links, validates previews, and can capture site screenshots for cards.

Surface Replies and Conversation Display Slots handles what people actually see in the web app or connectors like Slack, iMessage, and terminal clients. It sends final replies and prepares side panels for sources, artifacts, sites, tasks, previews, and other extras. Together, these parts turn completed work into a trustworthy conversation view.

### [Artifacts, Files, Previews, and Hosted Media](stage-13.1.md) `stage-13.1` — 9 files

This stage is shared support for anything the system saves, shows, or serves as media from a conversation. It is not the main thinking loop. It is the backstage machinery that turns files and hosted sandbox sites into safe links, previews, and downloadable artifacts.

The artifact code defines a shared file object: it can be listed, inspected, deleted, copied back into a workspace, or downloaded. The download route serves the actual bytes only when a signed link proves access, and can help signed-in teammates refresh old links. Image preview code checks that preview files are real, safe-sized images before showing them, while the preview data model records where a preview lives and its dimensions.

For hosted sites, the ingress host code creates special per-conversation web addresses so each site is isolated, like giving every exhibit its own locked room. The ingress server checks signed access, then serves stored site files or forwards live requests to the sandbox. The site previewer asks an outside screenshot service to capture a PNG of a live site and saves it as an artifact. The share card code combines UFO branding with that screenshot for social previews.

### [Surface Replies and Conversation Display Slots](stage-13.2.md) `stage-13.2` — 5 files

This stage is about what the user sees after the assistant has done its work. It sits near the end of the main work loop, when finished replies and related information are sent back to places like the web app, Slack, iMessage, a terminal, or other connectors. It also prepares “conversation slots,” which are side panels or display areas that hold useful extras.

The shared slot model defines the safe shapes that extensions may show, such as artifacts, sources, tasks, sites, automations, image previews, todos, and reports. The research file saves web sources found during research, trims them to a safe size, and lets the conversation later show a durable Sources panel. The scheduled tasks file turns stored future actions into a small Automations summary. The sites file checks what hosted sites the viewer may see, then exposes safe public links and counts.

Slack has one extra detail: connector messages can include a small footer saying where they came from. The attribution code adds and recognizes that footer without mistaking it for a new user message.

## [Scheduled Jobs, Maintenance Loops, and Autonomous Wakeups](stage-14.md) `stage-14` — 27 files

This stage is the system’s background alarm clock. It runs work that should happen later or repeatedly, without waiting for a user to click anything. Some of this happens during normal operation, like scheduled prompts, paused conversations, monitor checks, and source-change wakeups. These pieces watch the clock or shared data, safely claim work that is due, wake the right conversation or workflow, and then either finish it or schedule the next run.

Other parts are quiet maintenance. They retry missing file previews, clean old homepage settings, create short report digests, and run cautious self-improvement checks for agents. Those checks gather past failures, test possible prompt changes, and only move forward when the results look safe.

The shared job machinery ties this together across workspaces. The candidates file is the safe doorway for finding which workspaces might have pending work, without mixing their private data. The jobs file turns background job definitions into real scheduled runs. It makes sure each job runs inside the correct workspace, avoids duplicate runs, and can recover after a restart.

### [User-Visible Scheduled Work](stage-14.1.md) `stage-14.1` — 13 files

This stage is the system’s alarm clock and watchman. It is behind-the-scenes support, but its results are visible to users because it wakes conversations, agents, or workflows at the right moment. Scheduled tasks are recurring prompts. Their storage code creates, edits, cancels, lists, claims, runs, and reschedules them, while the runner checks the clock, finds due tasks, fires each one once, and moves it to its next time. The cron helper reads “cron” schedules, a compact calendar format, and calculates the next run. The scheduled-fire helper gives each run a clear identity, so the system knows which task and time caused it.

Pauses are one-time waits for a conversation. Their storage claims due pauses safely, and the pause runner wakes them or clears them if a human already replied. Monitors repeatedly run saved shell checks until something changes, fails too often, or times out; their runner decides when to probe and when to send the final alert. Source triggers wake conversations when shared sources change. Visibility rules protect private task details. The package files simply make these extensions importable.

### [Offline Product and Quality Maintenance](stage-14.2.md) `stage-14.2` — 12 files

This stage is background upkeep. It runs outside the main user conversation flow, like a night crew that fixes missed work, rebuilds summaries, and checks whether agents can be improved safely. The preview renderer looks for recently shared files that still lack preview images and asks the preview service to try again. The homepage cleanup removes an old workspace homepage setting when the chat app should now be the main landing page.

The report digest pieces keep scheduled reports easy to skim. The digest code defines what a short entry should look like, trims it, and removes repetition. The writer finds newly published reports without summaries, asks a model to summarize them, and stores the result.

The self-improvement extension reviews past agent activity. Its package file identifies the extension. The corpus builder collects failed tool-use conversations and splits them into learning and test examples. The cron job runs the periodic check. The model wrapper gives a simple way to ask the language model for text or turns. The proposer suggests prompt changes, while replay, evaluation, and gate test those changes cautiously before any human-approved proposal moves forward.

## [Per-Run Cleanup, Recovery, and Teardown](stage-15.md) `stage-15` — 3 files

This stage is the system’s safety and cleanup crew. It runs after a piece of work finishes, is stopped, fails, or when a process disappears unexpectedly. Its job is to make sure no rented resources, running tasks, or live updates are left hanging. It also helps recover work safely so the same job is not picked up twice.

runtime_instance.py keeps each running server process visible to the rest of the fleet, like a sign saying “I am alive and responsible for this work.” It also runs background sweeps that find abandoned or cancelled work and move it toward a safe final state.

stop.py handles a user or system request to stop an active conversation turn. A “turn” means one unit of agent work. It checks that stopping is allowed, triggers cancellation, and notifies live listeners that the turn is over.

cancellation.py contains the shared cancellation steps. It stops the active workflow first, then records in the database that the turn was cancelled, so the system’s saved state matches what really happened.

## [Durable Data, Blob Storage, and Persistence Contracts](stage-16.md) `stage-16` · (cross-cutting) — 7 files

This stage is the system’s long-term memory. It is shared behind-the-scenes support used during startup, normal request handling, agent turns, background jobs, and shutdown. Its job is to make sure data is saved in forms that other parts of the system can safely read later.

The database side is centered on tables.py, which defines the application’s tables and rules for SQLAlchemy, a library that maps Python objects to database rows. db.py is the safe doorway into that database, making sure each operation is tied to the correct workspace so different customers or projects do not get mixed together. records.py defines the agreed shapes of important events and messages, such as agent turns, user questions, terminal results, and credential requests. transcript.py does the same for saved conversations and summaries.

Large files are handled by blob.py, which hides whether bytes are stored on a local disk or in S3-style cloud storage, while keeping workspace files separate from deployment-wide files. durability.py protects saved workflow data from code changes. schema/__init__.py simply makes the schema folder importable.

## [Security, Identity, Credentials, Egress Policy, and Billing](stage-17.md) `stage-17` · (cross-cutting) — 29 files

This stage is shared safety and accounting support used across the whole system. It is not one single startup or shutdown step. Instead, it acts like the building’s security desk, network guard, and cashier during everyday use.

Authentication and Signed Links proves who someone is and creates safe temporary links. It uses signed tokens, which are small messages with a tamper-proof stamp, for login, shared pages, sandbox app access, and file downloads. It also completes outside account sign-ins, such as Anthropic login.

Network, Secret, and Spend Enforcement decides what an agent is allowed to use. It checks the active workspace and agent, unlocks only approved connected accounts, protects stored credentials, tells the network proxy which outside hosts are allowed, and records paid usage so billing limits and balances are respected.

The directly assigned files add privacy boundaries inside conversations. untrusted.py marks outside text as information to read, not commands to follow. audience.py labels who a message is meant for, such as private or shared. subjects.py gives a standard way to say who may read content, like one member or the whole workspace.

### [Authentication and Signed Links](stage-17.1.md) `stage-17.1` · (cross-cutting) — 11 files

This stage is shared behind-the-scenes support for proving identity and building safe links. It is used whenever the system must let a browser, extension, or download request in without keeping a server-side session for every case. At the center, token_signing.py makes small signed tokens, meaning data bundled with a tamper-proof stamp. bearer.py uses that stamp for login tokens that prove a user belongs to a workspace, while sdk/bearer.py exposes the checking side safely to extensions. surface_token.py signs links for shareable “surface” routes before normal session details are known.

Sandbox access uses the same pattern. ingress_token.py creates short-lived permission slips for one sandbox app port, and ingress_url.py packs the public host, port, conversation identity, and token into the browser link. artifact_url.py does this for stored files, creating expiring download links tied to one artifact.

External sign-in is the human-facing part. runtime/surfaces/cli.py receives the provider’s return request and serves the logo, callback_page.py builds the result page, and anthropic_login.py connects Anthropic accounts only after Anthropic confirms the credential works.

### [Network, Secret, and Spend Enforcement](stage-17.2.md) `stage-17.2` · (cross-cutting) — 15 files

This stage is the system’s safety and money gatekeeper during normal work. Before an agent calls an outside service or spends paid model tokens, it checks which workspace and agent are active, so secrets and charges stay with the right customer. Workspace and agent scope provide that identity boundary. Grants and connector access let members connect accounts like OpenAI, Anthropic, Gmail, GitHub, Composio, or Pipedream, refresh expiring tokens, and allow only chosen agents to use them. Credential handling encrypts secrets and chooses the right provider host or key without exposing it.

For network access, the egress rule and resolver code turns a run’s signed token, model choice, extensions, stored credentials, and grants into concrete “allowed hosts and secrets” rules. The egress control API is the private desk the Rust proxy calls to ask what traffic is allowed, fetch safe credential injections, and report usage.

Billing files price token use, track prepaid micro-dollar balances, enforce limits, debit accounts, roll up reports, and export billing data. The Metronome extension connects those records to Metronome, Stripe, admin chat tools, and billing pages. Package files only make these modules importable.

## [Extension SDK, Public Contracts, and Generated Protocol Types](stage-18.md) `stage-18` · (cross-cutting) — 65 files

This stage is the shared public “contract layer” of the system. It is not where the app starts, runs a conversation, or shuts down. Instead, it defines the stable names, data shapes, and import paths that other code depends on, especially extension authors and generated network code.

One part sets core runtime rules: safe extension context objects, object naming, and input/output contracts. Several SDK facade parts act like clean front doors under ufo.sdk, exposing connectors, credentials, HTTP helpers, jobs, manifests, sources, surfaces, tools, models, search, memory, browser, terminal, accounting, observability, permissions, identity, seats, skills, and other runtime features without exposing private internals. The iMessage parts define both the local provider contract and generated Protocol Buffer types, which are machine-readable message formats used for chats, messages, attachments, events, groups, polls, and service calls. The Google API proto files and package marker files make those generated imports work. Finally, ufo/sdk/__init__.py marks the SDK folder as importable, anchoring this public API surface.

### [Core Runtime Extension Contracts and Object Naming](stage-18.1.md) `stage-18.1` — 6 files

This stage is shared behind-the-scenes support for the runtime. It does not run the main conversation by itself. Instead, it defines the safe rules and common shapes that other parts rely on when extensions, agents, and built-in jobs do work.

The package marker files in host/kinds and runtime/kinds simply make those folders importable in Python, so the rest of the code can find their modules. The runtime/ext package marker defines the boundary for the extension API: what the platform gives to extensions and what extensions may provide back.

The main safety gate is context.py. It builds the “context” object handed to extensions and background jobs. This is like a limited toolbox: it allows access only to approved storage, credentials, workspace data, model calls, files, transcripts, sources, and turn calls.

object_name.py gives all runtime objects one shared naming rule and a small reference type for “this kind of object with this name.” contracts.py defines how agent inputs and outputs are checked, whether they use Pydantic models or JSON Schema.

### [Public SDK Extension Authoring Facades](stage-18.2.md) `stage-18.2` — 9 files

This stage is the project’s public front door for extension authors. It is not the main work loop itself. Instead, it is shared support used while people build add-ons for UFO. A “facade” here means a stable, simple import path that hides the messy internal folder layout, like a service desk that fetches the right specialist for you.

Each file opens one safe doorway. connectors.py exposes connector and OAuth building blocks for linking outside services. context.py provides the approved context objects extension code can use to understand where it is running. credentials.py gives controlled access to credential helpers. http.py supplies route authors with request, response, upload, form, and cookie tools without forcing them into lower-level web code. jobs.py exposes background job tools. manifest.py provides the types and constants used to describe an extension. sources.py gathers source-sync, REST connector, pagination, and sync result pieces. surfaces.py supports channels such as chat, inboxes, or terminals. tools.py exposes the approved pieces for defining UFO tools. Together, these files keep extensions stable even if the core internals move around.

### [Public SDK Runtime Capability Facades](stage-18.3.md) `stage-18.3` — 14 files

This stage is shared support for extensions and outside tools. It is not the main work loop itself. Instead, it acts like a row of clearly labeled doors into runtime features. Each file keeps a stable ufo.sdk import path, so extension code does not need to know where the real internal code lives.

The model, search, index, and memory doors expose the shapes and interfaces for talking to AI models, search services, embedding indexes, and memory search. Browser and terminal expose the public types for connecting to a browser or terminal session. Sandbox exposes the safe-execution boundary tools. Accounting and balance expose spending reports, prices, usage exports, and prepaid balance helpers. Flags exposes feature-flag helpers, which are switches for turning behavior on or off. Scheduled_fire exposes helpers for cron-like scheduled runs. O11y, short for observability, exposes logging and metrics so extensions can report what happened. Delivery_register shares the standard result-format prompt text. Untrusted exposes the shared wrapper for marking outside text as not fully trusted. Together, these files make the SDK steady even while the internal machinery can move around.

### [Public SDK Governance, Identity, and Domain Facades](stage-18.4.md) `stage-18.4` — 11 files

This stage is shared behind-the-scenes support for people building on top of the system. It creates stable public SDK doorways, so extension authors can import trusted names from ufo.sdk without depending on the project’s private internal layout. Most files here do not add new behavior. They act like labeled front desks that forward callers to the real machinery inside the runtime.

audience.py and subjects.py expose shared labels for who a conversation or disclosed data is meant for. grants.py publishes connection and permission-audit tools. authproxy.py opens the path for adding authentication backends, while operator.py exposes operator-only web session helpers. surface_token.py provides the public entry point for creating and checking permanent surface links. listings.py publishes listing and pagination tools, and objects.py exposes object kinds, types, and helpers. hub.py gathers event and hub classes for live updates. seats.py republishes seat-related tools, and skills.py republishes the building blocks used to define skills. Together, these files form the SDK’s stable outer shell.

### [iMessage Provider and Generated Service APIs](stage-18.5.md) `stage-18.5` — 9 files

This stage is shared behind-the-scenes support for the iMessage extension. It defines the “language” that the rest of the system uses when it talks about iMessage data or calls iMessage services over the network. The hand-written provider.py file is the local contract: it says what an iMessage provider must offer, and it defines the simple shapes for messages and attachments that other code can rely on.

The other files are generated from Protocol Buffers, a format for describing structured data so different programs agree on what each message contains. The attachment, chat, event, and message *_pb2.py files define the request and response shapes for those four service areas. The matching *_pb2_grpc.py files add the gRPC wiring, which is the network calling layer. They let a client call remote methods and let a server attach real implementations. Together, these files act like standard plugs and sockets: provider.py defines what this project expects, while the generated modules make sure network data fits correctly.

### [iMessage Generated Domain Protocol Types](stage-18.6.md) `stage-18.6` — 7 files

This stage is behind-the-scenes support for the iMessage extension. It does not start the system or run the main message loop by itself. Instead, it provides the shared “forms” that other code fills in when it needs to send, store, or understand iMessage data. These files are generated from Protocol Buffers, or protobufs, which are rules for packing data into a consistent machine-readable shape.

The address types file describes contact addresses and whether they can use iMessage, SMS, or RCS. The attachment types file describes shared details for sent files, such as names, file types, transfer state, and Live Photo companion data. The chat types file represents chats and changes to chats. The group types file focuses on group chat membership and other group-change records. The message types file covers messages, reactions, stickers, edits, read states, and related events. The poll types file describes poll options, votes, and poll updates. The streaming file defines a small heartbeat message, like a pulse, so streaming connections can show they are still alive.

### [Generated Proto Import Boundaries and Google API Annotation Protos](stage-18.7.md) `stage-18.7` — 8 files

This stage is shared behind-the-scenes support. It does not start the app, run the main message flow, or shut anything down. Instead, it makes a tree of generated protocol code usable by Python. A protocol buffer, or “proto,” is a structured message format used so different systems agree on what data looks like.

Most files here are small package markers named __init__.py. They are like labels on folders that tell Python, “you can import code from here.” The markers for proto, google, google.api, photon, photon.imessage, and photon.imessage.v1 open the path so the vendored iMessage protocol modules can be found reliably.

The two generated Google API files provide the actual support data. http_pb2.py defines message types for HTTP mappings, such as which web path matches a service method. annotations_pb2.py adds the special http option that generated service definitions can attach to methods. Together, the marker files provide the roads, and the generated files provide the road signs needed by later protocol code.

## [Model, Provider, Embedding, Search, and Feature-Flag Infrastructure](stage-19.md) `stage-19` · (cross-cutting) — 9 files

This stage is shared behind-the-scenes support. It gives the rest of the system one reliable way to know which AI models, search services, and feature switches are available. The models package marker simply makes the model code importable. The model spec file is the master form for describing a model: provider, price, limits, supported features, and needed credentials. The interface file defines the common request and response shape used with providers, and it safely replaces images when a provider cannot accept them.

The registry is the lookup desk. Other code asks it what a model is, what it costs, and how to create a client. The catalog fills that desk with built-in Anthropic and OpenAI model facts. The Bedrock extension adds Amazon Bedrock-hosted models in the same format, so they fit the same machinery.

The search file defines a common contract for web search and page fetching, so the system is not tied to one search provider. The flags file is the main doorway for reading feature flags, and the Flagship extension connects those switches to Cloudflare, including simple admin updates.

## [Operator, Debugger, Evaluation, and Support Utilities](stage-20.md) `stage-20` · (cross-cutting) — 8 files

This stage is shared behind-the-scenes support, not the normal path a user turn follows. It gives operators and engineers safe ways to inspect, test, and validate the system.

The operator module is the front desk for operator-only tools. It checks sign-in, lets an operator choose a workspace, and builds a “fleet directory,” a readable index of workspaces and recent conversations. The debugger surface uses that access to provide a read-only web page and JSON API, so the browser app can show conversations, turns, files, and live events. The steps module turns low-level stored execution records into a clear timeline of model calls, tool calls, and workflow steps for one turn. The debugger report tool lets an agent send one focused problem warning to engineers when it cannot fix an issue itself.

Observability code sends logs, metrics, traces, and health checks to monitoring services, while filtering sensitive data. The debugger package initializer simply makes that extension importable. The evaluation environment package and manifest define fake but realistic mailbox, calendar, code search, and business connectors, so tests use predictable data through the same connector routes as the real product.
