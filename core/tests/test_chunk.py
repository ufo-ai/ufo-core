import asyncio
import time

from ufo.runtime.indexing import (
    DELIMITER_PATTERNS,
    Chunk,
    Hit,
    IndexScope,
    TextChunker,
    chunk_embed_upsert,
)


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


def _check_delimiter_split_ends_each_piece_at_its_delimiter_and_drops_blank_pieces() -> None:
    sentences = DELIMITER_PATTERNS[2]
    assert sentences is not None
    assert TextChunker._split_at_delimiters("One. Two! Three?\n \n Four", sentences) == [
        "One. ",
        "Two! ",
        "Three?\n",
        " \n Four",
    ]
    assert TextChunker._split_at_delimiters("Only. ", sentences) == ["Only. "]
    assert TextChunker._split_at_delimiters(". . \n ", sentences) == [". ", ". "]
    assert TextChunker._split_at_delimiters("no delimiter here", sentences) == ["no delimiter here"]


def test_chunk_contract() -> None:
    checks = tuple(value for name, value in globals().items() if name.startswith("_check_"))
    assert len(checks) == 6
    for check in checks:
        check()


class _SlowChunker(TextChunker):
    def chunk(self, text: str, owner_kind: str, owner_id: str, subject: str) -> tuple[Chunk, ...]:
        time.sleep(0.3)
        return ()


class _Index:
    async def upsert(self, chunks: tuple[Chunk, ...]) -> None:
        raise AssertionError("no chunks to upsert")

    async def delete(self, scope: IndexScope) -> None:
        raise AssertionError("nothing to delete")

    async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None:
        assert keep == frozenset()

    async def has_chunks(self, scope: IndexScope) -> bool:
        return False

    async def restamp(self, scope: IndexScope, subject: str, keep: frozenset[str]) -> bool:
        return False

    async def lexical(
        self, query: str, subjects: frozenset[str], owner_kind: str, limit: int
    ) -> tuple[Hit, ...]:
        return ()

    async def vector(
        self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int
    ) -> tuple[Hit, ...]:
        return ()


class _Embed:
    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        raise AssertionError("no chunks to embed")


async def test_chunking_leaves_the_loop_free_for_the_turns_it_shares_it_with() -> None:
    ticks = 0

    async def tick() -> None:
        nonlocal ticks
        for _ in range(10):
            await asyncio.sleep(0.01)
            ticks += 1

    ticker = asyncio.create_task(tick())
    await chunk_embed_upsert(_Index(), _Embed(), _SlowChunker(), "page", "p1", "shared", "body")
    ticks_while_chunking = ticks
    await ticker
    assert ticks_while_chunking == 10
