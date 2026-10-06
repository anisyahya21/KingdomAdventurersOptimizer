use serde_json::{json, Map, Value};

const VERIFIED_BOUNDS_JSON: &str = include_str!(
    "../../../../../RE-evidence/20260922-search-contract/stat-bounds.json"
);
const MAX_TARGETS: usize = 4096;

#[derive(Clone, Copy)]
struct Axis {
    name: &'static str,
    label: &'static str,
    parameter: i32,
    bounded: bool,
    conditional_magic: bool,
}

#[derive(Clone, Copy)]
struct Bounds {
    minimum: i64,
    maximum: i64,
}

#[derive(Clone, Copy)]
struct ProbeAxis {
    name: &'static str,
    label: &'static str,
    parameter: Option<i32>,
    input: Option<&'static str>,
    conditional_magic: bool,
}

fn lookup_axis(name: &str) -> Option<Axis> {
    let (name, parameter, label, bounded, conditional_magic) = match name {
        "hp" => ("hp", 10, "HP", true, false),
        "mp" => ("mp", 11, "MP", true, false),
        "vig" => ("vig", 12, "Energy", true, false),
        "atk" => ("atk", 13, "Attack", false, false),
        "def" => ("def", 14, "Defence", false, false),
        "spd" => ("spd", 15, "Agility", false, false),
        "lck" => ("lck", 16, "Luck", false, false),
        "int" => ("int", 18, "Intelligence", false, true),
        "dex" => ("dex", 19, "Dexterity", false, false),
        _ => return None,
    };
    Some(Axis {
        name,
        label,
        parameter,
        bounded,
        conditional_magic,
    })
}

fn lookup_probe_axis(name: &str) -> Option<ProbeAxis> {
    if name == "herbs" {
        return Some(ProbeAxis {
            name: "herbs",
            label: "Holy Herb stock",
            parameter: None,
            input: Some("holyHerbStock"),
            conditional_magic: false,
        });
    }
    lookup_axis(name).map(|axis| ProbeAxis {
        name: axis.name,
        label: axis.label,
        parameter: Some(axis.parameter),
        input: None,
        conditional_magic: axis.conditional_magic,
    })
}

fn number_i64(value: &Value, label: &str) -> Result<i64, String> {
    value
        .as_i64()
        .or_else(|| value.as_u64().and_then(|v| i64::try_from(v).ok()))
        .ok_or_else(|| format!("{label} must be an integer in the signed 64-bit range"))
}

fn required<'a>(value: &'a Value, key: &str, context: &str) -> Result<&'a Value, String> {
    value
        .get(key)
        .ok_or_else(|| format!("{context} lacks {key}"))
}

fn own_units(parent: &Value) -> Result<&Vec<Value>, String> {
    parent
        .get("ownUnits")
        .and_then(Value::as_array)
        .ok_or_else(|| "scenario ownUnits must be an array".to_owned())
}

fn is_human(unit: &Value) -> bool {
    unit.get("human").and_then(Value::as_bool) == Some(true)
}

fn has_magic_attack(unit: &Value) -> bool {
    unit.get("skills")
        .and_then(Value::as_array)
        .is_some_and(|skills| {
            skills.iter().any(|skill| {
                skill
                    .as_i64()
                    .is_some_and(|id| (5..=19).contains(&id))
            })
        })
}

fn resolve_unit<'a>(
    parent: &'a Value,
    requested: Option<&Value>,
    axis: Axis,
) -> Result<(usize, &'a Value, String), String> {
    let units = own_units(parent)?;
    let index = if let Some(selector) = requested.filter(|v| !v.is_null()) {
        if let Some(name) = selector.as_str() {
            units
                .iter()
                .position(|unit| unit.get("name").and_then(Value::as_str) == Some(name))
                .ok_or_else(|| format!("unit {name:?} is not in this scenario"))?
        } else {
            let raw_index = number_i64(selector, "unit selector")?;
            let index = usize::try_from(raw_index)
                .map_err(|_| "unit index must be a nonnegative array index".to_owned())?;
            if index >= units.len() {
                return Err(format!("unit index {index} is outside ownUnits"));
            }
            index
        }
    } else {
        let eligible = |unit: &Value| {
            is_human(unit) && (!axis.conditional_magic || has_magic_attack(unit))
        };
        units
            .iter()
            .position(eligible)
            .or_else(|| {
                if axis.conditional_magic {
                    units.iter().position(is_human)
                } else {
                    None
                }
            })
            .ok_or_else(|| "scenario has no eligible human unit for this statistic".to_owned())?
    };
    let unit = &units[index];
    let name = unit
        .get("name")
        .and_then(Value::as_str)
        .ok_or_else(|| format!("ownUnits[{index}] lacks a string name"))?
        .to_owned();
    if !is_human(unit) {
        return Err(format!("unit {name:?} is not a human unit"));
    }
    if axis.conditional_magic && !has_magic_attack(unit) {
        return Err(format!(
            "unit {name:?} carries no magic attack skill, so Intelligence is not a combat input"
        ));
    }
    Ok((index, unit, name))
}

fn parameter_key(parameters: &Map<String, Value>, parameter_id: i32) -> Option<String> {
    parameters
        .keys()
        .find(|key| key.parse::<i32>().ok() == Some(parameter_id))
        .cloned()
}

