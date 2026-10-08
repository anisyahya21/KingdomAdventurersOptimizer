mod engine;
mod fixed_formation;
mod input;
mod objective;
mod prepare;
mod search;
mod store;
mod tuning;

use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet, HashSet};
use std::fs::{self, File};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::sync::{mpsc, Arc};
use std::time::{Duration, Instant};

const STATUS_INTERVAL: Duration = Duration::from_millis(200);
const MAX_ADMITTED_SCENARIO_BYTES: usize = 256 * 1024 * 1024;
const MAX_SERIALIZED_WORK_BYTES: usize = 256 * 1024 * 1024;

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Config {
    schema: String,
    kernel: PathBuf,
    catalog: PathBuf,
    candidates: PathBuf,
    output: PathBuf,
    executors: usize,
    generations: usize,
    candidates_per_generation: usize,
    #[serde(default = "default_candidate_limit")]
    candidate_limit: usize,
    #[serde(default)]
    encounter_filter: Option<i64>,
    #[serde(default)]
    encounter_filters: Option<Vec<i64>>,
    #[serde(default)]
    focus_encounter: Option<i64>,
    #[serde(default)]
    focus_encounters: Option<Vec<i64>>,
    #[serde(default)]
    control_path: Option<PathBuf>,
    #[serde(default)]
    initial_checkpoint: Option<PathBuf>,
    search_seed: u64,
    seed_pairs: Vec<[i32; 2]>,
    mechanics_provenance: Value,
    policy_provenance: Value,
    #[serde(default)]
    parity_attestation: Option<Value>,
    #[serde(default)]
    diagnostic_dump_directory: Option<PathBuf>,
    #[serde(default)]
    result_sync_batch_size: Option<usize>,
    #[serde(default)]
    stage_timing_telemetry: bool,
    #[serde(default = "default_execution_mode")]
    execution_mode: String,
    #[serde(default)]
    proposal_request: Option<Value>,
    #[serde(default)]
    replay_trace: bool,
    #[serde(default)]
    initial_search_state: Option<Value>,
    #[serde(default)]
    candidate_source_ids: Option<Vec<Value>>,
    /// Retains the original pinned roster and DPS-stat search policy and guards.
    #[serde(default)]
    fixed_formation: bool,
    #[serde(default = "default_objective_mode")]
    objective_mode: String,
    #[serde(default)]
    synthetic_dps_search: Option<SyntheticDpsSearch>,
    #[serde(default)]
    common_learner_prior_path: Option<PathBuf>,
    #[serde(default)]
    common_learner_prior_sha256: Option<String>,
}

#[derive(Clone, Debug, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct SyntheticDpsSearch {
    fixed_parameters: BTreeMap<String, i64>,
    mutable_parameters: Vec<String>,
}

fn default_execution_mode() -> String {
    "production".into()
}
fn default_objective_mode() -> String {
    "earned-only-v1".into()
}

fn objective_version(mode: &str) -> Option<u32> {
    match mode {
        "earned-only-v1" => Some(1),
        "mechanism-lanes-v3" => Some(objective::OBJECTIVE_VERSION),
        _ => None,
    }
}

#[derive(Clone, Debug, Default, serde::Serialize, serde::Deserialize)]
struct ObjectiveEntry {
    intent: Value,
    encounter_id: i64,
    defeat_count: i64,
    aggregate: objective::Aggregate,
}

#[derive(Clone, Debug, Default, serde::Serialize, serde::Deserialize)]
struct ObjectiveState {
    records: BTreeMap<String, ObjectiveEntry>,
    #[serde(default)]
    birth_order: Vec<String>,
    seen_slots: BTreeSet<String>,
    #[serde(default)]
    proposal_numbers: BTreeMap<i64, u64>,
    #[serde(default)]
    lineage_roots: BTreeMap<String, String>,
    #[serde(default)]
    root_productivity: BTreeMap<String, RootProductivity>,
    #[serde(default)]
    operator_stats: BTreeMap<i64, BTreeMap<String, LearnerCounter>>,
    #[serde(default)]
    scale_stats: BTreeMap<i64, BTreeMap<String, BTreeMap<String, LearnerCounter>>>,
    #[serde(default)]
    stat_anchors: BTreeMap<i64, BTreeMap<String, i64>>,
    #[serde(default)]
    rng_states: BTreeMap<i64, u64>,
    #[serde(default)]
    rewarded_children: BTreeSet<String>,
    #[serde(default)]
    learner_prior_sha256: Option<String>,
    #[serde(default)]
    learner_prior_source_identity: Option<Value>,
}

#[derive(Clone, Debug, Default, serde::Serialize, serde::Deserialize)]
struct RootProductivity {
    children: u64,
    improved: u64,
}

#[derive(Clone, Debug, Default, serde::Serialize, serde::Deserialize)]
struct LearnerCounter {
    #[serde(default)]
    attempts: u64,
    #[serde(default)]
    improved: u64,
    #[serde(default)]
    planned: u64,
}

impl ObjectiveState {
    fn lane_record(&self, candidate: &str) -> Value {
        self.records
            .get(candidate)
            .map(|entry| {
                entry
                    .aggregate
                    .record(json!(candidate), entry.encounter_id, entry.defeat_count)
            })
            .unwrap_or_else(|| {
                json!({
                    "candidate":candidate,"encounterId":0,"defeatCount":0,"n":0,"wins":0,
                    "earnedMax":null,"earnedCount":0,"potentialMax":null,"winInterval":[0.0,1.0],
                    "meanResources":null,"progress":{},"progressRuns":0
                })
            })
    }
}

fn objective_rng_unit(state: &mut ObjectiveState, encounter: i64, seed: u64) -> f64 {
    let value = state.rng_states.entry(encounter).or_insert_with(|| {
        let mut x = seed ^ (encounter as u64).wrapping_add(0x9e3779b97f4a7c15);
        x ^= x >> 30;
        x = x.wrapping_mul(0xbf58476d1ce4e5b9);
        x ^= x >> 27;
        x = x.wrapping_mul(0x94d049bb133111eb);
        x ^= x >> 31;
        if x == 0 {
            0x9e3779b97f4a7c15
        } else {
            x
        }
    });
    *value ^= *value >> 12;
    *value ^= *value << 25;
    *value ^= *value >> 27;
    let output = value.wrapping_mul(0x2545f4914f6cdd1d);
    (output as f64) / (u64::MAX as f64)
}

fn active_stat_weight(counter: &LearnerCounter) -> f64 {
    (1.0 + 2.0
        * counter.improved as f64
        * ((counter.improved as f64 + 1.0) / (counter.attempts as f64 + 4.0)))
        .max(0.35)
}

fn choose_weighted_index(weights: &[f64], draw: f64) -> Result<usize, String> {
    let total = weights.iter().sum::<f64>();
    if weights.is_empty() || !total.is_finite() || total <= 0.0 {
        return Err("weighted choice has no positive choices".into());
    }
    let target = draw * total;
    let mut cumulative = 0.0;
    for (index, weight) in weights.iter().enumerate() {
        cumulative += *weight;
        if target <= cumulative {
            return Ok(index);
        }
    }
    Ok(weights.len() - 1)
}

fn controlled_stat_child(
    parent: &Value,
    config: &SyntheticDpsSearch,
    state: &mut ObjectiveState,
    encounter: i64,
    seed: u64,
) -> Result<(Value, String, String), String> {
    let params = &config.mutable_parameters;
    let stats = state
        .operator_stats
        .get(&encounter)
        .cloned()
        .unwrap_or_default();
    let weights = params
        .iter()
        .map(|pid| {
            active_stat_weight(
                stats
                    .get(&format!("stat:{pid}"))
                    .unwrap_or(&LearnerCounter::default()),
            )
        })
        .collect::<Vec<_>>();
    let target_index = choose_weighted_index(&weights, objective_rng_unit(state, encounter, seed))?;
    let parameter = params[target_index].clone();
    let current = parent["ownUnits"]
        .as_array()
        .and_then(|units| {
            units
                .iter()
                .find(|u| u["name"].as_str() == Some("Synthetic DPS"))
        })
        .and_then(|u| u["parameters"].get(&parameter))
        .and_then(|p| p["rawValue"].as_i64())
        .ok_or("Synthetic DPS mutable rawValue missing")?;
    let (minimum, maximum) = match parameter.as_str() {
        "13" => (6, 5766),
        "15" => (5, 4780),
        "16" => (5, 5204),
        _ => return Err("controlled mutable parameter is outside ATK/SPD/LCK".into()),
    };
    let scales = [1.05, 1.15, 1.4, 2.0, 3.0, 0.95, 0.7, 0.5];
    let scale_stats = state
        .scale_stats
        .get(&encounter)
        .and_then(|all| all.get(&parameter))
        .cloned()
        .unwrap_or_default();
    let mut scale_names = scales
        .iter()
        .map(|s| {
            let key = python_scale_key(*s);
            let counter = scale_stats.get(&key).cloned().unwrap_or_default();
            (key, active_scale_weight(&counter))
        })
        .collect::<Vec<_>>();
    let jump = 1.0 + scale_stats.values().filter(|c| c.improved > 0).count() as f64;
    scale_names.push(("jump".into(), jump));
    let scale_draw = objective_rng_unit(state, encounter, seed);
    let scale_idx = choose_weighted_index(
        &scale_names.iter().map(|(_, w)| *w).collect::<Vec<_>>(),
        scale_draw,
    )?;
    let (scale, _) = scale_names[scale_idx].clone();
    let anchor = state
        .stat_anchors
        .get(&encounter)
        .and_then(|map| map.get(&parameter))
        .copied()
        .unwrap_or(current)
        .max(1);
    let mut value = if scale == "jump" {
        minimum
            + (objective_rng_unit(state, encounter, seed) * ((maximum - minimum + 1) as f64))
                .floor() as i64
    } else {
        let factor = scale
            .parse::<f64>()
            .map_err(|_| "invalid controlled scale")?;
        let direction_draw = objective_rng_unit(state, encounter, seed);
        let direction = if direction_draw < 0.5 {
            1.0
        } else if factor > 1.0 {
            -1.0
        } else {
            1.0
        };
        (anchor as f64
            * (if direction > 0.0 {
                factor
            } else {
                1.0 / factor
            }))
        .round() as i64
    };
    value = value.clamp(minimum, maximum);
    if value == current {
        value = (current + if current < maximum { 1 } else { -1 }).clamp(minimum, maximum);
    }
    let mut child = parent.clone();
    let unit = child["ownUnits"]
        .as_array_mut()
        .and_then(|units| {
            units
                .iter_mut()
                .find(|u| u["name"].as_str() == Some("Synthetic DPS"))
        })
        .ok_or("Synthetic DPS unit missing")?;
    unit["parameters"][parameter.clone()]["rawValue"] = json!(value);
    let stats = state.operator_stats.entry(encounter).or_default();
    stats.entry("set-stat".into()).or_default().attempts += 1;
    stats
        .entry(format!("stat:{parameter}"))
        .or_default()
        .attempts += 1;
    state
        .scale_stats
        .entry(encounter)
        .or_default()
        .entry(parameter.clone())
        .or_default()
        .entry(scale.clone())
        .or_default()
        .attempts += 1;
    Ok((child, parameter, scale))
}

fn active_scale_weight(counter: &LearnerCounter) -> f64 {
    (1.0 + 2.0 * counter.improved as f64
        - 0.15 * (counter.attempts.saturating_sub(counter.improved) as f64))
        .max(0.35)
}
fn python_scale_key(scale: f64) -> String {
    let mut key = scale.to_string();
    if scale.fract() == 0.0 {
        key.push_str(".0");
    }
    key
}

fn validate_synthetic_dps_search(
    rows: &[Value],
    config: Option<&SyntheticDpsSearch>,
) -> Result<(), String> {
    let Some(config) = config else { return Ok(()) };
    let expected_fixed = BTreeMap::from([
        ("10".to_owned(), 2500),
        ("11".to_owned(), 2500),
        ("14".to_owned(), 2500),
        ("19".to_owned(), 18),
    ]);
    if config.fixed_parameters != expected_fixed || config.mutable_parameters != ["13", "15", "16"]
    {
        return Err("syntheticDpsSearch requires fixedParameters {10:2500,11:2500,14:2500,19:18} and mutableParameters [13,15,16]".into());
    }
    if rows.is_empty() {
        return Err("syntheticDpsSearch requires a supplied scenario".into());
    }
    for row in rows {
        let dps = row["ownUnits"]
            .as_array()
            .and_then(|units| {
                units.iter().find(|u| {
                    u["name"].as_str() == Some("Synthetic DPS")
                        && u["human"].as_bool() == Some(true)
                })
            })
            .ok_or("syntheticDpsSearch parent lacks the Synthetic DPS human")?;
        for (parameter, expected) in &expected_fixed {
            let actual = dps["parameters"]
                .get(parameter)
                .and_then(|p| p["rawValue"].as_i64())
                .ok_or_else(|| format!("Synthetic DPS fixed rawValue {parameter} missing"))?;
            if actual != *expected {
                return Err(format!(
                    "Synthetic DPS parameter {parameter} must have rawValue {expected}"
                ));
            }
        }
        for parameter in &config.mutable_parameters {
            let value = dps["parameters"]
                .get(parameter)
                .and_then(|p| p["rawValue"].as_i64())
                .ok_or_else(|| format!("Synthetic DPS mutable rawValue {parameter} missing"))?;
            let (low, high) = match parameter.as_str() {
                "13" => (6, 5766),
                "15" => (5, 4780),
                "16" => (5, 5204),
                _ => unreachable!(),
            };
            if !(low..=high).contains(&value) {
                return Err(format!("Synthetic DPS mutable parameter {parameter} value {value} is outside {low}..={high}"));
            }
        }
    }
    Ok(())
}

fn import_common_learner_prior(
    config: &Config,
    checkpoint_exists: bool,
    journal_has_records: bool,
    state: &mut ObjectiveState,
) -> Result<(), String> {
    match (
        config.common_learner_prior_path.as_ref(),
        config.common_learner_prior_sha256.as_deref(),
    ) {
        (None, None) => return Ok(()),
        (Some(_), None) | (None, Some(_)) => {
            return Err(
                "commonLearnerPriorPath and commonLearnerPriorSha256 must be provided together"
                    .into(),
            )
        }
        (Some(path), Some(expected)) => {
            if config.objective_mode != "mechanism-lanes-v3" {
                return Err(
                    "common learner prior requires objectiveMode=mechanism-lanes-v3".into(),
                );
            }
            if checkpoint_exists || config.initial_checkpoint.is_some() || journal_has_records {
                return Err("common learner prior may only initialize a fresh run with no checkpoint or journal records".into());
            }
            let bytes = fs::read(path).map_err(|error| {
                format!("read common learner prior {}: {error}", path.display())
            })?;
            let actual = engine::digest(&bytes);
            if actual != expected {
                return Err(format!(
                    "common learner prior SHA-256 mismatch: expected {expected}, got {actual}"
                ));
            }
            let mut document: Value = serde_json::from_slice(&bytes)
                .map_err(|error| format!("parse common learner prior: {error}"))?;
            apply_common_prior_document(&mut document, &actual, state)
        }
    }
}

fn apply_common_prior_document(
    document: &mut Value,
    actual_sha256: &str,
    state: &mut ObjectiveState,
) -> Result<(), String> {
    if document["schema"].as_str() != Some("ka-mechanism-learner-prior-1")
        || document["objectiveMode"].as_str() != Some("mechanism-lanes-v3")
    {
        return Err("common learner prior schema/objective mode is incompatible".into());
    }
    if let Some(encounters) = document["operatorStats"].as_object_mut() {
        for (_, operators) in encounters {
            let Some(operators) = operators.as_object_mut() else {
                continue;
            };
            for (legacy, pid) in [("stat:atk", "13"), ("stat:spd", "15"), ("stat:lck", "16")] {
                if let Some(counter) = operators.remove(legacy) {
                    operators.insert(format!("stat:{pid}"), counter);
                }
            }
        }
    }
    state.operator_stats = serde_json::from_value(document["operatorStats"].clone())
        .map_err(|error| format!("invalid prior operatorStats: {error}"))?;
    state.scale_stats = serde_json::from_value(document["statScales"].clone())
        .map_err(|error| format!("invalid prior statScales: {error}"))?;
    state.stat_anchors = serde_json::from_value(document["statAnchors"].clone())
        .map_err(|error| format!("invalid prior statAnchors: {error}"))?;
    state.learner_prior_sha256 = Some(actual_sha256.to_owned());
    state.learner_prior_source_identity = Some(document["sourceIdentity"].clone());
    Ok(())
}

fn seed_lineage(state: &mut ObjectiveState, candidates: &[Candidate]) -> Result<(), String> {
    for candidate in candidates {
        let sha = hash(candidate.intent.as_ref())?;
        state.lineage_roots.entry(sha.clone()).or_insert(sha);
    }
    Ok(())
}

