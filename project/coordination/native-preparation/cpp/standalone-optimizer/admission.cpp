#include "admission.hpp"
#include "fixed_formation.hpp"
#include "normalization.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <set>
#include <string>
#include <unordered_map>

namespace kaopt {
namespace {
using namespace std::string_literals;
void require(bool ok, const std::string& message) {
    if (!ok) throw std::runtime_error(message);
}
bool integer(const Json& j) { return j.is_number_integer() && !j.is_boolean(); }
bool finite_number(const Json& j) {
    return j.is_number() && !j.is_boolean() && std::isfinite(j.get<double>());
}
std::int64_t i64(const Json& j) { return j.get<std::int64_t>(); }
std::int32_t key_i32(const std::string& key, const std::string& where) {
    std::size_t used = 0;
    long long value = 0;
    try { value = std::stoll(key, &used, 10); }
    catch (...) { throw std::runtime_error(where + " board keys must be integer IDs"); }
    require(used == key.size() && value >= INT32_MIN && value <= INT32_MAX,
            where + " board key outside signed32 integer IDs");
    return static_cast<std::int32_t>(value);
}
void validate_source_board(const Json& board, const std::string& where) {
    require(board.is_object(), where + " must be a mapping");
    std::set<std::int32_t> keys;
    for (auto it = board.begin(); it != board.end(); ++it) {
        const auto key = key_i32(it.key(), where);
        require(keys.insert(key).second, where + " has duplicate numeric board keys");
        require(integer(it.value()), where + " values must be integers");
        (void)i64(it.value()); // Native KaEntry values are signed64.
    }
}
void validate_preplacement(const Json& source, const std::unordered_map<std::int64_t, Json>& skills) {
    if (!source.contains("prePlacement")) return;
    const auto& all = source.at("prePlacement");
    require(all.is_object(), "prePlacement must map fighter names to source state");
    for (auto it = all.begin(); it != all.end(); ++it) {
        require(!it.key().empty(), "prePlacement fighter names must not be empty");
        const auto& pre = it.value();
        require(pre.is_object() && pre.size() == 5 && pre.contains("cell") &&
                pre.contains("position") && pre.contains("offset") && pre.contains("board") &&
                pre.contains("longBoard"),
                "prePlacement requires exactly cell, position, offset, board and longBoard");
        const auto& cell = pre.at("cell");
        require(cell.is_array() && cell.size() == 2,
                "prePlacement cell must be a two-integer pair");
        for (const auto& value : cell)
            require(integer(value) && i64(value) >= INT32_MIN && i64(value) <= INT32_MAX,
                    "prePlacement cells must fit signed32 integers");
        for (const char* field : {"position", "offset"}) {
            const auto& vector = pre.at(field);
            require(vector.is_array() && vector.size() == 3,
                    std::string("prePlacement ") + field + " must be a three-number vector");
            for (const auto& value : vector)
                require(finite_number(value), std::string("prePlacement ") + field +
                        " must contain finite numbers");
        }
        validate_source_board(pre.at("board"), "prePlacement board");
        validate_source_board(pre.at("longBoard"), "prePlacement longBoard");
        require(pre.at("board").size()<=48 && pre.at("longBoard").size()<=48,
                "prePlacement native boards exceed 48 entries");
        const auto& board = pre.at("board");
        std::set<std::int32_t> keys;
        for (auto b = board.begin(); b != board.end(); ++b) keys.insert(key_i32(b.key(), "prePlacement board"));
        for (int key = 4; key <= 8; ++key)
            require(keys.count(key) != 0, "prePlacement board must supply keys 4/5/6/7/8");
        const bool any_status = keys.count(62) || keys.count(63) || keys.count(64);
        if (any_status) {
            require(keys.count(62) && keys.count(63) && keys.count(64),
                    "Starting status requires all native fields 62/63/64");
            for (auto b = board.begin(); b != board.end(); ++b) {
                const auto key = key_i32(b.key(), "prePlacement board");
                if (key == 62 || key == 63 || key == 64)
                    require(i64(b.value()) >= INT32_MIN && i64(b.value()) <= INT32_MAX,
                            "prePlacement status fields must fit signed32 storage");
            }
            auto skill_id = board.begin();
            while (skill_id != board.end() && key_i32(skill_id.key(), "prePlacement board") != 62) ++skill_id;
            const auto skill = skills.find(i64(skill_id.value()));
            require(skill != skills.end() && skill->second.value("type", -1) >= 0 &&
                    (skill->second.value("type", -1) == 66 || skill->second.value("type", -1) == 67),
                    "Only supplied defense-down/sleep status skills are integrated");
        }
    }
}
void i32_field(const Json& j, const char* field, const std::string& where) {
    require(j.contains(field) && integer(j[field]), where + " missing integer " + field);
    const auto n = i64(j[field]);
    require(n >= INT32_MIN && n <= INT32_MAX, where + " exceeds signed32: " + field);
}
std::unordered_map<std::int64_t, Json> index_rows(const Json& rows, const char* key) {
    require(rows.is_array(), std::string("tables.") + key + " must be an array");
    std::unordered_map<std::int64_t, Json> result;
    for (const auto& r : rows) {
        require(r.is_object() && r.contains("id") && integer(r["id"]), std::string("bad row in ") + key);
        require(result.emplace(i64(r["id"]), r).second, std::string("duplicate id in ") + key);
    }
    return result;
}
} // namespace

// Canonical admission boundary: under the fixed-formation policy every candidate (input, resume
// import, generated child or evaluated job) is checked here, so no incompatible scenario can pass.
Json admit(const Json& source, const Json& tables) {
    if (fixed_formation_enabled()) validate_fixed_formation_raw(source);
    require(source.is_object(), "scenario must be an object");
    require(tables.is_object() && tables.contains("weapon-skill-profiles"), "tables requires weapon-skill-profiles group");
    const auto& profiles=tables.at("weapon-skill-profiles");
    require(profiles.is_object() && profiles.contains("skills") && profiles.contains("equipment"),
            "tables must contain weapon-skill-profiles skills and equipment arrays");
    const auto skills = index_rows(profiles["skills"], "skills");
    const auto equipment = index_rows(profiles["equipment"], "equipment");
    Json d = normalize_scenario_inputs(source, tables);

    require(d.value("schema", "") == "ka-special-combat-research-1", "Unsupported scenario schema");
    for (const char* k : {"encounterId", "defeatCount", "mathSeed", "libSeed", "tickLimit", "ownUnits", "inputs"})
        require(d.contains(k), std::string("Missing ") + k);
    for (const char* k : {"encounterId", "defeatCount", "mathSeed", "libSeed", "tickLimit"})
        require(integer(d[k]), std::string(k) + " must be an integer");
    require(i64(d["encounterId"]) >= 0 && i64(d["encounterId"]) < 20 &&
            i64(d["defeatCount"]) >= 0 && i64(d["tickLimit"]) >= 1,
            "Invalid encounter, defeat count or tick limit");
    require(d["ownUnits"].is_array() && !d["ownUnits"].empty(), "Explicit own roster required");
    require(d["inputs"].is_array(), "inputs must be an array");

    validate_preplacement(d, skills);
    if(d.contains("startProfile")) {
        const auto& profile=d.at("startProfile");
        require(profile.is_object()&&profile.value("kind",std::string{})=="isolated-scene0","Unsupported startProfile kind");
        const std::set<std::string> allowed{"kind","enemySpawnCell","bossCell","startingStatus","note"};
        for(auto i=profile.begin();i!=profile.end();++i)require(allowed.contains(i.key()),"Unknown startProfile field: "+i.key());
        for(const char* key:{"enemySpawnCell","bossCell"})if(profile.contains(key)&&!profile.at(key).is_null()){
            const auto& cell=profile.at(key);require(cell.is_array()&&cell.size()==2,"startProfile cell must be integer pair");
            for(const auto& coordinate:cell)require(integer(coordinate)&&i64(coordinate)>=INT32_MIN&&i64(coordinate)<=INT32_MAX,"startProfile coordinate outside signed32");
        }
        if(profile.contains("startingStatus"))require(profile.at("startingStatus").is_object(),"startingStatus must map fighter names to [skillId,turns]");
    }
    if (d.contains("finishPolicy"))
        require(d["finishPolicy"].is_string() &&
                (d["finishPolicy"] == "at-horizon" || d["finishPolicy"] == "after-ending" || d["finishPolicy"] == "on-verdict"),
                "finishPolicy must be at-horizon, after-ending or on-verdict");

    std::set<std::string> names;
    for (auto& u : d["ownUnits"]) {
        require(u.is_object(), "ownUnits entries must be objects");
        for (const char* k : {"name", "human", "monsterId", "parameters", "skills", "invocationLevels", "weaponId", "equipment", "visitor", "leaderIdentity"})
            require(u.contains(k), std::string("Unit lacks ") + k);
        require(u["name"].is_string() && names.insert(u["name"].get<std::string>()).second,
                "Unit names must be unique strings");
        require(u["human"].is_boolean() && (u["monsterId"].is_null() == u["human"].get<bool>()),
                "Unit must be exactly Human or Monster");
        require(u["visitor"].is_boolean() && u["leaderIdentity"].is_boolean(), "visitor/leaderIdentity must be booleans");
        require(!u.value("friend", false), "Friends are outside Wairo/Kairo scope");
        const auto flags = u.value("humanFlags", Json(0));
        require(integer(flags) && i64(flags) >= INT32_MIN && i64(flags) <= INT32_MAX, "humanFlags must fit signed32 storage");
        require(!u.value("onVehicle", false) && !(i64(flags) & 4), "Vehicle source components and movement are not integrated");
        require(u["human"].get<bool>() || i64(flags) == 0, "Monster cannot have Human flags");
        require(!u.contains("parameterLinks") || (u["parameterLinks"].is_array() && u["parameterLinks"].empty()),
                "Linked parameter maxima are not integrated");

        require(u["parameters"].is_object(), "parameters must be an object");
        std::set<int> parameter_ids;
        for (auto it = u["parameters"].begin(); it != u["parameters"].end(); ++it) {
            int pid = -1;
            try { pid = std::stoi(it.key()); } catch (...) { throw std::runtime_error("Parameter keys must be integer IDs"); }
            require(std::to_string(pid) == it.key() || (it.key().size() && it.key()[0] == '0'), "Non-canonical parameter ID");
            require(parameter_ids.insert(pid).second, "Duplicate parameter ID");
            const auto& p = it.value();
            require(p.is_object(), "parameter rows must be objects");
            for (const char* f : {"rawValue", "rawMax", "extraValue", "extraMax", "trainingLevel"}) i32_field(p, f, "parameter");
        }
        const std::set<int> human_required{10,11,12,13,14,15,16,18,19,20,21,22};
        const std::set<int> monster_required{10,11,13,14,15,16,19};
        for (int id : (u["human"].get<bool>() ? human_required : monster_required))
            require(parameter_ids.count(id), "Missing raw/training parameters; displayed stats alone are insufficient");

        require(u["skills"].is_array() && u["invocationLevels"].is_array() && u["skills"].size() == u["invocationLevels"].size(),
                "Every skill slot needs an invocation setting");
        for (std::size_t i=0; i<u["skills"].size(); ++i) {
            require(integer(u["skills"][i]) && skills.count(i64(u["skills"][i])), "Unknown skill id");
            require(integer(u["invocationLevels"][i]) && (u["invocationLevels"][i] == 0 || u["invocationLevels"][i] == 1 || u["invocationLevels"][i] == 2), "Unknown invocation setting");
        }
        if (u.contains("invokingSkills")) {
            require(u["invokingSkills"].is_array() && u["invokingSkills"].size()<=64, "invokingSkills must be at most 64 [skillId,count] pairs");
            for (const auto& pair : u["invokingSkills"])
                require(pair.is_array() && pair.size()==2 && integer(pair[0]) && integer(pair[1]) && skills.count(i64(pair[0])) && i64(pair[1])>0 && i64(pair[1])<INT32_MAX,
                        "Invalid invokingSkills entry");
        }
        require(u["equipment"].is_array(), "equipment must be an array");
        std::set<std::int64_t> equipment_ids;
        for (const auto& slot : u["equipment"]) {
            require(slot.is_object() && slot.size()==3 && slot.contains("id") && slot.contains("level") && slot.contains("affinity"),
                    "Equipment needs exactly id, level and affinity");
            require(integer(slot["id"]) && equipment.count(i64(slot["id"])), "Unknown equipment");
            require(integer(slot["level"]) && i64(slot["level"])>=1 && finite_number(slot["affinity"]), "Invalid equipment level/affinity");
            equipment_ids.insert(i64(slot["id"]));
        }
        require(integer(u["weaponId"]) && equipment.count(i64(u["weaponId"])), "Unknown weapon");
        require(equipment.at(i64(u["weaponId"])).value("category", -1) == 0, "Weapon ID is not a weapon");
        require(i64(u["weaponId"])==0 || equipment_ids.count(i64(u["weaponId"])), "Weapon must be included in equipment contributions");
    }
    for (const auto& e : d["inputs"]) {
        require(e.is_object() && e.contains("tick") && integer(e["tick"]) && i64(e["tick"])>=0 && i64(e["tick"])<=INT32_MAX, "Input tick must be nonnegative signed32");
        require(e.contains("type") && e["type"].is_string() && (e["type"]=="holy_herb" || e["type"]=="item"), "Input type must be holy_herb or item");
        require(e.contains("phase") && e["phase"].is_string() && (e["phase"]=="before_fighters" || e["phase"]=="after_fighters"), "Input phase must be explicit");
    }
    require(d.contains("holyHerbStock") && integer(d["holyHerbStock"]) && i64(d["holyHerbStock"])>=0, "Explicit Holy Herb stock required");
    const auto max_uses = d.value("holyHerbMaxUses", Json(0));
    require(integer(max_uses) && i64(max_uses)>=0 && i64(max_uses)<=i64(d["holyHerbStock"]), "Invalid holyHerbMaxUses");
    for (const char* field : {"holyHerbTriggerUnits", "mpWatchUnits"}) if (d.contains(field)) {
        const auto& list=d[field]; require(list.is_array() && list.size()<=2, std::string(field)+" must be a list of at most two unit names");
        std::set<std::string> seen;
        for (const auto& n : list) {
            require(n.is_string() && seen.insert(n.get<std::string>()).second && names.count(n.get<std::string>()), std::string("Invalid ")+field+" name");
            auto it=std::find_if(d["ownUnits"].begin(),d["ownUnits"].end(),[&](const Json& u){return u["name"]==n;});
            require(it!=d["ownUnits"].end() && (*it)["human"].get<bool>(), std::string(field)+" must name human residents");
        }
    }
    if (i64(max_uses)>0)
        require(d.contains("holyHerbTriggerUnits") && d["holyHerbTriggerUnits"].is_array() && !d["holyHerbTriggerUnits"].empty(), "Positive herb use requires explicit trigger units");
    return d;
}
} // namespace kaopt
