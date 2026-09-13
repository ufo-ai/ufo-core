"""Workspace-owned member-authored skills as objects and loadable runtime skills.

A skill belongs to the workspace, never to a member or to one agent: the saved set answers every
speaker in a turn and every signed-in member in the portal, and each agent states in its own
`use_workspace_skills` setting whether its turns load it."""

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
    MemberObject,
    ObjectDetail,
    ObjectKind,
    ObjectListQuery,
    ObjectPage,
    ObjectRow,
    object_page,
)
from ufo.sdk.sandbox import ContainmentError, contained_relative, workspace_path
from ufo.sdk.skills import SKILL_LINE_MAX_CHARS, RuntimeSkill, SkillCard, lexical_score, skill_root
from ufo.sdk.tools import ObjectBinding, TextContent, ToolContext, ToolDef, ToolResult
from ufo_ext_skill_create.store import (
    MAX_PINNED_USER_SKILLS,
    SKILL_OWNER_KIND,
    SKILL_SUBJECT,
    UserSkillStore,
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
    apply passes to keep a file unchanged. Content is read through `load_skill`, never
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
            "Always show this skill in the skill list; at most "
            f"{MAX_PINNED_USER_SKILLS} skills may be pinned."
        ),
    )


def _require_ext(ext: ExtensionContext | None) -> ExtensionContext:
    if ext is None:
        raise RuntimeError("the skill kind dispatched without its ExtensionContext")
    return ext


def _contained_keys(name: str, spec: UserSkillSpec) -> None:
    """Every file key names a file inside the skill's own load directory."""
    root = skill_root(name)
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
    """Skill object handlers over the ambient workspace's saved set."""

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
        skill belongs to the workspace, not to a member or to one agent: the whole saved set
        answers every speaker in a turn and every member here."""
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
        reads. File content stays out of both: the spec carries a sha256 and a size per file, never
        bytes."""
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
            ObjectRow(
                name=listed.name,
                summary=listed.description[:SUMMARY_MAX],
                fields={"pinned": listed.pinned},
            )
            for listed in await UserSkillStore(ext).listing()
        )

    async def _skill(self, ext: ExtensionContext, name: str) -> ObjectDetail[UserSkillSpec] | None:
        record = await UserSkillStore(ext).record(name)
        if record is None:
            return None
        return ObjectDetail(
            spec=UserSkillSpec(
                files={
                    path: FileRef(sha256=hashlib.sha256(content).hexdigest(), size=len(content))
                    for path, content in record.files.items()
                },
                pinned=record.pinned,
            ),
            created_at=record.created_at,
            updated_at=record.updated_at,
            generation=record.generation,
        )

    async def status(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> dict[str, JsonValue] | None:
        record = await UserSkillStore(_require_ext(ctx.ext)).record(name)
        if record is None:
            return None
        if expected_generation != record.generation:
            raise ValueError(f"skill {name!r} changed while reading")
        return {
            "description": record.description,
            "files": len(record.files),
            "bytes": sum(len(content) for content in record.files.values()),
            "pinned": record.pinned,
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
            name,
            resolved,
            frozenset(ctx.skills.by_name),
            pinned=spec.pinned,
            generation=expected_generation,
        )

    async def delete(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> None:
        ext = _require_ext(ctx.ext)
        record = await UserSkillStore(ext).record(name)
        if record is not None and expected_generation != record.generation:
            raise ValueError(f"skill {name!r} changed while deleting")
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
        "A member-authored skill of the workspace: SKILL.md plus bundled text files, loadable "
        "on later turns. Any member may create, update, or delete one."
    ),
    guidance=(
        "Apply a manifest to save a skill you authored in the workspace so it persists and can "
        "be loaded on later turns. `files` must contain a SKILL.md with YAML frontmatter — a "
        "name matching the object name and a description; a workspace file rides as "
        "{from: <path>} and is inlined on save, and any bundled files are saved with it. The "
        "skill is validated before saving and a bad SKILL.md is reported as an error. Skills "
        "belong to the workspace — one name is one skill, and every agent whose "
        "use_workspace_skills setting holds loads the set; frontmatter `metadata.agents` "
        "(a list of agent names) narrows one skill to those agents' turns. Get "
        "returns each file as {sha256, size}, never inline content — read a saved skill's "
        "content with load_skill, which loads the files; on re-apply, keep an unchanged file "
        "by passing its {sha256: <digest>} back. Carry the `generation` object_get returned as "
        "a top-level manifest key on every edit: a stale one is refused because another writer "
        "saved first — get the skill again and re-apply from the current state. Load the "
        "create-skill skill first for the authoring workflow. A saved skill cannot replace a "
        "built-in skill. A pinned skill always shows in the skill list; at most "
        f"{MAX_PINNED_USER_SKILLS} skills may be pinned. The kind's `skill_search` action "
        "searches every loadable skill by keyword — the deploy's built-in skills and the "
        "workspace's saved ones alike — where this listing shows only the saved rows."
    ),
    spec_model=UserSkillSpec,
    store=SkillObjects(),
    list_fields=frozenset({"pinned"}),
)

