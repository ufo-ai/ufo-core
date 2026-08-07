"""The OpenRouter model-provider extension: id->slug projection, the streaming ModelClient
(reasoning budget, dead-provider re-route, truncation), the registry seam that selects it and prices
its slugs, and `generate_image` over the Image API. The client is driven against a scripted
OpenAI-SDK stub — a fake stands in for the SDK; the ModelEvents and recorded request kwargs are what
the tests assert, never the stub. The image tool runs its real handler over an `httpx.MockTransport`
that records every request and answers canned Image API JSON — no live key or network — while the
key comes from the REAL credential store and the charge lands in the REAL ledger, so the host-side
key read and the `images` metering seam are exercised end to end."""

import base64
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import get_args
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
import ufo_ext_openrouter as openrouter
from cryptography.fernet import Fernet
from openai.types.chat import ChatCompletionChunk
from openai.types.chat.chat_completion_chunk import Choice, ChoiceDelta
from openai.types.completion_usage import CompletionUsage, PromptTokensDetails
from pydantic import ValidationError
from ufo_ext_openrouter import GenerateImageInput

from ufo.accounting import IMAGES_DIMENSION
from ufo.blob import FilesystemBlobStore
from ufo.config import BlobConfig, Config, DatabaseConfig
from ufo.credentials import CredentialStore
from ufo.db import workspace_tx
from ufo.ext.context import context_for
from ufo.models.interface import (
    Message,
    ModelRequest,
    ModelResponseTruncated,
    TextDelta,
    ToolSchema,
)
from ufo.models.registry import model_registry
from ufo.schema import tables
from ufo.schema.records import Agent, Turn, Usage
from ufo.sdk.audience import conversation_audience
from ufo.sdk.credentials import CredentialValueInvalid
from ufo.tools.context import ToolContext
from ufo.workspace import init_workspace_credentials, ws

IMAGE_KEY = "sk-or-v1-secret-0xfeedface"
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n-one").decode()
SECOND_PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n-two").decode()

REQUEST = ModelRequest(
    model="google/gemini-2.5-pro",
    system="be terse",
    messages=(Message(role="user", content="hi"),),
    max_tokens=64,
)


def _chunk(
    content: str | None = None,
    finish: str | None = None,
    provider: str | None = None,
    usage: CompletionUsage | None = None,
) -> ChatCompletionChunk:
    choices = (
        [Choice(index=0, finish_reason=finish, delta=ChoiceDelta(content=content))]
        if content is not None or finish is not None
        else []
    )
    fields: dict[str, object] = {
        "id": "c",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "x",
        "choices": choices,
    }
    if usage is not None:
        fields["usage"] = usage
    if provider is not None:
        fields["provider"] = provider
    return ChatCompletionChunk(**fields)


def _usage(prompt: int, completion: int, cached: int = 0) -> CompletionUsage:
    return CompletionUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        prompt_tokens_details=PromptTokensDetails(cached_tokens=cached),
    )


class ScriptedCreate:
    """Plays the SDK stream factory: one scripted chunk list per call, recording the kwargs sent."""

    def __init__(self, *streams: list[ChatCompletionChunk]) -> None:
        self.streams = list(streams)
        self.calls: list[dict[str, object]] = []

    async def __call__(self, **kwargs: object) -> AsyncIterator[ChatCompletionChunk]:
        self.calls.append(kwargs)
        return _aiter(self.streams[len(self.calls) - 1])


async def _aiter(chunks: list[ChatCompletionChunk]) -> AsyncIterator[ChatCompletionChunk]:
    for chunk in chunks:
        yield chunk


def _client(
    create: ScriptedCreate,
    spec: openrouter.ModelSpec = openrouter.OPENROUTER_MODEL_SPECS[0],
) -> openrouter.OpenRouterModelClient:
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return openrouter.OpenRouterModelClient(client=sdk, spec=spec)


def test_openrouter_slug_maps_bare_ids_and_passes_slugs_through() -> None:
    assert openrouter.openrouter_slug("google/gemini-2.5-pro") == "google/gemini-2.5-pro"
    assert openrouter.openrouter_slug("gpt-5.4") == "openai/gpt-5.4"
    assert openrouter.openrouter_slug("claude-opus-4-8") == "anthropic/claude-opus-4-8"
    assert openrouter.openrouter_slug("grok-2") == "grok-2"


