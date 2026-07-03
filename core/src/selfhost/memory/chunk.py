"""Chunking and the index value objects: internal, backend-owned, never ORM rows.

`Chunk` carries the columns the index stores; the embedding is filled by the derivation job
(via EmbedClient) before upsert. `TextChunker.chunk` is the workflow — recursive-delimiter split
to ~target-word pieces with sentence-aware overlap, char-capped — its `_`-methods its steps in
execution order.
"""

import hashlib
import itertools
import math
import re
from dataclasses import dataclass

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
        if level >= len(DELIMITER_LEVELS) or not DELIMITER_LEVELS[level]:
            return self._split_on_whitespace(text)
        pieces = self._split_at_delimiters(text, DELIMITER_LEVELS[level])
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
    def _split_at_delimiters(text: str, delimiters: tuple[str, ...]) -> list[str]:
        pieces: list[str] = []
        remaining = text
        while remaining:
            cuts = [
                (index, delim) for delim in delimiters if (index := remaining.find(delim)) != -1
            ]
            if not cuts:
                pieces.append(remaining)
                break
            earliest, delim = min(cuts)
            pieces.append(remaining[: earliest + len(delim)])
            remaining = remaining[earliest + len(delim) :]
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
