"""Conservative rival-boss reward-entitlement certificate: report-only plumbing.

Native basis (frozen `fb834373...30208`; RVAs resolved from the combat key/registry overlay
`RE-evidence/20260919-combat-registry/combat-keys.json`):

  * Producer, one prize per state-8 entry of the rival leader: `EnterLeaving 0x1586a80` ->
    `AddGuerrillaPrize 0x14ed618` (single site 0x1586e00; guard team1 + not isPvP + hasMonster +
    isGuerrilla + leader identity). State 8 has only two producers: `UpdateDamaging 0x1585f04`
    (dead monster, frame>=7) and `UpdateKnockingDown 0x1586954` (human, frame>=101).
  * A new state-8 entry needs a new state-6 entry, i.e. a new event26 result containing the boss.
  * event26 senders read a stored target - `BKL:16 BATTLE_TARGET_ENTITY_ID`, written only by
    `UpdateCharging 0x1584bf0` (0x1584fd0/0x1584ff4) and cleared only by `ExitUsingSkill 0x1585d2c`
    (0x1585e00), read by `UpdateAttacking 0x1585568` (0x1585674, gate `World.GetEntity` only) - or a
    queued AI command (`AISystem.Script 0x1481e54` -> `ScrSkill 0x148a950` joins cmd+0x24/+0x28), or
    they are `CanDamage 0x168e8cc`-gated cell routes.
  * `ExitAttacking 0x1585a18` does not clear gauge/target fields (special-combat.md:374), so BKL:16
    survives a death that happens in state 4 (Attacking).

Certificate C1..C4, evaluated at one frame boundary (T):

    C1  Param.GetValue(boss, 10) == 0           (dead; CanDamage and IsAttackableTarget need HP>=1)
    C2  BKI:5(boss) == 8                        (Leaving)
    C3  no fighter F has BKL:16(F) == id(boss)  (conservative, ALL fighters; see proof limits)
    C4  no fighter has a queued AI command      (strict: all command queues empty)
    C5 implied: no future selection (C1 + IsAttackableTarget 0x1588554)
    C6 implied: no cell/projectile hit   (C1 + CanDamage)

Timing rule: the certificate is only issued while the battle is unresolved (`engine.verdict is None`),
i.e. strictly BEFORE the verdict. A first C1..C4 hold observed at or after the verdict frame is never
certified: the native ordering between the last prize producer and `EnterEnding` is unproven, so a
certificate must be a pre-verdict snapshot of the prize queue. After the snapshot the run keeps going
(never latched, never frozen) and ANY later prize-queue change invalidates the certificate.

Sufficiency: every prize needs a new `ChangeState(boss, 8)`; a monster needs a new state-6 entry from
event26 containing the boss; the senders either read a stored target (excluded by C3/C4) or are
CanDamage-gated cell routes (excluded by C1); and no new selection can write BKL:16 = id(boss) (C5).
The certificate is *per encounter*: it is refused when the enemy roster contains a Cure-branch skill
(type 2 recovery / 15 revive), because the Cure route can raise an HP0 same-team ally (including the
boss) and so break C1.

TWO audited proof issues (`check_combat_reward_entitlement.py` re-derives both):

  (a) HP0 can become positive through the enemy Cure route. Native: `EnumerateAvailableRecoverySkill
      b__0 0x15e2190` accepts the same team with `Param.GetRate(e,10) < 100` (includes 0) and
      `EnumerateAvailableResurrectionSkill b__0 0x15e20dc` accepts the same team with
      `Param.GetValue(e,10) < 1`; `OnCure 0x1587e64` (0x1587f48) is the only Cure-branch HP add.
      Own-team battle items cannot reach the enemy boss: recovery targets the supplied same-team
      residents only, the two Recovery Potions add own-team HP (param 10) and the Holy Herb adds
      own-team MP (param 11) - so items are NOT MP-only, but none can heal an ENEMY boss.
      `AISystem.UpdateRecovery 0x14aaa04` is the overworld resident state
      (`AISystem.UpdateStateResident`), and the other HP writers are creation/`EnterStateGettingExp`/
      `ParamSet` construction. DISPOSITION: pin the encounter to a roster whose every enemy skill row is
      known and Cure-free; conservatively REJECT otherwise, including any enemy skill id with no known
      master row (a missing row is not evidence of safety).
  (b) A stale BKL:16 on an already-dead or idle fighter may never clear: only `UpdateCharging` and
      `ExitUsingSkill` write the key, and a fighter that dies in state 4 (or that never re-enters
      state 3/5) never runs either. DISPOSITION: KEEP the conservative all-fighter C3 (do not weaken
      it without native proof); the certificate may therefore not fire on some runs.

This module never mutates combat. `RewardEntitlementWatch` only reads the engine each frame; the run
keeps going and any prize queued after the certificate is reported (never latched, never frozen).
`awardedChestCount` is 0 for a native loss (the recorded Finish dispatch gate `special flag and
winner==1`), independent of the pending count and of any certificate; on a win it is the certified
pending count only when a certificate was issued strictly before the verdict and no later prize
appeared, else None (unknown). A hold first observed at/after the verdict is reported only as a late
hold and never certified. `rewardCountSettled` stays False while the certificate sufficiency is not
closed.
"""

