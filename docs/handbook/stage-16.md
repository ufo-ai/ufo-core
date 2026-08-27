# Source Sync, Indexing, Memory, and Recall  `stage-16`

This stage is the system’s long-term knowledge pipeline. It runs behind the scenes after a user connects outside tools, and it keeps working during normal use so the agent can search past content and recall useful facts in later conversations.

First, the core sync code defines the common rules: how to fetch pages, save them durably, remember a checkpoint, and report what changed or was deleted. Source registration and triggers decide which feeds exist and which conversations should wake up when shared content changes. Gbrain Markdown readers bring in local or GitHub Markdown files. The many provider groups do the same for workplace apps, engineering tools, CRM and support systems, marketing platforms, HR tools, and finance services. Each provider is an adapter that turns one service’s API responses into the same kind of records.

After pages change, indexing and embedding code make them searchable, including vector search, which finds text with similar meaning. The memory store saves important facts, recalls relevant ones before replies, and cleans or condenses rough notes into clearer summaries, profiles, and pages.

## Sub-stages

- [Core Source Sync Contracts and Checkpointing](stage-16.1.md) `stage-16.1` — 3 files
- [Gbrain Markdown Source Readers](stage-16.2.md) `stage-16.2` — 3 files
- [Source Registration, Connected Feeds, and Triggers](stage-16.3.md) `stage-16.3` — 3 files
- [Memory Store, Recall, and Condensation](stage-16.4.md) `stage-16.4` — 5 files
- [Workspace Content, Communication, and Document Providers](stage-16.5.md) `stage-16.5` — 12 files
- [Work Tracking and Engineering Providers](stage-16.6.md) `stage-16.6` — 9 files
- [CRM, Support, and Business Workflow Providers](stage-16.7.md) `stage-16.7` — 9 files
- [Marketing, Ads, and Audience Providers](stage-16.8.md) `stage-16.8` — 6 files
- [HR and Recruiting Providers](stage-16.9.md) `stage-16.9` — 6 files
- [Finance, Billing, and Commerce Providers](stage-16.10.md) `stage-16.10` — 7 files

## 📊 State Registers Touched

- `reg-database-session-workspace-scope` — The shared database access layer that keeps reads and writes inside the right workspace and transaction.
- `reg-background-job-queue` — The shared pool of delayed or recurring work that workers claim, run, retry, and clean up.
- `reg-schedule-monitor-store` — The saved recurring prompts, pauses, and outside-world watches that can wake conversations later.
- `reg-credential-vault` — The encrypted store of API keys, connected accounts, grants, and approvals that lets tools use outside services without exposing secrets.
- `reg-object-registry` — The shared object front desk that gives stable names, views, permissions, and change history for workspace records.
- `reg-source-sync-state` — The saved state of connected content sources, including pages, checkpoints, errors, ownership, and read grants.
- `reg-search-indexes` — The shared searchable indexes and embeddings that let turns, research tools, and memory lookup find relevant text.
- `reg-memory-store` — The long-term saved facts, profiles, notes, and summaries that can be recalled in later conversations.
- `reg-source-trigger-subscriptions` — Rules that map source changes or sync events to the conversations, agents, or turns that should be woken or admitted.
- `reg-content-provenance-trust-labels` — Visibility, provenance, and trust labels attached to messages, source content, and external text so prompt construction and policy checks can separate trusted instructions from untrusted content.
