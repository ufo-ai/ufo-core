"""First-run onboarding: the durable workspace + initial admin + main agent, the model key,
environment-provided credential slots, then each installed extension's onboarding steps — the flow
a cold start runs.

The core steps found the workspace through `Provisioning` exactly once; a second run against a
deploy that already has a member raises `AlreadyInitialized` rather than double-creating. Each
installed extension's onboarding steps then fire with that extension's scoped `ExtensionContext`
— the same handle its jobs receive. Onboarding runs within an already-open db boundary; the CLI
owns migrations because Alembic drives its own event loop."""

from dataclasses import dataclass
from uuid import UUID, uuid4

import sqlalchemy as sa

from ufo.config import Config
from ufo.db import workspace_tx
from ufo.harness.models.registry import model_registry
from ufo.harness.o11y import log
from ufo.product import (
    INIT_SURFACE,
    STEP_COMPLETED,
    STEP_FAILED,
    record_onboarding_step,
)
from ufo.runtime.access.credentials import CredentialStore, deploy_env
from ufo.runtime.billing.accounting import Ledger
from ufo.runtime.billing.spend import SpendGates
from ufo.runtime.ext.context import context_for
from ufo.runtime.ext.manifest import Manifest, minted_slots
from ufo.runtime.kinds.provisioning import AgentProvisioning
from ufo.runtime.provisioning import DEFAULT_AGENT_REASONING, Provisioned, Provisioning
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import ReasoningEffort


class AlreadyInitialized(RuntimeError):
    """A first run was asked of an initialized workspace."""


async def run_onboarding_steps(
    manifests: tuple[Manifest, ...],
    workspace_id: UUID,
    credentials: CredentialStore | None,
    spend: SpendGates,
    ledger: Ledger,
) -> None:
    """Fire each installed extension's onboarding steps for a freshly created workspace, every step
    with that extension's scoped ExtensionContext (its declared credential slots). A step that reads
    a declared slot finds the platform default live from the environment — credentials are read, not
    seeded per workspace, so rotating a deploy's value reaches every workspace. Steps run AFTER core
    access is established, and a step that raises is logged and skipped — one add-on's failure can
    neither strand the core workspace nor block another extension's steps.

    Each step's outcome is counted as an onboarding step of the workspace, so a step that raises is
    read off the board rather than off a log line: a workspace whose Slack step failed reached its
    first run holding nothing, and that is the same funnel a skipped screen belongs to."""
    with ws(workspace_id):
        for manifest in manifests:
            if not manifest.onboarding_steps:
                continue
            if credentials is None:
                log("onboarding.steps_skipped_no_credential_key", extension=manifest.name)
                continue
            context = context_for(
                manifest.name,
                frozenset(slot.name for slot in manifest.credentials),
                minted=minted_slots(manifest),
                spend=spend,
                ledger=ledger,
            )
            for step in manifest.onboarding_steps:
                named = f"{manifest.name}/{step.name}"
                try:
                    await step.handler(context)
                except Exception as error:
                    log(
                        "onboarding.step_failed",
                        extension=manifest.name,
                        step=step.name,
                        error_class=type(error).__name__,
                    )
                    await record_onboarding_step(
                        workspace_id, named, STEP_FAILED, surface=INIT_SURFACE
                    )
                else:
                    await record_onboarding_step(
                        workspace_id, named, STEP_COMPLETED, surface=INIT_SURFACE
                    )


@dataclass(frozen=True)
class Onboarding:
    """The durable first-run flow behind `ufoctl init`: require the chosen model's key, create the
    workspace + initial admin + main agent once, then run each installed extension's onboarding
    steps."""

    config: Config
    email: str
    model: str
    credentials: CredentialStore | None
    manifests: tuple[Manifest, ...]
    spend: SpendGates
    ledger: Ledger
    reasoning: ReasoningEffort = DEFAULT_AGENT_REASONING

    async def run(self) -> Provisioned:
        onboarded = await self.create()
        await self.run_steps(onboarded)
        return onboarded

    async def create(self) -> Provisioned:
        """Establish the durable core — model key, then workspace + initial admin + main agent +
        founded handlers in one transaction — so `ufoctl init` can bind the CLI token before running
        add-on onboarding steps that might fail."""
        self._require_model_key()
        self._require_credentials_for_steps()
        return await self._create_workspace()

    async def run_steps(self, onboarded: Provisioned) -> None:
        await AgentProvisioning(self.manifests).apply(onboarded.workspace_id)
        await run_onboarding_steps(
            self.manifests, onboarded.workspace_id, self.credentials, self.spend, self.ledger
        )

    def _require_credentials_for_steps(self) -> None:
        """A step's scoped context may store a per-workspace secret, so failing before the DB leaves
        no half-created workspace."""
        if self.credentials is not None:
            return
        if any(m.onboarding_steps for m in self.manifests):
            raise RuntimeError(
                "installed extensions contribute onboarding steps that need "
                f"{self.config.credentials.key_env} set before init can run"
            )

    def _require_model_key(self) -> None:
        env_name = self._model_key_env()
        if env_name is None:
            return
        if not deploy_env(env_name):
            raise RuntimeError(
                f"model {self.model!r} needs UFO_{env_name} (or {env_name}) set before the "
                "first turn can run"
            )

    def _model_key_env(self) -> str | None:
        """A core provider's configured key env, or None for an extension-contributed provider that
        resolves its own key lazily at turn time (which init cannot name to check eagerly)."""
        return model_registry(self.config, self.manifests).model_key_env(self.model, self.config)

    async def _create_workspace(self) -> Provisioned:
        async with workspace_tx() as connection:
            member = (await connection.execute(sa.select(tables.member.c.email))).first()
            if member is not None:
                raise AlreadyInitialized(f"already initialized (member {member.email})")
            provisioned = await Provisioning(
                founded=tuple(
                    spec for manifest in self.manifests for spec in manifest.workspace_founded
                )
            ).seat(connection, uuid4(), self.email)
            await connection.execute(
                sa.update(tables.agent)
                .where(
                    tables.agent.c.workspace_id == provisioned.workspace_id,
                    tables.agent.c.is_main,
                )
                .values(model=self.model, reasoning=self.reasoning)
            )
            return provisioned
