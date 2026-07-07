"""The skill-authoring extension's declared points: the `save_custom_skill` tool, the create-skill
teaching skill, and the `runtime_skills` provider.

`save_custom_skill` reads a skill directory the agent authored in the workspace, validates it, and
persists it workspace-scoped through the extension's own `user_skill` table; the create-skill skill
walks that authoring workflow. The `runtime_skills` provider is the per-turn seam core calls to
merge this workspace's saved skills back into the turn's `SkillRegistry`, so a saved skill is
resolvable by `load_skill`, listed by `list_skills`, and indexed in the prompt on later turns —
scoped to this workspace alone and never able to shadow a core or pack skill."""

import base64
import json
import shlex
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, Field

from ufo.sdk.context import ExtensionContext
from ufo.sdk.manifest import Manifest, SkillSpec
from ufo.sdk.sandbox import workspace_path
from ufo.sdk.skills import RuntimeSkill
from ufo.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult
from ufo_ext_skill_create.store import UserSkillStore

NAME = "skill_create"
VERSION = "0.1.0"
SKILL_DIR = Path(__file__).parent / "skills" / "create-skill"

MAX_SKILL_FILES = 50
MAX_SKILL_TOTAL_BYTES = 1_048_576

SKILL_READ_PROG = """
import base64, json, os, stat, sys
root, max_files, max_bytes = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
if not os.path.isdir(root):
    print(json.dumps({"error": "not a directory: " + root}))
    sys.exit(1)
files = {}
total = 0
for dirpath, dirnames, filenames in os.walk(root):
    dirnames.sort()
    for filename in sorted(filenames):
        full = os.path.join(dirpath, filename)
        if not stat.S_ISREG(os.lstat(full).st_mode):
            continue
        total += os.path.getsize(full)
        if len(files) >= max_files or total > max_bytes:
            capped = "skill exceeds %d files or %d bytes" % (max_files, max_bytes)
            print(json.dumps({"error": capped}))
            sys.exit(1)
        with open(full, "rb") as handle:
            files[os.path.relpath(full, root)] = base64.b64encode(handle.read()).decode()
print(json.dumps({"files": files}))
"""


class SaveCustomSkillInput(BaseModel):
    path: str = Field(
        description="Path to the skill directory in the workspace; it must contain a SKILL.md."
    )
    name: str | None = Field(
        default=None, description="Skill name; defaults to the directory name."
    )


async def save_custom_skill_handler(ctx: ToolContext, args: SaveCustomSkillInput) -> ToolResult:
    """Read the authored skill directory out of the sandbox, validate it with the skill parser (a
    bad or missing SKILL.md fails loud as a recoverable tool error), and persist it for this
    workspace so it survives across turns and sandboxes. The saved skill joins this workspace's
    loadable set on later turns — resolvable by `load_skill`, listed by `list_skills`, indexed in
    the prompt — but is scoped to this workspace alone and may not shadow a core or pack skill."""
    if ctx.ext is None:
        raise RuntimeError("save_custom_skill dispatched without its ExtensionContext")
    scoped = workspace_path(args.path)
    result = await ctx.sandbox.bash(
        f"python3 -c {shlex.quote(SKILL_READ_PROG)} {shlex.quote(scoped)} "
        f"{MAX_SKILL_FILES} {MAX_SKILL_TOTAL_BYTES}"
    )
    parsed = json.loads(result.stdout) if result.stdout.strip() else {}
    error = parsed.get("error") if isinstance(parsed, dict) else None
    if result.exit_code != 0 or isinstance(error, str):
        raise ValueError(
            error
            if isinstance(error, str)
            else (result.stderr.strip() or "could not read the skill directory")
        )
    raw_files = parsed.get("files") if isinstance(parsed, dict) else None
    if not isinstance(raw_files, dict):
        raise RuntimeError("skill reader returned no files")
    files = {path: base64.b64decode(content) for path, content in raw_files.items()}
    name = args.name or PurePosixPath(scoped).name
    skill = await UserSkillStore(ctx.ext).save(
        ctx.turn.workspace_id, name, files, frozenset(ctx.skills.by_name)
    )
    return ToolResult(
        content=(
            TextContent(
                text=json.dumps(
                    {
                        "skill": skill.name,
                        "description": skill.description,
                        "files": len(skill.files) + 1,
                    }
                )
            ),
        )
    )


async def _runtime_skills(ctx: ExtensionContext) -> tuple[RuntimeSkill, ...]:
    """This workspace's saved user-skills, parsed for the turn's registry merge — the per-turn seam
    core folds into the loadable set beside core's and the active packs' own."""
    return await UserSkillStore(ctx).load_all(ctx.store.workspace_id)


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        tools=(
            ToolDef(
                name="save_custom_skill",
                description=(
                    "Save a skill you authored in the workspace so it persists and can be loaded "
                    "on later turns. Point `path` at the skill directory (it must contain a "
                    "SKILL.md with YAML frontmatter — a name matching the directory and a "
                    "description); any bundled files are saved with it. The skill is validated "
                    "before saving and a bad SKILL.md is reported as an error. Load the "
                    "create-skill skill first for the authoring workflow. The saved skill is "
                    "scoped to this workspace and cannot replace a built-in skill."
                ),
                input_model=SaveCustomSkillInput,
                handler=save_custom_skill_handler,
            ),
        ),
        skills=(SkillSpec(path=SKILL_DIR),),
        runtime_skills=_runtime_skills,
    )
