# System Handbook — Stage Index

Each stage below links to its full page; the paragraph is the stage's role in the system.

## [Deployment preflight and schema migration entrypoints](stage-1.md) `stage-1` — 99 files

This stage happens before the system serves users. It is the preflight checklist for deployment: make sure the database layout, safety rules, optional add-ons, and sandbox environment all match what the running code expects.

The control database and Alembic entrypoints are the front door. They connect the migration tool, Alembic, to the project and prepare shared database rules, including row-level security, which keeps each workspace’s rows separate. The core migration groups are the main instruction book for the database. They create the first tables, then gradually add support for conversations, agents, sources, billing, scheduling, audit logs, artifacts, members, permissions, and newer product behavior while keeping older stored data usable.

Extension-owned migration trees are separate instruction books for optional features such as objectives, research, sample notes, scheduled-task pauses, hosted sites, skill creation, and web chat metadata. Finally, the sandbox validation scripts check the isolated execution environment: one verifies the sandbox image recipe, and the other proves encrypted web traffic goes through the required proxy. Together, these checks reduce surprises when the service starts.

### [Control database and Alembic preflight entrypoints](stage-1.1.md) `stage-1.1` — 4 files

This stage is part of deployment and startup preparation, before the main application begins serving real traffic. Its job is to make sure the databases are in the right shape and that access rules are in place, instead of discovering problems during a live request.

The control package marker, __init__.py, is the small doorway that lets Python import the control database code. The schema.py file does the practical setup work for the control gateway. It creates or checks the PostgreSQL tables that the gateway depends on, making database preparation an intentional step. The rls.py file adds the safety fence around shared data. “Row-level security” means PostgreSQL checks each individual row and only lets a workspace see its own data. This file creates the application database role and applies those rules to public tables.

The env.py file in the core schema migrations is the migration entrypoint. It connects Alembic, the tool that updates database structure over time, to the project’s expected schema and runs the needed changes. Together, these files prepare both structure and access boundaries before normal work starts.

### [Core baseline schema and early conversation migrations](stage-1.2.md) `stage-1.2` — 14 files

This stage is the project’s first database blueprint. A migration is an ordered change to the database, like adding rooms and labels to a filing cabinet before the app can use it. The first migration creates the core records for workspaces, users, agents, conversations, turns, identities, and cost tracking. Later migrations add safe storage for encrypted credentials, proposed changes, and nested turns where one agent delegates work to another. Extensions get a small JSON store. Synced sources and pages let the system remember imported content. Slack and web migrations let conversations come from those surfaces and prevent duplicate Slack work. Other migrations add shared files from turns, spending caps and paused turns, access grants to provider accounts, active runtime instances that check in, and scheduled tasks for future or repeating work. The knowledge graph migration adds “things” and “relationships” tables. Together, these files establish the early shared storage that the rest of the system depends on.

### [Core source, ledger, scheduling, and inbound-message migrations](stage-1.3.md) `stage-1.3` — 19 files

This stage is behind-the-scenes upgrade work for the database. Each file is a migration, meaning a small step that changes stored data structures and can usually be undone if the system rolls back. Together they make the core system better at tracking usage, work, messages, and outside connections.

Several changes expand the ledger, the system’s spending and usage log. It can now record egress, sandbox tokens, price details, workspace-level entries, and exports sent to outside consumers. Turn records, which represent units of conversation work, gain guards against duplicate runs, trace links for following related work, extra context from the outside surface, and a new “scheduled” admission reason.

Source records become more flexible too. They can use non-folder backends, count repeated errors for backoff, and be marked removed without losing history. Conversations can remember their sandbox handle so work can resume. Job and writeback indexes act like signposts, helping sweepers find due work quickly. Runtime instances can belong to a shared fleet instead of one workspace. Surface records are tied more clearly to workspaces. Finally, inbound messages get their own storage and a revised place for rendered text.

### [Core workspace, grant, source, page, and agent migrations](stage-1.4.md) `stage-1.4` — 19 files

This stage is behind-the-scenes upgrade work for the database, the system’s long-term filing cabinet. Each migration is a small step that reshapes old stored data so newer code can use it safely. Several changes improve how conversations and tasks record responsibility: turns gain a speaker, parent lookup, connection authorization, and “on behalf of” member links; scheduled tasks remember their last firing turn, creator, expiration time, and agent-specific name rules. Workspace billing and access are updated with member seats, seat limits, included seats, shared grants, and source ownership. Export records gain a BYOK flag, meaning the customer controls the encryption key. Pages become easier to browse and sync: they store origin details, titles, clearer record timestamps, and workspace-local revision numbers. Agent behavior is tightened by adding internet-access settings and required links from conversations and surface installations to an agent. Older shared-fleet and knowledge-graph structures are cleaned out. Finally, source grants make source access explicit by recording which agents may read which sources.

### [Core audience, control, connection, admission, and audit migrations](stage-1.5.md) `stage-1.5` — 14 files

This stage is behind-the-scenes upgrade work for the database, the system’s long-term memory. Each migration is a small numbered change that makes stored data match what newer code expects. It adds pause and origin fields for scheduled turns, records who a conversation’s audience is, and marks the main admin member and main agent for each workspace. It reorganizes account access by separating reusable connections from an agent’s permission to use them, then lets sources point to those connections directly. It expands turn admission so an “intent” can start work, gives shared artifacts stable IDs, adds agent reasoning settings, and lets scheduled tasks be paused without deleting them. It also repoints agents away from retired Bedrock model names. For oversight, it creates and then trims audit storage for admin transcript reads. Finally, it links conversations to sandbox runs, splits ledger token counts into clearer buckets, and stores the visible surface label where a conversation began. Together, these changes keep old databases usable while adding newer control, audit, and tracking features.

### [Core usage, artifact, member, conversation, and billing migrations](stage-1.6.md) `stage-1.6` — 17 files

This stage is part of upgrading the system’s database. These migration files are small step-by-step scripts that reshape stored data so newer code can run safely. They clean out old extension data for page alerts and YC, keeping retired sources visible instead of erasing history. They expand the usage ledger so it can count images and videos, then make ledger lookups faster by workspace and time. They add tracking for subagent child turns that must report results back, Git-based workspace changes in conversations, artifact previews, stored conversation titles, and spoken-turn searches by speaker. They improve member and sign-in support with a faster email lookup, move sharing settings onto connections, add account labels, and switch workspaces from seat limits to unlimited members. They also add a required small, medium, or large sandbox size for agents, remove old pause storage now handled outside core, and add tables for prepaid workspace balances and purchase records. Together, these changes keep the database aligned with newer product behavior.

### [Extension-owned migration trees](stage-1.7.md) `stage-1.7` — 10 files

This stage is behind-the-scenes upgrade work for optional extensions. A migration is a small script that changes stored data or table shapes when the software is updated. Instead of putting every change in the core database history, each extension keeps its own “tree” of migrations, like separate instruction sheets for add-on parts.

The objectives migration adds a stored yes/no flag for whether an objective step can run on its own. Research creates a table to remember outside web sources used in a conversation. The sample extension adds a simple per-workspace note table. Scheduled tasks adds durable storage for agents paused until a later time. Sites first creates hosted-site records, then adds a generation ID so different versions can be told apart. Skill creation first stores user-made skills, then ties those skills to a specific agent and updates old rows safely. Web migrations fill in missing chat metadata for older conversations, then move chat titles into the core conversation table so the main conversation list can show them directly.

### [Sandbox deployment validation scripts](stage-1.8.md) `stage-1.8` — 2 files

This stage is a set of preflight checks run during deployment, before the sandbox service is trusted to handle real traffic. A sandbox is an isolated workspace where UFO can run work safely. These scripts make sure that workspace is built correctly and that its network path is safe.

sandbox/build_template.py checks the “room” itself. It builds the local Docker image, which is the packaged software environment used on a machine, and compares it with the cloud E2B sandbox template, which is the version used when real sandboxes are launched. Its job is to keep both made from the same recipe, so code behaves the same in testing and in production.

sandbox/proxy_gate.py checks the “doorway” out to the internet. It starts a real sandbox, installs the trusted certificate, then tries HTTPS traffic, meaning encrypted web traffic. The script proves that this traffic goes through the sandbox proxy as expected. Together, the two checks confirm both the sandbox contents and its secure routing before deployment continues.

## [Process bootstrap, CLI commands, and application lifespan](stage-2.md) `stage-2` — 5 files

This stage is the system’s front door and power switch. It covers what happens when an operator starts UFO, runs an admin command, builds a deployable package, or shuts the service down cleanly. The main server path is in core/src/ufo/serve.py. It reads configuration, opens database connections, loads extensions, starts background workers, attaches web routes, and launches the HTTP server that clients talk to. core/src/ufo/cli.py provides ufoctl, the local command-line tool for setup, running, inspection, packaging, repair, and development tasks. control/src/ufo_control/main.py plays a similar role for the hosted control service, including database setup, invitations, Slack retry work, and access-policy setup. core/src/ufo/bundle.py freezes a deployment into a repeatable Docker build folder, like packing a machine with its exact parts list. core/src/ufo/proxy_serve.py starts the shared network proxy used by sandboxes, applying the right workspace rules based on each request’s run token. Together these pieces start services, prepare their dependencies, expose commands and routes, and support orderly shutdown.

