#pragma once

#include <nlohmann/json.hpp>

namespace kaopt {
using Json = nlohmann::json;

// Prepare the currently supported, explicit special-combat research intent into
// a ka_abi.engine_snapshot-compatible JSON value under result["snapshot"].
// Throws std::runtime_error for unsupported or malformed candidates.
// Probe reports retain pre-refill effective values for parity with strategy_probe.effective_stat.
// The default leaves all ordinary preparation outputs unchanged.
Json prepare(const Json& admitted, const Json& tables, bool includeInputEffectiveParameters = false,
             const Json* sourceRaw = nullptr);
}
