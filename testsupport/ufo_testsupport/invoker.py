"""The internal turn-invocation seam a test runtime is composed with, as `serve` composes it —
either the real admission over the test's own DBOS client, or a recorder for a test whose subject is
the invocation itself rather than the turn it founds."""

from dataclasses import dataclass, field
from uuid import UUID, uuid4

from dbos import DBOSClient

from ufo.runtime.ext.context import MemberReach, TurnRuntimeConfig
from ufo.runtime.jobs import InvokerFactory
from ufo.runtime.surfaces.admission import Admission, AdmissionInvoker
from ufo.schema.records import FiredBy, ModelAccountCapability


def invoker_factory(dbos: DBOSClient) -> InvokerFactory:
    """The factory `Runtime.invoker_for` takes: one `Admission` over the test's own DBOS client,
    bound per workspace. A subagent child hands its output back through this, so a test that runs a
    turn loop wires the real admission rather than a stand-in that would never admit the arrival."""
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())

    def invoker_for(workspace_id: UUID) -> AdmissionInvoker:
        return AdmissionInvoker(admission=admission, workspace_id=workspace_id)

    return invoker_for


@dataclass(frozen=True)
class RecordedTurn:
    """One invocation as it arrived, so a test asserts the ask rather than its effects."""

    conversation_id: UUID
    agent_id: UUID
    message: str
    idempotency_key: str
    standalone: bool
    runtime_config: TurnRuntimeConfig | None = None


@dataclass
class RecordingInvoker:
    """Stands in for the invoker where what is under test is what reaches it — an automatic
    caller's message, key, runtime capabilities, and folding. Typed rather than suppressed, so
    mypy holds it to `TurnInvoker` and a stub that drifts from the protocol stops standing in for
    the dependency.

    A caller whose subject is the *turn* wires `invoker_factory` and the real admission instead:
    this records the ask and founds nothing."""

    turns: list[RecordedTurn] = field(default_factory=list)

    async def invoke(
        self,
        conversation_id: UUID,
        agent_id: UUID,
        message: str,
        idempotency_key: str,
        *,
        holds_work_already_done: bool = False,
        as_scheduled: bool = False,
        standalone: bool = False,
        unless_member_since: int | None = None,
        unless_member_arrival_since: int | None = None,
        runtime_config: TurnRuntimeConfig | None = None,
        model_accounts: tuple[ModelAccountCapability, ...] = (),
        fired_by: FiredBy | None = None,
    ) -> UUID | None:
        self.turns.append(
            RecordedTurn(
                conversation_id=conversation_id,
                agent_id=agent_id,
                message=message,
                idempotency_key=idempotency_key,
                standalone=standalone,
                runtime_config=runtime_config,
            )
        )
        return uuid4()

    async def redispatch(self, conversation_id: UUID, ended_turn_id: UUID) -> UUID | None:
        return None

    async def member_reach(self, member_id: UUID, limit: int) -> tuple[MemberReach, ...]:
        return ()
