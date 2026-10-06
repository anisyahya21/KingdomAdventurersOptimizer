//! Deterministic candidate mutation and mean-Earned selection.
//!
//! This module deliberately mutates only explicit raw scenario fields. It does not
//! normalize or declare a candidate legal; the canonical preparation/admission layer
//! must validate every returned Value before dispatch.

use serde_json::Value;
use std::collections::{BTreeMap, HashSet};

#[derive(Clone)]
enum Change {
    Invocation(usize, usize, u64),
    EquipmentLevel(usize, usize, u64),
    SkillAdd(usize, usize, i64, u64),
    SkillRemove(usize, usize),
    SkillReplace(usize, usize, i64, u64),
    SkillMove(usize, usize, usize),
    SetTrigger(usize, usize, u64),
    InputTick(usize, u64),
    InputPhase(usize, &'static str),
    HolyHerbUses(u64),
}

impl Change {
    fn operator(&self) -> &'static str {
        match self {
            Self::Invocation(..) => "invocation-level",
            Self::EquipmentLevel(..) => "equipment-level",
            Self::SkillAdd(..) => "add-skill",
            Self::SkillRemove(..) => "remove-skill",
            Self::SkillReplace(..) => "replace-skill",
            Self::SkillMove(..) => "move-skill",
            Self::SetTrigger(..) => "set-trigger",
            Self::InputTick(..) => "input-tick",
            Self::InputPhase(..) => "input-phase",
            Self::HolyHerbUses(..) => "herb-policy",
        }
    }
}

#[derive(Clone, Debug)]
struct Sample {
    candidate: Value,
    earned: f64,
    count: u64,
}

fn is_scalar(value: &Value) -> bool {
    matches!(value, Value::Null | Value::Bool(_) | Value::Number(_) | Value::String(_))
}

fn is_frozen_layout_pointer(parent: &Value, pointer: &str) -> bool {
    if parent.get("prePlacement").is_none() {
        return false;
    }
    let segments = pointer
        .split('/')
        .skip(1)
        .map(|segment| segment.replace("~1", "/").replace("~0", "~"))
        .collect::<Vec<_>>();
    if segments.iter().any(|segment| segment == "prePlacement") {
        return true;
    }
    if segments.first().map(String::as_str) == Some("ownUnits") {
        return matches!(segments.get(2).map(String::as_str), Some(
            "name" | "human" | "monsterId" | "cell" | "grid" | "formationValue"
                | "leaderIdentity" | "visitor" | "humanFlags" | "ownerPlayer" | "friend"
                | "onVehicle"
        ));
    }
    false
}

// IDs resolved from the original search_contract.WHITELIST_NAMES through the
// canonical skillsByName table. 6-Hit Attack (109), derived weapon attacks and
// unreviewed catalog rows are deliberately excluded.
const APPROVED_SEARCH_SKILLS: &[i64] = &[
    22, 23, 24, 25, 110, 26, 30, 108, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14,
    15, 16, 17, 18, 19, 21, 20, 39, 38, 37, 36, 27, 28, 41, 40, 33, 34,
    35, 114, 115, 116, 31, 32, 29, 4, 117, 118, 119, 107, 106, 105,
];

fn skill_row<'a>(catalog_rows: &'a [Value], skill_id: i64) -> Option<&'a Value> {
    catalog_rows.iter().find(|row| row.get("id").and_then(Value::as_i64) == Some(skill_id))
}

fn is_formation_skill(skill_id: i64, catalog_rows: Option<&[Value]>) -> bool {
    skill_id == 105 || skill_id == 106 || skill_id == 107
        || catalog_rows.and_then(|rows| skill_row(rows, skill_id))
            .and_then(|row| row.get("type")).and_then(Value::as_i64) == Some(60)
}

/// Search state with reproducible xorshift64* draws and online mean selection.
pub struct Search {
    rng: u64,
    samples: BTreeMap<String, Sample>,
    best_key: Option<String>,
    operator_trials: BTreeMap<String, u64>,
    operator_wins: BTreeMap<String, u64>,
}

impl Search {
    pub fn new(seed: u64) -> Self {
        // xorshift's all-zero state is absorbing.
        let state = if seed == 0 {
            0x9e37_79b9_7f4a_7c15
        } else {
            seed
        };
        Self {
            rng: state,
            samples: BTreeMap::new(),
            best_key: None,
            operator_trials: BTreeMap::new(),
            operator_wins: BTreeMap::new(),
        }
    }

