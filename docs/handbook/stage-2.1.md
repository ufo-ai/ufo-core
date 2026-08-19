# Core schema migrations  `stage-2.1`

This stage is the project’s main database upgrade track. It runs behind the scenes when the system is installed or updated, making sure stored data has the right “shelves” for newer code. The first migration creates the basic records for workspaces, members, agents, conversations, turns, and usage costs. Another early migration adds proposals, so agents can save suggested workspace changes.

The later groups build on that foundation. Turn and subagent migrations let conversations branch into delegated work. Scheduling and fleet migrations store future jobs, pauses, and worker process state. Source and page migrations track imported content, sync errors, ownership, and read access. Surface and inbound-message migrations support Slack, web, iMessage, titles, visibility, and message queues. Credential and connection migrations separate accounts, permissions, secrets, and network safety rules. Ledger migrations make billing records detailed enough for usage, exports, and bring-your-own-key cases. Workspace migrations cover members, limits, balances, and top-ups. Memory and artifact migrations add or retire storage for extensions, shared files, and remembered facts. Agent configuration migrations keep saved agents, models, tools, and sandbox settings usable as the product changes.

## Sub-stages

- [Turn execution and subagent metadata migrations](stage-2.1.1.md) `stage-2.1.1` — 14 files
- [Scheduling, admission, and runtime fleet migrations](stage-2.1.2.md) `stage-2.1.2` — 13 files
- [Sources and page indexing migrations](stage-2.1.3.md) `stage-2.1.3` — 9 files
- [Surface, inbound message, and conversation presentation migrations](stage-2.1.4.md) `stage-2.1.4` — 15 files
- [Credentials, connections, grants, and control-plane safety migrations](stage-2.1.5.md) `stage-2.1.5` — 9 files
- [Ledger usage, export, and billing detail migrations](stage-2.1.6.md) `stage-2.1.6` — 12 files
- [Workspace membership, limits, and balance migrations](stage-2.1.7.md) `stage-2.1.7` — 10 files
- [Memory, artifacts, extension data, and cleanup migrations](stage-2.1.8.md) `stage-2.1.8` — 10 files
- [Agent configuration, model, and provisioning migrations](stage-2.1.9.md) `stage-2.1.9` — 14 files

## Files in this stage

### Initial core schema
Establishes the first core UFO tables and then extends the schema with proposal storage.

### `core/src/ufo/schema/migrations/versions/0001_heartbeat.py`

`data_model` · `database migration / schema setup`

This file teaches the database what shape the application’s core records should have. It is written for Alembic, a database migration tool that applies step-by-step changes to a database so every environment can be brought to the same structure safely.

The migration builds the project’s first schema from the ground up. It starts with a workspace, which acts like the top-level container. Inside a workspace, there can be agents, members, conversations, and turns. A turn is one exchange in a conversation: it records which agent responded, its order in the conversation, its status, the inbound text, and any final result. The ledger table records usage charges, currently token usage, tied back to a turn.

The file also adds guardrails. For example, agent names and member emails must be unique inside a workspace. A conversation can only use the supported surface, currently “cli”. Turn status must be one of a fixed set, and a finished turn must have terminal data while queued or running turns must not. These rules matter because they keep bad or inconsistent data from entering the system.

The downgrade path removes the same objects in reverse order, like carefully dismantling scaffolding so foreign-key links are not left dangling.

#### Function details

##### `upgrade`  (lines 12–114)

```
def upgrade() -> None
```

**Purpose**: Creates the initial database structure for the application. Someone uses this when setting up a new database or bringing an empty database up to the first recorded schema version.

**Data flow**: The function receives no direct input from application code. When Alembic runs it, it uses Alembic’s database operation object and SQLAlchemy’s table-building objects to describe new tables, columns, keys, uniqueness rules, and validation checks. The result is a database with seven new tables plus an index for quickly finding ledger entries by turn.

**Call relations**: This function is called by Alembic during a forward migration. It hands table and index creation requests to Alembic, which turns those requests into database commands. The order matters: it creates parent tables such as workspace before child tables such as agent, conversation, turn, and ledger, so the database can enforce their relationships.

*Call graph*: 13 external calls (create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint (+3 more)).


##### `downgrade`  (lines 117–125)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the tables and index created by upgrade. Someone uses this when rolling the database schema back before this first version, usually during development or a controlled rollback.

**Data flow**: The function receives no direct input from application code. Alembic runs it and it asks the database to drop the ledger index first, then remove the tables in reverse dependency order. After it finishes, the database no longer contains the schema introduced by this migration.

**Call relations**: This function is called by Alembic during a backward migration. It hands drop requests to Alembic, which sends the appropriate commands to the database. It works in the opposite direction from upgrade, removing dependent tables such as ledger and turn before removing the base workspace table.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0003_proposal.py`

`data_model` · `database migration during upgrade or rollback`

This is a database migration file. A migration is like a set of instructions for remodeling a database safely over time, so every running system can move from the old layout to the new one in the same way.

Here, the remodel adds a new table called `proposal`. Each proposal belongs to a workspace and an agent, records what kind of extension it is about, stores a before-and-after digest, and keeps the proposal body as JSON, which means structured data saved in a flexible format. It also tracks its status: only `pending`, `approved`, or `rejected` are allowed. That rule matters because it stops the database from accepting unclear states like `done` or `maybe`.

The table links to other existing tables. `workspace_id` points to a workspace, `agent_id` points to an agent, and `approved_by` optionally points to a member. These foreign keys are like cross-references that help keep records honest: a proposal cannot claim to belong to a non-existent workspace or agent.

Without this file, the application could not reliably store proposals in the database. Code that expects the `proposal` table would fail, and important review or approval state would have nowhere permanent to live.

#### Function details

##### `upgrade`  (lines 12–31)

```
def upgrade() -> None
```

**Purpose**: Creates the `proposal` table when the database is moved forward to this version. This gives the application a permanent place to store proposed changes, their status, and their links to workspaces, agents, and approvers.

**Data flow**: The function takes no direct input from application code. When the migration tool runs it, it builds a table definition made of columns, allowed status values, links to other tables, and a primary key. The result is a changed database schema: after it finishes, the database contains a new `proposal` table ready to hold proposal records.

**Call relations**: This function is called by Alembic, the database migration tool, when applying revision `0003`. It hands the table blueprint to Alembic's `create_table` operation, using SQLAlchemy objects to describe each column and rule in a database-independent way.

*Call graph*: 9 external calls (create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 34–35)

```
def downgrade() -> None
```

**Purpose**: Removes the `proposal` table when the database is rolled back from this version. This is the undo step for the migration.

**Data flow**: The function takes no direct input. When run by the migration tool, it asks the database to drop the `proposal` table. Afterward, the schema no longer has that table, and any proposal data stored there would be gone.

**Call relations**: This function is called by Alembic during a rollback. It delegates the actual removal to Alembic's `drop_table` operation, mirroring the `upgrade` function in reverse.

*Call graph*: 1 external calls (drop_table).
