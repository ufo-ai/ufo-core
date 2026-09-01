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
               |  GET /join/<key> -> mark session -> form   |
               |  GET /logout -> clear session -> /login    |
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

One worker per apex fronts `ufo.ai` and `testing.ufo.ai`. Each uses its own `origin_base` for
gateway-backed responses — the client script, the fleet count, and the onboarding wire — its own
D1 waitlist database, and its own confirmation queue. Unmatched requests pass through to what that
host serves. Sign-in lives on the app host, so the apex carries no signed-in state and the session
cookie stays same-origin. The retired `flyingobject.ai` zone answers every request with a 301 to
the same subdomain and path under ufo.ai.

## Terminal onboarding flow

```text
curl -fsSL https://ufo.ai/ufo | sh
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
  +-- no candidate, no grant, join door --> mint this domain's grant, burn it, create
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

Slack leads that list because the install is what moves a customer onto their own surface. The
gateway cannot carry the link itself: an "Add to Slack" URL seals one workspace and one member for
fifteen minutes (`CREDENTIAL_REQUEST_TTL_SECONDS`), so only the turn that answers the member can mint
one that still works when they click it. Slack and billing setup are admin-only.

### Driving billing locally

Billing lives in the `metronome` extension, which `assistant_hosted` bundles and the local
`assistant` pack does not — a dev stack has no business shipping usage to a billing vendor. The
`assistant_billing` pack is the local assistant bundle plus that one extension, selected through
`UFO_DEV_PACK`, so the admin's `Set up billing` choice has something to service on a laptop:

```
export STRIPE_SECRET_KEY=sk_test_…                     # Stripe TEST mode
export STRIPE_BILLING_PORTAL_CONFIGURATION_ID=bpc_…    # portal config, subscription_update off
export METRONOME_BEARER_TOKEN=…                        # Metronome SANDBOX token
UFO_DEV_PACK=assistant_billing docker compose up
```

Sign in at `:8080`; the first member of a fresh domain owns the workspace and gets the billing
choice. The three settings are read host-side by the tool and the shippers — never in the sandbox —
and the flow creates real objects in whichever account they name, so keep it pointed at test mode
and sandbox. Without them the chain still runs to the tool and refuses there, naming each missing
setting; with them it continues into the portal link, and the usage shipper creates the Metronome
customer that resolves the workspace's ingest alias on its next tick.
`extensions/metronome/tests/integration/test_billing_providers.py` drives the same providers from
pytest under the same three settings.

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
`ufo.harness.untrusted.wall`, attributed to the form and never as instructions: the form is public, whoever
filled it proved nothing, and the employee who later signs in never typed a word of it — so the
prompt states that the member is believed over it wherever the two differ. A grant minted without
them leaves a workspace reading exactly as one `ufoctl init` seats. The flow burns the domain's
live grant and stamps the claim's `invite_id` in one transaction — no third prompt, and nothing for
the member to carry from the invitation back into the terminal. The invitation therefore holds no
secret. It opens the web portal at `#/first-run` and includes the terminal installer. A colleague at
the granted domain is identified by the same grant. An exact membership admits its workspace
without spending a domain grant. A
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

## The join door

`UFO_SIGNUP_KEY` names one path segment, and `GET /join/<key>` is a signup that waits for nobody.
The apex hops the link to the app host (edge worker), where the door compares the key in constant
time, binds a sign-in session marked with the authority to found, and 303s to `/login?join=1` under
`Referrer-Policy: no-referrer`. The ask is load-bearing: bare `/login` forwards a browser holding a
live bearer straight to its portal, which would spend the mark on nothing and leave the member
unable to found their domain without signing out first. It rides the Google hop with the rest of the
carry for the same reason an invitation's does — the callback returns to this door, and a return
that dropped it would forward the completed sign-in away unfinished. The key rides that one request: the address the member lands on
names none, and the page never reads one. A key that does not match is answered as an unrouted path
is, and so is every request where the deploy configures none, because a refusal of its own would
tell a caller the door is there.

The invite gate stays required. What the marked session changes is one branch inside it: where a
verified domain has no grant, the flow mints that domain's grant itself and the ordinary redemption
spends it. The row, the burn, the `invite_id` stamped on the claim, the Slack Connect delivery that
keys on it — all identical to a grant `ufo-control invite` wrote, so nothing downstream learns a new
shape. It carries no `business` or `goals`, which the agent prompt already reads as absent. A domain
that already holds a grant mints no second one: live or already spent, the redemption that follows
answers for it, so two racing turns and a member who opens the link twice land on one grant.

