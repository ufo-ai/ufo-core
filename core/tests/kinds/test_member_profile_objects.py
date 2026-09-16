import base64
import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import yaml
from PIL import Image

from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.harness.sandbox.session import ExecResult, SandboxHandle, SandboxSession, SandboxSpec
from ufo.host.ext.loader import turn_tools
from ufo.host.kinds.member_profiles import (
    CLEAR_PHOTO_TOOL_DEF,
    MEMBER_PROFILE_KIND,
    MEMBER_PROFILE_OBJECT,
    SET_PHOTO_TOOL_DEF,
)
from ufo.runtime.member_profiles import (
    PROFILE_PHOTO_DIMENSION,
    InvalidProfilePhoto,
    photo_blob_key,
    read_profile,
)
from ufo.runtime.objects import (
    MemberListable,
    ObjectListQuery,
    UnknownObject,
    VerbNotSupported,
)
from ufo.runtime.tools.context import SpawnResult, ToolContext
from ufo.runtime.tools.registry import ToolDef
from ufo.runtime.turns.audience import conversation_audience
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import Agent, Turn

NOW = datetime(2026, 9, 15, tzinfo=UTC)
RAE = "rae.whitlock@example.com"
CLEO = "cleo.marsh@example.com"


class _UntouchedCarrier:
    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        raise AssertionError

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

    async def exec(
        self,
        handle: SandboxHandle,
        argv: tuple[str, ...],
        timeout_s: int,
        model_command: str | None = None,
    ) -> ExecResult:
        raise AssertionError


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise AssertionError


def _picture(width: int, height: int, colour: tuple[int, int, int]) -> bytes:
    written = BytesIO()
    Image.new("RGB", (width, height), colour).save(written, format="PNG")
    return written.getvalue()


async def _seed() -> tuple[UUID, UUID, UUID, UUID]:
    workspace_id, agent_id, rae_id, cleo_id = (uuid4() for _ in range(4))
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(id=workspace_id, created_at=NOW, updated_at=NOW)
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="Operator",
                prompt="p",
                model="gpt-5.6-terra",
                created_at=NOW,
                updated_at=NOW,
            )
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
                for member_id, email in ((rae_id, RAE), (cleo_id, CLEO))
            ],
        )
    return workspace_id, agent_id, rae_id, cleo_id


def _context(workspace_id: UUID, agent_id: UUID, speaker_id: UUID, root: Path) -> ToolContext:
    return ToolContext(
        sandbox=SandboxSession(
            carrier=_UntouchedCarrier(),
            handle=SandboxHandle(conversation_id=uuid4(), container_id="test"),
        ),
        blob=WorkspaceBlobStore(backend=FilesystemBlobStore(root=root)),
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=uuid4(),
            agent_id=agent_id,
            seq=1,
            status="running",
            inbound="set my profile",
            created_at=NOW,
        ),
        agent=Agent(name="Operator", prompt="p", model="gpt-5.6-terra"),
        spawn=_unavailable_spawn,
        speaker_member_id=speaker_id,
        audience=conversation_audience(speaker_id),
        artifact_token_secret="",
    )


def _tool(name: str) -> ToolDef:
    tools, _, _ = turn_tools((), None, audience=conversation_audience(None))
    return next(tool for tool in tools if tool.name == name)


async def _text(tool: ToolDef, ctx: ToolContext, **args: object) -> str:
    result = await tool.handler(ctx, tool.input_model.model_validate(args))
    assert result.is_error is False
    return result.content[0].text


