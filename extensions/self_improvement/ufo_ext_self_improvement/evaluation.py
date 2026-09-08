"""The gate's evidence: replay each held-out task under both prompt arms, judge the regenerated
answers, and score the acceptance lift. `absent` re-runs the agent's current prompt, `present` the
candidate — same task, same archived tool results, same judge, so the only difference is the prompt
body. A candidate passes when its present acceptance clears the absent acceptance by a
statistically-bounded margin on its mined class (local) AND does not regress the agent's other task
classes (global) — the two-stage lift gate."""

import json
from dataclasses import dataclass
from pathlib import Path

from ufo.sdk.models import Message
from ufo_ext_self_improvement.corpus import TaskExample
from ufo_ext_self_improvement.gate import GateVerdict, OutcomeLabel, two_stage_gate
from ufo_ext_self_improvement.model import ModelLeg, ReplayLeg
from ufo_ext_self_improvement.replay import REPLAY_ROUND_LIMIT, ReplayEvaluation


@dataclass(frozen=True)
class CandidateEvaluation:
    replay_model: ReplayLeg
    judge: ModelLeg
    round_limit: int = REPLAY_ROUND_LIMIT

    async def evaluate(
        self,
        candidate_prompt: str,
        current_prompt: str,
        local_held_out: tuple[TaskExample, ...],
        global_held_out: tuple[TaskExample, ...] = (),
    ) -> GateVerdict:
        local = await self._labels(candidate_prompt, current_prompt, local_held_out)
        outer = await self._labels(candidate_prompt, current_prompt, global_held_out)
        return two_stage_gate(local, outer)

    async def _labels(
        self, candidate_prompt: str, current_prompt: str, held_out: tuple[TaskExample, ...]
    ) -> tuple[OutcomeLabel, ...]:
        replay = ReplayEvaluation(self.replay_model, self.round_limit)
        labels: list[OutcomeLabel] = []
        for example in held_out:
            for present, prompt in ((False, current_prompt), (True, candidate_prompt)):
                result = await replay.replay(example.messages, prompt)
                accepted = await self._accepts(example.request, result.final_text)
                labels.append(OutcomeLabel(present=present, success=accepted))
        return tuple(labels)

    async def _accepts(self, request: str, answer: str) -> bool:
        verdict = await self.judge.complete(
            GRADER_SYSTEM,
            (Message(role="user", content=f"REQUEST:\n{request}\n\nANSWER:\n{answer}"),),
        )
        start, end = verdict.find("{"), verdict.rfind("}")
        if start == -1 or end <= start:
            return False
        try:
            parsed = json.loads(verdict[start : end + 1])
        except json.JSONDecodeError:
            return False
        return isinstance(parsed, dict) and parsed.get("accepted") is True


GRADER_SYSTEM = (Path(__file__).parent / "prompts" / "grader.md").read_text().strip()
