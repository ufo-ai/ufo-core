---
rfc: 0002
title: "Deploy service (proposal): `selfhost deploy` + a hosted layer"
status: proposed
date: 2026-07-06
---

# Deploy service (proposal): `selfhost deploy` + a hosted layer

Status: **proposal, not adopted.** Nothing here is built. It extends `spec.md` (§Deploy config
bundling, §Running it, §Scale-out, §Roles) with a one-command deploy and a Heroku-style hosted
layer, and names the control-plane pieces core deliberately does not carry.

The thesis: **core already emits every runnable seam a deploy needs — the artifact, the config, the
boot guard, the role split.** What is missing is not core machinery but a control plane *around* it:
provision the backing services, run the process, terminate TLS, route a hostname, ship logs. Per
doctrine (`spec.md` fixed decisions: *Kubernetes absent from core by construction*; *no router
service in core*) that control plane is a **layer**, never core. `selfhost deploy` mirrors `selfhost
bundle`: it generates a runnable recipe and can execute it, but imports no orchestrator.

## What ships today (the seams already paid for)

| Seam | Where | What it gives a deploy |
|---|---|---|
| `selfhost bundle` | `bundle.py` `Bundle.build` | A `docker build` context: a Dockerfile (`FROM python:3.12-slim`, `pip install selfhost==X` + each pinned extension, `COPY selfhost.toml selfhost.lock`, `ENTRYPOINT ["selfhost","serve"]`), the pinned `selfhost.toml`, and `selfhost.lock` (version + per-extension digest). Same artifact boots OSS/on-prem/hosted. |
| Sandbox image | `sandbox/build_template.py --build-docker` | Renders one image definition to `selfhost-sandbox:latest` (the tag `loop/queue.py:SANDBOX_IMAGE_REF` runs) and, separately, publishes the E2B template — one definition, drift-gated. Offline, no E2B key. |
| One config file | `config.py` `Config` | `selfhost.toml` validates every knob at load, `extra="forbid"`: `[database]` url, `[blob]` (fs root or S3 bucket/endpoint/STS), `[models]` key-env names, `[credentials] key_env`, `[serve] host/port`, `[connect] public_base_url`, `[o11y] otlp_endpoint`, `[sandbox] backend`, `[hub] backend/url`, `[browser]/[connectors]/[research]/[pack]`, `[ext] store`, `[[sources]]`. |
| Schema provisioning | `cli.py` `init`/`migrate`, `db.py` `apply_migrations` | `init` creates the DBOS system DB on Postgres (`_create_postgres_system_database`), runs `upgrade heads` over core + every pinned extension's migration branch, onboards workspace + owner + agent + model key, binds a CLI token. `migrate` re-runs after an `ext install`. |
| Run model | `serve.py` `run` | One process, one event loop: FastAPI/uvicorn + DBOS workers + jobs + the egress proxy (on its own loop thread). Fail-loud boot: schema present, `_validate_requires` resolves every seam, model key present, backends resolve. |
| Route mounts | `serve.py`, `surfaces/cli.py` | `/v1/*` (CLI: `chat`, `turns/{id}/stream`, `cancel`, `connect/callback`), `/artifacts/*` (TTL token URLs), `/ext/<name>/*` (webhooks, OAuth), `/surface/<name>/*` (Slack, web). |
| Scale-out guard | `runtime_instance.py` `BootGuard`/`Heartbeat` | Each instance heartbeats a `runtime_instance` row; a second instance carrying any single-instance backend (SQLite, filesystem blobs, in-process hub) refuses to boot while a peer is live. Shared Postgres + S3 + Redis hub → peers coexist. |
| Extension store | `cli.py` `ext`, `ext/store.py` | `search`/`install`/`remove` pin digests into `selfhost.lock`; disabled catalog entries are bundle-only. |

**The reachable conclusion:** from a bundle image + a real Postgres/S3/Redis + the right env, a
selfhost system boots, serves, and scales today. Everything below is the gap between "have the
image" and "one command, running and addressable."

## What a deploy operator does by hand today

A production deploy (Postgres, S3, Redis, Slack, connectors) is a manual runbook:

1. Provision Postgres (with pgvector), an S3 bucket, an STS role (`sts_role_arn`) scoped to the
   bucket for the sandbox-fs mount, and a Redis instance. `compose.yaml` only spins a **dev**
   Postgres (`pgvector/pgvector:pg17`, plaintext creds) — nothing for S3/Redis.
