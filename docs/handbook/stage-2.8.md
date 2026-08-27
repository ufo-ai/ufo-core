# Memory, Knowledge Graph, and Indexing Migrations  `stage-2.8`

This stage is behind-the-scenes upgrade work for how the system stores and finds knowledge. It is not the main user-facing work loop. Instead, it is a set of database migrations, meaning scripted changes that reshape the database safely as the product evolves.

It starts by showing the old core knowledge graph: tables for things and the links between them. That approach is then retired as the system moves to a simpler memory surface. Page revisions are also given clear per-workspace sequence numbers, so edits can be ordered reliably.

Next, the search chunk migrations build storage for small pieces of text used in search. They add both meaning-based lookup, through embeddings, and workspace separation. The memory base migrations create the main shelves for memory items and memory pages, then scope them to workspaces.

Later migrations make memory faster to browse and more time-aware, adding indexes and “as of” dates. Provenance migrations record which pages and page versions memories came from. The final group adds audiences, retirement markers, richer memory classes, and member profiles, making stored knowledge easier to organize, trace, and maintain.

## Sub-stages

- [Core Knowledge Graph Retirement and Page Revision Migrations](stage-2.8.1.md) `stage-2.8.1` — 3 files
- [Default Search Chunk Index Migrations](stage-2.8.2.md) `stage-2.8.2` — 2 files
- [Memory Extension Base Tables and Workspace Scoping](stage-2.8.3.md) `stage-2.8.3` — 4 files
- [Memory Indexes and Information Time](stage-2.8.4.md) `stage-2.8.4` — 4 files
- [Memory Page Provenance and Revision Links](stage-2.8.5.md) `stage-2.8.5` — 3 files
- [Memory Audiences, Lifecycle, Classes, and Profiles](stage-2.8.6.md) `stage-2.8.6` — 5 files
