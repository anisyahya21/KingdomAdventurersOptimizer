# Strategy mechanics / compiler / build-domain report

Scope: `strategy_mechanics.py`, `strategy_encounter_compiler.py`, `strategy_build_domain.py`,
`check_strategy_mechanics.py`. Read-only, deterministic, no battles, no SQLite, no live access.
Focus check: `tools/recovery/check_strategy_mechanics.py` -> **66/66**.

Revisions: `MECHANICS_REVISION = strategy-mechanics-2`, `CACHE_KEY_REVISION =
strategy-mechanics-cache-2`, `COMPILER_REVISION = strategy-encounter-compiler-2`, `DOMAIN_REVISION =
strategy-build-domain-2`.

## Public APIs

`strategy_mechanics`
- `dependency_manifest()` / `dependency_digest()` - per-file sha256 of every source file and runtime
  table this module derives numbers from, plus the semantic revisions.
- `invalidate_caches()` / `register_invalidator(cb)` - clear this process's caches (profile, prepared,
  speed table, dependency manifest, synthetic bounds, and the compiler's roster cache).
- `synthetic_stat_bounds(parameter_id=None)` - the search contract's permitted synthetic interval.
- `attack_search_bounds(context='synthetic', lo=None, hi=None)` - ATT domain with a named source.
- `speed_domain(context='synthetic')` - AGI interval the cached speed table covers.
- `action_timing(agility, max_agility=None)`, `agility_for_interval(interval, max_agility=None)`,
  `interval_contact_boundaries(agility)`.
- `damage_distribution(...)`, `base_damage_pmf`, `mean_damage`, `hits_to_kill`, `ttk_profile`,
  `pmf_summary`.
- `skill_selection_profile(prepared_unit, mp_available)`.
- `solve_attack_for_expected_damage(target, luck, defense, lo=None, hi=None, context='synthetic')`.
- `solve_attack_profile(target_profiles, luck, dex, enemy_roster, bounds)`.
- `profile(scenario)`, `prepared_setup(scenario)`, `normalize_scenario(scenario)`, `cache_info()`.

`strategy_encounter_compiler`
- `compile_encounter(scenario)`, `cache_info()`, `invalidate_caches()`,
  `ATK_LO`/`ATK_HI` (from the search contract, not hardcoded 6..5766).

`strategy_build_domain`
- `identity(scenario)`, `constraint_schema(constraints)`, `validate_constraints(scenario, constraints)`,
  `fingerprint(scenario, constraints)`.

## Constraints: schema vs candidate acceptance

`constraint_schema(constraints)` validates only the document: context must be `synthetic`, `player` or
`unrestricted` (anything else is invalid); `fixed`/`bounds` must be mappings of the documented path
`ownUnits.<index>.parameters.<parameterId>`; values must be signed-32 integers; a bound low must not
exceed its high; a fixed value must lie inside a supplied bound.

`validate_constraints(scenario, ...)` adds **candidate acceptance against effective prepared values**
(`raw + extra + equipment` for unbounded stats, from `combat_setup.prepare_setup`), not the raw stored
fields:
- a supplied `fixed` must equal the effective value of that parameter (a raw/extra/training mismatch
  is rejected);
- a supplied `bounds` interval must contain the effective value;
- under `synthetic`, every supplied value/interval must also lie inside `search_contract.stat_bounds`
  for that parameter, and a non-searchable parameter (12/20/21/22) is refused - signed-32 is only a
  storage floor, never the admissibility rule;
- a demanded value is preserved verbatim in `normalizedConstraints` (never replaced by the effective
  value);
- provenance: `synthetic` -> `declared`, `player`/`unrestricted` -> `unknown` (a player value is not
  verified), missing context -> `unknown`; joint reachability is always `unknown` and never claimed.

`syntheticBounds` echoes the permitted interval used. `valid` is the composed result and `schemaValid`
separates the document-only verdict.

## Fingerprint model

- `identity(scenario)` is **exactly** `strategy_optimizer.identity`: the raw input minus `mathSeed` /
  `libSeed`, the same sort/separators, **no normalization** (checked for parity).
- `normalizedFingerprint` is the separate effective-value key (seedless scenario + prepared effective
  values + revisions).
- `exactKey` hashes the seedless scenario, the constraint context/document, the prepared effective
  values, the encounter revision, and the source/data dependency digest. It is a complete *calculation*
  key. It does **not** by itself license pooling whole-battle outcome histories: history reuse still
  needs explicit full-behaviour validation.
- `features` is interpretation-only and carries stable indices (`unitIndex`, `enemyIndex`), and now
  covers MP, eligible/unavailable skills, normal-attack probability, command hits, expected MP per
  action, average training level, formation order, consumables, and the dependency digest - not just
  interval/crit/outgoing hit.
