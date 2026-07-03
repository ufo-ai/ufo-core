"""First-run onboarding: the durable workspace + owner + default agent, the model key, then each
installed extension's onboarding steps — the flow a cold start runs.

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

from selfhost.config import Config
from selfhost.credentials import CredentialStore
from selfhost.db import workspace_tx
from selfhost.ext.context import context_for
from selfhost.ext.manifest import Manifest
from selfhost.schema import tables
from selfhost.schema.records import DEFAULT_AGENT_NAME

DEFAULT_AGENT_PROMPT = "You are a helpful assistant."
ANTHROPIC_MODEL_PREFIXES = ("claude-",)
OPENAI_MODEL_PREFIXES = ("gpt-", "o1", "o3", "o4", "chatgpt-")


class AlreadyInitialized(RuntimeError):
    """A first run was asked of a workspace that already has an owner."""


@dataclass(frozen=True)
class Onboarded:
    """The workspace and owner a first run created; a surface binds its identity to them."""

    workspace_id: UUID
    member_id: UUID


async def run_onboarding_steps(
    manifests: tuple[Manifest, ...], workspace_id: UUID, credentials: CredentialStore | None
) -> None:
    """Fire each installed extension's onboarding steps for a freshly created workspace, every step
    with that extension's scoped ExtensionContext (its declared credential slots). An extension that
    contributes steps without a credential key set fails loud, since its context needs the store."""
    for manifest in manifests:
        if not manifest.onboarding_steps:
            continue
        if credentials is None:
            raise RuntimeError(
                f"extension {manifest.name!r} contributes onboarding steps "
                "but no credential key is set"
            )
        context = context_for(
            workspace_id,
            manifest.name,
            frozenset(slot.name for slot in manifest.credentials),
            credentials,
        )
        for step in manifest.onboarding_steps:
            await step.handler(context)


@dataclass(frozen=True)
class Onboarding:
    """The durable first-run flow behind `selfhost init`: require the chosen model's key, create the
    workspace + owner + default agent once, then run each installed extension's onboarding steps."""

    config: Config
    email: str
    model: str
    credentials: CredentialStore | None
    manifests: tuple[Manifest, ...]

    async def run(self) -> Onboarded:
        self._require_model_key()
        onboarded = await self._create_workspace()
        await run_onboarding_steps(self.manifests, onboarded.workspace_id, self.credentials)
        return onboarded

    def _require_model_key(self) -> None:
        env_name = self._model_key_env()
        if not os.environ.get(env_name):
            raise RuntimeError(
                f"model {self.model!r} needs {env_name} set before the first turn can run"
            )

    def _model_key_env(self) -> str:
        if self.model.startswith(ANTHROPIC_MODEL_PREFIXES):
            return self.config.models.anthropic_api_key_env
        if self.model.startswith(OPENAI_MODEL_PREFIXES):
            return self.config.models.openai_api_key_env
        raise ValueError(f"no provider serves model {self.model!r}")

    async def _create_workspace(self) -> Onboarded:
        async with workspace_tx() as connection:
            owner = (await connection.execute(sa.select(tables.member.c.email))).first()
            if owner is not None:
                raise AlreadyInitialized(f"already initialized (owner {owner.email})")
            workspace_id, member_id, agent_id = uuid4(), uuid4(), uuid4()
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
