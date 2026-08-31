# System Handbook — Stage Index

Each stage below links to its full page; the paragraph is the stage's role in the system.

## [Operator entrypoints, deployment recipes, and product selection](stage-1.md) `stage-1` — 13 files

This stage is the front door of the system. It runs before the main server or background jobs start, when an operator or deployment script decides what kind of UFO workspace to create and what it should include. The main command tool, ufoctl, turns human actions such as initializing, serving, inspecting, or repairing a workspace into concrete setup work. The bundle builder then packages a repeatable deployment with the app, configuration, extension lockfile, and sandbox client. The extension store is the catalog-and-shopping-cart layer: it finds extensions, pins them into the lockfile, and removes them when needed. The sandbox client helper simply locates the already-built client program and gives a clear error if it is missing.

The pack files are product recipes. They choose groups of extensions, skills, and services for local assistant use, hosted assistant use, billing development, evaluations, DSQA, GDPVal, and a small sample pack. Finally, the sandbox scripts build the agent runtime image and verify that its internet proxy path is safe before deployment proceeds.

## [Database migration and install-time schema preparation](stage-2.md) `stage-2` — 183 files

This stage runs during install or upgrade, before the system serves users or starts workspace jobs. It prepares the database, which is the system’s long-term memory. Alembic is the tool that applies these changes in order, like following a stack of numbered renovation plans. The env.py file is the entry door: it connects to the database, loads the table definitions the code expects, and runs any missing migrations.

The core migrations build and reshape the main platform records: workspaces, users, agents, conversations, turns, sources, pages, permissions, runtimes, schedules, billing ledger entries, artifacts, audits, and app identities. Later core groups clean up old designs, repair model names, improve search speed with indexes, and retire unused extension data.

The extension migrations prepare optional feature areas. Memory stores traceable workspace knowledge. Sites stores hosted site deployments and sharing details. Skills, source triggers, scheduling, and web migrations keep custom actions, external starts, paused work, and chats usable. Coding, indexing, evaluation, monitors, objectives, reports, research, and sample migrations add their own specialized tables. Together they make old and new installations match the code that will run next.

### [Core foundation and initial platform schema migrations](stage-2.1.md) `stage-2.1` — 11 files

This stage is part of the system’s first startup story for the database. It uses Alembic, a tool that applies database changes in order, like numbered renovation plans for a building. The first migration creates the basic rooms: workspaces, users, agents, conversations, message turns, and cost records. Later migrations add more storage as the product grows. Credentials get their own encrypted table. Proposals record suggested changes and approval status. Turns can be nested under other turns, so subagents can do work inside a larger conversation. Extensions get a small JSON store. Source and page tables let the system remember where content came from and cache what it read. Slack and web migrations add those surfaces as places conversations and identities can come from, plus Slack reply tracking. Spend caps add budget rules and allow turns to pause when limits apply. Grants record permissions tied to providers and conversations. Runtime instances track running worker environments and their check-ins. Together, these migrations lay the first durable memory for the platform.

### [Core early runtime, source, ledger, and scheduling migrations](stage-2.2.md) `stage-2.2` — 16 files

This stage is behind-the-scenes setup for the database. It is made of migrations, which are small ordered changes that update stored data structures before the system does its normal work. Together they make the runtime safer, more trackable, and easier to scale.

Several changes expand the ledger, the system’s accounting book: it can now record egress, sandbox token use, price audit text, and entries tied to a whole workspace instead of only one turn. Turn records gain guards against duplicate resume work, trace links for following related work, and extra context such as sender or timezone. Scheduled task storage is added, including due times, claiming, clearer pause records, and the last turn a schedule fired on. Source records become more flexible by allowing extension-defined backends, and more reliable by counting repeated errors for backoff. Conversation records learn how to remember their sandbox handles and sandbox conversations, so isolated work areas can be resumed. Job-selection indexes act like a database shortcut, helping background workers find candidates quickly. Runtime instances can also belong to a shared fleet instead of one workspace.

### [Core surface, inbound message, source, and page migrations](stage-2.3.md) `stage-2.3` — 14 files

This stage is behind-the-scenes database upgrade work. A database migration is a small step that changes how stored data is shaped, like adding labeled drawers to a filing cabinet. These migrations prepare the system for safer multi-workspace use, clearer ownership, and more reliable message and page history.

First, 0030 ties surfaces, installations, conversations, and writeback jobs to the right workspace, so work from one workspace cannot be mistaken for another. 0032 adds speaker and authorization details to conversation turns. 0033 creates a holding table for inbound messages, with rules that keep them unique, ordered, and linked correctly; 0034 adds rendered arrival text for those messages, while 0035 removes the older rendered field after the design changes.

Several migrations improve source access. 0036 lets sources be marked removed without erasing them. 0043 marks grants as shared or not. 0044 records whether a source is shared or owned by a member. 0059 creates source-specific read permissions for agents and backfills existing live sources.

The page migrations add browse fields, rename timestamps, and move page ordering to workspace revision numbers. Finally, 0055 stores conversation audience rules, and 0058 cleans out retired page-alert extension data.

### [Core scheduling, access, workspace control, and fleet migrations](stage-2.4.md) `stage-2.4` — 15 files

This stage is behind-the-scenes upgrade work for the database, the system’s long-term memory. These migrations change what the system can store so newer code can run safely. Several files improve scheduling and turns: scheduled admission records turns that were allowed in because they were planned, scheduled tasks can expire, tasks and turns can say which member they act on behalf of, and child turns can be found faster by parent. Ledger export migrations add progress tracking and mark whether customer-owned encryption keys, or BYOK, were used. Seat and workspace migrations add seat limits, included seat counts, workspace admins, main agents, and later remove an old seat-shipping marker. Agent-related migrations attach installations, conversations, and scheduled tasks to the right agent, while allowing different agents to reuse the same task name. Shared-fleet and memory-surface migrations remove old storage fields and retired knowledge-graph tables. Finally, the connections migration splits one broad permission table into clearer pieces: external account connections and the grants that let agents use them.

### [Core turn, conversation, artifact, and transcript migrations](stage-2.5.md) `stage-2.5` — 13 files

This stage is behind-the-scenes database upkeep. A database migration is an ordered change to stored data or table shape, like adding labeled drawers to a filing cabinet while keeping old records safe. These migrations grow the system’s memory for conversations and turns. They loosen old “surface” rules and add shared artifacts, then give each artifact its own ID, previews, and better media type labels. They add reasons a turn can be admitted, including user intent, and store the friendly surface name where a conversation started. They create an audit trail for admins reading private transcripts, then remove an unused shortcut index from that trail. They track subagent work by recording pending child-task results and saved display names. They capture Git workspace changes made during a conversation. They also tune lookup speed for spoken turns, first generally and then by speaker. Together, these changes make conversations more traceable, artifacts richer, subagent activity clearer, and common transcript or speech searches faster, while each file also defines how to roll its change back when needed.

### [Core agent, ledger, membership, and extension-retirement migrations](stage-2.6.md) `stage-2.6` — 15 files

This stage is part of the behind-the-scenes upgrade path. It changes the database shape and cleans up old records so the rest of the system can keep running with newer rules. Several migrations update agents: they add internet access, reasoning mode, and sandbox size settings, and they move agents away from discontinued Bedrock model names. Scheduled tasks also change: one migration adds a simple paused flag, while a later one removes older core pause fields because pause handling moved into an extension.

The ledger, which is the system’s usage and billing notebook, gains separate counts for prompt and cache-read tokens, plus new entry types for images and videos. Workspace money tracking is added through balance and transaction tables. Membership and access records are also reshaped: email lookup becomes faster, shared account information moves onto connections, and workspaces switch from limited seats to unlimited members. Finally, cleanup migrations remove traces of retired extensions such as YC and exa, marking or deleting data the core system should no longer treat as active.

### [Core agent provisioning, model identity, and member metadata migrations](stage-2.7.md) `stage-2.7` — 12 files

This stage is behind-the-scenes upgrade work for the database, the system’s long-term memory. These migrations reshape old saved records so newer code can understand agents, members, and model choices safely.

Several changes make agents easier to create, own, and manage. The provisioning migration records where an agent came from, which extension supplied it, and what tool rules apply. Setup and spawn migrations add stored setup details, accepted input, produced output, and the owning member. Visibility, icon, default-icon, and archive migrations let agents be shared across a workspace, shown with the right symbol, and hidden from normal use without deleting them. The archive change also prevents the main workspace agent from being archived.

Member migrations add personal and invitation metadata: the last valid timezone seen for a member, plus when they were invited and who invited them.

The model repair migrations clean up saved agent settings for Claude Fable models. They fix invalid reasoning settings and replace older or vague model names with the exact served model IDs the system now expects.

### [Core billing, BYOK, surface operations, and turn reference migrations](stage-2.8.md) `stage-2.8` — 18 files

