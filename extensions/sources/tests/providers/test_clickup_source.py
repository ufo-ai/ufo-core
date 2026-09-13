"""The ClickUp connector over a mock transport: the team root, the members collapsed into `users`,
the five-level hierarchy fanned out over the pages each parent row landed, `lists` reached under
both a folder and a space, the per-list task walk paged by an integer `?page`, and the
`date_updated` watermark advancing per list. A ClickUp id is unique across the workspace, so a page
of the four canonical streams keeps the address it had before the tree — `spaces/<id>`,
`lists/<id>`, `tasks/<id>`, no scope. Offline — a canned transport, no DB, no token, no broker."""

import json
from collections.abc import Callable, Mapping
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.clickup import (
    FOLDERS_FETCH_BUDGET,
    LISTS_FETCH_BUDGET,
    SPACES_FETCH_BUDGET,
    TASKS_FETCH_BUDGET,
    ClickUpConnector,
)

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped, SyncResult
from ufo.sdk.sources import (
    ConnectorBackend,
    ConnectorSourceConfig,
    ParentPages,
    ParentRecord,
    syncing_streams,
)

Landed = Mapping[str, tuple[ParentRecord, ...]]
ParentsReader = Callable[[Landed], ParentPages]
TEAM = {
    "id": "t1",
    "name": "Acme",
    "members": [{"user": {"id": "u1", "username": "Ada", "email": "ada@example.com"}}],
}
LIST_REF = "lists/l1"
TASKS_KEY = f"{LIST_REF}\n/list/l1/task"
FOLDER_LISTS_KEY = "folders/f1\n/folder/f1/list"
UPDATED = "1700000000200"
LANDED: Landed = {
    "teams": (ParentRecord(ref="teams/t1", fields={"id": "t1"}),),
    "spaces": (ParentRecord(ref="spaces/s1", fields={"id": "s1"}),),
    "folders": (ParentRecord(ref="folders/f1", fields={"id": "f1"}),),
    "lists": (ParentRecord(ref=LIST_REF, fields={"id": "l1", "name": "List"}),),
}
TASK_DIGEST = "sha256:8a7b89397400601b0d33c93b7205dcff2d2a687c8df9447b7d680aa3dd708ad1"


def _flat(result: SyncResult, ref: str) -> dict:
    body = next(page.body for page in result.pages if page.source_ref == ref)
    return json.loads(body.split("\n\n", 1)[1])


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handler = handler

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


async def _fetch(
    reader: ParentsReader,
    stream: str,
    handler: Callable[[httpx.Request], httpx.Response],
    cursor: str | None = None,
    landed: Landed = LANDED,
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler), parents=reader(landed))
    return await ConnectorBackend(connector=ClickUpConnector()).fetch(
        ConnectorSourceConfig(stream=stream), cursor, auth
    )


def _hierarchy_handler() -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.clickup.com"
        path = request.url.path
        if path.endswith("/team"):
            return httpx.Response(200, json={"teams": [TEAM]})
        if path.endswith("/team/t1/space"):
            return httpx.Response(200, json={"spaces": [{"id": "s1", "name": "Space"}]})
        if path.endswith("/space/s1/folder"):
            return httpx.Response(200, json={"folders": [{"id": "f1", "name": "Folder"}]})
        if path.endswith("/space/s1/list"):
            return httpx.Response(200, json={"lists": [{"id": "l2", "name": "Loose"}]})
        if path.endswith("/folder/f1/list"):
            return httpx.Response(200, json={"lists": [{"id": "l1", "name": "List"}]})
        if path.endswith("/list/l1/task"):
            if request.url.params.get("page") == "0":
                return httpx.Response(
                    200, json={"tasks": [{"id": "tk1", "name": "Do", "date_updated": UPDATED}]}
                )
            return httpx.Response(200, json={"tasks": []})
        return httpx.Response(404, json={"path": path})

    return handle


