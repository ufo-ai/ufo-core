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
               |       +--> domain grant burn (create only) |
               |       +--> bearer token                    |
               |  slack connect delivery (background poll)  |
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

- `GET /` — curl/wget/httpie get the text landing card (craft, the two counters, install
  one-liner); any other agent lands on the one site at the prod apex — proxied on the site's own
  host, a redirect (query intact) from any other.
- `POST /waitlist -d email=…` — the one join, reached from either renderer: a curl user types the
  command the card prints, a browser clicks any craft (or the page's `Join Waitlist` hail, the
  keyboard route) and the panel posts the same form encoding, rendering the returned ack
  verbatim. Records the email in a per-apex D1 database, idempotent, with a
  positional ack; D1 tracks the confirmation's queued and sent states, so a duplicate repairs a
  failed publish without resending delivered mail. Delivery retries five times before its terminal
  failure is logged and re-armed by the dead-letter consumer. The card's waitlist counter reads D1
  (≤1h stale per isolate, busted on join); its workspace counter reads the gateway's `/fleet`.
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
  +-- existing workspace for domain -----> join it
  |
  +-- domain holds a live grant ---------> burn it, then create workspace
  |
  +-- domain holds no live grant --------> say why, name the waitlist, exit
  |
  +-- workspace ready -------------------> mint bearer token
                                            emit token + workspace directives
                                            admin: choose (billing) · teammate: ask
```

The initial member's email domain identifies the workspace. Resolution fails if that domain
identifies more than one; creation derives the workspace UUID from the domain.
`SharedWorkspaces.ensure` reports whether the member is an admin (`EnsuredWorkspace.admin`), and
the last screen turns on it: an admin is offered `Set up billing` as a `choose`, a joined teammate
gets the ordinary `ask` prompt. The `workspace` directive has already landed by then, so whichever
option the admin picks posts to
`/surface/ufo` as their first chat message and the agent drives billing from there — the gateway
mints no billing link and adds no billing directive.

### Driving billing locally

Billing lives in the `metronome` extension, which `assistant_hosted` bundles and the local
`assistant` pack does not — a dev stack has no business shipping usage to a billing vendor. The
`assistant_billing` pack is the local assistant bundle plus that one extension, selected through
`UFO_DEV_PACK`, so the admin's `Set up billing` choice has something to service on a laptop:

```
export STRIPE_SECRET_KEY=sk_test_…                     # Stripe TEST mode
export STRIPE_BILLING_PORTAL_CONFIGURATION_ID=bpc_…    # portal config, subscription_update off
export METRONOME_BEARER_TOKEN=…                        # Metronome SANDBOX token
export METRONOME_PACKAGE_ALIAS=base-plan               # an existing Package's alias
UFO_DEV_PACK=assistant_billing docker compose up
```

Sign in at `:8080` (the code prints to `docker compose logs gateway`); the first member of a fresh
domain owns the workspace and gets the billing choice. The four settings are read host-side by the
tool and the job — never in the sandbox — and the flow creates real objects in whichever account
they name, so keep it pointed at test mode and sandbox. Without them the chain still runs to the
tool and refuses there, naming each missing setting; with them it continues into the portal link and
the activation job provisions the contract on the next tick.
`extensions/metronome/tests/integration/test_billing_providers.py` drives the same providers from
pytest under the same four settings.

The client is a pure renderer of tab-separated directive lines (`gateway_directives.py`): `say`,
`ask`, `choose`, `status`, `ufo` (the animation), `poll`, `token`, `workspace`, `install`,
`sendfile`, `logout`, `exit`. `install` self-installs the script into `~/.ufo/bin` on first run;
`token` and `workspace` land in `~/.ufo/credentials` (chmod 600) and `~/.ufo/workspace` — the
token is machine-consumed and never printed.

Creating a workspace is invite-gated; joining an existing one never is. A grant names one email
domain, so the verified email *is* the redemption: `ufo-control invite <object-number> <email>`
grants a waitlist object's domain and emails the invitation to it, and the flow burns the domain's
live grant and stamps the claim's `invite_id` in one transaction — no third prompt, and nothing for
the member to carry from the invitation back into the terminal. The invitation therefore holds no
secret, and a colleague at the granted domain is identified by the same grant, which is who usually
runs the installer. `SharedWorkspaces.exists` decides create versus join. Delivery rides the
gateway's own SES sender, read before the object spends its one live grant; a grant whose invitation
fails to send still stands, so the verb reports that rather than withdrawing it.

The `ufo_control.invite_code` ledger keeps one live grant per object and one per domain (14-day
expiry, both database-enforced), so one grant opens exactly one workspace and a resolution retry
proceeds without consulting it again. Each refusal — no grant, expired, already burned — says why,
names the waitlist, and exits, because the member holds nothing that could change the answer. The
claim keeps its verified email, so re-running the installer after a grant lands resolves it.

## Signup Slack Connect invitation

A claim that both burned a grant and created a workspace — `invite_id` and
`resulting_workspace_id` both set — earns one public channel in UFO's *own* Slack workspace and one
Slack-generated Slack Connect invitation to the email that signed up. A member joining an existing
workspace never burns a grant, so the same test excludes them, and only the earliest completed claim
of a workspace materializes: one channel per customer, never a second invitation.

The completed claim is the durable event source. `gateway_slack_connect.py` polls it on both gateway
replicas, materializes `ufo_control.slack_connect_delivery` rows, and claims one due row under a
lease (`FOR UPDATE SKIP LOCKED`, compare-and-set on `worker_id` for every write). Signup itself
never waits: `Onboarding._resolve` signs the member in whether or not Slack is reachable.

```text
completed invite-wall claim
  |
  +-- materialize   one delivery row, channel name ext-<domain-label>-flyingobject
  +-- claim         lease one due row; an expired lease is another replica's to recover
  +-- auth.test     the token must name UFO_CONTROL_SLACK_CONNECT_TEAM_ID, else the row fails
  +-- create        the deterministic channel; name_taken resolves by exact-name lookup
  +-- reconcile     only when invite_attempted_at is set: outgoing invite, else channel sharing
  +-- inviteShared  the claim's email, external_limited=true — a private, post-only channel
  +-- delivered     invitation id persisted, then the row settles
