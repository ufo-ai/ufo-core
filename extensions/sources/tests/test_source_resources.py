"""The map between a link and the clauses that wake a conversation about it, one rule set per
provider.

`canonical_resource` reads a link against the provider that owns it and answers the one URL a
narrowed trigger is written from. `resource_clauses` answers the other direction — the JMESPath
clauses that select the pages about that resource, one per event — and `default_clauses` the
provider's own set for a whole feed. `compile_when` decides one clause against one replayed page.

The trigger those rules serve is a `source_trigger` row carrying its clauses, so the store tests
below drive `SourceTriggerStore` against a conversation holding the whole feed of one connection and
one resource of it at once — the two rows the key admits — and pin the order each read hands them
back in. The last one disconnects the account and reads the rows back gone, which is the only thing
that ever removes a trigger."""

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from ufo_ext_sources.clauses import (
    WHEN_CLAUSE_MAX_CHARS,
    WHEN_CLAUSES_MAX,
    ClauseFailed,
    compile_when,
)
from ufo_ext_sources.manifest import NAME
from ufo_ext_sources.registry import CONNECTORS
from ufo_ext_sources.resources import (
    canonical_resource,
    default_clauses,
    resource_clauses,
    resource_identity,
    resource_scope,
)
from ufo_ext_sources.triggers import SourceTrigger, SourceTriggerStore, source_trigger

from ufo.db import workspace_tx
from ufo.runtime.agent_scope import agent
from ufo.runtime.ext.context import context_for
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.sdk.audience import SHARED_AUDIENCE
from ufo.sdk.sources import PageChange

GITHUB = "github"
PR = "https://github.com/acme/ufo/pull/1684"
ISSUE = "https://github.com/acme/ufo/issues/1684"
OTHER_PR = "https://github.com/acme/ufo/pull/1685"
CONNECTION = UUID("2f7c0f5e-1d0a-4c2b-9d3f-6b1f9a0c5e11")
CONVERSATION = UUID("29c88018-22cd-4e44-bb1e-7eae9cf5bf43")


def _repo(resource: str) -> str:
    """The repository a link names, which is the spelling the offer hands the clause builder."""
    return resource_scope(GITHUB, resource) or ""


def _trigger(resource: str = "") -> SourceTrigger:
    now = datetime(2026, 7, 20, tzinfo=UTC)
    return SourceTrigger(
        id=uuid4(),
        conversation_id=CONVERSATION,
        agent_id=uuid4(),
        connection_id=CONNECTION,
        name="watch",
        description=resource or "The whole feed",
        when=resource_clauses(GITHUB, resource, _repo(resource))
        if resource
        else default_clauses(GITHUB),
        fault=None,
        delivery="current",
        paused=False,
        created_by_member_id=None,
        internet_access=None,
        created_at=now,
        updated_at=now,
    )


def _page(body: str, stream: str = "pull_requests") -> PageChange:
    now = datetime(2026, 7, 20, tzinfo=UTC)
    return PageChange(
        page_id=uuid4(),
        source_id=uuid4(),
        connection_id=uuid4(),
        provider="github",
        subject="shared",
        stream=stream,
        title="a page",
        body=body,
        digest=f"sha256:{uuid4().hex}",
        revision=1,
        tombstone=False,
        indexed=True,
        created_at=now,
        as_of=now,
        changed_at=now,
    )


def test_every_spelling_of_a_pull_request_link_canonicalizes_to_one_url() -> None:
    """GitHub answers one pull request under every spelling of its repository, under its API path,
    and under every sub-page and fragment of its own page — so one watch is one row."""
    for spelling in (
        "https://github.com/Acme/ufo/pull/1684",
        "https://www.github.com/acme/ufo/pull/1684/files",
        "https://github.com/acme/ufo/pull/1684#issuecomment-5548599845",
        "https://github.com/acme/ufo/pull/1684?diff=split",
        "https://api.github.com/repos/acme/ufo/pulls/1684",
        " https://github.com/acme/ufo/pull/1684 ",
    ):
        assert canonical_resource(GITHUB, spelling) == PR


