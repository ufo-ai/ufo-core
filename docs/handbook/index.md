# System Handbook — Stage Index

Each stage below links to its full page; the paragraph is the stage's role in the system.

## [Deployment Preparation and Database Upgrade](stage-1.md) `stage-1` — 89 files

This stage happens during deployment, before the service starts handling normal user traffic. It is like checking a building, updating its floor plan, and making sure every room is usable before opening the doors. First, the runtime readiness checks confirm the control database exists and matches expectations, build or validate the sandbox image where user code will run, and test that secure proxy traffic works with the right certificate.

Next, the Alembic harness runs database migrations. Alembic is a tool that applies database changes in order, so stored data keeps matching the code. The core migrations create and upgrade the main tables for workspaces, agents, conversations, turns, credentials, proposals, memory links, sources, pages, ledgers, runtimes, exports, scheduled jobs, grants, connections, and audits. These changes make the system’s main records traceable, permission-aware, and ready for newer features.

Finally, extension migrations prepare add-on storage, such as memory records, searchable text chunks, test email and calendar data, hosted sites, notes, skills, and web metadata. Together, these steps make both the service runtime and its databases safe to use.

### [Deployment Runtime Readiness Checks](stage-1.1.md) `stage-1.1` — 3 files

This stage happens before the service is allowed to take real traffic. It is a set of “ready or not” checks, like inspecting a kitchen before opening the restaurant. The goal is to catch missing databases, mismatched runtime images, or broken secure network paths early, while deployment can still stop safely.

The control database check in `schema.py` prepares and verifies the PostgreSQL database, which is the service’s shared record book. PostgreSQL is the database system; the “schema” is the expected set of tables and fields. This check makes sure the UFO control service will not start with a missing or outdated ledger.

The sandbox build step in `build_template.py` defines the environment where user code will run. It keeps the hosted E2B sandbox template and the local Docker image built from the same instructions, so testing and production use the same recipe.

The proxy check in `proxy_gate.py` starts a fresh sandbox, installs the proxy’s certificate authority certificate, and confirms HTTPS traffic can pass through the proxy correctly. Together, these checks prove the runtime is safe enough to open.

### [Core Alembic Harness and Foundational Schema](stage-1.2.md) `stage-1.2` — 7 files

This stage is part of setting up and upgrading the database, before the main system can safely do its work. It uses Alembic, a tool that applies database changes step by step, like a careful renovation plan for stored data. The env.py file is the entry point: it connects to the database, loads the project’s table definitions, and tells Alembic to run any missing updates.

The earliest migrations then build the system’s basic storage. 0001_heartbeat.py creates the first core tables for workspaces, agents, members, conversations, conversation turns, identities, and usage costs. 0002_credentials.py adds encrypted credential storage for each workspace. 0003_proposal.py adds proposals, which track requested changes and whether they are pending, approved, or rejected. 0004_loop_depth.py expands conversation turns so subagents and parent-child turn links can be recorded. 0006_ext_store.py gives extensions a small per-workspace JSON storage area. Finally, knowledge_graph_0001_graph.py adds early knowledge graph tables for known things and the relationships between them.

### [Core Conversation, Surface, Inbound, and Artifact Migrations](stage-1.3.md) `stage-1.3` — 19 files

This stage is behind-the-scenes database preparation for the main conversation system. A database migration is a versioned change to how stored records are shaped. These files teach the database how to support more places where users talk to the system, how messages move through it, and how later work can be traced back correctly.

The early migrations add Slack and web as conversation “surfaces,” meaning user-facing entry points. They later loosen old surface limits, add workspace-aware surface installations, and make each installation and conversation point to an agent. Other migrations strengthen conversation records by saving sandbox handles, sandbox conversation links, audience visibility, and shared artifact IDs for files or outputs passed around during a turn.

Several files improve turns, which are single steps in a conversation. They add safety guards, parent-child lookup speed, speaker and authorization details, trace links, extra context like timezone, “on behalf of” attribution, and an “intent” admission source. The inbound message migrations add a queue-like table for incoming messages, keep them ordered and unique, then add and later remove an old rendered-text field. Together, these changes make conversations portable, traceable, safer, and ready for multiple user surfaces.

### [Core Source, Page, and Content Memory Migrations](stage-1.4.md) `stage-1.4` — 11 files

This stage is behind-the-scenes database upgrade work. It changes the system’s memory shelves so sources, pages, ownership, permissions, and old content records are stored in newer shapes without losing existing data. It starts with 0008, which creates the basic source and page records: where content came from, which workspace it belongs to, and when it should be checked again. 0019 opens source types beyond just folders, so extensions can add new backends. 0021 adds a failure counter for retry slowdowns, and 0036 records when a source was removed. 0044 adds ownership information, while 0059 adds clear read permissions for agents and fills them in for existing sources. For pages, 0047 adds browsing fields, 0049 renames timestamps to better match their meaning, and 0054 adds workspace revision numbers so page changes can be followed in order. 0058 cleans out the old page-alert extension entry. Finally, 0052 removes older knowledge-graph tables as the project moves toward one shared memory surface.

### [Core Ledger, Runtime, Sandbox Usage, and Export Migrations](stage-1.5.md) `stage-1.5` — 10 files

This stage is behind-the-scenes database upkeep. It is made of migrations, which are small step-by-step changes that reshape the database as the product grows. Together they make the system better at tracking cost, runtime activity, and exports.

The spend-cap migration adds rules for spending limits and lets a work turn be paused when a limit is reached. The ledger changes widen what the accounting book can record: first token use, then egress, meaning data sent out, then sandbox token use. Later changes add a price digest field, and let ledger entries attach to a whole workspace instead of only to one turn.

The runtime migrations add a table for live runtime instances, recording where they belong and when they last checked in. Later they allow shared-fleet runtimes that are not tied to one workspace, then remove older columns no longer needed for that shared-fleet model.

The export migrations add a progress tracker for ledger exports and mark whether an export used BYOK, where the customer provides the encryption key.

### [Core Scheduling, Jobs, and Task Admission Migrations](stage-1.6.md) `stage-1.6` — 8 files

This stage is part of the system’s behind-the-scenes setup and upgrade path. It is a set of database migrations, which are small step-by-step scripts that change the database structure as the product gains new scheduling features. Together, they build the storage and lookup rules for tasks that should run later.

The first migration creates the scheduled task table, the basic “calendar” where future agent work is recorded. Later migrations add speed helpers, called indexes, so background workers can find jobs and conversations without searching every row. Other changes teach the system how to record pauses, scheduled admission of turns, and the last turn that caused a scheduled task to fire. Another adds expiration times, so old scheduled tasks can stop being valid. The agent identity migration refines uniqueness rules: task names only need to be unique for the same agent in the same workspace. The final migration adds a paused flag, letting a task remain stored while temporarily blocked from running.

### [Core Workspace, Agent, Grants, Connections, and Audit Migrations](stage-1.7.md) `stage-1.7` — 11 files

This stage is behind-the-scenes upgrade work for the database, the system’s long-term memory. These migrations change stored tables so newer code can understand workspaces, agents, permissions, connections, and audits.

It first adds grants, which are recorded approvals for an account or agent to access something in a workspace or conversation. Later it adds a shared flag so the system can tell whether that approval is shared. It adds workspace seating rules: when members get seats, optional seat limits, and optional included seat counts, with checks that counts are positive. It makes each workspace’s controlling member and main agent explicit, rather than guessing from creation order.

It also improves agent settings. One migration records whether an agent may use the internet. Another adds a controlled reasoning setting, limited to valid choices. Another updates old Bedrock model names to working replacements.

For external services, it separates reusable connections from the individual grants that let agents use them. Finally, it adds an audit log for admin transcript access, then removes an unused lookup index from that log.

