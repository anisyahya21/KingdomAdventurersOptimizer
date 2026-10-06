// Fixed-formation policy enforcement for the Rust engine.
//
// Latest explicit user scope (2026-10-06) supersedes the historical formation/search rules: one fixed
// six-unit formation whose ONLY search variable is the Synthetic DPS's raw stats, inside the derived
// synthetic walls. Admission and candidate generation both run through this module, so a historical
// or otherwise incompatible candidate can never enter the search.
use serde_json::{Map, Value};
use std::collections::BTreeMap;
use std::sync::OnceLock;

const POLICY_JSON: &str = include_str!("fixed_formation_policy.json");
const PARAMETERS: [i64; 12] = [10, 11, 12, 13, 14, 15, 16, 18, 19, 20, 21, 22];

fn policy() -> Result<&'static Value, String> {
    static POLICY: OnceLock<Result<Value, String>> = OnceLock::new();
    POLICY
        .get_or_init(|| {
            let value: Value = serde_json::from_str(POLICY_JSON)
                .map_err(|e| format!("decode fixed-formation policy: {e}"))?;
            if value["schema"].as_str() != Some("ka-fixed-formation-policy-1") {
                return Err("fixed-formation policy has an unexpected schema".into());
            }
            if value["fixed"]["rosterOrder"].as_array().map(|a| a.len()) != Some(6) {
                return Err("fixed-formation policy is incomplete".into());
            }
            Ok(value)
        })
        .as_ref()
        .map_err(|e| e.clone())
}

pub fn policy_hash() -> String {
    policy()
        .ok()
        .and_then(|value| value["policyHash"].as_str().map(str::to_owned))
        .unwrap_or_default()
}

fn encounter_key(encounter: i64) -> String {
    encounter.to_string()
}

pub fn searchable_parameters() -> Result<BTreeMap<i64, (i64, i64)>, String> {
    let policy = policy()?;
    let table = policy["dps"]["searchableParameters"]
        .as_object()
        .ok_or("fixed-formation policy lacks searchableParameters")?;
    let mut out = BTreeMap::new();
    for (key, interval) in table {
        let pid: i64 = key.parse().map_err(|_| format!("bad searchable parameter key {key}"))?;
        let low = interval[0].as_i64().ok_or("bad searchable minimum")?;
        let high = interval[1].as_i64().ok_or("bad searchable maximum")?;
        out.insert(pid, (low, high));
    }
    Ok(out)
}

fn bounded(pid: i64) -> bool {
    pid == 10 || pid == 11 || pid == 12
}

fn parameter_value(pid: i64, entry: &Value) -> Option<i64> {
    if bounded(pid) {
        entry["rawMax"].as_i64()
    } else {
        entry["rawValue"].as_i64()
    }
}

fn parameter_fields(entry: &Value) -> Option<(i64, i64, i64, i64, i64)> {
    Some((
        entry["rawValue"].as_i64()?,
        entry["rawMax"].as_i64()?,
        entry["extraValue"].as_i64()?,
        entry["extraMax"].as_i64()?,
        entry["trainingLevel"].as_i64()?,
    ))
}

fn same_parameters(actual: &Value, expected: &Value) -> bool {
    actual.as_object().map(|m| m.len()) == expected.as_object().map(|m| m.len())
        && actual
            .as_object()
            .zip(expected.as_object())
            .map(|(a, b)| {
                a.iter().all(|(key, value)| {
                    b.get(key)
                        .and_then(|other| parameter_fields(value).zip(parameter_fields(other)))
                        .map(|(x, y)| x == y)
                        .unwrap_or(false)
                })
            })
            .unwrap_or(false)
}

fn same_i64_array(actual: &Value, expected: &Value) -> bool {
    match (actual.as_array(), expected.as_array()) {
        (Some(a), Some(b)) => {
            a.len() == b.len()
                && a.iter().zip(b.iter()).all(|(x, y)| x.as_i64() == y.as_i64())
        }
        _ => false,
    }
}

