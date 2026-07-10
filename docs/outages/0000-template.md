---
outage: 0000
title: "<one line: trigger → mechanism → user-visible effect>"
date: YYYY-MM-DD
envs: [testing | prod]
impact: "<what was lost, for how long>"
status: open
---

# <title>

## Impact

What stopped working, for whom, for how long. What kept working (bounds the blast radius).

## Timeline (UTC)

| Time | Event |
|---|---|
| | first cause |
| | detection |
| | mitigation |
| | recovery verified |

## Root cause

The causal chain, one step per sentence, each step citing its evidence (log line, kernel trace,
API response, dump path). Distinguish the trigger (what started it) from the amplifiers (what
turned a fault into an outage).

## Detection gap

Why monitoring saw it late or not at all.

## Remediation

What was done live, and what landed as code (PRs).

## Follow-ups

Each with an owner and a PR when it lands. The postmortem closes when this list is empty.
