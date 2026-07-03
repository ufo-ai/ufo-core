from selfhost.memory.embed import (
    EMBED_BATCH_MAX_CHARS,
    EMBED_BATCH_MAX_ITEMS,
    EMBED_MAX_ITEM_CHARS,
    plan_embed_batches,
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
