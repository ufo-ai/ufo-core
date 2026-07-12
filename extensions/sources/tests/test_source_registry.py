import pytest
from ufo_ext_sources.asana import AsanaConnector
from ufo_ext_sources.registry import CONNECTORS, _connector_registry


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
