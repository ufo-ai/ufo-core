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
> authorization stays in Python (the **control plane**). This ships git caching; npm/PyPI is deferred
> (see Scope).

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

A Rust daemon, `ufo-cache` (crate `cache/`), runs on the proxy pod. It is the sandbox egress cache
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

### Scope: git today, package registries deferred

This ships **git only**. npm/PyPI caching is deferred: the obvious approach (point the client at a
`/host/<registry>/` path prefix) fails because npm's client rebuilds every tarball URL as
`new URL(pathname, registry)`, dropping the prefix — correct registry caching needs registry-host
routing plus response rewriting, a separate design. So the daemon fronts only an **allowlist**
(`github.com`); any other host in a request path is refused before a URL is built from it, so a
sandbox cannot steer the cache at a private, in-cluster, or metadata address.

### The git strategy: local mirror over durable S3

Shell to `git`: `clone --mirror` into the principal path, serve `info/refs` + `git-upload-pack` from
the mirror via `git http-backend` (with `uploadpack.allowAnySHA1InWant` so a pinned `git fetch
origin <sha>` works). git cannot serve from an object store — it needs a POSIX filesystem — so the
proxy pod rolling on every deploy would leave a cold cache. Durability is therefore a **bundle**
(`git bundle create --all`) pushed to S3 (per-principal key prefix; a local directory in dev/tests;
off when neither is set) after a clone and on a snapshot interval; a cold daemon **restores** the
bundle to disk instead of re-cloning origin. **Ref discovery refreshes the mirror every time** (a
delta fetch, best-effort — an upstream blip serves the existing mirror), so a read right after a push
is never stale, while the objects still come from the local mirror. A rate-limited sweep evicts the
least-recently-used mirrors to keep the tree under its ceiling.

### Seams

The daemon is **deploy infrastructure**, not an extension capability: one daemon per proxy pod
serves every workspace, isolated by the principal the control plane returns — nothing per-workspace
to register. So it configures in core, beside the proxy's existing model-host knowledge, not through
a `Manifest.services` point.

- **proxy config** — the daemon address from the deploy env (off when unset). The daemon's own
  `allowed_git_hosts` (`github.com`) bounds what it will fetch.
- **`ServiceRule` + admission** — the proxy admits the cache host **only for an agent that already
  holds `InternetRule`** (the cache is a faster path to hosts that agent can already reach; a
  narrowed agent never gains reach through it). `_service` TLS-terminates and relays to the daemon on
  loopback, stamping the trusted `x-ufo-workspace`/`x-ufo-user` headers, and **falls through to the
  direct upstream (allowlisted host only) when the daemon is down** (a cache outage slows clones,
  never breaks them).
- **`exec_env` rewrite** — for an internet-holding agent only, `insteadOf` → cache for github, emitted
  through the existing `GIT_CONFIG_*` env channel. The rewrite and the admission share the one gate,
  so a narrowed agent is neither rewritten nor admitted.

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

- **npm/PyPI caching.** Deferred (see Scope). Doing it right needs registry-host routing (a cache
  host per registry so the client's URL rebuild survives) plus packument/simple-index response
  rewriting — its own PR, on top of this git-only base.
- **Least-privilege principal.** Default is "principal follows the credential Python chose," which
  isolates a public repo to a user when that user has a PAT connected. Detecting public repos to
  keep them in the shared `public`/`org` mirror is a follow-on optimization.
- **Snapshot cadence.** A warm mirror re-snapshots on an interval (600 s) and after a cold clone;
  tuning that against bundle cost versus staleness on restore is left to measurement.
- **S3 backend proof.** The durable logic (snapshot, restore) is proven against a real filesystem
  backend; the S3 adapter is a thin, inspectable layer that still needs a live or minio integration
  test before prod — the one piece not yet covered by the hermetic suite.
