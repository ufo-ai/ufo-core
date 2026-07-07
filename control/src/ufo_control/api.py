"""The deploy API — the endpoint ``ufoctl deploy --backend k8s --remote`` posts to.

It is deliberately thin: it validates the ``DeployRequest`` against the contract and server-side-
applies it as a ``Tenant`` object, then returns. It does not provision — the operator (a separate
Deployment, leader-elected) reconciles Tenants to Ready. This is metalcraft's gateway/operator split
(the API writes desired state; the operator drives it), stripped to one endpoint and one kind. The
client polls ``GET /v1/tenants/{name}`` for the reconciled status and the workspace URL.
"""

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from ufo.deploy import DeployRequest, DeployStatus

from ufo_control.kube import KubeClient


def deploy_app() -> FastAPI:
    state: dict[str, KubeClient] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Any:
        state["kube"] = KubeClient.from_env()
        try:
            yield
        finally:
            await state["kube"].close()

    app = FastAPI(lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/deploy")
    async def deploy(request: DeployRequest) -> DeployStatus:
        await state["kube"].apply_tenant(request)
        return DeployStatus(
            tenant=request.tenant.name, phase="Pending", message="tenant accepted; reconciling"
        )

    @app.get("/v1/tenants/{name}")
    async def tenant_status(name: str) -> DeployStatus:
        obj = await state["kube"].get_tenant(name)
        if obj is None:
            raise HTTPException(status_code=404, detail=f"no tenant {name!r}")
        status = obj.get("status")
        if not status:
            return DeployStatus(tenant=name, phase="Pending", message="not yet reconciled")
        return DeployStatus.model_validate({"tenant": name, **status})

    return app


app = deploy_app()
