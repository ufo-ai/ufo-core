# System Handbook — Stage Index

Each stage below links to its full page; the paragraph is the stage's role in the system.

## [Operator Entrypoints and Command Dispatch](stage-1.md) `stage-1` — 4 files

This stage is the system’s front desk. It covers the commands a human operator or developer runs before the rest of UFO takes over. The main entrypoint is ufoctl, which reads the user’s command and sends it to the right job: create or run a workspace, inspect its state, sign in through a browser, manage billing or credentials, install extensions, run migrations, seed data, or cancel a stuck turn in an emergency.

Some commands prepare UFO for deployment. The bundle builder creates a complete folder for building a Docker image, including the UFO package, configuration, sandbox client, Dockerfile, and a lockfile that fixes which extensions will be used. Other commands protect the code-running sandbox. The sandbox template builder checks that the hosted E2B sandbox and the local Docker image are built from the same recipe. The proxy gate script then performs a safety test: it launches a fresh sandbox, installs the proxy certificate, and confirms secure web traffic fails only in the expected controlled way.

## [Database Migration and Schema Upgrade/Rollback](stage-2.md) `stage-2` — 179 files

This stage is the database upgrade checkpoint, usually run during install or deployment before the main application does its work. It uses Alembic, a tool that runs ordered database change scripts called migrations. These scripts add, reshape, or remove tables while keeping existing data usable, and many include rollback steps to undo changes if a deployment must be reversed.

The env.py file is the runner. It connects to the database, compares it with the expected layout, and applies missing migrations. The first migration creates the basic filing cabinets: workspaces, users, agents, conversations, turns, and usage costs. Later direct migrations add proposals and a shared extension data store.

The sub-stages then fill out the rest of the system’s durable memory. Core migrations cover conversations, routing, runtime workers, sources, tasks, billing, members, permissions, agents, visibility, and cleanup of retired features. Memory and indexing migrations support search and stored knowledge. Extension migrations add long-term tables for coding, evaluations, sites, skills, monitors, reports, research, sample data, schedules, and web metadata. Together they keep old data moving safely into each new product shape.

### [Core Conversation, Turn, Inbound Message, and Artifact Migrations](stage-2.1.md) `stage-2.1` — 27 files

This stage is shared behind-the-scenes support for the database. It is made of Alembic migrations, which are ordered scripts that change the database layout without rewriting the whole system. These changes help the product remember conversations, turns, messages, and shared files more accurately as features grow.

One group expands conversation records with useful labels: where the conversation started, who it was for, its saved title, Git workspace changes, and links to the sandbox, which is the isolated work area used for code or files. Another group makes turn records richer. A turn is one step in a conversation or agent workflow. These migrations record parent and child turns, subagent details, admission reasons, tracing information for debugging, and stored context such as sender or timezone.

A third group improves turn lookup, including who spoke, which subagent must report back, and which agent turns are active or recent. The inbound message migrations create and refine the intake tray for messages waiting to be processed. The artifact migrations create storage for shared files and add stable IDs and preview data.

#### [Conversation Metadata, Surface, Sandbox, and Titles](stage-2.1.1.md) `stage-2.1.1` — 7 files

This stage is behind-the-scenes database upkeep. It is made of Alembic migrations, which are step-by-step scripts that change the database structure as the product grows. Together, they make conversations carry more of their own context, so the app can reopen, display, search, and manage them more reliably.

The sandbox migrations add links between a conversation and the isolated work area, or sandbox, where its code or files are changed. One stores a sandbox handle for resuming the same durable sandbox later. Another records which conversation owns the sandbox used by turns. The audience migration saves who the conversation was meant for, while being careful not to guess unsafe values for old Slack data. The surface label migration records the product area where the conversation began, using a human-readable name. The workspace change migration stores a summary of Git changes made during the conversation. The title migration adds saved conversation titles and backfills old ones from their first message. The final migration adds a flag showing whether title summarization has already been attempted.

#### [Turn Hierarchy, Admission, Context, and Tracing](stage-2.1.2.md) `stage-2.1.2` — 8 files

This stage is behind-the-scenes database upkeep. It is made of Alembic migrations, which are small ordered changes to the database layout. Together they make the system’s “turn” record richer. A turn is one unit of conversation or work.

The first change lets a turn point to a parent turn, so the system can model main-agent and subagent work like a family tree. It also records which subagent profile was used and whether the conversation happened on the normal command-line surface or a subagent surface. Later, an index makes parent lookups faster.

Other changes add safety and tracking. The run guard fields show which attempt is currently running a turn and whether a resume job has already been queued. Traceparent storage links a subagent’s work back to the trace, or diagnostic path, that created it. Context storage saves incoming details such as sender and timezone before processing.

The remaining migrations record why a turn was admitted, including intent-based admission, remember references created by the turn, and store when a connection request first landed.

#### [Turn Speaker, Spoken-Turn, Subagent, and Agent Indexes](stage-2.1.3.md) `stage-2.1.3` — 6 files

This stage is behind-the-scenes database upkeep. It is not part of a live conversation loop itself; instead, it changes the stored data layout so later parts of the system can ask better questions about turns. A “turn” is one step in a conversation or agent workflow.

The first migration adds fields for who spoke in a turn and how that turn was connected or authorized. This gives the system a clearer record of responsibility. Two migrations then improve lookup of spoken turns: one speeds up finding spoken member turns in a conversation, and another reshapes that shortcut so the database can quickly find turns spoken by a particular speaker.

The subagent migrations support delegated work. One records when a child turn still needs to send a result back to its parent, and adds a fast path to find those unfinished reports. Another stores the subagent’s display name so the activity feed stays consistent after reloads. The final migration adds shortcuts for finding an agent’s active and recent turns as the turn table grows.

#### [Inbound Message Queue Migrations](stage-2.1.4.md) `stage-2.1.4` — 3 files

This stage is part of the behind-the-scenes database upgrade path. It uses Alembic migrations, which are small ordered scripts that change the database structure as the project evolves. Together, these files build and then refine storage for inbound messages: messages that have arrived but have not yet been fully processed.

The first migration, 0033_inbound_message.py, creates the main inbound message table. This is like adding an intake tray where new messages can wait safely until the rest of the system is ready for them. It also adds rules and indexes, which are shortcuts the database uses to find queued messages reliably and quickly.

The second migration, 0034_inbound_rendered.py, adds storage for the rendered text of an inbound message: the human-readable version after the raw message has been turned into displayable content. It also includes a rollback path in case the schema must be reversed.

The third migration, 0035_drop_inbound_rendered.py, removes an older rendered text column from the inbound_message table, while still documenting how to restore it during rollback.

#### [Shared Artifact Storage and Preview Migrations](stage-2.1.5.md) `stage-2.1.5` — 3 files

This stage is behind-the-scenes database setup work. It is made of Alembic migrations, which are ordered scripts that change the database layout as the product grows. Together, they prepare the system to store artifacts, meaning files or generated content shared during a conversation turn, and later attach reliable preview information to them.

The first migration, 0018_surface_seam.py, loosens an older rule about which “surface” values are allowed, so the system is less tightly boxed in. It also creates the shared_artifact table, the main shelf where shared files or artifacts can be recorded. The next migration, 0061_shared_artifact_id.py, gives each shared artifact its own stable identifier, like adding a permanent label to every item on that shelf. This lets later code point to one artifact directly. The final migration, 0077_artifact_preview.py, adds fields for a preview of the artifact’s first rendered page and enforces that preview data is saved all together or not at all.

### [Core Runtime, Surface Routing, Listener, and Turn Delivery Migrations](stage-2.2.md) `stage-2.2` — 16 files

This stage is upgrade work for the database, the system’s long-term memory. It runs behind the scenes when the codebase moves to newer rules. The first changes widen where conversations can live: Slack and web records become valid, and Slack can write messages back. Runtime migrations add records for running worker processes, then loosen them so shared fleet workers do not need one workspace, and later remove old fleet columns. Routing changes make delivery depend on both surface and workspace, add listener claims so only one runtime owns a surface listener, and move iMessage routing away from old shared extension records toward surface installation data and sender addresses like phone numbers. Several cleanup migrations remove stale iMessage project links, claim codes, confirmation replies, and phone opt-in keys. Other changes support day-to-day reliability: job-candidate indexes speed up background searches, artifact media types are corrected for better display, mid-turn replies get their own durable table, BYOK fields record whether a turn used a customer-provided key, and an object-change journal records edits for later tracking.

### [Core Source, Page, and Scheduled Task Migrations](stage-2.3.md) `stage-2.3` — 16 files

This stage is behind-the-scenes setup for the database. A migration is an ordered change that reshapes stored data as the system grows. Here, the system learns how to store content sources, the pages imported from them, and jobs that should run later.

The early source and page migrations create the basic “source” and “page” records, connect them to workspaces, allow extension-provided source backends, track repeated errors, mark removed sources without erasing history, record ownership, and add browsing details such as page title, stream, and original timestamps. Later source changes also remember refusal and “parked” states, so troublesome sources can be set aside with a reason.

The scheduled task migrations build the memory for future work: what task should run, when, who claimed it, whether it expired, which turn it last fired on, and whether it is paused. They also refine task identity so names are unique per agent, add “scheduled” as a valid admission source, and finally remove old pause storage that no longer belongs in the core task table.

### [Core Legacy Extension and Daily Brief Cleanup Migrations](stage-2.4.md) `stage-2.4` — 8 files

This stage is part of database upgrade work, mostly cleanup after older features were removed or moved to newer designs. A database migration is a small, ordered change that updates stored data or table shapes as the software version moves forward. Here, the system tidies up durable leftovers so the main application does not keep seeing dead features as active.

Several migrations retire old extensions. The page alerts migration deletes saved page alert data. The YC migration removes stored YC authorization records and marks YC-related sources and pages as retired. The Exa migration removes Exa references and its saved API key slot. The QuickBooks migration finds sources that lack a company file, which means they cannot sync, then marks them removed and clears their live data.

The Daily Brief and Sweep files handle a feature transition. One migration creates a sweep tracking table. Another clears old Daily Brief agents, conversations, and related records before moving to the newer application-shaped model. Later migrations close an old branch safely, then drop unused Daily Brief/Sweep tables while keeping rollback instructions.

### [Core Billing, Ledger, Seats, and Balance Migrations](stage-2.5.md) `stage-2.5` — 21 files

This stage is shared behind-the-scenes support for billing. It is made of database migrations, which are small upgrade scripts that change how stored records are shaped while keeping existing data. Together they prepare the system to measure usage, charge for it, explain it later, and manage workspace payment rules.

The ledger usage migrations widen the billing notebook. They let ledger rows record new kinds of usage, such as data sent out, sandbox tokens, images, and videos, and they split token counts into clearer parts like input, output, cache reads, and cache writes.

The ledger metadata and export migrations add the “receipt details.” They store pricing snapshots, connect charges to workspaces, track which ledger records were exported, note BYOK exports, add faster lookup by workspace, record exact debits, and freeze per-turn billing data so history stays stable.

The seat migrations reshape how workspace membership is counted, moving from seat limits and bundled seats to a simpler model where old seat-tracking fields are removed.

The balance migrations add spend caps, prepaid workspace balances, top-up records, automatic refill settings, and verification dates for overdraft decisions.

#### [Ledger Usage Dimensions and Token Accounting Migrations](stage-2.5.1.md) `stage-2.5.1` — 6 files

This stage is behind-the-scenes database upkeep. It changes the ledger, the table that records measured usage for billing or tracking, so the rest of the system can store newer kinds of activity without being rejected by old rules. Think of it as widening and relabeling the columns in an accounting notebook.

The early migrations expand what a ledger row is allowed to count. 0012 adds egress, meaning data sent out. 0022 adds sandbox_tokens, for token use inside sandboxed work. 0070 and 0071 add images and videos, so non-text media usage can be recorded too, and both include rollback steps to remove those options if needed.

The later migrations make token accounting more detailed. 0068 splits token counts into prompt tokens and cache-read tokens instead of only keeping one combined total. 0101 goes further by adding fields for input, output, cache reads, cache writes, and whether pricing used a user-provided key. Together, these migrations let the ledger describe both what kind of usage happened and how that usage breaks down.

#### [Ledger Metadata, Exports, and Billing Record Migrations](stage-2.5.2.md) `stage-2.5.2` — 7 files

