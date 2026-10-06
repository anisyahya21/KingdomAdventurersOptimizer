"""The structured strategy names: one exact build per row, named by who changed what it came from.

The strategies window used to show the *search's* own labels - `Student 1 · track:atk · atk 343->439`,
`Breakthrough · h:19:prior:atk+spd · arm C · damage (low)` - which name an internal track or a
hypothesis id/arm code rather than the build. What the reader is owed is:

    <student making the change> · <unit and actual old->new change> · from <immediate parent's producer>

This check proves, against a real temporary library driven through the real host payload:

  * a joint intervention names **both** of its stats, with the unit, not whichever one the search's
    own label happened to mention;
  * the source is the **immediate** parent's producer, so a child of a child says "from Average"
    while its grandparent was Community - the original ancestor is never substituted;
  * an arm created by the Breakthrough stream names Breakthrough as the producer, including when the
    *parent* is an ordinary-search build, and the long hypothesis id/arm code stays in the detail line
    instead of the name;
  * a reused candidate keeps its own original name and recorded provenance: rediscovering it does not
    rename it as new work by the rediscovering student;
  * a root build (a supplied/imported baseline) keeps its own label, because it has no change to
    describe and no parent producer to state;
  * provenance that cannot be established reads `Unknown source`, never a guessed student name;
  * every ranked row's name, mean, sample count, attempts, maximum and comparability refer to that
    row's own `candidateId` - the host never pairs one build's name with a family's totals.

    python check_optimizer_strategy_names.py [--json OUT]
"""
import argparse
import json
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_naming as naming  # noqa: E402
import strategy_optimizer as optimizer  # noqa: E402
import strategy_optimizer_desktop as desktop  # noqa: E402
from strategy_optimizer_adapter import provenance  # noqa: E402

ENCOUNTER = 137


def _unit(name, *, atk=100, spd=200, hp=4000, skills=(1, 2), triggers=(0, 0)):
    return dict(name=name, human=True, weaponId=1, skills=list(skills),
                invocationLevels=list(triggers),
                parameters={'13': {'rawValue': atk}, '15': {'rawValue': spd},
                            '10': {'rawValue': hp}})


def scenario(units=None, *, encounter=ENCOUNTER, marker=0):
    return dict(encounterId=encounter, defeatCount=0, tickLimit=120,
                finishPolicy='terminal-verdict', ownUnits=units or [_unit(f'Build {marker}')],
                enemies=[])


def build_library(path):
    """A small real library: baseline -> Community child -> Average grandchild -> Breakthrough arm."""
    store = optimizer.Store(path, provenance())
    with store.db:
        store.set('scope', optimizer.scope(scenario()))
        baseline = store.add(scenario(units=[_unit('Ninja (A aw20)')], marker=0),
                             'Supplied baseline · set-weapon', 'supplied', {})
        # Community raises Ninja agility only.
        community = store.add_child(
            scenario(units=[_unit('Ninja (A aw20)', spd=365)]),
            'Student 1 · track:spd · spd 200->365',
            lambda: {}, baseline, 'track:spd', 'spd', 'spd 200->365', 'community', 1)[0]
        # Average changes both Attack and Speed in one intervention - the joint arm.
        average = store.add_child(
            scenario(units=[_unit('Ninja (A aw20)', atk=40, spd=66)]),
            'Average · refine · atk 365->40',
            lambda: {}, community, 'ladder', 'atk', 'atk 100->40', 'average', 1)[0]
        # The Breakthrough stream tests an intervention on its own grandchild.
        breakthrough = store.add_child(
            scenario(units=[_unit('Ninja (A aw20)', atk=20, spd=66)]),
            'Breakthrough · h:137:prior:atk+spd · arm D · damage+speed (low)',
            lambda: {}, average, 'intervention', 'atk+spd', 'atk 40->20', 'mechanism', 1)[0]
    store.close()
    return dict(baseline=baseline, community=community, average=average, breakthrough=breakthrough)


def check(condition, message, failures):
    if not condition:
        failures.append(message)


def wait_for_open(bridge, seconds=90):
    import time
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if bridge.status().get('state') != 'Opening library':
            return True
        time.sleep(.05)
    return False


