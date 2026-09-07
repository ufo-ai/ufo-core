# Public SDKs, protocols, generated types, and shared contracts  `stage-19` (cross-cutting infrastructure)

This stage is shared behind-the-scenes support. It is not the startup path or the main work loop. Its job is to define the public “agreements” that let extensions, generated clients, and messaging backends talk to the core system without reaching into private internals.

The runtime extension contracts define the safe context, shared records, object names, and conversation slots that extensions use while work is running. The extension authoring SDK and the SDK facade modules are the clean front doors: they re-export approved tools for skills, jobs, connectors, sources, browser access, models, search, memory, billing, identity, credentials, permissions, and visibility. Small utility modules add shared helpers for callbacks, HTTP cookies, logging, feature flags, scheduled runs, and untrusted text.

The iMessage protocol area supplies the same kind of stable boundary for messaging. Package marker files make generated modules importable. Google API protobuf support provides common annotation types. The generated iMessage protobuf files define chats, messages, attachments, events, groups, polls, and streaming heartbeats. The provider contract and gRPC stubs then give adapters and remote services a consistent plug shape.

## Sub-stages

- [Core runtime extension and shared record contracts](stage-19.1.md) `stage-19.1` — 5 files
- [Extension authoring SDK contracts](stage-19.2.md) `stage-19.2` — 8 files
- [SDK service, content, and runtime resource facades](stage-19.3.md) `stage-19.3` — 11 files
- [SDK access, identity, billing, and visibility facades](stage-19.4.md) `stage-19.4` — 11 files
- [Public SDK package utilities and lightweight helpers](stage-19.5.md) `stage-19.5` — 8 files
- [iMessage protocol package markers and Google API support](stage-19.6.md) `stage-19.6` — 8 files
- [iMessage v1 generated protobuf message contracts](stage-19.7.md) `stage-19.7` — 11 files
- [iMessage provider contract and generated gRPC stubs](stage-19.8.md) `stage-19.8` — 5 files

## 📊 State Registers Touched

- `reg-feature-flags` — The shared on/off switches that let the service enable or disable behavior at runtime.
- `reg-extension-registry` — The discovered set of installed extensions and the capabilities each extension contributes.
- `reg-tool-catalog` — The shared list of tools and actions agents may ask to run, including extension tools.
- `reg-surface-routing-state` — The saved routing state for web, Slack, iMessage, terminal, and other public conversation surfaces.
- `reg-source-index` — The stored external sources, synced pages, permissions, indexing status, and retry/backoff state.
- `reg-memory-store` — The remembered facts and searchable memory chunks that can be retrieved or condensed later.
- `reg-artifact-blob-store` — The shared files, media blobs, previews, metadata, and signed-download records created by agent work.
- `reg-workspace-change-log` — Durable per-conversation sandbox file-change snapshots and summaries used after tool execution and shown in workspace-change slots.
