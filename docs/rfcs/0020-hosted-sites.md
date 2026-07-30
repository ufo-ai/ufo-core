---
rfc: 0020
title: "Hosted sites — sandbox ingress and the access-controlled frame"
status: proposed
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
  (`core/src/ufo/surfaces/artifacts.py:25`): token-gated single-file download, TTL 1h. No
  multi-request browsing, no viewer identity, no visibility.
- Creation context is already modeled: `conversation.audience`
  (`core/src/ufo/audience.py`) is `member:<id>` (DM/CLI/web), `room:<surface>:<room>` (channel),
  `foreign:<surface>:<room>` (Slack Connect), or `shared`.
- Viewer identity is already modeled: the HMAC bearer `{ws, email, exp}` (`core/src/ufo/bearer.py`),
  minted by the gateway after an emailed code, carried by the web surface's `ufo_session` cookie;
  `SurfaceContext.linked_member(email)` resolves it to a member.
- The sandbox is reachable from the host: `Carrier.dial(handle, port)`
  (`core/src/ufo/sandbox/session.py:223`) maps an in-sandbox port to an externally dialable
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
| site bytes (every asset / app request) | **sandbox ingress** (`ufoctl ingress`), new | the site's own subdomain of `[sandbox] ingress_public_url` (`*.sites.<domain>`, wildcard cert) | all of it |

