"""Effective-stat candidate generator checks (owner-anchored, no hand-written raw stat).

Anchors the generator to the one externally corroborated point - the user's rank-A awakening-20
Ninja (RE-evidence/20260920-user-wairo-strategy derived totals) - and then asserts the generation
contract: the training level is SOLVED from the target effective stat, the native curve cap and the
equipment/affinity owner are respected, the output carries no caller raw stat, and the bounded
generation is deterministic. Run: python check_effective_candidates.py
"""
import json
import sys

from effective_candidates import (BOUNDS, STAT_TO_PARAM, effective_stats, generate_effective_candidates,
                                  nearest_training_level, profile_deficit)

NINJA_LEVELS = {"hp": 314, "mp": 330, "atk": 5, "def": 173, "spd": 3, "lck": 1, "dex": 1,
                "vig": 1, "int": 1, "gth": 1, "mov": 1, "hrt": 1}
NINJA_GEAR = [{"name": "A/ Legendary Hat", "level": 99}, {"name": "A/ Legendary Staff", "level": 76},
              {"name": "B/ Green Shield", "level": 57}, {"name": "A/ Verdant Armor", "level": 1}]
# The user-reference derived totals (RE-evidence/20260920-user-wairo-strategy/reference-result).
REFERENCE = {"hp": 4239, "mp": 1959, "atk": 343, "def": 1242, "spd": 320, "lck": 105, "dex": 11,
             "int": 376}

failures = []


def check(name, condition, detail=""):
    print(f"{'PASS' if condition else 'FAIL'} {name}" + (f" :: {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


effective = effective_stats("Ninja", "A", 20, NINJA_LEVELS, NINJA_GEAR)
check("owner anchor: rank-A awakening-20 Ninja effective totals match the reference",
      {k: effective[k]["value"] for k in REFERENCE} == REFERENCE,
      json.dumps({k: effective[k]["value"] for k in REFERENCE}))

round_trip = {k: nearest_training_level("Ninja", "A", 20, k, v, NINJA_GEAR) for k, v in REFERENCE.items()}
check("solve round-trip: each reference effective value returns its reference training level",
      all(round_trip[k]["level"] == NINJA_LEVELS[k] for k in REFERENCE),
      json.dumps({k: round_trip[k]["level"] for k in REFERENCE}))

impossible = nearest_training_level("Ninja", "A", 0, "def", 999999, NINJA_GEAR)
check("native curve cap: an unreachable target clamps to the awakening cap, never beyond",
      impossible["level"] == impossible["maxLevel"] == 30,
      json.dumps(impossible))
check("lower clamp: a target below the curve floor never solves below level 1",
      nearest_training_level("Ninja", "A", 20, "atk", -500, NINJA_GEAR)["level"] == 1)

profile = dict(hpMin=REFERENCE["hp"], mpMin=REFERENCE["mp"], defMin=REFERENCE["def"],
               lckMin=REFERENCE["lck"], atkMin=REFERENCE["atk"], atkMax=REFERENCE["atk"],
               spdMax=REFERENCE["spd"], dexMax=REFERENCE["dex"])
options = dict(jobs=[dict(jobName="Ninja", rank="A", awakening=20),
                     dict(jobName="Knight", rank="A", awakening=20),
                     dict(jobName="Scholar", rank="D", awakening=0)],
               gearSets=[dict(id="user", equipment=NINJA_GEAR)], skills=["Counter", "7-Hit Attack"],
               maxCandidates=4, statKeys=["hp", "mp", "def", "lck", "atk", "spd", "dex"])
first = generate_effective_candidates(profile, options)
second = generate_effective_candidates(profile, options)
check("bounded: generation respects the candidate cap", len(first["candidates"]) <= 4,
      str(len(first["candidates"])))
check("deterministic: identical request -> identical candidate set",
      [c["id"] for c in first["candidates"]] == [c["id"] for c in second["candidates"]])

top = first["candidates"][0]
check("reference identity reproduces the profile with zero deficit",
      top["id"].startswith("Ninja-A-aw20") and top["deficit"] == 0 and top["effective"] == REFERENCE,
      json.dumps(top["effective"]))
check("candidate summary exposes effective HP/MP/ATK/DEF/SPD/LUCK/DEX/INT",
      set(top["effective"]) == {"hp", "mp", "atk", "def", "spd", "lck", "dex", "int"})
check("candidate carries cost configuration and assumptions",
      {"trainingLevelSum", "gearLevelSum"} <= set(top["cost"]) and len(top["assumptions"]) >= 1)
check("skills are copied through unchanged as declared input",
      top["loadouts"][0]["skills"] == ["Counter", "7-Hit Attack"])
check("no caller raw stat: emitted loadout has statLevels only, no raw parameter fields",
      "statLevels" in top["loadouts"][0]
      and not any(k in top["loadouts"][0] for k in ("parameters", "rawValue", "rawMax", "effective")))
check("solved levels stay inside the awakening-expanded curve cap",
      all(v["level"] <= v["maxLevel"] for v in top["effectiveDetail"].values()))
check("profile deficit: zero on an exact match, positive on an impossible profile",
      profile_deficit(effective, profile) == 0
      and profile_deficit(effective, dict(profile, hpMin=10 ** 9)) > 0)
check("bounds are declared for the caller",
      BOUNDS["maxCandidates"] > 0 and BOUNDS["maxTrainingLevel"] > 0
      and set(STAT_TO_PARAM) >= {"hp", "mp", "atk", "def", "spd", "lck", "dex", "int"})
check("an unknown job/rank pair is refused rather than guessed",
      generate_effective_candidates(profile, dict(options, jobs=[dict(jobName="NotAJob", rank="A")]))["candidates"] == [])

print(f"\n{len(failures)} failure(s)" + (": " + ", ".join(failures) if failures else ""))
sys.exit(1 if failures else 0)
