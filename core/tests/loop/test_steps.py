from ufo.harness.models.interface import (
    Message,
    ReasoningItemBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from ufo.runtime.engine import DispatchResult, ImageRef, StreamResult
from ufo.runtime.steps import IMAGE_ATTACHMENT_NOTE, _step_messages


def _one(output: object) -> Message:
    (message,) = _step_messages(output)
    return message


def test_a_model_round_rebuilds_its_assistant_message() -> None:
    message = _one(
        StreamResult(
            reasoning=(ReasoningItemBlock(id="r1", encrypted_content="x", summary=("plan",)),),
            text="checking",
            tool_calls=(ToolUseBlock(id="c1", name="bash", input={"command": "pwd"}),),
        )
    )
    assert message.role == "assistant"
    kinds = [type(block).__name__ for block in message.content]
    assert kinds == ["ReasoningItemBlock", "TextBlock", "ToolUseBlock"]


def test_a_closing_round_with_only_text_rebuilds_the_answer() -> None:
    message = _one(StreamResult(text="all done"))
    assert message.role == "assistant"
    assert message.content == (TextBlock(text="all done"),)


def test_an_errored_round_salvages_its_partial_output() -> None:
    message = _one(
        StreamResult(error_class="ModelResponseTruncated", partial_output="half a thought")
    )
    assert message.role == "assistant"
    assert message.content == "half a thought"


def test_an_errored_round_with_nothing_salvaged_rebuilds_no_message() -> None:
    assert _step_messages(StreamResult(error_class="ProviderError")) == ()


def test_a_dispatch_rebuilds_the_tool_result() -> None:
    message = _one(DispatchResult(tool_use_id="c1", text="/workspace", is_error=False))
    assert message.role == "user"
    (block,) = message.content
    assert isinstance(block, ToolResultBlock)
    assert block.tool_use_id == "c1" and block.content == "/workspace"


def test_a_dispatch_with_images_notes_them_rather_than_rehydrating() -> None:
    message = _one(
        DispatchResult(
            tool_use_id="c1",
            text="see screenshot",
            is_error=False,
            image_refs=(ImageRef(media_type="image/png", blob_key="k"),),
        )
    )
    (block,) = message.content
    assert isinstance(block, ToolResultBlock)
    assert block.content == "see screenshot" + IMAGE_ATTACHMENT_NOTE.format(count=1)


def test_a_compaction_or_arrival_step_rebuilds_no_window() -> None:
    assert _step_messages(((), ())) == ()
    assert _step_messages(None) == ()