def test_a_mixed_case_repository_keeps_the_spelling_its_records_carry() -> None:
    """GitHub answers its own stored case in every URL a record carries, and the canonical link
    case-folds the repository so two spellings stay one trigger."""
    link = "https://github.com/Acme/ufo/pull/1684"
    clauses = resource_clauses(GITHUB, canonical_resource(GITHUB, link) or "", _repo(link))

    assert _repo(link) == "Acme/ufo"
    assert canonical_resource(GITHUB, link) == PR
    assert any("https://github.com/Acme/ufo/pull/1684" in clause for clause in clauses)
    assert not any("github.com/acme/ufo/pull" in clause for clause in clauses)
    landed = '{"url": "https://github.com/Acme/ufo/pull/1684", "state": "MERGED"}'
    assert any(compile_when(clauses).met("pull_requests", "a page", _landed("x", landed)))


def test_an_issue_watch_reads_the_fields_a_rest_issue_record_carries() -> None:
    """`issues` is a REST stream: its record holds the API link in `url`, the web link in
    `html_url`, and a lower-case `state`."""
    clauses = resource_clauses(GITHUB, ISSUE, _repo(ISSUE))
    closed = (
        '{"url": "https://api.github.com/repos/acme/ufo/issues/1684", '
        '"html_url": "https://github.com/acme/ufo/issues/1684", "state": "closed"}'
    )
    still_open = closed.replace('"closed"', '"open"')

    assert any(compile_when(clauses).met("issues", "a page", _landed("issues", closed)))
    assert not any(compile_when(clauses).met("issues", "a page", _landed("issues", still_open)))


def test_the_whole_feed_set_reads_an_issue_state_in_the_case_rest_sends() -> None:
    """The same spelling hazard on the whole-feed set, which no link goes through."""
    assert any(_met(GITHUB, "issues", '{"state": "closed"}'))
    assert not any(_met(GITHUB, "issues", '{"state": "CLOSED"}'))


def test_two_stream_narrowings_of_one_resource_keep_different_clauses() -> None:
    """Every clause names its stream, which is what lets a narrowing keep the clauses of the
    streams it named."""
    clauses = resource_clauses(GITHUB, PR, _repo(PR))

    def named(*streams: str) -> tuple[str, ...]:
        wanted = tuple(f"stream == '{stream}'" for stream in streams)
        return tuple(clause for clause in clauses if clause.startswith(wanted))

    assert all(clause.startswith("stream == '") for clause in clauses)
    assert named("pull_requests") != named("comments")
    assert named("pull_requests") and named("comments")


def test_a_pull_request_and_the_issue_of_its_number_are_one_resource() -> None:

    def one(first: str, second: str, provider: str = GITHUB) -> bool:
        return resource_identity(provider, first) == resource_identity(provider, second)

    assert one(PR, PR)
    assert one(PR, ISSUE)
    assert not one(PR, "https://github.com/acme/ufo/pull/16840")
    assert not one(PR, "https://github.com/acme/other/pull/1684")
    linear = "https://linear.app/acme/issue/UFO-1"
    assert resource_identity("linear", linear) == linear
    assert not one(linear, "https://linear.app/acme/issue/UFO-2", provider="linear")


def test_an_issue_link_canonicalizes_under_its_own_path() -> None:
    assert (
        canonical_resource(GITHUB, "https://api.github.com/repos/Acme/ufo/issues/7")
        == "https://github.com/acme/ufo/issues/7"
    )


def test_a_link_to_anything_but_a_pull_request_or_issue_names_nothing() -> None:
    """A repository is not a resource a trigger narrows to — a thread that names a repository is
    not asking to hear every push it carries, and the whole feed already has its own trigger."""
    for link in (
        "https://github.com/acme/ufo",
        "https://github.com/acme/ufo/pulls",
        "https://github.com/acme/ufo/commit/60b277f01b86d3ec672641cc6286ef70699284fc",
        "https://github.com/acme/ufo/pull/1684abc",
        "http://github.com/acme/ufo/pull/1684",
        "https://linear.app/acme/issue/UFO-1",
    ):
        assert canonical_resource(GITHUB, link) is None


def test_a_provider_without_resource_rules_names_nothing() -> None:
    assert canonical_resource("linear", "https://linear.app/acme/issue/UFO-1") is None
    assert resource_clauses("linear", "https://linear.app/acme/issue/UFO-1", "") == ()


def test_a_provider_without_resource_rules_wakes_once_per_new_page() -> None:
    """A trigger on a feed nothing can read clauses for still has to do something."""
    assert default_clauses("linear") == ("`true`",)
    assert _met("linear", "anything", '{"id": 1}') == (True,)


