---
rfc: 0004
title: "`selfhost deploy` on the `selfhost-k8s` backend"
status: proposed
date: 2026-07-06
---

# `selfhost deploy` on the `selfhost-k8s` backend

Status: **proposal, not adopted.** Nothing here is built. It extends `0002-deploy-service.md`
(which spec'd `selfhost deploy` as a recipe generator with a single-box compose target and a
"missing control-plane ledger") by naming the **managed backend that fills that ledger**: the
closed-source Kubernetes control plane `selfhost-k8s` (`0003-k8s-layer.md`). It changes no core
code — `selfhost deploy` still mirrors `selfhost bundle` (`bundle.py`), generating a recipe and
importing no orchestrator; the k8s backend is one more recipe target.

The thesis, unchanged from `deploy-service.md`: **core already emits every runnable seam a deploy
needs** — the bundle image, the one config file, the boot guard, the four-role split. What is
missing is a control plane *around* it. `deploy-service.md` fills the single-box case with a compose
stack; this RFC fills the scale case with `selfhost-k8s`. Same `selfhost deploy` verb, same
generated contract, a second executor.

## The deploy-backends abstraction

`selfhost deploy` resolves a **backend** that turns the bundle + config into a running, addressable
workspace. Two backends, one contract:

| | `--backend compose` (P1, `deploy-service.md`) | `--backend k8s` (this RFC) |
|---|---|---|
| Target | one VM | a `selfhost-k8s` cluster |
| Provisions | `docker compose` stack: serve + postgres + redis + caddy | a tenant **namespace** + backing services + ingress |
| Isolation | one workspace per box | one workspace per namespace, many namespaces per cluster |
| Scale | `restart: unless-stopped` | per-role HPA (`k8s-layer.md §5`) |
| Executes via | `--up` shells to `docker compose` at CLI startup | posts a **deploy request** to the `selfhost-k8s` API |
| Recipe artifact | a compose file the operator can read/edit/run | a deploy manifest (image + config + tenant + secret refs) |

Both are **generate-then-execute**, mirroring how `bundle` writes a Dockerfile it never builds
(`deploy-service.md:90`): `selfhost deploy` emits the recipe; `--up` (compose) or `--remote`/`--
backend k8s` (control plane) executes it. **No orchestrator import enters `core/`** — the k8s
backend is an HTTP client to `selfhost-k8s`, off the serve loop, at CLI startup.

```bash
selfhost deploy --backend k8s --remote acme --host acme.selfhost.app --pack assistant --email you@acme.com
# 1. selfhost bundle                 → OCI image context + pinned selfhost.toml + selfhost.lock
# 2. build + push image              → the registry selfhost-k8s pulls from
# 3. assemble the deploy request     → {image digest, config, tenant identity, secret material}
# 4. POST to the selfhost-k8s API    → the control plane provisions (below) and reconciles
# 5. poll until Ready                 → prints:  https://acme.selfhost.app · owner you@acme.com · first-run link
```

This is the `git push`-style hosted UX of `deploy-service.md:96-107` with `selfhost-k8s` named as the
control plane: "one push → one running workspace," where a workspace = one deploy (`spec.md`
Persistence) and "many workspaces" = many tenant namespaces the control plane fans out.

## What `selfhost deploy` hands the control plane

The deploy request is the **contract** — a serializable value the OSS CLI produces and `selfhost-k8s`
consumes. Everything in it comes from seams core already emits:

| Field | Source in core | What the control plane does with it |
|---|---|---|
| **bundle image** (digest) | `selfhost bundle` (`bundle.py:41`; `ENTRYPOINT ["selfhost","serve"]`) | the image the surface/worker/jobs/proxy Deployments run |
| **sandbox image** (digest) | `sandbox/build_template.py --build-docker` | the pod carrier's sandbox image |
| **config** (`selfhost.toml`) | `config.py` `Config` (validated, `extra="forbid"`) | rendered into a ConfigMap; `[pack] name`, `[sandbox] backend="pod"`, `[hub] backend="redis"`, `[blob] backend="s3"` |
| **tenant identity** | operator-supplied (`--remote acme`, `--host`) | the namespace name + the tenant label that gates quota/netpol/RBAC |
| **secret material** | secret *names/inventory*, not values | provisions a Secret; injects the env refs core expects |
| **pack** | `[pack] name` (`spec §Packs`) | the activation set — which extensions the bundle enabled |

Secret **inventory** (names, not values — `deploy-service.md:151`): `ANTHROPIC_API_KEY`/
`OPENAI_API_KEY`, `SELFHOST_CREDENTIAL_KEY` (Fernet, seals the whole BYOK store), `SELFHOST_ARTIFACT_
TOKEN_SECRET` (HMAC), AWS creds (S3/STS), carrier keys. BYOK provider secrets (Slack, Composio, Exa)
are **not** in the deploy request — they land encrypted in `credential` rows through chat onboarding,
after the workspace is up.

