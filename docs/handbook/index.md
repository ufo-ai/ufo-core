# System Handbook — Stage Index

Each stage below links to its full page; the paragraph is the stage's role in the system.

## [Deployment packaging and schema evolution](stage-1.md) `stage-1` — 210 files

This stage happens before the service starts normal work, during build, deployment, or upgrade. It is like preparing a shop before opening: pack the tools, check the safety room, and rearrange the storage shelves so the new software knows where everything belongs.

The deployment bundle tools create a repeatable package with the right configuration, extensions, runtime code, and sandbox client. The sandbox scripts build the protected place where untrusted code can run, then test that network traffic is forced through the proxy and blocked when it should be. The Alembic wiring connects the app to Alembic, the database upgrade tool, so schema changes run in order.

Most of the stage is migration files. Core migrations build and reshape the main database tables for workspaces, conversations, turns, agents, billing, sources, artifacts, permissions, scheduling, and routing. Timestamped core migrations continue that evolution for newer product features. Extension migrations do the same for optional features such as notifications, memory, indexing, hosted sites, research, coding workflows, objectives, skills, enrichment, and test environments. Together they let old deployments safely become new ones.

### [Deployment bundle, sandbox validation, and Alembic runtime wiring](stage-1.1.md) `stage-1.1` — 4 files

This stage is part of getting UFO ready to run in a real deployment. It happens around build time and deploy time, before the main service work begins. Its job is to package the system, prove the sandbox is safe and consistent, and make sure database upgrades can connect correctly.

The bundle builder creates a self-contained deployment folder, like packing a travel kit with exactly the needed clothes and tools. It freezes the configuration, extension lockfile, runtime package, and sandbox client so another machine can build and run the same version.

The Alembic environment file connects the project’s database schema to Alembic, the tool that applies database structure changes. It tells Alembic where the database is and how to run migrations safely.

The sandbox build script creates the protected environment where UFO runs code. It can build either a cloud template or a local Docker image from the same recipe, keeping both in sync.

The proxy gate script performs a final safety test. It launches a fresh sandbox, installs the trusted certificate, and checks that HTTPS traffic goes through the proxy and is blocked as expected.

### [Core migrations 0001-0022: foundational schema and early platform tables](stage-1.2.md) `stage-1.2` — 19 files

This stage is the database “ground floor.” It runs during setup or upgrade, before normal work can be stored. Each Alembic migration, meaning a small ordered database-change recipe, adds one slice of memory the system relies on. 0001 creates the core records: workspaces, members, agents, conversations, turns, and charges. 0002 adds encrypted credentials. 0003 stores proposals for suggested changes. 0004 links child turns to parent turns for delegated subagent work. 0006 gives extensions their own workspace storage. 0008 records synced sources and pages. 0009 adds Slack conversation and reply tracking, and 0010 allows web-origin records. 0011 adds spending caps and turn states. 0012, 0020, and 0022 expand the ledger so it can track egress, price details, and sandbox tokens. 0013 prevents duplicate turn runs. 0014 records access grants to provider accounts. 0015 tracks live runtime instances. 0017 stores scheduled tasks. 0018 opens conversation “surface” handling and adds shared turn artifacts. 0019 lets extensions define new source backends. 0021 tracks repeated source errors so retries can slow down.

### [Core migrations 0023-0041: turns, inbound messages, sources, seats, and ledger export](stage-1.3.md) `stage-1.3` — 19 files

This stage is behind-the-scenes database preparation. These migrations change the stored shape of the system so later features have the right “shelves” to put data on. They let ledger charges belong to a whole workspace, not only one turn, and add export tracking so outside consumers can receive ledger changes, including exports protected with BYOK, or “bring your own key” encryption.

Several changes improve conversation work. Conversations can remember their sandbox, turns can store tracing links, extra context, scheduled admission, speaker details, and connection authorization status. Scheduled pauses and scheduled-task “last turn” records let the system pause, resume, and remember recent automated activity.

Another group supports incoming messages. New inbound message storage keeps messages ordered, unique, and linked to the right workspace, conversation, member, and turns. Follow-up migrations adjust where rendered message text lives.

Other migrations make the system scale and stay tidy. Indexes speed up job searches, runtime fleet records no longer need one workspace, surface deliveries are tied to workspaces, removed sources get a timestamp, and early seat fields track workspace seat limits and included seats.

### [Core migrations 0042-0060: permissions, pages, agents, and scheduling refinements](stage-1.4.md) `stage-1.4` — 19 files

This stage is part of the system’s behind-the-scenes upgrade path. These migration files change the database structure so newer code can store permissions, pages, agents, scheduling, and ownership more clearly. First, it speeds up parent-turn lookups, then adds clearer sharing and ownership records for grants and sources. It records when a turn is done on someone’s behalf and who created a scheduled task, while removing old shared-fleet fields. Page storage is improved with browsing metadata, clearer timestamp names, explicit revision numbers, and removal of old page-alert markers. Scheduled tasks gain expiration times and names that are unique per agent. Agents gain an internet-access setting, become linked to installations and conversations, and each workspace gets one admin member and one main agent. Older memory tables are removed in favor of one memory surface. Conversations get an audience field for safer visibility. The old grant model is split into reusable account connections plus agent permissions, and sources are tied to both connections and allowed agents. Finally, turn admission rules are expanded so an “intent” can explain why a turn was accepted.

### [Core migrations 0061-0081: artifacts, transcript access, connection sharing, and agent runtime settings](stage-1.5.md) `stage-1.5` — 19 files

This stage is part of upgrading the database while the system evolves. A database migration is a small step that changes stored data or table shapes safely during startup or deployment. These migrations give shared artifacts stronger identities and later add preview data, so files can be referenced and shown more easily. They add agent settings for reasoning effort and sandbox size, and update old Bedrock model choices so existing agents still run. Scheduled tasks gain a paused switch. Conversations gain links to sandbox runs, a starting surface label, stored Git workspace changes, and faster lookup for spoken member turns. The ledger, which tracks usage, learns to split prompt and cache-read tokens and to count image and video usage. Privacy and administration improve through transcript access auditing and removal of an unused index. Old YC extension records are cleaned up. Delegated child tasks can now show whether they still owe results to a parent. Sign-in becomes faster with an email index. Finally, connection sharing moves onto the connection itself, with an optional account label, making shared access easier to manage.

### [Core migrations 0082-0100: membership, billing, app agents, and conversation productization](stage-1.6.md) `stage-1.6` — 19 files

This stage is part of the system’s database upgrade path. It changes the stored shape of important data so newer product features can work safely. Several migrations make lookups faster or clearer: ledger records can be found by workspace and creation time, spoken turns by speaker, and egress proxy rules get a version counter so cached rules can be refreshed promptly. Membership changes from counting limited seats to treating members as unlimited, while old pause data is moved out of the core task table. Conversations become more product-ready: titles are stored, old titles are backfilled, title-summary status is tracked in core, subagent display names are preserved, and mid-turn replies get durable records to avoid duplicate sends. Billing grows into prepaid balances, balance-change history, actual debit tracking, and automatic top-up settings. Agent records also mature: they remember their tool policy, source extension, setup data, input and output shape, and owner. Other migrations add member time zones, BYOK turn markers, better artifact file types, and remove the old Exa extension credential.

### [Core migrations 0101-0113 and legacy branch migrations](stage-1.7.md) `stage-1.7` — 16 files

This stage is part of upgrading the system’s database. A database migration is a small step that reshapes stored data so newer code can use it safely. Together, these migrations tidy old records and add fields needed by newer product features.

Several steps improve agent records: they turn on safe reasoning for older Fable agents, update outdated Fable model names, add agent icons, change the default icon, and add visibility so main agents can be shown to the whole workspace. Other steps improve billing and usage tracking by splitting ledger token counts into clearer categories and recording when a workspace first made a verified card top-up.

This stage also supports live services and communication surfaces. It adds a table showing which service instance has claimed a surface listener, removes stale seat-shipping data, and cleans up old iMessage binding and claim-code records. It adds a place for turns to record references they created. Finally, it starts two newer data areas: a knowledge graph for storing things and their links, and Daily Brief “sweep” tables for tracking brief editions and application state.

### [Timestamped core migrations: surface routing, app archival, sources, and object journals](stage-1.8.md) `stage-1.8` — 12 files

This stage is a set of database upgrade steps. They run behind the scenes when a deployment moves the application to a newer version, reshaping stored data so newer code can work safely. Several steps clean up or retire old paths: they delete obsolete iMessage claim records, retire broken QuickBooks sources while keeping their history, close an old Daily Brief migration branch, and remove unused Daily Brief tables. Others improve how active work is found and routed: turn indexes make agent work lookups fast, member invitation fields record who invited whom, and iMessage routing now uses the sender’s address or phone number instead of the receiving installation. Agent-related migrations add archiving, protect the main agent from being archived, fill built-in app icons, and free names from archived apps by moving their old names aside. Another migration creates an object change journal, like a ledger that records what changed, who changed it, and when. The final source-status change lets the system remember when a source refuses work and is temporarily parked.

