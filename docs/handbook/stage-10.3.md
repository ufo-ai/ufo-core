# Browser automation and remote browser sessions  `stage-10.3`

This stage is the system’s browser-driving workshop. It is used during the main work loop whenever an agent needs to use the web, and it also cleans up when that browser-using turn ends. First, the provider layer gives the agent a browser to work with. That browser might be a Chrome running in the project’s sandbox, a hosted Browserbase session, Browser Use’s cloud agent, or the built-in automation stack. All of them expose Chrome DevTools, a remote-control channel for Chrome.

The session core keeps that connection alive, sends commands, receives browser events, and reports clear errors. Around it, lifecycle helpers manage practical browser chores: tabs, page loads, downloads, pop-up dialogs, and deciding when a page is “settled” enough to continue. Page-reading helpers turn the live page into readable text and element descriptions, then map AI-chosen targets back to real screen coordinates. Finally, the action layer defines safe browser verbs like click, type, scroll, wait, screenshot, key press, and upload, cleans them up, and executes them in Chrome.

## Sub-stages

- [Browser providers and agent-facing adapters](stage-10.3.1.md) `stage-10.3.1` — 7 files
- [CDP connection and browser session core](stage-10.3.2.md) `stage-10.3.2` — 6 files
- [Browser lifecycle, tabs, downloads, dialogs, and settling](stage-10.3.3.md) `stage-10.3.3` — 4 files
- [Page reading, element lookup, and coordinate mapping](stage-10.3.4.md) `stage-10.3.4` — 4 files
- [Browser action vocabulary and input execution](stage-10.3.5.md) `stage-10.3.5` — 5 files
