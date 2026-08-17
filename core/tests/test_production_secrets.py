import importlib
import json
import os
import re
import subprocess
import textwrap
import tomllib
from hashlib import sha256
from pathlib import Path

import pytest
import yaml

import infra.production_secrets as production_secrets
from infra.production_secrets import (
    API_KEY_INPUTS,
    API_KEYS_PROPERTIES,
    API_KEYS_SECRET_ID_ENV,
    DEPLOYMENT_ID_ENV,
    GATEWAY_PROPERTIES,
    GATEWAY_SECRET_ID_ENV,
    main,
    production_secret_writes,
)

ROOT = Path(__file__).resolve().parents[2]


def _environment(deployment_id: str = "run-1") -> dict[str, str]:
    return {
        API_KEYS_SECRET_ID_ENV: "generated/api-keys",
        DEPLOYMENT_ID_ENV: deployment_id,
        GATEWAY_SECRET_ID_ENV: "generated/gateway",
        "ANTHROPIC_API_KEY": "anthropic-value",
        "BROWSERBASE_API_KEY": "browserbase-value",
        "DD_API_KEY": "datadog-value",
        "E2B_API_KEY": "e2b-value",
        "EXA_API_KEY": "exa-value",
        "OPENAI_API_KEY": "openai-value",
        "OPENROUTER_API_KEY": "openrouter-value",
        "TURBOPUFFER_API_KEY": "turbopuffer-value",
    }


def _payload(properties: frozenset[str]) -> bytes:
    return json.dumps({name: f"owned-{name}" for name in sorted(properties)}).encode()


def _output(source: str, name: str) -> str:
    return source.split(f'output "{name}" {{', maxsplit=1)[1].split("}", maxsplit=1)[0]


def test_secret_schema_matches_terraform() -> None:
    secrets = (ROOT / "infra" / "modules" / "platform" / "secrets.tf").read_text()
    module_outputs = (ROOT / "infra" / "modules" / "platform" / "outputs.tf").read_text()
    prod_outputs = (ROOT / "infra" / "envs" / "prod" / "outputs.tf").read_text()
    testing_outputs = (ROOT / "infra" / "envs" / "testing" / "outputs.tf").read_text()
    api_keys = secrets.split(
        'resource "aws_secretsmanager_secret_version" "api_keys" {', maxsplit=1
    )[1].split("lifecycle {", maxsplit=1)[0]
    gateway = secrets.split(
        'resource "aws_secretsmanager_secret_version" "gateway_slack_connect" {', maxsplit=1
    )[1].split("lifecycle {", maxsplit=1)[0]
    assert set(re.findall(r'^\s+"([^"]+)"\s*=', api_keys, re.MULTILINE)) == API_KEYS_PROPERTIES
    assert set(re.findall(r'^\s+"([^"]+)"\s*=', gateway, re.MULTILINE)) == GATEWAY_PROPERTIES
    assert "count = var.manage_runtime_secret_versions ? 1 : 0" in api_keys
    assert "count = var.manage_runtime_secret_versions ? 1 : 0" in gateway
    assert (
        "manage_runtime_secret_versions = true"
        in (ROOT / "infra" / "envs" / "testing" / "main.tf").read_text()
    )
    assert (
        "manage_runtime_secret_versions = false"
        in (ROOT / "infra" / "envs" / "prod" / "main.tf").read_text()
    )
    assert "value = aws_secretsmanager_secret.api_keys.id" in _output(
        module_outputs, "api_keys_secret_id"
    )
    assert "value = aws_secretsmanager_secret.gateway_slack_connect.id" in _output(
        module_outputs, "gateway_secret_id"
    )
    assert "api_keys_secret_arn" not in module_outputs + prod_outputs + testing_outputs
    assert "value = module.platform.api_keys_secret_id" in _output(
        prod_outputs, "api_keys_secret_id"
    )
    assert "value = module.platform.gateway_secret_id" in _output(prod_outputs, "gateway_secret_id")
    assert "api_keys_secret_id" not in testing_outputs
    assert "gateway_secret_id" not in testing_outputs
    for name in ("api_keys", "gateway_slack_connect"):
        assert re.search(
            rf"moved \{{\n\s+from = aws_secretsmanager_secret_version\.{name}\n"
            rf"\s+to\s+= aws_secretsmanager_secret_version\.{name}\[0\]\n\}}",
            secrets,
        )


