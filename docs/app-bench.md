# App bench sequence

This sequence improves application creation from a measured three-app baseline to the guided
creation flow. A phase passes only when its gate has recorded evidence. The status values are
`Passed`, `Active`, `Queued`, and `Blocked`.

## Current state

| Phase | Goal | Status | Gate evidence |
|---|---|---|---|
| 1. House system | Give pages of ours one exact visual system. | Passed | `ufo-style` tokens, dependent skills, routing checks, and theme tests. |
| 2. App bench | Keep three presentation controls, one rework control, and a connected data-backed app set. | Active | Run `357d81f3` records the final ten-app boundary: 0/10 binary, 0.807 product, 0.733 process, and one Google provider failure. Exact source coverage, first-screen density, interaction, visual taste, and process now report separately. |
| 3. Instructions | Add only skill or tool text that the bench proves useful. | Passed | Run `159d6126` keeps the compact build contract at 0.954 product versus 0.846. The QA sentence and component guidance are rejected. |
| 4. App agents | Test dedicated profiles and models on matched work. | Passed | Gemini 3.7 Flash at `medium` is the builder. Optional Luna and Fable roles lose to the monolith. Final Luna copy run `7c904a61` passes 3/7 and does not establish a stable copy stage. |
| 5. Build pipeline | Build through one worker and accept through deterministic artifacts. | Passed | The production path uses one Gemini delegation, a compiler-approved source digest, an independent product audit, and no routine Opus repair turn. |
| 6. Creation flow | Drive intent, approval, build, preview, and publication from chat. | Passed | Isolated A07–A10 reports pass 4/4. A10 retains one blocked build, then deploys on one later member-approved attempt without a partial publication. |

Update this table and the phase evidence in the same change that satisfies a gate. A changed case,
rubric, grader, tool set, model, or time bound changes the comparison identity.

## Measures

Every run records these values separately:

| Measure | Meaning |
|---|---|
| Binary success | Every hard product and process gate passes. |
| Product score | Equal mean of delivery, design fidelity, source, above-fold density, page audit, interaction, action, and visual taste. |
| Process score | Equal mean of Skill loading, direct file ownership, and efficient browser QA. |
| Deterministic success | Files, source facts, density, contrast, viewport fit, clipping, console output, and browser state. |
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
| E. Fixture realism | Passed | Ten connected cases use fixed, transformed connector records behind one digest. GitHub facts stay exact where allowed. Evaluations do not call live connectors. |

### First three apps

| Case | Required screen | Initial difficulty |
|---|---|---|
| `kanban-board` | `Build your interactive project board homepage for the ops team.` | Choose useful workflow structure, information, density, and controls. |
| `call-notes` | `Build your interactive internal notes homepage for one customer call.` | Choose useful call context, outcomes, follow-up structure, and controls. |
| `daily-brief` | `Build your interactive daily brief homepage for the team.` | Choose useful status, change, priority, source structure, and controls. |

### Connected app set

The three original cases stay unchanged as presentation and interaction controls. Ten connected
cases use transformed records shaped from current testing applications. A read-only testing scan on
22 August 2026 found live examples for eight cases. The fixture keeps their screen structure,
operating rules, status vocabulary, missing-data states, and cross-record relationships. It
replaces identifying and private values where the source requires it. GitHub records keep source
facts available to eval users, including repository identifiers, issue and pull request numbers,
titles, authors, heads, checks, reviews, labels, links, and relevant text. Secrets and unrelated
personal data never enter the fixture. A testing application with an empty queue supplies the
product shape, while the fixture supplies enough transformed rows to grade a populated screen. Raw
pages and unbounded connector responses do not enter the repository. Each case records its testing
source in `source_apps`; an empty list marks a synthetic extension.

Every case reads the same fixed data universe through only its granted providers. Parallel cases
therefore cannot replace another case's connector data.

Fixture source rules:

| Source | Keep | Do not keep |
|---|---|---|
| Testing application | Screen regions, labels, status vocabulary, operating rules, empty states, and useful edge cases. | Generated HTML, CSS, and implementation choices. |
| GitHub connector response | Field shape, relationships, and exact source facts available through the eval GitHub account. | Tokens, credentials, and unrelated personal data. |
| Other connector response | Field shape, nesting, nulls, value lengths, distributions, and cross-record relationships. | Identifying values, credentials, or private text. |
| Checked-in fixture | Transformed records, fixed timestamps, stable joins, and a content digest. | A live dependency or reversible substitution table. |