## What `selfhost-k8s` provisions

Given one deploy request, the control plane materializes a tenant namespace running one selfhost-core
workspace and returns its URL. This is metalcraft's tenant package (`~/src/metalcraft/deploy/tenant/
base/`) with the *whole runtime* per namespace instead of just CRDs:

| Resource | What it is | Fills core's config knob |
|---|---|---|
| **Namespace + label** | the tenant boundary; label gates quota/netpol/RBAC/spend policy | — |
| **Postgres tenant** | a database, **or** an RLS-scoped role on shared Postgres (`k8s-layer.md §5`) | `[database] url` — core serves ONE workspace either way, never assumes RLS |
| **Blob prefix** | an S3 bucket or a prefix + STS role scoped to it | `[blob] backend="s3"` + `sts_role_arn` |
| **Redis** | a shared or per-tenant instance (lifts the multi-instance boot guard) | `[hub] backend="redis" url=…` |
| **Secret** | the generated Fernet/HMAC + provider keys | env refs in the ConfigMap |
| **serve Deployments (+ HPA)** | surface/worker/jobs/proxy roles split by config (`spec §Roles`) | `serve` runs the bundle image |
| **pod carrier RBAC** | `sandbox-manager` Role (StatefulSet/PVC/NetworkPolicy/pods/exec) | `[sandbox] backend="pod"` |
| **Ingress + TLS + hostname** | Gateway/Ingress terminating TLS at `--host`, cert issuance, DNS | `[connect] public_base_url = https://<host>` |
| **schema + onboarding** | run `selfhost init` (`cli.py`) once against the DB: DBOS system DB, `upgrade heads`, onboard workspace+owner+agent+model key, bind a CLI token | — |

The control plane then **reconciles** the namespace to Ready (its operator, `k8s-layer.md §1.2`) and
returns `https://<host>` + the owner's first-run link. From there every capability — connect an
account, grant access — happens **in chat** (`deploy-service.md:110-124`), never a deploy step.

### The provisioning workflow (control-plane side, not core)

1. Pull the bundle + sandbox images by digest from the registry.
2. Apply the tenant namespace package (quota, LimitRange, default-deny NetworkPolicy, RBAC, the
   `sandbox-manager` Role).
3. Provision Postgres (database or RLS role) + blob prefix + Redis; write the Secret; render the
   `selfhost.toml` ConfigMap.
4. Apply the serve Deployments (surfaces/workers/jobs/proxy) + HPA + the Ingress/Gateway + TLS cert.
5. Run `selfhost init` inside a one-shot Job against the provisioned DB (schema + onboard).
6. Wait-healthy; publish the hostname; return the URL + owner link.

Steps 1-4 and 6 are metalcraft's provisioning path (`tenant_provision.py`, the chart's
Deployments/HPA/NetworkPolicy, the Gateway API route + TLS Secret — `~/src/metalcraft/docs/
deployment.md`). Step 5 is core's existing `init`, unchanged. **None of it is core code**; core's
only contribution is the image, the config schema, and `init`.

## Satisfying the deploy-service missing-ledger

`deploy-service.md:57-70` enumerated the control-plane pieces core deliberately does not carry. The
k8s backend fills every one with a `selfhost-k8s` component — proving the ledger's "Missing" column
is exactly the control plane's responsibility, not a core gap:

| `deploy-service.md` "Missing" | Filled by `selfhost-k8s` |
|---|---|
| image build+push; host pull+run | registry push in `deploy`; image pull in the serve Deployments |
| Postgres/Redis/S3/STS creation; secret generation+storage | the provisioning workflow (steps 1-3); Secret + external-secrets |
| process supervision (restart, health, drain) | Deployment + liveness/readiness probes; DBOS recovers in-flight workflows on restart |
| TLS termination; DNS/hostname; cert issuance | Ingress/Gateway + cert-manager + ExternalDNS at `--host` |
| load balancer in front of stateless surfaces; instance lifecycle | the surfaces Service + HPA (`k8s-layer.md §5`) |
| a secret manager/injection; rotation | Secret refs / external-secrets; the Fernet store seam is unchanged |
| shipped collector/dashboards; log egress | the chart's OpenTelemetry collector (observability-gated) → `[o11y] otlp_endpoint` |
| multi-tenant hostname→workspace routing | namespace-per-tenant + wildcard ingress (`k8s-layer.md §5`) |

The single-box compose backend fills the *same* ledger for one VM (`deploy-service.md` P1). The two
backends are the OSS/enterprise line `spec.md` already draws: P1 proves the seams suffice on one box;
the k8s backend is the managed version of the identical contract.