This stage is behind-the-scenes upgrade work. It is made of database migrations, which are small steps that change stored data so newer code can run safely. Together they sharpen billing records, conversation records, message routing, and turn tracking. Ledger changes add faster workspace/time lookup, record the exact debited amount, store detailed token usage with safety checks, support auto top-ups, and mark when a workspace has verified payment for overdraft access. Conversation changes store searchable titles and remember when titles have already been summarized. Turn changes record BYOK use, created references, connect arrival time, and add indexes so live or recent agent work is quick to find. Egress rules get a generation counter so cached access rules can be refreshed when they are stale. Mid-turn replies get their own table so partial answers can be claimed, retried, and not duplicated. Surface and iMessage migrations move routing to safer places, track which runtime owns a listener, and clean out old claim-code, receipt, opt-in, and project-binding records that no longer belong in shared extension storage.

### [Core late app identity, source cleanup, tool allowlist, and audit migrations](stage-2.9.md) `stage-2.9` — 16 files

This stage is a set of late database upgrades. A database migration is a one-time script that changes stored data or the tables that hold it. These changes run during upgrade, behind the scenes, so newer code sees cleaner and safer records.

Several migrations tidy old app and source identities. One retires broken QuickBooks sources with no company address. Others park sources that keep refusing work and add stable page identities so a source cannot save duplicate pages. A new object-change journal records what changed, who changed it, and when.

Another group updates built-in agents, which are app-like helpers in a workspace. Agents gain workspace skill settings, icons, reusable archived names, and a clear purpose field. Old code review agents are adopted by the newer coding app. The old Tasks app is archived, chat becomes the main agent, and wiki agents are made private to avoid accidental exposure.

The last group keeps operations consistent. Turn records get frozen billing identity data. Tool allowlists are renamed so saved Slack, iMessage, and object actions match the current tool registry, like updating labels on keys so they still open the right doors.

### [Core legacy branch and Daily Brief history migrations](stage-2.10.md) `stage-2.10` — 5 files

This stage is part of the project’s database upgrade story. It does not run the main product features directly. Instead, it records older changes to the database shape, so new or existing installations can move through history safely.

The knowledge graph migration creates the first tables for storing “things” the system knows about, such as people or companies, and the links between them. The first sweep migration adds tables for Daily Brief editions, one kind of saved brief for workspace members, and explains how to undo that change. The next sweep migration reshapes that Daily Brief storage: it removes records from the old design, changes the edition table, and adds a table for tracking Daily Brief applications.

The later two migrations tidy up this legacy path. One closes an extra Alembic branch, meaning it makes the migration tool see a single clean line of history without changing data. The last one drops the old Daily Brief tables because the application no longer uses them, while still keeping rollback instructions.

### [Memory extension migrations](stage-2.11.md) `stage-2.11` — 16 files

This stage is the memory extension’s upgrade path for its database. It runs during setup or deployment, before the main system relies on memory data. Each migration is a small numbered recipe that changes the database safely and, where possible, says how to undo it.

The first migrations create the basic storage: memory items, memory pages, memory kinds, confidence scores, and required workspace links. Later ones make everyday use faster by adding indexes, which are like book indexes that help the system find current memories or inventory lists without reading every row. The next group improves time and source tracking. It adds “as of” dates, copies page times into old records, records which page and exact page revision produced a memory, and then splits source links into their own table so one memory can be connected to several page-derived sources.

The final migrations refine what memory can represent. They add room-targeted memories, retired items that stay hidden after curation, new section and overview item classes, and a workspace member profile table. Together, these steps grow memory storage from simple notes into traceable, searchable workspace knowledge.

### [Sites extension migrations](stage-2.12.md) `stage-2.12` — 8 files

This stage is behind-the-scenes setup for the hosted sites feature. It is made of database migrations: ordered changes that teach the database what information it must store as the product grows. The first migration creates the hosted site record itself, including its workspace, conversation, name, port, visibility, creator, and timestamps. The next adds a required generation ID, giving old sites safe unique values too. Another links a site to a “homepage agent,” meaning the agent responsible for that site’s home page. One migration updates trusted seeded homepages so they are visible to the whole workspace when the existing data proves they belong there. Later migrations add optional preview image storage, share-card data for link previews, a deploy_generation number to track which deployment version is live, and a source_manifest text field to remember source details. Together, these steps act like carefully labeled drawers added to a filing cabinet, so hosted sites can be created, shown, shared, deployed, and traced reliably.

### [Skills, source-trigger, scheduling, and web extension migrations](stage-2.13.md) `stage-2.13` — 9 files

This stage is behind-the-scenes upgrade work. It is run when the system’s database needs to catch up with newer features, like renovating rooms in a house while keeping the furniture. One group of changes supports scheduled tasks by adding a table for paused conversations, so work can be stored and resumed later. Another group builds the storage for user-created skills: first saving each skill, then tying it to an agent, then adding routing-card details such as descriptions, dependencies, pinned state, and indexing data, and finally moving ownership back to the workspace level while removing duplicates. Source-trigger migrations create a clear table for conversations started by external source bindings, move old subscription records into it, and add delivery information so the system knows how triggers are delivered. The web migrations repair older web conversations by adding missing shared chat records and moving chat titles from web-only storage into the common conversation table. Together these migrations keep older data usable as features evolve.

### [Coding, indexing, evaluation, reporting, research, and sample extension migrations](stage-2.14.md) `stage-2.14` — 14 files

This stage is behind-the-scenes setup for several optional features. It is made of database migrations, which are small ordered changes that create, change, move, or remove stored data so newer code has the tables it expects.

The coding migrations first add storage for review inboxes and review runs, then connect review runs to the conversations that produced them. They later remove an older required agent link and finally move old review inbox data into the newer source-trigger conversation system before dropping the old tables. The evaluation environment migration adds fake email and calendar tables for each workspace, useful for tests and demos. The indexing migrations create searchable text chunk storage with embeddings, then make chunk identity workspace-aware. The monitors migration stores scheduled checks with timing and progress fields. The objectives migrations store goals, ordered steps, evidence, progress checks, and whether steps can run independently. The report digest migrations store readable summaries of reports and remember reports that were checked but unchanged. The research migration records observed source URLs during conversations. The sample migration adds a tiny per-workspace note table as a simple extension example.

## [Extension, app, provider, and capability registration](stage-3.md) `stage-3` — 46 files

This stage happens during startup. It is the system’s sign-in desk for capabilities: it finds what is installed, checks what is allowed, and records what the rest of UFO can use. The extension loader is the front door. It reads extension declarations and turns them into usable tools, hooks, skills, object types, credentials, web surfaces, backends, and background jobs. The skills runtime explains what a skill is, reads skill folders, lists available skills, and loads selected skills into an agent’s safe work area, called a sandbox.

The built-in workspace registration adds core apps such as Chat, Code, Issues, Wiki, Metrics, Radar, Meetings, Artifacts, and writing or briefing skill bundles. Backend provider registration adds outside services such as AI model providers, search, embeddings, connectors, feature flags, and Redis communication support.

The many extension manifests are registration cards for specific abilities: browser use, coding, research, sites, connectors, gbrain sources, iMessage, Slack, web portals, memory, objectives, monitors, report digests, scheduled tasks, debugging, and self-improvement. The sample extension acts like a fake practice shop so the whole registration system can be tested without real services.

### [Built-in workspace app and skill bundle registration](stage-3.1.md) `stage-3.1` — 12 files

This stage is part of startup and behind-the-scenes setup. It is where UFO tells itself which built-in workspace apps, agents, and skills are available before users start working. Most files here are manifests, meaning simple registration cards. They name an app, describe what it does, declare its home-screen skill, and list any setup, permissions, schedules, or helper skills it needs.

The app manifests register the main user-facing workspace tools: Artifacts for shared generated files and hosted sites, Chat for the main conversation agent, Code for pull request review, Issues for issue tracking work, Meetings for meeting support, Metrics for reports, Radar for monitoring signals, and Wiki for shared knowledge. The brief_pipeline manifest adds a chain of helper agent stages for producing briefs, while the documents manifest adds document-writing skills and a writing subagent.

The catalog_skill file is different: it builds a live “model catalog” skill, a table of available AI models, prices, limits, and features. The sample probe is a tiny test button that confirms a sample skill can run.

### [Backend provider and external service registration](stage-3.2.md) `stage-3.2` — 9 files

This stage is shared startup plumbing. It teaches the system what outside services and plug-in providers are available before the main work begins. The model registry is the central catalog: given a model name, it knows the provider, required key, client setup, and price. The Bedrock extension adds Amazon Bedrock-hosted models to that catalog. The OpenAI embedding extension adds the default service for turning text into number vectors used for search, but waits to contact OpenAI until work is needed.

