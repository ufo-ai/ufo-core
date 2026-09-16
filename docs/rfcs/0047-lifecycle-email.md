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
| `servers/control` | `POST /internal/email/send` — one address, one rendered message, a named kind; returns the SES message id. `GET /internal/email/send/{id}` reads the delivery back |
| `ufo.sdk` | `EmailSends`, held on the scoped context as `ctx.email`; posts to those routes with the control token. A deploy naming no control service wires none, and the capability is absent rather than silently doing nothing |
| `ufo_control` | `email_send`, one row per message; the consumer that reports a campaign's delivery updates it by the same SES message id |

An extension posts words, never markup: a subject, paragraphs separated by a blank line, and at
most one act. One frame draws every message this deploy sends, a campaign and a balance notice
alike, so the frame lives in control beside the sender that carries it and a second one cannot grow
in Python. It also means the unsubscribe footer is added where the topic is known, which is the
only place it can be: SES fills the placeholder only for a send that names a contact list.

Transactional mail gains its own SES configuration set, on the topic and queue the campaign set
already publishes to. A configuration set is what makes SES publish a delivery event at all, so
without one the seam could send and never report; the set is separate because the identity and the
`ses:FromAddress` condition differ from a campaign's, and the consumer does not.

Suppression stays where it already is, and control applies it centrally. A hard suppression — a
bounce or a complaint — bars every message this deploy sends, because reaching that address again
costs the sending domain its standing. An unsubscribe is not one of those. It says which mail the
member does not want; it does not say the address is bad. So an unsubscribe is a topic preference,
and a preference bars its own topic and nothing else: a member who leaves the founder list is still
told their balance ran out, because that is what their workspace is doing with their money. Every
send names a topic. `transactional` is the one a member cannot silence; `product_news` and
`founder_updates` are the two they can — but only `product_news` is ours to set. SES holds the
founder list's opt-out on its own contact and applies it to the send, so lifting our row alone
would report a resume that never happens; the hosted page is what lifts it.

The preference is a row in `ufo_control` beside the suppression it joins, and two paths write it.
In chat, the agent holds a tool and calls it for the speaker's own address. In the mail, SES's
hosted unsubscribe page records the opt-out and publishes a `Subscription` event; the feedback
consumer maps the contact-list topic it names to ours and writes the row — and lifts it again on
the opt-in the same page publishes, which for the founder topic is the only surface that can. There is no unsubscribe
endpoint of our own.

## What fires a sequence

| Kind | Fire point | Exists today |
|---|---|---|
| Lifecycle — invited | per-minute sweep of core's members | yes, unchanged |
| Lifecycle — absence of use | per-minute sweep of the extension's own event rows, banded by age, against a live read of core's connections | yes, unchanged |
| Transactional — balance, limits | per-minute sweep of `workspace_balance` headroom and `spend_cap` | yes, unchanged |

Nothing in that table asks core for a new hook, and nothing is caught as it happens. Every state
is already a row, so a job on a clock reads it — the standing rule that batch-at-interval is the
default. A hook that fired on the act would say less: a member who asks to connect an account has
not connected one, and the row is what knows whether they did.

## The state a sequence holds

Three tables in the extension's own migration chain, following `app_notification`'s precedent.

| Table | Holds |
|---|---|
| `lifecycle_event` | `(workspace_id, member_id, name, occurred_at, reconciled_at)` — the instant a delay is measured from, and whether a pass has offered it to the sequences yet |
| `lifecycle_enrollment` | one member in one sequence: `step`, `next_due_at`, `claimed_by`, `claim_expires_at`, `state` |
| `lifecycle_send` | one attempt: `(workspace_id, member_id, kind, occasion)`, `state`, `ses_message_id`, `delivery`, `last_error` |

`occasion` is what makes a message once-per-reason rather than once-ever: it names the instance of
the state that caused it — for the balance notice, the total credit the workspace has ever been
granted, so a top-up that is spent again is a new occasion and the same admin is told again. The
unique key over it is the claim: the row is inserted before control is called, and a second pass
inserts nothing and sends nothing.

