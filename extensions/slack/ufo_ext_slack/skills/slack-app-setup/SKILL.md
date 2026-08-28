---
name: slack-app-setup
description: Load when the user wants to create a Slack app, make a Slack bot, or connect Slack to this assistant. Drives the whole connection in chat with the slack setup tools — manifest, private credential entry, live verification.
---
# Slack app setup — connect Slack to this assistant

**Prefer the one-click OAuth install.** When the deploy has its own Slack app configured,
`slack_connect` (default `method="oauth"`) returns an "Add to Slack" link an admin clicks — no app
to build, no secrets to paste. This skill is the **bring-your-own-app** alternative: use it when the
admin wants their own Slack app, or when `slack_connect` reports OAuth is not configured on this
deploy. Reach it explicitly with `slack_connect` and `method: manifest`.

Stand up a Slack bot and wire it into the slack surface this deploy already runs. There is no new
server to build: the surface is mounted and listening, and you drive every step with three calls —
the `slack_connect` and `slack_app_manifest` actions on the `surface/slack` object (`object_action`
with `kind: surface`, `name: slack`, `action: <name>`, and the action's own fields under `input`;
`object_get` on `surface/slack` lists them), and the `credential` collection's `request_credentials`
action. The member only clicks through Slack's own pages and enters two values privately in their
terminal.

## Ground rules — read before you reply

**This deploy is the Slack receiver.** Slack's Events API request URL is this deploy's own route —
`slack_connect` reports it as `events_url`; never invent or hand-assemble one.

- **HTTP Events API only.** Never offer Socket Mode; the manifest below sets
  `socket_mode_enabled: false`.
- **Signature-verified.** The surface uses the event's team id only to select a registered signing
  secret, verifies Slack's signature over the original bytes, and only then enters that workspace.
  Slack's `url_verification` body has no team id, so its bounded challenge echoes without binding
  a workspace or marking setup connected. If verification fails anyway, the deploy isn't reachable
  at the URL — the URL is wrong.
- **Secrets never enter this chat.** The bot token and signing secret travel through the
  `credential` collection's `request_credentials` action — the member's terminal prompts for each
  value privately. Never ask for a
  secret in chat prose, and never accept one pasted here; if a member pastes one, tell them to
  rotate it.
- **Conversational bot.** It answers `@mentions` in channels and direct messages, replying
  in-thread; when mentioned in a thread it reads the earlier thread messages for context, and when
  first addressed it reads the channel's recent messages. Once a mention has made a thread its own
  it reads every later reply there, mentioned or not, and answers the ones that ask it something —
  two members talking to each other get no reply at all.
  Replies render the agent's markdown, and files the agent shares upload into the
  thread; a message's attachments download into the agent's workspace. While it works it shows
  Slack's native thread status ("Thinking…", then what it's doing), and when it asks a question
  with fixed choices it presents them as buttons — clicking one answers as the member who clicked.
  There are no slash commands or modals — don't offer them, don't tailor the scope set to a
  request. The manifest's scopes are the whole surface.

## Step 1 — where things stand

Call `slack_connect`. It walks the whole state machine idempotently: `connected` means there is
nothing to do; `pending` means an app identity exists, but this deploy has not verified a Slack
event with the current app credentials; a new install, manifest setup, or signing-secret rotation
can all land here (skip to Step 5); `not_configured` starts at Step 2.

## Step 2 — create the app from the manifest

Ask for the bot's display name if you don't have one, call `slack_app_manifest` with it as
`bot_name`, and show the returned YAML verbatim in a code block. Send the member to <https://api.slack.com/apps> →
**Create New App → From a manifest**, pick the workspace, and paste it exactly — Slack rejects a
manifest with extra fields. The tool renders this shape, filled in for this deploy:

```yaml
display_information:
  name: <bot display name>
features:
  agent_view:
    agent_description: Answers @mentions and direct messages, and follows the threads it joins.
  app_home:
    messages_tab_enabled: true
    messages_tab_read_only_enabled: false
  bot_user:
    display_name: <bot display name>
oauth_config:
  scopes:
    bot:
      - app_mentions:read
      - assistant:write
      - channels:history
      - channels:read
      - chat:write
      - files:read
      - files:write
      - groups:history
      - groups:read
      - im:history
      - im:read
      - mpim:history
      - mpim:read
      - users:read
      - users:read.email
settings:
  event_subscriptions:
    request_url: <public_base_url>/surface/slack
    bot_events:
      - app_home_opened
      - app_mention
      - message.channels
      - message.groups
      - message.im
      - message.mpim
  interactivity:
    is_enabled: true
    request_url: <public_base_url>/surface/slack/interactive
  org_deploy_enabled: false
  socket_mode_enabled: false
  token_rotation_enabled: false
```

