# App bench sequence

This sequence improves application creation from a measured three-app baseline to the guided
creation flow. A phase passes only when its gate has recorded evidence. The status values are
`Passed`, `Active`, `Queued`, and `Blocked`.

## Current state

| Phase | Goal | Status | Gate evidence |
|---|---|---|---|
| 1. House system | Give pages of ours one exact visual system. | Passed | `ufo-style` tokens, dependent skills, routing checks, and theme tests. |
| 2. App bench | Measure three clear apps and make every result inspectable. | Active | Run `be059bfd` passes 2/3 interactive homepage cases; timeout cases retain completed durable model and tool steps. |
| 3. Instructions | Add only skill or tool text that the bench proves useful. | Active | Matched runs `76d66a43` and `7d93c1f3` make the first browser result observable and pass the board four-call gate with lower time, tokens, and cost. |
| 4. App agents | Test dedicated profiles and models on matched work. | Queued | Quality, time, token, and cost comparison. |
| 5. Build pipeline | Split independent build work behind explicit artifacts. | Queued | Matched monolith and pipeline comparison. |
| 6. Creation flow | Drive intent, approval, build, preview, and publication from chat. | Queued | Realistic request to durable, usable application. |

Update this table and the phase evidence in the same change that satisfies a gate. A changed case,
rubric, grader, tool set, model, or time bound changes the comparison identity.

## Measures

Every run records these values separately:

| Measure | Meaning |
|---|---|
| Deterministic success | Files, tokens, contrast, viewport fit, clipping, console output, and browser state. |
| Trajectory success | Required skills and build actions occurred in a valid order. |
| Visual success | A vision model judged information density, hierarchy, type, shape, spacing, and finish from screenshots. |
| Browser success | Playwright completed named interactions and observed the required state. |
| Reliability | Scored passes, exclusions, and failure reason by grader layer. |
| Efficiency | Wall time, model time, tool time, tokens, and cost. |

An infrastructure exclusion does not become a quality failure. A model or visual-judge failure stays
visible even when a deterministic rerun proves that the artifact meets the contract.

## Phase 1: House system

Goal: constrain pages of ours before measuring application quality.

The system has one token source, one routing target for exact house-style questions, and dependent
application and design skills. Member-supplied design direction replaces the house system whole.

Gate:

- The portal tokens and skill tokens match.
- App creation, website building, and document design load the house system.
- Positive and neighboring negative routing cases pass.
- No application prompt repeats the token values.

## Phase 2: App bench

Goal: measure end-to-end creation quality on three clear, self-contained applications before the
case set or build architecture becomes complex.

| Gate | Status | Evidence |
|---|---|---|
| A. Harness integrity | Passed | Run `47ecdee1`: all three probe-owned HTML snapshots, audits, and light/dark screenshots are report-visible. A timeout regression proves that completed model rounds, tool calls, results, and named timing remain report-visible after cancellation. |
| B. Three-app baseline | Passed | Runs `6fec0e22`, `6cf2def1`, and `2f7f5ee5` pass 9/9 presentation cases. |
| C. Interaction | Active | Run `be059bfd`: notes and brief pass with 10 and 9 browser-proved state changes; their saved apps also pass the stricter accessible-name and page-state audit. Board reaches the 900-second bound without a terminal reply. |
| D. Efficient proof | Active | Run `c9f726bc` uses one preview start and three successful browser calls. The app misses one contrast check, so the quality gate remains open. |

### First three apps

| Case | Required screen | Initial difficulty |
|---|---|---|
| `kanban-board` | `Build your interactive project board homepage for the ops team.` | Choose useful workflow structure, information, density, and controls. |
| `call-notes` | `Build your interactive internal notes homepage for one customer call.` | Choose useful call context, outcomes, follow-up structure, and controls. |
| `daily-brief` | `Build your interactive daily brief homepage for the team.` | Choose useful status, change, priority, source structure, and controls. |