The authority is a mark inside the sealed session value the cookie carries, not beside it: `key~`,
the configured key under a signature, and an expiry. No caller can add it to a session of their own,
and the claim keyed by that session holds it through the code, the Google hop, and every retry with
no column of its own. The web POST and the Google hop each re-mint the session as they always did —
a claim is still only ever started under a session freshly minted by the gateway — and copy the
mark onto the session they mint, expiry included, so a re-mint carries the authority without
renewing it.

The mark is a bearer, because the door answers it in a `Set-Cookie` any HTTP client reads and a
member can hand it on. So it is bounded rather than trusted, and every turn grades it again. The
signed tag names the key the door was opened with, so emptying `signup_key` closes the door for the
marks already out and rotating it does the same — which is what "empty serves no door" has to mean.
The expiry bounds a captured mark to the sign-in it was minted for
(`SIGNUP_MARK_TTL_MINUTES`, thirty). Neither bound rests on the key being unguessable.

The work-email denylist is what stands between the door and a shared mailbox provider becoming one
workspace. An operator grant used to be the last check before any domain could be founded, so the
list was never load-bearing; behind the door it is. It names the consumer and disposable providers
by country as well as by brand, and a test refuses a duplicate, an uppercase entry, or a value the
address pattern could never produce. It does not solve a domain many unrelated people share
legitimately — a university, an ISP still selling mailboxes — which is a bound on who the door is
opened to, not on what the list can know.

The key is configuration rather than a secret container: `signup_key` in each environment's
`terraform.tfvars`. Nothing rests on it being unguessable, and production's is `ufo` — the soft door
that stands in for waitlist approval until the waitlist is removed, open to anyone who tries the
obvious segment. What it authorizes is bounded instead: founding a workspace for a domain WorkOS
says the member owns, and nothing else. It mails no invitation, so it is no relay; it names no
domain, so it opens nobody else's workspace; and it moves no seat in a workspace that already
stands. Everyone without the link is refused and pointed at the waitlist, exactly as before.

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

The invitation email opens `/surface/web#/first-run` and also carries the terminal installer. The
channel greeting names both browser and terminal doors. It is the one act that accepts a duplicate:
`greeted_at` is written *after* Slack answers, so a lost response reposts rather than leaving a
channel that names nowhere to sign in.

The unique channel is the idempotency boundary: a lost create response recovers by name, a lost
invitation response recovers from Slack's own state, and ambiguity Slack will not expose lands
`failed` rather than sending a blind duplicate. Transport failures, 429 (bounded `Retry-After`),
5xx, and documented transient Slack errors return the row to `pending` behind a bounded schedule;
authentication, scope, plan, policy, invalid-email, and inconsistent-channel errors are terminal.
`ufo-control slack-connect-retry <email-domain>` re-arms one failed row after its cause is fixed —
it never sends directly and never touches a delivered row.

UFO's app here (`servers/control/slack-connect-app.yaml`) is not the customer-installed Slack app below. Its
declared scopes are asserted against the Web API methods this workflow calls, since a missing scope
fails nowhere but production. It
lives only in the operator workspace, makes outbound Web API calls only, and holds one gateway-only
bot token (`UFO_CONTROL_SLACK_CONNECT_BOT_TOKEN`, its own Secret through an explicit `secretKeyRef`
— never `ufo-platform-secrets`, `ufo-serve`, an extension, or a workspace `CredentialSlot`).
`UFO_CONTROL_SLACK_CONNECT_ENABLED` defaults to false; enabled, a missing token or team ID fails
gateway startup.

## Teammate invitation email

An admin adds a teammate from the portal and that person gets one email: who added them, which
workspace, and where to sign in. Nothing else — no offer, no credit, no marketing. It carries both
bodies a message needs: plain text, and an HTML alternative in the sign-in page's own palette, card,
and button — every rule an inline attribute and every layout a table, because a mail client keeps no
`<style>` block, resolves no custom property, and lays out no flexbox. The mark is set as text: SVG
is the one image format clients reliably refuse.

