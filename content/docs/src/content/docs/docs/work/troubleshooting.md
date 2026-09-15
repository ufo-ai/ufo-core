---
title: Troubleshooting work
description: Diagnose a failed or incomplete result from the conversation that produced it.
---

Continue in the affected conversation. Give the failed action, time, system, and visible error. Do
not send credentials or secrets.

## Your UFO cannot reach a service

Ask it to check the current connection. Confirm that the connected account can perform the same
action in the provider. Reconnect only after your UFO identifies an expired or removed grant.

## The result uses the wrong data

Confirm the environment, account, workspace, database, time zone, and date range. Name the source
of truth when two systems disagree.

## The work stopped

Read the last reported blocker. Supply missing authority or context in the same conversation. A
message can wait when the workspace balance is below the reserve for a new turn.

## A scheduled task sent nothing

Open **Tasks**, then **Runs**. A run can complete without a message when it finds nothing to report.
Check its conversation, task state, connections, destination, and balance.

## Your UFO made the wrong change

State the observed result and the expected behavior. Ask it to inspect the current external state
before it attempts a correction. A second blind action can make the problem larger.

For workspace access, sign-in, Slack, or billing problems, see
[Workspace troubleshooting](/docs/workspace/troubleshooting/).
