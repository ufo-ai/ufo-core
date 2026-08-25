"""Code-review orchestration over a local pull-request fixture."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import sqlalchemy as sa

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    WorkspaceFile,
)
from evals.harness.harness import Json, JsonObject
from ufo.blob import WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.schema import tables

BASE_SHA = "08871cfcd60a9c407221af81ff22411754224c7e"
HEAD_SHA = "cc7a893a45200cd4d7f51e0a1c227207f9c04375"
OLD_HEAD_SHA = "1111111111111111111111111111111111111111"
PAGE_ID = UUID("20000000-0000-0000-0000-000000000001")
PREEMPT_PAGE_ID = UUID("20000000-0000-0000-0000-000000000002")
SOURCE_ID = UUID("30000000-0000-0000-0000-000000000001")
PREEMPT_SOURCE_ID = UUID("30000000-0000-0000-0000-000000000002")
OLD_SPAWNS = (
    "40000000-0000-0000-0000-000000000001",
    "40000000-0000-0000-0000-000000000002",
)
FIXTURE_FILES = ("alpha.py", "beta.py", "gamma.py", "delta.py", "epsilon.py", "zeta.py")
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
    WorkspaceFile("review-target/AGENTS.md", b"Review all changed Python files.\n"),
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
    calls = {call.call_id: call for call in output.calls}
    child_batches: list[Json] = []
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
    if len(child_batches) != 2:
        return CapabilityVerdict(
            False, f"recorded {len(child_batches)} reviewer turns, expected 2", evidence
        )
    return CapabilityVerdict(
        True, "two reviewers started together and each batched repository work", evidence
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
            "known independent repository operations",
            _grade_parallel_review,
        ),
        workspace_files=WORKSPACE_FILES,
        seed=_seed_review,
        prepare=_prepare_review,
        wait_for_background=True,
        digest_tag="code-review:two-reviewers:max-parallelism:v1",
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
