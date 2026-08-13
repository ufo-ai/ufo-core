---
rfc: 0027
title: "WorkOS sign-in — the gateway owns the email step, WorkOS verifies"
status: implemented
date: 2026-08-12
---

# WorkOS sign-in

> Replace the hosted gateway's hand-rolled email-code verifier with WorkOS: the gateway collects the
> work email on its own `/login` page and Magic Auth mails the code, with a `Continue with Google`
> button that hops to WorkOS with `provider=GoogleOAuth` and lands straight on Google. No hosted
> WorkOS page collects the address, so the work-email policy runs before any code is sent. WorkOS
> answers exactly one question — does this person control this email — and everything downstream of
> the answer (workspace resolution, invite gate, HMAC bearer, cookie, seats, roles) is untouched.
> Motive: Google sign-in and managed code delivery at launch, enterprise SSO after it, without
> owning an auth stack.

## Current state

Sign-in is one state machine, `Onboarding.advance` (`control/src/ufo_control/gateway.py:90`),
driven by two renderers over `POST /v1/onboard/{web,terminal}`: the self-contained `/login` page
(`gateway_web.py`) and the terminal client's directive protocol. The email-verification step is
ours end to end: `ClaimWorkflow` mints `secrets.randbelow(10**6)` (`gateway_claim.py:40`), stores
only the sha256 (`:27`), caps attempts at 5 (`:18`) under an optimistic-concurrency CAS, and
`SesEmailSender` delivers it over a hand-rolled SigV4 signer (`gateway_email.py:184`). A verified
claim resolves a workspace (`gateway_shared.py`), passes the invite gate (`gateway_invite.py`),
and mints the 30-day stateless HMAC bearer (`gateway_token.py` → `core/src/ufo/bearer.py:33`)
that is the `ufo_session` cookie, the CLI credential, and the terminal's `Authorization: Bearer`,
verbatim. Self-hosted deploys have no gateway: `ufoctl init` mints the token directly
(`core/src/ufo/cli.py:150`).

The gap: email-code is the only method — no Google, no passkeys, no MFA, no SSO — and code
delivery, the work-email denylist, and abuse handling are all ours to run. `WORKOS_API_KEY` sits
in `.env` with no reader.

## Proposal

WorkOS is the verifier; the claim is the seam. `onboard_claim` keeps its role as the machine's
state, loses its code columns, and gains its verified stamp from WorkOS instead of a hash compare.
Only `control/` imports `workos`; core never does.

Self-hosted deploys run none of this. They have no gateway — no `/login`, no onboarding machine,
no email verification — so there is nothing for WorkOS to replace: sign-in stays `ufoctl init`
minting the bearer directly and `ufoctl portal` handing it to the browser. The WorkOS vars are
read only at gateway boot; `ufoctl serve` boots without them and never notices.

### Web

| # | Step |
|---|---|
| 1 | `GET /login` serves the card page; the browser collects the work email and the code inline over `POST /v1/onboard/web` — the same machine the terminal drives — and `Continue with Google` navigates top-level to `GET /v1/onboard/auth/start` with the `?c=`/`?a=` carry |
| 2 | The email step is `ClaimWorkflow.start`: it validates `WorkEmailPolicy` and, only then, calls `create_magic_auth` — so a denylisted address is refused with no code sent — and the code confirms through `authenticate_with_magic_auth` |
| 3 | `POST /v1/onboard/web` mints and seals the `__Host-ufo_onboard` cookie server-side (`HttpOnly`, `Secure`, `SameSite=lax`, `Path=/`, host-only by the `__Host-` prefix the browser keeps un-plantable across hosts, no `max_age`) whenever the presented cookie stands behind no live claim, so a claim is only started under a session minted here and keyed by it — bound to that browser from the submit that starts it, never keyed by a value the caller names or obtained by asking |
| 4 | `Continue with Google`: `start` mints and binds that same cookie, signs the session and carry into the OAuth `state` under an HMAC signature, and 302s to WorkOS with `provider=GoogleOAuth` — WorkOS goes straight to Google, no hosted page |
| 5 | `GET /v1/onboard/auth/callback?code&state`: require the signature and require the session the state names to equal the cookie, exchange via `AsyncWorkOSClient.user_management.authenticate_with_code`, apply `WorkEmailPolicy` to the returned email (fails closed, so a personal Google account is refused), stamp the claim verified, 303 back to `/login` with the carry; the page resumes `advance`, which sees a verified claim and continues — resolve, choose, invite gate, mint bearer, signed-in card POSTs the token to `/surface/web` |

`start` mints the session id and it never leaves the cookie — no query names it and no page reads
it — so nothing outside the browser that signed in can name the session a verified email is written
under. The state is signed under a subkey of the gateway's token secret, so the only session a state
can name is one `start` minted, and the cookie check then ties that state to the browser it was
minted for. The code is single-use at WorkOS. A denylisted email refuses through the machine's
existing refusal directive. Both routes live under `/v1/onboard/`, so `RESERVED_HOST_PREFIXES`
(`core/src/ufo/serve.py:142`) and the nginx map are untouched.

### Terminal

The directive protocol does not change; `ClaimWorkflow`'s internals swap:

| Step | Today | Proposed |
|---|---|---|
| start | mint + hash code, SES send | `create_magic_auth(email)` — WorkOS mints and emails a six-digit code (10-minute TTL) |
| verify | `hmac.compare_digest` + attempt CAS | `authenticate_with_magic_auth(code, email)` — WorkOS enforces expiry and attempts |

