from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
from PIL import Image
from ufo_ext_slack.profiles import SLACK_PROFILES_PER_TICK, SlackProfiles
from ufo_ext_slack.surface import SLACK_EXTENSION, SLACK_USERS_INFO_URL, SURFACE_SLACK

from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.runtime.access.credentials import CredentialSlotUnset
from ufo.runtime.ext.context import MemberProfileWrites, ScopedStore
from ufo.runtime.ext.surface import SurfaceInstallationAccess
from ufo.runtime.member_profiles import MemberProfiles, read_profile
from ufo.runtime.workspace import ws
from ufo.schema import tables

NOW = datetime(2026, 9, 15, tzinfo=UTC)
RAE = "rae.whitlock@example.com"
CLEO = "cleo.marsh@example.com"
AVATAR = "https://avatars.slack-edge.com/2026/U_RAE_512.png"


def _picture(colour: tuple[int, int, int] = (10, 120, 200)) -> bytes:
    written = BytesIO()
    Image.new("RGB", (400, 400), colour).save(written, format="PNG")
    return written.getvalue()


class _Credentials:
    def __init__(self, token: str | None) -> None:
        self.token = token

    async def stored(self, slot: str) -> bool:
        return self.token is not None

    async def get(self, slot: str) -> str:
        if self.token is None:
            raise CredentialSlotUnset(slot)
        return self.token


class _Context:
    """The scoped context a job handler receives, carrying the two capabilities this job reaches
    for. Both are core's real ones over real state, so the test asserts the ranked write and the
    declared-surface gate rather than a fake's record of being called."""

    def __init__(self, root: Path, token: str | None = "xoxb-token") -> None:
        self.profiles = MemberProfileWrites(WorkspaceBlobStore(backend=FilesystemBlobStore(root)))
        self.installations = SurfaceInstallationAccess(frozenset({SURFACE_SLACK}))
        self.credentials = _Credentials(token)
        self.store = ScopedStore(SLACK_EXTENSION)


async def _seed(*people: tuple[str, str | None]) -> tuple[UUID, tuple[UUID, ...]]:
    """A workspace whose members are linked to the Slack ids named, or to none."""
    workspace_id = uuid4()
    member_ids = tuple(uuid4() for _ in people)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(id=workspace_id, created_at=NOW, updated_at=NOW)
        )
        await connection.execute(
            sa.insert(tables.member),
            [
                {
                    "id": member_id,
                    "workspace_id": workspace_id,
                    "email": email,
                    "created_at": NOW,
                    "updated_at": NOW,
                }
                for member_id, (email, _) in zip(member_ids, people, strict=True)
            ],
        )
        linked = [
            {
                "workspace_id": workspace_id,
                "member_id": member_id,
                "surface": SURFACE_SLACK,
                "external_id": slack_id,
                "created_at": NOW,
                "updated_at": NOW,
            }
            for member_id, (_, slack_id) in zip(member_ids, people, strict=True)
            if slack_id is not None
        ]
        if linked:
            await connection.execute(sa.insert(tables.surface_identity), linked)
    return workspace_id, member_ids


def _slack(
    monkeypatch: pytest.MonkeyPatch,
    users: dict[str, dict[str, object]],
    avatars: dict[str, bytes],
) -> list[str]:
    asked: list[str] = []

    async def get(self: httpx.AsyncClient, url: str, **kwargs: object) -> httpx.Response:
        asked.append(url)
        sent = httpx.Request("GET", url)
        if url == SLACK_USERS_INFO_URL:
            params = kwargs.get("params")
            assert isinstance(params, dict)
            found = users.get(str(params["user"]))
            if found is None:
                return httpx.Response(
                    200, json={"ok": False, "error": "user_not_found"}, request=sent
                )
            return httpx.Response(200, json={"ok": True, "user": found}, request=sent)
        picture = avatars.get(url)
        if picture is None:
            return httpx.Response(404, request=sent)
        return httpx.Response(200, content=picture, request=sent)

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    return asked


def _user(name: str, *, image: str | None) -> dict[str, object]:
    return {
        "real_name": name,
        "team_id": "T1",
        "profile": {
            "image_512": image or "https://avatars.slack-edge.com/generated.png",
            "is_custom_image": image is not None,
        },
    }


