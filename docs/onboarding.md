# Onboarding

Onboarding is CLI-first: the apex's public face is a Cloudflare edge worker, and the terminal
client is the primary onboarding renderer. The gateway serves a bootstrap installer at `/ufo`
that downloads the native client binary for the platform (`/ufo/bin/{target}`), and drives every
screen server-side as directives over `POST /v1/onboard/{channel}`. A browser sign-in
(`GET /login` + `POST /v1/onboard/web`) is a second renderer over the identical machine — see
"Web login" below.

## Component map

```text
                Cloudflare edge — apex (infra/modules/edge)
               +--------------------------------------------+
curl / ------->|  public door -> environment origin         |
browser ------>|  waitlist -> D1 + mail queue               |
               +----------------------+---------------------+
                                      | origin (apex ingress)
                                      v
                    hosted gateway (apex + app-host ingress)
               +--------------------------------------------+
ufo client --->|  GET /ufo -> version-stamped POSIX client  |
browser ------>|  GET /login -> sign-in page (app host)     |
               |  GET /v1/onboard/auth/* -> WorkOS (Google) |
               |  POST /v1/onboard/{channel} (web = JSON)   |
               |       |                                    |
               |       v                                    |
               |  Onboarding.advance ---------------------->+--> SharedWorkspaces
               |       +--> onboard_claim rows              |
               |       +--> WorkOS email verification       |
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

One worker per apex fronts `flyingobject.ai` and `testing.flyingobject.ai`. Each uses its own
`origin_base` for gateway-backed responses — the client script, the fleet count, and the
onboarding wire — its own D1 waitlist database, and its own confirmation queue. Unmatched requests
pass through to what that host serves. Sign-in lives on the app host, so the apex carries no
signed-in state and the session cookie stays same-origin.

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
  +-- email submitted ------------------> create claim, WorkOS emails a code, ask for it
  |
  +-- code submitted -------------------> verify claim
  |
  +-- one membership/domain candidate ---> join it
  |
  +-- several candidates ----------------> choose workspace
  |
  +-- membership plus live grant --------> choose membership or new workspace
  |
  +-- no candidate, live domain grant ---> burn it, then create workspace
  |
  +-- no candidate or grant -------------> say why, name the waitlist, exit
  |
  +-- workspace ready -------------------> mint bearer token
                                            emit token + workspace directives
                                            admin: choose (billing) · teammate: ask
```

WorkOS answers one question: does this person control this address. The terminal channel applies the
work-email denylist, then posts the address to Magic Auth, which emails the six-digit code and owns
its expiry and attempt count — a wrong code leaves the prompt standing, and a code WorkOS will not
redeem again ends the claim, so the member starts over. The browser channel collects the same email
and code on its own page, or takes the `Continue with Google` hop (see "Web login") — either way the
denylist runs before anything is sent. The gateway reads `WORKOS_API_KEY`, `WORKOS_CLIENT_ID`, and
`WORKOS_REDIRECT_URI` at startup and refuses to start without all three. Everything past the
verified address is the gateway's own: the claim, the resolution below, the invite gate, and the
bearer.

The verified address identifies every exact membership plus the workspace its email domain names.
One candidate opens directly; several are offered as a `choose` before a token is minted. Selecting
a domain workspace creates the member there, while selecting an exact membership changes no other
workspace. A live domain grant adds creation as another choice when the address already belongs to
a workspace. With no candidate, creating the domain workspace still requires that grant. A domain
claimed by two workspaces fails loud without creating another. Creation derives the workspace UUID
from the domain.
`SharedWorkspaces.create` and `join` report whether the member is an admin
(`EnsuredWorkspace.admin`), and
the last screen turns on it: an admin is offered `Connect Slack`, `Set up billing`, and a tour as a
`choose`, a joined teammate gets the ordinary `ask` prompt. The `workspace` directive has already
landed by then, so whichever option the admin picks posts to
`/surface/ufo` as their first chat message and the agent drives it from there — the gateway
mints no billing link, no install link, and adds no directive for either.

Slack leads that list because the install is what moves a customer onto their own surface, and the
browser card carries the same push as a `slack` directive naming what to ask. Neither renderer can
carry the link itself: an "Add to Slack" URL seals one workspace and one member for fifteen minutes
(`CREDENTIAL_REQUEST_TTL_SECONDS`), so only the turn that answers the member can mint one that still
works when they click it. Both are admin-only, since only an admin installs.

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

