# System Handbook — Stage Index

Each stage below links to its full page; the paragraph is the stage's role in the system.

## [Deployment preparation and database migration](stage-1.md) `stage-1` — 204 files

This stage gets the system ready before normal serving begins. It has two jobs: pack the software so it can run in the right environment, and update the database so stored data matches what the current code expects.

The runtime bundle part is like packing a travel kit. It gathers the runtime code, fixed settings, allowed extensions, sandbox client program, and Docker instructions into a deployable bundle. It also checks the sandbox setup, which is the protected place where risky or untrusted code can run. One script makes sure hosted and local sandbox images are built from the same recipe. Another performs a safety check by starting a temporary sandbox and testing proxy certificate behavior before deployment.

The migration part renovates the database. Alembic, a tool that applies database changes step by step, connects to storage and runs upgrades or rollbacks in order. Core migrations create and reshape the main tables. Extension migrations do the same for optional features. Together, they preserve old data while preparing storage for the new code.

### [Runtime bundle and sandbox image gates](stage-1.1.md) `stage-1.1` — 4 files

This stage is part of getting UFO ready to run safely outside a developer’s machine. It prepares the “runtime bundle,” which is like a packed travel kit containing the exact code, settings, allowed extensions, sandbox client program, and Docker instructions needed to reproduce the same runtime elsewhere.

core/src/ufo/bundle.py is the packer. It creates the folder used by ufoctl bundle, including the runtime wheel, pinned configuration, extension lockfile, sandbox client binary, and Dockerfile. core/src/ufo/harness/sandbox/client_binary.py is the locator for that client binary. It does not build the program; it checks that a suitable one already exists and gives a clear fix if it does not.

The sandbox scripts guard the environment where untrusted code runs. sandbox/build_template.py builds both the hosted E2B sandbox template and the local Docker image from the same recipe, keeping them matched. sandbox/proxy_gate.py is a safety test before deployment: it launches a temporary sandbox, installs the proxy certificate, and confirms HTTPS proxy behavior fails in the expected controlled way.

### [Core and extension schema upgrades or rollbacks](stage-1.2.md) `stage-1.2` — 200 files

This stage is the database renovation path for the whole system. It runs during install, upgrade, or rollback, not during normal chat work. Alembic, the migration tool, applies the steps in order. env.py is the foreman: it connects to the database, checks the current table definitions, and runs changes safely. 0001_heartbeat.py lays the first foundation: workspaces, members, agents, conversations, turns, identities, and usage costs. 0006_ext_store.py adds a shared JSON storage drawer for extensions.

The core migration groups then reshape the main product shelves: turns and subagents, conversation metadata, scheduling, message delivery, sources and pages, shared artifacts, workspace membership, grants and credentials, agent settings, model and tool rewrites, billing, retired-provider cleanup, and old iMessage cleanup. Extension groups do the same for optional features: Daily Brief, memory, search and research, skill creation, notifications, code review, Sites, monitors, objectives, reports, scheduled pauses, test utilities, samples, and old web chat data. Together they let old installations keep their history while the current code gets the tables and fields it expects.

#### [Core turn execution, subagent, and authority migrations](stage-1.2.1.md) `stage-1.2.1` — 20 files

This stage is behind-the-scenes database upkeep for the main turn system. A “turn” is one unit of conversation or work, and these migration files are small versioned steps that change how turns are stored as the product grows. Early changes let turns form parent-child chains, so a subagent can run work under another turn, and add guards so two workers do not claim or resume the same turn twice. Later migrations store trace links, surface context, runtime settings, created references, retry times, and retry counts, so each turn carries the details needed to debug and safely continue work.

Other changes refine who is speaking and under whose authority. They record speakers, represented members, connection authorization timing, and enforce that a turn cannot claim conflicting identities. Subagent-focused migrations remember display names, original spawn intent, and whether a child result still needs delivery to its parent. Several files add indexes, which are like book indexes for the database, so the system can quickly find child turns, spoken turns, live agent turns, recent activity, and pending subagent results as the turn table grows.

#### [Core conversation metadata migrations](stage-1.2.2.md) `stage-1.2.2` — 6 files

This stage is behind-the-scenes support for the system’s stored conversation data. It is made of database migrations, which are small upgrade steps that change the shape of saved records without losing old conversations. Together they teach the database to remember more about each chat.

The sandbox-related migrations add links between conversations and durable sandboxes, which are saved work areas the system can reconnect to later. One stores a sandbox handle on a conversation. Another records when one conversation is being used as the sandbox for another conversation’s turns, and can remove that field again during rollback.

The audience migration adds a field for who a conversation is safe to show to, and stops if old Slack records make that safety unclear. The surface-label migration stores the display name of the place where the conversation began. The title migration adds a real stored title and fills old ones from the first message. The final migration records whether a conversation has already been sent for title summarization.

#### [Core scheduling and runtime-instance migrations](stage-1.2.3.md) `stage-1.2.3` — 12 files

This stage is behind-the-scenes database upkeep. It is not the main work loop. It prepares the stored data so the scheduler, runtime workers, and pause/resume features have the shelves and labels they need. Each file is a migration, meaning a small database change that can usually be applied during an upgrade and reversed during a rollback.

The runtime-instance migrations create and reshape the table that records live runtime processes. Later changes let shared fleet workers exist without belonging to one workspace, then remove older columns from a retired dedicated-runtime design. The scheduled-task migrations build the table for planned prompts, then add practical details: when a task last fired, when it expires, whether it is paused, and how task names stay unique per agent. Other migrations add fast lookup indexes so background sweepers can find waiting work without reading every row. Pause-related migrations add, then later remove, core storage for pause/resume state as that responsibility moves elsewhere. Finally, scheduled admission lets a turn be marked as started by the scheduler, so later code can tell why the work began.

#### [Core surface, inbound, and delivery migrations](stage-1.2.4.md) `stage-1.2.4` — 10 files

This stage is behind-the-scenes setup work. It is a set of database migrations, which are ordered changes that teach the database what kinds of records the system can store. These changes prepare the system to accept messages from Slack, the web, and shared communication surfaces, then route and deliver replies safely.

The Slack migration adds storage for Slack conversations, retries, and sent replies. The web migration allows “web” to be recorded as a valid source. The surface workspace key migration makes each surface belong to a workspace, records installations, and helps find pending writeback jobs. The inbound message migration creates a queue table for messages waiting to be processed. Two later migrations add, then remove, an older pre-rendered text field as the design changes. The agent bindings migration makes every installation and conversation point to an agent, updating old data safely. The mid-turn reply migration stores replies sent before a full processing turn finishes, so workers can deliver them once. The listener claim migration records which running server owns a listener. The address routing migration routes shared-provider messages by sender address instead of installation.

#### [Core source, page, and knowledge-storage migrations](stage-1.2.5.md) `stage-1.2.5` — 13 files

This stage is behind-the-scenes database upgrade work. It changes the stored shape of the system as the product learns to track content more carefully. It starts by adding sources and pages, so the system can remember where content came from, which workspace it belongs to, and when it should be synced. Later migrations make sources more flexible: new backend types can be added, repeated errors can be counted for slower retry “backoff,” removed sources can be marked, ownership can be shared or tied to a member, and refusing sources can be temporarily parked.

The page-related migrations add fields useful for browsing, rename timestamps to clearer “record” wording, give page changes simple revision numbers, and add per-source page identity strings with a rule to prevent duplicates. Other migrations clean up older storage: one removes old page-alert extension data, one creates the first knowledge-graph tables for things and links, and a later one removes those old graph tables as the system moves to one unified memory surface. Together, these migrations keep saved content organized as the system evolves.

#### [Core shared artifact and object-history migrations](stage-1.2.6.md) `stage-1.2.6` — 9 files

This stage is behind-the-scenes database upkeep. It is used when the system is installed or upgraded, so older stored data keeps working as the product learns new tricks. The migrations act like renovation plans for the database.

The early proposal migration adds a place to store suggested changes, including who proposed them, the old and new values, and their approval state. The surface-seam migration adds records for artifacts shared during a conversation turn and relaxes older surface limits. Later shared-artifact migrations make those artifacts easier to track: one gives each artifact a stable ID, one adds a first-page preview with safety checks, and one records which request created the artifact and what content it points to.

Several migrations repair media-type labels, which are file-type descriptions used for display and preview. They fix Office files, patches, YAML, TOML, and TypeScript so old uploads open correctly. The conversation-change migration stores Git-reported workspace changes for a conversation. The object-change journal adds an audit trail, recording who changed an object, when, and what it looked like before and after.

#### [Core workspace and member lifecycle migrations](stage-1.2.7.md) `stage-1.2.7` — 8 files

This stage is behind-the-scenes database upkeep for the workspace member lifecycle. These migrations change the stored shape of the system so later code can reason about who belongs to a workspace, who controls it, and what member history is known.

It starts by adding “seats,” meaning counted membership slots: one migration records when a member takes a seat and lets a workspace set a seat limit. A later one adds “included seats,” a positive optional allowance. Another migration makes every workspace name its control person and main agent, filling old records before making that rule required. Member lookup is improved by adding an email index, like adding a catalog tab so sign-in can find people faster.

Then the model changes direction. The unlimited-members migration seats existing members, removes old seat-count fields, and clears approval marks that no longer apply. Later migrations add useful member details: the last valid timezone seen, and invitation stamps showing when someone was invited and by whom. Finally, an obsolete “seats shipped” marker is removed so old bookkeeping cannot mislead the system.

#### [Core grants, connections, credentials, and access migrations](stage-1.2.8.md) `stage-1.2.8` — 10 files

This stage is behind-the-scenes database upkeep. It is not the main work loop. Instead, it changes the stored data structures so the rest of the system can manage credentials, account connections, and access rules safely over time. First, 0002 creates a secure place for encrypted workspace credentials. 0014 adds “grants,” meaning records of who gave an agent permission to use an account, and 0043 adds a shared-or-private flag to those grants. Later, 0057 separates that older idea into reusable connections and each agent’s permission to use them, while also linking sources to the connection they depend on. 0059 adds source-specific grants, so agents can be allowed to read particular sources without breaking existing live sources. 0065 records when an admin opens another member’s private transcript, and 0066 removes an unused shortcut index from that audit table. 0079 moves sharing onto the connection itself and adds an account label. The final migrations record fulfilled credential requests and add optional commit name and email fields for connected accounts.

#### [Core agent configuration and ownership migrations](stage-1.2.9.md) `stage-1.2.9` — 13 files

This stage is behind-the-scenes database upkeep. It runs during upgrades, not during the agent’s normal work, and changes the stored “agent” records so newer code can rely on newer fields. Think of it as remodeling the filing cabinet while keeping old folders usable.

Several migrations add configuration knobs: internet access, reasoning mode, sandbox size, workspace-shared skills, and a short purpose description. Some also enforce allowed choices, such as small, medium, or large sandbox sizes, so bad values cannot be saved. Other migrations record where an agent came from and how it was set up: provisioning data, tools policy, setup details, expected input and output formats, and which member owns the agent. Visibility and ownership are also refined. One migration marks existing main agents as visible to the workspace, while another makes built-in wiki app rows private to match the newer rule. Icon migrations add an icon field, give the main agent a useful default, and later change the default for future agents. Finally, archive support lets agents be hidden from active use and prevents an archived agent from being treated as the workspace’s main agent.

#### [Core agent app, model, and tool-policy rewrites](stage-1.2.10.md) `stage-1.2.10` — 14 files

This stage is behind-the-scenes upgrade work for saved agents in the database. A database migration is a small script that reshapes old stored data so the newer application can understand it. Here, several migrations move agents away from model names that no longer work: old Bedrock IDs, Claude Fable naming and reasoning settings, OpenRouter Fable IDs, and the retired GLM 5.2 model are rewritten to supported choices.

Other migrations tidy built-in app agents. They assign the right icons to built-in apps, update the Artifacts icon, move the code review app from its old identity to the newer app_code identity, archive the old Tasks app without deleting history, and make the chat app the main agent in older workspaces.