```

The unique channel is the idempotency boundary: a lost create response recovers by name, a lost
invitation response recovers from Slack's own state, and ambiguity Slack will not expose lands
`failed` rather than sending a blind duplicate. Transport failures, 429 (bounded `Retry-After`),
5xx, and documented transient Slack errors return the row to `pending` behind a bounded schedule;
authentication, scope, plan, policy, invalid-email, and inconsistent-channel errors are terminal.
`ufo-control slack-connect-retry <claim-id>` re-arms one failed row after its cause is fixed — it
never sends directly and never touches a delivered row.

UFO's app here (`control/slack-connect-app.yaml`) is not the customer-installed Slack app below. It
lives only in the operator workspace, makes outbound Web API calls only, and holds one gateway-only
bot token (`UFO_CONTROL_SLACK_CONNECT_BOT_TOKEN`, its own Secret through an explicit `secretKeyRef`
— never `ufo-platform-secrets`, `ufo-serve`, an extension, or a workspace `CredentialSlot`).
`UFO_CONTROL_SLACK_CONNECT_ENABLED` defaults to false; enabled, a missing token or team ID fails
gateway startup. Rotation is: update the Secrets Manager value, wait for External Secrets, restart
the gateway deployment.

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

This is the app a *customer* installs in their own Slack workspace — nothing to do with the signup
inviter above, which represents UFO in UFO's workspace and shares none of its credentials.
The `assistant_hosted` shared fleet activates the `slack` extension alongside the ufo chat surface.
There are two install paths — both land the same per-workspace bot token and identity record, and
the agent drives either in chat with `slack_connect` (default `method="oauth"`).

**Preferred — OAuth on the deploy's own app.** The deploy holds one Slack app; its client id, client
secret, and signing secret are read from env in-process (never per-workspace BYOK, never the
sandbox). An admin installs it with one click:

```text
admin: connect slack
  |
  v
