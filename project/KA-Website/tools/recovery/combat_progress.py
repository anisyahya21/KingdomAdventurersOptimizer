"""Stored-attack progress metrics: the causal chain from setup to chests, measured exactly.

Part A traced the mechanism on a real 45-chest run
(`RE-evidence/20260922-objective-redesign/stored-attack-trace-earned19.json`):

    the rival leader's HP reaches 0
      -> it enters Leaving (state 8) and one prize is queued
      -> a *stored attack* that is still pointed at it is released
      -> it re-enters Damaging (state 6) and then Leaving again
      -> one more prize, once per re-entry, for as long as stored attacks keep landing

A *stored attack* is one of the two structures the native code keeps after the fighter stops acting:

  * a persistent skill command (opcode 29) in `unit['commands']` - it carries its own target, clock and
    use index, is written by `UpdateCharging`/the Counter path (`SharedControllers.enqueue`) and is
    released one head at a time by `execute_shared_skill_commands` every fighters phase. It is not the
    same thing as `metrics.attackAttempts`: the command is the *stored* action, the attack attempts are
    the hits its releases produce.
  * the stored normal-attack target `long_board[16]` (native BKL:16), written by `UpdateCharging` and
    read by `UpdateAttacking`; `ExitAttacking` does not clear it, so it survives a death.

Metrics are sampled once per tick at the same seam the reward-entitlement observer uses (after the
fighters phase), so Python and the native kernel observe the identical state. `release`/`enqueue`
counters come from the two mutation sites themselves - a queue *clear* (knockdown, teardown) is not a
release and is never counted.

The same observer also samples **MP telemetry** for the declared trigger units (the configured DPS
and healer) and latches the **live `<=3%` Holy Herb policy**. Both are experiment configuration, not
recovered game facts: `combat_scenario` validates `holyHerbTriggerUnits` and `holyHerbMaxUses`, and a
scenario that declares neither costs nothing here - the sample returns immediately and the policy can
never latch. Sampling deliberately rides this observer rather than adding a second per-tick loop.
"""


#: Input phases, indexed by the code the native report publishes (`before_fighters` / `after_fighters`).
PHASES = ('before_fighters', 'after_fighters')
#: The reverse mapping, so a reference-side dispatch can record the same numeric phase the kernel does.
PHASE_CODES = {name: index for index, name in enumerate(PHASES)}
#: The recovery parameter the Holy Herb restores (`combat_consumables.RECOVERY_PARAMETERS[2]`).
MP_PARAMETER = 11
#: `state::KA_HERB_TRIGGER_PERCENT`: the live policy's threshold, as a percentage of the unit's OWN
#: maximum MP - the engine's own `Param.GetRate`, never a re-derived ratio.
HERB_TRIGGER_PERCENT = 3
#: `state::KA_MAX_MP_WATCH`: the DPS and the healer, in declaration order.
MP_WATCH_LIMIT = 2
#: `state::KA_MAX_HERB_USES`: the explicit dispatch log's capacity.
HERB_USE_LIMIT = 16


def blank_progress():
    """The 18 published counters, all zero/absent. Field order is the published order."""
    return dict(
        bossIdentity=-1,
        bossDeathTick=-1,
        bossLeavingTick=-1,
        storedCommandsAtDeath=0,
        storedCommandsTargetingBossAtDeath=0,
        storedTargetHoldersAtDeath=0,
        commandsTargetingBoss=0,
        commandsTargetingBossReleased=0,
        maxSimultaneousStoredCommands=0,
        maxSimultaneousCommandsTargetingBoss=0,
        storedTargetHoldersPeak=0,
        postDeathBossReentries=0,
        postDeathBossLeavings=0,
        postDeathPrizes=0,
        commandsReleasedAfterDeath=0,
        commandsReleasedAfterDeathTargetingBoss=0,
        firstPostDeathCommandReleaseTick=-1,
        lastPostDeathCommandReleaseTick=-1,
    )


def boss_identity(specs):
    """The rival leader this battle is about, from the roster (known before the first tick)."""
    for identity in sorted(specs):
        if specs[identity].get('boss', False):
            return identity
    return -1


def mp_metrics(names, identities, minimums, percents, lows, ticks, phases, zeros):
    """The compact MP block, in the declared trigger order (`mpMetrics`).

    One entry per declared trigger unit. The kernel publishes exactly these values in its report and
    `strategy_optimizer_native._native_compact` passes them through unchanged, so the two backends
    are compared as the same object rather than as two similar ones.

    `minimumMp` / `minimumMpPercent` are `-1` while nothing has been sampled; a genuine minimum of
    `0` is a reading, never the sentinel. The percentage is the engine's own `world.rate(unit, 11)`,
    which is what `Param.GetRate` returns - not a separately re-derived ratio.
    """
    return [dict(name=names[slot] if slot < len(names) else None,
                 identity=int(identities[slot]),
                 minimumMp=int(minimums[slot]),
                 minimumMpPercent=int(percents[slot]),
                 reachedLowMp=bool(lows[slot]),
                 firstLowMpTick=int(ticks[slot]),
                 firstLowMpPhase=(PHASES[phases[slot]] if phases[slot] >= 0 else None),
                 reachedZero=bool(zeros[slot]))
            for slot in range(len(identities))]