fn objective_parent_sources(
    state: &ObjectiveState,
    encounter: i64,
    population: &[Candidate],
) -> Result<BTreeMap<String, Vec<String>>, String> {
    let mut records = state
        .records
        .iter()
        .filter(|(_, entry)| entry.encounter_id == encounter)
        .map(|(key, entry)| {
            let id = key.split(':').next().unwrap_or(key).to_owned();
            entry
                .aggregate
                .record(json!(id), entry.encounter_id, entry.defeat_count)
        })
        .collect::<Vec<_>>();
    let birth_rank = state
        .birth_order
        .iter()
        .enumerate()
        .map(|(rank, key)| (key.split(':').next().unwrap_or(key).to_owned(), rank))
        .collect::<BTreeMap<_, _>>();
    records.sort_by_key(|record| {
        birth_rank
            .get(record["candidate"].as_str().unwrap_or_default())
            .copied()
            .unwrap_or(usize::MAX)
    });
    let ranked = objective::elite_lanes(&records, objective::DEFAULT_POOL);
    let mut out = BTreeMap::new();
    for lane in objective::LANES {
        let mut ids = Vec::new();
        if let Some(groups) = ranked.as_object() {
            for (_, lanes) in groups {
                for id in lanes[lane].as_array().into_iter().flatten() {
                    if let Some(id) = id.as_str() {
                        if !ids.contains(&id.to_owned()) {
                            ids.push(id.to_owned());
                        }
                    }
                }
            }
        }
        out.insert(lane.to_owned(), ids);
    }
    let mut regions = Vec::<String>::new();
    let mut seen_regions = BTreeSet::<String>::new();
    let mut region_records = records.iter().collect::<Vec<_>>();
    region_records.sort_by(|a, b| a["candidate"].as_str().cmp(&b["candidate"].as_str()));
    for record in region_records {
        let id = record["candidate"].as_str().unwrap_or_default();
        let Some(entry) = state
            .records
            .iter()
            .find(|(key, _)| key.starts_with(&format!("{id}:")))
            .map(|(_, entry)| entry)
        else {
            continue;
        };
        let region = objective::strategy_region(&entry.intent)?;
        if seen_regions.insert(region) {
            regions.push(id.to_owned());
        }
    }
    out.insert("region".into(), regions);
    out.insert(
        "exploration".into(),
        population
            .iter()
            .map(|candidate| hash(candidate.intent.as_ref()))
            .collect::<Result<Vec<_>, _>>()?,
    );
    // The native journal has no explicit validation-phase provenance. Keep this
    // source empty rather than treating an arbitrary 64-run bank as validation.
    out.insert("mean".into(), Vec::<String>::new());
    Ok(out)
}

fn weighted_parent(
    state: &mut ObjectiveState,
    encounter: i64,
    pool: &[String],
    seed: u64,
) -> Result<String, String> {
    if pool.is_empty() {
        return Err("active parent source is empty".into());
    }
    let weights = pool
        .iter()
        .map(|id| {
            let root = state.lineage_roots.get(id).unwrap_or(id);
            let prod = state
                .root_productivity
                .get(root)
                .cloned()
                .unwrap_or_default();
            0.5 + (prod.improved as f64 + 1.0) / (prod.children as f64 + 2.0)
        })
        .collect::<Vec<_>>();
    let draw = objective_rng_unit(state, encounter, seed);
    Ok(pool[choose_weighted_index(&weights, draw)?].clone())
}

fn apply_lane_feedback(
    state: &mut ObjectiveState,
    encounter: i64,
    candidate_sha: &str,
    operator: &str,
    improvements: &[String],
    stat_target: Option<&str>,
    stat_value: Option<i64>,
) -> bool {
    if !state.rewarded_children.insert(candidate_sha.to_owned()) {
        return false;
    }
    let improved = !improvements.is_empty();
    let root = state
        .lineage_roots
        .get(candidate_sha)
        .cloned()
        .unwrap_or_else(|| candidate_sha.to_owned());
    let productivity = state.root_productivity.entry(root).or_default();
    productivity.children = productivity.children.saturating_add(1);
    if improved {
        productivity.improved = productivity.improved.saturating_add(1);
    }
    let stats = state.operator_stats.entry(encounter).or_default();
    let operator_stats = stats.entry(operator.to_owned()).or_default();
    if improved {
        operator_stats.improved = operator_stats.improved.saturating_add(1);
    }
    if improved && operator == "set-stat" {
        if let (Some(parameter), Some(value)) = (stat_target, stat_value) {
            state
                .stat_anchors
                .entry(encounter)
                .or_default()
                .insert(parameter.to_owned(), value);
        }
    }
    true
}

#[derive(Default)]
struct StageTelemetry {
    enabled: bool,
    output: Option<PathBuf>,
    complete: bool,
    written: bool,
    coordinator_seconds: BTreeMap<String, f64>,
    worker_seconds: BTreeMap<String, f64>,
    worker_counts: BTreeMap<String, u64>,
    worker_trial_seconds: f64,
    worker_trials: u64,
    journal: Value,
    requested_batch: Option<usize>,
    effective_batch: usize,
    run_timer: Option<Instant>,
}

fn timing_start(enabled: bool) -> Option<Instant> {
    if enabled {
        Some(Instant::now())
    } else {
        None
    }
}

impl StageTelemetry {
    fn flush(&mut self) -> Result<(), String> {
        self.written = true;
        if self.enabled {
            if let Some(path) = &self.output {
                save(path, &self.snapshot())?;
            }
        }
        Ok(())
    }
    fn wall(&mut self, stage: &str, timer: Option<Instant>) {
        if let Some(timer) = timer {
            *self.coordinator_seconds.entry(stage.into()).or_default() +=
                timer.elapsed().as_secs_f64();
        }
    }

    fn observe(&mut self, completion: &Completion) {
        if !self.enabled {
            return;
        }
        self.worker_trials += 1;
        if let Some(seconds) = completion.worker_seconds {
            self.worker_trial_seconds += seconds;
        }
        if let Some(seconds) = completion.preparation_seconds {
            *self
                .worker_seconds
                .entry("rawPreparation".into())
                .or_default() += seconds;
            *self
                .worker_counts
                .entry("rawPreparation".into())
                .or_default() += 1;
        }
        if let Ok((_, report, _)) = &completion.result {
            if let Some(seconds) = report["stageTimings"]["seconds"].as_object() {
                for (name, value) in seconds {
                    if let Some(value) = value.as_f64() {
                        *self.worker_seconds.entry(name.clone()).or_default() += value;
                    }
                }
            }
            if let Some(counts) = report["stageTimings"]["counts"].as_object() {
                for (name, value) in counts {
                    if let Some(value) = value.as_u64() {
                        *self.worker_counts.entry(name.clone()).or_default() += value;
                    }
                }
            }
        }
    }

    fn snapshot(&self) -> Value {
        json!({"schema":"ka-rust-comparison-stage-timings-v1", "complete":self.complete,
            "processWallSecondsBeforeTelemetryWrite":self.run_timer.map(|timer| timer.elapsed().as_secs_f64()),
            "coordinatorWallSeconds":self.coordinator_seconds,
            "workerOperationsSummedSeconds":self.worker_seconds,"workerOperationCounts":self.worker_counts,
            "workerTrialTotalSummedSeconds":self.worker_trial_seconds,"observedWorkerTrials":self.worker_trials,
            "journal":self.journal,"requestedResultSyncBatchSize":self.requested_batch,"effectiveResultSyncBatchSize":self.effective_batch,
            "interpretation":"Coordinator intervals are elapsed wall spans. Worker operation durations are summed across concurrent workers and overlap coordinator spans. Worker trial totals include nested operations; never add them together or subtract them from process wall.",
            "unavailable":["process start-to-exit (measure externally)","isolated raw admission versus raw preparation","worker queue wait and shutdown breakdown","failed native operation breakdown; outer failed trial is counted"],
            "limits":"Fresh process only counters; journal sync excludes checkpoint/status sync. Timing file writes and final status pacing are outside named stages."})
    }
}

impl Drop for StageTelemetry {
    fn drop(&mut self) {
        if self.enabled && !self.written {
            if let Some(path) = &self.output {
                if let Err(error) = save(path, &self.snapshot()) {
                    eprintln!("{}", json!({"telemetryWriteError":error}));
                }
            }
        }
    }
}

fn default_candidate_limit() -> usize {
    4096
}

#[derive(Clone)]
struct Candidate {
    intent: Arc<Value>,
    source_candidate_id: Value,
    parent_candidate_sha256: Option<String>,
    mutation_operator: Option<String>,
    mutation_target: Option<String>,
    mutation_scale: Option<String>,
}

#[derive(Default, Clone)]
struct ControlState {
    focus_encounter_ids: Option<Vec<i64>>,
    pause_requested: bool,
    stop_requested: bool,
}

thread_local! {
    static CONTROL_CACHE: std::cell::RefCell<Option<(Instant, PathBuf, ControlState)>> = const { std::cell::RefCell::new(None) };
}

fn read_control(config: &Config) -> Result<ControlState, String> {
    let Some(path) = &config.control_path else {
        return Ok(ControlState::default());
    };
    if let Some(cached) = CONTROL_CACHE.with(|cache| {
        cache
            .borrow()
            .as_ref()
            .filter(|(time, cached_path, _)| {
                cached_path == path && time.elapsed() < STATUS_INTERVAL
            })
            .map(|(_, _, state)| state.clone())
    }) {
        return Ok(cached);
    }
    if !path.exists() {
        return Ok(ControlState::default());
    }
    let value =
        read(path).map_err(|error| format!("read control file {}: {error}", path.display()))?;
    let mut focus = value
        .get("focusEncounterIds")
        .map(|rows| {
            rows.as_array()
                .ok_or("control focusEncounterIds must be an array")?
                .iter()
                .map(|row| {
                    row.as_i64()
                        .ok_or("control focusEncounterIds must contain integers")
                })
                .collect::<Result<Vec<_>, _>>()
        })
        .transpose()?;
    if let Some(ids) = &mut focus {
        if ids.iter().any(|id| !(0..20).contains(id))
            || ids.iter().copied().collect::<HashSet<_>>().len() != ids.len()
        {
            return Err("control focusEncounterIds must contain unique IDs in 0..19".into());
        }
        ids.sort_unstable();
    }
    let state = ControlState {
        focus_encounter_ids: focus,
        pause_requested: value["pauseRequested"].as_bool().unwrap_or(false),
        stop_requested: value["stopRequested"].as_bool().unwrap_or(false),
    };
    CONTROL_CACHE
        .with(|cache| *cache.borrow_mut() = Some((Instant::now(), path.clone(), state.clone())));
    Ok(state)
}

#[derive(Clone)]
struct Work {
    index: usize,
    key: String,
    raw: Value,
    intent: Arc<Value>,
    source_candidate_id: Value,
    parent_candidate_sha256: Option<String>,
    mutation_operator: Option<String>,
    math_seed: i32,
    lib_seed: i32,
}

struct Completion {
    worker: usize,
    work: Work,
    result: Result<(Value, Value, Value), String>,
    preparation_seconds: Option<f64>,
    worker_seconds: Option<f64>,
}

struct Pool {
    senders: Vec<mpsc::SyncSender<Option<Work>>>,
    results: mpsc::Receiver<Completion>,
    handles: Vec<std::thread::JoinHandle<()>>,
}

impl Pool {
    fn new(
        executors: usize,
        dll: &Path,
        catalog: Arc<Value>,
        diagnostic_dump: Option<PathBuf>,
        telemetry_enabled: bool,
        replay_trace: bool,
    ) -> Result<Self, String> {
        let (result_tx, results) = mpsc::sync_channel(executors);
        let (ready_tx, ready_rx) = mpsc::channel();
        let mut pool = Self {
            senders: Vec::with_capacity(executors),
            results,
            handles: Vec::with_capacity(executors),
        };

        for worker in 0..executors {
            let (tx, rx) = mpsc::sync_channel::<Option<Work>>(1);
            let done = result_tx.clone();
            let ready = ready_tx.clone();
            let path = dll.to_owned();
            let facts = catalog.clone();
            let diagnostic_dump = diagnostic_dump.clone();
            pool.senders.push(tx);
            let handle = std::thread::Builder::new()
                .name(format!("ka-rust-native-{worker}"))
                .spawn(move || {
                    let mut native = match engine::NativeEngine::load(&path) {
                        Ok(native) => {
                            let _ = ready.send(Ok(()));
                            native
                        }
                        Err(error) => {
                            let _ = ready.send(Err(error));
                            return;
                        }
                    };
                    while let Ok(Some(work)) = rx.recv() {
                        let worker_timer = timing_start(telemetry_enabled);
                        let mut preparation_seconds = None;
                        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                            (|| {
                                // Preparation is intentionally per trial: each raw scenario
                                // includes the exact math/lib seeds dispatched to the engine.
                                let preparation_timer = timing_start(telemetry_enabled);
                                let preparation_result = prepare::prepare(&work.raw, &facts);
                                preparation_seconds =
                                    preparation_timer.map(|timer| timer.elapsed().as_secs_f64());
                                let prepared = preparation_result?;
                                let ticks =
                                    work.raw["tickLimit"].as_u64().ok_or("tickLimit absent")?;
                                let ticks =
                                    u32::try_from(ticks).map_err(|_| "tickLimit exceeds u32")?;
                                let policy =
                                    match work.raw["finishPolicy"].as_str().unwrap_or("at-horizon")
                                    {
                                        "at-horizon" => 0,
                                        "after-ending" => 1,
                                        "on-verdict" => 2,
                                        _ => return Err("unsupported finishPolicy".into()),
                                    };
                                let mut report = native.run(
                                    &prepared,
                                    work.math_seed,
                                    work.lib_seed,
                                    ticks,
                                    policy,
                                    diagnostic_dump
                                        .as_ref()
                                        .map(|directory| directory.join(&work.key))
                                        .as_deref(),
                                    telemetry_enabled,
                                    replay_trace,
                                )?;
                                let initialized = report
                                    .as_object_mut()
                                    .ok_or("native report is not an object")?
                                    .remove("initializedPrepared")
                                    .ok_or("native report lacks actual initialized state")?;
                                Ok((initialized, report, native.kernel_provenance()))
                            })()
                        }))
                        .unwrap_or_else(|_| {
                            Err("native worker panicked while processing trial".into())
                        });
                        let panicked = matches!(
                            &result,
                            Err(error) if error == "native worker panicked while processing trial"
                        );
                        if done
                            .send(Completion {
                                worker,
                                work,
                                result,
                                preparation_seconds,
                                worker_seconds: worker_timer
                                    .map(|timer| timer.elapsed().as_secs_f64()),
                            })
                            .is_err()
                        {
                            break;
                        }
                        if panicked {
                            break;
                        }
                    }
                })
                .map_err(|error| format!("spawn native worker {worker}: {error}"))?;
            pool.handles.push(handle);
        }
        drop(result_tx);
        drop(ready_tx);

        for _ in 0..executors {
            ready_rx
                .recv()
                .map_err(|_| "native worker startup channel closed")??;
        }
        Ok(pool)
    }
}

impl Drop for Pool {
    fn drop(&mut self) {
        // Release workers waiting to send results before joining them. This also
        // unblocks startup-failure cleanup when only some workers loaded the DLL.
        let (_, empty) = mpsc::channel();
        self.results = empty;
        self.senders.clear();
        for handle in self.handles.drain(..) {
            let _ = handle.join();
        }
    }
}

struct StatusWriter {
    path: PathBuf,
    last_write: Option<Instant>,
}

impl StatusWriter {
    fn new(path: PathBuf) -> Self {
        Self {
            path,
            last_write: None,
        }
    }

    fn maybe_write(&mut self, value: &Value) -> Result<(), String> {
        if self
            .last_write
            .is_some_and(|last| last.elapsed() < STATUS_INTERVAL)
        {
            return Ok(());
        }
        self.write(value)
    }

    fn write_final(&mut self, value: &Value) -> Result<(), String> {
        // Completion must be observable immediately; only routine progress writes
        // are throttled. Sleeping here adds artificial latency to every bounded batch.
        self.write(value)
    }

    fn write(&mut self, value: &Value) -> Result<(), String> {
        save(&self.path, value)?;
        self.last_write = Some(Instant::now());
        Ok(())
    }
}

#[derive(Default)]
struct Counters {
    measured: usize,
    reused: usize,
    rejected: usize,
    fresh_rejected: usize,
}

struct TrialCursor {
    candidate: usize,
    seed: usize,
    seed_major: bool,
}

impl TrialCursor {
    fn new(seed_major: bool) -> Self {
        Self {
            candidate: 0,
            seed: 0,
            seed_major,
        }
    }

    fn next(&mut self, candidate_count: usize, seed_count: usize) -> Option<(usize, usize)> {
        if self.candidate >= candidate_count || seed_count == 0 || self.seed >= seed_count {
            return None;
        }
        let pair = (self.candidate, self.seed);
        if self.seed_major {
            self.candidate += 1;
            if self.candidate == candidate_count {
                self.candidate = 0;
                self.seed += 1;
            }
        } else {
            self.seed += 1;
            if self.seed == seed_count {
                self.seed = 0;
                self.candidate += 1;
            }
        }
        Some(pair)
    }

    fn remaining(&self, candidate_count: usize, seed_count: usize) -> usize {
        if self.candidate >= candidate_count || self.seed >= seed_count {
            return 0;
        }
        if self.seed_major {
            (seed_count - self.seed - 1) * candidate_count + (candidate_count - self.candidate)
        } else {
            (candidate_count - self.candidate - 1) * seed_count + (seed_count - self.seed)
        }
    }
}

