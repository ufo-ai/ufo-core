# Founder email

A founder campaign is written, prepared, approved, and sent from `/surface/email` on the workspace
host, under the operator-domain gate. RFC [0038](rfcs/0038-email-delivery.md) records why it lives
in `servers/control/`. This is the operator's copy: where each part is, and what to prove before the
first send.

## Where it lives

| Part | Home |
|---|---|
| Sending identity, MAIL FROM, DKIM, contact list | `infra/envs/edge/email.tf` — shared, one AWS account |
| Configuration set, SNS topic, feedback queue, IAM | `infra/modules/platform/ses.tf` — one set per environment |
| Ledger, state machine, audience | `servers/control/src/campaign.rs` |
| The sender | `servers/control/src/campaign_send.rs` |
| The delivery-event consumer | `servers/control/src/campaign_feedback.rs` |
| The HUD | `servers/control/src/hud.rs`, `email_hud.html` |
| Recipient candidates | `core/src/ufo/onboard/onboard_control.py` → `/internal/onboard/recipients` |

`UFO_FOUNDER_SENDERS` decides whether any of it runs. Unset — a self-hosted install, a local
stack — the workers do not spawn and `/surface/email` is not routed.

## Who a campaign can be sent from

The Compose view's From list is `local.founder_senders` in `infra/modules/platform/ses.tf`, and the
IAM condition names the same addresses, so a send from anything else is refused twice — once by the
ledger, once by SES. Today the list is:

| From | Replies reach |
|---|---|
| `ufo founders <founders@ufo.ai>` | `founders@ufo.ai` |
| `Marshall at ufo <marshall@ufo.ai>` | `marshall@ufo.ai` |
| `Alex at ufo <alex@ufo.ai>` | `alex@ufo.ai` |
| `ufo support <support@ufo.ai>` | `support@ufo.ai` |

Reply-To is always the From address: a reply to one person reaches that person, not a shared alias.
The campaign freezes its sender at creation, so editing the list later never re-points a campaign
that already went out — and a campaign whose sender has since left the list refuses to send rather
than substituting another.

## What must stay true of ufo.ai DNS

Terraform owns the DKIM CNAMEs and the `bounce.ufo.ai` MX and SPF, and nothing else in the zone.
These four records are outside it and carry the mailbox replies arrive in:

- the apex MX routes to Google;
- the apex SPF authorizes Google;
- DMARC is `p=none`, reporting to Cloudflare;
- WorkOS's SendGrid records sit under `mail.ufo.ai`.

Every address in the From list is a Google mailbox or alias. Terraform cannot create one. Each must
exist and be read before a campaign goes out from it, or its replies are lost.

## Before the first campaign

The production roll runs `.github/scripts/founder_email_prerequisites.sh`, which fails unless
`ufo.ai` is verified, DKIM is `SUCCESS`, the custom MAIL FROM is `bounce.ufo.ai` and `SUCCESS`, the
configuration set is sending, and the `founder-updates` topic exists. `production_prerequisites.sh`
already fails unless the account holds SES production access with sending enabled.

What the gate cannot check, an operator does once:

1. `terraform fmt`, `terraform validate`, and a scoped plan of `infra/envs/edge` and both
   environments.
2. `dig ufo.ai MX` and `dig ufo.ai TXT` still name Google.
3. Send to the SES simulator addresses — `success@simulator.amazonses.com`,
   `bounce@simulator.amazonses.com`, `complaint@simulator.amazonses.com` — as a campaign whose
   audience is `operators`, and read the Results view for delivered, bounced, and complained.
4. `aws sesv2 create-contact` then `aws sesv2 delete-contact` against `ufo-users`, to prove the
   topic accepts a subscription and the Preview's exclusion count moves.
5. Send a test to both founders from the Compose view, reply to it, and confirm the reply lands in
   the shared mailbox.
6. Prepare a small campaign, approve the exact revision and count the Preview names, and send it.
7. Read Results: every SES event must land on the recipient row it names.
8. Send the whole campaign only after reviewing the Preview's count and exclusions.

## Landing the grant

`deploy_change_gate.py` reads any added line inside an existing block of
`infra/modules/platform/ses.tf` as an authorization contraction, and refuses a deploy whose span also
holds a runtime path. The campaign grants are additive, but they are statements inside the policy
document that already exists, and no arrangement avoids it: handing a live IRSA role another policy
edits either that document or the module's `role_policy_arns`, and the gate reads both as narrowing.
Since `main` carries runtime commits continuously, any span from the last production deployment
holds one.

So the grant lands against a production roll rather than beside one:

1. Roll production.
2. Merge the grant, and roll production again before anything else lands.
3. Merge the runtime, and roll production again.

Steps 2 and 3 each race every other merge — a commit under `core/src/`, `extensions/`,
`servers/control/`, `packs/` or `sandbox/` in either window refuses the roll, and the remedy is to
roll again once the span is clear.

## Retiring the flyingobject.ai identity

The transactional sender still sends from `no-reply@flyingobject.ai`, and that identity is still
declared in `infra/modules/platform/ses.tf`. It cannot be retired here. SES verifies a domain once
per account, so destroying it un-verifies it for every environment at once — and a merge to main
applies testing while production keeps the release it was last rolled to, which would stop
production sign-in and invitation email until someone dispatched a deploy.

The order is therefore: move `ses_sender` to `no-reply@ufo.ai`, roll **production**, confirm its
pods carry the new value, and only then drop `aws_sesv2_email_identity.onboard`,
`infra/envs/testing/ses_dns.tf`, and their `retired_resources.json` entries in a change of their
own. Until that lands, `ufo.ai` and `flyingobject.ai` are both verified and both send.

## Sending

The HTTP request records the act; the worker sends. One recipient per message, because SES adds the
`List-Unsubscribe` header only to a single-recipient send. The body carries
`{{amazonSESUnsubscribeUrl}}`, which SES replaces with the one-click unsubscribe URL for that
contact and topic; without it the header ships and no visible link does.

A throttle returns the row to the queue. Every other SES refusal is terminal, and so is a send whose
outcome the ledger never recorded: nothing here repeats a message it cannot prove was not sent.

## Reading what happened

`ufo_control.email_event` holds every SES event verbatim, keyed by
`(ses_message_id, event_type, occurred_at)`. `ufo_control.email_recipient.delivery` holds the last
thing SES said about each address; a suppressed state — bounced, complained, unsubscribed — is
never overwritten by a later delivery, and bars the address from every later campaign.

A message the consumer cannot decode stays on the queue and parks in
`ufo-<env>-founder-email-feedback-dead` after five receives. Read it there rather than assuming the
event never came.
