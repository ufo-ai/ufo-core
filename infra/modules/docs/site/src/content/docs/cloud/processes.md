---
title: Processes
description: Understand the commands and local services that run in a cloud work session.
---

For code, data, and file work, the agent uses an isolated computer attached to the conversation. It
can read and write files, install project dependencies, run tests, and start local services.

## Commands

The agent runs commands in the conversation workspace. Command output is evidence, but a successful
command does not prove the user-facing result.

Ask for the focused checks that match the work:

> Run the unit test for the changed module, build the web application, and inspect the page in a
> browser at desktop and phone widths.

## Local services

The agent can start a local web server to inspect an application. A local address is not a result
you can open. Ask for a [hosted site](/work/files-sites/) when other people need access.

## Files and persistence

The workspace belongs to the conversation. Continue in that conversation when later work needs its
files and state. Share important output in the conversation.

Do not use the work session as production hosting, durable storage, or a system of record. A
deployed service, shared file, connected system, or hosted site must hold the final result.