### [Memory Extension Migrations](stage-1.8.md) `stage-1.8` — 12 files

This stage is behind-the-scenes setup for the memory extension’s database. A database migration is a careful recipe for changing stored data without losing it. These migrations build the memory system step by step. First, 0001 creates the main table for remembered facts or experiences in a workspace. 0002 adds memory pages, and 0004 ties those pages to workspaces too. 0003 adds labels for the kind of memory and a confidence score, so the system can tell what it believes and how strongly. 0005 and 0006 add indexes, like book indexes, so consolidation sweeps and inventory browsing stay fast as the table grows. 0007 adds an “as of” time, and 0008 fills missing times from related pages. 0009 creates a clear source-page link for memories. 0010 tracks the exact page revision a memory came from and clears older derived work so it can be rebuilt correctly. 0011 expands memory audiences to include rooms. Finally, 0012 moves source-page connections into a separate link table, making origins more flexible and less tangled with the memory item itself.

### [Non-Memory Extension Migrations](stage-1.9.md) `stage-1.9` — 8 files

This stage is behind-the-scenes setup for extensions, which are add-on parts of the system. It is made of database migrations: small upgrade scripts that create or change tables so newer code has the storage it expects. They also describe how to undo the change if the system is rolled back.

The evaluation environment migration adds fake email and calendar tables, separated by workspace, so tests can store messages and events safely. The default indexing migrations build the chunk table used for searchable pieces of text, add search indexes, and then change chunks so they are owned by a workspace to avoid name clashes. The sample extension adds a simple per-workspace note table. The sites extension creates records for hosted sites linked to workspaces and conversations. The skill creation migrations first store user-made skills, then refine that storage so skills belong to a specific agent. The web migration fills the shared extension store with metadata rows for existing web conversations. Together, these scripts prepare each extension’s own storage without mixing their data.

## [Process Bootstrap, CLI Commands, and Pack Selection](stage-2.md) `stage-2` — 15 files

This stage is the system’s front door. It runs when UFO is first started from a terminal, launched as a service, or packaged for deployment. The main command tools are ufoctl in cli.py for local setup, running, inspection, and packaging, and the hosted control command in main.py for starting the managed web gateway, preparing the database, invitations, Slack setup, and security rules. serve.py is the main assembly bench: it reads settings and connects the database, extensions, web routes, background workers, credentials, sandbox, and shutdown hooks. onboarding.py performs first-run setup by creating the first workspace, admin, and assistant, while preventing duplicate or incomplete setup. bundle.py freezes a chosen configuration into a Docker deployment folder. select.py picks the one sandbox, meaning the isolated place where tools run.

The pack files are ready-made menus of capabilities. Assistant, billing, hosted, and eval packs choose different assistant setups. Chief of staff, DSQA, GDPVal, sample, and YC packs declare their own extensions, skills, and setup steps so startup can enable the right bundle by name.

## [Extension Discovery and Capability Registration](stage-3.md) `stage-3` — 27 files

This stage runs during startup, before any user request is handled. It is the system’s plug-in intake desk. The loader first finds installed extensions from lockfiles and manifest files, checks which ones are allowed, and turns their “registration cards” into live choices the rest of the system can use.

The provider and backend registrations add outside services, such as AI model providers, embeddings, search, and Redis coordination, so later code can ask for these services through a common interface. The surface registrations announce places people can interact with the system, such as web pages, debugger routes, and UFO channels. The tool, skill, object, and subagent registrations add practical abilities, connectors, helper agents, credential needs, and object types.

The direct manifests add more cards to the same desk. Self-improvement registers a scheduled evaluation job. Sites adds website-building tools, a site-viewing surface, a site chat object, and a website-building subagent. Slack adds routes, workspace secrets, setup skills, and tools. Sources adds connector backends, credentials, object types, an authentication proxy, and a hook for syncing external content.

### [Provider and Backend Extension Registration](stage-3.1.md) `stage-3.1` — 5 files

This stage is part of startup and shared behind-the-scenes setup. It is like filling a switchboard before the system begins work, so later code can ask for “a model,” “embeddings,” “search,” or “a hub” without knowing the exact outside service.

The Bedrock extension tells the system how to use Amazon Bedrock Mantle: which AI models are offered, what they cost, what login details they need, and how to build the correct client to call them. The OpenRouter extension adds another route to AI models, wrapping OpenRouter so many possible model companies look like one normal provider. The OpenAI embedding extension turns text into “embeddings,” which are lists of numbers that capture meaning, so the system can compare and search text later. The Exa extension connects web search and page fetching to Exa, while keeping the user’s API key out of the sandboxed tool area. The Redis hub manifest registers a Redis-based live hub, so configuration can choose Redis when it needs shared coordination.

### [Surface Extension Registration](stage-3.2.md) `stage-3.2` — 3 files

This stage is part of startup and shared setup. Before people can use the system through a browser, a shell channel, or operator tools, the host needs to know which “surfaces” exist. A surface is a place where a user or operator can interact with the system, such as a web page, debugger route, or UFO channel.

The three manifest files act like registration cards. The debugger manifest announces the debugger extension, gives its version, and points to the web-facing routes that should be added for debugging. The UFO manifest announces the main UFO extension and the user-facing surface it contributes. The web manifest registers the web portal extension, names the portal surface, and lists the web-only tools that portal code is allowed to use.

Together, these files do not run the surfaces themselves. Instead, they let the host discover them, install them, and expose the right routes and tools in a controlled way.

### [Tool, Skill, Object, and Subagent Extension Registration](stage-3.3.md) `stage-3.3` — 14 files

This stage is the system’s plug-in registration desk. It runs behind the scenes so that, when an agent starts working on a turn, it knows which extra abilities are available and how to use them. Most files here are “manifests,” simple menu cards that describe an extension to the host system.

The browser, research, coding, documents, brief-pipeline, YC, memory, and scheduled-tasks manifests advertise bigger work packages: web browsing, deep research, repository work, document help, helper-agent pipelines, YC data access, remembered user context, and recurring tasks. The connectors, Composio, Pipedream, keyed connectors, and sources files register ways to reach outside services. Some use sign-in flows, some use API keys, and the sources registry maps short names like Slack or GitHub to the right connector. The MCP file adds a bridge to external tool servers and safely calls their tools.

Together, these files do not perform the main work themselves. They label the tools, agents, credentials, routes, and instructions so the main UFO system can discover them, load them, and offer them at the right moment.

## [Hosted Control-Plane Onboarding](stage-4.md) `stage-4` — 10 files

This stage is the front door for hosted UFO workspaces. It runs before normal workspace use, when a new customer or employee is proving who they are, joining the right workspace, and getting a sign-in token. The main web server in gateway.py coordinates the flow: install the client, enter a work email, pass any invite requirement, create or find a workspace, and sign in.

The client can be guided in two ways. gateway_directives.py sends small text instructions to the terminal client, including first-time install help. gateway_web.py turns the same steps into browser pages and JSON for the web interface. gateway_claim.py handles email proof by creating a short-lived secret code, emailing it, and later checking it safely. gateway_email.py rejects unsuitable personal addresses, builds verification and invite emails, and sends them. gateway_store.py keeps temporary signup claims in PostgreSQL. gateway_invite.py manages one-use invitations. gateway_shared.py maps a verified company domain to a shared workspace. gateway_slack_connect.py starts Slack Connect invites in the background. gateway_token.py creates the short-lived proof used to authenticate with the gateway.

## [Runtime Service Startup and Fleet Coordination](stage-5.md) `stage-5` — 2 files

This stage happens after the app has loaded its settings and add-ons. Its job is to bring the long-running runtime services online and keep them coordinated while the system is running. It is like opening the service doors, then making sure every worker keeps checking in.

