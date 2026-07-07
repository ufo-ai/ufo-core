# selfhost-k8s

The **closed-source Kubernetes control plane** that runs [selfhost-core](https://github.com/metalcraftai/selfhost-core)
as its per-workspace backend runtime — the enterprise upgrade that wraps the open-source core.

selfhost-core is the *workload*: one `selfhost bundle` image = one workspace = one `selfhost serve`
process (surfaces + workers + jobs + proxy, one event loop). selfhost-k8s is the *host*: it schedules,
scales, and routes those workspaces at cluster scale — one tenant **namespace** per workspace, many
namespaces per cluster.

```
selfhost deploy --backend k8s --remote acme --host acme.selfhost.app --pack assistant --email you@acme.com
      │  (selfhost-core CLI, generate-only — mirrors `selfhost bundle`)
      ▼  POST deploy request  {image digest, config, tenant identity, secret refs, pack}
┌──────────────────────────── selfhost-k8s ────────────────────────────┐
│  control-plane API  →  Tenant (CRD)  →  operator reconcile            │
│                                          │                            │
│                                          ▼  helm upgrade --install    │
│  tenant namespace: quota · limits · default-deny netpol · RBAC ·      │
│  Secret · ConfigMap(selfhost.toml) · serve Deployment + HPA + Service │
│  · Ingress + cert-manager TLS + ExternalDNS · `selfhost init` Job     │
└───────────────────────────────────────────────────────────────────────┘
      ▼  reconcile to Ready → https://acme.selfhost.app + owner first-run link
```

## Why this repo exists

The whole design is that **core drives k8s through no k8s** — core is backend-agnostic at every seam
(the turn loop, streaming, spend, the four-role split are all injected `Protocol` seams and DB/queue
state, none Kubernetes-shaped). So the control plane drives core the way any orchestrator drives a
stateless-plus-durable service: hand it an image + config + backing services, route traffic to it,
and it never knows it is on Kubernetes. This is metalcraft's namespace-per-tenant + RLS control plane,
**stripped to its essentials** and re-pointed so that the *whole selfhost runtime* runs per namespace
instead of just a set of product CRDs.

| | **selfhost-k8s** (this repo, closed) | **selfhost-core** (OSS, unchanged) |
|---|---|---|
| Owns | tenant namespaces, provisioning, ingress/TLS, HPA, the deploy API, the `Tenant` operator | the agent loop, sandbox, memory, surfaces, accounting, models, extensions |
| Is | the Kubernetes **host** | the **workload** — one image = one workspace = one deploy |
| Sees k8s | yes, entirely | **never** (attaches only via `bundle` / config / `init`) |

The dependency is one-way: selfhost-k8s consumes core's published `selfhost` image + config schema +
`init`; **core never imports or assumes selfhost-k8s.**

## RFC lineage

This repo implements three proposed RFCs from selfhost-core's `docs/rfcs/`:

- **[0003](https://github.com/metalcraftai/selfhost-core/blob/main/docs/rfcs/0003-k8s-layer.md)** —
  *selfhost-core as the backend runtime for the selfhost-k8s control plane.* The keystone: what
  strips down from metalcraft, what stays in core, what this repo owns.
- **[0002](https://github.com/metalcraftai/selfhost-core/blob/main/docs/rfcs/0002-deploy-service.md)** —
  *`selfhost deploy` + a hosted layer.* The deploy verb, the control-plane ledger, the single-box
  compose backend (P1).
- **[0004](https://github.com/metalcraftai/selfhost-core/blob/main/docs/rfcs/0004-deploy-via-k8s.md)** —
  *`selfhost deploy` on the selfhost-k8s backend.* The deploy-request contract and the k8s backend
  that fills the ledger.

The metalcraft control plane it re-derives (reference, read-only): `~/src/metalcraft`
(`metalcraft_servers/`, `metalcraft_k8s/`, `charts/metalcraft/`, `deploy/tenant/base/`).

## Layout

- `src/selfhost_k8s/` — the Python control plane:
  - `contract.py` — the `DeployRequest`/`DeployStatus` contract (the one OSS↔closed boundary).
  - `platform.py` — control-plane constants + `PlatformConfig` (shared services, cluster facts).
  - `render.py` — the config overlay: `DeployRequest` + provisioned Postgres → tenant `selfhost.toml`
    + Helm values (pure, cluster-free — the unit-tested heart).
  - `postgres.py` — database-per-tenant provisioning (mints a per-tenant role + database).
  - `kube.py` — a minimal async apiserver client (SSA the `Tenant`/namespace/Secret, read readiness,
    Lease) — the stripped re-derivation of metalcraft's raw `kubernetes_client.py`.
  - `provision.py` — the reconcile-one-tenant workflow (namespace → Postgres → render → Secret →
    Helm → observe).
  - `operator.py` — the level-triggered reconcile loop + Lease leader election.
  - `api.py` — the deploy endpoint (`POST /v1/deploy`, `GET /v1/tenants/{name}`).
  - `main.py` — `selfhost-k8s api` / `selfhost-k8s operator` (one image, one role per Deployment).
- `charts/selfhost-tenant/` — the Helm chart for one tenant namespace: quota, LimitRange,
  default-deny NetworkPolicy, sandbox-manager RBAC, serve Deployment + HPA + Service, Ingress +
  cert-manager TLS + ExternalDNS, and the `selfhost init` hook Job.
- `deploy/` — the control plane's own install: the `Tenant` CRD, the operator + API Deployments,
  RBAC, the platform-config example.
- `Dockerfile` — the control-plane image (bakes `helm` + the tenant chart).
- `tests/` — contract, render, postgres, kube-client, leader-election, and (helm-gated) chart tests.

## Local validation

`uv run ruff check . && uv run mypy && uv run pytest` — all green. `helm lint` / `helm template`
validate the chart (a `helm`-gated test asserts the rendered resources). No live cluster or cloud was
available here, so `kubectl apply` and the end-to-end reconcile against a real apiserver are
unexercised; the manifests are YAML- and `helm template`-validated only.