SKILL_SEARCH_ACTION = "skill_search"
SKILL_SEARCH_LIMIT = 8
SKILL_SEARCH_NO_MATCH = "No matches among {total} loadable skills."


class SkillSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(description="Keywords naming the task or capability to find a skill for.")
    limit: int = Field(
        default=SKILL_SEARCH_LIMIT,
        ge=1,
        le=SKILL_SEARCH_LIMIT,
        description="Maximum results.",
    )


async def skill_search(ctx: ToolContext, args: SkillSearchInput) -> ToolResult:
    """Rank every loadable skill's routing card — deploy and member alike — by the lexical scorer
    the member block uses, and return the matching `name: description` lines, never a body. Zero
    matches answers with the searchable total, so the caller knows the corpus was searched rather
    than empty."""
    cards = ctx.skills.all_cards()
    ranked = sorted(
        ((lexical_score(args.query, card), card) for card in cards),
        key=lambda scored: scored[0],
        reverse=True,
    )
    matched = [card for score, card in ranked if score > 0][: args.limit]
    if not matched:
        return ToolResult(
            content=(TextContent(text=SKILL_SEARCH_NO_MATCH.format(total=len(cards))),)
        )
    lines = "\n".join(f"{card.name}: {card.description}"[:SKILL_LINE_MAX_CHARS] for card in matched)
    return ToolResult(content=(TextContent(text=lines),))


SKILL_SEARCH_TOOL = ToolDef(
    name=SKILL_SEARCH_ACTION,
    description=(
        "Search every loadable skill by keyword and get back matching `name: description` "
        "lines to pass to load_skill. Use it when the task might have a skill the visible "
        "indexes do not show."
    ),
    input_model=SkillSearchInput,
    handler=skill_search,
    bound=ObjectBinding(kind=SKILL_KIND, binding="collection"),
    parallel_safe=True,
    binds_member_authority=False,
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
    row logs and the tick continues, so a single bad card never holds up the rest; a tick that
    settled none of its cards raises the last failure instead, because a fault under every card —
    a statement the schema refuses, an unset key, an index that answers nothing — leaves every
    saved skill of that workspace unfindable, and a warning inside a job that reports success is
    the one thing nobody reads."""
    index, embed = ctx.index, ctx.embed
    if index is None or embed is None:
        raise RuntimeError("skill_index requires the index and embed backends; none are wired")
    chunker = TextChunker()
    async with ctx.transaction() as connection:
        stale = (
            await connection.execute(
                sa.select(
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
    settled = 0
    failure: Exception | None = None
    for row in stale:
        try:
            await _index_card(ctx, index, embed, chunker, row)
        except Exception as error:
            failure = error
            logger.warning(
                "skill_create.skill_index_failed",
                extra={
                    "workspace_id": str(ctx.workspace_id),
                    "skill": row.name,
                    "error_class": type(error).__name__,
                },
                exc_info=True,
            )
        else:
            settled += 1
    if failure is not None and settled == 0:
        raise failure


async def _index_card(
    ctx: ExtensionContext,
    index: IndexBackend,
    embed: EmbedClient,
    chunker: TextChunker,
    row: sa.Row,
) -> None:
    await chunk_embed_upsert(
        index,
        embed,
        chunker,
        SKILL_OWNER_KIND,
        row.name,
        SKILL_SUBJECT,
        f"{row.name}: {row.description[:SKILL_INDEX_DESCRIPTION_MAX_CHARS]}",
        "",
    )
    async with ctx.transaction() as connection:
        settled = await connection.execute(
            sa.update(user_skill)
            .values(indexed_digest=row.digest)
            .where(
                user_skill.c.workspace_id == ctx.workspace_id,
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
                    user_skill.c.name == row.name,
                )
            )
        ).one_or_none()
    if survivor is None:
        await index.delete(IndexScope(SKILL_OWNER_KIND, row.name))


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
        tools=(SKILL_SEARCH_TOOL,),
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
