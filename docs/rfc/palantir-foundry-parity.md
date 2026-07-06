# Palantir AIP / Foundry parity (proposal)

Status: **proposal, not adopted.** Gap-analysis + spec against Palantir's three developer surfaces
(AIP Chatbot Studio, Foundry APIs, Developer Console applications). Nothing here is built; it exists
so the direction can be decided from a concrete shape. It touches `spec.md` (§Surfaces, §Workspace
model, §Extension system) and the `CLAUDE.md` "every member action happens in chat" rule.

Sources: Palantir docs (URLs at the bottom); selfhost citations are `path:line`.

## TL;DR

| Palantir surface | selfhost equivalent | Material gap |
|---|---|---|
| **Developer Console app** = a registered OAuth2 client (client-credentials service user + auth-code/PKCE) that lets an **external program** call the platform | An **Extension** = a pinned in-process Python package. Inbound HTTP auth resolves **only to a human `member`**; OAuth is **outbound-only** (deploy → provider). | **No inbound programmatic caller exists.** No app-client, client-credentials, scoped API token, or service principal. This is the single largest divergence. → **P0** |
| **Chatbots as Functions** = a named chatbot callable via the API (`userInput`+`sessionRid`→`markdownResponse`+`sessionRid`), streaming or blocking, with app-state I/O | Internal `TurnInvoker.invoke` seam (`ext/context.py:224`), conversation-as-session, `x-selfhost-agent` header on `POST /v1/chat` | The invoke primitive exists but has **no authenticated external entry** and **no typed I/O contract**. → **P0 + P1** |
| **Chatbot Studio** = visual builder for **many** chatbots: system instructions, tools, app variables, documents; test + eval + version + publish | **One** `assistant` agent per workspace (`agent` = `name`+`prompt`+`model`), seeded at `init`, prompt-only edits via governed proposal | No author path for a second agent; the `agent` record is far leaner than `spec.md` promises. → **P1** |
| **Ontology** (typed objects/links/actions/functions) + chatbot **Object query / Action / Function** tools over it | Freeform tools + a **text** RAG index (`source`→`page`→`chunk`, lexical+vector) | **No typed data/object/action layer.** Genuine net-new subsystem. → **non-goal for core; extension pattern.** |
| Full **OAuth2 authorization server** (client registration, 3-legged auth-code on behalf of a user, refresh rotation) | — | Big net-new. **Non-goal for core**; P0 scoped tokens cover the M2M 80%. |

---

## 1. What Palantir offers

### 1a. AIP Chatbot Studio + AIP agents

A visual studio to build **AIP Chatbots** (a.k.a. AIP Agents). A chatbot = **LLM + Ontology +
Documents + Tools + System instructions + Application state**. Authored in a UI, tested
interactively, evaluated with **AIP Evals**, versioned, and shared (marketplace).

| Tool type | Does | Governance |
|---|---|---|
| **Action** | Executes an **ontology edit** | Auto or **user-confirmation** |
| **Object query** | Reads specified object types: filter / aggregate / inspect / traverse links | Scoped to selected types + properties |
| **Function** | Calls any Foundry / AIP Logic function (pinned or latest) | — |
| **Update application variable** | Mutates chatbot app-state | — |
| **Command** | Triggers operations in other Palantir apps | — |
| **Request clarification** | Pauses to solicit user input | — |
| **Retrieval context** (legacy: semantic search) | Document sets + semantic search for grounding | Scoped to configured doc sets |

Tool-calling modes: **prompted** (one tool at a time, all models) vs **native** (parallel, specific
models). **Application state / variables** = a typed bag the caller seeds and tools update.

Deployment: **internal** (Workshop widgets), **external** (Ontology SDK + platform APIs), **as a
Function**, **marketplace**.

**Chatbots as Functions** — the key external contract. Publish a chatbot as a Foundry Function:

- Inputs: `userInput` (string, required), `sessionRid` (optional, `ri.aip-agents..session.{uuid}`,
  for multi-turn), application-variable overrides.
- Outputs: `markdownResponse`, `sessionRid`, updated application variables.
- Callable from Automate, Code Repositories, AIP Evals, and **external apps via the Foundry API**
  (streaming or blocking; create-session / get-history / execution-traces endpoints).

### 1b. Foundry APIs surfaced to chatbots/apps

The **Ontology** is Foundry's typed operational data layer: **object types, properties, link types,
action types, functions**. Generated language SDKs (OSDK, TS/Python) read **and write** it; Platform
APIs cover datasets, mediasets, functions, connectivity. Chatbot tools are thin bindings over this:
Object query → ontology read; Action → ontology edit; Function → Foundry function. REST is plain
Bearer: `curl -H "Authorization: Bearer <token>" https://<host>/api/v1/ontologies`.