The evaluated turn must deploy the site and bind it with `set_homepage`. After the turn ends, the
harness locates the generated HTML, serves it, runs the browser audit, exercises accessible
controls, captures both colour schemes, and preserves the generated HTML beside a static DOM
snapshot. The HTML report shows the screenshots, trajectory, grader verdicts, audit, downloads, an
inert snapshot, and an interactive preview with local scripts and styles embedded but no network
or parent access.

Select `ufo-app-bench` explicitly on a deploy whose `[sandbox] backend` is `docker`. The default
suite set omits this Docker-only browser probe.

The member query names only the product, audience, homepage, and need for interaction. The model
sees no path, file shape, server, audit, screenshot, sharing, reply, layout, component,
content-field, action, token, count, or viewport requirement. These are harness work or hidden
grading facts. The case tests product judgment, not prompt or packaging imitation.

### Historical query ablation

The matched arms use the same hidden rubrics, fact floors, tools, concurrency, and 900-second bound.

| Arm | Run | Result | First-screen evidence |
|---|---|---|---|
| Detailed layout and content query | `934316e9` (`sha256:1e769d78`) | 1/3 | Board passed all eight visual criteria; notes were 1,763px tall; brief was 1,782px tall. |
| Product and audience query | `6bb9e7e0` (`sha256:cfdee035`) | 0/3 | Board was 1,148px tall; notes were 2,317px tall; brief was 2,677px tall. |

The detailed query supplied enough structure to make one case pass. It did not make the other two
cases fit the first screen. The natural query exposes the intended baseline failure: the builder
must choose a compact information structure without receiving the answer in the request.

Gate A — harness integrity:

- A run ends with a terminal result or a named external failure.
- The suite-only bound covers the measured successful turn distribution; unrelated suites keep
  their own bound.
- The model receives the member query unchanged and no grader delivery instructions.
- The post-turn probe owns file discovery, serving, browser measurement, screenshots, and report
  artifacts.
- Each deterministic fact has one grader owner. The visual grader does not estimate a fact already
  measured from the page.
- The report opens every generated app without giving it access to the report origin or network.

Gate B — three-app presentation baseline:

- All three cases load `website-building` before the build.
- Every case passes its deterministic artifact and browser audit.
- Every case has a visual verdict for each criterion and both colour schemes.
- Each desktop screenshot meets its case-specific above-the-fold fact count without wasting the
  working area on oversized or decorative regions.
- Every case is visible and usable in the HTML report.
- Three matched full runs establish the pass distribution. The acceptance threshold is frozen from
  that distribution before any instruction treatment runs.

Gate C — interaction:

- All three cases load `website-building` before the build.
- Every case completes `deploy_website` before `set_homepage`.
- Every case passes its deterministic artifact and browser audit.
- Every case exposes at least two accessible controls that produce distinct visible state changes.
- Every case has a visual verdict for each criterion and both colour schemes.
- Each desktop screenshot meets its case-specific above-the-fold fact count without wasting the
  working area on oversized or decorative regions.
- Every case is visible and usable in the HTML report.
- Three matched full runs establish the pass distribution. The acceptance threshold is frozen from
  that distribution before any instruction treatment runs.

Complexity ladder:

Add one capability axis at a time. Keep earlier cases unchanged as regression controls.

| Rung | Added capability | New browser proof |
|---|---|---|
| Presentation | One screen and static sample data. | Load, viewport, accessibility, and console checks. |
| Interaction | Filters, forms, dialogs, selection, or drag actions. | Named action changes the expected visible state. |
| Local data | Create, edit, delete, reload, and empty/error states. | State survives the required boundary and invalid input is refused. |
| Backend | Typed API, durable records, and server failure states. | UI action changes server state and reload reads it back. |
| Connected work | One granted external source or action. | Exact scoped request, durable result, and member-visible use. |
| Permissioned work | Multiple members, roles, or background work. | Each role sees and changes only its allowed state. |

