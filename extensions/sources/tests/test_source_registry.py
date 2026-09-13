from inspect import getsource

import pytest
from ufo_ext_sources.manifest import manifest
from ufo_ext_sources.providers.asana import AsanaConnector
from ufo_ext_sources.registry import CONNECTORS, _connector_registry


class FirstConnector(AsanaConnector):
    name = "duplicate"


class SecondConnector(AsanaConnector):
    name = "duplicate"


def test_registry_maps_every_connector_name_once() -> None:
    assert len(CONNECTORS) == 55
    assert all(name == connector_type.name for name, connector_type in CONNECTORS.items())


def test_registry_rejects_duplicate_connector_names() -> None:
    with pytest.raises(ValueError, match="duplicate source connector name 'duplicate'"):
        _connector_registry((FirstConnector, SecondConnector))


def test_every_connector_declaring_a_window_reads_the_floor_it_is_handed() -> None:
    """A run's pinned floor arrives as `Run.backfill_after`, and every connector's `paginate` is
    handed the run whole — so a connector that declares `backfill_window_days` and never reads it
    has the connection's window resolved onto its row and pinned while its walk reaches back
    forever, honoured by nothing. That is the exact state a stream declaring no window avoids by
    taking no cutoff at all, so declaring one obliges the connector to read the floor it is given.

    Nothing hides the value any more — the obligation is to use it, which is what this reads."""
    obliged = {
        name: [stream.name for stream in cls().streams() if stream.backfill_window_days is not None]
        for name, cls in CONNECTORS.items()
    }
    declaring = {name: streams for name, streams in obliged.items() if streams}
    assert set(declaring) == {"datadog", "github", "gmail", "granola_mcp", "outlook", "slack"}

    deaf = [
        f"{name} declares a window on {streams} and its paginate never reads the floor"
        for name, streams in declaring.items()
        if "backfill_after" not in getsource(CONNECTORS[name].paginate)
    ]
    assert deaf == []


def test_a_two_key_connector_declares_the_slot_behind_each_header() -> None:
    slots = {slot.name for slot in manifest().credentials}
    assert {"datadog_feed_api_key", "datadog_feed_application_key"} <= slots
    assert "datadog" not in slots
    assert "github" in slots
    bearer_named = {name for name in CONNECTORS if not CONNECTORS[name].key_headers}
    assert bearer_named <= slots
