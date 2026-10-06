"""Focused checks for the on-demand diagnostic export (`strategy_diagnostic_export`).

Scratch SQLite libraries only - no real library, no simulator and no battle is run. The checks pin the
contracts the export must keep: newest-first monotonic ordering with a cutoff frozen at the start,
one battle per sampled row (never one per link), exact build details per referenced candidate, a
field-level change diff with human labels, worker/coordinator diagnostics only, a hard file cap that
fails safe without overwriting anything, the legacy `run` fallback when the ea_* tables hold no
evidence, and a source library whose bytes never move.

    python check_strategy_diagnostic_export.py
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
import types
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import strategy_diagnostic_export as dx
import strategy_experiment_store as store

CHECKS = 0


def check(condition, label):
    global CHECKS
    if not condition:
        raise AssertionError(label)
    CHECKS += 1


def scratch():
    """A writable scratch directory under the repo's own tmp/, removed by the caller."""
    base = Path(__file__).resolve().parents[2] / 'tmp' / 'encounter-redesign-20260928'
    base.mkdir(parents=True, exist_ok=True)
    root = base / ('_dx-check-%d' % os.getpid())
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    return root


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def remove_tree(path):
    """Remove a scratch tree, retrying briefly: Windows can hold a closed SQLite file for a moment."""
    for _attempt in range(10):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError:
            time.sleep(0.05)
    shutil.rmtree(path, ignore_errors=True)


CANDIDATE_SQL = (
    'CREATE TABLE candidate(id TEXT PRIMARY KEY, scenario TEXT NOT NULL, label TEXT NOT NULL, '
    'source TEXT NOT NULL, stats TEXT NOT NULL, created INTEGER NOT NULL)'
)
LINEAGE_SQL = 'CREATE TABLE lineage(candidate TEXT PRIMARY KEY, parent TEXT, source TEXT)'
RUN_SQL = ('CREATE TABLE run(candidate TEXT NOT NULL, phase TEXT NOT NULL, ordinal INTEGER NOT NULL, '
           'result TEXT NOT NULL, PRIMARY KEY(candidate,phase,ordinal))')

BASE_SCENARIO = {"ownUnits": [{"name": "Ninja", "parameters": {"10": {"rawValue": 100},
                                                               "13": {"rawValue": 40}}}], "items": {}}
CHILD_SCENARIO = {"ownUnits": [{"name": "Ninja", "parameters": {"10": {"rawValue": 100},
                                                                "13": {"rawValue": 20}}}], "items": {}}


def _result(seed_a, seed_b, **extra):
    payload = dict(seeds=[seed_a, seed_b], verdict=1, censored=False, prizeCallbacks=2,
                   rewardOutcome=dict(awardedChests=1, awardedBasis='certified', pendingChests=0))
    payload.update(extra)
    return payload


def _add_candidates(db, child=True):
    db.execute('INSERT INTO candidate VALUES(?,?,?,?,?,?)',
               ('a', json.dumps(BASE_SCENARIO), 'build-a', 'community', json.dumps({'runs': 4}), 1))
    if child:
        db.execute('INSERT INTO candidate VALUES(?,?,?,?,?,?)',
                   ('b', json.dumps(CHILD_SCENARIO), 'build-b', 'average',
                    json.dumps({'runs': 9}), 2))


def _add_experiment(db, request_key, reference_id, changed, purpose='improvement'):
    db.execute(
        'INSERT INTO ea_experiment(request_key,scope,parent_id,reference_id,policy,'
        'mechanics_revision,encounter_revision,purpose,owner,owner_share,fixed_fields,changed_fields,'
        'planned_budget,stopping,session_id,measurement_window,created_at,intent_json) '
        'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (request_key, 'community', None, reference_id, None, 'm1', 'e1', purpose, 'community', 1.0,
         '{}', json.dumps(changed), 6, json.dumps({'rule': 'fixed-workload', 'battles': 6}),
         None, None, 10.0, json.dumps({'question': 'does the lower ATK still win?'})))


