# evals

## Run the suites

Start `ufoctl serve` with a test workspace. You can delete the workspace after the run. Then:

```bash
uv run python -m evals --workspace <workspace-id> --label baseline
uv run python -m evals --remote --workspace <workspace-id> --label remote
uv run python -m evals --remote --only coding_profile --agent profile:coding \
  --workspace <workspace-id> --label coding-profile
uv run python -m evals --view
uv run python -m evals --share <current-run> <baseline-run>
```

`--remote` admits each case through `ufo --remote --json` at the configured serve URL. The current
`ufo` must be on `PATH`, and `UFO_TOKEN_SECRET` must match the running serve process. Remote cases
use the private member audience of the terminal surface; shared-audience cases fail before admission.
The server attests its config, sandbox templates, model, and reasoning; a packaged deploy also
attests its immutable image and revision. The runner refuses a missing or mixed identity before it
records any score and binds the identity into every runtime-pinned suite digest.
`profile:<name>` is an explicit profile target for suites that pin it. The member turn is a proxy
that must foreground-spawn that qualified production profile with the case objective unchanged;
the grader reads the child's validated result and ignores the proxy's answer. A durable agent with
the same bare name cannot capture the target.

Corpus runs use the same concurrency control:

```bash
uv run python -m evals --swebench --swebench-subset all --remote \
  --fresh-workspace --concurrency 26
uv run python -m evals --terminal-bench --remote \
  --workspace <workspace-id> --concurrency 24
```

Capture patches already shared by a completed remote SWE-bench workspace without rerunning turns:

```bash
uv run python -m evals --swebench-capture --workspace <workspace-id> \
  --swebench-subset all --swebench-submissions <directory>
```

The command copies exact durable artifact bytes and lists selected cases with no shared patch.

`--fresh-workspace` provisions one clean hosted workspace through the authenticated onboarding
boundary and records its id with the run. It requires `UFO_ONBOARD_CONTROL_TOKEN` and
`connect.public_base_url`; use `--workspace` instead to target an existing workspace. SWE-bench
admits one remote ufo session per case. Terminal-Bench delegates the same bound to one remote
Harbor job so Harbor grades the task environments it owns. The corpus READMEs hold setup and
official grading details.

## Read the results

Each `python -m evals` command records the run as JSON under `eval-reports/runs/`. The record does
not change after the run. Each command also rebuilds the offline viewer,
`eval-reports/index.html`.

Every case shows its full setup: the message or scenario, the rubric, and the member binding.
Every attempt shows:

- the tool trajectory and the tool errors
- the judge verdict for each criterion
- the grader evidence
- the turn log
- a transcript snapshot, with the base prompt of the agent

The viewer does not show inline images or private credential handoffs. The viewer shows unknown
evidence keys as raw JSON, so the evidence of a new suite is always visible. Comparisons show
deltas only when two suites have identical digests.

Every case is recorded whole. `record_run` refuses a case with a null prompt or a null response. A
run with no evidence in its archive shows "not recorded — rerun the case". It never shows as a
valid empty result.

`--reconstruct <run> --workspace <id>` builds a diagnostic copy of a run. It reads the durable
conversation, turn, and blob records. It writes the copy under `eval-reports/reconstructions/`. It
does not change the original.

Shared viewers are private and hold the whole record. A shared viewer holds only the named runs.
Its S3 object key has 192 random bits. The presigned URL is valid for seven days by default. The
share step selects the bucket in this order: `--s3-bucket`, then `UFO_EVAL_SHARE_BUCKET`, then the
S3 `[blob]` bucket from the config.

## What a delegated child generated: evals.delegated_generation

A spawn returns one payload; every round a child paid for before the round that carried it reached
nobody. `delegated_response_register` records each child turn's completed model-round output, and

```bash
uv run python -m evals.delegated_generation --metric-stdout
```

reads it back over the archive, so a run needs no re-run and a case that failed still reports what
its child generated. Every archive under `--runs` is read, which is one arm of an ablation:

```bash
uv run python -m evals.delegated_generation --metric-stdout \
  --runs eval-reports/experiments/<name>/runs/control
```