`proxy_serve.py` starts the shared egress proxy. An egress proxy is a controlled doorway from isolated workspaces to approved outside services, such as model APIs, storage, or connector hosts. The file gathers the needed settings, secrets, database access, and security certificates, then runs one proxy process that can safely serve many workspaces.

`runtime_instance.py` keeps each running server process visible to the wider fleet. It writes heartbeat records, which are regular “I am still alive” signals. It also runs cleanup loops in the background. If a workflow was queued or owned by a process that disappeared, it can recover that work. If a parent task is cancelled, it spreads that cancellation to child tasks that are still running. Together, these files make startup durable and keep live work from being stranded.

## [External Surface Ingress and Authentication](stage-6.md) `stage-6` — 6 files

This stage is the system’s front door. It runs during normal operation, when people or outside services contact UFO through Slack, a web browser, the command line, debugger pages, hosted site links, or sandbox links. Its first job is to check that each caller is allowed in, using signatures, cookies, membership checks, or signed links. Its second job is to translate each outside request into the system’s internal conversation or viewing flow.

The Slack surface checks that messages and button clicks really came from Slack, then turns them into conversation turns and sends results back. The web surface serves the main portal, signs users in, supports chat, and streams live updates. The UFO command-line surface does the same kind of bridge for terminal users. The debugger surface gives read-only views into sessions, transcripts, files, and live events. The hosted sites surface decides who may open a shared site and displays it safely. The ingress server forwards browser traffic through signed sandbox links to the correct running sandbox, including live WebSocket traffic.

## [Conversation, Member, Agent, and Workspace Admission](stage-7.md) `stage-7` — 11 files

This stage is the front door after someone is authenticated. It turns activity from chat apps or the web portal into real workspace records and actions. The surface bridge accepts outside events, finds or creates the member and conversation, admits each message once, and sends finished replies back to systems like Slack. Membership and seats decide who belongs, who is an admin, and whether the agent is allowed to answer them.

Once inside, the stage connects people to the right agents and objects. Audience rules decide which members may use which agents, while panels let web forms make the same audited changes as chat commands. Agents and conversations are exposed as workspace objects: agents can be listed and safely edited, while conversations can be read but not changed. The shared object system is the “shelf” for durable workspace items, checking names, ownership, and permissions before any specific object type acts. Object scope keeps each operation tied to the correct agent. Connector accounts and hosted sites plug into that shelf too, supporting listing, inspection, sharing, revocation, and deletion or disconnection.

## [Turn Admission, Queueing, Claiming, and Recovery](stage-8.md) `stage-8` — 2 files

This stage is the handoff point between “a user or schedule wants something done” and “an agent turn is safely ready to run.” A turn is one unit of conversation work, like one job ticket in a workshop. The admission file is the front desk. It receives new messages, resumed work, or folded-together conversation events, then checks whether the request is allowed: enough seats, spending limits, no duplicate delivery, and the right agent attached. Once approved, it passes the turn into the queue.

The queue file is the job board and safety net. It records the turn durably so it is not lost if the process crashes. It attaches the correct workspace, conversation, parent turn, sandbox, and credentials, so the runner has the right environment and permissions. It also lets a worker claim a turn for execution, finds turns abandoned by dead workers, and puts them back on track. If setup fails before the agent really starts, it still writes a final success or failure result, so every admitted turn reaches a clear ending.

## [Per-Turn Setup: Context, Sandbox, Skills, Prompts, and Models](stage-9.md) `stage-9` — 3 files

Before the engine asks the AI model what to do next, it prepares the “workbench” for the turn. This stage gathers the material the model will need: the conversation so far, saved memory, available tools, reusable skills, safety limits, prompt text, and the model choice. It is part of the setup before the main work loop begins.

The compaction code acts like an editor for an overlong notebook. AI models can only read a limited amount of text at once, so it keeps the newest messages unchanged and turns older parts into a structured summary that can be checked later. It stores both versions for review.

The prompt rendering code builds the final instruction sheet for the model. It fills in named blanks in prompt templates, makes sure no required piece is missing, and records a fingerprint so changes can be traced.

The skills runtime loads reusable abilities from skill folders. It understands what each skill needs, orders dependent skills correctly, and copies needed files into the sandbox workspace, so the agent has the right instructions and resources before it starts acting.

## [Agent Model Loop and Transcript Evolution](stage-10.md) `stage-10` — 4 files

This stage is the main work loop for one agent turn. It is where the system takes the current conversation, asks an AI model what to do next, reacts to the model’s answer, and writes the result back safely. The engine is the conductor. It claims the turn so two workers do not do the same job, gathers the transcript, sends the request, listens to streamed model events as they arrive, runs any requested tools, accepts new user messages that appear mid-turn, tracks cost and reasoning, and decides whether to keep going or finish with a final answer.

The transcript module is the notebook. It reads and writes the conversation in shared storage, while guarding against an older write replacing a newer one. The Anthropic and OpenAI modules are translators. UFO uses its own common request and event format, but each provider speaks a different API language. These files send the model request, stream the provider’s reply back into UFO’s format, and apply retry and error handling so the loop can stay reliable.

## [Tool Catalog Dispatch and Built-In Agent Actions](stage-11.md) `stage-11` — 3 files

This stage is the agent’s tool desk during the main work loop. When the model wants to do something outside text, such as read a file or run a command, it must choose from a controlled catalog instead of acting freely. The registry is that catalog. It gives each tool a name and description, shows only the allowed tools to the model, and matches a requested tool name to the correct implementation.

The built-ins are the standard tools on the desk. They turn model requests into real actions: running shell commands, reading or editing workspace files, sharing completed artifacts, asking the user for missing information, connecting accounts, loading extra skills, coordinating subagents, working with workspace objects, and running cleanup callbacks.

The context is the safety envelope around each tool call. It tells a tool what it may access, such as files, browser sessions, credentials, accounts, memory, or subagents. Together, these pieces let the model act usefully while keeping each action named, routed, and bounded.

## [Sandbox Execution, File I/O, and Network Mediation](stage-12.md) `stage-12` — 28 files

This stage is the system’s safe workshop for agent work during the main work loop. It gives each conversation a private workspace where commands can run, files can be read or written, websites can be opened, and network calls can be controlled.

The shared session layer is the front door. It gives all tools one common way to run commands, transfer files, and reach services inside the sandbox without exposing private host data. The conversation workspace code makes sure a conversation keeps returning to the same /workspace folder, so files do not get scattered across different sandboxes.

Different backends provide different kinds of workshop. The local sandbox uses an ordinary folder and subprocesses for development. The Docker extension runs work inside an isolated container. The E2B extension does the same in a remote cloud sandbox. All can route internet traffic through the same controls.

The egress proxy is the guarded exit, checking and metering outside requests and only adding credentials when allowed. Browser Session Control is the web bench, managing real browser sessions, clicks, downloads, page reading, and recovery.

### [Egress Proxy and Credential Injection](stage-12.1.md) `stage-12.1` — 2 files

This stage is the system’s guarded exit door for sandboxed agent work. It runs during the main work loop, whenever an agent inside a workspace tries to contact the outside network. Instead of letting the agent call any website or service directly, the request must pass through the egress proxy.

The rules file builds the rulebook. It looks at workspace settings, declared permissions, available credentials, connector grants, model access, and storage options. From these, it creates simple decisions such as “this host is allowed,” “this host is blocked,” “this destination may receive this secret,” or “this call should be counted for billing.”

The server file is the doorway that enforces that rulebook. It checks each outgoing request, denies unsafe hosts, and only injects sealed secrets when the destination is approved. For some connector traffic, it forwards the request through a grant broker, like a trusted middleman. It also records usage for audits and metering, then cleans up proxy resources when the sandbox stops.

### [Browser Session Control](stage-12.2.md) `stage-12.2` — 21 files

