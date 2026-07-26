"""The workspace-scoped user-skill store: a member (through the agent) authors a skill directory in
the workspace, and `save` validates it with the same parser core and pack skills go through, then
persists it so it survives across turns and disposable sandboxes.

The content — the `SKILL.md` text plus any bundled assets — is serialized to a base64 file map and
held in the `content` column of the extension's own `user_skill` table, keyed under the workspace;
a `digest` over the serialized bundle carries change identity. `load_all` is what the runtime-skills
provider reads each turn to merge a workspace's saved skills into that turn's `SkillRegistry`, so
`load_skill` and the `{{skill_index}}` resolve them beside core's own. The scoping is the whole
point: a saved skill is user-controlled text mounted into the agent's own context, so it is bound
to one workspace and can never be seen by another, nor shadow a core or pack skill."""

import base64
import hashlib
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict

from ufo.sdk.context import ExtensionContext
from ufo.sdk.skills import RuntimeSkill, parse_skill_content

DIGEST_PREFIX = "sha256:"
SKILL_NAME_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")
# Every saved skill is fetched and parsed on the turn's hot path (load_all), so the count is bounded
# — a workspace cannot make its own turns arbitrarily slow, and storage stays bounded.
MAX_USER_SKILLS_PER_WORKSPACE = 100

logger = logging.getLogger(__name__)

