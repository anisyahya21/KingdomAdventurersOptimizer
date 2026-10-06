#include "normalization.hpp"

#include <cstdint>
#include <map>
#include <set>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace kaopt {
namespace {
constexpr std::int64_t I32_MIN_VALUE = -2147483648LL;
constexpr std::int64_t I32_MAX_VALUE = 2147483647LL;
constexpr std::size_t MAX_NATIVE_UNITS = 32;
constexpr std::size_t MAX_NATIVE_ITEMS = 8;
constexpr std::size_t MAX_NATIVE_INPUTS = 32;

void require(bool condition, const std::string& message) {
    if (!condition) throw std::runtime_error(message);
}

bool integer(const Json& value) {
    return value.is_number_integer() && !value.is_boolean();
}

std::int64_t signed32(const Json& value, const std::string& field) {
    require(integer(value), field + " must be an integer");
    const auto n = value.get<std::int64_t>();
    require(n >= I32_MIN_VALUE && n <= I32_MAX_VALUE, field + " exceeds signed32 storage");
    return n;
}

std::string string_value(const Json& value, const std::string& field) {
    require(value.is_string(), field + " must be a string");
    return value.get<std::string>();
}

bool is_owner_human(const Json& unit) {
    return unit.is_object() && unit.contains("human") && unit["human"].is_boolean() &&
           unit["human"].get<bool>() && unit.contains("isHouseOwner") &&
           unit["isHouseOwner"].is_boolean() && unit["isHouseOwner"].get<bool>();
}

bool is_ally_monster(const Json& unit) {
    return unit.is_object() && unit.contains("human") && unit["human"].is_boolean() &&
           !unit["human"].get<bool>() && unit.contains("monsterId") &&
           !unit["monsterId"].is_null();
}

void normalize_households(Json& scenario) {
    require(scenario.contains("ownUnits") && scenario["ownUnits"].is_array(),
            "ownUnits must be an array before household normalization");
    auto& own = scenario["ownUnits"];
    const Json households = scenario.value("housePets", Json::object());
    require(households.is_object(), "housePets must map owner names to ordered pet arrays");

    Json materialized = scenario.value("householdOwners", Json::array());
    require(materialized.is_array(), "householdOwners must be a list of owner names");

    std::set<std::string> selected_names;
    std::vector<std::string> owners;
    for (const auto& unit : own) {
        require(unit.is_object() && unit.contains("name") && unit["name"].is_string(),
                "Every own unit needs a string name before household normalization");
        const auto name = unit["name"].get<std::string>();
        require(selected_names.insert(name).second, "Unit names must be unique");
        if (is_owner_human(unit)) owners.push_back(name);
    }

    std::set<std::string> owner_set(owners.begin(), owners.end());
    std::set<std::string> declared;
    for (const auto& value : materialized) {
        const auto owner = string_value(value, "householdOwners entries");
        require(owner_set.count(owner) != 0,
                "Materialized household '" + owner + "' is not a selected human house owner");
        declared.insert(owner);
    }

    std::map<std::string, std::vector<Json>> inline_pets;
    for (const auto& unit : own) {
        if (!unit.contains("petOwnerName") || unit["petOwnerName"].is_null()) continue;
        const auto owner = string_value(unit["petOwnerName"], "petOwnerName");
        require(owner_set.count(owner) != 0,
                "Inline pet owner '" + owner + "' is not a selected human house owner");
        require(is_ally_monster(unit), "Inline household pets must be ally monsters");
        inline_pets[owner].push_back(unit);
    }

    for (auto it = households.begin(); it != households.end(); ++it) {
        const auto& owner = it.key();
        require(selected_names.count(owner) != 0,
                "Pet owner '" + owner + "' is not a selected unit");
        require(owner_set.count(owner) != 0,
                "Pet owner '" + owner + "' is not a selected human house owner");
        require(inline_pets.count(owner) == 0,
                "'" + owner + "' has both inline petOwnerName units and a housePets entry");
        require(it.value().is_array(), "'" + owner + "' must map to an ordered pet list");
        for (const auto& pet : it.value())
            require(is_ally_monster(pet), "Household pets must be ally monsters");
        declared.insert(owner);
    }
    for (const auto& [owner, pets] : inline_pets) {
        (void)pets;
        declared.insert(owner);
    }

    for (const auto& owner : owners)
        require(declared.count(owner) != 0,
                "Selected human house owner '" + owner +
                "' has no complete household declaration; use housePets[name]=[] for no pets");

    // The original loader appends in selected-owner order, preserving every source pet row's
    // enumeration order and multiplicity. Existing inline rows are already materialized.
    for (const auto& owner : owners) {
        auto found = households.find(owner);
        if (found == households.end()) continue;
        for (const auto& source_pet : found.value()) {
            Json pet = source_pet;
            pet["petOwnerName"] = owner;
            own.push_back(std::move(pet));
        }
    }
    require(own.size() <= MAX_NATIVE_UNITS,
            "Normalized scenario carries more than 32 own units after household pet expansion");

    Json normalized_owners = Json::array();
    for (const auto& owner : owners)
        if (declared.count(owner)) normalized_owners.push_back(owner);
    if (!owners.empty()) scenario["householdOwners"] = std::move(normalized_owners);
    else if (!scenario.contains("householdOwners")) scenario.erase("householdOwners");
    scenario.erase("housePets");
}

void validate_items_and_inputs(const Json& scenario) {
    Json items = scenario.value("items", Json::object());
    Json stock = scenario.value("itemStock", Json::object());
    require(items.is_object(), "items must map names to explicit recovery-item rows");
    require(stock.is_object(), "itemStock must map item names to finite nonnegative counts");
    require(items.size() <= MAX_NATIVE_ITEMS, "Native battle supports at most 8 declared recovery items");

    for (auto it = items.begin(); it != items.end(); ++it) {
        require(!it.key().empty(), "Recovery item names must be nonempty strings");
        const auto& row = it.value();
        require(row.is_object(), "Item rows need a mapping");
        require(row.contains("bonusCategory") && signed32(row["bonusCategory"], "item bonusCategory") == 3,
                "Only bonusCategory 3 recovery items are integrated");
        require(row.contains("bonusType"), "Item row needs integer bonusType");
        const auto bonus_type = signed32(row["bonusType"], "item bonusType");
        require(bonus_type >= 0 && bonus_type <= 5,
                "Only bonusCategory 3 bonusType 0..5 recovery items are recognized");
        require(row.contains("bonusMinValue"), "Item row needs integer bonusMinValue");
        require(row.contains("bonusMaxValue"), "Item row needs integer bonusMaxValue");
        (void)signed32(row["bonusMinValue"], "item bonusMinValue");
        (void)signed32(row["bonusMaxValue"], "item bonusMaxValue");
    }
    for (auto it = stock.begin(); it != stock.end(); ++it) {
        const auto count = signed32(it.value(), "itemStock count");
        require(count >= 0, "itemStock counts must be nonnegative");
    }

    require(scenario.contains("inputs") && scenario["inputs"].is_array(),
            "inputs must be an array");
    const auto& inputs = scenario["inputs"];
    require(inputs.size() <= MAX_NATIVE_INPUTS,
            "Native battle supports at most 32 scheduled input events");
    for (const auto& input : inputs) {
        require(input.is_object(), "Explicit input entries must be objects");
        require(input.contains("tick"), "Input tick is required");
        const auto tick = signed32(input["tick"], "input tick");
        require(tick >= 0, "Input tick must be nonnegative");
        require(input.contains("phase") && input["phase"].is_string() &&
                (input["phase"] == "before_fighters" || input["phase"] == "after_fighters"),
                "Input phase must be before_fighters or after_fighters");
        require(input.contains("type") && input["type"].is_string(),
                "Input type is required");
        const auto type = input["type"].get<std::string>();
        if (type == "holy_herb") continue;
        if (type == "finish")
            throw std::runtime_error("Finish/EXP/teardown inputs are not integrated by the original runner");
        require(type == "item", "Unsupported explicit input type: " + type);
        require(input.contains("item") && input["item"].is_string(),
                "Item input needs a declared item name");
        const auto name = input["item"].get<std::string>();
        require(items.contains(name), "Item input references an undeclared item");
        require(stock.contains(name), "Item input requires an explicit finite stock entry");
        require(input.contains("target") && input["target"] == "all",
                "Battle item input target must be the explicit all-residents scope");
        const auto bonus_type = signed32(items.at(name).at("bonusType"), "item bonusType");
        require(bonus_type == 0 || bonus_type == 2 || bonus_type == 4,
                "Scheduled single-resident item types 1, 3 and 5 are not reachable through the current native all-resident item consumer");
    }
}

Json compute_initial_status_boards(const Json& source, const Json& tables) {
    Json result = Json::object();
    if (!source.contains("startProfile")) return result;
    const auto& profile = source.at("startProfile");
    require(profile.is_object(), "startProfile must be a mapping");
    if (!profile.contains("startingStatus")) return result;
    const auto& statuses = profile.at("startingStatus");
    require(statuses.is_object(), "startingStatus must map fighter names to [skillId,turns]");

    require(tables.is_object() && tables.contains("weapon-skill-profiles") &&
            tables.at("weapon-skill-profiles").is_object() &&
            tables.at("weapon-skill-profiles").contains("skills") &&
            tables.at("weapon-skill-profiles").at("skills").is_array(),
            "tables require weapon-skill-profiles.skills for startingStatus validation");
    std::map<std::int64_t, std::int64_t> skill_types;
    for (const auto& row : tables.at("weapon-skill-profiles").at("skills")) {
        if (!row.is_object() || !row.contains("id") || !row.contains("type")) continue;
        if (integer(row["id"]) && integer(row["type"]))
            skill_types.emplace(row["id"].get<std::int64_t>(), row["type"].get<std::int64_t>());
    }
    for (auto it = statuses.begin(); it != statuses.end(); ++it) {
        require(!it.key().empty(), "startingStatus fighter name must not be empty");
        const auto& pair = it.value();
        require(pair.is_array() && pair.size() == 2 && integer(pair[0]) && integer(pair[1]),
                "startingStatus entries must be [skillId,turns] integer pairs");
        const auto skill = signed32(pair[0], "startingStatus skillId");
        const auto turns = signed32(pair[1], "startingStatus turns");
        auto found = skill_types.find(skill);
        require(found != skill_types.end() && (found->second == 66 || found->second == 67),
                "Only supplied defense-down/sleep status skills are integrated");
        require(turns >= 1, "startingStatus turns must be a positive signed32 value");
        result[it.key()] = Json{{"62", skill}, {"63", turns}, {"64", 0}};
    }
    return result;
}
} // namespace

Json normalize_scenario_inputs(const Json& source, const Json& tables,
                               Json* initial_status_boards) {
    require(source.is_object(), "scenario must be an object");
    Json normalized = source;
    if (source.contains("prePlacement") && source.contains("startProfile"))
        throw std::runtime_error("Supply either captured prePlacement or a declared startProfile, not both");
    Json boards = compute_initial_status_boards(source, tables);
    normalize_households(normalized);
    validate_items_and_inputs(normalized);
    if (initial_status_boards) *initial_status_boards = std::move(boards);
    return normalized;
}
} // namespace kaopt
