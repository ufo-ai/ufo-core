# System Handbook — Stage Index

Each stage below links to its full page; the paragraph is the stage's role in the system.

## [Operator entrypoints, local setup, and process launch](stage-1.md) `stage-1` — 7 files

This stage is the system’s front door. It covers the steps an operator or developer uses before UFO is fully running: creating a local workspace, packaging a deployable setup, checking sandbox support, and finally starting the server process.

The ufoctl command in cli.py is the main control panel. It lets people initialize, inspect, package, run, or repair a workspace without editing hidden files or databases by hand. onboarding.py performs the first-run setup behind that command: it creates the workspace, the first admin user, the main assistant agent, checks needed keys, and lets extensions finish their own setup. bundle.py freezes a working setup into a Docker build folder, so the same configuration can be launched elsewhere.

The sandbox validation commands check the safe execution area used later for running code. One script builds and compares sandbox images; another tests that secure proxy networking works. serve.py is the actual service launcher: it loads configuration, connects storage, extensions, sandboxes, jobs, web routes, and workers, then starts the FastAPI web server. The sample skill probe is a tiny diagnostic that confirms a skill can run.

### [Sandbox image and deployment validation commands](stage-1.1.md) `stage-1.1` — 2 files

This stage is part of the behind-the-scenes preparation that happens before the system relies on sandboxed runtime work. A sandbox is a safe, isolated place where UFO can run code without exposing the main system. These commands make sure that place is built correctly and that its network path works before real traffic uses it.

The build_template.py script is the image builder and consistency checker. An image is like a frozen setup of a computer with the needed tools already installed. This script builds the local Docker version and keeps it matched with the E2B-hosted sandbox template, so both sandbox types are made from the same recipe and do not quietly become different over time.

The proxy_gate.py script is a deployment safety test. It starts a temporary E2B sandbox, installs the proxy’s trusted certificate authority, and checks that HTTPS traffic can pass through the sandbox proxy as expected. Together, these scripts act like a workshop inspection: first confirming the sandbox machine is built right, then confirming its secure network route is ready.

## [Database schema upgrade, rollback, and extension migrations](stage-2.md) `stage-2` — 145 files

This stage is the database changeover area used during setup, deployment, and rollback. The database schema is the shape of the stored data: which tables exist, what columns they have, and how records link together. Alembic, the migration tool, applies small ordered changes so new code and old data still match.

The env.py file is the launch script for these changes. It connects Alembic to the project’s database and sets the rules for running migrations safely. From there, the core schema migrations update the main shared storage: workspaces, users, agents, conversations, messages, scheduling, credentials, billing, sources, files, memory, and agent settings. They are the main track every installation depends on.

Extension-owned migrations are separate tracks for optional features. They create and evolve tables for code reviews, evaluation data, search indexes, memories, monitors, objectives, research sources, pauses, hosted sites, skills, triggers, briefs, notes, and web chats. Together, these parts act like a careful renovation crew: the core work updates the main building, while extensions update only the rooms they own.

### [Core schema migrations](stage-2.1.md) `stage-2.1` — 108 files

This stage is the project’s main database upgrade track. It runs behind the scenes when the system is installed or updated, making sure stored data has the right “shelves” for newer code. The first migration creates the basic records for workspaces, members, agents, conversations, turns, and usage costs. Another early migration adds proposals, so agents can save suggested workspace changes.

The later groups build on that foundation. Turn and subagent migrations let conversations branch into delegated work. Scheduling and fleet migrations store future jobs, pauses, and worker process state. Source and page migrations track imported content, sync errors, ownership, and read access. Surface and inbound-message migrations support Slack, web, iMessage, titles, visibility, and message queues. Credential and connection migrations separate accounts, permissions, secrets, and network safety rules. Ledger migrations make billing records detailed enough for usage, exports, and bring-your-own-key cases. Workspace migrations cover members, limits, balances, and top-ups. Memory and artifact migrations add or retire storage for extensions, shared files, and remembered facts. Agent configuration migrations keep saved agents, models, tools, and sandbox settings usable as the product changes.

#### [Turn execution and subagent metadata migrations](stage-2.1.1.md) `stage-2.1.1` — 14 files

This stage is behind-the-scenes database upkeep. Each file is a migration, meaning a step-by-step recipe that changes what the system can store without rewriting the whole database. Together, these changes make conversation “turns” more like a tree than a simple list, so one turn can create child turns, including work delegated to subagents.

The early migrations add parent-child turn links, subagent surfaces, run guards so two workers do not run the same turn, trace links for debugging across parent and child work, and saved context such as sender or timezone. Later ones record who is speaking, who a turn or scheduled task is acting for, and which sandbox conversation contains the work. Several migrations add indexes, which are like book indexes for the database, so it can quickly find child turns or spoken member turns. The subagent migrations track display names and whether a child turn still owes a result to its parent. The final changes remember BYOK key usage and references created by a turn, supporting replay, billing, and saved outputs.

#### [Scheduling, admission, and runtime fleet migrations](stage-2.1.2.md) `stage-2.1.2` — 13 files

This stage is behind-the-scenes database preparation for work that happens later in the main system loop. These files are migrations: small upgrade steps that change the database shape so newer code has the shelves and labels it needs.

Several migrations build the scheduling system. They add scheduled tasks, planned pauses, expiration times, “last turn” tracking, per-agent task names, and a paused flag, so the system can store jobs that should run later and know when not to run them. Later cleanup moves old pause data out of the core scheduled-task table after pauses get their own home.

Other migrations describe how work enters the system. They add admission sources such as scheduled and intent, meaning a turn can be recorded as coming from a timer or inferred user goal, not only a person or internal action.

A third group supports the runtime fleet, the pool of running worker processes. It records runtime instances, allows shared workers not tied to one workspace, and removes outdated shared-fleet columns. Finally, lookup indexes act like book indexes, helping background sweeps find jobs and conversations quickly.

#### [Sources and page indexing migrations](stage-2.1.3.md) `stage-2.1.3` — 9 files

This stage is behind-the-scenes upgrade work for the database. It changes the stored shape of “sources” and “pages” as the product learns to track more about where content comes from and who may read it. First, 0008 creates the basic source and page records, so each workspace can remember discovered pages and when to sync them again. 0019 opens the source “backend” field, meaning extensions can add new kinds of sources beyond the built-in folder type. 0021 adds an error streak counter, used to slow retries after repeated sync failures. 0036 adds soft removal, marking a source as removed without erasing its history. 0044 records whether a source is shared or owned by one member. 0047 adds browsing details to pages, such as stream, title, and original timestamps. 0049 renames timestamp fields to describe page records more clearly. 0054 gives page changes a reliable revision number, like a ticket number in a queue. Finally, 0059 adds read grants, recording which agents can access which sources.

#### [Surface, inbound message, and conversation presentation migrations](stage-2.1.4.md) `stage-2.1.4` — 15 files

This stage is behind-the-scenes database upkeep. It is a set of migrations, meaning small steps that change the database shape as the product learns to support more kinds of conversation “surfaces,” or places where users talk to the system.

The early steps add Slack support, then web support, and later loosen the old rules so conversations can come from more surfaces. They add storage for shared turn files, workspace-specific surface keys, and safer separation between different workspaces. The inbound-message migrations create a waiting area for new messages, move rendered message text into its own storage, then clean up the old field. Other steps connect surface installations and conversations to the right agent, store who may see a conversation, remember the surface’s display label, and save user-facing conversation titles. Later title work records whether a title has already been summarized and removes older tracking. Mid-turn replies get their own table so partial answers can be delivered once. Listener claims record which running service is watching a surface. The final cleanup moves iMessage project binding into the newer surface-installation data.

#### [Credentials, connections, grants, and control-plane safety migrations](stage-2.1.5.md) `stage-2.1.5` — 9 files

This stage is behind-the-scenes database upgrade work. It does not run the product’s main loop directly. Instead, it changes the stored data layout so later code can safely manage accounts, permissions, privacy, and network access.

It starts by adding encrypted workspace credentials, so secrets have a dedicated protected home. It then adds grants, which record that an agent may use a provider account, and later marks whether those grants are shared. Another migration identifies the controlling member and controlling agent for each workspace, and prevents more than one main agent from being set.

The next change separates two ideas that were once mixed together: a reusable connection to an account, and an agent’s permission to use it. Sources are updated to point at the connection they rely on. Later, sharing and account labels move onto the connection itself.

Privacy and safety are covered too. One table logs when an admin views a member’s private transcript, and a later cleanup removes an unused lookup index. Finally, workspace egress rule triggers bump a counter when network-rule data changes, telling the proxy to refresh its cached rules.

#### [Ledger usage, export, and billing detail migrations](stage-2.1.6.md) `stage-2.1.6` — 12 files

This stage is behind-the-scenes database upkeep for the billing ledger, the table that records usage and cost. It happens as the system is upgraded, so later runtime code can store richer billing facts without the database rejecting them. Several migrations widen what the ledger can describe: egress data transfer, sandbox token use, image use, and video use. Others add more detail to each record. Price digests keep an audit-friendly snapshot of pricing, workspace anchoring lets some charges attach to a workspace instead of a single interaction turn, and debited amounts record the exact money actually removed from a balance. Token accounting becomes more precise in two steps: prompt and cache-read counts are split out, then input, output, cached token counts and a “bring your own key” flag are added. Export support is also added: one migration creates a ledger export tracking table, and another marks whether an export used BYOK. Finally, a workspace-and-time index makes finding ledger records for a workspace faster. Together, these changes turn a simpler spending log into a detailed billing and export record.

#### [Workspace membership, limits, and balance migrations](stage-2.1.7.md) `stage-2.1.7` — 10 files

This stage is behind-the-scenes database housekeeping. It changes the stored shape of workspace, member, and billing data as the product’s rules evolve. First, it adds spend caps, so a workspace can be limited to a certain amount of usage in a time window, and work can be parked when needed. Then it introduces seats: when members became seated, workspace seat limits, and later included seats. It also speeds up sign-in by indexing member email addresses.

The stage then moves the model away from limited seats toward unlimited members. It marks existing members as seated, removes old seat-limit fields, and clears old approval markers. Member records also gain a saved time zone, so the system can remember a person’s last valid local time setting.

On the billing side, it adds prepaid workspace balances, purchase history, automatic top-up amounts and trigger levels, and a timestamp showing when a verified card top-up first happened. Finally, it removes an obsolete “seat shipping” marker, cleaning out data for a feature the system no longer uses.

#### [Memory, artifacts, extension data, and cleanup migrations](stage-2.1.8.md) `stage-2.1.8` — 10 files

This stage is part of the database upgrade path: the step-by-step work that reshapes old stored data so newer application code can use it safely. It is mostly behind-the-scenes support, like renovating shelves in a storeroom without losing what is still needed.

Some migrations add storage. The extension store creates a small per-workspace JSON area for extensions. The first knowledge-graph migration creates tables for known “things” and their relationships, while a later migration removes those tables as the system moves to one main memory surface. The conversation change migration adds a place to record what Git, the version-tracking tool, says changed in a workspace.

Other migrations improve shared artifacts, meaning files or outputs shared through the system. One gives each artifact its own stable ID. Another adds preview data, such as a rendered first page, and checks that preview fields stay complete. A media-type fix updates old file labels to clearer modern ones.

