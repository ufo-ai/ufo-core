# Founder email

A founder campaign is written, prepared, approved, and sent from `/surface/email` on the workspace
host, under the operator-domain gate. RFC [0038](rfcs/0038-email-delivery.md) records why it lives
in `servers/control/`. This is the operator's copy: where each part is, and what holds it together.

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
ledger, once by SES. The list is:

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
