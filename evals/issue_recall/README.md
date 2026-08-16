# issue_recall

Does default memory injection surface a repository's related issues and pull requests when the
turn's goal is filing a new one? The leaf grades the recall the memory extension's
`user_prompt_submit` hook injected for that exact turn — never the answer, never a search the agent
chose to run.

| Piece | What it is |
|---|---|
| Fixture | 23 in-repo GitHub records: 3 clusters of related issues/PRs/comments (9 pages) + 14 distractors, several near-topic (`atlas sync` performance, Okta SSO, webhook-signature docs) |
| Haystack | 473 declared ambient memories the graded facts compete against — 40 near-topic negatives, 13 live copies across five duplicate families, 60 issue-shaped records from five sibling repositories, and a generated bank of ordinary workspace facts |
| Pages | Rendered by the real `GitHubConnector` (`flatten` then `render`), landed by the core sync driver as a folder source |
| Recall path | `derive_facts` distills each page into durable facts on `models.background_jobs_model`, the model a deploy's page-change jobs run, stamping each with the `created_from_page_id` link back to its page; those facts are what auto-inject can reach, so readiness maps page → derived fact ids through that link |
| Grade | Coverage of the case's related pages in the injected memory ids, their ranks, and whether the front of the injected context is related rather than distractors |

One leaf (`issue_recall`), twenty-two cases: eighteen graded on coverage, three absent-topic
controls, and one machine control. The coverage cases are three filing topics — `sync-token-hang`,
`webhook-duplicate-orders`, `csv-export-truncation` — each asked four ways as a conversation's first
message, once more mid-conversation, and once as a source-change alert. The whole inbound is the
query the recall hook embeds, so a phrasing is a distinct retrieval query over one topic, and the
symptom text is held identical across the four so the only thing varying is the ask around it:

| Phrasing | Inbound |
|---|---|
| `named-repo` | "File a GitHub issue on northwind/atlas for me: … Give me the issue title and body you would submit." |
| `create-an-issue` | "Create an issue for this: …" |
| `file-issue` | "file issue: …" |
| `make-github-issue` | "Make a GitHub issue about this: …" |

Only `named-repo` names the repository; the other three are what a member actually sends. Each
message is an issue-filing ask and nothing more — no reminder that prior issues exist, no
instruction to search.

The `mid-thread` case is the same topic reached the way a member usually reaches it. Four prior turns
of ordinary technical Q&A (`asyncio.wait_for` cancellation, `BaseException` versus `Exception`, `|=`
on a dict) are seeded as one completed turn, and the graded turn is the terse ask that follows —
"ok, file an issue for the nightly job stalling on the auth refresh". The bar is the topic's own:
same related set, same `min_coverage`. Only the query shape differs, and it differs completely,
because the hook embeds the graded turn's inbound alone: the thread above it is not part of the
retrieval query, so a dozen words carry the whole search. These cases measure that — they are not
tuned to pass, and the ask is never padded with symptom words to help it.

The `source-alert` case is the topic reached the way scheduled admissions reach it. On a production
deploy sampled 2026-08-15, scheduled and internal turns outnumbered member asks roughly six to one,
and their inbounds are machine text the hook embeds verbatim: a source-change alert or a subagent
result, not a member's phrasing. The case's message is the alert shape `sources` emits — source and
connection ids, a stream name, a page uuid — around one related page's title, which is the query's
only topical signal. The bar is the topic's own, like `mid-thread`: not tuned to pass, measuring
whether recall still surfaces the cluster when the symptom words are absent and most of the query
is uniform boilerplate. Measured on this corpus, all three alert cases cover their full related
set, but the boilerplate costs precision before it costs coverage: the webhook alert's related
pages rank 1, 4, 5, letting distractors lead the front — the leaf's one failing case as of
2026-08-15, kept failing because that is the measurement.

Naming the repository does not help recall and can hurt it: `northwind/atlas` and `github` appear in
every page of the corpus (each body opens `# github issues:`), so those tokens are uniform noise in
the query while the symptom words carry all the signal. Measured on the corpus below, the three
terse asks rank the webhook cluster's comment at 3 where `named-repo` drops it to 5.

A case passes when the injection covers the majority of its related set and the front of the
injected context is at least half related — the first slots, as many as the related set has mapped
pages. Precision is graded at the front rather than over the whole injection because recall injects
a fixed number of memories: a related set of two or three is outnumbered overall no matter how well
retrieval ranks it, so counting the whole context would fail on the bound, not on the recall.