    /// Restore the deterministic generator and measured means from a checkpoint.
    /// Unknown or malformed checkpoint state is rejected instead of silently reset.
    pub fn restore(state: &Value) -> Result<Self, String> {
        if state["schema"].as_str() != Some("ka-rust-search-state-v1") {
            return Err("incompatible search-state checkpoint schema".into());
        }
        let rng = state["rng"]
            .as_u64()
            .filter(|value| *value != 0)
            .ok_or("search-state checkpoint has invalid rng")?;
        let mut search = Self::new(rng);
        for (name, field) in [("operatorTrials", &mut search.operator_trials),
                              ("operatorWins", &mut search.operator_wins)] {
            let rows = state[name]
                .as_object()
                .ok_or_else(|| format!("search-state checkpoint lacks {name}"))?;
            for (operator, count) in rows {
                let count = count
                    .as_u64()
                    .ok_or_else(|| format!("invalid {name} count for {operator}"))?;
                field.insert(operator.clone(), count);
            }
        }
        let samples = state["samples"]
            .as_array()
            .ok_or("search-state checkpoint lacks samples")?;
        for row in samples {
            let candidate = row.get("candidate").cloned().ok_or("sample lacks candidate")?;
            let earned = row["earnedSum"].as_f64().filter(|v| v.is_finite() && *v >= 0.0)
                .ok_or("sample has invalid earnedSum")?;
            let count = row["count"].as_u64().filter(|v| *v > 0)
                .ok_or("sample has invalid count")?;
            let key = serde_json::to_string(&candidate).map_err(|e| format!("serialize sample: {e}"))?;
            search.samples.insert(key, Sample { candidate, earned, count });
        }
        search.reselect_best();
        Ok(search)
    }

    /// Compact, JSON-safe resumable state for the generation checkpoint.
    pub fn snapshot(&self) -> Value {
        let samples = self.samples.values().map(|sample| serde_json::json!({
            "candidate": sample.candidate.clone(),
            "earnedSum": sample.earned,
            "count": sample.count
        })).collect::<Vec<_>>();
        serde_json::json!({
            "schema": "ka-rust-search-state-v1",
            "rng": self.rng,
            "samples": samples,
            "operatorTrials": self.operator_trials,
            "operatorWins": self.operator_wins
        })
    }

    fn reselect_best(&mut self) {
        self.best_key = self.samples.iter().max_by(|(key_a, a), (key_b, b)| {
            (a.earned / a.count as f64).partial_cmp(&(b.earned / b.count as f64))
                .unwrap_or(std::cmp::Ordering::Equal).then_with(|| key_b.cmp(key_a))
        }).map(|(key, _)| key.clone());
    }

    /// Record whether this mutation family improved its parent's measured mean.
    /// A Beta(1,1) prior keeps every family reachable while rewarding evidence.
    pub fn observe_operator(&mut self, operator: &str, improved: bool) {
        let trials = self.operator_trials.entry(operator.to_owned()).or_default();
        *trials = trials.saturating_add(1);
        if improved {
            let wins = self.operator_wins.entry(operator.to_owned()).or_default();
            *wins = wins.saturating_add(1);
        }
    }

    /// Drop measurements outside the current parent frontier while preserving
    /// deterministic RNG and learned operator history. The durable battle ledger
    /// remains the complete historical evidence source.
    pub fn retain_candidates(&mut self, parents: &[Value]) {
        let retained = parents.iter().filter_map(|candidate| {
            serde_json::to_string(candidate).ok()
        }).collect::<HashSet<_>>();
        self.samples.retain(|key, _| retained.contains(key));
        self.reselect_best();
    }

    /// Posterior mean used by callers when scheduling mutation families.
    pub fn operator_weight(&self, operator: &str) -> f64 {
        let trials = *self.operator_trials.get(operator).unwrap_or(&0) as f64;
        let wins = *self.operator_wins.get(operator).unwrap_or(&0) as f64;
        (wins + 1.0) / (trials + 2.0)
    }

    /// Try bounded mutations until one differs from already admitted/search-seen
    /// raw candidates. Canonical preparation remains the legality authority.
    pub fn mutate_unique(
        &mut self,
        parent: &Value,
        seen: &mut HashSet<String>,
        attempts: usize,
    ) -> Result<(Value, String), String> {
        let mut last_error = String::from("mutation repeated an existing candidate");
        for _ in 0..attempts.clamp(1, 64) {
            match self.mutate_with_operator(parent) {
                Ok((candidate, operator)) => {
                    let key = serde_json::to_string(&candidate)
                        .map_err(|e| format!("serialize mutation identity: {e}"))?;
                    if seen.insert(key) {
                        return Ok((candidate, operator));
                    }
                    last_error = "supported mutations repeated candidates already in this search".into();
                }
                Err(error) => last_error = error,
            }
        }
        Err(last_error)
    }

