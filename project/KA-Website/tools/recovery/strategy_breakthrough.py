"""Breakthrough / reliability: turn exceptional combat outcomes into measured, repeatable earning.

The optimiser already records *what* each build earned. This module owns the missing loop:

    runs (any student, any rank)  ->  contrast high vs ordinary  ->  a testable hypothesis
      ->  legal build interventions  ->  paired measurement on fresh seeds  ->  independent
      confirmation  ->  a candidate handed back to the existing population

It is a share stream, not a second leaderboard. Three properties are deliberate:

  * **Reward stays the judge.** Nothing here ranks, promotes or confirms on a proxy: stored-command
    counters, survival and kill timing are *explanations* that decide which intervention to test next,
    never a success claim. `earnedMax` is read to nominate diagnostic examples, never to estimate
    reliability (a build tested 20,000 times has had 20,000 chances at an extreme maximum).
  * **Evidence is compatible or it is not used.** A record is eligible only for its own encounter and
    its own declared scope (the canonical `scope()` of the scenario: horizon, seeds-excluded fields,
    item stock and policy). Death records rolled out of the replay window leave a compact row with no
    telemetry, so coverage is reported as incomplete rather than inferred from aggregates.
  * **The diagnostic archive never estimates a probability.** Outcome statistics come from every
    eligible run; the archive is a bounded, band-stratified set of *examples* for investigation.

Budget: the stream has its own share, 0% by default. At 0% it finalises work already in flight and
launches nothing: no proposals, no experiments, no analysis campaign. Every run it causes is charged to
its own budget through the same per-candidate banks the probe facility uses.

    python -c "import strategy_breakthrough"
"""
from __future__ import annotations

import json
import statistics
import time
from copy import deepcopy

import strategy_probe
import strategy_students as students
from strategy_optimizer import canonical, chest_count, identity, scope, seed_pair

#: The share stream this module answers to (`students.STUDENT_MECHANISM`).
SOURCE = students.STUDENT_MECHANISM
#: Human label, used in candidate labels and the UI. Descriptive, with no student number: the registry
#: keys students by name, and inventing a number here would duplicate one the plan already assigns.
LABEL = 'Breakthrough / reliability'
LABEL_PREFIX = 'Breakthrough · '

#: One meta blob. Bounded: the archive has hard per-band and per-candidate caps.
STATE_KEY = 'breakthrough'
STATE_VERSION = 1

#: Reward bands for the diagnostic archive. ANALYSIS SETTINGS, not game constants; a distribution is
#: not assumed to be bell-shaped, which is why the bands are explicit boundaries rather than quantiles.
REWARD_BANDS = ((0, 0), (1, 1), (2, 4), (5, 9), (10, 19), (20, None))
#: Reward thresholds whose hit probabilities are reported. ENCOUNTER/CONFIGURATION PARAMETERS.
DEFAULT_THRESHOLDS = (5, 10, 15, 20)

#: Examples kept per band, per candidate, and in total. Small on purpose: this is a reading list.
ARCHIVE_PER_BAND = 12
ARCHIVE_PER_CANDIDATE = 4
ARCHIVE_TOTAL = 60

#: Run banks. 64 is the existing screening bank; the ladder up is the existing validation ladder.
SCREEN_BANK = 64
MAX_BANK = 1024
CONFIRM_BANK = 512
#: Plans in flight per encounter, and candidates one screening plan may create.
MAX_PLANS_PER_ENCOUNTER = 2
#: Screening batches one hypothesis may spend before the region is recorded as unproductive. A
#: hypothesis is never called impossible - it is deprioritised, and new evidence can propose another.
MAX_ATTEMPTS_PER_HYPOTHESIS = 2
#: One batch is one direction (A/B/C/D = 4 arms); testing both directions in one plan is 7.
MAX_ARMS = 8
#: Compatible candidates analysed per slow pass. Small enough that a pass stays cheap, large enough that
#: a whole encounter is covered in a few passes; the rotation cursor carries the rest, so this is a rate
#: and never a permanent exclusion.
ANALYSIS_PER_PASS = 8
#: Runs this stream may consume per encounter before it reports exhausted budget.
RUNS_PER_ENCOUNTER_CAP = 6000

#: The two controllable axes the first investigation tests. Verified mapping from
#: `strategy_probe.STAT_AXES` (recovered `stat-parameter-ids.ts`): Attack is native parameter 13 and
#: Agility ("Speed" in the UI) is 15. Neither sign is assumed: the first batch tests a separated step
#: in *both* directions, because "lower is always better" is exactly what must be measured, not assumed.
PRIMARY_AXES = ('atk', 'spd')
#: Separated step for a screening arm: half and one-and-a-half times the parent's own value.
STEP_FRACTIONS = {'low': 0.5, 'high': 1.5}

#: Hypothesis lifecycle. `paused_for_budget` and `inconclusive` are not failures: they are states the
#: loop can resume from, and a mechanism is never called impossible because a finite search missed it.
HYPOTHESIS_STATUSES = ('proposed', 'observationally_supported', 'intervention_supported',
                       'failed_in_tested_region', 'inconclusive', 'paused_for_budget')
#: Encounter-level status shown in the UI.
ENCOUNTER_STATUSES = ('nothing_supported_yet', 'telemetry_missing', 'experiment_pending',
                      'promising_unconfirmed', 'confirmed_improvement',
                      'no_improvement_in_tested_region',
                      # A published confirmation that its own frozen evidence no longer supports. It is
                      # withdrawn from the badge and from selection, not from the record.
                      'claims_need_revalidation')

#: Resolved encounter names, read once from the same runtime data the optimiser composes its enemies
#: from. A number identifies an encounter unambiguously but says nothing to a reader, so every published
#: label carries the name and difficulty the game itself uses.
_CATALOGUE = None


def encounter_label(encounter):
    """`'Take out the Wairo Tank! (Wairo, enc 19)'` - the encounter's own name, then its number."""
    global _CATALOGUE
    if _CATALOGUE is None:
        try:
            import strategy_optimizer
            _CATALOGUE = strategy_optimizer.encounter_catalogue() or {}
        except Exception:  # noqa: BLE001 - a missing catalogue must not stop the stream
            _CATALOGUE = {}
    entry = _CATALOGUE.get(str(int(encounter))) or {}
    title = entry.get('title')
    if not title:
        return f'encounter {int(encounter)}'
    return f'{title} (enc {int(encounter)})'


def _now(now=None):
    return time.time() if now is None else float(now)


def _bands():
    return [dict(low=low, high=high, label=(f'{low}+' if high is None else
                                            f'{low}' if low == high else f'{low}-{high}'))
            for low, high in REWARD_BANDS]


def band_of(value):
    """The band label a chest count falls in, or None when the run has no resolved reward."""
    if value is None:
        return None
    for low, high in REWARD_BANDS:
        if high is None or value <= high:
            return f'{low}+' if high is None else (f'{low}' if low == high else f'{low}-{high}')
    return None


# ---------------------------------------------------------------------------------------------
# Evidence: a compatible, de-duplicated view of the whole encounter
# ---------------------------------------------------------------------------------------------

def _candidate_rows(db, encounter):
    """Every resident candidate of one fight, with the two identities the evidence rules need."""
    rows = db.execute(
        'SELECT c.id AS id, c.scenario AS scenario, c.label AS label, c.source AS source, '
        'c.created AS created, l.source AS stream, l.parent AS parent '
        'FROM candidate c JOIN candidate_meta m ON m.id=c.id '
        'LEFT JOIN lineage l ON l.candidate=c.id WHERE m.encounter=?', (int(encounter),)).fetchall()
    out = {}
    for row in rows:
        try:
            scenario = json.loads(row['scenario'])
        except (TypeError, ValueError):
            continue
        out[row['id']] = dict(candidate=row['id'], label=row['label'], source=row['source'],
                              stream=row['stream'] or 'unowned', parent=row['parent'],
                              scenario=scenario, created=row['created'],
                              scope=scope(scenario), identity=identity(scenario))
    return out


def evidence_view(store, encounter):
    """`(rows, coverage)` - every compatible, de-duplicated run record of one encounter.

    One record per *independent* observation: keyed by the run's own seed pair within its candidate, so
    the same deterministic battle replayed (or stored twice under two identical scenarios) is one
    sample, not two. Reward stays `None` for a censored or unresolved run: those are counted and
    reported, never folded into a mean as a zero.

    `coverage` states what the view could not see: runs whose detail has been rolled out of the replay
    window (they keep their compact row, so their reward is counted but their behaviour is not), and
    candidates whose scenario is no longer resident.
    """
    candidates = _candidate_rows(store.db, encounter)
    frames = {}
    for candidate in candidates.values():
        frames.setdefault(candidate['scope'], []).append(candidate)
    rows, seen, missing_detail, unresolved = [], set(), 0, 0
    for frame, members in frames.items():
        for member in members:
            cid = member['candidate']
            # The resident run rows carry the telemetry; the compact evidence rows survive retention.
            detailed = {}
            for row in store.db.execute(
                    'SELECT phase,ordinal,result FROM run WHERE candidate=?', (cid,)):
                try:
                    detailed[(row['phase'], int(row['ordinal']))] = json.loads(row['result'])
                except (TypeError, ValueError):
                    continue
            for phase, ordinal, decoded in _evidence_rows(store.db, cid):
                seeds = decoded.get('seeds')
                # Deduplicate the *same deterministic battle*: the same scenario content on the same
                # seed pair is one observation, however many times it was replayed or stored. Two
                # different builds measured on the same seeds are two battles, not a repeat, which is
                # why the key is the scenario identity and not the scope.
                key = (member['identity'], tuple(seeds) if isinstance(seeds, list) else None)
                if isinstance(seeds, list) and key in seen:
                    continue
                seen.add(key)
                censored = bool(decoded.get('censored'))
                # The *authoritative* Earned, not a direct `awardedChests` read. A resolved WIN whose
                # runner holds no certificate still released the chests it had queued at the verdict
                # (`chest_count`'s `queued-at-victory`), and a loss is the native gate's zero. Reading
                # `awardedChests` alone dropped every non-certified win, which is what made the earlier
                # coverage table claim that only the Wairo fights earned anything: on Kairobot Knight
                # (Normal) this library's own aggregate records 88 measured runs with a 1.93 mean while
                # the old reader reported a sample of 359 and a mean of 0.
                earned, earned_basis = chest_count(dict(
                    censored=censored, verdict=decoded.get('verdict'),
                    prizeCallbacks=decoded.get('prizeCallbacks'),
                    rewardOutcome=dict(decoded.get('rewardOutcome') or {})))
                if earned is None:
                    unresolved += 1
                result = detailed.get((phase, ordinal))
                if result is None:
                    missing_detail += 1
                rows.append(dict(candidate=cid, label=member['label'], stream=member['stream'],
                                 phase=phase, ordinal=ordinal, seeds=seeds,
                                 earned=earned, earnedBasis=earned_basis,
                                 verdict=decoded.get('verdict'), censored=censored,
                                 prizeCallbacks=decoded.get('prizeCallbacks'),
                                 pendingChests=(decoded.get('rewardOutcome') or {}).get('pendingChests'),
                                 result=result, scope=frame,
                                 telemetry=bool(result and result.get('progressMetrics'))))
    rows.sort(key=lambda row: (row['candidate'], row['phase'], row['ordinal']))
    coverage = dict(encounter=int(encounter), candidates=len(candidates), records=len(rows),
                    scopes=len(frames), unresolved=unresolved, withoutDetail=missing_detail,
                    withTelemetry=sum(1 for row in rows if row['telemetry']))
    return rows, coverage


