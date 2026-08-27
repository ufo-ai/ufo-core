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
    "perplexity-api-key": "PERPLEXITY_API_KEY",
    "openai-api-key": "OPENAI_API_KEY",
    "openrouter-api-key": "OPENROUTER_API_KEY",
    "spectrum-project-id": "SPECTRUM_PROJECT_ID",
    "spectrum-project-secret": "SPECTRUM_PROJECT_SECRET",
    "turbopuffer-api-key": "TURBOPUFFER_API_KEY",
}
TESTING_VALUE_PREFIXES = ("sk_test_", "pk_test_", "rk_test_")
API_KEYS_PROPERTIES = frozenset(
    {
        "anthropic-api-key",
        "bedrock-api-key",
        "browserbase-api-key",
        "cloudflare-account-id",
        "cloudflare-flagship-app-id",
        "cloudflare-flagship-token",
        "cloudflare-flagship-write-token",
        "composio-api-key",
        "datadog-api-key",
        "e2b-api-key",
        "perplexity-api-key",
        "github-app-client-id",
        "github-app-client-secret",
        "github-app-id",
        "github-app-private-key",
        "metronome-bearer-token",
        "openai-api-key",
        "openrouter-api-key",
        "pipedream-client-id",
        "pipedream-client-secret",
        "pipedream-gmail-oauth-app-id",
        "pipedream-project-id",
        "slack-client-id",
        "slack-client-secret",
        "slack-signing-secret",
        "spectrum-project-id",
        "spectrum-project-secret",
        "stripe-billing-portal-configuration-id",
        "stripe-secret-key",
        "turbopuffer-api-key",
    }
)
# The flag backend's three keys (`[flags] backend = "flagship"`, infra/envs/*/ufo.tf), seeded
# out-of-band like every other production-owned property. A document that carries none of them is
# filled with empty strings here instead of failing the deploy: the extension builds no provider
# unless all three are set, so every flag resolves to the closed default its call site passes, and
# the ExternalSecret projecting them still publishes a ready version. Requiring them would stop a
# deploy over a feature service the fleet runs perfectly well without.
FAIL_CLOSED_PROPERTIES = frozenset(
    {
        "cloudflare-account-id",
        "cloudflare-flagship-app-id",
        "cloudflare-flagship-token",
        "cloudflare-flagship-write-token",
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
    """The live secret, checked to carry every property this deploy declares, returned as the
    payload to write back.

    A declared property the secret lacks fails loud: that is the direction that loses a key a
    running deploy needs. A property the deploy no longer declares is carried through untouched.
    Dropping it here would be a step too early: the write runs before the terraform apply that
    replaces the ExternalSecret still projecting it, so the forced re-sync between them would ask
    External Secrets for a property the secret no longer holds, never publish a ready version, and
    time the deploy out before the apply that would have removed the projection — every retry
    failing the same way. Nothing reads a property no manifest projects, so carrying it costs a
    stale key and buys a removal that lands in one deploy."""
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{secret_id} must contain valid JSON") from error
    if not isinstance(value, dict) or not properties <= set(value):
        raise RuntimeError(f"{secret_id} must contain every declared secret property")
    if any(not isinstance(item, str) for item in value.values()):
        raise RuntimeError(f"{secret_id} properties must be strings")
    return value


def _refuse_testing_values(values: Mapping[str, str], secret_id: str) -> None:
    """Fail the deploy on a value that is visibly a test-mode credential, rather than publishing it.

    Production's secret documents are seeded by hand from copies of the testing values, and every
    layer below here accepts what that produces: the check above reads only that values are strings,
    and the runtime verifies a testing credential happily against the testing provider. A test-mode
    Stripe key charges nobody and issues no invoice, so the deploy is the last place the mistake is
    visible at all.

    Only a credential whose own format names its mode can be caught this way. A Metronome sandbox
    token and a testing Slack app's secret are shaped exactly like their production counterparts, so
    nothing here can tell them apart — those stay the runbook's job, and this catches the one family
    that announces itself."""
    testing = sorted(
        name for name, value in values.items() if value.startswith(TESTING_VALUE_PREFIXES)
    )
    if testing:
        raise RuntimeError(f"{secret_id} holds test-mode credentials: {', '.join(testing)}")


def _json(value: dict[str, str]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def production_secret_writes(
    environment: Mapping[str, str], api_keys_payload: bytes, gateway_payload: bytes
) -> tuple[SecretWrite, ...]:
    deployment_id = _required(environment, DEPLOYMENT_ID_ENV)
    api_keys_secret_id = _required(environment, API_KEYS_SECRET_ID_ENV)
    gateway_secret_id = _required(environment, GATEWAY_SECRET_ID_ENV)
    api_keys = _payload(
        api_keys_payload,
        API_KEYS_PROPERTIES - API_KEY_INPUTS.keys() - FAIL_CLOSED_PROPERTIES,
        api_keys_secret_id,
    )
    api_keys.update({name: api_keys.get(name, "") for name in FAIL_CLOSED_PROPERTIES})
    api_keys.update(
        {name: _required(environment, input_name) for name, input_name in API_KEY_INPUTS.items()}
    )
    gateway = _payload(gateway_payload, GATEWAY_PROPERTIES, gateway_secret_id)
    _refuse_testing_values(api_keys, api_keys_secret_id)
    _refuse_testing_values(gateway, gateway_secret_id)
    return (
        SecretWrite(
            api_keys_secret_id,
            _json(api_keys),
            deployment_id,
        ),
        SecretWrite(
            gateway_secret_id,
            _json(gateway),
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
