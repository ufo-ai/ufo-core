---
rfc: 0048
title: "CubeSandbox carrier — self-hosted microVM sandboxes on AWS"
status: proposed
date: 2026-09-12
---

# CubeSandbox carrier

> E2B is the only cloud carrier a deploy can run (`extensions/e2b/ufo_ext_e2b.py`), and it meters
> wall-clock provisioned capacity per second — billing a sandbox the whole time it idles waiting on a
> model round, which is most of a turn. A self-hosted CubeSandbox cluster on AWS makes that idle time
> free at the margin and turns concurrency from a purchased tier into our own capacity. This RFC adds
> a `cubesandbox` carrier on the existing `carriers` Manifest point, alongside a second backend that
> a deploy keeps live for its handles, so nothing strands and the old provider tears out whole.

## Current state

- The carrier seam is the `Carrier` protocol (`core/src/ufo/harness/sandbox/session.py:664`) behind
  `CarrierSpec` on the `carriers` Manifest point (`core/src/ufo/runtime/ext/manifest.py:242`), built
  by `select_carriers` (`core/src/ufo/harness/sandbox/select.py:24`). Four carriers exist today:
  `local` (core), `docker` (`extensions/docker/`), `e2b` (`extensions/e2b/ufo_ext_e2b.py:1127`), and
  `client` (terminal).
- The E2B carrier is an extension with no core coupling: no core module imports the E2B SDK, and the
  SDK is reached only through narrow Protocols (`E2BSdk:291`, `E2BSandbox:281`, `E2BCommands:202`,
  `E2BFiles:241`, `E2BPty:259`) in one file. `E2B_LIFECYCLE = {"on_timeout": "pause",
  "auto_resume": True}` (`:134`) means **the repository never calls pause or kill**; the provider
  pauses on its own wall-clock timeout and any data-plane call auto-resumes.
- The durable handle is already scheme-qualified, `<backend>:<id>` (`SANDBOX_HANDLE_SEP`,
  `session.py:265`), written on the conversation row (`conversation.py:127`), and
  `[sandbox] resume_backends` keeps a prior backend live for the handles bearing its scheme. That is
  the coexistence mechanism RFC 0040 designed and this RFC reuses unchanged.
- `/workspace` inside the sandbox is the only copy of a conversation's files
  (`Carrier` docstring, `session.py:664`: "nothing here reclaims a container"). There is no volume, no
  snapshot export, and no backup path in the repository.
- A pause is documented lossless against the live E2B service as of 2026-07-28 (module docstring,
  `ufo_ext_e2b.py:19-40`): processes, open file descriptors and local disk survive; a resume costs
  ~110 ms after a short pause and ~200 ms (360 ms worst) after twelve minutes.
- Production runs `auto_model = "z-ai/glm-5.3-flash"` with `[sandbox] backend = "e2b"`
  (`infra/envs/prod/ufo.tf:96-152`); testing the same shape on `deepseek/deepseek-v4.1-flash`.

The gap: E2B is a rented ceiling and a rented clock. Concurrency is a purchased tier, the per-second
meter runs while the sandbox waits on the model, and sandboxes are CPU-only at every tier.

## Requirements

| Requirement | Why |
|---|---|
| Suspend and resume | a sandbox must stop costing anything while idle and come back inside a turn |
| Persistent memory across suspend | agents hold tool state and long-lived processes in the box; a cold boot plus re-seed is not a substitute |
| Open ports | a sandbox must serve a port outward, WebSocket included, and still serve it after a resume |
| Persistent disk surviving unsuspend | `/workspace` is the only copy; plus a storage tier whose life is longer than one sandbox |

## CubeSandbox against each requirement

CubeSandbox is a Go/Rust microVM service on RustVMM + KVM, Apache-2.0, with `CubeAPI` as an
E2B-protocol REST gateway, `CubeProxy` as an E2B-protocol reverse proxy, `Cubelet` as the node agent,
`CubeVS` an eBPF switch, `CubeEgress` an L7 egress proxy, `cube-lifecycle-manager` the auto-pause
coordinator, and `CubeS3lvol` the S3 snapshot backend.

| Requirement | CubeSandbox answer |
|---|---|
| Suspend / resume | first-class `pausing` / `paused` / `resuming` states, plus `lifecycle={"on_timeout": "pause", "auto_resume": True}` in the same shape E2B uses |
| Persistent memory | the pause package carries memory and filesystem; the project's `auto-resume.py` example asserts kernel memory and filesystem are byte-identical across the cycle |
| Open ports | `CubeProxy` routes `<port>-<sandboxId>.<domain>` and `/sandbox/<id>/<port>/...`; WebSocket and a gRPC listener on 9090 |
| Persistent disk | the writable layer rides the pause package; the Volume framework adds storage whose lifecycle outlives any sandbox |

