from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from PIL import Image

from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.runtime.member_profiles import (
    PROFILE_PHOTO_DIMENSION,
    InvalidProfilePhoto,
    MemberProfile,
    MemberProfiles,
    ProfilePhoto,
    photo_blob_key,
    profile_name,
    read_profile,
)
from ufo.runtime.workspace import ws
from ufo.schema import tables

NOW = datetime(2026, 9, 15, tzinfo=UTC)


def _picture(width: int, height: int, colour: tuple[int, int, int], fmt: str = "PNG") -> bytes:
    written = BytesIO()
    Image.new("RGB", (width, height), colour).save(written, format=fmt)
    return written.getvalue()


async def _seed() -> tuple[UUID, UUID]:
    workspace_id, member_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(id=workspace_id, created_at=NOW, updated_at=NOW)
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="rae.whitlock@example.com",
                created_at=NOW,
                updated_at=NOW,
            )
        )
    return workspace_id, member_id


def _profiles(workspace_id: UUID, tmp_path: Path) -> MemberProfiles:
    return MemberProfiles(
        workspace_id=workspace_id,
        blob=WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path)),
    )


def test_an_unnamed_member_is_drawn_under_the_local_part_of_their_address() -> None:
    bare = MemberProfile(
        id=uuid4(),
        email="rae.whitlock@example.com",
        name=None,
        name_source=None,
        photo_digest=None,
        photo_source=None,
        created_at=NOW,
        updated_at=NOW,
    )
    assert profile_name(bare) == "rae.whitlock"
    assert profile_name(MemberProfile(**{**vars(bare), "name": "Rae Whitlock"})) == "Rae Whitlock"


def test_a_stored_photo_is_one_square_webp_whatever_arrived() -> None:
    stored, digest = ProfilePhoto.normalized(_picture(900, 300, (10, 120, 200), "JPEG"))
    with Image.open(BytesIO(stored)) as written:
        assert written.format == "WEBP"
        assert written.size == (PROFILE_PHOTO_DIMENSION, PROFILE_PHOTO_DIMENSION)
    assert len(digest) == 64
    assert ProfilePhoto.normalized(_picture(900, 300, (10, 120, 200), "JPEG"))[1] == digest


def test_bytes_that_are_not_a_picture_are_refused() -> None:
    with pytest.raises(InvalidProfilePhoto):
        ProfilePhoto.normalized(b"")
    with pytest.raises(InvalidProfilePhoto):
        ProfilePhoto.normalized(
            b"<svg xmlns='http://www.w3.org/2000/svg'><rect width='9' height='9'/></svg>"
        )


async def test_a_photo_a_member_set_survives_both_derived_sources(db: None, tmp_path: Path) -> None:
    workspace_id, member_id = await _seed()
    with ws(workspace_id):
        profiles = _profiles(workspace_id, tmp_path)
        theirs = await profiles.set_photo(member_id, _picture(64, 64, (200, 30, 30)), "member")
        assert theirs is not None

        assert await profiles.set_photo(member_id, _picture(64, 64, (30, 200, 30)), "slack") is None
        assert (
            await profiles.set_photo(member_id, _picture(64, 64, (30, 30, 200)), "gravatar") is None
        )

        held = await read_profile(workspace_id, member_id)
        assert held is not None
        assert held.photo_digest == theirs
        assert held.photo_source == "member"


async def test_slack_fills_what_gravatar_filled_and_gravatar_does_not_fill_back(
    db: None, tmp_path: Path
) -> None:
    workspace_id, member_id = await _seed()
    with ws(workspace_id):
        profiles = _profiles(workspace_id, tmp_path)
        await profiles.set_photo(member_id, _picture(64, 64, (30, 30, 200)), "gravatar")
        slack = await profiles.set_photo(member_id, _picture(64, 64, (30, 200, 30)), "slack")
        assert slack is not None

        assert (
            await profiles.set_photo(member_id, _picture(64, 64, (200, 30, 30)), "gravatar") is None
        )
        held = await read_profile(workspace_id, member_id)
        assert held is not None
        assert held.photo_digest == slack
        assert held.photo_source == "slack"


async def test_clearing_a_photo_drops_the_bytes_and_reopens_the_derived_sources(
    db: None, tmp_path: Path
) -> None:
    workspace_id, member_id = await _seed()
    with ws(workspace_id):
        profiles = _profiles(workspace_id, tmp_path)
        await profiles.set_photo(member_id, _picture(64, 64, (200, 30, 30)), "member")
        assert await profiles.photo(member_id) is not None

        await profiles.clear_photo(member_id)
        assert await profiles.photo(member_id) is None
        assert not await profiles.blob.exists(photo_blob_key(member_id))

        filled = await profiles.set_photo(member_id, _picture(64, 64, (30, 200, 30)), "slack")
        assert filled is not None


async def test_a_name_a_member_set_outranks_slack_and_clearing_reopens_it(
    db: None, tmp_path: Path
) -> None:
    workspace_id, member_id = await _seed()
    with ws(workspace_id):
        profiles = _profiles(workspace_id, tmp_path)
        assert await profiles.set_name(member_id, "Rae Whitlock", "slack") is True
        assert await profiles.set_name(member_id, "Rae W.", "member") is True
        assert await profiles.set_name(member_id, "Rae Whitlock", "slack") is False

        held = await read_profile(workspace_id, member_id)
        assert held is not None
        assert held.name == "Rae W."

        assert await profiles.set_name(member_id, None, "member") is True
        cleared = await read_profile(workspace_id, member_id)
        assert cleared is not None
        assert cleared.name is None
        assert cleared.name_source is None
        assert profile_name(cleared) == "rae.whitlock"
        assert await profiles.set_name(member_id, "Rae Whitlock", "slack") is True


async def test_a_name_is_collapsed_and_bounded(db: None, tmp_path: Path) -> None:
    workspace_id, member_id = await _seed()
    with ws(workspace_id):
        profiles = _profiles(workspace_id, tmp_path)
        await profiles.set_name(member_id, "  Rae   Whitlock \n", "member")
        held = await read_profile(workspace_id, member_id)
        assert held is not None
        assert held.name == "Rae Whitlock"

        await profiles.set_name(member_id, "   ", "member")
        blank = await read_profile(workspace_id, member_id)
        assert blank is not None
        assert blank.name is None
