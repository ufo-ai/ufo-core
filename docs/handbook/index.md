# System Handbook — Stage Index

Each stage below links to its full page; the paragraph is the stage's role in the system.

## [Process entrypoints, command modes, and deployment preparation](stage-1.md) `stage-1` — 16 files

This stage is the system’s front door. It covers the commands people run when they start UFO, prepare it for deployment, set up a new workspace, or check that its sandbox is safe to use. The pack selection part chooses a “toolbox” of extensions for the current setting, such as local development, hosted cloud use, billing tests, or evaluation runs.

The ufoctl command in cli.py is the main local control panel. It helps developers create, run, inspect, and manage a workspace. When a workspace is new, onboarding.py creates the first workspace, admin user, assistant agent, checks needed secret keys, and lets extensions finish their own setup. bundle.py packages the chosen setup into a reproducible Docker build folder with pinned extensions.

For hosted deployments, ufo_control/main.py provides maintenance commands, such as starting the hosted gateway, preparing the database, and issuing invitations. serve.py is the main service launcher: it assembles the web server, background workers, storage, credentials, extensions, and sandbox support. The sandbox scripts build the runtime image and test that its secure proxy route works before release.

### [Pack selection and deployment capability bundles](stage-1.1.md) `stage-1.1` — 9 files

This stage is part of startup. Before the assistant begins work, the system chooses a named “pack,” which is like a prepacked toolbox for a certain setting. The extension store is the gatekeeper: it can search the catalog of available extensions, install one by recording it in a lockfile, and remove it later. That turns possible capabilities into the exact ones this deployment will load.

The pack files are the ready-made toolboxes. The local assistant pack enables the normal full assistant on local infrastructure. The billing pack adds billing setup so developers can test it locally. The hosted assistant pack selects features meant for managed cloud workspaces. The evaluation pack starts from the assistant tools, removes real broker connections, and adds fake test tools plus Docker support. The chief-of-staff pack focuses on Slack-driven work with meetings, notes, todos, people files, and logs. DSQA and GDPVal packs provide different evaluation capability sets. The sample pack is a small end-to-end proof that extensions, skills, and onboarding steps can be bundled correctly.

## [Database preparation, migrations, and rollback paths](stage-2.md) `stage-2` — 129 files

This stage is the database “renovation crew” that runs before normal serving, or when an operator upgrades or rolls back the system. It uses Alembic, a tool that applies ordered database changes, so the stored layout matches the code that will use it.

The runners and entrypoints connect Alembic to the right database and make sure the ground is safe before traffic starts. The core migrations then build and refine the main shared records: workspaces, members, agents, conversations, turns, permissions, runtime workers, sources, pages, scheduled tasks, usage ledgers, billing limits, agents, connections, and memory transitions. Other core groups add support for user-facing surfaces like Slack and web, inbound messages, artifacts, transcript access, sandboxes, subagents, and partial replies.

Extension migrations add shelves for optional features. Coding, source triggers, sweep, and pauses support developer automation. Memory, indexing, and research migrations store searchable chunks, facts, pages, and observed web sources. User-facing extension migrations prepare monitors, objectives, sites, skills, sample notes, evaluation data, and web chat data. Together, these scripts let the database move forward safely, or back out changes when needed.

### [Database preparation runners and Alembic entrypoints](stage-2.1.md) `stage-2.1` — 2 files

This stage is the database “make sure the ground is ready” step. It is used by operators or startup jobs before the service begins normal work, so the application does not run against missing or outdated tables. The database schema is the layout of the database: which tables and columns exist, like the floor plan of a warehouse.

The UFO control gateway has its own preparation file, control/src/ufo_control/schema.py. It creates the PostgreSQL tables the gateway needs, upgrades them when the expected layout changes, and checks that the database is safe to use. This protects multiple gateway replicas from accepting traffic while the shared database is not ready.

The core project uses core/src/ufo/schema/migrations/env.py as its Alembic entrypoint. Alembic is the tool that applies database migrations, which are step-by-step schema changes. This file tells Alembic how to connect to the project database and where to find the project’s table definitions, so migrations run in the right place and in the right order.

### [Core baseline identity, grants, and runtime schema migrations](stage-2.2.md) `stage-2.2` — 12 files

This stage is behind-the-scenes setup for the database. It is a set of Alembic migrations, meaning small ordered scripts that change the database shape as the product grows. The first migration lays the foundation: workspaces, members, agents, conversations, turns, and usage costs. Later migrations add storage for encrypted workspace credentials, proposals for suggested changes, and a small JSON “extension store” where add-ons can keep their own data.

Other migrations build the permission and runtime machinery. The grant table records who or what has permission to act, and a later change marks whether a grant is shared. Runtime instance tables let the system track running worker processes, first per workspace and later as shared fleet processes, with old fleet columns removed when no longer needed.

The final pieces improve identity and lookup. One migration marks the controlling workspace admin and main agent using existing records. Another adds a fast email lookup for sign-in. The last stores a member’s latest valid timezone. Together, these scripts create and steadily refine the shared core identity model.

### [Core conversation, turn, sandbox, and subagent migrations](stage-2.3.md) `stage-2.3` — 16 files

This stage is behind-the-scenes database preparation for the main conversation loop. It changes what the system can remember while chats, turns, sandboxes, and subagents are running. A database migration is a small upgrade to stored data, like adding new labeled drawers to a filing cabinet.

The early migrations let turns nest inside other turns, mark subagent conversations, and prevent duplicate work with “already running” or “resume queued” guards. Others attach conversations to reusable sandboxes, including a separate sandbox conversation, so work can continue in the same workspace later. Trace and context fields keep useful background, such as where a subagent came from or who sent a message.

Several migrations make relationships easier to track: child turns can be found quickly, turns and scheduled tasks can record who they act for, and subagent turns can show pending results and display names. Conversation changes store Git workspace differences. Spoken-turn indexes make voice-style lookup faster, especially by speaker. Title migrations store conversation titles and remember whether they were summarized. Finally, mid-turn replies get their own table so partial responses can be delivered reliably once.

### [Core surface, inbound message, audience, and artifact migrations](stage-2.4.md) `stage-2.4` — 15 files

This stage is part of the database’s behind-the-scenes evolution. It prepares the system to handle conversations that arrive from user-facing places like Slack and the web, and to keep reliable records of messages, files, and access. Early migrations add Slack and web as valid origins, then loosen older surface limits so more kinds of conversation entry points can fit. Workspace-aware keys prevent Slack-style identifiers from clashing across different workspaces. Turn records gain speaker details and connection-link status, while inbound message tables store incoming messages in order, prevent duplicates, and later adjust where rendered message text belongs.

Other migrations add context around conversations: who the intended audience is, what surface label users saw, and whether old Slack history is safe to migrate. Shared artifact changes give uploaded or generated files stable IDs, previews, stricter preview completeness rules, and corrected media types for Office and patch files. Finally, transcript access migrations add and then simplify an audit trail for admins reading private transcripts, keeping sensitive access traceable without unused database shortcuts.

### [Core source and page content migrations](stage-2.5.md) `stage-2.5` — 11 files

This stage is behind-the-scenes setup for the system’s long-term memory. It is made of database migrations, which are small upgrade steps that change how stored data is shaped without losing it. The first step creates the basic source and page tables, so the system can remember where content comes from, when to sync it, and which pages appear in a workspace feed. Later steps make sources more flexible by allowing new backend types, counting repeated sync errors for backoff, recording removed sources, and adding ownership so a source can be shared or tied to one member. Pages then gain better browsing details, clearer record timestamps, and stable revision numbers so feeds can be ordered reliably even when times match. One cleanup step removes an old page alert marker. Source grants add explicit read permissions for agents and backfill them for existing sources. The final cleanup removes data from a retired YC extension, including saved state, credentials, permissions, and live source/page markings. Together, these migrations keep content storage durable, searchable, permissioned, and clean as the product evolves.

### [Core scheduled task and job admission migrations](stage-2.6.md) `stage-2.6` — 10 files

This stage is behind-the-scenes database setup for the system’s scheduled work. These migrations are like renovation steps for the storage room where future jobs are kept. First, 0017 creates the scheduled task table, so the system can store what should run, when it should run, and which workspace, conversation, and agent it belongs to. 0027 adds indexes, which are like labeled shelves, so job pickers and cleanup sweeps can find matching conversations and turns quickly. 0029 reshapes turns and scheduled tasks to support scheduled pauses and resumes, and 0031 allows turns to be admitted from a “scheduled” source. Later changes refine the same model: 0037 records the last turn created by a task, 0048 adds an expiration time, 0053 makes task names unique per agent rather than per whole workspace, and 0063 adds a paused switch. 0060 adds another allowed admission source, “intent.” Finally, 0085 removes old pause fields and one-time pause tasks because pause state has moved out of the core schema.

### [Core ledger, billing, quota, and balance migrations](stage-2.7.md) `stage-2.7` — 15 files

This stage is behind-the-scenes setup for the system’s money and usage records. It is made of database migrations, which are step-by-step changes to the shape and rules of stored data. Together they let the product track what workspaces use, what it costs, and what commercial limits apply.

The early changes add spend caps, so work can be paused when a workspace, member, or agent reaches a spending rule. They also expand the ledger, the system’s accounting book, so it can record more kinds of usage such as egress, sandbox tokens, images, and videos. Other migrations make ledger rows more useful: they store price fingerprints, split prompt and cache-read token counts, allow workspace-level charges not tied to one turn, and add an index for fast lookup by workspace and time.

