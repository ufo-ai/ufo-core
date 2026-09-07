"""The `gbrain_source` object kind end to end: registration through the object verbs.

Every mutation drives the real tool dispatch (`turn_tools` over the extension's manifest), and
assertions read back through the durable `source` and `connection` rows and the verbs' own results:
derived names, the one-origin spec refusals, the connection each origin gets of its own,
private-by-default registration, the operator-config refusal on a directory root, resync, and
delete disconnecting one origin and leaving its neighbour alone. Reads show shared sources plus the
member's own — a workspace admin sees all."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import yaml
from cryptography.fernet import Fernet
from ufo_ext_gbrain.folder import FOLDER_BACKEND, GbrainFolderConfig
from ufo_ext_gbrain.git import GIT_BACKEND, GITHUB_TOKEN_SLOT, GbrainGitConfig
from ufo_ext_gbrain.manifest import NAME, manifest
from ufo_ext_gbrain.objects import GBRAIN_KIND, gbrain_source_name

from ufo.config import SourceConfig, SourceEntry
from ufo.db import workspace_tx
from ufo.host.ext.loader import turn_tools
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.access.grants import GrantStore
from ufo.runtime.agent_scope import agent
from ufo.runtime.ext.context import context_for
from ufo.runtime.objects import UnknownObject
from ufo.runtime.sources.sync import register_sources
from ufo.runtime.tools.registry import ToolDef
from ufo.runtime.turns.subjects import SHARED_SUBJECT, member_subject
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import conversation_audience
from ufo.sdk.objects import VerbNotSupported
from ufo.sdk.sources import feed_handle
from ufo.sdk.tools import ToolContext

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

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
        grants=GrantStore(),
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
        tool.input_model.model_validate({"ref": f"{GBRAIN_KIND}/{name}"}),
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


async def _connections(state: _Workspace) -> list[sa.RowMapping]:
    with ws(state.workspace_id), agent(state.agent_id):
        async with workspace_tx() as connection:
            return list(
                (
                    await connection.execute(
                        sa.select(tables.connection)
                        .where(
                            tables.connection.c.workspace_id == state.workspace_id,
                            tables.connection.c.provider.in_(GBRAIN_BACKENDS),
                        )
                        .order_by(tables.connection.c.provider)
                    )
                )
                .mappings()
                .all()
            )


async def _seed_page(state: _Workspace, source_id: UUID, subject: str) -> UUID:
    page_id = uuid4()
    with ws(state.workspace_id), agent(state.agent_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.page).values(
                    id=page_id,
                    workspace_id=state.workspace_id,
                    source_id=source_id,
                    digest="sha256:x",
                    body_ref=f"pages/{page_id}",
                    subject=subject,
                    tombstone=False,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
    return page_id


async def _page_ids(state: _Workspace) -> list[UUID]:
    return [row["id"] for row in await _pages(state)]


async def _page_subjects(state: _Workspace) -> list[str]:
    return [row["subject"] for row in await _pages(state)]


async def _pages(state: _Workspace) -> list[sa.RowMapping]:
    with ws(state.workspace_id), agent(state.agent_id):
        async with workspace_tx() as connection:
            return list(
                (
                    await connection.execute(
                        sa.select(tables.page)
                        .where(tables.page.c.workspace_id == state.workspace_id)
                        .order_by(tables.page.c.id)
                    )
                )
                .mappings()
                .all()
            )


def test_manifest_declares_the_gbrain_kind() -> None:
    declared = manifest()
    assert declared.tools == ()
    assert {kind.name for kind in declared.objects} == {GBRAIN_KIND}
    assert {source.backend for source in declared.sources} == set(GBRAIN_BACKENDS)
    assert {slot.name for slot in declared.credentials} == {GITHUB_TOKEN_SLOT}


async def test_member_registers_a_repo_source_privately_by_default(db: None) -> None:
    """The row the register lands and the authority behind it: a connection of this origin's own,
    keyed by the origin config's `feed_handle`, owned by the registering member and private until
    they say otherwise."""
    state = await _workspace()
    ctx = _context(state, speaker_id=state.member_id)
    name = gbrain_source_name(REPO, None, None)
    with ws(state.workspace_id), agent(state.agent_id):
        applied = await _apply(ctx, _manifest_text(name, repo=REPO))
        assert applied == {"kind": GBRAIN_KIND, "name": name, "result": "created"}
        fetched = await _get(ctx, name)
    [row] = await _rows(state)
    [held] = await _connections(state)
    assert row["backend"] == GIT_BACKEND
    assert row["config"] == {"repo": REPO, "branch": None}
    assert row["connection_id"] == held["id"]
    assert held["provider"] == GIT_BACKEND
    assert held["account_id"] == feed_handle(GbrainGitConfig(repo=REPO, branch=None))
    assert held["owner_member_id"] == state.member_id
    assert held["shared"] is False or held["shared"] == 0
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


async def test_each_origin_gets_a_connection_of_its_own(db: None) -> None:
    """One connection per origin, keyed by the origin config's `feed_handle` — which is what makes
    deleting one repository leave the next one alone, since removal is disconnecting that
    connection."""
    state = await _workspace()
    ctx = _context(state, speaker_id=state.member_id)
    first = gbrain_source_name(REPO, None, None)
    second = gbrain_source_name(OTHER_REPO, None, None)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(ctx, _manifest_text(first, repo=REPO))
        await _apply(ctx, _manifest_text(second, repo=OTHER_REPO))
    rows = await _rows(state)
    connections = await _connections(state)
    assert {held["account_id"] for held in connections} == {
        feed_handle(GbrainGitConfig(repo=REPO)),
        feed_handle(GbrainGitConfig(repo=OTHER_REPO)),
    }
    assert len({row["connection_id"] for row in rows}) == 2


async def test_a_boot_registered_root_and_the_kinds_apply_settle_on_one_connection(
    db: None,
) -> None:
    """A root the deploy's `[[sources]]` config registers at boot and the same root named through
    the kind are one feed: the boot path keys its connection by `feed_handle` of `SourceConfig`,
    the kind derives the same handle from `GbrainFolderConfig`, so the kind reads the boot row as
    its own object and a member's apply of that spec settles on it — one connection, one row, and
    nothing minted beside them."""
    state = await _workspace()
    with ws(state.workspace_id):
        await register_sources(
            (SourceEntry(backend=FOLDER_BACKEND, config=SourceConfig(root=ROOT)),)
        )
    [held] = await _connections(state)
    assert held["account_id"] == feed_handle(GbrainFolderConfig(root=ROOT))
    assert held["owner_member_id"] is None
    assert held["shared"] is True or held["shared"] == 1

    name = gbrain_source_name(None, None, ROOT)
    ctx = _context(state, speaker_id=state.member_id)
    with ws(state.workspace_id), agent(state.agent_id):
        applied = await _apply(ctx, _manifest_text(name, root=ROOT, shared=True))
        fetched = await _get(ctx, name)
    assert applied == {"kind": GBRAIN_KIND, "name": name, "result": "updated"}
    assert fetched["spec"]["root"] == ROOT
    [row] = await _rows(state)
    assert row["connection_id"] == held["id"]
    assert await _connections(state) == [held]


async def test_wrong_name_refusal_hands_back_the_derived_name(db: None) -> None:
    state = await _workspace()
    ctx = _context(state, speaker_id=state.member_id)
    derived = gbrain_source_name(REPO, None, None)
    tool = _TOOLS["object_apply"]
    args = tool.input_model.model_validate({"manifest": _manifest_text("my-wiki", repo=REPO)})
    with ws(state.workspace_id), agent(state.agent_id), pytest.raises(ValueError, match=derived):
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


async def test_identical_reapply_settles_on_the_row_and_changes_nothing(db: None) -> None:
    """The name derives from the origin, so a submit the workspace already holds names the same
    origin: the row, its connection and its disclosure all stand exactly as they were, from a
    second agent's turn as much as the first's."""
    state = await _workspace()
    name = gbrain_source_name(REPO, None, None)
    submitted = _manifest_text(name, repo=REPO)
    with ws(state.workspace_id), agent(state.agent_id):
        assert (await _apply(_context(state, speaker_id=state.member_id), submitted))[
            "result"
        ] == "created"
    before, held = await _rows(state), await _connections(state)
    shipped = await _shipped_agent(state, "code-review")
    with ws(state.workspace_id), agent(shipped):
        applied = await _apply(
            _context(state, speaker_id=state.member_id, agent_id=shipped), submitted
        )
    assert applied == {"kind": GBRAIN_KIND, "name": name, "result": "updated"}
    assert await _rows(state) == before
    assert await _connections(state) == held


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
    [held] = await _connections(state)
    assert held["shared"] is True or held["shared"] == 1


async def test_sharing_a_private_source_restamps_its_pages(db: None) -> None:
    """The flag is the disclosure, so widening it carries what the source already synced with it:
    the connection flips and every live page it holds is restamped to the shared subject."""
    state = await _workspace()
    ctx = _context(state, speaker_id=state.member_id)
    name = gbrain_source_name(REPO, None, None)
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(ctx, _manifest_text(name, repo=REPO))
        [row] = await _rows(state)
        await _seed_page(state, row["id"], member_subject(state.member_id))
        flipped = await _apply(ctx, _manifest_text(name, repo=REPO, shared=True))
        fetched = await _get(ctx, name)
    assert flipped == {"kind": GBRAIN_KIND, "name": name, "result": "updated"}
    assert fetched["spec"]["shared"] is True
    [held] = await _connections(state)
    assert held["shared"] is True or held["shared"] == 1
    assert held["owner_member_id"] == state.member_id
    assert await _page_subjects(state) == [SHARED_SUBJECT]


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
            {"manifest": _manifest_text(name, repo=REPO, shared=True, resync=True)}
        )
        with pytest.raises(VerbNotSupported, match="a resync changes nothing else"):
            await tool.handler(ctx, args)
    [row] = await _rows(state)
    scheduled = row["next_sync_at"]
    if scheduled.tzinfo is None:
        scheduled = scheduled.replace(tzinfo=UTC)
    assert before - timedelta(seconds=5) <= scheduled <= datetime.now(UTC)


async def test_delete_disconnects_one_origin_and_leaves_its_neighbour(db: None) -> None:
    """Removal is disconnecting that origin's own connection: its source row and every page it
    synced follow by cascade, and the repository registered beside it is untouched — which is the
    whole reason each origin carries a connection rather than sharing the backend's."""
    state = await _workspace()
    ctx = _context(state, speaker_id=state.member_id)
    name = gbrain_source_name(REPO, None, None)
    other = gbrain_source_name(OTHER_REPO, None, None)
    delete_tool = _TOOLS["object_delete"]
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(ctx, _manifest_text(name, repo=REPO))
        await _apply(ctx, _manifest_text(other, repo=OTHER_REPO))
        [row] = [source for source in await _rows(state) if source["config"]["repo"] == REPO]
        [kept] = [source for source in await _rows(state) if source["config"]["repo"] == OTHER_REPO]
        page_id = await _seed_page(state, row["id"], member_subject(state.member_id))
        kept_page = await _seed_page(state, kept["id"], member_subject(state.member_id))
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
        assert await _list_names(ctx) == [other]
    assert [source["config"]["repo"] for source in await _rows(state)] == [OTHER_REPO]
    assert [held["account_id"] for held in await _connections(state)] == [
        feed_handle(GbrainGitConfig(repo=OTHER_REPO))
    ]
    assert await _page_ids(state) == [kept_page]
    assert page_id not in await _page_ids(state)


async def test_delete_admits_the_registrar_and_an_admin_only(db: None) -> None:
    state = await _workspace()
    name = gbrain_source_name(REPO, None, None)
    delete_tool = _TOOLS["object_delete"]
    args = delete_tool.input_model.model_validate({"kind": GBRAIN_KIND, "name": name})
    with ws(state.workspace_id), agent(state.agent_id):
        await _apply(_context(state, speaker_id=state.member_id), _manifest_text(name, repo=REPO))
        stranger = _context(state, speaker_id=await _stranger(state))
        with pytest.raises(UnknownObject):
            await delete_tool.handler(stranger, args)
        assert len(await _rows(state)) == 1
        await delete_tool.handler(_context(state), args)
    assert await _rows(state) == []
    assert await _connections(state) == []


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
                get_tool.input_model.model_validate({"ref": f"{GBRAIN_KIND}/{private_name}"}),
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
    [held] = await _connections(state)
    assert held["owner_member_id"] == state.member_id
