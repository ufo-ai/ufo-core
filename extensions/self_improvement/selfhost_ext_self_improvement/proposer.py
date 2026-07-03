"""The candidate source: rewrite an agent's prompt to better handle a task class it struggled with.
One model leg — render the current prompt and the class's mined examples, ask for a revised prompt
body, return it — or None when the model returns nothing or an unchanged prompt (a no-op the gate
would reject anyway)."""

from dataclasses import dataclass

from selfhost.sdk.models import Message
from selfhost_ext_self_improvement.corpus import EXAMPLE_CHARS, MAX_EXAMPLES, TaskClass
from selfhost_ext_self_improvement.model import ModelLeg

PROPOSER_SYSTEM = (
    "You improve an AI agent's system prompt. You are given the agent's CURRENT system prompt, a "
    "TASK CLASS it handles, and EXAMPLES of turns where that task hit friction (the request and "
    "the problem). Propose a revised system prompt that handles this class more reliably by "
    "addressing the problems with a clearer procedure — WITHOUT narrowing the agent to this one "
    "class or "
    "breaking its other behavior. Preserve the prompt's voice and scope; make the smallest change "
    "that helps. Return ONLY the full revised system prompt, verbatim, no preamble or code fences."
)


@dataclass(frozen=True)
class PromptCandidate:
    task: str
    prompt: str


@dataclass(frozen=True)
class PromptProposer:
    model: ModelLeg

    async def propose(self, current_prompt: str, task_class: TaskClass) -> PromptCandidate | None:
        if not task_class.mine:
            return None
        text = await self.model.complete(
            PROPOSER_SYSTEM,
            (Message(role="user", content=self._prompt(current_prompt, task_class)),),
        )
        body = _clean(text)
        if not body or body == current_prompt.strip():
            return None
        return PromptCandidate(task_class.name, body)

    def _prompt(self, current_prompt: str, task_class: TaskClass) -> str:
        examples = "\n\n".join(
            f"Example {index}:\nRequest: {example.request[:EXAMPLE_CHARS]}\n"
            f"Problem: {example.problem[:EXAMPLE_CHARS]}"
            for index, example in enumerate(task_class.mine[:MAX_EXAMPLES], start=1)
        )
        return (
            f"TASK CLASS: {task_class.name}\n\n"
            f"CURRENT SYSTEM PROMPT:\n{current_prompt}\n\n"
            f"EXAMPLES:\n{examples}\n\n"
            "Return the full revised system prompt now."
        )


def _clean(text: str) -> str:
    body = text.strip()
    if body.startswith("```"):
        lines = body.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        body = "\n".join(lines).strip()
    return body
