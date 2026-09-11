---
rfc: 0047
title: "Lifecycle email — an extension over one send seam"
status: proposed
date: 2026-09-10
---

# Lifecycle email

> A founder campaign is one blast to everyone at once. A lifecycle message is the opposite: one
> person, because of something they did or did not do, at a time measured from that moment. Drip
> sequences, product triggers, and billing notices are all that one shape. This record fixes where
> the engine lives, what fires it, and the single seam it needs from core.

## Decision

Lifecycle email is an extension. `extensions/lifecycle_email` owns the sequences, the enrollments,
the schedule, and the operator editor. Core grows exactly one new thing: a capability to send one
message. Everything else already exists.

| Fact | Consequence |
|---|---|
| The engine is an extension | it earns no place in `core/`; the SDK send capability is the whole core change |
| Sends route through `servers/control` | one SES client, one suppression store, one feedback consumer — no second sender |
| Triggers are hooks and sweeps | no new `HookEvent`, no event bus; `post_tool_use` and per-minute jobs cover both kinds |
| Transactional templates live in the tree | their wording is product copy, so a diff reviews it |
| Drip copy lives in rows | marketing iterates without a deploy, under an approve-before-live gate |
| Timing is a due column swept per minute | the `scheduled_tasks` pattern, lease-claimed, already proven |

## Why an extension holds it

`core/src/ufo/product.py` states the house position on event storage: "Every stage is already a row
— a connector grant, an invited member, a charged purchase — so nothing here is stored and no event
has to be caught as it happens." Lifecycle email does not overturn that. It needs an event *instant*
only to measure a delay from, so it keeps its own small log in its own tables rather than asking
core for a general event bus nobody else reads.

The manifest's hook taxonomy makes the same argument from the other side: "a declared event with no
fire-point is exactly the crippling this taxonomy avoids." A `balance_changed` or `member_seated`
hook would be a declaration with one consumer. A per-minute sweep over `workspace_balance` and
`member` reads the same truth and matches the standing rule that batch-at-interval is the default.

## The one seam

No Python in the repo sends email. Every sender is in `servers/control`: `SesEmailSender` for
transactional, `FounderSender` for campaigns, and `CampaignFeedback` consuming SES events off SQS
into `ufo_control.email_event`. A second sender in Python would duplicate the SES client, the
suppression union, and the feedback consumer three times over.

So the extension does not send. It asks control to.

| Layer | What lands |
|---|---|
| `servers/control` | `POST /internal/email/send` — one address, one rendered message, a named kind; returns the SES message id |
| `ufo.sdk` | `send_email(...)`, the capability an extension holds; posts to that route with the control token |
| `ufo_control` | the row the feedback consumer already updates, so a lifecycle send reports delivery like a campaign |

Suppression stays where it already is, and control applies it centrally. A hard suppression —
bounced, complained — bars every kind. A topic opt-out bars marketing and drip, and never bars
transactional: a member who unsubscribed from product news is still told their balance ran out.

## What fires a sequence

| Kind | Fire point | Exists today |
|---|---|---|
| Product — feature use | `HookSpec(event="post_tool_use", tools=(...))` writes an event row | yes, unchanged |
| Product — absence of use | per-minute job over the extension's own event rows | yes, unchanged |
| Lifecycle — seated, invited, connected | per-minute sweep of `member`, `object_change` | yes, unchanged |
| Transactional — balance, limits | per-minute sweep of `workspace_balance` headroom and `spend_cap` | yes, unchanged |

Nothing in that table asks core for a new hook. The product events the SDK already fires cover
feature use, and everything else is a state a sweep can read.

## The state a sequence holds

Three tables in the extension's own migration chain, following `app_notification`'s precedent.

| Table | Holds |
|---|---|
| `lifecycle_event` | `(workspace_id, member_id, name, occurred_at, payload)` — the instant a delay is measured from |
| `lifecycle_enrollment` | one member in one sequence: `step`, `next_due_at`, `claimed_by`, `claim_expires_at`, `state` |
| `lifecycle_send` | one attempt: `ses_message_id`, `delivery`, `attempted_at`, `last_error` |

The runner is the `scheduled_tasks` shape: a per-minute job whose candidates are the workspaces with
a due enrollment, a lease claim on the row, and the attempt marked in the same statement that claims
it. A result nothing can decide fails rather than repeating — a duplicate lifecycle email is worse
than a missing one, exactly as for a campaign.

An enrollment ends when the sequence ends, when an exit condition matches, or when the member
suppresses the topic. A member is in a sequence once: re-entry needs the enrollment to have ended.

## Who writes the words

| Kind | Home | Changing it |
|---|---|---|
| Transactional, event-triggered | a template module in the extension | a diff and a deploy |
| Drip sequence copy and timing | rows the operator edits | an edit, then an approval, then live |

The operator editor is a `RouteSpec` under `/ext/lifecycle_email/`, gated on the operator email
domain the way `extensions/debugger` and `extensions/memory` already gate their pages. A sequence
carries a revision; editing bumps it and drops the approval, and the runner only sends an approved
revision — the campaign ledger's rule, applied to a sequence.

## Units

Each lands with both ends and its own proof.

| # | Unit | Proves |
|---|---|---|
| 1 | The send seam, plus the balance-exhausted notice | an extension sends one message and reads its delivery back |
| 2 | Event log, enrollment, the per-minute runner, one repo-defined sequence | a delay measured from an instant fires on time, once |
| 3 | `post_tool_use` product triggers and an absence-of-use sequence | a feature event enrolls, and a non-event does too |
| 4 | The operator editor, revisions and approval | drip copy changes without a deploy and cannot go out unapproved |
| 5 | Member topic preferences | a member silences marketing and still receives transactional |

## Non-goals

Open and click tracking. SES rewrites every link to measure them, which costs deliverability and a
redirect domain, and no decision here turns on who clicked — the same call RFC 0038 made.

A general event bus. The extension logs what it measures delays from and nothing else.
