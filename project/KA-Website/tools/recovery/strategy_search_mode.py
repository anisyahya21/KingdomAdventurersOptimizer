"""Search-mode configuration (version 1) and its explicit, pure migration.

Two modes, one versioned config:

  * `community-first` - only the Community stream is eligible. There is no discovery floor and no
    absolute reservation escape: the caller may not fall back to `DISCOVERY_FLOOR` or
    `COMMUNITY_FLOOR_RUNS_PER_ENCOUNTER` to keep another lineage alive.
  * `all-strategy` - every registered share stream keeps its allocation, except the retiring
    `average` (Student 9) allocation which moves to the shared refinement owner.

Registered names are READ from `strategy_students` (`SHARE_STREAMS`, `STUDENT_NAMES`, `DEFAULT_SHARES`);
this module never invents a stream id. Student 9's actual identity is the `average` stream, verified
from the module and the orientation doc, not re-declared here.

`community-first` must hold a POSITIVE community allocation and an EXACT zero everywhere else. In every
mode the retired `average` stream must be exactly zero and `freewill` can never be allocated. Purpose
budgets are whole runs of work: an explicit `0` disables a purpose and at least one purpose must stay
positive; a fractional run budget is refused, never rounded. The small equal default per purpose is
deliberate (a fresh config spends very little until the caller raises it).

Migration is a PURE function of the old allocations and the target mode. It preserves the allocation
total exactly and emits a per-stream ledger plus a rollback snapshot of the old allocations. It is
never applied automatically to an existing session: the caller must validate the planned config and
persist it deliberately.
"""
from __future__ import annotations

import math

CONFIG_VERSION = 1
MODES = ('community-first', 'all-strategy')

#: The shared refinement owner that absorbs a retired/folded allocation.
REFINEMENT_OWNER = 'community'
#: Student 9's stream, retired by this redesign.
RETIRED_STREAM = 'average'
#: Deliberately undefined and unallocated; never eligible.
FREE_WILL = 'freewill'

#: Shared purposes with finite budgets.
PURPOSES = ('improvement', 'boundary', 'support', 'comparison', 'exploration')

#: The session-wide owner key used by a single shared (owner, purpose) budget row.
ANY_OWNER = '*'

_STREAM_CACHE = None


def share_streams():
    """The registered share-stream names, read from `strategy_students` (never invented)."""
    global _STREAM_CACHE
    if _STREAM_CACHE is None:
        import strategy_students
        _STREAM_CACHE = tuple(strategy_students.SHARE_STREAMS)
    return _STREAM_CACHE


def default_shares():
    import strategy_students
    return dict(strategy_students.DEFAULT_SHARES)


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validated_allocations(allocations, mode=None, legacy=False):
    """Validate stream allocations for `mode`.

    `legacy=True` is used only when reading an OLD allocation for `plan_migration`: it tolerates the
    retiring `average` stream that the plan will fold away, but still refuses `freewill`, unknown
    names, negatives and a non-positive total. For a persisted config (`legacy=False`):

      * `community-first` requires `community` strictly positive AND every other stream EXACTLY zero -
        a non-community share may not be smuggled into community-first.
      * `all-strategy` requires at least one positive stream, and the retired `average` stream must be
        exactly zero: it is never eligible in any mode.
    """
    if not isinstance(allocations, dict):
        raise ValueError('allocations must be a mapping of stream -> fraction')
    known = set(share_streams())
    if mode is not None and mode not in MODES:
        raise ValueError('unknown mode %r; expected one of %s' % (mode, MODES))
    cleaned = {name: 0.0 for name in share_streams()}
    for name, value in allocations.items():
        if name == FREE_WILL:
            raise ValueError('freewill is inactive and cannot be allocated')
        if name not in known:
            raise ValueError('unknown stream %r; expected one of %s' % (name, share_streams()))
        if not _finite(value):
            raise ValueError('%s allocation must be a finite number, not %r' % (name, value))
        if value < 0:
            raise ValueError('%s allocation must not be negative, got %r' % (name, value))
        cleaned[name] = float(value)
    if sum(cleaned.values()) <= 0:
        raise ValueError('at least one stream must hold a positive allocation')
    if legacy:
        return cleaned
    if mode == 'community-first':
        if cleaned.get(REFINEMENT_OWNER, 0.0) <= 0:
            raise ValueError('community-first requires a positive community allocation')
        offenders = {name: value for name, value in cleaned.items()
                     if name != REFINEMENT_OWNER and value != 0.0}
        if offenders:
            raise ValueError('community-first forbids any non-community allocation, got %r' % offenders)
    else:
        if cleaned.get(RETIRED_STREAM, 0.0) != 0.0:
            raise ValueError('the retired %s stream must be exactly zero in every mode, got %r'
                             % (RETIRED_STREAM, cleaned.get(RETIRED_STREAM)))
    return cleaned