A third group cleans up names and permissions. Archived apps stop blocking reuse of their old public names. Stored tool allowlists, which are lists of actions an agent may use, are renamed from older labels to the action names the current tool registry expects, especially for object actions, Slack, and iMessage. Together, these scripts keep old installations usable after an upgrade.

#### [Core ledger, billing, balance, and usage migrations](stage-1.2.11.md) `stage-1.2.11` — 19 files

This stage is behind-the-scenes database setup. It is made of migrations, small scripts that change the database shape during deployment so the rest of the system can bill and track usage safely. The early migrations add spending caps, a parked work state, and new ledger “dimensions,” meaning categories of measured usage such as tokens, egress network traffic, sandbox tokens, images, and videos. Other ledger changes add price fingerprints, workspace-level records, export progress, BYOK flags, separate prompt and cache-read token counts, detailed usage fields, debit amounts, and a faster lookup by workspace and time. Together, these make the ledger act like a detailed cash register receipt.

A second group supports balances. It adds workspace credit balances, records top-ups or grants, stores automatic refill rules, and notes when a verified card top-up first happened for overdraft decisions. BYOK, or “bring your own key,” is also recorded on turns and exports so billing and recovery can match the right customer key setup. Finally, an egress rule generation counter helps the network proxy know when cached access rules are out of date.

#### [Core retired provider and source cleanup migrations](stage-1.2.12.md) `stage-1.2.12` — 4 files

This stage is shared behind-the-scenes cleanup. It runs as part of database migrations, which are step-by-step changes that bring stored data into the shape the current software expects. Here, the goal is to stop the system from treating old integrations as if they still work.

The YC migration removes leftover login state, credentials, access grants, pages, and sources from the retired YC command-line extension, marking old items as removed. The Exa migration does a similar smaller cleanup for the old “exa” extension and its saved API-key slot. The QuickBooks migration looks for sources that are missing the company address needed to function. It retires those sources, removes their access grants, and marks live pages as deleted, while keeping historical page links for records and audits. The GitHub migration removes outdated GitHub connection records and GitHub App credential slots, so the database no longer suggests those connections are owned by an obsolete broker.

Together, these migrations tidy old wiring out of the system without erasing useful history.

#### [Core iMessage extension-store cleanup migrations](stage-1.2.13.md) `stage-1.2.13` — 3 files

This stage is part of the behind-the-scenes database upgrade path. It does not run during normal message handling. Instead, it runs when an installed system is brought up to a newer version and its stored data must be reshaped. The focus here is old iMessage extension-store data: information once kept in a shared extension storage table, but now removed or moved to newer homes.

The first migration removes an old iMessage project binding from that shared store, making sure the project link exists only in the newer surface installation data. The second migration clears out older claim-code and confirmation-reply records, as part of Alembic’s ordered migration chain. Alembic is the tool that applies database changes step by step. The third migration removes old phone-claim and receipt opt-in records, so the system no longer keeps outdated consent-related data. Together, these files act like a cleanup crew, clearing obsolete drawers after the application has learned better places to store or stop storing that information.

#### [Daily Brief and Sweep migration history](stage-1.2.14.md) `stage-1.2.14` — 4 files

This stage is part of behind-the-scenes database upkeep. It records how the old Daily Brief “Sweep” feature changed the database over time, so installs and deployments can move forward safely or undo changes if needed. A database migration is a saved step that changes the shape of stored data, like adding or removing shelves in a filing room.

The first file, sweep_0001_sweep.py, creates the original storage for Sweep editions of the Daily Brief and describes how to remove it on rollback. The second, sweep_0002_application_editions.py, reshapes that storage: it removes older Daily Brief agent data, changes the edition table, and adds a table for Daily Brief applications. The close_the_daily_brief_branch migration is a bookkeeping step. It does not alter tables; it reconnects the old Sweep migration branch to the main migration path so later updates have one clear route. Finally, drop_daily_brief_tables.py removes two unused Daily Brief tables, while keeping instructions to rebuild them if necessary.

#### [Memory extension migrations](stage-1.2.15.md) `stage-1.2.15` — 16 files

This stage is part of setup and upgrades. It is a set of database migrations, which are step-by-step instructions for changing stored data safely as the memory feature grows. The first migrations build the basic shelves: a table for memory items, a table for memory pages, labels for the kind of memory, and a direct workspace link so each page belongs in the right place. Later migrations make the shelves easier to search by adding indexes for consolidation and inventory browsing. The next group improves time and source tracking: memories can say what time they refer to, copy missing page times, link back to the page they came from, and record the exact page revision. Audience rules are widened so memories can belong to rooms as well as members or shared spaces. Source partitioning lets one fact be connected to more than one feed or page. Retirement records when a curator deliberately sets a memory aside. New item classes add sections and page overviews. Finally, memory profiles add shared summaries about members within each workspace.

#### [Search, source-trigger, enrichment, and research extension migrations](stage-1.2.16.md) `stage-1.2.16` — 7 files

This stage is behind-the-scenes setup for several optional extensions. It does not answer users directly. Instead, it changes the database so later parts of the system have the shelves they need to store derived and external information.

The enrichment migrations create storage for member enrichment profiles, such as matched people or company details found from outside sources. They also add places to record consent decisions and retry timing, so the system knows when enrichment is allowed and when to try again after failures.

The indexing migrations create storage for searchable text chunks. A chunk is a small piece of text saved so it can be found later by keyword search, and sometimes by “embedding,” a number-based representation used for similarity search. A later change ties each chunk to a workspace, keeping different workspaces safely separated.

The research migration records which outside sources were noticed during a conversation, preserving where research answers came from.

The sources migrations create and update source triggers, which connect conversations to external source bindings and define how updates should be delivered.

#### [Skill-create extension migrations](stage-1.2.17.md) `stage-1.2.17` — 4 files

This stage is behind-the-scenes setup work for the skill-create extension. It runs during database upgrades, making sure the stored records for user-made and workspace skills match what the newer code expects. A database migration is a step-by-step change to the database structure, like remodeling shelves without losing the items on them.

The first migration creates the original table for user-created skills, and can remove it again if the upgrade is undone. The second migration changes those skills so each one is tied to a specific agent, meaning a particular automated worker in the system, and updates old rows to fit that rule. The third migration adds “routing card” details: stored notes that help the system describe a skill, know its dependencies, mark it as pinned, and track whether it has been indexed for search or lookup. The fourth migration moves ownership from individual agents to the whole workspace, then removes duplicates so each workspace keeps only one copy of a skill with the same name.

#### [Notification and coding workflow extension migrations](stage-1.2.18.md) `stage-1.2.18` — 7 files

This stage is behind-the-scenes upgrade work. It changes the application’s database, which is the system’s long-term memory, so notification and coding-review features keep working as their storage design evolves. These migrations usually run during setup or version upgrades, not during everyday user actions.

The notification migrations build the notification store step by step. The first creates the basic table for saved app notifications. The second adds fields for workflow state: whether a notification was claimed, triaged, and when it was last raised. It also updates built-in notification agents to a newer prompt, while leaving user-edited prompts alone. The third adds delivery memory, so old notifications can record where and when they were sent, and refreshes installed agents with newer prompts, tool permissions, and version numbers.

The coding migrations do a similar job for code review. They first create review inbox and review-run tables, then add conversation links, then replace an old conversation-based link with an agent-based one. Finally, they move old inbox records into the newer shared trigger system and remove the now-obsolete review tables.

#### [Sites extension migrations](stage-1.2.19.md) `stage-1.2.19` — 8 files

This stage is part of behind-the-scenes setup and upgrade work for the Sites extension. It is a chain of database migrations, meaning small ordered changes that reshape stored data as the product gains new features. Together they build and evolve the records used to remember hosted sites made from conversations.

The first migration creates the basic hosted site table. The second adds a required generation value, so each site record can track its version. The third lets a site point to a special homepage agent, with a rule that keeps that homepage link unique inside a workspace. The fourth carefully updates older data: if it can prove a private site was really the main agent’s homepage seed, it makes it visible to the workspace; otherwise it leaves privacy unchanged.

The later migrations add supporting details. One stores preview image location and size. Another stores share card image information and the content hash used to identify it. Then a deploy_generation counter is added to track deployments. Finally, source_manifest stores optional source description text. Each step also includes rollback instructions.

#### [Monitor, objective, report, and scheduled-pause extension migrations](stage-1.2.20.md) `stage-1.2.20` — 6 files

This stage is behind-the-scenes setup for several extensions. It runs during installation or upgrade, when the system prepares its database for features that need to remember work over time. A migration is a small change to the database structure, like adding a new shelf before storing a new kind of record.

The monitor migration creates the table for scheduled checks, tied to a workspace, conversation, agent, timing, status, and owner. The objectives migrations build storage for goals, their ordered steps, proof that steps were completed or blocked, and later verification checks. A second objectives migration adds a stored flag showing whether a step can run on its own, so the runner does not need to recalculate that each time.

The report digest migrations add storage for summaries of published reports after they are read, plus records of report-read turns where nothing changed. Finally, the scheduled-pause migration creates a durable place for delayed tasks that must resume later. Together, these tables let long-running work survive restarts and upgrades.

#### [Small utility and compatibility extension migrations](stage-1.2.21.md) `stage-1.2.21` — 4 files

This stage is shared behind-the-scenes setup work. It is made of small database migrations, which are versioned changes that create or adjust stored data as the system evolves. These migrations belong to extensions, but they help those extensions fit cleanly into the larger system.

The eval_env migration creates tables for fake email and calendar data used in evaluation or test workspaces. This gives tests a safe place to store pretend messages and events without touching real user data. The sample extension migration creates a very simple note table, with one text note per workspace, so the sample extension has something concrete to save and remove.

The web extension migrations are compatibility repairs for older stored web chat data. One backfills a small chat record into the shared extension store when older queue keys already contain a member email. The other moves saved chat titles from the web extension’s private storage into the main conversation table. Together, these changes make old extension data usable by the core system.

## [Process startup and service bootstrap](stage-2.md) `stage-2` — 6 files

This stage is the system’s “turn the key” moment. It happens before normal web requests, workers, or sandboxes start running. First, ufoctl in cli.py gives operators and developers a safe command-line front door for setup, running, inspection, packaging, and maintenance. Instead of editing internals by hand, they use this tool.

config.py reads the main ufo.toml deployment file and checks that required settings are present and shaped correctly, so mistakes are caught early. proxy_serve.py then prepares a few shared inputs that sandboxes and ingress need, such as which outside model-provider hosts are allowed and which database connection string to use.

serve.py is the main service launcher. It connects the web server, database job runner, sandboxes, extensions, connectors, storage, and background workers into one running service. ufo_ext_flagship.py plugs feature flags into Cloudflare Flagship, so behavior can be changed safely by configuration. product.py builds a product “census” from workspace activity, showing onboarding, tool connections, and payment funnel status for dashboards.

## [Pack selection and extension loading](stage-3.md) `stage-3` — 41 files

This stage happens during startup, before the product begins serving users. Its job is to decide what kind of product is being assembled, then safely load the add-on packages that make that product work.

First, the pack recipe acts like a menu choice. It says which tools, prompts, skills, apps, and integrations should be turned on for a local assistant, hosted assistant, billing test setup, evaluation run, or small sample product. This gives the system a clear shape before anything else is registered.

Next, the extension loader finds the installed extensions. An extension is an add-on package, and its manifest is a registration card listing what it offers: tools, workspace surfaces, object types, model providers, search or browser backends, jobs, skills, agents, hooks, and credential slots. The loader checks these packages against a lockfile, which is an approved list that helps ensure the loaded code is expected and unchanged.

Together, the pack chooses the recipe, the lockfile guards the door, and the manifests register the parts the running system can use.

### [Pack recipes and product composition](stage-3.1.md) `stage-3.1` — 7 files

This stage is part of startup configuration. Before the product can run, the system needs a “pack”: a recipe that says which features, tools, and prompt instructions should be switched on together. Each file here defines one of those recipes for a different situation.

The local assistant pack builds a full developer-friendly assistant setup. The billing assistant pack adds billing support so developers can test onboarding without using the larger hosted setup. The hosted assistant pack describes the cloud-ready product shape, with managed-service features, skills, and guidance enabled. The assistant evaluation pack keeps the normal assistant core but swaps out integrations that do not work in tests, adding evaluation tools like a Docker workspace instead.

