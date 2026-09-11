---
title: Balance and payment
description: The prepaid balance a workspace runs on, what happens when it runs out, and how a card and automatic refills are set up.
---

A workspace runs on a prepaid balance. Turns spend it. Only a workspace admin sets up billing,
reads its state, or gets a payment link.

## When the balance runs out

A turn is refused once the balance reaches the headroom a turn needs to begin — which is at or
above zero, not at zero. The reserve beneath the balance is why a workspace with a balance still
showing can be refused.

A message you send is held rather than refused, whether it opens a conversation or continues one.
It is answered once credit is added, and you do not have to send it again.

A scheduled task's run is refused outright instead of held, and fires again on its next schedule.

A workspace whose card has already paid a refill keeps working for a fixed amount past that line, so
a refill still being charged does not stop it.

## The card

A card is entered at the payment provider, through a short-lived link. There is no form inside ufo
for a card, an invoice, or any other billing detail. Ask the agent for the link, or use the button
on the billing screen, which returns the same one — to save a first card or to change the one on
file. Invoices are read at the provider through that link.

## Automatic refills

The billing screen turns automatic refills on or off, at any whole-dollar amount and any balance to
refill below. Asking the agent arranges the same rule.

Saving a card alone adds nothing. A refill has to be arranged either way, and it can only be
arranged once a card is saved, because it runs with nobody present.

The screen names the card it will charge by brand and last four digits. If refills stop after a card
is refused, the card is the thing to fix — arranging the refill again is what restarts it.

## Asking where it stands

Ask the agent for status. It reports whether a card is on file, how much balance is left, and the
reserve beneath it.

## A workspace with its own model key

A workspace that has set its own model provider key pays that provider directly, and its turns are
admitted while its balance is above zero. It still spends the balance on generated images and video
and on the calls its sandbox makes, so it can still be refused.