This stage is behind-the-scenes database preparation for billing and audit history. A database migration is a small, ordered change to the shape of stored data. Together, these migrations make the ledger more useful as the system records charges, exports records, and explains past billing decisions.

First, the ledger gains a price digest, a text snapshot that helps later reviewers understand what pricing information was used. Then ledger entries are loosened so they can be tied to a workspace, not only to a single turn, which covers charges that belong to a broader area of work. A new ledger export table records which ledger data has been sent to outside consumers, and a later change marks whether that export used BYOK, meaning a customer-provided encryption key.

Another migration adds a shortcut for finding a workspace’s ledger records in creation order, like adding an index to a filing cabinet. The debit field records the exact amount actually taken from a balance, in micro-dollars. Finally, per-turn billing data is frozen into its own stored record, so later changes do not rewrite what a turn cost at the time.

#### [Seat and Workspace Membership Model Migrations](stage-2.5.3.md) `stage-2.5.3` — 4 files

This stage is part of the system’s behind-the-scenes upgrade path. It changes the database structure as the product’s idea of “workspace membership” evolves. A database migration is a small scripted change that moves stored data from an old shape to a new one, like remodeling a room without losing what is inside.

The first migration, 0039, introduces “seats”: paid or allocated member slots. It records when a member gets a seat and lets each workspace set an optional positive seat limit. The next migration, 0041, adds “included seats,” meaning seats bundled with a workspace plan, and adds rules so the value is either blank or positive.

Later, 0084 changes direction. Workspaces no longer need fixed seat limits, so it marks all existing members as seated, removes the old limit fields, and clears approval data that no longer applies. Finally, 0106 removes an obsolete “seat shipped” marker, since seats are no longer shipped or tracked that way.

#### [Workspace Balance, Spend Cap, and Top-Up Migrations](stage-2.5.4.md) `stage-2.5.4` — 4 files

This stage is part of the behind-the-scenes setup that changes the database as the product grows. A database migration is a small upgrade script that adds or changes stored information without rebuilding everything from scratch. Here, the system is learning how to manage prepaid workspace money and limits on spending.

First, 0011_spend_cap.py adds a place to store spending rules, such as “this workspace, person, or agent can spend only this much during this period.” It also updates the possible states of a “turn,” which is a unit of work the system processes. Next, 0087_workspace_balance.py adds the core prepaid balance records: one table for the current workspace balance, and another for each purchase that increased it. Then, 0099_balance_auto_topup.py adds settings for automatic refills, like a fuel tank that reorders fuel when it gets low. Finally, 0102_balance_topup_verified.py records when a top-up was first verified, so the system can decide when a workspace qualifies for overdraft access.

### [Core Agents, Members, Grants, and Connections Migrations](stage-2.6.md) `stage-2.6` — 24 files

This stage is behind-the-scenes upgrade work for the database. A migration is a careful change to stored data, like adding new drawers and labels to a filing cabinet before the application can use new features safely.

Its parts update the core records that describe agents, people, permissions, and outside links. The credential and grant migrations create secure places for secrets, then reshape permissions from one-off grants into reusable connections and agent-specific access rules. They also add source-reading permissions and a workspace counter that tells the network proxy when access rules need rebuilding.

The agent configuration migrations add settings such as internet access, reasoning level, sandbox size, provisioning details, and spawn input/output fields. The member and audit migrations record who actions belong to, mark key workspace controllers, speed up email lookup, store time zones, and log private transcript access by admins. Finally, the model remapping migrations rewrite old saved model names and settings so existing agents keep working when providers rename or retire models.

#### [Credential, Grant, Connection, and Source Permission Migrations](stage-2.6.1.md) `stage-2.6.1` — 7 files

This stage is part of database upgrades. It changes the stored shape of the system’s data so workspaces, agents, and outside services can use permissions safely. First, 0002 creates a place to store encrypted credentials, tied to a workspace, like a locked cabinet for secrets. Then 0014 adds grants, which record that a member allowed an agent to use an outside provider in a specific conversation. 0043 adds a simple shared flag to those grants.

Later, 0057 reorganizes that older grant idea into two parts: a reusable connection to an account, and a separate permission saying which agent may use it. 0079 refines sharing again by moving the shared setting onto the connection itself, and adds an optional account label so people can recognize it. 0059 adds source grants, which say which agents may read which sources, and backfills existing live sources so old setups keep working. Finally, 0093 adds a workspace counter that changes when network access rules may be stale, prompting the proxy to rebuild them.

#### [Agent Configuration, Binding, Provisioning, and Spawn Migrations](stage-2.6.2.md) `stage-2.6.2` — 7 files

This stage is behind-the-scenes database upkeep. It runs during upgrades, before the main application can safely use newer agent features. A migration is an ordered change to the database, like adding new labeled drawers to a filing cabinet so the code has somewhere to store new information.

The first group adds agent settings. One migration records whether an agent may use the internet. Another adds a reasoning setting and limits it to known choices. A third adds a required sandbox size, limited to small, medium, or large, so the system knows how much protected workspace to give the agent.

Other migrations connect agents to the rest of the product. One links surface installations and conversations to the agent they belong to. Another stores provisioning details: where an agent came from, which tools it may use, and rules for identifying it inside a workspace. A setup migration adds space for extra saved setup data. The spawn migration adds fields describing an agent’s expected input, output, and owning member.

#### [Member, Workspace Control, and Transcript Audit Migrations](stage-2.6.3.md) `stage-2.6.3` — 6 files

This stage is behind-the-scenes database preparation. It changes the shape of stored data so the rest of the system can better track who did what, find key people quickly, and protect private transcript access. The migrations work like careful renovation steps: each one adds or removes a specific shelf in the database, and most include a way to undo the change if needed.

One migration adds member attribution to turns and scheduled tasks, so the system can say which member an action was done for or created by. Another marks the controlling member and controlling agent for each workspace, making the workspace’s main admin and agent easy to identify. A privacy-focused migration creates a transcript access audit table, recording when an admin reads another member’s private transcript. The next migration removes an unused index from that table to keep the database lean. Another adds an index on member email addresses, which helps sign-in find members faster. The final migration stores each member’s latest valid time zone, helping time-based features use the right local time.

#### [Agent Model Remapping and Fable Compatibility Migrations](stage-2.6.4.md) `stage-2.6.4` — 4 files

This stage is behind-the-scenes upgrade work for saved agents. When outside model providers change the names or settings needed to use their models, old agent records in the database can point to names that no longer work. These database migrations act like forwarding labels on moved mail, rewriting old saved settings so agents keep running after an update.

The Bedrock migration repoints agents away from dropped Bedrock model IDs and onto replacement IDs that the system still serves. The first Fable migration fixes a behavior setting: agents using Claude Fable must have at least “low reasoning,” meaning a small amount of extra thinking effort, instead of reasoning being turned off. The next Fable migration changes agents from an old Anthropic Fable name to the identifier Anthropic actually serves. The final migration handles another naming change for Claude Fable 5 through OpenRouter, a service that routes requests to different model providers, and includes a reverse path so the database can be rolled back safely. Together, these steps preserve old agents while the model ecosystem changes around them.

### [Core Agent Visibility, Icons, Archiving, and Built-In App Migrations](stage-2.7.md) `stage-2.7` — 13 files

This stage is behind-the-scenes upgrade work. It changes stored database records so older workspaces match newer ideas about agents and built-in apps. First, agents gain clearer presentation and access rules: a visibility setting says whether an agent is private or workspace-wide, an icon field controls how it appears, the default icon is updated, and a purpose field can describe what the agent is for. Member records also gain invitation details, recording when someone was invited and by whom.

The stage also improves lifecycle control. Agents can now be archived instead of deleted, while the main workspace agent is protected from being archived. Archived agents have their old public names moved aside so active agents can reuse those names. Another setting lets an agent use the workspace’s shared skills.

Finally, several built-in app agents are brought into line with newer product design. Coding agents move to the new app identity with updated name and icon. Old Tasks agents are archived. Chat becomes the main agent. Wiki agents are made private. App icon records are also corrected.

### [Memory, Knowledge Graph, and Indexing Migrations](stage-2.8.md) `stage-2.8` — 21 files

This stage is behind-the-scenes upgrade work for how the system stores and finds knowledge. It is not the main user-facing work loop. Instead, it is a set of database migrations, meaning scripted changes that reshape the database safely as the product evolves.

It starts by showing the old core knowledge graph: tables for things and the links between them. That approach is then retired as the system moves to a simpler memory surface. Page revisions are also given clear per-workspace sequence numbers, so edits can be ordered reliably.

Next, the search chunk migrations build storage for small pieces of text used in search. They add both meaning-based lookup, through embeddings, and workspace separation. The memory base migrations create the main shelves for memory items and memory pages, then scope them to workspaces.

Later migrations make memory faster to browse and more time-aware, adding indexes and “as of” dates. Provenance migrations record which pages and page versions memories came from. The final group adds audiences, retirement markers, richer memory classes, and member profiles, making stored knowledge easier to organize, trace, and maintain.

#### [Core Knowledge Graph Retirement and Page Revision Migrations](stage-2.8.1.md) `stage-2.8.1` — 3 files

This stage is part of the system’s behind-the-scenes database history. It records an older way the product stored “knowledge” as a graph, then shows how that structure was retired, and finally adjusts how page changes are ordered. A database migration is a scripted change to the database layout, like renovating shelves in a library while keeping track of how to undo the work if needed.

The first graph migration creates the original knowledge graph tables. One table stores named things, and another stores the links or relationships between those things. Later, the one-memory-surface migration removes those graph tables as the system moves to a simpler single place for memory and content. It still includes rollback steps, so the old graph tables can be recreated if the change must be reversed.

The page revision migration solves a different ordering problem. Instead of relying on timestamps, which can be unclear when edits happen close together, it gives each workspace its own steadily increasing page revision number. This makes page changes easier to read in a reliable sequence.

#### [Default Search Chunk Index Migrations](stage-2.8.2.md) `stage-2.8.2` — 2 files

This stage is part of setup and upgrades. It prepares the database tables used by the default search index, so the rest of the system can store and find small pieces of content called chunks. A chunk is a slice of text taken from a larger document, like a card in a library catalog.

The first migration, 0001_chunk.py, builds the main storage for these chunks. It records the chunk text, its identity, and its embedding. An embedding is a list of numbers that represents the meaning of the text, so similar ideas can be found even when the words differ. It also supports normal word-based search, and on PostgreSQL it can use vector search to compare embeddings efficiently.

The second migration, 0002_chunk_workspace_id.py, adjusts that storage so chunks belong to a workspace. This prevents clashes when two workspaces contain chunks with the same digest, or fingerprint. Together, these migrations create a search-ready, workspace-aware chunk store.

#### [Memory Extension Base Tables and Workspace Scoping](stage-2.8.3.md) `stage-2.8.3` — 4 files

This stage is part of setup and upgrading, before the memory extension can do its normal work. It uses database migrations, which are small step-by-step changes to the database structure. Together, these files build the basic storage shelves for the system’s memory.

The first migration creates the main memory table, where remembered facts or notes can be saved. The second adds a separate table for memory pages, which act like larger containers or notebook pages for related memory content. The third improves each memory item by adding its kind, meaning what type of memory it is, and a confidence value, meaning how sure the system is about it. The fourth connects each memory page to a workspace, so memories are kept inside the right project area. It also makes cleanup safer: if a workspace is deleted, its memory pages can be removed with it.

Each migration also includes a rollback path, so the change can be undone if needed.

#### [Memory Indexes and Information Time](stage-2.8.4.md) `stage-2.8.4` — 4 files

This stage is behind-the-scenes upgrade work for the memory system. It does not create memories itself. Instead, it changes the database so stored memories can be found faster and understood in the right time context.

The first migration adds a shortcut, called an index, for finding older memories that are still current and may need to be merged or summarized. This keeps routine memory cleanup from slowing down as the table grows. The second migration adds another index for the inventory view, so it can quickly show the newest memory items for one workspace without searching everything.

The third migration adds an optional “as of” timestamp to each memory item. This means the system can record when the information was true, not just when it was stored. The fourth migration backfills that timestamp for memories created from pages, copying the page’s update time, or creation time if needed. Together, these changes make memory browsing faster and memory facts more historically accurate.