The DSQA and GDPVal evaluation files provide preset testing shapes, from small core setups to richer search, browser, document, research, or full configurations. The sample pack is a tiny working example that proves the pack mechanism works end to end. Together, these files act like menu choices for assembling the running product.

### [Extension manifests and lockfile enforcement](stage-3.2.md) `stage-3.2` — 34 files

This stage is part of the behind-the-scenes setup before the system starts running user work. It is the extension gatekeeper. Extensions are add-on packages, and their manifests are “registration cards” that describe what each package provides.

The built-in app manifests register first-party workspace apps such as Chat, Wiki, Issues, Metrics, and Notification. The connector manifests describe links to outside services like Slack, iMessage, Pipedream, and content sources. The agent, skill, and productivity manifests add specialist helpers for research, coding, browsing, documents, objectives, and sites. The platform and operations manifests add support pieces such as scheduled jobs, debugging, Redis transport, reports, monitors, and web features.

The loader is the front door. It scans installed extensions, checks them against the lockfile, and only loads approved code that has not changed. The store supports catalog, install, and remove commands by updating that lockfile. The extension object file then exposes loaded extensions as read-only workspace objects, so people can inspect what is available without accidentally changing the installed system.

#### [Built-in app extension manifests](stage-3.2.1.md) `stage-3.2.1` — 9 files

This stage is the set of “registration cards” for the built-in workspace apps. It is behind-the-scenes startup support: when the host extension loader scans the system, these manifest files tell it which first-party apps exist, how to install them, and what to show in the workspace.

Each manifest describes one app’s moving parts. Artifacts, Chat, Code, Radar, and Wiki register the agent that users can interact with and the home skill that powers the app’s main page. Issues adds its needed setup information and extra skill files, so the platform knows what must be prepared before use. Meetings and Metrics also declare required accounts or keys, plus scheduled jobs such as meeting-related work or reports. Notification is broader: it exposes tools, a notification object type, a background job, an agent, and a homepage skill.

Together, these files act like labels on drawers in a workshop. The apps may contain the real tools elsewhere, but these manifests tell the platform where each drawer is and what it contains.

#### [Connector and external service extension manifests](stage-3.2.2.md) `stage-3.2.2` — 7 files

This stage is part of the system’s behind-the-scenes setup. Each manifest is like a registration card that tells the main UFO application what an external service extension can do and how to plug it in safely. These files do not usually run the main work themselves. Instead, they make services visible to the host so the rest of the system can use them.

The Composio and Pipedream manifests register connector providers, including OAuth sign-in routes, which are web paths used when a user grants access to another service. Pipedream also registers a broker, the part that runs actions through that provider. The connectors manifest declares shared connector tools, object types, and assistant prompt text. The gbrain and sources manifests describe external content sources, the credentials they may need, and the stored objects or sync jobs they support. The iMessage manifest registers a messaging surface, user action, and required deployment secrets. The Slack manifest registers incoming Slack messages, needed credentials, tools, and background hooks. Together, these manifests let UFO discover outside services at startup and route messages, credentials, syncing, and actions to the right extension.

#### [Agent, skill, and productivity extension manifests](stage-3.2.3.md) `stage-3.2.3` — 7 files

This stage is shared behind-the-scenes support for loading extra abilities into the assistant. Each manifest is like a registration card: it tells the host system what an extension offers, what tools it needs, and what smaller specialist agents or skill folders should be made available.

The brief-pipeline manifest adds three helper-agent stages and one skill package for turning a request into a structured brief. The browser manifest registers browser tools, a browser-focused subagent, its prompt text, and outside dependencies. The coding manifest introduces coding workers, the tools they may use, their prompts and models, and their skill folder. The documents manifest makes writing, review, styling, and drafting skills discoverable, along with a document-writing subagent. The objectives manifest adds tools and reminders that keep long-running goals active across conversation turns. The research manifest lists web research tools, research subagents, prompts, saved skills, and shared conversation data. The sites manifest registers website-building tools, prompts, skills, background jobs, hooks, user-facing surfaces, and site-related object types. Together, these files let the runtime assemble the right toolbox before work begins.

#### [Platform, scheduling, and operations extension manifests](stage-3.2.4.md) `stage-3.2.4` — 8 files

This stage is shared start-up support for optional parts of the UFO system. Each file is a manifest, which is like a registration form. When the host application starts, these manifests tell it what extra surfaces, tools, jobs, and backends are available, so the code can be mounted and run at the right time.

The debugger manifest adds a user-facing debug area and a tool for reporting workspace problems to operators. The monitors manifest registers monitor objects, a monitor action, and a clock-driven job that checks probes when they are due. The Redis hub manifest offers Redis, a separate data service, as a hub and terminal transport option. The report digest manifest adds report objects, a writing skill, a scheduled digest writer, and an admin rebuild tool. The scheduled-tasks manifest registers tasks that can wait, repeat, and be used by agents. The self-improvement manifest schedules evaluation work. The UFO manifest exposes the terminal stream used by the shell client. The web manifest adds browser features, chat tools, feature flags, and background jobs.

## [First-run onboarding and workspace provisioning](stage-4.md) `stage-4` — 6 files

This stage is the front door of a new installation. It runs during first setup, and later supports people joining or choosing workspaces. A workspace is the shared place where members, assistants, and settings live. The onboarding code creates the first workspace, adds the first administrator, creates the main assistant agent, checks required secrets, and lets installed extensions add their own pieces.

The control API is the gatekeeper used by the Rust control plane. It creates or joins workspaces, checks whether someone belongs, lists available workspaces, counts them, and pages through invitations, so these rules stay consistent. The seats code manages who may use the assistant inside a workspace, and protects against removing the last seated admin. Provisioning applies agents shipped with extensions, adopting existing ones where possible and avoiding overwriting user edits. Agent setup code reports what each shipped agent still needs, such as credentials, account connections, or schedules. Seed code adds a fresh demo conversation, after removing any older copy, so newcomers can see the portal in action.

## [Per-turn host environment assembly](stage-5.md) `stage-5` — 9 files

This stage happens just before each agent turn. Its job is to assemble the “world” the model is allowed to see and use for that one step. It is like setting up a desk before someone starts work: the instructions are placed on the desk, the right tools are laid out, and any needed reference papers are opened.

The prompt, skill, and environment document resolution part gathers the written guidance. It loads prompt fragments, fills in templates, applies environment documents, chooses relevant skills, records fingerprints for repeatability, and lists the models available in this deployment.

The tool and spawn menu construction part builds the action menu. It decides which tools the model may call and which child agents or helper tasks it may start, while blocking confusing or unsafe names.

The main assembly file, `assemble.py`, ties these pieces together. It produces the complete per-turn package: prompt, tools, selected model, spawn options, skills, seeded files, and routing information, while making sure environment documents can restrict or shape the setup but cannot grant extra power.

### [Prompt, skill, and environment document resolution](stage-5.1.md) `stage-5.1` — 6 files

This stage is behind-the-scenes preparation for a model turn. Before the agent answers, it gathers the written instructions and reference material that shape what the model will see. The environment file defines “environment documents,” which are saved bundles of prompt changes, tool settings, skills, model choices, and files. It stores them by content digest, a unique label made from the file’s contents, so runs can be replayed exactly.

The delivery register loads shared answer-writing rules, such as how concise responses should be, so every kind of agent follows the same standards. The prompt renderer then fills in prompt templates, checks that all blanks were filled, and makes a fingerprint of the final prompt so changes can be tracked.

Skills are small instruction folders. The skill runtime reads and registers them, while the model catalog file creates one built-in skill that accurately lists the models available in this deployment. Finally, skill selection chooses which skill descriptions fit into the current turn, like packing the most useful tools into a limited toolbox.

### [Tool and spawn menu construction](stage-5.2.md) `stage-5.2` — 2 files

This stage prepares the menu of actions available on a single turn. Before the system asks the model what to do next, it must know exactly which tools are allowed, which object operations can be called, and which child agents or helpers can be started. This is like setting out the correct tools on a workbench before a job begins.

The tool registry is the rulebook for that workbench. It defines what counts as a tool, how its name and description are shown to the model, and how the system finds the real code when a tool is requested. It also blocks unsafe or confusing entries, such as duplicate names or names reserved for special system behavior.

The spawn catalog builds the separate list of things that may be spawned, meaning started as child tasks or agents, during this turn. It turns those allowed targets into clear instructions, including valid names and required input fields. Together, these files create a safe, precise action menu for the current turn.

## [Ingress, authentication, and surface routing](stage-6.md) `stage-6` — 24 files

This stage is the system’s front gate and switchboard. It sits at the edge of the service during normal use, where requests first arrive from browsers, terminals, Slack, iMessage, public app links, OAuth callbacks, operator tools, and developer/debug surfaces. Its first job is to decide who or what the request belongs to. Then it sends the request to the right workspace, member, conversation, hosted app, or runtime action.

The login and token checks act like security labels. Member login tokens prove the user and workspace. Surface tokens give limited proof for public entry points before a normal session exists. Shared signing code makes sure these labels cannot be quietly altered. Sandbox ingress tokens add time limits and purpose checks for preview and hosted-app access.

The member and operator surfaces are the visible doors. The web app, chat integrations, terminal client, hosted sites, and operator tools all translate outside actions into UFO conversation work, then return replies and live updates.

The sandbox ingress host adds one more routing layer. It creates and verifies special hostnames for sandboxed sites, tying each safe URL to a specific conversation and port.

### [Login, session, and signed-token checks](stage-6.1.md) `stage-6.1` — 4 files

This stage is shared security support that runs before protected parts of the system do their work. Its job is to prove that a request is carrying a trustworthy “label” about who it is for, without needing to keep all session details in server memory.

The bearer token code creates and checks signed login tokens for UFO members. These tokens say which user and workspace the request belongs to, and the signature proves the text was not changed. The surface token code does a narrower job for public entry routes called surfaces. It identifies the workspace early, before normal login or cookies are available, like a sealed address label rather than a full access pass.

The token signing code is the common stamp maker and stamp checker. Other parts use it to pack small pieces of data into tamper-resistant tokens. The sandbox ingress token code adds time limits and purpose checks for access to sandboxed app ports, so preview links, cookies, and reports cannot be swapped or misused.

### [Member-facing and operator-facing surfaces](stage-6.2.md) `stage-6.2` — 19 files

This stage is the system’s set of front doors. It is used during the main work loop, whenever a member, operator, or outside chat service talks to UFO. The web portal serves the browser app, signs members in, shows agents and transcripts, sends messages, and streams replies. Its panels turn button and form actions into the same chat-style work path, while community and starters fill the home screen with public skills and suggested starting ideas.

Other doors connect outside apps. Slack, iMessage, and the terminal UFO client translate incoming messages into UFO conversation turns, then translate replies, files, status updates, and questions back out. Slack helper files keep mentions readable and make bot attribution clear.

Hosted-site files open public or permission-checked app pages, build safe sandbox URLs, and route browser traffic to stored files or live sandbox ports. Operator tools provide a debugger, workspace directory, memory viewer, and problem reports, with strict operator sign-in rules. Behind all of this, the hub and surface bridge carry live updates, replay missed events, match users to workspaces, and deliver final replies.

## [Conversation admission, scheduling, and cancellation](stage-7.md) `stage-7` — 8 files

This stage is the traffic controller for conversation work. It sits behind the scenes during the main work loop, whenever a person sends a message, a timer wakes up, a monitor notices something, a paused task resumes, or a running reply is cancelled. Its job is to decide what becomes queued work and when it may run.

The admission code is the front door. It checks whether a new turn, meaning one unit of conversation work, can start now, must wait, should join an already-running turn, or should be refused for policy or billing reasons. The dispatch code is the handoff point. When a turn finishes, pauses, fails, or is cancelled, it starts the next waiting turn only if the conversation is free.

The audience and visibility pieces act like privacy guards. They decide who may see or join each turn, and when the agent should stay quiet in a busy shared room. The durable queue and stop pieces save waits and cancellations safely, notify listeners, and can start a follow-up. The site report file turns a broken hosted page into a controlled message so the agent can repair it.