## [Pack selection and capability assembly](stage-3.md) `stage-3` — 34 files

This stage happens during startup, before the assistant begins its main work. Its job is to choose a “pack,” which is a ready-made bundle of features, and turn it into all the usable parts the host needs: tools, skills, routes, credentials, background jobs, model and browser backends, connectors, hooks, and object types.

The pack bundle definitions are the recipes. They say which feature groups to enable for local development, hosted use, evaluations, or special workflows. The core extension and skill loading runtime is the unpacking machinery. It checks what was selected, loads extension manifests, and prepares skill folders in an isolated workspace.

The agent skill and subagent manifests add task-focused helpers for browsing, coding, documents, research, sites, and similar work. The connector manifests make outside services such as Slack, Composio, Pipedream, and source systems available. The memory, objectives, monitors, and scheduled automation manifests add long-running support such as reminders, saved context, watched conditions, and recurring jobs. The platform manifests expose the web portal, debugger, shell stream, and optional Redis-backed infrastructure.

### [Core extension and skill loading runtime](stage-3.1.md) `stage-3.1` — 4 files

This stage is shared startup support for the system’s extension and skill features. It prepares extra abilities before the main agent work begins, much like laying out tools on a workbench.

The main entry point is `ufo.ext.loader`. It looks for installed extensions, checks that the selected ones are allowed and safe to combine, then converts their declarations into usable pieces: tools the agent can call, hooks that run at set moments, skills, object types, credentials, and backend services. This gives the rest of the system one organized view of what extensions provide.

Skills get their own runtime in `ufo.skills.runtime`. A skill is a folder of instructions and files that teach the agent how to do a task. This file reads those folders, follows dependencies between skills, and copies the needed material into a sandboxed workspace, which is an isolated area where the agent can work without touching the original files.

The two `__init__.py` files simply make `ufo.ext` and `ufo.skills` importable Python packages.

### [Pack bundle definitions](stage-3.2.md) `stage-3.2` — 8 files

This stage is shared setup support. It defines “packs,” which are ready-made recipes for turning on groups of features together. Instead of asking users or tests to list every extension and skill one by one, the system can load a named pack and get the right bundle.

The local assistant pack is the basic recipe for running assistant features in development. The billing assistant pack adds billing setup support so developers can test that flow locally. The assistant evaluation pack starts from the normal assistant, removes real outside service connectors, and adds fake test tools plus Docker support, so evaluations are safer and repeatable. The hosted assistant pack selects features meant to run on managed cloud services.

Other packs serve special workflows. The chief-of-staff pack declares the extensions and skills for a manager-assistant style workflow. The DSQA and GDPVal evaluation packs provide different tool bundles for different evaluation needs, such as search or browser use. The sample pack is a small proof case for the public SDK, showing that packs can be found, loaded, and run.

### [Agent skill, subagent, and skill-support extensions](stage-3.3.md) `stage-3.3` — 8 files

This stage is behind-the-scenes setup for agent abilities. It does not perform the main work itself. Instead, it tells the larger UFO system which extra “extension” packs exist and how to load them. Most files here are manifests, meaning registration sheets that list the agents, tools, prompts, skills, and outside services an extension needs.

The brief pipeline manifest registers three specialist helpers that make a brief in steps: outline, draft, then critique. The browser package file makes the browser extension importable, while its manifest registers browser delegation tools and required browser capability. The coding manifest signs up a software-repository helper, its routes, tools, skills, and GitHub credentials. The documents manifest adds writing, editing, review, and formatting support. The research manifest registers search-based research tools, helper profiles, prompts, skills, and stored conversation data, and requires a search backend. The sites manifest adds website-building tools, prompts, skills, subagents, surfaces, and chat objects. The sample skill probe is a small test switch: running it prints success, proving the skill can be reached.

### [External connector and source integration manifests](stage-3.4.md) `stage-3.4` — 5 files

This stage is behind-the-scenes setup work. It is made of manifest files, which are like labels on plug-in boxes. Each manifest tells the main UFO system what an extension can do, what it needs, and how the rest of the system should load it.

The Composio manifest registers connectors that go through Composio, the shared broker that talks to Composio, and the web route used when a user signs in. The Pipedream manifest does the same kind of job for Pipedream, listing external apps, authorization needs, and its sign-in route. The Slack manifest describes Slack-specific routes, credentials, tools, hooks, and setup instructions so Slack can be connected cleanly.

The connectors manifest registers shared connector tools, data objects, and prompt text that helps the assistant explain and use those tools. The sources manifest registers source connectors, how to build them, the credentials they require, and any related objects or hooks. Together, these files make outside services visible and usable to the core system.

### [Memory, objectives, monitors, and scheduled automation manifests](stage-3.5.md) `stage-3.5` — 5 files

This stage is shared behind-the-scenes support. It is made of extension manifests, which are like registration forms that tell the host system what extra parts to load and when to run them. Together, they give the assistant long-term context, reminders, automatic checks, scheduled work, and evaluation loops.

The memory manifest wires in tools and background jobs for stored memories. It lets other parts of the system search, update, recall, index, and summarize information the assistant has saved. The objectives manifest keeps active goals visible. At the start of a conversation turn, it reminds the agent of the current plan, unfinished steps, and conditions that still need to be met.

The monitors manifest registers monitor objects and a recurring checker, so the system can revisit watched conditions when they are due. The scheduled-tasks manifest registers task objects, a wait tool, background jobs, and the skill for creating future work. The self-improvement manifest adds a scheduled evaluation job, so the system can periodically review itself and learn from results.

### [Platform surface and infrastructure manifests](stage-3.6.md) `stage-3.6` — 4 files

This stage is part of startup and shared setup. It is made of small “manifest” files, which are like labels and instruction cards for plug-in parts of the system. They tell the main UFO application what extra features exist, what version they are, and where to connect them.

The debugger manifest registers the debugger extension and its single web/API surface, so the host knows which route to expose for debugging tools. The Redis hub manifest registers optional Redis-backed hub and terminal pieces. Redis is an external in-memory data store; here it is used as shared backing for communication and terminal state. This manifest also provides factory functions, which are small makers that build the Redis versions when selected in configuration.

The UFO manifest registers the core UFO web surface, including the shell stream route used for live interaction. The web manifest installs the user-facing portal. It declares routes, permissions, conversation storage areas, and a background service that gives chats names based on their first messages. Together, these files make the platform’s visible doors and supporting services discoverable.

## [User onboarding and workspace enrollment](stage-4.md) `stage-4` — 12 files

This stage is the front door for new users. It runs during first setup, before the client is fully connected. The main gateway web server guides a person from installing UFO to joining the right workspace. The terminal client receives simple text instructions from gateway_directives, while gateway_web shows the same flow in a browser and translates those instructions into web-friendly data.

The email path checks that the address looks like a real work email, sends invite messages, and uses WorkOS, an external email sign-in service, to prove the person controls that address. gateway_claim records each attempt as a short-lived claim in PostgreSQL through gateway_store, then marks it verified or expired. gateway_invite manages one-time links that let a company domain create a workspace.

Once an email is verified, gateway_shared finds or creates the matching workspace and adds the user. gateway_token creates a 30-day sign-in token for the client. core onboarding then creates the workspace’s first admin, assistant, secrets, and extension setup. Finally, gateway_slack_connect can invite the new customer into UFO’s operator Slack workspace.

## [Surface ingress and external request routing](stage-5.md) `stage-5` — 27 files

This stage is the system’s set of front doors while it is running. Requests arrive from browsers, Slack, terminals, operators, file links, and sandbox-hosted sites. Each surface speaks its own outside language, then uses a shared bridge to turn that traffic into core actions such as finding a member, opening a conversation, sending a message, reading data, or streaming replies.

The web portal surface serves the browser app and handles chat, settings, admin views, objects, live updates, and community skill browsing. The Slack surface verifies Slack events, maps messages and buttons into conversations, formats mentions, and sends replies back. The terminal surface does the same for command-line users, including live progress, credential prompts, OAuth callbacks, and cross-server terminal streams. Operator and debugger surfaces are read-only control room windows for trusted staff to inspect conversations, turns, files, and memory.

Shared support files keep these doors safe. Signed tokens route early public requests. Signed artifact links and artifact routes protect downloads and refresh expired links for valid members. Image preview checks prevent harmful oversized inputs. The sandbox ingress server exposes sandbox sites only through valid signed host links.

### [Web portal surface](stage-5.1.md) `stage-5.1` — 4 files

This stage is the user-facing web doorway into UFO while the system is running. It serves the browser app and turns clicks, forms, and live updates into requests the rest of the system can understand.

