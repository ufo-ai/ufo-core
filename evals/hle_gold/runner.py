"""HLE-Verified Gold sentinels materialized from a pinned operator-supplied corpus."""

from __future__ import annotations

import ast
import asyncio
import base64
import json
import re
import unicodedata
from dataclasses import dataclass
from functools import partial
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    WorkspaceFile,
    run_capability_case,
)
from evals.harness.harness import EvalMetric, EvalReport, JsonObject, digest_payload
from evals.harness.registry import EvalTask, gather_cases
from evals.harness.target import CapabilityTarget

SOURCE_REVISION = "b705e0fb541c025a1532ce0d60d70ae2f53b00e0"
SOURCE_SHA256 = "f03c2632400970ffe5167d5ecde169ad38d34ce4dceb2eb8cfb54ece9c5fae00"
SOURCE_ROWS = 668
SELECTION_CASE_COUNT = 100
SELECTION_SMOKE_COUNT = 3
SELECTION_PATH = Path(__file__).with_name("selection.json")
RESPONSE_FORMAT = "Include exactly once:\nANSWER: <answer>\nCONFIDENCE: <0-100>%"
WEB_TOOLS = frozenset({"search_web", "search_vertical", "fetch_url", "browser_task"})
VISION_FORBIDDEN_TOOLS = frozenset(
    {
        "bash",
        "write",
        "edit",
        "search_web",
        "search_vertical",
        "fetch_url",
        "browser_task",
        "wide_browse",
        "spawn",
    }
)
CONTAMINATION_MARKERS = (
    "huggingface.co/datasets",
    "hle-verified",
    "humanity's last exam",
    "humanitys-last-exam",
)
COMPACTION_WORDS = "river stone cedar orbit lantern meadow copper harbor velvet winter "
COMPACTION_HEAD = (COMPACTION_WORDS * 20_000)[:126_000]
COMPACTION_TAIL = (COMPACTION_WORDS * 8_000)[:3_000]

type HLELeaf = Literal[
    "compaction_retain",
    "codegen_solver",
    "vision_read",
    "web_search_fetch",
    "sandbox_compute",
    "tool_restraint",
]

BEHAVIOR_CRITERIA: dict[HLELeaf, str] = {
    "compaction_retain": (
        "the turn compacts at least once and the response repeats the retention token"
    ),
    "codegen_solver": (
        "a non-trivial solver file is written and then executed with the answer in its "
        "output, without web tools"
    ),
    "vision_read": "the supplied image is read exactly once and no forbidden tool is used",
    "web_search_fetch": (
        "web research completes without benchmark-contaminated sources or exact-question queries"
    ),
    "sandbox_compute": "bash computes the answer without web tools",
    "tool_restraint": "no tool is called",
}


class ManifestCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(pattern=r"^[0-9a-f]{24}$")
    leaf: HLELeaf
    record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    smoke: bool = False


class SelectionManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_revision: str
    source_sha256: str
    source_rows: int
    cases: tuple[ManifestCase, ...]


class VerifyResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_valid: int
    error_type: int | str
    error_description: str | None = None
    error_type_verify_reason: str | None = None


class VerifyMeta(BaseModel):
    problem_verify: VerifyResult
    answer_verify: VerifyResult
    rationale_verify: VerifyResult


class EncodedBytes(BaseModel):
    value: str = Field(alias="__base64__")


class ImagePreview(BaseModel):
    path: None
    bytes: EncodedBytes


class GoldRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[0-9a-f]{24}$")
    question: str
    image: str
    image_preview: ImagePreview | None
    answer: str
    answer_type: Literal["exactMatch", "multipleChoice"]
    author_name: str
    rationale: str
    rationale_image: ImagePreview | None
    raw_subject: str
    category: str
    canary: str
    verify_meta_info: VerifyMeta
    Verified_Classes: Literal["Gold subset"]
    original_question: str | None = None
    original_rationale: str | None = None


class HLEGraderEvidence(BaseModel):
    answer: bool = False
    behavior: bool = False
    format: bool = False
    confidence: int = Field(default=0, ge=0, le=100)


class HLERecordedAttempt(BaseModel):
    grader: HLEGraderEvidence | None = None


class HLERecordedEvidence(BaseModel):
    selected_attempt: int = Field(alias="selectedAttempt", ge=0)
    attempts: tuple[HLERecordedAttempt, ...]


