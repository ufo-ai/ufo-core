---
rfc: 0038
title: "Invitation delivery — one mail API, our words"
status: accepted
date: 2026-08-20
---

# Invitation delivery

> A teammate added to a workspace now gets an email. The delivery costs a leased ledger, a
> 15-second poll, and a hand-rolled SigV4 sender — 1,849 lines of `servers/control/` to carry three facts
> and a link. This record fixes what the transport becomes, what comes out with it, and in what
> order. The message does not change: our words, our link.

## Current state

`AddMember.add` (`core/src/ufo/kinds/members.py:318`) mints the member row and stamps `invited_by`. That
row is the grant — `servers/control/src/shared.rs:125`: "Every workspace this address may enter: its exact
memberships plus the one its verified domain names." The invitation is therefore a message and
nothing more. An unread one locks nobody out.

Core cannot reach a mailer: RFC 0027 keeps `workos` control-only, core has no send path in any
language, and a self-hosted deploy runs no gateway. So control polls core.

| Piece | Cost | Why it exists |
|---|---|---|
| `servers/control/src/invite_delivery.rs` | 737 lines, 1,112 in its test | the queue core cannot enqueue into |
| `materialize` at `POLL_INTERVAL_SECONDS = 15` | reads every invited member ever, paged | core holds no delivered mark, so no cursor is safe |
| lease, `for update skip locked`, backoff to an hour | | two replicas over one table |
| attempt marker, `SendVerdict`, `AMBIGUOUS_SEND` | | SES answers no read, so after a timeout "was it sent" is undecidable |
| `INVITATIONS_PER_WORKSPACE_PER_DAY = 100` under an advisory lock | | abuse policy |
| `SesEmailSender` (`servers/control/src/email.rs:315`) | ~400 of that file's 600 lines | a local SigV4 signer, an STS `AssumeRoleWithWebIdentity` exchange, an XML parse |

`email.rs` has a second caller: `invite_email` (line 198), the waitlist grant. It leaves when the
waitlist does.

## Proposal

Three units, in this order. Each stands alone.

### 1. The transport

Swap SESv2 for one authenticated POST to a mail API, behind the `EmailSender` seam that already
exists (`servers/control/src/email.rs:545`).

| | Today | After |
|---|---|---|
| transport | SESv2, local SigV4, STS per send | one POST |
| resend safety | attempt marker, `SendVerdict`, `AMBIGUOUS_SEND`, manual re-arm | an `Idempotency-Key` per attempt |
| copy, link | ours | unchanged |
| ledger, lease, cap, poll | as built | unchanged |

An idempotency key makes a resend *safe*, which is stronger than the marker's attempt to make it
*decidable*. Resend's key window is 24 hours and covers the ledger's backoff.

### 2. The sender

When the waitlist grant goes, `email.rs` has one caller and the SES apparatus comes out: the
signer, the STS exchange, the XML parse, `AwsEndpoints`, every `SES_*` / `STS_*` / `AWS_*`
constant, `INVITE_HTML` and `html_escape`, `infra/modules/platform/ses.tf`, and the gateway
ServiceAccount's IRSA annotation — `ses:SendEmail` is that pod's only AWS grant. `WorkEmailPolicy`
stays: it is the work-email gate, not the transport.

### 3. The ledger

Give core one outbound call at `AddMember.add` and the reconciler has no work left — a mail API
owns delivery once it accepts the message. Deleted: `invite_delivery.rs` and its test, the table
and its rows in one migration, the `invitations` RPC (`servers/control/src/shared.rs:153`), and
`invite-delivery-retry`. Added: one hosted-only core→control call, with the per-workspace cap at
the call site.

## Doctrine fit / implications

- Core is untouched by units 1 and 2. Unit 3 adds a genuinely new seam — core calling control,
  where today only control calls core. It fails loud when configured and no-ops where the deploy
  has no gateway.
- **Both ends.** `member.invited_by` exists to name the inviter in the copy. Keeping our words
  keeps its only reader.
- **A tear-out includes its rows.** Dropping `invite_delivery` drops its rows in the same
  migration.
- Retry against a mail API is proven external uncertainty and is legitimate. The queue's retry
  against our own ledger is not, and does not survive unit 3.

## Alternatives

**WorkOS sends the message.** WorkOS is already a control dependency, and a GET answers "did it
send". Two things rule it out.

- *The words.* Branding sets the logo, the colors, and the theme. It is not a subject or body
  editor. Our line is `"{invited_by} added you to the {workspace_label} workspace on ufo."`
  Getting either fact into WorkOS's template needs an organization and an `inviter_user_id`, which
  puts the member directory in two places. RFC 0027 rejected exactly that.
- *An invitation object is not wanted.* The member row is the grant. A WorkOS invitation adds a
  second lifecycle — `pending` / `accepted` / `expired`, 30 days at most — over a membership that
  never expires, and with no organization it is redeemable from any address. WorkOS can be the
  mailer, never the gate, so its state machine is weight to reconcile against our own truth.

Two objections often raised are not true and are not the reason: `organization_id` is optional, so
an application-wide invitation exists; and the accept URL is a dashboard setting under
Applications → Redirects, so the link can be ours.

**WorkOS Custom Emails.** Turn their invitation email off, read the invitation object, send our own
words. It restores the copy and still needs the sender this record deletes.

**WorkOS Custom Email Providers.** WorkOS sends through our own Resend account, on our domain, with
no send code of ours. It carries every transactional email with it, Magic Auth sign-in codes
included, so one bad key in that account breaks sign-in. That is the sign-in path's blast radius
for one message.

**Keep the SES sender.** The signer, the verified identity, the IRSA role, and the undecidable
resend all stay.

## Open decisions

1. **Resend or Postmark.** Postmark's delivery reporting is the stronger one; Resend's API is the
   smaller.
2. **Whether core may call control.** Without it, unit 3 does not happen and the ledger is
   permanent.
3. **Order against the waitlist removal.** Unit 2 cannot land before it.