#### [Memory Page Provenance and Revision Links](stage-2.8.5.md) `stage-2.8.5` — 3 files

This stage is behind-the-scenes database upkeep for the memory system. It changes how stored memories record their origin, so later code can answer clearer questions like “which page did this come from?” and “which version of that page was it based on?”

The first migration, 0009, adds a dedicated source-page link to each memory item. Before this, page information could be mixed into a more general source field, like writing an address in the margin instead of in the address box. The migration moves existing page-like data into the new proper place.

The second migration, 0010, adds page revision tracking. A revision means a specific saved version of a page. This matters because a page can change over time, and a memory should be traceable to the exact version that produced it.

The third migration, 0012, makes provenance more flexible. It allows a memory to be connected to multiple source pages, instead of forcing each source to act like a separate identity. Together, these changes make memory history more accurate and easier to audit.

#### [Memory Audiences, Lifecycle, Classes, and Profiles](stage-2.8.6.md) `stage-2.8.6` — 5 files

This stage is part of the system’s behind-the-scenes upgrade path. It changes the database shape so the memory feature can describe more kinds of stored knowledge. A database migration is a small step that updates stored tables and rules when the software version changes.

First, the audience rules are widened. Memory items can now be aimed at a room or an outside “foreign” audience, not only a shared space or one member. Next, memory items gain a retired_at time stamp. This lets the system say “we deliberately stopped using this” without treating it as simply replaced by another memory.

The next two steps expand the types of memory content. A section class allows a memory item to represent one section of a larger page. An overview class allows an item to hold the opening summary for a wiki-style page.

Finally, the stage adds a profile table for workspace members. It stores what the workspace knows about a person, such as their role, focus, and when that profile was written. Together, these migrations make memory more precise, organized, and easier to manage over time.

### [Extension Workflow, Integration, Trigger, and Web Migrations](stage-2.9.md) `stage-2.9` — 17 files

This stage is behind-the-scenes upgrade work. Each file is a database migration: a planned change to the stored data layout, with a way to undo it if the upgrade is rolled back. Together they give extensions their own durable storage and move older data into newer shared places.

The coding migrations build the review inbox and review-run records, link runs to conversations, remove an old direct conversation field, then move review setup into the shared source-trigger system. The evaluation migration creates fake email and calendar tables for test workspaces. Monitor, objectives, report digest, research, sample, and scheduled-task migrations add tables for repeating checks, goal steps and evidence, report summaries and unchanged readings, remembered research sources, sample notes, and paused conversations.

The sources migrations create the source-trigger table, move old subscription data into it, and add delivery settings. The web migrations tidy chat metadata: one backfills older web chat rows into shared extension storage, and the next moves chat titles into the main conversation table. In short, this stage prepares the database so extension features can keep their history, schedules, and metadata consistently across upgrades.

### [Sites and Skill-Creation Extension Migrations](stage-2.10.md) `stage-2.10` — 12 files

This stage is behind-the-scenes upgrade work for the database, the system’s long-term memory. These migration files run when the product is installed or updated, so older data can keep working while new site and skill features are added.

The site migrations build up the hosted_site table step by step. They first create the basic record for a hosted site: workspace, conversation, name, port, visibility, creator, and dates. Later migrations add a generation number, a link to a homepage agent, preview file details, share-card storage, deployment version tracking, and an optional source manifest. One migration also updates old seeded homepage sites from private to workspace-visible, but only when the data shows that this is safe.

The skill-creation migrations do the same for user-made skills. They create the first user_skill table, then change ownership from workspace-level to agent-level, add routing-card fields for display and discovery, and finally move skills back to workspace ownership while removing duplicate names. Together, these files reshape stored data without losing it.

## [Configuration, Pack Selection, and Extension Discovery](stage-3.md) `stage-3` — 84 files

This stage is startup setup. Before the assistant can work, the system must know which configuration to trust, which feature packs are active, and which extensions are installed. It is like opening a toolbox, checking the instruction sheet, and laying out only the approved tools.

The configuration files define the expected ufo.toml settings and shared connection rules for model providers and the database. The extension store reads the public catalog, lets ufoctl choose extensions, and saves those choices in a lockfile so the same set can be loaded again. The extension loader then acts as the gatekeeper: it discovers installed extensions, checks that they fit together safely, and registers their tools, skills, routes, credentials, hooks, object types, and backends.

The sub-stages supply the pieces being registered. Packs define ready-made bundles for local, hosted, billing, and evaluation use. Manifests describe apps, skills, web surfaces, workflows, external services, and providers. Provider registration adds models, feature flags, sandboxes, connectors, and live transports. The many package-root files are simple Python nameplates that make extension folders importable. Together, these parts build the enabled capability set for the rest of the system.

### [Application and Skill Bundle Registration](stage-3.1.md) `stage-3.1` — 12 files

This stage is the system’s sign-up desk for apps and skill bundles. It runs as shared setup support, before onboarding or a user turn needs to create an agent or load a skill. Each manifest is like a labeled plug that tells the host, “I exist, this is my agent, these are my instructions, and these are the skills or scheduled jobs I bring.”

The app manifests register workspace apps: Artifacts, Chat, Code, Issues, Meetings, Metrics, Radar, and Wiki. They tell the platform how each app should appear, what workspace agent to create, what setup or accounts it needs, and which homepage skill to load. Code focuses on pull-request review, Issues and Meetings can add scheduled work, Metrics reports delivery information, and the others provide their own workspace-facing agents.

The brief-pipeline and documents manifests add reusable writing tools, including specialist subagents and document-making skills. The sample skill probe is a simple load test that prints a success message. The skill-create extension registers user-made workspace skills so later turns can save, find, load, update, or delete them.

### [Provider Backend Registration](stage-3.2.md) `stage-3.2` — 9 files

This stage is the system’s plug-in counter. During startup, it registers the outside services and interchangeable backends that the rest of UFO may use later. Instead of every feature knowing every provider’s details, these files put names, settings, and factory functions in central places.

The feature flag doorway in flags.py lets code ask “is this on?” while a deployment can plug in Cloudflare Flagship through the Flagship extension. The model registry keeps the master catalog of AI models, including prices and the code needed to create a provider client; the Bedrock extension adds Amazon Bedrock-backed models to that catalog. The OpenAI embedding extension adds a service that turns text into number lists, called vectors, for search and memory.

For running workspaces, sandbox/select.py chooses the sandbox carrier, such as local or remote. Redis Hub registers Redis as shared live transport so multiple servers can share terminal and frame updates. The sources registry maps connector names to connector classes. The Pipedream manifest adds Pipedream connectors, sign-in routes, and a broker for running actions safely without leaking user secrets.

### [Pack Definitions and Evaluation Bundles](stage-3.3.md) `stage-3.3` — 7 files

This stage is behind-the-scenes setup support. It defines “packs,” which are named bundles of features the system can turn on together, like choosing a preset mode instead of listing every part by hand. These files do not run the assistant’s main conversation loop. They tell startup and evaluation code what capabilities, tools, services, and test connectors belong in each mode.

The local assistant pack groups the normal assistant features for development. The assistant billing pack adds Metronome billing support on top of local assistant behavior so developers can test billing setup without using the full hosted deployment. The hosted assistant pack describes the production-style bundle, including hosted infrastructure and back-end services. The assistant evaluation pack starts from the normal assistant setup, removes real external broker pieces that would break in tests, and adds evaluation tools such as a Docker sandbox. The DSQA and GDPVal files define ready-made evaluation bundles for different test needs, from small core setups to search, browser, document, research, or all-in configurations. The sample pack is a working public SDK example that proves packs can add an extension, a skill, and an onboarding step.

### [Assistant, Web, and Workflow Extension Manifests](stage-3.4.md) `stage-3.4` — 11 files

This stage is the system’s set of extension “registration cards.” During startup, the host reads these manifest files to learn what extra abilities are available, how to load them, and when to run background work. The browser, coding, research, debugger, sites, and UFO manifests add user-facing or assistant-facing capabilities: browser delegation, coding helpers with GitHub access, research tools and shared conversation data, a debugger tool and web surface, website-building screens and safety checks, and the UFO extension’s own user surface. The web manifest connects the portal side, declaring routes, permissions, feature flags, side panels, and jobs. The monitors, scheduled-tasks, and self-improvement manifests add ongoing behind-the-scenes work: checking due monitors, running scheduled tasks, and periodically proposing, replaying, and grading improvements. The objectives manifest helps steer active conversations by reminding an agent about unfinished objectives at the start of each turn. Together, these files act like labeled plugs on a power strip: each tells the core system what it can provide, and the core decides how to make those pieces available.

### [External Integration Extension Manifests](stage-3.5.md) `stage-3.5` — 6 files

This stage is the system’s set of “registration cards” for extensions that connect UFO to outside services. It is mostly used during startup and setup, when the host application needs to discover what each extension can do, what secrets it needs, and which background jobs or web routes to enable.

The Composio manifest advertises available connectors, sign-in rules, and the OAuth consent route, which is the web step where a user approves access. The shared connectors manifest packages common connector tools, data objects, and assistant instructions. The gbrain manifest registers a sync tool that can pull Markdown pages from GitHub or a local folder. The iMessage manifest declares the messaging tool, send and receive behavior, and required secret settings. The Slack manifest is a full setup sheet for Slack, including credentials, routes, hooks, tools, and setup help. The sources manifest registers source providers, their credentials, event reactions, and a background sync job. Together, these files let the main system plug in external services cleanly.

### [Workspace App and User Surface Package Roots](stage-3.6.md) `stage-3.6` — 10 files

This stage is quiet behind-the-scenes support. It does not start features, run the main work loop, or shut anything down. Instead, it makes several extension folders visible to Python’s import system. In Python, an `__init__.py` file is like a nameplate on a room: it tells the program, “this folder is a package you can enter and load code from.”

Each file here is one of those nameplates. The app issue, meeting, and wiki packages make workspace app extensions importable. The debugger and REPL packages make developer-facing tools available. The iMessage, Slack, sites, web, and UFO packages mark communication, site, web, and core user-surface extension areas as importable packages.

These files do not register extensions, configure them, or run feature logic. They simply prepare the paths so that other parts of the system can later import the real modules inside these folders. They are small but necessary connectors in the project’s packaging structure.

### [Tool, Connector, and Provider Package Roots](stage-3.7.md) `stage-3.7` — 10 files

This stage is quiet behind-the-scenes setup for the extension system. Python only lets code be imported from folders that are treated as packages. These small __init__.py files act like nameplates on doors, telling Python “this folder is part of the project.” They do not start tools, register manifests, or run meaningful logic themselves.

Together, they make different extension areas visible during discovery. The browser package opens the area for sandbox browser and computer-use tools, and its bua subfolder becomes importable too. The coding, Composio, Pipedream, Redis Hub, connector, and source packages each make their own extension modules reachable by the rest of the codebase. The eval environment package marks a special testing area that provides predictable fake mailbox and calendar connector providers. Inside the sources area, the providers package makes provider modules importable.

In everyday terms, this stage labels the shelves before the system looks for tools on them.

### [Knowledge, Automation, and Skill Package Roots](stage-3.8.md) `stage-3.8` — 13 files

This stage is shared behind-the-scenes support. It does not start jobs or run the main work loop. Instead, it provides “front doors” for extension packages. In Python, an __init__.py file tells the system that a folder can be imported as a package, like putting a label on a drawer so other code can find what is inside.

Most files here are simple labels. The app metrics, app radar, monitors, research, report digest, scheduled tasks, gbrain, objectives, and brief pipeline initializers make those extension folders importable. A few also add a short human-readable description, but they still do not perform runtime setup. The office-xlsx scripts initializer does the same for script modules inside a document skill.

Three package roots also describe their extension’s purpose. The memory package is for long-lasting facts, recall during prompts, page-based updates, and background indexing. The self-improvement package is for learning from workspace activity and proposing human-approved prompt changes. The skill-create package is for agent-authored skills and skills available during a turn.

## [Workspace Onboarding and Initial Seeding](stage-4.md) `stage-4` — 6 files

This stage is the front door for a new workspace. It runs during first setup, or when the system needs to find an existing workspace and finish its basic setup. The main coordinator is onboarding.py. It creates the workspace, adds the first administrator, makes the main assistant agent, checks that required secrets are present, and then gives installed extensions a chance to set themselves up.

