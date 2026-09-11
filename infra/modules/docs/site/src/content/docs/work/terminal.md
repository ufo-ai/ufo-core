---
title: Terminal
description: Install the terminal client, start a conversation in a local directory, and stop a running turn.
---

The terminal client gives the agent access to the directory where you start the conversation. Use
it for local code, files, and command-line work.

## Install

Any workspace member can install the client:

```sh frame="code"
curl -fsSL https://ufo.ai/ufo | sh
```

Follow the sign-in step, then start ufo from the directory that contains the work.

## Choose the directory

The current directory is part of the request. Start the client from the repository or folder you
want the agent to use. Name the exact outcome and any limits in your first message.

For example:

> Find why this test fails, fix the cause, and run the focused test. Do not commit the change.

## Stop a running turn

Press **Esc**. The turn ends and does not resume. A message already sent before the stop starts a
new turn.

Use the web portal when you want to find conversations from other surfaces or inspect workspace
tasks and artifacts.
