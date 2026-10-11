"""Shrinkage toward a prior, the prior's strength fitted from the data, and
the odds-ratio rule that combines an offense with a defense.

Phase 4.3 and 4.4 of ``SIMULATION_PLAN.md``. Every rate the simulator
estimates is a posterior mean

    (successes + k * mu) / (trials + k)

with ``mu`` the league rate in the window and ``k`` the prior's strength in
pseudo-trials. ``k`` is not chosen by hand: :func:`fit_strength` splits each
qualifying player-season into odd and even games and picks the ``k`` whose
shrunk odd-game rate predicts the raw even-game rate best, weighted by the
even games' trials. Expect a large ``k`` for a three-point make rate (a
season of attempts says little) and a small one for a free-throw attempt
rate (a player's habit shows fast); that is regression to the mean applied
deliberately rather than by feel. A categorical mix shrinks the same way,
category by category, which is a Dirichlet prior with concentration
``k * mu`` (:func:`shrink_mix`, :func:`fit_mix_strength`).

An offense's rate ``o`` and a defense's rate ``d`` against a league rate
``l`` combine on the odds scale, ``odds(p) = odds(o) * odds(d) / odds(l)``
(:func:`combine`): a league-average defense leaves the offense's rate
alone, and the rule is symmetric in the two sides. Five defenders combine by
averaging their log-odds deviations from the league rate, weighted equally
(:func:`combine_defenders`) - a v1 choice, recorded as such.

Pure functions on floats and ints, deliberately: numpy is not a dependency
(decision D9), and nothing here is a hot loop.

.. versionadded:: 6.0.0
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence

EPSILON = 1e-9
"""The distance a probability is kept from 0 and 1 before taking its odds.

.. versionadded:: 6.0.0
"""

STRENGTH_RANGE: tuple[float, float] = (1e-3, 1e6)
"""The prior strengths :func:`fit_strength` searches over, in pseudo-trials.

