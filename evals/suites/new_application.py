"""Creating an application in chat: the one answered form, and the row it writes.

An `agent` object is the one kind a member cannot undo, so every trial grades the durable row
rather than the reply — a right-sounding answer that wrote the wrong spec fails — and grades the
order of the turn, since a form answered after the write agreed to nothing. Both ways in run the
design pass, so every creating case grades it: the pictures stand before the choice they ask for,
and the create stands after the choice. What separates the two paths is the proposal that opens a
guided build, which is why the design grader is told what stood in front of it rather than assuming
one. The last case is the neighbour: an application the workspace already holds must be wired,
never created a second time and never rewritten, because applying its name is an update.
"""

from __future__ import annotations

import asyncio
import io
import json
import stat
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from itertools import permutations
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import sqlalchemy as sa
import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from ufo_ext_sites.application_audit import (
    APPLICATION_DESIGN_FOLD,
    APPLICATION_DESIGN_MAX_HEIGHT,
    ApplicationAuditRegion,
    application_region_relation,
)
from ufo_ext_sites.application_builder import (
    APPLICATION_BUILDER_DELEGATION,
    APPLICATION_BUILDER_DELEGATION_TOOL,
    APPLICATION_BUILDER_DEPLOY_TOOL,
    APPLICATION_BUILDER_DESIGN_TOOL,
    APPLICATION_BUILDER_NAME,
    APPLICATION_BUILDER_QA_TOOL,
    APPLICATION_BUILDER_WRITE_TOOL,
    APPLICATION_SOURCE_PATH,
    ApplicationBuilderResult,
    ApplicationBuilderTask,
    RenderApplicationPreviewInput,
    homepage_design_block,
)
from ufo_ext_sites.store import hosted_site
from ufo_ext_web.surface import SEED_PROMPT

from evals.harness.capability import (
    ArtifactProbeResult,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    SharedArtifact,
    ToolInvocation,
    WorkspaceProbe,
)
from evals.harness.memory_fence import forget_workspace_memory
from evals.harness.scenario import EvalSeed, ScenarioCase, ScenarioOutcome, ScenarioUser
from evals.harness.target import CapabilityTarget
from evals.suites.app_audit_probe import app_audit_command
from evals.suites.ufo_app_bench import APP_WORKSPACE_FILES
from ufo.db import workspace_tx
from ufo.ext.context import ScopedStore
from ufo.kinds.agents import AGENT_KIND
from ufo.models.interface import AUTO_MODEL
from ufo.objects import ENVELOPE_KEYS
from ufo.schema import tables
from ufo.schema.records import auto_agent_icon
from ufo.turns.audience import conversation_audience
from ufo.workspace import ws_current

SKILL = "create-application"
PREVIEW_TOOL = "action:site:render_application_preview"
BUILD_ACTION = f"action:site:{APPLICATION_BUILDER_DELEGATION_TOOL}"
HOMEPAGE_ACTION = "action:agent:set_homepage"
WEB_EXTENSION = "web"
HOMEPAGE_SEED_PREFIX = "homepage-seed/"
SANDBOX_CONTAINER_PREFIX = "ufo-sbx-"
REPAIR_EVIDENCE_EXTENSION = "evals"
REPAIR_SOURCE_KEY = "application-repair/source/{application_id}"
REPAIR_BOUND_KEY = "application-repair/bound/{application_id}"
APPLICATION_ARTIFACT_NAME = "homepage"
APPLICATION_ARTIFACT_OUTPUT = ".eval-output/homepage"
APPLICATION_ARTIFACT_TIMEOUT_SECONDS = 120
APPLICATION_ARTIFACT_MAX_BYTES = 4 * 1024 * 1024
APPLICATION_BUNDLE_MAX_BYTES = 2 * 1024 * 1024
APPLICATION_BUNDLE_MAX_FILES = 1_000
REGION_IDENTITY_CONNECTIVES = frozenset(
    {"a", "an", "and", "for", "of", "on", "the", "to", "with", "you", "your"}
)
EXISTING_APPLICATION = "invoice-intake"
EXISTING_PROMPT = "You file invoices for the finance team. Ask before paying anything."
SATISFIED_INSTRUCTION = (
    "Answer what the assistant asks and approve what it proposes. Do not name the application "
    "yourself and do not write its instructions. Choose Build it on the design preview. A preview "
    "is not the application: never stop after one. End the conversation once it exists."
)
GUIDED_INSTRUCTION = (
    "Send exactly 'Build me a new app.' first. Choose Build that on the proposal. Accept the "
    "proposed job in the form and choose Just me. Choose Build it after the design preview. End "
    "the conversation once the application exists."
)
GUIDED_REVISION_INSTRUCTION = (
    "Send exactly 'Build me a new app.' first. Choose Build that on the proposal. Accept the "
    "proposed job in the form and choose Just me. On the first design, choose Change the design "
    "and ask it to put the overdue queue above the summary. Choose Build it on the revised design. "
    "End the conversation once the application exists. A preview is not the application. Never "
    "stop after a preview or design change."
)


class _ObjectApplyResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: str = Field(min_length=1)
    name: str = Field(min_length=1)
    result: Literal["created", "updated"]
    agent: str | None = Field(default=None, min_length=1)


@dataclass(frozen=True)
class _ApplicationIdentity:
    name: str
    create_index: int
    final_apply_index: int


@dataclass(frozen=True)
class _AcceptedApplicationDesign:
    application: _ApplicationIdentity
    preview_index: int
    preview: ToolInvocation
    contract: RenderApplicationPreviewInput


@dataclass(frozen=True)
class _ApplicationRecord:
    id: UUID
    name: str
    prompt: str
    model: str
    reasoning: str
    visibility: str
    owner_member_id: UUID | None


@dataclass(frozen=True)
class _CreatedApplication:
    identity: _ApplicationIdentity
    application: _ApplicationRecord


class _IncompleteApplicationPreview(ValueError):
    pass