def herb_metrics(start_stock, remaining, max_uses, use_count, log):
    """The explicit Holy Herb block (`herbMetrics`).

    `resourceUses` counts successful Holy Herb *and* battle-item uses together, so it cannot say
    which consumable spent a charge. This block carries the declared stock and cap, the successful
    use count, and one row per dispatch (tick, phase, and the trigger slot that authorised it - `-1`
    when the use came from a prescribed `{tick, phase}` input rather than the live policy).
    """
    return dict(startingStock=int(start_stock), remainingStock=int(remaining),
                maxUses=int(max_uses), useCount=int(use_count),
                uses=[dict(tick=int(tick), phase=PHASES[phase] if phase >= 0 else None,
                           source=(int(source) if source >= 0 else None), used=bool(used))
                      for tick, phase, source, used in log])


class ProgressWatch:
    """Read-only observer of the stored-attack chain; never mutates the engine.

    `observe(world)` is called once per tick after the fighters phase, `enqueue`/`release` from the
    two genuine mutation sites. `report()` returns the published counters. The boss identity comes
    from the roster rather than from the first sample, so a command enqueued during the very first
    fighters phase is still attributed correctly.
    """

    def __init__(self, boss, watch=(), watch_names=(), holy_herb_max_uses=0,
                 holy_herb_start_stock=0, rows=None):
        self.fields = blank_progress()
        self.fields['bossIdentity'] = boss
        # The skill-row table (`id -> row`) so the death-snapshot future-hit mass can read
        # `skill['count']`. The sandbox passes its own `ROWS`; `None` leaves the mass unavailable
        # rather than re-deriving a skill row here.
        self.rows = rows
        # v3 stored-attack mass: latched once, at the first after-fighters sample that sees the boss
        # dead, independent of whether the death was latched in-phase by a release.
        self.future_hits_captured = False
        self.future_hits_at_death = None
        self.future_include_executing = None
        # v3 occupancy (per-tick samples, never events) and the post-death Damaging reset count.
        self.targetable_ticks = 0
        self.using_skill_ticks = 0
        self.boss_damaging_resets = 0
        self.prizes_at_death = None
        self.previous_boss_state = None
        # Set when the death is first seen during a fighters phase rather than at the sample that
        # follows it; the sample then keeps this latch instead of writing its own later value.
        self.death_latched_in_phase = False
        # ---- MP telemetry and the live `<=3%` Holy Herb policy -------------------------------------
        # `watch` is the declared trigger list as identities (the configured DPS and healer), in
        # declaration order; `watch_names` is the same list as declared names, for the report. Both
        # are experiment configuration, never recovered game data.
        self.watch = [int(identity) for identity in watch][:MP_WATCH_LIMIT]
        self.watch_names = list(watch_names)[:MP_WATCH_LIMIT]
        self.holy_herb_max_uses = int(holy_herb_max_uses)
        self.holy_herb_start_stock = int(holy_herb_start_stock)
        # `1` while the unit may authorise a new use; cleared when it does, so a unit that simply
        # stays below the threshold cannot fire repeatedly.
        self.armed = [True]*len(self.watch)
        # `None` = nothing latched, else the trigger slot whose crossing latched it.
        self.pending_herb = None
        # Explicit Holy Herb evidence: one row per dispatch, `(tick, phase, source, used)`.
        self.herb_use_log = []
        self.herb_uses_ok = 0
        self.mp_min = [None]*len(self.watch)
        self.mp_min_percent = [None]*len(self.watch)
        self.mp_low = [False]*len(self.watch)
        self.mp_first_low_tick = [-1]*len(self.watch)
        self.mp_first_low_phase = [-1]*len(self.watch)
        self.mp_zero = [False]*len(self.watch)

    # ---- MP telemetry and the live policy ----------------------------------------------------
    def note_herb_use(self, tick, phase, source, used):
        """One explicit Holy Herb dispatch, from either the live policy or a prescribed input."""
        if len(self.herb_use_log) < HERB_USE_LIMIT:
            self.herb_use_log.append((tick, phase, -1 if source is None else int(source), bool(used)))
        if used:
            self.herb_uses_ok += 1

    def take_pending_herb(self):
        """`(slot, None)` when the live policy latched a use; `(None, slot)` when it did not.

        The latch is consumed by the *existing* item-action seam, so the automatic use travels the
        same recovered `use_battle_holy_herb` path a prescribed `{tick, phase}` event does.
        """
        slot = self.pending_herb
        self.pending_herb = None
        return slot

    def _observe_mp(self, world, stock):
        """One MP sample per declared trigger unit, plus the policy's latch."""
        if not self.watch:
            return
        may_spend = (self.holy_herb_max_uses > 0 and int(stock) > 0
                     and self.herb_uses_ok < self.holy_herb_max_uses)
        for slot, identity in enumerate(self.watch):
            if identity not in world.units:
                continue
            value = world.value(identity, MP_PARAMETER)
            percent = world.rate(identity, MP_PARAMETER)
            state = world.units[identity]['board'][5]
            if self.mp_min[slot] is None or value < self.mp_min[slot]:
                self.mp_min[slot] = value
            if self.mp_min_percent[slot] is None or percent < self.mp_min_percent[slot]:
                self.mp_min_percent[slot] = percent
            if value == 0:
                self.mp_zero[slot] = True
            if percent <= HERB_TRIGGER_PERCENT:
                if not self.mp_low[slot]:
                    self.mp_low[slot] = True
                    self.mp_first_low_tick[slot] = world.tick
                    # The sample runs at the after-fighters seam, which is the phase this observer is
                    # called with; recorded verbatim rather than guessed.
                    self.mp_first_low_phase[slot] = 1
                if may_spend and state not in (7, 8) and self.armed[slot]:
                    self.armed[slot] = False
                    if self.pending_herb is None:
                        self.pending_herb = slot
            else:
                self.armed[slot] = True

    def mp_report(self):
        """The compact `mpMetrics` block, in declaration order."""
        return mp_metrics(
            self.watch_names, self.watch,
            [-1 if value is None else value for value in self.mp_min],
            [-1 if value is None else value for value in self.mp_min_percent],
            self.mp_low, self.mp_first_low_tick, self.mp_first_low_phase, self.mp_zero)

    def herb_report(self, stock):
        """The compact `herbMetrics` block, from this watch's own dispatch log."""
        return herb_metrics(self.holy_herb_start_stock, stock, self.holy_herb_max_uses,
                            self.herb_uses_ok, self.herb_use_log)

    def counter_report(self):
        """The v3 per-tick readings, merged into the event-site counter dict by the sandbox."""
        return dict(
            bossFutureHitsAtDeath=(-1 if self.future_hits_at_death is None
                                   else int(self.future_hits_at_death)),
            bossFutureIncludeExecuting=(-1 if self.future_include_executing is None
                                        else int(self.future_include_executing)),
            targetableTicks=int(self.targetable_ticks),
            usingSkillTicks=int(self.using_skill_ticks),
            bossDamagingResets=int(self.boss_damaging_resets))

    # ---- the two real mutation sites ---------------------------------------------------------
    def enqueue(self, identity, target):
        """One stored command entered a fighter's queue (`SharedControllers.enqueue`)."""
        if self.fields['bossIdentity'] >= 0 and target == self.fields['bossIdentity']:
            self.fields['commandsTargetingBoss'] += 1

    def release(self, identity, command, tick, boss_hp=None):
        """A stored command's head completed and was removed - never a queue clear.

        `boss_hp` is the rival leader's HP *at the moment of the release* (the engine reads it; the
        observer never touches the world itself). That probe is what makes the ordering explicit: the
        per-tick sample only runs after the whole fighters phase, so a command released later in the
        SAME phase as the killing blow would otherwise look like it preceded the death. With the probe
        it is counted as post-death, and a release that happened earlier in that same phase (boss still
        alive at that instant) is not.
        """
        fields = self.fields
        boss = fields['bossIdentity']
        if command.get('target') == boss:
            fields['commandsTargetingBossReleased'] += 1
        if boss >= 0 and boss_hp == 0 and fields['bossDeathTick'] < 0:
            fields['bossDeathTick'] = tick
            self.death_latched_in_phase = True
        dead = fields['bossDeathTick'] >= 0 and (
            tick > fields['bossDeathTick']
            or (boss_hp == 0 and tick == fields['bossDeathTick']))
        if not dead:
            return
        fields['commandsReleasedAfterDeath'] += 1
        if command.get('target') == boss:
            fields['commandsReleasedAfterDeathTargetingBoss'] += 1
        if fields['firstPostDeathCommandReleaseTick'] < 0:
            fields['firstPostDeathCommandReleaseTick'] = tick
        fields['lastPostDeathCommandReleaseTick'] = tick

    # ---- the per-tick sample ----------------------------------------------------------------
    def observe(self, world, stock=0):
        fields = self.fields
        tick = world.tick
        # MP telemetry and the policy's latch need no rival leader, so they run before the boss guard:
        # a fight whose boss identity could not be resolved still reports MP exactly as the kernel's
        # own observer does.
        self._observe_mp(world, stock)
        boss = fields['bossIdentity']
        if boss < 0:
            return
        hp = world.value(boss, 10)
        state = world.units[boss]['board'][5]
        if hp == 0 and fields['bossDeathTick'] < 0:
            fields['bossDeathTick'] = tick
            fields['storedCommandsAtDeath'] = self._queued(world, boss)[0]
            fields['storedCommandsTargetingBossAtDeath'] = self._queued(world, boss)[1]
            fields['storedTargetHoldersAtDeath'] = self._stored_holders(world, boss)
        if hp == 0:
            # v3: latch the future-hit mass at the first after-fighters sample that sees the death,
            # even when a release latched the death earlier in the same phase.
            self._latch_future_hits(world, boss)
        if hp == 0 and self.prizes_at_death is None:
            # Latched whoever found the death first (this sample, or a release earlier in the phase),
            # so `postDeathPrizes` keeps counting from the death even when the release latched it.
            self.prizes_at_death = len(world.prizes)
        if state == 8 and fields['bossLeavingTick'] < 0:
            fields['bossLeavingTick'] = tick
        if fields['bossDeathTick'] >= 0 and tick > fields['bossDeathTick']:
            # A re-entry is a *change* into the state, exactly as the native observer sees it: the
            # state handlers keep 6 and 8 for at least seven frames, so no transition can hide
            # between two consecutive samples.
            if state == 6 and self.previous_boss_state != 6:
                fields['postDeathBossReentries'] += 1
            if state == 8 and self.previous_boss_state != 8:
                fields['postDeathBossLeavings'] += 1
            if self.previous_boss_state == 6 and state != 6:
                # A Damaging reset: the boss left the Damaging state after its first death.
                self.boss_damaging_resets += 1
        self.previous_boss_state = state
        # Occupancy is a per-tick sample, never an event: the after-fighters ticks on which the boss
        # was a normal-attack target (HP > 0, state in 1/3/4/6) or was in UsingSkill (state 5).
        if hp > 0 and state in (1, 3, 4, 6):
            self.targetable_ticks += 1
        if state == 5:
            self.using_skill_ticks += 1
        total, targeting = self._queued(world, boss)
        holders = self._stored_holders(world, boss)
        fields['maxSimultaneousStoredCommands'] = max(fields['maxSimultaneousStoredCommands'], total)
        fields['maxSimultaneousCommandsTargetingBoss'] = max(
            fields['maxSimultaneousCommandsTargetingBoss'], targeting)
        fields['storedTargetHoldersPeak'] = max(fields['storedTargetHoldersPeak'], holders)
        if self.prizes_at_death is not None:
            fields['postDeathPrizes'] = len(world.prizes)-self.prizes_at_death

    def _future_hits(self, world, boss):
        """`(remaining hit attempts, includes the currently executing hit)` for the boss commands.

        The mass is the sum over pending opcode-29 commands targeting the boss of
        `max(0, skill.count - use_index)` - exactly the hits the command has NOT fired yet.
        `use_index` advances at the instant a hit fires, so a hit already resolved (including the
        hit currently executing at the sample) is excluded; the next hit of a command that has
        begun (`tick > 0`) is therefore included, which is what the second value reports. `None`
        means the caller supplied no skill-row table, so the mass is unavailable rather than zero.
        """
        if self.rows is None:
            return None, None
        hits = 0
        executing = False
        for unit in world.units.values():
            for command in unit['commands']:
                if command.get('target') != boss:
                    continue
                row = self.rows.get(command.get('skill'))
                if row is None:
                    continue
                remaining = max(0, int(row['count']) - int(command.get('use_index') or 0))
                hits += remaining
                if remaining > 0 and int(command.get('tick') or 0) > 0:
                    executing = True
        return hits, executing

    def _latch_future_hits(self, world, boss):
        if self.future_hits_captured:
            return
        mass, executing = self._future_hits(world, boss)
        if mass is None:
            return
        self.future_hits_at_death = mass
        self.future_include_executing = executing
        self.future_hits_captured = True

    @staticmethod
    def _queued(world, boss):
        """(every queued command across all fighters, those whose stored target is the boss)."""
        total = targeting = 0
        for unit in world.units.values():
            total += len(unit['commands'])
            targeting += sum(1 for command in unit['commands'] if command.get('target') == boss)
        return total, targeting

    @staticmethod
    def _stored_holders(world, boss):
        """Fighters whose stored normal-attack target (native BKL:16) is the boss."""
        return sum(1 for unit in world.units.values() if unit['long_board'].get(16) == boss)

    def report(self):
        return dict(self.fields)