The remaining migrations clean up retired extensions: page alerts, YC, and Exa, removing stale flags, credentials, sources, and references so the system stops treating them as active.

#### [Agent configuration, model, and provisioning migrations](stage-2.1.9.md) `stage-2.1.9` — 14 files

This stage is behind-the-scenes upgrade work for the database, the place where the system keeps long-term records. These migrations run when the software version changes, so old saved agents and conversations still make sense to the newer code.

Several changes add new knobs to agent records. Agents gain internet access permission, a reasoning mode with only valid choices allowed, a sandbox size limited to small, medium, or large, visibility for private versus workspace use, and icon fields with sensible defaults. Other changes add practical bookkeeping: conversations can remember their sandbox handle so work can resume later, provisioned agents can record which extension created them and which tools they may use, setup data can store extra JSON answers from a member, and spawn fields describe expected input, output, and owner.

The remaining migrations repair old model settings. They move agents away from retired Bedrock model IDs, turn on low reasoning where Fable requires it, and rewrite outdated Fable names to the currently usable served or OpenRouter model IDs. Together, these files keep saved agents usable as the product grows.

### [Extension-owned schema migrations](stage-2.2.md) `stage-2.2` — 36 files

This stage is part of setup and upgrades, not the normal work loop. It gathers the database migrations owned by optional extensions. A migration is a careful change to stored data, like adding or reshaping drawers in a filing cabinet. These extensions only need their storage when they are installed, so their tables live outside the core system.

The coding and sources migrations move code review work from older inbox tables into a newer trigger-based model. The memory migrations create and improve storage for saved facts, pages, source tracking, audiences, and fast lookup indexes. Search, evaluation, and research migrations add shelves for test email and calendar data, searchable text chunks, and records of sources seen during research. Automation migrations store long-running state for monitors, objectives, scheduled pauses, and daily briefs. Hosted site and web migrations preserve web conversations and track sites built from them. Skill and sample migrations add simple extension-owned examples: workspace notes and user-created agent skills. Together, these migrations let optional features keep durable state safely as the system grows.

#### [Coding review and source trigger migrations](stage-2.2.1.md) `stage-2.2.1` — 6 files

This stage is part of the system’s upgrade path, not its daily work loop. It reshapes the database so code review work can be driven by “sources,” meaning outside places such as repositories or pull requests that can trigger activity.

The first migration creates the original coding review inbox: tables for items waiting for review and records of review runs that already happened. The second connects each review run to the conversation that produced it, so the system can trace a review back to its discussion. The third changes that model: instead of tying inbox entries to conversations, it ties them to the reviewing agent, the worker responsible for a source.

The fourth migration is the bridge. It moves old review inbox records into the newer source-trigger conversation system, then removes the older review tables, while still supporting rollback. The Sources migrations build the new foundation: one creates a structured source_trigger table and imports old subscription data into it; the next adds a delivery field so each trigger records how its work should be delivered.

#### [Memory core storage and indexing migrations](stage-2.2.2.md) `stage-2.2.2` — 6 files

This stage is part of the system’s setup and upgrade path. It prepares the database, which is the system’s long-term storage, so the memory extension has a reliable place to keep and find saved information. Each migration is a small step that changes the database structure safely over time.

The first step creates the main memory table, where individual memory items are stored and linked to a workspace, meaning the project area they belong to. The second step adds memory pages, which group memories around a subject and record when each page was created. The third step enriches each memory item with its kind and a confidence score, so the system can tell what type of memory it is and how certain it is. The fourth step makes workspace ownership explicit on memory pages too, avoiding guesswork. The last two steps add indexes, like book index pages, so common lookups are fast: one for finding older active facts to consolidate, and one for showing a workspace’s memory inventory newest first.

#### [Memory provenance, audience, and source partition migrations](stage-2.2.3.md) `stage-2.2.3` — 6 files

This stage is part of upgrade and behind-the-scenes maintenance for the memory extension. It changes the database, the place where stored memories are kept, so older memory records can carry clearer meaning without losing what was already saved.

First, 0007 adds an optional “as of” time, meaning the time the memory is about, not necessarily when it was stored. Then 0008 fills that time for memories made from pages, using the page’s last update time or, if missing, its creation time. Next, 0009 gives page-based memories a proper structured field for their source page, instead of hiding that page ID in loose text. 0010 goes further and records the exact page revision, then clears old derived page memory so it can be rebuilt more accurately. 0011 expands who a memory can be for, adding room-based audiences as well as shared and member-specific ones. Finally, 0012 changes source tracking so one fact can point to several source pages, rather than pretending each source creates a separate fact.

#### [Search, evaluation, and research data migrations](stage-2.2.4.md) `stage-2.2.4` — 4 files

This stage is behind-the-scenes setup work for extension data. It is made of database migrations, which are small upgrade scripts that create or change tables where the system stores information. They usually run when the application is installed or updated, before the main work can safely use the data.

The eval environment migration creates tables for email and calendar records used when testing or evaluating environments. The first indexing migration creates a table for searchable text chunks, plus their vector embeddings, which are number-based summaries that help the system find similar text. The next indexing migration improves that table by tying each chunk to a workspace, so different workspaces can keep separate copies even if the chunk content has the same digest, or fingerprint. The research migration creates a table for source observations, recording which web pages or sources were seen during a conversation. Together, these migrations lay down the storage shelves that later search, evaluation, and research features depend on.

#### [Automation, objectives, monitors, and brief migrations](stage-2.2.5.md) `stage-2.2.5` — 5 files

This stage is behind-the-scenes groundwork for features that need to remember work over time. It is mostly made of database migrations, which are small setup scripts that create or change storage tables so the running system has reliable places to save state.

The monitor migration builds the table for scheduled checks, including which workspace, conversation, and agent own a monitor, plus its status, counters, schedule, and history. The objectives migrations create storage for goals, ordered steps, proof that steps were done or blocked, and later reviews of those steps. A later objectives migration adds a simple “can run independently” flag, so the system can decide which steps may happen in parallel without rechecking that every time. The scheduled tasks migration creates a pause table, letting a conversation go quiet and resume at a planned time. The Sweep migration stores each person’s daily brief and whether it is pending, failed, or complete. Together these tables act like labeled shelves for long-running automation.

#### [Hosted site and web conversation migrations](stage-2.2.6.md) `stage-2.2.6` — 6 files

This stage is part of upgrading the system’s database, not the day-to-day chat loop. A database migration is a controlled change to stored data, like adding new labeled drawers to a filing cabinet and moving old papers into the right places.

The hosted site migrations build and refine the records for web sites created from conversations. The first creates the hosted site table, storing its workspace, conversation, port, creator, and visibility. The second adds a required generation value, giving old sites unique identifiers before enforcing the rule. The third records which agent should serve as a site’s homepage agent, and prevents one workspace from assigning the same homepage agent to multiple sites. The fourth carefully makes proven main-agent homepage sites visible to the whole workspace, while leaving uncertain older private sites alone.

The web migrations preserve older chat data. One creates durable web chat rows for recognizable old conversations. The other moves chat titles into the core conversation table so normal conversation lists can show them.

#### [User-created skill and sample note migrations](stage-2.2.7.md) `stage-2.2.7` — 3 files

This stage is part of the system’s behind-the-scenes setup and upgrade work. It defines small database changes, called migrations, which are step-by-step instructions for creating or changing stored data structures. These migrations belong to extensions, so they let optional parts of the system keep their own data without mixing it into the core tables.

The sample extension migration creates a simple note table. It stores one text note for each workspace, like a small scratchpad attached to that workspace. It can also remove the table if the extension is rolled back.

The first skill creation migration adds a table for user-created skills. These are custom abilities or instructions that users add to the system. The table gives those skills a stable place to live.

The second skill migration refines that design. It changes skills so they are tied to a specific agent, not only to a workspace. It updates the database links and keys so the database can safely enforce that ownership. Together, these files prepare storage for user-extensible features.

## [Configuration, pack selection, and extension discovery](stage-3.md) `stage-3` — 38 files

This stage happens during startup. It decides what version of the system is being assembled before any real user work begins. First, the configuration file defines the deployment settings and stops the server early if something important is missing or unsafe. Pack recipes then act like preset modes: they choose which extensions, skills, services, and test or production pieces should be loaded.

Next, extension manifests work like registration cards. They tell the host about tools, agents, web pages, background jobs, credentials, data types, and required capabilities. The extension store lets the command-line tool search, install, or remove extension pins safely. The extension loader then finds the installed packages and turns their declarations into usable parts of the running system.

Several helpers finish the setup. Sandbox selection chooses a safe place for agent-run code, either local or extension-provided. Proxy startup helpers centralize model-provider access and privileged database setup. Governance protects agent prompt changes by requiring approval before writing them. Provisioning copies extension-shipped agents into a workspace without overwriting local edits. Together, these pieces build the system’s available abilities before the main work loop starts.

### [Pack recipes](stage-3.1.md) `stage-3.1` — 8 files

Pack recipes are startup instructions. A pack is a named bundle that tells the system which add-ons, skills, setup steps, and infrastructure choices to load before the assistant starts doing work. They work like preset modes for the same machine.

The local assistant pack turns on the main assistant features for development. The hosted assistant pack chooses the features meant for managed production-style infrastructure. The billing pack is a smaller local recipe that adds billing support so developers can test the billing setup flow without starting the full hosted setup. The assistant evaluation pack swaps in fake evaluation services and Docker sandbox support, while leaving out real external broker connections that would not fit an eval run.

Other packs shape the assistant for specific jobs. The chief of staff pack combines Slack, meetings, memory, todos, scheduling, and improvement skills into a manager-support workflow. The DSQA and GDPVal evaluation packs offer preset toolkits, from minimal core tools to search, browser, document, research, or all-in setups. The sample pack proves the public SDK path works by loading a sample extension, skill, and onboarding step.

### [Extension manifests and capability registration](stage-3.2.md) `stage-3.2` — 23 files

This stage is startup and shared support. It is where extensions introduce themselves to the host system. Each manifest is like a registration card: it lists the tools, agents, web routes, background jobs, credentials, data types, and helper instructions that an extension offers. The host reads these cards and wires everything into the right place before users or agents need it.

The agent, skill, and workflow manifests add work-focused abilities, such as browser use, coding help, document creation, research, website building, and reusable user-made skills. The connector and communication manifests connect outside services, such as Slack, iMessage, Composio, Pipedream, and content sources, including their sign-in paths and required secrets. The state, objectives, scheduling, and background-job manifests register longer-running support, such as memory, reminders, monitors, scheduled tasks, and recurring evaluation work. The host surface, backend, and API example manifests expose user-facing pages, web routes, Redis-backed services, and a sample extension used to prove the extension system works. Together, these files make the system’s possible abilities visible and usable.

#### [Agent, skill, and domain-workflow extension manifests](stage-3.2.1.md) `stage-3.2.1` — 7 files

This stage is shared setup support. It is made of extension “manifests,” which are simple declaration files the UFO system reads when an extension is turned on. A manifest is like a menu or sign-up sheet: it tells the core system which agents, tools, prompts, skills, secrets, and outside services are available.