fn read(path: &Path) -> Result<Value, String> {
    serde_json::from_slice(&fs::read(path).map_err(|e| format!("read {}: {e}", path.display()))?)
        .map_err(|e| format!("parse {}: {e}", path.display()))
}

fn bytes(value: &Value) -> Result<Vec<u8>, String> {
    serde_json::to_vec(value).map_err(|e| e.to_string())
}

fn hash<T: serde::Serialize>(value: &T) -> Result<String, String> {
    let encoded = serde_json::to_vec(value).map_err(|e| e.to_string())?;
    Ok(engine::digest(&encoded))
}

fn source_digests() -> Value {
    json!({
        "optimizer/Cargo.toml": engine::digest(include_bytes!("../Cargo.toml")),
        "optimizer/Cargo.lock": engine::digest(include_bytes!("../Cargo.lock")),
        "optimizer/shared-kernel/Cargo.toml": engine::digest(include_bytes!("../shared-kernel/Cargo.toml")),
        "optimizer/src/main.rs": engine::digest(include_bytes!("main.rs")),
        "optimizer/src/input.rs": engine::digest(include_bytes!("input.rs")),
        "optimizer/src/prepare.rs": engine::digest(include_bytes!("prepare.rs")),
        "optimizer/src/search.rs": engine::digest(include_bytes!("search.rs")),
        "optimizer/src/objective.rs": engine::digest(include_bytes!("objective.rs")),
        "optimizer/src/store.rs": engine::digest(include_bytes!("store.rs")),
        "optimizer/src/engine.rs": engine::digest(include_bytes!("engine.rs")),
        "optimizer/src/tuning.rs": engine::digest(include_bytes!("tuning.rs")),
        "optimizer/src/fixed_formation.rs": engine::digest(include_bytes!("fixed_formation.rs")),
        "optimizer/src/fixed_formation_policy.json": engine::digest(include_bytes!("fixed_formation_policy.json")),
        "canonical/stat-bounds.json": engine::digest(include_bytes!("../../../../../RE-evidence/20260922-search-contract/stat-bounds.json")),
        "shared-engine/Cargo.toml": engine::digest(include_bytes!("../shared-kernel/Cargo.toml")),
        "shared-engine/canonical-Cargo.toml": engine::digest(include_bytes!("../../../../../KA-Website/tools/recovery/native/ka_kernel/Cargo.toml")),
        "shared-engine/Cargo.lock": engine::digest(include_bytes!("../../../../../KA-Website/tools/recovery/native/ka_kernel/Cargo.lock")),
        "shared-engine/src/ai.rs": engine::digest(include_bytes!("../../../../../KA-Website/tools/recovery/native/ka_kernel/src/ai.rs")),
        "shared-engine/src/battle.rs": engine::digest(include_bytes!("../../../../../KA-Website/tools/recovery/native/ka_kernel/src/battle.rs")),
        "shared-engine/src/battle_control.rs": engine::digest(include_bytes!("../../../../../KA-Website/tools/recovery/native/ka_kernel/src/battle_control.rs")),
        "shared-engine/src/combat.rs": engine::digest(include_bytes!("../../../../../KA-Website/tools/recovery/native/ka_kernel/src/combat.rs")),
        "shared-engine/src/lib.rs": engine::digest(include_bytes!("../../../../../KA-Website/tools/recovery/native/ka_kernel/src/lib.rs")),
        "shared-engine/src/params.rs": engine::digest(include_bytes!("../../../../../KA-Website/tools/recovery/native/ka_kernel/src/params.rs")),
        "shared-engine/src/phases.rs": engine::digest(include_bytes!("../../../../../KA-Website/tools/recovery/native/ka_kernel/src/phases.rs")),
        "shared-engine/src/profile.rs": engine::digest(include_bytes!("../../../../../KA-Website/tools/recovery/native/ka_kernel/src/profile.rs")),
        "shared-engine/src/rng.rs": engine::digest(include_bytes!("../../../../../KA-Website/tools/recovery/native/ka_kernel/src/rng.rs")),
        "shared-engine/src/state.rs": engine::digest(include_bytes!("../../../../../KA-Website/tools/recovery/native/ka_kernel/src/state.rs")),
        "shared-engine/src/world.rs": engine::digest(include_bytes!("../../../../../KA-Website/tools/recovery/native/ka_kernel/src/world.rs"))
    })
}

fn source_info() -> Result<Value, String> {
    let sources = source_digests();
    Ok(json!({
        "schema": "ka-rust-optimizer-source-info-v1",
        "sourceSha256": sources,
        "sourceSetSha256": hash(&sources)?
    }))
}

#[cfg(windows)]
fn atomic_replace(temp: &Path, target: &Path) -> Result<(), String> {
    use std::os::windows::ffi::OsStrExt;
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn MoveFileExW(existing: *const u16, replacement: *const u16, flags: u32) -> i32;
    }
    let existing: Vec<u16> = temp.as_os_str().encode_wide().chain(Some(0)).collect();
    let replacement: Vec<u16> = target.as_os_str().encode_wide().chain(Some(0)).collect();
    let ok = unsafe { MoveFileExW(existing.as_ptr(), replacement.as_ptr(), 0x1 | 0x8) };
    if ok != 0 {
        Ok(())
    } else {
        Err(format!(
            "atomic checkpoint replacement failed: {}",
            std::io::Error::last_os_error()
        ))
    }
}

#[cfg(not(windows))]
fn atomic_replace(temp: &Path, target: &Path) -> Result<(), String> {
    fs::rename(temp, target).map_err(|e| format!("atomic replace: {e}"))
}

fn save(path: &Path, value: &Value) -> Result<(), String> {
    let parent = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    fs::create_dir_all(parent).map_err(|e| format!("create {}: {e}", parent.display()))?;
    let name = path
        .file_name()
        .ok_or("output path has no file name")?
        .to_string_lossy();
    let pending = parent.join(format!(".{name}.pending-{}", std::process::id()));
    let result = (|| {
        let mut file = File::create(&pending).map_err(|e| format!("create pending output: {e}"))?;
        file.write_all(&bytes(value)?)
            .map_err(|e| format!("write pending output: {e}"))?;
        file.write_all(b"\n")
            .map_err(|e| format!("finish pending output: {e}"))?;
        file.sync_all()
            .map_err(|e| format!("sync pending output: {e}"))?;
        drop(file);
        atomic_replace(&pending, path)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&pending);
    }
    result
}

fn read_checkpoint(primary: &Path) -> Result<Option<Value>, String> {
    let backup = primary.with_extension("previous");
    let mut failures = Vec::new();
    let mut found = false;
    for path in [primary, backup.as_path()] {
        if !path.exists() {
            continue;
        }
        found = true;
        match read(path) {
            Ok(value) => return Ok(Some(value)),
            Err(error) => failures.push(format!("{}: {error}", path.display())),
        }
    }
    if found {
        Err(format!("no readable checkpoint; {}", failures.join("; ")))
    } else {
        Ok(None)
    }
}

fn save_checkpoint(path: &Path, value: &Value) -> Result<(), String> {
    // Keep the last known good checkpoint as recovery material while also using
    // atomic replacement for the live file.
    let backup = path.with_extension("previous");
    if path.exists() {
        let current = read(path);
        if current.is_err() && backup.exists() && read(&backup).is_ok() {
            // Do not replace the only readable checkpoint with a corrupt file.
            let pending = path.with_extension(format!("pending-{}", std::process::id()));
            save(&pending, value)?;
            let _ = fs::remove_file(path);
            return atomic_replace(&pending, path);
        }
        if backup.exists() {
            fs::remove_file(&backup).map_err(|e| format!("remove old checkpoint backup: {e}"))?;
        }
        fs::copy(path, &backup).map_err(|e| format!("preserve prior checkpoint: {e}"))?;
        // Windows FlushFileBuffers requires a writable handle. The backup
        // contents are preserved; opening read-only makes sync_all fail.
        fs::OpenOptions::new()
            .read(true)
            .write(true)
            .open(&backup)
            .and_then(|f| f.sync_all())
            .map_err(|e| format!("sync checkpoint backup: {e}"))?;
    }
    save(path, value)
}

fn resolve_paths(config: &mut Config, base: &Path) {
    if let Some(path) = &mut config.diagnostic_dump_directory {
        if path.is_relative() {
            *path = base.join(&*path);
        }
    }
    for path in [
        &mut config.kernel,
        &mut config.catalog,
        &mut config.candidates,
        &mut config.output,
    ] {
        if path.is_relative() {
            *path = base.join(&*path);
        }
    }
    if let Some(path) = &mut config.control_path {
        if path.is_relative() {
            *path = base.join(&*path);
        }
    }
    if let Some(path) = &mut config.initial_checkpoint {
        if path.is_relative() {
            *path = base.join(&*path);
        }
    }
    if let Some(path) = &mut config.common_learner_prior_path {
        if path.is_relative() {
            *path = base.join(&*path);
        }
    }
}

fn load_candidates(config: &Config) -> Result<(Vec<Value>, Vec<Value>, String), String> {
    let source_len = fs::metadata(&config.candidates)
        .map_err(|e| format!("stat candidate source: {e}"))?
        .len();
    if source_len > 256 * 1024 * 1024 {
        return Err("candidate source exceeds the 256 MiB admission bound".into());
    }
    let source_bytes = fs::read(&config.candidates)
        .map_err(|e| format!("read candidate source {}: {e}", config.candidates.display()))?;
    let source_sha256 = engine::digest(&source_bytes);
    let is_sqlite = source_bytes.starts_with(b"SQLite format 3\0");
    let selected_encounters = if let Some(encounter) = config.encounter_filter {
        vec![encounter]
    } else if let Some(encounters) = config
        .encounter_filters
        .as_ref()
        .filter(|rows| !rows.is_empty())
    {
        encounters.clone()
    } else {
        (0..20).collect()
    };
    let (candidate_value, source_ids) = if is_sqlite {
        let mut candidates = Vec::new();
        let mut ids = Vec::new();
        for encounter in &selected_encounters {
            let (rows, row_ids) = input::load_with_identity(
                &config.candidates,
                Some(*encounter),
                config.candidate_limit,
            )?;
            candidates.extend(
                rows.as_array()
                    .ok_or("SQLite loader returned non-array candidates")?
                    .iter()
                    .cloned(),
            );
            ids.extend(
                row_ids
                    .as_array()
                    .ok_or("SQLite loader returned non-array identities")?
                    .iter()
                    .cloned(),
            );
        }
        (Value::Array(candidates), Value::Array(ids))
    } else {
        let value: Value = serde_json::from_slice(&source_bytes)
            .map_err(|e| format!("candidate source is neither SQLite nor JSON: {e}"))?;
        let rows = value
            .as_array()
            .ok_or("JSON candidates must be a raw scenario array")?;
        let mut accepted = Vec::new();
        let mut ids = Vec::new();
        for encounter in &selected_encounters {
            let mut encounter_count = 0usize;
            for row in rows {
                if row["encounterId"].as_i64() != Some(*encounter) {
                    continue;
                }
                if encounter_count == config.candidate_limit {
                    break;
                }
                encounter_count += 1;
                accepted.push(row.clone());
                ids.push(row.get("id").cloned().unwrap_or(Value::Null));
            }
        }
        (Value::Array(accepted), Value::Array(ids))
    };
    if is_sqlite {
        let after_sha256 = engine::digest(
            &fs::read(&config.candidates)
                .map_err(|e| format!("re-read SQLite source identity: {e}"))?,
        );
        if after_sha256 != source_sha256 {
            return Err("SQLite candidate source changed during read-only admission".into());
        }
    }
    let candidates = match candidate_value {
        Value::Array(rows) => rows,
        _ => return Err("candidate loader returned a non-array".into()),
    };
    let ids = match source_ids {
        Value::Array(ids) => ids,
        _ => return Err("candidate identity loader returned a non-array".into()),
    };
    if candidates.len() != ids.len() {
        return Err(format!(
            "candidate identity count {} differs from row count {}",
            ids.len(),
            candidates.len()
        ));
    }
    if candidates.is_empty() {
        return Err("candidate source has no admitted raw scenarios".into());
    }
    let mut encounter_counts = BTreeMap::<i64, usize>::new();
    for candidate in &candidates {
        let encounter = candidate["encounterId"]
            .as_i64()
            .ok_or("candidate encounterId must be an integer")?;
        if !(0..20).contains(&encounter) {
            return Err(format!("candidate encounterId outside 0..19: {encounter}"));
        }
        let count = encounter_counts.entry(encounter).or_default();
        *count += 1;
        if *count > config.candidates_per_generation {
            return Err(format!(
                "{} admitted candidates for encounter {encounter} exceed candidatesPerGeneration {}; raise the per-encounter population or lower candidateLimit",
                *count, config.candidates_per_generation
            ));
        }
    }
    let mut total_bytes = 0usize;
    for candidate in &candidates {
        total_bytes = total_bytes
            .checked_add(bytes(candidate)?.len())
            .ok_or("admitted scenario byte count overflow")?;
        if total_bytes > MAX_ADMITTED_SCENARIO_BYTES {
            return Err("admitted scenarios exceed the 256 MiB serialized input bound".into());
        }
    }
    Ok((candidates, ids, source_sha256))
}

fn candidate_json(candidate: &Candidate) -> Value {
    json!({
        "intent": candidate.intent.as_ref(),
        "sourceCandidateId": candidate.source_candidate_id,
        "parentCandidateSha256": candidate.parent_candidate_sha256
        ,"mutationOperator": candidate.mutation_operator,
        "mutationTarget":candidate.mutation_target,
        "mutationScale":candidate.mutation_scale
    })
}

fn candidate_from_json(value: &Value) -> Result<Candidate, String> {
    Ok(Candidate {
        intent: Arc::new(
            value
                .get("intent")
                .cloned()
                .ok_or("checkpoint parent lacks intent")?,
        ),
        source_candidate_id: value
            .get("sourceCandidateId")
            .or_else(|| value.get("inputSourceCandidateId"))
            .cloned()
            .unwrap_or(Value::Null),
        parent_candidate_sha256: value
            .get("parentCandidateSha256")
            .and_then(Value::as_str)
            .map(str::to_owned),
        mutation_operator: value
            .get("mutationOperator")
            .and_then(Value::as_str)
            .map(str::to_owned),
        mutation_target: value
            .get("mutationTarget")
            .and_then(Value::as_str)
            .map(str::to_owned),
        mutation_scale: value
            .get("mutationScale")
            .and_then(Value::as_str)
            .map(str::to_owned),
    })
}

fn check_attestation(
    attestation: Option<&Value>,
    scope_sha256: &str,
    sources: &Value,
) -> Result<Option<Value>, String> {
    let Some(attestation) = attestation else {
        return Ok(None);
    };
    let object = attestation
        .as_object()
        .ok_or("parityAttestation must be an object")?;
    const REQUIRED: [&str; 5] = [
        "coordinatorThreadId",
        "scopeSha256",
        "sourceSha256",
        "parityReportSha256",
        "passed",
    ];
    if object.len() != REQUIRED.len() || REQUIRED.iter().any(|key| !object.contains_key(*key)) {
        return Err("parityAttestation must contain exactly the five Chat1 fields".into());
    }
    if object["scopeSha256"].as_str() != Some(scope_sha256)
        || &object["sourceSha256"] != sources
        || object["passed"].as_bool() != Some(true)
    {
        return Err("parityAttestation does not match this Chat1 scope and source set".into());
    }
    let report_hash = object["parityReportSha256"]
        .as_str()
        .ok_or("parityReportSha256 must be a SHA-256 hex digest")?;
    if report_hash.len() != 64 || !report_hash.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err("parityReportSha256 must be a SHA-256 hex digest".into());
    }
    Ok(Some(attestation.clone()))
}

fn diagnostic_intent(intent: &Value) -> bool {
    intent["finishPolicy"].as_str() == Some("on-verdict")
}

fn resolved_earned(report: &Value) -> Option<f64> {
    if report["completed"].as_bool() != Some(true) {
        return None;
    }
    match report["earnedBasis"].as_str()? {
        "native-win-loss-gate" | "reward-entitlement-certificate" | "queued-at-victory" => {}
        _ => return None,
    }
    report["earned"]
        .as_f64()
        .filter(|earned| earned.is_finite() && *earned >= 0.0)
}

fn production_purpose(config: &Config) -> bool {
    if config.execution_mode != "production" {
        return false;
    }
    let purpose = config.policy_provenance["purpose"]
        .as_str()
        .unwrap_or("")
        .to_ascii_lowercase();
    !["diagnostic", "synthetic", "invalid", "verification"]
        .iter()
        .any(|v| purpose.contains(v))
        && config.diagnostic_dump_directory.is_none()
}

fn score_saved(record: &Value, scope_sha256: &str) -> Option<f64> {
    if record["recordKind"].as_str() != Some("battle")
        || record["scopeSha256"].as_str() != Some(scope_sha256)
        || record["outcomeClassification"].as_str() != Some("resolved")
    {
        return None;
    }
    resolved_earned(&record["report"])
}

type SeedBanks = BTreeMap<i64, Vec<[i32; 2]>>;
type CompletedSlots = BTreeMap<String, Value>;

fn slot_key(candidate: &str, pair: [i32; 2]) -> String {
    format!("{candidate}:{}:{}", pair[0], pair[1])
}