### [Timestamped core migrations: app provisioning, billing identity, allowlists, and artifact content](stage-1.9.md) `stage-1.9` — 13 files

This stage is part of upgrading the system’s database. A database migration is a small step that changes stored data or table shapes so the newer code can run safely. These migrations tune how built-in agents and apps appear, how tools are named, and how later features can remember key identities.

Several steps update agents: agents gain settings for using shared workspace skills and a short purpose; the code review agent is moved to its newer app identity; old Tasks agents are archived; the main workspace agent becomes the official chat agent; and wiki agents that still use the shipped default are made private. Billing is made more stable by storing billing identity directly on each turn, which is one exchange in a conversation.

Other steps clean up tool allowlists, meaning the saved lists of tools an agent may use. They rename older object, Slack, and iMessage tool entries into the names this software expects. The remaining migrations add identifiers for source pages, record fulfilled credential requests only once, and add request, digest, and text flags to shared artifacts so their content can be tracked reliably.

### [Timestamped core migrations: retries, connections, media fixes, and conversation labels](stage-1.10.md) `stage-1.10` — 16 files

This stage is behind-the-scenes upgrade work. Each file is a database migration, meaning a small script that changes stored data or table shapes so newer code can run safely on older installations. Together they keep conversations, connections, artifacts, and agents consistent as the product changes.

Several migrations expand what a conversation turn can remember: when a connection request landed, runtime settings, original spawned intent, who had authority to speak, when a parked provider call should retry, and how many external retries have happened. One adds a rule so a turn cannot claim two conflicting speakers. Connection-related migrations remove obsolete GitHub App slots, add commit name and email fields, and update old application-builder tool permissions so old and new code can overlap during deployment.

Artifact migrations clean up shared files. They mark member-attached files, add artifact roles, fix text, WebP, and MKV media types so previews work, and update the Artifacts app icon. The remaining migrations move retired GLM 5.2 agents to GLM 5.3 and rename old “Direct message” conversation labels to “DM.”

### [Extension migrations: notifications, monitors, report digests, research, scheduled pauses, and web chat](stage-1.11.md) `stage-1.11` — 11 files

This stage is part of setup and upgrade work, not the daily chat loop. It contains database migrations, which are small step-by-step changes that prepare stored data for newer versions of the extensions. Think of them as renovating labeled drawers before the app starts using them.

The notification migrations first create a drawer for app notifications, then add fields so notifications can be claimed, sorted, searched, and marked as delivered. They also refresh built-in notification agents with newer prompts, tools, and version numbers, while trying not to overwrite member-edited wording. The monitor migration adds storage for scheduled checks linked to a workspace, conversation, and agent. The report digest migrations store readable summaries of published reports, including a record for reports that were checked but did not change. The research migration records web sources observed during a conversation, in order. The scheduled-task migration stores pauses so a conversation can resume later. The web migrations move old chat rows and saved titles into their newer homes, reducing duplicate or outdated storage.

### [Extension migrations: hosted sites and source triggers](stage-1.12.md) `stage-1.12` — 11 files

This stage is behind-the-scenes setup for two extensions: hosted sites and source triggers. It runs during upgrades, before normal work continues. Each file is a database migration, meaning a small step that changes saved data so newer code has the storage it needs.

The hosted-site migrations build up the record for a site published from a workspace conversation. The first creates the table. Later steps add a generation ID, remember which agent is the homepage, safely make certain main-agent homepages visible to the whole workspace, and store preview-image details. Further steps add share-card metadata, a deploy generation number, and an optional source manifest, which is a text description of where the site came from.

The source migrations do similar preparation for activation from external or stored sources. They create structured source-trigger records, move old subscription data into that table, add a delivery setting that says how triggers are sent, and add resource watches for tracking one specific item inside a source. Together, these migrations turn loose older data into clear, versioned storage.

### [Extension migrations: memory and default indexing](stage-1.13.md) `stage-1.13` — 18 files

This stage is behind-the-scenes setup for extensions that store searchable text and long-term memory. It runs as database migrations, meaning small upgrade steps that reshape the database without changing the main work loop.

The default indexing migrations first create a chunk table for pieces of text, word search, and vector embeddings, which are number lists used for meaning-based search. They then add workspace separation so different workspaces can store identical chunks safely. The memory migrations build up the memory system step by step. They create tables for memory records and memory pages, add memory kind and confidence, and make pages belong clearly to a workspace. Later steps add speed indexes for consolidation sweeps and inventory browsing, add an “as of” time for when information was true, and backfill that time from pages. Other migrations improve source tracking: they record which page and exact page revision produced a memory, support wider audiences, allow one memory to connect to multiple source pages, and let curators retire unwanted memories. The final steps allow new memory classes, section and overview, and add shared memory profiles for workspace members.

### [Extension migrations: enrichment, evaluation environment, and sample storage](stage-1.14.md) `stage-1.14` — 4 files

This stage is behind-the-scenes setup for optional extensions. It is made of database migrations, which are small upgrade scripts that create or remove storage tables when the system is installed, updated, or rolled back. They do not do the everyday work themselves; they prepare the shelves where extension data can live.

The enrichment profile migration creates a table for saved enrichment results about workspace members. The next enrichment migration adds two more tables: one records whether a member has agreed to enrichment, and the other records when a workspace should pause before trying enrichment again. Together, these let enrichment remember both its output and its permission state.

The evaluation environment migration creates fake inbox and calendar tables for testing or demos. Each workspace gets its own stored messages and events, and those records are removed if the workspace is deleted.

The sample extension migration creates a simple note table, storing one text note per workspace, mainly as a small example of extension-owned storage.

### [Extension migrations: coding workflows, objectives, and user-created skills](stage-1.15.md) `stage-1.15` — 10 files

This stage is behind-the-scenes upgrade work for optional extensions. These files are database migrations: small scripts that change the shape of stored data when the system is installed or updated, like adding new drawers to a filing cabinet and moving old papers into them.

The coding migrations build and revise the storage for code review workflows. They first add review inbox and review-run tables, then connect review runs to the conversations that helped produce them. Later changes shift review items away from conversation ownership and toward direct agent bindings, then move old inbox data into the newer source-trigger and conversation model before removing obsolete tables.

The objectives migrations create storage for goals, ordered steps, evidence of progress, and later checks. They also add a saved flag showing whether a step can run on its own.

The skill-creation migrations store user-made skills, first by workspace, then by agent, then with routing-card details such as descriptions and dependencies. The final migration consolidates skills back to workspace ownership and removes duplicates.

## [Process entry and service bootstrap](stage-2.md) `stage-2` — 2 files

This stage is the front door of the UFO process. It belongs to startup and setup, before the system can answer web requests or run background jobs. Its job is to gather the basic facts the rest of the service needs: deployment settings, database connections, rules about which model keys can be shown in sandboxed environments, product and build information, and the shared service objects later code will use.

There are two main entrances. The CLI stage, centered on `core/src/ufo/cli.py`, provides `ufoctl`, a terminal tool for operators. It lets a person initialize a workspace, inspect the installed product, run selected tasks, package things, or perform administration work. It is like a control panel used around the service, not the service’s main request loop.

`core/src/ufo/serve.py` is the service launcher. It assembles the running web service: reads configuration, connects storage and owner databases, installs routes and user-facing surfaces, starts background workers, and then hands control to the web server.

### [CLI, initialization, and product inspection commands](stage-2.1.md) `stage-2.1` — 1 files

This stage is the operator’s control panel for UFO. It runs outside the main HTTP service loop, so it is used before, after, or alongside the server rather than while handling web requests. Its job is to let a person set up a local workspace, start or run parts of the system, inspect what is installed, package things, and perform administration tasks.

The main piece here is `core/src/ufo/cli.py`. It defines `ufoctl`, the command-line tool, which is the front door for people operating the project from a terminal. A command-line tool is a program you run by typing commands, like asking a machine to perform a specific task. `ufoctl` turns those typed commands into concrete actions inside the UFO workspace. In practice, it acts like a dispatcher: it reads what the operator asked for, prepares the needed settings and context, then calls the right internal routines to initialize, inspect, run, or manage the deployment.

## [Configuration, packs, flags, and model catalog loading](stage-3.md) `stage-3` — 16 files

This stage happens during startup, before the system begins serving users. Its job is to decide what “bundle” of abilities to run, connect feature switches, and build the menu of AI models the rest of UFO may call. The main configuration file, checked by core/src/ufo/config.py, supplies safe deployment settings. The pack files are like preset toolboxes: assistant_dev, assistant_hosted, assistant_billing, assistant_eval, DSQA, GDPVal, and sample packs each name the extensions, skills, and test or hosted features that should be loaded together.

Once a pack is chosen, the model registry is filled. catalog.py lists built-in models, spec.py defines what every model record must say, and registry.py checks and stores the final list. Provider plugins such as Bedrock add extra model choices and connection rules.

Feature flags are runtime on/off switches. core flags.py gives the app one simple way to ask about them. flags_open answers “on” for local testing, while flagship connects to Cloudflare’s real flag service. proxy_serve.py supplies shared startup rules for provider access and owner database connections.

## [Extension discovery and capability registration](stage-4.md) `stage-4` — 60 files

