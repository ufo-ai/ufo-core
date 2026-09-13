# Grant system

## Resource grants

Each resource keeps its own authoritative edge:

| Resource | Grant |
|---|---|
| Connector account | `connector_grant`: connection → agent |
| Source content | Its connection's live agent attachment plus audience |
| Workspace credential | Declared workspace slot |
| Member model account | Exact provider slot in `TurnRuntimeConfig.model_accounts` |
| Stored content | Its audience subject and kind-specific ownership rules |

There is no polymorphic grant table and no generic principal path. Foreign keys, cascade deletion,
and each resource's own invariants remain enforceable in its store.

## Connection use

An ordinary call with a selected member may use that member's private attachments and connections
shared with the agent. In a multi-speaker round, no selection means conversation-common access only.

An automatic turn uses a non-null connection allowlist. It may use exactly those listed live agent
attachments regardless of owner; an empty tuple reaches none. Revocation, disconnect, replacement,
or narrowing takes effect when the live edge is rechecked. The same allowlist governs connector
selection, server-side execution, CLI export, proxy injection, git credentials, synced source reads,
and object discovery.

## Consent

`requested_by` selects an exact authenticated active message; it does not grant a resource itself.
The consent gate compares that message with the exact post-policy effect. A standing
`member_permission` records one member's approval for one exact agent, call, and effect. It changes
neither resource ownership nor the live resource gate.

Granting acts require a live speaker even after consent. Automatic work can spend capabilities a
member already granted but cannot connect an account, share it, revoke it, fill a credential, or
exercise administrator privileges.

## Persistence

The turn is the audit record. Automatic objects persist exact capabilities at creation and copy
them into every fired turn. A spawn persists exact connection and model-account capabilities on its
child. Creator ids remain management metadata only.
