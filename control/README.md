# ufo-control

The hosted gateway and database bootstrap for ufo's shared workspace fleet, a standalone Rust crate.

One `ufoctl serve` fleet hosts every workspace. A signed bearer selects the workspace before the
request reaches core, and Postgres row-level security enforces the same scope on every transaction.
The gateway verifies a work email, gates new workspace creation on a one-time grant to that email's
domain, has core create the workspace and default agent, and returns the bearer consumed by the
`ufo` surface.

**Control's SQL reaches its own three tables and nothing else.** Its role is granted the
`ufo_control` schema and no privilege on any table in `public`, so every read and write of a core
table goes to core `serve` over `/internal/onboard/*` (RFC 0036). That keeps one home for what a
workspace is — `create_member`'s seat semantics, the balance ledger's invariants, the default
agent's prompt — where they already live.

## Commands

| Command | Role |
|---|---|
| `ufo-control gateway` | Serves `/ufo`, `/fleet`, `/login`, the WorkOS paths, and `/v1/onboard/{channel}`. |
| `ufo-control migrate` | Shapes the `ufo_control` schema — the ledgers below — as the database owner. |
| `ufo-control invite <email>` | Grants an email domain one new workspace and emails it the invitation. |
| `ufo-control slack-connect-retry <domain>` | Re-arms one failed signup Slack Connect delivery. |
| `ufo-control rls-bootstrap` | Creates the `ufo_serve` and `ufo_control` roles, the DBOS database, grants, and workspace policies. |
| `ufo-control serve-dsn <host> <database>` | Prints the DSN the serve role connects with, derived from the shared seed. |

## Source

| Path | Owns |
|---|---|
| `src/gateway.rs` | HTTP routes and the onboarding state machine. |
| `src/claim.rs` | Claim time-to-live, verification, and the race each write can lose. |
| `src/workos.rs` | Magic Auth, the Google hop, and the signed state and cookie seals. |
| `src/web.rs`, `src/login.html` | The browser's renderer and the page it serves. |
| `src/shared.rs` | The onboarding RPC client — every core table is reached through it. |
| `src/invite.rs` | One-time domain-grant custody. |
| `src/store.rs` | The platform onboarding ledger. |
| `src/slack_connect.rs` | The signup Slack Connect channel and its delivery poller. |
| `src/email.rs` | Work-email policy and SES delivery. |
| `src/schema.rs` | The `ufo_control` schema, shaped by the deploy; the gateway only requires it. |
| `src/rls.rs` | Shared database role and policy bootstrap. |
| `src/db.rs` | The pool over control's own three ledgers. |
| `src/token.rs` | Bearer minting; the ufo surface owns verification. |
| `src/directives.rs` | The directive wire both renderers read. |
| `src/client/ufo` | The POSIX installer the gateway serves at `/ufo`. |

## Required environment

| Variable | Consumer |
|---|---|
| `UFO_CONTROL_GATEWAY_DSN` | The gateway's own three ledgers. |
| `UFO_CONTROL_SERVE_INTERNAL_URL` | Where the onboarding RPC is reached. |
| `UFO_ONBOARD_CONTROL_TOKEN` | Gates `/internal/onboard/*`; core `serve` holds the same value. |
| `UFO_CONTROL_POSTGRES_OWNER_DSN` | `migrate` and `rls-bootstrap` only — the deploy Jobs, never the gateway pod. |
| `UFO_CONTROL_PG_ROLE_SEED` | Deterministic `ufo_serve` and `ufo_control` passwords. |
| `UFO_WORKSPACE_BASE_URL` | Workspace URL returned after sign-in. |
| `UFO_TOKEN_SECRET` | Member bearer signing. |
| `UFO_PUBLIC_BASE_URL` | URL stamped into the terminal installer. |

`UFO_CONTROL_PG_CA_BUNDLE` adds the roots a managed Postgres is signed by; TLS otherwise follows the
DSN's own `sslmode`, which defaults to `prefer`.

`invite` and `slack-connect-retry` are the operator verbs, run by exec'ing into a gateway pod — so
they read `UFO_CONTROL_GATEWAY_DSN` like the gateway does, not the owner DSN. Each writes one row of
one `ufo_control` table, which is exactly what that role is granted, so the pod that serves sign-in
still holds no credential reaching a tenant table.

Email delivery requires `UFO_SES_SENDER`, `AWS_ROLE_ARN`, and `AWS_WEB_IDENTITY_TOKEN_FILE`;
`UFO_SES_REGION` defaults to `us-east-1`. Only `ufo-control invite` sends mail — WorkOS delivers the
sign-in code — so the gateway process itself never does. `UFO_CONTROL_EMAIL_MODE=console` logs the
invitation instead, for a local stack with no SES account.

`UFO_INVITE_REQUIRED` defaults to `true`: creating a new workspace demands a live grant for the
member's verified email domain. The local hosted stack (root README) sets it `false` so signup needs
no grant; unset means required, so a deploy never opens signup by forgetting the knob.

## Validation

```bash
make control-pg          # a Postgres on :5549 for the suites that need one
make test-control        # cargo test
make check-control       # cargo fmt --check && cargo clippy --all-targets -- -D warnings
```

The ledger, RLS, gateway, and Slack suites run against that Postgres, each test in a database of its
own: the invariants they cover are partial unique indexes, row locks, `for update skip locked`
leases, and grant fences, none of which a fake reproduces.
