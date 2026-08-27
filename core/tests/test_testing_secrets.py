import json
import os
from hashlib import sha256
from pathlib import Path

import pytest

import infra.testing_secrets as testing_secrets
from infra.testing_secrets import (
    DEPLOYMENT_ID_ENV,
    FAIL_CLOSED_PROPERTIES,
    REQUIRED_PROPERTIES,
    SECRET_ID_ENV,
    SECRET_INPUTS,
    main,
)

_SEEDED = {name: f"{name}-value" for name in sorted(REQUIRED_PROPERTIES - set(SECRET_INPUTS))} | {
    "retired-api-key": ""
}
_FILLED = dict.fromkeys(sorted(FAIL_CLOSED_PROPERTIES), "")


def _environment() -> dict[str, str]:
    return {
        DEPLOYMENT_ID_ENV: "run-1-attempt-1",
        SECRET_ID_ENV: "ufo/ufo-testing/api-keys",
        "ANTHROPIC_API_KEY": "anthropic-value",
        "PERPLEXITY_API_KEY": "perplexity-value",
        "SPECTRUM_PROJECT_ID": "spectrum-project-id-value",
        "SPECTRUM_PROJECT_SECRET": "spectrum-project-secret-value",
    }


def _document(**overrides: str) -> bytes:
    return json.dumps(_SEEDED | overrides).encode()


def test_testing_secret_write_preserves_the_live_document() -> None:
    raw = _document(**{"existing-api-key": "existing-value"})
    write = testing_secrets.testing_secret_write(_environment(), raw)
    assert json.loads(write.payload) == _SEEDED | _FILLED | {
        "existing-api-key": "existing-value",
        "anthropic-api-key": "anthropic-value",
        "perplexity-api-key": "perplexity-value",
        "spectrum-project-id": "spectrum-project-id-value",
        "spectrum-project-secret": "spectrum-project-secret-value",
    }
    assert "file:///dev/stdin" in write.command
    assert "perplexity-value" not in write.command
    assert (
        write.command[write.command.index("--client-request-token") + 1]
        == sha256(f"run-1-attempt-1\0{write.secret_id}\0".encode() + write.payload).hexdigest()
    )


@pytest.mark.parametrize(
    ("environment", "raw", "error"),
    (
        (_environment() | {DEPLOYMENT_ID_ENV: ""}, _document(), DEPLOYMENT_ID_ENV),
        (_environment() | {"ANTHROPIC_API_KEY": ""}, _document(), "ANTHROPIC_API_KEY"),
        (_environment() | {"PERPLEXITY_API_KEY": ""}, _document(), "PERPLEXITY_API_KEY"),
        (_environment() | {"SPECTRUM_PROJECT_ID": ""}, _document(), "SPECTRUM_PROJECT_ID"),
        (
            _environment() | {"SPECTRUM_PROJECT_SECRET": ""},
            _document(),
            "SPECTRUM_PROJECT_SECRET",
        ),
        (_environment() | {SECRET_ID_ENV: ""}, _document(), SECRET_ID_ENV),
        (_environment(), b"not-json", "must contain valid JSON"),
        (_environment(), b"[]", "must contain string properties"),
        (_environment(), b'{"value":null}', "must contain string properties"),
    ),
)
def test_testing_secret_write_rejects_invalid_input(
    environment: dict[str, str], raw: bytes, error: str
) -> None:
    with pytest.raises(RuntimeError, match=error):
        testing_secrets.testing_secret_write(environment, raw)


@pytest.mark.parametrize("property_name", sorted(REQUIRED_PROPERTIES - set(SECRET_INPUTS)))
def test_testing_secret_write_refuses_an_empty_required_property(property_name: str) -> None:
    """The deploy stops at this step instead of projecting an empty credential into the cluster and
    rolling pods that crash-loop until the apply's rollout wait expires."""
    with pytest.raises(RuntimeError, match=f"holds no value for {property_name}"):
        testing_secrets.testing_secret_write(_environment(), _document(**{property_name: ""}))


def test_testing_secret_write_names_every_empty_required_property() -> None:
    blanked = json.dumps(dict.fromkeys(sorted(REQUIRED_PROPERTIES), "")).encode()
    with pytest.raises(RuntimeError) as error:
        testing_secrets.testing_secret_write(_environment(), blanked)
    assert str(error.value) == (
        "ufo/ufo-testing/api-keys holds no value for "
        + ", ".join(sorted(REQUIRED_PROPERTIES - set(SECRET_INPUTS)))
        + "; seed the property in Secrets Manager before this deploy rolls the pods that read it"
    )


def test_testing_secret_write_fills_an_unseeded_flag_key_rather_than_stopping_the_deploy() -> None:
    """The cluster projects each flag key by name, so a property Secrets Manager does not hold
    leaves the ExternalSecret unready and times out the forced re-sync before the apply. An empty
    value costs testing nothing: serve builds no flag provider without all three, so every flag
    resolves to the closed default its call site passes."""
    write = testing_secrets.testing_secret_write(_environment(), _document())
    written = json.loads(write.payload)
    assert {written[name] for name in FAIL_CLOSED_PROPERTIES} == {""}
    assert not FAIL_CLOSED_PROPERTIES & REQUIRED_PROPERTIES


def test_testing_secret_write_preserves_a_seeded_flag_key() -> None:
    raw = _document(**{"cloudflare-flagship-token": "cf-flagship-token"})
    written = json.loads(testing_secrets.testing_secret_write(_environment(), raw).payload)
    assert written["cloudflare-flagship-token"] == "cf-flagship-token"
    assert written["cloudflare-flagship-app-id"] == ""


def test_main_reads_and_writes_through_stdin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "calls.jsonl"
    aws = tmp_path / "aws"
    aws.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "record = {'argv': sys.argv[1:], 'stdin': sys.stdin.read(), "
        "'keys': [name for name in "
        "('ANTHROPIC_API_KEY', 'PERPLEXITY_API_KEY', 'SPECTRUM_PROJECT_ID', "
        "'SPECTRUM_PROJECT_SECRET') "
        "if name in os.environ]}\n"
        "with Path(os.environ['AWS_STUB_OUTPUT']).open('a') as stream:\n"
        "    stream.write(json.dumps(record) + '\\n')\n"
        "if sys.argv[2] == 'get-secret-value':\n"
        f"    print({json.dumps(json.dumps(_SEEDED | {'existing-api-key': 'existing-value'}))})\n"
    )
    aws.chmod(0o755)
    environment = (
        os.environ
        | _environment()
        | {
            "AWS_STUB_OUTPUT": str(output),
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
        }
    )
    monkeypatch.setattr(testing_secrets.os, "environ", environment)

    main()

    calls = [json.loads(line) for line in output.read_text().splitlines()]
    assert len(calls) == 2
    assert calls[0]["argv"][1] == "get-secret-value"
    assert calls[1]["argv"][1] == "put-secret-value"
    assert json.loads(calls[1]["stdin"]) == _SEEDED | _FILLED | {
        "existing-api-key": "existing-value",
        "anthropic-api-key": "anthropic-value",
        "perplexity-api-key": "perplexity-value",
        "spectrum-project-id": "spectrum-project-id-value",
        "spectrum-project-secret": "spectrum-project-secret-value",
    }
    assert all(call["keys"] == [] for call in calls)


def test_main_rejects_arguments() -> None:
    with pytest.raises(RuntimeError, match=r"usage: testing_secrets\.py"):
        main(("unknown",))