Several files register connector options. Composio and Pipedream manifests announce which services they can connect to and which OAuth sign-in routes they use. The Composio resolver is a flexible front desk that can recognize many Composio toolkits by name and send them through one shared broker. Keyed connectors cover simpler services that use API keys, with rules for storing and sending those keys safely.

Flagship connects feature flags to Cloudflare so behavior can be switched on or off. Redis Hub registers Redis as shared live communication support for frames and terminal traffic.

## [Workspace onboarding, member identity, and account connection setup](stage-4.md) `stage-4` — 9 files

This stage covers the “getting connected” part of the system. It is used during first setup, signup, and later when a member links outside services. The main onboarding file creates a real workspace, adds the first admin, creates the main assistant, checks required secrets, and lets extensions run their own setup. The onboarding control API is the guarded doorway used by the Rust control plane, so rules about seats, ownership, credits, invitations, and prompts stay consistent.

Once a workspace exists, provisioning turns agents supplied by extensions into real workspace agents without overwriting user changes. Agent setup checks what each agent still needs, such as a linked account, password-like credential, or schedule. Grants records which member owns an outside account connection and which agent may use it.

Connection files are the bridges to specific services. Composio sends users through a hosted approval page and back. GitHub verifies the authorized installation really belongs to the user. iMessage reserves a phone number and gives opt-in instructions. The seed file adds a demo “Kitchen sink” conversation so new users can see the portal in action.

## [Serve runtime startup, liveness registration, and scheduler activation](stage-5.md) `stage-5` — 2 files

This stage is part of starting up the long-running UFO service. It is the moment when the process stops being just a program on disk and becomes a live worker in the system. The main entry point, core/src/ufo/serve.py, wires together the pieces the service needs: the web server, workflow runner, database access, extensions, credentials, sandboxes, connectors, live update hub, and background jobs. It also starts workspace-level schedulers, so each workspace can run its own timed or queued work.

The runtime_instance.py file acts like the service’s attendance sheet and cleanup crew. It registers that this process is alive, keeps that status fresh, and helps the rest of the system know which workers can still be trusted. It also runs repair loops in the background. These loops look for work left behind by crashes, cancelled tasks, or lost workflow attempts, then reclaim or clean it up. Together, these files turn on the service, announce it, start its background machinery, and prevent abandoned jobs from staying stuck forever.

## [Surface ingress and request routing](stage-6.md) `stage-6` — 24 files

This stage is the system’s front desk while the service is running. It receives traffic from people, browsers, chat apps, terminals, hosted sites, and outside services, then sends each request to the trusted inner parts of the system. It does not do the agent’s thinking itself. Instead, it checks who is calling, reshapes outside messages into the system’s internal format, and makes sure replies or files go back through the right door.

The messaging and chat surfaces handle conversation channels. Slack, iMessage, and terminal clients each have adapters that verify incoming events, turn them into agent messages, stream progress, and deliver final replies.

The web portal, hosted-site, and sandbox routes handle browser traffic. They serve the main app, protect hosted pages, and safely connect users to isolated sandbox workspaces without exposing private ports or guessed addresses.

The live streams, downloads, and callback routes keep users connected during work and setup. They publish live turn updates, serve signed artifact links, and finish account-connection flows such as OAuth returns.

### [Messaging and chat surfaces](stage-6.1.md) `stage-6.1` — 8 files

This stage is the system’s set of doors for conversations. It sits around the main agent work loop: it receives messages from people, turns them into a common internal form, then carries the agent’s replies back out to the right place. The central piece, core surface support, gives outside channels controlled access to trusted actions such as finding users, starting conversations, accepting messages, streaming live output, and delivering final answers.

Each channel then has its own adapter. The Slack surface checks that requests really came from Slack, converts Slack threads into agent turns, and posts replies, files, forms, and status updates. Slack mention handling changes Slack’s hidden user codes into readable names for storage, then restores real mentions when sending replies. Slack attribution and hooks add “sent via this agent” footers and tidy old connection buttons.

The iMessage surface does the same bridge work for texts and attachments, while its cloud helper talks to Spectrum’s service to send, receive, and authenticate. The ufo terminal surface exposes conversations and live output as simple commands a shell client can display.

### [Web portal, hosted-site, and sandbox ingress routes](stage-6.2.md) `stage-6.2` — 8 files

This stage is the web-facing front door for people using UFO in a browser. It sits in the main running system, after startup has configured servers and storage, and it decides what a visitor is allowed to see. The web portal file serves the main app, signs users in, carries chat messages, streams live agent replies, and shows workspace pages like settings, memory, sources, usage, and admin. The sites surface is the doorway for hosted sites: it checks access, shows a protected frame, redirects portal iframes, serves share images, or hides private details behind a plain 404.

The sandbox ingress pieces let browser pages safely reach work happening inside isolated workspaces. One file builds temporary port URLs, another signs and checks the short-lived tokens inside those URLs, and another creates safe per-conversation hostnames so guessed names cannot expose private data. The ingress server then verifies the visitor and either serves saved static files or proxies traffic to a live sandbox port. Two supporting web surfaces let operators inspect stored memory and let users browse community skills from skills.sh with friendly error messages.

### [Live streams, downloads, and callback routes](stage-6.3.md) `stage-6.3` — 8 files

This stage is the system’s set of “open doors” while work is running or while a user is returning from another service. It is not the core thinking loop itself. Instead, it lets browsers, terminals, and external providers stay connected to that loop.

The live-stream pieces work like a news feed for one running turn. core/src/ufo/runtime/hub.py publishes text, status, costs, replies, and results, and keeps a short memory so clients can reconnect. extensions/redis_hub/ufo_ext_redis_hub/stream_hub.py moves those updates through Redis Streams, a shared message pipe, so different servers can publish and read them. core/src/ufo/runtime/surfaces/hub_tail.py combines live messages with database checks, so late clients still learn when a turn ends or pauses. core/src/ufo/loop/steps.py turns internal workflow records into a readable timeline of model rounds, tool calls, and other steps.

The remaining routes help users get files or finish browser-based setup. core/src/ufo/runtime/surfaces/artifacts.py serves artifact downloads through signed links and can refresh expired ones for valid members. core/src/ufo/sdk/callback_page.py shows the final “done” page. core/src/ufo/runtime/surfaces/cli.py and extensions/pipedream/ufo_ext_pipedream/provider.py complete OAuth-style account connections, including Pipedream’s extra hosted setup step.

## [Workspace object system and portal mutation APIs](stage-7.md) `stage-7` — 24 files

This stage is the system’s “object counter” after a request has been logged in and sent to the right place. A workspace object is a named record, like an agent, member, file, site, or connected account. core objects.py is the safety gate: it checks shape, visibility, ownership, and which extension owns each kind before listing, reading, changing, deleting, or running actions.

Built-in kinds cover agents, members, workspaces, conversations, artifacts, credentials, installed extensions, chat surfaces, and governed prompt-change proposals. They expose what users may inspect or edit, while blocking unsafe writes, such as editing conversations or secrets directly. Extension kinds add connectors, gbrain sources, memory records, monitors, reports, sites, user-created skills, synced pages and sources, plus scheduled-task visibility rules.

Support files make the machine smooth: listings gives stable next/previous pages, object_scope tracks which agent an action is acting for, and object_views turns internal actions into safe button-like descriptions. Finally, web panels connect portal forms and buttons to the normal conversation-based write path, so portal mutations follow the same safety rules instead of bypassing them.

## [Conversation admission, turn creation, cancellation, and control](stage-8.md) `stage-8` — 4 files

This stage is the gatekeeper for conversation work. It sits just before the main agent work loop: messages, button presses, timer wake-ups, or internal events arrive here first, and only become durable “turns” if the rules allow it. A turn is one unit of conversation work, like one ticket in a support queue.

admission.py is the main front door. It checks that new work is in the right order, not a duplicate, allowed for billing and seat limits, visible to the right people, and safe to place on the queue. ambient_reply.py handles a quieter case: a thread message that does not directly call the agent. It decides whether the agent should answer or stay out of a human side conversation.

stop.py covers the stop button. It confirms the running turn belongs to the conversation, starts cancellation, tells live viewers what happened, and can move the member into a new follow-up turn. cancellation.py is the shared brake pedal: it stops the running workflow first, then records the turn as cancelled so it cannot continue later.

## [Turn engine claim, context assembly, and prompt construction](stage-9.md) `stage-9` — 7 files

This stage is the preparation room for one agent turn, before and during the first model call. A turn is one piece of pending conversation work. The queue safely claims that work, makes sure only one runner owns it, and records success or recovery if something crashes. The engine is the traffic controller: it gathers the conversation, watches for cancellations or spending limits, absorbs new user messages that arrive late, calls tools and the model, saves the transcript, and publishes the result.

