# coding_repo

Ten coding tasks this repository already answered, each pinned to the commit its work started from.
The corpus is `cases.py`; nothing is downloaded and nothing is vendored, because the reference answer
is a commit in this clone.

## What it measures

The lane, not just the answer: every case reaches the agent as a member's message, and the agent is
expected to route it to the `coding` subagent, which fetches the pinned commit and works there.

| Kind | Cases | Deliverable | Scored |
| --- | --- | --- | --- |
| research | 3 | the reply | in the run, against the case's criteria |
| research | 1 | a written survey | offline, against the case's criteria |
| fix | 3 | a unified diff and a note | offline, against the criteria, with the merged diff as reference |
| feature | 3 | a unified diff and a note | offline, against the criteria, with the merged diff as reference |

A patch case hands over a note beside its diff, because a diff cannot say what was verified, what
remains uncertain, or what the change reassigns — the reasoning the real pull requests recorded in
their descriptions, and what a third of these criteria ask for. The note is scored, not gated: a
missing note costs the criteria that needed it rather than suppressing judgment of the diff.

A case that has one names the criterion without which the answer is wrong — the root cause for a fix,
the core requirement for a feature. Grading reports it beside the score, so a respectable fraction
cannot hide a missed root cause.

A brief carries only what the requester knew — a symptom, an observation, an intent, and in one case
a lead that is wrong. The mechanism and the shape of the answer live in the criteria, which are drawn
from the reasoning the merged commit itself recorded.

## Why the pin holds

Each case names the parent of the squash its work landed as, so the defect is present and the answer
is absent. The envelope tells the child to fetch that one commit:

```
git fetch --depth 1 origin <base_sha>
```

which resolves to a single commit with no descendants, so the commit that fixed the case is not
reachable from the sandbox. The gate refuses an archive or contents-API read, which hands back a tree
with no `.git` at all, and a clone of this repository, which carries every commit after the pin. A
clone from a local path is neither — that is how the coding skill gives a second child its own
checkout of an already-pinned tree.

## Gates before judgment

A captured deliverable earns a judge only after the run proves it is real:

- delegated to the `coding` lane, and that delegation succeeded
- a call that succeeded fetches the pinned commit, and no call cloned this repository or reached it
  by a historyless route
- for a patch: parses as a unified diff, applies to the pinned commit's tree, and touches at least
  one path the merged change touched
- for a patch: passes its declared held-out pytest targets, overlaid after the candidate patch so
  candidate-written tests cannot replace them. The held-out tree is the reference test tree plus
  case-owned tests for requirements its reference tests do not detect
- for a document: shared under the name the brief asked for, and not empty
- the evaluated turn delegated rather than working the repository itself: its own `bash`, `edit`,
  `grep`, or `glob` calls may read child handoffs, but may not reach a checkout

`test_coding_repo_runner.py` proves the reference test is held out: the base behavior fails it, the
reference change passes it, and a candidate test cannot replace it.

## Run

Needs a running `ufoctl serve` under the `assistant` or `assistant_hosted` pack, and — because this
repository is private — `github_git_token` filled in the target workspace:

```bash
ufoctl credential set github_git_token   # a fine-grained PAT with Contents: read
uv run python -m evals --coding-repo --workspace <uuid> --concurrency 4
```

`--coding-repo-case <case>` (repeatable) narrows the run, and `--coding-repo-submissions` moves the
capture root. Base and reference trees materialize under `.local/coding_repo/trees/`. Captured
deliverables land under `.local/coding_repo/submissions/<case>/`. Every pin and held-out test is
verified when the suite loads, so missing history is a startup error rather than a failed case.
Each selected case's captured bytes are dropped when the run starts, so a startup abort destroys no
capture that no turn will replace, while a case that shares nothing this run still scores as having
shared nothing rather than on the last run's bytes. A patch the gate refused is kept under
`<case>/refused/`, readable but out of the offline judge's reach.

The suite splits into two tasks: `coding_repo_answers`, judged in the run against each case's
criteria, and `coding_repo_deliverables`, gated in the run and judged offline. Both pin the runtime
into their digest, so scores compare only across runs of the same pack, prompt, and model.

## Handoff

Whether a delegated child finished terse is a property of the run, not of the answer, so it is
reported over the archive rather than gated inside a case verdict. A run on a prompt that fails the
constraint still produces its quality scores — suppressing the judgment would spend the turn and
learn nothing.

```bash
uv run python -m evals.coding_repo.handoff --metric-stdout
```

The finish contract says never write a final prose message before `finish`. The finishing round is
not durable — the engine returns the moment `finish` validates — so what is read is the last
message the child left standing and the payload it returned, which is where a regenerated report
shows up. Overlap is counted over word shingles, so working narration reads near 0% and a rewritten
report near 100%.

Forbidding the message moves the second copy rather than removing it: a child can write the report
to a file, read it back, and summarize that into the payload, leaving the closing numbers clean. So
every document the child wrote is read too — its bytes, its re-reads, its write errors, and its
overlap with the payload — and a document the case never asked for, read back and restated, counts
as `rerouted-to-file`. Every one, because a patch case's own note is asked-for prose and usually the
largest thing the child wrote: reading one document would report every rerouted patch case as
compliant.

`coding_repo_handoff: <share>` is the fraction of children that were none of verbose, duplicating,
or rerouted to a file. The reported handoff cost is the closing message, the payload, and each
rerouted copy — never the deliverable, which is the work rather than the price of handing it over.

A run that recorded no handoff at all reports no metric and exits non-zero: absent data is not
compliance.

## Grade

Offline and re-runnable, so criteria and prompt can be iterated against work already captured:

```bash
uv run python -m evals.coding_repo.grading --metric-stdout --judge judge.json
```

`--judge` names a JSON spec — `{"name": …, "argv": […], "timeout_seconds": …}` — whose command
reads `{"system","prompt"}` on stdin and writes the verdict on stdout. Each case scores the fraction
of its criteria the deliverable satisfies, and flags the case whose essential criterion failed.
Per-case verdicts and the report land in `.local/coding_repo/grades/`; `--metric-stdout` ends stdout
with `coding_repo_quality: <mean>`, the line an optimizer loop reads. A case that captured nothing
scores zero through the same path rather than being dropped from the mean; a case the judge could
not answer for is not scored at all, so `report.json` names it under `unscored` and states no mean,
and the run exits non-zero and prints no metric.