## The closed-source boundary

- **The contract is OSS; the managed backend is closed.** selfhost-core (the bundle, the config
  schema, `init`, the four-role split) and the `selfhost deploy` CLI define the deploy request and
  the runnable image. `selfhost-k8s` — the operator, tenant provisioning, ingress/TLS, HPA — is the
  closed-source implementation of `--backend k8s`. The boundary is the deploy request + core's public
  seams, nothing more.
- **One-way dependency.** `selfhost-k8s` imports `selfhost.sdk` and consumes core's config/bundle;
  core never imports or assumes `selfhost-k8s` (`spec.md:36`, principle 3). This mirrors metalcraft's
  `metalcraft_cloud` seam — "OSS packages never import the extension; the extension imports OSS
  packages" (`~/src/metalcraft/docs/gateway.md:321`).
- **`deploy` generates, the backend executes.** Like `bundle` and like the compose backend, the k8s
  backend emits a deploy manifest and posts it; it imports no k8s client into `core/`. The
  `selfhost-k8s` API client lives beside `deploy`, invoked at CLI startup off the serve loop.
- **Secrets never widen access.** The Fernet store + env keys are the same seam whether injected by
  compose `.env` or a k8s Secret; the control plane is an injection source, not a new authority.
- **Core exposes no inbound control API — by design.** Provisioning, schema, onboarding, and spend
  caps are driven by running core's **own CLI in the pod** (`selfhost init`/`migrate`/`spend cap`,
  `cli.py`) plus injected config/env — not by a bespoke admin HTTP endpoint (which core deliberately
  lacks, `AGENTS.md`: the only endpoints are the chat transport + third-party plumbing). The control
  plane orchestrates core the way any operator would: image + config + `exec`, never a back door.

## Phased plan

Slots directly onto `deploy-service.md:209-220` (P0 manual runbook, P1 single-box compose):

| Phase | Deliverable | Lives in | Proves |
|---|---|---|---|
| **P1** (`deploy-service.md`) | `selfhost deploy --backend compose`: generate serve+pg+redis+caddy, `--up`, `init`, print URL | operator CLI verb (generate-only in core; `--up` shells out) | the seams (bundle, config, boot guard, roles) are enough on one box |
| **P2** | `selfhost-k8s`: the closed-source control plane — tenant provisioning + operator + ingress/TLS + HPA | separate closed repo wrapping unchanged core | the seams are enough at cluster scale — **any needed core change is a P1 seam bug, not a P2 feature** |
| **P2.a** | `selfhost deploy --backend k8s`: assemble + push + post the deploy request; poll to Ready | the `deploy` module beside `bundle` (an HTTP client to `selfhost-k8s`) | the deploy request contract round-trips |
| **P2.b** | the pod carrier on the `carriers` seam (`k8s-layer.md §3.1`) | a published carrier extension `selfhost-k8s` ships | per-workspace sandbox pods |
| **P2.c** | `extensions/ufo` surface (`k8s-layer.md §6`) | an extension on `surfaces`+`routes` | m8t/ufo client parity (a consequence) |

P2 changes **no core code**; if it needs one, that change is a seam bug fixed in P1's scope. The split
between P1 and P2 is the OSS/enterprise line `spec.md` draws — the compose backend and the k8s
backend are the same `selfhost deploy` verb resolving different executors.

## Open decisions

1. **Deploy-request transport** — a real git remote (receive-hook posts to `selfhost-k8s`) vs
   `deploy --remote acme` posting the deploy manifest directly. (Recommend: `--remote` first, per
   `deploy-service.md:238`; git-remote is sugar over it.)
2. **Tenant Postgres model** — database-per-tenant (hard isolation) vs RLS-role-per-tenant on shared
   Postgres (dense). Both transparent to core (`k8s-layer.md §5`); pick per pricing tier?
3. **Registry** — does the k8s backend require a push target (yes, the cluster pulls) while the
   compose backend builds locally? (Recommend: yes — the two backends differ here by nature.)
4. **`init` as a Job vs an operator step** — run `selfhost init` as a one-shot k8s Job in the tenant
   namespace, or fold schema+onboard into the control plane's reconcile? (Recommend: a Job — it
   reuses core's `init` verbatim, no reconcile logic duplicated.)
5. **Onboarding mint** — the hosted `/onboard` `token`/`workspace` flow (`k8s-layer.md §6`) depends
   on the parked member/access seam; land it first, or provision + pre-issue the owner CLI token in
   the `init` Job?
6. **TLS/DNS provider** — cert-manager + ExternalDNS in the chart (matches metalcraft) vs the
   operator's own cloud APIs. (Recommend: cert-manager + ExternalDNS; standard, swappable.)
