"""A skill the agent wrote, graded by what survived into it and by what its own description then
routes. Each case runs in two phases against one workspace. The authoring phase sends one member
request naming a skill and the rules it must carry, and reads the `skill` object the turn saved:
the rules are anchored on tokens no paraphrase can drop, matched across every file the skill
bundled, one graded case per rule so a dropped rule names itself. The loading phase then puts later
member queries to fresh conversations and watches the mounts — a probe that loads must mount its
own case's skill and none of its siblings, and a probe that does not must reach its terminal with
nothing this suite authored mounted. The description the agent wrote is the only thing routing
those queries, so the second phase grades the first phase's frontmatter the way a member's next
week does.

The phases are a barrier: every skill is saved before any probe runs, so each probe's siblings are
present as the neighbours a description has to be distinct from. A skill the authoring phase did
not save excludes its own probes — the loading question cannot be put to a skill that does not
exist — and the authoring case carries the failure once."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from functools import partial
from itertools import chain
from typing import Protocol, cast
from uuid import UUID

from ufo_ext_skill_create.store import UserSkillStore

from evals.harness.harness import (
    EvalCaseResult,
    EvalReport,
    JsonObject,
    digest_payload,
    infra_owned_fault,
    provider_owned_fault,
)
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
    TargetResult,
    capability_output,
    trajectory_snapshot,
)
from evals.harness.timing import TurnSteps
from ufo.runtime.skills.runtime import SKILL_MD, RuntimeSkill, parse_skill_content
from ufo.sdk.context import ExtensionContext

SUITE = "skill_authoring"
GRADER_REVISION = "authored-then-loaded-instructions-only-2"
PROBE_DEADLINE_SECONDS = 180.0
"""How long the probe's own work may run before the load is called missing. It is charged from the
turn's first durable engine step, so queue wait and sandbox boot no longer eat into it (see
`evals.harness.mounts`), and a probe that never began that work is excluded rather than read as a
clean no-load. The number is unchanged and its meaning is not, so the grader revision moves with it:
this suite's digest changes at this commit, and the sweep's trend line for every `skill_authoring`
trio starts again here."""
DESCRIPTION_OPENING = "Load when"
DESCRIPTION_MAX_WORDS = 50
EXCERPT_MARGIN = 60


@dataclass(frozen=True)
class CriticalInstruction:
    """One rule the member stated and the saved skill must carry, anchored on a token no paraphrase
    drops — an identifier, a threshold, an address, a channel. The match runs case-insensitively
    over the skill's instructions and every file it bundled, so a rule the agent moved into
    `references/` still counts and one it stated only in its frontmatter does not. `label` names
    the rule in the case it grades."""

    label: str
    pattern: str

    def found(self, text: str) -> re.Match[str] | None:
        return re.search(self.pattern, text, re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class LoadProbe:
    """One later member query, put to a conversation that opens after every skill is saved. `loads`
    is the whole verdict: a probe that loads must mount its own case's skill, and one that does not
    must end with nothing this suite authored mounted — the two halves of a description that fires
    on its own intent and on nothing else."""

    name: str
    message: str
    loads: bool = True


@dataclass(frozen=True)
class SkillAuthorCase:
    """One authored skill. `name` is the slug the request names and the skill is saved under, so
    the probes know what to watch for; `must` are the rules the request states and the body has to
    carry; `probes` are the queries its description then has to route.

    The request names every value it asks the skill to carry — the system of record, the reporting
    window, the bands a post uses. One it leaves unstated buys a clarifying question instead of a
    skill, and a case that ends in a question grades whether the agent asks well, which is a
    different eval and takes that skill's rules and probes down with it."""

    name: str
    request: str
    must: tuple[CriticalInstruction, ...]
    probes: tuple[LoadProbe, ...]

    def __post_init__(self) -> None:
        if self.name not in self.request:
            raise ValueError(f"case {self.name!r} does not name its skill in the request")
        labels = tuple(instruction.label for instruction in self.must)
        if len(set(labels)) != len(labels):
            raise ValueError(f"case {self.name!r} repeats a critical-instruction label")
        probes = tuple(probe.name for probe in self.probes)
        if len(set(probes)) != len(probes):
            raise ValueError(f"case {self.name!r} repeats a probe name")
        if not any(probe.loads for probe in self.probes):
            raise ValueError(f"case {self.name!r} has no probe its skill must answer")

    @property
    def saved_case(self) -> str:
        return f"{self.name}:saved"

    def instruction_case(self, instruction: CriticalInstruction) -> str:
        return f"{self.name}:says-{instruction.label}"

    def probe_case(self, probe: LoadProbe) -> str:
        return f"{self.name}:{'loads' if probe.loads else 'declines'}-{probe.name}"

    @property
    def grading(self) -> str:
        rules = ", ".join(instruction.label for instruction in self.must)
        return (
            f"the agent saves a skill named {self.name!r} whose body carries {rules}, and whose "
            f"description mounts it for its own queries and for no other case's"
        )

    def payload(self) -> JsonObject:
        return {
            "name": self.name,
            "request": self.request,
            "must": [
                {"label": instruction.label, "pattern": instruction.pattern}
                for instruction in self.must
            ],
            "probes": [
                {"name": probe.name, "message": probe.message, "loads": probe.loads}
                for probe in self.probes
            ],
        }


