# Onboarding

Onboarding is CLI-first: the apex's public face is a Cloudflare edge worker (landing card,
waitlist, `/ufo`), and the terminal client is the primary onboarding renderer. The gateway serves
the client script and drives every screen server-side as directives over
`POST /v1/onboard/{channel}`. A browser sign-in (`GET /login` + `POST /v1/onboard/web`) is a
second renderer over the identical machine — see "Web login" below.

## Component map

```text
                     Cloudflare edge (infra/modules/edge)
               +--------------------------------------------+
curl / ------->|  GET /            CLI UA -> landing card   |
               |                   other UA -> prod site    |
               |  POST /waitlist   email -> D1 + mail queue |
               |  queue consumer   confirmation email       |
               |  GET /ufo         proxy of gateway /ufo    |
               +----------------------+---------------------+
                                      | origin
                                      v
                            hosted apex gateway
               +--------------------------------------------+
ufo client --->|  GET /ufo -> version-stamped POSIX client  |
browser ------>|  GET /login -> sign-in page (web renderer) |
               |  POST /v1/onboard/{channel} (web = JSON)   |
               |       |                                    |
               |       v                                    |
               |  Onboarding.advance ---------------------->+--> SharedWorkspaces
               |       +--> onboard_claim rows              |
               |       +--> email code workflow             |
               |       +--> invite_code burn (create only)  |
               |       +--> bearer token                    |
               +--------------------------------------------+
                                      |
                                      | token + workspace directives -> ~/.ufo/
                                      v
                         shared workspace runtime
               +--------------------------------------------+
ufo client --->| ufo surface                                |
               |  verify bearer · bind workspace            |
               |  admit turns · stream frames               |
               +--------------------------------------------+
```

## The apex edge

One worker fronts both apexes (`flyingobject.ai`, `testing.flyingobject.ai`), claiming only three
paths — every other request passes through to what the host serves:

- `GET /` — curl/wget/httpie get the text landing card (saucer, waitlist counter, install
  one-liner); any other agent lands on the one site at the prod apex — proxied on the site's own
  host, a redirect (query intact) from any other.
- `POST /waitlist -d email=…` — records the email in a per-apex D1 database, idempotent, with a
  positional ack; D1 tracks the confirmation's queued and sent states, so a duplicate repairs a
  failed publish without resending delivered mail. Delivery retries five times before its terminal
  failure is logged and re-armed by the dead-letter consumer. The card's "N identified flying
  objects" counter reads D1 (≤1h stale per isolate, busted on join).
- `GET /ufo` — proxies the gateway's stamped `/ufo` from the module's `origin_base`; both apexes
  point at the one live fleet.

## Terminal onboarding flow

```text
curl -fsSL https://flyingobject.ai/ufo | sh
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
  +-- existing workspace for domain -----> join it (never asks for an invite)
  |
  +-- no workspace for domain -----------> ask for a one-time invite code
  |                                         redeem burns it, then create workspace
  |
  +-- workspace ready -------------------> mint bearer token
                                            emit token + workspace directives
```

The earliest member's email domain owns the workspace. Resolution fails if that domain owns more
than one; creation derives the workspace UUID from the domain.

The client is a pure renderer of tab-separated directive lines (`gateway_directives.py`): `say`,
`ask`, `choose`, `status`, `ufo` (the animation), `poll`, `token`, `workspace`, `install`,
`sendfile`, `logout`, `exit`. `install` self-installs the script into `~/.ufo/bin` on first run;
`token` and `workspace` land in `~/.ufo/credentials` (chmod 600) and `~/.ufo/workspace` — the
token is machine-consumed and never printed.

Creating a workspace is invite-gated; joining an existing one never is.
`ufo-control invite <object-number>` mints a code for a waitlist object and prints the invite
email once, code included; only the hash lands in the `ufo_control.invite_code` ledger (14-day
expiry, one live code per object), and redeeming consumes the code and stamps the claim's
`invite_id` in one transaction, so one code opens exactly one workspace and a resolution retry
never re-asks for it. `SharedWorkspaces.exists` decides create versus join. An unknown, expired,
or consumed code re-asks with its exact ledger state; an unknown one points at the waitlist.

## Web login

