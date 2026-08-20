"""Agent-owned member-authored skills as objects and loadable runtime skills.

A skill belongs to the agent that saved it, never to a member: the saved set answers every speaker
in a turn and every signed-in member in the portal, each behind the agent the caller bound."""

import base64
import hashlib
import json
import logging
import shlex
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ufo.sdk.context import ExtensionContext
from ufo.sdk.index import EmbedClient, IndexBackend, IndexScope, TextChunker, chunk_embed_upsert
from ufo.sdk.jobs import JobSpec, owner_candidates
from ufo.sdk.manifest import Manifest, MemberSkillsSpec, SkillSpec
from ufo.sdk.objects import (
    AGENT_KIND,
    MemberObject,
    ObjectDetail,
    ObjectKind,
    ObjectLink,
    ObjectListQuery,
    ObjectPage,
    ObjectRef,
    ObjectRow,
    object_page,
)
from ufo.sdk.sandbox import ContainmentError, contained_relative, workspace_path
from ufo.sdk.skills import RuntimeSkill, SkillCard, skill_mount_root
from ufo.sdk.tools import ToolContext
from ufo_ext_skill_create.store import (
    MAX_PINNED_USER_SKILLS,
    SKILL_OWNER_KIND,
    UserSkillStore,
    agent_subject,
    skill_owner_id,
    user_skill,
)

NAME = "skill_create"
VERSION = "0.1.0"
SKILL_KIND = "skill"
SKILL_DIR = Path(__file__).parent / "skills" / "create-skill"
SUMMARY_MAX = 120
SKILL_INDEX_JOB = "skill_index"
SKILL_INDEX_SCHEDULE = "0 * * * * *"
SKILL_INDEX_DESCRIPTION_MAX_CHARS = 2_000

logger = logging.getLogger(__name__)

MAX_SKILL_FILES = 50
MAX_SKILL_TOTAL_BYTES = 1_048_576

FILES_READ_PROG = """
import base64, json, os, stat, sys
paths, max_bytes = json.loads(sys.argv[1]), int(sys.argv[2])
contents = []
total = 0
for index, path in enumerate(paths):
    if not os.path.exists(path):
        print(json.dumps({"error": "no such file", "index": index}))
        sys.exit(1)
    if not stat.S_ISREG(os.lstat(path).st_mode):
        print(json.dumps({"error": "not a regular file", "index": index}))
        sys.exit(1)
    total += os.path.getsize(path)
    if total > max_bytes:
        print(json.dumps({"error": "files exceed %d bytes" % max_bytes}))
        sys.exit(1)
    with open(path, "rb") as handle:
        contents.append(base64.b64encode(handle.read()).decode())
print(json.dumps({"contents": contents}))
"""


class FileFrom(BaseModel):
    """A spec file value naming its workspace source: resolved to inline text at apply, so the
    persisted spec never references the disposable sandbox."""

    model_config = ConfigDict(extra="forbid")
    from_: str = Field(
        alias="from",
        description="Workspace-relative path to read this file's content from at apply.",
    )


class FileRef(BaseModel):
    """A stored file by content digest — what get returns instead of inline bodies, and what an
    apply passes to keep a file unchanged. Content is read through `load_skill`'s mount, never
    echoed into context."""

    model_config = ConfigDict(extra="forbid")
    sha256: str = Field(description="Hex digest of the stored file this value keeps unchanged.")
    size: int | None = Field(default=None, description="Stored size in bytes; ignored on apply.")


class UserSkillSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    files: dict[str, str | FileFrom | FileRef] = Field(
        description=(
            "The skill's files keyed by skill-relative path — SKILL.md (required; its "
            "frontmatter name must equal the object name) plus any bundled text files. A value "
            "is the content itself, {from: <workspace path>} to read it from the conversation "
            "workspace at apply, or {sha256: <digest>} to keep the stored file unchanged."
        )
    )
    pinned: bool = Field(
        default=False,
        description=(
            "Always show this skill in the agent's skill list; at most "
            f"{MAX_PINNED_USER_SKILLS} skills may be pinned."
        ),
    )


