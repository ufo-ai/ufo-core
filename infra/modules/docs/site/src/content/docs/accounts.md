---
title: Connecting your accounts
description: How the agent reaches your services — account connections, keyed providers, synced sources, and your own model key.
---

## Account connections

The agent reaches your services through connections you approve: Slack, email, calendars,
analytics, databases, CRMs, project trackers, and many more.

Ask the agent to connect one. It starts a private authorization handoff and you approve it through
a connection control. You are never asked to paste an authorization URL. Ask whether a service is
available rather than assuming it is not — the catalog is large.

An agent's access comes from the connection you granted it, never from whoever is speaking to it.

## Providers that take a key

Some services authenticate with a workspace API key rather than an account handoff. The agent
requests one through a private credential prompt that only a workspace admin can fill.

Never send a key, token, or secret in a chat message. A key entered through the prompt cannot be
echoed back, read, or written to a file — the sandbox never holds the real secret.

## Synced sources

Connect a source and its documents and records are synced and searchable, so the agent answers from
your own material.

- A source you register privately is searchable only in your own conversation.
- A source shared to the workspace is searchable by the workspace's main agent, which every member
  reaches.
- An agent you built yourself needs the source granted to it separately, shared or not.

## Your own model key

A workspace can supply its own key for the model provider serving it, entered through the same
private prompt. See [Balance and payment](/billing/) for what that changes about what the workspace
spends.
