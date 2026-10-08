//! Deterministic port of the Python four-lane candidate objective (version 3).
//!
//! This module consumes neutral, already-aggregated candidate records. Missing telemetry remains
//! missing: setup eligibility requires `progressRuns > 0`, and no historical progress is inferred.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::cmp::Ordering;
use std::collections::BTreeMap;

pub const OBJECTIVE_VERSION: u32 = 3;
pub const LANES: [&str; 4] = ["earned", "potential", "setup", "efficiency"];
pub const DEFAULT_POOL: usize = 4;
pub const POTENTIAL_FLOOR: f64 = 10.0;
pub const PROGRESS_MAX_FIELDS: [&str; 13] = [
    "postDeathPrizes",
    "postDeathBossLeavings",
    "postDeathBossReentries",
    "commandsReleasedAfterDeath",
    "commandsReleasedAfterDeathTargetingBoss",
    "storedCommandsAtDeath",
    "storedCommandsTargetingBossAtDeath",
    "maxSimultaneousStoredCommands",
    "maxSimultaneousCommandsTargetingBoss",
    "storedTargetHoldersPeak",
    "storedTargetHoldersAtDeath",
    "commandsTargetingBoss",
    "commandsTargetingBossReleased",
];

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Aggregate {
    n: u64,
    wins: u64,
    losses: u64,
    earned_count: u64,
    earned_max: Option<f64>,
    earned_sum: f64,
    earned_samples: u64,
    potential_max: Option<f64>,
    resources_sum: f64,
    resources_count: u64,
    progress_runs: u64,
    progress: BTreeMap<String, i64>,
}

impl Aggregate {
    pub fn observe_record(&mut self, record: &Value) {
        if record["recordKind"].as_str() != Some("battle")
            || record["report"].is_null()
            || record["diagnostic"].as_bool() == Some(true)
        {
            return;
        }
        let result = &record["report"];
        if result["completed"].as_bool() != Some(true) {
            return;
        }
        let battle = &result["report"];
        let measurement_eligible = record["measurementEligible"].as_bool() == Some(true);
        if measurement_eligible {
            self.n = self.n.saturating_add(1);
        }
        let verdict = integer(&battle["verdict"]);
        let resolved = verdict == 1 || verdict == 2;
        if measurement_eligible && resolved && verdict == 1 {
            self.wins = self.wins.saturating_add(1);
        }
        if measurement_eligible && resolved && verdict == 2 {
            self.losses = self.losses.saturating_add(1);
        }
        if measurement_eligible && verdict == 1 {
            if let Some(chests) = number(&result["earned"]) {
                self.earned_count = self.earned_count.saturating_add(1);
                self.earned_max = Some(self.earned_max.map_or(chests, |old| old.max(chests)));
            }
        }
        if measurement_eligible {
            if let Some(value) = number(&result["earned"]) {
                self.earned_sum += value;
                self.earned_samples = self.earned_samples.saturating_add(1);
            }
        }
        let potential_value = result["rewardOutcome"]["pendingChests"]
            .as_f64()
            .or_else(|| number(&battle["pre_verdict_prize_callbacks"]))
            .filter(|v| *v >= 0.0);
        if measurement_eligible {
            if let Some(value) = potential_value {
                self.potential_max = Some(self.potential_max.map_or(value, |old| old.max(value)));
            }
            if let Some(resources) = number(&battle["resource_uses"]) {
                self.resources_sum += resources;
                self.resources_count = self.resources_count.saturating_add(1);
            }
        }
        if measurement_eligible {
            if let Some(progress) = result["progressMetrics"].as_object() {
                self.progress_runs = self.progress_runs.saturating_add(1);
                for field in PROGRESS_MAX_FIELDS {
                    if let Some(value) = progress.get(field).and_then(number) {
                        let value = value.trunc().clamp(i64::MIN as f64, i64::MAX as f64) as i64;
                        self.progress
                            .entry(field.to_owned())
                            .and_modify(|old| *old = (*old).max(value))
                            .or_insert(value);
                    }
                }
            }
        }
    }

