# System Handbook

## 🗺️ System Overview

UFO is a runtime for AI agents: a service that lets people talk to assistants through web chat, Slack, iMessage, terminal tools, and other surfaces, while those assistants can safely use tools, browse the web, read connected sources, create files, and delegate work. A useful mental picture is a busy workshop. The user speaks at the front counter, the system assigns the job to the right worker, and the worker can use approved tools in a protected room.

Before the service opens, deployment code packages the app, extensions, sandbox, and configuration, then updates the database layout so old installations match the new code. At startup, the process reads settings, connects databases and storage, loads feature flags and AI model choices, and discovers extensions. Each extension brings a “menu” of tools, screens, agents, jobs, connectors, or sign-in methods. Onboarding then creates workspaces, members, and starter agents.

During normal use, requests arrive from chat, browser pages, APIs, or connected apps. Authentication checks who is acting and what workspace they belong to. A new message becomes a “turn,” meaning one unit of agent work. The turn is admitted into a durable queue, so it can survive restarts, and live streams send progress back to viewers. When the turn starts, the system builds the agent’s work room: instructions, allowed tools, skills, files, model choice, permissions, and sandbox.

The turn engine then runs the loop between the AI model and the tools. The model may answer directly, search memory, call connectors like Slack or GitHub, use a browser, edit files, create artifacts, publish a site, or ask subagents to help. Tool execution is checked and isolated, especially risky commands and web work.

Afterward, results are saved, costs are recorded, live listeners are notified, changed files are tracked, and stuck or cancelled work is cleaned up. In the background, scheduled jobs, monitors, notifications, source syncing, previews, memory cleanup, and evaluations keep running. Under everything are shared guardrails: durable databases and blob storage, public extension contracts, logs and metrics, path safety, workspace isolation, billing controls, and local test fixtures.

---

## See also

- [State-flow registers](register.md) — global state that flows across stages
- [Stage Index](index.md) — every stage and what it does
