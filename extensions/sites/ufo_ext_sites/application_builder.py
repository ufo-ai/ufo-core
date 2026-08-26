"""The typed application-page builder profile and its scoped source tools."""

import asyncio
import json
import re
import textwrap
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from math import isfinite
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal
from uuid import UUID
from xml.etree import ElementTree

from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field, field_validator, model_validator

from ufo.sdk.manifest import (
    Deny,
    HookContext,
    PreToolUse,
    SubagentProfile,
)
from ufo.sdk.sandbox import WORKSPACE_DIR, ContainmentError, contained_relative
from ufo.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult
from ufo_ext_sites.application_audit import (
    APPLICATION_AUDIT_ATTEMPT_KEY,
    APPLICATION_AUDIT_REQUEST_CONTRACT_KEY,
    APPLICATION_AUDIT_TURN_CONTRACT_KEY,
    ApplicationQaProof,
)
from ufo_ext_sites.source import (
    PROJECT_CONFIG,
    PROJECT_CONFIG_BYTES,
    unpack_page_kit,
)
from ufo_ext_sites.store import HostedSites, SourceManifest
from ufo_ext_sites.surface import site_url

APPLICATION_BUILDER_NAME = "ufo_application_builder"
APPLICATION_BUILDER_SKILL: Literal["ufo-style"] = "ufo-style"
APPLICATION_BUILDER_MODEL = "google/gemini-3.7-flash"
APPLICATION_BUILDER_REASONING: Literal["medium"] = "medium"
APPLICATION_BUILDER_MAX_ROUNDS = 35
APPLICATION_BUILDER_DELEGATION_TOOL = "build_ufo_application"
APPLICATION_PREVIEW_TOOL = "render_application_preview"
APPLICATION_BUILDER_DESIGN_TOOL = "write_application_design"
APPLICATION_BUILDER_EDIT_TOOL = "edit_application_source"
APPLICATION_BUILDER_READ_TOOL = "read_application_source"
APPLICATION_BUILDER_WRITE_TOOL = "write_application_source"
APPLICATION_BUILDER_QA_TOOL = "qa_ufo_application"
APPLICATION_BUILDER_DEPLOY_TOOL = "deploy_ufo_application"
APPLICATION_BUILDER_QA_CALL_KEY = "application-builder/qa-call/{turn_id}"
APPLICATION_BUILDER_QA_MAX_CALLS = 3
APPLICATION_BUILDER_QA_PROOF_KEY = "application-builder/qa-proof/{turn_id}"
APPLICATION_BUILDER_REDEPLOY_KEY = "application-builder/redeploy/{turn_id}"
APPLICATION_BUILDER_REPAIR_READ_LIMIT = 3
APPLICATION_BUILDER_REPAIR_READ_KEY = "application-builder/repair-read/{turn_id}"
APPLICATION_BUILDER_REPAIR_READ_REASON = (
    "Three repair source reads are complete. Edit the source, run product QA, and deploy again."
)
APPLICATION_BUILDER_DEPLOY_GUARD_REASON = "Run and pass product QA before deployment."
APPLICATION_SCAFFOLD_PATH = "/workspace/ufo-app"
APPLICATION_SOURCE_PATH = f"{APPLICATION_SCAFFOLD_PATH}/app.tsx"
APPLICATION_PREVIEW_FILENAME: Literal["application-preview.png"] = "application-preview.png"
APPLICATION_PREVIEW_WIDTH = 1280
APPLICATION_PREVIEW_HEIGHT = 800
APPLICATION_PREVIEW_BACKGROUND = "#FAF9F7"
APPLICATION_PREVIEW_FIELD = "#F4F3F2"
APPLICATION_PREVIEW_BORDER = "#EBEAE9"
APPLICATION_PREVIEW_INK = "#191A1A"
APPLICATION_PREVIEW_SOFT_INK = "#616161"
APPLICATION_PREVIEW_ACCENT = "#0095FF"
APPLICATION_PREVIEW_MARGIN = 32
APPLICATION_PREVIEW_RADIUS = 4
APPLICATION_PREVIEW_HEADER_HEIGHT = 72
APPLICATION_PREVIEW_REGION_GAP = 16
APPLICATION_BUILDER_PROMPT = (
    Path(__file__).parent / "prompts" / "subagent_ufo_application_builder.md"
).read_text()
APPLICATION_BUILD_TIMEOUT_SECONDS = 600
APPLICATION_BUILD_ERROR_MAX_CHARS = 2_000
APPLICATION_DESIGN_MAX_CHARS = 128_000
APPLICATION_SOURCE_MAX_CHARS = 256_000
APPLICATION_SOURCE_EXCERPT_MAX_CHARS = 5_000
SVG_DRAWING_ELEMENTS = frozenset(
    {"circle", "ellipse", "image", "line", "path", "polygon", "polyline", "rect", "text", "use"}
)
APPLICATION_INDEX = b"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Application</title>
<link rel="stylesheet" href="./sdk/kit.css">
</head>
<body>
<div id="root"></div>
<script type="module" src="./app.tsx"></script>
</body>
</html>
"""
APPLICATION_PREVIEW_SCAFFOLD = b"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Application preview</title>
<style>html,body,iframe{width:100%;height:100%;margin:0;border:0}body{overflow:hidden}</style>
</head>
<body>
<iframe name="ufo-app" title="Application preview" src="./dist/index.html"></iframe>
<script>
const agent={id:"preview-agent",name:"Assistant",model:"preview",main:true,icon:"user"};
const actionKey="ufo-application-preview-actions";
window.addEventListener("message",event=>{
  const message=event.data;
  if(!message||typeof message!=="object")return;
  if(message.ufo==="ready"){
    event.source.postMessage({ufo:"init",member:{email:"preview@localhost",admin:true},agents:[agent],agentId:agent.id,place:{},portal:location.origin},"*");
    return;
  }
  if(message.ufo!=="call")return;
  const path=String(message.path||"").replace(/^[/]+/,"");
  const route=path.split("?",1)[0];
  const request=typeof message.body==="string"?JSON.parse(message.body):message.body;
  const actions=JSON.parse(localStorage.getItem(actionKey)||"{}");
  let response={applied:true,message:"Prepared action accepted."};
  if(path==="api/agents"){
    response={agents:[agent],member:{email:"preview@localhost",admin:true}};
  }else if(message.method==="POST"&&path.startsWith("objects/")){
    const name=String(request.name||"");
    const result="Prepared action accepted.";
    actions[`${route}/${encodeURIComponent(name)}`]={spec:request.spec,result};
    localStorage.setItem(actionKey,JSON.stringify(actions));
    response={ok:true,name,detail:result};
  }else if(message.method==="GET"&&path.startsWith("objects/")){
    const parts=route.split("/");
    const stored=actions[route];
    const kind={kind:parts[1],fields:[],spec_schema:null,applies:false,deletes:false};
    if(stored){
      response={
        ...kind,
        name:decodeURIComponent(parts.slice(2).join("/")),
        summary:"",
        spec:stored.spec,
        status:{state:"applied",result:stored.result},
        links:[],
        created_at:null,
        updated_at:null
      };
    }else if(parts.length===2){
      response={...kind,objects:[],next_cursor:null};
    }else{
      response={...kind,name:decodeURIComponent(parts.slice(2).join("/")),summary:"",spec:null,status:{},links:[],created_at:null,updated_at:null};
    }
  }
  const body=JSON.stringify(response);
  event.source.postMessage({ufo:"data",id:message.id,ok:true,status:200,body},"*");
});
</script>
</body>
</html>
"""
APPLICATION_PLACEHOLDER = b"""import { mountApp } from "ufo/kit";
mountApp(document.getElementById("root")!, () => <main>Application source is not built.</main>);
"""
APPLICATION_SOURCE_READ = """from containment import ContainmentError, contained_file
import sys

try:
    with contained_file(sys.argv[1], sys.argv[2]) as target:
        sys.stdout.write(target.read_text(int(sys.argv[3]) if len(sys.argv) > 3 else 1000000))
except (ContainmentError, OSError) as error:
    raise SystemExit(str(error))"""