fn unit_fixed_fields(unit: &Value, index: usize) -> Result<(), String> {
    if unit["human"].as_bool() != Some(true) {
        return Err(format!("fixed formation unit {index} is not human"));
    }
    if unit.get("monsterId").map(|v| !v.is_null()).unwrap_or(false) {
        return Err(format!("fixed formation unit {index} must not be a monster"));
    }
    if unit["leaderIdentity"].as_bool() == Some(true) || unit["visitor"].as_bool() == Some(true) {
        return Err(format!("fixed formation unit {index} must not be a leader or visitor"));
    }
    if unit["weaponId"].as_i64() != Some(0) {
        return Err(format!("fixed formation unit {index} must carry no weapon"));
    }
    if unit["equipment"].as_array().map(|a| !a.is_empty()).unwrap_or(true) {
        return Err(format!("fixed formation unit {index} must carry no equipment"));
    }
    Ok(())
}

pub fn validate(raw: &Value) -> Result<(), String> {
    let policy = policy()?;
    let roster = policy["fixed"]["rosterOrder"]
        .as_array()
        .ok_or("fixed-formation policy lacks rosterOrder")?;
    let units = raw["ownUnits"].as_array().ok_or("fixed formation needs ownUnits")?;
    if units.len() != 6 {
        return Err(format!("fixed formation needs exactly six units, found {}", units.len()));
    }
    for (index, unit) in units.iter().enumerate() {
        unit_fixed_fields(unit, index)?;
        if unit["name"].as_str() != roster[index].as_str() {
            return Err(format!("fixed formation roster order mismatch at {index}"));
        }
    }
    let encounter = raw["encounterId"].as_i64().ok_or("fixed formation needs encounterId")?;
    let defaults = policy["dps"]["defaults"]
        .get(encounter_key(encounter))
        .and_then(Value::as_object)
        .ok_or_else(|| format!("no fixed-formation DPS template for encounter {encounter}"))?;
    let bounds = searchable_parameters()?;
    let dps = &units[0];
    if !same_i64_array(&dps["skills"], &policy["dps"]["skills"])
        || !same_i64_array(&dps["invocationLevels"], &policy["dps"]["invocationLevels"])
    {
        return Err("Synthetic DPS skills/triggers must be the fixed Counter/7/5/4/3/2 High set".into());
    }
    let params = dps["parameters"].as_object().ok_or("Synthetic DPS needs parameters")?;
    if params.len() != PARAMETERS.len() {
        return Err("Synthetic DPS must carry exactly the canonical twelve parameters".into());
    }
    for pid in PARAMETERS {
        let key = pid.to_string();
        let entry = params.get(&key).ok_or_else(|| format!("Synthetic DPS is missing parameter {pid}"))?;
        match bounds.get(&pid) {
            Some((low, high)) => {
                if entry["extraValue"].as_i64() != Some(0) || entry["extraMax"].as_i64() != Some(0) {
                    return Err(format!("Synthetic DPS parameter {pid} must have zero extra (no equipment)"));
                }
                let value = parameter_value(pid, entry)
                    .ok_or_else(|| format!("Synthetic DPS parameter {pid} lacks a value"))?;
                if value < *low || value > *high {
                    return Err(format!("Synthetic DPS parameter {pid} value {value} outside [{low}, {high}]"));
                }
                if let Some(expected) = defaults.get(&key) {
                    if entry["trainingLevel"].as_i64() != expected["trainingLevel"].as_i64() {
                        return Err(format!("Synthetic DPS parameter {pid} trainingLevel must match the template"));
                    }
                }
            }
            None => {
                let expected = defaults.get(&key).ok_or_else(|| format!("Synthetic DPS parameter {pid} is not canonical"))?;
                if !same_parameters(&Value::Object(Map::from_iter([(key.clone(), entry.clone())])), &Value::Object(Map::from_iter([(key, expected.clone())]))) {
                    return Err(format!("Synthetic DPS parameter {pid} is fixed and must match the template"));
                }
            }
        }
    }
    check_fixed_unit(&units[1], &policy["healer"], "healer")?;
    for index in 2..6 {
        check_fixed_unit(&units[index], &policy["fodder"], &format!("fodder {index}"))?;
    }
    let first = strip_name(&units[2]);
    for index in 3..6 {
        if strip_name(&units[index]) != first {
            return Err("the four fodders must be identical".into());
        }
    }
    Ok(())
}

