# Generated and wire protocol definitions  `stage-20.2` (cross-cutting infrastructure)

This stage is shared behind-the-scenes support. It defines the fixed “language” that separate parts of the system use to talk to each other, especially across process or network boundaries. Most of it is generated from Protocol Buffers, a format that describes messages in a strict, reusable way so both sides agree on the shape and names of the data.

The protocol scaffolding files provide the foundation. They mark Python packages so generated code can be imported, define Google-style HTTP annotations, and describe sandbox bridge messages for requests such as running a command or reading a file in an isolated work area.

The iMessage service API files define the remote-call contracts. Their message files describe requests and replies, while their gRPC bindings provide client and server wiring for attachments, chats, events, and messages.

The iMessage domain type files define the actual iMessage nouns: addresses, attachments, chats, groups, messages, polls, and streaming heartbeats. Together, these pieces act like a shared dictionary and plug shape for the rest of the system.

## Sub-stages

- [Protocol scaffolding and shared support schemas](stage-20.2.1.md) `stage-20.2.1` — 9 files
- [iMessage generated service APIs and gRPC bindings](stage-20.2.2.md) `stage-20.2.2` — 8 files
- [iMessage generated domain message types](stage-20.2.3.md) `stage-20.2.3` — 7 files
