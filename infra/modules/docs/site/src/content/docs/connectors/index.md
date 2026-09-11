---
title: Connecting your systems
description: Connect the accounts, services, and data that ufo needs to complete your work.
---

Ask the agent to connect a system:

> Connect my GitHub account so you can review pull requests.

> Connect our project tracker so you can prepare a weekly delivery report.

The agent checks the current catalog and returns the correct private control.

## Personal connections

Most services use the provider's authorization page. Open it from the control in the agent's
reply. A personal connection belongs to the member who approved it. Other members must connect
their own accounts.

## Workspace connections

Slack and services that use an API key are workspace connections. An admin installs them or enters
the required values through a private credential control.

Never put a password, API key, token, or authorization URL in chat. The agent cannot read a secret
that you enter through a credential control.

## Use a connection

Name the service and the result you need. State whether the agent may make changes.

> Review open Linear issues for the next release. Report missing owners and blocked work. Do not
> change any issues.

The agent uses only the access granted by the connected account.

## Manage a connection

Open **Connectors** to see connected accounts. Reconnect an account when access expires or the
provider removes it. Disconnecting an account stops new use and can remove synced data from that
connection.

If a service is not listed here, ask the agent to search the connector catalog. You can also
[connect an MCP server](/connectors/mcp/).
