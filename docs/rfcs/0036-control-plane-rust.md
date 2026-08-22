---
rfc: 0036
title: "Control plane in Rust — its own ledgers, a core RPC for core's tables"
status: implemented
date: 2026-08-17
---

# Control plane in Rust — its own ledgers, a core RPC for core's tables

> RFC 0035 moved the egress data plane to Rust and left every policy decision, key, and ledger write
> in core `serve`, noting there would be "nothing to re-port when `control/` becomes Rust". This RFC
> is that port. `control/` becomes a Rust crate holding one credential — a role scoped to the three
> `ufo_control` ledgers it alone owns — and reaches every core table through an internal RPC that
> core `serve` answers on paths it already runs. The gateway pod stops holding the RLS-bypassing
> owner DSN, and the control image stops carrying a Python runtime.

## Current state

`control/` is 3,515 lines of Python across 14 modules plus 6,270 lines of pytest. It serves
`/healthz`, `/ufo`, `/ufo/bin/{target}`, `/fleet`, `/login`, the three WorkOS paths, and
`/v1/onboard/{channel}` (`control/src/ufo_control/gateway.py:414-593`), and carries five CLI verbs:
`gateway`, `migrate`, `invite`, `slack-connect-retry`, `rls-bootstrap`
(`control/src/ufo_control/main.py`).

Two things make it more than an HTTP server.

**It imports core Python.** Twelve symbols across three modules:

| Site | Imports |
|---|---|
| `gateway_shared.py:10-17` | `ufo.billing.balance.credit`/`set_reserve`, `ufo.db.workspace_tx`, `ufo.onboard.onboarding` defaults, `ufo.schema.tables`, `ufo.schema.records.DEFAULT_AGENT_NAME`, `ufo.seats.create_member`/`email_domain`, `ufo.turns.untrusted.wall`, `ufo.workspace.ws` |
| `gateway.py:24-26` | `ufo.db.init_db`/`dispose_db`/`workspace_tx`, `ufo.ext.surface.OPERATOR_EMAIL_DOMAIN`, `ufo.sdk.http.set_session_cookie` |
| `gateway_token.py:7` | `ufo.auth.bearer.mint_token` |

This is why `control/Dockerfile` is `FROM ${UFO_IMAGE}` — the control image exists only as a layer on
the full Python runtime distribution.

**It holds the owner DSN in the serving pod.** `gateway_app`'s lifespan opens a pool on
`UFO_CONTROL_POSTGRES_OWNER_DSN` (`gateway.py:365`) and a second connection on
`UFO_CONTROL_SERVE_DSN` via `init_db` (`gateway.py:400`). The owner role bypasses RLS —
`control/src/ufo_control/rls.py` never sets `FORCE ROW LEVEL SECURITY`, so the table owner is exempt
by default. A pod on the public sign-in path can therefore read every workspace's rows.

It uses that reach in exactly three places:

| Site | Statement |
|---|---|
| `gateway.py:443` | `select count(*) from workspace` — the landing fleet |
| `gateway_shared.py:118-168` | `choices()` — the cross-workspace `member` read that lists a verified address's workspaces |
| `gateway_shared.py:171-262` | `create`/`join`/`_ensure` — the workspace row, the member seat, the default agent, the signup credit and reserve |

Everything else control touches is its own: the `ufo_control` schema's three ledgers
(`gateway_store.py`, `gateway_invite.py`, `gateway_slack_connect.py`), shaped by
`ufo-control migrate` (`schema.py`).

Core already runs both halves this RFC needs.

| Existing path | Cited |
|---|---|
| An internal RPC router behind a bearer control token | `core/src/ufo/access/egress_control.py:170`, with a second router on its own token at line 180 |
| The RLS-bypassing cross-workspace read | `owner_tx` (`core/src/ufo/db.py:357`), opened from `UFO_OWNER_DSN` by `serve` (`serve.py:412`, `compose.yaml:116`) and named at `db.py:11` as the one exception — "the RLS-bypassing read the cross-workspace background sweeps" use |

## Proposal

### The split

| Concern | Owner | Credential |
|---|---|---|
| HTTP, sessions, WorkOS, SES, Slack Connect poller, directive rendering, client binary | Rust | — |
| The three `ufo_control` ledgers and their DDL | Rust | `ufo_control` login role, scoped to the `ufo_control` schema |
| Every read and write of a core table — `choices`, membership, fleet, the member seat | core `serve` | its existing `owner_tx` / `workspace_tx` |
| `ufo_owner` DDL — schema shaping, roles, policies | Rust CLI verbs `migrate` / `rls-bootstrap` | owner DSN, in the deploy Job only |