WorkOS failures map onto the machine's existing refusal directives; the member types the same
six digits in the same prompt.

### Deleted and added

| Deleted | Added |
|---|---|
| Code mint/hash/attempt-cap in `gateway_claim.py`, code columns on `onboard_claim` (same migration) | `auth/start` + `auth/callback` routes on the gateway |
| `verification_email` and its send path (`gateway_email.py:150`; invites keep SES) | `workos` dependency in `control/` only |
| The code-verification tests in `test_gateway_claim.py` (the invite-grant tests stay) | `WORKOS_API_KEY`, `WORKOS_CLIENT_ID`, `WORKOS_REDIRECT_URI` read at gateway boot, failing loud without all three |

### Sessions stay stateless — deliberately

AuthKit's sealed-session cookie is not adopted. The terminal and CLI hold the bearer as an API
key, so a cookie-shaped session can never be the only credential, and per-request
`session.authenticate()`/refresh couples every portal request to WorkOS availability. WorkOS
answers once, at sign-in; the bearer's ~8 verify sites, the sites CSRF derivation, the artifact
`?a=` refresh, and `open_session` are unchanged.

### Config and infra

`WORKOS_API_KEY` + `WORKOS_CLIENT_ID` are a gateway-only secret,
`ufo/<env>/gateway-workos`, created and seeded out-of-band once per environment before its first
deploy; `infra/modules/platform/secrets.tf` reads it through a `data` source rather than owning it,
so terraform never writes a placeholder the gateway would boot on and the seed always precedes the
rollout. `WORKOS_REDIRECT_URI` rides beside them as a plain value —
stated per deploy rather than derived from a host, because the callback's origin is the app host
hosted and the gateway's own port under compose. The WorkOS dashboard registers that same URI per
environment, staging keys (`sk_test_`) to testing and production keys to prod. Dashboard setup — Google + Magic Auth as the only methods, a custom
sending domain, and email templates rewritten to the copy doctrine — ships with the unit that
needs it. WorkOS calls get bounded retry: external uncertainty, the legitimate case.

Seed the secret once, before the environment's first deploy (`<env>` is `ufo-testing` or `prod`):

```
aws secretsmanager create-secret --region us-east-1 --name ufo/<env>/gateway-workos \
  --secret-string '{"api-key":"<workos key>","client-id":"<workos client id>"}'
```

`WORKOS_MODE` selects the verifier: the default `workos` requires the three values at boot, and
`console` (the compose default) runs a credential-free dev verifier so `docker compose up` needs no
keys — the browser hop lands on a local email form the gateway serves in place of AuthKit, and the
terminal code is logged. It is an explicit mode, never inferred from missing keys, so a
misconfigured deploy still fails loud rather than silently faking sign-in.

### SSO, after launch

The reason WorkOS rather than another code sender: enterprise SSO arrives as home-realm discovery
on the email step. Because the gateway now owns that step (`Onboarding._collect_email`), the branch
has one home: a submitted address whose domain has a WorkOS SSO connection redirects to that
organization's `authorize` with `organization_id` instead of calling Magic Auth, and the callback
and everything downstream are unchanged. A workspace's domain — today derived as the first member's
email domain (`core/src/ufo/seats.py:340`) — maps to a WorkOS Organization with a verified domain.
Nothing of it lands now: no discovery call, no `workos_user_id` column, no organization sync — a
column without a reader fails both-ends — but the email-submit path is structured so the branch
slots in ahead of the code send.

## Doctrine fit / implications

- Core untouched. WorkOS is hosted-control-plane plumbing in `control/`, the component that
  exists only for the hosted deploy; self-hosted keeps `ufoctl init`.
- The callback is the sanctioned third-party plumbing carve-out; no new member-facing endpoint.
- Async-native via `AsyncWorkOSClient`; the gateway's one event loop never blocks on WorkOS.
- Fail loud: boot refuses to start half-configured, a denylisted email refuses at the callback.
- Tests: a fake WorkOS client stands in as the dependency, never the thing asserted; the gmail walk
  pins that a denylisted address is refused with WorkOS's `begin` never called (no code sent). The
  `/login` page stays self-contained — the Google hop is an HTTP redirect, never page content, so
  `test_login_page_is_self_contained_and_targets_the_web_wire` holds; the fixed-copy pins and the
  metaphor lexicon sweep govern the new strings.

## Alternatives

- **Sealed sessions end to end** — real logout and revocation, but two credential systems forever
  (the terminal keeps a bearer regardless) and WorkOS on the hot path of every portal request.
- **WorkOS Organizations as the member directory** — memberships, invitations, and roles synced
  from WorkOS. Fights chat-first member actions (`add_member` writes local rows) and buys nothing
  the launch needs; the SSO slice above arrives without it.
- **Keep the hand-rolled machine, add Google OAuth ourselves** — owns OAuth, MFA, SSO, and
  deliverability forever; the machine's irreplaceable parts (resolution, invite gate, bearer) are
  kept under this proposal anyway.

## Open decisions

- **Who sends the terminal code.** `create_magic_auth` returns the code, so we could deliver it
  over our SES path and keep one sender — but that keeps the verification-email machinery this
  RFC deletes. Recommended: WorkOS sends, from a custom domain, with templates rewritten to the
  copy doctrine.
- **The web page's first act.** Resolved: the page is the sign-in. It asks for the work email inline
  and offers `Continue with Google` beside it, so the member never leaves for a hosted page and the
  work-email policy runs before any code is sent.
