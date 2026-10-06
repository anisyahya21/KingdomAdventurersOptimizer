"""Planning budgets for expected earned chests, separate from statistical certification.

The normal-approximation precision estimate determines useful follow-up work. It is not a
confidence guarantee for a repeatedly selected strategy, and never changes a battle outcome.
"""
import math

PRECISION_HALF_WIDTH = 0.5
MAX_MEASUREMENT_RUNS = 65536
MIN_MEASUREMENT_RUNS = 1024


def precision_budget(runs, variance, *, half_width=PRECISION_HALF_WIDTH,
                     minimum=MIN_MEASUREMENT_RUNS, maximum=MAX_MEASUREMENT_RUNS):
    """Return a bounded planning target and explicitly distinguish a cap from precision."""
    if not math.isfinite(half_width) or half_width <= 0:
        raise ValueError('Precision half-width must be finite and positive')
    if minimum < 2 or maximum < minimum:
        raise ValueError('Invalid measurement budget bounds')
    if variance is None or not math.isfinite(variance) or variance < 0:
        return dict(target=min(maximum, max(minimum, int(runs) * 2)),
                    approximateHalfWidth=None, precisionReached=False,
                    budgetExhausted=int(runs) >= maximum)
    needed = max(minimum, math.ceil(1.96 ** 2 * variance / half_width ** 2))
    target = min(maximum, needed)
    width = 1.96 * math.sqrt(variance / runs) if runs > 1 else None
    reached = runs >= minimum and width is not None and width <= half_width
    return dict(target=target, approximateHalfWidth=width, precisionReached=reached,
                budgetExhausted=runs >= maximum and not reached)


def variance_from_totals(count, total, total_squared):
    if count < 2 or total_squared is None:
        return None
    return max(0., (float(total_squared) - float(total) ** 2 / count) / (count - 1))