# ---------------------------------------------------------------------------------------------
# Compact encounter telemetry (encounter-aware search), additively published after the digest.
#
# Every field below is either read from a counter the engine maintains at a genuine mutation site,
# or reported as `None` with its reason in `unavailable`. A metric the bounded task state cannot
# observe is never fabricated as a zero. The block is opt-in: it defaults `off` at every entry
# point (`simulate` / `simulate_compact` / `_compact`) and is attached AFTER the compact digest,
# so a telemetry-enabled run produces byte-identical `digest` and legacy metrics.
# ---------------------------------------------------------------------------------------------

#: Reason a compact counter cannot be produced. `None` + one of these in `unavailable` replaces a
#: fabricated zero. Only genuinely unobserved quantities remain here; the event-site counters
#: (own death/Leaving, enemy roll/resolved, counter checks/enqueues, boss post-death) are real.
TELEMETRY_UNAVAILABLE = {
    'ownFirstDeathTick': 'no event-site counter was supplied at this seam',
    'futureAttempts': 'no stored-attack observer with a skill-row table was supplied at this seam',
    'landedHits': 'the bounded observer retains no per-target future landing counter',
    'expectedLandedHits': 'no per-hit landing probability model is published at this seam',
    'postDeathAttemptedHits': 'no event-site post-death attempt counter was supplied at this seam',
    'postDeathLandedHits': 'no event-site post-death landed-hit counter was supplied at this seam',
    'hitGapTicks': 'no encounter counter observer was supplied at this seam',
    'resetCount': 'no progress observer was supplied at this seam',
    'enemyResolvedAttacks': 'no event-site enemy counter was supplied at this seam',
    'enemyHits': 'no event-site enemy counter was supplied at this seam',
    'enemyMisses': 'no event-site enemy counter was supplied at this seam',
    'counterChecks': 'no event-site counter-check counter was supplied at this seam',
    'counterEnqueues': 'no event-site counter-enqueue counter was supplied at this seam',
    'bossCounters': 'no event-site boss counter was supplied at this seam',
    'itemUseCount': 'no separate battle-item use count was supplied at this seam',
    'targetableOccupancy': 'no progress observer was supplied at this seam',
    'usingSkillOccupancy': 'no progress observer was supplied at this seam',
}