The control-plane API in onboard_control.py is the trusted bridge used by the Rust sign-in and control layer. It creates or finds workspaces, seats members, and answers onboarding questions without spreading sensitive rules across the system. Provisioning.py installs agent definitions that come from extensions, while carefully keeping any changes members already made. Agent_setup.py works out what an agent still needs, such as credentials, connected accounts, or a schedule, and turns that into setup screens or a temporary helper skill.

Seed.py can add a safe demo “kitchen sink” conversation, like a showroom model, so new users can see many product features at once. __init__.py simply makes this folder importable by other Python code.

## [Serve Process Bootstrap and Runtime Supervision](stage-5.md) `stage-5` — 3 files

This stage is the service’s launch pad and safety watch. It runs when the long-lived UFO server starts, then continues working behind the scenes while the service is alive. The main entry point is serve.py. It brings the system’s major parts online: the web server, databases, background workers, extension jobs, sandboxed execution areas, credentials, storage, live update channels, and workspace boundaries that keep one workspace’s work from spilling into another.

The runtime package marker, __init__.py, is just a signpost for Python. It tells Python that the runtime folder can be imported as a package; it adds no behavior itself.

runtime_instance.py acts like a check-in desk and night watchman. It records that this serve process is alive so other processes can see it. It also runs cleanup loops that look for trouble: jobs left behind by dead processes, child work that should stop after a parent was cancelled, and running turns whose workflow has disappeared. Together, these pieces start the service and keep its work from getting stranded.

## [Authenticated Ingress and User Surfaces](stage-6.md) `stage-6` — 21 files

This stage is the system’s set of guarded front doors. It runs during normal use, whenever a request arrives from a browser, Slack, iMessage, the terminal, a shared artifact link, an OAuth return page, or a hosted site. Its first job is to check who is knocking, then connect that request to the right workspace, member, conversation, or operator view.

The Browser Web Portal Surface handles the main signed-in web app: chat, settings, admin pages, memory views, usage pages, and live screens. Chat, Terminal, and Messaging Surfaces adapt Slack, iMessage, and command-line messages into the system’s common conversation shape, then send replies back out. Artifact, OAuth, Site, and Debugger Routes are side doors for shared files, login handoffs, public hosted sites, and safe read-only inspection.

The shared surface bridge is the adapter layer between these outside surfaces and the core system. The operator support file adds stricter login rules for powerful internal tools and builds a fleet directory of workspaces and conversations. The package marker simply makes the surfaces code importable.

### [Browser Web Portal Surface](stage-6.1.md) `stage-6.1` — 7 files

This stage is the browser-facing front door of the system. It is part of the live main interface: the place where a member signs in, sees agents, chats, changes settings, and opens pages like memory, usage, connections, objects, admin views, and live streams.

The main web surface serves the browser app and protects it with a session cookie, which is a small browser-stored sign-in token. It also provides the routes the app uses to read data, send chat messages, and make changes. The audience code acts like a door attendant. It decides which members may see or chat with which agents, and gives admins tools to grant access or inspect private transcripts. Panels turns setup and settings form submissions into ordinary agent tool calls, so button clicks fit the same system as chat actions. Starters prepares the suggested prompts on the start screen, using remembered work and short-term caching to stay fast. Community reads public skills from skills.sh and presents errors clearly. The memory surface lets authorized operators inspect saved workspace memory. The chat package marker simply makes the chat extension importable.

### [Chat, Terminal, and Messaging Surfaces](stage-6.2.md) `stage-6.2` — 6 files

This stage is the set of “front doors” where people talk to the system through Slack, iMessage, or the command-line terminal. It sits around the main work loop: it accepts outside messages, turns them into the project’s common conversation format, then delivers the agent’s replies back to the same place.

The Slack surface is the busiest doorway. It receives Slack messages, button clicks, file shares, app installs, and thread changes, then sends replies and files back. Slack mentions are translated both ways: hidden Slack codes become readable names, and selected names become real Slack mentions when replying. Slack attribution adds a visible bot reference to connector-sent messages, while making sure that added mention is not later treated as a fresh request.

The iMessage surface does the same kind of translation for iMessage conversations. Its cloud bridge handles the lower-level connection to Spectrum’s service: credentials, network calls, attachments, incoming events, and provider-ready message objects.

The terminal surface exposes the system to the ufo command-line client using simple tab-separated text commands for messages, progress, files, prompts, and local terminal actions.

### [Artifact, OAuth, Site, and Debugger Routes](stage-6.3.md) `stage-6.3` — 5 files

This stage is a set of side doors into the system. It is not the main work loop. Instead, it supports sharing, login handoffs, hosted site access, and safe inspection by trusted operators.

The sandbox ingress server receives public site requests. It checks signed site links, turns them into short-lived browser sessions, and then serves the site either from saved files in blob storage or by forwarding traffic to a live sandbox. The hosted site surface sits in front of that. It shows the public frame for a permanent site link, manages redirects to the real site origin, and decides who is allowed to enter before any site content is shown.

The artifact route does the same kind of guarding for shared files. It serves downloads or previews only when the link has a valid signature, and it can refresh expired links for signed-in workspace members. The CLI surface finishes OAuth, which is the “approve access on another service” flow, after the outside provider redirects back. The debugger surface gives trusted operators read-only pages and APIs to inspect sessions without changing them.

## [Conversation Admission, Membership, and Turn Creation](stage-7.md) `stage-7` — 3 files

This stage is the system’s front door for conversation work. It runs before the main engine starts thinking or using tools. Its job is to turn outside “surface” events, such as a chat message, a scheduled wake-up, or an internal extension result, into a safe, durable turn record that the engine can later pick up and process.

The main gate is admission.py. It gathers the needed context: who sent the message, which room or workspace it belongs to, what visibility rules apply, and whether the audience should be limited for private or external spaces. It also accepts tool-submitted intents, so internally generated requests enter through the same controlled path as human messages.

ambient_reply.py is a smaller decision helper for group threads. If someone talks in a shared thread without directly mentioning the agent, it decides whether the agent should respond or stay quiet, avoiding unwanted interruptions and wasted work.

__init__.py simply marks the turns area as a package so the code can be organized there.

## [Workspace Object APIs and Portal State Views](stage-8.md) `stage-8` — 18 files

This stage is shared support for the whole system. It is the “front desk” where tools, agents, and the portal ask to see or change workspace records in a safe way. The central object system routes each request, checks names, ownership, visibility, and allowed actions, while object names and object scope keep identities and agent attribution consistent.

Built-in object kinds cover the workspace itself, members, credentials, installed extensions, agents, and conversations. These give safe views of the roster, seats, secret slots, extension capabilities, agent settings, and past chats without exposing private data or allowing the wrong edits.

Extension files plug more record types into the same front desk. Memory exposes stored memories and profiles as read-only records. Monitors show command watches that can be deleted to stop them. Scheduled tasks and report digests expose recurring work, waiting tools, and generated digest results. Sites expose hosted websites and add a “Sites” section to conversations. Sources and gbrain sources expose synced pages and Markdown source collections. Todos expose conversation checklists. Together, these files make many different features feel like one consistent workspace object API.

## [Turn Claim, Context Assembly, Skills, and Prompt Construction](stage-9.md) `stage-9` — 14 files

This stage prepares one unit of agent work before and during a turn. A “turn” is one pass where the agent reads the latest conversation, thinks, may use tools, and writes back. The queue runner claims pending work so two workers do not handle the same turn. The turn engine then gathers the needed context, runs the model and tools, watches for new messages, records costs, and saves either the result or a safe failure.

Several helpers assemble what the model will see. Conversation compaction shortens old chat history into a checked summary so it fits inside the model’s reading limit, while keeping recent messages intact. Skill loading reads built-in and user-created skills, resolves their dependencies, and puts selected ones into the sandbox. Skill selection keeps the list small and relevant. The skill store preserves user-made skills and prevents unsafe names, overwrites, and conflicts.

Other files add live reference material: spawn catalogs describe which subagents can be started, the model catalog lists available AI models, automations summarize scheduled tasks, and the delivery register supplies reply-format rules. Finally, prompt rendering fills templates and checks the completed system prompt before the model sees it.

## [Model Invocation, Streaming, and Spend Admission](stage-10.md) `stage-10` — 8 files

This stage is part of the main work loop, when UFO needs to ask a language model for the next answer or action. It is the system’s “translator and cashier.” It checks that a model can be used, prepares the request in the format each provider expects, streams the reply back in UFO’s own common event format, and records usage so cost can be calculated.

The models package marker simply makes this model code importable. The Anthropic bridge talks to Anthropic’s API and hides Anthropic-only details such as special content blocks, retry behavior, and usage reporting. The OpenAI bridge does the same for OpenAI-style services, including chat messages, tools, images, reasoning data, and streamed replies. The pricing code turns token counts into billable cost and records which price table was used.

OpenRouter is added as an extension provider, including image and video generation with proper saving and billing. The self-improvement model adapter gives that extension a small, safe way to call models. Its proposer asks for better agent prompts after failures, and its replay code tests a new prompt against an old conversation without rerunning tools.

## [Tool Dispatch and Sandboxed Execution](stage-11.md) `stage-11` — 24 files

This stage is part of the main work loop. When the model asks to use a tool, the system must find that tool, check that the request is allowed, and run it without letting it escape its limits. The tool registry is the catalog: it names each tool, describes its inputs, and lets the engine look it up. The built-in tools do the practical jobs, such as shell commands, file edits, search, user questions, subagents, skills, account access, and secrets. The tool context is the guarded doorway each tool uses to reach files, sandboxes, billing, credentials, and permissions.

The sandbox support provides the safe workbench where commands and files live. It can be local, Docker-based, remote, or connected to a user terminal, while path checks keep file access inside bounds. Task tracking lets long shell work continue after a timeout.

The bridge files let code inside a sandbox request approved tools through a strict JSON contract and the normal turn loop. MCP adds outside workspace tools. The REPL extension adds persistent JavaScript and Python sessions for step-by-step coding.

### [Sandbox Carriers, Terminal Bridges, and File Boundaries](stage-11.1.md) `stage-11.1` — 15 files

This stage is shared behind-the-scenes support for running work safely outside the main server. A sandbox is an isolated workspace where commands run and files live, like a fenced workbench for one conversation. session.py defines the common workbench contract, while conversation.py opens, resumes, reads, writes, lists, and cleans up the right conversation’s files. containment.py is the safety gate that keeps file paths inside their allowed folder.

Different “carriers” provide the actual workbench. local.py uses a normal local directory and subprocesses. ufo_ext_docker.py runs work in Docker containers. ufo_ext_e2b.py connects to remote E2B Linux machines. terminal.py lets a user’s own connected terminal act as the sandbox, and stream_terminal.py carries terminal messages across server pods using Redis.

The rest are support pieces. cache.py configures safe reused downloads. client_binary.py finds the built UFO client program. exec_env.py prepares limited environment variables without exposing real secrets. preview.py names the private preview service safely. ingress_host.py and ingress_url.py create checked web addresses for sandbox-hosted sites. __init__.py only makes the folder importable.

## [Browser, Site, Research, and Document Workflows](stage-12.md) `stage-12` — 58 files

This stage is where the system does high-level “production” work during a turn. It is not just thinking or planning. It opens browsers, researches the web, builds small sites, and prepares documents that users can download or review.

The browser and hosted-site part works like a remote-controlled web desk. It can start Chrome, read what is on a page, click buttons, fill forms, take screenshots, watch downloads, and publish a built site after testing it. This lets the agent interact with real websites and check its own web apps.

The document and report part is a file workshop. It edits and reviews Word, PowerPoint, Excel, PDF, and report files. It can add comments, repair slide decks, refresh spreadsheet formulas, fill PDF forms, preview pages, and produce summaries.

The research files add web investigation. Search and fetch tools gather pages or specialized results. A wide-research tool runs many related searches in parallel and saves the combined findings as JSON, a structured text format. Source-tracking code remembers useful links and shows them later in a Sources panel. A small package marker file simply makes app-code extensions importable.

### [Browser Automation and Hosted Site Building](stage-12.1.md) `stage-12.1` — 30 files

This stage is the web-working part of the system. It is used during the main work loop when an agent must open Chrome, inspect a page, press buttons, fill forms, download files, or build and publish a small hosted site.