    /// Catalog-backed search path. It keeps retries bounded and returns only a
    /// raw mutation admitted by the same canonical preparer used for battles.
    pub fn mutate_unique_with_catalog(
        &mut self,
        parent: &Value,
        catalog: &Value,
        seen: &mut HashSet<String>,
        attempts: usize,
    ) -> Result<(Value, String), String> {
        let mut last_error = String::from("mutation repeated an existing candidate");
        for _ in 0..attempts.clamp(1, 64) {
            match self.mutate_with_catalog(parent, catalog) {
                Ok((candidate, operator)) => {
                    if let Err(error) = crate::prepare::prepare(&candidate, catalog) {
                        last_error = format!("{operator}: canonical preparation rejected candidate: {error}");
                        continue;
                    }
                    let key = serde_json::to_string(&candidate)
                        .map_err(|e| format!("serialize mutation identity: {e}"))?;
                    if seen.insert(key) {
                        return Ok((candidate, operator));
                    }
                    last_error = "supported mutations repeated candidates already in this search".into();
                }
                Err(error) => last_error = error,
            }
        }
        Err(last_error)
    }

    /// Fixed-formation search path: generates only Synthetic DPS raw-stat children inside the walls
    /// and re-validates each against the policy before returning it.
    pub fn mutate_fixed_unique(
        &mut self,
        parent: &Value,
        seen: &mut HashSet<String>,
        attempts: usize,
    ) -> Result<(Value, String), String> {
        let mut last_error = String::from("fixed-formation mutation repeated an existing candidate");
        for _ in 0..attempts.clamp(1, 64) {
            let mut rng = || self.next_u64();
            match crate::fixed_formation::mutate(parent, &mut rng) {
                Ok((candidate, operator)) => {
                    if let Err(error) = crate::fixed_formation::validate(&candidate) {
                        last_error = format!("{operator}: fixed-formation revalidation failed: {error}");
                        continue;
                    }
                    let key = serde_json::to_string(&candidate)
                        .map_err(|e| format!("serialize mutation identity: {e}"))?;
                    if seen.insert(key) {
                        return Ok((candidate, operator));
                    }
                    last_error = "fixed-formation mutation repeated candidates already in this search".into();
                }
                Err(error) => last_error = error,
            }
        }
        Err(last_error)
    }

