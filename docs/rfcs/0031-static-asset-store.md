---
rfc: 0031
title: "Content-addressed static assets in the shared blob store"
status: implemented
date: 2026-08-15
---

# Content-addressed static assets in the shared blob store

> Each serve pod writes its static assets to the shared blob store before it serves its first
> page. The asset route reads the store when a requested asset is not in the pod's own build.
> A page can only refer to assets that are in the store. Thus asset requests get a correct
> response from each pod, and deploy rolls that overlap do not cause 404 errors. This design
> changes only the web extension. It does not change core or the deploy pipeline.

## Current state

Each serve pod contains one frontend build. The pod reads the build into memory when the module
loads: `load_assets` (`extensions/web/ufo_ext_web/surface.py:180`) fills `STATIC_ASSETS`
(`surface.py:193`). The function `_static_response` (`surface.py:252`) serves only the names in
that dictionary, with an ETag and `cache-control: no-cache`. The route `static_asset`
(`surface.py:308`) sends a 404 error for all other names. The page HTML refers to each asset by
its Vite content hash. Thus a pod can serve only the assets of its own build.

The deploy pipeline updates the Deployment specification and then stops. It does not monitor the
rollout. When merges come in a short period, the rollouts overlap, and their completion order is
not known. On 2026-08-15, four deploys rolled on testing in 17 minutes (`939c2069b`,
`19610e8c0`, `8f1d3b6dd`, `7a899a5cb`). The workflow for the older commit completed after the
workflow for the newer commit. During the rollout, the load balancer sent requests to old pods
and new pods. A member got a page from a new pod. The page referred to `index-D5MUHu6D.js` and
`index-Dka4hxl9.css`. These names are the output of the `8f1d3b6dd` build. An old pod got the
asset requests and sent 404 errors. The portal did not load. Each rollout that overlaps or is
slow can cause this failure. A cache cannot prevent this failure, because the pod that gets the
request has never had the asset.

The necessary store is available. The `BlobStore` protocol (`core/src/ufo/blob.py:44`) has
`put`, `get`, and `exists`. Deploys use the S3 store. Local development uses the filesystem
store (`core/src/ufo/config.py:55`). The surface already holds the store as
`SurfaceContext.blob` (`core/src/ufo/runtime/ext/surface.py:1021`).

## Proposal

The design has two parts. The two parts are in `ufo_ext_web/surface.py` and go into one unit.

**Part 1 — write the assets before the first page.** The route `portal_page` (`surface.py:269`)
waits for one publish task before it serves a page. The publish task runs one time in each
process. The task writes each entry of `STATIC_ASSETS` to the blob store with the key
`static/web/assets/<name>`. The task does not write a key that `exists`. The names contain the
Vite content hash. Thus each key identifies its content, and the content of a key cannot change.
Two pods that write the same key write the same bytes. If the publish task fails, the page
request fails, and the publish task runs again on the next request. A deploy that cannot get
access to the blob store shows an error. It does not continue with a race condition.

The page request gives the correct order without a boot hook. A browser can request only a hash
that it read from a page. A pod serves a page only after its publish task is complete. Thus each
requested hash is in the store before the request, for each pod that gets the request. A
`JobSpec` with `schedule=None` is not the correct seam, because jobs apply to one workspace
(`core/src/ufo/runtime/ext/manifest.py:100`) and the publish task applies to the deploy. A new boot
callback on `SurfaceSpec` is not necessary, because the page request gives the same order.

**Part 2 — read the store when the build does not have the asset.** The route `static_asset`
serves `STATIC_ASSETS` first, as it does today. If the name is not in the dictionary, the route
reads the key `static/web/assets/<name>` from the blob store. Before the read, the route makes
sure that the name is correct: one leaf under `assets/`, only the characters `[A-Za-z0-9._-]`,
and a suffix from `ASSET_MEDIA_TYPES` (`surface.py:170`). This is the same suffix gate that
`load_assets` applies. The route keeps a store result in a small process dictionary with a size
limit. The route serves a store result with the usual ETag and `cache-control: no-cache`. If the
key is not in the store, the route sends a 404 error. The route continues to operate only for a
session. The response is the same for a local asset and for a store asset.

| Decision | Choice |
| --- | --- |
| Key namespace | `static/web/assets/<vite-name>`; only the publish task writes it |
| Write rule | do not write a key that exists; the bytes for one key are always the same |
| Publish trigger | one time in each process; `portal_page` waits for it before its first response |
| Publish failure | the page request fails; the next request starts the task again; no silent skip |
| Fallback gate | correct name shape and declared suffix, then a store read; other names get 404 |
| Fallback cache | a process dictionary with a size limit; entries do not change |
| Retention | keep all keys; a build is a small number of MB; if you delete a key, a browser with an old page gets 404 errors again |
| Auth, ETag, `no-cache` | no change |

Example — the 2026-08-15 failure with this design: the `8f1d3b6dd` pod starts. A member loads
the portal. The route `portal_page` writes `index-D5MUHu6D.js` to the store and then sends the
page. The browser sends the asset request. The `939c2069b` pod gets the request. The name is not
in the pod's dictionary. The pod reads the key from the store, keeps the result, and sends a 200
response. The order of the rollouts has no effect.

## Doctrine fit / implications

Core does not change. The store, the configuration, and `SurfaceContext.blob` are available
today. The two new parts are in the web extension, in the module that owns static serving. The
reader finds the publish task, the route, and the store read in one file, from top to bottom.
The two parts and their tests go into one unit. The publish task fails loudly. A 404 from the
route keeps one meaning: the asset does not exist. It does not mean "incorrect pod". The data
that goes to the store is the packaged asset set, which has a known size. The suffix gate limits
the store reads to the same set of types.

Out of scope: rollouts that do not overlap, a pipeline that monitors rollouts, a CDN cache (the
route operates only for a session), and pages from mixed versions. During a rollout, two pods
can serve two different versions of `index.html`. With this design, the two pages operate
correctly, because the assets of each page are in the store.

## Alternatives

- **Put the assets of the last N builds in the image.** This cannot prevent the failure that
  occurred. An old image cannot contain a build that did not exist when the image was made. This
  option helps only when an old page gets assets from a new pod. Fast merges also go past each
  fixed N.
- **Monitor the rollouts, or use a blue-green cutover.** This prevents mixed versions. But the
  pipeline must then monitor rollouts, make deploys sequential, and move traffic in one step.
  That is a large infrastructure change with its own failure modes. One extension can remove the
  symptom.
- **Use a CDN cache for the assets.** The route operates only for a session. Also, a CDN does
  not have a hash that it has not seen. The first request for a new hash during a rollout goes
  to an old pod and gets a 404 error.
- **Add a boot callback to `SurfaceSpec` for the publish task.** The effect is the same as the
  publish task in the page request, but this adds a core seam. The page request gives the order
  without a new seam.

## Open decisions

- The terminal client binaries are also static data in each pod. They can use the same store
  under `static/<surface>/…` after this design shows that it operates correctly. This design
  does not block that.