This stage is the system’s plug-in discovery desk. During startup, and later as shared support, the host looks for installed extensions, checks their manifest files, and registers what each one offers. A manifest is a clear “menu card” that says which tools, apps, background jobs, screens, sign-in methods, agents, data types, and outside services an extension adds.

The core loading part finds extensions, enforces the allowed list, and turns their menus into usable system pieces. Built-in app registrations add the chat, artifacts, radar, wiki, notification, code, issues, meetings, and metrics apps so the workspace can show and run them. Connector and authentication registrations prepare safe sign-in and action routes for services such as Slack, GitHub, Composio, Pipedream, and API-key based tools. Provider and communication extensions add browser, enrichment, iMessage, Slack, and knowledge-source abilities. Skill packs register writing, coding, document, research, and skill-authoring helpers. Automation registrations add monitors, objectives, reports, scheduled tasks, and self-improvement jobs. Runtime registrations add memory, sites, debugger pages, Redis backends, and the main UFO and web surfaces.

### [Core extension loading and manifest contract](stage-4.1.md) `stage-4.1` — 4 files

This stage is part of startup and shared support for UFO’s extension system. Extensions are add-ons, like plug-in attachments for a tool. Before the rest of the system can use them, UFO must know what is installed, what is allowed, and what each extension offers.

The store file supports the command line side. It can search an extension catalog, pin a chosen extension into a lockfile, and remove that pin later. A pin is a saved decision that says, “this deployment should load this extension.”

The loader file is the main front door at runtime. It discovers installed extensions, checks the active set against the allowed pins, then converts each extension’s declarations into usable system pieces such as tools, hooks, object types, skills, credentials, and backends.

The manifest file defines the contract for those declarations. It is the menu format an extension must use, with checks to catch unsafe or unclear entries. The package file simply makes this folder importable by Python.

### [Built-in conversational and content app registrations](stage-4.2.md) `stage-4.2` — 10 files

This stage is behind-the-scenes setup for the built-in apps that members see in the workspace. It is like filling out name tags before an event starts, so the platform knows which apps exist and how to show them. Each app folder has an __init__.py file, which simply marks the folder as importable Python code. These files mostly do not run the app; they make sure the rest of the system can find it.

The real registration work happens in the manifest.py files. The Chat manifest introduces the main conversation app, its agent, its purpose, and its home-screen skill. The Artifacts manifest registers the app used for created content and points to the agent and skill behind its homepage. The Radar manifest defines the feed-style Radar app and its agent. The Wiki manifest registers wiki pages and the skill files that support them. The Notification manifest adds more pieces: a notification tool, notification object type, special agent, scheduled inbox-draining job, and delivery action. Together, these manifests let the host system install and present these apps consistently.

### [Built-in work app registrations](stage-4.3.md) `stage-4.3` — 8 files

This stage is the sign-up desk for several built-in work apps. It is shared behind-the-scenes support, used when the platform discovers what apps exist, installs them in a workspace, and knows how to start their agents. An agent is the automated worker the app creates to do its job.

Each app folder has an __init__.py file. These files are simple package markers: they tell Python, the programming language used here, that the folder can be imported by other code. They do not run the apps themselves.

The real instructions are in the manifest.py files. The Code manifest registers the Code app, including its GitHub setup, prompt, agent, and shipped skill files. The Issues manifest tells the platform how the Issues app is installed, what its agent may do, and what skills it has. The Meetings manifest declares needed accounts and scheduled meeting work. The Metrics manifest defines report scheduling, possible account or key needs, and its home-page skill. Together, these manifests act like labels and instruction cards for the platform’s built-in apps.

### [Connector framework and authentication broker registrations](stage-4.4.md) `stage-4.4` — 7 files

This stage is behind-the-scenes setup work. It tells the UFO system which outside-service connectors exist, how users can sign in to them, and where the system should send connector-related requests. Think of it like installing labeled sockets before any appliances are plugged in.

The Composio manifest registers the Composio extension, including its connector lookup rules and the web address used when a user returns from an OAuth consent screen. OAuth is the common “sign in with another service” flow. The main connectors manifest adds shared connector tools, connector objects, and extra prompt instructions used when connector features are active. The keyed connectors file covers simpler services that use API keys. It creates private credential slots so owners can store secrets without exposing them to the sandbox. The Pipedream manifest registers Pipedream-backed connectors, their sign-in route, and the broker that runs their actions. The sources package marker only makes its folder importable. The sources manifest registers sync-related connectors, credentials, hooks, retries, and default authentication. Finally, the sources registry is the address book mapping names like Slack or GitHub to the code that talks to them.

### [External provider and communication extension registrations](stage-4.5.md) `stage-4.5` — 8 files

This stage is part of startup and shared behind-the-scenes support. It is where optional outside connections introduce themselves to the main UFO system, much like plug-in cards being read before a machine starts work. The manifest files are those cards. The browser manifest registers browser tools, a helper agent, prompt text, and any outside capability needed for web access. The enrichment package marker simply makes that folder importable, while its manifest describes company-website confirmation, optional work-profile lookup, read-only profile display, and short conversation summaries. It also checks whether a provider API key exists before enabling lookup features. The gbrain package marker does the same import job, and its manifest registers knowledge sources from GitHub or a local folder, including an optional GitHub token. The iMessage manifest registers the messaging surface, the connect action, and required cloud secrets. The Slack package marker enables importing, while the Slack manifest advertises its routes, credentials, tools, hooks, and workspace status facts. Together, these files let the core app discover what each extension can do.

### [Agent skill, authoring, document, coding, and research registrations](stage-4.6.md) `stage-4.6` — 7 files

This stage is part of the system’s behind-the-scenes setup. It does not do the writing, coding, or research itself. Instead, it registers the add-on packs that make those abilities available, like putting labeled tools into a workshop before anyone starts working.

Each manifest file is a registration sheet. The brief-pipeline manifest tells the host about three helper agents and the instructions for using them together to build briefs. The coding manifest declares repository-editing helpers, the tools they may use, their prompts, models, and skill folder, so code changes happen in a controlled way. The documents manifest makes document, PDF, theme, review, and drafting skills visible, along with a writing helper agent. The research manifest registers research tools, specialist agents, prompts, skills, and the required search backend. The skill-create manifest adds the ability for workspace members to save, reload, search, and index their own skills.

The two __init__.py files are package markers. They let Python recognize those extension folders as importable packages and provide a short description of what they contain.

### [Task automation, monitoring, objectives, and improvement registrations](stage-4.7.md) `stage-4.7` — 6 files

This stage is the system’s sign-up desk for long-running work. It mostly runs during startup, when the host learns which extensions exist, what tools they add, and which background jobs should run later. These files do not do all the work themselves. They register the parts so the rest of the system can find and use them.

The monitors manifest adds monitor objects, a monitor action, and a clock-based job that checks when monitors are due. The objectives manifest adds planning tools and prompt guidance, and it reminds the agent about unfinished work at the start of each turn. The report digest manifest registers a writing skill, a scheduled digest job, and an admin rebuild tool. The scheduled tasks manifest adds task objects, a pause-and-wait tool, recurring jobs, and a skill for scheduling future work.

The self-improvement package marker labels that extension as installable Python code and explains its purpose: reviewing past activity offline and proposing human-approved prompt changes. Its manifest registers the regular evaluation job that performs that review.

### [Runtime surfaces, memory, sites, and backend registrations](stage-4.8.md) `stage-4.8` — 10 files

This stage is shared startup support. It is where optional parts of the system introduce themselves to the main application before users start working. Most files are “manifests,” which are like registration cards: they name an extension, state its version, and list the tools, screens, routes, jobs, or backends it wants the host to connect.

The debugger manifest adds a protected debugging page and a reporting tool. The memory package marker is just a doorway for imports, while its manifest registers memory tools, automatic recall, page listeners, cleanup and writing jobs, search support, and a memory screen. The Redis hub manifest tells the system how to create Redis-based hub and terminal backends. The sites package marker enables imports, and its manifest wires in website-building tools, prompts, agents, storage, a web surface, and background work. The UFO and web package markers are import doorways; their manifests mount the shell client and web portal with their routes, permissions, jobs, flags, and browser-facing surfaces.

## [Workspace onboarding and agent provisioning](stage-5.md) `stage-5` — 6 files

This stage is the project’s front door. It runs during first setup, and also supports later workspace setup screens. Its job is to create a usable workspace, add the first people, choose how they can sign in, and make sure helpful agents are available.

The main coordinator is onboarding.py. It creates the first workspace, the first admin member, and the main assistant agent. It also checks required keys and lets extensions do setup work safely. onboard_control.py is the trusted private API used by the Rust control plane. It creates workspaces, seats members, lists sign-in choices, counts workspaces, and reads invitation pages, so these rules are not scattered around the login layer. provisioning.py installs agents that come from extensions, but only once, and it avoids overwriting later user edits. agent_setup.py describes what those agents still need, such as connected accounts or credentials, and reports setup status to the portal. seed.py adds a rich demo conversation for testing and cleans up old copies. __init__.py simply makes the onboarding folder importable.

## [Authentication, sessions, member identity, and credential grants](stage-6.md) `stage-6` — 20 files