@dataclass(frozen=True)
class _ApplicationHomepageArtifacts:
    def directory(self, workspace: Path, relative: Path, required: bool) -> Path:
        resolved_workspace = workspace.resolve(strict=True)
        if not workspace.is_dir() or relative.is_absolute() or ".." in relative.parts:
            raise ValueError("application artifact root is outside its workspace")
        current = workspace
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                raise ValueError(f"application artifact root {relative} contains a symlink")
            if current.exists() and not current.is_dir():
                raise ValueError(f"application artifact root {relative} is not a directory")
            if not current.exists():
                if required:
                    raise ValueError(f"application artifact root {relative} does not exist")
        resolved = current.resolve(strict=required)
        if not resolved.is_relative_to(resolved_workspace):
            raise ValueError("application artifact root is outside its workspace")
        return current

    def file(self, workspace: Path, path: Path) -> bytes:
        if path.is_symlink():
            raise ValueError(f"application artifact file {path.name} is a symlink")
        status = path.lstat()
        if not stat.S_ISREG(status.st_mode):
            raise ValueError(f"application artifact file {path.name} is not a regular file")
        if not path.resolve(strict=True).is_relative_to(workspace.resolve(strict=True)):
            raise ValueError("application artifact file is outside its workspace")
        return path.read_bytes()

    def preview_bundle(self, workspace: Path) -> bytes:
        root = self.directory(workspace, Path("ufo-app"), required=True)
        preview = root / "preview.html"
        dist_path = root / "dist"
        if not preview.exists() or not dist_path.exists():
            raise _IncompleteApplicationPreview("application has no runnable preview bundle")
        dist = self.directory(workspace, Path("ufo-app/dist"), required=True)
        index = dist / "index.html"
        if not index.exists():
            raise _IncompleteApplicationPreview("application has no runnable preview bundle")
        candidates = sorted(dist.rglob("*"))
        paths: list[Path] = [preview]
        for path in candidates:
            if path.is_symlink():
                raise ValueError("application preview bundle contains an unsafe file")
            if not path.resolve(strict=True).is_relative_to(workspace.resolve(strict=True)):
                raise ValueError("application preview bundle escapes its workspace")
            if path.is_dir():
                continue
            paths.append(path)
        if index not in paths:
            raise _IncompleteApplicationPreview("application has no runnable preview bundle")
        if len(paths) > APPLICATION_BUNDLE_MAX_FILES:
            raise ValueError("application preview bundle has an invalid file count")
        entries: list[tuple[str, bytes]] = []
        total = 0
        for path in paths:
            relative = path.relative_to(root).as_posix()
            if relative.startswith("/") or ".." in Path(relative).parts:
                raise ValueError("application preview bundle contains an unsafe path")
            content = self.file(workspace, path)
            total += len(content)
            if total > APPLICATION_BUNDLE_MAX_BYTES:
                raise _IncompleteApplicationPreview("application preview bundle is too large")
            entries.append((relative, content))
        body = io.BytesIO()
        with zipfile.ZipFile(body, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in entries:
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(info, content)
        bundle = body.getvalue()
        if len(bundle) > APPLICATION_BUNDLE_MAX_BYTES:
            raise _IncompleteApplicationPreview("application preview bundle is too large")
        return bundle

    def artifact(
        self, workspace: Path, relative_root: Path, name: str, artifact_name: str
    ) -> SharedArtifact | None:
        root = self.directory(workspace, relative_root, required=True)
        path = root / name
        if not path.exists():
            return None
        return SharedArtifact(artifact_name, self.file(workspace, path))

    async def __call__(
        self, output: CapabilityOutput, probe: WorkspaceProbe
    ) -> ArtifactProbeResult:
        if output.workspace_dir is None:
            return ArtifactProbeResult(error="application artifact capture has no workspace")
        workspace = output.workspace_dir
        application = self.directory(workspace, Path("ufo-app"), required=False)
        if not application.exists():
            return ArtifactProbeResult(error="application artifact root ufo-app does not exist")
        self.directory(workspace, Path(".eval-output/homepage"), required=False)
        source = await asyncio.to_thread(
            self.artifact, workspace, Path("ufo-app"), "app.tsx", "homepage-app.tsx"
        )
        design = await asyncio.to_thread(
            self.artifact,
            workspace,
            Path("ufo-app"),
            "application-design.svg",
            "homepage-design.svg",
        )
        artifacts = tuple(item for item in (source, design) if item is not None)
        if source is None:
            return ArtifactProbeResult(
                artifacts=artifacts,
                error="application artifact capture found no generated app.tsx",
                max_payload_bytes=APPLICATION_ARTIFACT_MAX_BYTES,
            )
        bundle: SharedArtifact | None = None
        bundle_error = ""
        try:
            content = await asyncio.to_thread(self.preview_bundle, workspace)
            bundle = SharedArtifact("homepage-preview.zip", content)
        except _IncompleteApplicationPreview as error:
            bundle_error = str(error)
        result = await probe.run(
            app_audit_command(
                name=APPLICATION_ARTIFACT_NAME,
                output_dir=f"/workspace/{APPLICATION_ARTIFACT_OUTPUT}",
                project="/workspace/ufo-app",
                design_path="/workspace/ufo-app/application-design.svg",
                compile_source=False,
            ),
            APPLICATION_ARTIFACT_TIMEOUT_SECONDS,
        )
        captured_root = self.directory(workspace, Path(APPLICATION_ARTIFACT_OUTPUT), required=False)
        names = (
            ("homepage-interactive.html", "homepage-interactive.html"),
            ("homepage-audit.json", "homepage-audit.json"),
            ("homepage-light.png", "homepage-light.png"),
            ("homepage-dark.png", "homepage-dark.png"),
            ("homepage-static.html", "homepage-static.html"),
            ("homepage-design-evidence.json", "homepage-design-evidence.json"),
        )
        captured: list[SharedArtifact | None] = []
        if captured_root.exists():
            captured = await asyncio.gather(
                *(
                    asyncio.to_thread(
                        self.artifact,
                        workspace,
                        Path(APPLICATION_ARTIFACT_OUTPUT),
                        path,
                        artifact_name,
                    )
                    for path, artifact_name in names
                )
            )
        details = []
        if bundle_error:
            details.append(bundle_error)
        if result.exit_code != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "no command output"
            details.append(f"application audit artifact capture failed: {detail[:500]}")
        retained = tuple(item for item in (*captured, *artifacts, bundle) if item is not None)
        return ArtifactProbeResult(
            artifacts=retained,
            error="; ".join(details),
            max_payload_bytes=APPLICATION_ARTIFACT_MAX_BYTES,
        )


APPLICATION_HOMEPAGE_ARTIFACTS = _ApplicationHomepageArtifacts()


async def _applications(name: str) -> tuple[_ApplicationRecord, ...]:
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    tables.agent.c.id,
                    tables.agent.c.name,
                    tables.agent.c.prompt,
                    tables.agent.c.model,
                    tables.agent.c.reasoning,
                    tables.agent.c.visibility,
                    tables.agent.c.owner_member_id,
                ).where(
                    tables.agent.c.workspace_id == ws_current().workspace_id,
                    tables.agent.c.name == name,
                    tables.agent.c.is_main.is_(False),
                )
            )
        ).all()
    return tuple(_ApplicationRecord(*row) for row in rows)


async def _application(name: str) -> _ApplicationRecord | None:
    """One application as its durable row, by the name this conversation's own apply wrote — so an
    application another trial left in the workspace can never stand in this case's count."""
    applications = await _applications(name)
    return applications[0] if len(applications) == 1 else None


async def _fixture_row() -> sa.Row | None:
    """The suite's own fixture as it stands, by name."""
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.agent.c.name, tables.agent.c.prompt).where(
                    tables.agent.c.workspace_id == ws_current().workspace_id,
                    tables.agent.c.name == EXISTING_APPLICATION,
                    tables.agent.c.is_main.is_(False),
                )
            )
        ).one_or_none()


def _seeded(*existing: str) -> EvalSeed:
    """Clear the applications an earlier trial created, then seed the ones this case starts with.
    Only a member-owned application with no conversation is cleared, so a provisioned agent the
    deploy shipped and anything a member has actually talked to both survive — except a row
    bearing the suite's own fixture name, which the seed takes back to fixture state in one
    upsert. One statement, because the name is not this seed's alone: A02's member asks for an
    invoice-reading application and the assistant picks the name, so a row called invoice-intake
    can commit between any two statements this seed runs — a reclaim-then-insert leaves exactly
    that window, and the unique-key raise it ends in discards the whole run's records. A leftover
    it cannot clear it leaves standing — deleting would chase every table that references a
    talked-to agent, and disowning changes the workspace the next case routes in — so the graders
    read this conversation's own applies instead of counting the workspace."""

    async def seed(workspace_id: UUID, _agent_id: UUID) -> None:
        await forget_workspace_memory()
        async with workspace_tx() as connection:
            spare = (
                (
                    await connection.execute(
                        sa.select(tables.agent.c.id).where(
                            tables.agent.c.workspace_id == workspace_id,
                            tables.agent.c.is_main.is_(False),
                            tables.agent.c.owner_member_id.is_not(None),
                            ~sa.select(tables.conversation.c.id)
                            .where(tables.conversation.c.agent_id == tables.agent.c.id)
                            .exists(),
                        )
                    )
                )
                .scalars()
                .all()
            )
            if spare:
                await connection.execute(
                    sa.delete(tables.connector_grant).where(
                        tables.connector_grant.c.workspace_id == workspace_id,
                        tables.connector_grant.c.agent_id.in_(spare),
                    )
                )
                await connection.execute(
                    sa.delete(tables.agent).where(tables.agent.c.id.in_(spare))
                )
            if not existing:
                return
            owner = (
                await connection.execute(
                    sa.select(tables.member.c.id)
                    .where(
                        tables.member.c.workspace_id == workspace_id,
                        tables.member.c.is_admin.is_(True),
                    )
                    .order_by(tables.member.c.created_at)
                    .limit(1)
                )
            ).scalar_one()
            insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
            for name in existing:
                fixture = {
                    "prompt": EXISTING_PROMPT,
                    "model": AUTO_MODEL,
                    "reasoning": "auto",
                    "visibility": "private",
                    "internet_access_allowed": True,
                    "sandbox_size": "small",
                    "owner_member_id": owner,
                }
                await connection.execute(
                    insert(tables.agent)
                    .values(
                        id=uuid4(),
                        workspace_id=workspace_id,
                        name=name,
                        icon=auto_agent_icon(name, ()),
                        is_main=False,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                        **fixture,
                    )
                    .on_conflict_do_update(
                        index_elements=[tables.agent.c.workspace_id, tables.agent.c.name],
                        set_={"updated_at": sa.func.now(), **fixture},
                        where=tables.agent.c.is_main.is_(False),
                    )
                )

    return seed