The main piece is surface.py. It delivers the web page, checks the user’s signed session cookie, and exposes the routes the browser calls. A route is a web address for a specific action. These routes cover chat messages, transcripts, workspace and admin data, object access, and live streaming so the portal can show updates as they happen.

community.py connects the portal to the public skills.sh directory. It lets users browse and search community skills, then fetches the full text of one chosen skill carefully, without pulling more remote data than needed.

panels.py connects settings-page buttons and forms to the normal chat-based write path, so portal actions use the same machinery as chat commands. It also supplies agent overview data for settings screens. __init__.py simply makes this folder importable as a Python package.

### [Slack surface](stage-5.2.md) `stage-5.2` — 6 files

The Slack surface is the system’s Slack-facing doorway. It is part of the main communication loop: Slack sends events in, ufo turns them into conversations, and replies go back to Slack. The package marker, __init__.py, simply makes this extension importable by the rest of the code.

surface.py is the front desk. It checks that incoming Slack requests are genuine, receives messages and button clicks, starts or continues the right ufo conversation, and posts replies, progress notes, and files back to Slack. tools.py adds guided chat tools for connecting a UFO workspace to Slack and for searching Slack conversations later, so setup and search can happen through the agent.

mentions.py translates Slack’s special encoded text, such as user and channel tags, into readable form and extracts who or what was mentioned. attribution.py manages the small footer added to connector-sent Slack messages, including the bot mention, while making sure that footer does not look like a real user message. hooks.py adds the proper Slack bot mention to outgoing connector messages when it can do so quickly and safely.

### [Terminal and command-line surface](stage-5.3.md) `stage-5.3` — 6 files

This stage is the system’s doorway to command-line users. It sits on the outside edge of the main work loop, turning internal events into simple text that a shell program can show, and turning terminal replies back into actions the system understands.

The main piece is the UFO terminal surface. It exposes an HTTP endpoint for the command-line client and speaks a small text command protocol. Through it, the user can see chat messages, live progress updates, credential prompts, file links, and terminal actions in their own shell. Redis terminal support keeps this working even when the user’s connection is on one server pod and the backend work is on another, using Redis Streams for short control messages and separate blob storage for larger data. The sandbox terminal code lets the system run work through a user’s already-open terminal instead of connecting to a separate container. The CLI callback file finishes OAuth account linking after an external service sends the user back. The two __init__ files are simple package markers so these extensions can be imported.

### [Operator and debugger surfaces](stage-5.4.md) `stage-5.4` — 4 files

This stage provides the “control room windows” for trusted operators. It is not part of the normal user conversation loop. Instead, it is shared behind-the-scenes support for inspecting what is happening in a workspace during development, debugging, or operations.

The shared helper in core/src/ufo/ext/operator.py acts like the front desk. It finds the operator’s access token, checks that the person belongs to the allowed email domain, and decides which workspace they are permitted to view. The debugger package marker, __init__.py, simply makes the debugger extension importable by the rest of the system.

The debugger surface then provides the read-only web pages and data routes for looking inside operator sessions. It can show conversations, individual turns, transcripts, files, and live event streams as they happen. The memory surface does a similar job for stored memory records: it gives authorized operators a read-only page and JSON data access for one workspace. Together, these pieces let trusted people inspect sessions and memory safely without modifying user data.

## [Identity, workspace membership, and object authorization](stage-6.md) `stage-6` — 16 files

This stage is the system’s identity checkpoint. It runs behind the scenes whenever a request needs to know who is acting, which workspace they belong to, and what they are allowed to see or change. The workspace context code keeps all work tied to the right workspace, credentials, database, and billing. Bearer tokens are signed login passes that prove a member’s email and workspace without a server session.

Membership and seats are managed by the member, seat, and workspace inspection objects. They show who belongs, who is an admin, who has an active seat, and prevent unsafe changes like removing the last seated admin. Agent scope and object scope record which agent is currently acting, so audits and permissions stay accurate.

The object system is the shared doorway for workspace items. It checks names, shapes, and permissions before calling each object type. Agents, conversations, credentials, extensions, and the workspace itself are exposed mostly as controlled or read-only objects. Web audience rules decide which members can see which agents. Audience and subject labels describe who content is for, while scheduled-task visibility protects private task text.

## [Conversation admission, turn creation, and live control](stage-7.md) `stage-7` — 3 files

This stage is the conversation “front desk” during the main work loop. It decides when new work may enter a conversation, creates a durable turn record for that work, and gives people live control over work that is already running.

The admission file is the main doorway. Member messages, scheduled events, and results from internal tools all pass through it. It applies the same safety and permission checks, then either starts a new turn or joins the caller to an existing live stream. This keeps conversation work orderly and recorded instead of happening as loose background activity.

The ambient reply file handles a quieter case: a message appears in a thread without directly calling on the agent. Before spending money and time on a full response, it asks a small bounded model whether the agent should speak or stay silent.

The stop file is the emergency brake. It verifies that a person is allowed to stop a running turn, cancels it safely, starts any waiting follow-up work, and notifies live listeners that the old turn ended.

## [Background job dispatch and scheduled wakeups](stage-8.md) `stage-8` — 6 files

This stage is the server’s alarm clock and background dispatcher. It runs behind the scenes, outside normal user requests, but often feeds work back into the same conversation and sync paths that user actions use. At startup, core/src/ufo/jobs.py finds registered background jobs and turns them into durable DBOS work, meaning work stored and retried reliably by the system. It schedules repeating jobs, starts one-off jobs, and runs each job inside the right workspace and extension context.

core/src/ufo/candidates.py helps decide which workspaces may have pending background work, but does it safely. Extensions can point to workspace IDs without getting broad access to every workspace’s data. core/src/ufo/ext/scheduled_fire.py keeps the shared key format for “this task fired at this time,” so the scheduler and history display agree.

The scheduled-tasks extension supplies the actual task runners. Its package marker makes the code importable. runner.py claims due scheduled tasks, sends each into the correct conversation once, then advances its next run time. pause_runner.py wakes paused conversations when their wait expires and cleans up the pause whichever event ended it.

## [Durable turn claiming and per-turn context setup](stage-9.md) `stage-9` — 4 files

This stage is the careful “take a ticket and set up the desk” part of the main work loop. A turn is one unit of agent work, such as answering a user message. The system must claim each queued turn exactly once, and it must be able to continue safely if the process crashes halfway through.

queue.py is the durable waiting line. It stores and claims turn records, connects them to the database, sandbox, model, tools, credentials, subagents, and live notifications, and records the final outcome. engine.py is the runner for a claimed turn. It gathers the conversation and workspace context, opens the sandbox, loads skills, calls the model, runs tools, records usage, and commits results without repeating expensive or side-effecting work after a restart.

compaction.py keeps long conversations small enough for the model by replacing older messages with a checked summary while preserving recent messages. __init__.py simply makes this folder importable as a Python package. Together these pieces make turn startup reliable, complete, and ready for the agent’s actual work.

## [Prompt construction and model streaming](stage-10.md) `stage-10` — 7 files

This stage prepares and runs the conversation with the language model during the main work loop. First, the prompt package makes prompt-building code importable. Its render module assembles the final system prompt: the trusted instruction text that tells the model its role, skills, citation rules, contributed extra sections, and knowledge cutoff. It also records a digest, like a fingerprint, so prompt changes can be noticed later.

Before outside text is added, untrusted.py wraps it so the model treats it as quoted evidence, not as new orders. This helps protect against a web page or tool result trying to hijack the agent.

The models package then provides the doors to real model services. The Anthropic bridge converts UFO’s internal message format into Anthropic’s Messages API request, and translates Anthropic’s streaming reply back into UFO’s common event stream. The OpenAI bridge does the same for OpenAI-style Chat Completions or Responses APIs. The OpenRouter extension adds another OpenAI-like route to many models, plus image and video generation tools that save outputs and record cost.

## [Tool registry, tool dispatch, and core built-ins](stage-11.md) `stage-11` — 5 files

This stage is the tool desk for the agent during the main work loop. When the model wants to do something outside plain text, such as read a file or run a command, it must ask for a named tool. The tools package marks this area of the codebase and groups the tool directory, the running context, and the built-in tools.

The registry is like a catalog. It lists each tool, its name, its description, and the shape of the input it accepts, so the model can be shown valid options and unclear names can be rejected. The context is the safety envelope passed to a tool when it runs. It controls what the tool may access, such as files, browser sessions, accounts, credentials, subagents, memory, and billing.

The built-ins are the main workbench: shell commands, file edits, search, sharing, user questions, skills, account connections, and subagent control. The todos extension adds a checklist tool pack, letting agents plan multi-step work and show progress in the UI.

## [Sandbox workspace, filesystem safety, and egress proxying](stage-12.md) `stage-12` — 13 files

This stage is shared behind-the-scenes support for any turn that runs tools. It gives each conversation a private workspace, like a locked workbench, and controls how that workspace touches files and the internet. The conversation module opens or reuses the right workspace, while select chooses the backend: a plain local folder, a Docker container, or an E2B cloud sandbox. The local, Docker, and E2B modules then run commands and move files in those places.

