"""Joint proposal service: the replacement Community candidate generator.

Read-only mathematics. Given a community-admitted parent scenario it returns deliberate,
compensated candidate changes - each one a full child scenario plus the pre-run mechanical
features the adviser consumes. The scheduler calls `pool` repeatedly with the productive
parents it chooses; this service never simulates a battle, never opens a database and never
imports the optimiser.

The service exposes one immutable, plain-serializable ``EncounterPlanningRequest`` (built by the
named ``planning_request`` factory) and one pure entry point, ``compute_planning_task(request)``, so
a planning worker can run the whole prepare/compute/finalize plan from frozen data alone. The same
work is exposed as the three phases ``prepare_planning_task`` / ``compute_planning_chunk`` /
``finalize_planning_task`` for the shared asynchronous host; ``compute_planning_task`` calls those
exact phases synchronously. ``pool`` is the synchronous compatibility wrapper over the same seam.

A proposal never changes skills, their order, trigger settings, the weapon or the fodder
identity. An improvement moves the DPS attack/luck/speed/dexterity jointly and leaves the
support exact; a support question may lower HP/MP/DEF on a clone; an improvement repair
raises a support stat only when the caller explicitly names the limiting non-fodder role.
"""
from __future__ import annotations

import hashlib
import statistics
import math
import pickle
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from functools import lru_cache

import strategy_build_domain as domain
import strategy_parallel_proposals as parallel_proposals
import strategy_encounter_compiler as compiler
import strategy_mechanics as mechanics
import strategy_students as students
import search_contract as contract
from combat_resolution import hit_rate

VERSION = 'strategy-joint-proposals-1'
PURPOSES = ('improvement', 'boundary', 'compensated', 'support')
JOINT_STATS = {'atk': 13, 'lck': 16, 'spd': 15, 'dex': 19}
SUPPORT_STATS = {'hp': 10, 'mp': 11, 'def': 14}
#: A missing constraint document is the declared synthetic search domain, never ``player``.
SYNTHETIC_CONSTRAINTS = {'context': 'synthetic'}
DEFAULT_MAXIMUM = 12
MAXIMUM_CAP = 64
_RUN_BUDGET = {'improvement': 8, 'boundary': 8, 'compensated': 16, 'support': 8}
COMPENSATION_SOLVER = mechanics.solve_attack_profile
#: Observer-only record of the most recent preparation plan. It is never read by selection; it
#: exists so a caller/test can prove whether a call was actually parallelised or honestly serial.
LAST_PREPARATION = {'workers': 0, 'reason': 'uninitialised', 'specs': 0}

REQUEST_VERSION = 'strategy-joint-planning-request-1'
#: Deterministic final ordering: highest novelty first, ties broken by proposal id.
ORDERING_KEY = 'novelty-desc,id-asc'
#: Selection stays round-index driven; a request may still carry an opaque seed control.
SEED_POLICY = 'round-index-rotation'
#: The module-level pure entry point a planning worker imports and calls.
COMPUTE_ENTRY_POINT = 'compute_planning_task'
#: The frozen compute input the request digest covers. Routing identity (library/campaign/session,
#: revisions, owner, controls) is carried but not hashed: the same frozen compute input is the same
#: planning work whatever library or session asked for it.
COMPUTE_FIELDS = ('requestId', 'scopeRevision', 'policyRevision', 'engineRevision', 'parentId',
                  'scenario', 'evidence', 'constraints', 'question', 'roundIndex', 'purpose',
                  'maximum', 'seed', 'seedPolicy', 'admissionPreparation')
#: Serialized routing/compatibility identity, separate from the compute digest.
ROUTING_FIELDS = ('library', 'campaignId', 'encounter', 'sessionId', 'generation', 'owner', 'mode',
                  'encounterRevision', 'mechanicsRevision', 'constraintsRevision',
                  'evidenceRevision', 'parentIdentity', 'controls', 'extra')
#: Phase payload revisions. Both phases exchange only plain primitive/tuple/dict values.
PREPARED_VERSION = 'strategy-joint-prepared-plan-1'
CHUNK_VERSION = 'strategy-joint-chunk-results-1'
#: The smallest useful planner phases the shared asynchronous host may dispatch separately.
PLANNING_PHASES = ('prepare_planning_task', 'compute_planning_chunk', 'finalize_planning_task')
#: Observer-only record of the most recent prepared phase payload size. Never read by selection.
LAST_PREPARED_PAYLOAD = {'bytes': 0, 'windowBytes': 0, 'specs': 0}


@dataclass(frozen=True)
class EncounterPlanningRequest:
    """Immutable, plain-serializable frozen input for one joint-proposal planning compute.

    It carries only frozen data (the parent scenario, bounded evidence and constraint/policy
    documents), the scalar controls a planning worker needs and the routing identity a host needs to
    route the result - never a database, store, executor, future, optimizer, whole library or any
    other live object. Nested documents are stored as private deep copies, so mutating the caller's
    dicts later cannot change a request already handed to a worker. Build one with
    :func:`planning_request` (or directly for the compatibility ``pool`` wrapper); :meth:`digest`
    covers the compute input only, while the routing fields travel alongside it.
    """

    # Compute-relevant frozen input; :meth:`digest` covers exactly these fields.
    requestId: str = ''
    scopeRevision: str = ''
    policyRevision: str = ''
    engineRevision: str = ''
    parentId: str | None = None
    scenario: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    constraints: dict | None = None
    question: dict | None = None
    roundIndex: int = 0
    purpose: str = 'improvement'
    maximum: int = DEFAULT_MAXIMUM
    seed: int | None = None
    seedPolicy: str = SEED_POLICY
    admissionPreparation: dict | None = None
    # Routing/compatibility identity: carried and serialized, deliberately outside the digest.
    library: str | None = None
    campaignId: str | None = None
    encounter: dict | None = None
    sessionId: str | None = None
    generation: int | None = None
    owner: str | None = None
    mode: str | None = None
    encounterRevision: str | None = None
    mechanicsRevision: str | None = None
    constraintsRevision: str | None = None
    evidenceRevision: str | None = None
    parentIdentity: str | None = None
    controls: dict | None = None
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, 'admissionPreparation', deepcopy(self.admissionPreparation))
        object.__setattr__(self, 'scenario',
                           deepcopy(self.scenario) if self.scenario is not None else {})
        object.__setattr__(self, 'evidence',
                           deepcopy(self.evidence) if self.evidence is not None else {})
        object.__setattr__(self, 'constraints',
                           deepcopy(self.constraints) if self.constraints is not None else None)
        object.__setattr__(self, 'question',
                           deepcopy(self.question) if self.question is not None else None)
        object.__setattr__(self, 'encounter',
                           deepcopy(self.encounter) if self.encounter is not None else None)
        object.__setattr__(self, 'controls',
                           deepcopy(self.controls) if self.controls is not None else None)
        object.__setattr__(self, 'extra', deepcopy(self.extra) if self.extra is not None else {})

    def compute_view(self):
        """The frozen compute input the digest is taken over (stable key order)."""
        body = {'requestVersion': REQUEST_VERSION}
        for name in COMPUTE_FIELDS:
            body[name] = getattr(self, name)
        return body

    def payload(self):
        """Plain JSON-compatible view of every frozen field (stable key order)."""
        body = self.compute_view()
        for name in ROUTING_FIELDS:
            body[name] = getattr(self, name)
        return body

    def digest(self):
        """Content revision of the frozen compute input (stable across processes)."""
        return hashlib.sha256(domain.canonical(self.compute_view()).encode('utf-8')).hexdigest()

    @classmethod
    def from_payload(cls, payload):
        """Reconstruct a request from :meth:`payload` output (plain data only)."""
        names = COMPUTE_FIELDS + ROUTING_FIELDS
        return cls(**{name: payload[name] for name in names if name in payload})


def _digest(value):
    return hashlib.sha256(domain.canonical(value).encode('utf-8')).hexdigest()


def _field(index, pid):
    return 'ownUnits.%d.parameters.%d' % (index, pid)


def _source_index(normalized, name):
    for index, unit in enumerate(normalized['ownUnits']):
        if unit['name'] == name:
            return index
    return None