Sign in at `:8080`; the first member of a fresh domain owns the workspace and gets the billing
choice. The four settings are read host-side by the tool and the
job — never in the sandbox — and the flow creates real objects in whichever account they name, so
keep it pointed at test mode and sandbox. Without them the chain still runs to the
tool and refuses there, naming each missing setting; with them it continues into the portal link and
the activation job provisions the contract on the next tick.
`extensions/metronome/tests/integration/test_billing_providers.py` drives the same providers from
pytest under the same four settings.

The client is a pure renderer of tab-separated directive lines (`gateway_directives.py`): `say`,
`ask`, `choose`, `status`, `ufo` (the animation), `poll`, `token`, `workspace`, `install`, `file`,
`logout`, `exit`. `file` names one artifact a turn shared and the link that opens it; `install`
downloads the served binary into `~/.ufo/bin` (first run, and again whenever the deploy serves a
newer version than the client reports in `x-ufo-script`);
`token` and `workspace` land in `~/.ufo/credentials` (chmod 600) and `~/.ufo/workspace` — the
token is machine-consumed and never printed.

Creating a workspace is invite-gated; joining an existing one never is. A grant names one email
domain, so the verified email *is* the redemption: `ufo-control invite <email>`
grants that email's domain a workspace and emails the invitation to it. `--object` names a
waitlist object where one exists; a grant approved from the intake form answers a form response,
which is no waitlist object, so it carries no number. Its `--business` and `--goals`
options carry what the intake form collected — what their company does, and what they want an agent
to do; they are given together or not at all,
and they open the main agent's prompt in the workspace that grant creates. They arrive through
`ufo.untrusted.wall`, attributed to the form and never as instructions: the form is public, whoever
filled it proved nothing, and the employee who later signs in never typed a word of it — so the
prompt states that the member is believed over it wherever the two differ. A grant minted without
them leaves a workspace reading exactly as one `ufoctl init` seats. The flow burns the domain's
live grant and stamps the claim's `invite_id` in one transaction — no third prompt, and nothing for
the member to carry from the invitation back into the terminal. The invitation therefore holds no
secret, and a colleague at the granted domain is identified by the same grant, which is who usually
runs the installer. An exact membership admits its workspace without spending a domain grant. A
live grant appears as a separate creation choice when that address already holds a membership, so
the member decides which workspace becomes active and the grant cannot be spent into the wrong one.
Delivery rides the
gateway's own SES sender, read before the object spends its one live grant; a grant whose invitation
fails to send still stands, so the verb reports that rather than withdrawing it.

The `ufo_control.invite_code` ledger keeps one live grant per object and one per domain (14-day
expiry, both database-enforced), so one grant opens exactly one workspace and a resolution retry
proceeds without consulting it again. Each refusal — no grant, expired, already burned — says why,
names the waitlist, and exits, because the member holds nothing that could change the answer. The
claim keeps its verified email, so re-running the installer after a grant lands resolves it.

## Signup Slack Connect invitation

A customer earns one public channel in UFO's *own* Slack workspace and one Slack-generated Slack
Connect invitation, from either of two durable facts: a granted email domain, or a signup that
created a workspace. The grant fires while signup is invite-gated, at approval — before that
customer signs up — so the channel is open by the time they read the invitation email. The signup that
*created* a workspace fires once the gate comes off and no grant exists to key on, so
`UFO_INVITE_REQUIRED=false` costs this feature nothing. Creating is the whole test, not merely
completing — a contractor, an advisor, or operator staff joining a workspace their own domain does
not name is no customer, and counting them would open a channel for a domain the invite gate would
then refuse.

The domain is the key, and neither fact is: a granted customer who then signs up satisfies both and
still lands on one row, so nobody is invited twice. Re-granting a domain whose first grant lapsed
likewise finds its row already there and sends nothing. Slack cannot help us do better either way, since
`conversations.listConnectInvites` names the inviter and never the invitee.

`gateway_slack_connect.py` polls both facts on either gateway replica, materializes
`ufo_control.slack_connect_delivery` rows, and claims one due row under a lease (`FOR UPDATE SKIP
LOCKED`, compare-and-set on `worker_id` for every write). Approval itself never waits:
`ufo-control invite` mints the grant and mails the invitation whether or not Slack is reachable.

```text
a granted domain, or a signup that created a workspace
  |
  +-- materialize   one delivery row, channel name ext-<domain-label>-flyingobject
  +-- claim         lease one due row; an expired lease is another replica's to recover
  +-- auth.test     the token must name UFO_CONTROL_SLACK_CONNECT_TEAM_ID, else the row fails
  +-- create        the deterministic channel; name_taken resolves by exact-name lookup
  +-- reconcile     only when invite_attempted_at is set: outgoing invite, else channel sharing
  +-- inviteShared  the grant's email, external_limited=false — Post and invite, not post-only
  +-- greet         one chat.postMessage naming the browser and terminal doors
  +-- delivered     invitation id persisted, then the row settles
```

