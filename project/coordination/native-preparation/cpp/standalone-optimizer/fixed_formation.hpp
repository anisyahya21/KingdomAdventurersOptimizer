#pragma once

#include <nlohmann/json.hpp>

#include <cstdint>
#include <map>
#include <string>
#include <utility>
#include <vector>

namespace kaopt {
using Json = nlohmann::json;

// Fixed-formation policy enforcement (latest explicit user scope, 2026-10-06).
//
// One pinned six-unit formation: Synthetic DPS, fixed healer, four identical fodders. The ONLY
// search variable is the Synthetic DPS's raw stats, inside the derived synthetic walls; roster
// order, placement, fodder/healer numbers, skills and order, triggers, weapons, equipment and
// formation are pinned. Everything is read from the canonical policy document embedded at build
// time, so a historical or otherwise incompatible candidate can never enter the search.

std::string fixed_formation_policy_hash();

// The seven searched Synthetic DPS raw-stat walls: parameter id -> [minimum, maximum].
std::map<std::string, std::pair<std::int64_t, std::int64_t>> fixed_formation_searchable_parameters();

// One proposal arm per searched parameter; used by the search instead of the generic mutation list.
std::vector<Json> fixed_formation_mutation_specs();

// Throws std::runtime_error when the raw scenario is not the one fixed search point for its encounter.
void validate_fixed_formation_raw(const Json& raw);
Json mutate_fixed_formation_stat(const Json& parent, const std::string& parameter, std::int64_t step);

// Canonical identity of everything the policy lets the search vary plus the fight identity.
std::string fixed_formation_identity(const Json& raw);

// Generate one child that changes exactly one Synthetic DPS raw stat, still inside the walls.
Json mutate_fixed_formation_parameter(const Json& parent, const std::string& parameter, std::uint64_t ordinal);

// Process-wide gate: when set, admit(), identity() and the search use the fixed policy. It is set
// once from config before any worker starts; the default keeps the generic optimizer unchanged.
bool fixed_formation_enabled();
void set_fixed_formation_enabled(bool enabled);

}  // namespace kaopt