This stage is the system’s identity checkpoint. It runs when people sign in, when browsers return from “connect account” pages, when agents need stored credentials, and when protected sandbox links are opened. The shared signing code makes tamper-proof tokens; bearer tokens prove a user’s workspace and email, surface tokens carry trusted route context, and ingress tokens guard access to sandbox app ports. Session and identity helpers define whether work is acting as a workspace or a specific member. Seat and audience rules decide which members may talk to which agents, while operator login code protects admin-only tools.

The credential side stores secrets safely and grants agents limited use of outside accounts. Core grant code tracks connected OAuth-style accounts and refreshable OpenAI or Anthropic logins. Web flows connect OpenAI by device code and Anthropic by OAuth or pasted key. The CLI surface route receives OAuth return trips. Composio and Pipedream providers bridge UFO to hosted consent screens, with Pipedream token code turning connections into usable sandbox credentials. iMessage tools verify phone ownership by opt-in text. Package marker files simply make these authentication folders importable.

## [Request routing, public surfaces, and workspace object APIs](stage-7.md) `stage-7` — 40 files

This stage is part of the main work loop after a user is signed in. It decides where each web request, chat message, object view, download, or operator page should go. Think of it as a switchboard for all the places people can interact with UFO.

External chat and terminal ingress brings messages in from Slack, iMessage, web chat, and terminal clients, converts them into UFO’s common conversation format, and sends replies back in each service’s own style. Portal and object-oriented workspace APIs let the portal, tools, and agents safely read or change workspace items such as agents, tasks, sites, memories, members, and connectors.

The web surface serves the browser portal, live chat, streaming replies, and member actions. Starters prepares personalized suggestions for the start screen. Runtime steps turns workflow records into a readable timeline for debugging. The debugger and memory surfaces give trusted operators read-only inspection pages. Scheduled task slots show allowed automations inside a conversation. The sites surface serves hosted site pages, previews, and visibility changes. The package marker simply groups surface code in one place.

### [External chat and terminal ingress](stage-7.1.md) `stage-7.1` — 9 files

This stage is the system’s front door for messages arriving from outside places, during the main work loop. Slack, iMessage, the UFO terminal client, web chat, and similar “surfaces” all speak different languages. These files translate them into the shared UFO conversation format, then translate replies back out again.

The core bridge in runtime/ext/surface.py is the gatekeeper. It lets trusted surfaces identify members, add new message turns, read conversation state, deliver replies, and show portal views. The Slack surface checks that requests really came from Slack, imports messages, files, forms, progress updates, and install flows, then sends UFO’s responses back. Slack mention utilities turn Slack’s hidden user codes into readable names and back again, while attribution utilities manage small bot footers without confusing them for real mentions.

The iMessage surface performs the same two-way conversion for texts, attachments, replies, files, and account prompts. Its cloud helper talks to Spectrum Cloud, the outside iMessage service. The terminal surface turns web requests into simple commands a shell script can follow, and the Redis terminal stream keeps terminal sessions working across separate server pods.

### [Portal and object-oriented workspace reads and writes](stage-7.2.md) `stage-7.2` — 23 files

This stage is shared support for the portal, tools, and agents when they read or change workspace “objects,” meaning named things like agents, sites, tasks, members, or connected accounts. The central object system defines the common rules for listing, opening, creating, updating, deleting, explaining, and running actions on these things. Agent objects cover prompts, models, permissions, archiving, and restoration, while governance adds an approval step before prompt changes take effect. Object scope keeps actions tied to the right agent.

The built-in kinds expose safe views of workspaces, members, conversations, installed extensions, credential slots, and chat surfaces. Most of these are read-only or hide sensitive values. Extension object types add notifications, connectors, synced source pages, gbrain Markdown sources, memories and profiles, monitors, reports, scheduled tasks, hosted sites, and task visibility checks. Each one decides what users may inspect, share, stop, forget, dismiss, or delete.

The web panel code connects portal buttons and forms to the normal conversation-based write path. The package marker files simply make these modules importable.

## [Turn admission, durable queuing, and live update streams](stage-8.md) `stage-8` — 6 files

This stage is the traffic control room for conversations. A “turn” means one unit of agent work, such as answering a user message or reacting to an internal event. When something new arrives, admission.py is the trusted front door. It checks that the account can use the system, the delivery is not a duplicate, the right agent is attached, and only one turn for the conversation runs at a time. ambient_reply.py adds a social filter for group chats: if someone replies in a thread without clearly inviting the agent, it can skip starting a costly unwanted turn.

Once a turn is waiting, dispatch.py decides when it is safe to start the next one and sends it to the background worker queue. While the turn runs, hub.py broadcasts live progress, like a radio channel for text, status, costs, and results. hub_tail.py helps clients follow that channel reliably, checking storage too so late or reconnecting viewers still see the ending. stream_hub.py extends the same live updates across many server processes using Redis Streams.

## [Per-turn host environment assembly](stage-9.md) `stage-9` — 7 files

This stage happens at the start of every model turn, after the system has decided which agent is allowed to act. Its job is to prepare the “room” the model will work in: the instructions it reads, the tools it may use, the skills it can call, and any starter files in its private sandbox. The host package labels this as the environment layer.

The main builder, assemble.py, gathers all these pieces and enforces limits. It may reduce what the wider platform allows, but it cannot add hidden extra powers. environment.py defines saved environment documents, which are controlled change sets for prompts, tools, model choice, skills, and files; it checks and records them so runs can be repeated. spawn_catalog.py lists which helper agents may be started with spawn and explains their required inputs.

object_views.py converts internal action definitions into safe, simple descriptions for models or portal pages. The prompts package provides prompt support, and render.py turns templates into the final system prompt, checking for missing placeholders and recording a fingerprint of the exact text.

## [Turn engine execution and model interaction loop](stage-10.md) `stage-10` — 22 files

This stage is the main work loop for an agent turn. A “turn” is one unit of work, such as answering a user message or running a subagent task. runtime/queue.py takes a waiting turn from the durable queue, builds the needed model, tools, permissions, sandbox, and billing checks, then hands it to runtime/engine.py. The engine is the careful supervisor: it rebuilds the conversation, calls the agent runner, saves progress, records costs, and publishes the final or paused result without repeating work after a crash.

harness/agent.py runs the back-and-forth between the AI model and tools. harness/rounds.py manages one live model response, streaming text, collecting tool requests, timing, reasoning notes, and usage data. harness/replies.py removes hidden routing tags from model text while preserving what they mean. runtime/ext/hooks.py lets extensions inspect, enrich, or block actions at key points.

The conversation and compaction parts keep history safe and short enough for the model. The provider adapters make different AI services look the same. The __init__.py files simply make these folders importable.

### [Conversation state, contracts, and compaction](stage-10.1.md) `stage-10.1` — 9 files

This stage is shared behind-the-scenes support for each conversation turn. Before the model is called, it checks what conversation history can be shown. During and after the turn, it records what happened in a safe, consistent form.

The compaction pieces keep long chats from overflowing the model’s “context window,” meaning the limited amount of text the model can read at once. harness/context.py decides when shrinking is needed. runtime/compaction.py keeps recent messages as they are, replaces older ones with a verified summary, and stores both versions for review. runtime/transcript.py safely writes the transcript to shared storage without letting stale copies overwrite newer ones. turns/transcript.py defines the common saved record shapes so every reader and writer agrees.

The turns files add rules around those records. contracts.py checks data passed between agents, whether it comes from built-in models or user JSON rules. delivery_register.py defines how agents should write replies and how long they may be. activity.py turns raw tool calls into friendly status labels. audience.py and subjects.py define who a turn is for, so private, shared, and room-visible content stay separate.

### [Model request streaming and provider normalization](stage-10.2.md) `stage-10.2` — 5 files

This stage is shared behind-the-scenes support for talking to AI models. Its job is to hide the differences between providers, so the rest of UFO can ask for a model response in one standard way and receive one standard stream of events back. The empty __init__.py simply makes the models folder importable. interface.py defines the common “contract”: what a request looks like, how streamed text, tool calls, images, reasoning notes, and responses are represented, and how image-heavy conversations are trimmed to stay within provider limits. anthropic.py translates that common format to Anthropic Claude’s API and translates Claude’s live reply back into UFO events, including deciding which errors are worth retrying. openai.py does the same for OpenAI-style APIs, including both older chat streaming and newer response streaming paths. The OpenRouter extension plugs in another provider route, using an OpenAI-like interface while adding cost tracking, credential handling, retries, and image or video generation tools. Together, these parts act like adapters for different power outlets: each provider is different, but the rest of the system can plug in the same way.

## [Tool dispatch, sandbox workspaces, and command execution](stage-11.md) `stage-11` — 35 files

This stage is used during the main work of a conversation, whenever the model asks to do something outside plain text. It is like the control desk for tools. The registry lists which tools exist, how they are shown to the model, and how a requested name is safely matched to real code. The tool context then gives that code only the powers it should have, such as sandbox access, artifact sharing, billing records, cleanup hooks, or connector account choices.

