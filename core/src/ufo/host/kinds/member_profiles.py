"""The core-registered `member_profile` object kind: what a member is called and the picture drawn
for them.

A member owns exactly one and reads no other — the speaker is the row, so no verb takes a target
and none can name somebody else's. The name is the spec; the picture is not, because a spec is read
back into a turn's context and a picture's bytes have no business there. Two collection actions
carry it instead: one sets it from bytes the member supplied, one clears it so the derived sources
answer again.

Who else may see a member's name and picture is not this kind's question: a colleague reads them off
the roster and the transcript projections, which is where a workspace's own members have always been
visible to each other."""

import base64
import binascii
from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ufo.runtime.ext.context import ExtensionContext, JsonValue
from ufo.runtime.member_profiles import (
    PROFILE_NAME_MAX_CHARS,
    PROFILE_PHOTO_MAX_BYTES,
    InvalidProfilePhoto,
    MemberProfile,
    MemberProfiles,
    profile_name,
    read_profile,
)
from ufo.runtime.objects import (
    MemberObject,
    ObjectDetail,
    ObjectKind,
    ObjectListQuery,
    ObjectPage,
    ObjectRow,
    VerbNotSupported,
    object_page,
)
from ufo.runtime.tools.context import TextContent, ToolContext, ToolResult
from ufo.runtime.tools.registry import ActionPresentation, ObjectBinding, ToolDef
from ufo.runtime.workspace import ws_current

MEMBER_PROFILE_KIND = "member_profile"
SET_PHOTO_TOOL = "set_member_photo"
CLEAR_PHOTO_TOOL = "clear_member_photo"
MEMBER_PROFILE_CREATE = "a member profile exists from the moment the member does"
MEMBER_PROFILE_DELETE = (
    f"a member profile is emptied, never deleted — apply a null name and run {CLEAR_PHOTO_TOOL}"
)


class MemberProfileSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(
        default=None,
        description=(
            "What to call this member, at most "
            f"{PROFILE_NAME_MAX_CHARS} characters. Null falls back to the name their Slack account "
            "reports, and then to the local part of their email address."
        ),
    )


