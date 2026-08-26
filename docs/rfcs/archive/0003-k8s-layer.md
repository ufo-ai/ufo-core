---
rfc: 0003
title: "selfhost-core as the backend runtime for the `selfhost-k8s` control plane"
status: proposed
date: 2026-07-06
---

# selfhost-core as the backend runtime for the `selfhost-k8s` control plane

Status: **proposal, not adopted.** Nothing here is built. It fixes the shape of the enterprise
Kubernetes offering principle 3 promises (`spec.md:16` — "Kubernetes is the enterprise upgrade,
wrapping this core") so `selfhost deploy` (`0002-deploy-service.md`) can name a real backend.
It touches no core doctrine: `spec.md §Kubernetes` ("Absent from core by construction … nothing in
core may assume or import it"), the `no k8s imports anywhere` standing gate (`docs/plan.md:141`),
and the enterprise-layer entry on the post-U10 backlog (`docs/plan.md:137`) all stand. Reference
repo (read-only): `~/src/metalcraft`.

## TL;DR — the control plane is a repo, the runtime is core

The reframe: metalcraft's **full** Kubernetes control plane — operator + 7 CRDs + apiserver client +
namespace-per-tenant + Postgres-RLS + HPA autoscaling + jobrunner + the ufo/m8t gateway — is not
salvaged into selfhost. It is **rebuilt as a new closed-source repo, `selfhost-k8s`**, and
**selfhost-core becomes the per-workspace agent runtime that control plane schedules, scales, and
orchestrates at k8s scale** — one `selfhost bundle` image per workspace/deployment, driven entirely
through core's published seams.

| | `selfhost-k8s` (new, **closed-source**) | selfhost-core (this repo, **OSS/unchanged**) |
|---|---|---|
| Owns | operator, CRDs, apiserver client, tenant namespaces, RLS, HPA, ingress/TLS, ufo/m8t gateway | the agent loop, sandbox, memory, surfaces, accounting, models, the extension system |
| Is | the Kubernetes **host** — schedules and reconciles workspace runtimes at scale | the **workload** — one bundle image = one workspace = one deployment/pod set |
| Sees k8s | yes, entirely | **never** — attaches only via `carriers` / `hubs` / `surfaces` / `bundle` |
| Unit | a tenant namespace (a running selfhost-core + its backing services) | `selfhost serve`, one process, one event loop |

This **inverts** the earlier framing of this RFC. metalcraft's control plane is not a thing selfhost
partially reimplements to make one CLI work; it is the deploy **backend** for the whole runtime.
`selfhost deploy --backend k8s` provisions and scales a selfhost-core instance *through*
`selfhost-k8s` — spec'd in `0004-deploy-via-k8s.md`. ufo/m8t compatibility falls out for free
(§6); it is a consequence, not the goal.

The load-bearing enabler is unchanged from the prior analysis: **core is already backend-agnostic at
every seam a control plane must drive.** The turn path, live streaming, durable answer, spend
enforcement, and the four-role split are all written against injected `Protocol` seams and DB/queue
state — none of them Kubernetes-shaped. So the control plane drives core the way any orchestrator
drives a stateless-plus-durable service: it hands core an image + config + backing services and
routes traffic to it; core does not know it is on Kubernetes.

---

## 1. What metalcraft's k8s control plane actually is

Three import roots (`~/src/metalcraft/docs/runtime-split.md`): `src/metalcraft_k8s/` (CRD machinery +
apiserver client + control-plane/job carriers + namespace membership), `src/metalcraft_servers/
{operator,executor,gateway,jobrunner,egress_proxy,sandbox_proxy}/` (the six deployed processes), and
`src/metalcraft_contracts/` (value contracts). `API_GROUP="metalcraft.ai"`, `API_VERSION="v1"`,
`PLATFORM_NAMESPACE="metalcraft-system"` (`metalcraft_contracts/platform.py`). All of this becomes
`selfhost-k8s`.

### 1.1 The deployed processes and how they scale (`~/src/metalcraft/docs/deployment.md:57-66`)

One image (`metalcraft/platform`), one process per Deployment selected by console-script `args`:

| Process | Entry | Replicas / scaling | Role in the control plane |
|---|---|---|---|
| **operator** | `metalcraft_servers/operator/server.py:main` (1099 LOC) | 2, leader-elected via a `coordination.k8s.io/v1` Lease | level-triggered reconcile over the 7 kinds: `/status` + finalizers + Events + Refinement promotion |
| **gateway** | `metalcraft_servers/gateway/server.py:main` | 2, **HPA 2→10 @70% CPU** | HTTP front door: SAR-authorizes virtual actions, enqueues DBOS turns, ufo/Slack/JSON ingress, artifact download |
| **executor** | `metalcraft_servers/executor/server.py:main` | 2, **HPA 2→20 @70% CPU** | DBOS durable-workflow worker pool that runs turns (model + tools + sandbox) |
| **job-runner** | `metalcraft_servers/jobrunner/server.py:main` | 2, **HPA 2→20 @70% CPU** + CronJobs | stateless batch: source-sync, reindex, distill, memory-curation, self-improve, refinement-eval, sandbox-reap, tenant-provision |
| **egress-proxy** | `metalcraft_servers/egress_proxy/server.py:main` | 2, **HPA 2→10 @70% CPU** | credential-injecting broker for connector/MCP HTTP |
| **sandbox-proxy** | `metalcraft_servers/sandbox_proxy/server.py:main` | 2 fixed, `LoadBalancer` Service | the sandbox's sole egress (sentinel swap, scope, meter) **and** the apiserver shim (§1.3) |

HPA is `charts/metalcraft/templates/hpa.yaml` (`autoscaling/v2`, CPU-target). Postgres is external
(managed); the chart references `metalcraft-postgres` Secret keys `application-url` (product DB, RLS)
+ `system-url` (DBOS DB).

### 1.2 The seven CRDs and the operator

`Store, Source, Tool, Agent, Channel, SpendPolicy, Refinement` in `metalcraft.ai/v1`, all
`Namespaced` with a `/status` subresource (`metalcraft_k8s/resources.py`, `kinds/*.py`; schemas
projected from pydantic in `schema_projection.py`). The operator (`operator/server.py`) runs a
**level-triggered** loop — `run_loop` → every `METALCRAFT_OPERATOR_INTERVAL_SECONDS` (default 15) it
full-re-lists all 7 kinds cluster-wide, computes status from a single Postgres snapshot
(`SqlProductStatusReader.snapshot`, RLS-scoped per namespace), and SSA-patches `/status`
(`~/src/metalcraft/docs/control-plane.md:14-127`). No informer/watch; idempotency is free because
re-deriving from the same durable state yields a no-op SSA. Leader election is a hand-rolled Lease
with optimistic-version PUT as the mutual exclusion.

### 1.3 The apiserver client + the in-sandbox rewriter

`KubernetesApiClient` (`metalcraft_k8s/kubernetes_client.py`) is a urllib/JSON apiserver client: CRD
CRUD, generic namespaced SSA apply (`application/apply-patch+yaml`, `force=true`) for
StatefulSet/CronJob/Service/PVC/NetworkPolicy, leases, events, and `TokenRequest`
(`~/src/metalcraft/docs/control-plane.md:218-233`). The **apiserver rewriter** is not a standalone
service — it lives *inside* sandbox-proxy (`sandbox_proxy/proxy.py`): an in-sandbox `kubectl` targets
`kubernetes.default.svc` through `HTTPS_PROXY`; the proxy path-checks (`metalcraft_store/egress.py`
`apiserver_request_allowed`), pins the path to the run's own namespace, rewrites onto the real
upstream, strips the sandbox auth, and injects a short-lived create-only `metalcraft-agent-writer` SA
token minted via `TokenRequest`, gated by a speaker `SubjectAccessReview`.

### 1.4 Multi-tenancy: namespace-per-tenant + Postgres RLS

A tenant is a **namespace** (`deploy/tenant/base/`, `~/src/metalcraft/docs/deployment.md:160-184`):
`Namespace + ResourceQuota (count/<plural>.metalcraft.ai) + LimitRange + NetworkPolicy(default-deny)
+ RBAC Roles/SAs + seed CRs`. Data isolation below the namespace is **Postgres RLS** — every product
query runs under `set_config('metalcraft.namespace', ns, true)` (`SqlProductStatusReader`, the
`tenant_tx` pattern). Provisioning = kustomize render + `kubectl/helm apply` or a GitOps PR, driven
by `metalcraft_improve/tenant_provision.py` (`tenant-provision` / `self-host-install` jobs).
Membership = an RBAC `RoleBinding` (`gateway` `/members/add`).

### 1.5 The ufo/m8t gateway

The gateway serves **product actions that are not Kubernetes CRUD** (`~/src/metalcraft/docs/
gateway.md:1-7`): enqueue/stream/cancel a turn, mint channel sessions, OAuth tool links, member
binds, promotions, artifact download, and the Slack/ufo surface adapters. Object CRUD goes straight
to kube-apiserver, not the gateway. Two client shapes ride it:

- **`ufo`** — the chat/terminal surface: `scripts/ufo` is a dumb POSIX-shell renderer of
  tab-separated directive lines; every screen is decided server-side
  (`gateway/channels/ufo.py`). "ufo is to its bound Agent what a Slack DM is to the slack surface"
  (`~/src/metalcraft/docs/channels.md:99`).
- **`m8t`** — the **envisioned** kubectl-native product CLI (`m8t get agent/salesbot`, `m8t apply -f
  newco.yaml`; `~/src/metalcraft/docs/research/selfhost-pitch-deck.html:929,1172`, and the design
  note "make generic `m8t` commands kubectl-native and keep product actions as `kubectl m8t`",
  `~/src/metalcraft/docs/research/kubernetes-native-change-spec.orig.md:1371`). m8t is object CRUD
  against the apiserver; ufo is conversation against the gateway.

**Naming note.** In shipped metalcraft the interactive surface is `ufo` (`gateway/channels/ufo.py`),
the CLI is `metalcraft`, and the gateway Deployment is `action-gateway`; `m8t` is only the
*pitch/spec* shorthand for the kubectl-native control CLI (the shipped RLS GUC is
`metalcraft.namespace`, not `m8t.tenant`). The distinction that matters here is durable: object CRUD
via SAR (the "m8t" flavor) is operator/apiserver work `selfhost-k8s` owns; conversation via the
gateway (the "ufo" flavor) is the only member surface under selfhost doctrine (§3.2, §6).

---

## 2. How the control plane drives core as its backend

`selfhost-k8s` schedules selfhost-core the way metalcraft's Deployments ran the `metalcraft/platform`
image — but the workload is now the whole selfhost runtime, not a per-role process. The mapping:

| metalcraft process/scaling | `selfhost-k8s` runs against selfhost-core | Mechanism in core |
|---|---|---|
| gateway Deployment (HPA 2→10) | `serve` **surfaces** role behind an Ingress; N stateless replicas | `_mount_surfaces` / `_mount_ext_routes` (`serve.py:156-157`); sessions/idempotency in Postgres |
| executor Deployment (HPA 2→20) | `serve` **workers** role; N replicas pull the same DBOS queue | `loop/queue.py` `TURN_QUEUE` (`concurrency=1`, partitioned on conversation) |
| job-runner Deployment + CronJobs | `serve` **jobs** role; DBOS schedules replace CronJob objects | `core_jobs`, `SandboxReaper`, `TurnDispatcher` (`serve.py`) |
| servers/egress/sandbox proxy Deployments | `serve` **proxy** role; core's own `EgressProxy` | `sandbox/proxy/server.py:157` `EgressProxy`, derived rules (§3.3) |
| executor `sandbox-manager` (StatefulSet+PVC+exec) | a **pod carrier** on the `carriers` seam | `Carrier` protocol (`sandbox/session.py:117`) |
| Redis stream hub | `hubs` seam, `extensions/redis_hub` | `Hub` protocol (`hub.py:67`) — same Redis Streams |
| operator + 7 CRDs + reconcile | **gone** — core's DB schema + alembic *is* the store | no CRDs to reconcile |
| SpendPolicy CRD + admission | `spend_cap` rows + `SpendEvaluator.decide` | `accounting.py:312-343` |

The four roles are the seam that makes this split cheap: `serve` runs all four in one process, but
**roles share nothing in memory** — cross-role communication is only Postgres/DBOS, the blob store,
the hub, and the proxy's HTTP endpoint (`spec.md:287-297`). The share-nothing invariant is
**gate-enforced today** (`gates.py` `ROLE_PACKAGES = ("selfhost.surfaces", "selfhost.loop",
"selfhost.jobs", "selfhost.sandbox.proxy")`, `_boundary_failures` fails any cross-role import), so
splitting `serve` into per-role Deployments with per-role HPA is *safe* by construction — exactly the
split metalcraft paid for. **The one thing not yet built is the knob:** `serve.run()` launches all
four roles unconditionally; there is no `--role surfaces|workers|jobs|proxy` selector. Adding it is a
small, config-shaped core change (a CLI/`[serve]` knob gating which roles a process starts) — the
"by configuration, not code change" of `spec.md:296` needs that one knob to become literally true.
Until then a k8s deploy runs the whole `serve` per replica and scales it as a unit, which is already
correct (roles coexisting is the OSS default); per-role autoscaling is the optimization the knob
unlocks.

### 2.1 The DBOS-turn model under Kubernetes

metalcraft's rule — "Interactive turns execute through the executor Deployment without creating a Job
or Pod by default" (`~/src/metalcraft/docs/research/kubernetes-native-change-spec.orig.md:1454`; the
worker caps concurrency per pod, `TURN_WORKER_CONCURRENCY=4`, so HPA fan-out doesn't starve peers) —
is core's model already: a turn is a DBOS durable workflow on the shared Postgres, serialized per
conversation, recovered by any peer on crash (`spec.md:270-281`). Under `selfhost-k8s`
this means the workers Deployment scales on CPU/queue-depth with no per-turn pod churn; the
per-conversation sandbox is the only pod a turn touches, and that is the pod carrier's StatefulSet,
reused across turns and reaped by the `jobs` role — never one pod per turn.

---

## 3. The boundary — what keeps core k8s-free

### 3.1 The single k8s-importing piece: the pod carrier

The only place a `kubernetes`/apiserver client enters the picture is the **pod carrier** — the
`CarrierSpec(name="pod", factory=PodCarrier)` implementing core's 4-method `Carrier` protocol
(`sandbox/session.py:117-135`):

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
async def exec(self, handle, argv: tuple[str, ...], stdin: bytes, timeout_s: int) -> ExecResult
async def export(self, handle, path: str, blob: BlobStore, key: str) -> None
async def destroy(self, handle) -> None
```

`PodCarrier` maps these onto a `StatefulSet(replicas:1)+PVC+NetworkPolicy(default-deny+DNS+proxy)`
named by `conversation_id`, `pods/exec` over the `v4.channel.k8s.io` websocket, a PVC/S3-prefix
stream on `export`, and a delete on `destroy` (mirroring metalcraft's `executor/pod_sandbox.py` +
`pod_exec.py`, sized like the Docker carrier `extensions/docker`, ~290 LOC). **This is the only
k8s-importing code**, and it does not live in core: it ships either inside `selfhost-k8s` or as a
carrier extension `selfhost-k8s` publishes, selected by `[sandbox] backend = "pod"`. Core stays
k8s-free; egress still routes through core's own `EgressProxy` (the carrier only wires
`ProxyEndpoint` into the pod env, exactly as Docker wires `host.docker.internal`).

### 3.2 Stays in `selfhost-k8s`, out of core (with the reason)

| Kept out of core | Why | Where it lives |
|---|---|---|
| CRDs, operator, reconcile, finalizers, SSA, Events, Leases | `spec §Non-goals`; the DB schema + alembic *is* the store — nothing to reconcile | `selfhost-k8s` operator; core has no CRDs |
| `SubjectAccessReview` / dual-principal / caller-identity authz | contradicts principle 4 ("granted via connectors through chat — never via caller identity"; `docs/salvage.md:75`) | `grants.py` + the speaker-gates-the-grant model |
| `ApiserverProxy` kubectl-rewrite / `metalcraft-agent-writer` token-mint | existed so an in-sandbox agent could edit its own CRDs; under "every member action happens in chat" that flows through `agents.propose_change` + grants | reserved on the core rewriter seam (`spec.md:103`); rebuilt in `selfhost-k8s` **only** if an enterprise deploy wants raw in-sandbox kubectl |
| namespace-per-tenant + RLS + quota/RBAC package | product/ops config, not runtime code; core serves ONE workspace (`spec §Fixed decisions`) | `selfhost-k8s` tenant chart (§3.4, `deploy-via-k8s.md`) |
| `m8t` CRD CRUD as a member surface | `AGENTS.md`: the only endpoints are the chat transport + third-party plumbing; operator verbs are the `selfhost` CLI | `selfhost` CLI + `selfhost-k8s` apiserver |
| HPA / ingress / TLS / autoscaling policy | deployment config, wrapping core | `selfhost-k8s` chart |

### 3.3 The egress proxy is core, and it is the rewriter seam

The sandbox proxy is core, not an extension — it is the enforcement point for sentinel swap, grant
scoping, and wire metering (`spec.md:96-104`). Its rules are **derived** from extension manifests
(`ScopeRule`/`InjectionRule`/`MeterRule`, `sandbox/proxy/rules.py`), never opened raw. `spec.md:103`
already reserves the seam: *"The enterprise k8s layer later ships its apiserver-rewrite / token-mint
module through this same rewriter seam."* Two honest degrees today:

- **Static injection is already a public seam.** A `CredentialSlot` with an `InjectionTarget(host=
  <apiserver>, header="Authorization", sentinel=…)` (`ext/manifest.py`) flows through
  `derive_credential_rules` into the Scope/Injection/Meter rules the proxy enforces — so a per-
  workspace apiserver *token held as a credential* is expressible now, no core change.
- **Dynamic per-request token-mint has no seam yet.** metalcraft's `metalcraft-agent-writer` token is
  minted *per request* via the apiserver `TokenRequest`, short-lived, SAR-gated
  (`sandbox_proxy/server.py` `KubectlAccess`). Core's `RuleResolver` (`PerAgentRules`) is constructed
  in `serve.py:_resolver` and is **not** a Manifest point, so a mint-per-request module cannot
  register today. Shipping it means **widening the public seam** — a `proxy_rules`/resolver Manifest
  point feeding `PerAgentRules.base` — in a separate, deliberate change, never reaching into core
  internals from `selfhost-k8s`. This is the exact case `spec.md:103` anticipates, and it is optional:
  in-sandbox `kubectl` is not needed for the runtime story (§3.2, Open decision 7).

Either way the proxy stays core and k8s-free — `selfhost-k8s` never forks it and core never imports
k8s to host the rewrite.

### 3.4 The gate stays code, not prose

`no k8s imports anywhere` is stated three ways today, but only one is executable: doctrine
(`spec.md:36`, `§Non-goals`) and the standing-gate list (`docs/plan.md:141`) are **prose**; the only
enforced check is `core/tests/test_sandbox_build.py::test_no_kubernetes_toolchain_baked` (the sandbox
*image* bakes no `kubectl`/`kubeconfig`). There is **no name-level import ban** on the `kubernetes`
package yet — but the banned-import mechanism already exists (`pyproject.toml`
`[tool.ruff.lint.flake8-tidy-imports.banned-api]` bans `requests`/`psycopg2`). **Recommendation:**
add `kubernetes`/`kubectl`-family packages to that banned-api map *scoped to `core/`* when
`selfhost-k8s` lands — the pod carrier (an extension, or in the closed repo) is unaffected; core
becomes enforced by code, not prose. The SDK gate (`gates.py:_sdk_import_failures`) already forbids
`extensions/`+`packs/` from reaching past `selfhost.sdk`, so any carrier/hub extension `selfhost-k8s`
ships cannot touch core internals.

---

## 4. The seam contract `selfhost-k8s` drives — and what's missing

Every attachment is an already-published Manifest point (`ext/manifest.py`), consumed by `serve` at
boot, importable only via `selfhost.sdk`:

| What `selfhost-k8s` needs | Core seam / mechanism | Status | Gap |
|---|---|---|---|
| run the workspace image | `selfhost bundle` OCI image (`bundle.py:41` `Bundle.build`; `python:3.12-slim`, `ENTRYPOINT ["selfhost","serve"]`, pinned `selfhost.toml` + `selfhost.lock`) | **built** | none |
| per-workspace sandbox pods | `carriers` (`CarrierSpec`, `sandbox/session.py:117`) | **seam exists** | build the pod carrier (§3.1) |
| cross-pod live streaming | `hubs` (`HubSpec`, `hub.py:67`; `extensions/redis_hub`) | **built** | none |
| multi-instance boot admit | `runtime_instance.BootGuard` lifts the single-instance refusal when hub+blob+DB are shared | **built** | none |
| stateless surface pods behind ingress | `surfaces` (`SurfaceContext.admit`/`.identity`/`.tail`, `ext/surface.py:128-338`; `SurfaceRoute`/`SurfaceSpec`) | **seam exists** | build the ufo surface (§6) for m8t/ufo parity |
| per-role autoscaled Deployments | share-nothing invariant gate-enforced (`gates.py` `ROLE_PACKAGES`) | **invariant built** | the `--role` selector knob is not (§2) — a small config-shaped core add |
| turn/job coordination | DBOS on shared Postgres; per-conversation queue | **built** | none |
| spend caps per tenant | `spend_cap` rows + `SpendEvaluator.decide` (`accounting.py:312-343`) | **built** | none |
| scheduled/recurring work | `jobs` (`JobSpec`) + DBOS schedules | **built** | DBOS replaces CronJob objects |
| egress metering/scoping | core `EgressProxy` (`sandbox/proxy/server.py`) | **built** | none — k8s-free |
| activation | a `Pack` naming the enabled extensions (`ext/manifest.py:429`) | **built** | none |

**The two big missing pieces are both outside core:** (1) the pod carrier — ~1 extension on an
existing seam; (2) the control plane itself — `selfhost-k8s` (operator, tenant provisioning,
ingress/TLS, HPA), spec'd as the deploy backend in `0004-deploy-via-k8s.md`. **Two small
core-side seam additions are optional, not blocking:** (a) the `--role` selector knob for per-role
autoscaling (§2 — without it a replica runs all four roles, still correct); (b) a `proxy_rules`
Manifest point if in-sandbox `kubectl` token-mint is ever wanted (§3.3 — not needed for the runtime).
Everything else core must expose, it already exposes. When `selfhost-k8s` needs something a seam does
not expose, that is a signal to widen the *public* seam in a separate change — never to reach into
core internals.

---

## 5. How the control plane leverages k8s scale

The point of `selfhost-k8s` is the scale k8s buys, spent on core's already-multi-instance design:

| k8s lever | Applied to core | Enabled by |
|---|---|---|
| **HPA over stateless surfaces** | the surfaces role is stateless behind an LB; scale replicas on CPU/RPS (metalcraft's gateway HPA 2→10) | sessions/idempotency in Postgres (`spec.md:274`) |
| **HPA over workers** | the workers role scales on queue depth/CPU (metalcraft's executor HPA 2→20); no per-turn pod | DBOS partitioned queue; crash-recovery on peers |
| **per-workspace sandbox pods** | one StatefulSet+PVC per conversation, reused across turns, reaped by the jobs role | pod carrier on `carriers`; `SandboxReaper` |
| **namespace-per-tenant density** | each tenant namespace runs one autoscaled selfhost-core = one workspace; quota/netpol/RBAC per namespace | one-deploy-one-workspace (`spec §Fixed decisions`) |
| **shared backing services** | many tenants share managed Postgres/Redis/S3, isolated by control-plane provisioning (RLS role or per-tenant DB + blob prefix) | `workspace_id` on every row; `[database]`/`[blob]`/`[hub]` are injected config |

The critical reconciliation: **RLS is the control plane's DB-provisioning choice, not a core
feature.** Core still serves ONE workspace and never assumes RLS (`spec §Non-goals`). `selfhost-k8s`
can nonetheless pack many single-workspace cores onto shared Postgres by handing each core a
connection whose role is RLS-scoped to its tenant — core sees only its `workspace_id` rows and is
none the wiser. Hard isolation (a database per tenant) and dense isolation (RLS role per tenant) are
both transparent to core; the choice lives entirely in `selfhost-k8s`. This is exactly metalcraft's
namespace-per-tenant + RLS, but with the *whole runtime* per namespace instead of just CRDs.

---

## 6. ufo/m8t compatibility — a consequence, not the goal

Because `selfhost-k8s` fronts core's `surfaces` seam with an Ingress, making metalcraft's `ufo`
client work is a byproduct: build `extensions/ufo`, a **live** surface (hub-tail delivery, like the
web surface) on `surfaces` + `routes`:

- `POST /surface/ufo/{namespace}/{channel}` — resolve/adopt identity from the bearer
  (`SurfaceContext.identity`/`adopt_identity`, `ext/surface.py:198`), `admit` the body onto the turn
  queue (`admit`, `:286`; empty body ⇒ poll), then `tail` the hub (`:338`) through a **directive
  codec** that maps `LiveFrame → txt/say/note/status/ask/exit`, holding ≤85s then emitting `poll`.
- `GET /ufo` — serve the shell client bytes.
- `POST /surface/ufo/onboard/{channel}` — the sign-in flow (mint depends on the parked member/access
  seam; see Open decisions).

The codec is the mirror of `redis_hub`'s frame codec — a ~40-line `LiveFrame`→directive map; the
`ufo` wire (3 endpoints, tab-separated directives, empty-body poll + held-stream ≤85s, HMAC bearer)
is fully specified by `gateway/channels/ufo.py` + `scripts/ufo`. **Nothing in it is Kubernetes** —
the same extension runs on a laptop against SQLite + in-process hub. `m8t`'s CRD CRUD does **not**
come back as a member surface: it is operator/apiserver work `selfhost-k8s` owns, already answered
for members by chat + `agents.propose_change`, and for operators by the `selfhost` CLI.

---

## 7. Open decisions

1. **Repo split granularity** — does `selfhost-k8s` ship the pod carrier as a published carrier
   extension (installable, digest-pinned in the bundle) or as closed code inside the control plane?
   (Recommend: a published extension on the `carriers` seam, so the boundary is the SDK, not a fork.)
2. **RLS density vs database-per-tenant** — §5: RLS role per tenant on shared Postgres (dense,
   cheaper) vs a database per tenant (hard isolation). Both transparent to core; pick per tier?
3. **Onboarding mint** — the ufo `/onboard` `token`/`workspace` flow depends on the parked
   member/access seam. Land that seam first, or ship ufo against a `selfhost init`-provisioned
   workspace + pre-issued CLI token?
4. **Banned-import gate scope** — add `kubernetes`/`kubectl*` to `flake8-tidy-imports.banned-api`
   scoped to `core/` when `selfhost-k8s` lands, so the pod carrier is exempt but core is enforced by
   code.
5. **Pod egress path** — reach core's `EgressProxy` from the sandbox pod via a cluster `Service`
   (clean) vs metalcraft's `LoadBalancer` L4 CONNECT (off-cluster e2b pattern). Service is idiomatic
   for in-cluster pod carriers; the L4 path is only for off-cluster carriers.
6. **Firecracker parity** — E2B is already a core carrier extension; is the pod carrier enough, or is
   a firecracker `runtimeClass` variant in scope? (Recommend: pod first; firecracker is the same
   seam, a carrier variant.)
7. **In-sandbox `kubectl`** — rebuild metalcraft's apiserver-rewrite/token-mint by widening the proxy
   seam (§3.3), or leave it out entirely (agents change config via `agents.propose_change`, not raw
   kubectl)? (Recommend: leave out; revisit only if an enterprise deploy demands it.)
8. **Per-role selector** — add a `serve --role …` / `[serve] roles` knob so `selfhost-k8s` can split
   surfaces/workers/jobs/proxy into per-role autoscaled Deployments (§2), or ship the k8s backend
   scaling the whole `serve` as one unit first? (Recommend: whole-`serve` first — the invariant is
   already safe; add the knob when per-role HPA is worth the config surface.)