Each file registers a different work area. The brief-pipeline manifest adds three specialist subagents and a parent skill that runs them in order. The browser manifest adds browser tools, a browser-focused subagent, prompt guidance, and a needed outside capability. The coding manifest registers coding agents, a review agent, GitHub credentials, and a GitHub installation route. The documents manifest makes document, PDF, presentation, spreadsheet, design, review, and drafting skills discoverable, plus a writing subagent. The research manifest adds research tools, helper agents, prompts, skills, and search support. The sites manifest adds website-building tools, prompts, skills, subagents, surfaces, and chat objects. The skill-create manifest lets users save, inspect, and reload their own skills later.

#### [External connector and communication manifests](stage-3.2.2.md) `stage-3.2.2` — 6 files

This stage is part of startup and behind-the-scenes setup. It is made of “manifest” files, which are like sign-up sheets for extensions. Each manifest tells the main UFO system what an outside service can do, what credentials or secrets it needs, and which web addresses or background jobs should be wired in.

The Composio and Pipedream manifests register collections of third-party app connectors. They describe how a user starts browser-based sign-in, often through OAuth, a standard way to grant access without sharing a password, and where the browser should return after consent. The general connectors manifest adds shared connector tools, connector object types, and prompt text so the agent knows how to use them. The iMessage manifest registers a messaging tool, its message surface, and required cloud credentials. The Slack manifest connects incoming Slack messages, installation routes, reply tools, secrets, and background hooks. The sources manifest registers external content providers, authentication choices, sync hooks, and retry work so connected content can be kept up to date.

#### [State, objectives, scheduling, and background-job manifests](stage-3.2.3.md) `stage-3.2.3` — 5 files

This stage is shared behind-the-scenes support. It is not where the agent does the main work itself. Instead, it is like a set of sign-up sheets that tell the host system what long-lived abilities and background routines exist, when they should run, and what tools or memory they should make available.

The memory manifest registers memory tools, automatic recall, page indexing, cleanup jobs, and search, so past information can be found and kept tidy. The monitors manifest exposes monitor objects and a tool for checking them, plus a clock-based job that runs on a schedule. The objectives manifest adds tools and reminders for long-running goals, so the agent does not lose track of plans, blockers, or unfinished steps between turns. The scheduled-tasks manifest declares task objects, a pause-and-wait tool, scheduled background jobs, and the skill instructions needed to plan timed work. The self-improvement manifest loads evaluation work and defines what happens when that recurring job runs. Together, these manifests make persistent work visible, remembered, and regularly maintained.

#### [Host surface, backend, and extension-API example manifests](stage-3.2.4.md) `stage-3.2.4` — 5 files

This stage is part of startup and shared setup. It is where small extension “registration cards” tell the host what extra surfaces and backends exist, so the main system can mount them in the right places. A manifest is just a short configuration file that says, “this extension is here, this is its version, and this is what it provides.”

The debugger manifest registers the debugger extension and tells the host to expose its web-based debugging surface. The UFO manifest does the same for the UFO surface itself, making it visible as a user-facing area. The web manifest is broader: it declares the web portal’s routes, access tools, background jobs, conversation slots, and browser home-page behavior. The Redis hub manifest plugs in Redis-based backends, meaning shared services built on Redis for hub and terminal work. The sample extension is a test model: it uses the public extension API in many ways with simple fake behavior, proving the core system can call extensions correctly.

## [Surface mounting and authenticated channel setup](stage-4.md) `stage-4` — 10 files

This stage sets up the service’s user-facing and operator-facing “doors” after the system already knows its configuration. It is part of normal running, not startup or shutdown. Its job is to make sure people, apps, and browsers can reach the right surface, prove who they are when needed, and exchange messages or files safely.

The member chat surfaces are the conversation doors. The web portal, Slack, iMessage, and command-line chat each receive messages in their own format, check identity or signatures, turn the input into a shared conversation request, and send the agent’s reply back to the same channel. The web side also serves chat pages, settings, admin views, community skill browsing, and form actions.

The operator, site, and file-serving surfaces are the viewing and inspection doors. They serve signed file downloads, read-only debugger pages, workspace memory views, and hosted site preview frames. Together, these parts act like a guarded lobby: every route opens a useful room, but only after checking the visitor’s right to enter.

### [Member chat surfaces](stage-4.1.md) `stage-4.1` — 6 files

This stage is the set of “front doors” where members talk to UFO. It sits in the main work loop: a person sends a message or clicks a form, the surface turns that channel-specific event into a common conversation turn for the agent system, then sends the agent’s answer back to the same place.

The web surface runs the browser portal. It signs people in, serves chat, streams live replies, and provides read-only pages for conversations, artifacts, settings, and admin views. The community file lets that portal browse the public skills.sh skill directory and read a selected skill’s SKILL.md. The panels file makes web forms active by converting submitted settings or actions into the same tool-style actions chat can run.

The iMessage surface receives texts and attachments, links accounts, avoids duplicate messages, and restores streams after reconnects. The Slack surface verifies Slack calls, converts messages and button/form clicks into turns, and posts replies, status, and files into the right thread. The command-line surface does the same for the ufo terminal chat screen, translating progress, files, prompts, and actions into plain text instructions.

### [Operator, site, and file-serving surfaces](stage-4.2.md) `stage-4.2` — 4 files

This stage is the system’s set of web “front doors” for people and browsers. It is not startup or shutdown code. It runs during normal use, when an operator opens a tool, a visitor views a hosted site, or someone follows a file link. Each door checks access before showing anything.

The artifacts route serves shared file downloads. It uses signed, time-limited links, like a ticket with an expiry date. If the ticket has expired, a signed-in member of the right workspace can refresh access; others cannot reach the file bytes.

The debugger surface gives operators a read-only window into one workspace’s sessions. It serves the debugger page, plus data and live update streams for conversations, turns, transcripts, files, and current activity.

The memory surface is another operator page. It shows stored workspace memory records and provides the small data endpoint the page uses to list them.

The sites surface serves public hosted-site frames. It checks whether the viewer may see the site, then safely embeds the real site from its separate hosting location.

## [Incoming event normalization, authentication, and authorization](stage-5.md) `stage-5` — 8 files

This stage is an early gatekeeper for anything coming into the system. Before a request, chat event, connector callback, form post, or account-linking return is allowed to do real work, it proves who sent it, what workspace it belongs to, and what that person or service is allowed to use. This protects the rest of the system from confused identities, stale sessions, bad signatures, or access to the wrong workspace.

The account-link and OAuth callback intake is the part that connects outside services. It sends members to approval pages, receives the callback, checks that the returned account or app installation belongs to the right user or organization, and records which agents may use the connection.

The onboarding control file is the trusted private doorway for creating or finding workspaces, adding signed-in members, and listing where a person may enter. The web audience file controls visibility inside the web portal: which members can see and chat with agents, how admins grant or remove access, and when private transcript access is recorded. Together, these parts turn messy outside signals into safe internal identities and permissions.

### [Account-link and OAuth callback intake](stage-5.1.md) `stage-5.1` — 6 files

This stage is shared behind-the-scenes support for connecting outside accounts to a ufo workspace. It is used when a member clicks “connect” for a service, approves access in a browser, and comes back to ufo. The key job is to prove the account really belongs to the right person or workspace before anything is saved or granted.

The Composio and Pipedream provider files act like front doors to hosted consent services. They send the user to the right approval page, receive the return signal, and translate that result into a connection ufo can use. The grants file is the record keeper and permission guard: it tracks who owns each connected account, which agents may use it, and how to safely finish the browser sign-in. The CLI surface provides the small “you’re done” web page after approval. The GitHub coding connector verifies that a GitHub App installation truly belongs to the current user’s organization. The iMessage tool confirms a phone number before attaching it, preventing accidental or unwanted messaging.

## [Conversation admission, queueing, cancellation, and live streaming](stage-6.md) `stage-6` — 9 files

This stage is the traffic controller for conversations after an incoming member event has been accepted. It sits in the main work path, just before and during a “turn,” meaning one user request and the system’s process of answering it. Its job is to decide what should happen next: start a fresh turn, attach the message to one already running, wait because limits are reached, cancel work, or save the message for later.

The admission code is the front door that makes this decision in one safe, locked place, so two messages do not confuse the same conversation. Ambient reply logic is the “should we speak?” filter for group chats, avoiding unnecessary work when the agent was not really addressed. Stop and cancellation code provide the brake pedal: they check permission, tell running work to halt, update records, and notify listeners. The external surface bridge connects web or chat clients to these core actions.

Live update support then acts like a broadcast system, streaming progress, reconnecting clients, and sharing updates across server processes.

### [Live turn updates and terminal coordination](stage-6.1.md) `stage-6.1` — 4 files

This stage is shared behind-the-scenes support for live communication while a turn is running. A “turn” is one user request and the system’s answer process. Its job is to keep clients updated in real time, and to recover cleanly if a browser reconnects or if work is split across several server pods.

The in-memory hub is the fast local broadcaster. It sends live text, tool activity, cost changes, replies, and final status to any client watching the turn. It also keeps a short replay log, so a reconnecting client can resume from a saved cursor instead of starting over.

The hub tail adds a safety net. It listens to the live hub, but also checks the database more slowly to confirm whether the turn really finished or was parked. This prevents a client from missing the final state.

The Redis stream hub extends live updates across multiple server processes. Redis acts like a shared message lane for temporary frames. The Redis terminal layer does the same for terminal sessions, using streams for coordination and blob storage for larger chunks of data.

## [Durable turn claiming, recovery, and engine loop](stage-7.md) `stage-7` — 4 files

This stage is the system’s main work loop for an agent turn, which is one unit of conversation work. Its job is to take work that was already admitted, claim it so only one worker runs it, and then finish it safely even if the process crashes halfway through.

The center is core/src/ufo/loop/engine.py. It runs the turn step by step: load saved state, build the context for the model, call the model, run any requested tools, add new messages, track cost, and save the final answer. It is careful not to repeat expensive model calls or tools that may have real-world effects after recovery.

core/src/ufo/loop/queue.py is the dispatcher. It pulls a queued turn from the database, prepares what the engine needs, runs it, stores the outcome, and wakes clients or parent turns waiting for the result.

core/src/ufo/durability.py is the packing and unpacking layer for saved Python objects, helping old stored data still load after code changes.

The heartbeat and cleanup sub-stage keeps this loop healthy by detecting dead workers, freeing abandoned turns, and passing cancellations to child turns.

### [Process heartbeats, abandoned work cleanup, and cancellation propagation](stage-7.1.md) `stage-7.1` — 1 files

This stage is behind-the-scenes housekeeping for the running system. While the main service is doing conversation work, it also needs to prove that each serve process is still alive, notice when work has been abandoned, and stop related work when a parent task is cancelled.

The key piece is core/src/ufo/runtime_instance.py. It acts like a check-in desk and cleanup crew. Each running process regularly records a “heartbeat,” which is a small sign of life that other parts of the fleet can see. If those signs stop, the system can treat that process as gone instead of waiting forever. The same file runs background cleanup jobs that look for turns, meaning units of conversation work, that were left marked as running after a crash or timeout. It releases or corrects that state so later conversation work is not blocked. It also carries cancellation downward: if a parent turn is cancelled, any child turns started from it are cancelled too, keeping the whole workflow consistent.

