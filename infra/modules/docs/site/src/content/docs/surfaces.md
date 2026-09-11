---
title: Where you talk to the agent
description: The web portal, Slack, the terminal client, and iMessage — what each one answers and how to stop a running turn.
---

## The web portal

Web sign-in opens the portal, where you chat with the workspace's main agent in the browser.

An admin reaches every agent. Every other member reaches the agents open to everyone in the
workspace, the agents they created themselves, and any agent shared with them. Sharing is said in
that agent's own chat — `let email@work.com reach this agent on the web` — and revoked the same
way.

## Slack

A workspace admin installs ufo into Slack and the agent works in your channels and DMs. Slack warns
that the app is not verified by Slack: the app is publicly installable but not listed in Slack's
directory, and it is safe to proceed.

- A direct message is always answered.
- In a channel, the agent answers when it is @-mentioned. Once a mention starts a thread, every
  reply in that thread reaches it, mentioned or not.
- A channel shared with an outside organization is sealed. It reads and writes only itself.

## The terminal client

Any member installs the terminal client:

```sh frame="code"
curl -fsSL https://ufo.ai/ufo | sh
```

A conversation opened from a connected terminal works in your own current directory.

## iMessage

Any member connects their own phone to iMessage once an admin has made the workspace's first
iMessage connection.

## Stopping a running turn

The portal's stop button and the terminal client's Esc key both end a running turn for good. It
does not resume, and a message sent before stopping starts a new turn.

Slack has no way to stop a running turn. Work started there runs to completion.