@dataclass(frozen=True)
class MemberProfileObjects:
    """Read and write exactly the acting member's own profile."""

    async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage:
        return object_page(await self._rows(ctx.require_speaker()), query)

    async def member_page(
        self,
        ext: ExtensionContext | None,
        *,
        member_id: UUID,
        admin: bool,
        query: ObjectListQuery,
    ) -> ObjectPage:
        return object_page(await self._rows(member_id), query)

    async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[MemberProfileSpec] | None:
        profile = await self._own(ctx.require_speaker(), name)
        return None if profile is None else _detail(profile)

    async def member_detail(
        self,
        ext: ExtensionContext | None,
        name: str,
        *,
        member_id: UUID,
        admin: bool,
    ) -> MemberObject[MemberProfileSpec] | None:
        profile = await self._own(member_id, name)
        if profile is None:
            return None
        return MemberObject(row=_row(profile), detail=_detail(profile))

    async def status(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> dict[str, JsonValue] | None:
        profile = await self._own(ctx.require_speaker(), name)
        if profile is None:
            return None
        return {"name": profile_name(profile), "photo": profile.photo_digest is not None}

    async def apply(
        self,
        ctx: ToolContext,
        name: str,
        spec: MemberProfileSpec,
        old: MemberProfileSpec | None,
        *,
        expected_generation: UUID | None,
    ) -> None:
        speaker = ctx.require_speaker()
        if old is None:
            raise VerbNotSupported(MEMBER_PROFILE_CREATE)
        if await self._own(speaker, name) is None:
            raise VerbNotSupported(MEMBER_PROFILE_CREATE)
        await _profiles(ctx).set_name(speaker, spec.name, "member")

    async def delete(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> None:
        raise VerbNotSupported(MEMBER_PROFILE_DELETE)

    async def _own(self, member_id: UUID, name: str) -> MemberProfile | None:
        try:
            named = UUID(name)
        except ValueError:
            return None
        if named != member_id:
            return None
        return await read_profile(ws_current().workspace_id, member_id)

    async def _rows(self, member_id: UUID) -> tuple[ObjectRow, ...]:
        profile = await read_profile(ws_current().workspace_id, member_id)
        return () if profile is None else (_row(profile),)


def _row(profile: MemberProfile) -> ObjectRow:
    drawn = "a photo" if profile.photo_digest else "initials"
    return ObjectRow(
        name=str(profile.id),
        summary=f"{profile_name(profile)} ({profile.email}), drawn with {drawn}",
        fields={"email": profile.email},
    )


def _detail(profile: MemberProfile) -> ObjectDetail[MemberProfileSpec]:
    return ObjectDetail(
        spec=MemberProfileSpec(name=profile.name),
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


class SetMemberPhotoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image: str = Field(
        description=(
            "The picture, base64-encoded, as a PNG, JPEG, GIF or WebP under "
            f"{PROFILE_PHOTO_MAX_BYTES} bytes. It is centre-cropped square and re-encoded before "
            "it is stored. These bytes come from a member handing over a file — if you do not "
            "have one, ask them for it rather than composing this field."
        )
    )


@dataclass(frozen=True)
class MemberPhoto:
    """Set and clear the speaking member's own picture. The bytes travel as the action's input
    rather than the profile's spec, so reading a profile back never drags a picture into context,
    and clearing is its own act rather than a null field — clearing restores the member to whatever
    Slack or gravatar answers for them, which setting a name to null does not do."""

    async def set(self, ctx: ToolContext, args: SetMemberPhotoInput) -> ToolResult:
        speaker = ctx.require_speaker()
        try:
            data = base64.b64decode(args.image, validate=True)
        except (binascii.Error, ValueError) as error:
            raise InvalidProfilePhoto("a profile photo is not base64") from error
        await _profiles(ctx).set_photo(speaker, data, "member")
        return ToolResult(content=(TextContent(text="Your photo is set."),))

    async def clear(self, ctx: ToolContext, args: BaseModel) -> ToolResult:
        await _profiles(ctx).clear_photo(ctx.require_speaker())
        return ToolResult(
            content=(
                TextContent(
                    text="Your photo is cleared. Slack's or gravatar's answers for your address "
                    "may fill it again."
                ),
            )
        )


class ClearMemberPhotoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _profiles(ctx: ToolContext) -> MemberProfiles:
    return MemberProfiles(workspace_id=ws_current().workspace_id, blob=ctx.blob)


SET_PHOTO_TOOL_DEF = ToolDef(
    name=SET_PHOTO_TOOL,
    description=(
        "Set the speaking member's own profile photo from base64 image bytes they supplied. "
        "It stands beside their name wherever the product draws them. Nobody else's photo can be "
        f"set here. {CLEAR_PHOTO_TOOL} removes it."
    ),
    input_model=SetMemberPhotoInput,
    handler=MemberPhoto().set,
    side_effecting=True,
    parallel_safe=True,
    bound=ObjectBinding(kind=MEMBER_PROFILE_KIND, binding="collection"),
    presentation=ActionPresentation(label="Set photo"),
)

CLEAR_PHOTO_TOOL_DEF = ToolDef(
    name=CLEAR_PHOTO_TOOL,
    description=(
        "Remove the speaking member's own profile photo, so their initials stand for them again "
        "unless their Slack account or gravatar answers for their address."
    ),
    input_model=ClearMemberPhotoInput,
    handler=MemberPhoto().clear,
    side_effecting=True,
    parallel_safe=True,
    bound=ObjectBinding(kind=MEMBER_PROFILE_KIND, binding="collection"),
    presentation=ActionPresentation(label="Remove photo"),
)

MEMBER_PROFILE_OBJECT = ObjectKind(
    name=MEMBER_PROFILE_KIND,
    description="The speaking member's own name and photo, as the product draws them.",
    guidance=(
        "One row, always the speaking member's own: the id is their member id, and no other "
        "member's profile is listed, read, or written here — ask the member kind who a colleague "
        "is. Apply {name: '...'} to set what they are called, or {name: null} to fall back to the "
        "name their Slack account reports and then to the local part of their address. The photo "
        f"is not a spec field: run the collection's {SET_PHOTO_TOOL} action with base64 image "
        f"bytes the member supplied, and {CLEAR_PHOTO_TOOL} to remove it. A cleared photo may be "
        "filled again from their Slack account or from gravatar; a photo they set is never "
        "overwritten by either. Profiles are neither created nor deleted — a member has one from "
        "the moment they join."
    ),
    spec_model=MemberProfileSpec,
    store=MemberProfileObjects(),
    list_fields=frozenset({"email"}),
)