def _validated_purposes(purposes):
    """Validate shared-purpose budgets.

    An explicit `0` disables a purpose (recorded, not run). A positive budget must be a whole number
    of runs - a fractional run budget is refused, never rounded. At least one purpose must stay
    positive so the scheduler always has something finite to spend.
    """
    if not isinstance(purposes, dict):
        raise ValueError('purposes must be a mapping of purpose -> budget')
    cleaned = {}
    for name, value in purposes.items():
        if name not in PURPOSES:
            raise ValueError('unknown purpose %r; expected one of %s' % (name, PURPOSES))
        if isinstance(value, bool) or not _finite(value):
            raise ValueError('%s budget must be a finite number, not %r' % (name, value))
        if value < 0:
            raise ValueError('%s budget must not be negative (use 0 to disable), got %r'
                             % (name, value))
        if value > 0 and float(value) != int(value):
            raise ValueError('%s budget must be a whole number of runs, not %r' % (name, value))
        cleaned[name] = int(value)
    if not any(value > 0 for value in cleaned.values()):
        raise ValueError('at least one purpose budget must stay positive')
    return cleaned


def default_config(mode='community-first'):
    """A fresh version-1 config for `mode` (equal purpose budgets; community-only by default)."""
    if mode not in MODES:
        raise ValueError('unknown mode %r; expected one of %s' % (mode, MODES))
    if mode == 'community-first':
        allocations = {name: 0.0 for name in share_streams()}
        allocations[REFINEMENT_OWNER] = 1.0
    else:
        allocations = default_shares()
        if allocations.get(RETIRED_STREAM):
            allocations[REFINEMENT_OWNER] = (allocations.get(REFINEMENT_OWNER, 0.0)
                                             + allocations.pop(RETIRED_STREAM))
    return dict(version=CONFIG_VERSION, mode=mode, allocations=allocations,
                purposes={name: 1 for name in PURPOSES})


def _validated_budget_matrix(config, allocations, purposes):
    """Validate the optional full (owner, purpose) budget matrix.

    Every owner must be a registered stream (or the session-wide `*`); a disabled owner (zero or
    absent allocation) must own a zero budget, and the retired `average`/`freewill` owners are never
    fundable. When the matrix is present, each purpose column must sum to the declared purpose budget
    so there is ONE canonical source and no double spend. Returns the per-purpose column totals.
    """
    budgets = config.get('budgets')
    if budgets is None:
        return None
    if not isinstance(budgets, dict):
        raise ValueError('budgets must be a mapping of owner -> {purpose: runs}')
    known = set(share_streams())
    totals = {purpose: 0 for purpose in PURPOSES}
    for owner, mapping in budgets.items():
        owner = str(owner)
        if owner not in known and owner != ANY_OWNER:
            raise ValueError('unknown owner %r in budgets; expected a registered stream or %r'
                             % (owner, ANY_OWNER))
        if owner == FREE_WILL:
            raise ValueError('freewill is inactive and cannot own a budget')
        if owner == RETIRED_STREAM:
            raise ValueError('the retired %s stream cannot own a budget' % RETIRED_STREAM)
        if not isinstance(mapping, dict):
            raise ValueError('budgets[%r] must be a purpose -> runs mapping' % (owner,))
        enabled = owner == ANY_OWNER or float(allocations.get(owner, 0.0)) > 0
        for purpose, total in mapping.items():
            if purpose not in PURPOSES:
                raise ValueError('unknown purpose %r in budgets' % (purpose,))
            if isinstance(total, bool) or not isinstance(total, int) or total < 0:
                raise ValueError('budget %s/%s must be a non-negative integer, got %r'
                                 % (owner, purpose, total))
            if not enabled and total != 0:
                raise ValueError('disabled owner %r must own a zero budget, got %s/%s=%r'
                                 % (owner, owner, purpose, total))
            totals[purpose] += int(total)
    for purpose, declared in purposes.items():
        if int(declared) != totals.get(purpose, 0):
            raise ValueError('purpose %r declared %r but the owner budget matrix totals %r; keep one '
                             'canonical source so budget is never double spent'
                             % (purpose, declared, totals.get(purpose, 0)))
    return totals


def validate_config(config):
    """Raise ValueError for any config the modes forbid.

    Beyond the version/mode/name checks this enforces the redesign's hard rules: community-first has a
    positive community and an exact zero everywhere else; all-strategy never allocates the retired
    `average`; `freewill` is never allocatable; purpose budgets are whole runs (0 disables a purpose)
    and at least one stays positive.
    """
    if not isinstance(config, dict):
        raise ValueError('config must be a mapping')
    if config.get('version') != CONFIG_VERSION:
        raise ValueError('config version must be %d, got %r' % (CONFIG_VERSION, config.get('version')))
    mode = config.get('mode')
    if mode not in MODES:
        raise ValueError('unknown mode %r; expected one of %s' % (mode, MODES))
    allocations = _validated_allocations(config.get('allocations') or {}, mode=mode)
    purposes = _validated_purposes(config.get('purposes') or {})
    _validated_budget_matrix(config, allocations, purposes)
    return True


