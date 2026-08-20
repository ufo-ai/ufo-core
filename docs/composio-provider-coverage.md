# Composio provider coverage

Which Composio toolkits this deploy can actually broker, and for those it can, whether the tools
reach the service's core jobs. Audited 2026-07-24 against Composio's live catalog (`api/v3.1`).

## Nothing is registered — read this first

No Composio provider is registered anywhere. `ComposioResolver` is an open namespace: it claims a
slug on demand, so **every toolkit in Composio's live catalog is a candidate** and the deploy names
none of them. The one exception is `client.CONNECTORS`, which holds `github` alone — the CLI
exception that needs a real provider host for `GH_TOKEN` forwarding.

Two other lists are easy to mistake for a provider registry, and neither is one:

| List | What it actually is | Size |
|---|---|---|
| `composio.client.CONNECTORS` | the only explicitly registered `ConnectorProvider`s | 1 (`github`) |
| `sources.registry.CONNECTORS` | hand-written **feed-sync** connectors — a `SourceProvider` + a `CredentialSlot` each, for pulling records into pages. Not connect-time providers; their slugs were merely renamed to match Composio's so the sync runner could route credentials to the broker | 48 |
| Composio's `/toolkits` | what a member can actually ask to connect | 1052 |

So the connectability gate below does not operate on a registered list. It changes what the open
namespace will claim, and its blast radius is the whole catalog:

**121 of 1052 toolkits carry managed credentials and tools. Of those, 22 are banned — so 99 are
offered, and 953 refused.**

Two independent gates, because the failures are different:

| Gate | Refuses | Why |
|---|---|---|
| `connectable` credential check | 931 | Composio holds no managed credentials, so the consent leg cannot mint a working link |
| `BANNED` banlist | 22 | connects fine, but withheld by judgement (see below) |

All 121 connectable toolkits were audited for core-job coverage — not just the 48 the sources
registry ships connectors for. Thirteen of those 48 fall in the refused 931 and are listed below;
that is an intersection, not the scope of the change.

## What the audit measured

| Axis | Source | Decisive? |
|---|---|---|
| Toolkit exists | `GET /toolkits/{slug}` status | yes — 404 means no such toolkit |
| Composio holds managed credentials | `composio_managed_auth_schemes` | yes — verified by live `POST /auth_configs` |
| Toolkit catalogs tools | `meta.tools_count` | yes |
| Managed OAuth grant reaches each tool | `composio_managed_auth[].scopes.available` vs each tool's `scope_requirements` | **only when the two name scopes in the same namespace** (see below) |
| Core-job coverage | per-tool descriptions judged against the jobs a member asks in chat | judgement |

The consent leg rides Composio-managed credentials: `ComposioClient._auth_config` reuses the
project's existing auth config and otherwise creates one with `use_composio_managed_auth`. For a
toolkit Composio holds no managed credentials for, that create fails outright —

```
POST /auth_configs {"toolkit":{"slug":"xero"},"auth_config":{"type":"use_composio_managed_auth"}}
400 Auth_Config_DefaultAuthConfigNotFound
  Default auth config not found for toolkit "xero". Composio does not have managed credentials
  for this toolkit.
```

— so the member's connect attempt dies after the link is minted. `ComposioResolver.claims` now
requires connectability, so these fail loud at the connect request instead, and `catalog` filters
them out so discovery never names a service the member cannot then connect. The predicate is read
off the live catalog record, so it covers all 1052 toolkits rather than a hand-kept blocklist.

### Where the scope axis is not evidence

Comparing granted scopes to each tool's declared requirement only means something when both sides
name scopes from the same vocabulary. Two failure modes make it useless:

- **Namespace mismatch** — granted and required share *no* scope name, so every tool with a declared
  requirement looks blocked. PagerDuty grants `read`/`write` while its 112 required names are all
  granular (`incidents.read`, `services.write`); intersection is empty. Intercom likewise: 31 granted,
  23 required, zero overlap. Their "blocked" counts are artifacts of stale Composio metadata, not gaps.
- **Unstated implication** — Stripe grants `read_write`, which subsumes the `read_only` that 116
  tools ask for. Treating the strings as opaque wrongly flagged them.