Each rung passes when deterministic and browser checks are stable, the visual pass distribution is
recorded, and its time, tokens, and cost remain separate from the earlier rungs.

## Phase 3: Instructions

Goal: add skill material or tool descriptions only where a measured failure needs them.

Use the levers in this order:

| Lever | Question | Treatment gate |
|---|---|---|
| Routing | Did `website-building`, its `webapp` child, and the house design dependency load instead of `create-application` or a neighboring skill? | Positive and negative loading cases improve before body text changes. |
| Skill content | Did the loaded material direct information structure, density, implementation, and browser proof? Does deleting a section remove a distraction or reduce cost without a quality loss? | Matched app quality improves, or cost and latency fall with no quality loss. |
| Blocks or templates | Does a repeated implementation step remain expensive or unreliable after routing and skill-content treatments? | A reached-for block improves the named layer on matched runs; an unused or neutral block does not ship. |

### Compact application QA

The `call-notes` match uses the same natural query, model, server tool, graders, and 900-second
bound. The compact arm reads only the application QA reference. The comparison arm reads the full
Playwright reference and the application reference.

| Measure | Compact `c9f726bc` | Full `d4729087` | Change |
|---|---:|---:|---:|
| Wall time | 550.8s | 711.0s | -22.5% |
| Model time | 403.7s | 421.7s | -4.3% |
| Tool time | 133.0s | 278.6s | -52.3% |
| Model rounds | 21 | 30 | -30.0% |
| Tokens | 1.59M | 2.63M | -39.5% |
| Cost | $2.22 | $2.89 | -23.0% |
| Browser QA | 3 successful calls | 2 failed calls | Compact proof passes. |

Both artifacts fail the whole case. The compact artifact has one small light-mode button at 4.2:1
instead of 4.5:1. The full-guide artifact clears the independent page audit but its agent browser
proof fails. Keep the phase active until matched repeats show the latency gain with no quality
loss.

### Self-directed efficiency loops

The unchanged board case keeps the quality checks and a four-browser-call limit.

| Run | Treatment | Wall | Model | Tool | Rounds | Tokens | Cost | Result |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `88e64bf1` | Three-app control | 576s | 125s | 439s | 20 | 1.96M | $2.79 | Board delegates a second complete build and uses seven browser calls. |
| `4cec4574` | One build owner | 559s | 452s | 98s | 22 | 1.91M | $2.70 | The 402-second child build is gone. A guessed selector waits 30 seconds; one replay fails; five browser calls. |
| `7b94e2b1` | Bounded, isolated browser calls | 654s | 590s | 54s | 26 | 2.31M | $3.19 | No browser call fails or waits 30 seconds. The first result is not printed and is repeated; a final hover repair makes five calls. |
| `76d66a43` | Matched control | 497s | 415s | 71s | 24 | 2.07M | $3.11 | Six browser calls; the first fails on an ambiguous accessible name and the second returns empty stdout. |
| `7d93c1f3` | Observable result and repair budget | 435s | 372s | 51s | 18 | 1.43M | $2.54 | Passes 7/7 semantic criteria with three successful browser calls and 11 visible state changes. |
| `efdcae3e` | Batched-write control | 482s | 425s | 46s | 26 | 2.16M | $2.71 | Passes with separate initial writes. |
| `bafca005` | Batched initial writes | 608s | 547s | 51s | 23 | 2.08M | $3.02 | Groups all three writes in one round and passes, but later setup fragments and total time and cost regress. |
| `189c71f9` | Font-flow rollback | 623s | 548s | 64s | 31 | 2.81M | $3.30 | Passes, but infers the visible font assets, reconstructs their setup, and fragments browser QA. |
| `3c6afb81` | Copy-ready local house fonts | 574s | 518s | 45s | 21 | 1.78M | $2.49 | Passes with the approved local faces, three browser calls, and no font-network repair. |
| `3943467d` | Structured-result schema, current Skill | 900s | 582s | 303s | 29 | — | — | Excluded timeout with five browser calls. |
| `cd26f70d` | Structured-result schema and Skill | 900s | 624s | 265s | 25 | — | — | Excluded timeout. The field returns later batches, but selector and browser failures produce eight calls. |
| `2c931442` | Old JavaScript contract | 873s | 558s | 304s | 37 | 3.55M | $3.85 | Completes but fails the four-call gate with seven browser calls. |
| `20da5079` | Kept branch, all three apps | 391–509s | 337–446s | 44–53s | 23–25 | 1.92–2.01M | $2.17–$2.68 | Passes 3/3 with four browser calls per app and 8–10 proved state changes. |