CERTIFICATE_ID = 'ka-reward-entitlement-certificate-1'

# SkillData types that reach the Cure branch (`SkillSystem.UseSkill` -> Cure -> OnCure 0x1587e64):
# 2 recovery and 15 revive. Both can target an HP0 same-team ally natively.
CURE_SKILL_TYPES = (2, 15)

# Recovered Finish dispatch gate (combat_finish.special_finish / check_combat_finish_dispatch.py).
DISPATCH_GATE = 'special battle flag and winner (battle+0x50) == 1'
VICTORY_REQUIRED = True

CLAUSES = (
    ('C1_bossHpZero', 'Param.GetValue(boss, 10) == 0'),
    ('C2_bossLeavingState8', 'BKI:5(boss) == 8'),
    ('C3_noStoredTarget', 'no fighter F has BKL:16(F) == id(boss) (ALL fighters, conservative)'),
    ('C4_noQueuedCommandTarget', 'no fighter has any queued AI command (strict: all queues empty)'),
)

PROOF_LIMITS = [
    'The certificate is a single frame boundary strictly before the verdict: C1..C4 are read once while '
    'the battle is unresolved and the run keeps going, so any prize queued afterwards is reported as a '
    'post-certificate change and invalidates the snapshot (never prevented, never latched). A first '
    'C1..C4 hold observed at or after the verdict frame is reported as a late hold and is NEVER '
    'certified (native producer/EnterEnding ordering unproven).',
    'C3 is deliberately conservative (all fighters). ExitAttacking 0x1585a18 does not clear BKL:16 and '
    'only ExitUsingSkill 0x1585d2c does, so a fighter that dies in state 4 (or never re-enters state 3/5) '
    'can hold a stale target forever; C3 then never holds and no certificate is issued. Do not weaken.',
    'C4 is stricter than the certificate text: it requires every command queue to be empty, not only '
    'the queues whose stored target is the boss.',
    'C5/C6 are implied by C1 (IsAttackableTarget 0x1588554 and CanDamage 0x168e8cc both require HP>=1); '
    'the UseSkill direct-result branch (0x15e1374) is covered only because C4 excludes all queued commands.',
    'Whether GarbageSystem 0x158eb54 ever destroys the boss in state 8 is not proven (would only tighten C3).',
    'The 8->6->8 repeat is simulator-derived, not native-executed; it is preserved and observed, not latched.',
    'awardedChestCount is an entitlement count, not an inventory receipt: dispatching a chest entity is '
    'not storage or collection (combat_finish.finish_report).',
]


def enemy_cure_sources(encounter, skill_rows):
    """Pinned Cure sources in the encounter's enemy roster (known rows only).

    On the supported path every enemy fighter carries exactly one `Monster.skillId`
    (`combat_encounters.special_enemy_baseline`), so the roster pins the whole enemy skill set.
    A skill id with no known row is NOT assumed safe - see `unsupported_enemy_skills`.
    """
    sources = []
    for fighter in encounter.get('fighters', []):
        for skill_id in fighter.get('skills', {}).get('dataIds', []):
            row = skill_rows.get(skill_id)
            if row is not None and row.get('type') in CURE_SKILL_TYPES:
                sources.append(dict(monsterId=fighter.get('monsterId'), name=fighter.get('name'),
                                    skillId=skill_id, skillType=row['type']))
    return sources


def unsupported_enemy_skills(encounter, skill_rows):
    """Enemy skill ids with no known SkillData row: unknown, so the scope must fail closed.

    These are reported separately from `enemy_cure_sources` (which only lists known Cure rows) so a
    silently missing master row can never be read as "not a Cure skill".
    """
    unsupported = []
    for fighter in encounter.get('fighters', []):
        for skill_id in fighter.get('skills', {}).get('dataIds', []):
            if skill_id not in skill_rows:
                unsupported.append(dict(monsterId=fighter.get('monsterId'),
                                        name=fighter.get('name'), skillId=skill_id))
    return unsupported