fn journal_key(
    scope: &str,
    candidate: &str,
    encounter: i64,
    pair: [i32; 2],
    mixed: bool,
) -> Result<String, String> {
    let mut identity =
        json!({"scope":scope,"candidate":candidate,"mathSeed":pair[0],"libSeed":pair[1]});
    if mixed {
        identity["encounterId"] = json!(encounter);
    }
    hash(&identity)
}

fn slot_from_record(record: &Value) -> Result<Value, String> {
    let candidate = record["candidateSha256"]
        .as_str()
        .ok_or("saved slot lacks candidate hash")?;
    let math = record["mathSeed"]
        .as_i64()
        .and_then(|n| i32::try_from(n).ok())
        .ok_or("saved slot lacks math seed")?;
    let lib = record["libSeed"]
        .as_i64()
        .and_then(|n| i32::try_from(n).ok())
        .ok_or("saved slot lacks lib seed")?;
    let rejected = record["recordKind"].as_str() == Some("rejection");
    Ok(
        json!({"candidateSha256":candidate,"mathSeed":math,"libSeed":lib,
        "earned":if rejected {None} else {resolved_earned(&record["report"])},"rejected":rejected}),
    )
}

fn load_completed_slots(
    checkpoint: &Value,
    source: Option<&Path>,
) -> Result<CompletedSlots, String> {
    let mut slots = CompletedSlots::new();
    for row in checkpoint["completedPendingSlots"]
        .as_array()
        .into_iter()
        .flatten()
    {
        let candidate = row["candidateSha256"]
            .as_str()
            .ok_or("completed slot lacks candidate hash")?;
        let math = row["mathSeed"]
            .as_i64()
            .and_then(|n| i32::try_from(n).ok())
            .ok_or("completed slot lacks math seed")?;
        let lib = row["libSeed"]
            .as_i64()
            .and_then(|n| i32::try_from(n).ok())
            .ok_or("completed slot lacks lib seed")?;
        if !row["rejected"].is_boolean()
            || !(row["earned"].is_null() || row["earned"].as_f64().is_some_and(f64::is_finite))
        {
            return Err("invalid completed slot outcome".into());
        }
        if slots
            .insert(slot_key(candidate, [math, lib]), row.clone())
            .is_some()
        {
            return Err("duplicate completed slot".into());
        }
    }
    // Backfill crash-safe durable records without rewriting or importing the old ledger.
    if let Some(path) = source.filter(|path| path.is_file()) {
        let mut reader = BufReader::new(File::open(path).map_err(|e| e.to_string())?);
        let mut line = Vec::new();
        loop {
            line.clear();
            if reader
                .read_until(b'\n', &mut line)
                .map_err(|e| e.to_string())?
                == 0
            {
                break;
            }
            if line.len() > 64 * 1024 * 1024 {
                return Err("resume ledger row exceeds 64 MiB".into());
            }
            if line.last() != Some(&b'\n') {
                break;
            }
            if line.iter().all(u8::is_ascii_whitespace) {
                continue;
            }
            let record: Value =
                serde_json::from_slice(&line).map_err(|e| format!("resume ledger: {e}"))?;
            if record["scopeSha256"] != checkpoint["scopeSha256"]
                || record["generation"] != checkpoint["pendingGeneration"]
            {
                continue;
            }
            let row = slot_from_record(&record)?;
            let pair = [
                row["mathSeed"].as_i64().unwrap() as i32,
                row["libSeed"].as_i64().unwrap() as i32,
            ];
            slots.insert(
                slot_key(row["candidateSha256"].as_str().unwrap(), pair),
                row,
            );
        }
    }
    Ok(slots)
}

fn collect_pending_slots(
    population: &[Candidate],
    banks: &SeedBanks,
    prior: &CompletedSlots,
    journal: &store::Store,
    scope: &str,
    mixed: bool,
) -> Result<CompletedSlots, String> {
    let mut slots = CompletedSlots::new();
    for candidate in population {
        let encounter = candidate.intent["encounterId"]
            .as_i64()
            .ok_or("pending candidate lacks encounter")?;
        let candidate_sha = hash(candidate.intent.as_ref())?;
        for pair in banks
            .get(&encounter)
            .ok_or("pending encounter lacks seed bank")?
        {
            let key = slot_key(&candidate_sha, *pair);
            if let Some(row) = prior.get(&key) {
                slots.insert(key.clone(), row.clone());
            }
            if let Some(record) = journal.get(&journal_key(
                scope,
                &candidate_sha,
                encounter,
                *pair,
                mixed,
            )?) {
                slots.insert(key, slot_from_record(record)?);
            }
        }
    }
    Ok(slots)
}

fn next_work(
    cursor: &mut TrialCursor,
    population: &[Candidate],
    candidate_hashes: &[String],
    seed_banks: &SeedBanks,
    completed_slots: &CompletedSlots,
    scope_sha256: &str,
    mixed_mode: bool,
    journal: &store::Store,
    scores: &mut [Vec<f64>],
    counters: &mut Counters,
    generation_reused: &mut usize,
    generation_rejected: &mut usize,
) -> Result<Option<Work>, String> {
    let max_seeds = seed_banks.values().map(Vec::len).max().unwrap_or(0);
    while let Some((index, seed_index)) = cursor.next(population.len(), max_seeds) {
        let candidate = &population[index];
        let encounter = candidate.intent["encounterId"]
            .as_i64()
            .ok_or("candidate lacks encounter")?;
        let bank = seed_banks
            .get(&encounter)
            .ok_or("candidate lacks seed bank")?;
        let Some(&[math_seed, lib_seed]) = bank.get(seed_index) else {
            continue;
        };
        if let Some(saved) =
            completed_slots.get(&slot_key(&candidate_hashes[index], [math_seed, lib_seed]))
        {
            counters.reused += 1;
            *generation_reused += 1;
            if saved["rejected"].as_bool() == Some(true) {
                counters.rejected += 1;
                *generation_rejected += 1;
            }
            if let Some(earned) = saved["earned"].as_f64() {
                scores[index].push(earned);
            }
            continue;
        }
        let key = journal_key(
            scope_sha256,
            &candidate_hashes[index],
            encounter,
            [math_seed, lib_seed],
            mixed_mode,
        )?;
        if let Some(saved) = journal.get(&key) {
            counters.reused += 1;
            *generation_reused += 1;
            if saved["recordKind"].as_str() == Some("rejection") {
                counters.rejected += 1;
                *generation_rejected += 1;
            }
            if let Some(earned) = score_saved(saved, scope_sha256) {
                scores[index].push(earned);
            }
            continue;
        }
        let mut raw = candidate.intent.as_ref().clone();
        raw["mathSeed"] = json!(math_seed);
        raw["libSeed"] = json!(lib_seed);
        return Ok(Some(Work {
            index,
            key,
            raw,
            intent: candidate.intent.clone(),
            source_candidate_id: candidate.source_candidate_id.clone(),
            parent_candidate_sha256: candidate.parent_candidate_sha256.clone(),
            mutation_operator: candidate.mutation_operator.clone(),
            math_seed,
            lib_seed,
        }));
    }
    Ok(None)
}

fn startup_checkpoint(
    scope_sha256: &str,
    generations: usize,
    next_generation: usize,
    parents: &[Candidate],
    objective_mode: &str,
    objective_state: &ObjectiveState,
) -> Value {
    json!({
        "schema": "ka-rust-checkpoint-v2",
        "scopeSha256": scope_sha256,
        "nextGeneration": next_generation,
        "targetGenerations": generations,
        "parents": parents.iter().map(candidate_json).collect::<Vec<_>>(),
        "objectiveMode": objective_mode,
        "objectiveVersion": objective_version(objective_mode),
        "objectiveState": objective_state
    })
}

fn status_value(
    stage: &str,
    generation: usize,
    config: &Config,
    population: usize,
    generation_done: usize,
    generation_total: usize,
    counters: &Counters,
    started: Instant,
    accepted_outstanding: usize,
    uncommitted_completed: usize,
) -> Value {
    let elapsed = started.elapsed().as_secs_f64();
    let durable = counters.measured.saturating_add(counters.fresh_rejected);
    json!({
        "stage": stage,
        "generation": generation,
        "generations": config.generations,
        "completedTrials": counters.measured,
        "reusedTrials": counters.reused,
        "rejectedTrials": counters.rejected,
        "elapsedSeconds": elapsed,
        "durableTrials": durable,
        "durableSuccessfulTrials": counters.measured,
        "durableRejectedTrials": counters.fresh_rejected,
        "durableTrialsPerSecond": if elapsed > 0.0 { Some(durable as f64 / elapsed) } else { None },
        "durableTrialsPerHour": if elapsed > 0.0 { Some(durable as f64 / elapsed * 3600.0) } else { None },
        "acceptedOutstandingTrials": accepted_outstanding,
        "uncommittedCompletedTrials": uncommitted_completed,
        "remainingUnsubmittedTrialSlots": generation_total.saturating_sub(generation_done.saturating_add(accepted_outstanding).saturating_add(uncommitted_completed)),
        "workCounterSemantics": "acceptedOutstandingTrials includes submitted work until its completion is received, including any completed results queued by workers; uncommittedCompletedTrials awaits durable journal sync. Neither field measures CPU utilization or executing threads. Rates count fresh durable records, exclude reused records, and include startup/preparation/saving in elapsed time.",
        "terminalStatusExemptFromRateLimit": true,
        "generationCandidates": population,
        "generationTrialsDone": generation_done,
        "generationTrialsTotal": generation_total,
        "executors": config.executors,
        "statusWriteLimitHz": 5
    })
}

fn status_inventory(
    mut value: Value,
    dispatch: &[i64],
    retained: &[i64],
    parents: &[Candidate],
    focus: &[i64],
) -> Value {
    let mut counts = BTreeMap::<i64, usize>::new();
    for parent in parents {
        if let Some(encounter) = parent.intent["encounterId"].as_i64() {
            *counts.entry(encounter).or_default() += 1;
        }
    }
    value["dispatchEncounterIds"] = json!(dispatch);
    value["focusEncounters"] = json!(focus);
    value["retainedEncounterIds"] = json!(retained);
    value["perEncounterParentCounts"] = json!(counts);
    value
}

fn check_control(
    config: &Config,
    active_focus: &[i64],
    admitted: &[i64],
) -> Result<Option<&'static str>, String> {
    let control = read_control(config)?;
    if control.stop_requested {
        return Ok(Some("stop-requested"));
    }
    if control.pause_requested {
        return Ok(Some("pause-requested"));
    }
    if let Some(mut requested) = control.focus_encounter_ids {
        if requested.is_empty() {
            requested = admitted.to_vec();
        }
        requested.sort_unstable();
        if requested.iter().any(|id| !admitted.contains(id)) {
            return Err("control focusEncounterIds must select only admitted encounters".into());
        }
        if requested != active_focus {
            return Ok(Some("focus-changed"));
        }
    }
    Ok(None)
}

fn run_prepare(args: &[std::ffi::OsString]) -> Result<(), String> {
    if args.len() != 4 {
        return Err(
            "usage: ka-rust-standalone-optimizer --prepare SCENARIO.json CATALOG.json OUTPUT.json"
                .into(),
        );
    }
    let scenario_path = PathBuf::from(&args[1]);
    let catalog_path = PathBuf::from(&args[2]);
    let output_path = PathBuf::from(&args[3]);
    let scenario = read(&scenario_path)?;
    let catalog = read(&catalog_path)?;
    let prepared = prepare::prepare(&scenario, &catalog)?;
    save(&output_path, &prepared)
}

fn run_prepare_initialized(args: &[std::ffi::OsString]) -> Result<(), String> {
    if args.len() != 5 {
        return Err("usage: ka-rust-standalone-optimizer --prepare-initialized SCENARIO.json CATALOG.json KERNEL.dll OUTPUT.json".into());
    }
    let scenario = read(Path::new(&args[1]))?;
    let catalog = read(Path::new(&args[2]))?;
    let prepared = prepare::prepare(&scenario, &catalog)?;
    let mut native = engine::NativeEngine::load(Path::new(&args[3]))?;
    let initialized = native.initialized_snapshot(&prepared)?;
    save(Path::new(&args[4]), &initialized)
}