fn parameter_input(unit: &Value, parameter_id: i32) -> Result<Value, String> {
    let parameters = unit
        .get("parameters")
        .and_then(Value::as_object)
        .ok_or_else(|| "unit parameters must be an object".to_owned())?;
    let key = parameter_key(parameters, parameter_id)
        .ok_or_else(|| format!("unit carries no parameter {parameter_id}"))?;
    Ok(parameters[&key].clone())
}

fn effective_stat(prepared: &Value, unit_name: &str, axis: Axis) -> Result<i64, String> {
    let units = prepared
        .get("ownPrepared")
        .and_then(Value::as_array)
        .ok_or_else(|| "canonical preparation omitted ownPrepared".to_owned())?;
    let unit = units
        .iter()
        .find(|unit| unit.get("name").and_then(Value::as_str) == Some(unit_name))
        .ok_or_else(|| format!("canonical preparation omitted unit {unit_name:?}"))?;
    let parameters = unit
        .get("effectiveParameters")
        .and_then(Value::as_object)
        .ok_or_else(|| "canonical preparation omitted effectiveParameters".to_owned())?;
    let key = parameter_key(parameters, axis.parameter)
        .ok_or_else(|| format!("prepared unit omitted parameter {}", axis.parameter))?;
    let prepared_parameter = &parameters[&key];
    let field = if axis.bounded { "maximum" } else { "value" };
    number_i64(
        required(prepared_parameter, field, "prepared parameter")?,
        "prepared effective parameter",
    )
}

fn prepared_parameter<'a>(
    prepared: &'a Value,
    unit_name: &str,
    parameter_id: i32,
) -> Result<&'a Value, String> {
    let units = prepared
        .get("ownPrepared")
        .and_then(Value::as_array)
        .ok_or_else(|| "canonical preparation omitted ownPrepared".to_owned())?;
    let unit = units
        .iter()
        .find(|unit| unit.get("name").and_then(Value::as_str) == Some(unit_name))
        .ok_or_else(|| format!("canonical preparation omitted unit {unit_name:?}"))?;
    let parameters = unit
        .get("effectiveParameters")
        .and_then(Value::as_object)
        .ok_or_else(|| "canonical preparation omitted effectiveParameters".to_owned())?;
    let key = parameter_key(parameters, parameter_id)
        .ok_or_else(|| format!("prepared unit omitted parameter {parameter_id}"))?;
    Ok(&parameters[&key])
}

fn raw_parameter_mut<'a>(
    scenario: &'a mut Value,
    unit_index: usize,
    parameter_id: i32,
) -> Result<&'a mut Value, String> {
    let unit = scenario
        .get_mut("ownUnits")
        .and_then(Value::as_array_mut)
        .and_then(|units| units.get_mut(unit_index))
        .ok_or_else(|| format!("ownUnits[{unit_index}] disappeared from cloned intent"))?;
    let parameters = unit
        .get_mut("parameters")
        .and_then(Value::as_object_mut)
        .ok_or_else(|| "unit parameters must be an object".to_owned())?;
    let key = parameters
        .keys()
        .find(|key| key.parse::<i32>().ok() == Some(parameter_id))
        .cloned()
        .ok_or_else(|| format!("unit carries no parameter {parameter_id}"))?;
    parameters
        .get_mut(&key)
        .ok_or_else(|| format!("unit parameter {parameter_id} disappeared"))
}

fn equipment_contribution(
    prepared: &Value,
    unit_name: &str,
    axis: Axis,
    original_parameter: &Value,
) -> Result<i32, String> {
    let raw_max = number_i64(required(original_parameter, "rawMax", "parameter")?, "rawMax")?;
    let raw_value = number_i64(
        required(original_parameter, "rawValue", "parameter")?,
        "rawValue",
    )?;
    let extra_max = number_i64(
        required(original_parameter, "extraMax", "parameter")?,
        "extraMax",
    )?;
    let extra_value = number_i64(
        required(original_parameter, "extraValue", "parameter")?,
        "extraValue",
    )?;
    let engine_parameter = prepared_parameter(prepared, unit_name, axis.parameter)?;
    let sentinel = i32::MAX as i64;
    let (prepared_value, input_value, input_extra) = if raw_max == sentinel {
        (
            number_i64(
                required(engine_parameter, "value", "prepared parameter")?,
                "prepared parameter value",
            )?,
            raw_value,
            extra_value,
        )
    } else {
        (
            number_i64(
                required(engine_parameter, "maximum", "prepared parameter")?,
                "prepared parameter maximum",
            )?,
            raw_max,
            extra_max,
        )
    };
    if raw_max == sentinel && (prepared_value == 0 || prepared_value == sentinel) {
        return Err(format!(
            "cannot recover parameter {} equipment contribution from a clamped baseline",
            axis.parameter
        ));
    }
    let contribution = prepared_value
        .checked_sub(input_value)
        .and_then(|value| value.checked_sub(input_extra))
        .ok_or_else(|| "equipment contribution arithmetic overflow".to_owned())?;
    i32::try_from(contribution)
        .map_err(|_| "equipment contribution exceeds the native signed 32-bit range".to_owned())
}

