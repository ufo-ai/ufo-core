"""Workspace-owned member-authored skills, persisted across turns and disposable sandboxes.

One name is one skill across the workspace: the portal's workspace page manages the set, every
agent whose `use_workspace_skills` setting holds loads it, and a row's `generation` moves on every
save — a save must carry the generation its read observed, so two writers cannot silently overwrite
each other."""

import base64
import hashlib
import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.sdk.context import ExtensionContext, agent_current
from ufo.sdk.index import IndexScope
from ufo.sdk.skills import RuntimeSkill, SkillCard, parse_skill_content

DIGEST_PREFIX = "sha256:"
SKILL_NAME_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")
MAX_USER_SKILLS_PER_WORKSPACE = 5000
MAX_PINNED_USER_SKILLS = 10
SKILL_OWNER_KIND = "skill"
SKILL_SUBJECT = "workspace"

logger = logging.getLogger(__name__)

_metadata = sa.MetaData()
user_skill = sa.Table(
    "user_skill",
    _metadata,
    sa.Column("workspace_id", sa.Uuid, primary_key=True),
    sa.Column("name", sa.Text, primary_key=True),
    sa.Column("generation", sa.Uuid, nullable=False),
    sa.Column("digest", sa.Text, nullable=False),
    sa.Column("content", sa.Text, nullable=False),
    sa.Column("description", sa.Text, nullable=False),
    sa.Column("depends", sa.Text, nullable=False),
    sa.Column("agents", sa.Text, nullable=False),
    sa.Column("pinned", sa.Boolean, nullable=False),
    sa.Column("indexed_digest", sa.Text, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)


def _save_lock_key(workspace_id: UUID) -> int:
    digest = hashlib.sha256(str(workspace_id).encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


class SkillCollidesWithCoreSkill(ValueError):
    """A save named a skill that a core or pack skill already owns. A user-skill may never shadow
    one, so the save is refused rather than persisted — surfaced to the model as a tool error."""


class StaleSkillGeneration(ValueError):
    """A save carried a generation other than the one the row now holds: another writer saved (or
    deleted) the skill after this writer's read. The save is refused rather than applied over the
    other writer's work — surfaced to the model as a tool error."""


class TooManyUserSkills(ValueError):
    """A save would exceed the workspace's user-skill cap."""


class PinnedSkillLimit(ValueError):
    """A save would leave the workspace with more than the pinned-skill cap."""


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
class SkillListing:
    """One saved skill as the object listing renders it."""

    name: str
    description: str
    pinned: bool


@dataclass(frozen=True)
class SkillRecord:
    """One saved skill whole, as the object detail reads it."""

    files: dict[str, bytes]
    description: str
    generation: UUID
    pinned: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class UserSkillStore:
    """Validate and persist the ambient workspace's skills — one row per name, saved under an
    optimistic generation fence."""

    ctx: ExtensionContext

    async def save(
        self,
        name: str,
        files: Mapping[str, bytes],
        registry_names: frozenset[str],
        pinned: bool = False,
        generation: UUID | None = None,
    ) -> RuntimeSkill:
        """Validate and persist one skill of the workspace, its routing-card columns written by
        the same parse that validates the content — a card can never drift from the frontmatter.
        `generation` is what the caller's read observed — None claims the name is unsaved; a save
        whose generation no longer matches the row refuses instead of overwriting the other
        writer's work, and a matching one moves it. The pin cap gates pinning an unpinned skill,
        never keeping a pin — a workspace holding more pins than the cap (the migration carries
        every agent's pins across) still re-saves each of them, since every portal save states the
        stored pin back. The checks run inside the write transaction,
        serialized per workspace by a Postgres advisory transaction lock, so concurrent saves
        cannot race past a cap or the fence; SQLite's single writer serializes on its own."""
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
            "agents": json.dumps(list(skill.agents)),
            "pinned": pinned,
        }
        lock_key = _save_lock_key(scope.workspace_id)
        async with self.ctx.transaction() as connection:
            if connection.dialect.name == "postgresql":
                await connection.execute(
                    sa.select(sa.func.pg_advisory_xact_lock(sa.cast(lock_key, sa.BigInteger)))
                )
            row = (
                await connection.execute(
                    sa.select(user_skill.c.generation, user_skill.c.pinned).where(
                        user_skill.c.workspace_id == scope.workspace_id,
                        user_skill.c.name == name,
                    )
                )
            ).one_or_none()
            held = None if row is None else row.generation
            already_pinned = row is not None and row.pinned
            if held is None and generation is not None:
                raise StaleSkillGeneration(
                    f"skill {name!r} was deleted after it was read — get it again, or apply "
                    f"without a generation to create it anew"
                )
            if held is not None and generation is None:
                raise StaleSkillGeneration(
                    f"skill {name!r} is already saved — pass the generation object_get returned "
                    f"to edit it"
                )
            if held is not None and generation != held:
                raise StaleSkillGeneration(
                    f"skill {name!r} changed after it was read — get it again and re-apply from "
                    f"the current state"
                )
            if held is None:
                if name in registry_names:
                    raise SkillCollidesWithCoreSkill(
                        f"skill {name!r} is already a core or pack skill and cannot be overridden"
                    )
                if await self._count(connection) >= MAX_USER_SKILLS_PER_WORKSPACE:
                    raise TooManyUserSkills(
                        f"this workspace already has {MAX_USER_SKILLS_PER_WORKSPACE} saved skills "
                        f"— remove or re-save an existing one instead of adding another"
                    )
            if (
                pinned
                and not already_pinned
                and await self._pinned_count(connection, name) >= MAX_PINNED_USER_SKILLS
            ):
                raise PinnedSkillLimit(
                    f"this workspace already has {MAX_PINNED_USER_SKILLS} pinned skills — unpin "
                    f"one before pinning another"
                )
            if held is None:
                await connection.execute(
                    sa.insert(user_skill).values(
                        workspace_id=scope.workspace_id,
                        name=name,
                        generation=uuid4(),
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                        **card,
                    )
                )
            else:
                await connection.execute(
                    sa.update(user_skill)
                    .values(generation=uuid4(), updated_at=sa.func.now(), **card)
                    .where(
                        user_skill.c.workspace_id == scope.workspace_id,
                        user_skill.c.name == name,
                        user_skill.c.generation == held,
                    )
                )
        return skill

    async def cards(self) -> tuple[SkillCard, ...]:
        """Every saved skill's routing card in the workspace — one projection of card columns,
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
                        user_skill.c.agents,
                        user_skill.c.pinned,
                    )
                    .where(user_skill.c.workspace_id == scope.workspace_id)
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
                    agents=tuple(json.loads(row.agents)),
                )
            )
        return tuple(cards)

    async def listing(self) -> tuple[SkillListing, ...]:
        """Every saved skill as the object listing renders it: name, description, pin. A row with
        the backfill's empty-description sentinel is skipped, exactly as `cards` skips it."""
        scope = agent_current()
        async with self.ctx.transaction() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        user_skill.c.name,
                        user_skill.c.description,
                        user_skill.c.pinned,
                    )
                    .where(user_skill.c.workspace_id == scope.workspace_id)
                    .order_by(user_skill.c.name)
                )
            ).all()
        return tuple(
            SkillListing(
                name=row.name,
                description=row.description,
                pinned=row.pinned,
            )
            for row in rows
            if row.description
        )

    async def record(self, name: str) -> SkillRecord | None:
        """One saved skill whole — files, description, generation, pin, timestamps — or None when
        the workspace holds no such name. A corrupt stored bundle raises, as every load by name
        does."""
        scope = agent_current()
        async with self.ctx.transaction() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        user_skill.c.content,
                        user_skill.c.description,
                        user_skill.c.generation,
                        user_skill.c.pinned,
                        user_skill.c.created_at,
                        user_skill.c.updated_at,
                    ).where(
                        user_skill.c.workspace_id == scope.workspace_id,
                        user_skill.c.name == name,
                    )
                )
            ).one_or_none()
        if row is None:
            return None
        stored = StoredSkill.model_validate_json(row.content)
        return SkillRecord(
            files={path: base64.b64decode(content) for path, content in stored.files.items()},
            description=row.description,
            generation=row.generation,
            pinned=row.pinned,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def materialize(self, name: str) -> RuntimeSkill | None:
        """One named skill parsed from its stored files, or None when the workspace has no such
        name. A corrupt stored bundle raises — a load of a named skill failing loud beats a silent
        miss; tolerance lives in the card projection."""
        files = await self.files(name)
        if files is None:
            return None
        return parse_skill_content(name, files)

    async def materialize_all(self) -> tuple[RuntimeSkill, ...]:
        """Every saved skill of the workspace parsed from stored files in one read, skipping
        corrupt rows with a log — the listing path's read-time tolerance, so one bad bundle never
        hides the rest. A load by name stays fail-loud through `materialize`."""
        scope = agent_current()
        async with self.ctx.transaction() as connection:
            rows = (
                await connection.execute(
                    sa.select(user_skill.c.name, user_skill.c.content)
                    .where(user_skill.c.workspace_id == scope.workspace_id)
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
                        "skill": row.name,
                        "error": str(error),
                    },
                )
        return tuple(skills)

    async def files(self, name: str) -> dict[str, bytes] | None:
        """Return one workspace skill's files, or None."""
        scope = agent_current()
        async with self.ctx.transaction() as connection:
            row = (
                await connection.execute(
                    sa.select(user_skill.c.content).where(
                        user_skill.c.workspace_id == scope.workspace_id,
                        user_skill.c.name == name,
                    )
                )
            ).one_or_none()
        if row is None:
            return None
        stored = StoredSkill.model_validate_json(row.content)
        return {path: base64.b64decode(content) for path, content in stored.files.items()}

    async def delete(self, name: str) -> None:
        """Delete one skill: mark its row stale, prune its index scope, then drop the row — in
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
                        user_skill.c.name == name,
                    )
                )
            await self.ctx.index.delete(IndexScope(SKILL_OWNER_KIND, name))
        async with self.ctx.transaction() as connection:
            await connection.execute(
                sa.delete(user_skill).where(
                    user_skill.c.workspace_id == scope.workspace_id,
                    user_skill.c.name == name,
                )
            )

    async def _count(self, connection: AsyncConnection) -> int:
        scope = agent_current()
        return (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(user_skill)
                .where(user_skill.c.workspace_id == scope.workspace_id)
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
                    user_skill.c.pinned.is_(True),
                    user_skill.c.name != excluding,
                )
            )
        ).scalar_one()