fn run_optimizer(config_path: &Path) -> Result<(), String> {
    let started = Instant::now();
    let config_path = fs::canonicalize(config_path).map_err(|e| e.to_string())?;
    let config_value = read(&config_path)?;
    let mut config: Config =
        serde_json::from_value(config_value).map_err(|e| format!("invalid config: {e}"))?;
    if config.schema != "ka-rust-optimizer-config-v1"
        || !(1..=64).contains(&config.executors)
        || !(1..=4096).contains(&config.candidates_per_generation)
        || !(1..=100_000).contains(&config.generations)
        || !(1..=100_000).contains(&config.candidate_limit)
        || config.seed_pairs.is_empty()
        || config.seed_pairs.len() > 4096
    {
        return Err("invalid config schema or bounded settings".into());
    }
    if !matches!(config.execution_mode.as_str(), "diagnostic" | "production") {
        return Err("executionMode must be diagnostic or production".into());
    }
    let Some(objective_version) = objective_version(&config.objective_mode) else {
        return Err("objectiveMode must be earned-only-v1 or mechanism-lanes-v3".into());
    };
    if config
        .result_sync_batch_size
        .is_some_and(|size| !(1..=64).contains(&size))
    {
        return Err("resultSyncBatchSize must be 1..=64".into());
    }
    let effective_batch = config
        .result_sync_batch_size
        .unwrap_or(config.executors.min(8).max(1));
    if config.seed_pairs.iter().flatten().any(|seed| *seed < 0) {
        return Err("seeds must be nonnegative31bit".into());
    }
    let unique_seeds: HashSet<_> = config.seed_pairs.iter().collect();
    if unique_seeds.len() != config.seed_pairs.len() {
        return Err("duplicate seed pairs inflate evidence".into());
    }
    if config
        .encounter_filter
        .is_some_and(|id| !(0..20).contains(&id))
    {
        return Err("encounterFilter must be in 0..19".into());
    }
    if config.encounter_filter.is_some()
        && config
            .encounter_filters
            .as_ref()
            .is_some_and(|rows| !rows.is_empty())
    {
        return Err("encounterFilter and encounterFilters are mutually exclusive".into());
    }
    if let Some(rows) = &config.encounter_filters {
        if rows.iter().any(|id| !(0..20).contains(id))
            || rows.iter().copied().collect::<HashSet<_>>().len() != rows.len()
        {
            return Err("encounterFilters must contain unique IDs in 0..19".into());
        }
    }
    if config.focus_encounter.is_some()
        && config
            .focus_encounters
            .as_ref()
            .is_some_and(|rows| !rows.is_empty())
    {
        return Err("focusEncounter and focusEncounters are mutually exclusive when focusEncounters is nonempty".into());
    }
    if config
        .focus_encounter
        .is_some_and(|id| !(0..20).contains(&id))
    {
        return Err("focusEncounter must be in 0..19".into());
    }
    if let Some(rows) = &config.focus_encounters {
        if rows.iter().any(|id| !(0..20).contains(id))
            || rows.iter().copied().collect::<HashSet<_>>().len() != rows.len()
        {
            return Err("focusEncounters must contain unique IDs in 0..19".into());
        }
    }
    if !config.mechanics_provenance.is_object() || !config.policy_provenance.is_object() {
        return Err("explicit mechanics/policy provenance objects required".into());
    }

    let base = config_path.parent().ok_or("config lacks parent")?;
    resolve_paths(&mut config, base);
    fs::create_dir_all(&config.output)
        .map_err(|e| format!("create output {}: {e}", config.output.display()))?;
    let mut telemetry = StageTelemetry::default();
    telemetry.enabled = config.stage_timing_telemetry;
    telemetry.output = Some(config.output.join("stage-timings.json"));
    telemetry.requested_batch = config.result_sync_batch_size;
    telemetry.effective_batch = effective_batch;
    telemetry.run_timer = if config.stage_timing_telemetry {
        Some(started)
    } else {
        None
    };
    telemetry.wall(
        "configReadValidationAndOutputDirectory",
        if telemetry.enabled {
            Some(started)
        } else {
            None
        },
    );
    let timer = timing_start(telemetry.enabled);
    let catalog = Arc::new(read(&config.catalog)?);
    let catalog_sha256 = hash(catalog.as_ref())?;
    let catalog_file_sha256 =
        engine::digest(&fs::read(&config.catalog).map_err(|e| e.to_string())?);
    telemetry.wall("catalogInputReadParseAndHash", timer);
    let timer = timing_start(telemetry.enabled);
    let (raw_candidates, mut source_ids, candidate_source_sha256) = load_candidates(&config)?;
    validate_synthetic_dps_search(&raw_candidates, config.synthetic_dps_search.as_ref())?;
    if config.fixed_formation {
        for (index, candidate) in raw_candidates.iter().enumerate() {
            fixed_formation::validate(candidate)
                .map_err(|e| format!("candidate {index} violates the fixed formation: {e}"))?;
        }
    }
    if config.synthetic_dps_search.is_some() && config.proposal_request.is_some() {
        return Err("syntheticDpsSearch generates only learned stat proposals; proposalRequest is not allowed".into());
    }
    if config.synthetic_dps_search.is_some() && config.objective_mode != "mechanism-lanes-v3" {
        return Err("syntheticDpsSearch requires objectiveMode=mechanism-lanes-v3".into());
    }
    if let Some(ids) = &config.candidate_source_ids {
        if ids.len() != raw_candidates.len() || ids.iter().any(|v| !v.is_string() && !v.is_null()) {
            return Err(
                "candidateSourceIds must contain one string/null identity per admitted candidate"
                    .into(),
            );
        }
        source_ids = ids.clone();
    }
    telemetry.wall("candidateInputReadAndAdmission", timer);
    if let Some(directory) = &config.diagnostic_dump_directory {
        if config.executors != 1
            || config.generations != 1
            || config.candidates_per_generation != 1
            || config.candidate_limit != 1
            || config.seed_pairs.len() != 1
            || config.parity_attestation.is_some()
            || raw_candidates.len() != 1
            || !diagnostic_intent(&raw_candidates[0])
        {
            return Err("raw native dumps require one diagnostic candidate/trial/generation/worker, without attestation".into());
        }
        fs::create_dir_all(directory)
            .map_err(|e| format!("create diagnostic dump directory: {e}"))?;
    }
    let source_id_value = Value::Array(source_ids.clone());
    let input_candidate_ids_sha256 = hash(&source_ids)?;
    let candidates_sha256 = hash(&raw_candidates)?;
    let encounter_ids = raw_candidates
        .iter()
        .map(|candidate| {
            candidate["encounterId"]
                .as_i64()
                .ok_or("encounterId absent")
        })
        .collect::<Result<BTreeSet<_>, _>>()?
        .into_iter()
        .collect::<Vec<_>>();
    if encounter_ids.is_empty() || encounter_ids.iter().any(|id| !(0..20).contains(id)) {
        return Err("admitted candidate encounters must be in 0..19".into());
    }
    if config
        .encounter_filter
        .is_some_and(|filter| !encounter_ids.contains(&filter))
    {
        return Err("admitted candidates do not contain encounterFilter".into());
    }
    if let Some(filters) = config
        .encounter_filters
        .as_ref()
        .filter(|rows| !rows.is_empty())
    {
        if raw_candidates.iter().any(|candidate| {
            candidate["encounterId"]
                .as_i64()
                .map_or(true, |id| !filters.contains(&id))
        }) {
            return Err("candidate loader returned an encounter outside encounterFilters".into());
        }
    }
    let mixed_mode = encounter_ids.len() > 1;
    let legacy_encounter = encounter_ids[0];
    let startup_control = read_control(&config)?;
    let mut dispatch_encounter_ids = if let Some(encounters) = startup_control
        .focus_encounter_ids
        .as_ref()
        .filter(|rows| !rows.is_empty())
    {
        encounters.clone()
    } else if startup_control
        .focus_encounter_ids
        .as_ref()
        .is_some_and(|rows| rows.is_empty())
    {
        encounter_ids.clone()
    } else if let Some(encounters) = config
        .focus_encounters
        .as_ref()
        .filter(|rows| !rows.is_empty())
    {
        encounters.clone()
    } else if let Some(encounter) = config.focus_encounter {
        vec![encounter]
    } else {
        encounter_ids.clone()
    };
    dispatch_encounter_ids.sort_unstable();
    if dispatch_encounter_ids.is_empty()
        || dispatch_encounter_ids
            .iter()
            .any(|id| !encounter_ids.contains(id))
    {
        return Err("focusEncounters must select one or more admitted encounters".into());
    }
    let active_focus_encounter_ids = dispatch_encounter_ids.clone();
    let mut scope_encounter_filters = config
        .encounter_filters
        .as_ref()
        .filter(|rows| !rows.is_empty())
        .cloned()
        .unwrap_or_else(|| encounter_ids.clone());
    scope_encounter_filters.sort_unstable();
    scope_encounter_filters.dedup();
    let timer = timing_start(telemetry.enabled);
    let kernel_bytes = fs::read(&config.kernel)
        .map_err(|e| format!("read kernel {}: {e}", config.kernel.display()))?;
    let kernel_sha256 = engine::digest(&kernel_bytes);
    let executable_sha256 = engine::digest(
        &fs::read(std::env::current_exe().map_err(|e| e.to_string())?)
            .map_err(|e| e.to_string())?,
    );
    let sources = source_digests();
    let mut scope = json!({
        "schema": "ka-rust-search-scope-v2",
        "kernelSha256": kernel_sha256,
        "executableSha256":executable_sha256,
        "catalogSha256": catalog_sha256,
        "catalogFileSha256":catalog_file_sha256,
        "candidateSourceSha256": candidate_source_sha256,
        "candidatesSha256": candidates_sha256,
        "inputCandidateIds": source_id_value,
        "inputCandidateIdsSha256": input_candidate_ids_sha256,
        "candidateLimit": config.candidate_limit,
        "mechanics": config.mechanics_provenance,
        "policy": config.policy_provenance,
        "seedPairs": config.seed_pairs,
        "searchSeed": config.search_seed,
        "population": config.candidates_per_generation,
        "resultSaving": {"durableSyncBatchSize": effective_batch, "syncPrimitive": "File.sync_all", "stageTimingTelemetry": config.stage_timing_telemetry},
        "sourceSha256": sources,
        "fixedFormation": config.fixed_formation,
        "fixedFormationPolicyHash": fixed_formation::policy_hash(),
        "executionMode":config.execution_mode,
        "objectiveMode":config.objective_mode,
        "objectiveVersion":objective_version,
        "syntheticDpsSearch":config.synthetic_dps_search,
        "commonLearnerPriorSha256":config.common_learner_prior_sha256,
        "proposalRequest":config.proposal_request,"replayTrace":config.replay_trace,
        "initialSearchState":config.initial_search_state
    });
    if mixed_mode {
        scope["encounterIds"] = json!(encounter_ids);
        scope["encounterFilters"] = json!(scope_encounter_filters);
    } else {
        // Preserve the exact single-encounter scope fields used by R14.
        scope["encounterId"] = json!(legacy_encounter);
        scope["encounterFilter"] = json!(config.encounter_filter);
    }
    let scope_sha256 = hash(&scope)?;
    let parity_attestation =
        check_attestation(config.parity_attestation.as_ref(), &scope_sha256, &sources)?;
    let attested = parity_attestation.is_some();
    telemetry.wall("kernelAndScopeIdentity", timer);

    let timer = timing_start(telemetry.enabled);
    let mut journal = store::Store::open(&config.output.join("battles.jsonl"))?;
    if telemetry.enabled {
        journal.enable_telemetry();
    }
    let resume_compatibility_sha256 = hash(&json!({
        "kernelSha256":kernel_sha256,"executableSha256":executable_sha256,
        "catalogSha256":catalog_sha256,"mechanics":config.mechanics_provenance,
        "policy":config.policy_provenance,"executionMode":config.execution_mode,
        "proposalRequest":config.proposal_request,
        "objectiveMode":config.objective_mode,"objectiveVersion":objective_version
        ,"syntheticDpsSearch":config.synthetic_dps_search
        ,"commonLearnerPriorSha256":config.common_learner_prior_sha256
    }))?;
    let checkpoint_path = config.output.join("checkpoint.json");
    let mut prior = read_checkpoint(&checkpoint_path)?;
    let mut objective_state = ObjectiveState::default();
    let external_checkpoint = prior.is_none() && config.initial_checkpoint.is_some();
    if external_checkpoint {
        prior = Some(read(config.initial_checkpoint.as_ref().unwrap())?);
    }
    import_common_learner_prior(
        &config,
        prior.is_some(),
        journal.records().next().is_some(),
        &mut objective_state,
    )?;
    let mut completed_slots = CompletedSlots::new();
    let mut pending_seed_banks = SeedBanks::new();
    let mut parents: Vec<Candidate> = raw_candidates
        .into_iter()
        .zip(source_ids.into_iter())
        .map(|(intent, source_id)| Candidate {
            intent: Arc::new(intent),
            source_candidate_id: source_id,
            parent_candidate_sha256: None,
            mutation_operator: None,
            mutation_target: None,
            mutation_scale: None,
        })
        .collect();
    let mut next_generation = 0usize;
    let mut pending_generation: Option<usize> = None;
    let mut pending_population: Option<Vec<Candidate>> = None;
    let mut search_state = search::SearchPools::new(
        config.search_seed,
        &encounter_ids,
        mixed_mode,
        config.initial_search_state.as_ref(),
    )?;
    if let Some(checkpoint) = prior {
        if checkpoint["schema"].as_str() != Some("ka-rust-checkpoint-v2") {
            return Err("checkpoint schema is incompatible; choose a new output directory".into());
        }
        if checkpoint["objectiveMode"].as_str() != Some(config.objective_mode.as_str())
            || checkpoint["objectiveVersion"].as_u64() != Some(u64::from(objective_version))
        {
            if config.objective_mode == "mechanism-lanes-v3" {
                return Err("checkpoint lacks matching versioned objective state; refusing to fabricate mechanism metrics".into());
            }
        } else if let Some(saved) = checkpoint.get("objectiveState") {
            objective_state = serde_json::from_value(saved.clone())
                .map_err(|e| format!("objective state: {e}"))?;
        }
        if config.objective_mode == "mechanism-lanes-v3"
            && checkpoint.get("objectiveState").is_none()
        {
            return Err("mechanism-lanes-v3 checkpoint has no objectiveState; refusing to fabricate mechanism metrics".into());
        }
        if external_checkpoint {
            if checkpoint["resumeCompatibilitySha256"].as_str()
                != Some(resume_compatibility_sha256.as_str())
            {
                return Err(
                    "external checkpoint native/mechanics/policy compatibility differs".into(),
                );
            }
        } else if checkpoint["scopeSha256"].as_str() != Some(scope_sha256.as_str()) {
            return Err("checkpoint scope differs; select a new output directory".into());
        }
        let checkpoint_target = checkpoint["targetGenerations"]
            .as_u64()
            .ok_or("checkpoint lacks targetGenerations")? as usize;
        if !external_checkpoint && config.generations < checkpoint_target {
            return Err(format!(
                "requested generations {} is below checkpoint target {}; increase it or use a new output directory",
                config.generations, checkpoint_target
            ));
        }
        next_generation = usize::try_from(
            checkpoint["nextGeneration"]
                .as_u64()
                .ok_or("invalid checkpoint generation")?,
        )
        .map_err(|_| "checkpoint generation exceeds usize")?;
        if external_checkpoint {
            next_generation = 0;
        }
        if next_generation > config.generations {
            return Err("checkpoint is beyond the requested generation target".into());
        }
        parents = checkpoint["parents"]
            .as_array()
            .ok_or("invalid checkpoint parents")?
            .iter()
            .map(candidate_from_json)
            .collect::<Result<Vec<_>, _>>()?;
        if let Some(pending) = checkpoint.get("pendingGeneration").and_then(Value::as_u64) {
            let pending =
                usize::try_from(pending).map_err(|_| "pending generation exceeds usize")?;
            let pending = if external_checkpoint { 0 } else { pending };
            if pending != next_generation {
                return Err("checkpoint pendingGeneration must equal nextGeneration".into());
            }
            let population = checkpoint["pendingPopulation"]
                .as_array()
                .ok_or("checkpoint pendingGeneration lacks pendingPopulation")?
                .iter()
                .map(candidate_from_json)
                .collect::<Result<Vec<_>, _>>()?;
            if population.is_empty() {
                return Err("checkpoint pendingPopulation is empty".into());
            }
            if population.iter().any(|candidate| {
                candidate.intent["encounterId"]
                    .as_i64()
                    .map_or(true, |id| !encounter_ids.contains(&id))
            }) {
                return Err(
                    "checkpoint pendingPopulation contains an encounter outside admitted input"
                        .into(),
                );
            }
            if let Some(value) = checkpoint.get("pendingSeedPairsByEncounter") {
                pending_seed_banks = serde_json::from_value(value.clone())
                    .map_err(|e| format!("pending seed banks: {e}"))?;
            } else {
                let bank: Vec<[i32; 2]> = serde_json::from_value(
                    checkpoint
                        .get("pendingSeedPairs")
                        .cloned()
                        .unwrap_or(json!(config.seed_pairs)),
                )
                .map_err(|e| format!("pending seeds: {e}"))?;
                for candidate in &population {
                    pending_seed_banks.insert(
                        candidate.intent["encounterId"].as_i64().unwrap(),
                        bank.clone(),
                    );
                }
            }
            if pending_seed_banks.values().any(|bank| {
                bank.is_empty()
                    || bank.len() > 100_000
                    || bank.iter().collect::<HashSet<_>>().len() != bank.len()
            }) {
                return Err("pending seed bank must be nonempty, unique and bounded".into());
            }
            let source_ledger = if external_checkpoint {
                config
                    .initial_checkpoint
                    .as_ref()
                    .unwrap()
                    .parent()
                    .map(|path| path.join("battles.jsonl"))
            } else {
                None
            };
            completed_slots = load_completed_slots(&checkpoint, source_ledger.as_deref())?;
            pending_generation = Some(pending);
            pending_population = Some(population);
        }
        if let Some(state) = checkpoint.get("searchState") {
            search_state = search::SearchPools::new(
                config.search_seed,
                &encounter_ids,
                mixed_mode,
                Some(state),
            )?;
        }
        if parents.is_empty() {
            return Err("checkpoint parent set is empty".into());
        }
        let mut checkpoint_parent_counts = BTreeMap::<i64, usize>::new();
        for parent in &parents {
            let encounter = parent.intent["encounterId"]
                .as_i64()
                .ok_or("checkpoint parent lacks encounterId")?;
            if !encounter_ids.contains(&encounter) {
                return Err(format!(
                    "checkpoint parent encounter {encounter} is outside admitted encounters"
                ));
            }
            let count = checkpoint_parent_counts.entry(encounter).or_default();
            *count += 1;
            let parent_limit = if next_generation == 0 {
                config.candidates_per_generation
            } else {
                (config.candidates_per_generation / 2).max(1).min(4)
            };
            if *count > parent_limit {
                return Err(format!(
                    "checkpoint has too many parents for encounter {encounter}"
                ));
            }
        }
        seed_lineage(&mut objective_state, &parents)?;
    } else {
        seed_lineage(&mut objective_state, &parents)?;
        save_checkpoint(
            &checkpoint_path,
            &startup_checkpoint(
                &scope_sha256,
                config.generations,
                0,
                &parents,
                &config.objective_mode,
                &objective_state,
            ),
        )?;
    }

    // Validate the checkpoint before replacing its human-readable scope.
    // A rejected cross-revision resume must preserve the prior provenance.
    save(&config.output.join("scope.json"), &scope)?;
    let mut status = StatusWriter::new(config.output.join("status.json"));
    let mut counters = Counters::default();
    status.write(&json!({
        "stage": "starting",
        "acceptedOutstandingTrials": 0,
        "uncommittedCompletedTrials": 0,
        "durableTrials": 0,
        "durableTrialsPerSecond": null,
        "durableTrialsPerHour": null,
        "terminalStatusExemptFromRateLimit": true,
        "generation": next_generation,
        "generations": config.generations,
        "completedTrials": 0,
        "reusedTrials": 0,
        "rejectedTrials": 0,
        "elapsedSeconds": started.elapsed().as_secs_f64(),
        "executors": config.executors,
        "statusWriteLimitHz": 5
    }))?;
    telemetry.wall("journalRecoveryCheckpointAndInitialStatus", timer);
    let mut pool: Option<Pool> = None;

    for generation in next_generation..config.generations {
        let mut seed_banks = pending_seed_banks.clone();
        for encounter in &encounter_ids {
            seed_banks
                .entry(*encounter)
                .or_insert_with(|| config.seed_pairs.clone());
        }
        let mut parents_by_encounter = BTreeMap::<i64, Vec<Candidate>>::new();
        for parent in &parents {
            let encounter = parent.intent["encounterId"]
                .as_i64()
                .ok_or("parent lacks encounterId")?;
            parents_by_encounter
                .entry(encounter)
                .or_default()
                .push(parent.clone());
        }
        let mut populations_by_encounter = BTreeMap::<i64, Vec<Candidate>>::new();
        let mut resumed_encounters = BTreeSet::new();
        if pending_generation == Some(generation) {
            for candidate in pending_population.take().unwrap_or_default() {
                let encounter = candidate.intent["encounterId"]
                    .as_i64()
                    .ok_or("pending candidate lacks encounterId")?;
                resumed_encounters.insert(encounter);
                populations_by_encounter
                    .entry(encounter)
                    .or_default()
                    .push(candidate);
            }
            pending_generation = None;
        }
        for encounter in &dispatch_encounter_ids {
            if !populations_by_encounter.contains_key(encounter) {
                let group = parents_by_encounter.get(encounter).ok_or_else(|| {
                    format!("no retained parents for dispatch encounter {encounter}")
                })?;
                if group.len() > config.candidates_per_generation {
                    return Err(format!("parent population for encounter {encounter} exceeds candidatesPerGeneration"));
                }
                populations_by_encounter.insert(*encounter, group.clone());
            }
        }
        let mut proposal_seen: HashSet<String> = parents
            .iter()
            .map(|p| serde_json::to_string(p.intent.as_ref()).map_err(|e| e.to_string()))
            .collect::<Result<_, _>>()?;
        let mut mutation_warnings = Vec::new();
        if let Some(request) = &config.proposal_request {
            for encounter in &dispatch_encounter_ids {
                if resumed_encounters.contains(encounter) {
                    continue;
                }
                let group = parents_by_encounter
                    .get(encounter)
                    .ok_or("missing encounter parent group")?;
                for parent in group {
                    let proposals =
                        if matches!(request["kind"].as_str(), Some("stat-axis" | "probe")) {
                            let document =
                                tuning::propose(parent.intent.as_ref(), catalog.as_ref(), request)?;
                            document["candidates"]
                                .as_array()
                                .ok_or("stat proposal response omitted candidates")?
                                .iter()
                                .map(|row| {
                                    let intent = row
                                        .get("intent")
                                        .cloned()
                                        .ok_or("stat proposal omitted intent")?;
                                    let operator = row["mutationOperator"]
                                        .as_str()
                                        .ok_or("stat proposal omitted mutationOperator")?
                                        .to_owned();
                                    Ok((intent, operator))
                                })
                                .collect::<Result<Vec<_>, String>>()?
                        } else {
                            search::Search::proposed_descendants(parent.intent.as_ref(), request)?
                        };
                    for (intent, operator) in proposals {
                        if intent["encounterId"].as_i64() != Some(*encounter) {
                            return Err("proposal changed the parent encounterId".into());
                        }
                        let identity = serde_json::to_string(&intent).map_err(|e| e.to_string())?;
                        if !proposal_seen.insert(identity) {
                            continue;
                        }
                        let population = populations_by_encounter
                            .get_mut(encounter)
                            .ok_or("missing encounter population")?;
                        if population.len() >= config.candidates_per_generation {
                            return Err(format!("explicit proposals exceed candidatesPerGeneration for encounter {encounter}; increase the per-encounter population instead of silently dropping requested values"));
                        }
                        population.push(Candidate {
                            intent: Arc::new(intent),
                            source_candidate_id: parent.source_candidate_id.clone(),
                            parent_candidate_sha256: Some(hash(parent.intent.as_ref())?),
                            mutation_operator: Some(operator),
                            mutation_target: None,
                            mutation_scale: None,
                        });
                    }
                }
            }
        } else {
            for encounter in &dispatch_encounter_ids {
                if resumed_encounters.contains(encounter) {
                    continue;
                }
                let group = parents_by_encounter
                    .get(encounter)
                    .ok_or("missing encounter parent group")?;
                if group.is_empty() {
                    return Err(format!("no retained parents for encounter {encounter}"));
                }
                let initial_count = populations_by_encounter
                    .get(encounter)
                    .ok_or("missing encounter population")?
                    .len();
                let mut errors = Vec::new();
                let mut attempt = 0u64;
                let proposal_number = *objective_state
                    .proposal_numbers
                    .entry(*encounter)
                    .or_insert(generation as u64);
                while populations_by_encounter
                    .get(encounter)
                    .ok_or("population missing")?
                    .len()
                    < config.candidates_per_generation
                    && attempt
                        < (config.candidates_per_generation as u64)
                            .saturating_mul(8)
                            .max(32)
                {
                    attempt += 1;
                    let parent = if config.objective_mode == "mechanism-lanes-v3" {
                        let current = populations_by_encounter
                            .get(encounter)
                            .ok_or("population missing")?;
                        let sources =
                            objective_parent_sources(&objective_state, *encounter, current)?;
                        let names = [
                            "earned",
                            "potential",
                            "setup",
                            "efficiency",
                            "region",
                            "exploration",
                            "mean",
                        ];
                        let source =
                            names[((proposal_number + attempt) % names.len() as u64) as usize];
                        let source_pool = sources
                            .get(source)
                            .filter(|v| !v.is_empty())
                            .or_else(|| sources.get("exploration"))
                            .ok_or("exploration source missing")?;
                        let chosen = weighted_parent(
                            &mut objective_state,
                            *encounter,
                            source_pool,
                            config.search_seed ^ generation as u64 ^ attempt,
                        )?;
                        current
                            .iter()
                            .chain(group.iter())
                            .find(|candidate| {
                                hash(candidate.intent.as_ref()).ok().as_deref()
                                    == Some(chosen.as_str())
                            })
                            .cloned()
                            .or_else(|| {
                                objective_state
                                    .records
                                    .iter()
                                    .find(|(key, entry)| {
                                        key.starts_with(&format!("{chosen}:"))
                                            && entry.encounter_id == *encounter
                                    })
                                    .map(|(_, entry)| Candidate {
                                        intent: Arc::new(entry.intent.clone()),
                                        source_candidate_id: Value::Null,
                                        parent_candidate_sha256: None,
                                        mutation_operator: None,
                                        mutation_target: None,
                                        mutation_scale: None,
                                    })
                            })
                            .ok_or_else(|| {
                                format!("selected historical parent {chosen} has no intent")
                            })?
                    } else {
                        let preferred = (populations_by_encounter
                            .get(encounter)
                            .ok_or("population missing")?
                            .len()
                            - initial_count)
                            % group.len();
                        group[preferred].clone()
                    };
                    let parent_sha256 = hash(parent.intent.as_ref())?;
                    let generated = if let Some(controlled) = config.synthetic_dps_search.as_ref() {
                        let (intent, target, scale) = controlled_stat_child(
                            parent.intent.as_ref(),
                            controlled,
                            &mut objective_state,
                            *encounter,
                            config.search_seed ^ generation as u64 ^ attempt,
                        )?;
                        Ok((intent, "set-stat".to_owned(), Some(target), Some(scale)))
                    } else if config.fixed_formation {
                        search_state
                            .mutate_fixed_unique(
                                *encounter,
                                parent.intent.as_ref(),
                                &mut proposal_seen,
                                32,
                            )
                            .map(|(intent, operator)| (intent, operator, None, None))
                    } else {
                        search_state
                            .mutate_unique_with_catalog(
                                *encounter,
                                parent.intent.as_ref(),
                                catalog.as_ref(),
                                &mut proposal_seen,
                                32,
                            )
                            .map(|(intent, operator)| (intent, operator, None, None))
                    };
                    match generated {
                        Ok((intent, operator, target, scale)) => {
                            if config.fixed_formation {
                                if let Err(error) = fixed_formation::validate(&intent) {
                                    errors.push(format!(
                                        "fixed-formation child failed revalidation: {error}"
                                    ));
                                    continue;
                                }
                            }
                            if intent["encounterId"].as_i64() != Some(*encounter) {
                                return Err("mutation changed the parent encounterId".into());
                            }
                            let identity =
                                serde_json::to_string(&intent).map_err(|e| e.to_string())?;
                            if !proposal_seen.insert(identity) {
                                errors.push("duplicate child".into());
                                continue;
                            }
                            let root = objective_state
                                .lineage_roots
                                .get(&parent_sha256)
                                .cloned()
                                .unwrap_or_else(|| parent_sha256.clone());
                            let child = Candidate {
                                intent: Arc::new(intent),
                                source_candidate_id: parent.source_candidate_id.clone(),
                                parent_candidate_sha256: Some(parent_sha256.clone()),
                                mutation_operator: Some(operator.clone()),
                                mutation_target: target,
                                mutation_scale: scale,
                            };
                            let child_sha = hash(child.intent.as_ref())?;
                            objective_state.lineage_roots.insert(child_sha, root);
                            if config.synthetic_dps_search.is_none() {
                                objective_state
                                    .operator_stats
                                    .entry(*encounter)
                                    .or_default()
                                    .entry(operator)
                                    .or_default()
                                    .attempts += 1;
                            }
                            populations_by_encounter
                                .get_mut(encounter)
                                .ok_or("population missing")?
                                .push(child);
                        }
                        Err(error) => errors.push(error),
                    }
                }
                objective_state
                    .proposal_numbers
                    .insert(*encounter, proposal_number.saturating_add(1));
                if populations_by_encounter
                    .get(encounter)
                    .ok_or("population missing")?
                    .len()
                    < config.candidates_per_generation
                {
                    mutation_warnings.push(format!(
                        "encounter {encounter}: active learner exhausted bounded proposals: {}",
                        errors.join("; ")
                    ));
                }
            }
        }
        let mut population = Vec::new();
        let max_group = populations_by_encounter
            .values()
            .map(Vec::len)
            .max()
            .unwrap_or(0);
        for row in 0..max_group {
            for encounter in &dispatch_encounter_ids {
                if let Some(candidate) = populations_by_encounter
                    .get(encounter)
                    .and_then(|group| group.get(row))
                {
                    population.push(candidate.clone());
                }
            }
        }
        let mut seen = HashSet::new();
        let mut unique_population = Vec::with_capacity(population.len());
        for candidate in population {
            if seen.insert(hash(candidate.intent.as_ref())?) {
                unique_population.push(candidate);
            }
        }
        let population = unique_population;
        if population.is_empty() {
            return Err("candidate generation produced an empty population".into());
        }
        let mut checkpoint_population = populations_by_encounter
            .values()
            .flat_map(|group| group.iter().cloned())
            .collect::<Vec<_>>();
        checkpoint_population
            .sort_by_key(|candidate| candidate.intent["encounterId"].as_i64().unwrap_or(i64::MAX));
        save_checkpoint(
            &checkpoint_path,
            &json!({
                "schema": "ka-rust-checkpoint-v2",
                "scopeSha256": scope_sha256,
                "nextGeneration": generation,
                "targetGenerations": config.generations,
                "pendingGeneration": generation,
                "pendingPopulation": checkpoint_population.iter().map(candidate_json).collect::<Vec<_>>(),
                "pendingSeedPairs": config.seed_pairs,
                "pendingSeedPairsByEncounter": seed_banks,
                "completedPendingSlots": completed_slots.values().collect::<Vec<_>>(),
                "resumeCompatibilitySha256": resume_compatibility_sha256,
                "searchState": search_state.snapshot(),
                "objectiveMode":config.objective_mode,"objectiveVersion":objective_version,"objectiveState":objective_state,
                "parents": parents.iter().map(candidate_json).collect::<Vec<_>>()
            }),
        )?;
        let candidate_hashes = population
            .iter()
            .map(|candidate| hash(candidate.intent.as_ref()))
            .collect::<Result<Vec<_>, _>>()?;
        let generation_total = population.iter().try_fold(0usize, |count, candidate| {
            count
                .checked_add(
                    seed_banks
                        .get(&candidate.intent["encounterId"].as_i64().unwrap())
                        .ok_or("candidate seed bank missing")?
                        .len(),
                )
                .ok_or("generation trial count overflow")
        })?;
        let mut generation_done = 0usize;
        let mut generation_reused = 0usize;
        let mut generation_rejected = 0usize;
        let mut scores = vec![Vec::<f64>::new(); population.len()];
        let mut cursor = TrialCursor::new(mixed_mode && dispatch_encounter_ids.len() > 1);
        let mut wave = Vec::with_capacity(config.executors.min(8));
        let wave_limit = effective_batch;
        let max_intent_bytes = population
            .iter()
            .map(|candidate| bytes(candidate.intent.as_ref()).map(|value| value.len() + 64))
            .collect::<Result<Vec<_>, _>>()?
            .into_iter()
            .max()
            .unwrap_or(0);
        let work_slots = config
            .executors
            .checked_add(wave_limit)
            .ok_or("serialized work slot count overflow")?;
        let serialized_work_bytes = max_intent_bytes
            .checked_mul(work_slots)
            .ok_or("serialized work size overflow")?;
        if serialized_work_bytes > MAX_SERIALIZED_WORK_BYTES {
            return Err(format!(
                "serializedWorkBytes bound exceeded: estimated {serialized_work_bytes}, limit {MAX_SERIALIZED_WORK_BYTES}"
            ));
        }
        let mut inflight = 0usize;
        let mut stop_reason = check_control(&config, &active_focus_encounter_ids, &encounter_ids)?;

        // The trial cursor walks candidates and seed pairs lazily. Only the resident
        // workers and one small result wave own cloned scenario data at a time.
        for worker in 0..config.executors {
            if stop_reason.is_none() {
                stop_reason = check_control(&config, &active_focus_encounter_ids, &encounter_ids)?;
            }
            if stop_reason.is_some() {
                break;
            }
            let work = next_work(
                &mut cursor,
                &population,
                &candidate_hashes,
                &seed_banks,
                &completed_slots,
                &scope_sha256,
                mixed_mode,
                &journal,
                &mut scores,
                &mut counters,
                &mut generation_reused,
                &mut generation_rejected,
            )?;
            let Some(work) = work else { break };
            if pool.is_none() {
                let timer = timing_start(telemetry.enabled);
                pool = Some(Pool::new(
                    config.executors,
                    &config.kernel,
                    catalog.clone(),
                    config.diagnostic_dump_directory.clone(),
                    config.stage_timing_telemetry,
                    config.replay_trace,
                )?);
                telemetry.wall("residentWorkersStartupWall", timer);
            }
            // Selection/reuse scanning and pool startup can outlast the control cache.
            // An unsubmitted work slot remains absent from the journal/checkpoint slots
            // and will be selected again on resume, even though this cursor advanced.
            stop_reason = check_control(&config, &active_focus_encounter_ids, &encounter_ids)?;
            if stop_reason.is_some() {
                break;
            }
            pool.as_ref().unwrap().senders[worker]
                .send(Some(work))
                .map_err(|_| format!("native worker {worker} closed its input channel"))?;
            inflight += 1;
        }

        // Publish submitted work before waiting for a potentially long battle.
        status.maybe_write(&status_inventory(
            status_value(
                "generation",
                generation,
                &config,
                population.len(),
                generation_done + generation_reused,
                generation_total,
                &counters,
                started,
                inflight,
                wave.len(),
            ),
            &dispatch_encounter_ids,
            &encounter_ids,
            &parents,
            &active_focus_encounter_ids,
        ))?;
        while inflight > 0 {
            let mut scheduling_error: Option<String> = None;
            let completion = match pool
                .as_ref()
                .ok_or("in-flight work exists without a native pool")?
                .results
                .recv_timeout(STATUS_INTERVAL)
            {
                Ok(completion) => Some(completion),
                Err(mpsc::RecvTimeoutError::Timeout) => {
                    if stop_reason.is_none() {
                        stop_reason =
                            check_control(&config, &active_focus_encounter_ids, &encounter_ids)?;
                    }
                    status.maybe_write(&status_inventory(
                        status_value(
                            "generation",
                            generation,
                            &config,
                            population.len(),
                            generation_done + generation_reused,
                            generation_total,
                            &counters,
                            started,
                            inflight,
                            wave.len(),
                        ),
                        &dispatch_encounter_ids,
                        &encounter_ids,
                        &parents,
                        &active_focus_encounter_ids,
                    ))?;
                    continue;
                }
                Err(mpsc::RecvTimeoutError::Disconnected) => {
                    scheduling_error = Some("native worker result channel closed".into());
                    inflight = 0;
                    None
                }
            };
            let worker = completion.as_ref().map(|completion| completion.worker);
            if let Some(completion) = completion {
                inflight -= 1;
                telemetry.observe(&completion);
                wave.push(completion);
            }

            if stop_reason.is_none() {
                stop_reason = check_control(&config, &active_focus_encounter_ids, &encounter_ids)?;
            }
            if effective_batch != 1 && scheduling_error.is_none() && stop_reason.is_none() {
                let refill = (|| -> Result<(), String> {
                    let next = next_work(
                        &mut cursor,
                        &population,
                        &candidate_hashes,
                        &seed_banks,
                        &completed_slots,
                        &scope_sha256,
                        mixed_mode,
                        &journal,
                        &mut scores,
                        &mut counters,
                        &mut generation_reused,
                        &mut generation_rejected,
                    )?;
                    if let Some(work) = next {
                        stop_reason =
                            check_control(&config, &active_focus_encounter_ids, &encounter_ids)?;
                        if stop_reason.is_some() {
                            return Ok(());
                        }
                        let worker = worker.ok_or("completion has no worker")?;
                        pool.as_ref().ok_or("native pool disappeared")?.senders[worker]
                            .send(Some(work))
                            .map_err(|_| {
                                format!("native worker {worker} closed its input channel")
                            })?;
                        inflight += 1;
                    }
                    Ok(())
                })();
                if let Err(error) = refill {
                    scheduling_error = Some(error);
                }
            }

            if wave.len() >= wave_limit || inflight == 0 || scheduling_error.is_some() {
                let assembly_timer = timing_start(telemetry.enabled);
                let mut ledger_batch = Vec::with_capacity(wave.len());
                let mut post_commit = Vec::with_capacity(wave.len());
                for completion in wave.drain(..) {
                    match completion.result {
                        Ok((prepared, report, kernel)) => {
                            if kernel["kernelSha256"].as_str() != Some(kernel_sha256.as_str()) {
                                return Err("resident kernel differs from scoped kernel".into());
                            }
                            let earned = resolved_earned(&report);
                            let resolved = earned.is_some();
                            let finish_diagnostic =
                                diagnostic_intent(completion.work.intent.as_ref());
                            let measurement_eligible =
                                production_purpose(&config) && earned.is_some();
                            let evidence = measurement_eligible;
                            let diagnostic = !evidence;
                            let record = json!({
                                "schema": "ka-rust-strategy-evidence-v2",
                                "recordKind": "battle",
                                "scopeSha256": scope_sha256,
                                "scope": scope,
                                "encounterId": completion.work.intent["encounterId"],
                                "candidateSha256": candidate_hashes[completion.work.index],
                                "intent": completion.work.intent.as_ref(),
                                "trialIntent": completion.work.raw,
                                "inputSourceCandidateId": completion.work.source_candidate_id,
                                "parentCandidateSha256": completion.work.parent_candidate_sha256,
                                "mutationOperator":completion.work.mutation_operator,
                                "seedPairs": seed_banks[&completion.work.intent["encounterId"].as_i64().unwrap()],
                                "mathSeed": completion.work.math_seed,
                                "libSeed": completion.work.lib_seed,
                                "preparedSha256": hash(&prepared)?,
                                "prepared": prepared,
                                "kernel": kernel,
                                "report": report,
                                "outcomeClassification": if resolved { "resolved" } else { "censored_or_unknown" },
                                "diagnostic": diagnostic,
                                "finishDiagnostic":finish_diagnostic,
                                "measurementEligible":measurement_eligible,
                                "executionMode":config.execution_mode,
                                "verificationOnly": !evidence,
                                "strategyEvidence": evidence,
                                "importDisabled": !evidence,
                                "parityAttestation": parity_attestation,
                                "generation": generation
                            });
                            ledger_batch.push((completion.work.key, record));
                            post_commit.push((completion.work.index, earned, false));
                        }
                        Err(error) => {
                            let record = json!({
                                "schema": "ka-rust-trial-rejection-v1",
                                "recordKind": "rejection",
                                "scopeSha256": scope_sha256,
                                "scope": scope,
                                "encounterId": completion.work.intent["encounterId"],
                                "candidateSha256": candidate_hashes[completion.work.index],
                                "intent": completion.work.intent.as_ref(),
                                "trialIntent": completion.work.raw,
                                "inputSourceCandidateId": completion.work.source_candidate_id,
                                "parentCandidateSha256": completion.work.parent_candidate_sha256,
                                "mutationOperator":completion.work.mutation_operator,
                                "seedPairs": seed_banks[&completion.work.intent["encounterId"].as_i64().unwrap()],
                                "mathSeed": completion.work.math_seed,
                                "libSeed": completion.work.lib_seed,
                                "error": error,
                                "diagnostic": diagnostic_intent(completion.work.intent.as_ref()),
                                "strategyEvidence": false,
                                "importDisabled": true,
                                "generation": generation
                            });
                            ledger_batch.push((completion.work.key, record));
                            post_commit.push((completion.work.index, None, true));
                        }
                    }
                }
                telemetry.wall("resultRecordAssembly", assembly_timer);
                let appended = journal.append_batch(&ledger_batch);
                if telemetry.enabled {
                    telemetry.journal = journal.telemetry_snapshot();
                }
                appended?;
                for (index, earned, rejected) in post_commit {
                    if rejected {
                        counters.rejected += 1;
                        counters.fresh_rejected += 1;
                        generation_rejected += 1;
                    } else {
                        counters.measured += 1;
                        if let Some(earned) = earned {
                            scores[index].push(earned);
                        }
                    }
                    generation_done += 1;
                }
                status.maybe_write(&status_inventory(
                    status_value(
                        "generation",
                        generation,
                        &config,
                        population.len(),
                        generation_done + generation_reused,
                        generation_total,
                        &counters,
                        started,
                        inflight,
                        wave.len(),
                    ),
                    &dispatch_encounter_ids,
                    &encounter_ids,
                    &parents,
                    &active_focus_encounter_ids,
                ))?;
            }
            if let Some(error) = scheduling_error {
                return Err(error);
            }
            if effective_batch == 1 {
                if stop_reason.is_none() {
                    stop_reason =
                        check_control(&config, &active_focus_encounter_ids, &encounter_ids)?;
                }
                let next = if stop_reason.is_none() {
                    next_work(
                        &mut cursor,
                        &population,
                        &candidate_hashes,
                        &seed_banks,
                        &completed_slots,
                        &scope_sha256,
                        mixed_mode,
                        &journal,
                        &mut scores,
                        &mut counters,
                        &mut generation_reused,
                        &mut generation_rejected,
                    )?
                } else {
                    None
                };
                if let Some(work) = next {
                    stop_reason =
                        check_control(&config, &active_focus_encounter_ids, &encounter_ids)?;
                    if stop_reason.is_none() {
                        let worker = worker.ok_or("completion has no worker")?;
                        pool.as_ref().ok_or("native pool disappeared")?.senders[worker]
                            .send(Some(work))
                            .map_err(|_| {
                                format!("native worker {worker} closed its input channel")
                            })?;
                        inflight += 1;
                    }
                }
            }
        }

        let all_completed_slots = collect_pending_slots(
            &checkpoint_population,
            &seed_banks,
            &completed_slots,
            &journal,
            &scope_sha256,
            mixed_mode,
        )?;
        let active_completed = collect_pending_slots(
            &population,
            &seed_banks,
            &completed_slots,
            &journal,
            &scope_sha256,
            mixed_mode,
        )?
        .len();
        let remaining_slots = generation_total.saturating_sub(active_completed);
        if let Some(reason) = stop_reason.filter(|_| remaining_slots > 0) {
            save_checkpoint(
                &checkpoint_path,
                &json!({
                    "schema": "ka-rust-checkpoint-v2",
                    "scopeSha256": scope_sha256,
                    "nextGeneration": generation,
                    "targetGenerations": config.generations,
                    "pendingGeneration": generation,
                    "pendingPopulation": checkpoint_population.iter().map(candidate_json).collect::<Vec<_>>(),
                    "pendingSeedPairs": config.seed_pairs,
                    "pendingSeedPairsByEncounter": seed_banks,
                    "completedPendingSlots": all_completed_slots.values().collect::<Vec<_>>(),
                    "resumeCompatibilitySha256": resume_compatibility_sha256,
                    "searchState": search_state.snapshot(),
                    "objectiveMode":config.objective_mode,"objectiveVersion":objective_version,"objectiveState":objective_state,
                    "parents": parents.iter().map(candidate_json).collect::<Vec<_>>()
                }),
            )?;
            telemetry.complete = true;
            status.write_final(&status_inventory(
                json!({
                    "stage": if reason == "stop-requested" { "stopped" } else { "paused" },
                    "reason": reason,
                    "generation": generation,
                    "generations": config.generations,
                    "acceptedOutstandingTrials": 0,
                    "uncommittedCompletedTrials": 0,
                    "remainingUnsubmittedTrialSlots": remaining_slots,
                    "durableTrials": counters.measured.saturating_add(counters.fresh_rejected),
                    "durableSuccessfulTrials": counters.measured,
                    "durableRejectedTrials": counters.fresh_rejected,
                    "completedTrials": counters.measured,
                    "reusedTrials": counters.reused,
                    "rejectedTrials": counters.rejected,
                    "elapsedSeconds": started.elapsed().as_secs_f64(),
                    "executors": config.executors,
                    "terminalStatusExemptFromRateLimit": true
                }),
                &dispatch_encounter_ids,
                &encounter_ids,
                &parents,
                &active_focus_encounter_ids,
            ))?;
            telemetry.flush()?;
            println!(
                "{}",
                json!({"ok":true,"stage":if reason == "stop-requested" { "stopped" } else { "paused" },"reason":reason,"remainingPlannedTrialSlots":remaining_slots,"output":config.output})
            );
            return Ok(());
        }

        let timer = timing_start(telemetry.enabled);
        if config.objective_mode == "mechanism-lanes-v3" {
            let mut candidates_in_birth_order = Vec::<(String, Candidate)>::new();
            let mut candidate_hashes_seen = BTreeSet::<String>::new();
            for candidate in checkpoint_population.iter().chain(parents.iter()) {
                let candidate_sha = hash(candidate.intent.as_ref())?;
                if candidate_hashes_seen.insert(candidate_sha.clone()) {
                    candidates_in_birth_order.push((candidate_sha, candidate.clone()));
                }
            }
            for (candidate_sha, candidate) in candidates_in_birth_order {
                let encounter = candidate.intent["encounterId"]
                    .as_i64()
                    .ok_or("candidate lacks encounterId")?;
                let defeat = candidate.intent["defeatCount"].as_i64().unwrap_or(0);
                let lane_key = format!("{candidate_sha}:{encounter}:{defeat}");
                if !objective_state.records.contains_key(&lane_key) {
                    objective_state.birth_order.push(lane_key.clone());
                }
                objective_state
                    .records
                    .entry(lane_key.clone())
                    .or_insert_with(|| ObjectiveEntry {
                        intent: candidate.intent.as_ref().clone(),
                        encounter_id: encounter,
                        defeat_count: defeat,
                        aggregate: objective::Aggregate::default(),
                    });
                let matching = journal
                    .records()
                    .filter(|(_, record)| {
                        record["scopeSha256"].as_str() == Some(scope_sha256.as_str())
                            && record["candidateSha256"].as_str() == Some(candidate_sha.as_str())
                            && record["encounterId"].as_i64() == Some(encounter)
                    })
                    .map(|(_, record)| record.clone())
                    .collect::<Vec<_>>();
                for record in matching {
                    let pair = [
                        record["mathSeed"]
                            .as_i64()
                            .ok_or("journal lacks math seed")? as i32,
                        record["libSeed"]
                            .as_i64()
                            .ok_or("journal lacks library seed")? as i32,
                    ];
                    let slot = slot_key(&lane_key, pair);
                    if objective_state.seen_slots.insert(slot) {
                        objective_state
                            .records
                            .get_mut(&lane_key)
                            .unwrap()
                            .aggregate
                            .observe_record(&record);
                    }
                }
            }
        }
        let mut ranked = Vec::new();
        let mut ranked_objective_improvements = BTreeMap::<String, Vec<String>>::new();
        let parent_means: BTreeMap<(i64, String), f64> = population
            .iter()
            .zip(scores.iter())
            .filter(|(candidate, values)| {
                values.len() == seed_banks[&candidate.intent["encounterId"].as_i64().unwrap()].len()
            })
            .map(|(candidate, values)| {
                let encounter = candidate.intent["encounterId"]
                    .as_i64()
                    .ok_or("candidate lacks encounterId")?;
                Ok((
                    (encounter, hash(candidate.intent.as_ref())?),
                    values.iter().sum::<f64>() / values.len() as f64,
                ))
            })
            .collect::<Result<_, String>>()?;
        for (index, values) in scores.iter().enumerate() {
            if values.len()
                != seed_banks[&population[index].intent["encounterId"].as_i64().unwrap()].len()
            {
                continue;
            }
            let candidate_encounter = population[index].intent["encounterId"]
                .as_i64()
                .ok_or("candidate lacks encounterId")?;
            let mean = values.iter().sum::<f64>() / values.len() as f64;
            search_state.observe(
                candidate_encounter,
                population[index].intent.as_ref().clone(),
                mean,
            )?;
            if let (Some(operator), Some(parent)) = (
                &population[index].mutation_operator,
                &population[index].parent_candidate_sha256,
            ) {
                if config.objective_mode == "mechanism-lanes-v3" {
                    let child_intent = population[index].intent.as_ref();
                    let child_key = format!(
                        "{}:{}:{}",
                        candidate_hashes[index],
                        candidate_encounter,
                        child_intent["defeatCount"].as_i64().unwrap_or(0)
                    );
                    let parent_intent = parents
                        .iter()
                        .find(|candidate| {
                            hash(candidate.intent.as_ref()).ok().as_deref() == Some(parent.as_str())
                        })
                        .map(|candidate| candidate.intent.as_ref())
                        .or_else(|| {
                            population
                                .iter()
                                .find(|candidate| {
                                    hash(candidate.intent.as_ref()).ok().as_deref()
                                        == Some(parent.as_str())
                                })
                                .map(|candidate| candidate.intent.as_ref())
                        });
                    let parent_intent = parent_intent.or_else(|| {
                        objective_state
                            .records
                            .iter()
                            .find(|(key, entry)| {
                                key.starts_with(&format!("{parent}:"))
                                    && entry.encounter_id == candidate_encounter
                            })
                            .map(|(_, entry)| &entry.intent)
                    });
                    let parent_defeat = parent_intent
                        .and_then(|intent| intent["defeatCount"].as_i64())
                        .unwrap_or(0);
                    let parent_key = format!("{parent}:{candidate_encounter}:{parent_defeat}");
                    let child_record = objective_state.lane_record(&child_key);
                    let parent_record = objective_state.lane_record(&parent_key);
                    let improvements = objective::improved_lanes(&parent_record, &child_record);
                    ranked_objective_improvements
                        .insert(candidate_hashes[index].clone(), improvements.clone());
                    let stat_target = population[index].mutation_target.as_deref();
                    let stat_value = stat_target.and_then(|parameter| {
                        population[index].intent["ownUnits"]
                            .as_array()
                            .and_then(|units| {
                                units
                                    .iter()
                                    .find(|u| u["name"].as_str() == Some("Synthetic DPS"))
                            })
                            .and_then(|u| u["parameters"].get(parameter))
                            .and_then(|p| p["rawValue"].as_i64())
                    });
                    apply_lane_feedback(
                        &mut objective_state,
                        candidate_encounter,
                        &candidate_hashes[index],
                        operator,
                        &improvements,
                        stat_target,
                        stat_value,
                    );
                } else if let Some(parent_mean) =
                    parent_means.get(&(candidate_encounter, parent.clone()))
                {
                    search_state.observe_operator(
                        candidate_encounter,
                        operator,
                        mean > *parent_mean,
                    )?;
                }
            }
            let highest = values.iter().copied().fold(f64::NEG_INFINITY, f64::max);
            let sample_sd = if values.len() > 1 {
                Some(
                    (values
                        .iter()
                        .map(|value| (value - mean).powi(2))
                        .sum::<f64>()
                        / (values.len() - 1) as f64)
                        .sqrt(),
                )
            } else {
                None
            };
            let finish_diagnostic = diagnostic_intent(population[index].intent.as_ref());
            let candidate_sha = &candidate_hashes[index];
            let verified = production_purpose(&config);
            let diagnostic = !verified;
            ranked.push(json!({
                "candidateSha256": candidate_hashes[index],
                "intent": population[index].intent.as_ref(),
                "inputSourceCandidateId": population[index].source_candidate_id,
                "parentCandidateSha256": population[index].parent_candidate_sha256,
                "mutationOperator":population[index].mutation_operator,
                "meanEarned": mean,
                "highestEarned": highest,
                "samples": values.len(),
                "sampleSD": sample_sd,
                "encounterId": candidate_encounter,
                "diagnostic": diagnostic,
                "finishDiagnostic":finish_diagnostic,
                "executionMode":config.execution_mode,
                "objectiveRecord":if config.objective_mode == "mechanism-lanes-v3" { Some(objective_state.lane_record(&format!("{}:{}:{}",candidate_sha,candidate_encounter,population[index].intent["defeatCount"].as_i64().unwrap_or(0)))) } else { None },
                "improvedLanes":ranked_objective_improvements.get(candidate_sha),
                "strategyEvidence": verified,
                "verified": verified,
                "verificationOnly": !verified,
                "importDisabled": !verified
            }));
        }
        ranked.sort_by(|a, b| {
            a["encounterId"]
                .as_i64()
                .cmp(&b["encounterId"].as_i64())
                .then_with(|| {
                    b["meanEarned"]
                        .as_f64()
                        .partial_cmp(&a["meanEarned"].as_f64())
                        .unwrap_or(std::cmp::Ordering::Equal)
                })
        });
        if ranked.is_empty() {
            let has_lane_eligible = if config.objective_mode == "mechanism-lanes-v3" {
                dispatch_encounter_ids.iter().any(|encounter| {
                    objective_parent_sources(&objective_state, *encounter, &checkpoint_population)
                        .map(|sources| {
                            ["earned", "potential", "setup", "efficiency"]
                                .iter()
                                .any(|lane| sources.get(*lane).is_some_and(|ids| !ids.is_empty()))
                        })
                        .unwrap_or(false)
                })
            } else {
                false
            };
            if !has_lane_eligible {
                return Err("no complete resolved candidate bank or mechanism-eligible candidate; preserved all trial records and rejection details; no strategy selected".into());
            }
        }

        let mut ranked_by_encounter = BTreeMap::<i64, Vec<Value>>::new();
        for row in &ranked {
            let encounter = row["encounterId"]
                .as_i64()
                .ok_or("leader lacks encounterId")?;
            ranked_by_encounter
                .entry(encounter)
                .or_default()
                .push(row.clone());
        }
        let keep_per_encounter = (config.candidates_per_generation / 2).max(1).min(4);
        let mut next_parents = Vec::new();
        for parent in &parents {
            let encounter = parent.intent["encounterId"]
                .as_i64()
                .ok_or("parent lacks encounterId")?;
            if !dispatch_encounter_ids.contains(&encounter) {
                next_parents.push(parent.clone());
            }
        }
        for encounter in &dispatch_encounter_ids {
            let leaders = ranked_by_encounter
                .get(encounter)
                .cloned()
                .unwrap_or_default();
            if config.objective_mode == "mechanism-lanes-v3" {
                let retained_before = next_parents.len();
                let sources =
                    objective_parent_sources(&objective_state, *encounter, &checkpoint_population)?;
                let mut selected_ids = Vec::<String>::new();
                for source in ["earned", "potential", "setup", "efficiency", "region"] {
                    if let Some(ids) = sources.get(source) {
                        for id in ids {
                            if !selected_ids.contains(id) {
                                selected_ids.push(id.clone());
                            }
                        }
                    }
                }
                // Preserve a bounded exploration slice and the existing earned leaders.
                if let Some(ids) = sources.get("exploration") {
                    for id in ids.iter().take(keep_per_encounter) {
                        if !selected_ids.contains(id) {
                            selected_ids.push(id.clone());
                        }
                    }
                }
                for row in leaders.iter().take(keep_per_encounter) {
                    let id = row["candidateSha256"].as_str().unwrap_or_default();
                    if !selected_ids.iter().any(|selected| selected == id) {
                        selected_ids.push(id.to_owned());
                    }
                }
                for id in selected_ids {
                    let found = checkpoint_population
                        .iter()
                        .chain(parents.iter())
                        .find(|candidate| {
                            hash(candidate.intent.as_ref()).ok().as_deref() == Some(id.as_str())
                        })
                        .cloned()
                        .or_else(|| {
                            objective_state
                                .records
                                .iter()
                                .find(|(key, entry)| {
                                    key.starts_with(&format!("{id}:"))
                                        && entry.encounter_id == *encounter
                                })
                                .map(|(_, entry)| Candidate {
                                    intent: Arc::new(entry.intent.clone()),
                                    source_candidate_id: Value::Null,
                                    parent_candidate_sha256: None,
                                    mutation_operator: None,
                                    mutation_target: None,
                                    mutation_scale: None,
                                })
                        });
                    if let Some(candidate) = found {
                        next_parents.push(candidate);
                    }
                }
                if next_parents.len() == retained_before {
                    return Err(format!("encounter {encounter} has no objective-eligible retained parent; saved records are preserved"));
                }
            } else {
                if leaders.is_empty() {
                    return Err(format!("encounter {encounter} has no complete resolved candidate bank; saved records are preserved"));
                }
                for row in leaders.iter().take(keep_per_encounter) {
                    next_parents.push(candidate_from_json(row)?);
                }
            }
        }
        next_parents
            .sort_by_key(|parent| parent.intent["encounterId"].as_i64().unwrap_or(i64::MAX));
        parents = next_parents;
        let retained_candidates = parents
            .iter()
            .map(|parent| {
                let encounter = parent.intent["encounterId"]
                    .as_i64()
                    .ok_or("parent lacks encounterId")?;
                Ok((encounter, parent.intent.as_ref().clone()))
            })
            .collect::<Result<Vec<_>, String>>()?;
        search_state.retain_candidates(&retained_candidates)?;
        let all_leaders_verified = ranked.iter().all(|leader| leader["verified"] == true);
        let any_diagnostic = ranked.iter().any(|leader| leader["diagnostic"] == true);
        save(
            &config.output.join("leaders.json"),
            &json!({
                "schema": "ka-rust-leaders-v2",
                "scopeSha256": scope_sha256,
                "parityAttestation": parity_attestation,
                "diagnostic": any_diagnostic,
                "verified": all_leaders_verified,
                "verificationOnly": !all_leaders_verified,
                "importDisabled": !all_leaders_verified,
                "leaders": ranked,
                "mutationWarnings": mutation_warnings,
                "coverageEncounters": dispatch_encounter_ids.len(),
                "dispatchEncounterIds": dispatch_encounter_ids,
                "retainedEncounterIds": encounter_ids,
                "websiteReady": false,
                "formationCompliance": "requires canonical parity confirmation"
            }),
        )?;
        let parked_population = checkpoint_population
            .iter()
            .filter(|candidate| {
                !dispatch_encounter_ids.contains(&candidate.intent["encounterId"].as_i64().unwrap())
            })
            .cloned()
            .collect::<Vec<_>>();
        pending_seed_banks = seed_banks
            .into_iter()
            .filter(|(encounter, _)| !dispatch_encounter_ids.contains(encounter))
            .collect();
        completed_slots = collect_pending_slots(
            &parked_population,
            &pending_seed_banks,
            &all_completed_slots,
            &journal,
            &scope_sha256,
            mixed_mode,
        )?;
        pending_generation = (!parked_population.is_empty()).then_some(generation + 1);
        pending_population = (!parked_population.is_empty()).then_some(parked_population.clone());
        save_checkpoint(
            &checkpoint_path,
            &json!({
                "schema": "ka-rust-checkpoint-v2",
                "scopeSha256": scope_sha256,
                "resumeCompatibilitySha256": resume_compatibility_sha256,
                "pendingGeneration": pending_generation,
                "pendingPopulation": parked_population.iter().map(candidate_json).collect::<Vec<_>>(),
                "pendingSeedPairs": config.seed_pairs,
                "pendingSeedPairsByEncounter": pending_seed_banks,
                "completedPendingSlots": completed_slots.values().collect::<Vec<_>>(),
                "nextGeneration": generation + 1,
                "targetGenerations": config.generations,
                "searchState": search_state.snapshot(),
                "objectiveMode":config.objective_mode,"objectiveVersion":objective_version,"objectiveState":objective_state,
                "parents": parents.iter().map(candidate_json).collect::<Vec<_>>()
            }),
        )?;
        save(
            &config.output.join("search-state.json"),
            &search_state.snapshot(),
        )?;
        telemetry.wall("rankingLeaderAndGenerationCheckpoint", timer);
        if let Some(reason) = stop_reason {
            status.write_final(&status_inventory(json!({"stage":if reason == "stop-requested" {"stopped"} else {"paused"},
                "reason":reason,"completedTrials":counters.measured,"reusedTrials":counters.reused,"rejectedTrials":counters.rejected,
                "durableTrials":counters.measured.saturating_add(counters.fresh_rejected),"elapsedSeconds":started.elapsed().as_secs_f64(),
                "acceptedOutstandingTrials":0,"uncommittedCompletedTrials":0,"executors":config.executors}),
                &dispatch_encounter_ids,&encounter_ids,&parents,&active_focus_encounter_ids))?;
            telemetry.complete = true;
            telemetry.flush()?;
            println!(
                "{}",
                json!({"ok":true,"stage":if reason == "stop-requested" {"stopped"} else {"paused"},"reason":reason,"output":config.output})
            );
            return Ok(());
        }
    }

    telemetry.complete = true;
    if telemetry.enabled {
        telemetry.journal = journal.telemetry_snapshot();
    }
    status.write_final(&json!({
        "stage": "complete",
        "acceptedOutstandingTrials": 0,
        "uncommittedCompletedTrials": 0,
        "remainingUnsubmittedTrialSlots": 0,
        "durableTrials": counters.measured.saturating_add(counters.fresh_rejected),
        "durableSuccessfulTrials": counters.measured,
        "durableRejectedTrials": counters.fresh_rejected,
        "durableTrialsPerSecond": counters.measured.saturating_add(counters.fresh_rejected) as f64 / started.elapsed().as_secs_f64().max(f64::MIN_POSITIVE),
        "durableTrialsPerHour": counters.measured.saturating_add(counters.fresh_rejected) as f64 / started.elapsed().as_secs_f64().max(f64::MIN_POSITIVE) * 3600.0,
        "terminalStatusExemptFromRateLimit": true,
        "executors": config.executors,
        "completedTrials": counters.measured,
        "reusedTrials": counters.reused,
        "rejectedTrials": counters.rejected,
        "elapsedSeconds": started.elapsed().as_secs_f64(),
        "output": config.output,
        "parityAttested": attested
    }))?;
    telemetry.flush()?;
    println!(
        "{}",
        json!({
            "ok": true,
            "completedTrials": counters.measured,
            "reusedTrials": counters.reused,
            "rejectedTrials": counters.rejected,
            "parityAttested": attested,
            "output": config.output
        })
    );
    Ok(())
}