Session is the safe doorway tools use to execute commands and read or write files. Containment checks every untrusted path so tricks like “go up a folder” or symlinks cannot escape the allowed area. Exec_env gives commands fake-looking credential variables, so tools can ask for access without seeing real secrets.

For network access, rules builds the allowed outbound connections and decides when credentials may be brokered. The proxy server enforces those rules, injects secrets only at the gate, and records billable usage. Workspace_changes keeps a compact “what changed?” record using diffs. The package init files simply make these modules importable.

## [Browser automation and web-interaction execution](stage-13.md) `stage-13` — 24 files

This stage is the system’s web browser workbench. It is used during the main work loop when an agent needs to open a site, read it, click buttons, type into forms, upload or download files, or take screenshots.

At the center, the BUA session layer leases a browser for the current turn and defines the allowed actions, like click, type, scroll, and wait. The DevTools transport is the communication cable to Chrome, using Chrome’s control protocol to send commands and receive events. Page inspection tools turn a live web page into readable text, accessibility information, and screen coordinates the agent can use. Action execution tools then turn the agent’s plan into real mouse, keyboard, form, and file actions, while waiting for the page to settle afterward.

Tabs, pop-up dialogs, and downloads are managed separately so browsing does not get stuck or lose track of files. External provider adapters can supply a local sandbox browser, a hosted Chrome session, or a cloud browsing service. Finally, `tools.py` exposes these abilities as agent tools, connecting tool calls to the browser engine underneath.

### [BUA session orchestration and action schema](stage-13.1.md) `stage-13.1` — 5 files

This stage is the shared control layer for browser automation. It is used during the main work loop, whenever the system needs to look at or operate a web page. Think of it as the browser “driver’s seat” plus the rulebook for what commands are allowed.

The core browser contract in `core/src/ufo/browser.py` says how the system should obtain a Chrome connection for a single turn of work, without tying the core code to one specific browser provider. `backend.py` builds on that by giving browser tools a per-turn surface. It opens the browser only when a tool actually needs it, then releases it safely when the turn ends.

`session.py` represents the live browser session itself. It gathers the practical abilities: working with tabs, reading pages, filling forms, handling downloads and dialogs, running JavaScript, and using mouse or keyboard actions. `actions.py` defines the approved action shapes, such as click, type, scroll, screenshot, and wait, so requests are clear before they run. `errors.py` defines a validation error for cases where the model refers to browser state that is not really there.

### [Chrome DevTools transport and runtime bridge](stage-13.2.md) `stage-13.2` — 3 files

This stage is shared behind-the-scenes support for browser automation. It is the “cable and adapter” between the project and Chrome. Chrome DevTools Protocol is Chrome’s control channel for tools and automation. The code here lets the rest of the system talk to that channel without dealing with raw messages.

The cdp.py file manages the live connection. It opens a WebSocket, which is a two-way network pipe, sends numbered commands to Chrome, waits for the matching replies, and passes browser events to the code that subscribed to them.

The wire.py file defines what valid DevTools messages should look like. Since those messages are JSON, or structured text data, this file checks that required fields and types are present. If Chrome sends something unexpected, the problem is caught early with a clear error.

The runtime.py file builds on that transport to run JavaScript inside a page. It wraps Chrome’s Runtime commands and turns JavaScript-side failures into ordinary Python exceptions. Together, these pieces make browser control reliable and safer for higher-level code.

### [Page inspection, accessibility content, and element lookup](stage-13.3.md) `stage-13.3` — 4 files

This stage is shared support for the browser agent’s main work loop. Before the system can click, type, or answer questions about a web page, it must turn the live page into something readable and findable. The content.py tools inspect the current browser page, pull out text and page details, and keep the result small enough to safely send to higher-level commands. The page.py tools do the broader packaging: they turn the live page into a clean structured description for the AI, then later translate any chosen element back into a real place on the screen. The find.py tools work with the accessibility tree, which is the browser’s built-in outline of links, buttons, fields, and text. They search that outline, tidy up AI-written search results, and present matches clearly. The coordinate.py tools handle the ruler and map: they convert between screenshot coordinates seen by a vision model and the actual browser viewport, so a planned click lands where intended. Together, these parts let the system read, locate, and act on page elements reliably.

### [User interaction execution and action fixups](stage-13.4.md) `stage-13.4` — 5 files

This stage is part of the main work loop, where a planned user action is turned into something the browser actually does. It is like the robot hand between the system’s decisions and the open web page. computer.py is the main driver: it receives actions such as click, type, scroll, wait, or screenshot, sends the right Chrome DevTools commands to the tab, then reports back with a new screenshot and warnings. Before that happens, fixup.py checks the action for common small problems, such as trying to type before a field is focused, or waiting without a time. forms.py handles form-specific work, including filling fields and attaching files, and confirms that uploads really reached the page. keys.py translates human keyboard ideas like “Ctrl+A” or typed text into low-level key press and release events, while remembering which keys are held down. settle.py watches for page loading, network activity, and visual updates so the system knows when it is safe to continue.

### [Tabs, dialogs, and downloads management](stage-13.5.md) `stage-13.5` — 3 files

This stage is the browser’s side-effect control desk. It runs behind the scenes while the agent browses, keeping the visible page flow from getting stuck or losing track of state. tabs.py is the traffic controller for browser tabs. It opens and closes tabs, switches between them, and sends navigation requests. It translates higher-level requests into Chrome DevTools Protocol messages, which are the low-level control commands Chrome understands.

dialogs.py watches for pop-up JavaScript dialogs, such as alerts, confirmation boxes, text prompts, and “are you sure you want to leave?” warnings. These dialogs can block the whole page until someone answers. This file chooses a quick response and records the result so the rest of the system knows what interrupted the page.

downloads.py watches for files the browser starts downloading and can steer some content into download form. For example, it helps make PDFs appear as saved files instead of being trapped inside an in-browser viewer. Together, these parts keep browsing actions moving smoothly.

### [External browser providers and hosted automation adapters](stage-13.6.md) `stage-13.6` — 3 files

This stage is behind-the-scenes support for web browsing tasks. Instead of forcing the main system to run and control one local browser, it offers several outside “browser providers,” meaning places where a browser or browser-like agent can run safely and be connected to the system.

The Browser Use adapter connects UFO to a hosted cloud browser agent. It exposes two tools: one to complete a single browsing job, and another to run similar browsing work across many sites. In that mode, the cloud service handles the browser-control loop.

The Browserbase adapter starts a remote Chrome session hosted by Browserbase. It connects UFO to that browser, transfers files in and out when needed, and closes the session afterward so it does not keep running or costing money.

The sandbox Chrome adapter starts Chrome inside the same sandbox environment as the conversation. Together, these adapters act like interchangeable power outlets: the rest of UFO asks for browser access, and the chosen provider supplies it.

## [Specialized creation workflows for code, documents, sites, and briefs](stage-14.md) `stage-14` — 34 files

This stage is a collection of specialist toolboxes the system calls when a task needs more than ordinary chat. It is mostly behind-the-scenes support for making and editing real work products: code, websites, briefs, and Office or PDF documents.

For code work, the GitHub extension connects a workspace to a GitHub App and mints short-lived access tokens, like temporary keys for repositories. The website extension builds site files, runs them safely in a sandbox, and exposes a controlled preview link. The scratchpad and skill-authoring support gives agents reusable notebooks and a way to save their own small tools.

For writing, the brief pipeline turns a request into an outline, draft, and critique. The document review extension records issues and writes them back into PDFs, PowerPoint slides, or Excel files as visible comments. Separate DOCX, PPTX, XLSX, and PDF utilities open these file packages, repair or recalculate them, add comments or form data, render previews, and pack them back up. Together, these parts let agents produce polished artifacts, not just text replies.

### [Coding extension and GitHub App repository access](stage-14.1.md) `stage-14.1` — 3 files

This stage is the bridge between a workspace and GitHub when the system needs to work with code repositories. It is used during setup, when an admin connects the workspace to a GitHub App, and later behind the scenes whenever the system needs temporary access to clone or write to a repository.

The package marker file, __init__.py, is like the label on a toolbox. It tells Python that these coding extension files belong together and can be imported by the rest of the project.

connect.py handles the connection ceremony. It builds the link that sends an admin to GitHub, receives the return request from GitHub, checks that the signed-in GitHub user is allowed to use the selected App installation, and then records that installation for the workspace.

github_app.py uses that saved installation to create a short-lived Git token. This token works like a temporary key, letting the system access repositories without keeping a permanent user password or secret.

### [Website building and hosting extension](stage-14.2.md) `stage-14.2` — 2 files

This stage is a behind-the-scenes support package for website work. It gives the system a safe way to create a site, run it in a local sandbox, and share it through a hosted link. A sandbox is an isolated workspace, like a test kitchen, where the website can run without touching the rest of the system.

The __init__.py file is the package label. It tells Python that ufo_ext_sites is a group of importable code files. It does not do any work by itself, but it lets the rest of the system find the website tools.

