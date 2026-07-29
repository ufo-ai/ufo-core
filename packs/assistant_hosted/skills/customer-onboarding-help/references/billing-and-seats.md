# Billing and Seats

## Who can do billing

Only a workspace admin can set up billing, read its state, or get a portal link, and a workspace may
have more than one admin.

Discuss billing in the admin's own private conversation. This is your discipline rather than something
the product enforces: a billing link or an invoice detail posted in a shared channel is visible to
everyone in it, so move it to a direct message. If a non-admin asks, tell them an admin handles it and
do not show them a link or the plan's state.

## Setting up a payment method

An admin asks, and the agent returns a short-lived link for saving a card. They open it, save the
card, and that is the whole customer-facing action. There is no form to fill in inside UFO and no
dashboard setting to change.

After the card is saved, the plan goes live shortly afterward rather than instantly, because
activation runs on a schedule. Say it will go live shortly and that you will confirm here when it
does. Never claim the plan is active before you have checked its status.

- Saving a card: return the setup link.
- Checking state: read the status, which reports whether a card is on file and whether the plan is
  live.
- Invoices, changing the card, updating billing details: return a fresh portal link.

Never quote a specific credit amount or predict a numeric total cost. Describe cost qualitatively.

## Bring your own model key

A workspace can supply its own Anthropic API key. When it does, model usage is metered for visibility
but not billed as pass-through. Without it, the platform key is used and model usage bills through.
The key is always entered through a private prompt, never pasted into chat. Supplying a key does not
change seat billing.

## Seats

A seat is what lets a person talk to the agent.

- A new member who joins is seated automatically while an included seat is open.
- Beyond the included allowance, a new member is not seated: their messages are refused and a
  workspace admin gets a seat approval request.
- If an admin approves, the extra seat bills as overage on the invoice. Always say that when an admin
  approves one.
- There is a hard cap on seats. When it is reached, no more seats can be granted from chat and the
  team has to be involved.
- Only an admin can grant or revoke seats. An admin can also list who currently holds one.

To an admin approving one: "Approving this adds a seat beyond your included allowance, and it bills as
overage."

## Boundaries

- Do not name the billing providers' internal identifiers, the package alias, or any configuration
  value. See `internal-only.md`.
- Do not tell a customer to log in to a payment provider's dashboard directly. Give them a link.
- There is no automatic credit grant on signup. Never promise a customer free credit, and if one
  believes they were promised some, do not confirm it — say you will check with the team.