    /// Generate explicit knob alternatives without coercion or clamping.
    /// Request shape: {"knobs":[{"pointer":"/ownUnits/0/equipment/0/level",
    /// "values":[1,2]}]}. Skill removal shape: {"kind":"remove-skills",
    /// "units":[0,1]}. Each pointer must resolve to an existing scalar.
    /// Canonical preparation remains responsible for full legality.
    pub fn proposed_descendants(parent: &Value, request: &Value) -> Result<Vec<(Value, String)>, String> {
        if let Some(kind) = request.get("kind").and_then(Value::as_str) {
            if kind != "remove-skills" {
                return Err(format!("unsupported proposal request kind: {kind}"));
            }
            let units = parent["ownUnits"].as_array().ok_or("scenario.ownUnits must be an array")?;
            let selected = if let Some(indices) = request.get("units") {
                let indices = indices.as_array().ok_or("remove-skills units must be an array")?;
                let mut parsed = Vec::with_capacity(indices.len());
                let mut unique = HashSet::new();
                for value in indices {
                    let index = value.as_u64().and_then(|v| usize::try_from(v).ok())
                        .ok_or("remove-skills unit indices must be nonnegative integers")?;
                    if index >= units.len() {
                        return Err(format!("remove-skills unit index out of range: {index}"));
                    }
                    if !unique.insert(index) {
                        return Err(format!("duplicate remove-skills unit index: {index}"));
                    }
                    parsed.push(index);
                }
                parsed
            } else {
                (0..units.len()).collect()
            };
            let mut output = Vec::new();
            let mut skill_groups: BTreeMap<i64, Vec<(usize, usize)>> = BTreeMap::new();
            for unit_index in selected {
                let unit = &units[unit_index];
                if !unit.get("human").and_then(Value::as_bool).unwrap_or(false) {
                    continue;
                }
                let skills = unit.get("skills").and_then(Value::as_array)
                    .ok_or_else(|| format!("ownUnits[{unit_index}].skills must be an array"))?;
                let levels = unit.get("invocationLevels").and_then(Value::as_array)
                    .ok_or_else(|| format!("ownUnits[{unit_index}].invocationLevels must be an array"))?;
                if skills.len() != levels.len() {
                    return Err(format!("ownUnits[{unit_index}] skills/invocationLevels length mismatch"));
                }
                for (skill_index, skill) in skills.iter().enumerate() {
                    let skill_id = skill.as_i64().ok_or_else(|| {
                        format!("ownUnits[{unit_index}].skills[{skill_index}] must be an integer")
                    })?;
                    if parent.get("prePlacement").is_some()
                        && is_formation_skill(skill_id, None)
                    {
                        continue;
                    }
                    skill_groups.entry(skill_id).or_default().push((unit_index, skill_index));
                    let mut child = parent.clone();
                    let child_skills = child["ownUnits"][unit_index]["skills"].as_array_mut()
                        .ok_or("skills must be an array")?;
                    child_skills.remove(skill_index);
                    let child_levels = child["ownUnits"][unit_index]["invocationLevels"].as_array_mut()
                        .ok_or("invocationLevels must be an array")?;
                    child_levels.remove(skill_index);
                    if output.len() >= 4096 {
                        return Err("remove-skills request exceeds 4096 descendants".into());
                    }
                    output.push((child, format!("explicit:remove-skill:{unit_index}:{skill_index}:{skill_id}")));
                }
            }
            for (skill_id, slots) in skill_groups.into_iter().filter(|(_, slots)| slots.len() >= 2) {
                let mut child = parent.clone();
                let mut removals = slots;
                removals.sort_unstable_by(|a, b| b.cmp(a));
                for (unit_index, skill_index) in removals {
                    let skills = child["ownUnits"][unit_index]["skills"].as_array_mut()
                        .ok_or("skills must be an array")?;
                    if skills.get(skill_index).and_then(Value::as_i64) != Some(skill_id) {
                        return Err("skill-removal group no longer matches the source intent".into());
                    }
                    skills.remove(skill_index);
                    let levels = child["ownUnits"][unit_index]["invocationLevels"].as_array_mut()
                        .ok_or("invocationLevels must be an array")?;
                    if skill_index >= levels.len() {
                        return Err("skill-removal invocation level index is missing".into());
                    }
                    levels.remove(skill_index);
                }
                if output.len() >= 4096 {
                    return Err("remove-skills request exceeds 4096 descendants".into());
                }
                output.push((child, format!("explicit:remove-team-skill:{skill_id}")));
            }
            return Ok(output);
        }
        let knobs = request["knobs"].as_array().ok_or("proposal request requires a knobs array")?;
        let mut output = Vec::new();
        for knob in knobs {
            let pointer = knob["pointer"].as_str().filter(|p| p.starts_with('/') && p.len() > 1)
                .ok_or("proposal knob requires a non-root JSON pointer")?;
            if is_frozen_layout_pointer(parent, pointer) {
                return Err(format!("proposal pointer changes frozen roster/layout under captured prePlacement: {pointer}"));
            }
            let values = knob["values"].as_array().filter(|v| !v.is_empty() && v.len() <= 4096)
                .ok_or("proposal values must be a nonempty array of at most 4096 values")?;
            let current = parent.pointer(pointer).ok_or_else(|| format!("proposal pointer does not exist: {pointer}"))?;
            if !is_scalar(current) {
                return Err(format!("proposal pointer must target an existing scalar: {pointer}"));
            }
            for value in values {
                if !is_scalar(value) {
                    return Err(format!("proposal value must be scalar: {pointer}"));
                }
                if value == current { continue; }
                if output.len() >= 4096 {
                    return Err("proposal request exceeds 4096 descendants".into());
                }
                let mut child = parent.clone();
                let target = child.pointer_mut(pointer)
                    .ok_or_else(|| format!("proposal pointer does not exist: {pointer}"))?;
                *target = value.clone();
                output.push((child, format!("explicit:{pointer}")));
            }
        }
        Ok(output)
    }

    fn next_u64(&mut self) -> u64 {
        let mut x = self.rng;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.rng = x;
        x.wrapping_mul(0x2545_f491_4f6c_dd1d)
    }

    fn pick(&mut self, upper: usize) -> usize {
        (self.next_u64() % upper as u64) as usize
    }

    /// Mutate known raw inputs. The output remains a candidate, not an admitted
    /// scenario: preparation must validate skill IDs, equipment, formation and all
    /// other constraints. Unknown/unsupported scenario shapes fail without edits.
    pub fn mutate(&mut self, parent: &Value) -> Result<Value, String> {
        self.mutate_with_operator(parent).map(|(candidate, _)| candidate)
    }

    /// As `mutate`, with the selected mutation family returned for learning.
    pub fn mutate_with_operator(&mut self, parent: &Value) -> Result<(Value, String), String> {
        self.mutate_internal(parent, None)
    }

    /// Catalog-backed variant. Add/replace proposals use only the fixed original
    /// approved-skill set and catalog facts; canonical prepare remains admission.
    pub fn mutate_with_catalog(&mut self, parent: &Value, catalog: &Value) -> Result<(Value, String), String> {
        self.mutate_internal(parent, Some(catalog))
    }