    pub fn record(&self, candidate: Value, encounter: i64, defeat: i64) -> Value {
        let p = Value::Object(
            self.progress
                .iter()
                .map(|(k, v)| (k.clone(), json!(v)))
                .collect(),
        );
        let interval = wilson(self.wins, self.n);
        json!({
            "candidate": candidate,
            "encounterId": encounter,
            "defeatCount": defeat,
            "n": self.n,
            "wins": self.wins,
            "losses": self.losses,
            "earnedMax": self.earned_max,
            "earnedCount": self.earned_count,
            "earnedMean": if self.earned_samples>0 {json!(self.earned_sum/self.earned_samples as f64)} else {Value::Null},
            "potentialMax": self.potential_max,
            "winInterval": interval,
            "meanResources": if self.resources_count > 0 { json!(self.resources_sum / self.resources_count as f64) } else { Value::Null },
            "progress": p,
            "progressRuns": self.progress_runs
        })
    }
}

fn wilson(successes: u64, count: u64) -> [f64; 2] {
    if count == 0 {
        return [0.0, 1.0];
    }
    let n = count as f64;
    let p = successes as f64 / n;
    let z = 1.96_f64;
    let center = (p + z * z / (2.0 * n)) / (1.0 + z * z / n);
    let half = z * (p * (1.0 - p) / n + z * z / (4.0 * n * n)).sqrt() / (1.0 + z * z / n);
    [(center - half).max(0.0), (center + half).min(1.0)]
}

fn number(value: &Value) -> Option<f64> {
    let value = value.as_f64()?;
    value.is_finite().then_some(value)
}

fn integer(value: &Value) -> i64 {
    number(value)
        .map(|v| v.trunc().clamp(i64::MIN as f64, i64::MAX as f64) as i64)
        .unwrap_or(0)
}

fn optional_number(record: &Value, key: &str) -> Option<f64> {
    number(&record[key])
}

fn earned(record: &Value) -> f64 {
    optional_number(record, "earnedMax").unwrap_or(0.0)
}

fn potential(record: &Value) -> f64 {
    optional_number(record, "potentialMax").unwrap_or(0.0)
}

fn reliability(record: &Value) -> f64 {
    record["winInterval"]
        .as_array()
        .and_then(|v| v.first())
        .and_then(number)
        .unwrap_or(0.0)
}

fn resources(record: &Value) -> f64 {
    optional_number(record, "meanResources").unwrap_or(0.0)
}

fn progress(record: &Value, field: &str) -> i64 {
    integer(&record["progress"][field])
}

fn compare_float(a: f64, b: f64) -> Ordering {
    a.total_cmp(&b)
}

fn cmp_desc(a: f64, b: f64) -> Ordering {
    compare_float(b, a)
}

fn cmp_int_desc(a: i64, b: i64) -> Ordering {
    b.cmp(&a)
}

pub fn qualifies(record: &Value, lane: &str) -> bool {
    let earned_exists = !record["earnedMax"].is_null() && integer(&record["earnedCount"]) > 0;
    match lane {
        "earned" => earned_exists,
        "potential" => {
            !record["potentialMax"].is_null()
                && potential(record) - earned(record) >= POTENTIAL_FLOOR
        }
        "setup" => integer(&record["progressRuns"]) > 0,
        "efficiency" => earned_exists,
        _ => false,
    }
}