async def test_complete_streams_text_then_usage_without_an_auto_reasoning_budget() -> None:
    create = ScriptedCreate(
        [_chunk(content="ok"), _chunk(finish="stop"), _chunk(usage=_usage(3, 2))]
    )
    events = [event async for event in _client(create).complete(REQUEST)]
    assert events[0] == TextDelta(text="ok")
    assert events[-1] == Usage(input_tokens=3, output_tokens=2)
    kwargs = create.calls[0]
    assert kwargs["model"] == "google/gemini-2.5-pro"
    assert kwargs["extra_body"] == {}
    assert kwargs["stream_options"] == {"include_usage": True}


async def test_cached_prompt_tokens_are_a_disjoint_usage_class() -> None:
    create = ScriptedCreate(
        [_chunk(content="ok"), _chunk(finish="stop"), _chunk(usage=_usage(10, 2, cached=4))]
    )
    events = [event async for event in _client(create).complete(REQUEST)]
    assert events[-1] == Usage(input_tokens=6, output_tokens=2, cache_read_tokens=4)


async def test_reasoning_effort_rides_from_the_request() -> None:
    create = ScriptedCreate(
        [_chunk(content="ok"), _chunk(finish="stop"), _chunk(usage=_usage(1, 1))]
    )
    async for _ in _client(create).complete(REQUEST.model_copy(update={"reasoning": "low"})):
        pass
    assert create.calls[0]["extra_body"] == {"reasoning": {"effort": "low"}}


async def test_reasoning_off_omits_the_reasoning_budget() -> None:
    create = ScriptedCreate(
        [_chunk(content="ok"), _chunk(finish="stop"), _chunk(usage=_usage(1, 1))]
    )
    async for _ in _client(create).complete(REQUEST.model_copy(update={"reasoning": "off"})):
        pass
    assert create.calls[0]["extra_body"] == {}


async def test_model_without_reasoning_omits_the_reasoning_budget() -> None:
    create = ScriptedCreate(
        [_chunk(content="ok"), _chunk(finish="stop"), _chunk(usage=_usage(1, 1))]
    )
    spec = replace(
        openrouter.OPENROUTER_MODEL_SPECS[0],
        reasoning=openrouter.ReasoningSupport(supported=False, tools_with_reasoning=False),
    )
    async for _ in _client(create, spec).complete(REQUEST):
        pass
    assert create.calls[0]["extra_body"] == {}


async def test_model_without_tools_with_reasoning_omits_the_reasoning_budget() -> None:
    create = ScriptedCreate(
        [_chunk(content="ok"), _chunk(finish="stop"), _chunk(usage=_usage(1, 1))]
    )
    spec = replace(
        openrouter.OPENROUTER_MODEL_SPECS[0],
        reasoning=openrouter.ReasoningSupport(supported=True, tools_with_reasoning=False),
    )
    request = REQUEST.model_copy(
        update={
            "tools": (
                ToolSchema(name="search", description="Search", input_schema={"type": "object"}),
            )
        }
    )
    async for _ in _client(create, spec).complete(request):
        pass
    assert create.calls[0]["extra_body"] == {}


async def test_dead_provider_completion_reroutes_excluding_that_provider() -> None:
    dead = [_chunk(finish="stop", provider="deadco"), _chunk(usage=_usage(1, 0))]
    good = [_chunk(content="recovered"), _chunk(finish="stop"), _chunk(usage=_usage(2, 3))]
    create = ScriptedCreate(dead, good)
    events = [event async for event in _client(create).complete(REQUEST)]
    assert len(create.calls) == 2
    assert events[0] == TextDelta(text="recovered")
    assert events[-1] == Usage(input_tokens=2, output_tokens=3)
    assert create.calls[1]["extra_body"]["provider"] == {"ignore": ["deadco"]}


async def test_length_finish_raises_truncated() -> None:
    create = ScriptedCreate(
        [_chunk(content="cut"), _chunk(finish="length"), _chunk(usage=_usage(1, 9))]
    )
    with pytest.raises(ModelResponseTruncated):
        [event async for event in _client(create).complete(REQUEST)]


def test_manifest_registers_slug_pinned_specs() -> None:
    manifest = openrouter.manifest()
    by_id = {spec.id: spec for spec in manifest.models}
    assert set(by_id) == {"google/gemini-2.5-pro", "z-ai/glm-5.2", "moonshotai/kimi-k3"}
    assert by_id["z-ai/glm-5.2"].price.output == 3_000_000
    assert by_id["z-ai/glm-5.2"].knowledge_cutoff == "2026-03"


