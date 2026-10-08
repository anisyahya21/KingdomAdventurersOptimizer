# Corrected original native learner

The original Python learner already uses earned rewards, queued-reward potential,
stored-command setup progress and efficiency as separate objectives. The native
search paths previously selected mostly by earned rewards, even when they saved
mechanism telemetry. This source update restores those objective rankings and
lane-aware feedback in C++, Go and Rust.

The corrected paths support the active seven parent sources, bounded lineage
productivity weights, stable lane rankings, region representatives, retention of
mechanism leaders, and the original stat-scale/jump rules. Missing evidence stays
unknown. A setup improvement can guide search even when earned reward is zero.
C++ same-output recovery reconstructs journal-derived attempts and credits fully
measured child feedback once rather than losing it after an interrupted batch.

## Use the corrected source

Build the canonical standalone source using [BUILD.md](BUILD.md), then run:

```powershell
python run_corrected_smoke.py cpp --exe PATH_TO_FRESHLY_BUILT_EXE
python run_corrected_smoke.py go --exe PATH_TO_FRESHLY_BUILT_EXE
python run_corrected_smoke.py rust --exe PATH_TO_FRESHLY_BUILT_EXE
```

Each command creates a fresh ignored runtime directory and requests two
evaluations on encounter 0. `--prepare-only` writes the input/configuration
without simulating. The smoke explicitly enables `mechanism-lanes-v3` and the
fixed three-stat profile. It needs no private database, learner prior or model.
Go retains its existing generated-child seed policy; this smoke is not a matched
cross-engine benchmark. Its results remain diagnostic until its production
identity checks are satisfied; the smoke does not promote them into a production
library.

DPS HP/MP/DEF are fixed at 2500 and DEX at 18; only ATK/SPD/LCK can change.
Roster, skills, equipment-free DPS and other units remain fixed. Predictor
guidance is disabled. Some C++ predictor integration remains dormant in source;
no predictor weights, training corpus or retraining automation are included.

The existing desktop binaries and finishing manifests remain historical pins.
Ordinary `Launch-*.cmd` entry points have **not** been repointed to these source
changes. Rebuild and use the explicit corrected entry above to inspect this fix.

## Verification and limits

Saved objective and learner fixtures are under `tests/mechanism-objectives`.
Local verification covered lane ranking, active parent selection, controlled
actions and feedback. The packaged-source smoke saved two evaluations per native
engine, with no rejected/execution-error trials in the C++ and Rust runs.
C++ restart checks use saved synthetic journal/reducer
fixtures; they do not establish end-to-end process-kill or GUI recovery.

The controlled C++ proof completed 400 evaluations: 20 encounters, one seed pair,
10 evaluations per method per encounter. The restored original obtained 28 wins
and 48 known chests; the same learner with optional predictor guidance obtained
142 wins and 237 known chests. Both best rewards were 5. One original outcome was
unresolved and excluded from learning. The guided experiment is evidence only;
it is not enabled by this correction's smoke entry.

This is **constrained stat-only learner parity**, not full unrestricted Python
scheduler parity. Fresh teams, local-probe/staged validation and starvation
reseed behavior have not all been ported. Without explicit validation provenance,
the native mean-parent source falls back rather than inventing validated samples.
The existing Python omission of normal per-stat/scale success-counter updates is
preserved for comparison; general operator feedback and anchors do update.
No general superiority or completed production release is claimed.