def _evidence_rows(db, candidate):
    """`(phase, ordinal, decoded)` from the compact table, via the module that owns its schema."""
    import strategy_evidence
    return [(phase, ordinal, row) for phase, ordinal, row in strategy_evidence.indexed(db, candidate)]


def outcome_stats(rows, thresholds=DEFAULT_THRESHOLDS):
    """Counts and reward summaries over every eligible record of one encounter.

    Losses are included exactly as the reward rules state them (a native loss awards zero), unresolved
    runs are excluded from the mean and reported separately, and a zero observed high outcome is
    reported as "no observation in this sample", never as a probability of zero.
    """
    values = [row['earned'] for row in rows if row['earned'] is not None]
    ordered = sorted(values)
    n = len(ordered)
    mean = statistics.mean(ordered) if ordered else None
    sd = statistics.stdev(ordered) if n > 1 else None
    se = (sd/(n**0.5)) if sd is not None and n else None
    def quantile(fraction):
        if not ordered:
            return None
        index = min(n-1, max(0, int(round(fraction*(n-1)))))
        return ordered[index]
    hits = {}
    for threshold in thresholds:
        count = sum(1 for value in ordered if value >= threshold)
        hits[int(threshold)] = dict(hits=count, n=n,
                                    rate=(count/n if n else None),
                                    wilson=_wilson(count, n))
    return dict(n=n, mean=mean, sd=sd, se=se, median=quantile(0.5), p10=quantile(0.1),
                p90=quantile(0.9), minimum=ordered[0] if ordered else None,
                maximum=ordered[-1] if ordered else None,
                zeroRate=(sum(1 for value in ordered if value == 0)/n if n else None),
                lowRate=(sum(1 for value in ordered if value <= 1)/n if n else None),
                thresholds=hits,
                unresolved=sum(1 for row in rows if row['censored'] or row['earned'] is None),
                censored=sum(1 for row in rows if row['censored']))


def _wilson(successes, count):
    if not count:
        return [0.0, 1.0]
    z = 1.96
    p = successes/count
    center = (p + z*z/(2*count))/(1 + z*z/count)
    half = z*((p*(1-p)/count + z*z/(4*count*count))**0.5)/(1 + z*z/count)
    return [max(0., center-half), min(1., center+half)]


def archive(previous, rows):
    """Update the bounded diagnostic archive. `(archive, totals)`.

    **Idempotent by construction.** The band totals are *recomputed* from the unique eligible rows of the
    view, not accumulated onto whatever the previous call stored: the first version added every row to
    the existing totals again, so reanalysing unchanged data inflated every band and duplicated work.
    `evidence_view` already collapses exact (scenario, seed) repetitions, so each row is one observation.

    Only a bounded reservoir of *examples* is kept - capped per band and per candidate, so one heavily
    tested build cannot monopolise the reading list - and the reservoir is *replaceable*: a full band
    admits a better example instead of freezing forever on the first twelve it ever saw. One example per
    band is taken before any band gets a second, so ordinary controls are not crowded out by the top band.
    """
    previous = dict(previous or {})
    bands = {definition['label']: dict(count=0, kept=0) for definition in _bands()}
    kept = {(entry['candidate'], entry['phase'], entry['ordinal']): dict(entry)
            for entry in previous.get('examples') or []}
    for row in rows:
        band = band_of(row['earned'])
        if band is None:
            continue
        bands[band]['count'] += 1

    def rank(row):
        return (-(row['earned'] or 0), row['candidate'], row['phase'], row['ordinal'])

    nominated = sorted((row for row in rows
                        if row['earned'] is not None and band_of(row['earned']) is not None),
                       key=rank)
    for row in nominated:
        band = band_of(row['earned'])
        key = (row['candidate'], row['phase'], row['ordinal'])
        if key in kept:
            continue
        per_band = sum(1 for entry in kept.values() if entry['band'] == band)
        per_candidate = sum(1 for entry in kept.values() if entry['candidate'] == row['candidate'])
        if per_candidate >= ARCHIVE_PER_CANDIDATE:
            continue
        if per_band >= ARCHIVE_PER_BAND:
            weakest = min((entry for entry in kept.values() if entry['band'] == band),
                          key=lambda entry: (entry['earned'], entry['candidate']), default=None)
            if weakest is None or (weakest['earned'] or 0) >= (row['earned'] or 0):
                continue
            kept.pop((weakest['candidate'], weakest['phase'], weakest['ordinal']), None)
        elif len(kept) >= ARCHIVE_TOTAL:
            continue
        # A diagnostic example nobody can inspect is not worth a slot: it is counted in the band above
        # and skipped here, with the coverage report saying how many records carry no detail.
        kept[key] = dict(candidate=row['candidate'], label=row['label'], stream=row['stream'],
                         phase=row['phase'], ordinal=row['ordinal'],
                         seeds=list(row['seeds']) if isinstance(row['seeds'], list) else None,
                         earned=row['earned'], band=band,
                         behaviour=_behaviour(row['result']),
                         telemetry=bool(row['telemetry']))
    examples = sorted(kept.values(), key=rank)
    for entry in examples:
        bands[entry['band']]['kept'] += 1
    return dict(bands=bands, examples=examples), bands


def _behaviour(result):
    """The lightweight telemetry of one run: existing `progressMetrics` plus the compact behaviour.

    Nothing new is instrumented here and no counter is invented: a field is reported only when the run
    carries it, and a missing value stays missing.
    """
    if not isinstance(result, dict):
        return None
    progress = result.get('progressMetrics')
    behaviour = result.get('behavior')
    out = dict(ticks=result.get('ticks'), survivors=result.get('survivors'),
               attacks=(behaviour or {}).get('attacks'),
               heals=(behaviour or {}).get('heals'))
    # No prize/reward restatement is carried here: `prizes` *is* the outcome this module is trying to
    # explain, so using it as a feature would let the contrast "discover" the reward it already knows.
    if isinstance(progress, dict):
        # Every field the stage table reads has to be copied, or `STAGES` silently analyses nothing:
        # `storedTargetHoldersPeak` and `postDeathBossReentries` were named there and never carried
        # here, so the `target` stage and half the `release` stage could not separate at all.
        out.update({key: progress.get(key) for key in (
            'bossDeathTick', 'bossLeavingTick', 'storedCommandsAtDeath',
            'storedCommandsTargetingBossAtDeath', 'commandsTargetingBoss',
            'commandsTargetingBossReleased', 'maxSimultaneousStoredCommands',
            'maxSimultaneousCommandsTargetingBoss', 'postDeathPrizes',
            'storedTargetHoldersPeak', 'postDeathBossReentries', 'postDeathBossLeavings',
            'commandsReleasedAfterDeath', 'commandsReleasedAfterDeathTargetingBoss',
            'firstPostDeathCommandReleaseTick', 'lastPostDeathCommandReleaseTick')})
    return out


# ---------------------------------------------------------------------------------------------
# Within-build contrast: which stage separates a build's productive runs from its ordinary ones
# ---------------------------------------------------------------------------------------------

#: The stages of the reward sequence, in the order a battle must pass them. These are *investigative
#: categories over already-recovered semantics* (stored commands, their target, the release, the
#: settlement), never new game mechanics: each maps to fields `combat_progress` already publishes.
STAGES = (
    ('accumulate', ('maxSimultaneousStoredCommands', 'commandsTargetingBoss')),
    ('target', ('storedTargetHoldersPeak', 'storedCommandsTargetingBossAtDeath')),
    # `survive` is about *timing*, not about the survivors a run ends with: `survivors` is the verdict
    # restated (a loss has none), so it can never explain a reward. What a build can move upstream is
    # how long the enemy stayed alive and whether the unit was still there to release.
    ('survive', ('bossDeathTick',)),
    ('release', ('commandsReleasedAfterDeath', 'commandsReleasedAfterDeathTargetingBoss',
                 'postDeathBossReentries', 'firstPostDeathCommandReleaseTick',
                 'lastPostDeathCommandReleaseTick')),
)
#: Reported beside every example and never used to choose an intervention: `postDeathPrizes` is the
#: reward the post-death dump produced, so presenting it as an upstream explanation of reward would be
#: the restatement the task forbids. `settle` is therefore not a stage.
SETTLEMENT_OBSERVATIONS = ('postDeathPrizes',)


def _values(rows, key):
    """One telemetry feature across rows, from the run's own behaviour block. Missing stays missing."""
    return [row['behaviour'][key] for row in rows
            if isinstance((row.get('behaviour') or {}).get(key), (int, float))]


def _median(values):
    return statistics.median(values) if values else None


#: ANALYSIS SETTING: the explicit reward band that defines "productive" when it is populated. The top
#: quartile is not the exceptional group on a fight whose rewards are mostly one chest, so the band is
#: tried first and the quartiles are only the fallback.
PRODUCTIVE_BAND_FLOOR = 5
#: A feature *shifts* when the two groups' medians differ by more than half their pooled scale: that is
#: what catches a materially moved but overlapping behaviour a strict disjoint-range rule would ignore.
SHIFT_RATIO = 0.5


def _feature_groups(scored, quartile=0.25):
    """`(productive, ordinary, how)` for one build's own runs.

    Preference order: the declared reward band (at least three runs on each side), then the extreme
    quartiles with the middle dropped. The rule is reported, so a hypothesis can state which split it
    read rather than implying one definition of "productive" for every fight.
    """
    floor = PRODUCTIVE_BAND_FLOOR
    high = [row for row in scored if (row['earned'] or 0) >= floor]
    low = [row for row in scored if (row['earned'] or 0) < floor]
    if len(high) >= 3 and len(low) >= 3:
        return high, low, f'reward>={floor} vs below'
    cut = max(2, int(len(scored)*quartile))
    return scored[:cut], scored[-cut:], f'top/bottom {cut} of {len(scored)}'