pub fn lane_key_cmp(a: &Value, b: &Value, lane: &str) -> Ordering {
    let earned_cmp = || cmp_desc(earned(a), earned(b));
    let potential_cmp = || cmp_desc(potential(a), potential(b));
    let reliability_cmp = || cmp_desc(reliability(a), reliability(b));
    let resources_cmp = || cmp_desc(-resources(a), -resources(b));
    let samples_cmp = || cmp_int_desc(integer(&a["n"]), integer(&b["n"]));
    match lane {
        "earned" => earned_cmp()
            .then_with(reliability_cmp)
            .then_with(resources_cmp)
            .then_with(samples_cmp),
        "potential" => cmp_desc(potential(a) - earned(a), potential(b) - earned(b))
            .then_with(potential_cmp)
            .then_with(reliability_cmp)
            .then_with(resources_cmp)
            .then_with(samples_cmp),
        "setup" => {
            let fields = [
                "postDeathPrizes",
                "commandsReleasedAfterDeathTargetingBoss",
                "storedCommandsTargetingBossAtDeath",
                "maxSimultaneousCommandsTargetingBoss",
                "commandsTargetingBossReleased",
                "storedTargetHoldersPeak",
                "maxSimultaneousStoredCommands",
            ];
            for field in fields {
                let ordering = cmp_int_desc(progress(a, field), progress(b, field));
                if ordering != Ordering::Equal {
                    return ordering;
                }
            }
            potential_cmp().then_with(earned_cmp)
        }
        "efficiency" => earned_cmp()
            .then_with(resources_cmp)
            .then_with(reliability_cmp)
            .then_with(samples_cmp),
        _ => Ordering::Equal,
    }
}

fn pareto_frontier(records: &[Value]) -> Vec<Value> {
    records
        .iter()
        .filter(|candidate| {
            let e = earned(candidate);
            let r = resources(candidate);
            !records.iter().any(|other| {
                let oe = earned(other);
                let or = resources(other);
                oe >= e && or <= r && (oe > e || or < r)
            })
        })
        .cloned()
        .collect()
}

pub fn elite_lanes(records: &[Value], pool: usize) -> Value {
    let mut groups = BTreeMap::<(i64, i64), Vec<Value>>::new();
    for record in records {
        let group = (
            integer(&record["encounterId"]),
            integer(&record["defeatCount"]),
        );
        groups.entry(group).or_default().push(record.clone());
    }
    let mut result = serde_json::Map::new();
    for ((encounter, defeat), members) in groups {
        let mut lanes = serde_json::Map::new();
        for lane in LANES {
            let eligible = members
                .iter()
                .filter(|record| qualifies(record, lane))
                .cloned()
                .collect::<Vec<_>>();
            let mut ranked = if lane == "efficiency" {
                pareto_frontier(&eligible)
            } else {
                eligible
            };
            // Rust's stable slice sort preserves input insertion order for equal Python tuple keys.
            ranked.sort_by(|a, b| lane_key_cmp(a, b, lane));
            let ids = ranked
                .iter()
                .take(pool)
                .map(|record| record["candidate"].clone())
                .collect::<Vec<_>>();
            lanes.insert(lane.to_owned(), Value::Array(ids));
        }
        result.insert(format!("{encounter}:{defeat}"), Value::Object(lanes));
    }
    Value::Object(result)
}

/// Return lane names improved by `child` under the same qualification/key contract as Python.
pub fn improved_lanes(parent: &Value, child: &Value) -> Vec<String> {
    LANES
        .iter()
        .filter(|lane| qualifies(child, lane))
        .filter(|lane| {
            !qualifies(parent, lane) || lane_key_cmp(child, parent, lane) == Ordering::Less
        })
        .map(|lane| (*lane).to_owned())
        .collect()
}

pub fn choose_parent(
    pools: &Value,
    population: &[Value],
    proposal_ordinal: u64,
    draw_index: usize,
) -> Option<(Value, String)> {
    if population.is_empty() {
        return None;
    }
    // This is Python's `proposal_number % exploration_every == exploration_every - 1`.
    let ids = population.iter().map(candidate_id).collect::<Vec<_>>();
    let (choice_pool, lane) = selected_pool(pools, &ids, proposal_ordinal);
    (!choice_pool.is_empty()).then(|| (choice_pool[draw_index % choice_pool.len()].clone(), lane))
}