fn verified_bounds(axis: Axis) -> Option<Bounds> {
    let document: Value = serde_json::from_str(VERIFIED_BOUNDS_JSON).ok()?;
    if document.get("schema").and_then(Value::as_str) != Some("ka-search-stat-bounds-2") {
        return None;
    }
    let verification = document.get("verification")?;
    if verification.get("verified").and_then(Value::as_bool) != Some(true)
        || verification
            .get("mismatches")
            .and_then(Value::as_array)
            .map_or(true, |rows| !rows.is_empty())
    {
        return None;
    }
    let rows = document.get("stats")?.as_array()?;
    let row = rows.iter().find(|row| {
        row.get("parameter").and_then(Value::as_i64) == Some(axis.parameter as i64)
    })?;
    let minimum = number_i64(row.get("minimum")?, "stat bound minimum").ok()?;
    let maximum = number_i64(row.get("maximum")?, "stat bound maximum").ok()?;
    (minimum <= maximum).then_some(Bounds { minimum, maximum })
}

fn round_like_python(value: f64) -> i64 {
    let floor = value.floor();
    let fraction = value - floor;
    if fraction < 0.5 {
        floor as i64
    } else if fraction > 0.5 {
        (floor + 1.0) as i64
    } else if (floor as i64) % 2 == 0 {
        floor as i64
    } else {
        (floor + 1.0) as i64
    }
}

fn broad_targets(baseline: i64, bounds: Option<Bounds>, direction: &str) -> Vec<i64> {
    let mut values = Vec::new();
    let mut seen = Vec::new();
    let mut accept = |value: i64, required: bool| {
        if value == baseline || value < 0 || seen.contains(&value) {
            return;
        }
        if let Some(bounds) = bounds {
            if value < bounds.minimum || value > bounds.maximum {
                return;
            }
        }
        if direction == "down" && value > baseline {
            return;
        }
        if direction == "up" && value < baseline {
            return;
        }
        if !required {
            let gap = round_like_python((value.unsigned_abs() as f64) * 0.01).max(1);
            if seen.iter().any(|other| value.abs_diff(*other) < gap as u64) {
                return;
            }
        }
        seen.push(value);
        values.push(value);
    };
    if let Some(bounds) = bounds {
        accept(bounds.minimum, true);
        accept(bounds.maximum, true);
    }
    for rung in [1000, 2000, 4000] {
        accept(rung, true);
    }
    if bounds.is_some() {
        for fraction in [0.5, 0.91, 1.09, 2.0] {
            let value = round_like_python(baseline as f64 * fraction);
            accept(value, false);
        }
    }
    values.sort_unstable();
    values.truncate(9);
    values
}

fn direction(
    request: &Value,
    direction_key: &str,
    explicit_targets: &[i64],
    baseline: i64,
) -> Result<String, String> {
    if let Some(value) = request.get(direction_key).filter(|v| !v.is_null()) {
        let value = value
            .as_str()
            .ok_or_else(|| format!("{direction_key} must be a string"))?;
        return match value {
            "down" | "up" | "both" => Ok(value.to_owned()),
            _ => Err(format!("{direction_key} must be down, up or both")),
        };
    }
    let below = explicit_targets.iter().any(|value| *value < baseline);
    let above = explicit_targets.iter().any(|value| *value > baseline);
    Ok(if below && !above {
        "down"
    } else if above && !below {
        "up"
    } else {
        "both"
    }
    .to_owned())
}

fn requested_targets(
    request: &Value,
    targets_key: &str,
    alias_key: &str,
    direction_key: &str,
    baseline: i64,
    bounds: Option<Bounds>,
) -> Result<(Vec<i64>, String), String> {
    let supplied = request
        .get(targets_key)
        .filter(|value| !value.is_null())
        .or_else(|| request.get(alias_key).filter(|value| !value.is_null()));
    if let Some(value) = supplied {
        let rows = value
            .as_array()
            .ok_or_else(|| format!("{targets_key} must be an array of integers"))?;
        if rows.is_empty() {
            let direction = direction(request, direction_key, &[], baseline)?;
            return Ok((broad_targets(baseline, bounds, &direction), direction));
        }
        if rows.len() > MAX_TARGETS {
            return Err(format!(
                "{targets_key} exceeds the bounded limit of {MAX_TARGETS}"
            ));
        }
        let mut targets = rows
            .iter()
            .map(|value| number_i64(value, "target"))
            .collect::<Result<Vec<_>, _>>()?;
        targets.sort_unstable();
        targets.dedup();
        let direction = direction(request, direction_key, &targets, baseline)?;
        return Ok((targets, direction));
    }
    let direction = direction(request, direction_key, &[], baseline)?;
    Ok((broad_targets(baseline, bounds, &direction), direction))
}

fn parameter_summary(parameter: &Value) -> Result<Value, String> {
    Ok(json!({
        "rawValue": number_i64(required(parameter, "rawValue", "parameter")?, "rawValue")?,
        "rawMax": number_i64(required(parameter, "rawMax", "parameter")?, "rawMax")?,
        "extraValue": number_i64(required(parameter, "extraValue", "parameter")?, "extraValue")?,
        "extraMax": number_i64(required(parameter, "extraMax", "parameter")?, "extraMax")?
    }))
}

fn apply_effective_target(
    intent: &mut Value,
    unit_index: usize,
    axis: Axis,
    target: i64,
    contribution: i32,
) -> Result<(), String> {
    let entry = raw_parameter_mut(intent, unit_index, axis.parameter)?;
    entry["rawValue"] = json!(target);
    if axis.bounded {
        entry["rawMax"] = json!(target);
        entry["extraMax"] = json!(-(contribution as i64));
        entry["extraValue"] = json!(0);
    } else {
        entry["extraValue"] = json!(-(contribution as i64));
    }
    Ok(())
}