def _role_indices(normalized):
    roles = {}
    try:
        placed = {int(row['grid']): row['unit'] for row in students._placed_rows(normalized)}
    except Exception:
        return roles
    for grid in sorted(placed):
        unit = placed[grid]
        roles.setdefault(students.role(unit), _source_index(normalized, unit['name']))
    return roles


def _effective(prepared, index, pid):
    entry = prepared['ownUnits'][index]['effectiveParameters'].get(pid)
    return None if entry is None else entry['value']


def _domain_table(prepared, constraints):
    table = {}
    for index in range(len(prepared['ownUnits'])):
        for pid in list(JOINT_STATS.values()) + list(SUPPORT_STATS.values()):
            span = mechanics.synthetic_stat_bounds(pid)
            if span is None:
                continue
            table[(index, pid)] = dict(low=span[0], high=span[1], fixed=None, adjustable=True)
    context, provenance = None, 'unknown'
    if constraints:
        schema = domain.constraint_schema(constraints)
        context, provenance = schema['context'], schema['provenanceStatus']
        for _field_name, spec in schema['fixed'].items():
            key = (spec['index'], spec['parameterId'])
            if key in table:
                table[key]['fixed'] = spec['value']
        for _field_name, spec in schema['bounds'].items():
            key = (spec['index'], spec['parameterId'])
            if key in table:
                table[key]['low'] = max(table[key]['low'], spec['low'])
                table[key]['high'] = min(table[key]['high'], spec['high'])
        adjustable = constraints.get('adjustableFields')
        if adjustable is not None:
            allowed = set()
            for adjustable_field in adjustable:
                parsed, _reason = domain._parse_field(adjustable_field)
                if parsed:
                    allowed.add(parsed)
            for key in table:
                table[key]['adjustable'] = key in allowed
    return table, context, provenance


def _clamp(table, index, pid, value):
    spec = table.get((index, pid))
    if spec is None or value is None or not spec['adjustable']:
        return None
    if spec['fixed'] is not None:
        return int(spec['fixed'])
    return max(spec['low'], min(spec['high'], int(value)))


def _luck_landmarks(luck):
    profile = mechanics.crit_profile(luck)
    values = [profile['nextLuckValueChangingRate'], profile['classEnteredAtLuck'],
              profile['previousClassEnteredAtLuck'], luck + 1, luck - 1]
    out = []
    for value in values:
        if value is not None and value != luck and value not in out:
            out.append(int(value))
    return out


def _dex_landmarks(dex, roster):
    values = [dex + 1, dex - 1]
    for enemy in roster[:3]:
        speed, luck = enemy['parameters']['speed'], enemy['parameters']['luck']
        now = hit_rate(dex, speed, luck)
        entry = mechanics.hit_rate_inverse(speed, luck, now)['dexterity']
        if entry is not None:
            values.extend([entry, entry - 1, entry + 1])
        floor = mechanics.hit_profile(0, speed, luck)['rateFloor']
        lower = mechanics.hit_rate_inverse(speed, luck, max(floor, now - 1))
        if lower['dexterity'] is not None:
            values.extend([lower['dexterity'], lower['dexterity'] - 1])
    out = []
    for value in values:
        if value is not None and value != dex and value not in out:
            out.append(int(value))
    return out


def _speed_landmarks(speed, luck, roster):
    timing = mechanics.action_timing(speed)
    values = [timing['lowerAgilityEdge'], timing['upperAgilityEdge']]
    for interval in (timing['nextFasterInterval'], timing['nextSlowerInterval']):
        if interval is not None:
            values.append(mechanics.agility_for_interval(interval))
    boss = next((row for row in roster if row['boss']), roster[0] if roster else None)
    if boss is not None:
        contact = mechanics.incoming_contact_boundaries(boss['parameters']['dexterity'], luck, speed)
        values.extend(row['agility'] for row in contact['interiorLandmarks'])
    out = []
    for value in values:
        if value is not None and value != speed and value not in out:
            out.append(int(value))
    return out

def _solver_roster(compiled):
    rows = []
    for row in compiled['roster']:
        params = row['parameters']
        rows.append(dict(name=row['name'], boss=row['boss'], hp=params['hp'],
                         defense=params['defense'], speed=params['speed'],
                         luck=params['luck'], dexterity=params['dexterity']))
    return rows


def _profile_targets(profile, dps_index, compiled):
    unit = profile['ownUnits'][dps_index]
    goals = {}
    for entry in unit['outgoing']:
        index = entry['enemyIndex']
        row = compiled['roster'][index] if index < len(compiled['roster']) else None
        if row is None:
            continue
        summary = entry['perHitSummary']
        goal = dict(damageP10=summary['p10'], damageMedian=summary['median'], damageP90=summary['p90'],
                    hitProbability=entry['probabilities']['hit'])
        hp = row['parameters']['hp']
        if hp and summary['mean']:
            goal['expectedHitsToKill'] = hp / summary['mean']
        goals[index] = goal
    return goals


def _best_attack(solve):
    best = None
    for result in solve.get('results', []):
        for candidate in result.get('candidates', []):
            row = (candidate.get('residual', 0.0), candidate['attack'])
            if best is None or row < best:
                best = row
        if not result.get('candidates') and result.get('best'):
            row = (result['best'].get('residual', 0.0), result['best']['attack'])
            if best is None or row < best:
                best = row
    return (None, None) if best is None else (best[1], best[0])


@lru_cache(maxsize=128)
def _cached_default_best_attack(solver, dependency_digest, exact_inputs):
    targets, luck, dex, roster, bounds = pickle.loads(exact_inputs)
    return _best_attack(solver(targets, luck, dex, roster, bounds))


def _clear_default_best_attack_cache():
    _cached_default_best_attack.cache_clear()


mechanics.register_invalidator(_clear_default_best_attack_cache)


def _solve_best_attack(targets, luck, dex, roster, bounds):
    solver = COMPENSATION_SOLVER
    if solver is not mechanics.solve_attack_profile:
        # Custom solvers are a test/integration seam. Preserve their call semantics and do not
        # retain any results that could survive a later monkeypatch.
        return _best_attack(solver(targets, luck, dex, roster, bounds))
    exact_inputs = pickle.dumps((targets, luck, dex, roster, bounds), protocol=5)
    return _cached_default_best_attack(solver, mechanics.dependency_digest(), exact_inputs)


def transfer_targets(source_scenario, target_scenario):
    """Transfer reduced damage/HTK targets by enemy role, never reward observations.

    Bosses match bosses. Followers match the nearest relative HP rank among source
    followers. This is an explicitly approximate proposal prior requiring destination
    simulation; it is not a claim that formation, healing or timings are equivalent.
    """
    source = mechanics.normalize_scenario(source_scenario)
    roles = _role_indices(source)
    if roles.get(students.ROLE_DPS) is None:
        return {}
    old = compiler.compile_encounter(source)
    new = compiler.compile_encounter(target_scenario)
    goals = _profile_targets(mechanics.profile(source), roles[students.ROLE_DPS], old)
    mapped = {}
    for boss in (False, True):
        origin = sorted((i for i, r in enumerate(old['roster']) if r['boss'] == boss),
                        key=lambda i: old['roster'][i]['parameters']['hp'])
        destination = sorted((i for i, r in enumerate(new['roster']) if r['boss'] == boss),
                             key=lambda i: new['roster'][i]['parameters']['hp'])
        for rank, index in enumerate(destination):
            if not origin:
                continue
            previous = origin[round(rank * (len(origin) - 1) / max(1, len(destination) - 1))]
            goal = deepcopy(goals.get(previous, {}))
            ratio = new['roster'][index]['parameters']['hp'] / max(1, old['roster'][previous]['parameters']['hp'])
            for field in ('damageP10', 'damageMedian', 'damageP90'):
                if goal.get(field) is not None:
                    goal[field] *= ratio
            mapped[index] = goal
    return mapped


def _boundary_specs(question):
    if not question or question.get('kind') != 'fixed-build':
        return []
    parsed, _reason = domain._parse_field(question.get('field') or '')
    if not parsed:
        return []
    value = (question.get('fixedFields') or {}).get(question['field'])
    if value is None:
        value = question.get('value')
    if not isinstance(value, int) or isinstance(value, bool):
        return []
    return [dict(axis=parsed, value=value, purpose='boundary', kind='fixed-build',
                 extraChanges=[], compensation=None,
                 notes='frozen fixed-build boundary point at the supplied value')]