def contrast_within(rows, candidate, quartile=0.25):
    """The same build's productive runs against its ordinary ones. `(contrast, coverage)`.

    Splitting one build's own runs is the strongest available control: the scenario is identical, so a
    difference cannot be a different build. The contrast reports a stage as *separating* only when the
    two halves' ranges do not overlap at all - a deliberately strict rule, because a difference of
    medians is not a bottleneck. It never claims a mechanism the evidence does not show, and it reports
    `None` for a build whose runs carry no telemetry rather than guessing from aggregates.
    """
    # Only runs whose behaviour detail is still resident can support a stage claim. A record whose
    # telemetry has been rolled out of the replay window keeps its reward (counted by the statistics)
    # but is not evidence for a mechanism, and the coverage note says how many were left out.
    mine = [dict(row, behaviour=_behaviour(row['result']) or {}) for row in rows
            if row['candidate'] == candidate and row['earned'] is not None and row['telemetry']]
    without_telemetry = sum(1 for row in rows
                            if row['candidate'] == candidate and row['earned'] is not None
                            and not row['telemetry'])
    scored = sorted(mine, key=lambda row: (-(row['earned'] or 0), row['phase'], row['ordinal']))
    if len(scored) < 8:
        return None, dict(candidate=candidate, runs=len(scored), withoutTelemetry=without_telemetry,
                          reason=('too few runs with behaviour detail'
                                  if without_telemetry else 'too few scored runs'))
    high, low, grouping = _feature_groups(scored, quartile)
    features = {}
    for stage, keys in STAGES:
        for key in keys:
            top, bottom = _values(high, key), _values(low, key)
            if len(top) < 2 or len(bottom) < 2:
                continue
            top_median, bottom_median = _median(top), _median(bottom)
            # A *shifted* middle is evidence too. Requiring non-overlapping ranges alone ignores a
            # materially moved but overlapping behaviour, which is the common case on a noisy fight.
            scale = max(1e-9, (abs(top_median)+abs(bottom_median))/2)
            shift = abs(top_median-bottom_median)/scale > SHIFT_RATIO and top_median != bottom_median
            features[key] = dict(stage=stage, highMedian=top_median, lowMedian=bottom_median,
                                 highMin=min(top), highMax=max(top),
                                 lowMin=min(bottom), lowMax=max(bottom),
                                 separates=min(top) > max(bottom) or max(top) < min(bottom),
                                 shift=shift,
                                 direction=('higher' if top_median > bottom_median else 'lower'),
                                 delta=top_median-bottom_median)
    separating = sorted([key for key, value in features.items()
                         if value['separates'] or value['shift']],
                        key=lambda key: -abs(features[key]['delta']))
    return (dict(candidate=candidate, runs=len(mine), highRuns=len(high), lowRuns=len(low),
                 excluded=len(mine)-len(high)-len(low),
                 grouping=grouping,
                 highMean=statistics.mean([row['earned'] for row in high]),
                 lowMean=statistics.mean([row['earned'] for row in low]),
                 highEarned=[row['earned'] for row in high],
                 lowEarned=[row['earned'] for row in low],
                 features=features, separating=separating,
                 highRuns_=[dict(phase=row['phase'], ordinal=row['ordinal'], earned=row['earned'],
                                 seeds=row['seeds']) for row in high[:4]],
                 lowRuns_=[dict(phase=row['phase'], ordinal=row['ordinal'], earned=row['earned'],
                                seeds=row['seeds']) for row in low[:4]]),
            dict(candidate=candidate, runs=len(mine), withoutTelemetry=without_telemetry))


def stage_bottleneck(contrasts):
    """Which stage the evidence points at, and how strongly. `(stage, evidence)`.

    The bottleneck is the earliest stage in the sequence that some *one* contrast actually supports: a
    build that never accumulates cannot be helped by releasing better, so the order matters. Each stage
    keeps the contrast that supports it, because choosing the stage globally and the "best" contrast
    separately let the two describe different builds.
    """
    tally = {}
    for contrast in contrasts:
        if not contrast:
            continue
        for key in contrast['separating']:
            stage = next(stage for stage, keys in STAGES if key in keys)
            entry = tally.setdefault(stage, dict(features=[], contrast=contrast,
                                                 order=len(tally)))
            entry['features'].append(key)
    for index, (stage, _keys) in enumerate(STAGES):
        if stage in tally:
            entry = dict(tally[stage])
            entry['stageIndex'] = index
            entry['candidate'] = entry['contrast']['candidate']
            return stage, entry
    return None, {}


# ---------------------------------------------------------------------------------------------
# Hypotheses: explicit, machine-readable, and linked to what would disprove them
# ---------------------------------------------------------------------------------------------

def hypothesis_id(encounter, parent, axes, basis='measured'):
    """A stable id. A *prior* is keyed by the encounter and its axes only, never by its anchor: the
    anchor moves as soon as an arm is measured, and an id that moves with it would re-open the same
    investigation under a new name on every pass."""
    if basis == 'declared_prior':
        return f'h:{int(encounter)}:prior:{"+".join(sorted(axes))}'
    return f'h:{int(encounter)}:{parent[:12]}:{"+".join(sorted(axes))}'


def propose_hypothesis(encounter, scope_key, contrasts, bottleneck, parent, axes=PRIMARY_AXES):
    """One hypothesis record. `None` when the evidence does not support proposing anything yet.

    The record carries the scope, the supporting and contrasting run references, the proposed stage,
    the controllable variables, the expected behavioural and reward changes, and the observation that
    would falsify it. The wording is generated from what was measured - it is not a template promising
    a mechanism the data has not shown.
    """
    if bottleneck is None or not contrasts:
        return None
    strongest = contrasts[0]
    features = [key for key in strongest['separating']
                if key in dict((key, stage) for stage, keys in STAGES for key in keys)
                and next(stage for stage, keys in STAGES if key in keys) == bottleneck]
    if not features:
        return None
    family = {'community': 'Community family', 'mutation': 'generated build'}.get(
        strongest.get('family') or '', 'compatible build')
    statement = (f'Within this {family}, ordinary runs of {strongest["candidate"][:10]} do not reach '
                 f'the {bottleneck} stage the way its productive runs do '
                 f'({", ".join(features)} separates {strongest["highEarned"]} from '
                 f'{strongest["lowEarned"]}). Changing {", ".join(axes)} should move that stage and, '
                 f'through it, the earned distribution.')
    return dict(id=hypothesis_id(encounter, parent, axes), encounter=int(encounter), scope=scope_key,
                parent=parent, family=strongest.get('family'),
                # Every hypothesis carries its basis. `_adopt_hypothesis` refreshes these keys, and a
                # measured hypothesis that omitted `basis` raised KeyError the first time it was
                # refreshed - which the caller's guard turned into a silent per-pass failure.
                basis='measured',
                bottleneck=bottleneck, features=features, statement=statement,
                status='proposed',
                supporting=strongest['highRuns_'], contrasting=strongest['lowRuns_'],
                controllable=[dict(axis=axis, parameter=strategy_probe.axis_info(axis).get('parameter'),
                                   direction='both', why='measured, not assumed')
                              for axis in axes],
                expectedBehaviour={feature: f'increase in the {bottleneck} stage' for feature in features},
                expectedReward='mean earned and the 5/10/15/20-chest hit rates rise on fresh seeds',
                falsifier=('no arm improves the paired mean on fresh seeds, or the stage feature does '
                           'not move, or the effect appears only on the seeds that nominated it'),
                attempted=[], evidence=dict(highRuns=len(strongest['highEarned']),
                                            lowRuns=len(strongest['lowEarned']),
                                            contrasts=len(contrasts)),
                created=_now())


#: Scored runs an encounter needs before the declared prior is worth a controlled batch. A prior is not
#: evidence: it is the user's proposal, recorded as a hypothesis to be *tested*, which is how a rare
#: signal can start an investigation instead of waiting for a separation that a 3%-frequency outcome
#: cannot produce in a 64-run bank.
MIN_RUNS_FOR_PRIOR = 32


def prior_hypothesis(encounter, scope_key, rows, parent, axes=PRIMARY_AXES):
    """The declared mechanism's hypothesis, machine-readable, with its falsifier attached.

    Written when the evidence does not yet separate a stage. It claims nothing: it records what the
    user proposed, the examples that motivate it (the encounter's own high runs, from *any* rank), the
    legal variables the batch will move, and the observation that would contradict it.
    """
    scored = [row for row in rows if row['earned'] is not None]
    if len(scored) < MIN_RUNS_FOR_PRIOR:
        return None
    ordered = sorted(scored, key=lambda row: (-(row['earned'] or 0), row['candidate'],
                                              row['phase'], row['ordinal']))
    high, low = ordered[:3], ordered[-3:]
    return dict(id=hypothesis_id(encounter, parent, axes, basis='declared_prior'),
                encounter=int(encounter), scope=scope_key,
                parent=parent, family=None, basis='declared_prior',
                bottleneck='accumulate',
                features=[],
                statement=('No stage separates this encounter\'s productive runs from its ordinary ones '
                           'yet, so the declared mechanism is tested instead of assumed: if the '
                           'high-reward episodes come from a longer accumulation phase, reducing damage '
                           'and changing the attack timing should make them more frequent, and only '
                           'the joint change should be needed for the interaction.'),
                status='proposed',
                supporting=[dict(candidate=row['candidate'], label=row['label'],
                                 phase=row['phase'], ordinal=row['ordinal'], earned=row['earned'],
                                 seeds=row['seeds']) for row in high],
                contrasting=[dict(candidate=row['candidate'], label=row['label'],
                                  phase=row['phase'], ordinal=row['ordinal'], earned=row['earned'],
                                  seeds=row['seeds']) for row in low],
                controllable=[dict(axis=axis, parameter=strategy_probe.axis_info(axis).get('parameter'),
                                   direction='both', why='measured, not assumed') for axis in axes],
                expectedBehaviour={'maxSimultaneousStoredCommands': 'the accumulation phase lengthens',
                                   'commandsReleasedAfterDeath': 'more of what accumulated is released'},
                expectedReward='the 5/10/15/20-chest hit rates rise and the mean earned rises with them',
                falsifier=('no arm improves the paired mean on fresh seeds, or only the seed set that '
                           'selected it does, or the stage features do not move at all'),
                attempted=[], created=_now(),
                evidence=dict(highRuns=len(scored), lowRuns=0, contrasts=0,
                              note='prior, not a measured separation'))


# ---------------------------------------------------------------------------------------------
# Legal interventions: the controlled arms
# ---------------------------------------------------------------------------------------------

def _axis_value(scenario, unit, axis, fraction):
    """A separated, legal value for one axis: `current * fraction`, clamped inside the stat wall."""
    import search_contract
    current = strategy_probe.pivot_value(scenario, unit, axis)
    info = strategy_probe.axis_info(axis)
    if 'parameter' not in info:
        return None
    minimum, maximum = search_contract.stat_bounds()[info['parameter']]
    target = int(round(current*fraction))
    if fraction < 1 and target >= current:
        # A step that rounds back to the parent is not an intervention. Move by a fraction of the
        # wall instead, so a small or heavily walled stat still gets a separated test.
        target = max(minimum, current - max(1, int((maximum-minimum)*0.15)))
    if fraction > 1 and target <= current:
        target = min(maximum, current + max(1, int((maximum-minimum)*0.15)))
    return int(min(maximum, max(minimum, target)))


