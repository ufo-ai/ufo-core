from uuid import UUID

from evals.driver import _trajectory_messages
from ufo.harness.models.interface import Message, ToolResultBlock, ToolUseBlock
from ufo.runtime.turns.transcript import Conversation, RecoveryRecord, RolloverRecord

FIRST_TURN = UUID(int=1)
SECOND_TURN = UUID(int=2)
FIRST_INBOUND = Message(
    role="user",
    content=f"<context>\nmessage_ref: {FIRST_TURN}\ntime: now\n</context>\nfirst",
)
SECOND_INBOUND = Message(
    role="user",
    content=f"<context>\nmessage_ref: {SECOND_TURN}\ntime: now\n</context>\nsecond",
)
RECOVERY = Message(role="user", content="<context_rollover>\nContinue.")


def test_a_later_turn_cannot_hide_the_evaluated_turns_terminal_transcript() -> None:
    first = Message(role="assistant", content="first answer")
    second = Message(role="assistant", content="second answer")
    conversation = Conversation(
        seq=2,
        messages=(SECOND_INBOUND, second),
        from_run=True,
    )

    messages = _trajectory_messages(
        (),
        conversation,
        FIRST_TURN,
        1,
        SECOND_TURN,
        (Message(role="user", content="first"), first),
    )

    assert messages == (Message(role="user", content="first"), first)


def test_a_later_turn_cannot_hide_messages_before_the_evaluated_turns_rollover() -> None:
    call = Message(
        role="assistant",
        content=(ToolUseBlock(id="call", name="bash", input={"command": "true"}),),
    )
    result = Message(
        role="user",
        content=(ToolResultBlock(tool_use_id="call", content="done"),),
    )
    record = RolloverRecord(
        index=1,
        before=(FIRST_INBOUND, call, result),
        after=(RECOVERY,),
        recovery=RecoveryRecord(),
    )
    answer = Message(role="assistant", content="finished")
    second = Message(role="assistant", content="second answer")
    conversation = Conversation(
        seq=2,
        messages=(RECOVERY, answer, SECOND_INBOUND, second),
        from_run=True,
    )

    messages = _trajectory_messages((record,), conversation, FIRST_TURN, 1, SECOND_TURN)

    assert messages == (FIRST_INBOUND, call, result, RECOVERY, answer)