The capture is read-only. Transformation happens before data enters the repository. A fixture
review must prove that no secret or disallowed identifying value remains and that important source
facts, shape, and hard cases survive. Every evaluation reads only the checked-in fixture. The member
prompt does not contain fixture facts. The grader maps each prompt clause to the required connector
call, visible result, interaction, or copy rewrite.

| Case | Testing source shape | Connected sources | Product proof |
|---|---|---|---|
| `pre-meeting-briefs` | `meeting-briefs` | Calendar, email, Drive, GitHub | Three meeting briefs with prior context, live work, source age, and points to raise. |
| `meeting-tasks` | `meeting-scribe-home` | Drive, GitHub | Decisions, matched issues, proposed issues, owners, dates, evidence, and approval controls. |
| `issue-owner` | `intake-watcher-homepage`, `issue-fixer-home` | GitHub | Unassigned work, suggested owner, load, reason, and a prepared assignment intent. |
| `issue-planner` | `issue-fixer-home` | GitHub | Approved work, linked implementation, plan, risk, test proof, and approval state. |
| `code-review-queue` | `code-review-home`, `ufo-review-homepage` | GitHub | Ready, blocked, and waiting reviews with exact blocker and issue coverage. |
| `engineering-metrics` | `investor-update-home`, `pr-babysitter-homepage` | GitHub | Ready-to-merged cycles and latency, open-to-merge latency, linked issues, and weekly trend. |
| `pr-babysitter` | `pr-babysitter-homepage` | GitHub | Exact head and status, blocker, babysitter state, model, and prepared setting intent. |
| `startup-metrics` | `investor-update-home` | Stripe | MRR, ARR, churn, customers, revenue mix, invoice risk, activity, and balance. |
| `account-health` | Synthetic extension | HubSpot | Ranked renewal risk, owner, value, activity, tickets, and next action. |
| `candidate-review` | Synthetic extension | Greenhouse | Stage, schedule, scorecard coverage, ratings, evidence, and missing input. |

The model must discover, describe, and call each named connector tool. It receives no fixture file.
The deterministic grader then checks each prompt clause against its required calls, hidden facts in
the static browser DOM, and source phrases that must be rewritten. Universal checks still own skill
loading, deployment, homepage binding, browser state, AA contrast, clipping, overflow, both colour
schemes, information density, and house style.

Seven cases name the exact fixture field that contains prose the app must rewrite. The copy proof
splits that field into sentences and compares its content words with local groups of rendered text,
using the same content-word instrument as `#2194`. It fails a group that keeps at least five source
words and 70% of the source sentence. The known filler phrase still fails on an exact match. Each
source path is validated at suite load and must resolve to a sentence that contains its marker, so
the prompt clause cannot ship with an inactive copy check. Each source also has a hidden reader
rewrite. All seven source sentences fail the copy proof and all seven reader rewrites pass it.
The requirement grader reports every missing connector call, rendered fact, and copy failure in one
result, so one label does not hide a later source-copy failure.

The explicit `ufo-app-copy` suite runs those seven prompts with the same connector seed and a
copy-only grader. It requires the matching connector call, rendered source facts, deployment,
homepage binding, and reader copy. Its post-turn probe opens the page in Chromium and captures one
rendered static DOM. It does not take screenshots or grade contrast, layout, interactions, visual
taste, or browser-call count. Those independent failures therefore cannot hide a copy change.

App eval stacks settle the web extension's automatic homepage-build marker after workspace seeding
and before `serve` starts. This keeps the product's five-minute homepage job out of the measured
turn. The live `copy-meeting-tasks` check created one member turn and one sandbox, reached the
copy grader, and retained its failed source sentence. Before this isolation, one app stack could
add seven scheduled turns and exhaust Docker's network pool before a model call.

Model turns remain parallel. Post-turn Chromium captures take one host-wide slot across eval
processes. This prevents aggregate browser pressure from crashing an otherwise valid capture and
does not change the measured model trajectory.

The connected visual judge adds four taste checks: first-scan order, component fit, dense but calm
composition, and one coherent interaction language. These cases are new, so the first Gemini run
records their baseline. The three original controls retain their existing seven criteria and comparison
identity.

