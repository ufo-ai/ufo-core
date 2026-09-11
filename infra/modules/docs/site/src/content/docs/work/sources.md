---
title: Synced sources
description: Keep documents and records searchable, choose their scope, and use them in conversations and tasks.
---

A synced source keeps documents or records available to the agent. Use one for material that the
agent must search across many conversations, such as product documentation, customer notes, issues,
or a knowledge repository.

Ask the agent to connect and register the source:

> Connect our product documentation as a shared source. Use the docs repository on GitHub.

## Choose the scope

- A private source is searchable only in your conversations.
- A shared source is searchable by the workspace's main agent.
- An application or private agent needs its own grant to the source.

State the scope when you ask for the source. Do not use a shared source for material that every
workspace member must not see.

## Ask questions from a source

Name the source and the expected evidence:

> From the support source, list the three most common setup problems this month. Link each example
> and state how many times it occurred.

The agent can combine a source with a connected account. For example, it can compare product notes
from a source with open issues from a project tracker.

## Run work when a source changes

A shared source can trigger an application when relevant records change. Use this for work such as
reviewing changed pull requests or triaging new issues. The application records each run as a task
conversation.

See [Scheduled work](/work/tasks/) for how to inspect and control recurring work.