One sentence states the boundary: **control's SQL reaches its own three tables and nothing else.**

The cross-workspace read joins `owner_tx`, which core already designates as the one cross-tenant
path, rather than opening a second one beside it.

### Credentials

| Variable | Holder | Change |
|---|---|---|
| `UFO_CONTROL_POSTGRES_OWNER_DSN` | `migrate`, `rls-bootstrap` Jobs | no longer reaches the gateway pod. `invite` and `slack-connect-retry` are run from a gateway pod and write one `ufo_control` row each, so they read the gateway DSN — asking for the owner's would put the verb somewhere no operator can run it. |
| `UFO_CONTROL_GATEWAY_DSN` | gateway pod | new — role `ufo_control`, granted the `ufo_control` schema alone, nothing in `public` |
| `UFO_ONBOARD_CONTROL_TOKEN` | gateway pod, `serve` | new — gates `/internal/onboard/*` alone |
| `UFO_CONTROL_SERVE_DSN` | — | deleted; control no longer opens core's tables |
| `UFO_TOKEN_SECRET`, WorkOS, SES/IRSA | gateway pod | unchanged |

`rls-bootstrap` grows a third role beside `ufo_owner` and `ufo_serve`, its password derived from
`UFO_CONTROL_PG_ROLE_SEED` the way `ufo_serve`'s is (`rls.py:29-33`).

