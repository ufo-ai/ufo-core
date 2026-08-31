---
rfc: 0015
title: "Extension host — a sandboxed, JS-only channel for third-party extensions"
status: accepted
date: 2026-07-14
---

# Extension host — a sandboxed, JS-only channel for third-party extensions

> Extensions today run in-process, in the `serve`/`jobs` roles, with full privilege — a static
> import gate is the only boundary. First-party (bundled) extensions stay exactly that way: they
> are reviewed by the same people who own core, and the migration cost of moving them is not worth
> paying for a population that isn't the actual threat. This proposes a genuinely new, additive
> mechanism for the population that is: a JS-only, out-of-process, WASM-sandboxed extension host —
> **`runner`**, a standalone service, reached over a channel — for third-party code and for local
> development against a real hosted deploy in test mode. Extends `spec.md` §Extension system,
> §Third-party extensions, and §Sandboxing.

## Current state

An extension is a Python package whose `ufo.extension` entry point returns a `Manifest`
(`spec.md:129-131`). Two facts, read directly off the code, describe the mechanism first-party
extensions keep using, unchanged, after this RFC:

1. **Extension code executes in the serve process, at every boot, before any trust decision.**
   `discovered()` (`core/src/ufo/host/ext/loader.py:129-139`) calls `entry.load()()` for **every
   installed entry point**, unconditionally — the module's top-level code and its manifest-builder
   function both run in `serve` before `load_manifests()` (`loader.py:215-240`) even checks whether
   the lockfile pins that extension. RFC 0007 already says this plainly: arbitrary code "runs
   in-process in the serve/jobs roles (not the sandbox)," and the SDK-import gate is "a static
   import boundary, not a runtime sandbox" (`docs/rfcs/0007…md:148-157`). This RFC does not close
   that gap for first-party code — see §Proposal's scope note for why — it closes it for the
   population RFC 0007's own words describe as carrying real risk: code someone other than this
   repo's maintainers wrote.
2. **`ExtensionContext` hands out live, unscoped power.** `transaction()`
   (`core/src/ufo/runtime/ext/context.py:387-397`) yields a raw whole-database `AsyncConnection`; `index`,
   `embed`, `invoker`, `scheduler` are live Protocol objects; every accessor reads the current
   workspace off an ambient contextvar, `ws_current()` (`core/src/ufo/runtime/workspace.py`, used at
   `context.py:65,141,189,330,…`). This remains exactly as-is for first-party extensions. It is
   the reason a *third-party* extension gets a deliberately narrower context instead (§5) — this
   surface was never designed to be handed to code the operator didn't write.

CI enforces the trust boundary today entirely by static AST analysis: `_sdk_import_failures`
(`gates.py:139-157`) fails a build if `extensions/`/`packs/` import `ufo.*` outside `ufo.sdk`;
`_boundary_failures` (115-129) fails cross-role imports; `_conformance_failures`/
`_sample_declared_points` (160-218) hold the sample extension to registering exactly the gated
Manifest points. None of these reason about a runtime boundary — and for first-party extensions,
after this RFC, none will. That remains a conscious, stated trade, not an oversight.

The **sandbox proxy** (`spec.md:108-116`) is prior art for the *principle* this RFC's credential
model follows — a sandboxed process sees a sentinel, the real secret exists only at a boundary the
sandboxed code doesn't control — but not for the literal mechanism §6 uses. `runner`'s boundary is
a custom host-function import, not a network-level proxy; §6 explains why once the real
constraints were checked against the actual embedding library, not assumed from its docs. Connector
brokers already execute credentialed calls **server-side**, handing the extension only a result,
never a token (`spec.md:141`) — closer in spirit to what §6 does. And `extensions/mcp` already
speaks JSON-RPC as a client (`ufo_ext_mcp.py:44,51`) — this repo is not choosing a wire format from
zero.