Several helpers build the agent’s “briefing packet.” Compaction shortens old conversation history into a checked summary while keeping recent messages unchanged, so the model has room to think. Skill selection chooses only the most relevant saved skills, instead of flooding the prompt. The delivery register loads the writing rules agents must follow when answering or reporting to a parent agent. The conversation slot code adds visible hosted sites as safe links when the viewer is allowed to see them. Finally, prompt rendering fills the approved template, checks every slot, and creates a digest, like a fingerprint, so the exact prompt can be traced later.

## [Model dispatch, streaming response handling, and usage accounting](stage-10.md) `stage-10` — 4 files

This stage is part of the main work loop, after UFO has prepared a prompt and is ready to ask an AI model for an answer. It is like a switchboard: it sends the conversation to the chosen provider, listens as the answer arrives piece by piece, and converts everything into UFO’s own standard event format so the rest of the system does not need to know which provider was used.

The Anthropic and OpenAI files are the main provider bridges. They submit requests, read streamed text, notice when the model asks to call a tool, capture “reasoning” notes when the provider sends them, retry failures that are safe to retry, and report token usage so spending can be tracked. The OpenRouter extension adds another route to many OpenAI-style models, plus image and video tools, without making it part of the core code.

The replies file cleans special hidden reply tags from model output. It keeps the useful message text while preventing internal markup from leaking to users.

## [Tool collection and trusted host-side dispatch](stage-11.md) `stage-11` — 45 files

This stage is the system’s tool room and dispatcher during the main work loop. When the model asks to use a tool, the system first decides which tools are allowed for this turn, checks that their names and input forms are safe, records the request, then either runs a trusted built-in tool or sends it to an approved outside broker.

The registry is the rulebook for defining and collecting tools. The context file builds the safe workbench each tool receives: what it may read, use, and report back. The tool bridge lets code inside a sandbox, a locked-down running area, ask the main system to list or run approved tools without bypassing checks. Activity labeling turns tool calls into short user-friendly status messages.

The built-in tools cover everyday workspace work like files, shell commands, questions, sharing, and subagents. Connector tools safely reach external services such as Gmail, GitHub, Slack, web search, and MCP servers without exposing secrets. Domain-specific extension tools add deeper abilities for web work, documents, spreadsheets, PDFs, schedules, monitors, reports, debugging, and test-only fake services.

### [Credentialed connector and external API tools](stage-11.1.md) `stage-11.1` — 12 files

This stage is the system’s safe doorway to outside services. It is shared support used when the agent needs to find or run tools from apps like Gmail, GitHub, Slack, Notion, or web search, without seeing private passwords or access tokens. The core connector boundary decides which tools exist, how they are called, and how background sync jobs get temporary access safely.

Several adapters plug into that boundary. Composio and Pipedream clients talk to their hosted services, create login links, check connected accounts, list available actions, upload files, and run tools. Their brokers translate UFO’s standard tool requests into each service’s format. Their proxy files act like privacy screens: UFO sends a normal request, the proxy routes it through Composio or Pipedream, and the real secret stays hidden.

The connector tools file presents these outside actions to the agent in a searchable, inspectable form. The MCP extension adds tools published by workspace-configured MCP servers, a standard tool-sharing protocol. Perplexity provides web search and page reading through an API key. Slack tools guide workspace setup and conversation search. Together, these pieces let the agent use external services safely and consistently.

### [Built-in workspace, file, shell, question, share, and skill tools](stage-11.2.md) `stage-11.2` — 2 files

This stage is the agent’s everyday toolbox during the main work loop. It gives the agent controlled ways to act in the workspace, talk to the user, and coordinate longer jobs. The builtins file is the front counter of the toolbox. It defines actions such as running a shell command, reading or editing files, searching project contents, sharing a finished artifact, asking the user a question, loading extra skills, requesting secrets, connecting external accounts, and starting or managing helper agents called subagents.

The tasks file is the safety clerk for shell commands, especially slow ones. Some commands may keep running after the agent stops waiting for them. Instead of launching the same command again by mistake, this code writes each command into a task journal, like a logbook. Later the agent can look up the existing command, check its output, stop it, or continue watching it. Together, these files let the agent do useful work while keeping actions trackable and less likely to be duplicated.

### [Domain-specific extension tools](stage-11.3.md) `stage-11.3` — 27 files

This stage is the agent’s toolbox for specialized jobs beyond ordinary chat. It sits mostly in the main work loop and shared support layer: when the agent must use the web, manage a long task, edit a document, or support testing, these tools turn broad requests into careful actions.

The web tools act like a browser remote control, search desk, and publishing station. Planning, todos, scheduling, and monitors help the agent track goals, wait for replies or timers, and watch for outside changes. Document review tools store findings and write comments back into PDFs, PowerPoints, and spreadsheets. The Word and PowerPoint packaging scripts open Office files as zipped sets of XML text files, make precise edits or repairs, then rebuild valid documents. Spreadsheet helpers use LibreOffice in the background to refresh formulas. PDF utilities fill forms, place text on pages, and render pages as images. Finally, debugger reporting alerts engineers to serious workspace problems, while fake evaluation connectors give tests a safe, predictable version of services like email and calendar.

#### [Web browsing, research, and site publishing tools](stage-11.3.1.md) `stage-11.3.1` — 3 files

This stage provides the agent’s web-facing tools. It is part of the main work loop: when the agent needs to look something up, operate a browser, or publish a site, these files translate that request into safe, structured actions.

The browser tools are like a remote control for a real web browser. They let the agent open pages, read what is on them, click buttons, type into fields, upload files, and save downloads. They also keep all browser actions in the same turn using one shared connection, then clean it up afterward so nothing is left hanging.

The research tools are the fast lookup desk. They check that a search or fetch request is valid, send it to the configured search service, and return results in a simple JSON format the agent can read.

The site tools are the publishing station. They build, preview, deploy, publish, or assign hosted websites from sandbox files or running apps, while checking ownership, visibility, safety, and whether the site is ready.

#### [Planning, todos, scheduling, and monitors](stage-11.3.2.md) `stage-11.3.2` — 4 files

This stage is shared workflow support for agents that need to manage work over time, not just answer once and stop. It provides the “planner’s desk” for the system: goals, checklists, reminders, waiting points, and watches on outside changes.

The objectives tools help an agent break a larger goal into steps, track progress, and hand independent steps to subagents. They also separate a claimed attempt from real proof that a step is done, so the plan stays honest. The todos extension is a lighter checklist for one conversation. It stores tasks durably, meaning the list can still be shown and updated on later turns.

The scheduled tasks tools handle time-based work. They expose recurring tasks as workspace objects, and they let a workflow pause until either a person responds or a timer runs out. The monitor tool watches outside state once by rerunning a shell command later. Before arming the watch, it runs the command immediately and saves that first result as the baseline, like taking a “before” photo.

#### [Document review state and annotation scripts](stage-11.3.3.md) `stage-11.3.3` — 5 files

This stage is shared support for the document review workflow. It keeps track of what reviewers or automated checks have found, then writes those findings back into the original documents so people can see them in context.

The models.py file defines the basic shape of a review issue, such as what the problem is and where it was found. It also turns an issue into a readable comment. This gives all the other scripts the same “form” to use, like a standard note card.

manage_state.py is the control panel. It stores the review’s progress, claims, issues, and final summary in a JSON file, which is a simple structured text file. Because the state is saved, different steps can run separately and still share the same memory.

The annotation scripts are the output workers. annotate_pdf.py adds highlights and sticky-note comments to PDFs. annotate_pptx.py inserts comments into PowerPoint files by editing the PPTX package. annotate_xlsx.py copies an Excel workbook and adds comments to the right cells. Together, they turn saved findings into visible document feedback.

#### [Word DOCX editing and packaging scripts](stage-11.3.4.md) `stage-11.3.4` — 4 files

This stage is a set of behind-the-scenes tools for working on Microsoft Word documents without using Word directly. A .docx file is really a zipped package of XML files, which are text files that describe the document’s words, styles, comments, and settings. These scripts open that package, make focused changes, and close it again.

The unpack script is the “open the box” step. It expands a .docx into a folder and cleans the main document XML so later edits are easier to compare. The comment script works inside that unpacked folder. It adds a Word comment by updating all the XML pieces Word expects, not just the visible text. The pack script is the “close the box” step. It turns the folder back into a working .docx and tidies whitespace to keep the package neat. Separately, the accept changes script makes a copy of a document and asks LibreOffice to accept all tracked changes invisibly, useful for producing a clean final version.

#### [PowerPoint PPTX repair, slide, and packaging scripts](stage-11.3.5.md) `stage-11.3.5` — 4 files

This stage is a toolbox for working on PowerPoint files outside the main program flow. A .pptx file is really a zipped package of many smaller files, mostly XML, which is a text format used to describe slides, layouts, images, and links. These scripts let the system open that package, make safe changes, and close it again.