Another group supports reporting. Ledger export tables remember which records were sent to outside consumers, including whether “bring your own key” behavior was used. The seat migrations first add paid seat tracking, then included seats, and later remove seat limits for unlimited membership. The final balance migration adds prepaid workspace balances and purchase records.

### [Core agent, connection, egress, and memory-transition migrations](stage-2.8.md) `stage-2.8` — 13 files

This stage is part of the system’s upgrade path. It is made of database migrations, which are small steps that change stored data safely as the product evolves. Several steps reshape what an agent record can remember: internet access, reasoning mode, sandbox size, provisioning source, setup details, expected inputs and outputs, and an owning member. The binding step links old surface installations and conversations to an agent, filling existing records with a sensible default so the new rule can be required.

Another group changes how outside services are modeled. The connection migration splits old connector access into reusable connections plus per-agent permission grants. A later step moves “shared” status onto the connection itself and adds an account label. Sources are updated to point at the connection they use.

Memory storage also changes over time. One migration creates the first knowledge-graph tables for known things and their relationships; a later one removes those tables when the system moves to a single memory surface. Other steps keep operations smooth by repointing obsolete Bedrock model names and adding an egress-rule generation counter so cached network rules can be refreshed.

### [Coding, source-trigger, sweep, and pause extension migrations](stage-2.9.md) `stage-2.9` — 8 files

This stage is behind-the-scenes setup and upgrade work for several extensions that support developer workflows and automation. It is made of database migrations, which are small steps that change stored data safely as the system evolves.

The coding migrations first add places to store code review inbox items and review runs, then connect each review run to the conversation that produced it. A later step loosens the old inbox design so review sources are not tied directly to one conversation there. The final coding migration moves old review inbox records into the newer source-trigger conversation system, then removes the old review tables.

The scheduled-tasks migration adds pause records, so an agent conversation can stop and resume later. The sources migrations create a structured source_trigger table, move older subscription data into it, and add a delivery field that records how triggered work should be sent. The sweep migration adds storage for each member’s daily brief edition and progress. Together, these migrations prepare the database for reviews, triggers, pauses, and daily automation.

### [Memory, indexing, and research extension migrations](stage-2.10.md) `stage-2.10` — 15 files

This stage is behind-the-scenes setup for extensions that remember, search, and cite information. It is made of database migrations, which are small upgrade steps that create or change tables and indexes so stored data has the right shape.

The indexing migrations first create a place to store text chunks, their word-search data, and, on PostgreSQL, vector embeddings, which are number lists used to search by meaning. They then make each chunk belong to a workspace so separate workspaces do not clash.

The memory migrations build the long-term memory store. They create tables for facts and memory pages, add memory type, confidence, workspace ownership, and “as of” time, then add fast lookup indexes for cleanup and inventory browsing. Later steps connect facts more clearly to their source pages, copy missing page time into facts, track exact page revisions, support room-based audiences, and allow one fact to point to multiple source pages.

The research migration adds a table of observed web sources, so later answers can be traced back to what the system actually saw.

### [User-facing feature extension migrations](stage-2.11.md) `stage-2.11` — 12 files

This stage is behind-the-scenes support for optional, user-visible features. It is made of database migrations: small upgrade scripts that change stored data when the system is installed or updated, and usually know how to undo the change if rolled back. Together, they give extensions their own shelves in the database without mixing everything into the core schema.

The eval environment migration adds fake inbox and calendar tables per workspace. Monitors get a table for scheduled watchers that remember what to check and their progress. Objectives add tables for goals, ordered steps, evidence, checks, and a later flag saying whether a step can run on its own. The sample extension adds a simple per-workspace note table. Sites migrations create hosted-site records, add a generation ID to separate versions, and link a site homepage to one agent with a safety rule against duplicates. Skill creation migrations store user-made skills, then move them under specific agents. Web migrations backfill older chat rows and move chat titles into the shared conversation table so the main UI can find them reliably.

## [Hosted control plane startup and workspace onboarding](stage-3.md) `stage-3` — 11 files

This stage is the front door for hosted UFO onboarding. It runs when the control service starts and then supports the sign-up and sign-in flow for new users. The gateway builds the public web server, where a person installs the client, proves a work email, joins or creates a workspace, and receives a login token. The directive and web layers translate the same onboarding steps for two surfaces: the terminal client gets small text instructions, while the browser gets JSON for the sign-in page.

Behind the scenes, claim handling creates short-lived proof records for email ownership, stores them safely in Postgres, and checks them through WorkOS or a local development substitute. The invite logic keeps a one-use, time-limited invitation ledger for new company domains. The email helper rejects personal-looking addresses, builds invite messages, and sends them through Amazon SES or logs them locally. Shared workspace logic turns a verified address into membership or a new domain workspace. Token code issues the hosted login token. Slack Connect setup then creates a retry-safe customer channel and sends the first invite.

## [Runtime configuration, extension discovery, and capability registration](stage-4.md) `stage-4` — 62 files

This stage happens mostly at startup. It decides what the running system is allowed to use, then builds a shared catalog of those abilities. The main configuration file, ufo.toml, is checked by config.py so missing or unsafe settings stop the app early. The extension loader then finds approved extensions, verifies them, and registers what they offer. extension_kind.py makes those loaded extensions visible as read-only objects, so users can inspect them without changing the deploy.

The registered pieces form several shelves. One shelf lists model, embedding, search, memory, sandbox, and Redis backends. Another loads agent skills, helper subagents, and workflow profiles. User-facing manifests add browser, coding, documents, objectives, sites, web, shell, and debugger surfaces. Connector manifests plug in services such as Slack, Gmail, Stripe, Composio, Pipedream, and API-key based providers. Background manifests schedule monitors, delayed tasks, and self-improvement checks. Finally, package marker files make extension folders importable. Together, these parts turn installed code into a clear menu of safe, usable capabilities.

### [Model, embedding, search, and sandbox backend registration](stage-4.1.md) `stage-4.1` — 11 files

This stage is part of startup. It prepares the “phone book” the rest of the system uses to find outside services safely and consistently. The models package marker makes model code importable. The model spec defines the standard record for an AI model: provider, cost, key source, context size, and special behavior. The core catalog fills that record for built-in Anthropic and OpenAI models, while the Bedrock extension adds Amazon Bedrock models. The registry then combines these entries into one master lookup table.

The same pattern is used for search and memory. The search interface defines how UFO asks for web results or page text, and the Exa extension supplies one real search provider. The OpenAI embedding extension turns text into number lists, called embeddings, so similar meanings can be compared. Turbopuffer stores and searches those embeddings as a memory index.

For safe execution and live connections, the sandbox selector chooses a valid code-running backend, and the Redis manifest registers Redis-based shared communication transports.

### [Agent skills, subagents, and workflow profiles](stage-4.2.md) `stage-4.2` — 12 files

This stage is the system’s “capability shelf.” It is mostly behind-the-scenes setup used before and during the main work loop, so an agent knows what special skills it can use and what child agents it can call for focused jobs. The skill runtime defines what a skill is, reads skill folders, works out dependencies, and copies needed files into the safe workspace where the agent runs. The model catalog skill is built from the live model registry, so users can ask which AI models are really available. The skills package file simply makes these modules importable.

Several files describe subagent profiles, which are recipes for smaller helper agents. There is a fallback general-purpose subagent, plus specialized helpers for browser use, writing, research, and website building. Each profile sets the helper’s instructions, allowed tools, input, output, model, and limits. Extension manifests announce extra bundles, such as the brief pipeline and research tools. The skill creation extension adds user-made skills, while its store safely saves, loads, updates, and deletes them without overwriting built-ins or exceeding limits.

### [User-facing extension capability manifests](stage-4.3.md) `stage-4.3` — 8 files

This stage is part of the system’s startup and discovery work. Each manifest is like a labeled plug on a tool: it tells the UFO host what the extension is called, what it adds, and how the rest of the system may safely use it. The browser manifest registers a browser helper agent, delegation tools, and instructions for when browsing should be handed off. The coding manifest adds coding and review agents, a coding skill, GitHub access details, and a setup tool. The debugger and UFO manifests are smaller cards that expose public web or shell surfaces, meaning places the user interface can connect to. The documents manifest adds writing and document skills plus a specialist writing agent. The objectives manifest supports longer-running goals by storing the current objective and reminding the agent what remains unfinished. The sites manifest wires in website-building tools, prompts, skills, object types, screens, and a site-focused agent. The web manifest exposes portal routes, permissions, background jobs, and conversation storage. Together, these files make user-facing capabilities visible and loadable.

### [Connector, source, and integration provider manifests](stage-4.4.md) `stage-4.4` — 7 files

This stage is shared setup support. It does not do the user’s main task itself. Instead, it tells the host runtime what outside services are available and how to plug them in, like labels and sockets on a power strip.

The Composio manifest advertises Composio connectors, account connection steps, command-line credential forwarding, and the web address used when OAuth sign-in returns from the browser. The general connectors manifest names the connectors extension, its version, its tools, shared objects, and the guidance text shown to the assistant. The keyed connectors file covers simpler services that use API keys, such as Datadog, by defining where keys are stored, how they are safely added to requests, and what users should do.

