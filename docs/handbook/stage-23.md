# Public SDK, Protocol Types, and Extension Contracts  `stage-23` (cross-cutting infrastructure)

This stage is shared behind-the-scenes support. It defines the public promises that extensions, model providers, web tools, search backends, and iMessage services rely on during the whole system lifecycle. It is less like the engine of a car and more like the agreed shape of the keys, plugs, dashboard labels, and service forms.

The core contracts describe what an extension may receive, what it may declare in its manifest, how conversation side panels are shaped, and how AI model calls and streaming updates look. The SDK facade stages turn those internal contracts into stable public import paths for extension writers, covering tools, context, credentials, billing, browser and terminal access, sources, search, jobs, surfaces, web callbacks, logging, flags, and utilities. The sample extension checks that this public surface really works.

The iMessage protocol stages provide generated message and service types, plus gRPC network bindings, so iMessage-related code can exchange chats, messages, attachments, events, groups, and polls in a consistent format. Finally, ufo.kinds is simply marked as an importable Python package.

## Sub-stages

- [Core Extension and Model Contracts](stage-23.1.md) `stage-23.1` — 4 files
- [Public SDK Core Facades and Sample Coverage](stage-23.2.md) `stage-23.2` — 8 files
- [Public SDK Identity, Credentials, Billing, and Access Facades](stage-23.3.md) `stage-23.3` — 9 files
- [Public SDK Provider, Search, Job, and Capability Facades](stage-23.4.md) `stage-23.4` — 10 files
- [Public SDK Web, Surface, Sandbox, and Utility Facades](stage-23.5.md) `stage-23.5` — 11 files
- [iMessage Protobuf Package and Google Annotation Glue](stage-23.6.md) `stage-23.6` — 8 files
- [iMessage v1 Generated Protobuf Message and Service Types](stage-23.7.md) `stage-23.7` — 11 files
- [iMessage Provider Contract and gRPC Service Bindings](stage-23.8.md) `stage-23.8` — 5 files

## Files in this stage

### Public SDK, Protocol Types, and Extension Contracts
### `core/src/ufo/kinds/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. That matters because code elsewhere can then refer to this area as `ufo.kinds` and import the actual modules inside it.

There is no logic here, no functions, and no setup work. Its job is more like putting a label on a drawer: the label does not contain the tools, but it makes the drawer recognizable and usable by the rest of the system. Without this file, depending on the Python version and packaging setup, imports involving `ufo.kinds` could fail or behave differently.

## 📊 State Registers Touched

- `reg-pack-and-feature-selection` — The chosen product packs and feature switches that decide which parts of the system are enabled.
- `reg-extension-installation-lock` — The saved list of installed extensions and exact versions that should be loaded again consistently.
- `reg-extension-capability-registry` — The live catalog of everything enabled extensions add, such as tools, routes, jobs, credentials, hooks, and backends.
- `reg-tool-catalog` — The current list of tools the agent may call, with their names, inputs, permissions, and implementations.
- `reg-skill-store` — The shared library of built-in and user-created skills that can be selected, checked, and loaded into a turn.
- `reg-model-catalog` — The shared list of available AI models, their abilities, prices, limits, and required credentials.
- `reg-credential-vault` — The encrypted store of API keys, connected accounts, grants, and approvals that lets tools use outside services without exposing secrets.
- `reg-object-registry` — The shared object front desk that gives stable names, views, permissions, and change history for workspace records.
