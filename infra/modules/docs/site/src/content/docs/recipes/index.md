---
title: Recipes
description: Complete operating loops built from connected accounts, synced sources, source triggers, and scheduled tasks.
---

Most useful work is a loop, not one prompt. A recipe defines what starts the work, which sources it
reads, when it stays quiet, where it reports, and which actions remain forbidden.

Run the work once before you automate it. Check its data, audience, links, and action boundary. Then
ask the agent to create the source trigger or scheduled task.

## Engineering and operations

- [Make a code change](/recipes/code-change/) — complete one bounded change and open a tested pull request.
- [Review every ready pull request](/recipes/pull-request-review/) — use the GitHub `pull_requests` stream to review each changed, reviewable head.
- [Run an on-call SITREP](/recipes/incident-investigation/) — wake on Datadog monitor and alert changes, correlate evidence, and post only actionable state.
- [Audit documentation after shipped changes](/recipes/documentation-audit/) — inspect changed pull requests and deployments for documentation drift.
- [Draft weekly release notes](/recipes/release-notes/) — turn deployed changes into a reviewable changelog draft.

## Company loops

- [Track weekly activation](/recipes/data-analysis/) — calculate and check one product measure on a schedule.
- [Prepare a weekly progress report](/recipes/weekly-progress-report/) — combine plans, shipped work, and blockers into one update.
- [Prepare daily meeting briefs](/recipes/meeting-brief/) — build briefs at 6:00 AM from Calendar, CRM, email, and shared notes.
- [Review customer feedback each week](/recipes/customer-feedback/) — deduplicate feedback, measure themes, and draft actions.
- [Build a monthly board pack](/recipes/create-deliverable/) — create and verify a recurring presentation and PDF.
- [Build a customer-health application](/recipes/internal-application/) — give a repeated workflow its own agent, sources, triggers, schedule, and homepage.

See [Scheduled work](/work/tasks/) and [Synced sources](/work/sources/) for the controls behind these
loops.
