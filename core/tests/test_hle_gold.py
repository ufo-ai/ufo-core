import asyncio
import json
from collections import Counter
from hashlib import sha256

import pytest

import evals.hle_gold.runner as hle_runner
from evals.__main__ import main as eval_main
from evals.harness.capability import (
    CapabilityOutput,
    EvalTrajectory,
    SharedArtifact,
    ToolInvocation,
    grading_statement,
    run_capability_case,
)
from evals.harness.harness import EvalMetric, EvalReport
from evals.harness.target import TargetResult
from evals.harness.viewer import VIEWER_PAGE
from evals.hle_gold.runner import (
    SELECTION_PATH,
    GoldRecord,
    HLERecordedEvidence,
    ManifestCase,
    SelectionManifest,
    _answers_match,
    _capability_case,
    _compaction_messages,
    _grader,
    _leaf_task,
    _message,
    _validate_manifest,
    _validate_record,
    load_hle_gold,
)
from ufo.sdk.models import Message


def _record(answer: str = "Poblet", answer_type: str = "exactMatch") -> GoldRecord:
    return GoldRecord.model_validate(
        {
            "id": "671ebaf92a7c16b748fd2709",
            "question": "Which place is intended?",
            "image": "",
            "image_preview": None,
            "answer": answer,
            "answer_type": answer_type,
            "author_name": "author",
            "rationale": "rationale",
            "rationale_image": None,
            "raw_subject": "History",
            "category": "Humanities/Social Science",
            "canary": "canary",
            "verify_meta_info": {
                "problem_verify": {"is_valid": 1, "error_type": 0},
                "answer_verify": {"is_valid": 1, "error_type": 0},
                "rationale_verify": {"is_valid": 1, "error_type": 0},
            },
            "Verified_Classes": "Gold subset",
        }
    )


def test_selection_is_one_hundred_unique_cases_grouped_by_leaf() -> None:
    manifest = SelectionManifest.model_validate_json(SELECTION_PATH.read_text())

    _validate_manifest(manifest)

    assert Counter(item.leaf for item in manifest.cases) == {
        "compaction_retain": 15,
        "codegen_solver": 15,
        "vision_read": 15,
        "web_search_fetch": 20,
        "sandbox_compute": 20,
        "tool_restraint": 15,
    }


def test_hle_run_requires_an_explicit_workspace() -> None:
    with pytest.raises(SystemExit):
        eval_main(["--hle-gold", "missing.jsonl"])


