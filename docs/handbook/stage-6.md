# Ingress, authentication, and surface routing  `stage-6`

This stage is the system’s front gate and switchboard. It sits at the edge of the service during normal use, where requests first arrive from browsers, terminals, Slack, iMessage, public app links, OAuth callbacks, operator tools, and developer/debug surfaces. Its first job is to decide who or what the request belongs to. Then it sends the request to the right workspace, member, conversation, hosted app, or runtime action.

The login and token checks act like security labels. Member login tokens prove the user and workspace. Surface tokens give limited proof for public entry points before a normal session exists. Shared signing code makes sure these labels cannot be quietly altered. Sandbox ingress tokens add time limits and purpose checks for preview and hosted-app access.

The member and operator surfaces are the visible doors. The web app, chat integrations, terminal client, hosted sites, and operator tools all translate outside actions into UFO conversation work, then return replies and live updates.

The sandbox ingress host adds one more routing layer. It creates and verifies special hostnames for sandboxed sites, tying each safe URL to a specific conversation and port.

## Sub-stages

- [Login, session, and signed-token checks](stage-6.1.md) `stage-6.1` — 4 files
- [Member-facing and operator-facing surfaces](stage-6.2.md) `stage-6.2` — 19 files

## Files in this stage

### Ingress, authentication, and surface routing
### `core/src/ufo/harness/sandbox/ingress_host.py`

`io_transport` · `request routing and hosted-site URL creation`

A hosted sandbox site needs its own web origin, meaning its own hostname, so browser cookies, storage, redirects, and root-based asset paths stay separate from every other site. This file turns a conversation ID and a sandbox port into a compact DNS label, like a short address printed on an envelope. The label contains the conversation ID, the port, and a short cryptographic signature made with the deployment secret. The signature is not the main security gate; tokens and cookies still decide who may enter. Its job is to stop random guessed hostnames from even being treated as valid addresses for a conversation.

The file also makes sure there is exactly one accepted spelling for each label. This matters because base32 text encoding can sometimes decode several different strings into the same bytes. Browsers would treat those different strings as different sites, with separate cookies and storage, so the parser decodes and then re-encodes the label to reject non-canonical spellings.

Besides labels, the file derives stable ports from conversation IDs and stable synthetic IDs for shipped app pages that do not come from a normal conversation row. In short, it gives every sandboxed or shipped site a durable, isolated web address.

#### Function details

##### `serve_port`  (lines 57–63)

```
def serve_port(conversation_id: UUID) -> int
```

**Purpose**: Chooses the stable sandbox port for a conversation. This lets every part of the system derive the same port from the conversation ID instead of storing and syncing a separate port value.

**Data flow**: It receives a conversation UUID. It reads the UUID as a large number, folds it into the allowed sandbox port range, adds the configured lower bound, and returns the resulting port number. It does not change any stored state.

**Call relations**: Other code can call this when it needs to know where a conversation's hosted site should listen. It stands at the start of the address-making flow: once the port is known, code can combine it with the conversation ID to build a site label.


##### `shipped_app_slug`  (lines 66–74)

```
def shipped_app_slug(provisioned_by: str | None) -> str | None
```

**Purpose**: Extracts the stable page slug from the name of a provisioned shipped app, if that name has the expected form. A slug is the short app identifier used for origins and bundle paths.

**Data flow**: It receives either a provision name string or nothing. If there is no name, or the name does not exactly look like `app_` followed by lowercase letters and digits, it returns nothing. If it matches, it returns just the slug part after `app_`.

**Call relations**: This is used when the system needs a stable app identity rather than a display name that might have been changed to avoid a collision. Its output can feed later steps that create stable shipped-app origins.


##### `shipped_anchor`  (lines 77–82)

```
def shipped_anchor(workspace_id: UUID, slug: str) -> UUID
```

**Purpose**: Creates a stable synthetic UUID for a shipped app page inside one workspace. This gives shipped pages their own browser storage and cookies even when there is no normal conversation row behind them.

**Data flow**: It receives a workspace UUID and an app slug. It combines them with a fixed label and passes that text into UUID version 5, which makes the same UUID every time for the same input. The returned UUID can then be used like an anchor identity for the app page.

**Call relations**: When shipped app pages need durable origins, this function supplies the identity they can be based on. It hands the deterministic ID creation to `uuid.uuid5`, which is designed for making repeatable UUIDs from names.

*Call graph*: 1 external calls (uuid5).


##### `site_label`  (lines 85–90)

```
def site_label(conversation_id: UUID, port: int) -> str
```

**Purpose**: Builds the DNS label for one conversation and sandbox port. This is the short hostname component that tells the ingress layer which sandbox address a browser is trying to reach.

**Data flow**: It receives a conversation UUID and a port number. It first rejects ports outside the valid network-port range. Then it packs the conversation bytes and port bytes together, signs those bytes with the deployment secret, appends the signature, and base32-encodes the result into lowercase text. The returned string is safe to use as a DNS label.