Browser Session Control is the system’s browser workshop. It is used during the main work loop whenever an agent needs to open a site, inspect it, click, type, download a file, or clean up afterward. It can use different browser sources: a Chrome running inside the sandbox, a remote Browserbase Chrome, the Browser Use service, or the project’s own automation stack.

The provider integrations decide where the browser comes from, like choosing which car to drive. The CDP layer, named for Chrome DevTools Protocol, is the control wire that sends commands to Chrome and listens for events. It manages tabs, page loading, dialogs, downloads, and safe JavaScript execution. The action layer turns planned actions into real mouse, keyboard, scroll, form, and upload events. The extraction layer reads the page and finds the exact element the agent referred to.

tools.py exposes browser abilities as agent tools. backend.py connects each turn’s tool calls to a real Chrome session and handles recovery and file transfer. session.py holds the live session state, including tabs, dialogs, downloads, and page readiness.

#### [Browser Provider Integrations](stage-12.2.1.md) `stage-12.2.1` — 3 files

This stage is shared support for any part of the system that needs a browser. Instead of forcing the rest of the code to know where the browser comes from, these integrations provide interchangeable “browser engines,” like swapping the motor in a machine while keeping the same controls.

The Browser Use integration connects UFO to a hosted web-automation service. It offers two familiar tools: `browser_task` for running one browser job, and `wide_browse` for running many jobs in parallel. Callers can keep asking for those tools without caring that the work is happening in an outside service.

The Browserbase integration provides a remote Chrome browser. For a browser run, it creates the hosted Chrome session, connects UFO to it, transfers needed files in and results out, then shuts the session down.

The sandbox Chrome integration uses a real Chrome browser inside the conversation’s own sandbox, which is an isolated workspace. It starts Chrome or reuses an existing one, exposes a safe control link, and lets the system drive it.

#### [CDP Connection, Runtime, Tabs, and Page Lifecycle](stage-12.2.2.md) `stage-12.2.2` — 6 files

This stage is the browser control room. It sits behind the main work loop and gives the rest of the system a reliable way to drive Chrome, watch what happens, and know when the page is ready for the next step. The cdp.py file is the main wire to Chrome. It opens a WebSocket, which is a two-way message pipe, sends DevTools commands, receives replies, and passes browser events to the right waiting task. On top of that, runtime.py offers a safer way to run JavaScript inside the current page and turns script failures into normal Python errors. tabs.py manages the visible workspaces: opening tabs, closing them, switching between them, and navigating to URLs. settle.py acts like a patient spotter after clicks or page loads, waiting for useful changes while ignoring endless background noise. dialogs.py quickly answers alerts, prompts, confirmations, and leave-page warnings so they do not block automation. downloads.py watches for files, triggers downloads when needed, and waits until they are actually saved.

#### [Browser Action Execution and Input Translation](stage-12.2.3.md) `stage-12.2.3` — 5 files

This stage is the hands and keyboard of the browser automation system. After another part of the system decides what should happen next, this stage turns that decision into real Chrome input, such as moving the mouse, clicking, typing, scrolling, filling a form, or uploading a file.

The main runner is computer.py. It takes actions like “click here” or “type this,” sends the right input events to the browser, then captures a fresh screenshot and reports any warnings. coordinate.py acts like a map legend. It converts positions from the resized screenshot seen by the AI back to the browser’s true screen coordinates, so clicks land in the right place. fixup.py is a safety checker. It cleans up small mistakes in planned actions before they reach Chrome. keys.py translates everyday keyboard instructions, such as “Ctrl+A” or typed text, into Chrome’s exact key event format. forms.py handles direct form work, including setting field values and attaching files to upload boxes. Together, these pieces make browser actions precise, safe, and repeatable.

#### [Page Content Extraction and Element Lookup](stage-12.2.4.md) `stage-12.2.4` — 4 files

This stage is the browser “reading and pointing” layer used during the main automation loop. Before the model can choose an action, the system must turn a live web page into clear text. After the model chooses something, the system must connect that text reference back to a real page element.

page.py is the main translator. It takes the current browser page and produces a clean, structured view the model can understand, then maps the model’s chosen element back to screen positions for actions like clicking or typing. content.py provides smaller tools for reading page content, extracting text, and searching elements, while keeping the results compact and safe for other code to use. find.py works with the accessibility tree, which is a browser-provided outline of visible controls and text. It parses tree lines, matches search requests, checks whether model answers really exist, and formats matches. errors.py defines the special error used when the model points to an element that is not actually on the page.

## [External Connector, OAuth, and Provider Action Flows](stage-13.md) `stage-13` — 18 files

This stage is the system’s “safe plug adapter” for outside services. It is used when a workspace or member connects an app, and later when an agent uses that app during normal work. The main connector and grant files decide who owns a connection, which agent may use it, and whether a secret is held by UFO or by a broker service. The callback file finishes browser approval flows, such as OAuth, where a user approves access on another site and returns to UFO.

Composio and Pipedream files provide hosted bridges to many apps. Their clients create consent links, check connected accounts, list tools or actions, run them, move files, and proxy web requests so raw tokens never enter the sandbox. Composio also has live tool discovery through its MCP tool router.

GitHub App files connect a workspace to GitHub and turn a verified installation into a short-lived access token. Shared connector tools let agents search and run available tools safely. Slack tools guide setup and lookup conversations. The YC bridge connects credentials and allows read-only YC actions.

## [Source Synchronization, Indexing, and Memory Recall](stage-14.md) `stage-14` — 62 files

This stage is the system’s intake and memory pipeline. It runs mostly during the main background work after a user connects outside services. Its job is to read information from tools like Google, Slack, GitHub, Salesforce, and many others, turn that information into standard “pages,” notice changes and deletions, and make the content searchable and usable later in conversations.

The concrete source connectors are the adapters for each outside service. They know how to call that service’s web API, which is a structured way for software to ask another system for data. The REST helper provides common plumbing for these calls, including retries and page-by-page fetching. The backend converts connector results into UFO’s shared sync format. The sync layer stores page bodies, cursors that mark progress, and a replay feed so indexers can catch up safely.

The tools file lets agents manage sources, resync them, and alert subscribed conversations when pages change. The pages file exposes synced pages for reading. Finally, the memory and search backends split text into chunks, embed it for meaning-based search, store it in an index, and recall or clean memories in background jobs.

### [Concrete Source Connectors](stage-14.1.md) `stage-14.1` — 49 files

This stage is the system’s large intake shelf for read-only source connectors. It runs during the main sync work, after accounts are connected, and its job is to fetch data from outside services without changing it. Each connector is an adapter: it knows one provider’s web API, asks for records, handles pages or change markers, and reshapes the results into a common stream the rest of the platform can store, search, and remember.

The sub-stages group these adapters by the kind of tool they read from. Google Workspace covers mail, calendars, docs, drive files, meetings, and sheets. Collaboration connectors bring in Slack, Teams, Outlook, Notion, and Confluence knowledge. Work and engineering connectors read tasks, issues, code, incidents, and errors. Customer, marketing, finance, recruiting, scheduling, and form connectors do the same for their own business systems, from Salesforce and Stripe to Greenhouse and Airtable.

The YC source file adds a special intake path for YC material, including trusted guidance collections and controlled searches over YC directories such as companies, founders, jobs, and forums.

#### [Google Workspace Source Connectors](stage-14.1.1.md) `stage-14.1.1` — 6 files

This stage is the Google Workspace “intake” layer. It runs when the system syncs connected accounts, not to change anything, but to read useful information and reshape it into plain text and records that the rest of the system can search and recall later.

