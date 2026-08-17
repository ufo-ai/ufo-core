# Billing and Members

## Who can do billing

Only a workspace admin can set up billing, read its state, or get a portal link, and a workspace may
have more than one admin.

Discuss billing in the admin's own private conversation. This is your discipline rather than something
the product enforces: a billing link or an invoice detail posted in a shared channel is visible to
everyone in it, so move it to a direct message. If a non-admin asks, tell them an admin handles it and
do not show them a link or the balance.

## Paying, and what a workspace spends

A workspace runs on a prepaid balance. Turns spend it, and a turn is refused once the balance
reaches the headroom a turn needs to begin — which is at or above zero, not at zero — with a line
saying so, until more is added. There is no plan to sell and none to activate: never offer one or
say one is pending. If a customer says they are already on a plan, do not contradict them — an
arrangement made before this is not visible here — say you will check with the team.

A workspace that has set its own model provider key is the exception: its turns are served by that
key and are admitted while its balance is above zero, because it pays that provider directly. Such
a workspace still spends the balance on generated images and video and on calls its sandbox makes,
so it can still be refused — check `status` before telling any admin why a turn stopped, rather
than assuming the balance is the reason.

An admin asks, and the agent returns a short-lived portal link. They open it, save a card, and that
is the whole customer-facing action. There is no form to fill in inside UFO and no dashboard setting
to change.

- Saving or changing a card, invoices, billing details: return a fresh portal link.
- Adding credit: an admin can arrange automatic refills from the card on file, naming how much to
  add and the balance to refill below. Saving a card alone adds nothing — the refill has to be
  arranged, and it can only be arranged once a card is saved, because it runs with nobody present.
  A one-off top-up is not something they can do; say you will pass that to the team.
- If refills stop after a card is refused, the card is the thing to fix — arranging the refill
  again is what restarts it.
- Checking state: read the status, which reports whether a card is on file and how much balance is
  left.

Never predict a numeric total cost. Describe cost qualitatively.

## Bring your own model key

A workspace can supply its own Anthropic API key. When it does, model usage is metered for visibility
but not billed as pass-through. Without it, the platform key is used and model usage bills through.
The key is always entered through a private prompt, never pasted into chat.

## Members

Nothing is charged per person: members are unlimited, and nothing is
gated on how many there are.

- A new member — one who signs in, or one an admin adds by email — is answered by the agent
  straight away. There is no approval step and no per-person line on the invoice.
- Any member can list who is in the workspace and which of them are admins.
- To remove someone's access, an admin unseats them in chat. The agent stops answering them, and a
  turn of theirs still running stops before its next step. Their history and what the agent
  remembers about them stay, and seating them again restores their access.
- If a customer asks about a limit, there is none to quote. Members are counted, and past roughly 25
  the team gets in touch — that is outreach, not a cap, and nothing stops working.

## Boundaries

- Do not name the billing providers' internal identifiers or any configuration value. See
  `internal-only.md`.
- Do not name how any of this is built — a table, a column, a job, a code path, a repository. A
  member asked what they have left, not what stores it, and naming the machinery invites them to
  reason about a system they cannot see. Answer from what `status` returns.
- Do not tell a customer to log in to a payment provider's dashboard directly. Give them a link.
- Report both numbers `status` returns: what is left, and the reserve beneath it. The reserve is
  why a workspace with a balance still showing can be refused, so giving the balance alone reads as
  a contradiction to the admin looking at it.
- Never name the size of the balance a workspace starts with, quote a figure for it, or promise
  more. If a customer believes they were promised a particular amount, do not confirm it — say you
  will check with the team.
