# Browser automation during tool execution  `stage-10.2`

This stage is the system’s browser driver during the main work loop. When the agent needs to use the web, these pieces connect to Chrome, control pages, read what changed, and clean up afterward.

The entrypoint and provider parts decide where Chrome comes from. They either connect to a hosted browser or start and reuse a sandboxed Chrome, then expose the control address. The transport and lifecycle parts keep that control link alive using Chrome DevTools Protocol, a remote-control channel for Chrome, and rebuild or close sessions when needed.

Once connected, the page state parts track tabs, page content, and visible elements, like a map of the current website. The action execution parts turn the agent’s commands into real clicks, typing, scrolling, waits, and screenshots, while fixing small input mistakes and waiting for the page to settle. The forms, downloads, and interruption parts handle file uploads, saved downloads, PDFs, and pop-up dialogs.

The two package marker files simply label these browser folders so Python can import them.

## Sub-stages

- [Browser tool entrypoints and endpoint providers](stage-10.2.1.md) `stage-10.2.1` — 4 files
- [CDP transport, runtime, and session lifecycle](stage-10.2.2.md) `stage-10.2.2` — 5 files
- [Page state, tabs, and element lookup](stage-10.2.3.md) `stage-10.2.3` — 4 files
- [Browser action execution and input normalization](stage-10.2.4.md) `stage-10.2.4` — 7 files
- [Forms, downloads, and browser interruptions](stage-10.2.5.md) `stage-10.2.5` — 3 files

## Files in this stage

### Browser package markers
Package initializer files label the browser extension and its browser-automation subpackage for import.

### `extensions/browser/ufo_ext_browser/__init__.py`

`other` · `import time`

This file does not contain executable logic. Its main job is to identify this directory as a Python package named `ufo_ext_browser`, so other parts of the system can import browser extension code from it. Think of it like a sign on a toolbox: it tells readers and Python itself that this folder holds the browser tool pack.

The short text inside the file says that this package contains sandbox browser or computer-use tools, plus a browser subagent profile. In plain terms, that means this area of the project is meant for code that lets an agent interact with a browser-like environment and describes how a browser-focused helper agent should behave.

Nothing would run directly from this file, and no browser action starts here. But without an `__init__.py` file in many Python projects, imports can become less clear or fail depending on how the package is loaded. Its value is structural: it makes the package easy to find, import, and understand at a glance.


### `extensions/browser/ufo_ext_browser/bua/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means other parts of the project can refer to code inside `extensions/browser/ufo_ext_browser/bua` using normal Python import paths. Think of it like putting a label on a drawer: the drawer may contain useful tools, but this label mainly tells Python, “this drawer belongs to the project and can be opened by name.” Without this file, some Python environments or packaging tools might not recognize the folder as a package, which could make imports fail. There are no functions, classes, settings, or startup actions here.