### [Audience, visibility, and participation decisions](stage-7.1.md) `stage-7.1` — 4 files

This stage is shared behind-the-scenes support that runs around each conversation turn. Its job is to answer a simple but important question: “Who is allowed to see or take part in this?” It keeps replies aimed at the right people and helps stop private workspace or member information from appearing in the wrong room.

The audience code gives each turn a clear audience name, such as one member, a whole workspace, a room, or a room shared outside the workspace. It can compare and translate these names so the rest of the system knows what is safe to read or show. The subjects code supplies smaller labels for visibility, separating messages visible to everyone from messages tied to one member. The ambient reply code is a gatekeeper for busy group threads. If people are chatting without directly calling on the agent, it decides whether the agent should stay quiet instead of starting a costly full response. The web audience code applies similar access rules in the web portal, including admin tools for granting access and auditing private transcript views.

### [Durable queues, waits, and stop requests](stage-7.2.md) `stage-7.2` — 1 files

This stage is behind-the-scenes support for keeping long-running conversation work under control, even when people cancel things or the system restarts. Its wider job is to make choices about queues, pauses, wake-ups, repeated jobs, and stop requests durable, meaning they are saved so they are not lost after a crash.

The file in this stage, `stop.py`, focuses on one important case: stopping a running turn in a conversation. A “turn” is one unit of work in the conversation, like one reply being produced. Before stopping it, the code checks that the turn really belongs to the conversation asking for the stop. That prevents the wrong work from being cancelled. It then cancels the turn in a safe way, so the rest of the system sees a clean ending rather than a half-broken task. It also notifies any live listeners, such as clients waiting for updates, that the turn has ended. If requested, it can then advance the conversation into a follow-up turn, so cancellation can lead smoothly into the next step.

## [Runtime fleet coordination and crash recovery](stage-8.md) `stage-8` — 2 files

This stage is behind-the-scenes support for a running fleet of serve processes. A serve process is a live worker that can pick up and run conversations or workflows. The job here is to make sure the fleet knows which workers are alive, which work is claimed, and what to do when something stops halfway through.

`runtime_instance.py` is the fleet’s attendance sheet and cleanup crew. Each running process records that it is alive so other processes do not mistake it for a dead one. It also runs background repairs. If a workflow was cancelled, abandoned, or left in an uncertain state after a crash, this code finds it and moves it toward a safe restart or cleanup. It also fixes “child” work that was launched by a “parent” turn when the parent crashed or was cancelled.

`delivery.py` is a fallback mail carrier for child-agent results. Normally a child’s finished result is handed back directly to the parent conversation. If that handoff was missed, this file finds the result and delivers it so the parent can continue.

## [Agent turn execution loop](stage-9.md) `stage-9` — 13 files

This stage is the main work loop for one agent turn: a single piece of queued work in a conversation. It starts when queue.py claims a turn from the durable queue, meaning a work list that survives crashes. It builds the needed model, tools, sandbox, billing, and permissions, then hands control to engine.py. The engine is the safety rail. It loads the conversation, runs the turn, saves progress, and records a final result even if something fails.

Before the model answers, the context and compaction parts gather the conversation history and shrink older material into summaries when the model’s reading limit would be exceeded. Then agent.py runs the core loop: send messages to the model, stream the reply, run any requested tools, and decide whether more rounds are needed. The model request stage cleans and records streamed output as it arrives.

hooks.py lets extensions inspect or block tool actions at key moments. Finally, the transcript and publication parts save the timeline, costs, file changes, and final answer, while live updates are sent to viewers.

### [Context loading and conversation compaction](stage-9.1.md) `stage-9.1` — 2 files

This stage is behind-the-scenes support that prepares the material the AI model reads before it answers. The system may need to include old messages, notes about changed files, writing rules, saved hints, source panels, artifacts, questions, and summaries. But an AI model has a limited context window, meaning it can only read so much text and media at once. This stage keeps that bundle useful without letting it grow too large.

The context harness in `core/src/ufo/harness/context.py` acts like a traffic monitor. It checks whether the conversation is getting close to the model’s limit and decides when shortening is needed. If so, it runs the process in a controlled way.

The compaction logic in `core/src/ufo/runtime/compaction.py` does the actual shrinking. It keeps the newest messages exactly as they are, because recent details matter most. It turns older messages into a structured summary that can be checked later. It also saves both the original and compacted versions, so the system can audit what changed or replay the conversation if needed.

### [Model request, stream handling, and intent runs](stage-9.2.md) `stage-9.2` — 2 files

This stage is part of the main conversation loop, when the system asks an AI model for the next response and listens as the answer comes back piece by piece. It turns the provider’s live stream into safe, structured records the rest of UFO can use, including visible text, hidden reasoning notes, tool requests, usage counts, timing, and retry information if something goes wrong.

The replies.py file is like a filter at the front of the speaker. Models may include special “reply-to” sections meant for internal routing, not for the user. This file detects those sections and removes their hidden markup even while text is still streaming, so users only see the clean message.

The rounds.py file manages one complete model “round”: send the request, read the stream until it ends, publish safe text as it arrives, and gather all final results. Together, these files make model output usable, private parts controlled, and each response ready to become part of the transcript.

### [Transcript, cost, and final-result publication](stage-9.3.md) `stage-9.3` — 5 files

This stage is the “show what happened” part of a turn. It runs alongside the main work and is especially important near the end, when the system must publish the final answer, status, costs, and evidence of what changed. It keeps a clear record so user interfaces, portals, and later diagnostics can trust what they display.

The steps module takes the raw history from DBOS, the workflow storage system, and turns it into a readable timeline: model messages, tool results, timings, and workflow events. The transcript module safely reads and writes the conversation transcript in blob storage, making sure an older or smaller copy does not overwrite a newer, better one. The activity module translates raw tool calls into friendly progress text, like a mechanic changing “internal part number” into “checking the brakes.” The workspace changes module asks the sandbox for Git-style file differences and saves a summary of changed files. The Redis stream hub carries live frames, such as text and status updates, to viewers and supports reconnecting from the right point when possible.

## [Tool workbench and sandboxed execution](stage-10.md) `stage-10` — 59 files

This stage is the system’s safe workbench. It is shared support used whenever an agent needs to do real work, such as running a command, editing a file, using a browser, processing a document, or asking for human help. The workbench is like a supervised workshop: tools are available, but each action must go through approved doors.

The sandbox and workspace part provides the private project folder and command runner. It can use local machines, Docker containers, cloud sandboxes, or a user’s terminal, while keeping files, logs, and long-running tasks tied to the right conversation. Browser automation adds a controlled browser for page reading, clicking, typing, downloads, and screenshots. Document and office scripts handle PDFs, Word, PowerPoint, and spreadsheets by unpacking, annotating, repairing, rendering, or recalculating them.

builtins.py defines the main tool set agents can call, such as shell, file, sharing, questions, skills, delegation, and secrets requests. context.py gives each tool its allowed workspace and result format. bridge.py and tool_bridge.py let live sandbox code request approved tools safely and durably. notify_tool.py adds guarded user notifications.

### [Sandbox carriers, workspaces, terminals, and command tasks](stage-10.1.md) `stage-10.1` — 12 files

This stage is shared behind-the-scenes support for any conversation that needs a place to work. It gives each conversation a private workspace, like a temporary project folder, and a way to run commands there. The conversation workspace code creates, reopens, lists, reads, writes, trims, and deletes files without losing the right sandbox. The session and protocol code provide one common “socket” for command running and file operations, so tools do not care whether the workspace is local, Docker, E2B, or a user terminal.

The local carrier runs directly on the host for development. The Docker carrier starts and manages per-conversation containers, with network traffic routed through a proxy. The E2B carrier does the same on a cloud sandbox service, including ports and leases. The terminal carrier sends work to a user’s already-open terminal, while the Redis terminal bridge keeps terminal sessions alive across different server pods.

Selection code chooses the active carrier while preserving old ones for saved sandboxes. Environment code supplies safe placeholder credentials. Task code keeps long-running command logs durable. The REPL extension preserves Python and JavaScript sessions across calls.

### [Browser automation backends](stage-10.2.md) `stage-10.2` — 23 files

This stage is the browser “engine room” for a turn of work. It gives agents a safe way to borrow a browser, understand what is on the page, act on it, and then clean everything up afterward.

First, the hosted and sandbox providers supply the browser itself. They may lease a remote Browserbase or Browser Use session, or start a private sandbox Chrome. The backend opens the control connection only when a tool needs it and releases it when the turn ends.

Once connected, the CDP transport layer is the control cable to Chrome. CDP, Chrome DevTools Protocol, is Chrome’s remote-control language. It sends commands, checks messages, runs small page scripts, and reports risky failures carefully.

Page modeling then turns the live page into useful text, controls, and screen positions. Action handling converts tool requests into clicks, typing, scrolling, screenshots, and waits. State helpers manage tabs, forms, pop-up dialogs, and downloads.

The tools.py file exposes all of this as agent tools, while backend.py ties each tool call to the current leased browser session.

#### [CDP transport and runtime safety](stage-10.2.1.md) `stage-10.2.1` — 4 files

This stage is shared behind-the-scenes support for talking to Chrome safely. It is the project’s “control cable” to the browser, used whenever higher-level code needs to inspect a page, click, type, open tabs, or run small scripts.

The cdp.py file manages the Chrome DevTools Protocol connection. This protocol is Chrome’s remote-control channel. The file opens one WebSocket, which is a two-way network pipe, sends commands through it, matches replies to the right requests, and routes browser events to any code that is listening.

The wire.py file checks the raw JSON messages that travel over that pipe. JSON is loose text data, so this file verifies the expected shapes before the rest of the system trusts them.

The runtime.py file uses the same protocol to run JavaScript inside the current page. It wraps the low-level messages in simple Python calls and reports page-side failures clearly.

The errors.py file defines careful error types for risky browser actions, where a retry might repeat a real click or typed text.

#### [Page content modeling and element discovery](stage-10.2.2.md) `stage-10.2.2` — 4 files

This stage is shared behind-the-scenes support for understanding a live browser page. Before an AI can click a button, read an article, or fill a form, the system must turn the messy visual page into a smaller, safer description it can reason about.

The main builder is page.py. It asks the browser for the accessibility tree, which is the browser’s structured list of useful controls such as links, buttons, text fields, and headings. It also records where elements are on the screen, including inside frames, then produces either an action-focused tree or a readable Markdown-style page view.

content.py wraps these abilities as browser content tools. It lets higher-level commands request a structured tree, plain text, or element search without needing to know browser details.

find.py is the search helper. It looks through the text form of the accessibility tree, checks whether an AI’s proposed match really exists, and formats matches clearly.

coordinate.py keeps sight and action aligned. It converts between real browser pixels and the vision model’s coordinate grid, so clicks land where the model intended.

#### [Browser action execution and input handling](stage-10.2.3.md) `stage-10.2.3` — 5 files

This stage is part of the main work loop, where an automation agent’s plan is turned into real browser behavior. It starts with actions.py, which acts like the rulebook. It lists the browser actions the agent is allowed to request, such as clicking, typing, scrolling, waiting, taking screenshots, and using shortcuts, and defines what information each request must include.

Before anything reaches the browser, fixup.py tidies the plan. It fixes small, predictable mistakes, like adding a focus step before typing or filling in a sensible wait time when one is missing.

computer.py is the main driver. It receives the cleaned-up action and sends the matching browser input, such as mouse movement, clicks, scrolls, screenshots, or waits. For keyboard work, it relies on keys.py, which translates text and shortcuts into Chrome DevTools Protocol messages. That protocol is Chrome’s remote-control language. keys.py also tracks held keys so combinations like Ctrl+C or shifted characters work correctly.

Finally, settle.py waits until the page has reacted enough to continue, while ignoring unrelated background noise such as ads or tracking requests.

#### [Browser page state, tabs, forms, dialogs, and downloads](stage-10.2.4.md) `stage-10.2.4` — 5 files