fn bounds_summary(bounds: Option<Bounds>) -> Value {
    bounds
        .map(|bound| {
            json!({
                "minimum":bound.minimum,
                "maximum":bound.maximum,
                "verified":true,
                "source":"RE-evidence/20260922-search-contract/stat-bounds.json"
            })
        })
        .unwrap_or_else(|| json!({"verified":false,"status":"unknown"}))
}

fn candidate_row(
    intent: Value,
    axis: Axis,
    axis2: Option<Axis>,
    unit_name: &str,
    target: i64,
    target2: Option<i64>,
    effective: Option<i64>,
    effective2: Option<i64>,
    validation_error: Option<String>,
) -> Value {
    let requested = if let Some(axis2) = axis2 {
        json!({"value":target,"value2":target2,"axis":axis.name,"axis2":axis2.name})
    } else {
        json!(target)
    };
    let effective_value = if let Some(axis2) = axis2 {
        json!({"value":effective,"value2":effective2,"axis":axis.name,"axis2":axis2.name})
    } else {
        effective.map_or(Value::Null, |value| json!(value))
    };
    let mutation_operator = axis2.map_or_else(
        || format!("fine-tune:stat-axis:{}", axis.name),
        |axis2| format!("fine-tune:stat-axis:{}+{}", axis.name, axis2.name),
    );
    let mut operator = json!({
        "kind":"stat-axis",
        "axis":axis.name,
        "unit":unit_name
    });
    if let Some(axis2) = axis2 {
        if let Some(object) = operator.as_object_mut() {
            object.insert("axis2".into(), json!(axis2.name));
        }
    }
    let target_reached = effective == Some(target)
        && axis2.map_or(target2.is_none(), |_| effective2 == target2);
    let admitted = validation_error.is_none();
    json!({
        "intent":intent,
        "mutationOperator":mutation_operator,
        "operator":operator,
        "requested":requested,
        "effective":effective_value,
        "validationError":validation_error,
        "admitted":admitted,
        "targetReached":target_reached
    })
}

fn probe_pivot_value(
    scenario: &Value,
    axis: ProbeAxis,
    unit_index: Option<usize>,
) -> Result<i64, String> {
    if let Some(parameter_id) = axis.parameter {
        let index = unit_index.ok_or_else(|| {
            format!("a human unit is required to probe {}", axis.label)
        })?;
        let unit = own_units(scenario)?
            .get(index)
            .ok_or_else(|| format!("ownUnits[{index}] is missing"))?;
        let parameter = parameter_input(unit, parameter_id)?;
        return match parameter.get("rawValue").filter(|value| !value.is_null()) {
            Some(value) => number_i64(value, "parameter rawValue"),
            None => Ok(0),
        };
    }
    let field = axis
        .input
        .ok_or_else(|| format!("probe axis {} has no raw input", axis.name))?;
    match scenario.get(field).filter(|value| !value.is_null()) {
        Some(value) => number_i64(value, field),
        None => Ok(0),
    }
}

fn probe_effective_stat(
    prepared: &Value,
    unit_name: Option<&str>,
    axis: ProbeAxis,
) -> Result<Option<i64>, String> {
    let Some(parameter_id) = axis.parameter else {
        return Ok(None);
    };
    let unit_name = unit_name.ok_or_else(|| {
        format!("canonical preparation needs a unit to read {}", axis.label)
    })?;
    let units = prepared
        .get("ownPrepared")
        .and_then(Value::as_array)
        .ok_or_else(|| "canonical preparation omitted ownPrepared".to_owned())?;
    let unit = units
        .iter()
        .find(|unit| unit.get("name").and_then(Value::as_str) == Some(unit_name))
        .ok_or_else(|| format!("canonical preparation omitted unit {unit_name:?}"))?;
    let parameters = unit
        .get("effectiveParameters")
        .and_then(Value::as_object)
        .ok_or_else(|| "canonical preparation omitted effectiveParameters".to_owned())?;
    let key = parameter_key(parameters, parameter_id)
        .ok_or_else(|| format!("prepared unit omitted parameter {parameter_id}"))?;
    let value = parameters[&key]
        .get("value")
        .ok_or_else(|| format!("prepared parameter {parameter_id} omitted value"))?;
    Ok(Some(number_i64(value, "prepared probe effective value")?))
}

fn apply_probe_axis(
    intent: &mut Value,
    axis: ProbeAxis,
    unit_index: Option<usize>,
    target: i64,
) -> Result<(), String> {
    if let Some(parameter_id) = axis.parameter {
        let index = unit_index.ok_or_else(|| {
            format!("a human unit is required to vary {}", axis.label)
        })?;
        let entry = raw_parameter_mut(intent, index, parameter_id)?;
        entry["rawValue"] = json!(target);
        if (10..=12).contains(&parameter_id) {
            if let Some(raw_max) = entry.get("rawMax").filter(|value| !value.is_null()) {
                let current = number_i64(raw_max, "parameter rawMax")?;
                if target > current {
                    entry["rawMax"] = json!(target);
                }
            }
        }
    } else {
        let field = axis
            .input
            .ok_or_else(|| format!("probe axis {} has no raw input", axis.name))?;
        intent[field] = json!(target);
    }
    Ok(())
}