`member.invited_at` and `member.invited_by` are the durable fact, stamped by `add_member` where the
teammate is minted, so an admin's action never waits on mail and a member who arrived by signing in
themselves carries no stamp and earns no message. An admin who will tell the person themselves adds
them with `notify` false, which writes no stamp and so sends nothing. Control reads it back over
`/internal/onboard/invitations` and materializes `ufo_control.invite_delivery` rows keyed on
`(workspace_id, email)` — the pair that is the person being invited, so re-reading a row already
materialized conflicts and writes nothing.

Every sweep reads every invitation, in bounded pages walked with a cursor over the read's own
ordering key, and carries no mark to the next sweep. `now()` is fixed when a transaction starts
while the seat write waits for the workspace row lock, so members commit out of stamp order: a mark
raised by the row a sweep saw would sit past the row that commits after it, and nothing else writes
this ledger, so that person would get no message and leave no failed row. The cost is a read of
every invited member per cycle. Bounding it exactly would take core knowing which invitations were
delivered, which is a control-to-core write that does not exist.

```text
member.invited_at, read whole every sweep
  |
  +-- materialize   one row per (workspace, email)
  +-- claim         lease one due row; an expired lease is another replica's to recover
  +-- cap           the workspace's day, counted under its own lock before SES is called
  +-- sent_at       the attempt marker, written before the POST
  +-- send          SESv2 from the verified sender: three facts, as text and as HTML
  +-- delivered     the row settles
```

SES answers no read, so nothing can be asked after the fact whether a message was accepted. The
attempt marker is what stands in for that: a claim that finds `sent_at` already set lands the row
`failed` rather than sending a second copy, and only a verdict proving SES never accepted the
message — a status it answered, or a connection that never opened — clears the marker and returns
the row to `pending` behind a bounded schedule. Transport failures, 429 (bounded `Retry-After`),
5xx, and documented transient SES errors reschedule; authentication, policy, verification, and
invalid-recipient errors are terminal.

Each workspace sends at most 100 invitations a day, counted in control's own ledger under that
workspace's advisory lock before SES is called, so two replicas cannot each read ninety-nine. A row
over the line lands `failed` naming the cap rather than disappearing.
`ufo-control invite-delivery-retry <workspace-id> <email>` re-arms one failed row after its cause is
fixed — it never sends directly and never touches a delivered row, and it clears the attempt marker,
because an operator re-arms having decided the message never landed.

Runtime secret containers have no Terraform-managed version, in either environment. Every value in
them is written out-of-band, so a version Terraform declares can carry placeholders only — and
Secrets Manager drops that version once later writes leave it unlabelled, which turns the next
apply's refresh into a create that publishes the placeholders over every live credential
([outage 0002](outages/0002-terraform-blanked-testing-runtime-secrets.md)).

The production deployment initializes missing documents with their exact schemas, refreshes
configured repository secrets, and preserves every other production-owned value. It validates both
complete documents before writing either one and sends values to AWS Secrets Manager through stdin.

The testing deployment writes only the properties it holds a repository secret for and preserves the
rest, so `ufo/ufo-testing/api-keys` and every property no deploy writes are a standing prerequisite,
seeded out-of-band like `ufo/<env>/gateway-workos`. A required property with no value stops the
deploy at `Write testing runtime secrets`, naming the property, before the apply rolls the pods that
would read it empty.

The flag backend's three keys (`cloudflare-flagship-app-id`, `cloudflare-account-id`,
`cloudflare-flagship-token`) are the one family neither deploy requires: both write them empty when
the document lacks them, because the cluster projects each one by name and a property Secrets
Manager does not hold leaves the ExternalSecret unready. Seed all three to read flags; leave them
and serve builds no flag provider, so every flag resolves to the default its call site passes: a
flag over one of the portal's own screens draws that screen, because it withholds something already
shipped, and a flag over a shipped app lists no app, because turning one on is what offers it.

Those three read flags and cannot write one. Which flags exist is code: `infra/envs/edge/flags.tf`
declares every key for both environments, applied by each deploy on a token scoped to that
environment's Flagship app, and a gate holds that list to the set the extensions read.

What a flag serves is not code — terraform creates it and then ignores the field — so a feature
reaches a member without a deploy:

```bash
kubectl -n ufo-system exec deployment/ufo-serve -- ufoctl flags set enable-wiki-app --on
```