async def test_teams_are_the_root_of_the_hierarchy(parents_reader: ParentsReader) -> None:
    result = await _fetch(parents_reader, "teams", _hierarchy_handler())
    assert {page.source_ref for page in result.pages} == {"teams/t1"}
    assert result.snapshot is False
    assert "Acme" in result.pages[0].body


async def test_users_collapse_the_team_members(parents_reader: ParentsReader) -> None:
    result = await _fetch(parents_reader, "users", _hierarchy_handler())
    assert {page.source_ref for page in result.pages} == {"users/u1"}
    assert "ada@example.com" in result.pages[0].body


@pytest.mark.parametrize(
    ("stream", "path", "ref"),
    [
        ("spaces", "/api/v2/team/t1/space", "spaces/s1"),
        ("folders", "/api/v2/space/s1/folder", "folders/f1"),
        ("tasks", "/api/v2/list/l1/task", "tasks/tk1"),
    ],
)
async def test_each_level_reads_only_the_parent_that_landed(
    stream: str, path: str, ref: str, parents_reader: ParentsReader
) -> None:
    """A ClickUp id is unique across the workspace, so every canonical stream declares
    `key_scope="global"` and is addressed by that id alone. Each tick asks the one collection
    under the parent page that landed — the `/team` root the hierarchy used to be re-walked from on
    every one of them is gone."""
    calls: list[str] = []
    hierarchy = _hierarchy_handler()

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return hierarchy(request)

    result = await _fetch(parents_reader, stream, handle)

    assert set(calls) == {path}
    assert {page.source_ref for page in result.pages} == {ref}
    assert {page.source_identity for page in result.pages} == {ref}


async def test_lists_fan_out_over_both_the_folders_and_the_spaces_that_hold_them(
    parents_reader: ParentsReader,
) -> None:
    """ClickUp publishes a folder's lists and a space's folderless lists as two collections, so
    `lists` declares both edges. Each partition is its own request and its own cursor entry, and the
    two lists land as two pages under the one bare address the stream keys by."""
    calls: list[str] = []
    hierarchy = _hierarchy_handler()

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return hierarchy(request)

    result = await _fetch(parents_reader, "lists", handle)

    assert sorted(calls) == ["/api/v2/folder/f1/list", "/api/v2/space/s1/list"]
    assert {page.source_ref for page in result.pages} == {"lists/l1", "lists/l2"}
    assert _flat(result, "lists/l1")["folder_id"] == "f1"
    assert _flat(result, "lists/l2")["space_id"] == "s1"


async def test_a_list_under_a_folder_and_one_under_a_space_keep_separate_cursor_entries(
    parents_reader: ParentsReader,
) -> None:
    """The walk keys a partition by the collection it asks and of whom, so the two edges hold two
    entries: a pass resuming with the folder edge already marked walks the space edge and only the
    space edge. Keyed by the parent record alone, one edge's mark would stand for both and the
    folderless lists would never be read."""
    calls: list[str] = []
    hierarchy = _hierarchy_handler()

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return hierarchy(request)

    result = await _fetch(
        parents_reader, "lists", handle, cursor=json.dumps({FOLDER_LISTS_KEY: ""})
    )

    assert calls == ["/api/v2/space/s1/list"]
    assert {page.source_ref for page in result.pages} == {"lists/l2"}


async def test_tasks_advance_a_watermark_per_list(parents_reader: ParentsReader) -> None:
    result = await _fetch(parents_reader, "tasks", _hierarchy_handler())
    assert {page.source_ref for page in result.pages} == {"tasks/tk1"}
    assert json.loads(result.next_cursor) == {TASKS_KEY: UPDATED}