This stage is the browser’s working memory and control desk during the main work loop. It keeps track of what Chrome is doing so higher-level tools can open pages, fill forms, answer pop-ups, and collect downloaded files without getting lost.

The center is session.py. It represents one live connection to Chrome and holds the shared state used by the other pieces. Tools enter through this session when they need to control the browser. tabs.py keeps the tab list accurate and performs tab actions, such as opening a new tab, switching tabs, closing one, or navigating to a page. forms.py fills text fields and attaches files to upload buttons, then checks that the browser really received the file. dialogs.py watches for JavaScript dialogs, meaning small pop-up boxes made by the page, and responds quickly so the page does not block. It also records the result. downloads.py watches for files being saved, can push Chrome to download instead of previewing, and waits until the file is ready. Together these parts make page workflows reliable.

#### [Hosted and sandbox browser providers](stage-10.2.5.md) `stage-10.2.5` — 3 files

This stage is behind-the-scenes support for web browsing work. Instead of always using a browser installed on the same machine, it gives the system other ways to “rent” a browser for a turn. A lease means a temporary browser session that is opened, used, and then cleaned up.

The Browser Use extension adds two tools, browser_task and wide_browse. They let the rest of the system request web automation in the usual way, but the actual browsing is done by Browser Use’s hosted agent, an outside service.

The Browserbase extension connects to Browserbase, which provides a remote Chrome browser. For one browser turn, it creates the remote session, transfers needed files into or out of it, and shuts it down afterward.

The sandbox Chrome extension starts a private headless Chrome, meaning Chrome without a visible window, inside the turn’s sandbox. It connects using Chrome’s DevTools protocol, a control channel for driving the browser, and carefully tears it down if the lease ends or recovery fails.

### [Document and office automation scripts](stage-10.3.md) `stage-10.3` — 19 files

This stage is a toolbox of on-demand scripts used when the system needs to inspect, change, or prepare office documents. It is not the main work loop; it is shared support that runs when a workflow asks for a specific document job.

The document-review scripts keep a review organized. constants.py names the saved state and log files. models.py defines what a review issue looks like and how to turn it into a readable comment. manage_state.py records sections, claims, issues, and the final summary. annotate_pdf.py, annotate_pptx.py, and annotate_xlsx.py then take those saved issues and place them into PDFs, PowerPoint slides, or Excel cells as visible notes.

The Office scripts treat DOCX and PPTX files like zipped folders of XML, which is the text-based format inside them. DOCX and PPTX unpack.py scripts unzip and clean these folders; pack.py scripts rebuild usable Office files. DOCX comment.py adds Word comment data, and accept_changes.py uses background LibreOffice to accept tracked edits. PPTX slides.py manages slides and thumbnails, while repair.py fixes known PowerPoint packaging problems.

For spreadsheets, _soffice.py helps run LibreOffice silently, and recalc.py refreshes formulas. The PDF tools render pages as images, inspect page layout, add annotations, and fill real PDF form fields.

## [Delegation, subagents, and multi-agent pipelines](stage-11.md) `stage-11` — 11 files

This stage is the system’s way of handing off work during the main conversation. When the main agent sees a task that needs a specialist, it can start a child agent, or subagent, much like asking a coworker to handle one part of a project. The contracts file defines the agreed “forms” for inputs and outputs, so each helper receives and returns data in the expected shape. The core subagents file checks those forms, decides which helpers are allowed, starts the child work, waits when needed, and returns the result safely. The core profiles file supplies a default helper when no extension provides one.

The extensions add specialized workers. Browser files define browser-focused helpers and tools for one or many web sessions. Document files define a prose-writing helper. Research files define normal, deep, and wide research flows, including saving partial results if interrupted. Site files define a website-building helper that leaves finished files in the original workspace. The brief pipeline is a three-step assembly line: outline, draft, then critique.

## [External connectors, credentials, and egress mediation](stage-12.md) `stage-12` — 32 files

This stage is the system’s controlled doorway to the outside world. It is used both during setup, when a user connects accounts, and during the main work loop, when agents call tools or send requests. Its job is to make outside services usable without spreading private keys and tokens through the system.

Connection setup and credential vaulting is the lockbox. It records which workspace owns each connection, guides OAuth sign-in or API-key setup, and stores secrets safely. Connector action execution and proxy access is the guard at runtime. It checks whether an outbound request is allowed, adds the right secret only when permitted, and lets proxies report usage.

The shared connector files provide the common doorway for services like Gmail, GitHub, Composio, and Pipedream. The Composio client talks to Composio to create login links, list tools, run actions, and prepare uploads while keeping real third-party tokens outside UFO. iMessage support chooses between Spectrum Cloud, a fake local development line, or no iMessage setup. Slack hooks add service-specific finishing touches around sends and account connection events without blocking the main flow.

### [Connection setup and credential vaulting](stage-12.1.md) `stage-12.1` — 13 files

This stage is the system’s front door for connecting outside accounts and keeping their secrets safe. It runs mostly during setup, before agents can use services like GitHub, Slack, OpenAI, Anthropic, iMessage, or API-key based tools. The grants code records who owns a connected account and which agents may use it. The credentials vault encrypts API keys, checks that secret requests are genuine, and limits where each secret may be used.

Several files act as bridges to outside sign-in services. Composio and Pipedream providers start hosted OAuth flows, where a user approves access on another website. The CLI callback pages finish the redirect back into UFO. The Pipedream client lists allowed connectors and prevents one workspace from using another’s account, while its token helper turns a connected GitHub account into safe sandbox credentials and commit identity.

Other parts cover direct credentials and special apps. Keyed connectors describe API-key services and inject real keys only for approved requests. Source direct auth fetches stored keys for sync jobs. Anthropic and OpenAI login files validate and store personal access. Slack and iMessage tools guide human setup and claim the needed workspace or phone identity.

### [Connector action execution and proxy access](stage-12.2.md) `stage-12.2` — 14 files

This stage is the system’s gateway to the outside world during the agent’s main work. It lets an agent find tools, call them, move files, search the web, and reach approved services without handing it raw secrets.

The access files act like a guard booth. Egress rules turn user grants, connector settings, storage settings, and credentials into exact allow-or-deny rules. The egress resolver decides which outside destinations and injected secrets are safe. Egress control is the private phone line used by the Rust network proxy to ask for permission, fetch credentials, and report usage for billing.

The connector files are the adapters. Composio and Pipedream brokers discover tool catalogs, describe actions, run them, and prepare uploads or downloads. Their proxy files rewrite normal HTTP requests so the real service token stays hidden. Composio’s MCP session supports catalog search. The general connector tools expose safe find, inspect, run, and file-transfer actions to the agent.

Other extensions add tool sources: evaluation tools with seeded fake workplace data, workspace MCP servers, Perplexity-backed search and fetch, and research tools that route searches to the configured provider.

## [Workspace objects, artifacts, sites, and portal data](stage-13.md) `stage-13` — 43 files

This stage is shared behind-the-scenes support for the workspace. It defines the “things” the system works with, such as agents, members, conversations, pages, reports, files, websites, todos, and long-running objectives. These things are stored as readable records, checked before use, and shown safely in the portal.

The object system is the rulebook. It decides what kind of object something is, whether a user may see or change it, and how changes are recorded. The artifacts and sites part is the display and delivery layer. It serves shared files, builds previews, tracks hosted websites, and exposes the right items back into conversations and portal panels.

The objective tools and store add durable planning. They remember planned steps, work attempts, and verification results, so a task is not marked done just because someone claims it is. The listings helper gives portal screens stable “next page” browsing. The todo extension adds simple conversation checklists. Together, these parts make workspace data reliable, permission-aware, and visible in the right places.

### [Object system and permission-safe mutations](stage-13.1.md) `stage-13.1` — 23 files

This stage is shared behind-the-scenes support for the whole workspace. It is the system’s rulebook for “objects,” meaning named records such as agents, members, pages, reports, skills, connected accounts, and hosted sites. Before anything can be shown or changed, this layer checks what kind of object it is, who may see it, what actions are allowed, and whether the change should be recorded first.

The central objects file is the main gate. It lets extensions register object kinds, lists and reads objects, routes create/update/delete requests, blocks changes to read-only records, and logs planned mutations before they happen. The object name file makes sure every object identity has a safe, predictable kind and name, like checking an address before delivering mail. The object views file turns internal actions into safe descriptions for the AI model or user interface, including what inputs each action accepts.

The sub-stages plug real object families into this gate: workspace and member records, agents and prompt approvals, work items like monitors and reports, knowledge pages and memory, plus connected accounts, sites, and user-created skills. Together they make many different assets behave consistently and safely.

#### [Built-in workspace and host object kinds](stage-13.1.1.md) `stage-13.1.1` — 5 files

This stage is shared behind-the-scenes support for the workspace. It defines several built-in object kinds, which are standard shapes the system uses to show important workspace information safely. These objects are mostly read-only, meaning callers can inspect them but cannot freely change them.

The conversation object gives tools, pages, artifacts, and scheduled jobs a safe way to refer back to earlier chats for the selected agent. It does not allow anyone to create or edit conversations. The credential slot object shows which extension-defined secret slots exist and whether they are filled, but never reveals the secret value; admins can only clear a stored value. The member object shows who belongs to the workspace, who is an admin, and who has an active seat. It also provides the controlled path for admins to add members. The surface object lists available chat entry points and whether they are connected. Finally, the workspace object gives a read-only summary of the workspace and its people. Together, these files act like labeled windows into the workspace, with only a few locked controls where change is allowed.

#### [Agent objects and prompt governance](stage-13.1.2.md) `stage-13.1.2` — 2 files

This stage is shared behind-the-scenes support for managing agents safely. An agent is the system’s editable record for a helper: what it is called, which AI model it uses, what instructions, or prompt, it follows, who is allowed to see or change it, and whether it is active or archived.

The agents file is the main “filing cabinet” for those records. It gives the rest of the system consistent ways to create an agent, read its details, update it, archive it when it should no longer be used, and restore it later. It also keeps these actions tied to permissions, so members only do what they are allowed to do.

The governance file adds a safety gate around prompt changes. Because a prompt can strongly affect how an agent behaves, changes are first saved as proposals. An approval step must happen before the new prompt is applied. The approval also checks that the current prompt is still the one the proposal was based on, preventing someone from approving an outdated change by accident.

#### [Workflow, notification, monitor, and report objects](stage-13.1.3.md) `stage-13.1.3` — 5 files

This stage defines several “work item” object types that the system can show, inspect, and manage during normal operation. These are not the core chat engine. They are the behind-the-scenes records that help people and agents track ongoing work, alerts, and results.

Notifications are made into readable objects that a member or admin can list, open, check for status, and delete after they are handled. Monitors are shown as objects too, so armed watches can be listed, inspected, or removed; creating them happens elsewhere because the first check must run during a live chat turn. Report objects expose finished or failed scheduled radar runs as read-only records, including their digest, task name, linked conversation, status, and shared files.

Scheduled tasks turn recurring agent work into normal workspace objects. They also provide a durable wait tool, so an agent can pause until a message arrives or a timer ends without losing the workflow. The visibility rules act like a privacy gate, deciding who may read a task’s private prompt and description based on its reporting location and creator.

#### [Knowledge source, memory, and page objects](stage-13.1.4.md) `stage-13.1.4` — 4 files

This stage is shared behind-the-scenes support for knowledge that comes from outside the conversation. It defines the “objects” users and agents can see, open, or manage when the system works with saved memory, synced documents, and external sources.

The gbrain object file is the control panel for markdown knowledge sources. A source can be a GitHub repository or a server folder. Users can register it, inspect it, resync it, share it, or remove it, and its markdown files become searchable memory.

The memory object file exposes saved memory items and member profiles as read-only records. “Read-only” means other parts of the system can list and open them, but not casually change them. It also checks permissions so people only see what they are allowed to see.

The pages file turns synced documents into workspace page objects. Users can list pages and read a limited-size copy, while admins can forget pages. Nobody creates or edits these pages manually.

The tools file registers outside provider accounts and streams, and lets conversations subscribe so they wake up when synced pages change.

#### [Connected accounts, hosted sites, and user-created skills](stage-13.1.5.md) `stage-13.1.5` — 4 files