Both were corrected before the verdicts below. A third limit is under-detection: 54 of Square's 122
tools declare no scope requirement at all, so a per-tool comparison cannot see that Square's payments
surface is unreachable — only reading the granted set does. **The scope axis is therefore a lead, never
a verdict.** No code gates on it, and it must not: the gate that landed keys on managed credentials
and tool count, both unambiguous.

### What the gate also hides: 15 no-auth toolkits

Fifteen of the refused 931 declare `auth_schemes: ["NO_AUTH"]` and need no credentials at all —
`hackernews`, `yelp`, `seat_geek`, `weathermap`, `text_to_pdf`, `codeinterpreter`, `composio_search`,
`deepwiki_mcp`, `instacart` and six more. Requiring managed credentials is the wrong test for them.

They were already unusable, so this is not a regression: `call_external_tool` resolves an account
through `ToolContext.connector_account`, which raises when the agent holds no grant, and a grant can
only come from the connect flow, which needs an auth config Composio will not create. The gate stops
discovery from advertising them, which is strictly better than surfacing a service that then fails
twice.

It does mean a no-auth toolkit is now invisible rather than one change away from working. Making
these usable means letting a grantless execute through for a no-auth toolkit — a separate change with
its own authorization question, not folded in here.

## Refused: the thirteen we ship connectors for

Of the 931 the gate refuses, these thirteen are ones `sources.registry` ships a sync connector for.
No judgement involved — each is refused by Composio itself.

| Provider | Why |
|---|---|
| `chargebee`, `recurly` | `GET /toolkits/{slug}` → 404, and a catalog search finds no equivalent under another slug — Composio does not broker these services |
| `facebook_ads` | 404; the nearest ads toolkit, `metaads`, has no managed credentials either, so renaming does not help (`facebook` does have them but is pages/social, not ads) |
| `active_campaign`, `ashby`, `bamboohr`, `brex`, `freshdesk`, `klaviyo`, `recruitee`, `xero` | no managed credentials — connect dies at `POST /auth_configs` |
| `deel`, `rippling` | no managed credentials *and* zero tools catalogued |

## Core-job coverage: the 35 we ship connectors for and can connect

The remaining 35 of the 48 sources slugs. They are a subset of the 121 connectable toolkits — the rest
of that 121 is unaudited, since we ship no connector for them. `tools` is the catalogued count.
Verdicts rest on tool presence plus, where the scope axis is reliable, reachability.

| Provider | Tools | Verdict | Gap |
|---|---|---|---|
| `airtable` | 26 | healthy | — |
| `asana` | 153 | healthy | — |
| `calendly` | 56 | healthy | — |
| `github` | 893 | healthy | `GITHUB_SEARCH_CODE` blocked; `GITHUB_SEARCH_CODE_ALL_PAGES` serves it |
| `gmail` | 63 | healthy | cannot *change* filters/vacation/send-as (read only) |
| `googleads` | 22 | healthy | reporting only via `GOOGLEADS_SEARCH_STREAM_GAQL` (as in the real API) |
| `googledocs` | 43 | healthy | — |
| `googledrive` | 90 | healthy | >5 MB uploads need stateful `GOOGLEDRIVE_RESUMABLE_UPLOAD` |
| `googlemeet` | 15 | healthy | — |
| `googlesheets` | 53 | healthy | — |
| `intercom` | 133 | healthy | scope axis unreliable; conversation read/reply/assign/close + ticket CRUD all present |
| `jira` | 102 | healthy | — |
| `mailchimp` | 275 | healthy | Conversations/Inbox deprecated with no replacement |
| `monday` | 125 | healthy | typed tools, not raw GraphQL; complex columns need `MONDAY_COLUMNS` first |
| `notion` | 48 | healthy | cannot start a new inline block comment |
| `quickbooks` | 114 | healthy | no tool emails an invoice — `QUICKBOOKS_GET_INVOICE_PDF` only |
| `salesforce` | 223 | healthy | `full` scope also exposes destructive admin tools — worth a prompt guardrail |
| `sentry` | 211 | healthy | — |
| `slack` | 167 | healthy | Canvas, custom emoji, Enterprise Grid admin blocked |
| `stripe` | 432 | healthy | 8 narrow tools blocked, each with a reachable synonym |
| `typeform` | 35 | healthy | video upload is a two-step signed-URL flow |
| `wrike` | 144 | healthy | — |
| `zendesk` | 452 | healthy | macro *apply* tools are dry-run previews only |
| `clickup` | 164 | healthy | guest/user management is Enterprise-plan gated |
| `googlecalendar` | 49 | partial | **no RSVP** — no accept/decline tool exists in the toolkit |
| `outlook` | 305 | partial | cannot enumerate a conversation's messages; Microsoft To Do unreachable |
| `microsoft_teams` | 169 | partial | no emoji reaction tool; presence, meeting transcripts, file search blocked |
| `confluence` | 69 | partial | **cannot create a page** (`CONFLUENCE_CREATE_PAGE` needs `read:space`, only `read:space-details` granted) and cannot list spaces |
| `linear` | 47 | partial | no structured issue filter — `LINEAR_LIST_LINEAR_ISSUES` filters only project/assignee; no file attach |
| `hubspot` | 245 | partial | no call-logging tool exists; every email-engagement tool blocked |
| `greenhouse` | 134 | partial | cannot advance or reject a candidate, or add a note |
| `instagram` | 36 | partial | publish + DM only — cannot list own media, read post insights, or read/reply to comments |
| `attio` | 108 | **unusable** | `record_permission:read` granted, every write needs `record_permission:read-write` — read-only CRM |
| `square` | 122 | **unusable** | only `CUSTOMERS_*` + `MERCHANT_PROFILE_*` granted; no payments, orders, invoices, catalog, inventory |
| `pagerduty` | 368 | unverified | scope metadata mismatched (see above) — needs one live call on a connected account to settle |

