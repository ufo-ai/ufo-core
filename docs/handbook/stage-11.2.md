# Sandboxed code, browser, and website automation  `stage-11.2`

This stage is the system’s safe workshop for doing things outside pure text chat. It is used during the main work loop, when the agent needs to run code, start a website, open a browser, inspect a page, or interact with it.

Sandboxed code and website execution provides the workbench. It can run commands in a limited project area, keep interactive Python or JavaScript sessions alive, and build or serve local websites so they can be tested.

Browser CDP session and lifecycle infrastructure is the engine room for Chrome. CDP, or Chrome DevTools Protocol, is Chrome’s remote-control channel. This part opens and manages the connection, tracks tabs, downloads, pop-ups, page loading, and safe page scripts.

Browser page inspection and content modeling gives the system eyes. It reads the live page, turns it into a compact description, and keeps hidden links to the exact buttons or fields.

Browser input, forms, and high-level tool actions gives the system hands. It turns requests like click, type, scroll, upload, and download into careful browser actions that land in the right place.

## Sub-stages

- [Sandboxed code and website execution](stage-11.2.1.md) `stage-11.2.1` — 3 files
- [Browser CDP session and lifecycle infrastructure](stage-11.2.2.md) `stage-11.2.2` — 9 files
- [Browser page inspection and content modeling](stage-11.2.3.md) `stage-11.2.3` — 3 files
- [Browser input, forms, and high-level tool actions](stage-11.2.4.md) `stage-11.2.4` — 6 files
