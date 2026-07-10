---
outage: 0001
title: "ENA kernel panic → CoreDNS blackout → 6h telemetry blind spot"
date: 2026-07-10
envs: [testing]
impact: "external DNS + all Datadog telemetry down ~6.5h; new pods unschedulable; two deploys degraded"
status: open
---

# ENA kernel panic → CoreDNS blackout → 6h telemetry blind spot

## Impact

From 05:53 to 12:16 UTC no pod in the testing cluster could resolve external DNS names (RDS,
Datadog). Every pod started in that window crash-looped; every already-running pod kept serving —
the gateway answered 200s throughout, so members saw nothing. All Datadog telemetry stopped at
~06:18 and the outage ran unobserved for six hours until a human noticed the silence. The #105
deploy failed (its rollout pods couldn't start), and the incident surfaced a second, unrelated
latent failure: the `rls-bootstrap` migrate Job had been failing silently since #44 merged
(2026-07-09), masked because the roles and policies it creates already existed.

## Timeline (UTC)

| Time | Event |
|---|---|
| 05:48–05:52 | #104 deploy rolls all five ufo-system deployments + every tenant serve pod at once |
| 05:52:02 / 05:52:19 | nodes `ip-10-0-31-82` and `ip-10-0-8-39` stop logging mid-activity — kernel panic in the ENA driver (`ena_reset_reason_info_free+0x9`, pstore dump `/var/lib/systemd/pstore/178366277`) |
| 05:52:46 / 05:53:06 | both nodes auto-reboot; back within ~40s, inside the NotReady grace window, so k8s never marks them down |
| 05:53 | both CoreDNS replicas (co-located on `31-82`) restart with broken egress to the VPC resolver (`10.0.0.2:53` i/o timeouts; a neighbor pod on the same node resolved fine) |
| ~06:18 | otel-collector's Datadog exporter queue exhausts → zero events reach Datadog |
| 07:17 | hourly sentinel sweep passes — `error events in window: 0` (a silent pipeline emits no errors) |
| 11:41–11:54 | #105 deploy fails: new `ufo-serve`/`ufo-gateway`/`ufo-sandbox-proxy` pods crash-loop on `getaddrinfo` for the RDS host; `ufo-migrate` Job fails the same way |
| ~12:05 | human notices Datadog silence; investigation starts |
| 12:16 | `rollout restart deployment/coredns` — fresh replicas land on two healthy nodes; external DNS verified end-to-end |
| 12:19 | crash-looped pods cleared, #105 deploy re-run green, collector exporting again (0 errors) |
| 12:30 | #110 merged: the migrate Job's real error (`UFO_CONTROL_PG_ROLE_SEED` unset) fixed in both env templates; next deploy's Job `Complete 1/1` — first clean bootstrap since #44 |
| ~12:50 | node `31-82` drained and terminated; ASG replacement Ready |

## Root cause

**Trigger.** The EKS AMI in use (`AL2023 v20260625`, kernel `6.1.175-219.357.amzn2023`) ships ENA
driver 2.17.0g, which its own release notes flag: "might cause kernel panic and unexpected node
reboot." The #104 deploy's simultaneous roll of every deployment produced mass veth/ENI churn, and
the driver page-faulted in its reset path on two nodes ~30s apart (identical pstore traces:
`BUG: unable to handle page fault`, `RIP: ena_reset_reason_info_free [ena]`). CloudTrail shows no
reboot API calls — the resets were in-kernel.

**Amplifier 1 — CoreDNS co-location.** Both replicas sat on one crashed node (the addon default is
only a *preferred* anti-affinity). After the dirty reboot, exactly those two pods came back with
their egress to the VPC resolver blackholed while other pods on the same node resolved fine —
post-crash CNI/eBPF state for the recreated sandboxes. One node's bad reboot became a
cluster-wide external-DNS outage, because every pod resolves through those two replicas.

**Amplifier 2 — fast reboots.** Both nodes returned inside the node-monitor grace period. `Ready`
never flipped, so nothing rescheduled, nothing alerted, and the broken CoreDNS pods stayed where
they were.

## Detection gap

All telemetry flows through one otel-collector to Datadog; when its exporter can't resolve
`api.us5.datadoghq.com`, the failure mode is *silence*. The sentinel sweep counts **error** events,
and zero events means zero errors — it read a dead pipeline as green for six hours. Absence of
telemetry was not a signal anywhere.

## Remediation

Live: CoreDNS restarted onto distinct healthy nodes; crash-looped pods and the failed Job cleared;
#105 deploy re-run (green); crashed node recycled. Landed as code: #110 (migrate Job env fix, both
envs), and this postmortem's PR — required CoreDNS pod anti-affinity, the deploy failing when the
migrate Job fails (`kubectl_manifest.ufo_migrate` waits on `status.succeeded`), and a Datadog
absence monitor (`telemetry_silent`, terraform-managed per env) alerting when 15 minutes pass with
no logs.

## Follow-ups

- Node-group AMI update to a release carrying ENA ≥ 2.17.1g. The fixed driver shipped in
  `v20260707`, which AWS abandoned without explanation; the current recommended release is still
  the affected `v20260625`. Watch amazon-eks-ami releases and update when the re-cut lands — until
  then any mass rollout can re-panic the remaining `v20260625` nodes (`8-39` already did once).
- `8-39` still runs post-crash state; recycle it at the next quiet window.