`GET /login` (gateway; the edge worker's default case passes it through to origin) serves a
self-contained sign-in page — a second renderer of the identical `Onboarding` machine, never a
second machine. The page generates a session UUID, sends each answer to `POST /v1/onboard/web`
(header `x-ufo-session`, channel `web` — the claim index isolates it from a terminal session with
the same ref), and receives the directive lines as JSON (`gateway_web.parse_directives` inverts the
wire escaping exactly). It renders `say`/`ask`/`exit` and, on `token` + `workspace`, a signed-in
home card: the member's email, the workspace URL, the terminal install one-liner. No `install`
preamble is sent on the web channel, and the token never appears in a human-visible line.

When the claim's channel-verified email domain equals `UFO_ADMIN_EMAIL_DOMAIN`, `_signed_in` adds
one extra machine-consumed directive — `debugger <workspace-url>/surface/debug` — and the card
shows a "Session debugger" form that POSTs the token in its body (the debug surface exchanges it
for an httponly cookie and redirects; the bearer never rides a URL, so no access log captures it —
`spec.md` "Surfaces" covers that surface). The directive is emitted channel-blind — the terminal
client drops unknown verbs — and never emitted when the env is unset. The gate is the server's;
the page merely renders what arrives.

## Connecting Slack

The `assistant_hosted` shared fleet activates the `slack` extension alongside the ufo chat surface.
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
slack_connect ---------------> auth.test proves the token; the derived team and
  |                            bot-user ids persist as the surface's own identity
  |                            record, pinned to the token's fingerprint, and the
  |                            team uniquely registers to this UFO workspace
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
              | first signed event or click with   |
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

`slack_connect` runs `auth.test` when its identity is absent or stale, persists that identity, and
uniquely registers the derived team to the workspace. A registered request with no cached identity
starts the same proof after its response and asks Slack to retry; the retry continues through
normal admission.
`slack_app_manifest` renders the app manifest from `[connect] public_base_url`; its events request
URL is `<public_base_url>/surface/slack`. The owner enters only the Bot User OAuth Token and Signing
Secret; the team and bot-user ids are derived metadata, never entered and never slots.

## URL verification signal

Slack proves it reached the deploy with the stored signing secret on every signature-verified
event or interactive request:

```text
Slack callback
  |
  | POST /surface/slack or /surface/slack/interactive
  | x-slack-signature + x-slack-request-timestamp
  v
slack surface
  |
  +-- url_verification -> echo the challenge unbound (nothing stored, no marker)
  +-- parse team id as an untrusted routing hint
  +-- lookup unique team registration -> candidate workspace
  +-- read that workspace's slack_signing_secret
  +-- verify HMAC and replay window over the original bytes
  +-- bind the workspace only after verification
  +-- event / click -> write marker, admit turn
```

The marker (best-effort, written once per stored secret per process):

```text
workspaces/<workspace_id>/surfaces/slack/url_verified
{"fingerprint": sha256(signing_secret), "at": <seconds>}
```

The unsigned echo exists because Slack probes the request URL the instant the app is created from
the manifest — before the owner can hold the secret Slack mints with the app. Echoing the caller's
own challenge stores and grants nothing, and it spares the owner a failed-verification banner with
no reliable retry. The handshake carries no team, so it cannot select a workspace or mark one
connected. The member's first signed DM, @mention, or click writes the marker and flips setup to
connected.

`slack_connect` binds `team:<team_id>` to one UFO workspace under a database uniqueness constraint.
The request's team id selects only a candidate secret; the HMAC authorizes the request. An unknown
team and a mismatched signature return the same rejection, and URL verification creates no binding.

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
  ext.installations.bind    own declared surface only (the unique Slack team)
  ctx.speaker_is_owner()    the owner gate on the identity-deriving step
  ctx.public_base_url       renders the Events request URL
  ctx.blob                  the identity record + url-verified marker
```

Credential slots are keyed by workspace and slot name, not by extension, so fulfillment fills the
Slack surface's slots without the Slack surface needing a separate setup API.

## Pack placement

The `assistant_hosted` pack includes the slack extension — its durable member surface
(`/surface/slack`), its two credential slots, and its setup tools; connecting it is a conversation,
not a separate setup surface.

## Operational edges

- Gateway environment is validated at process startup.
- The `flyingobject.ai` domain is onboarded in Cloudflare Email Sending; the edge binding permits
  only `no-reply@flyingobject.ai` as its sender.
- The ufo surface requires `UFO_TOKEN_SECRET`; a missing secret is a configuration error, not a 401.
- Only the owner (the earliest workspace member) can request Slack credential entry or derive a
  missing Slack identity. Once derived, a teammate may call `slack_connect`; it idempotently
  refreshes the team registration and returns status without another `auth.test` call.
- A secret value is bounded (4 KiB) and travels bearer-authenticated on the existing chat
  transport; the fulfillment response is a `say` line, never a turn.

## File map

```text
infra/modules/edge/
  worker.js               apex landing card, waitlist mail, /ufo proxy
  worker.test.mjs         its behavior proof (node --test, ci checks job)

control/src/ufo_control/
  gateway.py              Onboarding machine and apex routes
  gateway_directives.py   the directive wire the client renders
  gateway_web.py          the /login page + JSON rendering of the same wire
  gateway_claim.py        email -> 6-digit code -> constant-time verify
  gateway_invite.py       one-time invite codes gating workspace creation
  gateway_token.py        bearer minting
  gateway_shared.py       workspace/member/default-agent writes
  gateway_store.py        claim custody
  rls.py                  shared serve role and workspace policies
  client/ufo              the POSIX terminal client (renders `secret` prompts)

extensions/slack/ufo_ext_slack/
  surface.py              workspace-qualified Slack ingest, identity record, URL marker
  tools.py                slack_connect and slack_app_manifest tools

extensions/ufo/ufo_ext_ufo/
  surface.py              the terminal wire: secret rendering + fulfillment

core/src/ufo/
  tools/builtins.py       request_credentials
  credentials.py          the sealed-request contract
  ext/surface.py          privileged SurfaceContext seam (pending/fulfill)
  sdk/surfaces.py         public re-exports for surface extensions

packs/assistant_hosted/
  ufo_pack_assistant_hosted.py
                           shared-mountable ufo + Slack surfaces and hosted capabilities
```
