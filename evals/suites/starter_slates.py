"""Starter-slate cases: grade the requests generated for the two member-facing slates."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from functools import partial
from typing import cast
from uuid import NAMESPACE_URL, uuid5

from ufo_ext_web.starters import (
    AUTOMATIONS_SLATE,
    STARTERS_SLATE,
    SlatePrompt,
    StarterCache,
)

from evals.harness.harness import EvalCaseResult, EvalReport, Json, JsonObject, digest_payload
from evals.harness.judge import JUDGE_REVISION, rubric_pass
from evals.harness.registry import EvalTask, gather_cases
from evals.harness.target import CapabilityTarget, InProcessTarget

STARTER_SLATE_JUDGE_MODEL = "gpt-5.4-mini"
STARTER_ASK_PREFIX = "Build an application that"
REQUEST_RUBRIC = (
    "Every ask starts directly with an imperative verb. It does not start with a first-person "
    "subject such as I or we, a question, a greeting, or a polite preface.",
    "Every ask names the concrete work of its own row and reads as a complete request the member "
    "can send without editing.",
    "No ask tells the member to connect, authorize, or grant an account.",
)
AUTOMATION_SLATE_RUBRIC = (
    *REQUEST_RUBRIC,
    "Every ask states when the work runs, by schedule, frequency, or event.",
)


@dataclass(frozen=True)
class _SlateCase:
    name: str
    kind: str
    recalled: tuple[str, ...]
    rubric: tuple[str, ...]

    def _payload(self) -> JsonObject:
        return {
            "name": self.name,
            "kind": self.kind,
            "recalled": list(self.recalled),
            "rubric": list(self.rubric),
        }


FOUNDER_MEMORY = (
    "I run Northstar, a payments product for support teams at software companies.",
    "Each Monday I review Stripe revenue, QuickBooks cash, and overdue customer invoices.",
    "I triage the shared Gmail inbox each morning and follow up with inbound sales leads.",
    "The engineering team merges changes in GitHub and posts shipped work in Slack.",
    "I send investors a monthly update on runway, product releases, and customer growth.",
    "I track three competitors and write a short market note when their pricing changes.",
)
PRODUCT_MEMORY = (
    "I lead product at Halcyon, a B2B service used by hospital operations teams.",
    "Support themes come from Zendesk each week and become Linear issues when they repeat.",
    "I review GitHub delivery progress and post the sprint report in Slack every Friday.",
    "Customer calls are in Google Calendar, and their decisions are filed in Notion.",
    "I check Google Search Console and a Google Sheet for signup funnel changes each Monday.",
    "I write release notes and the customer announcement after each product release.",
)

CASES = (
    _SlateCase("starter-founder", "starters", FOUNDER_MEMORY, REQUEST_RUBRIC),
    _SlateCase("starter-product", "starters", PRODUCT_MEMORY, REQUEST_RUBRIC),
    _SlateCase(
        "automation-founder",
        "automations",
        FOUNDER_MEMORY,
        AUTOMATION_SLATE_RUBRIC,
    ),
    _SlateCase(
        "automation-product",
        "automations",
        PRODUCT_MEMORY,
        AUTOMATION_SLATE_RUBRIC,
    ),
)


def _prompt(kind: str) -> SlatePrompt:
    match kind:
        case "starters":
            return STARTERS_SLATE
        case "automations":
            return AUTOMATIONS_SLATE
        case _:
            raise ValueError(f"unknown slate kind {kind!r}")


async def _run_case(case: _SlateCase, target: CapabilityTarget) -> EvalCaseResult:
    local = cast("InProcessTarget", target)
    model = local.ctx.model
    if model is None:
        raise RuntimeError("starter slate eval requires model access")
    prompt = _prompt(case.kind)
    slate = await StarterCache(
        store=local.ctx.store,
        member_id=uuid5(NAMESPACE_URL, f"ufo:eval:{case.name}"),
        agents=(),
        recalled=case.recalled,
        model=model,
        solvent=True,
        prompt=prompt,
    ).read()
    if slate is None:
        return EvalCaseResult(
            name=case.name,
            passed=False,
            reason="the surface produced no slate",
            evidence={"kind": case.kind},
        )
    slate_payload = cast("JsonObject", slate.model_dump(mode="json"))
    answer = json.dumps(slate.model_dump(mode="json"), ensure_ascii=False)
    if case.kind == "starters" and any(
        not entry.ask.startswith(STARTER_ASK_PREFIX) for entry in slate.ranked
    ):
        return EvalCaseResult(
            name=case.name,
            passed=False,
            reason=f"a starter ask did not start {STARTER_ASK_PREFIX!r}",
            evidence={"kind": case.kind, "slate": slate_payload},
        )
    if case.kind == "automations" and slate.check_in is not None:
        return EvalCaseResult(
            name=case.name,
            passed=False,
            reason="the automation slate included a check-in",
            evidence={"kind": case.kind, "slate": slate_payload},
        )
    if target.judge is None:
        raise RuntimeError("starter slate eval requires a judge")
    verdict = await rubric_pass(prompt.system, answer, case.rubric, target.judge)
    judge: list[Json] = []
    for item in verdict.criteria:
        judge.append(
            {
                "criterion": item.criterion,
                "passed": item.passed,
                "reason": item.reason,
            }
        )
    return EvalCaseResult(
        name=case.name,
        passed=verdict.passed,
        reason=verdict.reason,
        evidence={
            "kind": case.kind,
            "rankedCount": len(slate.ranked),
            "slate": slate_payload,
            "judge": judge,
        },
    )


def starter_slates_task(cases: tuple[_SlateCase, ...] = CASES) -> EvalTask:
    digest = digest_payload(
        {
            "runner": "starter-slates",
            "task": "starter_slates",
            "cases": [case._payload() for case in cases],
            "judgeModel": STARTER_SLATE_JUDGE_MODEL,
            "judgeRevision": JUDGE_REVISION,
        }
    )

    async def run(target: CapabilityTarget, slots: asyncio.Semaphore) -> EvalReport:
        results = await gather_cases(
            slots,
            tuple(partial(_run_case, case, target) for case in cases),
        )
        return EvalReport(
            name="starter_slates",
            suite="starter_slates",
            digest=digest,
            cases=results,
        )

    return EvalTask(
        name="starter_slates",
        suite="starter_slates",
        digest=digest,
        cases=tuple(case.name for case in cases),
        run=run,
        judge_model=STARTER_SLATE_JUDGE_MODEL,
        judge_revision=JUDGE_REVISION,
        nightly=False,
        narrow=lambda names: starter_slates_task(
            tuple(case for case in cases if case.name in names)
        ),
    )