Run `455a67d3` exposed two harness defects: parallel cases replaced workspace-wide connector
fixtures, and 15 visual criteria exceeded the judge's 12-item limit. The fixed universe and
11-item rubric are covered by the focused artifact suite. Run `bf0768d6` is the first valid
ten-case Gemini baseline: every case used its required connectors and produced browser-proved
interactions; seven used only one browser QA batch, six failed AA contrast, and no case cleared all
deterministic gates. Run `58d5a403` then passed the PR-babysitter sentinel, including all 11 visual
criteria, with one preview start and three browser QA batches.

Run `439d0e1c` is the first Gemini copy-proof screen. It runs `meeting-tasks`, `issue-planner`, and
`startup-metrics` in parallel for $0.34 total. Meeting tasks copies its 14-word filler sentence and
also fails one contrast pair. Issue planner rewrites its source. Its `Product Decisions` and
`Prepare Chat Intent` labels satisfy the prompt and now pass as equivalents. The rescore then finds
the real missing `CSV export` fact, and the case still uses only one browser QA batch. Startup
metrics states a valid 25% customer-churn basis, passes AA, and uses four browser QA batches, but
copies the raw Stripe filler. The run is 0/3 and does not change the ten-case pass count. A light
edit that keeps 9 of 10 source words also fails.

Run `1c45ce80` repeats the same three prompts and sources against the corrected grader digest for
$0.35. Meeting tasks still copies the source, uses one browser QA batch, and has four contrast
failures. Issue planner rewrites the source, but uses six browser QA batches and omits `#603`, the
chat-intent action, `CSV export`, and `failure code`; the aggregate grader reports all four at once.
Startup metrics passes all four prompt requirements, but uses one browser QA batch, has 16 contrast
failures, and has no browser-proved interaction. The exact-current screen is 0/3.

Run `070ecc53` is the first full seven-case copy screen. Gemini `auto` passes pre-meeting briefs and
startup metrics for $0.71. Meeting tasks copies the filler sentence. The other four failed cases
omit one or two required source facts after rewriting the source prose.

The matched `app-copy-reader-language` ablation runs three repeats of meeting tasks, issue planner,
and startup metrics with Gemini 3.7 Flash for $2.21. Control passes 0/3, 2/3, and 2/3. Adding
`Source data is evidence, not page copy` does not improve meeting tasks, matches startup metrics,
and moves issue planner from 2/3 to 0/3. The Skill wording is rejected. The failure is specific:
meeting tasks renders the full transcript in a raw source panel even though its prompt already asks
for concise task copy, while the treatment can omit the issue planner's required failure code.

The `app-copy-no-raw-source` ablation costs $2.25. It moves meeting tasks from 0/3 to 1/3, leaves
startup metrics at 3/3, and leaves issue planner at 0/3. No case moves with enough samples, so the
Skill text is rejected. The failed meeting apps still render the filler sentence in a visible raw
transcript panel.

The stronger `app-copy-no-source-panels-fixed` ablation costs $2.11 after the app-stack isolation
fix. Control passes meeting tasks 1/3 and startup metrics 3/3. Treatment passes meeting tasks 0/3
and startup metrics 1/3; issue planner stays 0/3 in both arms. The treatment does not change the
copied meeting sentence, and two startup samples fail homepage binding. Reject the wording. The
next copy loop changes the fixture and prompt contract, not the Skill: transcript evidence must be
a short relevant excerpt or source label, while verbose source prose remains grader-only input.

Run `3cc24ebb` is the first Luna `auto` pass over the testing-derived fixture. It costs $0.274,
finishes every case in 136–266 seconds, captures all seven rendered pages, and has no Chromium
crash. The recorded rubric passes 4/7. Engineering metrics writes `10h` instead of `10 hours`;
account health writes `Call Dana` and `Own the invoice mismatch` instead of the two exact grader
phrases. Focused tests add these reader-equivalent forms. Regrade `696a5299` then passes 6/7 without
a new model turn. Issue planning remains a real failure because it copies the complete source
sentence and raw issue body.