fn probe_points(request: &Value, key: &str) -> Result<i64, String> {
    let Some(value) = request.get(key).filter(|value| !value.is_null()) else {
        return Ok(7);
    };
    let points = number_i64(value, key)?;
    if points > MAX_TARGETS as i64 {
        return Err(format!("{key} exceeds the bounded limit of {MAX_TARGETS}"));
    }
    Ok(points.max(0))
}

fn probe_default_ladder(pivot: i64, points: i64, axis_name: &str) -> Result<Vec<i64>, String> {
    if pivot <= 0 {
        return Err(format!(
            "the current {axis_name} value is {pivot}, which gives no ladder to probe"
        ));
    }
    if points == 1 {
        return Err("one default ladder point is invalid in the original probe schedule".to_owned());
    }
    let ratios: Vec<f64> = match points {
        3 => vec![0.6, 1.0, 1.4],
        5 => vec![0.5, 0.75, 1.0, 1.25, 1.5],
        7 => vec![0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6],
        n if n <= 0 => Vec::new(),
        n => (0..n)
            .map(|index| 0.4 + 1.2 * index as f64 / (n - 1) as f64)
            .collect(),
    };
    let mut ladder = vec![pivot];
    for ratio in ratios {
        let value = round_like_python(pivot as f64 * ratio).max(1);
        if !ladder.contains(&value) {
            ladder.push(value);
        }
    }
    ladder.sort_unstable();
    Ok(ladder)
}

fn probe_targets(
    request: &Value,
    target_key: &str,
    points_key: &str,
    pivot: i64,
    axis_name: &str,
) -> Result<Vec<i64>, String> {
    if let Some(value) = request.get(target_key).filter(|value| !value.is_null()) {
        let rows = value
            .as_array()
            .ok_or_else(|| format!("{target_key} must be an array of integers"))?;
        if rows.len() > MAX_TARGETS {
            return Err(format!(
                "{target_key} exceeds the bounded limit of {MAX_TARGETS}"
            ));
        }
        if !rows.is_empty() {
            let mut targets = rows
                .iter()
                .map(|value| number_i64(value, "probe target"))
                .collect::<Result<Vec<_>, _>>()?;
            targets.sort_unstable();
            targets.dedup();
            return Ok(targets);
        }
    }
    probe_default_ladder(pivot, probe_points(request, points_key)?, axis_name)
}

fn probe_candidate_row(
    intent: Value,
    axis: ProbeAxis,
    axis2: Option<ProbeAxis>,
    unit: &Value,
    target: i64,
    target2: Option<i64>,
    effective: Option<i64>,
    effective2: Option<i64>,
    target_reached: bool,
    validation_error: Option<String>,
) -> Value {
    let requested = if let Some(axis2) = axis2 {
        json!({"value":target,"value2":target2,"axis":axis.name,"axis2":axis2.name})
    } else {
        json!(target)
    };
    let actual_input = if target_reached {
        requested.clone()
    } else {
        Value::Null
    };
    let effective_value = if let Some(axis2) = axis2 {
        json!({"value":effective,"value2":effective2,"axis":axis.name,"axis2":axis2.name})
    } else {
        effective.map_or(Value::Null, |value| json!(value))
    };
    let effective_equals_requested = if axis.parameter.is_none()
        || axis2.is_some_and(|second| second.parameter.is_none())
        || validation_error.is_some()
    {
        Value::Null
    } else {
        json!(effective == Some(target) && axis2.map_or(true, |_| effective2 == target2))
    };
    let mutation_operator = axis2.map_or_else(
        || format!("probe:axis:{}", axis.name),
        |second| format!("probe:axis:{}+{}", axis.name, second.name),
    );
    let mut operator = json!({"kind":"probe","axis":axis.name,"unit":unit});
    if let Some(axis2) = axis2 {
        if let Some(object) = operator.as_object_mut() {
            object.insert("axis2".into(), json!(axis2.name));
        }
    }
    let admitted = validation_error.is_none();
    json!({
        "intent":intent,
        "mutationOperator":mutation_operator,
        "operator":operator,
        "requested":requested,
        "requestedDomain":"raw-input",
        "actualInput":actual_input,
        "effective":effective_value,
        "effectiveEqualsRequested":effective_equals_requested,
        "targetReached":target_reached,
        "validationError":validation_error,
        "admitted":admitted
    })
}