slack_connect ---------------> not_installed + an "Add to Slack" link. The link's state is a
  |                            Fernet-sealed handoff to the admin and the bot-token slot
  |                            (begin_credential_authorization), TTL-bound.
  |
admin opens the link --------> slack.com/oauth/v2/authorize?client_id=…&scope=…
  |                            &redirect_uri=<base>/surface/slack/oauth&state=<sealed>
  |
Slack redirects -------------> GET /surface/slack/oauth?code=…&state=…
  |                            oauth_callback: open the seal (it names this workspace + member),
  |                            exchange the code at oauth.v2.access for this workspace's xoxb bot
  |                            token, store it, bind team:<team_id>, write the identity record
  v
first DM / @mention ---------> writes the url-verified marker; slack_connect reads connected
```

Only an admin mints the link (the bot is shared); the seal binds the install to that member and the
workspace, so a forged callback cannot bind another team to this workspace. The bot token is
per-workspace, minted by the callback rather than pasted; the client and signing secrets belong to
the deploy's app and live in env.

**Alternative — bring-your-own app** (`slack_connect method="manifest"`, driven by the
`slack-app-setup` skill). Used when an admin wants their own Slack app, or when the deploy has no
OAuth app configured (`slack_connect` says so and points here). `slack_app_manifest` renders the
exact app YAML; the admin creates the app at api.slack.com and fills the per-workspace
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
no bot token / identity mismatch, method=oauth   -> not_installed + "Add to Slack" link (admin)
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
  gates: speaking member · workspace admin · declared slots · credential key
  seals: {workspace, member, slots} under the credential Fernet, TTL-bound
  ends the turn; the request rides the terminal frame / writeback

ufo surface fulfillment (POST /surface/ufo/{channel} + x-ufo-secret headers)
  SurfaceContext.credential_prompt_pending(sealed, slot)   the per-slot render gate
  SurfaceContext.fulfill_credential_request(...)            verify seal -> encrypted store
                                                            + that slot's own marker

slack_connect tool (ToolContext) — OAuth path
  ctx.begin_credential_authorization  seals the install handoff to the admin + bot-token slot
  ctx.speaker_is_admin()              the admin gate on minting the link / deriving identity
  ctx.public_base_url                 renders the OAuth redirect / authorize URL

slack_connect tool (ToolContext) — manifest path
  ctx.ext.credentials.get(slot)       the two per-workspace slots (bot token, signing secret)
  SlackIdentityResolver (auth.test)   derives + persists the identity for a pasted token
  ctx.ext.installations.bind          binds the unique Slack team to this workspace

slack oauth_callback (SurfaceContext)
  ctx.open_credential_authorization   recovers the sealed {workspace, member, slot}
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

- Gateway environment is validated at process startup, before the shared engine is initialized.
- The signup Slack Connect token is the gateway's alone; a team-ID mismatch fails that delivery for
  operator review instead of mutating a channel, and no persisted error, log line, or repr carries
  the token. The poller itself only ever stops on shutdown, so no fault strands later signups.
- The `flyingobject.ai` domain is onboarded in Cloudflare Email Sending; the edge binding permits
  only `no-reply@flyingobject.ai` as its sender.
- The ufo surface requires `UFO_TOKEN_SECRET`; a missing secret is a configuration error, not a 401.
- Only a workspace admin can mint the "Add to Slack" link or derive a
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
  gateway_invite.py       one-time domain grants gating workspace creation
  gateway_slack_connect.py
                          the signup Slack Connect delivery: table, client, leased workflow
  gateway_token.py        bearer minting
  gateway_shared.py       workspace/member/default-agent writes
  gateway_store.py        claim custody
  schema.py               the ufo_control schema, shaped by the deploy's `ufo-control migrate`
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