The real machinery is in tools.py. It defines the actions an agent can call: build the website files, start a local web server to preview them, and connect that server to an outside link. It also checks file paths, port numbers, logs, and sharing settings so the system exposes only what it should. Together, these parts turn website creation into a controlled, repeatable workflow.

### [Agent scratchpad and skill-authoring extension support](stage-14.3.md) `stage-14.3` — 4 files

This stage is shared support that helps an agent keep useful tools and scratch work across a runtime turn. It is not the main reasoning loop itself. Instead, it adds small “extensions,” meaning optional add-on packages, that the agent can call when it needs a workspace or wants to save a new skill.

The skill creation package starts with an __init__.py file that simply marks the folder as a Python package and identifies it as support for agent-authored skills. Its manifest defines the skill_create extension: it tells the system how to save, list, inspect, delete, and reload skills that an agent has written. The store file does the actual filing work. It keeps each saved skill tied to the current workspace and agent, like labeled drawers, so different agents do not mix up or overwrite each other’s tools.

The REPL extension adds two persistent scratchpads, one for JavaScript/Node.js and one for Python Excel work. These act like reusable notebooks during a session and advertise related data-analysis skills when needed.

### [Brief-writing pipeline extension](stage-14.4.md) `stage-14.4` — 2 files

This stage adds a small, optional writing workflow to the system. It is not the main work loop by itself. Instead, it is shared behind-the-scenes support that a parent agent can call when it needs to produce a structured brief.

The package marker file, __init__.py, is like a label on a folder. It tells Python that this directory is an importable package and gives the extension a short description. It does not run the brief process or make decisions.

The real setup lives in pipeline.py. This file describes a three-part writing machine. First, the outline step turns the request into a structured plan. Second, the draft step uses that plan to write the brief. Third, the critique step reviews the draft and points out weaknesses or improvements. For each step, the file defines what information goes in, what should come out, the prompt that guides the language model, and limits that keep the work bounded. Together, these definitions let another agent run the brief workflow in a clear order.

### [Document extension and review annotation workflow](stage-14.5.md) `stage-14.5` — 8 files

This stage is shared support for the document review skill. It is not the main reviewer itself; it is the toolbox that records what the review found and writes those findings back into finished documents so people can see them in familiar apps.

Two small package marker files make the document extension and its scripts folder importable by Python. They are like labels on drawers, so other code can find what is inside. The constants file keeps the agreed names for the review state file and the review log file, so every script looks in the same place.

The models file defines what a “review issue” looks like, such as the problem text and where it belongs, and turns it into a readable comment. The manage_state command-line tool keeps the review’s running notebook: progress, claims, issues, final summary, and log entries, saved as JSON. The annotation scripts then use that saved state. One adds highlights and sticky-note comments to PDFs. One writes comments into PowerPoint slides. One copies an Excel workbook and adds the issues as cell comments.

### [Word DOCX package and change/comment tools](stage-14.6.md) `stage-14.6` — 4 files

This stage is a small toolbox for working with Microsoft Word DOCX files behind the scenes. A DOCX file is really a zipped package of many XML files, where XML is structured text that describes the document. These tools let the system open that package, change it safely, and put it back together.

The workflow often starts with accept_changes.py, which asks LibreOffice to open the Word file invisibly and accept all tracked edits, producing a clean version. Then unpack.py turns the DOCX into a normal folder so its XML parts can be inspected or edited. It also simplifies the main document XML to make later work more predictable.

comment.py adds a Word comment to that unpacked folder. Word stores comments in several connected XML files, so the script updates those records and then tells the user what marker still must be inserted around the exact text being commented on. Finally, pack.py zips the folder back into a working DOCX file and tidies XML spacing without changing the document’s real content.

### [PowerPoint PPTX package, repair, and slide tools](stage-14.7.md) `stage-14.7` — 5 files

This stage is a set of behind-the-scenes tools for working with PowerPoint .pptx files. A .pptx is really a zipped package of many smaller files, including XML files, which are text files that describe slides, shapes, text, and links. These scripts let the system open that package up, adjust it, fix it, and close it again.

The empty __init__.py file simply tells Python that this scripts folder can contain importable tools. unpack.py is the “open the box” step: it expands a .pptx into a normal folder and formats the XML so it is easier to inspect or edit. pack.py is the matching “close the box” step: it rebuilds the folder into a .pptx and removes extra XML spacing without changing slide text. repair.py fixes known problems in generated presentations, especially ones made by pptxgenjs, so PowerPoint accepts them cleanly. slides.py is a small toolbox for practical slide work: removing unused package files, adding slides, and making thumbnail contact sheets for quick visual review.

### [Excel XLSX recalculation tools](stage-14.8.md) `stage-14.8` — 3 files

This stage is a support tool for working with Excel .xlsx files after they have been created or changed. It is not the main user-facing work loop. Instead, it runs behind the scenes when the system needs spreadsheet formulas to be up to date and checked for obvious problems.

The scripts folder is made importable by __init__.py. That file is like a label on a toolbox: it does not do work itself, but it lets other Python code find the tools inside. The _soffice.py helper knows how to start LibreOffice in “headless” mode, meaning it runs without showing a window. It also knows where LibreOffice keeps user macro files on Linux and macOS, so scripts can find the right support folders. The recalc.py script is the main worker. It opens the workbook in LibreOffice, tells it to recalculate every formula, saves the updated file, and then looks for common spreadsheet error values. Together, these files turn LibreOffice into an automated checker and refresher for Excel workbooks.

### [PDF form, layout, and rendering utilities](stage-14.9.md) `stage-14.9` — 3 files

This stage provides the PDF toolbox used when the system needs to inspect, fill, preview, or render PDF documents. It is not the main work loop by itself. Instead, it is behind-the-scenes support that other parts of the project can call when a PDF must be turned into something easier to understand or modify.

The formfill tool works with PDFs that already contain native fillable fields, like digital boxes for names, dates, or checkmarks. It can check whether those fields exist, export their names and details to JSON, which is a simple text format for structured data, and fill the PDF using values from that JSON.

The layout tool is for PDFs that only look like forms. These have no real fields, just page graphics. It scans the page for layout clues, can draw a preview of where fields should go, and can add text annotations onto the PDF.

The render tool converts PDF pages into PNG images, so pages can be viewed or processed like ordinary pictures.

## [Subagents and delegated multi-agent workflows](stage-15.md) `stage-15` — 11 files

This stage is shared support for delegation during the main work loop. It lets one agent act like a manager: choose a specialist helper, give it a clearly shaped task, wait for it, cancel it if needed, and safely read its result. The core profile file defines the default “general purpose” helper. The catalog file keeps an up-to-date menu of available helpers, so the parent agent knows what kinds of tasks each one accepts. The main subagents file creates child conversations, runs them, checks their inputs and outputs, and blocks unsafe or badly formed replies. The delivery file is a backup courier that returns child results even if the normal handoff was interrupted.

Extensions add specialist workers. Browser files define and launch browser helpers, including parallel web visits. Research files define normal and deep research helpers, plus a tool that splits broad research across many children and saves results. Documents adds a writing helper. Sites adds a website-building helper and a tool that connects “build a site” requests to that child worker.

## [External connectors, account brokering, and source synchronization](stage-16.md) `stage-16` — 78 files

This stage is shared behind-the-scenes support for safely working with outside services. It has two big jobs: let agents use third-party tools without seeing private tokens, and keep outside business records synced into the system.

The core connector and account grant layer is the guarded front desk. It tracks which user connected which account, what an agent may use, and how access can be shared or revoked. Composio and Pipedream are trusted middlemen that provide consent links, tool catalogs, remote tool execution, and proxied web requests, so secrets stay outside the sandbox. The MCP and evaluation pieces make the same connector path work for remote tool servers and predictable test connectors.

The source synchronization framework is the intake engine. It defines how connectors fetch records, remember progress with cursors, retry failures, store pages, handle deletes, and publish changes. The many source connectors are translators for specific services: productivity tools, support systems, CRMs, ads platforms, finance, HR, recruiting, and operations apps. Each reads allowed data from its service and reshapes it into one common format.

### [Core connector brokering and account grants](stage-16.1.md) `stage-16.1` — 9 files

This stage is shared behind-the-scenes support for letting agents use outside services without handing them raw secrets. It acts like a guarded front desk between the workspace, the agent, and services such as Gmail, GitHub, Slack, or brokered tools.

The core connector code defines that boundary. It makes sure connector calls can get the credentials they need, while keeping passwords, API keys, and tokens out of agent prompts, sandboxes, and logs. The credentials code stores those secrets safely and creates short-lived private authorization requests when a user must enter or approve one. The grants code records which workspace member connected an outside account and which agent is allowed to use it, including sharing, revoking, and disconnecting access.

The connector extension turns those accounts and permissions into normal workspace objects, so users can inspect and manage them consistently. Its tools file gives agents the live doorway to discover connectors, see available actions, call them, and move files safely. The MCP extension adds the same kind of tool access for Model Context Protocol servers. The evaluation extension provides fake mail, calendar, and code-search connectors so tests can run predictably through the same path.

