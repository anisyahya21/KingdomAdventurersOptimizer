"""A bounded diverse parent pool for expected earned chests, alongside mechanism discovery.

The variance penalty is a search heuristic, not a post-selection confidence guarantee.
Independent confirmation remains required. Existing structural regions are diversity proxies,
not proof that two builds implement distinct combat mechanisms.
"""
import math
from strategy_sampling import variance_from_totals

MEAN_PARENT_LIMIT = 4
MINIMUM_VALIDATION = 64


def mean_parent_pool(entries, limit=MEAN_PARENT_LIMIT):
    """Best measured representative per structural region, at most `limit` per encounter.

    Entries carry candidate, encounterId, defeatCount, region and validation aggregate `total`.
    Incomplete/censored evidence stays eligible for other discovery paths, never a fabricated mean.
    """
    by_family = {}
    order = lambda row: (-row['score'], -row['mean'], -row['samples'], row['candidate'])
    for entry in entries:
        total = entry.get('total') or {}
        n = int(total.get('n') or 0)
        if (n < MINIMUM_VALIDATION or int(total.get('censored') or 0)
                or int(total.get('count') or 0) != n
                or int(total.get('chestCount') or 0) != n
                or total.get('chestSum') is None or total.get('chestSum2') is None):
            continue
        summed, squared = float(total['chestSum']), float(total['chestSum2'])
        if (not math.isfinite(summed) or not math.isfinite(squared) or squared < 0
                or squared+1e-9*max(1., squared) < summed*summed/n):
            continue
        mean = summed / n
        variance = variance_from_totals(n, total['chestSum'], total['chestSum2'])
        if variance is None or not math.isfinite(mean) or not math.isfinite(variance) or mean <= 0:
            continue
        penalty = 1.96 * math.sqrt(variance / n)
        candidate = dict(candidate=entry['candidate'], encounterId=int(entry['encounterId']),
                         defeatCount=int(entry.get('defeatCount') or 0),
                         region=entry.get('region') or entry['candidate'],
                         mean=mean, samples=n, score=mean-penalty, penalty=penalty)
        key = (candidate['encounterId'], candidate['defeatCount'], candidate['region'])
        previous = by_family.get(key)
        if previous is None or order(candidate) < order(previous):
            by_family[key] = candidate
    selected, counts = [], {}
    for entry in sorted(by_family.values(), key=order):
        encounter = entry['encounterId']
        if counts.get(encounter, 0) >= limit:
            continue
        selected.append(entry)
        counts[encounter] = counts.get(encounter, 0)+1
    return selected
