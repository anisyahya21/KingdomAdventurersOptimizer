//! Bounded raw-scenario preparation into a canonical fresh native engine snapshot.
//!
//! The returned wrapper carries team-ordered initialization callbacks and (for the
//! isolated-scene0 profile) sequential placement operations; the native engine applies
//! those through the same Rust decide/change implementation used during combat.

use serde_json::{json, Map, Value};
use std::collections::{HashMap, HashSet};

const HUMAN_TRAINING: [i32; 12] = [10, 11, 12, 13, 14, 15, 16, 18, 19, 20, 21, 22];
const MONSTER_PARAMS: [i32; 7] = [10, 11, 13, 14, 15, 16, 19];
// combat_consumables.recovery_parameters: bonusType -> (Parameter id, native target scope).
const RECOVERY_PARAMETERS: [i32; 6] = [10, 10, 11, 11, 12, 12];
const RECOVERY_ALL_RESIDENTS: [bool; 6] = [true, false, true, false, true, false];

#[derive(Clone)]
struct CapturedPlacement {
    cell: [i32; 2],
    offset: [f64; 3],
    board: Map<String, Value>,
    long_board: Map<String, Value>,
}

fn err<T>(s: impl Into<String>) -> Result<T, String> {
    Err(s.into())
}
fn obj<'a>(v: &'a Value, name: &str) -> Result<&'a Map<String, Value>, String> {
    v.as_object()
        .ok_or_else(|| format!("{name} must be an object"))
}
fn arr<'a>(v: &'a Value, name: &str) -> Result<&'a [Value], String> {
    v.as_array()
        .map(Vec::as_slice)
        .ok_or_else(|| format!("{name} must be an array"))
}
fn field<'a>(v: &'a Value, key: &str, name: &str) -> Result<&'a Value, String> {
    v.get(key).ok_or_else(|| format!("{name} missing {key}"))
}
fn i(v: &Value, name: &str) -> Result<i32, String> {
    let n = v
        .as_i64()
        .ok_or_else(|| format!("{name} must be an integer"))?;
    i32::try_from(n).map_err(|_| format!("{name} exceeds signed32"))
}
fn b(v: &Value, name: &str) -> Result<bool, String> {
    v.as_bool().ok_or_else(|| format!("{name} must be boolean"))
}
fn text<'a>(v: &'a Value, name: &str) -> Result<&'a str, String> {
    v.as_str().ok_or_else(|| format!("{name} must be a string"))
}
fn int_or_decimal_string(v: &Value, name: &str) -> Result<i32, String> {
    if let Some(n) = v.as_i64() {
        return i32::try_from(n).map_err(|_| format!("{name} exceeds signed32"));
    }
    if let Some(s) = v.as_str() {
        return s
            .parse::<i32>()
            .map_err(|_| format!("{name} is not a signed32 integer"));
    }
    err(format!("{name} must be an integer"))
}
fn div(v: i32, d: i32) -> i32 {
    (v as i64 / d as i64) as i32
}
fn f32r(v: f32) -> f32 {
    f32::from_bits(v.to_bits())
}
fn ease(low: i32, high: i32, steps: i32, progress: i32) -> i32 {
    if progress < 0 {
        return low;
    }
    if progress >= steps {
        return high;
    }
    let fraction = f32r(f32r(progress as f32) / f32r(steps as f32));
    let out = f32r(f32r(fraction * f32r((high.wrapping_sub(low)) as f32)) + f32r(low as f32));
    (out as i32).clamp(low.min(high), low.max(high))
}
fn lookup<'a>(rows: &'a [Value], id: i32, label: &str) -> Result<&'a Value, String> {
    rows.iter()
        .find(|r| r.get("id").and_then(Value::as_i64) == Some(id as i64))
        .ok_or_else(|| format!("unknown {label} id {id}"))
}
fn row_i(row: &Value, key: &str, label: &str) -> Result<i32, String> {
    i(field(row, key, label)?, &format!("{label}.{key}"))
}
fn parameter_key(params: &Value, pid: i32) -> Result<&Value, String> {
    params
        .get(pid.to_string())
        .ok_or_else(|| format!("missing raw/training parameter {pid}"))
}
fn pfield(p: &Value, key: &str, label: &str) -> Result<i32, String> {
    i(field(p, key, label)?, &format!("{label}.{key}"))
}
fn finite_triple(v: &Value, name: &str) -> Result<[f64; 3], String> {
    let values = arr(v, name)?;
    if values.len() != 3 {
        return err(format!("{name} must have three values"));
    }
    let mut out = [0.0; 3];
    for (index, value) in values.iter().enumerate() {
        let number = value
            .as_f64()
            .ok_or_else(|| format!("{name} values must be finite numbers"))?;
        if !number.is_finite() {
            return err(format!("{name} values must be finite numbers"));
        }
        out[index] = number;
    }
    Ok(out)
}
fn captured_board(v: &Value, name: &str) -> Result<Map<String, Value>, String> {
    let source = obj(v, name)?;
    let mut out = Map::new();
    for (key, value) in source {
        let index = key
            .parse::<i32>()
            .map_err(|_| format!("{name} keys must be signed32 integers"))?;
        out.insert(index.to_string(), json!(i(value, name)?));
    }
    Ok(out)
}

// System.Random's subtractive generator, including the exact 31-bit modulo used
// by combat_setup's random_below. Seed is an explicit scenario input.
struct LegacyRandom {
    values: [i32; 56],
    index: usize,
    partner: usize,
}
impl LegacyRandom {
    fn new(seed: i32) -> Self {
        let magnitude = if seed == i32::MIN {
            i32::MAX
        } else {
            seed.abs()
        };
        let mut previous = 161803398i32.wrapping_sub(magnitude);
        let mut values = [0i32; 56];
        values[55] = previous;
        let mut current = 1i32;
        let mut idx = 0usize;
        for _ in 0..54 {
            idx = (idx + 21) % 55;
            values[idx] = current;
            let mut difference = previous.wrapping_sub(current);
            if difference < 0 {
                difference = difference.wrapping_add(i32::MAX);
            }
            previous = current;
            current = difference;
        }
        for _ in 0..4 {
            for n in 1..56 {
                let mut x = values[n].wrapping_sub(values[1 + (n + 30) % 55]);
                if x < 0 {
                    x = x.wrapping_add(i32::MAX);
                }
                values[n] = x;
            }
        }
        Self {
            values,
            index: 0,
            partner: 21,
        }
    }
    fn next(&mut self) -> i32 {
        self.index = if self.index < 55 { self.index + 1 } else { 1 };
        self.partner = if self.partner < 55 {
            self.partner + 1
        } else {
            1
        };
        let mut x = self.values[self.index].wrapping_sub(self.values[self.partner]);
        if x == i32::MAX {
            x -= 1;
        } else if x < 0 {
            x = x.wrapping_add(i32::MAX);
        }
        self.values[self.index] = x;
        x
    }
    fn below(&mut self, limit: i32) -> i32 {
        if limit <= 0 {
            0
        } else {
            self.next().rem_euclid(limit)
        }
    }
}

fn monster_value(curve: &[Value], level: i32) -> Result<i32, String> {
    if curve.len() != 4 {
        return err("monster parameter curve must have four values");
    }
    let c = [
        i(&curve[0], "curve[0]")?,
        i(&curve[1], "curve[1]")?,
        i(&curve[2], "curve[2]")?,
        i(&curve[3], "curve[3]")?,
    ];
    let (lo, hi, step, span) = if level < 1 {
        return Ok(c[0]);
    } else if level > 10_000 {
        return Ok(c[3]);
    } else if level <= 100 {
        (c[0], c[1], level - 1, 99)
    } else if level <= 1_000 {
        (c[1], c[2], level - 100, 900)
    } else {
        (c[2], c[3], level - 1_000, 9_000)
    };
    Ok(lo.wrapping_add(div(hi.wrapping_sub(lo).wrapping_mul(step), span)))
}

