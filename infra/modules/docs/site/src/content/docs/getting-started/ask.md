---
title: Ask for work
description: Give the agent enough context to complete a task and verify the result.
---

Write to the agent as you would write to a capable teammate. State the outcome. Add the context and
limits that change how the work must be done.

## A useful request

A useful request usually has four parts:

1. **Outcome.** State what must be true when the work is complete.
2. **Context.** Name the repository, document, customer, date range, or system to inspect.
3. **Limits.** State what the agent must not change or publish.
4. **Proof.** State how to verify the result.

For example:

> Find why workspace invitations fail for addresses outside the company domain. Use the testing
> environment. Fix the cause, add the test that would have caught it, and open a draft pull request.
> Do not deploy or merge.

You do not need to know the steps. Ask for the outcome and let the agent inspect the systems first.

## Give it the source material

Attach a file, paste a link, name a connected record, or point to a synced source. State which source
is authoritative when two sources can disagree.

If access is missing, ask the agent to connect the service. Use the connection or credential control
that it returns. Do not paste secrets into the conversation.

## Set decision boundaries

State which actions the agent may take. This is important for work that can affect customers,
production, money, or another person.

> Diagnose the production alert and give me the evidence. Do not change production.

> Update the draft in Notion. Do not send it to the customer.

## Ask for evidence

Name the proof you need. Examples include a test result, a browser check, a fresh query, a source
link, or the exact state after a change.

Use the same conversation to refine the result:

> Make the report shorter. Keep the table and remove the forecast.

> Check the pull request again after CI finishes. Fix only failures caused by this branch.

See [Recipes](/recipes/) for complete requests you can adapt.