def certificate_scope(encounter, skill_rows):
    """Encounter-scoped certificate scope.

    FAIL CLOSED: refused when any enemy can cure OR when any enemy skill row is unknown. A missing
    master row is not evidence of safety - an unread id could be a Cure-branch skill - so the
    certificate stays conditional (and is not issued) instead of being silently allowed.
    """
    sources = enemy_cure_sources(encounter, skill_rows)
    unsupported = unsupported_enemy_skills(encounter, skill_rows)
    return dict(
        encounterId=encounter.get('encounterId'),
        enemyCureSources=sources,
        unsupportedEnemySkills=unsupported,
        allowed=not sources and not unsupported,
        requirement=('every enemy skill row is known and none is a Cure-branch skill (type 2 recovery or 15 '
                     'revive): the Cure route can raise an HP0 same-team ally, so a healer could revive the dead '
                     'boss. Own-team battle items cannot reach the enemy boss (recovery targets supplied '
                     'same-team residents only): the Recovery Potions add own-team HP (param 10) and the Holy '
                     'Herb adds own-team MP (param 11), so no own item can heal the enemy boss. An unknown or '
                     'missing skill row is refused, not assumed safe.'),
        basis=('enemy roster Monster.skillId pinned against weapon-skill-profiles skill types; any '
               'unresolvable enemy skill id fails closed'),
    )


def boss_identity(engine):
    """The single rival leader (native leader identity); None when the run has no boss."""
    return next((i for i, spec in engine.specs.items() if spec.get('boss')), None)


def clause_readings(engine, boss):
    """C1..C4 at the current engine frame. Read-only."""
    unit = engine.units.get(boss)
    if unit is None:
        return None
    stored = sorted(i for i, u in engine.units.items() if u['long_board'].get(16) == boss)
    queued = sorted(i for i, u in engine.units.items() if u['commands'])
    boss_queued = sorted(i for i, u in engine.units.items()
                         if any(command.get('target') == boss for command in u['commands']))
    clauses = dict(
        C1_bossHpZero=engine.value(boss, 10) == 0,
        C2_bossLeavingState8=unit['board'][5] == 8,
        C3_noStoredTarget=not stored,
        C4_noQueuedCommandTarget=not queued,
    )
    return dict(boss=boss, bossHp=engine.value(boss, 10), bossState=unit['board'][5],
                storedTargetHolders=stored, queuedCommandHolders=queued,
                bossTargetCommandHolders=boss_queued, clauses=clauses)


class RewardEntitlementWatch:
    """Per-frame read-only observer of the certificate. Never mutates the engine.

    `observe` is called from the sandbox input callback after the fighters phase. It records the first
    frame at which C1..C4 hold inside an allowed scope WHILE the battle is still unresolved (strictly
    before the verdict), then any later prize change. A C1..C4 hold first seen at/after the verdict is
    kept only as `late_hold` and is never certified. The battle is never stopped, latched or frozen.
    """

    def __init__(self, encounter, skill_rows):
        self.scope = certificate_scope(encounter, skill_rows)
        self.certificate = None
        self.late_hold = None
        self.latest = None
        self.observations = 0
        self.verdict_observations = 0
        self.pending_final = 0
        self.pending_at_verdict = None
        self.post_certificate_changed = False
        self.post_certificate_delta = 0

    def observe(self, engine):
        self.observations += 1
        pending = len(engine.prizes)
        self.pending_final = pending
        boss = boss_identity(engine)
        if boss is not None:
            self.latest = clause_readings(engine, boss)
        if engine.verdict is not None:
            self.verdict_observations += 1
            if self.pending_at_verdict is None:
                self.pending_at_verdict = pending
        if self.certificate is None:
            if (self.scope['allowed'] and self.latest is not None
                    and all(self.latest['clauses'].values())):
                snapshot = dict(
                    frame=engine.tick, pendingChestCount=pending,
                    clauses=dict(self.latest['clauses']),
                    storedTargetHolders=list(self.latest['storedTargetHolders']),
                    bossTargetCommandHolders=list(self.latest['bossTargetCommandHolders']))
                if engine.verdict is None:
                    # Strictly before the verdict: the only accepted timing (ordering unproven).
                    self.certificate = snapshot
                elif self.late_hold is None:
                    # Held, but only at/after the verdict: report it, never certify it.
                    self.late_hold = snapshot
        elif pending != self.certificate['pendingChestCount']:
            self.post_certificate_changed = True
            self.post_certificate_delta = pending - self.certificate['pendingChestCount']
        return self.latest

    def readings(self):
        return self.latest or {}