fn propose_stat_axis(parent: &Value, catalog: &Value, request: &Value) -> Result<Value, String> {
    if request.get("kind").and_then(Value::as_str) != Some("stat-axis") {
        return Err("tuning request kind must be stat-axis".to_owned());
    }
    let axis_name = required(request, "axis", "request")?
        .as_str()
        .ok_or_else(|| "axis must be a string".to_owned())?;
    let axis = lookup_axis(axis_name).ok_or_else(|| {
        format!(
            "unknown fine-tuning axis {axis_name:?}; combat axes are hp, mp, vig, atk, def, spd, lck, dex, int"
        )
    })?;
    let axis2 = request
        .get("axis2")
        .filter(|value| !value.is_null())
        .map(|value| {
            let name = value
                .as_str()
                .ok_or_else(|| "axis2 must be a string".to_owned())?;
            lookup_axis(name).ok_or_else(|| {
                format!(
                    "unknown interaction axis {name:?}; combat axes are hp, mp, vig, atk, def, spd, lck, dex, int"
                )
            })
        })
        .transpose()?;
    if axis2.is_some_and(|second| second.name == axis.name) {
        return Err("interaction axis must differ from the swept axis".to_owned());
    }
    if axis2.is_none()
        && ["targets2", "values2", "direction2"]
            .iter()
            .any(|key| request.get(key).is_some_and(|value| !value.is_null()))
    {
        return Err("axis2 is required when supplying targets2 or direction2".to_owned());
    }
    let unit_axis = Axis {
        conditional_magic: axis.conditional_magic
            || axis2.is_some_and(|second| second.conditional_magic),
        ..axis
    };
    let (unit_index, unit, unit_name) = resolve_unit(parent, request.get("unit"), unit_axis)?;
    let original_parameter = parameter_input(unit, axis.parameter)?;
    let input_summary = parameter_summary(&original_parameter)?;

    let parent_prepared = crate::prepare::prepare(parent, catalog)?;
    let baseline_effective = effective_stat(&parent_prepared, &unit_name, axis)?;
    let bounds = verified_bounds(axis);
    let (requested, direction) =
        requested_targets(request, "targets", "values", "direction", baseline_effective, bounds)?;

    let (axis2_input, axis2_baseline, axis2_bounds, axis2_targets, axis2_direction) =
        if let Some(axis2) = axis2 {
            let original = parameter_input(unit, axis2.parameter)?;
            let summary = parameter_summary(&original)?;
            let effective = effective_stat(&parent_prepared, &unit_name, axis2)?;
            let bounds = verified_bounds(axis2);
            let (targets, direction) = requested_targets(
                request,
                "targets2",
                "values2",
                "direction2",
                effective,
                bounds,
            )?;
            (Some((original, summary)), Some(effective), bounds, Some(targets), Some(direction))
        } else {
            (None, None, None, None, None)
        };

    let target_count = if let Some(targets2) = &axis2_targets {
        requested
            .len()
            .checked_mul(targets2.len())
            .ok_or_else(|| "two-axis target population overflows addressable size".to_owned())?
    } else {
        requested.len()
    };
    if target_count > MAX_TARGETS {
        return Err(format!(
            "proposal population {target_count} exceeds the bounded limit of {MAX_TARGETS}; no targets were dropped"
        ));
    }

    let mut baseline = json!({
        "axis": axis.name,
        "axisLabel": axis.label,
        "parameterId": axis.parameter,
        "unit": unit_name,
        "unitIndex": unit_index,
        "input": input_summary,
        "effective": baseline_effective,
        "bounds": bounds_summary(bounds)
    });
    if let (Some(axis2), Some((_, input2)), Some(effective2)) =
        (axis2, axis2_input.as_ref(), axis2_baseline)
    {
        if let Some(object) = baseline.as_object_mut() {
            object.insert("axis2".into(), json!(axis2.name));
            object.insert("axis2Label".into(), json!(axis2.label));
            object.insert("parameterId2".into(), json!(axis2.parameter));
            object.insert("input2".into(), input2.clone());
            object.insert("effective2".into(), json!(effective2));
            object.insert("bounds2".into(), bounds_summary(axis2_bounds));
        }
    }

    let mut candidates = Vec::with_capacity(target_count);
    for target in requested {
        let second_targets: Vec<Option<i64>> = axis2_targets.as_ref().map_or_else(
            || vec![None],
            |values| values.iter().copied().map(Some).collect(),
        );
        for target2 in second_targets.iter().copied() {
        let mut intent = parent.clone();
        let contribution = equipment_contribution(
            &parent_prepared,
            &unit_name,
            axis,
            &original_parameter,
        );
        let contribution2 = match (axis2, axis2_input.as_ref()) {
            (Some(axis2), Some((original, _))) => Some(equipment_contribution(
                &parent_prepared,
                &unit_name,
                axis2,
                original,
            )),
            _ => None,
        };
        let contribution = match contribution {
            Ok(value) => value,
            Err(error) => {
                candidates.push(candidate_row(
                    intent,
                    axis,
                    axis2,
                    &unit_name,
                    target,
                    target2,
                    None,
                    None,
                    Some(error),
                ));
                continue;
            }
        };
        if let Err(error) = apply_effective_target(
            &mut intent,
            unit_index,
            axis,
            target,
            contribution,
        ) {
            candidates.push(candidate_row(
                intent,
                axis,
                axis2,
                &unit_name,
                target,
                target2,
                None,
                None,
                Some(error),
            ));
            continue;
        }
        if let (Some(axis2), Some(target2), Some(result)) =
            (axis2, target2, contribution2.as_ref())
        {
            match result {
                Ok(value) => {
                    if let Err(error) = apply_effective_target(
                        &mut intent,
                        unit_index,
                        axis2,
                        target2,
                        *value,
                    ) {
                        candidates.push(candidate_row(
                            intent,
                            axis,
                            Some(axis2),
                            &unit_name,
                            target,
                            Some(target2),
                            None,
                            None,
                            Some(error),
                        ));
                        continue;
                    }
                }
                Err(error) => {
                    candidates.push(candidate_row(
                        intent,
                        axis,
                        Some(axis2),
                        &unit_name,
                        target,
                        Some(target2),
                        None,
                        None,
                        Some(error.clone()),
                    ));
                    continue;
                }
            }
        }

        match crate::prepare::prepare(&intent, catalog) {
            Ok(prepared) => {
                let effective = effective_stat(&prepared, &unit_name, axis);
                let effective2 = axis2.map(|second| effective_stat(&prepared, &unit_name, second));
                match (effective, effective2) {
                    (Ok(value), None) => candidates.push(candidate_row(
                        intent, axis, None, &unit_name, target, None, Some(value), None, None,
                    )),
                    (Ok(value), Some(Ok(value2))) => candidates.push(candidate_row(
                        intent,
                        axis,
                        axis2,
                        &unit_name,
                        target,
                        target2,
                        Some(value),
                        Some(value2),
                        None,
                    )),
                    (Err(error), _) | (_, Some(Err(error))) => candidates.push(candidate_row(
                        intent,
                        axis,
                        axis2,
                        &unit_name,
                        target,
                        target2,
                        None,
                        None,
                        Some(error),
                    )),
                }
            }
            Err(error) => candidates.push(candidate_row(
                intent,
                axis,
                axis2,
                &unit_name,
                target,
                target2,
                None,
                None,
                Some(error),
            )),
        }
        }
    }

    let bounds_provenance = if let Some(axis2) = axis2 {
        json!({
            "axis":axis.name,
            "bounds":bounds_summary(bounds),
            "axis2":axis2.name,
            "bounds2":bounds_summary(axis2_bounds)
        })
    } else if bounds.is_some() {
        json!("embedded verified stat-bounds.json")
    } else {
        json!("unknown; no verified bound for this axis")
    };
    let mut response = json!({
        "schema":"ka-rust-native-stat-axis-proposals-v1",
        "parentIntent":parent,
        "request":request,
        "baseline":baseline,
        "direction":direction,
        "boundsProvenance":bounds_provenance,
        "candidates":candidates,
        "evaluationPerformed":false
    });
    if let (Some(axis2), Some(direction2)) = (axis2, axis2_direction) {
        if let Some(object) = response.as_object_mut() {
            object.insert("axis2".into(), json!(axis2.name));
            object.insert("direction2".into(), json!(direction2));
        }
    }
    Ok(response)
}