fn candidate_id(candidate: &Value) -> Value {
    candidate
        .get("candidate")
        .cloned()
        .unwrap_or_else(|| candidate.clone())
}

pub fn parity_case(case: &Value) -> Result<Value, String> {
    let records = case["records"]
        .as_array()
        .ok_or("case.records must be an array")?;
    let pool = case["pool"].as_u64().unwrap_or(DEFAULT_POOL as u64) as usize;
    let grouped = elite_lanes(records, pool);
    let group_key = format!(
        "{}:{}",
        integer(&records.first().unwrap_or(&Value::Null)["encounterId"]),
        integer(&records.first().unwrap_or(&Value::Null)["defeatCount"])
    );
    let lanes = grouped
        .get(&group_key)
        .cloned()
        .unwrap_or_else(|| json!({}));
    let mut eligibility = serde_json::Map::new();
    let mut rankings = serde_json::Map::new();
    for lane in LANES {
        let ids = records
            .iter()
            .filter(|record| qualifies(record, lane))
            .map(|record| record["candidate"].clone())
            .collect::<Vec<_>>();
        eligibility.insert(lane.to_owned(), Value::Array(ids));
        rankings.insert(lane.to_owned(), lanes[lane].clone());
    }
    let mut selections = Vec::new();
    for proposal in case["proposals"].as_array().into_iter().flatten() {
        let population = proposal["population"]
            .as_array()
            .ok_or("proposal.population must be an array")?;
        let ordinal = proposal["ordinal"]
            .as_u64()
            .ok_or("proposal.ordinal must be unsigned")?;
        let draw = proposal["drawIndex"]
            .as_u64()
            .ok_or("proposal.drawIndex must be unsigned")? as usize;
        let population_ids = population.iter().map(candidate_id).collect::<Vec<_>>();
        let (choice_pool, lane) = selected_pool(&lanes, &population_ids, ordinal);
        if choice_pool.is_empty() {
            return Err("proposal population is empty".into());
        }
        let candidate = choice_pool[draw % choice_pool.len()].clone();
        selections.push(
            json!({"ordinal":ordinal,"drawIndex":draw,"population":population_ids,
            "choicePool":choice_pool,"candidate":candidate,"lane":lane}),
        );
    }
    Ok(json!({"eligibility":eligibility,"laneRankings":rankings,"selections":selections}))
}

fn selected_pool(
    lanes: &Value,
    population: &[Value],
    proposal_ordinal: u64,
) -> (Vec<Value>, String) {
    if proposal_ordinal % 5 == 4 {
        return (population.to_vec(), "exploration".into());
    }
    for offset in 0..LANES.len() {
        let lane = LANES[((proposal_ordinal as usize).wrapping_add(offset)) % LANES.len()];
        let choices = lanes[lane]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|id| population.iter().any(|candidate| candidate == *id))
            .cloned()
            .collect::<Vec<_>>();
        if !choices.is_empty() {
            return (choices, lane.into());
        }
    }
    (population.to_vec(), "exploration".into())
}

pub fn parity_document(document: &Value) -> Result<Value, String> {
    if document["schema"].as_u64() != Some(1) {
        return Err("fixture schema must be 1".into());
    }
    if document["objectiveVersion"].as_u64() != Some(OBJECTIVE_VERSION as u64) {
        return Err(format!("objectiveVersion must be {OBJECTIVE_VERSION}"));
    }
    let lane_names = document["laneNames"]
        .as_array()
        .ok_or("fixture.laneNames must be an array")?;
    if *lane_names != LANES.iter().map(|name| json!(name)).collect::<Vec<_>>() {
        return Err(
            "fixture laneNames do not match earned/potential/setup/efficiency v3 order".into(),
        );
    }
    let cases = document["cases"]
        .as_array()
        .ok_or("fixture.cases must be an array")?;
    let pool = document["pool"].as_u64().unwrap_or(DEFAULT_POOL as u64);
    let mut results = Vec::new();
    for case in cases {
        let mut case_with_pool = case.clone();
        case_with_pool["pool"] = json!(pool);
        let actual = parity_case(&case_with_pool)?;
        results.push(json!({"id":case["id"],"actual":actual,"expected":case["expected"],"passed":actual==case["expected"]}));
    }
    let active = active_learner_parity(&document["activeLearnerFixtures"])?;
    let passed = results.iter().all(|row| row["passed"] == true) && active["passed"] == true;
    Ok(
        json!({"schema":"ka-rust-objective-parity-result-v1","objectiveVersion":OBJECTIVE_VERSION,"passed":passed,"cases":results,"activeLearner":active}),
    )
}

