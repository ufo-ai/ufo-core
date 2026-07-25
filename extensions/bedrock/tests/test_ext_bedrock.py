from pathlib import Path
from uuid import uuid4

import anthropic
import pytest
import ufo_ext_bedrock as bedrock

from ufo.config import BlobConfig, Config, DatabaseConfig
from ufo.models.anthropic import AnthropicClient
from ufo.models.openai import OpenAIClient
from ufo.models.registry import model_registry
from ufo.workspace import ws


def _spec(model_id: str) -> bedrock.ModelSpec:
    return next(spec for spec in bedrock.BEDROCK_MODEL_SPECS if spec.id == model_id)


def test_manifest_registers_mantle_specs() -> None:
    manifest = bedrock.manifest()
    assert tuple(slot.name for slot in manifest.credentials) == ("bedrock_api_key",)
    by_id = {spec.id: spec for spec in manifest.models}
    assert by_id["anthropic.claude-opus-4-8"].api_surface == "chat"
    assert by_id["openai.gpt-oss-120b"].api_surface == "chat"
    assert by_id["openai.gpt-5.5"].api_surface == "responses"
    assert by_id["openai.gpt-oss-120b"].price.output == 600_000
    assert by_id["openai.gpt-5.5"].price.output == 30_000_000
    assert by_id["anthropic.claude-opus-4-8"].knowledge_cutoff == "2026-01"
    assert by_id["anthropic.claude-opus-5"].context_window == 1_000_000
    assert by_id["anthropic.claude-opus-5"].knowledge_cutoff == "2026-05"
    for spec in manifest.models:
        assert spec.provider == "bedrock"
        assert spec.key_slot == "bedrock_api_key"
        assert spec.key_env == "AWS_BEARER_TOKEN_BEDROCK"


def test_anthropic_specs_build_the_native_mantle_messages_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(bedrock.AWS_REGION_ENV, "us-west-2")
    spec = _spec("anthropic.claude-opus-4-8")
    client = spec.client(spec, "bedrock-key")
    assert isinstance(client, AnthropicClient)
    assert isinstance(client.client, anthropic.AsyncAnthropicBedrockMantle)
    assert str(client.client.base_url) == "https://bedrock-mantle.us-west-2.api.aws/anthropic/"
    assert client.client.auth_headers == {"Authorization": "Bearer bedrock-key"}


def test_openai_chat_specs_build_a_mantle_chat_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(bedrock.AWS_REGION_ENV, "us-east-1")
    spec = _spec("openai.gpt-oss-120b")
    client = spec.client(spec, "bedrock-key")
    assert isinstance(client, OpenAIClient)
    assert client.spec.api_surface == "chat"
    assert str(client.client.base_url) == "https://bedrock-mantle.us-east-1.api.aws/v1/"
    assert client.client.auth_headers == {"Authorization": "Bearer bedrock-key"}


def test_frontier_openai_specs_build_a_mantle_responses_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(bedrock.AWS_REGION_ENV, "us-east-2")
    spec = _spec("openai.gpt-5.5")
    client = spec.client(spec, "bedrock-key")
    assert isinstance(client, OpenAIClient)
    assert client.spec.api_surface == "responses"
    assert str(client.client.base_url) == "https://bedrock-mantle.us-east-2.api.aws/openai/v1/"
    assert client.client.auth_headers == {"Authorization": "Bearer bedrock-key"}


def _config(tmp_path: Path) -> Config:
    return Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///:memory:"),
        blob=BlobConfig(backend="filesystem", root=tmp_path),
    )


async def test_registry_passes_the_platform_bedrock_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(bedrock.AWS_REGION_ENV, "us-west-2")
    monkeypatch.setenv(bedrock.BEDROCK_API_KEY_ENV, "platform-key")
    registry = model_registry(_config(tmp_path), (bedrock.manifest(),))
    with ws(uuid4()):
        client = await registry.client_for("anthropic.claude-opus-4-8")
    assert isinstance(client, AnthropicClient)
    assert client.client.auth_headers == {"Authorization": "Bearer platform-key"}


async def test_registry_fails_loud_without_a_bedrock_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(bedrock.AWS_REGION_ENV, "us-west-2")
    monkeypatch.delenv(bedrock.BEDROCK_API_KEY_ENV, raising=False)
    registry = model_registry(_config(tmp_path), (bedrock.manifest(),))
    with ws(uuid4()), pytest.raises(RuntimeError, match=bedrock.BEDROCK_API_KEY_ENV):
        await registry.client_for("anthropic.claude-opus-4-8")


async def test_registry_fails_loud_without_a_bedrock_region(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(bedrock.BEDROCK_API_KEY_ENV, "bedrock-key")
    monkeypatch.delenv(bedrock.AWS_REGION_ENV, raising=False)
    monkeypatch.delenv(bedrock.AWS_DEFAULT_REGION_ENV, raising=False)
    registry = model_registry(_config(tmp_path), (bedrock.manifest(),))
    with ws(uuid4()), pytest.raises(RuntimeError, match=bedrock.AWS_REGION_ENV):
        await registry.client_for("anthropic.claude-opus-4-8")
