# Surface ingress and conversation entry  `stage-5`

This stage is the system’s front gate for anything that starts or enters a conversation. It is used both in the main work loop, when members send messages or press buttons, and during setup, when members connect outside services. Its job is to take many different kinds of incoming requests and turn them into the same internal shape: a known workspace member, a conversation, a message, or an interaction.

The live user surfaces are the everyday doors. Slack verifies Slack signatures, the web portal checks signed-in browser sessions, the terminal accepts command-line posts, and the debugger lets operators inspect activity. All of them pass their cleaned-up requests to the shared surface entry point, which safely creates conversations, submits messages, and returns replies.

The OAuth and connection callbacks are the setup doors. After a member approves access in GitHub, Composio, Pipedream, or another provider, the callback files check the return visit and save the connection. Together, these parts make sure only trusted, well-identified traffic reaches the core conversation system.

## Sub-stages

- [Live user surfaces](stage-5.1.md) `stage-5.1` — 5 files
- [OAuth and connection callbacks](stage-5.2.md) `stage-5.2` — 4 files

## 📊 State Registers Touched

- `reg-workspace-boundary` — The current workspace or tenant boundary used to keep each customer’s data and actions separate.
- `reg-credential-store` — The encrypted store of API keys, service secrets, and owner-provided credentials.
- `reg-auth-session` — The signed login and identity state that proves which member or operator is using the system.
- `reg-workspace-objects` — The shared records for workspaces, agents, members, conversations, artifacts, memories, sources, and other workspace objects.
- `reg-membership-and-seats` — The shared membership, admin role, paid seat, and seat-limit state for a workspace.
- `reg-agent-identity` — The saved identity and settings of each agent, including its main workspace role and whether it may use the internet.
- `reg-audience-policy` — The saved visibility rules that decide which people may see or use a conversation or agent.
- `reg-conversation-state` — The durable conversation record that ties a surface, agent, audience, sandbox handle, and message history together.
- `reg-turn-queue` — The durable queue of conversation turns waiting to be claimed, run, completed, cancelled, or retried.
- `reg-inbound-message-state` — The saved incoming messages and surface-provided context waiting to be admitted into a conversation turn.
- `reg-connector-connections` — The saved external accounts, OAuth connections, and agent grants that let tools use outside services safely.
- `reg-live-stream-hub` — The live stream of turn updates that lets clients watch progress and reconnect without losing recent events.
- `reg-surface-installations` — The saved Slack, web, terminal, and other surface bindings used to receive messages and send replies back.
- `reg-onboarding-claims` — The hosted signup state for email claims, invitations, company-domain workspace mapping, and temporary access tokens.
- `reg-surface-delivery-state` — Durable outbound reply/writeback state used to de-duplicate, track, retry, and complete delivery of responses to Slack, web, terminal, or other surfaces.
- `reg-oauth-handshake-state` — Short-lived OAuth/connection callback state such as nonce, redirect intent, and verifier data used to bind an external authorization return to the initiating workspace, member, agent, and grant before saving the connection.
- `reg-acting-principal-scope` — The current acting principal context—member, agent, on-behalf-of member, and object/agent scope—used to authorize actions, attribute turns, choose grants, and keep tool work tied to the right actor.
- `reg-secret-handoff-state` — Short-lived private handoff state used to bind sensitive credential or connection setup payloads to the right actor before secrets are accepted and stored.
