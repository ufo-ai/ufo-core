# Source ingestion, indexing, and recall  `stage-14.2`

This stage is the system’s intake and recall pipeline. It runs mostly in the background after accounts or file sources are connected, and it keeps outside knowledge fresh for search and memory. The core sync runtime is the engine: connectors fetch records page by page, use bookmarks to resume later, turn records into stable pages, notice changes, and record deletes. The many provider groups are the plug adapters for real services, such as Google Drive, Slack, GitHub, Jira, Salesforce, Stripe, Zendesk, HR tools, finance tools, and marketing tools. They each speak that service’s API, then translate the results into the same internal stream.

Gbrain Markdown sources do a similar job for local folders or GitHub repositories of Markdown files. Account plumbing stores credentials, creates feeds, recognizes resources, and remembers sync progress. Once pages arrive, indexing and embedding code breaks text into smaller pieces, converts meaning into searchable number patterns, and stores it for search and memory recall. The providers package marker simply lets Python import all these connector modules.

## Sub-stages

- [Core source sync runtime framework](stage-14.2.1.md) `stage-14.2.1` — 4 files
- [Indexing, embeddings, and memory recall](stage-14.2.2.md) `stage-14.2.2` — 6 files
- [Gbrain Markdown page sources](stage-14.2.3.md) `stage-14.2.3` — 3 files
- [Source extension account plumbing and sync metadata](stage-14.2.4.md) `stage-14.2.4` — 6 files
- [Workspace, document, communication, and support source providers](stage-14.2.5.md) `stage-14.2.5` — 15 files
- [Work management, recruiting, HR, and incident source providers](stage-14.2.6.md) `stage-14.2.6` — 15 files
- [CRM, marketing, advertising, and customer data source providers](stage-14.2.7.md) `stage-14.2.7` — 12 files
- [Finance, billing, commerce, and contract source providers](stage-14.2.8.md) `stage-14.2.8` — 11 files

## Files in this stage

### Source ingestion, indexing, and recall
### `extensions/sources/ufo_ext_sources/providers/__init__.py`

`other` · `import/package discovery`

This is an empty Python package marker file. In Python, a folder with an `__init__.py` file can be treated as a package, which means code elsewhere can import files inside it using package-style paths. Here, it makes the `extensions/sources/ufo_ext_sources/providers` directory available as a place for provider-related modules. Think of it like putting a label on a drawer: the drawer may contain useful tools, but the label itself does not do the work. Without this file, depending on the Python version and packaging setup, imports from this folder could be less reliable or fail in environments that expect traditional packages. Because the file is empty, it does not create objects, run setup code, or change program behavior directly. Its value is structural: it helps organize the source extension code and gives the rest of the project a stable import location for provider implementations.
