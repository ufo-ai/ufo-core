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
               |  POST /waitlist   email -> D1 + mail queue |
               |  queue consumer   confirmation email       |
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
                         tenant workspace (in chat)
               +--------------------------------------------+
ufo client --->| slack setup tools (slack extension)        |
"connect       |  slack_connect · slack_app_manifest        |
 slack"        |  request_credentials (core builtin)        |
               |    -> secret directives -> hidden prompts  |
               |    -> fulfillment fills the Slack slots    |
               +----------------------+---------------------+
                                      | workspace-global Slack slots
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
  one-liner); any other agent lands on the one site at the prod apex — proxied on the site's own
  host, a redirect (query intact) from any other.
- `POST /waitlist -d email=…` — records the email in a per-apex D1 database, idempotent, with a
  positional ack; a first insert queues a matching confirmation email, retried five times before
  its terminal failure is logged by the dead-letter consumer. The card's "N identified flying
  objects" counter reads D1 (≤1h stale per isolate, busted on join).
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
for it. Both tiers resolve tenant-first through the same `TenantJoin`: a domain with a dedicated
Ready tenant joins it codeless, so an org with its own deploy is never forked onto a parallel
shared row; on the shared tier only a tenantless domain falls through to the workspace-row path,
where `SharedWorkspaces.exists` decides create vs join. An invalid or used code re-asks and points
at the waitlist.

## Connecting Slack in chat

The member says "connect slack"; the agent loads the `slack-app-setup` skill and drives every step
with tools — there is no setup page and no bespoke endpoint:

```text
member: connect slack
  |
  v
slack_connect ---------------> not_configured / pending / connected, + events_url
  |
slack_app_manifest(name) ----> the exact app YAML; member creates the app at api.slack.com
  |
request_credentials ---------> seals {workspace, owner, slots}; turn ends
  |                            terminal renders one `secret` line per still-
  |                            unanswered slot; shell prompts with hidden input,
  |                            POSTs each value with the seal — fulfillment
  |                            verifies workspace, member, slot, freshness, then
  |                            writes the encrypted slot and that slot's marker;
  |                            NO turn admitted, transcript never sees a byte
  |
slack_connect ---------------> auth.test proves the token; a signed request can
  |                            run the same proof. The derived team and bot-user
  |                            ids persist as the surface's own identity record,
  |                            pinned to the token's fingerprint (owner-only tool)
  v
first DM / @mention ---------> url-verified marker flips slack_connect to connected
```

Only the workspace owner can fill or finish (slots are workspace-global — the one bot every member
shares); the seal binds fulfillment to the member who asked, and each fulfilled or expired prompt
stops rendering individually (a per-slot blob marker gates it), so a disconnect mid-entry re-asks
only what is missing and a token rotation stales the identity record automatically. On Slack
itself the same frame renders as a hint to open the terminal — no surface ever collects a secret
in chat.

## Slack setup state machine

```text
       +-----------------+
       | not_configured  |
       +-----------------+
              |
              | request_credentials fulfillment stores
              | the bot token + signing secret;
              | slack_connect or a signed request derives
              | the identity record (auth.test,
              | fingerprint-pinned)
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

`slack_connect` reads two workspace-global secret slots — `slack_bot_token` and
`slack_signing_secret` — plus the surface's identity record:

```text
missing a secret slot
  -> not_configured

secrets set, identity absent or from a rotated token
  -> identity resolution derives + persists {token fingerprint, team_id, bot_user_id}

identity valid, but no matching url_verified marker
  -> pending

marker fingerprint matches current signing secret
  -> connected
```

`slack_connect` and signed Slack requests use the same `auth.test` identity resolution. A request
with no identity starts that proof after its response and asks Slack to retry; the retry continues
through normal admission. `slack_app_manifest` renders the app manifest from the tenant public
base URL (`[connect] public_base_url`; events request URL
`<public_base_url>/surface/slack`). The owner enters only the Bot User OAuth Token and Signing
Secret; the team and bot-user ids are derived metadata, never entered and never slots.

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

`slack_connect` trusts the marker only while its fingerprint matches the currently stored
signing secret. Rotating the signing secret therefore reads as pending until Slack's next signed
request.

If the marker write fails, the request still succeeds; the marker is only setup status, not the
Slack contract.

## The seams underneath

```text
request_credentials (core builtin)
  gates: speaking member · workspace owner · declared slots · credential key
  seals: {workspace, member, slots} under the credential Fernet, TTL-bound
  ends the turn; the request rides the terminal frame / writeback

ufo surface fulfillment (POST /surface/ufo/{channel} + x-ufo-secret headers)
  SurfaceContext.credential_prompt_pending(sealed, slot)   the per-slot render gate
  SurfaceContext.fulfill_credential_request(...)            verify seal -> encrypted store
                                                            + that slot's own marker

slack tools (ToolContext)
  ext.credentials.get       own declared slots only (the two secrets)
  ctx.speaker_is_owner()    the owner gate on the identity-deriving step
  ctx.public_base_url       renders the Events request URL
  ctx.blob                  the identity record + url-verified marker
```

Credential slots are keyed by workspace and slot name, not by extension, so fulfillment fills the
Slack surface's slots without the Slack surface needing a separate setup API.

## Hosted pack entry

The `assistant_hosted` pack includes the slack extension — its durable member surface
(`/surface/slack`), its four credential slots, and its setup tools; connecting it is a
conversation, not a surface.

## Operational edges

- Gateway environment misconfiguration fails loudly as a 500 before a flow error is rendered.
- The `flyingobject.ai` domain is onboarded in Cloudflare Email Sending; the edge binding permits
  only `no-reply@flyingobject.ai` as its sender.
- The ufo surface requires `UFO_TOKEN_SECRET`; missing secret is an operator error, not a 401.
- Only the owner, currently the earliest workspace member, can request credential entry or run
  `slack_connect`; a joined teammate's attempt raises before anything seals or writes.
- A secret value is bounded (4 KiB) and travels bearer-authenticated on the existing chat
  transport; the fulfillment response is a `say` line, never a turn.

## File map

```text
infra/modules/edge/
  worker.js               apex landing card, waitlist mail, install proxy
  worker.test.mjs         its behavior proof (node --test, ci checks job)

control/src/ufo_control/
  gateway.py              Onboarding machine and apex routes
  gateway_directives.py   the directive wire the client renders
  gateway_claim.py        email -> 6-digit code -> constant-time verify
  gateway_invite.py       one-time invite codes gating workspace creation
  gateway_token.py        the bearer mint/verify contract
  client/ufo              the POSIX terminal client (renders `secret` prompts)

extensions/slack/ufo_ext_slack/
  surface.py              Slack ingest, identity record, URL verification marker
  tools.py                slack_connect, slack_app_manifest

extensions/ufo/ufo_ext_ufo/
  surface.py              the terminal wire: secret rendering + fulfillment

core/src/ufo/
  tools/builtins.py       request_credentials
  credentials.py          the sealed-request contract
  ext/surface.py          privileged SurfaceContext seam (pending/fulfill)
  sdk/surfaces.py         public re-exports for surface extensions

packs/assistant_hosted/
  ufo_pack_assistant_hosted.py
                           includes slack and setup in the hosted pack
```
