"""Shrinkage, the fitted prior strength, and the odds-ratio combination."""

import math
import random

import pytest

from association.sim import shrink


def test_no_trials_gives_the_prior_and_many_trials_the_raw_rate() -> None:
    assert shrink.shrink(0, 0, 0.36, 100) == 0.36
    assert shrink.shrink(400_000, 1_000_000, 0.36, 100) == pytest.approx(0.4, abs=1e-4)
    # Halfway: as many trials as pseudo-trials puts the estimate midway.
    assert shrink.shrink(50, 100, 0.3, 100) == pytest.approx(0.4)


def test_shrink_refuses_a_negative_strength_and_no_evidence_at_all() -> None:
    with pytest.raises(ValueError):
        shrink.shrink(1, 2, 0.5, -1)
    with pytest.raises(ValueError):
        shrink.shrink(0, 0, 0.5, 0)


def test_a_mix_shrinks_category_by_category_and_sums_to_one() -> None:
    mu = {"a": 0.5, "b": 0.3, "c": 0.2}
    mixed = shrink.shrink_mix({"a": 10, "b": 0}, mu, 10)
    assert mixed == pytest.approx({"a": 0.75, "b": 0.15, "c": 0.10})
    assert sum(mixed.values()) == pytest.approx(1.0)
    assert shrink.shrink_mix({}, mu, 5) == pytest.approx(mu)
    with pytest.raises(ValueError, match="categories the prior lacks"):
        shrink.shrink_mix({"zzz": 1}, mu, 5)


def _synthetic_splits(rng: random.Random, a: float, b: float, players: int, trials: int) -> list[tuple[float, float, float, float]]:
    """Players whose true rates are Beta(a, b): the Bayes-optimal strength is a + b."""
    rows = []
    for _ in range(players):
        p = rng.betavariate(a, b)
        odd = sum(rng.random() < p for _ in range(trials))
        even = sum(rng.random() < p for _ in range(trials))
        rows.append((float(odd), float(trials), float(even), float(trials)))
    return rows


def test_the_fitter_recovers_a_known_strength() -> None:
    rng = random.Random(7)
    a, b = 60.0, 100.0  # mean 0.375, strength 160
    k = shrink.fit_strength(_synthetic_splits(rng, a, b, players=600, trials=150), mu=a / (a + b))
    assert 120 < k < 210, k
    # A wide spread of true rates (a weak prior) fits a small strength.
    k_weak = shrink.fit_strength(_synthetic_splits(rng, 3.0, 5.0, players=600, trials=150), mu=3 / 8)
    assert k_weak < 20, k_weak


def test_the_fitter_with_nothing_to_fit_trusts_the_league() -> None:
    assert shrink.fit_strength([], mu=0.5) == shrink.STRENGTH_RANGE[1]
    assert shrink.fit_strength([(3, 10, 0, 0)], mu=0.5) == shrink.STRENGTH_RANGE[1]
    assert shrink.fit_mix_strength([], mu={"a": 1.0}) == shrink.STRENGTH_RANGE[1]


def test_the_mix_fitter_recovers_a_known_strength() -> None:
    rng = random.Random(11)
    alpha = {"restricted": 40.0, "midrange": 30.0, "three": 50.0}  # Dirichlet strength 120
    total = sum(alpha.values())
    mu = {c: v / total for c, v in alpha.items()}
    splits = []
    for _ in range(500):
        gammas = {c: rng.gammavariate(v, 1.0) for c, v in alpha.items()}
        s = sum(gammas.values())
        probs = {c: g / s for c, g in gammas.items()}
        halves = []
        for _half in range(2):
            counts = dict.fromkeys(alpha, 0.0)
            for _ in range(120):
                r, acc = rng.random(), 0.0
                for c in alpha:
                    acc += probs[c]
                    if r < acc:
                        counts[c] += 1
                        break
            halves.append(counts)
        splits.append((halves[0], halves[1]))
    k = shrink.fit_mix_strength(splits, mu)
    assert 80 < k < 180, k


def test_the_odds_combination_is_symmetric_and_neutral_at_the_league_rate() -> None:
    assert shrink.combine(0.42, 0.36, 0.36) == pytest.approx(0.42)
    assert shrink.combine(0.42, 0.30, 0.36) == pytest.approx(shrink.combine(0.30, 0.42, 0.36))
    # A better-than-league defense lowers the rate; a worse one raises it.
    assert shrink.combine(0.42, 0.30, 0.36) < 0.42 < shrink.combine(0.42, 0.40, 0.36)
    # The odds really multiply.
    p = shrink.combine(0.5, 0.25, 0.5)
    assert p / (1 - p) == pytest.approx((0.5 / 0.5) * (0.25 / 0.75) / (0.5 / 0.5))


def test_defenders_combine_by_mean_log_odds_and_an_empty_floor_is_the_league() -> None:
    assert shrink.combine_defenders([], 0.36) == 0.36
    assert shrink.combine_defenders([0.36] * 5, 0.36) == pytest.approx(0.36)
    one_good = shrink.combine_defenders([0.30, 0.36, 0.36, 0.36, 0.36], 0.36)
    assert 0.30 < one_good < 0.36
    # Deviations average on the log-odds scale, not the probability scale.
    expected = shrink.expit(shrink.logit(0.36) + (shrink.logit(0.30) - shrink.logit(0.36)) / 5)
    assert one_good == pytest.approx(expected)


def test_logit_and_expit_invert_and_the_clamp_keeps_odds_finite() -> None:
    for p in (0.01, 0.5, 0.99):
        assert shrink.expit(shrink.logit(p)) == pytest.approx(p)
    assert math.isfinite(shrink.logit(0.0)) and math.isfinite(shrink.logit(1.0))
    assert shrink.clamp(-3.0) == shrink.EPSILON and shrink.clamp(2.0) == 1 - shrink.EPSILON


def test_combine_defenders_takes_deviations_from_the_measured_mean_and_anchors_on_the_league() -> None:
    from association.sim.shrink import combine_defenders, expit, logit

    # Five defenders exactly at the measured mean leave the league rate alone, whatever the unit gap.
    assert combine_defenders([0.142] * 5, 0.130, measured=0.142) == pytest.approx(0.130)
    # One deviation of +0.2 log-odds among five moves the anchor by a fifth of it.
    one = expit(logit(0.142) + 0.2)
    assert combine_defenders([one, 0.142, 0.142, 0.142, 0.142], 0.130, measured=0.142) == pytest.approx(expit(logit(0.130) + 0.04))
    # Without a measured mean the league rate is the base, as before.
    assert combine_defenders([0.142] * 5, 0.130) == pytest.approx(0.142)
