"""Skill loading probed live and settled early: each case sends one natural task, watches completed
`load_skill` steps, and decides at the first observed load — cancelling the still-running turn, so
a case costs the rounds before the load instead of the full task. A cancelled turn grades the same
as a finished one; a turn that ends or stalls without the expected load fails at its terminal or
the deadline.
A turn that reaches its own terminal also records its response, calls, and redacted trajectory,
so the archive shows what the agent did instead of loading. A case whose expected skill is not
loadable under the active pack or its own seeded corpus is excluded, not failed.

Member-tier cases seed their corpus onto the case's workspace through the real member save path
(`UserSkillStore.save`, the same write `create-skill` lands), so the saved rows, card columns, and
per-turn projection are exactly what a member save produces. Skills are workspace-scoped: every
case starts from a wiped member tier and a seeding case wipes again after itself, since a leftover
fixture would join the next case's corpus and falsify its regime. Seeding cases run one at a time
after the unseeded cases, and a suite containing any becomes exclusive — a seeded corpus is
visible to every concurrent turn of the workspace.

Block ablation is a stack property, not a case property: `[skills] member_block` in the serve
config renders or suppresses member-skill visibility — the turn-message block and the small-corpus
prompt fold alike — so the `-block-on` and `-block-off` twins of each bias case are two runs of
the eval stack. Run the `-block-on` cases against the default config, set `member_block = false`
under `[skills]`, restart serve, and run the `-block-off` twins — `python -m evals --only
skill_loading_member` per arm. The runner reads the arm from the same deploy config serve boots
from and excludes the mismatched twins, so a run can never record a case against the wrong arm; the
tag
joins each case's payload, so the two arms record under different digests and read as a pass-rate
delta on otherwise identical inputs."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from functools import partial
from hashlib import sha256
from typing import Protocol, cast
from uuid import UUID

import sqlalchemy as sa
from ufo_ext_skill_create.store import (
    MAX_USER_SKILLS_PER_WORKSPACE,
    UserSkillStore,
    user_skill,
)

from evals.harness.capability import WorkspaceFile
from evals.harness.harness import EvalCaseResult, EvalReport, JsonObject, digest_payload
from evals.harness.memory_fence import forget_workspace_memory
from evals.harness.mounts import (
    START_DEADLINE_SECONDS,
    TERMINAL_STATUSES,
    MountObservation,
    TurnControl,
    never_started,
    watch_mounts,
)
from evals.harness.registry import EvalTask, gather_cases
from evals.harness.target import (
    CapabilityTarget,
    EvalConversations,
    capability_output,
    trajectory_snapshot,
)
from evals.harness.timing import TurnSteps
from ufo.agent_scope import agent_current
from ufo.config import load_config
from ufo.db import workspace_tx
from ufo.sdk.context import ExtensionContext
from ufo.skills.runtime import SKILL_MD

SUITE = "skill_loading"
"""The suite label both skill-loading tasks register under. It is the label the runner keys on, not
the task name: the member task registers as `skill_loading_member`, and while that name was also its
suite the shard built no loadable-skill set for it and the suite raised on its first line, taking
every finished suite of the shard with it (nightly 2026-08-21, shard 4)."""
GRADER_REVISION = "skill-verdict-6"
LOAD_DEADLINE_SECONDS = 120.0
"""How long the turn's own work may run before the load is called missing. It is charged from the
turn's first durable engine step, so queue wait and sandbox boot no longer eat into it (see
`evals.harness.mounts`). The number is unchanged and its meaning is not, so the grader revision
moves with it: this suite's digest changes at this commit, and the sweep's trend line for
`skill_loading` and `skill_loading_member` starts again here."""
ABLATION_TAGS = frozenset({"", "block-on", "block-off"})
REGIMES = frozenset({"", "fold", "names", "retrieval", "tail"})


@dataclass(frozen=True)
class SkillFixture:
    """One member-authored skill seeded for a case: the routing-card fields plus a minimal body.
    `pinned` is the member's always-show mark on the saved row; `depends` lands in the frontmatter
    exactly where an authored skill declares it."""

    name: str
    description: str
    body: str
    pinned: bool = False
    depends: tuple[str, ...] = ()

    def skill_md(self) -> bytes:
        lines = ["---", f"name: {self.name}", f"description: {json.dumps(self.description)}"]
        if self.depends:
            lines.append("metadata:")
            lines.append(f"  depends: [{', '.join(self.depends)}]")
        lines.extend(["---", "", self.body, ""])
        return "\n".join(lines).encode()


def fixtures_digest(fixtures: tuple[SkillFixture, ...]) -> str:
    """A corpus's content digest — it joins the case payload, so the run digest moves whenever a
    seeded card, body, pin, or dependency changes."""
    return digest_payload(
        {
            "fixtures": [
                [item.name, item.description, item.body, item.pinned, list(item.depends)]
                for item in fixtures
            ]
        }
    )


async def seed_member_skills(
    ctx: ExtensionContext,
    fixtures: tuple[SkillFixture, ...],
    registry_names: frozenset[str],
) -> None:
    """Save fixtures to the bound workspace through the real member save path, pins included, so
    the rows and card columns are exactly what a member save writes. `registry_names` is the deploy
    tier — a fixture colliding with a pack skill fails the seed."""
    store = UserSkillStore(ctx=ctx)
    for fixture in fixtures:
        await store.save(
            fixture.name, {SKILL_MD: fixture.skill_md()}, registry_names, pinned=fixture.pinned
        )


async def forget_workspace_skills() -> None:
    """Delete every user skill of the bound workspace — the skill sibling of
    `forget_workspace_memory`. A fixture an earlier case left behind would join the next case's
    corpus and falsify the regime its assertions were built on."""
    scope = agent_current()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.delete(user_skill).where(user_skill.c.workspace_id == scope.workspace_id)
        )


@dataclass(frozen=True)
class SkillLoadCase:
    """One natural member query whose first skill load must be `expected` — an exact registry
    name; loading a child mounts its parent too, so a parent name is satisfied by any of its
    children and a child's own parent must never be forbidden. `forbidden` names the plausible
    wrong picks — a forbidden mount fails the case only while the expected skill is absent.
    `workspace_files` stage the artifacts the query names, so the agent routes instead of asking
    for a missing file.

    `member_skills` seed the case's corpus before its conversation begins; `late_skills` are saved
    only after `prelude_message`'s turn settles, so a freshness case proves a skill saved
    mid-conversation routes on the very next turn, before any index job could have run.
    `expects_no_load` inverts the verdict: the turn must reach its own terminal without mounting
    any forbidden skill. `ablation` tags a bias case for the stack configuration it runs against
    (see the module docstring); `regime` records the corpus regime its author asserted at
    collection time. Both join the payload, so either moves the digest."""

    name: str
    message: str
    expected: str = ""
    forbidden: tuple[str, ...] = ()
    workspace_files: tuple[WorkspaceFile, ...] = ()
    member_skills: tuple[SkillFixture, ...] = ()
    late_skills: tuple[SkillFixture, ...] = ()
    prelude_message: str = ""
    expects_no_load: bool = False
    ablation: str = ""
    regime: str = ""

    def __post_init__(self) -> None:
        if self.expects_no_load:
            if self.expected:
                raise ValueError(f"case {self.name!r}: expects_no_load takes no expected skill")
            if not self.forbidden:
                raise ValueError(f"case {self.name!r}: expects_no_load needs forbidden names")
        elif not self.expected:
            raise ValueError(f"case {self.name!r} names no expected skill")
        if self.late_skills and not self.prelude_message:
            raise ValueError(f"case {self.name!r}: late_skills need a prelude_message to follow")
        if self.ablation not in ABLATION_TAGS:
            raise ValueError(f"case {self.name!r}: unknown ablation tag {self.ablation!r}")
        if self.regime not in REGIMES:
            raise ValueError(f"case {self.name!r}: unknown regime {self.regime!r}")
        names = [fixture.name for fixture in self.seeds]
        if len(names) != len(set(names)):
            raise ValueError(f"case {self.name!r} seeds duplicate skill names")

    @property
    def seeds(self) -> tuple[SkillFixture, ...]:
        return (*self.member_skills, *self.late_skills)

    @property
    def grading(self) -> str:
        if self.expects_no_load:
            return (
                f"the turn reaches its own terminal within {LOAD_DEADLINE_SECONDS:g}s without "
                f"mounting {', '.join(map(repr, self.forbidden))}"
            )
        statement = f"skill {self.expected!r} mounts within {LOAD_DEADLINE_SECONDS:g}s"
        if self.forbidden:
            statement += f", never {', '.join(map(repr, self.forbidden))} without it"
        if any(name.startswith(f"{self.expected}/") for name in self.forbidden):
            statement += " (a forbidden child of it, never at all)"
        return statement

    def payload(self) -> JsonObject:
        payload: JsonObject = {
            "name": self.name,
            "message": self.message,
            "expected": self.expected,
            "forbidden": list(self.forbidden),
        }
        if self.workspace_files:
            payload["workspaceFiles"] = [
                {"path": item.path, "sha256": sha256(item.content).hexdigest()}
                for item in self.workspace_files
            ]
        if self.member_skills:
            payload["memberSkills"] = {
                "count": len(self.member_skills),
                "digest": fixtures_digest(self.member_skills),
            }
        if self.late_skills:
            payload["lateSkills"] = {
                "count": len(self.late_skills),
                "digest": fixtures_digest(self.late_skills),
            }
        if self.prelude_message:
            payload["preludeMessage"] = self.prelude_message
        if self.expects_no_load:
            payload["expectsNoLoad"] = True
        if self.ablation:
            payload["ablation"] = self.ablation
        if self.regime:
            payload["regime"] = self.regime
        return payload


def skill_load_verdict(case: SkillLoadCase, observation: MountObservation) -> tuple[bool, str]:
    """Expected-present wins: a forbidden mount fails the case only when the expected skill has
    not mounted — one agentic round can load several skills at once, and a companion grabbed
    alongside the right pick is not a routing miss. The exception is a forbidden CHILD of the
    expected skill: its mount pulls the parent in with it, so the parent's presence is implied by
    the wrong pick rather than evidence of routing, and the case fails.

    An `expects_no_load` case inverts the question: any watched mount fails it immediately, and it
    passes only when the turn reaches its own terminal with none — a turn still running at the
    deadline is a fail, never a pass, because it may yet load.

    A turn that never began its own work is neither: the rig held it in the queue or in setup for
    the whole start budget, so it is reported as the rig's fault and the caller excludes it."""
    if never_started(observation):
        return False, (
            f"the turn had not begun its own work {observation.elapsed_seconds:.0f}s after "
            f"admission (status {observation.status}); the {LOAD_DEADLINE_SECONDS:g}s load "
            "deadline never started"
        )
    if case.expects_no_load:
        loaded = [name for name in case.forbidden if name in observation.mounted]
        if loaded:
            return False, f"mounted {', '.join(loaded)} where no skill load was warranted"
        if observation.status in TERMINAL_STATUSES:
            return True, (
                f"ended {observation.status} after {observation.charged_seconds:.1f}s without "
                "mounting any watched skill"
            )
        return False, (
            f"still running at the deadline (status {observation.status}); "
            "no-load is only proven by the turn's own terminal"
        )
    loaded_forbidden = [name for name in case.forbidden if name in observation.mounted]
    implied_by = [name for name in loaded_forbidden if name.startswith(f"{case.expected}/")]
    if implied_by:
        return False, (
            f"mounted forbidden child skill(s) of {case.expected!r}: {', '.join(implied_by)}"
        )
    if case.expected in observation.mounted:
        alongside = f" (alongside {', '.join(loaded_forbidden)})" if loaded_forbidden else ""
        return (
            True,
            f"mounted {case.expected!r} after {observation.charged_seconds:.1f}s{alongside}",
        )
    if loaded_forbidden:
        return False, (
            f"mounted forbidden skill(s) without {case.expected!r}: {', '.join(loaded_forbidden)}"
        )
    if observation.status in TERMINAL_STATUSES:
        return False, f"turn ended {observation.status} without loading {case.expected!r}"
    return False, (
        f"did not load {case.expected!r} within {LOAD_DEADLINE_SECONDS:g}s "
        f"(status {observation.status})"
    )


