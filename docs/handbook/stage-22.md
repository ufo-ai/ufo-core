# Security, Access Control, Credentials, and Billing Policy  `stage-22` (cross-cutting infrastructure)

This stage is the system’s safety and policy layer. It runs behind the scenes during login, incoming requests, sandbox activity, model/tool use, and paid jobs. Its job is to answer practical questions before work continues: who is this, what workspace are they in, what may they access, which secrets may be used, and can this work be paid for?

Credential grants and egress policy act like a guarded vault and exit gate. They store encrypted credentials, track who allowed an agent to use them, and decide which outside services a sandbox may contact. Signed token code creates tamper-proof login, public route, and sandbox access tokens. Workspace context, agent scope, and seats keep actions tied to the right workspace, agent, and member permissions.

Content visibility and governance label who may see text, protect against untrusted outside instructions, and require safe approval for prompt changes. Billing tracks usage, prepaid balances, spend caps, and links to Metronome and Stripe. The two __init__.py files simply make the access and auth folders importable by the rest of the code.

## Sub-stages

- [Credential Grants and Egress Policy](stage-22.1.md) `stage-22.1` — 4 files
- [Signed Login, Surface, and Ingress Tokens](stage-22.2.md) `stage-22.2` — 4 files
- [Workspace Context, Agent Scope, and Seats](stage-22.3.md) `stage-22.3` — 3 files
- [Content Visibility, Audience Labels, and Governance](stage-22.4.md) `stage-22.4` — 5 files
- [Billing Ledger, Balances, and Payment Integrations](stage-22.5.md) `stage-22.5` — 4 files

## Files in this stage

### Access and Authentication Packages
Package initializers that make access-control and authentication modules importable for the security stage.

### `core/src/ufo/access/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can contain an `__init__.py` file to say, “treat this folder as an importable package.” That lets other parts of the project refer to code inside `core/src/ufo/access` using package-style imports, such as importing something from `ufo.access.some_module`.

Because the file is empty, it does not run setup code, expose shortcut names, or change how the access subsystem works. Its value is structural: it helps organize the project into named areas. You can think of it like a label on a drawer. The label does not contain the tools, but it tells the rest of the workshop that this drawer exists and can be opened in a predictable way.

Without this file, depending on the Python version and packaging setup, imports from this directory might be less reliable or fail in environments that expect traditional package markers.


### `core/src/ufo/auth/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder with an `__init__.py` file is treated as a package, which means other parts of the project can import modules from it using names like `ufo.auth.something`. Think of it like a label on a drawer: the drawer may contain useful tools, but this label itself does not do the work. Without this file, some Python setups or packaging tools might not recognize `ufo.auth` as an importable package, which could make authentication modules harder or impossible to load in the expected way. Since the file is empty, it does not run setup code, expose shortcuts, or change how authentication works. Its value is structural: it helps keep the code organized and importable.

## 📊 State Registers Touched

- `reg-effective-config` — The current trusted settings for how the service should run, including database, provider, deployment, and safety options.
- `reg-database-session-workspace-scope` — The shared database access layer that keeps reads and writes inside the right workspace and transaction.
- `reg-workspace-directory` — The durable list of workspaces and their core ownership, admin, billing, and setup state.
- `reg-member-auth-principals` — The shared answer to who the current person or service is and what member identity they are acting as.
- `reg-agent-profiles` — The saved assistant definitions, including each agent's model, tools, visibility, setup needs, internet access, and identity.
- `reg-surface-routing` — The mapping from outside places like web, Slack, terminal, and iMessage to the right workspace, conversation, member, and agent.
- `reg-conversation-records` — The durable conversation list, including titles, audience, surface labels, sandbox links, and visibility rules.
- `reg-turn-state` — The shared status record for each unit of agent work, including claiming, running, completion, failure, parent-child links, and billing markers.
- `reg-transcript-store` — The saved conversation history and compacted summaries that later turns, portals, and auditors read back.
- `reg-billing-ledger` — The shared money and usage record for tokens, images, videos, sandbox use, egress, balances, caps, and exports.
- `reg-credential-vault` — The encrypted store of API keys, connected accounts, grants, and approvals that lets tools use outside services without exposing secrets.
- `reg-egress-policy` — The shared network exit rules that decide which outside addresses sandboxes may contact and when secrets may be added.
- `reg-sandbox-handles` — The remembered execution workspaces, browser workbenches, terminal sessions, and sandbox IDs used across a conversation or turn.
- `reg-source-sync-state` — The saved state of connected content sources, including pages, checkpoints, errors, ownership, and read grants.
- `reg-observability-context` — The shared tracing, logging, metrics, health, and redaction context used to understand what happened safely.
- `reg-proposal-state` — Durable proposed-change records, including pending, approved, or rejected prompt/config/self-improvement proposals and their before/after payloads.
- `reg-transcript-access-audit-log` — Durable audit trail of privacy-sensitive transcript reads, especially admin access to another member’s private conversation history.
- `reg-workspace-membership-roster` — Durable workspace member records, roles/admin flags, invitations, inviter stamps, seating history, and member-local profile fields such as timezone or email lookup data.
- `reg-auth-and-oauth-flow-state` — Short-lived login and OAuth handoff state such as nonces, return targets, code-verifier data, pending claims, and callback correlation before it becomes an authenticated principal or stored credential.
- `reg-signed-token-keyring` — Shared signing secrets, key IDs, expiry rules, and validation parameters used to mint and verify login, public-route, artifact-download, and sandbox-access tokens.
- `reg-egress-policy-generation` — Per-workspace egress-rule version or invalidation counter used to rebuild cached sandbox proxy rules after credential, grant, or network-policy changes.
- `reg-content-provenance-trust-labels` — Visibility, provenance, and trust labels attached to messages, source content, and external text so prompt construction and policy checks can separate trusted instructions from untrusted content.