The Chrome session layer is the engine. It starts Chrome, talks to it through the Chrome DevTools Protocol, which is Chrome’s remote-control channel, manages tabs, waits for pages to settle, and watches downloads. The page modeling layer turns the visible page into text and stable references, so the agent can understand and act on buttons, links, and fields. The action layer is the control panel: tools accept requests like click, type, upload, or screenshot, check and fix them, then send them to Chrome. Provider code decides where the browser comes from, such as a sandbox, a remote browser, or a delegated browser worker.

The site-building files add publishing. They move source files into a sandbox, guide a safe build-test-repair-deploy workflow, audit the finished app in the browser, and register hosted sites with ownership, visibility, port, and homepage rules.

#### [Chrome Session and CDP Infrastructure](stage-12.1.1.md) `stage-12.1.1` — 8 files

This stage is the browser engine underneath the rest of the system. It is shared support used whenever the agent needs to drive Chrome during a work turn. The session file is the hub: it opens and owns one live Chrome connection, remembers state such as open tabs and downloads, and routes requests to helper parts.

The cdp file is the main communication pipe. It uses a WebSocket, a two-way network connection, to send Chrome DevTools Protocol commands and receive events. The wire file defines the expected message shapes, so bad browser messages are caught early. Tabs turns simple requests like “open,” “switch,” or “go to this address” into those low-level Chrome commands. Runtime runs small pieces of JavaScript inside the page and reports page errors as normal Python failures.

Other helpers keep automation from getting stuck. Dialogs answers alerts and confirmation boxes. Downloads watches for files and can force tricky items like PDFs to save as real files. Settle decides when a page is ready enough to continue, ignoring background noise while waiting for meaningful loading and visual changes.

#### [Browser Page Modeling and Content Inspection](stage-12.1.2.md) `stage-12.1.2` — 4 files

This stage is shared behind-the-scenes support for working with a live browser page. Its job is to turn a page that humans see visually into safer, simpler forms that an AI system can read, search, and act on. The page.py file is the main translator. It reads the live page and builds a clean text view, using stable-looking element references so the model can talk about buttons, links, and fields. It can later map those references back to real browser targets for actions like clicking. The content.py file exposes safe inspection tools on top of the browser session, such as reading the page’s element tree, extracting visible text, or finding items. The find.py file works like a finder in a document: it parses lines from the accessibility tree, which is the browser’s structured description of page elements, searches them, and verifies suggested matches. The coordinate.py file bridges vision and action by converting screenshot coordinates into real browser pixels and choosing screenshot sizes that keep images reliable for AI models.

#### [Browser Action Execution and Tool Surface](stage-12.1.3.md) `stage-12.1.3` — 8 files

This stage is the browser “tool surface”: the layer that lets an agent ask Chrome to do useful work during the main task loop. It sits between high-level requests, like “click this button” or “upload this file,” and the lower-level browser connection that performs them.

The tools.py file is the public counter where the agent places requests such as opening pages, reading content, typing, clicking, downloading, or uploading. backend.py provides the per-turn workbench: it opens a Chrome connection when needed, lets the tools use it for navigation, tabs, page content, and downloads, then cleans it up afterward. actions.py defines the allowed action formats, like an order form that can be checked before use. errors.py names the special case where the model invents an impossible browser target or value.

Before actions reach Chrome, fixup.py repairs common small mistakes. forms.py handles form fields and file upload controls. keys.py translates typing and key shortcuts into messages Chrome understands. computer.py finally carries out clicks, typing, scrolling, waiting, and screenshots, then returns updated visual feedback and safety guidance.

#### [Browser Providers and Delegated Browser Agents](stage-12.1.4.md) `stage-12.1.4` — 5 files

This stage is shared behind-the-scenes support for doing work in a web browser. The core system may need Chrome, but it should not care where that Chrome comes from. The browser contract in core/src/ufo/browser.py is the common plug shape: it says how to get a Chrome connection for one job, so the rest of UFO can drive a local, remote, or extension-provided browser in the same way.

The provider files are different “power outlets” for that plug. The sandbox Chrome extension starts or reuses Chrome inside the same safe sandbox as the conversation, opens its control socket, and can fetch downloaded files back out. The Browserbase extension creates a short-lived remote Chrome session, connects UFO to it, moves files in and out, then cleans it up. The Browser Use extension delegates whole browsing jobs to a hosted automation service, either one task or many in parallel.

The browser subagent profile is different: it defines a specialized child worker for browser automation, including its instructions, tools, inputs, outputs, and model.

### [Document, Office, PDF, and Report Production](stage-12.2.md) `stage-12.2` — 24 files

This stage is shared document-production support. It is used when the system needs to review, edit, repair, summarize, or prepare files as finished artifacts, rather than during one single main work loop. Think of it as a document workshop with separate benches for different file types.

For review work, the document state tools keep a saved record of issues, steps, and audit history, then write findings back into PDFs, PowerPoints, or spreadsheets as highlights and comments. The Word tools open DOCX files as editable zipped folders, add or prepare comment data, rebuild the DOCX, and can make a clean copy with tracked changes accepted. The PowerPoint tools unpack, repair, clean, modify, and repack PPTX decks. The Excel recalculation tools run LibreOffice silently in the background to refresh formulas and report remaining errors.

The PDF tools fill real PDF forms, place text on flat form-like pages, and render pages as images for preview. Finally, the writing and report tools define a specialist writing assistant and turn longer reports into scheduled digest summaries.

#### [Document Review State and Artifact Annotations](stage-12.2.1.md) `stage-12.2.1` — 7 files

This stage is the record keeper and feedback writer for document review. It is shared support used while a review is in progress and after findings have been created. The small package file only makes the scripts importable by Python. The constants file keeps the standard filenames for the saved review state and the audit log, so every script looks in the same place.

The models file defines what a “review issue” looks like: for example, the problem type, where it was found, and the comment text to show a user. The manage_state command-line tool is the control desk. It records each review step, saves progress in a JSON file, and writes a log of changes so the work can be traced later.

The annotation scripts then put the review back into the original documents. The PDF tool highlights matching text and adds sticky-note comments. The PowerPoint tool inserts findings as slide comments. The Excel tool finds matching cells and adds spreadsheet comments, creating an annotated copy.

#### [DOCX Package Editing and Commenting](stage-12.2.2.md) `stage-12.2.2` — 4 files

This stage is behind-the-scenes support for working with Word documents as editable packages. A DOCX file is really a compressed folder, like a zipped box, containing many XML files. XML is structured text that stores the document’s words, comments, settings, and links.

The unpack script opens that box. It turns the DOCX into a folder of readable XML files and cleans up Word’s extra clutter so later tools can inspect or change it more reliably. The comment script works inside that unpacked folder. It creates the hidden XML records Word needs for a new comment or a reply in a comment thread, then tells the user which marker tags still need to be inserted around the commented text in the main document. The pack script closes the box again, rebuilding the folder into a normal DOCX file while keeping the visible document unchanged. Separately, the accept_changes script uses LibreOffice without a visible window to make a clean copy where all tracked edits have been accepted.

#### [PPTX Package Repair and Slide Tools](stage-12.2.3.md) `stage-12.2.3` — 5 files

This stage is a set of behind-the-scenes tools for working with PowerPoint files after they have been created or while they are being adjusted. A .pptx file is really a zipped package of many XML files and media files. These tools let the system open that package, fix it, change it, and close it again.

The unpack tool opens a .pptx into a normal folder, like emptying a suitcase so each item can be inspected. It also makes the XML easier to read and replaces risky curly quote characters with safer text forms. The pack tool does the reverse: it tidies the XML and zips the folder back into a clean .pptx file. The repair tool fixes known problems from pptxgenjs-created decks, such as packaging mistakes or text spacing that could make PowerPoint complain or display text differently. The slides tool performs practical slide operations, including removing unused package parts, adding slides, and creating thumbnail contact sheets. The __init__ file simply makes these scripts importable as a Python package.

#### [XLSX LibreOffice Recalculation](stage-12.2.4.md) `stage-12.2.4` — 2 files

This stage is a behind-the-scenes support step for working with Excel files. It is used when the system needs the values in a workbook to be fresh, not just whatever old results were stored in the file. LibreOffice is used as the spreadsheet engine, but it is run “headless,” meaning it starts without showing a normal desktop window.

The `_soffice.py` helper is the toolbelt for this. It knows how to launch LibreOffice from scripts in that quiet mode, and it also knows where LibreOffice keeps user macro files on Linux and macOS. That path knowledge matters when spreadsheet automation needs macro support.

The `recalc.py` script is the main worker. It opens the Excel workbook in LibreOffice, tells LibreOffice to recalculate all formulas, saves the workbook, and then checks for spreadsheet error values that are still present. Together, these files act like a workshop: one sets up the machinery, and the other runs the recalculation job and reports what still looks broken.

#### [PDF Form, Layout, and Rendering Tools](stage-12.2.5.md) `stage-12.2.5` — 3 files

This stage provides small command-line tools for working with PDF documents, especially forms. It is not the main application loop itself. Instead, it is behind-the-scenes support for document workflows that need to inspect, fill, mark up, or view PDFs.

The formfill tool is for true fillable PDFs, called AcroForms. An AcroForm is a PDF that already contains named boxes for typing values, like “name” or “date.” This tool can check whether those fields exist, export the field list as JSON, and fill the PDF using values from JSON.

The layout tool is for PDFs that look like forms but are not actually fillable. It scans the page for layout clues, helps preview where text should be placed, and can write text annotations onto the PDF.

The render tool converts PDF pages into PNG images. These images are easier for other tools, previews, or visual checks to use. Together, the three tools cover the main PDF form cases: detect and fill real fields, place text on flat forms, and turn pages into images for inspection.

#### [Writing and Report Production Extensions](stage-12.2.6.md) `stage-12.2.6` — 3 files

This stage adds higher-level help for writing and for turning longer reports into short digests. It is not the core work loop itself. Instead, it is shared support that other parts of the system can call when they need clear prose or a scheduled summary.

The documents subagent file defines a built-in “writing” subagent, which is like a specialist assistant inside the larger assistant. It spells out what the writer is allowed to do, what tools it can use, what kind of input it receives, what kind of output it must return, and which model and skill description guide its behavior.

The report digest code has two parts. The digest file defines the shape of a digest entry, meaning the small summary item produced from a full report, and cleans that entry so it is ready to store or show. It also creates the instructions for the digest writer. The manifest file plugs the digest feature into the system by declaring its scheduled job, admin tool, skill text, and report object.

## [External Connectors, Credentials, and Egress Requests](stage-13.md) `stage-13` — 24 files

This stage is shared support for the moments when UFO must reach outside its own walls. Sync jobs use it to read data from services, and agents use it to call tools or send messages, while secrets stay controlled.

The source connector framework gives sync jobs a common way to fetch records from different services. Direct and keyed connectors cover the simpler case where a member supplies an API key. The main connector access file defines the rules all providers must follow, and checks that a credential still belongs to the right workspace, member, provider, and account.

Composio and Pipedream integrations act as safe middlemen. They manage connected accounts, expose available actions, proxy web requests, run tools, and handle files without handing raw tokens to the sandbox. Generic connector objects and agent tools make these accounts visible and usable inside UFO, while evaluation fakes provide predictable test versions.

GitHub App credentials support coding work with verified, short-lived access. Slack and iMessage flows guide messaging setup. Finally, egress rules decide which network calls a sandboxed agent may make, and where secrets are safely added outside the sandbox.

### [Source Connector Framework](stage-13.1.md) `stage-13.1` — 2 files

The Source Connector Framework is shared behind-the-scenes support for sync jobs. A sync job needs to pull records from many outside services, such as email, chat, or code hosting. Those services all behave differently, so this stage gives them a common shape before the rest of the system sees them.

`connector.py` defines the basic contract for a connector: what it must provide, how it returns records, and how it moves through pages of results. Pagination means fetching a long list in smaller chunks, like turning pages in a book, so the system does not overload itself or the provider.

`rest.py` builds on that contract for services reached through REST APIs, which are web endpoints called over HTTP. It handles the repeated chores: setting up authentication, sending requests, retrying when temporary errors happen, following pagination rules, checking responses for safety, and cleaning records into a standard form.

Together, these files let each source connector focus on service-specific details while the sync system gets dependable, predictable data.

