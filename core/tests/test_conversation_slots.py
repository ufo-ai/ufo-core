from typing import cast

import pytest
from pydantic import BaseModel, ValidationError

from ufo.ext.conversation_slots import (
    CONVERSATION_CHANGES_MAX,
    ChangesSlotPayload,
    ConversationChange,
    ConversationSlotContext,
    ConversationSlotProvider,
)
from ufo.ext.manifest import Manifest, conversation_slot_declarations


async def _summary(_ctx: ConversationSlotContext) -> int:
    return 0


async def _read(_ctx: ConversationSlotContext) -> ChangesSlotPayload:
    return ChangesSlotPayload(changes=(), truncated=False)


def _provider(slot_id: str = "changes") -> ConversationSlotProvider:
    return ConversationSlotProvider(
        id=slot_id,
        label="Changes",
        icon="diff",
        content=ChangesSlotPayload,
        summarize=_summary,
        read=_read,
    )


def test_conversation_slot_declarations_preserve_manifest_order() -> None:
    first = Manifest(name="first", version="1", conversation_slots=(_provider("changes"),))
    second = Manifest(name="second", version="1", conversation_slots=(_provider("files"),))

    assert conversation_slot_declarations((first, second)) == (
        (first, first.conversation_slots[0]),
        (second, second.conversation_slots[0]),
    )


@pytest.mark.parametrize("slot_id", ["", "Changes", "two words", "_hidden", "x" * 65])
def test_conversation_slot_declarations_reject_invalid_ids(slot_id: str) -> None:
    manifest = Manifest(name="bad", version="1", conversation_slots=(_provider(slot_id),))

    with pytest.raises(RuntimeError, match="invalid conversation slot id"):
        conversation_slot_declarations((manifest,))


def test_conversation_slot_declarations_reject_duplicate_ids() -> None:
    first = Manifest(name="first", version="1", conversation_slots=(_provider(),))
    second = Manifest(name="second", version="1", conversation_slots=(_provider(),))

    with pytest.raises(RuntimeError, match="two extensions register conversation slot"):
        conversation_slot_declarations((first, second))


def test_conversation_slot_declarations_reject_unknown_payloads() -> None:
    class UnknownPayload(BaseModel):
        type: str = "unknown"

    provider = ConversationSlotProvider(
        id="unknown",
        label="Unknown",
        icon="artifact",
        content=cast(type[ChangesSlotPayload], UnknownPayload),
        summarize=_summary,
        read=_read,
    )
    manifest = Manifest(name="bad", version="1", conversation_slots=(provider,))

    with pytest.raises(RuntimeError, match="unsupported payload"):
        conversation_slot_declarations((manifest,))


def test_changes_payload_bounds_its_collection() -> None:
    change = ConversationChange(path="file.py", patch="+new", truncated=False)

    with pytest.raises(ValidationError, match="too_long"):
        ChangesSlotPayload(
            changes=(change,) * (CONVERSATION_CHANGES_MAX + 1),
            truncated=True,
        )
