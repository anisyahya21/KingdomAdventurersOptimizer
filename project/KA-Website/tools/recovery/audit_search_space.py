"""Read-only audit of a Strategy Optimiser library: what did the search actually explore?

This is diagnostic evidence for the search-space contract. It never writes to the library:
the connection is opened `mode=ro` and every statement is a SELECT.

Usage:
    python audit_search_space.py [--library PATH] [--out PATH]
"""
from __future__ import annotations

import argparse
import collections
import json
import sqlite3
import statistics
from pathlib import Path

import strategy_optimizer

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[2]
DEFAULT_LIBRARY = WORKSPACE / 'KA-Website' / 'strategies.FINISHED.REWARD.SYSTEM.V1.sqlite'

STAT_IDS = (10, 11, 12, 13, 14, 15, 16, 18, 19, 20, 21, 22)


def connect(path):
    return sqlite3.connect(f'file:{Path(path).as_posix()}?mode=ro', uri=True)


def meta(conn):
    return {row[0]: row[1] for row in conn.execute('SELECT key, value FROM meta')}


def unit_signature(unit):
    """Everything a candidate says about one unit except the two searchable axes' values."""
    return json.dumps({k: v for k, v in sorted(unit.items())
                       if k not in ('skills', 'invocationLevels')}, sort_keys=True)


def skill_pairing(unit):
    return tuple(zip(unit.get('skills') or [], unit.get('invocationLevels') or []))


