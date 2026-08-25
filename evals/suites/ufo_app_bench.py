"""Natural app requests, connected source fixtures, and post-turn browser proof."""

import asyncio
import base64
import fcntl
import json
import os
import re
import shlex
import tempfile
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, model_validator
from ufo_ext_eval_env.manifest import (
    ACCOUNT_ID,
    APP_FIXTURE_PREFIX,
    CALENDAR_HOST,
    CALENDAR_PROVIDER,
    DRIVE_HOST,
    DRIVE_PROVIDER,
    EMAIL_HOST,
    EMAIL_PROVIDER,
    GITHUB_HOST,
    GITHUB_PROVIDER,
    GREENHOUSE_HOST,
    GREENHOUSE_PROVIDER,
    HUBSPOT_HOST,
    HUBSPOT_PROVIDER,
    STRIPE_HOST,
    STRIPE_PROVIDER,
    eval_env_email,
    eval_env_event,
)
from ufo_ext_eval_env.manifest import (
    NAME as EVAL_ENV_NAME,
)

from evals.driver import EVAL_SURFACE
from evals.harness.artifact_checks import ArtifactCheck, valid_png
from evals.harness.capability import (
    ArtifactProbeResult,
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
    ProbeCommandResult,
    SharedArtifact,
    WorkspaceProbe,
    grading_statement,
)
from evals.harness.harness import EvalMetric, EvalReport, Json, JsonObject
from evals.harness.registry import EvalTask
from evals.harness.scorers import combine, content_words, required_tools_scorer, skill_scorer
from evals.harness.target import CapabilityTarget
from ufo.access.grants import GrantStore
from ufo.agent_scope import agent
from ufo.blob import WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.schema import tables
from ufo.sdk.context import ScopedStore

HOUSE_STYLE_SKILL = "ufo-style"
SITE_SKILL = "website-building"
AUDIT_CONTENT = Path(__file__).with_name("ufo_app_bench_audit.cjs").read_bytes()
COPY_CAPTURE_CONTENT = Path(__file__).with_name("ufo_app_copy_capture.cjs").read_bytes()
AUDIT_DIGEST = sha256(AUDIT_CONTENT).hexdigest()
COPY_CAPTURE_DIGEST = sha256(COPY_CAPTURE_CONTENT).hexdigest()
PROBE_OUTPUT = ".eval-output"
PROBE_PORT = 8137
PROBE_TIMEOUT_SECONDS = 120
DESKTOP_WIDTH = 1440
DESKTOP_HEIGHT = 900
NARROW_WIDTH = 390
NARROW_HEIGHT = 844
SCHEMES = ("light", "dark")
# The four views `app-audit.cjs` measures. A report missing one fails the case as an unmeasured view
# rather than passing on the three that ran.
MEASURED_VIEWS = tuple(
    (scheme, width) for width in (DESKTOP_WIDTH, NARROW_WIDTH) for scheme in SCHEMES
)
AA_BODY = 4.5
AA_LARGE = 3.0
LARGE_PX = 24.0
LARGE_BOLD_PX = 18.66
BOLD_WEIGHT = 700
REPORTED_FAILURES = 4
WORKFLOW_WAIT_SECONDS = 900.0
SUPPORTED_BACKENDS = ("docker",)
INTERACTION_MIN_CONTROLS = 2
INTERACTION_MIN_SUCCESSES = 2
MAX_PREVIEW_SERVER_CALLS = 1
MAX_BROWSER_QA_CALLS = 4
DEPLOY_TOOLS = ("deploy_website", "publish_website")
SOURCE_SENTENCE = re.compile(r"(?<=[.!?])\s+")
SOURCE_COPY_WINDOW_PARTS = 3
SOURCE_COPY_MIN_WORDS = 6
SOURCE_COPY_MIN_SHARED_WORDS = 5
SOURCE_COPY_SHARE_LIMIT = 0.70
BROWSER_PROBE_LOCK = Path(tempfile.gettempdir()) / "ufo-app-browser-probe.lock"
BROWSER_PROBE_LOCK_POLL_SECONDS = 0.05
_BROWSER_PROBE_TASK_LOCK = asyncio.Lock()


def _score_evidence(key: str, passed: int, total: int) -> JsonObject:
    return {f"{key}Passed": passed, f"{key}Total": total}


CONTROL_MEMBER_QUERIES = {
    "kanban-board": "Build your interactive project board homepage for the ops team.",
    "call-notes": "Build your interactive internal notes homepage for one customer call.",
    "daily-brief": "Build your interactive daily brief homepage for the team.",
    "daily-brief-rework": (
        "Build your interactive daily brief homepage for the team. Once it is live, make the "
        "headline row bolder and redeploy the page."
    ),
}