### [Direct and Keyed API-Credential Connectors](stage-13.2.md) `stage-13.2` — 2 files

This stage is shared support for connecting to outside services when the member provides an API key directly. An API key is a secret string that proves the user is allowed to call a service, like a password made for software. This path is used instead of an OAuth login, where the system would normally send the user through a brokered sign-in flow.

The keyed connector file defines which outside services work this way. It builds a manifest, which is a structured description the system can read. That manifest tells UFO what credential to ask for, how to store it safely, and how to attach it to outgoing web requests. The goal is to let the sandboxed code use the service without ever seeing the raw secret.

The direct source connector file is the runtime side of the same idea. When a sync job needs data from a direct source, it reads the stored API key from the credential store and turns it into a bearer credential, which is the form used in web request headers. Together, these files define the connector and then safely use its key.

### [Composio Brokered Connector Integration](stage-13.3.md) `stage-13.3` — 6 files

This stage is shared behind-the-scenes support for using outside apps through Composio. Composio is a hosted service that keeps user account tokens and offers tools for services like GitHub or Google. UFO uses it as a safe middleman, so workspaces can use connected accounts without seeing secret credentials.

The client is the main doorway to Composio. It creates connection links, checks which accounts are connected, searches available tools, uploads files, and asks Composio to run a tool. The provider adapts UFO’s normal login flow to Composio’s link-based connection process. The resolver acts like a front desk: when someone asks for a toolkit, it checks whether Composio supports it and routes the request to the shared broker.

The broker is the central machine operator. It exposes Composio tools to UFO, prepares uploads, collects file outputs, runs brokered actions, and keeps provider calls safe. The proxy turns ordinary HTTP requests into Composio proxy calls and returns ordinary-looking responses. The MCP session handles one short tool-router call over MCP, a simple protocol for calling remote tools, and normalizes the reply.

### [Pipedream Brokered Connector Integration](stage-13.4.md) `stage-13.4` — 4 files

This stage is shared behind-the-scenes support for using outside apps through Pipedream, a hosted service that manages app connections for us. It lets UFO offer actions from tools like Gmail or Linear without storing the user’s secret login tokens.

The client is the main messenger to Pipedream Connect. It creates account-linking links, checks that a connected account really belongs to the expected user or workspace, lists available app actions, and asks Pipedream to run them. The provider plugs this into UFO’s normal “connect an account” flow. Because Pipedream’s login process happens on its own site and finishes later, the provider acts like a careful handoff point.

The broker connects Pipedream’s action catalog to UFO’s connector system. It helps search for actions, explain what inputs they need, run the chosen action with the right connected account, and gather any files the action creates. The proxy supports direct provider API calls. It wraps a normal web request, sends it through Pipedream so the secret token stays hidden, and returns the provider’s real response.

### [Generic Connector Objects, Agent Tools, and Evaluation Fakes](stage-13.5.md) `stage-13.5` — 3 files

This stage is shared behind-the-scenes support for working with outside services. It is the layer that turns connected accounts, like Slack or GitHub, into things the workspace and the agent can understand and use safely.

The objects file makes each connected third-party account appear as a workspace object. It also defines the rules for what users and agents may do with it. A key idea is separation: having a real account connected is not the same as giving an agent permission to act through it.

The tools file is the agent’s safe control panel for connectors. It lets the agent discover available tools and call them in a consistent way, no matter which service they come from. It also cleans up risky or awkward results, such as uploaded files, returned files, very large encoded blobs, or repeated JSON data.

The eval manifest provides a fake but predictable workplace for tests. It simulates connectors like email, calendar, Drive, GitHub, Stripe, HubSpot, and Greenhouse using seeded data, so evaluations can check changes without touching real services.

### [Coding GitHub App Connector Credentials](stage-13.6.md) `stage-13.6` — 2 files

This stage is behind-the-scenes support for coding work that needs access to GitHub. Its job is to connect a workspace to an approved GitHub App installation, then turn that connection into usable, short-lived credentials. Short-lived means the access token expires soon, which is safer than keeping a long-term password-like secret.

The connect.py file runs the connection process. When a user provides a GitHub App installation ID, it does not simply trust that number. It checks with GitHub that the authorizing user can really see and use that installation. This is like checking someone’s badge with the front desk instead of accepting a handwritten note.

The github_app.py file is used later, when the coding workflow needs to talk to GitHub. It looks at the workspace and, if a GitHub App installation is bound, creates an organization-approved GitHub App token for that installation. If no installation is connected, it falls back to the member’s stored GitHub token. Together, these files make GitHub access both verified and properly scoped.

### [Slack and iMessage Connector Flows](stage-13.7.md) `stage-13.7` — 3 files

This stage is the bridge between the main system and two outside messaging worlds: Slack and iMessage. It is mostly setup and behind-the-scenes support, with some tools the agent can use during a conversation. Its job is to make sure people can connect the right accounts, choose the right message destinations, and receive clear next steps instead of dealing with raw provider setup details.

The iMessage tool lets a signed-in workspace member attach a phone number. It checks that the person is allowed to do this, reserves the number so it is not claimed twice, prepares the opt-in text they must send, and gives instructions with a QR code.

The Slack hooks run around larger system events. They adjust outgoing connector messages so the correct Slack bot is mentioned, and they update the “connect account” button after a user finishes linking an account.

The Slack tools are the user-facing controls for Slack setup and lookup. They help connect a workspace, generate a Slack app setup manifest, and find channels or direct messages, turning Slack’s multi-step setup into guided states.

## [Delegation, Subagents, Objectives, and Multi-Agent Workflows](stage-14.md) `stage-14` — 13 files

This stage is part of the main work loop, with some shared support behind the scenes. It lets the main agent act like a project lead: break work into tasks, send tasks to helper agents, track progress, and collect results.

The subagent core starts and manages these helpers. Profiles define the default helper and specialized helpers, while contracts check that each task and result has the expected shape, like using a form before work begins and after it ends. The subagent manager queues child turns, waits for them or lets them run separately, cancels them when needed, and returns their final answers.

Several extensions provide ready-made helpers. Browser delegation sends web tasks to one or many browser agents. Research profiles define normal and deep research agents. Site tools hand website-building to a focused builder. The brief pipeline runs outline, draft, and critique as three linked writing jobs.

Objectives tools and storage support longer plans. They record steps, evidence, attempts, and completion, and they re-check real success conditions instead of trusting a simple “done.” The self-improvement files build test sets from past failures, compare prompt changes, and only accept changes that prove reliable.

## [Artifacts, Media Rendering, and Shared Outputs](stage-15.md) `stage-15` — 10 files

This stage is shared behind-the-scenes support for anything the system produces as a file or preview. When a conversation turn creates a file, the artifact object defines how that file can be listed, inspected, downloaded, copied back into a workspace, or deleted. It also blocks direct creation, so shared files must come through the approved sharing path.

Download safety is handled by signed links, which are web links with a built-in tamper check and expiry time. The artifact URL code verifies that each link still points to a real, allowed artifact. Media rendering turns files and sites into useful previews. The document renderer sends document bytes to an outside preview service and gets back text and page images, while checking file size, response shape, and bundle safety. If a preview was missed, the preview renderer retries later. Image preview checks make sure accepted preview images are the promised type and safe to process, and the preview data model records where previews are stored. Site previewing captures sandboxed website screenshots, and share cards combine those screenshots with branding for public links. Package marker files simply make these modules importable.

## [Source Sync, Indexing, Memory, and Recall](stage-16.md) `stage-16` — 63 files

This stage is the system’s long-term knowledge pipeline. It runs behind the scenes after a user connects outside tools, and it keeps working during normal use so the agent can search past content and recall useful facts in later conversations.

First, the core sync code defines the common rules: how to fetch pages, save them durably, remember a checkpoint, and report what changed or was deleted. Source registration and triggers decide which feeds exist and which conversations should wake up when shared content changes. Gbrain Markdown readers bring in local or GitHub Markdown files. The many provider groups do the same for workplace apps, engineering tools, CRM and support systems, marketing platforms, HR tools, and finance services. Each provider is an adapter that turns one service’s API responses into the same kind of records.

After pages change, indexing and embedding code make them searchable, including vector search, which finds text with similar meaning. The memory store saves important facts, recalls relevant ones before replies, and cleans or condenses rough notes into clearer summaries, profiles, and pages.

### [Core Source Sync Contracts and Checkpointing](stage-16.1.md) `stage-16.1` — 3 files

This stage defines the basic contract for bringing outside content into the system and remembering where syncing left off. It is part of the main work loop, after sources have been configured and before search indexes are updated. The package marker, __init__.py, is just the doorway: it lets Python treat this folder as the place where source-sync code lives.

backend.py acts like an adapter plug. External connectors may each describe files or pages in their own way. This code takes their stream of records and turns it into the project’s standard SyncResult, so the rest of the system can treat every source consistently. That includes knowing what was fetched, what checkpoint to resume from, and what may have been deleted.

sync.py does the core work. It fetches documents, saves their page text safely, records checkpoints, tracks changes and deletions, and then offers those page changes to downstream indexers so search can stay up to date.

### [Gbrain Markdown Source Readers](stage-16.2.md) `stage-16.2` — 3 files

This stage is the intake area for the Gbrain extension. It is shared behind-the-scenes support used when Gbrain needs to sync pages from Markdown files, either from a folder on the user’s computer or from a GitHub repository online. Think of it as a set of loaders that collect raw documents and hand over tidy page records to the rest of the system.

The folder reader scans a local directory safely, finds Markdown files, and builds a complete snapshot of the pages available there. The GitHub reader does the same kind of job for a remote repository, but it is careful not to waste time or network traffic: it checks whether the repository has changed before downloading file contents again. Both readers rely on the page conversion utility. That shared code filters out paths that should not become pages, makes sure each file is readable UTF-8 text, and turns the Markdown into a page record with a sensible title. Together, these pieces make different Markdown sources look the same to Gbrain.

### [Source Registration, Connected Feeds, and Triggers](stage-16.3.md) `stage-16.3` — 3 files

This stage is shared behind-the-scenes support for bringing outside content into the system and letting conversations react when that content changes. A “source” here means a registered stream of content, such as a feed from a connected provider. The tools file defines the user-facing pieces: agents can register a provider stream to be synced, look up what sources exist, remove them, and set a trigger, meaning a request to wake a conversation when a shared source updates.

The connected-account file acts like an automatic setup helper. When a member links an external account, it creates the private feed records for that account’s main streams right away, so the member does not need to configure them separately. If that first setup is interrupted or incomplete, it also offers a retry path to fill in the missing feed rows later.

The triggers file is the memory for wake-up requests. It stores which conversations are watching which shared sources, and provides safe ways to add, remove, find, and list those rules inside one workspace.

### [Memory Store, Recall, and Condensation](stage-16.4.md) `stage-16.4` — 5 files

This stage is the memory system’s shared “library desk.” It is behind-the-scenes support used while the agent is working: before a reply it can recall useful context, during work it can save new facts, and later background jobs clean and summarize what was saved.

The manifest is the front sign and schedule board. It tells the larger system which memory tools agents may call, which automatic hooks should run before replies, and which cleanup or summary jobs should run in the background. The shared memory contract in core defines a common way to search and browse saved memories, so other parts of the system do not need to know the exact storage details. The events file keeps the names and size limits for recall events in one place, so every component reports recall the same way.

The store is the main filing cabinet and search desk. It saves memories, indexes source pages, filters unsafe or stale results, and avoids noisy duplicates. The condenser is the editor. It turns rough saved fragments into clearer facts, summaries, profiles, and readable wiki-style pages.

### [Workspace Content, Communication, and Document Providers](stage-16.5.md) `stage-16.5` — 12 files

This stage is shared behind-the-scenes support for bringing a user’s work content into the system. Each file is a “provider,” meaning a reader that talks to an outside service, collects allowed data, and reshapes it into the project’s common searchable format. Gmail reads mail and tracks changes so future syncs can fetch only updates. Outlook does the same kind of bridge for Microsoft mail, contacts, calendars, conversations, and folders. Google Calendar reads events and attendees. Google Docs, Sheets, Drive, and Meet read documents, spreadsheets, files, permissions, comments, revisions, transcripts, and meeting notes. The shared Google helper tells permission errors apart from quota limits, so the sync knows when to skip and when to retry. Slack reads users, channels, messages, and threads. Microsoft Teams reads teams, channels, chats, and messages through Microsoft Graph. Confluence reads spaces, pages, blog posts, comments, groups, and audits. Notion reads users, pages, data sources, comments, and nested page blocks. Together, these adapters act like translators for many workplace tools.