Measured on the project's own nested-KVM cloud VM (AMD EPYC 9K65, 16 vCPU, 30 GiB):

| Operation | concurrency 1 avg | concurrency 10 amortized |
|---|---|---|
| Pause | 370.8 ms | 158.6 ms |
| Resume | 18.9 ms | 2.7 ms |

Resume is effectively free. Pause is the bottleneck because it currently writes the sandbox's whole
anonymous memory to storage, and the project states a soft-dirty incremental mode is coming that is
expected to cut it 80-90%. **Plan against 371 ms per 2 GiB, not the projection.**

### The one fidelity difference that matters

Pause fidelity is documented precisely: CPU registers, process memory, TCP state with no external
peer, and filesystem mutations survive; **outbound sockets the sandbox opened are dropped on pause**
and must be reopened by the application. Every long-lived connection out of the box — a dev server
watch, a held CDP socket — needs re-establishment on resume, and the carrier's contract should say so
rather than leave it to each tool.

### The one operational trap that matters

Resume recreates the guest NIC and host ports and rewrites the Redis proxy map, then CubeMaster
best-effort purges CubeProxy's `local_cache`. That purge requires CubeMaster and CubeProxy to share
the same admin token; if they differ the purge returns 403, Redis is still correct, and CubeProxy can
serve a stale backend until the entry expires — a same-node 504 for the member. This is a two-
component config-drift bug with a silent failure mode, so it gets a startup assertion, a post-resume
probe, and an alarm rather than a runbook entry.

## Proposal

A `cubesandbox` carrier extension on the existing `carriers` Manifest point, plus a small core change
so a deploy can run it beside `e2b` and route by scheme.

| Decision | Choice |
|---|---|
| Carrier | `extensions/cubesandbox/ufo_ext_cubesandbox.py`, `off_cluster=True`, same `sizes` tuple. It talks the E2B wire protocol through `CubeAPI`, so the carrier logic is recognisably `E2BCarrier`'s with `pause`/`resume` made explicit instead of provider-driven. |
| SDK | `cubesandbox` (PyPI, `>=0.7`). Not the stock `e2b` client: it works for create/connect/exec/files by pointing `E2B_API_URL`/`E2B_API_KEY` at CubeAPI, but Volumes are hardcoded to the e2b.cloud backend in the official SDK, so volume work needs `cubesandbox`. One dependency, one code path. |
| Lifecycle | the provider still owns auto-pause; `timeout` plus `lifecycle={"on_timeout": "pause", "auto_resume": True}` at create, matching `E2B_LIFECYCLE`. Unlike E2B's wall clock, CubeSandbox's is an idle timer reset by any SDK call or by direct HTTP to a service in the box, which is strictly better for us. |
| Resume | `connect()` for resume, `NEVER_TIMEOUT` only for boxes we intend to keep paused indefinitely |
| Open ports | `dial` returns `DialTarget` from the path form `/sandbox/<id>/<port>/` by default, avoiding a wildcard DNS requirement; host-based `<port>-<id>.<domain>` behind a config flag for SPA workloads that load root-absolute assets |
| Port re-resolution | `dial` is never cached across a resume; the carrier re-resolves on every dial, because the mapping is recreated and the proxy cache may be stale |
| Persistent disk | the pause package covers `/workspace` on suspend. A tenant-level Volume (`driver="s3"` against AWS S3) is the mechanism for anything that must outlive a sandbox; the carrier exposes volume mount as an opt-in on `SandboxSpec`, not a default. |
| Coexistence | reuse RFC 0040's scheme routing: `[sandbox] backend = "cubesandbox"` for new boxes, `resume_backends = ["e2b"]` keeps E2B live for its handles. Nothing migrates, nothing strands. |
| Template backend | every template built with `--backend s3`, because that choice is locked at template create and cannot be changed later, and only `s3` templates can resume on another node. |
| Egress | **keep our own proxy.** CubeEgress's inject rules are the same shape as our sentinel model, but they put the secret in CubeSandbox's config rather than our credential store. Use CubeEgress for domain allow-listing and the per-host JSONL audit; leave credential injection where it is. |

### Carrier mapping