### [Composio brokered tool integration](stage-16.2.md) `stage-16.2` — 7 files

This stage is behind-the-scenes support for using external app tools through Composio, a service that connects many apps through one doorway. It helps during setup, when a user connects an account, and during the main work loop, when the agent discovers and runs tools.

The package marker file simply makes this extension loadable by Python. The resolver turns a Composio slug, such as “github,” into connector details, so the system does not need a separate built-in entry for every app. The provider handles the browser consent step, similar to “sign in with Google,” where the user approves access without sharing passwords with UFO.

The client talks to Composio’s API to create consent links, check accounts, search tool catalogs, upload files, and run tools. The broker is the main adapter: it presents Composio tools in the shape UFO expects, including inputs, outputs, files, and safe authenticated calls. The MCP session makes a single remote Tool Router call and simplifies the reply. The proxy rewrites ordinary web requests so Composio adds the secret credential on its servers, then returns a normal response.

### [Pipedream brokered tool integration](stage-16.3.md) `stage-16.3` — 5 files

This stage is the Pipedream connection layer. It is shared behind-the-scenes support used when a user connects an outside account, when the system looks up available actions, and when it runs those actions. Pipedream acts like a trusted middleman, so the project can use services such as Gmail without storing the user’s private provider token.

The client file is the main doorway to Pipedream Connect. It creates browser consent links, checks that connected accounts belong to the right user, lists actions Pipedream offers, and asks Pipedream to run them. The provider file plugs this into UFO’s normal account-connection flow, sending the user to Pipedream’s hosted OAuth page; OAuth is the standard “sign in and grant access” process. The broker file connects Pipedream actions to UFO’s tool system, so actions can be discovered, described, executed, and returned with any produced files. The proxy file rewrites ordinary service HTTP calls so they pass safely through Pipedream instead of exposing tokens. The package file simply makes these pieces importable.

### [Source synchronization framework and registry](stage-16.4.md) `stage-16.4` — 9 files

This stage is shared behind-the-scenes support for syncing outside content into the system. It is the framework that lets services such as GitHub, Zendesk, or other APIs all plug in the same way, even though each service has its own rules.

The package marker files simply make the source folders importable by Python. The connector module defines the basic agreement every connector must follow, including how progress is tracked with “cursors,” which are bookmarks for where the last sync stopped. The REST helper gives API-based connectors common tools for logging in, retrying failed requests, and moving through paged results. The backend module translates a connector’s raw records into the system’s standard sync output.

The sync module is the main engine. It fetches pages, stores updates, removes deleted content, retries failures, and produces a change feed for indexers. The tools module lets users create, view, edit, delete, and watch synced sources. The direct module supports API-key login. The registry is the address book that maps each source name to its connector class.

### [Productivity, collaboration, and work-management source connectors](stage-16.5.md) `stage-16.5` — 20 files

This stage is a set of read-only “source connectors.” They run during sync, when the system gathers outside work information and turns it into searchable pages. Each connector knows one service’s layout and API, meaning the web doorway the service provides for reading data.

Airtable reads bases, tables, and records. Asana, ClickUp, Jira, Linear, monday.com, and Wrike read project spaces, tasks or issues, comments, users, boards, goals, and related work details. Calendly and Google Calendar read scheduling data, including events, invitees, memberships, and attendees. Confluence, Google Docs, Google Drive, Google Meet, Google Sheets, and Notion turn documents, pages, files, meeting notes, spreadsheets, comments, and revisions into plain readable text. GitHub reads repositories, issues, commits, users, and other development records. Gmail and Outlook read mail, contacts, calendars, folders, and change feeds so later syncs can fetch only updates. Slack and Microsoft Teams read people, channels, chats, messages, and threads.

Together, these files act like translators at many office doors: they only look inside, collect allowed records, and reshape them into the system’s common page format.

### [Customer support, CRM, marketing, ads, and observability source connectors](stage-16.6.md) `stage-16.6` — 15 files

This stage is a set of read-only “source connectors.” A connector is an adapter that knows how to talk to another company’s web service through its API, the doorway that software uses to request data. These files sit behind the scenes during syncing. They do not run the product’s main work themselves, and they do not change outside systems. They fetch pages of records and reshape them into a common form the rest of the codebase can store, search, or recall.

The CRM connectors cover ActiveCampaign, Attio, HubSpot, and Salesforce, pulling contacts, companies, deals, tasks, cases, and related activity. The advertising and social connectors read Facebook Ads, Google Ads, and Instagram accounts, campaigns, posts, stories, and performance metrics. Freshdesk, Intercom, and Zendesk bring in support tickets, users, conversations, help articles, and community data. Klaviyo and Mailchimp read marketing audiences, campaigns, events, and reports. Typeform collects forms and responses. PagerDuty brings in incidents, services, schedules, and on-call data. Sentry reads error-tracking projects, issues, events, and releases. Together they act like many intake pipes feeding one shared sync system.

### [Finance, billing, HR, recruiting, and operations source connectors](stage-16.7.md) `stage-16.7` — 13 files

This stage is a set of read-only “connectors,” which are small adapters that know how to fetch data from outside business tools without changing it. It is behind-the-scenes support for the sync system: each connector logs in to a service, asks its web API for records, follows page-by-page results, and reshapes them into standard streams the rest of the project can process.

The recruiting connectors cover Ashby, Greenhouse, and Recruitee, pulling candidates, jobs, applications, interviews, offers, and hiring lookup data. The HR connectors cover BambooHR, Deel, and Rippling, bringing in employee records, contracts, payslips, teams, workers, tasks, and related data. The finance and operations connectors cover Brex for spend data, QuickBooks and Xero for accounting, Chargebee and Recurly for subscription billing, and Stripe and Square for payments, orders, customers, invoices, inventory, and commerce records. Together, these files act like different plug adapters for different wall sockets: each understands one vendor’s shape, but all deliver records in the same usable form.

## [Retrieval, research, memory, indexing, and embeddings](stage-17.md) `stage-17` — 16 files

This stage is the system’s recall library. It works behind the scenes while conversations run, so agents can find saved facts, synced pages, and web evidence instead of relying only on the current prompt. The core search and memory files define common “plug shapes”: one for web search and fetching, one for memory lookup, and one for splitting text into searchable chunks. Different backends can then fit into those shapes. The default index stores text chunks locally, while Turbopuffer can store and search them in an external service. OpenAI embeddings turn text into number lists that capture rough meaning, so searches can match ideas, not just exact words. The memory store saves durable facts, indexes them, and searches them later. Its condenser turns raw pages or old facts into cleaner long-term memories, while memory objects, events, and package files define safe access and shared names. Synced source pages provide read-only documents that can become memories. For web research, Exa supplies search and page fetching, research tools expose that to agents, and observations save found sources so a conversation can show its evidence later.

## [Long-running automation, objectives, monitors, billing, and self-improvement](stage-18.md) `stage-18` — 22 files

This stage is the system’s long-term memory and alarm clock. It supports work that must continue after one chat turn ends: scheduled jobs, paused conversations, watched changes, multi-step objectives, billing, and offline prompt improvement.

Scheduled task files let agents create future or repeating work. The cron helper checks schedules and finds the next run time. The schedules store records what should run and lets workers safely claim it. The pauses store wakes a conversation later, without firing the same timer twice. The tools file exposes these actions to agents, while the conversation slot shows a short safe summary of active automations.

Monitors and source-change wakeups act like sensors. They rerun saved checks or notice source updates, then resume the right conversation when something changes. Objectives keep durable plans, steps, blockers, and completion checks so progress can be verified, not just claimed.

Metronome connects usage reporting and billing, with Stripe used for payment setup. Self-improvement runs in the background, replaying past failures, testing prompt changes, and only proposing careful, governed updates.

### [Monitors and source-change wakeups](stage-18.1.md) `stage-18.1` — 4 files

This stage is behind-the-scenes support for waking a conversation when the outside world changes. Instead of making the agent keep checking manually, it sets up watches and trigger records, like alarm clocks tied to real events.

The monitor tool is the front door. When an agent asks to watch something, it runs a shell command once inside the conversation’s safe workspace. This first run proves the command works and records the starting result, so later checks have something to compare against. The monitor kind file teaches the system how monitors are displayed, inspected, and stopped. It is the control panel, not the creator.

The monitor runner is the timer-driven worker. It wakes up on a schedule, reruns saved monitor commands, compares the new output with the baseline, and notifies the agent if the output changes, failures repeat, or the deadline is reached.

Source triggers are similar wake-up rules for shared project sources. They record which conversations care about which source changes, so those conversations can be resumed when the shared material is updated.

### [Durable objectives and checked progress](stage-18.2.md) `stage-18.2` — 3 files