### [Work Tracking and Engineering Providers](stage-16.6.md) `stage-16.6` — 9 files

This stage is shared behind-the-scenes support for syncing work and engineering tools. It gives the system “readers” for outside services, so project work, code activity, incidents, and errors can be copied into the system as searchable records or readable memory pages. Each reader talks to a service’s API, meaning its official doorway for software, and breaks large results into batches the main sync system can store.

The project-management readers cover Asana, ClickUp, Jira, Linear, monday.com, and Wrike. They collect things like projects, tasks, issues, comments, users, teams, boards, lists, goals, custom fields, and workspace details. Together they let the system understand planned work across many common tools.

The engineering readers add operational context. GitHub discovers organizations and repositories, then reads issues, pull requests, commits, and users. PagerDuty reads incidents, services, schedules, and on-call assignments. Sentry reads organizations, projects, issues, events, members, and releases, without writing anything back. Together, these providers act like adapters for different plug shapes, making many services fit one sync pipeline.

### [CRM, Support, and Business Workflow Providers](stage-16.7.md) `stage-16.7` — 9 files

This stage is shared behind-the-scenes support for syncing business tools into the system. Each provider is like an adapter plug: it knows one outside service, talks to that service’s API, and reshapes the replies into the common record format used by the rest of the project.

The CRM adapters cover customer and sales data. Airtable discovers bases and tables, then reads their records. Attio reads companies, people, deals, tasks, notes, meetings, and calls. HubSpot handles a wide range of CRM, marketing, conversation, consent, list, analytics, and custom-object data. Salesforce reads standard sales and support objects such as accounts, contacts, opportunities, and cases.

The workflow and response adapters bring in scheduling and form data. Calendly reads users, event types, groups, scheduled events, and invitees. Typeform reads forms, responses, workspaces, themes, images, and webhooks.

The support adapters cover help-desk systems. Freshdesk, Intercom, and Zendesk each know how to authenticate, request pages of results, and turn tickets, conversations, users, organizations, articles, and related details into steady streams for storage and search.

### [Marketing, Ads, and Audience Providers](stage-16.8.md) `stage-16.8` — 6 files

This stage is a set of behind-the-scenes “readers” used when the system syncs marketing and advertising data. Each reader knows how to talk to one outside service, sign in with the right credentials, ask for data in small pages, and turn the replies into regular records the rest of the system can store, search, and recall.

ActiveCampaign reads customer and marketing collections from that platform. Facebook Ads uses Meta’s Graph API, an online doorway for Meta data, to collect ad accounts, campaigns, ad sets, ads, and daily performance results. Google Ads does the same for Google advertiser accounts, campaigns, ad groups, ads, and performance numbers. Instagram also uses Meta’s API, but focuses on business Pages, connected Instagram accounts, posts, stories, and insight metrics. Klaviyo reads email and ecommerce marketing data such as profiles, campaigns, events, lists, and catalog items. Mailchimp reads audiences, members, campaigns, reports, unsubscribes, and email activity. Together, these files act like adapters that make many different marketing tools look consistent to the sync engine.

### [HR and Recruiting Providers](stage-16.9.md) `stage-16.9` — 6 files

This stage is part of the system’s data-gathering layer. Its job is to connect to HR and recruiting services and turn their online API responses into steady streams of records that the rest of the sync system can store, search, or process. An API is a service’s structured doorway for requesting data.

Each file is an adapter, like a plug shaped for one vendor. The Ashby reader pulls recruiting records such as candidates, jobs, applications, interviews, offers, and users from Ashby’s paged API. BambooHR reads employee and HR records from several BambooHR endpoints and presents them as named streams. Deel reads workforce records such as contracts, forms, payslips, timesheets, and tasks. Greenhouse does a similar job for Greenhouse Harvest, covering candidates, jobs, applications, interviews, offers, and users across many endpoints. Recruitee reads hiring data such as candidates, job offers, and departments. Rippling reads companies, workers, and teams. Together, these providers hide vendor differences so the rest of the system sees consistent batches of records.

### [Finance, Billing, and Commerce Providers](stage-16.10.md) `stage-16.10` — 7 files

This stage is a set of behind-the-scenes “connectors” for finance and commerce services. It is used during source syncing, when the system reaches out to outside products, reads their data, and turns it into a common stream of records that the rest of the codebase can store, search, or index. Each file is like an adapter plug for a different service.

Brex reads spend-management data such as expenses, transactions, users, vendors, budgets, and departments. Chargebee and Recurly read subscription billing data, including customer and billing objects, while handling each service’s login and page-by-page result format. QuickBooks and Xero read accounting records such as accounts, contacts, invoices, and payments, asking their APIs for large result sets safely. Square reads commerce data such as customers, payments, locations, catalog items, orders, and inventory counts. Stripe reads a wide range of payment and subscription records, including connected-account data.

Together, these providers hide the differences between many financial APIs and present one steady record stream to the sync system.

## [Scheduled and Background Work Execution](stage-17.md) `stage-17` — 15 files

This stage is the system’s after-startup “night crew.” Once the service is running, it looks for work that should happen later or repeat on a schedule, then runs it safely inside the correct workspace so different customers or projects do not interfere with each other.

Scheduled tasks use cron rules, a simple five-part time pattern, to decide their next run. The schedules storage records recurring prompts, while the runner claims due tasks, sends them into the right conversation, and reschedules them. Pauses work similarly: they store a conversation wake-up time, then resume it once unless a human already replied. Shared firing keys keep task names and history aligned.

Monitors are like alarm clocks watching the outside world. The monitor tool creates a watch and records its first result. The monitor runner repeats the check and alerts the agent only on change, repeated failure, or deadline. Monitor storage keeps these watches safe.

Runtime helpers find which workspaces have waiting jobs, queue one execution per workspace, and run each job in extension context. Other background jobs deliver missed child-agent results, write report digests, test self-improvement proposals, and clean up old homepage bindings.

## [Live Updates, Cancellation, Replies, and Surface Delivery](stage-18.md) `stage-18` — 7 files

This stage is the system’s live “delivery desk” during and just after a conversation turn. While the assistant is working, core/src/ufo/hub.py sends small progress messages, called frames, to any open user interface and keeps a short memory so reconnecting clients can catch up. core/src/ufo/surfaces/hub_tail.py is the watcher for one turn: it combines that live feed with a database check so it knows whether the work is still running, paused, or finished.

If the system is spread across several server processes, extensions/redis_hub/ufo_ext_redis_hub/stream_hub.py uses Redis Streams, a fast shared message log, to pass those live frames between them without saving every temporary update in the main database.

Stopping work is handled carefully. core/src/ufo/surfaces/stop.py checks the requested turn, asks cancellation to happen, alerts listeners, and may point the user to a follow-up turn. core/src/ufo/turns/cancellation.py performs the shared safe stop.

For what users see, core/src/ufo/loop/replies.py extracts private reply blocks from model output and hides control tags. core/src/ufo/turns/activity.py turns raw tool activity into safe, friendly status labels.

## [Turn Commit, Teardown, Recovery, and Cleanup](stage-19.md) `stage-19` — 2 files

This stage happens when a turn is ending, or when the system must recover from something that did not finish cleanly. Its job is to leave the workspace in a knowable state. It records final results, releases temporary resources like browsers or sandboxes, cancels work that should no longer keep running, and saves enough information for later review, replay, billing, or a follow-up turn.

The files here focus on one important part of that ending record: changed files. `workspace_changes.py` asks git, the version-tracking tool, what changed in the conversation’s workspace. This catches edits made by shell commands, file renames, and deletes, even when no tool message directly described them. It answers the practical question, “What did this turn leave behind?” `file_changes.py` supplies one shared limit for how long a recorded file path may be. That keeps file-change records consistent across the system. Together, they act like the checkout list at the end of a workshop session: note what was altered, keep the notes tidy, and make them durable for whoever comes next.

## [Observability, Telemetry, and Operator Inspection](stage-20.md) `stage-20` · (cross-cutting) — 3 files

This stage is the system’s set of “dashboard and black box recorder” tools. It is not one single moment in the workflow. It runs behind the scenes during startup, normal requests, agent turns, tool calls, background jobs, and shutdown, so operators can see what happened and diagnose problems safely.

The main toolbox is core/src/ufo/o11y.py. It sets up tracing, which follows work as it moves through the system, metrics, which are counted measurements like timing and success rates, and structured logs, which are machine-readable notes about events. It also redacts sensitive text, meaning it removes or hides prompts, secrets, and tokens before they can leak into logs. It can report service health to Datadog, an external monitoring system.

core/src/ufo/loop/steps.py turns raw stored workflow history into a readable timeline of a turn: each step, its order, and how long it took. extensions/debugger/ufo_ext_debugger/report.py adds a tool for agents to flag serious workspace problems with enough context for engineers to investigate.

## [Persistence, Schema Contracts, and Pagination](stage-21.md) `stage-21` · (cross-cutting) — 9 files

This stage is the system’s shared storage foundation. It works behind the scenes during startup, normal work, background jobs, and migrations, so every part of the product agrees on how data is shaped, saved, and read. The central blueprint is schema/tables.py, which defines the database tables and rules, while db.py is the guarded doorway that opens database connections, runs migrations, wraps changes in transactions, and keeps each workspace’s data separate. schema/records.py defines common record formats for turns, agents, requests, billing, icons, and terminal results; schema/__init__.py simply makes those schema modules importable.

Conversation history has two layers. turns/transcript.py defines the agreed file format for saved conversations and compaction records, and loop/transcript.py reads and writes those transcripts safely in shared blob storage without letting older state overwrite newer state. blob.py stores raw files either locally or in cloud storage while keeping workspace files isolated. listings.py provides cursor-based paging, like bookmarks in a long list, so portals can move older or newer without missing items. durability.py helps saved workflow objects survive software changes across versions.

## [Security, Access Control, Credentials, and Billing Policy](stage-22.md) `stage-22` · (cross-cutting) — 22 files

This stage is the system’s safety and policy layer. It runs behind the scenes during login, incoming requests, sandbox activity, model/tool use, and paid jobs. Its job is to answer practical questions before work continues: who is this, what workspace are they in, what may they access, which secrets may be used, and can this work be paid for?

Credential grants and egress policy act like a guarded vault and exit gate. They store encrypted credentials, track who allowed an agent to use them, and decide which outside services a sandbox may contact. Signed token code creates tamper-proof login, public route, and sandbox access tokens. Workspace context, agent scope, and seats keep actions tied to the right workspace, agent, and member permissions.

Content visibility and governance label who may see text, protect against untrusted outside instructions, and require safe approval for prompt changes. Billing tracks usage, prepaid balances, spend caps, and links to Metronome and Stripe. The two __init__.py files simply make the access and auth folders importable by the rest of the code.

### [Credential Grants and Egress Policy](stage-22.1.md) `stage-22.1` — 4 files

This stage is behind-the-scenes safety gear for any sandbox that needs to use outside services. A sandbox is the isolated place where an agent runs. Before it can call an external website or use a private account, this stage checks what is allowed and which secrets may be used.

credentials.py is the vault. It asks users for API keys or account details, stores them encrypted, and only reveals them to trusted parts of the system when needed. grants.py is the permission desk. It records when a workspace member connects an outside account, lets an agent use it, shares or lists that access, and later revokes or disconnects it.

egress_resolver.py is the rule brain. It looks at the workspace, the agent, stored grants, and credentials to decide which destinations are allowed and what secret headers or tokens may be attached. egress_control.py is the doorway used by the Rust egress proxy. The proxy asks it for a decision, while the Python service keeps the sensitive policy, credentials, and billing records centralized and protected.

### [Signed Login, Surface, and Ingress Tokens](stage-22.2.md) `stage-22.2` — 4 files

This stage is shared behind-the-scenes security support. It gives the system a way to pass small pieces of identity or access information around without keeping everything in a server-side session. The trick is signing: the data is bundled with proof made from a secret key, so later code can tell if anyone changed it.