unpack.py is the first step. It takes a normal .pptx file, expands it into a folder, and tidies the XML so people and tools can read it more easily. slides.py works on that unpacked folder. It can remove unused pieces, add a new slide, or make a contact sheet, which is like a page of slide thumbnails for quick review. pack.py is the final step. It cleans the XML carefully, then zips the folder back into a valid .pptx without harming slide text. repair.py is a special fixer for files made by pptxgenjs, correcting known package and text issues that PowerPoint might otherwise complain about.

#### [Spreadsheet recalculation helpers](stage-11.3.6.md) `stage-11.3.6` — 2 files

This stage is a behind-the-scenes support step for working with Excel spreadsheets. It is used when the system needs a workbook’s formulas to be up to date before another tool reads the file. Instead of asking a person to open the spreadsheet and press “recalculate,” it uses LibreOffice, a free office program, to do that work automatically.

The shared helper file, _soffice.py, is like the launcher and map. It starts LibreOffice in a quiet mode, without showing the normal desktop window, so scripts can use it as a background worker. It also knows where LibreOffice keeps user macros on Linux and macOS, which helps scripts find the right support files.

The recalc.py script is the main tool in this stage. It opens an Excel workbook, tells LibreOffice to refresh every formula, saves the updated workbook, and checks for remaining spreadsheet error values such as broken formulas. Together, these files make spreadsheet recalculation repeatable and machine-driven.

#### [PDF form, layout, and rendering utilities](stage-11.3.7.md) `stage-11.3.7` — 3 files

This stage is shared document support for working with PDFs. It is not the main business logic by itself. Instead, it gives other parts of the system practical tools for seeing, filling, and marking up PDF files, much like a workshop with different tools for the same kind of material.

The form filling tool works with PDFs that already contain hidden fillable fields. It can find those fields, save a field map as JSON, which is a simple text data format, and later fill the PDF using values from that JSON. This turns a hard-to-see PDF form structure into editable data.

The layout tool helps when a PDF is only a static page, with no real form fields. It can inspect page layout, preview where answers should be placed, and write text onto the PDF at chosen positions.

The render tool converts each PDF page into a PNG image. That makes pages easy to view, compare, or pass to tools that work with normal images rather than PDF files.

#### [Debugger reporting and evaluation connectors](stage-11.3.8.md) `stage-11.3.8` — 2 files

This stage provides behind-the-scenes support for agents and tests, rather than tools a normal user would directly use. It helps the system report serious workspace problems, and it gives evaluation runs a realistic but controlled world to interact with.

The debugger reporting file defines a tool called report_problem. When an agent finds a workspace issue it cannot solve inside the chat, this tool creates a warning for deploy engineers. If possible, it also includes a debugger link to the exact conversation turn where the problem occurred, like leaving a pin on a map so engineers can inspect the right spot.

The evaluation environment manifest defines a fake but realistic set of connectors for testing. It includes things such as email, calendar, code search, app data, and a narrowly limited repair agent. Tests can then use the same connector route as production code, but with seeded, predictable data. Together, these pieces make the system easier to debug and safer to test.

## [Sandbox, terminal, browser, and document execution environments](stage-12.md) `stage-12` — 30 files

This stage provides the controlled “rooms” where risky or practical work happens: running commands, browsing the web, opening documents, moving files, and showing terminals. It is mostly behind-the-scenes support used during the main work loop whenever a tool needs to act outside the chat itself.

The conversation workspace file is the front door. It finds or creates the private sandbox for a conversation and keeps track of its location. The session layer is the safety wall: it offers one standard way to run commands, read files, write files, load skills, and reach services without leaving the allowed area. The execution-environment file prepares safe environment variables, giving tools placeholders instead of raw secret keys.

Below that, carrier and terminal code chooses where work runs: locally, in Docker, in a cloud sandbox, or through a user’s connected terminal. Browser adapters provide Chrome wherever it lives, while DevTools plumbing remotely controls tabs, JavaScript, dialogs, and downloads. The perception and interaction layer reads pages or rendered documents, finds buttons and fields, maps screen positions, and sends clicks, typing, uploads, scrolling, screenshots, and document actions.

### [Sandbox carrier implementations and terminal transports](stage-12.1.md) `stage-12.1` — 6 files

This stage is the system’s “workshop selector and wiring.” It sits behind the scenes when a conversation needs a place to run commands, edit files, serve a web port, or show a live terminal. The selector chooses the sandbox carrier, meaning the kind of workspace to use, and keeps old choices available so paused work can resume.

The local carrier runs commands in an ordinary folder on the host computer, useful for simple development without containers. The Docker carrier gives each conversation its own container, a more isolated mini-computer, and manages creating it, copying files, running commands, exposing ports, and cleaning up idle ones. The E2B carrier does the same kind of work on a remote cloud sandbox service. The member-terminal carrier lets a user’s own connected terminal act as the sandbox, with safe request-and-reply handling even if the browser connection blips. The Redis stream terminal adds a transport bridge for multi-pod deployments, passing small control messages through Redis Streams and larger terminal data through blob storage.

### [Browser provider adapters and sandbox Chrome launchers](stage-12.2.md) `stage-12.2` — 4 files

This stage is shared support for any part of the system that needs to use the web during a turn of work. Its job is to provide a usable Chrome browser, without forcing the main code to care where that browser lives. The core browser file defines the common contract: the system asks for a browser connection, and an adapter supplies one, whether it is local, sandboxed, or hosted.

The Browserbase adapter connects to a hosted Chrome service. It creates a fresh remote browser session for each run, keeps login and browsing state only for that run, then cleans the session up when finished. The sandbox Chrome adapter runs a real headless Chrome, meaning Chrome without a visible window, inside the same protected workspace as the conversation. It can start or reuse that browser, expose its debugging connection, and recover downloaded files. The Browser Use adapter is higher level: it sends whole browsing jobs to a cloud service through tools called browser_task and wide_browse. Together, these adapters act like interchangeable power plugs for web access.

### [Chrome DevTools session plumbing](stage-12.3.md) `stage-12.3` — 8 files

This stage is the low-level plumbing that lets the system drive a real Chrome browser. It sits behind the main browser tools: when a turn needs Chrome, backend.py opens or reuses a browser connection, recovers if Chrome crashes, moves files in or out, and cleans up afterward. session.py is the shared workspace for that live browser session. It holds the current tabs, downloads, and helper objects, then routes tasks to the right specialist.

cdp.py talks to Chrome through the DevTools Protocol, which is Chrome’s remote-control channel. It sends commands over a WebSocket connection, waits for replies, and passes browser events to listeners. tabs.py uses that channel to open, close, switch, and navigate tabs. runtime.py safely runs small pieces of JavaScript inside a page and returns the result. dialogs.py prevents pop-up alerts from blocking automation by recording and accepting or dismissing them. downloads.py detects and waits for downloads, including files Chrome might otherwise preview. settle.py decides when a page has reacted enough after an action, while ignoring harmless background activity.

### [Rendered content perception and browser interaction primitives](stage-12.4.md) `stage-12.4` — 9 files

This stage is the system’s eyes and hands for rendered content. It sits in the main work loop, after a page or document is visible and before the agent decides or performs the next action. For documents, document_renderer.py asks a rendering service to make safe page images and text, then checks the result before use. For live browser pages, content.py collects page text, the element tree, or search matches. page.py turns that page view into model-friendly text and maps references like “e12” back to real screen points. find.py searches the accessibility tree, which is the browser’s list of visible controls such as buttons and fields, and formats usable matches. coordinate.py keeps model image coordinates aligned with real browser pixels. Once an action is chosen, fixup.py corrects common incomplete instructions. computer.py sends the final clicks, typing, scrolling, waiting, screenshots, and page commands to Chrome. keys.py builds the exact keyboard messages Chrome expects. forms.py fills fields, attaches files, and verifies that uploads really happened.

## [Subagents, delegation, and multi-step agent workflows](stage-13.md) `stage-13` — 12 files

This stage is part of the main work loop, when the assistant decides a job is too large, specialized, or slow to do alone. It can hand work to child agents, like asking helpers in a workshop to research, browse, write, or build while the main conversation continues. The fallback profile defines what a basic helper may do, and the spawn catalog shows the agent which helpers are available right now. The core subagents code starts those helpers, checks permissions and costs, validates the agreed input and output formats, and returns results either immediately or later in the parent conversation.

Specialized profiles give helpers narrower jobs. The browser helper can use web tools; browser delegation runs one browsing session or many parallel visits. Research delegation runs many research helpers and merges their JSON results into a saved file. The brief pipeline passes typed messages through outline, draft, and critique helpers. The document helper drafts and edits prose. The objectives store records plans, steps, evidence, and blockers. The site tools hand off website building, guide safe app creation, and audit the result before accepting it.

