import pytest

from ufo.harness.models.interface import (
    ImageBlock,
    ImageSource,
    Message,
    ReasoningItemBlock,
    RedactedThinkingBlock,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from ufo.runtime.engine import _from_harness_message, _to_harness_message


@pytest.mark.parametrize(
    "message",
    [
        Message(role="user", content="hello"),
        Message(
            role="assistant",
            content=(
                TextBlock(text="text"),
                ImageBlock(source=ImageSource(media_type="image/png", data="aW1hZ2U=")),
                ThinkingBlock(thinking="reason", signature="signature"),
                RedactedThinkingBlock(data="cmVkYWN0ZWQ="),
                ReasoningItemBlock(
                    id="reasoning",
                    encrypted_content="ZW5jcnlwdGVk",
                    summary=("summary",),
                ),
                ToolUseBlock(id="call", name="inspect", input={"path": "file.txt"}),
                ToolResultBlock(
                    tool_use_id="call",
                    content=(
                        TextBlock(text="result"),
                        ImageBlock(source=ImageSource(media_type="image/jpeg", data="cmVzdWx0")),
                    ),
                    is_error=True,
                    activity=True,
                    activity_text="Inspecting file.txt",
                ),
            ),
        ),
    ],
)
def test_runtime_message_round_trips_through_the_harness_boundary(message: Message) -> None:
    assert _from_harness_message(_to_harness_message(message)) == message