Built-in and extension tools are the actual instruments: shell commands, file edits, REPL snippets, Slack search, notifications, MCP server calls, todos, monitoring, and more. The sandbox lifecycle layer provides the safe workspace where risky work happens, whether local, terminal-based, Docker, or cloud-hosted, and controls network access and secrets.

The bridge files let code inside a sandbox ask the main runtime to list, inspect, or run approved tools through the normal permission-checked turn system. The package files are simple signposts that make these tool folders importable and explain what belongs there.

### [Sandbox lifecycle and controlled network egress](stage-11.1.md) `stage-11.1` — 16 files

This stage is shared support for the system’s main work: giving each conversation a safe workspace and controlling how it reaches the internet. The conversation layer creates or reattaches the right workspace, records its location, and offers safe file read, write, list, and cleanup operations. The selector chooses which sandbox backends to keep ready, while the package marker simply makes the sandbox code importable.

Several backends can do the actual work. Local runs commands in a host folder for development. Terminal uses a user’s connected machine as the runner. Docker creates or reuses per-conversation containers. E2B does the same on a cloud sandbox service. Session and protocol provide the common “language” for commands and files, so the rest of UFO does not care which backend is underneath.

Exec environment and client-binary helpers prepare commands with approved settings and the right UFO program. Cache settings route downloads through shared caches. Background tasks keep long jobs running and expose logs. Finally, egress control, resolver, and rules decide which outside sites are allowed, what usage is metered, and where approved secrets can be safely injected.

### [Built-in tools and extension tools](stage-11.2.md) `stage-11.2` — 13 files

This stage is the agent’s toolbox. It is shared support used during the main work of a conversation, whenever the agent needs to act rather than just reply. The core built-ins are the basic tools: run shell commands, read or edit files, share artifacts, start subagents, ask the user questions, and request private credentials, all inside a controlled sandbox.

The extension files add special-purpose tools around that core. The notification tools let an agent place a message in a member’s inbox, then let the Notification app deliver it safely and only in the right context. The debugger report tool records deployment problems for engineers, with a link to the relevant transcript turn when possible. The MCP tool connects to outside tool servers, discovers what they offer, and calls them with limits and clear errors. The monitor tool watches for outside changes by rerunning a command. The REPL extension runs small Python or JavaScript snippets and keeps successful state. Slack tools help connect and search Slack. The todo tool stores checklists for ongoing work. Several __init__ files simply mark extension folders as importable packages.

## [Browser and computer-use automation](stage-12.md) `stage-12` — 28 files

This stage is the system’s browser-and-computer-use workshop. It is shared support that the main agent calls when it needs to use the web: start or attach to Chrome, visit pages, understand what is on screen, click, type, move files, and clean up afterward.

The browser extension surface is the front door. It exposes simple tools and a browser-focused helper agent for web tasks. The provider and launch adapters decide where the browser actually runs, such as local Chrome, sandbox Chrome, or a hosted cloud browser, while hiding those differences from the rest of the system.

Inside Chrome, the BUA control-room code talks to Chrome’s remote-control interface, checks messages, runs page JavaScript, handles pop-ups, and preserves useful error details. The session and tab lifecycle code keeps one orderly browser workbench per turn, tracks tabs, waits for pages to settle, and closes things safely. The page understanding code turns messy web pages into readable text, element lists, and click targets. The action layer then performs clicks, typing, scrolling, uploads, downloads, and form filling. The package marker file simply makes the BUA code importable.

### [Browser extension tool and subagent surface](stage-12.1.md) `stage-12.1` — 4 files

This stage is the agent’s doorway into browser work. It is not the main reasoning loop itself. It is a support layer the main agent can call when it needs to open web pages, interact with sites, or ask a browser-focused helper to do a web task.

The package file marks this folder as a browser extension module, like a sign on a toolbox that says what belongs inside. The tools file fills that toolbox. It offers concrete actions such as opening tabs, reading page content, clicking, typing, uploading files, and saving downloads. These tool calls are translated into commands for the real browser-control system underneath.

The subagent file defines a specialized child agent for browser tasks. It says what kind of assignment the child receives and what kind of answer it returns. The delegation file gives the main agent two ways to use that child: send one complete browser task, or split many small browser visits to run in parallel and collect saved results.

### [Browser providers and hosted/local launch adapters](stage-12.2.md) `stage-12.2` — 4 files

This stage is the browser “plug socket” for the system. When a turn needs to use the web, the core code should not care whether Chrome is running locally, in a sandbox, or on a hosted service. The shared browser boundary in core/src/ufo/browser.py defines what any browser provider must offer, like connection details and session information, without starting a browser itself.

The other files are adapters that plug real browser choices into that boundary. The Browserbase adapter starts a fresh hosted Chrome session for a run, preserves temporary login state while it is active, transfers files between the remote browser and the system, and then removes the session. The Browser Use adapter exposes two existing tool names, browser_task and wide_browse, but sends the work to Browser Use’s cloud browser agent instead of the project’s own automation. The sandbox Chrome adapter launches a private headless Chrome inside the turn’s sandbox, connects through Chrome DevTools, and manages ports, proxy helpers, downloads, reconnection, and cleanup.

### [BUA CDP communication and runtime safety](stage-12.3.md) `stage-12.3` — 5 files

This stage is the browser automation “control room.” It is shared behind-the-scenes support used while the system is driving Chrome. Its job is to talk to Chrome safely, understand the answers, run small bits of page code, and prevent surprises from breaking the main automation flow.

The cdp.py file opens the main WebSocket connection, which is a two-way message pipe, to Chrome’s DevTools Protocol, the remote-control API for Chrome. It sends commands, waits for matching replies, and delivers browser events to the right waiting code. The wire.py file acts like a customs checkpoint for these messages. It describes the expected JSON shapes and checks incoming data before the rest of the system trusts it. The runtime.py file builds on that pipe to run JavaScript inside the current page and report page-side failures clearly. The dialogs.py file watches for web page pop-ups and accepts or dismisses them quickly so automation does not hang. The errors.py file preserves useful failure details, including what browser actions already happened, so recovery code can retry safely.

### [BUA session, tab, and turn lifecycle](stage-12.4.md) `stage-12.4` — 4 files

This stage is the browser “workbench” used during each turn of the main work loop. A turn is one round of work where the system may inspect a page, click, type, navigate, or download something. The code here keeps the browser connection lazy: it opens Chrome only when a browser action is actually needed, then cleans up safely when the turn ends.

The backend file is the surface that tools use. It offers actions like reading a page, clicking, typing, uploading files, managing tabs, and fetching downloads. The session file holds the live connection to Chrome and stores the browser state for the turn, so those actions have one shared place to work through. The tabs file keeps the system’s tab list matched with the real browser, like keeping a map updated while rooms are opened, closed, or entered. The settle file waits after actions until the page is ready enough to continue, while ignoring noisy background activity such as ads. Together, these pieces make browser work reliable and orderly.

### [BUA page understanding and element targeting](stage-12.5.md) `stage-12.5` — 4 files

This stage is the browser’s “page understanding” layer. It runs during the main work loop, after a page is open and before the system decides where to click, type, or read. Its job is to turn a complex live web page into a safer, simpler description that an AI can use.

The page module is the main scanner. It captures the page’s accessibility tree, which is a browser-made outline of visible controls and text, along with positions, frames, and stable element references. It can render this either as an action-focused tree for clicking and typing, or as markdown for reading.

The content module wraps this page data for higher-level tools. It extracts text or search results and keeps responses within safe size limits. The find module searches the rendered accessibility text for matching buttons, links, fields, and other elements, then checks that matches still point to real targets. The coordinate module acts like a map converter, translating screenshot points from an AI vision model into actual browser click positions and choosing screenshot sizes that avoid image distortion.

### [BUA action execution, input, forms, and downloads](stage-12.6.md) `stage-12.6` — 6 files

This stage is the action layer for browser automation. It sits in the main work loop, between an agent’s plain request, such as “click this button” or “download that PDF,” and the low-level messages Chrome needs to receive. The actions file defines the menu of allowed requests and what information each one must carry, like a standard order form. Before an order is sent, fixup cleans up small missing details, such as adding a default wait time or making sure a field is focused before typing. Computer then performs the action by sending real browser input events for clicks, typing, scrolling, screenshots, and waits. Keys handles the tricky keyboard details, including modifier keys like Ctrl or Shift and Mac-style shortcuts, so fake typing behaves like real typing. Forms provides safer helpers for filling fields and uploading files, with clear errors when a page element cannot be used. Downloads watches for files, forces download behavior when needed, and waits until the automation agent can access the saved file.

## [Subagents, skills, and structured long-running work](stage-13.md) `stage-13` — 19 files

This stage is shared support for work that is too large, specialized, or long-running for one agent turn. It gives the main agent a way to call helper agents, load reusable “skills” as saved instructions and files, and keep track of goals across many turns.