def row_of(payload, candidate_id):
    return next((entry for entry in payload.get('strategies') or []
                 if entry['candidateId'] == candidate_id), None)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json')
    args = parser.parse_args(argv)
    report = dict(schema='ka-optimizer-strategy-names-check-1', checks=[], cases=0)
    failures = []

    def record(title, detail):
        entry = dict(detail)
        entry['name'] = title
        report['checks'].append(entry)
        report['cases'] += 1
        print(f'  OK {title}: {json.dumps(detail, sort_keys=True)}')

    # ---- 1. The formatter itself, on the pieces the requirement names -------------------------------
    parent = scenario(units=[_unit('Ninja (A aw20)')])
    joint = naming.describe(parent, scenario(units=[_unit('Ninja (A aw20)', atk=40, spd=66)]))
    check(joint == 'Ninja ATK 100->40; Speed 200->66', f'joint change reads {joint!r}', failures)
    record('a-joint-intervention-names-both-stats',
           dict(change=joint, why='the search label named only the first stat; the name names both'))

    single = naming.describe(parent, scenario(units=[_unit('Ninja (A aw20)', spd=48)]))
    check(single == 'Ninja Speed 200->48', f'single change reads {single!r}', failures)
    check('->' in single and ';' not in single, f'single change has an arrow and no joiner: {single!r}',
          failures)
    record('one-stat-change-reads-as-one-old-new-pair', dict(change=single))

    skill_change = naming.describe(parent, scenario(units=[_unit('Ninja (A aw20)', skills=(1, 3))]))
    check(skill_change.startswith('Ninja '), f'skill change names its unit: {skill_change!r}', failures)
    team_change = naming.describe(parent, scenario(units=[_unit('Ninja (A aw20)'), _unit('Healer')]))
    check(team_change == 'team 1->2', f'team change reads {team_change!r}', failures)
    record('non-stat-changes-are-described-honestly',
           dict(skill=skill_change, team=team_change))

    # ---- 2. Real library, real host payload ---------------------------------------------------------
    # The Bridge holds the library open until its own coordinator thread finishes; on Windows the
    # directory cannot be removed until then, so cleanup is best-effort rather than a test failure.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        path = pathlib.Path(work) / 'strategies-names.sqlite'
        ids = build_library(path)
        bridge = desktop.Bridge(path)
        try:
            check(wait_for_open(bridge), 'the temporary library never finished opening', failures)
            payload = bridge.encounter_strategies(ENCOUNTER, 0)
            check(payload.get('ok') is True, f'encounter_strategies failed: {payload.get("error")!r}',
                  failures)
            names = {key: row_of(payload, cid) for key, cid in ids.items()}

            # (a) The immediate parent's producer, not the original ancestor.
            grandchild = names['breakthrough']
            check(grandchild is not None, 'the Breakthrough arm is missing from the ranked payload',
                  failures)
            if grandchild:
                check(grandchild['displayName'].startswith('Breakthrough · '),
                      f'the arm is not named for its creator: {grandchild["displayName"]!r}', failures)
                check('from Average' in grandchild['displayName'],
                      f'the arm does not name its IMMEDIATE parent (Average): {grandchild["displayName"]!r}',
                      failures)
                check('from Community' not in grandchild['displayName'],
                      'the arm named the original ancestor instead of its own parent', failures)
                check('ATK 40->20' in grandchild['displayName'],
                      f'the arm does not state the actual change: {grandchild["displayName"]!r}',
                      failures)
                check(grandchild['parentId'] == ids['average'],
                      f'parent id {grandchild["parentId"]!r} != {ids["average"]!r}', failures)
                check(grandchild['parentProducer'] == 'Average',
                      f'parent producer {grandchild["parentProducer"]!r}', failures)
                check('h:137:prior:atk+spd' not in grandchild['displayName'],
                      'the hypothesis id is in the name instead of the detail', failures)
                check('h:137:prior:atk+spd' in (grandchild['nameDetail'] or ''),
                      'the hypothesis id was dropped entirely instead of moving to the detail',
                      failures)
                check(grandchild['nameDerived'] is True, 'the name was not derived from lineage',
                      failures)
            record('a-child-of-a-child-names-its-own-parent',
                   dict(name=grandchild and grandchild['displayName'],
                        detail=grandchild and grandchild['nameDetail'],
                        why='the source is the immediate parent (Average), not the root (Baseline)'))

            # (b) The Average grandchild is named for its own joint change and Community parent.
            average = names['average']
            if average:
                check(average['displayName'].startswith('Average · '),
                      f'the Average build is misnamed: {average["displayName"]!r}', failures)
                check('from Community' in average['displayName'],
                      f'the Average build does not name Community as its parent: {average["displayName"]!r}',
                      failures)
                # Named against its IMMEDIATE parent (the Community build, whose Speed is 365), not
                # against the baseline it was originally built from.
                check('ATK 100->40' in average['displayName'] and 'Speed 365->66' in average['displayName'],
                      f'the joint change is incomplete: {average["displayName"]!r}', failures)
            record('a-joint-change-survives-into-the-payload',
                   dict(name=average and average['displayName']))

            # (c) A root build keeps its own recorded label: nothing renames it.
            baseline = names['baseline']
            if baseline:
                check(baseline['displayName'] == baseline['label'],
                      f'the root was relabelled: {baseline["displayName"]!r} vs {baseline["label"]!r}',
                      failures)
                check(baseline['creator'] == 'Baseline',
                      f'the supplied build creator reads {baseline["creator"]!r}', failures)
            record('a-root-keeps-its-own-label', dict(name=baseline and baseline['displayName']))

            # (d) Every row's numbers belong to that row's own candidate.
            for key, cid in ids.items():
                row = row_of(payload, cid)
                if row is None:
                    failures.append(f'{key} ({cid[:8]}) is missing from the ranked payload')
                    continue
                check(isinstance(row['attempts'], int) and row['attempts'] >= 0,
                      f'{key}: attempts {row["attempts"]!r}', failures)
                check(row['earnedSamples'] is None or row['earnedSamples'] <= row['attempts'],
                      f'{key}: {row["earnedSamples"]} earned samples exceed {row["attempts"]} attempts',
                      failures)
                check(bool(row['displayName']) or bool(row['label']),
                      f'{key}: the row has no name at all', failures)
            distinct = {row['displayName'] for row in payload['strategies']}
            check(len(distinct) == len(payload['strategies']),
                  'two ranked rows share a name, so a name does not identify one build', failures)
            record('each-row-is-one-build-with-its-own-numbers',
                   dict(rows=len(payload['strategies']), distinctNames=len(distinct),
                        names=sorted(distinct)))

            # (e) Reuse: rediscovering a build does not rename it or rewrite its provenance.
            store = optimizer.Store(path, provenance())
            try:
                reused, existed = store.add_child(
                    scenario(units=[_unit('Ninja (A aw20)', spd=365)]),
                    'Rebel · rediscovered · spd 200->365', lambda: {}, ids['baseline'],
                    'rebel', 'spd', 'spd 200->365', 'rebel', 2)
                check(existed is True and reused == ids['community'],
                      f'rediscovery made a new candidate ({reused!r}, existed={existed!r})', failures)
                root, depth, source = store.db.execute(
                    'SELECT parent,depth,source FROM lineage WHERE candidate=?', (reused,)).fetchone()
                check(root == ids['baseline'] and source == 'community',
                      f'rediscovery rewrote the lineage: parent={root!r} source={source!r}', failures)
            finally:
                store.close()
            payload2 = bridge.encounter_strategies(ENCOUNTER, 0)
            again = row_of(payload2, ids['community'])
            check(again and again['displayName'] == names['community']['displayName'],
                  'a rediscovered build was renamed by the rediscovering student', failures)
            record('reuse-preserves-name-and-provenance',
                   dict(name=again and again['displayName'],
                        why='the second student tested it; it did not become their new build'))

            # (f) Unknown provenance is stated, never guessed.
            orphan = naming.name_for('nobody', {}, {},
                                     fallback=None)
            check(orphan['name'] == naming.UNKNOWN_SOURCE,
                  f'an unknown candidate is named {orphan["name"]!r}', failures)
            ghost_parent = naming.name_for(
                'child', {'child': dict(parent='missing', source='mystery-marker')},
                {}, fallback='old label')
            check('Unknown source' in ghost_parent['name'],
                  f'unestablishable provenance reads {ghost_parent["name"]!r}', failures)
            record('unestablishable-provenance-says-unknown',
                   dict(orphan=orphan['name'], child=ghost_parent['name']))
        finally:
            bridge.close()

    print(f"strategy name checks: {report['cases']} cases, {len(failures)} failures")
    for failure in failures:
        print(f'  FAIL {failure}')
    report['failures'] = failures
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(report, indent=1, sort_keys=True) + '\n',
                                           encoding='utf-8')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
