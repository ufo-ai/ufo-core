# System Handbook

## 🗺️ System Overview

UFO is an AI agent server: a service that lets people talk to assistants which can use tools, browse the web, edit files, run code, connect to outside apps, remember useful facts, and keep working on scheduled or long-running tasks. A good mental picture is a workshop with many front doors, a careful receptionist, specialist workbenches, and a filing room that remembers what happened.

Before it opens, the system runs a deployment checklist. It updates the database shape, sets workspace safety rules, loads optional feature migrations, and checks that sandboxed work areas and network proxying are ready. When the server starts, it reads configuration, opens database connections, loads the selected “pack” of extensions and skills, starts background workers, attaches web routes, and begins accepting requests.

New users come in through onboarding. The system verifies their email, creates or joins the right workspace, issues a sign-in token, and sets up the first assistant and permissions. After that, requests can arrive from the web app, Slack, a terminal, operator tools, file links, or hosted sandbox sites. Each entrance translates its own kind of traffic into the same core actions.

The main loop begins when a message, schedule, or internal event creates a durable “turn,” meaning one unit of agent work. The system records it in a queue, claims it safely, prepares the workspace, loads tools and skills, builds the model prompt, and streams the model’s response. If the model asks to act, tools run inside a protected sandbox with controlled files, credentials, browser access, and internet rules. The agent can delegate to subagents, search memory, sync external sources, create documents or sites, and send live progress back to viewers.

When work finishes, results are saved as messages, artifacts, hosted outputs, memories, or future automations. If someone stops a turn, or a server crashes, cancellation and recovery code cleans up running work and prevents confusion.

Underneath everything are shared foundations: database storage, schemas, signed tokens, permissions, configuration, model catalogs, extension contracts, billing, logs, metrics, and live update hubs. These keep the workshop organized, safe, measurable, and extensible.

---

## See also

- [State-flow registers](register.md) — global state that flows across stages
- [Stage Index](index.md) — every stage and what it does
