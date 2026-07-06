# A minimal k8s layer that runs `ufo` against selfhost-core

Status: **proposal, not adopted.** Nothing here is built. It exists so the shape of a metalcraft-style
Kubernetes deployment — one that keeps core k8s-free and makes metalcraft's `ufo` CLI work — can be
decided concretely. It touches no core doctrine: `spec.md §Kubernetes` ("Absent from core by
construction … nothing in core may assume or import it"), the `no k8s imports anywhere` standing gate
(`docs/plan.md:141`), and the enterprise-layer entry on the post-U10 backlog (`docs/plan.md:137`) all
stand. Reference repo (read-only): `~/src/metalcraft`.

## TL;DR — the layer is small because the chat path is already generic

The load-bearing finding: **`ufo`'s conversation path touches nothing Kubernetes.** `ufo` is a
server-driven terminal renderer (`~/src/metalcraft/scripts/ufo`) speaking plain HTTP directives; its
Python twin `metalcraft chat` speaks the same wire. Both are pure clients — the gateway decides every
screen. The gateway's ingress is written against injected `Protocol` seams (`ChannelCatalog`,
`AuthorizationResolver`, `StreamHub`, `UfoCommandRunner` — `gateway/ingress.py:106-153`); only two of
those are k8s-shaped (CRD reads + `SubjectAccessReview`). Everything else — HTTP directive stream,
Redis-backed live tokens, Postgres durable answer, HMAC CLI token, in-chat onboarding — is backend-
agnostic and **already has a home in selfhost-core.**

So "make `ufo` work against selfhost-core" is **not** "rebuild the control plane." It is:

| Piece | Effort | Where |
|---|---|---|
| `ufo` surface (directive protocol ↔ `LiveFrame`) | **new**, ~1 extension | `extensions/ufo` on the `surfaces` + `routes` points |
| Cross-pod live streaming | **exists** | `extensions/redis_hub` (same Redis Streams, same `LiveFrame` kinds) |
| Pod-per-conversation sandbox | **new**, ~1 carrier (~150 LOC, mirrors Docker) | `extensions/carrier_pod` on the `carriers` point |
| serve → Deployment, Ingress, Postgres/Redis/S3 | **ops config**, no code | `deploy/k8s/` (Helm/kustomize) over a `selfhost bundle` image |
| Multi-workspace routing + per-workspace provisioning | **new**, the only genuinely novel work | a thin out-of-core control layer / operator |

