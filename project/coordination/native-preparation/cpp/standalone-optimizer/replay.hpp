#pragma once

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#include <cstddef>
#include <cstdint>

#include "preparation.hpp"
#include "../full_battle_abi/ka_battle_report.hpp"

namespace kaopt {

// Observes an already-running canonical native battle. The caller owns tick policy and calls
// capture_tick only after a successful ka_native_full_tick. No battle step is run or inferred here.
// The native event ABI has seven integer fields and no explicit phase tag; replay output preserves
// every tuple in engine order and adds its enclosing tick; a native phase is named only when its
// producer has a uniquely established owner in the canonical tick.
class ReplayCapture {
public:
    ReplayCapture(HMODULE module, void* battle, const Json& prepared);

    // Snapshot the current tick's full native event log and fighter state after a successful step.
    void capture_tick(HMODULE module, void* battle);

    // Build the native replay payload from the captured trace and caller's report. The coordinator
    // adapts its raw KaEvent tuples and sparse frames into the browser's replay schema. The capture
    // is accepted only when its tick/frame range agrees with that report.
    Json finish(HMODULE module, void* battle, const Json& prepared,
                const KaBattleReport& report) const;

private:
    Json execution_;
    Json setup_summary_;
    Json encounter_;
    Json units_;
    Json initial_objects_;
    Json events_;
    Json raw_native_events_;
    Json frames_;
    Json initial_config_;
    Json previous_unit_values_;
    Json previous_objects_;
    std::int32_t last_tick_{};
    std::uint32_t next_sequence_{};
    std::uint32_t captured_tick_count_{};
    std::size_t estimated_payload_bytes_{};
};

} // namespace kaopt
