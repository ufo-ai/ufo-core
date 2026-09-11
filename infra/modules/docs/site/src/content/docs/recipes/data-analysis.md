---
title: Track weekly activation
description: Calculate, reconcile, and report one product measure on a fixed weekly schedule.
---

## Define and test the measure

Connect the analytics service and the source that defines internal or test accounts. Write the
measure before you schedule it:

> Define activation as a new workspace that completes one agent task within 24 hours of sign-up.
> Exclude internal workspaces. Use Pacific time and complete weeks only.

Run the query for the last eight weeks. Ask the agent to show the query, reconcile counts with raw
events, inspect sample rows, and explain missing or duplicate records.

## Create the scheduled task

> Every Monday at 6:00 AM Pacific time, calculate activation for the previous complete week and the
> seven weeks before it. Use the agreed definition and exclusions. Return weekly new-workspace
> counts, activated counts, activation rates, and week-over-week change. Reconcile the totals with
> raw event counts and report missing or duplicate data. Compare the latest week with the prior
> four-week range. Post a table and chart in this conversation. Call out a change only when it is
> outside that range or the data check fails. Do not write to the source.

The task creates a new run conversation each Monday. Keep the query or formula with the result so a
reader can reproduce it.

## Change the definition safely

Do not edit a published metric definition in place without showing the effect. Ask for both old and
new definitions over the same period, approve the new one, then update the task. Record the effective
date in the task prompt.

Use **Tasks**, then **Scheduled** to pause or change the loop.
