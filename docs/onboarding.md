# Onboarding

Onboarding is CLI-first: the apex's public face is a Cloudflare edge worker (landing card,
waitlist, `/ufo`), and the terminal client is the primary onboarding renderer. The gateway serves
the client script and drives every screen server-side as directives over
`POST /v1/onboard/{channel}`. A browser sign-in (`GET /login` + `POST /v1/onboard/web`) is a
second renderer over the identical machine — see "Web login" below.

## Component map

```text
                Cloudflare edge — apex (infra/modules/edge)
               +--------------------------------------------+
curl / ------->|  GET /            CLI UA -> landing card   |
browser ------>|                   other UA -> prod site    |
               |  POST /waitlist   email -> D1 + mail queue |
               |  queue consumer   confirmation email       |
               |  GET /ufo         proxy of gateway /ufo    |
               |  GET /login       302 -> app.<host>/login  |
               +----------------------+---------------------+
                                      | origin (apex ingress)
                                      v
                    hosted gateway (apex + app-host ingress)
               +--------------------------------------------+
ufo client --->|  GET /ufo -> version-stamped POSIX client  |
browser ------>|  GET /login -> sign-in page (app host)     |
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
                                      | browser: host-only cookie (app host)
                                      v
                    shared workspace runtime (app host)
               +--------------------------------------------+
ufo client --->| ufo surface        verify bearer · admit   |
browser ------>| web (ufo_session) · debug (ufo_debug)      |
               |  bind workspace · admit turns · stream     |
               +--------------------------------------------+
```

## The apex edge

One worker fronts both apexes (`flyingobject.ai`, `testing.flyingobject.ai`), claiming only four
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
- `GET /login` — 302 to `app.<host>/login`. Sign-in lives on the app host (the sole authenticated
  host), so the apex carries no signed-in state and the session cookie is set and read same-origin.

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

The whole browser sign-in flow is same-origin on the **app host** (`app.<env>`), the sole
authenticated host. The apex `GET /login` 302s there (edge worker); on the app host the ingress
routes the front-door prefixes (`ufo.serve.RESERVED_HOST_PREFIXES`: `/login`, `/v1/onboard`,
`/ufo`) to `ufo-gateway`, while `/` and `/surface/*` stay on `ufo-serve` (nginx longest-prefix).
Two invariants hold this up by construction rather than by convention: the serve fleet **fails its
boot** if it mounts any route under a reserved prefix (`_assert_no_reserved_routes`), and every
session cookie is set through `ufo.sdk.http.set_session_cookie`, which takes no `Domain` — so a
cookie is always host-only and a session can never cross a subdomain, environment, or preview host
(a repo gate forbids raw `set_cookie` elsewhere).

`GET /login` serves a self-contained sign-in page — a second renderer of the identical
`Onboarding` machine, never a second machine. The page generates a session UUID, sends each answer
to the same-origin `POST /v1/onboard/web` (header `x-ufo-session`, channel `web` — the claim index
isolates it from a terminal session with the same ref), and receives the directive lines as JSON
(`gateway_web.parse_directives` inverts the wire escaping exactly). It renders `say`/`ask`/`exit`
and, on `token` + `workspace`, a signed-in home card: the member's email, the workspace URL, and
the terminal install one-liner. No `install` preamble is sent on the web channel, and the token
never appears in a human-visible line.

When the claim's channel-verified email domain equals `OPERATOR_EMAIL_DOMAIN`, `_signed_in` adds
one extra machine-consumed directive — `debugger <workspace-url>/surface/debug` — and the card
also shows a "Session debugger" form that POSTs the token in its body (the debug surface exchanges
it for its `ufo_debug` cookie and redirects; the bearer never rides a URL into the debugger,
so no access log captures it — `spec.md` "Surfaces" covers that surface). The directive is emitted
channel-blind — the terminal client drops unknown verbs — and never emitted when the env is unset.
The gate is the server's; the page merely renders what arrives.

## Connecting Slack

The `assistant_hosted` shared fleet activates the `slack` extension alongside the ufo chat surface.
There are two install paths — both land the same per-workspace bot token and identity record, and
the agent drives either in chat with `slack_connect` (default `method="oauth"`).

**Preferred — OAuth on the deploy's own app.** The deploy holds one Slack app; its client id, client
secret, and signing secret are read from env in-process (never per-workspace BYOK, never the
sandbox). The owner installs it with one click:

```text
owner: connect slack
  |
  v
slack_connect ---------------> not_installed + an "Add to Slack" link. The link's state is a
  |                            Fernet-sealed handoff to the owner and the bot-token slot
  |                            (begin_credential_authorization), TTL-bound.
  |
owner opens the link --------> slack.com/oauth/v2/authorize?client_id=…&scope=…
  |                            &redirect_uri=<base>/surface/slack/oauth&state=<sealed>
  |
Slack redirects -------------> GET /surface/slack/oauth?code=…&state=…
  |                            oauth_callback: open the seal (it names this workspace + owner),
  |                            exchange the code at oauth.v2.access for this workspace's xoxb bot
  |                            token, store it, bind team:<team_id>, write the identity record
  v
first DM / @mention ---------> writes the url-verified marker; slack_connect reads connected
```

