from types import SimpleNamespace
from uuid import uuid4

import pytest
from openai.resources.embeddings import AsyncEmbeddings
from ufo_ext_embed_openai import (
    API_KEY_ENV,
    EMBED_BATCH_MAX_CHARS,
    EMBED_BATCH_MAX_ITEMS,
    EMBED_MAX_ITEM_CHARS,
    EMBED_MODEL,
    OpenAIEmbedClient,
    build,
    plan_embed_batches,
)

from ufo.ext.context import CredentialAccess, ExtensionContext, ScopedStore


def _ctx() -> ExtensionContext:
    workspace_id = uuid4()
    return ExtensionContext(
        store=ScopedStore(workspace_id=workspace_id, extension="embed-openai"),
        credentials=CredentialAccess(workspace_id=workspace_id, declared=frozenset(), _store=None),
    )


def test_empty_input_plans_no_batches() -> None:
    assert plan_embed_batches(()) == ()


def test_splits_on_item_count() -> None:
    texts = tuple("x" for _ in range(EMBED_BATCH_MAX_ITEMS + 5))
    batches = plan_embed_batches(texts)
    assert [len(batch) for batch in batches] == [EMBED_BATCH_MAX_ITEMS, 5]


def test_splits_on_char_budget() -> None:
    item = "y" * EMBED_MAX_ITEM_CHARS
    count = EMBED_BATCH_MAX_CHARS // EMBED_MAX_ITEM_CHARS + 1
    batches = plan_embed_batches(tuple(item for _ in range(count)))
    assert len(batches) == 2
    assert sum(len(batch) for batch in batches) == count


def test_clips_oversized_item() -> None:
    huge = "z" * (EMBED_MAX_ITEM_CHARS + 100)
    batches = plan_embed_batches((huge,))
    assert len(batches[0][0]) == EMBED_MAX_ITEM_CHARS


def test_build_boots_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact boot crash: the OpenAI SDK raises on an empty key at construction, so a zero-config
    dev serve with no OPENAI_API_KEY must still build the base-pinned default embed backend."""
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    client = build(_ctx())
    assert isinstance(client, OpenAIEmbedClient)


async def test_embed_without_a_key_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    """Boot is key-free, so the fail-loud moves to use: an embed call with no key raises a clear
    error rather than silently no-opping or falling back to another provider."""
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    with pytest.raises(RuntimeError, match=f"{API_KEY_ENV} required to embed"):
        await build(_ctx()).embed(("hello",))


async def test_embed_with_a_key_constructs_and_returns_vectors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the key set the real OpenAI SDK client constructs; only the network create is stubbed,
    so the batch planning and out-of-order response reassembly run for real."""
    monkeypatch.setenv(API_KEY_ENV, "test-key")

    seen: dict[str, object] = {}

    async def fake_create(_self: object, *, model: str, input: list[str]) -> object:
        seen["model"] = model
        seen["input"] = tuple(input)
        rows = [
            SimpleNamespace(index=i, embedding=[float(i), float(len(text))])
            for i, text in enumerate(input)
        ]
        return SimpleNamespace(data=list(reversed(rows)))

    monkeypatch.setattr(AsyncEmbeddings, "create", fake_create)

    vectors = await build(_ctx()).embed(("a", "bb"))

    assert seen["model"] == EMBED_MODEL
    assert seen["input"] == ("a", "bb")
    assert vectors == ((0.0, 1.0), (1.0, 2.0))
