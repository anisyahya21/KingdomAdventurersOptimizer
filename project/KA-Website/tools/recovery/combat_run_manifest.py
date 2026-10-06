"""Reproducible research provenance and enforced fidelity boundaries."""
import hashlib
import json
import platform
from pathlib import Path
from combat_runtime_data import (
    EVIDENCE_DIR, NATIVE_DIR, TABLES_DIR, WORKSPACE_ROOT, manifest, mode, packaged, package_files,
)
from combat_scenario import ScenarioError
from combat_initial_state import START_PROFILE_KINDS
from combat_reward_entitlement import (CERTIFICATE_ID as REWARD_CERTIFICATE_ID,
    CLAUSES as REWARD_CERTIFICATE_CLAUSES, CURE_SKILL_TYPES as REWARD_CURE_SKILL_TYPES)


def source_state_record(scenario):
    """What the run assumes about pre-init entity state, and why each field is or is not modelled.

    A declared profile is a simulation condition, never captured save data.
    """
    if 'prePlacement' in scenario:
        source='captured pre-placement supplied per fighter'
    elif (scenario.get('startProfile') or {}).get('kind') in START_PROFILE_KINDS:
        source='declared isolated-scene0 startProfile (simulation condition, not captured state)'
    else:
        source='final-cell approximation (not native)'
    return dict(source=source,profile=scenario.get('startProfile'),
        modelledSourceFields=[
            'enemy Cell: World.CreateMonster 0x147780c computes trunc-toward-zero((int)x/24,(int)z/24) via AddCell 0x146cb3c (0x2AAAAAAB magic, high>>2+sign at 0x147795c..0x14779b4); followers enter at x=y=z=0 (BuildBossBattle projection 0x16ac77c) and the boss at the SpecialBoss entity world position (SubForm.UpdateGuerrillaDungeon 0x16f5340)',
            'enemy AI board keys19/20 = that same cell x/y: Dictionary<Int32Enum,int>.Add(0x13,cellX) 0x1477af4 and Add(0x14,cellY) 0x1477b08, installed by BlackboardInt..ctor 0x1465378 / AddAI 0x146b520',
            'AI board 62/63/64 starting status, only when the scenario declares it'],
        overwrittenBeforeAnyRead=['Cell','Position(x,y,z)','Speed','Direction','Parent','commands','path','invisible','animation/image/seb presentation',
            'AI board keys 4,5,6,7,8 (InitFighters writes 4:0,5:Waiting1,6:team,7:grid,8:0)'],
        unreadOnTheSupportedPath=['Position offset window [3..5]','AI board keys other than 4,5,6,7,8,12,13,14,17,62,63,64','longBoard keys other than 16',
            'own-team source Cell/board: own members are placed and decided before their source values can be read'],
        declaredConditions=[
            'not-yet-placed fighters carry the board-contract placeholders 4:0/5:0/7:0/8:0 instead of InitFighters output or the factory board; 5:0 keeps an unplaced opponent outside IsAttackableTarget 0x1588554, which reads BKI:5 through Dictionary.get_Item',
            'enemySpawnCell defaults to the recovered follower spawn (0,0); a boss created at its world position is not captured, so its source cell stays an explicit profile field (bossCell)',
            'startingStatus is a user-selected simulation condition, not observed source state'],
        sensitivity=['check_combat_source_profile.py measures the event/RNG interval over the declared enemy source cells; identical traces there bound the uncertainty for strategy comparison'],
        evidence=['check_combat_source_profile.py'])

NATIVE_SHA256='fb834373cb3bd1dc7dac941fcf94113f3b5123e033cf0a41d5e00c0656e30208'
ROOT = WORKSPACE_ROOT
OUT = EVIDENCE_DIR
TABLES = TABLES_DIR
NATIVE = NATIVE_DIR