def _compensated_specs(question, table, roles):
    if not question or question.get('kind') != 'compensated':
        return []
    parsed, _reason = domain._parse_field(question.get('field') or '')
    if not parsed:
        return []
    value = (question.get('fixedFields') or {}).get(question['field'])
    if value is None:
        value = question.get('value')
    if not isinstance(value, int) or isinstance(value, bool):
        return []
    dps = roles.get(students.ROLE_DPS)
    atk_field = _field(dps, JOINT_STATS['atk']) if dps is not None else None
    if atk_field is None or atk_field not in set(question.get('adjustableFields') or []):
        return []
    return [dict(axis=parsed, value=value, purpose='compensated', kind='compensated',
                 extraChanges=[], compensation='atk',
                 notes='fixes the questioned field and lets the permitted ATK compensation move')]


def _stat_label(pid):
    for name, value in SUPPORT_STATS.items():
        if value == pid:
            return name.upper()
    for name, value in JOINT_STATS.items():
        if value == pid:
            return name.upper()
    return str(pid)


def _defense_for_mean_damage(enemy_attack, allowed_damage, table, index):
    """Largest permitted DEF whose expected per-hit damage fits ``allowed_damage``.

    The recovered armor term is non-increasing in DEF, so a bounded search is exact on the
    reduced expected-damage model; the full damage distribution is never assumed here.
    """
    spec = table.get((index, SUPPORT_STATS['def']))
    if spec is None or not spec['adjustable'] or spec['fixed'] is not None:
        return None
    low, high = int(spec['low']), int(spec['high'])
    best = None
    while low <= high:
        middle = (low + high) // 2
        if mechanics.mean_damage(enemy_attack, middle) <= allowed_damage:
            best = middle
            low = middle + 1
        else:
            high = middle - 1
    return best


def _support_compensation(prepared, compiled, index, axis_pid, axis_value, adjustable, table):
    """Bounded HP/DEF compensation for a lowered support boundary on ONE non-fodder unit.

    Only a *reduction* is compensated, only with the partner HP/DEF field the caller declared
    adjustable, only through the reduced expected-damage survival model, and never with an item
    or a consumable path: the measurement/resource policy stays frozen. Returns
    ``(extra_changes, detail)`` where detail carries the exact/reduced/approx label and residual.
    """
    current = _effective(prepared, index, axis_pid)
    if current is None or axis_value >= current:
        return [], None  # a repair or an unchanged point gets no protective compensation
    partner = (SUPPORT_STATS['hp'] if axis_pid == SUPPORT_STATS['def']
               else SUPPORT_STATS['def'] if axis_pid == SUPPORT_STATS['hp'] else None)
    if partner is None:
        return [], None  # MP has no survival-coupled partner here
    if _field(index, partner) not in adjustable:
        return [], None
    spec = table.get((index, partner))
    if spec is None or not spec['adjustable'] or spec['fixed'] is not None:
        return [], None
    attacks = [row['effectiveAttack'] for row in
               compiled['demands']['survivalPressure']['perEnemy']]
    attacks = [int(value) for value in attacks if value]
    hit_points = _effective(prepared, index, SUPPORT_STATS['hp'])
    defense = _effective(prepared, index, SUPPORT_STATS['def'])
    if not attacks or hit_points is None or defense is None:
        return [], None
    worst = max(attacks)
    damage_now = mechanics.mean_damage(worst, defense)
    if damage_now <= 0:
        return [], None
    hits = hit_points / damage_now
    if axis_pid == SUPPORT_STATS['def']:
        damage_new = mechanics.mean_damage(worst, axis_value)
        required = int(math.ceil(hits * damage_new))
        target = _clamp(table, index, partner, required)
        if target is None:
            return [], None
        residual = float(max(0, required - target))
        method = ('expected-damage survival ratio (reduced model: mean per-hit damage; the '
                  'full damage distribution is not modelled)')
    else:
        allowed = axis_value / hits if hits > 0 else 0.0
        target = _defense_for_mean_damage(worst, allowed, table, index)
        if target is None:
            return [], None
        required = int(target)
        residual = 0.0
        method = ('largest DEF inside the expected-damage survival budget (reduced model; the '
                  'full damage distribution is not modelled)')
    fidelity = 'approx' if residual > 0 else 'reduced'
    detail = dict(partnerPid=partner, partnerStat=_stat_label(partner), value=int(target),
                  before=int(_effective(prepared, index, partner)), required=int(required),
                  residual=residual, fidelity=fidelity, method=method,
                  consumablePolicy='frozen', healingPolicy='frozen', itemPath=None)
    return [(index, partner, int(target))], detail


def _support_question_specs(question, compiled, prepared, roles, table):
    """Honour a frozen support question: freeze its axis value and add bounded compensation."""
    parsed, _reason = domain._parse_field(question.get('field') or '')
    if not parsed:
        return []
    index, pid = parsed
    if pid not in SUPPORT_STATS.values():
        return []
    role = next((name for name, position in roles.items() if position == index), None)
    if role not in (students.ROLE_DPS, students.ROLE_HEALER):
        return []  # support role preserved; a fodder is never made durable or questioned here
    value = (question.get('fixedFields') or {}).get(question['field'])
    if value is None:
        value = question.get('value')
    if not isinstance(value, int) or isinstance(value, bool):
        return []
    adjustable = set(question.get('adjustableFields') or [])
    extra, detail = _support_compensation(prepared, compiled, index, pid, value, adjustable, table)
    notes = ('frozen support question at %s %d on the %s unit' % (_stat_label(pid), value, role))
    if detail:
        notes += (' with bounded %s compensation %s (residual %g, %s)'
                  % (detail['partnerStat'], detail['value'], detail['residual'], detail['fidelity']))
    return [dict(axis=(index, pid), value=value, purpose='support', kind='support-boundary',
                 extraChanges=extra, compensation=('support' if extra else None),
                 supportCompensation=detail, exact=True, notes=notes)]


def _support_landmark_specs(compiled, prepared, roles, table):
    fallback_defense = compiled['demands']['survivalPressure']['fallbackDefenseForEncounter']
    max_enemy_attack = max((row['effectiveAttack'] for row in
                            compiled['demands']['survivalPressure']['perEnemy']), default=0)
    specs = []
    for role, index in ((students.ROLE_DPS, roles.get(students.ROLE_DPS)),
                        (students.ROLE_HEALER, roles.get(students.ROLE_HEALER))):
        if index is None:
            continue
        landmarks = ((SUPPORT_STATS['def'], fallback_defense, 'fallback-forcing DEF'),
                     (SUPPORT_STATS['hp'], max_enemy_attack, 'largest enemy effective ATK'),
                     (SUPPORT_STATS['mp'], 0, 'MP floor'))
        for pid, landmark, reason in landmarks:
            current = _effective(prepared, index, pid)
            target = _clamp(table, index, pid, landmark)
            if current is None or target is None or target >= current:
                continue
            specs.append(dict(axis=(index, pid), value=target, purpose='support',
                              kind='support-boundary', extraChanges=[], compensation=None,
                              notes='lower support on the %s clone to the %s' % (role, reason)))
    return specs


def _support_specs(compiled, prepared, roles, table, question=None):
    """A frozen support question when one is supplied, else the default support landmarks."""
    if question is not None and question.get('kind') == 'support':
        return _support_question_specs(question, compiled, prepared, roles, table)
    return _support_landmark_specs(compiled, prepared, roles, table)