Each connector is like an adapter for a different Google tool. The Gmail connector reads mailbox messages, extracts searchable text, and remembers Gmail’s change markers so future syncs only pick up new or changed mail. Google Calendar reads the primary calendar, turns events into records, and also creates attendee records so invitations can be understood person by person. Google Docs reads accessible documents and converts their contents into text. Google Drive covers the wider file system: files, shared drives, permissions, comments, and revisions. Google Meet reads meeting artifacts such as transcripts and generated notes, making meetings searchable. Google Sheets finds accessible spreadsheets and breaks them into spreadsheet, tab, and cell-value records. Together, these files turn Google Workspace from separate apps into consistent read-only streams for the system.

#### [Collaboration, Messaging, and Knowledge Source Connectors](stage-14.1.2.md) `stage-14.1.2` — 5 files

This stage is a set of behind-the-scenes connectors that let the system bring in information from workplace tools outside Google Workspace. Think of each connector as an adapter plug: every service speaks its own language, and these files translate that into the project’s common record format so the rest of the system can sync, search, and recall it later.

The Confluence connector reads Atlassian wiki material such as spaces, pages, blog posts, comments, groups, and audit records, then turns it into readable text. The Notion connector does the same for Notion users, pages, databases, blocks, and comments. The Microsoft Teams connector uses Microsoft Graph, Microsoft’s shared web doorway for its apps, to collect teams, channels, chats, and messages. The Outlook connector also uses Microsoft Graph, but focuses on mail, contacts, calendar events, conversations, and folders; it tracks changes in pages so later runs can fetch only updates. The Slack connector reads users, channels, messages, threads, and participants. Together, they feed outside collaboration knowledge into one searchable system.

#### [Work Management and Engineering Operations Source Connectors](stage-14.1.3.md) `stage-14.1.3` — 9 files

This stage is shared behind-the-scenes support for the system’s syncing work. Its job is to connect to common tools where teams plan work, write code, respond to incidents, and track bugs, then turn those outside records into standard pages the rest of the system can store and search. Think of each file as an adapter plug for a different service.

The Asana, ClickUp, Jira, Linear, monday.com, and Wrike connectors read project-management data such as tasks, issues, comments, teams, boards, folders, and workspace details. They also deal with each service’s shape: ClickUp has nested workspaces, Linear uses GraphQL, and others return paged web responses. The GitHub connector brings in code-collaboration records like organizations, repositories, issues, pull requests, comments, and users. PagerDuty adds incident-response information, including incidents, services, schedules, notes, and on-call records. Sentry adds error-tracking data, such as projects, issues, events, members, and releases.

Together, these connectors are read-only translators. They fetch data from outside systems, reshape it into common records, and leave storage and search to the rest of the platform.

#### [CRM, Sales, and Customer Support Source Connectors](stage-14.1.4.md) `stage-14.1.4` — 6 files

This stage is the customer-data intake area of the system. It runs during sync work, when the project reaches out to outside services and brings back records that can be stored, searched, and reused later. Each source file is like an adapter plug for a different service. Attio reads companies, people, deals, tasks, notes, meetings, and call recordings, flattening Attio’s nested replies into simpler records. HubSpot covers a wide range of sales and marketing data, including contacts, companies, deals, assets, analytics, and links between records. Salesforce reads objects such as Accounts and Contacts, including signs that records were deleted, but it never writes changes back. Freshdesk focuses on helpdesk data and knows how to sign in and move through Freshdesk’s different page-by-page result formats. Intercom brings in conversations, contacts, companies, tickets, tags, and activity logs. Zendesk imports support tickets, users, organizations, help articles, and community posts. Together, these connectors translate many customer-facing tools into one steady record stream.

#### [Marketing, Ads, and Social Source Connectors](stage-14.1.5.md) `stage-14.1.5` — 6 files

This stage is a set of behind-the-scenes connectors for marketing and advertising tools. A connector is like a plug adapter: it knows how to talk to one outside service, ask for the right data, and reshape the reply so the rest of the system can use it in the same way.

ActiveCampaign brings in marketing and customer relationship records, carefully moving through pages of results from its API, which is the service’s data doorway. Facebook Ads reads Meta ad accounts, campaigns, ad sets, ads, and daily performance numbers. Google Ads does the same for Google customers, campaigns, ad groups, ads, and metrics, flattening complex replies into simple records. Instagram uses Meta’s Graph API to collect business Pages, linked Instagram accounts, posts, stories, and insight statistics.

Klaviyo focuses on lifecycle marketing data. It can continue from the last synced point and turn nested records into searchable rows. Mailchimp reads audiences, subscribers, campaigns, reports, tags, and email activity. Together, these files feed campaign, audience, and engagement data into the wider sync system.

#### [Finance, Billing, Spend, and Commerce Source Connectors](stage-14.1.6.md) `stage-14.1.6` — 7 files

This stage is a set of read-only “source connectors.” A connector is an adapter that knows how to talk to one outside service and reshape its data into the common form used by the sync system. These files are used during the main syncing work, after the system has been configured and is ready to fetch records.

Each connector handles one finance or commerce tool. Brex brings in spend data such as expenses, vendors, budgets, users, and departments. Chargebee and Recurly bring in subscription-billing records, including customers, subscriptions, invoices, and transactions. Stripe reads payment and billing data such as charges, checkout sessions, invoices, customers, and related child records. QuickBooks and Xero read accounting ledgers, including invoices, bills, contacts, accounts, payments, and journal entries. Square reads commerce records such as customers, payments, locations, catalog items, orders, and inventory counts.

Together, they act like different plug shapes for the same socket: each speaks its service’s API, follows paged results, and produces reusable streams of records for storage or later processing.

#### [Recruiting, HR, and Workforce Source Connectors](stage-14.1.7.md) `stage-14.1.7` — 6 files

This stage is the set of “adapters” that lets the system bring in people and hiring data from outside services. It is used during the main sync work, when the system contacts a vendor’s web API, which is a structured way for software to ask another service for data, and turns the replies into records it can store and search.

Each file knows the habits of one service. The Ashby, Greenhouse, and Recruitee connectors focus on recruiting: candidates, jobs, applications, interviews, offers, departments, users, and related lookup details. Greenhouse also knows how to fetch smaller child records that belong to larger items, such as notes or questions tied to a candidate or job. BambooHR covers employee records, time off, timesheets, company metadata, and custom reports, smoothing out BambooHR’s varied response shapes. Deel reads contractor and HR operations data such as contracts, payslips, tasks, forms, and timesheets. Rippling reads companies, workers, and teams. Together, these connectors act like translators, turning many different vendor formats into steady streams the wider sync system can process.

#### [Horizontal Data, Scheduling, and Form Source Connectors](stage-14.1.8.md) `stage-14.1.8` — 3 files

This stage is a set of behind-the-scenes connectors that bring in common business data from outside services. It is not the main work of the product by itself. Instead, it feeds the rest of the system with clean, repeatable records that can be stored, searched, and reused later.

The Airtable connector reads from Airtable, a spreadsheet-like database tool. It asks Airtable’s web API, meaning its online data access doorway, what bases, tables, and records exist. It then breaks that data into streamable pages so the system can process it in manageable chunks.

The Calendly connector reads scheduling data. It collects users, event types, meetings, and invitees, then reshapes Calendly’s responses into a common record format.

The Typeform connector reads form and intake data. It gathers forms, responses, workspaces, themes, images, and webhook settings, which are rules for sending updates when something happens.

Together, these connectors act like import adapters. Each speaks a different outside service’s language, then hands the system data in a familiar shape.

### [Memory and Search Backends](stage-14.2.md) `stage-14.2` — 8 files

This stage is shared behind-the-scenes support for remembering and finding information. It is like a library system: some parts decide how to split books into useful pages, some parts store the pages, and some parts help people browse the shelves.