The skills files define how skills are stored, read, selected, and loaded without repeating the same guidance. The model catalog skill is built from the live model list, while skill_create stores user-made skills safely, and the web community bridge can fetch public skills without trusting bad or oversized responses. Subagent profiles describe helper workers: general assistants, writers, website builders, homepage publishers, and research agents. The subagents runtime starts these child turns, checks their expected input and output shapes, waits when needed, and returns results. Brief pipeline config splits writing into outline, draft, and critique steps. Website and research delegation tools hand off focused jobs and collect summaries or JSON results. Objectives storage and tools record plans, steps, evidence, blocks, and delegated work so progress survives across turns and is checked against real workspace facts.

## [Connectors, source syncing, search, and memory retrieval](stage-14.md) `stage-14` — 94 files

This stage is shared support for the agent’s main work and for background syncing. It is how the system reaches outside knowledge, brings that knowledge inside, and finds it again later. Connector action execution is the “front desk” for live actions in apps like Slack or GitHub. It lists available tools, checks what they need, runs them through brokers such as Composio or Pipedream, and keeps secret credentials away from the agent.

Source ingestion, indexing, and recall is the “library team.” It connects to outside sources, copies records into internal pages, notices updates and deletes, splits text into searchable pieces, and stores indexes for later recall. The source tools let agents register these sources, choose what to sync, and set wake-ups when content changes.

Search and memory files define common shapes so the rest of the system can ask for web results or remembered information without caring which provider is underneath. Perplexity and research tools provide web search and page fetching. Turbopuffer stores and searches memory chunks. Enrichment providers add person and company details from live data or safe recordings. Package marker files simply make these modules importable.

### [Connector action execution](stage-14.1.md) `stage-14.1` — 12 files

This stage is the system’s safe doorway to outside services during the main work loop. When the agent needs Slack, GitHub, Gmail, or another app, it does not get the user’s secret token. Instead, connector brokers act like reception desks: they list available accounts and tools, explain what each tool needs, run the chosen action, and pass files back and forth.

The core access file defines the trusted handoff points, including how tools are discovered, executed, and used by sync jobs without leaking secrets. The general connector tools extension gives the agent searchable commands for finding connectors, inspecting tool inputs, running actions, and moving files. Composio support includes a resolver that treats many Composio apps as one catalog, plus a broker, client, MCP short-call helper, and proxy that run tools or HTTP requests through Composio. Pipedream support mirrors this with its package entry point, broker, client, and proxy for Pipedream Connect actions and credentials. Slack hooks add finishing touches, such as bot attribution and updating connection buttons after a Slack account is linked.

### [Source ingestion, indexing, and recall](stage-14.2.md) `stage-14.2` — 73 files

This stage is the system’s intake and recall pipeline. It runs mostly in the background after accounts or file sources are connected, and it keeps outside knowledge fresh for search and memory. The core sync runtime is the engine: connectors fetch records page by page, use bookmarks to resume later, turn records into stable pages, notice changes, and record deletes. The many provider groups are the plug adapters for real services, such as Google Drive, Slack, GitHub, Jira, Salesforce, Stripe, Zendesk, HR tools, finance tools, and marketing tools. They each speak that service’s API, then translate the results into the same internal stream.

Gbrain Markdown sources do a similar job for local folders or GitHub repositories of Markdown files. Account plumbing stores credentials, creates feeds, recognizes resources, and remembers sync progress. Once pages arrive, indexing and embedding code breaks text into smaller pieces, converts meaning into searchable number patterns, and stores it for search and memory recall. The providers package marker simply lets Python import all these connector modules.

#### [Core source sync runtime framework](stage-14.2.1.md) `stage-14.2.1` — 4 files

This stage is the main machinery that keeps outside content sources in step with UFO. It runs during the regular sync work loop, with some shared support code used by many connectors. A connector is a small adapter that knows how to talk to one outside system, such as a web app or file service.

connector.py defines the rules every connector must follow. It also provides tools for grouping incoming records into pages and for tracking cursors, which are bookmarks that say “continue from here next time,” even when a stream is split into many parts like projects or channels.

rest.py is the shared web-request layer for connectors that use REST APIs, meaning ordinary HTTP calls that return data such as JSON. It handles login, retries, response reading, and pagination.

backend.py bridges connector output into UFO’s internal format. It builds stable pages, tracks deletes, skips bad records safely, and chooses safe stopping points for large imports.

sync.py ties it all together. It fetches source documents, stores current bodies, records changes, removes deleted items, and provides a reliable change feed for indexers downstream.

#### [Indexing, embeddings, and memory recall](stage-14.2.2.md) `stage-14.2.2` — 6 files

This stage is shared behind-the-scenes support for finding things again later. It turns large pieces of text into smaller chunks, gives those chunks searchable “embeddings” (lists of numbers that roughly capture meaning), and stores them in indexes or memory stores.

The core indexing file is the common doorway. It breaks long text into search-sized pieces, asks an embedding provider to describe them as numbers, and keeps the index updated when content changes. The default index is the built-in filing cabinet: it stores chunks and can search by exact words or by similar meaning. The OpenAI embedding extension is the translator that calls OpenAI’s service, handling API keys and batching so other code can simply ask for embeddings.

The memory store builds on this. It saves recallable memories, searches them when a user asks something, and keeps memories made from pages aligned with their source pages. The memory events file defines shared event names and size limits. The research observations file keeps a clean, capped list of web sources found or read in a conversation, so they can be shown later as reliable sources.

#### [Gbrain Markdown page sources](stage-14.2.3.md) `stage-14.2.3` — 3 files

This stage is the intake desk for Gbrain’s Markdown-based knowledge pages. It runs before the pages can be searched or used, gathering text either from a folder on the local computer or from a GitHub repository. The local-folder part reads every Markdown file under a chosen root folder and turns each one into a page, while checking paths carefully so a mistaken or malicious filename cannot make it read files outside that folder. The Git-backed part does the same job for a remote repository. It first checks whether the repository has changed, like asking “is there new mail?” before downloading, so it avoids unnecessary work. When needed, it refreshes the local copy and passes the Markdown files onward. The shared page-building code decides which files really count as Markdown pages, makes sure their text can be safely read, and gives each page a clear title. Together these pieces convert raw Markdown files into clean source pages Gbrain can index and search.

#### [Source extension account plumbing and sync metadata](stage-14.2.4.md) `stage-14.2.4` — 6 files

This stage is shared support for source extensions, the parts of the system that read outside services like Google or GitHub. It helps both when an account is first connected and later when background sync jobs keep data up to date. The connected-account piece creates private feed rows for an account’s main streams right after connection, and can try again later if that setup failed. The direct-auth piece supports a simpler path where a member stores their own API key; it turns that key into a bearer credential, meaning a token the sync worker can present to the outside service. The Google helper separates “you do not have permission” from “the service is temporarily out of quota,” so the system knows whether to skip one stream or retry the run. Resource recognition matches URLs and synced records to the same real-world item, such as a pull request. Triggers store notification rules for changed shared sources. Watermarks act like bookmarks, remembering the latest record already read.

#### [Workspace, document, communication, and support source providers](stage-14.2.5.md) `stage-14.2.5` — 15 files

This stage is shared behind-the-scenes support for bringing outside workspace knowledge into the system. Each provider is a connector: it talks to one service’s web API, reads allowed content, and reshapes it into steady “records” or readable pages that the rest of the sync and search system can store, update, and recall.

The knowledge-base and document connectors cover Airtable bases and tables, Confluence spaces and pages, Notion pages and databases, Google Docs, Google Drive files, Google Sheets rows, and Google Meet transcripts or notes. The support connectors bring in Freshdesk and Zendesk tickets, users, help articles, forums, and settings. The developer connector reads GitHub repositories, issues, commits, comments, and releases. Communication connectors read Gmail and Outlook mail, Google Calendar events and attendees, Microsoft Teams teams, chats, channels, and messages, and Slack users, channels, messages, and threads.

Together, these files act like adapters for different plug shapes. Each service speaks differently, but these providers translate them into one common form for search and recall.

#### [Work management, recruiting, HR, and incident source providers](stage-14.2.6.md) `stage-14.2.6` — 15 files

This stage is shared behind-the-scenes support for bringing outside company data into the system. Each file is a connector, meaning a small adapter that knows how to talk to one web service, ask for data, follow result pages, and reshape the answers into records the rest of the product can store and search.

The work-management connectors cover Asana, ClickUp, Jira, Linear, monday.com, and Wrike. They read projects, tasks, issues, comments, boards, teams, goals, and related activity so the system can remember what work is happening and where. The recruiting connectors, Ashby, Greenhouse, and Recruitee, read candidates, jobs, applications, interviews, offers, and departments. The HR connectors, BambooHR, Deel, and Rippling, bring in employee, contract, time-off, timesheet, payroll, company, and team information. Calendly adds scheduling data such as users, event types, meetings, and invitees. PagerDuty and Sentry add engineering operations data, including incidents, on-call schedules, services, software errors, releases, and events. Together, these adapters act like import docks for many tools, all feeding the same sync pipeline.

#### [CRM, marketing, advertising, and customer data source providers](stage-14.2.7.md) `stage-14.2.7` — 12 files

This stage is shared behind-the-scenes support for bringing outside customer and marketing data into the system. Each file is a connector, like a custom plug for a different service. The plug knows how to ask that service for data, handle its page-by-page replies, and reshape the answers into records the rest of the system can store, search, and resume syncing later.

