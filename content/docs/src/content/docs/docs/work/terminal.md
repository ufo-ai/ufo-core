---
title: Terminal
description: Install the terminal client, start a conversation in a local directory, and stop a running turn.
---

The terminal client gives your UFO access to the directory where you start it. Use it for local
code, files, and command-line work.

## Install

Any workspace member can install the client:

```sh frame="code"
curl -fsSL https://ufo.ai/ufo | sh
```

Sign in. Then start ufo from the directory that contains the work.

## Choose the directory

The current directory is part of the request. Start the client from the repository or folder that
Your UFO must use. State the required result and limits in your first message.

For example:

> Find why this test fails, fix the cause, and run the focused test. Do not commit the change.

## Stop a running turn

Press **Esc**. The turn ends and does not resume. If you send another message before the turn stops,
that message starts a new turn.

Use the web portal when you want to find conversations from other surfaces or inspect workspace
tasks and artifacts.
