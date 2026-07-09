# Web onboarding portal structure

This documents the structure added by PR #3: a browser presentation for first-run onboarding and
a tenant setup surface for connecting Slack. The important invariant is that the web portal is only
a second renderer. It drives the same `Onboarding` claim machine as the terminal client.

## Component map

```text
                         control plane / apex
               +------------------------------------+
Browser        | ufo_control.gateway                | Kubernetes
GET / -------->|  GET / -> PORTAL_PAGE              | Tenant CRs
               |  POST /v1/onboard/web             |
               |       |                            |
               |       v                            |
               |  Onboarding.advance(channel=web) --+--> JoinOrProvision
               |       |                            |
               |       +--> onboard_claim rows      |
               |       +--> email code workflow     |
               |       +--> setup bearer token      |
               +------------------------------------+
                                |
                                | token + workspace directives
                                v
                         tenant workspace
               +------------------------------------+
Browser        | setup surface                      | core
/surface/setup |  status                            | SurfaceContext
?token=... --->|  slack/manifest                    |  credential()
               |  slack/credentials                 |  put_credential()
               |  slack/test                        |  public_base_url
               +----------------+-------------------+
                                |
                                | fills workspace-global Slack slots
                                v
               +------------------------------------+
Slack          | slack surface                      | core
Events API --->|  verify signing secret             | blob marker:
               |  url_verification -> challenge     | workspaces/<ws>/surfaces/slack/url_verified
               |  message events -> admit turn      |
               +------------------------------------+
```

The split is deliberate:

- `control/src/ufo_control/gateway.py` owns the pre-tenant sign-in and tenant resolution flow.
- `control/src/ufo_control/gateway_web.py` owns the self-contained browser page and directive JSON
  conversion.
- `extensions/setup/ufo_ext_setup/surface.py` owns the tenant-side Slack setup page and APIs.
- `extensions/slack/ufo_ext_slack/surface.py` owns Slack signature verification, URL verification,
  event admission, and writeback.
- `core/src/ufo/ext/surface.py` exposes the privileged surface seam needed by both setup and Slack.

## Apex onboarding flow

```text
Browser loads apex
  |
  | GET /
  v
PORTAL_PAGE
  |
  | POST /v1/onboard/web
  | header: x-ufo-session=<browser uuid>
  | body: "" or the user's answer
  v
Onboarding.advance("web", session, body, install=b"")
  |
  +-- no claim yet ---------------------> ask for work email
  |
  +-- email submitted ------------------> create claim, email code, ask for code
  |
  +-- code submitted -------------------> verify claim
  |
  +-- existing tenant for domain --------> join workspace
  |
  +-- no tenant for domain -------------> create Tenant CR
  |                                         status + poll until Ready
  |
  +-- tenant Ready or joined -----------> mint bearer token
                                            emit token + workspace directives
```

The endpoint returns JSON:

```text
tab-separated directive bytes
  -> parse_directives()
  -> {"directives": [{"verb": "...", "fields": ["..."]}]}
```

The page renders those directives:

```text
say       -> transcript line
ask       -> next form prompt
status    -> provisioning status line
poll      -> delayed POST with the same session id
token     -> saved in page memory only
workspace -> paired with token to build /surface/setup?token=...
exit != 0 -> visible error
```

The terminal and web flows share the same state machine, but not the same claim row unless both the
channel and session match. The web channel is `"web"`; the terminal keeps its own channel.

## Setup handoff

When onboarding completes, the page builds the tenant handoff link:

```text
workspace directive: https://<tenant>.<base-domain>
token directive:     <gateway HMAC bearer>

setup URL:
https://<tenant>.<base-domain>/surface/setup?token=<gateway HMAC bearer>
```

The token is machine-consumed. It is not echoed in a human-visible `say` directive.

On first contact, the setup surface validates the token against the tenant workspace id and binds it
as a cookie:

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

The setup page generates a Slack app manifest from the tenant public base URL:

```text
[connect] public_base_url = https://<tenant>.<base-domain>

events request URL:
https://<tenant>.<base-domain>/surface/slack
```

The owner only needs to paste:

```text
Bot User OAuth Token
Signing Secret
```

`slack_team_id` and `slack_bot_user_id` are derived server-side with Slack `auth.test` when they are
not provided manually.

## URL verification signal

Slack proves the deploy is reachable by sending a signed `url_verification` request to the Slack
surface:

```text
Slack Events API
  |
  | POST /surface/slack
  | x-slack-signature + x-slack-request-timestamp
  v
slack surface
  |
  +-- read slack_signing_secret
  +-- verify HMAC and replay window
  +-- parse url_verification challenge
  +-- best-effort write blob:
  |     workspaces/<workspace_id>/surfaces/slack/url_verified
  |     {"fingerprint": sha256(signing_secret), "at": <seconds>}
  |
  +-- return {"challenge": "..."}
```

The setup surface trusts that marker only while its fingerprint matches the currently stored
signing secret. Rotating the signing secret therefore moves the UI back to pending until Slack
verifies the URL again.

If the signing secret is not configured yet, Slack ingest returns 401 instead of raising a 500.
If the marker write fails, the challenge still succeeds; the marker is only setup status, not the
Slack contract.

## Why the core seam changed

The setup surface configures a peer surface. That requires capabilities normal scoped extensions do
not have:

```text
SurfaceContext
  credential(slot)       read workspace credential slots
  put_credential(slot)   write workspace credential slots
  public_base_url        build externally reachable callback URLs
  is_workspace_owner()   gate workspace-wide setup writes
  blob                   read/write the Slack verification marker
```

Credential slots are keyed by workspace and slot name, not by extension. The setup surface can
therefore fill the Slack surface slots without the Slack surface needing a separate setup API.

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
- A transient web POST failure re-shows the last prompt instead of stranding the browser session.

## File map

```text
control/src/ufo_control/
  gateway.py              shared Onboarding machine and apex routes
  gateway_web.py          browser page and directive JSON parser
  gateway_token.py        token mint/verify contract used by setup

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