## Banned: 22 of the 121

Each connects and catalogs plenty of tools. Twenty-one still fail a member's first real request;
`googlesuper` works but is withheld for a different reason, below.

The test is the service's *defining* verb — a retrieval product (analytics, search, reference data)
is not hobbled for lacking writes, which is why `google_analytics`, `stack_exchange`, `ticketmaster`,
`strava`, `gong` and `hugging_face` are all kept. Nor is a partial capability banned when it still
carries a real job on its own: Instagram and LinkedIn cannot read their own post performance, but
publishing works and a content drip is worth having.

Two shapes of gap. **The grant omits the defining verb:**

| Toolkit | Granted | Missing |
|---|---|---|
| `attio` | `record_permission:read` | every record write needs `…:read-write` — a read-only CRM |
| `square` | `CUSTOMERS_*`, `MERCHANT_PROFILE_*` | no payments, orders, invoices, catalog, inventory |
| `confluence` | `read:space-details` | `read:space`, so no page creation and no space listing |
| `greenhouse` | 30 `harvest:*` | `applications:move`, `applications:reject`, `notes:create` |
| `googlephotos` | `…appcreateddata` only | the member's own library (a Google platform restriction) |
| `digital_ocean` | `read` | any write, so nothing can be provisioned |
| `yandex` | `login:*` identity | every product scope; Disk reads only others' public files |

**Composio never shipped a tool for it:**

| Toolkit | Has | Missing |
|---|---|---|
| `gorgias` | ticket CRUD, tags | any tool that posts a reply into a ticket |
| `zoho_desk` | read threads | any tool that answers the customer |
| `ynab` | full budget read | enter or categorise a transaction (only scheduled ones) |
| `freshbooks` | clients, projects | invoices, payments, expenses |
| `omnisend` | contacts, `LIST_CAMPAIGNS` | create or send a campaign |
| `dynamics365` | create contact/account | *read* a contact, account or opportunity |
| `google_classroom` | list submissions | enter or return a grade |
| `apaleo` | property/unit setup | reservations, availability, rates |
| `servicem8` | create a job | update it afterwards, or create its customer |
| `boldsign` | send, list | retrieve the signed document |
| `blackbaud` | 5 tools | any constituent tool; its gift write needs an uncreatable batch |
| `mural` | add a sticky note | create a board, or edit/move/delete anything |
| `discord` | identity, invites | any message or channel tool (that is `discordbot`) |
| `linear` | create/update/comment | server-side filter by issue state or label |

**`googlesuper` is banned as a duplicate surface, not a hobbled one.** Its 429 tools restate Gmail,
Drive, Calendar and Meet under one slug, with per-product reachability that disagrees with the
dedicated toolkits — its `CREATE_MEET` is unreachable while `googlemeet` works. Two differently
named toolkits claiming one job is a coin-flip for the agent's tool search, so the namespace offers
only the dedicated ones.