The Pipedream manifest declares Pipedream-backed connectors and their OAuth start and return routes. The Slack manifest gathers Slack routes, credentials, tools, hooks, and assistant instructions. The sources manifest advertises source backends, credential slots, hooks, objects, an authentication proxy, and a background job. Finally, the sources registry maps names like Slack, Gmail, or Stripe to the actual connector code the system can run.

### [Scheduled and background-work extension manifests](stage-4.5.md) `stage-4.5` — 3 files

This stage is shared behind-the-scenes support. It does not do the user-facing work itself. Instead, it provides “manifests,” which are registration sheets that tell the main application which background features exist and when they should run. Without these files, the code for the features might be present, but the system would not know to load it or schedule it.

The monitors manifest registers a monitor tool, a monitor object type, and a recurring job that checks whether any monitors are due. It is like adding a watchdog to the system and giving it a timetable.

The scheduled-tasks manifest registers the pieces needed for delayed or repeated user tasks: the scheduled-task object type, a pause-and-wait tool, two background jobs, a skill package, and a storage slot for conversation state. These parts let the system remember future work and wake up to continue it.

The self-improvement manifest registers a daily evaluation job. This job reviews system behavior using the main deployed model, so the evaluation matches real production behavior.

### [Extension package import markers](stage-4.6.md) `stage-4.6` — 18 files

This stage is quiet behind-the-scenes support. It does not start tools, run the main work, or shut anything down. Instead, these __init__.py files act like nameplates on folders. In Python, that nameplate tells the system “this folder is a package,” meaning other code can import and use files inside it.

Most files simply make an extension importable: documents, pipedream, redis hub, repl, research, scheduled tasks, sites, Slack, sources, sweep, ufo, and web. The nested document script markers do the same for document-review, PowerPoint, and Excel skill script folders, so their helper scripts can be reached in the normal Python way. The browser marker is the front door for browser-related tools, including sandbox browser or computer-use features and a browser subagent profile. The self-improvement marker also records that its package reviews past workspace activity offline and suggests prompt changes for human approval. The skill-create marker notes support for agent-authored skills and skills available during each runtime turn. Together, these small files make the extension shelves visible before the real tools are loaded.

## [HTTP server startup, route mounting, and surface ingress](stage-5.md) `stage-5` — 15 files

This stage is where the running server opens its front doors. During startup it mounts the routes that people and outside services use, then during normal operation it checks each request, works out who is calling, and turns it into safe actions inside the system.

The web portal and panel API are the browser entrance. They serve the app, confirm the signed-in member, accept chat messages, stream replies, and provide panel data such as files, memory, usage, skills, and admin settings. Slack, shell, debugger, and OAuth routes are other entrances: Slack messages become conversation turns, the command-line client gets simple text events, operators can inspect read-only debug data, and OAuth callbacks finish account linking.

The shared bridge in `core/src/ufo/ext/surface.py` is like the reception desk behind all these doors. It helps trusted surfaces identify members, create conversations, submit messages, read portal data, and send replies back out. `core/src/ufo/ingress_serve.py` handles hosted sandbox websites by turning signed links into temporary browser sessions and forwarding web traffic safely. `surfaces/__init__.py` simply makes the surfaces folder importable.

### [Web portal and panel API ingress](stage-5.1.md) `stage-5.1` — 5 files

This stage is the front door for the web version of the system. It is part of the live user-facing loop: loading the browser app, checking who the user is, accepting chat messages, streaming answers back, and showing workspace panels.

The main web surface is the central reception desk. It serves the web app, verifies signed-in users, exposes chat routes, streams agent replies, and provides data for panels such as memory, files, usage, skills, and admin views. The audience rules act like the door policy: they decide which members may see or use which agents, and give admins tools to grant or remove access.

The panels bridge turns settings forms into the same normal action path used by chat, so changes to agents, members, credentials, and access do not need separate custom endpoints. The memory surface adds a read-only memory explorer for authorized operators. The community directory reader lets the app browse public skills, while guarding against slow, oversized, broken, or rate-limited responses. Together, these pieces make the web portal usable, controlled, and safe.

### [Slack, shell, debugger, and OAuth callback ingress](stage-5.2.md) `stage-5.2` — 7 files

This stage is the set of “front doors” where outside tools talk to the system during normal use and account setup. Slack’s surface receives Slack web requests, checks that they really came from Slack, turns messages and button clicks into conversation turns, and sends replies, progress updates, and files back into Slack threads. The Slack mention helpers translate Slack’s hidden user codes into readable names, then restore real mentions when replying. The attribution and hook files add a safe footer to connector-sent Slack messages so people can see which bot sent them, while making sure that footer is not mistaken for a new message to the bot.

The shell surface serves the command-line client. It converts server conversation events into simple text commands that a shell script can display or act on.

The debugger surface is a read-only window for operators. It shows stored conversations, turns, files, transcripts, and live activity for one workspace.

The OAuth callback finishes account-linking after an external provider sends the user back, records the grant, and redirects them to chat.

## [Workspace, member, agent, conversation, and object resolution](stage-6.md) `stage-6` — 7 files

After a request has proved who it comes from, this stage gathers the long-lived workspace facts that the rest of the system will rely on. It is like opening the right filing cabinet before doing any work. The code resolves who belongs to the workspace, what agents exist, what conversations and objects can be seen, and which rules apply.

The member and workspace files describe the roster: who is a member, who is an admin, who has a seat, and how admins can add someone. The agents file manages editable workspace agents, while provisioning brings in default agents from installed extensions without overwriting local changes. The conversations file exposes old conversations as read-only records and can turn an allowed transcript into workspace material. The objects file is the central gate for durable named items owned by extensions: it checks names, data, permissions, and sends each request to the right owner. The object scope file tracks which agent should get credit while object work is happening. Together, these pieces build the safe request-time map used for routing and authorization.

## [Turn admission, cancellation, and live subscription setup](stage-7.md) `stage-7` — 8 files

This stage is the front door for work before the main agent loop begins, and it also handles “stop” requests while work is running. Every new member message, scheduled wake-up, and internal call enters through the admission path. admission.py checks permissions, finds or creates the right conversation, decides whether to join an existing turn or queue a new one, and prepares the work for execution.

ambient_reply.py is a small gatekeeper for group threads. If someone speaks without directly calling the agent, it decides whether the agent should answer or stay quiet, avoiding wasted turns when people are just talking to each other.

While a turn runs, the live frame and terminal transport stage acts like a viewing window. It sends partial answers, tool updates, terminal data, and final status to listeners, and lets them reconnect without missing the ending.

If a member presses stop, stop.py checks that the request is allowed, uses cancellation.py to safely tell the outside workflow system to halt, records the cancellation, notifies listeners, and can start a follow-up turn if needed.

### [Live frame and terminal transport](stage-7.1.md) `stage-7.1` — 4 files

This stage is the live delivery system for a running turn. After a turn is accepted, the model and tools produce many small updates before the final answer is ready: partial text, tool progress, terminal output, cost changes, parked states, and completion messages. The hub code is the local message room for these updates. It lets user interfaces watch the turn as it happens and reconnect from a saved position, called a cursor, instead of losing their place.

The tail helper makes this reliable for late or unlucky listeners. It follows the live stream, but also checks the database so a client still learns that a turn finished or was parked even if the last live message was missed.

When the system runs across several server pods, the Redis hub version acts like a shared pipe between them, carrying the same live frame updates through Redis Streams. The Redis terminal transport does a similar job for terminal sessions: it lets a terminal connection and the worker that needs it find each other across pods, sending small control messages through Redis and larger byte data through blob storage.

## [Turn queue claiming, recovery, and context assembly](stage-8.md) `stage-8` — 7 files

This stage is the main doorway into doing one unit of conversation work, called a turn. It makes sure only one turn runs at a time for the same conversation, so replies do not collide. It also makes the work durable, meaning it is recorded safely enough that a crash can be noticed and repaired later.

The queue is the gatekeeper. It finds a runnable turn, claims it, restores failed or interrupted work, opens the right sandbox, and makes sure the turn ends with either a result or a saved failure. The engine is the traffic controller. After a turn is claimed, it gathers the conversation history, user and member permissions, tools, skills, memory, goals, model choice, and spending limits. It then runs the model, streams updates, executes tools, records costs, and saves the final state.

Before the model call, prompt construction packs the instructions and conversation history into a size the model can read. Context compaction summarizes older material when needed. Subagent and objective injection adds the current helper catalog and ongoing goals, so the agent can delegate work and continue unfinished plans.

### [Prompt construction and context compaction](stage-8.1.md) `stage-8.1` — 3 files

This stage happens just before the AI model is called. Its job is to prepare the instructions and conversation history so the model gets the right guidance without being overloaded. Think of it as packing a suitcase: the most important current items stay in full, older items may be folded into a smaller summary, and everything must still be checkable later.

The prompt rendering code builds the final system prompt from templates. A template is text with blanks to fill in, like a form letter. It fills the required slots, checks that no unfinished placeholders remain, and creates a fingerprint, which is a stable ID based on the prompt content so changes can be noticed and traced.

The compaction code watches the conversation length. If the history is too large for the model’s context window, meaning the amount of text the model can read at once, it keeps recent messages unchanged and summarizes older ones. It also records both the full and compacted histories for inspection. The package marker simply makes the prompt code importable.

### [Subagent and objective context injection](stage-8.2.md) `stage-8.2` — 2 files

This stage happens during the main turn loop, before and while the agent does its work. Its job is to give the agent an up-to-date view of what help it can request, and what long-running goals are already in progress. Think of it like resetting the agent’s desk at the start of each turn: the right helper list is placed beside the current checklist.

