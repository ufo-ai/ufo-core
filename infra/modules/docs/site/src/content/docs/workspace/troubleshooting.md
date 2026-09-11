---
title: Troubleshooting
description: Resolve common sign-in, Slack, connection, memory, task, and billing problems.
---

Ask the agent to check the current state before you reconnect an account or repeat work.

## A teammate cannot sign in

Confirm the exact email address they used and check its spam folder for the sign-in code. A teammate
on the workspace's work domain can join directly. A person on another domain must first be added by
an admin.

If the address is correct and no code arrives, ask the agent to pass the problem to the ufo team.

## The agent does not answer in Slack

Confirm that ufo is in the channel and mention `@ufo` once. After a mention starts a thread, replies
in that thread reach the agent.

For a new member, check the email address on their Slack account. A person outside the workspace's
email domain must be added before the agent can answer.

## A Slack install link fails

Ask the agent for a new link. Install links are short-lived and cannot be reused. If the new link
also fails, ask the agent to raise the problem with the team.

## A connection stopped working

Ask the agent to check the connection, then start a new authorization handoff. Access may have
expired or been removed in the connected service.

For GitHub, each member connects their own account. A teammate's connection does not give another
member access to clone or push a private repository.

## The agent cannot find a remembered fact

Ask where the fact was stated. Private memory, workspace memory, a private room, and an external
shared channel have different scopes. Restate the fact in the scope where it must be available.

## A task did not post

A quiet scheduled run sends no message. Open **Tasks**, then **Runs**, to inspect its conversation.
Check the task state, connected accounts, destination, and workspace balance.

## A running turn must stop

Use the stop button on the web or press **Esc** in the terminal. Slack cannot stop a running turn.

## A message is waiting

Check the workspace balance. A member message is held when there is not enough credit. It runs
automatically after an admin adds credit.

## The problem remains

Ask the agent to pass the symptom, affected account, time, and failed action to the ufo team. Do not
send a credential or secret.
