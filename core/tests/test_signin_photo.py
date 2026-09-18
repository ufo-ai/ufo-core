from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
from PIL import Image

from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.runtime.ext.context import CORE_EXTENSION, MemberProfileWrites, ScopedStore
from ufo.runtime.member_profiles import MemberProfiles, read_profile
from ufo.runtime.signin_photo import SigninPhotos, unfetched_signin_photo_workspaces
from ufo.runtime.workspace import ws
from ufo.schema import tables

NOW = datetime(2026, 9, 17, tzinfo=UTC)
RAE = "rae.whitlock@example.com"
CLEO = "cleo.marsh@example.com"
HOSTED = "https://lh3.googleusercontent.com/a/ACg8ocRae=s96-c"
STORED_SIZE = "https://lh3.googleusercontent.com/a/ACg8ocRae=s256-c"


def _picture(colour: tuple[int, int, int] = (10, 120, 200)) -> bytes:
    written = BytesIO()
    Image.new("RGB", (400, 400), colour).save(written, format="PNG")
    return written.getvalue()


class _Context:
    def __init__(self, root: Path) -> None:
        self.profiles = MemberProfileWrites(WorkspaceBlobStore(backend=FilesystemBlobStore(root)))
        self.store = ScopedStore(CORE_EXTENSION)


async def _seed(*people: tuple[str, str | None]) -> tuple[UUID, tuple[UUID, ...]]:
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
                    "seated_at": NOW,
                    "signin_photo_url": url,
                    "created_at": NOW,
                    "updated_at": NOW,
                }
                for member_id, (email, url) in zip(member_ids, people, strict=True)
            ],
        )
    return workspace_id, member_ids


def _host(monkeypatch: pytest.MonkeyPatch, answers: dict[str, httpx.Response]) -> list[str]:
    asked: list[str] = []

    async def get(self: httpx.AsyncClient, url: str, **kwargs: object) -> httpx.Response:
        httpx.URL(url)
        asked.append(url)
        held = answers.get(url)
        if isinstance(held, Exception):
            raise held
        return held if held is not None else httpx.Response(404, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    return asked


async def _pending_url(workspace_id: UUID, member_id: UUID) -> str | None:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.member.c.signin_photo_url).where(
                    tables.member.c.workspace_id == workspace_id, tables.member.c.id == member_id
                )
            )
        ).scalar_one()


async def test_the_picture_the_sign_in_named_is_fetched_at_the_stored_size(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, (rae_id,) = await _seed((RAE, HOSTED))
    asked = _host(monkeypatch, {STORED_SIZE: httpx.Response(200, content=_picture())})
    with ws(workspace_id):
        await SigninPhotos().run(_Context(tmp_path))
        held = await read_profile(workspace_id, rae_id)
    assert asked == [STORED_SIZE]
    assert held is not None
    assert held.photo_source == "signin"
    assert held.photo_digest is not None


async def test_a_link_the_host_refuses_is_dropped_after_one_attempt(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, (rae_id,) = await _seed((RAE, HOSTED))
    _host(monkeypatch, {})
    candidates = unfetched_signin_photo_workspaces()
    assert workspace_id in await candidates()
    with ws(workspace_id):
        await SigninPhotos().run(_Context(tmp_path))
        held = await read_profile(workspace_id, rae_id)
        pending = await _pending_url(workspace_id, rae_id)
    assert held is not None
    assert held.photo_digest is None
    assert pending is None
    assert workspace_id not in await candidates()


async def test_a_host_out_of_reach_is_asked_again_next_tick(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, (rae_id,) = await _seed((RAE, HOSTED))
    _host(monkeypatch, {STORED_SIZE: httpx.ConnectError("refused")})  # type: ignore[dict-item]
    with ws(workspace_id):
        await SigninPhotos().run(_Context(tmp_path))
        pending = await _pending_url(workspace_id, rae_id)
    assert pending == HOSTED


async def test_an_address_httpx_cannot_parse_is_dropped_and_the_tick_goes_on(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, (cleo_id, rae_id) = await _seed(
        (CLEO, "https://example.com:port/a.png"), (RAE, HOSTED)
    )
    asked = _host(monkeypatch, {STORED_SIZE: httpx.Response(200, content=_picture())})
    with ws(workspace_id):
        await SigninPhotos().run(_Context(tmp_path))
        cleo = await read_profile(workspace_id, cleo_id)
        rae = await read_profile(workspace_id, rae_id)
        pending = await _pending_url(workspace_id, cleo_id)
    assert asked == [STORED_SIZE]
    assert cleo is not None and cleo.photo_digest is None
    assert pending is None
    assert rae is not None and rae.photo_source == "signin"


async def test_bytes_that_are_not_a_picture_drop_the_link(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, (rae_id,) = await _seed((RAE, HOSTED))
    _host(monkeypatch, {STORED_SIZE: httpx.Response(200, content=b"<html>sign in</html>")})
    with ws(workspace_id):
        await SigninPhotos().run(_Context(tmp_path))
        held = await read_profile(workspace_id, rae_id)
        pending = await _pending_url(workspace_id, rae_id)
    assert held is not None
    assert held.photo_digest is None
    assert pending is None


async def test_a_member_who_set_their_own_picture_is_never_asked_about(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, (rae_id, cleo_id) = await _seed((RAE, HOSTED), (CLEO, None))
    asked = _host(monkeypatch, {STORED_SIZE: httpx.Response(200, content=_picture())})
    store = WorkspaceBlobStore(backend=FilesystemBlobStore(tmp_path))
    with ws(workspace_id):
        theirs = await MemberProfiles(workspace_id=workspace_id, blob=store).set_photo(
            rae_id, _picture((200, 30, 30)), "member"
        )
        assert workspace_id not in await unfetched_signin_photo_workspaces()()
        await SigninPhotos().run(_Context(tmp_path))
        rae = await read_profile(workspace_id, rae_id)
        cleo = await read_profile(workspace_id, cleo_id)
    assert asked == []
    assert rae is not None and rae.photo_digest == theirs and rae.photo_source == "member"
    assert cleo is not None and cleo.photo_digest is None