async def test_task_filter_compares_numeric_timestamps(parents_reader: ParentsReader) -> None:
    """`/list/{id}/task` takes no time bound, so the list's watermark filters the page here.
    ClickUp stamps `date_updated` as a millisecond epoch, which it sends as a string and sometimes
    as a number."""
    hierarchy = _hierarchy_handler()
    newer = "1700000001000"

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/list/l1/task"):
            tasks = [{"id": "tk1", "date_updated": newer}]
            return httpx.Response(
                200, json={"tasks": tasks if request.url.params.get("page") == "0" else []}
            )
        return hierarchy(request)

    first = await _fetch(
        parents_reader, "tasks", handle, cursor=json.dumps({TASKS_KEY: "1700000000999"})
    )
    assert {page.source_ref for page in first.pages} == {"tasks/tk1"}
    assert json.loads(first.next_cursor) == {TASKS_KEY: newer}
    second = await _fetch(parents_reader, "tasks", handle, cursor=first.next_cursor)
    assert second.pages == ()
    assert json.loads(second.next_cursor) == {TASKS_KEY: newer}


def _shaped_handler() -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/list/l1/task"):
            if request.url.params.get("page") == "0":
                return httpx.Response(
                    200,
                    json={
                        "tasks": [
                            {
                                "id": "tk1",
                                "name": "Do",
                                "status": {"status": "in progress"},
                                "due_date": "1700000001700",
                                "date_created": "1700000000100",
                                "date_updated": UPDATED,
                            }
                        ]
                    },
                )
            return httpx.Response(200, json={"tasks": []})
        if path.endswith("/list/l1/comment"):
            return httpx.Response(
                200,
                json={
                    "comments": [
                        {
                            "id": "cm1",
                            "comment_text": "nice work",
                            "user": {"id": "u2", "username": "Bo"},
                            "date": "1700000000300",
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"path": path})

    return handle


async def test_list_comments_flatten_derive_body_author_and_parent(
    parents_reader: ParentsReader,
) -> None:
    record = _flat(
        await _fetch(parents_reader, "list_comments", _shaped_handler()), "list_comments/l1/cm1"
    )
    assert record["body"] == "nice work"
    assert record["author"] == "Bo"
    assert record["parent_external_id"] == "l1"


async def test_a_task_carries_the_list_it_was_read_from(parents_reader: ParentsReader) -> None:
    record = _flat(await _fetch(parents_reader, "tasks", _shaped_handler()), "tasks/tk1")
    assert record["list_id"] == "l1"
    assert record["list_name"] == "List"


async def test_a_task_page_renders_what_it_rendered_before_the_tree(
    parents_reader: ParentsReader,
) -> None:
    """A task's body names the list it sits in: the id and the name both ride the edge, neither
    being a value the page's address carries. The digest pins the rendered body, so a field that
    stops reaching a task fails here rather than re-deriving every task page on the next deploy."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/list/l1/task"):
            tasks = (
                [
                    {
                        "id": "tk1",
                        "name": "Do",
                        "status": {"status": "in progress"},
                        "due_date": "1700000001700",
                        "date_created": "1700000000100",
                        "date_updated": UPDATED,
                    }
                ]
                if request.url.params.get("page") == "0"
                else []
            )
            return httpx.Response(200, json={"tasks": tasks})
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch(parents_reader, "tasks", handle)

    assert result.pages[0].digest == TASK_DIGEST


async def test_a_list_page_projects_the_fields_the_streams_under_it_read(
    parents_reader: ParentsReader,
) -> None:
    """The carried name reaches a task only if the list page holds it, so the projection carries
    both what the path renders and what the edges carry."""
    result = await _fetch(parents_reader, "lists", _hierarchy_handler())
    page = next(page for page in result.pages if page.source_ref == "lists/l1")

    assert page.parent_fields == {"id": "l1", "name": "List"}


async def test_goals_land_the_ones_a_goal_folder_holds(parents_reader: ParentsReader) -> None:
    """ClickUp answers `/team/{id}/goal` with two required arrays — the team's loose goals under
    `goals`, and the goals filed in a goal folder under `folders[].goals`
    (https://developer.clickup.com/reference/getgoals). Reading `goals` alone drops every goal a
    workspace files in a folder, which is where a workspace that uses folders keeps most of
    them."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/team/t1/goal"):
            return httpx.Response(
                200,
                json={
                    "goals": [{"id": "g1", "name": "Ship the tree"}],
                    "folders": [
                        {
                            "id": "gf1",
                            "name": "Q3",
                            "goals": [{"id": "g2", "name": "Cut sync cost"}],
                        },
                        {"id": "gf2", "name": "Empty", "goals": []},
                    ],
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch(parents_reader, "goals", handle)

    assert {page.source_ref for page in result.pages} == {"goals/t1/g1", "goals/t1/g2"}
    assert "team_id" not in _flat(result, "goals/t1/g2")


def test_the_catalog_registers_every_ancestor_the_canonical_streams_fan_from() -> None:
    streams = {stream.name: stream for stream in ClickUpConnector().streams()}

    assert syncing_streams(list(streams.values())) == {
        "teams",
        "spaces",
        "folders",
        "lists",
        "tasks",
    }
    assert streams["teams"].parents == ()
    assert [(edge.stream, edge.path) for edge in streams["lists"].parents] == [
        ("folders", "/folder/{id}/list"),
        ("spaces", "/space/{id}/list"),
    ]
    assert [name for name, spec in streams.items() if spec.key_scope == "global"] == [
        "spaces",
        "folders",
        "lists",
        "tasks",
    ]
    assert not streams["teams"].indexed
    assert streams["tasks"].indexed


async def test_a_refused_team_listing_is_skipped_rather_than_failed(
    parents_reader: ParentsReader,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"err": "OAUTH_017"})

    with pytest.raises(StreamSkipped):
        await _fetch(parents_reader, "teams", handle)


def _parents(stream: str, count: int) -> tuple[ParentRecord, ...]:
    return tuple(
        ParentRecord(ref=f"{stream}/{stream[0]}{n}", fields={"id": f"{stream[0]}{n}", "name": "P"})
        for n in range(count)
    )


@pytest.mark.parametrize(
    ("stream", "landed", "budget"),
    [
        ("spaces", {"teams": _parents("teams", 8)}, SPACES_FETCH_BUDGET),
        ("folders", {"spaces": _parents("spaces", 13)}, FOLDERS_FETCH_BUDGET),
        (
            "lists",
            {"folders": _parents("folders", 10), "spaces": _parents("spaces", 8)},
            LISTS_FETCH_BUDGET,
        ),
        ("tasks", {"lists": _parents("lists", 18)}, TASKS_FETCH_BUDGET),
    ],
)
async def test_a_tick_asks_no_more_parents_than_the_row_budgets(
    stream: str, landed: Landed, budget: int, parents_reader: ParentsReader
) -> None:
    """ClickUp allows 100 requests a minute per token on its Free, Unlimited and Business plans, and
    every canonical stream fans over a catalog that can be wider than that — a list's tasks cost at
    least two requests a tick, since `?page` runs to the first empty answer. Each row's budget
    bounds the tick; a truncated pass resumes after the parent it reached."""
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        parent = request.url.path.rsplit("/", 2)[-2]
        if request.url.path.endswith("/task"):
            tasks = (
                [{"id": f"tk-{parent}", "name": "Do", "date_updated": UPDATED}]
                if request.url.params.get("page") == "0"
                else []
            )
            return httpx.Response(200, json={"tasks": tasks})
        return httpx.Response(200, json={stream: [{"id": f"{stream}-{parent}", "name": "P"}]})

    first = await _fetch(parents_reader, stream, handle, landed=landed)
    assert len(set(asked)) == budget

    asked.clear()
    await _fetch(parents_reader, stream, handle, first.next_cursor, landed=landed)
    assert len(set(asked)) == sum(len(records) for records in landed.values()) - budget
