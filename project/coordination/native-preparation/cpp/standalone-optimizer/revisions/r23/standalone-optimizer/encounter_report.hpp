#pragma once

#include <nlohmann/json.hpp>

#include <array>
#include <cstdint>
#include <stdexcept>

namespace kaopt {

inline constexpr std::int32_t KA_MAX_UNITS = 32;
inline constexpr std::int32_t KA_ENCOUNTER_REPORT_VERSION = 3;

// Field order and signed widths mirror KA-Website/tools/recovery/ka_encounter_abi.py.
struct KaEncounterReport {
    std::int32_t version{};
    std::int32_t own_count{};
    std::int32_t own_identity[KA_MAX_UNITS]{};
    std::int32_t own_first_death_tick[KA_MAX_UNITS]{};
    std::int32_t own_first_leaving_tick[KA_MAX_UNITS]{};
    std::int32_t own_survived[KA_MAX_UNITS]{};
    std::int32_t enemy_resolved_attacks{};
    std::int32_t enemy_hits{};
    std::int32_t enemy_misses{};
    std::int32_t enemy_rolls{};
    std::int32_t enemy_roll_hits{};
    std::int32_t enemy_roll_misses{};
    std::int32_t counter_checks{};
    std::int32_t counter_enqueues{};
    std::int32_t boss_postdeath_attempts{};
    std::int32_t boss_postdeath_lands{};
    std::int32_t boss_death_tick{};
    std::int32_t boss_reentries{};
    std::int32_t boss_leavings{};
    std::int32_t boss_postdeath_gap_count{};
    std::int32_t boss_postdeath_gap_min{};
    std::int32_t boss_postdeath_gap_max{};
    std::int32_t boss_postdeath_gap_sum{};
    std::int32_t future_hits_at_death{};
    std::int32_t future_hits_include_executing{};
    std::int32_t boss_access_first_command{};
    std::int32_t boss_access_first_attempt{};
    std::int32_t targetable_ticks{};
    std::int32_t using_skill_ticks{};
    std::int32_t boss_damaging_resets{};
    std::int32_t item_uses_ok{};
    std::int32_t finish_dispatched_chests{};
};

static_assert(sizeof(KaEncounterReport) == (2 + 4 * KA_MAX_UNITS + 26) * sizeof(std::int32_t),
              "KaEncounterReport ABI size changed");

inline void validate_encounter_report(const KaEncounterReport& r) {
    if (r.version != KA_ENCOUNTER_REPORT_VERSION)
        throw std::runtime_error("encounter report version mismatch");
    if (r.own_count < 0 || r.own_count > KA_MAX_UNITS)
        throw std::runtime_error("encounter report own_count outside ABI capacity");
}

// Mirrors ka_encounter_abi.read(): own[] contains only the reported own_count slots;
// the getter has already applied its supplied finish policy when it filled this struct.
inline nlohmann::json encounter_report_json(const KaEncounterReport& r) {
    validate_encounter_report(r);
    nlohmann::json own = nlohmann::json::array();
    for (std::int32_t slot = 0; slot < r.own_count; ++slot) {
        own.push_back({
            {"identity", r.own_identity[slot]},
            {"firstDeathTick", r.own_first_death_tick[slot]},
            {"firstLeavingTick", r.own_first_leaving_tick[slot]},
            {"survived", r.own_survived[slot] != 0}
        });
    }
    return nlohmann::json{
        {"version", r.version},
        {"own", std::move(own)},
        {"enemyResolvedAttacks", r.enemy_resolved_attacks},
        {"enemyHits", r.enemy_hits},
        {"enemyMisses", r.enemy_misses},
        {"enemyRolls", r.enemy_rolls},
        {"enemyRollHits", r.enemy_roll_hits},
        {"enemyRollMisses", r.enemy_roll_misses},
        {"counterChecks", r.counter_checks},
        {"counterEnqueues", r.counter_enqueues},
        {"bossPostdeathAttempts", r.boss_postdeath_attempts},
        {"bossPostdeathLands", r.boss_postdeath_lands},
        {"bossDeathTick", r.boss_death_tick},
        {"bossReentries", r.boss_reentries},
        {"bossLeavings", r.boss_leavings},
        {"bossPostdeathGapCount", r.boss_postdeath_gap_count},
        {"bossPostdeathGapMin", r.boss_postdeath_gap_min},
        {"bossPostdeathGapMax", r.boss_postdeath_gap_max},
        {"bossPostdeathGapSum", r.boss_postdeath_gap_sum},
        {"futureHitsAtDeath", r.future_hits_at_death},
        {"futureHitsIncludeExecuting", r.future_hits_include_executing},
        {"bossAccessFirstCommand", r.boss_access_first_command},
        {"bossAccessFirstAttempt", r.boss_access_first_attempt},
        {"targetableTicks", r.targetable_ticks},
        {"usingSkillTicks", r.using_skill_ticks},
        {"bossDamagingResets", r.boss_damaging_resets},
        {"itemUsesOk", r.item_uses_ok},
        // The native ABI's historical name is misleading: the adapter reads this as the
        // pending prize queue at the verdict, never as an inventory receipt or actual dispatch.
        {"finishDispatchedChests", r.finish_dispatched_chests}
    };
}

// Lossless inspection form: 32 ABI fields, including all fixed-capacity slot arrays.
inline nlohmann::json encounter_report_fields_json(const KaEncounterReport& r) {
    validate_encounter_report(r);
    const auto int_array = [](const std::int32_t* p) {
        nlohmann::json a = nlohmann::json::array();
        for (std::int32_t i = 0; i < KA_MAX_UNITS; ++i) a.push_back(p[i]);
        return a;
    };
    return nlohmann::json{
        {"version", r.version}, {"own_count", r.own_count},
        {"own_identity", int_array(r.own_identity)},
        {"own_first_death_tick", int_array(r.own_first_death_tick)},
        {"own_first_leaving_tick", int_array(r.own_first_leaving_tick)},
        {"own_survived", int_array(r.own_survived)},
        {"enemy_resolved_attacks", r.enemy_resolved_attacks}, {"enemy_hits", r.enemy_hits},
        {"enemy_misses", r.enemy_misses}, {"enemy_rolls", r.enemy_rolls},
        {"enemy_roll_hits", r.enemy_roll_hits}, {"enemy_roll_misses", r.enemy_roll_misses},
        {"counter_checks", r.counter_checks}, {"counter_enqueues", r.counter_enqueues},
        {"boss_postdeath_attempts", r.boss_postdeath_attempts}, {"boss_postdeath_lands", r.boss_postdeath_lands},
        {"boss_death_tick", r.boss_death_tick}, {"boss_reentries", r.boss_reentries},
        {"boss_leavings", r.boss_leavings}, {"boss_postdeath_gap_count", r.boss_postdeath_gap_count},
        {"boss_postdeath_gap_min", r.boss_postdeath_gap_min}, {"boss_postdeath_gap_max", r.boss_postdeath_gap_max},
        {"boss_postdeath_gap_sum", r.boss_postdeath_gap_sum}, {"future_hits_at_death", r.future_hits_at_death},
        {"future_hits_include_executing", r.future_hits_include_executing},
        {"boss_access_first_command", r.boss_access_first_command},
        {"boss_access_first_attempt", r.boss_access_first_attempt},
        {"targetable_ticks", r.targetable_ticks}, {"using_skill_ticks", r.using_skill_ticks},
        {"boss_damaging_resets", r.boss_damaging_resets}, {"item_uses_ok", r.item_uses_ok},
        {"finish_dispatched_chests", r.finish_dispatched_chests}
    };
}

} // namespace kaopt