def test_production_selects_the_redis_terminal_transport() -> None:
    """The shared fleet's held connection and its turn's workflow land on different pods, so both
    hosted configs select the cross-pod terminal transport the redis_hub extension registers beside
    the redis hub — both ends, over the one Redis the hub already runs."""
    for environment in ("prod", "testing"):
        serve_config = (ROOT / "infra" / "envs" / environment / "ufo.tf").read_text()
        assert re.search(r'\[terminal\]\n\s*backend = "redis"', serve_config), environment
        assert re.search(r'\[hub\]\n\s*backend = "redis"', serve_config), environment
    manifest = importlib.import_module("ufo_ext_redis_hub.manifest").manifest()
    assert any(spec.backend == "redis" for spec in manifest.terminal_transports)


def test_configured_production_backends_receive_platform_credentials() -> None:
    source = (ROOT / "infra" / "envs" / "prod" / "ufo.tf").read_text()
    _, start, remainder = source.partition("  serve_config = <<-TOML\n")
    config_source, end, _ = remainder.partition("  TOML\n")
    assert start and end
    providers_source, serve, _ = textwrap.dedent(config_source).partition("\n[serve]\n")
    assert serve
    config = tomllib.loads(providers_source)
    providers = (
        (config["memory"]["index_backend"], "indexes"),
        (config["research"]["search_provider"], "search"),
        (config["browser"]["cdp_provider"], "cdp"),
    )
    manifests = list(
        yaml.safe_load_all(
            re.sub(
                r"\$\{([^}]+)\}",
                r"\1",
                (ROOT / "infra" / "templates" / "cluster-services.yaml.tpl").read_text(),
            )
        )
    )
    platform = next(
        document
        for document in manifests
        if document.get("kind") == "ExternalSecret"
        and document["metadata"]["name"] == "ufo-platform-secrets"
    )
    projections = {
        item["secretKey"]: item["remoteRef"]["property"] for item in platform["spec"]["data"]
    }
    hosted = re.sub(
        r'(?m)^%\{ (?:if workload_ha|if cache_enabled|if cache_s3_bucket != ""|endif) \}\n?',
        "",
        (ROOT / "infra" / "templates" / "hosted.yaml.tpl").read_text(),
    )
    documents = list(yaml.safe_load_all(re.sub(r"\$\{([^}]+)\}", r"\1", hosted)))
    serve = next(
        document
        for document in documents
        if document.get("kind") == "Deployment" and document["metadata"]["name"] == "ufo-serve"
    )
    (container,) = serve["spec"]["template"]["spec"]["containers"]
    assert {item["secretRef"]["name"] for item in container["envFrom"]} == {"ufo-platform-secrets"}
    for provider, point in providers:
        manifest = importlib.import_module(f"ufo_ext_{provider}").manifest()
        match point:
            case "indexes":
                assert any(item.name == provider for item in manifest.indexes)
            case "search":
                assert any(item.backend == provider for item in manifest.search_providers)
            case "cdp":
                assert any(item.backend == provider for item in manifest.cdp_providers)
        (slot,) = manifest.credentials
        environment_name = slot.name.upper()
        property_name = slot.name.replace("_", "-")
        assert projections[environment_name] == property_name
        assert API_KEY_INPUTS[property_name] == environment_name