fn run() -> Result<(), String> {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if args.first().and_then(|value| value.to_str()) == Some("--propose") {
        if args.len() != 5 {
            return Err("usage: ka-rust-standalone-optimizer --propose SCENARIO.json CATALOG.json REQUEST.json OUTPUT.json".into());
        }
        let parent = read(Path::new(&args[1]))?;
        let catalog = read(Path::new(&args[2]))?;
        let request = read(Path::new(&args[3]))?;
        if matches!(request["kind"].as_str(), Some("stat-axis" | "probe")) {
            let mut proposed = tuning::propose(&parent, &catalog, &request)?;
            proposed["parentCandidateSha256"] = json!(hash(&parent)?);
            proposed["requestSha256"] = json!(hash(&request)?);
            proposed["evaluationPerformed"] = json!(false);
            proposed["source"] = source_info()?;
            if let Some(rows) = proposed["candidates"].as_array_mut() {
                for row in rows {
                    row["candidateSha256"] = json!(hash(&row["intent"])?);
                    row["parentCandidateSha256"] = json!(hash(&parent)?);
                }
            }
            return save(Path::new(&args[4]), &proposed);
        }
        // Reject an invalid baseline before producing a tuning/experiment family.
        prepare::prepare(&parent, &catalog)?;
        let parent_hash = hash(&parent)?;
        let mut rows = Vec::new();
        for (intent, operator) in search::Search::proposed_descendants(&parent, &request)? {
            let validation = prepare::prepare(&intent, &catalog);
            let (admitted, prepared_hash, error) = match validation {
                Ok(prepared) => (true, Some(hash(&prepared)?), None),
                Err(error) => (false, None, Some(error)),
            };
            rows.push(json!({"intent":intent,"mutationOperator":operator,
                "candidateSha256":hash(&intent)?,"parentCandidateSha256":parent_hash,
                "admitted":admitted,"preparedSha256":prepared_hash,"validationError":error}));
        }
        return save(
            Path::new(&args[4]),
            &json!({"schema":"ka-rust-native-proposals-v1",
            "parentIntent":parent,"parentCandidateSha256":parent_hash,"request":request,
            "requestSha256":hash(&request)?,"candidates":rows,
            "evaluationPerformed":false,"source":source_info()?}),
        );
    }
    if args.first().and_then(|value| value.to_str()) == Some("--abi-layout") {
        if args.len() != 1 {
            return Err("usage: ka-rust-standalone-optimizer --abi-layout".into());
        }
        println!("{}", engine::abi_layout());
        return Ok(());
    }
    if args.first().and_then(|value| value.to_str()) == Some("--prepare") {
        return run_prepare(&args);
    }
    if args.first().and_then(|value| value.to_str()) == Some("--prepare-initialized") {
        return run_prepare_initialized(&args);
    }
    if args.first().and_then(|value| value.to_str()) == Some("--source-info") {
        if args.len() != 1 {
            return Err("usage: ka-rust-standalone-optimizer --source-info".into());
        }
        println!("{}", source_info()?);
        return Ok(());
    }
    if args.first().and_then(|value| value.to_str()) == Some("--objective-parity") {
        if args.len() != 3 {
            return Err(
                "usage: ka-rust-standalone-optimizer --objective-parity FIXTURE.json OUTPUT.json"
                    .into(),
            );
        }
        let fixture = read(Path::new(&args[1]))?;
        let result = objective::parity_document(&fixture)?;
        save(Path::new(&args[2]), &result)?;
        if result["passed"].as_bool() != Some(true) {
            return Err(format!(
                "Rust objective parity failed; report saved to {}",
                args[2].display()
            ));
        }
        println!(
            "{}",
            json!({"ok":true,"report":args[2],"cases":result["cases"].as_array().map(Vec::len).unwrap_or(0)})
        );
        return Ok(());
    }
    let config = args
        .first()
        .ok_or("usage: ka-rust-standalone-optimizer CONFIG.json")?;
    if args.len() != 1 {
        return Err("usage: ka-rust-standalone-optimizer CONFIG.json".into());
    }
    run_optimizer(Path::new(config))
}