The fence is a grant, checked by a test. Every grant in `rls.py:146-159` names `ufo_serve`
explicitly, and the live database agrees: each core table carries
`relacl={ufo=arwdDxtm/ufo,ufo_serve=arwd/ufo}` and `pg_default_acl` is empty. A fresh role therefore
holds `USAGE` on schema `public` (Postgres's own default) and no privilege on any table in it —
`has_table_privilege` answers false and the select answers `permission denied for table member`.

### The onboarding RPC

Four routes on core `serve` under `/internal/onboard`, behind
`Authorization: Bearer <UFO_ONBOARD_CONTROL_TOKEN>` — its own secret, never the egress control
token, following RFC 0035's two-secrets rule.

| Route | Runs as | Replaces |
|---|---|---|
| `GET /choices?email=&domain=` | `owner_tx` | `gateway_shared.py:118-168` |
| `GET /membership?workspace_id=&email=` | `workspace_tx` under `ws(workspace_id)` | the `is_admin` read at `gateway_shared.py:185-195` |
| `GET /fleet` | `owner_tx` | `gateway.py:443` |
| `POST /seat` | `workspace_tx` under `ws(workspace_id)` | `_ensure` at `gateway_shared.py:198-262` |

```
POST /internal/onboard/seat
{"workspace_id": "<uuid>", "domain": "acme.com", "email": "dana@acme.com",
 "profile": {"business": "...", "goals": "..."} | null}
→ 200 {"workspace_id": "<uuid>", "admin": true}
→ 409 {"error": "workspace <uuid> no longer belongs to acme.com"}
```

`seat` runs today's `_ensure` body unchanged: the `on conflict do nothing` workspace insert, the
`for update` lock, the first-member domain check, `create_member`, the default-agent insert with
`agent_prompt(profile)`, and — only when this call founded the workspace — `credit` plus
`set_reserve`. No seat semantics are reimplemented, so `SIGNUP_GRANT_MICRO_USD`, the walled intake
prompt, and `DEFAULT_AGENT_MODEL` keep their single home.

The two `owner_tx` routes — `choices` and `fleet` — warn on every call, under the event
`onboard.cross_workspace_read`. WARNING is the level `o11y._bridge_warning_logs` exports to the
collector, so a read that leaves a workspace on control's behalf is visible rather than inferred.
Only these two are logged: `owner_tx` itself stays silent, because its other callers are scheduled
sweeps that repeat forever and would bury the signal.

Control derives `workspace_id` in both paths: `uuid5(NAMESPACE_DNS, domain)` for a new workspace, the
selected candidate's id for a join. The `uuid` crate's `Uuid::new_v5` is the same computation.

`_ensure`, `agent_prompt`, `_inert`, `SIGNUP_PROMPT`, `INTAKE_SOURCE`, `INTAKE_FIELDS`,
`SIGNUP_GRANT_MICRO_USD`, `SIGNUP_RESERVE_MICRO_USD`, and `EnsuredWorkspace` move from
`gateway_shared.py` to `core/src/ufo/onboard/onboarding.py`, beside the `DEFAULT_AGENT_*` values they
already use, keeping their names. `core/src/ufo/onboard/onboard_control.py` is the router over them, shaped
like `egress_control.py`.

**The blast radius.** Control can no longer read a core table at all. The cross-workspace membership
oracle `choices` represents still exists — sign-in must list the workspaces a verified address may
enter before it belongs to any — but it now sits behind the control token on an internal route
rather than behind a database credential on the public pod, and it discloses no more than today:
workspace existence, its uuid, and the first member's email domain.

### Crate shape

`control/` becomes a Rust crate in place, standalone like `cache/`, `client/`, and `egress/` — the
repo has no root Cargo workspace.

```
control/Cargo.toml            ufo-control
control/src/main.rs           clap: gateway | migrate | invite | slack-connect-retry | rls-bootstrap
control/src/gateway.rs        axum routes and Onboarding::advance
control/src/{web,claim,directives}.rs
control/src/{store,invite,slack_connect,schema}.rs
control/src/{shared,token,workos,email,rls}.rs
control/src/client/ufo        the POSIX script, include_str!
control/tests/{gateway_it,schema_it,contract}.rs
```

`tokio-postgres` + `deadpool-postgres`, not `sqlx`: control's SQL is raw strings today, and `sqlx`'s
compile-time query checking needs a live database at image-build time. The rest — `axum`, `reqwest`,
`serde`, `hmac`/`sha2`/`base64`, `uuid`, `chrono`, `anyhow`, `thiserror`, `tracing` — matches
`egress/Cargo.toml`, with `clap` and `opentelemetry-otlp` added.

`control/Dockerfile` stops being `FROM ${UFO_IMAGE}`: a Rust build stage over a slim runtime
carrying the binary and the `clientbin` directory. The control image drops the Python runtime.

### Two hand-rolled integrations

Neither needs a vendor SDK, because neither meaningfully uses one today.

| Integration | Surface | Rust |
|---|---|---|
| WorkOS | four calls: `get_authorization_url`, `authenticate_with_code`, `create_magic_auth`, `authenticate_with_magic_auth` (`gateway_workos.py:141-163`) | `reqwest` against the same REST endpoints; the `authenticate_with_code` response shape is pinned by a test |
| SES | already a hand-rolled SigV4 signer over `httpx` plus STS `AssumeRoleWithWebIdentity` (`gateway_email.py:177-290`) | direct translation on `reqwest` + `hmac`/`sha2`; no AWS SDK |

### The bearer codec

`core/src/ufo/auth/bearer.py` is a 115-line self-contained HMAC codec whose wire format its own docstring
spells out. Rust mints it; core keeps the verify half every surface uses. Golden vectors —
`(secret, workspace_id, email, exp) → token` — are asserted from both sides, the shape
`egress/tests/contract.rs` and `rule_contract.json` already established.

### Tests

| Suite | Home |
|---|---|
| Gateway HTTP surface, claim, invite, email, Slack Connect, schema | `control/tests/*.rs`, against real Postgres |
| Bearer vectors and the RPC wire | `control/tests/contract.rs` + a golden JSON, checked from both sides |
| `/internal/onboard/*` | new `core/tests/test_onboard_control.py` |
| The `ufo_control` role's fence — `select` on `member` denied, `has_table_privilege` false | a `control/tests/schema_it.rs` case |

CI runs Postgres as a service, applies core's alembic migrations with `uv run`, then `cargo test` —
the order `control/tests/conftest.py:73` establishes today, and the shape the existing `rls` job on
:5544 already uses.

### Repo fallout, same change

| Site | Change |
|---|---|
| `testsupport/ufo_testsupport/wire_fixture.py:13` | imports `ufo_control.gateway_directives.directive` to build the golden fixtures every client replays; fixture generation moves into the Rust crate |
| `gates.py:61-64` | three control paths repoint from `.py` to `.rs`; the wire-parity gate already parses `client/src/wire.rs` |
| `core/tests/test_deploy_workflow.py:733,3949`, `core/tests/test_onboarding_corpus_claims.py:24-28` | six control source paths repoint |
| root `pyproject.toml:105,262`, `.pre-commit-config.yaml:37` | drop the `ufo-control` workspace member and its mypy entry; add cargo fmt/clippy |
| `compose.yaml`, `Makefile`, `README.md`, `docs/onboarding.md`, `docs/permissions.md` | the verb table and the RLS section |
| deploy | the `ufo-control` ECR image builds from Rust; the `ufo-migrate` Job command keeps its name |

## Doctrine fit / implications

**Core stays core.** What moves into `core/` is the seat write that already lived in core's
vocabulary — `create_member`, `credit`, `DEFAULT_AGENT_*` — plus one router mounted the way
`egress_control.py` already mounts its own. Nothing extensions could express moves in.

**One shape.** The cross-workspace read joins `owner_tx`, the path core already designates for it,
instead of standing a second mechanism beside it.

**Enforce, don't document.** "Control cannot read tenant data" becomes a grant with a test.
`docs/permissions.md:22,84` states it in prose today against a pod holding the owner DSN.

**Both ends or neither.** Each `/internal/onboard/*` route lands with its Rust caller and its core
handler in the same change, exercised end to end.

**What gets worse.** Sign-in depends on `serve` for the candidate list and for first-time seating.
Today control answers both itself. A `serve` outage already makes a minted bearer useless, so the
practical loss is small — but the refusal needs a member-visible sentence.

## Alternatives

| Option | Why not |
|---|---|
| `SECURITY DEFINER` functions in `ufo_control`, so control keeps the cross-workspace read | Drafted, then dropped on finding `owner_tx`: it solves a problem core does not have, since `serve` already holds `UFO_OWNER_DSN` for exactly this read. The mechanism also carries two traps — `CREATE FUNCTION` grants `EXECUTE` to `PUBLIC` by default (120+ live functions sit at that default, so `ufo_serve` could call the function and defeat its own RLS), and the function bypasses RLS only when its owner owns the tables, which is `ufo_owner` deployed but the `ufo` superuser locally, so a hardcoded owner returns an empty candidate list rather than an error. |
| Move control's three ledgers into core too, leaving control a pure API caller | Marginal: control still holds `UFO_TOKEN_SECRET`, which forges a bearer for any workspace — strictly more powerful than its ledger access. Making it real means moving minting as well, ~12 RPC verbs instead of four, a round trip per state-machine step, and three pre-tenant ledgers plus the invite CLI and Slack poller landing in `core/`. |
| Reimplement the seat write in Rust SQL | A second answer to how a member is seated, how a workspace is credited, and what the default agent's prompt is. |
| Keep the owner DSN on the gateway and port faithfully | Smaller diff, but the rewrite is precisely when the composition root changes anyway. |
| Keep `gateway_shared` as a Python sidecar | Two deploy artifacts forever, and the onboarding flow spans two languages permanently. |
| Route-level switch, or CLI verbs first | Lower blast radius, at the cost of a dual stack across releases. Big-bang chosen by the author. |

### The database TLS the port had to add

`tokio_postgres` defaults to `sslmode=prefer`, and `prefer` does not mean "TLS if it works": once the
server answers the SSLRequest, a failed handshake is a connection error with no fallback to plaintext
(`connect_tls.rs`). A managed instance always answers it, and its certificate is signed by a CA in no
public root store — so a pool built on the platform roots alone cannot connect to it at all. asyncpg
reached the same instance only because libpq's `prefer` does not verify the certificate.

The control image therefore ships the managed bundle and points `UFO_CONTROL_PG_CA_BUNDLE` at it.
Verified against a Postgres offering TLS under a private CA: refused without the bundle ("error
performing TLS handshake"), connected with it, and connected under `sslmode=disable` — which is what
a local stack that offers no TLS falls back to. The port is a strict improvement on the Python here:
the connection was unverified before and is verified now.

## How the open decisions resolved

| Fork | Resolution |
|---|---|
| Where `control/tests/test_rls.py` (937 lines) lands | It split. The bootstrap half — policies, roles, grants, timeouts, the DBOS database, lock-timeout behaviour — is `control/tests/rls_it.rs`. The `SharedWorkspaces` half — the seat, the signup balance, the default agent, candidate resolution — is `core/tests/test_onboard_control.py`, because that code moved to core. |
| The directive encoder for `wire_fixture.py` | Neither. The two codecs were byte-identical, so the fixture is generated through the one surviving Python encoder and the Rust producer is held to those same golden lines by `control/tests/contract.rs`. Regenerating produced a zero-byte diff. |
| Refusal copy when `serve` is unreachable | "Could not reach the workspace service. Try again." A refusal core itself worded rides back verbatim instead, so a domain mapping to two workspaces reads the same either side of the wire. |

## What the port changed from this design

| Item | Why |
|---|---|
| The seat write landed in a new `core/src/ufo/onboard/onboard_control.py`, not `onboarding.py` | `onboarding.py` is the `ufoctl init` first-run flow. The router and its four workflows read top to bottom in one file, beside their only caller. |
| `ufo-control serve-dsn` is a sixth verb | `dev/entrypoint.sh` derived the serve DSN by importing `ufo_control.rls`. The derivation keeps one home rather than being respelled in shell. |
| The gateway pod's pool is lazy | `deadpool` opens no connection until one is taken, where `asyncpg.create_pool(min_size=1)` opened one per replica at boot. The boot-race the Python pool-budget test guarded cannot occur; the steady-state ceiling is unchanged at four. |
