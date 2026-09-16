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
from ufo.runtime.gravatar import (
    GRAVATAR_MEMBERS_PER_TICK,
    GravatarPrefill,
    gravatar_digest,
    unpictured_member_workspaces,
)
from ufo.runtime.member_profiles import MemberProfiles, read_profile
from ufo.runtime.workspace import ws
from ufo.schema import tables

NOW = datetime(2026, 9, 15, tzinfo=UTC)
RAE = "rae.whitlock@example.com"
CLEO = "cleo.marsh@example.com"


def _picture() -> bytes:
    written = BytesIO()
    Image.new("RGB", (400, 400), (10, 120, 200)).save(written, format="PNG")
    return written.getvalue()


class _Context:
    """Stands in for the scoped context a job handler receives, carrying the two capabilities the
    gravatar job reaches for. Both are core's own over real state — the ranked write and the store
    the cursor lives in — so what the tests assert is the behaviour, never a fake's record of being
    called. One instance stands for the deploy across ticks, as the real context does."""

    def __init__(self, root: Path) -> None:
        self.profiles = MemberProfileWrites(WorkspaceBlobStore(backend=FilesystemBlobStore(root)))
        self.store = ScopedStore(CORE_EXTENSION)


async def _seed(*emails: str) -> tuple[UUID, tuple[UUID, ...]]:
    workspace_id = uuid4()
    member_ids = tuple(uuid4() for _ in emails)
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
                for member_id, email in zip(member_ids, emails, strict=True)
            ],
        )
    return workspace_id, member_ids


def _answers(monkeypatch: pytest.MonkeyPatch, by_digest: dict[str, httpx.Response]) -> list[str]:
    """Every gravatar URL the job asked for, answering each from the digest it names."""
    asked: list[str] = []

    async def get(self: httpx.AsyncClient, url: str, **kwargs: object) -> httpx.Response:
        asked.append(url)
        held = by_digest.get(url.rsplit("/", 1)[-1])
        return held if held is not None else httpx.Response(404, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    return asked


def test_a_member_is_keyed_by_the_normalized_form_of_their_address() -> None:
    assert gravatar_digest("  Rae.Whitlock@Example.COM ") == gravatar_digest(RAE)
    assert len(gravatar_digest(RAE)) == 64


async def test_a_member_with_no_picture_gets_the_one_gravatar_holds(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, (rae_id,) = await _seed(RAE)
    asked = _answers(monkeypatch, {gravatar_digest(RAE): httpx.Response(200, content=_picture())})
    with ws(workspace_id):
        await GravatarPrefill().run(_Context(tmp_path))
        held = await read_profile(workspace_id, rae_id)
    assert held is not None
    assert held.photo_source == "gravatar"
    assert held.photo_digest is not None
    assert len(asked) == 1


async def test_gravatar_holding_no_picture_leaves_the_member_undrawn(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, (rae_id,) = await _seed(RAE)
    _answers(monkeypatch, {})
    with ws(workspace_id):
        await GravatarPrefill().run(_Context(tmp_path))
        held = await read_profile(workspace_id, rae_id)
    assert held is not None
    assert held.photo_digest is None
    assert held.photo_source is None


async def test_a_picture_the_member_set_is_never_asked_about(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, (rae_id, cleo_id) = await _seed(RAE, CLEO)
    store = WorkspaceBlobStore(backend=FilesystemBlobStore(tmp_path))
    asked = _answers(monkeypatch, {gravatar_digest(CLEO): httpx.Response(200, content=_picture())})
    with ws(workspace_id):
        theirs = await MemberProfiles(workspace_id=workspace_id, blob=store).set_photo(
            rae_id, _picture(), "member"
        )
        await GravatarPrefill().run(_Context(tmp_path))
        rae = await read_profile(workspace_id, rae_id)
        cleo = await read_profile(workspace_id, cleo_id)
    assert rae is not None and rae.photo_digest == theirs and rae.photo_source == "member"
    assert cleo is not None and cleo.photo_source == "gravatar"
    assert asked == [f"https://gravatar.com/avatar/{gravatar_digest(CLEO)}"]


async def test_a_tick_asks_about_no_more_than_its_bound(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    emails = tuple(f"member{index}@example.com" for index in range(GRAVATAR_MEMBERS_PER_TICK + 5))
    workspace_id, _ = await _seed(*emails)
    asked = _answers(monkeypatch, {})
    with ws(workspace_id):
        await GravatarPrefill().run(_Context(tmp_path))
    assert len(asked) == GRAVATAR_MEMBERS_PER_TICK


async def test_an_unreachable_gravatar_leaves_the_job_standing(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, (rae_id,) = await _seed(RAE)

    async def refuse(self: httpx.AsyncClient, url: str, **kwargs: object) -> httpx.Response:
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(httpx.AsyncClient, "get", refuse)
    with ws(workspace_id):
        await GravatarPrefill().run(_Context(tmp_path))
        held = await read_profile(workspace_id, rae_id)
    assert held is not None
    assert held.photo_digest is None


async def test_only_a_workspace_with_an_unpictured_member_opens_a_tick(
    db: None, tmp_path: Path
) -> None:
    workspace_id, (rae_id,) = await _seed(RAE)
    candidates = unpictured_member_workspaces()
    assert workspace_id in await candidates()

    with ws(workspace_id):
        await MemberProfiles(
            workspace_id=workspace_id,
            blob=WorkspaceBlobStore(backend=FilesystemBlobStore(tmp_path)),
        ).set_photo(rae_id, _picture(), "member")
    assert workspace_id not in await candidates()


async def test_a_tick_starts_where_the_last_one_stopped_so_nobody_is_stranded(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gravatar holding no picture leaves a member exactly as undrawn as it found them, so a window
    that took the head of the order every tick would ask about the same addresses forever and never
    reach the rest. The second tick opens where the first stopped, and between them every address on
    a roster half again as long as one window has been asked about."""
    emails = tuple(
        f"member{index:02d}@example.com" for index in range(GRAVATAR_MEMBERS_PER_TICK + 10)
    )
    workspace_id, _ = await _seed(*emails)
    asked = _answers(monkeypatch, {})
    job, ctx = GravatarPrefill(), _Context(tmp_path)
    with ws(workspace_id):
        await job.run(ctx)
        first = tuple(asked)
        asked.clear()
        await job.run(ctx)
        second = tuple(asked)

    every = {f"https://gravatar.com/avatar/{gravatar_digest(email)}" for email in emails}
    assert len(first) == GRAVATAR_MEMBERS_PER_TICK
    assert len(second) == GRAVATAR_MEMBERS_PER_TICK
    assert set(second) - set(first) == every - set(first)
    assert set(first) | set(second) == every
