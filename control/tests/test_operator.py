import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ufo_control.kube import KubeClient
from ufo_control.operator import (
    LeaderElection,
    _deleting,
    _format_time,
    _parse_time,
    _workspace_id,
)
from ufo_control.provision import _job_failed, _job_succeeded, _ready_replicas

NOW = datetime(2026, 7, 6, 12, 0, 0, 500000, tzinfo=UTC)


def test_lease_time_round_trips() -> None:
    assert _parse_time(_format_time(NOW)) == NOW


def test_workspace_id_reuses_status_and_mints_when_absent() -> None:
    persisted = "3f8c1e2a-0b4d-4c6e-9a1f-2b3c4d5e6f70"
    assert _workspace_id({"status": {"workspaceId": persisted}}) == persisted
    minted = _workspace_id({"status": {}})
    assert minted and minted != persisted
    assert _workspace_id({}) != _workspace_id({})


def test_ready_and_job_helpers() -> None:
    assert _ready_replicas(None) == 0
    assert _ready_replicas({"status": {"readyReplicas": 2}}) == 2
    assert _job_succeeded({"status": {"succeeded": 1}})
    assert not _job_succeeded({"status": {}})
    assert _job_failed({"status": {"failed": 1}})
    assert _deleting({"metadata": {"deletionTimestamp": "2026-07-06T00:00:00Z"}})
    assert not _deleting({"metadata": {}})


def _lease_client(store: dict[str, Any]) -> KubeClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            if "lease" not in store:
                return httpx.Response(404)
            return httpx.Response(200, json=store["lease"])
        body = json.loads(request.content)
        store["lease"] = {"spec": body["spec"]}
        return httpx.Response(200, json=store["lease"])

    return KubeClient(
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://kube.test")
    )


async def test_acquires_when_no_lease_exists() -> None:
    store: dict[str, Any] = {}
    election = LeaderElection(kube=_lease_client(store), identity="me")
    assert await election.try_acquire(NOW) is True
    assert store["lease"]["spec"]["holderIdentity"] == "me"


async def test_renews_its_own_lease() -> None:
    store = {
        "lease": {
            "spec": {"holderIdentity": "me", "renewTime": _format_time(NOW), "leaseTransitions": 3}
        }
    }
    election = LeaderElection(kube=_lease_client(store), identity="me")
    assert await election.try_acquire(NOW + timedelta(seconds=5)) is True
    assert store["lease"]["spec"]["leaseTransitions"] == 3


async def test_yields_to_a_fresh_peer() -> None:
    store = {
        "lease": {
            "spec": {
                "holderIdentity": "peer",
                "renewTime": _format_time(NOW),
                "leaseTransitions": 1,
            }
        }
    }
    election = LeaderElection(kube=_lease_client(store), identity="me")
    assert await election.try_acquire(NOW + timedelta(seconds=5)) is False


async def test_takes_over_an_expired_lease() -> None:
    store = {
        "lease": {
            "spec": {
                "holderIdentity": "peer",
                "renewTime": _format_time(NOW),
                "leaseTransitions": 1,
            }
        }
    }
    election = LeaderElection(kube=_lease_client(store), identity="me")
    assert await election.try_acquire(NOW + timedelta(seconds=60)) is True
    assert store["lease"]["spec"]["holderIdentity"] == "me"
    assert store["lease"]["spec"]["leaseTransitions"] == 2