def plan_arms(parent_scenario, unit, axes=PRIMARY_AXES, directions=('low',)):
    """The legal arms of one screening experiment.

    A is the unchanged parent, B moves the timing axis alone, C the damage axis alone, D both - the
    user's proposed mechanism - and each direction is a separated step, so the *sign* of the effect is
    measured rather than assumed. Every arm is produced by `strategy_probe.apply_axis`, which is the
    same verified single-axis edit the probe facility already uses, so nothing here is a new way to
    build a scenario.
    """
    axes = tuple(axes)
    arms = [dict(name='A', role='parent', axis=None, value=None, scenario=None)]
    plan = {}
    for direction in directions:
        fraction = STEP_FRACTIONS[direction]
        values = {}
        for axis in axes:
            value = _axis_value(parent_scenario, unit, axis, fraction)
            if value is None:
                return None, f'{axis} has no parameter to move'
            values[axis] = value
        order = [('B', axes[1:], 'speed'), ('C', axes[:1], 'damage'),
                 ('D', axes, 'damage+speed')] if len(axes) > 1 else [('B', axes, 'value')]
        for name, moved, role in order:
            if any(values.get(axis) is None for axis in moved):
                continue
            scenario = deepcopy(parent_scenario)
            for axis in moved:
                scenario = strategy_probe.apply_axis(scenario, unit, axis, values[axis])
            arms.append(dict(name=name if len(directions) == 1 else f'{name}.{direction}',
                             role=f'{role} ({direction})', axis='+'.join(moved),
                             value={axis: values[axis] for axis in moved}, scenario=scenario))
    return arms[:MAX_ARMS], None


# ---------------------------------------------------------------------------------------------
# Persisted state: hypotheses, plans, archive, budget, decisions
# ---------------------------------------------------------------------------------------------

def load(store):
    state = dict(store.get(STATE_KEY) or {})
    state.setdefault('version', STATE_VERSION)
    state.setdefault('encounters', {})
    return state


def save(store, state):
    state['version'] = STATE_VERSION
    with store.db:
        store.set(STATE_KEY, state)


def encounter_state(state, encounter):
    """The per-encounter record, created on first use so a restart resumes where it stopped."""
    record = state['encounters'].setdefault(str(int(encounter)), dict(
        hypotheses=[], plans=[], archive=dict(bands={}, examples=[]),
        budget=dict(runs=0, plans=0, experiments=0, confirmations=0),
        status='nothing_supported_yet', lastDecision='', updated=None, proposals=0))
    record.setdefault('hypotheses', [])
    record.setdefault('plans', [])
    record.setdefault('archive', dict(bands={}, examples=[]))
    record.setdefault('budget', dict(runs=0, plans=0, experiments=0, confirmations=0))
    return record


def plan_banks(record):
    """`{candidate id: validation bank}` for every unfinished plan of one encounter."""
    out = {}
    for plan in record.get('plans') or []:
        if plan.get('status') != 'pending':
            continue
        bank = int(plan.get('bank') or 0)
        for cid in [plan.get('parent')] + [arm.get('candidate') for arm in plan.get('arms') or []]:
            if cid and bank:
                out[cid] = max(out.get(cid, 0), bank)
    return out


def banks(store):
    """Every bank this stream has authorised, for the scheduler's existing `probeBanks` mechanism."""
    state = load(store)
    out = {}
    for record in state['encounters'].values():
        for cid, bank in plan_banks(record).items():
            out[cid] = max(out.get(cid, 0), bank)
    return out


def _evidence_counts(store, candidates):
    """`{candidate id: measured evidence rows}` for a bounded set of candidates, in one query."""
    ids = sorted({cid for cid in candidates if cid})
    if not ids:
        return {}
    counts = {}
    placeholders = ','.join('?'*len(ids))
    for cid, count in store.db.execute(
            f'SELECT candidate, COUNT(*) FROM evidence WHERE candidate IN ({placeholders}) '
            'GROUP BY candidate', tuple(ids)):
        counts[cid] = int(count)
    return counts


def plan_watermark(store, plan):
    """The per-participant evidence counts at the moment a plan is authorised.

    Recorded on the plan itself so that what the plan *caused* can be told apart from what its
    participants had already run. Without it, a control with 300,000 historical runs made one
    encounter look as if it had spent those runs, and a plan could not be audited at all.
    """
    participants = _plan_participants(plan)
    counts = _evidence_counts(store, participants)
    return {cid: int(counts.get(cid, 0)) for cid in participants}


def _spent_detail(store, record=None):
    """`(attributed, unattributed)` evidence rows this stream's experiments caused in one encounter.

    Charged to the *request* that caused the work, not to whoever created the candidate: a Breakthrough
    experiment testing an Average build spends Breakthrough's budget while the build keeps Average as
    its creator, and a control with another creator is charged here because its runs were requested
    here. The first version counted every evidence row whose *candidate* lineage said `mechanism`
    across the whole library and called that one encounter's spend - so a single fight could exhaust
    every fight's supposed budget.

    `attributed` is exact: a plan's own watermark is subtracted from each participant's evidence count,
    so only rows the plan added are charged. For a plan recorded before watermarks existed, the rows
    of the candidates the plan itself *created* are its work (they could not have existed otherwise),
    while its pre-existing controls' history is reported in `unattributed` rather than invented as
    this experiment's spend. Historical runs are therefore never retroactively billed, and the size of
    what cannot be attributed is stated instead of guessed.
    """
    if record is None:
        return 0, 0
    plans = record.get('plans') or []
    participants = {cid for plan in plans for cid in _plan_participants(plan)}
    counts = _evidence_counts(store, participants)
    # When each participant entered the library. A participant that appeared at or after the plan was
    # authorised could not have been running before it, so its rows are the plan's own work even
    # without a watermark.
    created_at = {}
    if participants:
        marks = ','.join('?'*len(participants))
        for cid, when in store.db.execute(
                f'SELECT id, created FROM candidate WHERE id IN ({marks})', tuple(sorted(participants))):
            created_at[cid] = int(when or 0)
    attributed, unattributed = 0, 0
    for plan in plans:
        baseline = plan.get('baseline')
        if baseline is not None:
            for cid in _plan_participants(plan):
                attributed += max(0, counts.get(cid, 0) - int(baseline.get(cid) or 0))
            continue
        made = {arm.get('candidate') for arm in plan.get('arms') or []
                if arm.get('candidate') and arm.get('existed') is False}
        started = plan.get('created')
        if started is not None:
            made |= {cid for cid in _plan_participants(plan)
                     if created_at.get(cid, 0) >= int(started)}
        for cid in _plan_participants(plan):
            if cid in made:
                attributed += counts.get(cid, 0)
            else:
                unattributed += counts.get(cid, 0)
    return attributed, unattributed


def _spent_runs(store, record=None):
    """The exactly attributed part of `_spent_detail`: the runs this stream's requests caused."""
    return _spent_detail(store, record)[0]


def _plan_participants(plan):
    """The candidates one plan's runs are charged to: its parent/control first, then its arms."""
    out = []
    for cid in [plan.get('parent')] + [arm.get('candidate') for arm in plan.get('arms') or []]:
        if cid and cid not in out:
            out.append(cid)
    return out


def _window_observed(store, candidate, start, end):
    """How many ordinals of `[start, end)` this candidate has already been measured on."""
    if end <= start:
        return 0
    return int(store.db.execute(
        'SELECT COUNT(DISTINCT ordinal) FROM evidence WHERE candidate=? AND ordinal>=? AND ordinal<?',
        (candidate, int(start), int(end))).fetchone()[0])


def campaign(store, record):
    """One encounter's campaign budget, stated in full: what it is, what it has spent, what it owes.

    `scope` says plainly what the cap bounds. It is **one encounter's** campaign, not one experiment's
    and not the library's: the failed first version charged every evidence row whose candidate lineage
    said `mechanism` across the whole library to one encounter, so a single fight could exhaust every
    fight's budget. `used` is this stream's own measured work in this encounter.

    `used` is what its own requests *caused*, told apart from what its participants had already run:
    a plan's per-participant watermark is subtracted from each participant's evidence count, so a
    control with a long history is not charged as this stream's spend. `unattributedRuns` states the
    part old records cannot account for rather than inventing an attribution for it.

    `reserved` is what its in-flight plans have been authorised but not yet measured, arm by arm and
    control included, measured over each plan's own window: an already-measured ordinal is not reserved
    again, because a cached compatible result costs no new battle. `remaining` is `cap - used -
    reserved`, never negative, so a plan that is committed beyond the cap shows as over-committed
    rather than as free budget.

    `stopReason` is `budget` when the encounter has reached its cap or is paused for it, and None
    otherwise - exhaustion means "paused for budget", never "no better build exists". `extension`
    names the mechanism that continues it: a bounded, explicit raise of this encounter's cap.
    """
    budget = record.get('budget') or {}
    cap = int(budget.get('cap') or RUNS_PER_ENCOUNTER_CAP)
    # What this stream's own requests caused, told apart from what its participants had already run.
    # `unattributed` is the part old plans cannot account for (a control's pre-existing history); it is
    # reported, never billed to the experiment.
    used, unattributed = _spent_detail(store, record)
    reserved = 0
    for plan in record.get('plans') or []:
        if plan.get('status') != 'pending':
            continue
        bank = int(plan.get('bank') or 0)
        if not bank:
            continue
        # A confirmation measures only its fresh window; a screening plan measures from its start.
        start = int(plan.get('developmentBank') or 0) if plan.get('kind') == 'confirmation' else 0
        for cid in _plan_participants(plan):
            reserved += max(0, (bank - start) - _window_observed(store, cid, start, bank))
    status = str(record.get('status') or '')
    exhausted = used >= cap
    return dict(scope='one encounter', cap=cap, used=used, reserved=reserved,
                remaining=max(0, cap - used - reserved), exhausted=exhausted,
                overCommitted=used + reserved > cap,
                unattributedRuns=unattributed,
                stopReason='budget' if (exhausted or status == 'paused_for_budget') else None,
                extension='raise this encounter\'s cap by an explicit number of runs')