APPLICATION_SOURCE_CLAIM = """import os
from containment import ContainmentError, contained_file
import sys

try:
    with contained_file(sys.argv[1], sys.argv[2], create_parent=True) as target:
        descriptor = os.open(
            target.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=target.parent_fd,
        )
        os.close(descriptor)
except FileExistsError:
    raise SystemExit(17)
except (ContainmentError, OSError) as error:
    raise SystemExit(str(error))"""
APPLICATION_SOURCE_REQUIRE_CLAIM = """import stat
from containment import ContainmentError, contained_file
import sys

try:
    with contained_file(sys.argv[1], sys.argv[2]) as target:
        status = target.lstat()
        if status is None or not stat.S_ISREG(status.st_mode):
            raise SystemExit(17)
except ContainmentError as error:
    raise SystemExit(str(error))"""
IMPORT_DECLARATION = re.compile(r"(?m)^[ \t]*import\b")
EXPORT_DECLARATION = re.compile(r"(?m)^[ \t]*export\b")
IMPORT_MODULE = re.compile(r"\bfrom\s*['\"]([^'\"]+)['\"]|\bimport\s*\(\s*['\"]([^'\"]+)['\"]")
SIDE_EFFECT_IMPORT = re.compile(r"(?m)^[ \t]*import\s*['\"]")
ROOT_MOUNT = re.compile(
    r"\bmountApp\s*\(\s*document\.getElementById\(\s*['\"]root['\"]\s*\)\s*!?\s*,"
)
SOURCE_EDIT_PATCH = re.compile(
    r"\A<<<<<<< SEARCH\n(?P<old>.*?)\n=======\n(?P<new>.*?)\n>>>>>>>(?: REPLACE)?\n?\Z",
    re.DOTALL,
)
WORKSPACE_ROOT = PurePosixPath("/workspace")
BoundedSourceTerm = Annotated[str, Field(min_length=1, max_length=200)]
BoundedOldSource = Annotated[str, Field(min_length=1, max_length=20_000)]
BoundedNewSource = Annotated[str, Field(max_length=128_000)]
ApplicationRegion = Annotated[str, Field(min_length=1, max_length=80)]
ApplicationLayout = Literal["summary-detail", "queue-detail", "timeline", "metrics"]


class ApplicationBuilderTask(BaseModel):
    """One member request for a fixed-scaffold application."""

    objective: str = Field(min_length=1, max_length=20_000)
    scaffold_path: str
    source_path: str
    preload_skills: tuple[Literal["ufo-style"]] = (APPLICATION_BUILDER_SKILL,)

    @model_validator(mode="after")
    def source_is_the_scaffolds_app_tsx(self) -> "ApplicationBuilderTask":
        try:
            scaffold = PurePosixPath(contained_relative(self.scaffold_path, str(WORKSPACE_ROOT)))
            source = PurePosixPath(contained_relative(self.source_path, str(WORKSPACE_ROOT)))
        except ContainmentError as error:
            raise ValueError(
                "source_path must be app.tsx directly inside scaffold_path under /workspace"
            ) from error
        if source != scaffold / "app.tsx":
            raise ValueError(
                "source_path must be app.tsx directly inside scaffold_path under /workspace"
            )
        return self


class BuildUfoApplicationInput(BaseModel):
    """One fixed application delegation for the current member request."""


class RenderApplicationPreviewInput(BaseModel):
    """The complete small design contract for one application preview."""

    purpose: str = Field(min_length=1, max_length=240)
    first_screen_priority: str = Field(min_length=1, max_length=160)
    regions: tuple[ApplicationRegion, ...] = Field(min_length=2, max_length=6)
    layout: ApplicationLayout
    design_direction: str = Field(default="", max_length=200)


