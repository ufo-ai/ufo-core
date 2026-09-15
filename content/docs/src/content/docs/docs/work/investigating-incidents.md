---
title: Investigating incidents
description: Correlate telemetry, deployments, code, and data without changing production.
---

Connect the telemetry service, source repository, deployment system, and data source for the
incident.

> Investigate checkout errors from 09:00 to 10:00 Pacific. Correlate Datadog logs and traces with
> deployments and source changes. Report observed facts, likely causes, and uncertainty. Do not
> change production.

## Bound the incident

State the environment, service, time range, symptom, and affected users. Include available request
identifiers, error text, and monitor links.

## Keep evidence separate

Ask your UFO to separate:

- Current observed state.
- Events that happened before or during the incident.
- Causal evidence.
- Inference and unresolved questions.

An application error does not prove that a database, provider, or network is unhealthy. Confirm
each dependency from its own evidence.

## Control changes

Read-only investigation is the default. A restart, rollback, configuration change, or data repair
needs a separate request. State the exact target and blast radius. Ask for verification
after an approved operation.

See the complete [incident investigation recipe](/docs/recipes/incident-investigation/).