## Why the haystack is there

Recall injects a fixed number of memories (`RECALL_LIMIT`, 8). A corpus holding only the graded
evidence therefore hands retrieval a third of itself, and a coverage bar sits close to chance: with
23 facts in the workspace, a hypergeometric draw of 8 clears the two-page topic's bar 58.5% of the
time and covers a four-page set outright 0.8% of the time. Measured coverage of 100% against that
pool says the ranking is real, but it cannot separate excellent retrieval from adequate retrieval.

The haystack fixes the denominator. 473 ambient memories — durable shared rows no page produced —
put the pool at 496, so the injected share falls to 1.6% and covering a four-page set by luck falls
to about 1e-08. Forty of them are hand-written to be near-topic: session tokens that refresh
silently, webhook retries capped by the provider, a ten-thousand-row *UI page size*, a stalled-job
runbook. Those carry the filing topics' vocabulary about other systems entirely, so the leaf tests
discrimination rather than topic detection; the generated remainder is ordinary workspace noise whose
job is the pool size. Every ambient row is a distractor for every case, so it competes for the same
injection slots and shows up in `distractorInjected` and the front-precision check.

Thirteen more mirror what a production memory store actually accretes, measured on a live deploy
2026-08-15: one workspace held 27 live copies of a single "canonical ledger" memory (bodies to
16.7KB), ordinary facts written 3–10 times across sessions, and injections that spent three of
their eight slots restating one fact three ways. The corpus carries that shape as five duplicate
families — one triplet of restatements per filing topic (a plain statement, a dated
"re-confirmed" echo, a restatement with its practical consequence), a giant update-in-place
operations ledger beside its stale earlier copy, and a point-in-time snapshot beside its revision.
Copies rank independently, so a retrieval that cannot see they are one fact burns injection slots
the related set needed; `duplicateCopiesInjected` records per turn how many injected memories were
a second-or-later copy from one family. The families also calibrate the dedup sweep's supersede
threshold: measured on text-embedding-3-large, the ledger pair sits at 0.909 cosine and the
snapshot pair at 0.985 — above `SUPERSEDE_COSINE` (0.90) — while the reworded triplets sit at
0.63–0.83 and the nearest cross-family pair at 0.55. So the threshold retires near-verbatim copies
and never collapses distinct facts, and free rewordings are a measured non-goal of
embedding-threshold dedup: no value separates 0.83 from 0.55 safely, so they stay live and the
injection-side slot guard bounds what they cost. The ledger is also the junk-drawer distractor: its dated
entries sweep all three topics' vocabulary, so it partial-matches every graded query and floods
the context when it wins a slot.

`_commit_ambient` seeds every family with a raw content-addressed insert rather than
`MemoryStore.commit`: the haystack is history, written before the write path bounded a body, and its
operations ledger is longer than `MEMORY_BODY_MAX_CHARS` — a body `commit` refuses today.
`MemoryDeduper`'s sweep is the healing this models:
`test_the_dedup_sweep_collapses_the_seeded_families` proves one run collapses every family to its
single live member while every other ambient row stays live.

A denominator alone would not have been enough. `derive_facts` writes the graded facts in
issue-tracker voice — "Atlas issue #388 (closed) reported that `atlas sync` retried token refresh in
a tight loop" — so if they were the workspace's only records of that shape, a filing-shaped query
could pick them out by form and never discriminate on topic. Sixty issue-shaped memories from five
sibling repositories (`ledger`, `storefront`, `warehouse`, `notify`, `gateway`) supply that shape in
quantity, written in the same voice, which leaves the nine graded pages competing against 74
same-form records. Several are deliberately adjacent to a filing topic without being it: a nightly
reconciliation stalling on a late FX feed, a stock count hanging on an offline printer queue, a
weekly report whose CSV stops at 5,000 rows, partner callbacks retried without backoff.

The bank is all `fact`-class memory, matching the derived page facts, so this measures retrieval
rather than class mix. Recall's per-class diversity cap reserving slots for preferences and decisions
is a separate pressure and is not what these cases vary.

## The absent-topic control

Three more cases ask for an issue on a topic the corpus holds nothing about: a desktop app crashing
on cold start with no network, an org chart showing deactivated teammates as active, a print
stylesheet dropping the footer on landscape pages. `AbsentTopicGrader` scores them, and it grades the
opposite of coverage — there is nothing to find, so the reply must not claim there is.

