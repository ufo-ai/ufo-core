"""Code-review orchestration over a local pull-request fixture."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import sqlalchemy as sa

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    ToolInvocation,
    WorkspaceFile,
)
from evals.harness.harness import Json, JsonObject
from ufo.blob import WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.schema import tables

BASE_SHA = "08871cfcd60a9c407221af81ff22411754224c7e"
HEAD_SHA = "52b8c52b2575d4eaa4403765de981824fe8c22bd"
OLD_HEAD_SHA = "1111111111111111111111111111111111111111"
PAGE_ID = UUID("20000000-0000-0000-0000-000000000001")
PREEMPT_PAGE_ID = UUID("20000000-0000-0000-0000-000000000002")
INSTRUCTION_PAGE_ID = UUID("20000000-0000-0000-0000-000000000003")
NO_PLAN_PAGE_ID = UUID("20000000-0000-0000-0000-000000000004")
STRICT_RESULT_PAGE_ID = UUID("20000000-0000-0000-0000-000000000005")
SOURCE_ID = UUID("30000000-0000-0000-0000-000000000001")
PREEMPT_SOURCE_ID = UUID("30000000-0000-0000-0000-000000000002")
INSTRUCTION_SOURCE_ID = UUID("30000000-0000-0000-0000-000000000003")
NO_PLAN_SOURCE_ID = UUID("30000000-0000-0000-0000-000000000004")
STRICT_RESULT_SOURCE_ID = UUID("30000000-0000-0000-0000-000000000005")
OLD_SPAWNS = (
    "40000000-0000-0000-0000-000000000001",
    "40000000-0000-0000-0000-000000000002",
)
FIXTURE_FILES = ("alpha.py", "beta.py", "gamma.py", "delta.py", "epsilon.py", "zeta.py")
ROOT_INSTRUCTION = "Review all changed Python files."
NESTED_INSTRUCTION = "Review src changes against supported behavior."
CHECKOUT_PATTERN = re.compile(r"/workspace/code-review-[0-9a-f]{40}-(?:correctness|security)")
REVIEW_FAILURES = (
    "escapes /workspace",
    "is not a regular file",
    "Unable to read current working directory",
    "index.lock",
    "remote origin already exists",
)
FIXTURE_TIME = datetime(2026, 8, 25, tzinfo=UTC)
GIT_ENV = {
    "GIT_AUTHOR_DATE": "2026-08-25T00:00:00Z",
    "GIT_COMMITTER_DATE": "2026-08-25T00:00:00Z",
}
HEAD_GIT_ENV = {
    "GIT_AUTHOR_DATE": "2026-08-25T00:01:00Z",
    "GIT_COMMITTER_DATE": "2026-08-25T00:01:00Z",
}
PATCH = """diff --git a/src/alpha.py b/src/alpha.py
index 351b79b..041b5f7 100644
--- a/src/alpha.py
+++ b/src/alpha.py
@@ -1,2 +1,2 @@
 def value():
-    return 0
+    return 1
diff --git a/src/AGENTS.md b/src/AGENTS.md
new file mode 100644
--- /dev/null
+++ b/src/AGENTS.md
@@ -0,0 +1 @@
+Review src changes against supported behavior.
diff --git a/src/CLAUDE.md b/src/CLAUDE.md
new file mode 120000
--- /dev/null
+++ b/src/CLAUDE.md
@@ -0,0 +1 @@
+AGENTS.md
\\ No newline at end of file
diff --git a/src/beta.py b/src/beta.py
index 351b79b..041b5f7 100644
--- a/src/beta.py
+++ b/src/beta.py
@@ -1,2 +1,2 @@
 def value():
-    return 0
+    return 1
diff --git a/src/delta.py b/src/delta.py
index 351b79b..041b5f7 100644
--- a/src/delta.py
+++ b/src/delta.py
@@ -1,2 +1,2 @@
 def value():
-    return 0
+    return 1
diff --git a/src/epsilon.py b/src/epsilon.py
index 351b79b..041b5f7 100644
--- a/src/epsilon.py
+++ b/src/epsilon.py
@@ -1,2 +1,2 @@
 def value():
-    return 0
+    return 1
diff --git a/src/gamma.py b/src/gamma.py
index 351b79b..041b5f7 100644
--- a/src/gamma.py
+++ b/src/gamma.py
@@ -1,2 +1,2 @@
 def value():