    fn mutate_internal(&mut self, parent: &Value, catalog: Option<&Value>) -> Result<(Value, String), String> {
        let mut child = parent.clone();
        let obj = child
            .as_object_mut()
            .ok_or("scenario must be a JSON object")?;
        let units = obj
            .get("ownUnits")
            .and_then(Value::as_array)
            .ok_or("scenario.ownUnits must be an array")?;
        if units.is_empty() {
            return Err("scenario.ownUnits is empty".into());
        }
        if units.len() > 32 {
            return Err("scenario.ownUnits exceeds canonical 32-unit admission bound".into());
        }
        let catalog_skills = if let Some(catalog) = catalog {
            Some(catalog.get("profiles").and_then(|v| v.get("skills"))
                .and_then(Value::as_array)
                .ok_or("catalog.profiles.skills must be an array")?.as_slice())
        } else { None };
        let freeze_captured_layout = parent.get("prePlacement").is_some();

        // All options preserve source values and remain inside locally known
        // input domains. The full canonical preparer still admits/rejects each
        // result before dispatch.
        let mut changes = Vec::new();
        for (ui, unit) in units.iter().enumerate() {
            let skills = unit
                .get("skills")
                .and_then(Value::as_array)
                .ok_or("unit.skills must be an array")?;
            let levels = unit
                .get("invocationLevels")
                .and_then(Value::as_array)
                .ok_or("unit.invocationLevels must be an array")?;
            if skills.len() != levels.len() {
                return Err("skills and invocationLevels length mismatch".into());
            }
            if skills.len() > 12 {
                return Err("unit skills exceed canonical 12-slot admission bound".into());
            }
            for si in 0..levels.len() {
                let old = levels[si]
                    .as_u64()
                    .filter(|v| *v <= 2)
                    .ok_or("invocation level must be 0, 1, or 2")?;
                for next in 0..=2 {
                    if next != old {
                        changes.push(Change::Invocation(ui, si, next));
                    }
                }
            }
            if unit.get("human").and_then(Value::as_bool) == Some(true) {
                let skill_ids = skills.iter().map(|value| value.as_i64()
                    .ok_or("human skill IDs must be integers"))
                    .collect::<Result<Vec<_>, _>>()?;
                let forms = skill_ids.iter().filter(|id| is_formation_skill(**id, catalog_skills)).count();
                for si in 0..skill_ids.len() {
                    if !freeze_captured_layout || !is_formation_skill(skill_ids[si], catalog_skills) {
                        changes.push(Change::SkillRemove(ui, si));
                    }
                    if !is_formation_skill(skill_ids[si], catalog_skills) {
                        for trigger in 0..=2 {
                            if levels[si].as_u64() != Some(trigger) {
                                changes.push(Change::SetTrigger(ui, si, trigger));
                            }
                        }
                    }
                    for to in 0..skill_ids.len() {
                        if si != to { changes.push(Change::SkillMove(ui, si, to)); }
                    }
                }

                if let Some(rows) = catalog_skills {
                    let equipment = catalog.and_then(|value| value.get("profiles"))
                        .and_then(|value| value.get("equipment"))
                        .and_then(Value::as_array).ok_or("catalog.profiles.equipment must be an array")?;
                    let weapon_id = unit.get("weaponId").and_then(Value::as_i64)
                        .ok_or("human weaponId must be an integer")?;
                    let weapon_type = equipment.iter()
                        .find(|row| row.get("id").and_then(Value::as_i64) == Some(weapon_id))
                        .and_then(|row| row.get("type")).and_then(Value::as_i64)
                        .ok_or("human weaponId is absent from catalog equipment")?;
                    let team_seven_hit = units.iter()
                        .filter(|entry| entry.get("human").and_then(Value::as_bool) == Some(true))
                        .filter_map(|entry| entry.get("skills").and_then(Value::as_array))
                        .flatten().filter(|skill| skill.as_i64() == Some(110)).count();
                    for skill_id in APPROVED_SEARCH_SKILLS.iter().copied() {
                        let Some(row) = skill_row(rows, skill_id) else { continue };
                        let required_type = row.get("requiredEquipType").and_then(Value::as_i64).unwrap_or(-1);
                        if required_type != -1 && weapon_type != required_type { continue; }
                        let formation = is_formation_skill(skill_id, Some(rows));
                        let has_id = skill_ids.contains(&skill_id);
                        let can_add_seven = skill_id != 110 || team_seven_hit < 2;
                        if !has_id && can_add_seven && skill_ids.len() < 9
                            && (!freeze_captured_layout || !formation)
                            && (!formation || forms == 0)
                        {
                            for position in 0..=skill_ids.len() {
                                let triggers: &[u64] = if formation { &[1] } else { &[0, 1, 2] };
                                for trigger in triggers {
                                    changes.push(Change::SkillAdd(ui, position, skill_id, *trigger));
                                }
                            }
                        }
                        for si in 0..skill_ids.len() {
                            if skill_ids[si] == skill_id
                                || skill_ids.iter().enumerate().any(|(index, existing)| index != si && *existing == skill_id)
                            { continue; }
                            let removed_form = if is_formation_skill(skill_ids[si], catalog_skills) { 1 } else { 0 };
                            let remaining_forms = forms.saturating_sub(removed_form);
                            if freeze_captured_layout && formation { continue; }
                            if formation && remaining_forms > 0 { continue; }
                            let removed_seven = if skill_ids[si] == 110 { 1 } else { 0 };
                            if skill_id == 110 && team_seven_hit.saturating_sub(removed_seven) >= 2 { continue; }
                            let triggers: &[u64] = if formation { &[1] } else { &[0, 1, 2] };
                            for trigger in triggers {
                                changes.push(Change::SkillReplace(ui, si, skill_id, *trigger));
                            }
                        }
                    }
                }
            }
            if let Some(equipment) = unit.get("equipment").and_then(Value::as_array) {
                for (ei, slot) in equipment.iter().enumerate() {
                    let level = slot.get("level").and_then(Value::as_u64)
                        .ok_or("equipment slot level must be an unsigned integer")?;
                    if level > 1 { changes.push(Change::EquipmentLevel(ui, ei, level - 1)); }
                    if level < i64::MAX as u64 { changes.push(Change::EquipmentLevel(ui, ei, level + 1)); }
                }
            }
        }

        if let Some(inputs) = obj.get("inputs").and_then(Value::as_array) {
            for (ii, input) in inputs.iter().enumerate() {
                if let Some(tick) = input.get("tick").and_then(Value::as_u64) {
                    if tick > 0 {
                        changes.push(Change::InputTick(ii, tick - 1));
                    }
                    if tick < i64::MAX as u64 {
                        changes.push(Change::InputTick(ii, tick + 1));
                    }
                }
                match input.get("phase").and_then(Value::as_str) {
                    Some("before_fighters") => {
                        changes.push(Change::InputPhase(ii, "after_fighters"))
                    }
                    Some("after_fighters") => {
                        changes.push(Change::InputPhase(ii, "before_fighters"))
                    }
                    Some(_) => return Err("unsupported input phase".into()),
                    None => return Err("input phase is required".into()),
                }
            }
        }

        // Declared stocks describe availability. Search may vary use policy
        // within that availability, but must never invent additional stock.
        if let Some(stock) = obj.get("holyHerbStock").and_then(Value::as_u64) {
            let uses = obj
                .get("holyHerbMaxUses")
                .and_then(Value::as_u64)
                .unwrap_or(0);
            if obj
                .get("holyHerbTriggerUnits")
                .and_then(Value::as_array)
                .is_some_and(|v| !v.is_empty())
            {
                for next in [
                    0,
                    stock.min(i64::MAX as u64),
                    uses.saturating_sub(1),
                    uses.saturating_add(1).min(stock),
                ] {
                    if next <= stock && next != uses {
                        changes.push(Change::HolyHerbUses(next));
                    }
                }
            }
        }
        if changes.is_empty() {
            return Err("scenario has no supported mutation options".into());
        }
        let operator_names = ["invocation-level", "equipment-level", "add-skill", "remove-skill",
            "replace-skill", "move-skill", "set-trigger", "input-tick", "input-phase", "herb-policy"];
        let mut family_totals = [0usize; 10];
        for change in &changes {
            let index = operator_names.iter().position(|name| *name == change.operator()).unwrap();
            family_totals[index] += 1;
        }
        let mut total_weight = 0.0;
        let weights = operator_names.iter().enumerate().map(|(index, name)| {
            // Every available mutation family competes on learned merit; the
            // number of concrete positions does not grant a larger family share.
            let weight = if family_totals[index] == 0 { 0.0 } else { self.operator_weight(name) };
            total_weight += weight;
            weight
        }).collect::<Vec<_>>();
        let mut draw = (self.next_u64() as f64 / u64::MAX as f64) * total_weight;
        let mut selected_family = 0usize;
        for (index, weight) in weights.iter().enumerate() {
            if *weight > 0.0 {
                if draw < *weight { selected_family = index; break; }
                draw -= *weight;
                selected_family = index;
            }
        }
        let family = operator_names[selected_family];
        let family_changes = changes.iter().filter(|change| change.operator() == family).collect::<Vec<_>>();
        let change = family_changes[self.pick(family_changes.len())].clone();
        match change {
            Change::Invocation(ui, si, next) => {
                child["ownUnits"][ui]["invocationLevels"][si] = Value::from(next);
            }
            Change::EquipmentLevel(ui, ei, level) => {
                child["ownUnits"][ui]["equipment"][ei]["level"] = Value::from(level);
            }
            Change::SkillAdd(ui, position, skill, trigger) => {
                child["ownUnits"][ui]["skills"].as_array_mut().ok_or("skills must be an array")?
                    .insert(position, Value::from(skill));
                child["ownUnits"][ui]["invocationLevels"].as_array_mut().ok_or("invocationLevels must be an array")?
                    .insert(position, Value::from(trigger));
            }
            Change::SkillRemove(ui, position) => {
                child["ownUnits"][ui]["skills"].as_array_mut().ok_or("skills must be an array")?.remove(position);
                child["ownUnits"][ui]["invocationLevels"].as_array_mut().ok_or("invocationLevels must be an array")?.remove(position);
            }
            Change::SkillReplace(ui, position, skill, trigger) => {
                child["ownUnits"][ui]["skills"][position] = Value::from(skill);
                child["ownUnits"][ui]["invocationLevels"][position] = Value::from(trigger);
            }
            Change::SkillMove(ui, from, to) => {
                let skill = child["ownUnits"][ui]["skills"].as_array_mut().ok_or("skills must be an array")?.remove(from);
                child["ownUnits"][ui]["skills"].as_array_mut().ok_or("skills must be an array")?.insert(to, skill);
                let trigger = child["ownUnits"][ui]["invocationLevels"].as_array_mut().ok_or("invocationLevels must be an array")?.remove(from);
                child["ownUnits"][ui]["invocationLevels"].as_array_mut().ok_or("invocationLevels must be an array")?.insert(to, trigger);
            }
            Change::SetTrigger(ui, position, trigger) => {
                child["ownUnits"][ui]["invocationLevels"][position] = Value::from(trigger);
            }
            Change::InputTick(ii, tick) => child["inputs"][ii]["tick"] = Value::from(tick),
            Change::InputPhase(ii, phase) => child["inputs"][ii]["phase"] = Value::from(phase),
            Change::HolyHerbUses(uses) => child["holyHerbMaxUses"] = Value::from(uses),
        }

        // Seeds belong to the trial bank, not candidate identity. Main supplies
        // those bank seeds for every candidate, so mutations preserve raw seeds.
        Ok((child, family.to_owned()))
    }