.. versionadded:: 6.0.0
"""


def shrink(successes: float, trials: float, mu: float, k: float) -> float:
    """The posterior mean of a rate: ``(successes + k * mu) / (trials + k)``.

    With no trials it is ``mu``; as trials grow it approaches the raw rate.
    ``k`` must be non-negative, and ``trials + k`` positive.

    .. versionadded:: 6.0.0
    """
    if k < 0:
        raise ValueError(f"prior strength must be non-negative, got {k}")
    if trials + k <= 0:
        raise ValueError("a rate needs trials or a prior")
    return (successes + k * mu) / (trials + k)


def shrink_mix(counts: Mapping[str, float], mu: Mapping[str, float], k: float) -> dict[str, float]:
    """A categorical mix shrunk toward ``mu`` with a Dirichlet prior of
    concentration ``k * mu``: each category is ``(count + k * mu) /
    (total + k)``. Categories are ``mu``'s; a count for a category ``mu``
    lacks is an error, since the prior would give it no mass.

    .. versionadded:: 6.0.0
    """
    stray = set(counts) - set(mu)
    if stray:
        raise ValueError(f"counts for categories the prior lacks: {sorted(stray)}")
    total = sum(counts.values())
    return {category: shrink(counts.get(category, 0.0), total, mu[category], k) for category in mu}


def _golden_section(f: Callable[[float], float], lo: float, hi: float, steps: int = 60) -> float:
    """Minimize ``f`` over ``[lo, hi]`` on a log scale by golden-section
    search. ``f`` is unimodal enough on these losses; the coarse grid in the
    caller guards the rest."""
    a, b = math.log(lo), math.log(hi)
    phi = (math.sqrt(5) - 1) / 2
    c, d = b - phi * (b - a), a + phi * (b - a)
    fc, fd = f(math.exp(c)), f(math.exp(d))
    for _ in range(steps):
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - phi * (b - a)
            fc = f(math.exp(c))
        else:
            a, c, fc = c, d, fd
            d = a + phi * (b - a)
            fd = f(math.exp(d))
    return math.exp((a + b) / 2)


def _minimize_strength(f: Callable[[float], float]) -> float:
    """A coarse log grid over :data:`STRENGTH_RANGE`, then golden-section
    search in the best grid cell."""
    lo, hi = STRENGTH_RANGE
    grid = [lo * (hi / lo) ** (i / 36) for i in range(37)]
    best = min(range(len(grid)), key=lambda i: f(grid[i]))
    left = grid[max(best - 1, 0)]
    right = grid[min(best + 1, len(grid) - 1)]
    return _golden_section(f, left, right)


def fit_strength(splits: Iterable[tuple[float, float, float, float]], mu: float) -> float:
    """The prior strength ``k`` that best predicts held-out rates.

    Each split is ``(odd_successes, odd_trials, even_successes, even_trials)``
    for one player-season, its games split into odd and even; the loss is the
    squared error of the shrunk odd-game rate against the raw even-game rate,
    weighted by the even-game trials, summed over players. Returns a value in
    :data:`STRENGTH_RANGE`; with no usable splits (no even trials anywhere)
    the range's upper end, which is "trust the league".

    .. versionadded:: 6.0.0
    """
    rows = [(so, no, se / ne, ne) for so, no, se, ne in splits if ne > 0]
    if not rows:
        return STRENGTH_RANGE[1]

    def _loss(k: float) -> float:
        return sum(ne * (shrink(so, no, mu, k) - raw_even) ** 2 for so, no, raw_even, ne in rows)

    return _minimize_strength(_loss)


def fit_mix_strength(splits: Iterable[tuple[Mapping[str, float], Mapping[str, float]]], mu: Mapping[str, float]) -> float:
    """The Dirichlet strength that best predicts held-out mixes: each split is
    ``(odd_counts, even_counts)`` for one player-season, the loss the squared
    error of the shrunk odd mix against the raw even mix, summed over the
    categories and weighted by the even total.

    .. versionadded:: 6.0.0
    """
    rows = []
    for odd, even in splits:
        total = sum(even.values())
        if total > 0:
            rows.append((odd, {c: even.get(c, 0.0) / total for c in mu}, total))
    if not rows:
        return STRENGTH_RANGE[1]

    def _loss(k: float) -> float:
        return sum(total * sum((p - raw[c]) ** 2 for c, p in shrink_mix(odd, mu, k).items()) for odd, raw, total in rows)

    return _minimize_strength(_loss)


def clamp(p: float) -> float:
    """``p`` kept inside ``(EPSILON, 1 - EPSILON)`` so its odds are finite.

    .. versionadded:: 6.0.0
    """
    return min(max(p, EPSILON), 1 - EPSILON)


def logit(p: float) -> float:
    """The log-odds of ``p``.

    .. versionadded:: 6.0.0
    """
    p = clamp(p)
    return math.log(p / (1 - p))


def expit(x: float) -> float:
    """The probability whose log-odds are ``x``.

    .. versionadded:: 6.0.0
    """
    return 1 / (1 + math.exp(-x))


def combine(offense: float, defense: float, league: float) -> float:
    """``odds(p) = odds(o) * odds(d) / odds(l)``: the probability when an
    offense with rate ``o`` meets a defense that allows rate ``d`` against a
    league whose rate is ``l``. Symmetric in ``o`` and ``d``; ``o`` exactly
    when ``d == l``.

    .. versionadded:: 6.0.0
    """
    return expit(logit(offense) + logit(defense) - logit(league))


def combine_defenders(allowed: Sequence[float], league: float) -> float:
    """The rate five defenders allow together: the league rate moved by the
    mean of their log-odds deviations from it. An empty sequence is the
    league rate.

    .. versionadded:: 6.0.0
    """
    if not allowed:
        return league
    base = logit(league)
    return expit(base + sum(logit(d) - base for d in allowed) / len(allowed))
