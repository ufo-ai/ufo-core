"""The map between a link and the pages a source replays, one rule set per provider.

`canonical_resource` reads a link against the provider that owns it and answers the one URL a
narrowed trigger stores. `resource_matches` answers the other direction — whether one replayed page
body is about that resource, on the URL forms the provider's own records carry. `_about_resource` is
where a narrowed trigger's changes pass through the second."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from ufo_ext_sources.resources import canonical_resource, resource_digest, resource_matches
from ufo_ext_sources.tools import _about_resource
from ufo_ext_sources.triggers import SourceTrigger

from ufo.sdk.sources import PageChange

GITHUB = "github"
PR = "https://github.com/metalcraftai/ufo/pull/1684"
CONVERSATION = UUID("29c88018-22cd-4e44-bb1e-7eae9cf5bf43")


def _trigger(resource: str) -> SourceTrigger:
    now = datetime(2026, 7, 20, tzinfo=UTC)
    return SourceTrigger(
        id=uuid4(),
        conversation_id=CONVERSATION,
        agent_id=uuid4(),
        binding="github-ee65f064",
        resource=resource,
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


def test_a_whole_binding_trigger_takes_every_change() -> None:
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