The k8s-specific machinery `ufo` also carries — the `/command` CRD CRUD, `SubjectAccessReview`, the
`ApiserverProxy` kubectl-mint — are **not member actions** under selfhost doctrine ("every member
action happens in chat"; caller-identity authority is replaced by grants, principle 4). They do not
come back; they are already answered by the `selfhost` operator CLI, `agents.propose_change`, and the
grant flow.

---

## 1. What metalcraft's k8s layer actually did

Three roots (`~/src/metalcraft/docs/runtime-split.md`): `src/metalcraft_k8s/` (CRD machinery + apiserver
client + control-plane write surface), `src/metalcraft_servers/{operator,executor,jobrunner,gateway,
egress_proxy,sandbox_proxy}/` (the deployed processes), `src/metalcraft_contracts/` (value contracts).
`API_GROUP="metalcraft.ai"`, `API_VERSION="v1"`, `PLATFORM_NAMESPACE="metalcraft-system"`
(`metalcraft_contracts/platform.py:1-12`).

### 1.1 Components

| Component | File(s) | What it does |
|---|---|---|
| **7 CRDs** | `metalcraft_k8s/resources.py` (`Kind`, `crd_manifest`) | `Store, Source, Tool, Agent, Channel, SpendPolicy, Refinement` in group `metalcraft.ai/v1`, Namespaced, `/status` subresource. Schema projected from pydantic (`schema_projection.py`). |
| **Operator** | `metalcraft_servers/operator/server.py` (1099 LOC) | Single level-triggered reconcile loop over the 7 kinds: `/status` + finalizers + Events + Refinement promotion. Leader-elected via a `coordination.k8s.io/v1` Lease (`metalcraft-operator`, ×2 replicas). |
| **apiserver client** | `metalcraft_k8s/kubernetes_client.py` (`KubernetesApiClient`) | urllib/JSON apiserver client — CRD CRUD, generic namespaced apply (StatefulSet/CronJob/Service/PVC/NetworkPolicy), leases, events, SSA (`application/apply-patch+yaml`, `field_ownership.py`). |
| **Gateway** | `metalcraft_servers/gateway/` | HTTP front door. SAR-authorizes **virtual resources** (`agents/inbound`, `agents/invoke`, `channels/mint`, `tools/connections`, … — *not* CRD subresources), enqueues DBOS turns, `ufo`/Slack ingress, artifact download, stream tail. |
| **Executor** | `metalcraft_servers/executor/` | **DBOS durable-workflow worker pool** that runs turns (model + tools + sandbox), HPA-scaled. **Turns are not k8s Jobs/Pods** — `spec` gate: "interactive turns execute through the executor Deployment without creating a Job or Pod." |
| **Pod sandbox** | `metalcraft_contracts/sandbox_carrier.py`, `executor/{executor_sandbox,pod_sandbox,pod_exec}.py` | Per-**workspace** (conversation-root) sandbox: a `StatefulSet(replicas:1)` + `PVC(1Gi)` + `NetworkPolicy(default-deny + DNS + egress-proxy)` + optional `Service`, `ownerRef`→Agent. `runAsNonRoot`, uid 10001, cpu/mem limits. `exec` over the apiserver `pods/exec` websocket (`v4.channel.k8s.io`). Reused across turns; reaped by retention. Backends: `e2b` (default), `firecracker`, `pod`. |
| **sandbox-proxy** | `metalcraft_servers/sandbox_proxy/{proxy,server}.py`, `metalcraft_store/egress.py` | MITM forward/CONNECT proxy = the sandbox's sole egress. Run-token auth (live turn only), sentinel→real key swap, LLM-token metering. **AND the apiserver shim** — see 1.2. |
| **egress-proxy** | `metalcraft_servers/egress_proxy/` | In-cluster credential-injecting **broker** for connector/MCP HTTP (per-request `allowedHosts`, reads `secrets/{ref}`). Distinct from sandbox-proxy. |
| **jobrunner** | `metalcraft_servers/jobrunner/`, `metalcraft_k8s/job_client.py` | Stateless batch worker (`POST /jobs/run`, or CLI in a CronJob). Kinds: `source-sync`, `store-reindex`, `store-distill`, `memory-curation`, `self-improve`, `refinement-eval`, `sandbox-reap`, `tenant-provision`, … Uses the Postgres owner-url (cross-tenant scan). |
| **control-plane write** | `metalcraft_k8s/control_plane_service.py` (`KubernetesControlPlaneService`) | The `ufo` human CLI + trusted scheduled-task path: SSA against the 6 kinds, same-namespace only, **dual-principal** (Agent SA RBAC ∩ speaker `SubjectAccessReview`). |
| **Multi-tenant** | `deploy/tenant/base/`, `metalcraft_improve/tenant_provision.py`, `metalcraft_k8s/members.py` | **Namespace-per-tenant.** Tenant package = `Namespace + ResourceQuota + LimitRange + NetworkPolicy(default-deny) + RBAC Roles/SAs + the product CRs`. Tenant-data isolation below that = **Postgres RLS** (`tenant_tx(namespace)`). Provision = kustomize render + `kubectl/helm apply` or GitOps PR. Membership = RBAC `RoleBinding` to `metalcraft-owner`. |
| **Scheduled tasks** | `metalcraft_contracts/scheduling.py` | `ScheduledTask → batch/v1 CronJob` (a locked-down `curlimages/curl` pod) that POSTs `agents/invoke` to the gateway each tick. The agent never applies a raw CronJob. |

### 1.2 The apiserver shim (`ApiserverProxy`) — what "apiserver rewriting" means here

There is **no standalone apiserver-shim service.** The rewriter is a component *inside* sandbox-proxy
(`sandbox_proxy/proxy.py:127-160`). When an agent runs `kubectl` in its sandbox, the sandbox targets
`kubernetes.default.svc` (in-cluster-only) through its `HTTPS_PROXY`; sandbox-proxy recognizes the
apiserver host and:

1. path-checks (`egress.py apiserver_request_allowed`) — discovery reads, else path pinned to the run's own namespace;
2. **rewrites** the path onto the real upstream apiserver base URL;
3. **strips** the sandbox's `authorization`/`host` and **injects** `Bearer <minted token>`.

The token is a short-lived **create-only** `metalcraft-agent-writer` SA token, minted per-namespace via
the apiserver `TokenRequest` subresource (`server.py:240-332`), gated by a speaker `SubjectAccessReview`
(agent ≤ speaker, coarse, once per run). Net effect: the agent's `kubectl` mutates its own namespace's
CRDs without ever holding a real cluster credential, bounded by `speaker ∩ create-only-RBAC ∩
same-namespace`.

`spec.md:103` already reserves the seam: *"The enterprise k8s layer later ships its apiserver-rewrite /
token-mint module through this same rewriter seam."* — i.e. selfhost's egress proxy
(`core/src/selfhost/sandbox/proxy/`).

### 1.3 Which parts `ufo` actually depends on

| `ufo` capability | Depends on | k8s-bound? |
|---|---|---|
| Send a message / receive a turn | gateway ingress → DBOS turn → executor | **No** — generic (Channel resolve + admit + stream) |
| Live token stream | Redis Streams hub (`metalcraft_store/stream_hub.py`) | **No** — plain Redis |
| Durable answer (poll fallback) | Postgres `stream_frame` / `conversation_queue` | **No** — raw SQL |
| Sign in / get a token / get a workspace | `/v1/onboard` directives + HMAC `cli_token` | **No** — pure HTTP |
| `/get /apply /describe /patch /delete /explain /watch` | `KubernetesControlPlaneService` → CRD SSA + SAR | **Yes** |
| `/logs /cost /budget /connect /connection /refinement` | CRD reads, ledger, connector broker, promotion | **Mixed** (ledger/connector generic; CRD/promote k8s) |
| dual-principal authorization | `SubjectAccessReview` at the apiserver | **Yes** |

Only the `/command` CRUD verbs and SAR are k8s-shaped. The conversation is not.

---

## 2. What `ufo` needs to function — the exact surface

Two clients, **one wire protocol** (`~/src/metalcraft/scripts/ufo`, `metalcraft_cli/gateway_client.py`;
server side `gateway/channels/ufo.py`, `gateway/ingress.py`).

### 2.1 Endpoints

| Method + path | Purpose |
|---|---|
| `POST /v1/cli/{namespace}/{channel}` | Authenticated CLI ingress (message body, or **empty body = poll**) |
| `POST /v1/onboard/{channel}` | Signin/onboarding before a namespace is bound (mints token + assigns workspace) |
| `GET /ufo` | Serves the installable shell script itself (`text/x-shellscript`) |

`{namespace}` = the tenant k8s namespace = the workspace. `{channel}` = the conversation/channel.

### 2.2 Request

```
POST /v1/cli/{namespace}/{channel}
  content-type: text/plain
  x-ufo-session: <session id>            # conversation/queue key
  x-ufo-tty: 0|1
  authorization: Bearer <cli_token>      # if signed in
  x-ufo-file / x-ufo-command             # file-upload turns
  body: message bytes                    # empty = poll for the in-flight turn
```

### 2.3 Response = a server-driven directive stream (`text/plain`, tab-separated lines)

`verb \t field \t …` The server renders the whole screen; the client is dumb.

| verb | meaning | selfhost `LiveFrame` analogue |
|---|---|---|
| `txt` | streamed token delta | `TextDelta` |
| `say` / `note` | finalized / dim line | `Terminal.text` / narration |
| `status` | transient activity row | `ToolCall` / `SkillLoad` |
| `ufo` | ASCII mascot frame | — (cosmetic) |
| `ask` / `choose` | prompt for input (terminal) | `ask_user` tool / `Terminal` |
| `poll` | sleep N then re-POST empty | the long-poll continuation |
| `token` | store bearer to `~/.ufo/credentials` | onboarding token mint |
| `workspace` | store assigned namespace | workspace bind |
| `install` / `logout` / `sendfile` / `exit` | client-local ops | — |

### 2.4 Streaming & auth

- **Stream:** per-turn Redis Stream; the gateway `XREAD`s and translates `LiveFrame`s → directives
  (`ufo.py:246-294`), holding the HTTP response ≤ 85s (under the client's `curl --max-time 90`), then
  cutting to a `poll` so the client reconnects. No hub → poll Postgres for the terminal frame.
- **Auth:** self-contained HMAC bearer, `base64url(JSON{alg,subject,expiresAt}).sig`, 30-day TTL
  (`metalcraft_cloud/onboard/cli_token.py`), minted in the onboard flow, verified per call. OSS wires
  no authenticator → falls back to the channel's attested subject.

**A "ufo-compat" server must reimplement:** the 3 endpoints, the directive codec, the empty-body poll +
held-stream semantics, HMAC token verify — and provide its own channel-resolve, authorize, hub, and
(optionally) command runner behind those seams. All present in selfhost-core except the directive codec
and the onboarding mint.

---

## 3. How selfhost-core's seams map to a k8s deployment

| metalcraft k8s | selfhost seam / mechanism | Status | Gap |
|---|---|---|---|
| Channel/Agent CRD + gateway ingress + admit | **`surfaces` seam** — `SurfaceContext.admit` / `.identity` / `.tail` (`core/src/selfhost/ext/surface.py`), over `agent`/`conversation` tables | seam exists | build the `ufo` surface |
| `ufo` directive protocol | `SurfaceRoute` handler + hub `tail` → codec `LiveFrame`→directive | seam exists | the codec (mirror of `redis_hub`'s frame codec) |
| Redis Streams live hub | **`hubs` seam** — `extensions/redis_hub` (per-turn stream, `XADD`/`XREAD`, `covers`) | **built** | none — same pattern, same `LiveFrame` kinds |
| Durable terminal answer / poll | Postgres `turn.terminal` + `hub.covers` fallback (`surfaces/cli.py`) | **built** | none |
| Cross-pod fan-out lifts single-instance guard | `runtime_instance.BootGuard` admits peers when hub is shared | **built** | none — shared hub + Postgres + S3 = multi-instance |
| cli_token HMAC auth | CLI bearer via `surface_identity(surface="cli")` (`surfaces/cli.py:_authenticate`) | **built** | onboarding **mint-in-chat** = parked member/access seam |
| executor DBOS worker pool (turns) | `serve` = one process, DBOS workers; roles ("surfaces/workers/jobs/proxy") share nothing in memory (`spec §Roles`, import-boundary gate) | **built** | per-role split is config, not code — `spec §Scale-out` |
| Turns-are-not-Jobs | DBOS durable workflow per turn, queue serialized on conversation_id | **built** | none |
| jobrunner + CronJobs (sync/reindex/reap/…) | **`jobs` seam** — DBOS schedules (`jobs.py core_jobs`, `SandboxReaper`) + scheduled-tasks ext | **built** | none — DBOS replaces CronJob objects |
| SpendPolicy CRD | `spend_cap` rows + inbound/per-step decide (`accounting.py`) | **built** | none |
| pod sandbox (StatefulSet+PVC) | **`carriers` seam** — `Carrier.create/exec/export/destroy` (`sandbox/session.py`); Docker/E2B carriers | seam exists | build a **pod carrier** (~150 LOC) |
| sandbox-proxy egress (sentinel/scope/meter) | **core** `sandbox/proxy/` `EgressProxy` (`ScopeRule`/`InjectionRule`/`MeterRule`, derived) | **built** | none — this is core, k8s-free |
| `ApiserverProxy` kubectl rewrite + token-mint | the same rewriter seam (`spec.md:103`) | reserved | **not rebuilt** — replaced by `agents.propose_change` + grants (see §5) |
| egress-proxy credential broker | `auth_proxies` (Composio proxy-broker) / `connectors` | **built** | none |
| operator reconcile over CRDs | **none** — declarative object state → selfhost DB schema + alembic migrations | n/a | not needed (no CRDs; the DB is the store) |
| SSA / field managers / finalizers / Events | schema writes + jobs + o11y; deletes are explicit workflows | n/a | not needed |
| namespace-per-tenant + RLS | **one deploy = one workspace** (`workspace_id` column, no RLS) | by design | multi-workspace = the enterprise layer (§4.5) |
| tenant_provision (kustomize/helm) | **`selfhost bundle`** → OCI image + pinned config + lockfile | **built** | per-workspace orchestration wrapper (§4.5) |
| `{namespace}` URL routing | — | — | multi-workspace ingress routing (§4.5) |
| `/command` CRD CRUD + SAR | `selfhost` operator CLI (`spend`, `grants`, `ext`, spend-cap) + chat + `propose_change` | **built** | intentionally not a member HTTP surface |

Two rows are the only real code gaps: **the `ufo` surface** and **the pod carrier**. One row is the
only architectural gap: **multi-workspace routing/provisioning**. Everything else is already in
selfhost-core or deliberately absent.

---

## 4. The minimal out-of-core layer

A deploy pack `packs/k8s_hosting` (activation config) + a small set of extensions + a `deploy/k8s/`
ops directory. **No core change.** Everything attaches through published Manifest points and imports
`selfhost.sdk` only.

### 4.1 `extensions/ufo` — the ufo-compat surface (the one that makes `ufo` work)

A **live** surface (delivery by hub-tail, like the web surface). Registers on `surfaces` + `routes`:

- `SurfaceRoute POST /surface/ufo/{namespace}/{channel}` — resolve/adopt identity from the bearer
  (`SurfaceContext.identity` / `adopt_identity`), `admit` the message body onto the turn queue (empty
  body ⇒ poll the existing turn), then stream the response by `tail`-ing the hub and running the
  **directive codec** (`LiveFrame → txt/say/note/status/ask/exit`, holding ≤85s then emitting `poll`).
- `SurfaceRoute GET /ufo` — serve the shell client bytes.
- `SurfaceRoute POST /surface/ufo/onboard/{channel}` — the sign-in directive flow; on success emit
  `token` + `workspace`. (Mint depends on the member/access seam — see §4.5 / Open decisions.)

The codec is the mirror image of `redis_hub`'s frame codec (`stream_hub.py frame_payload`) — a ~40-line
`LiveFrame`→directive map. **Nothing here is k8s.** The same extension runs on a laptop against SQLite +
in-process hub; `namespace` is just the workspace routing key (single-workspace deploy ⇒ ignored).

> Doctrine note: this reuses metalcraft's insight that "the ufo surface is to its bound Agent what Slack
> DMs are on the slack surface" (`gateway/channels/ufo.py`). It is a peer of `extensions/slack` and
> `extensions/web` on the one surface seam — no privileged path.

### 4.2 `extensions/redis_hub` — already built

Cross-pod live streaming (`extensions/redis_hub/…/stream_hub.py`). Same Redis Streams model metalcraft
used, same six `LiveFrame` kinds. Selecting `hub.backend="redis"` lifts the single-instance boot guard
(`runtime_instance.py`), enabling many `serve` pods behind the ingress. **Reuse as-is.**

### 4.3 `extensions/carrier_pod` — the pod sandbox on the `carriers` point

A `CarrierSpec(name="pod", factory=PodCarrier)`. `PodCarrier` implements the 4-method `Carrier` protocol,
sized like the Docker carrier (`extensions/docker/selfhost_ext_docker.py`, ~290 LOC):

| method | pod implementation |
|---|---|
| `create(spec)` | create-or-attach a `StatefulSet(replicas:1)` + `PVC` + `NetworkPolicy(default-deny + DNS + proxy)` named by `conversation_id`; wait `phase==Running`; set the sandbox `HTTPS_PROXY` = the egress proxy's `ProxyEndpoint`, sentinel model keys, and the proxy CA (exactly the Docker carrier's env). |
| `exec(handle, argv, stdin, timeout)` | apiserver `pods/exec` websocket (`v4.channel.k8s.io`), the `pod_exec.py` decoder. |
| `export(handle, path, blob, key)` | stream the workspace file out of the PVC (or S3-mount prefix) into the blob store, unbuffered — the Docker carrier's `export` shape. |
| `destroy(handle)` | delete the StatefulSet by conversation name (idempotent); a `SandboxReaper` already drives idle reclamation via core's `jobs` seam. |

**This extension is the *only* place `kubernetes`/apiserver-client imports appear.** They are confined to
a carrier extension, behind `CarrierSpec.factory`, selected by `[sandbox] backend = "pod"`. Core stays
k8s-free; the gate holds (§5).

Egress stays core: the pod routes out through selfhost's own `EgressProxy` (`sandbox/proxy/server.py`),
reachable from the pod via a `Service` or the node — the carrier only wires `ProxyEndpoint` into the pod
env, same as Docker wires `host.docker.internal`.

### 4.4 `deploy/k8s/` — ops config over a `selfhost bundle` image (no code)

`selfhost bundle` (`core/src/selfhost/bundle.py`) already emits an OCI image + pinned `selfhost.toml` +
lockfile whose entrypoint is `selfhost serve`. The k8s layer wraps that artifact in a Helm/kustomize
chart:

| Object | Content |
|---|---|
| `Deployment/selfhost-serve` (HPA) | the bundle image; `serve` = surfaces + workers + jobs + proxy in one process; scale-out = replicas + shared hub (`spec §Scale-out`). |
| `Service` + `Ingress`/`Gateway` | route `POST /surface/ufo/{namespace}/{channel}` and `GET /ufo` to serve; TLS. |
| `StatefulSet`/managed `Postgres`, `Redis`, S3 bucket | the shared backends the boot guard requires for multi-instance. |
| `Secret` | model keys, credential-store key, artifact-token secret (env refs in `selfhost.toml`). |
| RBAC `ServiceAccount` + `Role` | **only** what `carrier_pod` needs (create/get/delete StatefulSet/PVC/NetworkPolicy + `pods/exec` in the serve namespace) — the sandbox-manager role, nothing broader. |

No operator, no CRDs, no `SubjectAccessReview`, no tenant-package kustomize tree. The bundle *is* the
install unit (`spec §Roles`: "the same bundle installs OSS, on-prem, or hosted").

### 4.5 Multi-workspace routing + provisioning — the only novel piece

Core serves **one workspace per deploy** (`spec §Fixed decisions`, `§Non-goals`: no RLS multi-tenancy).
metalcraft's `{namespace}` in the `ufo` URL is a multi-tenant router. Two ways to honor it without
touching core:

- **(A) Workspace = namespace = a single-workspace `serve` Deployment** (recommended first cut). Each
  workspace is its own selfhost deploy (own DB/schema or own Postgres database, own bundle) in its own
  k8s namespace; the ingress routes `{namespace}` → that Deployment's Service. Isolation is a hard
  process/namespace boundary — stronger than RLS, and it needs *zero* core change. Provisioning = render
  the chart for a new namespace + `helm/kubectl apply` (the role `tenant_provision.py` played), driven by
  a thin out-of-core **provisioner** (a Deployment or a `Job` per onboard) that calls `selfhost init` +
  `helm upgrade`. This is the analogue of metalcraft's namespace-per-tenant, but with the whole runtime
  (not just CRDs) per namespace.
- **(B) A front router + shared serve fleet** — a small reverse proxy maps `{namespace}` → the right
  workspace's DB/config on a shared fleet. This needs core to serve N workspaces per process (RLS or a
  workspace-scoped connection per request) — a **core change**, explicitly a non-goal today. Deferred.

Onboarding (`/v1/onboard` → `token` + `workspace`) mints a member token and provisions a workspace **in
chat**. In selfhost that is the parked **member/access seam** (per memory: member provisioning + web-
session credential + interactive-onboarding credential-write). The provisioner in (A) is its k8s
embodiment: the onboard route drives it through a public `routes`/`invoke` path, never a bespoke core
endpoint.

---

## 5. What must stay out of core — and how the layer attaches

### 5.1 The gate

`no k8s imports anywhere` is enforced three ways today: doctrine (`spec.md:36`, `§Non-goals`), the
standing-gate list (`docs/plan.md:141`), and `core/tests/test_sandbox_build.py::test_no_kubernetes_
toolchain_baked` (the sandbox image bakes no `kubectl`/`kubeconfig`/`ufo-tool`). The banned-import
mechanism already exists (`pyproject.toml [tool.ruff.lint.flake8-tidy-imports.banned-api]` bans
`requests`/`psycopg2`). **Recommendation:** when this layer lands, add `kubernetes` and `kubectl`-family
packages to that banned-api map *scoped to `core/`*, so the gate is code, not prose — and the pod
carrier (an extension) is unaffected.

### 5.2 Stays out of core (with the reason)

| Kept out | Why | Where it lives instead |
|---|---|---|
| CRDs, operator, reconcile, finalizers, SSA, Events, Leases | `spec §Non-goals`: "No Kubernetes, CRDs, operators…". Declarative object state is selfhost's DB schema + alembic migrations; the DB *is* the store, so there is nothing to reconcile. | nowhere — deleted concept |
| `SubjectAccessReview` / dual-principal / caller-identity authz | Contradicts principle 4 ("granted via connectors through chat — never via caller identity"). `docs/salvage.md:75`: "caller-identity authority contradicts principle 4; grants replace it." | `grants.py` + the speaker-gates-the-grant model |
| `ApiserverProxy` kubectl-rewrite / `metalcraft-agent-writer` token-mint | Existed so an in-sandbox agent could edit its own CRDs. Under "every member action happens in chat," agent config changes flow through `agents.propose_change` (governed) + the grant/approval flow, not in-sandbox `kubectl`. `docs/salvage.md:76`: "returns as the enterprise rewriter module." | reserved on the core rewriter seam (`spec.md:103`); rebuilt **only** if an enterprise deploy wants raw in-sandbox kubectl — not needed for `ufo` |
| RLS multi-tenancy | `spec §Non-goals`: "the `workspace_id` column is the only concession to the future." | one-workspace-per-deploy; multi-workspace = §4.5(A) namespace boundary |
| `/command` CRD CRUD as an end-user HTTP surface | `CLAUDE.md`: the only endpoints are the chat transport + unavoidable third-party plumbing; operator verbs are the `selfhost` CLI, a different audience. | `selfhost` CLI (`spend`/`grants`/`ext`/`spend-cap`) + chat |
| namespace/quota/RBAC tenant package | product config, not runtime code | `deploy/k8s/` chart values |

### 5.3 How it attaches — public seams only

Every attachment point is an already-published Manifest point consumed by `serve` at boot, imported via
`selfhost.sdk`:

| Layer piece | Manifest point / seam | `serve` wiring (existing) |
|---|---|---|
| `ufo` surface | `surfaces`, `routes` | `_mount_surfaces` / `_mount_ext_routes` (`serve.py`) |
| pod carrier | `carriers` (`CarrierSpec`) | `_select_carrier` (`serve.py`), `[sandbox] backend="pod"` |
| redis hub | `hubs` (`HubSpec`) | `_select_hub` (`serve.py`), `[hub] backend="redis"` |
| egress | — (core `EgressProxy`) | `_egress_proxy` — unchanged, k8s-free |
| jobs/reaper | `jobs` (`JobSpec`) | `core_jobs` / `JobRunner` — unchanged |
| deploy artifact | — | `selfhost bundle` OCI image |
| activation | a `Pack` naming `{ufo, redis_hub, carrier_pod, …}` | `[pack] name`, `load_manifests` |

The CI gate that forbids `core` internals in `extensions/` (`gates.py _sdk_import_failures`) already
guarantees the layer cannot reach past these seams. If the layer needs something a seam doesn't expose,
that is a signal to widen the *public* seam in a separate change — never to import core internals from
the k8s layer.

---

## 6. Open decisions

1. **Multi-workspace model** — §4.5(A) namespace-per-workspace (recommended, zero core change) vs (B)
   shared-fleet + RLS (needs core multi-workspace, a standing non-goal). Adopt (A) first?
2. **Onboarding mint** — the `/v1/onboard` `token`/`workspace` flow depends on the parked member/access
   seam. Land that seam first, or ship `ufo` read-only against a `selfhost init`-provisioned workspace +
   pre-issued CLI token?
3. **`/command` verbs** — drop entirely (chat + `selfhost` CLI cover them), or keep a read-only `/get`,
   `/cost`, `/logs` subset as an operator convenience mapped onto existing CLI queries? (Recommend: drop;
   they are the k8s-native surface, not member actions.)
4. **Pod egress path** — reach core's `EgressProxy` from the pod via a cluster `Service` (clean) vs the
   node/hostPort (metalcraft's grey-cloud L4 pattern). Service is the k8s-idiomatic choice.
5. **Banned-import gate scope** — add `kubernetes`/`kubectl*` to `flake8-tidy-imports.banned-api` scoped
   to `core/` when the layer lands, so the pod carrier extension is exempt but core is enforced by code.
6. **Firecracker/E2B parity** — E2B is already a core carrier extension; is the pod carrier enough for
   the k8s story, or is a firecracker `runtimeClass` variant in scope? (Recommend: pod first; firecracker
   is a carrier variant, same seam.)