Why each piece: `app_mentions:read` + the `message.*` events and matching `*:history` scopes let
the surface see the messages it's added to and the mentions it must answer; the `*:read` scopes
(`channels:read`, `groups:read`, `im:read`, `mpim:read`) back the `slack_channels` action, which pages
`conversations.list` on the bot token so the agent can find a public or private channel by name, or
a DM/group DM by the people in it, instead of only acting on an id it was handed; `chat:write` posts
the reply in-thread; the `agent_view` block enables Slack's Agents & AI Apps experience and
`assistant:write` lets the surface set the thread's native status while a turn runs — Slack's
validator pairs `agent_view` with the `app_home_opened` event, which the surface receives and
ignores; `files:read` downloads a message's attachments into the workspace and `files:write`
uploads files the agent shares back; `users:read` + `users:read.email` let the surface match a
Slack user's verified email to a workspace member, so a member speaking in a DM acts with their own
rights. The `interactivity` block delivers button clicks (the agent's multiple-choice questions) to
the surface's interactive route, signature-verified like every event. The `app_home` block enables
the Messages tab so direct messages reach the bot.

Then the member opens **Install App** (left sidebar, under *Settings*) → **Install to Workspace**
→ **Allow**. Nothing to pick: the manifest already declared the bot scopes. Ignore any App-Level
Token / Socket Mode prompt — that mints an `xapp-` token this surface never uses.

## Step 3 — collect the two secrets privately

Run the `credential` collection's `request_credentials` action with reason "connecting Slack" and
these two prompts, then tell the
member where each value lives and end your turn — their terminal prompts for the values with
hidden input, and they never appear in this conversation:

- `slack_bot_token` — **Bot User OAuth Token** (`xoxb-…`), under *OAuth & Permissions*, shown
  after install.
- `slack_signing_secret` — **Signing Secret**, under *Basic Information → App Credentials* (click
  **Show**).

Only a workspace admin can fill these — the bot is shared by every member.

## Step 4 — finish and verify

When the member says they've entered the values, call `slack_connect` again. It verifies the token
live against Slack and derives the app's identity itself (nothing more to paste), reporting what
remains. A rejected token means a bad copy — re-run Step 3.

## Step 5 — first contact

If the signing secret changed since it was entered, collect `slack_signing_secret` again through
the `credential` collection's `request_credentials` action before first contact. Then the member invites the bot to a channel and
@mentions it, or DMs it. The first signed request flips `pending` to `connected`; confirm with
`slack_connect`.

## Operator alternative

On a self-managed deploy the operator can fill the two secret slots out-of-band instead:
`ufoctl credential set <slot>` prompts with hidden input, and a cold start can carry
`SLACK_BOT_TOKEN` and `SLACK_SIGNING_SECRET` in the environment before `ufoctl init` — init seeds
every declared slot from its upper-cased env var. `slack_connect` still derives the identity.

## Discovering channels

The `channels:read`/`groups:read` scopes (channels) and `im:read`/`mpim:read` scopes (DMs and group
DMs) back the `slack_channels` action: it pages `conversations.list` on the app's own bot token
host-side and returns the conversations matching a query — a channel by its `name`/`purpose`/`topic`,
or a DM by the people in it. A DM has no name, so the tool resolves each DM's members to their
display name/email (using the same `users:read` the surface already relies on) and matches on those.
Pass a specific `query` to search, or leave it empty to list from the top; the scan is bounded, and a
`truncated` result means the workspace has more than one scan covers — narrow the query and search
again.

## Updating an existing app

Every settings change ships through the same manifest — never walk the member through individual
settings pages. They open the app at <https://api.slack.com/apps> → **App Manifest** (left
sidebar, under *Features*), replace the YAML with a fresh `slack_app_manifest` render (same
display name), and **Save Changes**. Slack applies it in place. Only a scope change needs more —
Slack then shows a reinstall banner; reinstalling rotates nothing, the credential slots stay valid.

## A second bot in the same workspace

Works the same way: a distinct app with its own display name, installed, its token and signing
secret collected through Step 3, then `slack_connect`. The deploy serves one app at a time —
refilling the slots switches the surface to the new bot; the old app stops verifying.