def test_kimi_k3_spec_carries_its_price_cache_rate_and_million_token_window() -> None:
    spec = {s.id: s for s in openrouter.manifest().models}["moonshotai/kimi-k3"]
    assert spec.price.input == 3_000_000
    assert spec.price.output == 15_000_000
    assert spec.price.cache_read == 300_000
    assert spec.context_window == 1_000_000


async def test_model_client_requires_its_key(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.delenv(openrouter.OPENROUTER_API_KEY_ENV, raising=False)
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///:memory:"),
        blob=BlobConfig(backend="filesystem", root=tmp_path),
    )
    registry = model_registry(config, (openrouter.manifest(),))
    with ws(uuid4()), pytest.raises(RuntimeError, match=openrouter.OPENROUTER_API_KEY_ENV):
        await registry.client_for("google/gemini-2.5-pro")


async def test_registry_rejects_a_non_ascii_openrouter_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv(openrouter.OPENROUTER_API_KEY_ENV, "—")
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///:memory:"),
        blob=BlobConfig(backend="filesystem", root=tmp_path),
    )
    registry = model_registry(config, (openrouter.manifest(),))
    with ws(uuid4()):
        with pytest.raises(
            CredentialValueInvalid,
            match=(
                r"model 'google/gemini-2\.5-pro' key contains non-ASCII characters: "
                rf"env {openrouter.OPENROUTER_API_KEY_ENV} or the workspace's "
                rf"{openrouter.OPENROUTER_KEY_SLOT!r} BYOK slot holds a value "
                r"the provider wire cannot carry\."
            ),
        ):
            await registry.client_for("google/gemini-2.5-pro")


async def test_registry_selects_openrouter_and_prices_its_slug(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(openrouter.OPENROUTER_API_KEY_ENV, "sk-openrouter-test")
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///:memory:"),
        blob=BlobConfig(backend="filesystem", root=tmp_path),
    )
    registry = model_registry(config, (openrouter.manifest(),))
    with ws(uuid4()):
        client = await registry.client_for("google/gemini-2.5-pro")
    assert isinstance(client, openrouter.OpenRouterModelClient)
    assert client.spec is registry.spec("google/gemini-2.5-pro")
    priced = registry.pricing.micro_usd(
        "google/gemini-2.5-pro", Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    )
    assert priced == 11_000_000


@dataclass
class _ImageApi:
    """Answers `POST /api/v1/images` with canned Image API JSON and records every request."""

    images: list[dict[str, object]] = field(
        default_factory=lambda: [{"b64_json": PNG, "media_type": "image/png"}]
    )
    usage: dict[str, object] | None = field(default_factory=lambda: {"cost": 0.08})
    status: int = 200
    error_body: dict[str, object] | None = None
    requests: list[httpx.Request] = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path != "/api/v1/images":
            raise AssertionError(f"unscripted request: {request.method} {request.url}")
        if self.error_body is not None:
            return httpx.Response(self.status, json=self.error_body)
        body: dict[str, object] = {"created": 1748372400, "data": self.images}
        if self.usage is not None:
            body["usage"] = self.usage
        return httpx.Response(self.status, json=body)

    def sent(self) -> dict[str, object]:
        (request,) = self.requests
        return json.loads(request.content)


@dataclass
class _Sandbox:
    """Captures the bytes the image tool writes into the workspace."""

    writes: dict[str, bytes] = field(default_factory=dict)

    async def write_file(self, path: str, content: bytes) -> None:
        self.writes[path] = content


async def _keyed_turn(stored: bool = False) -> tuple[UUID, UUID]:
    """One running turn to meter against, keyed either way: `stored` puts the workspace's own
    OpenRouter key in the credential store (BYOK — OpenRouter bills the workspace directly), and the
    default leaves the platform's `OPENROUTER_API_KEY` env to serve it."""
    workspace_id, member_id, agent_id, conversation_id, turn_id = (uuid4() for _ in range(5))
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="a@b.c",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="cli",
                queue_key="session",
                member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="running",
                inbound="draw me a poster",
                terminal=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    if stored:
        await store.put(workspace_id, openrouter.OPENROUTER_KEY_SLOT, IMAGE_KEY)
    return workspace_id, turn_id