class SkillLoadRunTarget(Protocol):
    @property
    def ctx(self) -> ExtensionContext: ...

    @property
    def conversations(self) -> EvalConversations: ...

    @property
    def outcome(self) -> TurnControl: ...

    @property
    def turn_steps(self) -> TurnSteps: ...

    @property
    def loadable_skills(self) -> frozenset[str] | None: ...


def skill_loading_task(
    cases: tuple[SkillLoadCase, ...],
    name: str = "skill_loading",
    packs: tuple[str, ...] = (),
) -> EvalTask:
    """One skill-loading suite under its own task name and digest. The pack cases and the
    member-seeding cases register as separate tasks at the seeding boundary: a seeded corpus is
    agent-global, so a task carrying any seeding case is exclusive and its cases cost a full shard
    slot each — splitting there keeps the pack cases concurrent and each task inside a nightly
    shard's weight ceiling.

    Both tasks register under the shared `SUITE` label. The name is the task's own; the label is
    what the runner and the run's setup key on, so neither task can be the one the setup skips.

    The task narrows to named cases, so `--case` and an ablation's `cases` can measure a handful of
    them instead of the whole catalog. A narrowing rebuilds the suite from the kept cases, so its
    digest covers that subset and its exclusivity follows the seeds the subset keeps."""
    digest = digest_payload(
        {
            "runner": "skill-load-case",
            "task": name,
            "grader": GRADER_REVISION,
            "deadlineSeconds": LOAD_DEADLINE_SECONDS,
            "startDeadlineSeconds": START_DEADLINE_SECONDS,
            "cases": [case.payload() for case in cases],
        }
    )
    suite = SkillLoadingSuite(cases=cases, digest=digest, name=name)

    def narrow(names: tuple[str, ...]) -> EvalTask:
        return skill_loading_task(
            tuple(case for case in cases if case.name in names), name=name, packs=packs
        )

    return EvalTask(
        name,
        SUITE,
        digest,
        tuple(case.name for case in cases),
        suite.run,
        exclusive=any(case.seeds for case in cases),
        packs=packs,
        narrow=narrow,
    )