fn propose_probe(parent: &Value, catalog: &Value, request: &Value) -> Result<Value, String> {
    let axis_name = required(request, "axis", "probe request")?
        .as_str()
        .ok_or_else(|| "axis must be a string".to_owned())?;
    let axis = lookup_probe_axis(axis_name).ok_or_else(|| {
        "unknown probe axis; strategy_probe.AXES supports atk, def, dex, hp, int, lck, mp, spd, vig, herbs"
            .to_owned()
    })?;
    let axis2 = request
        .get("axis2")
        .filter(|value| !value.is_null())
        .map(|value| {
            let name = value
                .as_str()
                .ok_or_else(|| "axis2 must be a string".to_owned())?;
            lookup_probe_axis(name).ok_or_else(|| {
                format!("unknown probe interaction axis {name:?}")
            })
        })
        .transpose()?;
    if axis2.is_some_and(|second| second.name == axis.name) {
        return Err("probe interaction axis must differ from the primary axis".to_owned());
    }
    if axis2.is_none()
        && ["targets2", "points2"]
            .iter()
            .any(|key| request.get(key).is_some_and(|value| !value.is_null()))
    {
        return Err("axis2 is required when supplying targets2 or points2".to_owned());
    }

    let unit_axis = axis
        .parameter
        .and_then(|_| lookup_axis(axis.name))
        .or_else(|| {
            axis2.and_then(|second| second.parameter.and_then(|_| lookup_axis(second.name)))
        })
        .map(|mut selected| {
            selected.conditional_magic = axis.conditional_magic
                || axis2.is_some_and(|second| second.conditional_magic);
            selected
        });
    let (unit_index, unit_name, unit_value) = if let Some(unit_axis) = unit_axis {
        let (index, _unit, name) = resolve_unit(parent, request.get("unit"), unit_axis)?;
        (Some(index), Some(name.clone()), json!(name))
    } else {
        (
            None,
            None,
            request
                .get("unit")
                .cloned()
                .unwrap_or(Value::Null),
        )
    };

    let parent_prepared = crate::prepare::prepare(parent, catalog)?;
    let pivot = probe_pivot_value(parent, axis, unit_index)?;
    let effective = probe_effective_stat(&parent_prepared, unit_name.as_deref(), axis)?;
    let parameter_input_summary = match axis.parameter {
        Some(parameter_id) => {
            let unit = own_units(parent)?
                .get(unit_index.ok_or("probe unit index missing")?)
                .ok_or("probe unit index outside ownUnits")?;
            Some(parameter_summary(&parameter_input(unit, parameter_id)?)?)
        }
        None => None,
    };
    let targets = probe_targets(request, "targets", "points", pivot, axis.name)?;

    let (pivot2, effective2, parameter_input_summary2, targets2) = if let Some(axis2) = axis2 {
        let pivot2 = probe_pivot_value(parent, axis2, unit_index)?;
        let effective2 = probe_effective_stat(&parent_prepared, unit_name.as_deref(), axis2)?;
        let summary = match axis2.parameter {
            Some(parameter_id) => {
                let unit = own_units(parent)?
                    .get(unit_index.ok_or("probe unit index missing")?)
                    .ok_or("probe unit index outside ownUnits")?;
                Some(parameter_summary(&parameter_input(unit, parameter_id)?)?)
            }
            None => None,
        };
        let targets2 = probe_targets(request, "targets2", "points2", pivot2, axis2.name)?;
        (Some(pivot2), Some(effective2), summary, Some(targets2))
    } else {
        (None, None, None, None)
    };

    let target_count = if let Some(targets2) = &targets2 {
        targets
            .len()
            .checked_mul(targets2.len())
            .ok_or_else(|| "two-axis probe population overflows addressable size".to_owned())?
    } else {
        targets.len()
    };
    if target_count > MAX_TARGETS {
        return Err(format!(
            "probe proposal population {target_count} exceeds the bounded limit of {MAX_TARGETS}; no targets were dropped"
        ));
    }

    let mut baseline = json!({
        "axis":axis.name,
        "axisLabel":axis.label,
        "axisInDefaultRotation":axis.name != "vig",
        "parameterId":axis.parameter,
        "inputField":axis.input,
        "unit":unit_value.clone(),
        "requestedDomain":"raw-input",
        "input":pivot,
        "rawInput":pivot,
        "effective":effective,
        "parameterInput":parameter_input_summary
    });
    if let Some(index) = unit_index {
        if let Some(object) = baseline.as_object_mut() {
            object.insert("unitIndex".into(), json!(index));
        }
    }
    if let (Some(axis2), Some(pivot2), Some(effective2)) = (axis2, pivot2, effective2) {
        if let Some(object) = baseline.as_object_mut() {
            object.insert("axis2".into(), json!(axis2.name));
            object.insert("axis2Label".into(), json!(axis2.label));
            object.insert("axis2InDefaultRotation".into(), json!(axis2.name != "vig"));
            object.insert("parameterId2".into(), json!(axis2.parameter));
            object.insert("inputField2".into(), json!(axis2.input));
            object.insert("input2".into(), json!(pivot2));
            object.insert("rawInput2".into(), json!(pivot2));
            object.insert("effective2".into(), json!(effective2));
            object.insert(
                "parameterInput2".into(),
                parameter_input_summary2.clone().unwrap_or(Value::Null),
            );
        }
    }

    let mut candidates = Vec::with_capacity(target_count);
    for target in targets.iter().copied() {
        let second_targets: Vec<Option<i64>> = targets2.as_ref().map_or_else(
            || vec![None],
            |values| values.iter().copied().map(Some).collect(),
        );
        for target2 in second_targets.iter().copied() {
            let mut intent = parent.clone();
            let mut target_reached = false;
            let applied = apply_probe_axis(&mut intent, axis, unit_index, target).and_then(|_| {
                if let (Some(axis2), Some(target2)) = (axis2, target2) {
                    apply_probe_axis(&mut intent, axis2, unit_index, target2)?;
                }
                Ok(())
            });
            match applied {
                Err(error) => candidates.push(probe_candidate_row(
                    intent,
                    axis,
                    axis2,
                    &unit_value,
                    target,
                    target2,
                    None,
                    None,
                    target_reached,
                    Some(error),
                )),
                Ok(()) => {
                    target_reached = true;
                    match crate::prepare::prepare(&intent, catalog) {
                        Ok(prepared) => {
                            let actual =
                                probe_effective_stat(&prepared, unit_name.as_deref(), axis);
                            let actual2 = axis2.map(|second| {
                                probe_effective_stat(&prepared, unit_name.as_deref(), second)
                            });
                            match (actual, actual2) {
                                (Ok(value), None) => candidates.push(probe_candidate_row(
                                    intent,
                                    axis,
                                    None,
                                    &unit_value,
                                    target,
                                    None,
                                    value,
                                    None,
                                    target_reached,
                                    None,
                                )),
                                (Ok(value), Some(Ok(value2))) => {
                                    candidates.push(probe_candidate_row(
                                        intent,
                                        axis,
                                        axis2,
                                        &unit_value,
                                        target,
                                        target2,
                                        value,
                                        value2,
                                        target_reached,
                                        None,
                                    ));
                                }
                                (Err(error), _) | (_, Some(Err(error))) => {
                                    candidates.push(probe_candidate_row(
                                        intent,
                                        axis,
                                        axis2,
                                        &unit_value,
                                        target,
                                        target2,
                                        None,
                                        None,
                                        target_reached,
                                        Some(error),
                                    ));
                                }
                            }
                        }
                        Err(error) => candidates.push(probe_candidate_row(
                            intent,
                            axis,
                            axis2,
                            &unit_value,
                            target,
                            target2,
                            None,
                            None,
                            target_reached,
                            Some(error),
                        )),
                    }
                }
            }
        }
    }

    Ok(json!({
        "schema":"ka-rust-native-probe-proposals-v1",
        "parentIntent":parent,
        "request":request,
        "axis":axis.name,
        "axis2":axis2.map(|second| second.name),
        "unit":unit_value,
        "baseline":baseline,
        "targets":targets,
        "targets2":targets2,
        "requestedDomain":"raw-input",
        "effectiveSource":"canonical prepare ownPrepared.effectiveParameters[].value; null for input axes",
        "candidates":candidates,
        "evaluationPerformed":false
    }))
}

pub fn propose(parent: &Value, catalog: &Value, request: &Value) -> Result<Value, String> {
    match request.get("kind").and_then(Value::as_str) {
        Some("stat-axis") => propose_stat_axis(parent, catalog, request),
        Some("probe") => propose_probe(parent, catalog, request),
        Some(other) => Err(format!("unsupported tuning request kind {other:?}")),
        None => Err("tuning request kind must be a string".to_owned()),
    }
}