ActiveCampaign, HubSpot, Salesforce, Attio, Apollo, and Intercom cover customer relationship data such as contacts, companies, deals, tasks, conversations, tickets, notes, and accounts. Klaviyo and Mailchimp bring in marketing data such as profiles, audiences, campaigns, lists, events, subscribers, and reports. Facebook Ads and Google Ads pull advertising structures and performance numbers, from accounts and campaigns down to ads and daily metrics. Instagram reads business pages, posts, stories, and analytics through Meta’s API. Typeform imports forms, responses, workspaces, themes, images, and webhooks.

Together, these connectors turn many different outside APIs, each with its own rules, into one steady source-sync flow.

#### [Finance, billing, commerce, and contract source providers](stage-14.2.8.md) `stage-14.2.8` — 11 files

This stage is shared behind-the-scenes support for bringing outside money and contract records into the system. It does not run the business logic itself. Instead, each file is a connector, like a plug adapter, that knows how to talk to one external service’s API, meaning its web doorway for data.

The finance connectors cover business spending, banking, and accounting. Brex and Ramp read company spend data such as cards, vendors, receipts, bills, and reimbursements. Mercury reads bank accounts and transactions. QuickBooks and Xero read accounting records such as invoices, bills, contacts, payments, and journal entries.

The billing and commerce connectors read customer and sales activity. Chargebee and Recurly handle subscription billing records. Stripe reads payments, customers, invoices, subscriptions, and related details. Square reads customers, locations, orders, refunds, catalog items, payments, and inventory.

The document connectors, DocuSign and PandaDoc, read envelopes, templates, documents, and contacts. Together these providers turn many different outside API formats into the system’s common stream of searchable records, while tracking paging and sync progress.

## [Artifacts, media, hosted sites, and document outputs](stage-15.md) `stage-15` — 41 files

This stage is shared support for turning an agent’s work into things people can see, open, download, or share. It sits after tools create results, and it also supports the main work loop when sites or document previews need to stay available.

One part handles hosted websites. The site tools start servers, publish static or server-backed apps, and set a homepage. The audit code checks that an app is safe and matches its design before deployment. Source helpers move site files in and out of storage, while the site store records names, owners, permissions, ports, and saved files. Ingress files build signed links, route browser traffic to the right sandbox or stored site, isolate each site’s web address, and report broken sites back to the agent. Preview and share-card code captures screenshots for thumbnails and shared links. Conversation-slot code shows allowed sites inside the chat.

Another part handles artifacts and media. Artifact objects wrap shared files with metadata and permissions. Signed URL and download-route code let people fetch them safely for a limited time. Preview records describe stored images. Document rendering and office-file helper scripts turn PDFs, Word, PowerPoint, and Excel files into previews, comments, repaired files, or annotated outputs.

### [Document and office-file helper scripts](stage-15.1.md) `stage-15.1` — 22 files

This stage is shared behind-the-scenes support for document workflows. It is a toolbox of command-line scripts used after an agent has inspected or changed office files. The document-review scripts keep the review organized: constants.py names the state and log files, models.py defines a review issue, manage_state.py records review progress in JSON and a log, and __init__.py makes the folder importable. The annotate scripts turn saved findings into visible feedback: PDFs get highlights and notes, PowerPoint files get comments by editing their internal XML, and Excel files get cell comments.

The Office helpers open the “zip packages” inside Word, PowerPoint, and Excel files. DOCX unpack.py and pack.py expand and rebuild Word files, comment.py adds comment records, and accept_changes.py uses hidden LibreOffice to accept tracked edits. PPTX unpack.py and pack.py expand and rebuild presentations, repair.py fixes generated files, slides.py cleans, adds slides, or makes previews, and __init__.py supports imports. XLSX _soffice.py runs LibreOffice safely, while recalc.py recalculates formulas and reports errors. PDF helpers fill real forms, place text using page layout clues, or render pages as images.

## [Scheduled jobs, notifications, monitors, and maintenance loops](stage-16.md) `stage-16` — 23 files

This stage is the system’s night shift: work that happens outside a live user request. The core job runner gathers scheduled job definitions, finds workspaces with pending work, and runs each job safely inside one workspace so runs do not duplicate or leak across teams. A shared scheduled-fire key format keeps delayed task IDs consistent.

Several loops then do useful upkeep. Notification draining batches pending notices and wakes the right agent conversation. Scheduled task and pause runners use cron-style schedules, meaning repeating time rules, to fire due tasks or resume waiting workflows once. Monitor jobs poll user-created monitors and alert agents when something changes.

Other jobs improve stored information. The memory condenser cleans and merges remembered facts. Report digest code summarizes new scheduled reports and skips unchanged ones. The preview renderer fills in missing shared-file cover images. Source-style maintenance includes retry-safe background patterns, product metrics that show workspace progress, and a small homepage cleanup for chat-first workspaces.

The self-improvement loop is more cautious. It proposes prompt changes, replays old conversations without rerunning tools, grades results with a model, and only promotes changes that pass the gate repeatedly. Package files simply make extensions importable.

## [Result publication, teardown, recovery, and cleanup](stage-17.md) `stage-17` — 5 files

This stage is the system’s “put everything away safely” phase. It runs after a turn finishes, is stopped, crashes, or gets stuck. A turn is one unit of work in a conversation. The goal is to publish the final state, cancel what should no longer run, save useful records, and prevent half-finished work from being left behind.

The stop surface is the front door for a user or member asking to stop a running turn. It checks whether stopping is allowed, marks the turn as cancelled, may start the next needed turn, and notifies live listeners that the old work ended. The cancellation helper does the careful inner step: it stops the workflow before recording cancellation in the database.

Workspace change tracking records what files changed, using the sandbox’s file scanner as the trusted source. Delivery cleanup is a safety net for child turns whose results were saved but not handed back to their parent. Runtime instance cleanup keeps running server processes visible and sweeps for stuck processes, workflows, child turns, and turns that look busy but cannot move forward.

## [Persistence, database schema, and durable stores](stage-18.md) `stage-18` · (cross-cutting) — 10 files

This stage is the system’s long-term memory. It is shared behind-the-scenes support used during startup, normal work, and recovery after changes or crashes. The database holds structured records such as workspaces, conversations, schedules, notifications, and monitors. Blob storage holds large files that do not fit neatly in database rows.

The core database doorway is core/src/ufo/db.py. It opens safe database sessions, runs migrations that update the schema, and keeps each workspace’s data separated. core/src/ufo/schema/tables.py is the main blueprint for the core tables, while schema/__init__.py simply makes those definitions importable. core/src/ufo/blob.py is the file cabinet for large byte data, using local disk in development or S3 in production. core/src/ufo/harness/durability.py helps old saved workflow records remain readable after code moves.

The extension stores add specialized shelves to the same memory system: notifications manage inbox rows, enrichment stores profile and permission data, monitors store repeating watch jobs, pauses store conversations waiting to resume, and schedules store recurring tasks and safely hand due work to background runners.

## [Public SDKs, protocols, generated types, and shared contracts](stage-19.md) `stage-19` · (cross-cutting) — 67 files

This stage is shared behind-the-scenes support. It is not the startup path or the main work loop. Its job is to define the public “agreements” that let extensions, generated clients, and messaging backends talk to the core system without reaching into private internals.

The runtime extension contracts define the safe context, shared records, object names, and conversation slots that extensions use while work is running. The extension authoring SDK and the SDK facade modules are the clean front doors: they re-export approved tools for skills, jobs, connectors, sources, browser access, models, search, memory, billing, identity, credentials, permissions, and visibility. Small utility modules add shared helpers for callbacks, HTTP cookies, logging, feature flags, scheduled runs, and untrusted text.

The iMessage protocol area supplies the same kind of stable boundary for messaging. Package marker files make generated modules importable. Google API protobuf support provides common annotation types. The generated iMessage protobuf files define chats, messages, attachments, events, groups, polls, and streaming heartbeats. The provider contract and gRPC stubs then give adapters and remote services a consistent plug shape.

### [Core runtime extension and shared record contracts](stage-19.1.md) `stage-19.1` — 5 files

This stage is shared behind-the-scenes support. It does not run the app by itself. Instead, it defines the common rules that other parts rely on when extensions, conversations, and background work need to talk to each other.

The ext package marker is the signpost. It says this folder is where the runtime extension API lives: the public set of shapes and promises that add-ons and built-in features must follow. The context file defines the safe toolbox given to an extension or background job. It lets that code do approved things, such as read its settings, call a model, open a conversation, or read synced pages, without giving it full access to private data or other workspaces.

The conversation slots file defines the small conversation panels an extension can fill, such as artifacts, sources, tasks, sites, automations, and workspace changes. The object name file gives one shared way to name and reference things. The records file defines the standard “work ticket” for a turn, so the user interface, storage, queues, agents, and runtime all understand the same facts.

### [Extension authoring SDK contracts](stage-19.2.md) `stage-19.2` — 8 files

This stage is the public “front desk” for people writing extensions. It is shared support rather than part of startup or the main work loop. Its job is to give extension authors stable import paths, so they can use approved pieces of the system without depending on private internal files that may change.

