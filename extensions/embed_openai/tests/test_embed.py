import pytest
from ufo_ext_embed_openai import (
    API_KEY_ENV,
    EMBED_BATCH_MAX_ITEMS,
    EMBED_MAX_ITEM_CHARS,
    OpenAIEmbedClient,
    build,
    plan_embed_batches,
)

from ufo.runtime.ext.context import CredentialAccess, ExtensionContext, ScopedStore


def _ctx() -> ExtensionContext:
    return ExtensionContext(
        store=ScopedStore(extension="embed_openai"),
        credentials=CredentialAccess(declared=frozenset()),
    )


def test_empty_input_plans_no_batches() -> None:
    assert plan_embed_batches(()) == ()


def test_splits_on_item_count() -> None:
    texts = tuple("x" for _ in range(EMBED_BATCH_MAX_ITEMS + 5))
    batches = plan_embed_batches(texts)
    assert [len(batch) for batch in batches] == [EMBED_BATCH_MAX_ITEMS, 5]


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
    monkeypatch.delenv(f"UFO_{API_KEY_ENV}", raising=False)
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    with pytest.raises(RuntimeError, match=rf"UFO_{API_KEY_ENV} \(or {API_KEY_ENV}\) required"):
        await build(_ctx()).embed(("hello",))