`delegated_intermediate_tokens: <mean>` is the output tokens per child turn that reached no parent —
the number a change to the subagent contract has to move. `delegated_payload_chars` is the `finish`
payload, the one field the register bounds; `delegated_document_chars` is what the child left in
files, which is the deliverable rather than the price of handing it over. Each is per child turn,
so an arm that delegated less often does not read as a reduction. An attempt whose child rounds
carry no usage is excluded and named; a run with none refuses a metric and exits non-zero.

Every figure is a size. Nothing compares two texts for similarity: a child that re-derives its
findings in the payload instead of copying them shares almost no word shingles with what it already
wrote, so an overlap check reads it as compliant while it pays for every word. Prose written at
length before the handover is the failure, whatever it says.

`delegated_forced_finish: <share>` is the one figure that is not a size — the share of child turns
that ended on prose and made the engine buy a round to get the payload. It is read from the step
sequence: a child that calls `finish` itself has a tool dispatch between its last two model rounds,
and one that narrated instead leaves two model rounds back to back.

No case elicits it: 47 control child turns over eight repeats produced none, and `browser_nav`'s
children none either. That is the finish contract working — the same signature over the fleet's
step log fell in every lane when `<parent_handoff>` landed, browser 26.3% to 0% and research 6.8%
to 2.2% across 1,100 coding children. Read it over the fleet rather than here, and treat a lane
that climbs back as the reason to reopen the wording.

## Run many suites: evals.stack

`python -m evals.stack matrix.toml` runs several suites at the same time. Each suite gets its own
isolated stack:

- a run directory under `.local/evals/<stamp>/<label>/`
- a derived `ufo.toml`, with its own database, its own blob root, and probed serve and proxy ports
- a workspace with fresh seed data
- its own `ufoctl serve`

Every run goes into the shared archive. Each `[[run]]` block names a label, a template config with
the suite knobs, and the arguments for `python -m evals`. The run directories and the per-run
databases stay on disk, so you can reconstruct a run later.

## Measure a prompt change: evals.ablate

The Prompts rule in `AGENTS.md` requires an ablation for each change to text that a model reads: a
skill, a prompt section, or a corpus file. `python -m evals.ablate experiment.toml` runs the
ablation.

Each variant gets one arm. Each arm gets its own git worktree, its own venv, and its own stack. A
control arm always runs on the unchanged base.

The experiment file names:

- the base revision
- the suites and the cases
- the repeat count
- a budget — preflight refuses an estimate above it; remote runs divide it exactly across every
  arm and repeat, then fund each disposable workspace with that allocation
- the template config for the stacks
- `[[arm]]` blocks, which map repo paths to variant files

```toml
name = "customers-section-clauses"
base = "origin/main"
suites = ["onboarding_help"]
cases = ["slack-install-pending", "promised-credits"]
repeats = 2
remote = true
budget_usd = 60.0

[template]
database = { url = "postgresql+asyncpg://ufo:ufo@127.0.0.1:5541/ufo" }
blob = { backend = "filesystem", root = "./blobs" }
connect = { public_base_url = "http://evals.invalid" }
pack = { name = "assistant_hosted" }

[[arm]]
name = "no-topic-list"
[arm.files]
"packs/assistant_hosted/ufo_pack_assistant_hosted.py" = "arms/no-topic-list.py"
```

Set `remote = true` to admit every arm through `ufo --remote --json`. Each arm still runs against
its own isolated stack. A running turn may cross its workspace allocation by one model round, and
concurrent turns may each do so; the balance gate stops every later round and admission.

Verdicts compare the pass counts for each case, at the sample level. A verdict calls a change only
in these conditions: the gap is two samples or more at equal sample counts, or a case fully fails,
or a case is fully fixed. A failed case is data. A nonzero exit from a stack is fatal only when
the stack wrote no record.

The reports, the records of each arm, and the stack logs of each arm go under
`eval-reports/experiments/<name>/`. If an arm owed a record and wrote none, the report names the
gap. The arm keeps its worktree, so you can read the stack that spent the money.

