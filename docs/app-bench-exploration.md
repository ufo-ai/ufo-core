# App build exploration campaign

## Outcome

Select one production-shaped application build path that turns a natural member request and fixed
connector data into a useful, interactive ufo homepage. The selected path must also support
reviewable planning, prepared actions, repair, and publication without lowering the existing hard
quality gates.

Iteration speed is the limiting resource. The normal path keeps large source, connector results,
browser output, and repair diagnostics inside one fast worker turn. Opus receives none of them on a
passing run.

The current ten connected prompts, four controls, fixture digest, graders, model bounds, and browser
probe remain fixed comparison inputs. A changed input starts a new comparison identity.

## Fixed boundary

- The member prompt names the job and its requirements. It contains no fixture fact, file path,
  audit command, framework choice, or delivery instruction.
- Fixed transformed fixtures replace live connectors. GitHub facts stay exact where allowed.
- The standalone HTML suite remains the control until the production-shaped suite proves
  equivalent coverage.
- Binary success remains strict. Product and process scores diagnose partial progress; they do not
  excuse a hard failure.
- Deterministic graders own source facts, copy, density, accessibility, browser state, connector
  calls, and prepared actions. The visual judge owns only visible design taste.
- A provider or harness fault is an exclusion. Repair its root cause before it enters a matched
  comparison.
- The stack template selects one fixed Opus main agent. `ufoctl init` receives no app-bench model
  or reasoning override. Builder model and reasoning changes belong to the application-builder
  subagent profile.
- The main agent delegates once and gives one final response. The Gemini worker owns connector
  inspection, source, QA, repair, deployment, and structured evidence.
- Product checks verify the retained source, deployment, and recorded browser batches before they
  bind the homepage. The eval harness then compiles and audits facts and interactions. Gemini never
  supplies either verdict.
- Opus escalation receives only an ambiguous request or a failed deterministic audit with no
  stable worker or harness rule. A known process failure changes the worker or harness. An optional
  diagnostic arm can lower this threshold, but it does not enter the normal path.
- A prompt or Skill change ships only after a matched ablation.
- The planned phases use at most 239 evaluated cases. Stage A has one approved 30-case recovery
  reserve. Stage D has the approved nine-case reasoning extension. Stage F has the later approved
  30-case exploration reserve. A failed gate stops its dependent arms.

## Product patterns

