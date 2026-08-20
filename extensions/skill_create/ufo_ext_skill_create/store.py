"""Agent-owned member-authored skills, persisted across turns and disposable sandboxes."""

import base64
import hashlib
import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.sdk.context import ExtensionContext, agent_current
from ufo.sdk.index import IndexScope
from ufo.sdk.skills import RuntimeSkill, SkillCard, parse_skill_content

DIGEST_PREFIX = "sha256:"
SKILL_NAME_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")
MAX_USER_SKILLS_PER_AGENT = 5000
MAX_PINNED_USER_SKILLS = 10
SKILL_OWNER_KIND = "skill"

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
    sa.Column("description", sa.Text, nullable=False),
    sa.Column("depends", sa.Text, nullable=False),
    sa.Column("pinned", sa.Boolean, nullable=False),
    sa.Column("indexed_digest", sa.Text, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)


def skill_owner_id(agent_id: UUID, name: str) -> str:
    """The index owner id of one agent's skill: the scope a save's chunks upsert under and a
    delete prunes."""
    return f"{agent_id}:{name}"


def agent_subject(agent_id: UUID) -> str:
    """The index subject scoping skill retrieval to one agent's saved corpus."""
    return f"agent:{agent_id}"


def _save_lock_key(workspace_id: UUID, agent_id: UUID) -> int:
    digest = hashlib.sha256(f"{workspace_id}:{agent_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


class SkillCollidesWithCoreSkill(ValueError):
    """A save named a skill that a core or pack skill already owns. A user-skill may never shadow
    one, so the save is refused rather than persisted — surfaced to the model as a tool error."""


class TooManyUserSkills(ValueError):
    """A save would exceed the agent's user-skill cap."""


class PinnedSkillLimit(ValueError):
    """A save would leave the agent with more than the pinned-skill cap."""


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
        pinned: bool = False,
    ) -> RuntimeSkill:
        """Validate and upsert one skill for the bound agent, its routing-card columns written by
        the same parse that validates the content — a card can never drift from the frontmatter.
        The ownership and cap checks run inside the write transaction, serialized per (workspace,
        agent) by a Postgres advisory transaction lock, so concurrent saves cannot race past a
        cap; SQLite's single writer serializes on its own."""
        scope = agent_current()
        if not SKILL_NAME_PATTERN.fullmatch(name):
            raise InvalidSkillName(
                f"skill name {name!r} must be a lowercase slug — letters and digits with internal "
                f"hyphens (no slashes, dots, uppercase, or spaces)"
            )
        skill = parse_skill_content(name, files)
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
        card = {
            "digest": digest,
            "content": content.decode(),
            "description": skill.description,
            "depends": json.dumps(list(skill.depends)),
            "pinned": pinned,
        }
        lock_key = _save_lock_key(scope.workspace_id, scope.agent_id)
        async with self.ctx.transaction() as connection:
            if connection.dialect.name == "postgresql":
                await connection.execute(
                    sa.select(sa.func.pg_advisory_xact_lock(sa.cast(lock_key, sa.BigInteger)))
                )
            already_owned = await self._owns(connection, name)
            if name in registry_names and not already_owned:
                raise SkillCollidesWithCoreSkill(
                    f"skill {name!r} is already a core or pack skill and cannot be overridden"
                )
            if not already_owned and await self._count(connection) >= MAX_USER_SKILLS_PER_AGENT:
                raise TooManyUserSkills(
                    f"this agent already has {MAX_USER_SKILLS_PER_AGENT} saved skills — remove "
                    f"or re-save an existing one instead of adding another"
                )
            if pinned and await self._pinned_count(connection, name) >= MAX_PINNED_USER_SKILLS:
                raise PinnedSkillLimit(
                    f"this agent already has {MAX_PINNED_USER_SKILLS} pinned skills — unpin one "
                    f"before pinning another"
                )
            updated = await connection.execute(
                sa.update(user_skill)
                .values(updated_at=sa.func.now(), **card)
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
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                        **card,
                    )
                )
        return skill

    async def cards(self) -> tuple[SkillCard, ...]:
        """Every saved skill's routing card for the bound agent — one projection of card columns,
        never touching content. A row whose stored bundle is corrupt still yields its card; the
        load of a named skill is where corruption surfaces. A row with an empty description — the
        backfill's sentinel for content it could not parse — is skipped with a log, since a card
        that routes nowhere and loads nothing would only mislead the list."""
        scope = agent_current()
        async with self.ctx.transaction() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        user_skill.c.name,
                        user_skill.c.description,
                        user_skill.c.depends,
                        user_skill.c.pinned,
                    )
                    .where(
                        user_skill.c.workspace_id == scope.workspace_id,
                        user_skill.c.agent_id == scope.agent_id,
                    )
                    .order_by(user_skill.c.name)
                )
            ).all()
        cards: list[SkillCard] = []
        for row in rows:
            if not row.description:
                logger.warning(
                    "skill_create.card_without_description_skipped",
                    extra={
                        "workspace_id": str(scope.workspace_id),
                        "agent_id": str(scope.agent_id),
                        "skill": row.name,
                    },
                )
                continue
            cards.append(
                SkillCard(
                    name=row.name,
                    description=row.description,
                    depends=tuple(json.loads(row.depends)),
                    pinned=row.pinned,
                )
            )
        return tuple(cards)

    async def materialize(self, name: str) -> RuntimeSkill | None:
        """One named skill parsed from its stored files, or None when the bound agent has no such
        name. A corrupt stored bundle raises — a load of a named skill failing loud beats a silent
        miss; tolerance lives in the card projection."""
        files = await self.files(name)
        if files is None:
            return None
        return parse_skill_content(name, files)

    async def materialize_all(self) -> tuple[RuntimeSkill, ...]:
        """Every saved skill of the bound agent parsed from stored files in one read, skipping
        corrupt rows with a log — the listing path's read-time tolerance, so one bad bundle never
        hides the rest. A load by name stays fail-loud through `materialize`."""
        scope = agent_current()
        async with self.ctx.transaction() as connection:
            rows = (
                await connection.execute(
                    sa.select(user_skill.c.name, user_skill.c.content)
                    .where(
                        user_skill.c.workspace_id == scope.workspace_id,
                        user_skill.c.agent_id == scope.agent_id,
                    )
                    .order_by(user_skill.c.name)
                )
            ).all()
        skills: list[RuntimeSkill] = []
        for row in rows:
            try:
                stored = StoredSkill.model_validate_json(row.content)
                files = {path: base64.b64decode(content) for path, content in stored.files.items()}
                skills.append(parse_skill_content(row.name, files))
            except Exception as error:
                logger.warning(
                    "skill_create.user_skill_load_failed",
                    extra={
                        "workspace_id": str(scope.workspace_id),
                        "agent_id": str(scope.agent_id),
                        "skill": row.name,
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
        """Delete one skill: mark the row stale, prune its index scope, then drop the row — in
        that order, so a crash at any point leaves either a stale live row the index job repairs
        on its next tick or a fully deleted skill, never a rowless ghost chunk nothing prunes."""
        scope = agent_current()
        if self.ctx.index is not None:
            async with self.ctx.transaction() as connection:
                await connection.execute(
                    sa.update(user_skill)
                    .values(indexed_digest=None)
                    .where(
                        user_skill.c.workspace_id == scope.workspace_id,
                        user_skill.c.agent_id == scope.agent_id,
                        user_skill.c.name == name,
                    )
                )
            await self.ctx.index.delete(
                IndexScope(SKILL_OWNER_KIND, skill_owner_id(scope.agent_id, name))
            )
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

    async def _owns(self, connection: AsyncConnection, name: str) -> bool:
        scope = agent_current()
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

    async def _count(self, connection: AsyncConnection) -> int:
        scope = agent_current()
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

    async def _pinned_count(self, connection: AsyncConnection, excluding: str) -> int:
        scope = agent_current()
        return (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(user_skill)
                .where(
                    user_skill.c.workspace_id == scope.workspace_id,
                    user_skill.c.agent_id == scope.agent_id,
                    user_skill.c.pinned.is_(True),
                    user_skill.c.name != excluding,
                )
            )
        ).scalar_one()