Only the owner mints the link (the bot is shared); the seal binds the install to the owner and the
workspace, so a forged callback cannot bind another team to this workspace. The bot token is
per-workspace, minted by the callback rather than pasted; the client and signing secrets belong to
the deploy's app and live in env.

**Alternative — bring-your-own app** (`slack_connect method="manifest"`, driven by the
`slack-app-setup` skill). Used when the owner wants their own Slack app, or when the deploy has no
OAuth app configured (`slack_connect` says so and points here). `slack_app_manifest` renders the
exact app YAML; the owner creates the app at api.slack.com and fills the per-workspace
`slack_bot_token` and `slack_signing_secret` slots through `request_credentials` (privately, never
in chat); `slack_connect` derives the identity with `auth.test`.

## Slack setup state machine

Both paths converge on the same downstream states — `pending` once identity is proven, `connected`
once a signature-verified request has stamped the url-verified marker:

```text
   OAuth: not_installed --(callback)--> pending --(first verified event)--> connected
manifest: not_configured --(secrets + auth.test)--> pending --(first verified event)--> connected
```

```text
no bot token / identity mismatch, method=oauth   -> not_installed + "Add to Slack" link (owner)
no bot token / identity mismatch, method=manifest -> not_configured + manifest instructions
identity present, no matching url-verified marker  -> pending
marker fingerprint matches the verifying secret    -> connected
```

## Request verification

The untrusted team id selects one `surface_installation`; that workspace's signing secret — its own
`slack_signing_secret` slot (a bring-your-own app) if set, else the deploy env secret (an OAuth
install) — verifies the original bytes before any workspace is bound:

```text
Slack callback
  |
  | POST /surface/slack or /surface/slack/interactive
  | x-slack-signature + x-slack-request-timestamp
  v
slack surface
  |
  +-- url_verification -> echo the challenge (unbound; it carries no team)
  +-- parse team id as an untrusted routing hint
  +-- lookup the unique team registration -> candidate workspace
  +-- verifying secret = workspace's own slot, else the deploy env secret
  +-- verify HMAC + replay window over the original bytes; bind only after
  +-- event / click -> write the url-verified marker, admit the turn
```

An unknown team, a workspace with no signing secret, and a bad signature return the same rejection.
The OAuth callback is the one route not signature-verified: it is a browser redirect authenticated
by its Fernet-sealed state, and it resolves its workspace from that state alone.

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

slack_connect tool (ToolContext) — OAuth path
  ctx.begin_credential_authorization  seals the install handoff to the owner + bot-token slot
  ctx.speaker_is_owner()              the owner gate on minting the link / deriving identity
  ctx.public_base_url                 renders the OAuth redirect / authorize URL

slack_connect tool (ToolContext) — manifest path
  ctx.ext.credentials.get(slot)       the two per-workspace slots (bot token, signing secret)
  SlackIdentityResolver (auth.test)   derives + persists the identity for a pasted token
  ctx.ext.installations.bind          binds the unique Slack team to this workspace

slack oauth_callback (SurfaceContext)
  ctx.open_credential_authorization   recovers the sealed {workspace, owner, slot}
  ctx.fulfill_credential_request      verify seal -> encrypted bot-token slot
  ctx.bind_installation               binds the unique Slack team to this workspace
  ctx.blob                            writes the identity record
```

Credential slots are keyed by workspace and slot name, not by extension, so the callback (OAuth) and
`request_credentials` fulfillment (manifest) both fill the Slack surface's slots without a separate
setup API.

## Pack placement

The `assistant_hosted` pack includes the slack extension — its durable member surface
(`/surface/slack`, with the OAuth callback at `/surface/slack/oauth`), its two per-workspace slots,
the deploy Slack app's env secrets, and its `slack_connect` / `slack_app_manifest` tools plus the
`slack-app-setup` skill; connecting it is a conversation, not a separate setup surface.

## Operational edges

- Gateway environment is validated at process startup.
- The `flyingobject.ai` domain is onboarded in Cloudflare Email Sending; the edge binding permits
  only `no-reply@flyingobject.ai` as its sender.
- The ufo surface requires `UFO_TOKEN_SECRET`; a missing secret is a configuration error, not a 401.
- Only the owner (the earliest workspace member) can mint the "Add to Slack" link or derive a
  bring-your-own-app identity; the OAuth callback installs into the workspace the sealed state names.
  A teammate may call `slack_connect` to read the install status but never installs.
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
  surface.py              Slack ingest, OAuth install callback, identity record + url marker
  tools.py                slack_connect (oauth + manifest) and slack_app_manifest tools

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