Keep the observable result and repair-budget treatment. Its one matched board sample reduces wall
time 12.5%, rounds 25.0%, tokens 30.9%, and cost 18.3% without weakening browser proof. A speed
distribution still needs matched repeats on all three cases.

Keep the local-font treatment. Against its matched rollback, it reduces wall time 7.9%, rounds
32.3%, tokens 36.6%, and cost 24.5%. Both arms pass. The rollback still sees the mounted asset
names, but it has no copy-ready relative layout and uses ten shell calls and standalone browser
scripts. The treatment copies the approved portal faces with the token stylesheet and makes no
font request to the network.

The next build-architecture comparison has three arms: the compact monolith, one model round that
emits all disjoint initial writes, and a typed three-piece fan-out with disjoint file ownership.
Do not use the current whole-site subagent profile for the fan-out. It shares files, ports,
Chromium, deployment, and homepage binding, so parallel children race instead of composing.

| Arm | Build shape |
|---|---|
| A | Compact monolith. |
| B | One model round emits all disjoint initial writes. |
| C | The parent writes one app contract. Markup, style, and behavior profiles write disjoint files in parallel. The parent alone integrates, serves, checks, deploys, and binds. |

Run all three unchanged app cases three times. Keep a new shape only when it has no lower pass count
or new timeout, every case is faster, median wall time falls at least 20%, and tokens and cost rise
no more than 25%. If B matches C, keep B and delete the profiles.

One loop changes one lever:

1. Group failures by routing, planning, implementation, visual finish, browser behavior, and
   packaging.
2. Add a focused eval from the real failure and its nearest negative case.
3. Run the unchanged control and one treatment through the ablation runner.
4. Keep only a treatment that improves the named layer without a neighboring regression.
5. Record quality, wall time, model time, tool time, rounds, tokens, and cost before the next loop.

### Recorded loops

