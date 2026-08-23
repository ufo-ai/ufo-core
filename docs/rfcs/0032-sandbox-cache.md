---
rfc: 0032
title: "Sandbox cache — a Rust data-plane daemon behind the egress proxy"
status: proposed
date: 2026-08-15
---

# Sandbox cache — a Rust data-plane daemon behind the egress proxy

> Sandbox turns re-clone the same repositories every conversation, across agents and workspaces. A
> per-customer cache in the egress path removes the repeat cost. The cache holds no credentials of
> its own: it phones the Python control plane for a git credential scoped to `(workspace, user,
> host)`, so the cross-customer mint that sank the first attempt (#1629) is structurally impossible,
> not policed. The daemon is Rust — it is the first module of the egress **data plane**, while
> authorization stays in Python (the **control plane**). It caches git and the public package
> registries (npm, PyPI, crates.io, the Go proxy) — the build/test time sinks.

## Current state

Sandbox egress goes through the Python MITM proxy, `core/src/ufo/sandbox/proxy/server.py`
(~1,700 lines). Every `git clone`, `npm ci`, and `pip install` fetches from the origin every time —
nothing between the sandbox and the internet is cached. The proxy already injects a github.com
credential for direct clones (the sentinel path) and enforces per-agent egress rules
(`rules.py`), but it caches nothing.

A prior attempt (#1629, RFC 0032 draft, now superseded) ran one `cachewd` (block/cachew) subprocess
per workspace behind the proxy. It failed on isolation: cachew holds the GitHub App key and mints a
token for **whatever org a request path names**, ignoring its own `installations` allowlist in favor
of dynamic discovery. Locking each instance to its own org via OPA closed the mint leak but broke
every public and cross-org clone (host-wide `insteadOf` → own-org OPA → 403, no fallback). cachew
also keys its mirror by URL alone (`RepoPathFromURL` = host/org/repo), so it has no axis for
per-user credentials — a per-user PAT would leak across users of one workspace.

## Proposal

A Rust daemon, `ufo-cache` (crate `servers/cache/`), runs on the proxy pod. It is the sandbox egress cache
and the seed of a Rust egress data plane. The split that makes this safe and small:

| Plane | Owner | What | Where decided |
|---|---|---|---|
| **Data** | Rust `ufo-cache` | TLS relay, git mirrors, byte streaming | per request, no phone-home |
| **Control** | Python core | per-agent rules, credential minting, grants, metering, RLS | per sandbox-open (policy push) + two narrow callbacks |

The daemon never phones home for a *decision*. Python resolves the per-sandbox policy at open and
the proxy admits the service; the daemon then serves autonomously, calling back only for the two
things that are genuinely per-request-dynamic: **credential minting** and (later) **metering**.

### Authorization: the cache holds no key

The daemon has no GitHub App key and no stored PAT. For an upstream that needs auth it calls the
control plane:

```
POST /internal/git-credential
  { workspace_id, user_id, host, repo_path }
→ { username, token, principal }              # a credential to use upstream
   | { credential: null, principal: "public" } # fetch anonymously
```

Python resolves least-privilege-first and returns the **principal** matching the credential it
chose. The principal is the mirror-isolation key — the daemon uses `token` to fetch and `principal`
to pick the mirror directory; it never decides isolation itself.

| Credential chosen | When | principal | Mirror sharing |
|---|---|---|---|
| none (anonymous) | public repo | `public` | shared in workspace |
| workspace App token | own-org private | `org:<org>` | shared across workspace users (equal access) |
| per-user PAT | user's personal/private (later) | `user:<U>` | isolated to that user |

`user_id` reaches the daemon only through a header the proxy injects (it already scopes per-agent);
a sandbox can never request another user's credential. Because the daemon can only ever ask for
*this* workspace's credential and can mint nothing itself, it cannot obtain another customer's
token — the leak is gone by construction, and there is no OPA org-lock and no org-scoped rewrite.

### Mirrors namespaced by principal

The mirror path is `MirrorRoot / <principal> / <host> / <org> / <repo>`. Public and own-org content
is shared across a workspace's users (they have equal access); PAT-fetched content is isolated to
the fetching user. This is the intra-workspace analogue of the per-customer instance boundary.

### Scope: git and public package registries

Two strategies behind one daemon, each fronting an **allowlist** so a sandbox cannot steer the cache
at a private, in-cluster, or metadata address:

- **git** (`github.com`) — the principal-namespaced mirror below.
- **packages** (`registry.npmjs.org`, `pypi.org`, `files.pythonhosted.org`, `crates.io`,
  `static.crates.io`, `index.crates.io`, `proxy.golang.org`, `sum.golang.org`) — the transparent
  HTTP cache below.

The naïve package approach — point the client at a `/host/<registry>/` path prefix — fails because
npm rebuilds every tarball URL as `new URL(pathname, registry)`, dropping the prefix. So the proxy
**transparently intercepts the real registry host** instead (it already MITMs hosts for credential
injection): npm/pip/cargo/go are unchanged and a metadata document's own absolute download URL still
resolves to a cached host. Because these artifacts are public and immutable, the package cache is
**shared across tenants** (one `public` principal) rather than per-principal like git — the opposite
isolation call, and the right one: it maximises the hit rate and leaks nothing.

### The package strategy: a shared HTTP forward-cache

A standard RFC-7234 cache keyed by `(host, path, accept-encoding)`. It honours the origin's
`Cache-Control`: a `GET 200` marked `immutable`/long `max-age` (every tarball, wheel, crate, and Go
zip) is stored and served from disk; mutable metadata (`no-cache`/short `max-age`, or no freshness
signal at all — never guessed) passes through. A stale entry with a validator is **revalidated
conditionally** (`If-None-Match`/`If-Modified-Since`) — a `304` refreshes it without re-downloading.
Three things stay uncached, streamed straight through: a non-GET, a request carrying
`Authorization`/`Cookie` (so a private package on a shared host never enters the shared cache), and a
`Range` request. The cold miss writes the body to disk and commits it — renamed into place with its
meta — before responding, so the very next request is a hit and a dropped download leaves nothing
half-written; warm requests stream from disk. Durability and the LRU sweep are the git strategy's,
reused. Because the proxy now MITMs these registries, each carrier points npm and pip at a CA bundle
holding the proxy root (the e2b and docker carriers both do), or their TLS to the intercepted host
would fail.

### The git strategy: local mirror over durable S3

Shell to `git`: `clone --mirror` into the principal path, serve `info/refs` + `git-upload-pack` from
the mirror via `git http-backend` (with `uploadpack.allowAnySHA1InWant` so a pinned `git fetch
origin <sha>` works). git cannot serve from an object store — it needs a POSIX filesystem — so the
proxy pod rolling on every deploy would leave a cold cache. Durability is therefore a **bundle**
(`git bundle create --all`) pushed to S3 (per-principal key prefix; a local directory in dev/tests;
off when neither is set) after a clone and on a snapshot interval; a cold daemon **restores** the
bundle to disk instead of re-cloning origin. **Ref discovery always refreshes the mirror** (a delta
fetch, best-effort — an upstream blip serves the existing mirror), so a clone that starts after a push
sees the new head. Ref discovery is the `info/refs` GET *and* a protocol-v2 `ls-refs` POST, which is
the advertisement itself: a window that covered it would let a replica whose mirror predates the push
advertise the older head, and the client would check it out with no error. The `fetch` negotiation
that follows skips the refresh while the same mirror's last successful fetch is inside a **bounded
freshness window** (`UFO_CACHE_GIT_FRESH_TTL_SECS`, default 15 s; `0` fetches every time) *and* the
mirror already holds every object it wants, so the negotiation rounds of one clone cost no further
upstream trip. The wants condition is what makes the window safe across replicas: a negotiation can
land on a pod other than the one whose advertisement it answers, and a want that pod's mirror cannot
back forces its fetch instead of a refused want. A cold or evicted mirror always clones
or restores first, an authenticated mirror is never served without a successful fetch inside the
window, and the authorization decision is still per request in the control plane — the window bounds
the object refresh only, so an upstream revocation is honoured up to the TTL late on the negotiation of
a clone whose ref discovery was still authorized. A rate-limited sweep evicts the least-recently-used
mirrors to keep the tree under its ceiling.

git-lfs derives its API endpoint from the same remote URL the rewrite points at the cache, so its
calls (`<repo>.git/info/lfs/*` — batch, verify, locks, uploads and downloads alike) arrive at the
daemon too. The daemon **relays them to the origin** with the principal's credential injected —
and rewrites each batch answer's download href onto its own content route, so LFS objects are
**cached per principal by oid** (`UFO_CACHE_LFS_CACHE_MB`, default 4096; `0` relays untouched).
Objects are immutable and content-addressed, so a hit needs no freshness rule: a miss re-batches
against the allowlisted origin with the daemon's own credential (never a client-named URL), fetches
an href only if its host is allowlisted or resolves to globally routable addresses — the daemon
inherits the fetch the sandbox's egress rules used to guard — and commits bytes only when they
hash to the oid. Committed objects snapshot to the durable tier (`lfs/<principal>/<oid>`) off the
response path, and a cold pod restores from it under the same hash proof before it re-downloads
anything; the bucket's blanket expiration bounds the prefix like every other tier's. Uploads, locks, and verify relay untouched, and an object past the per-entry cap
streams through uncached. The proxy stamps `x-forwarded-proto` on what it relays, which is how the
daemon spells absolute hrefs the sandbox can reach.

The mirror does not make pack generation cheaper, so `git-upload-pack` responses are **cached on
disk** beside the mirrors (`UFO_CACHE_PACK_CACHE_MB`, default 4096; `0` disables it) and replayed
byte-identically for an identical request. The key is a sha256 of the principal, host, repo,
negotiation body, the request headers the backend reads, and a **ref-state fingerprint** (every ref's
target plus `HEAD`), so any ref movement is a miss; entries also live under the principal's own
directory, so no principal can address another's pack. Only a `200` from a clean backend run is
stored, and a sweep of the same shape as the mirrors' keeps the tier under its ceiling. One entry is
capped, and the cap stops the capture rather than judging it afterwards: a response past the cap is
served from the bytes captured plus the rest of the backend's pipe, so a response no entry could keep
never takes a full copy of the volume.

### Seams

The daemon is **deploy infrastructure**, not an extension capability: one daemon per proxy pod
serves every workspace, isolated by the principal the control plane returns — nothing per-workspace
to register. So it configures in core, beside the proxy's existing model-host knowledge, not through
a `Manifest.services` point.

- **proxy config** — the daemon address from the deploy env (off when unset). The daemon's own
  `allowed_git_hosts`/`allowed_pkg_hosts` bound what it will fetch; both default to the same lists the
  proxy routes, kept in step by a cross-language test.
- **`ServiceRule` + admission** — the proxy admits the cache host and each package host **only for an
  agent that already holds `InternetRule`** (the cache is a faster path to hosts that agent can
  already reach; a narrowed agent never gains reach through it). `_service` TLS-terminates and relays
  to the daemon on loopback, stamping the trusted `x-ufo-workspace`/`x-ufo-user` headers. A git rule
  relays verbatim; a package rule carries a `daemon_prefix` (`/pkg/<host>`) so the intercepted real
  host names the daemon's package route. Either **falls through to the direct upstream when the daemon
  is down** (a cache outage slows fetches, never breaks them).
- **`exec_env` rewrite** — git alone needs one: for an internet-holding agent, `insteadOf` → cache for
  github via the existing `GIT_CONFIG_*` channel. Packages need no rewrite — the proxy intercepts the
  real host, so the sandbox's package managers are unconfigured. Rewrite and admission share the one
  gate, so a narrowed agent is neither rewritten nor admitted.

### Packaging

`ufo-cache` builds in a multi-stage Docker stage from our source (no external binary to
checksum-pin, unlike `cachewd`). It runs on the proxy pod with the cache on an `emptyDir`; the
per-customer boundary is the deploy's RLS-scoped workspace, and the principal namespaces within it.

## Doctrine fit / implications

- **Core/extension.** The cache is egress-proxy infrastructure, which core already owns (the proxy,
  its model-host knowledge, credential injection). It is deploy config, not a member or workspace
  capability, so it does not belong to an extension — no `Manifest` point, no per-workspace lifecycle.
  Core gains a `ServiceRule`, the admission gate, and the credential-callback endpoint.
- **One shape.** A Rust daemon we own beats a forked Go binary carrying our patches (a "second
  answer to what is this"). It also rides the Rust toolchain the client already established, not a
  third (Go) toolchain.
- **Enforce, don't document.** Isolation is a path prefix the control plane dictates, not a policy
  the daemon interprets. The daemon *cannot* mint, so it cannot leak.
- **Both ends or neither.** Each unit ships producer and consumer together: the service declaration
  with its proxy dispatch, the git rewrite with the daemon that serves it, the callback client with
  the endpoint.
- **Fail loud, but degrade in the data plane.** Control-plane faults raise. A *cache* is an
  availability aid: the proxy falls through to the direct upstream when the daemon is unreachable, so
  the cache is never a new single point of failure for egress.
- **Realtime.** Byte-shoveling is the part Python's GIL taxes; moving it to Rust is the long-term
  win. Only the cache is in scope now — migrating TLS/stream/DNS out of the Python proxy is a later
  unit, gated on measuring the proxy's actual event-loop cost, not asserted here.

## Alternatives

- **Fork cachew, patch minting + mirror keying.** The mint fix is ~50 lines, but per-user PAT
  isolation requires threading a principal through cachew's URL-only mirror path and its 2,900-line
  snapshot layer — a re-architecture of its core assumption, plus a maintained Go fork against our
  doctrine. Rejected once per-user credentials entered scope.
- **Keep the Python cachew-subprocess design (#1629).** Superseded — the org-lock breaks public and
  cross-org clones, and the App-in-the-cache model is the leak.
- **Port the whole egress proxy to Rust now.** No — ~half of `server.py` is control-plane logic
  welded to Postgres, grants, credentials, manifests, and pricing; porting it means reimplementing
  core in Rust or a per-request round-trip. Data plane yes, control plane no.
- **No auth in the cache (anonymous only).** Caches public git + npm + PyPI with zero risk but never
  own-org private repos (the #768 ask). The callback keeps private caching without holding a key.

## Open decisions

- **More package ecosystems.** apt, apk, RubyGems, and Maven drop in as `allowed_pkg_hosts` +
  `CACHE_PKG_HOSTS` entries — no new code, since the package strategy is host-agnostic — once a real
  workload wants them.
- **Private registries.** The shared package cache serves public registries only; an authenticated
  request passes through uncached. A per-principal package tier (mirroring the git model) would let
  private-registry artifacts cache too, if a workload needs it.
- **Cold-miss streaming.** A cold package miss buffers the body to disk and commits before responding,
  so its first byte waits on the full download; a tee that streams to the client while caching would
  cut that latency on the first fetch of a large artifact. Warm hits already stream.
- **Least-privilege principal.** Default is "principal follows the credential Python chose," which
  isolates a public repo to a user when that user has a PAT connected. Detecting public repos to
  keep them in the shared `public`/`org` mirror is a follow-on optimization.
- **Snapshot cadence.** A warm mirror re-snapshots on an interval (600 s) and after a cold clone;
  tuning that against bundle cost versus staleness on restore is left to measurement.
- **S3 backend proof.** The durable logic (snapshot, restore) is proven against a real filesystem
  backend; the S3 adapter is a thin, inspectable layer that still needs a live or minio integration
  test before prod — the one piece not yet covered by the hermetic suite.
