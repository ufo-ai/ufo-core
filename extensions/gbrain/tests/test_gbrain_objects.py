"""The `gbrain_source` object kind end to end: registration through the object verbs.

Every mutation drives the real tool dispatch (`turn_tools` over the extension's manifest), and
assertions read back through the durable `source` rows and the verbs' own results: derived names,
the one-origin spec refusals, private-by-default registration, the operator-config refusal on a
directory root, the settled-source grant on an identical re-apply, resync, and delete tombstoning
the source's pages. Reads show shared sources plus the member's own — a workspace admin sees
all."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import yaml
from cryptography.fernet import Fernet
from ufo_ext_gbrain.folder import FOLDER_BACKEND
from ufo_ext_gbrain.git import GIT_BACKEND, GITHUB_TOKEN_SLOT
from ufo_ext_gbrain.manifest import NAME, manifest
from ufo_ext_gbrain.objects import GBRAIN_KIND, gbrain_source_name

from ufo.access.credentials import CredentialStore
from ufo.agent_scope import agent
from ufo.config import SourceConfig, SourceEntry
from ufo.db import workspace_tx
from ufo.ext.context import context_for
from ufo.ext.loader import turn_tools
from ufo.objects import UnknownObject
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import conversation_audience
from ufo.sdk.objects import VerbNotSupported
from ufo.sdk.tools import ToolContext
from ufo.sources.sync import register_sources
from ufo.tools.registry import ToolDef
from ufo.turns.subjects import SHARED_SUBJECT, member_subject
from ufo.workspace import ws

TOOL_NARRATION = "setting up the gbrain source"
REPO = "octo/wiki"
OTHER_REPO = "octo/handbook"
BRANCH = "docs"
ROOT = "/srv/notes"
DECLARED = frozenset({GITHUB_TOKEN_SLOT})
GBRAIN_BACKENDS = (GIT_BACKEND, FOLDER_BACKEND)


@dataclass(frozen=True)
class _Workspace:
    workspace_id: UUID
    owner_id: UUID
    member_id: UUID
    agent_id: UUID
    conversation_id: UUID


async def _workspace() -> _Workspace:
    workspace_id = uuid4()
    owner_id = uuid4()
    member_id = uuid4()
    agent_id = uuid4()
    conversation_id = uuid4()
    created_at = datetime(2026, 8, 1, tzinfo=UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=created_at, updated_at=created_at
            )
        )
        await connection.execute(
            sa.insert(tables.member),
            [
                {
                    "id": owner_id,
                    "workspace_id": workspace_id,
                    "email": f"{owner_id.hex}@x.test",
                    "is_admin": True,
                    "created_at": created_at,
                    "updated_at": created_at,
                },
                {
                    "id": member_id,
                    "workspace_id": workspace_id,
                    "email": f"{member_id.hex}@x.test",
                    "is_admin": False,
                    "created_at": created_at + timedelta(seconds=1),
                    "updated_at": created_at + timedelta(seconds=1),
                },
            ],
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                is_main=True,
                created_at=created_at,
                updated_at=created_at,
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="cli",
                queue_key=uuid4().hex,
                member_id=owner_id,
                created_at=created_at,
                updated_at=created_at,
            )
        )
    return _Workspace(workspace_id, owner_id, member_id, agent_id, conversation_id)


async def _stranger(state: _Workspace) -> UUID:
    stranger_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=stranger_id,
                workspace_id=state.workspace_id,
                email=f"{stranger_id.hex}@x.test",
                created_at=datetime(2026, 8, 2, tzinfo=UTC),
                updated_at=datetime(2026, 8, 2, tzinfo=UTC),
            )
        )
    return stranger_id


async def _shipped_agent(state: _Workspace, name: str) -> UUID:
    agent_id = uuid4()
    created_at = datetime(2026, 8, 1, tzinfo=UTC)
    with ws(state.workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.agent).values(
                    id=agent_id,
                    workspace_id=state.workspace_id,
                    name=name,
                    prompt="p",
                    model="claude-opus-4-8",
                    is_main=False,
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
    return agent_id


_TOOLS: dict[str, ToolDef] = {
    tool.name: tool
    for tool in turn_tools(
        (manifest(),),
        CredentialStore(fernet=Fernet(Fernet.generate_key())),
        audience=conversation_audience(None),
    )[0]
}


def _context(
    state: _Workspace,
    *,
    speaker_id: UUID | None = None,
    no_speaker: bool = False,
    agent_id: UUID | None = None,
) -> ToolContext:
    speaker = None if no_speaker else (speaker_id or state.owner_id)
    return ToolContext(
        sandbox=None,
        blob=None,
        turn=Turn(
            id=uuid4(),
            workspace_id=state.workspace_id,
            conversation_id=state.conversation_id,
            agent_id=agent_id or state.agent_id,
            seq=1,
            status="running",
            inbound="connect a gbrain source",
            created_at=datetime(2026, 8, 1, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=None,
        speaker_member_id=speaker,
        audience=conversation_audience(speaker),
        artifact_token_secret="",
        ext=context_for(NAME, DECLARED),
    )


def _manifest_text(
    name: str,
    *,
    repo: str | None = None,
    branch: str | None = None,
    root: str | None = None,
    shared: bool = False,
    resync: bool = False,
) -> str:
    spec: dict[str, object] = {}
    if repo is not None:
        spec["repo"] = repo
    if branch is not None:
        spec["branch"] = branch
    if root is not None:
        spec["root"] = root
    if shared:
        spec["shared"] = shared
    if resync:
        spec["resync"] = resync
    return yaml.safe_dump({"kind": GBRAIN_KIND, "name": name, "spec": spec})


async def _apply(ctx: ToolContext, manifest_text: str) -> dict[str, object]:
    tool = _TOOLS["object_apply"]
    result = await tool.handler(
        ctx,
        tool.input_model.model_validate({"manifest": manifest_text}),
    )
    assert result.is_error is False
    return json.loads(result.content[0].text)


async def _get(ctx: ToolContext, name: str) -> dict[str, object]:
    tool = _TOOLS["object_get"]
    result = await tool.handler(
        ctx,
        tool.input_model.model_validate({"kind": GBRAIN_KIND, "name": name}),
    )
    assert result.is_error is False
    return yaml.safe_load(result.content[0].text)


async def _list_names(ctx: ToolContext) -> list[str]:
    tool = _TOOLS["object_list"]
    result = await tool.handler(
        ctx,
        tool.input_model.model_validate({"kind": GBRAIN_KIND}),
    )
    assert result.is_error is False
    return [row["name"] for row in json.loads(result.content[0].text)["objects"]]


async def _rows(state: _Workspace) -> list[sa.RowMapping]:
    with ws(state.workspace_id), agent(state.agent_id):
        async with workspace_tx() as connection:
            return list(
                (
                    await connection.execute(
                        sa.select(tables.source)
                        .where(
                            tables.source.c.workspace_id == state.workspace_id,
                            tables.source.c.backend.in_(GBRAIN_BACKENDS),
                        )
                        .order_by(tables.source.c.id)
                    )
                )
                .mappings()
                .all()
            )


async def _granted_agents(state: _Workspace) -> set[UUID]:
    with ws(state.workspace_id), agent(state.agent_id):
        async with workspace_tx() as connection:
            return set(
                (
                    await connection.execute(
                        sa.select(tables.source_grant.c.agent_id).where(
                            tables.source_grant.c.workspace_id == state.workspace_id
                        )
                    )
                )
                .scalars()
                .all()
            )


def test_manifest_declares_the_gbrain_kind() -> None:
    declared = manifest()
    assert declared.tools == ()
    assert {kind.name for kind in declared.objects} == {GBRAIN_KIND}
    assert {source.backend for source in declared.sources} == set(GBRAIN_BACKENDS)
    assert {slot.name for slot in declared.credentials} == {GITHUB_TOKEN_SLOT}


async def test_member_registers_a_repo_source_privately_by_default(db: None) -> None:
    state = await _workspace()
    ctx = _context(state, speaker_id=state.member_id)
    name = gbrain_source_name(REPO, None, None)
    with ws(state.workspace_id), agent(state.agent_id):
        applied = await _apply(ctx, _manifest_text(name, repo=REPO))
        assert applied == {"kind": GBRAIN_KIND, "name": name, "result": "created"}
        fetched = await _get(ctx, name)
    [row] = await _rows(state)
    assert row["backend"] == GIT_BACKEND
    assert row["config"] == {"repo": REPO, "branch": None}
    assert row["subject"] == member_subject(state.member_id)
    assert row["owner_member_id"] == state.member_id
    assert await _granted_agents(state) == {state.agent_id}
    assert fetched["spec"] == {
        "repo": REPO,
        "branch": None,
        "root": None,
        "shared": False,
        "resync": False,
    }
    assert fetched["status"]["shared"] is False
    assert fetched["status"]["consecutive_errors"] == 0
    assert fetched["status"]["owner_member_id"] == str(state.member_id)
    assert fetched["status"]["next_sync_at"] is not None


async def test_the_model_registers_a_shared_branch_source_on_request(db: None) -> None:
    state = await _workspace()
    ctx = _context(state, speaker_id=state.member_id)
    name = gbrain_source_name(REPO, BRANCH, None)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(ctx, _manifest_text(name, repo=REPO, branch=BRANCH, shared=True))
        fetched = await _get(ctx, name)
    [row] = await _rows(state)
    assert row["config"] == {"repo": REPO, "branch": BRANCH}
    assert row["subject"] == SHARED_SUBJECT
    assert row["owner_member_id"] == state.member_id
    assert fetched["spec"]["shared"] is True
    assert "owner_member_id" not in fetched["status"]


async def test_wrong_name_refusal_hands_back_the_derived_name(db: None) -> None:
    state = await _workspace()
    ctx = _context(state, speaker_id=state.member_id)
    derived = gbrain_source_name(REPO, None, None)
    tool = _TOOLS["object_apply"]
    args = tool.input_model.model_validate({"manifest": _manifest_text("my-wiki", repo=REPO)})
    with ws(state.workspace_id), agent(state.agent_id), pytest.raises(ValueError, match=derived):
        await tool.handler(ctx, args)
    assert await _rows(state) == []


async def test_the_spec_names_exactly_one_origin(db: None) -> None:
    state = await _workspace()
    ctx = _context(state)
    tool = _TOOLS["object_apply"]
    for manifest_text in (
        _manifest_text("gbrain-deadbeef", repo=REPO, root=ROOT),
        _manifest_text("gbrain-deadbeef"),
    ):
        args = tool.input_model.model_validate({"manifest": manifest_text})
        with (
            ws(state.workspace_id),
            agent(state.agent_id),
            pytest.raises(ValueError, match="exactly one of repo or root"),
        ):
            await tool.handler(ctx, args)
    args = tool.input_model.model_validate(
        {
            "manifest": _manifest_text("gbrain-deadbeef", root=ROOT, branch=BRANCH),
        }
    )
    with (
        ws(state.workspace_id),
        agent(state.agent_id),
        pytest.raises(ValueError, match="only with repo"),
    ):
        await tool.handler(ctx, args)
    assert await _rows(state) == []


async def test_root_apply_is_refused_for_everyone(db: None) -> None:
    state = await _workspace()
    name = gbrain_source_name(None, None, ROOT)
    for ctx in (_context(state, speaker_id=state.member_id), _context(state)):
        with (
            ws(state.workspace_id),
            agent(state.agent_id),
            pytest.raises(VerbNotSupported, match="operator config"),
        ):
            await _apply(ctx, _manifest_text(name, root=ROOT))
    assert await _rows(state) == []


async def test_configured_root_lists_as_a_shared_source(db: None) -> None:
    state = await _workspace()
    await register_sources((SourceEntry(backend=FOLDER_BACKEND, config=SourceConfig(root=ROOT)),))
    name = gbrain_source_name(None, None, ROOT)
    ctx = _context(state, speaker_id=state.member_id)
    with ws(state.workspace_id), agent(state.agent_id):
        assert name in await _list_names(ctx)
        fetched = await _get(ctx, name)
    assert fetched["spec"]["root"] == ROOT
    assert fetched["spec"]["shared"] is True
    [row] = await _rows(state)
    assert row["backend"] == FOLDER_BACKEND
    assert row["subject"] == SHARED_SUBJECT
    assert row["owner_member_id"] is None


async def test_identical_reapply_grants_the_settled_source_to_the_calling_agent(db: None) -> None:
    state = await _workspace()
    name = gbrain_source_name(REPO, None, None)
    submitted = _manifest_text(name, repo=REPO)
    with ws(state.workspace_id), agent(state.agent_id):
        assert (await _apply(_context(state, speaker_id=state.member_id), submitted))[
            "result"
        ] == "created"
    rows = await _rows(state)
    assert await _granted_agents(state) == {state.agent_id}
    shipped = await _shipped_agent(state, "code-review")
    with ws(state.workspace_id), agent(shipped):
        applied = await _apply(
            _context(state, speaker_id=state.member_id, agent_id=shipped), submitted
        )
    assert applied == {"kind": GBRAIN_KIND, "name": name, "result": "updated"}
    assert await _granted_agents(state) == {state.agent_id, shipped}
    assert [row["id"] for row in await _rows(state)] == [row["id"] for row in rows]


async def test_unsharing_is_delete_and_recreate(db: None) -> None:
    state = await _workspace()
    ctx = _context(state, speaker_id=state.member_id)
    name = gbrain_source_name(REPO, None, None)
    tool = _TOOLS["object_apply"]
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(ctx, _manifest_text(name, repo=REPO, shared=True))
        args = tool.input_model.model_validate({"manifest": _manifest_text(name, repo=REPO)})
        with pytest.raises(VerbNotSupported, match="delete"):
            await tool.handler(ctx, args)
    [row] = await _rows(state)
    assert row["subject"] == SHARED_SUBJECT


async def test_sharing_a_private_source_restamps_its_row(db: None) -> None:
    state = await _workspace()
    ctx = _context(state, speaker_id=state.member_id)
    name = gbrain_source_name(REPO, None, None)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(ctx, _manifest_text(name, repo=REPO))
        flipped = await _apply(ctx, _manifest_text(name, repo=REPO, shared=True))
    assert flipped == {"kind": GBRAIN_KIND, "name": name, "result": "updated"}
    [row] = await _rows(state)
    assert row["subject"] == SHARED_SUBJECT
    assert row["owner_member_id"] == state.member_id


async def test_resync_pulls_the_sources_next_sync_to_now(db: None) -> None:
    state = await _workspace()
    ctx = _context(state, speaker_id=state.member_id)
    name = gbrain_source_name(REPO, None, None)
    tool = _TOOLS["object_apply"]
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(ctx, _manifest_text(name, repo=REPO))
        future = datetime(2027, 1, 1, tzinfo=UTC)
        async with workspace_tx() as connection:
            await connection.execute(sa.update(tables.source).values(next_sync_at=future))
        before = datetime.now(UTC)
        resynced = await _apply(ctx, _manifest_text(name, repo=REPO, resync=True))
        assert resynced == {"kind": GBRAIN_KIND, "name": name, "result": "updated"}
        fetched = await _get(ctx, name)
        assert fetched["spec"]["resync"] is False
        args = tool.input_model.model_validate(
            {
                "manifest": _manifest_text(name, repo=REPO, shared=True, resync=True),
            }
        )
        with pytest.raises(VerbNotSupported, match="a resync changes nothing else"):
            await tool.handler(ctx, args)
    [row] = await _rows(state)
    scheduled = row["next_sync_at"]
    if scheduled.tzinfo is None:
        scheduled = scheduled.replace(tzinfo=UTC)
    assert before - timedelta(seconds=5) <= scheduled <= datetime.now(UTC)


async def test_delete_removes_the_source_and_tombstones_its_pages(db: None) -> None:
    state = await _workspace()
    ctx = _context(state, speaker_id=state.member_id)
    name = gbrain_source_name(REPO, None, None)
    delete_tool = _TOOLS["object_delete"]
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(ctx, _manifest_text(name, repo=REPO))
        [row] = await _rows(state)
        page_id = uuid4()
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.page).values(
                    id=page_id,
                    workspace_id=state.workspace_id,
                    source_id=row["id"],
                    digest="sha256:x",
                    body_ref="pages/x",
                    subject=member_subject(state.member_id),
                    tombstone=False,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
        deleted = json.loads(
            (
                await delete_tool.handler(
                    ctx,
                    delete_tool.input_model.model_validate({"kind": GBRAIN_KIND, "name": name}),
                )
            )
            .content[0]
            .text
        )
        assert deleted["deleted"] is True
        assert deleted["spec"]["repo"] == REPO
        assert await _list_names(ctx) == []
        async with workspace_tx() as connection:
            page = (
                (
                    await connection.execute(
                        sa.select(tables.page.c.tombstone).where(tables.page.c.id == page_id)
                    )
                )
                .mappings()
                .one()
            )
    [row] = await _rows(state)
    assert row["removed_at"] is not None
    assert page["tombstone"] is True or page["tombstone"] == 1
    assert await _granted_agents(state) == set()


async def test_object_list_shows_only_visible_sources(db: None) -> None:
    state = await _workspace()
    stranger_id = await _stranger(state)
    member_ctx = _context(state, speaker_id=state.member_id)
    admin_ctx = _context(state)
    stranger_ctx = _context(state, speaker_id=stranger_id)
    private_name = gbrain_source_name(REPO, None, None)
    shared_name = gbrain_source_name(OTHER_REPO, None, None)
    get_tool = _TOOLS["object_get"]
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(member_ctx, _manifest_text(private_name, repo=REPO))
        await _apply(member_ctx, _manifest_text(shared_name, repo=OTHER_REPO, shared=True))
        assert await _list_names(stranger_ctx) == [shared_name]
        with pytest.raises(UnknownObject):
            await get_tool.handler(
                stranger_ctx,
                get_tool.input_model.model_validate(
                    {
                        "kind": GBRAIN_KIND,
                        "name": private_name,
                    }
                ),
            )
        for ctx in (member_ctx, admin_ctx):
            assert set(await _list_names(ctx)) == {private_name, shared_name}
            fetched = await _get(ctx, private_name)
            assert fetched["spec"]["repo"] == REPO


async def test_private_origin_of_another_member_is_refused_with_the_share_path(db: None) -> None:
    state = await _workspace()
    member_ctx = _context(state, speaker_id=state.member_id)
    stranger_ctx = _context(state, speaker_id=await _stranger(state))
    admin_ctx = _context(state, speaker_id=state.owner_id)
    name = gbrain_source_name(REPO, None, None)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(member_ctx, _manifest_text(name, repo=REPO))
        with pytest.raises(VerbNotSupported, match="registered privately by another member"):
            await _apply(stranger_ctx, _manifest_text(name, repo=REPO))
        applied = await _apply(admin_ctx, _manifest_text(name, repo=REPO))
        assert applied["result"] == "updated"
    [row] = await _rows(state)
    assert row["owner_member_id"] == state.member_id