#: The telemetry schema version. Bump when the block's shape changes so a reader can tell an old
#: diagnostic row from a new one without re-deriving it. v2 publishes the real event-site counters
#: (true own death vs first Leaving, enemy roll vs resolved result, boss post-death) that were
#: `null` + reason in v1. v3 adds the death-snapshot future-hit mass (distinct from the remaining
#: command count), the compact post-death actual-hit gap summary, Damaging-reset/Leaving/re-entry
#: counters, boss-access first-command/first-attempt ticks and per-tick targetable/UsingSkill
#: occupancy; only `landedHits` and `expectedLandedHits` remain `null` + reason.
ENCOUNTER_TELEMETRY_VERSION = 3


def encounter_role(unit):
    """A declared semantic role, or `unclassified`.

    A tactical human/monster label is deliberately NOT used. The roster's own declared semantic
    role (`role`/`communityRole`) is read when present; otherwise the entry is labelled
    `unclassified` clearly rather than guessed from the `human`/`monster` flags.
    """
    for key in ('role', 'communityRole', 'community_role'):
        value = unit.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return 'unclassified'


class EncounterCounters:
    """Read-only event-site counters: the Python mirror of the native encounter counters.

    Every field is incremented at the same genuine mutation site the native kernel counts - the HP
    mutation in `deliver`, the attack resolution in `attack_result`, the reaction candidate loop and
    the Leaving entry. Nothing here mutates the engine, draws a random number or enters the digest,
    so a telemetry-off run is byte-identical and the counters are comparable field for field.
    """

    def __init__(self, own_slots, boss):
        self.own_slots = [int(identity) for identity in own_slots]
        self.boss = int(boss)
        self.own_first_death_tick = [-1]*len(self.own_slots)
        self.own_first_leaving_tick = [-1]*len(self.own_slots)
        self.enemy_rolls = 0
        self.enemy_roll_hits = 0
        self.enemy_roll_misses = 0
        self.enemy_resolved_attacks = 0
        self.enemy_hits = 0
        self.enemy_misses = 0
        self.counter_checks = 0
        self.counter_enqueues = 0
        self.boss_death_tick = -1
        self.boss_postdeath_attempts = 0
        self.boss_postdeath_lands = 0
        self.boss_reentries = 0
        # v3: event-site Leaving entries, first access timestamps and the compact post-death
        # actual-hit gap summary (count/min/max/sum; no per-hit trace is retained).
        self.boss_leavings = 0
        self.boss_access_first_command = -1
        self.boss_access_first_attempt = -1
        self.boss_postdeath_last_hit = -1
        self.boss_postdeath_gap_count = 0
        self.boss_postdeath_gap_min = -1
        self.boss_postdeath_gap_max = -1
        self.boss_postdeath_gap_sum = 0

    def note_own_death(self, identity, tick):
        if identity in self.own_slots:
            slot = self.own_slots.index(identity)
            if self.own_first_death_tick[slot] < 0:
                self.own_first_death_tick[slot] = int(tick)

    def note_own_leaving(self, identity, tick):
        if identity in self.own_slots:
            slot = self.own_slots.index(identity)
            if self.own_first_leaving_tick[slot] < 0:
                self.own_first_leaving_tick[slot] = int(tick)

    def note_boss_leaving(self, tick):
        """One Leaving (state 8) entry for the rival leader; event-site, never the death."""
        self.boss_leavings += 1

    def note_boss_target_command(self, tick, target):
        """The first stored command aimed at the rival leader (`-1` while none has been enqueued)."""
        if target == self.boss and self.boss_access_first_command < 0:
            self.boss_access_first_command = int(tick)

    def note_boss_attempt(self, tick):
        """The first attack attempt aimed at the rival leader, hit or miss."""
        if self.boss_access_first_attempt < 0:
            self.boss_access_first_attempt = int(tick)

    def note_postdeath_hit(self, tick):
        """One actual post-death landed hit on the rival leader, compacted into gap statistics."""
        tick = int(tick)
        if self.boss_postdeath_last_hit >= 0:
            gap = tick - self.boss_postdeath_last_hit
            self.boss_postdeath_gap_count += 1
            self.boss_postdeath_gap_sum += gap
            if self.boss_postdeath_gap_min < 0 or gap < self.boss_postdeath_gap_min:
                self.boss_postdeath_gap_min = gap
            if gap > self.boss_postdeath_gap_max:
                self.boss_postdeath_gap_max = gap
        self.boss_postdeath_last_hit = tick

    def report(self):
        return dict(
            ownFirstDeathTick=[[identity, tick] for identity, tick
                               in zip(self.own_slots, self.own_first_death_tick)],
            ownFirstLeavingTick=[[identity, tick] for identity, tick
                                 in zip(self.own_slots, self.own_first_leaving_tick)],
            enemyRolls=self.enemy_rolls, enemyRollHits=self.enemy_roll_hits,
            enemyRollMisses=self.enemy_roll_misses,
            enemyResolvedAttacks=self.enemy_resolved_attacks, enemyHits=self.enemy_hits,
            enemyMisses=self.enemy_misses,
            counterChecks=self.counter_checks, counterEnqueues=self.counter_enqueues,
            bossDeathTick=self.boss_death_tick,
            bossPostdeathAttempts=self.boss_postdeath_attempts,
            bossPostdeathLands=self.boss_postdeath_lands,
            bossReentries=self.boss_reentries,
            bossLeavings=self.boss_leavings,
            bossAccessFirstCommand=self.boss_access_first_command,
            bossAccessFirstAttempt=self.boss_access_first_attempt,
            bossPostdeathGapCount=self.boss_postdeath_gap_count,
            bossPostdeathGapMin=self.boss_postdeath_gap_min,
            bossPostdeathGapMax=self.boss_postdeath_gap_max,
            bossPostdeathGapSum=self.boss_postdeath_gap_sum)


