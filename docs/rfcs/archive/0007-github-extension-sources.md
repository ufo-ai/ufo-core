---
rfc: 0007
title: "GitHub extension sources (proposal): install extensions from git URLs, public and private"
status: proposed
date: 2026-07-06
---

# GitHub extension sources (proposal): install extensions from git URLs, public and private

Status: **proposal, not adopted.** Nothing here is built. It extends `spec.md` (§Extension system,
§Extension store, §Deploy config bundling) with a third way to obtain an extension package — a git
URL (`git+https://…`, `git+ssh://…`), including a **private** GitHub repo — alongside the workspace
member (dev) and the package index (bundle) paths that exist today.

The thesis: **the install path is the only new thing.** Discovery, the lockfile, the digest refuse,
and the bundle are already source-agnostic — they operate on the *installed environment*, not on
where the package came from. `pip`/`uv` already resolve `git+https://…@<ref>` and `git+ssh://…` to a
wheel; the RFC threads a git source through the existing `selfhost ext` → lockfile → boot-verify →
`selfhost bundle` chain and adds **nothing** to the boot hot path. No parallel fetcher, no second
lockfile, no second digest.

## Current model (precise)

An extension is a pip/uv-installable Python package with a `selfhost.extension` entry point returning
a `Manifest` (`spec.md:117`; `research/pyproject.toml` `[project.entry-points."selfhost.extension"]`).
The system reasons only about the *installed environment*:

| Concern | Mechanism | Where |
|---|---|---|
| Discover | `entry_points(group="selfhost.extension")`, each loaded + name-deduped | `loader.py:112-122` (`discovered()`), group const `loader.py:58` |
| Digest | sha256 over **every** source file of the installed package (excl. `__pycache__`/`.pyc`), prefix `sha256:` | `loader.py:155-175` (`extension_digest`) |
| Pin | `ExtensionPin(name, version, digest)`, `extra="forbid"` | `loader.py:80-87` |
| Lockfile | `Lockfile(selfhost_version, extensions=(ExtensionPin,…))`, JSON file | `loader.py:90-97`, `read/write` `104-109` |
| Boot | lockfile present → only pinned load, **each re-verified against pinned digest** (missing/drifted fails loud); none → dev, all discovered active | `loader.py:198-223` (drift refuse `216-219`) |
| Install | pins an **already-installed** package: `pin_for(name)` fails loud if `discovered()` lacks it — the store **does not fetch or install a package**, only records its digest | `store.py:56-63`, `86-96` |
| Bundle | freezes pins into a Dockerfile: `RUN pip install "selfhost=={version}"` (the one distribution ships every first-party extension; the lockfile narrows the active set, boot re-verifies each pinned digest) + `COPY selfhost.toml selfhost.lock` | `bundle.py` `_dockerfile` |
| Dev | one `selfhost` distribution bundles core + every first-party extension + pack; `pip install -e .` / `uv sync` makes them all present without a per-package install | root `pyproject.toml` (`[tool.hatch.build.targets.wheel]`) |

Two load-bearing facts the target builds on:

1. **`serve` never installs.** `run()` does `load_config()` → `load_manifests()` → verify; it assumes
   the environment is already populated (`serve.py:84-96`). Population happens **out of band** — the
   Dockerfile `RUN pip install` at build, or uv at dev time. There is no fetch on the serve loop.
2. **The digest is source-independent.** It hashes installed source files, so a package installed
   from a git commit and the same package installed from a PyPI wheel of that commit produce the
   *same* digest. Commit-pin and digest-pin therefore compose for free.

## Target

A git source names a repo + ref; a token authenticates a private one. Two equivalent surfaces, both
**off the serve loop**, both converging on one lockfile pin:

```toml
# declarative — reconciled by `selfhost migrate` / `selfhost ext sync`, never by `serve`
[[extensions]]
source = "git+https://github.com/acme/selfhost-ext-crm@v0.3.1"
subdirectory = "crm"          # optional: monorepo package path
token_env = "ACME_GH_TOKEN"   # optional: env var holding a PAT for a private repo
```

```
# imperative equivalent
selfhost ext install git+https://github.com/acme/selfhost-ext-crm@v0.3.1
selfhost ext install git+ssh://git@github.com/acme/private-ext@main   # ssh deploy key
```

**Where the resolve happens: install time, operator/CLI role — not boot.** This is the central
decision and it falls straight out of fact 1 above. `serve` stays pure discovery + digest-verify
(offline, deterministic, no GitHub on the hot path). The declarative `[[extensions]]` array is
*intent*; an explicit reconcile step (`selfhost migrate`, or a new `selfhost ext sync`) realizes it —
exactly as `init`/`migrate` already run out of band from `serve` (`cli.py:60-81`). A git entry in
config that has not been reconciled into the lockfile is not loaded; a git pin in the lockfile whose
package is absent fails loud at boot like any other missing pin (`loader.py:211-213`).