def extend_budget(store, encounter, runs, now=None):
    """The user's explicit extension of one encounter's campaign budget.

    This is the resumption mechanism the campaign needs: a cap is a pause, never a permanent stop, so
    the person holding the budget raises it by a stated number of runs and a paused encounter returns
    to work. Everything measured so far is kept exactly as it is - the extension adds authorisation,
    it never rewrites or deletes evidence, and it never un-pauses a stream whose share is off (the
    scheduler's own share gate decides whether runs may be dispatched).
    """
    state = load(store)
    record = state['encounters'].get(str(int(encounter)))
    if record is None:
        return dict(ok=False, error=f'encounter {int(encounter)} has no breakthrough record yet')
    try:
        extra = int(runs)
    except (TypeError, ValueError):
        return dict(ok=False, error='the extension must be a whole number of runs')
    if extra <= 0:
        return dict(ok=False, error='the extension must be at least one run')
    budget = record.setdefault('budget', {})
    before = int(budget.get('cap') or RUNS_PER_ENCOUNTER_CAP)
    budget['cap'] = before + extra
    resumed = []
    if record.get('status') == 'paused_for_budget':
        record['status'] = 'nothing_supported_yet'
        resumed.append('encounter')
    for hypothesis in record.get('hypotheses') or []:
        if hypothesis.get('status') == 'paused_for_budget':
            hypothesis['status'] = 'proposed'
            resumed.append(hypothesis.get('id'))
    record['lastDecision'] = (f'campaign budget extended by {extra} runs by request '
                              f'({budget["cap"]} total); {_spent_runs(store, record)} already spent')
    record['updated'] = _now(now)
    save(store, state)
    return dict(ok=True, encounter=int(encounter), cap=budget['cap'], added=extra,
                previousCap=before, resumed=[item for item in resumed if item])


def _series(store, candidate, window=None):
    """`{seeds: chests}` for one candidate, optionally restricted to an experiment's own window.

    The window is a half-open ordinal range: `[start, end)` for one phase or, when `window` is a pair of
    pairs, a separate range per phase. Without it the comparison silently used every historical run a
    candidate ever had, which is not the experiment's evaluation window - and a parent with 300,000 runs
    would have had to be matched by the child's whole history instead of the assigned controls.
    """
    rows = []
    if window is None:
        rows = store.rows(candidate, 'discovery') + store.rows(candidate, 'validation')
    else:
        # The ordinal is a column of `run`, not a field inside the stored result, so the window filter has
        # to read it from the table itself: filtering `Store.rows()`'s dictionaries by a missing
        # `ordinal` matched nothing and silently produced empty experiments.
        for phase, span in (window.items() if isinstance(window, dict)
                            else {'validation': window}.items()):
            start, end = int(span[0]), int(span[1])
            for stored in store.db.execute(
                    'SELECT ordinal, result FROM run WHERE candidate=? AND phase=? ORDER BY ordinal',
                    (candidate, phase)):
                if start <= int(stored['ordinal']) < end:
                    try:
                        rows.append(json.loads(stored['result']))
                    except (TypeError, ValueError):
                        continue
    return strategy_probe.chest_series(rows)


def plan_window(plan):
    """The ordinal range one plan's comparison may read. `{phase: (start, end)}`."""
    if plan.get('kind') == 'confirmation':
        return {'validation': (int(plan['developmentBank']), int(plan['bank']))}
    return {'validation': (0, int(plan.get('bank') or 0))}


def _complete(store, plan):
    bank = int(plan.get('bank') or 0)
    for cid in [plan.get('parent')] + [arm.get('candidate') for arm in plan.get('arms') or []]:
        if cid and store.next_ordinal(cid, 'validation') < bank:
            return False
    return True


def _behaviour_of(store, candidate):
    """Median lightweight telemetry for one candidate's resident runs."""
    rows = []
    for row in store.rows(candidate, 'validation') + store.rows(candidate, 'discovery'):
        behaviour = _behaviour(row)
        if behaviour:
            rows.append(behaviour)
    out = {}
    for key in ('ticks', 'survivors', 'attacks', 'prizes', 'maxSimultaneousStoredCommands',
                'commandsTargetingBoss', 'commandsReleasedAfterDeath', 'postDeathPrizes',
                'bossDeathTick', 'storedCommandsTargetingBossAtDeath'):
        values = [row[key] for row in rows if isinstance(row.get(key), (int, float))]
        out[key] = _median(values)
    return out


def _incumbent(store, rows, bank=SCREEN_BANK):
    """The encounter's current incumbent: the best mean over builds measured to the screening bank."""
    per_candidate = {}
    for row in rows:
        if row['earned'] is None:
            continue
        per_candidate.setdefault(row['candidate'], []).append(row['earned'])
    best = None
    for cid, values in per_candidate.items():
        if len(values) < bank:
            continue
        mean = statistics.mean(values)
        if best is None or mean > best['mean']:
            best = dict(candidate=cid, mean=mean, n=len(values))
    return best


def _encounter_unit(scenario, axes=PRIMARY_AXES):
    """The unit the intervention moves: the one that carries every axis the hypothesis names."""
    try:
        return strategy_probe.default_probe_unit(scenario, list(axes)), None
    except strategy_probe.ProbeError as error:
        return None, str(error)


def start_screening(store, record, encounter, parent, scenario, hypothesis, stats=None,
                    directions=('low',), bank=SCREEN_BANK, now=None):
    """Create the controlled arms and authorise their bank. Returns the persisted plan or `None`.

    Arm A is the unchanged parent, which joins the same bank so every arm is compared on the *same*
    seed pairs. Nothing is run here: the scheduler hands the plan's seeds to workers through the
    existing banks, and the plan is closed by `evaluate_plan` when its last arm finishes.
    """
    unit, why = _encounter_unit(scenario)
    if unit is None:
        record['lastDecision'] = f'no unit carries {"/".join(PRIMARY_AXES)}: {why}'
        return None
    arms, refusal = plan_arms(scenario, unit, directions=directions)
    if arms is None:
        record['lastDecision'] = f'arms refused: {refusal}'
        return None
    if stats is None:
        from strategy_optimizer_adapter import stats as adapter_stats
        stats = adapter_stats
    proposal = int(record.get('proposals') or 0) + 1
    created = []
    for arm in arms:
        if arm['scenario'] is None:
            arm['candidate'] = parent
            continue
        label = (f'{LABEL_PREFIX}{hypothesis["id"]} · arm {arm["name"]} · {arm["role"]}')[:160]
        # `add_child` calls its `stats` argument with no arguments, so the scenario is bound here.
        cid, existed = store.add_child(arm['scenario'], label,
                                       lambda scenario=arm['scenario']: stats(scenario),
                                       parent, 'intervention', arm['axis'], str(arm['value']),
                                       SOURCE, proposal)
        arm['candidate'] = cid
        arm['existed'] = bool(existed)
        created.append(cid)
    plan = dict(id=f'e:{int(encounter)}:{proposal}', kind='screening', encounter=int(encounter),
                hypothesis=hypothesis['id'], parent=parent, parentLabel=hypothesis.get('parentLabel'),
                unit=unit, axes=list(PRIMARY_AXES), directions=list(directions),
                bank=int(bank), arms=arms, status='pending', created=_now(now),
                result=None, reason=None)
    # The per-participant evidence counts at authorisation, so what this plan *causes* can later be
    # told apart from the runs its control already had. Without it a heavily tested control made the
    # encounter look spent, and the plan's own cost could not be audited.
    plan['baseline'] = plan_watermark(store, plan)
    record['plans'].append(plan)
    record['proposals'] = proposal
    record['budget']['plans'] = int(record['budget'].get('plans') or 0) + 1
    record['budget']['experiments'] = int(record['budget'].get('experiments') or 0) + 1
    record['status'] = 'experiment_pending'
    record['lastDecision'] = (f'dispatching screening {plan["id"]} on {hypothesis["id"]}: arms '
                              f'{", ".join(arm["name"] for arm in arms[1:])} at bank {bank}, '
                              f'compared on the same seeds as the parent')
    record['updated'] = _now(now)
    return plan