2. Build + push the bundle image and the sandbox image to a registry the host can pull.
3. Write `selfhost.toml` for the target: `[database] url`, `[blob] backend="s3"` (+ bucket, endpoint,
   region, s3_url, sts_endpoint, path_style), `[hub] backend="redis" url=redis://…`,
   `[sandbox] backend="docker"|"e2b"`, `[connect] public_base_url="https://…"`, `[pack] name`.
4. Inject env secrets by hand: `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`, `SELFHOST_CREDENTIAL_KEY`
   (Fernet — encrypts every BYOK slot at rest), `SELFHOST_ARTIFACT_TOKEN_SECRET` (HMAC for share
   links), AWS creds, `BROWSER_CDP_URL` (sandbox-cdp) or `E2B_API_KEY`+`SELFHOST_E2B_TEMPLATE`.
5. Run `selfhost init` against the DB (once), then `selfhost serve`.
6. Stand up a reverse proxy for **TLS** — uvicorn serves plain HTTP (`serve.py`: `uvicorn.run(app,
   host, port, log_level="warning")`, no TLS args), yet `connect.public_base_url` **must** be
   `https://` and non-bind or `_connect_redirect_uri` fails loud the moment a connector registers.
7. Point that proxy's TLS host at `public_base_url` so provider OAuth redirects reach
   `/v1/connect/callback`.
8. Ensure a Docker daemon on the host for the `docker` carrier — sandboxes are **sibling**
   containers reaching the proxy at `host.docker.internal` (`extensions/docker`); no DinD, ever
   (`spec.md` non-goal). Or select `e2b`/a remote carrier and need no host Docker.
9. Supervise the process (restart on crash), collect logs, point `[o11y] otlp_endpoint` at a
   collector, and connect Slack (bot token + signing secret land as BYOK slots, filled in chat).

## Have vs. missing — the control-plane ledger

| Piece | Have | Missing |
|---|---|---|
| **Build** | Bundle Dockerfile + sandbox image recipe | Image *build+push* to a registry; a host *pull+run* step |
| **Provision** | Schema/DBOS migrations, onboarding, dev Postgres compose | Postgres/Redis/S3/STS *creation*; secret *generation* (Fernet, HMAC) + storage |
| **Run** | `serve` one-loop process; fail-loud boot | Process *supervision* (restart, health, drain); a run recipe (compose/systemd/unit) |
| **Route** | Router mounts; `public_base_url`→callback derivation | **TLS termination**; DNS/hostname assignment; cert issuance |
| **Scale** | DBOS per-conversation queue; boot guard; heartbeat; roles-share-nothing gate | The **load balancer** in front of stateless surfaces; instance lifecycle |
| **Secrets** | Env-ref model keys; Fernet BYOK store; HMAC artifacts | A secret *manager*/injection; rotation; no plaintext-in-compose |
| **Observe** | OTLP export target (config only) | A shipped collector/dashboards; log egress |
| **Registry/router** | Single-workspace deploy; `workspace_id` on every row | Multi-tenant hostname→workspace routing (the enterprise layer) |

None of the "missing" column is core doctrine's to own. Each is a control-plane responsibility.

## Target UX

### Single box — `selfhost deploy`

An operator verb (the `selfhost` CLI is the operator surface; a member never deploys). Zero to a
running, addressable, TLS'd system on one VM:

```bash
selfhost deploy --host chat.acme.com --pack assistant --email you@acme.com
# 1. selfhost bundle              → image context + pinned config + lockfile
# 2. build sandbox image          → selfhost-sandbox:latest on this host
# 3. generate a compose stack      → serve + postgres(pgvector) + redis + caddy(TLS)
# 4. generate + store secrets      → Fernet key, artifact HMAC, prompt for model key
# 5. docker compose up -d          → the stack (with --up; else just writes the recipe)
# 6. wait-healthy, then selfhost init inside the serve container (schema + onboard)
# → prints:  https://chat.acme.com  · owner you@acme.com · first-run link
```

`deploy` **generates a recipe and only runs it under `--up`** — exactly as `bundle` writes a
Dockerfile it never builds. The generated compose is a plain artifact an operator can read, edit,
and run themselves; `--up` shells to `docker compose` (sync subprocess is fine at CLI startup, off
the serve loop). No orchestrator import enters `core/`.

### `git push`-style (hosted)

The hosted service treats a **pack repo** (a `packs/<name>/` + `selfhost.toml`) as the deployable:

