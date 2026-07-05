"""The workspace-scoped user-skill store: a member (through the agent) authors a skill directory in
the workspace, and `save` validates it with the same parser core and pack skills go through, then
persists it so it survives across turns and disposable sandboxes.

The content — the `SKILL.md` text plus any bundled assets — lives in the blob store, content-
addressed by a digest of the serialized bundle and keyed under the workspace; a small index row
(workspace_id, name, digest) points at it. `load_all` is what the turn loop reads to merge a
workspace's saved skills into that turn's `SkillRegistry`, so `load_skill`/`list_skills`/the
`{{skill_index}}` resolve them beside core's own. The scoping is the whole point: a saved skill is
user-controlled text mounted into the agent's own context, so it is bound to one workspace and can
never be seen by another, nor shadow a core or pack skill."""

import base64
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict

from selfhost.blob import BlobStore
from selfhost.db import workspace_tx
from selfhost.schema import tables
from selfhost.skills.runtime import RuntimeSkill, parse_skill_content

USER_SKILL_KEY_PREFIX = "user-skills/"
DIGEST_PREFIX = "sha256:"


class SkillCollidesWithCoreSkill(ValueError):
    """A save named a skill that a core or pack skill already owns. A user-skill may never shadow
    one, so the save is refused rather than persisted — surfaced to the model as a tool error."""


class StoredSkill(BaseModel):
    """The persisted form of a saved user-skill: each file of the skill directory (`SKILL.md` and
    any bundled assets) mapped from its relative path to its base64-encoded bytes. Crosses the
    boundary into the blob store, so it validates on the way back out."""

    model_config = ConfigDict(extra="forbid")
    files: dict[str, str]


@dataclass(frozen=True)
class UserSkillStore:
    blob: BlobStore

    async def save(
        self,
        workspace_id: UUID,
        name: str,
        files: Mapping[str, bytes],
        registry_names: frozenset[str],
    ) -> RuntimeSkill:
        """Validate an authored skill directory and persist it for this workspace, returning the
        parsed skill. `files` maps each relative path to its bytes (`SKILL.md` plus assets), read
        out of the sandbox. `registry_names` is the loadable set this turn already resolves (core,
        packs, and this workspace's other user-skills); a name in it that is not already this
        workspace's own user-skill belongs to a core or pack skill and is refused, so a user-skill
        never shadows one. The content is stored content-addressed by digest; the index row is
        upserted so re-saving the same name replaces it in place."""
        skill = parse_skill_content(name, files)
        already_owned = await self._owns(workspace_id, name)
        if name in registry_names and not already_owned:
            raise SkillCollidesWithCoreSkill(
                f"skill {name!r} is already a core or pack skill and cannot be overridden"
            )
        payload = (
            StoredSkill(
                files={
                    path: base64.b64encode(content).decode()
                    for path, content in sorted(files.items())
                }
            )
            .model_dump_json()
            .encode()
        )
        digest = DIGEST_PREFIX + hashlib.sha256(payload).hexdigest()
        await self.blob.put(_content_key(workspace_id, name, digest), payload)
        async with workspace_tx() as connection:
            updated = await connection.execute(
                sa.update(tables.user_skill)
                .values(digest=digest, updated_at=sa.func.now())
                .where(
                    tables.user_skill.c.workspace_id == workspace_id,
                    tables.user_skill.c.name == name,
                )
            )
            if updated.rowcount == 0:
                await connection.execute(
                    sa.insert(tables.user_skill).values(
                        workspace_id=workspace_id,
                        name=name,
                        digest=digest,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
        return skill

    async def load_all(self, workspace_id: UUID) -> tuple[RuntimeSkill, ...]:
        """Every user-skill this workspace has saved, parsed back into RuntimeSkills for the turn's
        registry. Scoped strictly to `workspace_id`, so one workspace's skills never reach another;
        each is re-parsed on read, so a stored bundle validates the same way it did on save."""
        async with workspace_tx() as connection:
            rows = (
                (
                    await connection.execute(
                        sa.select(tables.user_skill.c.name, tables.user_skill.c.digest).where(
                            tables.user_skill.c.workspace_id == workspace_id
                        )
                    )
                )
                .mappings()
                .all()
            )
        skills: list[RuntimeSkill] = []
        for row in rows:
            stored = StoredSkill.model_validate_json(
                await self.blob.get(_content_key(workspace_id, row["name"], row["digest"]))
            )
            files = {path: base64.b64decode(content) for path, content in stored.files.items()}
            skills.append(parse_skill_content(row["name"], files))
        return tuple(skills)

    async def _owns(self, workspace_id: UUID, name: str) -> bool:
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.user_skill.c.name).where(
                        tables.user_skill.c.workspace_id == workspace_id,
                        tables.user_skill.c.name == name,
                    )
                )
            ).one_or_none()
        return row is not None


def _content_key(workspace_id: UUID, name: str, digest: str) -> str:
    return f"{USER_SKILL_KEY_PREFIX}{workspace_id}/{name}/{digest.removeprefix(DIGEST_PREFIX)}"
