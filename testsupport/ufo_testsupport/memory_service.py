"""The memory service as core's tests serve it, over the golden pairs of its wire file."""

from importlib.resources import files
from pathlib import Path

from ufo_testsupport.service_stand_in import ServiceStandIn

MEMORY_WIRE = Path(str(files("ufo_testsupport") / "wire" / "memory_wire.json"))


class MemoryServiceStandIn(ServiceStandIn):
    """The memory service's public routes, answering `memory_wire.json`."""

    def __init__(self, golden: Path = MEMORY_WIRE) -> None:
        super().__init__(golden)
