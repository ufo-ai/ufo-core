# Onboarding

Onboarding is CLI-first: the apex's public face is a Cloudflare edge worker (landing card,
waitlist, `/install`), and the terminal client is the one onboarding renderer. The gateway serves
the client script and drives every screen server-side as directives over
`POST /v1/onboard/{channel}`.

## Component map

```text
                     Cloudflare edge (infra/modules/edge)
               +--------------------------------------------+
curl / ------->|  GET /            CLI UA -> landing card   |
               |                   other UA -> prod site    |
               |  POST /waitlist   email -> D1; counter     |
               |  GET /install     proxy of gateway /ufo    |
               +----------------------+---------------------+
                                      | origin
                                      v
                         control plane / apex gateway
               +--------------------------------------------+
ufo client --->|  GET /ufo -> version-stamped POSIX client  |
               |  POST /v1/onboard/{channel}                |
               |       |                                    |
               |       v                                    |
               |  Onboarding.advance ---------------------->+--> SharedWorkspaces
               |       +--> onboard_claim rows              |    or JoinOrProvision
               |       +--> email code workflow             |
               |       +--> invite_code burn (create only)  |
               |       +--> bearer token                    |
               +--------------------------------------------+
                                      |
                                      | token + workspace directives -> ~/.ufo/
                                      v
                         tenant workspace
               +--------------------------------------------+
Browser        | setup surface                              | core
/surface/setup |  status · slack/manifest ·                 | SurfaceContext
?token=... --->|  slack/credentials · slack/test            |  credential()/put_credential()
               +----------------------+---------------------+
                                      | fills workspace-global Slack slots
                                      v
               +--------------------------------------------+
Slack          | slack surface                              | blob marker:
Events API --->|  verify signing secret · admit turns       | workspaces/<ws>/surfaces/slack/
               +--------------------------------------------+                     url_verified
```

## The apex edge

One worker fronts both apexes (`flyingobject.ai`, `testing.flyingobject.ai`), claiming only three
paths — every other request passes through to what the host serves:

- `GET /` — curl/wget/httpie get the text landing card (saucer, waitlist counter, install
  one-liner); any other agent is proxied to the module's `site_base` — both apexes land browsers
  on the one site at the prod apex.
- `POST /waitlist -d email=…` — records the email in a per-apex D1 database, idempotent, with a
  positional ack; the card's "N identified flying objects" counter reads it back (≤1h stale per
  isolate, busted on join).
- `GET /install` / `/install.sh` — proxies the gateway's stamped `/ufo` from the module's
  `origin_base`; both apexes point at the one live fleet.

## Terminal onboarding flow

```text
curl -fsSL https://flyingobject.ai/install | sh
  |
  | POST /v1/onboard/{channel}
  | header: x-ufo-session=<host.pid.epoch>
  | body: "" or the user's answer
  v
Onboarding.advance(channel, session, body, install)
  |
  +-- no claim yet ---------------------> ask for work email
  |
  +-- email submitted ------------------> create claim, email code, ask for code
  |
  +-- code submitted -------------------> verify claim
  |
  +-- existing tenant for domain --------> join workspace (never asks for an invite)
  |
  +-- no tenant for domain -------------> ask for a one-time invite code
  |                                         redeem burns it, then create Tenant CR
  |                                         status + poll until Ready
  |
  +-- tenant Ready or joined -----------> mint bearer token
                                            emit token + workspace directives
```

The client is a pure renderer of tab-separated directive lines (`gateway_directives.py`): `say`,
`ask`, `choose`, `status`, `ufo` (the animation), `poll`, `token`, `workspace`, `install`,
`sendfile`, `logout`, `exit`. `install` self-installs the script into `~/.ufo/bin` on first run;
`token` and `workspace` land in `~/.ufo/credentials` (chmod 600) and `~/.ufo/workspace` — the
token is machine-consumed and never printed.

Creating a workspace is invite-gated; joining an existing one never is. The operator mints codes
with `ufo-control invite` — the plaintext prints once, only its hash lands in the
`ufo_control.invite_code` ledger, and redeeming burns the code and stamps the claim's `invite_id`
in one transaction, so one code opens exactly one workspace and a resolution retry never re-asks
for it. The shared tier gates identically (`SharedWorkspaces.exists` decides create vs join). An
invalid or used code re-asks and points at the waitlist.

## Setup handoff

The Slack setup page is reached with the credentials the client already holds:

```text
$(cat ~/.ufo/workspace)/surface/setup?token=$(cat ~/.ufo/credentials)
```

On first contact, the setup surface validates the token against the tenant workspace id and binds
it as a cookie:

```text
GET /surface/setup?token=...
  |
  +-- token valid for this workspace -> Set-Cookie: ufo_setup=...
  |                                    HttpOnly; Secure; SameSite=Strict
  |
  +-- token missing/invalid ---------> serve page without cookie;
                                      API calls answer 401
```

Every setup API re-verifies either `?token=` or the bound cookie. Owner-only APIs also check
`SurfaceContext.is_workspace_owner(email)`, because Slack credentials are workspace-wide.

## Slack setup state machine