fn main() {
    if let Err(error) = run() {
        eprintln!("{}", json!({ "ok": false, "error": error }));
        std::process::exit(1);
    }
}

#[cfg(test)]
mod objective_runtime_tests {
    use super::*;

    #[test]
    fn legacy_fixed_formation_and_corrected_profile_configs_parse() {
        let legacy = json!({
            "schema":"ka-rust-optimizer-config-v1",
            "kernel":"kernel.dll","catalog":"catalog.json",
            "candidates":"candidates.json","output":"output",
            "executors":1,"generations":1,"candidatesPerGeneration":1,
            "searchSeed":1,"seedPairs":[[1,1]],
            "mechanicsProvenance":{},"policyProvenance":{},
            "fixedFormation":true
        });
        let parsed_legacy: Config = serde_json::from_value(legacy.clone()).unwrap();
        assert!(parsed_legacy.fixed_formation);
        assert!(parsed_legacy.synthetic_dps_search.is_none());

        let mut corrected = legacy;
        corrected["objectiveMode"] = json!("mechanism-lanes-v3");
        corrected["syntheticDpsSearch"] = json!({
            "fixedParameters":{"10":2500,"11":2500,"14":2500,"19":18},
            "mutableParameters":["13","15","16"]
        });
        let parsed_corrected: Config = serde_json::from_value(corrected).unwrap();
        assert!(parsed_corrected.fixed_formation);
        assert_eq!(parsed_corrected.objective_mode, "mechanism-lanes-v3");
        assert_eq!(
            parsed_corrected
                .synthetic_dps_search
                .unwrap()
                .mutable_parameters,
            vec!["13", "15", "16"]
        );
    }