def _repair_specs(profile, prepared, compiled, roles, table, evidence):
    """Upward-only support repair for the EXACT limiting stats the evidence names.

    The named role alone is not a licence to raise every support stat: an observed MP crossing must
    not spend the improvement budget on unrelated DEF/HP, and a degraded HP/DEF point must not spend it on
    MP. ``limitingStats`` is the evidence's own list of the axes actually shown to limit (``mp`` for a
    crossing, ``hp``/``def`` for a controlled degraded support point); only those axes are repaired.
    Absent or empty ``limitingStats`` yields no repair, because an un-evidenced axis must stay unspent.
    """
    role = (evidence or {}).get('limitingRole')
    if role not in (students.ROLE_DPS, students.ROLE_HEALER):
        return []
    index = roles.get(role)
    if index is None:
        return []
    limiting = (evidence or {}).get('limitingStats')
    if not isinstance(limiting, (list, tuple, set)) or not limiting:
        return []
    allowed = {str(name).lower() for name in limiting}
    fallback_defense = compiled['demands']['survivalPressure']['fallbackDefenseForEncounter']
    max_enemy_attack = max((row['effectiveAttack'] for row in
                            compiled['demands']['survivalPressure']['perEnemy']), default=0)
    unit = profile['ownUnits'][index]
    kit_cost = sum(int(entry['cost']) for entry in unit['mpDependencies']['skillCosts'])
    specs = []
    for name, pid, landmark, reason in (
            ('def', SUPPORT_STATS['def'], fallback_defense, 'fallback-forcing DEF'),
            ('hp', SUPPORT_STATS['hp'], max_enemy_attack, 'largest enemy effective ATK'),
            ('mp', SUPPORT_STATS['mp'], kit_cost, 'full kit MP cost')):
        if name not in allowed:
            continue
        current = _effective(prepared, index, pid)
        target = _clamp(table, index, pid, landmark)
        if current is None or target is None or target <= current:
            continue
        specs.append(dict(axis=(index, pid), value=target, purpose='improvement', kind='repair',
                          extraChanges=[], compensation=None,
                          notes='upward-only support repair of the named limiting %s (%s)' % (role, reason)))
    return specs

def _improvement_specs(profile, compiled, roles, table, evidence):
    dps = roles.get(students.ROLE_DPS)
    if dps is None:
        return []
    unit = profile['ownUnits'][dps]
    luck = unit['effectiveStats']['luck']
    dex = unit['effectiveStats']['dexterity']
    speed = unit['effectiveStats']['speed']
    roster = compiled['roster']
    specs = []
    # Move to adjacent reduced hits-to-kill targets, in both directions. Slower
    # progress can improve farming opportunities; stronger is not presumed better.
    targets = _profile_targets(profile, dps, compiled)
    for direction in (-1, 1):
        shifted = deepcopy(targets)
        for goal in shifted.values():
            previous = goal.get('expectedHitsToKill')
            if previous is None or previous <= 0:
                continue
            following = max(1, int(previous) + direction)
            goal['expectedHitsToKill'] = following
            for field in ('damageP10', 'damageMedian', 'damageP90'):
                if goal.get(field) is not None:
                    goal[field] *= previous / following
        specs.append(dict(axis=(dps, JOINT_STATS['atk']), value=unit['effectiveStats']['attack'],
                          purpose='improvement', kind='progression', extraChanges=[], compensation='atk',
                          targetProfiles=shifted, notes='adjacent reduced boss/follower hits-to-kill target'))
    source = (evidence or {}).get('sourceScenario')
    if source is not None:
        transferred = (evidence or {}).get('transferTargets')
        if transferred:
            specs.append(dict(axis=(dps, JOINT_STATS['lck']), value=luck, purpose='improvement',
                              kind='encounter-transfer', extraChanges=[], compensation='atk',
                              targetProfiles=transferred, notes='role-matched source HTK prior; destination evidence required'))
    for value in _luck_landmarks(luck):
        target = _clamp(table, dps, JOINT_STATS['lck'], value)
        if target is not None:
            specs.append(dict(axis=(dps, JOINT_STATS['lck']), value=target, purpose='improvement',
                              kind='compensation', extraChanges=[], compensation='atk',
                              notes='discrete crit-rate landmark compensated by the bounded ATK inverse'))
    for value in _dex_landmarks(dex, roster):
        target = _clamp(table, dps, JOINT_STATS['dex'], value)
        if target is not None:
            specs.append(dict(axis=(dps, JOINT_STATS['dex']), value=target, purpose='improvement',
                              kind='compensation', extraChanges=[], compensation='atk',
                              notes='discrete outgoing-accuracy landmark compensated by the ATK inverse'))
    nearest_luck = min(_luck_landmarks(luck), key=lambda value: abs(value - luck), default=None)
    for value in _speed_landmarks(speed, luck, roster):
        target = _clamp(table, dps, JOINT_STATS['spd'], value)
        if target is None:
            continue
        extra = []
        if nearest_luck is not None:
            luck_target = _clamp(table, dps, JOINT_STATS['lck'], nearest_luck)
            if luck_target is not None:
                extra.append((dps, JOINT_STATS['lck'], luck_target))
        specs.append(dict(axis=(dps, JOINT_STATS['spd']), value=target, purpose='improvement',
                          kind='speed-contact', extraChanges=extra, compensation='atk',
                          notes='timing/contact class change with joint Luck+ATK compensation'))
    observed = (evidence or {}).get('observedStatVectors')
    if observed:
        row = _coverage_spec(dps, unit['effectiveStats'], observed, table)
        if row is not None:
            specs.append(row)
    return specs


def _coverage_spec(dps, stats, observed, table):
    stats = {name: stats.get(name, stats.get(full)) for name, full in
             (('atk', 'attack'), ('lck', 'luck'), ('spd', 'speed'), ('dex', 'dexterity'))}
    axis_pid = axis_name = None
    best = -1.0
    for name, pid in JOINT_STATS.items():
        span = table.get((dps, pid))
        if span is None:
            continue
        values = [int(vector[name]) for vector in observed if name in vector]
        if not values:
            continue
        width = max(1, span['high'] - span['low'])
        gap = max(abs(stats[name] - min(values)), abs(max(values) - stats[name])) / width
        if gap > best:
            best, axis_pid, axis_name = gap, pid, name
    if axis_pid is None:
        return None
    span = table[(dps, axis_pid)]
    extreme = span['high'] if stats[axis_name] <= (span['low'] + span['high']) / 2 else span['low']
    target = _clamp(table, dps, axis_pid, extreme)
    if target is None or target == stats[axis_name]:
        return None
    return dict(axis=(dps, axis_pid), value=target, purpose='improvement', kind='coverage',
                extraChanges=[], compensation='atk',
                notes='coverage point outside the observed win region, compensated by the ATK inverse')


def _apply(child, index, pid, value):
    contract.set_effective_parameter(child, child['ownUnits'][index], pid, int(value))


def _preserved(parent, child, parent_prepared, roles):
    if len(parent['ownUnits']) != len(child['ownUnits']):
        return False, 'unit count changed'
    for before, after in zip(parent['ownUnits'], child['ownUnits']):
        if (list(before.get('skills') or []) != list(after.get('skills') or [])
                or list(before.get('invocationLevels') or []) != list(after.get('invocationLevels') or [])
                or before.get('weaponId') != after.get('weaponId')):
            return False, 'skills/invocation/weapon changed'
    child_prepared = mechanics.prepared_setup(child)
    if child_prepared['ownFormationOrder'] != parent_prepared['ownFormationOrder']:
        return False, 'formation order changed'
    for index, unit in enumerate(parent['ownUnits']):
        if students.role(unit) != students.ROLE_FODDER:
            continue
        for pid in (10, 14):
            if _effective(parent_prepared, index, pid) != _effective(child_prepared, index, pid):
                return False, 'fodder changed'
    return True, 'preserved'


def _contact_summary(compiled, luck, speed):
    boss = next((row for row in compiled['roster'] if row['boss']), None)
    if boss is None or luck is None or speed is None:
        return None
    try:
        contact = mechanics.incoming_contact_boundaries(boss['parameters']['dexterity'], luck, speed)
    except Exception:
        return None
    return dict(hitRateAtAgility=contact['hitRateAtAgility'], interval=contact['interval'],
                rateChangeCount=contact['rateChangeCount'])

