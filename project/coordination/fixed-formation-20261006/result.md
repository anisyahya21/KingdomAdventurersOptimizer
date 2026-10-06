# Fixed-formation enforcement — delivered and verified (4 engines)

Policy hash: `403a443d931840b23ccc1bad071055422fd6e1f465681fbf2b5a6291fef7382f`
Machine-readable summary: `coordination/fixed-formation-20261006/result.json`
Work status: `coordination/fixed-formation-20261006/work-status.json`

## Fixed policy (authoritative)

- Six units, fixed roster order: `Synthetic DPS, fixed Healer, Scholar Fodder 1..4`.
- Placement roles (canonical preparation): `dps, fodder, fodder, fodder, fodder, healer`.
  Confirmed in the C++ engine: canonical preparation emits `ownFormationOrder [0,2,3,4,5,1]`.
- Only search variable: the Synthetic DPS raw stats, inside the walls below. Healer/fodder
  numbers, positions, skills/order, triggers, weapons, equipment and formation never mutate.
- Four fodders are identical; permutation is refused, so it cannot proliferate candidates.
- The user's exact notation (`DPS 0,1`; fodders `0,2–0,5`; healer `1,7`) is preserved unmodified.
  Canonical preparation currently uses five columns, so the coordinate/slot mapping is validated
  through canonical placement rather than inferred from names.

### Synthetic DPS (neutral synthetic unit, no real class/awakening/equipment identity)

- No equipment, `weaponId 0`, no job/class/awakening identity.
- Skills in exact order (invocation level 0 = player-facing "High"):
  `26 Counter, 110 7-hit, 25 5-hit, 24 4-hit, 23 3-hit, 22 2-hit`.
- Searchable raw-stat walls (7): hp `[55,19287]`, mp `[5,7986]`, atk `[6,5766]`,
  def `[3,4976]`, spd `[5,4780]`, lck `[5,5204]`, dex `[2,7049]`.
- Pinned (not searched): params `12,18,20,21,22` (INT/magic has no attack path).

### Fixed healer

- Skills in exact order: `37` (Heal Maddy, High), `107` (Backup). `weaponId 0`, no equipment.
- Established numbers preserved (hp 4077 / mp 1539 / def 9 / spd 32 / ...).

### Fixed fodders (x4, identical)

- No skills; `weaponId 0`, no equipment. Established numbers preserved
  (hp 150 / mp 43 / def 42 / spd 43 / lck 43 / dex 91 ...).

## Per-engine enforcement and verification

- **Python** — `KA-Website/tools/recovery/fixed_formation.py` is the canonical policy
  implementation (admission, identity/dedup, mutation, canonical scenario). The original optimizer's
  `strategy_admission_preparation.prepare` (canonical preparation) and
  `strategy_optimizer.Store.add_child` (child generation) refuse non-fixed scenarios when enabled;
  default stays silent, so historical libraries and behaviour are untouched.
  Activation: `fixed_formation.set_enabled(True)`.
  Evidence: `python-verification.json` (20/20 admitted, placement 20/20, all mutations admitted,
  all non-DPS refusals enforced, identity deterministic, gate integration all true) → `ok: true`.
- **Go** — `fixed_formation.go` (validate/identity/mutate) with admission at
  `NewSearch`/`MergeSeeds`/`Next`; CLI `--policy search-contract|fixed-formation`.
  Evidence: `go-verification.json` (77 distinct candidates, all compliant, stats varied) → `ok: true`;
  the original equipped cast is refused at startup; default policy unaffected.
- **Rust** — `src/fixed_formation.rs` (validate/identity/mutate/policy_hash); admission after
  `load_candidates`; fixed-only mutation branch; scope records `fixedFormation` +
  `fixedFormationPolicyHash`. Evidence: `rust-verification.json` (5 distinct compliant, stats varied)
  → `ok: true`; `launch/rust/output/scope.json` records the policy hash.
- **C++** — new `fixed_formation.cpp/.hpp` with embedded canonical policy; `admission.cpp` gates
  every candidate at canonical admission; `pipeline.cpp` binds `identity()`, adds the
  `fixed-dps-stat` operator, refuses non-fixed inputs before the search, and folds the policy hash
  into the run policy; `main.cpp` adds `--prepare-fixed`.
  Evidence: `cpp-verification.json` → `ok: true`:
  canonical preparation accepts the fixed point and refuses the original equipped cast (exit 2);
  fixed proposal emits only `fixed-dps-stat` children; headless search saved 10 rows,
  0 execution errors, 5 distinct compliant candidates with varied stats.

## Shared launch configs

`launch/launch.json` maps each engine to its activation and artifact. The C++, Rust and Go launch
artifacts were executed end-to-end and their outputs validated as compliant (`launch/*/validation.json`).

## Files changed

`tools/`: `embed_cpp_fixed_policy.py`, `check_fixed_formation_cpp.py`, `build_fixed_launch_configs.py`,
`check_fixed_formation.py`, `verify_fixed_candidates.py`.
`KA-Website/tools/recovery/`: `fixed_formation.py`, `strategy_admission_preparation.py`,
`strategy_optimizer.py`.
`coordination/native-preparation/cpp/standalone-optimizer/`: `fixed_formation.hpp`, `fixed_formation.cpp`,
`fixed_formation_policy.inc`, `admission.cpp`, `pipeline.cpp`, `main.cpp`, `build.cmd`.
(Go and Rust engine modules were already in place from the same task and are carried unchanged.)

## Remaining concrete blocker

- The shared desktop app `coordination/native-preparation/shared/native_optimizer_app.py` still pins
  frozen engine revisions (Rust r8 / Go r11 / C++ r9) built before the policy. Repointing the app to
  policy-aware builds is coordinator-owned frozen-artifact work and was **not** done. Policy-activating
  launch configs are provided and verified instead. Nothing else is outstanding for this task.

No credentials appear in any file or log. Historical strategy records, libraries and checkpoints were
not rewritten; all new evidence is diagnostic and separate from the strategy library.
