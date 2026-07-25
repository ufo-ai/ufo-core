# System Handbook

## 🗺️ System Overview

UFO is an AI workspace server: a place where people can chat with an agent, connect work tools like Slack or Gmail, let the agent search and edit information, and run code safely in a sandbox. A useful mental picture is a staffed workshop. Users bring requests to the front desk, the system gives the agent the right tools and permissions, and the agent works in a locked room where its actions can be watched and recorded.

Before the workshop opens, deployment checks that the database has the right shape and that sandboxes and storage really work. When the process starts, it reads settings, connects to databases, starts web and proxy servers, loads feature packs and extensions, and prepares background workers. New hosted users go through onboarding: they prove their email, get or create a workspace, become a member, and receive a token, which is a secret pass for later access.

During normal use, messages can arrive from the web, Slack, the terminal, or other surfaces. Each surface checks identity, translates the event into the common conversation format, and sends it to the turn admission system. A “turn” is one unit of agent work for a message. The system queues turns durably so they survive crashes and run in order. A worker claims the next turn, builds the runtime, mounts the workspace in a sandbox, loads tools and credentials, gathers the conversation history, recalls useful memories or synced source pages, and chooses an AI model.

Then the agent loop begins. The model thinks, streams partial replies, asks to use tools, searches, runs commands, edits files, calls connectors, or delegates to subagents. Costs are tracked as it works. When the turn finishes, the system saves the result, sends replies and files back to the right surface, cleans up the sandbox, or records cancellation or failure clearly.

Behind the scenes, shared infrastructure keeps everything safe and reliable: database and blob storage, public extension contracts, secrets and permission grants, spend limits and billing, logs and traces, scheduled jobs, source syncing, and configuration adapters that let replaceable parts fit together.

---

## See also

- [State-flow registers](register.md) — global state that flows across stages
- [Stage Index](index.md) — every stage and what it does
