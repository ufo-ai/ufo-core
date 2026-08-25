"""Sandbox CLI cases: load the sandbox skill, then use its model and tool bridge commands."""

import re
from typing import Literal

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
)
from evals.harness.scorers import combine, skill_scorer

UFO_LLM = re.compile(r"(?<![\w-])ufo\s+llm(?:\s|$)")
UFO_TOOL_LIST = re.compile(r"(?<![\w-])ufo\s+tool\s+--list(?:\s|$)")
UFO_OBJECT_LIST = re.compile(r"(?<![\w-])ufo\s+tool\s+object_list(?:\s|$)")
SHELL_STEP = re.compile(r"&&|;|\n")


def sandbox_cli_scorer(command: Literal["llm", "tool"]) -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        bash_calls = tuple(call for call in output.calls if call.name == "bash")
        commands = tuple(str(call.input.get("command", "")) for call in bash_calls)
        successful_commands = tuple(
            str(call.input.get("command", ""))
            for call in output.calls
            if call.name == "bash" and call.succeeded
        )
        if command == "llm":
            matched = next(
                (
                    call
                    for call in bash_calls
                    if UFO_LLM.search(str(call.input.get("command", "")))
                    and "SECOND MODEL READY" in str(call.input.get("command", ""))
                    and (call.succeeded or "ufo llm:" in call.result)
                ),
                None,
            )
            if matched is None:
                return CapabilityVerdict(False, "no bash call reached ufo llm")
            return CapabilityVerdict(True, str(matched.input.get("command", "")))
        if any(call.name == "object_list" for call in output.calls):
            return CapabilityVerdict(False, "object_list was invoked as a direct tool")
        steps = tuple(step for line in successful_commands for step in SHELL_STEP.split(line))
        listed = any(UFO_TOOL_LIST.search(step) for step in steps)
        described = any(
            UFO_OBJECT_LIST.search(str(call.input.get("command", "")))
            and "--describe" in str(call.input.get("command", ""))
            and (call.succeeded or "input_schema" in call.result)
            for call in bash_calls
        )
        executed = any(
            UFO_OBJECT_LIST.search(step)
            and "--describe" not in step
            and "kind" in step
            and "agent" in step
            for step in steps
        )
        if not listed or not described or not executed:
            return CapabilityVerdict(
                False,
                f"bridge listed={listed}, described={described}, executed={executed}; "
                f"commands={commands!r}",
            )
        return CapabilityVerdict(
            True,
            "bridge tools were listed before object_list was described and called",
        )

    return DescribedGrader(
        (
            "a bash call reaches ufo llm with the requested prompt"
            if command == "llm"
            else "successful bash calls list bridge tools, then describe and execute object_list "
            "through ufo tool, without a direct object_list call"
        ),
        grade,
    )


CASES = (
    CapabilityCase(
        "sandbox-ufo-llm",
        "From /workspace, use the sandbox's built-in one-shot model command with model "
        "claude-haiku-4-5 and a 32-token limit to ask a second model exactly: 'Reply with SECOND "
        "MODEL READY and nothing else.' Return the second model's output; do not answer the quoted "
        "prompt yourself.",
        combine(
            skill_scorer("sandbox", "website-building"),
            sandbox_cli_scorer("llm"),
        ),
        digest_tag="sandbox-cli:ufo-llm",
    ),
    CapabilityCase(
        "sandbox-ufo-tool",
        "From /workspace, using only bash, discover which tools the sandbox's JSON bridge makes "
        "available, describe object_list, then call it with kind agent and tell me how many "
        "objects it returned. Do not invoke object_list as a direct tool.",
        combine(
            skill_scorer("sandbox", "create-application"),
            sandbox_cli_scorer("tool"),
        ),
        digest_tag="sandbox-cli:ufo-tool",
    ),
)