## Resolve → install → pin (the pipeline, off the loop)

One flow, run by the CLI (sync I/O off the loop is sanctioned for CLI/build — `AGENTS.md`
"Async-native"):

1. **Resolve + install** — shell `uv pip install "<git-url>@<ref>"` (with `#subdirectory=…` when set).
   uv resolves the ref to a concrete commit, clones into **its own cache** (`~/.cache/uv`), builds the
   wheel, installs it into the venv. We reuse uv's git support wholesale — no dedicated fetcher.
2. **Discover** — the newly-installed package now appears in `discovered()` (`loader.py:112`); its
   manifest name and version are read from the loaded entry point.
3. **Pin** — extend the existing `pin_for` (`store.py:56-63`) to record the resolved commit as
   provenance alongside the digest it already computes. The pin lands in the same `Lockfile` via the
   same `write_lockfile` (`loader.py:108`).

`ExtensionPin` gains one optional field:

```python
class ExtensionPin(BaseModel):
    name: str
    version: str
    digest: str
    source: str | None = None   # "git+https://github.com/acme/repo@<resolved-commit>"; None ⇒ index
```

`source` carries the git URL **pinned to the resolved commit, with no token** (see auth). Absent ⇒
the current PyPI-index path (`bundle.py:79` unchanged). Present ⇒ bundle vendors a wheel (below). A
moving ref (`@main`) is resolved to a commit **once** at install and frozen there; re-running
`ext install` re-resolves and re-pins.

## Download + cache

- **Cache = uv's git cache**, keyed by repo URL + resolved commit (uv's existing behavior). We add no
  cache of our own; a second install of the same commit is a cache hit and rebuilds nothing.
- **Populated at install time**, never at boot. A deployed image carries the *installed package*, not
  a clone — the cache is a build-host concern only.
- **Invalidation** is the ref→commit re-resolve on the next `ext install`/`ext sync`; the digest
  refuse (`loader.py:216-219`) catches any post-install drift regardless of cache state.
- **Offline / bundled deploys need no GitHub.** `selfhost bundle` vendors the *resolved* package:
  `uv pip wheel "<git-url>@<commit>"` at bundle time produces a `.whl`, copied into the build context;
  the Dockerfile installs from the local wheel, not from GitHub. The running image has **zero**
  runtime GitHub dependency or network need — identical to the PyPI path's offline property.

## Private GitHub auth

The download authenticates in the **operator/CLI role** only — the role that also holds the model
keys and `credentials.key_env`. It is a resolve-time secret, categorically distinct from a workspace
BYOK `credential` slot:

| | Private-repo token | BYOK `credential` slot |
|---|---|---|
| Lives in | env var named in config (`token_env`), like `models.*_api_key_env` (`config.py:68-69`) | Fernet-encrypted per workspace in DB (`credentials.py`) |
| Read by | `uv`/`git` at CLI/bundle time | the egress proxy, to sentinel-swap into sandbox traffic (`credentials.py:2-4`) |
| Reaches the sandbox? | **never** — resolve happens before any turn, in the operator role; not on any proxy InjectionRule | yes, that is its entire purpose |
| In the lockfile/bundle? | **never** — only the tokenless `git+…@<commit>` URL is recorded | n/a |

Mechanics: for `git+https://…`, the CLI injects the token at fetch time via `GIT_ASKPASS` /
`x-access-token:<token>@` on the URL passed to uv, then strips it before writing `source`. For
`git+ssh://…`, no token at all — the operator's ssh-agent / deploy key does it, and selfhost touches
no secret. The token env var is **not** a selfhost credential slot (wrong role, wrong lifecycle: it
belongs to the operator installing code, not to a workspace member granting the agent an account),
and it is never persisted, logged, or egressed.

## Security / integrity

The tamper-refuse invariant is **preserved by reuse, not extended**:

- A git-sourced extension is pinned to its **resolved commit** (`source`) + its `extension_digest`
  (`digest`) in the same `Lockfile`, and re-verified at boot by the **same** `load_manifests` path as
  a registry install (`loader.py:210-220`). No parallel verify, no git-source branch at boot.
- The **digest is the refuse**; the commit is reproducibility + human-auditable provenance. Because
  the digest hashes installed source (fact 2), commit-pin and digest-pin agree: the pinned commit
  reproduces the pinned digest.

Trust model — state it plainly, because arbitrary GitHub code runs **in-process in the serve/jobs
roles** (not the sandbox):

