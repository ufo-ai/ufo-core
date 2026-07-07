"""The operator — a level-triggered reconcile loop over ``Tenant`` objects, leader-elected by Lease.

The stripped re-derivation of metalcraft's ``operator/server.py``: the same ``run_loop`` →
``reconcile_if_leader`` → sleep(interval) shape and the same ``coordination.k8s.io/v1`` Lease
acquire/renew/take-over branches, but over the single ``Tenant`` kind instead of seven product CRDs,
and async on one event loop instead of blocking sleeps. Each interval it re-lists every Tenant,
reconciles it to Ready (idempotent — see ``provision``), and server-side-applies the computed
``.status``. Re-deriving status from the same durable state yields a no-op apply, so there is no
edge-triggered work to miss.

The Lease here coordinates a rolling update rather than guaranteeing exclusion (it is SSA, not a
resourceVersion CAS); the operator Deployment runs a single replica with a Recreate strategy, which
is the actual mutual-exclusion guarantee. A CAS Lease is the documented hardening follow-up.
"""

import asyncio
import contextlib
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI

from selfhost_k8s.kube import KubeClient, request_from_tenant
from selfhost_k8s.platform import (
    LEASE_DURATION_SECONDS,
    OPERATOR_LEASE_NAME,
    PLATFORM_NAMESPACE,
    PlatformConfig,
    load_platform_config,
)
from selfhost_k8s.provision import TenantReconciler


@dataclass(frozen=True)
class LeaderElection:
    kube: KubeClient
    identity: str
    namespace: str = PLATFORM_NAMESPACE
    name: str = OPERATOR_LEASE_NAME
    duration_seconds: int = LEASE_DURATION_SECONDS

    @classmethod
    def from_env(cls, kube: KubeClient) -> "LeaderElection":
        identity = os.environ.get("POD_NAME") or os.environ.get("HOSTNAME")
        if not identity:
            raise RuntimeError("POD_NAME/HOSTNAME unset — the operator needs a Lease identity")
        return cls(kube=kube, identity=identity)

    async def try_acquire(self, now: datetime) -> bool:
        lease = await self.kube.get_lease(self.namespace, self.name)
        if lease is None:
            await self._write(now, transitions=0)
            return True
        spec = lease.get("spec", {})
        holder = spec.get("holderIdentity")
        transitions = int(spec.get("leaseTransitions", 0))
        if holder == self.identity:
            await self._write(now, transitions=transitions)
            return True
        renewed = spec.get("renewTime")
        if renewed is None or (now - _parse_time(renewed)).total_seconds() >= self.duration_seconds:
            await self._write(now, transitions=transitions + 1)
            return True
        return False

    async def _write(self, now: datetime, transitions: int) -> None:
        await self.kube.apply_lease(
            self.namespace,
            self.name,
            {
                "holderIdentity": self.identity,
                "leaseDurationSeconds": self.duration_seconds,
                "acquireTime": _format_time(now),
                "renewTime": _format_time(now),
                "leaseTransitions": transitions,
            },
        )


@dataclass(frozen=True)
class Operator:
    kube: KubeClient
    platform: PlatformConfig
    election: LeaderElection

    async def run_forever(self) -> None:
        reconciler = TenantReconciler(kube=self.kube, platform=self.platform)
        while True:
            with contextlib.suppress(Exception):
                if await self.election.try_acquire(datetime.now(UTC)):
                    await self._reconcile_all(reconciler)
            await asyncio.sleep(self.platform.reconcile_interval_seconds)

    async def _reconcile_all(self, reconciler: TenantReconciler) -> None:
        for obj in await self.kube.list_tenants():
            if _deleting(obj):
                continue
            request = request_from_tenant(obj)
            status = await reconciler.reconcile(request)
            await self.kube.patch_tenant_status(request.tenant.name, status)


def operator_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Any:
        kube = KubeClient.from_env()
        platform = load_platform_config()
        operator = Operator(kube=kube, platform=platform, election=LeaderElection.from_env(kube))
        task = asyncio.create_task(operator.run_forever())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            await kube.close()

    app = FastAPI(lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


def _deleting(obj: dict[str, Any]) -> bool:
    return obj.get("metadata", {}).get("deletionTimestamp") is not None


def _format_time(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
