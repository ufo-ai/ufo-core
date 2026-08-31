"""First-run onboarding: the durable workspace + initial admin + main agent, the model key,
environment-provided credential slots, then each installed extension's onboarding steps — the flow
a cold start runs.

The core steps create the workspace and its principals exactly once; a second run against a
workspace that already has a member raises `AlreadyInitialized` rather than double-creating. Each
installed extension's onboarding steps then fire with that extension's scoped `ExtensionContext`
— the same handle its jobs receive. Onboarding runs within an already-open db boundary; the CLI
owns migrations because Alembic drives its own event loop."""

from dataclasses import dataclass
from uuid import UUID, uuid4

import sqlalchemy as sa

from ufo.config import Config
from ufo.db import workspace_tx
from ufo.harness.models.interface import AUTO_MODEL
from ufo.harness.models.registry import model_registry
from ufo.harness.o11y import log
from ufo.runtime.access.credentials import CredentialStore, deploy_env
from ufo.runtime.ext.context import context_for
from ufo.runtime.ext.manifest import Manifest
from ufo.runtime.kinds.provisioning import AgentProvisioning
from ufo.runtime.seats import create_member
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import (
    DEFAULT_AGENT_NAME,
    DEFAULT_REASONING_EFFORT,
    MAIN_AGENT_ICON,
    ReasoningEffort,
)

DEFAULT_AGENT_PROMPT = "You are a helpful assistant."
DEFAULT_AGENT_MODEL = AUTO_MODEL


class AlreadyInitialized(RuntimeError):
    """A first run was asked of an initialized workspace."""


@dataclass(frozen=True)
class Onboarded:
    """The workspace and initial admin a first run created; a surface binds its identity to them."""

    workspace_id: UUID
    member_id: UUID


async def run_onboarding_steps(
    manifests: tuple[Manifest, ...], workspace_id: UUID, credentials: CredentialStore | None
) -> None:
    """Fire each installed extension's onboarding steps for a freshly created workspace, every step
    with that extension's scoped ExtensionContext (its declared credential slots). A step that reads
    a declared slot finds the platform default live from the environment — credentials are read, not
    seeded per workspace, so rotating a deploy's value reaches every workspace. Steps run AFTER core
    access is established, and a step that raises is logged and skipped — one add-on's failure can
    neither strand the core workspace nor block another extension's steps."""
    with ws(workspace_id):
        for manifest in manifests:
            if not manifest.onboarding_steps:
                continue
            if credentials is None:
                log("onboarding.steps_skipped_no_credential_key", extension=manifest.name)
                continue
            context = context_for(
                manifest.name, frozenset(slot.name for slot in manifest.credentials)
            )
            for step in manifest.onboarding_steps:
                try:
                    await step.handler(context)
                except Exception as error:
                    log(
                        "onboarding.step_failed",
                        extension=manifest.name,
                        step=step.name,
                        error_class=type(error).__name__,
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
    reasoning: ReasoningEffort = DEFAULT_REASONING_EFFORT

    async def run(self) -> Onboarded:
        onboarded = await self.create()
        await self.run_steps(onboarded)
        return onboarded

    async def create(self) -> Onboarded:
        """Establish the durable core with no dependency on any extension — model key, then
        workspace + initial admin + main agent in one transaction — so `ufoctl init` can bind the
        CLI token before running add-on onboarding steps that might fail."""
        self._require_model_key()
        self._require_credentials_for_steps()
        return await self._create_workspace()

    async def run_steps(self, onboarded: Onboarded) -> None:
        await AgentProvisioning(self.manifests).apply(onboarded.workspace_id)
        await run_onboarding_steps(self.manifests, onboarded.workspace_id, self.credentials)

    def _require_credentials_for_steps(self) -> None:
        """Fail before the DB (like the model key) when installed extensions contribute onboarding
        steps but no credential key is set — a step's scoped context may store a per-workspace
        secret, and failing here leaves no half-created workspace behind. Platform defaults are read
        live from the environment and need no key."""
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

    async def _create_workspace(self) -> Onboarded:
        async with workspace_tx() as connection:
            member = (await connection.execute(sa.select(tables.member.c.email))).first()
            if member is not None:
                raise AlreadyInitialized(f"already initialized (member {member.email})")
            workspace_id, agent_id = uuid4(), uuid4()
            await connection.execute(
                sa.insert(tables.workspace).values(
                    id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
                )
            )
            member_id = await create_member(connection, workspace_id, self.email, is_admin=True)
            await connection.execute(
                sa.insert(tables.agent).values(
                    id=agent_id,
                    workspace_id=workspace_id,
                    name=DEFAULT_AGENT_NAME,
                    icon=MAIN_AGENT_ICON,
                    prompt=DEFAULT_AGENT_PROMPT,
                    model=self.model,
                    reasoning=self.reasoning,
                    is_main=True,
                    visibility="workspace",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            return Onboarded(workspace_id=workspace_id, member_id=member_id)