| `Carrier` verb | CubeSandbox mechanism |
|---|---|
| `create` | `resume_id` → `Sandbox.connect(sandbox_id)`; gone → `Sandbox.create(template=…, timeout=…, lifecycle=…, metadata={"ufo.conversation_id": …})`. Prepare strictly on a fresh box, bounded on a resume, as `_prepare_strictly` / `resume_prepare_seconds` already do. |
| `attach` | `get_info()`; 404 → `None`; `paused` → leave paused (an attach is read-only and must not bill a resume) |
| `exec` | `commands.run` with the same combined-output `ExecResult` contract and the same deadline-kills-the-group behaviour, since CubeSandbox inherits E2B's sever-the-call semantics |
| `write` / `read` | `files.write` / `files.read`, streamed in bounded chunks |
| `dial` | path-form URL, re-resolved per call, plus the allow-listed-port and traffic-token handling |
| `file_op` | `ufo fs`, unchanged — the image bakes the client |
| `stop_commands` | same turn-scoped group kill |

### Core change

Only one: `select_carriers` already builds every registered backend named by `backend` and
`resume_backends`, so the carrier registry itself needs nothing new. What is new is two config fields
in `[sandbox]`: `cubesandbox_api_url` and `cubesandbox_api_key_env`, mirroring how the E2B carrier
reads `E2B_API_KEY` (`ufo_ext_e2b.py:91`). A deploy that registers the extension but leaves the URL
unset fails loud at boot rather than at first sandbox open.

### Deploy

- Compute on **virtual EC2 with nested virtualization enabled**:
  `--cpu-options "NestedVirtualization=enabled"` on C8i/M8i/R8i (and since 2026-06-18 also on C7i,
  R7i, M7i, I7i, X8i and the `-flex`/`-id` variants). This is the decision that makes self-hosting
  tractable: it gives `/dev/kvm` on an ordinary instance, so **the PVM custom-kernel path is not
  needed** and we run a supported OS.
- **XFS at `/data/cubelet`**, ≥ 200 GB. Not a tuning choice: CubeCoW's O(1) snapshot and clone are the
  `FICLONE` reflink ioctl. On ext4 it silently degrades to full copies.
- A control node (CubeMaster, CubeOps, CubeAPI, CubeProxy, Redis, MySQL) and N compute nodes
  (Cubelet only), registering to CubeOps on 3010.
- External S3, not the bundled MinIO, for both CubeS3lvol's snapshot bucket and the Volume bucket.
  CubeS3lvol is opt-in (`[cow.s3] enable = true`) and off by default. Each node that may restore
  cross-node also needs a 512 GiB sparse WAL image at `/data/cubelet/rcow/wal_bdev.img` and about
  2 CPU + 18 GiB RAM of overhead.
- Set the CubeMaster/CubeProxy shared admin token explicitly, in one place.

### What we are accepting

- **Cross-node resume is preview-grade and slow.** Default `xfs` snapshots are origin-pinned; cross-
  node needs `backend=s3` plus `remote_status=ready` plus an unschedulable origin plus matching
  `cpuid_hash` and `host_kernel_release`, and costs 6.5-14.7 s per sandbox against 64.5 ms for a local
  xfs restore. There is also a documented "cannot pause again for a short time after a cross-node
  resume". Mitigation: build every template with `--backend s3`, keep node kernels and CPU images
  identical, prefer origin, and design the client to tolerate seconds.
- **No filesystem-only snapshot.** E2B ships `keepMemory: false`; CubeSandbox has it on the roadmap
  only. This is the reason the Volume framework is in scope rather than optional.
- **Paused retention is our configuration, not a platform guarantee.** E2B keeps a paused sandbox
  indefinitely; CubeSandbox leaves it to operator config.
- **Young project.** Five months of public releases, contributors concentrated in one vendor team,
  and a v0.7.0 DB and on-disk layout break whose migration path is tested from 0.6.0 only. Pin
  versions, stage upgrades.

## Doctrine fit / implications

- **The extension boundary holds.** The carrier seam was built for exactly this; the change is one
  extension plus two config fields. No core sandbox logic learns about CubeSandbox.
- **One shape.** `dial` returns the same `DialTarget`; `exec` the same `ExecResult`; a resume the same
  `SandboxHandle`. The one contract that must be written down rather than implied is that sockets do
  not survive a pause.
- **Fail loud.** The API URL unset, a template built on `xfs` when the deploy configures `s3`, an
  admin-token mismatch, and a `/data/cubelet` that is not XFS are all boot-time assertions, not
  runtime surprises.
