# Extension manifest loading  `stage-2.2`

This stage is part of startup and shared behind-the-scenes support. Its job is to find optional extension packages and read their manifests, which are small “identity cards” that say what each package adds. The core loader is the gatekeeper: it checks which extensions are present and allowed, then turns their declarations into usable pieces such as tools, web pages, helper agents, credentials, skills, scheduled jobs, and data backends.

The core runtime declarations add surfaces like the debugger, web UI, and UFO shell. Agent workflow packages add work modes for browsing, coding, research, and writing pipelines. Content and business packages add document skills, website-building support, and YC-focused resources. Connector packages announce links to outside services such as Slack, Composio, Pipedream, and synced content sources, including login routes and required secrets. Automation and memory packages add remembering, scheduling, pausing, self-review, and skill creation. The many __init__.py files mostly mark folders as importable Python packages, while the manifest.py files provide the real catalog entries the system uses.

## Sub-stages

- [Core loader and runtime surface declarations](stage-2.2.1.md) `stage-2.2.1` — 10 files
- [Agent workflow extension declarations](stage-2.2.2.md) `stage-2.2.2` — 8 files
- [Content and business extension declarations](stage-2.2.3.md) `stage-2.2.3` — 6 files
- [Connector and external service declarations](stage-2.2.4.md) `stage-2.2.4` — 10 files
- [Automation, memory, and self-improvement packages](stage-2.2.5.md) `stage-2.2.5` — 6 files
