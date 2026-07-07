"""First-run onboarding: the durable workspace + owner + default agent, the model key,
environment-provided credential slots, then each installed extension's onboarding steps — the flow
a cold start runs.

The core steps create the workspace and its owner exactly once; a second run against a workspace
that already has an owner raises `AlreadyInitialized` rather than double-creating. Each installed
extension's onboarding steps then fire with that extension's scoped `ExtensionContext` — the same
handle its jobs receive. Onboarding runs within an already-open db boundary (the CLI opens it and
owns migrations, since Alembic drives its own event loop), so it mirrors the job runner rather than
managing the connection itself."""

import os
from dataclasses import dataclass
from uuid import UUID, uuid4

import sqlalchemy as sa

from ufo.config import Config
from ufo.credentials import CredentialStore
from ufo.db import workspace_tx
from ufo.ext.context import context_for
from ufo.ext.manifest import Manifest
from ufo.models.registry import model_registry
from ufo.o11y import log
from ufo.schema import tables
from ufo.schema.records import DEFAULT_AGENT_NAME

DEFAULT_AGENT_PROMPT = "You are a helpful assistant."


class AlreadyInitialized(RuntimeError):
    """A first run was asked of a workspace that already has an owner."""


@dataclass(frozen=True)
class Onboarded:
    """The workspace and owner a first run created; a surface binds its identity to them."""

    workspace_id: UUID
    member_id: UUID


def env_credentials(manifest: Manifest) -> dict[str, str]:
    """Declared slot values the environment provides — a slot seeds from its upper-cased name
    (`slack_bot_token` ← `SLACK_BOT_TOKEN`), so a cold start connects an extension without a
    post-init fill step."""
    return {
        slot.name: value
        for slot in manifest.credentials
        if (value := os.environ.get(slot.name.upper()))
    }


async def run_onboarding_steps(
    manifests: tuple[Manifest, ...], workspace_id: UUID, credentials: CredentialStore | None
) -> None:
    """Seed each extension's environment-provided credential slots, then fire its onboarding steps
    for a freshly created workspace, every step with that extension's scoped ExtensionContext (its
    declared credential slots). Seeding runs first so a step that reads a slot it declared finds
    the deploy-provided value. Steps run AFTER core access is established, and a step that raises
    is logged and skipped — one add-on's failure can neither strand the core workspace nor block
    another extension's steps."""
    for manifest in manifests:
        if credentials is not None:
            for slot, value in env_credentials(manifest).items():
                await credentials.put(workspace_id, slot, value)
                log("onboarding.credential_seeded", extension=manifest.name, slot=slot)
        if not manifest.onboarding_steps:
            continue
        if credentials is None:
            log("onboarding.steps_skipped_no_credential_key", extension=manifest.name)
            continue
        context = context_for(
            workspace_id,
            manifest.name,
            frozenset(slot.name for slot in manifest.credentials),
            credentials,
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
    workspace + owner + default agent once, then run each installed extension's onboarding steps."""

    config: Config
    email: str
    model: str
    credentials: CredentialStore | None
    manifests: tuple[Manifest, ...]
    workspace_id: UUID | None = None

    async def run(self) -> Onboarded:
        onboarded = await self.create()
        await self.run_steps(onboarded)
        return onboarded

    async def create(self) -> Onboarded:
        """Establish the durable core with no dependency on any extension — model key, then
        workspace + owner + default agent in one transaction — so `ufoctl init` can bind the CLI
        token (core access) before running add-on onboarding steps that might fail."""
        self._require_model_key()
        self._require_credentials_for_steps()
        return await self._create_workspace()

    async def run_steps(self, onboarded: Onboarded) -> None:
        await run_onboarding_steps(self.manifests, onboarded.workspace_id, self.credentials)

    def _require_credentials_for_steps(self) -> None:
        """Fail before the DB (like the model key) when installed extensions contribute onboarding
        steps or the environment provides declared slot values but no credential key is set — the
        scoped context and the seeding both need the store, and failing here leaves no half-created
        workspace behind."""
        if self.credentials is not None:
            return
        if any(m.onboarding_steps for m in self.manifests) or any(
            env_credentials(m) for m in self.manifests
        ):
            raise RuntimeError(
                "installed extensions contribute onboarding steps or the environment provides "
                f"credential values that need {self.config.credentials.key_env} set before init "
                "can run"
            )

    def _require_model_key(self) -> None:
        env_name = self._model_key_env()
        if env_name is None:
            return
        if not os.environ.get(env_name):
            raise RuntimeError(
                f"model {self.model!r} needs {env_name} set before the first turn can run"
            )

    def _model_key_env(self) -> str | None:
        """A core provider's configured key env, or None for an extension-contributed provider that
        resolves its own key lazily at turn time (which init cannot name to check eagerly)."""
        return model_registry(self.config, self.manifests).model_key_env(self.model, self.config)

    async def _create_workspace(self) -> Onboarded:
        async with workspace_tx() as connection:
            owner = (await connection.execute(sa.select(tables.member.c.email))).first()
            if owner is not None:
                raise AlreadyInitialized(f"already initialized (owner {owner.email})")
            workspace_id, member_id, agent_id = self.workspace_id or uuid4(), uuid4(), uuid4()
            await connection.execute(
                sa.insert(tables.workspace).values(
                    id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
                )
            )
            await connection.execute(
                sa.insert(tables.member).values(
                    id=member_id,
                    workspace_id=workspace_id,
                    email=self.email,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            await connection.execute(
                sa.insert(tables.agent).values(
                    id=agent_id,
                    workspace_id=workspace_id,
                    name=DEFAULT_AGENT_NAME,
                    prompt=DEFAULT_AGENT_PROMPT,
                    model=self.model,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            return Onboarded(workspace_id=workspace_id, member_id=member_id)
