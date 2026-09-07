"""The index seam and its value objects: the retrieval contract, chunking, and the derivation step.

`IndexBackend` and `EmbedClient` are the two Protocols an extension implements to contribute
retrieval and embedding; `Chunk`/`Hit`/`IndexScope` are the dialect-neutral value objects that cross
that seam. `TextChunker.chunk` is the workflow — recursive-delimiter split to ~target-word pieces
with sentence-aware overlap, char-capped. `chunk_embed_upsert` is the derivation step a memory or
page indexer shares: chunk one body, embed each chunk, upsert them under the owner, then prune the
owner's chunks outside that set so an edit leaves no orphan. None of these touch the database or a
dialect — a backend does storage, ANN/FTS, and the subject filter; these stay in-process value
objects reached by both core and the extensions that implement the seam.
"""

import asyncio
import hashlib
import itertools
import math
import re
from dataclasses import dataclass, replace
from typing import Protocol

CHUNK_TARGET_WORDS = 300
CHUNK_OVERLAP_WORDS = 50
CHUNK_MAX_CHARS = 6_000
CJK_DENSITY_THRESHOLD = 0.30
CJK_CHARS = re.compile(r"[一-鿿぀-ゟ゠-ヿ가-힯]")  # noqa: RUF001
WORD_RUNS = re.compile(r"\S+\s*")
SENTENCE_BOUNDARY = re.compile(r"[.!?]\s+")
DELIMITER_LEVELS: tuple[tuple[str, ...], ...] = (
    ("\n\n",),
    ("\n",),
    (". ", "! ", "? ", ".\n", "!\n", "?\n", "。", "！", "？"),  # noqa: RUF001
    ("; ", ": ", ", ", "；", "：", "，", "、"),  # noqa: RUF001
    (),
)
DELIMITER_PATTERNS: tuple[re.Pattern[str] | None, ...] = tuple(
    re.compile("|".join(re.escape(delimiter) for delimiter in sorted(level))) if level else None
    for level in DELIMITER_LEVELS
)

OWNER_KIND_MEMORY_ITEM = "memory_item"
OWNER_KIND_PAGE = "page"


@dataclass(frozen=True)
class Chunk:
    chunk_digest: str
    owner_kind: str
    owner_id: str
    subject: str
    ordinal: int
    text: str
    embedding: tuple[float, ...] = ()


@dataclass(frozen=True)
class Hit:
    chunk_digest: str
    owner_kind: str
    owner_id: str
    subject: str
    ordinal: int
    text: str
    score: float


@dataclass(frozen=True)
class IndexScope:
    owner_kind: str
    owner_id: str


class IndexBackend(Protocol):
    async def upsert(self, chunks: tuple[Chunk, ...]) -> None: ...

    async def delete(self, scope: IndexScope) -> None: ...

    async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None: ...

    async def has_chunks(self, scope: IndexScope) -> bool: ...

    async def lexical(
        self, query: str, subjects: frozenset[str], owner_kind: str, limit: int
    ) -> tuple[Hit, ...]: ...

    async def vector(
        self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int
    ) -> tuple[Hit, ...]: ...


class EmbedClient(Protocol):
    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]: ...


async def chunk_embed_upsert(
    index: IndexBackend,
    embed: EmbedClient,
    chunker: "TextChunker",
    owner_kind: str,
    owner_id: str,
    subject: str,
    body: str,
) -> None:
    """Chunk one body, embed each chunk, upsert them under the owner, then prune the owner's chunks
    outside this desired set — the derivation step both indexers share. Upsert is idempotent on
    chunk_digest, so a re-run over unchanged content rewrites the same rows; the prune drops the
    digests an edit no longer produces (all of them when the new body is empty), so re-chunked
    content leaves no orphaned chunk to surface as a stale hit. Chunking is pure-Python CPU work
    over the whole body, so it runs in a thread: a job re-indexing thousands of pages must not hold
    the loop that serves every turn and every request of the process."""
    chunks = await asyncio.to_thread(chunker.chunk, body, owner_kind, owner_id, subject)
    if chunks:
        vectors = await embed.embed(tuple(chunk.text for chunk in chunks))
        await index.upsert(
            tuple(
                replace(chunk, embedding=vector)
                for chunk, vector in zip(chunks, vectors, strict=True)
            )
        )
    await index.prune(
        IndexScope(owner_kind, owner_id), frozenset(chunk.chunk_digest for chunk in chunks)
    )