The invitation email carries the terminal installer alone, so the channel message is the only place
`/login` appears. It is the one act that accepts a duplicate: `greeted_at` is written *after* Slack
answers, so a lost response reposts rather than leaving a channel that names nowhere to sign in.

The unique channel is the idempotency boundary: a lost create response recovers by name, a lost
invitation response recovers from Slack's own state, and ambiguity Slack will not expose lands
`failed` rather than sending a blind duplicate. Transport failures, 429 (bounded `Retry-After`),
5xx, and documented transient Slack errors return the row to `pending` behind a bounded schedule;
authentication, scope, plan, policy, invalid-email, and inconsistent-channel errors are terminal.
`ufo-control slack-connect-retry <email-domain>` re-arms one failed row after its cause is fixed —
it never sends directly and never touches a delivered row.

UFO's app here (`control/slack-connect-app.yaml`) is not the customer-installed Slack app below. Its
declared scopes are asserted against the Web API methods this workflow calls, since a missing scope
fails nowhere but production. It
lives only in the operator workspace, makes outbound Web API calls only, and holds one gateway-only
bot token (`UFO_CONTROL_SLACK_CONNECT_BOT_TOKEN`, its own Secret through an explicit `secretKeyRef`
— never `ufo-platform-secrets`, `ufo-serve`, an extension, or a workspace `CredentialSlot`).
`UFO_CONTROL_SLACK_CONNECT_ENABLED` defaults to false; enabled, a missing token or team ID fails
gateway startup.

Production runtime secret containers have no Terraform-managed version. The deployment initializes
missing documents with their exact schemas, refreshes configured repository secrets, and preserves
every other production-owned value. It validates both complete documents before writing either one
and sends values to AWS Secrets Manager through stdin.

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

`/login` is the deploy's one sign-in, and every other browser door leads to it. `GET /` on the app
host redirects to the surface claiming `SurfaceSpec.home` — the portal (`/surface/web`) — which
serves its shell only to a resolved session and redirects an arrival without one to `/login`; a
session that expires under an open page leaves the shell offering the same link. The portal takes
no bearer from a member: the bearer enters through the one POST the signed-in card makes, so there
is exactly one place a bearer becomes a session.

A self-hosted node runs no gateway, so it has no sign-in page and verifies no email — the member at
the terminal is the owner `ufoctl init` seated, and their CLI token is the proof. The WorkOS
settings are read at gateway boot alone, so `ufoctl serve` starts without them. `ufoctl portal`
spends it: a loopback listener serves one page at an unguessable path, the browser posts the bearer
from that page's form to `/surface/web`, and the listener closes behind the request that took it.
Same route, same act, same cookie as the hosted card — the deploy differs, the door does not.
`ufoctl serve` names the portal and that verb at startup.

`GET /login` serves a self-contained sign-in page — a second renderer of the identical
`Onboarding` machine, never a second machine. The page names no session at all:
`POST /v1/onboard/web` mints the `__Host-ufo_onboard` cookie server-side (host-only by the
`__Host-` prefix the browser enforces, so no sibling host can plant it — `HttpOnly`, `Secure`,
`SameSite=lax`, `Path=/`, no `max_age`) whenever the presented cookie stands behind no live claim,
so a claim is only started under a session minted here and keyed by it, and every answer the page
sends rides that same-origin POST under that cookie (channel `web` — the claim index isolates
it from a terminal session with the same ref) and comes back as JSON directives
(`gateway_web.parse_directives` inverts the wire escaping exactly). It renders `say`/`ask`/`exit`
and, on `token` + `workspace`, a signed-in home card: the member's email, the workspace URL, and
the terminal install one-liner. No `install` preamble is sent on the web channel, and the token
never appears in a human-visible line.