That verb writes through `cloudflare-flagship-write-token`, a fourth property scoped to the one
Flagship app and projected into the fleet's own pods, because the operator surface here is a verb
run in one of them. The vendor's dashboard does the same thing and neither is reverted by an apply.
Seeding that property is the one act nothing here can do for itself: Cloudflare mints an API token
only from its dashboard.

## Web login

The whole browser sign-in flow is same-origin on the **app host** (`app.<apex>`), the sole
authenticated host. The apex 302s both browser doors there (edge worker): `GET /login` and
`GET /logout`, query and all — an apex door is only a hop to the host that binds the cookie, and the
ask a link carries is read there. On the app host the ingress routes the front-door prefixes
(`ufo.serve.RESERVED_HOST_PREFIXES`: `/login`, `/logout`, `/v1/onboard`, `/ufo`) to `ufo-gateway`,
while `/` and `/surface/*` stay on `ufo-serve` (nginx longest-prefix). Two invariants hold this up
by construction rather than by convention: the serve fleet **fails its boot** if it mounts any route
under a reserved prefix (`_assert_no_reserved_routes`), and every session cookie is set through
`ufo.sdk.http.set_session_cookie`, which takes no `Domain` — so a cookie is always host-only and a
session can never cross a subdomain, environment, or preview host (a repo gate forbids raw
`set_cookie` elsewhere).

`/login` is the deploy's one sign-in, and every other browser door leads to it. A browser that
already holds a live `ufo_session` is forwarded from `/login` to the portal — the address the page's
own handoff would have posted it to, so `?c=` and `?first=1` still land where they name — rather
than asked again for an address it has proved. Four asks are the page's alone (`FORM_ONLY_ASKS`), and
the form is drawn for them however live the session is: each reaches this door from a caller a forward
would send straight back to, or carries a sentence the page alone states. `?debug=1` comes from
`ufo.runtime.ext.operator.OPERATOR_LOGIN_PATH`, and the operator surfaces read `ufo_debug`, which only the
page's POST binds. `?a=` comes from `ufo.runtime.surfaces.artifacts._refusal`, which refused this very
session the artifact, so it would refuse the forward too. `?invite=1` is what the invitation mail
links to, and that mail names an address this session may not prove, whose seat is claimed in the
walk the page runs. `?error=` comes from the auth callback, and the sentence it carries is one the
page alone states, so a forward would drop a refusal the member is owed.

An ask rides the Google hop with the rest of the carry (`AuthCarry`), so a member who reaches the
form under one and takes `Continue with Google` returns to the door still holding it — the page runs
again and finishes the walk, rather than being forwarded away with a claim verified and unresolved.

`GET /logout` is the door back out: it expires every cookie this host binds (`ufo_session`,
`ufo_debug`, and `__Host-ufo_onboard`) and sends the browser to `/login`, which draws the form for a
browser holding none of them, so a stale tab, a second click, and a sign-in as somebody else all land
on the form rather than wait out an expiry. The portal offers it wherever it states who is signed in
— the account menu and the sidebar's foot — and it is where a refusal that a second address would
answer leads: the portal's `no-member` and `no-seat` screens and a private site's "not signed in" page
all name it, since the bearer behind each is live.
`GET /` on the app host redirects to the surface claiming `SurfaceSpec.home` — the portal
(`/surface/web`) — which serves its shell only to a resolved session and redirects an arrival
without one to `/login`; a session that expires under an open page leaves the shell offering the
same link. The portal takes no bearer from a member: the bearer enters through the one automatic
POST sign-in makes, so there is exactly one place a bearer becomes a session.

A self-hosted node runs no gateway, so it has no sign-in page and verifies no email — the member at
the terminal is the owner `ufoctl init` seated, and their CLI token is the proof. The WorkOS
settings are read at gateway boot alone, so `ufoctl serve` starts without them. `ufoctl portal`
spends it: a loopback listener serves one page at an unguessable path, the browser posts the bearer
from that page's form to `/surface/web`, and the listener closes behind the request that took it.
Same route, same act, same cookie as hosted sign-in — the deploy differs, the door does not.
`ufoctl serve` names the portal and that verb at startup.

