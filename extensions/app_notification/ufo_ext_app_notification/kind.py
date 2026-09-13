"""The `notification` object kind: what has been raised for a member, read back and dismissed.

Raising needs a live turn — the row carries the turn's own authority and agent — so `apply` refuses
and names the tool. What the kind supplies is the register: every notification raised for the acting
member, its subject and latest body, how often it was raised and by which agent, and the delete
that dismisses one.

A row is the member's own and nobody else's, which is where this kind departs from the member-owned
default. Every other such kind lets a workspace admin inspect a row, because the row is a thing the
workspace holds and an admin answers for it. A notification is not that. It is one agent's judgement
about what should interrupt one person, addressed to that person, and reading someone else's is
nearer to reading their mail than to inspecting an object. The workspace's second admin would
otherwise open this app and see the first admin's whole inbox, which is what a member reported.

The kind therefore lists no `mine`. Every row a caller can see is theirs, so the flag could only
ever read true, and a filter that cannot separate anything is a filter the reader has to test to
learn nothing."""

from dataclasses import dataclass
from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ufo.sdk.audience import conversation_audience
from ufo.sdk.context import ExtensionContext
from ufo.sdk.objects import (
    AGENT_KIND,
    CONVERSATION_KIND,
    MemberReadableObjects,
    ObjectDetail,
    ObjectKind,
    ObjectLink,
    ObjectOwner,
    ObjectRef,
    OwnedRow,
    VerbNotSupported,
)
from ufo.sdk.tools import ToolContext
from ufo_ext_app_notification.store import (
    NOTIFICATION_KIND,
    Notification,
    NotificationStore,
)

SUMMARY_MAX = 120
RAISE_REFUSAL = (
    "raising a notification happens in a turn — the `notify` tool records it under that turn's "
    "authority"
)
DELETE_GATE = "only the member a notification concerns may dismiss it"


class NotificationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(title="Subject", description="The stable thing this is about.")
    body: str = Field(title="Body", description="The latest account of what happened.")


def _require_ext(ext: ExtensionContext | None) -> ExtensionContext:
    if ext is None:
        raise RuntimeError("the notification kind requires the app_notification ExtensionContext")
    return ext


def _owner(row: Notification) -> ObjectOwner:
    return ObjectOwner(member_id=row.member_id, audience=conversation_audience(row.member_id))


@dataclass(frozen=True)
class NotificationObjects(MemberReadableObjects[NotificationSpec, ObjectOwner]):
    """The kind's handlers over `NotificationStore`. The gate narrows the base's: a row is seen and
    dismissed by the member it concerns, and by nobody else."""

    kind_name: ClassVar[str] = NOTIFICATION_KIND
    mutate_gate: ClassVar[str] = RAISE_REFUSAL
    delete_gate: ClassVar[str] = DELETE_GATE

    def _visible(self, owner: ObjectOwner, acting: UUID | None, is_admin: bool) -> bool:
        """Ownership alone, admin or not. The base admits a workspace admin to every row; here that
        would hand one member every other member's notifications, and the read projection the
        homepage draws from is the same gate, so the page would show them too."""
        return self._owned(owner, acting)

    async def _member_rows(
        self, ext: ExtensionContext | None, *, member_id: UUID | None
    ) -> tuple[OwnedRow[ObjectOwner], ...]:
        return tuple(
            OwnedRow(
                name=row.name,
                summary=f"{row.subject}: {row.body}"[:SUMMARY_MAX],
                owner=_owner(row),
                fields={
                    "subject": row.subject,
                    "occurrences": row.occurrences,
                    "producer": row.produced_by_agent_name,
                    "triaged": row.triaged_turn_id is not None,
                    "delivered_surface": row.delivered_surface,
                    "created_at": row.created_at.isoformat(),
                },
            )
            for row in await NotificationStore(_require_ext(ext)).rows()
        )

    async def _member_object(
        self,
        ext: ExtensionContext | None,
        name: str,
        owner: ObjectOwner,
        *,
        member_id: UUID | None,
    ) -> ObjectDetail[NotificationSpec] | None:
        row = await self._find(ext, name)
        if row is None:
            return None
        return ObjectDetail(
            spec=NotificationSpec(subject=row.subject, body=row.body),
            created_at=row.created_at,
            updated_at=row.updated_at,
            links=(
                ObjectLink(
                    relation="created_in",
                    target=ObjectRef(
                        kind=CONVERSATION_KIND, name=str(row.produced_in_conversation_id)
                    ),
                ),
                ObjectLink(
                    relation="scoped_to",
                    target=ObjectRef(kind=AGENT_KIND, name=row.produced_by_agent_name),
                ),
            ),
        )

    async def _status(
        self, ctx: ToolContext, name: str, owner: ObjectOwner
    ) -> dict[str, JsonValue] | None:
        row = await self._find(ctx.ext, name)
        if row is None:
            return None
        return {
            "occurrences": row.occurrences,
            "first_raised_at": row.created_at.isoformat(),
            "last_raised_at": row.last_raised_at.isoformat(),
            "triaged_turn": None if row.triaged_turn_id is None else str(row.triaged_turn_id),
            "delivered_surface": row.delivered_surface,
        }

    async def _apply_owned(
        self,
        ctx: ToolContext,
        name: str,
        spec: NotificationSpec,
        old: NotificationSpec | None,
        owner: ObjectOwner | None,
    ) -> None:
        raise VerbNotSupported(RAISE_REFUSAL)

    async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None:
        row = await self._find(ctx.ext, name)
        if row is None or not await NotificationStore(_require_ext(ctx.ext)).dismiss(row.id):
            raise ValueError(f"notification {name!r} changed while dismissing")

    async def _find(self, ext: ExtensionContext | None, name: str) -> Notification | None:
        return next(
            (row for row in await NotificationStore(_require_ext(ext)).rows() if row.name == name),
            None,
        )


NOTIFICATION_OBJECT = ObjectKind(
    name=NOTIFICATION_KIND,
    description=(
        "One message an agent turn raised for a member and put in the Notification app's inbox: "
        "the subject it is about, the latest account of what happened, how many times that subject "
        "has been raised, and which agent raised it. Seen by the member it concerns and by nobody "
        "else, an admin included, since it is addressed to that member rather than held by the "
        "workspace. Raising is not an apply: the `notify` tool records a notification under "
        "the raising turn's authority, so this kind's list and get read. Deleting dismisses one."
    ),
    guidance=(
        "List to see what has been raised for the member and how often; get to read one "
        "notification's subject and body beside when it was first and last raised and whether a "
        "triage turn has read it and where it was delivered. Listing filters and orders on "
        "`subject`, `occurrences`, `producer`, `triaged`, `delivered_surface` and `created_at`; "
        "get "
        "shows a `created_in` link naming the conversation it was raised in and a `scoped_to` link "
        "naming the agent that raised it. Applying a manifest is refused: raise with `notify`. "
        "When a member says a notification is handled or unwanted, delete it by name."
    ),
    spec_model=NotificationSpec,
    store=NotificationObjects(),
    list_fields=frozenset(
        {"subject", "occurrences", "producer", "triaged", "delivered_surface", "created_at"}
    ),
)
