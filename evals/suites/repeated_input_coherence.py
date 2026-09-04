"""One completion escapes a repeated failed-action loop in scheduled history."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from ufo_ext_scheduled_tasks.runner import REPORT_INSTRUCTION

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
    WorkspaceFile,
)
from evals.harness.harness import Json, JsonObject
from evals.suites.slack_message_block import inspect
from ufo.sdk.models import Message, TextBlock, ToolResultBlock, ToolUseBlock

OLD_TASK_TURNS = 32
OLD_MESSAGES_PER_TURN = 6
OLD_TOOLS_PER_TURN = 2
REPEATED_TURNS = 50
REPEATED_MESSAGES_PER_TURN = 8
REPEATED_TOOLS_PER_TURN = 3
FAILED_CORRECTION_TURNS = 20
FAILED_CORRECTION_MESSAGES_PER_TURN = 4
PAYLOAD_RECORDS = 30
OLD_TASK_MARKERS = ("project cedar", "launch brief", "draft section")
STATUS_FILES = tuple(f"status/source-{stage}.json" for stage in range(1, 4))
CURRENT_STATUS_FILE = "status/current.json"
CURRENT_COMMAND = "cd /workspace/status && cat current.json"
KNOWN_BAD_COMMAND = "pwd && cat current.json"
SCHEDULE_PROMPT = (
    "Run these commands separately and in order: `pwd && cat "
    "/workspace/status/source-1.json`, `pwd && cat /workspace/status/source-2.json`, then `pwd "
    "&& cat /workspace/status/source-3.json`. Do not use a loop or glob. Report their result "
    "value."
)
CURRENT_PROMPT = f"Run exactly `{CURRENT_COMMAND}`, then report its result value."
START = datetime(2026, 8, 3, 9, tzinfo=UTC)


@dataclass(frozen=True)
class HistoryCase:
    name: str
    repeated_reply: str
    current_reply: str
    required_fragments: tuple[str, ...]


HISTORY_CASES = (
    HistoryCase(
        name="pull_requests_failed_correction_history",
        repeated_reply="No new pull requests to review.",
        current_reply="1 new pull request from Ada.",
        required_fragments=("new pull request", "ada"),
    ),
    HistoryCase(
        name="emails_failed_correction_history",
        repeated_reply="No new emails.",
        current_reply="1 new email from Ada.",
        required_fragments=("new email", "ada"),
    ),
    HistoryCase(
        name="clean_check_failed_correction_history",
        repeated_reply="Check clean.",
        current_reply="Check failed: 1 changed file.",
        required_fragments=("check failed", "changed file"),
    ),
)


def _tool_payload(domain: str, stage: int, result: str) -> str:
    return json.dumps(
        {
            "source": "synthetic-history",
            "domain": domain,
            "run": 0,
            "stage": stage,
            "result": result,
            "records": [
                {
                    "id": f"synthetic-{domain}-{stage}-{record:02d}",
                    "state": "unchanged",
                    "summary": (
                        "Synthetic historical status record with no member data, no credential, "
                        "and no actionable update."
                    ),
                }
                for record in range(PAYLOAD_RECORDS)
            ],
        },
        separators=(",", ":"),
    )


def _scheduled_inbound(index: int, *, current: bool) -> str:
    fired_at = START + timedelta(hours=index)
    prompt = CURRENT_PROMPT if current else SCHEDULE_PROMPT
    return (
        "<context>\n"
        f"message_ref: {UUID(int=index + 2)}\n"
        f"time: {fired_at.strftime('%A %Y-%m-%d %H:%M UTC')}\n"
        "</context>\n"
        "<scheduled_task>\n"
        f"scheduled_fire: {fired_at.isoformat().replace('+00:00', 'Z')}\n"
        "</scheduled_task>\n"
        f"{prompt}\n"
        "<scheduled_task_instruction>\n"
        f"{REPORT_INSTRUCTION}\n"
        "</scheduled_task_instruction>"
    )


def _history(case: HistoryCase) -> tuple[Message, ...]:
    messages: list[Message] = []
    for index in range(OLD_TASK_TURNS):
        first_call = f"old-{index}-1"
        second_call = f"old-{index}-2"
        messages.extend(
            (
                Message(
                    role="user",
                    content=(
                        "<context>\n"
                        f"message_ref: old-{index:03d}\n"
                        "</context>\n"
                        f"Continue section {index + 1} of the Project Cedar launch brief."
                    ),
                ),
                Message(
                    role="assistant",
                    content=(
                        TextBlock(text="I will inspect the synthetic research notes."),
                        ToolUseBlock(
                            id=first_call,
                            name="bash",
                            input={"command": f"read synthetic cedar notes {index}"},
                        ),
                    ),
                ),
                Message(
                    role="user",
                    content=(
                        ToolResultBlock(
                            tool_use_id=first_call,
                            content=_tool_payload(
                                "cedar-notes",
                                1,
                                f"Project Cedar draft section {index + 1}",
                            ),
                        ),
                    ),
                ),
                Message(
                    role="assistant",
                    content=(
                        TextBlock(text="I will inspect the synthetic planning record."),
                        ToolUseBlock(
                            id=second_call,
                            name="bash",
                            input={"command": f"read synthetic cedar plan {index}"},
                        ),
                    ),
                ),
                Message(
                    role="user",
                    content=(
                        ToolResultBlock(
                            tool_use_id=second_call,
                            content=_tool_payload(
                                "cedar-plan",
                                2,
                                f"Project Cedar launch brief section {index + 1}",
                            ),
                        ),
                    ),
                ),
                Message(
                    role="assistant",
                    content=(
                        f"Project Cedar draft section {index + 1}: synthetic planning notes saved."
                    ),
                ),
            )
        )
    messages.extend(
        (
            Message(
                role="user",
                content=(
                    "<context>\n"
                    f"message_ref: {UUID(int=1)}\n"
                    "time: Monday 2026-08-03 08:59 UTC\n"
                    "sender: Eval Member\n"
                    "</context>\n"
                    "Read the three status files every hour and report their result value."
                ),
            ),
            Message(role="assistant", content="Scheduled."),
        )
    )
    for index in range(REPEATED_TURNS):
        messages.append(Message(role="user", content=_scheduled_inbound(index, current=False)))
        for stage, path in enumerate(STATUS_FILES, 1):
            call_id = f"scheduled-{case.name}-{index}-{stage}"
            messages.extend(
                (
                    Message(
                        role="assistant",
                        content=(
                            TextBlock(text=f"I will inspect synthetic status source {stage}."),
                            ToolUseBlock(
                                id=call_id,
                                name="bash",
                                input={"command": f"pwd && cat /workspace/{path}"},
                            ),
                        ),
                    ),
                    Message(
                        role="user",
                        content=(
                            ToolResultBlock(
                                tool_use_id=call_id,
                                content=_tool_payload(
                                    case.name,
                                    stage,
                                    case.repeated_reply,
                                ),
                            ),
                        ),
                    ),
                )
            )
        messages.append(Message(role="assistant", content=case.repeated_reply))
    for index in range(FAILED_CORRECTION_TURNS):
        call_id = f"correction-{case.name}-{index}"
        messages.extend(
            (
                Message(
                    role="user",
                    content=_scheduled_inbound(REPEATED_TURNS + index, current=True),
                ),
                Message(
                    role="assistant",
                    content=(
                        TextBlock(text=f"I will run `{CURRENT_COMMAND}` exactly."),
                        ToolUseBlock(
                            id=call_id,
                            name="bash",
                            input={"command": KNOWN_BAD_COMMAND},
                        ),
                    ),
                ),
                Message(
                    role="user",
                    content=(
                        ToolResultBlock(
                            tool_use_id=call_id,
                            content="cat: current.json: No such file or directory",
                            is_error=True,
                        ),
                    ),
                ),
                Message(
                    role="assistant",
                    content=(
                        "The command omitted the required directory change. I will correct it on "
                        "the next run."
                    ),
                ),
            )
        )
    return tuple(messages)


def _coherence_grader(case: HistoryCase) -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        text = output.response.strip()
        word_count = len(text.split())
        failures = []
        missing_fragments = tuple(
            fragment for fragment in case.required_fragments if fragment not in text.casefold()
        )
        if missing_fragments:
            failures.append(f'did not report the current result "{case.current_reply}"')
        if not text:
            failures.append("returned an empty reply")
        replayed = tuple(marker for marker in OLD_TASK_MARKERS if marker in text.casefold())
        if replayed:
            failures.append("replayed the unrelated old task: " + ", ".join(replayed))
        lines = tuple(line.strip().casefold() for line in text.splitlines() if line.strip())
        if any(lines.count(line) > 1 for line in lines):
            failures.append("repeated an output line")
        failures.extend(inspect(text).failures)
        unexpected_tools = tuple(
            call.name
            for call in output.own_calls
            if call.name not in {"bash", "write", "share_file"}
        )
        if unexpected_tools:
            failures.append("used an unexpected tool: " + ", ".join(unexpected_tools))
        status_calls = tuple(
            call
            for call in output.own_calls
            if call.name == "bash"
            and CURRENT_STATUS_FILE.rsplit("/", 1)[-1] in str(call.input.get("command"))
        )
        repeated_bad_action = bool(
            status_calls and status_calls[0].input.get("command") == KNOWN_BAD_COMMAND
        )
        if repeated_bad_action:
            failures.append("repeated the known-bad command as its first action")
        current_reads = tuple(call for call in status_calls if call.succeeded)
        if not current_reads:
            failures.append(f'did not read "{CURRENT_STATUS_FILE}" successfully')
        defects: list[Json] = [*failures]
        evidence: JsonObject = {
            "repeatedTurns": REPEATED_TURNS,
            "failedCorrectionTurns": FAILED_CORRECTION_TURNS,
            "historyMessages": (
                OLD_TASK_TURNS * OLD_MESSAGES_PER_TURN
                + 2
                + REPEATED_TURNS * REPEATED_MESSAGES_PER_TURN
                + FAILED_CORRECTION_TURNS * FAILED_CORRECTION_MESSAGES_PER_TURN
            ),
            "historyToolCalls": (
                OLD_TASK_TURNS * OLD_TOOLS_PER_TURN
                + REPEATED_TURNS * REPEATED_TOOLS_PER_TURN
                + FAILED_CORRECTION_TURNS
            ),
            "newToolCalls": len(output.own_calls),
            "knownBadFirstAction": repeated_bad_action,
            "wordCount": word_count,
            "defects": defects,
        }
        if failures:
            return CapabilityVerdict(False, "; ".join(failures), evidence)
        return CapabilityVerdict(
            True,
            "the completion escaped the repeated failed-action loop",
            evidence,
        )

    return DescribedGrader(
        "the completion does not repeat the known-bad first action, reads the current status file, "
        "reports its result, replays no unrelated old task, and contains no transcript "
        "continuation, repeated-sentence loop, or language drift",
        grade,
    )


def _case(case: HistoryCase) -> CapabilityCase:
    return CapabilityCase(
        name=case.name,
        message=_scheduled_inbound(
            REPEATED_TURNS + FAILED_CORRECTION_TURNS,
            current=True,
        ),
        grader=_coherence_grader(case),
        digest_tag=(f"repeated-failed-scheduled-action:{REPEATED_TURNS}:{FAILED_CORRECTION_TURNS}"),
        workspace_files=(
            *(
                WorkspaceFile(
                    path=path,
                    content=(_tool_payload(case.name, stage, case.repeated_reply).encode() + b"\n"),
                )
                for stage, path in enumerate(STATUS_FILES, 1)
            ),
            WorkspaceFile(
                path=CURRENT_STATUS_FILE,
                content=(_tool_payload(case.name, 4, case.current_reply).encode() + b"\n"),
            ),
        ),
        prior_transcript=_history(case),
    )


CASES = tuple(_case(case) for case in HISTORY_CASES)