`spawn_catalog.py` builds that helper list, called a spawn catalog. It is a short instruction page showing which subagents or agent profiles may be started, and what information each one needs. Because workspaces and rules can change, the catalog is rebuilt every turn instead of being reused blindly.

`subagents.py` is the machinery that actually starts and manages those helpers. It can launch a smaller named helper or a full child agent, check that requests and results are valid, cancel work when needed, follow up, and return results safely. Together with injected objective state, this lets the agent delegate, see unfinished steps, record progress, and test whether the goal is done.

## [Model request routing and streamed response normalization](stage-9.md) `stage-9` — 3 files

This stage is part of the main work loop, after the system has prepared a request for a language model and chosen where to send it. Its job is like using different power adapters: each provider has its own plug shape, but the rest of UFO expects one standard kind of output.

The Anthropic bridge sends requests to Anthropic’s Messages API, then converts Anthropic’s live streaming reply into UFO’s common events, such as text, reasoning, tool use, usage numbers, and errors. It also retries carefully when Anthropic fails before showing any user-visible output.

The OpenAI bridge does the same for OpenAI-style services. It can use either Chat Completions or Responses APIs, then normalizes their streamed chunks into the same UFO event stream.

The OpenRouter extension registers OpenRouter as another provider. It also adds image and video generation tools, while keeping produced files and costs tracked in the workspace. Together, these adapters let the engine swap providers without changing how the rest of the system reads model results.

## [Tool dispatch, sandboxed execution, and workspace side effects](stage-10.md) `stage-10` — 64 files

This stage is where the agent’s requested actions become real work. During the main conversation loop, the model may ask to use a tool, such as reading a file, running a command, searching the web, or opening a browser. The tool registry is the catalog: it defines each tool, its allowed inputs, and how to find it by name. The tool context is the safety wrapper: it tells the tool what workspace, credentials, accounts, cleanup steps, and helper agents it may use.

The built-in tools connect model requests to the project’s safe workspace, file store, database, user prompts, secrets, account connections, and subagents. Research tools add web search and page fetching through configured search services.

Around these tools are larger workshops. The sandbox stage runs commands, limits file access, tracks changes, and controls network access. The document stage repairs, reads, annotates, and rebuilds office files and PDFs. The browser stage drives real or hosted browsers, reads pages, and performs clicks, typing, downloads, and screenshots safely. Together, these parts turn tool calls into controlled side effects and return results to the conversation.

### [Sandbox workspace, command execution, file access, and egress proxying](stage-10.1.md) `stage-10.1` — 14 files

This stage is the system’s workbench. During a conversation, it gives the agent one private workspace, keeps returning to it, and controls how commands, files, web access, and preview sites work. The conversation and session code are the front door: they find the right workspace, limit file access to `/workspace`, and expose safe actions like run, read, and write. Local, terminal, Docker, and E2B carriers are different “engines” for the same workbench: a host folder, the user’s terminal, a container, or a cloud sandbox.

Command work is made durable by task helpers, which save long-running output and results so later calls can reconnect instead of rerun. Workspace change tracking records what files changed, while file path limits keep stored change data bounded. The REPL extension adds persistent Python and JavaScript scratchpads.

Network access is guarded by the egress proxy. The proxy entrypoint runs the shared service, the proxy server approves destinations, injects only allowed credentials, and records usage. Cache settings allow controlled downloads, and ingress host labels safely route browser traffic to one sandbox port.

### [Document, office, PDF, and skill helper execution](stage-10.2.md) `stage-10.2` — 19 files

This stage is a shared workshop for document jobs inside the sandbox. It is not the main chat loop itself. Instead, agents or manual workflows call these helpers when they need to inspect, fix, mark up, or rebuild office files and PDFs.

For review work, the annotation tools keep a simple saved record of findings, then write those findings back as visible comments in PDFs, PowerPoint files, and spreadsheets. For Word files, the DOCX helpers treat the file like a zipped box of structured text: they unpack it, optionally accept tracked changes, add comments, and pack it again. The PowerPoint helpers do the same kind of unpack-and-repack work for PPTX files, with extra tools to add slides, make preview sheets, and repair common package problems. The Excel helpers use LibreOffice in the background to reopen spreadsheets, recalculate formulas, save fresh results, and report obvious errors. The PDF tools render pages as images, inspect page layout, and fill real PDF form fields. Together, these parts act like a document repair bench.

#### [Document review state and annotation scripts](stage-10.2.1.md) `stage-10.2.1` — 6 files

This stage is the notebook and markup desk for the document review workflow. It is shared support used while the review is running and when results are written back to user files. First, constants.py keeps common file names, like the review state file and log file, in one place so all tools point to the same records. models.py defines what a review issue looks like, such as its location, message, and severity, and can turn it into a clear human comment. manage_state.py is the progress keeper. It updates a JSON state file, which is a simple structured text file, with sections, claims, issues, and the final summary. The annotation scripts then use that saved state. annotate_pdf.py marks matching text in a PDF and adds note-style comments. annotate_pptx.py inserts the findings as PowerPoint comments. annotate_xlsx.py copies a spreadsheet and adds the findings as Excel cell comments. Together, these files preserve review memory and turn findings into visible feedback.

#### [Word DOCX package and comment helpers](stage-10.2.2.md) `stage-10.2.2` — 4 files

This stage is a set of command-line tools for working on Microsoft Word DOCX files behind the scenes. A DOCX file is really a zipped package of XML files, where XML is structured text that describes the document. These helpers let an automated workflow open that package, change it safely, and build it again.

The flow starts with unpack.py, which unzips the DOCX into a folder and cleans up the main document XML so it is easier for tools or people to read and edit. If the document has tracked changes, accept_changes.py can first make a clean version by asking LibreOffice to accept all changes without showing any window, which is useful on servers. Once the package is unpacked, comment.py can insert a new Word comment or a reply by writing the special XML entries and links Word needs. Finally, pack.py zips the folder back into a valid DOCX and trims unnecessary XML spacing. Together, they act like a careful unpack-edit-repack workbench for Word documents.

#### [PowerPoint PPTX package repair and slide helpers](stage-10.2.3.md) `stage-10.2.3` — 4 files

This stage is shared behind-the-scenes support for working with PowerPoint files without using PowerPoint itself. A .pptx file is really a zipped package: a bundle of folders, XML files, images, and links that together describe the slides. These tools open that package, make safe changes, fix common problems, and close it again.

unpack.py is the “opening” tool. It turns a .pptx into a normal folder and formats the XML so people and programs can inspect it more easily. slides.py is the workbench. It can clean the unpacked folder, add a new slide, and create thumbnail contact sheets so changes can be checked visually. repair.py is the mechanic for files made by pptxgenjs, a library that creates PowerPoint documents. It fixes known packaging and text issues that may cause PowerPoint to warn, repair, or alter the file. pack.py is the “closing” tool. It rebuilds the folder into a valid .pptx and removes unnecessary XML spacing while preserving real slide text.

#### [Excel XLSX recalculation and LibreOffice helpers](stage-10.2.4.md) `stage-10.2.4` — 2 files

This stage is a behind-the-scenes spreadsheet repair and checking step. It is used when the system needs an Excel XLSX file to have fresh formula results, especially after data has changed or when the saved results inside the file may be out of date.

The small _soffice.py helper is the “launcher.” It starts LibreOffice in headless mode, meaning LibreOffice runs without showing a normal desktop window. That lets scripts use LibreOffice like a tool in the background. It also knows where LibreOffice keeps user macros on Linux and macOS, so other scripts can find the right support files if needed.

The recalc.py script is the main worker. It opens the spreadsheet through LibreOffice, tells it to recalculate every formula, saves the updated XLSX file, and then looks for visible spreadsheet error values such as #REF! or #DIV/0!. Together, these files act like an automatic spreadsheet technician: open the workbook, refresh the math, save the result, and report obvious formula problems.

#### [PDF rendering, layout, and form filling tools](stage-10.2.5.md) `stage-10.2.5` — 3 files

This stage provides small command-line tools for working with PDFs when the system needs to see, inspect, or modify document pages. It is not part of startup or shutdown. It is behind-the-scenes support used during document processing, especially when a PDF must be previewed, analyzed, or filled in.

The render tool turns each PDF page into a PNG image. This is like taking a clear photo of every page, so other parts of the system can preview it or use image-based checks.

The layout tool helps with PDFs that are just flat pages, with no real fillable boxes inside them. It inspects the visual layout, shows where form-like fields might be placed, and can write text annotations onto the page. This makes a plain document easier to understand and safely mark up.

The formfill tool works with PDFs that already contain native form fields. It can detect those fields, export their names and values as JSON, and fill them back in from JSON data. Together, these tools cover both image-style PDFs and true fillable forms.

### [Browser automation and remote browser sessions](stage-10.3.md) `stage-10.3` — 26 files

This stage is the system’s browser-driving workshop. It is used during the main work loop whenever an agent needs to use the web, and it also cleans up when that browser-using turn ends. First, the provider layer gives the agent a browser to work with. That browser might be a Chrome running in the project’s sandbox, a hosted Browserbase session, Browser Use’s cloud agent, or the built-in automation stack. All of them expose Chrome DevTools, a remote-control channel for Chrome.