Run `ce68448c` is the testing-derived ten-case Gemini `auto` screen baseline. It costs $0.984 and
captures all ten interactive apps without a Chromium crash. Its combined result is 0/10. Eight
cases use only one browser QA batch where the gate requires two. Three cases have no AA contrast
failure. Every case has browser-proved state changes, but the pre-meeting clipboard action also
raises a page error. The harness stopped before the visual judge when a deterministic check failed,
so this result does not contain a design-taste verdict. Screen cases now run the visual judge after
that failure while retaining the deterministic failure in the final result. This separates taste,
fact coverage, interaction, contrast, and trajectory evidence without lowering a gate.

Matched run `bc9a38a7` exercises that gate at revision `10a88403d67f`. It costs $1.222, captures
all ten apps, and passes 1/10 overall. Issue owner clears every layer. Eight apps pass all 11 visual
criteria. Engineering metrics and candidate review each fail only the density criterion. The main
remaining screen failures are browser-batch count, fact coverage, and contrast, not general visual
taste. This run is the visual baseline for the testing-derived fixture.

Run order:

1. Sample real testing connector responses through read-only access outside the evaluation.
2. Preserve allowed GitHub facts, transform sensitive provider values, and freeze fixture digests and joins.
3. Run the ten connected cases once with Gemini 3.7 Flash.
4. Repair fixture, prompt, probe, or grader defects without changing a failing quality threshold.
5. Repeat the unchanged connected set three times with Gemini.
6. Screen one connector-heavy case per model and reasoning setting.
7. Run the full set once for surviving settings, then three matched repeats for finalists.

The evaluated turn delegates once through `build_ufo_application`. The worker deploys through
`deploy_ufo_application`, and deterministic acceptance binds the homepage. After the turn ends,
the harness locates the generated HTML, serves it, runs the browser audit, exercises accessible
controls, captures both colour schemes, and preserves the generated HTML beside a static DOM
snapshot. The HTML report shows the screenshots, trajectory, grader verdicts, audit, downloads, an
inert snapshot, and an interactive preview with local scripts and styles embedded but no network
or parent access.

Select `ufo-app-bench` explicitly on a deploy whose `[sandbox] backend` is `docker`. The default
suite set omits this Docker-only browser probe.

Each control query names only the product, audience, homepage, and need for interaction. The model
sees no path, file shape, server, audit, screenshot, sharing, reply, layout, component,
content-field, action, token, count, or viewport requirement. These are harness work or hidden
grading facts. The case tests product judgment, not prompt or packaging imitation.

A connected query also states its product requirements and source accounts. It does not state tool
slugs, fixture values, expected copy, layout, component choice, fact count, or audit mechanics.

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

- All three workers preload `ufo-style` before the build.
- Every case passes its deterministic artifact and browser audit.
- Every case has a visual verdict for each criterion and both colour schemes.
- The browser audit proves each exact connected fact above the fold in both desktop schemes. The
  visual judge checks density and hierarchy without counting facts.
- Every case is visible and usable in the HTML report.
- Three matched full runs establish the pass distribution. The acceptance threshold is frozen from
  that distribution before any instruction treatment runs.

Gate C — interaction:

- All three workers preload `ufo-style` before the build.
- Every worker completes `deploy_ufo_application`; deterministic acceptance binds the homepage.
- Every case passes its deterministic artifact and browser audit.
- Every case exposes at least two accessible controls that produce distinct visible state changes.
- Every case has a visual verdict for each criterion and both colour schemes.
- The browser audit proves each exact connected fact above the fold in both desktop schemes. The
  visual judge checks density and hierarchy without counting facts.
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

## Boundary optimization report

This loop used 55 of the 200 allowed builder cases. The recorded report cost is $26.64. The Opus
record omitted $12.63 from completed rounds in three cancelled turns; the SQLite ledger records
$34.39 for that run and puts the effective loop total at $39.27. The harness now records completed
round spend before a cancelled turn settles, so later reports do not show a false zero.

### Run ledger

`Product` is the continuous app score. `Process` is the separate workflow score. A dash means that
the run predates those metrics or is the copy-only suite. Scores before the density gate are not
directly comparable with scores after it.

