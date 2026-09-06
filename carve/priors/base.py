"""
Filesystem allocation priors -- pluggable and, critically, ABLATABLE.

Every paper in this field scores fragment pairs on their bytes alone and
ignores the physical medium. But filesystems do not fragment randomly:
allocators are trying hard to keep a file contiguous and mostly succeed, so
the successor of cluster c is overwhelmingly c+1, and when it is not, the
gap is usually small and usually forward.

The score combines as

    score(a -> b) = validator_score(a, b) + log P(gap = b - a | filesystem)

UniformPrior exists so the with-prior/without-prior ablation is runnable
from night one. An ablation you cannot run is an ablation you will fake.

Phase 2 replaces LocalityPrior's hand-set numbers with gap distributions
MEASURED from real aged ext4/NTFS volumes. The hand-set version is honest
about being a placeholder: see `is_measured`.
"""

from __future__ import annotations

import math


class Prior:
    """Interface for allocation priors."""

    name = "base"
    is_measured = False          # True once fitted to real aged filesystems

    def log_gap_prob(self, gap: int) -> float:
        raise NotImplementedError

    def allows_greedy_run(self) -> bool:
        """
        May the search accept a clean gap=+1 continuation without exploring
        alternatives? This is the run-merging optimisation, and it IS the
        prior in its strongest form, so the uniform ablation must refuse it.
        """
        return False

    def rank_candidates(self, current: int, candidates) -> list:
        """Return [(cluster, log_prior)] ordered best-first."""
        scored = [(c, self.log_gap_prob(c - current)) for c in candidates]
        scored.sort(key=lambda t: -t[1])
        return scored


class UniformPrior(Prior):
    """No allocation knowledge at all. The ablation control."""

    name = "uniform"

    def log_gap_prob(self, gap: int) -> float:
        return 0.0

    def allows_greedy_run(self) -> bool:
        return False

    def rank_candidates(self, current: int, candidates) -> list:
        # index order, not distance order -- must not smuggle locality back in
        return [(c, 0.0) for c in sorted(candidates)]


class LocalityPrior(Prior):
    """
    Generic allocator locality: contiguous-first, then small forward gaps,
    then backward gaps.

    Numbers are hand-set placeholders chosen to be conservative, NOT fitted.
    They deliberately keep backward jumps affordable, because a prior that
    forbids them cannot solve hard.img -- and real allocators do produce
    backward runs (ext4 delayed allocation, wrapped log-structured writes).
    """

    name = "locality"

    def __init__(self, contiguous_bonus: float = 3.0, decay: float = 0.012,
                 backward_penalty: float = 1.2):
        self.contiguous_bonus = contiguous_bonus
        self.decay = decay
        self.backward_penalty = backward_penalty

    def allows_greedy_run(self) -> bool:
        return True

    def log_gap_prob(self, gap: int) -> float:
        if gap == 0:
            return -1e9                      # a cluster cannot follow itself
        if gap == 1:
            return self.contiguous_bonus
        d = abs(gap)
        lp = -self.decay * d - math.log(1.0 + d)
        if gap < 0:
            lp -= self.backward_penalty
        return lp


PRIORS = {
    "uniform": UniformPrior,
    "locality": LocalityPrior,
}


def get_prior(name: str) -> Prior:
    if name not in PRIORS:
        raise ValueError("unknown prior %r (have: %s)" % (name, ", ".join(PRIORS)))
    return PRIORS[name]()
