# How the optimiser works — a debugger's starting guide

This guide collects the repository locations, simplified architecture, function flow and debugging points discussed in the accompanying chat. It is intended for someone who has never seen the project before.

Source references were checked against commit `004a1c43a8048635083ca300e3a51d49b3610d8a` on 2026-10-07. Line numbers describe that snapshot and can move after edits; use the function names to find them again.

## Repository and included files

The optimiser repository is [KingdomAdventurersOptimizer](https://github.com/anisyahya21/KingdomAdventurersOptimizer). The broader website repository is [Kingdom-adventures-game-data-and-tools](https://github.com/anisyahya21/Kingdom-adventures-game-data-and-tools).

This repository contains Python, Rust, Go and C++ optimiser source, the desktop interface, launchers, checks, documentation, supporting game data and selected convenience binaries. Personal databases and strategy libraries are excluded. Runtime libraries created on another machine are local application state.

On the original machine, the upload checkout is `A:\KingdomAdventurersOptimizer\developer-handoff`. Paths below are relative to this repository, so they also work for another developer's clone.

Read these supporting documents:

- [README.md](README.md): installation and launch commands.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): component responsibilities.
- [docs/BUILD.md](docs/BUILD.md): rebuilding engines and the interface.
- [docs/KNOWN_PROBLEMS.md](docs/KNOWN_PROBLEMS.md): investigation starting points and verification limitations.
- [docs/PROVENANCE.md](docs/PROVENANCE.md): exact identities of included builds.
- [docs/VERIFICATION.md](docs/VERIFICATION.md): existing verification information.
- [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md): why supporting files are included.

## The five main parts

| Part | Responsibility | Location |
|---|---|---|
| Interface | Settings, buttons, counters and results | `project/KA-Website/artifacts/kingdom-adventures/src/` |
| Controller | Connects the interface to engines and manages lifecycle | `project/KA-Website/tools/recovery/strategy_language_desktop.py`, `strategy_optimizer_desktop.py`, `strategy_native_controller.py` |
| Search | Chooses which strategies and trials to try next | Python recovery modules and native standalone optimiser folders |
| Simulation | Determines what happens in a battle | Adapter/backend modules and shared Rust kernel |
| Storage | Saves strategies, ancestry and measured evidence | Python SQLite store and experiment ledger; native engines have their own persistence implementations |

The search engine chooses what to try. The battle simulator determines the outcome. These are separate responsibilities.

```text
Choose settings in the desktop app
    -> controller selects/starts an engine
    -> search generates candidate strategies
    -> simulator tests them
    -> results and evidence are saved
    -> rankings and future choices are updated
    -> another search round
```

Strategies describe builds and battle setups: units, equipment, skills, formation and other scenario settings. Which dimensions may change depends on the active search policy and mode. This overview does not mean every mode can freely change every field.

## Source locations by problem

| Problem | Start here |
|---|---|
| Python scheduling, search or result saving | `project/KA-Website/tools/recovery/strategy_optimizer.py` |
| Desktop buttons, layout or counters | `project/KA-Website/artifacts/kingdom-adventures/src/pages/strategy-optimizer-desktop.tsx` |
| Interface-to-engine communication | `strategy_language_desktop.py` and `strategy_optimizer_desktop.py` in the recovery folder |
| Native start, pause, resume or configuration | `strategy_native_controller.py`, `strategy_native_run_config.py`, `strategy_native_assets.py` in the recovery folder |
| Rust search | `project/coordination/native-preparation/rust/standalone-optimizer/src/` |
| Go search | `project/coordination/native-preparation/go/standalone-optimizer/` |
| C++ search | `project/coordination/native-preparation/cpp/standalone-optimizer/` |
| Shared battle simulation | `project/KA-Website/tools/recovery/native/ka_kernel/` |

The preserved `KA-Website` directory name is part of the source layout. It does not mean that the entire website is needed to run this handoff.

## First establish which algorithm is running

The Python host contains both the original branching search and a newer encounter coordinator. The native engines have separate implementations. Do not assume `spawn_children()` describes every current search mode.

The included desktop manifests also pin older executables than some of the latest source. A source rebuild does not automatically change the executable selected by the desktop. Check `docs/PROVENANCE.md` and `project/coordination/native-finish/{rust,go,cpp}/build-manifest.json` before diagnosing a mismatch between source and running behaviour.

## Step-by-step function flow

The following is a reading map for the Python host. References use `file:line` notation; linked filenames open the relevant source.

### 1. Receive commands and coordinate work