| Run | Cases | Binary | Product | Process | Cost | Case wall sum | Decision |
|---|---:|---:|---:|---:|---:|---:|---|
| Opus baseline `19130935` | 10 | 1/7, 3 excluded | — | — | $21.76 reported | 6,955s | Reject Opus as the default builder. It is expensive and reaches the 900-second bound in three cases. |
| Gemini effort screen `f317dca1`, `89523a62`, `b7bf7540`, `c93b7fb5`, `d1c0d707` | 5 | 0/5 | 0.942–0.964 | 0.667–1.000 | $0.90 | 1,338s | Remove `high`. Test `off` and `medium` on three cases. |
| Gemini `off` `8aee02dc` | 3 | 0/3 | 0.811 | 0.667 | $0.33 | 545s | Reject. Two delivery failures. |
| Gemini `medium` `ec37c9f8` | 3 | 0/3 | 0.846 | 0.778 | $0.36 | 528s | Keep as the builder setting. |
| Build contract `159d6126` | 3 | 0/3 | 0.954 | 0.667 | $0.27 | 471s | Keep. Delivery is 3/3, source is 0.966, and interactions are 3/3. |
| Full shadcn path `2033f7ca` | 2 | 0/2 | 0.920 | 0.667 | $0.26 | 452s | Reject. The nested Skill does not load, one delivery fails, and direct builds score 0.970 on these cases. |
| Gemini, Luna, Fable `888580b9` | 3 | 0/3 | 0.946 | 0.667 | $0.35 | 552s | Reject. Luna runs once, Fable never runs, and the parent ignores Luna's prohibited phrase list. |
| Density boundary `a1552df3` | 3 | 0/3 | 0.937 | 0.778 | $0.33 | 516s | Keep the boundary. Source is 0.967 and above-fold density is 0.911. |
| Explicit QA sentence `d7f2ec40` | 3 | 0/3 | 0.944 | 0.778 | $0.34 | 505s | Reject. QA stays at 1/3. |
| Final Gemini set `357d81f3` | 10 | 0/10 | 0.807 | 0.733 | $1.20 | 1,773s | Final reference. One case is a Google 400 before the app starts. |
| House CSS blocks `ae8418f7` | 3 | 0/3 | 0.903 | 0.667 | $0.27 | 433s | Reject. Contrast improves in one case, but product and QA regress. |
| Final Luna copy set `7c904a61` | 7 | 3/7 | — | — | $0.28 | 1,175s | Do not add a Luna stage. Copy and fact coverage are not stable across the set. |

### Accepted setup

| Part | Final choice | Evidence |
|---|---|---|
| Builder | Gemini 3.7 Flash, `medium` reasoning | It beats `off` on the three-case finalist screen and avoids the large time and token increase at `high`. |
| Build form | One direct HTML, CSS, and JavaScript owner | Full shadcn, optional specialist roles, and CSS blocks do not beat the monolith. |
| Skill guidance | One private build contract | It lists exact facts, reader copy, controls, delivery, and homepage binding before files are written. |
| Hard verdict | All product and process gates | Easy visual quality does not excuse contrast, interaction, source, density, or QA failures. |
| Product score | Delivery, design, source, density, page, interaction, action, visual | A single hard failure no longer hides partial product quality. |
| Process score | Skill, direct ownership, QA efficiency | Process experiments do not change the product score. |
| Fixtures | Fixed, transformed connector records | GitHub facts remain exact where allowed. No app eval calls a live connector. |

### Judge boundary

| Owner | Checks |
|---|---|
| Deterministic connector grader | Tool discovery, described and called connectors, exact fixture facts, and source-copy leakage. |
| Deterministic browser audit | Exact facts above the fold in light and dark desktop views, AA contrast, clipping, overflow, console errors, and visible interaction state. |
| Visual judge | Visible colour character, accent restraint, type roles, hierarchy, corner consistency, spacing, component fit, density, and product coherence. |
| Process grader | Skill loading, one build owner, one preview server, two to four browser batches, deployment, and homepage binding order. |

The visual rubric no longer asks a screenshot model to infer exact CSS token values, a loaded font
license, a four-pixel radius, or a numeric fact count. It tells the judge that the two screenshots
intentionally use different schemes and that each image is judged independently. Real green and red
status hues still fail the house palette. Deterministic AA failures remain hard failures.

### Final frontier

