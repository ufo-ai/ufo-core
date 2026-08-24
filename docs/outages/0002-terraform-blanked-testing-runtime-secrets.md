---
outage: 0002
title: "Aged-out Secrets Manager version → terraform re-creates its placeholder → testing runtime credentials blank → Deploy (testing) red"
date: 2026-08-21
envs: [testing]
impact: "every runtime credential in ufo/ufo-testing/api-keys emptied; Deploy (testing) red on main from 20:55 UTC; no commit reached testing"
status: open
---

# Aged-out Secrets Manager version → terraform re-creates its placeholder → testing runtime credentials blank

## Impact

Every property of `ufo/ufo-testing/api-keys` became an empty string at 20:10:35 UTC. The pods that
rolled after External Secrets published that document crash-looped on the first credential their
boot path reads, so `ufo-serve` and `ufo-ingress` never became ready and every `Deploy (testing)`
run on `main` from 20:55 UTC failed after its 10-minute rollout wait. No commit reached testing
after `227dec3`. The pods already running kept serving with the credentials they started with, so
the testing surface stayed up. Production was untouched: prod's api-keys document has no
terraform-managed version, and Deploy (production) of the same commit `ad1e235`
([run 32531916063](https://github.com/metalcraftai/ufo/actions/runs/32531916063)) succeeded at
22:19 UTC.

## Timeline (UTC)

| Time | Event |
|---|---|
| 2026-07-07 23:57 | terraform creates version `terraform-2026070723570618860000000a` of `ufo/ufo-testing/api-keys`; every later deploy writes a newer version, leaving that one unlabelled |
| 2026-08-21 20:10:04 | run [32521629215](https://github.com/metalcraftai/ufo/actions/runs/32521629215) (commit `d04d211`) refreshes state; Secrets Manager no longer holds the recorded version |
| 20:10:21 | its plan reads `module.platform.aws_secretsmanager_secret_version.api_keys[0] will be created` — a create, which `ignore_changes = [secret_string]` does not cover |
| 20:10:35 | the apply creates version `terraform-20260821201035423600000001`: all 26 declared properties as empty strings, staged `AWSCURRENT` |
| 20:52 | run [32525448466](https://github.com/metalcraftai/ufo/actions/runs/32525448466) step `Write testing runtime secrets` reads that document, refills `perplexity-api-key`, `spectrum-project-id`, `spectrum-project-secret`, writes it back; `e2b-api-key` and 22 others stay empty |
| 20:51–20:55 | the same run's rollout succeeds — its pods still read the previous `ufo-platform-secrets` projection |
| ~20:55–20:58 | the hourly ExternalSecret refresh publishes the empty document into `ufo-platform-secrets` |
| 20:58:19 | first `RuntimeError: e2b carrier selected but E2B_API_KEY is not set` (pod `ufo-ingress-6c76bc4f9c-qfxnr`, `env:testing`) — 248 occurrences follow, split evenly between `ufo-ingress` and `ufo-serve`, none in `env:production` |
| 21:16 / 21:19 / 22:15 | runs [32527700078](https://github.com/metalcraftai/ufo/actions/runs/32527700078), [32527930636](https://github.com/metalcraftai/ufo/actions/runs/32527930636), [32525479773](https://github.com/metalcraftai/ufo/actions/runs/32525479773) attempt 2 each fail: `ufo-serve` and `ufo-ingress` hang in `Terraform apply` for 10 minutes, then `context deadline exceeded`; the `deployment` job fails at `Require the selected deployment work` |

## Root cause

**Trigger.** Secrets Manager retains a bounded number of versions and drops one that carries no
staging label. Every testing deploy writes a new version through `infra/testing_secrets.py`, so the
version terraform created on 2026-07-07 lost its label, aged out, and stopped existing.

**Mechanism.** `aws_secretsmanager_secret_version.api_keys` declared the document's schema with an
empty string for every property and relied on `lifecycle { ignore_changes = [secret_string] }` to
leave the live values alone. That guard covers an update and nothing else. The refresh in run
32521629215 found the recorded version gone, dropped it from state, and the plan became a create —
which writes `secret_string` as declared. The apply published 26 empty strings over the live
credentials (plan line at 20:10:21, creation line at 20:10:35, both in that run's rollout log).

**Amplifier 1.** `infra/testing_secrets.py` writes three properties and preserves the rest, so the
deploy could not restore what the apply erased. `e2b-api-key` is not among the three even though the
same job holds a valid `E2B_API_KEY` and builds this deploy's e2b templates with it, so recovery
needs an operator.

**Amplifier 2.** Nothing between the empty document and a crash-looping pod could see the value.
The rollout step waits for `externalsecret/ufo-platform-secrets` to be `Ready`, and Ready says the
controller published something, never that a value is non-empty — the same lesson
`infra/secret_sync.py` already records for `ufo-egress-ca`. The apply's own error names
`kubectl_manifest.ufo[...ufo-serve]` and no credential.

## Detection gap

The failure took 10 minutes per run to surface and named a Deployment, not a credential. The
crashing credential appeared only in pod logs; no monitor watches
`"E2B_API_KEY is not set"`, and the deploy conclusion check `ufo.deploy.main` reports a red deploy
without its cause. Nothing at all watches the api-keys document for an emptied property.

## Remediation

- Live: an operator re-seeds every blanked property of `ufo/ufo-testing/api-keys`, writing the whole
  document back:

  ```
  aws secretsmanager put-secret-value --region us-east-1 \
    --secret-id ufo/ufo-testing/api-keys --secret-string file:///dev/stdin
  ```

- Code, this PR:
  - terraform owns no version of either runtime secret document; the two placeholder version
    resources are gone and `removed` blocks drop them from testing's state without deleting the
    live versions (`infra/modules/platform/secrets.tf`).
  - `Write testing runtime secrets` refuses to write, and fails the deploy, when a required
    property holds no value — naming the properties, before the apply rolls any pod.

## Follow-ups

- The deploy owns `e2b-api-key` itself, from the `E2B_API_KEY` its `Select sandbox template` step
  already builds this run's templates with: add that name to the `Write testing runtime secrets`
  step env and to `SECRET_INPUTS`. The templates are scoped to that e2b account, so the carrier has
  to hold the key that built them, and testing then recovers from a blanked property with no
  operator. Not in this PR: the change touches `.github/workflows/deploy.yml`, which the App
  credential that pushed this branch may not write.
- A Datadog monitor on a pod raising on a missing credential at boot, so the next occurrence pages
  in a minute rather than surfacing as a 10-minute apply timeout.