def test_production_secret_writes_preserve_owned_values() -> None:
    writes = production_secret_writes(
        _environment(), _payload(API_KEYS_PROPERTIES), _payload(GATEWAY_PROPERTIES)
    )
    assert [write.secret_id for write in writes] == ["generated/api-keys", "generated/gateway"]
    api_keys = json.loads(writes[0].payload)
    assert set(api_keys) == API_KEYS_PROPERTIES
    assert api_keys["anthropic-api-key"] == "anthropic-value"
    assert api_keys["browserbase-api-key"] == "browserbase-value"
    assert api_keys["datadog-api-key"] == "datadog-value"
    assert api_keys["e2b-api-key"] == "e2b-value"
    assert api_keys["exa-api-key"] == "exa-value"
    assert api_keys["openai-api-key"] == "openai-value"
    assert api_keys["openrouter-api-key"] == "openrouter-value"
    assert api_keys["turbopuffer-api-key"] == "turbopuffer-value"
    assert all(
        api_keys[name] == f"owned-{name}" for name in API_KEYS_PROPERTIES - API_KEY_INPUTS.keys()
    )
    assert json.loads(writes[1].payload) == {"bot-token": "owned-bot-token"}
    assert all("file:///dev/stdin" in write.command for write in writes)
    assert [
        write.command[write.command.index("--client-request-token") + 1] for write in writes
    ] == [
        sha256(f"run-1\0{write.secret_id}\0".encode() + write.payload).hexdigest()
        for write in writes
    ]
    values = [
        "anthropic-value",
        "browserbase-value",
        "datadog-value",
        "e2b-value",
        "exa-value",
        "openai-value",
        "openrouter-value",
        "turbopuffer-value",
    ]
    assert all(value not in part for write in writes for value in values for part in write.command)


def test_main_rejects_unknown_arguments() -> None:
    with pytest.raises(RuntimeError, match=r"usage: production_secrets\.py"):
        main(("unknown",))


@pytest.mark.parametrize(
    ("name", "error"),
    (
        (DEPLOYMENT_ID_ENV, DEPLOYMENT_ID_ENV),
        (API_KEYS_SECRET_ID_ENV, API_KEYS_SECRET_ID_ENV),
        (GATEWAY_SECRET_ID_ENV, GATEWAY_SECRET_ID_ENV),
        *[(name, name) for name in API_KEY_INPUTS.values()],
    ),
)
def test_production_secret_writes_reject_missing_inputs(name: str, error: str) -> None:
    environment = _environment() | {name: ""}
    with pytest.raises(RuntimeError, match=error):
        production_secret_writes(
            environment, _payload(API_KEYS_PROPERTIES), _payload(GATEWAY_PROPERTIES)
        )


@pytest.mark.parametrize(
    ("api_keys", "gateway", "error"),
    (
        (b"{}", _payload(GATEWAY_PROPERTIES), "generated/api-keys must contain every declared"),
        (_payload(API_KEYS_PROPERTIES), b"{}", "generated/gateway must contain every declared"),
        (b"not-json", _payload(GATEWAY_PROPERTIES), "generated/api-keys must contain valid JSON"),
        (
            json.dumps({name: None for name in sorted(API_KEYS_PROPERTIES)}).encode(),
            _payload(GATEWAY_PROPERTIES),
            "generated/api-keys properties must be strings",
        ),
    ),
)
def test_production_secret_writes_reject_invalid_owned_values(
    api_keys: bytes, gateway: bytes, error: str
) -> None:
    with pytest.raises(RuntimeError, match=error):
        production_secret_writes(_environment(), api_keys, gateway)


