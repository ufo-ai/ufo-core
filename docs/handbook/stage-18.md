# Cross-cutting identity, credentials, authorization, and safety gates  `stage-18` (cross-cutting infrastructure)

This stage is the system’s shared security backbone. It is not one step in startup or shutdown. Instead, it runs behind the scenes whenever people, agents, workspaces, outside services, sandboxes, or private data are involved.

First, workspace identity and execution context make sure every action knows which workspace, member, operator, or agent it belongs to, so one customer’s data cannot leak into another’s. Signed tokens act like tamper-proof tickets for special links, such as artifact downloads or sandbox access. The credential vault stores secrets in sealed form and releases them only through narrow, checked paths.

External account grants decide which connected services, such as GitHub or API providers, an agent may use. Sandbox safety then limits what untrusted code can read, which fake environment “keys” it sees, and where it may connect on the network. Finally, audience, visibility, governance, and untrusted-content checks decide who may see objects, how prompt changes are approved, and how outside text or images are treated safely. Together, these parts form the locks, badges, tickets, and guardrails for the whole system.

## Sub-stages

- [Workspace identity, member seats, and execution context](stage-18.1.md) `stage-18.1` — 6 files
- [Signed tokens for routing, artifact access, and sandbox ingress](stage-18.2.md) `stage-18.2` — 4 files
- [Credential vault, BYOK slots, and controlled secret disclosure](stage-18.3.md) `stage-18.3` — 3 files
- [External account grants and connector authorization](stage-18.4.md) `stage-18.4` — 4 files
- [Sandbox containment, injected environment, and egress policy](stage-18.5.md) `stage-18.5` — 3 files
- [Audience, visibility, governance, and untrusted content safety](stage-18.6.md) `stage-18.6` — 6 files

## 📊 State Registers Touched

- `reg-effective-config` — The merged settings that tell the whole system what is enabled, safe, and available in this run.
- `reg-workspace-roster` — The saved list of workspaces, members, admins, seats, and membership rules.
- `reg-agent-definitions` — The saved assistant agents for each workspace, including their settings, tools, model choices, and provisioning source.
- `reg-identity-context` — The current answer to who is acting, in which workspace, and on behalf of which member or agent.
- `reg-auth-tokens` — The signed tickets and login tokens used to prove access to sessions, downloads, sandbox links, and hosted onboarding.
- `reg-onboarding-ledger` — The temporary claims, invitations, verified email proofs, and hosted sign-in records used to create or join workspaces.
- `reg-credential-vault` — The encrypted store of workspace secrets and API keys that tools and connectors can request through guarded paths.
- `reg-connection-grants` — The saved outside-service account connections and the grants saying which agents may use them.
- `reg-connector-tool-catalog` — The discovered connector actions from systems like Gmail, Slack, GitHub, Composio, Pipedream, and MCP servers.
- `reg-conversation-state` — The durable conversation records, titles, audience, surface labels, sandbox links, and visible thread metadata.
- `reg-transcript-state` — The saved message history and transcript snapshots that are read, compacted, updated, audited, and shown later.
- `reg-sandbox-state` — The per-conversation sandbox handle, size, filesystem environment, command execution state, and cleanup state.
- `reg-network-egress-policy` — The shared network access rules and freshness counter that tell sandboxes and proxies where code may connect.
- `reg-browser-sessions` — The browser or Chrome DevTools session state used when tools and hosted sandbox websites need a controlled browser.
- `reg-object-store` — The durable named workspace objects owned by extensions, with their names, data, permissions, and owner routing.
- `reg-blob-artifacts` — The shared file and artifact storage for large bytes, generated files, previews, and signed downloads.
- `reg-surface-ingress` — The shared records that connect external surfaces like web, Slack, shell, OAuth, and inbound messages to conversations and replies.
- `reg-source-feeds` — The registered external content sources, sync cursors, backoff state, ownership, grants, and wake-up triggers.
- `reg-visibility-policy` — The shared audience, sharing, governance, and permission rules that decide who may see or change private data.
- `reg-prompt-governance` — The saved prompt proposals, approval status, evaluation results, and safety checks for changing agent instructions.
- `reg-untrusted-content-taint` — Trust/taint markers attached to external content as it moves through retrieval, prompts, tools, and rendering so prompt-injection safety checks can be enforced.
- `reg-sandbox-runtime-image` — The prepared sandbox runtime image, build/cache metadata, and proxy validation state used before sandboxes can be launched safely.
- `reg-connector-link-state` — Pending external-account consent/OAuth linking state between generated approval links, callbacks, completion markers, and eventual saved connections.
