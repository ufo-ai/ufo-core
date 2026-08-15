---
rfc: 0032
title: "Blob keys live under the bound workspace"
status: implemented
date: 2026-08-15
---

# Blob keys live under the bound workspace

> Every workspace-owned blob lives under `workspaces/<workspace_id>/`. Code never spells that
> prefix: `WorkspaceBlobStore` reads the ambient `ws(...)` scope at call time and prepends it, the
> way `ws_current()` scopes credentials and RLS. Keys in code, in Postgres columns, and in DBOS
> step records stay workspace-relative and unchanged. Deploy-owned data goes through
> `FleetBlobStore`, whose namespace is closed to `static/` and `term/`. Migrating a deployed
> bucket is an object copy driven by the database — no rows change.

## Current state

The bucket mixed every workspace's data in toplevel prefixes. On the testing bucket:

| Prefix | Objects | Owner |
|---|---|---|
| `conversations/<conv>/…` | 71,412 | one workspace each, unnamed in the key |
| `sources/<src>/<page>/<digest>` | 20,749 | same |
| `tool-images/<turn>/<call>/<n>` | 563 | same |
| `artifacts/<uuid>/<name>` | 666 | same |
| `workspaces/<ws>/…` | 12 | already scoped (credential markers, slack identity) |
| `static/web/…`, `eval-viewers/…` | — | the deploy, not a workspace |

Key builders took no workspace (`transcript_key`, `compaction_key`, the sources and tool-image
templates), two families spelled `workspaces/<ws>/` by hand, and nothing prevented a new call
site from writing anywhere in the bucket: the app IAM role is whole-bucket
(`infra/modules/platform/iam.tf:46`).

## Design

`core/src/ufo/blob.py` holds two frozen wrappers over the one backend
(`FilesystemBlobStore` | `S3BlobStore`), both satisfying the `BlobStore` protocol:

- **`WorkspaceBlobStore`** resolves `ws_current()` inside every operation and prepends
  `workspaces/<id>/`. The workspace is never an argument — an unscoped call raises
  `WorkspaceUnbound`, a key can never land under another workspace, and a key already carrying
  the prefix is refused (an un-migrated caller, caught loudly). `list` strips the prefix from
  returned keys, so callers live entirely in workspace-relative key space; `get_stream` resolves
  its key at the call, not at the first chunk, so a download body serves after the binding
  releases. `presigned_put` signs the full key.
- **`FleetBlobStore`** takes keys only under `static/` (portal assets, RFC 0031) and `term/`
  (redis-hub terminal spill) — workspace data cannot flow around the workspace store.
  `SurfaceContext.fleet_blob` derives it from `blob.backend`.

Because callers hold workspace-relative keys, nothing stored changes shape:
`shared_artifact.blob_key`/`.preview_blob_key`, `page.body_ref`, and the tool-image keys frozen
in DBOS `operation_outputs` are already the relative form. There is no schema migration.

Raw backends survive only at boot boundaries — `serve.py` (which also derives the egress proxy's
host rule from the backend), `cli.py`, `proxy_serve.py`, and the eval bootstraps — enforced by
the `_raw_blob_failures` gate in `gates.py`.

### Artifact URLs

The public path stays `/artifacts/<uuid>/<name>` and remains the workspace-relative blob key. The
signed query gains `ws`, the workspace whose store holds the bytes, inside the HMAC
(`core/src/ufo/artifact_url.py`); the route binds it and serves DB-free. A URL carrying no `ws`
claim — the address form living in messages minted before the claim — verifies against its own
signed message and never serves directly: it takes the member-refresh path, which authorizes
exactly as an expired link (session member + `shared_artifact` owner, unchanged query) and 303s
to the same path under a fresh claim-carrying grant.

## Migration

One idempotent copy, driven by the database mapping (conversation, page, shared_artifact, and
turn rows name their workspace), server-side `CopyObject`, skip-if-destination-newer, never
deleting. Sequence per environment: bulk copy live → scale `ufo-serve` to 0 → delta copy → deploy
this change → verify every row-referenced key exists at its new address → scale up → soak (old
tool-image keys recorded in pre-roll DBOS workflows must stay readable until those workflows are
terminal) → delete the old toplevel prefixes, orphans included, from the report. Prod holds 12
objects in one prefix; testing is the real migration. Local dev: delete `./blobs` and reseed.

## Not doing

- Per-workspace IAM prefix conditions — the layout now permits them; the role stays whole-bucket.
- Scoping `term/op|reply/<op_id>` — self-deleting transport spill with no workspace in scope.
- Moving `eval-viewers/` — operator-published, S3-served, outside the `BlobStore` seam.