def _stub_aws(tmp_path: Path) -> Path:
    executable = tmp_path / "aws"
    input_names = sorted(API_KEY_INPUTS.values())
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "import sys\n"
        "from pathlib import Path\n"
        "argv = sys.argv[1:]\n"
        "secret_id = argv[argv.index('--secret-id') + 1]\n"
        "record = {\n"
        "    'argv': argv,\n"
        "    'stdin': sys.stdin.read(),\n"
        f"    'inputs': {{name: os.environ.get(name) for name in {input_names!r}}},\n"
        "}\n"
        "with Path(os.environ['AWS_STUB_OUTPUT']).open('a') as stream:\n"
        "    stream.write(json.dumps(record) + '\\n')\n"
        "payload = ('AWS_STUB_API_PAYLOAD' if secret_id.endswith('api-keys') "
        "else 'AWS_STUB_GATEWAY_PAYLOAD')\n"
        "if argv[1] == 'get-secret-value':\n"
        "    if secret_id == os.environ.get('AWS_STUB_MISSING_ID'):\n"
        "        print('An error occurred (ResourceNotFoundException)', file=sys.stderr)\n"
        "        raise SystemExit(254)\n"
        "    if os.environ.get('AWS_STUB_FAIL_GET') == '1':\n"
        "        print('An error occurred (AccessDeniedException)', file=sys.stderr)\n"
        "        raise SystemExit(254)\n"
        "    print(os.environ[payload])\n"
        "elif os.environ.get('AWS_STUB_FAIL_PUT') == '1':\n"
        "    raise SystemExit(1)\n"
        "else:\n"
        "    print(json.dumps({'ARN': 'arn:aws:secretsmanager:us-east-1:123:secret:' "
        "+ secret_id, 'Name': secret_id, 'VersionId': 'generated-version'}))\n"
    )
    executable.chmod(0o755)
    return executable


def _stub_environment(tmp_path: Path) -> tuple[dict[str, str], Path]:
    output = tmp_path / "calls.jsonl"
    environment = (
        os.environ
        | _environment()
        | {
            "AWS_STUB_API_PAYLOAD": _payload(API_KEYS_PROPERTIES).decode(),
            "AWS_STUB_GATEWAY_PAYLOAD": _payload(GATEWAY_PROPERTIES).decode(),
            "AWS_STUB_OUTPUT": str(output),
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
        }
    )
    return environment, output


def test_main_preserves_production_values_through_stdin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_aws(tmp_path)
    environment, output = _stub_environment(tmp_path)
    monkeypatch.setattr(production_secrets.os, "environ", environment)

    main()

    calls = [json.loads(line) for line in output.read_text().splitlines()]
    puts = [call for call in calls if call["argv"][1] == "put-secret-value"]
    writes = production_secret_writes(
        environment, _payload(API_KEYS_PROPERTIES), _payload(GATEWAY_PROPERTIES)
    )
    assert [call["argv"] for call in puts] == [list(write.command[1:]) for write in writes]
    assert [call["stdin"].encode() for call in puts] == [write.payload for write in writes]
    assert all("file:///dev/stdin" in call["argv"] for call in puts)
    assert all(value is None for call in calls for value in call["inputs"].values())
    read_ids = [
        call["argv"][call["argv"].index("--secret-id") + 1]
        for call in calls
        if call["argv"][1] == "get-secret-value"
    ]
    assert read_ids == [
        "generated/api-keys",
        "generated/gateway",
    ]


@pytest.mark.parametrize("secret_id", ["generated/api-keys", "generated/gateway"])
def test_main_initializes_missing_production_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, secret_id: str
) -> None:
    _stub_aws(tmp_path)
    environment, output = _stub_environment(tmp_path)
    environment["AWS_STUB_MISSING_ID"] = secret_id
    monkeypatch.setattr(production_secrets.os, "environ", environment)

    main()

    calls = [json.loads(line) for line in output.read_text().splitlines()]
    puts = [call for call in calls if call["argv"][1] == "put-secret-value"]
    assert len(puts) == 2
    written = {
        call["argv"][call["argv"].index("--secret-id") + 1]: json.loads(call["stdin"])
        for call in puts
    }
    if secret_id.endswith("api-keys"):
        expected = dict.fromkeys(API_KEYS_PROPERTIES, "") | {
            "anthropic-api-key": "anthropic-value",
            "browserbase-api-key": "browserbase-value",
            "datadog-api-key": "datadog-value",
            "e2b-api-key": "e2b-value",
            "exa-api-key": "exa-value",
            "openai-api-key": "openai-value",
            "openrouter-api-key": "openrouter-value",
            "turbopuffer-api-key": "turbopuffer-value",
        }
    else:
        expected = {"bot-token": ""}
    assert written[secret_id] == expected


