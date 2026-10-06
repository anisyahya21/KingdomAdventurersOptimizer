"""Read-only, lineage-held-out trial of CPU and GPU strategy predictors.

Install optional experiment dependencies in a separate environment, then run this
against a paused or live WAL library. No optimizer state or result is changed.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import sqlite3
import time
import zlib

import numpy as np
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupShuffleSplit
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
import xgboost as xgb


PARAMETERS = ('10', '11', '12', '13', '14', '15', '16', '18', '19', '20', '21', '22')


def chest_count(result):
    if result.get('censored') or result.get('verdict') is None:
        return None
    if result['verdict'] == 2:
        return 0
    if result['verdict'] != 1:
        return None
    reward = result.get('rewardOutcome') or {}
    for value in (reward.get('awardedChests'), reward.get('pendingChests'),
                  result.get('prizeCallbacks')):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
    return None


def features(scenario, stats):
    # Use only the frozen proposal itself. Historical candidate.stats were computed
    # under earlier display/setup code and can differ from stats() computed today;
    # mixing them makes prospective predictions out-of-distribution.
    units = scenario.get('ownUnits') or []
    result = {
        f'encounter:{scenario["encounterId"]}': 1.,
        'defeat': float(scenario.get('defeatCount') or 0),
        'team_size': float(len(units)),
        'humans': float(sum(bool(u.get('human')) for u in units)),
        'monsters': float(sum(not u.get('human') for u in units)),
        'leaders': float(sum(bool(u.get('leaderIdentity')) for u in units)),
        'inputs': float(len(scenario.get('inputs') or [])),
        'holy_herbs': float(scenario.get('holyHerbStock') or 0),
    }
    skill_counts = Counter()
    weapon_counts = Counter()
    parameter_values = defaultdict(list)
    for unit in units:
        weapon_counts[int(unit.get('weaponId') or 0)] += 1
        for position, skill in enumerate(unit.get('skills') or []):
            skill_counts[int(skill)] += 1
            result[f'skill_early:{skill}'] = result.get(f'skill_early:{skill}', 0.) + 1./(1+position)
        for pid in PARAMETERS:
            # JSON storage stringifies parameter IDs; the legal mutator returns
            # in-memory integer keys. Both represent the same combat input.
            parameters = unit.get('parameters') or {}
            raw = parameters.get(pid) or parameters.get(int(pid)) or {}
            value = (raw.get('rawValue') or 0)+(raw.get('extraValue') or 0)
            parameter_values[pid].append(max(0., float(value)))
    for skill, count in skill_counts.items():
        result[f'skill:{skill}'] = float(count)
    for weapon, count in weapon_counts.items():
        result[f'weapon:{weapon}'] = float(count)
    for pid, values in parameter_values.items():
        result[f'param:{pid}:sum'] = math.log1p(sum(values))
        result[f'param:{pid}:max'] = math.log1p(max(values))
        result[f'param:{pid}:mean'] = math.log1p(sum(values)/len(values))
    return result


def load_candidates(path, minimum_runs=32):
    uri = Path(path).resolve().as_uri()+'?mode=ro'
    db = sqlite3.connect(uri, uri=True, timeout=30)
    db.execute('BEGIN')
    try:
        rows = db.execute('''
            SELECT c.id,c.scenario,c.stats,c.created,l.root
            FROM candidate c LEFT JOIN lineage l ON l.candidate=c.id
            ORDER BY c.created,c.id''').fetchall()
        candidates = {}
        for cid, scenario_json, stats_json, created, root in rows:
            scenario = json.loads(scenario_json)
            candidates[cid] = dict(id=cid, encounter=int(scenario['encounterId']),
                root=root or cid, created=created,
                features=features(scenario, json.loads(stats_json)), wins=0,
                losses=0, chest_total=0., chest_n=0)
        for cid, result_json in db.execute('''
                SELECT r.candidate,r.result FROM run r JOIN candidate c ON c.id=r.candidate
                WHERE r.phase='validation' AND r.ordinal<64
                ORDER BY r.candidate,r.ordinal'''):
            result = json.loads(result_json)
            chest = chest_count(result)
            if chest is None:
                continue
            item = candidates[cid]
            item['wins'] += result.get('verdict') == 1
            item['losses'] += result.get('verdict') == 2
            item['chest_total'] += chest
            item['chest_n'] += 1
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='predictor_history'").fetchone():
            for cid, packed, root, encounter, created, discovery, validation in db.execute('''
                    SELECT candidate,scenario_zlib,root,encounter,created,discovery,validation
                    FROM predictor_history'''):
                if cid in candidates:
                    continue
                aggregate = json.loads(validation or discovery or '{}')
                count = int(aggregate.get('chestCount') or 0)
                if count < minimum_runs:
                    continue
                scenario = json.loads(zlib.decompress(packed))
                candidates[cid] = dict(id=cid, encounter=int(encounter),
                    root=root, created=created, features=features(scenario, {}),
                    wins=int(aggregate.get('wins') or 0),
                    losses=int(aggregate.get('losses') or 0),
                    chest_total=float(aggregate.get('chestSum') or 0), chest_n=count)
        return [item for item in candidates.values() if item['chest_n'] >= minimum_runs]
    finally:
        db.close()


def pair_accuracy(candidates, prediction):
    by_encounter = defaultdict(list)
    for item, guess in zip(candidates, prediction):
        by_encounter[item['encounter']].append((item['chest_total']/item['chest_n'], guess))
    correct = pairs = 0
    for rows in by_encounter.values():
        for i, (actual_a, predicted_a) in enumerate(rows):
            for actual_b, predicted_b in rows[i+1:]:
                if abs(actual_a-actual_b) < .5:
                    continue
                pairs += 1
                correct += (predicted_a-predicted_b)*(actual_a-actual_b) > 0
                correct += .5*((predicted_a-predicted_b) == 0)
    return (correct/pairs if pairs else None, pairs)


def evaluate(candidates, predictions, baseline):
    actual_win = np.array([x['wins']/x['chest_n'] for x in candidates])
    actual_chest = np.array([x['chest_total']/x['chest_n'] for x in candidates])
    win_pred, chest_pred = predictions
    win_base, chest_base = baseline
    result = {
        'win_brier': float(np.mean((win_pred-actual_win)**2)),
        'baseline_win_brier': float(np.mean((win_base-actual_win)**2)),
        'chest_mae': float(np.mean(abs(chest_pred-actual_chest))),
        'baseline_chest_mae': float(np.mean(abs(chest_base-actual_chest))),
        'chest_pair_accuracy': pair_accuracy(candidates, chest_pred)[0],
        'chest_pairs': pair_accuracy(candidates, chest_pred)[1],
    }
    viable = actual_win >= .8
    if len(set(viable)) == 2:
        result['viability_auc'] = float(roc_auc_score(viable, win_pred))
        result['baseline_viability_auc'] = float(roc_auc_score(viable, win_base))
    return result


def fit_fold(candidates, train_index, test_index, matrix, device):
    train = [candidates[i] for i in train_index]
    test = [candidates[i] for i in test_index]
    x_train, x_test = matrix[train_index], matrix[test_index]
    by_encounter = defaultdict(list)
    for item in train:
        by_encounter[item['encounter']].append(item)
    global_wins = sum(x['wins'] for x in train)/sum(x['chest_n'] for x in train)
    global_chests = sum(x['chest_total'] for x in train)/sum(x['chest_n'] for x in train)
    baseline_win, baseline_chest = [], []
    for item in test:
        peers = by_encounter.get(item['encounter']) or []
        baseline_win.append(sum(x['wins'] for x in peers)/sum(x['chest_n'] for x in peers)
                            if peers else global_wins)
        baseline_chest.append(sum(x['chest_total'] for x in peers)/sum(x['chest_n'] for x in peers)
                              if peers else global_chests)
    baseline_win = np.array(baseline_win)
    baseline_chest = np.array(baseline_chest)
    y_binary = np.array([0, 1]*len(train))
    x_binary = np.repeat(x_train, 2, axis=0)
    weights = np.array([part for x in train for part in (x['losses'], x['wins'])], dtype=float)
    started = time.perf_counter()
    logistic = make_pipeline(StandardScaler(), LogisticRegression(C=.1, max_iter=500))
    logistic.fit(x_binary, y_binary, logisticregression__sample_weight=weights)
    win_pred = logistic.predict_proba(x_test)[:, 1]
    logistic_seconds = time.perf_counter()-started
    chest_train = np.array([x['chest_total']/x['chest_n'] for x in train])
    started = time.perf_counter()
    model = xgb.XGBRegressor(n_estimators=160, max_depth=3, learning_rate=.05,
        subsample=.85, colsample_bytree=.85, reg_lambda=5., tree_method='hist',
        device=device, n_jobs=4, random_state=5)
    model.fit(x_train, chest_train,
              sample_weight=np.array([math.sqrt(x['chest_n']) for x in train]))
    chest_pred = np.maximum(0., model.get_booster().predict(xgb.DMatrix(x_test)))
    tree_seconds = time.perf_counter()-started
    return dict(metrics=evaluate(test, (win_pred, chest_pred),
                                 (baseline_win, baseline_chest)),
                train=len(train), test=len(test), logistic_seconds=logistic_seconds,
                tree_seconds=tree_seconds)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', required=True)
    parser.add_argument('--device', choices=('cpu', 'cuda'), default='cpu')
    parser.add_argument('--minimum-runs', type=int, default=32,
                        help='Minimum resolved runs per candidate (8 includes discovery-only history).')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    started = time.perf_counter()
    candidates = load_candidates(args.db, minimum_runs=args.minimum_runs)
    vectorizer = DictVectorizer(sparse=False)
    matrix = vectorizer.fit_transform([x['features'] for x in candidates]).astype(np.float32)
    groups = [x['root'] for x in candidates]
    folds = GroupShuffleSplit(n_splits=3, test_size=.25, random_state=230923)
    trials = [dict(kind='held-out-lineage',
                   **fit_fold(candidates, train, test, matrix, args.device))
              for train, test in folds.split(matrix, groups=groups)]
    root_birth = {}
    for item in candidates:
        root_birth[item['root']] = min(root_birth.get(item['root'], item['created']),
                                       item['created'])
    ordered_roots = sorted(root_birth, key=lambda root: (root_birth[root], root))
    cutoff = int(.75*len(ordered_roots))
    training_roots = set(ordered_roots[:cutoff])
    train = np.array([i for i, item in enumerate(candidates)
                      if item['root'] in training_roots])
    test = np.array([i for i, item in enumerate(candidates)
                     if item['root'] not in training_roots])
    trials.append(dict(kind='newer-lineages',
                       **fit_fold(candidates, train, test, matrix, args.device)))
    report = dict(device=args.device, minimum_runs=args.minimum_runs, candidates=len(candidates),
                  lineage_roots=len(set(groups)), features=len(vectorizer.feature_names_),
                  trials=trials, total_seconds=time.perf_counter()-started)
    encoded=json.dumps(report, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(encoded+'\n', encoding='utf-8')
    print(encoded)


if __name__ == '__main__':
    main()