The session core keeps that connection alive, sends commands, receives browser events, and reports clear errors. Around it, lifecycle helpers manage practical browser chores: tabs, page loads, downloads, pop-up dialogs, and deciding when a page is “settled” enough to continue. Page-reading helpers turn the live page into readable text and element descriptions, then map AI-chosen targets back to real screen coordinates. Finally, the action layer defines safe browser verbs like click, type, scroll, wait, screenshot, key press, and upload, cleans them up, and executes them in Chrome.

#### [Browser providers and agent-facing adapters](stage-10.3.1.md) `stage-10.3.1` — 7 files

This stage is the bridge between agents and web browsers. It is shared support used during the main work loop whenever an agent needs to visit a site, read a page, click buttons, type text, upload files, or collect downloads.

At the center is `core/src/ufo/browser.py`, which defines a simple promise: “give me a Chrome DevTools connection for this turn.” Chrome DevTools is the control channel that lets software drive Chrome. The core code does not care where Chrome comes from.

Different providers fulfill that promise. `ufo_ext_sandbox_chrome.py` starts or reuses a real Chrome inside the conversation’s sandbox and exposes a safe connection to it. `ufo_ext_browserbase.py` instead creates a hosted Browserbase Chrome session, moves files in and out, and cleans it up afterward. `ufo_ext_browser_use.py` sends whole browsing tasks to Browser Use’s cloud agent.

On top, `backend.py` manages the per-turn browser object and opens connections only when needed. `tools.py` turns browser actions into agent-callable tools. `delegation.py` lets the main agent hand browsing to one or many separate browser agents.

#### [CDP connection and browser session core](stage-10.3.2.md) `stage-10.3.2` — 6 files

This stage is the core of the built-in browser automation engine. It sits behind the scenes during the main work loop, keeping a live Chrome browser session open and turning high-level browser requests into real browser control messages.

The main coordinator is session.py. It represents one active browser session: it opens Chrome’s control channel, keeps track of tabs, page loading, downloads, and pop-up dialogs, and sends each requested action to the right helper. cdp.py is the wire to Chrome. It uses Chrome’s DevTools Protocol, a remote-control interface for the browser, over a WebSocket, which is a two-way network connection. It sends commands, waits for answers, and routes browser events to whoever is listening.

runtime.py is the JavaScript bridge. It runs small scripts inside the current page, converts browser replies into normal Python values, and reports page-side script failures clearly. wire.py defines and checks the expected message shapes, like a customs desk checking paperwork before messages enter the system. errors.py defines a clear automation-specific error for impossible AI-supplied browser references. __init__.py simply makes this folder importable as a package.

#### [Browser lifecycle, tabs, downloads, dialogs, and settling](stage-10.3.3.md) `stage-10.3.3` — 4 files

This stage is shared support for the browser automation loop. After the system asks the browser to do something, these helpers keep the browser safe, usable, and in sync before the next step begins. The tabs helper is like the browser’s map. It knows which tabs are open, which one is active, and how to open, close, switch, or navigate them while listening for real browser changes. The dialogs helper watches for JavaScript pop-ups, which are small browser message boxes that can block all work until answered. It records them, accepts simple required ones, and dismisses ones that might confirm a risky action. The downloads helper notices when a file download starts, waits for it to finish, and removes blocks that would stop it. It also turns PDF pages into real downloads when Chrome would otherwise hide them in its built-in viewer. The settle helper decides when a page has reacted enough to continue, without waiting on unimportant background noise like ads or tracking requests.

#### [Page reading, element lookup, and coordinate mapping](stage-10.3.4.md) `stage-10.3.4` — 4 files

This stage is behind-the-scenes support for the main browser work loop. Its job is to turn a live web page into something the AI can understand, then turn the AI’s choices back into real places on the screen.

The page reader is the main bridge. It takes the current browser page and produces readable text, including a simplified “accessibility tree,” which is a screen-reader-style outline of buttons, links, fields, and other page parts. The content service wraps this lower-level page reading so higher-level commands can ask simple questions like “what is on the page?” or “where is this element?”

The find logic searches that text outline and converts possible hits into structured, safer matches the rest of the system can use. This helps avoid treating raw text as if it were a real page object.

The coordinate mapper completes the loop. Screenshots may be resized before a vision model sees them, so it translates model coordinates back to the browser’s real pixel positions, keeping clicks and typing aimed correctly.

#### [Browser action vocabulary and input execution](stage-10.3.5.md) `stage-10.3.5` — 5 files

This stage is the action layer for the browser. It sits in the main work loop, after the assistant has decided what it wants to do and before Chrome actually does it. The actions file defines the allowed “verbs” of this small language, such as click, type, scroll, wait, screenshot, and key press. It also says what details each verb must include, so bad requests can be caught early.

Before an action reaches the browser, fixup cleans it up. For example, it can add a missing wait time or make sure a text field is focused before typing. Computer is the main bridge that takes these cleaned, high-level instructions and turns them into real browser events on the active tab.

Some actions need special handling. Forms fills page fields and handles file uploads through Chrome’s debugging protocol, which is a control channel for driving the browser. Keys translates human keyboard ideas like “Ctrl+A” or normal text into the exact key-down and key-up messages Chrome expects, while remembering which keys are being held.

## [Credentialed connectors and external provider actions](stage-11.md) `stage-11` — 18 files

This stage is shared behind-the-scenes support for letting agents use outside services without handing them private keys. It comes into play when an agent needs Gmail, Slack, GitHub, Notion, or another provider during its normal work. The core connector contract defines how tools are found, called, and protected, while agent setup notices which agents still need a member to connect accounts and gives them wording to ask for that access.

The generic connector tools are the workbench: they search available tools, run them, move files in and out, and keep results small enough for chat. Connector objects make connected accounts visible as workspace items, with rules for who may share, revoke, or disconnect them.

Composio and Pipedream are two outside broker bridges. Their clients create consent links, check accounts, list actions, run actions, fetch files, and proxy web requests so real service tokens stay hidden. Their providers connect UFO’s account-linking flow to hosted approval pages. MCP support adds tools from workspace-configured MCP servers. Slack tools provide guided Slack setup and search. The package files simply make these modules importable.

## [Source synchronization, page ingestion, indexing, and retrieval](stage-12.md) `stage-12` — 66 files

This stage is the system’s intake and search pipeline. It runs mostly in the main background work loop, after a user connects an outside account or registers a source. First, connected.py, tools.py, and triggers.py create source feeds, let users request syncing, and record which conversations should wake up when shared content changes. The connector layer defines the common rules for all outside readers, including cursors, which are saved bookmarks that let a sync resume where it stopped.

Provider-specific connectors then talk to services like Google, Slack, GitHub, or Jira and turn their different API results into one standard record shape. backend.py adapts that stream into normal source runs, while sync.py stores current page bodies, metadata, deletions, and change events. pages.py lets users view those synced pages safely as read-only objects.

After pages arrive, indexing.py cuts long text into smaller chunks and prepares them for search. The default index stores and searches those chunks, while memory extraction can turn page changes into durable facts for later recall. Together, these parts move content from external systems into searchable pages, indexes, and agent memory.

### [Provider-specific source connectors](stage-12.1.md) `stage-12.1` — 49 files

This stage is the system’s library of source connectors. It runs during the main data sync work, after the system knows which outside service it should read from. A connector is an adapter that signs in to a provider’s API, asks for allowed data, follows page-by-page results, and reshapes each item into the project’s standard record format.

The shared REST foundation provides the common plumbing for many web APIs: authentication, retries after temporary failures, pagination, and cursors that remember where a sync left off. On top of that base, provider-specific groups handle different worlds of work. Google Workspace connectors read mail, calendars, files, documents, spreadsheets, and meeting notes. Collaboration connectors bring in knowledge from tools like Slack, Teams, Notion, Outlook, and Confluence. Engineering connectors cover GitHub, Jira, Linear, PagerDuty, and Sentry. Other groups read work management, CRM, support, marketing, HR, recruiting, finance, billing, accounting, and commerce systems. Together, these parts turn many different SaaS products into one reliable stream of searchable records.

#### [Shared REST source connector foundation](stage-12.1.1.md) `stage-12.1.1` — 1 files

This stage is shared behind-the-scenes support for connectors that read from REST APIs. A REST API is a web service that returns data when the program sends HTTP requests, like asking a website for a specific page of information. Provider-specific connectors can focus on the details of one service, while this foundation handles the repeated plumbing.

The file `core/src/ufo/sources/rest.py` is the common engine for that work. It builds and sends authenticated requests, meaning it includes the right proof of identity, such as tokens or keys, so the remote service accepts the call. If a request fails because of a temporary problem, such as a busy server or network hiccup, it retries instead of giving up immediately. It also walks through paginated results, where an API returns data in pages rather than all at once, and supports cursor-style progress markers so reading can continue from the right place. Together, these pieces act like a reusable transport and paging machine for many REST-based source connectors.

#### [Google Workspace source connectors](stage-12.1.2.md) `stage-12.1.2` — 6 files

This stage is a group of Google Workspace connectors. It is shared behind-the-scenes support for the system’s syncing work: it reaches into Google services, reads what the connected account is allowed to see, and turns that data into standard records the rest of the system can search and recall.

Each file covers one Google product. The Gmail connector reads mailboxes, converts messages into clear text, and keeps track of new and deleted emails so it does not start from scratch every time. The Calendar connector reads the primary calendar, saves events, and also records each attendee separately so the system can understand who was invited. The Docs connector finds documents through Drive, fetches their contents, and flattens Google’s nested document structure into plain text. The Drive connector reads files, shared drives, permissions, comments, and revision history, packaging Google’s page-by-page API results into the project’s normal stream format. The Sheets connector reads spreadsheets, tabs, and cell values. The Meet connector turns meeting transcripts and generated notes into searchable pages. Together, they make Google work data available to the larger recall system.