def _context(workspace_id: UUID, turn_id: UUID, sandbox: _Sandbox, tmp_path: Path) -> ToolContext:
    return ToolContext(
        sandbox=sandbox,
        blob=FilesystemBlobStore(root=tmp_path),
        turn=Turn(
            id=turn_id,
            workspace_id=workspace_id,
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="draw me a poster",
            created_at=datetime(2026, 8, 6, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=None,
        speaker_member_id=None,
        audience=conversation_audience(None),
        artifact_token_secret="",
        idempotency_key="turn/generate_image/call-1",
        ext=context_for(openrouter.NAME, frozenset({openrouter.OPENROUTER_KEY_SLOT})),
    )


def _wire(monkeypatch: pytest.MonkeyPatch, api: _ImageApi) -> None:
    monkeypatch.setattr(openrouter, "IMAGE_TRANSPORT", httpx.MockTransport(api.handle))
    monkeypatch.setenv(openrouter.OPENROUTER_API_KEY_ENV, IMAGE_KEY)


async def _images_ledger(turn_id: UUID) -> tuple[int, int, str, str | None] | None:
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.ledger.c.amount,
                    tables.ledger.c.priced_micro_usd,
                    tables.ledger.c.model,
                    tables.ledger.c.price_digest,
                ).where(
                    (tables.ledger.c.turn_id == turn_id)
                    & (tables.ledger.c.dimension == IMAGES_DIMENSION)
                )
            )
        ).one_or_none()
    if row is None:
        return None
    return int(row.amount), int(row.priced_micro_usd), row.model, row.price_digest


async def _generate(
    workspace_id: UUID, turn_id: UUID, sandbox: _Sandbox, tmp_path: Path, **overrides: object
):
    args = GenerateImageInput(
        **{
            "prompt": "a red panda astronaut, studio lighting",
            "name": "poster",
            "user_description": "drawing a poster",
            **overrides,
        }
    )
    with ws(workspace_id):
        return await openrouter.GENERATE_IMAGE_TOOL.handler(
            _context(workspace_id, turn_id, sandbox, tmp_path), args
        )


def test_the_image_allowlist_and_its_limits_name_the_same_models() -> None:
    """The wire schema's model enum and the limits table are one allowlist: a model the agent can
    ask for that has no entry would price a cost-less response at a KeyError."""
    assert set(get_args(openrouter.ImageModel)) == set(openrouter.IMAGE_MODELS)
    assert openrouter.DEFAULT_IMAGE_MODEL == "bytedance-seed/seedream-4.5"
    assert "openai/gpt-image-2" in openrouter.IMAGE_MODELS


def test_every_advertised_aspect_ratio_is_served_by_the_default_model() -> None:
    """The field offers one ratio enum across every model, so the default must serve all of it; a
    model serving less narrows the call in `_within_model_limits`, never the schema."""
    limits = openrouter.IMAGE_MODELS[openrouter.DEFAULT_IMAGE_MODEL]
    assert set(get_args(openrouter.AspectRatio)) == limits.aspect_ratios
    assert openrouter.IMAGE_MODELS["recraft/recraft-v4.1"].aspect_ratios < limits.aspect_ratios


def test_manifest_publishes_the_image_tool_and_the_key_slot() -> None:
    """The key slot is declared because a tool reads credentials only for slots its manifest names;
    the model specs resolve the same slot, so models and images run on one key."""
    manifest = openrouter.manifest()
    (tool,) = manifest.tools
    assert tool.name == "generate_image"
    assert tool.side_effecting
    (slot,) = manifest.credentials
    assert slot.name == openrouter.OPENROUTER_KEY_SLOT
    assert slot.injection is None
    assert {spec.key_slot for spec in manifest.models} == {openrouter.OPENROUTER_KEY_SLOT}


def test_the_payload_is_bounded_at_the_tool_boundary() -> None:
    common = {"prompt": "p", "name": "poster", "user_description": "d"}
    with pytest.raises(ValidationError):
        GenerateImageInput(**common, n=openrouter.MAX_IMAGES_PER_CALL + 1)
    with pytest.raises(ValidationError):
        GenerateImageInput(**{**common, "prompt": "x" * (openrouter.MAX_IMAGE_PROMPT_CHARS + 1)})
    with pytest.raises(ValidationError):
        GenerateImageInput(**{**common, "name": "../escape"})
    with pytest.raises(ValidationError):
        GenerateImageInput(**common, model="stability/whatever")


