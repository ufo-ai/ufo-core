# Cross-cutting SDK, extension ABI, protocol types, and public contracts  `stage-19` (cross-cutting infrastructure)

This stage is shared behind-the-scenes support for extension authors and outside integrations. It is not the startup path or the main work loop. Instead, it defines the public “contract” for how outside code may talk to the system without touching private internal parts.

The extension ABI, manifests, and context describe what an extension can declare and what safe tools it receives while running. The model, connector, source, and search contracts define common plugs for AI models, content feeds, search indexes, and memory retrieval, so different providers can fit the same sockets. The interactive SDK APIs expose approved ways to build web views, panels, browser links, objects, terminals, and sandboxed code. The identity and safety contracts provide stable access to audiences, credentials, permissions, seats, tokens, and markers for untrusted input. The SDK package shell and utility re-exports gather everyday helpers such as jobs, schedules, logging, accounting, listings, and skills.

Together, these pieces form the project’s public toolbox: stable, labeled doors into selected capabilities while the engine room stays private.

## Sub-stages

- [Extension ABI, manifests, and execution context](stage-19.1.md) `stage-19.1` — 6 files
- [Model, connector, source, and search provider contracts](stage-19.2.md) `stage-19.2` — 8 files
- [Interactive surfaces, web, browser, object, and terminal SDK APIs](stage-19.3.md) `stage-19.3` — 8 files
- [Identity, credentials, access, seats, and safety contracts](stage-19.4.md) `stage-19.4` — 8 files
- [SDK package shell and operational utility re-exports](stage-19.5.md) `stage-19.5` — 7 files

## 📊 State Registers Touched

- `reg-extension-catalog` — The shared list of installed extensions and the capabilities they registered for this deployment.
- `reg-agent-definitions` — The saved assistant agents for each workspace, including their settings, tools, model choices, and provisioning source.
- `reg-identity-context` — The current answer to who is acting, in which workspace, and on behalf of which member or agent.
- `reg-auth-tokens` — The signed tickets and login tokens used to prove access to sessions, downloads, sandbox links, and hosted onboarding.
- `reg-credential-vault` — The encrypted store of workspace secrets and API keys that tools and connectors can request through guarded paths.
- `reg-connection-grants` — The saved outside-service account connections and the grants saying which agents may use them.
- `reg-model-catalog` — The shared catalog of AI models, providers, routing rules, reasoning modes, key lookup rules, and usage shapes.
- `reg-tool-registry` — The shared catalog of tools the model is allowed to call and the input rules for each tool.
- `reg-connector-tool-catalog` — The discovered connector actions from systems like Gmail, Slack, GitHub, Composio, Pipedream, and MCP servers.
- `reg-browser-sessions` — The browser or Chrome DevTools session state used when tools and hosted sandbox websites need a controlled browser.
- `reg-object-store` — The durable named workspace objects owned by extensions, with their names, data, permissions, and owner routing.
- `reg-blob-artifacts` — The shared file and artifact storage for large bytes, generated files, previews, and signed downloads.
- `reg-portal-slots` — The safe display state for conversation panels such as sources, artifacts, tasks, sites, automations, and workspace changes.
- `reg-source-feeds` — The registered external content sources, sync cursors, backoff state, ownership, grants, and wake-up triggers.
- `reg-page-index` — The stored pages, revisions, chunks, embeddings, and search indexes used to find synced knowledge later.
- `reg-memory-store` — The durable remembered facts and notes that agents can search, browse, update, consolidate, and show with provenance.
- `reg-scheduled-work` — The saved jobs, scheduled tasks, pauses, monitors, due times, retry state, and duplicate-run guards.
- `reg-visibility-policy` — The shared audience, sharing, governance, and permission rules that decide who may see or change private data.
- `reg-extension-kv-store` — Private per-workspace JSON/key-value state owned by extensions for setup, feature bookkeeping, and small durable extension data that is not a user-visible object.
- `reg-untrusted-content-taint` — Trust/taint markers attached to external content as it moves through retrieval, prompts, tools, and rendering so prompt-injection safety checks can be enforced.
