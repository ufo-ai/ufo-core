# Surface ingress and request routing  `stage-6`

This stage is the system’s front desk while the service is running. It receives traffic from people, browsers, chat apps, terminals, hosted sites, and outside services, then sends each request to the trusted inner parts of the system. It does not do the agent’s thinking itself. Instead, it checks who is calling, reshapes outside messages into the system’s internal format, and makes sure replies or files go back through the right door.

The messaging and chat surfaces handle conversation channels. Slack, iMessage, and terminal clients each have adapters that verify incoming events, turn them into agent messages, stream progress, and deliver final replies.

The web portal, hosted-site, and sandbox routes handle browser traffic. They serve the main app, protect hosted pages, and safely connect users to isolated sandbox workspaces without exposing private ports or guessed addresses.

The live streams, downloads, and callback routes keep users connected during work and setup. They publish live turn updates, serve signed artifact links, and finish account-connection flows such as OAuth returns.

## Sub-stages

- [Messaging and chat surfaces](stage-6.1.md) `stage-6.1` — 8 files
- [Web portal, hosted-site, and sandbox ingress routes](stage-6.2.md) `stage-6.2` — 8 files
- [Live streams, downloads, and callback routes](stage-6.3.md) `stage-6.3` — 8 files

## 📊 State Registers Touched

- `reg-workspace-records` — The saved workspace records that identify each customer space and hold its limits, setup state, balance settings, and routing boundaries.
- `reg-member-identity` — The shared record of who each user is, how they logged in, what workspace they belong to, and what timezone or invitation state is known.
- `reg-auth-tokens` — The signed login, surface, artifact, and SDK tokens used to prove that a caller or link is allowed to act.
- `reg-conversation-turn-state` — The conversation and turn queue state that tracks each unit of agent work from admission through running, completion, cancellation, or recovery.
- `reg-inbound-message-log` — The saved incoming-message log that keeps outside chat, terminal, web, and scheduled events ordered, unique, and ready to become turns.
- `reg-surface-routing` — The shared mapping from external surfaces such as Slack, iMessage, web, terminal, and hosted sites to the right workspace, agent, and conversation.
- `reg-live-update-hub` — The live activity stream that carries turn progress, tool status, cancellations, mid-turn replies, and final updates to connected viewers.
- `reg-runtime-fleet-liveness` — The heartbeat and listener-claim records that show which long-running service instances are alive and what work they currently own.
- `reg-credentials-and-grants` — The encrypted secrets, account connections, and grants that say which member or agent may use an outside service.
- `reg-sandbox-handles` — The remembered sandbox or workspace handle for each conversation so tools can resume the same isolated files, terminals, browsers, and services.
- `reg-feature-flags` — The workspace feature switches that let the system turn capabilities on or off without changing the code.
- `reg-observability-traces` — The shared logs, metrics, traces, traceparent links, and safety-filtered operator views used to understand what the system is doing.
- `reg-object-store` — The workspace object records and change journal for agents, members, files, credentials, sites, connectors, memory records, reports, and extension objects.
- `reg-audience-visibility` — The saved visibility and audience rules that decide who may see a conversation, transcript, agent, source, artifact, or object.
- `reg-artifact-blob-store` — The shared file, blob, attachment, artifact, signed download, and media-preview storage used to publish and recover produced work.
- `reg-hosted-site-registry` — The hosted-site records that remember who owns each site, which conversation created it, where it runs, and how previews or sharing are allowed.
- `reg-service-connection-pools` — Process-local shared connection/client pools for database, Redis/pubsub, HTTP/provider calls, and other long-lived service clients reused by requests, workers, tools, and jobs.
- `reg-request-actor-scope` — Context-local current workspace, member, acting agent, and object/action scope carried through authorization, database boundaries, object APIs, tools, and egress checks.
- `reg-surface-delivery-outbox` — Durable outgoing surface delivery state for final replies, mid-turn replies, writebacks, claims, retries, and de-duplication across Slack, iMessage, web, terminal, and other surfaces.
- `reg-membership-access-policy` — Durable workspace membership, owner/admin role, seat, invitation, and mutation-permission state used to decide what a member may manage beyond simple object visibility.
- `reg-http-route-registry` — Process-local mounted HTTP, WebSocket, callback, and extension route dispatch table used by the running web server.