def test_the_offered_resolution_tiers_are_the_ones_seedream_draws() -> None:
    """Seed's parameter list names `1K`, and Seed then refuses to render it: it draws at least
    3,686,400 output pixels and 1K is 1,048,576 at every aspect ratio. What the field offers is
    what came back as an image, so `1K` is not a tier here however the parameter list reads."""
    common = {"prompt": "p", "name": "poster", "user_description": "d"}
    tiers, _none = get_args(GenerateImageInput.model_fields["resolution"].annotation)
    assert set(get_args(tiers)) == {"2K", "4K"}
    assert openrouter.IMAGE_MODELS[openrouter.DEFAULT_IMAGE_MODEL].resolutions == set(
        get_args(tiers)
    )
    for below in ("1K", "512"):
        with pytest.raises(ValidationError):
            GenerateImageInput(**common, resolution=below)


def test_an_unasked_resolution_settles_on_the_cheapest_tier_that_draws() -> None:
    """A call that names no tier draws at 2K rather than whatever the provider would pick, and 4K
    stays reachable for the member who wants it."""
    common = {"prompt": "p", "name": "poster", "user_description": "d"}
    assert GenerateImageInput(**common).resolution == openrouter.DEFAULT_RESOLUTION
    assert openrouter.DEFAULT_RESOLUTION == "2K"
    for tier in ("2K", "4K"):
        assert GenerateImageInput(**common, resolution=tier).resolution == tier


def test_a_model_that_sizes_its_own_output_is_sent_no_tier() -> None:
    """Only seedream takes a resolution, so the default is never applied to the others and naming
    one for them is refused rather than sent as a parameter their providers do not serve."""
    common = {"prompt": "p", "name": "poster", "user_description": "d"}
    for model in ("openai/gpt-image-2", "black-forest-labs/flux.2-pro", "recraft/recraft-v4.1"):
        assert GenerateImageInput(**common, model=model).resolution is None
        with pytest.raises(ValidationError, match="takes no resolution tier"):
            GenerateImageInput(**common, model=model, resolution="2K")


def test_a_model_that_draws_one_image_refuses_a_batch() -> None:
    """The flux.2 models are `n: 1-1` upstream, so a batch the schema's own cap allows is refused
    here rather than spent on a 400 mid-turn."""
    common = {"prompt": "p", "name": "poster", "user_description": "d"}
    for model in ("black-forest-labs/flux.2-pro", "black-forest-labs/flux.2-klein-4b"):
        assert GenerateImageInput(**common, model=model, n=1).n == 1
        with pytest.raises(ValidationError, match="at most 1 image"):
            GenerateImageInput(**common, model=model, n=2)
    assert GenerateImageInput(**common, model="bytedance-seed/seedream-4.5", n=2).n == 2


def test_an_aspect_ratio_the_chosen_model_does_not_serve_is_refused() -> None:
    """recraft serves five of the eight ratios the field offers; the other three are a 400 from
    OpenRouter, so they are caught where the model can read why and pick one it serves."""
    common = {"prompt": "p", "name": "poster", "user_description": "d"}
    for ratio in ("3:2", "2:3", "21:9"):
        with pytest.raises(ValidationError, match="does not take aspect_ratio"):
            GenerateImageInput(**common, model="recraft/recraft-v4.1", aspect_ratio=ratio)
        assert (
            GenerateImageInput(
                **common, model="bytedance-seed/seedream-4.5", aspect_ratio=ratio
            ).aspect_ratio
            == ratio
        )
    assert (
        GenerateImageInput(**common, model="recraft/recraft-v4.1", aspect_ratio="16:9").aspect_ratio
        == "16:9"
    )