## [Source ingestion, page sync, indexing, and memory building](stage-14.md) `stage-14` — 69 files

This stage is the system’s knowledge intake and memory workshop. It runs mostly behind the scenes during setup and ongoing sync. When a member connects an account, the connected helper creates the needed feeds, and can repair missed setup later. The connector rules define the shared language every source must use. The REST helper handles ordinary web API calls, including sign-in, retries, and paging. The backend turns provider records into standard pages, deletes, cursors, and warnings. The sync engine stores page bodies, notices changes, and passes updated pages onward.

Around this core, many adapters bring in data from Markdown folders and GitHub, Google and Microsoft, workplace tools, CRM and support systems, HR tools, finance services, forms, documents, and seeded evaluation data. All of them feed the same pipeline.

After pages arrive, the indexing parts split text into smaller searchable chunks, create embeddings, and store them in the default database index or Turbopuffer. The memory parts then recall useful facts, extract lasting notes, merge duplicates, and keep long-term knowledge tidy.

### [Local and repository Markdown sources](stage-14.1.md) `stage-14.1` — 3 files

This stage is the intake point for Markdown content. It lets the gbrain extension read pages from places people already keep notes: a folder on disk or a GitHub repository. Its job is shared behind-the-scenes support for syncing, not the main user-facing work. It turns files into “source pages,” meaning units of text the rest of the system can compare, search, and update.

The folder connector scans a local directory safely, finds Markdown files, turns each one into a page, and reports the whole current state as a snapshot. The GitHub connector does the same kind of job for a remote repository. To avoid needless work, it first checks whether the repository has changed, only downloads it when needed, and stores enough information to continue from the last known state. The pages helper is the common translator. It decides which files really count as Markdown pages, makes sure their text can be read safely, and picks a sensible title for each page. Together, these parts act like a loading dock for Markdown knowledge.

### [Search chunking and index backends](stage-14.2.md) `stage-14.2` — 3 files

This stage is the behind-the-scenes search memory for the system. Its job is to take large source files, break them into smaller pieces, store those pieces, and later find the most relevant ones when the system needs context. The shared indexing file sets the rules for this process. It decides how text is split into searchable chunks, how those chunks are sent to storage, and what “contracts” storage and embedding providers must follow. An embedding is a list of numbers that represents the meaning of text, so similar ideas can be compared even when the words differ.

The built-in index is the default storage engine. It saves chunks in the project database and can search either by normal word matching or by comparing embeddings. It works with PostgreSQL or SQLite, depending on what the system is using.

The Turbopuffer extension is an alternate backend. Instead of local database search, it sends chunks and embeddings to the Turbopuffer service, then asks that service to find matches by meaning, words, or both.

### [Memory storage, recall, and consolidation](stage-14.3.md) `stage-14.3` — 4 files

This stage is the system’s long-term notebook. It works mostly behind the scenes during the main conversation loop: before answering, it can recall useful memories; after syncing or learning, it can clean and reshape what was stored. The events file defines a small shared “event language” and limits, so other code can consistently report when memory was recalled before a response. The store is the central cabinet and librarian. It saves memory rows, searches for the ones that match a user’s question, ranks them, hides or retires ones that should not be used, and keeps the search index up to date. The condenser is the editor. It reads raw pages and older memory rows, pulls out clear facts, merges duplicates, writes summaries and people profiles, and retires clutter that no longer helps. The research observations file keeps track of web sources found during research, safely trims their text, stores them, and shows them later in a “Sources” panel tied to the conversation.

### [Google and Microsoft source connectors](stage-14.4.md) `stage-14.4` — 10 files

This stage is the set of “adapters” that lets the sync system talk to Google and Microsoft services. It sits in the main data-gathering loop: each connector calls an outside web API, reads pages of results, and turns them into the project’s standard records so they can be stored, searched, updated, or deleted later.

On the Google side, Gmail reads mailbox changes and formats messages as text. Google Calendar reads events and attendee rows. Google Docs lists documents through Drive, fetches their content, and extracts plain text. Google Drive covers files, shared drives, permissions, comments, and revisions. Google Meet imports transcripts and generated notes. Google Sheets reads spreadsheets, tabs, and rows. Google Ads streams accounts, campaigns, ads, and statistics. The shared google.py helper tells connectors whether an access problem means “skip this” or “try again later.”

On the Microsoft side, Teams uses Microsoft Graph, Microsoft’s web API, to read teams, channels, chats, and messages. Outlook also uses Graph to sync mail, contacts, calendars, conversations, and folders.

### [Work, engineering, and collaboration connectors](stage-14.5.md) `stage-14.5` — 14 files

This stage is shared behind-the-scenes support for syncing outside work tools into the system. Each connector is like an adapter plug: it talks to one service’s API, meaning its web doorway for data, then reshapes the answer into records the rest of the system can store, search, and recall.

Airtable reads bases, tables, and records. Calendly reads users, event types, scheduled events, and invitees. Asana, ClickUp, Jira, Linear, monday.com, and Wrike read project-management data such as tasks, issues, projects, comments, teams, boards, folders, and custom fields. Confluence and Notion bring in team knowledge, turning pages, databases, comments, and rich content blocks into readable text instead of raw technical data. Slack reads conversations, messages, threads, users, and participants. GitHub reads engineering work such as organizations, repositories, issues, commits, releases, and users. PagerDuty reads incident-response information, including services, schedules, incidents, and on-call records. Sentry reads error-tracking data such as projects, issues, events, members, and releases. Together, these files feed many workplace sources into one common search-and-sync pipeline.

### [CRM, support, marketing, and social connectors](stage-14.6.md) `stage-14.6` — 12 files

This stage is shared behind-the-scenes support for bringing outside customer data into the system. Each connector knows how to talk to one service’s API, meaning its web doorway for requesting data, and turns the answers into “source pages”: small batches of records the rest of the sync system can store, search, and recall.

The marketing connectors cover ActiveCampaign, Klaviyo, and Mailchimp. They pull things like contacts, audiences, campaigns, lists, events, reports, and email activity, while handling each service’s paging rules so large accounts are copied safely. Apollo, Attio, HubSpot, and Salesforce cover sales and CRM data such as companies, people, deals, tasks, notes, opportunities, conversations, analytics, and deleted-record notices. Facebook Ads and Instagram bring in social and advertising data, including campaigns, ads, posts, stories, accounts, and performance metrics. Freshdesk, Intercom, and Zendesk cover support and helpdesk work, such as tickets, conversations, users, organizations, help articles, tags, teams, and activity logs. Together, these files act like adapters for many plug shapes, making very different services feed one common sync pipeline.

### [HR and recruiting connectors](stage-14.7.md) `stage-14.7` — 6 files

This stage is the set of adapters that let the system bring in people and hiring data from outside services. It is shared support for the sync process: when the main system needs records, these files know how to talk to each provider’s web API, which is a service’s online doorway for requesting data. Each connector turns provider-specific pages of results into standard “streams,” meaning named flows of records that the rest of the system can process in the same way.

Ashby, Greenhouse, and Recruitee cover recruiting. They fetch items such as candidates, jobs, applications, interviews, offers, departments, and activity details. BambooHR focuses on employee operations, including employee records, time off, timesheets, metadata, and custom reports. Deel reads contractor and HR records such as contracts, forms, payslips, tasks, and timesheets. Rippling brings in company, worker, and team data. Together, these connectors act like translators at the front desk, each speaking one vendor’s language and handing clean batches to the common sync machinery.

### [Finance, billing, commerce, and document connectors](stage-14.8.md) `stage-14.8` — 12 files

This stage is shared behind-the-scenes support for bringing outside business data into the system. Each file is a connector: a small translator that knows how to talk to one company’s web API, meaning its online doorway for requesting data. The connectors ask for records, follow pagination when results come back in batches, and reshape the answers into the system’s common stream of syncable records.

Brex, Ramp, and Mercury bring in spend, card, vendor, transfer, account, and banking transaction data. QuickBooks and Xero bring in accounting records such as invoices, accounts, contacts, and payments. Chargebee, Recurly, and Stripe bring in billing and subscription data, including customers, invoices, subscriptions, coupons, and payments. Square brings in commerce data such as orders, catalog items, locations, inventory, customers, and payments. DocuSign and PandaDoc bring in document, template, envelope, and contact information for later search and recall. Typeform brings in forms, responses, workspaces, themes, images, and webhook settings. Together, these connectors act like plug adapters, making many different services fit the same sync machinery.

## [Recurring jobs, scheduled automation, reports, and self-improvement](stage-15.md) `stage-15` — 25 files

While the server is running, this stage is the backstage crew for work not tied to one live request. candidates.py safely finds workspaces with pending work, and jobs.py schedules each job in the right workspace without duplicate pileups. delivery.py rescues missed child-task results, preview_renderer.py fills in missing file thumbnails, and product.py recounts funnel metrics from existing data.

