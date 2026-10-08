#include "fixed_formation.hpp"

#include "pipeline.hpp"

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdint>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>

namespace kaopt {
namespace {

#include "fixed_formation_policy.inc"

std::atomic<bool> g_fixed_formation_enabled{false};

struct FixedBounds {
    std::int64_t minimum = 0;
    std::int64_t maximum = 0;
};

std::string canonical_text(const Json& value) {
    return value.dump(-1, ' ', false, Json::error_handler_t::strict);
}

const Json& policy_document() {
    static const Json document = [] {
        Json value = Json::parse(kFixedFormationPolicyJson);
        if (!value.is_object() || value.value("schema", std::string{}) != "ka-fixed-formation-policy-1")
            throw std::runtime_error("fixed-formation policy has an unexpected schema");
        if (!value.contains("fixed") || !value["fixed"].contains("rosterOrder") ||
            !value["fixed"]["rosterOrder"].is_array() || value["fixed"]["rosterOrder"].size() != 6)
            throw std::runtime_error("fixed-formation policy is incomplete");
        if (!value.contains("dps") || !value["dps"].contains("searchableParameters"))
            throw std::runtime_error("fixed-formation policy lacks the Synthetic DPS walls");
        return value;
    }();
    return document;
}

const std::map<std::string, FixedBounds>& bounds_by_parameter() {
    static const std::map<std::string, FixedBounds> table = [] {
        std::map<std::string, FixedBounds> out;
        const auto& walls = policy_document()["dps"]["searchableParameters"];
        for (auto it = walls.begin(); it != walls.end(); ++it) {
            if (!it.value().is_array() || it.value().size() != 2)
                throw std::runtime_error("fixed-formation searchable parameter is malformed");
            out.emplace(it.key(), FixedBounds{it.value().at(0).get<std::int64_t>(),
                                              it.value().at(1).get<std::int64_t>()});
        }
        return out;
    }();
    return table;
}

bool bounded_parameter(const std::string& key) {
    return key == "10" || key == "11" || key == "12";
}

std::int64_t parameter_value(const std::string& key, const Json& entry) {
    const char* field = bounded_parameter(key) ? "rawMax" : "rawValue";
    if (!entry.is_object() || !entry.contains(field) || !entry[field].is_number_integer())
        throw std::runtime_error("fixed-formation parameter " + key + " lacks an integer " + field);
    return entry[field].get<std::int64_t>();
}

bool same_parameter(const Json& actual, const Json& expected) {
    static const char* fields[] = {"rawValue", "rawMax", "extraValue", "extraMax", "trainingLevel"};
    for (const char* field : fields) {
        const auto actual_value = actual.value(field, Json(nullptr));
        const auto expected_value = expected.value(field, Json(nullptr));
        if (!actual_value.is_number_integer() || !expected_value.is_number_integer()) return false;
        if (actual_value.get<std::int64_t>() != expected_value.get<std::int64_t>()) return false;
    }
    return true;
}

bool same_int_array(const Json& actual, const Json& expected) {
    if (!actual.is_array() || !expected.is_array() || actual.size() != expected.size()) return false;
    for (std::size_t index = 0; index < actual.size(); ++index)
        if (!actual[index].is_number_integer() || !expected[index].is_number_integer() ||
            actual[index].get<std::int64_t>() != expected[index].get<std::int64_t>())
            return false;
    return true;
}

std::string require_string_field(const Json& value, const char* field, const std::string& label) {
    if (!value.contains(field) || !value[field].is_string())
        throw std::runtime_error(label + " needs a " + field + " string");
    return value[field].get<std::string>();
}

void check_fixed_unit(const Json& unit, const Json& template_unit, const std::string& label) {
    if (!unit.is_object()) throw std::runtime_error(label + " must be an object");
    if (!unit.value("human", false)) throw std::runtime_error(label + " must be human");
    if (unit.contains("monsterId") && !unit["monsterId"].is_null())
        throw std::runtime_error(label + " must not be a monster");
    if (unit.value("leaderIdentity", false) || unit.value("visitor", false))
        throw std::runtime_error(label + " must not be a leader or visitor");
    if (!unit.contains("weaponId") || !unit["weaponId"].is_number_integer() ||
        unit["weaponId"].get<std::int64_t>() != 0)
        throw std::runtime_error(label + " must carry no weapon");
    if (unit.contains("equipment") && unit["equipment"].is_array() && !unit["equipment"].empty())
        throw std::runtime_error(label + " must carry no equipment");
    if (!unit.contains("skills") || !unit.contains("invocationLevels") ||
        !same_int_array(unit["skills"], template_unit.at("skills")) ||
        !same_int_array(unit["invocationLevels"], template_unit.at("invocationLevels")))
        throw std::runtime_error(label + " skills/triggers do not match the fixed template");
    if (!unit.contains("parameters") || !unit["parameters"].is_object())
        throw std::runtime_error(label + " parameter blocks are missing");
    const auto& parameters = unit["parameters"];
    const auto& expected = template_unit.at("parameters");
    if (parameters.size() != expected.size())
        throw std::runtime_error(label + " parameter blocks do not match the fixed template");
    for (auto it = parameters.begin(); it != parameters.end(); ++it) {
        if (!expected.contains(it.key()) || !same_parameter(it.value(), expected.at(it.key())))
            throw std::runtime_error(label + " parameter " + it.key() + " is fixed and must not mutate");
    }
}

std::uint64_t mix64(std::uint64_t value) {
    value += 0x9E3779B97F4A7C15ULL;
    value = (value ^ (value >> 30)) * 0xBF58476D1CE4E5B9ULL;
    value = (value ^ (value >> 27)) * 0x94D049BB133111EBULL;
    return value ^ (value >> 31);
}

}  // namespace

bool fixed_formation_enabled() { return g_fixed_formation_enabled.load(std::memory_order_relaxed); }

void set_fixed_formation_enabled(bool enabled) {
    g_fixed_formation_enabled.store(enabled, std::memory_order_relaxed);
}

std::string fixed_formation_policy_hash() {
    return policy_document().value("policyHash", std::string{});
}

std::map<std::string, std::pair<std::int64_t, std::int64_t>> fixed_formation_searchable_parameters() {
    std::map<std::string, std::pair<std::int64_t, std::int64_t>> out;
    for (const auto& [key, interval] : bounds_by_parameter())
        out.emplace(key, std::make_pair(interval.minimum, interval.maximum));
    return out;
}

std::vector<Json> fixed_formation_mutation_specs() {
    std::vector<Json> specs;
    for (const auto& [key, interval] : bounds_by_parameter())
        specs.push_back(Json{{"op", "fixed-dps-stat"},
                             {"unitIndex", 0},
                             {"parameter", key},
                             {"minimum", interval.minimum},
                             {"maximum", interval.maximum},
                             {"label", "Synthetic DPS raw stat " + key}});
    return specs;
}

void validate_fixed_formation_raw(const Json& raw) {
    const Json& policy = policy_document();
    if (!raw.is_object()) throw std::runtime_error("fixed-formation scenario must be an object");
    if (!raw.contains("encounterId") || !raw["encounterId"].is_number_integer())
        throw std::runtime_error("fixed-formation scenario needs an integer encounterId");
    const auto encounter = std::to_string(raw["encounterId"].get<std::int64_t>());
    if (!raw.contains("ownUnits") || !raw["ownUnits"].is_array())
        throw std::runtime_error("fixed-formation scenario needs ownUnits");
    const auto& units = raw["ownUnits"];
    const auto unit_count = policy["fixed"].value("unitCount", 6);
    if (units.size() != static_cast<std::size_t>(unit_count))
        throw std::runtime_error("fixed formation needs exactly " + std::to_string(unit_count) + " units, found " +
                                 std::to_string(units.size()));
    const auto& roster = policy["fixed"]["rosterOrder"];
    for (std::size_t index = 0; index < units.size(); ++index) {
        const auto& unit = units[index];
        if (!unit.is_object()) throw std::runtime_error("fixed formation unit " + std::to_string(index) + " is not an object");
        if (!unit.value("human", false))
            throw std::runtime_error("fixed formation unit " + std::to_string(index) + " is not human");
        if (unit.contains("monsterId") && !unit["monsterId"].is_null())
            throw std::runtime_error("fixed formation unit " + std::to_string(index) + " must not be a monster");
        if (unit.value("leaderIdentity", false) || unit.value("visitor", false))
            throw std::runtime_error("fixed formation unit " + std::to_string(index) + " must not be a leader or visitor");
        if (!unit.contains("weaponId") || !unit["weaponId"].is_number_integer() ||
            unit["weaponId"].get<std::int64_t>() != policy["fixed"].value("weaponId", 0))
            throw std::runtime_error("fixed formation unit " + std::to_string(index) + " must carry no weapon");
        if (unit.contains("equipment") && unit["equipment"].is_array() && !unit["equipment"].empty())
            throw std::runtime_error("fixed formation unit " + std::to_string(index) + " must carry no equipment");
        if (require_string_field(unit, "name", "fixed formation unit " + std::to_string(index)) !=
            roster[index].get<std::string>())
            throw std::runtime_error("fixed formation roster order mismatch at " + std::to_string(index));
    }

    const auto& defaults = policy["dps"]["defaults"];
    if (!defaults.contains(encounter))
        throw std::runtime_error("no fixed-formation DPS template for encounter " + encounter);
    const Json& dps_template = defaults[encounter];
    const Json& dps = units[0];
    if (!dps.contains("skills") || !dps.contains("invocationLevels") ||
        !same_int_array(dps["skills"], policy["dps"]["skills"]) ||
        !same_int_array(dps["invocationLevels"], policy["dps"]["invocationLevels"]))
        throw std::runtime_error("Synthetic DPS skills/triggers must be the fixed Counter/7/5/4/3/2 High set");
    if (!dps.contains("parameters") || !dps["parameters"].is_object())
        throw std::runtime_error("Synthetic DPS parameter block is missing");
    const auto& parameters = dps["parameters"];
    if (parameters.size() != dps_template.size())
        throw std::runtime_error("Synthetic DPS must carry exactly the canonical twelve parameters");
    for (auto it = parameters.begin(); it != parameters.end(); ++it) {
        const auto wall = bounds_by_parameter().find(it.key());
        if (wall != bounds_by_parameter().end()) {
            const auto& entry = it.value();
            if (entry.value("extraValue", std::int64_t(0)) != 0 || entry.value("extraMax", std::int64_t(0)) != 0)
                throw std::runtime_error("Synthetic DPS parameter " + it.key() + " must have zero extra value (no equipment)");
            const auto value = parameter_value(it.key(), entry);
            if (value < wall->second.minimum || value > wall->second.maximum)
                throw std::runtime_error("Synthetic DPS parameter " + it.key() + " value " + std::to_string(value) +
                                         " outside [" + std::to_string(wall->second.minimum) + ", " +
                                         std::to_string(wall->second.maximum) + "]");
            if (dps_template.contains(it.key()) &&
                entry.value("trainingLevel", std::int64_t(0)) !=
                    dps_template.at(it.key()).value("trainingLevel", std::int64_t(0)))
                throw std::runtime_error("Synthetic DPS parameter " + it.key() + " trainingLevel must match the template");
        } else if (!dps_template.contains(it.key()) || !same_parameter(it.value(), dps_template.at(it.key()))) {
            throw std::runtime_error("Synthetic DPS parameter " + it.key() + " is fixed and must match the template");
        }
    }
    for (auto it = dps_template.begin(); it != dps_template.end(); ++it)
        if (!parameters.contains(it.key()))
            throw std::runtime_error("Synthetic DPS is missing a canonical parameter");

    check_fixed_unit(units[1], policy["healer"], "fixed healer");
    for (std::size_t index = 2; index < units.size(); ++index)
        check_fixed_unit(units[index], policy["fodder"], "fixed fodder " + std::to_string(index));
}

Json mutate_fixed_formation_stat(const Json& parent, const std::string& parameter, std::int64_t step) {
    if (!fixed_formation_enabled()) throw std::runtime_error("Synthetic-DPS mutation requested while fixed profile is disabled");
    if (step == 0)
        throw std::runtime_error("Synthetic-DPS mutation step must be one of -5, -1, 1, 5");
    const auto walls = fixed_formation_searchable_parameters();
    const auto found = walls.find(parameter);
    if (found == walls.end()) throw std::runtime_error("Synthetic-DPS mutation parameter is not searchable");
    validate_fixed_formation_raw(parent);
    Json child = parent;
    auto& entry = child.at("ownUnits").at(0).at("parameters").at(parameter);
    const bool maxBounded = parameter == "10" || parameter == "11";
    const auto current = maxBounded ? entry.at("rawMax").get<std::int64_t>() : entry.at("rawValue").get<std::int64_t>();
    const auto next = current + step;
    if (next < found->second.first || next > found->second.second)
        throw std::runtime_error("Synthetic-DPS mutation crosses canonical stat wall");
    if (entry.value("extraValue", std::int64_t(0)) != 0 || entry.value("extraMax", std::int64_t(0)) != 0)
        throw std::runtime_error("Synthetic-DPS searched stat unexpectedly has equipment contribution");
    entry["rawValue"] = next;
    if (maxBounded) entry["rawMax"] = next;
    validate_fixed_formation_raw(child);
    return child;
}

std::string fixed_formation_identity(const Json& raw) {
    if (!raw.is_object() || !raw.contains("ownUnits") || !raw["ownUnits"].is_array() ||
        raw["ownUnits"].size() != 6)
        throw std::runtime_error("fixed-formation identity needs six units");
    Json stats = Json::object();
    const auto& parameters = raw["ownUnits"][0].at("parameters");
    for (const auto& [key, interval] : bounds_by_parameter()) {
        (void)interval;
        if (!parameters.contains(key))
            throw std::runtime_error("Synthetic DPS is missing searched parameter " + key);
        stats[key] = parameter_value(key, parameters.at(key));
    }
    // The engine identifies a trial by identity(trialScenario), so the projection must keep the seed
    // fields: the same stat vector evaluated under a different seed bank is a different trial. It
    // reduces only the Synthetic DPS parameter block, which is the sole thing the search varies
    // (everything else is pinned by admission, including the other units' exact numbers).
    Json projection = raw;
    projection["ownUnits"][0]["parameters"] = std::move(stats);
    return sha256_text(canonical_text(projection));
}

Json mutate_fixed_formation_parameter(const Json& parent, const std::string& parameter, std::uint64_t ordinal) {
    if (!fixed_formation_enabled())
        throw std::runtime_error("fixed-formation mutation requested while the fixed policy is disabled");
    const auto wall = bounds_by_parameter().find(parameter);
    if (wall == bounds_by_parameter().end())
        throw std::runtime_error("fixed-formation mutation target parameter is not searchable");
    Json child = parent;
    if (!child.contains("ownUnits") || !child["ownUnits"].is_array() || child["ownUnits"].size() != 6)
        throw std::runtime_error("fixed-formation mutation needs six units");
    auto& parameters = child["ownUnits"][0].at("parameters");
    if (!parameters.contains(parameter))
        throw std::runtime_error("fixed-formation mutation target parameter is absent");
    auto& entry = parameters[parameter];
    const auto current = parameter_value(parameter, entry);
    const auto low = wall->second.minimum;
    const auto high = wall->second.maximum;
    if (low > high) throw std::runtime_error("fixed-formation parameter wall is inverted");
    static const double factors[] = {1.15, 1.35, 1.6, 0.85, 0.7, 1.05, 0.95, 2.0, 0.5};
    constexpr std::size_t factor_count = sizeof(factors) / sizeof(factors[0]);
    const auto parameter_id = static_cast<std::uint64_t>(std::stoll(parameter));
    std::int64_t target = current;
    bool changed = false;
    if (current > 0) {
        for (std::size_t step = 0; step < factor_count; ++step) {
            const auto factor = factors[(ordinal + step) % factor_count];
            auto candidate = static_cast<std::int64_t>(std::llround(static_cast<double>(current) * factor));
            candidate = std::max(low, std::min(high, candidate));
            if (candidate != current) { target = candidate; changed = true; break; }
        }
    }
    if (!changed) {
        const auto span = static_cast<std::uint64_t>(high - low);
        if (span == 0) throw std::runtime_error("fixed-formation parameter wall has zero width");
        const auto mixed = mix64(ordinal * 0x9E3779B97F4A7C15ULL + parameter_id);
        auto candidate = low + static_cast<std::int64_t>(mixed % (span + 1));
        if (candidate == current) candidate = low + static_cast<std::int64_t>((mixed + 1) % (span + 1));
        if (candidate == current) throw std::runtime_error("fixed-formation mutation could not change parameter " + parameter);
        target = candidate; changed = true;
    }
    if (bounded_parameter(parameter)) {
        entry["rawValue"] = target;
        entry["rawMax"] = target;
        entry["extraValue"] = 0;
        entry["extraMax"] = 0;
    } else {
        entry["rawValue"] = target;
        entry["extraValue"] = 0;
    }
    validate_fixed_formation_raw(child);
    return child;
}

}  // namespace kaopt