def _add_sample(db, sample_key, seed_a, seed_b, result=None, created=100.0):
    db.execute(
        'INSERT INTO ea_sample(sample_key,candidate_id,policy,mechanics_revision,'
        'encounter_revision,seed_a,seed_b,measurement_window,result,outcome,timing,created_at) '
        'VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
        (sample_key, 'b', None, 'm1', 'e1', seed_a, seed_b, None,
         None if result is None else json.dumps(result),
         None if result is None else json.dumps({'chests': result['rewardOutcome']['awardedChests']}),
         None if result is None else json.dumps({'completedAt': created}), created))


def build_ea(root):
    """A Community library whose battles live in ea_sample, with one oversized legacy-style trace."""
    path = root / 'ea.sqlite'
    db = sqlite3.connect(str(path))
    db.executescript(CANDIDATE_SQL + ';' + LINEAGE_SQL + ';')
    store.initialize(db)
    _add_candidates(db)
    _add_experiment(db, 'rk1', 'a', ['ownUnits.0.parameters.13.rawValue'])
    _add_experiment(db, 'rk2', 'a', [], purpose='support')
    for index in range(5):
        _add_sample(db, 's%d' % index, 10 + index, 20 + index, _result(10 + index, 20 + index),
                    created=100.0 + index)
    _add_sample(db, 'pending', 90, 91, None, created=200.0)
    hostile = _result(99, 100)
    hostile['replay'] = {'events': 'x' * 2_000_000}
    _add_sample(db, 'hostile', 99, 100, hostile, created=300.0)
    # s0 is requested by two experiments at once: one charged owner, one reuse. It is ONE battle.
    db.execute('INSERT INTO ea_sample_link VALUES(?,?,?,?,?,?,?,?)',
               (1, 'b', 10, 20, 's0', 0, 1, 100.0))
    db.execute('INSERT INTO ea_sample_link VALUES(?,?,?,?,?,?,?,?)',
               (2, 'b', 10, 20, 's0', 1, 1, 101.0))
    db.execute('INSERT INTO ea_sample_link VALUES(?,?,?,?,?,?,?,?)',
               (1, 'b', 99, 100, 'hostile', 0, 1, 300.0))
    db.commit()
    db.close()
    return path


def build_many(root, count=40, blob=2000):
    """A Community library with many sizeable samples, for the cap checks."""
    path = root / 'many.sqlite'
    db = sqlite3.connect(str(path))
    db.executescript(CANDIDATE_SQL + ';' + LINEAGE_SQL + ';')
    store.initialize(db)
    _add_candidates(db)
    _add_experiment(db, 'rk1', 'a', ['ownUnits.0.parameters.13.rawValue'])
    for index in range(count):
        _add_sample(db, 'm%d' % index, index, index + 1,
                    _result(index, index + 1, notes='y' * blob), created=100.0 + index)
    db.commit()
    db.close()
    return path


def build_legacy(root, with_ea=False):
    """A legacy library whose results live in `run`; optionally with the empty ea_* tables present."""
    path = root / ('legacy-ea.sqlite' if with_ea else 'legacy.sqlite')
    db = sqlite3.connect(str(path))
    db.executescript(CANDIDATE_SQL + ';' + LINEAGE_SQL + ';' + RUN_SQL + ';')
    if with_ea:
        store.initialize(db)
    _add_candidates(db)
    db.execute("INSERT INTO lineage VALUES('b','a','average')")
    for ordinal in range(3):
        db.execute('INSERT INTO run VALUES(?,?,?,?)',
                   ('b', 'validation', ordinal, json.dumps(_result(ordinal, ordinal + 5,
                                                                    trace={'events': [1, 2, 3]}))))
    db.commit()
    db.close()
    return path


def read_zip(path):
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        battles = [json.loads(line) for line in zf.read('battles.jsonl').decode('utf-8').splitlines()]
        builds = [json.loads(line) for line in zf.read('builds.jsonl').decode('utf-8').splitlines()]
        diagnostics = json.loads(zf.read('diagnostics.json'))
        manifest = json.loads(zf.read('manifest.json'))
        sizes = {name: zf.getinfo(name).file_size for name in names}
    return names, battles, builds, diagnostics, manifest, sizes