Run `357d81f3` has 0.900 delivery, 0.820 source coverage, 0.685 above-fold density, 0.775 page
audit, 0.800 interaction, and 0.864 visual taste. Four cases use valid browser QA. The main defects
are missing or buried facts, copied source prose, custom accent text below AA, one clipboard page
error, and one-batch browser proof. The Google provider rejects startup metrics before its first
assistant message with `Requests ending with a model turn are not supported.` It bills 1.05 million
input tokens and $0.14. Keep this provider or adapter fault separate from application quality.

The next loop must change an enforced boundary, not add more optional instructions. The best next
test is a typed data and copy artifact that the builder must consume. A component library is useful
only when the build system owns imports and forbids raw colour overrides. Optional blocks and
optional subagents do not provide that control.

## Phase 3: Instructions

Goal: add skill material or tool descriptions only where a measured failure needs them.

Use the levers in this order:

| Lever | Question | Treatment gate |
|---|---|---|
| Routing | Did `ufo-style` load in the application worker instead of `create-application` or a neighboring skill? | Positive and negative loading cases improve before body text changes. |
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

### Application QA loading attempts

The first valid connected Gemini baseline loaded `website-building` in all ten cases, but seven
cases did not read its application QA reference and used one browser batch. Two matched wording
treatments did not change that behavior.

| Treatment | Control | Treatment | Cost | Decision |
|---|---:|---:|---:|---|
| Tell the model to read the application QA reference before writing files. | 0/3 | 0/3 | $0.74 | Reject. The treatment still did not read the reference. |
| Put the two-batch rule in the Skill hub. | 0/3 | 0/3 | $0.60 | Reject. Completed treatment runs still used one browser batch. |

The tracked Skill keeps neither treatment. The next comparison changes the model, not the Skill
wording.

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

### Gemini 3.7 Flash

The Gemini arm uses `google/gemini-3.7-flash` through OpenRouter. It changes only the deploy's
`auto_model`; the three queries, Skill content, tools, 900-second bound, browser probe, visual judge,
and grader digest stay fixed.

The Gemini and latest Opus records use exact matching app-bench, audit, Skill, and application-QA
file blobs. Their repository revisions differ because the branch was synchronized with main, so
this is a comparison with the latest Opus reference, not a same-revision model ablation.

| Model | Run | Passed | Per-case wall sum | Model time | Tool time | Rounds | Tokens | Cost |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Gemini 3.7 Flash | `b2b873a6` | 2/3 | 512s | 367s | 109s | 62 | 2.45M | $0.44 |
| Opus 5 | `20da5079` | 3/3 | 1,402s | 1,227s | 143s | 71 | 5.87M | $7.40 |

Gemini reduces the per-case wall sum 63.5%, model time 70.1%, tokens 58.2%, and reported cost
94.1%. Its board and brief pass all seven visual criteria and prove 11 state changes each. Its
notes app fails two light-mode text colours at 2.96:1 and 2.78:1. A prior Gemini run, `e42054ae`,
passes notes but fails board's four-browser-call gate and one brief contrast value. Each Gemini case
therefore passes once and fails once across two runs. Keep it as an arm, not a replacement, until
three unchanged matched repeats establish the pass distribution.

The OpenRouter client also envelopes a Google tool result that contains a JSON Schema `$ref` as
text. A live reproduction proves the raw JSON returns 400 and the envelope returns 200 without
losing the schema. This provider repair changes no app prompt or grader.

### Connected-case model screen

The screen uses the unchanged `code-review-queue` case. `auto` is each model's configured default.
The second setting tests more reasoning for Gemini and Luna and less reasoning for Terra and GLM.

| Model | Reasoning | Run | Result | Wall time | Rounds | Browser QA | Main failure |
|---|---|---|---|---:|---:|---:|---|
| Gemini 3.7 Flash | `auto` | `d8976ac1` | 0/1 | 203s | 18 | 1 | One 2.92:1 contrast pair and too few browser QA batches. |
| GPT-5.6 Luna | `auto` | `b54a5cf8` | 0/1 | 153s | 16 | 4 | Dark-mode contrast and missing review facts. |
| GPT-5.6 Terra | `auto` | `f8aed506` | 0/1 | 300s | 14 | 10 | Too many browser QA batches and missing review facts. |
| GLM 5.3 | `auto` | `3d989ce8` | Excluded | 900s | 4 | 0 | Model timeout before the first file write. |
| Gemini 3.7 Flash | `high` | `901dee32` | 0/1 | 257s | 32 | 4 | Fourteen computed contrast failures. |
| GPT-5.6 Luna | `high` | `3a011e14` | 0/1 | 295s | 32 | 8 | Dark-mode contrast and too many browser QA batches. |
| GPT-5.6 Terra | `low` | `df9b3618` | 0/1 | 139s | 22 | 0 | No deployment or homepage binding and one clipped title. |
| GLM 5.3 | `low` | `61b3f8c7` | 0/1 | 303s | 17 | 0 | The text-only route rejected browser image input. |