@dataclass(frozen=True)
class HLEGoldRun:
    tasks: tuple[EvalTask, ...]


def load_hle_gold(source: Path, smoke: bool = False) -> HLEGoldRun:
    manifest = SelectionManifest.model_validate_json(SELECTION_PATH.read_text())
    _validate_manifest(manifest)
    selected = tuple(item for item in manifest.cases if item.smoke or not smoke)
    wanted = {item.id: item for item in selected}
    records: dict[str, GoldRecord] = {}
    digest = sha256()
    rows = 0
    with source.open("rb") as handle:
        for raw in handle:
            rows += 1
            digest.update(raw)
            body = json.loads(raw)
            item_id = body.get("id")
            if item_id not in wanted:
                continue
            record = GoldRecord.model_validate(body)
            canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
            if sha256(canonical).hexdigest() != wanted[record.id].record_sha256:
                raise ValueError(f"HLE Gold record {record.id} does not match the selection")
            _validate_record(record)
            records[record.id] = record
    if digest.hexdigest() != manifest.source_sha256 or rows != manifest.source_rows:
        raise ValueError("HLE Gold source does not match the pinned asset")
    missing = wanted.keys() - records.keys()
    if missing:
        raise ValueError(f"HLE Gold source is missing selected IDs: {', '.join(sorted(missing))}")
    by_leaf: dict[HLELeaf, list[CapabilityCase]] = {}
    for item in selected:
        by_leaf.setdefault(item.leaf, []).append(_capability_case(item, records[item.id]))
    tasks = tuple(_leaf_task(leaf, tuple(cases)) for leaf, cases in by_leaf.items() if cases)
    return HLEGoldRun(tasks)


def _validate_manifest(manifest: SelectionManifest) -> None:
    if (
        manifest.source_revision != SOURCE_REVISION
        or manifest.source_sha256 != SOURCE_SHA256
        or manifest.source_rows != SOURCE_ROWS
    ):
        raise ValueError("HLE Gold manifest source pin is invalid")
    ids = [item.id for item in manifest.cases]
    if len(ids) != SELECTION_CASE_COUNT or len(set(ids)) != SELECTION_CASE_COUNT:
        raise ValueError(f"HLE Gold selection must contain {SELECTION_CASE_COUNT} unique IDs")
    if sum(item.smoke for item in manifest.cases) != SELECTION_SMOKE_COUNT:
        raise ValueError(f"HLE Gold selection must contain {SELECTION_SMOKE_COUNT} smoke cases")


def _validate_record(record: GoldRecord) -> None:
    verified = record.verify_meta_info
    if any(
        check.is_valid != 1
        for check in (verified.problem_verify, verified.answer_verify, verified.rationale_verify)
    ):
        raise ValueError(f"HLE Gold record {record.id} failed strict verification")
    replaced_fields = {"original_question", "original_rationale"}.intersection(
        record.model_fields_set
    )
    if replaced_fields:
        raise ValueError(f"HLE Gold record {record.id} contains replaced source material")


def _capability_case(item: ManifestCase, record: GoldRecord) -> CapabilityCase:
    path = f"hle/{record.id}"
    fixtures: tuple[WorkspaceFile, ...] = ()
    if item.leaf == "vision_read":
        media_type, encoded = record.image.split(",", 1)
        suffix = {
            "data:image/jpeg;base64": ".jpg",
            "data:image/png;base64": ".png",
        }.get(media_type)
        if suffix is None:
            raise ValueError(f"HLE Gold vision record {record.id} has an unsupported image type")
        path += suffix
        fixtures = (WorkspaceFile(path, base64.b64decode(encoded)),)
    elif record.image:
        raise ValueError(f"HLE Gold non-vision record {record.id} has an image")
    message = _message(item, record, path)
    return CapabilityCase(
        name=f"hle_gold.{item.leaf}.{record.id}",
        message=message,
        grader=_grader(item, record, path),
        web_dependent=item.leaf == "web_search_fetch",
        digest_tag=f"hle-gold:{item.id}:{item.record_sha256}",
        workspace_files=fixtures,
        prior_messages=_compaction_messages(record.id) if item.leaf == "compaction_retain" else (),
    )