- [strategy_optimizer_desktop.py](project/KA-Website/tools/recovery/strategy_optimizer_desktop.py):1167 — `Bridge.command()` receives commands such as Start and Pause and forwards them to the optimiser.
- [strategy_optimizer.py](project/KA-Website/tools/recovery/strategy_optimizer.py):4823 — `Optimizer.command()` is the optimiser command entry point.
- `strategy_optimizer.py:9574` — `Optimizer._loop()` coordinates command handling, available workers, pending trials, completed results and progress publication.
- `strategy_optimizer.py:8994` — `_campaign_fleet_pass()` coordinates encounter searches.
- [strategy_encounter_search.py](project/KA-Website/tools/recovery/strategy_encounter_search.py):1018 — `Coordinator.run_pass()` advances one encounter's search workflow.
- `strategy_encounter_search.py:1722` — `_plan_round()` chooses the next round's purpose and planning work.

Debugging question: is the fault in choosing work, dispatching it, executing it or consuming the result?

### 2. Open the library and retrieve strategies

- `strategy_optimizer.py:3461` — `Store.__init__()` opens SQLite, configures persistence and initializes tables and caches.
- `strategy_optimizer.py:3723` — `Store.scenario(candidate)` selects the scenario by candidate ID, decodes its JSON and caches it.
- `strategy_optimizer.py:3735` — `Store.lineage()` retrieves ancestry information.

This is how a previous strategy becomes input to another generation step: its ID identifies a stored scenario that can be loaded as a parent/reference.

### 3. Generate candidate strategies

For the encounter workflow:

- `strategy_encounter_search.py:1965` — `_propose()` routes generation using the search owner, purpose, parent and evidence. Community-first uses the strict joint proposal pool; other configured routing can use the stream proposal service. Read the mode checks rather than assuming every route is available.
- [strategy_joint_proposals.py](project/KA-Website/tools/recovery/strategy_joint_proposals.py):1445 — `pool()` provides the Community candidate-generation service.

For the original branching workflow:

- `strategy_optimizer.py:1790` — `spawn_children()` assembles parent pools, selects parents, chooses modifications and adds generated children.
- `strategy_optimizer.py:1695` — `weighted_parent()` selects from a parent pool using lineage/productivity information.
- [strategy_learner.py](project/KA-Website/tools/recovery/strategy_learner.py):415 — `choose_operator()` chooses a mutation type using learned weights.
- `strategy_learner.py:433` — `choose_stat_target()` selects a stat target when that mutation applies.
- [search_contract.py](project/KA-Website/tools/recovery/search_contract.py):790 — `MUTATION_OPS` lists supported modification types.
- `search_contract.py:862` — `mutate()` applies a modification within the search rules.
- `search_contract.py:700` — `set_stat()` applies a selected stat change.
- `search_contract.py:1064` — `describe_change()` describes the difference between parent and child.
- `strategy_optimizer.py:4068` — `Store.add_child()` stores the candidate and its ancestry/modification metadata.

The original branching search combines strong parents, different strategy regions and exploration. Productive changes can become more likely, while exploration remains possible. A candidate is a proposed improvement, not a proven improvement.

Debugging question: are children different from their parents, legal under the active policy, and actually admitted to storage?

### 4. Schedule and execute trials

- `strategy_optimizer.py:777` — `seed_pair()` supplies reproducible random seeds for a trial.
- `strategy_optimizer.py:9630` — nested `submit_ready()` within `_loop()` dispatches ready work, with batch submission when supported. Locate the function name if this line shifts.
- `strategy_optimizer.py:4520` — `worker()` calls the adapter and measures execution time.
- [strategy_optimizer_adapter.py](project/KA-Website/tools/recovery/strategy_optimizer_adapter.py):411 — `simulate()` prepares/validates the simulation input and routes to the selected backend, returning a compact result.
- [strategy_encounter_evaluation.py](project/KA-Website/tools/recovery/strategy_encounter_evaluation.py):286 — `harvest()` collects encounter evaluation completions.
- `strategy_encounter_evaluation.py:348` — `_record()` handles completed evaluation recording.

The usual accelerated path uses native simulation; the adapter also contains a Python fallback path. The shared Rust kernel is central to the native simulation architecture. Inspect the actual backend selection for a particular run.

### 5. Store outcomes durably

- `strategy_optimizer.py:4202` — `Store.record()` accepts a trial result and maintains recording order/bookkeeping.
- `strategy_optimizer.py:4246` — `Store.flush()` applies and commits batched writes.
- [strategy_experiment_store.py](project/KA-Website/tools/recovery/strategy_experiment_store.py):726 — `reserve()` records experiment trial reservations.
- `strategy_experiment_store.py:818` — `complete()` records completed experiment samples.

The main loop buffers out-of-order completions so results can be recorded in canonical order. Duplicate responses are counted and discarded. Accepted/buffered work and committed work are different states: inspect the flush path when debugging missing data after an interruption.

### 6. Compare evidence and guide the next round

For the original branching search:

- `strategy_optimizer.py:1234` — `lane_record()` builds a candidate's evidence/ranking record.
- `strategy_optimizer.py:1397` — `elite_lanes()` constructs elite candidate pools.
- `strategy_optimizer.py:2774` — `observe_children()` compares child evidence with parent evidence and updates learning state.
- `strategy_learner.py:331` — `improved_lanes()` defines improvement under the original lane orderings.
- `strategy_learner.py:352` — `promotion_stage()` decides whether a candidate deserves more evidence.
- `strategy_learner.py:397` — `operator_weights()` gives productive modification types more selection weight, with a floor retaining exploration.

For the encounter workflow:

- `strategy_encounter_search.py:3829` — `_fit_adviser()` is a starting point for evidence-based proposal guidance.
- `strategy_encounter_search.py:3856` — `_rank()` ranks a proposal pool.
- `strategy_encounter_search.py:3948` — `_refresh_portfolio()` refreshes the strategy portfolio from measured outcomes.
- `strategy_experiment_store.py:1107` — `record_portfolio()` persists portfolio information.

The original lane objective and encounter portfolio are different evaluation paths. Do not assume a single average-reward comparison controls every mode. Read the active ranking/comparison function when investigating why one candidate was preferred.

### 7. Publish progress and repeat

- `strategy_optimizer.py:5325` — `_publish()` begins progress/result publication.
- `strategy_optimizer.py:9574` — `_loop()` continues the search while handling lifecycle commands.

Conceptual pseudocode, not a literal copy of the implementation:

```python
while running:
    handle_commands()
    results = collect_finished_trials()
    save_results(results)
    update_evidence_and_rankings()

    if_more_candidates_needed = planning_requires_more_candidates()
    if if_more_candidates_needed:
        parent = retrieve_reference_strategy()
        children = generate_candidates(parent, evidence)
        admit_and_store_candidates(children)

    dispatch_ready_trials()
    publish_progress()
```

Learning in the original branching mechanism means updating measured selection weights and evidence. It does not require an AI model/API call for each generated strategy or battle.

## What the database stores

Start with `Store.__init__()` and the schema definitions at `strategy_experiment_store.py:85`.

| Stored information | Purpose |
|---|---|
| `candidate` | Strategy ID, JSON scenario, label, source, stats and creation metadata |
| `run` | Recorded trial result keyed by candidate, phase and ordinal |
| `lineage` | Parent/root ancestry and modification history; initialized through the learner |
| `meta` | Settings, counters, aggregate evidence and learning state |
| `archive` | Original search's archived candidate selections |
| `predictor_history` | Preserved compressed scenario/evidence information for pruned candidates |
| `ea_experiment`, `ea_sample`, related ledger tables | Encounter experiments, reserved/completed samples and associated evidence |
| `ea_earning_portfolio`, session and confirmation tables | Portfolio, campaign/session and confirmation state |

`identity()` at `strategy_optimizer.py:738` computes a scenario identity. `Store.add()` at line 3766 is the candidate insertion entry point. Candidate identity/equivalence handling helps avoid repeating equivalent strategies; inspect that path if apparently different proposals are rejected.

Storage is bounded and includes pruning and compact evidence/history paths. Do not assume every full replay payload is retained forever. Ancestry and durable summaries serve different purposes from full per-trial replay data.

## How previous strategies become better strategies

For the original branching path, follow this chain:

```text
saved candidates and evidence
    -> parent pools / weighted_parent()
    -> Store.scenario(parent_id)
    -> choose_operator() / choose_stat_target()
    -> mutate() / set_stat()
    -> Store.add_child()
    -> trials through worker() / simulate()
    -> Store.record() / flush()
    -> observe_children() / improved_lanes()
    -> updated operator weights, stat anchors and parent evidence
    -> future proposals
```

The stored parent is a reference. Generated changes become separate candidates with lineage, so the optimiser can distinguish the previous strategy from a proposed descendant. Descendants can fail or perform worse. The aim is to spend more future effort on productive directions while preserving exploration.

For the encounter path, begin at `_plan_round()` and follow `_propose()`, evaluation, experiment completion and portfolio/adviser refresh. This is the more useful starting path when reproducing Community campaign behaviour.

## What to give a debugger

Provide the repository link, this guide and a reproduction record containing:

- Engine, executable revision/hash and search mode.
- Encounter selection, policy/settings and worker count.
- Exact actions taken: Start, Pause, Resume, Stop, reopen, export or replay.
- Expected behaviour and observed behaviour, including the exact error.
- Relevant runtime logs, configuration/checkpoint and a minimal reproducing library when needed.
- Whether the run used an included pinned executable or a new source build.

For a first bounded reproduction, use a fresh library and record saved-row counts before and after lifecycle actions. The README documents launchers and `run_fixed_smoke.py`. Historical acceptance reports and known problems are investigation evidence, not proof that the current build reproduces or fixes a fault.

For strategy-quality problems, inspect generation, parent retrieval and ranking. For incorrect battle results, inspect the simulator/backend. For missing results, inspect reservations, completion and commits. For broken controls or counters, inspect the UI, bridge/controller and publication paths.
