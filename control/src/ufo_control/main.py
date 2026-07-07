"""The ``ufo-control`` entry point — one image, one role per Deployment, selected by argument.

``ufo-control api`` runs the deploy endpoint; ``ufo-control operator`` runs the reconcile loop.
This is metalcraft's one-image/console-script-args deployment shape (``[metalcraft-operator]``),
stripped to the two processes this control plane needs. Both serve ``/healthz`` for probes.
"""

import asyncio

import click
import uvicorn

from ufo_control.rls import bootstrap_policies, owner_dsn

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080


@click.group()
def main() -> None:
    """The Kubernetes control plane that runs ufo as its per-workspace backend."""


@main.command()
@click.option("--host", default=DEFAULT_HOST, show_default=True)
@click.option("--port", default=DEFAULT_PORT, show_default=True)
def api(host: str, port: int) -> None:
    """Serve the deploy endpoint (POST /v1/deploy, GET /v1/tenants/{name})."""
    uvicorn.run("ufo_control.api:app", host=host, port=port, log_level="info")


@main.command()
@click.option("--host", default=DEFAULT_HOST, show_default=True)
@click.option("--port", default=DEFAULT_PORT, show_default=True)
def operator(host: str, port: int) -> None:
    """Run the level-triggered Tenant reconcile loop behind a /healthz probe."""
    uvicorn.run(
        "ufo_control.operator:operator_app", host=host, port=port, factory=True, log_level="info"
    )


@main.command(name="rls-bootstrap")
def rls_bootstrap() -> None:
    """Enable RLS + workspace policies on the shared app database, as the owner. The cluster migrate
    Job runs this after ``ufoctl migrate``, once per bundle rollout."""
    asyncio.run(bootstrap_policies(owner_dsn()))
    click.echo("rls policies at head")
