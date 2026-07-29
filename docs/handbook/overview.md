# System Handbook

## 🗺️ System Overview

UFO is an extensible AI agent platform: picture a staffed workshop that people can reach from Slack, a web app, a terminal, or connected services. A user asks for help, and the system turns that request into a tracked piece of work. The agent can then think, answer, use tools, browse, edit files, call approved outside apps, and hand parts of the job to specialist helpers, all inside guarded boundaries.

Before the shop opens, UFO prepares its long-term filing cabinet: the database. It applies ordered schema changes, sets up workspace separation rules, and checks that required tables exist. Then it reads configuration, chooses a deployment “pack,” loads extensions, registers available models and backends, and starts the web servers, workers, logs, metrics, and process supervision. In hosted setups, a gateway also handles signup: verifying email, checking invites, creating or finding a workspace, issuing a short-lived token, and creating the first admin and assistant.

Once running, every incoming action passes through a common front gate. Slack messages, browser requests, terminal posts, scheduled tasks, and OAuth callbacks are checked, identified, and mapped to a workspace, member, conversation, and message. Permission and credential systems decide what the person or agent may see or use. Accepted work becomes a durable “turn,” meaning it is saved before a worker handles it, so a restart does not lose it.

For each turn, UFO gathers the conversation history, memories, model settings, tools, credentials, and sandbox workspace. The agent then talks to an AI model in a loop. If the model asks to use a tool, UFO runs that tool safely, records the result, and continues until there is a final answer, a pause, or a controlled failure. Live updates stream back to the user, and shared files are protected by signed download links.

Behind the scenes, scheduled jobs sync external sources, build search indexes, maintain memories, process billing, and even test prompt improvements. On shutdown or failure, supervision cancels or recovers work cleanly. Across everything, shared SDKs, schemas, security rules, accounting, storage, provider adapters, and observability keep the workshop consistent, safe, measurable, and extensible.

---

## See also

- [State-flow registers](register.md) — global state that flows across stages
- [Stage Index](index.md) — every stage and what it does
