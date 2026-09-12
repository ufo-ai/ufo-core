"""Reciprocal-rank fusion: how this extension combines every ranked list it holds.

Two legs of one index rank by different arithmetic, and a web backend's rank shares no scale with
either, so nothing here compares scores. Each list contributes `1 / (RRF_K + rank)` to the key it
holds at that rank — the fusion Cormack, Clarke and Buettcher measured as beating the individual
systems it combines, and the learned combinations they compared it against, with no tuning and no
shared score scale (`Reciprocal Rank Fusion Outperforms Condorcet and Individual Rank Learning
Methods`, SIGIR 2009). `RRF_K = 60` is the constant they report and the one every later system
adopts; it damps the head so one leg's top hit cannot carry a document the other leg never ranked.

Both users fuse over a key they choose: the chunk digest inside the page store, the passage
identity across the legs of a whole prefetch."""

from collections.abc import Sequence

RRF_K = 60


def fused_scores(legs: Sequence[Sequence[str]]) -> dict[str, float]:
    """Every key's fused score across the ranked lists that hold it, best-ranked first in each."""
    scores: dict[str, float] = {}
    for leg in legs:
        for rank, key in enumerate(leg):
            scores[key] = scores.get(key, 0.0) + 1 / (RRF_K + rank + 1)
    return scores
