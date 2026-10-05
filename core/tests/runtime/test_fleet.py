import pytest
from dbos import DBOS
from dbos._utils import INTERNAL_QUEUE_NAME

from ufo.config import Config
from ufo.serve import API_FLEET, FLEETS, JOBS_FLEET, TURNS_FLEET, WHOLE_FLEET


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
def test_the_fleets_partition_every_queue_the_deploy_registers(dbos_launched: Config) -> None:
    registered = {queue.name for queue in DBOS.list_queues()} - {INTERNAL_QUEUE_NAME}
    claimed = [queue for fleet in (TURNS_FLEET, JOBS_FLEET, API_FLEET) for queue in fleet.queues]
    assert len(claimed) == len(set(claimed))
    assert set(claimed) == registered == set(WHOLE_FLEET.queues)
    assert API_FLEET.queues == ()
    assert not API_FLEET.surfaces
    assert set(FLEETS) == {"all", "turns", "jobs", "api"}
