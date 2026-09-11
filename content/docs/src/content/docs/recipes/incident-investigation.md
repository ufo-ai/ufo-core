---
title: Run an on-call SITREP
description: Wake on Datadog monitor changes, correlate current evidence, and post an actionable incident summary.
---

## Connect the evidence

Connect [Datadog](/connectors/datadog/), GitHub, the deployment system, and the incident destination
in Slack. Share the Datadog connection and register its `monitors` and `monitor_alerts` streams as
a synced source.

Run one read-only SITREP first. Confirm the environment, monitor states, time zone, deployment
history, and Slack destination.

## Create the source trigger

Ask in the on-call conversation:

> Watch the shared Datadog connection's `monitors` and `monitor_alerts` streams in this
> conversation. When either changes, prepare an on-call SITREP for production. Read the current
> monitor state and alert events from the last 60 minutes. Correlate them with deployments, source
> changes, logs, traces, and known incidents. State customer impact, start time, affected services,
> current state, owner, evidence, likely cause, uncertainty, and the next safe action. Post in the
> on-call Slack channel only when a new actionable incident starts, materially changes, or recovers.
> Do not post a result for duplicate alerts or unchanged state. Do not acknowledge monitors, change
> production, or create an incident.

Send every incident update to this on-call conversation.

## Control the loop

Open **Tasks**, then **Triggers** to inspect or delete the trigger. Each run appears under **Runs**.
If the source stops, check both Datadog keys, their scopes, the selected Datadog site, and each
stream's sync status before you treat the silence as healthy production.
