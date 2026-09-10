---
rfc: 0038
title: "Email delivery — SES hosted, campaigns in control"
status: implemented
date: 2026-09-09
---

# Email delivery

> Two kinds of message leave this deploy. A transactional one carries a link to one address that
> just asked for it. A founder campaign carries our words to every member at once, and the words,
> the audience, the count, and the refusals all have to be reviewable before it goes. This record
> fixes the transport both use, who owns the campaign, and where an operator reads the result.

## Decision

Amazon SES is the hosted email transport. A campaign is sent from one of a configured list of
`@ufo.ai` addresses the operator picks in the HUD, and replies go to that same address — a Google
mailbox someone reads.

| Fact | Consequence |
|---|---|
| SES is the transport | one verified identity, `ufo.ai`, with DKIM and a custom MAIL FROM |
| The From is a per-campaign choice | one identity carries every address; the ledger freezes the one it was approved under |
| Reply-To is the From | a reply to a person reaches that person, not a shared alias |
| Transactional stays separate | the invitation keeps `no-reply@ufo.ai`, its own words, and no list management |
| Control owns campaign execution | `servers/control/`: the ledger, the sender, the feedback consumer, the HUD |
| SES is the final unsubscribe gate | a send carries `ListManagementOptions`; SES refuses an unsubscribed contact |
| Postgres owns campaign and delivery history | `ufo_control.email_campaign` / `email_recipient` / `email_event` |
| The HUD is a control surface | `/surface/email` on the gateway, under the operator-domain gate |

## Why control, not core

Core cannot reach a mailer: RFC 0027 keeps `workos` control-only, core has no send path, and a
self-hosted deploy runs no gateway. Control already holds the SES sender, the IRSA grant, and its
own schema. A campaign is an operator act over the whole fleet, so it belongs where the fleet is
already addressable and where no tenant's RLS scope has to be crossed to read one.

Control's database role reaches `ufo_control` and nothing in `public`, so the recipient list comes
from core over the internal onboarding RPC — one paged, authenticated read projection beside
`invitations`, never SQL against a core table.

## The ledger

```text
draft -> prepared -> approved -> sending -> completed
                        |           |
                        +-> cancelled <-+
```

| Rule | Why |
|---|---|
| Preparing freezes content and the exact recipient list | approval means nothing over a moving audience |
| Editing prepared content bumps the revision, drops approval and the frozen list | the list belongs to the words it was frozen for |
| Recipients are unique by normalized email | one member, one message, whatever they are seated in |
| Operator addresses are excluded unless the audience is `operators` | a fleet campaign is not a staff campaign |
| Unsubscribed, complained, and hard-bounced addresses are excluded | SES bounces them anyway; we do not spend reputation finding out |
| The attempt is marked before SES is called | a crash mid-send reads as attempted, never as unsent |
| An uncertain SES result never retries | a duplicate campaign email is worse than a missing one |
| A cancel stops only rows that have not started | a sent message cannot be recalled |
| One recipient per send | SES adds `List-Unsubscribe` only to a single-recipient message |

`email_event` is immutable and keyed by `(ses_message_id, event_type, occurred_at)`, so SNS's
at-least-once redelivery lands once.

## Doctrine fit

- **Both ends.** Every event type the configuration set publishes has a consumer; every column the
  recipient row carries is read by the HUD.
- **Retry.** SES, STS, and SQS are external uncertainty. The feedback consumer polls; the sender
  does not retry, by the rule above.
- **A tear-out includes its rows.** The three tables are one DDL group; dropping the feature drops
  all three.
- **Derived state.** Delivery counts are not computed on write: the feedback consumer writes the
  recipient's last known delivery state, and the HUD counts rows.

## Alternatives

**A mail API (Resend, Postmark).** Smaller than SES per send, and an idempotency key makes a resend
safe rather than merely decidable. It carries no contact list, so the unsubscribe gate becomes ours
to build and ours to get wrong, and it adds a second vendor beside the one that already carries the
invitation. Reconsider if SES reputation management becomes the cost.

**WorkOS sends the message.** Branding sets the logo, the colors, and the theme; it is not a subject
or body editor. Our line is ours. Custom Email Providers would route every transactional message,
Magic Auth codes included, through one campaign vendor's key — the sign-in path's blast radius for
a newsletter.

**Open and click tracking.** Not enabled. Link rewriting costs deliverability and a custom redirect
domain, and no decision here turns on who clicked.
