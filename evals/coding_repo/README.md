# coding_repo

Ten coding tasks this repository already answered, each pinned to the commit its work started from.
The corpus is `cases.py`; nothing is downloaded and nothing is vendored, because the reference answer
is a commit in this clone.

## What it measures

The lane, not just the answer: every case reaches the agent as a member's message, and the agent is
expected to route it to the `coding` subagent, which fetches the pinned commit and works there.

| Kind | Cases | Deliverable | Measured |
| --- | --- | --- | --- |
| research | 3 | the reply | judged in the run against the case's criteria |
| research | 1 | a written survey | gated in the run: shared under the name the brief named, not empty |
| fix | 3 | a unified diff | gated in the run: applies to the pinned tree, touches a path the merged diff touched |
| feature | 3 | a unified diff | gated in the run: applies to the pinned tree, touches a path the merged diff touched |

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

## Gates

- delegated to the `coding` lane, and that delegation succeeded
- a call that succeeded fetches the pinned commit, and no call cloned this repository or reached it
  by a historyless route
- for a patch: parses as a unified diff, applies to the pinned commit's tree, and touches at least
  one path the merged change touched
- for a document: shared under the name the brief asked for, and not empty
- the evaluated turn delegated rather than working the repository itself: it called none of `bash`,
  `edit`, `grep`, `glob`. The child shares the spawning turn's sandbox, so this is not about which
  files reach whom — it grades the coding skill's rule that the main agent explores and edits
  nothing itself

`test_coding_repo_runner.py` holds the gate to the only standard that matters: every case's own
merged diff passes the gate at that case's base commit.

## Run

Needs a running `ufoctl serve` under the `assistant` or `assistant_hosted` pack, and — because this
repository is private — `github_git_token` filled in the target workspace:

```bash
ufoctl credential set github_git_token   # a fine-grained PAT with Contents: read
uv run python -m evals --coding-repo --workspace <uuid> --concurrency 4
```

`--coding-repo-case <case>` (repeatable) narrows the run. Base trees materialize under
`.local/coding_repo/trees/`, one per pinned commit. Every pin is verified against this clone before a
turn starts, so a commit this clone lacks is a startup error rather than a failed case.

The suite splits into two tasks: `coding_repo_answers`, judged in the run against each case's
criteria, and `coding_repo_deliverables`, gated in the run against the file the case asked for. Both
pin the runtime into their digest, so scores compare only across runs of the same pack, prompt, and
model.
