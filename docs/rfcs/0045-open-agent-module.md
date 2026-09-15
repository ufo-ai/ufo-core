---
rfc: 0045
title: "Open agent module — two repos, one internal RPC"
status: proposed
date: 2026-09-06
---

# Open agent module — two repos, one internal RPC

> Split ufo into an open agent module and a private hosted repo. The open repo is the runtime a
> developer runs, reads, and extends: `ufoctl`, the terminal client, the SDK, and the open extension
> set. The hosted repo is what runs the paid service: the portal, the Slack and iMessage surfaces,
> billing, connector sources, evals, the four Rust servers, and the deploy. The two meet at one
> versioned internal RPC and at the existing extension seam, which gains the few points the hosted
> parts need to import nothing but `ufo.sdk`. Extends `spec.md` §Principles (open source / on-prem /
> hosted), §Extension system, §Surfaces, §Accounting / billing, and RFC 0043.

## Current state

One repo, one wheel. `pyproject.toml:52-107` registers 57 extensions and 12 packs, and
`[tool.hatch.build.targets.wheel]` bundles core, every extension, every pack, and `evals/` into one
distribution. Everything Python runs in the one `ufoctl serve` process (`spec.md:35`).

**The extension seam is in-process by construction.** `Manifest` (`runtime/ext/manifest.py:819`)
is a frozen dataclass; 24 of its 35 points carry live callables or classes (`ToolDef.handler`,
`ObjectKind.store`, `JobSpec.candidates`, `SurfaceSpec.post`, `CarrierSpec.factory`,
`SubagentProfile.input_model`). `ExtensionContext.transaction()` (`runtime/ext/context.py:1790`)
yields a raw `AsyncConnection`. Workspace scope is an ambient contextvar (`db.py:105`). RFC 0015
(accepted) refused to move first-party extensions out of process for exactly these reasons, and RFC
0043 rejected a service boundary inside a round.

**Four Rust servers already sit behind a wire.** Each is wire-only except the gateway:

| Server | Direction | Routes today | Bearer |
|---|---|---|---|
| `ufo-egress` | proxy → runtime | `/internal/egress/{authorize,resolve,meter,tool-bridge}` (`runtime/access/egress_control.py:145`) | `UFO_EGRESS_CONTROL_TOKEN` |
| `ufo-cache` | cache → runtime | `/internal/git-credential` (`egress_control.py:152`) | `UFO_CACHE_CONTROL_TOKEN` |
| `ufo-control` | gateway → runtime | `/internal/onboard/{seat,membership,choices,fleet,invitations}` (`onboard/onboard_control.py:300`) | `UFO_ONBOARD_CONTROL_TOKEN` |
| `ufo-preview` | runtime → service | `POST /render`, multipart (`harness/document_renderer.py:82`, `runtime/media/preview_renderer.py:96`) | `UFO_PREVIEW_TOKEN` or a presigned PUT |
| sandbox ingress | ingress → runtime | `/internal/site-not-answering` (`harness/sandbox/site_report.py`) | signed report token |

No `.proto` exists. The contract is Rust serde types plus three JSON fixtures under
`servers/*/tests/*_contract.json` that core tests read by relative path
(`core/tests/access/test_egress_control.py:56`, `core/tests/auth/test_surface_token.py:50`,
`core/tests/onboard/test_onboard_control.py:744`). `ufo-control` additionally provisions core's
schema as database owner: `servers/control/src/rls.rs` enumerates `pg_tables`, refuses any table
without `workspace_id` (`:42`), creates the RLS policies, and creates the DBOS database (`:114`).
A core migration adding an unscoped table breaks a Rust binary.

**Billing is a set of gates inside transactions.** `runtime/billing/accounting.py` (1,586 lines)
holds the ledger writers, `SpendEvaluator` (caps), the usage-export seam, and `BalanceGate`;
`balance.py` (447 lines) holds the prepaid balance. `BalanceGate.admits` runs at admission
(`runtime/surfaces/admission.py:1009`), on fold (`:868`), on spawn (`runtime/subagents.py:839`),
on resume (`runtime/jobs.py:204`), and on off-turn model calls (`runtime/ext/context.py:837`);
`BalanceGate.sustains` runs before every model round (`runtime/engine.py:2762`), behind a
five-second in-process cache (`balance.py:40`). Every recorder debits the balance inside the caller's
transaction. Core names the billing extension's tool: `BILLING_ACTION` at `schema/records.py:219`
and a migration allowlist entry. The signup grant is two constants in `onboard_control.py:387-394`.