def _features(profile, roles, compiled):
    units = {unit['unitIndex']: unit for unit in profile['ownUnits']}
    dps = units[roles['dps']]
    stats = dps['effectiveStats']
    hit_out = [e['probabilities']['hit'] for e in dps['outgoing'] if e['probabilities']['hit'] is not None]
    hit_in = [e['hitRate'] for e in dps['incomingAccuracy']['perEnemy']]
    means = [e['perHitSummary']['mean'] for e in dps['outgoing'] if e['perHitSummary']['mean'] is not None]
    ttk = [e['ttk'].get('approximateExpectedHits') for e in dps['outgoing']
           if e['ttk'].get('approximateExpectedHits') is not None]
    contact = _contact_summary(compiled, stats['luck'], stats['speed']) or {}
    features = {
        'unit_count': len(profile['ownUnits']),
        'dps_atk': stats['attack'], 'dps_lck': stats['luck'], 'dps_spd': stats['speed'],
        'dps_dex': stats['dexterity'], 'dps_hp': stats['hp'], 'dps_def': stats['defense'],
        'dps_mp': stats['mp'], 'dps_interval': dps['speed']['interval'],
        'dps_period': dps['speed']['period'], 'dps_crit_rate': dps['critProfile']['rate'],
        'incoming_hit_min': min(hit_in) if hit_in else 0,
        'incoming_hit_max': max(hit_in) if hit_in else 0,
        'incoming_hit_mean': statistics.fmean(hit_in) if hit_in else 0,
        'outgoing_hit_min': min(hit_out) if hit_out else 0,
        'outgoing_hit_max': max(hit_out) if hit_out else 0,
        'outgoing_hit_mean': statistics.fmean(hit_out) if hit_out else 0,
        'dps_damage_mean': statistics.fmean(means) if means else 0,
        'dps_damage_mean_max': max(means) if means else 0,
        'dps_ttk_approx_mean': statistics.fmean(ttk) if ttk else 0,
        'incoming_contact_rate_changes': contact.get('rateChangeCount', 0),
        'incoming_contact_hit_rate': contact.get('hitRateAtAgility', 0),
        'mp_expected_per_action': dps['mpDependencies']['expectedMpPerAction'],
        'normal_attack_probability': dps['skills']['normalAttackProbability'],
        'expected_command_hits': dps['skills']['expectedCommandHitsPerAction'],
        'formation_dps_index': roles['dps'],
    }
    healer = units.get(roles.get('healer'))
    if healer is not None:
        features.update(healer_hp=healer['effectiveStats']['hp'],
                        healer_def=healer['effectiveStats']['defense'],
                        healer_mp=healer['effectiveStats']['mp'])
    fodder_indices = [i for i, unit in enumerate(profile['ownUnits']) if unit['role'] == students.ROLE_FODDER]
    if fodder_indices:
        features['fodder_hp_max'] = max(profile['ownUnits'][i]['effectiveStats']['hp'] for i in fodder_indices)
        features['fodder_def_max'] = max(profile['ownUnits'][i]['effectiveStats']['defense'] for i in fodder_indices)
    return {name: float(value) for name, value in features.items()}


def _novelty(parent_stats, child_stats, observed):
    span = sum(max(1, abs(value)) for value in child_stats.values()) or 1
    distance = sum(abs(child_stats[key] - parent_stats[key]) for key in child_stats) / span
    novelty = min(1.0, distance)
    if observed:
        gaps = []
        for vector in observed:
            common = [key for key in child_stats if key in vector]
            if common:
                gaps.append(sum(abs(child_stats[key] - vector[key]) for key in common) /
                            (sum(max(1, abs(child_stats[key])) for key in common) or 1))
        if gaps:
            novelty = min(1.0, 0.5 * novelty + 0.5 * min(gaps))
    return round(novelty, 6)


def _training(prepared, index):
    return prepared['ownUnits'][index].get('averageTrainingLevel')


def _compensation_explanation(spec, compensation, residual):
    """The exact/reduced/approx label, its model and the residual mismatch for one proposal."""
    detail = spec.get('supportCompensation')
    if detail is not None:
        return detail['fidelity'], detail['method'], detail.get('residual')
    if compensation == 'atk':
        mismatch = 0.0 if residual is None else float(residual)
        fidelity = 'approx' if mismatch > 1e-9 else 'reduced'
        return fidelity, 'bounded expected-damage ATK inverse (reduced model)', mismatch
    return 'exact', 'single frozen field changed exactly; no compensation claimed', None


def _why_simulation(spec, compensation, evidence, context, provenance, fidelity=None, model=None):
    parts = ['The change is chosen from discrete mechanical landmarks'
             + (' and the ATK compensation from the bounded inverse.' if compensation == 'atk' else '.')]
    parts.append('A simulation is required because earned reward, survival and target-selection are '
                 'stateful and are never predicted here.')
    if fidelity:
        parts.append('Compensation explanation is %s (%s); the residual mismatch is recorded and a '
                     'simulation still measures whether the joint change works.'
                     % (fidelity, model or 'no compensation claimed'))
    if context:
        parts.append('Constraint provenance is %s (%s); no reachability is claimed.' % (provenance, context))
    telemetry = (evidence or {}).get('parentTelemetry')
    if telemetry:
        parts.append('Parent intervention explanation: %s' % (telemetry,))
    negative = (evidence or {}).get('negativeAxes')
    if negative:
        parts.append('Isolated negative evidence on %s does not veto this joint move.' % (negative,))
    return ' '.join(parts)

def _build(parent, parent_prepared, parent_compiled, parent_profile, roles, table,
           constraints, context, provenance, spec, parent_id, round_index, evidence, admit=None):
    # dmit defaults to the strict Community contract. The all-strategy service passes an
    # explicit non-Community callback (see strategy_stream_proposals) so the shared joint
    # mechanics can refine a rebel/discovery/stumble/mechanism seed under that stream's own
    # rules instead of relabelling the result as another Community row.
    admit = admit or students.community_admits
    dps = roles['dps']
    child = deepcopy(parent)
    nominal = {}

    def write(index, pid, value):
        target = _clamp(table, index, pid, value)
        if target is None:
            return None
        _apply(child, index, pid, target)
        nominal[_field(index, pid)] = int(target)
        return target

    axis_index, axis_pid = spec['axis']
    axis_value = write(axis_index, axis_pid, spec['value'])
    if axis_value is None:
        return None
    if (spec.get('exact') or spec['kind'] in ('fixed-build', 'compensated')) \
            and axis_value != spec['value']:
        return None  # A query outside its domain is not silently answered at a different value.
    for index, pid, value in spec.get('extraChanges', []):
        write(index, pid, value)

    residual = None
    compensation = spec.get('compensation')
    if compensation == 'atk':
        atk_key = (dps, JOINT_STATS['atk'])
        if table.get(atk_key, {}).get('fixed') is not None:
            write(dps, JOINT_STATS['atk'], table[atk_key]['fixed'])
        else:
            prepared_mid = mechanics.prepared_setup(child)
            dex = _effective(prepared_mid, dps, JOINT_STATS['dex'])
            luck = _effective(prepared_mid, dps, JOINT_STATS['lck'])
            roster = _solver_roster(parent_compiled)
            bounds = table.get(atk_key)
            targets = spec.get('targetProfiles') or _profile_targets(parent_profile, dps, parent_compiled)
            if roster and targets and bounds is not None:
                attack, residual = _solve_best_attack(
                    targets, luck, dex, roster, {'attack': [bounds['low'], bounds['high']]})
                if attack is not None and write(dps, JOINT_STATS['atk'], attack) is None:
                    return None

    admitted, failures = admit(child, parent)
    if not admitted:
        return None
    preserved, reason = _preserved(parent, child, parent_prepared, roles)
    if not preserved:
        return None
    prepared = mechanics.prepared_setup(child)
    # Always validate, even with no caller constraints: an absent document is the declared
    # synthetic domain, never a silent ``player`` label (see SYNTHETIC_CONSTRAINTS).
    effective_constraints = constraints if constraints is not None else SYNTHETIC_CONSTRAINTS
    constraints_result = domain.validate_constraints(child, effective_constraints)
    if not constraints_result['valid']:
        return None

    dps_pids = JOINT_STATS
    parent_stats = {name: _effective(parent_prepared, dps, pid) for name, pid in dps_pids.items()}
    child_stats = {name: _effective(prepared, dps, pid) for name, pid in dps_pids.items()}
    actual_changes = {name: dict(nominal=nominal.get(_field(dps, pid)), before=parent_stats.get(name),
                                 after=child_stats.get(name))
                      for name, pid in dps_pids.items() if _field(dps, pid) in nominal}
    support_changes = {name: dict(before=_effective(parent_prepared, axis_index, pid),
                                  after=_effective(prepared, axis_index, pid))
                       for name, pid in SUPPORT_STATS.items() if _field(axis_index, pid) in nominal}
    profile = mechanics.profile(child)
    features = _features(profile, roles, parent_compiled)
    parent_contact = _contact_summary(parent_compiled, parent_stats.get('lck'), parent_stats.get('spd'))
    child_contact = _contact_summary(parent_compiled, child_stats.get('lck'), child_stats.get('spd'))
    fidelity, compensation_model, compensation_residual = _compensation_explanation(
        spec, compensation, residual)
    identity = domain.identity(child)
    fixed_fields = {_field(index, pid): row['fixed'] for (index, pid), row in table.items()
                    if row.get('fixed') is not None}
    axis_field = _field(axis_index, axis_pid)
    if spec['kind'] in ('fixed-build', 'compensated') and axis_field in nominal:
        fixed_fields.setdefault(axis_field, int(nominal[axis_field]))
    return dict(
        id=_digest(dict(version=VERSION, parentId=parent_id, purpose=spec['purpose'],
                        kind=spec['kind'], changedFields=sorted(nominal), identity=identity,
                        round=round_index)),
        childIdentity=identity,
        scenario=child,
        parentId=parent_id,
        purpose=spec['purpose'],
        changedFields=sorted(nominal),
        fixedFields=fixed_fields,
        domain=constraints_result,
        expectedMechanicalDifferences=dict(
            dpsEffectiveChanges=actual_changes,
            supportEffectiveChanges=support_changes,
            critRateChange=dict(before=mechanics.critical_rate(parent_stats.get('lck') or 0),
                                after=mechanics.critical_rate(child_stats.get('lck') or 0)),
            intervalChange=dict(before=parent_profile['ownUnits'][dps]['speed']['interval'],
                                after=profile['ownUnits'][dps]['speed']['interval']),
            incomingContact=dict(before=parent_contact, after=child_contact),
            trainingSideEffects=dict(before=_training(parent_prepared, axis_index),
                                     after=_training(prepared, axis_index)),
            mpSideEffects=dict(before=_effective(parent_prepared, axis_index, 11),
                               after=_effective(prepared, axis_index, 11),
                               dependenciesBefore=parent_profile['ownUnits'][axis_index]['mpDependencies'],
                               dependenciesAfter=profile['ownUnits'][axis_index]['mpDependencies'],
                               eligibilityBefore=parent_profile['ownUnits'][axis_index]['skills'],
                               eligibilityAfter=profile['ownUnits'][axis_index]['skills']),
            targetProfiles=spec.get('targetProfiles'),
            proposalKind=spec['kind'],
            proposalReason=spec['notes'],
            compensationFidelity=fidelity,
            compensationModel=compensation_model,
            compensationResidual=compensation_residual,
            formationOrderPreserved=True,
            nominalWrittenFields=dict(nominal),
        ),
        approximatelyPreserved=dict(
            formation=True, skills=True, invocationLevels=True, weapon=True, fodder=True,
            outgoingDamageProfile=('deliberately shifted reduced profile' if spec.get('targetProfiles') else
                                   'approximated by the bounded ATK inverse' if compensation == 'atk'
                                   else 'unchanged'),
            compensationResidual=residual,
            compensationFidelity=fidelity,
            admission=dict(admitted=True, failures=failures),
            constraints=constraints_result['valid'],
        ),
        residualMismatch=residual,
        whySimulation=_why_simulation(spec, compensation, evidence, context, provenance,
                                      fidelity, compensation_model),
        plannedBudget=dict(runs=_RUN_BUDGET.get(spec['purpose'], 8), finite=True,
                           note='finite purpose-owned sample; scheduler owns the reservation'),
        stopping=dict(maxRuns=_RUN_BUDGET.get(spec['purpose'], 8),
                      rule='stop when the finite sample is complete; unresolved work blocks any claim'),
        features=features,
        novelty=_novelty(parent_stats, child_stats, (evidence or {}).get('observedStatVectors')),
    )