Each site lives at its own origin: a stable DNS label that is a signed *address* for
`(conversation, port)` — base32 of the conversation id, the port, and a truncated HMAC (34 bytes →
55 chars, inside DNS's 63, case-insensitive by construction). The workspace is not in the label; it
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

- `core/src/ufo/sandbox/ingress_token.py`: `mint_ingress_token(claims, kind)` /
  `verify_ingress_token(token, now, kind)` over `token_signing`, claims
  `{kind, ws, conversation_id, port, exp}`. The deploy secret is resolved inside core exactly as
  `ufo.bearer` resolves it, so neither end takes it as a parameter and no caller holds it. The two
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
- `core/src/ufo/sandbox/ingress_host.py`: `site_label(conversation_id, port)` /
  `parse_site_label(label)` — the DNS label codec, base32 over the conversation, the port, and a
  truncated HMAC of the same deploy secret. It refuses to import if the signed bytes would outgrow
  DNS's 63-character label bound, and parses only the one canonical spelling of a label: 34 bytes
  fill 272 of the 275 bits 55 base32 characters carry, so without that check eight labels decode
  alike and a browser reads all eight as separate origins with their own cookies and storage.
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
  named `/~theme.css` is served rather than read as a malformed token. Every other
  request — every method an app serves (GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS) —
  reads its site off the Host, authorizes from that cookie, reads `conversation.sandbox_handle` for
  the cookie's workspace, dials, and streams. The handle read is one owner-DSN query per request and
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
  sites hostname (`*.sites.<domain>` DNS + cert) to it.

`SurfaceContext` gains `ingress_url(conversation_id, port)` so the sites surface mints without
holding the secret — the `artifact_link` pattern. The TTL is `ingress_token`'s own constant, not an
argument: one deploy has one answer for how long a link may sit in a frame.

### Surface tokens (core, new)

The permanent site link needs the workspace resolvable from the URL alone (a public viewer has no
cookie; `SurfaceSpec.identify` must return the workspace before any DB binding).
`core/src/ufo/surface_token.py` + `ufo.sdk.surface_token`: `mint_surface_token(surface, payload)` /
`verify_surface_token(surface, token)`, HMAC over `UFO_TOKEN_SECRET` resolved inside core exactly
as `ufo.bearer` does, namespaced by surface so one surface's token never replays at another. The
site link is `{public_base_url}/surface/sites/{site_token}` with payload `{ws, site}` — an
address, not an authorization, so it never expires; every request still passes the visibility gate.

### The sites pack (everything else)

**`hosted_site` table** (extension-owned, own migration, like `user_skill`):
`(workspace_id, conversation_id, name)` identity, plus `port`, `visibility`,
`creator_member_id`, timestamps. Re-deploying upserts the row and bumps `updated_at`.

**Registration**: `deploy_website` and `publish_website`, after their readiness probe, upsert the
row and return `site_url` beside the sandbox-local `url`. A port serves one origin, so
registering retires any other site row on the same `(conversation, port)` — otherwise an older
name would silently serve the newer deploy's content. Creator is `ctx.acting_member_id`
(raise without one — a site needs an owner). Default visibility from `ctx.audience`:

| audience | default |
|---|---|
| `member:<id>` | `private` |
| `room:<surface>:<room>` | `workspace` |
| `shared` | `workspace` |
| `foreign:<surface>:<room>` | `private` |

`foreign` is doctrine, not taste: a sealed external room must never default a site into the whole
company. An explicit `visibility` argument (the chat directive, e.g. "make it public") overrides.

**Visibility levels**: `private` (creator member only) · `workspace` (any authenticated member of
the workspace) · `public` (anyone with the link).

**Frame** (`/surface/sites/{site_token}`, main serve): verifies the token, authenticates the
viewer from the `ufo_session` cookie (public sites skip it), gates on visibility, then renders
header + iframe with a fresh view token. Creator sees a live selector; another authorized member
sees a read-only badge ("ask in chat to change it"); no cookie on a non-public site gets a sign-in
page linking a plain `/login`, after which the member reopens the site link (the return-to that
would have spared them is withdrawn, below); everything else is a uniform 404 — no existence
oracle.
Because the frame page (app origin) and the site (ingress origin) are cross-origin, the embedded
site's scripts cannot reach the selector, the session cookie, or any app endpoint; the visibility
`POST` still carries a CSRF token bound to the session cookie.

**Visibility changes — both ends of one column**: the in-frame selector `POST`s to the sites
surface (creator + CSRF gated), and a `site` object kind gives chat the same act —
`object_apply` flips `visibility`, `object_delete` unregisters the site; create/update-by-spec
are refused naming `deploy_website`, the `artifact` kind's pattern. The read path lists this
agent's audience-visible sites.

**Prompt section**: `sites_section.md` gains the hosted link — the deliverable becomes the
`site_url`, with `share_file` for a downloadable copy.

## Units

| unit | ships | proof |
|---|---|---|
| U1 sandbox ingress | `ingress_token`, `Carrier.dial` (e2b/docker/local), `ufoctl ingress`, infra Deployment, e2b `allow_public_traffic=False` | token → bytes stream from a live sandbox port; tampered/expired → 403; cleared handle → 503 |
| U2 sites pack | `hosted_site` + migration, tool registration + audience defaults, `surface_token` seam, frame + selector + sign-in, visibility POST, `site` object kind, prompt section, `ingress_public_url` knob, `SurfaceContext.ingress_url`, ingress host-label addressing + per-site session cookie | site built in a DM: creator 200, other member 404; flip to `workspace`: other member 200; foreign room defaults private; two sites never share an origin |

U1 has no sites knowledge and U2 consumes it. A third unit — a `?next=` return-to on `/login`, so a
coworker bounced to sign in lands back on the site — was built and withdrawn (PR #839): the gateway
sets no browser credential, so the redirect arrived anonymous, took a 401, and destroyed the token
the page was holding, restarting the email-code walk it was meant to shorten. A working return-to
has to end on a workspace-host URL that binds the cookie before forwarding, which spans the gateway,
the web surface, and the edge worker — its own unit, not this RFC's. Until then the frame links a
plain `/login` and the member reopens the site link.

## Dependencies and named risks

- **The idle reaper (being moved to the docker extension in a separate change).** Today
  `SandboxReaper` (`core/src/ufo/jobs.py:280`) destroys idle sandboxes on *any* backend at 30
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
- **No WebSocket proxying.** The ingress forwards HTTP methods only, so a dev server's live
  reload (Vite HMR) never connects; a `publish_website` app that needs a socket at runtime does
  not work through the frame either.
- **The sites wildcard is published DNS-only, and the reason is measured.** Proxying a wildcard is
  available on every Cloudflare plan, so that was never the limit; the zone already serves a free
  Let's Encrypt edge certificate carrying `*.testing.flyingobject.ai`, so ACM is active on it too.
  The limit is depth: a wildcard SAN matches exactly one label, so nothing on that certificate
  covers `<label>.sites.<apex>`. Measured with the record proxied on the first deploy —
  `probe.sites.testing.flyingobject.ai` resolved to Cloudflare addresses and the TLS handshake was
  refused outright (`SSLV3_ALERT_HANDSHAKE_FAILURE`), so every site was unreachable. DNS-only
  restores hosting: nginx terminates with the cert-manager wildcard, which DNS-01 issues whatever
  the record's proxy state. The cost is that site bytes alone reach the shared NLB directly, without
  proxy-side DDoS absorption, WAF, or origin-IP concealment. Adding `*.sites.<apex>` to the zone's
  ACM certificate is what lets `cloudflare-proxied` flip back to `"true"`, and that is the only
  change needed then.
- **The sites base is a sibling subdomain of the app host, so a site's scripts can still plant a
  cookie on it. Scrubbing covers the header path only.** `sites.<domain>` shares a registrable domain
  with `app.<domain>`, and that shared parent is what the browser computes `Domain=`-scoped cookies
  and SameSite on, so it does not separate a site from the app for us. The ingress scrubs what it
  relays — `Domain` stripped from every `Set-Cookie`, every cookie named under `ufo_` dropped, a
  nameless cookie dropped with them — and that closes the response header as a write path
  completely. It does not touch the other one: a site is agent-authored code *with scripts*, and
  `document.cookie = "ufo_session=…; domain=<parent>"` from the site's own origin is accepted by the
  browser and never passes through the ingress at all. The app host reads `ufo_session` at face
  value, so under `public` visibility — where a site's author and its viewer can be in different
  workspaces — that is cross-tenant session fixation. Nothing in the tree stops the script today: no
  CSP, no iframe `sandbox`. The two candidate closes are a separate registrable domain for the sites
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
- **No per-workspace fairness on the shared upstream pool.** The client is bounded
  (`UPSTREAM_MAX_CONNECTIONS`) but the bound is process-wide: enough concurrent slow requests to
  one workspace's site exhaust it and 502 every other workspace. U2 sizes the fairness bound
  (per workspace, from the token's verified claims) once real traffic shapes exist.
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
  or unroutable is a 503 and an origin that will not answer through a live sandbox is a 502, each
  naming what the member can do next, never a hung stream.

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
