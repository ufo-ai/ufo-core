# Inbound Request, Surface, and Session Handling  `stage-6`

This stage is the live system’s set of front doors. After the server has started, it receives people and outside services through many paths: the browser portal, Slack, iMessage, terminal clients, shared site links, object APIs, sign-in flows, and downloads. Its job is to turn those incoming requests into safe, recognized workspace actions.

The web portal and workspace APIs serve the browser app. They sign members in, load chats and settings, stream agent replies, check who may access which agents, and support admin-style pages. Chat, terminal, and external messaging surfaces do the same kind of translation for Slack, iMessage, and command-line clients: they verify the sender, map outside messages into workspace conversations, and send replies back out. Workspace object request handling is the common front desk for things like agents, members, credentials, sources, memories, monitors, reports, and hosted sites, with permission checks for each.

The surfaces package file simply lets this group of code be imported. The hosted-sites surface handles public share links, shows allowed pages safely in frames, and lets creators control link access.

## Sub-stages

- [Web Portal and Workspace APIs](stage-6.1.md) `stage-6.1` — 8 files
- [Chat, Terminal, and External Messaging Surfaces](stage-6.2.md) `stage-6.2` — 10 files
- [Workspace Object Request Handling](stage-6.3.md) `stage-6.3` — 14 files

## Files in this stage

### Hosted Site Surface
Defines the importable surface package and the public hosted-site doorway for safe framed share links, access checks, and creator-managed visibility.

### `core/src/ufo/runtime/surfaces/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it has an `__init__.py` file. That means other parts of the project can refer to code under `core/src/ufo/runtime/surfaces` using normal Python import paths.

There is no logic here: no functions, classes, setup work, or side effects. Its job is structural, like a label on a drawer saying “the files inside belong together.” Without it, depending on the Python version and packaging setup, imports from this folder might fail or behave differently. Keeping the file also makes the project layout clearer to readers and tooling.


### `extensions/sites/ufo_ext_sites/surface.py`

`io_transport` · `request handling`

A hosted site link is meant to be easy to share, but it must not become a secret back door. This file solves that by treating the link as an address, not as permission. Each visit is checked again: the code verifies the signed site token, finds the workspace and site, reads the viewer from the session cookie if there is one, and applies the site’s visibility rules. Public sites can be opened by anyone. Workspace sites require a signed-in workspace member. Private sites are limited to the creator and workspace admins.

The actual site bytes are not served from this page. Instead, this file builds a wrapper page with an iframe, like a picture frame around a painting. The iframe points to a separate ingress URL that is minted for that visit. This keeps the hosted site’s scripts away from the main portal session and from the selector used to change visibility.

The file also supports special homepage links for agents and deploy-wide shipped app bundles. Those often need to open inside the member portal, so the code redirects viewers there when needed. Finally, it creates safe social preview tags and serves public share-card images only while the site is still public, so private names and screenshots do not leak through chat unfurl previews.

#### Function details

##### `site_token`  (lines 174–183)

```
def site_token(workspace_id: UUID, conversation_id: UUID, name: str) -> str
```

**Purpose**: Creates the permanent signed token that names one hosted site. Someone would use it when they need a stable link that identifies a workspace, conversation, and site name without exposing database access.

**Data flow**: It receives a workspace ID, conversation ID, and site name. It packages those values into signed token claims for the sites surface. It returns the token string that can later be verified by this same surface.

**Call relations**: When `site_url` needs to build a full public link, it asks `site_token` for the token part of that link. The token is later read back by functions such as `site_address` during request handling.

*Call graph*: called by 1 (site_url); 1 external calls (mint_surface_token).


##### `site_url`  (lines 186–196)

```
def site_url(public_base_url: str | None, workspace_id: UUID, conversation_id: UUID, name: str) -> str
```

**Purpose**: Builds the full public URL for a hosted site. It refuses to invent a link if the deployment has no public base URL configured, because such a link would not actually work.

**Data flow**: It receives the public base URL plus the site’s workspace, conversation, and name. If the base URL is missing, it raises a clear configuration error. Otherwise it creates a site token and returns the frame route URL containing that token.

