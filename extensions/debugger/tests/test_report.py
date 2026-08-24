import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from ufo_ext_debugger import manifest as debugger_manifest
from ufo_ext_debugger.report import (
    ERROR_CLASS_MAX_CHARS,
    PROBLEM_REPORTED_EVENT,
    REPORT_PROBLEM_TOOL,
    ReportProblemInput,
    report_problem,
)

from ufo.blob import FilesystemBlobStore
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import conversation_audience
from ufo.tools.context import SpawnResult, ToolContext
from ufo.workspace import ws

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


async def test_a_report_names_the_workspace_and_links_to_the_turn_that_made_it(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    workspace_id = uuid4()
    member_id = uuid4()
    ctx = _context(workspace_id, tmp_path, member_id=member_id)
    with ws(workspace_id), caplog.at_level(logging.WARNING, logger="ufo"):
        result = await report_problem(
            ctx,
            ReportProblemInput(
                origin="fault",
                user_description="reporting the broken Gmail connection",
                symptom="the gmail connection returns 401 on every call",
                next_action="reconnect the account or check the broker's token refresh",
                object_ref="connection/gmail",
                error_class="HTTPStatusError 401",
            ),
        )
    reported = _reported(caplog)
    assert reported["workspace_id"] == str(workspace_id)
    assert reported["origin"] == "fault"
    assert reported["symptom"] == "the gmail connection returns 401 on every call"
    assert reported["object_ref"] == "connection/gmail"
    assert reported["error_class"] == "HTTPStatusError 401"
    assert reported["next_action"] == "reconnect the account or check the broker's token refresh"
    assert reported["member_id"] == str(member_id)
    assert reported["debug_url"] == (
        f"https://fleet.example.com/surface/debug?ws={workspace_id}"
        f"&c={ctx.turn.conversation_id}&t={ctx.turn.id}"
    )
    assert "operators" in result.content[0].text


async def test_a_member_request_reports_under_its_own_origin(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    workspace_id = uuid4()
    with ws(workspace_id), caplog.at_level(logging.WARNING, logger="ufo"):
        await report_problem(
            _context(workspace_id, tmp_path),
            ReportProblemInput(
                origin="member_request",
                user_description="reporting the empty daily brief",
                symptom="the daily brief has been empty for three days",
                next_action="read the brief job's last three runs",
            ),
        )
    reported = _reported(caplog)
    assert reported["origin"] == "member_request"
    assert reported["object_ref"] is None
    assert reported["error_class"] is None
    assert reported["member_id"] is None


async def test_a_deploy_that_publishes_no_base_url_reports_the_ids_alone(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    workspace_id = uuid4()
    ctx = _context(workspace_id, tmp_path, public_base_url=None)
    with ws(workspace_id), caplog.at_level(logging.WARNING, logger="ufo"):
        await report_problem(
            ctx,
            ReportProblemInput(
                origin="fault",
                user_description="reporting the empty Slack token slot",
                symptom="the slack_bot_token slot is empty",
                next_action="ask the workspace admin to refill the slot",
            ),
        )
    reported = _reported(caplog)
    assert reported["debug_url"] is None
    assert reported["turn_id"] == str(ctx.turn.id)
    assert reported["conversation_id"] == str(ctx.turn.conversation_id)
    assert reported["agent_id"] == str(ctx.turn.agent_id)


def test_an_error_class_carries_neither_a_dump_nor_a_url() -> None:
    """The two bounds that keep a sandbox's own output — and the signed run token its environment
    echoes in `HTTP_PROXY` — out of the record and out of the alert body."""
    for over_bound in ("x" * (ERROR_CLASS_MAX_CHARS + 1), "https://tok3n:@proxy.example:443"):
        with pytest.raises(ValidationError):
            ReportProblemInput(
                origin="fault",
                user_description="reporting a failing stream",
                symptom="a stream fails on every run",
                next_action="read the stream's last run",
                error_class=over_bound,
            )


def test_the_manifest_declares_the_tool_and_its_two_origins() -> None:
    (tool,) = debugger_manifest.manifest().tools
    assert tool.name == REPORT_PROBLEM_TOOL
    assert tool.parallel_safe is True
    assert tool.side_effecting is False
    properties = tool.input_model.model_json_schema()["properties"]
    assert properties["origin"]["enum"] == ["fault", "member_request"]
    assert set(properties) >= {"symptom", "next_action", "object_ref", "error_class"}
    assert tool.input_model.model_fields["user_description"].is_required()
