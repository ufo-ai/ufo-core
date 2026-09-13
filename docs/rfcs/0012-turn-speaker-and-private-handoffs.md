---
rfc: 0012
title: "Turn speaker and private handoffs"
status: implemented
date: 2026-07-12
---

# Turn speaker and private handoffs

> Record who authored each member turn separately from the conversation's disclosure scope. A
> member may then authorize an account connection from a shared Slack channel without making that
> channel their private conversation or exposing its OAuth URL to the channel.

## Current state

Slack resolves a member only for DMs (`extensions/slack/ufo_ext_slack/surface.py:603`), while the
turn engine loads `ToolContext.member_id` from `conversation.member_id`. Channel conversations
therefore have no speaking member, so `connect_account` refuses them
(`core/src/ufo/host/tools/builtins.py:591`). Filling the conversation field with the sender would expose
their personal recall to every participant. Returning the OAuth URL as tool text would also let the
model post it publicly.

## Proposal

| Record | Field | Meaning |
|---|---|---|
| `turn` | `speaker_member_id: UUID | None` | Member who authored this inbound |
| `turn` | `connect_authorization_url: str | None` | Memoized private handoff for its terminal request |
| `turn` | `connect_authorized_at: datetime | None` | Mint time bounding replay of that URL |
| `conversation` | `member_id` | Existing private/shared disclosure scope; unchanged |
| `TerminalFrame` / `Writeback` | `connect_request: ConnectRequest | None` | Provider awaiting a member-only continuation |

Every member surface passes its authenticated member to admission. Slack resolves the sender on
every inbound. A shared-channel resolution failure admits the turn with no speaker and
member-gated tools fail closed; a DM or other member-private conversation fails admission without
its member. Only a surface-admitted member inbound has a speaker; timer, system, and subagent turns
have no speaker and carry only their exact persisted runtime capabilities.

`ToolContext` and `HookContext` name both meanings: `speaker_member_id` gates `connect_account`,
credential requests, and owner checks; the exact conversation `audience` atom feeds recall, memory
writes, hooks, and transcript access without being re-derived from the speaker.
`request_credentials` and extension provider authorization through
`begin_credential_authorization` require the speaker's private member audience; shared and venue
turns fail closed. Member spend remains an accounting policy and is not changed here.

`connect_account` validates the provider and returns a structured `ConnectRequest`, not a URL. The
engine carries the request like `AskUserInput` and `CredentialRequest`; the terminal turn is the
durable pending action.

Slack renders a button with `value=<turn_id>` and no `url`. Its signed interaction handler resolves
the clicker and asks one core workflow for the URL. That workflow loads the turn, requires the
clicker to equal `speaker_member_id`, and atomically memoizes one URL from `ConnectFlow.authorize`.
Minting requires the terminal request to be no older than `CONNECT_STATE_TTL_SECONDS`; a memoized
URL replays until `connect_authorized_at` reaches the same TTL. Concurrent claims return the stored
winner, and a retry by the same speaker returns that URL rather than minting another. Slack delivers
it through an ephemeral interaction response. Long replies split into bounded blocks; an
`invalid_blocks` response retries with section blocks and the same connect button, never plain text.
A different clicker or stale request receives an ephemeral refusal. CLI, the `ufo` terminal surface,
and web use the same workflow with their authenticated member. The OAuth callback records the
sealed speaker as grantor and directs the member back to chat; it does not fabricate an inbound
turn. Later connector use from a live message is scoped to its selected speaker. Automatic work
uses only its exact persisted connection capabilities and never reconstructs access from a creator
or conversation owner. The granting act is speaker-only; use scoping narrows use, never widens
grant authority.

Interactive continuations find their existing conversation before admission. A DM click may then
bind a newly resolved member to that private conversation; a channel click never calls
`conversation_for`, so it cannot claim a shared thread (`core/src/ufo/runtime/ext/surface.py:410`).

**Proof:** a linked member asks to connect Google Calendar in a public channel, receives the OAuth
URL privately, completes the grant, returns to chat, and asks the agent to use it in that channel.
Another member cannot obtain the URL. The conversation stays shared, personal recall stays absent,
an unresolved speaker cannot connect, a subagent inherits only exact runtime capabilities, extension-owned
authorization refuses a shared audience, a lost response replays the same URL, and DM/CLI/`ufo`/web
flows still complete.

## Doctrine fit / implications

Speaker identity is asserted once by the trusted surface, persisted for DBOS recovery, and threaded
to authorization consumers. The URL never enters model context or the public transcript. No Slack
primitive enters core: core exposes a checked handoff; the Slack extension chooses buttons and
ephemeral delivery.

## Alternatives

- Setting `conversation.member_id` to the sender conflates authorization with disclosure.
- Posting the OAuth URL and trusting only its sealed grantor lets another channel member connect an
  account under that grantor's audit identity.
- DM-only connection preserves the identity gap for every other member-authorized action.