Grepping `core/`, `extensions/`, `docs/` for `extension_host`, `remote_extension`, `quickjs`,
`grpc`, `js_ext` returns nothing: the mechanism this RFC adds is greenfield.

## Proposal

**Scope, stated once.** This RFC does not touch first-party extensions. They keep exactly the
in-process Python mechanism §Current state describes, in every particular — same loader, same
`ExtensionContext`, same static-only gate. Everything below is new capacity added *alongside* that:
a JS-only, out-of-process, sandboxed mechanism for **third-party** extensions (store-installed or
loaded via `ufoctl ext dev`), plus the local-dev and test-mode flow that goes with it. The judgment
this rests on: first-party code is reviewed by the same people who own core, so the thin boundary
RFC 0007 flagged is an accepted trade for code this repo's own maintainers wrote — it is not an
acceptable trade for code a stranger wrote, which is the actual problem being solved here.

### 1. What a third-party extension may register

Third-party extensions get **three** Manifest points — `tools`, `subagent_tool_grants` (scoped to
the manifest's own declared tools), and `credentials` (slot declaration) — and nothing else. Every
other point is either core's own backend tier, which was never a third party's to provide, or needs
a contract a static JSON manifest cannot yet carry:

| Point | Status | Reason |
|---|---|---|
| `tools`, `subagent_tool_grants`, `credentials` | **allowed** | fully expressible as static JSON, dispatched over §3–§6 |
| `surfaces` | never | asserts member identity; `SurfaceContext` is strictly more privileged than `ExtensionContext` |
| `indexes`, `embeds`, `models`, `carriers`, `hubs`, `cdp_providers`, `auth_proxies`, `search_providers`, connector brokers | never | core's own backend tier (RFC 0001), not a third party's to provide |
| `routes`, `jobs`, `sources`, `onboarding_steps`, `subagents` | deferred | each needs a live Python value a static manifest can't carry (`RouteSpec.identify`, `JobSpec.candidates`, `SourceProvider.build`, `OnboardingStep.handler`, `SubagentProfile.input_model`/`output_model`) |
| `hooks`, `prompt_sections`, `skills` | deferred | each puts untrusted text or observation into a trusted context (the system prompt, or cross-tool turn visibility) and needs a trust/scope contract this cut doesn't design |
| connector tool-surface consumption | deferred | no `ctx.connector.execute` RPC exists; a third-party's path to an external API is a declared credential (§6), not a brokered call |
| `runtime_skills` | never (this cut) | no proven use case |

This is additive to RFC 0001, not a revision of it: the backend tier stays exactly as privileged as
it is today, for everyone. `subagent_tool_grants` is allowed but scoped tighter than first-party's:
a third-party grant may name only tools the same manifest declares, and only a subagent profile that
already exists. First-party's "grant a profile that may appear later" tolerance
(`loader.py:355-365`) is a sleeper grant when the granting code is unreviewed — a store extension
could name a not-yet-installed profile and have its tool silently start reaching that profile the
day a matching name installs — so that tolerance stays first-party-only. The deferred points are
Open decision 6, taken one at a time after the narrow set proves out.

### 2. The manifest: declarative, JS-only

A third-party extension ships a static file at its package root, `ufo.manifest.json`, read
**without executing any extension code**:

```json
{
  "name": "acme-crm", "version": "0.3.0",
  "tools": [{
    "name": "lookup_contact", "description": "Look up an Acme CRM contact by email",
    "args_schema": {...}, "handler": "lookupContact", "activation": "lazy"
  }],
  "credentials": [{
    "name": "acme_api_key", "description": "Acme CRM API key",
    "injection": {"host": "api.acme.com", "header": "authorization",
                  "sentinel": "UFO_SENTINEL_ACME_API_KEY", "dimension": "egress"}
  }]
}
```

There is no `runtime` field — third-party is JS, full stop — and no `requires` field, since every
seam it could name is a backend point §1 doesn't allow. `handler` names the JS export `runner`
calls (§4), so a tool can rename without breaking its binding; `description` is required, as
first-party's `ToolDef` already is (`registry.py:25-30`). **Activation is lazy**, mirroring VS
Code's `activationEvents`: `runner` loads and calls a handler only on first dispatch to it.

Three decisions make third-party tools safe to register from an unreviewed manifest:

- **`untrusted` and `side_effecting` are forced `true` at publish/install, never author-controlled.**
  `untrusted: false` would let attacker-controlled output skip the engine's untrusted-content wall;
  `side_effecting: false` would drop the `idempotency_key` (`engine.py:1026`) a credentialed write
  needs to survive a DBOS replay without double-firing. First-party earns honest flags by review;
  nothing plays that role for a store manifest.
- **`args_schema` is raw JSON Schema, wrapped without conversion.** `ToolDef.input_model` is a
  `type[BaseModel]` dispatch validates against directly (`engine.py:1009`); a third-party tool wraps
  its `args_schema` in a dynamically-built `BaseModel` whose `model_validate` delegates to a
  JSON-Schema validator and whose `model_json_schema` returns the declared schema verbatim — one
  schema drives both what the model is shown and what input is checked, and `ToolDef`'s shape and
  first-party dispatch stay untouched. No JSON-Schema-to-pydantic translation.
- **Credential slots reuse `CredentialSlot`/`InjectionTarget` exactly** (`manifest.py:41-58`),
  except `injection` is **required** (a slot with `injection: null` has no meaning for code with no
  in-process readability, §6), and rows key on a composite `f"{extension_name}:{slot_name}"` so two
  store extensions never collide on a slot name (§6).

The enforcement boundary is `ufoctl ext publish`/store-install validation — re-run at every
boot/upgrade, never `gates.py`'s CI conformance check, which a real third-party manifest authored
outside this repo never reaches. See `spec.md` §Third-party extensions for the settled schema and
the collision, duplicate-name, and namespace rules that validation enforces.

### 3. The channel: JSON-RPC 2.0, transport-swappable

| Leg | Transport | Auth |
|---|---|---|
| Core ↔ `runner`, co-located (zero-services default) | JSON-RPC 2.0 over a Unix domain socket (`0600`, private runtime dir) | process ownership is the boundary |
| Core ↔ `runner`, scaled deploy | JSON-RPC 2.0 over WebSocket + TLS | mTLS or a shared bearer — never a bearer without TLS |
| Local extension dev → hosted test mode (§7) | JSON-RPC 2.0 over WebSocket + TLS | bearer scoped to the one disposable workspace it provisioned, rejected by any other |

Every leg is a **persistent, bidirectional** connection, not request/response HTTP — load-bearing,
not a style choice. §5's restricted context needs `runner` to call *back* into core mid-dispatch
(`ctx.store`, a metered `ctx.model`) while core still awaits that same dispatch's result; plain
HTTP request/response gives the server no second channel and would deadlock the first time a handler
touches `ctx.*`. A UDS and a WebSocket both carry JSON-RPC's peer-symmetric requests natively;
"HTTP behind a load balancer" does not.

`core → runner` carries `dispatch_tool` — the only dispatch method this cut needs (§1);
`dispatch_hook`/`run_job`/`handle_route` stay off the wire until those points are un-deferred.
`runner → core` carries the restricted context (§5). A **control** notification (`cancel`,
`shutdown`) rides a separate priority queue, so a cancellation never waits behind a slow call.
Handshake (`go-plugin`'s): `runner` sends `{"ufo_protocol": 1}` on connect; core rejects a mismatch
before any call, fail-loud, no fallback. Rejected: gRPC+protobuf — a per-language codegen toolchain
this repo doesn't need, and `extensions/mcp` already has a working JSON-RPC client (see
Alternatives).

### 4. `runner`: a standalone service, not a Kubernetes workload

`runner` is a **standalone FastAPI service**, deployed exactly like `servers/control/` already is — its own
`pyproject.toml`, its own image, its own process — never folded into `ufoctl serve`'s one process,
and never requiring Kubernetes. It embeds `wasmtime` directly (the `wasmtime` PyPI package),
confirmed against the real 2026 release, not assumed from documentation:

- **JS only, because that's what makes this cheap.** A JS engine can be handed a fresh,
  capability-scoped realm at low cost — Figma's and Shopify's own conclusion after evaluating
  alternatives. Python cannot be safely confined the same way at the language level — CPython
  maintainers' own conclusion, RestrictedPython's documented escape history (§Alternatives). That
  asymmetry is *why* the line in §1 is drawn by language: it is not a policy preference, it is the
  only axis on which the cheap mechanism exists at all.
- **Stateless per call.** Every restricted-context method (§5) already round-trips through
  `ctx.store`/Postgres, never extension-local memory — so a WASM instance carries no state across
  calls. One `wasmtime.Store` per RPC call, discarded after. No session affinity, no per-tenant warm
  pool, no reaper to write.
- **Bounds are structural, confirmed against the library, not hand-waved.** `Config.consume_fuel`
  and `Config.epoch_interruption` bound CPU/instructions; `Store.set_limits(memory_size=…)` bounds
  memory. A guest declaring any import other than the two host functions §5–§6 define fails to
  instantiate — `Linker.define_unknown_imports_as_traps()` — loud, not silent. (Concrete fixed
  limits live in `spec.md` §Third-party extensions.)
- **Modules are content-addressed, reusing what already exists.** A published extension's compiled
  WASM artifact is digested exactly like `ExtensionPin.digest` digests a Python package today, and
  stored in the existing S3-compatible blob store. `runner` fetches-and-caches a module by digest on
  first use.
- **Deployment is whatever autoscaling substrate is already in play.** Plain stateless processes
  behind a load balancer — an AWS ASG, a GCP MIG, an Azure VMSS, a plain on-prem VM pool, or a
  container scheduler including but never requiring Kubernetes. This matches core's own fixed
  decision (`spec.md`'s Fixed decisions table: "Kubernetes… absent from core by construction").
- **A sibling service, like `servers/control/`, not a fifth role.** `ufoctl serve`'s four roles
  (surfaces/workers/jobs/proxy) still share nothing in memory and still run in one process by
  default; `runner` sits beside that, exactly as `servers/control/` already does — no new architectural
  category, and nothing about the existing Roles doctrine changes.

### 5. `ExtensionContext` over the wire — the third-party surface

A third-party extension gets a **restricted** context — the restriction is the point, not a
limitation to work around. Two host imports carry it: `ufo.http_request` (§6) and `ufo.ctx_call`,
which routes every `ctx.store`/`ctx.model` call back to core over §3's connection as a `runner →
core` callback. `@ufo/sdk` wraps both, so the guest never speaks JSON-RPC directly.

| Method | Shape | Note |
|---|---|---|
| `ctx.store.get/put/delete/list` | `JsonValue`, size- and count-bounded per call | bounds live in core, outside the WASM store's own limits — this is core persisting guest data, not guest code executing |
| `ctx.credentials.get(slot)` | the **sentinel**, never the real secret | a static manifest value; `@ufo/sdk` returns it locally, `runner` resolves it only at the HTTP boundary (§6) |
| `ctx.model.complete/turn` | Pydantic, **spend-checked before the call** | the same `SpendEvaluator` turn admission uses, reserving *worst-case* cost (bounded input + `max_tokens` priced at max), not metered only after |
| `ctx.idempotency_key` | present iff the tool declared `side_effecting: true` | the key first-party's engine folds onto `ToolContext` (`engine.py:1026`), carried on the wire so a resumed turn dedups the external write |
| `ctx.invoke`, `ctx.schedule` | **absent this cut** | deferred with `jobs`/`hooks` (§1); no wire shape for a live invoker/scheduler yet, and first-party's tool path wires no invoker either (`loader.py:307`) |
| `ctx.transaction()`, `ctx.index`/`embed`/`invoker`/`scheduler` | **absent** | no raw DB handle or backend/SPI access crosses this boundary, ever (§1) |

Scope rides every call as `(workspace_id, extension_name, extension_digest)` — `runner` holds no
per-workspace connection, any process serves any tenant. `extension_name` scopes durable state (it
must survive a publish, so it keys on the stable name, not the artifact digest); `extension_digest`
names which artifact to run. But `runner` authenticates to core as the fleet (§3), so a callback's
claimed scope cannot be trusted on faith. Core mints a per-dispatch **capability** at `dispatch_tool`
time, scoped to exactly that call and valid for its whole duration (one call makes several `ctx.*`
callbacks, so it is not consumed on first use); every `runner → core` callback must present it, and
core accepts one only while that dispatch is still open, invalidating it the moment the dispatch
completes. A compromised `runner` can't claim another workspace's data. This is the same
memoized-token-scoped-to-one-act shape connect-account handoffs already use.

### 6. Credentials and egress: a host-function import, not a network proxy

Third-party extensions never see a real secret, and get **no unmediated network** — every outbound
call is this one host import. A network-level proxy reusing `ScopeRule`/`InjectionRule` (the agent's
own turn-sandbox proxy) is not the mechanism: checked against the real `wasmtime` Python package,
`Store.set_wasi_http()` takes no arguments and exposes no request-interception hook — there is no
WASI-HTTP seam to inject into from Python (see Alternatives). Instead:

- `runner` defines exactly one HTTP host import via `Linker.define_func`; nothing else network-facing
  is linked, so any other import the guest declares traps (§4).
- At dispatch, **core** — not `runner` — resolves the call's declared credential slots (by the
  composite key, §2) and sends `runner` a short-lived, call-scoped list of injection rules
  `(host, header, sentinel, real)` — the shape `InjectionTarget`/`InjectionRule` already use.
  `runner` queries no credential store and holds nothing between calls.
- The guest's request carries the sentinel; `runner` substitutes the real value **only when the
  request's target host, scheme (`https://` only), and header all match a declared rule** — any
  other host, plaintext, or header is sent with the sentinel unsubstituted. This
  match-before-substitute check is the whole injection-enforcement surface: without it, a guest
  could put the sentinel on a request to any host and be handed the real secret.
- A guest reaches **no private or internal address** (loopback, link-local, private, reserved,
  multicast, or CGNAT/shared space) regardless of its rules, since `runner` makes the call in its own
  network position; the validated address is pinned through the connection to close DNS rebinding.
- **Public egress is otherwise unrestricted, by design.** A guest can send data it legitimately
  holds to any public host, exactly as any browser, VS Code, or MCP extension with network access
  can (`extensions/mcp`'s own remote tools carry this same reach today). The sandbox stops secret
  *theft* and internal-network *reach* — not data an extension was handed from leaving over its own
  declared access. Store review/provenance, not this sandbox, is the control for that.
- Metering rides the dispatch and is recorded per host-import call **as it completes** — success,
  trap, or timeout — so a billable request that already went out before an error still lands in the
  ledger; an uncredentialed call records under the `egress` dimension.

The settled enforcement details (response redaction, byte and time caps outside the WASM store's own
limits, off-event-loop execution, header handling) live in `spec.md` §Third-party extensions; their
implementation and tests land with `runner` (Unit C).

### 7. Local dev: no new "test mode" primitive needed, and no WASM in the loop

The requirement — real external calls, no live workspace data — is already load-bearing doctrine,
not a gap: `spec.md:29` — "every row carries `workspace_id`… a deploy serves ONE workspace" — and
cross-workspace access is already unrepresentable (`test_a_second_workspace_reaches_none_of_the_firsts_rows`,
`core/tests/test_ext_conformance.py:912`). The research on Stripe/Shopify/Salesforce converges on
the same shape ufo already has: **"test mode" is a scoped, disposable tenant on real
infrastructure, not a mocked network.**

```bash
ufoctl ext dev ./my-extension --remote https://acme.hosted.ufo.dev
```

1. `ufoctl` authenticates as a member and provisions (or targets an existing) **disposable
   workspace** on the hosted deploy — ordinary onboarding, nothing new.
2. It spawns the local extension as a plain **Node** process (there is no other option — §1), and
   opens the WebSocket+TLS leg from §3 straight to core, which routes it to that workspace's own
   `runner` access. Every credentialed call still runs through the same host/header-checked injection
   `runner` uses — never an ambient real secret in the local process.
3. Real grants/credentials the developer adds in that workspace (in chat, the existing flow) make
   real external calls genuinely succeed.
4. `ufoctl ext dev` opens the CLI's existing live surface (`spec.md:248`) against that workspace,
   so the developer chats with the agent with their extension active, immediately.

No workspace ever sees another workspace's data — that invariant predates this RFC. **WASM never
enters this loop.** `runner` (§4) is where a *published, installed* extension executes at scale; a
developer always runs their own code as a plain Node process with a normal debugger attached.
Compiling to WASM is a step in `ufoctl ext publish`'s pipeline (off the boot loop, the principle
RFC 0007 already established for git-sourced installs), never something a developer's inner loop
waits on. Running fully offline (`ufoctl ext dev ./my-extension`, no `--remote`) targets a local
`ufoctl serve` the same way, over the UDS leg.

### 8. Conformance: a new probe for a new seam

`extensions/sample` is untouched — it continues to prove the existing, unchanged first-party seam
exactly as it does today. This RFC's seam gets its own sibling probe, `extensions/sample_js`, built
the way `AGENTS.md`'s testing doctrine prescribes: a real consumer (real manifest, real dispatch,
the real restricted context) proving the seam, not a fake — and the one fixture `gates.py`'s schema
check validates. It grows with the units it proves (§9): at Unit A it proves the channel end to end
(a locally-run Node process dispatching into `ufoctl serve`, no `runner`/WASM yet); once `runner`
lands at Unit C it adds what only `runner` can — a fuel-exhausting case confirming one runaway call
is contained to itself, a negative case confirming a guest calling any import beyond the two host
functions traps, and a negative case confirming a request to an undeclared host is sent with its
sentinel unsubstituted (proving §6's match-before-substitute, not just asserting it).

### 9. Landing order

| Unit | Ships | Proof |
|---|---|---|
| A | Manifest schema + JSON-RPC channel + JS host-runtime; `extensions/sample_js` | Sample JS's tools dispatch over the channel from a locally-run Node process into `ufoctl serve` |
| B | `ufoctl ext dev` (offline) and `--remote` (disposable hosted workspace) | A locally-run extension makes a real external call through a declared, injected credential from a hosted test-mode workspace, while a second workspace's data stays unreachable |
| C | `runner` (FastAPI + embedded `wasmtime`; fuel/epoch/memory bounds; the two host imports); `ufoctl ext publish`'s JS→WASM compile step (Open decision 5); call-scoped credential injection | A published extension's tool call executes in `runner` with a real secret injected for that call only; a fuel-exhausted call is killed without affecting a concurrent call; a call to an undeclared host never receives the real secret |
| D | `runner` fairness (per-workspace concurrency cap) | A synthetic high-volume workspace cannot starve a second workspace's calls |

Four units, no first-party extension touched by any of them. **Unit C is not adversarial-multi-tenant
safe without D:** without the concurrency cap, one high-volume workspace can occupy `runner`'s whole
`to_thread` pool with concurrent slow dispatches and starve every other workspace (§6). C-without-D
is fine for a controlled rollout (a small, trusted set of installed extensions); D is a precondition
for opening `runner` to genuinely adversarial load, not a later nicety.

## Doctrine fit / implications

- **Core doctrine, deliberately not fully closed.** "If a capability can be an extension, it is
  not core" always meant *logically* separable, not *procedurally* isolated — extensions still ran
  with core's own privilege. This RFC closes that gap for third-party code and leaves it open for
  first-party, on the stated judgment that review by core's own maintainers is an acceptable
  substitute for a runtime boundary. That is a trade, not a fix, and it is named as one.
- **One shape, per population.** Third-party's manifest is one clean thing: a file read without
  executing code, always. First-party keeps its existing mechanism untouched, including its own
  known gate blind spot (`runtime_skills`/`requires` escaping `_manifest_point_fields`, §Current
  state) — this RFC does not fix that, because it does not touch first-party's manifest path at all.
- **Enforce, don't document — for the population that needs it.** Third-party gets a real runtime
  boundary (WASM + a host-mediated-only capability surface, host/scheme/header-matched before any
  secret moves) where none existed before. First-party keeps the static import gate as its only
  enforcement, exactly as today — an explicit, judgment-based choice, not an inconsistency.
- **Hot paths.** The co-located leg (core ↔ `runner`, same machine) gets **no retry** on failure —
  a wedged local call is a bug, root-caused like any other internal fault. The local-dev-to-hosted
  leg crosses a real, uncontrolled network to a developer's own machine — genuine external
  uncertainty, and the one leg where reconnect/backoff is legitimate.
- **A small, new mechanism, not a second consumer of an existing one.** §6 is not the agent
  turn-sandbox's proxy reused for a new caller. It is the smallest mechanism that fits — one host
  function, one call-scoped rule list, no standing state — following the *same principle* (sentinel
  in the sandbox, real value only at a boundary the sandboxed code doesn't control, released only to
  a matched host and header) without literally reusing the proxy's types.
- **A sibling service, not a new architectural category.** `runner` is a separate deployable unit,
  exactly as `servers/control/` already is for the hosted gateway — no Kubernetes dependency either way,
  matching core's own fixed decision.
- **A bigger departure from the named prior art than it looks.** VS Code does not distinguish
  first-party from third-party extensions at all — every extension shares one host, full privilege.
  This RFC does, sharply: first-party is untouched, third-party is sandboxed harder than VS Code
  sandboxes anything. The closer analogy is a host application trusting its own built-in modules
  while sandboxing its plugin marketplace (Chrome's own components vs. web-store extensions).
- **Interaction with RFC 0007, noted not resolved.** RFC 0007 lets an operator install a Python
  extension from an arbitrary git URL and run it in-process — by this RFC's line, that is still
  "first-party" (operator-approved), because the split here is about review/provenance, not source
  location. Whether that's the right call for an unreviewed git URL is a legitimate follow-up this
  RFC does not settle.

## Alternatives

| Alternative | Rejected because |
|---|---|
| Also sandbox first-party extensions | Cost/benefit: migrating a reviewed, all-Python ecosystem to a new manifest and channel doesn't address the actual threat (untrusted authorship), and the population that needs a runtime boundary is fully served by the third-party mechanism alone |
| Kubernetes-based deployment (SpinKube/containerd-shim-wasm) | Requires Kubernetes as a hard dependency, which `spec.md`'s Fixed decisions table forbids in core, and fails the on-prem-and-major-clouds requirement without it |
| Hosted/managed WASM platform (Fermyon/Akamai Functions) | A hosted SaaS dependency this RFC's deployment requirement excludes by construction; also a post-acquisition vendor risk |
| A network-level egress proxy reusing `ScopeRule`/`InjectionRule` | No WASI-HTTP request-interception hook exists to proxy (`Store.set_wasi_http()` takes no arguments), and the guest has no raw socket once its only network import is §6's host function — a proxy would solve a problem that doesn't exist at this boundary |
| Substitute any header value matching a known sentinel, regardless of destination | A credential-exfiltration bug: it lets a guest redirect a sentinel-bearing header to any host and receive the real secret back. §6's match-before-substitute (host, scheme, and header must all match a declared rule) closes it |
| gRPC + protobuf wire protocol | Real contender, but costs a per-language codegen toolchain this repo doesn't otherwise need; `extensions/mcp` already has a JSON-RPC client to build on |
| One WASM sandbox per workspace, rather than one `Store` per call | A `Store` is cheap enough per call that holding one warm per workspace buys nothing; stateless-per-call is simpler and every persistent thing already lives in `ctx.store`/Postgres |
| V8-isolate-style shared-process multitenancy | Workable — credential handling (§6) is a host import either way; WASM is chosen because `wasmtime`'s Python embedding confirmed fuel/epoch/memory limits directly, and Javy/QuickJS-in-WASM has Shopify's production precedent |
| Firecracker microVM per call (E2B/Lambda model) | Strongest isolation, but the trust framing (many mutually unrelated authors sharing a marketplace) is Cloudflare's Workers-for-Platforms case, not Lambda's fully-adversarial one; the cost doesn't buy a property this threat model needs |
| In-process language-level sandbox only (QuickJS/RestrictedPython, no WASM) | WASM adds a memory-safety wall a bare language-level sandbox doesn't have — Figma's own reason for moving off the Realms shim |

## Open decisions

1. **Landing scope for the first cut.** Land Units A–D and reassess, or specify the full third-party
   capability set (§1) up front? Recommend: A–D only — feel `runner`'s first real load before
   locking down which Manifest points beyond the initial set are worth exposing.
2. **JS host-runtime distribution.** Ship `@ufo/sdk` as an npm package mirroring the restricted
   context (§5) 1:1, or let the wire protocol (§3) be the only contract? Recommend: ship it —
   otherwise every third-party author hand-rolls the JSON-RPC envelope.
3. **Disposable test-mode workspace lifecycle.** Auto-expire a `ufoctl ext dev --remote` workspace
   after idle time, or leave cleanup to the operator? Recommend: fold into the existing reaper's job
   rather than invent a second cleanup path — needs the operator's sign-off on the retention window.
4. **`runner` fairness — and a separate egress-volume gap.** A per-workspace concurrency cap
   (reusing `spend_cap`'s scope shape) versus weighted fair scheduling closes thread-pool starvation
   (§9). Distinctly, and unclosed: uncredentialed public egress is metered under the `egress`
   dimension at `priced_micro_usd=0` (`accounting.py:268-288`), and `SpendEvaluator` sums only
   *priced* spend — so call *volume* trips no cap, bounded only by per-call fuel/timeout. It needs
   its own answer (a rate/count quota, or pricing `egress` nonzero); decide it alongside the
   concurrency cap, same Unit-D resource-governance bucket. (The module cache's total size across
   distinct digests wants an eviction policy in the same bucket.)
5. **JS→WASM compile toolchain — and neither option produces this cut's ABI as-is.** `runner`'s
   embedding (§4) expects `alloc`/`handle` exports plus the two host imports, everything else
   trapped. Plain Javy compiles a stdin/stdout entry point, not a called export, and needs its own
   WASI imports linked (which `define_unknown_imports_as_traps` would trap); ComponentizeJS has
   native typed import/export but less mature JS tooling. Recommend: prototype the actual output — a
   Javy linear-memory shim with its WASI imports explicitly allowlisted, or a ComponentizeJS spike —
   before Unit C's "a real JS extension runs in `runner`" is more than a hand-written-WAT
   demonstration.
6. **Declarative contracts for the deferred points (§1).** Five need a JSON substitute for a live
   Python value; three need a trust/scope contract (a per-hook grant; an untrusted rendering context
   for `prompt_sections`/`skills` — a tool's own `description` is the same trusted-context risk,
   mitigated in this cut only by a length cap); one needs a brokered `ctx.connector.execute` RPC.
   Recommend: design each after Units A–D prove the narrow set, not as a precondition for landing.
