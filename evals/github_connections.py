"""GitHub connection routing: PR work needs the App while API operations need Composio."""

from uuid import UUID, uuid4

import sqlalchemy as sa
from cryptography.fernet import InvalidToken
from ufo_ext_coding.connect import GIT_INSTALLATION_SLOT
from ufo_ext_coding.manifest import GIT_SLOT

from evals.driver import EVAL_SURFACE
from evals.harness.capability import CapabilityCase, CapabilitySeed
from evals.harness.scorers import attempted_tools_scorer, combine, skill_scorer
from ufo.agent_scope import agent
from ufo.blob import BlobStore
from ufo.credentials import (
    CredentialRequestInvalid,
    CredentialSlotUnset,
    installed_credential_requests,
    open_installation,
)
from ufo.db import workspace_tx
from ufo.grants import GrantStore
from ufo.schema import tables
from ufo.sdk.context import CredentialAccess
from ufo.workspace import ws_current

GITHUB_ACCOUNT_ID = "eval-github-account"
GITHUB_INSTALLATION_ID = "123456"
GITHUB_PROVIDER = "github"


def _github_state(*, connector: bool, app: bool) -> CapabilitySeed:
    async def seed(_workspace_id: UUID, agent_id: UUID, _blob: BlobStore) -> None:
        workspace = ws_current()
        conversation_id = uuid4()
        async with workspace_tx() as connection:
            connections = (
                await connection.execute(
                    sa.select(
                        tables.connection.c.id,
                        tables.connection.c.account_id,
                        tables.connection.c.owner_member_id,
                    ).where(
                        tables.connection.c.workspace_id == workspace.workspace_id,
                        tables.connection.c.provider == GITHUB_PROVIDER,
                    )
                )
            ).all()
            foreign_accounts = tuple(
                row.account_id for row in connections if row.account_id != GITHUB_ACCOUNT_ID
            )
            if foreign_accounts:
                raise RuntimeError(
                    "github_connections requires a disposable workspace without GitHub accounts"
                )
            slots = frozenset(
                (
                    await connection.execute(
                        sa.select(tables.credential.c.slot).where(
                            tables.credential.c.workspace_id == workspace.workspace_id,
                            tables.credential.c.slot.in_((GIT_INSTALLATION_SLOT, GIT_SLOT)),
                        )
                    )
                )
                .scalars()
                .all()
            )
            member_id = (
                await connection.execute(
                    sa.select(tables.member.c.id)
                    .where(tables.member.c.workspace_id == workspace.workspace_id)
                    .order_by(tables.member.c.created_at)
                    .limit(1)
                )
            ).scalar_one()
        if GIT_SLOT in slots:
            raise RuntimeError(
                "github_connections requires a disposable workspace without a GitHub git token"
            )
        if GIT_INSTALLATION_SLOT in slots:
            try:
                sealed = await workspace.credential(GIT_INSTALLATION_SLOT)
                installation_id = open_installation(
                    installed_credential_requests().fernet,
                    workspace.workspace_id,
                    GIT_INSTALLATION_SLOT,
                    sealed,
                )
            except (CredentialRequestInvalid, CredentialSlotUnset, InvalidToken):
                raise RuntimeError(
                    "github_connections requires a disposable workspace without a GitHub App"
                ) from None
            if installation_id != GITHUB_INSTALLATION_ID:
                raise RuntimeError(
                    "github_connections requires a disposable workspace without a GitHub App"
                )
        fixture_connection = next(iter(connections), None)
        if fixture_connection is not None:
            with agent(agent_id):
                await GrantStore().disconnect(
                    fixture_connection.id,
                    actor_member_id=fixture_connection.owner_member_id,
                )
        async with workspace_tx() as connection:
            await connection.execute(
                sa.delete(tables.credential).where(
                    tables.credential.c.workspace_id == workspace.workspace_id,
                    tables.credential.c.slot == GIT_INSTALLATION_SLOT,
                )
            )
            if connector:
                await connection.execute(
                    sa.insert(tables.conversation).values(
                        id=conversation_id,
                        workspace_id=workspace.workspace_id,
                        agent_id=agent_id,
                        surface=EVAL_SURFACE,
                        queue_key=f"{EVAL_SURFACE}-github-state:{conversation_id}",
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
        if app:
            await CredentialAccess(frozenset({GIT_INSTALLATION_SLOT})).bind_installation(
                GIT_INSTALLATION_SLOT,
                GITHUB_INSTALLATION_ID,
            )
        if connector:
            with agent(agent_id):
                await GrantStore().record(
                    provider=GITHUB_PROVIDER,
                    account_id=GITHUB_ACCOUNT_ID,
                    host="",
                    grantor_member_id=member_id,
                    conversation_id=conversation_id,
                    shared=True,
                )

    return seed


CASES = (
    CapabilityCase(
        "neither-github-connection",
        "I haven't connected GitHub to this workspace yet. Set it up so you can work on issues "
        "and send pull requests for our private repositories.",
        combine(
            skill_scorer("coding", "code-review"),
            attempted_tools_scorer(
                (
                    ("connect_github", {}),
                    ("connect_account", {"provider": "github"}),
                ),
                ("spawn_subagent",),
                (("load_skill", "connect_github"), ("load_skill", "connect_account")),
            ),
        ),
        digest_tag="github-connections:neither",
        seed=_github_state(connector=False, app=False),
    ),
    CapabilityCase(
        "operations-work-prs-fail",
        "Our GitHub issues already work here, but private clone and push do not. Connect what's "
        "missing so pull request work can proceed.",
        combine(
            skill_scorer("coding", "code-review"),
            attempted_tools_scorer(
                (("connect_github", {}),),
                ("connect_account", "spawn_subagent"),
                (("load_skill", "connect_github"),),
            ),
        ),
        digest_tag="github-connections:operations-work-prs-fail",
        seed=_github_state(connector=True, app=False),
    ),
    CapabilityCase(
        "git-works-operations-fail",
        "Private clone and push already work here, but the agent can't read or update our GitHub "
        "issues. Connect what's missing.",
        combine(
            skill_scorer("coding", "code-review"),
            attempted_tools_scorer(
                (("connect_account", {"provider": "github"}),),
                ("connect_github", "spawn_subagent"),
                (("load_skill", "connect_account"),),
            ),
        ),
        digest_tag="github-connections:git-works-operations-fail",
        seed=_github_state(connector=False, app=True),
    ),
)