@dataclass(frozen=True)
class TextChunker:
    target_words: int = CHUNK_TARGET_WORDS
    overlap_words: int = CHUNK_OVERLAP_WORDS
    max_chars: int = CHUNK_MAX_CHARS

    def chunk(self, text: str, owner_kind: str, owner_id: str, subject: str) -> tuple[Chunk, ...]:
        return tuple(
            Chunk(
                chunk_digest=self._digest(owner_kind, owner_id, subject, ordinal, piece),
                owner_kind=owner_kind,
                owner_id=owner_id,
                subject=subject,
                ordinal=ordinal,
                text=piece,
            )
            for ordinal, piece in enumerate(self._slices(text))
        )

    def _slices(self, text: str) -> list[str]:
        if not text.strip():
            return []
        if self._count_words(text) <= self.target_words:
            return self._cap_by_chars(text.strip())
        pieces = self._recursive_split(text, 0)
        merged = self._greedy_merge(pieces)
        overlapped = self._apply_overlap(merged)
        return [capped for piece in overlapped for capped in self._cap_by_chars(piece.strip())]

    @staticmethod
    def _count_words(text: str) -> int:
        non_whitespace = len(re.sub(r"\s", "", text))
        if non_whitespace == 0:
            return 0
        if len(CJK_CHARS.findall(text)) / non_whitespace >= CJK_DENSITY_THRESHOLD:
            return non_whitespace
        return len(WORD_RUNS.findall(text))

    def _cap_by_chars(self, text: str) -> list[str]:
        if len(text) <= self.max_chars:
            return [text] if text else []
        overlap = min(500, self.max_chars // 10)
        stride = max(1, self.max_chars - overlap)
        output: list[str] = []
        for start in range(0, len(text), stride):
            piece = text[start : start + self.max_chars].strip()
            if piece:
                output.append(piece)
            if start + self.max_chars >= len(text):
                break
        return output

    def _recursive_split(self, text: str, level: int) -> list[str]:
        pattern = DELIMITER_PATTERNS[level] if level < len(DELIMITER_PATTERNS) else None
        if pattern is None:
            return self._split_on_whitespace(text)
        pieces = self._split_at_delimiters(text, pattern)
        if len(pieces) <= 1:
            return self._recursive_split(text, level + 1)
        result: list[str] = []
        for piece in pieces:
            if self._count_words(piece) > self.target_words:
                result.extend(self._recursive_split(piece, level + 1))
            else:
                result.append(piece)
        return result

    @staticmethod
    def _split_at_delimiters(text: str, delimiters: re.Pattern[str]) -> list[str]:
        pieces: list[str] = []
        start = 0
        for match in delimiters.finditer(text):
            pieces.append(text[start : match.end()])
            start = match.end()
        pieces.append(text[start:])
        return [piece for piece in pieces if piece.strip()]

    def _split_on_whitespace(self, text: str) -> list[str]:
        words = WORD_RUNS.findall(text)
        target = self.target_words
        if not words or (len(words) == 1 and len(words[0]) > target):
            if not text.strip():
                return []
            size = max(1, target)
            return [
                piece
                for start in range(0, len(text), size)
                if (piece := text[start : start + size]).strip()
            ]
        return [
            piece
            for start in range(0, len(words), target)
            if (piece := "".join(words[start : start + target])).strip()
        ]

    def _greedy_merge(self, pieces: list[str]) -> list[str]:
        if not pieces:
            return []
        result: list[str] = []
        current = pieces[0]
        for piece in pieces[1:]:
            combined = current + piece
            if self._count_words(combined) <= math.ceil(self.target_words * 1.5):
                current = combined
            else:
                result.append(current)
                current = piece
        if current.strip():
            result.append(current)
        return result

    def _apply_overlap(self, chunks: list[str]) -> list[str]:
        if len(chunks) <= 1 or self.overlap_words <= 0:
            return chunks
        return [
            chunks[0],
            *(self._trailing_context(prev) + chunk for prev, chunk in itertools.pairwise(chunks)),
        ]

    def _trailing_context(self, text: str) -> str:
        words = WORD_RUNS.findall(text)
        if len(words) <= self.overlap_words:
            return ""
        trailing = "".join(words[-self.overlap_words :])
        boundary = SENTENCE_BOUNDARY.search(trailing)
        if boundary is not None and boundary.start() < len(trailing) / 2:
            after = SENTENCE_BOUNDARY.sub("", trailing[boundary.start() :], count=1)
            if after.strip():
                return after
        return trailing

    @staticmethod
    def _digest(owner_kind: str, owner_id: str, subject: str, ordinal: int, text: str) -> str:
        payload = "\x00".join((owner_kind, owner_id, subject, str(ordinal), text))
        return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
