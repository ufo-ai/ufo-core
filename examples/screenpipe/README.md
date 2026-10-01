# Screenpipe search

Connect [Screenpipe](https://github.com/screenpipe/screenpipe) recordings to UFO's existing
MCP extension. This example exposes `search-content` through FastMCP, already a UFO dependency.
It uses the published `screenpipe-mcp@0.20.0` stdio server, which authenticates to Screenpipe's
API. It exposes no recording controls, computer controls, or write tools.

## Scope

Run `ufoctl serve`, this bridge, and Screenpipe on the same trusted computer. The bridge listens
only on `127.0.0.1:3031`; localhost addresses the machine running `ufoctl serve`, even when the
terminal client runs somewhere else. This setup does not connect a laptop to hosted UFO.

An MCP server in UFO belongs to the workspace, not to an individual member. Connect only
recordings intended for that workspace. Search results enter UFO's conversation and model
context; keeping the recording database local does not keep retrieved text local. Every local
process that can reach port 3031 can also search through this bridge. Do not expose the port or
forward it to an untrusted network.

## Start the bridge

Install UFO's dependencies with `uv sync`. Install Node.js 18 or newer with `npx`, and run
Screenpipe with its local API enabled. From this repository root:

```bash
SCREENPIPE_LOCAL_API_KEY="$(screenpipe auth token)" uv run python examples/screenpipe/bridge.py
```

Keep the process running. The first search downloads the pinned MCP package through `npx`.
The API key reaches only the child Screenpipe MCP process; it is not a UFO credential, a chat
message, or an HTTP client token. For a Screenpipe instance on another local port, also set
`SCREENPIPE_LOCAL_API_URL`, for example `http://127.0.0.1:3035`.

## Connect in chat

The default `assistant` pack includes the MCP extension. As a workspace administrator, ask UFO:

> Add an MCP server named screenpipe at http://127.0.0.1:3031/mcp. List its tools and show the
> input schema for search-content.

UFO registers an `mcp_server` object and uses `list_mcp_tools` to discover its schema. There is
no bearer token to enter for this loopback bridge. Then ask for a bounded retrieval:

> Search Screenpipe audio transcripts for pricing between 2026-09-27T09:00:00-07:00 and
> 2026-09-27T10:00:00-07:00. Return at most five results with their timestamps.

Choose a time range in your own recordings. The MCP tool name is `search-content`, including
the hyphen. It accepts filters such as `q`, `content_type`, `start_time`, `end_time`, and `limit`.
Read the discovered schema before calling it. Results reflect what Screenpipe recorded;
missing results do not establish that an event did not happen.

## Verify and disconnect

The catalog must contain only `search-content`. A query for a known recorded phrase should
return timestamped results. An empty result can mean the phrase or time range did not match.
An API authentication error means the key was rejected; obtain it again with
`screenpipe auth token` and restart the bridge. A connection error means one of the two local
servers is stopped or its port differs from the configured URL.

To disconnect, ask UFO to remove the `screenpipe` MCP server, then stop the bridge with Ctrl-C.
