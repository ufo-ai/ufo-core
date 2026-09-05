# Source synchronization, indexing, memory, and enrichment  `stage-14`

This stage is the system’s knowledge intake and preparation area. It runs during normal operation, after accounts or folders have been connected, and keeps outside information fresh and useful for agents.

First, the provider polling and page storage part acts like a loading dock. Connectors visit services such as Google tools, GitHub, CRM, support, HR, finance, recruiting systems, or Markdown folders. Each connector knows how to fetch records in batches, remember its place with cursors, notice deleted items, and turn everything into a common “page” shape. Shared sync code saves the page text and metadata, records warnings or changes, and marks updated pages for later processing.

Then the index, memory, and profile derivation part turns stored pages into usable knowledge. It breaks long text into chunks, creates embeddings, meaning number patterns used for semantic search, and stores them locally or in external services such as OpenAI or Turbopuffer. Memory tools search, summarize, clean up, and condense this material into facts, profiles, wiki-like notes, and recall results. Optional enrichment can also add consent-based external profile details for members.

## Sub-stages

- [Provider polling and page storage](stage-14.1.md) `stage-14.1` — 63 files
- [Index, memory, and profile derivation](stage-14.2.md) `stage-14.2` — 12 files

## 📊 State Registers Touched

- `reg-prompt-skill-environment` — The saved instructions, skills, environment documents, and fingerprints that shape what an agent sees for a turn.
- `reg-transcript-history` — The saved conversation timeline, including messages, compacted summaries, final answers, costs, and readable history.
- `reg-credential-vault-connections` — The lockbox of account connections, OAuth grants, API keys, BYOK attempts, and agent permissions to use outside services.
- `reg-source-sync-state` — The saved state for connected information sources, including cursors, pages, deletions, warnings, backoff, and source access grants.
- `reg-memory-index-profiles` — The searchable memory layer made from synced pages, chunks, embeddings, summaries, facts, and member or workspace profiles.
- `reg-blob-storage-state` — The raw byte/blob storage namespaces and content-addressed stored files that back artifacts, previews, environment files, workspace files, and deploy-wide assets.
- `reg-indexing-enrichment-work-queue` — Dirty-page and processing markers that tell background workers which synced pages need chunking, embedding, memory/profile derivation, or enrichment refresh.
- `reg-external-client-connection-pools` — Process-global HTTP/gRPC client sessions, proxy clients, DNS/TLS state, and connection pools used for model providers, connectors, cloud storage, and sandbox services.
- `reg-provider-rate-limit-backoff` — Shared throttling, retry-after, backoff, and concurrency state for AI providers and external connector APIs, separate from billing spend caps.