class _ConnectorCall(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    tool: str


class _Requirement(BaseModel):
    model_config = ConfigDict(frozen=True)

    prompt: str
    calls: tuple[_ConnectorCall, ...] = ()
    visible: tuple[str, ...] = ()
    visible_any: tuple[tuple[str, ...], ...] = ()
    rewrite_sources: tuple[str, ...] = ()
    absent: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _has_proof(self) -> "_Requirement":
        if not (
            self.calls or self.visible or self.visible_any or self.rewrite_sources or self.absent
        ):
            raise ValueError(f"requirement {self.prompt!r} has no proof")
        if any(not alternatives for alternatives in self.visible_any):
            raise ValueError(f"requirement {self.prompt!r} has an empty visible alternative group")
        return self


class _EmailFixture(BaseModel):
    model_config = ConfigDict(frozen=True)

    folder: str
    sender: str
    recipients: tuple[str, ...]
    subject: str
    body: str
    sent_at: datetime


class _EventFixture(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str
    start: datetime
    end: datetime
    attendees: tuple[str, ...]
    status: str


class _ConnectedApp(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    source_apps: tuple[str, ...]
    request: str
    emails: tuple[_EmailFixture, ...]
    events: tuple[_EventFixture, ...]
    tools: dict[str, dict[str, JsonObject]]
    requirements: tuple[_Requirement, ...]

    @model_validator(mode="after")
    def _rewrite_sources_exist(self) -> "_ConnectedApp":
        for requirement in self.requirements:
            for path in requirement.rewrite_sources:
                source = self._source_text(path)
                segments = tuple(
                    segment
                    for segment in _source_segments(source)
                    if len(content_words(segment)) >= SOURCE_COPY_MIN_WORDS
                    and any(
                        marker.casefold() in segment.casefold() for marker in requirement.absent
                    )
                )
                if not segments:
                    raise ValueError(
                        f"rewrite source {path!r} has no graded sentence for {requirement.absent!r}"
                    )
        return self

    def _source_text(self, path: str) -> str:
        value: Json = self.model_dump(mode="json")
        for part in path.removeprefix("/").split("/"):
            match value:
                case dict():
                    value = value[part]
                case list():
                    value = value[int(part)]
                case _:
                    raise ValueError(f"rewrite source {path!r} stops at {part!r}")
        match value:
            case str():
                return value
            case _:
                raise ValueError(f"rewrite source {path!r} is not text")


class _ConnectedApps(BaseModel):
    model_config = ConfigDict(frozen=True)

    cases: tuple[_ConnectedApp, ...]


def _source_segments(source: str) -> tuple[str, ...]:
    return tuple(segment.strip() for segment in SOURCE_SENTENCE.split(source) if segment.strip())


APP_DATA_CONTENT = Path(__file__).with_name("ufo_app_bench_data.json").read_bytes()
APP_DATA_DIGEST = sha256(APP_DATA_CONTENT).hexdigest()
CONNECTED_APPS = _ConnectedApps.model_validate_json(APP_DATA_CONTENT).cases


def _merge_app_tools(cases: tuple[_ConnectedApp, ...]) -> dict[str, dict[str, JsonObject]]:
    merged: dict[str, dict[str, JsonObject]] = {}
    for spec in cases:
        for provider, tools in spec.tools.items():
            provider_tools = merged.setdefault(provider, {})
            for tool, response in tools.items():
                combined = provider_tools.setdefault(tool, {})
                for name, value in response.items():
                    if name not in combined:
                        combined[name] = value
                        continue
                    existing: Json = combined[name]
                    if isinstance(existing, list) and isinstance(value, list):
                        combined[name] = [*existing, *value]
                        continue
                    if existing != value:
                        raise ValueError(
                            f"app fixture conflict at {provider}.{tool}.{name}: "
                            f"{existing!r} != {value!r}"
                        )
    return merged


APP_UNIVERSE_EMAILS = tuple(item for spec in CONNECTED_APPS for item in spec.emails)
APP_UNIVERSE_EVENTS = tuple(item for spec in CONNECTED_APPS for item in spec.events)
APP_UNIVERSE_TOOLS = _merge_app_tools(CONNECTED_APPS)
MEMBER_QUERIES = {
    **CONTROL_MEMBER_QUERIES,
    **{
        case.name: " ".join((case.request, *(item.prompt for item in case.requirements)))
        for case in CONNECTED_APPS
    },
}
PROVIDER_HOSTS = {
    EMAIL_PROVIDER: EMAIL_HOST,
    CALENDAR_PROVIDER: CALENDAR_HOST,
    DRIVE_PROVIDER: DRIVE_HOST,
    GITHUB_PROVIDER: GITHUB_HOST,
    STRIPE_PROVIDER: STRIPE_HOST,
    HUBSPOT_PROVIDER: HUBSPOT_HOST,
    GREENHOUSE_PROVIDER: GREENHOUSE_HOST,
}


@dataclass(frozen=True)
class _ConnectedAppSeed:
    spec: _ConnectedApp

    async def __call__(self, workspace_id: UUID, agent_id: UUID, _blob: WorkspaceBlobStore) -> None:
        conversation_id = uuid4()
        async with workspace_tx() as connection:
            (
                await connection.execute(
                    sa.select(tables.workspace.c.id)
                    .where(tables.workspace.c.id == workspace_id)
                    .with_for_update()
                )
            ).scalar_one()
            await connection.execute(
                sa.delete(eval_env_email).where(eval_env_email.c.workspace_id == workspace_id)
            )
            await connection.execute(
                sa.delete(eval_env_event).where(eval_env_event.c.workspace_id == workspace_id)
            )
            member_id = (
                await connection.execute(
                    sa.select(tables.member.c.id)
                    .where(tables.member.c.workspace_id == workspace_id)
                    .order_by(tables.member.c.created_at)
                    .limit(1)
                )
            ).scalar_one()
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=conversation_id,
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    member_id=member_id,
                    surface=EVAL_SURFACE,
                    queue_key=f"{EVAL_SURFACE}-app-bench-seed:{conversation_id}",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            if APP_UNIVERSE_EMAILS:
                await connection.execute(
                    sa.insert(eval_env_email),
                    [
                        {
                            "id": uuid4(),
                            "workspace_id": workspace_id,
                            "folder": item.folder,
                            "sender": item.sender,
                            "recipients": list(item.recipients),
                            "subject": item.subject,
                            "body": item.body,
                            "sent_at": item.sent_at,
                        }
                        for item in APP_UNIVERSE_EMAILS
                    ],
                )
            if APP_UNIVERSE_EVENTS:
                await connection.execute(
                    sa.insert(eval_env_event),
                    [
                        {
                            "id": uuid4(),
                            "workspace_id": workspace_id,
                            "title": item.title,
                            "start_at": item.start,
                            "end_at": item.end,
                            "attendees": list(item.attendees),
                            "status": item.status,
                        }
                        for item in APP_UNIVERSE_EVENTS
                    ],
                )
        providers = {
            call.provider for requirement in self.spec.requirements for call in requirement.calls
        }
        store = ScopedStore(extension=EVAL_ENV_NAME)
        for provider in sorted(providers & APP_UNIVERSE_TOOLS.keys()):
            tools = APP_UNIVERSE_TOOLS[provider]
            for tool, response in tools.items():
                await store.put(f"{APP_FIXTURE_PREFIX}{provider}:{tool}", response)
        with agent(agent_id):
            grants = GrantStore()
            for provider in sorted(providers):
                await grants.record(
                    provider=provider,
                    account_id=ACCOUNT_ID,
                    host=PROVIDER_HOSTS[provider],
                    grantor_member_id=member_id,
                    conversation_id=conversation_id,
                    shared=True,
                )


def _rendered_parts(output: CapabilityOutput) -> tuple[str, tuple[str, ...]] | None:
    reports = tuple(
        artifact for artifact in output.artifacts if artifact.name.endswith("-audit.json")
    )
    if len(reports) != 1:
        return None
    try:
        views = json.loads(reports[0].content)["views"]
        view = next(
            item
            for item in views
            if item["scheme"] == "light" and int(item["width"]) == DESKTOP_WIDTH
        )
        text = view["renderedText"]
        parts = tuple(view["renderedParts"])
    except (KeyError, StopIteration, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(text, str) or not all(isinstance(part, str) for part in parts):
        return None
    return text, parts


def _source_copy(
    source: str, markers: tuple[str, ...], passages: tuple[str, ...]
) -> tuple[str, int, int] | None:
    for segment in _source_segments(source):
        source_words = content_words(segment)
        if len(source_words) < SOURCE_COPY_MIN_WORDS or not any(
            marker.casefold() in segment.casefold() for marker in markers
        ):
            continue
        for passage in passages:
            shared = len(source_words & content_words(passage))
            if (
                shared >= SOURCE_COPY_MIN_SHARED_WORDS
                and shared / len(source_words) >= SOURCE_COPY_SHARE_LIMIT
            ):
                return segment, shared, len(source_words)
    return None


def _rewrite_source_call(path: str) -> _ConnectorCall:
    match path.removeprefix("/").split("/"):
        case ["emails", *_]:
            return _ConnectorCall(provider=EMAIL_PROVIDER, tool="list_emails")
        case ["events", *_]:
            return _ConnectorCall(provider=CALENDAR_PROVIDER, tool="list_events")
        case ["tools", provider, tool, *_]:
            return _ConnectorCall(provider=provider, tool=tool)
        case _:
            raise ValueError(f"rewrite source {path!r} has no connector call")


def _requirement_scorer(spec: _ConnectedApp) -> Grader:
    total = 1 + sum(
        len(requirement.calls)
        + len(requirement.visible)
        + len(requirement.visible_any)
        + (
            len(requirement.rewrite_sources)
            if requirement.rewrite_sources
            else len(requirement.absent)
        )
        for requirement in spec.requirements
    )

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        rendered = _rendered_parts(output)
        if rendered is None:
            return CapabilityVerdict(
                False,
                "app audit report has no rendered desktop text",
                _score_evidence("appSource", 0, total),
            )
        rendered_text, parts = rendered
        text = rendered_text.casefold()
        passages = tuple(
            " ".join(parts[start : start + width])
            for start in range(len(parts))
            for width in range(1, SOURCE_COPY_WINDOW_PARTS + 1)
            if start + width <= len(parts)
        )
        successful = tuple(call for call in output.calls if call.succeeded)
        failures = []
        passed = 0
        if any(call.name == "list_external_tools" for call in successful):
            passed += 1
        else:
            failures.append("did not discover the connected source catalog")
        for requirement in spec.requirements:
            for expected in requirement.calls:
                described = any(
                    call.name == "describe_external_tools"
                    and call.input.get("source_id") == expected.provider
                    for call in successful
                )
                called = any(
                    call.name == "call_external_tool"
                    and call.input.get("source_id") == expected.provider
                    and call.input.get("tool_name") == expected.tool
                    for call in successful
                )
                if not described or not called:
                    failures.append(
                        f"{requirement.prompt} lacks {expected.provider}.{expected.tool} proof",
                    )
                else:
                    passed += 1
            missing = tuple(item for item in requirement.visible if item.casefold() not in text)
            passed += len(requirement.visible) - len(missing)
            if missing:
                failures.append(
                    f"{requirement.prompt} lacks rendered facts: {', '.join(missing[:4])}",
                )
            missing_any = tuple(
                alternatives
                for alternatives in requirement.visible_any
                if not any(item.casefold() in text for item in alternatives)
            )
            passed += len(requirement.visible_any) - len(missing_any)
            if missing_any:
                labels = tuple(" or ".join(items) for items in missing_any[:4])
                failures.append(
                    f"{requirement.prompt} lacks rendered facts: {', '.join(labels)}",
                )
            copied = tuple(item for item in requirement.absent if item.casefold() in text)
            if copied:
                failures.append(
                    f"{requirement.prompt} copies source text: {', '.join(copied[:2])}",
                )
            if not requirement.rewrite_sources:
                passed += len(requirement.absent) - len(copied)
            for path in requirement.rewrite_sources:
                source_copy = _source_copy(spec._source_text(path), requirement.absent, passages)
                if not copied and source_copy is None:
                    passed += 1
                    continue
                if copied:
                    continue
                if source_copy is None:
                    raise RuntimeError(f"rewrite source {path!r} has no copy verdict")
                source, shared, source_words = source_copy
                failures.append(
                    f"{requirement.prompt} keeps {shared}/{source_words} source words: "
                    f"{source[:120]}"
                )
        evidence = _score_evidence("appSource", passed, total)
        if failures:
            return CapabilityVerdict(False, "; ".join(failures), evidence)
        return CapabilityVerdict(
            True,
            f"{len(spec.requirements)} prompt requirements have connector and page proof",
            evidence,
        )

    return DescribedGrader(
        "each connected-app prompt requirement has its mapped connector, rendered fact, or copy "
        "proof",
        grade,
    )


def _above_fold_scorer(spec: _ConnectedApp) -> Grader:
    groups = tuple(
        (fact, (fact,)) for requirement in spec.requirements for fact in requirement.visible
    ) + tuple(
        (" or ".join(alternatives), alternatives)
        for requirement in spec.requirements
        for alternatives in requirement.visible_any
    )
    total = len(groups) * len(SCHEMES)

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        reports = tuple(
            artifact for artifact in output.artifacts if artifact.name.endswith("-audit.json")
        )
        if len(reports) != 1:
            return CapabilityVerdict(
                False,
                f"captured {len(reports)} app audit reports",
                _score_evidence("appDensity", 0, total),
            )
        try:
            views = {
                (str(view["scheme"]), int(view["width"])): view
                for view in json.loads(reports[0].content)["views"]
            }
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return CapabilityVerdict(
                False,
                "app audit report has no valid views",
                _score_evidence("appDensity", 0, total),
            )
        missing = [scheme for scheme in SCHEMES if (scheme, DESKTOP_WIDTH) not in views]
        if missing:
            return CapabilityVerdict(
                False,
                f"app audit has no desktop view for {', '.join(missing)}",
                _score_evidence("appDensity", 0, total),
            )
        passed = 0
        failures = []
        for scheme in SCHEMES:
            text = str(views[(scheme, DESKTOP_WIDTH)].get("aboveFoldText", "")).casefold()
            for label, alternatives in groups:
                if any(alternative.casefold() in text for alternative in alternatives):
                    passed += 1
                else:
                    failures.append(f"{scheme} desktop lacks {label}")
        evidence = _score_evidence("appDensity", passed, total)
        if failures:
            return CapabilityVerdict(False, "; ".join(failures[:6]), evidence)
        return CapabilityVerdict(
            True, f"{len(groups)} required fact(s) are above the fold in both schemes", evidence
        )

    return DescribedGrader(
        "every exact connected-app fact is above the fold in both desktop schemes", grade
    )


def _copy_scorer(spec: _ConnectedApp) -> Grader:
    rewrite_requirements = tuple(
        requirement for requirement in spec.requirements if requirement.rewrite_sources
    )
    if not rewrite_requirements:
        raise ValueError(f"copy case {spec.name!r} has no rewrite source")

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        rendered = _rendered_parts(output)
        if rendered is None:
            return CapabilityVerdict(False, "app audit report has no rendered desktop text")
        rendered_text, parts = rendered
        text = rendered_text.casefold()
        passages = tuple(
            " ".join(parts[start : start + width])
            for start in range(len(parts))
            for width in range(1, SOURCE_COPY_WINDOW_PARTS + 1)
            if start + width <= len(parts)
        )
        successful = tuple(call for call in output.calls if call.succeeded)
        failures = []
        if not any(call.name == "list_external_tools" for call in successful):
            failures.append("did not discover the connected source catalog")
        for requirement in rewrite_requirements:
            for path in requirement.rewrite_sources:
                expected = _rewrite_source_call(path)
                described = any(
                    call.name == "describe_external_tools"
                    and call.input.get("source_id") == expected.provider
                    for call in successful
                )
                called = any(
                    call.name == "call_external_tool"
                    and call.input.get("source_id") == expected.provider
                    and call.input.get("tool_name") == expected.tool
                    for call in successful
                )
                if not described or not called:
                    failures.append(
                        f"{requirement.prompt} lacks {expected.provider}.{expected.tool} proof"
                    )
                source_copy = _source_copy(spec._source_text(path), requirement.absent, passages)
                if source_copy is not None:
                    source, shared, total = source_copy
                    failures.append(
                        f"{requirement.prompt} keeps {shared}/{total} source words: {source[:120]}"
                    )
            missing = tuple(item for item in requirement.visible if item.casefold() not in text)
            if missing:
                failures.append(
                    f"{requirement.prompt} lacks rendered source facts: {', '.join(missing[:4])}"
                )
            missing_any = tuple(
                alternatives
                for alternatives in requirement.visible_any
                if not any(item.casefold() in text for item in alternatives)
            )
            if missing_any:
                labels = tuple(" or ".join(items) for items in missing_any[:4])
                failures.append(
                    f"{requirement.prompt} lacks rendered source facts: {', '.join(labels)}"
                )
            copied = tuple(item for item in requirement.absent if item.casefold() in text)
            if copied:
                failures.append(f"{requirement.prompt} copies source text: {', '.join(copied[:2])}")
        if failures:
            return CapabilityVerdict(False, "; ".join(failures))
        return CapabilityVerdict(
            True,
            f"{len(rewrite_requirements)} source requirement(s) use connector facts and "
            "reader copy",
        )

    return DescribedGrader(
        "the app uses its connected prose source and rewrites it for the member",
        grade,
    )


@dataclass(frozen=True)
class AppBenchWorkspaceProbe(WorkspaceProbe):
    """Run a bounded app-bench command in a local Docker conversation sandbox."""

    conversation_id: UUID

    async def run(self, command: str, timeout_s: int = 60) -> ProbeCommandResult:
        async with _browser_probe_slot():
            process = await asyncio.create_subprocess_exec(
                "docker",
                "exec",
                f"ufo-sbx-{self.conversation_id}",
                "bash",
                "-lc",
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout_s)
            except TimeoutError:
                process.kill()
                await process.wait()
                return ProbeCommandResult(124, "", "probe timed out", timeout_s)
            return ProbeCommandResult(
                process.returncode or 0,
                stdout.decode(errors="replace"),
                stderr.decode(errors="replace"),
            )


@asynccontextmanager
async def _browser_probe_slot() -> AsyncIterator[None]:
    async with _BROWSER_PROBE_TASK_LOCK:
        descriptor = await asyncio.to_thread(
            os.open,
            BROWSER_PROBE_LOCK,
            os.O_CREAT | os.O_RDWR,
            0o600,
        )
        try:
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    await asyncio.sleep(BROWSER_PROBE_LOCK_POLL_SECONDS)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            await asyncio.to_thread(os.close, descriptor)


@dataclass(frozen=True)
class _AppBenchProbe:
    name: str

    async def __call__(
        self, output: CapabilityOutput, probe: WorkspaceProbe
    ) -> ArtifactProbeResult:
        if output.workspace_dir is None:
            return ArtifactProbeResult(error="app probe has no workspace")
        directory = output.workspace_dir / PROBE_OUTPUT / self.name
        result = await probe.run(self._command(), PROBE_TIMEOUT_SECONDS)
        if result.exit_code != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "no command output"
            return ArtifactProbeResult(error=f"app probe failed: {detail[:500]}")
        paths = (
            directory / f"{self.name}-interactive.html",
            directory / f"{self.name}-static.html",
            directory / f"{self.name}-audit.json",
            *(directory / f"{self.name}-{scheme}.png" for scheme in SCHEMES),
        )
        missing = [path.name for path in paths if not path.is_file()]
        if missing:
            return ArtifactProbeResult(error=f"app probe produced no {', '.join(missing)}")
        contents = await asyncio.gather(*(asyncio.to_thread(path.read_bytes) for path in paths))
        return ArtifactProbeResult(
            artifacts=tuple(
                SharedArtifact(path.name, content)
                for path, content in zip(paths, contents, strict=True)
            )
        )

    def _command(self) -> str:
        directory = f"/workspace/{PROBE_OUTPUT}/{self.name}"
        audit = base64.b64encode(AUDIT_CONTENT).decode()
        find_page = """python3 - <<'PY'
from pathlib import Path

root = Path('/workspace')
excluded = {'.skills', '.eval-output', 'node_modules'}
pages = [
    path
    for path in root.rglob('*.html')
    if not excluded.intersection(path.parts) and path.is_file()
]
pages.sort(key=lambda path: (path.name == 'index.html', path.stat().st_mtime), reverse=True)
if pages:
    print(pages[0])
PY
"""
        readiness = f"""python3 - <<'PY'
import socket
import time

deadline = time.time() + 15
while time.time() < deadline:
    try:
        with socket.create_connection(('127.0.0.1', {PROBE_PORT}), timeout=1):
            raise SystemExit(0)
    except OSError:
        time.sleep(0.2)
raise SystemExit(1)
PY"""
        return (
            "set -eu\n"
            f"capture={shlex.quote(directory)}\n"
            'rm -rf "$capture"\n'
            'mkdir -p "$capture"\n'
            f"printf %s {shlex.quote(audit)} | base64 -d > /tmp/ufo-app-bench-audit.cjs\n"
            f"page=$({find_page})\n"
            'if [ -z "$page" ]; then printf %s "no generated HTML page" >&2; exit 2; fi\n'
            f"(fuser -k {PROBE_PORT}/tcp 2>/dev/null || true)\n"
            f"nohup python3 -m http.server {PROBE_PORT} --bind 127.0.0.1 "
            ' --directory "$(dirname "$page")" >/tmp/ufo-app-bench-server.log 2>&1 &\n'
            f"{readiness}\n"
            "node /tmp/ufo-app-bench-audit.cjs "
            f'http://localhost:{PROBE_PORT}/$(basename "$page") '
            f'"$capture/{self.name}-audit.json" "$capture/{self.name}-light.png" '
            f'"$capture/{self.name}-dark.png" "$capture/{self.name}-interactive.html" '
            f'"$capture/{self.name}-static.html"'
        )


@dataclass(frozen=True)
class _AppCopyProbe:
    name: str

    async def __call__(
        self, output: CapabilityOutput, probe: WorkspaceProbe
    ) -> ArtifactProbeResult:
        if output.workspace_dir is None:
            return ArtifactProbeResult(error="app copy probe has no workspace")
        directory = output.workspace_dir / PROBE_OUTPUT / self.name
        result = await probe.run(self._command(), PROBE_TIMEOUT_SECONDS)
        if result.exit_code != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "no command output"
            return ArtifactProbeResult(error=f"app copy probe failed: {detail[:500]}")
        paths = (
            directory / f"{self.name}-static.html",
            directory / f"{self.name}-audit.json",
        )
        missing = [path.name for path in paths if not path.is_file()]
        if missing:
            return ArtifactProbeResult(error=f"app copy probe produced no {', '.join(missing)}")
        contents = await asyncio.gather(*(asyncio.to_thread(path.read_bytes) for path in paths))
        return ArtifactProbeResult(
            artifacts=tuple(
                SharedArtifact(path.name, content)
                for path, content in zip(paths, contents, strict=True)
            )
        )

    def _command(self) -> str:
        directory = f"/workspace/{PROBE_OUTPUT}/{self.name}"
        capture = base64.b64encode(COPY_CAPTURE_CONTENT).decode()
        find_page = """python3 - <<'PY'
from pathlib import Path

root = Path('/workspace')
excluded = {'.skills', '.eval-output', 'node_modules'}
pages = [
    path
    for path in root.rglob('*.html')
    if not excluded.intersection(path.parts) and path.is_file()
]
pages.sort(key=lambda path: (path.name == 'index.html', path.stat().st_mtime), reverse=True)
if pages:
    print(pages[0])
PY
"""
        readiness = f"""python3 - <<'PY'
import socket
import time

deadline = time.time() + 15
while time.time() < deadline:
    try:
        with socket.create_connection(('127.0.0.1', {PROBE_PORT}), timeout=1):
            raise SystemExit(0)
    except OSError:
        time.sleep(0.2)
raise SystemExit(1)
PY"""
        return (
            "set -eu\n"
            f"capture={shlex.quote(directory)}\n"
            'rm -rf "$capture"\n'
            'mkdir -p "$capture"\n'
            f"printf %s {shlex.quote(capture)} | base64 -d > /tmp/ufo-app-copy-capture.cjs\n"
            f"page=$({find_page})\n"
            'if [ -z "$page" ]; then printf %s "no generated HTML page" >&2; exit 2; fi\n'
            f"(fuser -k {PROBE_PORT}/tcp 2>/dev/null || true)\n"
            f"nohup python3 -m http.server {PROBE_PORT} --bind 127.0.0.1 "
            ' --directory "$(dirname "$page")" >/tmp/ufo-app-copy-server.log 2>&1 &\n'
            f"{readiness}\n"
            "node /tmp/ufo-app-copy-capture.cjs "
            f'http://localhost:{PROBE_PORT}/$(basename "$page") '
            f'"$capture/{self.name}-static.html" "$capture/{self.name}-audit.json"'
        )


HOUSE_CRITERIA = (
    "The two screenshots intentionally show different light and dark schemes. Judge each image "
    "independently: one screenshot must not split or mix schemes within itself. Surfaces and text "
    "are neutral, blue is the primary accent, and orange marks attention. Green, red, or purple "
    "status and decorative hues fail this criterion.",
    "The screen is neutral with the accent carried by a few small elements: an accent is a fill, a "
    "marker or a link, never the colour of body text or of a whole pane, and the second accent "
    "appears only where something wants attention.",
    "Text uses one neutral grotesque face for prose and a monospace face for identifiers, dates, "
    "counts, and measurements. A serif can appear only in the screen title. Decorative, novelty, "
    "or visibly mismatched faces fail this criterion.",
    "Type uses a small number of deliberate, readable steps. The compact screen title can be the "
    "largest text once; metrics, cards, and section headings do not compete with it.",
    "Panels, cards, controls, and menus have consistent near-square corners. Full pills appear "
    "only on compact rows, tags, filters, or statuses. Do not fail minor radius differences that "
    "are not visibly inconsistent.",
    "Gaps, padding and alignment are even and deliberate: elements sit on a shared grid, "
    "comparable gaps match, columns and baselines line up, and related controls use consistent "
    "interior padding.",
)

TASTE_CRITERIA = (
    "The first scan has a clear order: urgent exceptions and decisions lead, current work follows, "
    "and stable reference information stays available without competing for attention.",
    "Components fit their information and action: use rows for comparable records, tables for "
    "repeated fields, charts for real comparisons, and controls for actions. Do not turn every "
    "fact into a card, chip, badge, or decorative metric.",
    "The page is dense but calm. It uses grouping, whitespace, type, and alignment to separate "
    "meaning, with no empty hero area, repeated heading, decorative illustration, or filler copy.",
    "The screen reads as one intentional product surface. Navigation, filters, detail, status, and "
    "prepared actions use one consistent visual and interaction language.",
)


def _needed_ratio(px: float, weight: int) -> float:
    """WCAG AA for one rendered string: 4.5:1, or 3:1 where the text is large — 24px, or 18.66px at
    bold weight. The audit reports every string under the strict floor with its own size and weight,
    so the threshold each string owed is decided here and not by the script that measured it."""
    if px >= LARGE_PX or (px >= LARGE_BOLD_PX and weight >= BOLD_WEIGHT):
        return AA_LARGE
    return AA_BODY


def _measured_screen(content: bytes) -> ArtifactCheck:
    """The measured half: every view `app-audit.cjs` shot, with contrast reported first because it
    is the check a first bench run showed a static token check cannot make. Contrast fails the
    screen, then a document wider than its viewport, then clipped text, then a console error."""
    try:
        report = json.loads(content)
        views = {(str(view["scheme"]), int(view["width"])): view for view in report["views"]}
        missing = [
            f"{scheme} at {width}px"
            for scheme, width in MEASURED_VIEWS
            if (scheme, width) not in views
        ]
        if missing:
            return ArtifactCheck(False, f"measures no {', '.join(missing)}")
        unread = [
            f"{scheme} at {width}px"
            for scheme, width in MEASURED_VIEWS
            if int(views[(scheme, width)]["textChecked"]) == 0
        ]
        if unread:
            return ArtifactCheck(False, f"read no text in {', '.join(unread)} — the page is empty")
        below = [
            f"{scheme} {width}px {item['selector']} at {float(item['ratio'])}:1 needs {needed}:1"
            for scheme, width in MEASURED_VIEWS
            for item in views[(scheme, width)]["text"]
            if (needed := _needed_ratio(float(item["px"]), int(item["weight"])))
            > float(item["ratio"])
        ]
        wide = [
            f"{scheme} {width}px document is {int(views[(scheme, width)]['documentWidth'])}px"
            for scheme, width in MEASURED_VIEWS
            if int(views[(scheme, width)]["documentWidth"]) > width
        ]
        clipped = [
            f"{scheme} {width}px clips {clip}"
            for scheme, width in MEASURED_VIEWS
            for clip in views[(scheme, width)]["clipped"]
        ]
        noisy = [
            f"{scheme} {width}px logs {problem}"
            for scheme, width in MEASURED_VIEWS
            for problem in views[(scheme, width)]["console"]
        ]
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError, KeyError) as error:
        return ArtifactCheck(False, f"is not a bench audit report: {error}")
    if below:
        return ArtifactCheck(
            False,
            f"{len(below)} string(s) of text below AA: {'; '.join(below[:REPORTED_FAILURES])}",
        )
    for reported in (wide, clipped, noisy):
        if reported:
            return ArtifactCheck(False, "; ".join(reported[:REPORTED_FAILURES]))
    return ArtifactCheck(
        True, "every string clears AA in both schemes, and nothing clips or overflows horizontally"
    )


def _interaction_screen(name: str, content: bytes) -> ArtifactCheck:
    try:
        interaction = json.loads(content)["interaction"]
        controls = interaction["controls"]
        successes = interaction["successes"]
        problems = interaction["console"]
        selectors = {str(success["selector"]) for success in successes}
        names = [str(success["name"]) for success in successes]
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError, KeyError) as error:
        return ArtifactCheck(False, f"is not an interaction audit: {error}")
    if problems:
        return ArtifactCheck(False, f"interaction logs {str(problems[0])[:300]}")
    if len(controls) < INTERACTION_MIN_CONTROLS:
        return ArtifactCheck(
            False,
            f"{name} exposes {len(controls)} accessible control(s), needs "
            f"{INTERACTION_MIN_CONTROLS}",
        )
    if len(selectors) < INTERACTION_MIN_SUCCESSES:
        return ArtifactCheck(
            False,
            f"{name} has {len(selectors)} distinct visible state change(s), needs "
            f"{INTERACTION_MIN_SUCCESSES}",
        )
    return ArtifactCheck(
        True,
        f"{name} has {len(selectors)} browser-proved visible state changes: "
        f"{', '.join(names[:INTERACTION_MIN_SUCCESSES])}",
    )


def _captured_artifact_scorer(
    suffix: str, validate: Callable[[bytes], ArtifactCheck] | None = None
) -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        if output.artifact_error:
            return CapabilityVerdict(False, f"artifact inspection failed: {output.artifact_error}")
        artifacts = [
            artifact for artifact in output.artifacts if artifact.name.lower().endswith(suffix)
        ]
        if len(artifacts) != 1:
            return CapabilityVerdict(False, f"probe captured {len(artifacts)} {suffix} artifacts")
        if validate is None:
            return CapabilityVerdict(True, f"captured {artifacts[0].name}")
        checked = validate(artifacts[0].content)
        return CapabilityVerdict(checked.passed, f"{artifacts[0].name}: {checked.reason}")

    return DescribedGrader(f"the harness probe captures one valid {suffix} artifact", grade)


def _screen_images_scorer() -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        images = [artifact for artifact in output.artifacts if artifact.name.endswith(".png")]
        if len(images) != len(SCHEMES):
            return CapabilityVerdict(
                False, f"probe captured {len(images)} screen image(s), need {len(SCHEMES)}"
            )
        invalid = next((image for image in images if not valid_png(image.content).passed), None)
        if invalid is not None:
            return CapabilityVerdict(False, f"{invalid.name} is not a valid PNG")
        return CapabilityVerdict(True, f"captured {len(images)} screen image(s)")

    return DescribedGrader("the harness probe captures both valid screen images", grade)


def _interaction_scorer(name: str) -> Grader:
    base = _captured_artifact_scorer(".json", lambda content: _interaction_screen(name, content))

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        verdict = await base(output)
        return replace(
            verdict,
            evidence={
                **_score_evidence("appInteraction", 1 if verdict.passed else 0, 1),
            },
        )

    return DescribedGrader(grading_statement(base), grade)


def _page_scorer() -> Grader:
    graders = (
        _captured_artifact_scorer("-interactive.html"),
        _captured_artifact_scorer("-static.html"),
        _captured_artifact_scorer(".json", _measured_screen),
        _screen_images_scorer(),
    )

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        verdicts = [await grader(output) for grader in graders]
        passed = sum(1 for verdict in verdicts if verdict.passed)
        return CapabilityVerdict(
            passed == len(verdicts),
            "; ".join(verdict.reason for verdict in verdicts),
            _score_evidence("appPage", passed, len(verdicts)),
        )

    return DescribedGrader(
        "; ".join(grading_statement(grader) for grader in graders),
        grade,
    )


def _skill_scorer() -> Grader:
    base = skill_scorer(SITE_SKILL, HOUSE_STYLE_SKILL)

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        verdict = await base(output)
        return replace(
            verdict,
            evidence={
                **verdict.evidence,
                **_score_evidence("processSkill", 1 if verdict.passed else 0, 1),
            },
        )

    return DescribedGrader(grading_statement(base), grade)


def _delivery_scorer() -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        successful = tuple(
            (index, call) for index, call in enumerate(output.calls) if call.succeeded
        )
        deployments = tuple(
            (index, call) for index, call in successful if call.name in DEPLOY_TOOLS
        )
        homepages = tuple(
            (index, call) for index, call in successful if call.name == "set_homepage"
        )
        ordered = next(
            (
                (deployment, homepage)
                for deployment in deployments
                for homepage in homepages
                if deployment[0] < homepage[0]
            ),
            None,
        )
        evidence = _score_evidence(
            "appDelivery", (1 if deployments else 0) + (1 if ordered else 0), 2
        )
        if not deployments:
            return CapabilityVerdict(
                False, "did not complete deploy_website or publish_website", evidence
            )
        if ordered is None:
            return CapabilityVerdict(
                False, "set_homepage must complete after the application deployment", evidence
            )
        return CapabilityVerdict(
            True,
            f"{ordered[0][1].name} completed before set_homepage",
            evidence,
        )

    return DescribedGrader(
        "deploy_website or publish_website completes successfully before set_homepage", grade
    )


def _qa_efficiency_scorer() -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        failed = _score_evidence("processQa", 0, 1)
        starts = tuple(call for call in output.calls if call.name == "start_server")
        if len(starts) != MAX_PREVIEW_SERVER_CALLS:
            return CapabilityVerdict(
                False,
                f"used start_server {len(starts)} time(s), needs {MAX_PREVIEW_SERVER_CALLS}",
                failed,
            )
        if not starts[0].succeeded:
            return CapabilityVerdict(False, "the preview server did not start successfully", failed)
        browser_indexes = tuple(
            index for index, call in enumerate(output.calls) if call.name == "js_repl"
        )
        if not browser_indexes:
            return CapabilityVerdict(False, "used no js_repl browser QA batch", failed)
        if len(browser_indexes) > MAX_BROWSER_QA_CALLS:
            return CapabilityVerdict(
                False,
                f"used {len(browser_indexes)} browser QA batches, needs at most "
                f"{MAX_BROWSER_QA_CALLS}",
                failed,
            )
        deployments = tuple(
            (index, call) for index, call in enumerate(output.calls) if call.name in DEPLOY_TOOLS
        )
        homepages = tuple(
            index
            for index, call in enumerate(output.calls)
            if call.name == "set_homepage" and call.succeeded
        )
        if not deployments or not homepages:
            return CapabilityVerdict(
                False,
                "browser QA must precede the application deployment and set_homepage",
                failed,
            )
        if not deployments[-1][1].succeeded:
            return CapabilityVerdict(False, "the final application deployment failed", failed)
        names = tuple(call.name for call in output.calls)
        start_index = names.index("start_server")
        if not start_index < min(browser_indexes):
            return CapabilityVerdict(False, "browser QA must run after start_server", failed)
        successful_deployments = tuple(index for index, call in deployments if call.succeeded)
        first_deployment = successful_deployments[0]
        homepage_deadline = (
            successful_deployments[1] if len(successful_deployments) > 1 else len(output.calls)
        )
        if not any(first_deployment < homepage < homepage_deadline for homepage in homepages):
            return CapabilityVerdict(
                False,
                f"set_homepage must run after the first {output.calls[first_deployment].name} "
                "and before a redeploy",
                failed,
            )
        prior_deployment = start_index
        assigned_browser_indexes: list[int] = []
        for cycle_index, deployment in enumerate(successful_deployments):
            cycle = tuple(
                index for index in browser_indexes if prior_deployment < index < deployment
            )
            successful = tuple(index for index in cycle if output.calls[index].succeeded)
            required = 2 if cycle_index == 0 else 1
            if len(successful) < required:
                return CapabilityVerdict(
                    False,
                    f"used {len(successful)} successful browser QA batch(es) before "
                    f"{output.calls[deployment].name}, needs at least {required}",
                    failed,
                )
            if not output.calls[cycle[-1]].succeeded:
                return CapabilityVerdict(False, "the final browser QA batch failed", failed)
            assigned_browser_indexes.extend(cycle)
            prior_deployment = deployment
        if tuple(assigned_browser_indexes) != browser_indexes:
            return CapabilityVerdict(
                False,
                f"browser QA must finish before {output.calls[successful_deployments[-1]].name}",
                failed,
            )
        return CapabilityVerdict(
            True,
            f"started one preview server and used {len(browser_indexes)} browser QA batch(es) "
            f"across {len(successful_deployments)} deployment(s)",
            _score_evidence("processQa", 1, 1),
        )

    return DescribedGrader(
        "one preview start, two to four browser QA batches total, and one affected-batch proof "
        "before each redeploy",
        grade,
    )


APP_TIERS = {
    **{case: 1 for case in CONTROL_MEMBER_QUERIES},
    **{
        case.name: 3
        if len({call.provider for item in case.requirements for call in item.calls}) > 1
        else 2
        for case in CONNECTED_APPS
    },
}


def _fraction(evidence: JsonObject, key: str, default: float = 0.0) -> float:
    passed = evidence.get(f"{key}Passed")
    total = evidence.get(f"{key}Total")
    if not isinstance(passed, int) or not isinstance(total, int) or total <= 0:
        return default
    return passed / total


def _score_app_report(report: EvalReport) -> EvalReport:
    cases = []
    layer_values: dict[str, list[float]] = {
        "delivery": [],
        "source": [],
        "density": [],
        "page": [],
        "interaction": [],
        "visual": [],
    }
    process_values = []
    app_values = []
    for case in report.cases:
        selected = case.evidence.get("selectedAttempt")
        attempts = case.evidence.get("attempts")
        attempt: JsonObject = {}
        if isinstance(selected, int) and isinstance(attempts, list) and selected < len(attempts):
            candidate = attempts[selected]
            if isinstance(candidate, dict):
                attempt = candidate
        grader = attempt.get("grader")
        scored: JsonObject = grader if isinstance(grader, dict) else {}
        judge = attempt.get("judge")
        rubric = case.evidence.get("visualRubric")
        visual_total = len(rubric) if isinstance(rubric, list) else 0
        visual_passed = (
            sum(1 for item in judge if isinstance(item, dict) and item.get("passed") is True)
            if isinstance(judge, list)
            else 0
        )
        layers = {
            "delivery": _fraction(scored, "appDelivery"),
            "source": _fraction(
                scored, "appSource", 1.0 if case.name in CONTROL_MEMBER_QUERIES else 0.0
            ),
            "density": _fraction(
                scored, "appDensity", 1.0 if case.name in CONTROL_MEMBER_QUERIES else 0.0
            ),
            "page": _fraction(scored, "appPage"),
            "interaction": _fraction(scored, "appInteraction"),
            "visual": visual_passed / visual_total if visual_total else 0.0,
        }
        process_layers = {
            "skill": _fraction(scored, "processSkill"),
            "qa": _fraction(scored, "processQa"),
        }
        app_score = sum(layers.values()) / len(layers)
        process_score = sum(process_layers.values()) / len(process_layers)
        evidence = {
            **case.evidence,
            "appScore": app_score,
            "appScoreLayers": layers,
            "processScore": process_score,
            "processScoreLayers": process_layers,
        }
        cases.append(case.model_copy(update={"evidence": evidence, "tier": APP_TIERS[case.name]}))
        if case.excluded:
            continue
        app_values.append(app_score)
        process_values.append(process_score)
        for name, value in layers.items():
            layer_values[name].append(value)
    metrics = (
        (
            EvalMetric(name="app_score", value=sum(app_values) / len(app_values)),
            *(
                EvalMetric(name=f"{name}_score", value=sum(values) / len(values))
                for name, values in layer_values.items()
            ),
            EvalMetric(name="process_score", value=sum(process_values) / len(process_values)),
        )
        if app_values
        else ()
    )
    return report.model_copy(update={"cases": tuple(cases), "metrics": (*report.metrics, *metrics)})


def _scored_task(task: EvalTask) -> EvalTask:
    async def run(target: CapabilityTarget, slots: asyncio.Semaphore) -> EvalReport:
        return _score_app_report(await task.run(target, slots))

    return replace(task, run=run)


def _pull_before_redeploy_scorer() -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        names = tuple(call.name for call in output.calls)
        deploys = tuple(index for index, name in enumerate(names) if name == "deploy_website")
        if len(deploys) < 2:
            return CapabilityVerdict(
                False, f"used deploy_website {len(deploys)} time(s); the rework needs a second"
            )
        pulls = tuple(
            index
            for index, call in enumerate(output.calls)
            if call.name == "object_get" and call.succeeded
        )
        if not any(deploys[0] < pull < deploys[-1] for pull in pulls):
            return CapabilityVerdict(
                False, "the rework must object_get the site between the deploys, before editing"
            )
        return CapabilityVerdict(True, "pulled the deployed source before the rework's deploy")

    return DescribedGrader("the rework pulls the deployed source before deploying again", grade)


def _screen(
    name: str,
    information_inventory: str,
    *,
    extra_graders: tuple[Grader, ...] = (),
    extra_visual: tuple[str, ...] = (),
    seed: _ConnectedAppSeed | None = None,
    data_digest: str = "",
) -> CapabilityCase:
    return CapabilityCase(
        name,
        MEMBER_QUERIES[name],
        combine(
            _skill_scorer(),
            _delivery_scorer(),
            _qa_efficiency_scorer(),
            _page_scorer(),
            _interaction_scorer(name),
            *extra_graders,
        ),
        visual_rubric=(
            *HOUSE_CRITERIA,
            *extra_visual,
            f"The first {DESKTOP_WIDTH} x {DESKTOP_HEIGHT} view gives useful operational density "
            f"above the fold: {information_inventory}. The working area uses compact grouped "
            "content, and no oversized title, empty decorative panel, or unused region takes "
            "space from requested information. Judge visible density and hierarchy, not an exact "
            "fact count.",
        ),
        judge_on_deterministic_failure=True,
        digest_tag=(
            f"ufo-app-bench:{name}:interactive-homepage:qa-total-{MAX_BROWSER_QA_CALLS}:"
            "redeploy-1:"
            f"wait-{WORKFLOW_WAIT_SECONDS:g}:audit-{AUDIT_DIGEST[:12]}{data_digest}"
        ),
        artifact_probe=_AppBenchProbe(name),
        seed=seed,
    )


CONTROL_CASES = (
    _screen(
        "kanban-board",
        "the board context, useful workflow groups, enough work items to scan, and ownership or "
        "current state on each item; the exact layout, labels, and item count are the builder's "
        "choice",
    ),
    _screen(
        "call-notes",
        "the call identity and context, participants, the outcome or summary, decisions or key "
        "points, and follow-up work with responsibility or timing where useful; the exact layout "
        "and labels are the builder's choice",
    ),
    _screen(
        "daily-brief",
        "the brief's date or scope, current status or metrics, material changes, priorities or "
        "follow-ups, and source context; the exact layout, labels, and item count are the "
        "builder's choice",
    ),
    _screen(
        "daily-brief-rework",
        "the brief's date or scope, current status or metrics, material changes, priorities or "
        "follow-ups, and source context; the exact layout, labels, and item count are the "
        "builder's choice",
        extra_graders=(_pull_before_redeploy_scorer(),),
        data_digest=":pull-before-redeploy",
    ),
)


CONNECTED_CASES = tuple(
    _screen(
        spec.name,
        " ".join(requirement.prompt for requirement in spec.requirements),
        extra_graders=(_requirement_scorer(spec), _above_fold_scorer(spec)),
        extra_visual=TASTE_CRITERIA,
        seed=_ConnectedAppSeed(spec),
        data_digest=f":data-{APP_DATA_DIGEST[:12]}",
    )
    for spec in CONNECTED_APPS
)

COPY_CASES = tuple(
    CapabilityCase(
        f"copy-{spec.name}",
        MEMBER_QUERIES[spec.name],
        combine(
            skill_scorer(SITE_SKILL, HOUSE_STYLE_SKILL),
            required_tools_scorer(
                ("deploy_website", "set_homepage"),
                (("deploy_website", "set_homepage"),),
            ),
            _captured_artifact_scorer("-static.html"),
            _copy_scorer(spec),
        ),
        digest_tag=(
            f"ufo-app-copy:{spec.name}:source-use-and-reader-copy:"
            f"wait-{WORKFLOW_WAIT_SECONDS:g}:capture-{COPY_CAPTURE_DIGEST[:12]}:"
            f"data-{APP_DATA_DIGEST[:12]}"
        ),
        artifact_probe=_AppCopyProbe(spec.name),
        seed=_ConnectedAppSeed(spec),
    )
    for spec in CONNECTED_APPS
    if any(requirement.rewrite_sources for requirement in spec.requirements)
)

CASES = (*CONTROL_CASES, *CONNECTED_CASES)