def _met(provider: str, stream: str, record: str, resource: str = "") -> tuple[bool, ...]:
    """Which of a trigger's clauses one landed page meets."""
    clauses = (
        resource_clauses(provider, resource, _repo(resource))
        if resource
        else default_clauses(provider)
    )
    return compile_when(clauses).met(stream, "a page", _landed(stream, record))


def _landed(stream: str, record: str) -> str:
    """One page as the connector renders it: the heading over the record's JSON."""
    return f"# {GITHUB} {stream}: a page\n\n{record}"


def test_the_clauses_for_a_resource_select_the_pages_the_provider_links_to_it() -> None:
    """Each clause reads a field GitHub's own record carries: the pull request's `url`, a comment's
    `issue_url`, a review comment's `pull_request_url`, a workflow run's `pull_requests[].url`."""
    for stream, record in (
        ("pull_requests", f'{{"url": "{PR}", "state": "MERGED"}}'),
        ("pull_requests", f'{{"url": "{PR}", "checks": {{"state": "SUCCESS"}}}}'),
        (
            "comments",
            '{"issue_url": "https://api.github.com/repos/acme/ufo/issues/1684"}',
        ),
        (
            "review_comments",
            '{"pull_request_url": "https://api.github.com/repos/acme/ufo/pulls/1684"}',
        ),
        (
            "workflow_runs",
            '{"status": "completed", "conclusion": "success", "pull_requests": '
            '[{"url": "https://api.github.com/repos/acme/ufo/pulls/1684"}]}',
        ),
    ):
        assert any(_met(GITHUB, stream, record, resource=PR)), (stream, record)


def test_a_page_about_another_resource_meets_no_clause() -> None:
    for stream, record in (
        ("pull_requests", '{"url": "https://github.com/acme/ufo/pull/16840"}'),
        ("pull_requests", '{"url": "https://github.com/acme/other/pull/1684"}'),
        ("pull_requests", '{"url": "https://github.com/acme/ufo/pull/1685"}'),
        ("comments", '{"issue_url": "https://api.github.com/repos/other/ufo/issues/1684"}'),
        ("pull_requests", "{}"),
    ):
        assert not any(_met(GITHUB, stream, record, resource=PR)), (stream, record)


def test_a_body_carrying_no_record_meets_no_clause() -> None:
    """A tombstone and a folder source's file text are not JSON objects, so `page` is null and a
    clause reaching into it answers null, which JMESPath calls no match."""
    clauses = compile_when(resource_clauses(GITHUB, PR, _repo(PR)))
    assert not any(clauses.met("pull_requests", "a page", ""))
    assert not any(clauses.met("pull_requests", "a page", "# heading\n\nnot json"))


def test_the_whole_feed_set_is_the_comments_reviews_finished_runs_and_closures() -> None:
    """What a member watches a feed for: somebody said something, the checks answered, or it
    landed."""
    for stream, record in (
        ("comments", '{"body": "lgtm"}'),
        ("review_comments", '{"body": "one nit"}'),
        ("workflow_runs", '{"status": "completed", "conclusion": "success"}'),
        ("workflow_runs", '{"status": "completed", "conclusion": "failure"}'),
        ("pull_requests", '{"state": "OPEN", "checks": {"state": "FAILURE"}}'),
        ("pull_requests", '{"state": "OPEN", "checks": {"state": "SUCCESS"}}'),
        ("pull_requests", '{"state": "MERGED"}'),
        ("pull_requests", '{"state": "CLOSED"}'),
        ("issues", '{"state": "closed"}'),
    ):
        assert any(_met(GITHUB, stream, record)), (stream, record)


def test_the_whole_feed_set_leaves_every_other_change() -> None:
    """An open pull request moves its page on a title edit, a label, a push and a review request,
    and a run moves its page at every step before it concludes."""
    for stream, record in (
        ("pull_requests", '{"state": "OPEN", "isDraft": true}'),
        ("pull_requests", '{"state": "OPEN", "checks": {"state": "PENDING"}}'),
        ("pull_requests", '{"state": "OPEN", "checks": null}'),
        ("issues", '{"state": "open"}'),
        ("workflow_runs", '{"status": "in_progress", "conclusion": null}'),
        ("workflow_runs", '{"status": "completed", "conclusion": "skipped"}'),
        ("releases", '{"tag_name": "v1"}'),
        ("repositories", '{"full_name": "acme/ufo"}'),
        ("organizations", '{"login": "acme"}'),
    ):
        assert not any(_met(GITHUB, stream, record)), (stream, record)