Two entries deserve their dissent recorded. **`linear`** can filter by project and assignee and
offers raw GraphQL via `LINEAR_RUN_QUERY_OR_MUTATION`, so "open bugs" is reachable by schema
introspection — banned on the judgement that a common request should not need hand-written GraphQL.
Linear connects through the Pipedream allowlist instead, whose `linear-search-issues` and
`linear-list-workflow-states` actions reach issue state directly; the explicit registration wins
before this namespace is consulted, so the ban here stands untouched.
Note `wrike` and `basecamp` are kept while lacking an assignee filter, which is the same class of
gap one notch less severe. **`boldsign`** is the weakest: send and list work, and `DOCUMENT_LIST`
may expose a download URL — unverified.

### Not banned, on review

Four verdicts were retracted after checking the evidence rather than the claim: `pagerduty` and
`intercom` (scope-namespace mismatch — their flags are drift, and every incident/conversation tool
exists), `stripe` (`read_write` subsumes the `read_only` 116 tools declare), and `dialpad` (its tools
declare no scope requirement at all, so the ban was inference from scope *names*; calling, SMS, call
info and transcripts all have tools).

`figma` is kept: its REST API has never supported editing design content — that is Plugin-API-only —
so read, image export and comment is the most any server integration could do, and all three work.

`instagram` and `linkedin` are kept despite publishing blind. Neither can read its own post
performance (Instagram's own-media, insights and comment tools need ungranted legacy `instagram_basic`
/`pages_*` scopes; LinkedIn's `GET_POST_CONTENT`, `GET_SHARE_STATS` and `LIST_REACTIONS` need
ungranted `r_*_social`), so "how did my last post do" fails. Publishing is reachable on both
(`INSTAGRAM_POST_IG_USER_MEDIA_PUBLISH`, `LINKEDIN_CREATE_LINKED_IN_POST`), which is enough to run a
content drip — the job they are actually connected for.

## The remedy for a scope-limited provider is a custom auth config, not removal

`_auth_config` prefers the project's existing config over a managed one. So a toolkit banned for a
narrow grant is un-bannable by registering our own OAuth client in Composio with the scopes its tools
need, then deleting its `BANNED` line — no other code change. That path exists for `attio` (add
`record_permission:read-write`, `note:read-write`), `square` (the payments/orders/invoices scopes),
`confluence` (`read:space`), `greenhouse`, `instagram`, `linkedin` and `digital_ocean` (`read write`).

It does **not** exist for the missing-tool bans, which need Composio to ship a tool, nor for
`googlephotos`, where Google itself restricts the API to app-created media.

## BYOK sync: routed on the account handle

The thirteen above have no Composio grant to sync through, so their only path is a member-added key
through the `direct` auth proxy. `ConnectorRegistry.credential` picks the backend from the source's
**account handle**: `DIRECT_ACCOUNT` — what registration stores when the member set the provider's
credential instead of connecting an account — resolves through the deploy's fallback backend, and a
connected-account id resolves through its provider's broker. The provider name cannot carry that
decision, because `ComposioResolver.entry` claims every slug; routing on the name alone sent every
keyed source to Composio, which holds no account for it, and failed every run.

`claims` is deliberately not the signal: it is the connectability/ban gate, so a scope-limited ban
(`attio`) would fall through to BYOK correctly while a missing-tool ban (`gorgias`) would fall
through wrongly. The handle says what the source actually holds.

Twelve of the thirteen sync on a raw key as-is — each connector adapts it to the scheme its service
expects (`klaviyo`'s `Klaviyo-API-Key`, HTTP Basic for `freshdesk`, `ashby`, `bamboohr`, `chargebee`
and `recurly`, `active_campaign`'s `Api-Token`, a plain bearer for `brex`, `deel`, `rippling`,
`recruitee` and `facebook_ads`). **`xero` still cannot**: every call needs a `xero-tenant-id` header
naming one org inside the grant, and `XeroConnector` sets it only when constructed with a tenant —
the source factory constructs it with none. Xero also issues no static API key (OAuth 2.0 only, and a
custom-connection token expires in 30 minutes), so a BYOK slot is the wrong shape for it. It needs
per-connector work, not a routing change.
