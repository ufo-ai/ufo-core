"""Replay retained app source through one bounded Gemini repair and live product audits."""

import asyncio
import base64
import json
import shlex
import shutil
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from ufo_ext_eval_env.manifest import (
    APP_QA_EDIT_CALL_LIMIT,
    APP_QA_EDIT_NEW_BYTES_LIMIT,
    APP_QA_EDIT_OLD_BYTES_LIMIT,
)
from ufo_ext_sites.application_audit import (
    ApplicationAuditContract,
    ApplicationAuditIssue,
    ApplicationAuditReport,
    audit_application,
)
from ufo_ext_sites.application_homepage import APPLICATION_TEMPLATE_DIR
from ufo_ext_sites.source import KIT_DIR, PROJECT_CONFIG_BYTES, PROJECT_PREVIEW_BYTES
from ufo_ext_sites.tools import APPLICATION_AUDIT_TIMEOUT_SECONDS, BUILD_TIMEOUT_SECONDS

from evals.harness.capability import (
    ArtifactProbeResult,
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    SharedArtifact,
    WorkspaceFile,
    WorkspaceProbe,
)
from evals.harness.harness import EvalMetric, EvalReport, Json, JsonObject
from evals.harness.registry import EvalTask, capability_task, rewrapped
from evals.harness.target import CapabilityTarget
from evals.suites.app_audit_probe import app_audit

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "ufo_app_qa_replay"
APP_ROOT = "/workspace/ufo-app"
SOURCE_PATH = f"{APP_ROOT}/app.tsx"
PREVIEW_PATH = f"{APP_ROOT}/preview.svg"
OUTPUT_ROOT = "/workspace/.eval-output/ufo-app-qa-replay"
APPLICATION_INDEX = (APPLICATION_TEMPLATE_DIR / "index.html").read_bytes()
PROBE_TIMEOUT_SECONDS = BUILD_TIMEOUT_SECONDS + APPLICATION_AUDIT_TIMEOUT_SECONDS
PROBE_ERROR_DETAIL_CHARS = 500
PROBE_WRITE_TIMEOUT_SECONDS = 30
WORKFLOW_WAIT_SECONDS = 300.0
REPAIR_AGENT = "app-qa-repair"
ALLOWED_TOOLS = frozenset({"read", "edit"})


class ExpectedFeedback(BaseModel):
    model_config = ConfigDict(frozen=True)

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    issues: tuple[ApplicationAuditIssue, ...] = Field(min_length=1)