## [Context assembly, prompt construction, skills, and spawn catalog](stage-8.md) `stage-8` — 9 files

This stage is shared behind-the-scenes work that happens before each call to the AI model. Its job is to pack the model’s “briefcase”: the current conversation, the main instructions, the tools it may use, and the other agents it may ask for help.

Prompt rendering builds the final system prompt from templates. It inserts the agent’s instructions, available skills, citation rules, capability notes, and model knowledge cutoff, then records a hash so that exact prompt can be traced later. If the conversation has grown too large, compaction shrinks older history into a checked summary while leaving the newest messages intact.

Skill loading gathers reusable abilities from built-in folders, installed extensions, and user-created skill folders. It also tracks skill dependencies and protects helper files by copying only allowed content into a safe workspace. Agent setup adds a temporary skill when an installed agent still needs account connections, so the model can ask the user to complete setup. The spawn catalog lists which subagents can be started and what they require. Untrusted-content wrapping marks outside text as data, not commands.

### [Built-in and extension skill loading](stage-8.1.md) `stage-8.1` — 4 files

This stage is shared behind-the-scenes support used during a turn, when the agent needs reusable abilities called “skills.” A skill is a small bundle of instructions and optional helper files, like a recipe card with the tools needed to follow it. Some skills come with the core system, some come from extensions such as document, research, sweep, brief, sample, or scheduling features, and some are created by users.

The package marker file simply makes the core skills folder importable by Python, so the rest of the system can find it. The runtime file does the real loading work: it defines the shape of a skill, reads skill folders, follows any “this skill needs that skill too” links, and copies allowed files into a protected workspace. The model catalog file builds a live skill that lists available AI models, costs, and capabilities. The user skill store saves custom skills per workspace and agent, checks names, blocks replacing built-in skills, and keeps the registry from growing without limits.

## [Model selection, provider invocation, streaming, and accounting](stage-9.md) `stage-9` — 10 files

This stage is the system’s gateway to AI services during the main work loop. When the engine needs a model to answer, summarize, embed text, or process media, this stage decides which model record to use, finds the right provider and credentials, sends the request, and turns the provider’s streaming reply into UFO’s own standard event format.

The model catalogs and provider bridges act like a directory and a set of plug adapters. The catalogs describe available models, their limits, and prices. The registry makes them easy to look up. Provider clients for OpenAI, Anthropic, Bedrock, and OpenRouter translate UFO requests into each company’s expected format, then translate streamed responses, errors, retries, and token counts back into a common form. Embedding providers do similar work for turning text into number lists used for search and memory.

interface.py defines that common request-and-response language, including safeguards for image limits. pricing.py turns reported token usage into cost records and tags them with the exact price table used.

### [Model catalogs and provider bridges](stage-9.1.md) `stage-9.1` — 8 files

This stage is shared behind-the-scenes support for choosing and using AI models. It is like a phone book plus a set of adapters: the rest of the system asks for a model by name, and this stage knows what it costs, how much text it can handle, what provider to call, and how to translate messages to and from that provider.

The model description shape lives in spec.py, which defines the official record for a model. catalog.py fills that shape with the built-in OpenAI and Anthropic models. registry.py combines these records into one master lookup table for the rest of the app. anthropic.py and openai.py are the bridges that actually send chat requests and turn streamed replies back into UFO’s common event format, including retries, errors, and token counting. The Bedrock extension adds Amazon Bedrock Mantle models and the right client style for each. The OpenRouter extension adds extra chat models plus image and video routes. The OpenAI embedding extension turns text into number lists so indexing and memory can compare meaning.

## [Tool dispatch, sandboxed execution, artifacts, and files](stage-10.md) `stage-10` — 19 files

This stage is the system’s supervised “workshop” during the main conversation loop. When the model asks to do something, the tool registry acts like a catalog: it defines which tools exist, how they are shown to the model, and how the right one is found. The tool context is the rulebook handed to that tool. It says who the tool is acting for, which files and accounts it may use, how it can call subagents, and how it must report back.

The sandbox backends provide the safe workbench. They create a private workspace, run commands in local, terminal, Docker, or cloud containers, expose previews and ports, and block unsafe file paths that try to escape the workspace. The built-in tools are the everyday instruments: run shell commands, read and edit files, ask the user questions, collect credentials, manage checklists, and keep REPL sessions alive. Task journals remember long-running commands.

Artifacts are shared files the agent has deliberately produced for the user, with access checks. Activity messages turn raw tool calls into clear, short updates people can understand.

### [Sandbox backends and file safety](stage-10.1.md) `stage-10.1` — 11 files

This stage is the behind-the-scenes safety and workspace layer for conversations. Its job is to give each conversation a private place to work, run commands there, expose previews, and keep files from leaking outside that place. conversation.py is the front door to a conversation’s /workspace: it opens it, reuses it, and reads or writes files there. session.py gives the rest of UFO one common “sandbox” interface for commands, files, ports, and proof that a request belongs to the right conversation. containment.py is the lock on the door: it checks untrusted paths so tricks like “../” or symbolic links cannot escape the workspace. local.py runs the workspace as a normal folder for development. terminal.py uses a member’s connected terminal as the worker. The Docker and E2B extensions provide stronger isolated workers, either in local containers or a cloud container. cache.py points sandboxes at a controlled dependency cache. ingress_host.py creates safe, unique names for exposed sandbox websites. preview.py names the document-to-image preview service. file_changes.py sets a shared maximum path length.

### [Built-in interactive and stateful tools](stage-10.2.md) `stage-10.2` — 4 files

This stage supplies the agent’s everyday work tools during the main conversation loop. It is like a supervised workshop: the agent can run commands, inspect and change files, ask for help, and keep notes, but only through controlled paths that protect the host system.

The builtins file is the main tool counter. It turns an agent’s request into safe actions: running shell commands in a sandbox, reading or editing workspace files, sharing files as artifacts, asking the user questions, delegating to subagents, loading skills, and collecting credentials when setup is needed. The tasks file adds memory for long-running shell work. Instead of losing track when a command takes time, it writes a task journal with the command’s log, process id, and final result, so later checks continue the same task rather than starting over.

Two extensions add durable working state. The REPL extension lets the agent run ongoing Python or JavaScript code sessions, keeping successful code available for later steps. The todos extension gives the agent a checklist it can update and show, helping multi-step work stay organized.

## [Browser automation and website hosting work](stage-11.md) `stage-11` — 30 files

This stage is where the system works with the web as both a visitor and a publisher. It is mainly used during the main work loop, after the agent has started and needs to browse sites, test web pages, or share a site it has built. One half is the browser control center. It opens Chrome or a hosted browser, talks to it through an automation channel, watches pages load, reads what is on the screen, and turns requests like “click this,” “type here,” or “upload this file” into real browser actions. It also hides where the browser is actually running, whether locally, in a sandbox, or through a remote service.

The other half is the website preview and hosting path. After files are created, it can build the site, run a server inside the sandbox, expose that server through a safe public link, and record who owns and may access it. Together, these parts let the agent inspect existing websites, create new ones, test them in a real browser, and share them safely.

### [Chrome DevTools and browser session control](stage-11.1.md) `stage-11.1` — 24 files

This stage is the system’s browser control center. It is used mostly during the main work loop, when the agent needs to visit websites, read pages, click buttons, type into forms, download files, or take screenshots. It also includes behind-the-scenes support for getting a browser from different places.

The agent-facing tool surface is the simple front door. It lets the rest of the system ask for browser actions without knowing where Chrome is running. The DevTools transport is the communication line to Chrome, using Chrome’s automation channel to send commands and receive events safely. Session orchestration manages the live browser: tabs, page loading, pop-ups, downloads, and knowing when an action has settled.

The page inspection layer turns a real web page into something the agent can understand, including readable text, clickable element references, and screen coordinates. The action toolbox turns the agent’s requests into real mouse, keyboard, scrolling, form, and file-upload operations. Finally, the provider layer can supply different browsers, such as hosted services, remote Browserbase sessions, or Chrome inside an isolated sandbox. Together, these parts let the agent browse the web reliably while hiding most browser complexity.

#### [Agent-facing browser tool surface and browser acquisition contract](stage-11.1.1.md) `stage-11.1.1` — 3 files

This stage is the agent’s front door to the browser. It belongs to the main work loop, where an agent may need to open pages, inspect them, click or type, upload files, take screenshots, or wait for downloads. Its job is to offer these abilities in a simple, stable way while hiding the messy details of how Chrome is actually reached.

The core browser contract in core/src/ufo/browser.py is like a rental agreement for a browser connection. The rest of the system can ask for Chrome for one turn of work without caring whether it is running locally, in a sandbox, or on a remote machine.

The tools file in extensions/browser/ufo_ext_browser/tools.py names the actions the agent can call, such as “navigate” or “read page,” and connects those names to real browser behavior.

The backend in extensions/browser/ufo_ext_browser/bua/backend.py does the turn-by-turn work. It opens the browser only when needed, performs requested actions, and cleans up safely when the turn ends.

#### [Chrome DevTools Protocol transport and runtime primitives](stage-11.1.2.md) `stage-11.1.2` — 3 files

This stage is shared behind-the-scenes support for browser automation. It is the system’s “phone line” to Chrome. Higher-level code asks Chrome to open pages, inspect them, or run scripts, but this stage provides the low-level tools that make those requests safe and reliable.

The cdp.py file manages the actual connection to Chrome’s DevTools Protocol, which is Chrome’s automation control channel. It opens a WebSocket, a two-way network pipe, sends numbered commands, waits for matching replies, and forwards browser events to any code that registered interest.

The runtime.py file builds on that connection to run JavaScript inside a browser tab. It hides the raw protocol details and turns JavaScript errors from the page into normal Python exceptions, so callers can handle failures in the usual way.

The wire.py file acts like a border checkpoint. It checks that incoming JSON messages have the expected shape before the rest of the engine trusts them. Together, these files form the safe communication layer between Python and Chrome.

#### [Browser session orchestration, tabs, settling, dialogs, and downloads](stage-11.1.3.md) `stage-11.1.3` — 5 files

This stage is the live control desk for browser work. It runs during the main work loop, after Chrome is connected and while the agent is using real web pages. The central piece is session.py, which represents one browser session. It connects the tools for opening pages, reading page content, clicking, typing, filling forms, managing tabs, and watching files that may be downloaded.

The other files act like specialist helpers around that desk. tabs.py manages the browser’s tabs: it can open, close, switch, and navigate them using Chrome’s DevTools Protocol, a control channel that lets software talk directly to Chrome. settle.py decides when an action is finished enough to move on, so the agent waits for useful page changes but does not wait forever for ads or background tracking. dialogs.py deals with pop-up boxes such as alerts, confirmations, prompts, and “leave this page?” warnings, answering them quickly so the browser does not stall. downloads.py detects and waits for downloads, including cases where Chrome tries to preview a PDF instead of saving it.

#### [Page inspection, accessibility-tree parsing, and coordinate mapping](stage-11.1.4.md) `stage-11.1.4` — 4 files

This stage is the system’s page-reading layer. It sits behind the main work loop, helping the AI understand what is currently open in a browser tab and where actions should happen on the screen. It is like a translator between a living web page and the model’s simpler view of the world.

