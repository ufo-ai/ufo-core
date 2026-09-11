---
title: Troubleshooting work
description: Diagnose a failed or incomplete result from the conversation that produced it.
---

Continue in the affected conversation. Give the exact failed action, time, system, and visible
error. Do not send a credential or secret.

## The agent cannot reach a service

Ask it to check the current connection. Confirm that the connected account can perform the same
action in the provider. Reconnect only after the agent identifies an expired or removed grant.

## The result uses the wrong data

Confirm the environment, account, workspace, database, time zone, and date range. Name the source
of truth when two systems disagree.

## The work stopped

Read the last reported blocker. Supply missing authority or context in the same conversation. A
workspace balance can hold a new member message until an admin adds credit.

## A scheduled task sent nothing

Open **Tasks**, then **Runs**. A quiet run can complete without a message. Check its conversation,
task state, connections, destination, and balance.

## The agent made the wrong change

State the observed result and the expected behavior. Ask it to inspect the current external state
before it attempts a correction. A second blind action can make the problem larger.

For workspace access, sign-in, Slack, or billing problems, see
[Workspace troubleshooting](/workspace/troubleshooting/).