def check_ea(root):
    source = build_ea(root)
    out = root / 'ea.zip'
    inserted = {}

    def progress(stage, **fields):
        if stage == 'writing' and not inserted:
            inserted['done'] = True
            writer = sqlite3.connect(str(source))
            writer.execute('INSERT INTO ea_sample(sample_key,candidate_id,seed_a,seed_b,result,'
                           'created_at) VALUES(?,?,?,?,?,?)',
                           ('late', 'b', 500, 501, json.dumps(_result(500, 501)), 999.0))
            writer.commit()
            writer.close()
            inserted['hash'] = sha(source)

    result = dx.export(source, out, 6,
                       diagnostics={'state': 'Running', 'error': None, 'scheduler': {'workers': 12},
                                    'candidates': [{'not': 'a diagnostic'}]},
                       progress=progress)
    check(inserted.get('done'), 'the mid-export row insert actually happened')
    check(result['sourceKind'] == 'ea_sample', 'ea_sample is the source when it holds results')
    check(result['counts']['cutoffRowid'] == 7, 'the cutoff is the max completed rowid at start')
    check(result['included'] == 6 and result['truncated'] == 0, 'all requested battles were included')
    check(not result['libraryHeldFewer'] and not result['truncatedByCap'], 'no truncation was needed')
    names, battles, builds, diag, manifest, sizes = read_zip(out)
    check(names == ['README.txt', 'battles.jsonl', 'builds.jsonl', 'diagnostics.json',
                    'manifest.json'], 'the archive has the documented files')
    rowids = [b['rowid'] for b in battles]
    check(rowids == [7, 5, 4, 3, 2, 1], 'battles are newest-first by monotonic rowid')
    check(all(rid <= 7 for rid in rowids), 'no row past the frozen cutoff entered the window')
    check('late' not in {b.get('sampleKey') for b in battles}, 'a row written mid-export stays out')
    s0 = [b for b in battles if b['sampleKey'] == 's0']
    check(len(s0) == 1, 'one battle per sampled row, not one per link')
    check(len(s0[0]['experiments']) == 2, 'both requesting experiments are kept as provenance')
    hostile = [b for b in battles if b['sampleKey'] == 'hostile'][0]
    check('replay' not in (hostile['result'] or {}), 'a known trace field is dropped from the result')
    check(hostile['traceOmitted'] and hostile['replayTracesIncluded'] is False,
          'the row states the replay is omitted')
    check(sizes['battles.jsonl'] < 200_000, 'a 2 MB replay is not transported into the archive')
    check(not any('replay' in (b['result'] or {}) for b in battles),
          'no battle carries a replay field')
    latest = battles[0]
    check(latest['seedPair'] == [99, 100], 'the ordered seed pair is recorded')
    check(latest['changes']['available'] and 'ATK 40->20' in (latest['changes']['human'] or ''),
          'the change diff is human labelled')
    check('ownUnits.0.parameters.13.rawValue' in (latest['changes'].get('declaredChangedFields') or []),
          'the declared changed field is kept')
    exp = latest['experiments'][0]['experiment']
    check(exp['question'] == 'does the lower ATK still win?' and exp['purpose'] == 'improvement',
          'the experiment question and purpose are kept')
    check(latest['uncertainty']['singleSample'] is True and latest['uncertainty']['stopping'],
          'uncertainty names the single sample and the stopping rule')
    by_id = {b['candidateId']: b for b in builds}
    check(set(by_id) >= {'a', 'b'}, 'every referenced candidate has a build entry')
    check(by_id['b']['available'] and by_id['b']['scenario'] == CHILD_SCENARIO,
          'the exact frozen scenario is included once')
    check('label' in by_id['b'], 'the build carries its label')
    check(diag['optimizer'].get('scheduler') == {'workers': 12},
          'worker/coordinator diagnostics are kept')
    check('candidates' not in diag['optimizer'],
          'the large candidate list is not copied as diagnostics')
    check('not included' in diag['omissions']['replayTraces'],
          'the omissions state replays are not included')
    check(manifest['counts']['included'] == 6, 'the manifest carries the counts')
    check(sha(source) == inserted['hash'], 'reading the library leaves its bytes unchanged')
    check(not (root / 'ea.zip.partial').exists(), 'no partial file is left behind')