def _build_from_payload(context, spec):
    """Worker entry point: rebuild one proposal from the frozen parent context.

    The context is a plain, picklable tuple. Every field is derived read-only in the parent and is
    reused verbatim in the worker, so a parallel build is byte-identical to the serial one. Only the
    default Community admission callback reaches here (`_parallel_plan` refuses every other case).
    """
    (parent, parent_prepared, parent_compiled, parent_profile, roles, table,
     constraints, provenance_context, provenance, parent_id, round_index, evidence) = context
    return _build(parent, parent_prepared, parent_compiled, parent_profile, roles, table,
                  constraints, provenance_context, provenance, spec, parent_id, round_index,
                  evidence, admit=students.community_admits)


def _parallel_plan(admit, spec_count):
    """Whether this call may prepare builds on the bounded process pool, and why.

    Parallelism is only ever a preparation speed-up. It is refused (serial) whenever the build is
    not the pure default-Community function, so a custom admission callback or a non-recovered
    compensation solver is never silently dropped from the worker.
    """
    if admit is not students.community_admits:
        return 0, 'custom-admission-serial'
    if COMPENSATION_SOLVER is not mechanics.solve_attack_profile:
        return 0, 'custom-solver-serial'
    return parallel_proposals.plan(spec_count)


def _parent_build_context(parent, constraints):
    """Deterministic read-only build context derived from one already-normalized parent.

    Shared by the prepare phase (spec construction) and the compute phase (spec building) so the
    wire payload never has to ship the derived profile/compiled/table documents between phases; the
    derivation is pure and content-keyed, so recomputing it in the compute phase is identical.
    """
    parent_prepared = mechanics.prepared_setup(parent)
    parent_compiled = compiler.compile_encounter(parent)
    parent_profile = mechanics.profile(parent)
    roles = _role_indices(parent)
    effective_constraints = constraints if constraints is not None else SYNTHETIC_CONSTRAINTS
    table, context, provenance = _domain_table(parent_prepared, effective_constraints)
    return parent_prepared, parent_compiled, parent_profile, roles, table, context, provenance


def _prepare_planning(request, admit):
    """Stage 1 (prepare): frozen validation, normalization, admission and spec construction.

    Pure and deterministic: it reads only ``request`` and ``admit`` and returns a plain prepared
    plan, or ``None`` when the frozen input legitimately yields no candidates. Genuine caller
    errors (unknown purpose, malformed constraint document) still raise, exactly as ``pool`` did.
    """
    if request.purpose not in PURPOSES:
        raise ValueError('unknown purpose %r; expected one of %s' % (request.purpose, PURPOSES))
    maximum = max(0, min(int(request.maximum), MAXIMUM_CAP))
    if not maximum:
        return None
    evidence = dict(request.evidence or {})
    constraints = request.constraints
    if constraints is not None and not domain.constraint_schema(constraints)['valid']:
        raise ValueError('Invalid build constraint document')
    scenario = request.scenario
    try:
        parent = mechanics.normalize_scenario(scenario)
    except Exception:
        return None
    admitted, _failures = admit(parent, None)
    if not admitted:
        return None
    context_rows = _parent_build_context(parent, constraints)
    parent_prepared, parent_compiled, parent_profile, roles, table, context, provenance = context_rows
    if roles.get(students.ROLE_DPS) is None:
        return None
    parent_id = request.parentId if request.parentId is not None else domain.identity(scenario)
    if evidence.get('sourceScenario') is not None:
        evidence['transferTargets'] = transfer_targets(evidence['sourceScenario'], parent)

    question = request.question
    if request.purpose == 'boundary':
        specs = _boundary_specs(question)
    elif request.purpose == 'compensated':
        specs = _compensated_specs(question, table, roles)
    elif request.purpose == 'support':
        specs = _support_specs(parent_compiled, parent_prepared, roles, table, question=question)
    else:
        specs = _improvement_specs(parent_profile, parent_compiled, roles, table, evidence)
        specs += _repair_specs(parent_profile, parent_prepared, parent_compiled, roles, table, evidence)

    # Interleave axes, then rotate across rounds: a small caller limit must not
    # continually spend on the first Luck landmarks or compile an unbounded pool.
    families = {}
    for spec in specs:
        families.setdefault((spec['kind'], spec['axis']), []).append(spec)
    interleaved = []
    while any(families.values()):
        for rows in families.values():
            if rows:
                interleaved.append(rows.pop(0))
    offset = (int(request.roundIndex) * maximum) % max(1, len(interleaved))
    specs = interleaved[offset:] + interleaved[:offset]
    window = specs[:maximum * 3]
    return dict(parent=parent, parent_prepared=parent_prepared, parent_compiled=parent_compiled,
                parent_profile=parent_profile, roles=roles, table=table, constraints=constraints,
                context=context, provenance=provenance, parent_id=parent_id,
                round_index=request.roundIndex, evidence=evidence, window=window, maximum=maximum,
                seen={domain.identity(parent)})


