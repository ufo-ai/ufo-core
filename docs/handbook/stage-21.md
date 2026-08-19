# Protocols, generated types, and serialization glue  `stage-21` (cross-cutting infrastructure)

This stage is shared behind-the-scenes support. It is used whenever the system needs reliable message formats, network service definitions, or importable folders. It is not the startup, main work loop, or shutdown. It is more like the plumbing and labeled shelving that other parts depend on.

The iMessage provider and gRPC service boundary defines the real iMessage contract. The hand-written provider code says what an iMessage provider must offer, while generated Protocol Buffer files define exact message shapes for chats, addresses, attachments, events, and streams. gRPC files add standard network calling code, so clients and servers can talk in the same format.

The Google API annotation files are generated helpers that let the system understand HTTP mapping details attached to API methods. The iMessage proto namespace markers make the nested protocol folders importable.

The remaining parts are many __init__.py package markers. They open the core UFO folders and many extension folders, including integrations, productivity tools, evaluation tools, runtime helpers, and nested script areas. They do not run behavior themselves, but they let Python find the code when needed.

## Sub-stages

- [iMessage provider and gRPC service boundary](stage-21.1.md) `stage-21.1` — 17 files
- [Generated Google API annotation protos](stage-21.2.md) `stage-21.2` — 4 files
- [iMessage proto namespace package markers](stage-21.3.md) `stage-21.3` — 4 files
- [Core UFO package namespace markers](stage-21.4.md) `stage-21.4` — 11 files
- [Integration extension package markers](stage-21.5.md) `stage-21.5` — 9 files
- [Productivity and knowledge extension package markers](stage-21.6.md) `stage-21.6` — 8 files
- [Runtime, evaluation, and internal extension package markers](stage-21.7.md) `stage-21.7` — 7 files
- [Nested extension subpackage markers](stage-21.8.md) `stage-21.8` — 4 files
