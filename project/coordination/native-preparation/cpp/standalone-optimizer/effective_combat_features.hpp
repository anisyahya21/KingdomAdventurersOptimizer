#pragma once

#include <nlohmann/json.hpp>

namespace kaopt {
using Json = nlohmann::json;

// Equipment-normalized, mechanics-facing features shared by offline export and runtime ranking.
Json effective_combat_features(const Json& raw, const Json& prepared, const Json& tables);
Json flatten_effective_combat_features(const Json& features);
}
