# ufo-control

The hosted gateway and database bootstrap for ufo's shared workspace fleet.

One `ufoctl serve` fleet hosts every workspace. A signed bearer selects the workspace before the
request reaches core, and Postgres row-level security enforces the same scope on every transaction.
The gateway verifies a work email, gates new workspace creation on a one-time grant to that email's
domain, creates the workspace and default agent, and returns the bearer consumed by the `ufo`
surface.

## Commands

| Command | Role |
|---|---|
| `ufo-control gateway` | Serves `/ufo`, `/fleet`, and `/v1/onboard/{channel}`. |
| `ufo-control migrate` | Shapes the `ufo_control` schema — the ledgers below — as the database owner. |
| `ufo-control invite <object-number> <email>` | Grants a waitlist object's email domain one new workspace and emails it the invitation. |
| `ufo-control rls-bootstrap` | Creates the `ufo_serve` role, its DBOS database, grants, and workspace policies. |

## Source

| Path | Owns |
|---|---|
| `gateway.py` | HTTP routes and the onboarding workflow. |
| `gateway_claim.py` | Email-code expiry, hashing, and attempt limits. |
| `gateway_email.py` | Work-email policy and SES delivery. |
| `gateway_invite.py` | One-time domain-grant custody. |
| `gateway_shared.py` | Workspace, member, and default-agent writes. |
| `gateway_store.py` | The platform onboarding ledger. |
| `gateway_token.py` | Bearer minting; the ufo surface owns verification. |
| `rls.py` | Shared database role and policy bootstrap. |
| `schema.py` | The `ufo_control` schema, shaped by the deploy; the gateway only requires it. |
| `client/ufo` | The POSIX terminal client served by the gateway. |

## Required environment

| Variable | Consumer |
|---|---|
| `UFO_CONTROL_POSTGRES_OWNER_DSN` | Gateway ledger, invites, and RLS bootstrap. |
| `UFO_CONTROL_SERVE_DSN` | Workspace-scoped gateway writes. |
| `UFO_CONTROL_PG_ROLE_SEED` | Deterministic `ufo_serve` password. |
| `UFO_WORKSPACE_BASE_URL` | Workspace URL returned after sign-in. |
| `UFO_TOKEN_SECRET` | Member bearer signing. |
| `UFO_PUBLIC_BASE_URL` | URL stamped into the terminal client. |

Email delivery requires `UFO_SES_SENDER`, `AWS_ROLE_ARN`, and
`AWS_WEB_IDENTITY_TOKEN_FILE`; `UFO_SES_REGION` defaults to `us-east-1`. Both the verification code
and the invitation ride it, so `ufo-control invite` runs where the gateway runs — its IRSA identity
reaches an `exec`'d process. `UFO_CONTROL_EMAIL_MODE=console` logs either message instead, for a
local stack with no SES account.

`UFO_INVITE_REQUIRED` defaults to `true`: creating a new workspace demands a live grant for the
member's verified email domain. The local hosted stack (root README) sets it `false` so signup needs
no grant; unset means required, so a deploy never opens signup by forgetting the knob.

## Validation

```bash
uv run --project control ruff check control/src control/tests
uv run --project control mypy
uv run --project control pytest control/tests --ignore=control/tests/test_rls.py
```

The RLS integration suite runs against the CI Postgres on port 5544:

```bash
UFO_RLS_REQUIRED=1 uv run --project control pytest control/tests/test_rls.py
```
