---
rfc: 0035
title: "Egress proxy in Rust — a data plane over a core control RPC"
status: proposed
date: 2026-08-16
---

# Egress proxy in Rust — a data plane over a core control RPC

> RFC 0032 split sandbox egress into a Rust **data plane** (the cache daemon: byte streaming, no
> keys, phones the control plane for the one credential it needs) and a Python **control plane**
> (authorization, credential minting, metering). This RFC moves the rest of the data plane — the MITM
> proxy — into Rust the same way. The Rust proxy holds no customer key, no owner DSN, and no policy
> logic: it verifies the deployment-signed run/probe token, asks core `serve` over an internal
> token-scoped RPC what egress is allowed (and for the resolved secrets), enforces it on the wire, and
> posts metering back. Every policy decision, every key, and every ledger write stay in core `serve`,
> which already owns them — no reimplementation, no drift, and nothing to re-port when `servers/control/`
> becomes Rust.

## Current state

Sandbox egress goes through the Python MITM proxy: `core/src/ufo/harness/sandbox/proxy/server.py` (1,947 lines,
the wire + the policy), `rules.py` (292, the `Rule` derivations), `proxy_serve.py` (220, the standalone
composition root), `credential_callback.py` (123, the cache daemon's git-credential callback). It boots
standalone (`ufoctl proxy`) and in-process inside `serve.py`. It opens the RLS-bypassing **owner DSN**
and does four things per turn, all in the proxy process:

| Concern | Reads/writes | Owner logic |
|---|---|---|
| Authorize each CONNECT | turn `running`? + agent `internet_access_allowed` + workspace `egress_rules_generation` | `PerAgentRules.turn_live` |
| Resolve rules | model base, credential injections (incl. GitHub-App **minting** in the `coding` ext), grant scopes, CLI forwards | `PerAgentRules.resolve`, `slot_secret`, `rules.py` |
| Broker forward | a sentinel request executed server-side | `ComposioRequestForwarder` (the `composio` ext) |
| Meter | `ledger` upserts (egress + sandbox_tokens, priced) | `accounting.record_*` |

The policy is **extension-coupled** (Composio forward and GitHub-App minting are extensions;
`PerAgentRules` composes manifest-declared slots/clis/transfer-hosts) and **key-bearing** (the Fernet
credential key, the App private key, the broker key). None of it can leave core Python without
re-porting the extension system.

## Decisions

Settled with the author:

| Fork | Decision |
|---|---|
| Split | Rust owns the **wire only** (CONNECT, TLS-terminate/MITM, relay, inject, DNS-pin, SSE token parse). Core `serve` owns **all policy, keys, and metering**, exposed as an internal RPC. |
| Where the control RPC lives | Core **`serve`** (Python), not `servers/control/` and not a sidecar on the egress pod. `serve` already runs this exact code and holds the keys; `servers/control/` (soon Rust) stays the sign-in gateway. |
| Keys | The Fernet key, the GitHub-App key, the Composio key, and the owner DSN live only in core `serve`. The egress pod holds none of them — a compromised proxy leaks at most a turn's already-on-the-wire secret, never a master key. |
| Owner DSN | Deleted from the egress path. `serve` resolves each token under `ws(workspace_id)` with its normal **RLS-scoped** `ufo_serve` role; no cross-workspace superuser DSN is distributed to the edge. |

## Architecture

```
sandbox ──HTTP(S)_PROXY──▶ ufo-egress (Rust, data plane)
                             │ verify run/probe token (HMAC signing secret only)
                             │ CONNECT default-deny · TLS MITM (rcgen leaf) · DNS-pin · relay · inject
                             │ tee model response → parse Usage (SSE/JSON)
                             │        │ authorize / resolve / meter / forward
                             └────────┴──── internal RPC ───▶ core serve (control plane, RLS-scoped)
                                                                 PerAgentRules · slot_secret · grants
                                                                 accounting · Composio forward · keys
                                                                        │
                                                                     Postgres
  cache daemon ──── git-credential ────▶ core serve (same endpoint, repointed off the proxy pod)
```

### The internal egress RPC (core `serve`)

The `/internal/egress/*` routes are gated by `Authorization: Bearer <UFO_EGRESS_CONTROL_TOKEN>` (a
shared secret the proxy holds; a request without it is refused before any work). The run/probe token
rides in the body as the raw `Proxy-Authorization` value; `serve` verifies its signature and scopes to
its workspace. The proxy also verifies the token locally (for the per-workspace connection cap and the
rule cache key) — the signing secret is not a customer credential, so it is fine on the edge.

The git-credential callback the cache daemon phones is a **separate** route under its **own**
`UFO_CACHE_CONTROL_TOKEN`, not the egress control token. Two secrets, two surfaces: the cache
credential reaches only `/internal/git-credential`, and the egress token — the key to the secrets and
metering tier — is never accepted there. This is the isolation the loopback callback had before the
route moved onto `serve`, and it needs no cache-side change (the daemon already presents its own token).

| Route | Guard | Request | Response | Reuses |
|---|---|---|---|---|
| `POST /internal/egress/authorize` | egress | `{proxy_auth}` | `{authorized, generation}` — fresh, per CONNECT | `PerAgentRules.turn_live` / probe expiry + `rules_generation` |
| `POST /internal/egress/resolve` | egress | `{proxy_auth}` | `{rules: [...]}` — injections carry **real secrets**; cached per `(token, generation)` | `PerAgentRules.resolve` |
| `POST /internal/egress/meter` | egress | `{records: [...]}` (batched) | `{}` | `accounting.record_*` (+ pricing) |
| `POST /internal/egress/forward` | egress | `{proxy_auth, account_id, method, url, headers, body_b64}` | `{status, headers, body_b64}` | the grant's `RequestForwarder.forward` |
| `POST /internal/git-credential` | **cache** | `{workspace_id, host}` | `{username, token, principal}` or `{principal:"public"}` | `credential_callback` logic, moved here |

Rule wire shape (one object per rule, `kind`-tagged) — the one contract Python serializes and Rust
deserializes:

```
{"kind":"scope","hosts":[...]}                                    {"kind":"internet"}
{"kind":"injection","host":_,"header":_,"sentinel":_,"real":_}     {"kind":"meter","host":_,"dimension":_}
{"kind":"forward","host":_,"header":_,"sentinel":_,"account_id":_} {"kind":"service","host":_,"daemon_prefix":_|null}
```

Metering records: `{"kind":"egress","workspace_id":_,"turn_id":_|null}` and
`{"kind":"tokens","workspace_id":_,"turn_id":_,"model":_,"usage":{...}}`. The proxy parses model usage
off the wire (it tees the response) and posts it; `serve` prices and writes — so pricing and its digest
never leave Python.

### Crate `servers/egress/` (thin)

| Module | Responsibility |
|---|---|
| `main.rs` | boot: config, tracing, control client, signal shutdown, serve |
| `config.rs` | env: bind/port/public-url, `UFO_TOKEN_SECRET`, egress CA (or ephemeral), control RPC URL + `UFO_EGRESS_CONTROL_TOKEN`, cache daemon addr |
| `token.rs` | run/probe token verify — **HMAC byte-parity** with `token_signing.py` (kept) |
| `tls.rs` | shared/ephemeral CA + per-host `rcgen` leaf + rustls configs (kept) |
| `usage.rs` | `HttpTokenUsage`: chunked/gzip/deflate + Anthropic/OpenAI SSE/JSON usage (kept) |
| `types.rs` | `Rule` enum (serde, the RPC wire type), tokens, `Usage` |
| `control.rs` | the RPC client: `authorize`, `resolve`, `forward`, `meter` |
| `meter.rs` | batch meter records and post them via `control` |
| `server.rs` | the wire: accept, caps, CONNECT parse, auth gate, rule cache (by generation), tunnel/mitm/service dispatch, relay half-close, refusal drain |

Dropped versus a self-contained proxy: `db` (sqlx), `credentials` (Fernet/JWT/seal), `pricing`,
`rules` derivations, `resolver`, and the standalone `callback` — all served by core `serve` now. The
Rust dependency set loses sqlx, fernet, and jsonwebtoken.

### Parity contracts

Only two values must match across the boundary now, both already de-risked:

1. **Token signing** — `base64url(payload) + "." + base64url(HMAC_SHA256(secret, body))`; the proxy
   verifies what the carrier env minted.
2. **Usage parsing** — the Anthropic/OpenAI SSE/JSON shapes incl. OpenAI's cached-prompt normalization.

Fernet, `uuid5` ledger ids, and the price digest stay in Python (core `serve`), where they already
live — so they are not parity contracts at all.

## Both ends

**Core `serve` (Python):** add `egress_control.py` — the `/internal/egress/*` routes above, wiring
`PerAgentRules`, `accounting`, and the connector forwarders that already exist; move the
git-credential callback here. Delete `sandbox/proxy/server.py` (the wire) and `credential_callback.py`;
`proxy_serve.py` sheds the wire and stays as the composition-root base (`model_rule_base`, the owner
DSN both `serve` and `ingress` open). `rules.py`, `PerAgentRules`, `accounting`, and the extension
forwarders **stay** (now behind the RPC). `serve.py`/`cli.py` stop booting a Python proxy: `serve` mounts the
control routes and hands the carrier the endpoint, and the `ufo-egress` wire runs as its **own
process** everywhere — a separate pod hosted, a compose service (`network_mode: service:serve`, so
the local carrier's `127.0.0.1:proxy_port` reaches it unchanged) in the dev rig, both sharing the
egress CA and control token through env. `serve` does **not** spawn or supervise it; a plain
`ufoctl serve` with no proxy running gives the sandbox a proxy address that refuses every CONNECT,
so a well-behaved client gets no egress — which is why the dev rig runs the service beside it.

**Rust (`servers/egress/`):** the crate above.

**Tests split with the code:** the wire tests (`test_proxy_server.py`'s CONNECT/MITM/relay/DNS/caps
+ the SSE parse) become Rust `#[tokio::test]`s driving a real CONNECT against a **fake control RPC**
(and, for the live leg, real git). The policy tests (`test_proxy_rules.py`, resolution, metering,
forward, `test_grants`/`test_connectors`) **stay Python**, retargeted at the new `/internal/egress`
endpoint or at `PerAgentRules` directly. `make test` and `cargo test` both green.

## Deploy

- `servers/egress/Dockerfile` + `.github/workflows/egress.yml` (fmt, clippy, `cargo test` — no Postgres needed
  now; the Rust tests use a fake control server).
- `deploy.yml` builds/pushes `ufo-egress`; the proxy pod runs it beside the cache daemon.
- The proxy env carries the control RPC URL + `UFO_EGRESS_CONTROL_TOKEN` + `UFO_TOKEN_SECRET` +
  the egress CA; no DB DSN, no credential key, no pricing table.

## Risks

- **Request-time dependency on `serve`.** `resolve` caches per `(token, generation)` with a TTL; only
  the tiny liveness check is fresh per CONNECT; `serve` is a fleet (HA) — the same availability class
  the proxy's Postgres dependency was. A `serve` fault answers the CONNECT with 503, never a broad
  allow.
- **Secret on the wire.** `resolve` returns injections with real secrets — the same bytes the proxy
  puts on the wire, short-lived and cached briefly; the master keys that mint them never cross.
- **Fake-control fidelity.** The Rust wire tests assert against a fake control server; a golden
  contract test (Python serializes a rule set, Rust deserializes it) pins the JSON shape so the fake
  cannot drift from `serve`.

## Non-goals

No change to what egress is allowed, metered, or injected — a language and topology move. No change to
the cache daemon beyond repointing its credential callback. `servers/control/` is untouched.
