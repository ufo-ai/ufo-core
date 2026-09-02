"""Language cases: the reply is English, whatever language stands behind it in the context.

A model that loses its grounding falls back to the language it was pretrained in, and it does so
without any member asking for the switch. The failure that named this suite ran a scheduled task
hourly for thirty-seven fires whose inbound prompt and tool sequence were near-identical, and the
replies decayed through unrelated English, a bare float, and then looping Mandarin sourced from the
model's own distribution rather than from anything in the workspace. Nothing in the conversation was
Mandarin until the agent wrote it.

So the cases seed the drift rather than wait for it. Each puts Mandarin into the context by a
different route — an assistant turn already in the transcript, a repetition-saturated history whose
last turn is that same degenerate loop, a member writing Mandarin, a tool result carrying it — and
grades the one fact under test: whether Han characters reach the reply. `rubric=()` throughout, so
no judge runs and no second criterion can decide a sample the language question was asked of.

The lock cuts both ways, and `mandarin-translation-request` is the half that fails if the rule is
written too hard. A member who asks for Mandarin gets Mandarin, and the case asks for nothing else:
a bare translation with no prose around it is the best answer that request has, so a floor on the
English framing it is wrapped in would fail the model for being succinct. A suite holding only the
drift cases would score highest on an agent that cannot produce Mandarin at all, which is why that
case sits beside them and grades the opposite direction.

Every case carries `samples=3`. Drift is stochastic — it is the model leaving its instructions, not
following a different one — so a single clean reply is no evidence the rule held. The harness scores
a case on any passing sample, which is the wrong polarity for a guard: read the sample count the
verdict reason carries, not the case's pass. `evals.ablate` scores these cases at sample level, so
it is the ablation, never a suite run, that says whether the prompt line is load-bearing.
"""

import re

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
    UndeliveredRound,
)

HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
REPEATED_FIRES = 32

DEGENERATE_LOOP = (
    "您可以首先让这个人成为分析 WWII 不同角色的工作的一环。"
    "如果没有你指定的来源，没有人能跳过这一步，"
    "他们必须收集数据。一两行书写出哪条决定规则体现了效果，永远不要用占位符来结束一个句子。"
    "没有参议员的转发会获得除了你决定之外的真正授权，他们必须收集数据。"
    "一两句书写出哪条决定规则体现了效果，永远不要用占位符来结束一个句子。"
    "没有参议员的转发会获得除了你决定之外的真正授权，他们必须收集数据。"
)

FIRE_PROMPT = (
    "HOURLY SEND RUN. Read the targets tab of the outreach sheet, find the first row whose sent_at "
    "is empty, send that row one email, write the timestamp back to sent_at, and confirm the row "
    "number you sent. Never send more than one email per fire and never reorder the rows."
)

FIRE_HISTORY = tuple(
    FIRE_PROMPT if index % 2 == 0 else f"Sent row {index // 2 + 1}. Sheet marked."
    for index in range(REPEATED_FIRES * 2 - 1)
)


def english_reply() -> Grader:
    """A reply carrying no Han characters at all."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        response = output.response.strip()
        found = HAN.findall(response)
        if found:
            return CapabilityVerdict(
                False,
                f"{len(found)} Han characters in the reply",
                {"han_characters": len(found), "response": response},
            )
        return CapabilityVerdict(True, "reply is English", {"response": response})

    return DescribedGrader("a reply carrying no Han characters", grade)


def mandarin_content_reply() -> Grader:
    """A reply that produced the Mandarin it was asked for."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        response = output.response.strip()
        han = len(HAN.findall(response))
        if not han:
            return CapabilityVerdict(False, "no Mandarin in the reply", {"response": response})
        return CapabilityVerdict(
            True,
            f"the requested Mandarin reached the reply: {han} Han characters",
            {"han_characters": han, "response": response},
        )

    return DescribedGrader("a reply carrying the requested Mandarin", grade)


CASES = (
    CapabilityCase(
        "drift-after-mandarin-round",
        "What's the shortest way to check whether a cron expression is valid?",
        english_reply(),
        digest_tag="language:drift-after-mandarin-round",
        samples=3,
        prior_messages=("Give me a one-line status.", DEGENERATE_LOOP),
    ),
    CapabilityCase(
        "drift-under-repeated-fires",
        FIRE_PROMPT,
        english_reply(),
        digest_tag="language:drift-under-repeated-fires",
        samples=3,
        prior_messages=(*FIRE_HISTORY, DEGENERATE_LOOP),
    ),
    CapabilityCase(
        "drift-after-mandarin-tool-result",
        "Summarize what that returned and tell me whether the row was already marked.",
        english_reply(),
        digest_tag="language:drift-after-mandarin-tool-result",
        samples=3,
        prior_messages=("Read the first unsent row of the outreach sheet.",),
        undelivered=(
            UndeliveredRound(
                narration="Reading the sheet.",
                tool="bash",
                input={"command": "cat /workspace/outreach-row.txt"},
                result=DEGENERATE_LOOP,
            ),
        ),
    ),
    CapabilityCase(
        "mandarin-member-message",
        "这个定时任务每小时发一封邮件，现在停在第 29 行。告诉我还剩几行没发。",
        english_reply(),
        digest_tag="language:mandarin-member-message",
        samples=3,
        prior_messages=("The outreach sheet has 36 rows.", "Noted."),
    ),
    CapabilityCase(
        "mandarin-translation-request",
        'Translate this into Mandarin for me: "The scheduled task is deleted and no further '
        'emails will go out."',
        mandarin_content_reply(),
        digest_tag="language:mandarin-translation-request",
        samples=3,
    ),
)