fn form_value(member: &Value, priorities: &[Value]) -> Result<i32, String> {
    let mut category = member
        .get("formationValue")
        .filter(|v| !v.is_null())
        .map(|v| i(v, "formationValue"))
        .transpose()?
        .unwrap_or(2);
    if member.get("visitor").and_then(Value::as_bool) == Some(true) {
        category = 3;
    }
    if member.get("leaderIdentity").and_then(Value::as_bool) == Some(true) {
        category = 5;
    }
    let idx = usize::try_from(category).map_err(|_| "invalid formation category")?;
    let base = priorities
        .get(idx)
        .ok_or_else(|| format!("formation priority missing category {category}"))?;
    let mut p = i(base, "formation priority")?;
    if member.get("monster").and_then(Value::as_bool) == Some(true) {
        p += 1;
    }
    if member.get("ownerPlayer").and_then(Value::as_bool) == Some(true) {
        p += 2;
    }
    Ok(p)
}

fn supported_skill(row: &Value) -> Result<bool, String> {
    let typ = row_i(row, "type", "skill")?;
    let category = row_i(row, "category", "skill")?;
    let flags = row_i(row, "flags", "skill")?;
    Ok(if flags & 0x40000 != 0 {
        matches!(typ, 66 | 67) && category == 0
    } else if matches!(
        typ,
        0 | 1 | 2 | 11 | 15 | 18 | 19 | 20 | 21 | 22 | 23 | 24 | 26 | 27 | 60
    ) {
        true
    } else if typ == 48 {
        category == 2 && flags & 8 == 0
    } else {
        false
    })
}

fn formation(
    mut members: Vec<Value>,
    priorities: &[Value],
    team: i32,
    opponent_count: usize,
) -> Result<(Vec<Value>, Vec<usize>), String> {
    let mut keyed = Vec::with_capacity(members.len());
    for (index, mut m) in members.drain(..).enumerate() {
        let pr = form_value(&m, priorities)?;
        let defense = m
            .get("effectiveDefense")
            .and_then(Value::as_i64)
            .ok_or("member lacks integer effectiveDefense")?;
        keyed.push((pr, -defense, index, m));
    }
    keyed.sort_by_key(|(p, d, index, _)| (*p, *d, *index));
    let offset = 3usize.max(opponent_count / 5 + 1);
    let mut order = Vec::with_capacity(keyed.len());
    let mut out = vec![Value::Null; keyed.len()];
    for (grid, (_, _, incoming, mut m)) in keyed.into_iter().enumerate() {
        let row = grid / 5;
        let col = grid % 5;
        let y = if team == 0 {
            offset + 1 + row
        } else {
            offset.saturating_sub(row)
        };
        let cell = json!([col, y]);
        let priority = form_value(&m, priorities)?;
        let o = m.as_object_mut().ok_or("internal member shape")?;
        o.insert("incomingIndex".into(), json!(incoming));
        o.insert("priority".into(), json!(priority));
        o.insert("grid".into(), json!(grid));
        o.insert("row".into(), json!(row));
        o.insert("column".into(), json!(col));
        o.insert("cell".into(), cell);
        order.push(incoming);
        out[incoming] = m;
    }
    Ok((out, order))
}

/// Mirror `combat_scenario.load_scenario` + `combat_household_pets`: append complete, explicitly
/// supplied household results after the selected roster, in selected-owner/list order. This is the
/// recovered BattleForm.CreateBossBattle path (0x16a8fa0 -> MonsterSystem.SearchAllyMonstersInHouse
/// 0x15bede8); membership is supplied as data and is never inferred from a pet's stats or name.
fn expand_household_pets(raw: &Value, selected: &[Value]) -> Result<(Vec<Value>, Vec<String>), String> {
    let households = match raw.get("housePets") {
        None => Map::new(),
        Some(value) => obj(value, "housePets")?.clone(),
    };
    let materialized = match raw.get("householdOwners") {
        None => Vec::new(),
        Some(value) => arr(value, "householdOwners")?.to_vec(),
    };

    let mut selected_names = HashSet::new();
    let mut house_owners = Vec::new();
    for unit in selected {
        let name = text(field(unit, "name", "own unit")?, "own unit name")?.to_owned();
        selected_names.insert(name.clone());
        if unit.get("human").and_then(Value::as_bool) == Some(true)
            && unit.get("isHouseOwner").and_then(Value::as_bool) == Some(true)
        {
            house_owners.push(name);
        }
    }
    let house_owner_set: HashSet<String> = house_owners.iter().cloned().collect();

    let mut inline_owners = HashSet::<String>::new();
    for unit in selected {
        let Some(owner_value) = unit.get("petOwnerName") else {
            continue;
        };
        if owner_value.is_null() {
            continue;
        }
        let owner = text(owner_value, "petOwnerName")?;
        if !house_owner_set.contains(owner) {
            return err(format!("inline pet owner {owner:?} is not a selected human house owner"));
        }
        if unit.get("human").and_then(Value::as_bool) != Some(false)
            || !unit.get("monsterId").is_some_and(|value| !value.is_null())
        {
            return err(format!("inline pet for {owner:?} is not an ally monster"));
        }
        inline_owners.insert(owner.to_owned());
    }

    let mut materialized_owners = HashSet::<String>::new();
    for owner_value in &materialized {
        let owner = text(owner_value, "householdOwners entry")?;
        if !house_owner_set.contains(owner) {
            return err(format!("materialized household {owner:?} is not a selected human house owner"));
        }
        materialized_owners.insert(owner.to_owned());
    }

    for (owner, pets_value) in &households {
        if !selected_names.contains(owner) {
            return err(format!("pet owner {owner:?} is not a selected unit"));
        }
        if !house_owner_set.contains(owner.as_str()) {
            return err(format!("pet owner {owner:?} is not a selected human house owner"));
        }
        if inline_owners.contains(owner) {
            return err(format!("household {owner:?} has both inline pets and a housePets entry"));
        }
        let pets = arr(pets_value, "housePets owner entry")?;
        for pet in pets {
            if pet.get("human").and_then(Value::as_bool) != Some(false)
                || !pet.get("monsterId").is_some_and(|value| !value.is_null())
            {
                return err(format!("household entry for {owner:?} contains a non-ally-monster"));
            }
        }
    }

    let mut known_owners = inline_owners.clone();
    known_owners.extend(materialized_owners);
    known_owners.extend(households.keys().cloned());
    let missing: Vec<_> = house_owners
        .iter()
        .filter(|owner| !known_owners.contains(owner.as_str()))
        .cloned()
        .collect();
    if !missing.is_empty() {
        return err(format!(
            "selected human house owner(s) {} have no complete household declaration; supply housePets owner:[] when empty",
            missing.join(", ")
        ));
    }

    let mut expanded = selected.to_vec();
    for owner in &house_owners {
        if let Some(pets_value) = households.get(owner) {
            for pet in arr(pets_value, "housePets owner entry")? {
                let mut inline = obj(pet, "house pet")?.clone();
                inline.insert("petOwnerName".into(), json!(owner));
                expanded.push(Value::Object(inline));
            }
        }
    }
    let declared_owners = house_owners
        .into_iter()
        .filter(|owner| known_owners.contains(owner))
        .collect();
    Ok((expanded, declared_owners))
}

