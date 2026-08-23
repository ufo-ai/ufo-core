---
rfc: 0011
title: "ufo — the merged hosted service: one repo, one database, RLS"
status: implemented
date: 2026-07-07
---

# ufo — the merged hosted service: one repo, one database, RLS

> Merge `~/src/selfhost-core`, `~/src/selfhost-k8s`, and metalcraft's onboarding + `ufo` CLI into
> one **closed-source** repo, **`ufo`** (`metalcraftai/ufo`), that is the hosted service at
> `flyingobject.ai`. It replaces the metalcraft deploy. The runtime takes the product's name —
> **selfhost → ufo everywhere**. Tenancy is **one Postgres database shared across tenants, isolated
> by RLS** — porting metalcraft's shipped pattern (`metalcraft_contracts/db.py`) onto core's
> `workspace_id`. All tenants run **one extension set**: the `assistant_hosted` pack. The
> onboarding backend is itself an **extension** running on the public seam, and the onboarding
> script keeps its address: `curl -fsSL https://flyingobject.ai/ufo | sh`. This RFC settles what
> 0002/0003/0004 left open (repo topology; the Postgres tier) and is the executable plan — no
> decisions remain.

## Current state

Three repos, each holding a piece the service needs; none is the service.

| Repo | Has | Lacks |
|---|---|---|
| `selfhost-core` | the runtime (`serve`, one workspace/process), the `assistant_hosted` pack (19 exts), `selfhost deploy` → `DeployRequest`/`POST /v1/deploy` (`deploy.py`, `cli.py:680`), RFCs 0002–0004 | any tenancy above one workspace; RLS (`spec.md:336` scopes it out of core); a ufo surface |
| `selfhost-k8s` | the control plane (~700 lines): `api.py` (`/v1/deploy`, `/v1/tenants/{name}`), operator + `Tenant` CRD, tenant Helm chart, `postgres.py` DB-per-tenant | the RLS tier (`ensure_tenant_postgres` raises `NotImplementedError`); onboarding; a domain; CI; has never touched a live cluster |
| `metalcraft` | the shipped service: `scripts/ufo` (855-line POSIX renderer, byte-identical to `gateway/channels/ufo`), `GET /ufo` version-stamped serving (`gateway/server.py:106,213`), email-code onboarding (`metalcraft_cloud/onboard/`), **single-DB RLS in production** (`metalcraft_contracts/db.py`, `store/migrations/001_product_tables.sql:378-515`), 7 product CRDs (`Store, Source, Tool, Agent, Channel, SpendPolicy, Refinement`), the AWS substrate (EKS/RDS/Redis/S3/SES/Cloudflare, `infra/`) | the selfhost runtime — it is the previous architecture this repo replaced |

Facts the design leans on:

- Core isolation today is `workspace_id` WHERE-discipline; `serve` resolves **the sole workspace
  row at boot** (`serve.py:259-261` `.scalar_one()`). Every core table carries `workspace_id`
  except `workspace` itself; of the pack-active extension tables only `mem_page`
  (`extensions/memory/.../migrations/0002_mem_page.py`) lacks it. `chunk` belongs to
  `index_default`, which `assistant_hosted` excludes, and pack-narrowed migrations
  (`db.py:83-99`) never create it.
- DBOS lives in a **derived sibling database** — `DatabaseConfig.system_url` is a `@property`
  computing `<dbname>_dbos` (`config.py:23-30`); with one shared app DB every tenant would derive
  the *same* system DB and cross-recover each other's workflows.
- `selfhost init --email` mints the workspace id internally (`onboarding.py:148-179`) and raises
  `AlreadyInitialized` if any visible workspace row exists.
