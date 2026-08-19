# Chrome DevTools and browser session control  `stage-11.1`

This stage is the system’s browser control center. It is used mostly during the main work loop, when the agent needs to visit websites, read pages, click buttons, type into forms, download files, or take screenshots. It also includes behind-the-scenes support for getting a browser from different places.

The agent-facing tool surface is the simple front door. It lets the rest of the system ask for browser actions without knowing where Chrome is running. The DevTools transport is the communication line to Chrome, using Chrome’s automation channel to send commands and receive events safely. Session orchestration manages the live browser: tabs, page loading, pop-ups, downloads, and knowing when an action has settled.

The page inspection layer turns a real web page into something the agent can understand, including readable text, clickable element references, and screen coordinates. The action toolbox turns the agent’s requests into real mouse, keyboard, scrolling, form, and file-upload operations. Finally, the provider layer can supply different browsers, such as hosted services, remote Browserbase sessions, or Chrome inside an isolated sandbox. Together, these parts let the agent browse the web reliably while hiding most browser complexity.

## Sub-stages

- [Agent-facing browser tool surface and browser acquisition contract](stage-11.1.1.md) `stage-11.1.1` — 3 files
- [Chrome DevTools Protocol transport and runtime primitives](stage-11.1.2.md) `stage-11.1.2` — 3 files
- [Browser session orchestration, tabs, settling, dialogs, and downloads](stage-11.1.3.md) `stage-11.1.3` — 5 files
- [Page inspection, accessibility-tree parsing, and coordinate mapping](stage-11.1.4.md) `stage-11.1.4` — 4 files
- [Browser action vocabulary and input/form execution](stage-11.1.5.md) `stage-11.1.5` — 6 files
- [Hosted and sandboxed browser providers](stage-11.1.6.md) `stage-11.1.6` — 3 files