class ApplicationPreviewResult(BaseModel):
    """The image and contract digest returned to the creation conversation."""

    shared_filename: Literal["application-preview.png"] = APPLICATION_PREVIEW_FILENAME
    design_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ApplicationBuilderResult(BaseModel):
    """The deployment evidence returned to the parent after one complete application build."""

    status: Literal["deployed", "blocked"]
    source_path: str
    site_name: str = ""
    site_url: str = ""
    browser_batches: int = Field(ge=0, le=4)
    controls_checked: tuple[str, ...] = Field(default=(), max_length=100)
    observed_errors: tuple[str, ...] = Field(default=(), max_length=20)
    blocker: str = Field(default="", max_length=4_000)

    @model_validator(mode="after")
    def result_matches_status(self) -> "ApplicationBuilderResult":
        if self.status == "deployed" and (not self.site_name or not self.site_url):
            raise ValueError("a deployed application requires site_name and site_url")
        if self.status == "deployed" and self.blocker:
            raise ValueError("a deployed application cannot include a blocker")
        if self.status == "blocked" and not self.blocker:
            raise ValueError("a blocked application requires a blocker")
        if self.status == "blocked" and (self.site_name or self.site_url):
            raise ValueError("a blocked application cannot include site identity")
        return self


@dataclass(frozen=True)
class ApplicationBuildAcceptance:
    """Bind one worker deployment only after product-owned source and browser checks pass."""

    ctx: ToolContext
    child_turn_id: UUID

    async def accept(self, result: ApplicationBuilderResult) -> ApplicationBuilderResult:
        if result.status == "blocked":
            return result
        if self.ctx.ext is None:
            raise RuntimeError("the application builder dispatched without its extension context")
        if result.source_path != APPLICATION_SOURCE_PATH:
            return self._blocked(result, "The worker returned the wrong source path.", 0)
        key = APPLICATION_BUILDER_QA_PROOF_KEY.format(turn_id=self.child_turn_id)
        stored_proof = await self.ctx.ext.store.get(key)
        if stored_proof is None:
            return self._blocked(
                result,
                "The worker returned no passed product QA proof.",
                0,
            )
        try:
            proof = ApplicationQaProof.model_validate(stored_proof)
        except ValueError as error:
            raise RuntimeError("application builder QA proof is invalid") from error
        browser_batches = proof.browser_batches
        if result.observed_errors:
            return self._blocked(
                result,
                "The worker reported browser errors.",
                browser_batches,
            )
        sites = HostedSites(self.ctx.ext.store.workspace_id, self.ctx.ext.transaction)
        site = await sites.read(self.ctx.sandbox.conversation_id, result.site_name)
        if site is None:
            homepage = await sites.homepage(self.ctx.turn.agent_id)
            if homepage is not None and homepage.name == result.site_name:
                site = homepage
        if site is None or site.updated_at < self.ctx.turn.created_at:
            return self._blocked(
                result,
                "The worker returned no site deployed by this build.",
                browser_batches,
            )
        if site.creator_member_id != self.ctx.acting_member_id:
            return self._blocked(
                result,
                "The deployed site does not belong to the application owner.",
                browser_batches,
            )
        if site.source_manifest is None:
            return self._blocked(
                result,
                "The deployed site retained no source.",
                browser_batches,
            )
        manifest = SourceManifest.model_validate_json(site.source_manifest)
        deployed_source = manifest.files.get("src/app.tsx")
        if deployed_source is None:
            return self._blocked(
                result,
                "The deployed site retained no app.tsx.",
                browser_batches,
            )
        if deployed_source.sha256 != proof.source_sha256:
            return self._blocked(
                result,
                "The deployed app.tsx does not match the product QA proof.",
                browser_batches,
            )
        task = ApplicationBuilderTask(
            objective="Accept the staged application source.",
            scaffold_path=APPLICATION_SCAFFOLD_PATH,
            source_path=APPLICATION_SOURCE_PATH,
        )
        accepted_source = await self.ctx.sandbox.python(
            APPLICATION_SOURCE_READ,
            await _source_acceptance_path(self.ctx, task, self.child_turn_id),
            await _runtime_root(self.ctx),
        )
        if accepted_source.exit_code != 0:
            return self._blocked(
                result,
                "The worker produced no accepted app.tsx.",
                browser_batches,
            )
        if deployed_source.sha256 != accepted_source.stdout.strip():
            return self._blocked(
                result,
                "The deployed app.tsx does not match the accepted source.",
                browser_batches,
            )
        bound = await sites.set_homepage(self.ctx.turn.agent_id, site.conversation_id, site.name)
        if bound is None:
            raise RuntimeError("the accepted application site disappeared before binding")
        return result.model_copy(
            update={
                "site_name": bound.name,
                "site_url": site_url(
                    self.ctx.public_base_url,
                    self.ctx.ext.store.workspace_id,
                    bound.conversation_id,
                    bound.name,
                ),
                "browser_batches": browser_batches,
            }
        )

    def _blocked(
        self,
        result: ApplicationBuilderResult,
        reason: str,
        browser_batches: int,
    ) -> ApplicationBuilderResult:
        return ApplicationBuilderResult(
            status="blocked",
            source_path=APPLICATION_SOURCE_PATH,
            browser_batches=browser_batches,
            controls_checked=result.controls_checked,
            observed_errors=result.observed_errors,
            blocker=reason,
        )


class WriteApplicationSourceInput(BaseModel):
    """The complete `app.tsx` source for the current typed build task."""

    content: str = Field(
        min_length=1,
        max_length=APPLICATION_SOURCE_MAX_CHARS,
        description="Complete app.tsx source. Use named ufo/kit imports and no export "
        "declarations.",
    )


class WriteApplicationDesignInput(BaseModel):
    """One SVG visual contract for the application first screen."""

    content: str = Field(min_length=1, max_length=APPLICATION_DESIGN_MAX_CHARS)


class ReadApplicationSourceInput(BaseModel):
    """Select bounded excerpts from the admitted `app.tsx` source for one repair."""

    terms: tuple[BoundedSourceTerm, ...] = Field(default=(), max_length=20)


class ApplicationSourceEdit(BaseModel):
    """One exact source replacement for this repair."""

    old_text: BoundedOldSource
    new_text: BoundedNewSource


