---
title: Slack
description: Connect Slack, start work in a channel or direct message, and understand thread and memory behavior.
---

A workspace admin connects Slack. Ask the agent for the install control:

> Connect this workspace to Slack.

Complete the Slack approval. If Slack says the app is not verified, you can continue. The app is
publicly installable but is not listed in the Slack directory.

## Work in a channel

Invite ufo to the channel, then mention `@ufo` with a request. It replies in a thread.

After the first mention, every reply in that thread reaches the agent. It answers messages that ask
it to do something. A conversation between people in the thread does not require an agent reply.

## Work in a direct message

Send a direct message to start a private conversation. The agent always reads direct messages.

## New members

The first Slack message resolves the member from the email address that Slack confirms. A teammate
on the workspace's email domain joins from that message. A person on another domain must be added to
the workspace first.

## Shared channels

A channel shared with another organization has its own memory boundary. Workspace memory does not
enter the channel, and facts from the channel do not enter workspace memory. Your private memory is
still available to you there.

## Running work

Slack cannot stop a running turn. Work that starts in Slack runs to completion. Open the web portal
when you need a stop control or a larger view of the result.

If an install link fails, ask the agent for a new one. Install links are short-lived.