They exist because the haystack is issue-shaped by design. With 83 issue-shaped memories in a pool of
496, an injection of eight carries one on roughly four queries in five whatever the topic, which is
an alternative explanation for the agent citing existing issues in the coverage cases: it might be
reaching for "check what is already filed" because its context looks like an issue tracker rather
than because the relevant issues were recalled. A reply that attributes one of these asks to a corpus
issue or pull request number — any of them, across the graded repository and the siblings, since every
record behind every one of those numbers is about something else — fails the case. Drafting the issue
fresh passes. Each case records `issueShapedInjected`, so the exposure the control rules out is
visible per turn rather than argued.

The machine control extends the same bar to a query with no topic at all: a cancelled subagent
result — profile, uuid, status, empty body — the inbound internal admissions put through the hook
verbatim. Sampled live, such turns received six to eight injected memories chosen by nothing but
form, since the query is pure boilerplate. The reply must not turn that exposure into a corpus
attribution; what recall injected is recorded, never prescribed.

Coverage is a fraction of the whole related set, never of the subset that derived a fact. A page the
derivation pass distilled nothing from counts against its case and is named in `unmappedEvidence` and
`mappedCount` so the cause is separable from a ranking miss — had the denominator re-based onto the
mapped subset, a `derive_facts` regression leaving one of a topic's four pages deriving would read as
a perfect pass while three quarters of the graded evidence left the grading set. The report
surfaces `meanMappedEvidenceCoverage`, `minMappedEvidenceCoverage`, `degradedRecallCount`, and
`unmappedEvidenceCount`, and each case's evidence adds `evidenceRanks`, `relatedInjected`,
`distractorInjected`, `duplicateCopiesInjected`, `frontSlots`, `relatedInFront`,
`memorySearchCalls`, and the issue numbers the reply cited. The `memory_search` calls are recorded, never required or forbidden: the graded event
is the hook's, exported before the model's first round, so a search the agent chose to run
afterwards cannot flatter the coverage.

The fixture lands as a folder source, so its page rows carry the folder backend's `files` stream
rather than `issues`/`pull_requests`; recall reads page and fact bodies only, and those are
connector-identical. Keeping the folder backend also keeps serve's own sync idempotent for the
duration of a run — it re-reads the same staged bytes and changes nothing.

## Run it

The stack orchestrator materializes the corpus before serve boots (`ufoctl init`, then
`python -m evals.issue_recall.materialize`), then drives the leaf against the running deploy. The
run needs `ANTHROPIC_API_KEY` (target turns), `OPENAI_API_KEY` (corpus and query embeddings, and the
fact-derivation pass on the default `models.background_jobs_model`), and `EXA_API_KEY` (the
assistant pack's research extension refuses to boot without its search provider), and a template
`[o11y] otlp_endpoint` — the recall collector binds a free loopback port per run.

The stack imposes no Postgres requirement on this corpus — 23 pages ask nothing of the database that
SQLite cannot hold, and provisioning accepts a SQLite template. The recipe below still names Postgres
because that is what every measured run of this leaf used: a run boots `ufoctl serve` while the eval
driver and DBOS read and write the same database concurrently, and SQLite's single writer under that
load is unmeasured here. Point it at a Postgres you own rather than a shared one — a loaded instance
answers a fresh connection slowly enough to time out materialization.

```bash
mkdir -p .local/issue-recall

cat > .local/issue-recall/template.toml <<'EOF'
[database]
url = "postgresql+asyncpg://ufo:ufo@127.0.0.1:5541/ufo"

[blob]
backend = "filesystem"
root = "./blobs"

[pack]
name = "assistant"

[research]
search_provider = "exa"

[connect]
public_base_url = "http://127.0.0.1:8710"

[o11y]
otlp_endpoint = "http://127.0.0.1:4318"
EOF

cat > .local/issue-recall/matrix.toml <<'EOF'
[[run]]
label = "issue-recall"
config = ".local/issue-recall/template.toml"
issue_recall = true
EOF

set -a; source .env; set +a
uv run python -m evals.stack .local/issue-recall/matrix.toml
```

Run directories land under `.local/evals/<stamp>/issue-recall/` with `seed.log` (staging, sync,
derivation), `serve.log`, `eval.log`, and `state/<corpus-digest>/` holding the staged pages and
`readiness.json`. Reports land in the shared `eval-reports/` archive.

Materialization is idempotent for one corpus digest: the staged page files are reused and verified
rather than rewritten. Editing the fixture changes the digest, which changes the staging directory,
the readiness, and every case's suite digest — so a corpus edit can never be compared against a run
of the old one.