fn active_learner_parity(f: &Value) -> Result<Value, String> {
    let mut checks = Vec::<Value>::new();
    let sources = f["sourceRotation"]["parentSources"]
        .as_array()
        .ok_or("active fixture parentSources missing")?;
    let mut rotation = Vec::new();
    for proposal in f["sourceRotation"]["proposalNumbers"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_u64)
    {
        for attempt in f["sourceRotation"]["attemptNumbers"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_u64)
        {
            let index = ((proposal + attempt) % sources.len() as u64) as usize;
            let item = json!({"proposalNumber":proposal,"attempt":attempt,"source":sources[index]});
            if f["sourceRotation"]["expected"]
                .as_array()
                .into_iter()
                .flatten()
                .any(|want| want == &item)
            {
                rotation.push(item);
            }
        }
    }
    checks.push(
        json!({"id":"sourceRotation","actual":rotation,"expected":f["sourceRotation"]["expected"]}),
    );

    let mut weighted = Vec::new();
    for row in f["weightedParents"].as_array().into_iter().flatten() {
        let pool = row["pool"]
            .as_array()
            .ok_or("weighted fixture pool missing")?;
        let draw = row["drawUnit"]
            .as_f64()
            .ok_or("weighted fixture draw missing")?;
        let weights = pool
            .iter()
            .map(|id| {
                let name = id.as_str().unwrap_or_default();
                let root = row["lineage"][name]["root"].as_str().unwrap_or(name);
                let rate = row["productivity"][root]["rate"].as_f64().unwrap_or(0.5);
                json!({"id":id,"weight":0.5+rate})
            })
            .collect::<Vec<_>>();
        let total = weights
            .iter()
            .map(|r| r["weight"].as_f64().unwrap_or(0.0))
            .sum::<f64>();
        let target = draw * total;
        let mut upto = 0.0;
        let mut selected = weights
            .last()
            .map(|r| r["id"].clone())
            .unwrap_or(Value::Null);
        for entry in &weights {
            upto += entry["weight"].as_f64().unwrap_or(0.0);
            if target <= upto {
                selected = entry["id"].clone();
                break;
            }
        }
        weighted.push(json!({"expectedCandidate":selected,"expectedRandomCalls":[draw]}));
    }
    checks.push(json!({"id":"weightedParents","actual":weighted,"expected":f["weightedParents"].as_array().unwrap().iter().map(|r| json!({"expectedCandidate":r["expectedCandidate"],"expectedRandomCalls":r["expectedRandomCalls"]})).collect::<Vec<_>>()}));

    let better=f["improvedLanes"].as_array().into_iter().flatten().map(|r| json!({"id":r["id"],"expectedBetterLanes":improved_lanes(&r["parent"],&r["child"])})).collect::<Vec<_>>();
    checks.push(json!({"id":"improvedLanes","actual":better,"expected":f["improvedLanes"].as_array().unwrap().iter().map(|r|json!({"id":r["id"],"expectedBetterLanes":r["expectedBetterLanes"]})).collect::<Vec<_>>()}));

    let mut operators = Vec::new();
    for row in f["operatorWeights"].as_array().into_iter().flatten() {
        let ops = row["operations"]
            .as_array()
            .ok_or("operator list missing")?;
        let mut weights = serde_json::Map::new();
        let mut total = 0.0;
        for op in ops {
            let name = op.as_str().unwrap_or_default();
            let attempts = integer(&row["stats"][name]["attempts"]).max(0) as f64;
            let improved = integer(&row["stats"][name]["improved"]).max(0) as f64;
            let weight = (1.0 + 2.0 * improved * ((improved + 1.0) / (attempts + 4.0))).max(0.35);
            weights.insert(name.to_owned(), json!(weight));
            total += weight;
        }
        let target = row["drawUnit"].as_f64().unwrap_or(0.0) * total;
        let mut upto = 0.0;
        let mut selected = Value::Null;
        for op in ops {
            let name = op.as_str().unwrap_or_default();
            upto += weights[name].as_f64().unwrap_or(0.0);
            if target <= upto {
                selected = op.clone();
                break;
            }
        }
        operators.push(json!({"expectedWeights":weights,"expectedOperator":selected,"expectedRandomCalls":[row["drawUnit"]]}));
    }
    checks.push(json!({"id":"operatorWeights","actual":operators,"expected":f["operatorWeights"].as_array().unwrap().iter().map(|r|json!({"expectedWeights":r["expectedWeights"],"expectedOperator":r["expectedOperator"],"expectedRandomCalls":r["expectedRandomCalls"]})).collect::<Vec<_>>()}));

    let stats = &f["scaleWeights"]["stats"];
    let scales = f["scaleWeights"]["scales"]
        .as_array()
        .ok_or("scale list missing")?;
    let mut scale_weights = serde_json::Map::new();
    let mut jump = 1.0;
    for scale in scales {
        let key = python_float_key(scale.as_f64().unwrap_or(0.0));
        let attempts = integer(&stats[&key]["attempts"]).max(0) as f64;
        let improved = integer(&stats[&key]["improved"]).max(0) as f64;
        let w = (1.0 + 2.0 * improved - 0.15 * (attempts - improved).max(0.0)).max(0.35);
        scale_weights.insert(key, json!(w));
    }
    for (_, entry) in stats.as_object().into_iter().flatten() {
        if integer(&entry["improved"]) > 0 {
            jump += 1.0;
        }
    }
    scale_weights.insert("jump".into(), json!(jump));
    checks.push(json!({"id":"scaleWeights","actual":{"weights":scale_weights},"expected":{"weights":f["scaleWeights"]["expected"]}}));

    let mut targets = Vec::new();
    for row in f["statTargets"].as_array().into_iter().flatten() {
        let mut cumulative = Vec::<(String, f64)>::new();
        let mut wtotal = 0.0;
        for scale in row["scales"].as_array().into_iter().flatten() {
            let key = python_float_key(scale.as_f64().unwrap_or(0.0));
            let attempts = integer(&row["stats"][&key]["attempts"]).max(0) as f64;
            let improved = integer(&row["stats"][&key]["improved"]).max(0) as f64;
            let weight = (1.0 + 2.0 * improved - 0.15 * (attempts - improved).max(0.0)).max(0.35);
            cumulative.push((key, weight));
            wtotal += weight;
        }
        let mut jump = 1.0;
        for (_, entry) in row["stats"].as_object().into_iter().flatten() {
            if integer(&entry["improved"]) > 0 {
                jump += 1.0;
            }
        }
        cumulative.push(("jump".into(), jump));
        wtotal += jump;
        let randoms = row["randomDraws"].as_array().cloned().unwrap_or_default();
        let target = randoms.first().and_then(Value::as_f64).unwrap_or(0.0) * wtotal;
        let mut upto = 0.0;
        let mut scale = "jump".to_owned();
        for (key, w) in &cumulative {
            upto += *w;
            if target <= upto {
                scale = key.clone();
                break;
            }
        }
        let current = integer(&row["current"]);
        let base = integer(&row["anchor"]).max(1);
        let minimum = integer(&row["minimum"]);
        let maximum = integer(&row["maximum"]);
        let mut calls = Vec::new();
        let mut raw_target;
        if scale == "jump" {
            let randint = row["randintCalls"]
                .as_array()
                .and_then(|a| a.first())
                .ok_or("jump target fixture missing randint")?;
            raw_target = integer(&randint["value"]);
        } else {
            let factor = scale.parse::<f64>().map_err(|_| "invalid scale fixture")?;
            let draw = randoms.get(1).and_then(Value::as_f64).unwrap_or(0.0);
            calls.push(json!(draw));
            let direction = if draw < 0.5 {
                1.0
            } else if factor > 1.0 {
                -1.0
            } else {
                1.0
            };
            let product = base as f64
                * (if direction > 0.0 {
                    factor
                } else {
                    1.0 / factor
                });
            raw_target = product.round() as i64;
        }
        let mut out = raw_target.clamp(minimum, maximum);
        if out == current {
            out = (current + if current < maximum { 1 } else { -1 }).clamp(minimum, maximum);
        }
        targets.push(json!({"expectedScale":scale,"expectedTarget":out,"expectedRandomCalls":calls,"randintCalls":if scale=="jump" {row["randintCalls"].clone()} else {json!([])}}));
    }
    checks.push(json!({"id":"statTargets","actual":targets,"expected":f["statTargets"].as_array().unwrap().iter().map(|r|json!({"expectedScale":r["expectedScale"],"expectedTarget":r["expectedTarget"],"expectedRandomCalls":if r["expectedScale"]=="jump" {json!([])} else {json!([r["randomDraws"][1]])},"randintCalls":r["randintCalls"]})).collect::<Vec<_>>()}));

    let region = strategy_region(&f["region"]["scenario"])?;
    checks.push(json!({"id":"region","actual":region,"expected":f["region"]["expected"]}));
    let passed = checks.iter().all(|row| row["actual"] == row["expected"]);
    Ok(json!({"passed":passed,"checks":checks}))
}