class EditApplicationSourceInput(BaseModel):
    """Exact replacements to apply to the current admitted `app.tsx`."""

    edits: tuple[ApplicationSourceEdit, ...] = Field(min_length=1, max_length=20)

    @field_validator("edits", mode="before")
    @classmethod
    def json_text_edits_are_objects(cls, value: object) -> object:
        if not isinstance(value, (list, tuple)):
            return value
        edits = []
        for item in value:
            if not isinstance(item, str):
                edits.append(item)
                continue
            if item.lstrip().startswith("{"):
                edits.append(json.loads(item))
                continue
            match = SOURCE_EDIT_PATCH.fullmatch(item)
            edits.append(
                {"old_text": match.group("old"), "new_text": match.group("new")}
                if match is not None
                else item
            )
        if edits and len(edits) % 2 == 0 and all(isinstance(item, str) for item in edits):
            return tuple(
                {"old_text": edits[index], "new_text": edits[index + 1]}
                for index in range(0, len(edits), 2)
            )
        return tuple(edits)


def _validate_application_source(source: str) -> None:
    modules = tuple(left or right for left, right in IMPORT_MODULE.findall(source))
    if IMPORT_DECLARATION.search(source) is None or "ufo/kit" not in modules:
        raise ValueError("app.tsx must import its runtime and components from ufo/kit")
    if SIDE_EFFECT_IMPORT.search(source) or any(module != "ufo/kit" for module in modules):
        raise ValueError("app.tsx may import only from ufo/kit")
    if EXPORT_DECLARATION.search(source):
        raise ValueError("app.tsx must not export declarations")
    if "UfoAppKit" in source:
        raise ValueError("app.tsx must import from ufo/kit instead of using UfoAppKit")
    if ROOT_MOUNT.search(source) is None:
        raise ValueError("mountApp must receive the root element and a render callback")


def _validate_application_design(source: str) -> None:
    if "<!DOCTYPE" in source.upper() or "<!ENTITY" in source.upper():
        raise ValueError("application design must not declare XML entities")
    try:
        root = ElementTree.fromstring(source)
    except ElementTree.ParseError as error:
        raise ValueError("application design must be valid SVG") from error
    if root.tag.rsplit("}", 1)[-1] != "svg":
        raise ValueError("application design root must be svg")
    try:
        view_box = tuple(
            float(value) for value in re.split(r"[ ,]+", root.attrib["viewBox"].strip())
        )
    except (KeyError, ValueError) as error:
        raise ValueError("application design svg requires a viewBox") from error
    if (
        len(view_box) != 4
        or not all(isfinite(value) for value in view_box)
        or view_box[2] <= 0
        or view_box[3] <= 0
    ):
        raise ValueError("application design svg requires a viewBox")
    drawing_elements = 0
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        attributes = {
            name.rsplit("}", 1)[-1]: value.strip() for name, value in element.attrib.items()
        }
        match tag:
            case "circle":
                visible = attributes.get("r", "") not in {"", "0", "0.0"}
            case "ellipse":
                visible = all(
                    attributes.get(name, "") not in {"", "0", "0.0"} for name in ("rx", "ry")
                )
            case "image" | "rect":
                visible = all(
                    attributes.get(name, "") not in {"", "0", "0.0"} for name in ("width", "height")
                )
            case "line":
                visible = (
                    attributes.get("x1", "") != attributes.get("x2", "")
                    or attributes.get("y1", "") != attributes.get("y2", "")
                ) and attributes.get("stroke", "").casefold() not in {"", "none", "transparent"}
            case "path":
                visible = bool(attributes.get("d"))
            case "polygon" | "polyline":
                visible = bool(attributes.get("points"))
            case "text":
                visible = bool("".join(element.itertext()).strip())
            case "use":
                visible = attributes.get("href", "").startswith("#")
            case _:
                visible = False
        if tag in SVG_DRAWING_ELEMENTS and visible:
            drawing_elements += 1
        if tag in {"script", "foreignObject"}:
            raise ValueError("application design must contain SVG drawing elements only")
        for name, value in element.attrib.items():
            attribute = name.rsplit("}", 1)[-1].casefold()
            lowered = value.casefold()
            if attribute.startswith("on") or any(
                scheme in lowered for scheme in ("javascript:", "data:", "http:", "https:")
            ):
                raise ValueError("application design must not contain active or external content")
    if drawing_elements == 0:
        raise ValueError("application design must contain SVG drawing elements only")


async def _build_application_project(
    ctx: ToolContext, project: str, runtime_root: str | None = None
) -> None:
    config = f"{project}/{PROJECT_CONFIG}"
    if runtime_root is None:
        await ctx.sandbox.write_file(config, PROJECT_CONFIG_BYTES)
    else:
        await ctx.sandbox.write_runtime_path(config, PROJECT_CONFIG_BYTES)
    await unpack_page_kit(ctx, project, runtime_root)
    result = await ctx.sandbox.sh(
        'cd "$1" && vite build', project, timeout_s=APPLICATION_BUILD_TIMEOUT_SECONDS
    )
    if result.exit_code != 0:
        error = (result.stderr or result.stdout or "compiler returned no error").strip()
        raise ValueError(f"app.tsx does not compile: {error[:APPLICATION_BUILD_ERROR_MAX_CHARS]}")


async def _compile_application_source(
    ctx: ToolContext, task: ApplicationBuilderTask, source: str
) -> None:
    check_relative = f"tool-output/application-builder/{ctx.turn.id}/project"
    check_root = await ctx.sandbox.runtime_path(check_relative)
    runtime_root = await _runtime_root(ctx)
    index = await ctx.sandbox.python(
        APPLICATION_SOURCE_READ, f"{task.scaffold_path}/index.html", WORKSPACE_DIR
    )
    if index.exit_code != 0:
        raise RuntimeError(index.stderr or "application index.html could not be read")
    await ctx.sandbox.write_runtime_file(f"{check_relative}/index.html", index.stdout.encode())
    await ctx.sandbox.write_runtime_file(f"{check_relative}/app.tsx", source.encode())
    await _build_application_project(ctx, check_root, runtime_root)


async def _source_claim_path(ctx: ToolContext, task: ApplicationBuilderTask, turn_id: UUID) -> str:
    return await ctx.sandbox.runtime_path(
        "tool-output/application-builder/"
        f"{sha256(task.source_path.encode()).hexdigest()}.{turn_id}.claimed"
    )