| Protects against | Mechanism |
|---|---|
| Post-install tampering / supply-chain drift after pin | boot digest re-verify (`loader.py:216-219`) |
| A moving branch silently changing under a deploy | commit freeze at install; boot loads the pinned commit's digest |
| An extension reaching into core internals | static SDK-import gate — `extensions/` may import only `selfhost.sdk` (`gates.py` `SDK_PUBLIC_PREFIX`) |
| Unreviewed code entering a deploy | operator-gated install (CLI/config), never a member chat action |
| **Does NOT** protect against | a *malicious extension the operator chose to install* — it runs with serve/jobs privileges; the SDK gate is a **static import boundary, not a runtime sandbox**. Same threat surface as any PyPI extension; the git source widens *reach* (any repo), not *privilege*. |

The bar is therefore identical to installing an arbitrary PyPI package: **operator install + digest
pin + SDK-import gate.** Git URLs do not lower it.

## CLI / UX + bundle integration

| Command | Behavior |
|---|---|
| `selfhost ext install git+https://…@ref` | resolve→install→pin (above); prints `installed <name> <version> (<digest>) from <commit>`. Works with the store **off** — a git source is a direct source, not a catalog lookup (extends `cli.py:542-550`). |
| `selfhost ext install <name>` | unchanged — catalog/store path (`store.py:86`). |
| `selfhost ext sync` (new) / `selfhost migrate` | reconcile `[[extensions]]` git entries into the lockfile; the config-driven twin of the imperative verb. |
| `selfhost ext remove <name>` | unchanged (`store.py:98`); drops the pin regardless of source. |
| `selfhost bundle` | git pins (`source != None`) → `uv pip wheel` the resolved commit into the context, `COPY` + `RUN pip install ./wheels/<name>.whl`; index pins → current `pip install "{dist}=={version}"` (`bundle.py:79`). Output image: no runtime GitHub dependency. |

What the **lockfile records** for a git pin: `name`, `version`, `digest`, and
`source = "git+https://github.com/acme/repo@<commit>"` — **no token, no ref alias**. Fully
reproducible and safe to commit.

## What changes in the codebase (concrete)

- `loader.py`: `ExtensionPin` gains `source: str | None = None` (both ends: written by install,
  read by bundle). No change to `discovered()`, `extension_digest`, or `load_manifests` — they are
  already source-agnostic.
- `store.py` / a new install path: `pin_for` records the resolved commit into `source`; a git-install
  entry point shells `uv pip install` then pins. Git install is store-independent (no catalog entry
  required).
- `config.py`: add `[[extensions]]` → an `ExtensionSource` BaseModel (`source`, optional
  `subdirectory`, optional `token_env`), `extra="forbid"` — mirrors `SourceEntry` (`config.py:145-152`).
- `bundle.py`: `_dockerfile` branches on `pin.source` — vendor a wheel for git pins, index-install
  otherwise (`bundle.py:73-91`).
- `cli.py`: `ext install` accepts a `git+…` argument; add `ext sync` (or fold into `migrate`).
- **Nothing in `serve.py`.** Boot is untouched — the whole point.

## Open decisions

1. **Auth-token storage.** Env var named in config (`token_env`, recommended — mirrors model keys and
   `credentials.key_env`, right role, never persisted) vs. a selfhost credential slot (**rejected** —
   that's workspace BYOK for the sandbox egress proxy, wrong role and lifecycle) vs. ssh deploy key
   via agent (supported for `git+ssh://`, zero selfhost secret handling).
2. **Boot-time vs. install-time resolve.** Recommend **install-time** (keeps boot offline +
   deterministic, keeps the bundle vendorable, keeps GitHub off the serve hot path). Boot-time resolve
   would put a network fetch on `serve` startup and break offline/air-gapped deploys — rejected.
3. **`[[extensions]]` array vs. CLI-only.** Recommend **both**, converging on one lockfile pin. Open:
   whether reconcile is a dedicated `ext sync` or folded into `migrate` (which already re-runs after
   `ext install` per `spec.md:163`).
4. **Monorepo subdir extensions.** `#subdirectory=` is native to uv/pip; expose as the `subdirectory`
   config field. The digest still covers only the installed package's source — unaffected.
5. **Ref vs. commit pinning.** Config names a ref (branch/tag/commit); resolve **freezes the concrete
   commit** into `source`. Open: should `ext sync` warn (or refuse) when a lockfile'd branch ref has
   advanced upstream, or silently keep the frozen commit until an explicit re-install?
6. **Extension store's role for git sources.** Recommend git sources **bypass the catalog** (a direct
   source, like `pip install git+`), pinning straight into the lockfile so they work with the store
   off. Open: whether a catalog entry may optionally carry a `source` git URL so `ext search` can
   surface curated private extensions.
