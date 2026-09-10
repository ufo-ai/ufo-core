"""The inbound a source trigger sends into the conversation it wakes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from ufo_ext_sources.tools import alert_message
from ufo_ext_sources.triggers import SourceTrigger

from ufo.runtime.access.grants import FeedConnection
from ufo.sdk.sources import PageChange

WAKE_TIME = datetime(2026, 7, 20, tzinfo=UTC)
WAKE_ID = UUID("30000000-0000-0000-0000-000000000001")


def woken_inbound(
    provider: str, account: str, stream: str, title: str, page_id: UUID, resource: str = ""
) -> str:
    """One updated page, worded by the deploy's own writer, so a case that stages a woken turn
    reads what the product sends rather than a copy of it that drifts."""
    return alert_message(
        FeedConnection(
            id=WAKE_ID,
            provider=provider,
            account_id=account,
            base_url=None,
            backfill_days=None,
            owner_member_id=None,
            shared=True,
        ),
        SourceTrigger(
            id=WAKE_ID,
            connection_id=WAKE_ID,
            conversation_id=WAKE_ID,
            agent_id=WAKE_ID,
            resource=resource,
            delivery="current",
            created_by_member_id=None,
            created_at=WAKE_TIME,
            updated_at=WAKE_TIME,
        ),
        [
            PageChange(
                page_id=page_id,
                source_id=WAKE_ID,
                subject="shared",
                stream=stream,
                title=title,
                body="",
                digest="sha256:0",
                revision=1,
                tombstone=False,
                created_at=WAKE_TIME - timedelta(days=1),
                as_of=WAKE_TIME,
                changed_at=WAKE_TIME,
            )
        ],
        None,
    )