- The data model already holds **many agents per workspace** (`agent` UNIQUE(workspace_id, name),
  `tables.py:37`) with **per-agent connections** (`grant.agent_id`, `tables.py:147`;
  `connect_account` binds the speaking turn's agent, `tools/builtins.py:629-644`), and admission
  is agent-parameterized (`surfaces/admission.py:51,172`) — but every surface admits to the one
  `DEFAULT_AGENT_NAME` agent (`ext/surface.py:172-184`). The binding primitive is the gap (§5).
- metalcraft's RLS: every table has `namespace text not null` + policy
  `namespace = current_setting('metalcraft.namespace', true)`; the **table-owner role bypasses RLS
  by design** (platform workers), a **non-owner app role is RLS-subject**; DBOS gets its own
  schema; the pre-tenant `onboard_claim` table sits in its own schema without RLS.
- flyingobject.ai runs on AWS (`899147036157`, us-east-1): `testing.flyingobject.ai` is live
  metalcraft; **prod substrate exists with the app disabled** (`infra/envs/prod`,
  `enable_app = false`). GitHub Actions is billing-blocked, so today's builds and applies run from
  an operator machine.

## Proposal

### 1. The repo — and the name

`metalcraftai/ufo`, private. selfhost-core's tree **is the root** (history preserved); the control
plane and infra join it:

```
ufo/
  core/ extensions/ packs/ docs/ spec.md …   ← selfhost-core, merged with full history
  servers/control/                                    ← selfhost-k8s, subtree with history
    src/ufo_control/                          ← + the rls arm, the members write, the gateway role (§4)
    charts/ufo-tenant/
  extensions/ufo/                             ← the member surface (§4)
  infra/                                      ← metalcraft Terraform, copy-adapted (prod env only)
```

Mechanics: `git init` → `git fetch <selfhost-core> && git merge --allow-unrelated-histories` →
`git subtree add --prefix control <selfhost-k8s> main` → metalcraft pieces arrive by **copy-adapt**
(no history; salvaged code arrives as if written here). One uv workspace; `gates.py`, ruff, and
the test suites run at the root. The hand-mirrored contract collapses: `servers/control/` deletes its
`contract.py` and imports the runtime's `DeployRequest` — one definition, one shape. After `ufo`
main is green, `selfhost-core` and `selfhost-k8s` are archived read-only; all development moves
here.

**The rename** — in this repo the runtime is named for the product; `selfhost` survives nowhere:

| Today | In `ufo` |
|---|---|
| package `selfhost` (`core/src/selfhost`) | `ufo` (`core/src/ufo`) |
| `selfhost_ext_<name>` / `selfhost_pack_<name>` | `ufo_ext_<name>` / `ufo_pack_<name>` |
| entry-point groups `selfhost.extension` / `selfhost.pack` | `ufo.extension` / `ufo.pack` |
| `selfhost.sdk` (+ the SDK import gate) | `ufo.sdk` (gate constant follows) |
| operator CLI `selfhost` | **`ufoctl`** — the bare `ufo` belongs to the member client (`~/.ufo/bin/ufo`); two audiences, two binaries |
| `selfhost.toml` / `selfhost.lock` | `ufo.toml` / `ufo.lock` |
| env `SELFHOST_*` (`CONFIG`, `LOCKFILE`, `CREDENTIAL_KEY`, `ARTIFACT_TOKEN_SECRET`, `E2B_*`) | `UFO_*` |
| DBOS application name `selfhost` (`serve.py:146-153`) | `ufo` |
| images `selfhost` / `selfhost-sandbox` (+ control plane) | `ufo` / `ufo-control` — two ECR images (§4 removes the bespoke gateway); the hosted sandbox artifact is the **E2B template `ufo-sbx`** (same layer definition the docker carrier renders for self-host) |
| chart `selfhost-tenant`; namespaces `selfhost-system` / `selfhost-<name>` | `ufo-tenant`; `ufo-system` / `ufo-<name>` |
| CRD `tenants.selfhost.sh`; labels `selfhost.sh/*` | `tenants.flyingobject.ai`; labels `flyingobject.ai/*` (the owned domain, metalcraft's `metalcraft.ai` precedent) |
| package `selfhost_k8s` | `ufo_control` |

It lands as one mechanical commit inside unit A — directory moves plus a bounded-token sweep —
proven by the gates and both test suites going green immediately after. No aliases, no
compatibility shims, no dual names: one shape. Git history keeps the old names; the tree never
does. The rest of this RFC cites today's code by its current names and names deliverables in ufo
terms.

### 2. Tenancy: one database, RLS on `workspace_id`

The topology is RFC 0003 §5's dense tier, now the only tier: **namespace-per-tenant pod-sets, each
a whole single-workspace `ufoctl serve`, all sharing one Postgres database** (`ufo`). RLS makes
the shared database *present a single-workspace view* to each tenant's connection — core keeps
believing it owns the database, `_sole_workspace_id()` still returns exactly one row, and core
never learns RLS exists. This is metalcraft's shipped pattern with `workspace_id` as the tenant
key instead of `namespace`.

**Roles** (provisioned by `servers/control/`'s `postgres.py`, replacing the `NotImplementedError` arm):

| Role | Kind | Rights | Used by |
|---|---|---|---|
| `ufo_owner` | LOGIN, table owner | DDL; **bypasses RLS as owner** (metalcraft precedent: platform workers) | alembic migrations, RLS bootstrap, control-plane cross-tenant reads/writes |
| `ufo_app` | NOLOGIN group | USAGE on schema; SELECT/INSERT/UPDATE/DELETE on all tables (+ default privileges); USAGE on sequences; SELECT on `alembic_version` | grant anchor for tenant roles |
| `ufo_t_<name>` | LOGIN, `IN ROLE ufo_app` | RLS-subject; password = sha256(seed ‖ name) (selfhost-k8s scheme, nothing persisted) | the tenant's serve pods and init Job |

**The pin**: `ALTER ROLE ufo_t_<name> IN DATABASE ufo SET app.workspace_id = '<uuid>'` — applied
at provisioning, effective at login, so the DSN alone scopes every connection and **core needs no
session-variable code**. Threat model, stated honestly: a session can re-`SET` its GUC, so this
RLS isolates against *application bugs* (a missing WHERE, a cross-tenant leak), not against a
hostile pod — pods run only platform code; agent code runs in E2B sandboxes with no DSN. The same
posture metalcraft shipped. (Hardening beyond it: key policies on `current_user` via a role→
workspace map — in the ledger below, not today.)

**Policies** — the bootstrap runs as `ufo_owner` after every migrate, and is the enforcement gate:

```
for every table in the app schema except alembic_version:
    workspace table  → USING/WITH CHECK (id = current_setting('app.workspace_id')::uuid)
    has workspace_id → USING/WITH CHECK (workspace_id = current_setting('app.workspace_id')::uuid)
    neither          → FAIL LOUD: the deploy aborts naming the table
```

Any future migration adding an unpoliced table breaks the deploy, not the isolation — enforce,
don't document.

**Migrations** run **once per bundle rollout, as `ufo_owner`**, via a cluster-scoped migrate Job
(`ufoctl migrate` + RLS bootstrap), because tenants share one schema. The per-tenant init Job
still calls `apply_migrations` — at head it is a no-op needing only SELECT on `alembic_version`.

**DBOS** stays per-tenant: shared bookkeeping would let one tenant's pod recover another's
workflows. The control plane creates `ufo_dbos_<name>` owned by `ufo_t_<name>` and renders an
explicit system URL; init's `_create_postgres_system_database` sees it exists and skips, so tenant
roles need no CREATEDB.

**Core seam additions** (the one unit of core change; RFC 0004's rule — a needed core change is a
seam bug fixed in core's scope — and both are producer+consumer complete in this plan):

| Seam | Change | Producer / consumer |
|---|---|---|
| `[database] system_url` | optional explicit field on `DatabaseConfig`; the `_dbos` derivation becomes its default (`config.py:19-30`) | `servers/control/render.py` writes it per tenant / `serve.py:120,146` + `cli.py:131-142` read it |
| `ufoctl init --workspace-id` | optional; `Onboarding` uses the supplied uuid for the workspace row (`onboarding.py:148-167`, `cli.py:68-91`) | control plane mints the uuid, pins the GUC to it, passes it to the init Job / the workspace INSERT must equal the GUC or RLS `WITH CHECK` rejects it |

**Schema gap**: `mem_page` gains `workspace_id` (memory extension migration `0004`, NOT NULL, FK
CASCADE) and the extension's writer populates it from its context — both ends in one change. New
deploys start empty, so no backfill path is needed.

**Everything else already fits**: `credential` is Fernet-sealed per workspace with a per-tenant
minted key, so even `ufo_owner` reads only ciphertext across tenants; OAuth grants hold no tokens
(Composio does); `ledger`, `spend_cap`, turn/queue state are all workspace-keyed.

### 3. Runtime topology and shared services

Per tenant: a namespace `ufo-<name>`, the tenant chart (serve Deployment + HPA + Ingress + init
Job + quota + default-deny NetworkPolicy), one rendered `ufo.toml` Secret. Shared, with the
isolation each brings:

| Service | Shared as | Isolated by |
|---|---|---|
| Postgres (RDS) | database `ufo` + per-tenant `ufo_dbos_<name>` | RLS (§2); separate DBOS DBs |
| Redis (ElastiCache) | one instance, `[hub] backend="redis"` | hub channels keyed by turn/conversation uuids; per-tenant ACL prefixes in the ledger |
| S3 | one bucket, `[blob] backend="s3"` | uuid-addressed keys; sandbox mounts already STS-scoped per conversation (`sandbox/fs_creds.py`); per-tenant IAM prefix scoping in the ledger |
| Turbopuffer | platform key seeded into each workspace's `turbopuffer_api_key` slot at init (`onboarding.py:43-51` env seeding) | index namespaces keyed by workspace-scoped owner ids |
| E2B | platform `UFO_E2B_API_KEY` + template | one sandbox per conversation, off-cluster, egress via core's proxy |
| Composio | platform `COMPOSIO_API_KEY` | per-workspace connected accounts; broker holds tokens |
| OpenRouter | platform `OPENROUTER_API_KEY` (`extensions/openrouter` reads env) | spend metered per workspace (`ledger`) |
| Model keys | `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` platform Secret, replicated per namespace (existing selfhost-k8s mechanism) | spend metered per workspace (`ledger`) |

**Provider keys are platform-level for every tenant** — onboarding never asks for one. Two
mechanisms, both existing: env-read providers (model keys, OpenRouter, Composio, E2B) get the
platform Secret's env directly; slot-backed providers (Turbopuffer, Exa) get the platform value
env-seeded into the workspace's credential slot at init (`onboarding.py:43-51`). Per-service admin
BYOK comes later, in chat (the credential-write side of the parked member/access seam): slot-backed
providers already support the override — writing the slot replaces the seed; env-read providers
first need promotion to workspace credential slots, which is why BYOK sits in the hardening ledger
rather than this deploy.

**One extension set**: the rendered config pins `[pack] name = "assistant_hosted"` and
`[memory] index_backend = "turbopuffer"` (the pack excludes `index-default`); the pack gains the
`ufo` member surface (§4), making it 20 extensions, and the bundle's `ufo.lock` digest-pins
exactly that set. Every tenant runs the same bundle image; there is no per-tenant extension
choice — that is the product.

`DeployRequest.postgres` stays `"database"` from the CLI (`deploy.py:160`); the tier is chosen
server-side as `deploy.py:159` already states — `servers/control/` platform config sets
`postgres_model = "rls"` and that wins. The compose backend and `ufoctl deploy` keep working
unchanged for self-hosters.

### 4. The ufo member surface (extension) and the onboarding backend (control-plane role)

Two producers of the same `DeployRequest` contract: the `ufoctl deploy` CLI (operators, over the
deploy API `POST /v1/deploy`) and the **gateway role** (members, below). The gateway lives inside
the control-plane package, so it applies the Tenant CR directly through `KubeClient` rather than
over HTTP. Both end at Tenant CR → operator reconcile.

**The client** (`servers/control/src/ufo_control/client/ufo` — force-included in the control wheel so the
gateway role serves it at runtime): metalcraft's script copied verbatim, then two mechanical adapts
— (a) the `workspace` directive's value becomes the tenant's base URL
(`https://<name>.flyingobject.ai`), written to `~/.ufo/workspace`; chat posts go to
`{WORKSPACE_URL}/surface/ufo/{channel}`; (b) onboarding stays at the apex
(`https://flyingobject.ai/v1/onboard/{channel}`). The directive grammar (`say`/`note`/`txt`/
`status`/`ask`/`choose`/`poll`/`token`/`workspace`/`install`/`sendfile`/`exit`), the
`~/.ufo/credentials` handling, and the `UFO_SCRIPT_VERSION` sha-stamp self-reinstall are untouched.

**The member surface** (`extensions/ufo`, per RFC 0003 §6): a live surface on the `surfaces` seam
in every tenant — verify the bearer (`UFO_TOKEN_SECRET`, replicated into tenant namespaces;
workspace claim must match), resolve the member through `surface_identity`/`link_member` (the
owner exists from init; joined members from the control plane, below), `admit` the body, `tail`
the hub through a directive codec (`LiveFrame` → `txt`/`say`/`note`/`status`/`ask`/`exit`; hold
≤85s then `poll`; empty body = poll). It imports only `ufo.sdk` and runs identically on a laptop
against SQLite.

**The onboarding backend is a control-plane server role** (`ufo-control gateway`, copy-adapted from
`metalcraft_cloud/onboard/`): the apex registers only `routes` — no agent loop, no turns, no DBOS —
so it is not an `ufoctl serve` workspace but a plain uvicorn app in the `ufo-control` image, one
Deployment behind the apex Ingress. It applies Tenant CRs and reads their status through the same
`KubeClient` the operator uses, and writes the pre-tenant claim ledger as the Postgres owner:

| Need | How |
|---|---|
| `GET /ufo`, `POST /v1/onboard/{channel}` | served directly by the role (uvicorn, `UFO_GATEWAY_PORT`, default 8080); the apex Ingress routes the `flyingobject.ai` host at its Service — no path rewrite |
| the claim machine (email → 6-digit code; SES; hash-only storage, 15-min TTL, 5 attempts, constant-time compare) | `onboard_claim` is a **control-plane platform table** in the `ufo_control` schema of the shared DB — pre-tenant, so it is owned by `ufo_owner`, never granted to a tenant role, never RLS-policed; the role creates schema + table idempotently at startup (no alembic migration); async SES via HTTP + SigV4 |
| provision (0 tenants for the domain) | mint a deterministic per-domain tenant name (`<slug>-<sha8>`), assemble a `DeployRequest` (platform image digest, `pack="assistant_hosted"`, owner = claimant), `KubeClient.apply_tenant` the Tenant CR, hold the stream on `status` directives while polling `status.workspaceId` to Ready — **the identical contract `ufoctl deploy` speaks**; the operator mints and persists the workspace uuid |
| join (1 tenant) / >1 → escalate | `members.add_member` in-process (runs as `ufo_owner`, inserts the `member` row) — cross-tenant authority stays in the one closed service; the `POST /v1/tenants/{name}/members` API endpoint remains for external callers (RFC 0003 open decision 3; both become clients of the member/access seam when it lands) |
| sign-in completion | emits `token <hmac>` (30-day, stateless, `UFO_TOKEN_SECRET`, payload `{ws, email, exp}`) + `workspace https://<name>.flyingobject.ai` |

The tenant shared DB carries only `assistant_hosted`'s tables — no pack union, no onboarding schema.
This keeps the hosted apex on the same two images (`ufo` / `ufo-control`, no bespoke gateway image)
and puts onboarding where its cluster authority — Tenant apply and owner-role writes — already
lives, rather than spinning a whole agent runtime for two routes.

### 5. Metalcraft's product primitives — the import ledger

metalcraft modeled the product as 7 CRDs so one tenant could run **multiple agents with different
connections, surfaces, and stores**. Core already carries most of that as rows; CRDs and the
reconcile machinery stay dead (RFC 0003: the DB schema + alembic *is* the store). What imports is
the one concept core lost:

| metalcraft CRD | In core today | Import? |
|---|---|---|
| `Agent` (N per tenant) | `agent` rows, UNIQUE(workspace_id, name) (`tables.py:37`); turns bind agent_id | concept present; **creation-in-chat ships with unit H** |
| `Channel` (binds a surface channel to an agent) | **missing** — surfaces hardcode `default_agent()` (`ext/surface.py:172-184`) though `admit(agent_id)` is already parameterized | **YES — unit H**: a `channel_binding` row (workspace, surface, queue_key → agent), consulted at admission with default-agent fallback, managed by chat tools (create agent, bind channel) — member actions in chat, never CRD CRUD |
| `Tool` (`connector://` per tenant) | grants are per-agent (`grant.agent_id`); `connectors` = dynamic Composio tools; `mcp_servers` slot | no — already rows |
| `Store` (named stores, per-store index) | one memory subsystem with `subject` scoping + `IndexScope`; blob store | no — subject/index-scope covers it; named per-agent stores go to the ledger if the product ever demands them |
| `Source` | `source` rows + sync jobs | no — already rows |
| `SpendPolicy` | `spend_cap` rows + `SpendEvaluator` (`accounting.py:312-343`) | no — already rows (default seed in the ledger) |
| `Refinement` | `proposal` rows + `self_improvement` | no — already rows |
| operator + SSA + level-triggered reconcile of the 7 | nothing to reconcile; only the `Tenant` CRD exists | no — dead by design |

So "multiple agents, with different connections/surfaces/stores" decomposes as: connections are
per-agent **today**; agents are N-per-workspace **today**; surface binding is **unit H**; stores
stay unified under subject scoping until a real product demand says otherwise.

### 6. Deploy and cutover — today

Stage 1 targets the **testing environment** (`infra/envs/testing`): ufo **replaces the live
metalcraft app at `testing.flyingobject.ai`** on the substrate already running there — EKS, RDS,
ElastiCache, S3, SES, Secrets Manager, Cloudflare. Prod (`infra/envs/prod`) stays dark and
untouched; flipping `flyingobject.ai` is a later repeat of the same module work once testing has
soaked. `infra/` arrives from metalcraft minus the metalcraft app (platform Helm release,
cloud-gateway overlay); added: ingress-nginx (the tenant chart's ingress class), a cert-manager
**DNS01** ClusterIssuer through the existing Cloudflare credentials (Cloudflare proxying breaks
HTTP01), the `Tenant` CRD, `ufo-system` (control-plane API + operator), ECR repos for the two
images. Hostnames thread as variables — apex `testing.flyingobject.ai`, tenants
`<name>.testing.flyingobject.ai` — never hardcoded, so the prod repeat is a tfvars change. GitHub
Actions is billing-blocked: the ported `deploy.yml` lands dormant; today's builds and applies run
from the operator machine (docker buildx + `terraform apply`, OIDC-independent AWS creds).

Cutover sequence:

1. Repo bootstrap + rename (§1); land the execution units below; ufo main green (gates, ruff,
   pytest).
2. Build + push, digest-pinned: bundle (`ufo.lock` pinning assistant_hosted + its extensions) and
   control-plane — two ECR images; the apex runs the control-plane image with the `gateway` role.
   Publish + boot-verify the `ufo-sbx` E2B template (`sandbox/build_template.py`,
   needs `E2B_API_KEY`); tenants reach it via `UFO_E2B_TEMPLATE`.
3. Secrets Manager entries → External Secrets: pg admin DSN + role seed, model keys, OpenRouter,
   Composio, E2B (+ template), Turbopuffer, Exa, SES sender, `UFO_TOKEN_SECRET`.
4. RDS: create `ufo` DB + `ufo_owner`; run the migrate-and-RLS-bootstrap Job.
5. `terraform apply` testing (this removes the metalcraft app and installs ufo in one apply);
   verify control plane healthy (`/healthz`), operator holds the Lease; the `gateway` Deployment is
   up behind the apex Ingress (host `testing.flyingobject.ai`) and serves `GET /ufo`.
6. Cloudflare: `testing.flyingobject.ai` → the gateway Service's ingress (proxied,
   source-restricted to CF ranges, the existing pattern); `*.testing.flyingobject.ai` → tenant
   ingress LB.
7. Smoke, in order: `curl -fsSL https://testing.flyingobject.ai/ufo | sh` → onboard with a real
   email →
   watch the Tenant CR reconcile (namespace, role + GUC, `ufo_dbos_*`, init Job, serve Ready) →
   chat turn round-trips live → **RLS proof**: onboard a second domain, `psql` as each tenant role
   and assert zero cross-visibility → `connect_account` OAuth round-trips → browser tool boots
   under the E2B carrier (else set `BROWSER_CDP_URL` per `serve.py:419-424` before opening
   traffic).
8. Nothing is migrated — testing held only metalcraft test tenants and prod never served members.
   Archiving of the two source repos happens after soak; the prod (`flyingobject.ai`) flip is the
   same terraform against `envs/prod` once testing has soaked.

Rollback at any step: `terraform apply` of metalcraft's testing configuration restores the old
app at `testing.flyingobject.ai` (its images remain in ECR and its DB/schema are untouched — ufo
uses its own `ufo` database and roles).

## Doctrine fit / implications

- **Core stays core.** RLS remains outside core (`spec.md:336` holds): core code never reads a
  GUC, never sees a policy; the two seam additions are config-shaped knobs with both ends shipped.
  The k8s import ban, the SDK gate, and one-workspace-per-process all stand unchanged.
- **The hosted service dogfoods the seam.** Onboarding — the most product-specific thing the
  service does — is an extension importing only `ufo.sdk`, deployed as a tenant of its own control
  plane. If the seam can't express the product, that is a seam bug found immediately.
- **Every member action stays in chat.** Onboarding is the chat bootstrap (the same wire the
  client renders); the only public endpoints are the chat transport (`/surface/ufo/*`,
  `/v1/onboard/*`) and script delivery (`GET /ufo`) — no admin HTTP surface; provisioning drives
  core via its own CLI in Jobs; agent/channel management (unit H) is chat tools, not CRD CRUD.
- **One shape.** One repo, one name (`ufo` — the runtime is not called something else than the
  product), one `DeployRequest` definition, one extension set, one database, one client script.
- **Copy-adapt salvage.** The client script, claim workflow, RLS pattern, and Terraform arrive by
  copy + mechanical adapt from metalcraft, never rewritten from understanding.
- This RFC **supersedes** 0002/0004's open decisions on repo split and Postgres tier, and 0003 §7
  decisions 1–3. The compose backend and self-host story are untouched; re-publishing the runtime
  later is a subtree split away and nothing here blocks it.

## Alternatives

- **One shared multi-tenant serve process** (metalcraft's gateway/executor shape): rejected —
  requires per-request workspace resolution through the whole runtime, destroying core's
  one-workspace invariant; RLS-per-connection gives density without the re-architecture.
- **Database-per-tenant** (selfhost-k8s's built path): rejected by owner decision — the shared-DB
  RLS tier is the product's cost structure; DB-per-tenant remains expressible later for an
  enterprise tier since the tier is chosen server-side.
- **Keep three repos / keep the `selfhost` name**: rejected by owner decision — one closed repo,
  one name; the OSS boundary survives as gates inside it, not as repo topology or naming.
- **A bespoke apex gateway image** (metalcraft's `cloud-gateway` shape): rejected — a separate
  service would fork serve's ops story and add a fourth image; the apex is a role in the existing
  `ufo-control` image (§4), sharing its `KubeClient` and owner DSN, not new infrastructure.
- **Product objects as CRDs** (metalcraft's 7 kinds): rejected — the DB is the store; member
  actions happen in chat; the operator reconciles exactly one kind, `Tenant` (§5).
- **Apex-proxied chat** (preserve the script byte-for-byte, proxy `/v1/cli/*` to tenant pods):
  rejected — an extra stateful hop per turn; the `workspace`-directive-as-URL adapt is mechanical
  and the client already persists it.

## Execution plan

Units land in order, each independently reviewable, each with its proof; B–F parallelize after A.

| Unit | Delivers | Proof |
|---|---|---|
| **A. repo bootstrap + rename** | history-preserving merge (§1), uv workspace, root gates/CI config, dormant `deploy.yml`; then the one mechanical selfhost→ufo commit (§1 table) | gates + ruff + both test suites green at root, again after the rename; `git grep -i selfhost` returns only history-facing docs |
| **B. core seams** | `[database] system_url` field; `init --workspace-id` | unit tests: explicit system_url wins over derivation; init with supplied uuid creates that workspace id |
| **C. memory gap** | `mem_page` migration 0004 + writer populates `workspace_id` | migration test + writer test against Postgres |
| **D. RLS tier** | `servers/control/postgres.py` rls arm (roles, GUC pin, `ufo_dbos_*`), policy bootstrap, migrate-as-owner Job, `render.py` emits tenant DSN + system_url + workspace id | pytest against real Postgres (Docker): two tenants provisioned; per-role connections see disjoint single-workspace views; unpoliced-table case fails loud |
| **E. `extensions/ufo`** | member surface + directive codec + HMAC bearer verify; added to `assistant_hosted` (→ 20) | focused tests: codec frame map; token verify + member link; admit/tail against the web-surface pattern; pack test asserts the set |
| **F. `ufo-control gateway` role + members write** | the claim machine (control-plane `onboard_claim` table, no RLS; SES; directives), join-or-provision applying the Tenant CR through `KubeClient`, token mint; `members.add_member` (also exposed as `POST /v1/tenants/{name}/members`) | claim-flow tests (code hash, TTL, attempts); provision assembles a valid `DeployRequest`; join test drives the member write against real Postgres and the surface links the new member |
| **G. ship** | images, Terraform apply, DNS, smoke, cutover (§6) | the §6 smoke list, executed in order |
| **H. channels + agents** *(first post-cutover unit)* | `channel_binding` rows consulted at admission (default-agent fallback); chat tools to create an agent and bind a channel (§5) | two agents bound to two Slack channels route their turns respectively; unbound channel falls back to the default agent |

Hardening ledger (explicitly not today, none load-bearing for isolation as shipped): policies
keyed on `current_user` instead of the GUC; per-tenant Redis ACLs; per-tenant S3 IAM prefix
scoping; a default `spend_cap` seeded by the init Job; per-service admin BYOK in chat (slot writes
already override the Turbopuffer/Exa seeds; promote the env-read providers — model keys,
OpenRouter, Composio, E2B — to workspace credential slots); named per-agent stores on the
subject/index-scope seam if the product demands them; Slack sign-in for onboarding (metalcraft's
was unwired too); reviving `deploy.yml` when Actions billing unblocks.

## Open decisions

None — the owner fixed repo topology, naming, tenancy tier, extension set, key policy, and
address; everything else above is derived. Follow-ups live in the hardening ledger and unit H.
