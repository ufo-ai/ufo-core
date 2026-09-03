# SDK re-export surface for extension authors  `stage-20.1` (cross-cutting infrastructure)

This stage is the public shelf of the SDK for extension authors. It is not the startup path, main work loop, or shutdown path. Instead, it is shared support that gives outside code stable import points, so extensions can use approved tools without reaching into private internal files.

Its parts cover the main areas an extension may need. The web, browser, terminal, and surface seams expose safe ways to connect to browsers, pages, terminals, operator sessions, and user-facing surfaces. The agent context, models, tools, and prompt-safety exports provide the shapes and helpers for conversations, model calls, tool actions, permissions, audiences, and unsafe outside text. The credentials, grants, subjects, and seats exports cover identity, access checks, stored credentials, and visibility rules. The package root and operational exports provide common accounting, balance, feature flag, and observability tools. The content, connector, search, and object exports support outside data, indexing, search, and stored objects. The manifest, jobs, hub, and sandbox exports let extensions describe themselves, schedule background work, and use controlled runtime features.

## Sub-stages

- [SDK web, browser, terminal, and surface seams](stage-20.1.1.md) `stage-20.1.1` — 7 files
- [SDK agent context, models, tools, and prompt-safety exports](stage-20.1.2.md) `stage-20.1.2` — 8 files
- [SDK credentials, grants, subjects, and seat access exports](stage-20.1.3.md) `stage-20.1.3` — 6 files
- [SDK package root and cross-cutting operational exports](stage-20.1.4.md) `stage-20.1.4` — 5 files
- [SDK content, connector, search, and object exports](stage-20.1.5.md) `stage-20.1.5` — 7 files
- [SDK extension manifest, jobs, hub, and sandbox exports](stage-20.1.6.md) `stage-20.1.6` — 5 files