def _message(item: ManifestCase, record: GoldRecord, path: str) -> str:
    wrapper = {
        "compaction_retain": (
            "Answer the question and add the retention token from the earlier conversation after "
            "the confidence line in the form RETAINED: <token>."
        ),
        "codegen_solver": (
            f"Solve by writing Python to /workspace/hle/{record.id}.py and executing that file."
        ),
        "vision_read": (
            f"Call read once on /workspace/{path}, inspect the image, and solve it directly. "
            "Do not use bash, write or edit files, search the web, browse, or delegate."
        ),
        "web_search_fetch": (
            "Research independent sources before answering. Do not search the exact question or "
            "use HLE, Hugging Face, GitHub benchmark repositories, or dataset mirrors."
        ),
        "sandbox_compute": "Use bash or Python in the sandbox to calculate the answer.",
        "tool_restraint": "Answer directly without tools.",
    }[item.leaf]
    return f"{wrapper}\n\n{record.question}\n\n{RESPONSE_FORMAT}"


def _compaction_messages(item_id: str) -> tuple[str, ...]:
    head = tuple(
        (
            f"Persistent instruction: retain RETENTION-{item_id} for the next answer. "
            if index == 0
            else "Continue retaining the instruction. "
        )
        + COMPACTION_HEAD
        for index in range(8)
    )
    tail = tuple("Acknowledge the retained instruction. " + COMPACTION_TAIL for _ in range(8))
    return (*head, *tail)


def _grader(item: ManifestCase, record: GoldRecord, path: str):
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        confidence_matches = re.findall(r"^CONFIDENCE:\s*(\d{1,3})%\s*$", output.response, re.M)
        answer_matches = re.findall(r"^ANSWER:\s*(.+?)\s*$", output.response, re.M)
        confidence = int(confidence_matches[0]) if len(confidence_matches) == 1 else 0
        formatted = len(confidence_matches) == 1 and confidence <= 100 and len(answer_matches) == 1
        answer = answer_matches[0].strip() if len(answer_matches) == 1 else ""
        answer_ok = _answers_match(answer, record.answer, record.answer_type)
        behavior, reason = _behavior(item, output, path, record.question, answer)
        evidence: JsonObject = {
            "id": record.id,
            "leaf": item.leaf,
            "category": record.category,
            "rawSubject": record.raw_subject,
            "expectedAnswer": record.answer,
            "answerType": record.answer_type,
            "format": formatted,
            "answer": answer_ok,
            "behavior": behavior,
            "confidence": min(confidence, 100),
            "toolCount": len(output.calls),
            "compactions": output.compactions,
        }
        if not formatted:
            return CapabilityVerdict(False, "missing ANSWER or valid CONFIDENCE", evidence)
        if not answer_ok:
            return CapabilityVerdict(False, "answer did not match", evidence)
        return CapabilityVerdict(behavior, reason, evidence)

    return DescribedGrader(
        "the response carries exactly one ANSWER and one CONFIDENCE line; ANSWER matches the "
        f"gold {record.answer_type} answer; {BEHAVIOR_CRITERIA[item.leaf]}",
        grade,
    )


def _answers_match(candidate: str, expected: str, answer_type: str) -> bool:
    if answer_type == "multipleChoice":
        return candidate.strip().casefold() == expected.strip().casefold()
    normalized_candidate = _normalize_answer(candidate)
    normalized_expected = _normalize_answer(expected)
    if not normalized_candidate:
        return False
    if normalized_candidate == normalized_expected:
        return True
    if re.fullmatch(r"[\w ]+", normalized_expected):
        contains = re.search(rf"\b{re.escape(normalized_expected)}\b", normalized_candidate)
        if contains is None:
            return False
        remainder = (
            normalized_candidate[: contains.start()] + normalized_candidate[contains.end() :]
        )
        ambiguous = re.search(r"\b(?:not|and|or|versus|vs)\b|;", remainder)
        return ambiguous is None
    return False


