"""The typed application-page builder profile and its scoped source tools."""

import asyncio
import json
import re
from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal
from uuid import UUID
from xml.etree import ElementTree

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from ufo.sdk.manifest import (
    Deny,
    HookContext,
    PreToolUse,
    SubagentProfile,
)
from ufo.sdk.sandbox import WORKSPACE_DIR, ContainmentError, ExecResult, contained_relative
from ufo.sdk.surfaces import MEMBER_ADMISSION
from ufo.sdk.tools import ObjectBinding, TextContent, ToolContext, ToolDef, ToolResult
from ufo_ext_sites.application_audit import (
    APPLICATION_AUDIT_ATTEMPT_KEY,
    APPLICATION_AUDIT_REQUEST_CONTRACT_KEY,
    APPLICATION_AUDIT_TURN_CONTRACT_KEY,
    APPLICATION_DESIGN_FOLD,
    APPLICATION_DESIGN_MAX_HEIGHT,
    DESIGN_REGION_MAX,
    DESIGN_REGION_MIN,
    AcceptedApplicationDesignEvidence,
    ApplicationAuditRegion,
    ApplicationQaProof,
    application_design_region_fold_failure,
    application_design_region_size_failure,
    application_region_relation,
)
from ufo_ext_sites.objects import SITE_KIND
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
APPLICATION_BUILDER_WIREFRAME_TOOL = "design_ufo_application"
APPLICATION_BUILDER_ACCEPT_DESIGN_TOOL = "accept_application_wireframe"
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
APPLICATION_CREATION_SKILL = "create-application"
APPLICATION_CREATION_ROUTE_KEY = "application-builder/route/{turn_id}"
APPLICATION_CREATION_REQUEST = re.compile(
    r"\b(?:build|create|make|set\s+up|spin\s+up)\s+"
    r"(?:(?:me|us)\s+)?(?:"
    r"(?:app|application)s?\b|"
    r"(?:a|an|another|new)\s+(?:[\w-]+\s+){0,3}(?:app|application)s?\b"
    r")",
    re.IGNORECASE,
)
APPLICATION_CREATION_SITE = re.compile(
    r"\b(?:website|web\s+site|webpage|web\s+page|standalone\s+page|"
    r"web\s+(?:app|application)|browser\s+(?:app|application)|homepage|"
    r"(?:slack|mobile|desktop)\s+(?:app|application)|full[-\s]stack)\b",
    re.IGNORECASE,
)
APPLICATION_CREATION_ROUTE_REASON = (
    "This member asked to create a ufo application. Load create-application before using any "
    "other tool."
)
APPLICATION_BUILDER_DEPLOY_GUARD_REASON = "Run and pass product QA before deployment."
APPLICATION_SCAFFOLD_PATH = "/workspace/ufo-app"
APPLICATION_SOURCE_PATH = f"{APPLICATION_SCAFFOLD_PATH}/app.tsx"
APPLICATION_DESIGN_PATH = f"{APPLICATION_SCAFFOLD_PATH}/application-design.svg"
APPLICATION_DESIGN_PREVIEW_RELATIVE = "tool-output/application-builder/{digest}.design.png"
APPLICATION_WIREFRAME_KEY = "application-wireframe/{name}"
APPLICATION_WIREFRAME_FILENAME = "{name}-wireframe-{digest}.svg"
APPLICATION_BUILDER_PROMPT = (
    Path(__file__).parent / "prompts" / "subagent_ufo_application_builder.md"
).read_text()
APPLICATION_BUILD_TIMEOUT_SECONDS = 600
APPLICATION_BUILD_ERROR_MAX_CHARS = 2_000
APPLICATION_DESIGN_AUDIT_TIMEOUT_SECONDS = 15
APPLICATION_DESIGN_AUDIT_MAX_BYTES = 4_096
APPLICATION_DESIGN_EVIDENCE_MAX_CHARS = 8_192
APPLICATION_DESIGN_MAX_CHARS = 128_000
APPLICATION_DESIGN_WIDTH = 305
APPLICATION_SOURCE_MAX_CHARS = 256_000
APPLICATION_SOURCE_EXCERPT_MAX_CHARS = 5_000
APPLICATION_DESIGN_EFFECT_ERROR = (
    "application design native bounds do not support clip, mask, or filter effects"
)
APPLICATION_DESIGN_EFFECT_STYLE = re.compile(
    r"(?:^|[;{])\s*(?:-(?:moz|webkit)-)?(?:clip-path|filter|mask(?:-image)?)\s*:\s*([^;}]+)",
    re.IGNORECASE,
)
SVG_DRAWING_ELEMENTS = frozenset(
    {"circle", "ellipse", "image", "line", "path", "polygon", "polyline", "rect", "text", "use"}
)
APPLICATION_AUDIT_SCRIPT = (
    Path(__file__).parent / "scripts" / "audit_application.cjs"
).read_bytes()
APPLICATION_DESIGN_REGIONS = TypeAdapter(tuple[ApplicationAuditRegion, ...])
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
  const streamParts=route.split("/");
  if(message.method==="GET"&&streamParts.length===3&&streamParts[0]==="turns"&&streamParts[1]&&streamParts[2]==="stream"){
    const terminal=JSON.stringify({
      status:"done",text:"",model:"preview",tokens:0,cost_micro_usd:0
    });
    event.source.postMessage({ufo:"opened",id:message.id},"*");
    event.source.postMessage({ufo:"frame",id:message.id,event:"terminal",data:terminal},"*");
    event.source.postMessage({ufo:"end",id:message.id},"*");
    return;
  }
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
APPLICATION_FIXED_CALL_CLAIM = """import os
from containment import ContainmentError, contained_file
import sys
from uuid import uuid4

data = sys.argv[3].encode()
if not data:
    raise SystemExit("application idempotency key is empty")
staged = f".ufo-staged-{uuid4().hex}"
descriptor = -1
try:
    with contained_file(sys.argv[1], sys.argv[2], create_parent=True) as target:
        if target.lstat() is not None:
            raise SystemExit(18 if target.read_bytes(len(data) + 1) == data else 17)
        descriptor = os.open(
            staged,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=target.parent_fd,
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(
                    staged,
                    target.name,
                    src_dir_fd=target.parent_fd,
                    dst_dir_fd=target.parent_fd,
                    follow_symlinks=False,
                )
            except FileExistsError:
                raise SystemExit(18 if target.read_bytes(len(data) + 1) == data else 17)
            os.fsync(target.parent_fd)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(staged, dir_fd=target.parent_fd)
            except FileNotFoundError:
                pass
            os.fsync(target.parent_fd)
except (ContainmentError, OSError) as error:
    raise SystemExit(str(error))"""
