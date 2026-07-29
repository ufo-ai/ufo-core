# Sandboxed tool execution  `stage-10`

Sandboxed tool execution is the part of the main work loop where the agent stops just talking and safely does things. A sandbox is a controlled workspace, like a fenced workbench, where commands can run and files can change without exposing the whole computer.

The built-in tools are the basic hands: they run shell commands, read and edit files, share artifacts, ask the user for missing input, load skills, and coordinate helpers. The tool context acts like a permission slip, giving each tool only the access it needs. Sandbox session code creates or resumes the conversation’s workspace and records the work.

The carrier and proxy parts provide the workshop and its guarded internet door. Work can run in Docker locally or in a remote E2B sandbox. Network traffic is checked against policy, credentials are added only when allowed, and browser tools can click, type, download, upload, and preview sites.

Connector, research, coding, REPL, todo, and external-app tools let the agent use outside services and run repeated code safely. Document, spreadsheet, presentation, and PDF scripts handle office files inside the same protected setup.

## Sub-stages

- [Built-in shell, file, artifact, and coordination tools](stage-10.1.md) `stage-10.1` — 5 files
- [Sandbox carriers, proxying, browser, and website automation](stage-10.2.md) `stage-10.2` — 31 files
- [Connector, MCP, research, coding, REPL, todo, and external-app tools](stage-10.3.md) `stage-10.3` — 16 files
- [Document, spreadsheet, presentation, and PDF skill scripts](stage-10.4.md) `stage-10.4` — 22 files

## 📊 State Registers Touched

- `reg-effective-configuration` — The chosen runtime settings that tell the service how this deployment should behave.
- `reg-pack-selection` — The selected product pack that decides which bundle of extensions, skills, and infrastructure is enabled.
- `reg-extension-inventory` — The installed extension set and their declared capabilities, such as tools, routes, jobs, skills, and storage.
- `reg-credential-store` — The encrypted store of API keys, service secrets, and owner-provided credentials.
- `reg-workspace-objects` — The shared records for workspaces, agents, members, conversations, artifacts, memories, sources, and other workspace objects.
- `reg-agent-identity` — The saved identity and settings of each agent, including its main workspace role and whether it may use the internet.
- `reg-conversation-state` — The durable conversation record that ties a surface, agent, audience, sandbox handle, and message history together.
- `reg-transcript-state` — The stored conversation transcript, including exact recent messages and compact summaries of older content.
- `reg-tool-catalog` — The shared catalog of tools the model is allowed to see and call during a turn.
- `reg-skill-store` — The shared set of built-in, extension-provided, and user-created skills available to agents.
- `reg-sandbox-session` — The saved or live sandbox workspace where an agent can run commands and keep files across tool calls.
- `reg-egress-policy` — The network access rules that decide which outside sites sandboxed work may contact and which secrets may be injected.
- `reg-connector-connections` — The saved external accounts, OAuth connections, and agent grants that let tools use outside services safely.
- `reg-search-index` — The shared searchable index and chunk store built from synced or fetched content.
- `reg-memory-store` — The long-term memory store of remembered facts and recall results used to inform later responses.
- `reg-live-stream-hub` — The live stream of turn updates that lets clients watch progress and reconnect without losing recent events.
- `reg-artifact-storage` — The shared file and blob storage for generated artifacts, plus the signed download state used to protect them.
- `reg-accounting-ledger` — The shared usage ledger that records tokens, egress, sandbox usage, billing exports, and spend-limit checks.
- `reg-observability-context` — The shared logging, metrics, tracing, and trace-link state used to understand work across requests and subagents.
- `reg-outbound-http-client-pool` — Shared outbound HTTP client/session pool state used for connection reuse, retries, and provider/API calls across model adapters, connectors, search, tools, and billing jobs.
- `reg-acting-principal-scope` — The current acting principal context—member, agent, on-behalf-of member, and object/agent scope—used to authorize actions, attribute turns, choose grants, and keep tool work tied to the right actor.
- `reg-interactive-tool-session-state` — Live state for interactive sandbox tools such as browser contexts, page/element references, REPL kernels, and long-running app sessions reused across tool calls or delegated browser work.