/// Convert a raw scenario and static recovered facts into a fresh ABI snapshot and
/// native initialization instructions. Catalog keys include `formationPriorities`,
/// `profiles`, `encounterTables`, `monsterTable`, `prizeCandidates`,
/// `animationResources`, `effectResources`, and `constants`.
pub fn prepare(raw: &Value, catalog: &Value) -> Result<Value, String> {
    if text(field(raw, "schema", "scenario")?, "schema")? != "ka-special-combat-research-1" {
        return err("unsupported scenario schema");
    }
    let finish_policy = raw
        .get("finishPolicy")
        .map(|policy| text(policy, "finishPolicy"))
        .transpose()?
        .unwrap_or("at-horizon");
    if !["at-horizon", "after-ending", "on-verdict"].contains(&finish_policy) {
        return err("finishPolicy must be at-horizon, after-ending or on-verdict");
    }
    let finish_policy_source = if raw.get("finishPolicy").is_some() {
        "scenario-intent"
    } else {
        "canonical-default"
    };
    let encounter_id = i(field(raw, "encounterId", "scenario")?, "encounterId")?;
    let defeat = i(field(raw, "defeatCount", "scenario")?, "defeatCount")?;
    let seed = i(field(raw, "libSeed", "scenario")?, "libSeed")?;
    let math_seed = i(field(raw, "mathSeed", "scenario")?, "mathSeed")?;
    let tick_limit = i(field(raw, "tickLimit", "scenario")?, "tickLimit")?;
    if !(0..20).contains(&encounter_id) || defeat < 0 || tick_limit < 1 {
        return err("invalid encounter, defeat count or tick limit");
    }
    if raw.get("prePlacement").is_some() && raw.get("startProfile").is_some() {
        return err("supply either prePlacement or startProfile, not both");
    }
    let profile = raw.get("startProfile");
    if let Some(profile) = profile {
        let p = obj(profile, "startProfile")?;
        if p.keys().any(|k| {
            ![
                "kind",
                "enemySpawnCell",
                "bossCell",
                "startingStatus",
                "note",
            ]
            .contains(&k.as_str())
        }) {
            return err("unknown startProfile field");
        }
        if text(field(profile, "kind", "startProfile")?, "startProfile.kind")? != "isolated-scene0"
        {
            return err("only isolated-scene0 startProfile is supported");
        }
    }
    let selected_own = arr(field(raw, "ownUnits", "scenario")?, "ownUnits")?;
    if selected_own.is_empty() {
        return err("explicit own roster required");
    }
    if selected_own.len() > 32 {
        return err("selected own roster exceeds native fighter capacity");
    }
    let raw_intent_own_count = selected_own.len();
    let (expanded_own, household_owners) = expand_household_pets(raw, selected_own)?;
    if expanded_own.len() > 32 {
        return err("own roster plus declared household pets exceeds native fighter capacity");
    }
    let own = expanded_own.as_slice();
    let empty_object = Map::new();
    let item_rows = match raw.get("items") {
        Some(value) => obj(value, "items")?,
        None => &empty_object,
    };
    if item_rows.len() > 8 {
        return err("battle item definitions exceed native capacity");
    }
    let item_stock = match raw.get("itemStock") {
        Some(value) => obj(value, "itemStock")?,
        None => &empty_object,
    };
    let mut native_items = Vec::with_capacity(item_rows.len());
    let mut item_slots = HashMap::with_capacity(item_rows.len());
    for (name, row) in item_rows {
        let category = i(field(row, "bonusCategory", "item row")?, "item.bonusCategory")?;
        let bonus_type = i(field(row, "bonusType", "item row")?, "item.bonusType")?;
        if category != 3 || !(0..6).contains(&bonus_type) {
            return err("unsupported item effect; only bonusCategory3 bonusType0..5 are recovered");
        }
        let slot = native_items.len();
        item_slots.insert(name.clone(), slot);
        let stock = item_stock
            .get(name)
            .map(|value| i(value, "itemStock count"))
            .transpose()?
            .unwrap_or(0);
        if stock < 0 {
            return err("itemStock counts must be nonnegative");
        }
        native_items.push(json!({
            "name":name,
            "parameter":RECOVERY_PARAMETERS[bonus_type as usize],
            "all_residents":RECOVERY_ALL_RESIDENTS[bonus_type as usize],
            "bonus_min":i(field(row,"bonusMinValue","item row")?,"item.bonusMinValue")?,
            "bonus_max":i(field(row,"bonusMaxValue","item row")?,"item.bonusMaxValue")?,
            "stock":stock
        }));
    }
    for count in item_stock.values() {
        if i(count, "itemStock count")? < 0 {
            return err("itemStock counts must be nonnegative");
        }
    }
    let raw_inputs = field(raw, "inputs", "scenario")?.clone();
    let input_rows = arr(&raw_inputs, "inputs")?;
    if input_rows.len() > 32 {
        return err("input schedule exceeds native capacity");
    }
    let mut native_inputs = Vec::with_capacity(input_rows.len());
    for input in input_rows {
        let kind = match text(field(input, "type", "input")?, "input.type")? {
            "holy_herb" => 0,
            "item" => {
                let item_name = text(field(input, "item", "input")?, "input.item")?;
                if !item_stock.contains_key(item_name) {
                    return err("item input needs an explicit finite stock entry");
                }
                if text(field(input, "target", "input")?, "input.target")? != "all" {
                    return err("battle item input target must be the explicit all-residents scope");
                }
                let slot = *item_slots
                    .get(item_name)
                    .ok_or("item input needs a declared item name")?;
                if !native_items[slot]["all_residents"].as_bool().unwrap_or(false) {
                    return err("single-resident recovery items cannot use the all-residents battle input");
                }
                let tick = i(field(input, "tick", "input")?, "input.tick")?;
                if tick < 0 {
                    return err("input tick must be nonnegative");
                }
                let phase = text(field(input, "phase", "input")?, "input.phase")?;
                if !["before_fighters", "after_fighters"].contains(&phase) {
                    return err("input phase is invalid");
                }
                native_inputs.push(json!({
                    "tick":tick,
                    "phase":if phase=="before_fighters" {0} else {1},
                    "kind":1,
                    "item":slot
                }));
                continue;
            }
            "finish" => return err("scheduled Finish input has no native battle input kind; use finishPolicy"),
            _ => return err("unsupported explicit input"),
        };
        let tick = i(field(input, "tick", "input")?, "input.tick")?;
        if tick < 0 {
            return err("input tick must be nonnegative");
        }
        let phase = text(field(input, "phase", "input")?, "input.phase")?;
        if !["before_fighters", "after_fighters"].contains(&phase) {
            return err("input phase is invalid");
        }
        native_inputs.push(json!({
            "tick":tick,
            "phase":if phase=="before_fighters" {0} else {1},
            "kind":kind,
            "item":-1
        }));
    }
    let herb_stock = i(field(raw, "holyHerbStock", "scenario")?, "holyHerbStock")?;
    let herb_max = raw
        .get("holyHerbMaxUses")
        .map(|v| i(v, "holyHerbMaxUses"))
        .transpose()?
        .unwrap_or(0);
    if herb_stock < 0 || herb_max < 0 || herb_max > herb_stock {
        return err("invalid Holy Herb stock/use cap");
    }
    let profiles = field(catalog, "profiles", "catalog")?;
    let skills = arr(
        field(profiles, "skills", "catalog.profiles")?,
        "catalog.profiles.skills",
    )?;
    let equipment = arr(
        field(profiles, "equipment", "catalog.profiles")?,
        "catalog.profiles.equipment",
    )?;
    let priorities = arr(
        field(catalog, "formationPriorities", "catalog")?,
        "catalog.formationPriorities",
    )?;
    if priorities.len() != 6 {
        return err("formationPriorities must provide all six recovered native categories");
    }
    let enc_table = field(catalog, "encounterTables", "catalog")?;
    let encounters = arr(
        field(enc_table, "encounters", "catalog.encounterTables")?,
        "catalog.encounterTables.encounters",
    )?;
    let monsters = arr(
        field(enc_table, "monsters", "catalog.encounterTables")?,
        "catalog.encounterTables.monsters",
    )?;
    let encounter = lookup(encounters, encounter_id, "encounter")?;
    let level = row_i(encounter, "levelField", "encounter")?.wrapping_add(div(defeat, 5));
    let mut rng = LegacyRandom::new(seed);
    let followers = arr(
        field(encounter, "followers", "encounter")?,
        "encounter.followers",
    )?;
    let mut enemy_members = Vec::with_capacity(followers.len() + 1);
    for (n, follower) in followers.iter().enumerate() {
        let mid = row_i(follower, "monsterId", "follower")?;
        let rate = row_i(follower, "checkRate", "follower")?;
        if !(0..=100).contains(&rate) {
            return err("follower checkRate outside 0..100");
        }
        let roll = rng.below(100);
        if roll < rate {
            enemy_members.push(make_monster(
                lookup(monsters, mid, "monster")?,
                mid,
                false,
                level,
                catalog,
            )?);
        }
        let _ = n;
    }
    let boss_id = row_i(encounter, "bossId", "encounter")?;
    enemy_members.push(make_monster(
        lookup(monsters, boss_id, "monster")?,
        boss_id,
        true,
        level,
        catalog,
    )?);
    let enemy_count = enemy_members.len();
    let (enemy_sorted, enemy_order) = formation(enemy_members, priorities, 1, enemy_count)?;

    let mut names = HashSet::new();
    let mut own_members = Vec::with_capacity(own.len());
    for (idx, unit) in own.iter().enumerate() {
        for key in [
            "name",
            "human",
            "monsterId",
            "parameters",
            "skills",
            "invocationLevels",
            "weaponId",
            "equipment",
            "visitor",
            "leaderIdentity",
        ] {
            if unit.get(key).is_none() {
                return err(format!("ownUnits[{idx}] lacks {key}"));
            }
        }
        let name = text(&unit["name"], "unit.name")?.to_owned();
        if !names.insert(name.clone()) {
            return err("unit names must be unique");
        }
        if unit.get("friend").and_then(Value::as_bool) == Some(true) {
            return err("friends are outside supported encounter scope");
        }
        let human_flags = i(
            unit.get("humanFlags").unwrap_or(&Value::from(0)),
            "humanFlags",
        )?;
        if unit.get("onVehicle").and_then(Value::as_bool) == Some(true) || human_flags & 4 != 0 {
            return err("vehicle state is unsupported");
        }
        if let Some(links) = unit.get("parameterLinks").filter(|value| !value.is_null()) {
            if !arr(links, "parameterLinks")?.is_empty() {
                return err("parameterLinks are unsupported by the native KaParams ABI: original ParameterComponent.GetValue/GetMaxValue reads entity extras, which this kernel snapshot cannot carry faithfully");
            }
        }
        let human = b(&unit["human"], "unit.human")?;
        let monster_id = if unit["monsterId"].is_null() {
            None
        } else {
            Some(i(&unit["monsterId"], "unit.monsterId")?)
        };
        if human == monster_id.is_some() {
            return err("unit must be exactly Human or Monster");
        }
        if !human && human_flags != 0 {
            return err("Monster cannot have Human flags");
        }
        for key in ["visitor", "leaderIdentity"] {
            b(&unit[key], &format!("unit.{key}"))?;
        }
        if let Some(owner) = unit.get("ownerPlayer") {
            b(owner, "unit.ownerPlayer")?;
        }
        let params = &unit["parameters"];
        let required: &[i32] = if human {
            &HUMAN_TRAINING
        } else {
            &MONSTER_PARAMS
        };
        for pid in required {
            let p = parameter_key(params, *pid)?;
            for k in [
                "rawValue",
                "rawMax",
                "extraValue",
                "extraMax",
                "trainingLevel",
            ] {
                pfield(p, k, &format!("unit {name} parameter {pid}"))?;
            }
        }
        let skill_ids = arr(&unit["skills"], "unit.skills")?;
        let levels = arr(&unit["invocationLevels"], "unit.invocationLevels")?;
        if skill_ids.len() != levels.len() {
            return err("every skill slot needs invocation setting");
        }
        let mut skill_rows = Vec::with_capacity(skill_ids.len());
        for (sidv, levv) in skill_ids.iter().zip(levels) {
            let sid = i(sidv, "skill id")?;
            let lev = i(levv, "invocation level")?;
            if !(0..=2).contains(&lev) {
                return err("invocation level must be 0, 1 or 2");
            }
            skill_rows.push(lookup(skills, sid, "skill")?);
        }
        if skill_rows.len() > 12 {
            return err(format!("unit {name} exceeds the native 12-skill capacity"));
        }
        for row in &skill_rows {
            if !supported_skill(row)? {
                return err(format!(
                    "skill {} is not in the recovered integrated effect routes",
                    row_i(row, "id", "skill")?
                ));
            }
        }
        if let Some(invoking) = unit.get("invokingSkills") {
            let rows = arr(invoking, "unit.invokingSkills")?;
            if rows.len() > 64 {
                return err("invokingSkills exceeds native capacity");
            }
            for row in rows {
                let pair = arr(row, "invokingSkills row")?;
                if pair.len() != 2 {
                    return err("invokingSkills rows must be [skillId,count]");
                }
                let sid = i(&pair[0], "invoking skill id")?;
                if i(&pair[1], "invoking skill count")? <= 0 {
                    return err("invoking skill count must be positive");
                }
                lookup(skills, sid, "invoking skill")?;
            }
        }
        let mut lifted = HashSet::new();
        for row in &skill_rows {
            if row_i(row, "type", "skill")? == 48 {
                lifted.insert(row_i(row, "value", "skill")?);
            }
        }
        let eq_input = arr(&unit["equipment"], "unit.equipment")?;
        if eq_input.len() > 8 {
            return err(format!(
                "unit {name} exceeds the native 8-equipment capacity"
            ));
        }
        let mut equip_rows = Vec::with_capacity(eq_input.len());
        for slot in eq_input {
            if obj(slot, "equipment slot")?.len() != 3 {
                return err("equipment slot requires exactly id, level, affinity");
            }
            let id = row_i(slot, "id", "equipment slot")?;
            let er = lookup(equipment, id, "equipment")?;
            let eq_level = row_i(slot, "level", "equipment slot")?;
            if eq_level < 1 {
                return err("equipment level must be positive");
            }
            let affinity = i(
                field(slot, "affinity", "equipment slot")?,
                "equipment affinity",
            )?;
            let typ = row_i(er, "type", "equipment")?;
            let actual_affinity = if (affinity == -1 || affinity == 0) && lifted.contains(&typ) {
                1
            } else {
                affinity
            };
            equip_rows.push((er, eq_level, actual_affinity));
        }
        let weapon_id = i(&unit["weaponId"], "weaponId")?;
        let weapon = lookup(equipment, weapon_id, "weapon")?;
        if row_i(weapon, "category", "weapon")? != 0 {
            return err("weaponId is not a weapon");
        }
        if weapon_id != 0
            && !eq_input
                .iter()
                .any(|v| v.get("id").and_then(Value::as_i64) == Some(weapon_id as i64))
        {
            return err("weapon must be included in equipment contributions");
        }
        let mut parameters = Map::new();
        let mut effective_defense = None;
        if obj(params, "unit.parameters")?.len() > 16 {
            return err("unit exceeds native 16-parameter capacity");
        }
        for (key, p) in obj(params, "unit.parameters")? {
            let pid = key
                .parse::<i32>()
                .map_err(|_| format!("invalid parameter key {key}"))?;
            let raw = pfield(p, "rawValue", "parameter")?;
            let raw_max = pfield(p, "rawMax", "parameter")?;
            let extra = pfield(p, "extraValue", "parameter")?;
            let extra_max = pfield(p, "extraMax", "parameter")?;
            let mut value = raw.wrapping_add(extra);
            let mut maximum = raw_max.wrapping_add(extra_max);
            if raw_max == i32::MAX {
                maximum = i32::MAX;
            } else {
                for (er, lv, aff) in &equip_rows {
                    let pairs = arr(
                        field(er, "parameters", "equipment")?,
                        "equipment.parameters",
                    )?;
                    let pi = pid - 10;
                    if pi >= 0 && (pi as usize) < pairs.len() && !pairs[pi as usize].is_null() {
                        let pair = arr(&pairs[pi as usize], "equipment parameter pair")?;
                        if pair.len() != 2 {
                            return err("equipment parameter pair needs base,growth");
                        }
                        let selected = if *lv > 0 {
                            *lv
                        } else {
                            row_i(er, "level", "equipment")?
                        };
                        let e = i(&pair[0], "equipment base")?.wrapping_add(
                            i(&pair[1], "equipment growth")?.wrapping_mul(selected - 1),
                        );
                        let contribution = if human && *aff == 0 {
                            if e > 0 {
                                (e / 2).max(1)
                            } else {
                                0
                            }
                        } else {
                            e
                        };
                        maximum = maximum.wrapping_add(contribution);
                    }
                }
            }
            if raw_max == i32::MAX {
                for (er, lv, aff) in &equip_rows {
                    let pairs = arr(
                        field(er, "parameters", "equipment")?,
                        "equipment.parameters",
                    )?;
                    let pi = pid - 10;
                    if pi >= 0 && (pi as usize) < pairs.len() && !pairs[pi as usize].is_null() {
                        let pair = arr(&pairs[pi as usize], "equipment parameter pair")?;
                        if pair.len() != 2 {
                            return err("equipment parameter pair needs base,growth");
                        }
                        let selected = if *lv > 0 {
                            *lv
                        } else {
                            row_i(er, "level", "equipment")?
                        };
                        let e = i(&pair[0], "equipment base")?.wrapping_add(
                            i(&pair[1], "equipment growth")?.wrapping_mul(selected - 1),
                        );
                        value = value.wrapping_add(if human && *aff == 0 {
                            if e > 0 {
                                (e / 2).max(1)
                            } else {
                                0
                            }
                        } else {
                            e
                        });
                    }
                }
            }
            if pid != 25 {
                value = value.min(maximum).max(0);
            }
            if pid == 14 {
                effective_defense = Some(value);
            }
            parameters.insert(key.clone(), json!({"value":value,"maximum":maximum}));
        }
        let training = if human {
            let mut total = 0i32;
            for pid in HUMAN_TRAINING {
                total = total.wrapping_add(pfield(
                    parameter_key(params, pid)?,
                    "trainingLevel",
                    "training",
                )?);
            }
            (div(total, 12)).max(1)
        } else {
            1
        };
        let formation_skill = skill_rows
            .iter()
            .find(|s| s.get("type").and_then(Value::as_i64) == Some(60))
            .map(|s| s.get("value").cloned().unwrap_or(Value::Null));
        let defense = effective_defense
            .ok_or_else(|| format!("unit {name} lacks effective defense parameter 14"))?;
        let mut mp_costs = Vec::with_capacity(skill_rows.len());
        for s in skill_rows {
            let min = row_i(s, "minMp", "skill")?;
            let max = row_i(s, "maxMp", "skill")?;
            let cost = if !human || (min == 0 && max == 0) {
                min
            } else {
                ease(min, max, 998, training.wrapping_sub(1))
            };
            mp_costs.push(json!({"skillId":row_i(s,"id","skill")?,"cost":cost}));
        }
        let mut simulation_source = unit.clone();
        if let Some(source_params) = simulation_source
            .get_mut("parameters")
            .and_then(Value::as_object_mut)
        {
            // combat_sandbox refills every own fighter to effective HP/MP maxima before
            // the sequential SharedControllers initialization.
            for pid in [10, 11] {
                let maximum = parameters
                    .get(&pid.to_string())
                    .and_then(|v| v.get("maximum"))
                    .and_then(Value::as_i64);
                if let (Some(p), Some(maximum)) = (source_params.get_mut(&pid.to_string()), maximum)
                {
                    if let Some(raw_value) = p.get("rawValue").and_then(Value::as_i64) {
                        let sum = (raw_value as i32).wrapping_add(maximum as i32);
                        let new_value = if maximum > 0 {
                            sum.clamp(0, maximum as i32)
                        } else {
                            sum
                        };
                        if let Some(map) = p.as_object_mut() {
                            map.insert("rawValue".into(), json!(new_value));
                        }
                    }
                }
            }
        }
        for pid in [10, 11] {
            if let Some(row) = parameters.get_mut(&pid.to_string()) {
                if let Some(maximum) = row.get("maximum").cloned() {
                    row["value"] = maximum;
                }
            }
        }
        let equipment_abi: Vec<Value> = equip_rows.iter().map(|(er, lv, affinity)| json!({
            "level":lv,"pvpLevel":er.get("pvpLevel").and_then(Value::as_i64).unwrap_or(0),
            "affinity":affinity,"parameters":er.get("parameters").cloned().unwrap_or_else(||json!([]))
        })).collect();
        let projectile_flag = match weapon.get("projectileFlag") {
            Some(Value::Bool(v)) => {
                if *v {
                    1
                } else {
                    0
                }
            }
            Some(v) => i(v, "weapon.projectileFlag")?,
            None => return err("weapon row lacks projectileFlag"),
        };
        if ![0, 1].contains(&projectile_flag) {
            return err("weapon.projectileFlag must be 0 or 1");
        }
        let weapon_abi = json!({"type":row_i(weapon,"type","weapon")?,"shootingRange":row_i(weapon,"shootingRange","weapon")?,"motion":row_i(weapon,"motion","weapon")?,"projectileFlag":projectile_flag});
        let (monster_type, monster_size) = if let Some(mid) = monster_id {
            let (t, s) = monster_facts(catalog, mid)?;
            (t, s)
        } else {
            (0, 0)
        };
        if let Some(source_object) = simulation_source.as_object_mut() {
            source_object.insert("monsterType".into(), json!(monster_type));
            source_object.insert("monsterSize".into(), json!(monster_size));
            source_object.insert("equipmentAbi".into(), json!(equipment_abi));
            source_object.insert("weaponAbi".into(), weapon_abi.clone());
        }
        own_members.push(json!({"name":name,"human":human,"monsterId":monster_id,"effectiveDefense":defense,"effectiveParameters":parameters,"averageTrainingLevel":training,"formationValue":formation_skill,"visitor":unit["visitor"],"leaderIdentity":unit["leaderIdentity"],"monster":!human,"ownerPlayer":unit.get("ownerPlayer").and_then(Value::as_bool).unwrap_or(false),"isHouseOwner":unit.get("isHouseOwner").cloned().unwrap_or(Value::Null),"petOwnerName":unit.get("petOwnerName").cloned().unwrap_or(Value::Null),"weaponId":weapon_id,"weaponRange":row_i(weapon,"shootingRange","weapon")?,"weaponType":row_i(weapon,"type","weapon")?,"weaponMotion":row_i(weapon,"motion","weapon")?,"weaponProjectileFlag":weapon_abi["projectileFlag"],"skillIds":unit["skills"],"skills":unit["skills"],"invocationLevels":unit["invocationLevels"],"levels":unit["invocationLevels"],"equipment":unit["equipment"],"equipmentAbi":equipment_abi,"weaponAbi":weapon_abi,"parameters":simulation_source["parameters"],"humanFlags":unit.get("humanFlags").cloned().unwrap_or(json!(0)),"invokingSkills":unit.get("invokingSkills").cloned().unwrap_or(json!([])),"simulationSource":simulation_source,"skillCosts":mp_costs,"sourceIndex":idx,"monsterType":monster_type,"monsterSize":monster_size}));
    }
    let (own_sorted, own_order) = formation(own_members, priorities, 0, enemy_sorted.len())?;
    if own_sorted.len() + enemy_sorted.len() > 32 {
        return err("combined roster exceeds native 32 fighter capacity");
    }
    let mut all_names = HashSet::new();
    for unit in &own_sorted {
        all_names.insert(
            unit["name"]
                .as_str()
                .ok_or("own unit name missing")?
                .to_owned(),
        );
    }
    for unit in &enemy_sorted {
        let idx = unit["incomingIndex"]
            .as_u64()
            .ok_or("enemy incomingIndex missing")?;
        let mid = unit["monsterId"]
            .as_i64()
            .ok_or("enemy monsterId missing")?;
        all_names.insert(format!("enemy:{idx}:{mid}"));
    }
    let captured_placements = if let Some(value) = raw.get("prePlacement") {
        let entries = obj(value, "prePlacement")?;
        if entries.is_empty() {
            return err("prePlacement must explicitly cover all fighters");
        }
        for name in entries.keys() {
            if !all_names.contains(name) {
                return err(format!("prePlacement names unknown fighter {name:?}"));
            }
        }
        if entries.len() != all_names.len() {
            return err("prePlacement must name every own and spawned enemy exactly once");
        }
        let mut captured = HashMap::with_capacity(entries.len());
        for name in &all_names {
            let value = entries
                .get(name)
                .ok_or_else(|| format!("prePlacement is missing fighter {name:?}"))?;
            let row = obj(value, "prePlacement fighter")?;
            if row.len() != 5
                || ["cell", "position", "offset", "board", "longBoard"]
                    .iter()
                    .any(|key| !row.contains_key(*key))
            {
                return err(
                    "prePlacement requires exactly cell, position, offset, board and longBoard",
                );
            }
            let cell = pair(field(value, "cell", "prePlacement fighter")?, "prePlacement.cell")?;
            let _position = finite_triple(
                field(value, "position", "prePlacement fighter")?,
                "prePlacement.position",
            )?;
            let offset = finite_triple(
                field(value, "offset", "prePlacement fighter")?,
                "prePlacement.offset",
            )?;
            let board = captured_board(
                field(value, "board", "prePlacement fighter")?,
                "prePlacement.board",
            )?;
            let long_board = captured_board(
                field(value, "longBoard", "prePlacement fighter")?,
                "prePlacement.longBoard",
            )?;
            if ["4", "5", "6", "7", "8"]
                .iter()
                .any(|key| !board.contains_key(*key))
            {
                return err("prePlacement board must supply keys 4/5/6/7/8");
            }
            let status_fields = ["62", "63", "64"]
                .iter()
                .filter(|key| board.contains_key(**key))
                .count();
            if status_fields != 0 && status_fields != 3 {
                return err(format!(
                    "prePlacement {name} requires all starting status fields 62/63/64"
                ));
            }
            if status_fields == 3 {
                let skill_id = i(&board["62"], "prePlacement status skill")?;
                if i(&board["63"], "prePlacement status turns")? <= 0 {
                    return err(format!(
                        "prePlacement {name} starting status turns must be positive"
                    ));
                }
                let status_type = row_i(
                    lookup(skills, skill_id, "prePlacement status skill")?,
                    "type",
                    "prePlacement status skill",
                )?;
                if status_type != 66 && status_type != 67 {
                    return err(format!(
                        "prePlacement {name} has unsupported starting status skill {skill_id}"
                    ));
                }
            }
            captured.insert(
                name.clone(),
                CapturedPlacement {
                    cell,
                    offset,
                    board,
                    long_board,
                },
            );
        }
        captured
    } else {
        HashMap::new()
    };
    let profile_value = profile.unwrap_or(&Value::Null);
    let spawn = if profile.is_some() {
        profile_cell(profile_value, "enemySpawnCell", [0, 0])?
    } else {
        [0, 0]
    };
    let boss_cell = if profile.is_some() {
        profile_cell(profile_value, "bossCell", spawn)?
    } else {
        spawn
    };
    let statuses = profile_value
        .get("startingStatus")
        .cloned()
        .unwrap_or_else(|| json!({}));
    let statuses = obj(&statuses, "startProfile.startingStatus")?;
    for (name, entry) in statuses {
        if !all_names.contains(name) {
            return err(format!("startingStatus names unknown fighter {name}"));
        }
        let pair = arr(entry, "startingStatus entry")?;
        if pair.len() != 2 {
            return err("startingStatus entries need skillId and turns");
        }
        let sid = i(&pair[0], "starting skill id")?;
        let turns = i(&pair[1], "starting status turns")?;
        if turns <= 0 {
            return err("starting status turns must be positive");
        }
        if row_i(
            lookup(skills, sid, "starting status skill")?,
            "type",
            "starting status skill",
        )? != 66
            && row_i(
                lookup(skills, sid, "starting status skill")?,
                "type",
                "starting status skill",
            )? != 67
        {
            return err("only defense-down/sleep starting status is supported");
        }
    }
    let mut units = Vec::with_capacity(own_sorted.len() + enemy_sorted.len());
    let mut placement_groups = vec![Vec::<Value>::new(), Vec::<Value>::new()];
    let mut initialization_orders = vec![Vec::<i32>::new(), Vec::<i32>::new()];
    for team in 0..2 {
        let roster = if team == 0 {
            &own_sorted
        } else {
            &enemy_sorted
        };
        let order = if team == 0 { &own_order } else { &enemy_order };
        let base = 100i32
            + if team == 0 {
                0
            } else {
                own_sorted.len() as i32
            };
        for (incoming, member) in roster.iter().enumerate() {
            let identity = base + incoming as i32;
            let is_boss = team == 1 && member["leaderIdentity"].as_bool().unwrap_or(false);
            let name = if team == 0 {
                member["name"]
                    .as_str()
                    .ok_or("unit name missing")?
                    .to_owned()
            } else {
                format!(
                    "enemy:{}:{}",
                    member["incomingIndex"]
                        .as_u64()
                        .ok_or("enemy incomingIndex missing")?,
                    member["monsterId"]
                        .as_i64()
                        .ok_or("monsterId missing")?
                )
            };
            let captured = captured_placements.get(&name);
            let source_cell = if profile.is_some() {
                if team == 0 {
                    [0, 0]
                } else if is_boss {
                    boss_cell
                } else {
                    spawn
                }
            } else if let Some(captured) = captured {
                captured.cell
            } else {
                pair(&member["cell"], "prepared cell")?
            };
            let mut board = captured
                .map(|placement| placement.board.clone())
                .unwrap_or_default();
            let source_state = if profile.is_some() { 0 } else { 1 };
            for (k, v) in [
                (4, 0),
                (5, source_state),
                (6, team as i32),
                (
                    7,
                    if profile.is_some() {
                        0
                    } else {
                        member["grid"].as_i64().unwrap_or(0) as i32
                    },
                ),
                (8, 0),
            ] {
                board.insert(k.to_string(), json!(v));
            }
            if team == 1 && profile.is_some() {
                board.insert("19".into(), json!(source_cell[0]));
                board.insert("20".into(), json!(source_cell[1]));
            }
            if let Some(status) = statuses.get(&name) {
                let e = arr(status, "startingStatus entry")?;
                board.insert("62".into(), e[0].clone());
                board.insert("63".into(), e[1].clone());
                board.insert("64".into(), json!(0));
            }
            let mut source = member.clone();
            if team == 1 {
                if let Some(m) = source.as_object_mut() {
                    m.insert("humanFlags".into(), json!(0));
                    m.insert("longBoard".into(), json!({}));
                    m.insert("equipmentAbi".into(), json!([]));
                    m.insert(
                        "weaponAbi".into(),
                        json!({"type":0,"shootingRange":1,"motion":4,"projectileFlag":0}),
                    );
                    m.insert("monsterId".into(), member["monsterId"].clone());
                }
            }
            units.push(native_unit(
                identity,
                team as i32,
                member,
                &source,
                source_cell,
                board,
                captured.map(|placement| placement.offset).unwrap_or([0.0; 3]),
                captured
                    .map(|placement| Value::Object(placement.long_board.clone()))
                    .unwrap_or_else(|| {
                        source
                            .get("longBoard")
                            .cloned()
                            .unwrap_or_else(|| json!({}))
                    }),
            )?);
        }
        for incoming in order {
            let idx = usize::try_from(*incoming).map_err(|_| "negative formation index")?;
            let member = roster.get(idx).ok_or("formation index outside roster")?;
            let identity = base + idx as i32;
            let final_cell = pair(&member["cell"], "prepared cell")?;
            let name = if team == 0 {
                member["name"]
                    .as_str()
                    .ok_or("unit name missing")?
                    .to_owned()
            } else {
                format!(
                    "enemy:{}:{}",
                    member["incomingIndex"]
                        .as_u64()
                        .ok_or("enemy incomingIndex missing")?,
                    member["monsterId"]
                        .as_i64()
                        .ok_or("monsterId missing")?
                )
            };
            let captured = captured_placements.get(&name);
            let source_cell = if profile.is_some() {
                if team == 0 {
                    [0, 0]
                } else if member["leaderIdentity"].as_bool().unwrap_or(false) {
                    boss_cell
                } else {
                    spawn
                }
            } else if let Some(captured) = captured {
                captured.cell
            } else {
                final_cell
            };
            let source_offset = captured
                .map(|placement| placement.offset)
                .unwrap_or([0.0; 3]);
            let step = json!({"identity":identity,"team":team,"sourceCell":source_cell,"sourcePosition":[source_cell[0] as f64*24.0,0.0,source_cell[1] as f64*24.0,0.0,0.0,0.0,null],"sourceOffset":source_offset,"cell":final_cell,"grid":member["grid"],"postPlacementState":1});
            if profile.is_some() || captured.is_some() {
                placement_groups[team].push(step);
            }
            initialization_orders[team].push(identity);
        }
    }
    let mut bucket_rows = Vec::<(i32, Vec<i32>)>::new();
    for unit in &units {
        let cell = pair(&unit["components"]["cell"], "snapshot cell")?;
        let key = key_for_cell(cell, 8);
        if let Some((_, ids)) = bucket_rows.iter_mut().find(|(k, _)| *k == key) {
            ids.push(unit["identity"].as_i64().ok_or("identity missing")? as i32);
        } else {
            bucket_rows.push((
                key,
                vec![unit["identity"].as_i64().ok_or("identity missing")? as i32],
            ));
        }
    }
    let roster_ids: Vec<i32> = units
        .iter()
        .map(|u| u["identity"].as_i64().unwrap_or(-1) as i32)
        .collect();
    let subsets = json!([
        subset(&[]),
        subset(&roster_ids),
        subset(&roster_ids),
        subset(&[]),
        subset(&roster_ids),
        subset(&[]),
        subset(&[]),
        subset(&roster_ids),
        subset(&roster_ids),
        subset(&roster_ids),
        subset(&roster_ids)
    ]);
    let mut needed = HashSet::<i32>::from([1, 2]);
    for unit in &units {
        for sid in arr(&unit["skills"], "snapshot skills")? {
            let id = i(sid, "skill id")?;
            let row = lookup(skills, id, "skill ABI row")?;
            if !supported_skill(row)? {
                return err(format!(
                    "skill {id} is not in the recovered integrated effect routes"
                ));
            }
            needed.insert(id);
        }
        if let Some(sid) = unit["board"].get("62") {
            needed.insert(i(sid, "starting status skill")?);
        }
    }
    let mut skill_table = Map::new();
    for sid in needed {
        let row = lookup(skills, sid, "skill ABI row")?;
        for key in [
            "category",
            "type",
            "flags",
            "minMp",
            "maxMp",
            "requiredEquipType",
            "shootingRange",
            "range",
            "count",
            "motion",
            "value",
            "seb",
            "img",
            "impactImg",
            "impactSeb",
        ] {
            if row.get(key).is_none() {
                return err(format!("skill row {sid} lacks ABI field {key}"));
            }
        }
        skill_table.insert(sid.to_string(), row.clone());
    }
    let effect_data = field(catalog, "effectResources", "catalog")?;
    let effect_list = arr(
        field(effect_data, "resources", "effectResources")?,
        "effectResources.resources",
    )?;
    if effect_list.len() > 48 {
        return err("effect resource list exceeds native capacity");
    }
    let mut effect_resources = Map::new();
    for row in effect_list {
        effect_resources.insert(
            row_i(row, "id", "effect resource")?.to_string(),
            json!(row_i(row, "maxFrame", "effect resource")?),
        );
    }
    let anim_data = field(catalog, "animationResources", "catalog")?;
    let anim_groups = field(anim_data, "resources", "animationResources")?;
    let mut animation_resources = Vec::new();
    for (resource, key) in [(11, "chara"), (22, "monster")] {
        for row in arr(field(anim_groups, key, "animation resources")?, key)? {
            animation_resources.push(json!([
                resource,
                row_i(row, "id", "animation resource")?,
                row_i(row, "maxFrame", "animation resource")?,
                0
            ]));
        }
    }
    if animation_resources.len() > 512 {
        return err("animation resources exceed native capacity");
    }
    let human_bases = field(
        field(
            field(catalog, "constants", "catalog")?,
            "humanAnimationSebBases",
            "constants",
        )?,
        "values",
        "humanAnimationSebBases",
    )?
    .clone();
    if arr(&human_bases, "human animation bases")?.len() > 48 {
        return err("human animation bases exceed native capacity");
    }
    let prizes_obj = field(catalog, "prizeCandidates", "catalog")?;
    let prizes = prizes_obj
        .get(encounter_id.to_string())
        .ok_or("prize candidates missing encounter")?
        .clone();
    if arr(&prizes, "prize candidates")?.len() > 16 {
        return err("prize candidates exceed native capacity");
    }
    for key in ["mpWatchUnits", "holyHerbTriggerUnits"] {
        if raw.get(key).is_some_and(|v| !v.is_array()) {
            return err(format!("{key} must be an array of own-unit names"));
        }
    }
    let watch_names = raw
        .get("mpWatchUnits")
        .and_then(Value::as_array)
        .filter(|v| !v.is_empty())
        .cloned()
        .or_else(|| {
            raw.get("holyHerbTriggerUnits")
                .and_then(Value::as_array)
                .filter(|v| !v.is_empty())
                .cloned()
        })
        .unwrap_or_default();
    if watch_names.len() > 2 {
        return err("MP watch is limited to two units");
    }
    let triggers = raw
        .get("holyHerbTriggerUnits")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    if triggers.len() > 2 || (herb_max > 0 && triggers.is_empty()) {
        return err("Holy Herb uses require one or two explicit trigger units");
    }
    if herb_max > 0 && herb_max > 16 {
        return err("Holy Herb use cap exceeds native capacity");
    }
    let mut watch_ids = Vec::new();
    for name in &watch_names {
        let s = text(name, "MP watch unit")?;
        let idx = own
            .iter()
            .position(|u| u.get("name").and_then(Value::as_str) == Some(s))
            .ok_or_else(|| format!("MP watch names unknown own unit {s}"))?;
        if !own[idx]["human"].as_bool().unwrap_or(false) {
            return err("MP watch must identify human residents");
        }
        let identity = 100 + idx as i32;
        if watch_ids.contains(&identity) {
            return err("MP watch units must be unique");
        }
        watch_ids.push(identity);
    }
    let mut trigger_seen = HashSet::new();
    for name in &triggers {
        let s = text(name, "Holy Herb trigger unit")?;
        let idx = own
            .iter()
            .position(|u| u.get("name").and_then(Value::as_str) == Some(s))
            .ok_or_else(|| format!("Holy Herb trigger names unknown own unit {s}"))?;
        if !own[idx]["human"].as_bool().unwrap_or(false) {
            return err("Holy Herb triggers must identify human residents");
        }
        if !trigger_seen.insert(idx) {
            return err("Holy Herb trigger units must be unique");
        }
    }
    if watch_ids.len() > 2 {
        return err("MP watch exceeds native capacity");
    }
    let consumables = json!({"holy_herb_stock":herb_stock,"holy_herb_max_uses":herb_max,"mp_watch":watch_ids,"items":native_items,"inputs":native_inputs,"uses":[]});
    let row_offset = 3usize.max(enemy_sorted.len() / 5 + 1);
    let snapshot = json!({
        "config":{"tick":-1,"first_identity":100,"next_identity":100+units.len(),"next_command":0,"map_width":8,"row_offset":row_offset,"movement_enabled":true,"battle_state":2,"battle_frame":0,"verdict":0,"verdict_tick":-1,"ending_counter":-1,"ending_gate_tick":-1,"prize_count":0},
        "units":units,"objects":[],"subsets":subsets,"buckets":bucket_rows.iter().map(|(k,ids)|json!([k,ids])).collect::<Vec<_>>(),
        "rng":{"math":random_state(math_seed),"lib":random_state(seed)},"rows":skill_table,"effect_resources":effect_resources,
        "prizes":prizes,"human_bases":human_bases,"projectile_sources":[],"animation_resources":animation_resources,"consumables":consumables
    });
    // Reward-certificate scope is keyed to enemy possessed SkillData rows, not the
    // Monster table's category/type column. Unknown skill rows fail the scope closed.
    let mut scope_allowed = true;
    for member in &enemy_sorted {
        for sid in arr(&member["skills"]["dataIds"], "enemy skill ids")? {
            let id = i(sid, "enemy skill id")?;
            match skill_table.get(&id.to_string()) {
                Some(row) => match row.get("type").and_then(Value::as_i64) {
                    Some(2 | 15) => scope_allowed = false,
                    Some(_) => {}
                    None => scope_allowed = false,
                },
                None => scope_allowed = false,
            }
        }
    }
    let mut name_to_identity = Map::new();
    let mut prepared_fighters = Vec::with_capacity(own_sorted.len() + enemy_sorted.len());
    for (roster_index, member) in own_sorted.iter().enumerate() {
        let identity = 100 + roster_index as i32;
        let name = text(&member["name"], "prepared own unit name")?;
        name_to_identity.insert(name.to_owned(), json!(identity));
        prepared_fighters.push(json!({"identity":identity,"team":0,"side":"ally",
            "rosterIndex":roster_index,"name":name,"alias":name,
            "sourceIndex":member.get("sourceIndex").cloned().unwrap_or(Value::Null)}));
    }
    for (roster_index, member) in enemy_sorted.iter().enumerate() {
        let identity = 100 + own_sorted.len() as i32 + roster_index as i32;
        let incoming_index = member["incomingIndex"]
            .as_u64()
            .ok_or("enemy incomingIndex missing")?;
        let monster_id = member["monsterId"]
            .as_i64()
            .ok_or("enemy monsterId missing")?;
        let alias = format!("enemy:{incoming_index}:{monster_id}");
        let name = text(&member["name"], "prepared enemy unit name")?;
        name_to_identity.insert(alias.clone(), json!(identity));
        prepared_fighters.push(json!({"identity":identity,"team":1,"side":"enemy",
            "rosterIndex":roster_index,"name":name,"alias":alias,
            "sourceIndex":incoming_index,"monsterId":monster_id}));
    }
    Ok(
        json!({"snapshot":snapshot,"ownPrepared":own_sorted,"enemyPrepared":enemy_sorted,
            "rawIntentOwnCount":raw_intent_own_count,
            "names":name_to_identity.clone(),"nameToIdentity":name_to_identity,
            "preparedFighters":prepared_fighters,
            "householdOwners":household_owners,
            "initializationOrders":initialization_orders,"placementGroups":placement_groups,
            "initializationMode":if profile.is_some() || !captured_placements.is_empty(){"profile"}else{"preplaced"},
            "capturedPrePlacement":!captured_placements.is_empty(),
            "followerSelectionDraws":followers.len(),"scopeAllowed":scope_allowed,
            "finishPolicy":finish_policy,"finishPolicySource":finish_policy_source,"mpWatchNames":watch_names}),
    )
}