### 1c. Developer Console applications (OAuth clients + scopes)

A Developer Console **application is a registered OAuth2 client** — the mechanism by which an
external program gets scoped API access. The platform is the **authorization server**.

| Grant | Audience | Mechanics |
|---|---|---|
| **Authorization Code + PKCE** | 3rd-party app acting **on behalf of a Foundry user** | `GET /multipass/api/oauth2/authorize` → consent (app icon/name) → code → `POST /multipass/api/oauth2/token`; pre-registered redirect URIs; `offline_access` → refresh token (rotated, reuse-detection, 30-day idle expiry) |
| **Client Credentials** ("Backend service") | Non-interactive **machine/service** | `client_id`+`client_secret` → token; Foundry **auto-creates a service user** (username = client id); a **Foundry admin assigns roles/permissions** to that service user |

**Scopes** compose from three axes: **Ontology SDK scope** (select object/action types → generated
SDK bindings; token restricted to selected entities), **Platform SDK scope** (add platform resources
+ allowed operations), and **client operations** (e.g. "AIP Chatbots write"). Scope strings look
like `api:use-ontologies-read` / `api:ontologies-write`; the `use-` prefix restricts a token to the
platform API and blocks underlying service endpoints. **Effective scope = intersection(app-max
scope, token-requested scope, principal's permissions).**

---

## 2. selfhost-core today

### 2a. Agent / chatbot model — leaner than the spec

| Aspect | Reality | Cite |
|---|---|---|
| `agent` record | `id, workspace_id, name, prompt, model` — name + system-prompt text + model slug. **No** tool list, skill set, memory scope, persona, or config JSON, despite `spec.md`'s §Workspace-model promise ("granted tool set, skill packs, memory scope"). | `schema/tables.py:37`; `schema/records.py:56` |
| Create | Only at `init`: exactly one agent `assistant`, prompt `"You are a helpful assistant."` — **the only `insert(agent)` in the codebase.** No CLI verb, no chat tool. | `onboarding.py:27,144` |
| Edit | Prompt-only, via governed proposal (CAS on `sha256(prompt)`), approved at `POST /v1/proposals/{id}/approve`. Model/name not editable. | `governance.py`; `records.py:82`; `cli.py:136` |
| System prompt | Assembled per turn: `shell.md` + `{{agent-prompt}}` + pack `{{sections}}` + `{{skill_index}}`. | `loop/prompts/render.py:47` |
| Tools | `ToolRegistry` (frozen tuple), 17 builtins (`bash/read/write/edit/glob/grep/share_file/spawn_subagent/load_sessions/ask_user/load_skill/connect_account/pause_and_wait/list_skills/wait/cancel/message_subagent`). Extensions add via `Manifest.tools`. Native tool-calling. `untrusted` flag walls a result as data. `ask_user`/`pause_and_wait` ≈ Request clarification; `connect_account` ≈ OAuth-tool. | `tools/registry.py:20`; `builtins.py:720` |
| Subagents | `SubagentProfile(name, prompt, tool_names subset, input_model, output_model, max_rounds, model)` — **typed I/O already exists here.** One core profile `general_purpose`; extensions register more. | `ext/manifest.py:335`; `loop/profiles.py:16` |
| Skills | SKILL.md folders; 2 core (`sandbox`, `delegation`); packs/extensions add. Not per-agent. | `skills/runtime.py:33,163` |
| Invoke seam | `TurnInvoker.invoke(conversation, agent, message, idempotency_key)` — "the one boundary that evaluates the spend cap," shared by surfaces, jobs, scheduled tasks, the eval harness. `ExtensionContext.invoke` wraps it. | `ext/surface.py:54`; `ext/context.py:224`; `surfaces/admission.py:36` |

**Gap:** no agent-authoring path, no typed top-level I/O, and the `agent` record is under-built vs
spec. The invoke primitive is the "chatbot as function" engine — but internal-only.

### 2b. Data layer — text RAG, no ontology

No object types, no typed schema, no object query, no typed governed "action." The only structured
surfaces are the memory/RAG pipeline (`source`→`page`→`chunk`→`IndexBackend`, lexical+vector,
retrieval-only, keyed by `owner_kind`+`subject`, injected via the `on_inbound` hook — not queried by
a tool) and connector calls (freeform third-party APIs, marked `untrusted`). Cites: `tables.py:239`
(`source`), `tables.py:277` (`page`), `indexing.py:67` (`IndexBackend`), `manifest.py:262`
(`InjectContext` recall hook).

### 2c. Developer-app / auth surface — inbound is human-only

**Headline: selfhost has no app / client-credentials / scoped-API-token concept for external
programmatic callers.** No `client_id`, `client_secret`, client-credentials grant, app registration,
service account, or M2M token anywhere in core. OAuth is **outbound-only**: the deploy is an OAuth
*client* to providers (Gmail/GitHub), never an authorization server issuing clients — the inverse of
Foundry.

Every inbound HTTP credential resolves to a human `member`:

| Path | Auth | Cite |
|---|---|---|
| `POST /v1/chat`, `/v1/turns/*`, `/v1/proposals/*` | CLI **Bearer token** → `surface_identity(surface="cli")` → member | `surfaces/cli.py:41`; `cli.py:106` |
| `/surface/<name>/…` (web) | session cookie → member (same digest scheme) | `extensions/web/.../surface.py:25` |
| `/surface/<name>/…` (slack) | HMAC request signature (webhook, not a caller identity) | `extensions/slack/.../surface.py:98` |
| `/v1/connect/callback` | Fernet-sealed **state**, *not* authenticated | `surfaces/cli.py:157` |
| `/artifacts/download` | HMAC-signed URL (one file, not an identity) | `surfaces/artifacts.py:25`; `artifact_token.py:41` |
| `/ext/<name>/…` | **no framework auth at all** — the extension's own concern | `serve.py:496,518` |

The "developer app" unit is an **Extension**: a pinned Python package returning a `Manifest`
(`ext/manifest.py:392`), loaded in-process at boot, digest-verified against a lockfile
(`ext/loader.py:198`), isolated by the static `selfhost.sdk` import gate (`gates.py:132`) + the
capability-scoped `ExtensionContext` (`ext/context.py:201`). Manifest points: `tools, subagents,
prompt_sections, skills, connectors, sources, triggers, hooks, jobs, routes, surfaces, credentials,
onboarding, models, carriers, indexes, embeds, hubs, auth_proxies, search_providers` (`spec.md`
§Extension system).

**Grants** bind an OAuth account to an **agent** via `/connect` in chat; the broker holds the token,
the grant carries only `account_id`+`host` (`grants.py:70,197`). The egress proxy derives
`ScopeRule`/`InjectionRule`/`MeterRule` from manifests + grants — default-deny, sentinel→key swap
for BYOK, grant hosts tunnelled opaquely and metered (`sandbox/proxy/rules.py:63`; `server.py:117`).

---

## 3. Gap catalog (prioritized)

| # | Gap | Fits existing seam? | Priority |
|---|---|---|---|
| G1 | No authenticated **inbound programmatic caller** (app-client / scoped token / service principal) | Mostly — `surfaces`, `surface_identity`, `admit`/`invoke`, `spend_cap` all exist; needs **one** new core concept (service principal) + a token/scope record | **P0** |
| G2 | No **agent-as-a-function** external entry (blocking/streaming invoke of a named agent) | Yes — an `api` surface on the `surfaces` seam over `TurnInvoker`, once G1 lands | **P0** |
| G3 | No **typed I/O / app-state** for a top-level invoke | Yes — lift the subagent `input_model`/`output_model` pattern to the invoke boundary | **P1** |
| G4 | No **multi-agent authoring in chat**; `agent` record under-built vs spec | Yes — owner-gated `create_agent`/`configure_agent` tools through the governed proposal flow; enrich the `agent` migration | **P1** |
| G5 | No **ontology / typed object-query / governed action** data layer | No — genuine net-new subsystem | **non-goal (core); extension** |
| G6 | No **OAuth2 authorization-server** (client registration, 3-legged on-behalf-of, refresh rotation) | No — big net-new | **non-goal (core); enterprise/ext** |

---

## 4. P0 — scoped inbound API: machine principal + token + invoke surface

**The doctrine question, answered.** `CLAUDE.md` forbids "bespoke end-user HTTP endpoints" and says
"every member action happens in chat." A **machine caller is not a member action** — it is exactly
the "subsequent use is the wire's job" half of the grant doctrine. The **issuance** of an API token
still happens in chat (an owner asks the agent, conversationally, to mint one), identical to how a
connector grant is won in chat. So P0 is doctrine-*aligned*, not an exception: **grant in chat, use
on the wire.** The `admit`/`invoke` boundary already advertises non-chat callers ("scheduled tasks
and the eval harness also call it," `ext/surface.py:6`); an external API caller is the third.

### The pieces

| Piece | Where | New/reuse |
|---|---|---|
| **Service principal** — a non-human identity a turn is attributed to (least-privilege, distinct audit, own spend caps) | `member.kind ∈ {human, service}` column, or a distinct `principal` table | **1 small core migration** (both ends: the api surface produces it, `admit`/spend/ownership consume it) |
| **API token** — digest-at-rest, scope, expiry, issuer | Extension's own table via `ExtensionContext.transaction` (`ctx.transaction()`, `context.py:212`); mirrors `credential`/`grant` (secret shown once, digest stored) | **extension** |
| **Issuance in chat** — owner-gated `issue_api_token` tool → returns secret once, records digest+scope, binds `surface_identity(surface="api", external_id=digest)` | `Manifest.tools` + `SurfaceContext.link_member` | **extension** |
| **Invoke surface** — `surfaces` point mounted at `/surface/api/…` | `SurfaceSpec.routes`; `POST /surface/api/v1/agents/{name}/invoke` (Bearer → token → principal → `admit`); blocking terminal frame or SSE via `SurfaceContext.tail` | **extension** (the same seam Slack/web use, `serve.py:530`) |
| **Session continuity** — `sessionRid` analog | reuse `conversation` id; accept/return it for multi-turn | **reuse** |
| **Spend attribution** — per-token/principal caps | `spend_cap` scope already ∈ `{workspace, member, agent}`; a service principal is a `member` | **reuse** |

### The scope model — mapped to Foundry

| Foundry axis | selfhost analog | Mechanism |
|---|---|---|
| Ontology SDK scope (which objects/actions) | which **agent(s)** the token may invoke | token record field |
| Platform SDK scope (resources + ops) | which **tool subset** the invoked turn may use | lift the subagent `tool_names` allow-list (`manifest.py:335`) to the token |
| Client operations | — (agent-implicit) | — |
| Effective = ∩(app-max, requested, principal perms) | Effective = token scope **∩** the agent's grants (`grants.py:174`) **∩** spend caps | already an intersection at the proxy + admission |

Outbound scoping is **unchanged**: the invoked agent still reaches only its own granted hosts via
the egress proxy — the token grants *inbound* reach to an agent, never a bypass of that agent's
outbound grants. No confused-deputy: the caller cannot widen the agent's egress.

**Net core cost of P0: one migration (service principal) + accepting the api surface as a first-class
`surfaces` consumer.** Everything else is an extension. If we accept mapping a token to its issuing
*human owner* member (over-privileged, weaker audit), even the migration drops — but the
service-principal is the correct, least-privilege shape and is recommended.

## 5. P1 — authoring in chat + typed I/O

- **Multi-agent authoring.** Owner-gated `create_agent` / `configure_agent` tools routed through the
  existing governed proposal flow (`governance.py`, `agents.propose_change`) — **not** a builder GUI
  (a GUI would be an out-of-core extension surface). The schema already allows N agents per workspace
  (`UniqueConstraint(workspace_id, name)`); what's missing is the create/edit path and a **richer
  `agent` record**. Enrich the migration to the shape `spec.md` already promises: granted tool set,
  skill packs, model policy, memory scope. This is a **both-ends** change (producer = the tools,
  consumer = the turn engine's tool/skill resolution) and closes a live spec-vs-impl gap.
- **Typed invoke I/O + app-state.** Lift the subagent `input_model`/`output_model` contract
  (`manifest.py:335`) to the top-level invoke so the api surface can offer Foundry's typed
  `userInput`/`markdownResponse` + application-variable shape. Optional: a mutable per-conversation
  variable bag tools can update (Foundry "Update application variable"). Modest; rides the invoke
  seam.

## 6. Non-goals / big net-new subsystems

| Item | Why not core | Path if wanted |
|---|---|---|
| **Ontology / typed object-query / governed Action layer** | selfhost core is an *agent runtime*, not a data platform; doctrine: "if a capability can be an extension, it is not core." Foundry's Ontology is its whole differentiator and a large subsystem. | An **extension**: owns typed tables via its own migration, exposes Object-query / Action tools via `Manifest.tools`, backs retrieval on the existing `indexes`/`sources` seams. The `untrusted` flag + `ask_user`/governed-proposal already give the "Action with confirmation" primitive. |
| **Full OAuth2 authorization server** (client registration console, 3-legged auth-code+PKCE on-behalf-of a member, refresh-token rotation) | Big net-new; P0 scoped tokens cover the machine-to-machine 80%. The 3-legged "external app acts as a selfhost member" case is rare for a self-hosted single-workspace deploy. | Enterprise/k8s layer (`spec.md` principle 3), or a dedicated extension mounting `/surface/oauth/…`. |
| **Agent-builder GUI, generated client SDKs, marketplace** | Authoring is a chat/CLI act (doctrine); SDK-gen + marketplace are ecosystem, not runtime. | The invoke surface's OpenAPI + `selfhost ext search/install` are the analogs; a web builder is an extension surface. |

## 7. Where selfhost already leads (keep, don't regress)

- **Durable turns** (DBOS workflow per turn, crash recovery, at-most-one-per-conversation) — Foundry
  exposes execution traces; selfhost's `turn`/`turn_step`/`transcript` + `TrajectoryCorpus`
  (`trajectories.read`) match or exceed the observability contract.
- **Per-call spend metering + caps** (`ledger`, `spend_cap`) evaluated at the single `admit` boundary
  — a native answer to "meter every model/tool call," finer than Foundry's coarse token model.
- **Egress proxy with sentinel→key swap** — raw secrets never enter the sandbox; grant hosts are
  tunnelled opaquely. Stronger than a Bearer token the app holds.
- **Grant-in-chat, agent-bound** (principle 4) — no caller-identity borrowing, no confused deputy.
- **Governed self-improvement** (`trajectories.read` + `agents.propose_change`) — no Foundry analog.

## 8. Doctrine delta (spec.md / CLAUDE.md)

- **§Workspace model / `member`.** "A human" → add `kind ∈ {human, service}`; a service principal is
  a non-human identity turns are attributed to, minted in chat, used on the wire.
- **§Surfaces.** Add the **api surface** as a recognized `surfaces` archetype: a *programmatic* mode
  (Bearer token → principal → `admit`; blocking or SSE), beside durable (Slack) and live (web). It
  reuses the seam wholesale; no new privileged context.
- **§Extension system.** Note the api surface + `issue_api_token` tool + token table as an example
  extension (a new row in the acceptance-test table: "API surface / agent-as-function → surfaces,
  tools, credentials, invoke").
- **CLAUDE.md.** Clarify that "every member action happens in chat" governs **member** actions; a
  **machine** caller authenticated by a chat-issued token is the "subsequent use is the wire's job"
  clause, not an exception — exactly the grant model.
- **§Agent loop / `agent` record.** Reconcile the promised agent shape (tool set, skills, model
  policy, memory scope) with the implemented `name`+`prompt`+`model` (P1).

## 9. Open decisions

1. **Service principal vs. reuse owner member** for a token's identity. (Recommend: service
   principal — least-privilege + clean audit + own spend caps; costs one migration.)
2. **Token table home** — the api extension's own tables (recommended, mirrors how connectors own
   theirs) vs a core `api_token` table. Core only if a second consumer appears.
3. **Scope granularity** — agent-only, or agent + tool-subset from day one? (Recommend: agent +
   optional tool allow-list, reusing the subagent pattern.)
4. **Invoke response contract** — freeform markdown first (matches Foundry `markdownResponse`), or
   typed `output_model` immediately (P1)? (Recommend: markdown + `sessionRid`-analog first; typed I/O
   as P1.)
5. **Ontology** — confirm it stays an extension pattern, never core.

---

## Sources

- AIP Chatbot Studio — Foundry APIs: <https://www.palantir.com/docs/foundry/chatbot-studio/foundry-apis>
- AIP Chatbot Studio — Tools: <https://www.palantir.com/docs/foundry/chatbot-studio/tools>
- AIP Chatbot Studio — Overview: <https://palantir.com/docs/foundry/chatbot-studio/overview/>
- AIP Chatbot Studio — Chatbots as Functions: <https://www.palantir.com/docs/foundry/chatbot-studio/chatbots-as-functions>
- Developer Console — Create application: <https://www.palantir.com/docs/foundry/developer-console/create-application>
- Developer Console — Permissions: <https://www.palantir.com/docs/foundry/developer-console/permissions>
- Writing OAuth2 clients for Foundry: <https://www.palantir.com/docs/foundry/platform-security-third-party/writing-oauth2-clients>
- Client-credentials application setup: <https://www.palantir.com/docs/foundry/consumer-mode/client-credentials-setup>
- Foundry API — Authentication: <https://www.palantir.com/docs/foundry/api/general/overview/authentication>
- Foundry Ontology — Overview: <https://www.palantir.com/docs/foundry/ontology/overview>