The scheduled-task pieces are the clockwork: schedules.py stores recurring prompts, cron.py checks repeat rules, scheduled_fire.py defines the exact run key, runner.py fires due tasks once, pauses.py stores timed waits, and pause_runner.py wakes them. Monitors use monitors.py for watch records and monitor_runner.py to run checks and notify agents. Reports use digest.py for summary rules and writer.py to create feed entries. Other helpers clean old site homepages, store source-change wakeups, and refresh web start suggestions.

Self-improvement builds test cases from past failures, proposes prompt rewrites, replays old conversations without re-running tools, judges answers, gates promotion, uses one model access path, and runs these checks on a schedule.

## [Output publication, artifacts, sites, panels, and user delivery](stage-16.md) `stage-16` — 5 files

This stage is where finished work becomes visible and usable. It is part of the delivery end of the system’s story: after something has been built, answered, scheduled, or hosted, these pieces help publish it safely to the places users can see.

The hosted-site registry keeps track of each public site: who owns it, which conversation it belongs to, where it is running, and whether it can be previewed or shared. The site source mover copies a site’s files between durable storage and a temporary work area, so the system can edit or rebuild it without losing the original. The media previewer visits a running sandbox site and captures a PNG screenshot, like taking a quick photo of the page. The share-card builder combines that screenshot with UFO branding so links look informative when pasted into chat or social apps. Finally, the conversation slot for scheduled tasks turns saved automation records into a short, safe “Automations” panel. Together, these parts turn internal results into user-facing artifacts, previews, sites, and panels.

## [Teardown, cancellation, retry recovery, and resource cleanup](stage-17.md) `stage-17` — 1 files

This stage is the system’s clean-up and recovery area. It runs when a turn, request, job, or whole process is finishing, failing, being cancelled, or restarting after a crash. Its job is to leave the system in a safe, understandable state: save what must be remembered, stop work that should no longer run, release outside resources like browsers, terminals, sandboxes, or containers, and make retries safe so the same cleanup can happen more than once without causing damage.

The file `workspace_changes.py` handles one important piece of that story: it records what changed in a conversation’s workspace during a turn. It does this by scanning files in a git-like way, meaning it compares the workspace before and after to find added, edited, or removed files. This creates a durable record of “what this turn left behind.” That record remains useful even if detailed tool output is later shortened, compacted, or missing from the chat history.

## [Cross-cutting persistence, shared schema, and durable records](stage-18.md) `stage-18` · (cross-cutting) — 9 files

This stage is the system’s shared filing cabinet. It is behind-the-scenes support used during startup, normal requests, background jobs, recovery after crashes, and reporting. At startup, the database doorway in db.py opens connection pools, runs migrations, and enforces workspace boundaries so one customer’s rows are not mixed with another’s. The table definitions in schema/tables.py describe the cabinet’s drawers and rules, while schema/records.py defines common record shapes for turns, agents, questions, credentials, final results, and workflow IDs so all parts of the system speak the same language.

Several files handle durable content. turns/transcript.py defines the saved format for conversations and compacted summaries. loop/transcript.py safely reads and writes those transcripts in workspace storage, making sure old results do not overwrite newer ones. blob.py stores raw files, such as images or attachments, either locally or in S3, while keeping workspace files separate from deployment-wide files. media/previews.py records where image previews live and their sizes. durability.py makes saved workflow objects safe to reload after crashes or code changes. Finally, the skill creation store keeps user-made workspace skills saved, listed, loaded, and deleted without name clashes.

## [Cross-cutting SDK, extension APIs, protocols, and type contracts](stage-19.md) `stage-19` · (cross-cutting) — 64 files

This stage is shared behind-the-scenes support. It is the rulebook that lets UFO’s core, extensions, model providers, browser tools, sandboxes, search systems, memory systems, and iMessage code talk to each other without guessing each other’s data shapes.

The public SDK facade files act like stable front doors. Extension authors import approved tools for manifests, tools, jobs, auth, credentials, connectors, models, search, memory, browser control, logging, billing, feature flags, and administration without depending on fragile internal paths. The core extension contracts define what an extension may declare, what safe runtime context it receives, and what it may show in conversation panel slots.

The sandbox and browser contracts define the exact messages used to request tool actions, automate pages, and report browser errors. The generated iMessage protobuf and gRPC files define message, chat, attachment, event, and streaming records, plus the service “sockets” used by iMessage providers. The direct contract files add shared shapes for memory search, AI model calls, web search, object names, and spawned-agent inputs and outputs. Together, these pieces are the common language that keeps many replaceable parts working as one system.

### [Public SDK facades for extension capabilities](stage-19.1.md) `stage-19.1` — 19 files

This stage is shared support for extension authors. It is not the main work loop itself; it is the stable front desk they use when adding new abilities to UFO. Each file in ufo.sdk re-exports approved pieces from deeper internal code, so outside extensions can rely on steady import paths even if the inside of the project is reorganized.

The manifest, tools, skills, jobs, and surfaces doorways help an extension describe itself, offer actions, run background work, and connect new user-facing “surfaces,” meaning places where people or other systems interact with UFO. Sources, connectors, authproxy, credentials, and grants support bringing in outside data safely, including login methods, OAuth-style connections, stored secrets, and permission checks. Models, index, memory, and search expose the building blocks for AI model calls, embedding and indexing data, remembering past information, and finding results. Browser, sandbox, and terminal provide controlled access to outside execution environments. Http gives extensions request and response types and safe cookie helpers. Context exposes identity and runtime context, so extensions know who is acting and where.

### [Public SDK facades for platform utilities and administration](stage-19.2.md) `stage-19.2` — 15 files

This stage is shared behind-the-scenes support for extension authors and other outside code. Its job is to provide stable “front doors” under ufo.sdk, so callers do not have to reach into internal folders that may change. Most files here do not invent new behavior. They re-export, meaning they pass along selected tools from deeper modules under safer public names.

The billing doors are accounting for spend reports and balance for prepaid balance tools. seats exposes seat management helpers, while flags exposes the feature-flag check, a simple way to ask whether an optional feature is enabled. audience and subjects provide the standard ways to describe who can see conversation data. untrusted provides the marker used to fence off text from outside sources. delivery_register shares prompt constants used when building delivery-register prompts. hub, listings, and objects publish common SDK types for hubs, paged listings, and stored objects. o11y, short for observability, offers approved logging and metrics. scheduled_fire exposes helpers for cron-like scheduled triggers. operator publishes operator-only session/authentication tools. surface_token exposes token creation and checking for surfaces.

### [Core extension API contracts](stage-19.3.md) `stage-19.3` — 3 files

This stage is shared behind-the-scenes support for UFO’s extension system. It defines the rules and safe boundaries that let extra features plug into the core app without getting unlimited access.

The manifest file is the extension’s “menu.” It tells UFO what the extension offers, such as tools, credentials it needs, routes, hooks, agents, skills, or backend services. The core can read this menu and decide how to wire those pieces into the larger system.

The context file builds the safe toolbox an extension receives when it actually runs. Instead of handing over the whole system, UFO gives a carefully scoped runtime context: the current workspace, the extension’s own stored data, declared credentials, allowed files, model access, conversations, synced sources, and similar approved abilities.

The conversation slots file defines what extensions may show inside conversation panels. It gives clear data shapes for things like artifacts, sources, tasks, sites, and automations, plus provider objects that know how to summarize and read that slot content. Together, these contracts let extensions add useful features while staying predictable and contained.

### [Sandbox and browser wire contracts](stage-19.4.md) `stage-19.4` — 4 files

This stage is shared behind-the-scenes support. It defines the “contracts” that other parts of the system rely on when they talk to a sandbox or control a browser. A contract here means a clear agreed shape for messages, like a form that must be filled in the right way.

The sandbox bridge file defines the shared language for live tool use. It says which tool requests are allowed, what replies should look like, and how the system lists the tools a sandbox can offer. This keeps the tool bridge predictable.

The browser actions file does the same for browser automation. It names the actions an agent may request, such as click, type, scroll, wait, or take a screenshot, and gives each action a valid structure.

The browser wire file checks raw JSON messages arriving from Chrome DevTools Protocol, the browser’s control channel, before the rest of the engine uses them. The errors file gives browser code a specific way to report impossible AI outputs, such as referring to something that is not really on the page.

### [Generated iMessage protobuf message contracts](stage-19.5.md) `stage-19.5` — 13 files

This stage is shared behind-the-scenes support. It does not start the app or run the main message loop by itself. Instead, it provides the “forms” that other code fills in when talking about iMessage data. Protocol Buffers are a compact data format; these generated Python files define the exact shapes of those data records so different parts of the system agree.