This stage is the project’s memory and gatekeeper for long-running work. It is used during the main work loop, when an agent needs to plan a goal, split it into steps, hand work to others, or check whether progress is real. Instead of trusting a worker’s short-term memory or a simple “done” message, it keeps a durable record in the database and rechecks the stated conditions before marking a step complete.

The package marker, __init__.py, simply labels this extension so the larger system can recognize it. The store.py file is the filing cabinet. It saves and retrieves objectives, their steps, attempts to complete them, blockers, and condition checks. The tools.py file is the front desk. It exposes the actions agents can use to create plans, inspect status, delegate tasks, report blockers, and request completion checks. Together, the tools decide what should happen, while the store preserves the facts so later agents can continue from reliable records.

### [Prompt self-improvement and governed prompt changes](stage-18.3.md) `stage-18.3` — 9 files

This stage is behind-the-scenes maintenance, not part of the live chat loop. Its job is to learn from past agent mistakes and suggest safer, better system prompts, which are the standing instructions that guide an agent’s behavior. It does this offline so it does not disturb users or change the outside world.

The self-improvement extension defines this review process. The corpus builder collects old conversations where tools failed, groups them by the failing tool, and keeps some examples for learning and some for testing. The model adapter gives the extension a simple way to ask a language model for suggestions or judged answers. The proposer uses the failure examples to draft a new prompt, but only if it is truly different.

The replay code reruns saved tasks with old tool results fixed, like testing a new driver on a closed track. Evaluation compares the old and new prompts, while the gate uses cautious statistics to reject weak or risky changes. The cron job runs this regularly. If a change keeps passing, governance opens a human-approved proposal and applies it only if the original prompt has not changed meanwhile.

## [Reply delivery, live updates, panels, artifacts, and hosted outputs](stage-19.md) `stage-19` — 8 files

This stage is the return path from the system back to the person watching it. As a conversation turn runs, it turns internal work into visible updates, then makes finished results easy to open, share, and reuse. activity.py writes short human-friendly status messages, such as when a tool is about to run. hub_tail.py keeps the live update stream dependable, combining quick in-memory messages with database checks so late viewers still see the turn finish.

The other files deal with outputs that live beyond a single message. artifacts.py makes generated shared files show up as reusable workspace items. The sandbox ingress files protect hosted outputs: ingress_host.py creates valid web addresses for sandboxed sites, and ingress_token.py checks short-lived signed passes for opening a specific port. The Sites extension then presents these hosted results. conversation_slot.py fills the side panel with sites the viewer may access. objects.py makes sites manageable as workspace objects, including visibility changes and unhosting. surface.py serves the public site page, checks permission, and displays the site safely inside a protected frame.

## [Cancellation, teardown, cleanup, and crash recovery](stage-20.md) `stage-20` — 2 files

This stage is the system’s safety and cleanup crew. It is used when a piece of work is cancelled, when a server process stops, or when a crash leaves unfinished work behind. Its job is to make sure the system does not keep wasting resources, does not leave child tasks running after a parent stops, and does not confuse old abandoned work with work that is still alive.

`cancellation.py` provides the careful order for cancelling one “turn,” meaning one unit of user work. First it tells the running workflow to stop. Only after that does it mark the turn as cancelled in the database, so the record matches what actually happened.

`runtime_instance.py` keeps track of which server processes are alive, like a sign-in sheet for workers. Other parts of the system can use this to tell whether a turn is truly active or was left behind by a crashed process. It also helps recover abandoned turns and passes cancellation from parent turns to their child turns, so related work shuts down together.

## [Cross-cutting persistence, schema, storage, and durable records](stage-21.md) `stage-21` · (cross-cutting) — 32 files

This stage is the system’s long-term memory and filing system. It is shared behind-the-scenes support, used during startup, upgrades, normal work, and recovery after restarts. Its job is to make sure important records are stored in agreed shapes and can still be read as the code changes.

The core persistence layer provides the basic plumbing: database connections, table blueprints, migrations, workspace separation, and blob storage for large files. On top of that, transcript storage saves conversation history safely, including protections so an old copy cannot overwrite a newer one.

Several parts are upgrade paths. Coding review and source-trigger migrations move older review records into a shared trigger model. Index and evaluation migrations add search storage and test inbox/calendar tables. Memory migrations build the tables for remembered facts, page snapshots, source links, confidence, audiences, and fast inventory lookup.

Finally, durable feature stores keep records for monitors, objectives, and hosted sites. Together these pieces act like labeled drawers in one filing room, so every feature can save, find, upgrade, and protect its data consistently.

### [Core persistence infrastructure and shared schema](stage-21.1.md) `stage-21.1` — 5 files

This stage is shared behind-the-scenes support for anything that needs durable storage. It is not one user-facing workflow. Instead, it provides the common “plumbing” that other parts of the system rely on when they save files, read database records, or recover long-running work.

The blob module is the file cabinet for large chunks of bytes. In development those bytes may be local files, while in production they may live in S3-style cloud storage. The rest of the code uses the same async calls either way, so it does not need to know where the bytes are physically stored.

The database module is the guarded front door to the database. It creates connection pools, runs transactions, applies migrations, and keeps workspace data separated unless trusted owner-level access is requested.

The durability module helps DBOS reload saved Python workflow state safely, even when Pydantic data models have changed between versions.

The schema package marker gives schema code a clear import home. The tables module is the shared blueprint for the database tables, columns, links, and rules used by SQLite and Postgres.

### [Durable conversation transcript storage](stage-21.2.md) `stage-21.2` — 2 files

This stage is shared behind-the-scenes support for keeping conversation history safe after it leaves memory. It defines what a saved transcript looks like and provides the main doorway used to read and write it in durable storage, meaning storage that should survive beyond one running process.

The shared format lives in core/src/ufo/transcript.py. It is like the agreed form everyone must fill out. It defines the keys, record shapes, and conversion rules used for saved conversations and for compaction records, which are shortened summaries or reorganized versions of older transcript data. Because all parts of the system use the same definitions, a writer and a reader do not accidentally disagree about what a record means.

The safe storage doorway lives in core/src/ufo/loop/transcript.py. It loads and saves transcript records through the storage layer, but with protection against stale writes. If one part has an older copy, it cannot overwrite a newer version. Together, these files make transcript storage consistent and safe.

### [Coding review and source-trigger migrations](stage-21.3.md) `stage-21.3` — 6 files

This stage is behind-the-scenes upgrade work for the database. A database migration is a small step that changes stored data and table shapes when the system is installed or updated. Here, the coding review feature and the source-trigger system are brought into the same newer model.

The first coding migration creates the original review inbox: a record of code sources waiting for review and review runs already started. The second adds a link from each review run to the conversation that created it, so the system can trace a review back to its discussion. The third loosens the old design by removing the direct conversation column from the inbox. The fourth is the bridge: it moves old review inbox records into the newer source-trigger conversation system, then drops the old review-only tables.

The sources migrations build that newer system. The first creates a structured source_trigger table and moves older subscription data into it. The second adds a delivery field, which says how each trigger should be delivered. Together, these steps preserve old data while shifting reviews onto the shared trigger mechanism.

### [Index and evaluation-environment migrations](stage-21.4.md) `stage-21.4` — 3 files

This stage is part of setup and upgrade, not the everyday work loop. It prepares extra database storage used by optional extensions. A database migration is a small step that creates or changes tables so newer code has the right places to save data.

The evaluation-environment migration creates tables for a fake inbox and calendar. These are used when the system is being tested or evaluated, so each workspace can have its own sample emails and calendar events.

The indexing migrations prepare storage for searchable text. The first one creates a table of “chunks,” meaning small pieces of text split out from larger content so search can find them quickly. It also adds database indexes, which are like lookup tabs in a book, for subject searches, full-text searches, and PostgreSQL vector similarity searches, which find text with similar meaning.

The second indexing migration tightens the design by attaching each chunk to a workspace. That prevents two workspaces with matching chunk fingerprints from being mixed up.

### [Memory item schema and inventory migrations](stage-21.5.md) `stage-21.5` — 6 files

This stage is behind-the-scenes setup for the memory extension. It is made of database migrations, which are small upgrade steps that change the shape of stored data as the system grows. Together they build and refine the main table where memories live.

The first migration creates the memory table, giving each workspace a place to store remembered facts or experiences. Later, the memory_kind migration adds labels for what type of memory an item is, plus a confidence value showing how sure the system is. The as_of migration adds an optional time field, so a memory can say what moment it refers to, not just when it was saved. The room_audience migration broadens who a memory can be meant for: everyone, one member, a room, or an external room-like target.

Two migrations make reading this data faster. The consolidation index helps background cleanup find older active facts that may need merging. The inventory index helps the memory inventory page list one workspace’s items quickly, newest first.

### [Memory page, provenance, and source-partition migrations](stage-21.6.md) `stage-21.6` — 6 files

