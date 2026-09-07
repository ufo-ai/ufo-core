# Browser and computer-use automation  `stage-12`

This stage is the system’s browser-and-computer-use workshop. It is shared support that the main agent calls when it needs to use the web: start or attach to Chrome, visit pages, understand what is on screen, click, type, move files, and clean up afterward.

The browser extension surface is the front door. It exposes simple tools and a browser-focused helper agent for web tasks. The provider and launch adapters decide where the browser actually runs, such as local Chrome, sandbox Chrome, or a hosted cloud browser, while hiding those differences from the rest of the system.

Inside Chrome, the BUA control-room code talks to Chrome’s remote-control interface, checks messages, runs page JavaScript, handles pop-ups, and preserves useful error details. The session and tab lifecycle code keeps one orderly browser workbench per turn, tracks tabs, waits for pages to settle, and closes things safely. The page understanding code turns messy web pages into readable text, element lists, and click targets. The action layer then performs clicks, typing, scrolling, uploads, downloads, and form filling. The package marker file simply makes the BUA code importable.

## Sub-stages

- [Browser extension tool and subagent surface](stage-12.1.md) `stage-12.1` — 4 files
- [Browser providers and hosted/local launch adapters](stage-12.2.md) `stage-12.2` — 4 files
- [BUA CDP communication and runtime safety](stage-12.3.md) `stage-12.3` — 5 files
- [BUA session, tab, and turn lifecycle](stage-12.4.md) `stage-12.4` — 4 files
- [BUA page understanding and element targeting](stage-12.5.md) `stage-12.5` — 4 files
- [BUA action execution, input, forms, and downloads](stage-12.6.md) `stage-12.6` — 6 files

## Files in this stage

### Browser and computer-use automation
### `extensions/browser/ufo_ext_browser/bua/__init__.py`

`other` · `import time`

This is an empty Python package marker file. In Python projects, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. That means other parts of the system can refer to code inside `extensions/browser/ufo_ext_browser/bua` using normal Python import paths. Think of it like putting a label on a drawer: the drawer may contain useful tools elsewhere, but this label is what lets the rest of the project find and open it consistently. Because this file is empty, it does not run setup code, create objects, or change program behavior directly. Its importance is structural: without it, some Python environments or tooling might not recognize the folder as a package, which could make imports fail or behave inconsistently.

## 📊 State Registers Touched

- `reg-sandbox-handles` — The remembered sandbox workspaces and conversation sandbox handles used to resume or clean up execution.
- `reg-egress-sandbox-policy` — The shared rules for sandbox network access, proxy behavior, internet permission, and exposed secrets.
- `reg-browser-sessions` — The active browser automation workbench for a turn, including Chrome sessions, tabs, and downloads.
- `reg-hosted-site-registry` — The saved hosted-site names, owners, visibility, ports, files, previews, and ingress routing state.
