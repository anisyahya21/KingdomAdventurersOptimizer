"""Prospective CPU predictor shadow trial against random legal mutations.

This experiment never writes into the active strategy library. It trains on its
retained validated candidates, generates legal one-step mutations, and runs a
small, bounded paired comparison while the desktop optimizer can keep running.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import as_completed
import json
import random
from pathlib import Path
import sqlite3
import time

import numpy as np
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
import xgboost as xgb

from strategy_optimizer_adapter import propose
from strategy_optimizer_fast import HeadlessPool
from strategy_predictor_experiment import chest_count, features, load_candidates
from strategy_optimizer_native import eligibility, auto_native_safe


ENCOUNTERS = (2, 6, 10, 14, 18, 19)


def fit_models(candidates):
    vectorizer = DictVectorizer(sparse=False)
    matrix = vectorizer.fit_transform([x['features'] for x in candidates]).astype(np.float32)
    logistic = make_pipeline(StandardScaler(), LogisticRegression(C=.1, max_iter=500))
    labels = np.array([part for _ in candidates for part in (0, 1)])
    weights = np.array([part for x in candidates for part in (x['losses'], x['wins'])], dtype=float)
    logistic.fit(np.repeat(matrix, 2, axis=0), labels,
                 logisticregression__sample_weight=weights)
    model = xgb.XGBRegressor(n_estimators=160, max_depth=3, learning_rate=.05,
        subsample=.85, colsample_bytree=.85, reg_lambda=5., tree_method='hist',
        device='cpu', n_jobs=4, random_state=5)
    target = np.array([x['chest_total']/x['chest_n'] for x in candidates])
    model.fit(matrix, target, sample_weight=np.array([np.sqrt(x['chest_n']) for x in candidates]))
    return vectorizer, logistic, model


def select_parents(candidates):
    best = {}
    for item in candidates:
        encounter = item['encounter']
        if encounter not in ENCOUNTERS or item['chest_n'] < 64:
            continue
        current = best.get(encounter)
        if current is None or item['chest_total']/item['chest_n'] > current['chest_total']/current['chest_n']:
            best[encounter] = item
    return best


def generate(db_path, best, vectorizer, logistic, model, count, selections):
    db = sqlite3.connect(Path(db_path).resolve().as_uri()+'?mode=ro', uri=True)
    pairs = []
    try:
        for encounter in ENCOUNTERS:
            parent = best.get(encounter)
            if parent is None:
                continue
            row = db.execute('SELECT scenario FROM candidate WHERE id=?', (parent['id'],)).fetchone()
            if row is None:
                continue
            original = json.loads(row[0])
            proposals = {}
            for index in range(count*4):
                try:
                    child = propose(original, 1_000_000+encounter*10_000+index)
                except ValueError:
                    continue
                key = json.dumps(child, sort_keys=True, separators=(',', ':'))
                if (key != json.dumps(original, sort_keys=True, separators=(',', ':'))
                        and eligibility(child)[0] and auto_native_safe(child)):
                    proposals[key] = child
                if len(proposals) >= count:
                    break
            if len(proposals) < 2:
                continue
            generated = list(proposals.values())
            matrix = vectorizer.transform([features(child, {}) for child in generated]).astype(np.float32)
            win_prediction = logistic.predict_proba(matrix)[:, 1]
            chest_prediction = model.get_booster().predict(xgb.DMatrix(matrix))
            # The chest target already includes zero on a loss. Use the logistic
            # estimate as a guard only; multiplying would double-count survival.
            eligible = [i for i, win in enumerate(win_prediction) if win >= .2]
            if not eligible:
                eligible = list(range(len(generated)))
            ranked = sorted(eligible, key=lambda i: (chest_prediction[i], win_prediction[i]),
                            reverse=True)
            guided = ranked[:selections]
            rng = random.Random(9000+encounter)
            controls = rng.sample([i for i in range(len(generated)) if i not in guided],
                                  min(len(guided), len(generated)-len(guided)))
            pairs.append(dict(encounter=encounter, parent=parent['id'],
                parent_chests=parent['chest_total']/parent['chest_n'],
                candidates=len(generated),
                selections=[dict(kind='parent', scenario=original,
                                 predicted_chests=None, predicted_win=None)] +
                           [dict(kind=kind, scenario=generated[index],
                    predicted_chests=float(chest_prediction[index]),
                    predicted_win=float(win_prediction[index]))
                    for kind, index in ([('cpu-guided', i) for i in guided]
                                        + [('random', i) for i in controls])]))
    finally:
        db.close()
    return pairs


def battle(pairs, seeds, workers):
    pool = HeadlessPool(workers)
    try:
        requests = {}
        for pair in pairs:
            for selection in pair['selections']:
                selection['observed'] = []
                for index in range(seeds):
                    encounter = pair['encounter']
                    future = pool.submit(None, selection['scenario'],
                        (7_000_000+encounter*1000+index, 8_000_000+encounter*1000+index))
                    requests[future] = selection
        for future in as_completed(requests, timeout=300):
            result = future.result(timeout=1)
            selection = requests[future]
            selection['observed'].append(dict(verdict=result.get('verdict'),
                chests=chest_count(result), backend=result.get('resultBackend'),
                elapsed=result.get('elapsedSeconds')))
        for pair in pairs:
            for selection in pair['selections']:
                rows = selection.pop('observed')
                resolved = [row for row in rows if row['chests'] is not None]
                selection['observed_runs'] = len(rows)
                selection['observed_win_rate'] = sum(row['verdict'] == 1 for row in resolved)/len(resolved) if resolved else None
                selection['observed_chests'] = sum(row['chests'] for row in resolved)/len(resolved) if resolved else None
                selection['backend_counts'] = {backend:sum(row['backend'] == backend for row in rows)
                                                for backend in {row['backend'] for row in rows}}
                selection['native_only'] = all(row['backend'] == 'native' for row in rows)
                # Preserve the exact proposal for replay, import, or a larger
                # paired validation bank after this quick screen.
        return pairs
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--proposals', type=int, default=48)
    parser.add_argument('--seeds', type=int, default=4)
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--selections', type=int, default=4)
    parser.add_argument('--minimum-runs', type=int, default=8)
    args = parser.parse_args()
    started = time.perf_counter()
    candidates = load_candidates(args.db, minimum_runs=args.minimum_runs)
    parents = select_parents(candidates)
    held_out_roots = {item['root'] for item in parents.values()}
    training = [item for item in candidates if item['root'] not in held_out_roots]
    vectorizer, logistic, model = fit_models(training)
    trained = time.perf_counter()
    pairs = generate(args.db, parents, vectorizer, logistic, model, args.proposals,
                     args.selections)
    generated = time.perf_counter()
    pairs = battle(pairs, args.seeds, args.workers)
    report = dict(training_candidates=len(training), held_out_parent_roots=len(held_out_roots),
                  minimum_runs=args.minimum_runs, cpu_training_seconds=trained-started,
                  proposal_seconds=generated-trained, battle_seconds=time.perf_counter()-generated,
                  proposals_per_encounter=args.proposals, selections_per_arm=args.selections,
                  seeds_per_selection=args.seeds,
                  workers=args.workers, pairs=pairs)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(report_file=str(args.out), training_candidates=len(training),
        cpu_training_seconds=report['cpu_training_seconds'],
        battle_seconds=report['battle_seconds'],
        comparisons=[dict(encounter=pair['encounter'],
                          guided=float(np.mean([s['observed_chests'] for s in pair['selections']
                                                if s['kind']=='cpu-guided'])),
                          random=float(np.mean([s['observed_chests'] for s in pair['selections']
                                                if s['kind']=='random'])),
                          parent=next(s['observed_chests'] for s in pair['selections']
                                      if s['kind']=='parent'),
                          all_native=all(s['native_only'] for s in pair['selections']))
                     for pair in pairs]), indent=2))


if __name__ == '__main__':
    main()