-    return 0
+    return 1
diff --git a/src/zeta.py b/src/zeta.py
index 351b79b..041b5f7 100644
--- a/src/zeta.py
+++ b/src/zeta.py
@@ -1,2 +1,2 @@
 def value():
-    return 0
+    return 1
"""
WORKSPACE_FILES = (
    WorkspaceFile("review-target/README.md", b"# Review fixture\n"),
    WorkspaceFile("review-target/AGENTS.md", f"{ROOT_INSTRUCTION}\n".encode()),
    *(
        WorkspaceFile(f"review-target/src/{name}", b"def value():\n    return 0\n")
        for name in FIXTURE_FILES
    ),
    WorkspaceFile(".review.patch", PATCH.encode()),
)


async def _seed_page(
    workspace_id: UUID,
    agent_id: UUID,
    blob: WorkspaceBlobStore,
    page_id: UUID,
    source_id: UUID,
    head_sha: str,
) -> None:
    body = json.dumps(
        {
            "number": 7,
            "state": "open",
            "draft": False,
            "merged": False,
            "html_url": "file:///workspace/review-target/pull/7",
            "base": {
                "sha": BASE_SHA,
                "repo": {
                    "clone_url": "file:///workspace/review-target",
                    "full_name": "eval/review-target",
                },
            },
            "head": {"sha": head_sha},
        },
        sort_keys=True,
    ).encode()
    body_ref = f"pages/{page_id}"
    await blob.put(body_ref, body)
    async with workspace_tx() as connection:
        await connection.execute(sa.delete(tables.page).where(tables.page.c.id == page_id))
        await connection.execute(
            sa.delete(tables.source_grant).where(tables.source_grant.c.source_id == source_id)
        )
        await connection.execute(sa.delete(tables.source).where(tables.source.c.id == source_id))
        await connection.execute(
            sa.insert(tables.source).values(
                id=source_id,
                workspace_id=workspace_id,
                backend="fixture",
                config={},
                subject="shared",
                next_sync_at=FIXTURE_TIME,
                created_at=FIXTURE_TIME,
                updated_at=FIXTURE_TIME,
            )
        )
        await connection.execute(
            sa.insert(tables.source_grant).values(
                workspace_id=workspace_id,
                source_id=source_id,
                agent_id=agent_id,
                created_at=FIXTURE_TIME,
                updated_at=FIXTURE_TIME,
            )
        )
        await connection.execute(
            sa.insert(tables.page).values(
                id=page_id,
                workspace_id=workspace_id,
                source_id=source_id,
                digest=f"sha256:{hashlib.sha256(body).hexdigest()}",
                body_ref=body_ref,
                stream="pull_requests",
                title="Review fixture",
                record_created_at=FIXTURE_TIME.isoformat(),
                record_updated_at=FIXTURE_TIME.isoformat(),
                subject="shared",
                tombstone=False,
                created_at=FIXTURE_TIME,
                updated_at=FIXTURE_TIME,
            )
        )


async def _seed_review(workspace_id: UUID, agent_id: UUID, blob: WorkspaceBlobStore) -> None:
    await _seed_page(workspace_id, agent_id, blob, PAGE_ID, SOURCE_ID, HEAD_SHA)


async def _seed_preemption(workspace_id: UUID, agent_id: UUID, blob: WorkspaceBlobStore) -> None:
    await _seed_page(
        workspace_id,
        agent_id,
        blob,
        PREEMPT_PAGE_ID,
        PREEMPT_SOURCE_ID,
        HEAD_SHA,
    )


async def _seed_instruction_read(
    workspace_id: UUID, agent_id: UUID, blob: WorkspaceBlobStore
) -> None:
    await _seed_page(
        workspace_id,
        agent_id,
        blob,
        INSTRUCTION_PAGE_ID,
        INSTRUCTION_SOURCE_ID,
        HEAD_SHA,
    )


async def _seed_no_plan(workspace_id: UUID, agent_id: UUID, blob: WorkspaceBlobStore) -> None:
    await _seed_page(
        workspace_id,
        agent_id,
        blob,
        NO_PLAN_PAGE_ID,
        NO_PLAN_SOURCE_ID,
        HEAD_SHA,
    )


async def _seed_strict_result(workspace_id: UUID, agent_id: UUID, blob: WorkspaceBlobStore) -> None:
    await _seed_page(
        workspace_id,
        agent_id,
        blob,
        STRICT_RESULT_PAGE_ID,
        STRICT_RESULT_SOURCE_ID,
        HEAD_SHA,
    )


async def _git(root: Path, *args: str, env: dict[str, str] | None = None) -> str:
    process = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(root),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, **(env or {})},
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(stderr.decode().strip())
    return stdout.decode().strip()


async def _prepare_review(_workspace_id: UUID, workspace: Path) -> None:
    root = workspace / "review-target"
    await _git(root, "init", "-b", "main")
    await _git(root, "add", "README.md", "AGENTS.md", "src")
    await _git(
        root,
        "-c",
        "user.name=Eval",
        "-c",
        "user.email=eval@localhost",
        "commit",
        "-m",
        "base",
        env=GIT_ENV,
    )
    if await _git(root, "rev-parse", "HEAD") != BASE_SHA:
        raise RuntimeError("code-review fixture base SHA changed")
    await _git(root, "apply", str(workspace / ".review.patch"))
    await _git(root, "add", "src")
    await _git(
        root,
        "-c",
        "user.name=Eval",
        "-c",
        "user.email=eval@localhost",
        "commit",
        "-m",
        "head",
        env=HEAD_GIT_ENV,
    )
    if await _git(root, "rev-parse", "HEAD") != HEAD_SHA:
        raise RuntimeError("code-review fixture head SHA changed")


def _call_evidence(output: CapabilityOutput, call_ids: frozenset[str]) -> list[Json]:
    if output.timing is None:
        return []
    return [
        {
            "turn": str(turn.turn_id),
            "role": turn.role,
            "message": step.message_index,
            "tool": step.name,
        }
        for turn in output.timing.turns
        for step in turn.steps
        if step.call_id in call_ids
    ]


def _child_calls(output: CapabilityOutput) -> tuple[tuple[ToolInvocation, ...], ...]:
    if output.timing is None:
        return ()
    calls = {call.call_id: call for call in output.calls}
    return tuple(
        tuple(
            calls[step.call_id]
            for step in turn.steps
            if step.kind == "tool_call" and step.call_id in calls
        )
        for turn in output.timing.turns
        if turn.role == "child"
    )


def _review_failures(output: CapabilityOutput) -> tuple[str, ...]:
    texts = (
        *output.tool_errors,
        *(call.result for calls in _child_calls(output) for call in calls),
    )
    return tuple(text for text in texts if any(failure in text for failure in REVIEW_FAILURES))


async def _grade_parallel_review(output: CapabilityOutput) -> CapabilityVerdict:
    spawns = tuple(
        call
        for call in output.own_calls
        if call.name == "spawn"
        and isinstance(target := call.input.get("target"), str)
        and target.removeprefix("profile:") == "coding"
        and call.input.get("background") is True
    )
    evidence: JsonObject = {"spawn_count": len(spawns)}
    if len(spawns) != 2:
        return CapabilityVerdict(
            False, f"started {len(spawns)} background reviewers, expected 2", evidence
        )
    spawn_steps = _call_evidence(output, frozenset(call.call_id for call in spawns))
    evidence["spawns"] = spawn_steps
    spawn_messages: set[int] = set()
    for item in spawn_steps:
        if not isinstance(item, dict):
            continue
        message = item.get("message")
        if isinstance(message, int):
            spawn_messages.add(message)
    if len(spawn_messages) != 1:
        return CapabilityVerdict(
            False, "the two reviewers were not spawned in one response", evidence
        )
    if output.timing is None:
        return CapabilityVerdict(False, "the run recorded no timing", evidence)
    failures = _review_failures(output)
    evidence["checkout_failures"] = [*failures]
    if failures:
        return CapabilityVerdict(False, "a reviewer hit a known checkout failure", evidence)
    calls = {call.call_id: call for call in output.calls}
    child_batches: list[Json] = []
    checkout_roots: set[str] = set()
    for turn in (turn for turn in output.timing.turns if turn.role == "child"):
        groups: dict[int, list[str]] = {}
        durable_groups: dict[int, list[str]] = {}
        current_round = 0
        bulk = False
        for step in turn.steps:
            if step.kind == "model_round":
                current_round += 1
                continue
            if step.kind != "tool_call":
                continue
            call = calls.get(step.call_id)
            if call is not None:
                encoded = json.dumps(call.input)
                checkout_roots.update(CHECKOUT_PATTERN.findall(encoded))
                if "/tmp/" in encoded or encoded.startswith('"/tmp'):
                    evidence["checkout_roots"] = [*sorted(checkout_roots)]
                    return CapabilityVerdict(
                        False, "a reviewer put repository work under /tmp", evidence
                    )
                bulk = bulk or sum(name in encoded for name in FIXTURE_FILES) >= 2
            if step.message_index is None:
                if current_round:
                    durable_groups.setdefault(current_round, []).append(step.name)
                continue
            groups.setdefault(step.message_index, []).append(step.name)
        maximum = max(
            (len(group) for group in (*groups.values(), *durable_groups.values())), default=0
        )
        child_batches.append({"turn": str(turn.turn_id), "max_same_round": maximum, "bulk": bulk})
        if maximum < 2 and not bulk:
            evidence["children"] = child_batches
            return CapabilityVerdict(
                False,
                "a reviewer used no same-round batch or bulk operation for known independent work",
                evidence,
            )
    evidence["children"] = child_batches
    evidence["checkout_roots"] = [*sorted(checkout_roots)]
    if len(child_batches) != 2:
        return CapabilityVerdict(
            False, f"recorded {len(child_batches)} reviewer turns, expected 2", evidence
        )
    expected_roots = {
        f"/workspace/code-review-{HEAD_SHA}-correctness",
        f"/workspace/code-review-{HEAD_SHA}-security",
    }
    if checkout_roots != expected_roots:
        return CapabilityVerdict(
            False, "the reviewers did not use distinct workspace checkout roots", evidence
        )
    return CapabilityVerdict(
        True,
        "two reviewers started together, used isolated workspace checkouts, and batched work",
        evidence,
    )


async def _grade_no_parent_plan(output: CapabilityOutput) -> CapabilityVerdict:
    spawns = tuple(
        call
        for call in output.own_calls
        if call.name == "spawn"
        and isinstance(target := call.input.get("target"), str)
        and target.removeprefix("profile:") == "coding"
        and call.input.get("background") is True
    )
    planning = tuple(
        call
        for call in output.own_calls
        if call.name.startswith(("plan", "objective", "journal", "todo"))
    )
    evidence: JsonObject = {
        "spawn_count": len(spawns),
        "planning_calls": [call.name for call in planning],
    }
    if len(spawns) < 2:
        return CapabilityVerdict(
            False, f"started {len(spawns)} background reviewers, expected at least 2", evidence
        )
    if planning:
        return CapabilityVerdict(
            False, "the parent created durable review planning state", evidence
        )
    return CapabilityVerdict(True, "the parent started review without a plan", evidence)


async def _grade_strict_reviewer_json(output: CapabilityOutput) -> CapabilityVerdict:
    handoffs = output.handoffs
    evidence: JsonObject = {
        "handoff_count": len(handoffs),
        "json_objects": [handoff.result_json_object for handoff in handoffs],
    }
    if len(handoffs) != 2:
        return CapabilityVerdict(
            False, f"recorded {len(handoffs)} reviewer handoffs, expected 2", evidence
        )
    if any(not handoff.result_json_object for handoff in handoffs):
        return CapabilityVerdict(False, "a reviewer added text outside its JSON object", evidence)
    return CapabilityVerdict(True, "both reviewers returned one exact JSON object", evidence)


async def _grade_instruction_reads(output: CapabilityOutput) -> CapabilityVerdict:
    children = _child_calls(output)
    failures = _review_failures(output)
    evidence: JsonObject = {
        "child_count": len(children),
        "checkout_failures": [*failures],
        "instructions": [],
    }
    if failures:
        return CapabilityVerdict(False, "a reviewer failed while reading instructions", evidence)
    instruction_reads: list[Json] = []
    read_flags: list[tuple[bool, bool]] = []
    for calls in children:
        result = "\n".join(call.result for call in calls if call.succeeded)
        root_read = ROOT_INSTRUCTION in result
        nested_read = NESTED_INSTRUCTION in result
        found: JsonObject = {"root": root_read, "nested": nested_read}
        instruction_reads.append(found)
        read_flags.append((root_read, nested_read))
    evidence["instructions"] = instruction_reads
    if len(instruction_reads) != 2:
        return CapabilityVerdict(
            False, f"recorded {len(instruction_reads)} reviewer turns, expected 2", evidence
        )
    if any(not root or not nested for root, nested in read_flags):
        return CapabilityVerdict(
            False, "a reviewer did not read both applicable instruction files", evidence
        )
    return CapabilityVerdict(
        True,
        "both reviewers read the root and nested instruction files without a tool failure",
        evidence,
    )


async def _grade_preemption(output: CapabilityOutput) -> CapabilityVerdict:
    cancels = tuple(call for call in output.own_calls if call.name == "cancel_spawn")
    cancelled = {str(call.input.get("spawn_id", "")) for call in cancels}
    publications = tuple(
        call
        for call in output.own_calls
        if call.name == "call_external_tool"
        and OLD_HEAD_SHA in (encoded := json.dumps(call.input).lower())
        and ("status" in encoded or "review" in encoded)
    )
    cancelled_evidence: list[Json] = [*sorted(cancelled)]
    evidence: JsonObject = {
        "cancelled": cancelled_evidence,
        "old_head_publications": len(publications),
        "cancellations": _call_evidence(output, frozenset(call.call_id for call in cancels)),
    }
    if cancelled != set(OLD_SPAWNS):
        return CapabilityVerdict(
            False, "the new head did not cancel both old-head reviewers", evidence
        )
    if publications:
        return CapabilityVerdict(False, "the old head still received a verdict", evidence)
    return CapabilityVerdict(
        True,
        "the new head cancelled both old-head reviewers and published nothing for it",
        evidence,
    )


def _source_change(page_id: UUID) -> str:
    return (
        "pull_requests: 1 updated. Changed pages (object_get each to read what changed): "
        f"page/{page_id}"
    )


CASES = (
    CapabilityCase(
        name="code-review-two-parallel-reviewers",
        message=_source_change(PAGE_ID),
        grader=DescribedGrader(
            "exactly two background reviewers start in one response and each reviewer batches "
            "known independent repository operations from its own checkout under /workspace",
            _grade_parallel_review,
        ),
        workspace_files=WORKSPACE_FILES,
        seed=_seed_review,
        prepare=_prepare_review,
        wait_for_background=True,
        digest_tag="code-review:two-reviewers:max-parallelism:v1",
    ),
    CapabilityCase(
        name="code-review-reads-applicable-instructions",
        message=_source_change(INSTRUCTION_PAGE_ID),
        grader=DescribedGrader(
            "both reviewers read regular root and nested AGENTS.md files without path or file "
            "type failures",
            _grade_instruction_reads,
        ),
        workspace_files=WORKSPACE_FILES,
        seed=_seed_instruction_read,
        prepare=_prepare_review,
        wait_for_background=True,
        digest_tag="code-review:instruction-files:v1",
    ),
    CapabilityCase(
        name="code-review-starts-without-parent-plan",
        message=_source_change(NO_PLAN_PAGE_ID),
        grader=DescribedGrader(
            "the parent starts exactly two reviewers without a plan, objective, journal, or todo",
            _grade_no_parent_plan,
        ),
        workspace_files=WORKSPACE_FILES,
        seed=_seed_no_plan,
        prepare=_prepare_review,
        digest_tag="code-review:no-parent-plan:v1",
    ),
    CapabilityCase(
        name="code-review-returns-strict-reviewer-json",
        message=_source_change(STRICT_RESULT_PAGE_ID),
        grader=DescribedGrader(
            "both reviewers return exactly one valid review JSON object with no text before or "
            "after it",
            _grade_strict_reviewer_json,
        ),
        workspace_files=WORKSPACE_FILES,
        seed=_seed_strict_result,
        prepare=_prepare_review,
        wait_for_background=True,
        digest_tag="code-review:strict-reviewer-json:v1",
    ),
    CapabilityCase(
        name="code-review-new-head-preempts-reviewers",
        message=_source_change(PREEMPT_PAGE_ID),
        grader=DescribedGrader(
            "a new head cancels both reviewers of the old head and publishes no old-head status",
            _grade_preemption,
        ),
        prior_messages=(
            _source_change(PAGE_ID),
            (
                f"Review of head {OLD_HEAD_SHA} is running in background spawns "
                f"{OLD_SPAWNS[0]} and {OLD_SPAWNS[1]}."
            ),
        ),
        seed=_seed_preemption,
        digest_tag="code-review:new-head-preemption:v1",
    ),
)