#### [Collaboration, messaging, and knowledge source connectors](stage-12.1.3.md) `stage-12.1.3` — 5 files

This stage is shared behind-the-scenes support for bringing outside team knowledge into the system. Its connectors act like careful librarians: they visit approved workplace tools, read what the user has access to, and translate it into plain records that the rest of the product can store, search, and recall.

The Confluence connector reads Atlassian spaces, pages, blog posts, comments, groups, and audit records, turning wiki-style content into searchable text. The Microsoft Teams connector uses Microsoft Graph, Microsoft’s standard web doorway for Microsoft 365 data, to collect teams, channels, chats, and messages. The Notion connector reads users, pages, databases, comments, and page blocks, but never changes anything in Notion. The Outlook connector also uses Microsoft Graph to read email, conversations, contacts, calendars, and folders, and remembers its last stopping point so future syncs only fetch changes. The Slack connector reads workspace users, channels, messages, threads, and senders. Together, these files feed the system’s memory without posting, editing, or deleting in the original tools.

#### [Engineering, issue tracking, and incident source connectors](stage-12.1.4.md) `stage-12.1.4` — 5 files

This stage is the system’s set of “source connectors” for engineering work. A connector is a small translator that logs in to an outside service, reads its data through that service’s API, and turns it into the common record format the rest of the system can store, sync, and search. It is mostly behind-the-scenes support for the main syncing work.

The GitHub connector discovers accessible organizations and repositories, then reads code-related activity such as issues, commits, comments, users, teams, and releases. The Jira connector reads Jira Cloud sites, including projects, issues, comments, users, boards, and sprints. The Linear connector reads similar planning and tracking data from Linear, using its GraphQL API, which is a structured way to ask for exactly the needed fields. The PagerDuty connector brings in incident-response data such as services, incidents, notes, schedules, and on-call records. The Sentry connector reads error-monitoring information such as projects, issues, events, members, and releases. Together, these connectors let the system build one searchable memory of engineering work across many tools.

#### [Work management, scheduling, and forms source connectors](stage-12.1.5.md) `stage-12.1.5` — 7 files

This stage is behind-the-scenes support for syncing work information from outside tools into the system. Each connector acts like an adapter plug: it knows how one service organizes its data, asks that service for the data through its API, and reshapes the results into records the rest of the system can store, search, and display as readable pages.

Airtable reads bases, tables, and table records from structured workspaces. Asana reads workspaces, projects, tasks, stories, users, and related metadata, including results that arrive in pages. ClickUp walks through its nested structure of teams, spaces, folders, lists, tasks, comments, fields, and goals. monday.com imports users, teams, workspaces, boards, items, updates, logs, and tags. Wrike brings in contacts, folders, tasks, comments, workflows, and custom fields. Calendly focuses on scheduling data such as users, event types, groups, scheduled events, and invitees. Typeform reads forms, responses, workspaces, themes, images, and webhook settings. Together, these files turn many different work tools into one steady stream of searchable system records.

#### [CRM, sales, and customer support source connectors](stage-12.1.6.md) `stage-12.1.6` — 6 files

This stage is part of the system’s data intake work. It connects to outside customer and support tools, reads their records through each service’s API, and reshapes them into a common form the rest of the product can store, search, and analyze. An API is a doorway a service provides so software can ask for data in an organized way.

Each file is like an adapter for a different machine. The Attio, HubSpot, and Salesforce connectors focus on customer relationship and sales data, such as companies, people, accounts, contacts, deals, tasks, notes, and deletion markers. HubSpot also covers marketing, analytics, associations between records, and product-specific data. The Freshdesk, Intercom, and Zendesk connectors focus on support data, including tickets, conversations, users, companies, agents or admins, help articles, forums, tags, and related setup records. Together, they hide the differences between these services’ paging and record formats, producing steady streams of clean records for the shared sync pipeline.

#### [Marketing, advertising, and social source connectors](stage-12.1.7.md) `stage-12.1.7` — 6 files

This stage is part of the system’s data-gathering work. Its job is to connect to outside marketing and advertising services, ask them for business data through their APIs, and reshape the replies into standard records the rest of the project can store, search, and reuse. An API is simply a service’s official doorway for software to request data.

Each file is like an adapter for a different platform. ActiveCampaign reads customer and marketing automation objects. Facebook Ads reads Meta ad accounts, campaigns, ad sets, ads, and daily performance results. Google Ads does the same for Google customers, campaigns, ad groups, ads, and performance numbers. Instagram uses Meta’s Graph API to collect business pages, connected Instagram accounts, posts, stories, and analytics. Klaviyo pulls many marketing resources, including profiles, campaigns, events, lists, segments, flows, and catalog items. Mailchimp reads audiences, subscribers, campaigns, reports, and email activity. Together, these connectors hide each platform’s different paging and response style behind one consistent stream format.

#### [HR, recruiting, and workforce source connectors](stage-12.1.8.md) `stage-12.1.8` — 6 files

This stage is the doorway into HR and recruiting systems. It is used during the system’s data sync work, when the code reaches out to outside services, reads their records, and reshapes them into a steady stream the rest of the codebase can store or process. Each connector is like an adapter plug for a different vendor’s API, meaning its web-based way to ask for data.

The Ashby and Greenhouse connectors pull recruiting records such as candidates, jobs, applications, interviews, offers, and users. Recruitee does a similar job for hiring pipelines, including candidates, job offers, and departments. BambooHR focuses on employee operations, including staff lists, employee details, time off, timesheets, and field definitions. Deel reads contractor and workforce records such as contracts, forms, payslips, timesheets, and tasks. Rippling reads company, worker, and team information. Together, these files hide the differences between many HR tools and turn their paged responses into consistent batches of records.

#### [Finance, billing, accounting, and commerce source connectors](stage-12.1.9.md) `stage-12.1.9` — 7 files

This stage is the set of “adapters” that let the sync system read money-related data from outside services. It is used during the main sync work, after the system knows which source to contact. Each file speaks the language of one finance tool, calls that tool’s API, and turns the answers into a steady, common stream of records the rest of the system can store.

Brex reads spend-management information like card transactions, expenses, vendors, budgets, users, and departments. Chargebee and Recurly read subscription billing data such as customers, subscriptions, invoices, items, and payments. QuickBooks and Xero read accounting records, including accounts, contacts, invoices, and payments. Square reads commerce data like customers, locations, catalog items, orders, payments, and inventory. Stripe reads a wide range of payment and billing records, including customers, invoices, subscriptions, checkout sessions, and connected-account data.

Most of these services return results in pages, like search results. These connectors handle that paging and package each page into the project’s standard format.

### [Memory extraction and searchable recall](stage-12.2.md) `stage-12.2` — 7 files

This stage is shared behind-the-scenes support for long-term memory. Before an agent answers a prompt, it can search past notes and bring back useful facts. After pages change, it can learn from them, clean up what it learned, and keep the search index fresh.

The manifest is the stage’s switchboard. It tells the system which memory tools exist, which automatic hooks run before prompts or after page changes, and which scheduled cleanup jobs should run later. The store is the main filing cabinet and search desk. It saves memories, searches them, indexes page text, and does background indexing so normal writes do not slow down. The condenser is the editor. It extracts clearer facts from raw page changes, merges related facts into summaries, and removes duplicates. The shared core memory model defines the common shape for memory search results, so other code can ask for memories without caring how they are stored. The objects file exposes memories as read-only items that can be opened by id. Events and package files provide common names, limits, and extension identity.

## [Higher-level agent workflows and extension domain features](stage-13.md) `stage-13` — 22 files

This stage sits above the basic tools and helps the assistant run larger jobs that take planning, waiting, delegation, or repeated checking. It is part main work loop and part behind-the-scenes support, like a project manager layer on top of simple actions.

The planning and automation pieces give agents a shared notebook for objectives, an alarm clock for scheduled work, and a watchman for monitors. They record plans, blockers, delegated tasks, paused conversations, and recurring jobs so progress survives beyond one chat message. The creation workflow pieces turn plans into outputs: they help build and publish websites, manage who can view them, hand web work to specialist agents, and split research across parallel workers.

The brief pipeline adds a simple writing assembly line: outline first, draft second, critique third, with clear input and output rules for each step. The todos extension gives a conversation a visible checklist, so both the assistant and user can see what is done, in progress, or still waiting.

### [Planning, delegation, automations, and monitors](stage-13.1.md) `stage-13.1` — 13 files

This stage is shared support for work that lasts longer than one chat turn. It gives the system a notebook, an alarm clock, and a watchman. The objectives tools and store let agents write plans, record attempts, blockers, and evidence, and delegate separate steps to subagents; the store rebuilds progress from saved facts, not from a worker’s memory. Scheduled task tools expose repeating jobs and “pause until later” workflows; cron code validates calendar-like rules and finds the next due time; schedules stores ownership and due times and lets background runners safely claim work; pauses does the same for sleeping conversations; scheduled_fire defines one common ID text for a particular scheduled run. The monitor package marker just makes the extension importable. Its monitor tool starts a one-shot watch by running a shell command now, saving it only if this first probe succeeds; monitor_kind shows armed watches as stoppable objects; monitors stores them and decides when probes run, fire, fail, or get skipped. Sweep uses these pieces to make private daily briefs: it gathers context, sends scout subagents, then schedules drafting after each member’s morning starts.

