---
rfc: 0040
title: "Daytona carrier — a second cloud backend, provider-routed"
status: implemented
date: 2026-08-23
---

# Daytona carrier — a second cloud backend, provider-routed

> E2B is the only cloud carrier a deploy can run, so leaving it means stranding every live
> conversation's `/workspace` — the sandbox disk is the only copy. This RFC adds a `daytona`
> carrier extension on the existing `carriers` Manifest point and makes the deploy route
> per-conversation by the backend scheme already persisted on the conversation row
> (`e2b:sbx_…`, `daytona:…`, `client:/path`): new sandboxes open on the configured default
> backend, and every carrier a deploy keeps registered stays live for the handles bearing its
> scheme. Old conversations keep resuming on E2B until they go quiet; nothing migrates, nothing
> strands; the E2B extension tears out whole once its handles no longer matter.

## Current state

- The carrier seam is the `Carrier` protocol (`core/src/ufo/sandbox/session.py:304`) behind
  `CarrierSpec` on the `carriers` Manifest point (`core/src/ufo/ext/manifest.py:185`).
  `select_carrier` (`core/src/ufo/sandbox/select.py:12`) builds exactly one backend per deploy.
- The durable handle is already scheme-qualified: `<backend>:<container_id>`
  (`SANDBOX_HANDLE_SEP`, `session.py:265`; written at `conversation.py:127`). But a stored handle
  from any backend other than the deploy's resolves to `resume_id=None` — a fresh sandbox on the
  new backend, the old workspace stranded on the old provider. The ingress routes the same way
  (`ingress_serve.py:362`).
- The E2B carrier's machinery answers E2B's clock: the timeout is a wall clock the carrier must
  renew (`_Lease`, `SANDBOX_LEASE_SECONDS=300`, `DIAL_LEASE_SECONDS=900`), a pause is lossless
  (processes, fds, memory survive), and any data-plane call auto-resumes a paused box
  (`extensions/e2b/ufo_ext_e2b.py:20-46`).

Daytona differs on each axis. Its container runtime (the default) stops an idle sandbox — disk
survives, **memory and processes do not** — restarts in <90ms, and its `auto_stop_interval` is an
idle timer the provider refreshes on activity, not a wall clock the carrier renews. Its VM runtime
pauses losslessly like E2B but cannot build images declaratively (snapshot-from-live-VM only).

## Decisions

| Decision | Choice |
|---|---|
| Runtime | Container. The Dockerfile the build already renders is the image, unchanged — one definition. VM runtime is the recorded fallback if the probe kills the container assumptions. |
| Coexistence | Carrier registry, routed by the stored handle's scheme. `[sandbox] backend` names where new sandboxes open; a new `[sandbox] resume_backends` list keeps prior backends live for their handles. |
| Lost E2B sandbox | Reopens on E2B (the carrier's own resume-or-open, unchanged). Rare — a paused box is kept indefinitely — and drainage is the tear-out's job, not v1's. |
| Idle timer | `auto_stop_interval` 5 minutes flat, matching E2B's `SANDBOX_LEASE_SECONDS`. `dial` mutates no timer — Daytona's timer is activity-refreshed and the ingress re-dials per proxied request, so active traffic renews itself. Probe-gated; the fallback is dial bumps the sandbox to 15 minutes and the next non-dial call resets it. |
| Archive / delete | `auto_archive_interval` explicit at the provider's 7-day default (a stopped box holds disk quota; an archived one frees it). Auto-delete never — `/workspace` is the only copy. |
| Snapshots | One per size, resources fixed at snapshot create, the build digest in the name (`ufo-sbx-<size>-<digest12>`) so the drift gate is a name lookup. Built by Daytona's own server-side builder from the shared definition (`apply_layers` over the SDK's `Image`), so no registry sits between the definition and the snapshot. |
| Workspace migration | None in v1. Coexistence makes it unnecessary; a cross-provider copy job is a later RFC if drainage ever needs forcing. |

## Probe (unit 0, throwaway)

A scratch script against the live service; findings land in the carrier docstring as E2B's
measured matrix did (`ufo_ext_e2b.py:20`), because none of this is inferable from the SDK's types.

| Question | Decides |
|---|---|
| Does a long-running `exec` refresh the idle timer? | Whether lease-style management stays dead. The one result that could reshape the carrier. |
| Does preview/CDP traffic through Daytona's proxy refresh it? A held websocket? | Whether `dial` needs the bump-to-15 fallback. |
| `exec` timeout: killed tree, or severed stream with the work continuing? | Whether the kill-group-on-deadline logic ports as-is. |
| Stop→start and archive→start latency; what a restart preserves. | The member-visible cost of an idle gap; archive interval sizing. |
| `fs.download_file` streaming; `upload_file` parent creation. | `read`/`write` mechanics — SDK if it streams, httpx against the toolbox endpoint if not. |
| Does container `exec` inherit image ENV? | Whether `SANDBOX_ENV` merges per-exec (as E2B) or rides the image. |
| The auto-delete "never" value; per-sandbox resource ceilings at our org tier. | Create parameters; whether `large` (8 vCPU/8 GiB) needs an account tier raise first. |

## The extension: `extensions/daytona/ufo_ext_daytona.py`

`Manifest(carriers=(CarrierSpec(name="daytona", factory=build_daytona_carrier, off_cluster=True,
sizes=SANDBOX_SIZES),))`. Env: `DAYTONA_API_KEY`, `DAYTONA_SNAPSHOTS=small=…,medium=…,large=…`
(the wire form `E2B_TEMPLATES` uses), `DAYTONA_API_URL`/`DAYTONA_TARGET` optional. `DaytonaCarrier`
is a frozen dataclass holding the `AsyncDaytona` client and the size→snapshot map.

