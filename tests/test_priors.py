"""
Allocation priors, and the integrity of the ablation.

The prior is worth 23.8x the runtime and the difference between recovering
hard.img and failing it, so the control has to stay a real control. The most
important test here is that UniformPrior does not smuggle locality back in
through candidate ordering -- an ablation that quietly cheats is worse than
no ablation, because it produces a number you will believe.
"""

from __future__ import annotations

import pytest

from carve.priors.base import LocalityPrior, UniformPrior, get_prior


def test_get_prior_returns_the_right_types():
    assert isinstance(get_prior("locality"), LocalityPrior)
    assert isinstance(get_prior("uniform"), UniformPrior)


def test_get_prior_rejects_unknown_names():
    with pytest.raises(ValueError):
        get_prior("wishful-thinking")


# --------------------------------------------------------------------------
# LocalityPrior
# --------------------------------------------------------------------------

def test_a_cluster_cannot_follow_itself():
    assert LocalityPrior().log_gap_prob(0) <= -1e8


def test_contiguous_is_the_best_gap():
    p = LocalityPrior()
    best = p.log_gap_prob(1)
    for gap in (2, 3, 10, 100, -1, -10, -100):
        assert best > p.log_gap_prob(gap), "gap=%d outranked contiguous" % gap


def test_probability_decays_with_distance():
    p = LocalityPrior()
    assert p.log_gap_prob(2) > p.log_gap_prob(20) > p.log_gap_prob(200)


def test_backward_jumps_are_penalised_but_not_forbidden():
    """
    hard.img is unsolvable if backward jumps are impossible, and real
    allocators do produce them (ext4 delayed allocation, wrapped
    log-structured writes). Penalised, never barred.
    """
    p = LocalityPrior()
    assert p.log_gap_prob(-10) < p.log_gap_prob(10)
    assert p.log_gap_prob(-10) > -1e8


def test_locality_allows_the_greedy_run_merge():
    assert LocalityPrior().allows_greedy_run() is True


# --------------------------------------------------------------------------
# UniformPrior -- the ablation control
# --------------------------------------------------------------------------

def test_uniform_is_genuinely_flat():
    p = UniformPrior()
    for gap in (1, 2, -1, 50, -50, 1000):
        assert p.log_gap_prob(gap) == 0.0


def test_uniform_refuses_the_greedy_run_merge():
    """Run-merging IS the prior in its strongest form, so the control must
    refuse it or the ablation measures nothing."""
    assert UniformPrior().allows_greedy_run() is False


def test_uniform_ranks_in_index_order_not_distance_order():
    """The control must not reintroduce locality through the back door."""
    candidates = [50, 12, 99, 13, 7]
    ranked = UniformPrior().rank_candidates(current=12, candidates=candidates)
    assert [c for c, _ in ranked] == sorted(candidates)
    assert all(lp == 0.0 for _, lp in ranked)


def test_locality_ranks_nearest_first():
    ranked = LocalityPrior().rank_candidates(current=12, candidates=[50, 13, 99, 20])
    assert ranked[0][0] == 13, "contiguous successor should rank first"
