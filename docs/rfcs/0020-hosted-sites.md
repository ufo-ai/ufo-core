---
rfc: 0020
title: "Hosted sites — sandbox ingress and the access-controlled frame"
status: implemented
date: 2026-07-28
---

# Hosted sites — sandbox ingress and the access-controlled frame

> A site the agent builds gets a stable, externally reachable link that opens an access-controlled
> frame: the creator sees a visibility selector, an authorized viewer sees the embedded site, and
> the bytes are served out of the conversation's sandbox through a dedicated ingress process so
> site traffic never rides the serve event loop. Default visibility derives from the conversation
> audience that produced the site. Issue #276.

## Current state

- `deploy_website` / `publish_website` / `start_server` (`extensions/sites/ufo_ext_sites/tools.py`)
  serve `http://localhost:<port>` inside the sandbox — reachable by the browser tools for
  validation, by nobody else. Files leave only through `share_file`.
- The one externally reachable delivery path is the artifact route
  (`core/src/ufo/runtime/surfaces/artifacts.py:25`): token-gated single-file download, TTL 1h. No
  multi-request browsing, no viewer identity, no visibility.
- Creation context is already modeled: `conversation.audience`
  (`core/src/ufo/runtime/turns/audience.py`) is `member:<id>` (DM/CLI/web), `room:<surface>:<room>` (channel),
  `foreign:<surface>:<room>` (Slack Connect), or `shared`.
- Viewer identity is already modeled: the HMAC bearer `{ws, email, exp}` (`core/src/ufo/runtime/auth/bearer.py`),
  minted by the gateway after an emailed code, carried by the web surface's `ufo_session` cookie;
  `SurfaceContext.linked_member(email)` resolves it to a member.