```bash
git push selfhost main          # or: selfhost deploy --remote acme
# control plane: build bundle → assign acme.selfhost.app + cert → provision
#   Postgres schema + S3 prefix + Redis → inject secrets → run image → route
```

One push → one running workspace. A workspace = one deploy (`spec.md` Persistence: *a deploy serves
ONE workspace; hosted multi-workspace is the enterprise layer*), so "many workspaces" is many
deploys the control plane fans out, not RLS inside one process.

### First-run onboarding + surface/OAuth URLs

Onboarding is core (`onboarding.py`); the deploy's job is to reach it over the wire and surface the
URLs the flow needs:

| URL | Purpose | Source |
|---|---|---|
| `https://<host>/surface/web/…` | Web chat + first-owner claim | `web` surface; live tail over SSE |
| `https://<host>/surface/slack/…` | Slack events + writeback | `slack` surface; bot token/secret are BYOK slots |
| `https://<host>/v1/connect/callback` | OAuth return for `connect_account` | derived from `connect.public_base_url` |
| `https://<host>/ext/<name>/…` | Webhooks, plugin OAuth | extension `routes` |
| `https://<host>/artifacts/<token>` | `share_file` TTL delivery | HMAC-signed, no token no bytes |

`deploy` sets `public_base_url = https://<host>` so all five resolve; onboarding runs once
(`init`), the owner claims the workspace via the web link, and every subsequent capability
(connect an account, grant access) happens **in chat** — never a deploy step.

## Service architecture

Five stages. Each maps to existing seams; the new work is the control plane between them.

### Build — bundle → image

| Step | Mechanism | New for the service |
|---|---|---|
| Freeze | `selfhost bundle` (config + lockfile + Dockerfile) | — |
| Serve image | `docker build` the bundle context | build+push to a registry |
| Sandbox image | `build_template.py --build-docker` → `selfhost-sandbox:latest` | push; pin its digest beside the bundle |

Both images must reach the host that runs them. On one box: local build. Hosted: a registry the
orchestrator pulls from.

### Provision — Postgres / blob / secrets / carrier

| Resource | Config knob | Deploy provisions |
|---|---|---|
| Postgres (+pgvector) | `[database] url` | instance/DB; `init` creates the DBOS system DB + runs migrations |
| Blob | `[blob]` fs root **or** S3 bucket + `sts_role_arn` + endpoints | bucket + STS role scoped to the bucket (S3 backend is required for scale-out) |
| Hub | `[hub] backend/url` | Redis instance (required once >1 instance) |
| Carrier | `[sandbox] backend` | host Docker daemon **or** E2B key+template **or** a remote runner |
| Secrets | `key_env`/`token_secret_env`/model key envs | generate Fernet + HMAC once; store; inject as env |

Secret inventory a deploy must hold: `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`, `SELFHOST_CREDENTIAL_KEY`
(Fernet, seals the whole BYOK store — losing it orphans every stored grant), `SELFHOST_ARTIFACT_TOKEN_SECRET`,
AWS creds (S3/STS), and carrier keys. BYOK provider secrets (Slack, Composio, Exa) are **not** deploy
env — they land encrypted in `credential` rows through onboarding/chat.

### Run — the one-loop process

`serve` is one async process running all four roles (`spec.md` §Roles): **surfaces** (HTTP in,
streams out), **workers** (turn workflows), **jobs** (sync, derivation, reaping), **proxy** (sandbox
egress on its own loop thread). A blocking call stalls all four, so the deploy must not wedge the
loop — the service's contribution is *around* the process: a supervisor that restarts on crash and
drains on deploy. DBOS already recovers in-flight workflows on restart; the supervisor only has to
keep a live process (systemd unit, compose `restart: unless-stopped`, or the hosted orchestrator).

### Route — TLS, surfaces, `/ext`, `/connect`