def _normalize_answer(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = normalized.replace("\\,", "").replace("\\text{", "").replace("}", "")
    normalized = normalized.replace("$", "").strip().strip("`\"'")
    return re.sub(r"\s+", " ", normalized).rstrip(".")


def _behavior(
    item: ManifestCase,
    output: CapabilityOutput,
    path: str,
    question: str,
    answer: str,
) -> tuple[bool, str]:
    successful = [call for call in output.calls if call.succeeded]
    names = {call.name for call in successful}
    match item.leaf:
        case "web_search_fetch":
            if not names.intersection(WEB_TOOLS):
                return False, "completed no web research"
            question_text = " ".join(question.casefold().split())
            contaminated = any(
                marker in f"{call.input} {call.result}".casefold()
                for call in output.calls
                for marker in CONTAMINATION_MARKERS
            )
            exact_query = any(
                question_text in " ".join(str(call.input).casefold().split())
                for call in output.calls
                if call.name in WEB_TOOLS
            )
            valid = not contaminated and not exact_query
            reason = "independent web research" if valid else "used a prohibited web query"
            return valid, reason
        case "sandbox_compute":
            valid = "bash" in names and not names.intersection(WEB_TOOLS)
            return valid, "sandbox computation" if valid else "required bash without web"
        case "codegen_solver":
            solver_file = f"{item.id}.py"
            write = next(
                (
                    call
                    for call in successful
                    if call.name == "write"
                    and call.input.get("file_path") == f"/workspace/hle/{solver_file}"
                ),
                None,
            )
            write_index = output.calls.index(write) if write is not None else len(output.calls)
            run = next(
                (
                    call
                    for index, call in enumerate(output.calls)
                    if index > write_index
                    and call.name == "bash"
                    and call.succeeded
                    and solver_file in str(call.input.get("command", ""))
                ),
                None,
            )
            source = str(write.input.get("content", "")) if write is not None else ""
            valid = (
                write is not None
                and _solver_source_valid(source)
                and run is not None
                and _normalize_answer(answer) in _normalize_answer(run.result)
                and not names.intersection(WEB_TOOLS)
            )
            reason = "wrote and ran a solver" if valid else "required an executed solver"
            return valid, reason
        case "vision_read":
            reads = [call for call in successful if call.name == "read" and path in str(call.input)]
            forbidden = VISION_FORBIDDEN_TOOLS.intersection(output.tools)
            valid = len(reads) == 1 and not forbidden
            reason = "read the supplied image" if valid else "required the image without web"
            return valid, reason
        case "tool_restraint":
            valid = not output.calls
            return valid, "answered directly" if valid else "used a tool on a restraint case"
        case "compaction_retain":
            retained = f"RETAINED: RETENTION-{item.id}" in output.response
            valid = output.compactions >= 1 and retained
            reason = (
                "retained through compaction"
                if valid
                else "required durable compaction and retention"
            )
            return valid, reason


def _solver_source_valid(source: str) -> bool:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    if not tree.body:
        return False
    match tree.body:
        case [ast.Expr(value=ast.Call(func=ast.Name(id="print"), args=[ast.Constant()]))]:
            return False
        case _:
            return True


def _leaf_task(leaf: HLELeaf, cases: tuple[CapabilityCase, ...]) -> EvalTask:
    name = f"hle_gold.{leaf}"
    digest = digest_payload(
        {"runner": "hle-gold", "task": name, "cases": [case.payload() for case in cases]}
    )

    async def run(target: CapabilityTarget, slots: asyncio.Semaphore) -> EvalReport:
        results = await gather_cases(
            slots, tuple(partial(run_capability_case, case, target) for case in cases)
        )
        confidence: list[float] = []
        format_passes = 0
        behavior_passes = 0
        answer_passes: list[bool] = []
        scored = tuple(result for result in results if not result.excluded)
        for result in scored:
            recorded = HLERecordedEvidence.model_validate(result.evidence)
            grader = recorded.attempts[recorded.selected_attempt].grader or HLEGraderEvidence()
            confidence.append(grader.confidence / 100)
            format_passes += int(grader.format)
            behavior_passes += int(grader.behavior)
            answer_passes.append(grader.answer)
        count = len(scored)
        if not count:
            return EvalReport(name=name, suite="hle_gold", digest=digest, cases=results)
        brier = (
            sum(
                (probability - float(answer_passed)) ** 2
                for probability, answer_passed in zip(confidence, answer_passes, strict=True)
            )
            / count
        )
        metrics = (
            EvalMetric(name="accuracy", value=sum(answer_passes) / count),
            EvalMetric(name="calibration", value=1 - brier),
            EvalMetric(name="format_rate", value=format_passes / count),
            EvalMetric(name="tool_policy_rate", value=behavior_passes / count),
        )
        return EvalReport(
            name=name, suite="hle_gold", digest=digest, cases=results, metrics=metrics
        )

    return EvalTask(name, "hle_gold", digest, tuple(case.name for case in cases), run)