@dataclass(frozen=True)
class SkillLoadingSuite:
    cases: tuple[SkillLoadCase, ...]
    digest: str
    name: str = SUITE

    async def run(self, target: CapabilityTarget, slots: asyncio.Semaphore) -> EvalReport:
        run_target = cast(SkillLoadRunTarget, target)
        loadable = run_target.loadable_skills
        if loadable is None:
            raise RuntimeError(f"the {self.name} suite requires the pack's loadable-skill set")
        arm = "block-on" if load_config().skills.member_block else "block-off"
        direct = tuple(case for case in self.cases if not case.seeds)
        seeding = tuple(case for case in self.cases if case.seeds)
        settled = await gather_cases(
            slots,
            tuple(partial(self._gated_case, case, run_target, loadable, arm) for case in direct),
        )
        by_name = dict(zip((case.name for case in direct), settled, strict=True))
        for case in seeding:
            async with slots:
                by_name[case.name] = await self._gated_case(case, run_target, loadable, arm)
        return EvalReport(
            name=self.name,
            suite=SUITE,
            digest=self.digest,
            cases=tuple(by_name[case.name] for case in self.cases),
        )

    async def _gated_case(
        self,
        case: SkillLoadCase,
        target: SkillLoadRunTarget,
        loadable: frozenset[str],
        arm: str,
    ) -> EvalCaseResult:
        if case.ablation and case.ablation != arm:
            return EvalCaseResult(
                name=case.name,
                passed=False,
                reason=(
                    f"requires the {case.ablation} stack; the deploy config runs the {arm} arm "
                    f"([skills] member_block)"
                ),
                evidence=self._evidence(case, None, None),
                excluded=True,
            )
        if len(case.seeds) > MAX_USER_SKILLS_PER_WORKSPACE:
            return EvalCaseResult(
                name=case.name,
                passed=False,
                reason=(
                    f"corpus of {len(case.seeds)} exceeds the live cap "
                    f"MAX_USER_SKILLS_PER_WORKSPACE={MAX_USER_SKILLS_PER_WORKSPACE}"
                ),
                evidence=self._evidence(case, None, None),
                excluded=True,
            )
        reachable = loadable | frozenset(fixture.name for fixture in case.seeds)
        if not case.expects_no_load and case.expected not in reachable:
            return EvalCaseResult(
                name=case.name,
                passed=False,
                reason=(
                    f"skill {case.expected!r} is not loadable under the active pack "
                    "or the case's seeded corpus"
                ),
                evidence=self._evidence(case, None, None),
                excluded=True,
            )
        return await self._case(case, target, loadable)

    async def _case(
        self,
        case: SkillLoadCase,
        target: SkillLoadRunTarget,
        loadable: frozenset[str],
    ) -> EvalCaseResult:
        await forget_workspace_memory()
        await forget_workspace_skills()
        conversation_id = await target.conversations.open(
            case.name, workspace_files=case.workspace_files
        )
        try:
            try:
                if case.member_skills:
                    await seed_member_skills(target.ctx, case.member_skills, loadable)
                if case.prelude_message:
                    await self._prelude(case, target, conversation_id)
                if case.late_skills:
                    await seed_member_skills(target.ctx, case.late_skills, loadable)
                turn_id = await target.conversations.admit(
                    conversation_id, case.message, f"{case.name}:{conversation_id}"
                )
            except Exception as error:
                return EvalCaseResult(
                    name=case.name,
                    passed=False,
                    reason=f"invoke raised: {type(error).__name__}: {error}",
                    evidence=self._evidence(case, None, conversation_id),
                )
            watched = case.forbidden if case.expects_no_load else (case.expected, *case.forbidden)
            observation = await watch_mounts(
                target, conversation_id, turn_id, watched, LOAD_DEADLINE_SECONDS
            )
            passed, reason = skill_load_verdict(case, observation)
            evidence = self._evidence(case, observation, conversation_id)
            evidence["attempts"] = [
                await self._attempt(passed, reason, observation, target, conversation_id, turn_id)
            ]
            evidence["selectedAttempt"] = 0
            return EvalCaseResult(
                name=case.name,
                passed=passed,
                reason=reason,
                evidence=evidence,
                excluded=never_started(observation),
            )
        finally:
            if case.seeds:
                await forget_workspace_skills()

    async def _prelude(
        self, case: SkillLoadCase, target: SkillLoadRunTarget, conversation_id: UUID
    ) -> None:
        """Run the conversation's first turn to its own terminal, so what a freshness case saves
        afterward is a skill entering a conversation already underway."""
        prelude_id = await target.conversations.admit(
            conversation_id, case.prelude_message, f"{case.name}:prelude:{conversation_id}"
        )
        settled = await target.outcome.settle(conversation_id, prelude_id)
        if settled is None:
            raise RuntimeError("prelude turn did not settle before the seeding step")

    def _evidence(
        self,
        case: SkillLoadCase,
        observation: MountObservation | None,
        conversation_id: UUID | None,
    ) -> JsonObject:
        evidence: JsonObject = {
            "message": case.message,
            "grading": case.grading,
            "expected": case.expected,
            "forbidden": list(case.forbidden),
            "workspaceFiles": [item.path for item in case.workspace_files],
            "conversationId": None if conversation_id is None else str(conversation_id),
        }
        if case.seeds:
            evidence["memberSkills"] = {
                "count": len(case.seeds),
                "digest": fixtures_digest(case.seeds),
            }
        if case.expects_no_load:
            evidence["expectsNoLoad"] = True
        if case.ablation:
            evidence["ablation"] = case.ablation
        if case.regime:
            evidence["regime"] = case.regime
        if observation is not None:
            evidence["mounted"] = list(observation.mounted)
            evidence["present"] = list(observation.present)
            evidence["status"] = observation.status
            evidence["cancelled"] = observation.cancelled
            evidence["elapsedSeconds"] = round(observation.elapsed_seconds, 1)
            evidence["chargedSeconds"] = round(observation.charged_seconds, 1)
            evidence["startupSeconds"] = (
                None
                if observation.startup_seconds is None
                else round(observation.startup_seconds, 1)
            )
        return evidence

    async def _attempt(
        self,
        passed: bool,
        reason: str,
        observation: MountObservation,
        target: SkillLoadRunTarget,
        conversation_id: UUID,
        turn_id: UUID,
    ) -> JsonObject:
        """The viewer-shaped record of the one run. A turn that reached its own terminal has a
        durable transcript, so its response, calls, and redacted trajectory record like a
        capability attempt; a turn this suite cancelled has none, and the mount evidence is the
        whole record."""
        attempt: JsonObject = {"passed": passed, "reason": reason, "response": "", "calls": []}
        if observation.cancelled:
            return attempt
        trajectory = await target.outcome.settle(conversation_id, turn_id)
        if trajectory is None:
            return attempt
        output = capability_output(trajectory.messages)
        snapshot = trajectory_snapshot(
            conversation_id, turn_id, observation.status, trajectory.messages
        )
        attempt["response"] = output.response
        attempt["calls"] = [
            {
                "name": call.name,
                "input": call.input,
                "result": call.result,
                "hasResult": call.has_result,
                "isError": call.is_error,
            }
            for call in output.calls
        ]
        attempt["trajectory"] = snapshot.model_dump(mode="json")
        return attempt