**Sources are a core framework nothing in core consumes.** `runtime/sources/` (2,923 lines), the
`source` and `page` tables, the `source_sync` job, and the `page_change` runner
exist for `extensions/sources` (55 providers) and `extensions/gbrain`. Memory imports one name from
the sources extension: `ufo_ext_sources.pages.PAGE_KIND` (`extensions/memory/ufo_ext_memory/manifest.py:32`).

**Web and Slack are already surface extensions with no core-internal imports.** Web imports four
sibling extensions (`extensions/web/ufo_ext_web/surface.py:44-52`: imessage, sites, slack, ufo);
slack imports connectors constants. Core holds web-only knowledge: `PORTAL_SURFACE = "web"`
(`schema/records.py:54`), `WEB_SURFACE` (`host/kinds/artifacts.py:76`, `onboard/seed.py:33`).

**Evals are an in-process client.** `evals/__main__.py:958-1098` calls `init_db`,
`load_manifests`, `context_for`, and admits through `MemberAdmission` directly; 358 imports reach
`ufo.*`, 30 of them `ufo.sdk`. About twenty suites write 19 core tables through
`ufo.db.workspace_tx`. Seven core and extension test files import `evals.*`
(`core/tests/loop/test_turn_lifecycle.py:30-34`, `core/tests/test_o11y.py:37`, and five more).
A remote path exists: `evals/driver.py:341` admits through `ufo --remote --json`. Environment
documents (RFC 0043) already reshape prompt, tools, and skills per turn, and evals already pass
`--environment` (`evals/__main__.py:306`); ablation still builds a git worktree per arm
(`evals/ablate.py:717`) because the text most arms edit is a Python string literal.

## Proposal

### 1. Two repos

