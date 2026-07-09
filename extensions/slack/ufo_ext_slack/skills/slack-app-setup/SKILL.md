---
name: slack-app-setup
description: Load when the user wants to create a Slack app, make a Slack bot, or connect Slack to this assistant. Walks them through creating the app from a ready-made manifest, installing it, and filling the four credential slots the slack surface reads.
---
# Slack app setup — connect Slack to this assistant

Stand up a Slack bot and wire it into the slack surface this deploy already runs. There is no new
server to build: the surface is mounted and listening. The whole job is (1) create a Slack app from
the manifest below, (2) install it to the workspace, (3) read four values back from Slack, and (4)
fill the surface's four credential slots with them.

## Ground rules — read before you reply

**This deploy is the Slack receiver.** Slack's Events API request URL is this deploy's own route:

```
<public_base_url>/surface/slack
```

`<public_base_url>` is the deploy's externally reachable base (`[connect] public_base_url`, e.g.
`https://ufo.example.com`) — the same scheme+host a browser reaches this deploy at. If you
don't know it, ask; never invent one and never emit a placeholder (`example.com`, `your-server…`).

- **HTTP Events API only.** Never offer Socket Mode; the surface receives events over HTTP at the
  route above. The manifest below sets `socket_mode_enabled: false`.
- **Signature-verified.** The surface verifies every event's Slack signature against the signing
  secret before acting on it. The one exception is Slack's `url_verification` handshake while the
  `slack_signing_secret` slot is still empty — the challenge echoes back (it stores and grants
  nothing), so the request URL verifies the moment the app is created, before Step 4. If
  verification fails anyway, the deploy isn't reachable at the URL — the URL is wrong.
- **Don't probe the URL.** The sandbox egress proxy denies arbitrary hosts, so `curl`/`dig` against
  the deploy or Slack proves nothing. The URL is correct by construction; use it.
- **Conversational bot.** It answers `@mentions` in channels and direct messages, replying in-thread;
  when mentioned in a thread it reads the earlier thread messages for context, and when first
  addressed it reads the channel's recent messages — though it only ever answers when addressed.
  Replies render the agent's markdown, and files the agent shares upload into the thread; a message's
  attachments download into the agent's workspace. While it works it shows Slack's native thread
  status ("Thinking…", then what it's doing), and when it asks a question with fixed choices it
  presents them as buttons — clicking one answers as the member who clicked. There are no slash
  commands or modals — don't offer them, don't tailor the scope set to a request. The Step 2 scopes
  are the whole surface.

**Produce the manifest in the same reply.** Once you have the app's display name, render the filled-in
manifest immediately — don't promise it and stall.

## Step 1 — create the app from a manifest

Send the user to <https://api.slack.com/apps> → **Create New App → From a manifest**, pick the
workspace, and paste the YAML below. Substitute the display name and the request URL; leave
everything else exactly as written — Slack rejects a manifest with extra fields.

```yaml
display_information:
  name: <bot display name>
features:
  agent_view:
    agent_description: Answers @mentions in channels and direct messages, replying in-thread.
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
      - chat:write
      - files:read
      - files:write
      - groups:history
      - im:history
      - mpim:history
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

Why each piece: `app_mentions:read` + the `message.*` events and matching `*:history` scopes let the
surface see the messages it's added to and the mentions it must answer; `chat:write` posts the reply
in-thread; the `agent_view` block enables Slack's Agents & AI Apps experience and `assistant:write`
lets the surface set the thread's native status ("Thinking…", then what the agent is doing) while a
turn runs — Slack's validator pairs `agent_view` with the `app_home_opened` event, which the
surface receives and ignores; `files:read` downloads a message's
attachments into the workspace and `files:write` uploads files the agent shares back; `users:read` +
`users:read.email` let the surface match a Slack user's verified email to a workspace member, so a
member speaking in a DM acts with their own rights. The `interactivity` block delivers button clicks
(the agent's multiple-choice questions) to the surface's interactive route, signature-verified like
every event. The `app_home` block enables the Messages tab so direct messages reach the bot.

Slack verifies the request URL as the app is created and it succeeds right away — events only
start being accepted once Step 4 fills the slots.

## Step 2 — install to the workspace

In the app, open **Install App** (left sidebar, under *Settings*) → **Install to Workspace** →
**Allow**. Nothing to pick: the manifest already declared the bot scopes.

## Step 3 — read the four values back from Slack

- **Bot User OAuth Token** (`xoxb-…`) — *OAuth & Permissions*, shown after install. → `slack_bot_token`
- **Signing Secret** — *Basic Information → App Credentials* (click **Show**). → `slack_signing_secret`
- **Bot User ID** (`U…`) — *OAuth & Permissions*; the bot's name links to its member profile, whose id
  is the `U…` value. → `slack_bot_user_id`
- **Team (workspace) ID** (`T…`) — Slack → workspace settings, or the `T…` segment of any message's
  "Copy link". → `slack_team_id`

Ignore any **App-Level Token** / *"Scopes to be accessed by this token"* prompt — that mints an
`xapp-` Socket-Mode token this surface never uses.

## Step 4 — fill the four credential slots

ufo stores each value as an encrypted, workspace-scoped credential slot; the surface reads them
in-process to verify events and call Slack, and they never enter the sandbox. The user runs these in
the deploy's terminal — each prompts for its value with hidden input, so a secret never enters this
chat or its transcript; never ask for one here:

```
ufoctl credential set slack_bot_token
ufoctl credential set slack_signing_secret
ufoctl credential set slack_bot_user_id
ufoctl credential set slack_team_id
```

| Slack value | credential slot |
| --- | --- |
| Bot User OAuth Token (`xoxb-…`) | `slack_bot_token` |
| Signing Secret | `slack_signing_secret` |
| Bot User ID (`U…`) | `slack_bot_user_id` |
| Team ID (`T…`) | `slack_team_id` |

`ufoctl credential list` then shows all four slots `set` (encrypted at rest under the key named by
`[credentials] key_env`, default `UFO_CREDENTIAL_KEY`).

On a cold start the deploy can instead carry the four values in its environment (`SLACK_BOT_TOKEN`,
`SLACK_SIGNING_SECRET`, `SLACK_BOT_USER_ID`, `SLACK_TEAM_ID`) before `ufoctl init` — init seeds
every declared slot from its upper-cased env var.

Once all four slots are filled and `ufoctl serve` is running behind `<public_base_url>`, invite
the bot to a channel and `@mention` it (or DM it). It replies in-thread.

## Updating an existing app

Every settings change ships through the same manifest — never walk the user through individual
settings pages. Open the app at <https://api.slack.com/apps> → **App Manifest** (left sidebar,
under *Features*), replace the YAML with Step 1's (same display name and request URLs), and **Save
Changes**. Slack applies it in place: event subscriptions, the interactivity request URL, and
scopes all update at once. Only a scope change needs more — Slack then shows a reinstall banner;
reinstalling rotates nothing, the credential slots stay valid.

## A second bot in the same workspace

Works the same way: create a distinct app with its own display name, install it, and fill the same
four slots with *that* app's token, signing secret, bot user id, and (the same) team id. The deploy
serves one app at a time — refilling the slots switches the surface to the new bot; the old app
stops verifying.