async def _runtime_root(ctx: ToolContext) -> str:
    return str(PurePosixPath(await ctx.sandbox.runtime_path("tool-output")).parent)


def _design_path(task: ApplicationBuilderTask) -> str:
    return f"{task.scaffold_path}/application-design.svg"


async def _design_claim_path(ctx: ToolContext, task: ApplicationBuilderTask, turn_id: UUID) -> str:
    return await ctx.sandbox.runtime_path(
        "tool-output/application-builder/"
        f"{sha256(_design_path(task).encode()).hexdigest()}.{turn_id}.claimed"
    )


async def _source_candidate_path(
    ctx: ToolContext, task: ApplicationBuilderTask, turn_id: UUID
) -> str:
    return await ctx.sandbox.runtime_path(
        "tool-output/application-builder/"
        f"{sha256(task.source_path.encode()).hexdigest()}.{turn_id}.candidate.tsx"
    )


async def _source_acceptance_path(
    ctx: ToolContext, task: ApplicationBuilderTask, turn_id: UUID
) -> str:
    return await ctx.sandbox.runtime_path(
        "tool-output/application-builder/"
        f"{sha256(task.source_path.encode()).hexdigest()}.{turn_id}.accepted"
    )


async def _require_application_source(ctx: ToolContext, task: ApplicationBuilderTask) -> None:
    claim = await ctx.sandbox.python(
        APPLICATION_SOURCE_REQUIRE_CLAIM,
        await _source_claim_path(ctx, task, ctx.turn.id),
        await _runtime_root(ctx),
    )
    if claim.exit_code == 17:
        raise ValueError("write_application_source must complete before source repair")
    if claim.exit_code != 0:
        raise RuntimeError(claim.stderr or "application source claim could not be read")


async def _require_application_design(ctx: ToolContext, task: ApplicationBuilderTask) -> None:
    claim = await ctx.sandbox.python(
        APPLICATION_SOURCE_REQUIRE_CLAIM,
        await _design_claim_path(ctx, task, ctx.turn.id),
        await _runtime_root(ctx),
    )
    if claim.exit_code == 17:
        raise ValueError("write_application_design must complete before write_application_source")
    if claim.exit_code != 0:
        raise RuntimeError(claim.stderr or "application design claim could not be read")
    result = await ctx.sandbox.python(APPLICATION_SOURCE_READ, _design_path(task), WORKSPACE_DIR)
    if result.exit_code != 0 or not result.stdout:
        raise ValueError("write_application_design must complete before write_application_source")
    _validate_application_design(result.stdout)


async def write_application_design(
    ctx: ToolContext, args: WriteApplicationDesignInput
) -> ToolResult:
    """Write one SVG visual contract before application source work starts."""

    task = ApplicationBuilderTask.model_validate_json(ctx.turn.inbound)
    _validate_application_design(args.content)
    design_path = _design_path(task)
    claim = await ctx.sandbox.python(
        APPLICATION_SOURCE_CLAIM,
        await _design_claim_path(ctx, task, ctx.turn.id),
        await _runtime_root(ctx),
    )
    if claim.exit_code == 17:
        raise ValueError("the application design is already fixed for this build")
    if claim.exit_code != 0:
        raise RuntimeError(claim.stderr or "application design ownership could not be claimed")
    content = args.content.encode()
    await ctx.sandbox.write_file(design_path, content)
    return ToolResult(
        content=(
            TextContent(
                text=json.dumps(
                    {
                        "path": design_path,
                        "design_digest": sha256(content).hexdigest(),
                        "size_bytes": len(content),
                    }
                )
            ),
        )
    )


