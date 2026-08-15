# Cross-cutting SDK, protocols, contracts, and type vocabulary  `stage-22` (cross-cutting infrastructure)

This stage is shared behind-the-scenes support. It is the system’s common dictionary and rulebook, so the core program, extensions, models, browsers, sources, and accounting code all describe the same things in the same way.

The core contracts define the real shapes used inside UFO: what an extension is allowed to see, what it promises in its manifest, how conversation side panels are described, how AI model requests and streamed replies look, and how a conversation “turn” is recorded.

The public SDK facades are stable front doors for extension authors. One group exposes extension building blocks such as tools, jobs, skills, HTTP routes, sandbox behavior, logs, and metrics. Another group exposes connectors, credentials, grants, bearer-token checks, browser links, terminal transport, and operator sessions. A third group exposes model, search, memory, indexing, and accounting vocabulary. The last group exposes workspace and user-facing concepts such as audiences, seats, hubs, listings, objects, scheduled actions, surfaces, and untrusted text.

Together, these parts let outside extensions and internal code plug into UFO without depending on fragile private details.

## Sub-stages

- [Core extension, model, object, and turn contracts](stage-22.1.md) `stage-22.1` — 6 files
- [Public SDK extension authoring and runtime facades](stage-22.2.md) `stage-22.2` — 10 files
- [Public SDK connector, credential, and transport facades](stage-22.3.md) `stage-22.3` — 9 files
- [Public SDK model, retrieval, memory, and accounting facades](stage-22.4.md) `stage-22.4` — 5 files
- [Public SDK workspace, surface, visibility, and object facades](stage-22.5.md) `stage-22.5` — 10 files

## 📊 State Registers Touched

- `reg-object-catalog` — The shared catalog of manageable workspace object types and the rules for who may view or change them.
- `reg-conversation-slots` — Side-panel data shown beside a conversation, such as tasks, sources, sites, automations, and workspace changes.