| Concern | Today | Service adds |
|---|---|---|
| TLS | none (plain HTTP uvicorn) | terminating reverse proxy (Caddy/Let's Encrypt on one box; LB certs hosted) |
| Path routing | FastAPI mounts all paths in-process | none — one origin fronts everything |
| OAuth callback | `public_base_url`→`/v1/connect/callback`, https+non-bind enforced | ensure the TLS host == `public_base_url` |
| Hostname | operator-chosen | DNS record; hosted: wildcard `*.selfhost.app` + per-deploy cert |

TLS is external by construction: core stays HTTP, the proxy owns certs. This keeps cert plumbing out
of the one-loop process (a cert reload must never touch the turn loop).

### Scale — per-conversation queue + multi-instance hub

Scale-out is a deployment mode, not a feature (`spec.md` §Scale-out): the same bundle, more
instances, all four invariants already enforced.

| Concern | Multi-instance | Enforced by |
|---|---|---|
| ≤1 running turn / conversation | DBOS queue partitions on conversation key | `loop/queue.py` `TURN_QUEUE` (`concurrency=1`, `partition_queue=True`) |
| Turn/job recovery | any instance pulls; peers recover a crashed instance's workflows | DBOS on Postgres |
| Live deltas | shared Redis hub; terminal frames stay durable in Postgres | `[hub]` backend; `BootGuard` lifts the refusal only when shared |
| Blobs | S3 required (fs is single-instance) | `BootGuard._single_instance_backends` |
| Surfaces/webhooks | stateless behind a load balancer | sessions/idempotency in Postgres |

The service adds exactly one piece here: **the load balancer** in front of stateless surfaces. The
hard part (coordination) is already Postgres/DBOS's job.

## Doctrine — what stays out of core

- **No k8s in core.** The managed multi-tenant orchestrator is the enterprise layer wrapping core
  (`spec.md` fixed decisions + principle 3); nothing in `core/` imports or assumes it.
- **No router service in core.** Multi-tenant hostname→workspace routing lives in the control
  plane; core serves one workspace.
- **`deploy` generates, the layer executes.** Like `bundle`, `selfhost deploy` emits a recipe
  (compose/systemd/env) and imports no orchestrator; `--up` shells out at CLI startup (off the
  loop). Provisioning/supervision/TLS/log-egress are the layer's, not core's.
- **The four-role split is already paid for** (`spec.md` §Roles): roles share nothing in memory
  (Postgres/DBOS/blob/hub/proxy-HTTP only, import-gated), so the enterprise layer later splits them
  into per-role autoscaled deployments **by configuration, not code change**. The deploy service
  need not anticipate that split; it inherits it free.
- **Secrets never widen access.** The Fernet store and env keys stay the same seam; a secret
  manager is an injection source, not a new authority.

## Phased plan

| Phase | Deliverable | Lives in | Out of scope |
|---|---|---|---|
| **P0** (today) | Bundle image + manual runbook | — | everything below is manual |
| **P1** | `selfhost deploy` single-box: generate compose (serve+pg+redis+caddy) + secrets, `--up`, run `init`, print URL + owner link | operator CLI verb (generate-only in core; `--up` shells out); a `deploy` module beside `bundle` | multi-tenant, autoscale, k8s |
| **P2** | Managed hosted service: `git push`/`--remote` → build → provision (pg schema + S3 prefix + Redis) → assign host+cert → inject secrets → run image → route → scale behind shared hub | the **enterprise layer** (separate service; may use k8s) wrapping unchanged core | any core change — proof that P1's seams sufficed |

P1 is a thin generator + a compose stack; it is the "Heroku-on-one-box" story and the acceptance
test that the seams (bundle, config, boot guard, roles) are enough. P2 changes **no core code** — if
it needs a core change, that change is a seam bug to fix in P1's scope, not a P2 feature. The split
between them is exactly the OSS/enterprise line `spec.md` already draws.

## Open decisions

1. **`deploy` home** — a `deploy.py` beside `bundle.py` (core, generate-only), or a standalone ops
   tool outside `core/` (like `sandbox/build_template.py`)? (Recommend: generate-only in core,
   mirroring `bundle`'s no-execute discipline; `--up` shells out.)
2. **P1 supervisor** — `docker compose` (matches the dev `compose.yaml` and the sibling-container
   carrier) vs. a systemd unit (no host Docker unless the carrier needs it). Compose is the tighter
   fit given the docker carrier already assumes a daemon.
3. **TLS provider** — bundle Caddy (auto-Let's Encrypt) into the generated stack, or leave TLS to
   the operator and only emit the `public_base_url` contract? (Recommend: bundle Caddy in P1 so
   "one command, addressable" holds; operators can swap it.)
4. **Secret storage in P1** — a generated `.env` (gitignored) vs. a host secret store. `.env`
   matches today's `SELFHOST_CREDENTIAL_KEY` env-ref model; a store is a P2 concern.
5. **Registry** — does P1 build images locally on the box (no registry) or require a push target?
   Local build keeps single-box zero-dependency; hosted P2 needs a registry regardless.
6. **`git push` transport** — a real git remote (receive-hook builds), or `deploy --remote` posting
   the bundle to the control plane? (Recommend: `--remote` first; the git-remote is sugar over it.)