This stage is shared support for workspace assets that people create or connect while using the system. It makes these assets behave like normal workspace objects, so they can be found, inspected, shared, protected, or removed in a consistent way.

The skill creation manifest is the front door for member-authored skills. A skill is a reusable instruction or capability saved by a workspace member. The manifest tells the system that skills can be created, edited, searched, loaded, indexed, and deleted. The skill store is the filing cabinet behind that door. It saves and retrieves skill content, lists available skills, and blocks unsafe cases such as bad names, duplicate names, accidental overwrites, or too many saved items.

The connectors object file does a similar job for third-party accounts, such as external services someone has linked. It separates the account connection from an agent’s permission to use it. The sites object file brings hosted websites into the same object system, so they can be listed, shared, made public or private, inspected, and deleted.

### [Artifacts, previews, hosted sites, and app pages](stage-13.2.md) `stage-13.2` — 16 files

This stage is shared support for things a user can see or download after the main agent work: files, previews, hosted web pages, sources, tasks, and small app pages. It is like the display shelf and shipping desk for the system.

For shared files, the artifact files define what a downloadable artifact is, create signed links that prove permission and expire, and serve the bytes only when the link is valid or safely refreshed. Image and document preview files check uploads, reject unsafe or oversized media, call outside preview services when needed, and store simple preview records.

For websites, the site store records who owns each hosted page, where it runs, what files it uses, and what preview or share card belongs to it. Source handling moves site files between storage and the sandbox. Site tools let agents build, serve, publish, audit, and bind sites. The application builder and audit guide generated app pages from plan to code to browser checks to deployment.

Finally, conversation slots expose the right items back to the portal: sites, research sources, and scheduled automations, but only for authorized conversations.

## [Source synchronization, indexing, memory, and enrichment](stage-14.md) `stage-14` — 75 files

This stage is the system’s knowledge intake and preparation area. It runs during normal operation, after accounts or folders have been connected, and keeps outside information fresh and useful for agents.

First, the provider polling and page storage part acts like a loading dock. Connectors visit services such as Google tools, GitHub, CRM, support, HR, finance, recruiting systems, or Markdown folders. Each connector knows how to fetch records in batches, remember its place with cursors, notice deleted items, and turn everything into a common “page” shape. Shared sync code saves the page text and metadata, records warnings or changes, and marks updated pages for later processing.

Then the index, memory, and profile derivation part turns stored pages into usable knowledge. It breaks long text into chunks, creates embeddings, meaning number patterns used for semantic search, and stores them locally or in external services such as OpenAI or Turbopuffer. Memory tools search, summarize, clean up, and condense this material into facts, profiles, wiki-like notes, and recall results. Optional enrichment can also add consent-based external profile details for members.

### [Provider polling and page storage](stage-14.1.md) `stage-14.1` — 63 files

This stage is the system’s main intake and storage loop for outside information. It talks to many providers, reads records in safe batches, turns them into standard pages, stores the page text and details, and tells the indexing system which pages changed.

The connector groups are the adapter plugs. Some read Markdown from folders or GitHub repositories. Others register available connectors and prepare account feeds when a user links a service. The Google, workplace, engineering, CRM, support, finance, HR, and recruiting connectors each know how to ask their own service for data, follow that service’s paging rules, and reshape the results into the same internal format.

The shared files are the machinery underneath. The REST helper provides the web client, retries, and page-by-page fetching. The connector contract defines what every adapter must provide and how to walk partitions, such as channels or repositories, without losing progress. The backend turns connector output into stored sync results, including changes, deletions, and warnings. The sync file ties it together by saving page bodies and exposing changed pages for indexing.

#### [Markdown and repository-backed page sources](stage-14.1.1.md) `stage-14.1.1` — 3 files

This stage is an intake area for the gbrain extension. Its job is to turn Markdown files into standard Page records, so the rest of the system can index or sync them without caring where they came from. It sits before the common source-sync pipeline, like a loading dock that labels and checks boxes before they enter a warehouse.

The folder source reads Markdown files from a local directory. It treats each file as one page and uses safety checks so the scan cannot wander outside the chosen folder. The GitHub source does the same kind of work for a repository online. It first checks whether the repository has changed, so it can avoid downloading it unnecessarily. When needed, it fetches the files and finds the Markdown pages.

The pages helper is the shared cleaner and formatter. It filters out paths that should not become pages, confirms the file is readable text, and picks a useful title. Together, these parts make different storage places look the same to the rest of the system.

#### [Source connector registration and account bootstrap](stage-14.1.2.md) `stage-14.1.2` — 2 files

This stage is behind-the-scenes setup for bringing outside services into the system. It makes sure the app knows which external source connectors exist, and it prepares the basic “feed” records needed when a member links an account. A connector is the adapter that knows how to talk to a provider such as Slack or Salesforce.

The registry file is like the system’s address book for connectors. It lists every supported provider and stores them under their public names, so other parts of the app can ask for “Slack” or “Salesforce” and get the right connector code.

The connected file runs when a member connects one of those provider accounts. It creates the first feed rows for that account’s main streams, so the rest of the system has something to read from and sync. If that first setup fails or leaves gaps, it also offers a retry path that can create the missing feed rows later. Together, these files turn “we support this provider” into “this member’s account is ready to use.”

#### [Google provider connectors](stage-14.1.3.md) `stage-14.1.3` — 8 files

This stage is a set of connectors for Google services. It is part of the main source-sync work: the system signs in with an authorized Google account, asks Google what data is available, converts that data into simpler records, and tracks changes so later runs do not have to reread everything.

Each file is a different “adapter” for a Google product. Gmail reads mailboxes and turns messages into searchable text while noticing new and deleted email. Drive lists files, shared drives, permissions, comments, and revisions. Docs and Sheets start from Drive file lists, then fetch document bodies or spreadsheet rows and flatten them into readable text records. Calendar reads events and also creates separate attendee records so invitees can be searched directly. Meet imports meeting transcripts and AI-written notes as recallable pages. Google Ads reads advertiser objects such as customers, campaigns, ads, and performance reports. The shared Google helper separates real permission problems from temporary quota limits, so the system can skip inaccessible data but retry when Google is only asking it to slow down.

#### [Workplace communication and knowledge connectors](stage-14.1.4.md) `stage-14.1.4` — 5 files

This stage is part of the system’s intake work: it connects to the tools people use every day and turns their conversations, emails, and documents into standard records the rest of the system can store, search, and reuse. Each connector is like an adapter plug for a different workplace product.

The Confluence connector reads Atlassian Cloud spaces, pages, blog posts, comments, groups, and audit entries, then converts wiki-style content into readable text. The Microsoft Teams connector uses Microsoft Graph, a web doorway into Microsoft services, to collect teams, channels, chats, and messages. The Notion connector reads users, pages, databases, comments, and page blocks, then flattens them into searchable text. The Outlook connector also uses Microsoft Graph, but for mail, contacts, calendars, and folders; it carefully follows change feeds, which are lists of updates delivered in pages. The Slack connector reads users, conversations, messages, threads, and senders. Together, these files make many separate workplace systems look like one consistent stream of source records.

#### [Work management, engineering, and operational connectors](stage-14.1.5.md) `stage-14.1.5` — 10 files

This stage is part of the system’s source-sync work loop: it reaches out to outside tools, reads what the current credential is allowed to see, and reshapes that data into standard “streams” of records the rest of the system can store, search, and recall. A stream is just a steady list of items, like pages moving along a conveyor belt.

Each connector knows the map and rules of one service. Airtable walks through bases, tables, and records. Asana reads workspaces, teams, projects, tasks, stories, and users. ClickUp follows its nested teams, spaces, folders, lists, tasks, comments, goals, and fields. GitHub discovers organizations and repositories, then fetches issues, comments, users, and related objects. Jira reads projects, issues, boards, sprints, comments, and users. Linear uses its GraphQL interface, a query-based API, to gather issues, projects, teams, comments, and workflow states. monday.com, PagerDuty, Sentry, and Wrike do the same for boards and items, incidents and on-call records, error reports and releases, or folders and tasks. Together, they turn many different tools into one common memory format.

#### [CRM, sales, advertising, and marketing connectors](stage-14.1.6.md) `stage-14.1.6` — 11 files

This stage is shared behind-the-scenes support for bringing business data into the system. It does not run the product by itself. Instead, it acts like a set of adapters, each one shaped to fit a different outside service. An adapter calls that service’s web API, meaning its online doorway for data, then turns the answers into a steady stream of records the rest of the sync system can store and search.

ActiveCampaign, Klaviyo, Mailchimp, HubSpot, and Typeform cover marketing, email, forms, and customer activity. Apollo, Attio, and Salesforce cover sales and CRM data such as contacts, companies, deals, tasks, notes, and opportunities. Calendly brings in scheduling data like event types, bookings, and invitees. Facebook Ads reads ad accounts, campaigns, ads, and daily performance. Instagram reads business accounts, posts, stories, and analytics through Facebook’s Graph API.

Together, these files hide the quirks of each service, such as paging through long result lists, and present everything as consistent source records.

#### [Customer support connectors](stage-14.1.7.md) `stage-14.1.7` — 3 files

This stage is part of the main syncing work. Its job is to visit customer-support tools and turn their data into source pages the system can store, search, and recall later. Think of it as a set of translators: each one speaks to a different helpdesk service, but all return records in a shape the rest of the system understands.

The Freshdesk connector knows how to log in to Freshdesk, which kinds of objects are available, and how to move through Freshdesk’s paged API results, meaning batches of data returned a page at a time. The Intercom connector does the same for conversations, contacts, companies, teams, tags, and tickets, smoothing out Intercom’s varied response formats into steady streams of records. The Zendesk connector covers Zendesk Support and related areas, including tickets, users, Help Center articles, and community posts.

Together, these files hide the differences between support platforms so the larger sync system can treat them as reliable sources of recallable information.

#### [Finance, billing, spend, and document-commerce connectors](stage-14.1.8.md) `stage-14.1.8` — 11 files

This stage is a set of read-only “connectors” used during the system’s data-sync work. A connector is an adapter: it knows how to talk to one outside service, ask for records page by page, and reshape them into the standard streams the rest of the product can store, search, and recall.

The finance side covers Brex and Ramp for spend data like cards, expenses, vendors, receipts, budgets, and transfers; Mercury for bank accounts and transactions; QuickBooks and Xero for accounting records; and Stripe, Square, Chargebee, and Recurly for payments and subscription billing, including customers, invoices, subscriptions, payouts, orders, and related child records. The document-commerce side covers DocuSign and PandaDoc, pulling envelopes, templates, contacts, and documents.

Together, these files act like a row of translators at the edge of the system. Each understands one vendor’s API, including its paging rules and record IDs, but all produce a common flow of syncable records for the shared source-sync machinery.

#### [HR and recruiting connectors](stage-14.1.9.md) `stage-14.1.9` — 6 files

This stage is part of the system’s regular data-collection work. It connects to outside workforce and recruiting tools and brings their records into the sync pipeline. A connector is like an adapter plug: each service has its own API, meaning a web doorway for requesting data, and these files translate those doorways into a common stream of records.

The Ashby, Greenhouse, and Recruitee connectors focus on recruiting. They fetch candidates, jobs, applications, interviews, offers, departments, and similar hiring data. Greenhouse also knows how to fetch nested details, such as interviews belonging to a specific application.

The BambooHR, Deel, and Rippling connectors focus more on employee and workforce operations. BambooHR reads HR datasets from its REST API. Deel brings in contracts, payslips, timesheets, tasks, and forms. Rippling reads company, worker, and team data.

Together, these files handle paging, which means collecting results a batch at a time, so the rest of the system can store and process the data consistently.

### [Index, memory, and profile derivation](stage-14.2.md) `stage-14.2` — 12 files

This stage is shared behind-the-scenes support for memory and search. It takes source text, cuts it into smaller chunks, turns those chunks into embeddings, which are number lists that capture meaning, and stores them so agents can find useful information later.