def _dispatch(prepared, admit, build_parallel, observe):
    """Stage 2 (compute): build every windowed spec and keep the first-valid deduped prefix.

    ``build_parallel`` is ``_parallel_plan`` for the compatibility wrapper and ``None`` for the
    pure entry point, which must never open a nested executor.
    """
    window = prepared['window']
    maximum = prepared['maximum']
    if build_parallel is not None:
        workers, reason = build_parallel(admit, len(window))
    else:
        workers, reason = 0, 'pure-compute-serial'
    preparation = dict(workers=workers, reason=reason, specs=len(window))
    if observe:
        LAST_PREPARATION.update(preparation)
    proposals = []
    seen = set(prepared['seen'])
    if workers:
        payload = (prepared['parent'], prepared['parent_prepared'], prepared['parent_compiled'],
                   prepared['parent_profile'], prepared['roles'], prepared['table'],
                   prepared['constraints'], prepared['context'], prepared['provenance'],
                   prepared['parent_id'], prepared['round_index'], prepared['evidence'])
        chunks = parallel_proposals.iter_build_chunks(
            'strategy_joint_proposals', '_build_from_payload', payload, window, workers=workers)
        full = False
        for chunk_specs, built_chunk in chunks:
            for _spec, built in zip(chunk_specs, built_chunk):
                if built is None or built['childIdentity'] in seen:
                    continue
                seen.add(built['childIdentity'])
                proposals.append(built)
                if len(proposals) >= maximum:
                    full = True
                    break
            if full:
                break
    else:
        for spec in window:
            built = _build(prepared['parent'], prepared['parent_prepared'], prepared['parent_compiled'],
                           prepared['parent_profile'], prepared['roles'], prepared['table'],
                           prepared['constraints'], prepared['context'], prepared['provenance'],
                           spec, prepared['parent_id'], prepared['round_index'], prepared['evidence'],
                           admit=admit)
            if built is None or built['childIdentity'] in seen:
                continue
            seen.add(built['childIdentity'])
            proposals.append(built)
            if len(proposals) >= maximum:
                break
    return proposals, preparation


def _finalize(proposals, maximum):
    """Stage 3 (finalize): ranking/diversity order preserved verbatim, then truncate."""
    proposals.sort(key=lambda row: (-row['novelty'], row['id']))
    return proposals[:maximum]


def _request_meta(request):
    """The scalar identity/revision envelope of one frozen request (bounded, plain values)."""
    return dict(
        version=VERSION, requestVersion=REQUEST_VERSION, requestId=request.requestId,
        requestDigest=request.digest(), scopeRevision=request.scopeRevision,
        policyRevision=request.policyRevision, engineRevision=request.engineRevision,
        purpose=request.purpose, roundIndex=request.roundIndex, seed=request.seed,
        seedPolicy=request.seedPolicy, maximum=request.maximum)


def _envelope(meta, effective_maximum, parent_id, preparation, proposals):
    """Plain result envelope: ordered proposals plus deterministic byte/revision metadata."""
    encoded = domain.canonical(proposals).encode('utf-8')
    return dict(
        version=meta['version'],
        requestVersion=meta['requestVersion'],
        requestId=meta['requestId'],
        requestDigest=meta['requestDigest'],
        scopeRevision=meta['scopeRevision'],
        policyRevision=meta['policyRevision'],
        engineRevision=meta['engineRevision'],
        purpose=meta['purpose'],
        roundIndex=meta['roundIndex'],
        seed=meta['seed'],
        seedPolicy=meta['seedPolicy'],
        maximum=meta['maximum'],
        effectiveMaximum=effective_maximum,
        parentId=parent_id,
        ordering=dict(key=ORDERING_KEY, count=len(proposals),
                      proposalIds=[row['id'] for row in proposals]),
        preparation=dict(preparation),
        proposalDigest=hashlib.sha256(encoded).hexdigest(),
        proposalBytes=len(encoded),
        proposals=proposals,
    )


def _planning_result(request, prepared, preparation, proposals):
    """Plain result envelope for the synchronous seam (unchanged byte-for-byte)."""
    return _envelope(
        _request_meta(request),
        prepared['maximum'] if prepared is not None else 0,
        prepared['parent_id'] if prepared is not None else request.parentId,
        preparation, proposals)


def _plan(request, *, admit=None, build_parallel=None, observe=False):
    """Shared prepare/compute/finalize used by both the pure entry point and ``pool``."""
    admit = admit or students.community_admits
    prepared = _prepare_planning(request, admit)
    if prepared is None:
        return _planning_result(request, None, dict(workers=0, reason='no-prepared-plan', specs=0), [])
    proposals, preparation = _dispatch(prepared, admit, build_parallel, observe)
    return _planning_result(request, prepared, preparation,
                            _finalize(proposals, prepared['maximum']))


def _coerce_request(request):
    """Accept the frozen dataclass, a plain :func:`planning_request` mapping, or a duck-typed request."""
    if isinstance(request, EncounterPlanningRequest):
        return request
    if isinstance(request, Mapping):
        return planning_request(request)
    return request


def _prepared_payload(request, prepared):
    """Bounded, plain-serializable prepare output: frozen parent plus the interleaved spec window."""
    window = prepared['window']
    payload = dict(
        preparedVersion=PREPARED_VERSION,
        request=_request_meta(request),
        parent=prepared['parent'],
        constraints=prepared['constraints'],
        evidence=prepared['evidence'],
        parentId=prepared['parent_id'],
        roundIndex=prepared['round_index'],
        effectiveMaximum=prepared['maximum'],
        seen=sorted(prepared['seen']),
        window=window,
        specCount=len(window),
    )
    if request.admissionPreparation is not None:
        import strategy_admission_preparation as admission
        payload['admissionPreparation'] = request.admissionPreparation
        payload['referenceAdmission'] = admission.prepare(
            request.scenario, request.admissionPreparation, include_stats=False)
    payload['windowBytes'] = len(domain.canonical(window).encode('utf-8'))
    payload['payloadBytes'] = len(domain.canonical(payload).encode('utf-8'))
    return payload


def prepare_planning_task(request):
    """Phase A (prepare): validate/normalize the frozen request into a bounded plain payload.

    Returns ``None`` when the frozen input legitimately yields no candidates (exactly as the
    synchronous seam does); genuine caller errors (unknown purpose, malformed constraints) still
    raise. The derived profile/compiled/table documents are deliberately NOT shipped: the compute
    phase re-derives them from ``parent`` + ``constraints``, so the wire payload stays bounded and is
    not serialized repeatedly for every chunk.
    """
    request = _coerce_request(request)
    prepared = _prepare_planning(request, students.community_admits)
    if prepared is None:
        return None
    payload = _prepared_payload(request, prepared)
    LAST_PREPARED_PAYLOAD.update(bytes=payload['payloadBytes'], windowBytes=payload['windowBytes'],
                                 specs=payload['specCount'])
    return payload


def compute_planning_chunk(prepared_payload, indexed_specs):
    """Phase B (compute): build each ``(index, spec)`` independently; no executor, no dedup.

    Every specification keeps its stable index, so the finalize phase can reassemble the original
    order. The caller may split one prepared payload's specs into several chunks and concatenate the
    returned plain records; this function never opens a pool and never touches the database, a battle
    or the GUI.
    """
    parent = prepared_payload['parent']
    constraints = prepared_payload['constraints']
    context_rows = _parent_build_context(parent, constraints)
    parent_prepared, parent_compiled, parent_profile, roles, table, context, provenance = context_rows
    indexed = []
    for index, spec in indexed_specs:
        built = _build(parent, parent_prepared, parent_compiled, parent_profile, roles, table,
                       constraints, context, provenance, spec, prepared_payload['parentId'],
                       prepared_payload['roundIndex'], prepared_payload['evidence'],
                       admit=students.community_admits)
        if built is not None and prepared_payload.get('admissionPreparation') is not None:
            import strategy_admission_preparation as admission
            built['admissionPreparation'] = admission.prepare(
                built['scenario'], prepared_payload['admissionPreparation'])
        indexed.append([int(index), built])
    chunk = dict(chunkVersion=CHUNK_VERSION, preparedBytes=prepared_payload.get('payloadBytes'),
                 count=len(indexed), indexed=indexed)
    chunk['resultBytes'] = len(domain.canonical(chunk).encode('utf-8'))
    return chunk