**Call relations**: This is the label-making side of the flow. It relies on `_signature` to add the tamper-checking tag and `_encode` to turn raw bytes into DNS-friendly text. Later, `parse_site_label` performs the mirror-image check when a request arrives with such a label.

*Call graph*: calls 2 internal fn (_encode, _signature).


##### `parse_site_label`  (lines 93–105)

```
def parse_site_label(label: str) -> tuple[UUID, int]
```

**Purpose**: Reads a DNS label back into the conversation ID and port it claims, but only if the label is well formed, canonically spelled, and signed by this deployment. It prevents bad hostnames from reaching deeper sandbox lookup work.

**Data flow**: It receives a label string from a hostname. It base32-decodes the text, re-encodes the decoded bytes to ensure this is the one accepted spelling, splits the bytes into address and signature, and compares the signature with a freshly computed one. If anything is malformed or does not match, it raises `SiteLabelError`. If all checks pass, it returns the conversation UUID and port number.

**Call relations**: This is the label-checking side of the flow, typically used when a request arrives for a sandbox hostname. It calls `_encode` to enforce canonical spelling and `_signature` plus `hmac.compare_digest` to check the signature safely. If it succeeds, later routing code can use the returned conversation and port; if it fails, the request can be rejected before touching sandbox state.

*Call graph*: calls 2 internal fn (_encode, _signature); 4 external calls (__init__, b32decode, compare_digest, UUID).


##### `_encode`  (lines 108–109)

```
def _encode(raw: bytes) -> str
```

**Purpose**: Turns raw bytes into the lowercase base32 text used in site labels. Base32 is an encoding that represents bytes using DNS-friendly letters and numbers.

**Data flow**: It receives a byte string. It base32-encodes those bytes, removes padding characters that are not needed in the label, lowercases the result, and returns the final text.

**Call relations**: This helper is used by `site_label` when creating labels and by `parse_site_label` when checking that an incoming label uses the one canonical spelling. It delegates the byte-to-text conversion to Python's `base64.b32encode`.

*Call graph*: called by 2 (parse_site_label, site_label); 1 external calls (b32encode).


##### `_signature`  (lines 112–114)

```
def _signature(address: bytes) -> bytes
```

**Purpose**: Creates the short cryptographic signature attached to a site label. The signature proves that the label was minted by code that knows this deployment's ingress secret.

**Data flow**: It receives the raw address bytes, meaning the conversation ID plus port. It reads the ingress secret, combines that secret with a fixed label-kind marker and the address bytes, computes an HMAC using SHA-256, and returns only the first four bytes of the digest.

**Call relations**: This helper is used by `site_label` to sign newly created labels and by `parse_site_label` to recompute what the signature should be for an incoming label. It calls `ingress_secret` to get the shared deployment secret and `hmac.new` to perform the keyed hash.

*Call graph*: called by 2 (parse_site_label, site_label); 2 external calls (new, ingress_secret).

## 📊 State Registers Touched

- `reg-workspace-member-seat-state` — The shared record of workspaces, members, admins, invitations, seats, and workspace-level limits.
- `reg-auth-tokens-sessions` — The login, surface, sandbox, and signing tokens that prove who a request belongs to and what it may access.
- `reg-authority-context` — The current acting identity for runtime work, saying which workspace, member, and agent are allowed to act.
- `reg-surface-routing-state` — The saved routing information that maps web, Slack, iMessage, terminal, hosted app, and public-link traffic to the right workspace and conversation.
- `reg-conversation-records` — The durable conversation state, including conversation identity, title, surface label, sandbox handle, audience, and related metadata.
- `reg-turn-queue-state` — The durable queue of conversation turns, including admission source, run claim, parked state, resume state, and final status.
- `reg-audience-visibility-state` — The shared privacy labels that decide who may read or join conversation content and workspace objects.
- `reg-live-updates-delivery` — The live reply and notification delivery state used to stream running turns and safely deliver mid-turn or delayed messages once.
- `reg-sandbox-handles` — The durable handles and leases that let conversations reconnect to their sandbox, files, ports, hosted previews, and work directories.
- `reg-egress-proxy-policy` — The network access rules and proxy state that decide which outside hosts can be reached and when secrets may be attached.
- `reg-credential-vault-connections` — The lockbox of account connections, OAuth grants, API keys, BYOK attempts, and agent permissions to use outside services.
- `reg-portal-slots-ui-state` — The structured conversation portal display state that extensions can fill with artifacts, sources, tasks, sites, and automations.
- `reg-inbound-message-buffer` — Durable inbound messages from external surfaces waiting to be rendered, admitted, deduplicated, or converted into conversation work.
- `reg-human-request-state` — Pending and resolved human-interaction requests, including agent questions, secret requests, credential requests, and connection-authorization handoffs.
- `reg-transcript-access-audit` — Audit records of privileged transcript reads, especially admin access to another member’s private conversation history.
- `reg-user-feedback-buffer` — Collected user/operator feedback events, ratings, comments, and review signals used by telemetry, diagnostics, and offline improvement loops.
