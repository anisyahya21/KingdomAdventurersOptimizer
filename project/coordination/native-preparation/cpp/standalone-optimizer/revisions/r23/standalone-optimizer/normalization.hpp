#pragma once

#include <nlohmann/json.hpp>

namespace kaopt {
using Json = nlohmann::json;

// Port the original wrapper's supported household, recovery-item and declared-start-status
// normalization. Returns a copy of source; optional output maps fighter names to the exact initial
// board keys written by combat_initial_state.expand_start_profile (62=skill, 63=turns, 64=0).
// Keep the unnormalized source separately for provenance/identity. Throws on unsupported or
// malformed declarations rather than guessing household membership or silently dropping input.
Json normalize_scenario_inputs(const Json& source, const Json& tables,
                               Json* initial_status_boards = nullptr);
}