def _as_indexed_pair(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError('indexed result must be an (index, proposal) pair; got %s'
                         % (type(value).__name__,))
    index, built = value
    return int(index), built


def _ordered_indexed_results(indexed_results):
    """Flatten chunk record(s)/pairs into one index-ordered list, preserving the stable spec order."""
    pairs = []

    def absorb(value):
        if value is None:
            return
        if isinstance(value, dict):
            if 'indexed' not in value:
                raise ValueError('indexed result chunk is missing "indexed"')
            for item in value['indexed']:
                absorb(item)
            return
        looks_like_pair = (isinstance(value, (list, tuple)) and len(value) == 2
                           and isinstance(value[0], int))
        if looks_like_pair:
            pairs.append(_as_indexed_pair(value))
            return
        for item in value:
            absorb(item)

    absorb(indexed_results)
    pairs.sort(key=lambda row: row[0])
    return pairs


def finalize_planning_task(prepared_payload, indexed_results):
    """Phase C (finalize): reassemble by spec order, then dedup/prefix/rank exactly as the seam.

    Accepts one chunk record, a list of chunk records, or a flat iterable of ``(index, proposal)``
    pairs. Applies the unchanged first-valid-prefix rule (parent identity pre-seeded, duplicate child
    identities skipped, stop at the effective maximum), then the unchanged ranking/truncation.
    """
    ordered = _ordered_indexed_results(indexed_results)
    maximum = int(prepared_payload['effectiveMaximum'])
    seen = set(prepared_payload['seen'])
    proposals = []
    for _index, built in ordered:
        if built is None or built['childIdentity'] in seen:
            continue
        seen.add(built['childIdentity'])
        proposals.append(built)
        if len(proposals) >= maximum:
            break
    proposals = _finalize(proposals, maximum)
    preparation = dict(workers=0, reason='pure-compute-serial',
                       specs=int(prepared_payload['specCount']))
    result = _envelope(prepared_payload['request'], maximum, prepared_payload['parentId'],
                       preparation, proposals)
    if prepared_payload.get('referenceAdmission') is not None:
        result['referenceAdmission'] = prepared_payload['referenceAdmission']
    return result


def compute_planning_task(request):
    """Pure planning compute for one frozen :class:`EncounterPlanningRequest`.

    Deterministic, synchronous, and free of any database, battle spending, GUI or nested executor,
    so ``strategy_parallel_proposals.PlanningPool.submit(
    requestId, 'strategy_joint_proposals', 'compute_planning_task', (request,))`` can run it in a
    worker. It calls the exact prepare/compute/finalize phases in-process, so the shared asynchronous
    host can dispatch those same phases separately and obtain identical output. Returns the ordered
    proposals plus request/byte revision metadata.
    """
    request = _coerce_request(request)
    prepared = prepare_planning_task(request)
    if prepared is None:
        return _envelope(_request_meta(request), 0, request.parentId,
                         dict(workers=0, reason='no-prepared-plan', specs=0), [])
    indexed_specs = [[index, spec] for index, spec in enumerate(prepared['window'])]
    indexed_results = compute_planning_chunk(prepared, indexed_specs)
    return finalize_planning_task(prepared, indexed_results)


#: Caller-facing spellings accepted by :func:`planning_request`, mapped onto the frozen field names.
_REQUEST_ALIASES = {
    'request_id': 'requestId',
    'scope_revision': 'scopeRevision',
    'policy_revision': 'policyRevision',
    'engine_revision': 'engineRevision',
    'mechanics_revision': 'mechanicsRevision',
    'encounter_revision': 'encounterRevision',
    'constraints_revision': 'constraintsRevision',
    'evidence_revision': 'evidenceRevision',
    'parent_id': 'parentId',
    'parent_identity': 'parentIdentity',
    'round_index': 'roundIndex',
    'planning_sequence': 'roundIndex',
    'sequence': 'roundIndex',
    'campaign': 'campaignId',
    'campaign_id': 'campaignId',
    'session': 'sessionId',
    'session_id': 'sessionId',
}
_REQUEST_FIELD_NAMES = frozenset(COMPUTE_FIELDS + ROUTING_FIELDS)
_PLAIN_TYPES = (str, int, float, bool, type(None))


def _assert_plain(value, path='request'):
    """Reject any non-primitive/tuple/dict value so a live object can never reach a worker."""
    if isinstance(value, _PLAIN_TYPES):
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_plain(item, '%s[%d]' % (path, index))
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _assert_plain(key, '%s.<key>' % (path,))
            _assert_plain(item, '%s.%s' % (path, key))
        return
    raise TypeError('planning request must hold primitive/tuple/dict values only; %s is %s'
                    % (path, type(value).__name__))


def _derived_request_id(request):
    body = {name: getattr(request, name) for name in COMPUTE_FIELDS if name != 'requestId'}
    body['requestVersion'] = REQUEST_VERSION
    return 'jreq-' + hashlib.sha256(domain.canonical(body).encode('utf-8')).hexdigest()[:24]


def planning_request(*args, **fields):
    """Named factory: freeze one immutable, plain-serializable encounter planning request.

    Accepts the ``pool`` keyword contract (``scenario``/``constraints``/``evidence``/``round_index``/
    ``maximum``/``purpose``/``question``), the coordinator's routing keywords (``owner``, ``mode``,
    ``library``, ``campaign``, ``session``, revisions, ...) and a single positional mapping, so a host
    can discover and call the factory without importing its exact signature. The request carries the
    request id, library/campaign/encounter/session/generation/owner, purpose and planning sequence,
    the engine/mechanics/encounter/policy/constraints revisions, the evidence revision, the parent
    identity, the exact frozen scenario, bounded compatible evidence, the controls and the question.
    Only primitive/tuple/dict values are accepted; unknown primitive keywords are preserved under
    ``extra``. The request digest covers the compute input only (see :meth:`compute_view`).
    """
    merged = {}
    for arg in args:
        if arg is None:
            continue
        if not isinstance(arg, Mapping):
            raise TypeError('planning_request positional arguments must be mappings')
        merged.update(arg)
    merged.update(fields)
    normalized, extra = {}, {}
    for key, value in merged.items():
        name = _REQUEST_ALIASES.get(key, key)
        if name in _REQUEST_FIELD_NAMES:
            normalized[name] = value
        else:
            extra[key] = value
    if extra:
        carried = dict(normalized.get('extra') or {})
        carried.update(extra)
        normalized['extra'] = carried
    request = EncounterPlanningRequest(**normalized)
    if not request.requestId:
        object.__setattr__(request, 'requestId', _derived_request_id(request))
    _assert_plain(request.payload())
    return request


def pool(scenario, *, constraints=None, evidence=None, round_index=0, maximum=DEFAULT_MAXIMUM,
         purpose='improvement', question=None, admit=None):
    """Deliberate joint candidates for one parent (deterministic, no battle).

    Compatibility wrapper over the shared prepare/compute/finalize seam: it freezes the arguments
    into an :class:`EncounterPlanningRequest`, runs the exact same pure compute, and additionally
    selects the bounded parallel preparation path when the default Community admission and the
    recovered compensation solver are in force. ``admit`` defaults to
    ``strategy_students.community_admits``; the all-strategy service passes an explicit callback so
    a legal seed from another registered stream can be refined by the same shared mechanics under
    that stream's own rules. Byte-identical to the pre-seam implementation on frozen fixtures.
    """
    request = EncounterPlanningRequest(
        scenario=scenario, constraints=constraints, evidence=evidence, roundIndex=round_index,
        purpose=purpose, question=question, maximum=maximum)
    return _plan(request, admit=admit, build_parallel=_parallel_plan, observe=True)['proposals']


__all__ = ['VERSION', 'REQUEST_VERSION', 'ORDERING_KEY', 'COMPUTE_ENTRY_POINT', 'PURPOSES',
           'COMPENSATION_SOLVER', 'PREPARED_VERSION', 'CHUNK_VERSION', 'PLANNING_PHASES',
           'EncounterPlanningRequest', 'planning_request', 'prepare_planning_task',
           'compute_planning_chunk', 'finalize_planning_task', 'compute_planning_task', 'pool',
           'transfer_targets']