# These are integration gaps, not claims that the original behavior is unknown.
BOUNDARIES={
    'rng_backend': 'Default backend is System.Random (JRandom.Logic static+4 defaults 0, accessor 0x22a4e94; no decoded writer of set_Logic 0x22a4edc in the frozen ELF). Math and Lib are separate JRandom instances built from independent clock-ms seeds at their own cctors (kairo.unity.math.Random 0x145fdfc, ext.util.Lib 0x145112c); scenario mathSeed/libSeed are explicit injections, not a captured session. fix_ forces seed 0 when ActionReplay.CheckReplayFile or DEVCMD_REPLAY_RECORD=="on" under UNITY_EDITOR (IApplication.Awake 0x231e644). Open: relative startup order of the two cctors, an inlined/reflection write of Logic, string-id resolution',
    'clone_state': 'Copy chain Entity.Clone 0x147405c -> BaseEntity.CopyComponents 0x147e2d8 (ascending component index, no shared Param) and Parameter/Skill/OwnerPlayer/Equip serialization round trips (1013 native cases) are integrated. The clone is a per-component wire round trip: ByteArrayOutputStream 0x2388b00 -> Serialize/SerializeExtra dispatch -> ToByteArray 0x2388c60 -> ByteArrayInputStream 0x23888e4 -> ComponentFactory.Create 0x14c39dc -> Deserialize/DeserializeExtra dispatch, so extraValue/extraMaxValue are deep-copied and their overflow effect on starting stats is covered by the native Param.GetValue 0x166070c / GetMaxValue 0x16609ec cases. SkillComponent.invokingSkills is written last (0x14d1a50 -> WriteList<InvokingSkill> 0x1902e00) and restored to +0x28 (0x14d1b10 -> ReadList<InvokingSkill> 0x1901a60); the executed InitFighters call set contains no SkillComponent address, so in-flight invocations are explicit scenario inputs, never reset or invented. Entity identity/order resolved: fresh MyEntityManager 0x14d497c -> EntityManager 0x147e8d4, InitFighters 0x1582a88/0x1582b10 (BB4=0,BB6=team,BB7=grid,BB8=0,BB5=Waiting1, zero velocity, clear commands, keep unrelated BB), id counter CreateEntity 0x1474148. Pre-placement source state: InitFighters overwrites exactly BB4/5/6/7/8 and recomputes position from the placed cell as i32(x*24)/i32(z*24), so a supplied pre-placement board or position under those fields is discarded by native code (any finite supplied vector describes a legal source state; see support.sourceState). The only pre-placement source field a supported fight can still read is the enemy Cell during the own-team initialization, and the declared isolated-scene0 startProfile derives it from the recovered World.CreateMonster 0x147780c producer instead of hand-written blackboards. The invokingSkills append producer is BattleHelper.Attack 0x168d1e8: List<InvokingSkill>.AddWithResize 0x1dd0928 at 0x168dea4 stores the (dataId,10) pair packed at 0x168de5c..0x168de60, with InvokingSkill..ctor 0x1686204 inlined (no separate caller in the index); the native fast-path store is executed against the model append in check_combat_invoking (33 cases), so the supported path has exactly this producer and no still-untraced appender',
        'animation': 'Combat clip/frame, ground-facing and empty-map height integrated; Human appearance slot writes recovered (InitFighters0x1582b10 get_hasHuman0x146ee7c -> ChangeWeaponImage0x148b6cc/ChangeShieldImage0x148b764 -> SetHumanImgs0x165ea88, no RNG/allocation/membership) and recorded, pixels/SEB decode unmodelled; behaviors18(Rand4)/36(Pick) unreachable (outside COMBAT_BEHAVIORS and recovered EquipData.motion); vehicles rejected',
    'status_passives': 'Supplied sleep/defense-down expiration, sleep charging gate and the generated status skills are integrated: the flags&0x40000 route (UseSkill 0x15e0c2c -> BuffEntitiesOnCell 0x168ee44 -> CanBuff 0x168ea2c -> Buff 0x168e49c with CalcBuffAccuracy 0x168e66c / CalcBuffTurnNum 0x168e760) covers original skill ids 113-116 (type 66) and 117-119 (type 67); every other unrecovered skill/passive still raises instead of being ignored. Open: monster passives outside that route',
        'effects': 'AISystem.ScrRotate0x148a1ec is direction-write plus AIComponent.RemoveCommandAt with no RNG/allocation, reachable only with a queued Lua AI script command the supported fight does not create; trails use explicit isometric mode0 (source scene default, no setter caller found); cell/healing/balloon/attack effect specs, World.CreateEffect component insertion order and cleanup integrated',
        'death_bookkeeping': 'Monster-defeat and rival-leader prize callbacks run synchronously inside the state6->8 / Leaving entries with fields intact and no direct RNG of their own (the Leaving sound roll is a separate Lib draw). Synchronous defeat writers (MonsterSystem.OnMonsterDefeated0x15bed5c -> MonsterData.AddDefeatCount0x162f490 / FriendCampaignSystem.AddDefeat0x158ab14 -> CampaignData.AddDefeatCount0x161ca48 / MissionSystem.CheckAndAddPoint0x15bbd2c -> MissionData.AddNowValue0x162ed78) clamp and store with no random producer; stored only in Data singletons, no attack/status/prize rule reads them back inside the fight',
    'settlement': 'Confirmation/EXP/teardown unresolved; the decoded ItemData receipt arms, the accepted IStock data-type path and the queued/dispatched/retained ledger are integrated in combat_receipt.py, but the unnamed interface resolver target, world collection and the chest-open result derivation stay outside',
    'legal_build': 'Job, ownership, slots and upgrade feasibility not enforced',
}


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def support_contract(scenario):
    mode=scenario.get('mode','research')
    if mode not in ('research','exact','recommendation'):raise ScenarioError('Unknown run mode')
    blockers=dict(BOUNDARIES)
    if 'prePlacement' in scenario:
        initialization='supplied'
    elif (scenario.get('startProfile') or {}).get('kind') in START_PROFILE_KINDS:
        initialization='profile'
    else:
        initialization='approximate'
        blockers['placement']='Final-cell approximation; neither captured pre-placement state nor a declared startProfile'
    if any(u['human'] and 'humanFlags' not in u for u in scenario['ownUnits']):
        blockers['human_flags']='A Human omits humanFlags, so it runs on the declared default 0 (native HumanComponent..ctor 0x14cb02c); the source per-instance flags were not captured'
    if mode!='research':
        raise ScenarioError(f'{mode} mode unavailable: unresolved '+', '.join(blockers))
    # Minimal conditions for the conditional simulation scope. Injected seeds and explicit source
    # inputs are allowed; an exact live-session replay stays a separate, unsupported guarantee, and
    # global recommendations are not loosened until the whole condition set is proven.
    from combat_farmer_slice import ROWS
    from combat_shared_controllers import skill_unsupported_reason
    # Fail closed with an actionable validation error, not a deep runner crash: the transport
    # answers 422 scenario-rejected naming the skill row, so a genuinely unsupported row never
    # reaches the client as a generic 500.
    unsupported=[(sid,ROWS[sid]) for u in scenario['ownUnits'] for sid in u['skills']
                 if skill_unsupported_reason(ROWS[sid]) is not None]
    if unsupported:
        sid,row=unsupported[0]
        raise ScenarioError(f"skill {sid} ({row['nameText']}, type {row['type']}, "
            f"category {row['category']}) is not integrated: {skill_unsupported_reason(row)}")
    finish_policy=scenario.get('finishPolicy','at-horizon')
    conditions=[
        dict(name='nonvehicle',met=not any(u.get('onVehicle',False) or u.get('humanFlags',0)&4 for u in scenario['ownUnits']),
             requirement='no onVehicle and no Human flag0x4; vehicle components and movement are rejected'),
        dict(name='human_flags',met=not any(u.get('humanFlags',0)&~(0x4|0x20) for u in scenario['ownUnits'] if u['human']),
             requirement='no Human declares a flag bit outside the verified set. ecs.HumanComponent$$Check 0x14cab78 is the only flag reader; on the supported special-battle path it is reached with flag0x4 (AISystem.IsOnVehicle 0x148b7fc, rejected by nonvehicle) and flag0x20 (HeightSystem.Update 0x1595ba0, modelled). AISystem.GetMoveSpeed 0x148c3cc, reached by FighterSystem.UpdateMoving 0x15846fc -> AISystem.Move 0x148c2e0, doubles move speed on flag0x208000 and is not modelled, so every other bit fails closed; a missing humanFlags runs as the declared default 0, a disclosed declaration never claimed to be captured source'),
        dict(name='unlinked_parameters',met=not any(u.get('parameterLinks') for u in scenario['ownUnits']),
             requirement='no parameterLinks; linked ParamSet graphs are rejected'),
        dict(name='supported_skills',met=all(skill_unsupported_reason(ROWS[s]) is None for u in scenario['ownUnits'] for s in u['skills']),
             requirement='every equipped skill maps to an integrated route (types0,1,2,11,15,18,19,20,21,22,23,24,26,27,60 and flags&0x40000 types66/67); every other row is rejected by type'),
        dict(name='scene0_special_battle',met=True,
             requirement='special battle encounter 0..19; no Lua AI script command is queued, so AISystem.ScrRotate stays unreachable'),
        dict(name='source_state',met='prePlacement' in scenario or initialization=='profile',
             requirement='captured prePlacement for every fighter, or the declared isolated-scene0 startProfile whose only modelled source fields are the enemy Cell/board keys19/20 recovered from World.CreateMonster 0x147780c plus a declared starting status; every other source field is InitFighters-overwritten or unread before/inside the supported battle'),
        dict(name='injected_seeds',met=True,
             requirement='mathSeed/libSeed are explicit injections, not a captured live RNG stream'),
        dict(name='yield_eligible_finish_policy',met=False,
             requirement='FAIL CLOSED: no declared finish policy is a proven native-automatic producer. '
                         'The only native exit from STATE_ENDING 3 is the one-frame KEY_SELECT input '
                         'edge at 0x14ef538 (Canvas.CheckKeyPulse 0x22f44c8 -> Keypad.CheckKeyPulse '
                         '0x23438b4 over keyPulse_ computed by Keypad.DecideKeyState 0x2343818), and '
                         'an exhaustive bl-site scan of libil2cpp.so finds no automatic producer of '
                         'that edge (check_combat_auto_finish_producer.py). on-verdict additionally '
                         'truncates post-verdict prizes. Dispatched chest counts stay diagnostic and '
                         'no candidate is yield-eligible until the real producer is recovered'),
    ]
    unmet=[c['name'] for c in conditions if not c['met']]
    # Additive decoupling, leaving conditionalSimulation verbatim: the combat rules (source state,
    # skills, flags, placement) are supported independently of the reward yield. The only unmet
    # condition is the unproven automatic Finish producer, so combatRulesSupported can be True while
    # rewardYieldSupported stays False (fail closed). Neither loosens ranking or a global yield.
    combat_rules_unmet=[name for name in unmet if name!='yield_eligible_finish_policy']
    return dict(mode=mode,exactReplaySupported=False,recommendationsSupported=False,
                combatRulesSupported=not combat_rules_unmet,combatRulesUnmet=combat_rules_unmet,
                rewardYieldSupported=False,
                finishPolicy=dict(policy=finish_policy,
                    yieldEligible=False,
                    autoFinishProducerProven=False,
                    autoFinishReason='native Ending exit 0x14ef538 is the one-frame KEY_SELECT input '
                                     'edge; no automatic producer exists in the direct call graph, so '
                                     'the observed click-free Wairo completion is not yet reproduced '
                                     'natively (check_combat_auto_finish_producer.py)',
                    afterEndingPolicy='after-ending runs every native update past the verdict until the '
                                      'recovered Ending counter passes 79 (declared wait-until-frame-80), then requests '
                                      'ONE native confirmation; it does not wait for command queues to empty and does '
                                      'not claim the game settles them (pending activity is reported diagnostically)',
                    note='every declared finish policy is DIAGNOSTIC ONLY: at-horizon and after-ending '
                         'request ONE declared Ending confirmation, on-verdict cuts at the verdict '
                         'boundary; none is a native-automatic claim and none yields a trusted chest '
                         'count'),
                rewardEntitlement=dict(certificate=REWARD_CERTIFICATE_ID,
                    rewardYieldSupported=False,autoFinishProducerProven=False,
                    clauses=[dict(name=name,definition=definition) for name,definition in REWARD_CERTIFICATE_CLAUSES],
                    cureSkillTypes=list(REWARD_CURE_SKILL_TYPES),
                    note='additive report-only certificate (combat_reward_entitlement): C1..C4 read once while the '
                         'battle is unresolved (STRICTLY BEFORE the verdict - a hold first seen at/after the verdict '
                         'is never certified), with pendingChestCount/battleVerdict/victoryRequired/awardedChestCount. '
                         'awardedChestCount is 0 for a native loss (recorded Finish gate: special flag and winner==1) '
                         'independent of the pending count and of any certificate; on a win it is the certified '
                         'pre-verdict pending count only when no later prize was queued, else unknown. '
                         'rewardYieldSupported stays False and autoFinishProducerProven stays False: no timing guess, '
                         'no ranking, no global yield trust. The certificate is refused for an encounter whose enemy '
                         'roster carries a Cure-branch skill (type 2/15) because that route can raise an HP0 ally, '
                         'refused when any enemy skill id has no known master row (fail closed, not assumed safe), '
                         'and its all-fighter C3 is conservative (a stale BKL:16 on a dead fighter may never clear), '
                         'so it may not hold on a real run'),
                conditionalSimulation=dict(supported=not unmet,unmetConditions=unmet,conditions=conditions,
                    scope='conditional model estimate on injected seeds and explicit source inputs; not a live replay'),
                sourceState=source_state_record(scenario),
                blockers=blockers,initialization=initialization,
                provenExclusions=['Fighter post-update attribute drawing: native terrain=-1 exits before render callbacks'],
                rewardMeasure='queued callbacks plus the explicit receipt ledger; retained inventory needs collected chests with supplied master data',
                prizeQueue='append-only on the supported path: EnterLeaving 0x1586a80 -> AddGuerrillaPrize 0x14ed618 ends in List<BattleSystem.Prize>.AddWithResize 0x14ed824 and its 22-callee set holds no capacity, size or clamp site; Finish 0x14ed864 filters and dispatches that same +0x58 list, and ClearPrizes 0x14ee474 runs only at its tail, so the supported path has no fixed chest-entity cap (a whole-program absence claim is not made)')