content.py is the front door for requests such as “read this page” or “search the page.” It asks the browser session for inspection data and turns it into structured page trees, readable text, or search results. page.py builds a snapshot of the page that the model can use. It gives visible elements stable references, so the model can say “click element 12” instead of guessing. It can also turn those references back into screen locations.

find.py works with the accessibility tree, a browser-made outline of buttons, links, fields, and text. It makes that outline searchable and checks that matches still point to real items. coordinate.py maps between the model’s screenshot coordinates and the browser’s real pixels, so clicks land where intended.

#### [Browser action vocabulary and input/form execution](stage-11.1.5.md) `stage-11.1.5` — 6 files

This stage is the action toolbox for driving a browser. It sits in the main work loop, after an agent decides what it wants to do on a web page and before Chrome actually receives the command. The actions.py file defines the common “language” for requests such as click, type, scroll, wait, screenshot, fill a form, upload a file, or press keys. fixup.py acts like a proofreader for those requests, correcting small model mistakes, such as typing before selecting a field or leaving out a wait time. computer.py is the main bridge to Chrome: it turns these cleaned-up actions into real mouse, keyboard, scrolling, and screenshot operations. keys.py handles the fine details of keyboard input, translating things like “Ctrl+A” or plain text into the exact key events Chrome expects. forms.py gives safer, more focused tools for filling fields and attaching files on real pages. errors.py provides a clear way to report when the agent asks for something impossible, such as a browser value that does not exist.

#### [Hosted and sandboxed browser providers](stage-11.1.6.md) `stage-11.1.6` — 3 files

This stage is behind-the-scenes support for web browsing. It gives the system several ways to use a browser without depending only on the built-in, in-process browser engine. Think of it as a set of adapters for different kinds of “borrowed browsers,” so the rest of the project can ask for browsing work in the same general way.

The Browser Use provider connects to a hosted web-browsing agent. It exposes tools such as browser_task for focused automation and wide_browse for broader browsing jobs, letting the system delegate web work to an outside service.

The Browserbase provider uses remote Chrome sessions. It can create a hosted Chrome browser, reconnect to it later, move files in and out, and clean it up when finished.

The sandbox Chrome provider runs a real Chrome inside a per-conversation sandbox, which is an isolated workspace. It starts or reuses that browser, exposes a safe control connection, and retrieves downloaded files. Together, these providers offer flexible browser choices for different safety, hosting, and file-handling needs.

### [Hosted sites and preview delivery](stage-11.2.md) `stage-11.2` — 6 files

This stage turns a website made inside a workspace into something people can view safely in a browser. It sits in the main work loop, after an agent has created files and wants to preview or share them. The tools file gives the agent practical buttons: build a site, run a local server, publish that server as a hosted link, and set it as a homepage. The delegation file adds a specialist helper, the build_website subagent, so website work can be handed off while staying in the same sandbox, meaning the same files and server remain usable.

The store is the address book and rule book. It records which conversation owns each hosted site, what sandbox port serves it, and who may view or change it. The objects file makes these records appear as normal workspace “site” items that members can list, inspect, edit, or remove. The conversation slot builds the Sites panel, showing safe display details like URLs and visibility. Finally, the ingress server is the public doorway: it checks signed links or sessions, finds the right sandbox server, and relays browser traffic to it.

## [Subagents, delegated pipelines, and long-running objectives](stage-12.md) `stage-12` — 11 files

This stage is behind-the-scenes support for work the main agent should not do alone. It is used during the main work loop when the agent needs a helper, a specialized workflow, or a longer plan that must be checked over time. Think of it as a dispatch desk: it chooses the right helper, gives clear instructions, tracks the job, and brings back results.

The specialist profiles are the job descriptions. They define helpers for general tasks, web browsing, document writing, research, website building, coding-style sweeps, and a brief-writing pipeline that splits outlining, drafting, and critique. The contracts file defines the “forms” these helpers must receive and return, so confused or badly shaped messages are caught early.

The subagents loop is the machinery that starts helpers, validates their inputs and outputs, waits for them, cancels them, or lets them continue detached. Browser delegation adds tools for single or parallel web visits. Objectives and progress verification manage longer goals like a project notebook, storing steps, evidence, and checks so progress means “proved done,” not just “someone tried.”

### [Specialist subagent profiles](stage-12.1.md) `stage-12.1` — 6 files

This stage is shared behind-the-scenes support for delegation. It defines “profiles,” which are recipes the main agent uses when it wants to spin up a temporary specialist helper. Each recipe says what the helper is allowed to do, which tools it can use, how much time or work budget it gets, and what its input and output should look like.

The core profile is the fallback helper. It can work in the shared workspace, but it cannot pass work to others, interrupt, or ask the user questions. The brief pipeline splits writing a brief into three small helpers: one makes an outline, one drafts, and one critiques. The browser profile creates a web-use helper with browser tools. The documents profile creates a prose-writing helper for drafting and editing. The research profiles create normal and deeper research helpers, with different budgets. The sites profile creates a website-building helper for making, testing, and preparing sites. Together, these profiles let the system delegate work safely and predictably.

### [Objectives and progress verification](stage-12.2.md) `stage-12.2` — 2 files

This stage is shared behind-the-scenes support for managing longer goals that may take several turns or several workers to finish. It acts like a project notebook plus a referee. The tools file gives the agent ways to create an objective, break it into steps, record what was tried, ask whether the required proof is actually true, and send independent steps to subagents, which are helper agents working on separate pieces. Its key rule is that effort is not the same as success: a step is not complete just because someone reports doing it; the required check must pass.

The store file is the durable memory for this system. It records the goal, its steps, attempts, evidence, and current check results. It keeps an append-only history, meaning new facts are added instead of old ones being overwritten. Together, the tools decide how objectives are planned and verified, while the store preserves the evidence trail so progress remains trustworthy across turns.

## [External connector discovery, credentials, and action execution](stage-13.md) `stage-13` — 20 files

This stage is shared behind-the-scenes support for letting the agent use outside services without directly handling private passwords or tokens. It is like a controlled service desk: the agent asks what tools are available, requests an action, and receives a cleaned-up result, while the sensitive account access stays behind a safe boundary.

The brokered and keyed connector backends connect to tool providers such as Composio, Pipedream, MCP servers, and simple API-key services. They discover available actions, choose allowed tools, proxy requests, run actions, and provide fake connectors for tests. The native integration parts do the same for familiar services: Slack message setup and search, iMessage through Spectrum, and GitHub credentials for coding work.

core/src/ufo/connectors.py defines the safety rules for connected accounts, making sure secrets do not leak into the agent, sandbox, logs, or unrelated code. extensions/connectors/ufo_ext_connectors/tools.py gives the agent the generic controls to find connector tools, describe them, run them, move files in and out, and trim bulky results into something useful.

### [Brokered and keyed connector backends](stage-13.1.md) `stage-13.1` — 12 files

This stage is shared behind-the-scenes support for connecting the agent to outside tools and accounts. It is the “switchboard” that lets the rest of the system ask for a tool without needing to know where the user’s secret login token is kept.

The Composio files form one backend: the client talks to Composio’s API, the broker presents Composio tools in the system’s normal connector shape, the proxy forwards ordinary web requests through Composio, the resolver chooses an allowed Composio toolkit by name, and the MCP session helper makes one tool call over MCP, a standard way for services to expose tools. The Pipedream files do the same kind of job for Pipedream Connect: define safe connected accounts, discover actions, run them, and proxy requests without exposing secrets. The connector objects file shows connected accounts as workspace items that can be shared, revoked, or disconnected with permission checks. Keyed connectors cover simpler services that use API keys. The MCP extension discovers tools from configured MCP servers. The evaluation manifest supplies fake mailbox, calendar, and code-search connectors for repeatable tests.

### [Native communication and code-service integrations](stage-13.2.md) `stage-13.2` — 6 files

This stage is the system’s set of adapters for outside services that people already use: Slack, iMessage through Spectrum, and GitHub. It is shared behind-the-scenes support, used whenever the project needs to talk in a native chat app or access code on GitHub.

The Slack pieces work like a careful translator and installer. The mentions code changes Slack’s special hidden mention format into readable names, and safely turns approved names back into real Slack mentions when replying. The attribution and hooks code add a small footer to connector-sent Slack messages so people can see the bot was responsible, while making sure the bot does not mistake that footer for a new user message. The tools code guides an administrator through connecting a Slack workspace, setting up the Slack app, and searching Slack conversations.

The iMessage cloud bridge signs in to Spectrum, opens secure connections, sends messages, receives events, and turns Spectrum responses into objects the extension can use. The GitHub App code checks a workspace’s installation and creates short-lived credentials for coding tasks, falling back to a user’s stored GitHub token when needed.

## [Source synchronization, indexing, search, research, and memory recall](stage-14.md) `stage-14` — 68 files

This stage is the system’s knowledge supply chain. It runs mostly behind the scenes before and during conversations, so the assistant can use up-to-date company data, web research, and remembered facts instead of relying only on its built-in training.

The syncing part connects to outside services such as Gmail, Slack, GitHub, Notion, Salesforce, and many others. Each connector knows how to ask its service for records, page through long lists of results, and turn them into stored “pages” the system can read. The shared sync engine registers these sources, saves changed document bodies, marks missing documents as deleted, and tells later steps what needs re-indexing.

The indexing and retrieval part works like a library catalog. It breaks documents into smaller text chunks, creates embeddings, which are number-based summaries of meaning, and stores them for keyword or meaning-based search. Memory code keeps durable facts, cleans duplicates, and records when memories are recalled.

Research support adds outside web search and page fetching through a shared search shape, so the rest of the system can request research results without caring which search provider supplies them.

### [Connector-backed source sync](stage-14.1.md) `stage-14.1` — 55 files

This stage is the system’s data intake area for read-only external services. It runs behind the scenes during syncs, when the system asks connected tools for their latest records and turns them into searchable pages. The connector groups cover the many kinds of tools people use: workplace suites like Gmail, Drive, Outlook, and Teams; engineering tools like GitHub, Jira, Slack, PagerDuty, and Sentry; planning tools like Airtable, Asana, Notion, and Wrike; CRM and support tools like HubSpot, Salesforce, Intercom, and Zendesk; marketing and ad tools; finance systems like Stripe, QuickBooks, and Xero; and HR or recruiting tools like Greenhouse, BambooHR, and Deel.

The shared files are the engine underneath these adapters. The connector contract defines what every adapter must provide. The REST helper handles web requests, retries, and “pagination,” which means fetching long result lists in chunks. The backend turns fetched records into stored pages and remembers where to resume. The registry maps service names to connector code. The connected, tools, and pages files create feed rows, expose sources to the rest of the app, and let users read or forget synced pages.

#### [Workspace suites, mail, docs, and calendars](stage-14.1.1.md) `stage-14.1.1` — 8 files

This stage is a set of read-only “connectors”: adapters that log in to workplace tools and copy out information the system is allowed to see, without changing anything there. It belongs to the behind-the-scenes sync work that feeds the search and recall system.