async def test_a_workspace_whose_bot_token_was_emptied_is_left_alone(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, (rae_id,) = await _seed((RAE, "U_RAE"))
    asked = _slack(
        monkeypatch, {"U_RAE": _user("Rae Whitlock", image=AVATAR)}, {AVATAR: _picture()}
    )
    with ws(workspace_id):
        await SlackProfiles().run(_Context(tmp_path, token=None))
        held = await read_profile(workspace_id, rae_id)
    assert asked == []
    assert held is not None
    assert held.name is None
    assert held.photo_digest is None


async def test_slack_fills_the_name_and_the_picture_a_member_set_there(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, (rae_id,) = await _seed((RAE, "U_RAE"))
    _slack(monkeypatch, {"U_RAE": _user("Rae Whitlock", image=AVATAR)}, {AVATAR: _picture()})
    with ws(workspace_id):
        await SlackProfiles().run(_Context(tmp_path))
        held = await read_profile(workspace_id, rae_id)
    assert held is not None
    assert held.name == "Rae Whitlock"
    assert held.name_source == "slack"
    assert held.photo_digest is not None
    assert held.photo_source == "slack"


async def test_slacks_generated_pattern_is_not_taken_for_a_picture(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, (rae_id,) = await _seed((RAE, "U_RAE"))
    _slack(monkeypatch, {"U_RAE": _user("Rae Whitlock", image=None)}, {})
    with ws(workspace_id):
        await SlackProfiles().run(_Context(tmp_path))
        held = await read_profile(workspace_id, rae_id)
    assert held is not None
    assert held.name == "Rae Whitlock"
    assert held.photo_digest is None


async def test_a_member_slack_does_not_know_is_never_asked_about(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, (rae_id, cleo_id) = await _seed((RAE, "U_RAE"), (CLEO, None))
    asked = _slack(monkeypatch, {"U_RAE": _user("Rae Whitlock", image=None)}, {})
    with ws(workspace_id):
        await SlackProfiles().run(_Context(tmp_path))
        cleo = await read_profile(workspace_id, cleo_id)
    assert cleo is not None
    assert cleo.name is None
    assert asked == [SLACK_USERS_INFO_URL]
    assert (await read_profile(workspace_id, rae_id)) is not None


async def test_what_a_member_chose_is_never_replaced_by_slacks_answer(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, (rae_id,) = await _seed((RAE, "U_RAE"))
    store = WorkspaceBlobStore(backend=FilesystemBlobStore(tmp_path))
    _slack(monkeypatch, {"U_RAE": _user("Rae Whitlock", image=AVATAR)}, {AVATAR: _picture()})
    with ws(workspace_id):
        profiles = MemberProfiles(workspace_id=workspace_id, blob=store)
        await profiles.set_name(rae_id, "Rae W.", "member")
        theirs = await profiles.set_photo(rae_id, _picture((200, 30, 30)), "member")

        await SlackProfiles().run(_Context(tmp_path))
        held = await read_profile(workspace_id, rae_id)
    assert held is not None
    assert held.name == "Rae W."
    assert held.name_source == "member"
    assert held.photo_digest == theirs
    assert held.photo_source == "member"


async def test_a_tick_asks_slack_about_no_more_than_its_bound(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    people = tuple(
        (f"member{index}@example.com", f"U{index}") for index in range(SLACK_PROFILES_PER_TICK + 5)
    )
    workspace_id, _ = await _seed(*people)
    asked = _slack(
        monkeypatch,
        {slack_id: _user(f"Member {slack_id}", image=None) for _, slack_id in people},
        {},
    )
    with ws(workspace_id):
        await SlackProfiles().run(_Context(tmp_path))
    assert len(asked) == SLACK_PROFILES_PER_TICK


async def test_an_avatar_slack_will_not_serve_leaves_the_name_standing(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, (rae_id,) = await _seed((RAE, "U_RAE"))
    _slack(monkeypatch, {"U_RAE": _user("Rae Whitlock", image=AVATAR)}, {})
    with ws(workspace_id):
        await SlackProfiles().run(_Context(tmp_path))
        held = await read_profile(workspace_id, rae_id)
    assert held is not None
    assert held.name == "Rae Whitlock"
    assert held.photo_digest is None


async def test_a_tick_starts_where_the_last_one_stopped_so_nobody_is_stranded(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A member whose Slack account carries no picture of their own stays undrawn, so a window that
    took the head of the order every tick would ask about the same members forever. The second tick
    opens where the first stopped, and between them every linked member has been asked about."""
    people = tuple(
        (f"member{index:02d}@example.com", f"U{index:02d}")
        for index in range(SLACK_PROFILES_PER_TICK + 10)
    )
    workspace_id, _ = await _seed(*people)
    asked = _slack(
        monkeypatch,
        {slack_id: _user(f"Member {slack_id}", image=None) for _, slack_id in people},
        {},
    )
    job, ctx = SlackProfiles(), _Context(tmp_path)
    with ws(workspace_id):
        await job.run(ctx)
        first = len(asked)
        asked.clear()
        await job.run(ctx)
        second = len(asked)
        drawn = {profile.email for profile in await ctx.profiles.undrawn() if profile.name}

    assert first == SLACK_PROFILES_PER_TICK
    assert second == SLACK_PROFILES_PER_TICK
    assert drawn == {email for email, _ in people}