def native_identity():
    """Native build identity: rehashed locally, attested when running from a package.

    SOURCE VERIFIED AT PACKAGE BUILD   - the packaged manifest attests the SHA-256 that was measured
                                         against the original binary when the package was exported.
    SOURCE PRESENT AND REHASHED AT EXECUTION - the recovery workspace hashes the real ELF every run.
    These are deliberately different guarantees; the manifest says which one produced it.
    """
    native=NATIVE/'inputs/libil2cpp.so'
    if native.is_file():
        with native.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        if digest!=NATIVE_SHA256:raise ScenarioError('Native evidence build hash mismatch')
        return digest,'rehashed-local-binary'
    attested=manifest().get('attested',{})
    digest=attested.get('nativeSha256')
    if digest!=NATIVE_SHA256:
        raise ScenarioError('Attested native build hash missing or mismatched in the runtime package')
    return digest,'attested-package-manifest'


def run_manifest(scenario,contract):
    digest,digestSource=native_identity()
    if packaged():
        # Frozen runtime derivative: hash the package's own files, and carry the source identities
        # that were verified when it was exported.
        files=package_files()
        attested=manifest().get('attested',{})
        return dict(schema='ka-combat-run-manifest-1',nativeSha256=digest,nativeSha256Source=digestSource,
                    runtimeMode=mode(),runtimeFiles=files,attestedSources=attested,
                    normalizedScenarioSha256=canonical_hash(scenario),files=files,
                    contentSha256=canonical_hash(files),python=platform.python_version(),
                    platform=platform.platform(),support=contract,
                    rngBackend='System.Random subtractive primitive (JRandom.Logic defaults to 0 => System.Random); seeds are explicit scenario injections or live per-session wall-clock',
                    initialSeeds=dict(math=scenario['mathSeed'],lib=scenario['libSeed']))
    # Hash contents, including dirty edits; a Git commit alone cannot reproduce this workspace.
    paths=set(Path(__file__).parent.glob('combat_*.py'))
    paths.add(Path(__file__).with_name('recover_special_combat.py'))
    paths.add(Path(__file__).with_name('recover_combat_animation_resources.py'))
    paths.update(TABLES.glob('*.txt'))
    paths.update(OUT/name for name in ('weapon-skill-profiles.json','encounters.json',
                                     'formation-rules.json','effect-resource-checks.json',
                                     'animation-resources.json','skill-combat-constants.json'))
    files={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
    return dict(schema='ka-combat-run-manifest-1',nativeSha256=digest,nativeSha256Source=digestSource,
                runtimeMode=mode(),
                normalizedScenarioSha256=canonical_hash(scenario),files=files,contentSha256=canonical_hash(files),
                python=platform.python_version(),platform=platform.platform(),support=contract,
                rngBackend='System.Random subtractive primitive (JRandom.Logic defaults to 0 => System.Random); seeds are explicit scenario injections or live per-session wall-clock',
                initialSeeds=dict(math=scenario['mathSeed'],lib=scenario['libSeed']))