The Google connectors cover each major surface. Gmail reads mail, turns messages into plain searchable text, and remembers what changed since the last run. Google Calendar reads events and also builds an attendee-focused view, so invitations can be understood by person. Google Docs extracts document text. Google Drive reads files, shared drives, permissions, comments, and revisions. Google Meet turns transcripts and smart notes into meeting pages. Google Sheets converts spreadsheets, tabs, and cell grids into records, while skipping over isolated access problems when possible.

The Microsoft side uses Microsoft Graph, a web doorway into Microsoft 365 data. Teams reads teams, channels, chats, and messages. Outlook reads mail, contacts, calendars, conversations, and folders, while tracking progress so later syncs fetch only new, changed, or deleted items.

#### [Engineering collaboration and service operations connectors](stage-14.1.2.md) `stage-14.1.2` — 7 files

This stage is the set of “connectors” that lets the system bring in day-to-day engineering knowledge from outside tools. It is shared behind-the-scenes support for the sync process: each connector talks to a service through its API, meaning the service’s structured doorway for reading data, then reshapes the results into records the rest of the system can store, search, and recall.

The Confluence connector reads spaces, pages, blog posts, comments, groups, and audit logs, and converts rich page layouts into plain text. GitHub gathers repositories, issues, comments, users, commits, and releases, while carefully handling many repositories and paged results. Jira and Linear pull work-tracking data such as projects, issues, teams, comments, boards, and sprints. PagerDuty brings in operational data like incidents, services, schedules, notes, and on-call entries. Sentry adds error-monitoring records, including projects, issues, events, releases, and members. Slack reads workspace conversations, messages, threads, users, and participants. Together, these parts act like translators, turning many workplace tools into one searchable memory.

#### [Work management and productivity connectors](stage-14.1.3.md) `stage-14.1.3` — 7 files

This stage is shared behind-the-scenes support for bringing work-planning information into the system. It is like a set of adapters for different office tools: each adapter knows how that tool organizes its data, asks for it through the tool’s web API, and reshapes it into standard records the rest of the system can store, search, and recall.

The Airtable connector finds bases, tables, and records. The Asana connector reads workspaces, projects, tasks, stories, and users, including results that arrive in pages. Calendly brings in scheduling data such as event types, groups, scheduled events, and invitees. ClickUp walks through its layered structure, from teams down to lists and tasks. The monday.com connector collects users, teams, workspaces, boards, items, updates, logs, and tags. Notion reads pages, blocks, databases, comments, and users, turning workspace content into searchable text without writing anything back. Wrike imports contacts, folders, tasks, comments, workflows, and custom fields. Together, these connectors turn scattered work tools into a common memory source.

#### [CRM and customer support connectors](stage-14.1.4.md) `stage-14.1.4` — 6 files

This stage is the set of “adapters” that lets the system read customer and support data from outside services during a sync. A sync is the repeated work of asking another product for its latest records, then shaping those records so this system can store and search them in a consistent way.

Each file is one adapter for a different service. Attio brings in CRM items such as companies, people, deals, tasks, notes, meetings, and call recordings. HubSpot handles a wider mix, including contacts, companies, deals, emails, forms, campaigns, analytics, and custom objects, and smooths over HubSpot’s many API formats. Salesforce reads core sales records like accounts, contacts, opportunities, and tasks. Freshdesk brings in helpdesk data such as tickets, conversations, agents, contacts, companies, articles, forums, and settings. Intercom reads customer conversations, contacts, companies, teams, tags, tickets, and activity logs. Zendesk reads support tickets, users, organizations, Help Center and community content, plus admin data. Together, these connectors act like translators between many customer platforms and one shared search system.

#### [Marketing, advertising, social, and form connectors](stage-14.1.5.md) `stage-14.1.5` — 7 files

This stage is a set of “connectors,” which are adapters that let the system read data from outside services during a sync run. It sits in the main data-gathering part of the system: each connector talks to a different web API, asks for records in batches, and reshapes the replies into a common stream of rows that the rest of the project can store and reuse.

The ActiveCampaign, Klaviyo, and Mailchimp files cover marketing and email tools. They fetch things like contacts, profiles, campaigns, lists, reports, tags, events, and email activity. The Facebook Ads and Google Ads files cover advertising platforms. They pull account, campaign, ad, and performance data, including daily metrics. The Instagram file reads business-facing Instagram data through Meta’s API, including pages, linked accounts, posts, stories, and insights, without posting or changing anything. The Typeform file brings in form-related data, such as forms, responses, workspaces, themes, images, and webhooks. Together, these adapters act like plug heads for different outlets, making many services feed the same sync machine.

#### [Finance, billing, spend, and accounting connectors](stage-14.1.6.md) `stage-14.1.6` — 7 files

This stage is a set of behind-the-scenes connectors for financial systems. A connector is the adapter that knows how to talk to one outside service, ask for records, and reshape the answers into the project’s standard “streams,” meaning repeatable lists of records the sync engine can process.

Each file covers a different money-related service. Brex reads spend-management data such as expenses, vendors, budgets, users, and departments. Chargebee and Recurly read subscription billing records like customers, subscriptions, invoices, and transactions. Stripe does similar billing and payment work, including checkout sessions and connected-account data. Square focuses on point-of-sale records such as payments, orders, catalog items, locations, inventory, and customers. QuickBooks pulls accounting records like bills, invoices, vendors, payments, and journal entries. Xero also reads accounting data, while handling Xero-specific details such as tenant headers, update dates, and record IDs.

Together, these files are like plug adapters for different financial tools. Each one hides the service’s paging and authentication rules so the rest of the system can sync financial records in one common way.

#### [HR and recruiting connectors](stage-14.1.7.md) `stage-14.1.7` — 6 files

This stage is a set of behind-the-scenes connectors for HR and recruiting tools. A connector is a small adapter that knows how to talk to one outside service and translate its replies into the system’s common shape. These files are used during sync runs, when the system gathers fresh records and stores them as “recallable pages,” meaning saved batches that can be read again later.

The Ashby, Greenhouse, and Recruitee connectors cover recruiting. They fetch things like candidates, jobs, applications, interviews, offers, departments, and users, then split long API results into streams the sync engine can page through. BambooHR focuses on employee records, time off, timesheets, custom fields, and reports, including several different response formats. Deel reads workforce and payroll-adjacent data such as contracts, forms, payslips, timesheets, and tasks. Rippling reads company, worker, and team records.

Together, these connectors act like plug adapters: each fits a different HR product, but all deliver clean batches of records to the same storage and sync machinery.

### [Indexing, retrieval, and memory extraction](stage-14.2.md) `stage-14.2` — 11 files

This stage is the system’s library and research desk. It works behind the scenes before and during answers: saving useful text, finding it again, keeping memories clean, and reaching out to the web when needed. The core indexing code cuts long text into smaller searchable pieces, adds embeddings, which are number patterns that roughly represent meaning, and sends them to a chosen search store. The default index keeps this locally with word and meaning search, while the Turbopuffer extension does the same through an external service and can delete outdated chunks.

Memory has a shared interface so the rest of the system can ask for memories without caring where they live. The memory store saves, searches, and re-indexes them. The condenser turns changed pages into clean facts, merges related facts, and removes near-duplicates. Memory events record when memories were recalled, with size limits.

For outside research, Perplexity provides web search and page fetching. Research tools wrap search, fetch, image, or academic queries into safe JSON results. Source observations save links for later display, and wide_research splits a large research task into parallel smaller ones and writes the combined result.

## [Document, office, PDF, spreadsheet, and coding workspace automation](stage-15.md) `stage-15` — 19 files

This stage is shared behind-the-scenes support for working with files inside the agent’s workspace. It gives the system practical tools for documents, spreadsheets, presentations, PDFs, and coding tasks, so the agent can inspect and change real project files during a turn instead of only talking about them.

Its main part is the Office and PDF command script toolbox. These scripts act like a workbench of small specialized tools. Some keep track of a document review: they store the review state, define what counts as an issue, and record outlines, claims, checks, findings, and summaries. Other tools turn those findings into visible comments, highlights, or annotations in PDFs, PowerPoint slides, and Excel sheets.

The file-format tools open up Office documents into editable pieces, change the needed parts, and package them back into usable files. Word, PowerPoint, and Excel each have helpers for repair, cleanup, comments, tracked changes, previews, or formula recalculation. PDF helpers fill forms, inspect pages, write marks, and render pages as images.

### [Office and PDF command scripts](stage-15.1.md) `stage-15.1` — 19 files

This stage is a toolbox of command scripts for working with Office files and PDFs. It is mostly behind-the-scenes support: tools the system can run when it needs to review, mark up, repair, or convert documents without asking a person to open each app.

The document-review scripts keep the review moving. One file names the saved state and log files, another defines what a review “issue” looks like, and the state manager records outlines, claims, checks, issues, and summaries. The annotation tools then copy those findings into PDFs, PowerPoint slides, or Excel cells as visible highlights and comments.

The Word tools unpack a DOCX into editable XML, add comment data, repack it into a DOCX, or accept tracked changes using LibreOffice. The PowerPoint tools do the same kind of unpack-and-pack work, plus cleanup, slide/contact-sheet creation, and repairs for fragile generated files. The Excel tools launch LibreOffice quietly and recalculate formulas.

The PDF tools fill real form fields, inspect or write onto static forms, and render pages as images for preview or later processing.

## [Scheduled, recurring, billing, monitoring, and offline improvement jobs](stage-16.md) `stage-16` — 28 files

This stage is the system’s background shift. It runs when no one is actively sending a message, like an alarm clock, bookkeeper, cleaner, and quality tester working behind the scenes. Conversation wakeups and recurring work handle timed automations: they remember jobs, wake paused workflows, retry missed child-task results, watch shell-command monitors for changes, send daily briefs, and notice shared source updates. This keeps long-running conversations moving without needing a user to poke them.

Billing and self-improvement background processing keeps the service sustainable and safer over time. The billing pieces measure usage, enforce spending limits, report charges, and manage prepaid credit or automatic top-ups. The self-improvement pieces replay old conversations in a controlled way, test proposed prompt changes, and accept only changes that score better without repeating real-world actions.

The jobs.py file connects all discovered background job definitions to DBOS, the durable workflow system that stores queued work in the database so it can survive restarts. preview_renderer.py fills in missing document preview images later, by finding recent gaps, rendering previews, and saving the results.

### [Conversation wakeups and recurring work](stage-16.1.md) `stage-16.1` — 16 files

This stage is the system’s alarm clock and night watch. It is shared background support that wakes conversations when time, outside changes, or unfinished child work need attention. The scheduled task tools let agents create recurring jobs and pause a workflow until a reply or timeout. Schedules, cron, scheduled_fire, and the runner store those jobs, calculate their next times, label each firing, claim due work safely, and send it into the right conversation once. Pauses and the pause runner do the same for sleeping workflows. The conversation slot shows users the safe “Automations” view.

Monitors are saved watches that rerun a shell command, compare new output with a saved baseline, and wake the agent on change, repeated failure, or deadline. The monitor tool creates them, monitor_kind exposes them as objects, monitors stores their state, and monitor_runner performs the timed checks.

Sources triggers wake conversations when shared sources change. Sweep registers a daily brief job and its helpers. Candidates safely finds workspaces with pending work, while delivery retries lost child-task results so parent conversations do not wait forever.

### [Billing and self-improvement background processing](stage-16.2.md) `stage-16.2` — 10 files