    #[test]
    fn stored_attack_progress_rewards_a_zero_earned_child_once() {
        let parent = json!({
            "candidate":"parent","encounterId":7,"defeatCount":0,"n":1,"wins":0,
            "earnedMax":null,"earnedCount":0,"potentialMax":null,"winInterval":[0.0,1.0],
            "meanResources":null,"progress":{"storedCommandsTargetingBossAtDeath":0},"progressRuns":1
        });
        let child = json!({
            "candidate":"child","encounterId":7,"defeatCount":0,"n":1,"wins":0,
            "earnedMax":null,"earnedCount":0,"potentialMax":null,"winInterval":[0.0,1.0],
            "meanResources":null,"progress":{"storedCommandsTargetingBossAtDeath":2},"progressRuns":1
        });
        let improvements = objective::improved_lanes(&parent, &child);
        assert_eq!(improvements, vec!["setup"]);
        let mut state = ObjectiveState::default();
        state.lineage_roots.insert("child".into(), "root".into());
        state.operator_stats.entry(7).or_default().insert(
            "set-stat".into(),
            LearnerCounter {
                attempts: 1,
                improved: 0,
                planned: 0,
            },
        );
        assert!(apply_lane_feedback(
            &mut state,
            7,
            "child",
            "set-stat",
            &improvements,
            Some("13"),
            Some(99)
        ));
        assert_eq!(state.root_productivity["root"].children, 1);
        assert_eq!(state.root_productivity["root"].improved, 1);
        assert_eq!(state.operator_stats[&7]["set-stat"].attempts, 1);
        assert_eq!(state.operator_stats[&7]["set-stat"].improved, 1);
        assert_eq!(state.stat_anchors[&7]["13"], 99);
        assert!(!apply_lane_feedback(
            &mut state,
            7,
            "child",
            "set-stat",
            &improvements,
            Some("13"),
            Some(99)
        ));
        assert_eq!(state.root_productivity["root"].children, 1);
    }

    #[test]
    fn common_prior_import_translates_only_supported_stat_labels_and_keeps_provenance() {
        let mut document = json!({
            "schema":"ka-mechanism-learner-prior-1",
            "objectiveMode":"mechanism-lanes-v3",
            "operatorStats":{"7":{"set-stat":{"attempts":4,"improved":2,"unproductive":2},
                "stat:atk":{"attempts":3},"stat:spd":{"attempts":1},"stat:lck":{"attempts":2}}},
            "statScales":{},"statAnchors":{"7":{"13":301,"15":802}},
            "sourceIdentity":{"historySha256":"abc123"}
        });
        let mut state = ObjectiveState::default();
        apply_common_prior_document(&mut document, "prior-sha", &mut state).unwrap();
        assert_eq!(state.operator_stats[&7]["set-stat"].improved, 2);
        assert_eq!(state.operator_stats[&7]["stat:13"].attempts, 3);
        assert_eq!(state.operator_stats[&7]["stat:15"].attempts, 1);
        assert_eq!(state.operator_stats[&7]["stat:16"].attempts, 2);
        assert_eq!(state.stat_anchors[&7]["13"], 301);
        assert_eq!(state.learner_prior_sha256.as_deref(), Some("prior-sha"));
        assert_eq!(
            state.learner_prior_source_identity.as_ref().unwrap()["historySha256"],
            "abc123"
        );
        assert!(state.lineage_roots.is_empty());
        assert!(state.records.is_empty());
    }

    #[test]
    fn synthetic_prior_fixture_loads_without_historical_training_data() {
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("requests/synthetic-common-learner-prior.json");
        let bytes = fs::read(&path).expect("synthetic learner-prior test fixture is required");
        let sha = engine::digest(&bytes);
        let mut document: Value = serde_json::from_slice(&bytes).unwrap();
        let mut state = ObjectiveState::default();
        apply_common_prior_document(&mut document, &sha, &mut state).unwrap();
        assert_eq!(state.learner_prior_sha256.as_deref(), Some(sha.as_str()));
        assert_eq!(
            state.learner_prior_source_identity.as_ref().unwrap(),
            &document["sourceIdentity"]
        );
        assert!(state
            .operator_stats
            .values()
            .any(|encounter| encounter.contains_key("set-stat")));
        assert!(state.lineage_roots.is_empty());
        assert!(state.records.is_empty());
    }
}