### [Creation workflows for sites, documents, code, and research](stage-13.2.md) `stage-13.2` — 6 files

This stage is part of the system’s main work loop, where the assistant stops just talking and starts making things. It covers several creation paths: building websites, publishing them, delegating web work to a specialist, and running broad research jobs.

The site surface is the front door for a hosted website. When someone opens a link, it checks whether they are allowed in, shows the site safely inside a protected frame, and lets the creator adjust viewing permissions. The site tools are the workshop controls: they build site files, start a local web server, publish it as a stable hosted link, and record that link. They also add guardrails, such as keeping file paths inside the workspace and checking that servers are actually running. The site store is the address book behind this, remembering which conversation owns each site and who can access it. The delegation file lets the main assistant hand website work to a child agent. The research tool similarly splits a list of research targets into parallel jobs and saves the combined answers as JSON.

## [Background schedulers, recurring jobs, and offline maintenance](stage-14.md) `stage-14` — 8 files

This stage is the system’s night shift. It runs outside the normal request path, using clocks and cleanup loops to keep work moving when no user is actively clicking. The candidates helper safely finds which workspaces may have pending work, then hands each workspace back to its normal protected area before anything real happens. The jobs runner turns registered job definitions into scheduled runs, making sure they use the right workspace and extension settings and do not pile up duplicates.

Several runners act like alarm clocks. The monitor runner checks saved watches when they are due and notifies agents about changes, repeated failures, or deadlines. The scheduled task runner fires timed tasks once and advances repeat tasks to their next time. The pause runner resumes conversations whose wait time has ended, unless a person already resumed them.

Other pieces clean up loose ends. The delivery loop returns missed child-task results to their parent conversation. The runtime instance records which server processes are alive, recovers abandoned work, and passes cancellations down to child work. The self-improvement cron tests possible prompt changes and only proposes ones that repeatedly pass checks.

## [Result delivery, portal rendering, objects, panels, and artifacts](stage-15.md) `stage-15` — 9 files

This stage is part of the main work loop and the end of a turn. It is where the system turns internal results into things people can actually see: replies, source lists, files, website links, automations, and other conversation panels. It acts like a display counter, taking finished work from the back room and arranging only the safe, allowed pieces for each viewer.

The shared slot model defines the kinds of items extensions may place in the conversation portal, such as sources, artifacts, tasks, sites, automations, and workspace changes, and rejects unsafe data before it reaches the screen. Research observations save web sources in the database and show them later in a Sources panel. Scheduled tasks and hosted sites add Automations and Sites panels, while filtering private details and keeping payloads small. Activity messages explain upcoming tool or skill use in user-friendly words. Reply handling strips hidden control tags from model text before showing replies. Artifacts manage shared files created by tools, and artifact routes provide secure signed downloads. Site objects let hosted websites be listed, inspected, permissioned, or removed.

## Turn completion, cleanup, failure recording, and service teardown `stage-16` — 0 files
## [Cross-cutting persistence, schema contracts, and durable records](stage-17.md) `stage-17` · (cross-cutting) — 10 files

This stage is the system’s shared filing cabinet. It is behind-the-scenes support used during startup, normal work, recovery, and background jobs. Its job is to make sure every part of the project agrees on how durable data is named, shaped, stored, and read later.

The database doorway is in db.py. It opens safe database connections, starts transactions, ties them to the right workspace, runs migrations, and cleans up. tables.py defines the actual table layout for both local SQLite and production Postgres. records.py defines the shapes of important saved items, such as turns, agents, tool requests, billing usage, and terminal results. transcript.py defines the saved conversation format, while loop/transcript.py safely reads and writes conversation snapshots so an old copy cannot replace a newer one.

blob.py stores large files in either local storage or cloud storage, keeping workspace files separate. durability.py makes recovered saved Python objects survive software changes. listings.py provides stable page-by-page browsing with cursor tokens. object_name.py rejects unsafe object names. schema/__init__.py simply makes the schema folder importable.

## [Cross-cutting identity, credentials, authorization, and safety gates](stage-18.md) `stage-18` · (cross-cutting) — 26 files

This stage is the system’s shared security backbone. It is not one step in startup or shutdown. Instead, it runs behind the scenes whenever people, agents, workspaces, outside services, sandboxes, or private data are involved.

First, workspace identity and execution context make sure every action knows which workspace, member, operator, or agent it belongs to, so one customer’s data cannot leak into another’s. Signed tokens act like tamper-proof tickets for special links, such as artifact downloads or sandbox access. The credential vault stores secrets in sealed form and releases them only through narrow, checked paths.

External account grants decide which connected services, such as GitHub or API providers, an agent may use. Sandbox safety then limits what untrusted code can read, which fake environment “keys” it sees, and where it may connect on the network. Finally, audience, visibility, governance, and untrusted-content checks decide who may see objects, how prompt changes are approved, and how outside text or images are treated safely. Together, these parts form the locks, badges, tickets, and guardrails for the whole system.

### [Workspace identity, member seats, and execution context](stage-18.1.md) `stage-18.1` — 6 files

This stage is shared behind-the-scenes support for almost everything the system does. It answers the basic questions: “Which workspace are we acting in?”, “Who is the member or operator?”, “Which agent is allowed to act?”, and “What data may this code touch?”

The workspace context is the main anchor. It records the current workspace for a request or background job, so secrets, billing, and database work do not get mixed between customers. The database safety layer then uses that workspace to enforce row-level security, meaning Postgres only exposes rows for the selected workspace. Bearer tokens act like signed ID cards: they prove a member’s identity and workspace without keeping a server-side session. Operator tools use a shared helper to read those tokens, verify them, and decide what an operator may inspect. Agent scope adds another guardrail by making sure agent-owned actions run only as the correct agent in the correct workspace. Seats decide which members the agent may serve, including creating members, granting or removing access, and ensuring at least one admin keeps a seat.

### [Signed tokens for routing, artifact access, and sandbox ingress](stage-18.2.md) `stage-18.2` — 4 files

This stage is shared behind-the-scenes support for links and routes that must not rely on whatever a client says. It is used when the system needs to hand out a small, temporary “proof ticket” in a URL: for routing to the right workspace, downloading an artifact, or opening access to a sandbox port.

At the center is token_signing.py. It makes compact signed tokens, which are small data bundles with a seal. A shared secret key creates the seal, and later checks whether the contents were changed. Other files build safer, purpose-specific tickets on top of that. surface_token.py signs early routing information for public surface routes, before normal login cookies or sessions are available. artifact_url.py creates short-lived download links tied to one exact artifact and workspace, and rejects expired or altered links. ingress_token.py does the same for sandbox access, but narrows the permission to one workspace conversation and one port. Together, these files let the system trust the token, not mutable URL input.

### [Credential vault, BYOK slots, and controlled secret disclosure](stage-18.3.md) `stage-18.3` — 3 files

This stage is shared security support that sits behind the main work of the system. Its job is to keep secrets, such as API keys or Git passwords, out of ordinary code paths and reveal them only when a trusted part of the system truly needs them.

The credential slot model in credential_kind.py is like a set of labeled empty lockers. Extensions can declare “bring your own key” slots, meaning places where a user must supply a secret. The workspace can show that a slot exists and whether it has been filled, but it never shows the secret itself.

credentials.py is the vault. It stores user-supplied and provider-issued credentials in sealed form, and it checks short-lived proof tokens before releasing anything. These tokens act like temporary claim tickets tied to the correct workspace, member, and slot.

credential_callback.py is a narrow service door for the sandbox cache daemon. When it needs Git credentials, it calls this private endpoint and receives only the credential already tied to that workspace request, not general access to the vault.

### [External account grants and connector authorization](stage-18.4.md) `stage-18.4` — 4 files

This stage is shared behind-the-scenes support for deciding which outside services an agent is allowed to use. It is like the permission desk for accounts such as GitHub or API-based source providers.

The grants code lets a workspace member connect an outside account using OAuth, a standard “sign in and approve access” flow. It records who owns the connection, which agents may use it, how the connection appears in the workspace, and what happens when it is shared, revoked, or cleaned up.

The GitHub connection code is for workspace administrators. When an admin links a GitHub App installation, it checks that the installation really belongs to the GitHub user who approved it, rather than trusting a raw installation number from a web link.

The GitHub App token code then turns that approved installation into short-lived GitHub access tokens. It prefers the workspace’s organization-approved GitHub identity, and uses a member’s personal token only if no App installation is available.

The direct source connector code covers services that use an API key. It retrieves the stored key safely and turns it into a bearer credential for provider calls.

### [Sandbox containment, injected environment, and egress policy](stage-18.5.md) `stage-18.5` — 3 files

This stage is the sandbox’s safety layer. It sits behind the scenes whenever untrusted work is run, such as an agent action, connector call, or off-turn probe. Its job is to give that work only the space, secrets, and network reach it is allowed to have.

The containment file checks file paths before the sandbox uses them. If a model or connector asks for a path, it verifies that the path stays inside the approved directory. It also guards against symbolic links, which are shortcut files that might secretly point outside the sandbox.

The exec environment file prepares the environment variables for sandbox probes. Environment variables are small name-value settings passed into a process. Here they may include approved credential names, but only as safe placeholders, never real secret values.

