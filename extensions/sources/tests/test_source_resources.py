"""The map between a link and the pages a connection replays, one rule set per provider.

`canonical_resource` reads a link against the provider that owns it and answers the one URL a
narrowed trigger stores. `resource_matches` answers the other direction — whether one replayed page
body is about that resource, on the URL forms the provider's own records carry. `_about_resource` is
where a narrowed trigger's changes pass through the second.

The trigger those rules serve is a `source_trigger` row carrying its resource, so the store tests
below drive `SourceTriggerStore` against a conversation holding the whole feed of one connection and
one resource of it at once — the two rows the key admits — and pin the order each read hands them
back in. The last one disconnects the account and reads the rows back gone, which is the only thing
that ever removes a trigger."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from ufo_ext_sources.manifest import NAME
from ufo_ext_sources.registry import CONNECTORS
from ufo_ext_sources.resources import (
    canonical_resource,
    resource_digest,
    resource_keys,
    resource_matches,
)
from ufo_ext_sources.tools import _about_resource
from ufo_ext_sources.triggers import SourceTrigger, SourceTriggerStore, source_trigger

from ufo.db import workspace_tx
from ufo.runtime.agent_scope import agent
from ufo.runtime.ext.context import context_for
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.sdk.audience import SHARED_AUDIENCE
from ufo.sdk.sources import PageChange

GITHUB = "github"
PR = "https://github.com/metalcraftai/ufo/pull/1684"
ISSUE = "https://github.com/metalcraftai/ufo/issues/1684"
CONNECTION = UUID("2f7c0f5e-1d0a-4c2b-9d3f-6b1f9a0c5e11")
CONVERSATION = UUID("29c88018-22cd-4e44-bb1e-7eae9cf5bf43")


def _trigger(resource: str) -> SourceTrigger:
    now = datetime(2026, 7, 20, tzinfo=UTC)
    return SourceTrigger(
        id=uuid4(),
        conversation_id=CONVERSATION,
        agent_id=uuid4(),
        connection_id=CONNECTION,
        resource=resource,
        streams=(),
        delivery="current",
        created_by_member_id=None,
        created_at=now,
        updated_at=now,
    )


def _page(body: str, stream: str = "pull_requests") -> PageChange:
    now = datetime(2026, 7, 20, tzinfo=UTC)
    return PageChange(
        page_id=uuid4(),
        source_id=uuid4(),
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
        "https://github.com/MetalCraftAI/ufo/pull/1684",
        "https://www.github.com/metalcraftai/ufo/pull/1684/files",
        "https://github.com/metalcraftai/ufo/pull/1684#issuecomment-5548599845",
        "https://github.com/metalcraftai/ufo/pull/1684?diff=split",
        "https://api.github.com/repos/metalcraftai/ufo/pulls/1684",
        " https://github.com/metalcraftai/ufo/pull/1684 ",
    ):
        assert canonical_resource(GITHUB, spelling) == PR


def test_a_pull_request_and_the_issue_of_its_number_share_a_key() -> None:
    """GitHub numbers pull requests and issues in one sequence and files a pull request's comments
    under `issues/<n>`, so the two canonical spellings of one number share a key; the next number
    and another repository share none, and a provider without rules keys a resource on itself."""

    def one(first: str, second: str, provider: str = GITHUB) -> bool:
        return not set(resource_keys(provider, first)).isdisjoint(resource_keys(provider, second))

    assert one(PR, PR)
    assert one(PR, "https://github.com/metalcraftai/ufo/issues/1684")
    assert not one(PR, "https://github.com/metalcraftai/ufo/pull/16840")
    assert not one(PR, "https://github.com/metalcraftai/other/pull/1684")
    linear = "https://linear.app/metalcraft/issue/UFO-1"
    assert resource_keys("linear", linear) == (linear,)
    assert not one(linear, "https://linear.app/metalcraft/issue/UFO-2", provider="linear")


def test_an_issue_link_canonicalizes_under_its_own_path() -> None:
    assert (
        canonical_resource(GITHUB, "https://api.github.com/repos/MetalCraftAI/ufo/issues/7")
        == "https://github.com/metalcraftai/ufo/issues/7"
    )


def test_a_link_to_anything_but_a_pull_request_or_issue_names_nothing() -> None:
    """A repository is not a resource a trigger narrows to — a thread that names a repository is
    not asking to hear every push it carries, and the whole feed already has its own trigger."""
    for link in (
        "https://github.com/metalcraftai/ufo",
        "https://github.com/metalcraftai/ufo/pulls",
        "https://github.com/metalcraftai/ufo/commit/60b277f01b86d3ec672641cc6286ef70699284fc",
        "https://github.com/metalcraftai/ufo/pull/1684abc",
        "http://github.com/metalcraftai/ufo/pull/1684",
        "https://linear.app/metalcraft/issue/UFO-1",
    ):
        assert canonical_resource(GITHUB, link) is None


def test_a_provider_without_resource_rules_names_nothing() -> None:
    assert canonical_resource("linear", "https://linear.app/metalcraft/issue/UFO-1") is None


def test_a_page_is_about_the_resource_its_provider_links_it_to() -> None:
    """A page body is the provider's own JSON, so the resource's URLs are in it wherever GitHub
    linked them: the pull request's `html_url`, a comment's `issue_url`, a review comment's
    `pull_request_url`, a workflow run's `pull_requests[].url` — under any spelling of the
    repository."""
    for body in (
        '{"html_url": "https://github.com/MetalCraftAI/ufo/pull/1684", "number": 1684}',
        '{"issue_url": "https://api.github.com/repos/metalcraftai/ufo/issues/1684", '
        '"body": "lgtm"}',
        '{"pull_request_url": "https://api.github.com/repos/metalcraftai/ufo/pulls/1684"}',
        '{"pull_requests": [{"url": "https://api.github.com/repos/metalcraftai/ufo/pulls/1684"}]}',
        '{"html_url": "https://github.com/metalcraftai/ufo/pull/1684/files"}',
    ):
        assert resource_matches(GITHUB, PR, body)


def test_a_page_about_another_resource_is_not_about_this_one() -> None:
    for body in (
        '{"html_url": "https://github.com/metalcraftai/ufo/pull/16840"}',
        '{"html_url": "https://github.com/metalcraftai/other/pull/1684"}',
        '{"html_url": "https://github.com/metalcraftai/ufo/pull/1685", "body": "see 1684"}',
        "",
    ):
        assert not resource_matches(GITHUB, PR, body)


def test_a_provider_without_resource_rules_matches_nothing() -> None:
    assert not resource_matches("linear", "https://linear.app/x/issue/UFO-1", "UFO-1")


def test_a_resource_digest_is_one_stable_path_segment() -> None:
    assert resource_digest(PR) == resource_digest(PR)
    assert len(resource_digest(PR)) == 8
    assert resource_digest(PR).isalnum()
    assert resource_digest(PR) != resource_digest("https://github.com/metalcraftai/ufo/pull/1685")


def test_a_whole_feed_trigger_takes_every_change() -> None:
    changes = [_page('{"html_url": "https://github.com/metalcraftai/ufo/pull/1"}'), _page("{}")]
    assert _about_resource(GITHUB, _trigger(""), changes) == changes


def test_a_resource_watch_takes_only_the_changes_about_that_resource() -> None:
    watched = _page('{"html_url": "https://github.com/metalcraftai/ufo/pull/1684"}')
    comment = _page(
        '{"issue_url": "https://api.github.com/repos/metalcraftai/ufo/issues/1684"}',
        stream="comments",
    )
    other = _page('{"html_url": "https://github.com/metalcraftai/ufo/pull/1685"}')
    assert _about_resource(GITHUB, _trigger(PR), [watched, other, comment]) == [watched, comment]


def test_a_watch_under_a_provider_without_rules_wakes_nothing() -> None:
    """A narrowed trigger is a promise about one thing. Where nothing can read the resource, it
    wakes nobody rather than everybody — a widened promise is the firehose the narrowing avoids."""
    changes = [_page('{"url": "https://linear.app/x/issue/UFO-1"}')]
    assert _about_resource("linear", _trigger("https://linear.app/x/issue/UFO-1"), changes) == []


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
                sa.select(source_trigger.c.id, source_trigger.c.resource)
                .where(source_trigger.c.workspace_id == workspace_id)
                .order_by(source_trigger.c.resource)
            )
        ).all()
    return [(row.id, row.resource) for row in listed]


async def _stamp(trigger: SourceTrigger, created_at: datetime) -> SourceTrigger:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(source_trigger)
            .values(created_at=created_at)
            .where(source_trigger.c.id == trigger.id)
        )
    return replace(trigger, created_at=created_at)


async def test_one_conversation_holds_a_whole_feed_and_a_narrowed_trigger(db: None) -> None:
    """Both kinds of trigger on one connection for one conversation, which is what
    (workspace_id, conversation_id, connection_id, resource) is keyed to admit: both land as rows of
    `source_trigger` carrying their own resource. Each read then answers over the two — `waking`
    hands the sweep both, `watched` reports the narrowed resource alone so the whole feed is never
    mistaken for an offer already taken, `list_reported` orders the whole feed ahead of its
    resources — and a repeat of either refuses in the store's own vocabulary. `remove` takes the one
    row it names whichever kind it is, refuses a row already gone, leaves its neighbour reporting,
    and the key readmits what it removed."""
    seeded = await _seed()
    feed = seeded.connection_id
    with ws(seeded.workspace_id), agent(seeded.agent_id):
        store = SourceTriggerStore(context_for(NAME, frozenset(CONNECTORS)))
        whole = await store.create(seeded.conversation_id, feed, "current")
        narrowed = await store.create(
            seeded.conversation_id, feed, "per_page", seeded.member_id, PR
        )

        assert whole.resource == ""
        assert narrowed.resource == PR
        assert await _rows(seeded.workspace_id) == [(whole.id, ""), (narrowed.id, PR)]
        assert set(await store.waking(feed)) == {whole, narrowed}
        assert await store.watched(seeded.conversation_id) == frozenset({(feed, PR)})
        listed = await store.list_reported(conversation_id=seeded.conversation_id)
        assert [row.trigger for row in listed] == [whole, narrowed]
        assert {row.audience for row in listed} == {SHARED_AUDIENCE}
        assert {row.surface_label for row in listed} == {None}

        for resource in ("", PR):
            with pytest.raises(ValueError, match="already watches"):
                await store.create(seeded.conversation_id, feed, "current", resource=resource)

        await store.remove(narrowed)
        assert await store.waking(feed) == (whole,)
        assert await store.watched(seeded.conversation_id) == frozenset()
        assert [row.trigger for row in await store.list_reported()] == [whole]
        with pytest.raises(ValueError, match="changed while removing"):
            await store.remove(narrowed)

        narrowed = await store.create(
            seeded.conversation_id, feed, "per_page", seeded.member_id, PR
        )
        assert await _rows(seeded.workspace_id) == [(whole.id, ""), (narrowed.id, PR)]

        await store.remove(whole)
        assert await store.waking(feed) == (narrowed,)
        assert await store.watched(seeded.conversation_id) == frozenset({(feed, PR)})


async def test_disconnecting_the_account_takes_every_trigger_on_it(db: None) -> None:
    """A trigger names its connection by foreign key, so disconnecting takes the whole feed's
    triggers with the source rows and the pages — across every agent that subscribed, and whether
    the trigger watched the whole feed or one resource of it. Nothing sweeps them, because the
    cascade is the sweep, and a trigger left behind would watch a feed nobody can reach."""
    seeded = await _seed()
    with ws(seeded.workspace_id), agent(seeded.agent_id):
        store = SourceTriggerStore(context_for(NAME, frozenset(CONNECTORS)))
        await store.create(seeded.conversation_id, seeded.connection_id, "current")
        await store.create(seeded.conversation_id, seeded.connection_id, "per_page", resource=PR)
        kept = await store.create(seeded.conversation_id, seeded.later_connection_id, "current")

        async with workspace_tx() as connection:
            await connection.execute(
                sa.delete(tables.connection).where(
                    tables.connection.c.id == seeded.connection_id,
                )
            )

        assert await _rows(seeded.workspace_id) == [(kept.id, "")]
        assert await store.waking(seeded.connection_id) == ()
        assert await store.watched(seeded.conversation_id) == frozenset()
        assert [row.trigger for row in await store.list_reported()] == [kept]


async def test_the_alert_sweep_reads_a_feeds_triggers_oldest_first(db: None) -> None:
    """`waking` hands the sweep one order — oldest first, the id breaking a tie — so one batch
    wakes conversations in the order they subscribed however the rows sit in the table. These three
    are stamped counter to the order they were written in, which is the order a read that skipped
    the sort would answer with."""
    seeded = await _seed()
    feed = seeded.connection_id
    with ws(seeded.workspace_id), agent(seeded.agent_id):
        store = SourceTriggerStore(context_for(NAME, frozenset(CONNECTORS)))
        newest = await _stamp(
            await store.create(seeded.conversation_id, feed, "per_page", resource=PR),
            datetime(2026, 7, 20, 14, tzinfo=UTC),
        )
        oldest = await _stamp(
            await store.create(seeded.conversation_id, feed, "current"),
            datetime(2026, 7, 20, 12, tzinfo=UTC),
        )
        middle = await _stamp(
            await store.create(seeded.conversation_id, feed, "per_page", resource=ISSUE),
            datetime(2026, 7, 20, 13, tzinfo=UTC),
        )
        assert await store.waking(feed) == (oldest, middle, newest)


async def test_a_listing_heads_each_feed_with_its_whole_feed(db: None) -> None:
    """`list_reported` orders by connection and then by resource, so a member reads a feed's whole
    stream above the resources of it and the feeds in one order every time. These four are written
    in none of that order, which is the order a listing that skipped the sort would draw."""
    seeded = await _seed()
    first, second = sorted((seeded.connection_id, seeded.later_connection_id))
    with ws(seeded.workspace_id), agent(seeded.agent_id):
        store = SourceTriggerStore(context_for(NAME, frozenset(CONNECTORS)))
        later_feed = await store.create(seeded.conversation_id, second, "current")
        pull_request = await store.create(seeded.conversation_id, first, "per_page", resource=PR)
        whole = await store.create(seeded.conversation_id, first, "current")
        issue = await store.create(seeded.conversation_id, first, "per_page", resource=ISSUE)
        listed = await store.list_reported()
        assert [row.trigger for row in listed] == [whole, issue, pull_request, later_feed]