def test_a_merge_on_a_green_pull_request_flips_a_clause_of_its_own() -> None:
    green = f'{{"url": "{PR}", "state": "OPEN", "checks": {{"state": "SUCCESS"}}}}'
    merged = f'{{"url": "{PR}", "state": "MERGED", "checks": {{"state": "SUCCESS"}}}}'
    clauses = resource_clauses(GITHUB, PR, _repo(PR))

    before = _met(GITHUB, "pull_requests", green, resource=PR)
    after = _met(GITHUB, "pull_requests", merged, resource=PR)

    assert clauses[0].endswith("contains(['CLOSED', 'MERGED'], page.state)")
    assert (before[0], after[0]) == (False, True)
    assert (before[1], after[1]) == (True, True)


def test_a_clause_raising_a_python_error_is_still_the_trigger_s_fault() -> None:
    clauses = compile_when(("contains(page.url, `7`)",))

    with pytest.raises(ClauseFailed, match=r"contains\(page\.url"):
        clauses.met("pull_requests", "a page", _landed("pull_requests", '{"url": "https://x/7"}'))


def test_a_clause_that_raises_names_the_clause_and_the_stream() -> None:
    """JMESPath types its functions at call time, so a clause reaching into a field the record
    does not carry raises rather than answering false."""
    clauses = compile_when(("contains(page.labels, 'urgent')",))

    with pytest.raises(ClauseFailed, match=r"contains\(page\.labels"):
        clauses.met("pull_requests", "a page", _landed("pull_requests", '{"state": "OPEN"}'))


def test_a_clause_that_does_not_parse_is_refused() -> None:
    with pytest.raises(ValueError, match="not a JMESPath expression"):
        compile_when(("page.state ==",))


def test_a_when_longer_than_the_bounds_is_refused() -> None:
    """The one place a member's text enters, so the one place it is bounded."""
    with pytest.raises(ValueError, match="at most 16 clauses"):
        compile_when(tuple(f"stream == 's{index}'" for index in range(17)))
    with pytest.raises(ValueError, match="at most 1000 characters"):
        compile_when((f"stream == '{'s' * 1000}'",))
    with pytest.raises(ValueError, match="at least one clause"):
        compile_when(())


async def _create(
    store: SourceTriggerStore,
    conversation_id: UUID,
    connection_id: UUID,
    delivery: str,
    *,
    created_by_member_id: UUID,
    resource: str = "",
    name: str = "",
) -> SourceTrigger:
    """One trigger with the clauses its resource is written from, which is what the offer and the
    apply both hand the store. These tests are about the rows, not about the clause builders."""
    return await store.create(
        conversation_id=conversation_id,
        connection_id=connection_id,
        delivery="current",
        created_by_member_id=created_by_member_id,
        name=name or (f"watch-{uuid4().hex[:8]}"),
        description=resource or "The whole feed",
        when=resource_clauses(GITHUB, resource, _repo(resource))
        if resource
        else default_clauses(GITHUB),
    )


@dataclass(frozen=True)
class _Seeded:
    workspace_id: UUID
    member_id: UUID
    agent_id: UUID
    conversation_id: UUID
    connection_id: UUID
    later_connection_id: UUID


async def _seed() -> _Seeded:
    seeded = _Seeded(uuid4(), uuid4(), uuid4(), uuid4(), uuid4(), uuid4())
    created_at = datetime(2026, 7, 20, tzinfo=UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=seeded.workspace_id, created_at=created_at, updated_at=created_at
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=seeded.member_id,
                workspace_id=seeded.workspace_id,
                email=f"{seeded.member_id.hex}@x.test",
                created_at=created_at,
                updated_at=created_at,
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=seeded.agent_id,
                workspace_id=seeded.workspace_id,
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
                id=seeded.conversation_id,
                workspace_id=seeded.workspace_id,
                agent_id=seeded.agent_id,
                surface="cli",
                queue_key=uuid4().hex,
                member_id=None,
                created_at=created_at,
                updated_at=created_at,
            )
        )
        await connection.execute(
            sa.insert(tables.connection),
            [
                {
                    "id": connection_id,
                    "workspace_id": seeded.workspace_id,
                    "provider": GITHUB,
                    "account_id": account_id,
                    "host": "github.com",
                    "owner_member_id": seeded.member_id,
                    "shared": True,
                    "created_at": created_at,
                    "updated_at": created_at,
                }
                for connection_id, account_id in (
                    (seeded.connection_id, "acct-one"),
                    (seeded.later_connection_id, "acct-two"),
                )
            ],
        )
    return seeded


