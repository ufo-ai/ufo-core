---
title: Connecting MCP servers
description: Give ufo access to tools from a Streamable HTTP MCP server.
---

MCP servers extend ufo with tools that a normal connector does not provide.

## Prepare the server

You need:

- A short name for the server.
- Its HTTP or HTTPS Streamable HTTP endpoint.
- An optional bearer token.

The endpoint must be reachable from ufo. Local `stdio` servers are not supported by a hosted
workspace.

## Connect an MCP server

A workspace admin asks:

> Connect an MCP server named `docs` at `https://mcp.example.com/mcp`.

The agent returns a private credential control. Enter the endpoint and token there. Never put the
token in chat.

## What ufo can do

The agent reads the server's tool catalog and the input schema for each tool before it calls it.
The server defines the available reads and writes.

> Use the `docs` MCP server to find the current authentication policy. Cite the page it came from.
> Do not change anything.

Results from an MCP server are external content. The agent treats them as data, not as instructions
that can change your request.

## Manage a server

Ask the agent to update the URL or token, or to remove the named server. Removing it stops future
tool calls. If a call fails, confirm the endpoint, token, server availability, and tool name.