No setting advances to the full ten-case run. More reasoning increased time, rounds, and browser
fragmentation for Gemini and Luna. Lower reasoning made Terra faster but removed required delivery.
GLM 5.3 is a valid text and tool route, but it is not eligible for this visual loop until model
capabilities can stop image-bearing tool results before the provider call.

The testing-derived screen at revision `d86d0f169860` repeats the four non-default effort settings
with every visual layer recorded.

| Model | Reasoning | Run | Result | Wall | Rounds | Browser QA | Visual | Cost |
|---|---|---|---:|---:|---:|---:|---:|---:|
| Gemini 3.7 Flash | `high` | `e1aa38d8` | 0/1 | 304s | 33 | 4 | 11/11 | $0.242 |
| GPT-5.6 Luna | `high` | `088e014f` | 0/1 | 255s | 33 | 7 | 9/11 | $0.083 |
| GPT-5.6 Terra | `low` | `35986a6e` | 0/1 recorded, 1/1 regraded as `f900c9ff` | 143s | 22 | 4 | 11/11 | $0.385 |
| GLM 5.3 | `low` | `8ea78222` | 0/1 | 204s | 12 | 3 | 11/11 | $0.202 |

`ModelSpec.accepts_image_input` now marks GLM 5.2 and 5.3 as text-only from OpenRouter's model
metadata. Their provider requests replace browser images with an explicit text marker; the run
record and visual judge keep the screenshots. GLM therefore completes the app and visual judge
instead of failing when an image-bearing browser result reaches the next model round. It still
misses Skill loading, deployment, homepage binding, and a second successful QA batch.

Terra low clears QA, AA contrast, interactions, and all visual criteria. Its page shows `Checks
Failed` and `Threads · Issue 2 · #602`; the grader accepted only `failing` and forms beginning with
the number. Focused tests add these equivalent reader forms. Regrade `f900c9ff` passes without a
new model call, so Terra low advances to the full testing-derived set.

Full Terra-low run `ef94d964` at revision `a8f608b328cc` passes 1/10; meeting tasks clears every
layer. It costs $3.099 across 1,439 seconds of case wall time, 187 rounds, and 7.43 million model
tokens. All ten apps clear the deterministic AA audit. Nine use two to four browser calls, although
issue owner and startup metrics still fail deployment order; code review uses five calls. Six apps
pass all 11 visual criteria. Most failures are missing required connected facts. Against Gemini
`bc9a38a7`, Terra reduces case wall time 23.6% and removes all contrast failures, but costs 2.5
times as much and moves the 11/11 visual count from eight apps to six. Terra low is the stronger
implementation and QA arm; Gemini `auto` remains the cheaper visual arm.

The screen also found one grader format defect. Correct apps used reader-safe forms such as
`2 open threads`, `2 open reviewer discussion threads`, and `2 open · #602` instead of the exact
fixture phrase `2 unresolved`. The deterministic requirement now accepts these equivalent forms.
This repair does not convert a screen result to a pass: each affected run still has a contrast,
browser-efficiency, content, clipping, or delivery failure.

### Copy-only model screen

The matched sentinel is `copy-pre-meeting-briefs`. It exercises Calendar, email, Drive, and GitHub,
requires deployment and homepage binding, and grades source facts and rewritten reader copy without
a visual judge.