async def _rows(workspace_id: UUID) -> list[tuple[UUID, str]]:
    async with workspace_tx() as connection:
        listed = (
            await connection.execute(
                sa.select(source_trigger.c.id, source_trigger.c.when)
                .where(source_trigger.c.workspace_id == workspace_id)
                .order_by(source_trigger.c.when)
            )
        ).all()
    return [(row.id, json.loads(row.when)) for row in listed]


async def _stamp(trigger: SourceTrigger, created_at: datetime) -> SourceTrigger:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(source_trigger)
            .values(created_at=created_at)
            .where(source_trigger.c.id == trigger.id)
        )
    return replace(trigger, created_at=created_at)


async def test_a_taken_name_and_a_held_watch_refuse_in_their_own_words(db: None) -> None:
    """The name is the table's key and the clauses are not, so the two collisions are caught in
    two places and an agent is told which one it met."""
    seeded = await _seed()
    filler = "x" * (WHEN_CLAUSE_MAX_CHARS - 20)
    clauses = tuple(f"stream == '{filler}{index}'" for index in range(WHEN_CLAUSES_MAX))
    assert len(json.dumps(list(clauses)).encode()) > 2704
    compile_when(clauses)

    with ws(seeded.workspace_id), agent(seeded.agent_id):
        store = SourceTriggerStore(context_for(NAME, frozenset(CONNECTORS)))
        landed = await store.create(
            conversation_id=seeded.conversation_id,
            connection_id=seeded.connection_id,
            delivery="current",
            created_by_member_id=seeded.member_id,
            name="filled",
            when=clauses,
        )
        assert landed.when == clauses
        with pytest.raises(ValueError, match="already watches"):
            await store.create(
                conversation_id=seeded.conversation_id,
                connection_id=seeded.connection_id,
                delivery="current",
                created_by_member_id=seeded.member_id,
                name="filled-again",
                when=clauses,
            )
        with pytest.raises(ValueError, match="already named 'filled'"):
            await store.create(
                conversation_id=seeded.conversation_id,
                connection_id=seeded.later_connection_id,
                delivery="current",
                created_by_member_id=seeded.member_id,
                name="filled",
                when=clauses,
            )


async def test_one_conversation_holds_a_whole_feed_and_a_narrowed_trigger(db: None) -> None:
    seeded = await _seed()
    feed = seeded.connection_id
    with ws(seeded.workspace_id), agent(seeded.agent_id):
        store = SourceTriggerStore(context_for(NAME, frozenset(CONNECTORS)))
        whole = await _create(
            store, seeded.conversation_id, feed, "current", created_by_member_id=seeded.member_id
        )
        narrowed = await _create(
            store,
            seeded.conversation_id,
            feed,
            "current",
            created_by_member_id=seeded.member_id,
            resource=PR,
        )

        assert whole.when == default_clauses(GITHUB)
        assert narrowed.when == resource_clauses(GITHUB, PR, _repo(PR))
        assert await _rows(seeded.workspace_id) == sorted(
            [(whole.id, list(whole.when)), (narrowed.id, list(narrowed.when))],
            key=lambda row: json.dumps(row[1]),
        )
        assert set(await store.waking(feed)) == {whole, narrowed}
        held = await store.watched(seeded.conversation_id)
        assert set(held[feed]) == {
            *default_clauses(GITHUB),
            *resource_clauses(GITHUB, PR, _repo(PR)),
        }
        listed = await store.list_reported(conversation_id=seeded.conversation_id)
        assert [row.trigger for row in listed] == sorted(
            (whole, narrowed), key=lambda row: (row.connection_id, row.when, row.id)
        )
        assert {row.audience for row in listed} == {SHARED_AUDIENCE}
        assert {row.surface_label for row in listed} == {None}

        for resource in ("", PR):
            with pytest.raises(ValueError, match="already watches"):
                await _create(
                    store,
                    seeded.conversation_id,
                    feed,
                    "current",
                    created_by_member_id=seeded.member_id,
                    resource=resource,
                )

        await store.remove(narrowed)
        assert await store.waking(feed) == (whole,)
        assert (await store.watched(seeded.conversation_id))[feed] == default_clauses(GITHUB)
        assert [row.trigger for row in await store.list_reported()] == [whole]
        with pytest.raises(ValueError, match="changed while removing"):
            await store.remove(narrowed)

        narrowed = await _create(
            store,
            seeded.conversation_id,
            feed,
            "current",
            created_by_member_id=seeded.member_id,
            resource=PR,
        )
        assert await _rows(seeded.workspace_id) == sorted(
            [(whole.id, list(whole.when)), (narrowed.id, list(narrowed.when))],
            key=lambda row: json.dumps(row[1]),
        )

        await store.remove(whole)
        assert await store.waking(feed) == (narrowed,)
        assert (await store.watched(seeded.conversation_id))[feed] == resource_clauses(
            GITHUB, PR, _repo(PR)
        )