APPLICATION_DESIGN_ACCEPT = """import hashlib
import json
import os
import stat
from containment import ContainmentError, contained_file
import sys
from uuid import uuid4

def claim_call(identity):
    staged_name = f".ufo-staged-{uuid4().hex}"
    descriptor = -1
    with contained_file(sys.argv[5], sys.argv[6], create_parent=True) as target:
        if target.lstat() is not None:
            if target.read_bytes(len(identity) + 1) != identity:
                raise SystemExit(17)
            return
        descriptor = os.open(
            staged_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=target.parent_fd,
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(identity)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(
                    staged_name,
                    target.name,
                    src_dir_fd=target.parent_fd,
                    dst_dir_fd=target.parent_fd,
                    follow_symlinks=False,
                )
            except FileExistsError:
                if target.read_bytes(len(identity) + 1) != identity:
                    raise SystemExit(17)
            os.fsync(target.parent_fd)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(staged_name, dir_fd=target.parent_fd)
            except FileNotFoundError:
                pass
            os.fsync(target.parent_fd)

def target_matches(path, data, maximum, label):
    with contained_file(path, sys.argv[6], create_parent=True) as target:
        status = target.lstat()
        if status is None:
            return False
        if stat.S_IMODE(status.st_mode) != 0o400:
            raise SystemExit(f"{label} is not read-only")
        current = target.read_bytes(maximum + 1)
        if len(current) > maximum or current != data:
            raise SystemExit(f"{label} differs from this application design call")
        return True

def publish(path, data, maximum, label):
    with contained_file(path, sys.argv[6], create_parent=True) as target:
        staged_name = f".{target.name}.{uuid4().hex}.accepted"
        descriptor = os.open(
            staged_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=target.parent_fd,
        )
        try:
            os.fchmod(descriptor, 0o400)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(
                    staged_name,
                    target.name,
                    src_dir_fd=target.parent_fd,
                    dst_dir_fd=target.parent_fd,
                    follow_symlinks=False,
                )
                os.fsync(target.parent_fd)
            except FileExistsError:
                if not target_matches(path, data, maximum, label):
                    raise SystemExit(f"{label} was not published")
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(staged_name, dir_fd=target.parent_fd)
            except FileNotFoundError:
                pass

try:
    identity = sys.argv[7].encode()
    if not identity:
        raise SystemExit("application idempotency key is empty")
    pairs = (
        (sys.argv[1], sys.argv[2], int(sys.argv[8]), "application design"),
        (sys.argv[3], sys.argv[4], int(sys.argv[9]), "application design evidence"),
    )
    proposed = []
    for source_path, target_path, maximum, label in pairs:
        with contained_file(source_path, sys.argv[6]) as source:
            data = source.read_bytes(maximum + 1)
        if len(data) > maximum:
            raise SystemExit(f"{label} is too large")
        proposed.append((target_path, data, maximum, label))
    try:
        evidence = json.loads(proposed[1][1])
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SystemExit("application design evidence is invalid") from error
    if (
        not isinstance(evidence, dict)
        or evidence.get("design_sha256") != hashlib.sha256(proposed[0][1]).hexdigest()
    ):
        raise SystemExit("application design evidence does not match the design")
    claim_call(identity)
    present = [target_matches(*item) for item in proposed]
    for (target_path, data, maximum, label), exists in zip(proposed, present, strict=True):
        if not exists:
            publish(target_path, data, maximum, label)
    for target_path, data, maximum, label in proposed:
        if not target_matches(target_path, data, maximum, label):
            raise SystemExit(f"{label} was not published")
except (ContainmentError, OSError) as error:
    raise SystemExit(str(error))"""
APPLICATION_DESIGN_RELEASE_ACCEPTED = """import hashlib
from containment import ContainmentError, contained_file
import sys

try:
    identity = sys.argv[9].encode()
    owned = []
    pairs = (
        (sys.argv[1], sys.argv[3], int(sys.argv[5]), "accepted application design"),
        (sys.argv[2], sys.argv[4], int(sys.argv[6]), "accepted application design evidence"),
    )
    for path, digest, maximum, label in pairs:
        with contained_file(path, sys.argv[7]) as target:
            data = target.read_bytes(maximum + 1)
            if len(data) > maximum or hashlib.sha256(data).hexdigest() != digest:
                raise SystemExit(f"{label} is not owned")
            owned.append(path)
    with contained_file(sys.argv[8], sys.argv[7]) as claim:
        if claim.read_bytes(len(identity) + 1) != identity:
            raise SystemExit("application design claim is not owned")
        owned.append(sys.argv[8])
    for path in owned:
        with contained_file(path, sys.argv[7]) as target:
            target.unlink()
except (ContainmentError, OSError) as error:
    raise SystemExit(str(error))"""