The event row and the enrollments measured from it are one transaction. The event is the whole
idempotency key — a second pass reads it and writes nothing — so a pass that committed the event
and then stopped would leave a member logged, never enrolled, and beyond repair.

The runner is the `scheduled_tasks` shape: a per-minute job whose candidates are the workspaces with
a due enrollment, a lease claim on the row, and the attempt marked in the same statement that claims
it. A result nothing can decide fails rather than repeating — a duplicate lifecycle email is worse
than a missing one, exactly as for a campaign.

An enrollment ends when the sequence ends, when an exit condition matches, or when the member
suppresses the topic. A member is in a sequence once: re-entry needs the enrollment to have ended,
which a unique index over the live rows is what says.

An exit condition is not a second kind of declaration. A step composes its message from the member
as they are now, and answers nothing where the reason for the message is gone — a teammate who has
since shown up, a seat that was removed — so the enrollment ends having sent nothing and the
condition lives beside the words it governs.

A producer only logs an instant; one job decides what measures from it, and marks the event read
whether or not anything did. That is what lets a sequence approved this morning reach the events
logged since the last pass without reaching a year of them, and what keeps a hook from knowing
whether a sequence lives in the tree or in a row.

A sequence measured from an instant is only offered to members who reached it recently: the sweep
that enrolls looks back a bounded window, which keeps a per-minute job off the whole fleet and
states the product rule at the same time — a sequence measured from an invitation has nothing to
say about one from last quarter.

## Who writes the words

| Kind | Home | Changing it |
|---|---|---|
| Transactional, event-triggered | a module in the extension | a diff and a deploy |
| Drip sequence copy and timing | rows in `ufo_control` the operator edits | an edit, then an approval, then live |

The editor is served by `servers/control`, beside the campaign HUD and behind the same operator
cookie and CSRF proof, and the rows live in `ufo_control`. Not a `RouteSpec` under
`/ext/lifecycle_email/`: a sequence is one set of words for the whole fleet, and every table an
extension can reach is scoped to one workspace by row security — a fleet-wide row there is
invisible to the role the extension reads with. The control schema is outside that fence, which is
the same reason the campaign ledger is there. The runner reads the approved set through one more
bearer-gated internal route on the channel the send seam already opened.

A sequence carries a revision; editing bumps it and drops the approval. Approving stores that
revision's own words, and the runner is handed those — not the row, which is what the operator is
editing. So an edit in progress changes nothing a member is part-way through, and retiring is the
one act that stops a sequence: a sequence the runner cannot resolve has no step left, so a live
enrollment ends rather than waiting on words that are not coming.

A row holds what a module holds: a subject, a body, and at most one act — a button's label and its
link. Its words are literal but for `{url}`, which becomes this deploy's portal. One placeholder and
no expression language: copy that could fail to render is a message a member never gets and nobody
is told about.

## Units

Each lands with both ends and its own proof.

| # | Unit | Proves |
|---|---|---|
| 1 | The send seam, plus the balance-exhausted notice | an extension sends one message and reads its delivery back |
| 2 | Event log, enrollment, the per-minute runner, one repo-defined sequence | a delay measured from an instant fires on time, once |
| 3 | The sweep that writes events, and an absence-of-use sequence | an invitation is logged where the member is invited, an absence is logged where the deadline passes without a connection, and both enroll |
| 4 | The operator editor, revisions and approval | drip copy changes without a deploy and cannot go out unapproved |
| 5 | Member topic preferences | a member silences product news in chat and still receives transactional |
| 6 | An unsubscribe becomes a topic preference | a member who leaves the founder list is barred from the next campaign and still gets their balance notice |

## Non-goals

Open and click tracking. SES rewrites every link to measure them, which costs deliverability and a
redirect domain, and no decision here turns on who clicked — the same call RFC 0038 made.

A general event bus. The extension logs what it measures delays from and nothing else.
