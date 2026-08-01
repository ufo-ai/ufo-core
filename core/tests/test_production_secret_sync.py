import json
import os
from pathlib import Path

import pytest

import infra.production_secret_sync as production_secret_sync
from infra.production_secret_sync import main


def _stub_kubectl(tmp_path: Path) -> None:
    executable = tmp_path / "kubectl"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "import sys\n"
        "from pathlib import Path\n"
        "arguments = sys.argv[1:]\n"
        "calls = Path(os.environ['KUBECTL_CALLS'])\n"
        "with calls.open('a') as stream:\n"
        "    stream.write(json.dumps(arguments) + '\\n')\n"
        "if 'api-resources' in arguments:\n"
        "    if os.environ.get('KUBECTL_HAS_CRD', '1') == '1':\n"
        "        print('externalsecrets.external-secrets.io')\n"
        "elif 'get' in arguments:\n"
        "    state = Path(os.environ['KUBECTL_STATE'])\n"
        "    poll = int(state.read_text()) if state.exists() else 0\n"
        "    state.write_text(str(poll + 1))\n"
        "    changed = poll > 0 and os.environ.get('KUBECTL_CHANGES', '1') == '1'\n"
        "    ready = (\n"
        "        poll > int(os.environ.get('KUBECTL_READY_AFTER', '1'))\n"
        "        and os.environ.get('KUBECTL_READY', '1') == '1'\n"
        "    )\n"
        "    items = []\n"
        "    names = os.environ.get('KUBECTL_NAMES', 'platform,datadog')\n"
        "    if poll > 0:\n"
        "        names = os.environ.get('KUBECTL_NAMES_AFTER', names)\n"
        "    for name in names.split(','):\n"
        "        items.append({'metadata': {'name': name}, 'status': {\n"
        "            'syncedResourceVersion': ('new-' if changed else 'old-') + name,\n"
        "            'conditions': [{'type': 'Ready', 'status': 'True' if ready else 'False'}],\n"
        "        }})\n"
        "    print(json.dumps({'items': items}))\n"
    )
    executable.chmod(0o755)


def _environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    calls = tmp_path / "calls.jsonl"
    monkeypatch.setenv("KUBECTL_CALLS", str(calls))
    monkeypatch.setenv("KUBECTL_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setattr(production_secret_sync.time, "sleep", lambda _: None)
    return calls


def test_sync_waits_for_every_annotated_secret_to_change_and_be_ready(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_kubectl(tmp_path)
    calls_path = _environment(monkeypatch, tmp_path)

    main(("ufo-system", "run-1"))

    calls = [json.loads(line) for line in calls_path.read_text().splitlines()]
    annotations = [call for call in calls if "annotate" in call]
    reads = [index for index, call in enumerate(calls) if "get" in call]
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
    _stub_kubectl(tmp_path)
    _environment(monkeypatch, tmp_path)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(production_secret_sync, "TIMEOUT_SECONDS", 0)

    with pytest.raises(TimeoutError, match="did not publish new ready versions"):
        main(("ufo-system", "run-1"))


def test_sync_skips_a_cluster_without_external_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_kubectl(tmp_path)
    calls_path = _environment(monkeypatch, tmp_path)
    monkeypatch.setenv("KUBECTL_HAS_CRD", "0")

    main(("ufo-system", "run-1"))

    calls = [json.loads(line) for line in calls_path.read_text().splitlines()]
    assert len(calls) == 1
    assert "api-resources" in calls[0]


def test_sync_rejects_unknown_arguments() -> None:
    with pytest.raises(RuntimeError, match=r"usage: production_secret_sync\.py"):
        main(("ufo-system",))
