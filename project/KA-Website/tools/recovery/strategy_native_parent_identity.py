"""Verified alias metadata for native parent candidate IDs.

RNG seed fields vary per trial and are intentionally excluded from strategy identity.
When two source rows reuse one stable candidate ID, only that precise equivalence is mergeable.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

_SEED_FIELDS = frozenset(("mathSeed", "libSeed", "seedPair", "seeds"))


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def seedless_identity(intent: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(intent, Mapping):
        raise ValueError("Candidate identity needs a raw intent object")
    return {key: value for key, value in intent.items() if key not in _SEED_FIELDS}


def exact_intent_sha256(intent: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(dict(intent))).hexdigest()


def strategy_identity_sha256(intent: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(seedless_identity(intent))).hexdigest()


def merge_parent_identity_alias(primary: dict[str, Any], alias: Mapping[str, Any]) -> bool:
    """Retain a same-ID, same-seedless-intent source alias; reject identity collisions."""
    if not isinstance(primary, dict) or not isinstance(alias, Mapping):
        raise ValueError("Candidate identity alias rows must be objects")
    primary_id = primary.get("candidateId", primary.get("id"))
    alias_id = alias.get("candidateId", alias.get("id"))
    if not isinstance(primary_id, (str, int)) or isinstance(primary_id, bool) or not str(primary_id):
        raise ValueError("Primary parent has no stable candidate ID")
    if not isinstance(alias_id, (str, int)) or isinstance(alias_id, bool) or not str(alias_id):
        raise ValueError("Alias parent has no stable candidate ID")
    primary_id, alias_id = str(primary_id), str(alias_id)
    if primary_id != alias_id:
        raise ValueError(f"Cannot merge different candidate IDs {primary_id!r} and {alias_id!r}")
    primary_intent = primary.get("scenario", primary.get("intent"))
    alias_intent = alias.get("scenario", alias.get("intent"))
    if not isinstance(primary_intent, dict) or not isinstance(alias_intent, dict):
        raise ValueError(f"Duplicate candidate ID {primary_id!r} has an incomplete raw intent")
    primary_identity = strategy_identity_sha256(primary_intent)
    alias_identity = strategy_identity_sha256(alias_intent)
    if primary_identity != alias_identity:
        raise ValueError(
            f"Candidate ID {primary_id!r} maps to different seed-stripped strategy intents "
            f"({primary_identity} != {alias_identity})"
        )
    source = alias.get("source", "original-library")
    if not isinstance(source, str) or not source:
        raise ValueError("Candidate identity alias has no source label")
    entry = {
        "candidateId": primary_id,
        "source": source,
        "intent": alias_intent,
        "intentSha256": exact_intent_sha256(alias_intent),
        "strategyIntentSha256": alias_identity,
    }
    aliases = primary.setdefault("identityAliases", [])
    if not isinstance(aliases, list):
        raise ValueError(f"Parent {primary_id!r} has malformed identityAliases")
    # Keep source provenance as well as exact intent, but don't repeat the same source row.
    if any(item.get("source") == source and item.get("intentSha256") == entry["intentSha256"]
           for item in aliases if isinstance(item, Mapping)):
        return False
    aliases.append(entry)
    return True


def validate_parent_identity_aliases(parent_id: str, parent_intent: dict[str, Any],
                                     aliases: Any) -> list[dict[str, Any]]:
    """Validate and preserve aliases in the durable parent metadata."""
    if aliases in (None, []):
        return []
    if not isinstance(aliases, list) or len(aliases) > 64:
        raise ValueError(f"Parent {parent_id!r} identityAliases must be a list of at most 64 rows")
    primary_identity = strategy_identity_sha256(parent_intent)
    checked: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(aliases):
        if not isinstance(item, Mapping):
            raise ValueError(f"Parent {parent_id!r} identity alias {index} must be an object")
        alias_id = item.get("candidateId")
        source = item.get("source")
        intent = item.get("intent")
        if str(alias_id) != parent_id or not isinstance(source, str) or not source or not isinstance(intent, dict):
            raise ValueError(f"Parent {parent_id!r} identity alias {index} has invalid id/source/intent")
        exact_sha = exact_intent_sha256(intent)
        strategy_sha = strategy_identity_sha256(intent)
        if item.get("intentSha256") != exact_sha or item.get("strategyIntentSha256") != strategy_sha:
            raise ValueError(f"Parent {parent_id!r} identity alias {index} hash metadata is inconsistent")
        if strategy_sha != primary_identity:
            raise ValueError(f"Parent {parent_id!r} identity alias {index} changes strategy identity")
        if intent.get("encounterId") != parent_intent.get("encounterId"):
            raise ValueError(f"Parent {parent_id!r} identity alias {index} changes encounter")
        key = (source, exact_sha)
        if key in seen:
            raise ValueError(f"Parent {parent_id!r} repeats identity alias {index}")
        seen.add(key)
        checked.append({
            "candidateId": parent_id,
            "source": source,
            "intent": intent,
            "intentSha256": exact_sha,
            "strategyIntentSha256": strategy_sha,
        })
    return checked
