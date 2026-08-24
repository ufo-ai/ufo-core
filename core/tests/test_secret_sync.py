import json
import os
from pathlib import Path

import pytest

import infra.secret_sync as secret_sync
from infra.secret_sync import main

KUBECTL_STUB = """#!/usr/bin/env python3
import base64
import json
import os
import sys
from pathlib import Path

arguments = sys.argv[1:]
calls = Path(os.environ['KUBECTL_CALLS'])
with calls.open('a') as stream:
    stream.write(json.dumps(arguments) + '\\n')


def entries(name):
    return [{
        'secretKey': 'KEY_' + name,
        'remoteRef': dict(
            json.loads(os.environ.get('REMOTE_REF_' + name, '{}')),
            key='ufo/prod/' + name,
            property='p-' + name,
        ),
    }]


if 'api-resources' in arguments:
    if os.environ.get('KUBECTL_HAS_CRD', '1') == '1':
        print('externalsecrets.external-secrets.io')
elif arguments[3] == 'get' and arguments[4] == 'externalsecret':
    state = Path(os.environ['KUBECTL_STATE'])
    poll = int(state.read_text()) if state.exists() else 0
    state.write_text(str(poll + 1))
    changed = poll > 0 and os.environ.get('KUBECTL_CHANGES', '1') == '1'
    ready = (
        poll > int(os.environ.get('KUBECTL_READY_AFTER', '1'))
        and os.environ.get('KUBECTL_READY', '1') == '1'
    )
    names = os.environ.get('KUBECTL_NAMES', 'platform,datadog')
    if poll > 0:
        names = os.environ.get('KUBECTL_NAMES_AFTER', names)
    items = []
    for name in names.split(','):
        specification = {'target': {'name': name + '-target'}, 'data': entries(name)}
        if os.environ.get('DATA_FROM_' + name):
            specification['dataFrom'] = [{'extract': {'key': 'whole'}}]
        items.append({
            'metadata': {'name': name},
            'spec': specification,
            'status': {
                'syncedResourceVersion': ('new-' if changed else 'old-') + name,
                'conditions': [{'type': 'Ready', 'status': 'True' if ready else 'False'}],
            },
        })
    print(json.dumps({'items': items}))
elif arguments[3] == 'get' and arguments[4] == 'secret':
    name = arguments[5].removesuffix('-target')
    value = os.environ.get('PROJECTED_' + name, 'v-' + name)
    print(json.dumps({
        'data': {'KEY_' + name: base64.b64encode(value.encode()).decode()},
    }))
"""

AWS_STUB = """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

arguments = sys.argv[1:]
calls = Path(os.environ['AWS_CALLS'])
with calls.open('a') as stream:
    stream.write(json.dumps(arguments) + '\\n')
name = arguments[arguments.index('--secret-id') + 1].removeprefix('ufo/prod/')
print(json.dumps({'p-' + name: os.environ.get('SOURCE_' + name, 'v-' + name)}))
"""


def _stub(tmp_path: Path, name: str, source: str) -> None:
    executable = tmp_path / name
    executable.write_text(source)
    executable.chmod(0o755)


