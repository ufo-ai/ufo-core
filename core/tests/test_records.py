from uuid import uuid4

import pytest
from pydantic import ValidationError

from selfhost.schema.records import TerminalFrame, Turn, ledger_id_for, turn_id_for


def test_turn_id_deterministic() -> None:
    workspace_id, conversation_id = uuid4(), uuid4()
    assert turn_id_for(workspace_id, conversation_id, 1) == turn_id_for(
        workspace_id, conversation_id, 1
    )
    assert turn_id_for(workspace_id, conversation_id, 1) != turn_id_for(
        workspace_id, conversation_id, 2
    )


def test_ledger_id_deterministic() -> None:
    workspace_id, turn_id = uuid4(), uuid4()
    assert ledger_id_for(workspace_id, turn_id, "tokens") == ledger_id_for(
        workspace_id, turn_id, "tokens"
    )


def _turn(status: str, terminal: TerminalFrame | None) -> Turn:
    return Turn(
        id=uuid4(),
        workspace_id=uuid4(),
        conversation_id=uuid4(),
        agent_id=uuid4(),
        seq=1,
        status=status,
        inbound="hello",
        terminal=terminal,
    )


def test_running_turn_has_no_terminal() -> None:
    assert _turn("running", None).terminal is None
    with pytest.raises(ValidationError):
        _turn("running", TerminalFrame(status="done"))


def test_terminal_turn_requires_matching_frame() -> None:
    assert _turn("done", TerminalFrame(status="done")).status == "done"
    with pytest.raises(ValidationError):
        _turn("done", None)
    with pytest.raises(ValidationError):
        _turn("done", TerminalFrame(status="failed"))