This stage is background support for two jobs that run alongside the main product: keeping billing accurate and helping agents improve safely. The billing side acts like a cash register and fuel gauge. accounting.py records each workspace’s usage, applies spend limits, subtracts prepaid funds, and prepares reports for billing systems. balance.py tracks prepaid credit in tiny dollar units, deciding whether paid model work can continue, needs a top-up, or must stop. ufo_ext_metronome.py connects those records to Metronome for usage reporting and to Stripe for saved cards, portal links, and automatic refills.

The self-improvement side studies past conversations without changing the outside world. corpus.py selects useful failed examples and splits them for learning and testing. proposer.py asks a model to suggest a better system prompt, meaning the instruction text that guides an agent. replay.py reruns old conversations with the new prompt while reusing old tool results, so no emails, purchases, or other actions happen again. evaluation.py grades old versus new answers, gate.py accepts only reliable improvements, cron.py schedules repeated checks, and model.py gives all of this one safe, metered way to call the language model.

## [Turn completion, delivery, cleanup, and teardown](stage-17.md) `stage-17` — 2 files

This stage is the system’s “wrap up and put things away” step. It happens after the assistant has done its work for a turn. The final answer is committed, the turn is marked as finished or paused, live viewers are updated, and any reply is sent back to the place that asked for it. At the same time, temporary resources such as browsers, sandboxes, terminals, locks, and child tasks are closed or released so they do not leak into later work. It also helps during service shutdown by cleaning up old leftover resources.

The replies file acts like a mail sorter. If the model includes a hidden “reply-to” tag, meaning “send this part privately to that message,” it detects the tag, routes the reply correctly, and removes the tag so users do not see internal control text. The workspace changes file acts like a change log. It stores a snapshot of files changed during the conversation, using git-style scanning, so the system can later report what work was done.

## [Core schema, durable records, and persistence contracts](stage-18.md) `stage-18` · (cross-cutting) — 5 files

This stage is shared behind-the-scenes support. It defines the durable “paperwork” the rest of the system relies on: what records look like, where they are stored, and how they are safely read or changed. It is used during startup, normal work, background jobs, and rendering, because all those parts must agree on the same data shapes.

The database doorway is core/src/ufo/db.py. It creates connections to the database, starts and finishes transactions, keeps each workspace’s data separate, runs migrations that update the database layout, and closes connections cleanly. The main database blueprint is core/src/ufo/schema/tables.py, which describes the tables, columns, relationships, defaults, and safety rules for both local SQLite and deployed Postgres databases.

The shared vocabulary lives in core/src/ufo/schema/records.py. It defines the records for agents, conversation turns, terminal results, questions, credential requests, and queue state, so workers and user-facing parts understand the same messages. Conversation history is covered by core/src/ufo/transcript.py, which defines the saved transcript format, and core/src/ufo/loop/transcript.py, which safely reads and writes transcript snapshots without letting older copies overwrite newer ones.

## [Extension SDK, object system, slots, and public contracts](stage-19.md) `stage-19` · (cross-cutting) — 44 files

This stage is shared support for extensions, the add-ons that let the platform grow without letting outside code poke at private internals. It is like a set of guarded doors and standard forms. Extension authors get stable public imports, while the core system keeps control over safety, naming, permissions, and data shape.

The workspace object system gives the platform a common way to name, list, open, and describe things such as agents, conversations, memories, extensions, and workspace members. Internal extension contracts define what an extension can declare in its manifest, what limited services it receives while running, and what extra conversation panels it may add. Validation checks make sure those panels and slot data are well formed and safe to show.

The SDK facade modules are the public front counter. Some expose platform services such as accounting, credentials, auth proxy, grants, logging, and visibility labels. Others expose extension-building tools for manifests, tools, skills, jobs, web routes, context, and scheduled triggers. Integration facades cover browsers, terminals, connectors, sources, search, indexing, memory, models, sandboxes, and user surfaces.

### [Workspace object system and built-in object kinds](stage-19.1.md) `stage-19.1` — 9 files

This stage is shared behind-the-scenes support for the workspace. It gives the system a safe way to treat important things as named objects that can be listed, opened, described, or sometimes changed. The main object system defines the common rules: object names, simple YAML-based descriptions, visibility checks, validation, and routing each request to the code that owns that kind of object. The naming file keeps object identities consistent, and the scope helper records which agent should get credit for an audited action. The SDK file gives extension authors a stable public place to import these object tools.

On top of that shared machinery, several built-in object kinds are plugged in. Agent objects can be read, created, and updated under strict rules, but not deleted. Conversation objects expose past chats as read-only records, including transcripts only when allowed. Memory objects let callers safely list and open stored memories, while edits happen through memory tools instead. Extension objects show what installed extensions provide, but cannot be changed here. Workspace objects report basic member information and are also read-only.

### [Internal extension contracts, runtime context, and conversation slots](stage-19.2.md) `stage-19.2` — 3 files

This stage is shared behind-the-scenes support for extensions, which are plugin-like add-ons that can expand the main application. It defines the rules for what an extension can promise, what it is allowed to do while running, and what extra information it may place inside a conversation.

The manifest file is the “application form” for an extension or pack. It lists what the add-on provides, such as tools, scheduled jobs, credentials it needs, agents, skills, routes, hooks, or backends. The core system reads this declaration to know how to wire the add-on in safely.

The context file defines the limited workbench given to extension code and background jobs at runtime. Instead of full access to the system, they receive scoped abilities, such as reading their own settings, using approved credentials, opening conversations, reading transcripts, or registering synced sources.

The conversation slots file defines the extra panels extensions can add to conversations, such as artifacts, sources, tasks, sites, and automations. It validates these payloads so they stay safe, well-shaped, and displayable.

### [SDK cross-cutting platform service facades](stage-19.3.md) `stage-19.3` — 14 files

This stage is shared behind-the-scenes support for extension authors and SDK users. It is made of “facades”: small public doorways that keep import paths stable, even if the deeper internal code moves. Most files do not do the real work themselves. They re-export approved tools so outside code does not depend on private project layout.

Accounting exposes spending report types. Balance exposes prepaid balance, credits, and auto top-up tools. Listings provides listing and paging helpers. Seats publishes seat objects and helpers. Authproxy exposes pieces for credential backends, while credentials publishes selected credential tools. Bearer gives only token-checking helpers, not token creation. Grants exposes connection and audit tools. Operator provides operator-only web session helpers. Surface_token forwards surface token functions. Audience gives shared conversation audience names and helpers. Subjects exposes standard visibility labels, such as workspace-wide or individual-member access. Untrusted gives one common way to mark risky text from tools or extensions. O11y opens logging and metrics tools. Together, these files act like a clean front counter for many platform services.

### [SDK extension authoring and execution facades](stage-19.4.md) `stage-19.4` — 7 files

This stage is shared behind-the-scenes support for people who write UFO extensions. It is not the main work loop itself. Instead, it provides stable “front doors” into the system, so extension code can use approved names without depending on private internal files that may move or change.

The manifest module is where an extension describes itself: its declared features, constants, and helper rules. The tools and skills modules expose the building blocks an extension can offer to the system, such as callable actions and higher-level abilities. The jobs module lets an extension declare background work and choose which workspaces it should run in. The http module supports extension web routes by providing request and response types, plus a safe helper for setting session cookies. The context module gives running extension code access to its execution context, meaning the useful information and services available while it runs. The scheduled_fire module exposes helpers for scheduled triggers. Together, these files act like a clean control panel over deeper machinery.

### [SDK integration, data access, and surface facades](stage-19.5.md) `stage-19.5` — 11 files

This stage is shared behind-the-scenes support for people building on top of the system. It does not run the main work itself. Instead, it provides stable public “front doors” into features that live deeper inside the codebase. That matters because extension authors can import from the SDK without depending on private file paths that may change.

Each file is one of these front doors. browser.py exposes the approved way to connect browser extensions to browser transport code. terminal.py does the same for terminal transports, terminal backends, and blob storage. hub.py gathers hub protocol messages, live frames, and activity types used for shared communication. connectors.py exposes connector and OAuth building blocks, while sources.py collects tools for adding content sources, such as REST APIs, pagination, and sync results.

The data-facing doors are index.py for indexing and embeddings, search.py for search interfaces, memory.py for memory search, and models.py for approved model clients and helpers. sandbox.py exposes safe execution tools. surfaces.py gathers the types and helpers for building user-facing surfaces. Together, these files act like a clean reception desk for the larger system.

## [Security, credentials, sessions, and access boundaries](stage-20.md) `stage-20` · (cross-cutting) — 21 files

This stage is shared safety plumbing that runs behind many parts of the system. It is not one single work loop. Instead, it sets the rules for who may act, what they may see, which secrets they may use, and which outside services they may contact.

Signed tokens, sessions, and protected links create “sealed notes” that prove a login, file link, sandbox port link, or surface identifier was made by the system and was not changed. Workspace, member, and visibility boundaries decide the scope of work: which workspace is active, which agent or member is involved, and who can read each object or task. Credential storage, declaration, and injection keeps API keys and tokens encrypted, labels which secrets exist, and only passes approved secrets into sandboxes or connectors. Network egress policy and proxy authorization checks outbound sandbox traffic, allowing only approved destinations and credentials. Operator access and uploaded media safety protects internal tools and rejects unsafe image previews. Together, these parts act like locks, badges, and guards around the system.

### [Signed tokens, sessions, and protected links](stage-20.1.md) `stage-20.1` — 5 files

This stage is shared behind-the-scenes security support. It gives the system a way to hand out small pieces of text, called tokens, that can be checked later without keeping a database record for each one. A token is like a sealed note: people can carry it around, but if they change it, the seal no longer matches.

The common sealing tool lives in token_signing.py. It signs token contents with a secret key and later checks that the text was really made by the system and was not altered. The other files use that tool for specific jobs. bearer.py makes member login tokens, so services can recognize a logged-in member without storing a server-side session. artifact_url.py makes time-limited download links for allowed artifact files and rejects forged or expired links. ingress_token.py makes short-lived passes for reaching one sandbox port through the browser. surface_token.py makes permanent signed identifiers for surfaces, carrying safe text claims. Together, these pieces protect identity, downloads, and access links with one consistent signing method.

### [Workspace, member, and visibility boundaries](stage-20.2.md) `stage-20.2` — 7 files

This stage is shared behind-the-scenes support for keeping work inside the right boundaries. It answers questions like: “Which workspace are we in?”, “Which agent is acting?”, “Who is a member?”, and “Who is allowed to see this?”

The workspace file sets the main boundary, like putting a case folder on the desk before any work starts. Secrets, database access, and billing all follow that same workspace. The agent scope file adds the current agent identity inside that workspace, so agent-owned features do not accidentally run as the wrong agent.

Membership and seat files manage people. Members defines who belongs to a workspace, who is an admin, and lets admins add someone in advance. Seats decides which members the agent may answer, grants or removes access, and prevents changes that would leave nobody able to manage seats.

Subjects and audience provide consistent visibility labels. Subjects covers simple content access, such as everyone or one member. Audience handles conversation disclosure rules and keeps private, shared, room, and external-room content separate. Scheduled task visibility then uses these rules to decide who may read each task’s prompt and description.