_metadata = sa.MetaData()
user_skill = sa.Table(
    "user_skill",
    _metadata,
    sa.Column("workspace_id", sa.Uuid, primary_key=True),
    sa.Column("name", sa.Text, primary_key=True),
    sa.Column("digest", sa.Text, nullable=False),
    sa.Column("content", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)


class SkillCollidesWithCoreSkill(ValueError):
    """A save named a skill that a core or pack skill already owns. A user-skill may never shadow
    one, so the save is refused rather than persisted — surfaced to the model as a tool error."""


class TooManyUserSkills(ValueError):
    """A save would exceed the workspace's user-skill cap. Every saved skill is loaded and parsed on
    each turn, so the count is bounded; the member re-saves an existing name (an update, always
    allowed) or removes one first — surfaced to the model as a tool error."""


class InvalidSkillName(ValueError):
    """A save named a skill with something that is not a safe slug. The name is both the registry
    key and the persisted key, so it is validated up front: a traversing name (`../../etc`), a
    dot-segment (`.`, `..`), a slash (a user-skill is one directory, never a `parent/child` tree),
    uppercase, whitespace, or other punctuation is refused rather than left to a downstream guard —
    surfaced to the model as a tool error."""


class StoredSkill(BaseModel):
    """The persisted form of a saved user-skill: each file of the skill directory (`SKILL.md` and
    any bundled assets) mapped from its relative path to its base64-encoded bytes. Crosses the
    boundary into the `user_skill.content` column, so it validates on the way back out."""

    model_config = ConfigDict(extra="forbid")
    files: dict[str, str]


@dataclass(frozen=True)
class UserSkillStore:
    """The user-skill workflow over the extension's scoped context: validate and persist an authored
    skill, load a workspace's saved skills back for the turn. Reads and writes only the extension's
    own `user_skill` table, through the workspace-scoped `transaction()` core threads onto the
    context — never a core internal, never another workspace."""

    ctx: ExtensionContext

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
        never shadows one. The serialized bundle is stored in the `content` column; the row is
        upserted so re-saving the same name replaces it in place."""
        if not SKILL_NAME_PATTERN.fullmatch(name):
            raise InvalidSkillName(
                f"skill name {name!r} must be a lowercase slug — letters and digits with internal "
                f"hyphens (no slashes, dots, uppercase, or spaces)"
            )
        skill = parse_skill_content(name, files)
        already_owned = await self._owns(workspace_id, name)
        if name in registry_names and not already_owned:
            raise SkillCollidesWithCoreSkill(
                f"skill {name!r} is already a core or pack skill and cannot be overridden"
            )
        if not already_owned and await self._count(workspace_id) >= MAX_USER_SKILLS_PER_WORKSPACE:
            raise TooManyUserSkills(
                f"this workspace already has {MAX_USER_SKILLS_PER_WORKSPACE} saved skills — remove "
                f"or re-save an existing one instead of adding another"
            )
        content = (
            StoredSkill(
                files={
                    path: base64.b64encode(payload).decode()
                    for path, payload in sorted(files.items())
                }
            )
            .model_dump_json()
            .encode()
        )
        digest = DIGEST_PREFIX + hashlib.sha256(content).hexdigest()
        async with self.ctx.transaction() as connection:
            updated = await connection.execute(
                sa.update(user_skill)
                .values(digest=digest, content=content.decode(), updated_at=sa.func.now())
                .where(
                    user_skill.c.workspace_id == workspace_id,
                    user_skill.c.name == name,
                )
            )
            if updated.rowcount == 0:
                await connection.execute(
                    sa.insert(user_skill).values(
                        workspace_id=workspace_id,
                        name=name,
                        digest=digest,
                        content=content.decode(),
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
        return skill

    async def load_all(self, workspace_id: UUID) -> tuple[RuntimeSkill, ...]:
        """Every user-skill this workspace has saved, parsed back into RuntimeSkills for the turn's
        registry. Scoped strictly to `workspace_id`, so one workspace's skills never reach another;
        each is re-parsed on read, so a stored bundle validates the same way it did on save.

        A saved skill is optional member content, not core state, so one that no longer loads — a
        corrupt bundle a later parser rejects — is dropped with a log rather than raised, exactly
        as `SkillRegistry.merged_with` drops a shadowing one. Raising here would wedge every turn in
        the workspace on a single bad skill, with no in-chat path to remove it; skip-and-log keeps
        the turn (and the member's other skills) working."""
        async with self.ctx.transaction() as connection:
            rows = (
                (
                    await connection.execute(
                        sa.select(user_skill.c.name, user_skill.c.content)
                        .where(user_skill.c.workspace_id == workspace_id)
                        .order_by(user_skill.c.name)
                    )
                )
                .mappings()
                .all()
            )
        skills: list[RuntimeSkill] = []
        for row in rows:
            try:
                stored = StoredSkill.model_validate_json(row["content"])
                files = {path: base64.b64decode(content) for path, content in stored.files.items()}
                skills.append(parse_skill_content(row["name"], files))
            except Exception as error:
                logger.warning(
                    "skill_create.user_skill_load_failed",
                    extra={
                        "workspace_id": str(workspace_id),
                        "skill": row["name"],
                        "error": str(error),
                    },
                )
        return tuple(skills)

    async def files(self, workspace_id: UUID, name: str) -> dict[str, bytes] | None:
        """One saved skill's file map — the persisted bundle decoded back to bytes, None when the
        workspace holds no skill of that name."""
        async with self.ctx.transaction() as connection:
            row = (
                await connection.execute(
                    sa.select(user_skill.c.content).where(
                        user_skill.c.workspace_id == workspace_id,
                        user_skill.c.name == name,
                    )
                )
            ).one_or_none()
        if row is None:
            return None
        stored = StoredSkill.model_validate_json(row.content)
        return {path: base64.b64decode(content) for path, content in stored.files.items()}

    async def delete(self, workspace_id: UUID, name: str) -> None:
        async with self.ctx.transaction() as connection:
            await connection.execute(
                sa.delete(user_skill).where(
                    user_skill.c.workspace_id == workspace_id,
                    user_skill.c.name == name,
                )
            )

    async def timestamps(self, workspace_id: UUID, name: str) -> tuple[datetime, datetime] | None:
        async with self.ctx.transaction() as connection:
            row = (
                await connection.execute(
                    sa.select(user_skill.c.created_at, user_skill.c.updated_at).where(
                        user_skill.c.workspace_id == workspace_id,
                        user_skill.c.name == name,
                    )
                )
            ).one_or_none()
        return None if row is None else (row.created_at, row.updated_at)

    async def _count(self, workspace_id: UUID) -> int:
        async with self.ctx.transaction() as connection:
            return (
                await connection.execute(
                    sa.select(sa.func.count())
                    .select_from(user_skill)
                    .where(user_skill.c.workspace_id == workspace_id)
                )
            ).scalar_one()

    async def _owns(self, workspace_id: UUID, name: str) -> bool:
        async with self.ctx.transaction() as connection:
            row = (
                await connection.execute(
                    sa.select(user_skill.c.name).where(
                        user_skill.c.workspace_id == workspace_id,
                        user_skill.c.name == name,
                    )
                )
            ).one_or_none()
        return row is not None