- The sandbox is reachable from the host: `Carrier.dial(handle, port)`
  (`core/src/ufo/harness/sandbox/session.py:223`) maps an in-sandbox port to an externally dialable
  `DialTarget`, reading whatever the wire requires (e2b's traffic-access header) off the live
  container rather than the handle, so a handle rebuilt from the durable row reaches a port exactly
  as the process that created it does. The conversation row persists
  `sandbox_handle` (`<backend>:<id>`), and the e2b carrier resumes a paused sandbox from it —
  `E2B_LIFECYCLE = {"on_timeout": "pause", "auto_resume": True}`, and a `nohup` webserver survives a
  pause and answers again on the next dial — measured end to end, §Resume-on-dial below.
- Process topology precedent: the egress proxy is its own composition root
  (`core/src/ufo/proxy_serve.py`, `ufoctl proxy`) and its own Deployment
  (`infra/templates/hosted.yaml.tpl`), booted from config + manifests + owner DSN.

The workspace's S3 sync is being dropped, so blob reads are not a serving path: the sandbox
filesystem is the only copy of a built site. Serving means dialing the sandbox.

## Proposal

### Shape

Two origins, two processes, one wire format:

| piece | process | origin | traffic |
|---|---|---|---|
| frame page (header + selector + `<iframe>`) | main serve, `/surface/sites/{site_token}` | app host | one authenticated GET per visit |
| site bytes (every asset / app request) | **sandbox ingress** (`ufoctl ingress`), new | the site's own subdomain of `[sandbox] ingress_public_url` (`*.<domain>`, wildcard cert) | all of it |

Each site lives at its own origin: a stable DNS label that is a signed *address* for
`(conversation, port)` — base32 of the conversation id, the port, and a truncated HMAC (22 bytes →
36 chars, inside DNS's 63, case-insensitive by construction). The workspace is not in the label; it
rides the signed claims the viewer's session carries, and filters the conversation read. Per-origin
sites are what make a generated site behave like a website: `/`-rooted assets and redirects (Vite
and Next's default output) resolve, and cookies and `localStorage` persist per site and cannot cross
sites.

The frame authenticates the viewer and mints a short-TTL **view token**; the iframe's `src` opens
the site's subdomain with that token in the path once (`/~t/{view_token}`); the ingress verifies
it, binds a host-only session cookie for that origin, and redirects to `/` — every later request
authorizes by the cookie. Per request the ingress resolves the label's conversation and
`sandbox_handle`, dials the sandbox port through the carrier, and streams the response back. High
traffic lands on the ingress Deployment; the serve loop never sees it.

Both static and live sites serve the same way: `deploy_website` already leaves a webserver running
on a known port; `publish_website` leaves the app's own server. Hosting a site is *registering that
port*, not moving bytes.

### Sandbox ingress (core, new)

The inbound twin of the egress proxy, and core for the same reasons: it needs the carrier, the
token secret, and the core `conversation.sandbox_handle` column — and it knows nothing about
sites. It is a generic token-gated reverse proxy to a conversation's sandbox port; the sites pack
is its first minter.

- `core/src/ufo/harness/sandbox/ingress_token.py`: `mint_ingress_token(claims, kind)` /
  `verify_ingress_token(token, now, kind)` over `token_signing`, claims
  `{kind, ws, conversation_id, port, exp}`. The deploy secret is resolved inside core exactly as
  `ufo.runtime.auth.bearer` resolves it, so neither end takes it as a parameter and no caller holds it. The two
  hops of a visit are two kinds — `view` for the link the frame opens, `session` for the cookie the
  ingress binds — named at the mint and at the verify, so neither passes where the other is
  expected: a cookie replayed at the view path mints no successor, and a view token pasted into a
  cookie jar opens nothing. A view token is usable until it expires and may open several sessions in
  that window (a reload is a second one); each session then runs its own TTL from the moment it was
  minted, and no request extends it.
- `Carrier.dial(handle, port) -> DialTarget(host, tls, headers)` — the externally dialable
  authority, whether that authority terminates TLS (so the caller picks its scheme rather than
  guessing), and whatever the backend requires on the wire (e2b: the traffic-token header, as
  `sandbox_chrome` already sends; docker and local: bare `host:port`, no TLS). All three carriers
  implement it in the same change.
- `core/src/ufo/harness/sandbox/ingress_host.py`: `site_label(conversation_id, port)` /
  `parse_site_label(label)` — the DNS label codec, base32 over the conversation, the port, and a
  truncated HMAC of the same deploy secret. It refuses to import if the signed bytes would outgrow
  DNS's 63-character label bound, and parses only the one canonical spelling of a label: 22 bytes
  fill 176 of the 180 bits 36 base32 characters carry, so without that check sixteen labels decode
  alike and a browser reads all sixteen as separate origins with their own cookies and storage. The
  HMAC is 4 bytes because forging one wins nothing by itself: both ways in carry the
  `(conversation, port)` they claim and must match the label, so a guessed hostname is 403 before a
  row is read or a sandbox dialed. The tag keeps enumeration off the conversation read; the cookie is
  the gate, and 19 characters fewer is what a member reads and copies.
- `ufoctl ingress`: composition root mirroring `proxy_serve.py` — config, manifests, carrier, owner
  DSN, the token secret, and `[sandbox] ingress_public_url` resolved at boot, so a deploy missing
  any of them dies before its readiness probe reports green. `GET /~t/{view_token}` verifies the
  frame's token, requires the claimed `(conversation, port)` to be the very site the Host addresses,
  and binds them as a host-only session cookie (a signed token of its own, session TTL, `lax` —
  since arrival is a navigation in from the app host: a different origin, one registrable domain, so
  the cookie is same-site and rides both the arrival and the embedded site's own requests). The view
  path and everything under it is claimed on every method, answering 405 to all but GET: a GET-only
  route matches the path but not the method, and Starlette prefers a later route matching both, so
  the catch-all took a `POST /~t/{token}` and forwarded the token to the sandbox as its request
  path. Claimed on the segment boundary rather than as a three-character prefix, so a site asset
  named `/~theme.css` is served rather than read as a malformed token. The view path is claimed a
  second time for WebSocket, because a handshake matches no HTTP route: without it `/~t/{token}`
  over WebSocket reached the catch-all and was forwarded to the sandbox as a request path, which is
  the same leak on the other protocol. Every other
  request — every method an app serves (GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS), and a
  WebSocket handshake on any path — reads its site off the Host, authorizes from that cookie, reads
  `conversation.sandbox_handle` for the cookie's workspace, dials, and streams or relays. Both
  protocols pass one gate (`_dial_site`) and refuse with one `SiteRefusal`, the socket denying its
  handshake with the response the proxy would have sent, so neither can admit what the other turns
  away. The socket adds one check the proxy has no use for: the handshake's `Origin` must be the site
  it addresses. A handshake is exempt from CORS, and every site is a label under one `base_host`, so
  the browser counts two sites same-site and attaches the addressed site's host-only `ufo_site` cookie
  to a socket opened from any other label — which without the check hands site B a bidirectional
  channel into site A's own server for the hour A's session lasts, strictly more than HTTP ever gave
  it, where CORS still withheld the response body. A missing `Origin` is refused with a mismatched
  one, since the browser this constrains always sends one. The handle read is one owner-DSN query per
  request and
  stays uncached: the origin's own cost (a fresh TLS handshake into the sandbox) dominates it by
  orders of magnitude, and a cache would have to be invalidated by every sandbox reap. No cookie, or
  a cookie or token naming another site, 403; a Host under no signed label 404; no handle or wrong
  backend 503, saying the site is no longer hosted and to ask the agent that built it to put it back
  up — the sandbox is what vanished, but a reap the member never saw is not a distinction they can
  act on.

  Cookies are confined in both directions, because a site is agent-authored code. Upstream: the
  `ufo_site` crumb is cut out of every `cookie` line, since a site could otherwise read an hour of
  access to itself out of its own request log, and the upstream client keeps no jar, since one
  process dials every workspace's sandbox and a jar would replay one site's cookies to the next site
  answering on that authority. Downstream: every relayed `Set-Cookie` loses its `Domain` attribute
  and any cookie named under the `ufo_` prefix is dropped whole. Without that, a site answering
  `Set-Cookie: ufo_session=…; Domain=<parent>` plants a session on the app host — the parent is no
  public suffix, so the browser accepts it — which is session fixation reached from inside a member's
  own site, and which the host-only guarantee `ufo.sdk.http.set_session_cookie` states covers only
  cookies ufo sets, not ones it relays. Stripping the attribute rather than the cookie leaves the
  site its own cookies, host-only to its own label, which is what a per-site origin is for.
- Infra: one new Deployment + Service in `hosted.yaml.tpl`; the env overlay routes the wildcard
  sites hostname (`*.<domain>` DNS + cert) to it.

`SurfaceContext` gains `ingress_url(conversation_id, port)` so the sites surface mints without
holding the secret — the `artifact_link` pattern. The TTL is `ingress_token`'s own constant, not an
argument: one deploy has one answer for how long a link may sit in a frame.

### Surface tokens (core, new)

The permanent site link needs the workspace resolvable from the URL alone (a public viewer has no
cookie; `SurfaceSpec.identify` must return the workspace before any DB binding).
`core/src/ufo/runtime/auth/surface_token.py` + `ufo.sdk.surface_token`: `mint_surface_token(surface, payload)` /
`verify_surface_token(surface, token)`, HMAC over `UFO_TOKEN_SECRET` resolved inside core exactly
as `ufo.runtime.auth.bearer` does, namespaced by surface so one surface's token never replays at another. The
site link is `{public_base_url}/surface/sites/{site_token}` with payload `{ws, conversation, name}` —
the site's own identity, so the link is derivable wherever a site is registered and a re-deploy
reproduces it exactly. It is an address, not an authorization: it never expires, and every request
still passes the visibility gate.

### The sites pack (everything else)

**`hosted_site` table** (extension-owned, own migration, like `user_skill`):
`(workspace_id, conversation_id, name)` identity, plus `port`, `visibility`,
`creator_member_id`, timestamps. Re-deploying upserts the row and bumps `updated_at`. The
member-supplied name is slugged to the object-name grammar on the way in, bounded so the `site`
object name that carries it stays addressable.

**Registration**: `deploy_website` and `publish_website`, after their readiness probe, mint the
link, upsert the row, and return `site_url` beside the sandbox-local `url`. `start_server` registers nothing; a scratch server is not a deliverable.
Creator is `ctx.acting_member_id` (raise without one — a site needs an owner), and the site is
registered against the conversation whose sandbox is serving the port — for a subagent, the one it
inherited at admission, which is the member's however deep the spawn chain runs.

A port serves one origin, so registering retires any other site row on the same
`(conversation, port)` — otherwise an older name would silently serve the newer deploy's content.
Retiring is unhosting, so it carries the unhost rule rather than riding in behind a deploy: the
port's current site must be the acting member's own, and the turn must be one that may unhost —
a member speaking. A subagent carries no speaker, so it re-deploys under the site's own name rather
than taking a different one's port; that refusal costs it nothing, because every refusal hosting can
raise needs only the row and so fires before the port is taken. Default visibility from
`ctx.audience`:

| audience | default |
|---|---|
| `member:<id>` | `private` |
| `room:<surface>:<room>` | `workspace` |
| `shared` | `workspace` |
| `foreign:<surface>:<room>` | `private` |

`foreign` is doctrine, not taste: a sealed external room must never default a site into the whole
company. An explicit `visibility` argument (the chat directive, e.g. "make it public") overrides the
default — for the site's creator, on a turn with a live speaker. The column has one authorization
rule, a re-deploy is not a second door to it, and choosing who can open a site is a granting act, so
a scheduled turn acting on the creator's behalf may re-deploy but never re-gate. Without an explicit
argument a site that already exists keeps the visibility it has, so a teammate re-deploying never
re-opens or re-closes what its creator set.

**Visibility levels**: `private` (creator member only) · `workspace` (any authenticated member of
the workspace) · `public` (anyone with the link).

**Frame** (`/surface/sites/{site_token}`, main serve): verifies the token, authenticates the
viewer from the `ufo_session` cookie (public sites skip it), gates on visibility, then renders
header + iframe with a fresh view token. Creator sees a live selector; another authorized member
sees a read-only badge naming the level ("Visible to workspace members"); no cookie on a non-public
site gets a page saying so and linking `/login`, which the shared host routes to the onboarding
gateway — same origin as the frame, and the walk it starts ends on a card whose one button posts the
member's bearer into the portal, binding `ufo_session`. Signing in is the whole recovery: a member
needs nothing widened to see a site their workspace already may. The portal is not the link, since
reached cold it can only ask for a token the viewer does not have. Everything else is a uniform 404 — no existence oracle, and an unverifiable token answers with that
same 404 from `identify` rather than a 401. The iframe's address is minted per render and never
stored — the view token is a bearer credential until its short TTL passes, traded on first load for
the origin's own session cookie — and a deploy with no ingress configured renders the frame saying so
rather than framing a dead origin. Because the frame page (app origin) and the site (ingress origin)
are cross-origin, the embedded site's scripts cannot reach the selector, the session cookie, or any
app endpoint, and the iframe's `sandbox` withholds `allow-top-navigation` so a site cannot navigate
the member's tab away; the visibility `POST` still carries a CSRF token bound to the session cookie
(a surface token signed over the cookie's digest — an attacker holds neither the HttpOnly cookie nor
the deploy secret), and `referrerpolicy=no-referrer` keeps the frame's address out of the embedded
site's requests.

**Visibility changes — both ends of one column**: the in-frame selector `POST`s to the sites
surface (creator + CSRF gated), and a `site` object kind gives chat the same act —
`object_apply` flips `visibility`, `object_delete` unregisters the site; create and any other spec
change are refused naming `deploy_website`, the `artifact` kind's pattern. The kind is a
`MemberOwnedObjects` over the same column: the creator owns the row, a non-private site is shared,
so a private site is invisible to every other member and only its creator may re-gate it — on a turn
with a live speaker, since disclosure is a granting act. A workspace admin may narrow a shared site
to private but never widen one, the `connector_grant` rule. Object names are
`<site-name>-<conversation-digest>` (carried in the deploy result), because two conversations may
each host a `dashboard`.

**Prompt section**: `sites_section.md` gains the hosted link — the deliverable becomes the
`site_url`, with `share_file` for a downloadable copy.

## Units

| unit | ships | proof |
|---|---|---|
| U1 sandbox ingress | `ingress_token`, `Carrier.dial` (e2b/docker/local), `ufoctl ingress`, infra Deployment, e2b `allow_public_traffic=False` | token → bytes stream from a live sandbox port; tampered/expired → 403; cleared handle → 503 |
| U2 sites pack | `hosted_site` + migration, tool registration + audience defaults, `surface_token` seam, frame + selector + the not-signed-in page, visibility POST, `site` object kind, prompt section, `ingress_public_url` knob, `SurfaceContext.ingress_url`, ingress host-label addressing + per-site session cookie | site built in a DM: creator 200, other member 404; flip to `workspace`: other member 200; foreign room defaults private; two sites never share an origin |
| U3 socket relay | WebSocket routes on the catch-all and the view path, `SiteRefusal`/`DialedSite` as the one gate both protocols pass, subprotocol negotiation, frame-type and close-code relay | a real client through a real ASGI server to a real WebSocket origin: `vite-hmr` selected, text and binary each relayed as themselves, `ufo_site` and the viewer's handshake fields withheld, every refusal the proxy's own status and line, `/~t/{token}` never reaching the site, a socket opened by another label refused |

U1 has no sites knowledge and U2 consumes it; U3 adds a protocol to U1 and needs no sites knowledge
either. A further unit — a `?next=` return-to on `/login`, so a coworker bounced to sign in lands
back on the site — was built and withdrawn (PR #839): the gateway
sets no browser credential, so the redirect arrived anonymous, took a 401, and destroyed the token
the page was holding, restarting the email-code walk it was meant to shorten. A working return-to
has to end on a workspace-host URL that binds the cookie before forwarding, which spans the gateway,
the web surface, and the edge worker — its own unit, not this RFC's. Until then an unauthenticated
viewer of a non-public site is told that this browser is not signed in to the hosting workspace and
sent to `/login`; after that walk the browser opens the portal and holds a session. Opening the
permanent link again succeeds.

## Dependencies and named risks

- **A site is registered against the conversation whose sandbox serves it, which is what lets a
  subagent host one.** A subagent runs in the sandbox of the turn that spawned it, up the chain to
  the member's, so the port it brings up is answered by the member's own sandbox and the site is
  registered there — the link outlives the child turn and a rebuild lands on the same link, because
  each spawn is a new conversation but not a new sandbox. Registering against the child instead would
  resolve a conversation with no handle on it and answer that the site is gone. The website-building
  subagent therefore holds the hosting tools: it builds, brings the site up, validates it against a
  real browser, and deploys, all in the conversation the member is in. It cannot replace a site
  already serving that conversation's port under a different name: retiring a live link is an
  unhost, and the child carries no speaker. Gating on the chain's root would admit the delegate of
  a speaking member and still exclude a scheduled fire, but nothing in the extension seam sees the
  root, so the rule is the turn's own speaker and the child hands a rename back.
- **The idle reaper (being moved to the docker extension in a separate change).** Today
  `SandboxReaper` (`core/src/ufo/runtime/jobs.py:280`) destroys idle sandboxes on *any* backend at 30
  minutes and clears `sandbox_handle` — which kills a hosted site. Until that change lands, a
  hosted site dies at first idle reap; after it, e2b pauses instead and the site's continuity rests
  on resume-on-dial, which is measured (below). Related latent bug for that thread:
  `E2BCarrier.create`
  (`extensions/e2b/ufo_ext_e2b.py:216`) has no `SandboxNotFoundException` fallback, so a resume id
  whose pause aged out of e2b retention wedges the conversation permanently.
- **Serving capacity is the sandbox's.** One uvicorn/`http.server` in one sandbox; the ingress
  isolates the serve loop but does not scale the origin. The follow-up issue: promote static
  sites to object-store serving behind the same tokens.
- **Resume-on-dial is measured end to end.** The hosted-sites story rests on it: a site idles, e2b
  pauses the sandbox, a viewer returns, and the dial brings it back with the member doing nothing.
  Proven against the live service on both pause routes, each time asserting the *same* sha256 the
  site served before the pause. Explicit pause, then a **cold replica** — a second `ufoctl ingress`
  with an empty lease map, which is what an ingress replica that never created the sandbox actually
  is, and which therefore has no path to a `DialTarget` except `sdk.connect` returning: first hit
  0.413s, against 0.545s for the same cold dial to an already-running sandbox, so the resume cost
  sits inside the noise of one TLS handshake. Then the route a real idle site takes — the provider's
  own timeout pause, confirmed `state: paused` on the control plane rather than inferred — dialed 23s
  later by a fresh replica: first hit 0.610s, with that process's own log showing the
  `POST /sandboxes/<id>/connect` it made. Two facts the run turned up that were not previously
  recorded: the e2b **edge** also auto-resumes a paused sandbox on inbound per-port traffic (0.369s,
  restoring the sandbox's configured 900s span, not the 300s default #826 measured for data-plane
  auto-resume), so the ingress has two independent routes to a resumed container and
  `endAt - startedAt == 900` alone does not attribute a wake to our `connect`; and
  `allow_public_traffic: False` is enforced live — the same per-port URL without the
  `e2b-traffic-access-token` header answers `403 "Sandbox is secured with traffic …"`, so the
  ingress, which alone holds that token, is the only way in. The ingress does not pre-warm.
- **An app that redirects to its own absolute `{port}-{id}.e2b.app` address 401s**, since public
  port traffic is off. A `/`-rooted asset or `Location` is the site's own root and resolves.
- **WebSockets are relayed, through the same gate as the proxy.** A dev server's live reload (the
  webapp template's Vite HMR, on `/vite-hmr` of the site's own server) connects, and so does an app
  whose protocol is not request/response. The handshake authorizes off the same Host, the same
  `ufo_site` cookie, and the same dial, and it is denied with the very response the proxy would have
  sent — one gate, one status, one sentence, so a socket cannot become the weaker of two doors into
  the same bytes. Nothing is accepted until the site's own server has agreed, the subprotocol the
  viewer is told is the one the site chose, and the site's own close code is what reaches the viewer.
  A frame is bounded at `WEBSOCKET_MAX_MESSAGE_BYTES` in either direction by two separate
  mechanisms — the client's `max_size` upstream, the ASGI server's `ws_max_size` for the viewer's
  half — and permessage-deflate is off on both, since the bound is only as good as the failure it
  produces. Measured on the upstream half: with deflate negotiated a message one byte over the bound
  arrives clipped to exactly the bound, so a site's own protocol frame would be silently short — JSON
  cut mid-object instead of a socket that ended. Uncompressed it raises at every size tried. The
  viewer's half refuses an over-bound frame either way, so deflate is off there for uniformity rather
  than against a measured clipping.
  The count of sockets is not bounded, and deliberately so here: a socket
  is dialed by the WebSocket client rather than the pooled `httpx` one, so it is outside
  `UPSTREAM_MAX_CONNECTIONS`, and each viewer's socket holds one upstream connection open for as long
  as it lasts. Capping them needs an answer for the viewer who is turned away, which is the same
  question as the per-workspace fairness named below and belongs with it.
- **A site sits one label under the apex, and both halves of that are measured.** Two deploy facts
  fix the address. The NLB admits only Cloudflare's ranges (`loadBalancerSourceRanges`), so a site
  published DNS-only resolves and then drops every connection — measured on
  `probe.sites.testing.flyingobject.ai` at `52.2.153.27`, timing out on 80 and 443 alike while the
  proxied apex served 200. And a wildcard TLS SAN matches exactly one label, so the zone's edge
  certificate — measured as `*.flyingobject.ai`, `*.testing.flyingobject.ai`, `flyingobject.ai` —
  covers `<label>.<apex>` and covers nothing under a `sites.` prefix; proxied at that depth the
  handshake is refused outright (`SSLV3_ALERT_HANDSHAKE_FAILURE`). A site host one label under the
  apex satisfies both at once, with the certificate the zone already has: no ACM purchase, no
  Cloudflare token scope beyond the DNS edit external-dns already holds. What it costs is the apex's
  unclaimed namespace — the wildcard rule is the last match for `*.<apex>`, so a name nobody has
  claimed with an exact rule reaches the ingress and is refused for naming no site. `sites.<apex>`
  is not a host anymore.
- **A site is a sibling subdomain of the app host, so a site's scripts can still plant a cookie on
  it. Scrubbing covers the header path only.** `<label>.<domain>` shares a registrable domain with
  `app.<domain>`, and that shared parent is what the browser computes `Domain=`-scoped cookies and
  SameSite on, so it does not separate a site from the app for us — nor would a `sites.` prefix have,
  since the shared parent is the same either way. The ingress scrubs what it
  relays — `Domain` stripped from every `Set-Cookie`, every cookie named under `ufo_` dropped, a
  nameless cookie dropped with them — and that closes the response header as a write path
  completely. It does not touch the other one: a site is agent-authored code *with scripts*, and
  `document.cookie = "ufo_session=…; domain=<parent>"` from the site's own origin is accepted by the
  browser and never passes through the ingress at all. The app host reads `ufo_session` at face
  value, so under `public` visibility — where a site's author and its viewer can be in different
  workspaces — that is cross-tenant session fixation. Nothing in the tree stops the script: the frame
  does sandbox the iframe, but the flags a site is promised — `allow-scripts` with
  `allow-same-origin` — are exactly what leave `document.cookie` writable from its own origin, and
  there is no CSP. The two candidate closes are a separate registrable domain for the sites
  base (the browser then treats a site as a different site and `domain=` cannot reach the app host)
  and refusing a request that carries more than one `ufo_session`; neither is decided, and the
  scrubbing above is not a substitute for whichever lands. `ufo.sdk.http.set_session_cookie`
  guarantees host-only for the cookies ufo *sets* — no cookie a site writes passes through it.
- **Rotating `UFO_TOKEN_SECRET` renames every site.** The label is signed with it, so a rotation
  moves every hosted site to a new origin at once: bookmarks 404, and each site's cookies and
  `localStorage` are orphaned at the old hostname with no way to reach them. Rotation is also the
  only way to revoke every outstanding session at once, so the two properties trade against each
  other; a per-site or per-workspace key that a rotation could turn independently is the way out if
  a deploy ever needs one.
- **Sandboxes created before `allow_public_traffic=False` keep their ports open** to the internet
  for the rest of their life, since the flag is set at create. Closing them needs a one-time
  operator sweep or an `update_network({"allow_public_traffic": False})` on the resume path — ufo
  sets no egress rules, so that call's "omitted fields are cleared" caveat is a no-op here.
- **No per-workspace fairness on the shared upstream pool, and sockets are outside it.** The HTTP
  client is bounded (`UPSTREAM_MAX_CONNECTIONS`) but the bound is process-wide: enough concurrent
  slow requests to one workspace's site exhaust it and 502 every other workspace. A relayed
  WebSocket is not in that pool at all and is held open for as long as the viewer keeps it, so it is
  unbounded on both counts. One fairness bound per workspace, from the token's verified claims, would
  cover both; it waits on real traffic shapes and on deciding what a turned-away viewer is told.
- **A view token's claims are readable, not secret.** The token is signed, not encrypted, so its
  workspace id, conversation id, port, and expiry decode from any copy of the URL — a browser
  history entry, an access log, a `Referer` on an outbound link the site itself renders. It grants
  nothing without the signature, and the label already names the site, so the exposure is
  identifiers rather than access. It is called out because the `/~t/` URL is the one a member
  pastes to a colleague: what leaks is which conversation produced a site and when the link dies.

## Doctrine fit

- **Core additions and why extensions cannot express them**: `ingress_token` + `ingress_host` +
  `surface_token` (extensions never hold `UFO_TOKEN_SECRET`), `Carrier.dial` (the carrier protocol
  is core's),
  `ufoctl ingress` (process topology, like `ufoctl proxy`), `SurfaceContext.ingress_url` (the
  mint). None of it knows what a site is.
- **Member actions stay in chat**: visibility is settable by directive at creation and through
  the `site` object kind afterward. The in-frame selector is a second consumer of the same column,
  explicitly requested in #276; the speaker-gates-the-granting-act rule holds — the selector acts
  only for the authenticated creator.
- **Both ends**: every declared piece ships producer and consumer in its unit — the token with its
  verifying route, the dial seam with the ingress, the column with the selector and the kind.
- **Fail loud**: no silent fallback from a missing handle to a stale cache; a sandbox that is gone
  or unroutable and an origin that will not answer through a live sandbox are both a 503, each
  naming what the member can do next, never a hung stream. The ingress answers no 502 or 504: the
  proxied wildcard's edge discards those and substitutes an error page under `x-frame-options:
  SAMEORIGIN`, which the frame reading a site is not.

## Alternatives

- **Promote built files to the blob store and serve from blob** (previous draft): rejected — the
  workspace S3 sync is being dropped, and it forked static and live sites into two serving models.
- **Serve site traffic from the main serve process**: rejected — one event loop; a popular site
  would stall every turn, stream, and surface (§Hot paths).
- **Per-file carrier reads instead of proxying to the in-sandbox server**: rejected — a second
  data path for exactly the static half, and the tools already leave a running server behind.
- **Site id in the URL instead of a signed token**: rejected — `identify` would need a
  cross-workspace owner read on an extension table to resolve the workspace; the signed token
  carries it.

## Open decisions

- `workspace` visibility currently means "any member of the workspace"; an email-domain allowlist
  distinct from membership (the issue mentions both) is deferred until a workspace needs
  domain-wide-but-not-member viewers.
