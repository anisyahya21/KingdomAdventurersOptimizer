#pragma once

#include <nlohmann/json.hpp>

namespace kaopt {
using Json = nlohmann::json;

// Validate an explicit raw scenario against the supplied runtime profile tables.
// Returns a lossless normalized copy; unsupported representations throw.
Json admit(const Json& scenario, const Json& tables);
}