def _manifest(member_id: UUID, name: str | None) -> str:
    return yaml.safe_dump(
        {"kind": MEMBER_PROFILE_KIND, "name": str(member_id), "spec": {"name": name}}
    )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_member_lists_and_names_only_their_own_profile(db: None, tmp_path: Path) -> None:
    workspace_id, agent_id, rae_id, cleo_id = await _seed()
    with ws(workspace_id):
        rae = _context(workspace_id, agent_id, rae_id, tmp_path)
        listing = json.loads(await _text(_tool("object_list"), rae, kind=MEMBER_PROFILE_KIND))
        assert [row["name"] for row in listing["objects"]] == [str(rae_id)]
        assert listing["objects"][0]["summary"] == f"rae.whitlock ({RAE}), drawn with initials"

        await _text(_tool("object_apply"), rae, manifest=_manifest(rae_id, "Rae Whitlock"))

        named = await read_profile(workspace_id, rae_id)
        assert named is not None
        assert named.name == "Rae Whitlock"
        assert named.name_source == "member"

        cleo = await read_profile(workspace_id, cleo_id)
        assert cleo is not None
        assert cleo.name is None


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_member_cannot_reach_a_colleagues_profile(db: None, tmp_path: Path) -> None:
    workspace_id, agent_id, rae_id, cleo_id = await _seed()
    with ws(workspace_id):
        rae = _context(workspace_id, agent_id, rae_id, tmp_path)
        with pytest.raises(UnknownObject):
            await _tool("object_get").handler(
                rae,
                _tool("object_get").input_model.model_validate(
                    {"ref": f"{MEMBER_PROFILE_KIND}/{cleo_id}"}
                ),
            )

        with pytest.raises(VerbNotSupported):
            await _tool("object_apply").handler(
                rae,
                _tool("object_apply").input_model.model_validate(
                    {"manifest": _manifest(cleo_id, "Not theirs")}
                ),
            )

        untouched = await read_profile(workspace_id, cleo_id)
        assert untouched is not None
        assert untouched.name is None

        store = MEMBER_PROFILE_OBJECT.store
        assert isinstance(store, MemberListable)
        page = await store.member_page(
            None,
            member_id=rae_id,
            admin=True,
            query=ObjectListQuery(supported_fields=MEMBER_PROFILE_OBJECT.list_fields),
        )
        assert [row.name for row in page.rows] == [str(rae_id)]
        assert await store.member_detail(None, str(cleo_id), member_id=rae_id, admin=True) is None


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_profile_is_emptied_rather_than_deleted(db: None, tmp_path: Path) -> None:
    workspace_id, agent_id, rae_id, _ = await _seed()
    with ws(workspace_id):
        rae = _context(workspace_id, agent_id, rae_id, tmp_path)
        store = MEMBER_PROFILE_OBJECT.store
        with pytest.raises(VerbNotSupported):
            await store.delete(rae, str(rae_id), expected_generation=None)

        await _text(_tool("object_apply"), rae, manifest=_manifest(rae_id, "Rae Whitlock"))
        await _text(_tool("object_apply"), rae, manifest=_manifest(rae_id, None))
        cleared = await read_profile(workspace_id, rae_id)
        assert cleared is not None
        assert cleared.name is None


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_photo_actions_store_and_clear_the_speakers_own_picture(
    db: None, tmp_path: Path
) -> None:
    workspace_id, agent_id, rae_id, _ = await _seed()
    with ws(workspace_id):
        rae = _context(workspace_id, agent_id, rae_id, tmp_path)
        await _text(
            SET_PHOTO_TOOL_DEF,
            rae,
            image=base64.b64encode(_picture(900, 300, (10, 120, 200))).decode(),
        )
        held = await read_profile(workspace_id, rae_id)
        assert held is not None
        assert held.photo_digest is not None
        assert held.photo_source == "member"

        stored = await rae.blob.get(photo_blob_key(rae_id))
        with Image.open(BytesIO(stored)) as written:
            assert written.format == "WEBP"
            assert written.size == (PROFILE_PHOTO_DIMENSION, PROFILE_PHOTO_DIMENSION)

        listing = json.loads(await _text(_tool("object_list"), rae, kind=MEMBER_PROFILE_KIND))
        assert listing["objects"][0]["summary"].endswith("drawn with a photo")

        await _text(CLEAR_PHOTO_TOOL_DEF, rae)
        emptied = await read_profile(workspace_id, rae_id)
        assert emptied is not None
        assert emptied.photo_digest is None
        assert emptied.photo_source is None
        assert not await rae.blob.exists(photo_blob_key(rae_id))


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_photo_that_is_not_a_picture_is_refused(db: None, tmp_path: Path) -> None:
    workspace_id, agent_id, rae_id, _ = await _seed()
    with ws(workspace_id):
        rae = _context(workspace_id, agent_id, rae_id, tmp_path)
        with pytest.raises(InvalidProfilePhoto):
            await SET_PHOTO_TOOL_DEF.handler(
                rae,
                SET_PHOTO_TOOL_DEF.input_model.model_validate(
                    {"image": base64.b64encode(b"not a picture").decode()}
                ),
            )

        held = await read_profile(workspace_id, rae_id)
        assert held is not None
        assert held.photo_digest is None