async def test_disconnecting_the_account_takes_every_trigger_on_it(db: None) -> None:
    seeded = await _seed()
    with ws(seeded.workspace_id), agent(seeded.agent_id):
        store = SourceTriggerStore(context_for(NAME, frozenset(CONNECTORS)))
        await _create(
            store,
            seeded.conversation_id,
            seeded.connection_id,
            "current",
            created_by_member_id=seeded.member_id,
        )
        await _create(
            store,
            seeded.conversation_id,
            seeded.connection_id,
            "current",
            created_by_member_id=seeded.member_id,
            resource=PR,
        )
        kept = await _create(
            store,
            seeded.conversation_id,
            seeded.later_connection_id,
            "current",
            created_by_member_id=seeded.member_id,
        )

        async with workspace_tx() as connection:
            await connection.execute(
                sa.delete(tables.connection).where(
                    tables.connection.c.id == seeded.connection_id,
                )
            )

        assert await _rows(seeded.workspace_id) == [(kept.id, list(kept.when))]
        assert await store.waking(seeded.connection_id) == ()
        assert seeded.connection_id not in await store.watched(seeded.conversation_id)
        assert [row.trigger for row in await store.list_reported()] == [kept]


async def test_the_alert_sweep_reads_a_feeds_triggers_oldest_first(db: None) -> None:
    """`waking` hands the sweep one order — oldest first, the id breaking a tie — so one batch
    wakes conversations in the order they subscribed however the rows sit in the table."""
    seeded = await _seed()
    feed = seeded.connection_id
    with ws(seeded.workspace_id), agent(seeded.agent_id):
        store = SourceTriggerStore(context_for(NAME, frozenset(CONNECTORS)))
        newest = await _stamp(
            await _create(
                store,
                seeded.conversation_id,
                feed,
                "current",
                created_by_member_id=seeded.member_id,
                resource=PR,
            ),
            datetime(2026, 7, 20, 14, tzinfo=UTC),
        )
        oldest = await _stamp(
            await _create(
                store,
                seeded.conversation_id,
                feed,
                "current",
                created_by_member_id=seeded.member_id,
            ),
            datetime(2026, 7, 20, 12, tzinfo=UTC),
        )
        middle = await _stamp(
            await _create(
                store,
                seeded.conversation_id,
                feed,
                "current",
                created_by_member_id=seeded.member_id,
                resource=OTHER_PR,
            ),
            datetime(2026, 7, 20, 13, tzinfo=UTC),
        )
        assert await store.waking(feed) == (oldest, middle, newest)


async def test_a_listing_heads_each_feed_with_its_whole_feed(db: None) -> None:
    """`list_reported` orders by connection and then by resource, so a member reads a feed's
    whole stream above the resources of it and the feeds in one order every time."""
    seeded = await _seed()
    first, second = sorted((seeded.connection_id, seeded.later_connection_id))
    with ws(seeded.workspace_id), agent(seeded.agent_id):
        store = SourceTriggerStore(context_for(NAME, frozenset(CONNECTORS)))
        later_feed = await _create(
            store,
            seeded.conversation_id,
            second,
            "current",
            created_by_member_id=seeded.member_id,
        )
        pull_request = await _create(
            store,
            seeded.conversation_id,
            first,
            "current",
            created_by_member_id=seeded.member_id,
            resource=PR,
        )
        whole = await _create(
            store,
            seeded.conversation_id,
            first,
            "current",
            created_by_member_id=seeded.member_id,
        )
        other = await _create(
            store,
            seeded.conversation_id,
            first,
            "current",
            created_by_member_id=seeded.member_id,
            resource=OTHER_PR,
        )
        listed = await store.list_reported()
        assert [row.trigger for row in listed] == sorted(
            (whole, other, pull_request, later_feed),
            key=lambda row: (row.connection_id, row.when, row.id),
        )
