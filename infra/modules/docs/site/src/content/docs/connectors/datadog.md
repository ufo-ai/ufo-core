---
title: Connecting Datadog
description: Connect Datadog with workspace keys for monitors, incidents, dashboards, and telemetry.
---

A workspace admin connects Datadog.

## Prepare Datadog

Create these values in Datadog **Organization Settings**:

- An API key.
- An application key with the minimum scopes needed for the work.

Read the Datadog site from your Datadog URL. For example, US1 uses `api.datadoghq.com` and EU1 uses
`api.datadoghq.eu`.

## Connect Datadog

Ask the agent:

> Connect Datadog for this workspace.

The agent returns a private credential control for the API key, application key, and site. Enter
the values there. Never paste them into chat.

## What ufo can do

ufo can read monitors, alerts, incidents, dashboards, logs, metrics, and related configuration. The
application key scopes control access. ufo can make changes only when you request them and the keys
permit them.

> Investigate checkout errors from 09:00 to 10:00 Pacific. Correlate logs, traces, monitors, and
> the deployment. Report evidence and uncertainty. Do not change Datadog.

Datadog monitors and alert events can also be added as a [synced source](/work/sources/).

## Fix Datadog access

An API key alone cannot read most endpoints. Confirm that both keys are present, the application
key has the required scopes, and the selected site matches the organization.
