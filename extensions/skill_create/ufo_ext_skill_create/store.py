"""Agent-owned member-authored skills, persisted across turns and disposable sandboxes."""

import base64
import hashlib
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict

from ufo.sdk.context import ExtensionContext, agent_current
from ufo.sdk.skills import RuntimeSkill, parse_skill_content

DIGEST_PREFIX = "sha256:"
SKILL_NAME_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")
MAX_USER_SKILLS_PER_AGENT = 100

logger = logging.getLogger(__name__)

_metadata = sa.MetaData()
user_skill = sa.Table(
    "user_skill",
    _metadata,
    sa.Column("workspace_id", sa.Uuid, primary_key=True),
    sa.Column("agent_id", sa.Uuid, primary_key=True),
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
    """A save would exceed the agent's user-skill cap."""


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
    """Validate and persist skills under the ambient workspace and agent."""

    ctx: ExtensionContext

    async def save(
        self,
        name: str,
        files: Mapping[str, bytes],
        registry_names: frozenset[str],
    ) -> RuntimeSkill:
        """Validate and upsert one skill for the bound agent."""
        scope = agent_current()
        if not SKILL_NAME_PATTERN.fullmatch(name):
            raise InvalidSkillName(
                f"skill name {name!r} must be a lowercase slug — letters and digits with internal "
                f"hyphens (no slashes, dots, uppercase, or spaces)"
            )
        skill = parse_skill_content(name, files)
        already_owned = await self._owns(name)
        if name in registry_names and not already_owned:
            raise SkillCollidesWithCoreSkill(
                f"skill {name!r} is already a core or pack skill and cannot be overridden"
            )
        if not already_owned and await self._count() >= MAX_USER_SKILLS_PER_AGENT:
            raise TooManyUserSkills(
                f"this agent already has {MAX_USER_SKILLS_PER_AGENT} saved skills — remove "
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
                    user_skill.c.workspace_id == scope.workspace_id,
                    user_skill.c.agent_id == scope.agent_id,
                    user_skill.c.name == name,
                )
            )
            if updated.rowcount == 0:
                await connection.execute(
                    sa.insert(user_skill).values(
                        workspace_id=scope.workspace_id,
                        agent_id=scope.agent_id,
                        name=name,
                        digest=digest,
                        content=content.decode(),
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
        return skill

    async def load_all(self) -> tuple[RuntimeSkill, ...]:
        """Parse every skill owned by the bound agent, skipping corrupt member content."""
        scope = agent_current()
        async with self.ctx.transaction() as connection:
            rows = (
                (
                    await connection.execute(
                        sa.select(user_skill.c.name, user_skill.c.content)
                        .where(
                            user_skill.c.workspace_id == scope.workspace_id,
                            user_skill.c.agent_id == scope.agent_id,
                        )
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
                        "workspace_id": str(scope.workspace_id),
                        "agent_id": str(scope.agent_id),
                        "skill": row["name"],
                        "error": str(error),
                    },
                )
        return tuple(skills)

    async def files(self, name: str) -> dict[str, bytes] | None:
        """Return one bound agent skill's files, or None."""
        scope = agent_current()
        async with self.ctx.transaction() as connection:
            row = (
                await connection.execute(
                    sa.select(user_skill.c.content).where(
                        user_skill.c.workspace_id == scope.workspace_id,
                        user_skill.c.agent_id == scope.agent_id,
                        user_skill.c.name == name,
                    )
                )
            ).one_or_none()
        if row is None:
            return None
        stored = StoredSkill.model_validate_json(row.content)
        return {path: base64.b64decode(content) for path, content in stored.files.items()}

    async def delete(self, name: str) -> None:
        scope = agent_current()
        async with self.ctx.transaction() as connection:
            await connection.execute(
                sa.delete(user_skill).where(
                    user_skill.c.workspace_id == scope.workspace_id,
                    user_skill.c.agent_id == scope.agent_id,
                    user_skill.c.name == name,
                )
            )

    async def timestamps(self, name: str) -> tuple[datetime, datetime] | None:
        scope = agent_current()
        async with self.ctx.transaction() as connection:
            row = (
                await connection.execute(
                    sa.select(user_skill.c.created_at, user_skill.c.updated_at).where(
                        user_skill.c.workspace_id == scope.workspace_id,
                        user_skill.c.agent_id == scope.agent_id,
                        user_skill.c.name == name,
                    )
                )
            ).one_or_none()
        return None if row is None else (row.created_at, row.updated_at)

    async def _count(self) -> int:
        scope = agent_current()
        async with self.ctx.transaction() as connection:
            return (
                await connection.execute(
                    sa.select(sa.func.count())
                    .select_from(user_skill)
                    .where(
                        user_skill.c.workspace_id == scope.workspace_id,
                        user_skill.c.agent_id == scope.agent_id,
                    )
                )
            ).scalar_one()

    async def _owns(self, name: str) -> bool:
        scope = agent_current()
        async with self.ctx.transaction() as connection:
            row = (
                await connection.execute(
                    sa.select(user_skill.c.name).where(
                        user_skill.c.workspace_id == scope.workspace_id,
                        user_skill.c.agent_id == scope.agent_id,
                        user_skill.c.name == name,
                    )
                )
            ).one_or_none()
        return row is not None