- **Both ends.** The member sees a sandbox that no longer bills while idle. The operator gets a
  cluster whose limits they own, and a second backend they can drop back to.

## Alternatives

| Route | Why not |
|---|---|
| Stay on E2B | the meter bills idle time and concurrency is a purchased tier; the whole point is the cost curve |
| Run CubeSandbox on the PVM kernel | requires swapping the host kernel for a Tencent fork whose production validation is scoped to Tencent Cloud, with no documented AWS validation. Nested virtualization on the instance suffices. |
| Bare-metal EC2 | nested virtualization on virtual EC2 removes the need; bare metal is a much larger cost step for the same `/dev/kvm` |
| Keep the official `e2b` SDK and skip Volumes | Volumes are how persistent disk outlives a sandbox; giving that up leaves `/workspace` as the only copy, which RFC 0040 already identified as the reason to keep providers alive |
| A hard cutover to CubeSandbox | strands every live conversation's only workspace copy |
| Port CubeEgress's credential injection into our stack | our sentinel model is already built and audited; moving secrets into a second config plane is a downgrade |

## Testing

Extension tests mirror `extensions/e2b/tests/test_ext_e2b.py`: the `cubesandbox` SDK behind Protocol
fakes, asserting carrier logic — lifecycle arguments at create, resume mapping, the group-kill on
deadline, dial re-resolution, volume mount opt-in. Core routing tests drive `cubesandbox:` and `e2b:`
handles through `ConversationSandbox` and the ingress against two registered carriers. The live proof
is a boot-and-verify across a real pause and resume, asserting a marker file and a process's memory
survive.

## Open decisions

1. **Which EC2 family and size for compute nodes.** Needs our own density measurement; the project's
   numbers are a different machine and workload.
2. **Path-based or host-based sandbox routing.** Path-based needs no wildcard DNS and no certificate;
   host-based is required for SPAs that load root-absolute assets. This is a product decision about
   what a published sandbox URL looks like.
3. **Does any workflow pause a sandbox for days?** Paused retention is ours to configure and paused
   boxes still consume snapshot storage.
4. **What is our tolerance for the public port changing across a resume?** If zero, we need a
   reservation mechanism, and the docs describe none.
5. **Volumes for tenants, or only for us.** A per-tenant Volume is a new resource with its own
   lifecycle and quota question; this RFC does not settle whether members get one.

## Sources

CubeSandbox documentation: [README](https://github.com/TencentCloud/CubeSandbox/blob/master/README.md),
[Sandbox Lifecycle](https://github.com/TencentCloud/CubeSandbox/blob/master/docs/guide/lifecycle.md),
[Cross-Node Snapshots](https://github.com/TencentCloud/CubeSandbox/blob/master/docs/guide/cross-node-snapshot.md),
[S3 Volumes](https://github.com/TencentCloud/CubeSandbox/blob/master/docs/guide/s3-volume.md),
[Volume Plugin Development](https://github.com/TencentCloud/CubeSandbox/blob/master/docs/guide/volume-plugin.md),
[Persistent Storage](https://github.com/TencentCloud/CubeSandbox/blob/master/docs/guide/persistent-storage.md),
[Security Proxy](https://github.com/TencentCloud/CubeSandbox/blob/master/docs/guide/security-proxy.md),
[Restrict Public Access](https://github.com/TencentCloud/CubeSandbox/blob/master/docs/guide/restrict-public-access.md),
[HTTPS & Domain Resolution](https://github.com/TencentCloud/CubeSandbox/blob/master/docs/guide/https-and-domain.md),
[CubeVS Network Model](https://github.com/TencentCloud/CubeSandbox/blob/master/docs/architecture/network.md),
[Architecture Overview](https://github.com/TencentCloud/CubeSandbox/blob/master/docs/architecture/overview.md),
[PVM benchmark report](https://github.com/TencentCloud/CubeSandbox/blob/master/docs/blog/posts/2026-06-03-cubesandbox-perf-benchmark-pvm.md).

AWS: [EC2 nested virtualization](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/amazon-ec2-nested-virtualization.html),
[launch 2026-02-16](https://aws.amazon.com/about-aws/whats-new/2026/02/amazon-ec2-nested-virtualization-on-virtual/),
[expansion 2026-06-18](https://aws.amazon.com/about-aws/whats-new/2026/06/nested-virtualization-intel-us-gov-cloud/).

E2B: [Sandbox persistence](https://docs.e2b.dev/sandbox/persistence),
[pricing explained](https://www.beam.cloud/blog/e2b-pricing-explained).