def audit(path):
    conn = connect(path)
    info = meta(conn)
    candidates = [(row[0], json.loads(row[1]), row[2], row[3], row[4])
                  for row in conn.execute('SELECT id, scenario, label, source, stats FROM candidate')]
    runs = conn.execute('SELECT count(*) FROM run').fetchone()[0]
    run_by_candidate = dict(conn.execute('SELECT candidate, count(*) FROM run GROUP BY candidate'))
    archive = [row[0] for row in conn.execute('SELECT candidate FROM archive')] \
        if conn.execute("SELECT count(*) FROM sqlite_master WHERE name='archive'").fetchone()[0] else []

    report = dict(library=str(Path(path).name),
                  meta={k: info.get(k) for k in
                        ('totalRuns', 'timedRuns', 'proposals', 'scheduleStep', 'improvements',
                         'lastImprovementRun', 'objectiveVersion', 'schema', 'scope')},
                  survivingRunRows=runs, candidates=len(candidates), archiveMembers=len(archive),
                  proposalAxes=json.loads(info['proposalAxes']) if info.get('proposalAxes') else None,
                  proposalLanes=json.loads(info['proposalLanes']) if info.get('proposalLanes') else None)

    # --- encounter scoping -------------------------------------------------
    encounters = collections.Counter(scn.get('encounterId') for _, scn, _, _, _ in candidates)
    report['encounters'] = dict(sorted(encounters.items(), key=lambda kv: str(kv[0])))
    report['tickLimits'] = dict(collections.Counter(
        scn.get('tickLimit') for _, scn, _, _, _ in candidates))

    # --- the searchable structure across the whole live population ----------
    ordered_lists, skill_sets, pairings, stat_vectors, formations, weapons = (set() for _ in range(6))
    per_unit = collections.defaultdict(lambda: dict(lists=set(), sets=set(), stats=collections.defaultdict(set)))
    skill_id_counts = collections.Counter()
    invocation_counts = collections.Counter()
    stat_values = collections.defaultdict(list)
    identical_payloads = collections.Counter()
    payload_of = {}
    order_only_pairs = 0
    unit_names = collections.Counter()
    for _cid, scn, _label, _source, _stats in candidates:
        units = scn.get('ownUnits') or []
        ordered_lists.add(json.dumps([[u.get('skills') for u in units]]))
        skill_sets.add(json.dumps(sorted(tuple(sorted(u.get('skills') or [])) for u in units)))
        pairings.add(json.dumps([sorted(map(list, skill_pairing(u))) for u in units]))
        formations.add(json.dumps([u.get('name') for u in units]))
        weapons.add(json.dumps([u.get('weaponId') for u in units]))
        stats_here = []
        for unit in units:
            name = unit.get('name')
            unit_names[name] += 1
            entry = per_unit[name]
            entry['lists'].add(json.dumps(unit.get('skills')))
            entry['sets'].add(json.dumps(sorted(unit.get('skills') or [])))
            for sid in unit.get('skills') or []:
                skill_id_counts[sid] += 1
            for level in unit.get('invocationLevels') or []:
                invocation_counts[level] += 1
            for pid in STAT_IDS:
                block = (unit.get('parameters') or {}).get(str(pid))
                if not block:
                    continue
                value = block.get('rawValue')
                stat_values[pid].append(value)
                entry['stats'][pid].add(value)
                stats_here.append([pid, value])
        stat_vectors.add(json.dumps(sorted(stats_here)))
        # A payload identity that ignores only the *order* of one unit's skills: two candidates
        # sharing it differ only by skill order (or not at all).
        payload = json.dumps([(unit.get('name'), unit_signature(unit),
                               sorted(map(list, skill_pairing(unit))))
                              for unit in units], sort_keys=True)
        identical_payloads[payload] += 1
        payload_of[_cid] = payload

    report['distinct'] = dict(
        orderedSkillLists=len(ordered_lists), skillSets=len(skill_sets),
        skillInvocationPairings=len(pairings), statVectors=len(stat_vectors),
        formations=len(formations), weaponAssignments=len(weapons),
        orderOnlyPayloads=sum(count for count in identical_payloads.values() if count > 1),
        exactDuplicatePayloads=sum(count - 1 for count in identical_payloads.values()))
    # Runs spent on candidates that are pure skill-ORDER variants of another live candidate: the
    # order-insensitive payload (stats, equipment, weapon, unit order and the skill/trigger multiset)
    # is shared, so the only searchable difference is the sequence inside `skills`.
    order_only_candidates = {cid for cid, payload in payload_of.items()
                             if identical_payloads[payload] > 1}
    order_only_runs = sum(run_by_candidate.get(cid, 0) for cid in order_only_candidates)
    report['orderPermutationShare'] = dict(
        candidates=len(order_only_candidates), candidateRuns=order_only_runs,
        survivingRunRows=runs,
        fractionOfSurvivingRuns=(order_only_runs / runs) if runs else 0.0,
        fractionOfTotalRuns=(order_only_runs / int(info.get('totalRuns') or 1)))
    report['skillIdsEquipped'] = dict(sorted(skill_id_counts.items()))
    report['invocationLevelsUsed'] = dict(sorted(invocation_counts.items()))
    report['stats'] = {
        str(pid): dict(count=len(values), distinct=len(set(values)), minimum=min(values),
                       maximum=max(values), median=statistics.median(values))
        for pid, values in sorted(stat_values.items()) if values}
    report['perUnit'] = {
        name: dict(distinctOrderedLists=len(entry['lists']), distinctSets=len(entry['sets']),
                   distinctStatValues={str(pid): len(vals)
                                       for pid, vals in sorted(entry['stats'].items())})
        for name, entry in sorted(per_unit.items())}

    # --- the generator the library was produced with ------------------------
    try:
        from strategy_optimizer_adapter import default_scenario
        base = default_scenario()
        report['baseline'] = dict(
            units=[dict(name=unit['name'], skills=unit['skills'],
                        invocationLevels=unit['invocationLevels'], weaponId=unit['weaponId'])
                   for unit in base['ownUnits']])
        base_sets = {unit['name']: sorted(unit['skills']) for unit in base['ownUnits']}
        base_names = set(base_sets)
        adds, removes, replaces = 0, 0, 0
        reachable = 0
        for _cid, scn, _label, _source, _stats in candidates:
            for unit in scn.get('ownUnits') or []:
                if unit['name'] not in base_names:
                    continue
                current = unit['skills']
                original = base_sets[unit['name']]
                if sorted(current) == original:
                    reachable += 1
                    continue
                new_ids = set(current) - set(original)
                gone_ids = set(original) - set(current)
                if len(current) > len(original) and not gone_ids:
                    adds += 1
                elif len(current) < len(original) and not new_ids:
                    removes += 1
                else:
                    replaces += 1
        report['baselineComparison'] = dict(unitsMatchingBaselineSkillSet=reachable,
                                            unitsWithAddedSkill=adds, unitsWithRemovedSkill=removes,
                                            unitsWithReplacedSkill=replaces)
    except Exception as error:  # noqa: BLE001 - the baseline is context, not the audit
        report['baseline'] = dict(error=f'{type(error).__name__}: {error}')

    # --- what the optimiser's own identity/tag layer recorded ---------------
    report['candidateSources'] = dict(collections.Counter(source for _, _, _, source, _ in candidates))
    report['candidateLabels'] = dict(collections.Counter(label for _, _, label, _, _ in candidates).most_common(12))
    try:
        holder = json.loads(info.get('recordHolders') or '{}')
        report['recordHolders'] = {key: {k: v for k, v in value.items() if k != 'scenario'}
                                   for key, value in holder.items()} if isinstance(holder, dict) else holder
    except Exception:  # noqa: BLE001
        report['recordHolders'] = None
    report['runsPerCandidate'] = dict(count=len(run_by_candidate),
                                      minimum=min(run_by_candidate.values()) if run_by_candidate else 0,
                                      maximum=max(run_by_candidate.values()) if run_by_candidate else 0,
                                      median=statistics.median(run_by_candidate.values())
                                      if run_by_candidate else 0)
    conn.close()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', default=str(DEFAULT_LIBRARY))
    parser.add_argument('--out', default=None)
    args = parser.parse_args(argv)
    report = audit(args.library)
    out = Path(args.out) if args.out else WORKSPACE / 'RE-evidence/20260922-search-contract' / 'library-audit.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('meta', 'survivingRunRows', 'candidates',
                                             'archiveMembers', 'distinct', 'tickLimits',
                                             'stats', 'baselineComparison', 'proposalAxes',
                                             'proposalLanes', 'candidateSources')},
                     indent=1))
    print('written', out)


if __name__ == '__main__':
    main()