The shared indexing code defines how long text is cut into smaller searchable chunks. It also sets the common “contract” that every search backend must follow, so the rest of the system can search without knowing where the index lives. The default index is the built-in option. It stores chunks locally and can search by matching words or by comparing vector embeddings, which are number patterns that represent meaning. The Turbopuffer extension does the same kind of work using an outside search service.

The memory store saves explicit facts and source-page text, then queues background indexing so saving stays quick. The condenser cleans up raw pages and older facts into more durable memories. Events define shared names and limits for memory activity. Objects make memories readable by ID, while the surface provides a read-only web view and API for authorized operators.

## [Specialized Agent Workflows and Durable Skills](stage-15.md) `stage-15` — 17 files

This stage is shared support for “bigger than one turn” work. It lets the main agent call focused helper agents, keep useful skills, and manage long-running or multi-step jobs. The subagent core defines a default helper profile and the machinery to start, message, wait for, or cancel child agents. Extensions then add specialists: browser agents for web tasks, research agents for search and page fetching, website agents for building and publishing sites, and a brief pipeline that passes work from outline to draft to critique.

Several tools wrap these helpers so the main agent can delegate cleanly. Browser delegation runs one or many browsing jobs. Wide research runs many research jobs and saves one combined JSON result. Website delegation sends a build request to the site builder, while site tools run servers and publish safe links.

Other files give the agent durable working memory and scratch space. Skill creation saves user-written skills for later turns, with checks for names and ownership. Todos keep visible checklists. Scheduled tasks pause, wait, or recur. The REPL extension provides Python and JavaScript scratchpads. A sample skill probe simply proves a skill file can run.

## [Document, Office, Spreadsheet, Slide, and PDF Processing](stage-16.md) `stage-16` — 19 files

This stage is a toolbox used when the system needs to work on documents on demand, rather than part of a constant main loop. Like a workshop bench, it has separate tools for each file type. The document-review scripts share two common pieces: constants.py names the state and log files, and models.py defines what a review issue looks like. manage_state.py records the review’s progress in JSON, then annotate_pdf.py, annotate_pptx.py, and annotate_xlsx.py read that saved state and place the findings back into PDFs, PowerPoint decks, or Excel cells as visible comments. For Word files, unpack.py opens a DOCX package into editable XML, comment.py prepares Word comment records, pack.py rebuilds the DOCX, and accept_changes.py uses hidden LibreOffice to accept tracked changes. For PowerPoint, unpack.py and pack.py open and rebuild PPTX packages, slides.py cleans or adds slide material, and repair.py fixes known deck problems. For spreadsheets, _soffice.py runs LibreOffice quietly, while recalc.py refreshes formulas and reports errors. The PDF tools fill real form fields, mark up flat forms, and render pages as images.

## [Live Updates, Artifact Sharing, and Surface Writeback](stage-17.md) `stage-17` — 5 files

This stage runs during and just after an agent turn, when the system needs to show progress, share results, and let people download files. It is the “live display and handoff” layer. The main hub keeps a short stream of update frames, such as new text, tool activity, cost changes, and the final answer. If a screen disconnects and returns, it can replay recent frames instead of losing the thread. The hub tail adds a safety check against the database, so a late viewer can still learn whether the turn already finished and stop cleanly. When the system is spread across multiple server processes, the Redis hub carries the same live frames through Redis Streams, a shared message pipe, so watchers in other processes can follow along. For shared files, the artifact model represents files produced by a turn and allows listing, reading, copying back, or deleting them. The artifact download route is the guarded doorway: it serves the stored file only when the request includes a valid signed token.

## [Scheduled, Billing, Evaluation, and Self-Improvement Background Work](stage-18.md) `stage-18` — 14 files

This stage is the system’s behind-the-scenes workshop. It runs work that is not part of answering a user message, such as timed jobs, billing updates, test worlds, and prompt improvement. When the server starts, the jobs layer turns declared background jobs into durable queued work, while the candidates helper safely finds which workspaces need attention and then processes them one at a time. The scheduling layer stores timers in the database so they survive restarts, and the scheduled-task runner ticks the clock, claims due tasks, fires them once, retires expired tasks, and reschedules repeating ones.

Billing work connects workspaces to Metronome for usage, Stripe for payments, and chat tools for seat and plan administration, with safeguards against double billing. Evaluation support creates a predictable fake email, calendar, and code-search world for tests.

The self-improvement pieces form a careful feedback loop. They collect failed tool conversations, call the model in a controlled way, propose better prompts, replay old tasks without running real tools, judge results, and gate changes. Governance then requires approval before any prompt is replaced.

## [Turn Completion, Cleanup, and Service Teardown](stage-19.md) `stage-19` — 1 files

This stage is the system’s “put everything away” step. It runs after a unit of work, called a turn, finishes, fails, or is cancelled, and it also helps during application shutdown. Its job is to make the final state trustworthy: save the last transcript, finish usage or billing records, mark queued jobs as done or failed, and clean up anything the turn borrowed, such as sandboxes, browser sessions, provider clients, proxy connections, containers, or remote machines. It also runs cleanup callbacks for tools and removes scheduled tasks that have expired.

The shared piece here is `cancellation.py`. It defines the safe way to cancel a turn. A cancellation is not just a database label; the running workflow must first be told to stop, like signaling a worker before closing their task record. Only after that stop signal is sent does the system record the turn as cancelled. This ordering helps avoid half-running work being marked as already finished.

## [Persistence, Schema, and Durable Store Contracts](stage-20.md) `stage-20` · (cross-cutting) — 6 files

This stage is the system’s durable memory. It sits behind almost every other phase, from startup through the main work loop, because many parts need to save conversations, queue work, store files, and later read them back safely. The database doorway in db.py opens connections, runs migrations, meaning planned database shape changes, and keeps each workspace’s data separated. tables.py defines the full database map using SQLAlchemy, a tool that connects Python code to database tables, so development and production use the same layout. records.py defines the shared “paper forms” for turns, agents, user questions, credentials, billing, and queue state, so workers and request handlers agree on what each record means. transcript.py does the same for saved conversations and compacted conversation summaries. blob.py stores large byte data, using local disk or S3-style cloud storage through one common interface. The hosted-site store adds durable records for live generated sites, tying names, ports, owners, and permissions together so links stay safe and cannot be hijacked.

## [Security, Secrets, Permissions, and Grant Boundaries](stage-21.md) `stage-21` · (cross-cutting) — 14 files

This stage is the system’s security guardrail. It runs behind the scenes during sign-in, request handling, tool use, connector access, sandbox browsing, and file downloads. Its main job is to make sure every action is tied to the right workspace, person, agent, audience, and permission.

The workspace and database files set the boundary between customers: one marks “this is the active workspace,” while the database safety fence keeps rows from leaking across workspaces. Agent scope records which agent is acting. Audience labels control who may see conversation information. Bearer tokens identify a member and workspace without storing a server-side session. Operator session logic does the same for staff-only tools.

Several files protect secrets and access grants. Credential files show which secrets are needed, store them safely, verify credential requests, and reveal values only to the proxy at the last moment. The direct connector credential path lets background sync jobs use member-supplied API keys without exposing them to agents.

The remaining pieces sign and verify special tokens: for shared artifact downloads, sandbox hostnames and ports, early surface routing, and general tamper-proof payloads. Together they act like sealed labels and locked doors.

## [Public SDK, Protocol Types, and Extension Interfaces](stage-22.md) `stage-22` · (cross-cutting) — 42 files

This stage is shared behind-the-scenes support. It defines the public promises that extensions and internal parts depend on, but it does not start the system or run the main work loop. Think of it as the rulebook and front desk for anyone adding new abilities.

