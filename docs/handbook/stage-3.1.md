# Core and Extension Schema Changes  `stage-3.1`

This stage is the project’s database renovation plan. It runs during install or upgrade, before normal work, so the current code can read old saved data safely. The first migration, 0001_heartbeat.py, lays the foundation: workspaces, people, agents, conversations, turns, and usage charges.

The later migrations expand that foundation in many directions. Conversation and turn migrations record richer chat history, execution state, subagent work, titles, surfaces, and workspace changes. Scheduling and runtime migrations track delayed jobs and live workers. Surface, inbound-message, source, page, and artifact migrations store where messages and content come from. Agent, app, visibility, identity, access, audit, billing, and cleanup migrations keep permissions, built-in apps, costs, and retired data consistent.

Extension migrations add feature-owned tables for memory, search indexes, publishing, skills, source triggers, web chat, workflows, objectives, reports, research, eval fixtures, samples, scheduled pauses, and code review. Together, these files act like careful numbered construction steps, adding shelves, relabeling boxes, and removing old rooms without losing the records the system depends on.

## Sub-stages

- [Core Conversation Record and Surface Schema Migrations](stage-3.1.1.md) `stage-3.1.1` — 7 files
- [Core Turn Execution, Admission, and Subagent Migrations](stage-3.1.2.md) `stage-3.1.2` — 20 files
- [Core Scheduling and Runtime Fleet Migrations](stage-3.1.3.md) `stage-3.1.3` — 11 files
- [Core Surface, Inbound Message, and Shared Artifact Migrations](stage-3.1.4.md) `stage-3.1.4` — 13 files
- [Core Source, Page, Connection, and Memory-Surface Migrations](stage-3.1.5.md) `stage-3.1.5` — 14 files
- [Core Agent Configuration, Model, and Spawn Migrations](stage-3.1.6.md) `stage-3.1.6` — 15 files
- [Core Agent Binding, App Lifecycle, Visibility, and Tool-Allowlist Migrations](stage-3.1.7.md) `stage-3.1.7` — 12 files
- [Core Identity, Access, Workspace Metadata, and Audit Migrations](stage-3.1.8.md) `stage-3.1.8` — 20 files
- [Core Ledger, Balance, Spend, and Usage Billing Migrations](stage-3.1.9.md) `stage-3.1.9` — 17 files
- [Core Legacy Branch and Retired Surface Data Cleanup Migrations](stage-3.1.10.md) `stage-3.1.10` — 9 files
- [Memory and Default Index Extension Migrations](stage-3.1.11.md) `stage-3.1.11` — 18 files
- [Publishing, Skill, Source Trigger, and Web Extension Migrations](stage-3.1.12.md) `stage-3.1.12` — 16 files
- [Workflow, Objective, Report, Research, Eval, and Sample Extension Migrations](stage-3.1.13.md) `stage-3.1.13` — 9 files
- [Coding Extension Review Migration History](stage-3.1.14.md) `stage-3.1.14` — 4 files

## Files in this stage

### Core and Extension Schema Changes
### `core/src/ufo/schema/migrations/versions/0001_heartbeat.py`

`data_model` · `database setup and migration`

This file is a database migration, which means it is a scripted change to the database structure. It is like the blueprint for building the first version of the project’s filing cabinet. Without it, the application would not have the tables it needs to remember who belongs to a workspace, what agents exist, what conversations happened, or how much model usage cost.

The migration creates a small connected world of records. A workspace is the top-level container. Agents and members belong to a workspace. Conversations also belong to a workspace and are tied to a member. A surface identity links an outside identity, currently only from the command-line interface surface called `cli`, back to a member. A turn records one step in a conversation: what came in, which agent handled it, its order in the conversation, and whether it is queued, running, finished, failed, or cancelled. A ledger entry records billable usage for a turn, currently token usage and its price in micro-US dollars.

The file also adds safety rules directly in the database. For example, turn sequence numbers must be at least 1, usage amounts must be positive, and certain text fields may only contain known values. These rules protect the data even if application code has a bug.

#### Function details

##### `upgrade`  (lines 12–114)

```
def upgrade() -> None
```

**Purpose**: Builds the first version of the database schema. Someone runs this when setting up a new database or moving an empty database to revision `0001`.

**Data flow**: It reads no application data. It sends table and index definitions to Alembic, the migration tool, which then asks the database to create those structures. After it finishes, the database has tables for workspaces, agents, members, conversations, surface identities, turns, and ledger records, plus an index that makes looking up ledger entries by turn faster.

**Call relations**: This function is called by Alembic during a forward migration. Inside it, the function hands each table definition to Alembic’s `create_table`, using SQLAlchemy objects to describe columns, primary keys, foreign keys, uniqueness rules, and check rules. At the end, it asks Alembic to create the `ledger_turn` index so ledger records can be found efficiently for a specific turn.

*Call graph*: 13 external calls (create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint (+3 more)).


##### `downgrade`  (lines 117–125)

```
def downgrade() -> None
```

**Purpose**: Undoes this migration by removing the schema created by `upgrade`. This is used when rolling the database back before revision `0001`, usually during development or recovery.

**Data flow**: It takes the existing database structures created by `upgrade` and removes them in a safe order. First it drops the ledger index, then drops tables from the most dependent ones back to the root `workspace` table. After it finishes, none of the tables from this initial schema remain.

**Call relations**: This function is called by Alembic during a rollback. It uses Alembic’s `drop_index` and `drop_table` operations. The order matters: tables that point to other tables are removed first, much like taking apart shelves before removing the frame they are attached to.

*Call graph*: 2 external calls (drop_index, drop_table).