fn make_monster(
    row: &Value,
    id: i32,
    boss: bool,
    level: i32,
    catalog: &Value,
) -> Result<Value, String> {
    let curves = arr(
        field(row, "parametersRaw", "monster")?,
        "monster.parametersRaw",
    )?;
    if curves.len() != 7 {
        return err("monster must provide seven canonical parameter curves");
    }
    let mut params = Map::new();
    for (pid, curve) in MONSTER_PARAMS.iter().zip(curves) {
        let v = monster_value(arr(curve, "monster curve")?, level)?;
        params.insert(pid.to_string(),json!({"rawValue":v,"rawMax":if *pid==10||*pid==11 {v}else{i32::MAX},"extraValue":0,"extraMax":0,"trainingLevel":1}));
    }
    let defense = parameter_key(&Value::Object(params.clone()), 14)?
        .get("rawValue")
        .and_then(Value::as_i64)
        .ok_or("monster missing defense")?;
    let (monster_type, monster_size) = monster_facts(catalog, id)?;
    Ok(
        json!({"monsterId":id,"name":field(row,"name","monster")?,"monster":true,"human":false,"leaderIdentity":boss,"visitor":false,"ownerPlayer":false,"rank":if boss {1}else{level},"level":level,"monsterType":monster_type,"monsterSize":monster_size,"effectiveDefense":defense,"parameters":params,"skills":{"dataIds":[row_i(row,"skillId","monster")?],"invocationLevels":[1],"invokingSkills":[]}}),
    )
}