def evaluate_plan(store, record, plan, now=None):
    """Close one finished plan: paired reward deltas plus the behaviour change per arm.

    Every number here is a *paired* difference against the parent over the seeds both actually
    measured, which is the only comparison the shared bank supports. A maximum is never used as the
    evidence: it is carried for nomination only.
    """
    window = plan_window(plan)
    reference = _series(store, plan['parent'], window)
    parent_behaviour = _behaviour_of(store, plan['parent'])
    arms = []
    for arm in plan['arms']:
        cid = arm.get('candidate')
        series = _series(store, cid, window)
        delta = strategy_probe.paired_delta(series, reference)
        values = list(series.values())
        behaviour = _behaviour_of(store, cid) if cid != plan['parent'] else parent_behaviour
        moved = {key: (None if behaviour.get(key) is None or parent_behaviour.get(key) is None
                       else behaviour[key]-parent_behaviour[key])
                 for key in behaviour}
        arms.append(dict(name=arm['name'], role=arm['role'], axis=arm.get('axis'),
                         value=arm.get('value'), candidate=cid,
                         n=len(values), mean=(statistics.mean(values) if values else None),
                         maximum=(max(values) if values else None),
                         threshold10=(sum(1 for value in values if value >= 10)/len(values)
                                      if values else None),
                         delta=delta, behaviour=behaviour, behaviourChange=moved,
                         rewardPromising=bool(delta and delta['lower'] is not None
                                              and delta['lower'] > 0
                                              and delta['n'] >= max(16, int(plan['bank'])//2))))
    # A real mean of 0 is a result, not a missing value: `or float('-inf')` turned the best zero-delta
    # arm into the worst one and could pick the wrong nominee.
    def arm_rank(arm):
        delta = arm.get('delta') or {}
        mean = delta.get('mean')
        return (mean if isinstance(mean, (int, float)) else float('-inf'),
                delta.get('n') or 0, arm['name'])
    result = dict(arms=arms, bank=int(plan['bank']), window=window,
                  best=(max((arm for arm in arms if arm['name'] != 'A'),
                            key=arm_rank,
                            default=None)))
    best = result['best']
    plan['result'] = result
    plan['status'] = 'complete'
    plan['evaluated'] = _now(now)
    promoted = [arm for arm in arms if arm['name'] != 'A' and arm['rewardPromising']]
    failed = [arm for arm in arms if arm['name'] != 'A' and arm['delta']
              and arm['delta']['upper'] is not None and arm['delta']['upper'] < 0]
    for hypothesis in record['hypotheses']:
        if hypothesis['id'] != plan['hypothesis']:
            continue
        hypothesis['attempted'].append(dict(plan=plan['id'], kind=plan['kind'],
                                            bank=plan['bank'], arms=[
                                                dict(name=arm['name'], role=arm['role'],
                                                     axis=arm['axis'], value=arm['value'],
                                                     delta=arm['delta'],
                                                     behaviourChange=arm['behaviourChange'],
                                                     rewardPromising=arm['rewardPromising'])
                                                for arm in arms[1:]]))
        if promoted:
            hypothesis['status'] = 'intervention_supported'
        elif failed and len(failed) == len(arms)-1:
            hypothesis['status'] = 'failed_in_tested_region'
        else:
            hypothesis['status'] = 'inconclusive'
    record['budget']['runs'] = _spent_runs(store, record)
    if promoted:
        record['status'] = 'promising_unconfirmed'
        record['lastDecision'] = (f'{plan["id"]} finished: {len(promoted)} arm(s) beat the parent on '
                                  f'paired fresh seeds ({", ".join(arm["name"] for arm in promoted)}); '
                                  f'nominate for independent confirmation')
        # The comparator is frozen *now*, from the declared criterion, together with the arm it will be
        # compared against: refreshing the incumbent just before the confirmation started let the
        # nominee itself become the comparator it was measured against.
        chosen = max(promoted, key=arm_rank)
        record['nominations'] = dict(record.get('nominations') or {})
        record['nominations'][plan['id']] = dict(
            plan=plan['id'], arm=chosen['name'], candidate=chosen['candidate'],
            axis=chosen['axis'], value=chosen['value'],
            comparator=(record.get('incumbent') or {}).get('candidate'),
            comparatorMean=(record.get('incumbent') or {}).get('mean'),
            at=_now(now))
        record['nomination'] = dict(record['nominations'][plan['id']])
    elif failed and len(failed) == len(arms)-1:
        record['status'] = 'no_improvement_in_tested_region'
        record['lastDecision'] = (f'{plan["id"]} finished: every arm was worse than the parent in the '
                                  f'tested region, so this hypothesis is deprioritised')
    else:
        record['lastDecision'] = (f'{plan["id"]} finished inconclusively at bank {plan["bank"]}; '
                                  f'the next batch narrows the measured region rather than guessing')
    record['updated'] = _now(now)
    return result, promoted


def start_confirmation(store, record, plan, arm, now=None,
                       bank=None, incumbent=None):
    """Freeze one nominated candidate and its comparison plan *before* any fresh seed is run.

    The development bank stays where it is and the extra ordinals above it are the confirmation window:
    they were never used to choose the hypothesis, the values or the candidate, and the incumbent is
    measured on exactly the same seeds.
    """
    # The comparator travels with the nomination, so "which build did this beat" is answered from the
    # frozen record rather than from whatever the live incumbent happens to be by the time confirmation
    # starts. A nominee is never its own comparator.
    nomination = (record.get('nominations') or {}).get(plan['id']) or record.get('nomination') or {}
    frozen = dict(incumbent or {})
    if not frozen.get('candidate'):
        comparator = nomination.get('comparator')
        if comparator:
            frozen = dict(candidate=comparator, mean=nomination.get('comparatorMean'))
    if not frozen.get('candidate'):
        frozen = dict(record.get('incumbent') or {})
    incumbent = frozen or None
    comparison_basis = 'live-incumbent'
    if not incumbent or incumbent.get('candidate') == arm.get('candidate'):
        # The refreshed incumbent *is* the nominee, which is the self-comparison this freeze exists to
        # prevent. Fall back to the build the experiment started from and say so: a nominee measured
        # against its own parent is a real, weaker claim, and it is labelled as one.
        parent = plan.get('parent')
        if not parent or parent == arm.get('candidate'):
            record['lastDecision'] = (f'{plan["id"]}: no candidate other than the nominee is measured '
                                      f'here, so there is nothing independent to confirm it against')
            return None
        incumbent = dict(candidate=parent, mean=(record.get('incumbent') or {}).get('mean'),
                         basis='experiment parent')
        comparison_basis = 'experiment-parent'
    development = int(plan['bank'])
    confirm_bank = int(bank or max(development*2, min(CONFIRM_BANK, MAX_BANK)))
    confirmation = dict(id=f'c:{plan["id"]}:{arm["name"]}', kind='confirmation',
                        hypothesis=plan['hypothesis'], encounter=int(plan['encounter']),
                        nominee=arm['candidate'], nomineeArm=arm['name'],
                        incumbent=incumbent['candidate'], developmentBank=development,
                        bank=confirm_bank,
                        arms=[dict(name='A', role='incumbent', candidate=incumbent['candidate']),
                              dict(name='N', role='nominee', candidate=arm['candidate'],
                                   axis=arm['axis'], value=arm['value'])],
                        comparisonBasis=comparison_basis,
                        frozenAt=_now(now), status='pending', result=None, reason=None,
                        screening=plan['id'])
    # A confirmation's watermark is taken over its own fresh window, so a control with a long history
    # is charged only for what the confirmation actually adds.
    confirmation['baseline'] = plan_watermark(store, confirmation)
    record['plans'].append(confirmation)
    record['budget']['confirmations'] = int(record['budget'].get('confirmations') or 0) + 1
    record['status'] = 'promising_unconfirmed'
    record['lastDecision'] = (f'froze {arm["candidate"][:10]} ({arm["role"]}) against the incumbent '
                              f'{incumbent["candidate"][:10]} at bank {confirm_bank}; the fresh window '
                              f'is ordinals {development}..{confirm_bank-1}, unused for selection')
    record['updated'] = _now(now)
    return confirmation


def evaluate_confirmation(store, record, plan, now=None):
    """Score a frozen confirmation on its fresh window only, and publish or refuse."""
    window = plan_window(plan)
    nominee = _series(store, plan['nominee'], window)
    incumbent = _series(store, plan['incumbent'], window)
    delta = strategy_probe.paired_delta(nominee, incumbent)
    # Means are means, and the window is stated as the ordinal range it is: the first version reported
    # `_median` in fields named `...Mean` over *all* available history, and called ordinals 64..511
    # "512 confirmation samples" when only 448 of them were ever new.
    nominee_values = list(nominee.values())
    incumbent_values = list(incumbent.values())
    requested = int(plan['bank']) - int(plan['developmentBank'])
    completed = len(nominee_values)
    unresolved = (requested - completed)
    supports = bool(delta and delta['n'] >= 16 and delta['lower'] is not None
                    and delta['lower'] > 0 and completed >= requested)
    incomplete = completed < requested
    plan['result'] = dict(
        delta=delta, paired=(delta or {}).get('n') or 0,
        requestedSamples=requested, completedSamples=completed, unresolvedSamples=unresolved,
        nomineeMean=(statistics.mean(nominee_values) if nominee_values else None),
        incumbentMean=(statistics.mean(incumbent_values) if incumbent_values else None),
        nomineeMedian=_median(nominee_values), incumbentMedian=_median(incumbent_values),
        nomineeThresholds={int(t): (sum(1 for value in nominee_values if value >= t)/len(nominee_values)
                                    if nominee_values else None) for t in DEFAULT_THRESHOLDS},
        incumbentThresholds={int(t): (sum(1 for value in incumbent_values if value >= t)
                                      /len(incumbent_values) if incumbent_values else None)
                             for t in DEFAULT_THRESHOLDS},
        comparator=plan['incumbent'],
        freshWindow=[int(plan['developmentBank']), int(plan['bank'])],
        supports=supports, incomplete=incomplete)
    plan['status'] = ('confirmed' if supports else
                      'inconclusive' if incomplete else 'not_promoted')
    plan['evaluated'] = _now(now)
    record['incumbent'] = record.get('incumbent')
    if supports:
        confirmed = [dict(entry) for entry in record.get('confirmed') or []]
        confirmed.append(dict(candidate=plan['nominee'], hypothesis=plan['hypothesis'],
                              delta=delta, at=_now(now), screening=plan['screening'],
                              confirmation=plan['id']))
        record['confirmed'] = confirmed[-8:]
        record['status'] = 'confirmed_improvement'
        record['lastDecision'] = (f'{plan["id"]}: the nominee beats the incumbent on the fresh window '
                                  f'(+{delta["mean"]:.2f} chests/battle, n={delta["n"]}); published as '
                                  f'a confirmed candidate and left in the population for refinement')
    else:
        record['status'] = ('experiment_pending' if incomplete
                            else 'no_improvement_in_tested_region')
        record['lastDecision'] = (
            f'{plan["id"]}: the confirmation window is incomplete '
            f'({completed}/{requested} of the assigned runs resolved), so no claim is made yet'
            if incomplete else
            f'{plan["id"]}: the nominee did not beat the frozen comparator '
            f'{plan["incumbent"][:10]} on the fresh window '
            f'({(delta or {}).get("mean")}, paired n={(delta or {}).get("paired") or 0}); not promoted')
    for hypothesis in record['hypotheses']:
        if hypothesis['id'] == plan['hypothesis'] and supports:
            hypothesis['status'] = 'intervention_supported'
            hypothesis['confirmed'] = plan['id']
    record['budget']['runs'] = _spent_runs(store, record)
    record['updated'] = _now(now)
    return plan['result']


# ---------------------------------------------------------------------------------------------
# The controller: what this stream does on each slow pass
# ---------------------------------------------------------------------------------------------

#: The evidence a published confirmation has to still show for its badge to stand. These are exactly
#: the fields the fixed `evaluate_confirmation` writes from the frozen window; a claim recorded before
#: it existed cannot produce them.
CLAIM_PROOF_FIELDS = ('comparator', 'freshWindow', 'requestedSamples', 'completedSamples',
                      'nomineeMean', 'incumbentMean')
#: Bumped when the acceptance rule for an existing claim changes, so the re-check runs once and then
#: never again for a library that has already been read under the new rule.
CLAIM_POLICY_VERSION = 1
#: The smallest paired sample count a claim may rest on, the same floor `evaluate_confirmation`
#: applies before it will call a difference positive.
MIN_PAIRED_SAMPLES = 16


def claim_status(record, claim):
    """`(status, reason)` for one published confirmation, judged on its own frozen evidence.

    This is deliberately a *re-read of the claim's own record*, not a re-run and not a check of the
    current nominee or incumbent: fixing the arithmetic must never turn an old unsupported claim into
    a supported one. A claim whose confirmation plan is missing, whose comparator was not frozen,
    whose window was never completed, or whose paired sample count is below the floor is marked
    `needs_revalidation` and withdrawn.
    """
    plan = next((entry for entry in record.get('plans') or []
                 if entry.get('id') == claim.get('confirmation')), None)
    if plan is None or plan.get('kind') != 'confirmation':
        return 'needs_revalidation', 'the confirmation plan that produced it is not in the record'
    if plan.get('status') != 'confirmed':
        return 'needs_revalidation', f"the confirmation's own status is {plan.get('status')!r}"
    incumbent, nominee = plan.get('incumbent'), plan.get('nominee')
    if not incumbent or incumbent == nominee:
        return 'needs_revalidation', 'the nominee was compared against itself'
    result = plan.get('result') or {}
    if result.get('comparator') != incumbent:
        return 'needs_revalidation', 'the comparator was not frozen with the claim'
    missing = [field for field in CLAIM_PROOF_FIELDS if result.get(field) is None]
    if missing:
        return 'needs_revalidation', 'the frozen window evidence is missing: ' + ', '.join(missing)
    if int(result.get('completedSamples') or 0) < int(result.get('requestedSamples') or 0):
        return 'needs_revalidation', 'the confirmation window was never completed'
    if int(result.get('paired') or 0) < MIN_PAIRED_SAMPLES:
        return 'needs_revalidation', f"only {int(result.get('paired') or 0)} paired samples"
    return 'supported', None


def migrate_claims(store, record):
    """Re-check every published confirmation once, and withdraw the ones that do not stand up.

    An old claim is not promoted just because the code that judged it is fixed now: it is re-read
    against the evidence it actually froze. Unsupported claims lose their badge and are removed from
    the published list - so the surface and anything choosing from it stop treating them as results -
    while the claim itself, its plan, its runs and its raw observations are all preserved under
    `claims` with the reason it was withdrawn. Idempotent: after the first pass the marker on the
    record means nothing is rewritten on restart.
    """
    if record.get('claimsChecked') == CLAIM_POLICY_VERSION:
        return record.get('claimsRevalidation')
    claims = []
    for claim in record.get('confirmed') or []:
        status, reason = claim_status(record, claim)
        entry = dict(claim)
        entry['status'] = status
        entry['reason'] = reason
        claims.append(entry)
    supported = [entry for entry in claims if entry['status'] == 'supported']
    withdrawn = [entry for entry in claims if entry['status'] != 'supported']
    record['claims'] = claims
    record['claimsChecked'] = CLAIM_POLICY_VERSION
    record['claimsRevalidation'] = dict(
        policy=CLAIM_POLICY_VERSION, checked=len(claims), supported=len(supported),
        needsRevalidation=len(withdrawn),
        withdrawn=[dict(candidate=entry.get('candidate'), hypothesis=entry.get('hypothesis'),
                        confirmation=entry.get('confirmation'), reason=entry.get('reason'))
                   for entry in withdrawn][:4])
    if withdrawn and not supported and record.get('status') == 'confirmed_improvement':
        record['status'] = 'claims_need_revalidation'
        record['lastDecision'] = (
            f'{len(withdrawn)} published confirmation(s) do not stand up to their own frozen evidence, '
            f'so the confirmed badge is withdrawn and they are removed from selection: '
            f'{withdrawn[0].get("reason")}')
    return record['claimsRevalidation']


def published_claims(record):
    """The confirmations the surface may still show as results: only the ones that stand up."""
    claims = record.get('claims')
    if claims is None:
        return record.get('confirmed') or []
    return [entry for entry in claims if entry.get('status') == 'supported']


def _finalise(store, record, now=None):
    """Close every finished plan. Runs at any share, including 0%: in-flight work may finish safely."""
    closed = []
    for plan in record.get('plans') or []:
        if plan.get('status') != 'pending' or not _complete(store, plan):
            continue
        if plan['kind'] == 'confirmation':
            evaluate_confirmation(store, record, plan, now=now)
        else:
            _result, promoted = evaluate_plan(store, record, plan, now=now)
            if promoted:
                record['nomination'] = dict((record.get('nominations') or {}).get(plan['id'])
                                            or dict(plan=plan['id'], arm=promoted[0]['name'],
                                                    candidate=promoted[0]['candidate'],
                                                    axis=promoted[0]['axis'],
                                                    value=promoted[0]['value']))
        closed.append(plan['id'])
    return closed


def _choose_encounter(store, state, focus_encounter):
    """Which fight to work on next: the focus, else the least-attempted resident one.

    Never "always the highest average" and never the lowest until it is exhausted: each encounter
    advances one experiment at a time, and a fight whose hypotheses have all failed is only revisited
    when a *new* hypothesis is proposed for it.
    """
    encounters = sorted(set(store.candidate_encounters().values()))
    if not encounters:
        return None
    if focus_encounter is not None and int(focus_encounter) in encounters:
        return int(focus_encounter)
    def rank(encounter):
        record = state['encounters'].get(str(int(encounter))) or {}
        budget = record.get('budget') or {}
        pending = any(plan.get('status') == 'pending' for plan in record.get('plans') or [])
        return (1 if pending else 0, int(budget.get('experiments') or 0), int(record.get('proposals') or 0),
                int(encounter))
    return min(encounters, key=rank)


def _analyse(store, record, encounter, thresholds=DEFAULT_THRESHOLDS, now=None):
    """Refresh the encounter's evidence view, archive, statistics, contrasts and hypotheses."""
    rows, coverage = evidence_view(store, encounter)
    previous_records = int((record.get('cursor') or {}).get('records') or 0)
    archive_state, bands = archive(record.get('archive'), rows)
    record['archive'] = archive_state
    record['coverage'] = coverage
    record['stats'] = outcome_stats(rows, thresholds)
    record['cursor'] = dict(records=len(rows), unchanged=len(rows) == previous_records)
    # Contrasts are computed for the builds that actually hold the extreme *and* the ordinary runs:
    # a poor-average build with one exceptional run is evidence, so the candidates are picked by
    # "has enough scored runs and a spread", not by their rank on any leaderboard.
    per_candidate = {}
    for row in rows:
        if row['earned'] is not None:
            per_candidate.setdefault(row['candidate'], []).append(row['earned'])
    # Population coverage, not a top-six-by-maximum cut. Three sources, in this order, bounded per pass
    # by ANALYSIS_PER_PASS and rotated by a persistent cursor so a per-pass limit can never permanently
    # exclude the same candidates:
    #   * every build holding a *productive-band* episode (a low-ranked build with one 31-chest run is
    #     exactly the evidence this student exists for, and a maximum-ranked cutoff hid it);
    #   * every build whose evidence changed since its last analysis (new information first);
    #   * the rest of the compatible population, in stable rotation.
    counts = {cid: len(values) for cid, values in per_candidate.items()}
    analyzable = {cid for cid, values in per_candidate.items()
                  if len(values) >= 8 and max(values) > min(values)}
    queue = dict(record.get('queue') or {})
    analysed = dict(queue.get('analysed') or {})
    order = [cid for cid in sorted(per_candidate) if cid in analyzable]
    productive = sorted((cid for cid in analyzable
                         if max(per_candidate[cid]) >= PRODUCTIVE_BAND_FLOOR),
                        key=lambda cid: (-max(per_candidate[cid]), cid))
    fresh = [cid for cid in order if analysed.get(cid) != counts[cid]]
    start = int(queue.get('index') or 0) % max(1, len(order))
    rotation = order[start:] + order[:start]
    picked, seen_pick = [], set()
    for cid in list(productive) + fresh + rotation:
        if cid in seen_pick or cid not in analyzable:
            continue
        seen_pick.add(cid)
        picked.append(cid)
        if len(picked) >= ANALYSIS_PER_PASS:
            break
    record['queue'] = dict(index=(start + len(picked)) % max(1, len(order)),
                           analysed={**analysed, **{cid: counts[cid] for cid in picked}},
                           population=len(order), lastAnalysed=picked)
    contrasts, contrast_coverage = [], []
    for cid in picked:
        contrast, coverage_entry = contrast_within(rows, cid)
        contrast_coverage.append(coverage_entry)
        if contrast:
            member = next((row for row in rows if row['candidate'] == cid), None)
            contrast['family'] = (member or {}).get('stream')
            contrasts.append(contrast)
    record['contrasts'] = [dict(candidate=contrast['candidate'], runs=contrast['runs'],
                                highMean=contrast['highMean'], lowMean=contrast['lowMean'],
                                grouping=contrast.get('grouping'),
                                separating=contrast['separating'],
                                features={key: value for key, value in contrast['features'].items()
                                          if value['separates'] or value['shift']},
                                family=contrast.get('family')) for contrast in contrasts]
    contrast_coverage_list = contrast_coverage
    stage, evidence = stage_bottleneck(contrasts)
    record['bottleneck'] = dict(stage=stage, **evidence) if stage else None
    incumbent = _incumbent(store, rows)
    if incumbent:
        record['incumbent'] = incumbent
    hypothesis = None
    if stage and contrasts:
        # The stage and the supporting contrast travel together: the contrast that established the
        # stage is the one the hypothesis is written from, so the two can never describe different builds.
        best = evidence.get('contrast') or max(contrasts, key=lambda c: len(c['separating']))
        scope_key = best.get('scope') or next((row['scope'] for row in rows), None)
        hypothesis = propose_hypothesis(encounter, scope_key, [best], stage, best['candidate'])
    if hypothesis is None:
        # Nothing separates yet. That is the normal state on a fight whose high outcomes are rare, so
        # the declared mechanism is recorded as a hypothesis to test rather than waiting for a
        # separation a 3%-frequency outcome cannot produce inside one 64-run bank.
        scored = [row for row in rows if row['earned'] is not None]
        if len(scored) >= MIN_RUNS_FOR_PRIOR:
            anchor = max(scored, key=lambda row: (row['earned'] or 0, row['candidate']))['candidate']
            hypothesis = prior_hypothesis(encounter, next((row['scope'] for row in rows), None),
                                          rows, anchor)
    if hypothesis is None:
        if coverage['records'] and not coverage['withTelemetry']:
            record['status'] = 'telemetry_missing'
            record['lastDecision'] = (f'{coverage["records"]} records for encounter {encounter} carry '
                                      f'no behaviour detail: no stage can be established from '
                                      f'aggregates, so nothing is proposed')
        else:
            record['status'] = 'nothing_supported_yet'
            record['lastDecision'] = (f'no stage separates productive from ordinary runs yet '
                                      f'({coverage["withTelemetry"]}/{coverage["records"]} records '
                                      f'carry telemetry)')
    else:
        hypothesis = _adopt_hypothesis(record, hypothesis)
        record['hypothesis'] = hypothesis['id']
    record['updated'] = _now(now)
    return dict(rows=rows, coverage=coverage, bands=bands, contrasts=contrasts,
                contrastCoverage=contrast_coverage_list, hypothesis=hypothesis,
                incumbent=incumbent)


def _axes_of(hypothesis):
    return tuple(sorted(entry.get('axis') for entry in hypothesis.get('controllable') or []))


def _adopt_hypothesis(record, hypothesis):
    """Register a hypothesis, or evolve the one already under investigation.

    Measurement can only *upgrade* an investigation: when the declared prior's stage finally separates,
    that is the same investigation with better evidence, not a second one. Keeping one evolving record
    per (encounter, axes) is what lets the surface show a single line - what it believes, what it tried,
    what that did - instead of a list of near-identical hypotheses with different anchors.
    """
    known = {entry['id']: entry for entry in record['hypotheses']}
    # One spelling for this status, and it is the one `HYPOTHESIS_STATUSES` declares: the first version
    # wrote `observational_supported` here and `observationally_supported` in the status list, so a
    # hypothesis could never be recognised as refreshing itself and every pass appended a new copy.
    open_statuses = ('proposed', 'observationally_supported', 'inconclusive', 'paused_for_budget')
    same = [entry for entry in record['hypotheses']
            if _axes_of(entry) == _axes_of(hypothesis)
            and entry.get('scope') == hypothesis.get('scope')
            and entry.get('parent') == hypothesis.get('parent')
            and not entry.get('confirmed')
            and entry.get('status') in open_statuses]
    if hypothesis['id'] in known:
        existing = known[hypothesis['id']]
    elif same:
        existing = max(same, key=lambda entry: len(entry.get('attempted') or []))
    else:
        record['hypotheses'].append(hypothesis)
        return hypothesis
    attempts = existing.get('attempted') or []
    status = existing.get('status')
    existing.update({key: hypothesis[key] for key in
                     ('features', 'supporting', 'contrasting', 'statement', 'evidence',
                      'controllable', 'bottleneck', 'family', 'scope', 'basis', 'parent')})
    existing['attempted'] = attempts
    # An investigation that has already been tested keeps the more advanced status: new evidence
    # upgrades the *description*, never the conclusion.
    if attempts and status:
        existing['status'] = status
    return existing


def advance(store, share, workers=1, focus_encounter=None, stats=None, now=None,
            thresholds=DEFAULT_THRESHOLDS):
    """One slow pass of the stream. Returns a summary of what it decided. Never runs a battle.

    At a zero share the only work is closing plans whose seeds were already authorised - the honest
    meaning of "existing in-flight work may finish safely". Nothing is proposed, dispatched or analysed.
    """
    state = load(store)
    changed = False
    closed = []
    for key, record in state['encounters'].items():
        done = _finalise(store, record, now=now)
        if done:
            closed += done
            changed = True
        # The claim re-check runs here rather than inside `_finalise` so it happens on the same pass as
        # the plan closures that could have produced a new claim, and so it runs at any share: an old
        # badge has to come down whether or not the stream is switched on.
        was_unchecked = record.get('claimsChecked') != CLAIM_POLICY_VERSION
        migrate_claims(store, record)
        if was_unchecked:
            changed = True
    enabled = float(share or 0.0) > 0
    summary = dict(enabled=enabled, share=float(share or 0.0), closed=closed, started=None,
                   encounter=None, decision=None)
    if not enabled:
        summary['decision'] = ('share is 0%: no proposals, no replays and no experiments are launched; '
                               f'{len(closed)} in-flight plan(s) were closed')
        if changed:
            save(store, state)
        return summary
    encounter = _choose_encounter(store, state, focus_encounter)
    if encounter is None:
        summary['decision'] = 'the library holds no encounter to work on'
        return summary
    record = encounter_state(state, encounter)
    summary['encounter'] = encounter
    spent = _spent_runs(store, record)
    record['budget']['runs'] = spent
    cap = int(record.get('budget', {}).get('cap') or RUNS_PER_ENCOUNTER_CAP)
    if spent >= cap:
        record['status'] = 'paused_for_budget'
        record['lastDecision'] = (f'this encounter\'s campaign budget is spent ({spent}/{cap} runs); '
                                  f'hypotheses are paused, not declared impossible, and another '
                                  f'encounter takes the next pass')
        for hypothesis in record['hypotheses']:
            if hypothesis['status'] in ('proposed', 'inconclusive'):
                hypothesis['status'] = 'paused_for_budget'
        # Rotate to another encounter rather than returning: an encounter that has spent its budget must
        # not be re-chosen as "least attempted" forever while other fights get nothing.
        alternatives = [cid for cid in sorted(state['encounters'], key=int)
                        if cid != str(encounter)
                        and not any(plan.get('status') == 'pending'
                                    for plan in (state['encounters'][cid].get('plans') or []))]
        if alternatives:
            encounter = int(alternatives[0])
            record = encounter_state(state, encounter)
            summary['encounter'] = encounter
            spent = _spent_runs(store, record)
            record['budget']['runs'] = spent
        if spent >= int(record.get('budget', {}).get('cap') or RUNS_PER_ENCOUNTER_CAP):
            save(store, state)
            summary['decision'] = record['lastDecision']
            return summary
    pending = [plan for plan in record['plans'] if plan.get('status') == 'pending']
    if pending:
        # One plan in flight per encounter; more than that and the comparison stops being controlled.
        summary['decision'] = f'{len(pending)} plan(s) already in flight for encounter {encounter}'
        if changed:
            save(store, state)
        return summary
    analysis = _analyse(store, record, encounter, thresholds=thresholds, now=now)
    changed = True
    # Per-nomination processing: a confirmation that completed must not stop the *next* nominee, and
    # completing one experiment must leave the encounter able to run more.
    nomination = record.get('nomination')
    if nomination and not (record.get('confirmations_started') or {}).get(nomination['plan']):
        plan = next((plan for plan in record['plans']
                     if plan.get('id') == nomination['plan']), None)
        arm = next((arm for arm in ((plan or {}).get('result') or {}).get('arms') or []
                    if arm['name'] == nomination['arm']), None)
        if plan and arm:
            confirmation = start_confirmation(store, record, plan, arm, now=now,
                                              incumbent=record.get('incumbent'))
            if confirmation:
                record['confirmations_started'] = dict(record.get('confirmations_started') or {},
                                                       **{nomination['plan']: confirmation['id']})
                summary['started'] = confirmation['id']
    hypothesis = analysis['hypothesis']
    in_flight = len([entry for entry in record['plans'] if entry.get('status') == 'pending'])
    if summary['started'] is None and hypothesis is not None and in_flight < MAX_PLANS_PER_ENCOUNTER:
        attempts = [entry for entry in hypothesis.get('attempted') or []]
        if len(attempts) >= MAX_ATTEMPTS_PER_HYPOTHESIS:
            # The tested region has an answer, so this hypothesis is *paused* rather than retired: new
            # evidence can reopen it, and the encounter keeps working on anything else it has.
            if hypothesis['status'] in ('proposed', 'inconclusive'):
                hypothesis['status'] = 'paused_for_budget'
            record['status'] = ('no_improvement_in_tested_region' if record.get('conclusive')
                                else 'nothing_supported_yet')
            record['lastDecision'] = (f'{hypothesis["id"]} has been tested in both directions in the '
                                      f'measured region; it is paused, not retired - the encounter '
                                      f'takes the next hypothesis or the next fight')
        else:
            directions = ('low',) if not attempts else ('high',)
            parent = hypothesis['parent']
            scenario = store.scenario(parent)
            if scenario is not None:
                hypothesis['parentLabel'] = (store.db.execute(
                    'SELECT label FROM candidate WHERE id=?', (parent,)).fetchone() or [''])[0]
                plan = start_screening(store, record, encounter, parent, scenario, hypothesis,
                                       stats=stats, directions=directions, now=now)
                if plan:
                    summary['started'] = plan['id']
    summary['decision'] = record.get('lastDecision')
    summary['status'] = record.get('status')
    save(store, state)
    return summary


# ---------------------------------------------------------------------------------------------
# What the surface shows: whether it is learning anything useful
# ---------------------------------------------------------------------------------------------

#: Examples published with every status snapshot. The full archive is fetched on demand: a poll that
#: carried every retained example would cost more than the analysis it describes.
PUBLISHED_EXAMPLES = 6


def _plan_summary(plan):
    if not plan:
        return None
    result = plan.get('result') or {}
    arms = []
    for arm in result.get('arms') or plan.get('arms') or []:
        arms.append(dict(name=arm.get('name'), role=arm.get('role'), axis=arm.get('axis'),
                         value=arm.get('value'), candidate=arm.get('candidate'),
                         n=arm.get('n'), mean=arm.get('mean'), maximum=arm.get('maximum'),
                         delta=arm.get('delta'), behaviourChange=arm.get('behaviourChange'),
                         rewardPromising=arm.get('rewardPromising')))
    return dict(id=plan.get('id'), kind=plan.get('kind'), status=plan.get('status'),
                hypothesis=plan.get('hypothesis'), bank=plan.get('bank'),
                developmentBank=plan.get('developmentBank'), arms=arms,
                created=plan.get('created'), evaluated=plan.get('evaluated'),
                nominee=plan.get('nominee'), incumbent=plan.get('incumbent'),
                reason=plan.get('reason'))


def status(store):
    """The bounded status payload: one entry per encounter this stream has touched."""
    state = load(store)
    out = dict(label=LABEL, source=SOURCE, version=STATE_VERSION,
               bands=[definition['label'] for definition in _bands()],
               thresholds=list(DEFAULT_THRESHOLDS),
               cap=RUNS_PER_ENCOUNTER_CAP, bank=SCREEN_BANK, confirmBank=CONFIRM_BANK,
               # What the cap bounds, stated once at the top as well as per encounter, so a reader
               # cannot mistake it for a per-experiment or library-wide limit.
               campaignScope='one encounter',
               axes=list(PRIMARY_AXES),
               axisParameters={axis: strategy_probe.axis_info(axis).get('parameter')
                               for axis in PRIMARY_AXES},
               spentRuns=_spent_runs(store), encounters={})
    for key, record in sorted(state['encounters'].items(), key=lambda item: int(item[0])):
        plans = record.get('plans') or []
        pending = next((plan for plan in plans if plan.get('status') == 'pending'), None)
        latest = next((plan for plan in reversed(plans) if plan.get('status') != 'pending'), None)
        examples = (record.get('archive') or {}).get('examples') or []
        hypotheses = record.get('hypotheses') or []
        out['encounters'][key] = dict(
            label=encounter_label(key), status=record.get('status'), updated=record.get('updated'),
            lastDecision=record.get('lastDecision'),
            stats=record.get('stats'), coverage=record.get('coverage'),
            bands=(record.get('archive') or {}).get('bands') or {},
            examples=examples[:PUBLISHED_EXAMPLES],
            exampleCount=len(examples),
            bottleneck=record.get('bottleneck'), contrasts=record.get('contrasts') or [],
            incumbent=record.get('incumbent'),
            # Only confirmations that still stand up to their own frozen evidence. A withdrawn claim
            # is not published as a result; it stays in the record with its reason.
            confirmed=published_claims(record),
            claimsRevalidation=record.get('claimsRevalidation'),
            budget=record.get('budget') or {},
            campaign=campaign(store, record),
            hypothesis=next((entry for entry in hypotheses
                             if entry['id'] == record.get('hypothesis')), None),
            hypothesisStatuses={entry['id']: entry.get('status') for entry in hypotheses},
            plan=_plan_summary(pending or latest),
            inFlight=bool(pending))
    return out


def details(store, encounter):
    """The full record of one encounter, fetched on demand rather than published on every poll."""
    state = load(store)
    record = state['encounters'].get(str(int(encounter)))
    if record is None:
        return None
    return dict(encounter=int(encounter), status=record.get('status'),
                label=encounter_label(encounter),
                coverage=record.get('coverage'), stats=record.get('stats'),
                archive=record.get('archive'), bottleneck=record.get('bottleneck'),
                contrasts=record.get('contrasts') or [],
                hypotheses=record.get('hypotheses') or [],
                plans=[_plan_summary(plan) for plan in record.get('plans') or []],
                confirmed=published_claims(record),
                claims=record.get('claims'), claimsRevalidation=record.get('claimsRevalidation'),
                budget=record.get('budget') or {},
                incumbent=record.get('incumbent'), lastDecision=record.get('lastDecision'),
                updated=record.get('updated'))