APPLICATION_DESIGN_RELEASE_CLAIM = """from containment import ContainmentError, contained_file
import sys

try:
    identity = sys.argv[3].encode()
    with contained_file(sys.argv[1], sys.argv[2]) as claim:
        if claim.lstat() is not None:
            if claim.read_bytes(len(identity) + 1) != identity:
                raise SystemExit("application design claim is not owned")
            claim.unlink()
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
NON_NAMED_IMPORT = re.compile(r"(?m)^[ \t]*import\s+(?!type\s*\{|\{)")
NAMED_KIT_IMPORT = re.compile(
    r"(?ms)^[ \t]*import\s+(?P<type>type\s+)?\{(?P<names>[^{}]*)\}"
    r"\s*from\s*['\"]ufo/kit['\"]\s*;?"
)
KIT_IMPORT_NAME = re.compile(
    r"(?:(?P<type>type)\s+)?(?P<export>[A-Za-z_$][\w$]*)"
    r"(?:\s+as\s+(?P<local>[A-Za-z_$][\w$]*))?"
)
SOURCE_LITERAL_OR_COMMENT = re.compile(
    r"//[^\n]*|/\*.*?\*/|(?<![\w$])'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`",
    re.DOTALL,
)
JSX_COMPONENT = re.compile(r"<\s*([A-Z][A-Za-z0-9_$]*)\b")
LOCAL_NAMED_DECLARATION = re.compile(r"\b(?:class|function|const|let|var)\s+([A-Za-z_$][\w$]*)")
LOCAL_DESTRUCTURED_DECLARATION = re.compile(r"\b(?:const|let|var)\s*\{(?P<bindings>[^{}]*)\}\s*=")
LOCAL_DESTRUCTURED_BINDING = re.compile(
    r"(?:^|,)\s*(?:[A-Za-z_$][\w$]*\s*:\s*)?(?:\.\.\.)?"
    r"([A-Za-z_$][\w$]*)\s*(?=[,}=]|$)"
)
FUNCTION_PARAMETERS = re.compile(
    r"\bfunction\b[^()]*\((?P<function>[^()]*)\)"
    r"|\((?P<arrow>[^()]*)\)\s*=>"
    r"|(?P<single>\b[A-Za-z_$][\w$]*)\s*=>",
    re.DOTALL,
)


def _local_source_bindings(code: str) -> set[str]:
    bindings = set(LOCAL_NAMED_DECLARATION.findall(code))
    for declaration in LOCAL_DESTRUCTURED_DECLARATION.finditer(code):
        bindings.update(LOCAL_DESTRUCTURED_BINDING.findall(declaration.group("bindings")))
    for parameters in FUNCTION_PARAMETERS.finditer(code):
        single = parameters.group("single")
        if single:
            bindings.add(single)
            continue
        values = parameters.group("function") or parameters.group("arrow") or ""
        bindings.update(
            re.findall(
                r"(?:^|,)\s*(?:\.\.\.)?([A-Za-z_$][\w$]*)\s*(?=[:,?=]|$)",
                values,
            )
        )
        bindings.update(re.findall(r"(?:\{|,|:\s)([A-Za-z_$][\w$]*)\s*(?=[,}=])", values))
    return bindings


APPLICATION_KIT_COMPONENTS = frozenset(
    {
        "AgentIcon",
        "AppConversations",
        "ApplicationAction",
        "ArtifactText",
        "Avatar",
        "AvatarFallback",
        "AvatarStack",
        "Badge",
        "BrandMark",
        "Breakdown",
        "BreakdownHeader",
        "BreakdownLabel",
        "BreakdownMark",
        "BreakdownName",
        "BreakdownRow",
        "BreakdownRows",
        "BreakdownValue",
        "Button",
        "Card",
        "CardAction",
        "CardContent",
        "CardDescription",
        "CardFooter",
        "CardGrid",
        "CardHeader",
        "CardTitle",
        "Chart",
        "ChartBars",
        "ChatPane",
        "ConversationDetail",
        "DataTable",
        "Detail",
        "Dialog",
        "DialogTrigger",
        "DropdownMenu",
        "DropdownMenuCheckboxItem",
        "DropdownMenuContent",
        "DropdownMenuItem",
        "DropdownMenuLabel",
        "DropdownMenuRadioGroup",
        "DropdownMenuRadioItem",
        "DropdownMenuSeparator",
        "DropdownMenuTrigger",
        "Empty",
        "Facts",
        "FacetMenu",
        "FileSheet",
        "FoundingChat",
        "Group",
        "Header",
        "IconChevronDown",
        "IconChevronUp",
        "IconDots",
        "IconFilter2",
        "IconWorldWww",
        "IconX",
        "Lede",
        "Legend",
        "LegendItem",
        "Markdown",
        "MediaIcon",
        "Meter",
        "Moment",
        "ObjectDetail",
        "ObjectPane",
        "Page",
        "PageToolbar",
        "Pager",
        "Pane",
        "PaneNote",
        "Panel",
        "PanelBlank",
        "PanelEmpty",
        "PressRow",
        "RebuildDialog",
        "RowLines",
        "Section",
        "SectionApp",
        "Segmented",
        "Separator",
        "Sheet",
        "Stat",
        "StatDelta",
        "StatDescription",
        "StatHeader",
        "StatLabel",
        "StatMedia",
        "StatValue",
        "SurfaceGlyph",
        "Td",
        "TdFact",
        "ToolbarRule",
        "ViewSwitch",
        "Waiting",
    }
)
ROOT_MOUNT = re.compile(
    r"\bmountApp\s*\(\s*document\.getElementById\(\s*['\"]root['\"]\s*\)\s*!?\s*,"
)
# The rules a shipped app page is held to by `gates.py`, restated for the one page nothing else
# reads. A shipped page is walked in the repo; a generated page exists only in a member's sandbox,
# so this validator is where the same rules have to be true or they are true of half the product.
# The refusals are worded as the repair the builder should make, because its repair loop reads them.
# A Tailwind arbitrary value is always a utility carrying one — `w-[3px]`, `text-[#fff]` — or an
# arbitrary property, which spells `[prop:value]`. A bare `[...]` is JavaScript: an array of issue
# references or percentages reads exactly like a raw colour or length, and refusing it would block a
# page over its data.
ARBITRARY_VALUE = re.compile(r"[a-z][\w-]*-\[([^\]\n]*)\]")
ARBITRARY_PROPERTY = re.compile(r"\[([a-z-]+:[^\]\n]*)\]")
RAW_CSS_VALUE = re.compile(
    r"#[0-9a-fA-F]|\d+(?:\.\d+)?(?:px|rem|em|ch|ex|vh|vw|vmin|vmax|%)(?![\w-])"
)
COMPOSITION_STEPS = ("hair", "2xs", "sm", "2xl", "6xl", "8xl")
COMPOSITION_GAP = re.compile(r"(?<![\w-])gap-(?:x-|y-)?(\[[^\]]*\]|[\w.]+)")
FRAMED_STAT = re.compile(r"<Stat[\s>][^>]*?(?<![\w-])border(?![\w-])", re.S)
PAGE_CLASS_REFUSALS = (
    (re.compile(r"(?<![\w-])space-[xy]-"), "stack with flex and a gap"),
    (re.compile(r"(?<![\w-])dark:"), "the colour scheme carries itself; write no dark variant"),
    (re.compile(r"overflow-hidden text-ellipsis whitespace-nowrap"), "truncate says this"),
    (re.compile(r"className=\{`"), "compose classes with cn()"),
)
STYLE_TAG = re.compile(r"<style[\s/>]")
DATA_SLOT_ATTRIBUTE = re.compile(r"(?<![\w-])data-slot\s*=")

LITERAL_WHITE_ON_SCHEME_INK = re.compile(
    r"\bstyle\s*=\s*\{\{"
    r"(?=(?:(?!\}\}).)*\bbackground(?:Color)?\s*:(?:(?!\}\}).)*var\(--color-ink\))"
    r"(?=(?:(?!\}\}).)*\bcolor\s*:(?:(?!\}\}).)*['\"](?:#fff(?:fff)?|white)['\"])",
    re.DOTALL | re.IGNORECASE,
)
APPLICATION_DESIGN_REGION = re.compile(r"[a-z][a-z0-9-]{0,79}")
APPLICATION_DESIGN_KIT_COMPONENT = re.compile(r"[A-Z][A-Za-z0-9]*")
SOURCE_EDIT_PATCH = re.compile(
    r"\A<<<<<<< SEARCH\n(?P<old>.*?)\n=======\n(?P<new>.*?)\n>>>>>>>(?: REPLACE)?\n?\Z",
    re.DOTALL,
)
WORKSPACE_ROOT = PurePosixPath("/workspace")
BoundedSourceTerm = Annotated[str, Field(min_length=1, max_length=200)]
BoundedOldSource = Annotated[str, Field(min_length=1, max_length=20_000)]
BoundedNewSource = Annotated[str, Field(max_length=128_000)]
ApplicationName = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,62}[a-z0-9]$")]


class ApplicationBuilderTask(BaseModel):
    """One member request for a fixed-scaffold application."""

    objective: str = Field(min_length=1, max_length=20_000)
    scaffold_path: str
    source_path: str
    phase: Literal["wireframe", "build"] = "build"
    accepted_design_digest: str = Field(default="", pattern=r"^(?:[0-9a-f]{64})?$")
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

    model_config = ConfigDict(extra="forbid")


class DesignUfoApplicationInput(BaseModel):
    """The proposed application and optional member revision for one builder wireframe."""

    model_config = ConfigDict(extra="forbid")
    application_name: ApplicationName
    application_prompt: str = Field(min_length=1, max_length=16_000)
    revision: str = Field(default="", max_length=2_000)


class AcceptedApplicationWireframe(BaseModel):
    """The exact builder SVG held until the named application starts its build."""

    design_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    content: str = Field(min_length=1, max_length=APPLICATION_DESIGN_MAX_CHARS)


class ApplicationWireframeResult(BaseModel):
    """The exact SVG shared by one completed builder wireframe phase."""

    status: Literal["ready", "blocked"]
    shared_filename: str = ""
    design_digest: str = Field(default="", pattern=r"^(?:[0-9a-f]{64})?$")
    blocker: str = Field(default="", max_length=4_000)

    @model_validator(mode="after")
    def result_matches_status(self) -> "ApplicationWireframeResult":
        if self.status == "ready" and (not self.shared_filename or not self.design_digest):
            raise ValueError("a ready wireframe requires a shared filename and design digest")
        if self.status == "ready" and self.blocker:
            raise ValueError("a ready wireframe cannot include a blocker")
        if self.status == "blocked" and not self.blocker:
            raise ValueError("a blocked wireframe requires a blocker")
        if self.status == "blocked" and (self.shared_filename or self.design_digest):
            raise ValueError("a blocked wireframe cannot include shared design evidence")
        return self


class ApplicationBuilderResult(BaseModel):
    """The evidence returned after one wireframe phase or one complete application build."""

    status: Literal["wireframe", "deployed", "blocked"]
    source_path: str = ""
    design_path: str = ""
    design_digest: str = Field(default="", pattern=r"^(?:[0-9a-f]{64})?$")
    site_name: str = ""
    site_url: str = ""
    browser_batches: int = Field(ge=0, le=4)
    controls_checked: tuple[str, ...] = Field(default=(), max_length=100)
    observed_errors: tuple[str, ...] = Field(default=(), max_length=20)
    blocker: str = Field(default="", max_length=4_000)

    @model_validator(mode="after")
    def result_matches_status(self) -> "ApplicationBuilderResult":
        if self.status == "wireframe" and (not self.design_path or not self.design_digest):
            raise ValueError("a wireframe result requires its path and digest")
        if self.status == "wireframe" and (
            self.source_path or self.site_name or self.site_url or self.blocker
        ):
            raise ValueError("a wireframe result cannot include build output")
        if self.status == "deployed" and (not self.site_name or not self.site_url):
            raise ValueError("a deployed application requires site_name and site_url")
        if self.status == "deployed" and not self.source_path:
            raise ValueError("a deployed application requires its source path")
        if self.status == "deployed" and self.blocker:
            raise ValueError("a deployed application cannot include a blocker")
        if self.status == "blocked" and not self.blocker:
            raise ValueError("a blocked application requires a blocker")
        if self.status == "blocked" and (
            self.site_name or self.site_url or self.design_path or self.design_digest
        ):
            raise ValueError("a blocked application cannot include site identity")
        return self


@dataclass(frozen=True)
class ApplicationBuildAcceptance:
    """Bind one worker deployment only after product-owned source and browser checks pass."""

    ctx: ToolContext
    child_turn_id: UUID

    async def accept(self, result: ApplicationBuilderResult) -> ApplicationBuilderResult:
        if result.status == "wireframe":
            return self._blocked(result, "The worker returned a wireframe instead of a build.", 0)
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
    """One full-page SVG visual contract for the 305 px application lane."""

    content: str = Field(
        min_length=1,
        max_length=APPLICATION_DESIGN_MAX_CHARS,
        description=(
            'Full-page SVG with viewBox="0 0 305 H", width="305", matching finite '
            "content-driven height H, and the primary task and required facts above y=844. "
            "Keep every visible drawing and text bound inside the viewBox."
        ),
    )


class AcceptApplicationWireframeInput(BaseModel):
    """Seal the exact member-accepted SVG already staged for this build turn."""

    model_config = ConfigDict(extra="forbid")


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


def _validate_application_source(
    source: str, designed_kit_components: tuple[str, ...] = ()
) -> None:
    modules = tuple(left or right for left, right in IMPORT_MODULE.findall(source))
    if IMPORT_DECLARATION.search(source) is None or "ufo/kit" not in modules:
        raise ValueError("app.tsx must import its runtime and components from ufo/kit")
    if SIDE_EFFECT_IMPORT.search(source) or any(module != "ufo/kit" for module in modules):
        raise ValueError("app.tsx may import only from ufo/kit")
    if NON_NAMED_IMPORT.search(source):
        raise ValueError("app.tsx must use named imports from ufo/kit")
    if EXPORT_DECLARATION.search(source):
        raise ValueError("app.tsx must not export declarations")
    if "UfoAppKit" in source:
        raise ValueError("app.tsx must import from ufo/kit instead of using UfoAppKit")
    if ROOT_MOUNT.search(source) is None:
        raise ValueError("mountApp must receive the root element and a render callback")
    imported_components: dict[str, set[str]] = {}
    for declaration in NAMED_KIT_IMPORT.finditer(source):
        if declaration.group("type"):
            continue
        for value in declaration.group("names").split(","):
            imported = KIT_IMPORT_NAME.fullmatch(value.strip())
            if (
                imported is not None
                and imported.group("type") is None
                and imported.group("export") in APPLICATION_KIT_COMPONENTS
            ):
                imported_components.setdefault(imported.group("export"), set()).add(
                    imported.group("local") or imported.group("export")
                )
    code = SOURCE_LITERAL_OR_COMMENT.sub("", source)
    local_declarations = _local_source_bindings(code)
    rendered_components = set(JSX_COMPONENT.findall(code)) - local_declarations
    rendered_kit_components = {
        exported
        for exported, local_names in imported_components.items()
        if not local_names.isdisjoint(rendered_components)
    }
    if not rendered_kit_components:
        raise ValueError("app.tsx must render at least one UI component imported from ufo/kit")
    missing_designed_components = tuple(
        name for name in designed_kit_components if name not in rendered_kit_components
    )
    if missing_designed_components:
        label = "component" if len(missing_designed_components) == 1 else "components"
        raise ValueError(
            f"app.tsx must directly render designed Kit {label}: "
            f"{', '.join(missing_designed_components)}"
        )
    if LITERAL_WHITE_ON_SCHEME_INK.search(source):
        raise ValueError(
            "a --color-ink background must use --color-surface text in both colour schemes"
        )
    if STYLE_TAG.search(source):
        raise ValueError("app.tsx may not emit a <style> tag — the kit's theme is the sheet")
    if DATA_SLOT_ATTRIBUTE.search(source):
        raise ValueError("app.tsx: data-slot is reserved for ufo/kit components")
    for pattern, repair in PAGE_CLASS_REFUSALS:
        found = pattern.search(source)
        if found:
            raise ValueError(f"app.tsx: {found.group(0)!r} — {repair}")
    for pattern in (ARBITRARY_VALUE, ARBITRARY_PROPERTY):
        for segment in pattern.finditer(source):
            if RAW_CSS_VALUE.search(segment.group(1)):
                raise ValueError(
                    f"app.tsx: {segment.group(0)} names a raw value — "
                    "resolve it through a theme token"
                )
    if FRAMED_STAT.search(source):
        raise ValueError(
            "app.tsx: a Stat carries a border — a figure divides by the space around it, and a "
            "tile is what a Stat already is"
        )
    for gap in COMPOSITION_GAP.finditer(source):
        if gap.group(1) not in COMPOSITION_STEPS:
            raise ValueError(
                f"app.tsx: {gap.group(0)!r} is not a composition step — a page spaces its parts "
                f"with {', '.join('gap-' + step for step in COMPOSITION_STEPS)} and nothing else"
            )


def _validate_application_design(
    source: str,
) -> tuple[tuple[str, ...], tuple[str, ...], int]:
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
        or view_box[:3] != (0, 0, APPLICATION_DESIGN_WIDTH)
        or not view_box[3].is_integer()
        or not APPLICATION_DESIGN_FOLD <= view_box[3] <= APPLICATION_DESIGN_MAX_HEIGHT
        or root.attrib.get("width") != str(APPLICATION_DESIGN_WIDTH)
        or root.attrib.get("height") != str(round(view_box[3]))
    ):
        raise ValueError(
            'application design must use viewBox="0 0 305 H", width="305", and a matching '
            "integer height H from 844 through 4096"
        )
    regions = []
    kit_components: list[str] = []
    ids = set()
    drawing_elements = 0
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag.casefold() in {"clippath", "filter", "mask"}:
            raise ValueError(APPLICATION_DESIGN_EFFECT_ERROR)
        attributes = {
            name.rsplit("}", 1)[-1]: value.strip() for name, value in element.attrib.items()
        }
        if any(
            name.casefold() in {"clip-path", "filter", "mask", "mask-image"}
            and value.casefold() not in {"", "none"}
            for name, value in attributes.items()
        ) or any(
            match.group(1).strip().casefold() != "none"
            for value in (
                attributes.get("style", ""),
                "".join(element.itertext()) if tag == "style" else "",
            )
            for match in APPLICATION_DESIGN_EFFECT_STYLE.finditer(value)
        ):
            raise ValueError(APPLICATION_DESIGN_EFFECT_ERROR)
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
        element_id = element.attrib.get("id", "").strip()
        if element_id:
            if element_id in ids:
                raise ValueError("application design SVG ids must be unique")
            ids.add(element_id)
        if tag in {"script", "foreignObject"}:
            raise ValueError("application design must contain SVG drawing elements only")
        region = element.attrib.get("data-app-region")
        if region is not None:
            if tag != "g" or APPLICATION_DESIGN_REGION.fullmatch(region) is None:
                raise ValueError(
                    "application design regions must be lowercase slugs on SVG g elements"
                )
            regions.append(element)
        kit_component = element.attrib.get("data-kit-component")
        if kit_component is not None:
            if tag != "g":
                raise ValueError(
                    "application design data-kit-component must be on an SVG g element"
                )
            if not kit_component.strip():
                raise ValueError(
                    "application design data-kit-component must name one visual ufo/kit export"
                )
            if APPLICATION_DESIGN_KIT_COMPONENT.fullmatch(kit_component) is None:
                raise ValueError("application design data-kit-component must be one ComponentName")
            if kit_component not in APPLICATION_KIT_COMPONENTS:
                raise ValueError(
                    f"application design data-kit-component {kit_component!r} is not a visual "
                    "ufo/kit export"
                )
            if kit_component not in kit_components:
                kit_components.append(kit_component)
        for name, value in element.attrib.items():
            attribute = name.rsplit("}", 1)[-1].casefold()
            lowered = value.casefold()
            if attribute.startswith("on") or any(
                scheme in lowered for scheme in ("javascript:", "data:", "http:", "https:")
            ):
                raise ValueError("application design must not contain active or external content")
    if drawing_elements == 0:
        raise ValueError("application design must contain SVG drawing elements only")
    names = tuple(element.attrib["data-app-region"] for element in regions)
    if not DESIGN_REGION_MIN <= len(names) <= DESIGN_REGION_MAX or len(set(names)) != len(names):
        raise ValueError(
            f"application design requires {DESIGN_REGION_MIN} to {DESIGN_REGION_MAX} unique regions"
        )
    if any(
        descendant is not region and descendant.attrib.get("data-app-region") is not None
        for region in regions
        for descendant in region.iter()
    ):
        raise ValueError("application design regions must not be nested")
    if not kit_components:
        raise ValueError(
            "application design requires data-kit-component on at least one SVG g element"
        )
    return names, tuple(kit_components), int(view_box[3])


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


def application_design_acceptance_relative(design_path: str, turn_id: UUID) -> str:
    """Return the runtime-owned accepted design path for one builder turn."""

    return (
        "tool-output/application-builder/"
        f"{sha256(design_path.encode()).hexdigest()}.{turn_id}.accepted.svg"
    )


def application_design_evidence_relative(design_path: str, turn_id: UUID) -> str:
    """Return the runtime-owned accepted design evidence path for one builder turn."""

    return (
        "tool-output/application-builder/"
        f"{sha256(design_path.encode()).hexdigest()}.{turn_id}.accepted-design.json"
    )


async def _source_candidate_path(
    ctx: ToolContext, task: ApplicationBuilderTask, turn_id: UUID
) -> str:
    return await ctx.sandbox.runtime_path(
        "tool-output/application-builder/"
        f"{sha256(task.source_path.encode()).hexdigest()}.{turn_id}.candidate.tsx"
    )


async def _render_application_design(
    ctx: ToolContext,
    candidate_path: str,
    preview_path: str,
    names: tuple[str, ...],
    page_height: int,
) -> tuple[ApplicationAuditRegion, ...]:
    script_relative = f"tool-output/application-builder/{ctx.turn.id}/audit-application.cjs"
    script_path = await ctx.sandbox.runtime_path(script_relative)
    await ctx.sandbox.write_runtime_file(script_relative, APPLICATION_AUDIT_SCRIPT)
    rendered = await ctx.sandbox.sh(
        'node "$1" --design "$2" "$3"',
        script_path,
        candidate_path,
        preview_path,
        timeout_s=APPLICATION_DESIGN_AUDIT_TIMEOUT_SECONDS,
    )
    if rendered.exit_code != 0:
        detail = (rendered.stderr or rendered.stdout or "audit returned no error").strip()[:400]
        if "application design must not contain active or external content" in detail:
            raise ValueError("application design must not contain active or external content")
        overrun = re.search(
            r"application design extends outside its viewBox: [^\n]+",
            detail,
        )
        if overrun is not None:
            raise ValueError(overrun.group(0))
        overlap = re.search(
            r"application design has accidental internal overlap: [^\n]+",
            detail,
        )
        if overlap is not None:
            raise ValueError(overlap.group(0))
        raise RuntimeError(f"Run the browser audit successfully: {detail}")
    if len(rendered.stdout.encode()) > APPLICATION_DESIGN_AUDIT_MAX_BYTES:
        raise RuntimeError("application design audit returned malformed output")
    try:
        regions = APPLICATION_DESIGN_REGIONS.validate_json(rendered.stdout)
    except ValidationError as error:
        raise RuntimeError("application design audit returned malformed output") from error
    rendered_names = tuple(region.name for region in regions)
    if (
        len(regions) > DESIGN_REGION_MAX
        or rendered_names != names
        or len(set(rendered_names)) != len(rendered_names)
    ):
        raise ValueError(f"design has {len(regions)} unique visible named regions")
    for first_index, first in enumerate(regions):
        for second in regions[first_index + 1 :]:
            if application_region_relation(first, second, page_height) is None:
                raise ValueError(f"design regions {first.name} and {second.name} overlap")
    return regions


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


async def _require_application_design(
    ctx: ToolContext, task: ApplicationBuilderTask
) -> tuple[str, ...]:
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
    if (
        task.accepted_design_digest
        and sha256(result.stdout.encode()).hexdigest() != task.accepted_design_digest
    ):
        raise ValueError("the accepted application wireframe digest does not match")
    _, kit_components, _ = _validate_application_design(result.stdout)
    return kit_components


async def write_application_design(
    ctx: ToolContext, args: WriteApplicationDesignInput
) -> ToolResult:
    """Write one SVG visual contract before application source work starts."""

    if ctx.idempotency_key is None:
        raise RuntimeError("write_application_design requires an idempotency key")
    task = ApplicationBuilderTask.model_validate_json(ctx.turn.inbound)
    if task.accepted_design_digest and sha256(args.content.encode()).hexdigest() != (
        task.accepted_design_digest
    ):
        raise ValueError("the application wireframe differs from the member-accepted SVG")
    names, kit_components, page_height = _validate_application_design(args.content)
    design_path = _design_path(task)
    content = args.content.encode()
    content_sha256 = sha256(content).hexdigest()
    candidate_path = await ctx.sandbox.runtime_path(
        "tool-output/application-builder/"
        f"{sha256(design_path.encode()).hexdigest()}.{ctx.turn.id}."
        f"{content_sha256}.candidate.svg"
    )
    accepted_path = await ctx.sandbox.runtime_path(
        application_design_acceptance_relative(design_path, ctx.turn.id)
    )
    await ctx.sandbox.write_runtime_path(candidate_path, content)
    rendered_regions = await _render_application_design(
        ctx,
        candidate_path,
        await ctx.sandbox.runtime_path(
            APPLICATION_DESIGN_PREVIEW_RELATIVE.format(digest=content_sha256)
        ),
        names,
        page_height,
    )
    if size_failure := application_design_region_size_failure(rendered_regions, page_height):
        raise ValueError(size_failure)
    if fold_failure := application_design_region_fold_failure(rendered_regions, page_height):
        raise ValueError(fold_failure)
    evidence = AcceptedApplicationDesignEvidence(
        design_sha256=content_sha256,
        kit_components=kit_components,
        regions=rendered_regions,
    )
    evidence_content = evidence.model_dump_json(by_alias=True).encode()
    evidence_sha256 = sha256(evidence_content).hexdigest()
    evidence_candidate_path = await ctx.sandbox.runtime_path(
        "tool-output/application-builder/"
        f"{sha256(design_path.encode()).hexdigest()}.{ctx.turn.id}."
        f"{evidence_sha256}.candidate-design.json"
    )
    evidence_path = await ctx.sandbox.runtime_path(
        application_design_evidence_relative(design_path, ctx.turn.id)
    )
    await ctx.sandbox.write_runtime_path(evidence_candidate_path, evidence_content)
    claim_path = await _design_claim_path(ctx, task, ctx.turn.id)
    runtime_root = await _runtime_root(ctx)
    claim_identity = json.dumps(
        (
            str(ctx.turn.parent_turn_id or ""),
            str(ctx.turn.id),
            ctx.idempotency_key,
            content_sha256,
            evidence_sha256,
        ),
        separators=(",", ":"),
    )
    accepted = False
    try:
        acceptance = await ctx.sandbox.python(
            APPLICATION_DESIGN_ACCEPT,
            candidate_path,
            accepted_path,
            evidence_candidate_path,
            evidence_path,
            claim_path,
            runtime_root,
            claim_identity,
            str(APPLICATION_DESIGN_MAX_CHARS),
            str(APPLICATION_DESIGN_EVIDENCE_MAX_CHARS),
        )
        if acceptance.exit_code == 17:
            raise ValueError("the application design is already fixed for this build")
        if acceptance.exit_code != 0:
            raise RuntimeError(
                acceptance.stderr
                or acceptance.stdout
                or "accepted application design could not be written"
            )
        accepted = True
        await ctx.sandbox.write_file(design_path, content)
    except BaseException as error:
        cleanup_failures: list[str] = []
        if accepted:
            released_design, failures = await _complete_application_design_cleanup(
                ctx,
                APPLICATION_DESIGN_RELEASE_ACCEPTED,
                accepted_path,
                evidence_path,
                content_sha256,
                evidence_sha256,
                str(APPLICATION_DESIGN_MAX_CHARS),
                str(APPLICATION_DESIGN_EVIDENCE_MAX_CHARS),
                runtime_root,
                claim_path,
                claim_identity,
            )
            cleanup_failures.extend(failures)
            if released_design is not None and released_design.exit_code != 0:
                cleanup_failures.append(
                    released_design.stderr
                    or released_design.stdout
                    or "accepted application design could not be released"
                )
        else:
            released_claim, failures = await _complete_application_design_cleanup(
                ctx,
                APPLICATION_DESIGN_RELEASE_CLAIM,
                claim_path,
                runtime_root,
                claim_identity,
            )
            cleanup_failures.extend(failures)
            if released_claim is not None and released_claim.exit_code != 0:
                cleanup_failures.append(
                    released_claim.stderr
                    or released_claim.stdout
                    or "application design claim could not be released"
                )
        for failure in cleanup_failures:
            error.add_note(failure)
        raise
    return ToolResult(
        content=(
            TextContent(
                text=json.dumps(
                    {
                        "path": design_path,
                        "design_digest": sha256(content).hexdigest(),
                        "size_bytes": len(content),
                        "page_height": page_height,
                        "rendered_regions": [
                            {
                                key: value
                                for key, value in region.model_dump(
                                    mode="json", by_alias=True
                                ).items()
                                if key != "visibleText" or value
                            }
                            for region in rendered_regions
                        ],
                    }
                )
            ),
        )
    )


async def accept_application_wireframe(
    ctx: ToolContext, _args: AcceptApplicationWireframeInput
) -> ToolResult:
    """Seal the staged member-approved SVG as this build turn's design evidence."""

    task = ApplicationBuilderTask.model_validate_json(ctx.turn.inbound)
    if not task.accepted_design_digest:
        raise ValueError("this application build has no member-accepted wireframe")
    result = await ctx.sandbox.python(APPLICATION_SOURCE_READ, _design_path(task), WORKSPACE_DIR)
    if result.exit_code != 0 or not result.stdout:
        raise ValueError("the accepted application wireframe is missing")
    return await write_application_design(ctx, WriteApplicationDesignInput(content=result.stdout))


