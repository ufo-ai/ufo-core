# Core Agents, Members, Grants, and Connections Migrations  `stage-2.6`

This stage is behind-the-scenes upgrade work for the database. A migration is a careful change to stored data, like adding new drawers and labels to a filing cabinet before the application can use new features safely.

Its parts update the core records that describe agents, people, permissions, and outside links. The credential and grant migrations create secure places for secrets, then reshape permissions from one-off grants into reusable connections and agent-specific access rules. They also add source-reading permissions and a workspace counter that tells the network proxy when access rules need rebuilding.

The agent configuration migrations add settings such as internet access, reasoning level, sandbox size, provisioning details, and spawn input/output fields. The member and audit migrations record who actions belong to, mark key workspace controllers, speed up email lookup, store time zones, and log private transcript access by admins. Finally, the model remapping migrations rewrite old saved model names and settings so existing agents keep working when providers rename or retire models.

## Sub-stages

- [Credential, Grant, Connection, and Source Permission Migrations](stage-2.6.1.md) `stage-2.6.1` — 7 files
- [Agent Configuration, Binding, Provisioning, and Spawn Migrations](stage-2.6.2.md) `stage-2.6.2` — 7 files
- [Member, Workspace Control, and Transcript Audit Migrations](stage-2.6.3.md) `stage-2.6.3` — 6 files
- [Agent Model Remapping and Fable Compatibility Migrations](stage-2.6.4.md) `stage-2.6.4` — 4 files