def _agent_applies(output: CapabilityOutput) -> tuple[tuple[int, str], ...]:
    """Where in the trajectory a successful object_apply carried a well-formed agent manifest, and
    the name each applied."""
    applies = []
    for index, call in enumerate(output.calls):
        if call.name != "object_apply" or not call.succeeded:
            continue
        try:
            document = yaml.safe_load(str(call.input.get("manifest", "")))
        except yaml.YAMLError:
            continue
        if (
            isinstance(document, dict)
            and set(document) == ENVELOPE_KEYS
            and document.get("kind") == AGENT_KIND
        ):
            applies.append((index, str(document.get("name", ""))))
    return tuple(applies)


def _created_application_identity(
    output: CapabilityOutput,
) -> tuple[_ApplicationIdentity | None, str | None]:
    identity: _ApplicationIdentity | None = None
    for index, call in enumerate(output.calls):
        if call.name != "object_apply" or not call.succeeded:
            continue
        try:
            result = _ObjectApplyResult.model_validate_json(call.result)
        except ValueError:
            try:
                manifest = yaml.safe_load(str(call.input.get("manifest", "")))
            except yaml.YAMLError:
                continue
            if isinstance(manifest, dict) and manifest.get("kind") == AGENT_KIND:
                return None, f"object_apply call {index + 1} returned a malformed result"
            continue
        if result.kind != AGENT_KIND:
            continue
        if result.result == "created":
            if identity is not None:
                return None, "more than one application was created"
            identity = _ApplicationIdentity(result.name, index, index)
            continue
        if identity is None:
            return None, "an application update preceded its create"
        if result.name != identity.name:
            return None, (f"object_apply updated {result.name!r} after creating {identity.name!r}")
        identity = _ApplicationIdentity(identity.name, identity.create_index, index)
    if identity is None:
        return None, "no successful object_apply created an application"
    return identity, None


async def _created_application(
    output: CapabilityOutput,
) -> tuple[_CreatedApplication | None, str | None]:
    identity, failure = _created_application_identity(output)
    if identity is None:
        return None, failure
    applications = await _applications(identity.name)
    if len(applications) != 1:
        return None, (
            f"created {identity.name!r} but found {len(applications)} durable application rows"
        )
    return _CreatedApplication(identity, applications[0]), None


def _asks(output: CapabilityOutput) -> tuple[int, ...]:
    return tuple(
        index
        for index, call in enumerate(output.calls)
        if call.name == "ask_user" and call.succeeded
    )


def _interviews(output: CapabilityOutput) -> tuple[int, ...]:
    """The asks that stood before the create — the form the member's submission agreed to. An ask
    after the create is the closing move the skill itself teaches (offer to attach an account) and
    counts toward nothing here."""
    applies = _agent_applies(output)
    return tuple(index for index in _asks(output) if index < applies[0][0]) if applies else ()


def _design_pass_failure(
    output: CapabilityOutput, previews: int, *, opening_asks: int
) -> str | None:
    """What is wrong with the design pass every create stands behind, or None. Both ways in run it,
    so `opening_asks` is what stands in front of it: a guided build's proposal, or nothing for a
    member who named the job. The rest is the same either way — the product renderer alone draws
    the page, each picture is followed by the choice it is asking for, and the create comes after
    the last of them."""
    applies = _agent_applies(output)
    if not applies:
        return "no application create follows the design"
    create = applies[0][0]
    asks = tuple(index for index in _asks(output) if index < create)
    preview_calls = tuple(
        (index, call)
        for index, call in enumerate(output.calls)
        if index < create and call.call == PREVIEW_TOOL and call.succeeded
    )
    delegated_previews = tuple(
        index
        for index, call in enumerate(output.calls)
        if index < create and call.name == "build_application_preview" and call.succeeded
    )
    parent_build_calls = tuple(
        call.name
        for index, call in enumerate(output.calls)
        if index < create
        and call.succeeded
        and call.name in {"bash", "read", "write", "edit", "start_server", "js_repl"}
    )
    parent_website_skill = any(
        index < create
        and call.name == "load_skill"
        and call.succeeded
        and call.input.get("name") == "website-building"
        for index, call in enumerate(output.calls)
    )
    if parent_build_calls:
        return f"the parent ran preview build tools: {', '.join(parent_build_calls)}"
    if parent_website_skill:
        return "the parent loaded website-building for preview work"
    if delegated_previews:
        return "the design preview used a model worker"
    if any(
        index < create and call.name == "share_file" and call.succeeded
        for index, call in enumerate(output.calls)
    ):
        return "the parent shared the product preview a second time"
    # The interview, one ask per picture, whatever opens the run, and one round of slack for the
    # detail the skill lets ride the form.
    settled = previews + 1 + opening_asks
    allowed_asks = {settled, settled + 1}
    if len(asks) not in allowed_asks:
        expected = " or ".join(str(count) for count in sorted(allowed_asks))
        return f"the run used {len(asks)} asks before create, expected {expected}"
    if len(preview_calls) != previews:
        return f"the run rendered {len(preview_calls)} previews, expected {previews}"
    render_indexes = tuple(index for index, _ in preview_calls)
    design_asks = tuple(
        next((ask for ask in asks if ask > render), -1) for render in render_indexes
    )
    for position, (render, ask) in enumerate(zip(render_indexes, design_asks, strict=True)):
        if ask < 0:
            return f"preview {position + 1} has no later design choice"
        if render >= ask:
            return f"preview {position + 1} was not rendered before its design choice"
        if position and design_asks[position - 1] >= render:
            return f"preview {position + 1} was not built after the prior design choice"
    for position, (_, call) in enumerate(preview_calls):
        contract = call.arguments
        required = {
            "purpose",
            "first_screen_priority",
            "regions",
            "layout",
            "design_direction",
        }
        if missing := sorted(required - contract.keys()):
            return f"preview {position + 1} contract omits {', '.join(missing)}"
    if previews > 1:
        contract = preview_calls[-1][1].arguments
        regions = contract.get("regions", [])
        priority = str(contract.get("first_screen_priority", "")).casefold()
        first_region = str(regions[0]).casefold() if isinstance(regions, list) and regions else ""
        waiting_on_member = first_region.strip() == "waiting on you"
        if "overdue" not in priority or ("overdue" not in first_region and not waiting_on_member):
            return "the revised preview contract does not put the overdue queue first"
    return None


def _accepted_design(
    output: CapabilityOutput,
) -> tuple[_AcceptedApplicationDesign | None, str | None]:
    """The last valid design preview before the one successful application create."""
    application, failure = _created_application_identity(output)
    if application is None:
        return None, failure
    previews: list[tuple[int, ToolInvocation, RenderApplicationPreviewInput]] = []
    for index, call in enumerate(output.calls[: application.create_index]):
        if call.call != PREVIEW_TOOL or not call.succeeded:
            continue
        try:
            contract = RenderApplicationPreviewInput.model_validate(call.arguments)
        except ValueError:
            continue
        previews.append((index, call, contract))
    if not previews:
        return None, "the application has no valid accepted preview before its create"
    preview_index, preview, contract = previews[-1]
    return _AcceptedApplicationDesign(application, preview_index, preview, contract), None


