"""The sources service as core's tests serve it, over the golden pairs of its wire file."""

from importlib.resources import files
from pathlib import Path

from ufo_testsupport.service_stand_in import ServiceStandIn

SOURCES_WIRE = Path(str(files("ufo_testsupport") / "wire" / "sources_wire.json"))


class SourcesServiceStandIn(ServiceStandIn):
    """The sources service's public routes, answering `sources_wire.json`."""

    def __init__(self, golden: Path = SOURCES_WIRE) -> None:
        super().__init__(golden)