def _require_ext(ext: ExtensionContext | None) -> ExtensionContext:
    if ext is None:
        raise RuntimeError("the skill kind dispatched without its ExtensionContext")
    return ext


def _contained_keys(name: str, spec: UserSkillSpec) -> None:
    """Every file key names a file inside the skill's own mount directory. A saved skill is written
    into the workspace again by each later `load_skill`, so a key climbing out of `.skills/<name>/`
    is a durable write primitive over the agent's other files — refused here, where the key is
    persisted, as well as at the mount that would carry it out."""
    root = skill_mount_root(name)
    for path in spec.files:
        try:
            contained_relative(path, root)
        except ContainmentError as error:
            raise ValueError(f"skill file {path!r} is not a path inside the skill") from error


def _text(path: str, content: bytes) -> str:
    try:
        return content.decode()
    except UnicodeDecodeError as error:
        raise ValueError(
            f"skill file {path!r} is not text — a skill bundles only UTF-8 files"
        ) from error


@dataclass(frozen=True)
class SkillObjects:
    """Skill object handlers scoped by the ambient turn agent."""

    async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage:
        return object_page(await self._rows(_require_ext(ctx.ext)), query)

    async def member_page(
        self,
        ext: ExtensionContext | None,
        *,
        member_id: UUID,
        admin: bool,
        query: ObjectListQuery,
    ) -> ObjectPage:
        """The saved skills a signed-in member reads outside a turn — the rows `list` produces. A
        skill belongs to an agent, not to a member: the whole saved set answers every speaker in a
        turn and every member here, and the agent the caller bound is the only wall either read
        stands behind."""
        return object_page(await self._rows(_require_ext(ext)), query)

    async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[UserSkillSpec] | None:
        return await self._skill(_require_ext(ctx.ext), name)

    async def member_detail(
        self,
        ext: ExtensionContext | None,
        name: str,
        *,
        member_id: UUID,
        admin: bool,
    ) -> MemberObject[UserSkillSpec] | None:
        """One saved skill as the portal reads it — the row `list` renders beside the digests `get`
        reads, on the agent the caller bound. File content stays out of both: the spec carries a
        sha256 and a size per file, never bytes."""
        scoped = _require_ext(ext)
        row = next((row for row in await self._rows(scoped) if row.name == name), None)
        if row is None:
            return None
        detail = await self._skill(scoped, name)
        if detail is None:
            return None
        return MemberObject(row=row, detail=detail)

    async def _rows(self, ext: ExtensionContext) -> tuple[ObjectRow, ...]:
        return tuple(
            ObjectRow(name=card.name, summary=card.description[:SUMMARY_MAX])
            for card in await UserSkillStore(ext).cards()
        )

    async def _skill(self, ext: ExtensionContext, name: str) -> ObjectDetail[UserSkillSpec] | None:
        store = UserSkillStore(ext)
        card = next((card for card in await store.cards() if card.name == name), None)
        files = await store.files(name)
        if card is None or files is None:
            return None
        timestamps = await store.timestamps(name)
        if timestamps is None:
            return None
        created_at, updated_at = timestamps
        return ObjectDetail(
            spec=UserSkillSpec(
                files={
                    path: FileRef(sha256=hashlib.sha256(content).hexdigest(), size=len(content))
                    for path, content in files.items()
                },
                pinned=card.pinned,
            ),
            created_at=created_at,
            updated_at=updated_at,
            links=(
                ObjectLink(
                    relation="scoped_to",
                    target=ObjectRef(kind=AGENT_KIND, name=await ext.agent_name()),
                ),
            ),
        )

    async def status(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> dict[str, JsonValue] | None:
        store = UserSkillStore(_require_ext(ctx.ext))
        card = next((card for card in await store.cards() if card.name == name), None)
        files = await store.files(name)
        if card is None or files is None:
            return None
        return {
            "description": card.description,
            "files": len(files),
            "bytes": sum(len(content) for content in files.values()),
            "pinned": card.pinned,
        }

    async def apply(
        self,
        ctx: ToolContext,
        name: str,
        spec: UserSkillSpec,
        old: UserSkillSpec | None,
        *,
        expected_generation: UUID | None,
    ) -> None:
        ext = _require_ext(ctx.ext)
        if len(spec.files) > MAX_SKILL_FILES:
            raise ValueError(f"a skill holds at most {MAX_SKILL_FILES} files")
        _contained_keys(name, spec)
        resolved = await self._resolve(ctx, name, spec)
        total = sum(len(content) for content in resolved.values())
        if total > MAX_SKILL_TOTAL_BYTES:
            raise ValueError(f"skill exceeds {MAX_SKILL_TOTAL_BYTES} bytes")
        await UserSkillStore(ext).save(
            name, resolved, frozenset(ctx.skills.by_name), pinned=spec.pinned
        )

    async def delete(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> None:
        ext = _require_ext(ctx.ext)
        await UserSkillStore(ext).delete(name)

    async def _resolve(self, ctx: ToolContext, name: str, spec: UserSkillSpec) -> dict[str, bytes]:
        stored = await UserSkillStore(_require_ext(ctx.ext)).files(name) or {}
        kept: dict[str, bytes] = {}
        for path, value in spec.files.items():
            if not isinstance(value, FileRef):
                continue
            content = stored.get(path)
            if content is None or hashlib.sha256(content).hexdigest() != value.sha256:
                raise ValueError(
                    f"skill file {path!r} has no stored content with digest {value.sha256!r} — "
                    f"pass the content itself or a {{from: <workspace path>}} reference"
                )
            kept[path] = content
        sources = {
            path: workspace_path(value.from_)
            for path, value in spec.files.items()
            if isinstance(value, FileFrom)
        }
        ordered = sorted(set(sources.values()))
        by_source: dict[str, bytes] = {}
        if ordered:
            result = await ctx.sandbox.bash(
                f"python3 -c {shlex.quote(FILES_READ_PROG)} "
                f"{shlex.quote(json.dumps(ordered))} {MAX_SKILL_TOTAL_BYTES}"
            )
            parsed = json.loads(result.stdout) if result.stdout.strip() else {}
            error = parsed.get("error") if isinstance(parsed, dict) else None
            if result.exit_code != 0 or isinstance(error, str):
                if not isinstance(error, str):
                    raise ValueError(result.stderr.strip() or "could not read the referenced files")
                index = parsed.get("index")
                named = f"{error}: {ordered[index]}" if isinstance(index, int) else error
                raise ValueError(named)
            by_source = {
                source: base64.b64decode(content)
                for source, content in zip(ordered, parsed["contents"], strict=True)
            }
        resolved: dict[str, bytes] = {}
        for path, value in spec.files.items():
            match value:
                case FileRef():
                    content = kept[path]
                case FileFrom():
                    content = by_source[sources[path]]
                case str():
                    content = value.encode()
            _text(path, content)
            resolved[path] = content
        return resolved


SKILL_OBJECT = ObjectKind(
    name=SKILL_KIND,
    description=(
        "A member-authored skill: SKILL.md plus bundled text files, mounted into the loadable "
        "skill set on later turns. Any member may create, update, or delete; a skill can never "
        "shadow a built-in one."
    ),
    guidance=(
        "Apply a manifest to save a skill you authored in the workspace so it persists and can "
        "be loaded on later turns. `files` must contain a SKILL.md with YAML frontmatter — a "
        "name matching the object name and a description; a workspace file rides as "
        "{from: <path>} and is inlined on save, and any bundled files are saved with it. The "
        "skill is validated before saving and a bad SKILL.md is reported as an error. Skills "
        "belong to this agent — the `scoped_to` link names it; another agent may use the same "
        "name for its own skill. Get "
        "returns each file as {sha256, size}, never inline content — read a saved skill's "
        "content with load_skill, which mounts the files; on re-apply, keep an unchanged file "
        "by passing its {sha256: <digest>} back. Load the create-skill skill first for the "
        "authoring workflow. A saved skill cannot replace a built-in skill. A pinned skill "
        f"always shows in the agent's skill list; at most {MAX_PINNED_USER_SKILLS} skills "
        "may be pinned."
    ),
    spec_model=UserSkillSpec,
    store=SkillObjects(),
)


async def _member_cards(ctx: ExtensionContext) -> tuple[SkillCard, ...]:
    return await UserSkillStore(ctx).cards()


async def _materialize_skill(ctx: ExtensionContext, name: str) -> RuntimeSkill | None:
    return await UserSkillStore(ctx).materialize(name)


async def _materialize_all_skills(ctx: ExtensionContext) -> tuple[RuntimeSkill, ...]:
    return await UserSkillStore(ctx).materialize_all()


async def index_skills(ctx: ExtensionContext) -> None:
    """Chunk and embed each stale routing card of the bound workspace, then settle its
    `indexed_digest` — guarded on the digest the card was read at. A guard miss is re-read: a row
    deleted mid-embed gets its scope pruned (undoing the upsert), one re-saved mid-embed stays
    stale for the next tick — either way unindexed content is never marked settled. One failing
    row logs and the tick continues."""
    index, embed = ctx.index, ctx.embed
    if index is None or embed is None:
        raise RuntimeError("skill_index requires the index and embed backends; none are wired")
    chunker = TextChunker()
    async with ctx.transaction() as connection:
        stale = (
            await connection.execute(
                sa.select(
                    user_skill.c.agent_id,
                    user_skill.c.name,
                    user_skill.c.description,
                    user_skill.c.digest,
                ).where(
                    user_skill.c.workspace_id == ctx.workspace_id,
                    sa.or_(
                        user_skill.c.indexed_digest.is_(None),
                        user_skill.c.indexed_digest != user_skill.c.digest,
                    ),
                )
            )
        ).all()
    for row in stale:
        try:
            await _index_card(ctx, index, embed, chunker, row)
        except Exception:
            logger.warning(
                "skill_create.skill_index_failed",
                extra={
                    "workspace_id": str(ctx.workspace_id),
                    "agent_id": str(row.agent_id),
                    "skill": row.name,
                },
                exc_info=True,
            )


async def _index_card(
    ctx: ExtensionContext,
    index: IndexBackend,
    embed: EmbedClient,
    chunker: TextChunker,
    row: sa.Row,
) -> None:
    owner_id = skill_owner_id(row.agent_id, row.name)
    await chunk_embed_upsert(
        index,
        embed,
        chunker,
        SKILL_OWNER_KIND,
        owner_id,
        agent_subject(row.agent_id),
        f"{row.name}: {row.description[:SKILL_INDEX_DESCRIPTION_MAX_CHARS]}",
    )
    async with ctx.transaction() as connection:
        settled = await connection.execute(
            sa.update(user_skill)
            .values(indexed_digest=row.digest)
            .where(
                user_skill.c.workspace_id == ctx.workspace_id,
                user_skill.c.agent_id == row.agent_id,
                user_skill.c.name == row.name,
                user_skill.c.digest == row.digest,
            )
        )
        if settled.rowcount > 0:
            return
        survivor = (
            await connection.execute(
                sa.select(user_skill.c.digest).where(
                    user_skill.c.workspace_id == ctx.workspace_id,
                    user_skill.c.agent_id == row.agent_id,
                    user_skill.c.name == row.name,
                )
            )
        ).one_or_none()
    if survivor is None:
        await index.delete(IndexScope(SKILL_OWNER_KIND, owner_id))


def _skills_awaiting_index() -> sa.Select[tuple[UUID]]:
    return (
        sa.select(user_skill.c.workspace_id)
        .where(
            sa.or_(
                user_skill.c.indexed_digest.is_(None),
                user_skill.c.indexed_digest != user_skill.c.digest,
            )
        )
        .distinct()
    )


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        objects=(SKILL_OBJECT,),
        skills=(SkillSpec(path=SKILL_DIR),),
        member_skills=MemberSkillsSpec(
            cards=_member_cards,
            materialize=_materialize_skill,
            materialize_all=_materialize_all_skills,
        ),
        jobs=(
            JobSpec(
                name=SKILL_INDEX_JOB,
                schedule=SKILL_INDEX_SCHEDULE,
                handler=index_skills,
                candidates=owner_candidates(_skills_awaiting_index),
            ),
        ),
    )