def eligible_streams(config):
    """The streams allowed to spend budget under `config`.

    Community mode returns exactly `(community,)`; it never consults a discovery floor or an
    absolute reservation to admit another lineage. A stream with a zero (or absent) allocation is not
    eligible in any mode, and the retired `average` stream is never eligible.
    """
    validate_config(config)
    if config['mode'] == 'community-first':
        return (REFINEMENT_OWNER,)
    allocations = config['allocations']
    return tuple(name for name in share_streams()
                 if name != RETIRED_STREAM and float(allocations.get(name, 0.0)) > 0)


def mode_policy(config):
    """The explicit mode semantics: eligibility, no-floor/pos-reserve flags, freewill state."""
    validate_config(config)
    community_first = config['mode'] == 'community-first'
    return dict(mode=config['mode'],
                eligibleStreams=eligible_streams(config),
                discoveryFloor=0.0 if community_first else None,
                absoluteReservation=None if community_first else None,
                discoveryFloorEscape=not community_first,
                freewillActive=False)


def purpose_budgets(config):
    """The finite shared-purpose budgets, validated."""
    validate_config(config)
    return dict(config['purposes'])


def snapshot(config):
    """A deep, JSON-ready rollback snapshot of `config` (mode + allocations + purposes + budgets)."""
    validate_config(config)
    result = dict(version=CONFIG_VERSION, mode=config['mode'],
                  allocations=dict(config['allocations']), purposes=dict(config['purposes']))
    if config.get('budgets') is not None:
        result['budgets'] = {owner: dict(purposes) for owner, purposes in config['budgets'].items()}
    return result


def budget_owners(config):
    """The owners eligible to hold a budget under `config` (positive allocation, not retired)."""
    validate_config(config)
    if config['mode'] == 'community-first':
        return (REFINEMENT_OWNER,)
    allocations = config['allocations']
    return tuple(name for name in share_streams()
                 if name != RETIRED_STREAM and float(allocations.get(name, 0.0)) > 0)


def _apportion_pairs(total, owners, weights):
    """Deterministic largest-remainder apportionment of `total` units across `owners`.

    Returns a dict ``owner -> units`` of non-negative ints summing EXACTLY to `total`. The largest
    fractional remainder wins the leftover units; owner order breaks every tie, so the result is
    reproducible without any randomness.
    """
    units = {owner: 0 for owner in owners}
    if total <= 0 or not owners:
        return units
    weight_sum = sum(float(weights.get(owner, 0.0)) for owner in owners)
    if weight_sum <= 0:
        weights = {owner: 1.0 for owner in owners}
        weight_sum = float(len(owners))
    index = {owner: position for position, owner in enumerate(owners)}
    exact = {owner: total * float(weights.get(owner, 0.0)) / weight_sum for owner in owners}
    used = 0
    for owner in owners:
        units[owner] = int(math.floor(exact[owner]))
        used += units[owner]
    leftover = total - used
    ranked = sorted(owners, key=lambda owner: (-(exact[owner] - math.floor(exact[owner])), index[owner]))
    for owner in ranked[:leftover]:
        units[owner] += 1
    return units