    /// Add one measured Earned result. Non-finite/negative values are rejected by
    /// ignoring them because the caller's simple API has no error channel.
    pub fn observe(&mut self, candidate: Value, earned: f64) {
        if !earned.is_finite() || earned < 0.0 {
            return;
        }
        let key = match serde_json::to_string(&candidate) {
            Ok(s) => s,
            Err(_) => return,
        };
        let sample_mean = {
            let sample = self.samples.entry(key.clone()).or_insert_with(|| Sample {
                candidate,
                earned: 0.0,
                count: 0,
            });
            sample.count = sample.count.saturating_add(1);
            sample.earned += earned;
            sample.earned / sample.count as f64
        };
        let replace = self
            .best_key
            .as_ref()
            .and_then(|k| self.samples.get(k))
            .map(|best| sample_mean > best.earned / best.count as f64)
            .unwrap_or(true);
        if replace {
            self.best_key = Some(key);
        }
    }

    pub fn best(&self) -> Option<&Value> {
        self.best_key
            .as_ref()
            .and_then(|key| self.samples.get(key))
            .map(|s| &s.candidate)
    }

    pub fn best_cloned(&self) -> Option<Value> {
        self.best().cloned()
    }
}

/// Encounter-local learning for a single process that schedules one global
/// worker pool. A legacy single-encounter run keeps the original Search state
/// and RNG byte-for-byte; mixed runs use one independently seeded Search per
/// encounter so reward scales never compete across encounters.
pub struct SearchPools {
    legacy_single: Option<Search>,
    by_encounter: BTreeMap<i64, Search>,
}