def authored_verdict(case: SkillAuthorCase, skill: RuntimeSkill | None) -> tuple[bool, str]:
    """The saved artifact judged as a skill: it exists under the name the member gave, and its
    description is a routing trigger rather than a summary of the workflow — the two properties a
    later turn's `<available_skills>` listing is built out of."""
    if skill is None:
        return False, f"no skill named {case.name!r} was saved for this agent"
    description = " ".join(skill.description.split())
    if not description.startswith(DESCRIPTION_OPENING):
        return False, f"the description does not open {DESCRIPTION_OPENING!r}: {description!r}"
    words = len(description.split())
    if words > DESCRIPTION_MAX_WORDS:
        return False, f"the description runs {words} words, over {DESCRIPTION_MAX_WORDS}"
    return True, f"saved with a {words}-word routing description"


def instruction_verdict(instruction: CriticalInstruction, text: str) -> tuple[bool, str]:
    match = instruction.found(text)
    if match is None:
        return False, f"nothing the skill saved matches /{instruction.pattern}/"
    start = max(match.start() - EXCERPT_MARGIN, 0)
    excerpt = " ".join(text[start : match.end() + EXCERPT_MARGIN].split())
    return True, f"…{excerpt}…"


@dataclass(frozen=True)
class ProbeVerdict:
    """A probe's outcome. `excluded` marks a turn that never made a routing decision to grade — it
    died or was cancelled by something other than this watcher — which is neither a pass nor a
    fail: counting an outage as correct non-routing is how a negative half reads clean on a run
    where no agent ever chose anything."""

    passed: bool
    reason: str
    excluded: bool = False


def probe_verdict(
    case: SkillAuthorCase, probe: LoadProbe, observation: MountObservation
) -> ProbeVerdict:
    """Expected-present wins on a probe that loads: the watch settles on the case's own skill, so a
    sibling reaching the workspace first is a turn still free to correct itself, and only a sibling
    standing alone at the terminal is the confusion two neighbouring descriptions produce. A probe
    that does not load is the mirror — any authored mount fails it, and a clean terminal or the
    deadline passes it. A turn that never began its own work is excluded ahead of both: its empty
    workspace says nothing about routing, and it would otherwise read as a clean no-load pass."""
    if never_started(observation):
        return ProbeVerdict(
            False,
            f"the turn never began its own work (status {observation.status})",
            excluded=True,
        )
    others = tuple(name for name in observation.mounted if name != case.name)
    unclean = observation.status in TERMINAL_STATUSES and observation.status != "done"
    if not probe.loads:
        if observation.mounted:
            return ProbeVerdict(
                False,
                f"mounted {', '.join(observation.mounted)} on a query no authored skill answers",
            )
        if unclean:
            return ProbeVerdict(False, f"turn ended {observation.status}", excluded=True)
        if observation.status in TERMINAL_STATUSES:
            return ProbeVerdict(True, "turn ended done with no authored skill mounted")
        return ProbeVerdict(True, f"no authored skill mounted within {PROBE_DEADLINE_SECONDS:g}s")
    if case.name in observation.mounted:
        alongside = f" (alongside {', '.join(others)})" if others else ""
        return ProbeVerdict(
            True, f"mounted {case.name!r} after {observation.elapsed_seconds:.1f}s{alongside}"
        )
    if unclean:
        return ProbeVerdict(False, f"turn ended {observation.status}", excluded=True)
    if others:
        return ProbeVerdict(False, f"mounted {', '.join(others)} instead of {case.name!r}")
    if observation.status in TERMINAL_STATUSES:
        return ProbeVerdict(False, f"turn ended done without loading {case.name!r}")
    return ProbeVerdict(
        False,
        f"did not load {case.name!r} within {PROBE_DEADLINE_SECONDS:g}s "
        f"(status {observation.status})",
    )


