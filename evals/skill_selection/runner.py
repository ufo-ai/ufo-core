"""Offline selector comparison — the offline half of the RFC 0038 promote gate. Each labeled
query is scored against the runtime's own lexical selection (`select_top_k` — exactly what the
member block renders and the shadow log records) and against fused lexical + vector, with no
agent, sandbox, or turn: recall@k over the fixture corpora is the whole verdict, at case counts
and corpus sizes a live-agent eval cannot afford. The vector leg embeds through the deploy's
configured embed backend, so the run needs the eval environment's model credentials loaded and
fails loud without them.

`python -m evals --only skill_selection --workspace <disposable-workspace-id>`"""

from __future__ import annotations

import asyncio
import math
import os
from dataclasses import dataclass

from cryptography.fernet import Fernet

from evals.harness.harness import EvalCaseResult, EvalMetric, EvalReport, digest_payload
from evals.harness.registry import EvalTask
from evals.harness.target import CapabilityTarget
from evals.skill_loading.runner import fixtures_digest
from evals.skill_selection.queries import CORPORA, QUERIES
from ufo.config import load_config
from ufo.credentials import CredentialStore
from ufo.ext.loader import embed_backend, load_manifests
from ufo.indexing import EmbedClient
from ufo.skills.runtime import SkillCard
from ufo.skills.selection import SKILL_TOP_K, lexical_score, select_top_k

EMBED_BATCH = 128
RRF_K = 60
SELECTORS = ("lexical", "fused")


def skill_selection_task() -> EvalTask:
    digest = digest_payload(
        {
            "runner": "skill-selection",
            "task": "skill_selection",
            "k": SKILL_TOP_K,
            "corpora": {name: fixtures_digest(corpus) for name, corpus in CORPORA.items()},
            "queries": [[query.name, query.query, query.expected] for query in QUERIES],
        }
    )
    suite = SkillSelectionSuite(digest=digest)
    names = tuple(
        f"{corpus}:{selector}:{query.name}"
        for corpus in CORPORA
        for selector in SELECTORS
        for query in QUERIES
    )
    return EvalTask("skill_selection", "skill_selection", digest, names, suite.run)


@dataclass(frozen=True)
class SkillSelectionSuite:
    digest: str

    async def run(self, target: CapabilityTarget, slots: asyncio.Semaphore) -> EvalReport:
        embed = self._embed_client()
        cases: list[EvalCaseResult] = []
        metrics: list[EvalMetric] = []
        for corpus_name, corpus in CORPORA.items():
            cards = tuple(
                SkillCard(
                    name=fixture.name,
                    description=fixture.description,
                    depends=fixture.depends,
                    pinned=fixture.pinned,
                )
                for fixture in corpus
            )
            if any(card.pinned for card in cards):
                raise RuntimeError(
                    f"selection corpus {corpus_name!r} carries a pinned card; select_top_k ranks "
                    "unpinned cards only, so a pinned expected skill would grade as a silent miss"
                )
            names = tuple(card.name for card in cards)
            corpus_vectors = await self._embed_batched(
                embed, tuple(f"{card.name}: {card.description}" for card in cards)
            )
            query_vectors = await self._embed_batched(
                embed, tuple(query.query for query in QUERIES)
            )
            hits = dict.fromkeys(SELECTORS, 0)
            for query, query_vector in zip(QUERIES, query_vectors, strict=True):
                lexical_top, lexical_full, lexical_hits = await asyncio.to_thread(
                    self._lexical_ranking, query.query, cards
                )
                vector = await asyncio.to_thread(
                    self._vector_ranking, names, corpus_vectors, query_vector
                )
                fused = self._fused_ranking(lexical_hits, vector)
                rankings = {
                    "lexical": (lexical_top, lexical_full),
                    "fused": (fused[:SKILL_TOP_K], fused),
                }
                for selector in SELECTORS:
                    top, ranking = rankings[selector]
                    hit = query.expected in top
                    hits[selector] += hit
                    rank = ranking.index(query.expected) + 1 if query.expected in ranking else None
                    placed = (
                        f"{query.expected!r} ranked {rank} of {len(cards)}"
                        if rank is not None
                        else f"{query.expected!r} unranked"
                    )
                    cases.append(
                        EvalCaseResult(
                            name=f"{corpus_name}:{selector}:{query.name}",
                            passed=hit,
                            reason=f"{placed}; top-{SKILL_TOP_K} {'hit' if hit else 'miss'}",
                            evidence={
                                "corpus": corpus_name,
                                "selector": selector,
                                "query": query.query,
                                "expected": query.expected,
                                "rank": rank,
                                "topK": list(top),
                            },
                        )
                    )
            for selector in SELECTORS:
                metrics.append(
                    EvalMetric(
                        name=f"{corpus_name}_{selector}_recall_at_{SKILL_TOP_K}",
                        value=hits[selector] / len(QUERIES),
                    )
                )
        return EvalReport(
            name="skill_selection",
            suite="skill_selection",
            digest=self.digest,
            cases=tuple(cases),
            metrics=tuple(metrics),
        )

    def _embed_client(self) -> EmbedClient:
        config = load_config()
        key = os.environ.get(config.credentials.key_env)
        credentials = CredentialStore(fernet=Fernet(key.encode())) if key else None
        return embed_backend(
            load_manifests(config.pack.name), config.memory.embed_backend, credentials
        )

    async def _embed_batched(
        self, embed: EmbedClient, texts: tuple[str, ...]
    ) -> tuple[tuple[float, ...], ...]:
        vectors: list[tuple[float, ...]] = []
        for start in range(0, len(texts), EMBED_BATCH):
            vectors.extend(await embed.embed(texts[start : start + EMBED_BATCH]))
        return tuple(vectors)

    def _lexical_ranking(
        self, query: str, cards: tuple[SkillCard, ...]
    ) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        """The runtime's own selection plus its ordering: `top` is `select_top_k` verbatim —
        recall@k against exactly what the block renders and the shadow log records — `full` the
        stable score-descending order it prefixes (evidence ranks), and `hits` the scoring cards
        only, the lexical leg the fusion consumes (memory's zero-drop rule)."""
        top = tuple(card.name for card in select_top_k(query, cards))
        scored = sorted(
            ((lexical_score(query, card), card.name) for card in cards),
            key=lambda item: -item[0],
        )
        full = tuple(name for _, name in scored)
        hits = tuple(name for score, name in scored if score > 0)
        return top, full, hits

    def _vector_ranking(
        self,
        names: tuple[str, ...],
        corpus_vectors: tuple[tuple[float, ...], ...],
        query_vector: tuple[float, ...],
    ) -> tuple[str, ...]:
        scored = zip(
            (self._cosine(query_vector, vector) for vector in corpus_vectors),
            names,
            strict=True,
        )
        ranked = sorted(scored, key=lambda item: (-item[0], item[1]))
        return tuple(name for _, name in ranked)

    def _cosine(self, a: tuple[float, ...], b: tuple[float, ...]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
        return 0.0 if norm == 0 else dot / norm

    def _fused_ranking(self, lexical: tuple[str, ...], vector: tuple[str, ...]) -> tuple[str, ...]:
        scores: dict[str, float] = {}
        for leg in (lexical, vector):
            for rank, name in enumerate(leg, start=1):
                scores[name] = scores.get(name, 0.0) + 1.0 / (RRF_K + rank)
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return tuple(name for name, _ in ranked)
