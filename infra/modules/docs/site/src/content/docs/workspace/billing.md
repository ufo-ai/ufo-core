---
title: Balance and billing
description: Understand the prepaid balance, card portal, automatic refills, and billing access.
---

A workspace runs on a prepaid balance. Work spends the balance. Members are unlimited and are not
billed by seat.

Only an admin can read billing state, get a billing portal link, or change a refill rule. Handle
billing in a private conversation.

## Read the current state

Ask the agent:

> Show the workspace balance, reserve, card status, and automatic refill rule.

The reserve is the amount needed before a new turn can start. A turn can stop when the displayed
balance is still above zero because the remaining balance is below that reserve.

## Save or change a card

Ask for a fresh billing portal link, or use the button on the billing page. Card details, invoices,
and billing information are handled through that short-lived link. ufo does not show a card form.

Saving a card does not add credit. Configure an automatic refill after the card is saved.

## Automatic refills

Set a whole-dollar refill amount and the balance that triggers it. Use the billing page or ask the
agent:

> Refill $100 when the balance falls below $25.

You can change or disable the rule later. If a card is refused, fix the card and arrange the refill
again to restart it.

A one-time credit purchase is not available. A refill rule remains active until an admin disables
it.

## When credit runs out

A member message is held. It runs automatically after an admin adds credit, so the member does not
need to send it again.

A scheduled run is refused. The task runs again at its next scheduled time.

## Your own model provider key

A workspace can enter its own model provider key through a private credential control. It pays that
provider directly for model use. Generated media and calls made by the agent's working environment
can still spend the ufo balance.