def derive_budget_matrix(config):
    """Deterministic per-owner integer budgets for `config` (pure; never writes).

    An explicit `budgets` matrix wins (returned normalised). Otherwise each declared purpose total is
    apportioned across the eligible owners in proportion to their allocation, in whole two-battle
    PAIRS wherever possible: the pair budget is apportioned first and doubled, so an owner's budget is
    even unless it carries the single unspendable battle of an odd purpose total. That one unit is
    still assigned (the declared total is preserved EXACTLY) but is named in ``remainders`` so the
    scheduler leaves it unspent rather than pair it against filler. Zero owners are ALWAYS zero: the
    eligible set is exactly the positive, non-retired allocations, so no reservation or fallback can
    fund a stream the caller never allocated.
    """
    validate_config(config)
    allocations = config['allocations']
    purposes = config['purposes']
    owners = budget_owners(config)
    registered = [name for name in share_streams() if name not in (RETIRED_STREAM, FREE_WILL)]
    remainders = []
    if config.get('budgets'):
        source = 'explicit'
        explicit = config['budgets']
        matrix = {owner: {purpose: int(explicit.get(owner, {}).get(purpose, 0))
                          for purpose in PURPOSES} for owner in owners}
        for owner in owners:
            for purpose in PURPOSES:
                total = matrix[owner][purpose]
                if total > 0 and total % 2:
                    remainders.append(dict(owner=owner, purpose=purpose, units=1,
                                           reason='odd owner budget cannot buy a complete two-battle '
                                                  'pair; this unit stays unspendable'))
    else:
        source = 'derived'
        matrix = {owner: {purpose: 0 for purpose in PURPOSES} for owner in owners}
        for purpose in PURPOSES:
            total = int(purposes.get(purpose, 0))
            if total <= 0:
                continue
            weights = {owner: float(allocations.get(owner, 0.0)) for owner in owners}
            pair_units = _apportion_pairs(total // 2, owners, weights)
            for owner in owners:
                matrix[owner][purpose] = pair_units[owner] * 2
            if total % 2:
                carrier = max(owners, key=lambda owner: (float(allocations.get(owner, 0.0)),
                                                         -owners.index(owner)))
                matrix[carrier][purpose] += 1
                remainders.append(dict(owner=carrier, purpose=purpose, units=1,
                                       reason='odd purpose total cannot buy a complete two-battle '
                                              'pair; this unit stays unspendable'))
    totals = {purpose: sum(matrix[owner][purpose] for owner in owners) for purpose in PURPOSES}
    for purpose, declared in purposes.items():
        if int(declared) != totals.get(purpose, 0):
            raise AssertionError('derived budget changed the %s total: %r -> %r'
                                 % (purpose, declared, totals.get(purpose, 0)))
    return dict(mode=config['mode'], source=source, owners=list(owners), registered=registered,
                matrix=matrix, totals=totals, remainders=remainders,
                note='derived from allocations; zero owners stay zero and every purpose total is '
                     'preserved exactly')


def plan_migration(old_allocations, mode, old_purposes=None, old_budgets=None):
    """A PURE mapping from `old_allocations` to a validated config for `mode`.

    Preserves the allocation total exactly. The retiring `average` allocation is mapped to the shared
    refinement owner (`community`) in every mode; in `community-first` every other stream is folded
    there too, while in `all-strategy` the other allocations are preserved. When the old config's
    `purposes`/`budgets` are supplied they are carried through (retired/non-eligible owners folded
    into the refinement owner) so a migration never silently drops a budget or an enabled purpose.
    Returns the planned config, a per-stream ledger and a rollback snapshot. Never writes anything.
    """
    if mode not in MODES:
        raise ValueError('unknown mode %r; expected one of %s' % (mode, MODES))
    old = _validated_allocations(old_allocations, legacy=True)
    total_before = sum(old.values())
    mapped = {}
    ledger = []
    for name, value in sorted(old.items()):
        if name == RETIRED_STREAM:
            target = REFINEMENT_OWNER
            reason = ('average (Student 9) retired: allocation moves to the shared refinement owner '
                      '(community)')
        elif mode == 'community-first':
            target = REFINEMENT_OWNER
            reason = 'community-first: only the community stream is eligible; allocation folded in'
        else:
            target = name
            reason = 'all-strategy: allocation preserved'
        mapped[target] = mapped.get(target, 0.0) + value
        ledger.append(dict(stream=name, oldValue=value, target=target, newValue=value, reason=reason))
    total_after = sum(mapped.values())
    if abs(total_before - total_after) > 1e-12:
        raise AssertionError('migration changed the allocation total: %r -> %r'
                             % (total_before, total_after))
    purposes = (dict(old_purposes) if old_purposes is not None
                else {name: 1 for name in PURPOSES})
    budgets = None
    if old_budgets is not None:
        budgets = {}
        for owner, purpose_map in old_budgets.items():
            owner = str(owner)
            target = REFINEMENT_OWNER if (mode == 'community-first' or owner == RETIRED_STREAM) else owner
            slot = budgets.setdefault(target, {purpose: 0 for purpose in PURPOSES})
            for purpose, total in purpose_map.items():
                slot[purpose] = slot.get(purpose, 0) + int(total)
    config = dict(version=CONFIG_VERSION, mode=mode, allocations=mapped, purposes=purposes)
    if budgets is not None:
        config['budgets'] = budgets
    validate_config(config)
    rollback = dict(version=CONFIG_VERSION, mode=mode, allocations=old)
    if old_purposes is not None:
        rollback['purposes'] = dict(old_purposes)
    if old_budgets is not None:
        rollback['budgets'] = {owner: dict(purpose_map) for owner, purpose_map in old_budgets.items()}
    return dict(config=config, ledger=ledger, total=total_after, rollback=rollback,
                applied=False, budgetsPreserved=budgets is not None,
                note='pure plan only; the caller must persist it deliberately (never auto-applied)')