Judge and simulator variance is real. Compare arms from the same experiment only. Do not compare
across runs.

## Search for prompt text: evals.gepa

`python -m evals.gepa optimize.toml` searches for better prompt text. It writes variants; an
ablation then measures them. GEPA proposes; the ablation decides. It uses GEPA (Genetic-Pareto
reflective prompt evolution, [Agrawal et al.](https://arxiv.org/abs/2507.19457)).

The config names the `modules` to optimize: a skill description, a skill body, a corpus reference,
or a pack prompt section. The scored cases divide into a minibatch source and a held-out set,
`D_pareto`.

Each iteration does these steps:

1. It selects a parent from the Pareto frontier. The frontier holds the candidates that lead on
   one case or more. Dominated candidates are removed. A candidate that leads on more cases gets
   more weight.
2. It selects one module, round-robin.
3. It samples a minibatch.
4. It asks the reflector model to rewrite the module.

The reflector reads the structured evidence of each case: the member message, the answer, each
tool call with its result, the rationale of each judge criterion, and the rig signatures of the
harness. It does not read the top-level reason of the case. `combine` builds that reason from the
words of all graders, joined into one line.

One candidate changes exactly one module, so one rollout measures one lever. Two candidates that
changed different modules can merge. The merge joins the two diffs into one.

Every verdict is a paired run. The proposal and the parent run at the same time, on the same
minibatch, plus the `controls` that the change can break. The parent is reduced to the same
module. A proposal is accepted only when it gains on the targets and loses no control.

A comparison reads only the cases that both arms scored. A case that one stack excluded for an
infrastructure fault has no score in that arm. A pair with no case in common accepts nothing. Do
not compare scores from two different runs: controls with identical text have moved
4/6 → 0/6 → 3/6 in one family.

Some cases cannot be scored:

- Put a case that the environment cannot pass in `unscorable`. It leaves the task set before the
  optimization starts.
- A case that the seed rollout could not score leaves the same way.

A module must be short enough for the reflector to read whole. The config load refuses a module
that is too long. The reply replaces the file, and a reply to a cut module comes back without the
tail.

`--dry-run` prints the plan and the cost estimate. It spends nothing.

Before a result ships, each final arm runs against the base, on the whole suites, in one paired
run. The final arms are the top `arms` candidates and the merge of the winners on different
modules. This full run is necessary because a minibatch cannot show a regression in a neighbour
case. Each arm must also pass the claims gate. A fit to a rubric is not truth: text that wins a
judge with a false statement about the product is refused.

For a clause-level question — does one sentence carry weight? — use `evals.ablate`, not GEPA.

GEPA writes no repo file. A shipped candidate lands as
`eval-reports/experiments/<name>/arms/<module>.<candidate>.<ext>`. Next to it lands a ready-to-run
`experiment.toml` that names the candidates as `[[arm]]` blocks. `state.jsonl` holds each
candidate with its paired scores, its ancestry, its gate verdicts, and its spend. A stopped run
resumes from that file. It does not restart.

A stack exits nonzero as soon as one case fails. That failure is data. The records of the stack
are archived and scored first, whatever the exit code. Only a stack that wrote no record is an
error.

```toml
name = "digest-routing"
base = "origin/main"
suites = ["report_digest", "skill_routing"]
cases = ["report-digest-attributed-findings", "report-digest-outside-companies"]
controls = ["board-visual-narrative", "forecast-assumption-model"]
unscorable = ["connector-composio-install"]
modules = [
  "extensions/report_digest/ufo_ext_report_digest/skills/report-digest/SKILL.md",
  "packs/assistant_hosted/ufo_pack_assistant_hosted.py",
]
iterations = 6
minibatch_size = 2
budget_usd = 180.0

[template]
database = { url = "postgresql+asyncpg://ufo:ufo@127.0.0.1:5541/ufo" }
blob = { backend = "filesystem", root = "./blobs" }
connect = { public_base_url = "http://evals.invalid" }
pack = { name = "assistant_eval" }
```