async def _complete_application_design_cleanup(
    ctx: ToolContext, program: str, *args: str
) -> tuple[ExecResult | None, tuple[str, ...]]:
    task = asyncio.create_task(ctx.sandbox.python(program, *args))
    failures = []
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            failures.append(str(error) or "application design cleanup was interrupted")
        except BaseException:
            pass
    try:
        return task.result(), tuple(failures)
    except BaseException as error:
        failures.append(str(error) or type(error).__name__)
        return None, tuple(failures)


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
    designed_kit_components = await _require_application_design(ctx, task)
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
    _validate_application_source(source, designed_kit_components)
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
    designed_kit_components = await _require_application_design(ctx, task)
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
        _validate_application_source(args.content, designed_kit_components)
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


async def design_ufo_application(ctx: ToolContext, args: DesignUfoApplicationInput) -> ToolResult:
    """Run the application builder's wireframe phase and share its exact accepted SVG."""

    if ctx.ext is None:
        raise RuntimeError("the application builder dispatched without its extension context")
    if ctx.idempotency_key is None:
        raise RuntimeError("design_ufo_application requires an idempotency key")
    await _ensure_application_scaffold(ctx)
    revision = f"\n\nMember revision:\n{args.revision}" if args.revision else ""
    result = await ctx.spawn(
        f"profile:{APPLICATION_BUILDER_NAME}",
        ApplicationBuilderTask(
            objective=f"Application instructions:\n{args.application_prompt}{revision}",
            scaffold_path=APPLICATION_SCAFFOLD_PATH,
            source_path=APPLICATION_SOURCE_PATH,
            phase="wireframe",
        ).model_dump(),
        dedup_key=ctx.idempotency_key,
    )
    if result.output is None:
        blocked = ApplicationWireframeResult(
            status="blocked", blocker="The application builder returned no wireframe."
        )
        return ToolResult(content=(TextContent(text=blocked.model_dump_json()),))
    worker = ApplicationBuilderResult.model_validate(result.output)
    if worker.status == "blocked":
        blocked = ApplicationWireframeResult(status="blocked", blocker=worker.blocker)
        return ToolResult(content=(TextContent(text=blocked.model_dump_json()),))
    if worker.status != "wireframe" or worker.design_path != APPLICATION_DESIGN_PATH:
        raise RuntimeError("the application builder returned no accepted wireframe")
    rendered = await ctx.sandbox.python(
        APPLICATION_SOURCE_READ, APPLICATION_DESIGN_PATH, WORKSPACE_DIR
    )
    if rendered.exit_code != 0 or not rendered.stdout:
        raise RuntimeError("the application builder wireframe is missing")
    _validate_application_design(rendered.stdout)
    content = rendered.stdout.encode()
    digest = sha256(content).hexdigest()
    if worker.design_digest != digest:
        raise RuntimeError("the application builder wireframe digest does not match")
    filename = APPLICATION_WIREFRAME_FILENAME.format(name=args.application_name, digest=digest[:12])
    preview = await ctx.store_preview(
        await ctx.sandbox.runtime_path(APPLICATION_DESIGN_PREVIEW_RELATIVE.format(digest=digest)),
        f"{PurePosixPath(filename).stem}-preview",
    )
    if preview is None:
        raise RuntimeError("the application builder wireframe preview is missing")
    await ctx.share_artifact(filename, content, "Application wireframe", preview=preview)
    await ctx.ext.store.put(
        APPLICATION_WIREFRAME_KEY.format(name=args.application_name),
        AcceptedApplicationWireframe(
            design_digest=digest,
            content=rendered.stdout,
        ).model_dump(mode="json"),
    )
    ready = ApplicationWireframeResult(
        status="ready", shared_filename=filename, design_digest=digest
    )
    return ToolResult(content=(TextContent(text=ready.model_dump_json()),))


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
    if ctx.idempotency_key is None:
        raise RuntimeError("build_ufo_application requires an idempotency key")
    claim_path = await ctx.sandbox.runtime_path(
        f"tool-output/application-builder/{ctx.turn.id}.delegated"
    )
    runtime_root = await _runtime_root(ctx)
    claim = await ctx.sandbox.python(
        APPLICATION_FIXED_CALL_CLAIM,
        claim_path,
        runtime_root,
        ctx.idempotency_key,
    )
    if claim.exit_code == 17:
        raise ValueError("build_ufo_application already ran for this parent turn")
    if claim.exit_code not in (0, 18):
        raise RuntimeError(claim.stderr or "application delegation could not be claimed")
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
    accepted_design_digest = ""
    wireframe_key = APPLICATION_WIREFRAME_KEY.format(name=ctx.agent.name)
    stored_wireframe = await ctx.ext.store.get(wireframe_key)
    if stored_wireframe is not None:
        try:
            wireframe = AcceptedApplicationWireframe.model_validate(stored_wireframe)
        except ValueError as error:
            raise RuntimeError("the stored application wireframe is invalid") from error
        _validate_application_design(wireframe.content)
        if sha256(wireframe.content.encode()).hexdigest() != wireframe.design_digest:
            raise RuntimeError("the stored application wireframe digest does not match")
        await ctx.sandbox.write_file(APPLICATION_DESIGN_PATH, wireframe.content.encode())
        accepted_design_digest = wireframe.design_digest
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
                accepted_design_digest=accepted_design_digest,
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
    if accepted_design_digest and accepted.status == "deployed":
        # The accepted wireframe is spent once the page it describes is bound: a later request to
        # reshape that page reaches a build free to design again, while a build that never bound
        # the page keeps the SVG the member accepted for its next attempt.
        await ctx.ext.store.delete(wireframe_key)
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