Each file is a doorway for one kind of extension work. context.py exposes the safe context objects an extension may use while it runs. manifest.py provides the types and helpers for describing an extension’s identity and capabilities. jobs.py offers the tools for declaring background jobs. skills.py re-exports the public skill helpers and classes. tools.py gathers the approved building blocks for defining or running tools. connectors.py exposes connector and OAuth pieces for linking outside services. sources.py provides the blocks for syncing external content like mail, chat, repositories, or REST services. surfaces.py exposes the pieces for building user-facing surface extensions. Together, they act like labeled shelves in a workshop: extension authors know where to pick up the right parts.

### [SDK service, content, and runtime resource facades](stage-19.3.md) `stage-19.3` — 11 files

This stage is shared behind-the-scenes support for people building on top of the system. It is not where the main work is performed. Instead, it provides stable “front doors” in the SDK, so extension authors can import approved tools without depending on the project’s private folder structure. These files mostly re-export existing runtime pieces, meaning they point to real implementations elsewhere rather than adding new behavior.

The audience module exposes conversation-audience tools. Browser exposes the allowed browser connection interface. Hub gathers hub classes and event-frame types. Index lets extensions plug in search and embedding backends, while search exposes the general search interface. Listings provides helpers for returning long lists one page at a time. Memory exposes memory-search types. Models collects model clients, message formats, tool-call types, pricing records, and permission helpers. Objects exposes object kinds and helper classes. Sandbox gathers tools for controlled execution areas. Terminal exposes approved terminal and blob-store types. Together, these facades act like a clean control panel over deeper machinery.

### [SDK access, identity, billing, and visibility facades](stage-19.4.md) `stage-19.4` — 11 files

This stage is a set of public front doors for extension authors and other SDK users. It is shared behind-the-scenes support, not the main work loop. Its job is to let outside code use approved identity, access, billing, and visibility tools without depending on private internal paths that may change.

The files mostly re-export trusted pieces from deeper runtime code. accounting exposes the objects used to describe workspace cost reports, while balance exposes tools for prepaid credit, payments, and auto top-ups. authority publishes the allowed execution authority names, and authproxy publishes authentication-proxy types. bearer provides only safe bearer-token checking, meaning it can verify login tokens but not create secret ones. credentials exposes credential objects, and grants exposes grant and connection audit tools. operator gives operator-only web pages the same session helpers and access rules as the runtime. seats exposes seat state and rules for membership or licensing. subjects names who can see disclosed data, such as a whole shared workspace or a specific member. surface_token exposes helpers for tokens used by surface-facing authentication. Together, these files act like a reception desk: callers get the right approved tools without entering the engine room.

### [Public SDK package utilities and lightweight helpers](stage-19.5.md) `stage-19.5` — 8 files

This stage is shared behind-the-scenes support for people building on the public UFO SDK. It is not the main work loop itself. Instead, it provides stable “front doors” that extension code can import, even when the deeper internal code moves around.

The package marker makes ufo.sdk importable. Several files are simple signposts: delivery_register re-exports prompt text used by the runtime, flags re-exports feature-flag helpers, scheduled_fire exposes helpers for making and reading keys for scheduled task runs, and o11y opens the approved path for logging and metrics, meaning records of what happened and simple measurements.

Other files provide small pieces of active support. callback_page builds the browser page shown after sign-in, install, or consent, guiding the person back, closing the window, or redirecting them. http gathers safe request and response types and centralizes session cookie rules. untrusted gives everyone the same wrapper for text that came from outside the system, so it can be displayed with the right caution. Together, these helpers make extensions safer and more consistent.

### [iMessage protocol package markers and Google API support](stage-19.6.md) `stage-19.6` — 8 files

This stage is shared behind-the-scenes support for the iMessage protocol code. It does not run the main app or perform message work itself. Instead, it makes sure Python can find and load the generated protocol modules that other parts of the system depend on.

Most files here are package markers. The __init__.py files in proto, google, google.api, photon, photon.imessage, and photon.imessage.v1 act like labels on folders. They tell Python, “this folder is importable code.” That lets the rest of the project refer to the generated iMessage version 1 protocol files using normal Python imports.

The two generated Google API files provide a small but important vocabulary used by those protocol modules. http_pb2.py defines Protocol Buffers message types such as Http, HttpRule, and CustomHttpPattern. Protocol Buffers are a common format for describing structured messages. annotations_pb2.py registers the google.api.http annotation, which describes how a remote procedure call can correspond to an HTTP request. Together, these files provide the scaffolding the generated iMessage protocol code expects.

### [iMessage v1 generated protobuf message contracts](stage-19.7.md) `stage-19.7` — 11 files

This stage is shared behind-the-scenes support for the iMessage extension. It does not send messages by itself. Instead, it provides the agreed “forms” that other code fills in when it talks about iMessage data. These files are generated from Protocol Buffers, a format for describing structured data so different parts of a system can read the same information in the same way.

The address file defines people’s contact addresses and which service they use, such as iMessage, SMS, or RCS. The attachment service and attachment type files describe how attachments are named, uploaded, downloaded, and tracked. The chat service and chat type files describe chats, participants, typing, read state, backgrounds, and chat updates. The event service file defines the stream of changes the system can follow to stay caught up. Group, message, and poll type files describe group changes, messages, reactions, edits, receipts, stickers, and poll data. The streaming file adds a heartbeat message, like a pulse, to keep live connections alive.

### [iMessage provider contract and generated gRPC stubs](stage-19.8.md) `stage-19.8` — 5 files

This stage defines the boundary between the rest of the app and an iMessage-like messaging source. It is shared behind-the-scenes support: other code can ask for chats, messages, attachments, or past events without needing to know whether the data comes from a local database, a phone bridge, or a network service.

The provider.py file is the main contract. It describes what a message provider must be able to do, and it defines the simple data shapes for messages and attachments that the rest of the extension expects. Think of it as the agreed plug shape for any iMessage adapter.

The four generated gRPC files are the network wiring. gRPC is a system for calling functions on another process or machine as if they were local. The attachment, chat, message, and event service stubs give clients ready-made methods to call those remote services. They also give servers matching hooks where real implementation code can be plugged in. Together, the contract and generated stubs let the extension talk to messaging backends in a consistent way.

## [Observability, safety gates, and generic infrastructure](stage-20.md) `stage-20` · (cross-cutting) — 10 files

This stage is shared behind-the-scenes support. It is not one main workflow; it is the guardrails and dashboard used by many workflows while the system starts, serves requests, runs tools, and handles background work. The containment module is the main file safety gate. It checks untrusted paths before reading, writing, deleting, or walking folders, so tricks like symlinks cannot escape the allowed workspace. Workspace and agent-scope modules make sure code knows which workspace and which agent it is acting for, protecting secrets, billing, and database access from crossing boundaries. Observability records traces, metrics, structured logs, stack summaries, and service checks, with redaction to hide sensitive data. Image preview validation rejects corrupt, mislabeled, oversized, or costly images before they can cause trouble. Listings provides safe cursor-based paging, so long lists can be read in pieces without losing place. Tools groups ordered work into safe parallel chunks. Untrusted wraps outside text so agents treat it as information, not commands. File-change limits give one shared maximum path length. The Redis package marker simply makes that extension importable.

## [Billing, usage accounting, and commercial controls](stage-21.md) `stage-21` · (cross-cutting) — 5 files

This stage is the system’s money meter. It runs partly before work starts, partly while models and tools are being used, and partly afterward when usage must be reported or paid for. Its job is to make sure a workspace has enough credit, measure what it used, turn that use into a cost, and send the right records to billing services.

The main ledger is accounting.py. It records workspace usage, applies charges to prepaid balances, checks spending limits, and prepares records for outside billing systems. balance.py is the prepaid wallet. It stores credit in very small dollar units, records purchases and refills, decides whether work can continue, and provides the messages users see when credit is low or gone. pricing.py is the price list for model usage: it converts token counts into costs and stamps records with the exact price table used. ufo_ext_metronome.py connects the local system to Metronome for usage reporting and Stripe for payments, billing pages, admin tools, and automatic refills. __init__.py simply makes the billing code importable.

## [Evaluation, samples, and local development fixtures](stage-22.md) `stage-22` · (cross-cutting) — 7 files

This stage is behind-the-scenes support for testing, evaluation, and local development. It gives developers safe, predictable stand-ins for real services, so they can check agent behavior without touching real mailboxes, calendars, messages, or customer data.

The harness package marker simply makes shared test helper code importable. The eval_env package marker introduces a deterministic evaluation environment, meaning a setup that behaves the same way every time. Its manifest is the main engine: it wires up fake but realistic connectors for email, calendar, code search, GitHub, Drive, Stripe, HubSpot, and Greenhouse. Tests can load known data, let an agent act through normal connector routes, then inspect exactly what changed.

The fake iMessage local line supports development machines without a real messaging backend. It lets setup screens proceed, including QR code and SMS-link flow, but never sends real messages. The sample skill probe is a simple “is this installed?” check. The sample extension demonstrates the extension SDK end to end. The self-improvement corpus builder turns failed tool-use conversations into examples for later evaluation and learning.
