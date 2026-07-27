# System Handbook

## 🗺️ System Overview

This system is an agent server: a service that lets people talk to an AI assistant through Slack, a browser, a terminal, or operator tools. A useful mental picture is a busy workshop. Messages arrive at front desks, get checked in, move along a saved conveyor belt, and are handled by an AI worker that can use safe tools, outside services, and specialist helpers.

Before the workshop opens, the deployment is checked. The system proves its sandbox “workroom” image works, then upgrades the database so it has the tables it needs for workspaces, users, conversations, turns, credentials, billing, sources, and extensions. In the hosted version, new users can sign in, verify their work email, create or join a workspace, and connect Slack.

At startup, the server reads its settings, connects storage, loads a chosen pack of features, and assembles extensions. These extensions add routes, tools, skills, connectors, jobs, and user interfaces. Once running, incoming messages are normalized into one common shape. Identity and permission checks make sure the user, workspace, connected accounts, seats, and spending limits are allowed. If the message passes, it becomes a durable turn: a saved unit of work that survives restarts.

Workers then claim turns from the queue in order. For each turn, the system gathers conversation context, builds the AI’s instruction sheet, prepares a sandboxed workspace, and lists the tools the model may use. The model can stream an answer, call tools, edit files, search, browse, use connected services like GitHub or Gmail, or delegate subtasks to child agents. Behind the scenes, source-sync and memory jobs keep external knowledge searchable and up to date.

As the answer is produced, live updates are streamed back to watching clients. Final transcripts, costs, and artifacts are saved, and private files are shared only through short-lived download links. Throughout everything, shared infrastructure acts like the building’s plumbing: database safety keeps workspaces separated, encrypted credentials protect secrets, network rules control outbound access, accounting tracks usage, logs and metrics help operators debug, and the public SDK gives extensions stable, safe ways to plug in.

---

## See also

- [State-flow registers](register.md) — global state that flows across stages
- [Stage Index](index.md) — every stage and what it does