def _environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    _stub(tmp_path, "kubectl", KUBECTL_STUB)
    _stub(tmp_path, "aws", AWS_STUB)
    calls = tmp_path / "calls.jsonl"
    aws_calls = tmp_path / "aws.jsonl"
    monkeypatch.setenv("KUBECTL_CALLS", str(calls))
    monkeypatch.setenv("AWS_CALLS", str(aws_calls))
    monkeypatch.setenv("KUBECTL_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setattr(secret_sync.time, "sleep", lambda _: None)
    return calls, aws_calls


def _calls(path: Path) -> list[list[str]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_sync_waits_for_every_annotated_secret_to_change_and_be_ready(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls_path, _ = _environment(monkeypatch, tmp_path)

    main(("ufo-system", "run-1"))

    calls = _calls(calls_path)
    annotations = [call for call in calls if "annotate" in call]
    reads = [
        index
        for index, call in enumerate(calls)
        if call[3] == "get" and call[4] == "externalsecret"
    ]
    assert [call[4] for call in annotations] == [
        "externalsecret/platform",
        "externalsecret/datadog",
    ]
    assert all("force-sync=run-1" in call for call in annotations)
    assert reads[0] < min(calls.index(call) for call in annotations)
    assert max(calls.index(call) for call in annotations) < reads[1]
    assert len(reads) == 3
    assert not any("rollout" in call for call in calls)


@pytest.mark.parametrize(
    "environment",
    [
        {"KUBECTL_CHANGES": "0", "KUBECTL_READY_AFTER": "0"},
        {"KUBECTL_READY": "0"},
        {"KUBECTL_NAMES_AFTER": "datadog", "KUBECTL_READY_AFTER": "0"},
    ],
)
def test_sync_requires_a_new_ready_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, environment: dict[str, str]
) -> None:
    _environment(monkeypatch, tmp_path)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(secret_sync, "TIMEOUT_SECONDS", 0)

    with pytest.raises(TimeoutError, match="did not publish new ready versions"):
        main(("ufo-system", "run-1"))


def test_sync_skips_a_cluster_without_external_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls_path, _ = _environment(monkeypatch, tmp_path)
    monkeypatch.setenv("KUBECTL_HAS_CRD", "0")

    main(("ufo-system", "run-1"))

    calls = _calls(calls_path)
    assert len(calls) == 1
    assert "api-resources" in calls[0]


def test_sync_reads_every_projected_key_and_its_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls_path, aws_calls_path = _environment(monkeypatch, tmp_path)

    main(("ufo-system", "run-1"))

    projected = [call[5] for call in _calls(calls_path) if call[3] == "get" and call[4] == "secret"]
    sourced = [
        call[call.index("--secret-id") + 1]
        for call in _calls(aws_calls_path)
        if "get-secret-value" in call
    ]
    assert projected == ["platform-target", "datadog-target"]
    assert sourced == ["ufo/prod/platform", "ufo/prod/datadog"]


def test_sync_rejects_a_projected_value_that_no_longer_matches_its_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _environment(monkeypatch, tmp_path)
    monkeypatch.setenv("PROJECTED_datadog", "superseded-encoding")

    with pytest.raises(
        RuntimeError,
        match=r"differ from Secrets Manager: externalsecret/datadog "
        r"KEY_datadog from ufo/prod/datadog$",
    ):
        main(("ufo-system", "run-1"))


def test_sync_reports_every_drifted_key_at_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _environment(monkeypatch, tmp_path)
    monkeypatch.setenv("PROJECTED_platform", "stale")
    monkeypatch.setenv("PROJECTED_datadog", "stale")

    with pytest.raises(RuntimeError, match=r"externalsecret/platform.*externalsecret/datadog"):
        main(("ufo-system", "run-1"))


@pytest.mark.parametrize(
    ("remote_ref", "error"),
    [
        ({"conversionStrategy": "Unicode"}, "unsupported conversionStrategy 'Unicode'"),
        ({"decodingStrategy": "Base64"}, "unsupported decodingStrategy 'Base64'"),
        ({"version": "AWSPREVIOUS"}, "pinned remoteRef version is unsupported"),
    ],
)
def test_sync_rejects_a_remote_ref_it_cannot_compare(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, remote_ref: dict[str, str], error: str
) -> None:
    _environment(monkeypatch, tmp_path)
    monkeypatch.setenv("KUBECTL_NAMES", "platform")
    monkeypatch.setenv("REMOTE_REF_platform", json.dumps(remote_ref))

    with pytest.raises(RuntimeError, match=error):
        main(("ufo-system", "run-1"))


def test_sync_rejects_a_data_from_projection_it_cannot_compare(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _environment(monkeypatch, tmp_path)
    monkeypatch.setenv("KUBECTL_NAMES", "platform")
    monkeypatch.setenv("DATA_FROM_platform", "1")

    with pytest.raises(RuntimeError, match="dataFrom is unsupported: externalsecret/platform"):
        main(("ufo-system", "run-1"))


def test_sync_rejects_unknown_arguments() -> None:
    with pytest.raises(RuntimeError, match=r"usage: secret_sync\.py"):
        main(("ufo-system",))


def test_a_multiline_pem_survives_the_projection_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _environment(monkeypatch, tmp_path)
    pem = "-----BEGIN PRIVATE KEY-----\nbody\n-----END PRIVATE KEY-----\n"
    monkeypatch.setenv("PROJECTED_platform", pem)
    monkeypatch.setenv("SOURCE_platform", pem)
    monkeypatch.setenv("KUBECTL_NAMES", "platform")

    main(("ufo-system", "run-1"))