def _accepted_contract_failure(accepted: _AcceptedApplicationDesign, prompt: str) -> str | None:
    """Whether the design block the renderer returned reaches the application prompt. The renderer
    composes the block from the accepted contract and the skill places it, so this grader recomposes
    nothing: it renders the same block from the same contract and reads it back out of the durable
    prompt. Only the run of whitespace between words is free, because a block copied into a YAML
    scalar is free to wrap where the line ends."""
    block = " ".join(homepage_design_block(accepted.contract).split())
    if block not in " ".join(prompt.split()):
        return f"the application prompt omits the accepted design block: {block}"
    return None


async def _creation_failure(outcome: ScenarioOutcome, visibility: str) -> CapabilityVerdict | None:
    """What is wrong with the one application this conversation should have created, or None."""
    if not any(
        call.name == "load_skill" and call.succeeded and str(call.input.get("name", "")) == SKILL
        for call in outcome.output.calls
    ):
        return CapabilityVerdict(False, f"never loaded {SKILL!r}")
    applies = _agent_applies(outcome.output)
    if not applies:
        return CapabilityVerdict(False, "no successful object_apply carried an agent manifest")
    names = list(dict.fromkeys(name for _, name in applies))
    if len(names) != 1:
        return CapabilityVerdict(False, f"expected one new application, applied {names}")
    row = await _application(names[0])
    if row is None:
        return CapabilityVerdict(False, f"applied {names[0]!r} but no such application stands")
    if not _interviews(outcome.output):
        return CapabilityVerdict(False, "created the application before confirming it")
    if (row.model, row.reasoning) != (AUTO_MODEL, "auto"):
        return CapabilityVerdict(
            False, f"{row.name} runs model {row.model!r} at reasoning {row.reasoning!r}"
        )
    if row.visibility != visibility:
        return CapabilityVerdict(
            False, f"{row.name} is {row.visibility!r}, the member asked for {visibility!r}"
        )
    if not row.prompt.strip():
        return CapabilityVerdict(False, f"{row.name} has an empty prompt")
    return None


async def _prepare_created_homepage(
    outcome: ScenarioOutcome, target: CapabilityTarget
) -> tuple[_ApplicationRecord, UUID]:
    created, failure = await _created_application(outcome.output)
    if created is None:
        raise RuntimeError(failure or "homepage followup found no created application")
    application = created.application
    if application.owner_member_id is None:
        raise RuntimeError("created application has no owner for its homepage turn")
    await ScopedStore(extension=WEB_EXTENSION).put(
        f"{HOMEPAGE_SEED_PREFIX}{application.id}", str(application.owner_member_id)
    )
    key = f"homepage/{application.id}/{application.owner_member_id}"
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent)
            .where(
                tables.agent.c.workspace_id == ws_current().workspace_id,
                tables.agent.c.id == application.id,
            )
            .values(tools=[APPLICATION_BUILDER_DELEGATION.canonical_id])
        )
        insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
        await connection.execute(
            insert(tables.conversation)
            .values(
                id=uuid4(),
                workspace_id=ws_current().workspace_id,
                agent_id=application.id,
                surface=WEB_EXTENSION,
                queue_key=key,
                member_id=application.owner_member_id,
                audience=str(conversation_audience(application.owner_member_id)),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
            .on_conflict_do_nothing(
                index_elements=[
                    tables.conversation.c.workspace_id,
                    tables.conversation.c.surface,
                    tables.conversation.c.queue_key,
                ]
            )
        )
        conversation_id = (
            await connection.execute(
                sa.select(tables.conversation.c.id).where(
                    tables.conversation.c.workspace_id == ws_current().workspace_id,
                    tables.conversation.c.surface == WEB_EXTENSION,
                    tables.conversation.c.queue_key == key,
                )
            )
        ).scalar_one()
    workspace = target.conversations.workspace_path(conversation_id, "")
    await asyncio.to_thread(workspace.mkdir, parents=True, exist_ok=True)
    for item in APP_WORKSPACE_FILES:
        path = workspace / item.path
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, item.content)
    await _sync_active_application_workspace(conversation_id, workspace / "ufo-app")
    return application, conversation_id