async def test_generate_image_posts_the_bounded_request_and_saves_every_image(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = _ImageApi(
        images=[
            {"b64_json": PNG, "media_type": "image/png"},
            {"b64_json": SECOND_PNG, "media_type": "image/webp"},
        ]
    )
    _wire(monkeypatch, api)
    workspace_id, turn_id = await _keyed_turn()
    sandbox = _Sandbox()
    result = await _generate(
        workspace_id,
        turn_id,
        sandbox,
        tmp_path,
        n=2,
        resolution="2K",
        aspect_ratio="16:9",
        quality="high",
    )

    (request,) = api.requests
    assert request.headers["authorization"] == f"Bearer {IMAGE_KEY}"
    assert api.sent() == {
        "model": "bytedance-seed/seedream-4.5",
        "prompt": "a red panda astronaut, studio lighting",
        "n": 2,
        "resolution": "2K",
        "aspect_ratio": "16:9",
        "quality": "high",
    }
    assert sandbox.writes == {
        "generated-images/poster-1.png": base64.b64decode(PNG),
        "generated-images/poster-2.webp": base64.b64decode(SECOND_PNG),
    }
    assert json.loads(result.content[0].text) == {
        "model": "bytedance-seed/seedream-4.5",
        "files": ["generated-images/poster-1.png", "generated-images/poster-2.webp"],
        "machine_generated": True,
        "cost_micro_usd": 80_000,
    }
    assert [(block.type, block.media_type) for block in result.content[1:]] == [
        ("image", "image/png"),
        ("image", "image/webp"),
    ]
    assert not result.is_error


async def test_the_providers_reported_cost_meters_onto_the_turn(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OpenRouter reports what it charged for this generation whatever unit the upstream billed in,
    so that number is the ledger's, and the row counts images rather than tokens. A default call
    also sends only the fields every allowlisted model accepts — GPT Image 2 takes no
    `resolution`."""
    api = _ImageApi(usage={"prompt_tokens": 0, "completion_tokens": 4175, "cost": 0.1234})
    _wire(monkeypatch, api)
    workspace_id, turn_id = await _keyed_turn()
    await _generate(workspace_id, turn_id, _Sandbox(), tmp_path, model="openai/gpt-image-2")
    assert api.sent() == {
        "model": "openai/gpt-image-2",
        "prompt": "a red panda astronaut, studio lighting",
        "n": 1,
    }
    assert await _images_ledger(turn_id) == (1, 123_400, "openai/gpt-image-2", None)


async def test_a_response_without_a_cost_meters_the_models_list_rate(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = _ImageApi(
        images=[{"b64_json": PNG}, {"b64_json": SECOND_PNG}],
        usage={"prompt_tokens": 0, "completion_tokens": 4175},
    )
    _wire(monkeypatch, api)
    workspace_id, turn_id = await _keyed_turn()
    sandbox = _Sandbox()
    result = await _generate(workspace_id, turn_id, sandbox, tmp_path, n=2)
    list_rate = openrouter.IMAGE_MODELS["bytedance-seed/seedream-4.5"].list_micro_usd
    assert await _images_ledger(turn_id) == (
        2,
        2 * list_rate,
        "bytedance-seed/seedream-4.5",
        None,
    )
    assert sorted(sandbox.writes) == [
        "generated-images/poster-1.png",
        "generated-images/poster-2.png",
    ]
    assert [block.media_type for block in result.content[1:]] == ["image/png", "image/png"]


async def test_a_byok_generation_meters_the_upstream_charge_it_reports(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A workspace running its own upstream key is charged nothing by OpenRouter, which reports
    `cost` as zero and states the real spend under `cost_details`. That is the same money one hop
    further out, so it is what the turn is metered — a reported zero is not a free image."""
    api = _ImageApi(
        usage={
            "prompt_tokens": 12,
            "completion_tokens": 229,
            "cost": 0,
            "is_byok": True,
            "cost_details": {"upstream_inference_cost": 0.00693},
        }
    )
    _wire(monkeypatch, api)
    workspace_id, turn_id = await _keyed_turn()
    await _generate(workspace_id, turn_id, _Sandbox(), tmp_path, model="openai/gpt-image-2")
    assert await _images_ledger(turn_id) == (1, 6_930, "openai/gpt-image-2", None)


async def test_a_default_seedream_call_puts_the_cheap_tier_on_the_wire(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The settled default is a real request field, not merely a validated value: a call naming no
    tier reaches OpenRouter asking for 2K."""
    api = _ImageApi()
    _wire(monkeypatch, api)
    workspace_id, turn_id = await _keyed_turn()
    await _generate(workspace_id, turn_id, _Sandbox(), tmp_path)
    assert api.sent() == {
        "model": "bytedance-seed/seedream-4.5",
        "prompt": "a red panda astronaut, studio lighting",
        "n": 1,
        "resolution": "2K",
    }


async def test_a_workspace_on_its_own_key_is_not_metered_for_its_own_spend(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OpenRouter bills a stored `openrouter_api_key` directly, and an `images` export carries no
    byok label to hold the consumer off, so metering that call would charge the workspace a second
    time for spend it has already paid. The images are still generated and saved."""
    api = _ImageApi(usage={"prompt_tokens": 0, "completion_tokens": 4175, "cost": 0.1234})
    _wire(monkeypatch, api)
    workspace_id, turn_id = await _keyed_turn(stored=True)
    sandbox = _Sandbox()
    result = await _generate(workspace_id, turn_id, sandbox, tmp_path)

    (request,) = api.requests
    assert request.headers["authorization"] == f"Bearer {IMAGE_KEY}"
    assert not result.is_error
    assert sorted(sandbox.writes) == ["generated-images/poster-1.png"]
    assert await _images_ledger(turn_id) is None


async def test_a_zero_cost_carrying_no_upstream_charge_falls_back_to_the_list_rate(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, _ImageApi(usage={"prompt_tokens": 0, "completion_tokens": 4175, "cost": 0}))
    workspace_id, turn_id = await _keyed_turn()
    await _generate(workspace_id, turn_id, _Sandbox(), tmp_path)
    list_rate = openrouter.IMAGE_MODELS["bytedance-seed/seedream-4.5"].list_micro_usd
    assert await _images_ledger(turn_id) == (1, list_rate, "bytedance-seed/seedream-4.5", None)


async def test_two_generations_on_one_turn_accumulate_into_one_images_row(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, _ImageApi())
    workspace_id, turn_id = await _keyed_turn()
    await _generate(workspace_id, turn_id, _Sandbox(), tmp_path)
    await _generate(workspace_id, turn_id, _Sandbox(), tmp_path, name="second")
    assert await _images_ledger(turn_id) == (2, 160_000, "bytedance-seed/seedream-4.5", None)


async def test_a_provider_refusal_returns_its_message_and_bills_nothing(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A generation OpenRouter refused is not billed by OpenRouter, so it is not metered here, and
    the refusal reaches the model as tool-result text rather than a raised failure."""
    api = _ImageApi(
        status=400, error_body={"error": {"message": "prompt rejected by the safety system"}}
    )
    _wire(monkeypatch, api)
    workspace_id, turn_id = await _keyed_turn()
    sandbox = _Sandbox()
    result = await _generate(workspace_id, turn_id, sandbox, tmp_path)
    assert result.is_error
    assert "prompt rejected by the safety system" in result.content[0].text
    assert "bytedance-seed/seedream-4.5" in result.content[0].text
    assert sandbox.writes == {}
    assert await _images_ledger(turn_id) is None


async def test_an_image_over_the_byte_cap_is_never_written(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, _ImageApi())
    monkeypatch.setattr(openrouter, "MAX_IMAGE_BYTES", 4)
    workspace_id, turn_id = await _keyed_turn()
    sandbox = _Sandbox()
    with pytest.raises(openrouter.OpenRouterImageError, match="saves at most 4"):
        await _generate(workspace_id, turn_id, sandbox, tmp_path)
    assert sandbox.writes == {}
    assert await _images_ledger(turn_id) is None


async def test_one_oversized_image_leaves_none_of_the_generation_on_disk(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cap is met on every image before any of them lands, so a batch whose second image is
    too large writes nothing rather than half a generation the member would have to sort out."""
    oversized = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 64).decode()
    _wire(
        monkeypatch,
        _ImageApi(images=[{"b64_json": PNG}, {"b64_json": oversized}]),
    )
    monkeypatch.setattr(openrouter, "MAX_IMAGE_BYTES", 32)
    workspace_id, turn_id = await _keyed_turn()
    sandbox = _Sandbox()
    with pytest.raises(openrouter.OpenRouterImageError, match="saves at most 32"):
        await _generate(workspace_id, turn_id, sandbox, tmp_path, n=2)
    assert sandbox.writes == {}
    assert await _images_ledger(turn_id) is None


async def test_a_success_carrying_no_image_fails_loud(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, _ImageApi(images=[]))
    workspace_id, turn_id = await _keyed_turn()
    with pytest.raises(openrouter.OpenRouterImageError, match="no image data"):
        await _generate(workspace_id, turn_id, _Sandbox(), tmp_path)
    assert await _images_ledger(turn_id) is None
