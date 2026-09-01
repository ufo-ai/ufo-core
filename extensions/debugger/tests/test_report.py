import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from ufo_ext_debugger.report import (
    PROBLEM_MAX_CHARS,
    PROBLEM_REPORTED_EVENT,
    ReportProblemInput,
    report_problem,
)

from ufo.blob import FilesystemBlobStore
from ufo.runtime.tools.context import SpawnResult, ToolContext
from ufo.runtime.workspace import ws
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import conversation_audience

BASE_URL = "https://fleet.example.com/"


@dataclass
class _NoSandbox:
    """The report reaches the telemetry pipeline alone, so the context's sandbox is a stand-in the
    handler must never call."""


async def _unavailable_spawn(profile: str, payload: dict, background: bool = False) -> SpawnResult:
    raise AssertionError("report_problem must not spawn")


def _context(
    workspace_id: UUID,
    tmp_path: Path,
    public_base_url: str | None = BASE_URL,
    member_id: UUID | None = None,
) -> ToolContext:
    turn = Turn(
        id=uuid4(),
        workspace_id=workspace_id,
        conversation_id=uuid4(),
        agent_id=uuid4(),
        seq=0,
        status="running",
        inbound="the brief is empty again",
        created_at=datetime(2026, 8, 23, tzinfo=UTC),
    )
    return ToolContext(
        sandbox=_NoSandbox(),
        blob=FilesystemBlobStore(root=tmp_path),
        turn=turn,
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=member_id,
        audience=conversation_audience(member_id),
        artifact_token_secret="",
        public_base_url=public_base_url,
    )


def _reported(caplog: pytest.LogCaptureFixture) -> dict[str, object]:
    (record,) = [
        record for record in caplog.records if record.getMessage() == PROBLEM_REPORTED_EVENT
    ]
    return record.ufo


async def test_a_report_carries_its_category_and_impact_and_links_to_the_turn_that_made_it(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    workspace_id = uuid4()
    member_id = uuid4()
    ctx = _context(workspace_id, tmp_path, member_id=member_id)
    with ws(workspace_id), caplog.at_level(logging.WARNING, logger="ufo"):
        result = await report_problem(
            ctx,
            ReportProblemInput(
                problem=(
                    "I ran the daily brief build three times and every run ended on a 401 from "
                    "the gmail connection; I expected the last seven days of mail."
                ),
                category="external_connector",
                impact="major",
                origin="fault",
            ),
        )
    reported = _reported(caplog)
    assert reported["workspace_id"] == str(workspace_id)
    assert reported["category"] == "external_connector"
    assert reported["impact"] == "major"
    assert reported["origin"] == "fault"
    assert reported["problem"].startswith("I ran the daily brief build three times")
    assert reported["member_id"] == str(member_id)
    assert reported["debug_url"] == (
        f"https://fleet.example.com/surface/debug?ws={workspace_id}"
        f"&c={ctx.turn.conversation_id}&t={ctx.turn.id}"
    )
    assert "engineers" in result.content[0].text


async def test_a_member_request_reports_under_its_own_origin(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    workspace_id = uuid4()
    with ws(workspace_id), caplog.at_level(logging.WARNING, logger="ufo"):
        await report_problem(
            _context(workspace_id, tmp_path),
            ReportProblemInput(
                problem=(
                    "A member asked for this to be reported: the daily brief has arrived empty "
                    "for three days and they have stopped opening it."
                ),
                category="cron_task",
                impact="minor",
                origin="member_request",
            ),
        )
    reported = _reported(caplog)
    assert reported["origin"] == "member_request"
    assert "member_id" not in reported


async def test_a_deploy_that_publishes_no_base_url_reports_the_ids_alone(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    workspace_id = uuid4()
    ctx = _context(workspace_id, tmp_path, public_base_url=None)
    with ws(workspace_id), caplog.at_level(logging.WARNING, logger="ufo"):
        await report_problem(
            ctx,
            ReportProblemInput(
                problem=(
                    "Every send from this workspace fails because the slack_bot_token slot is "
                    "empty; I expected the slot to hold the workspace's bot token."
                ),
                category="external_connector",
                impact="critical",
                origin="fault",
            ),
        )
    reported = _reported(caplog)
    assert "debug_url" not in reported
    assert reported["turn_id"] == str(ctx.turn.id)
    assert reported["conversation_id"] == str(ctx.turn.conversation_id)
    assert reported["agent_id"] == str(ctx.turn.agent_id)


def test_the_problem_carries_neither_a_dump_nor_a_credential() -> None:
    """The two bounds that keep a sandbox's own output — and the signed run token its environment
    echoes in `HTTP_PROXY` — out of the record."""
    for over_bound in (
        "x" * (PROBLEM_MAX_CHARS + 1),
        "the sandbox echoed HTTP_PROXY=http://tok3n:@proxy.example:443 and then died",
    ):
        with pytest.raises(ValidationError):
            ReportProblemInput(
                problem=over_bound,
                category="sandbox_runtime",
                impact="major",
                origin="fault",
            )


def test_a_problem_may_name_the_host_that_stopped_answering() -> None:
    """The refusal is of a credential, not of a URL: a site that stopped answering is named."""
    accepted = ReportProblemInput(
        problem="https://shop.example.com has returned 502 on every request for twenty minutes.",
        category="asset_site",
        impact="major",
        origin="fault",
    )
    assert accepted.category == "asset_site"