`GET /login` serves a self-contained sign-in page to a browser holding no session — a second
renderer of the identical `Onboarding` machine, never a second machine. The page names no session
at all: `POST /v1/onboard/web` mints the `__Host-ufo_onboard` cookie server-side (host-only by the
`__Host-` prefix the browser enforces, so no sibling host can plant it — `HttpOnly`, `Secure`,
`SameSite=lax`, `Path=/`, no `max_age`) whenever the presented cookie stands behind no live claim,
so a claim is only started under a session minted here and keyed by it, and every answer the page
sends rides that same-origin POST under that cookie (channel `web` — the claim index isolates
it from a terminal session with the same ref) and comes back as JSON directives
(`gateway_web.parse_directives` inverts the wire escaping exactly). It renders `say`/`ask`/`exit`
and, on `token` + `workspace`, posts the bearer to `/surface/web` and opens the portal. A founding
sign-in or an invitation opened at `#/first-run` carries `?first=1` through that POST; the portal
replaces it with its own `#/first-run` address. No `install` preamble is sent on the web channel,
and the token never appears in a human-visible line.

A web session with no claim is asked for its work email, exactly as the terminal is: the address
runs `WorkEmailPolicy` and only then does Magic Auth mail the code — a denylisted address is refused
with no code sent — the code confirms, and resolution continues. `Continue with Google` is the one
act that leaves the page: it navigates top-level to `GET /v1/onboard/auth/start` with the
`?c=`/`?a=`/`?first=1` carry, and `start` mints and binds that same `__Host-ufo_onboard` cookie and
302s to WorkOS with `provider=GoogleOAuth`, the session and carry packed into the OAuth `state` under
an HMAC signature — a conversation id that is not a uuid and a target that is not an artifact path
are dropped before they ride it. WorkOS goes straight to Google, with no hosted page in between.
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
one extra machine-consumed directive — `debugger <workspace-url>/surface/debug`. It states a
capability, not a destination: the automatic form POSTs the token to the member portal for every
member, and to that target only when the page was asked for the debug surface by name, with
`/login?debug=1`. The ask reaches the page the way `?c=` and `?a=` do — an operator surface bounces
a bearer-less page GET to `/login?debug=1`, and the signed Google state carries the ask back — so
the click that wanted the debugger returns to it and every other sign-in opens the portal. The ask is
the page's own even for a browser already holding a session: the debug surface authenticates by
`ufo_debug`, which only this POST binds, so a forward would bounce back here unbound. The debug
surface exchanges the posted token for its `ufo_debug` cookie and redirects. The bearer never rides
a URL into the debugger, so no access log captures it — `spec.md` "Surfaces" covers that surface.
The directive is emitted channel-blind, and the terminal client drops unknown verbs. The gate is the
server's; the page uses the target it gets.

## Connecting Slack

This is the app a *customer* installs in their own Slack workspace — nothing to do with the signup
inviter above, which represents UFO in UFO's workspace and shares none of its credentials.
The `assistant_hosted` shared fleet activates the `slack` extension alongside the ufo chat surface.
There are two install paths — both land the same per-workspace bot token and identity record, and
the agent drives either in chat with the `slack_connect` action on `surface/slack` (default
`method="oauth"`).

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
request_credentials (core action on the credential collection, dispatched through object_action)
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
the deploy Slack app's env secrets, and its `slack_connect` / `slack_app_manifest` actions on
`surface/slack` plus the `slack-app-setup` skill; connecting it is a conversation, not a separate setup surface.

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

servers/control/src/
  main.rs                 the seven CLI verbs: gateway, migrate, invite, slack-connect-retry,
                          invite-delivery-retry, rls-bootstrap, serve-dsn
  gateway.rs              the HTTP routes and the onboarding state machine
  claim.rs                claim time-to-live, verification, and the races each write loses
  workos.rs               Magic Auth, the Google hop, and the signed state and cookie seals
  web.rs                  the browser's renderer; login.html is the page it serves
  shared.rs               the onboarding RPC client — every core table is read and written there
  invite.rs               one-time domain-grant custody
  store.rs                the platform onboarding ledger
  slack_connect.rs        the signup Slack Connect channel and its delivery poller
  invite_delivery.rs      the teammate invitation email and its delivery poller
  email.rs                work-email policy and SES delivery
  schema.rs               the ufo_control schema, shaped by the deploy's `ufo-control migrate`
  rls.rs                  shared database role and policy bootstrap
  db.rs                   the pool over control's own ledgers, and nothing else
  token.rs                bearer minting, and the claim read back for the sign-in door
  directives.rs           the directive wire both renderers read
  client/ufo              the POSIX installer the gateway serves at /ufo
```
