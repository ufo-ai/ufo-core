from ufo.serve import FLEETS, JOBS_FLEET, TURNS_FLEET, WHOLE_FLEET


def test_the_api_fleet_claims_no_queue_and_carries_no_surface() -> None:
    claimed = [queue for fleet in (TURNS_FLEET, JOBS_FLEET) for queue in fleet.queues]

    assert set(FLEETS) == {"all", "turns", "jobs", "api"}
    assert FLEETS["api"].queues == ()
    assert FLEETS["api"].surfaces is False
    assert sorted(claimed) == sorted(set(WHOLE_FLEET.queues))