def test_main_propagates_read_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _stub_aws(tmp_path)
    environment, _ = _stub_environment(tmp_path)
    environment["AWS_STUB_FAIL_GET"] = "1"
    monkeypatch.setattr(production_secrets.os, "environ", environment)

    with pytest.raises(subprocess.CalledProcessError):
        main()


def test_main_propagates_write_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _stub_aws(tmp_path)
    environment, _ = _stub_environment(tmp_path)
    environment["AWS_STUB_FAIL_PUT"] = "1"
    monkeypatch.setattr(production_secrets.os, "environ", environment)

    with pytest.raises(subprocess.CalledProcessError):
        main()


def test_production_secret_versions_are_retry_stable_and_deployment_unique() -> None:
    payloads = (_payload(API_KEYS_PROPERTIES), _payload(GATEWAY_PROPERTIES))
    first = production_secret_writes(_environment(deployment_id="run-1"), *payloads)[0]
    retry = production_secret_writes(_environment(deployment_id="run-1"), *payloads)[0]
    changed = production_secret_writes(
        _environment(deployment_id="run-2") | {"OPENAI_API_KEY": "changed"}, *payloads
    )[0]
    rollback = production_secret_writes(_environment(deployment_id="run-3"), *payloads)[0]
    tokens = [
        write.command[write.command.index("--client-request-token") + 1]
        for write in (first, retry, changed, rollback)
    ]
    assert tokens[0] == tokens[1]
    assert len({tokens[0], tokens[2], tokens[3]}) == 3


def test_a_retired_property_is_carried_rather_than_wedging_the_deploy() -> None:
    """Retiring a setting must not need two deploys or a hand-edited secret. Rejecting the extra
    would fail every deploy; dropping it would be a step too early, because the write runs before
    the terraform apply that replaces the ExternalSecret still projecting it — the forced re-sync
    between them would ask for a property the secret no longer holds, never go ready, and time out
    before that apply. Carrying it through costs a stale key nothing reads."""
    retired = json.dumps(
        {name: f"owned-{name}" for name in sorted(API_KEYS_PROPERTIES)} | {"retired": "value"}
    ).encode()
    (api_keys, _gateway) = production_secret_writes(
        _environment(), retired, _payload(GATEWAY_PROPERTIES)
    )
    written = json.loads(api_keys.payload)
    assert written["retired"] == "value"
    assert set(API_KEYS_PROPERTIES) <= set(written)


def test_a_test_mode_credential_never_reaches_production() -> None:
    """Production's secret document is seeded by hand from copies of the testing values, and every
    layer below the deploy accepts what that produces — the payload check reads only that values are
    strings, and the runtime verifies a testing key happily against the testing provider. A
    test-mode Stripe key charges nobody and issues no invoice, so this is the last place it
    shows."""
    seeded = json.dumps(
        {name: f"owned-{name}" for name in sorted(API_KEYS_PROPERTIES)}
        | {"stripe-secret-key": "sk_test_51abcdef"}
    ).encode()
    with pytest.raises(RuntimeError, match="holds test-mode credentials: stripe-secret-key"):
        production_secret_writes(_environment(), seeded, _payload(GATEWAY_PROPERTIES))


def test_a_live_credential_passes_the_mode_check() -> None:
    """The guard reads the credential's own format, so a live key of the same family is untouched —
    a Metronome sandbox token and a testing Slack secret look exactly like production's and stay the
    runbook's job."""
    live = json.dumps(
        {name: f"owned-{name}" for name in sorted(API_KEYS_PROPERTIES)}
        | {"stripe-secret-key": "sk_live_51abcdef"}
    ).encode()
    (api_keys, _gateway) = production_secret_writes(
        _environment(), live, _payload(GATEWAY_PROPERTIES)
    )
    assert json.loads(api_keys.payload)["stripe-secret-key"] == "sk_live_51abcdef"
