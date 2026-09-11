---
title: Connecting Sentry
description: Connect Sentry to inspect errors, issues, events, and releases.
---

## Connect Sentry

Ask the agent:

> Connect my Sentry account.

Open the private authorization control and approve the organization and projects needed for the
work. If Sentry is not available in the current connector catalog, connect its supported MCP server
through [MCP](/connectors/mcp/).

## What ufo can do

Available actions depend on the connected Sentry account. Typical incident work includes:

- Find issues and error events.
- Read stack traces, tags, affected releases, and project details.
- Compare an error with source changes and deployments.
- Prepare an incident report or create follow-up work.

State the organization, project, environment, time range, and whether changes are allowed.

> Investigate new production errors in `checkout-api` since the last release. Link each finding to
> source evidence. Do not resolve or assign issues.

## Fix Sentry access

Confirm that the connected account can open the organization and project. Reconnect if the grant
expired. Give exact project and environment names when the organization has several similar
projects.

See [Investigating incidents](/work/investigating-incidents/).