| Lever and arm | Run | Result |
|---|---|---|
| Routing control | `9ac490ce` | 0/3 concise app requests load `website-building`. |
| Routing neighbor control | `a00af175` | 6/6 website, full-stack, agent-app, brief-app, and scheduling neighbors route correctly. |
| Routing candidate | `ea2da6dd` | 9/9 app requests and neighbors route correctly. |
| Skill-body control | `451ff4a4` | 0/3; every case loads the skill, asks for data, and writes no HTML. |
| Sparse-preview rule | `2bd6d30e` | Notes and brief build; board exceeds 900 seconds in more than 40 delegated rounds. |
| Compact skill candidate | `a3ad1dbe` | 1/3; all three build and pass routing, artifact, contrast, overflow, and above-fold fact checks. Brief passes 8/8 visual criteria; board and notes each miss one rendering-consistency criterion. |
| Exact-head candidate | `47ecdee1` | 1/3; notes passes 7/7 semantic criteria. Board fails one 2.78:1 owner label; brief fails the house monospace rule for data values. |
| House token roles | `50d4f603`, `08c3c4db` | Notes and brief pass 7/7. The matched board turn stalls in an external model stream; its focused rerun passes 7/7. |
| Static sparse previews | `32b12674` | Rejected at 1/3. It does not stop browser QA, increases board work to 48 rounds, and regresses notes contrast and brief density. |
| Direct static deployment | `ce060ff4` | Rejected at 0/1. The board still runs browser QA for 34 rounds, then fails contrast. |
| Inline browser setup facts | `eada3470` | Rejected despite 7/7 quality. The board still repeats setup failures and rises to 41 rounds and 780 seconds. |
| REPL contract | `d3b3b37f` | Board passes 7/7 in 33 rounds. ES-module import and the Docker workspace fix remove the first setup failures; standalone scripts remain. |
| REPL-only browser checks | `993ec161` | Incomplete at 43 rounds and 862 seconds. Three REPL calls return valid output but hold Chromium open to their tool limits. |
| Bounded REPL browser calls | `c3d48d77`, `ed5d8efe` | Board passes 7/7 in 24 rounds, then the full run passes 3/3 in 18–23 rounds. |
| Bounded REPL replication | `bc660790` | 2/3. Notes and brief pass; board uses the raw attention accent as text and fails AA at 2.78:1. |
| AA-safe accent roles | `2b9383ae`, `6fec0e22`, `6cf2def1`, `2f7f5ee5` | Board passes 7/7. Three full runs pass 9/9 cases in 16–26 rounds. |
| Interactive homepage control | `be059bfd` | 2/3. Notes and brief deploy, bind, and prove 10 and 9 state changes. Board reaches 900 seconds without a terminal reply. |
| Direct application ownership | `88e64bf1`, `4cec4574` | The board stops delegating a second complete build. Wall time falls 3%; model work replaces most of the removed child time. |
| Bounded browser failure rules | `7b94e2b1` | All browser calls succeed and the prior 30-second wait and replay error disappear. The case still fails at five calls and is slower, so no efficiency claim ships. |
| Observable browser result and repair budget | `76d66a43`, `7d93c1f3` | The matched board treatment passes with three browser calls versus control failure at six. Wall time falls 12.5%, rounds 25.0%, tokens 30.9%, and cost 18.3%. |
| Batched initial writes | `efdcae3e`, `bafca005` | Rejected. The three writes share one model round and total rounds fall by three, but wall time rises 26.2% and cost rises 11.4% after later setup fragments. |
| Local house fonts | `189c71f9`, `3c6afb81` | Both arms pass. Copy-ready local fonts reduce wall time 7.9%, rounds 32.3%, tokens 36.6%, and cost 24.5%; the rollback infers the mounted assets but fragments setup and browser QA. |
| JavaScript structured result | `3943467d`, `cd26f70d`, `2c931442` | Rejected and reverted. Both new-contract arms reach 900 seconds; the old contract completes in 873 seconds but fails at seven browser calls. The field returns successful batches, but it does not prevent selector errors or browser loss. |
| Full kept-branch gate | `20da5079` | Passes 3/3. Every app uses one preview server and four browser batches, passes 7/7 semantic criteria, and proves 8–10 visible state changes. Wall time is 391–509 seconds in 23–25 rounds. |

The body control uses 38–53 seconds, 4–5 rounds, 0.21–0.26 million model tokens, and
$0.22–$0.27 per case because it stops before the build. The compact candidate uses 517–714 seconds,
30–47 rounds, 2.20–3.57 million model tokens, and $2.20–$2.99 per case. These are quality gains, not
an efficiency win. The house-token treatment uses 462–617 seconds, 30–33 rounds, 2.15–2.44 million
model tokens, and $2.04–$2.39 per completed case. The bounded-REPL treatment uses 250–435 seconds,
18–23 rounds, 1.16–1.78 million model tokens, and $1.32–$2.15 per case. Every case keeps its 7/7
quality result while wall time, rounds, tool calls, and tokens fall. The final token-role treatment
uses 358–477 seconds, 20–25 rounds, and passes all three cases without relying on raw accents for
normal-size text.

The three final full runs use 251–516 seconds, 16–26 rounds, 1.05–2.39 million model tokens, and
$1.28–$2.96 per case. Run `23145c11` is excluded from the quality distribution because Docker
could not allocate a network before any turn started. The scoped cleanup removed only containers
and networks that belonged to recorded app-bench conversations; its retry completed normally.

