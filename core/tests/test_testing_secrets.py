import json
import os
from hashlib import sha256
from pathlib import Path

import pytest

import infra.testing_secrets as testing_secrets
from infra.testing_secrets import (
    DEPLOYMENT_ID_ENV,
    PERPLEXITY_INPUT,
    SECRET_ID_ENV,
    main,
)


def _environment() -> dict[str, str]:
    return {
        DEPLOYMENT_ID_ENV: "run-1-attempt-1",
        PERPLEXITY_INPUT: "perplexity-value",
        SECRET_ID_ENV: "ufo/ufo-testing/api-keys",
    }


def test_testing_secret_write_preserves_the_live_document() -> None:
    raw = json.dumps({"anthropic-api-key": "anthropic-value", "exa-api-key": "exa-value"}).encode()
    write = testing_secrets.testing_secret_write(_environment(), raw)
    assert json.loads(write.payload) == {
        "anthropic-api-key": "anthropic-value",
        "exa-api-key": "exa-value",
        "perplexity-api-key": "perplexity-value",
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
        (_environment() | {DEPLOYMENT_ID_ENV: ""}, b"{}", DEPLOYMENT_ID_ENV),
        (_environment() | {PERPLEXITY_INPUT: ""}, b"{}", PERPLEXITY_INPUT),
        (_environment() | {SECRET_ID_ENV: ""}, b"{}", SECRET_ID_ENV),
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
        "'key': os.environ.get('PERPLEXITY_API_KEY')}\n"
        "with Path(os.environ['AWS_STUB_OUTPUT']).open('a') as stream:\n"
        "    stream.write(json.dumps(record) + '\\n')\n"
        "if sys.argv[2] == 'get-secret-value':\n"
        '    print(\'{"exa-api-key":"exa-value"}\')\n'
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
    assert json.loads(calls[1]["stdin"]) == {
        "exa-api-key": "exa-value",
        "perplexity-api-key": "perplexity-value",
    }
    assert all(call["key"] is None for call in calls)


def test_main_rejects_arguments() -> None:
    with pytest.raises(RuntimeError, match=r"usage: testing_secrets\.py"):
        main(("unknown",))