This stage is part of upgrading the memory extension’s database. It does not run the everyday memory work itself. Instead, it reshapes the stored data so later code can read and rebuild memory facts more accurately. First, 0002 creates a table for memory pages, giving the system a place to store page snapshots. Then 0004 ties each page directly to a workspace, so pages clearly belong to the correct project area. 0008 backfills missing “as of” times, meaning the time a memory fact should be considered true, by copying dates from the page it came from. 0009 adds a proper page link for provenance, or “where this came from,” and moves old loose text references into that link when safe. 0010 adds page revision tracking, so the system knows which exact version of a page produced a memory item, then clears older derived results so they can be rebuilt correctly. Finally, 0012 lets one memory item point to multiple source pages, making origins more flexible and less duplicated.

### [Durable feature records and hosted-site stores](stage-21.7.md) `stage-21.7` — 4 files

This stage is shared behind-the-scenes support. It gives higher-level features a durable memory, so important records survive restarts and can be safely shared by different workers. A “migration” is a small setup script that creates or changes database tables, like adding labeled drawers before the system can store paperwork in them.

The monitor migration creates the drawer for scheduled monitor checks: what workspace, conversation, and agent they belong to, when they should run, and how far they have progressed. The monitor storage code then uses that table day to day. It defines monitor records and provides safe ways to create, claim, update, and delete them, including protection when multiple runners might try to work on the same monitor.

The objectives migration creates tables for goals, their steps, progress evidence, and satisfaction checks. This lets the objectives feature remember both plans and proof of progress.

The hosted-site store keeps the live site registry: owners, names, sandbox ports, creators, and viewers. It prevents site-name takeovers and lets permanent links resolve to the right running site.

## [Cross-cutting SDK, protocols, contracts, and type vocabulary](stage-22.md) `stage-22` · (cross-cutting) — 40 files

This stage is shared behind-the-scenes support. It is the system’s common dictionary and rulebook, so the core program, extensions, models, browsers, sources, and accounting code all describe the same things in the same way.

The core contracts define the real shapes used inside UFO: what an extension is allowed to see, what it promises in its manifest, how conversation side panels are described, how AI model requests and streamed replies look, and how a conversation “turn” is recorded.

The public SDK facades are stable front doors for extension authors. One group exposes extension building blocks such as tools, jobs, skills, HTTP routes, sandbox behavior, logs, and metrics. Another group exposes connectors, credentials, grants, bearer-token checks, browser links, terminal transport, and operator sessions. A third group exposes model, search, memory, indexing, and accounting vocabulary. The last group exposes workspace and user-facing concepts such as audiences, seats, hubs, listings, objects, scheduled actions, surfaces, and untrusted text.

Together, these parts let outside extensions and internal code plug into UFO without depending on fragile private details.

### [Core extension, model, object, and turn contracts](stage-22.1.md) `stage-22.1` — 6 files

This stage is shared behind-the-scenes support. It defines the basic “forms” and rules that many other parts of UFO rely on, so the core system, extensions, background workers, and user screens all agree on what things mean.

The extension context is the safe toolbox given to an extension or background job. It limits that code to the current workspace’s approved data, credentials, conversations, models, sources, and transcripts. The manifest file is the extension’s menu of promises: it describes what the extension adds, such as tools, jobs, routes, hooks, credentials, or onboarding steps. Conversation slots define the side panels an extension can show next to a chat, like files, tasks, sources, or workspace changes.

The model interface gives UFO one common way to talk to different AI providers. It standardizes requests, streamed replies, images, reasoning text, and tool calls. The object name helper enforces one rule for safe names before they are stored or linked. The records schema defines conversation “turns,” meaning one unit of work, so queues, workers, and user views all track the same states and results.

### [Public SDK extension authoring and runtime facades](stage-22.2.md) `stage-22.2` — 10 files

This stage is the public front door for people writing UFO extensions. It is shared support used while an extension is loaded and while it runs. Most files here are “facades”: thin, stable import points that hide the project’s private folder layout, so extension code can keep working even if internals move.

The package marker makes ufo.sdk importable. The manifest doorway exposes the types and limits used to describe an extension. The context doorway gives extensions the scoped information they receive from the host while running. The HTTP toolbox provides safe request, response, form, upload, and cookie helpers for routes. The tools, jobs, skills, and sandbox doorways expose the building blocks for user-callable tools, background work, skill definitions, and controlled execution behavior. The observability doorway lets extensions write logs and metrics, meaning structured signals about what happened, using names the core system understands.

The sample extension ties the machine together. It declares examples of these features and records results, proving the public SDK can be used as intended.

### [Public SDK connector, credential, and transport facades](stage-22.3.md) `stage-22.3` — 9 files

This stage is shared behind-the-scenes support for people writing extensions. It does not run the main work itself. Instead, it provides stable “front doors” into the SDK, so outside code can import approved tools without depending on deeper internal paths that may change.

The connector and source doors work together for adding new data inputs. connectors.py exposes the pieces needed to register connector providers and OAuth-style sign-in flows, while sources.py exposes the types and errors used to sync content sources such as REST APIs. authproxy.py and credentials.py cover the handoff of login details and stored credentials, giving extensions only the credential tools they are meant to use. grants.py exposes grant and connection audit types, so extensions can refer to permission records in a stable way. bearer.py exposes token verification only, meaning code can check bearer tokens without gaining access to token creation secrets.

The transport doors cover how extensions talk to users or outside environments. browser.py exposes browser connection interfaces, terminal.py exposes terminal transport and storage pieces, and operator.py exposes helpers for operator-only web sessions.

### [Public SDK model, retrieval, memory, and accounting facades](stage-22.4.md) `stage-22.4` — 5 files

This stage is shared support for extension authors. It does not run the main work of searching, remembering, calling models, or calculating costs. Instead, it creates stable “front doors” in the public SDK, so outside code can use the project’s approved interfaces without depending on private internal file paths that may change.

The models facade gathers the main model-facing pieces in one place: message shapes, model client interfaces, pricing and specification types, and helper functions. The index facade exposes the contracts needed to plug in a search index or embedding service, where embeddings are numeric representations used for finding similar content. The search facade publishes the search-related types that tools and extensions use to ask for and return search results. The memory facade does the same for memory-search provider types, which are used to look up stored past information. The accounting facade publishes the project’s spending and accounting vocabulary, so extensions can describe cost-related data consistently. Together, these files act like a clean reception desk for the SDK.

### [Public SDK workspace, surface, visibility, and object facades](stage-22.5.md) `stage-22.5` — 10 files

This stage is the public front door of the SDK, the software kit used by extensions and outside code. It is shared support, not a main work loop. Its job is to hide the project’s internal layout and give users steady import paths that do not change when the inside is reorganized.

Each file is a small facade, like a labeled service window. audience.py exposes audience tools, while subjects.py exposes the standard names used to describe who can see a piece of data. seats.py re-exports seat objects and helpers. hub.py gathers the hub protocol, live event types, and in-process hub, so extensions can talk to the event system. listings.py provides listing and paging helpers for returning results in chunks. objects.py exposes object types and helpers. scheduled_fire.py publishes helpers for scheduled actions. surface_token.py offers tools for creating and checking permanent surface link tokens. surfaces.py gathers the main contracts for surface extensions. untrusted.py lets extensions mark outside text as untrusted in the same way the core system does.

## [Cross-cutting configuration, catalogs, packs, credentials, and model/provider metadata](stage-23.md) `stage-23` · (cross-cutting) — 9 files

This stage is the system’s shared settings and catalog layer. It is read mainly at startup, so mistakes are caught before real requests run, but its information is used everywhere afterward. It acts like the label board and key cabinet for the rest of the machine.

The main configuration file defines what a deployment is allowed to look like and loads it from one TOML settings file, which is a simple structured text format. A small file-change setting gives all code one shared path-length limit. The model files work together to describe, collect, and expose AI model choices: the model “spec” defines the standard record for a model, the catalog lists built-in models and how to call them, the registry turns all model definitions into one clear lookup table, and the catalog skill lets users ask what models are available. The Bedrock extension adds Amazon Bedrock models in the same format. The extension store manages adding or removing extension pins from the lockfile. The keyed connectors extension defines API-key based services, including which secrets to request and where they may safely be sent.

## [Cross-cutting accounting, observability, live hubs, and operational utilities](stage-24.md) `stage-24` · (cross-cutting) — 10 files

This stage is shared operational support that runs behind the scenes across the whole system. It helps the product measure work, show live progress, and stay safe for operators and users.

The accounting pieces act like the system’s cash register. accounting.py records model usage, checks spending limits, prepares billing data, and builds reports. balance.py keeps a fast prepaid balance for each workspace, so paid work can be approved without rereading every past payment. pricing.py stores model rates, turns token use into cost, and stamps records with the exact price version used.

The live update pieces keep people informed while an agent turn is running. hub.py broadcasts text, tool activity, costs, and final status to a command line or web page, with recent history for reconnects. The Redis hub package lets those updates pass between server processes through Redis Streams.

The remaining utilities support operations. listings.py provides safe cursor-based paging. seed.py creates a rich demo conversation for the portal. token_signing.py makes tamper-proof signed tokens. o11y.py records traces, metrics, and logs while hiding sensitive or oversized data.
