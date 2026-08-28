# Keyed connectors

Providers a broker cannot front, reached with a workspace API key instead. A broker (Composio,
Pipedream) can only serve a provider whose OAuth consent it hosts — `connectable()` admits a Composio
toolkit only when `composio_managed_auth_schemes` is non-empty. Everything authenticated by
`API_KEY` / `BEARER_TOKEN` / `BASIC` has no managed consent: ~1,282 of Composio's 1,403 toolkits, and
by sample ~74% of Pipedream's ~3,334 apps.

Those become connectable through the `keyed_connectors` extension: a table row per provider, whose
secrets live in **our** credential store under RLS, injected at the egress proxy. No third-party
vault, and nothing that depends on Composio's managed-OAuth connected-accounts endpoint (retiring for
all orgs 2026-07-03).

## Adding one is a row

`extensions/keyed_connectors/ufo_ext_keyed_connectors.py`, `KEYED_PROVIDERS`:

| Field | What it is |
|---|---|
| `provider`, `label` | the slug the slots are named from, and the member-facing name |
| `host` | the provider's API host, when it is the same for every account |
| `secrets` | one `KeyedSecret` per key the API takes: slot suffix, wire header, sandbox env var, what the member is asked for, and the auth `scheme` prefixing the value where the API takes one |
| `sites` | for a provider that pins its host per account: the closed set of hosts it publishes, of which the member selects one (the first is the default). Declared instead of `host` |
| `host_env`, `site_description` | required with `sites`: the env var carrying the selected host into the sandbox, and what the member is asked to choose |

Everything else derives: slot names, sentinels, the companion slot for a site choice, proxy
scope/injection/meter rules, the sandbox env, and the prompt section's usage line. Adding a provider
needs no new code and no new test — the extension's tests read the derived declaration. Collecting
the slots refuses a shared sentinel, two claims on one sandbox variable carrying different values,
and a choice naming a companion slot nothing declares, in both the serve and proxy processes.

A `scheme` names one of `Bearer`, `Token` or `API-Key` (PandaDoc's `Authorization: API-Key <key>`),
which is the closed set the egress proxy re-prefixes the real secret with (`SWAPPABLE_SCHEMES`, held
in both the row and `servers/egress`). A row naming any other scheme fails at import, because its
sentinel would ride to the provider unswapped.

A member never types a hostname: the selection resolves to one of the declared literals or to
nothing, so what the proxy scopes and injects on is always a string the row wrote. A provider whose
host is an *open* set instead of a published one — a self-hosted PostHog, a Supabase project ref —
needs a form that builds the host from a member-supplied label under a fixed domain, and lands with
its first provider.

## Registered rows

| Provider | Host | Key on the wire |
|---|---|---|
| `datadog` | one of nine published sites | `DD-API-KEY` plus `DD-APPLICATION-KEY` |
| `posthog` | `us.posthog.com` or `eu.posthog.com` | `Authorization: Bearer <key>` |
| `mercury` | `api.mercury.com` | `Authorization: Bearer <token>` |
| `apollo` | `api.apollo.io` | `X-Api-Key` |
| `pandadoc` | `api.pandadoc.com` | `Authorization: API-Key <key>` |

Mercury, Apollo and PandaDoc issue no managed OAuth either broker can host: Composio asks for your
own credentials for all three, Mercury and Apollo's search surface are key-only on Pipedream too, and
PandaDoc's API key reaches the same documents/templates/contacts surface as its OAuth app. Apollo
gates sequences, tasks and deals behind a master key, and Mercury gates payment initiation behind an
approved-partner token — both are the provider's own limit on the key, not this deploy's. PandaDoc
also holds a Pipedream allowlist entry, so a member may connect it either way.

The same three keys now also feed sync. `sources.registry` ships a `mercury`, `apollo` and
`pandadoc` feed-sync connector, and a source registered with the `DIRECT_ACCOUNT` handle resolves
its key through `DirectAuthProxy`, which reads the slot named for the provider — `mercury`,
`apollo`, `pandadoc` — and hands the connector a raw key. That slot is the sources extension's own,
separate from the row's `<provider>_api_key` slot the egress proxy injects for a sandbox turn, so a
member who wants both surfaces fills both slots. Each connector re-applies the scheme its row names
(a plain bearer for Mercury, `X-Api-Key` for Apollo, `API-Key` for PandaDoc), because a synced run
calls the provider host-side and never passes the injecting proxy. PandaDoc's second path also
carries over: a PandaDoc source registered against its Pipedream connection syncs through the
broker's transport instead, with no key at rest.

## How a member connects one

In chat, like everything else. The agent runs the credential collection's `request_credentials`
action for the provider's slots; the
owner enters each value privately (never in the transcript); the agent then calls the provider's REST
API from the sandbox with the exported env vars. The sandbox holds sentinels — the proxy swaps the
real secret onto the wire for a live turn only, and meters the host under `requests`.

Slot state is visible as the `credential` object kind: `list` shows each slot filled or empty, a read
shows the hosts a site choice offers, and its status reports the host this workspace's key actually
rides to — resolved through the same resolver the proxy and the sandbox export use, so a filled slot
whose selection the row does not offer reads as no host rather than as the default.

## Not expressible

| Shape | Why | Examples |
|---|---|---|
| An open host set | a published list can be declared; an arbitrary customer domain has to be *built* from a member-supplied label under a fixed domain | self-hosted PostHog, Supabase `<ref>.supabase.co`, Databricks workspaces |
| Auth signed over the request | not a header swap | AWS SigV4, GCP |
| Basic composed from two stored values | one header derived from two secrets needs a composer | Twilio (SID + token), Mixpanel (service account) |
| An auth scheme outside `SWAPPABLE_SCHEMES` | the proxy keeps a prefix it recognises; anything else reaches the wire holding the sentinel | a `Basic <key>` provider |
| OAuth with the workspace's own client | consent leg, not a key | Intercom |

## Provider inventories

Mined from the authoritative catalogs; both tables live on
[#666](https://github.com/metalcraftai/ufo/issues/666):

| Source | Method | Scale |
|---|---|---|
| [Pipedream](https://github.com/PipedreamHQ/pipedream) | Git Tree API over `components/*/*.app.mjs`, classified by `this.$auth.*` | ~3,334 apps; 37 of a 50-app sample are API-key |
| [Composio](https://docs.composio.dev/toolkits) | toolkit catalog + `managed-auth` list + `api/v3.1/toolkits/{slug}` | 1,403 toolkits, 121 managed-OAuth |

Both brokers' non-managed sets overlap heavily — Datadog, PostHog, Tavily, Perplexity, SerpAPI,
SendGrid, New Relic, OpsGenie, Pinecone, Brevo, Mailgun, Resend, ElevenLabs, Cohere, Groq,
Firecrawl, Apify. Region/host fields to watch when adding one: PostHog `instance_url`, OpsGenie
`instance_region`, Amplitude/Mailgun `region`, Supabase `subdomain`, Databricks `domain`, Datadog
`site`.
