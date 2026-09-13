"""A syncing child keyed `local` is addressed under its parents, so a canonical stream that takes
the default re-addresses every page it landed and leaves the old ones live. Each row here shipped
its sweep: deel `tasks` is a `delete_missing` snapshot; mailchimp `list_members` and the two
microsoft_teams streams are reaped by the release after this one, once no outgoing pod can re-land
the old identities; github scoped its keys by hand before the tree, so `local` is its parity; the
two ancestors were never canonical and landed nothing."""

from ufo_ext_sources.registry import CONNECTORS

from ufo.sdk.sources import syncing_streams

SCOPED_UNDER_PARENTS = frozenset(
    {
        ("airtable", "tables"),
        ("deel", "tasks"),
        ("freshdesk", "solution_folders"),
        ("github", "comments"),
        ("github", "commit_comments"),
        ("github", "issues"),
        ("github", "pull_requests"),
        ("github", "releases"),
        ("github", "repositories"),
        ("github", "review_comments"),
        ("github", "workflows"),
        ("mailchimp", "list_members"),
        ("microsoft_teams", "channel_messages"),
        ("microsoft_teams", "channels"),
    }
)


def test_every_syncing_child_scoped_under_its_parents_shipped_its_sweep() -> None:
    scoped: set[tuple[str, str]] = set()
    for name, connector_type in CONNECTORS.items():
        streams = connector_type().streams()
        syncing = syncing_streams(streams)
        scoped.update(
            (name, stream.name)
            for stream in streams
            if stream.parents and stream.key_scope == "local" and stream.name in syncing
        )
    assert scoped == SCOPED_UNDER_PARENTS