class SkillAuthoringRunTarget(Protocol):
    @property
    def ctx(self) -> ExtensionContext: ...

    @property
    def conversations(self) -> EvalConversations: ...

    @property
    def outcome(self) -> TurnControl: ...

    @property
    def turn_steps(self) -> TurnSteps: ...

    async def step(
        self, conversation_id: UUID, message: str, idempotency_key: str
    ) -> TargetResult: ...


@dataclass(frozen=True)
class Authored:
    """One authoring phase's outcome: the skill the turn saved, if any, and the cases it settled.
    A skill whose description failed its own case is still a skill: it carries into the loading
    phase, because a description that reads wrong is exactly what the probes are there to catch."""

    skill: RuntimeSkill | None
    results: tuple[EvalCaseResult, ...]
    provider_fault: bool = False
    """Whether the authoring turn ended on a fault the provider owns. The probes this trio runs
    later read it, so a skill that never reached the store because the provider dropped the turn
    excludes its probes under that owner instead of as cohort drift."""


def skill_authoring_task(name: str, cases: tuple[SkillAuthorCase, ...]) -> EvalTask:
    """One trio as its own task. The runner is the suite; the name is the trio, so a run selects
    the skills it wants to grade and each trio's report stays comparable across runs."""
    digest = digest_payload(
        {
            "runner": "skill-author-case",
            "task": name,
            "grader": GRADER_REVISION,
            "probeDeadlineSeconds": PROBE_DEADLINE_SECONDS,
            "startDeadlineSeconds": START_DEADLINE_SECONDS,
            "descriptionOpening": DESCRIPTION_OPENING,
            "descriptionMaxWords": DESCRIPTION_MAX_WORDS,
            "cases": [case.payload() for case in cases],
        }
    )
    suite = SkillAuthoringSuite(name=name, cases=cases, digest=digest)
    return EvalTask(name, SUITE, digest, case_names(cases), suite.run, exclusive=True)


def case_names(cases: tuple[SkillAuthorCase, ...]) -> tuple[str, ...]:
    """Every graded case, in the order the report holds them: the authoring phase's whole set
    first, because every one of them settles before the first probe opens a conversation."""
    authoring = tuple(
        name
        for case in cases
        for name in (case.saved_case, *(case.instruction_case(rule) for rule in case.must))
    )
    loading = tuple(case.probe_case(probe) for case in cases for probe in case.probes)
    return (*authoring, *loading)


