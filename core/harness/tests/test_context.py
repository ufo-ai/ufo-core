from dataclasses import dataclass

from ufo.harness.context import ContextWindow, is_context_overflow


@dataclass(frozen=True)
class Message:
    role: str
    text: str
    opaque: int = 0
    images: int = 0


def window(**overrides: int) -> ContextWindow[Message]:
    values = {
        "context_tokens": 100,
        "reserve_tokens": 10,
        "buffer_tokens": 20,
        "chars_per_token": 2,
        "image_tokens": 8,
        **overrides,
    }
    return ContextWindow(
        role=lambda message: message.role,
        text=lambda message: message.text,
        opaque_chars=lambda message: message.opaque,
        image_count=lambda message: message.images,
        **values,
    )


def test_the_line_is_the_window_less_the_reserve_and_the_buffer_unless_pinned() -> None:
    assert window().trigger == 70
    assert window(trigger_tokens=20).trigger == 20


def test_tokens_count_role_text_opaque_chars_and_images() -> None:
    messages = (Message("user", "abcdefghij", opaque=10, images=1),) * 3

    assert window().tokens(messages) == 60


def test_context_overflow_detection_uses_class_and_message() -> None:
    assert is_context_overflow(RuntimeError("prompt is too large"))
    assert not is_context_overflow(RuntimeError("network closed"))