class FixtureProvenance(BaseModel):
    model_config = ConfigDict(frozen=True)

    run: str
    run_sha256: str = Field(alias="runSha256", pattern=r"^[0-9a-f]{64}$")
    arm: str
    case: str
    attempt: int
    source_call: int = Field(alias="sourceCall")
    preview_call: int = Field(alias="previewCall")
    feedback_call: int = Field(alias="feedbackCall")
    source_sha256: str = Field(alias="sourceSha256", pattern=r"^[0-9a-f]{64}$")
    preview_sha256: str = Field(alias="previewSha256", pattern=r"^[0-9a-f]{64}$")
    contract_sha256: str = Field(alias="contractSha256", pattern=r"^[0-9a-f]{64}$")
    data_sha256: str = Field(alias="dataSha256", pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ReplayFixture:
    name: str
    source: bytes
    preview: bytes
    contract: ApplicationAuditContract
    expected: ExpectedFeedback
    provenance: FixtureProvenance

    @classmethod
    def load(cls, name: str) -> "ReplayFixture":
        root = FIXTURE_ROOT / name
        source = (root / "app.tsx").read_bytes()
        preview = (root / "preview.svg").read_bytes()
        provenance = FixtureProvenance.model_validate_json((root / "provenance.json").read_bytes())
        expected = ExpectedFeedback.model_validate_json(
            (root / "expected-initial-feedback.json").read_bytes()
        )
        contract_bytes = (root / "contract.json").read_bytes()
        if provenance.case != name:
            raise ValueError(f"fixture {name} provenance names {provenance.case}")
        if sha256(source).hexdigest() != provenance.source_sha256:
            raise ValueError(f"fixture {name} source SHA-256 does not match provenance")
        if sha256(preview).hexdigest() != provenance.preview_sha256:
            raise ValueError(f"fixture {name} preview SHA-256 does not match provenance")
        if sha256(contract_bytes).hexdigest() != provenance.contract_sha256:
            raise ValueError(f"fixture {name} contract SHA-256 does not match provenance")
        if issue_fingerprint(expected.issues) != expected.sha256:
            raise ValueError(f"fixture {name} feedback SHA-256 does not match its issues")
        return cls(
            name=name,
            source=source,
            preview=preview,
            contract=ApplicationAuditContract.model_validate_json(contract_bytes),
            expected=expected,
            provenance=provenance,
        )


class ReplayEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    phase: Literal["initial", "final"]
    source_sha256: str = Field(alias="sourceSha256", pattern=r"^[0-9a-f]{64}$")
    feedback_sha256: str = Field(alias="feedbackSha256", pattern=r"^[0-9a-f]{64}$")
    issues: tuple[ApplicationAuditIssue, ...]
    compile_ms: int = Field(alias="compileMs", ge=0)
    audit_ms: int = Field(alias="auditMs", ge=0)
    agent_ms: int = Field(alias="agentMs", ge=0)
    agent_response: str = Field(alias="agentResponse")
    agent_calls: int = Field(alias="agentCalls", ge=0)
    repair_turn: int = Field(default=0, alias="repairTurn", ge=0)


def issue_fingerprint(issues: tuple[ApplicationAuditIssue, ...]) -> str:
    payload = [issue.model_dump(mode="json") for issue in issues]
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


@dataclass
class RepairTurns:
    """The repair turns one replay conversation has admitted, which the followup counts and the
    final probe records: a failed probe writes no evidence, so the evidence a turn left behind
    cannot name the turn. The conversation's workspace directory identifies the run, so a repeated
    sample counts from its own first turn."""

    workspace: Path | None = None
    turn: int = 0

    def next_turn(self, workspace: Path | None) -> int:
        if workspace != self.workspace:
            self.workspace = workspace
            self.turn = 0
        return self.turn + 1

    def admit(self) -> None:
        self.turn += 1


@dataclass(frozen=True)
class AppQaReplayProbe:
    fixture: ReplayFixture
    phase: Literal["initial", "final"]
    turns: RepairTurns

    async def __call__(
        self, output: CapabilityOutput, probe: WorkspaceProbe
    ) -> ArtifactProbeResult:
        if output.workspace_dir is None:
            return ArtifactProbeResult(error="app QA replay probe has no workspace")
        name = f"{self.fixture.name}-{self.phase}"
        relative = Path(".eval-output/ufo-app-qa-replay") / self.fixture.name / self.phase
        directory = output.workspace_dir / relative
        evidence_path = directory / f"{name}-evidence.json"
        audit = app_audit(
            name=name,
            output_dir=f"{OUTPUT_ROOT}/{self.fixture.name}/{self.phase}",
            project=APP_ROOT,
            # The subject here is repairing a page against live audits, so the audit measures the
            # page alone. The captured wireframe stays beside it as the record of the run it came
            # from, unedited: re-cutting it to today's lane would falsify the provenance that names
            # the run, arm and call which produced these bytes.
            design_path=None,
            compile_source=True,
        )
        result = await probe.run(audit.command, PROBE_TIMEOUT_SECONDS)
        if result.exit_code != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "no command output"
            return ArtifactProbeResult(
                error=f"app QA replay probe failed: {detail[:PROBE_ERROR_DETAIL_CHARS]}"
            )
        paths = tuple(directory / output_name for output_name in audit.outputs)
        missing = tuple(path.name for path in paths if not path.is_file())
        if missing:
            return ArtifactProbeResult(
                error=f"app QA replay probe produced no {', '.join(missing)}"
            )
        report_path = directory / f"{name}-audit.json"
        timing_path = directory / "timing.json"
        source_path = output.workspace_dir / "ufo-app" / "app.tsx"
        contents = await asyncio.gather(
            *(asyncio.to_thread(path.read_bytes) for path in (*paths, source_path))
        )
        captured = dict(zip(paths, contents[:-1], strict=True))
        report = ApplicationAuditReport.model_validate_json(captured[report_path])
        issues = audit_application(report, self.fixture.contract).issues
        timing = json.loads(captured[timing_path])
        evidence = ReplayEvidence(
            phase=self.phase,
            sourceSha256=sha256(contents[-1]).hexdigest(),
            feedbackSha256=issue_fingerprint(issues),
            issues=issues,
            compileMs=timing["compileMs"],
            auditMs=timing["auditMs"],
            agentMs=output.timing.wall_ms if output.timing is not None else 0,
            agentResponse=output.response,
            agentCalls=len(output.calls),
            repairTurn=0 if self.phase == "initial" else self.turns.turn,
        )
        evidence_bytes = evidence.model_dump_json(by_alias=True).encode()
        sandbox_evidence_path = (
            f"{OUTPUT_ROOT}/{self.fixture.name}/{self.phase}/{evidence_path.name}"
        )
        encoded_evidence = base64.b64encode(evidence_bytes).decode()
        written = await probe.run(
            f"printf %s {shlex.quote(encoded_evidence)} | base64 -d > "
            f"{shlex.quote(sandbox_evidence_path)}",
            PROBE_WRITE_TIMEOUT_SECONDS,
        )
        if written.exit_code != 0:
            detail = written.stderr.strip() or written.stdout.strip() or "no command output"
            return ArtifactProbeResult(
                error=f"app QA replay evidence write failed: {detail[:PROBE_ERROR_DETAIL_CHARS]}"
            )
        prior_evidence: tuple[SharedArtifact, ...] = ()
        if self.phase == "final":
            initial_name = f"{self.fixture.name}-initial-evidence.json"
            initial_path = output.workspace_dir / relative.parent / "initial" / initial_name
            try:
                initial_bytes = await asyncio.to_thread(initial_path.read_bytes)
            except FileNotFoundError:
                return ArtifactProbeResult(error=f"app QA replay probe produced no {initial_name}")
            prior_evidence = (SharedArtifact(initial_name, initial_bytes),)
        artifacts = tuple(
            SharedArtifact(
                f"{name}-timing.json" if path == timing_path else path.name,
                captured[path],
            )
            for path in paths
        )
        return ArtifactProbeResult(
            artifacts=(
                *prior_evidence,
                *artifacts,
                SharedArtifact(evidence_path.name, evidence_bytes),
            )
        )


def _evidence(output: CapabilityOutput, fixture: ReplayFixture, phase: str) -> ReplayEvidence:
    name = f"{fixture.name}-{phase}-evidence.json"
    found = tuple(artifact for artifact in output.artifacts if artifact.name == name)
    if len(found) != 1:
        detail = (
            f": {output.artifact_error[:PROBE_ERROR_DETAIL_CHARS]}" if output.artifact_error else ""
        )
        raise ValueError(f"replay captured {len(found)} {phase} evidence artifacts{detail}")
    return ReplayEvidence.model_validate_json(found[0].content)


def _repair_followup(fixture: ReplayFixture, turns: RepairTurns):
    async def followup(output: CapabilityOutput) -> str | None:
        turn = turns.next_turn(output.workspace_dir)
        if turn == 1:
            try:
                evidence = _evidence(output, fixture, "initial")
            except ValueError:
                return None
            if evidence.feedback_sha256 != fixture.expected.sha256:
                raise RuntimeError(
                    f"{fixture.name} initial feedback moved from {fixture.expected.sha256} "
                    f"to {evidence.feedback_sha256}"
                )
            if fixture.name == "pre-meeting-briefs":
                instruction = (
                    "Repair every live deterministic issue below in /workspace/ufo-app/app.tsx. "
                    "Read only the current file. For contrast, pair a fixed light background with "
                    "an explicit dark foreground and pair a theme background with a theme "
                    "foreground. For overflow, keep every region in its original order and keep "
                    "its display and layout direction; reduce an existing width, minimum width, "
                    "padding, or gap, or add minWidth: 0. Before every edit call, read the current "
                    "file, copy each old string from that latest read, include unique surrounding "
                    "JSX, and submit one edit at a time. Pass edits as a JSON array. Never use "
                    "replace_all. Do not replace a fixed background with a theme variable or read "
                    "or change another file."
                )
            else:
                instruction = (
                    "Repair only the live deterministic issues below in "
                    "/workspace/ufo-app/app.tsx. Read that file before exact edits. Never use "
                    "replace_all. Do not change another file."
                )
        else:
            final_name = f"{fixture.name}-final-evidence.json"
            final_artifacts = tuple(
                artifact for artifact in output.artifacts if artifact.name == final_name
            )
            if len(final_artifacts) > 1:
                raise RuntimeError(
                    f"replay captured {len(final_artifacts)} final evidence artifacts"
                )
            if not final_artifacts:
                return None
            evidence = ReplayEvidence.model_validate_json(final_artifacts[0].content)
            if not evidence.issues:
                return None
            issue_codes = {issue.code for issue in evidence.issues}
            instructions = [
                "Repair every remaining deterministic issue below in the current "
                "/workspace/ufo-app/app.tsx. Read only the current file before exact edits. Fix "
                "every listed issue."
            ]
            if "contrast" in issue_codes:
                instructions.append(
                    "For contrast, pair a fixed light background with an explicit dark foreground "
                    "and pair a theme background with a theme foreground. Before every edit call, "
                    "read the current file, copy each old string from that latest read, include "
                    "unique surrounding JSX, and submit one edit at a time."
                )
            if "overflow" in issue_codes:
                instructions.append(
                    "For overflow, keep every region in its original order and keep its display "
                    "and layout direction; reduce an existing width, minimum width, padding, or "
                    "gap, or add minWidth: 0."
                )
            instruction = " ".join(instructions) + (
                " Never use replace_all. Do not replace a fixed background with a theme variable "
                "or read or change another file."
            )
        issues = [issue.model_dump(mode="json") for issue in evidence.issues]
        turns.admit()
        return (
            instruction
            + "\n\n"
            + json.dumps({"issues": issues}, sort_keys=True, separators=(",", ":"))
        )

    return followup


def _replay_grader(fixture: ReplayFixture) -> DescribedGrader[CapabilityOutput]:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        try:
            initial = _evidence(output, fixture, "initial")
            final = _evidence(output, fixture, "final")
        except ValueError as error:
            return CapabilityVerdict(False, str(error))
        repeated = len(output.calls) - len(
            {(call.name, json.dumps(call.input, sort_keys=True)) for call in output.calls}
        )
        forbidden = tuple(call.name for call in output.calls if call.name not in ALLOWED_TOOLS)
        failed = sum(call.is_error for call in output.calls)
        repair_turns = max(final.repair_turn, 1)
        edit_call_limit = APP_QA_EDIT_CALL_LIMIT * repair_turns
        edit_old_bytes_limit = APP_QA_EDIT_OLD_BYTES_LIMIT * repair_turns
        edit_new_bytes_limit = APP_QA_EDIT_NEW_BYTES_LIMIT * repair_turns
        paths: list[str] = []
        edit_paths: list[str] = []
        replace_all = False
        invalid_edit_payload = False
        old_string_bytes = 0
        new_string_bytes = 0
        for call in output.calls:
            path = call.input.get("file_path")
            if call.name in ALLOWED_TOOLS and isinstance(path, str):
                paths.append(path)
            if call.name == "edit" and isinstance(path, str):
                edit_paths.append(path)
            raw_edits = call.input.get("edits")
            if call.name != "edit":
                continue
            if not isinstance(raw_edits, list):
                invalid_edit_payload = True
                continue
            for edit in raw_edits:
                if not isinstance(edit, dict):
                    invalid_edit_payload = True
                    continue
                replace_all = replace_all or edit.get("replace_all") is True
                old_string = edit.get("old_string")
                new_string = edit.get("new_string")
                if not isinstance(old_string, str) or not isinstance(new_string, str):
                    invalid_edit_payload = True
                    continue
                old_string_bytes += len(old_string.encode())
                new_string_bytes += len(new_string.encode())
        reads = sum(call.name == "read" for call in output.calls)
        edits = sum(call.name == "edit" for call in output.calls)
        final_codes = {issue.code for issue in final.issues}
        changed_paths: list[Json] = []
        changed_paths.extend(sorted(set(edit_paths)))
        failures = []
        if initial.feedback_sha256 != fixture.expected.sha256:
            failures.append("initial feedback fingerprint moved")
        if initial.agent_response.strip() != "READY" or initial.agent_calls:
            failures.append("warm-up did not reply only READY without tools")
        if final.issues:
            failures.append(f"final audit has {len(final.issues)} issue(s)")
        if initial.source_sha256 == final.source_sha256:
            failures.append("repair did not change app.tsx")
        if forbidden:
            failures.append(f"used forbidden tools: {', '.join(forbidden)}")
        if any(path != SOURCE_PATH for path in paths):
            failures.append("read or edited a path other than app.tsx")
        if replace_all:
            failures.append("used replace_all")
        if invalid_edit_payload:
            failures.append("used an invalid edit payload")
        if edits > edit_call_limit:
            failures.append(f"exceeded edit call limit {edit_call_limit}")
        if old_string_bytes > edit_old_bytes_limit:
            failures.append(f"exceeded old_string byte limit {edit_old_bytes_limit}")
        if new_string_bytes > edit_new_bytes_limit:
            failures.append(f"exceeded new_string byte limit {edit_new_bytes_limit}")
        if reads == 0:
            failures.append("did not read app.tsx")
        if edits == 0:
            failures.append("did not edit app.tsx")
        if failed:
            failures.append(f"made {failed} failed tool call(s)")
        evidence: JsonObject = {
            "initialSourceSha256": initial.source_sha256,
            "finalSourceSha256": final.source_sha256,
            "initialFeedbackSha256": initial.feedback_sha256,
            "expectedInitialFeedbackSha256": fixture.expected.sha256,
            "warmupMs": initial.agent_ms,
            "initialCompileMs": initial.compile_ms,
            "initialAuditMs": initial.audit_ms,
            "repairMs": final.agent_ms,
            "finalCompileMs": final.compile_ms,
            "finalAuditMs": final.audit_ms,
            "turns": final.repair_turn + 1,
            "repairTurns": repair_turns,
            "editCallLimit": edit_call_limit,
            "editOldBytesLimit": edit_old_bytes_limit,
            "editNewBytesLimit": edit_new_bytes_limit,
            "reads": reads,
            "edits": edits,
            "oldStringBytes": old_string_bytes,
            "newStringBytes": new_string_bytes,
            "failedCalls": failed,
            "repeatedCalls": repeated,
            "forbiddenCalls": list(forbidden),
            "changedPaths": changed_paths,
            "finalIssues": [issue.model_dump(mode="json") for issue in final.issues],
            "compilePassed": True,
            "interactionPassed": not bool(final_codes & {"controls", "interaction"}),
            "factsPassed": not bool(final_codes & {"fact", "above_fold"}),
            "aboveFoldPassed": "above_fold" not in final_codes,
            "contrastPassed": "contrast" not in final_codes,
        }
        if failures:
            return CapabilityVerdict(False, "; ".join(failures), evidence)
        return CapabilityVerdict(True, "the bounded repair passed the live final audit", evidence)

    return DescribedGrader(
        "the retained source receives only bounded app.tsx edits and passes the live final audit",
        grade,
    )


FIXTURES = tuple(ReplayFixture.load(name) for name in ("pre-meeting-briefs", "issue-owner"))


def _case(fixture: ReplayFixture) -> CapabilityCase:
    turns = RepairTurns()
    return CapabilityCase(
        name=fixture.name,
        message="Start the fixed application QA replay. Reply only READY without tools.",
        grader=_replay_grader(fixture),
        digest_tag=(
            f"ufo-app-qa-replay:{fixture.name}:{fixture.provenance.source_sha256}:"
            f"{fixture.provenance.contract_sha256}:{fixture.expected.sha256}"
        ),
        workspace_files=(
            WorkspaceFile("ufo-app/index.html", APPLICATION_INDEX),
            WorkspaceFile("ufo-app/preview.html", PROJECT_PREVIEW_BYTES),
            WorkspaceFile("ufo-app/app.tsx", fixture.source),
            WorkspaceFile("ufo-app/preview.svg", fixture.preview),
            WorkspaceFile("ufo-app/vite.config.ts", PROJECT_CONFIG_BYTES),
        ),
        prepare=_prepare_kit,
        artifact_probe=AppQaReplayProbe(fixture, "initial", turns),
        followup=_repair_followup(fixture, turns),
        followup_turns=2,
        followup_artifact_probe=AppQaReplayProbe(fixture, "final", turns),
    )


async def _prepare_kit(_workspace_id: UUID, workspace: Path) -> None:
    if not (KIT_DIR / "kit.js").is_file():
        raise RuntimeError("app QA replay requires the built application kit")
    await asyncio.to_thread(
        shutil.copytree,
        KIT_DIR,
        workspace / "ufo-app" / "sdk",
        dirs_exist_ok=True,
    )


CASES = tuple(_case(fixture) for fixture in FIXTURES)
RATE_FIELDS = (
    ("compile_pass_rate", "compilePassed"),
    ("interaction_pass_rate", "interactionPassed"),
    ("facts_pass_rate", "factsPassed"),
    ("above_fold_pass_rate", "aboveFoldPassed"),
    ("contrast_pass_rate", "contrastPassed"),
)


def _metrics(report: EvalReport) -> EvalReport:
    eligible = tuple(case for case in report.cases if not case.excluded)
    records: list[JsonObject | None] = []
    for case in eligible:
        grader: JsonObject | None = None
        selected = case.evidence.get("selectedAttempt")
        attempts = case.evidence.get("attempts")
        if isinstance(selected, int) and isinstance(attempts, list):
            attempt = attempts[selected]
            if isinstance(attempt, dict):
                selected_grader = attempt.get("grader")
                if isinstance(selected_grader, dict):
                    grader = selected_grader
        records.append(grader)
    metrics = []
    for name, field in RATE_FIELDS:
        passed = sum(record is not None and record.get(field) is True for record in records)
        metrics.append(EvalMetric(name=name, value=passed / len(records) if records else 0.0))
    hard_pass = sum(case.passed for case in eligible) / len(eligible) if eligible else 0.0
    return report.model_copy(
        update={
            "metrics": (
                *report.metrics,
                *metrics,
                EvalMetric(name="hard_pass_rate", value=hard_pass),
            )
        }
    )


def scored_task(task: EvalTask) -> EvalTask:
    async def run(target: CapabilityTarget, slots: asyncio.Semaphore) -> EvalReport:
        return _metrics(await task.run(target, slots))

    return replace(task, run=run)


TASK = rewrapped(
    capability_task(
        "ufo-app-qa-replay",
        CASES,
        serial=True,
        packs=("assistant_eval",),
        agent=REPAIR_AGENT,
        wait_seconds=WORKFLOW_WAIT_SECONDS,
    ),
    scored_task,
)