- Seed values do not change `identity`, `exactKey` or `normalizedFingerprint`.

## Caching and dependency digest

Every cache key (`prepared`, `profile`) is a canonical wrapper naming the semantic revisions,
`CACHE_KEY_REVISION`, and `dependency_digest()`. The digest covers the source of
`combat_resolution`, `combat_setup`, `combat_parameters`, `combat_initial_state`, `combat_encounters`,
`combat_skill_selection`, `combat_scenario`, `combat_runtime_data`, this module, and the runtime tables
`weapon-skill-profiles.json`, `encounters.json`, `formation-rules.json`,
`skill-combat-constants.json`. The profile key is the whole normalized scenario (so formation,
`startProfile`/`prePlacement`, consumables and inputs are included) plus that digest.

`compile_encounter` caches by the **actual encounter revision digest** plus the dependency digest, so a
change to own-unit stats reuses the same compiled roster object (checked). `invalidate_caches()`
clears in-process caches and is exposed through both modules. **Dynamic update is not claimed**:
already-imported source and data are fixed for the process lifetime, so an edited file on disk needs a
restart; the digest exists so a probe can tell whether the running process matches the current files.

## Skill selection

`active_skill_infos` is now called with the authoritative `skill_mp_cost` (not `lambda: 0`), so MP
excludes unaffordable skills, and the invocation level still comes from the **filtered** ordinal
(native `Where` precedes indexed `Select`). `count` is reported as a **command hit count**
(`commandHitCount`, `expectedCommandHitsPerAction`), never landed damage; `landedDamageModel` points at
`damage_distribution`/`ttk_profile`, and unaffordable skills are listed explicitly in
`unavailableRoutes`. The legacy `expectedHitsPerAction` key is retained as an alias of the command-hit
count with a `commandHitSemantics` note. Training level changes the recovered cost and therefore
eligibility (checked).

## Bounds and landmarks

ATT inversion and encounter progression landmarks use `search_contract.stat_bounds` (currently
`[6, 5766]`), not a hardcoded range; a caller may tighten either end. The speed table is scanned to the
synthetic AGI interval (currently `4780`), not a round "legal-ish" number; `speed_domain` names the
source and an explicit `unrestricted` fallback exists. `player`/`unrestricted` ATT search requires
explicit bounds and is named, never silently signed-32. `interval_contact_boundaries` now returns the
full incoming-contact landmark ladder with the in-band row flagged, not only the two adjacent
endpoints.

## Honest reduced-model labels

- Per-hit damage is the recovered **base attack/defense law plus the critical half-armour branch only**
  (`modelKind=base-law-per-hit`, `completeUnitCombatPmf=False`); invoking skill type/value modifiers,
  weapon type/affinity differences, status/buffs/barriers/reflection and area/target branches are
  explicitly **not** applied. This is not a complete per-unit combat PMF.
- TTK is a capped reduced-model convolution (`TTK_HIT_CAP`), not a whole-battle prediction.
- `period = interval + 21` is labelled the **uninterrupted normal-attack reduced path**, not universal
  action cadence (skills, reactions, movement and status shift actual frames).
- Incoming accuracy is the static `hit_rate`; type-22 invocation windows need live state.
- `solve_attack_profile` is a **soft** bounded fit: one bounded inverse per target plus a few
  neighbouring ATT candidates, scored by hit probability and per-hit damage PMF quantile residuals
  (`damageP10`/`damageMedian`/`damageP90`, which carry the miss mass at 0) plus expected hits-to-kill
  and, when an enemy's HP is inside `TTK_HP_CAP`, hits-to-kill quantiles, for boss and followers. It
  makes no whole-battle call, forms no Cartesian product, is O(targets) and never claims reachability.

## Remaining unsupported semantics

- No complete per-unit or whole-battle damage PMF: invoking modifiers, weapon-type/affinity effects,
  status/buff/barrier/reflection, area and target-selection branches are outside this layer.
- No simulated MP drain or consumable cadence; consumables and MP watch are reported as declared
  policy, and healing is a static affordable-cast count.
- Formation is represented by order/priority only, not by live placement or movement.
- No reachability solver and no real-build (job/rank/equipment) resolution.
- Source/data changes require a process restart to take effect; the dependency digest names the inputs
  but is not a live watcher.

## Cross-worker note (not fixed here)

`check_encounter_contracts.py` now passes its constraint and identity probes, but its final leg fails in
`strategy_experiment_store.reserve` (`_seeds((123, 456))` -> "seed must be an integer"), an untracked
sibling file outside this task's ownership.