```text
       +-----------------+
       | not_configured  |
       +-----------------+
              |
              | save bot token + signing secret
              | auth.test derives team and bot ids
              | put_credential() writes all slots
              v
       +-----------------+
       |     pending     |<------------------------+
       +-----------------+                         |
              |                                    |
              | Slack URL verification with        |
              | current signing secret writes      |
              | marker blob                        |
              v                                    |
       +-----------------+                         |
       |    connected    |-------------------------+
       +-----------------+   signing secret rotation
                             makes marker stale
```

Setup reads four workspace-global Slack slots:

```text
slack_bot_token
slack_signing_secret
slack_team_id
slack_bot_user_id
```

State calculation:

```text
missing any slot
  -> not_configured

all slots set, but no matching url_verified marker
  -> pending

all slots set, and marker fingerprint matches current signing secret
  -> connected
```

The setup page generates a Slack app manifest from the tenant public base URL
(`[connect] public_base_url`; events request URL `<public_base_url>/surface/slack`). The owner
pastes only the Bot User OAuth Token and Signing Secret; `slack_team_id` and `slack_bot_user_id`
are derived server-side with Slack `auth.test` when not provided manually.

## URL verification signal

Slack proves it reached the deploy with the stored signing secret on every signature-verified
request — the `url_verification` handshake or a real event:

```text
Slack Events API
  |
  | POST /surface/slack
  | x-slack-signature + x-slack-request-timestamp
  v
slack surface
  |
  +-- read slack_signing_secret
  |     unset + url_verification -> echo the challenge (nothing stored, no marker)
  |     unset + event            -> 401
  +-- verify HMAC and replay window
  +-- url_verification -> write marker, return {"challenge": "..."}
  +-- foreign team_id  -> ignored, no marker
  +-- event / click    -> write marker, admit turn
```

The marker (best-effort, written once per stored secret per process):

```text
workspaces/<workspace_id>/surfaces/slack/url_verified
{"fingerprint": sha256(signing_secret), "at": <seconds>}
```

The unsigned echo exists because Slack probes the request URL the instant the app is created from
the manifest — before the owner can hold the secret Slack mints with the app. Echoing the caller's
own challenge stores and grants nothing, and it spares the owner a failed-verification banner with
no reliable retry. Because any signed request from the configured team writes the marker (the
handshake carries no team and always counts), the member's first DM or @mention is what flips setup
to connected — no manual re-save of the request URL, and no false green from an event the team gate
drops.

The setup surface trusts the marker only while its fingerprint matches the currently stored
signing secret. Rotating the signing secret therefore moves the UI back to pending until Slack's
next signed request.

If the marker write fails, the request still succeeds; the marker is only setup status, not the
Slack contract.

## The setup seam

The setup surface configures a peer surface, which requires capabilities normal scoped extensions
do not have:

```text
SurfaceContext
  credential(slot)       read workspace credential slots
  put_credential(slot)   write workspace credential slots
  public_base_url        build externally reachable callback URLs
  is_workspace_owner()   gate workspace-wide setup writes
  blob                   read/write the Slack verification marker
```

Credential slots are keyed by workspace and slot name, not by extension, so the setup surface fills
the Slack surface's slots without the Slack surface needing a separate setup API.
`CredentialSlotUnset` is re-exported from `ufo.sdk.surfaces` so setup and Slack can distinguish
"slot absent" from other credential failures without importing private core modules.

## Hosted pack entry

The `assistant_hosted` pack includes both surfaces:

```text
assistant_hosted
  |
  +-- slack  durable member surface
  |     /surface/slack
  |
  +-- setup  live setup surface
        /surface/setup
```

The setup surface has no credential slots of its own and no writeback delivery. It is a tenant-local
request/response surface for configuring Slack.

## Operational edges

- Gateway environment misconfiguration fails loudly as a 500 before a flow error is rendered.
- Setup requires `UFO_TOKEN_SECRET`; missing secret is an operator error, not a 401.
- The setup cookie is `Secure`, `HttpOnly`, and `SameSite=Strict`.
- Only the owner, currently the earliest workspace member, can save credentials, fetch the manifest,
  or run the Slack test.
- Blank credential fields keep existing stored values; nothing is written until all provided values
  validate.

## File map

```text
infra/modules/edge/
  worker.js               apex landing card, waitlist, install proxy
  worker.test.mjs         its behavior proof (node --test, ci checks job)

control/src/ufo_control/
  gateway.py              Onboarding machine and apex routes
  gateway_directives.py   the directive wire the client renders
  gateway_claim.py        email -> 6-digit code -> constant-time verify
  gateway_invite.py       one-time invite codes gating workspace creation
  gateway_token.py        token mint/verify contract used by setup
  client/ufo              the POSIX terminal client

extensions/setup/ufo_ext_setup/
  manifest.py             setup surface registration
  surface.py              setup page, auth, status, manifest, save, test

extensions/slack/ufo_ext_slack/
  surface.py              Slack ingest, URL verification marker, event admission

core/src/ufo/
  ext/surface.py          privileged SurfaceContext seam
  sdk/surfaces.py         public re-exports for surface extensions

packs/assistant_hosted/
  ufo_pack_assistant_hosted.py
                           includes slack and setup in the hosted pack
```
