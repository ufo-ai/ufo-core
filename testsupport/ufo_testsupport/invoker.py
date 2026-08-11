"""The internal turn-invocation seam a test runtime is composed with, as `serve` composes it."""

from uuid import UUID

from dbos import DBOSClient

from ufo.jobs import InvokerFactory
from ufo.surfaces.admission import Admission, AdmissionInvoker


def invoker_factory(dbos: DBOSClient) -> InvokerFactory:
    """The factory `Runtime.invoker_for` takes: one `Admission` over the test's own DBOS client,
    bound per workspace. A subagent child hands its output back through this, so a test that runs a
    turn loop wires the real admission rather than a stand-in that would never admit the arrival."""
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())

    def invoker_for(workspace_id: UUID) -> AdmissionInvoker:
        return AdmissionInvoker(admission=admission, workspace_id=workspace_id)

    return invoker_for
