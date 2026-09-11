---
title: Build a customer-health application
description: Give customer health its own agent, connected systems, synced sources, triggers, schedule, and homepage.
---

Use an application when one operating loop needs a stable owner and view.

## Define the application

> Build a workspace application named Customer health. It should combine product activity, support
> history, renewal dates, billing state, recent meetings, and open account work. Everyone in the
> workspace can use it. The homepage needs a searchable account list with health state, reason,
> owner, renewal date, last customer contact, open commitments, and source links.

Approve the homepage before the agent creates the application. Check the hierarchy, actions, empty
states, and phone layout.

## Connect and sync its inputs

In the application's own conversation, connect the CRM, product analytics, support, billing, and
calendar accounts. Grant the shared customer-notes source to the application. Test one account and
confirm which source supplies every field.

## Add its loops

Create two separate automations:

> Every Monday at 7:00 AM Pacific time, recalculate every account's health state. Compare it with
> the prior run. Post newly at-risk and materially changed accounts in the customer-success Slack
> channel with the reason, owner, evidence, and next action. Stay quiet when no account changed.
> Do not update the CRM or contact customers.

> Watch the shared support connection's ticket stream in this application. When a priority customer
> opens or materially updates a severe ticket, refresh that account's health state and post only a
> new actionable risk. Keep all changes for one account in one stable conversation.

The schedule provides a complete weekly sweep. The source trigger provides a faster response to
important support changes.

Use **Tasks** to inspect runs, pause the schedule, or remove the trigger. Grant write actions only
after the read-only loop produces reliable results.