fn table_row<'a>(catalog: &'a Value, id: i32) -> Result<&'a [Value], String> {
    let table = field(catalog, "monsterTable", "catalog")?;
    let row = table
        .get(id.to_string())
        .ok_or_else(|| format!("missing static Monster row {id}"))?;
    arr(row, "monsterTable row")
}

fn monster_facts(catalog: &Value, id: i32) -> Result<(i32, i32), String> {
    let row = table_row(catalog, id)?;
    if row.len() <= 5 {
        return err(format!("Monster row {id} is too short for type/size"));
    }
    Ok((
        int_or_decimal_string(&row[4], "Monster.type")?,
        int_or_decimal_string(&row[5], "Monster.size")?,
    ))
}

fn random_state(seed: i32) -> Value {
    let rng = LegacyRandom::new(seed);
    json!({"values":rng.values.to_vec(),"index":rng.index,"partner":rng.partner,"draws":0})
}

fn native_unit(
    identity: i32,
    team: i32,
    member: &Value,
    source: &Value,
    start_cell: [i32; 2],
    start_board: Map<String, Value>,
    start_offset: [f64; 3],
    start_long_board: Value,
) -> Result<Value, String> {
    let human = member["human"]
        .as_bool()
        .ok_or("prepared member lacks human flag")?;
    let cell = start_cell;
    let position = json!([
        cell[0] as f32 * 24.0,
        0.0,
        cell[1] as f32 * 24.0,
        start_offset[0],
        start_offset[1],
        start_offset[2],
        null
    ]);
    let flags = i(
        source.get("humanFlags").unwrap_or(&Value::from(0)),
        "humanFlags",
    )?;
    let skill_ids: Vec<i32> = if team == 0 {
        arr(&source["skills"], "source skills")?
            .iter()
            .map(|v| i(v, "skill id"))
            .collect::<Result<_, _>>()?
    } else {
        arr(&source["skills"]["dataIds"], "enemy skills")?
            .iter()
            .map(|v| i(v, "skill id"))
            .collect::<Result<_, _>>()?
    };
    let levels: Vec<i32> = if team == 0 {
        arr(&source["invocationLevels"], "source invocationLevels")?
            .iter()
            .map(|v| i(v, "invocation level"))
            .collect::<Result<_, _>>()?
    } else {
        arr(
            &source["skills"]["invocationLevels"],
            "enemy invocationLevels",
        )?
        .iter()
        .map(|v| i(v, "invocation level"))
        .collect::<Result<_, _>>()?
    };
    let invoking = source
        .get("invokingSkills")
        .cloned()
        .unwrap_or_else(|| json!([]));
    let mut components = Map::new();
    components.insert("position".into(), position);
    components.insert("speed".into(), json!([0.0, 0.0, 0.0]));
    components.insert("seb".into(), json!([if human { 11 } else { 22 }, 0, 0, -1]));
    components.insert("depth".into(), Value::Null);
    components.insert("cell".into(), json!(cell));
    components.insert("image".into(), json!([-1, -1, -1, -1, -1, -1]));
    components.insert("animation".into(), json!([1, -1]));
    components.insert("direction".into(), json!(if team == 0 { 0 } else { 2 }));
    components.insert("modifier".into(), Value::Null);
    components.insert("garbage".into(), Value::Null);
    components.insert("effect".into(), Value::Null);
    components.insert("projectile".into(), Value::Null);
    components.insert("attack".into(), Value::Null);
    let mut present_slots = vec![0, 1, 2, 5, 7, 12, 14, 28, 33, 51];
    if human {
        present_slots.push(18);
    } else {
        present_slots.push(20);
    }
    if team == 0 {
        present_slots.push(49);
    }
    present_slots.sort_unstable();
    let board: Map<String, Value> = start_board;
    let parameters = source
        .get("parameters")
        .cloned()
        .ok_or("prepared unit missing raw parameters")?;
    let equip = source
        .get("equipmentAbi")
        .cloned()
        .unwrap_or_else(|| json!([]));
    let weapon = source
        .get("weaponAbi")
        .cloned()
        .unwrap_or_else(|| json!({"type":0,"shootingRange":1,"motion":4,"projectileFlag":0}));
    let (monster_type, monster_size) = if !human {
        let mid = source
            .get("monsterId")
            .and_then(Value::as_i64)
            .ok_or("monster unit missing monsterId")? as i32;
        (
            source
                .get("monsterType")
                .and_then(Value::as_i64)
                .unwrap_or(0),
            source
                .get("monsterSize")
                .and_then(Value::as_i64)
                .unwrap_or(0),
        )
    } else {
        (0, 0)
    };
    let boss = source
        .get("leaderIdentity")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    Ok(
        json!({"identity":identity,"present":1,"id":identity,"team":team,"human":human,"monster":!human,"flags":0,"destroyed":false,"components":components,"board":board,"long_board":start_long_board,"parameters":parameters,"equipment":equip,"skills":skill_ids,"levels":levels,"invoking":invoking,"commands":[],"path":[],"weapon":weapon,"boss":boss,"monsterType":monster_type,"specialHuman":false,"monsterSize":monster_size,"human_flag":if human {flags}else{0},"present_slots":present_slots}),
    )
}

fn pair(v: &Value, name: &str) -> Result<[i32; 2], String> {
    let a = arr(v, name)?;
    if a.len() != 2 {
        return err(format!("{name} must have two coordinates"));
    }
    Ok([i(&a[0], name)?, i(&a[1], name)?])
}

fn profile_cell(profile: &Value, key: &str, default: [i32; 2]) -> Result<[i32; 2], String> {
    match profile.get(key) {
        None | Some(Value::Null) => Ok(default),
        Some(v) => pair(v, key),
    }
}

fn key_for_cell(cell: [i32; 2], width: i32) -> i32 {
    (cell[0] as i64 + (cell[1] as i64 * width as i64 * 2)) as i32
}

fn subset(ids: &[i32]) -> Value {
    json!({"len":ids.len(),"free_len":0,"version":ids.len(),"slots":ids,"free":[]})
}