| Model | Reasoning | Run | Result | Wall | Cost | Main failure |
|---|---|---|---:|---:|---:|---|
| Gemini 3.7 Flash | `auto` | `63668b43` | 0/1 | 181s | $0.12 | Copy passes; homepage binding is missing. |
| Gemini 3.7 Flash | `high` | `d8da8dd9` | 1/1 | 200s | $0.13 | Pass. |
| GPT-5.6 Luna | `auto` | `43266af0` | 1/1 | 179s | $0.05 | Pass. |
| GPT-5.6 Luna | `high` | `62945757` | 1/1 | 284s | $0.08 | Pass, with no gain over `auto`. |
| GPT-5.6 Terra | `auto` | `afce30ea` | 0/1 | 119s | $0.32 | Omits the SSO fact. |
| GPT-5.6 Terra | `low` | `42ed5602` | 0/1 | 107s | $0.30 | Does not deploy or bind. |
| GLM 5.3 | `auto` | `568c9887` | 0/1 | 900s | — | Model work reaches the bound. |
| GLM 5.3 | `low` | `afbdda1e` | 0/1 | 259s | $0.35 | Turn ends with `NotFoundError`. |

The full-set finalists confirm the sentinel direction. Gemini `high` run `a290245f` passes 1/7,
costs $0.93, and has two 900-second model timeouts. Luna `auto` run `dbb6b9d4` passes 3/7 for
$0.37. Luna passes pre-meeting briefs, meeting tasks, and engineering metrics. Two later Luna
artifact captures crash under concurrent Chromium pressure; isolated captures succeed, but both
apps still omit required facts, so the pass count does not change.

Final Luna run `7c904a61` also passes 3/7 on the current fixture digest. It passes the same first two
cases and engineering metrics, copies 9/10 words from the issue-planner source, and omits required
facts in three other cases. Use Gemini `medium` for the visual loop. Keep Luna as a copy research
arm, not as a default stage. Do not advance higher Luna reasoning, Terra, or GLM. A model advances
to three repeats only after an enforced handoff improves the seven-case copy frontier.

Gate:

- A specialist improves a named quality layer or reduces cost or time without a quality loss.
- Tool grants remain narrower than or equal to the work the profile performs.
- Handoffs preserve the member request, files, and grader-visible trajectory.
- A profile with no measured benefit does not ship.

## Phase 5: Build pipeline

Goal: separate model work, deterministic acceptance, and publication behind owned artifacts.

| Owner | Work |
|---|---|
| Opus parent | Make one delegation from the member request and give one final response. |
| Gemini builder | Inspect connector data, write `app.tsx`, run bounded QA, repair, and deploy. |
| Deterministic harness | Compile, check the exact source digest, audit interactions and facts, and reject invalid source. |
| Opus escalation | Run only for an ambiguous request or a deterministic failure class that has no product rule. |

Gemini does not certify its own result. Product acceptance decides if the exact deployed source can
bind as the homepage. One failed audit can return one bounded diagnostic batch to the same worker.
A second failure ends the build as `blocked`. The parent does not read source, browser output, or
repair diagnostics in the normal path.

The application design is a typed artifact. A fixed renderer produces its preview in milliseconds.
The accepted contract becomes builder input. The source candidate is the next artifact. The compiler
and browser audit produce the final proof against its digest. Deployment uses that proof and does
not repeat the audit.

[The exploration campaign](app-bench-exploration.md) records the rejected multi-model and
parent-supervised paths. The accepted A07–A10 path passes all four complete journeys. Corrected
archival process grading passes 13 of 14 presentation paths. The remaining failure is product
quality, not pipeline ownership.

Gate:

- One Gemini delegation owns connector inspection, source, QA, repair, and deployment.
- Acceptance checks the compiler, source digest, facts, interactions, contrast, and viewport fit.
- A failed build keeps its evidence and creates no partial homepage binding.
- No routine Opus source or browser repair enters the path.

## Phase 6: Creation flow

Goal: let a member create and use an application through one guided chat flow.

Sequence:

1. The Apps screen admits `Build me a new app.` as a chat turn.
2. `create-application` opens the fixed phase board and interviews the member.
3. The parent renders the typed design and waits for approval or a named revision.
4. One Gemini worker builds the accepted design.
5. Deterministic acceptance checks the exact deployed source before homepage binding.
6. The approved application, site, homepage, and access rules become durable together.
7. The member opens the application and completes its benchmark task.

The guided and revision screens pass. The complete A07–A10 journeys pass 4/4. A10 proves the
failure path: one build ends as `blocked`, keeps its evidence, and creates no partial publication.
One later approved attempt deploys through one parent and one Gemini worker.

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