**Call relations**: This is the main producer of permanent site links. It delegates the token-making part to `site_token`, then adds the deployment’s public address and the hosted-site frame path around it.

*Call graph*: calls 1 internal fn (site_token); 1 external calls (__init__).


##### `shipped_homepage_url`  (lines 208–223)

```
def shipped_homepage_url(public_base_url: str | None, workspace_id: UUID, slug: str, digest: str) -> str | None
```

**Purpose**: Builds a stable portal-embed link for a deploy-wide shipped app bundle. This is for app code that belongs to the deployment rather than to one editable hosted site row.

**Data flow**: It receives the public base URL, workspace ID, shipped app slug, and bundle digest. If there is no public base URL, it returns nothing. Otherwise it signs those values into a token marked as a portal embed and returns the frame URL for it.

**Call relations**: This link is later interpreted by `shipped_address` and opened by `_shipped_frame` through the normal frame route. It uses the same surface-token system as ordinary hosted site links, but with different claims.

*Call graph*: 1 external calls (mint_surface_token).


##### `shipped_address`  (lines 226–240)

```
def shipped_address(token: str) -> ShippedAddress | None
```

**Purpose**: Checks whether a token names a shipped app bundle and, if so, extracts its address information. It keeps shipped-app tokens separate from ordinary site tokens.

**Data flow**: It receives a token string. It verifies the signature and confirms the token is marked as a portal embed, then reads the workspace ID, slug, and digest. If anything is missing, malformed, or not a shipped-app token, it returns nothing; otherwise it returns a `ShippedAddress` object.

**Call relations**: `resolve_workspace` uses this when a request might be for shipped app code rather than a hosted site. `frame` also checks it first so shipped app links can be handed to `_shipped_frame` instead of being looked up as site rows.

*Call graph*: called by 2 (frame, resolve_workspace); 3 external calls (__init__, verify_surface_token, UUID).


##### `site_card_url`  (lines 243–252)

```
def site_card_url(public_base_url: str | None, token: str, digest: str) -> str | None
```

**Purpose**: Builds the public URL for a site’s social share-card image. The digest in the URL helps old cards stop matching after the card image changes.

**Data flow**: It receives the public base URL, the site token, and the image digest. If there is no public base URL, it returns nothing. Otherwise it returns the anonymous share-card route for that token and digest.

**Call relations**: `frame` calls this only when preparing preview metadata for a public, non-homepage site. The URL it returns points back to `share_card`, which rechecks that the site is still public before serving the image.

*Call graph*: called by 1 (frame).


##### `site_address`  (lines 255–274)

```
def site_address(token: str) -> SiteAddress | None
```

**Purpose**: Verifies an ordinary hosted-site token and turns it into a usable address. It answers the question: which workspace, conversation, and site name does this link claim to point at?

**Data flow**: It receives a token string. It verifies the token for the sites surface, checks whether it is a normal link or an allowed portal-embed form, converts ID text into UUID values, and returns a `SiteAddress`. If verification or required fields fail, it returns nothing.

**Call relations**: This is the common reader for ordinary site links. `resolve_workspace`, `frame`, `_resolve`, and `homepage_embed_url` all use it before trusting anything inside a URL.

*Call graph*: called by 4 (_resolve, frame, homepage_embed_url, resolve_workspace); 3 external calls (__init__, verify_surface_token, UUID).


##### `homepage_embed_url`  (lines 277–292)

```
def homepage_embed_url(url: str) -> str
```

**Purpose**: Converts a normal hosted-site URL into the special signed form used when that site is embedded as a portal homepage. It ensures only real hosted-site URLs are converted.

**Data flow**: It receives a URL, splits off the last path segment as the token, and verifies that the URL points at the hosted-site frame path. It reads the original site address, signs a new token with the portal-embed marker, and returns the same URL path with the new token. If the input is not a valid hosted-site URL, it raises an error.

