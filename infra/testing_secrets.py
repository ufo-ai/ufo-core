from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256

AWS_REGION = "us-east-1"
DEPLOYMENT_ID_ENV = "TESTING_DEPLOYMENT_ID"
SECRET_ID_ENV = "TESTING_API_KEYS_SECRET_ID"
SECRET_INPUTS = {
    "anthropic-api-key": "ANTHROPIC_API_KEY",
    "perplexity-api-key": "PERPLEXITY_API_KEY",
    "spectrum-project-id": "SPECTRUM_PROJECT_ID",
    "spectrum-project-secret": "SPECTRUM_PROJECT_SECRET",
}
REQUIRED_PROPERTIES = frozenset(SECRET_INPUTS) | {
    "browserbase-api-key",
    "datadog-api-key",
    "e2b-api-key",
    "turbopuffer-api-key",
}
FAIL_CLOSED_PROPERTIES = frozenset(
    {
        "cloudflare-account-id",
        "cloudflare-flagship-app-id",
        "cloudflare-flagship-token",
        "cloudflare-flagship-write-token",
        "people-data-labs-api-key",
    }
)


@dataclass(frozen=True)
class SecretWrite:
    secret_id: str
    payload: bytes
    deployment_id: str

    @property
    def command(self) -> tuple[str, ...]:
        return (
            "aws",
            "secretsmanager",
            "put-secret-value",
            "--region",
            AWS_REGION,
            "--secret-id",
            self.secret_id,
            "--secret-string",
            "file:///dev/stdin",
            "--client-request-token",
            sha256(f"{self.deployment_id}\0{self.secret_id}\0".encode() + self.payload).hexdigest(),
            "--no-cli-pager",
        )


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def testing_secret_write(environment: Mapping[str, str], raw: bytes) -> SecretWrite:
    """Set the testing provider keys and preserve every other runtime secret property.

    A required property that is empty stops the deploy here, before the write and the apply that
    follows it. This step is the last place an empty credential is visible at all: External Secrets
    projects it into `ufo-platform-secrets` as an empty env var, the container starts, the process
    raises on the credential it needs, and the apply spends its whole rollout wait watching pods
    crash-loop before it reports a timeout that names a Deployment and no credential."""
    secret_id = _required(environment, SECRET_ID_ENV)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{secret_id} must contain valid JSON") from error
    if not isinstance(value, dict) or any(not isinstance(item, str) for item in value.values()):
        raise RuntimeError(f"{secret_id} must contain string properties")
    value.update({name: value.get(name, "") for name in FAIL_CLOSED_PROPERTIES})
    value.update(
        {name: _required(environment, input_name) for name, input_name in SECRET_INPUTS.items()}
    )
    empty = sorted(name for name in REQUIRED_PROPERTIES if not value.get(name))
    if empty:
        raise RuntimeError(
            f"{secret_id} holds no value for {', '.join(empty)}; seed the property in Secrets "
            "Manager before this deploy rolls the pods that read it"
        )
    return SecretWrite(
        secret_id=secret_id,
        payload=json.dumps(value, sort_keys=True, separators=(",", ":")).encode(),
        deployment_id=_required(environment, DEPLOYMENT_ID_ENV),
    )


def _aws_environment(environment: Mapping[str, str]) -> dict[str, str]:
    return {
        name: value for name, value in environment.items() if name not in SECRET_INPUTS.values()
    }


def main(arguments: Sequence[str] = ()) -> None:
    if arguments:
        raise RuntimeError("usage: testing_secrets.py")
    secret_id = _required(os.environ, SECRET_ID_ENV)
    read = subprocess.run(
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
        env=_aws_environment(os.environ),
        check=True,
    )
    write = testing_secret_write(os.environ, read.stdout)
    subprocess.run(
        write.command,
        input=write.payload,
        env=_aws_environment(os.environ),
        stdout=subprocess.DEVNULL,
        check=True,
    )


if __name__ == "__main__":
    main(sys.argv[1:])
