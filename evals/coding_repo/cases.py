"""The pinned coding corpus: tasks this repository already answered, each fixed to the commit its
work started from.

A case names the base commit the child fetches, the merged commit that answered it, and the criteria
that merged commit's own reasoning established. The base is the parent of the
squash the work landed as, so the defect is present and the answer is absent — and the child fetches
exactly that one commit, so no later commit, and never the fix, is reachable from the sandbox.

What a case delivers decides how it is measured. A `reply` case answers in the turn's own text and
is judged against its criteria inside the run. A `patch` or `document` case hands back a file, which
the run gates deterministically: fetched at the pin, and for a patch, applying to that tree and
touching the paths the real change touched.

A brief carries only what the requester knew — a symptom, an observation, an intent, sometimes a
lead that is wrong. The mechanism, the file, and the shape of the answer are what the case measures,
so they live in the criteria."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
MAX_CRITERIA_PER_CASE = 12
DOCUMENT_SUFFIX = ".md"
PATCH_SUFFIX = ".patch"

type CodingKind = Literal["research", "feature", "fix"]
type CodingDeliverable = Literal["reply", "patch", "document"]


@dataclass(frozen=True)
class CodingCase:
    """One pinned task. `base_sha` is what the child fetches; `reference_sha` is the merged commit
    that answered it, whose own diff is what the gate is proven against, and `expected_paths` are
    the source files that commit changed, tests excluded — the deterministic floor a candidate patch
    must reach. `document_path` is the workspace path a document case is asked to write, so the gate
    checks the file the brief named rather than any shared file."""

    name: str
    kind: CodingKind
    base_sha: str
    brief: str
    criteria: tuple[str, ...]
    deliverable: CodingDeliverable = "reply"
    reference_sha: str = ""
    expected_paths: tuple[str, ...] = ()
    document_path: str = ""

    @property
    def deliverable_suffix(self) -> str:
        return PATCH_SUFFIX if self.deliverable == "patch" else DOCUMENT_SUFFIX

    @property
    def delivers_a_file(self) -> bool:
        return self.deliverable != "reply"

    def __post_init__(self) -> None:
        if SHA_PATTERN.fullmatch(self.base_sha) is None:
            raise ValueError(f"{self.name}: base_sha must be a full 40-character commit sha")
        if not self.criteria:
            raise ValueError(f"{self.name}: a case states its criteria")
        if len(self.criteria) > MAX_CRITERIA_PER_CASE:
            raise ValueError(
                f"{self.name}: {len(self.criteria)} criteria exceeds the judge's "
                f"{MAX_CRITERIA_PER_CASE}"
            )
        if (self.deliverable == "patch") != (self.kind in ("feature", "fix")):
            raise ValueError(f"{self.name}: a {self.kind} case delivers a {self.deliverable}")
        if self.deliverable == "patch":
            if SHA_PATTERN.fullmatch(self.reference_sha) is None:
                raise ValueError(
                    f"{self.name}: a patch case names the merged commit that answered it"
                )
            if not self.expected_paths:
                raise ValueError(f"{self.name}: a patch case names the paths its answer changes")
            return
        if self.reference_sha or self.expected_paths:
            raise ValueError(
                f"{self.name}: only a patch case names a reference commit or expected paths"
            )
        if (self.deliverable == "document") != bool(self.document_path):
            raise ValueError(f"{self.name}: a document case names the path it writes")
        if self.document_path and not self.document_path.endswith(DOCUMENT_SUFFIX):
            raise ValueError(f"{self.name}: a document deliverable is {DOCUMENT_SUFFIX}")


RESEARCH_CASES: tuple[CodingCase, ...] = (
    CodingCase(
        name="research-offload-dir-squatter",
        kind="research",
        base_sha="3e89876b33b96bd978ce3d117e7731673e11144e",
        brief=(
            "A turn died with this, and every turn in that conversation has died the same way "
            "since:\n\n"
            "    OSError: mkdir: cannot create directory '/workspace/.tool-output': File exists\n\n"
            "The member had been working in that workspace with bash for a while before it "
            "started. Nothing about the request that failed was unusual, and the same request in a "
            "fresh conversation is fine.\n\n"
            "Explain what is happening: what that path is for, why the error says the directory "
            "exists when creating it, why one conversation is poisoned permanently rather than "
            "just failing that one turn, and why the agent cannot route around it. Name the files "
            "and functions involved. Do not change any code."
        ),
        criteria=(
            "It identifies /workspace/.tool-output as the engine's own private offload area for "
            "oversize tool results and truncation salvage, not member data.",
            "It explains that a regular file (or broken symlink) is squatting that exact path, and "
            "that `mkdir -p` is idempotent for an existing directory but fails `File exists` "
            "against a non-directory.",
            "It identifies a member-driven write — bash or a file tool — as what could leave a "
            "regular file at that path.",
            "It explains the permanence: the workspace is durable, so once the file is there every "
            "later offload and salvage write in that conversation raises.",
            "It explains why the agent cannot route around it: the failure is in the harness's own "
            "offload/recovery machinery, so the exception escapes the round loop into the turn's "
            "terminal commit as a failed turn rather than arriving as a tool result the model "
            "sees.",
            "It names the real code path — the sandbox session's offload write and the engine "
            "round loop / compaction salvage that call it — with file paths, not a guess.",
            "It changes no files and reports findings only.",
        ),
    ),
    CodingCase(
        name="research-sqlite-test-lock",
        kind="research",
        base_sha="29918d309c2cc26c3fad69d1cd3f8a476677b74e",
        brief=(
            "A CI test shard failed one test's `db` fixture setup at `begin immediate` with "
            "`sqlite3.OperationalError: database is locked`, and the test it failed had nothing to "
            "do with whatever else was running. It does not reproduce locally.\n\n"
            "Work out the mechanism from the code: which file the lock is on, who the competing "
            "writers are, why a test can be failed by activity that belongs to a different test, "
            "and why raising the busy timeout is the wrong answer. Name the files and functions. "
            "Do not change any code."
        ),
        criteria=(
            "It identifies that every sqlite test in an xdist worker runs against one shared "
            "session database file, and names the fixture and plugin that establish it.",
            "It states that sqlite allows a single writer per file, so writers from different "
            "tests contend on that one file.",
            "It identifies the competing writers concretely: background turn workflows a previous "
            "test left running on the session DBOS worker, whose late writes land in tables the "
            "next test's setup is wiping.",
            "It identifies the per-test setup wipe as itself a heavy standing writer.",
            "It explains that any hold crossing the busy timeout fails an unrelated test's setup, "
            "and that the trigger is environmental (a loaded CI disk) while the coupling that lets "
            "it fail an unrelated test is structural.",
            "It argues against raising the timeout, and toward removing the coupling — per-test "
            "isolation of the database file — as the fix at the root.",
            "It changes no files and reports findings only.",
        ),
    ),
    CodingCase(
        name="research-credential-never-in-sandbox",
        kind="research",
        base_sha="09ea6a713839e46bb40e2b6317a8110c2e02a00f",
        brief=(
            "I need to explain to a customer's security reviewer how our sandbox reaches a "
            "third-party API with their credential without that credential being inside the "
            "sandbox.\n\n"
            "Trace it in the code end to end: what the sandbox actually holds, where the real "
            "secret is substituted, what decides which hosts are reachable at all, and how a "
            "brokered OAuth account differs from a stored key. Cover git specifically — a "
            "`git clone` of a private repository authenticates, so say what git is given and why "
            "it needs handling the other clients do not. Name files, functions, and types. Do not "
            "change any code."
        ),
        criteria=(
            "It states that the sandbox holds only a sentinel value, never the real secret, and "
            "names the sentinel mechanism.",
            "It identifies the egress proxy as the substitution point and names the rule that "
            "performs the sentinel-to-secret swap on the wire.",
            "It explains that egress is default-deny and that the reachable host set is derived "
            "rather than registered, naming the derivations (model provider hosts, a grant's host, "
            "connector transfer hosts, an injecting credential slot's host).",
            "It distinguishes a brokered grant from a stored key: the grant injects nothing "
            "because the broker holds the token and executes the request server-side, so the "
            "request is forwarded rather than re-originated.",
            "It explains that git will not present the run token unprompted — its default proxy "
            "auth waits for a challenge the proxy never sends — so its config is set to present "
            "the signed token on the first CONNECT.",
            "It states that a workspace holding a git credential gets that host's authorization "
            "header configured against the sentinel, so clone and push authenticate off a value "
            "the swap replaces.",
            "It cites real files and symbols for each step rather than describing a plausible "
            "design.",
            "It changes no files and reports findings only.",
        ),
    ),
    CodingCase(
        name="research-secret-management-survey",
        kind="research",
        base_sha="09ea6a713839e46bb40e2b6317a8110c2e02a00f",
        deliverable="document",
        document_path="/workspace/secret_survey.md",
        brief=(
            "Survey how API keys and secrets are managed today, so a human can write an issue "
            "proposing we move dev/testing/prod keys to a single secret manager. I want an "
            "accurate picture of what exists, not a design.\n\n"
            "Answer these specifically, citing file paths:\n"
            "1. Where do third-party API keys live per environment? Leads I have, which I want "
            "verified rather than repeated: Secrets Manager secrets named like "
            "`ufo/ufo-testing/api-keys` and `ufo/ufo-testing/preview-api-keys`; a Kubernetes "
            "ExternalSecret named `ufo-platform-secrets` projecting keys into env vars; Terraform "
            "that seeds placeholder values and then ignores changes to them so real values are set "
            "out of band. List the actual secret names, the files that define them, and the "
            "consumers.\n"
            "2. What are the distinct deploys, and how does each get its keys? How does local "
            "development get them today?\n"
            "3. Which environment variable names does the application expect? Give the list as it "
            "actually appears, and name the file that is authoritative for it.\n"
            "4. Which CI workflows use which secrets?\n"
            "5. Any existing mention anywhere of Doppler, Vault, SOPS, or sealed-secrets.\n\n"
            "Write the findings to /workspace/secret_survey.md as tight factual bullets grouped by "
            "those five questions, under 1200 words, and share that file. Then reply with a 5-10 "
            "line summary of the key facts. Report what exists; do not propose a design."
        ),
        criteria=(
            "It names the real Secrets Manager secrets and where they come from: the prefix built "
            "from the deploy name, and the api-keys, postgres, platform, and gateway Slack Connect "
            "secrets, citing the Terraform file that defines them.",
            "It corrects the mistaken lead rather than repeating it: there is no preview-api-keys "
            "secret at this commit.",
            "It identifies the ufo-platform-secrets ExternalSecret as the projection into "
            "environment variables, cites the template that declares it, and names where the "
            "projected variables are consumed.",
            "It reports the placeholder pattern exactly: the api-keys properties are seeded empty "
            "and the resource ignores later changes to them, so real values are set out of band "
            "and never enter state.",
            "It reports that the authoritative list of expected environment variable names is the "
            "ExternalSecret's property-to-variable mapping rather than a central configuration "
            "module, and gives the names as they appear there.",
            "It reports local development accurately: a dotenv file beside the config file, parsed "
            "and loaded by the CLI before any verb reads a key, with the init verb minting dev "
            "secrets into it — not a password manager, direnv, or an AWS CLI fetch.",
            "It notes that the gateway Slack Connect token is deliberately its own secret, kept "
            "out of the platform projection because only the gateway pod reads it.",
            "It states that no Doppler, Vault, SOPS, or sealed-secrets integration exists anywhere "
            "at this commit.",
            "It does not present an unrelated keyword match as a finding: the repository's vault "
            "matches are memory test fixtures, not secret tooling.",
            "It reports CI secret usage from the workflow files themselves rather than assuming.",
            "It delivers the written survey at the requested path, within the requested length, "
            "and shares it, and the reply carries a summary of the requested length.",
            "It reports only what exists and proposes no design or migration.",
        ),
    ),
)

FIX_CASES: tuple[CodingCase, ...] = (
    CodingCase(
        name="fix-progress-post-after-commit",
        kind="fix",
        deliverable="patch",
        base_sha="931fc84d43f4a546725339ebd164e2bbb9802d2a",
        reference_sha="2dec8dcf12d702d28a6a2b0d5e4cad95471f9f2d",
        expected_paths=(
            "core/src/ufo/ext/surface.py",
            "extensions/slack/ufo_ext_slack/surface.py",
        ),
        brief=(
            "In Slack, an interim progress update sometimes lands in the thread below the final "
            "reply it was reporting progress towards, so the thread reads as though work continued "
            "after the answer was given.\n\n"
            "It is intermittent. It showed up in CI as "
            "`test_a_rejected_progress_post_costs_an_update_and_not_the_reply` failing, where the "
            "last `chat.postMessage` was a progress line instead of the reply — the test was right "
            "and the product was wrong.\n\n"
            "Find the root cause and fix it deterministically. No sleeps, no retries, no widened "
            "bounds."
        ),
        criteria=(
            "It root-causes the ordering to the window between the turn committing and the "
            "progress reporter learning it ended, because the reporter polls durable state rather "
            "than being told by the hub — not to hub delivery, not to the test.",
            "The fix makes a checkpoint consult durable turn state at the moment it is about to "
            "post, and return without posting when the turn has already committed.",
            "A missing turn row counts as terminal rather than raising or posting.",
            "The read is added to the surface context available to the extension, and the guard is "
            "applied in the Slack progress reporter; the engine's commit path is not changed.",
            "It introduces no sleep, retry, backoff, or timeout change, consistent with the "
            "repository's rule that an internal fault is fixed at its root.",
            "It ships a test driving the real shape — a checkpoint coming due after the turn "
            "committed posts nothing — and that test fails if the guard is removed.",
            "The added cost is bounded and stated: one read per checkpoint, against a cadence that "
            "backs off, so a long turn pays a handful of reads.",
            "It does not claim to close the window of a request already in flight when the turn "
            "commits; a residual of that shape is named rather than hidden.",
        ),
    ),
    CodingCase(
        name="fix-status-cleared-on-shutdown",
        kind="fix",
        deliverable="patch",
        base_sha="2d1bdc506676c83015af907f7942b0c686c5ccf0",
        reference_sha="931fc84d43f4a546725339ebd164e2bbb9802d2a",
        expected_paths=("extensions/slack/ufo_ext_slack/surface.py",),
        brief=(
            "When we roll the serve deployment, Slack threads for turns that are still running go "
            "blank: the status disappears, then the turn is recovered and runs for another hour "
            "while the member sees the thread a finished turn leaves.\n\n"
            "From the 2026-07-30 02:41 UTC rollout: each dying pod wrote thread status with an "
            "empty status text for every in-flight turn it was following, in a single burst. The "
            "post-shutdown log line that runs after the server returns fired in the same "
            "second.\n\nFix it so a turn that is still running keeps its status, and make sure the "
            "paths that should still clear a status keep clearing it."
        ),
        criteria=(
            "It root-causes the clear to the status follower clearing on every exit path — a bare "
            "`finally` — so a follower killed by process shutdown writes the clear on its way out.",
            "It identifies cancellation as the only signal available to distinguish 'this process "
            "is ending' from 'the turn ended', and the fix keys on `CancelledError` specifically.",
            "It supports that choice from the code: followers are cancelled before the durable "
            "runtime is destroyed, and a shutdown-cancelled turn commits no terminal, so there is "
            "nothing durable for the follower to read.",
            "Cancellation is re-raised rather than swallowed.",
            "Normal return and `Exception` still clear the status, so a member is never left "
            "watching a status that nothing will replace.",
            "The suppressed path emits its own observability record, because no status and no log "
            "is indistinguishable from a follower that never ran.",
            "It ships a test that arms a real follower, waits for a status write, cancels the "
            "task, and asserts no empty status was ever written and the new record fired.",
            "It names the load-bearing invariant it now depends on — that nothing cancels a status "
            "follower for a live reason — and the residual that a turn finishing during the "
            "shutdown drain keeps its status up.",
            "It confines the change to the Slack status follower and does not alter the progress "
            "reporter, core, the SDK, or the manifest.",
        ),
    ),
    CodingCase(
        name="fix-sqlite-journal-mode",
        kind="fix",
        deliverable="patch",
        base_sha="b956e7abf726464c04da6eb768eacc5d8fe99f07",
        reference_sha="4909102e06a296f63f91f44668a7db4a7d0de4ef",
        expected_paths=("core/src/ufo/db.py",),
        brief=(
            "A second connection opening a migrated sqlite file can fail outright with "
            "`sqlite3.OperationalError: database is locked`, and the busy timeout does not help "
            "it. A file our own engine created does not behave this way.\n\n"
            "Work out why the migrated file is different and fix it at the root. Do not raise the "
            "busy timeout, add a retry, or serialize the readers."
        ),
        criteria=(
            "It identifies that the migration tool builds its own engine, so the connect hook that "
            "sets the journal mode never runs against a migrated file, leaving it in sqlite's "
            "default rollback journal — a measured claim, not a guess.",
            "It explains that every later engine opening that file therefore has to convert it, "
            "that the conversion takes an exclusive lock, and that the journal-mode pragma is not "
            "a statement the busy timeout retries.",
            "The fix seals the file at the end of migration — once, before any engine or any copy "
            "of the file exists, where nothing else holds it.",
            "It checkpoints or truncates the write-ahead log so no sidecar file is left beside the "
            "migrated file and a plain byte copy carries every migrated row.",
            "It does not raise the busy timeout, add a retry or backoff, or serialize readers.",
            "It ships a test that produces the lock deterministically — a held write transaction "
            "on a copy while a second connection asks for the mode — and that reds when only the "
            "seal is reverted.",
            "The test also pins the properties the seal must not break: no sidecar left behind, "
            "and a byte copy still reads its migration-version rows.",
            "It is honest about scope: this is a demonstrated way to produce that error which the "
            "current design leaves open, not a proven identification of any particular observed "
            "flake.",
        ),
    ),
)

FEATURE_CASES: tuple[CodingCase, ...] = (
    CodingCase(
        name="feature-progress-step-summaries",
        kind="feature",
        deliverable="patch",
        base_sha="2c4cce5e0bef004acd7760f7b51486bf9dcc826b",
        reference_sha="07a4c2a883af1d94b1fccc7ea0c2e08e94ffeb73",
        expected_paths=("extensions/slack/ufo_ext_slack/surface.py",),
        brief=(
            "Our interim Slack progress posts close with a tool tally that tells a member "
            "nothing:\n\n"
            "    _20m in · 7 tool calls since the last update: bash x7_\n\n"
            "Change that closing line to describe the work of the interval in the model's own "
            "words — every tool call already carries a short description of what it is doing — so "
            "the line reads as a summary rather than a log. A busy interval must not produce a "
            "long line.\n\n"
            "Everything else about a progress post stays exactly as it is: the quoted narration, "
            "the current-step line, the no-new-activity note, the skip of a checkpoint with "
            "nothing to report, and the cadence."
        ),
        criteria=(
            "The interval's activity record keeps the calls' descriptions, deduplicated and in the "
            "order they happened, in place of the per-slug counter.",
            "The closing line names a small fixed number of steps and summarizes the remainder as "
            "a count, so a busy interval reads as a summary.",
            "A call with no description is named by its slug read as words, and a raw slug "
            "survives only when the slug is nothing but separators.",
            "The old slug-tally fallback is removed rather than left alongside the new shape.",
            "The steps are bounded as they are recorded, so the line is bounded by how many steps "
            "it can name.",
            "The quoted narration, the current-step line (including the writing-size step for a "
            "tool-free turn), the no-new-activity note, the signalless-checkpoint skip, and the "
            "cadence are all unchanged.",
            "The bounds are named constants rather than inline magic values, and the line's copy "
            "is spartan and factual per the repository's user-facing copy rules.",
            "It ships tests covering a described call, an undescribed call named by its slug, and "
            "the remainder count.",
        ),
    ),
    CodingCase(
        name="feature-bounded-error-class",
        kind="feature",
        deliverable="patch",
        base_sha="5ccafe058001313155e81693b04a657f4843c81a",
        reference_sha="a98301dd581b843fdd9408f09e18e70f976b8dd8",
        expected_paths=(
            "core/src/ufo/o11y.py",
            "core/src/ufo/models/anthropic.py",
            "core/src/ufo/models/openai.py",
        ),
        brief=(
            "`error_class` rides several of our metrics as an unbounded dimension: the value is "
            "whatever class raised — an extension handler, a gating hook, a provider SDK, a "
            "database driver. A metric series costs the product of its dimensions, so every new "
            "class permanently mints a series across every other dimension of that metric, for a "
            "class nothing reads.\n\n"
            "Bound it: a class we actually read keeps its own series, everything else folds to a "
            "single catch-all. Do not make each emission site repeat the rule, and do not let an "
            "unknown class raise — every one of those emissions sits in a failure path, where "
            "raising would destroy the error the metric exists to report."
        ),
        criteria=(
            "The fold happens at one boundary that every emission passes through, so no call site "
            "repeats the rule and the existing emissions are untouched.",
            "An unlisted class folds to the catch-all and never raises.",
            "The bounded set is derived from checkable sources — the provider SDKs' own error "
            "bases, the driver and OS error trees, the clauses the clients already catch — rather "
            "than a hand-typed list of vendor names.",
            "A derivation that walks a base class's subclasses also filters by the declaring "
            "package, so the set does not become a live graph of whatever the interpreter happens "
            "to have imported.",
            "Every value in the set is fixed at import time: nothing in it grows with traffic, "
            "tenants, or request volume.",
            "It states the deliberate widening its chosen roots admit rather than leaving it "
            "unremarked.",
            "It ships tests proving both directions — a listed class keeps its own name, an "
            "unlisted one folds — and covers a class the derivation cannot find by asking the real "
            "stack what it raises.",
            "It does not change what the emissions mean or drop the dimension.",
        ),
    ),
    CodingCase(
        name="feature-short-site-label",
        kind="feature",
        deliverable="patch",
        base_sha="e0876ff14607373ec22b1f3d684eadc5f9f18206",
        reference_sha="ee7372148c5ab453062acabd277372e3c0c0f89a",
        expected_paths=(
            "core/src/ufo/sandbox/ingress_host.py",
            "docs/rfcs/0020-hosted-sites.md",
        ),
        brief=(
            "A hosted site's hostname is 55 characters, and almost all of it is signature:\n\n"
            "    vrp2zis5inhwtggwsuwzazy67mpubymqbad6dzha5tqoqfiaqreq67q"
            ".testing.flyingobject.ai\n\n"
            "A member reads and copies this. The signature is not what protects a site — every "
            "way in verifies the credential it arrived with. Work out what the signature in the "
            "label actually buys us, shorten it to the width that argument supports, and state "
            "the argument. Keep the protections around the label itself intact."
        ),
        criteria=(
            "It narrows the signature width rather than removing the tag, and the width it lands "
            "on is justified rather than asserted.",
            "It states what the tag actually protects — keeping a guessed hostname from reaching "
            "the conversation read at all — and that the verified credential, not the label, is "
            "the gate.",
            "It prices the remaining tag concretely (the request volume a guess would need) and "
            "explains why a wider tag buys nothing beyond that.",
            "The import-time hostname-length bound and the canonical-spelling check still hold, "
            "and the alias count the narrower signature implies is updated.",
            "The test derives the alias count from the module's own arithmetic instead of a "
            "literal, so narrowing the signature again cannot leave it stale.",
            "It explains why the canonical-spelling check matters — without it a browser treats "
            "every alternate spelling as its own origin with its own cookie jar.",
            "It names the blast radius: labels are derived rather than stored, so this reassigns "
            "every existing site's hostname.",
            "It updates the RFC's statements of the arithmetic in the same change, per the "
            "repository's rule that docs move with the change they describe.",
        ),
    ),
)

PATCH_CASES: tuple[CodingCase, ...] = (*FIX_CASES, *FEATURE_CASES)
CASES: tuple[CodingCase, ...] = (*RESEARCH_CASES, *PATCH_CASES)