The core indexing file defines the common rules for chunking, embedding, storing, and searching text. The default index stores and searches chunks in the local database, while the Turbopuffer file can send the same kind of work to an external search service. The OpenAI embedding file supplies the meaning-vectors, carefully splitting requests so they are not too large.

The memory files build on that search base. The manifest connects memory tools, automatic recall, page-change reactions, cleanup, and scheduled writing to the rest of the system. The store records facts and searches pages or remembered items. The condenser turns messy notes and synced pages into clean facts, summaries, profiles, and wiki pages. The events and runtime memory files define the shared event names and result shapes.

The enrichment files add optional, consent-based profile lookup: they track permission, choose live or replayed providers, store results, and surface short summaries later.

## [Scheduled jobs, automations, and asynchronous apps](stage-15.md) `stage-15` — 27 files

This stage is the system’s background shift. It runs work that should happen later, repeat regularly, or continue after a user has stopped waiting. The durable schedules and wake-ups pieces act like an alarm clock: they store future tasks, pauses, monitors, source-change triggers, and notification inbox items, then let only one worker claim each due item. The job runtime decides which workspaces have pending work, keeps each job inside that workspace’s safety boundary, and runs both built-in and extension-declared jobs without duplicates.

Several extensions plug into this loop. Scheduled-task runners fire due tasks and pauses. Monitor tools set up watches on outside systems, and the monitor runner checks them until something changes, fails, or expires. Notification drain and delivery turn stored app updates into controlled chat messages. Report digest code summarizes newly published reports once. Preview rendering retries missing document thumbnails. Homepage cleanup removes an old seeded page only when the chat app has replaced it.

Finally, the offline improvement loop studies past failures, replays saved conversations with proposed instruction changes, and opens only cautious, human-reviewable improvements.

### [Durable schedules and wake-ups](stage-15.1.md) `stage-15.1` — 7 files

This stage is the system’s alarm clock and claim ticket desk. It is shared behind-the-scenes support for work that must happen later, repeat on a schedule, or wake a conversation when something changes. Its main job is to store these future jobs durably in the database and make sure only one worker takes each job when it is due.

The scheduled task pieces define repeating jobs. The cron file checks rules like “every day at 9” and works out the next run time. The schedules file stores those tasks, lets users create or cancel them, and lets a background runner claim due work. The scheduled_fire utility gives both the runner and portal UI the same small label for “this task at this allowed time.”

Pauses are one-shot alarms for conversations that should resume later. Monitors store periodic command checks and their next probe time. Source triggers remember which conversations should wake when shared source data changes. The app notification store acts like an inbox, merging repeated updates and safely leasing pending notifications so workers do not duplicate them.

### [Offline evaluation and improvement loops](stage-15.2.md) `stage-15.2` — 8 files

This stage is behind-the-scenes support for improving the agent after real use. It does not change live conversations directly. Instead, it studies past mistakes, tries safer alternatives offline, and only suggests changes when there is strong evidence.

The evaluation environment package sets up fake email and calendar services, so tests can run the same way every time without touching real accounts. The corpus builder scans old workspace conversations, finds cases where tools failed, groups them by failing tool, and splits them into learning and test examples. The model wrapper gives the rest of the code a simple way to ask the AI for text or for a tool-aware chat turn.

The proposer asks the AI to rewrite an agent’s system prompt, meaning its standing instructions, for a problem area. Replay then reruns saved conversations with the new prompt while reusing old tool results, like watching a recording with a different narrator. Evaluation compares old and new prompts on those replays. The gate applies cautious rules to decide if the new prompt is truly better. Finally, the cron job runs this process on a schedule and opens a human-reviewed proposal only after repeated success.

## [Turn teardown, cleanup, and cancellation finalization](stage-16.md) `stage-16` — 1 files

This stage happens at the end of a unit of work, whether that work finished normally or was stopped early. Its job is to leave the system tidy and honest. It releases things that were borrowed during the turn, such as browser sessions, sandbox leases, terminal state, live streams, and child work that may still be running. It also records what cleanup succeeded, marks workflows as complete or cancelled, and saves enough state so unfinished durable work can be recovered later.

The file cancellation.py provides the shared “stop this turn” procedure. A turn is one run of work through the system. When cancellation is requested, this code first tells the active workflow to stop, so the moving parts have a chance to wind down safely. Only after that does it update the database to say the turn was cancelled. That order matters: it prevents the records from claiming the work is stopped while the workflow is still running in the background.

## [Schema, persistence contracts, and durable storage](stage-17.md) `stage-17` · (cross-cutting) — 3 files

This stage is the system’s filing cabinet and rulebook for saved data. It is shared behind-the-scenes support used by the app screens, background workers, workflows, extensions, and storage engines whenever they need to save or read long-lived information.

The records file defines the common shapes of important items: agents, conversation turns, settings, final answers, questions, credential requests, and queue choices. In plain terms, it says what fields each item must have so every part of the system describes the same thing in the same way.

The transcript file focuses on saved conversations. It gives the system one agreed format for naming, compressing, decoding, and reading conversation history and compacted summaries.

The tables file maps these records into real database tables. It defines columns, links between tables, default values, and safety rules for both SQLite, used locally, and Postgres, used in deployed setups. Together, these files make saved state reliable and understandable across the whole project.

## [Model catalog, provider adapters, and billing accounting](stage-18.md) `stage-18` · (cross-cutting) — 13 files

This stage is shared behind-the-scenes support for any part of UFO that calls an AI model or charges for that work. It acts like a travel desk: it knows which “vehicles” are available, how to book each one, and how much the trip costs.

The model interface defines the common shape of requests and streaming replies, including tool calls, images, and reasoning text, so the main turn loop can talk to every provider the same way. The model spec, catalog, Bedrock extension, and OpenRouter extension list available models, their limits, prices, API style, and client setup. The registry is the lookup desk that turns a chosen model name into the right facts, credentials, and caller.

The Anthropic and OpenAI adapters translate UFO’s standard requests into each provider’s API and translate streamed answers back again, including retry decisions. Grants keep connected provider accounts usable by refreshing expired tokens.

The pricing, accounting, balance, and Metronome files form the money side. They price usage, check prepaid credit and spend limits, record usage, and export billing data to Metronome or Stripe.

## [Public SDK, extension APIs, and generated protocols](stage-19.md) `stage-19` · (cross-cutting) — 65 files

This stage is the public front door for extension authors and nearby integrations. It is shared behind-the-scenes support, not the main user work loop. Its job is to give outside code stable names, safe data shapes, and approved entry points, even while the system’s private internals keep changing.

The SDK authoring helpers define what an extension can declare and what limited toolbox it receives while running. The capability interfaces expose plug-in points for browser control, search, models, memory, terminal access, objects, and similar services. The identity and credential APIs provide safe public access to login tokens, connectors, OAuth, grants, and web sessions. The workspace and surface helpers expose billing, audience, listing, hub, and channel-integration types.

The sample extension acts like a test dummy that exercises all official hooks. The iMessage protocol area supplies generated message formats and network call wiring, so separate pieces can exchange the same structured data. Finally, conversation_slots.py defines what extensions may place into conversation portal areas, with size and safety rules so the display layer gets predictable content.

### [Public SDK authoring and execution helpers](stage-19.1.md) `stage-19.1` — 13 files

This stage is the public workbench for people who write UFO extensions. It sits behind the scenes, between outside extension code and the deeper runtime, so authors can use stable, safe entry points instead of depending on private internals.

The manifest files define what an extension brings to the system: tools, web routes, background jobs, credentials, hooks, agents, search or browser backends, and other plug-in parts. The context files build the limited “toolbox” an extension receives while running, such as scoped access to storage, credentials, conversations, files, model calls, and workspace records. Tool, job, scheduled-run, authority, and flag modules act as public doors to approved definitions used during execution.

The HTTP and callback-page helpers support browser-facing flows, such as routes, session cookies, and the page shown after a user grants consent or finishes installation. The observability helper lets extensions report logs and metrics in approved ways. The untrusted-content helper marks risky outside text so the system treats it carefully. Together, these files make extension authoring safer, clearer, and more stable.

### [Public SDK capability provider interfaces](stage-19.2.md) `stage-19.2` — 12 files

This stage is shared behind-the-scenes support for extensions. It defines the public “plug sockets” that outside code can use without depending on the project’s internal wiring. The core browser file defines the basic promise for getting a Chrome DevTools Protocol connection, which is a control channel for a browser, whether that browser is local, sandboxed, or remote. The runtime search file defines the common shape for web search and page fetching.

Most files in this stage are SDK doorways. They re-export approved internal tools through stable import paths, so extension authors can rely on them even if the internal layout changes. The browser, search, index, memory, models, objects, sandbox, skills, sources, and terminal SDK files each gather the public names for one capability area. Together they let add-ons provide or use browser access, search backends, indexing, memory lookup, model calls, object types, source syncing, sandbox tools, skills, and terminal support. Like labeled ports on a machine, they hide the inner machinery while making extension points clear and safe to use.

### [Public SDK identity, credentials, connectors, and grants](stage-19.3.md) `stage-19.3` — 7 files

This stage is shared behind-the-scenes support for extensions that need to deal with identity, login, credentials, connectors, and access grants. Its main job is to provide stable SDK import paths, so extension authors do not have to reach into private runtime code that may change.

The files work like a set of labeled service windows. authproxy.py exposes the types needed to add credential backends for feed-sync connectors. bearer.py lets extensions check bearer tokens, which are login tokens sent with requests, without revealing the secret used to make them. connectors.py publishes the connector and OAuth shapes used by integration code. credentials.py exposes approved credential tools. grants.py publishes tools for auditing connector and connection grants, meaning records of who allowed what access. operator.py exposes web-session tools for operator users. surface_token.py lets code create and verify long-lived surface link tokens while keeping the system-wide secret hidden. Together, these files form a safe public boundary around sensitive authentication and access-control internals.

### [Public SDK workspace, billing, audience, and surface helpers](stage-19.4.md) `stage-19.4` — 9 files

This stage is shared support for people writing code against the public SDK. It is not the main work loop itself. Instead, it is like a set of labeled front doors into deeper parts of the system, so extension authors do not have to import from changing internal paths.

The accounting and balance modules expose spending summaries, billing balances, and related functions used by workspace screens and command-line spending tools. The seats module does the same for seat types and helpers, which describe who can occupy or use workspace capacity. The audience module collects tools for describing who a conversation is meant for, while subjects provides standard names and helpers for saying who can see a piece of data. Delivery_register publishes fixed text constants used by delivery registration. Hub gathers public hub-related types. Listings exposes listing and paging helpers, which help callers fetch results in chunks. Surfaces is the broad doorway for building channel integrations, collecting the types, constants, errors, and helpers needed to let UFO communicate through outside places.

### [Sample extension hook coverage](stage-19.5.md) `stage-19.5` — 1 files

This stage is a test support stage. It is not part of normal startup, the main work loop, or shutdown for users. Instead, it acts like a full practice extension that the test suite can plug into the system. Its job is to prove that every public extension hook, meaning every official place where outside code is allowed to connect, really works from end to end.

The file `extensions/sample/ufo_ext_sample.py` is the whole sample extension. It provides small fake versions of many things a real extension might add: tools a user can call, web routes, background jobs, storage, search, browser access, model access, connectors, surfaces, and object backends. “Fake” here does not mean useless; it means simplified, predictable parts built for testing. Together they act like a training rig for the extension system. Tests can load this sample, call its pieces, and check that the system treats them the same way it would treat a real third-party extension.

### [iMessage extension provider and generated protocol plumbing](stage-19.6.md) `stage-19.6` — 22 files

This stage is behind-the-scenes plumbing for the iMessage extension. It is not the public face of the SDK and it is not the code that chooses how to handle a conversation. Instead, it supplies the contract, message shapes, and network wiring that let the extension talk to the rest of the system in a predictable way.

The provider.py file is the hand-written center of this stage. It defines what an iMessage-like provider must be able to do, and the simple records used for incoming messages, attachments, and provider events.

Around it is generated Protocol Buffers code, which provides shared “forms” for data. One group supplies Google API support needed by the generated files. Other groups define iMessage data shapes for messages, media, chats, groups, polls, and service requests. The gRPC files add the call wiring, so clients and servers can send those forms across process or network boundaries. Together, these pieces work like a standardized plug and socket set for the iMessage extension.