fn strip_name(unit: &Value) -> String {
    let mut clone = unit.clone();
    if let Some(obj) = clone.as_object_mut() {
        obj.remove("name");
        obj.remove("id");
    }
    clone.to_string()
}

fn check_fixed_unit(unit: &Value, template: &Value, label: &str) -> Result<(), String> {
    if unit["weaponId"].as_i64() != Some(0) {
        return Err(format!("{label} must carry no weapon"));
    }
    if unit["equipment"].as_array().map(|a| !a.is_empty()).unwrap_or(true) {
        return Err(format!("{label} must carry no equipment"));
    }
    if !same_i64_array(&unit["skills"], &template["skills"])
        || !same_i64_array(&unit["invocationLevels"], &template["invocationLevels"])
    {
        return Err(format!("{label} skills/triggers do not match the fixed template"));
    }
    if !same_parameters(&unit["parameters"], &template["parameters"]) {
        return Err(format!("{label} parameter blocks do not match the fixed template"));
    }
    Ok(())
}

pub fn identity(raw: &Value) -> Result<String, String> {
    let bounds = searchable_parameters()?;
    let dps = &raw["ownUnits"][0];
    let mut stats = BTreeMap::new();
    for pid in bounds.keys() {
        let entry = &dps["parameters"][pid.to_string()];
        let value = parameter_value(*pid, entry).ok_or_else(|| format!("Synthetic DPS is missing parameter {pid}"))?;
        stats.insert(pid.to_string(), value);
    }
    let projection = serde_json::json!({
        "encounterId": raw["encounterId"], "defeatCount": raw["defeatCount"], "tickLimit": raw["tickLimit"],
        "holyHerbStock": raw["holyHerbStock"], "inputs": raw["inputs"], "dpsStats": stats,
    });
    Ok(crate::engine::digest(projection.to_string().as_bytes()))
}

/// One child that changes exactly one Synthetic DPS raw stat, still inside the walls. `rng` is the
/// search RNG, so the policy step stays deterministic and checkpoint-reproducible.
pub fn mutate<F: FnMut() -> u64>(parent: &Value, rng: &mut F) -> Result<(Value, String), String> {
    validate(parent)?;
    let bounds = searchable_parameters()?;
    let mut ids: Vec<i64> = bounds.keys().copied().collect();
    ids.sort_unstable();
    let mut child = parent.clone();
    let start = (rng() % ids.len() as u64) as usize;
    for offset in 0..ids.len() {
        let pid = ids[(start + offset) % ids.len()];
        let (low, high) = bounds[&pid];
        let key = pid.to_string();
        let entry = child["ownUnits"][0]["parameters"][&key].clone();
        let current = parameter_value(pid, &entry).ok_or("DPS parameter has no value")?;
        for factor in [1.15_f64, 1.35, 1.6, 0.85, 0.7] {
            let mut target = (current as f64 * factor).round() as i64;
            target = target.clamp(low, high);
            if target == current {
                continue;
            }
            let mut updated = entry.clone();
            updated["rawValue"] = serde_json::json!(target);
            updated["extraValue"] = serde_json::json!(0);
            if bounded(pid) {
                updated["rawMax"] = serde_json::json!(target);
                updated["extraMax"] = serde_json::json!(0);
            }
            child["ownUnits"][0]["parameters"][&key] = updated;
            if validate(&child).is_ok() {
                return Ok((child, "set-dps-stat".into()));
            }
            child["ownUnits"][0]["parameters"][&key] = entry.clone();
        }
    }
    Err("no legal Synthetic DPS stat step inside the fixed walls".into())
}