def entitlement_report(*, watch, verdict, prize_callbacks, diagnostic_dispatched, finish_boundary):
    """The additive `rewardEntitlement` report block. Safe-loss 0, pre-verdict-certificate-only win."""
    readings = watch.readings()
    certificate = watch.certificate
    late_hold = watch.late_hold
    pending = watch.pending_final
    changed = watch.post_certificate_changed or watch.post_certificate_delta != 0
    if verdict is None:
        awarded, basis, settled = None, 'unknown-unresolved-battle', False
        reason = ('battle unresolved (IsAnnihilated not reached): pending chest count %d is reported and the '
                  'awarded count is unknown' % pending)
    elif verdict != 1:
        awarded, basis, settled = 0, 'native-win-loss-gate', False
        reason = ('safe 0 from the recorded native Finish dispatch gate (%s); winner==2 dispatches no chest, '
                  'independent of the pending chest count (%d) and of any certificate. The pending count is NOT '
                  'an award: reward-entitlement certificate sufficiency is not closed (enemy-Cure source in some '
                  'supported encounters; C3 clearing unproven)' % (DISPATCH_GATE, pending))
    elif certificate is not None and not changed:
        awarded, basis, settled = certificate['pendingChestCount'], 'reward-entitlement-certificate', True
        reason = ('certificate C1..C4 held at frame %d, strictly before the verdict, in a Cure-free known-row '
                  'encounter and no prize was queued afterwards; awarded = the pending count at the '
                  'certificate' % certificate['frame'])
    else:
        awarded, basis, settled = None, 'unknown-win-without-certificate', False
        unsupported = watch.scope.get('unsupportedEnemySkills') or []
        if not watch.scope['allowed']:
            if unsupported:
                why = ('certificate scope refused: enemy skill row(s) %s have no known master row, so the roster '
                       'cannot be shown Cure-free (a missing row is not assumed safe)'
                       % ', '.join(str(entry['skillId']) for entry in unsupported))
            else:
                why = ('certificate scope refused: the enemy roster carries a Cure-branch skill, so an HP0 boss '
                       'could be healed back to positive HP')
        elif changed:
            why = ('a prize was queued after the pre-verdict certificate (delta %d), so the snapshot no longer '
                   'covers the final queue' % watch.post_certificate_delta)
        elif late_hold is not None:
            why = ('C1..C4 first held only at or after the verdict (frame %d, verdict already set); a late '
                   'certificate is never accepted because the native ordering between the prize producers and '
                   'EnterEnding is unproven' % late_hold['frame'])
        elif certificate is None:
            failed = [name for name, ok in (readings.get('clauses') or {}).items() if not ok]
            why = ('C1..C4 never held before the verdict in a resolved battle' +
                   ('; failed clauses %s' % ', '.join(failed) if failed else ' (no boss reading was observed)'))
        else:
            why = 'a prize was queued after the pre-verdict certificate'
        reason = 'win without a proven pre-verdict certificate: %s' % why
    return dict(
        certificateId=CERTIFICATE_ID,
        certificate=dict(
            holds=certificate is not None,
            frame=(certificate or {}).get('frame'),
            timingRule=('issued only while the battle is unresolved (strictly before the verdict); a hold first '
                        'observed at/after the verdict is never certified'),
            issuedBeforeVerdict=certificate is not None,
            clauses=(certificate or readings).get('clauses'),
            storedTargetHolders=(certificate or readings).get('storedTargetHolders', []),
            bossTargetCommandHolders=(certificate or readings).get('bossTargetCommandHolders', []),
            bossHp=readings.get('bossHp'),
            bossState=readings.get('bossState'),
            scope=watch.scope,
            observations=watch.observations,
            verdictObservations=watch.verdict_observations,
            lateHold=None if late_hold is None else dict(
                frame=late_hold['frame'], pendingChestCount=late_hold['pendingChestCount'],
                clauses=late_hold['clauses']),
        ),
        battleVerdict=verdict,
        victoryRequired=VICTORY_REQUIRED,
        victoryRequiredGate=DISPATCH_GATE,
        pendingChestCount=pending,
        capturedPendingAtVerdict=watch.pending_at_verdict,
        awardedChestCount=awarded,
        awardedChestCountBasis=basis,
        rewardCountSettled=settled,
        rewardCountReason=reason,
        lateCertificateObserved=late_hold is not None,
        postCertificateQueueChanged=changed,
        postCertificateQueueDelta=watch.post_certificate_delta,
        diagnosticFinish=dict(dispatchedChests=diagnostic_dispatched, boundary=finish_boundary,
                              note='the declared Finish policy is a DIAGNOSTIC cut; it is reported separately '
                                   'from the captured pending count and from any certificate'),
        prizeCallbacks=prize_callbacks,
        proofLimits=list(PROOF_LIMITS),
    )