impl SearchPools {
    pub fn new(
        seed: u64,
        encounter_ids: &[i64],
        mixed: bool,
        checkpoint: Option<&Value>,
    ) -> Result<Self, String> {
        let mut unique = encounter_ids.to_vec();
        unique.sort_unstable();
        unique.dedup();
        if unique.is_empty() || unique.iter().any(|id| !(0..20).contains(id)) {
            return Err("search state requires encounter IDs in 0..19".into());
        }

        if !mixed {
            if unique.len() != 1 {
                return Err("legacy search state requires exactly one encounter".into());
            }
            let search = match checkpoint {
                Some(state) if state["schema"].as_str() == Some("ka-rust-search-state-v1") => {
                    Search::restore(state)?
                }
                Some(state) => {
                    let id = unique[0].to_string();
                    if state["schema"].as_str() != Some("ka-rust-search-pools-v1") {
                        return Err("incompatible search-state checkpoint schema".into());
                    }
                    let saved = state["byEncounter"]
                        .as_object()
                        .ok_or("search-pool checkpoint lacks byEncounter")?;
                    Search::restore(saved.get(&id).ok_or_else(|| {
                        format!("search-pool checkpoint lacks encounter {id}")
                    })?)?
                }
                None => Search::new(seed),
            };
            return Ok(Self {
                legacy_single: Some(search),
                by_encounter: BTreeMap::new(),
            });
        }

        let mut by_encounter = BTreeMap::new();
        match checkpoint {
            Some(state) if state["schema"].as_str() == Some("ka-rust-search-pools-v1") => {
                let saved = state["byEncounter"]
                    .as_object()
                    .ok_or("search-pool checkpoint lacks byEncounter")?;
                for (key, snapshot) in saved {
                    let encounter = key
                        .parse::<i64>()
                        .map_err(|_| format!("invalid encounter key in search state: {key}"))?;
                    if !(0..20).contains(&encounter) {
                        return Err(format!("search-state encounter outside 0..19: {encounter}"));
                    }
                    by_encounter.insert(encounter, Search::restore(snapshot)?);
                }
            }
            Some(state) if state["schema"].as_str() == Some("ka-rust-search-state-v1") => {
                if unique.len() != 1 {
                    return Err("a legacy single-encounter search state cannot seed a mixed run".into());
                }
                by_encounter.insert(unique[0], Search::restore(state)?);
            }
            Some(_) => return Err("incompatible search-state checkpoint schema".into()),
            None => {}
        }
        for encounter in unique {
            by_encounter
                .entry(encounter)
                .or_insert_with(|| Search::new(encounter_seed(seed, encounter)));
        }
        Ok(Self {
            legacy_single: None,
            by_encounter,
        })
    }