def check_legacy(root):
    source = build_legacy(root)
    before = sha(source)
    out = root / 'legacy.zip'
    result = dx.export(source, out, 2)
    check(result['sourceKind'] == 'legacy_run', 'the legacy run table is used when ea_* is absent')
    names, battles, builds, diag, manifest, sizes = read_zip(out)
    check([b['ordinal'] for b in battles] == [2, 1], 'legacy rows are newest-first by rowid')
    check(battles[0]['seedPair'] == [2, 7], 'the seed pair is read from the recorded result')
    check(battles[0]['changes']['available']
          and 'ATK 40->20' in (battles[0]['changes']['human'] or ''),
          'the legacy parent diff is computed from lineage and the stored scenarios')
    check('trace' not in (battles[0]['result'] or {}), 'a legacy trace field is dropped')
    check(any('legacy run' in note for note in (battles[0]['notes'] or [])),
          'the legacy row states it has no canonical outcome row')
    check(sha(source) == before, 'a legacy library is left byte-identical')

    empty_ea = build_legacy(root, with_ea=True)
    out2 = root / 'legacy-ea.zip'
    result2 = dx.export(empty_ea, out2, 2)
    check(result2['sourceKind'] == 'legacy_run', 'empty ea_* tables fall back to the legacy run rows')
    check('no completed ea_sample rows' in ' '.join(read_zip(out2)[4]['notes']),
          'the fallback is explained in the manifest')


def check_cap(root):
    source = build_many(root, count=40, blob=2000)
    before = sha(source)
    out = root / 'capped.zip'
    cap = 40_000
    result = dx.export(source, out, 40, max_bytes=cap, uncompressed_cap=cap)
    check(os.path.getsize(out) <= cap, 'the archive never exceeds the injected cap')
    check(result['truncatedByCap'] and result['included'] < 40,
          'the cap truncated the window instead of oversizing the file')
    check(result['truncated'] == 40 - result['included'], 'the truncated count is exact')
    names, battles, builds, diag, manifest, sizes = read_zip(out)
    check(len(battles) == result['included'], 'the written battles match the reported count')
    covered = {b['candidateId'] for b in battles}
    check(covered <= {b['candidateId'] for b in builds},
          'every included battle has its referenced build details')
    check(sha(source) == before, 'a capped export leaves the source unchanged')

    protected = root / 'protected.zip'
    protected.write_bytes(b'ORIGINAL')
    try:
        dx.export(source, protected, 40, max_bytes=500, uncompressed_cap=500)
        raise AssertionError('an impossibly small cap should have failed safe')
    except ValueError as exc:
        check('cap' in str(exc), 'the fail-safe error names the cap')
    check(protected.read_bytes() == b'ORIGINAL', 'a failed export never overwrites the destination')


def check_empty(root):
    path = root / 'empty.sqlite'
    db = sqlite3.connect(str(path))
    db.execute('CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)')
    db.commit()
    db.close()
    out = root / 'empty.zip'
    result = dx.export(path, out, 5)
    check(result['sourceKind'] == 'none' and result['included'] == 0,
          'an empty library exports zero battles')
    check(result['truncated'] == 5 and result['libraryHeldFewer'],
          'the shortfall is reported accurately')
    names, battles, builds, diag, manifest, sizes = read_zip(out)
    check(battles == [] and builds == [], 'the archive is still well formed with no battles')
    check(bool(manifest['notes']), 'the manifest explains that no battle table is present')
    check(os.path.getsize(out) <= dx.MAX_EXPORT_BYTES, 'an empty export stays under the cap')


def check_errors(root):
    target = root / 'errors'
    target.mkdir(parents=True, exist_ok=True)
    source = build_ea(target)
    before = sha(source)
    try:
        dx.export(source, source, 5)
        raise AssertionError('exporting onto the library must be refused')
    except ValueError as exc:
        check('must not be the library' in str(exc), 'exporting onto the source is refused')
    check(sha(source) == before, 'the refused export leaves the library unchanged')