def encounter_telemetry(progress, *, own_units=None, own_survivors=None, own_present=False,
                        resource_uses=None, herb=None, item_use_count=None, verdict=None,
                        censored=None, finish_boundary=None, finish_boundary_label=None,
                        reward_outcome=None, finish_policy=None, diagnostic_finish=None,
                        counters=None):
    """The additive compact telemetry block, built from readings both backends already publish.

    `progress` is `blank_progress()`-shaped (`ProgressWatch.report()` in Python, the `progress_*`
    fields of `KaBattleReport` in the native adapter). `own_units` is the declared roster when it
    is reachable; `None` means the caller has no roster (the identity fields are then `null` with a
    reason rather than invented). `counters` is the event-site counter block (`EncounterCounters.
    report()` in Python, built from the native encounter report in the adapter): own true-death and
    first-Leaving ticks, enemy roll/resolved hits and misses, counter checks/enqueues and the boss
    post-death attempts/lands/re-entries. Nothing here mutates the engine or enters the digest.

    An absent `counters` or `progress` observer is `null` + reason, never `blank_progress()`-shaped
    zeros; a `-1` timing value from a present observer means the event was not observed and is
    listed in `notObserved`, which is a different statement from unavailable.

    `finish_boundary` is the *observed* finish boundary published at the shared encounter seam.
    The native encounter report publishes no such field (its only finish reading is the pre-verdict
    prize count), so it stays `null` + reason there; a backend that holds only a declared
    finish-*policy* descriptor (`combat_sandbox`'s `diagnostic-truncated` / `awaiting-confirmation`
    strings) reports it under the separate `finishBoundaryLabel` key rather than promoting a policy
    label into the observed-boundary slot.

    `diagnostic_finish` is the engine's declared Finish reading. `dispatchedChests` is a real
    dispatch count; `queueAtVerdict` is the pre-verdict pending queue (what the native encounter
    report exposes). The final `dispatchedChests` is the canonical on-verdict dispatch: the observed
    win dispatch on a terminal win, and `0` on a terminal non-win, because the recovered Finish
    dispatch gate is win-only and a loss never dispatches the queued chests merely because they were
    pending. The queue/pending count travels separately, never promoted into a final dispatch.

    """
    progress_present = bool(progress)
    progress = progress or {}
    counters = counters or None
    unavailable = {}
    not_observed = []

    identities = roles = None
    if own_units:
        identities = [int(unit['identity']) for unit in own_units
                      if unit.get('identity') is not None]
        roles = {str(int(unit['identity'])): encounter_role(unit) for unit in own_units
                 if unit.get('identity') is not None}
    else:
        unavailable['ownIdentities'] = 'the declared roster is not reachable at this seam'
        unavailable['ownRoles'] = 'the declared roster is not reachable at this seam'

    # ---- Event-site counters (shared, comparable field for field with the native kernel) ----
    own_deaths = own_leavings = enemy = counter_block = boss_block = post_death = None
    occupancy = boss_future = boss_future_exec = None
    if counters:
        own_deaths = {str(int(identity)): int(tick)
                      for identity, tick in counters.get('ownFirstDeathTick') or () if int(tick) >= 0}
        own_leavings = {str(int(identity)): int(tick)
                        for identity, tick in counters.get('ownFirstLeavingTick') or ()
                        if int(tick) >= 0}
        enemy = dict(rolls=int(counters['enemyRolls']),
                     rollHits=int(counters['enemyRollHits']),
                     rollMisses=int(counters['enemyRollMisses']),
                     resolvedAttacks=int(counters['enemyResolvedAttacks']),
                     hits=int(counters['enemyHits']), misses=int(counters['enemyMisses']))
        counter_block = dict(checks=int(counters['counterChecks']),
                             enqueues=int(counters['counterEnqueues']))
        boss_block = dict(firstDeathTick=int(counters['bossDeathTick']),
                          postDeathAttempts=int(counters['bossPostdeathAttempts']),
                          postDeathLands=int(counters['bossPostdeathLands']),
                          reentries=int(counters.get('bossReentries', 0)),
                          leavings=int(counters.get('bossLeavings', 0)),
                          damagingResets=int(counters.get('bossDamagingResets', 0)),
                          accessFirstCommandTick=int(counters.get('bossAccessFirstCommand', -1)),
                          accessFirstAttemptTick=int(counters.get('bossAccessFirstAttempt', -1)))
        gap_count = int(counters.get('bossPostdeathGapCount', 0))
        boss_block['postDeathHitGaps'] = dict(
            count=gap_count,
            minTicks=(None if gap_count == 0 else int(counters.get('bossPostdeathGapMin', -1))),
            maxTicks=(None if gap_count == 0 else int(counters.get('bossPostdeathGapMax', -1))),
            sumTicks=int(counters.get('bossPostdeathGapSum', 0)))
        occupancy = dict(targetableTicks=int(counters.get('targetableTicks', 0)),
                         usingSkillTicks=int(counters.get('usingSkillTicks', 0)))
        boss_future = counters.get('bossFutureHitsAtDeath')
        boss_future_exec = counters.get('bossFutureIncludeExecuting')
        post_death = dict(attemptedHits=int(counters['bossPostdeathAttempts']),
                          landedHits=int(counters['bossPostdeathLands']))
    else:
        for key in ('ownFirstDeathTick', 'enemyResolvedAttacks', 'enemyHits', 'enemyMisses',
                    'counterChecks', 'counterEnqueues', 'postDeathAttemptedHits',
                    'postDeathLandedHits', 'bossCounters', 'hitGapTicks', 'resetCount',
                    'targetableOccupancy', 'usingSkillOccupancy'):
            unavailable[key] = TELEMETRY_UNAVAILABLE[key]

    if progress_present:
        boss_identity = int(progress.get('bossIdentity', -1))
        boss_first_death = int(progress.get('bossDeathTick', -1))
        boss_leaving = int(progress.get('bossLeavingTick', -1))
        boss_lifecycle = dict(reentries=int(progress.get('postDeathBossReentries', 0)),
                              leavings=int(progress.get('postDeathBossLeavings', 0)),
                              postDeathPrizes=int(progress.get('postDeathPrizes', 0)))
        commands = dict(remainingAtDeath=int(progress.get('storedCommandsAtDeath', 0)),
                        remainingTargetingBossAtDeath=int(
                            progress.get('storedCommandsTargetingBossAtDeath', 0)),
                        targetHoldersAtDeath=int(progress.get('storedTargetHoldersAtDeath', 0)),
                        targetingBoss=int(progress.get('commandsTargetingBoss', 0)),
                        targetingBossReleased=int(progress.get('commandsTargetingBossReleased', 0)),
                        maxSimultaneous=int(progress.get('maxSimultaneousStoredCommands', 0)),
                        maxSimultaneousTargetingBoss=int(
                            progress.get('maxSimultaneousCommandsTargetingBoss', 0)),
                        targetHoldersPeak=int(progress.get('storedTargetHoldersPeak', 0)),
                        releasedAfterDeath=int(progress.get('commandsReleasedAfterDeath', 0)),
                        releasedAfterDeathTargetingBoss=int(
                            progress.get('commandsReleasedAfterDeathTargetingBoss', 0)),
                        firstReleaseAfterDeathTick=int(
                            progress.get('firstPostDeathCommandReleaseTick', -1)),
                        lastReleaseAfterDeathTick=int(
                            progress.get('lastPostDeathCommandReleaseTick', -1)),
                        landedHits=None, expectedLandedHits=None)
        commands['futureAttempts'] = (None if boss_future is None or int(boss_future) < 0
                                      else int(boss_future))
        commands['futureAttemptsIncludesExecutingHit'] = (
            None if boss_future_exec is None or int(boss_future_exec) < 0
            else bool(int(boss_future_exec)))
        commands['futureAttemptsSemantics'] = (
            'remaining pending-command hit attempts aimed at the boss at the first-death snapshot: '
            'sum of max(0, skill.count - use_index); a hit already resolved (including the hit '
            'currently executing at the sample) has advanced use_index and is excluded')
        for name, value in (('bossFirstDeathTick', boss_first_death),
                            ('bossLeavingTick', boss_leaving),
                            ('firstReleaseAfterDeathTick', commands['firstReleaseAfterDeathTick']),
                            ('lastReleaseAfterDeathTick', commands['lastReleaseAfterDeathTick'])):
            if value < 0:
                not_observed.append(name)
        if commands['futureAttempts'] is None:
            not_observed.append('futureAttempts')
        if commands['futureAttemptsIncludesExecutingHit'] is None:
            not_observed.append('futureAttemptsIncludesExecutingHit')
        if counters:
            for name, key in (('accessFirstCommandTick', 'bossAccessFirstCommand'),
                              ('accessFirstAttemptTick', 'bossAccessFirstAttempt')):
                if int(counters.get(key, -1)) < 0:
                    not_observed.append(f'boss.{name}')
    else:
        boss_identity = boss_first_death = boss_leaving = None
        boss_lifecycle = commands = None
        for key in ('bossIdentity', 'bossFirstDeathTick', 'bossLeavingTick', 'bossLifecycle',
                    'commands'):
            unavailable[key] = 'no progress observer was supplied at this seam'

    # `landedHits` (a per-target future landing counter) and `expectedLandedHits` (a per-hit
    # probability model) are genuinely unsupported, so they stay null with their reason. Every
    # other v3 metric now has a real reading from the counter/progress observer and is absent here.
    if counters is None:
        unavailable['futureAttempts'] = TELEMETRY_UNAVAILABLE['futureAttempts']
    unavailable['landedHits'] = TELEMETRY_UNAVAILABLE['landedHits']
    unavailable['expectedLandedHits'] = TELEMETRY_UNAVAILABLE['expectedLandedHits']
    if item_use_count is None:
        unavailable['itemUseCount'] = TELEMETRY_UNAVAILABLE['itemUseCount']
    if finish_boundary is None:
        unavailable['finishBoundary'] = (
            'no observed finish boundary is published at the shared encounter seam; the declared '
            'finish-policy descriptor is reported separately under finishBoundaryLabel'
            if finish_boundary_label is not None else
            'this report does not publish a finish boundary')
    if own_survivors is None or not own_present:
        unavailable['ownSurvivors'] = 'the own-roster survivor count is not reachable at this seam'
    if resource_uses is None:
        unavailable['resourceUses'] = 'no consumable-use count was supplied at this seam'

    final_reward = None
    resolved = verdict is not None and not censored
    if finish_policy == 'on-verdict' and resolved:
        finish = diagnostic_finish if isinstance(diagnostic_finish, dict) else {}
        win_dispatch = finish.get('dispatchedChests')
        queue_at_verdict = finish.get('queueAtVerdict')
        observed = win_dispatch if win_dispatch is not None else queue_at_verdict
        pending = (reward_outcome or {}).get('pendingChests')
        if verdict == 1:
            # A terminal win is the only verdict at which the declared on-verdict Finish can
            # dispatch the pre-verdict queue; report the observed count (or null + reason when the
            # engine exposes no dispatch reading at all).
            dispatched = observed
            basis = 'declared-model-final-dispatch'
            note = ('declared on-verdict Finish dispatch on a terminal win; a diagnostic cut, '
                    'never an inventory receipt')
        else:
            # The recovered Finish dispatch gate is win-only: a terminal non-win dispatches no
            # chest. The pre-verdict queue is a pending count, never promoted into a final dispatch.
            dispatched = 0
            basis = 'canonical-win-loss-gate'
            note = ('a terminal non-win never dispatches the queued chests; the final dispatch is 0 '
                    'by the canonical win/loss gate, independent of the pre-verdict pending count')
        final_reward = dict(policy='on-verdict', basis=basis, dispatchedChests=dispatched,
                            pendingChests=pending, inventoryReceipt=False, note=note)
        if queue_at_verdict is not None and queue_at_verdict != dispatched:
            final_reward['queueAtVerdict'] = queue_at_verdict
        if dispatched is None:
            final_reward['unavailable'] = 'engine report exposes no diagnostic dispatched count'
    elif finish_policy == 'on-verdict':
        unavailable['finalReward'] = 'the run is unresolved; there is no final award'

    return dict(
        version=ENCOUNTER_TELEMETRY_VERSION,
        ownIdentities=identities,
        ownRoles=roles,
        ownSurvivors=(int(own_survivors) if own_present and own_survivors is not None else None),
        ownDeaths=own_deaths,
        ownLeavings=own_leavings,
        bossIdentity=boss_identity,
        bossFirstDeathTick=boss_first_death,
        bossLeavingTick=boss_leaving,
        bossLifecycle=boss_lifecycle,
        commands=commands,
        enemy=enemy,
        counterChecks=(counter_block['checks'] if counter_block else None),
        counterEnqueues=(counter_block['enqueues'] if counter_block else None),
        occupancy=occupancy,
        boss=boss_block,
        postDeath=post_death,
        resource=dict(resourceUses=(int(resource_uses) if resource_uses is not None else None),
                      itemUseCount=(int(item_use_count) if item_use_count is not None else None),
                      herb=herb),
        finalStatus=dict(verdict=verdict, censored=censored, finishBoundary=finish_boundary,
                         finishBoundaryLabel=finish_boundary_label, finishPolicy=finish_policy,
                         rewardOutcome=reward_outcome),
        finalReward=final_reward,
        notObserved=not_observed,
        unavailable=unavailable,
    )