    pub fn mutate_unique_with_catalog(
        &mut self,
        encounter: i64,
        parent: &Value,
        catalog: &Value,
        seen: &mut HashSet<String>,
        attempts: usize,
    ) -> Result<(Value, String), String> {
        self.get_mut(encounter)?
            .mutate_unique_with_catalog(parent, catalog, seen, attempts)
    }

    pub fn mutate_fixed_unique(
        &mut self,
        encounter: i64,
        parent: &Value,
        seen: &mut HashSet<String>,
        attempts: usize,
    ) -> Result<(Value, String), String> {
        self.get_mut(encounter)?
            .mutate_fixed_unique(parent, seen, attempts)
    }

    pub fn observe(&mut self, encounter: i64, candidate: Value, earned: f64) -> Result<(), String> {
        self.get_mut(encounter)?.observe(candidate, earned);
        Ok(())
    }

    pub fn observe_operator(
        &mut self,
        encounter: i64,
        operator: &str,
        improved: bool,
    ) -> Result<(), String> {
        self.get_mut(encounter)?.observe_operator(operator, improved);
        Ok(())
    }

    /// Keep measurements for every currently retained parent, including
    /// encounters excluded by the mutable focus dispatch filter.
    pub fn retain_candidates(&mut self, candidates: &[(i64, Value)]) -> Result<(), String> {
        let mut grouped: BTreeMap<i64, Vec<Value>> = BTreeMap::new();
        for (encounter, candidate) in candidates {
            grouped.entry(*encounter).or_default().push(candidate.clone());
        }
        if let Some(search) = self.legacy_single.as_mut() {
            let candidates = grouped
                .into_values()
                .next()
                .ok_or("legacy search state cannot retain an empty parent set")?;
            search.retain_candidates(&candidates);
            return Ok(());
        }
        for (encounter, search) in &mut self.by_encounter {
            if let Some(candidates) = grouped.remove(encounter) {
                search.retain_candidates(&candidates);
            }
        }
        Ok(())
    }

    pub fn snapshot(&self) -> Value {
        if let Some(search) = &self.legacy_single {
            return search.snapshot();
        }
        let by_encounter = self
            .by_encounter
            .iter()
            .map(|(encounter, search)| (encounter.to_string(), search.snapshot()))
            .collect::<serde_json::Map<_, _>>();
        serde_json::json!({
            "schema": "ka-rust-search-pools-v1",
            "byEncounter": by_encounter,
        })
    }

    fn get_mut(&mut self, encounter: i64) -> Result<&mut Search, String> {
        if let Some(search) = self.legacy_single.as_mut() {
            return Ok(search);
        }
        self.by_encounter
            .get_mut(&encounter)
            .ok_or_else(|| format!("search state has no encounter {encounter}"))
    }
}

fn encounter_seed(seed: u64, encounter: i64) -> u64 {
    let mut value = seed ^ (encounter as u64).wrapping_add(0x9e37_79b9_7f4a_7c15);
    value = (value ^ (value >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
    value = (value ^ (value >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
    value ^ (value >> 31)
}