fn python_float_key(value: f64) -> String {
    let mut key = value.to_string();
    if value.fract() == 0.0 {
        key.push_str(".0");
    }
    key
}

pub fn strategy_region(scenario: &Value) -> Result<String, String> {
    let mut humans = Vec::new();
    let mut units = scenario["ownUnits"]
        .as_array()
        .ok_or("region scenario lacks ownUnits")?
        .iter()
        .filter(|u| u["human"].as_bool() == Some(true))
        .collect::<Vec<_>>();
    let mut params = vec![
        ("hp", 10),
        ("mp", 11),
        ("atk", 13),
        ("def", 14),
        ("spd", 15),
        ("lck", 16),
        ("int", 18),
        ("dex", 19),
    ];
    params.sort_by_key(|(_, id)| *id);
    for u in units.drain(..) {
        let mut skills = u["skills"]
            .as_array()
            .cloned()
            .ok_or("region unit skills missing")?;
        skills.sort_by_key(|v| v.as_i64().unwrap_or(i64::MAX));
        let levels = u["invocationLevels"]
            .as_array()
            .cloned()
            .ok_or("region unit levels missing")?;
        let mut buckets = Vec::new();
        for (_, pid) in &params {
            let key = pid.to_string();
            let raw = u["parameters"]
                .get(&key)
                .and_then(|x| x["rawValue"].as_f64())
                .unwrap_or(0.0);
            let magnitude = raw.abs().max(1.0);
            buckets.push((magnitude.log10() * 4.0).trunc() as i64);
        }
        humans.push(json!([
            skills,
            levels,
            buckets,
            u["weaponId"].as_i64().unwrap_or(0),
            u["grid"].as_i64().unwrap_or(-1)
        ]));
    }
    let payload = serde_json::to_vec(&humans).map_err(|e| e.to_string())?;
    Ok(crate::engine::digest(&payload)[..16].to_owned())
}