**Call relations**: It relies on `site_address` to validate the original link before minting a portal-embed version. Later, `frame` sees the portal-embed marker and treats homepage iframe requests differently from cold browser visits.

*Call graph*: calls 1 internal fn (site_address); 1 external calls (mint_surface_token).


##### `resolve_workspace`  (lines 295–305)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: Figures out which workspace a request belongs to before the site row is read. This matters because public viewers may not have a session cookie that could otherwise identify the workspace.

**Data flow**: It reads the token from the request path. It first tries to interpret it as an ordinary hosted-site token, then as a shipped-app token. It returns the workspace ID when either form is valid, returns a not-found response for invalid tokens, or returns nothing only when no workspace can be resolved through the expected path.

**Call relations**: The surface framework calls this early so the rest of the request runs in the correct workspace context. It uses `site_address` and `shipped_address`, and hides bad tokens behind the same `_not_found` response used elsewhere.

*Call graph*: calls 3 internal fn (_not_found, shipped_address, site_address).


##### `frame`  (lines 308–384)

```
async def frame(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Opens a hosted site link. It verifies the link, checks the viewer’s right to see the site, and then either redirects to the embedded site origin or returns the wrapper page containing the iframe.

**Data flow**: It receives the surface context and HTTP request. It reads the token, detects shipped-app tokens, resolves the hosted site row, prepares safe preview metadata, identifies the viewer from the session cookie, applies homepage or site visibility rules, mints an ingress URL for the iframe when allowed, and returns an HTTP response. The response may be a 404, a sign-in page, a redirect into the portal, a redirect to ingress, or an HTML frame page.

**Call relations**: This is the central request handler for GET site links and deep links. It hands shipped-app requests to `_shipped_frame`, uses `_viewer`, `_viewer_is_admin`, `_sites`, `_share_tags`, `site_card_url`, `_into_the_portal`, `_unconfigured_page`, and `_frame_page` as helper parts of the larger gate-and-render flow.

*Call graph*: calls 18 internal fn (ingress_url, list_agents, _frame_page, _framed_from, _into_the_portal, _is_portal_iframe_request, _not_found, _page, _session_digest, _share_tags (+8 more)); 3 external calls (HTMLResponse, RedirectResponse, mint_surface_token).


##### `_shipped_frame`  (lines 387–426)

```
async def _shipped_frame(ctx: SurfaceContext, request: Request, shipped: ShippedAddress) -> Response
```

**Purpose**: Opens a deploy-wide shipped app bundle through the same frame system. Unlike normal hosted sites, it does not read a hosted-site row because the bundle is deployment code selected by slug and digest.

**Data flow**: It receives the surface context, request, and a verified shipped address. If the request is not already inside the portal iframe, it finds the matching agent and redirects the viewer to that agent’s portal screen. If it is inside the portal iframe, it builds an ingress URL for the shipped app bundle and redirects there, or returns an unconfigured page if ingress cannot be made.

**Call relations**: `frame` calls this after recognizing a shipped-app token. It uses `_is_portal_iframe_request` and `_framed_from` to understand how the request arrived, `_into_the_portal` for cold visits, and `_share_tags` for generic preview metadata.

*Call graph*: calls 8 internal fn (ingress_url, list_agents, _framed_from, _into_the_portal, _is_portal_iframe_request, _not_found, _share_tags, _unconfigured_page); called by 1 (frame); 5 external calls (HTMLResponse, RedirectResponse, serve_port, shipped_anchor, shipped_app_slug).


##### `_is_portal_iframe_request`  (lines 429–430)

```
def _is_portal_iframe_request(request: Request) -> bool
```

**Purpose**: Checks whether the browser says this request is for an iframe. This helps distinguish a page opened directly from the same page being loaded inside the member portal.

**Data flow**: It reads the `sec-fetch-dest` request header. If the value is `iframe`, it returns true; otherwise it returns false.

**Call relations**: `frame` and `_shipped_frame` use this to decide whether to redirect into the portal or continue with an embedded view. `_framed_from` uses it before trusting the referrer as the page that framed the request.

*Call graph*: called by 3 (_framed_from, _shipped_frame, frame).


##### `_framed_from`  (lines 433–436)

```
def _framed_from(request: Request) -> str | None
```

**Purpose**: Returns the page that framed this request, but only when the request is actually an iframe request. This avoids treating an ordinary browser referrer as iframe context.

**Data flow**: It receives the request, checks whether it is an iframe load, and then reads the `referer` header. It returns that referrer for iframe requests or nothing for non-iframe requests.

**Call relations**: `frame` and `_shipped_frame` pass this value into ingress URL creation. It depends on `_is_portal_iframe_request` so the ingress side gets framing context only when that context is meaningful.

*Call graph*: calls 1 internal fn (_is_portal_iframe_request); called by 2 (_shipped_frame, frame).


##### `_into_the_portal`  (lines 439–452)

```
def _into_the_portal(ctx: SurfaceContext, agent_id: UUID, share: str) -> Response
```

**Purpose**: Redirects a viewer to an agent’s page inside the member portal. This is needed for pages that rely on the portal’s bridge to receive their startup information.

**Data flow**: It receives the surface context, an agent ID, and preview metadata. It asks the context for the portal URL for that agent. If a portal exists, it returns a redirect there; if not, it returns a small HTML page explaining that there is no portal to open.

**Call relations**: `frame` uses this for homepage-bound sites opened outside the portal iframe. `_shipped_frame` uses it for shipped app links opened cold. It uses `_page` to build the fallback explanation.

*Call graph*: calls 2 internal fn (home_url, _page); called by 2 (_shipped_frame, frame); 2 external calls (HTMLResponse, RedirectResponse).


##### `_unconfigured_page`  (lines 455–463)

```
def _unconfigured_page(title: str, share: str) -> str
```

**Purpose**: Builds the HTML shown when the deployment cannot create an ingress URL for the embedded site or app. In plain terms, it says hosting is not configured instead of showing an empty frame.

**Data flow**: It receives a title and share metadata. It escapes the title for safe HTML, combines the base styles with frame styles, adds the unconfigured message, and returns a complete HTML string.

**Call relations**: `frame` uses this when a homepage redirect cannot be minted. `_shipped_frame` uses it for shipped app bundles in the same situation. It delegates the final document shell to `_page`.

*Call graph*: calls 1 internal fn (_page); called by 2 (_shipped_frame, frame); 1 external calls (escape).


##### `share_card`  (lines 466–505)

```
async def share_card(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the social preview image for a public site. It is deliberately strict so private or no-longer-public sites do not keep leaking screenshots through cached chat previews.

**Data flow**: It receives the context and request, resolves the site from the token, and checks that the site exists, is not an agent homepage, is public, has a stored card image, and that the URL digest matches the row’s current card hash. If any check fails, it returns the standard not-found response. If all checks pass, it reads the image bytes from blob storage and returns them with cache headers.

**Call relations**: This route is reached by crawlers or browsers following the URL produced by `site_card_url` and inserted by `_share_tags`. It uses `_resolve` to find the site and `_not_found` to avoid revealing whether a private site exists.

*Call graph*: calls 2 internal fn (_not_found, _resolve); 1 external calls (Response).


##### `set_visibility`  (lines 508–529)

```
async def set_visibility(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lets the creator of a hosted site change whether it is private, workspace-visible, or public. It protects the form with a CSRF token, which is a signed proof that the request came from the creator’s own session page.

**Data flow**: It resolves the site, identifies the viewer, and confirms the viewer is the site creator. It rejects homepage-bound sites because their visibility follows the agent instead. It reads the submitted form, verifies the CSRF token, parses the requested visibility level, updates the stored site row, and redirects back to the frame.

**Call relations**: This is the POST handler for the visibility selector produced by `_selector` inside `_frame_page`. It uses `_viewer` to authenticate the person, `_csrf_holds` to protect the form, and `_sites` to write the new visibility value.

*Call graph*: calls 5 internal fn (_csrf_holds, _not_found, _resolve, _sites, _viewer); 4 external calls (PlainTextResponse, RedirectResponse, form, visibility_level).


##### `_resolve`  (lines 532–536)

```
async def _resolve(ctx: SurfaceContext, request: Request) -> HostedSite | None
```

**Purpose**: Looks up the hosted-site row named by the request token. It is a small shared helper for routes that need the site but do not handle shipped-app tokens.

**Data flow**: It reads the token from the request path, verifies and decodes it with `site_address`, and then reads the matching site from the workspace’s hosted-site store. It returns the site row or nothing if the token is invalid or the row is absent.

**Call relations**: `share_card` and `set_visibility` both use this before applying their own extra rules. It uses `_sites` so store access is always scoped to the current workspace context.

*Call graph*: calls 2 internal fn (_sites, site_address); called by 2 (set_visibility, share_card).


##### `_sites`  (lines 539–540)

```
def _sites(ctx: SurfaceContext) -> HostedSites
```

**Purpose**: Creates the hosted-site store object for the current workspace. This is the doorway to reading or updating hosted-site rows.

**Data flow**: It receives the surface context, takes the workspace ID and transaction factory from it, and returns a `HostedSites` store bound to that workspace.

**Call relations**: `frame`, `_resolve`, and `set_visibility` use this whenever they need to read or update hosted-site data. It keeps store creation in one place so each route uses the same workspace-scoped access pattern.

*Call graph*: called by 3 (_resolve, frame, set_visibility); 1 external calls (__init__).


##### `_viewer_is_admin`  (lines 543–548)

```
async def _viewer_is_admin(ctx: SurfaceContext, viewer: UUID | None) -> bool
```

**Purpose**: Checks whether the current viewer is a seated workspace admin. Admins are allowed to see private sites and private agent homepages even when they are not the creator or owner.

**Data flow**: It receives the context and a possible viewer ID. If there is no viewer, it returns false. Otherwise it opens a transaction, reads the workspace seat snapshot, and returns true only if the matching member is both seated and marked as an admin.

**Call relations**: `frame` calls this when deciding whether to admit someone to a private hosted site or a private homepage-bound agent. It relies on the seats system for the current membership state.

*Call graph*: calls 1 internal fn (transaction); called by 1 (frame); 1 external calls (__init__).


##### `_viewer`  (lines 551–564)

```
async def _viewer(ctx: SurfaceContext, request: Request) -> UUID | None
```

**Purpose**: Identifies the signed-in workspace member for this request. It turns the `ufo_session` cookie into a member ID, or returns nothing when the browser is not validly signed in for this workspace.

**Data flow**: It reads the session cookie. If missing, it returns nothing. It verifies the bearer token against the workspace, gets an email, links that email to a member if needed, checks that the member still has access, and returns the member ID only when all steps succeed.

**Call relations**: `frame` uses this to decide whether the viewer can see a site. `set_visibility` uses it to prove the person posting the form is the creator. It hands cookie verification to `verify_token` and membership work to the surface context.

*Call graph*: calls 3 internal fn (link_member, linked_member, member_has_access); called by 2 (frame, set_visibility); 1 external calls (verify_token).


##### `_csrf_holds`  (lines 567–569)

```
def _csrf_holds(request: Request, submitted: str) -> bool
```

**Purpose**: Checks whether a submitted visibility form token matches this browser session. This blocks another site from tricking a signed-in creator’s browser into changing visibility.

**Data flow**: It receives the request and submitted token. It verifies the token signature and compares the token’s stored session digest with the digest of the current request’s session cookie. It returns true only when they match.

**Call relations**: `set_visibility` calls this after it has already confirmed the viewer is the creator. It uses `_session_digest` to bind the form token to the exact browser session that loaded the frame.

*Call graph*: calls 1 internal fn (_session_digest); called by 1 (set_visibility); 1 external calls (verify_surface_token).


##### `_session_digest`  (lines 572–576)

```
def _session_digest(request: Request) -> str
```

**Purpose**: Creates a one-way fingerprint of the current session cookie for CSRF protection. A one-way fingerprint means the code can compare sessions without placing the raw cookie value into the form token.

**Data flow**: It reads the `ufo_session` cookie from the request, uses SHA-256 hashing to turn it into a fixed digest string, and returns that digest.

**Call relations**: `frame` uses this when minting a CSRF token for the creator’s visibility selector. `_csrf_holds` uses it again during form submission to confirm the token belongs to the same session.

*Call graph*: called by 2 (_csrf_holds, frame); 1 external calls (sha256).


##### `_not_found`  (lines 579–580)

```
def _not_found() -> Response
```

**Purpose**: Returns the standard not-found response used throughout this surface. Using the same body for missing, invalid, or unauthorized resources helps avoid revealing which private sites exist.

**Data flow**: It creates a plain-text HTTP response with the shared not-found message and a 404 status code. Nothing else is read or changed.

**Call relations**: `resolve_workspace`, `frame`, `_shipped_frame`, `share_card`, and `set_visibility` all use this when a request should not learn more. It is a small but important part of the privacy story.

*Call graph*: called by 5 (_shipped_frame, frame, resolve_workspace, set_visibility, share_card); 1 external calls (PlainTextResponse).


##### `_page`  (lines 583–588)

```
def _page(title: str, style: str, body: str, share: str) -> str
```

**Purpose**: Builds a complete minimal HTML document from a title, style block, body, and share metadata. It is the common wrapper for the simple pages this surface returns.

**Data flow**: It receives already prepared title text, CSS style text, HTML body text, and metadata tags. It concatenates them into one HTML document string with charset and viewport metadata.

**Call relations**: `frame`, `_frame_page`, `_into_the_portal`, and `_unconfigured_page` use this so their responses share the same basic document shape. The callers are responsible for escaping any user-controlled text before passing it in.

*Call graph*: called by 4 (_frame_page, _into_the_portal, _unconfigured_page, frame).


##### `_share_tags`  (lines 591–625)

```
def _share_tags(name: str | None, canonical: str | None, card: str | None) -> str
```

**Purpose**: Creates the metadata that chat apps and social sites read when unfurling a link. It names the site and uses the site’s own card only when the caller has already decided that information is public.

**Data flow**: It receives an optional public site name, optional canonical URL, and optional card image URL. It escapes all values for HTML, chooses a generic title and brand image when private information should not be exposed, and returns Open Graph and Twitter metadata tags.

**Call relations**: `frame` calls this for ordinary hosted-site pages after deciding whether the site is public. `_shipped_frame` calls it with generic values. The image URL it may include points to the `share_card` route, which performs its own public-only check.

*Call graph*: called by 2 (_shipped_frame, frame); 1 external calls (escape).


##### `_frame_page`  (lines 628–667)

```
def _frame_page(site: HostedSite, embedded: str | None, frame_path: str, csrf: str, share: str) -> str
```

**Purpose**: Builds the main hosted-site wrapper page: a header with the site name and visibility control or badge, plus the iframe that displays the site itself. It also applies sandbox rules so model-authored site content cannot take over the whole browser tab.

**Data flow**: It receives the site row, optional ingress URL, frame path, CSRF token, and share metadata. If a CSRF token is present, it builds the creator’s visibility selector; otherwise it shows a visibility badge. If an ingress URL exists, it creates a sandboxed iframe pointing at it; otherwise it shows the hosting-unconfigured message. It returns a complete HTML page.

**Call relations**: `frame` calls this after the viewer has passed the visibility gate for an ordinary hosted site. `_frame_page` uses `_selector` for the creator’s form and `_page` for the final document shell.

*Call graph*: calls 2 internal fn (_page, _selector); called by 1 (frame); 1 external calls (escape).


##### `_selector`  (lines 670–680)

```
def _selector(current: Visibility, frame_path: str, csrf: str) -> str
```

**Purpose**: Builds the small visibility-change form shown to a site creator. It lets the creator choose private, workspace-visible, or public and submit the change safely.

**Data flow**: It receives the current visibility level, the frame path to post back to, and a CSRF token. It creates option elements with the current level selected, includes the hidden CSRF field, escapes URL and token values for HTML, and returns the form HTML.

**Call relations**: `_frame_page` calls this only when `frame` has supplied a CSRF token, which happens for the site creator. The form submits to the `set_visibility` route, where the token and requested level are checked before the store is updated.

*Call graph*: called by 1 (_frame_page); 1 external calls (escape).

## 📊 State Registers Touched

- `reg-durable-database` — The main long-term database where shared business and runtime records are stored.
- `reg-workspace-member-agent-state` — The saved list of workspaces, people, memberships, seats, and agents.
- `reg-agent-configuration` — Each agent’s saved settings, such as model choice, reasoning mode, tools, visibility, internet access, sandbox size, and setup needs.
- `reg-extension-registry` — The loaded set of extensions and the routes, tools, hooks, jobs, skills, agents, and backends they contribute.
- `reg-extension-install-store` — The saved record of which extensions are installed, removed, or holding extension-specific data.
- `reg-surface-routing` — The shared routing state that maps browser, Slack, iMessage, terminal, site, and object requests to the right workspace, agent, and conversation.
- `reg-auth-identity-sessions` — The current proof of who a person, operator, shared-link visitor, or external service caller is.
- `reg-credential-connections` — The encrypted accounts, secrets, connection grants, and credential fulfillments that let agents use outside services safely.
- `reg-access-permissions-audience` — The shared rules for who may read, use, share, or act on workspace content and conversations.
- `reg-feature-flags` — The rollout switches that turn product and infrastructure behavior on or off across the system.
- `reg-memory-index-state` — The stored knowledge, embeddings, chunks, and memory indexes that agents can search later.
- `reg-source-config-sync-state` — The configured external sources plus their sync progress, errors, backoff, ownership, and access grants.
- `reg-tool-catalog-allowlists` — The shared list of tools and actions an agent may see or run, including extension tools and sandbox bridge tools.
- `reg-conversation-turn-queue` — The durable state of conversations and turns, including admission, ordering, current runner, lifecycle status, and queued work.
- `reg-inbound-message-queue` — The saved queue of incoming external messages waiting to be rendered, ordered, deduplicated, and admitted as turns.
- `reg-live-turn-stream` — The live event feed that lets clients and other processes watch a running turn and learn how it ended.
- `reg-transcript-history` — The saved conversation transcript, summaries, compactions, and access records that preserve what happened in a chat.
- `reg-sandbox-runtime` — The durable sandbox and browser workspace handles where agent commands, files, web browsing, and hosted previews run safely.
- `reg-blob-artifact-store` — The shared file, blob, artifact, preview, download, and hosted media storage used by turns and surfaces.
- `reg-presentation-slots` — The shared conversation display slots for showing artifacts, sources, tasks, sites, automations, image previews, and other side-panel content.
- `reg-object-journal` — The shared naming and change history for workspace objects such as tasks, prompts, skills, monitors, memories, and reports.
- `reg-database-connection-pool` — The shared SQLAlchemy engine/session and connection-pool state used by requests, turns, workers, migrations, and persistence helpers to access the database safely.
- `reg-surface-listener-leases` — The stored claims/leases that coordinate which runtime instance is allowed to listen on a shared surface installation or address, avoiding duplicate external listeners.
- `reg-outbound-surface-delivery-queue` — The durable outgoing reply/writeback state, including mid-turn replies and surface deliveries that must be claimed, sent, retried, and acknowledged exactly once.
- `reg-workspace-object-store` — The current persisted workspace object records, such as tasks, monitors, todos, reports, prompts, and site metadata, read and mutated through object APIs, tools, jobs, and slots.
- `reg-proposal-approval-state` — Persisted proposed changes with before/after payloads, authoring information, and pending/approved/rejected status used for review and application workflows.
- `reg-server-route-mounts` — The in-process ASGI route, middleware, static mount, and extension route table assembled at startup and used to dispatch later HTTP/webhook/control requests.