The core protocol files define common data shapes and interfaces, such as AI messages, tool calls, browser access, memory results, search, source syncing, and extension manifests. The SDK runtime and declaration facades give extension authors safe public ways to describe tools, jobs, skills, web routes, logs, and run context. The provider, source, model, and sandbox facades expose stable entry points for connectors, search indexes, models, browser sessions, and controlled execution. The identity, auth, governance, and accounting facades publish approved types for credentials, tokens, permissions, audiences, seats, and spending records. The hub, object, listing, and surface facades collect public records for catalogs, objects, paging, and user-interface integrations. Finally, package marker files set clean Python import boundaries. Together, these pieces keep the SDK stable, safe, and understandable while the internals evolve.

### [Core Public Protocol and Provider Contracts](stage-22.1.md) `stage-22.1` — 8 files

This stage is shared behind-the-scenes support. It defines the “contracts” that other parts of the system rely on: agreed shapes for data and agreed promises about what a component can do. These files do not run the main work by themselves. They make it possible to swap pieces in and out safely.

The extension context file defines the limited toolbox an extension gets inside a workspace, so add-ons cannot freely reach databases, secrets, files, or other workspaces. The browser file defines how the system asks for a Chrome connection without caring where Chrome is running. The manifest file defines how an extension describes the tools, jobs, credentials, and other features it brings.

The memory file sets the common form for recall results and memory providers. The model interface defines the shared language for AI messages, tool calls, images, reasoning, and response events. The search file gives one contract for web search and page fetching. The source connector file defines how outside data arrives in streams and pages, and how progress is tracked. The subjects file keeps audience labels consistent.

### [SDK Extension Runtime and Declaration Facades](stage-22.2.md) `stage-22.2` — 8 files

This stage is shared support for people writing UFO extensions. It is not the main work loop itself. Instead, it is the public front door that extension code uses while the system runs. These files act like a reception desk: they offer stable names and hide the private room layout behind them, so outside code does not break when internals move.

The context doorway gives extensions safe access to facts about the current run, such as identity, credentials, pages, sources, and recorded steps. The HTTP toolkit provides request and response objects, file uploads, forms, and cookies for extension web routes without exposing the underlying web library. The jobs and scheduling doorways expose the approved ways to declare background work and when it should run. The manifest file supplies the public types used to describe an extension. The skills file exposes skill objects and the parser that reads skill declarations. The tools file provides the public classes for declaring callable tools. The observability file lets extensions log events and report metrics using names controlled by the core system.

### [SDK Provider, Source, Model, and Sandbox Facades](stage-22.3.md) `stage-22.3` — 9 files

This stage is shared behind-the-scenes support for extension authors. It does not do the main work itself. Instead, it provides stable “front doors” into parts of the system that may move around internally. That way, outside code can keep importing from the SDK without depending on private file locations.

The browser module exposes safe browser connection interfaces. Connectors gathers the pieces needed to talk to external services, including login support such as OAuth, a standard web sign-in flow. Sources is for adding new content sources, with sync tools, REST helpers, pagination, and errors. Index opens the door to search indexes and embedding backends, which turn text into searchable numeric representations. Memory and search expose the public types used for memory lookup and general search. Models gathers model clients, message formats, tool-call blocks, and pricing helpers. Operator exposes web-session helpers meant only for operator use. Sandbox re-exports the public API for controlled execution environments. Together, these files act like a reception desk for plugins: they route authors to the right tools while shielding them from internal rearrangements.

### [SDK Identity, Auth, Governance, and Accounting Facades](stage-22.4.md) `stage-22.4` — 8 files

This stage is shared behind-the-scenes support for people building on top of the system. It does not run the main work itself. Instead, it provides stable “front doors” in the public SDK, so outside extensions can import the right tools without depending on the project’s private folder layout.

Each file is a small facade, like a labeled counter in a service desk. accounting.py exposes accounting and spending-related objects, but does not calculate costs itself. audience.py exposes names and helpers for deciding who a conversation is meant for. authproxy.py gathers the records and types used when an external connector works through an authentication proxy. bearer.py exposes safe checks for bearer tokens, which are proof strings sent with requests, without revealing the secret used to make them. credentials.py republishes approved credential classes and helpers. grants.py exposes permission grants and connection audit helpers. seats.py exposes seat types and rules, meaning concepts about who may occupy or use access. surface_token.py forwards helpers for surface tokens. Together, these files keep the SDK safe, simple, and stable.

### [SDK Hub, Object, Listing, and Surface Facades](stage-22.5.md) `stage-22.5` — 4 files

This stage is shared support for people building on top of UFO, not part of the main work loop itself. It provides “facades”: simple public doorways that hide the project’s internal file layout. That matters because outside extensions can import stable SDK paths even if the code inside the project is reorganized later.

The hub module is the doorway for hub records and related model types. It does not create new behavior; it simply points users to the approved hub-related names. The listings module does the same for listing and paging helpers, which are tools for returning many items in manageable chunks, like pages in a catalog. The objects module gathers public object helpers and types so extension authors do not need to know where those pieces live internally. The surfaces module is for surface extensions: integrations that show UFO conversations or actions in another user interface. It collects the classes, errors, helper functions, and records those integrations need. Together, these files act like a clean front desk for the SDK.

### [Package Boundary Marker Modules](stage-22.6.md) `stage-22.6` — 5 files

This stage is quiet behind-the-scenes support. It does not start the system, run the main work, or shut anything down. Instead, it gives Python clear borders between groups of code. In Python, an __init__.py file marks a folder as a package, meaning other code can import files from that folder using a name like ufo.models.

Each file here is a signpost, not a machine part with moving logic. core/src/ufo/models/__init__.py opens the model area for imports. core/src/ufo/sandbox/__init__.py does the same for sandbox code, and core/src/ufo/sandbox/proxy/__init__.py marks the nested proxy area inside the sandbox. core/src/ufo/schema/__init__.py marks the place for schema code, which describes data shapes and rules. core/src/ufo/sdk/__init__.py marks the SDK area, the public toolkit other code may use.

Together, these files act like labels on drawers in a workshop. They do not build anything themselves, but they make sure the rest of the system can find the right tools.

## [Model Catalogs, Pricing, Accounting, and Usage Metering](stage-23.md) `stage-23` · (cross-cutting) — 6 files

This stage is shared behind-the-scenes support for knowing which AI models exist, how to call them, and how much their use costs. It is used during startup to build the available model list, and during normal requests to choose models, price usage, and record spending.

The model spec file defines the standard “fact sheet” for a model: provider, API route, context size, pricing, and features such as reasoning support. The catalog file fills that format with the built-in models the system already knows. The registry then acts like the front desk: given a model name, it returns the correct facts, required API key information, and client object used to make calls.

Pricing turns token counts into money. Tokens are small chunks of text counted by AI APIs. It also fingerprints the price table so old bills can show exactly which prices were used. The catalog skill exposes the live registry to users, so they can ask what models are available. Accounting is the cash register: it records usage, checks spend limits, exports billable records, and builds spending reports.

## [Configuration Manifests, Catalogs, and Generic Utilities](stage-24.md) `stage-24` · (cross-cutting) — 44 files

This stage is the system’s shared toolbox and map room. Some of it runs during startup, some supports normal request handling and background jobs, and much of it simply helps other code find the right pieces later.

The deployment configuration code reads UFO’s local settings and rejects bad setups early, while the extension catalog and lockfile decide which add-ons are available and exactly which ones should be loaded. The many package manifest files are like labels on drawers: they make core areas, control code, browser tools, connectors, documents, memory, evaluation, and other extension folders importable by Python. Shared runtime utilities provide common services, such as paging through long result lists safely and wiring up logs, metrics, and traces while hiding sensitive data. Browser validation files define safe shapes for commands sent to a browser and data received back. The cron helper reads scheduled-task timetables and finds the next run time. The sample extension acts as a test plug, checking that extension hooks still work. Together, these pieces make the larger system predictable, discoverable, and easier to operate.