async def read_application_source(ctx: ToolContext, args: ReadApplicationSourceInput) -> ToolResult:
    """Read bounded source excerpts after an exact edit mismatch."""

    task = ApplicationBuilderTask.model_validate_json(ctx.turn.inbound)
    await _require_application_source(ctx, task)
    result = await ctx.sandbox.python(
        APPLICATION_SOURCE_READ,
        await _source_candidate_path(ctx, task, ctx.turn.id),
        await _runtime_root(ctx),
    )
    if result.exit_code != 0:
        raise RuntimeError(result.stderr or "app.tsx could not be read")
    if len(result.stdout) > APPLICATION_SOURCE_MAX_CHARS:
        raise ValueError(f"app.tsx exceeds {APPLICATION_SOURCE_MAX_CHARS} characters")
    lines = result.stdout.splitlines()
    matches: list[tuple[str, tuple[int, ...]]] = []
    for term in args.terms:
        indexes = tuple(
            index for index, line in enumerate(lines) if term.casefold() in line.casefold()
        )
        matches.append((term, indexes))
    windows = [(0, min(12, len(lines))), (max(0, len(lines) - 8), len(lines))]
    for _, indexes in matches:
        if not indexes:
            continue
        positions = (0,) if len(indexes) == 1 else (0, len(indexes) // 2, len(indexes) - 1)
        for position in dict.fromkeys(positions):
            index = indexes[position]
            windows.append((max(0, index - 3), min(len(lines), index + 4)))
    excerpts = [f'matches for "{term}": {len(indexes)}' for term, indexes in matches]
    size = sum(len(line) + 1 for line in excerpts)
    covered: set[int] = set()
    for start, end in windows:
        uncovered = tuple(index for index in range(start, end) if index not in covered)
        if not uncovered:
            continue
        groups: list[list[int]] = []
        for index in uncovered:
            if not groups or index != groups[-1][-1] + 1:
                groups.append([index])
            else:
                groups[-1].append(index)
        for group in groups:
            first = group[0]
            last = group[-1] + 1
            block = [f"SOURCE LINES {first + 1}-{last}"]
            block.extend(f"{index + 1}\t{lines[index]}" for index in group)
            block.append("END SOURCE")
            rendered = "\n".join(block)
            if size + len(rendered) + 1 > APPLICATION_SOURCE_EXCERPT_MAX_CHARS:
                continue
            excerpts.append(rendered)
            size += len(rendered) + 1
            covered.update(group)
    return ToolResult(content=(TextContent(text="\n".join(excerpts)),))


async def edit_application_source(ctx: ToolContext, args: EditApplicationSourceInput) -> ToolResult:
    """Apply exact repairs only to the `app.tsx` path admitted in this child turn."""

    task = ApplicationBuilderTask.model_validate_json(ctx.turn.inbound)
    await _require_application_source(ctx, task)
    candidate_path = await _source_candidate_path(ctx, task, ctx.turn.id)
    result = await ctx.sandbox.python(
        APPLICATION_SOURCE_READ, candidate_path, await _runtime_root(ctx)
    )
    if result.exit_code != 0:
        raise RuntimeError(result.stderr or "app.tsx could not be read")
    source = result.stdout
    replacements: list[tuple[int, int, str]] = []
    for edit in args.edits:
        if source.count(edit.old_text) != 1:
            raise ValueError("old_text must occur exactly once in the current app.tsx")
        start = source.index(edit.old_text)
        end = start + len(edit.old_text)
        if any(
            start < other_end and other_start < end for other_start, other_end, _ in replacements
        ):
            raise ValueError("source edits must not overlap")
        replacements.append((start, end, edit.new_text))
    for start, end, new_text in sorted(replacements, reverse=True):
        source = source[:start] + new_text + source[end:]
    if len(source) > APPLICATION_SOURCE_MAX_CHARS:
        raise ValueError(f"app.tsx exceeds {APPLICATION_SOURCE_MAX_CHARS} characters")
    await ctx.sandbox.write_runtime_path(candidate_path, source.encode())
    _validate_application_source(source)
    await _compile_application_source(ctx, task, source)
    await ctx.sandbox.write_file(task.source_path, source.encode())
    await _build_application_project(ctx, task.scaffold_path)
    await ctx.sandbox.write_runtime_path(
        await _source_acceptance_path(ctx, task, ctx.turn.id),
        sha256(source.encode()).hexdigest().encode(),
    )
    return ToolResult(
        content=(
            TextContent(
                text=json.dumps(
                    {
                        "path": task.source_path,
                        "replacements": len(args.edits),
                        "size_bytes": len(source.encode()),
                    }
                )
            ),
        )
    )


async def write_application_source(
    ctx: ToolContext, args: WriteApplicationSourceInput
) -> ToolResult:
    """Write only the `app.tsx` path admitted in this child turn's typed input."""

    task = ApplicationBuilderTask.model_validate_json(ctx.turn.inbound)
    await _require_application_design(ctx, task)
    claim = await ctx.sandbox.python(
        APPLICATION_SOURCE_CLAIM,
        await _source_claim_path(ctx, task, ctx.turn.id),
        await _runtime_root(ctx),
    )
    if claim.exit_code == 17:
        raise ValueError(
            "an initial app.tsx candidate already exists; repair it with "
            "read_application_source and edit_application_source"
        )
    if claim.exit_code != 0:
        raise RuntimeError(claim.stderr or "app.tsx ownership could not be claimed")
    await ctx.sandbox.write_runtime_path(
        await _source_candidate_path(ctx, task, ctx.turn.id), args.content.encode()
    )
    try:
        _validate_application_source(args.content)
        await _compile_application_source(ctx, task, args.content)
    except ValueError as error:
        raise ValueError(
            f"{error} Candidate retained; use read_application_source and "
            "edit_application_source instead of another full write."
        ) from error
    await ctx.sandbox.write_file(task.source_path, args.content.encode())
    await _build_application_project(ctx, task.scaffold_path)
    await ctx.sandbox.write_runtime_path(
        await _source_acceptance_path(ctx, task, ctx.turn.id),
        sha256(args.content.encode()).hexdigest().encode(),
    )
    return ToolResult(
        content=(
            TextContent(
                text=json.dumps(
                    {"path": task.source_path, "size_bytes": len(args.content.encode())}
                )
            ),
        )
    )


def _preview_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def _preview_text(value: str, width: int, lines: int) -> str:
    parts = textwrap.wrap(value.strip(), width=width)[:lines]
    if not parts:
        return ""
    if len(parts) == lines and len(" ".join(parts)) < len(value.strip()):
        parts[-1] = parts[-1].rstrip(" .") + "…"
    return "\n".join(parts)


def _preview_card(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    title: str,
    detail: str,
) -> None:
    draw.rounded_rectangle(
        box,
        radius=APPLICATION_PREVIEW_RADIUS,
        fill=APPLICATION_PREVIEW_BACKGROUND,
        outline=APPLICATION_PREVIEW_BORDER,
        width=2,
    )
    left, top, right, bottom = box
    draw.text(
        (left + 20, top + 18),
        _preview_text(title, 34, 2),
        font=_preview_font(18),
        fill=APPLICATION_PREVIEW_INK,
    )
    line_y = min(top + 74, bottom - 32)
    draw.line(
        (left + 20, line_y, right - 20, line_y),
        fill=APPLICATION_PREVIEW_BORDER,
        width=2,
    )
    if bottom - line_y > 50:
        draw.text(
            (left + 20, line_y + 16),
            _preview_text(detail, 46, 3),
            font=_preview_font(15),
            fill=APPLICATION_PREVIEW_SOFT_INK,
            spacing=6,
        )


def _preview_boxes(layout: ApplicationLayout, count: int) -> tuple[tuple[int, int, int, int], ...]:
    left = APPLICATION_PREVIEW_MARGIN
    right = APPLICATION_PREVIEW_WIDTH - APPLICATION_PREVIEW_MARGIN
    top = 344
    bottom = APPLICATION_PREVIEW_HEIGHT - 58
    gap = APPLICATION_PREVIEW_REGION_GAP
    if layout == "timeline":
        height = (bottom - top - gap * (count - 1)) // count
        return tuple(
            (
                left,
                top + index * (height + gap),
                right,
                top + index * (height + gap) + height,
            )
            for index in range(count)
        )
    if layout == "queue-detail" and count > 1:
        split = left + round((right - left - gap) * 0.62)
        side_height = (bottom - top - gap * (count - 2)) // (count - 1)
        return (
            (left, top, split, bottom),
            *tuple(
                (
                    split + gap,
                    top + index * (side_height + gap),
                    right,
                    top + index * (side_height + gap) + side_height,
                )
                for index in range(count - 1)
            ),
        )
    columns = 3 if layout == "metrics" else 2
    rows = (count + columns - 1) // columns
    width = (right - left - gap * (columns - 1)) // columns
    height = (bottom - top - gap * (rows - 1)) // rows
    return tuple(
        (
            left + (index % columns) * (width + gap),
            top + (index // columns) * (height + gap),
            left + (index % columns) * (width + gap) + width,
            top + (index // columns) * (height + gap) + height,
        )
        for index in range(count)
    )


def _application_preview(args: RenderApplicationPreviewInput) -> bytes:
    image = Image.new(
        "RGB",
        (APPLICATION_PREVIEW_WIDTH, APPLICATION_PREVIEW_HEIGHT),
        APPLICATION_PREVIEW_BACKGROUND,
    )
    draw = ImageDraw.Draw(image)
    draw.line(
        (
            0,
            APPLICATION_PREVIEW_HEADER_HEIGHT,
            APPLICATION_PREVIEW_WIDTH,
            APPLICATION_PREVIEW_HEADER_HEIGHT,
        ),
        fill=APPLICATION_PREVIEW_BORDER,
        width=2,
    )
    draw.ellipse((32, 27, 42, 37), fill=APPLICATION_PREVIEW_ACCENT)
    draw.text(
        (54, 23),
        "Application preview",
        font=_preview_font(18),
        fill=APPLICATION_PREVIEW_INK,
    )
    draw.text(
        (1090, 24),
        args.layout.replace("-", " ").title(),
        font=_preview_font(15),
        fill=APPLICATION_PREVIEW_SOFT_INK,
    )
    draw.text(
        (32, 104),
        _preview_text(args.purpose, 70, 2),
        font=_preview_font(34),
        fill=APPLICATION_PREVIEW_INK,
        spacing=8,
    )
    draw.rounded_rectangle(
        (32, 214, 1248, 320),
        radius=APPLICATION_PREVIEW_RADIUS,
        fill=APPLICATION_PREVIEW_FIELD,
    )
    draw.text(
        (52, 234),
        "First on the page",
        font=_preview_font(14),
        fill=APPLICATION_PREVIEW_SOFT_INK,
    )
    draw.text(
        (52, 266),
        _preview_text(args.first_screen_priority, 72, 2),
        font=_preview_font(24),
        fill=APPLICATION_PREVIEW_INK,
    )
    for region, box in zip(
        args.regions, _preview_boxes(args.layout, len(args.regions)), strict=True
    ):
        _preview_card(draw, box, region, f"{region} content and controls appear here.")
    direction = args.design_direction.strip() or "House style"
    draw.text(
        (32, 770),
        _preview_text(direction, 110, 1),
        font=_preview_font(13),
        fill=APPLICATION_PREVIEW_SOFT_INK,
    )
    body = BytesIO()
    image.save(body, format="PNG")
    return body.getvalue()


class _PillowApplicationPreview:
    @staticmethod
    def render(args: RenderApplicationPreviewInput) -> bytes:
        return _application_preview(args)


async def render_application_preview(
    ctx: ToolContext, args: RenderApplicationPreviewInput
) -> ToolResult:
    """Render and share one stateless product-owned application preview."""
    body = await asyncio.to_thread(_PillowApplicationPreview.render, args)
    await ctx.share_artifact(APPLICATION_PREVIEW_FILENAME, body, "Application preview")
    contract = args.model_dump(mode="json")
    digest = sha256(
        json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    result = ApplicationPreviewResult(design_digest=digest)
    return ToolResult(content=(TextContent(text=result.model_dump_json()),))


async def _ensure_application_scaffold(ctx: ToolContext) -> None:
    for path, content in (
        (f"{APPLICATION_SCAFFOLD_PATH}/index.html", APPLICATION_INDEX),
        (APPLICATION_SOURCE_PATH, APPLICATION_PLACEHOLDER),
        (f"{APPLICATION_SCAFFOLD_PATH}/preview.html", APPLICATION_PREVIEW_SCAFFOLD),
    ):
        present = await ctx.sandbox.python(APPLICATION_SOURCE_READ, path, WORKSPACE_DIR, "1")
        if present.exit_code != 0:
            await ctx.sandbox.write_file(path, content)


async def build_ufo_application(ctx: ToolContext, _args: BuildUfoApplicationInput) -> ToolResult:
    """Run the fixed application worker once and return its structured result."""

    if ctx.ext is None:
        raise RuntimeError("the application builder dispatched without its extension context")
    if ctx.speaker_member_id is not None:
        await ctx.ext.store.put(
            APPLICATION_BUILDER_REDEPLOY_KEY.format(turn_id=ctx.turn.id),
            str(ctx.speaker_member_id),
        )
    request_contract = await ctx.ext.store.get(
        APPLICATION_AUDIT_REQUEST_CONTRACT_KEY.format(
            request_sha256=sha256(ctx.turn.inbound.encode()).hexdigest()
        )
    )
    if request_contract is not None:
        await ctx.ext.store.put(
            APPLICATION_AUDIT_TURN_CONTRACT_KEY.format(turn_id=ctx.turn.id),
            request_contract,
        )
    await _ensure_application_scaffold(ctx)
    objective = (
        f"Application instructions:\n{ctx.agent.prompt}\n\nCurrent request:\n{ctx.turn.inbound}"
    )
    try:
        result = await ctx.spawn(
            f"profile:{APPLICATION_BUILDER_NAME}",
            ApplicationBuilderTask(
                objective=objective,
                scaffold_path=APPLICATION_SCAFFOLD_PATH,
                source_path=APPLICATION_SOURCE_PATH,
            ).model_dump(),
            dedup_key=ctx.idempotency_key,
        )
    except Exception as error:
        accepted = ApplicationBuilderResult(
            status="blocked",
            source_path=APPLICATION_SOURCE_PATH,
            browser_batches=0,
            blocker=f"The application worker failed: {type(error).__name__}: {error}"[:4_000],
        )
    else:
        if result.output is None:
            accepted = ApplicationBuilderResult(
                status="blocked",
                source_path=APPLICATION_SOURCE_PATH,
                browser_batches=0,
                blocker="The application worker returned no result.",
            )
        else:
            worker = ApplicationBuilderResult.model_validate(result.output)
            accepted = await ApplicationBuildAcceptance(ctx, result.turn_id).accept(worker)
    return ToolResult(content=(TextContent(text=accepted.model_dump_json()),))


async def limit_application_builder_repair_reads(ctx: HookContext) -> Deny | None:
    """Limit consecutive source reads after the product audit returns repair work."""

    if ctx.turn is None or ctx.turn.subagent_profile != APPLICATION_BUILDER_NAME:
        return None
    match ctx.payload:
        case PreToolUse():
            pass
        case _:
            return None
    if ctx.payload.tool_name not in (
        APPLICATION_BUILDER_READ_TOOL,
        APPLICATION_BUILDER_EDIT_TOOL,
    ):
        return None
    attempt_key = APPLICATION_AUDIT_ATTEMPT_KEY.format(turn_id=ctx.turn.id)
    attempts = await ctx.ext.store.get(attempt_key)
    if attempts is None:
        return None
    if type(attempts) is not int:
        raise RuntimeError("application audit attempt count is not an integer")
    key = APPLICATION_BUILDER_REPAIR_READ_KEY.format(turn_id=ctx.turn.id)
    if ctx.payload.tool_name == APPLICATION_BUILDER_EDIT_TOOL:
        await ctx.ext.store.put(key, 0)
        return None
    stored = await ctx.ext.store.get(key)
    if stored is None:
        used = 0
    elif type(stored) is int:
        used = stored
    else:
        raise RuntimeError("application builder repair read count is not an integer")
    if used >= APPLICATION_BUILDER_REPAIR_READ_LIMIT:
        return Deny(reason=APPLICATION_BUILDER_REPAIR_READ_REASON)
    await ctx.ext.store.put(key, used + 1)
    return None


async def require_application_builder_qa(ctx: HookContext) -> Deny | None:
    """Refuse application deployment until deterministic product QA passes."""

    if ctx.turn is None or ctx.turn.subagent_profile != APPLICATION_BUILDER_NAME:
        return None
    key = APPLICATION_BUILDER_QA_PROOF_KEY.format(turn_id=ctx.turn.id)
    stored_proof = await ctx.ext.store.get(key)
    if stored_proof is None:
        return Deny(reason=APPLICATION_BUILDER_DEPLOY_GUARD_REASON)
    try:
        ApplicationQaProof.model_validate(stored_proof)
    except ValueError as error:
        raise RuntimeError("application builder QA proof is invalid") from error
    return None


APPLICATION_BUILDER_DELEGATION = ToolDef(
    name=APPLICATION_BUILDER_DELEGATION_TOOL,
    description=(
        "Delegate the current member request once to the fixed interactive ufo application "
        "worker. "
        "The worker inspects connected data, writes app.tsx, runs browser QA, repairs, deploys, "
        "and returns evidence. Product checks bind the accepted homepage."
    ),
    input_model=BuildUfoApplicationInput,
    handler=build_ufo_application,
    side_effecting=True,
)


APPLICATION_PREVIEW = ToolDef(
    name=APPLICATION_PREVIEW_TOOL,
    description=(
        "Render one application design from a small typed contract. This fixed product renderer "
        "shares one preview PNG. It runs no model, file or browser tool, deployment, or "
        "member-state change. Ask the member to build or revise the shared design."
    ),
    input_model=RenderApplicationPreviewInput,
    handler=render_application_preview,
    side_effecting=True,
)


APPLICATION_BUILDER_DESIGN = ToolDef(
    name=APPLICATION_BUILDER_DESIGN_TOOL,
    description=(
        "Write one complete SVG visual contract for the first laptop screen before app.tsx. "
        "The SVG fixes information order, layout, component shapes, labels, and action placement; "
        "it is not embedded in the application."
    ),
    input_model=WriteApplicationDesignInput,
    handler=write_application_design,
    profile_only=True,
)


APPLICATION_BUILDER_EDIT = ToolDef(
    name=APPLICATION_BUILDER_EDIT_TOOL,
    description=(
        "Replace exact unique text in the current app.tsx. Each edit has old_text copied exactly "
        "from the source and its replacement new_text."
    ),
    input_model=EditApplicationSourceInput,
    handler=edit_application_source,
    profile_only=True,
)

APPLICATION_BUILDER_READ = ToolDef(
    name=APPLICATION_BUILDER_READ_TOOL,
    description=(
        "Read bounded, diverse matching excerpts and match counts from app.tsx after an exact "
        "edit reports that old_text does not match."
    ),
    input_model=ReadApplicationSourceInput,
    handler=read_application_source,
    profile_only=True,
)

APPLICATION_BUILDER_WRITE = ToolDef(
    name=APPLICATION_BUILDER_WRITE_TOOL,
    description="Write the complete app.tsx source at the exact path in this build contract.",
    input_model=WriteApplicationSourceInput,
    handler=write_application_source,
    profile_only=True,
)

APPLICATION_BUILDER_PROFILE = SubagentProfile(
    name=APPLICATION_BUILDER_NAME,
    prompt=APPLICATION_BUILDER_PROMPT,
    tool_names=(
        "list_external_tools",
        "describe_external_tools",
        "search_connector_tools",
        "call_external_tool",
        "read",
        APPLICATION_BUILDER_DESIGN_TOOL,
        APPLICATION_BUILDER_QA_TOOL,
        APPLICATION_BUILDER_READ_TOOL,
        APPLICATION_BUILDER_EDIT_TOOL,
        APPLICATION_BUILDER_WRITE_TOOL,
        APPLICATION_BUILDER_DEPLOY_TOOL,
    ),
    input_model=ApplicationBuilderTask,
    output_model=ApplicationBuilderResult,
    max_rounds=APPLICATION_BUILDER_MAX_ROUNDS,
    model=APPLICATION_BUILDER_MODEL,
    reasoning=APPLICATION_BUILDER_REASONING,
    untrusted_output=True,
    isolated_tools=True,
    connector_read_only=True,
)
