# Artifacts, media previews, hosted sites, and generated app outputs  `stage-14`

This stage is the system’s sharing and publishing workshop. It comes after work has been created, and turns that work into things people can open: downloadable files, document previews, live site links, app homepages, screenshots, and signed downloads. Much of it runs behind the scenes, making sure shared output is useful but still controlled.

The shared file storage part is the guarded file counter. It creates temporary signed links, which are web addresses that prove who may fetch a file and for how long. It serves downloads only when that proof checks out, makes safe previews for documents and images, and retries preview jobs that failed earlier.

The hosted sites and app homepage part is the publishing desk. It records who owns each site, builds and audits app pages, moves source files into sandboxes, creates temporary or permanent public links, serves site traffic, reports broken sites, and makes preview cards or screenshots.

The media package marker simply makes the media runtime folder importable by the rest of the codebase.

## Sub-stages

- [Shared file storage and signed download links](stage-14.1.md) `stage-14.1` — 7 files
- [Hosted sites and app homepage publishing](stage-14.2.md) `stage-14.2` — 14 files

## Files in this stage

### Artifacts, media previews, hosted sites, and generated app outputs
### `core/src/ufo/runtime/media/__init__.py`

`other` · `import/package discovery`

This is an empty package file. In Python projects, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may contain useful tools, but the label is what lets the rest of the system find that drawer by name.

Here, the drawer is `ufo.runtime.media`. Even though this file does not define any functions, classes, or settings, it still matters because imports elsewhere in the codebase may rely on this package existing. Without it, depending on the Python version and packaging setup, code that tries to import media runtime modules from this directory could fail or behave differently.

Because it is empty, it does not run startup logic, load data, or change program state. Its job is structural: it helps organize the project and makes the media runtime area visible to Python's import system.

## 📊 State Registers Touched

- `reg-billing-ledger` — The shared meter and wallet state for usage costs, spend caps, prepaid balances, and billing identity.
- `reg-sandbox-state` — The remembered sandbox handles and execution environments where commands, files, and risky work run safely.
- `reg-artifact-publication` — The shared state for files, previews, signed downloads, hosted sites, app pages, and published outputs.
- `reg-conversation-slots-ui` — The shared side-panel and workspace UI state for artifacts, sources, tasks, sites, automations, and app home screens.
- `reg-blob-store` — The durable binary-object namespace and storage keys for large files, imported page bodies, previews, attachments, and other non-row data.
- `reg-document-review-state` — Saved document-review findings and annotation state files used by PDF, PowerPoint, Word, and spreadsheet automation utilities.