| Carrier verb | Daytona mechanism |
|---|---|
| `create` | `resume_id` → `daytona.get(id)`, `start()` if stopped or archived; gone → fresh from `snapshots[size]` with `labels={"ufo.conversation_id": …}`, the idle/archive/delete intervals set at create; then prepare, strictly on every open. Daytona's exec has no per-call user and the image strips sudo, so prepare escalates nothing: the proxy CA lands beside a merged trust bundle under `/tmp/.ufo-ca` (the terminal carrier's merged-roots pattern), the egress env points every TLS reader at it, and the image bakes `/workspace` — prepare only proves it writable. |
| `attach` | `get(id)`; deleted → None; stopped/archived → `start()`. Never a fresh box. |
| `exec` | One command via `process.exec` under `setsid`, the leader pid recorded on the box's own disk at launch (a per-run file the deadline's kill names, a per-turn file `stop_commands` sweeps with a session-leader guard); the deadline kills the whole group (`kill -9 -<pid>`), because Daytona's timeout severs the call and leaves the tree running — measured, and the same failure E2B already taught. Every exit code is a result (`ExecResult`, combined output on stdout — Daytona keeps no stderr division). |
| `write` / `read` | `fs.upload_file` in; streamed download out in bounded chunks (SDK stream or httpx on the toolbox endpoint — probe decides). Missing path → `FileNotFoundError`. |
| `file_op` | `ufo_fs_file_op` — the image bakes the `ufo` client, and the op is `ufo fs`. |
| `dial` | `get_preview_link(port)` → `DialTarget(host, tls=True, headers={"x-daytona-preview-token": token})`, fetched per dial because the token resets on restart; ensures the box is started, so a stopped box self-heals on the next request. |
| `stop_commands` | `kill -9 -<pid>` per group `handle.turn_id` launched (`CommandStopping`), same turn-scoped reach as E2B. |

No lease clock and no `_Lease` map: the provider owns the idle timer. The carrier keeps a
per-conversation cache of live `AsyncSandbox` objects and handles exactly one lifecycle answer
deterministically — the provider's sandbox-not-running error on a data-plane call means the box
idled out between calls, so the carrier starts it and reissues the call once. That is Daytona's
documented lifecycle, not a masked fault; every other error raises.

The egress environment (proxy URL from the run token, sentinel model keys, CA paths, `NO_PROXY`)
is byte-for-byte E2B's `_egress_env`. Two off-cluster carriers now need it, so it moves to
`ufo.sdk.sandbox` and both import it — the second-user rule, applied on the day the second user
exists.

Accepted regression, stated once: an idle-stop on the container runtime kills detached background
tasks and the live services behind `dial` (a dev server, a CDP browser) — E2B's pause froze and
revived them. Softened by old conversations staying on E2B, by dial's ensure-started self-heal,
and by the VM runtime remaining a live route if product pain overturns the call.

## Core: the carrier registry

`select_carrier` becomes `select_carriers`: build the `[sandbox] backend` carrier plus one per
`resume_backends` entry (a new `SandboxConfig` field, default `()`), every name failing loud if
unregistered, the off-cluster proxy-URL guard applied to each built carrier. `ConversationSandbox`
and the ingress route by the stored scheme: `client:` → terminal (unchanged); a registered
scheme → that carrier with `resume_id`; no stored handle → the default backend. The portal's
sandbox-size setting reads the default backend's `CarrierSpec.sizes`, as today. The config field
and the routing that consumes it land in one unit.

## Snapshot build

`sandbox/build_template.py` gains a Daytona target beside the E2B and Docker ones: render the
existing Dockerfile from the one definition, build and push to ghcr.io, then
`daytona.snapshot.create` per size with the tier's resources. `Sizing` grows a `disk_gb` axis
(Daytona requires one; E2B ignores it): small 2 vCPU/2 GiB, medium 4/4, large 8/8, disk 10 GiB
each. The publish prints the `DAYTONA_SNAPSHOTS` wire line; verification boots each snapshot and
runs `SANDBOX_TEMPLATE_READY_COMMAND`; the drift gate asserts the digest-named snapshot exists and
is active (Daytona deactivates a snapshot unused for two weeks — the gate catches that, the
publish step reactivates).

## Deploy and rollout

`DAYTONA_API_KEY` joins the deploy secrets and terraform vars; `daytona_snapshots` rides beside
`e2b_templates` in both deploy workflows. Both deploys open new sandboxes on E2B; testing carries
`resume_backends = ["daytona"]`, so the Daytona handles it already holds keep resuming on Daytona.
Either provider's tear-out lands whole: extension, build and CI targets, terraform vars and secret,
and its stored handles' backend dropped from `resume_backends`, in one change.

## Testing

Extension tests mirror `extensions/e2b/tests/test_ext_e2b.py`: the SDK behind Protocol fakes (the
fake stands in for the dependency; the assertions are carrier logic — lifecycle answers, error
mapping, timer arguments, group stops). Core routing tests drive stored `e2b:`/`daytona:`/`client:`
handles through `ConversationSandbox` and the ingress against two registered carriers. The publish
path's boot-and-verify is the live proof; CI carries the drift gate and nothing else keyed.

## Routes

| Route | Status |
|---|---|
| Container runtime + scheme-routed coexistence | Chosen. |
| VM runtime (lossless pause, fork, snapshot-from-sandbox) | Fallback — reopened by the probe failing on idle-timer refresh, or by product need for processes surviving idle. Build pipeline would be provision-then-snapshot. |
| Hard cutover, single backend | Rejected — strands every live conversation's only workspace copy. |