### [Deployment Configuration and Extension Catalog State](stage-24.1.md) `stage-24.1` — 2 files

This stage is part of startup and setup. It decides what UFO is allowed to do before the server begins real work. Think of it as the system’s checklist and parts shelf: one part reads the local rules, and the other decides which optional add-ons are available.

The configuration file code in core/src/ufo/config.py defines what a valid UFO deployment setup looks like. It reads a single ufo.toml file, checks required settings, and rejects broken or incomplete values early. This helps the system fail fast with a clear problem instead of starting in a confused state.

The extension store in core/src/ufo/ext/store.py connects two things: a catalog of extensions that could be used, and a lockfile that records the exact extensions UFO should load. It supports command-line actions such as searching, installing by pinning an extension, and removing it. Together, these files make startup predictable: UFO knows its settings and exactly which extensions are selected.

### [Shared Core Runtime Utilities](stage-24.2.md) `stage-24.2` — 2 files

This stage is shared behind-the-scenes support. It is not one feature by itself, and it is not only for startup or shutdown. Instead, many parts of the system call these helpers while doing their normal work.

The listings helper gives the project a safe, consistent way to move through long lists a page at a time. A page is just a small slice of a bigger result set, like one screen of search results. It turns an item’s position into a cursor token, which acts like a bookmark. The next request uses that bookmark to continue from the right place, reducing the chance of missing items or showing the same item twice.

The observability helper is the system’s “dashboard wiring.” It sets up tracing, metrics, and structured logs. In plain terms, these record what happened, how long it took, and where work traveled through the system. Before this information leaves the process, it redacts sensitive fields so private data is not accidentally exposed. Together, these utilities make runtime behavior easier to use, inspect, and trust.

### [Browser BUA Wire-Data Validation](stage-24.3.md) `stage-24.3` — 2 files

This stage is behind-the-scenes support for the browser automation part of the system. It does not click buttons itself. Instead, it defines the “vocabulary” and safety checks used when the system talks to a real browser.

The actions.py file describes the allowed browser actions in a clear, structured way: click here, type this text, scroll, wait, or take a screenshot. Think of it as a standard order form. Other parts of the system can fill in that form, and the browser worker can read it without guessing what was meant.

The wire.py file checks data coming back from Chrome DevTools Protocol, the browser’s control and inspection channel. That data arrives as JSON, a common text format for nested values like numbers, strings, lists, and objects. wire.py defines the expected shapes of those values and rejects anything surprising early.

Together, these files keep browser commands and browser responses predictable. They make the main browser engine simpler, because it can rely on checked inputs instead of defending against every possible malformed value.

### [Scheduled Task Cron Parsing Helper](stage-24.4.md) `stage-24.4` — 1 files

This stage is a small behind-the-scenes helper for the scheduled-tasks extension. It is not the code that runs the task itself. Instead, it answers two practical questions: “Is this schedule written correctly?” and “When should it run next?”

The single file, `cron.py`, understands cron expressions. A cron expression is a compact five-part schedule, commonly used to say things like “run every day at 2:00” or “run every Monday morning.” The helper checks that the schedule matches the expected five fields, so invalid schedules can be caught before they cause confusion.

It also calculates the next run time from a valid schedule. This lets the rest of the scheduled-task system focus on storing tasks and starting them at the right moment. In everyday terms, this file is the calendar reader: it does not perform the job, but it reads the timetable and points to the next appointment.

### [Sample Extension API Coverage Module](stage-24.5.md) `stage-24.5` — 1 files

This stage is a built-in “test plug” for the extension system. It is not part of the main user workflow. Instead, it supports development and testing by proving that UFO’s public extension points still work. An extension point is a planned place where outside code can connect to the system, like a socket where an add-on can be plugged in.

The single file, `extensions/sample/ufo_ext_sample.py`, defines a working sample extension. It registers with many of the public hooks, commands, or capabilities that UFO exposes to extensions. When the system loads this sample, it checks whether those connection points are still available and whether an extension can use them as expected.

In practice, this file acts like a smoke test for the extension API. If it loads cleanly and its features behave correctly, developers gain confidence that recent changes have not broken extension support. It is less a reusable tool and more a broad coverage example for keeping the extension doorway healthy.

### [Core and Control Package Manifests](stage-24.6.md) `stage-24.6` — 9 files

This stage is quiet behind-the-scenes support. It does not start the program, run the main loop, or shut anything down. Instead, it makes the project’s folder structure visible to Python. Each __init__.py file is like a label on a drawer, telling Python, “you can import code from here.”

The top-level markers create the main import areas: ufo_control for the control side of the system, and ufo for the core system. Under ufo, the other markers open named drawers for different kinds of core code. ufo.ext is for extensions. ufo.loop is for the main looping machinery, and ufo.loop.prompts is for prompt-related pieces used by that loop. ufo.skills marks skill modules, ufo.sources marks source modules, and ufo.surfaces marks surface modules. ufo.tools marks the tools area and notes that it contains the tool registry, handler context, and built-in tools.

Together, these files form the import skeleton that lets the rest of the codebase find its parts reliably.

### [External Connectivity and Surface Extension Manifests](stage-24.7.md) `stage-24.7` — 10 files

This stage is shared behind-the-scenes support for the project’s extension system. It does not run the main work itself. Instead, it gives Python clear “signposts” for where extension code lives. In Python, an __init__.py file marks a folder as a package, meaning other code can import from it by name, like opening a labeled drawer in a toolbox.

Each file here labels one extension area. The browser package identifies the home for sandbox browser and computer-use tools, plus the browser subagent profile; its bua subfolder is also marked as importable. The web, sites, and sources packages mark areas for web-facing tools, site-specific helpers, and source-related integrations. The connectors package marks the place for connector modules. Composio, Pipedream, Redis Hub, and Slack each get their own package marker so third-party integration code can be found cleanly.

Together, these files create the outer map of the extension surface. They make later startup and runtime code able to discover and import the right extension modules without hardwiring everything into the core system.

### [Agent Workflow, Development, and Evaluation Extension Manifests](stage-24.8.md) `stage-24.8` — 10 files

This stage is shared behind-the-scenes support for the extension system. Each file here is a small Python “package marker”: it tells Python that a folder can be imported as a package, meaning other parts of the project can load code from it. Most of these files do not run any logic themselves. They are more like labels on tool drawers, so the system can find the right tools later.

The brief pipeline, coding, debugger, REPL, research, scheduled tasks, and UFO-internal extension markers simply make those extension areas available for import. The eval environment marker also explains that its package provides fake mailbox and calendar connectors, useful for repeatable tests where results should not depend on real outside services. The self-improvement marker describes an extension that can replay past workspace activity offline and suggest prompt changes for human review. The skill creation marker describes support for agent-owned skills and temporary skills used during one runtime turn. Together, these files make the extension folders visible and self-describing.

### [Document, Memory, and Domain Pack Manifests](stage-24.9.md) `stage-24.9` — 7 files

This stage is shared behind-the-scenes support. It does not run the main work of the system. Instead, it puts “package marker” files in important folders. In Python, a package is a folder that Python knows how to import from, like a labeled drawer in a cabinet. These files mostly exist so the rest of the codebase can reliably find document, memory, and YC-related code.

The document extension marker opens the main document package. Separate markers inside the document-review, PowerPoint, and Excel skill script folders make those script folders importable too, so their tools can be loaded in a standard way. The memory extension marker does the same for memory features, and its short note says this area is meant for saved facts, recall during prompts, page-based memory, and indexing. The YC extension marker and YC pack marker reserve importable namespaces for YC-specific extension code and packaged content. Together, these files act like signs on doors, making the project’s larger parts visible to Python without adding runtime behavior themselves.