def check_diagnostics_bound(root):
    huge = {'state': 'Running', 'scheduler': {'pad': 'z' * 200_000}, 'error': 'boom'}
    view = dx.diagnostic_view(huge, budget=10_000)
    check(view.get('state') == 'Running' and view.get('error') == 'boom',
          'small diagnostics are kept')
    check('scheduler' not in view and 'scheduler' in view.get('_omittedDiagnosticKeys', []),
          'an oversized diagnostic block is dropped by name, not silently')

    target = root / 'diag'
    target.mkdir(parents=True, exist_ok=True)
    source = build_many(target, count=2, blob=10)
    out = root / 'diag.zip'
    dx.export(source, out, 2,
              diagnostics={'state': 'Running', 'candidates': [{'x': 1}], 'error': 'e'})
    _, _, _, diag, _, _ = read_zip(out)
    check('candidates' not in diag['optimizer'], 'only diagnostic keys are copied from the snapshot')
    check(diag['omissions']['fullCompactOutcomes'],
          'the archive states compact outcomes are retained')


def check_counts(root):
    check(dx.normalize_count(None) == 1000, 'the default count is 1000')
    check(dx.normalize_count('') == 1000 and dx.normalize_count('  ') == 1000,
          'a blank draft uses the default')
    check(dx.normalize_count('5') == 5, 'a numeric string is accepted')
    bad_ok = True
    for bad in (0, -1, 1_000_001, 'abc', '1.5', True):
        try:
            dx.normalize_count(bad)
            bad_ok = False
        except ValueError:
            pass
    check(bad_ok, 'out-of-range and non-integer counts are refused')


def check_bridge(root):
    """The Bridge hands back a job id at once, refuses overlap, and never accepts the source as target."""
    fake = types.ModuleType('webview')
    fake.FileDialog = types.SimpleNamespace(OPEN=1, SAVE=2)
    sys.modules.setdefault('webview', fake)
    from strategy_optimizer_desktop import Bridge

    target = root / 'bridge'
    target.mkdir(parents=True, exist_ok=True)
    source = build_ea(target)
    destination = root / 'bridge.zip'
    bridge = Bridge.__new__(Bridge)
    bridge._library = source
    bridge._export = None
    bridge._window = types.SimpleNamespace(create_file_dialog=lambda *a, **k: [str(destination)])
    bridge.status = lambda: {'state': 'Running', 'error': None, 'scheduler': {'workers': 4}}

    started = bridge.export_diagnostics(3)
    check(started.get('ok') and started.get('jobId'), 'export_diagnostics returns a job id at once')
    check(started.get('path') == str(destination), 'the job reports the chosen destination')
    deadline = time.monotonic() + 15
    status = {}
    while time.monotonic() < deadline:
        status = bridge.export_diagnostics_status(started['jobId'])
        if status.get('state') != 'running':
            break
        time.sleep(0.05)
    check(status.get('state') == 'done' and status.get('included') == 3,
          'the background job finishes with the requested battles')
    check(destination.is_file(), 'the finished archive exists at the chosen path')
    check(bridge.export_diagnostics_status('not-the-job').get('ok') is False,
          'a stale job id is refused')

    bridge._export = types.SimpleNamespace(running=lambda: True, job_id='busy')
    overlap = bridge.export_diagnostics(3)
    check(overlap.get('ok') is False and 'already running' in (overlap.get('error') or ''),
          'a second export is refused while one is running')

    bridge._export = None
    bridge._window = types.SimpleNamespace(create_file_dialog=lambda *a, **k: [str(source)])
    same = bridge.export_diagnostics(3)
    check(same.get('ok') is False and 'library itself' in (same.get('error') or ''),
          'the library itself is refused as the destination')


def main():
    root = scratch()
    try:
        check_counts(root)
        check_ea(root)
        check_legacy(root)
        check_cap(root)
        check_empty(root)
        check_errors(root)
        check_diagnostics_bound(root)
        check_bridge(root)
        print('strategy diagnostic export: %d checks passed' % CHECKS, flush=True)
        return 0
    finally:
        remove_tree(root)


if __name__ == '__main__':
    raise SystemExit(main())