### [Credential storage, declaration, and injection](stage-20.3.md) `stage-20.3` — 4 files

This stage is shared behind-the-scenes support for handling secrets, such as API keys and access tokens. Its job is to let the system know what credentials exist, store their real values safely, and provide them only to trusted work that needs them.

The credential kind file acts like a public label board. Extensions can declare that they need a credential slot, and users can see whether that slot is empty or filled. The secret itself is never shown there. The credentials file is the locked safe. It encrypts secret values before saving them, checks sealed short-lived update requests before accepting changes, and makes sure a secret belongs to the right workspace and user flow.

When sandboxed tools need access, the sandbox environment file prepares safe environment variables. These are named settings passed into the sandbox process. It gives tools approved access without copying raw secrets into the sandbox. The direct source connector file uses the same safe store when a connector needs a member’s own API key, turning it into an authentication token for feed syncing.

### [Network egress policy and proxy authorization](stage-20.4.md) `stage-20.4` — 3 files

This stage is shared behind-the-scenes support for sandboxed agent runs. When an agent tries to reach the outside network, it should not be able to call any service or use any secret by accident. This stage acts like a checkpoint between the sandbox and the internet.

egress_rules.py builds the actual rulebook. It looks at things like enabled models, user credentials, granted permissions, connectors, and artifact storage, then turns them into specific proxy rules: which destinations are allowed, which credentials can be attached, and how usage should be counted.

egress_resolver.py chooses the right rulebook for one particular agent run or probe. It makes sure rules are tied to the correct workspace, agent, and user, so credentials or permissions do not leak between jobs.

egress_control.py exposes a private web API for the Rust egress proxy. The proxy asks this API before allowing outbound traffic or attaching secrets. Core keeps the sensitive decisions and billing records, while the proxy simply enforces the answer.

### [Operator access and uploaded media safety](stage-20.5.md) `stage-20.5` — 2 files

This stage is shared behind-the-scenes protection for two places where trust matters: people entering internal tools, and image files entering the system. It is not the main work loop. It acts more like a guarded doorway before sensitive pages or risky media are allowed through.

The operator access file provides common sign-in and session rules for operator-only web tools, such as debugging or memory inspection pages. It avoids putting long-lived access tokens in web addresses, since URLs can be copied or logged. It checks that the requester’s email belongs to the approved operator domain, then decides which workspace that person may view. This keeps powerful tools limited to the right people and the right scope.

The image preview file checks uploaded or received previews before they are shown or processed. It verifies that the file is the type and size it claims to be, and rejects malformed, mislabeled, oversized, or dangerous images. Together, these parts reduce risk at system boundaries.

## [Protocols, generated types, and serialization glue](stage-21.md) `stage-21` · (cross-cutting) — 64 files

This stage is shared behind-the-scenes support. It is used whenever the system needs reliable message formats, network service definitions, or importable folders. It is not the startup, main work loop, or shutdown. It is more like the plumbing and labeled shelving that other parts depend on.

The iMessage provider and gRPC service boundary defines the real iMessage contract. The hand-written provider code says what an iMessage provider must offer, while generated Protocol Buffer files define exact message shapes for chats, addresses, attachments, events, and streams. gRPC files add standard network calling code, so clients and servers can talk in the same format.

The Google API annotation files are generated helpers that let the system understand HTTP mapping details attached to API methods. The iMessage proto namespace markers make the nested protocol folders importable.

The remaining parts are many __init__.py package markers. They open the core UFO folders and many extension folders, including integrations, productivity tools, evaluation tools, runtime helpers, and nested script areas. They do not run behavior themselves, but they let Python find the code when needed.

### [iMessage provider and gRPC service boundary](stage-21.1.md) `stage-21.1` — 17 files

This stage is the border between the iMessage extension and the rest of the system. It is shared support code, used whenever the system needs to talk about iMessage data or call iMessage services over the network. The __init__.py file is only a package marker, like a label on a folder, so Python can import the extension.

The hand-written provider.py file defines the contract that real iMessage providers must follow. It also defines small shared data shapes for phone numbers, incoming messages, attachments, and events, so the rest of the extension can pass information around consistently.

The many *_pb2.py files are generated from Protocol Buffers, a schema format for structured data. They define the agreed shapes for addresses, chats, messages, attachments, groups, polls, events, and stream heartbeats. The *_pb2_grpc.py files add gRPC wiring, which means standard client and server code for calling those services across a network. Together, these files act like adapters and plugs: provider.py says what the extension can do, while the generated files make sure both sides speak the same language.

### [Generated Google API annotation protos](stage-21.2.md) `stage-21.2` — 4 files

This stage is quiet behind-the-scenes support for the iMessage protocol code. It does not start the app, run the main work, or shut anything down. Instead, it provides the Python pieces needed to understand Google API “protobuf” definitions. A protobuf is a structured message format, like a shared form that different programs can fill in and read the same way.

The two __init__.py files are simple signposts. They tell Python that the google and google.api folders are importable packages, so other code can find the generated files inside them.

The annotations_pb2.py file teaches Python about the google.api.http annotation. An annotation is extra information attached to an API method; here it describes how that method maps to HTTP, such as web routes.

The http_pb2.py file defines the generated message types used by those annotations, such as Http, HttpRule, and CustomHttpPattern. Together, these files let the rest of the system read and inspect HTTP metadata without custom parsing code.

### [iMessage proto namespace package markers](stage-21.3.md) `stage-21.3` — 4 files

This stage is quiet behind-the-scenes support. It does not start the app, run the main iMessage work, or shut anything down. Instead, it prepares a set of folders so Python can treat them as packages, meaning named places where code can be imported from.

The four files are all package markers named __init__.py. They are like labels on nested drawers. The top proto marker labels the general protocol area, where message format code can live. Inside it, the photon marker labels the Photon protocol section. Inside that, the imessage marker labels the iMessage-specific protocol area. Finally, the v1 marker labels the version 1 protocol folder, where other code can import the actual iMessage protocol modules.

None of these files performs calculations, opens connections, or changes data at runtime. Their job is structural. Together, they make the folder path importable step by step, so the rest of the system can reliably find and use the generated or protocol-related iMessage code stored underneath.

### [Core UFO package namespace markers](stage-21.4.md) `stage-21.4` — 11 files

This stage is behind-the-scenes support for the whole project. It does not start the app, run the main loop, or shut anything down. Instead, it sets up the folder “signposts” Python needs so code can be imported by name. In Python, an __init__.py file marks a folder as a package, meaning other files can refer to it using paths like ufo.models or ufo.tools.

The top-level ufo marker opens the main package. The ext, loop, models, sandbox, schema, sdk, sources, and surfaces markers do the same for their own areas, such as extension code, the main loop, model code, sandbox code, data schemas, developer-facing SDK code, source connectors, and surface integrations. The loop.prompts marker makes prompt-related code inside the loop area importable. The tools marker also identifies the tools area and briefly documents that it contains the tool registry, tool-running context, and built-in tools. Together, these files act like labeled doors in a building: they do not do the work, but they let the rest of the system find the right rooms.

### [Integration extension package markers](stage-21.5.md) `stage-21.5` — 9 files

This stage is quiet behind-the-scenes support. It is not part of the main work loop itself. Instead, it helps Python find and load extension code when the system needs to connect UFO to outside tools and services. Each file is an __init__.py file, which is a small marker that tells Python, “this folder is a package you can import from.”

The browser marker opens the area for sandbox browser and computer-use tools, including a browser-focused helper profile. The Composio, Pipedream, Slack, web, and UFO markers make their extension folders importable for those specific integrations. The connectors marker does the same for general connector modules, which are pieces that link UFO to other systems. The sites marker prepares site-specific extension code for import. The sources marker prepares modules that bring in or work with external sources.

Together, these files act like labeled doors in a building. They do not run the machinery, but they make sure the rest of the code can enter the right rooms when needed.

### [Productivity and knowledge extension package markers](stage-21.6.md) `stage-21.6` — 8 files

This stage is shared behind-the-scenes support for a group of optional extensions. Each file here is a Python package initializer. In plain terms, that means it is like a label on a folder telling Python, “this folder contains importable code.” These files do not run the main work of the extensions themselves. They make the folders visible and understandable to the rest of the system.

Together, they mark packages for several productivity and knowledge areas. The brief pipeline package is for preparing or managing briefs. The coding package is for code-related help. The documents package is for document tools. The memory package adds a description of longer-term stored facts, recall during prompts, page-based updates, and indexing work. The objectives package is for goal or objective support. The research package is for research tools. The scheduled tasks package is for work planned to happen later or repeatedly. The skill creation package is for agent-made skills and skills used during normal runtime turns.

### [Runtime, evaluation, and internal extension package markers](stage-21.7.md) `stage-21.7` — 7 files

This stage is shared behind-the-scenes support. It does not run the debugger, evaluation tools, monitors, or replay system by itself. Instead, it gives Python clear “front doors” into these extension folders. In Python, an __init__.py file marks a folder as a package, meaning other parts of the system can import code from it.

Each file here opens one extension area. The debugger package marker lets the debugging extension be found. The evaluation environment marker also documents its purpose: fake, predictable mailbox and calendar connectors used for tests or evaluations. The monitors marker opens the monitoring extension. The Redis hub marker opens runtime infrastructure that other code can import. The REPL marker opens the interactive command extension, where a user can type commands and see results. The self-improvement marker describes offline replay of past work, judging it, and suggesting prompt changes for human approval. The sweep marker opens tools for running broad sets of experiments or checks. Together, these files are like labeled doors in a workshop: they do not build anything themselves, but they make every tool reachable.

### [Nested extension subpackage markers](stage-21.8.md) `stage-21.8` — 4 files

This stage is behind-the-scenes support for the extension system. It does not start the app, run the main work, or shut anything down. Instead, it places small marker files in nested folders so Python knows those folders can be treated as packages. A package is simply a folder that Python is allowed to import code from, like a labeled drawer in a filing cabinet.

The browser extension marker, in the bua folder, makes browser automation helper code reachable by other parts of the project. The three document extension markers do the same for script folders inside different document skills: document review, PowerPoint files, and Excel files. These skills can then keep their helper scripts organized in separate subfolders while still letting the rest of the system import them in a predictable way.

Together, these files are quiet pieces of structure. They contain no real runtime behavior, but they make the surrounding code easier to find, load, and maintain.

## [Shared utilities, telemetry-like reporting, accounting, and diagnostics](stage-22.md) `stage-22` · (cross-cutting) — 4 files

This stage is shared behind-the-scenes support. It does not belong to just startup, request handling, or shutdown. Instead, other parts of the system call into it whenever they need common services.

The blob module is the shared storage drawer for raw bytes, such as uploaded files or generated artifacts. It hides whether the data is kept on the local disk or in S3-style cloud storage, and it keeps workspace data separate from deployment-wide data to avoid accidental mixing.

The listings module gives portal pages a consistent way to move through long lists. Its paging rules keep “next” and “previous” results stable even if new items appear while someone is browsing.

The o11y module is the system’s observability center. Observability means traces, metrics, and logs that help operators understand what happened. It records useful diagnostic information while avoiding leaks of large prompts, tokens, or sensitive text.

The seed module builds a safe demo conversation showing the portal’s features, and cleans up only its own older demo data.