async def enforce_application_builder_phase(ctx: HookContext) -> Deny | None:
    """Keep the wireframe phase out of source, connector, QA, and deployment work."""

    if ctx.turn is None or ctx.turn.subagent_profile != APPLICATION_BUILDER_NAME:
        return None
    match ctx.payload:
        case PreToolUse():
            pass
        case _:
            return None
    task = ApplicationBuilderTask.model_validate_json(ctx.turn.inbound)
    if task.phase == "wireframe" and ctx.payload.tool_name != APPLICATION_BUILDER_DESIGN_TOOL:
        return Deny(reason="The wireframe phase can only read style and write its SVG design.")
    if task.accepted_design_digest and ctx.payload.tool_name == APPLICATION_BUILDER_DESIGN_TOOL:
        return Deny(reason="The member already accepted the application wireframe.")
    return None


def is_application_creation_request(text: str) -> bool:
    return (
        APPLICATION_CREATION_REQUEST.search(text) is not None
        and APPLICATION_CREATION_SITE.search(text) is None
    )


async def enforce_application_creation_route(ctx: HookContext) -> Deny | None:
    if (
        ctx.turn is None
        or ctx.agent is None
        or not ctx.agent.is_main
        or ctx.turn.subagent_profile is not None
        or ctx.turn.speaker_member_id is None
        or ctx.turn.admission_source != MEMBER_ADMISSION
        or not is_application_creation_request(ctx.turn.inbound)
    ):
        return None
    key = APPLICATION_CREATION_ROUTE_KEY.format(turn_id=ctx.turn.id)
    match ctx.payload:
        case PreToolUse(tool_name="load_skill", tool_input=tool_input):
            if await ctx.ext.store.get(key) is not None:
                return None
            if tool_input.model_dump().get("name") != APPLICATION_CREATION_SKILL:
                return Deny(reason=APPLICATION_CREATION_ROUTE_REASON)
            await ctx.ext.store.put(key, True)
            return None
        case PreToolUse():
            if await ctx.ext.store.get(key) is None:
                return Deny(reason=APPLICATION_CREATION_ROUTE_REASON)
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
    bound=ObjectBinding(kind=SITE_KIND, binding="collection"),
)