The proxy rules file controls network exits. It gathers the model being used, extension manifests, credentials, grants, and artifact storage settings, then turns them into concrete proxy rules. Together, these parts work like locked doors, fake keys, and a guarded gate.

### [Audience, visibility, governance, and untrusted content safety](stage-18.6.md) `stage-18.6` — 6 files

This stage is shared behind-the-scenes safety gear. It does not run the main conversation by itself. Instead, it sets rules that other parts of the system rely on before showing content, changing prompts, or accepting outside data.

The audience and subjects files define the allowed labels for “who may see this,” such as a whole workspace or one member. They check those labels so a private conversation is not accidentally treated as public, and so every part of the system uses the same wording. The scheduled task visibility file applies those rules to task prompts and descriptions, deciding whether a member may read them.

The governance file protects agent prompt changes with a proposal and approval step. It only applies an approved change if the original prompt has not changed meanwhile, like signing the exact version of a document.

The untrusted content file wraps outside text so the model treats it as material to inspect, not commands to follow. The image preview file similarly checks outside image data before use, making sure it matches its promised type and size and is not malformed or dangerously large.

## [Cross-cutting SDK, extension ABI, protocol types, and public contracts](stage-19.md) `stage-19` · (cross-cutting) — 37 files

This stage is shared behind-the-scenes support for extension authors and outside integrations. It is not the startup path or the main work loop. Instead, it defines the public “contract” for how outside code may talk to the system without touching private internal parts.

The extension ABI, manifests, and context describe what an extension can declare and what safe tools it receives while running. The model, connector, source, and search contracts define common plugs for AI models, content feeds, search indexes, and memory retrieval, so different providers can fit the same sockets. The interactive SDK APIs expose approved ways to build web views, panels, browser links, objects, terminals, and sandboxed code. The identity and safety contracts provide stable access to audiences, credentials, permissions, seats, tokens, and markers for untrusted input. The SDK package shell and utility re-exports gather everyday helpers such as jobs, schedules, logging, accounting, listings, and skills.

Together, these pieces form the project’s public toolbox: stable, labeled doors into selected capabilities while the engine room stays private.

### [Extension ABI, manifests, and execution context](stage-19.1.md) `stage-19.1` — 6 files

This stage is shared behind-the-scenes support for UFO’s extension system. It defines the “rules of the road” for outside code, so extensions can plug in safely without depending on private internals. The manifest file is the main menu: it describes what an extension or pack may declare, such as tools, routes, background jobs, credentials, agents, hooks, storage, and search providers. The contracts file defines the expected shape of data sent to and returned from spawned agents, using either built-in models or JSON Schema, so the core can validate both consistently.

The execution context file builds the limited toolbox an extension or job receives while running. It gives access only to approved things like workspace data, credentials, conversations, model calls, sources, and files. The SDK files are the public front doors for extension authors. They re-export context, manifest, and tool types from stable locations, so extensions can import supported names without reaching into the engine room.

### [Model, connector, source, and search provider contracts](stage-19.2.md) `stage-19.2` — 8 files

This stage is shared behind-the-scenes support. It does not run the system by itself. Instead, it defines the “plugs and sockets” that outside providers and extensions must use so they fit safely into UFO.

The core model interface describes the common shape for talking to AI providers: messages sent to a model, streamed answers coming back, tool requests, images, reasoning notes, and usage counts. This lets different model services behave like interchangeable parts.

The SDK files are public doorways for extension authors. The models SDK re-exports the model objects from one stable place. The auth proxy, connectors, and sources SDK files expose the pieces needed to add authenticated feed syncing, connector services, and external content sources. The index SDK exposes the allowed pieces for adding search indexes or embedding backends, which turn content into searchable form. The memory SDK provides the public types for memory retrieval. The search SDK gathers the public request, result, fetch, and provider types. Together, these files keep internal code private while giving outside integrations a clear, consistent contract.

### [Interactive surfaces, web, browser, object, and terminal SDK APIs](stage-19.3.md) `stage-19.3` — 8 files

This stage is the public front door for extension authors who want to build interactive features. It is shared behind-the-scenes support, not the main work loop itself. Its job is to hide the project’s internal layout and offer stable import paths, so extensions can keep working even if the inside of the system is reorganized.

The browser file exposes approved browser-connection pieces for real-time communication. The HTTP file provides the web route toolbox: requests, responses, forms, uploads, and cookies. The hub file re-exports hub tools, which are used to connect extension code to the system’s shared coordination area. The objects file gathers supported object types and helpers. The sandbox file exposes tools for running code in a controlled, safer space. The surface-token file provides tools for creating and checking permanent links to interactive surfaces. The surfaces file gathers the official building blocks for user-facing extension panels or views. The terminal file exposes the supported terminal API. Together, these files act like labeled sockets on a machine: extensions plug in safely without touching internal wiring.

### [Identity, credentials, access, seats, and safety contracts](stage-19.4.md) `stage-19.4` — 8 files

This stage is shared behind-the-scenes support for extensions and outside code. It creates safe public “front doors” into identity, access, and safety tools, so callers do not need to depend on private internal modules that may move.

The audience and subjects SDK files describe who can see a conversation or row. Audience gives public access to conversation-audience names and helpers. Subjects exposes helpers for shared-workspace visibility and member-specific visibility. Bearer provides a safe way to verify bearer tokens, which are login/session tokens carried with a request, without exposing the secret used to make them. Credentials exposes approved credential tools. Grants re-publishes connection and permission-audit tools, so extensions can inspect access in a stable way.

Operator exposes helpers for operator-only web sessions. Seats re-exports seat types and helpers, used to track or reason about allowed user places. Untrusted shares the common marker for text that should be treated carefully, like input from outside users. Together these files form a controlled SDK layer around access and safety.

### [SDK package shell and operational utility re-exports](stage-19.5.md) `stage-19.5` — 7 files

This stage is shared behind-the-scenes support for people writing code against the SDK. An SDK is the public “toolbox” the project offers to extensions and outside code. These files mostly do not create new behavior. Instead, they act like labeled doors in a building: stable import paths that lead to the real machinery elsewhere, even if the internal layout changes later.

The package marker, __init__.py, simply tells Python that ufo.sdk is a package that can contain importable modules. accounting.py opens a public door to spending and usage records, such as reports and totals. listings.py exposes tools for paged lists, where large result sets are delivered in manageable chunks. o11y.py gives extensions approved logging and metrics tools, so they can report what happened and how much work was done. scheduled_fire.py re-exports helpers for cron-like scheduled runs. jobs.py exposes supported names for declaring background jobs. skills.py provides the public path to skill-related tools. Together, these files keep extension code simple, stable, and separated from private internals.

## [Cross-cutting accounting, billing, usage export, and spend reporting](stage-20.md) `stage-20` · (cross-cutting) — 4 files

This stage is the system’s behind-the-scenes cash register. It runs alongside normal work, before and after tasks, to measure what was used, check whether spending is allowed, and prepare records for billing and reports.

The accounting file is the main ledger. It records costs from model tokens, sandbox work, image and video generation, network use, connector activity, and proxy credentials. It can stop work before it starts if a spend cap would be exceeded, and it builds reports from both live and saved usage records.

The balance file focuses on prepaid funds for a workspace. It stores balances in micro-USD, meaning tiny fractions of a dollar, so calculations stay exact. It also prevents the same payment credit from being counted twice.

The pricing file is the price tag maker for model usage. It turns token counts into costs and saves a fingerprint of the price table, so old bills can be traced back to the exact prices used.

The Metronome extension connects these records to outside billing tools. It exports daily usage and member counts, links Stripe payment setup, and lets admins manage billing from chat.

## [Cross-cutting diagnostics, demos, evaluations, and conformance fixtures](stage-21.md) `stage-21` · (cross-cutting) — 13 files

This stage is shared support that helps people inspect, test, and improve the system without touching real user data or real outside services. The observability toolbox records logs, traces, and metrics, which are like the system’s dashboard gauges, while trimming sensitive or huge text before it is stored. The seed file builds a “kitchen sink” demo conversation so the web portal can show many features safely.

Several files provide controlled test extensions. The debugger and evaluation packages make their extension code importable. The evaluation manifest supplies fake email, calendar, and code-search connectors, so tests can use the normal connector route with predictable data. The sample skill probe prints a fixed success message, and the sample extension exercises nearly every public extension hook to prove third-party-style extensions still work.

The self-improvement files form a careful offline loop. They collect past failed tool conversations, talk to a language model through a small wrapper, propose better agent instructions, replay old conversations without running real tools, judge the results, and use a safety gate before accepting any new prompt.

## [Python package namespace markers](stage-22.md) `stage-22` — 6 files

This stage is quiet behind-the-scenes support. It is not part of startup, the main work loop, or shutdown. Instead, it sets up the project’s Python “package” layout. A package is a folder Python is allowed to import code from. These __init__.py files are like nameplates on office doors: they tell Python that the rooms exist, even though the nameplates do not do the work inside.

The ufo_control marker makes the control package importable. The main ufo marker does the same for the core runtime area. Inside it, ufo.ext opens the extension namespace, ufo.loop opens the loop-related namespace, and ufo.sandbox opens the sandbox namespace. The ufo.sandbox.proxy marker goes one level deeper, making proxy-related sandbox modules importable.

Together, these files create the project’s import map. Other code can reliably say “load this module from this package,” while these marker files add no runtime behavior of their own.