The Google annotation files describe how service calls can be linked to web-style HTTP methods and paths. The address file names the kinds of contact addresses, such as iMessage, SMS, or RCS. Attachment files define both attachment data and the service calls for uploading, downloading, creating, and reading it. Chat files define chat records and chat service requests, including creating chats, typing status, backgrounds, and chat event subscriptions. Group files describe group-change events. Message files define message records, reactions, edits, reads, stickers, and the service calls for sending, editing, listing, and subscribing. Poll files describe poll data. Event and streaming files define catch-up event requests and simple heartbeat messages used to keep a live connection recognizable.

### [iMessage service stubs and provider contract](stage-19.6.md) `stage-19.6` — 5 files

This stage is shared plumbing for the iMessage extension. It does not do the real work of reading or sending messages by itself. Instead, it defines the “sockets” where other code plugs in, and the network wrappers that let different parts of the system talk to those sockets.

The four generated gRPC files are the network wiring. gRPC is a system for calling code on another process or machine as if it were a local function. The attachment service wiring covers attachment calls, the chat service covers chat operations, the event service lets clients ask for missed event history, and the message service covers message-related calls. Each file gives client code a ready-made caller, and server code a place to attach the real implementation.

The provider.py file is the provider contract. It defines what a message record looks like, what errors a provider may return, and what actions the system can request. Together, these files form the public interface between iMessage-specific code and the rest of the application.

## [Cross-cutting security, authorization, credentials, and network policy](stage-20.md) `stage-20` · (cross-cutting) — 21 files

This stage is the system’s safety layer. It is not one step in the main work loop; it is checked whenever the system starts work, serves a page, runs an agent, opens a file, calls the network, or cleans up. token_signing.py is the tamper-evident seal maker for small signed messages. auth/bearer.py checks member login tokens, surface_token.py signs link-based route claims, and sdk/bearer.py lets extensions verify tokens without learning signing secrets. workspace.py and agent_scope.py keep the current workspace and acting agent tied to each decision. seats.py, web/audience.py, turns/audience.py, and turns/subjects.py decide who may talk, see agents, or read conversation items; turns/untrusted.py labels outside text as evidence, not instructions. credentials.py stores secrets encrypted and governs who may fill or use them; github_app.py and sources/direct.py turn approved grants or API keys into provider access. egress_resolver.py, egress_rules.py, and egress_control.py translate those permissions into proxy rules for outbound network calls and secret injection. artifact_url.py signs temporary downloads, image_previews.py rejects unsafe preview files, containment.py keeps file paths inside the sandbox, and ext/operator.py protects trusted operator tools.

## [Cross-cutting billing, metering, feature flags, and operator observability](stage-21.md) `stage-21` · (cross-cutting) — 7 files

This stage is shared behind-the-scenes support for money, feature access, and operator visibility. It is used throughout the system whenever a model is called, a tool runs, usage is reported, or staff need to inspect what is happening.

The billing pieces work like a cash register and receipt book. accounting.py records workspace usage, checks spend limits, charges prepaid balances, exports usage to billing partners, and builds spend reports. balance.py tracks prepaid credit in micro-USD, very small fractions of a dollar, and decides what happens when credit is low or gone. pricing.py stores model price tables and turns token counts into charges, keeping a version stamp so old bills can be traced to the exact prices used.

The external billing bridge is metronome.py, which reports usage to Metronome and connects prepaid payment flows to Stripe, including refill and billing portal support. flags.py is the system’s feature switchboard, letting code ask whether a feature is enabled for a workspace. o11y.py provides traces, metrics, logs, and safety filters. The debugger surface gives trusted operators read-only fleet inspection tools without changing customer data.

## [Cross-cutting configuration, catalogs, packaging, and import-time modules](stage-22.md) `stage-22` · (cross-cutting) — 73 files

This stage is shared behind-the-scenes support. It is not one single startup step or request path. Instead, many parts of the system consult it to know what is available, what is allowed, and where to find things.

Several sub-stages provide Python package markers. These small __init__.py files act like labels on folders, so the main code, app extensions, automation features, provider integrations, and generated protocol code can be imported by name. The nested scaffolding also includes document review constants, which keep shared filenames consistent.

The direct source files provide the system’s rulebooks. model spec defines what facts every AI model record must contain, while the catalog lists the built-in models, their providers, prices, limits, API routes, and key sources. config defines and checks the main ufo.toml deployment settings, stopping early when something important is missing or unsafe. proxy_serve, sandbox cache, and sandbox preview define shared service addresses, allowed providers, database settings, and safe sandbox routing. file_changes sets one common maximum file path length. Together, these pieces make the rest of the system predictable before real work begins.

### [Core package import markers](stage-22.1.md) `stage-22.1` — 20 files

This stage is quiet behind-the-scenes support. It is not part of startup, the main work loop, or shutdown. Its job is to make Python recognize folders as packages, meaning folders that other code can import from by name. Each __init__.py file is like a label on a drawer: it does not do the work inside, but it lets the rest of the system find that drawer.

The top-level ufo marker opens the main core package. The access, auth, billing, ext, kinds, media, models, onboard, runtime, sandbox, schema, sdk, skills, sources, surfaces, and turns markers do the same for their own feature areas. The loop marker makes the main loop code importable, and loop/prompts does this for prompt-related code inside it. The tools marker also labels the tools package and gives readers a small signpost toward the tool registry, handler context, and built-in tools. Together, these files create the import map that lets the real code connect cleanly.

### [App extension package markers](stage-22.2.md) `stage-22.2` — 8 files

This stage is quiet behind-the-scenes support. It does not start the app, run the main work loop, or shut anything down. Instead, it makes several app extensions visible to Python’s import system. An import is how one Python file asks to use code from another file. Each __init__.py file acts like a sign on a folder saying, “this folder is a package you may import from.”

The packages here represent user-facing application areas: artifacts, chat, code, issues, meetings, metrics, radar, and wiki. The files in those folders do not add behavior by themselves. They are more like labeled doors into separate rooms. When other parts of the system need chat features, issue tracking features, wiki features, or similar extension code, these markers make those folders reachable in a standard way.

Together, these small files provide the common entry points for the app_* extension packages, keeping each application surface separately organized but importable by the larger system.

### [Feature and automation extension package markers](stage-22.3.md) `stage-22.3` — 16 files

This stage is quiet behind-the-scenes support. It does not run the agent’s main work. Instead, these __init__.py files act like labels on drawers in a workshop. In Python, such a file tells the system “this folder is a package,” meaning other code can import tools from it.

Each marker opens the door to a different extension area. Brief pipeline, report digest, documents, research, and web mark places for reading, writing, summarizing, and online work. Browser and UFO mark more specialized computer-use behavior. Coding, debugger, and REPL mark developer tools for writing, testing, and interactive commands. Memory marks durable fact storage, while objectives marks goal-related features. Monitors and scheduled tasks mark automation that watches or runs things over time. Skill create marks agent-authored skills, and self-improvement marks offline review that can suggest human-approved prompt changes.

Together, these files are the project’s extension signposts. They make optional capability folders discoverable without adding behavior themselves.

### [Integration and provider extension package markers](stage-22.4.md) `stage-22.4` — 11 files

This stage is quiet behind-the-scenes support. It does not start services or run the main work loop. Instead, it puts nameplates on extension folders so Python can treat them as importable packages. An import is how one part of the program asks to use code from another part, like opening the right drawer in a workshop.

Each file here is an __init__.py marker. Most contain no running logic. They simply make extension areas visible to the rest of UFO: Composio, general connectors, GBrain, iMessage, Pipedream, Redis Hub, sites, Slack, and source integrations. The eval environment marker also documents its role: fake, predictable mailbox and calendar connectors used for testing and evaluation, so results do not depend on real outside services. The sources package marker opens the broader source-extension area, while the nested providers marker opens the specific folder where source provider modules live.

Together, these files form the import map for external integrations. They make sure later, more active code can find the right extension pieces when needed.

### [Nested extension import scaffolding and script constants](stage-22.5.md) `stage-22.5` — 11 files

This stage is quiet behind-the-scenes support. It does not start the app, run the main work, or shut anything down. Instead, it makes sure extension folders can be found and used by Python, and it gives a few scripts shared names for important files.

Most files here are `__init__.py` files. In Python, these are package markers: small files that tell Python, “this folder contains importable code.” They are like labels on drawers, so other parts of the system can reliably open the right drawer. The browser automation extension uses one for its `bua` folder. The document extensions use them for script folders in document review, PowerPoint, and Excel skills. The iMessage extension uses a chain of them inside its generated protocol folders, including `google.api` and `photon.imessage.v1`, so generated message definitions can be imported normally.

The only file with actual shared values is the document review `constants.py`. It names the saved review-state file and the review log file, keeping those filenames consistent across the review scripts.
