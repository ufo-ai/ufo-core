"""Agent-owned member-authored skills as objects and loadable runtime skills.

A skill belongs to the agent that saved it, never to a member: the saved set answers every speaker
in a turn and every signed-in member in the portal, each behind the agent the caller bound."""

import base64
import hashlib
import json
import shlex
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ufo.sdk.context import ExtensionContext
from ufo.sdk.manifest import Manifest, SkillSpec
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
from ufo.sdk.sandbox import workspace_path
from ufo.sdk.skills import RuntimeSkill, parse_skill_content
from ufo.sdk.tools import ToolContext
from ufo_ext_skill_create.store import UserSkillStore

NAME = "skill_create"
VERSION = "0.1.0"
SKILL_KIND = "skill"
SKILL_DIR = Path(__file__).parent / "skills" / "create-skill"
SUMMARY_MAX = 120

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


def _require_ext(ext: ExtensionContext | None) -> ExtensionContext:
    if ext is None:
        raise RuntimeError("the skill kind dispatched without its ExtensionContext")
    return ext


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
        reads, on the agent the caller bound. The row decides: a skill the index skipped is
        not-found here rather than a parse raised from the detail. File content stays out of both:
        the spec carries a sha256 and a size per file, never bytes."""
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
            ObjectRow(name=skill.name, summary=skill.description[:SUMMARY_MAX])
            for skill in await UserSkillStore(ext).load_all()
        )

    async def _skill(self, ext: ExtensionContext, name: str) -> ObjectDetail[UserSkillSpec] | None:
        files = await UserSkillStore(ext).files(name)
        if files is None:
            return None
        timestamps = await UserSkillStore(ext).timestamps(name)
        if timestamps is None:
            return None
        created_at, updated_at = timestamps
        return ObjectDetail(
            spec=UserSkillSpec(
                files={
                    path: FileRef(sha256=hashlib.sha256(content).hexdigest(), size=len(content))
                    for path, content in files.items()
                }
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
        files = await UserSkillStore(_require_ext(ctx.ext)).files(name)
        if files is None:
            return None
        return {
            "description": parse_skill_content(name, files).description,
            "files": len(files),
            "bytes": sum(len(content) for content in files.values()),
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
        resolved = await self._resolve(ctx, name, spec)
        total = sum(len(content) for content in resolved.values())
        if total > MAX_SKILL_TOTAL_BYTES:
            raise ValueError(f"skill exceeds {MAX_SKILL_TOTAL_BYTES} bytes")
        await UserSkillStore(ext).save(name, resolved, frozenset(ctx.skills.by_name))

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
        "authoring workflow. A saved skill cannot replace a built-in skill."
    ),
    spec_model=UserSkillSpec,
    store=SkillObjects(),
)


async def _runtime_skills(ctx: ExtensionContext) -> tuple[RuntimeSkill, ...]:
    """The bound agent's saved skills for its turn registry."""
    return await UserSkillStore(ctx).load_all()


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        objects=(SKILL_OBJECT,),
        skills=(SkillSpec(path=SKILL_DIR),),
        runtime_skills=_runtime_skills,
    )