@dataclass(frozen=True)
class SkillAuthoringSuite:
    name: str
    cases: tuple[SkillAuthorCase, ...]
    digest: str

    async def run(self, target: CapabilityTarget, slots: asyncio.Semaphore) -> EvalReport:
        run_target = cast(SkillAuthoringRunTarget, target)
        await self._clear(run_target)
        try:
            authored = await gather_cases(
                slots, tuple(partial(self._author, case, run_target) for case in self.cases)
            )
            saved = frozenset(
                case.name
                for case, outcome in zip(self.cases, authored, strict=True)
                if outcome.skill is not None
            )
            weather = frozenset(
                case.name
                for case, outcome in zip(self.cases, authored, strict=True)
                if outcome.provider_fault
            )
            probes = await gather_cases(
                slots,
                tuple(
                    partial(self._probe, case, probe, run_target, saved, weather)
                    for case in self.cases
                    for probe in case.probes
                ),
            )
        finally:
            await self._clear(run_target)
        return EvalReport(
            name=self.name,
            suite=SUITE,
            digest=self.digest,
            cases=(*chain.from_iterable(outcome.results for outcome in authored), *probes),
        )

    async def _clear(self, target: SkillAuthoringRunTarget) -> None:
        """Drop this suite's skills from the agent, before it authors them and again once it has
        graded them. Every case creates what it grades instead of finding a previous run's copy,
        and no run leaves a member-authored skill in `<available_skills>` for the next suite to
        route against — the agent is the whole workspace's, not this suite's. Only the names this
        suite writes are removed; a workspace's other saved skills are its own."""
        store = UserSkillStore(target.ctx)
        for case in self.cases:
            await store.delete(case.name)

    async def _author(self, case: SkillAuthorCase, target: SkillAuthoringRunTarget) -> Authored:
        conversation_id = await target.conversations.open(case.name)
        result = await target.step(conversation_id, case.request, f"{case.name}:{conversation_id}")
        skill, parse_error = await self._saved(case, target)
        passed, reason = authored_verdict(case, skill)
        status = result.trajectory.status if result.trajectory is not None else None
        unwritten = skill is None and not result.clean
        excluded = unwritten and infra_owned_fault(
            result.error_class, result.failure_reason, status
        )
        provider = excluded and provider_owned_fault(
            result.error_class, result.failure_reason, result.expiry_status, result.work_started
        )
        if not result.clean and not passed:
            reason = f"{reason} (turn {result.failure_reason})"
        evidence = self._authoring_evidence(case, skill, parse_error, conversation_id, result)
        results = (
            EvalCaseResult(
                name=case.saved_case,
                passed=passed,
                reason=reason,
                evidence=evidence,
                excluded=excluded,
                provider_fault=provider,
            ),
            *self._instruction_results(case, skill, provider),
        )
        return Authored(skill=skill, results=results, provider_fault=provider)

    async def _saved(
        self, case: SkillAuthorCase, target: SkillAuthoringRunTarget
    ) -> tuple[RuntimeSkill | None, str]:
        """The skill the turn saved, or the reason it cannot be read. A stored row the store cannot
        decode is a member-authored artifact this suite grades, never a fault that should take the
        rest of the trio's report down with it — the store itself treats corrupt rows that way."""
        try:
            files = await UserSkillStore(target.ctx).files(case.name)
            if files is None:
                return None, ""
            return parse_skill_content(case.name, files), ""
        except Exception as error:
            return None, f"{type(error).__name__}: {error}"

    def _authoring_evidence(
        self,
        case: SkillAuthorCase,
        skill: RuntimeSkill | None,
        parse_error: str,
        conversation_id: UUID,
        result: TargetResult,
    ) -> JsonObject:
        attempt: JsonObject = {
            "passed": result.clean,
            "reason": result.failure_reason or "turn ended clean",
            "response": result.output.response,
            "calls": [
                {
                    "name": call.name,
                    "input": call.input,
                    "result": call.result,
                    "hasResult": call.has_result,
                    "isError": call.is_error,
                }
                for call in result.output.calls
            ],
        }
        if result.trajectory is not None:
            attempt["trajectory"] = result.trajectory.model_dump(mode="json")
        evidence: JsonObject = {
            "message": case.request,
            "grading": case.grading,
            "skill": case.name,
            "conversationId": str(conversation_id),
            "description": "" if skill is None else skill.description,
            "files": [] if skill is None else [SKILL_MD, *(path for path, _ in skill.files)],
            "attempts": [attempt],
            "selectedAttempt": 0,
        }
        if parse_error:
            evidence["parseError"] = parse_error
        return evidence

    def _instruction_results(
        self, case: SkillAuthorCase, skill: RuntimeSkill | None, provider_fault: bool
    ) -> tuple[EvalCaseResult, ...]:
        """One case per rule the member stated, read over the skill's instructions and every file
        it bundled, so partial credit names which rule the agent dropped. The frontmatter is not
        part of that text: a description naming `#exec-brief` is a routing trigger, not an
        instruction to post there, and a rule an agent stated only in its description is a rule the
        skill does not carry. A skill that was never saved carries its failure on the authoring
        case alone; its rules are excluded, under the owner of the fault that stopped the authoring
        turn."""
        text = (
            ""
            if skill is None
            else "\n".join(
                (
                    skill.instructions,
                    *(content.decode("utf-8", "replace") for _, content in skill.files),
                )
            )
        )
        results: list[EvalCaseResult] = []
        for instruction in case.must:
            evidence: JsonObject = {
                "message": case.request,
                "grading": f"the saved skill states {instruction.label}",
                "pattern": instruction.pattern,
            }
            if skill is None:
                results.append(
                    EvalCaseResult(
                        name=case.instruction_case(instruction),
                        passed=False,
                        reason=f"no skill named {case.name!r} was saved to read",
                        evidence=evidence,
                        excluded=True,
                        provider_fault=provider_fault,
                    )
                )
                continue
            passed, reason = instruction_verdict(instruction, text)
            results.append(
                EvalCaseResult(
                    name=case.instruction_case(instruction),
                    passed=passed,
                    reason=reason,
                    evidence=evidence,
                )
            )
        return tuple(results)

    async def _probe(
        self,
        case: SkillAuthorCase,
        probe: LoadProbe,
        target: SkillAuthoringRunTarget,
        saved: frozenset[str],
        weather: frozenset[str],
    ) -> EvalCaseResult:
        """One later query, watched against every skill this suite saved: the case's own is what a
        loading probe must mount, and the rest are the neighbours it must not be confused with. A
        loading probe settles on its own skill alone, so a sibling reaching the workspace first
        leaves the turn running long enough to correct itself; a declining probe settles on any of
        them, since the first authored mount is already its answer."""
        watch = tuple(item.name for item in self.cases if item.name in saved)
        evidence: JsonObject = {
            "message": probe.message,
            "grading": (
                f"{case.name!r} mounts within {PROBE_DEADLINE_SECONDS:g}s, never a sibling without "
                f"it"
                if probe.loads
                else f"no authored skill mounts within {PROBE_DEADLINE_SECONDS:g}s"
            ),
            "skill": case.name,
            "watched": list(watch),
        }
        if case.name not in saved:
            return EvalCaseResult(
                name=case.probe_case(probe),
                passed=False,
                reason=f"skill {case.name!r} was never saved to route on",
                evidence=evidence,
                excluded=True,
                provider_fault=case.name in weather,
            )
        if not watch:
            return EvalCaseResult(
                name=case.probe_case(probe),
                passed=False,
                reason="the authoring phase saved no skill to watch for",
                evidence=evidence,
                excluded=True,
            )
        conversation_id = await target.conversations.open(f"{case.name}-{probe.name}")
        evidence["conversationId"] = str(conversation_id)
        try:
            turn_id = await target.conversations.admit(
                conversation_id, probe.message, f"{case.name}:{probe.name}:{conversation_id}"
            )
        except Exception as error:
            return EvalCaseResult(
                name=case.probe_case(probe),
                passed=False,
                reason=f"invoke raised: {type(error).__name__}: {error}",
                evidence=evidence,
            )
        observation = await watch_mounts(
            target,
            conversation_id,
            turn_id,
            watch,
            PROBE_DEADLINE_SECONDS,
            settle=(case.name,) if probe.loads else watch,
        )
        verdict = probe_verdict(case, probe, observation)
        evidence["mounted"] = list(observation.mounted)
        evidence["present"] = list(observation.present)
        evidence["status"] = observation.status
        evidence["cancelled"] = observation.cancelled
        evidence["elapsedSeconds"] = round(observation.elapsed_seconds, 1)
        evidence["attempts"] = [
            await self._probe_attempt(verdict, observation, target, conversation_id, turn_id)
        ]
        evidence["selectedAttempt"] = 0
        return EvalCaseResult(
            name=case.probe_case(probe),
            passed=verdict.passed,
            reason=verdict.reason,
            evidence=evidence,
            excluded=verdict.excluded,
        )

    async def _probe_attempt(
        self,
        verdict: ProbeVerdict,
        observation: MountObservation,
        target: SkillAuthoringRunTarget,
        conversation_id: UUID,
        turn_id: UUID,
    ) -> JsonObject:
        """The record of what the agent did instead. A turn that reached its own terminal has a
        durable transcript, so its response and calls read back like a capability attempt; a turn
        this suite cancelled at the mount has none, and the mount evidence is the whole record."""
        attempt: JsonObject = {
            "passed": verdict.passed,
            "reason": verdict.reason,
            "response": "",
            "calls": [],
        }
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
