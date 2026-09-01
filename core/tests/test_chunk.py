from ufo.runtime.indexing import TextChunker


def _check_short_text_is_one_chunk() -> None:
    chunks = TextChunker().chunk("A short note about apples.", "memory_item", "m1", "member:me")
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.ordinal == 0
    assert chunk.owner_kind == "memory_item"
    assert chunk.owner_id == "m1"
    assert chunk.subject == "member:me"
    assert chunk.text == "A short note about apples."
    assert chunk.chunk_digest.startswith("sha256:")
    assert chunk.embedding == ()


def _check_long_text_splits_with_sequential_ordinals_and_unique_digests() -> None:
    text = ". ".join(f"Sentence number {index} about the topic" for index in range(400))
    chunks = TextChunker().chunk(text, "page", "p1", "shared")
    assert len(chunks) > 1
    assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))
    assert len({chunk.chunk_digest for chunk in chunks}) == len(chunks)


def _check_blank_text_yields_no_chunks() -> None:
    assert TextChunker().chunk("   \n  ", "memory_item", "m1", "member:me") == ()


def _check_each_chunk_respects_char_cap() -> None:
    chunks = TextChunker(max_chars=6_000).chunk("x" * 20_000, "memory_item", "m1", "member:me")
    assert chunks
    assert all(len(chunk.text) <= 6_000 for chunk in chunks)


def _check_digest_is_deterministic_for_identical_content() -> None:
    a = TextChunker().chunk("Same content here.", "memory_item", "m1", "member:me")
    b = TextChunker().chunk("Same content here.", "memory_item", "m1", "member:me")
    assert a[0].chunk_digest == b[0].chunk_digest


def test_chunk_contract() -> None:
    checks = tuple(value for name, value in globals().items() if name.startswith("_check_"))
    assert len(checks) == 5
    for check in checks:
        check()
