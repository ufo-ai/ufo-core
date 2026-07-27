---
name: chief-of-staff-setup
description: Wire the chief-of-staff workspace end to end — the Slack front door, meeting-transcript and channel feeds, the markdown state repo, and the sync cadence. Load when the member is setting up or asks how the pieces connect.
---
# Chief-of-staff setup

Target shape: one Slack front door; Google Meet transcripts and Gemini smart notes, Slack chatter,
and a markdown state repo syncing into memory; a recurring sync run reviewing and routing what
accumulates. Drive each step in conversation; where the operator must act (OAuth config, keys),
say exactly what to do and wait for it.

## 1 — Slack front door

Have the owner say "connect Slack": call `slack_connect` and give them the "Add to Slack" link it
returns; approving it installs the bot. Then have the member create a private channel with just
them and the bot — the inbox where stray thoughts, dictated notes, and the sync runs live.

## 2 — Feeds

Feeds authenticate through brokered OAuth: the member grants each provider in chat, and the sync
driver reads through the broker — no key ever lands here. Everything this pack syncs is
read-only; grant no write scope anywhere. Confirm with the member before registering anything —
the speaker gates the granting act.

- Google Meet: ask the member to connect `googlemeet` (`object_explain` on kind `source` shows
  the spec), then apply a `source` object with the exact `meeting_artifacts` stream. The source reads generated Meet transcripts
  (rendered as speaker-grouped dialogue) and Gemini smart notes. The operator first creates a
  custom Google OAuth auth config for the `googlemeet` toolkit in the Composio project, requesting
  only
  `https://www.googleapis.com/auth/meetings.space.readonly` — that one scope covers conference
  records, transcripts, and transcript entries. Adding
  `https://www.googleapis.com/auth/documents.readonly` is optional: with it, smart-note Docs are
  inlined as prose; without it, each page keeps the Doc link and syncs fine.
- Slack chatter: ask the member to connect `slack`, then apply a `source` object with the exact
  `users`, `conversations`, `conversation_threads`, `messages`, and
  `message_participants` streams. The sync reads conversations through the granted account, so the
  consent's scope set must include the conversation read scopes (`channels:read`, `groups:read`,
  `im:read`, `mpim:read`, and the matching `*:history`); if the project's Slack auth config
  requests more than reads, have the operator narrow it the same way as Google Meet's.

## 3 — The state repo

The member's markdown repo — people files, the org chart, daily logs — is the canon; memory
derives from it and never replaces it. The operator adds to `ufo.toml` and restarts serve:

```toml
[[sources]]
backend = "folder"
config = { root = "/absolute/path/to/the/repo" }
```

Every file becomes a recallable page; the org chart is how `memory_search` places a person. The
member keeps editing the repo with their own tools — nothing edits canon from here.

## 4 — The cadence

In the inbox channel, schedule the sync (see `task-scheduling`): apply a `scheduled_task` object
with a schedule like `*/30 * * * *` and the prompt "Load the `sync` skill and run it." Confirm the
cadence with the member first — each run costs credits.

## 5 — Prove the loop

Have the member drop one thought in the inbox, then run one sync by hand ("run my sync"). It
should gather, propose a fan-out, write on approval, and record its run marker. Then the
schedule owns it.
