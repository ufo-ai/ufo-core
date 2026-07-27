# Browser and computer-use sessions  `stage-12.1`

This stage is the browser-use part of a turn. It lets the agent use Chrome much like a person would: open pages, inspect what is visible, click, type, upload files, download files, and then clean up when the turn ends. It is both part of the main work loop and shared support behind the scenes.

First, the endpoint providers supply a Chrome to control, either from Browserbase, a hosted browser service, or from a sandboxed local Chrome. They hide where the browser lives. Next, the CDP transport is the control wire to Chrome. CDP, or Chrome DevTools Protocol, is Chrome’s remote-control language. It sends commands, receives events, runs small page scripts, handles pop-up dialogs, and waits until pages settle.

On top of that, session orchestration and agent tools keep one shared browser session for the turn and expose safe actions to the agent. Page inspection is the system’s eyes: it reads the page, builds a simpler map of text and controls, and connects model-chosen elements back to real screen locations. Finally, the action modules are the hands: tabs, clicks, typing, forms, uploads, downloads, scrolling, and screenshots.

## Sub-stages

- [Browser endpoint providers and package shells](stage-12.1.1.md) `stage-12.1.1` — 5 files
- [CDP transport, runtime, and browser settling](stage-12.1.2.md) `stage-12.1.2` — 5 files
- [Browser session orchestration and agent tools](stage-12.1.3.md) `stage-12.1.3` — 6 files
- [Page inspection and element mapping](stage-12.1.4.md) `stage-12.1.4` — 4 files
- [Browser actions, input, tabs, forms, and downloads](stage-12.1.5.md) `stage-12.1.5` — 5 files
