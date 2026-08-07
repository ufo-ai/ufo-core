import pytest
from ufo_ext_sources.asana import AsanaConnector
from ufo_ext_sources.registry import CONNECTORS, _connector_registry

from ufo.sdk.sources import RestConnector


class FirstConnector(AsanaConnector):
    name = "duplicate"


class SecondConnector(AsanaConnector):
    name = "duplicate"


def test_registry_maps_every_connector_name_once() -> None:
    assert len(CONNECTORS) == 48
    assert all(name == connector_type.name for name, connector_type in CONNECTORS.items())


def test_registry_rejects_duplicate_connector_names() -> None:
    with pytest.raises(ValueError, match="duplicate source connector name 'duplicate'"):
        _connector_registry((FirstConnector, SecondConnector))


def test_every_connector_declaring_a_window_reads_the_floor_it_is_handed() -> None:
    """A run's pinned floor arrives as `fetch_page`'s `backfill_after`, and `paginate_source` drops
    it by default so the connectors that read no window keep the `paginate` they have. That default
    is a trap for the next connector to declare `backfill_window_days`: the knob would be accepted,
    pinned on the row, and reported in `status`, while its walk quietly reached back forever —
    honoured by nothing, which is the exact state `backfill_days` is refused elsewhere to avoid.

    So declaring a window on a stream obliges the connector to take delivery of one. Overriding
    `paginate_source` is how a connector does that (gmail and outlook do; slack overrides it for the
    acting identity instead and declares no window)."""
    obliged = {
        name: [stream.name for stream in cls().streams() if stream.backfill_window_days is not None]
        for name, cls in CONNECTORS.items()
    }
    declaring = {name: streams for name, streams in obliged.items() if streams}
    assert set(declaring) == {"gmail", "outlook"}

    deaf = [
        f"{name} declares a window on {streams} but does not override paginate_source"
        for name, streams in declaring.items()
        if CONNECTORS[name].paginate_source is RestConnector.paginate_source
    ]
    assert deaf == []
