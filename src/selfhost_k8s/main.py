"""The ``selfhost-k8s`` entry point — one image, one role per Deployment, selected by argument.

``selfhost-k8s api`` runs the deploy endpoint; ``selfhost-k8s operator`` runs the reconcile loop.
This is metalcraft's one-image/console-script-args deployment shape (``[metalcraft-operator]``),
stripped to the two processes this control plane needs. Both serve ``/healthz`` for probes.
"""

import click
import uvicorn

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080


@click.group()
def main() -> None:
    """The Kubernetes control plane that runs selfhost-core as its per-workspace backend."""


@main.command()
@click.option("--host", default=DEFAULT_HOST, show_default=True)
@click.option("--port", default=DEFAULT_PORT, show_default=True)
def api(host: str, port: int) -> None:
    """Serve the deploy endpoint (POST /v1/deploy, GET /v1/tenants/{name})."""
    uvicorn.run("selfhost_k8s.api:app", host=host, port=port, log_level="info")


@main.command()
@click.option("--host", default=DEFAULT_HOST, show_default=True)
@click.option("--port", default=DEFAULT_PORT, show_default=True)
def operator(host: str, port: int) -> None:
    """Run the level-triggered Tenant reconcile loop behind a /healthz probe."""
    uvicorn.run(
        "selfhost_k8s.operator:operator_app", host=host, port=port, factory=True, log_level="info"
    )
