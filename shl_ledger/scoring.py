"""The arithmetic of a parked assumption's confidence. Pure functions, no I/O.

Support: a Bayesian odds update. With likelihood ratio L (how much more likely the
person is to say this if the assumption is true than if it is false):

    odds' = odds * L        p' = p*L / (p*L + 1 - p)

so each supporting mention counts for less as confidence rises.

The rejection itself: the person's "no" is the strongest evidence there is, so it is
applied first, as an odds update with a weight below 1 (rejection_weight):

    p_after_no = p*w / (p*w + 1 - p)

Contrary evidence: a fixed step down, as in the author's specification:

    p' = max(0, p - decay)

L and decay are settings, not fitted values. The update rule is Bayesian; the
numbers fed into it are uncalibrated until measured.
"""
from __future__ import annotations

CAP = 0.99


def support(p: float, likelihood_ratio: float = 2.0) -> float:
    p = min(CAP, max(0.0, float(p)))
    L = max(1.0, float(likelihood_ratio))
    q = p * L / (p * L + (1.0 - p)) if p > 0 else 0.0
    return min(CAP, q)


def weigh_rejection(p: float, weight: float = 0.25) -> float:
    """The person's "no" as evidence: odds' = odds * weight (weight <= 1)."""
    p = min(CAP, max(0.0, float(p)))
    w = min(1.0, max(0.0, float(weight)))
    return p * w / (p * w + (1.0 - p)) if p > 0 else 0.0


def contradict(p: float, decay: float = 0.15) -> float:
    return max(0.0, float(p) - max(0.0, float(decay)))


def evidence_due(support_count: int, p: float, need: int = 3, threshold: float = 0.65) -> bool:
    """Enough supporting mentions, and confidence back above the threshold."""
    return int(support_count) >= int(need) and float(p) >= float(threshold)


def trajectory(start: float, directions, likelihood_ratio: float = 2.0, decay: float = 0.15) -> list[float]:
    """Confidence after each piece of evidence. For docs, tests and simulations."""
    out, p = [], float(start)
    for d in directions:
        p = support(p, likelihood_ratio) if d == "for" else contradict(p, decay)
        out.append(round(p, 4))
    return out