| Proven pattern | Campaign use | Source |
|---|---|---|
| Review a plan before files change; keep recoverable checkpoints. | The application contract is reviewable. Each accepted build attempt has one retained source and evidence checkpoint. | [Replit Plan and Build modes](https://docs.replit.com/learn/plan-vs-build-mode) |
| Keep one project identity across chats, deployment, settings, and integrations. | An application keeps one agent, homepage, source record, and deployment identity through revisions. | [v0 Projects](https://api2.v0.dev/docs/projects) |
| Add UI, data, core behavior, then polish in small working steps. | Stages integrate one producer and consumer before the next capability enters. | [v0 full-stack applications](https://v0.dev/docs/full-stack-apps) |
| Test in a live preview and repair detected errors before publication. | Browser proof runs against the production-shaped page before `deploy_website`. Product acceptance binds the accepted deployment. | [GitHub Spark application workflow](https://docs.github.com/en/enterprise-cloud@latest/copilot/tutorials/spark/build-apps-with-spark) |

## Production build target

The current product page is a Vite project whose `app.tsx` imports from `ufo/kit`. The kit supplies
React, the house components, routes, bridge transport, reads, prepared intents, and the portal
theme. Vite builds the page into `dist`. The page loads inside the same framed homepage path that
members use.

The product owns `index.html`, `vite.config.ts`, the loader, bridge client, and kit version. The
builder receives that fixed scaffold and writes only `app.tsx`. The build retains the source under
`dist/src`. Generated HTML is not an application source artifact.

The campaign therefore compares two targets:

| Target | Purpose |
|---|---|
| Standalone HTML, CSS, and JavaScript | Frozen benchmark control. |
| Vite-built `app.tsx` over `ufo/kit` | Candidate production target. The builder owns the page source, not Vite, the portal kit, or the bridge. |

Data reads use the bridge endpoint table. A member action submits a prepared intent, which the
engine admits as a turn and dispatches to the typed object verb. A generated page does not add a
member-facing endpoint or call a connector mutation directly.

The build path belongs to an extension. Core receives no application-builder workflow.

## Worker topology

The app benchmark uses the parent only to route the member request. The app-builder profile owns
the build model and reasoning settings. The stack preparation gives the parent only
`build_ufo_application`. The tool passes the application instructions and current request and
preloads `ufo-style` in the worker, so Skill wording is not the enforcement boundary.

| Owner | Work |
|---|---|
| Opus parent | Delegate the member request once. Give one final response from the worker's structured deployment result. |
| Gemini builder | Inspect connector data, build `app.tsx`, run QA, repair, deploy, and return structured evidence. |
| Product acceptance | Compile source, audit the staged page and interactions, verify retained source and deployment ownership, then bind the homepage. |
| Eval harness | Repeat the product audit, grade connector use and copy, and reject invalid output. |
| Opus escalation | Act only in a named diagnostic arm for a novel failed deterministic audit or an ambiguous request. |

The application-builder profile holds only the tools needed for its complete workflow. It cannot
delegate, ask the member, share files, change grants, use a shell, or bind a homepage. The parent
does not inspect connectors, read source, run browser QA, repair, deploy, or bind the homepage.
Source and browser evidence stay inside the Gemini turn. Product acceptance reads deterministic
state and binds directly. The eval harness repeats its checks independently, so the worker's report
is evidence and never acceptance.

Stable compile, edit, QA, deployment, and source-shape failures are process classes. Their next
attempt changes a deterministic tool contract or worker rule. Opus can inspect one such failure in
an explicit diagnostic arm when classification is uncertain. It does not repair the artifact, and
the arm does not qualify as worker throughput.

The normal path has zero Opus diagnostic turns. A screen can lower the diagnostic threshold for a
repeated process class, but it records that call as experiment cost and cannot promote that path.
Once a process class has a deterministic name, later runs change the worker or harness directly.

The profile input contains the application instructions, current request, source path, and fixed
scaffold path. Its result contains the deployed site identity, source path, browser batches,
checked controls, and observed errors. It does not return a freeform build report.

`SubagentProfile` needs an optional reasoning setting beside its existing model setting. An unset
value preserves inheritance. The application profile pins both values, so a fast Gemini child does
not inherit the Opus parent's reasoning setting. This is a core manifest seam because extensions
cannot express child reasoning through the current profile type.

App-bench model arms change the profile definition in an ablation worktree. They never change the
initialized main agent. Reports name and price the parent and builder separately. Throughput,
product score, and independent acceptance decide the winner.

## PR #2261 correction units

Make these corrections on the PR branch before the larger exploration starts. Keep each unit small
enough to review and move the accepted commits back to PR #2261 before its next matched run.

| Unit | Change | Proof |
|---|---|---|
| 1. Profile control | Add optional reasoning to `SubagentProfile`. Let the stack template select the fixed Opus parent; pass no app-bench model setting through `ufoctl init`. | A profile test proves that a pinned child model and reasoning setting override inheritance. An unset value still inherits. |
| 2. Application worker | Add the typed, isolated `ufo_application_builder` profile. Give it connector reads, fixed-scaffold source tools, browser QA, deployment, and structured evidence. | Profile and tool-policy tests reject subagent, member, sharing, grant, and homepage tools. The product compiler rejects invalid TSX before each source change. |
| 3. Benchmark topology | Enforce one worker delegation and one parent response. Keep the independent artifact audit. | Trajectory tests prove one parent spawn, worker ownership of the full build, one parent response, separate model costs, and the `app.tsx` artifact. |
| 4. Escalation | Keep Opus outside the normal path. An explicit diagnostic arm can inspect a novel final-audit failure. | A passing worker run has no escalation. A repeated failure class moves into a deterministic rule. |

Write the trajectory checks before the profile text. Run a matched profile-prompt ablation before
the wording stays. The independent Gemini provider fault belongs to the exploration branch unless
it blocks the corrected PR run.

## Campaign sequence

`Production target → Build contract → Component substrate → Model roles → Connected actions → Creation flow`

| Stage | Case cap | Question | Exit evidence |
|---|---:|---|---|
| A. Production target | 30 | Can the harness build and grade the same apps through `UfoAppKit` and the real bridge? | Matched kit pages preserve facts, interactions, visual evidence, deployment, and homepage binding. |
| B. Data and copy contract | 42 | Does an enforced typed handoff fix buried facts and copied source prose? | The required contract beats the direct-build control without prompt leakage. Sixteen used; twenty-six remain. |
| C. Component substrate | 30 | Which owned starting surface improves density, contrast, speed, and consistency? | No starter beats the blank kit page. Keep the blank surface. Twenty-four used; six remain. |
| D. Model and role routing | 45 | Which worker model and reasoning setting gives the best accepted throughput? | No Vite reasoning arm is accepted. `auto` remains a candidate after deterministic delivery repair. All 45 cases are used. |
| E. Connected actions | 42 | Can generated pages read connector projections and complete approved actions through chat? | Done. Prepared actions and setup pass 3/3. Thirty-nine cases are used; three move to final confirmation. |
| F. Creation flow | 50 | Can a member approve, preview, revise, publish, and use the selected build? | Four complete member journeys pass without partial publication. All 50 cases are used. |
| **Planned total** | **239** | | Unused cases move to final confirmation repeats. |
| **Stage A recovery reserve** | **30** | | All 30 cases are used. |
| **Stage F exploration reserve** | **30** | | All 30 evaluated cases are used. |

## Current Vite reasoning screen

The screen compares Gemini 3.7 Flash `medium`, `high`, and `auto` on `pre-meeting-briefs`,
`meeting-tasks`, and `issue-owner`. It adds nine explicitly approved cases. Commit `4bf9801b6` is
the common base. The arms change only `APPLICATION_BUILDER_REASONING` in the builder profile.

The Vite smoke found and fixed two harness faults before the matched run:

- Vite asset URLs are relative, so the product preview can load `dist/index.html` below its source
  server.
- Server cleanup identifies the process that owns the listening socket through Linux `/proc`.
  The sandbox image has no `fuser` or `lsof`.

The medium smoke then reached deterministic acceptance. The audit rejected real app defects:
phone overflow, one missing asset, and required facts below the fold. That result remains model
evidence. It does not cause a prompt or grader change.

One matched repeat produced this result:

| Reasoning | Strict | Mean app score | Mean wall time | Cost | Result |
|---|---:|---:|---:|---:|---|
| `medium` | 0/3 | 0.493 | 338 s | $0.430 | Control. |
| `high` | 0/3 | 0.612 | 389 s | $0.487 | Better source coverage; slower; no accepted delivery. |
| `auto` | 0/3 | 0.645 | 362 s | $0.457 | Best density and app score; no accepted delivery. |

Every arm scored 0 for delivery and interaction and 0.333 for process. `auto` improved mean app
score by 0.152 for 7% more wall time and 6% more cost than `medium`. It is promising but does not
replace the control. Application acceptance requires the source root because `preview.html` sits
beside `dist` there.

The four-case recovery screen used `meeting-tasks` and `issue-owner` at `medium` and `auto`. Commit
`348bbd766` is the common base. Product acceptance now rejects a builder deployment whose root is
not `/workspace/ufo-app` before it creates site state or starts an audit.

| Reasoning | Strict | Mean app score | Mean wall time | Cost | Result |
|---|---:|---:|---:|---:|---|
| `medium` | 0/2 | 0.655 | 396 s | $0.282 | Source-root rejection worked; delivery still failed. |
| `auto` | 0/2 | 0.653 | 353 s | $0.265 | Faster and cheaper; delivery still failed. |

The common missing resource was an audit defect. The audit copied stylesheet rules into the framed
document without their original base URL. Vite font URLs then resolved from `dist` instead of
`dist/assets`. The interactive report had the same broken relative resources. Both report paths now
embed same-origin CSS resources as data URLs. A replay of the archived `meeting-tasks` Vite app
reported zero console errors in all four views and in the interaction audit. The app's real narrow
layout width remains in the report.

The saved worker trajectories expose two process failures after that screen:

- Three of four workers ended after large Gemini rounds without a final OpenRouter usage event. The
  provider adapter now retrieves exact native usage from the generation record only when the stream
  already has a generation ID and terminal finish reason. An unfinished stream still fails.
- The worker that built source opened the server root, then started servers from the Vite source and
  `dist` directories. The application-builder `start_server` path now accepts only
  `/workspace/ufo-app`, accepts no command, and returns the exact framed `/preview.html` URL.

These repairs change provider and tool boundaries, not model wording. They keep the current Skill
arm fixed for the next confirmation.

The synchronized confirmation uses `meeting-tasks` and `issue-owner` once at `medium` and once at
`auto`. Both arms pass `meeting-tasks` and fail `issue-owner`, so `auto` moves no case.

| Reasoning | Run | Strict | Mean app | Mean process | Median wall | Cost | Result |
|---|---|---:|---:|---:|---:|---:|---|
| `medium` | `22ada6f6` | 1/2 | 1.000 | 0.833 | 419s | $0.264 | Keep. |
| `auto` | `e555eccb` | 1/2 | 0.987 | 1.000 | 431s | $0.291 | Reject. No strict case moves. |

The medium `issue-owner` page passes every product layer, including facts, first-screen placement,
contrast, interaction, and all 11 visual criteria. It fails only because one browser call appears
before `start_server`. The auto page passes the process layer but misses two visual criteria. Keep
the process and visual failures. Do not lower either gate.

Seven of ten connected hard passes and all four controls is the accepted confirmation bar. Land
the builder and deterministic acceptance changes from this campaign. The next latency campaign
owns the complete presentation confirmation and the remaining quality work.

## Next latency campaign

Start from main after the current builder and benchmark changes land. Keep all interaction,
contrast, fact, density, and visual thresholds.

| Order | Change | Gate |
|---:|---|---|
| 1 | Replace free-form worker browser QA with one frame-aware product QA tool. | The tool runs the same interaction checks. Failed 30-second waits and redundant model rounds decrease. |
| 2 | Separate audit from deployment and cache a passed audit against the exact source digest. | Deployment accepts only a proof for the deployed digest and does not repeat the audit. |
| 3 | Run the four independent viewport checks in parallel, then test bounded parallel interaction contexts. | Output order stays deterministic and every current check remains. |
| 4 | Give the application profile fixed-root start and deploy tools with no `project_path` input. | A worker cannot select `dist` or another root. |
| 5 | Compare Gemini `low` with `medium`. | Test only after deterministic waste is removed. Keep `medium` unless `low` preserves hard passes and improves accepted throughput. |

Step 1 passes its three-case screen. The worker receives one fixed-root `qa_ufo_application` tool
instead of `start_server` and `js_repl`. The tool enters the application frame and runs the same
four-view, contrast, fit, clipping, console, fact, first-screen, control, and interaction checks. A
failed check returns one typed repair result. Only a passed result records the deployment proof.
Deployment still repeats the audit; step 2 owns that removal.

| Arm | Runs | Hard | Median wall | Worker rounds | Worker tools | Tokens | Cost |
|---|---|---:|---:|---:|---:|---:|---:|
| Browser batches | `045f6552`, `5515a7ba` | 0/3 | 350s | 69 | 84 | 1.39M | $0.384 |
| Product QA | `a7843852`, `d0b0526b` | 2/3 | 315s | 54 | 66 | 1.14M | $0.379 |

Product QA passes `pre-meeting-briefs` and `issue-owner`. `meeting-tasks` passes every deterministic
product check, all required facts, and 11 interactions. It fails two visual judgments because the
proposal approval controls sit below the first desktop screen. Keep that failure for the later
quality campaign. Each product-QA case uses one repair result and one passed result, with no
free-form browser call.

Step 2 passes its source and deployment gate. A passed product audit stores one typed proof with
the exact `app.tsx` SHA-256 and the one or two browser QA calls used. Deployment compares the
current source with that proof before it builds or changes hosting state. It does not run the
browser audit again. Product acceptance also compares the deployed source with the same proof.
A focused stale-source test proves that an edit after QA is refused before a build starts.

The unchanged three-case matched screen passes one case in each arm. Four samples stop in product
QA before the source-proof boundary, so their different app failures measure worker variance.
Treatment `meeting-tasks` reaches the boundary, deploys, and passes every hard gate. Its valid
deployment takes 22.1 seconds. The two successful step-1 deployments took 55.3 and 60.3 seconds
because deployment repeated the browser audit. Total case wall time remains model-dominated.

| Case | Control | Source proof | Boundary result |
|---|---:|---:|---|
| `pre-meeting-briefs` | `fed64f1c` 0/1 | `0bb5d88e` 0/1 | Both fail separate app checks. |
| `issue-owner` | `a1092ae1` 1/1 | `7b0e85b9` 0/1 | Treatment stops in product QA before deployment. |
| `meeting-tasks` | `ea3fc570` 0/1 | `9651c935` 1/1 | Treatment reuses the exact-source proof and passes. |

Step 3 accepts viewport parallelism. The four independent light, dark, desktop, and phone checks
run together. `Promise.all` keeps their declared output order. The interaction audit remains
sequential. In the matched `meeting-tasks` screen, control run `3ecb809e` takes 36.1 and 36.3
seconds for its two product QA calls. Parallel run `71a32ed4` takes 31.1 and 31.7 seconds. It keeps
all four views, both screenshots, 14 first-screen facts, ten visible state changes, and all 11
visual criteria. It deploys and fails only the unchanged source-copy grader.

Step 3 rejects two concurrent interaction contexts. The valid matched controls `b669b6cc` and
`764167ee` pass 2/2. Treatments `7845d847` and `f94c1129` pass 0/2. Treatment audits keep ordered
results, nested controls, four to seven visible state changes, all four views, and no console
errors. Their QA calls are faster, but one app has phone overflow and one omits a required prepared
assignment label. Keep sequential interaction contexts because the strict product bar regresses.
The earlier treatment `a28cd6f8` never reaches product QA and does not count.

Step 4 accepts the fixed-root deployment boundary. The application profile now receives
`deploy_ufo_application`, whose schema contains only `site_name`. The tool owns
`/workspace/ufo-app` and `index.html`; the generic website profile keeps `deploy_website`. A
focused tool test proves both fixed values and rejects access outside the application profile.

Matched `meeting-tasks` run `4dccd36d` completes two product QA calls and one deployment with no
path argument or invalid `dist` call. Control `fcf0a9cc` stops in product QA. Both fail the unchanged
copy rule, so no strict case moves. The `issue-owner` sentinel (`633eae48` / `3f9dbcda`) also moves no
case because both workers stop on separate fact-placement failures before deployment. Historical
control `764167ee` passed only after one rejected `/workspace/ufo-app/dist` call and a corrected
deployment. That invalid call is now impossible at the schema boundary.

Step 5 rejects Gemini `low`. The matched three-case screen keeps `medium` as the control and changes
only the application profile reasoning setting. Medium run `18c46712` passes `issue-owner`; low run
`4a950efe` loses that pass and moves neither failing case. Low uses 1.05 million tokens instead of
1.56 million and costs $0.36 instead of $0.42, but median wall time rises from 273 to 287 seconds.
The low change is reverted. Keep `medium` for the SVG-first screen.

Step 6 accepts one SVG before source work. The application profile receives one fixed-root
`write_application_design` tool. It accepts one safe SVG visual contract, writes
`/workspace/ufo-app/application-design.svg`, and blocks `app.tsx` until that design exists. The
worker must implement semantic HTML, CSS, and controls; it cannot embed the SVG as the application.

The matched three-case control run `03e971ed` passes 0/3. SVG-first run `7e73a411` passes
`issue-owner` and moves no control pass backward. Mean app score rises from 0.809 to 0.916 and mean
process score rises from 0.333 to 0.778. Median wall time falls from 293 to 256 seconds. Tokens rise
from 1.19 million to 1.97 million and cost rises from $0.364 to $0.475. Each treatment case writes
exactly one SVG before its first source write.

Keep the one-SVG boundary for review. Do not hide its remaining waste: `meeting-tasks` takes 543
seconds and makes three rejected source-edit calls before it repairs and deploys. Test at most two
design-only SVG revisions next. A later repair must remove invalid edit shapes at the tool boundary
without lowering the product checks.

The two-revision screen rejects three required SVG versions. Control run `0154c307` and treatment
run `96bceab8` both pass 0/3. Mean app score rises only from 0.846 to 0.867, below the 0.03 product
gate, while median wall time rises from 396 to 484 seconds. Treatment workers write all three SVG
versions, then make 23 source-edit calls; ten are rejected. The extra design rounds do not direct a
useful repair and do not reduce source thrash. The revision change is reverted. Keep one SVG.

The first source-edit diagnostic run `059f8598` ends on a Gemini provider error after 15 worker
tools. Its failed child transcript keeps only the inbound request, so the report loses every worker
tool name, input, and result. The eval harness now reconstructs completed model and tool steps for a
failed or cancelled child whose current transcript has no calls. It does not change the production
transcript or model context. The focused failed-child and timeout-trajectory tests pass on SQLite
and Postgres.

Repeated run `c021956d` retains the missing evidence. The worker rejects one disallowed source
export, one malformed `<<<<` edit patch, and two exact repairs that reach compilation. Both exact
repairs fail because the fresh worktree contains no generated `sdk/kit.js`. The case is invalid for
source-edit comparison. Add a no-spend app-stack prerequisite for the generated kit, build it, and
repeat before changing the edit input boundary.

The app stack now requires generated `kit.js` and `kit.css` before database seed or model work.
After the kit build, run `5a5961e1` compiles and admits one exact source repair. Product QA then
shows a separate harness fault: the Vite page correctly requests root `/assets` URLs, while the
framed audit server exposes those files only below `/dist/assets`. Keep the source evidence and fix
the audit mount before another paid source-edit comparison.

The audit server now maps root `/assets` requests to the staged `dist/assets` tree without changing
the deployment build. A deterministic replay of the archived `5a5961e1` application loads all four
views with 51 checked text nodes each, 15 controls, eight visible state changes, and no console
errors. The same application now reaches its real contrast failures. The repeat `d5dc5532` ends
before source work when Gemini returns an empty response twice, so it is excluded from the server
gate.

The five-case `app-builder-activity-line-keys` ablation tests one schema description against the
unchanged control. Hard passes stay at zero because both arms still reach product failures. The
claimed process boundary improves: parent delegation calls fall from 18 to five, over-length input
errors fall from six to zero, complete one-worker processes rise from one to three, cost falls from
$1.05 to $0.93, and summed case wall time falls from 1,402 to 1,294 seconds. Keep the activity-line
description.

The four-case `app-source-export-boundary` ablation compares the unchanged control, a worker prompt
clause, and a source-field contract. Hard passes stay at zero because product checks fail later.
Export rejections are three, three, and zero. Source read and edit calls are 24, 25, and six. Cost is
$0.57, $0.54, and $0.48. Summed case wall time is 1,124, 1,199, and 1,080 seconds. Keep the local
source-field contract and reject the prompt clause.

The field arm also exposes one deterministic edit parser fault. The code-review worker sends 16
valid search and replacement blocks with the standard `>>>>>>> REPLACE` closing marker. The typed
input accepts only `>>>>>>>` and rejects the full batch. Accept both closing forms and keep the same
exact-replacement checks in the handler.

The four-case `app-source-import-boundary-exact` ablation tests one source-field clause against the
unchanged export constraint. Hard passes stay at zero because product checks fail later. Imports
outside `ufo/kit` fall from two to zero, initial source rejections fall from two to one, source read
and edit calls fall from 34 to 15, cost falls from $0.58 to $0.54, and summed case wall time falls
from 1,148 to 1,009 seconds. Keep the named-import clause.

Every connected case in the new runs also reports no rendered desktop text. The archived audit
files contain valid views and above-fold text, but the browser script does not return its computed
`renderedText` or `renderedParts`; it also returns an older leaf-text approximation instead of its
painted `aboveFoldText`. Return the computed fields and pass the text style and box in the existing
visually-hidden check. This restores the existing connected-fact and copy grader contract without
changing a model input.

Combined run `51504079` at the rendered-text head passes `code-review-queue` and fails the other
four cases on real fact, placement, action-copy, or contrast gates. Failed-case app scores range
from 0.77 to 0.84. The `app-single-delegation` arm removes one repeated cached parent call but
regresses `meeting-tasks` from pass to fail and raises cost from $0.67 to $0.97. Reject the wording.

The combined-run SVGs locate the remaining fact loss. `pre-meeting-briefs` omits the same four facts
in its SVG and page. `issue-owner` omits the same chat-review fact in both. `issue-planner` omits
three facts in both and loses one more during implementation. `meeting-tasks` carries every fact
into the page but fails placement and contrast. `code-review-queue` adds one fact during
implementation and passes.

The design-fact preflight tests the existing audit fact contract before accepting the SVG. Runs
`873a665a` and `22dda176` stay at one of five hard passes. Cost falls from $0.73 to $0.70 and $0.67,
but the passing case changes and other app scores move in both directions. The preflight does not
prove SVG-to-source fidelity and depends on an eval-only contract. Revert it. The next SVG phase
needs a product contract that measures implementation fidelity without exposing hidden grader
facts.

Exact SVG text is also not a usable fidelity contract. Only 10 to 22 of 39 to 56 SVG labels appear
verbatim in most rendered pages because implementation splits, shortens, or rewrites composite
labels. The SVGs use anonymous groups and no stable region ids. Capture the accepted SVG and a
sandboxed static SVG preview in the HTML report before defining a structural region contract.
Run `be872908-919a-4399-8437-0fc3d73402bf` proved the archive path on a failed
`code-review-queue` case. The report retained the SVG, its static HTML preview, both app previews,
the audit, and both screenshots.

Named visible regions provide the structural fidelity contract. Screen experiment
`app-design-regions-screen` compares runs `6234d087-9eff-4f88-9295-a4a645ac5b4a` and
`d97e0351-5ed5-4b68-8816-088650a995b7`. All three treatment apps use the same 4 to 5 semantic
region names in SVG groups and app containers. `meeting-tasks` moves from fail to pass.
Two-repeat confirmation `app-design-regions-confirm` compares control runs
`ac8da585-5600-45df-915c-aca986d4e818` and `f6bedff5-9986-4b09-a29a-6d9e47f1abaa` with treatment
runs `4367cb67-61bd-40d8-87a0-a3d45bb3b7d4` and
`6b484db9-c2a8-42b8-ab26-dd55de9908c9`. Hard passes move from 1/6 to 2/6, below the sample signal
threshold. Mean product score moves from 0.763 to 0.906 in the same six samples, and every case
family improves. Mean region fidelity is 0.993. Total wall time falls from 1,748 to 1,584 seconds;
cost moves from $0.718 to $0.732. Keep the instruction and let product QA enforce 2 to 6 unique
SVG groups, matching visible app containers, and relative desktop order.
Exact-commit run `7c90fb9d-8e88-4e69-9415-7130a9c9c795` passes the enforced product audit with
19/19 region checks, two bounded QA calls, all other deterministic page layers, and all 11 visual
criteria. Its 0.993 app score fails only because the existing copy grader finds one retained
transcript filler sentence.

Creation run `062c23a0-faf1-4ac0-aa5e-0a56b5bc0506` proves the bounded member revision. A06 uses
two ordered previews and carries the accepted overdue-queue change into the application prompt.
A09 then exposes a false region failure: a full-width queue above a lower watch list changes their
horizontal centres without changing their order. Region fidelity now compares vertical separation
first and compares horizontal separation only when vertical ranges overlap. Exact-head run
`1de258cf-75f0-4c4e-a624-de1fb7cf5b01` keeps one parent builder call, both previews, the accepted
revision, and the corrected region proof. It stops only on four 4.46:1 light-mode text samples
after the worker uses both repair rounds. Improve that typed contrast evidence in the next unit;
do not lower the 4.5:1 threshold.

The contrast audit already captures each failed label and foreground colour, but its typed model
drops both and returns only a tag such as `div`. Preserve the label and measured foreground and
background in the repair issue. Run `1fec6df8-bc6b-4dce-b990-1f55208d2e30` shows the worker using
that exact evidence for one bounded source edit. It still fails on a different app with severe
dark contrast, phone overflow, and built-page region order. Its preview contract calls the requested
overdue queue `Waiting on you`; the later retained-contract check classifies that separate label
failure as a grader defect. The unit improves the deterministic repair input and does not change a
threshold or claim a hard-pass gain.

Keep the product audit's fixed waits. Four inferred-settlement designs fail browser probes. A
microtask can miss a delayed bridge response. A first-visible-change wait can return before a write
starts. The bench preview harness answers each call inside one synchronous message handler, so its
call and response counters never read as busy. The rejected `window.__ufoPending = CALLS.size`
signal reads zero before a delayed call starts, between action phases, and during a stream. It can
also measure a late-rendering page before its real text arrives and hide a contrast failure.
Retained replay does not prove this timing property because the retained application sources
render fixed fixture data without bridge calls. Reject all four designs. A replacement needs an
explicit application lifecycle signal for unary calls, streams, and delayed starts, with an
executing browser regression for each case.

The lifecycle implementation is private runtime state, not application source or model instruction.
Startup stays active through passive effects. Unary calls, streams, one-shot timers, and
observation epochs stay active through their terminal boundary. A repeating timer has no terminal
boundary, so it holds blocking work only while one of its ticks runs, and a page that polls reaches
idle between its ticks. Clearing a timer stops future ticks; work from a running tick stays active
through settlement. The audit captures only when the mounted generation has no blocking work and
its revision is stable across two animation frames. Missing, persistent, and unstable work fails
with one bounded private lifecycle diagnostic.
This unit supplies the lifecycle proof for Phase 2 Gate D. Gate D stays Active, and execution row
22 stays Pending, until the unchanged presentation set runs.

Run `1fec6df8-bc6b-4dce-b990-1f55208d2e30` also contains one stable scheme-token error. Three styles
use `--color-ink` as a background and literal white as text. The pair passes light mode and fails
dark mode because ink changes with the scheme. The source guard now rejects literal white on a
`--color-ink` background and directs the worker to `--color-surface`. It rejects the archived
initial source before compilation and the first
42-second product audit. A scan of 22 retained Vite applications finds only this failed source;
valid generated pages already use the paired tokens.
The final Stage F reserve case is run `1fec6df8-bc6b-4dce-b990-1f55208d2e30`, so this deterministic
unit gets focused and archived-source proof without another model call.

Do not test templates in this campaign.

## Stage A: Production target

Recover a missing final OpenRouter usage event from its generation record only after the Gemini
stream has a terminal finish. Reject an incomplete stream. Keep the provider regression beside the
adapter before another comparison.

Extend the eval environment so the main Opus turn spawns `ufo_application_builder` once. The worker
owns connector inspection through deployment and returns structured evidence. The artifact probe opens the same framed
`app.tsx` page, kit assets, bridge transport, and endpoint table that the portal uses. The bridge
reads from the existing fixed fixture universe. The visual judge receives the same two screenshots.
The deterministic audit sees the same DOM and controls.

Replace the direct-build process check with these checks:

- The parent successfully spawns the application-builder profile exactly once before application
  work starts.
- The worker owns connector inspection, `app.tsx`, browser QA, repair, deployment, and structured
  evidence. Product acceptance owns homepage binding. The parent owns none of them.
- The worker compiles each source change before it lands.
- Browser QA finishes before deployment. Product acceptance finishes before homepage binding.
- The parent gives one final response after the worker returns.
- A timeout retains the parent and every started child trajectory, handoff, model charge, and file
  artifact.

Screen these five cases because they cover the main current failure shapes:

| Case | Boundary |
|---|---|
| `pre-meeting-briefs` | Three sources, missing interaction proof, and a page error. |
| `meeting-tasks` | Strong source coverage and the lowest density score. |
| `issue-owner` | Near-pass regression control. |
| `engineering-metrics` | Dense data, charts, and weak QA. |
| `account-health` | Strong product score and weak QA. |

Measure the worker-only path. An optional diagnostic arm can inspect no more than six novel final-
audit failures. It does not repair the artifact or enter accepted throughput. Run two screen cases
first, then the five-case screen, then one ten-case confirmation if the gate passes.

Gate:

- Every kit artifact compiles and loads through the production runtime.
- Every case uses the builder profile, and no case changes the main agent model.
- A passing worker-only case uses one parent delegation and no Opus verification or repair.
- A known process failure changes a deterministic contract or worker rule before another case runs.
- The bridge supplies all required reads without fixture facts in the member prompt.
- Product score loses no more than 0.02 on any screen case.
- Source, density, and interaction coverage do not decrease.
- No new timeout, provider exclusion, console error, or deployment failure appears.
- Median wall time and cost each increase by no more than 20%.
- Worker throughput is at least 12 completed cases per hour at screen concurrency.

## Stage B: Data and copy contract

Define one boundary model, `ApplicationBuildContract`. It crosses from planning into the builder,
so it is a validated serializable model. It contains explicit records, not open dictionaries.

| Record | Carries |
|---|---|
| Screen | Purpose, audience, regions, and first-screen priority. |
| Fact | Stable ID, source reference, value, label, and required placement. |
| Copy task | Source references, reader intent, length bound, and evidence rule. It contains no expected sentence. |
| Control | Accessible name, initial state, member action, changed state, and refusal state. |
| Connection | Provider, account state, needed reads, and prepared actions. |
| Acceptance check | Observable browser or durable-state result. |

Every record has one producer and one consumer. The same Gemini worker produces the contract from
its connector reads and consumes it before its first file write. A deterministic compiler arm can
produce the same schema without another model. The artifact digest enters the trajectory evidence.
The contract never appears in the rendered page or the Opus parent context.

Test these arms on `pre-meeting-briefs`, `meeting-tasks`, `issue-planner`, and the `issue-owner`
regression control:

1. Current builder with an in-memory plan.
2. Deterministic contract compiled from the fixed fixture and prompt requirements.
3. A typed contract producer inside the Gemini worker that sees the same source records and writes
   the same schema.

Run three repeats. Arm 3 runs only if Arm 2 passes. This isolates the value of the handoff before it
adds another model.

Gate:

- Every target case improves its named weak layer.
- `meeting-tasks` density increases by at least 0.15.
- `issue-planner` and `pre-meeting-briefs` density increase by at least 0.10.
- Source coverage does not decrease, and copied source prose decreases.
- `pre-meeting-briefs` interaction passes.
- `issue-owner` loses no more than 0.02 product score.
- Median wall time increases by no more than 15%; total cost increases by no more than 25%.

## Stage C: Component substrate

Use the portal kit as the component authority. Do not add a second shadcn identity beside it. Test
three owned starting surfaces:

1. Blank `app.tsx` with the full kit API.
2. A thin page starter that mounts `SectionApp`, connects the bridge, and leaves all product regions
   empty.
3. The thin starter plus a small catalog of existing kit compositions for a queue, metric group,
   table, detail panel, and prepared-action control.

The compositions contain no product copy, fixture values, page layout, or case-specific component.
They use only existing kit exports. A catalog item that no builder reaches does not ship.

Gate:

- The treatment is used in every treatment trajectory.
- AA contrast, clipping, density, and accessible controls improve or remain perfect.
- Raw colour overrides and copied portal components do not enter generated source.
- Product score increases by at least 0.03 or median wall time falls by at least 20%, with no product
  decrease.
- The smallest winning surface ships.

## Stage D: Model and role routing

Run model comparisons only after the worker contract and kit input are fixed. Avoid a full
cross-product. Every arm changes the worker profile or a later typed role; no arm changes the main
agent or adds routine Opus verification.

Use `meeting-tasks`, `issue-planner`, and `issue-owner` for three repeats.

Builder screen:

- Gemini 3.7 Flash at `medium` in the builder profile as control.
- GPT-5.6 at the lowest reasoning setting that completes the production target in the same profile.
- GLM 5.3 at its best supported bounded reasoning setting in the same profile.

Role screen:

- One winning worker owns contract, copy, build, QA, and deployment.
- Luna owns contract and copy only if that typed handoff improves accepted throughput.
- Opus or Fable receives only failed deterministic checks. It never enters a passing run.

Limit expensive repair models to six evaluated failures. A role that is not called, returns an
invalid artifact, or is ignored fails its arm.

Gate:

- No case loses product score.
- At least two of three cases improve the named layer or pass the binary gate.
- Median wall time falls by 15%, or product rises by 0.05 within 25% more cost.
- Every role output has one required consumer.
- If one worker matches the larger portfolio, keep one worker.

Result:

| Arm | Recorded cases | Strict passes | Product | Process | Median wall | Recorded cost |
|---|---:|---:|---:|---:|---:|---:|
| Gemini 3.7 Flash, medium | 9/9 | 1 | 0.921 | 1.000 | 258 s | $1.05 |
| GPT-5.6, low | 9/9 | 2 | 0.900 | 0.852 | 166 s | $1.80 |
| GLM 5.3, high | 6/9 | 0 | 0.809 | 0.889 | 627 s | $1.52 |

GPT low lowers median wall time by 35%, but `issue-planner` falls from 0.878 to 0.783 and two cases
do not finish the worker process. GLM excludes three cases: two reach the 900-second deadline, and
one provider idle timeout causes the parent to attempt a second delegation. The retry is not valid
topology evidence.

A final reasoning screen compares Gemini medium with GPT medium and high. Gemini scores 0.940 with
full process completion. GPT medium scores 0.825 with 0.667 process completion. GPT high scores
0.828 with 0.778 process completion. Both GPT arms are faster and more expensive, but neither
repairs the product or process regression. Keep one Gemini worker. Do not add a copy role or a
routine repair role.

## Stage E: Connected actions

Add three production-shaped action cases:

| Case | Browser action | Durable proof |
|---|---|---|
| `action-meeting-tasks` | Approve one proposed issue. | The prepared turn creates the expected GitHub fixture issue and the refreshed page shows it. |
| `action-issue-owner` | Approve one assignment. | The prepared turn assigns the expected fixture owner and the queue updates. |
| `action-pr-babysitter` | Change the babysitter state or model. | The prepared turn updates the typed setting and the page shows its new state. |

The fixed connector service matches the production tool schemas and owns mutable external state for
the case. The grader tests the application, bridge, turn, object verb, and refreshed projection. It
does not assert the fixture service's internal implementation.

Each page also has a setup and status view. It reports required connectors, connection state, and
notification surfaces. A setup control opens or prepares the correct chat action. It does not grant
access, start OAuth, or change a notification target outside chat.

The first setup screen shows every fixed setup fact in two of three apps, but none opens chat and
all three omit the prepared application action. The case had replaced the action requirement with
the setup requirement, and “review in chat” allowed a local success message. The corrected case
keeps the action contract and requires the member outcome: open chat when setup review starts.
Action and setup proofs report separately. Thirty-three Stage E cases are used.

The corrected screen keeps both requirements. One app passes its prepared action; all three setup
controls remain unmeasured because they appear only after the audit opens the Setup tab. The audit
now explores one nested control level in a fresh context, so a tab cannot hide its controls from
interaction proof. This is a general browser correction. Thirty-six Stage E cases are used.

The final screen passes every prepared action. All three setup controls open chat and make no
direct setup write. Two setup facts pass exact text matching. The third uses `Active` for
`selected` and `Disconnected` for `not connected`; these are clearer equivalent member terms. The
grader accepts only those two pairs. The corrected deterministic result is 3/3 without another
model run. Thirty-nine Stage E cases are used; three move to final confirmation.

First screen:

- The connector fixture supplies exact structured `ufoWrite` and `ufoRead` argument lists beside
  the matching GitHub resources. The member prompt carries no object kind, name, spec, route, or
  fixture fact.
- The browser audit records the exact bridge call and reloads the page in the same browser context.
- The harness replays that captured call through the real prepared-intent admission lane. The
  eval-only object kind mutates the fixed GitHub fixture.
- The harness submits the same idempotency key twice, submits one invalid action, and reads the
  action and connector projection from durable storage.
- The first three-case Gemini screen completes with no exclusion and full process score. It is not
  valid action evidence: concurrent case seeds overwrite one shared GitHub response, and the one
  surviving contract does not state the bridge argument order.
- The second screen keeps one stable action list per GitHub tool and supplies structured `ufoWrite`
  and `ufoRead` argument arrays. Two apps make the exact write. The third app imitates the result
  in local storage and fails the source-independent action check.
- The next screen passes the complete action proof for two of three apps.
  `meeting-tasks` does not make the exact browser write.
- Exact action wording does not move `meeting-tasks` in a matched three-repeat ablation. Both arms
  pass zero of three. The wording is removed.
- Moving each action beside its target record lowers the action score from 0.667 to 0.333. The
  shared action list remains.
- Full read, write, reload, and storage wording changes which two apps pass, lowers product to
  0.663, and lowers process to 0.667. The wording is removed.
- One generic `ApplicationAction` kit component owns the exact write, durable read, refusal, busy
  state, and visible result. It contains no case data or page layout. Naming it only in the
  connector record does not route it reliably: `issue-owner` uses it and passes, while the other
  two apps omit it and fail the exact write.
- A matched generic component rule makes all three apps use the component, but both arms already
  pass all three durable action proofs. The rule lowers mean product from 0.895 to 0.863 and
  regresses two apps, so it is removed. The unchanged worker path passes the prepared-action gate.
  Thirty of the 42 Stage E cases are used. No failure triggers routine Opus repair.

The setup and notification view is the second Stage E screen. It starts only after the three
prepared actions pass, so its connector-state and chat-opening requirements have one stable action
consumer.

Gate:

- Browser click to prepared turn to durable result to refreshed page passes for all three cases.
- A refused action changes no connector or application state.
- A second delivery of the same prepared intent is idempotent.
- Private and shared fixture accounts remain inside their member and workspace boundaries.
- No generated member-facing endpoint enters the app.

## Stage F: Creation flow

The creation conversation and the homepage build are separate flows. The main agent proposes,
interviews, settles a small typed design contract, and receives the member's approval in chat. A
product-owned renderer turns that contract into the preview. It does not run a model, generate app
source, host a site, or change member state. After approval, the main agent creates one `agent`.
That application's first homepage turn delegates once to the Gemini worker. The worker reads
connectors, builds `app.tsx`, runs QA and repair, and stages the page. A deterministic audit grants
the homepage bind only when the staged page passes. The application then gives one final response.

The design contract contains only the page purpose, first-screen priority, regions, layout choice,
and member design direction. The fixed renderer owns the house components and tokens. A revision
replaces the contract and renders again. The accepted contract enters the application prompt and
the worker task, so the final audit can compare the built page with what the member approved.

Opus performs language work only: proposal, interview, the typed design choice, and the final chat
response. It does not write source, call browser tools, read connector data, inspect audit output,
or repair a build. A normal creation journey has one Gemini build delegation and no diagnostic
Opus turn.

Run four journeys through the accepted target, contract, substrate, and model route:

1. A named application with enough detail gets one prefilled interview, one design preview, one
   approval, one Gemini build, and one accepted homepage.
2. `Build me a new app.` gets one proposal and then that same run, so it reaches the create a round
   later.
3. A member changes the design preview once. No application exists before `Build it`. The accepted
   design reaches the created application's prompt and homepage.
4. A deterministic build failure retains its source and evidence and binds no homepage. A later
   Gemini attempt repairs the same application and binds its first accepted homepage.

The member reviews the application contract and preview, not an implementation plan. Each approval
is a chat turn. The `agent` is the application identity. Its bound `site` is the homepage identity.
Build turns and their archived source and evidence are attempts, not a second application object.

Gate:

- No agent or grant exists before the member's final approval. A design preview is a shared image,
  not a hosted site.
- The preview renderer finishes without a model call and adds no generated source or browser QA to
  the creation turn.
- One Gemini delegation owns connector reads, source, QA, repair, staging, and its structured
  evidence. No routine Opus verification or repair occurs.
- Every attempt retains source, screenshots, deterministic audit, browser proof, cost, and failure
  evidence under its build turn.
- A failed attempt binds no homepage. A later accepted attempt keeps the same agent and binds or
  updates one homepage identity.
- Product acceptance, not the Gemini report, admits homepage binding.
- The member opens the app and completes its benchmark task through the portal.

## Promotion rules

Screen arms use continuous scores to find a load-bearing change. A stage winner then runs the full
unchanged ten-case suite. The campaign winner must improve the hard pass count.

- Run at least two matched repeats for a screen and three for a close result.
- Keep model, reasoning, fixture digest, prompt, rubric, tool set, case order, concurrency, and time
  bound fixed inside one comparison.
- Reject a treatment with a new timeout, provider fault, missing artifact, unused handoff, or
  unconsumed output.
- Reject a mean improvement caused by one case when another case regresses by more than 0.02.
- Record binary, every product and process layer, wall time, model time, tool time, rounds, tokens,
  cost, parent and child model use, and artifact links.
- Revert rejected code and wording in the same experiment branch.

Campaign exit:

- At least 7 of 10 connected cases pass every binary gate.
- Product score is at least 0.92 and process score is at least 0.90.
- The three presentation controls pass, including interaction and rework.
- At least 6 of 7 copy cases pass.
- All three prepared-action cases pass.
- All four creation journeys pass.
- No infrastructure exclusion remains in the confirmation run.

## Run ledger

Add one row after each completed comparison.

| Stage | Run | Arms | Cases | Binary | Product | Process | Cost | Wall | Decision |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| A baseline | `eb0445e9` | Current standalone Gemini path | 14 | 1/14 | 0.905 | 0.738 | $1.760 | 2,614s sum | Keep only as the pre-correction baseline. |
| A smoke | `4dcc42a8` | Opus parent, Gemini builder, production scaffold | 2 | 0/0, 2 excluded | — | — | $5.179 observed | 1,800s sum | Reject the repair loop. Keep the boundary and fix its typed fixture and framed-QA contracts. |
| A correction | `fb98e473` | Opus parent, Gemini builder, bounded repair | 1 | 0/1 | 0.936 | 0.667 | $4.186 | 884s | Keep the product result. Reject the source-ownership path and fix the builder boot contract. |
| A source control 1 | `c65663c4` | Builder-owned full-source repair | 1 | 0/0, 1 excluded | — | — | $1.984 retained | 900s | Reject clipped full-source repair. The report predates child evidence retention. |
| A source control 2 | `17609a3a` | Builder-owned full-source repair | 1 | 0/0, 1 excluded | — | — | $2.684 | 900s | Reject clipped full-source repair. Keep the child evidence retention fix. |
| A bounded repair | `fb0f8c20` | Diverse excerpts and exact edits | 1 | 0/0, 1 excluded | — | — | $2.455 | 900s | Reject repeated reads and full-build fallback. Enforce one source claim and one read-edit-result repair. |
| A repair protocol | `8d7052f0` | One source claim and three-round repair | 1 | 0/0, 1 excluded | — | — | $2.939 | 900s | Keep the source claim. Align the edit schema with the child output and enforce one read. |
| A edit schema | `607b1207` | Observed edit fields and one read | 1 | 0/1 | — | — | $3.405 | 894s | Keep the successful narrow edit. Add structural excerpts and normalize encoded edit objects. |
| A structural excerpts | `ca0e63cd` | Fixed structural and diagnostic windows | 1 | 0/0, 1 excluded | — | — | $2.969 | 900s | Reject copied exact text. Replace shown ranges by digest-bound excerpt ids. |
| A excerpt edits | `1dac29b3` | Digest-bound source-range edits | 1 | 0/1 | — | — | $3.168 | 788s | Keep the handle contract. Return fixture, style, and rendered occurrences for each diagnostic term. |
| A balanced excerpts | `da8b4605` | First, middle, and last diagnostic matches | 1 | 0/0, 1 excluded | — | — | $2.785 | 900s | Keep balanced matches. Restore five initial-build rounds behind the one-read repair gate. |
| A complete repair | `b0ada4f6` | Five-round builder with digest-bound edits | 1 | 0/1 | 0.941 | 0.333 | $2.512 | 639s | Keep the final app and bounded edits. Correct the shell-write false positive and the app scaffold's unsafe text roles. |
| A QA ablation | `1c5d4dba` / `fec7bdea` | Existing QA / split functional and visual QA | 2 | 0/1, 1 excluded | 0.978 / — | 1.000 / — | $1.559 / $2.259 | 429s / 900s | Reject the split QA wording. It increased browser calls from 4 to 8 and builder handoffs from 1 to 4, then timed out. |
| A final screen | `62a321a1` | Structured diagnostics and semantic action proof | 2 | 0/0, 2 excluded | — | — | $2.603 / $2.957 | 900s / 900s | Stage A fails. Preserve the traces, accept structured repair diagnostics, and reject structural source damage. |
| A guarded repair | `d44e616c` | Structured diagnostics, source guard, and inherited child authority | 2 | 0/2, 0 excluded | 0.919 / 0.931 | 1.000 / 0.667 | $2.556 / $2.311 | 732s / 621s | Keep the timeout fixes. Start a new comparison identity because the static-DOM source grader rewards hidden tab content and misses browser-reachable facts. |
| A interactive control | `3b56e4c4` | Standalone Gemini with browser-state source grading | 2 | 0/2, 0 excluded | 0.964 / 0.931 | 0.667 / 0.667 | $0.121 / $0.065 | 165s / 143s | Keep as the corrected standalone reference. It exposes all briefs facts through controls, but both cases use only one QA batch. |
| A supervised repeat | `5aefa9b2` | Opus-supervised Gemini repair loop | 2 | 0/0, 2 excluded | — | — | $2.409 / $2.510 | 900s / 900s | Reject routine Opus supervision. Both cases time out before deployment after repeated source, browser, and repair handoffs. |
| A worker screen 1 | `131665ac` | One Gemini end-to-end worker with prompt-only parent boundary | 2 | 0/1, 1 excluded | 0.565 | 0.333 | $2.483 / $0.816 | 900s / 374s | Reject prompt-only parent ownership and range-ID repair. Enforce the parent tool set and use same-turn exact edits. |
| A worker screen 2 | `7bfa7bb0` | Enforced parent tools and same-turn exact edits | 2 | 0/2, 0 excluded | 0.873 | 0.333 | $0.662 / $0.491 | 394s / 497s | Keep the worker loop. Replace generic spawn with one fixed-target delegation and refuse browser QA after four batches. |
| A worker screen 3 | `e5fb5b44` | Fixed-target delegation and four executed QA batches | 2 | 0/2, 0 excluded | 0.437 | 0.500 | $0.325 / $0.347 | 213s / 357s | Keep the parent and QA bounds. Force a fresh REPL, reject global React, and block deployment until two QA batches pass. |
| A worker screen 4 | `bb0d8f22` | Fresh REPL, React binding, and QA-gated deployment | 1 | 0/1, 0 excluded | 0.848 | 1.000 | $0.183 | 255s | Keep. The process passes. Move buried facts and contrast into the data/copy and component stages. |
| A five-case screen | `8d47c7eb` | Parallel fixed-target workers with independent acceptance | 5 | 0/5, 0 excluded | 0.891 | 0.867 | $0.117–$0.232 | 190s–380s | Keep the worker and QA gates. Enforce Skill routing. Start the typed data/copy contract. |
| A worker Skill preload | `d4202f99` | `website-building` preloaded in the Gemini worker | 1 | 0/1, 0 excluded | 0.946 | 1.000 | $0.245 | 333s | Keep the worker preload. Remove the duplicate parent Skill load and pass the original request verbatim. |
| A direct parent delegation | `c5b9851a` | Verbatim request, worker Skill preload, no parent setup tool | 1 | 0/1, 0 excluded | 0.913 | 1.000 | $0.266 | 228s | Keep. The parent uses two rounds and one tool. Start Stage B; use accepted throughput, not parent verification, for further routing work. |
| A argument-free delegation | `d8772e2e` / `79b94536` | Empty tool schema with exact / stale Skill call text | 1 | 0/1 / 0/1 | 0.860 / 0.911 | 1.000 / 1.000 | $0.203 / $0.219 | 188s / 231s | Keep the empty schema and strict scorer. Both arms make one call, so remove the redundant call syntax from the Skill. |
| B nested-contract control | `86622d07` | Optional nested contract tool with current worker text | 2 | 0/2, 0 excluded | 0.928 | 1.000 | $0.300 | 209s–260s | Reject the tool shape. Both workers attempt invalid contract calls; one later records a contract. |
| B nested-contract wording | `f8822f81` | Same tool plus explicit contract procedure | 2 | 0/2, 0 excluded | 0.943 | 0.833 | $0.327 | 237s | Reject. No case moves, cost rises 9%, density falls, and one final QA batch fails. |
| B fixture projection | `02ab008e` / `1771eab0` | Direct fixtures / deterministic fact and copy projection | 2 | 1/2 / 1/2 | 0.973 / 0.916 | 1.000 / 0.667 | $0.294 / $0.308 | 207s–295s / 248s–251s | Reject and remove. The worker reads the projection, but no product layer improves. One treatment case fails final QA and homepage binding. |
| C leaked starter screen | `56f013b1` / `d04c82d7` | Blank prompt / explicit `SectionApp` starter | 2 | 0/2 / 0/2 | 0.860 / 0.916 | 0.667 / 0.833 | $0.225 / $0.235 | 227s–267s / 246s–292s | Invalid comparison. The starter file is visible in the control's loaded Skill. |
| C leaked starter repeat | `a23c6df1` / `3e83c0b5` | Blank prompt / explicit `SectionApp` starter | 2 | 0/2 / 0/2 | 0.934 / 0.894 | 1.000 / 1.000 | $0.232 / $0.242 | 232s–279s / 210s–212s | Reject and remove. The control reads the treatment file, and the treatment result reverses. |
| C isolated starter | no records | Pre-starter base / treatment-only starter replacement | 2 per arm | No records | — | — | $0 recorded | — | Stop. Both serves receive `SIGTERM` during active child turns. Repair the harness lifecycle before another paid arm. |
| C traced interruption | no records | Same isolated arm with process lifecycle records | 2 per arm | No records | — | — | $0 recorded | — | Both measured serves exit `-15` at the same instant with no stack terminate event. A concurrent dev workflow ran `pkill -f "ufoctl serve"`. |
| C isolated starter screen | `57c31a4f` / `bbad57b2` | Full taste reference / starter replacing taste | 2 | 0/2 / 0/2 | 0.903 / 0.918 | 0.833 / 1.000 | $0.227 / $0.365 | 239s–303s / 208s–397s | Repeat. Mean product rises 0.015, but one case regresses and cost rises 61%. |
| C isolated starter repeat | `9c58733a` / `a75b8b80` | Full taste reference / starter replacing taste | 2 | 0/2 / 0/2 | 0.902 / 0.932 | 1.000 / 1.000 | $0.247 / $0.212 | 235s–237s / 212s–228s | Reject. The combined two-run gain is 0.023 and median time falls 7.5%; both miss the gate. The treatment also removes the taste reference. |
| C inline starter screen | `4cccdbbd` / `27555a0e` | Existing Skill / existing Skill plus inline `SectionApp` shell | 2 | 0/2 / 0/2 | 0.891 / 0.848 | 1.000 / 0.667 | $0.339 / $0.248 | 251s–345s / 274s–280s | Reject. The isolated shell lowers product 0.043, misses deployment on one case, and lowers process 0.333. Keep the blank kit input. |
| E action contract control | `26897319` | Case-local action contract with ambiguous bridge fields | 3 | 0/3, 0 excluded | 0.572 | 1.000 | $0.388 | 137s–438s | Invalid action comparison. Concurrent seeds leave only one contract, and that worker calls `ufoWrite` with the wrong arity. Keep the worker topology. Make the fixture stable and state exact bridge arguments. |
| E exact action projection | `c6e17c41` | Stable action list with exact bridge argument arrays | 3 | 0/3, 0 excluded | 0.742 | 0.889 | $0.372 | 197s–265s | Keep the data contract. Two apps make exact browser writes. Fix the prepared-intent input omission. The third app uses local storage and remains a worker-rule failure. |
| E prepared-intent fix | `80dde1c2` | Exact action projection through the corrected prepared-intent lane | 3 | 0/3, 0 excluded | 0.790 | 1.000 | $0.509 | 160s–287s | Keep the product fix. Two apps pass the full action proof. `meeting-tasks` makes no exact write. |
| E action wording | `app-action-worker-rule` | Existing worker text / exact bridge action text | 3 per arm | 0/3 / 0/3 | — | — | $0.51 / $0.52 | — | Reject and remove. Exact write wording does not move `meeting-tasks`. |
| E localized action records | `a3948d8d` | Action records moved beside their target resources | 3 | 0/3, 0 excluded | 0.685 | 1.000 | $0.460 | 235s–265s | Reject and remove. Only `issue-owner` passes the action proof. |
| E persistence wording | `821769a1` | Exact durable read, write, reload, and storage procedure | 3 | 0/3, 0 excluded | 0.663 | 0.667 | $0.633 | 195s–360s | Reject and remove. Action remains 2/3, but `issue-owner` fails the build process. |
| E component data route | `ba554f45` | Generic action component named only by connector data | 3 | 0/3, 0 excluded | — | 0.889 | $0.330 | 193s–296s | Reject data-only routing. Only `issue-owner` uses the component, and it passes the action proof. The other two omit it. Test one generic worker routing rule. |
| E component rule | `e4cbec6b` / `22f1799f` | Existing worker / generic action component rule | 3 per arm | 0/3 / 0/3 | 0.895 / 0.863 | 1.000 / 1.000 | $0.36 / $0.35 | 181s–232s / 183s–237s | Reject the rule. Both arms pass 3/3 durable actions. The treatment uses the component in every app but regresses two product scores. Start setup and status. |
| E setup screen 1 | `79f05224` | Setup state and chat review added without the action requirement | 3 | 0/3, 0 excluded | 0.560 | 0.778 | $0.341 | 189s–233s | Reject the case wording. No app keeps the action or opens chat. Two show every setup fact; the third misses the selected state. Keep both requirements and require chat to open. |
| E setup screen 2 | `dfc56546` | Action and setup requirements with a first-load-only interaction audit | 3 | 0/3, 0 excluded | 0.845 | 1.000 | $0.389 | 214s–378s | Invalid setup measurement. One action passes. The audit opens each Setup tab but never discovers the chat control rendered inside it. Explore one nested control level, then repeat once. |
| E setup screen 3 | `df4ccc67` | Nested interaction audit with both action and setup requirements | 3 | 0/3, 0 excluded | 0.923 | 1.000 | $0.358 | 150s–201s | Keep. All actions and chat navigations pass. Accept `Active` / `selected` and `Disconnected` / `not connected` as exact semantic pairs; setup then passes 3/3. Strict failures remain in existing fact and contrast layers. |
| F creation screen | `090bfe4b` | Guided proposal and preview revision on the current creation path | 2 | 2/2, 0 excluded | — | — | $1.280 / $1.990 | 218s / 301s agent-turn sums | Keep the cases and isolation repair. Reject Opus-owned preview source and browser work before the full journeys. |
| F preview worker 1 | `app-creation-preview-worker` | Current Opus preview / Gemini static-preview worker | 4 per arm | 0/4 / 1/4 | — | — | $8.41 / $5.09 | Gemini preview children took 114s–217s | Reject and remove. Cost falls, but the extra model-built artifact does not improve accepted speed or quality. |
| F preview worker 2 | `app-creation-preview-worker-fast` | Current Opus preview / bounded Gemini static-preview worker | 4 per arm | 0/4 / 3/4 | — | — | $9.12 / $4.03 | First previews took 137s–153s / 139s–149s | Reject and remove. Ownership and cost improve, but preview latency does not. Replace the preapproval model build with a product renderer. |
| F product renderer | `app-creation-preview-renderer` | Current Opus preview / product-rendered PNG returned by path | 4 per arm | 0/4 / 0/4 | — | — | $9.18 / $4.53 | Renderer 174ms–229ms | Reject the file handoff. Opus reads the returned PNG before sharing it. Keep the deterministic renderer and make delivery atomic. |
| F atomic preview setup | `app-creation-shared-preview` / `app-creation-shared-preview-corrected` | Current Opus preview / product renderer sharing its own PNG | 2 per arm each | Invalid | — | — | $5.13 / $1.95 and $3.33 / $1.44 | Treatment 142s–214s and 124s–157s | Do not score. The archived grader still expected a separate share, then exact prompt text failed across Markdown line wrapping. Keep the traces and correct the shared grader. |
| F atomic preview final | `app-creation-shared-preview-final` | Current Opus preview / atomic product render and share | 2 per arm | 0/2 / 2/2 | — | — | $5.10 / $1.86 | 342s–464s / 167s–183s | Keep. The fixed renderer takes 20ms–380ms, uses no child, and the accepted contract reaches the durable application prompt. |
| F named journey smoke | `app-stage-f-journey-smoke-3` | One application parent and one Gemini worker | 1 | 0/1 by archived grader | — | — | $1.066 | 401s | Keep the one-delegation trace. The child task contains the full application prompt; the grader compared raw text with JSON encoding and failed falsely. Move homepage binding from Gemini to product acceptance before the matched repeat. |
| F named journey acceptance | `24fa97eb` | One application parent, one Gemini worker, product-owned binding | 1 | 1/1 | — | — | $1.480 | 453s | Keep. Product acceptance verifies the deployment, retained `app.tsx`, and two recorded browser batches before it binds. Gemini makes no homepage call. |
| F guided and revised journeys | `4a2b9988` / `3c2160ff` | Guided approval and one preview revision through the full build | 1 each | 1 excluded / 0/1 by archived grader | — | — | $13.153 / $1.272 | 1,041s / 488s | Keep both traces. A08 repeats full source until timeout. A09 builds and binds, but exact pronoun matching rejects the accepted purpose. |
| F source candidate | `34351d8c` | One full source candidate, then bounded reads and edits | 1 | 0/1 | — | — | $1.103 | 346s | Keep the source bound. Child tokens fall from 4.05M to 410K. Reject binding the untouched scaffold after a failed candidate. |
| F guided acceptance | `fdf51a7d` | Compiler-owned source and digest-matched product binding | 1 | 0/1 by archived grader | — | — | $0.893 | 306s | Keep. One 225K-token Gemini worker builds and binds the matching 38 KB source in 151s. The corrected contract grader accepts the recorded priority paraphrase. |
| F repair routing | `1c392336` / `c694cb4c` / `27869e6d` | Retained attempts, one product bind, and process-failure routing | 1 each | 0/1 each | — | — | $0.731 / $0.855 / $0.729 | 413s / 629s / 811s | Keep the failure evidence. Remove cumulative follow-up calls, same-turn Gemini retries, and the automatic-seed race. |
| F repair acceptance | `247300f8` | Two sequential parents, two Gemini workers, blocked then deployed | 1 | 0/1 by archived grader | — | — | $0.825 | 648s | Product path passes. The context-prefixed transcript made the second retained turn include both results. Keep the slicer correction. |
| Final presentation confirmation | `9a349a9f` | Ten connected apps and four presentation controls | 14 | 0/14 archived | 0.841 | 0.333 archived | $2.072 | 171s–247s | The process grader expected worker-owned binding. Corrected archival process grading passes 13/14. Keep every strict product failure. |
| Final pre-meeting repair | `9ef3a925` | Corrected topology and source edits | 1 | 0/1 | 0.928 | 1.000 | $0.139 | 290s | Process, contrast, interaction, and visual checks pass. Missing issue, source-age, and first-screen facts remain real failures. |
| Final creation shared stack | `bd83104d` | Four journeys in one workspace | 4 | 1/4 | — | — | $3.482 | — | Invalid isolation. Later cases observed the first case's application. Keep the evidence and isolate workspaces. |
| Final creation confirmation | `12d97f88` / `5dfeb2a8` / `1c5cf398` / `a35add87` | Four isolated creation journeys | 4 | 4/4 | — | — | $3.487 | 330s–418s | Keep. A10 uses exactly two parents and two Gemini workers: blocked with retained evidence, then deployed and bound. |

The original connected subset passes 0/10. All ten connected apps miss at least one required visible fact.
Nine apps have an AA contrast failure, and seven use a browser-QA count outside the allowed range.
Interaction is 1.00 and visual quality is 0.955. The next run must test the one-delegation worker
boundary, not another change to the initialized main model.

The Stage A smoke fixes the parent to Opus 5 at high reasoning and runs Gemini 3.7 Flash at medium
reasoning only in `ufo_application_builder`. Both cases spawn the profile and write only `app.tsx`.
Both reach the unchanged 900-second bound during repeated repair handoffs, so the harness excludes
them and retains their parent trajectories. The saved-app audit still proves the production target:
`meeting-tasks` changes 10 of 10 controls and `issue-owner` changes 4 of 6 controls; both have zero
contrast, clipping, horizontal-overflow, and console failures. The failed process has three direct
causes: both parents first send a structured fixture to a tuple-only field, one parent tests the
empty preview shell instead of its application frame, and one repair child spends its 50-round
default before typed finish fails. The correction accepts bounded JSON fixture input, names the
application frame, removes the unenforced contract hash, sends browser failures directly to repair,
and caps a child at five rounds. The first correction attempt proves a cap alone is insufficient:
two children use all five rounds reading the fixed scaffold and return `blocked`. The builder now
skips product-owned files on an initial build and reads only `app.tsx` on repair.

The correction run completes deployment inside the bound. It passes all four browser interaction
batches and all 11 visual criteria. The main failure is the source boundary: the builder emits a
module form that the classic-script loader cannot start, two repair children do not correct it,
and the parent edits `app.tsx` directly. Six low-contrast divider strings and one missing prepared
action label also fail the page rubric. The next change must make the builder output contract match
the loader and must keep every source repair in the builder profile.

The two source-control runs keep every source change in the builder, but the repair child receives
only the first 6 KB of a 35 KB file. Each full rewrite loses source outside that excerpt and creates
a new mount or hook failure. The harness now retains completed child turns when the parent reaches
its bound, so the second report shows four child spans and their source calls.

The bounded-repair run starts with a valid 26 KB page and passes interaction, clipping, overflow,
and console checks. Two repair children then use all five tool rounds on source reads and make no
edit. A frequent search term fills each 5 KB result before the other requested areas appear. The
parent then sends two requests with empty repair diagnostics, which replace the whole source and
start a fifth browser repair. The next arm gives each requested term a balanced excerpt and match
count, limits the child to read, edit, and result, and lets the source tool accept only one full
write for each application path.

The repair-protocol run keeps the initial 25.7 KB source unchanged. The source tool rejects the
parent's final full-build request. Three repair children reach `edit_application_source`, but the
handler receives no edit: Gemini first sends search blocks, then sends valid replacement objects
with `old_text` and `new_text`, while the tool schema names `old_string` and `new_string`. All three
calls fail validation. The next arm uses the observed object field names and makes a second source
read fail with the exact edit shape. This gives one schema and one read-edit path.

The edit-schema run completes deployment and homepage binding. Its first repair applies three exact
replacements and changes 355 bytes. The second repair sends its edit objects as JSON text, then
guesses one long source block because the excerpt omits the sort implementation. Its normalized
object attempt does not match exactly. The parent then reads and edits source, uses six browser
batches, and produces a page with two primary-button contrast failures and three required facts
below the first screen. The next arm keeps the exact-edit boundary, accepts JSON-encoded objects at
the validated input boundary, and always includes the app function, sort function, style opening,
and root container in the one bounded source excerpt.

The structural-excerpt run keeps source ownership but makes no repair. Both children copy long
source blocks from incomplete windows, and each block differs from the current file. All exact
replacements fail. The next arm removes copied old source from the edit contract. Each returned
window has a digest-bound excerpt id, and an edit replaces only that shown range. The handler
validates the current full-source digest, range digest, range bounds, and overlap before one atomic
write.

The excerpt-edit run completes deployment and homepage binding without a source repair. The child
uses the handle schema, but its requested `Urgent` and colour terms return only their first source
occurrence in fixture data. No returned window contains the failing style or rendered control, so
the child sends an empty edit list. The next arm returns the first, middle, and last occurrence of
each requested term. This keeps the 5 KB result bound while covering data, style, and rendered use.

The balanced-excerpt sample does not reach repair. The initial builder corrects a rejected module
form and then a rejected mount form, but its third tool round ends before the corrected source can
be accepted. A second handoff treats the unchanged placeholder as a repair and cannot use the
initial-write tool. The next arm restores five child rounds. The read tool's per-turn claim still
limits every repair to one source read, so the added rounds are available for bounded edit
correction instead of the old repeated-read loop.

The complete-repair sample finishes with correct interaction, contrast, clipping, deployment, and
homepage binding. Its process score is false: the scorer treats `head app.tsx` as a shell write.
The page also has to override the portal's 3:1 secondary text role because the fixed application
scaffold promises AA text. The corrected scorer detects shell mutations instead of file mentions,
and the scaffold supplies AA-safe application text roles without changing the portal theme.

The matched QA ablation rejects the new wording. The existing reference completes with one builder
handoff, four browser batches, full process score, and 0.978 product score. The treatment reaches
eight browser batches and four completed repair handoffs before the 900-second bound. Its browser
guidance is reverted. The control app renders both `Prepare an assignment` and `Assignments are
made in chat`, but the grader accepts only one exact combined phrase. The requirement now proves
the prepared action and the chat boundary as two separate semantic groups.

The final Stage A screen excludes both cases at the 900-second bound. `pre-meeting-briefs` completes
three builder handoffs and five browser batches. Its first repair request fails because structured
browser diagnostics do not fit the tuple-only input, and later exact edits corrupt JSX tags and
braces. `meeting-tasks` completes four builder handoffs and seven browser batches. Its repairs also
create unmatched braces, then the parent reads and edits `app.tsx` directly. The boundary now
accepts bounded JSON diagnostics and rejects source with unmatched delimiters or HTML tags. The
process grader explicitly rejects the observed parent `edit` call. Stage A used all
30 original cases and did not pass its gate. The member approved 30 additional cases. The guarded
repair, corrected standalone control, interrupted kit attempt, and supervised repeat use 8, leaving
22. The interrupted worker attempt and completed worker screen use four more, leaving 18.

Both initial builder handoffs also lose their first full-source write. Gemini sends the parent
message's `requested_by` value on `write_application_source`, but that message is not active in the
child turn. The failed calls contain 49 KB and 35 KB of source. Their child spans are 237 seconds
and 145 seconds, including first model rounds of 129 seconds and 106 seconds. A profile-only tool
ignores this irrelevant field because it consumes no member-private capability. Normal tools still
validate an active member message. This removes the repeated full-source generation without
changing model-visible text.

A deterministic replay of the retained edit payloads confirms the structural gate. Both initial
sources have balanced delimiters and tags. The first briefs edit changes the brace balance to -1
and leaves an open `div`. The three meeting-task edits change brace balance to -2, -4, and -2 and
leave open `aside` tags in the first two. The new handler rejects each edit before it writes.

The guarded-repair run removes both exclusions. Both apps load through the production runtime,
deploy, bind the homepage, change every tested control, clear AA, avoid clipping and overflow, and
meet all 11 visual criteria. `pre-meeting-briefs` finishes in 732 seconds with one builder handoff.
`meeting-tasks` finishes in 621 seconds with three bounded handoffs. The production target improves
product score by 0.032 and 0.024 over the old standalone samples, but it does not pass the Stage A
gate: the briefs source score falls from 1.00 to 0.80, and time and cost remain far above the
standalone path.

The briefs source loss exposes a comparison fault. The standalone app keeps inactive tab panels in
the DOM, so the source grader counts hidden facts. The React app mounts only the selected brief, so
the same grader misses facts that its own interaction audit reaches. The audit now records the
visible text parts in its initial state and after every successful control change. Source and copy
graders use those browser states when present and no longer accept hidden static content. This
changes a fixed grader input, so the next standalone and kit runs form a new comparison identity.

The corrected standalone reference completes both screen cases in 143 to 165 seconds for $0.065 to
$0.121. Browser-state grading restores briefs source coverage to 1.00 without rewarding hidden DOM.
The matched supervised repeat excludes both kit cases at 900 seconds. Briefs sends two builder
handoffs and costs $2.409. Tasks sends four handoffs and costs $2.510. Neither reaches deployment.
The saved traces show the parent carrying 35 to 48 KB source, browser output, and repair diagnostics
through repeated Opus rounds. This is 20 to 39 times the corrected standalone cost and does not
improve product evidence.

The first end-to-end worker screen does not enforce ownership. Both workers create full sources and
run browser QA. Both reach their round bound before worker-owned deployment. The parent then reads
connectors, repairs source, runs browser checks, and deploys. `meeting-tasks` produces a strong page,
but the independent process grader rejects the parent work, one browser batch, contrast failures,
and no visible state changes. `pre-meeting-briefs` reaches the 900-second harness bound. The two
workers make 20 bounded source reads to obtain range IDs for source they wrote in the same turn.

These are stable process failures. The second worker screen gives the parent only `load_skill` and
generic `spawn`. The worker edit tool accepts exact `old_text` and `new_text`, compiles the full
candidate, and permits a bounded read only after an exact mismatch. The screen measures this
deterministic treatment without Opus escalation.

The second worker screen removes both exclusions and deploys both apps. The briefs worker finishes
in 310 seconds for $0.155; the tasks worker finishes in 449 seconds for $0.225. The parent adds
$0.303 and $0.266. Briefs also uses generic spawn for a six-second Opus connector verifier that
costs $0.204. The workers run 9 and 12 browser batches, so process remains 0.333. Product rises from
0.565 to 0.873, with full delivery, interaction, and visual scores. Remaining product failures are
AA contrast, first-screen facts, and density.

The next treatment gives the parent `build_ufo_application` instead of generic `spawn`. That tool
always dispatches the typed worker with the fixed scaffold paths. A Sites pre-tool gate refuses a
fifth `js_repl` call from this worker. It keeps the four-call Playwright protocol and the full final
harness audit.

The third worker screen removes the second delegation and cuts wall time to 213 and 357 seconds.
The briefs worker costs $0.100 and the tasks worker costs $0.114. Both attempt a fifth browser call,
but the gate runs only four. Briefs loads and interacts; its unhandled clipboard rejection, hidden
facts, and dark contrast fail product checks. Tasks compiles but reads hooks from a missing browser
`React` global, so it never mounts. Its four QA calls include three failures, but the worker deploys
anyway. The next rule forces `reset: true` on every builder REPL call, rejects React unless it is
read from `UfoAppKit`, and refuses deployment until two QA calls succeed. The QA grader counts only
calls that reached the REPL, not the denied fifth attempt.

The focused tasks repeat passes every process layer in 255 seconds for $0.183. It mounts, proves ten
visible state changes, and earns full source and interaction scores. Missing first-screen facts and
AA contrast keep the product result at 0.848.

The five-case parallel screen completes without an exclusion in 380 seconds. All five workers pass
delegation and QA. Three parents load `website-building`; two delegate without it. Product score is
0.891. Every case deploys and proves interactions. The common product failures are first-screen fact
placement, copied fixture prose, and AA contrast. These are the planned inputs to Stages B and C,
not a reason to send the artifact through Opus.

The matched Skill-preload tasks case raises product score from 0.848 to 0.946. Source, interaction,
and visual scores are full; one date and two low-contrast buttons remain. The parent still loads the
same Skill before delegation and rewrites the request, so the run costs $0.245 and takes 333 seconds.
The next treatment gives the parent only `build_ufo_application`; that tool passes the member's
original request and preloads the Skill in the worker.

The direct-delegation repeat cuts wall time from 333 to 228 seconds. The parent uses two model rounds
and one tool. The worker costs $0.066 and completes in 186 seconds. Total cost rises to $0.266 because
the Opus parent call varies, not because verification returned. Product score is 0.913, with full
process, delivery, interaction, and visual scores. Stage B now tests whether a typed in-worker
contract fixes copied prose and missing first-screen facts.

The first Stage B ablation rejects a nested model-authored contract. A four-case run exceeds the
host command window after all app turns finish; its report is excluded. The smaller matched screen
completes. The typed wording raises app score by 0.015 but moves no case, costs 9% more, lowers
density by 0.107, and lowers process score from 1.000 to 0.833. Both arms make invalid contract calls:
Gemini sends strings or nearby field names instead of the nested schema. The tool and wording are
removed.

The compact deterministic projection is also rejected. It adds required facts, alternatives, copy
tasks, and acceptance text to one connector response without a prompt or model change. The worker
reads it, but the two-case screen moves no binary or product layer. `issue-owner` also loses final
QA and homepage binding in the treatment. The projection code is removed. Stage C now tests owned
starting surfaces. A later Stage B arm must change how the worker consumes facts, not only add more
fixture structure.

The parent tool input was still a stable process fault. Its unused 200-character activity field
caused three failed calls before one successful delegation in a retained control trajectory. The
tool now has an empty input schema and still passes the original member request from the turn. The
process scorer counts failed and successful delegation attempts. A matched Skill screen gives both
the exact and stale call text one successful parent call, so the schema is the enforcement boundary
and the Skill call syntax is removed. A later no-line screen has no records because both local serve
processes receive `SIGTERM`; it is an infrastructure gap and consumes no campaign case.

The first component screen is not a valid substrate comparison. Adding the starter file to the
base changes the control's loaded Skill file set. A repeat confirms the leak: both control workers
read the starter, and the treatment's initial product gain reverses. The asset and its test are
removed. A corrected arm starts from the pre-starter base and replaces an existing conditional
reference only in the treatment worktree. It writes no record because both independent serve
processes receive `SIGTERM` during active Gemini turns. The same paired failure now appears in three
ablation attempts. A direct serve recovery also exits while it recovers the retained turns. Stop
paid component and model arms until the stack owns and reports this process lifecycle.

The process trace identifies the paired termination. Each measured serve exits `-15` without a
preceding stack terminate event. At the same instant, a concurrent `default-apps` workflow runs
`pkill -f "ufoctl serve"` and replaces its own service. The stack now records starts, signals, and
exits in `process.log`, which the ablation archive retains. It invokes the same `ufoctl` entry point
through an absolute stack-private `eval-serve` name, so an unscoped development restart does not
match it. The focused stack suite passes 26 tests with one skip, and the live two-stack proof
passes. Two later matched screens complete without a missing record.

Neither isolated starter passes Stage C. Replacing the taste reference with the starter produces a
0.023 combined product gain and a 7.5% median time reduction, below both gates, with one case
regression. Keeping the Skill and adding only the inline shell lowers product by 0.043 and misses
one deployment. The blank kit page is the selected component input for Stage D. No starter file or
prompt change ships.

The first Stage F attempt starts seven unrelated automatic homepage turns for agents that existed
before the case. It writes no run record. Creation preparation now marks only those existing
homepages as settled and leaves the main agent's tools unchanged. The clean repeat contains only the
member's creation turns and passes both new cases.

The guided path passes with four Opus turns and costs $1.280. Its 106-second design turn reads the
design system, writes page source, runs browser QA, and shares the preview. The revision path passes
with five Opus turns and costs $1.990. Its first design takes 100 seconds and the requested repair
takes another 77 seconds. This is approval work mixed with builder work. Keep the Opus parent for
proposal, interview, and the final chat response, but move preview source, browser QA, and repair to
the fast worker before the four end-to-end journeys. Two of the 20 Stage F cases are used; eighteen
remain.

Two matched preview-worker arms confirm that a second model build is the wrong boundary. The first
Gemini preview worker reduces cost but passes only one of four cases and takes 114 to 217 seconds
per child. A bounded worker improves deterministic ownership and passes three of four cases for
$4.03 instead of $9.12, but its first previews still take 139 to 149 seconds, against 137 to 153
seconds for the direct path. Its remaining failure also drops the member's accepted overdue-queue
revision from the durable application prompt. Both worker variants are removed. That screen tested
a typed design contract and a fixed product renderer before one final Gemini build.

Returning the fixed PNG by path still causes Opus to read it before sharing. The renderer now uses
the public in-process artifact seam and delivers the PNG in its own call. The final matched screen
passes both guided journeys. Cost falls from $5.10 to $1.86, and case wall time falls from 342 to
464 seconds to 167 to 183 seconds. Render-and-share itself takes 20 to 380 milliseconds. No model
child, source tool, browser tool, second share, hosted site, or member-state change enters the
preview phase. The accepted typed contract reaches the created application's prompt. This is the
result of that experiment.

The product path uses the application builder for both phases. Its first phase writes one typed SVG,
and the product shares those exact bytes. Approval stores the SVG digest. The same builder receives
that SVG and digest for the build phase, cannot write another design, and must implement the accepted
artifact before QA and deployment. The trajectory gate rejects source, connector, QA, or deployment
work before the create. The artifact gate rejects a final `homepage-design.svg` with another digest.

The accepted-wireframe screen passes control 1/1. Removing the worker's accepted-design steps
regresses to 0/1; removing the Skill's first action call passes 1/1 but later produces a zero-preview
counterexample, so the explicit call stays. The passing control's accepted, deployed, and retained
SVG share one digest. The focused prompt-contract screen passes 1/1 in both arms after prompt
equality is replaced by the app-name and SVG-digest contract. Two full-journey wording screens are
inconclusive because both matched arms miss preview cardinality before the build.

The complete journey screen removes routine Opus build work. A normal homepage parent calls one
fixed build tool and gives one final response. Gemini owns connector inspection, one full source
candidate, bounded repair, browser QA, and deployment. Product code accepts only a compiler-approved
source digest that matches the deployed `app.tsx`, then binds the homepage. A repeated parent tool
call returns the first cached result and cannot start another Gemini worker. A worker failure returns
`blocked`; it does not become an Opus repair turn.

The source candidate cuts the A08 child from 4.05 million tokens and 27 rounds to 225 thousand
tokens and 12 rounds in the accepted build. The isolated A07, A08, and A09 confirmations pass. A10
then passes the product sequence `blocked`, `deployed` with exactly two parents and two Gemini
children. The creation stack disables the ambient homepage seed job, grants the application only
`build_ufo_application`, and lets the explicit journey own every attempt. No Opus diagnostic turn
runs. All four complete creation journeys now pass.

The final presentation run does not pass the campaign quality gate. Its archived process grader
expects the worker to bind its own homepage, which contradicts product acceptance. Regrading that
unchanged evidence against the selected topology passes 13 of 14 process paths. The repaired
pre-meeting case then passes the full process, contrast, interaction, and visual checks, but still
omits required issue and source-age facts above the fold. These are product failures, not grounds
to lower the grader.

## Next phase: deterministic worker feedback

Keep one Gemini delegation. Product acceptance runs the compiler, source-digest check, browser
interaction audit, fact placement, contrast, and overflow checks before it binds. A failed check
returns one typed diagnostic batch to the active Gemini worker. Gemini repairs `app.tsx`, reruns
browser QA, and returns one final structured result. The parent sees only that result.

The existing `deploy_website` tool serves the staged page on an isolated preview port and runs the
same audit script as the eval harness. Connected-case facts live in the Sites product store, not in
the member prompt or workspace. A failed first deploy returns at most eight typed issues. A second
failed deploy ends the worker path. The builder prompt, Skill, and tool description do not change.

Use `pre-meeting-briefs`, `meeting-tasks`, and `issue-owner` as the screen. They cover missing
first-screen facts, density, contrast, and a near-pass control. Keep all current thresholds. A
stable diagnostic that Gemini cannot repair becomes a deterministic source or component rule. An
ambiguous diagnostic can enter a named Opus experiment, but it does not enter normal throughput.

The matched reasoning screen keeps `medium`:

| Builder reasoning | Run | Strict passes | Product | Process | Median wall | Cost |
|---|---|---:|---:|---:|---:|---:|
| `medium` | `7607a825` | 2/3 | 0.905 | 0.778 | 340s | $0.52 |
| `high` | `e3094bd9` | 0/3 | 0.757 | 0.333 | 397s | $0.63 |
| `auto` | `4608fdd7` | 0/3 | 0.812 | 0.333 | 447s | $0.63 |

Every case received a product audit repair batch. `medium` repaired and deployed
`pre-meeting-briefs` and `issue-owner`. `meeting-tasks` reached the 35-round worker limit after its
first failed audit. The two treatment arms cost 21% more. Both lose the two `medium` passes, and
neither repairs `meeting-tasks`. Keep `medium`. Test a bounded repair-round reserve before the
ten-plus-four confirmation.

The application kit now owns the narrow layout. At widths below 720 pixels, it limits the app
shell, wraps horizontal rows, and changes app grids to one column. The unchanged audit replayed two
retained sources against the rebuilt kit. The older `pre-meeting-briefs` source fell from 699 to 390
document pixels and changed 9 of 16 tested controls. The latest A09 source fell from 485 to 390
document pixels and changed 5 of 17 tested controls. Both light and dark views had no console
errors. No model case, audit threshold, or generated source changed.

The retained A09 revision exposes a grader-only label mismatch. Its accepted contract states
`The queue of overdue items waiting on you` and puts `Waiting on you` first. The prior check
accepted only a first region that repeated `overdue`. The exact reader label now counts as the same
queue while the priority must still state `overdue`. Focused negative cases prove that a missing
overdue priority and `Waiting on your weekly summary` still fail. No model input, contract, or
member request changes.

After the three-case screen passes, repeat the unchanged ten connected apps and four controls.
Only then compare worker models, reasoning settings, and component inputs.

## First execution order

| Order | Work | Status | Gate |
|---:|---|---|---|
| 1 | Give the Gemini worker connector, source, browser, deployment, and homepage tools. | Done | The profile excludes subagent, member, sharing, and grant tools. |
| 2 | Limit the parent to one fixed worker delegation and one final response. | Done | The app-eval setup grants only `build_ufo_application`; the tool passes the member request verbatim and preloads `ufo-style` in the worker. |
| 3 | Compile each source write and exact edit. Keep final acceptance independent. | Done | Tool tests and the artifact audit compile through `UfoAppKit`. |
| 4 | Prove the fresh-REPL, React-binding, and pre-deployment QA gates. | Done | One focused case and five parallel cases complete with no exclusion; every worker passes delegation and QA. |
| 5 | Make Skill routing and request transfer deterministic. | Done | The worker starts with `ufo-style` and the verbatim member request. The parent uses one tool and does no other work. |
| 6 | Run the typed data/copy contract arms. | Done | Both nested and deterministic fixture contracts are rejected and removed. Neither improves a product layer. |
| 7 | Remove stable parent delegation input failures. | Done | Every passing case makes one parent tool call before the worker starts. |
| 8 | Repair paired ablation serve termination and retain active trajectories. | Done | Lifecycle records name stack signals. The private serve name completes two matched screens without a missing record. Harness deadlines retain parent and completed child trajectories. |
| 9 | Run the model and reasoning screens with the selected blank kit input. | Done | Gemini 3.7 Flash at medium keeps the best complete portfolio. GPT and GLM fail the no-regression gate. |
| 10 | Add connected prepared-action cases. | Done | Both matched arms pass all three browser, prepared-turn, durable-state, refusal, idempotency, and scope checks. |
| 11 | Add connector setup and notification status. | Done | All three pages show the fixed state, open the exact new-chat path, and make no direct setup write. |
| 12 | Add guided creation and preview-revision screens. | Done | Both cases create only after the accepted design. The accepted revision reaches the build by its SVG digest. |
| 13 | Remove Opus-owned preview source and browser work. | Done | Both Gemini preview-worker arms are rejected and removed. They lower cost but do not lower preview latency. |
| 14 | Add the deterministic application-design renderer. | Done | The atomic renderer passes 2/2, cuts matched cost 64%, cuts wall time 51%–61%, and performs no model or browser work. |
| 15 | Run the four complete creation journeys. | Done | The isolated A07–A10 reports pass 4/4. A10 retains one blocked attempt, then deploys through one later Gemini attempt with no extra parent or worker. |
| 16 | Confirm the revised topology on ten connected apps and four controls. | Done | Corrected archival process grading passes 13/14, and creation passes 4/4. Strict presentation quality still fails. |
| 17 | Return deterministic audit diagnostics to the same Gemini delegation. | Done | The product audit repairs and deploys two of three `medium` cases. No Opus turn or lower threshold enters the path. |
| 18 | Reserve bounded worker rounds for a failed product audit. | Done | The product audit gets one second deployment attempt without a prompt or grader change. |
| 19 | Repeat the unchanged presentation confirmation. | Done | The strict presentation gate fails. Keep the accepted topology and every product failure. |
| 20 | Recheck reasoning through the Vite application builder. | Done | `medium` and `auto` each pass 1/2 after delivery repair. `auto` moves no case and costs more. Keep `medium`. |
| 21 | Land the accepted Vite builder and benchmark path. | Done | The benchmark boundary and application builder are merged at reviewed heads. |
| 22 | Reduce deterministic latency and recheck the complete presentation set. | Pending | Replace fixed waits with the private lifecycle signal, then repeat the unchanged presentation gate. |
