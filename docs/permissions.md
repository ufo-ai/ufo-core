# Permissions

Identity attributes an act; capabilities authorize it. A creator, conversation owner, or earlier
speaker never becomes the principal of automatic work.

## Member calls

The engine starts a round with no selected speaker. One authenticated member speaking alone binds
automatically. In a round with multiple speakers or an unattributed message, `requested_by` selects
one exact active member message.

Before the selected member's private access opens, a side `gpt-5.6-luna` call classifies prior
consent from that message's exact words, the policy-rewritten effect, and an exact standing
permission. A grounded allow dispatches. A grounded denial refuses. An uncertain decision asks that
member `Allow`, `Deny`, or `Always Allow` in chat. The authenticated answer carries the pending
authorization id and structured choice; labels and free text never settle a grant.

`SpeakerRequired` is the repairable refusal. The model may retry the call with a visible active
message ref. A granting or administrative act still requires a live speaker after selection.

## Automatic work

Scheduled tasks, source triggers, monitors, pauses, notification lanes, site recovery, and
subagents have no speaker. Their creator ids own and audit their configuration; they confer no
runtime access.

`TurnRuntimeConfig` is the durable capability snapshot:

| Field | Meaning |
|---|---|
| `connections` | Exact active connection ids the turn may use; `()` admits none |
| `internet_access` | A narrowing of the agent's public-internet ceiling |
| `model_accounts` | Exact opaque member-provider slots granted to a spawn |
| `model` | Concrete model pin |
| `environment` | Exact environment-document digest |

Automatic callers persist these values when the member creates or arms the work. Descendants
inherit them. Execution never reconstructs them from a creator, audience, or owner.

## Resource decisions

| Decision | Gate |
|---|---|
| Use a connection | Agent attachment plus selected speaker, or exact `connections` capability |
| Read content | Conversation audience plus per-kind disclosure rules |
| Change ownership or sharing | Live owner or administrator speaker |
| Connect, revoke, or fill a credential | Live speaker |
| Use a member model account | Exact `model_accounts` capability |
| Manage the workspace | Live administrator speaker, checked from the member row |

A private conversation's audience may expose its own private content to speakerless work running in
that conversation. That is a disclosure property of the room, not an inferred execution member.
A foreign room remains sealed from workspace content.

## Seats

Member admission requires a seated member. Seat state is checked again after model work and before a
selected member's tool effect. Automatic work has no member seat to infer; revoking a creator's seat
does not rewrite capabilities already granted to an automatic task.

## Boundaries

Workspace scope is established at every trusted boundary. Postgres RLS enforces it for the serve
role. Signed sandbox and probe tokens carry the workspace, turn, and exact connection ids. The
egress proxy and server-side connector path both recheck those ids against live agent attachments
before use.
