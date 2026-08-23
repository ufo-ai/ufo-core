from datetime import UTC, datetime
from typing import cast

import pytest
from pydantic import BaseModel, ValidationError

from ufo.ext.conversation_slots import (
    CONVERSATION_ARTIFACTS_MAX,
    CONVERSATION_TASKS_MAX,
    ArtifactsSlotPayload,
    ConversationArtifact,
    ConversationSlotContext,
    ConversationSlotProvider,
    ConversationSource,
    ConversationTask,
    ImagePreview,
    TasksSlotPayload,
)
from ufo.ext.manifest import Manifest, conversation_slot_declarations
from ufo.media.image_previews import raster_image_media_type
from ufo.turns.workspace_changes import WORKSPACE_CHANGES_MAX, WorkspaceChange, WorkspaceChanges


async def _summary(_ctx: ConversationSlotContext) -> int:
    return 0


async def _read(_ctx: ConversationSlotContext) -> WorkspaceChanges:
    return WorkspaceChanges(changes=(), truncated=False)


def _provider(slot_id: str = "changes") -> ConversationSlotProvider:
    return ConversationSlotProvider(
        id=slot_id,
        label="Changes",
        icon="diff",
        content=WorkspaceChanges,
        summarize=_summary,
        read=_read,
    )


def test_conversation_slot_declarations_preserve_manifest_order() -> None:
    first = Manifest(name="first", version="1", conversation_slots=(_provider("changes"),))
    second = Manifest(name="second", version="1", conversation_slots=(_provider("artifacts"),))

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
        content=cast(type[WorkspaceChanges], UnknownPayload),
        summarize=_summary,
        read=_read,
    )
    manifest = Manifest(name="bad", version="1", conversation_slots=(provider,))

    with pytest.raises(RuntimeError, match="unsupported payload"):
        conversation_slot_declarations((manifest,))


def test_changes_payload_bounds_its_collection() -> None:
    change = WorkspaceChange(path="file.py", patch="+new", truncated=False)

    with pytest.raises(ValidationError, match="too_long"):
        WorkspaceChanges(
            changes=(change,) * (WORKSPACE_CHANGES_MAX + 1),
            truncated=True,
        )


def test_artifacts_payload_bounds_its_collection() -> None:
    artifact = ConversationArtifact(
        filename="report.pdf",
        subject="Quarterly report",
        media_type="application/pdf",
        size_bytes=42,
        created_at=datetime(2026, 8, 6, tzinfo=UTC),
        url="https://ufo.example/artifacts/report.pdf",
        preview=None,
    )

    with pytest.raises(ValidationError, match="too_long"):
        ArtifactsSlotPayload(
            artifacts=(artifact,) * (CONVERSATION_ARTIFACTS_MAX + 1),
            truncated=True,
        )


def test_tasks_payload_bounds_its_collection() -> None:
    task = ConversationTask(description="ship", status="pending")

    with pytest.raises(ValidationError, match="too_long"):
        TasksSlotPayload(
            title="Launch",
            tasks=(task,) * (CONVERSATION_TASKS_MAX + 1),
            total_count=CONVERSATION_TASKS_MAX + 1,
            completed_count=0,
            truncated=True,
        )


def test_tasks_payload_rejects_impossible_progress() -> None:
    with pytest.raises(ValidationError, match="completed task count exceeds"):
        TasksSlotPayload(
            title="Launch",
            tasks=(),
            total_count=1,
            completed_count=2,
            truncated=False,
        )

    with pytest.raises(ValidationError, match="visible task count exceeds"):
        TasksSlotPayload(
            title="Launch",
            tasks=(ConversationTask(description="build"),) * 2,
            total_count=1,
            completed_count=0,
            truncated=True,
        )

    with pytest.raises(ValidationError, match="untruncated task count"):
        TasksSlotPayload(
            title="Launch",
            tasks=(ConversationTask(description="build"),),
            total_count=2,
            completed_count=0,
            truncated=False,
        )

    with pytest.raises(ValidationError, match="visible completed tasks exceed"):
        TasksSlotPayload(
            title="Launch",
            tasks=(ConversationTask(description="build", status="completed"),),
            total_count=2,
            completed_count=0,
            truncated=True,
        )

    with pytest.raises(ValidationError, match="visible incomplete tasks exceed"):
        TasksSlotPayload(
            title="Launch",
            tasks=(ConversationTask(description="build", status="pending"),),
            total_count=1,
            completed_count=1,
            truncated=True,
        )


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "file:///etc/passwd", "https://user:secret@example.com"],
)
def test_conversation_artifacts_reject_unsafe_links(url: str) -> None:
    with pytest.raises(ValueError, match="artifact URL must be HTTP"):
        ConversationArtifact(
            filename="unsafe.txt",
            subject=None,
            media_type="text/plain",
            size_bytes=1,
            created_at=datetime(2026, 8, 6, tzinfo=UTC),
            url=url,
        )


@pytest.mark.parametrize(
    "url",
    [
        "/image.png",
        "//example.com/image.png",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "https://user:secret@example.com/image.png",
        "https://example.com/image.png#fragment",
        "https://example.com/\\evil.example/image.png",
        "https://example.com/%5cevil.example/image.png",
        "https://example.com/image\x00.png",
        "https://example.com/image%0d.png",
        "https://example.com/image\x7f.png",
    ],
)
def test_image_previews_accept_only_credential_free_http_urls(url: str) -> None:
    """A picture link names the host serving it, because an app page draws the chat framed on a site
    origin of its own and resolves a root-relative link against that site."""
    with pytest.raises(ValueError, match="image preview URL"):
        ImagePreview(media_type="image/png", url=url)


def test_an_image_preview_carries_the_link_the_deploy_minted() -> None:
    assert (
        ImagePreview(
            media_type="image/png", url="https://app.example.com/artifacts/a/b.png?exp=1&sig=2"
        ).url
        == "https://app.example.com/artifacts/a/b.png?exp=1&sig=2"
    )


@pytest.mark.parametrize(
    ("path", "media_type"),
    [
        ("chart.GIF", "image/gif"),
        ("chart.jpeg", "image/jpeg"),
        ("chart.jpg", "image/jpeg"),
        ("chart.PNG", "image/png"),
        ("chart.webp", "image/webp"),
        ("chart.svg", None),
    ],
)
def test_raster_image_media_types_are_a_closed_set(path: str, media_type: str | None) -> None:
    assert raster_image_media_type(path) == media_type


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "file:///etc/passwd", "https://user:secret@example.com"],
)
def test_conversation_sources_reject_unsafe_links(url: str) -> None:
    with pytest.raises(ValueError, match="source URL must be HTTP"):
        ConversationSource(url=url, title="unsafe", snippet="", published_date=None)