| | Open repo | Hosted repo |
|---|---|---|
| Ships | The `ufo` wheel, the `ufo` terminal client, the sandbox image, the RPC schema bundle | Deployed images, the four Rust servers, terraform, the hosted extension wheels |
| Python | `core/src/ufo` (harness, runtime, host, sdk, schema, onboard, cli, serve), `testsupport/`, `gates.py` | Extension packages depending on the `ufo` wheel; `evals/`; hosted packs; a `gates.py` for its own rules |
| Extensions | sample, ufo, debugger, memory, index_default, embed_openai, turbopuffer, gbrain, connectors, composio, pipedream, keyed_connectors, mcp, research, perplexity, browser, browser_use, sandbox_chrome, browserbase, repl, coding, documents, todos, objectives, scheduled_tasks, monitors, report_digest, brief_pipeline, skill_create, self_improvement, sites, docker, e2b, redis_hub, flags_open, bedrock, openrouter | web, the nine `app_*`, slack, imessage, sources, billing (today's metronome plus the balance), enrichment, flagship, eval_env, product census |
| Packs | sample_pack, assistant (the open flagship: today's list minus web, the apps, sources, flagship, enrichment) | assistant_hosted, assistant_billing, assistant_eval, dsqa_eval, gdpval_eval |
| Rust | `client/` | `servers/{control,cache,egress,preview}` |
| Ops | `compose.yaml`, `Makefile` (build, test, serve), CI for the open set, releases | `infra/`, deploy and nightly workflows, `.github/scripts/`, `hosted.toml`, `sandbox/proxy_gate.py`, `docs/outages/` |
| RFCs | Every RFC whose code stays | 0027 (WorkOS), 0032 (cache), 0035 (egress), 0036 (control), 0037 (preview), 0038 (invitations) |

The partition rule: **a vendor backend behind a config-selected seam is open** (a model provider,
an index, a carrier, a hub, a broker, a CDP provider: an installer brings their own key); **a piece
of running the paid product is hosted** (its surfaces, its money, its data feeds, its measurement,
its servers, its deploy). Two named exceptions to the user-facing list, moved for the same reason as
their neighbours: `imessage` is a fleet-wide surface on the deploy's Spectrum account, the same
class as Slack; `product.py` counts the funnel onto the fleet's metrics and reads `balance_purchase`,
which leaves with billing.

The open module talks through the terminal client and through any surface an installer writes on
the `surfaces` seam. It ships no browser portal.

**History.** The public repo starts from a fresh first commit holding the open tree at the cut. The
private repo keeps this repo's full history, deletes the open paths, and adds the `ufo` wheel as a
dependency. Nothing under `infra/`, `servers/`, or the deploy workflows ever appears in public
history. Which GitHub name each repo carries is a rename at the cut; the public package and CLI are
`ufo` and `ufoctl` either way.

### 2. Three kinds of boundary

| Boundary | Who crosses it | Mechanism | New here |
|---|---|---|---|
| Package | web, apps, slack, imessage, sources, billing, enrichment, flagship, census | In-process extensions installed from the hosted repo; the same `ufo.extension` entry point, the same `ufo.sdk`-only gate | Four SDK points (§4) |
| Process | control, egress, cache, preview, sandbox ingress | The internal RPC (§3) | One prefix, one version, one schema bundle |
| Client | evals | The terminal wire in, the operator JSON reads out, `ufoctl` for boot | Environment document `sections`; no in-process import |

Python parts stay in-process because the Manifest is live callables, because the balance decision
runs inside the admission transaction and before every round, and because `spec.md:35` fixes that
the package seam is not an HTTP seam. A process boundary there would be RFC 0015's `runner` rebuilt
for the one population it refused: first-party code the same people own.

### 3. The internal RPC

The RPC is the runtime's service API: every call between the `ufoctl serve` process and a process
the hosted repo runs. It is JSON over HTTP, request and response, one bearer per caller role. RFC
0015 chose JSON-RPC for a channel that needed callbacks mid-dispatch; none of these calls does, and
the Rust callers already speak reqwest JSON.

| Service | Verb | Caller → callee | Auth |
|---|---|---|---|
| `onboard` | `seat`, `membership`, `choices`, `fleet`, `invitations` | gateway → runtime | `UFO_ONBOARD_CONTROL_TOKEN` |
| `egress` | `authorize`, `resolve`, `meter`, `tool-bridge` | proxy → runtime | `UFO_EGRESS_CONTROL_TOKEN` |
| `cache` | `git-credential` | cache → runtime | `UFO_CACHE_CONTROL_TOKEN` |
| `sites` | `not-answering` | ingress → runtime | the signed report token |
| `preview` | `render` | runtime → preview | `UFO_PREVIEW_TOKEN` or a presigned PUT |

What changes:

| Change | Detail |
|---|---|
| One prefix, one version | Every runtime-served verb mounts at `/internal/v1/<service>/<verb>`. A breaking change ships `v2` beside `v1` for one release, because a rolling deploy meets both. |
| Contract as code | Every request and response is a `BaseModel` beside its handler. `ufoctl rpc-schema` writes `rpc/v1/schema.json`: the route table, every model's JSON Schema, and test vectors for the three signed tokens the parties share (`UFO_TOKEN_SECRET` signs the member bearer, the run token, and the probe token). The bundle is checked in, regenerated by a gate, and published as a release asset. |
| One check on each side | The three `*_contract.json` fixtures are deleted. Each Rust crate carries one test that loads the bundle and round-trips its serde types through every example; core's test does the same against its models. |
| The runtime owns its schema | `ufoctl rls-bootstrap` creates the RLS policies and the DBOS database from core's own metadata. `servers/control/src/rls.rs` and the `rls` subcommand go. The bootstrap Job runs `ufoctl migrate && ufoctl rls-bootstrap`. The gateway keeps its own `ufo_control` schema DDL. |
| No money in `seat` | `onboard/seat` creates the workspace, member, and main agent, then runs the active manifests' onboarding steps for a new workspace, the flow `ufoctl init` already runs. The signup grant is the billing extension's onboarding step (§4). |

`RESERVED_HOST_PREFIXES` (`serve.py:211`) and the shared `UFO_TOKEN_SECRET` stay: the reserved
paths are part of the published contract, and the token codecs are what the bundle's test vectors
pin.

### 4. New extension interfaces

Four points, each with its producer and consumer in one change.

| Point | Shape | Producer | Consumer |
|---|---|---|---|
| `hooks` event `admission` | Payload: `phase` (`admit`, `fold`, `round`, `spawn`, `resume`, `off_turn`), workspace, member, agent, resolved model, `pending_micro_usd`, `turn_has_debited`, the `ToolIntent` if any. Outcome: `None` (allow), `Park(message)`, `Deny(message)`. | billing | The seven call sites `BalanceGate` runs from today. `SpendEvaluator` (caps) stays core and runs first. A park's message is stored with the turn's parked state; the hub tail reads it back instead of re-asking a gate. |
| `hooks` event `usage_recorded` | Payload: the ledger row just written. `HookContext.connection` carries the writer's open transaction, so the hook writes its own tables in the same commit. Outcome: `Debited(micro_usd)` or `None`; core writes `ledger.debited_micro_usd` from it. | billing | The five ledger recorders (`record_turn_usage`, `record_workspace_usage`, `record_sandbox_tokens`, `record_image_usage`, `record_video_usage`). |
| `cli` | `click.Group | None`, mounted as `ufoctl <extension> …` with an operator-scoped `ExtensionContext` factory. | billing (`ufoctl billing show|credit|reserve`) | `cli.py` mounts it; the sample extension declares one, so `_conformance_failures` holds. |
| `page` object kind in core | `PAGE_OBJECT` moves from `extensions/sources/ufo_ext_sources/pages.py` to `host/kinds/pages.py`; `PAGE_KIND` is exported from `ufo.sdk.objects`. | core | memory, sources |

Two existing seams widen without a new point:

- **Environment documents gain `sections`.** Every prompt text a model reads gets a name: core's
  prompt parts (`runtime/prompts/*.md` and the constants in `render.py`), every extension
  `prompt_sections` entry, every pack section. A document addresses `sections.<name>` with `text`
  or `replace`, the same grammar `profiles.<name>.prompt` already takes. An ablation arm is then one
  document against one shared stack for core and extension text alike, and no arm edits source.
- **`onboard/seat` runs onboarding steps.** Today only `ufoctl init` does. The billing extension's
  step grants the signup balance from its own configuration.

Cross-extension imports keep working with one rule: an import of another extension's module is a
dependency declared in package metadata, and across the two repos it points down only. Web
importing `ufo_ext_ufo` and `ufo_ext_sites`, slack importing `ufo_ext_connectors`, and two apps
importing `ufo_ext_coding` are hosted packages depending on open ones. Web importing
`ufo_ext_slack` and `ufo_ext_imessage` stays inside the hosted repo. The open CI installs no hosted
package, so the reverse direction cannot pass.

### 5. Each moving part

**Servers.** Move as they are. The egress proxy, cache, and preview crates change only their route
prefixes and the contract test. The gateway loses `rls.rs`, the `rls` subcommand, and its copies of
core's seat and credit semantics. The gateway image bakes the terminal client from the open repo's
release asset instead of `servers/control/clientbin/`.

**Billing.** `extensions/billing` holds `balance.py`, the gate as an `admission` hook, the debit as
a `usage_recorded` hook, the Stripe and Metronome code from today's `metronome` module, the
`billing` CLI group, and migrations that adopt `workspace_balance` and `balance_purchase` in place
and re-key today's `metronome` rows in `ext_store`. Leaves core: `BalanceGate`, `balance.py`,
`BILLING_ACTION` and `admits_spent_balance` (`schema/records.py:219-222`), the `manage_billing`
allowlist entry, the `billing_url` field threaded through `Admission`, `HubTailer`, the engine,
`Subagents`, and the runtime record, `SIGNUP_GRANT_MICRO_USD` and `SIGNUP_RESERVE_MICRO_USD`, and
`ufoctl balance`. Stays core: `ledger`, `ledger_export`, `spend_cap`, `turn.billing_identity`,
`SpendEvaluator`, the usage-export seam, the spend rollups, `ufoctl spend-cap`, and the rate card in
`harness/models/pricing.py`. The per-round cost is unchanged: the hook keeps the same five-second
negative cache for a workspace without a balance row, and `accounting.py` stops importing the
private `_forget_absent_balance`.

**Web, apps, Slack, iMessage.** Move as they are. Core replaces `PORTAL_SURFACE` and `WEB_SURFACE`
with `home_surface(manifests)`, which already names the surface claiming `home`. `PortalKind`,
`PortalSkill`, `ActionView`, the conversation-slots seam, the `writeback` and `mid_turn_reply`
tables with both pollers, and `onboard/seed.py` (generalised to the home surface) stay: they are the
surface seam, and an installer's own surface uses them. Core tests that import `ufo_ext_web` (2),
`ufo_ext_slack` (5), or `ufo_ext_sources` (6) move into those extensions' test trees or are
rewritten against the sample extension; the terminal surface's tests stop importing slack, and the
sites tests stop importing web.

**Sources.** `extensions/sources` moves with its 55 providers, three object kinds, two hooks, one
job, and the `direct` auth proxy. `runtime/sources/`, the three tables, `source_sync`, the
`page_change` runner, `FolderSource`, and `ufo.sdk.sources` stay: memory and gbrain ride them, and
an installer's folder source does too. Memory imports `PAGE_KIND` from `ufo.sdk.objects`.

**Evals.** `evals/`, `core/tests/evals/`, `extensions/eval_env`, and the eval packs move. The
runner becomes a client:

| Today | After |
|---|---|
| `init_db`, `load_manifests`, `context_for`, `MemberAdmission.admit` in-process | `ufo --remote --json` admits; the JSON event stream carries the turn id and terminal |
| Turn-row poll, `DBOSClient`, transcript blob decoded with the private codec | The debugger surface's operator JSON reads (`api/turns/{id}`, `api/turns/{id}/steps`, conversations) |
| `stack.py` builds core `Config` objects | `stack.py` writes `ufo.toml` and runs `ufoctl migrate`, `ufoctl init`, `ufoctl serve` |
| Suites write 19 core tables | Suites seed through the product's own doors: `ufoctl` operator verbs, object verbs over the terminal wire, `ufoctl billing credit`; state a suite fabricated as rows is produced by a real turn |
| Arms edit source in a git worktree per arm | One document per arm addresses `sections.<name>` against one shared stack |
| Core tests import `evals.*` | `WorkspaceDriver`, `CapabilityCase`, `exact_scorer`, `InProcessTarget`, `UNNAMED_TOOL`, `TRANSIENT_ERROR_CLASSES` move to `testsupport/ufo_testsupport/`; evals import them from there |

**Product census.** `product.py` and its job move to a hosted extension `jobs` handler reading
through `ufo.sdk` (`seats`, installations, connector grants and agents through the object kinds,
turns through `trajectories.read`) and through the billing extension's own tables for the paid
stage. The `_census_period_failures` gate moves with it.

### 6. Versions, releases, gates

| Concern | Rule |
|---|---|
| Version pin | The hosted repo pins `ufo==X.Y.Z`. The open repo publishes a wheel, the terminal client binaries, the sandbox image, and `rpc/v1/schema.json` per release. |
| Compatibility | A hosted extension pins the `ufo` version it was tested against. Rolling deploys keep RFC 0043's `MOVED_MODULES` rule: a persisted class that moves lands with its alias. |
| SDK gate | `_sdk_import_failures` moves into `ufo_testsupport` as a function both repos' `gates.py` call over their own trees. |
| Gates that move | Portal (`_portal_style_failures`, the `_app_*` gates, `_kit_catalogue_failures`, `_framed_stat_failures`, `_composition_rhythm_failures`, `_waiting_line_failures`, `_consent_mark_failures`, `_sse_listener_failures`, `_skill_palette_failures`), terraform (`_declared_flag_failures`, `_shared_singleton_failures`, `_retired_resource_failures`), census. |
| Gates that stay | Everything over core, the SDK, the harness, migrations, skills, the directive wire, and the sample extension's conformance. |
| Open CI | Installs only the open set, runs the open tests and gates, boots `ufoctl serve` with the open `assistant` pack, and drives one real turn through the terminal client. |
| Hosted CI | Installs the pinned `ufo` wheel plus its own packages, runs its tests and gates, builds the servers, and runs the contract test against the bundle the pin shipped. |

### 7. Landing order

Every unit lands in this repo before the cut, so the tree partitions cleanly while one CI still
proves the union.

| Unit | Ships | Proof |
|---|---|---|
| 1 | Eval helpers into `testsupport`; `page` kind into core; `PORTAL_SURFACE` and `WEB_SURFACE` replaced by the home surface; core and extension tests stop importing web, slack, sources, eval_env | Core tests pass with `evals/`, `extensions/web`, `extensions/slack`, `extensions/sources` uninstalled |
| 2 | `/internal/v1/*`, `BaseModel` contracts, `ufoctl rpc-schema`, the bundle, the Rust round-trip tests, `ufoctl rls-bootstrap`, `seat` runs onboarding steps and grants nothing | The three fixtures are gone; each crate's test passes against the checked-in bundle; a fresh Postgres bootstraps from `ufoctl` alone and RLS holds |
| 3 | `admission` and `usage_recorded` events, `cli` point, `extensions/billing`, the core tear-out named in §5 | A spent balance refuses a turn with the extension's line; a `manage_billing` intent is admitted by the hook alone; a BYOK row debits nothing; per-round latency measured unchanged |
| 4a | `sections` in the environment document; the eval runner boots, admits, and reads as a client | The recorded ablation arms rerun as documents against one stack with the same verdicts |
| 4b | Suites seed through verbs, one suite per commit | `evals/` imports nothing outside `ufo.sdk`, `ufo_testsupport`, and the terminal client |
| 5 | The open `assistant` pack; the census as a hosted extension; the partition declared in one file and gated; the open-only CI job | The open-only job is green on this repo's `main` |
| 6 | The cut: the private repo from this history minus the open paths, the public repo from a fresh commit, the first release, hosted CI green against the pinned release, one deploy from the hosted repo | The deployed fleet runs the hosted repo's images against the released wheel |

### 8. Cost

| Unit | Engineer-days |
|---|---|
| 1 | 2 to 3 |
| 2 | 5 to 8 |
| 3 | 5 to 8 |
| 4a | 5 to 8 |
| 4b | 10 to 20, about one day per suite |
| 5 | 3 to 5 |
| 6 | 5 to 8 |

Units 1, 2, and 3 are independent of one another. Unit 4b is the long pole and can run beside 5.

## Doctrine fit / implications

- **Core doctrine holds and gets sharper.** The prepaid balance was a hosted product rule living in
  core; it becomes an extension on two hook events, which is what "if a capability can be an
  extension, it is not core" asks. Caps and metering stay, because a self-host deploy needs them and
  the spec names accounting as core.
- **One shape.** Money constants leave `seat`; a surface name leaves `records.py`; a Rust binary
  stops enumerating core's tables. Each fact has one home again.
- **Enforce, don't document.** The RPC contract is generated from the models that serve it and
  tested on both sides from one bundle. The repo partition is a declared file and a gate, not a
  convention.
- **Both ends or neither.** Each new point lands with its producer and consumer: the billing
  extension and the seven gate sites; the CLI group and the mount; the `page` kind and memory's
  import; `sections` and the reran arms.
- **Hot paths.** Nothing that was a local query becomes a network call. The one per-round decision
  moves from a core function to an in-process hook with the same cache.
- **Prompts.** Every prompt text becomes addressable, so a wording change anywhere is measurable
  the way `CLAUDE.md` §Prompts requires.
- **What the reader loses.** A hosted change that also needs a runtime change is two PRs and a
  version bump. That is the price of the seam, and the gates make the second PR small.

## Alternatives

| Alternative | Rejected because |
|---|---|
| Run the hosted Python out of process over the RPC (surfaces, billing, sources as remote services) | The Manifest is live callables; `SurfaceContext` is 120 methods of read projection that would each need a wire shape; the balance decision would become a network call before every round, which RFC 0043 rejected for the harness and `CLAUDE.md` forbids masking with retries. RFC 0015 refused this for first-party code. |
| Keep one repo and publish the open subset as a filtered mirror | The seams never have to be real: hosted code keeps importing anything, and a public contributor cannot build or test what they see. |
| Move the sources framework and its three tables out with the providers | Memory and gbrain lose the page feed they ride, and a folder source needs a new seam for the open module. If the framework must leave, it takes the tables into the extension's migrations and memory needs a page-feed point of its own. |
| Let evals keep a direct DSN and import `ufo.schema.tables` | A second repo silently depends on core's table shapes with no gate, which is the coupling this RFC exists to name. |
| gRPC and protobuf for the RPC | A codegen toolchain neither side has; the Rust callers already speak JSON, and RFC 0015 rejected it for the same reason. |
| Keep the balance in core because "no balance row" already means self-host | True today and still dead weight: 600 lines, two tables, a hard-coded extension name, and a URL threaded through five types, for a rule only the hosted product applies. |

## Open decisions

1. **Names.** The working names are the `ufo` GitHub repo for the public module and a new private
   repo for the hosted product. Renaming at the cut is one act; the package and CLI names do not
   change.
2. **Publish channel.** PyPI for the `ufo` wheel from the first release, or a private index until
   the public repo opens. The hosted pin works either way.
3. **Sites and the apps.** Sites stays open as a capability and its ingress is core; the nine
   `app_*` extensions move because the portal builds and serves their pages. If the open module
   should ship apps without a portal, the app agents split from their pages first.