APPLICATION_BUILDER_WIREFRAME = ToolDef(
    name=APPLICATION_BUILDER_WIREFRAME_TOOL,
    description=(
        "Run the ufo application builder's wireframe phase for the proposed name and complete "
        "application prompt. It shares the exact accepted SVG and stores it for that application's "
        "build. Pass the member's requested change in revision when replacing a prior wireframe."
    ),
    input_model=DesignUfoApplicationInput,
    handler=design_ufo_application,
    side_effecting=True,
    bound=ObjectBinding(kind=SITE_KIND, binding="collection"),
)


APPLICATION_BUILDER_ACCEPT_DESIGN = ToolDef(
    name=APPLICATION_BUILDER_ACCEPT_DESIGN_TOOL,
    description=(
        "Accept the exact member-approved application-design.svg already staged for this build "
        "turn. It validates and seals the SVG without generating or changing it."
    ),
    input_model=AcceptApplicationWireframeInput,
    handler=accept_application_wireframe,
    side_effecting=True,
    profile_only=True,
)


APPLICATION_BUILDER_DESIGN = ToolDef(
    name=APPLICATION_BUILDER_DESIGN_TOOL,
    description=(
        "Write one full-page SVG visual contract for a 305 px-wide app lane before app.tsx. "
        'Use viewBox="0 0 305 H", width="305", and a matching finite content-driven height H. '
        "Keep every visible bound inside the viewBox. Keep the primary task and the required "
        "facts above y=844, and do not draw one region as a band across y=844. Do not design a "
        "laptop or desktop layout. "
        "The SVG fixes information order, layout, component shapes, labels, and action placement; "
        "it is not embedded in the app."
    ),
    input_model=WriteApplicationDesignInput,
    handler=write_application_design,
    side_effecting=True,
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
        APPLICATION_BUILDER_ACCEPT_DESIGN_TOOL,
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