def test_load_hle_gold_enforces_pinned_asset_and_selected_ids(tmp_path, monkeypatch) -> None:
    records = tuple(_record().model_copy(update={"id": f"{index:024x}"}) for index in range(1, 4))
    bodies = tuple(
        record.model_dump(
            mode="json",
            by_alias=True,
            exclude={"original_question", "original_rationale"},
        )
        for record in records
    )
    raw = b"".join(json.dumps(body, separators=(",", ":")).encode() + b"\n" for body in bodies)
    source = tmp_path / "gold.jsonl"
    source.write_bytes(raw)
    source_digest = sha256(raw).hexdigest()
    cases = tuple(
        ManifestCase(
            id=f"{index:024x}",
            leaf="tool_restraint",
            record_sha256=(
                sha256(
                    json.dumps(bodies[index - 1], sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                if index <= len(bodies)
                else f"{index:064x}"
            ),
            smoke=index <= len(bodies),
        )
        for index in range(1, 101)
    )
    manifest = SelectionManifest(
        source_revision=hle_runner.SOURCE_REVISION,
        source_sha256=source_digest,
        source_rows=len(bodies),
        cases=cases,
    )
    selection = tmp_path / "selection.json"
    selection.write_text(manifest.model_dump_json())
    monkeypatch.setattr(hle_runner, "SELECTION_PATH", selection)
    monkeypatch.setattr(hle_runner, "SOURCE_SHA256", source_digest)
    monkeypatch.setattr(hle_runner, "SOURCE_ROWS", len(bodies))

    assert len(load_hle_gold(source, smoke=True).tasks) == 1

    tampered = tmp_path / "tampered.jsonl"
    tampered.write_bytes(b" " + raw)
    with pytest.raises(ValueError, match="pinned asset"):
        load_hle_gold(tampered, smoke=True)

    missing_raw = b"".join(raw.splitlines(keepends=True)[:-1])
    missing_digest = sha256(missing_raw).hexdigest()
    missing = tmp_path / "missing.jsonl"
    missing.write_bytes(missing_raw)
    selection.write_text(
        manifest.model_copy(
            update={"source_sha256": missing_digest, "source_rows": len(bodies) - 1}
        ).model_dump_json()
    )
    monkeypatch.setattr(hle_runner, "SOURCE_SHA256", missing_digest)
    monkeypatch.setattr(hle_runner, "SOURCE_ROWS", len(bodies) - 1)

    with pytest.raises(ValueError, match="missing selected IDs"):
        load_hle_gold(missing, smoke=True)


def test_strict_gold_validation_rejects_every_replaced_source_field() -> None:
    record = _record().model_copy(update={"original_question": None})

    with pytest.raises(ValueError, match="replaced source material"):
        _validate_record(record)


def test_answer_matching_is_strict_but_normalizes_presentation() -> None:
    assert _answers_match("`POBLET.`", "Poblet", "exactMatch")
    assert _answers_match("Royal Monastery of Santa Maria de Poblet", "Poblet", "exactMatch")
    assert _answers_match(
        "The Royal Monastery of Santa Maria de Poblet (its Golden Gate / Porta Daurada), "
        "in Catalonia, Spain",
        "Poblet",
        "exactMatch",
    )
    assert _answers_match("5.1 V", "$5.1 \\, \\text{V}$", "exactMatch")
    assert _answers_match("B", "b", "multipleChoice")
    assert not _answers_match("El Escorial", "Poblet", "exactMatch")
    assert not _answers_match("not Poblet", "Poblet", "exactMatch")
    assert not _answers_match("Poblet or El Escorial", "Poblet", "exactMatch")
    assert not _answers_match("Poblet and El Escorial", "Poblet", "exactMatch")
    assert not _answers_match("B and C", "B", "multipleChoice")


async def test_hle_grader_checks_format_and_leaf_tool_policy() -> None:
    record = _record("760")
    item = ManifestCase(
        id=record.id,
        leaf="sandbox_compute",
        record_sha256="a" * 64,
    )
    grader = _grader(item, record, "unused")

    passed = await grader(
        CapabilityOutput(
            "ANSWER: 760\nCONFIDENCE: 90%",
            (ToolInvocation("bash", {"cmd": "python solve.py"}, "760", True),),
        )
    )
    failed = await grader(CapabilityOutput("ANSWER: 760\nCONFIDENCE: 90%", ()))

    assert passed.passed
    assert passed.evidence["answer"] is True
    assert passed.evidence["expectedAnswer"] == record.answer
    assert passed.evidence["answerType"] == record.answer_type
    assert "bash computes the answer without web tools" in grading_statement(grader)
    assert failed.passed is False
    assert failed.evidence["behavior"] is False


async def test_hle_grader_requires_one_answer_and_percent_confidence() -> None:
    record = _record()
    item = ManifestCase(id=record.id, leaf="tool_restraint", record_sha256="9" * 64)
    grader = _grader(item, record, "unused")

    missing_percent = await grader(CapabilityOutput("ANSWER: Poblet\nCONFIDENCE: 80", ()))
    duplicate = await grader(
        CapabilityOutput("ANSWER: Poblet\nANSWER: Poblet\nCONFIDENCE: 80%", ())
    )

    assert missing_percent.passed is False
    assert missing_percent.evidence["format"] is False
    assert duplicate.passed is False
    assert duplicate.evidence["format"] is False


async def test_out_of_range_confidence_is_a_leaf_format_failure() -> None:
    record = _record()
    item = ManifestCase(id=record.id, leaf="tool_restraint", record_sha256="6" * 64)
    case = _capability_case(item, record)

    class Target:
        judge = None

        async def run(self, _case):
            return TargetResult(CapabilityOutput("ANSWER: Poblet\nCONFIDENCE: 150%", ()), True)

    report = await _leaf_task("tool_restraint", (case,)).run(Target(), asyncio.Semaphore(1))

    assert report.cases[0].passed is False
    assert report.metrics[2] == EvalMetric(name="format_rate", value=0)


async def test_compaction_grader_requires_durable_record_and_retained_token() -> None:
    record = _record()
    item = ManifestCase(id=record.id, leaf="compaction_retain", record_sha256="8" * 64)
    grader = _grader(item, record, "unused")
    response = f"ANSWER: Poblet\nCONFIDENCE: 90%\nRETAINED: RETENTION-{record.id}"

    passed = await grader(CapabilityOutput(response, (), compactions=1))
    missing_record = await grader(CapabilityOutput(response, ()))
    missing_token = await grader(
        CapabilityOutput("ANSWER: Poblet\nCONFIDENCE: 90%", (), compactions=1)
    )

    assert passed.passed
    assert missing_record.passed is False
    assert missing_token.passed is False


def test_vision_case_rejects_an_unsupported_image_type() -> None:
    record = _record().model_copy(update={"image": "data:image/webp;base64,eA=="})
    item = ManifestCase(id=record.id, leaf="vision_read", record_sha256="7" * 64)

    with pytest.raises(ValueError, match="unsupported image type"):
        _capability_case(item, record)


async def test_vision_grader_enforces_one_read_and_no_forbidden_tools() -> None:
    record = _record("Poblet")
    item = ManifestCase(id=record.id, leaf="vision_read", record_sha256="c" * 64)
    grader = _grader(item, record, f"hle/{record.id}.png")
    read = ToolInvocation("read", {"path": f"/workspace/hle/{record.id}.png"}, "image", True)

    passed = await grader(CapabilityOutput("ANSWER: Poblet\nCONFIDENCE: 80%", (read,)))
    repeated = await grader(CapabilityOutput("ANSWER: Poblet\nCONFIDENCE: 80%", (read, read)))
    forbidden = await grader(
        CapabilityOutput(
            "ANSWER: Poblet\nCONFIDENCE: 80%",
            (read, ToolInvocation("bash", {"cmd": "inspect"}, "ok", True)),
        )
    )

    assert passed.passed
    assert repeated.passed is False
    assert forbidden.passed is False


async def test_codegen_grader_requires_solver_source_and_post_write_execution() -> None:
    record = _record("760")
    item = ManifestCase(id=record.id, leaf="codegen_solver", record_sha256="1" * 64)
    grader = _grader(item, record, "unused")
    path = f"/workspace/hle/{record.id}.py"
    valid_write = ToolInvocation(
        "write",
        {"file_path": path, "content": "value = pow(2, 10)\nprint(760)\n"},
        "written",
        True,
    )
    literal_write = ToolInvocation(
        "write", {"file_path": path, "content": "print(760)\n"}, "written", True
    )
    run = ToolInvocation("bash", {"command": f"python {path}"}, "760", True)
    relative_run = ToolInvocation(
        "bash", {"command": f"cd /workspace/hle && python {record.id}.py"}, "760", True
    )
    other_run = ToolInvocation(
        "bash", {"command": "cd /workspace/hle && python other.py"}, "760", True
    )
    response = "ANSWER: 760\nCONFIDENCE: 90%"

    passed = await grader(CapabilityOutput(response, (valid_write, run)))
    relative = await grader(CapabilityOutput(response, (valid_write, relative_run)))
    literal = await grader(CapabilityOutput(response, (literal_write, run)))
    wrong_order = await grader(CapabilityOutput(response, (run, valid_write)))
    wrong_file = await grader(CapabilityOutput(response, (valid_write, other_run)))

    assert passed.passed
    assert relative.passed
    assert literal.passed is False
    assert wrong_order.passed is False
    assert wrong_file.passed is False


async def test_web_grader_rejects_exact_queries_and_benchmark_mirrors() -> None:
    record = _record()
    item = ManifestCase(id=record.id, leaf="web_search_fetch", record_sha256="d" * 64)
    grader = _grader(item, record, "unused")
    response = "ANSWER: Poblet\nCONFIDENCE: 80%"

    exact = await grader(
        CapabilityOutput(
            response,
            (ToolInvocation("search_web", {"queries": [record.question]}, "independent", True),),
        )
    )
    mirror = await grader(
        CapabilityOutput(
            response,
            (
                ToolInvocation(
                    "search_web",
                    {"queries": ["golden gate monastery"]},
                    "https://huggingface.co/datasets/example",
                    True,
                ),
            ),
        )
    )

    assert exact.passed is False
    assert mirror.passed is False


def test_compaction_prompt_does_not_repeat_the_retention_token() -> None:
    record = _record()
    item = ManifestCase(id=record.id, leaf="compaction_retain", record_sha256="e" * 64)

    message = _message(item, record, "unused")

    assert f"RETENTION-{record.id}" not in message
    assert f"RETENTION-{record.id}" in _compaction_messages(record.id)[0]


async def _private_case_result():
    record = _record("private-gold-answer", "multipleChoice")
    item = ManifestCase(
        id=record.id,
        leaf="tool_restraint",
        record_sha256="b" * 64,
    )
    case = _capability_case(item, record)

    class Target:
        judge = None

        async def run(self, _case):
            return TargetResult(
                CapabilityOutput(
                    "ANSWER: private-gold-answer\nCONFIDENCE: 85%",
                    (),
                    artifacts=(SharedArtifact("private artifact filename", b"secret"),),
                    artifact_error="private artifact filename is missing",
                ),
                True,
                trajectory=EvalTrajectory(
                    conversation_id="00000000-0000-0000-0000-000000000001",
                    turn_id="00000000-0000-0000-0000-000000000002",
                    status="done",
                    messages=(Message(role="user", content="private source trajectory"),),
                ),
            )

    return await run_capability_case(case, Target())


async def test_hle_case_keeps_source_and_candidate_text_in_the_record() -> None:
    result = await _private_case_result()
    body = json.dumps(result.model_dump(mode="json"))

    assert result.passed
    assert "Which place is intended?" in body
    assert "private-gold-answer" in body
    assert "private source trajectory" in body
    assert "private artifact filename" in body
    assert "Humanities/Social Science" in body
    recorded = HLERecordedEvidence.model_validate(result.evidence)
    grader = recorded.attempts[recorded.selected_attempt].grader
    assert grader is not None
    assert grader.answer and grader.format and grader.behavior
    assert grader.confidence == 85


async def test_infra_excluded_cases_do_not_emit_hle_metrics() -> None:
    record = _record()
    item = ManifestCase(id=record.id, leaf="web_search_fetch", record_sha256="f" * 64)
    case = _capability_case(item, record)

    class Target:
        judge = None

        async def run(self, _case):
            return TargetResult(
                CapabilityOutput("ANSWER: Poblet\nCONFIDENCE: 80%", (), ("service unavailable",)),
                True,
            )

    report = await _leaf_task("web_search_fetch", (case,)).run(Target(), asyncio.Semaphore(1))

    assert report.cases[0].excluded
    assert report.metrics == ()


def test_compaction_pressure_is_bounded_and_carries_the_nonce() -> None:
    messages = _compaction_messages("671ebaf92a7c16b748fd2709")

    assert len(messages) == 16
    assert max(map(len, messages)) < 200_000
    assert "RETENTION-671ebaf92a7c16b748fd2709" in messages[0]
    assert sum(map(len, messages)) // 4 > 251_000
    assert sum(map(len, messages[-8:])) < 40_000
    assert all("RETENTION-" not in message for message in messages[-8:])


def test_hle_metrics_cross_the_report_and_viewer_boundaries() -> None:
    report = EvalReport(
        name="hle_gold.vision_read",
        suite="hle_gold",
        digest="sha256:test",
        cases=(),
        metrics=(EvalMetric(name="accuracy", value=0.75),),
    )

    assert report.to_json()["metrics"] == [{"name": "accuracy", "value": 0.75}]
    assert "'hle_gold.'" in VIEWER_PAGE
    assert "metricMeta" in VIEWER_PAGE
    assert VIEWER_PAGE.count("${h(metrics)}") == 1