Run `be059bfd` is the first interaction control, not an instruction improvement claim. It keeps the
full browser workflow and adds deployment, homepage binding, and visible state checks. A current
deterministic audit of the saved notes and brief apps confirms 10 and 9 state changes with no
console errors after hash-only changes and unnamed controls are excluded. The board timeout remains
a measured failure; it does not justify removing browser QA or increasing the time bound.

The audit permits vertical scrolling and leaves first-screen density to the explicit fact rubric.
It still refuses horizontal overflow, visible clipping, low contrast, and console errors. Standard
screen-reader-only labels are not visible clipping.

Gate:

- Positive and negative loading cases pass.
- The matched app cases improve on the named failure layer.
- No duplicated instruction survives the ablation.
- Quality, time, tokens, and cost are reported for each arm.

## Phase 4: App agents

Goal: determine whether one or more dedicated app agents and models improve the bench.

Run the main agent and each candidate profile against the same case digest, tools, sandbox, time
bound, and sample count. Compare a single specialist, stage specialists, and model choices only when
the prior comparison gives a reason to add another arm.

Gate:

- A specialist improves a named quality layer or reduces cost or time without a quality loss.
- Tool grants remain narrower than or equal to the work the profile performs.
- Handoffs preserve the member request, files, and grader-visible trajectory.
- A profile with no measured benefit does not ship.

## Phase 5: Build pipeline

Goal: run independent build work in parallel without making integration implicit.

| Stage | Artifact contract |
|---|---|
| Product shape | Screens, states, interaction flows, and acceptance checks. |
| Data shape | Entities, transitions, API contracts, permissions, and seed data. |
| Frontend | Components and browser behavior against the data contract. |
| Backend | Durable behavior against the same data contract. |
| Integration | One runnable application with producer and consumer connected. |
| Package and proof | Start command, audit, screenshots, browser run, and shared deliverables. |

Parallel work starts only for artifacts that do not depend on each other. The integration stage owns
contract conflicts; it does not ask a later stage to infer missing fields or behavior.

Gate:

- Every artifact has one producer and one consumer in the same run.
- The integrated app passes the same case that its monolithic baseline passed.
- Parallel work reduces wall time or improves quality without increasing unresolved handoffs.
- Backend and permission cases prove durable state through the browser, not through a fake.

## Phase 6: Creation flow

Goal: let a member create and use an application through one guided chat flow.

Sequence:

1. The Apps screen admits `Build me a new app.` as a chat turn.
2. `create-application` opens the fixed phase board and interviews the member.
3. The agent presents one application specification and waits for approval.
4. The build runs through the best measured agent and pipeline configuration.
5. The member sees progress and sandboxed previews while the run continues.
6. The approved application, homepage, and access rules become durable together.
7. The member opens the application and completes its benchmark task.

Gate:

- The opening and interview trajectory cases pass.
- No application object, site, grant, or publication act occurs before the member approves it in
  chat.
- A realistic request produces durable application and site records.
- The member can open the page, perform the required browser task, and return to its conversation.
- A failed build ends with a terminal state, keeps its evidence, and creates no partial published
  application.

## Benchmark design sources

- [WebArena](https://arxiv.org/abs/2307.13854): reproducible environments and functional task
  completion.
- [BrowserGym](https://arxiv.org/abs/2412.05467): common observation, action, and experiment
  interfaces across task sets.
- [VisualWebArena](https://arxiv.org/abs/2401.13649): visually grounded tasks and multimodal
  evaluation.
- [OSWorld](https://arxiv.org/abs/2404.07972): executable environments and state-based reward
  functions.
- [Perplexity skill design](https://research.perplexity.ai/articles/designing-refining-and-maintaining-agent-skills-at-perplexity): eval-first routing, narrow descriptions, and body ablation.
