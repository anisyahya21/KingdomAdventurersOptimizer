#include "effective_combat_features.hpp"

#include <algorithm>
#include <bit>
#include <cmath>
#include <cstdint>
#include <map>
#include <limits>
#include <cctype>
#include <stdexcept>
#include <string>

namespace kaopt {
namespace {
using I32 = std::int32_t;
constexpr I32 I32Max = 2147483647;
I32 wrap32(std::int64_t value) { return static_cast<I32>(static_cast<std::uint32_t>(value)); }
I32 div0(I32 value, I32 divisor) { if (!divisor) throw std::runtime_error("division by zero in skill cost"); return value / divisor; }
I32 checked_i32(const Json& value, const char* label) {
    if (!value.is_number_integer()) throw std::runtime_error(std::string(label) + " must be an integer");
    const auto out = value.get<std::int64_t>();
    if (out < std::numeric_limits<I32>::min() || out > I32Max) throw std::runtime_error(std::string(label) + " outside signed32");
    return static_cast<I32>(out);
}
const Json& rows(const Json& tables, const char* group, const char* key) {
    const auto& value = tables.at(group).at(key);
    if (!value.is_array()) throw std::runtime_error(std::string("tables.") + group + "." + key + " must be an array");
    return value;
}
I32 linear_easing(I32 low, I32 high, I32 progress) {
    constexpr I32 steps = 998;
    if (progress < 0) return low;
    if (progress >= steps) return high;
    const float fraction = std::bit_cast<float>(std::bit_cast<std::uint32_t>(static_cast<float>(progress) / static_cast<float>(steps)));
    const float delta = std::bit_cast<float>(std::bit_cast<std::uint32_t>(static_cast<float>(wrap32(static_cast<std::int64_t>(high) - low))));
    const float base = std::bit_cast<float>(std::bit_cast<std::uint32_t>(static_cast<float>(low)));
    const float value = std::bit_cast<float>(std::bit_cast<std::uint32_t>(std::bit_cast<float>(std::bit_cast<std::uint32_t>(fraction * delta)) + base));
    const auto converted = static_cast<I32>(value);
    return std::max(std::min(low, high), std::min(std::max(low, high), converted));
}
Json scalar_or_null(const Json& value, const char* key) { return value.contains(key) ? value.at(key) : Json(nullptr); }
}

Json effective_combat_features(const Json& raw, const Json& prepared, const Json& tables) {
    static constexpr I32 parameterIds[] = {10,11,12,13,14,15,16,18,19,20,21,22};
    static constexpr const char* weaponFields[] = {"type","motion","shootingRange","projectileFlag","category"};
    if (!raw.is_object() || !raw.contains("ownUnits") || !raw.at("ownUnits").is_array() || raw.at("ownUnits").empty())
        throw std::runtime_error("effective combat features need a scenario with ownUnits");
    if (!prepared.is_object() || !prepared.contains("ownUnits") || prepared.at("ownUnits").size() != raw.at("ownUnits").size())
        throw std::runtime_error("prepared roster does not match source scenario");

    std::map<I32, const Json*> equipmentById, skillById;
    for (const auto& row : rows(tables,"weapon-skill-profiles","equipment")) {
        const auto id = checked_i32(row.at("id"),"equipment id");
        if (!equipmentById.emplace(id,&row).second) throw std::runtime_error("duplicate equipment id in feature tables");
    }
    for (const auto& row : rows(tables,"weapon-skill-profiles","skills")) {
        const auto id = checked_i32(row.at("id"),"skill id");
        if (!skillById.emplace(id,&row).second) throw std::runtime_error("duplicate skill id in feature tables");
    }

    Json fight = Json::object();
    for (const char* key : {"encounterId","defeatCount","tickLimit","holyHerbStock","inputs"})
        fight[key] = scalar_or_null(raw,key);
    Json units = Json::array();
    for (std::size_t i=0; i<raw.at("ownUnits").size(); ++i) {
        const auto& unit = raw.at("ownUnits").at(i);
        const Json* preparedUnit = nullptr;
        for (const auto& candidate : prepared.at("ownUnits"))
            if (candidate.value("incomingIndex",std::size_t(999)) == i) { preparedUnit = &candidate; break; }
        if (!preparedUnit) throw std::runtime_error("prepared roster index missing during feature export");
        const auto weaponId = checked_i32(unit.at("weaponId"),"weaponId");
        const auto weapon = equipmentById.find(weaponId);
        if (weapon == equipmentById.end()) throw std::runtime_error("weapon table row missing during feature export");
        const auto training = checked_i32(preparedUnit->at("averageTrainingLevel"),"averageTrainingLevel");
        Json effective = Json::object();
        const auto& preparedStats = preparedUnit->at("effectiveParameters");
        for (const auto pid : parameterIds) {
            const auto key = std::to_string(pid);
            const bool boundedMaximum = pid==10 || pid==11 || pid==12;
            effective[key] = checked_i32(preparedStats.at(key).at(boundedMaximum?"maximum":"value"),"effective stat value");
        }
        Json behavior = Json::object();
        for (const char* field : weaponFields) {
            if (!weapon->second->contains(field)) throw std::runtime_error(std::string("weapon table lacks behavior field ") + field);
            behavior[field] = weapon->second->at(field);
        }
        Json skills = unit.at("skills"), levels = unit.at("invocationLevels"), costs = Json::array();
        if (!skills.is_array() || !levels.is_array() || skills.size() != levels.size())
            throw std::runtime_error("skill and invocation slots differ during feature export");
        for (const auto& skillIdValue : skills) {
            const auto skillId = checked_i32(skillIdValue,"skill id");
            const auto skill = skillById.find(skillId);
            if (skill == skillById.end()) throw std::runtime_error("skill table row missing during feature export");
            const auto minMp = checked_i32(skill->second->at("minMp"),"skill minMp");
            const auto maxMp = checked_i32(skill->second->at("maxMp"),"skill maxMp");
            const auto cost = unit.at("human").get<bool>() ? linear_easing(minMp,maxMp,training-1) : minMp;
            costs.push_back(cost);
        }
        Json placement = {{"grid",scalar_or_null(unit,"grid")},{"cell",scalar_or_null(unit,"cell")}};
        units.push_back(Json{{"rosterIndex",i},{"human",unit.at("human")},{"placement",std::move(placement)},
            {"effectiveStats",std::move(effective)},
            {"combatContext",{{"weaponBehavior",std::move(behavior)},{"skillIdsOrdered",std::move(skills)},
                {"invocationLevelsOrdered",std::move(levels)},{"averageTrainingLevel",training},
                {"skillMpCostsOrdered",std::move(costs)}}}});
    }
    Json order = raw.value("ownFormationOrder",Json(nullptr));
    return Json{{"schema","kaopt-effective-combat-features-1"},{"fightContext",std::move(fight)},
        {"ownFormationOrder",std::move(order)},{"units",std::move(units)}};
}

namespace {
void flatten(const Json& value, const std::string& path, Json& out) {
    if (value.is_object()) {
        for (auto it=value.begin(); it!=value.end(); ++it) {
            const bool numericKey=!it.key().empty()&&std::all_of(it.key().begin(),it.key().end(),[](unsigned char c){return std::isdigit(c)!=0;});
            const std::string child=path.empty()?it.key():(numericKey&&path.ends_with(".effectiveStats")?path+"["+it.key()+"]":path+"."+it.key());
            flatten(it.value(),child,out);
        }
        return;
    }
    if (value.is_array()) {
        for (std::size_t i=0;i<value.size();++i) {
            const std::string child=path+"["+std::to_string(i)+"]";
            if (path=="units") flatten(value[i],"unit["+std::to_string(i)+"]",out);
            else if (path.ends_with(".skillIdsOrdered")) {
                const auto& skill=value[i]; out[child+"="+skill.dump()]=1.0;
            } else flatten(value[i],child,out);
        }
        return;
    }
    const bool categorical = value.is_null() || value.is_string() || value.is_boolean() ||
        path.ends_with(".weaponBehavior.type") || path.ends_with(".weaponBehavior.motion") ||
        path.ends_with(".weaponBehavior.projectileFlag") || path.ends_with(".weaponBehavior.category");
    if (categorical) out[path+"="+value.dump()]=1.0;
    else if (value.is_number()) out[path]=value.get<double>();
    else throw std::runtime_error("unsupported normalized feature leaf at "+path);
}
}
Json flatten_effective_combat_features(const Json& features) {
    if (!features.is_object() || features.value("schema",std::string{})!="kaopt-effective-combat-features-1")
        throw std::runtime_error("normalized feature schema mismatch");
    Json out=Json::object(); flatten(features,"",out); return out;
}
}