async def _sync_active_application_workspace(conversation_id: UUID, source: Path) -> None:
    container = f"{SANDBOX_CONTAINER_PREFIX}{conversation_id}"
    inspect = await asyncio.create_subprocess_exec(
        "docker",
        "inspect",
        container,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await inspect.communicate()
    if inspect.returncode != 0:
        return
    copied = await asyncio.create_subprocess_exec(
        "docker",
        "cp",
        f"{source}/.",
        f"{container}:/workspace/ufo-app",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await copied.communicate()
    if copied.returncode != 0:
        raise RuntimeError(stderr.decode().strip() or "active application workspace sync failed")


async def _build_created_homepage(outcome: ScenarioOutcome, target: CapabilityTarget):
    application, conversation_id = await _prepare_created_homepage(outcome, target)
    return await target.invoke(
        conversation_id,
        application.id,
        SEED_PROMPT,
        f"homepage-seed:{application.id}:{datetime.now(UTC).date().isoformat()}",
        on_behalf_of_member_id=application.owner_member_id,
        as_scheduled=True,
    )


async def _repair_created_homepage(outcome: ScenarioOutcome, target: CapabilityTarget):
    application, conversation_id = await _prepare_created_homepage(outcome, target)
    first = await target.invoke(
        conversation_id,
        application.id,
        SEED_PROMPT,
        f"homepage-seed:{application.id}:without-authority",
        on_behalf_of_member_id=None,
        as_scheduled=True,
    )
    workspace = target.conversations.workspace_path(conversation_id, "")
    source_path = workspace / APPLICATION_SOURCE_PATH.removeprefix("/workspace/")
    source_digest = ""
    if await asyncio.to_thread(source_path.is_file):
        source_digest = sha256(await asyncio.to_thread(source_path.read_bytes)).hexdigest()
    async with workspace_tx() as connection:
        homepage = (
            await connection.execute(
                sa.select(hosted_site.c.name).where(
                    hosted_site.c.workspace_id == ws_current().workspace_id,
                    hosted_site.c.homepage_agent_id == application.id,
                )
            )
        ).one_or_none()
    evidence = ScopedStore(extension=REPAIR_EVIDENCE_EXTENSION)
    await evidence.put(REPAIR_SOURCE_KEY.format(application_id=application.id), source_digest)
    await evidence.put(
        REPAIR_BOUND_KEY.format(application_id=application.id),
        "yes" if homepage is not None else "no",
    )
    second = await target.invoke(
        conversation_id,
        application.id,
        SEED_PROMPT,
        f"homepage-seed:{application.id}:with-authority",
        on_behalf_of_member_id=application.owner_member_id,
        as_scheduled=True,
    )
    return first, second


def _built_design_failure(
    calls: tuple[ToolInvocation, ...],
    accepted: RenderApplicationPreviewInput,
) -> str | None:
    """Whether the rendered worker design keeps the accepted region identities, display order,
    first-screen priority, and layout."""
    designs = tuple(
        call for call in calls if call.name == APPLICATION_BUILDER_DESIGN_TOOL and call.succeeded
    )
    if not designs:
        return "the worker accepted no design"
    design = designs[-1]

    def terms(value: str) -> frozenset[str]:
        words = "".join(
            character if character.isalnum() else " " for character in value.casefold()
        ).split()
        variants = set(words) - {"s"}
        for word in words:
            if len(word) > 3 and word.endswith("ies"):
                variants.add(f"{word[:-3]}y")
            elif len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
                variants.add(word[:-1])
        return frozenset(variants)

    try:
        payload = json.loads(design.result)
        page_height = int(payload.get("page_height", APPLICATION_DESIGN_FOLD))
        if not APPLICATION_DESIGN_FOLD <= page_height <= APPLICATION_DESIGN_MAX_HEIGHT:
            raise ValueError("the design page height is outside the accepted range")
        rendered = tuple(
            ApplicationAuditRegion.model_validate(region) for region in payload["rendered_regions"]
        )
    except (KeyError, TypeError, ValueError):
        return "the worker design has no rendered region measurements"
    rendered_names = tuple(region.name for region in rendered)
    if len(rendered_names) != len(accepted.regions):
        return (
            f"the worker rendered {len(rendered_names)} regions for the "
            f"{len(accepted.regions)} the member accepted: {', '.join(rendered_names)}"
        )
    if any(not name.strip() for name in rendered_names):
        return "the worker rendered an empty region identity"
    if len(rendered_names) != len(set(rendered_names)):
        return "the worker rendered duplicate region identities"
    count = len(rendered)
    accepted_identity_terms = []
    for name in accepted.regions:
        name_terms = terms(name)
        accepted_identity_terms.append(name_terms - REGION_IDENTITY_CONNECTIVES or name_terms)
    rendered_identity_terms = tuple(
        terms(f"{region.name} {region.visible_text}") for region in rendered
    )
    overlap_scores = tuple(
        tuple(len(accepted_terms & rendered_terms) for rendered_terms in rendered_identity_terms)
        for accepted_terms in accepted_identity_terms
    )
    diagonal_scores = tuple(overlap_scores[index][index] for index in range(count))
    if 0 in diagonal_scores:
        index = diagonal_scores.index(0)
        return (
            f"rendered region {index + 1} does not carry accepted identity "
            f"{accepted.regions[index]!r}: {rendered[index].name}"
        )
    identity_score = sum(diagonal_scores)
    assignment_scores = tuple(
        sum(
            overlap_scores[index][rendered_index] for index, rendered_index in enumerate(assignment)
        )
        for assignment in permutations(range(count))
    )
    best_score = max(assignment_scores)
    if identity_score < best_score:
        return "the rendered semantic region order differs from the accepted design"
    if assignment_scores.count(best_score) != 1:
        return "the rendered region identities are semantically ambiguous"
    if not any(character.isalnum() for character in rendered[0].visible_text):
        return "the first rendered region has no material visible content"
    match accepted.layout:
        case "timeline":
            expected_boxes = tuple((0.0, index / count, 1.0, 1.0 / count) for index in range(count))
        case "queue-detail":
            side_count = count - 1
            expected_boxes = (
                (0.0, 0.0, 0.6, 1.0),
                *tuple(
                    (0.6, index / side_count, 0.4, 1.0 / side_count) for index in range(side_count)
                ),
            )
        case "metrics" | "summary-detail":
            columns = 3 if accepted.layout == "metrics" else 2
            rows = (count + columns - 1) // columns
            expected_boxes = tuple(
                (
                    (index % columns) / columns,
                    (index // columns) / rows,
                    1.0 / columns,
                    1.0 / rows,
                )
                for index in range(count)
            )
    expected = tuple(
        ApplicationAuditRegion(
            name=name,
            left=box[0],
            top=box[1],
            width=box[2],
            height=box[3],
        )
        for name, box in zip(rendered_names, expected_boxes, strict=True)
    )
    for first_index, first_region in enumerate(rendered):
        for second_index in range(first_index + 1, count):
            actual_relation = application_region_relation(
                first_region, rendered[second_index], page_height
            )
            expected_relation = application_region_relation(
                expected[first_index], expected[second_index], APPLICATION_DESIGN_FOLD
            )
            if actual_relation != expected_relation:
                return (
                    f"the rendered {accepted.layout} layout changes the accepted relation for "
                    f"{first_region.name} and {rendered[second_index].name}"
                )
    widths = tuple(region.width for region in rendered)
    heights = tuple(region.height for region in rendered)
    match accepted.layout:
        case "queue-detail":
            side = rendered[1:]
            if rendered[0].width < max(region.width for region in side) * 1.25:
                return "the rendered queue-detail first region is not wider than its detail column"
            if min(region.width for region in side) < max(region.width for region in side) * 0.7:
                return "the rendered queue-detail detail regions have inconsistent widths"
            first_bottom = rendered[0].top + rendered[0].height
            side_bottom = max(region.top + region.height for region in side)
            if (
                rendered[0].top > min(region.top for region in side) + 0.02
                or first_bottom + 0.02 < side_bottom
            ):
                return "the rendered queue-detail first region does not span its detail column"
        case "timeline":
            if min(widths) < max(widths) * 0.7:
                return "the rendered timeline regions have inconsistent widths"
            if min(heights) < max(heights) * 0.6:
                return "the rendered timeline regions have inconsistent heights"
        case "metrics" | "summary-detail":
            if min(widths) < max(widths) * 0.7:
                return f"the rendered {accepted.layout} regions have inconsistent widths"
            if min(heights) < max(heights) * 0.6:
                return f"the rendered {accepted.layout} regions have inconsistent heights"
    return None


def _application_worker_tool_failure(calls: tuple[ToolInvocation, ...]) -> str | None:
    completed = frozenset(call.name for call in calls if call.succeeded)
    required = {
        APPLICATION_BUILDER_DEPLOY_TOOL,
        APPLICATION_BUILDER_DESIGN_TOOL,
        APPLICATION_BUILDER_QA_TOOL,
    }
    if missing := sorted(required - completed):
        return f"the Gemini worker did not complete: {', '.join(missing)}"
    if not any(call.name == APPLICATION_BUILDER_WRITE_TOOL for call in calls):
        return f"the Gemini worker did not call {APPLICATION_BUILDER_WRITE_TOOL}"
    if not any(call.name == APPLICATION_BUILDER_QA_TOOL and call.succeeded for call in calls):
        return "the Gemini worker completed no product QA batch"
    if any(call.call == HOMEPAGE_ACTION for call in calls):
        return "the Gemini worker tried to certify its own homepage"
    return None


async def _homepage_journey_failure(
    outcome: ScenarioOutcome, accepted: _AcceptedApplicationDesign
) -> str | None:
    followup = outcome.followup
    if followup is None:
        return "the created application ran no homepage turn"
    delegations = tuple(call for call in followup.own_calls if call.call == BUILD_ACTION)
    if not delegations or any(not call.succeeded for call in delegations):
        return f"the application made {len(delegations)} successful-or-failed build call(s)"
    if len({call.result for call in delegations}) != 1:
        return "the application parent received inconsistent cached build results"
    try:
        result = ApplicationBuilderResult.model_validate_json(delegations[0].result)
    except ValueError:
        return "the application parent received no structured build result"
    if result.status != "deployed":
        return f"the deterministic acceptance result was {result.status}: {result.blocker}"
    forbidden = {
        "list_external_tools",
        "describe_external_tools",
        "search_connector_tools",
        "call_external_tool",
        "read",
        "bash",
        "start_server",
        "js_repl",
        APPLICATION_BUILDER_WRITE_TOOL,
        "edit_application_source",
        "read_application_source",
        "action:site:deploy_website",
        HOMEPAGE_ACTION,
        "action:site:build_website",
        "write",
        "edit",
    }
    parent_work = tuple(call.call for call in followup.own_calls if call.call in forbidden)
    if parent_work:
        return f"the Opus application parent entered the build loop: {', '.join(parent_work)}"
    if failure := _application_worker_tool_failure(followup.calls):
        return failure
    created, failure = await _created_application(outcome.output)
    if created is None:
        return failure or "no durable application exists"
    application = created.application
    async with workspace_tx() as connection:
        homepage = (
            await connection.execute(
                sa.select(hosted_site.c.source_manifest).where(
                    hosted_site.c.workspace_id == ws_current().workspace_id,
                    hosted_site.c.homepage_agent_id == application.id,
                )
            )
        ).one_or_none()
        turns = (
            await connection.execute(
                sa.select(
                    tables.turn.c.parent_turn_id,
                    tables.turn.c.subagent_profile,
                    tables.turn.c.inbound,
                    tables.turn.c.status,
                ).where(
                    tables.turn.c.workspace_id == ws_current().workspace_id,
                    tables.turn.c.agent_id == application.id,
                )
            )
        ).all()
    if homepage is None or not homepage.source_manifest:
        return "the application has no bound homepage with retained source"
    parents = tuple(turn for turn in turns if turn.parent_turn_id is None)
    children = tuple(turn for turn in turns if turn.parent_turn_id is not None)
    if len(parents) != 1 or parents[0].inbound != SEED_PROMPT or parents[0].status != "done":
        return "the application did not run one successful scheduled homepage parent turn"
    if (
        len(children) != 1
        or children[0].subagent_profile != APPLICATION_BUILDER_NAME
        or children[0].status != "done"
    ):
        return "the homepage parent did not run one successful Gemini builder child"
    task = ApplicationBuilderTask.model_validate_json(children[0].inbound)
    if application.prompt not in task.objective:
        return "the Gemini task omitted the created application's instructions"
    return _built_design_failure(followup.calls, accepted.contract)


async def _named_design_failure(
    outcome: ScenarioOutcome,
) -> tuple[str | None, _AcceptedApplicationDesign | None]:
    """The design pass on the path that opens with the member's own words: one preview and the
    choice it asks for stand between the answered form and the create, and the contract they agreed
    to is in the application's prompt. Nothing opens this run, so the interview and the design are
    the whole of what is asked."""
    design_failure = _design_pass_failure(outcome.output, 1, opening_asks=0)
    if design_failure is not None:
        return design_failure, None
    name = _agent_applies(outcome.output)[0][1]
    row = await _application(name)
    if row is None:
        return f"applied {name!r} but no such application stands", None
    accepted, failure = _accepted_design(outcome.output)
    if accepted is None:
        return failure or "the application has no accepted design", None
    return _accepted_contract_failure(accepted, row.prompt), accepted


async def _graded_shows_the_design(outcome: ScenarioOutcome) -> CapabilityVerdict:
    """The design gate, and nothing else: the skill that owns the interview loaded, and the member
    saw the page before an application existed.

    An `agent` object cannot be undone, so the preview is the one moment a member can look at what
    they are about to make while looking is still free. Whether they got that moment is a fact about
    the trajectory — a preview stands before any apply, and an ask stands after it — so no judge and
    no rubric decide it. The graders above measure the whole creation product, so a miss anywhere in
    a reply takes their sample down; that says nothing about whether this member got to look. This
    case grades the gate alone."""
    calls = outcome.output.calls
    if not any(
        call.name == "load_skill" and call.succeeded and str(call.input.get("name", "")) == SKILL
        for call in calls
    ):
        return CapabilityVerdict(False, f"never loaded {SKILL!r}")
    previews = tuple(
        index for index, call in enumerate(calls) if call.call == PREVIEW_TOOL and call.succeeded
    )
    if not previews:
        return CapabilityVerdict(False, "rendered no design, so the member saw nothing")
    applies = _agent_applies(outcome.output)
    if applies and previews[0] > applies[0][0]:
        return CapabilityVerdict(False, "created the application before showing a design")
    if not any(index > previews[-1] for index in _asks(outcome.output)):
        return CapabilityVerdict(False, "showed the design and asked the member nothing")
    return CapabilityVerdict(
        True,
        f"the skill loaded and {len(previews)} design(s) stood before the create",
    )


async def _graded_carries_the_design(outcome: ScenarioOutcome) -> CapabilityVerdict:
    """The accepted design reaching the application, and nothing else: the renderer composed a
    block, and the durable prompt carries it.

    The worker implements the `Homepage design` it reads in the application's own instructions, so
    a design that is accepted and then not carried is a page the member never chose. Whether it
    was carried is a fact about the contract and the row — no judge and no rubric decide it. The
    creating graders above measure the whole product, so a miss anywhere in a reply takes their
    sample down; that says nothing about whether the accepted design survived the create."""
    applies = _agent_applies(outcome.output)
    if not applies:
        return CapabilityVerdict(False, "no successful object_apply carried an agent manifest")
    name = applies[0][1]
    row = await _application(name)
    if row is None:
        return CapabilityVerdict(False, f"applied {name!r} but no such application stands")
    accepted, failure = _accepted_design(outcome.output)
    if accepted is None:
        return CapabilityVerdict(False, failure or "the application has no accepted design")
    failure = _accepted_contract_failure(accepted, row.prompt)
    if failure is not None:
        return CapabilityVerdict(False, failure)
    return CapabilityVerdict(True, f"{name} carries the design block the member accepted")


async def _graded_support_desk(outcome: ScenarioOutcome) -> CapabilityVerdict:
    failure = await _creation_failure(outcome, "workspace")
    if failure is not None:
        return failure
    named_failure, _ = await _named_design_failure(outcome)
    if named_failure is not None:
        return CapabilityVerdict(False, named_failure)
    return CapabilityVerdict(
        True, "one workspace application created from one form and one accepted design"
    )


async def _graded_stated_up_front(outcome: ScenarioOutcome) -> CapabilityVerdict:
    failure = await _creation_failure(outcome, "private")
    if failure is not None:
        return failure
    named_failure, _ = await _named_design_failure(outcome)
    if named_failure is not None:
        return CapabilityVerdict(False, named_failure)
    return CapabilityVerdict(
        True, "one private application created from one prefilled form and one accepted design"
    )


async def _graded_existing_untouched(outcome: ScenarioOutcome) -> CapabilityVerdict:
    fixture = await _fixture_row()
    if fixture is None:
        return CapabilityVerdict(False, f"{EXISTING_APPLICATION!r} is gone")
    if fixture.prompt != EXISTING_PROMPT:
        return CapabilityVerdict(False, f"{EXISTING_APPLICATION} was rewritten")
    if applies := _agent_applies(outcome.output):
        return CapabilityVerdict(False, f"applied {len(applies)} agent manifest(s) to a wiring ask")
    return CapabilityVerdict(True, f"{EXISTING_APPLICATION} survived unchanged")


async def _graded_daily_brief(outcome: ScenarioOutcome) -> CapabilityVerdict:
    failure = await _creation_failure(outcome, "private")
    if failure is not None:
        return failure
    named_failure, _ = await _named_design_failure(outcome)
    if named_failure is not None:
        return CapabilityVerdict(False, named_failure)
    apply = _agent_applies(outcome.output)[0]
    row = await _application(apply[1])
    if row is None:
        return CapabilityVerdict(False, "the applied Daily Brief application is missing")
    prompt = row.prompt.lower()
    required = (
        "daily-brief",
        "configure_daily_brief",
        "sweep_newspaper",
        "scheduled",
        "markdown",
        "share_file",
        "homepage",
    )
    missing = tuple(value for value in required if value not in prompt)
    if missing:
        return CapabilityVerdict(False, f"application prompt omits {', '.join(missing)}")
    return CapabilityVerdict(True, "one private Daily Brief application holds the complete job")


async def _guided_design_grade(
    outcome: ScenarioOutcome, *, revisions: int
) -> tuple[CapabilityVerdict, _AcceptedApplicationDesign | None]:
    failure = await _creation_failure(outcome, "private")
    if failure is not None:
        return failure, None
    design_failure = _design_pass_failure(outcome.output, revisions + 1, opening_asks=1)
    if design_failure is not None:
        return CapabilityVerdict(False, design_failure), None
    name = _agent_applies(outcome.output)[0][1]
    row = await _application(name)
    if row is None:
        return CapabilityVerdict(False, f"applied {name!r} but no such application stands"), None
    accepted, accepted_failure = _accepted_design(outcome.output)
    if accepted is None:
        return (
            CapabilityVerdict(False, accepted_failure or "the application has no accepted design"),
            None,
        )
    contract_failure = _accepted_contract_failure(accepted, row.prompt)
    if contract_failure is not None:
        return CapabilityVerdict(False, contract_failure), None
    if revisions:
        prompt = row.prompt.casefold()
        if "overdue" not in prompt or "queue" not in prompt:
            return (
                CapabilityVerdict(
                    False, "the accepted overdue-queue revision is absent from prompt"
                ),
                None,
            )
        reason = "the second preview and its accepted revision precede create"
    else:
        reason = "one proposal, interview, and preview precede the create"
    return CapabilityVerdict(True, reason), accepted


async def _graded_guided_build(outcome: ScenarioOutcome) -> CapabilityVerdict:
    verdict, _ = await _guided_design_grade(outcome, revisions=0)
    return verdict


async def _graded_guided_revision(outcome: ScenarioOutcome) -> CapabilityVerdict:
    verdict, _ = await _guided_design_grade(outcome, revisions=1)
    return verdict


async def _graded_named_journey(outcome: ScenarioOutcome) -> CapabilityVerdict:
    failure = await _creation_failure(outcome, "private")
    if failure is not None:
        return failure
    named_failure, accepted = await _named_design_failure(outcome)
    if named_failure is not None:
        return CapabilityVerdict(False, named_failure)
    if accepted is None:
        return CapabilityVerdict(False, "the application has no accepted design")
    journey_failure = await _homepage_journey_failure(outcome, accepted)
    if journey_failure is not None:
        return CapabilityVerdict(False, journey_failure)
    return CapabilityVerdict(
        True,
        "one approval creates the application; one parent route and one Gemini build bind its page",
    )


async def _graded_guided_journey(outcome: ScenarioOutcome) -> CapabilityVerdict:
    creation, accepted = await _guided_design_grade(outcome, revisions=0)
    if not creation.passed:
        return creation
    if accepted is None:
        return CapabilityVerdict(False, "the application has no accepted design")
    journey_failure = await _homepage_journey_failure(outcome, accepted)
    if journey_failure is not None:
        return CapabilityVerdict(False, journey_failure)
    return CapabilityVerdict(
        True,
        "one proposal, interview, preview, parent route, and Gemini build produce one homepage",
    )


async def _graded_guided_revision_journey(outcome: ScenarioOutcome) -> CapabilityVerdict:
    creation, accepted = await _guided_design_grade(outcome, revisions=1)
    if not creation.passed:
        return creation
    if accepted is None:
        return CapabilityVerdict(False, "the application has no accepted design")
    journey_failure = await _homepage_journey_failure(outcome, accepted)
    if journey_failure is not None:
        return CapabilityVerdict(False, journey_failure)
    return CapabilityVerdict(
        True,
        "one revised preview, parent route, and Gemini build produce one homepage",
    )


def _application_repair_tool_failure(
    first: CapabilityOutput, second: CapabilityOutput
) -> str | None:
    if not any(call.name == APPLICATION_BUILDER_WRITE_TOOL for call in first.calls):
        return "the failed attempt made no initial source write"
    if any(call.name == APPLICATION_BUILDER_DEPLOY_TOOL and call.succeeded for call in first.calls):
        return "the failed attempt deployed a site"
    if not any(
        call.name == APPLICATION_BUILDER_DEPLOY_TOOL and call.succeeded for call in second.calls
    ):
        return "the repair attempt deployed no site"
    return None


async def _graded_repair_journey(outcome: ScenarioOutcome) -> CapabilityVerdict:
    failure = await _creation_failure(outcome, "private")
    if failure is not None:
        return failure
    if len(outcome.followups) != 2:
        return CapabilityVerdict(False, f"the journey retained {len(outcome.followups)} build(s)")
    build_statuses: list[str] = []
    for followup in outcome.followups:
        calls = tuple(
            call for call in followup.own_calls if call.call == BUILD_ACTION and call.succeeded
        )
        if not calls:
            return CapabilityVerdict(
                False,
                "an application parent made no successful build call",
            )
        results = {call.result for call in calls}
        if len(results) != 1:
            return CapabilityVerdict(False, "an application parent received inconsistent results")
        result = yaml.safe_load(results.pop())
        if not isinstance(result, dict) or not isinstance(result.get("status"), str):
            return CapabilityVerdict(False, "a build delegation returned no status")
        build_statuses.append(result["status"])
    if build_statuses != ["blocked", "deployed"]:
        return CapabilityVerdict(False, f"build statuses were {build_statuses}")
    if any(call.call == HOMEPAGE_ACTION for call in outcome.output.calls):
        return CapabilityVerdict(False, "a Gemini worker tried to certify its own homepage")
    first, second = outcome.followups
    if repair_failure := _application_repair_tool_failure(first, second):
        return CapabilityVerdict(False, repair_failure)
    name = _agent_applies(outcome.output)[0][1]
    async with workspace_tx() as connection:
        application = (
            await connection.execute(
                sa.select(tables.agent.c.id).where(
                    tables.agent.c.workspace_id == ws_current().workspace_id,
                    tables.agent.c.name == name,
                )
            )
        ).one()
        homepage = (
            await connection.execute(
                sa.select(hosted_site.c.source_manifest).where(
                    hosted_site.c.workspace_id == ws_current().workspace_id,
                    hosted_site.c.homepage_agent_id == application.id,
                )
            )
        ).one_or_none()
        turns = (
            await connection.execute(
                sa.select(tables.turn.c.parent_turn_id, tables.turn.c.subagent_profile).where(
                    tables.turn.c.workspace_id == ws_current().workspace_id,
                    tables.turn.c.agent_id == application.id,
                )
            )
        ).all()
    evidence = ScopedStore(extension=REPAIR_EVIDENCE_EXTENSION)
    first_source = await evidence.get(REPAIR_SOURCE_KEY.format(application_id=application.id))
    first_bound = await evidence.get(REPAIR_BOUND_KEY.format(application_id=application.id))
    if not isinstance(first_source, str) or len(first_source) != 64:
        return CapabilityVerdict(False, "the failed attempt retained no source digest")
    if first_bound != "no":
        return CapabilityVerdict(False, "the failed attempt bound a homepage")
    if homepage is None or not homepage.source_manifest:
        return CapabilityVerdict(False, "the repair attempt bound no retained homepage")
    parents = tuple(turn for turn in turns if turn.parent_turn_id is None)
    children = tuple(turn for turn in turns if turn.parent_turn_id is not None)
    if len(parents) != 2 or len(children) != 2:
        return CapabilityVerdict(
            False, f"the repair used {len(parents)} parent(s) and {len(children)} worker(s)"
        )
    if any(turn.subagent_profile != APPLICATION_BUILDER_NAME for turn in children):
        return CapabilityVerdict(False, "a repair worker used the wrong profile")
    return CapabilityVerdict(
        True,
        "one blocked build retains evidence; one later Gemini build binds the same application",
    )


SCENARIOS = (
    ScenarioCase(
        "A01-support-desk",
        ScenarioUser(
            reason_for_call="You want the support team to have an application of its own that "
            "answers the common product questions and passes anything else to a person.",
            known_info="The whole support team uses it, not just you.",
            task_instructions=SATISFIED_INSTRUCTION,
        ),
        DescribedGrader(
            "one workspace-visible application lands on auto model and auto reasoning, written "
            "only after the form comes back and the member accepts the design shown to them",
            _graded_support_desk,
        ),
        seed=_seeded(),
        rubric=(
            "The assistant proposed the name and the instructions itself rather than asking the "
            "member to supply them.",
            "The assistant never asks the member how much the application may do on its own, or "
            "when it should run.",
            "Before the application is created, the assistant states the boundary it wrote: the "
            "application handles the routine work of its job itself and brings anything unusual to "
            "a person.",
            "After creating it, the assistant says the new application holds no connected "
            "accounts, credentials, sources, skills, or memory yet, and offers to attach an "
            "account the workspace already has.",
            "The assistant never offers to delete the application or undo the create.",
        ),
        digest_tag="new-application:actions:support-desk",
    ),
    ScenarioCase(
        "A02-stated-up-front",
        ScenarioUser(
            reason_for_call="You want a private application of your own that reads the invoices "
            "landing in your shared inbox, files the totals, and asks you before anything "
            "unusual. Nobody else should see it.",
            task_instructions="Say all of that in your first message. Approve what the assistant "
            "proposes. If it asks something you already told it, answer briefly and say you "
            "already said so. End the conversation once the application exists.",
        ),
        DescribedGrader(
            "the member who stated the job up front is still asked once, prefilled, sees the "
            "design before anything is created, and gets a private application on auto model and "
            "auto reasoning",
            _graded_stated_up_front,
        ),
        seed=_seeded(),
        rubric=(
            "The assistant never asks the member how much the application may do on its own, or "
            "when it should run.",
        ),
        digest_tag="new-application:actions:stated-up-front",
    ),
    ScenarioCase(
        "A03-existing-application",
        ScenarioUser(
            reason_for_call=f"The {EXISTING_APPLICATION} application you already have cannot see "
            "the shared inbox, and you want it hooked up.",
            known_info=f"It is called {EXISTING_APPLICATION} and it already exists.",
            task_instructions="You do not want a new application; you want the one you have to "
            "work. End the conversation once the assistant has explained what it needs from you.",
        ),
        DescribedGrader(
            f"{EXISTING_APPLICATION} survives unchanged and no agent manifest is applied",
            _graded_existing_untouched,
        ),
        seed=_seeded(EXISTING_APPLICATION),
        rubric=(
            "The assistant never creates a second application and never claims to have created "
            "one.",
        ),
        digest_tag="new-application:actions:existing-application",
    ),
    ScenarioCase(
        "A04-daily-brief",
        ScenarioUser(
            reason_for_call="You want a private Daily Brief application that reviews your work "
            "each weekday morning, publishes each edition in Radar, and keeps its homepage "
            "current.",
            known_info="Only you use it. Sweep is installed.",
            task_instructions=SATISFIED_INSTRUCTION,
        ),
        DescribedGrader(
            "one private application prompt carries the Daily Brief skill, Sweep tool, scheduled "
            "Markdown publication, and homepage job",
            _graded_daily_brief,
        ),
        seed=_seeded(),
        rubric=(
            "The assistant creates an ordinary member-owned application, not an extension-owned "
            "or provisioned agent.",
            "The application registers its private conversation before it creates the recurring "
            "task.",
            "The assistant says scheduling happens in the new application's own conversation, "
            "where one recurring task keeps reporting.",
            "The application prompt keeps task and memory suggestions as drafts until the member "
            "approves them in a later turn.",
        ),
        digest_tag="new-application:actions:daily-brief",
    ),
    ScenarioCase(
        "A05-guided-build",
        ScenarioUser(
            reason_for_call="You opened New application without deciding what job it should do.",
            task_instructions=GUIDED_INSTRUCTION,
        ),
        DescribedGrader(
            "one proposal, one standard interview, and one shared design preview precede the "
            "private application create",
            _graded_guided_build,
        ),
        seed=_seeded(),
        rubric=(
            "The assistant proposes one concrete application instead of asking the member what "
            "to build.",
        ),
        digest_tag="new-application:actions:guided-build",
    ),
    ScenarioCase(
        "A06-guided-revision",
        ScenarioUser(
            reason_for_call="You opened New application without deciding what job it should do.",
            task_instructions=GUIDED_REVISION_INSTRUCTION,
        ),
        DescribedGrader(
            "two ordered design previews precede the create and the accepted overdue-queue "
            "revision reaches the durable application prompt",
            _graded_guided_revision,
        ),
        seed=_seeded(),
        rubric=(
            "The assistant changes the existing design instead of starting the interview again.",
            "The assistant creates nothing before the member accepts the revised design.",
        ),
        digest_tag="new-application:actions:guided-revision",
    ),
    ScenarioCase(
        "A07-named-homepage-journey",
        ScenarioUser(
            reason_for_call="You want a private application that prepares one compact brief before "
            "each customer call from calendar and email context.",
            known_info="Only you use it.",
            task_instructions=(
                f"{SATISFIED_INSTRUCTION} If asked whether to connect accounts first, choose to "
                "create the application now and attach the accounts later. Do not wait for a "
                "connection."
            ),
        ),
        DescribedGrader(
            "one approved application runs one parent routing turn and one complete Gemini "
            "homepage build, then holds one bound page with retained source",
            _graded_named_journey,
        ),
        seed=_seeded(),
        rubric=(
            "The creation reply states what the application still needs and does not claim that "
            "the homepage build was verified by Opus.",
        ),
        digest_tag="new-application:actions:named-homepage-journey",
        followup=_build_created_homepage,
        artifact_probe=APPLICATION_HOMEPAGE_ARTIFACTS,
    ),
    ScenarioCase(
        "A08-guided-homepage-journey",
        ScenarioUser(
            reason_for_call="You opened New application without deciding what job it should do.",
            task_instructions=GUIDED_INSTRUCTION,
        ),
        DescribedGrader(
            "one proposal, one standard interview, one shared preview, one parent route, and one "
            "Gemini build produce a bound homepage with retained source",
            _graded_guided_journey,
        ),
        seed=_seeded(),
        rubric=(
            "The assistant proposes one concrete application instead of asking the member what "
            "to build.",
            "No parent turn claims to inspect or verify the Gemini build.",
        ),
        digest_tag="new-application:actions:guided-homepage-journey",
        followup=_build_created_homepage,
        artifact_probe=APPLICATION_HOMEPAGE_ARTIFACTS,
    ),
    ScenarioCase(
        "A09-revised-homepage-journey",
        ScenarioUser(
            reason_for_call="You opened New application without deciding what job it should do.",
            task_instructions=GUIDED_REVISION_INSTRUCTION,
        ),
        DescribedGrader(
            "two ordered previews carry the accepted revision into one application, one parent "
            "route, and one Gemini-built bound homepage with retained source",
            _graded_guided_revision_journey,
        ),
        seed=_seeded(),
        rubric=(
            "The assistant changes the existing design instead of starting the interview again.",
            "No parent turn claims to inspect or verify the Gemini build.",
        ),
        digest_tag="new-application:actions:revised-homepage-journey",
        followup=_build_created_homepage,
        artifact_probe=APPLICATION_HOMEPAGE_ARTIFACTS,
    ),
    ScenarioCase(
        "A10-failed-repaired-homepage-journey",
        ScenarioUser(
            reason_for_call="You want a private application that prepares one compact brief before "
            "each customer call from calendar and email context.",
            known_info="Only you use it.",
            task_instructions=(
                f"{SATISFIED_INSTRUCTION} If asked whether to connect accounts first, choose to "
                "create the application now and attach the accounts later. Do not wait for a "
                "connection."
            ),
        ),
        DescribedGrader(
            "one blocked build retains its source and evidence without binding; one later Gemini "
            "build repairs the same application and binds its first retained homepage",
            _graded_repair_journey,
        ),
        # Three member messages reach the create on this path: the request, the answered form, and
        # `Build it` on the design. The trial sends one message per turn, so a smaller cap ends the
        # conversation on the design ask, creates nothing, and the followup raises rather than
        # grading the repair this case exists for.
        max_turns=3,
        seed=_seeded(),
        rubric=(
            "The creation reply states what the application still needs.",
            "No parent turn claims to inspect or verify either Gemini build.",
        ),
        digest_tag="new-application:actions:failed-repaired-homepage-journey",
        followup=_repair_created_homepage,
        artifact_probe=APPLICATION_HOMEPAGE_ARTIFACTS,
    ),
    ScenarioCase(
        "A11-named-shows-the-design",
        ScenarioUser(
            reason_for_call="You want a private application that reads the invoices in your shared "
            "inbox and files the totals.",
            known_info="Only you use it.",
            task_instructions=SATISFIED_INSTRUCTION,
        ),
        DescribedGrader(
            "the member who named the job loads the skill and sees the homepage design before any "
            "application exists",
            _graded_shows_the_design,
        ),
        seed=_seeded(),
        digest_tag="new-application:actions:named-shows-the-design",
    ),
    ScenarioCase(
        "A12-guided-shows-the-design",
        ScenarioUser(
            reason_for_call="You pressed New application in the portal and want the assistant to "
            "propose something.",
            task_instructions=GUIDED_INSTRUCTION,
        ),
        DescribedGrader(
            "the guided build loads the skill and shows the homepage design before any application "
            "exists",
            _graded_shows_the_design,
        ),
        seed=_seeded(),
        digest_tag="new-application:actions:guided-shows-the-design",
    ),
    ScenarioCase(
        "A13-carries-the-design",
        ScenarioUser(
            reason_for_call="You want a private application that reads the invoices in your shared "
            "inbox and files the totals.",
            known_info="Only you use it.",
            task_instructions=SATISFIED_INSTRUCTION,
        ),
        DescribedGrader(
            "the design block the member accepted reaches the created application's instructions",
            _graded_carries_the_design,
        ),
        seed=_seeded(),
        digest_tag="new-application:carries-the-design",
    ),
)