#### [Google API protobuf support for iMessage extension](stage-19.6.1.md) `stage-19.6.1` — 4 files

This stage is shared behind-the-scenes support for the iMessage extension’s protocol code. The extension uses Protocol Buffers, a format for defining structured messages and services so different parts of a system can agree on what data looks like. Some generated iMessage service files refer to standard Google API definitions, especially rules that describe how a service method could map to an HTTP request. These files provide that missing foundation.

The two __init__.py files are small but important signposts. They tell Python that the google and google.api folders are importable packages, so generated code can reliably find the modules inside them. The annotations_pb2.py file registers the google.api.http annotation, which is extra metadata attached to service methods. The http_pb2.py file supplies the message classes that describe HTTP routes and methods. Together, they act like adapter pieces in a toolkit: they do not drive the extension directly, but they let the generated iMessage protocol tree load cleanly and understand its Google API references.

#### [iMessage protobuf service descriptor modules](stage-19.6.2.md) `stage-19.6.2` — 4 files

This stage is shared behind-the-scenes support for the iMessage extension. It does not send messages by itself. Instead, it defines the “paper forms” that other code must use when talking about iMessage data. These files are generated from Protocol Buffers, a format that describes structured data so different programs can agree on exact request and response shapes.

The attachment module defines the forms for working with file attachments, such as creating or reading attachment requests and replies. The chat module defines the shapes for chat-related actions and the service description for those remote calls. The event module defines how a client asks for updates it missed, such as message, group, poll, or chat changes. The message module defines the main message API shapes, including sending, editing, listing, reacting to, and subscribing to message updates.

Together, these files act like the labeled parts bins in a workshop. Later gRPC code adds the actual client and server wiring, but these modules first define what can be sent and understood.

#### [iMessage core message and media protobuf types](stage-19.6.3.md) `stage-19.6.3` — 6 files

This stage is shared behind-the-scenes support for the iMessage extension. It does not send messages by itself. Instead, it provides the common shapes that other parts of the system use when they talk about iMessage data. These shapes are generated from Protocol Buffers, a format for defining structured data so different pieces of software can read and write it the same way.

The two __init__.py files are simple package markers. They make the photon and iMessage protocol folders importable in Python, like putting labels on drawers so the rest of the code can find what it needs.

The address types module defines how iMessage addresses and supported chat services are represented. The attachment types module defines records for media and extras, such as files, stickers, and Live Photo videos. The message types module defines the main message and event records. The streaming module defines the Heartbeat record used to keep a message stream alive. Together, these files act like standardized forms that the rest of the system fills in, reads, and passes around.

#### [iMessage chat, group, and poll protobuf types](stage-19.6.4.md) `stage-19.6.4` — 3 files

This stage is shared behind-the-scenes support for the iMessage extension. It does not start the app or run the main chat workflow by itself. Instead, it provides the standard data shapes that other parts of the system use when they talk about conversations. These files are generated from Protocol Buffers, a compact format for describing structured data so different programs can read and write it the same way.

The chat types file is the general vocabulary for iMessage chat records and chat-level events. It gives the rest of the code ready-made Python classes for those records. The group types file focuses on changes inside group chats, such as people joining or leaving, or the group name being updated. The poll types file does the same for polls, covering options, votes, and poll change events.

Together, these files act like labeled forms. Other code fills them in, sends them, stores them, or reads them, knowing every part has the same expected meaning.

#### [iMessage generated gRPC service wiring](stage-19.6.5.md) `stage-19.6.5` — 4 files

This stage is shared behind-the-scenes support for the iMessage extension. It is not the code that decides what to do with messages or chats. Instead, it is the network “plumbing” that lets one part of the system call another part over gRPC, a system for making function calls across a network as if they were local calls.

The attachment service wiring provides the ready-made client calls and server connection points for attachment operations. The chat service wiring does the same for chat-related actions. The event service wiring focuses on fetching a stream of older events, so a client can catch up before listening for new live events. The message service wiring connects message-related remote calls to the real code that sends, reads, or manages messages.

These files are generated from service definitions, so developers normally do not edit them by hand. Like labeled sockets in a switchboard, they make sure clients and servers agree on which calls exist and how data moves between them.

## [Shared safety, storage, configuration, and utility infrastructure](stage-20.md) `stage-20` · (cross-cutting) — 82 files

This stage is shared behind-the-scenes support used by many parts of the system. It is not one step in the main work loop. Instead, it provides common guardrails and “plumbing” that other stages rely on.

The storage and database files are the main service pipes. blob.py stores and reads raw bytes, either from local files or cloud storage, while keeping workspace files separate from deploy-wide files. db.py opens safe database connections, runs migrations, wraps work in transactions, and cleans up connection pools. flags.py reads feature flags, which are controlled on/off switches, and falls back safely if the flag service fails.

The harness support adds safety walls around file paths, untrusted text, saved workflow data, logging, health checks, and sandbox network access. The runtime context files track which workspace, authority, and agent are currently allowed to act. The many package marker stages are mostly labels on folders, telling Python where runtime, extension, integration, document, developer, and sample code can be imported from. Together, these pieces keep shared resources organized, separated, and safer to use.

### [Harness safety, serialization, observability, and sandbox network settings](stage-20.1.md) `stage-20.1` — 7 files

This stage is shared behind-the-scenes support for the harness, the part of the system that runs work on behalf of agents and tools. Its job is to keep that work safe, repeatable, and visible.

The containment code is the safety gate for file access. It checks any path that comes from an untrusted source and makes sure it cannot escape the intended folder, even with tricks like symbolic links. The untrusted-content helper solves a similar problem for text: it wraps outside material so the model treats it as evidence to read, not orders to follow.

The durability code protects saved workflow data. It stores and reloads Python objects in a way that can survive crashes and later code changes. The observability code is the monitoring window. It sends logs, metrics, traces, and health checks outward, while reducing the chance that private text leaks.

The sandbox cache and preview settings define safe network destinations for isolated work. Finally, the tools helper groups ordered tasks into chunks that can run in parallel when safe.

### [Runtime workspace, authority, agent scope, and shared path limits](stage-20.2.md) `stage-20.2` — 5 files

This stage is shared behind-the-scenes support. It sets the “current context” for code while the system is running, so later work knows whose workspace it belongs to, which person or workspace has authority, and which agent is allowed to act. Think of it like badges and room labels in a building: before doing sensitive work, code checks the badge and the room.

workspace.py records the current workspace, meaning the customer area the work is happening in. That keeps secrets, model costs, and billing attached to the right place. authority.py describes whether the work is using a particular member’s private credentials or broader workspace authority, and it can convert this into the older stored format. agent_scope.py tracks the active agent for agent-owned abilities and blocks mistakes such as using no agent or an agent from another workspace. object_scope.py adds a temporary override for object actions, so an object handler can act as the normal agent or a specific dispatched agent. file_changes.py provides one shared maximum path length for file-change handling.

### [Core non-runtime package markers](stage-20.3.md) `stage-20.3` — 11 files

This stage is quiet behind-the-scenes support. It does not start the system, run the main work, or shut anything down. Instead, it puts name tags on important folders so Python can import code from them. In Python, an `__init__.py` file marks a folder as a package, meaning other files can refer to it by name.

The top-level `ufo/__init__.py` opens the main `ufo` package. Under it, `harness/__init__.py` marks the test or execution harness area, with smaller package markers for `harness.auth`, `harness.models`, and `harness.sandbox` so authentication, model, and sandbox code can be imported cleanly. `host/__init__.py` is a signpost for the environment layer: the tools, extensions, skills, and prompts an agent can use during a turn. `host.ext` and `host.kinds` are marked as importable sub-areas. Finally, `onboard`, `schema`, and `sdk` are also marked as packages, preparing space for onboarding, data-shape definitions, and developer-facing SDK code.

### [Runtime package markers](stage-20.4.md) `stage-20.4` — 13 files

This stage is behind-the-scenes support for the codebase. It does not start the app, run the main loop, or shut anything down. Instead, it lays out the project’s runtime “neighborhoods” so Python can find them. In Python, an __init__.py file marks a folder as a package, meaning other code can import files from it by name.

The main runtime marker opens the ufo.runtime area. Inside it, separate markers reserve clear spaces for access, billing, extension APIs, kinds, media, prompts, skills, sources, surfaces, tools, and turns. Most of these files contain no working code. Their job is like putting labels on drawers, so later modules know where to store and find related parts.

Two tool markers add a little more explanation. The host tools package points to the host-side tool registry, handler context, and built-in tools. The runtime tools package names the shared tool contract, dispatch context, and registry that connect tool names to their implementations. Together, these marker files keep the runtime layout importable and organized.

### [Application extension package markers](stage-20.5.md) `stage-20.5` — 11 files

This stage is shared behind-the-scenes support for the extension system. It does not run the main app, start services, or shut anything down. Instead, it gives Python clear entry points for optional user-facing areas. In Python, a package is a folder that can be imported by name; these __init__.py files act like labels on drawers, telling the system “you can find extension code here.”

Most files are simple markers. The artifacts, chat, code, issues, meetings, metrics, radar, wiki, coding, and sites packages each make their folder importable so other parts of the project can load their modules when needed. They add no behavior by themselves.

The notification package marker is the only one with extra explanation. It describes an inbox-style app where agents can leave messages for a member, and another agent decides which messages are important enough to interrupt them.

Together, these files form the front doors for extension areas, keeping the project organized and ready to load features cleanly.

### [External integration and web extension package markers](stage-20.6.md) `stage-20.6` — 14 files

This stage is quiet behind-the-scenes support. It does not start services or run the main work loop. Instead, it places small Python “package markers” in extension folders. A package marker is an __init__.py file that tells Python, “this folder can be imported as code.” Think of these files as labels on drawers, so the rest of the system knows where to find tools later.

The browser marker names the browser extension area, including sandbox browser tools, computer-use tools, and a browser-focused helper profile. Its bua marker labels a smaller browser subfolder. The Composio, connectors, Pipedream, Redis hub, Slack, UFO, and web markers each make their extension folder importable for external services, integration hubs, or web features. The iMessage markers label the main iMessage package plus its protocol-related proto and photon/imessage folders. The sources marker and its providers marker label the area where source-provider code can live.

Together, these files form the import map for optional integrations. They prepare the shelves, but they do not operate the tools themselves.

### [Knowledge, document, monitoring, and workflow extension package markers](stage-20.7.md) `stage-20.7` — 13 files

This stage is quiet behind-the-scenes support. It does not start services, run workflows, or process user data. Instead, it gives Python clear signposts for where extension code lives. In Python, a “package” is a folder that other code can import from, like opening a labeled drawer in a toolbox.

Each file here is one of those labels. The brief pipeline, documents, enrichment, gbrain, memory, monitors, objectives, report digest, research, and scheduled tasks folders are all marked as importable extension areas. That lets the rest of the system find optional features for generating briefs, working with documents, enriching data, storing memories, watching activity, tracking goals, building reports, doing research, and running scheduled work.

The document extension also has package markers inside script folders for document review, PowerPoint, and spreadsheet skills. These make those helper scripts reachable in the same standard way. Most files contain no running code; they simply make the project’s extension drawers visible and organized.

### [Developer, debugging, skill-creation, and sample extension markers](stage-20.8.md) `stage-20.8` — 5 files

This stage is mostly behind-the-scenes support for developers and extension authors. It is not part of the main work loop. Instead, it helps optional tools be recognized, imported, and checked during development.

Two files, the debugger and REPL package markers, simply tell Python that their folders are importable packages. A package is a folder Python can load code from. The debugger package is for debugging tools, and the REPL package is for an interactive “type a command, see a result” developer console. These files do not run useful behavior by themselves; they act like labels on drawers so the rest of the system can find the tools inside.

The sample skill probe is a tiny test script. When run, it prints a fixed success message, proving that the sample skill exists and can be executed. The self-improvement package marker introduces an extension for reviewing past work and suggesting prompt improvements for human approval. The skill-create marker labels support for agent-created or temporary skills.
