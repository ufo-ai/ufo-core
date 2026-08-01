from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256

AWS_REGION = "us-east-1"
DEPLOYMENT_ID_ENV = "PRODUCTION_DEPLOYMENT_ID"
API_KEYS_SECRET_ID_ENV = "PRODUCTION_API_KEYS_SECRET_ID"
GATEWAY_SECRET_ID_ENV = "PRODUCTION_GATEWAY_SECRET_ID"
API_KEY_INPUTS = {
    "anthropic-api-key": "ANTHROPIC_API_KEY",
    "browserbase-api-key": "BROWSERBASE_API_KEY",
    "datadog-api-key": "DD_API_KEY",
    "e2b-api-key": "E2B_API_KEY",
    "exa-api-key": "EXA_API_KEY",
    "openai-api-key": "OPENAI_API_KEY",
    "turbopuffer-api-key": "TURBOPUFFER_API_KEY",
}
API_KEYS_PROPERTIES = frozenset(
    {
        "anthropic-api-key",
        "bedrock-api-key",
        "browserbase-api-key",
        "composio-api-key",
        "datadog-api-key",
        "e2b-api-key",
        "exa-api-key",
        "github-app-client-id",
        "github-app-client-secret",
        "github-app-id",
        "github-app-private-key",
        "metronome-bearer-token",
        "metronome-package-alias",
        "openai-api-key",
        "openrouter-api-key",
        "pipedream-client-id",
        "pipedream-client-secret",
        "pipedream-gmail-oauth-app-id",
        "pipedream-project-id",
        "slack-client-id",
        "slack-client-secret",
        "slack-signing-secret",
        "stripe-billing-portal-configuration-id",
        "stripe-secret-key",
        "turbopuffer-api-key",
    }
)
GATEWAY_PROPERTIES = frozenset({"bot-token"})
SECRET_INPUTS = frozenset(API_KEY_INPUTS.values())


@dataclass(frozen=True)
class SecretWrite:
    secret_id: str
    payload: bytes
    deployment_id: str

    @property
    def command(self) -> tuple[str, ...]:
        command = (
            "aws",
            "secretsmanager",
            "put-secret-value",
            "--region",
            AWS_REGION,
            "--secret-id",
            self.secret_id,
            "--secret-string",
            "file:///dev/stdin",
        )
        return (
            *command,
            "--client-request-token",
            sha256(f"{self.deployment_id}\0{self.secret_id}\0".encode() + self.payload).hexdigest(),
            "--no-cli-pager",
        )


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _payload(raw: bytes, properties: frozenset[str], secret_id: str) -> dict[str, str]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{secret_id} must contain valid JSON") from error
    if not isinstance(value, dict) or set(value) != properties:
        raise RuntimeError(f"{secret_id} must contain the exact secret properties")
    if any(not isinstance(item, str) for item in value.values()):
        raise RuntimeError(f"{secret_id} properties must be strings")
    return value


def _json(value: dict[str, str]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def production_secret_writes(
    environment: Mapping[str, str], api_keys_payload: bytes, gateway_payload: bytes
) -> tuple[SecretWrite, ...]:
    deployment_id = _required(environment, DEPLOYMENT_ID_ENV)
    api_keys_secret_id = _required(environment, API_KEYS_SECRET_ID_ENV)
    gateway_secret_id = _required(environment, GATEWAY_SECRET_ID_ENV)
    api_keys = _payload(api_keys_payload, API_KEYS_PROPERTIES, api_keys_secret_id)
    api_keys.update(
        {name: _required(environment, input_name) for name, input_name in API_KEY_INPUTS.items()}
    )
    return (
        SecretWrite(
            api_keys_secret_id,
            _json(api_keys),
            deployment_id,
        ),
        SecretWrite(
            gateway_secret_id,
            _json(_payload(gateway_payload, GATEWAY_PROPERTIES, gateway_secret_id)),
            deployment_id,
        ),
    )


def _aws_environment(environment: Mapping[str, str]) -> dict[str, str]:
    return {name: value for name, value in environment.items() if name not in SECRET_INPUTS}


def _read_secret(
    secret_id: str, properties: frozenset[str], environment: Mapping[str, str]
) -> bytes:
    result = subprocess.run(
        (
            "aws",
            "secretsmanager",
            "get-secret-value",
            "--region",
            AWS_REGION,
            "--secret-id",
            secret_id,
            "--query",
            "SecretString",
            "--output",
            "text",
            "--no-cli-pager",
        ),
        capture_output=True,
        env=_aws_environment(environment),
        check=False,
    )
    if result.returncode == 0:
        return result.stdout
    if (
        result.returncode == 254
        and b"An error occurred (ResourceNotFoundException)" in result.stderr
    ):
        return _json(dict.fromkeys(properties, ""))
    result.check_returncode()
    raise AssertionError


def main(arguments: Sequence[str] = ()) -> None:
    if arguments:
        raise RuntimeError("usage: production_secrets.py")
    api_keys_secret_id = _required(os.environ, API_KEYS_SECRET_ID_ENV)
    gateway_secret_id = _required(os.environ, GATEWAY_SECRET_ID_ENV)
    writes = production_secret_writes(
        os.environ,
        _read_secret(api_keys_secret_id, API_KEYS_PROPERTIES, os.environ),
        _read_secret(gateway_secret_id, GATEWAY_PROPERTIES, os.environ),
    )
    for write in writes:
        subprocess.run(
            write.command,
            input=write.payload,
            env=_aws_environment(os.environ),
            stdout=subprocess.DEVNULL,
            check=True,
        )


if __name__ == "__main__":
    main(sys.argv[1:])
