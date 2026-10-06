"""Separate battle/measurement compatibility from optimizer implementation diagnostics.

`battle_compatibility_revision` names the code and data that can change a stored battle's
meaning, its measured outcome, the candidate identity, or the frozen measurement policy.
Scheduling, orchestration, database recovery queries, and reporting are deliberately omitted.

The adapter provenance manifest supplies all `combat*.py` modules, combat runtime data/tables,
`strategy_search.py`, and `strategy_optimizer_adapter.py`. Its two optimizer files are removed
because they coordinate work and configure workers; they do not implement a battle. We add the
measurement/outcome and policy modules below, plus only the frozen-policy declaration and
projection from `strategy_encounter_search.py`. The rest of that module owns coordination and
recovery and must not invalidate a frozen battle plan when it changes.

If the manifest recipe itself changes, bump `REVISION_SCHEMA`. The exact pre-split scheduler
revision and the battle digest calculated from that same source tree are retained as a narrowly
scoped bridge: old frozen work is accepted only while the current battle digest still equals the
captured digest. Any subsequent mechanics or measurement change closes that bridge automatically.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
REVISION_SCHEMA = 'battle-compatibility-v1'

# Captured from Community-Knowledge-20260928.sqlite before any production edit on 2026-09-29.
# This is the exact scheduler digest loaded by the then-current production source tree.
LEGACY_KNOWN_GOOD_SCHEDULER_REVISION = (
    'ade4c17bcf4107f545bea09b38a9af6115213cb9baa77b5f79eaaa0022d89f8c')
# Digest of the battle/measurement inputs in that same pre-edit tree. It was independently
# calculated and saved in studies/coordinator_single_scan_20260929/pre-implementation-revision.json.
LEGACY_KNOWN_GOOD_BATTLE_REVISION = (
    '5392897a691c57f7573b5cedc71917f8ecf92117885345aaaa1a9d59456d20c2')

SCHEDULER_ONLY_PROVENANCE_FILES = frozenset((
    'tools/recovery/strategy_optimizer.py',
    'tools/recovery/strategy_optimizer_limits.py',
))

MEASUREMENT_AND_POLICY_FILES = (
    'strategy_encounter_evaluation.py',  # worker result normalization and evidence measurement
    'strategy_encounter_confirmation.py',  # frozen comparison and holdout inference
    'strategy_outcomes.py',  # canonical outcome interpretation
    'strategy_search_mode.py',  # policy, owner, and purpose semantics
)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False)


def revision_from_manifest(provenance, measurement_files, frozen_policy_definitions):
    """Build a stable semantic revision, filtering scheduler-only provenance by exact path."""
    source_files = dict(provenance.get('files') or {})
    for name in SCHEDULER_ONLY_PROVENANCE_FILES:
        source_files.pop(name, None)
    missing = sorted(name for name in (provenance.get('missing') or [])
                     if name not in SCHEDULER_ONLY_PROVENANCE_FILES)
    missing.extend(name for name, digest in source_files.items() if digest is None)
    missing.extend(name for name, digest in measurement_files.items() if digest is None)
    if missing:
        raise RuntimeError('battle compatibility inputs are missing: %s' % ', '.join(sorted(set(missing))))
    manifest = {
        'revisionSchema': REVISION_SCHEMA,
        'simulator': {
            'mode': provenance.get('mode'),
            'files': dict(sorted(source_files.items())),
        },
        'measurementAndPolicyFiles': dict(sorted(measurement_files.items())),
        'frozenPolicyDefinitions': dict(sorted(frozen_policy_definitions.items())),
    }
    return hashlib.sha256(_canonical(manifest).encode('utf-8')).hexdigest(), manifest


def _frozen_policy_definitions(path):
    text = path.read_text(encoding='utf-8')
    tree = ast.parse(text, filename=str(path))
    definitions = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == '_freeze_policy':
            definitions[node.name] = ast.get_source_segment(text, node)
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = ([target.id for target in node.targets if isinstance(target, ast.Name)]
                       if isinstance(node, ast.Assign) else
                       ([node.target.id] if isinstance(node.target, ast.Name) else []))
            if 'POLICY_KEYS' in targets:
                definitions['POLICY_KEYS'] = ast.get_source_segment(text, node)
    if set(definitions) != {'POLICY_KEYS', '_freeze_policy'}:
        raise RuntimeError('frozen policy definition is incomplete; revision fails closed')
    return definitions


def current_battle_compatibility_revision():
    """Hash canonical battle/measurement inputs; fail closed when any input is unavailable."""
    import strategy_optimizer_adapter as adapter

    provenance = adapter.provenance(refresh=True)
    measurement_files = {}
    for name in MEASUREMENT_AND_POLICY_FILES:
        path = HERE / name
        measurement_files['tools/recovery/' + name] = (
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None)
    policy_definitions = _frozen_policy_definitions(HERE / 'strategy_encounter_search.py')
    return revision_from_manifest(provenance, measurement_files, policy_definitions)


def accepts_legacy_scheduler_revision(stored_revision, current_battle_revision):
    """Accept the one captured scheduler hash only on its unchanged battle-input baseline."""
    return (stored_revision == LEGACY_KNOWN_GOOD_SCHEDULER_REVISION
            and current_battle_revision == LEGACY_KNOWN_GOOD_BATTLE_REVISION)