A web session with no claim is asked for its work email, exactly as the terminal is: the address
runs `WorkEmailPolicy` and only then does Magic Auth mail the code — a denylisted address is refused
with no code sent — the code confirms, and resolution continues. `Continue with Google` is the one
act that leaves the page: it navigates top-level to `GET /v1/onboard/auth/start` with the
`?c=`/`?a=` carry, and `start` mints and binds that same `__Host-ufo_onboard` cookie and 302s to WorkOS
with `provider=GoogleOAuth`, the session and carry packed into the OAuth `state` under an HMAC
signature — a conversation id that is not a uuid and a target that is not an artifact path are
dropped before they ride it. WorkOS goes straight to Google, with no hosted page in between.
`GET /v1/onboard/auth/callback` requires the state's signature and requires the session it names to
be the one that browser holds in the cookie, exchanges the returned code for the account's email,
applies the same work-email denylist — so a personal Google account is refused — inserts the claim
already verified, and 303s to `/login` with the carry, or with `?error=` and the sentence that
refused, which the page states over a `Sign in again` link. The page resumes the machine under the
same cookie, `advance` finds a verified claim, and resolution continues as the terminal's does. A
second callback on the same session returns the claim already there, so a replayed link signs the
same member in rather than starting over.

The session id is the whole of what stands behind a verified email, so it is minted server-side —
by the web POST on the first email turn, or by `start` on the Google hop — and reaches only the
browser that signed in: no query states it, the page cannot read it, the callback honors only a
session a state this gateway signed names and the cookie holds, and no email claim is keyed by a
value the caller sends. A session another party chose therefore keys nothing — the bearer it would
otherwise hand out is the workspace credential itself. The email POST and both auth routes sit under
`/v1/onboard`, so the reserved prefixes and the nginx map already reach them, and
`WORKOS_REDIRECT_URI` names the app-host callback the rest of the flow is already on.

`WORKOS_MODE=console` runs a credential-free dev verifier, so `docker compose up` needs no WorkOS
keys — the default for the local stack. The inline email step logs its code (`000000`) through the
console verifier rather than mailing it, and `Continue with Google` 302s to
`/v1/onboard/auth/console`, a plain email form the gateway serves in place of the WorkOS hop, whose
GET reaches the same callback carrying the typed address as the code; the cookie binding, the signed
state, and every step past the identity proof are the deploy's own. Terminal sign-in in console mode
logs its code the same way. The route is mounted only under `WORKOS_MODE=console`; the default
`workos` mode requires the three `WORKOS_*` values at boot and serves no such door.

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
- `WORKOS_API_KEY` and `WORKOS_CLIENT_ID` are the gateway's alone, in their own Secret through an
  explicit `secretKeyRef` (`ufo-gateway-workos`, never `ufo-platform-secrets`, which serve mounts
  whole); `WORKOS_REDIRECT_URI` rides beside them as a plain value, and a missing one of the three
  fails startup. The redirect is whichever origin reaches the gateway: the app host's
  `https://app.<env>/v1/onboard/auth/callback` on a deploy, `http://localhost:8080` + that path
  under compose. One WorkOS environment answers one deploy, and the redirect it accepts is
  registered there. The two secret values are a standing prerequisite, not a deploy artifact:
  `ufo/<env>/gateway-workos` (`<env>` is `ufo-testing` or `prod`) is created and seeded out-of-band
  once, before the environment's first deploy, and terraform reads it through a `data` source. An
  empty placeholder would fail the gateway's startup check, so the seed precedes the rollout that
  reads it.

  ```
  aws secretsmanager create-secret --region us-east-1 --name ufo/<env>/gateway-workos \
    --secret-string '{"api-key":"<workos key>","client-id":"<workos client id>"}'
  ```
- Only a workspace admin can mint the "Add to Slack" link or derive a
  bring-your-own-app identity; the OAuth callback installs into the workspace the sealed state names.
  A teammate may call `slack_connect` to read the install status but never installs.
- A secret value is bounded (4 KiB) and travels bearer-authenticated on the existing chat
  transport; the fulfillment response is a `say` line, never a turn.

## File map

```text
infra/modules/edge/
  worker.js               public edge behavior
  worker.test.mjs         its behavior proof (node --test, ci checks job)

control/src/ufo_control/
  gateway.py              Onboarding machine and apex routes
  gateway_directives.py   the directive wire the client renders
  gateway_web.py          the /login page + JSON rendering of the same wire
  gateway_claim.py        the claim under its TTL: start, verify, admit verified
  gateway_workos.py       WorkOS custody: the Google hop and Magic Auth codes
  gateway_invite.py       one-time domain grants gating workspace creation
  gateway_slack_connect.py
                          the signup Slack Connect delivery: table, client, leased workflow
  gateway_token.py        bearer minting
  gateway_shared.py       domain/roster workspace resolution, member/default-agent writes
  gateway_store.py        claim custody
  schema.py               the ufo_control schema, shaped by the deploy's `ufo-control migrate`
  rls.py                  shared serve role and workspace policies
  client/ufo              the bootstrap installer `/ufo` serves — download the binary, exec it

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