The reusable base is token_signing.py. It knows how to make and check these signed tokens, and how to reject tokens that are broken, altered, or made with the wrong secret. bearer.py uses that machinery for member login tokens. These say which workspace and email address the logged-in user belongs to. surface_token.py uses the same idea for public “surface” routes, where the system may need workspace or claim information before cookies or database records are available. It proves the claims were issued by the system, but does not itself grant permission.

ingress_token.py applies the pattern to sandboxes. It creates short-lived tokens for one browser to reach one specific sandbox port, so access cannot be guessed, moved elsewhere, or reused forever.

### [Workspace Context, Agent Scope, and Seats](stage-22.3.md) `stage-22.3` — 3 files

This stage is shared behind-the-scenes support for keeping work inside the right boundaries. The system may serve many workspaces, and different agents may act inside them. These files make sure each action knows which workspace it belongs to, which agent is acting, and which members are allowed to use that agent.

workspace.py is the anchor. It records the current workspace for the running task, so database access, credentials, and billing are not accidentally mixed between customers or teams. agent_scope.py adds the next layer: once a workspace is known, it tracks the current acting agent. Agent-owned code can ask “who am I?” and gets a clear answer only when it is running inside a valid agent scope. If not, it fails loudly instead of guessing.

seats.py controls permission to use an agent. A “seat” means a workspace member has access. Admins can grant, revoke, check, and list seats. Together, these parts work like badges at a secure building: workspace, agent identity, and member access must all line up.

### [Content Visibility, Audience Labels, and Governance](stage-22.4.md) `stage-22.4` — 5 files

This stage is shared behind-the-scenes support for keeping workspace content in the right hands. It does not run one main feature by itself. Instead, it supplies the rules that other parts of the system use before showing, sharing, or changing sensitive text.

The audience code labels each conversation turn with who it is meant for, such as the whole workspace, one person, one room, or an externally shared room. The subject rules give those labels a simple shape, distinguishing “everyone here can read this” from “only this member can read this.” Together they act like address labels on mail, and they also check that private or external material is not accidentally combined with the wrong audience.

The untrusted-text wrapper marks outside text as something to examine, not instructions to follow, which helps protect the model from being tricked. Scheduled task visibility applies similar access checks to task prompts and descriptions, based on where the task reports and who created it. Governance protects agent prompts by requiring proposed edits to be approved, and only applying them if the original prompt has not changed meanwhile.

### [Billing Ledger, Balances, and Payment Integrations](stage-22.5.md) `stage-22.5` — 4 files

This stage is the money meter for paid work. It runs behind the scenes while the system is doing its main job, checking whether a workspace has enough prepaid credit, recording what was used, and deciding when spending must stop. The billing package marker simply makes these billing parts importable by the rest of the code.

The accounting file is the ledger, like a detailed notebook at a shop counter. It records each unit of work, its cost, and the workspace it belongs to. From those records it can build reports, prepare usage data for export, and decide whether a spend limit has been reached.

The balance file tracks prepaid credit. It adds money or grants, subtracts usage costs, and answers the practical question: can this workspace keep running paid model work right now?

The Metronome extension connects this internal ledger to outside billing services. It sends usage to Metronome, uses Stripe for prepaid balance payments, provides billing information to chat and status pages, and runs scheduled refill and reporting jobs.

## [Public SDK, Protocol Types, and Extension Contracts](stage-23.md) `stage-23` · (cross-cutting) — 67 files

This stage is shared behind-the-scenes support. It defines the public promises that extensions, model providers, web tools, search backends, and iMessage services rely on during the whole system lifecycle. It is less like the engine of a car and more like the agreed shape of the keys, plugs, dashboard labels, and service forms.

The core contracts describe what an extension may receive, what it may declare in its manifest, how conversation side panels are shaped, and how AI model calls and streaming updates look. The SDK facade stages turn those internal contracts into stable public import paths for extension writers, covering tools, context, credentials, billing, browser and terminal access, sources, search, jobs, surfaces, web callbacks, logging, flags, and utilities. The sample extension checks that this public surface really works.

The iMessage protocol stages provide generated message and service types, plus gRPC network bindings, so iMessage-related code can exchange chats, messages, attachments, events, groups, and polls in a consistent format. Finally, ufo.kinds is simply marked as an importable Python package.

### [Core Extension and Model Contracts](stage-23.1.md) `stage-23.1` — 4 files

This stage is shared behind-the-scenes support. It defines the “rules of the road” for extensions and AI model providers, so the rest of the system can work with them safely and consistently.

The context file builds the safe workbench an extension receives when it runs. Instead of giving an add-on full access to the whole system, it hands over only approved tools: its own storage, declared credentials, model access, synced sources, conversation files, and read-only transcripts.

The conversation slots file defines the shapes of small conversation side panels, such as artifacts, tasks, sources, sites, automations, and workspace changes. It also defines how extensions can provide summaries or readable views of that data.

The manifest file is the declaration form for extensions and packs. It says what they add, such as tools, jobs, routes, agents, hooks, skills, or search backends.

The model interface file defines the common format for talking to AI providers, including messages, tool calls, streaming updates, and errors. Together, these contracts let optional pieces plug in without surprising the core system.

### [Public SDK Core Facades and Sample Coverage](stage-23.2.md) `stage-23.2` — 8 files

This stage is shared behind-the-scenes support for people writing UFO extensions. It creates the public “front door” of the SDK, so extension code can import safe, stable names without depending on the project’s private internal folders. The package marker `__init__.py` makes `ufo.sdk` importable. The facade files then gather useful pieces into clear entry points: `context.py` exposes conversation state, agent identity, credentials, models, facts, and page records; `manifest.py` exposes the types and constants used to describe an extension; `models.py` exposes approved message, model, tool-call, pricing, and helper objects; and `tools.py` exposes the public tool-building pieces. Two small utility facades keep shared wording consistent: `delivery_register.py` publishes the common delivery-register prompt block, and `untrusted.py` gives extensions the same marker the core uses for text that should not be blindly trusted. Finally, the sample extension acts like a full test drive. It uses nearly every public feature and records the results, proving the SDK surface works in the real system.

### [Public SDK Identity, Credentials, Billing, and Access Facades](stage-23.3.md) `stage-23.3` — 9 files

This stage is shared support for extension writers. It is like a row of labeled service windows at the front of a building: extensions use these public SDK modules instead of walking through private internal hallways that may change. The files mostly do not add new behavior. They re-export, meaning they make selected internal tools available again under stable public names.

The billing side is covered by accounting.py, which exposes spend and accounting data types, and balance.py, which exposes balance tools. seats.py does the same for seat and membership limits. The identity and access side is split by purpose: authproxy.py exposes pieces for supplying credentials to feed-sync connectors, credentials.py exposes approved credential classes, errors, and helpers, and grants.py exposes connection and grant audit helpers. bearer.py gives extensions a safe way to verify login bearer tokens, which are “show this to prove who you are” strings, without revealing signing secrets. subjects.py exposes standard workspace visibility subjects, such as “everyone in this workspace” or “this member.” surface_token.py exposes helpers for creating and checking surface tokens.

### [Public SDK Provider, Search, Job, and Capability Facades](stage-23.4.md) `stage-23.4` — 10 files

This stage is shared support for extension authors. It does not run the browser, search engine, jobs, or terminal itself. Instead, it provides stable “front doors” in the public SDK, so outside code can import approved names without depending on deep internal file paths that may change.

Each file is one doorway for a different kind of extension work. The browser and terminal SDK files expose the allowed connection types for talking to browsers and terminals. The connectors and sources files expose tools for bringing outside content into UFO, including source helpers and source error types. The search, index, and memory files expose the interfaces used to plug in search systems, embedding or indexing backends, and memory search providers. The skills file exposes the public objects needed to define extension skills. The jobs file exposes safe types for describing background work. The hub file exposes the hub interface and live event objects, so extensions can react to system activity. Together, these files act like a reception desk: extensions ask here for the official tools, while the engine stays free to reorganize internally.

### [Public SDK Web, Surface, Sandbox, and Utility Facades](stage-23.5.md) `stage-23.5` — 11 files

This stage is shared support for extension authors. It is the public “front counter” of the SDK: small modules that give stable, approved import paths, while hiding the deeper internal file layout. Most files do not create new behavior. They re-publish tools that already exist so extension code does not depend on parts that may move later.

The utility facades cover common needs. audience exposes conversation audience names and helpers. flags exposes the feature-flag check used to turn behavior on or off. o11y opens the approved logging and metrics doorway. scheduled_fire exposes helpers for making and reading scheduled-task keys. listings and objects provide public types and constants for portal lists and object features. sandbox gathers sandbox types and helpers. operator exposes shared operator-only web session tools. surfaces gathers the building blocks for UI surface extensions.

The web-facing pieces support browser interactions. http provides request and response types plus safe session-cookie handling. callback_page builds the small page users see after sign-in, install, or consent, guiding them back or telling them they may close the tab.

### [iMessage Protobuf Package and Google Annotation Glue](stage-23.6.md) `stage-23.6` — 8 files

This stage is behind-the-scenes support for the iMessage extension’s generated protocol code. Protocol buffers are shared message definitions, and the generated Python files need a clean folder structure so the rest of the system can import them normally.

Most files here are simple package markers. The __init__.py files in proto, google, google.api, photon, photon.imessage, and photon.imessage.v1 tell Python, “this folder is part of an importable module tree.” They do not run real logic, but they make paths like the iMessage versioned protocol modules or Google helper modules visible to other code.

The two generated Google files provide a small dependency that other protocol files may expect. http_pb2.py defines the data shapes for describing HTTP routes, such as GET or POST mappings. annotations_pb2.py connects those HTTP rules to Google’s annotation system. Together, these files act like adapter plugs: they let the iMessage protocol tree load correctly when generated code refers to standard Google API annotations.

### [iMessage v1 Generated Protobuf Message and Service Types](stage-23.7.md) `stage-23.7` — 11 files

This stage is shared behind-the-scenes support for the iMessage extension. It is not the code that starts the app or performs the user action itself. Instead, it provides the common “forms” that other code fills in and reads. These files are generated from Protocol Buffers, a compact data format used so different parts of a system can agree on the exact shape of a message.

The address types describe contact addresses and which chat service they use. Attachment types describe photos, files, stickers, and related media, while the attachment service file defines the remote calls for working with them. Chat types describe chats and chat events, and the chat service file defines actions such as creating chats, marking them read, and watching for updates. Message types cover texts, edits, reactions, stickers, and read events, while the message service file defines sending and changing messages. Event service types support catching up on streams of changes. Group types record joins, leaves, and renames. Poll types describe options, votes, and poll changes. Streaming adds a heartbeat message to keep live connections alive.

### [iMessage Provider Contract and gRPC Service Bindings](stage-23.8.md) `stage-23.8` — 5 files

This stage is shared behind-the-scenes support for the iMessage extension. It does not send messages by itself. Instead, it defines the “plugs and sockets” that the rest of the system uses to talk to an iMessage provider, whether that provider is local code or a separate service on the network.

The provider.py file is the human-written contract. It says what an iMessage provider must be able to do, and it defines small data shapes for things like incoming messages, attachments, and events. These are like standard forms everyone agrees to fill out the same way.

The other files are generated gRPC bindings. gRPC is a way for one program to call functions in another program over the network as if they were local calls. The attachment, chat, message, and event binding files each provide client stubs for making those calls and server hooks for exposing them. Together, they let the system use the same provider ideas across process and network boundaries.

## [Shared Search, Model Catalogs, and Knowledge Infrastructure](stage-24.md) `stage-24` · (cross-cutting) — 7 files

This stage is shared behind-the-scenes support. It gives the rest of the system common “reference shelves” for AI models, search, and stored knowledge, so different parts can ask the same kinds of questions in the same way.

The model files are the catalog desk. The model specification defines what every AI model record must say, such as its name, cost, required API key, and special abilities. The built-in catalog fills that record with known OpenAI and Anthropic models, including prices, token limits, and reasoning support.

The indexing files are the filing system. The core indexing contract defines how text is split into searchable pieces and what a stored piece looks like. The default index stores and searches those pieces locally, using either word matching or embeddings, which are number patterns that roughly represent meaning. The Turbopuffer extension offers another storage backend for the same job.

The search files are the outside-library window. The core search contract defines search results and page fetching, while the Perplexity extension connects that contract to Perplexity’s web API.
