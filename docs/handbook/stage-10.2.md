# Connectors, Credentials, and Provider Tool Brokers  `stage-10.2`

This stage is the system’s bridge to outside services such as GitHub, Slack, iMessage, and many app platforms. It supports setup, when a user connects an account, and the main work loop, when agents use approved tools. Its main job is to keep credentials, meaning private access keys, out of ordinary code paths while still letting agents act on a user’s behalf.

The Composio and Pipedream brokers are like service desks for large catalogs of third-party tools. They create login links, check connections, discover available actions, run them, proxy requests, and handle files returned by those tools. The generic connector layer turns connected accounts and external tools into normal workspace objects, so they can be listed, shared, attached to agents, revoked, or safely called. It also supports MCP servers, which are outside tool providers that publish a standard tool list. The app-specific setup pieces handle direct integrations: verifying GitHub App installations and creating short-lived tokens, guiding Slack setup and search, and connecting iMessage numbers with proper ownership checks.

## Sub-stages

- [Composio Connector Broker and Dynamic Providers](stage-10.2.1.md) `stage-10.2.1` — 7 files
- [Pipedream Connector Broker and Proxy](stage-10.2.2.md) `stage-10.2.2` — 5 files
- [Generic Connector Objects and External Tool Surfaces](stage-10.2.3.md) `stage-10.2.3` — 4 files
- [App-Specific Connect and Credential Setup](stage-10.2.4.md) `stage-10.2.4` — 4 files
