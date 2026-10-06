#include "replay.hpp"

#include "native_abi.hpp"

#include <algorithm>
#include <array>
#include <cstdint>
#include <limits>
#include <map>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

namespace kaopt {
namespace {
constexpr std::size_t kReplayPayloadLimit = 32u * 1024u * 1024u;
constexpr std::uint32_t kNativeEventCapacity = 8192;
constexpr std::uint32_t kRngWords = 56;

template <typename Function>
Function resolve(HMODULE module, const char* name) {
    if (!module) throw std::runtime_error("Replay requires the live native kernel module.");
    auto function = reinterpret_cast<Function>(GetProcAddress(module, name));
    if (!function) throw std::runtime_error(std::string("Replay requires native export ") + name + ".");
    return function;
}

using ExportUnits = std::uint32_t(__cdecl*)(void*, KaUnit*);
using EventCount = std::uint32_t(__cdecl*)(const void*);
using ExportEvents = std::uint32_t(__cdecl*)(const void*, KaEvent*);
using Counters = void(__cdecl*)(const void*, std::uint32_t*, std::int32_t*, std::int32_t*);
using RngPointer = const void*(__cdecl*)(const void*);
using RngIndex = std::int32_t(__cdecl*)(const void*);
using RngValue = std::int32_t(__cdecl*)(const void*, std::uint32_t);
using DrawCount = std::uint32_t(__cdecl*)(const void*);
using ItemCount = std::uint32_t(__cdecl*)(const void*);
using ItemRead = std::int32_t(__cdecl*)(const void*, std::uint32_t, std::int32_t*);
using UseCount = std::uint32_t(__cdecl*)(const void*);
using UseRead = std::int32_t(__cdecl*)(const void*, std::uint32_t, std::int32_t*);
using HerbStock = std::int32_t(__cdecl*)(const void*);
using ObjectCount = std::uint32_t(__cdecl*)(const void*);
using ObjectEntity = std::int32_t(__cdecl*)(const void*, std::uint32_t, KaEntity*);

std::vector<KaUnit> read_units(HMODULE module, void* battle, std::size_t expected) {
    const auto export_fn = resolve<ExportUnits>(module, "ka_battle_export");
    if (expected == 0 || expected > 32) throw std::runtime_error("Replay roster is outside native capacity.");
    std::vector<KaUnit> units(expected);
    const auto copied = export_fn(battle, units.data());
    if (copied != expected) throw std::runtime_error("Replay fighter export count changed.");
    return units;
}

std::size_t roster_size(const Json& prepared) {
    return prepared.at("snapshot").at("units").size();
}

std::int32_t parameter_value(const KaUnit& unit, std::int32_t parameter) {
    const bool human = unit.human != 0;
    const bool ally = unit.team == 0;
    const auto count = std::min<std::uint32_t>(unit.params.count, 16);
    const KaParam* row = nullptr;
    for (std::uint32_t i = 0; i < count; ++i) {
        if (unit.params.rows[i].id == parameter) {
            row = &unit.params.rows[i];
            break;
        }
    }
    if (!row) return 0;
    const auto wrap_i32 = [](std::int64_t value) -> std::int32_t {
        const auto low = static_cast<std::uint32_t>(static_cast<std::uint64_t>(value));
        return low <= static_cast<std::uint32_t>(std::numeric_limits<std::int32_t>::max())
            ? static_cast<std::int32_t>(low)
            : static_cast<std::int32_t>(static_cast<std::int64_t>(low) - 0x100000000LL);
    };
    const bool bounded = row->raw_max != std::numeric_limits<std::int32_t>::max();
    std::int32_t value = wrap_i32(static_cast<std::int64_t>(row->raw_value) + row->extra_value);
    if (!bounded) {
        const auto equipment_count = std::min<std::uint32_t>(unit.params.equipment_count, 8);
        for (std::uint32_t i = 0; i < equipment_count; ++i) {
            const auto& equipment = unit.params.equipment[i];
            const auto index = parameter - 10;
            if (index < 0 || index >= equipment.pair_count || index >= 16 ||
                equipment.present[index] == 0) continue;
            const auto selected_level = human && !ally ? equipment.pvp_level : equipment.level;
            const auto level = selected_level > 0 ? selected_level : equipment.level;
            const auto level_delta = wrap_i32(static_cast<std::int64_t>(level) - 1);
            const auto growth = wrap_i32(static_cast<std::int64_t>(equipment.pairs[index][1]) * level_delta);
            auto contribution = wrap_i32(static_cast<std::int64_t>(equipment.pairs[index][0]) + growth);
            if (human && equipment.affinity == 0) {
                contribution = contribution > 0 ? std::max<std::int32_t>(contribution / 2, 1) : 0;
            }
            value = wrap_i32(static_cast<std::int64_t>(value) + contribution);
        }
    }
    // Native current-value lookup adds equipment only for unbounded parameters, then clamps
    // against the separately sourced maximum for bounded parameters.
    std::int32_t maximum = row->raw_max;
    if (bounded) {
        maximum = wrap_i32(static_cast<std::int64_t>(row->raw_max) + row->extra_max);
        const auto equipment_count = std::min<std::uint32_t>(unit.params.equipment_count, 8);
        for (std::uint32_t i = 0; i < equipment_count; ++i) {
            const auto& equipment = unit.params.equipment[i];
            const auto index = parameter - 10;
            if (index < 0 || index >= equipment.pair_count || index >= 16 ||
                equipment.present[index] == 0) continue;
            const auto selected_level = human && !ally ? equipment.pvp_level : equipment.level;
            const auto level = selected_level > 0 ? selected_level : equipment.level;
            const auto level_delta = wrap_i32(static_cast<std::int64_t>(level) - 1);
            const auto growth = wrap_i32(static_cast<std::int64_t>(equipment.pairs[index][1]) * level_delta);
            auto contribution = wrap_i32(static_cast<std::int64_t>(equipment.pairs[index][0]) + growth);
            if (human && equipment.affinity == 0) {
                contribution = contribution > 0 ? std::max<std::int32_t>(contribution / 2, 1) : 0;
            }
            maximum = wrap_i32(static_cast<std::int64_t>(maximum) + contribution);
        }
    }
    if (parameter == 25) return value;
    return std::max<std::int32_t>(0, std::min(value, maximum));
}

std::optional<std::int32_t> native_parameter_maximum(const KaUnit& unit, std::int32_t parameter) {
    const auto count = std::min<std::uint32_t>(unit.params.count, 16);
    const KaParam* row = nullptr;
    for (std::uint32_t i = 0; i < count; ++i) {
        if (unit.params.rows[i].id == parameter) {
            row = &unit.params.rows[i];
            break;
        }
    }
    if (!row) return std::nullopt;
    if (row->raw_max == std::numeric_limits<std::int32_t>::max()) return row->raw_max;
    const auto wrap_i32 = [](std::int64_t value) -> std::int32_t {
        const auto low = static_cast<std::uint32_t>(static_cast<std::uint64_t>(value));
        return low <= static_cast<std::uint32_t>(std::numeric_limits<std::int32_t>::max())
            ? static_cast<std::int32_t>(low)
            : static_cast<std::int32_t>(static_cast<std::int64_t>(low) - 0x100000000LL);
    };
    std::int32_t value = wrap_i32(static_cast<std::int64_t>(row->raw_max) + row->extra_max);
    const bool human = unit.human != 0;
    const bool ally = unit.team == 0;
    const auto equipment_count = std::min<std::uint32_t>(unit.params.equipment_count, 8);
    for (std::uint32_t i = 0; i < equipment_count; ++i) {
        const auto& equipment = unit.params.equipment[i];
        const auto index = parameter - 10;
        if (index < 0 || index >= equipment.pair_count || index >= 16 ||
            equipment.present[index] == 0) continue;
        const auto selected_level = human && !ally ? equipment.pvp_level : equipment.level;
        const auto level = selected_level > 0 ? selected_level : equipment.level;
        const auto level_delta = wrap_i32(static_cast<std::int64_t>(level) - 1);
        const auto growth = wrap_i32(static_cast<std::int64_t>(equipment.pairs[index][1]) * level_delta);
        auto contribution = wrap_i32(static_cast<std::int64_t>(equipment.pairs[index][0]) + growth);
        if (human && equipment.affinity == 0) {
            contribution = contribution > 0 ? std::max<std::int32_t>(contribution / 2, 1) : 0;
        }
        value = wrap_i32(static_cast<std::int64_t>(value) + contribution);
    }
    return value;
}

std::optional<std::int32_t> fighter_state(const KaUnit& unit) {
    const auto count = std::min<std::uint32_t>(unit.board.len, 48);
    for (std::uint32_t i = 0; i < count; ++i) {
        if (unit.board.entries[i].key == 5) {
            return static_cast<std::int32_t>(unit.board.entries[i].value);
        }
    }
    return std::nullopt;
}

const char* state_name(std::int32_t state) {
    switch (state) {
    case 1: return "waiting";
    case 2: return "moving";
    case 3: return "charging";
    case 4: return "attacking";
    case 5: return "using_skill";
    case 6: return "damaging";
    case 7: return "knocking_down";
    case 8: return "leaving";
    default: return "";
    }
}

Json native_position(const KaEntity& body) {
    if (!body.has[0]) return nullptr;
    return Json::array({body.position.x, body.position.y, body.position.z,
                        body.offset.x, body.offset.y, body.offset.z,
                        body.parent < 0 ? Json(nullptr) : Json(body.parent)});
}

const char* event_name(std::int32_t kind) {
    switch (kind) {
    case 1: return "state";
    case 2: return "enqueue";
    case 3: return "invocation";
    case 4: return "animation";
    case 5: return "use";
    case 6: return "attack";
    case 7: return "mp_pay";
    case 8: return "create";
    case 9: return "destroy";
    case 10: return "hp";
    case 11: return "heal";
    case 12: return "status";
    case 13: return "prize";
    case 14: return "invoking";
    case 15: return "area_cell";
    case 16: return "state_sound";
    case 18: return "body_flight";
    case 19: return "release";
    case 20: return "projectile_launch";
    case 21: return "revive";
    case 22: return "status_text";
    case 23: return "attack_batch";
    case 24: return "cell_change";
    case 25: return "status_tick";
    case 26: return "body_impact";
    case 27: return "projectile_impact";
    case 28: return "projectile_cleanup";
    case 29: return "verdict";
    case 30: return "resource_change";
    case 31: return "battle_item";
    default: return nullptr;
    }
}

struct NativePhase {
    const char* name;
    std::int32_t index;
};

NativePhase event_phase(const KaEvent& event, std::size_t event_index,
                        const std::optional<std::size_t>& verdict_index) {
    // battle_control::tick resets the log once, then appends events from multiple phases to this
    // shared buffer. KaEvent has no phase field. Only the verdict event has a uniquely known
    // producer (tick_head); assigning a phase to other event kinds would invent chronology.
    if (event.kind == 29 && verdict_index && event_index == *verdict_index) {
        return {"tick_head", 0};
    }
    return {nullptr, -1};
}

Json native_event_json(const KaEvent& event, std::int32_t tick, std::uint32_t sequence,
                       std::size_t event_index,
                       const std::optional<std::size_t>& verdict_index,
                       const Json& units,
                       const std::optional<std::int32_t>& hp_after) {
    const auto* name = event_name(event.kind);
    if (!name) throw std::runtime_error("Native replay encountered an unknown event kind.");
    const auto phase = event_phase(event, event_index, verdict_index);
    Json out{{"seq", sequence}, {"tick", tick}, {"phase", "battle"},
                {"captureStage", "native-full-tick"},
                {"nativePhase", phase.name ? Json(phase.name) : Json(nullptr)},
                {"nativePhaseIndex", phase.index},
                {"nativePhaseReason", phase.name ?
                    Json("The verdict event is emitted only by battle_control::tick_head.") :
                    Json("KaEvent has no phase field and the native full-tick event log merges multiple phases; exact emitter phase is unavailable.")},
                {"kind", name}, {"nativeKind", event.kind}, {"unit", event.unit},
                {"a", event.a}, {"b", event.b}, {"c", event.c}, {"d", event.d},
                {"e", event.e},
                {"rawTuple", Json::array({event.kind, event.unit, event.a, event.b,
                                           event.c, event.d, event.e})}};
    auto unit_id = [&units](std::int32_t entity_id) -> Json {
        for (const auto& unit : units) {
            if (unit.value("entityId", std::numeric_limits<std::int32_t>::min()) == entity_id) {
                return unit.at("unitId");
            }
        }
        return nullptr;
    };
    switch (event.kind) {
    case 1:
        out["targetUnitId"] = unit_id(event.unit);
        out["old"] = event.a;
        out["new"] = event.b;
        out["hp"] = event.c;
        if (event.b >= 1 && event.b <= 8) out["stateName"] = state_name(event.b);
        else out["stateName"] = nullptr;
        break;
    case 4:
        out["kind"] = "animation_request";
        out["targetUnitId"] = unit_id(event.unit);
        out["behavior"] = event.a;
        out["clip"] = event.b;
        break;
    case 6:
        out["attackerUnitId"] = unit_id(event.unit);
        out["targetUnitId"] = unit_id(event.a);
        out["skillId"] = event.b < 0 ? Json(nullptr) : Json(event.b);
        out["hit"] = event.c != 0;
        out["critical"] = event.d != 0;
        out["damage"] = event.e;
        if (hp_after) out["hpAfter"] = *hp_after;
        break;
    case 7:
        out["kind"] = "mp";
        out["casterUnitId"] = unit_id(event.unit);
        out["skillId"] = event.a < 0 ? Json(nullptr) : Json(event.a);
        out["before"] = event.b;
        out["amount"] = event.c;
        // The emitter stores the raw parameter with a lower clamp. Its effective value can also
        // depend on extra/equipment rows and an upper bound, so the tuple alone cannot supply the
        // post-payment effective MP. Leave it unknown rather than subtracting from `before`.
        out["after"] = nullptr;
        out["afterReason"] = "MP-pay tuple records effective before and amount, while pay mutates raw MP; post-pay effective MP is not in the tuple.";
        break;
    case 11:
        out["casterUnitId"] = unit_id(event.unit);
        out["targetUnitId"] = unit_id(event.a);
        out["skillId"] = event.b < 0 ? Json(nullptr) : Json(event.b);
        out["amount"] = event.c;
        out["before"] = event.d;
        out["after"] = event.e;
        break;
    case 15:
        out["kind"] = "cell";
        out["casterUnitId"] = unit_id(event.unit);
        out["skillId"] = event.a < 0 ? Json(nullptr) : Json(event.a);
        out["cell"] = Json::array({event.b, event.c});
        break;
    case 19:
        out["casterUnitId"] = unit_id(event.unit);
        out["targetUnitId"] = unit_id(event.a);
        out["skillId"] = event.b < 0 ? Json(nullptr) : Json(event.b);
        out["index"] = event.c;
        out["used"] = event.d != 0;
        out["rowId"] = event.e;
        break;
    case 24:
        out["targetUnitId"] = unit_id(event.unit);
        out["oldKey"] = event.a;
        out["cell"] = Json::array({event.b, event.c});
        break;
    case 30:
        out["targetUnitId"] = unit_id(event.unit);
        out["parameter"] = event.a;
        out["after"] = event.b;
        out["before"] = event.c;
        out["sourceItemSlot"] = event.d;
        out["max"] = event.e;
        // -1 is the native Holy Herb source. Consumable slots are preserved as slot indices;
        // a display item id is not part of this event tuple.
        out["sourceItem"] = event.d == -1 ? Json("holy-herb") : Json(nullptr);
        break;
    default:
        break;
    }
    return out;
}

Json raw_native_event_json(const KaEvent& event, std::int32_t tick, std::uint32_t sequence) {
    return Json{{"tick", tick}, {"seq", sequence}, {"nativeKind", event.kind},
                {"rawTuple", Json::array({event.kind, event.unit, event.a, event.b,
                                           event.c, event.d, event.e})}};
}

std::vector<std::optional<std::int32_t>> correlate_attack_hp(
        const std::vector<KaEvent>& events) {
    std::vector<std::optional<std::int32_t>> hp_after(events.size());
    std::vector<bool> used(events.size(), false);
    for (std::size_t hp_index = 0; hp_index < events.size(); ++hp_index) {
        const auto& hp = events[hp_index];
        if (hp.kind != 10) continue;
        for (std::size_t attack_index = 0; attack_index < hp_index; ++attack_index) {
            const auto& attack = events[attack_index];
            if (!used[attack_index] && attack.kind == 6 && attack.a == hp.unit &&
                attack.e == hp.a) {
                hp_after[attack_index] = hp.b;
                used[attack_index] = true;
                break;
            }
        }
    }
    return hp_after;
}

Json typed_parameters(const Json& source, const KaUnit& native) {
    Json result = Json::object();
    if (!source.is_object() || !source.contains("parameters") ||
        !source.at("parameters").is_object()) return result;
    const Json& raw_parameters = source.at("parameters");
    const auto nullable = [](const Json& row, const char* key) -> Json {
        if (!row.is_object() || !row.contains(key)) return nullptr;
        return row.at(key);
    };
    for (auto it = raw_parameters.begin(); it != raw_parameters.end(); ++it) {
        const Json& row = it.value();
        Json raw{{"rawValue", nullable(row, "rawValue")},
                 {"rawMax", nullable(row, "rawMax")},
                 {"extraValue", nullable(row, "extraValue")},
                 {"extraMax", nullable(row, "extraMax")},
                 {"trainingLevel", nullable(row, "trainingLevel")}};
        Json value = nullptr;
        Json maximum = nullptr;
        const auto native_maximum = native_parameter_maximum(native, std::stoi(it.key()));
        if (native_maximum) {
            value = parameter_value(native, std::stoi(it.key()));
            maximum = *native_maximum;
        }
        result[it.key()] = Json{{"raw", std::move(raw)},
                                {"effectiveValue", std::move(value)},
                                {"effectiveMaximum", std::move(maximum)}};
    }
    return result;
}

Json unit_snapshot(const KaUnit& unit) {
    const auto state = fighter_state(unit);
    return Json{{"entityId", unit.identity}, {"hp", parameter_value(unit, 10)},
                {"mp", parameter_value(unit, 11)},
                {"state", state ? Json(*state) : Json(nullptr)},
                {"stateName", state && *state >= 1 && *state <= 8
                                  ? Json(state_name(*state)) : Json(nullptr)},
                {"cell", Json::array({unit.body.cell[0], unit.body.cell[1]})},
                {"nativePosition", native_position(unit.body)}};
}

Json packed_frame_units(const std::vector<KaUnit>& units) {
    Json rows = Json::array();
    for (const auto& unit : units) {
        const auto state = fighter_state(unit);
        rows.push_back(Json::array({unit.identity, parameter_value(unit, 10),
                                    parameter_value(unit, 11),
                                    state ? Json(*state) : Json(nullptr),
                                    unit.body.cell[0], unit.body.cell[1],
                                    native_position(unit.body)}));
    }
    return rows;
}

Json vec3(const KaVec3& value) {
    return Json::array({value.x, value.y, value.z});
}

Json entity_components(const KaEntity& entity) {
    Json components = Json{{"position", nullptr}, {"speed", nullptr}, {"seb", nullptr},
                          {"depth", nullptr}, {"cell", nullptr}, {"image", nullptr},
                          {"animation", nullptr}, {"direction", nullptr},
                          {"modifier", nullptr}, {"garbage", nullptr}, {"effect", nullptr},
                          {"projectile", nullptr}, {"attack", nullptr}};
    if (entity.has[0]) {
        components["position"] = Json::array({entity.position.x, entity.position.y,
            entity.position.z, entity.offset.x, entity.offset.y, entity.offset.z,
            entity.parent < 0 ? Json(nullptr) : Json(entity.parent)});
    }
    if (entity.has[1]) components["speed"] = vec3(entity.speed);
    if (entity.has[2]) components["seb"] = Json::array({entity.seb[0], entity.seb[1], entity.seb[2], entity.seb[3]});
    if (entity.has[4]) components["depth"] = Json::array({entity.depth[0], entity.depth[1]});
    if (entity.has[5]) components["cell"] = Json::array({entity.cell[0], entity.cell[1]});
    if (entity.has[7]) components["image"] = Json::array({entity.image[0], entity.image[1], entity.image[2],
        entity.image[3], entity.image[4], entity.image[5]});
    if (entity.has[12]) components["animation"] = Json::array({entity.animation[0], entity.animation[1]});
    if (entity.has[14]) components["direction"] = entity.direction;
    if (entity.has[19]) {
        const auto& m = entity.modifier;
        components["modifier"] = Json{{"type", m.type_}, {"offset_x", m.offset_x},
            {"offset_y", m.offset_y}, {"offset_z", m.offset_z}, {"scale_x", m.scale_x},
            {"scale_y", m.scale_y}, {"angle", m.angle}, {"anchor", m.anchor},
            {"frame", m.frame}, {"duration", m.duration},
            {"destroy_on_finish", m.destroy_on_finish != 0}, {"loop", m.looping != 0},
            {"alpha", m.alpha}};
    }
    if (entity.has[32]) components["garbage"] = entity.garbage;
    if (entity.has[38]) {
        const auto& e = entity.effect;
        components["effect"] = Json{{"type", e.type_}, {"value1", e.value1},
            {"value2", e.value2}, {"depth", e.depth != 0}, {"frame", e.frame},
            {"max_frame", e.max_frame}, {"parent", e.parent < 0 ? Json(nullptr) : Json(e.parent)},
            {"scale", e.scale}};
    }
    if (entity.has[39]) {
        const auto& p = entity.projectile;
        components["projectile"] = Json{{"start", vec3(p.start)}, {"end", vec3(p.end)},
            {"speed", p.speed}, {"height", p.height}, {"frame", p.frame},
            {"length", p.length}, {"owner", p.owner}};
    }
    if (entity.has[46]) components["attack"] = entity.attack;
    return components;
}

Json read_objects(HMODULE module, void* battle) {
    const auto count_fn = resolve<ObjectCount>(module, "ka_object_count");
    const auto read_fn = resolve<ObjectEntity>(module, "ka_object_entity");
    const auto count = count_fn(battle);
    // Bound the object snapshots independently of the eventual serialized replay size. A
    // large-arena kernel can expose 262144 slots; duplicating that full object graph for the
    // previous/current comparison would exceed the replay memory budget before the JSON cap runs.
    if (count > 32768) throw std::runtime_error("Native replay object count exceeds the bounded 32768-object capture limit.");
    Json objects = Json::array();
    for (std::uint32_t slot = 0; slot < count; ++slot) {
        KaEntity entity{};
        if (read_fn(battle, slot, &entity) != 0) throw std::runtime_error("Native replay object export failed.");
        Json present_slots = Json::array();
        for (std::int32_t component = 0; component < 52; ++component) {
            if (entity.has[component] != 0) present_slots.push_back(component);
        }
        objects.push_back(Json{{"id", entity.id}, {"destroyed", entity.destroyed != 0},
                               {"present_slots", std::move(present_slots)},
                               {"components", entity_components(entity)}});
    }
    return objects;
}

Json changed_unit_rows(const Json& previous, const Json& current) {
    if (previous.size() != current.size()) {
        throw std::runtime_error("Native replay fighter roster changed during execution.");
    }
    Json changed = Json::array();
    for (std::size_t i = 0; i < current.size(); ++i) {
        if (previous[i] != current[i]) changed.push_back(current[i]);
    }
    return changed;
}

Json changed_object_rows(const Json& previous, const Json& current) {
    std::map<std::int32_t, const Json*> prior_by_id;
    for (const auto& item : previous) prior_by_id[item.at("id").get<std::int32_t>()] = &item;
    Json changed = Json::array();
    for (const auto& item : current) {
        const auto id = item.at("id").get<std::int32_t>();
        const auto old = prior_by_id.find(id);
        if (old == prior_by_id.end()) {
            // A newly allocated object is copied in full from the native arena.
            changed.push_back(item);
            continue;
        }
        const Json& before = *old->second;
        Json delta{{"id", id}};
        bool differs = false;
        for (const char* key : {"destroyed", "present_slots"}) {
            if (before.at(key) != item.at(key)) {
                delta[key] = item.at(key);
                differs = true;
            }
        }
        Json component_changes = Json::object();
        const auto& before_components = before.at("components");
        const auto& after_components = item.at("components");
        for (auto it = after_components.begin(); it != after_components.end(); ++it) {
            if (!before_components.contains(it.key()) || before_components.at(it.key()) != it.value()) {
                component_changes[it.key()] = it.value();
                differs = true;
            }
        }
        if (!component_changes.empty()) delta["components"] = std::move(component_changes);
        if (differs) changed.push_back(std::move(delta));
    }
    return changed;
}

Json unit_metadata(const KaUnit& native, std::size_t ally_index, std::size_t enemy_index,
                   const Json& prepared) {
    const bool ally = native.team == 0;
    const Json& roster = ally ? prepared.at("ownUnits") : prepared.at("enemies");
    const auto roster_index = ally ? ally_index : enemy_index;
    if (roster_index >= roster.size()) {
        throw std::runtime_error("Replay native roster cannot be matched to prepared scenario units.");
    }
    const Json& source = roster[roster_index];
    const std::string side = ally ? "ally" : "enemy";
    Json out{{"unitId", side + ":" + std::to_string(roster_index)},
             {"side", side}, {"rosterIndex", roster_index}, {"entityId", native.identity},
             {"name", source.value("name", side + ":" + std::to_string(roster_index))},
             {"kind", native.human ? "human" : "monster"}, {"human", native.human != 0},
             {"monsterId", source.is_object() && source.contains("monsterId")
                               ? source.at("monsterId") : Json(nullptr)},
             {"startHp", parameter_value(native, 10)}, {"startMp", parameter_value(native, 11)},
             {"cell", Json::array({native.body.cell[0], native.body.cell[1]})},
             {"nativePosition", native_position(native.body)},
             {"sourceUnit", source}};
    for (const char* key : {"weaponId", "skills", "levels", "equipment",
                            "effectiveParameters", "weapon", "averageTrainingLevel", "priority",
                            "formationValue", "grid", "column", "row", "leaderIdentity", "boss", "level",
                            "rank", "visitor", "ownerPlayer", "humanFlags", "isHouseOwner",
                            "petOwnerName"}) {
        if (source.is_object() && source.contains(key)) out[key] = source[key];
    }
    out["parameters"] = typed_parameters(source, native);
    if (ally) {
        out["skillIds"] = source.value("skills", Json::array());
        out["invocationLevels"] = source.value("levels", Json::array());
    } else {
        out["skillIds"] = source.value("skills", Json::array());
        out["invocationLevels"] = Json::array();
        out["boss"] = source.value("leaderIdentity", false);
    }
    out["weapon"] = Json{{"type", native.weapon_type},
                         {"shootingRange", native.weapon_shooting_range},
                         {"motion", native.weapon_motion},
                         {"projectileFlag", native.weapon_projectile_flag}};
    out["skillIds"] = Json::array();
    for (std::uint32_t i = 0; i < std::min<std::uint32_t>(native.skill_count, 12); ++i) {
        out["skillIds"].push_back(native.skill_ids[i]);
    }
    out["invocationLevels"] = Json::array();
    for (std::uint32_t i = 0; i < std::min<std::uint32_t>(native.level_count, 12); ++i) {
        out["invocationLevels"].push_back(native.levels[i]);
    }
    if (source.contains("effectiveParameters")) out["effectiveParameters"] = source["effectiveParameters"];
    return out;
}

Json build_unit_metadata(const std::vector<KaUnit>& native_units, const Json& prepared) {
    Json result = Json::array();
    std::size_t ally_index = 0;
    std::size_t enemy_index = 0;
    for (const auto& native : native_units) {
        result.push_back(unit_metadata(native, ally_index, enemy_index, prepared));
        if (native.team == 0) ++ally_index;
        else ++enemy_index;
    }
    if (ally_index != prepared.at("ownUnits").size() ||
        enemy_index != prepared.at("enemies").size()) {
        throw std::runtime_error("Replay prepared and native team roster counts differ.");
    }
    return result;
}

Json rng_state(HMODULE module, void* battle) {
    const auto math_pointer = resolve<RngPointer>(module, "ka_battle_math_rng")(battle);
    const auto lib_pointer = resolve<RngPointer>(module, "ka_battle_lib_rng")(battle);
    if (!math_pointer || !lib_pointer) throw std::runtime_error("Native replay RNG export returned null.");
    const auto index_fn = resolve<RngIndex>(module, "ka_rng_index");
    const auto partner_fn = resolve<RngIndex>(module, "ka_rng_partner");
    const auto value_fn = resolve<RngValue>(module, "ka_rng_value");
    const auto draws_fn = resolve<DrawCount>(module, "ka_battle_math_draws");
    const auto lib_draws_fn = resolve<DrawCount>(module, "ka_battle_lib_draws");
    Json streams = Json::object();
    for (const auto& entry : {std::pair<const char*, const void*>{"math", math_pointer},
                              std::pair<const char*, const void*>{"lib", lib_pointer}}) {
        Json values = Json::array();
        for (std::uint32_t i = 0; i < kRngWords; ++i) values.push_back(value_fn(entry.second, i));
        streams[entry.first] = Json{{"values", std::move(values)},
                                    {"index", index_fn(entry.second)},
                                    {"partner", partner_fn(entry.second)}};
    }
    streams["math"]["draws"] = draws_fn(battle);
    streams["lib"]["draws"] = lib_draws_fn(battle);
    return streams;
}

Json item_remaining(HMODULE module, void* battle) {
    const auto count_fn = resolve<ItemCount>(module, "ka_battle_item_count");
    const auto item_fn = resolve<ItemRead>(module, "ka_battle_item");
    Json result = Json::array();
    const auto count = count_fn(battle);
    if (count > 8) throw std::runtime_error("Native replay item count exceeds ABI capacity.");
    for (std::uint32_t slot = 0; slot < count; ++slot) {
        std::array<std::int32_t, 6> row{};
        if (item_fn(battle, slot, row.data()) != 0) throw std::runtime_error("Native replay item export failed.");
        result.push_back(Json{{"id", row[0]}, {"parameter", row[1]},
                              {"allResidents", row[2] != 0}, {"bonusMin", row[3]},
                              {"bonusMax", row[4]}, {"stock", row[5]}});
    }
    return result;
}

Json item_uses(HMODULE module, void* battle) {
    const auto count_fn = resolve<UseCount>(module, "ka_use_log_count");
    const auto read_fn = resolve<UseRead>(module, "ka_use_log");
    Json result = Json::array();
    const auto count = count_fn(battle);
    if (count > 64) throw std::runtime_error("Native replay consumable log exceeds ABI capacity.");
    for (std::uint32_t slot = 0; slot < count; ++slot) {
        std::array<std::int32_t, 6> row{};
        if (read_fn(battle, slot, row.data()) != 0) throw std::runtime_error("Native replay use log export failed.");
        result.push_back(Json{{"kind", row[0]}, {"item", row[1]}, {"tick", row[2]},
                              {"used", row[3] != 0}, {"percent", row[4]},
                              {"remaining", row[5]}});
    }
    return result;
}

void copy_if_present(Json& target, const Json& source, const char* key) {
    if (source.is_object() && source.contains(key)) target[key] = source[key];
}

Json state_names() {
    return Json{{"1", "waiting"}, {"2", "moving"}, {"3", "charging"},
                {"4", "attacking"}, {"5", "using_skill"}, {"6", "damaging"},
                {"7", "knocking_down"}, {"8", "leaving"}};
}

Json skill_catalog(const Json& prepared) {
    Json skills = Json::object();
    const auto& rows = prepared.at("snapshot").at("rows");
    for (auto it = rows.begin(); it != rows.end(); ++it) {
        const auto& row = it.value();
        Json skill = Json::object();
        for (const char* key : {"id", "category", "type", "value", "motion", "minMp",
                                "maxMp", "requiredEquipType", "shootingRange", "range", "count"}) {
            copy_if_present(skill, row, key);
        }
        skill["source"] = "bundled native skill row";
        skills[it.key()] = std::move(skill);
    }
    return skills;
}

Json execution_identity(const Json& prepared) {
    Json identity = Json::object();
    if (prepared.contains("rawIntent") && prepared["rawIntent"].is_object()) {
        const auto& intent = prepared["rawIntent"];
        for (const char* key : {"encounterId", "defeatCount", "mathSeed", "libSeed",
                                "tickLimit", "finishPolicy"}) {
            copy_if_present(identity, intent, key);
        }
    }
    for (const char* key : {"tickLimit", "finishPolicy", "policyCode"}) {
        copy_if_present(identity, prepared, key);
    }
    return identity;
}

Json setup_summary(const Json& prepared) {
    const Json& intent = prepared.at("rawIntent");
    Json summary = Json::object();
    for (const char* key : {"encounterId", "defeatCount", "mathSeed", "libSeed",
                            "tickLimit", "holyHerbStock", "inputs", "items", "itemStock",
                            "prePlacement", "startProfile"}) {
        copy_if_present(summary, intent, key);
    }
    summary["finishPolicy"] = prepared.value("finishPolicy", std::string("at-horizon"));
    summary["finishPolicyEligible"] = summary["finishPolicy"] != "on-verdict";
    summary["ownUnitCount"] = prepared.at("ownUnits").size();
    summary["enemyUnitCount"] = prepared.at("enemies").size();
    return summary;
}

Json encounter_summary(const Json& prepared) {
    Json enemy_ids = Json::array();
    for (const auto& enemy : prepared.at("enemies")) {
        enemy_ids.push_back(enemy.is_object() && enemy.contains("monsterId")
                                ? enemy.at("monsterId") : Json(nullptr));
    }
    Json encounter = Json::object();
    encounter["encounterId"] = prepared.at("config").value("encounterId", 0);
    encounter["defeatCount"] = prepared.at("config").value("defeatCount", 0);
    encounter["title"] = nullptr;
    encounter["level"] = prepared.at("config").contains("level")
                              ? prepared.at("config").at("level") : Json(nullptr);
    encounter["followerSelectionDraws"] = prepared.contains("followerSelectionDraws")
                                               ? prepared.at("followerSelectionDraws") : Json(nullptr);
    encounter["enemyMonsterIds"] = std::move(enemy_ids);
    encounter["ownFormationOrder"] = prepared.value("ownFormationOrder", Json::array());
    encounter["formationOrder"] = prepared.value("enemyFormationOrder", Json::array());
    return encounter;
}

Json final_units(const std::vector<KaUnit>& native_units) {
    Json result = Json::array();
    std::size_t ally_index = 0;
    std::size_t enemy_index = 0;
    for (const auto& native : native_units) {
        const bool ally = native.team == 0;
        const auto index = ally ? ally_index++ : enemy_index++;
        const std::string side = ally ? "ally" : "enemy";
        Json unit = unit_snapshot(native);
        unit["unitId"] = side + ":" + std::to_string(index);
        unit["side"] = side;
        unit["rosterIndex"] = index;
        result.push_back(std::move(unit));
    }
    return result;
}

std::optional<std::int32_t> last_tick_with_prize(const Json& events) {
    std::optional<std::int32_t> last;
    for (const auto& event : events) {
        if (event.value("nativeKind", 0) == 13) last = event.at("tick").get<std::int32_t>();
    }
    return last;
}

const char* finish_policy_name(std::int32_t policy) {
    switch (policy) {
    case 0: return "at-horizon";
    case 1: return "after-ending";
    case 2: return "on-verdict";
    default: return "unknown";
    }
}

Json report_state(HMODULE module, void* battle, const Json& prepared,
                  const KaBattleReport& report, const Json& events,
                  const std::vector<KaUnit>& native_units) {
    const std::int32_t verdict_tick = report.verdict_tick < 0 ? -1 : report.verdict_tick;
    const std::int32_t horizon_tick = report.ticks > 0 ? report.ticks - 1 : -1;
    std::int32_t cut_tick = horizon_tick;
    if (!events.empty()) {
        cut_tick = -1;
        for (const auto& event : events) cut_tick = std::max(cut_tick, event.at("tick").get<std::int32_t>());
    } else if (verdict_tick >= 0) {
        cut_tick = verdict_tick;
    }
    Json state = Json::object();
    state["verdict"] = report.verdict;
    state["battleState"] = report.battle_state;
    state["battleFrame"] = report.battle_frame;
    state["ticks"] = report.ticks;
    state["verdictTick"] = verdict_tick < 0 ? Json(nullptr) : Json(verdict_tick);
    state["finishTick"] = nullptr;
    state["finishPolicy"] = finish_policy_name(report.finish_policy);
    state["stopReason"] = report.verdict == 0 ? "tick_limit" :
        (report.finish_policy == 2 ? "on-verdict" :
         (report.finish_policy == 1 && report.ending_confirmed ? "ending_confirmed" : "tick_limit"));
    state["censored"] = report.verdict == 0;
    state["finishBoundary"] = nullptr;
    state["yieldTrusted"] = report.certificate_held != 0 && report.post_certificate_delta == 0;
    state["rewardsTruncated"] = nullptr;
    state["endingConfirmed"] = report.ending_confirmed != 0;
    state["endingCounter"] = report.ending_counter;
    state["pendingActivity"] = report.unresolved_commands + report.pending_projectiles;
    state["endingGateTick"] = report.ending_gate_tick < 0 ? Json(nullptr) : Json(report.ending_gate_tick);
    state["endingTick"] = verdict_tick < 0 ? Json(nullptr) : Json(verdict_tick);
    state["endingCutTick"] = cut_tick;
    state["horizonTick"] = horizon_tick;
    state["postEndingTicks"] = verdict_tick < 0 ? Json(nullptr) : Json(cut_tick - verdict_tick);
    state["silentTailTicks"] = std::max(0, horizon_tick - cut_tick);
    state["finishPhaseEvents"] = 0;
    state["finishPhaseFirstTick"] = nullptr;
    state["endingCutReason"] = "Native events are captured through the final simulated tick; no Finish-phase trace is synthesized.";
    state["windowed"] = false;
    state["windowStopTick"] = nullptr;
    state["windowHorizonTick"] = nullptr;
    state["windowRemainingTicks"] = nullptr;
    state["windowNote"] = nullptr;
    const auto prize_tick = last_tick_with_prize(events);
    state["lastPrizeTick"] = prize_tick ? Json(*prize_tick) : Json(nullptr);
    state["unresolvedCommands"] = report.unresolved_commands;
    state["prizeCallbacks"] = report.prize_callbacks;
    state["mathDraws"] = report.math_draws;
    state["libDraws"] = report.lib_draws;
    state["rngFinalState"] = rng_state(module, battle);
    state["retainedRewards"] = report.pending_final;
    state["initializationMode"] = prepared.value("initializationMode", std::string("native prepared scenario"));
    state["units"] = final_units(native_units);
    state["rewardEntitlement"] = Json{
        {"scopeAllowed", report.scope_allowed != 0},
        {"certificateHeld", report.certificate_held != 0},
        {"certificateFrame", report.certificate_frame},
        {"certificatePending", report.certificate_pending},
        {"lateHoldFrame", report.late_hold_frame},
        {"pendingAtVerdict", report.pending_at_verdict},
        {"pendingFinal", report.pending_final},
        {"postCertificateDelta", report.post_certificate_delta},
        {"verdictObservations", report.verdict_observations},
        {"clauses", Json::array({report.clauses[0], report.clauses[1], report.clauses[2], report.clauses[3]})}
    };
    state["cellsDerivedFrom"] = "direct KaUnit cell component captured after each native tick";
    (void)prepared;
    return state;
}
} // namespace

ReplayCapture::ReplayCapture(HMODULE module, void* battle, const Json& prepared)
    : execution_(execution_identity(prepared)), setup_summary_(setup_summary(prepared)),
      encounter_(encounter_summary(prepared)), events_(Json::array()),
      raw_native_events_(Json::array()), frames_(Json::array()),
      initial_config_(prepared.at("snapshot").at("config")),
      last_tick_(initial_config_.value("tick", -1)) {
    const auto native_units = read_units(module, battle, roster_size(prepared));
    units_ = build_unit_metadata(native_units, prepared);
    previous_unit_values_ = packed_frame_units(native_units);
    initial_objects_ = read_objects(module, battle);
    previous_objects_ = initial_objects_;
    estimated_payload_bytes_ = execution_.dump().size() + setup_summary_.dump().size() +
        encounter_.dump().size() + units_.dump().size() + initial_objects_.dump().size() +
        skill_catalog(prepared).dump().size() + 65536;
    if (estimated_payload_bytes_ >= kReplayPayloadLimit) {
        throw std::runtime_error("Native replay metadata exceeds the 32 MiB UI payload limit.");
    }
}

void ReplayCapture::capture_tick(HMODULE module, void* battle) {
    const auto event_count_fn = resolve<EventCount>(module, "ka_battle_event_count");
    const auto event_export_fn = resolve<ExportEvents>(module, "ka_battle_export_events");
    const auto counters_fn = resolve<Counters>(module, "ka_battle_counters");
    const auto count = event_count_fn(battle);
    // push_event silently refuses entries at/above KA_MAX_EVENTS. At capacity we cannot prove
    // there was no dropped suffix, so never label a saturated tick a complete trace.
    if (count >= kNativeEventCapacity) {
        throw std::runtime_error("Native replay event log reached its fixed per-tick capacity; trace is incomplete.");
    }
    std::vector<KaEvent> native_events(count);
    if (count != 0 && event_export_fn(battle, native_events.data()) != count) {
        throw std::runtime_error("Native replay event count changed during export.");
    }

    std::uint32_t tick_word = 0;
    std::int32_t next_identity = 0;
    std::int32_t next_command = 0;
    counters_fn(battle, &tick_word, &next_identity, &next_command);
    const auto tick = static_cast<std::int32_t>(tick_word);
    if (tick != last_tick_ + 1) {
        throw std::runtime_error("Native replay tick sequence is not contiguous.");
    }
    last_tick_ = tick;

    std::optional<std::size_t> verdict_index;
    for (std::size_t i = 0; i < native_events.size(); ++i) {
        if (native_events[i].kind == 29) {
            verdict_index = i;
            break;
        }
    }
    Json event_batch = Json::array();
    Json raw_event_batch = Json::array();
    const auto attack_hp_after = correlate_attack_hp(native_events);
    auto sequence = next_sequence_;
    for (std::size_t i = 0; i < native_events.size(); ++i) {
        if (sequence == std::numeric_limits<std::uint32_t>::max()) {
            throw std::runtime_error("Native replay event sequence exceeded its integer range.");
        }
        event_batch.push_back(native_event_json(native_events[i], tick, sequence, i, verdict_index,
                                                units_, attack_hp_after[i]));
        raw_event_batch.push_back(raw_native_event_json(native_events[i], tick, sequence));
        ++sequence;
    }

    const auto native_units = read_units(module, battle, units_.size());
    auto current_unit_values = packed_frame_units(native_units);
    auto changed_units = changed_unit_rows(previous_unit_values_, current_unit_values);
    auto current_objects = read_objects(module, battle);
    auto changed_objects = changed_object_rows(previous_objects_, current_objects);
    Json frame = Json::object();
    if (!changed_units.empty() || !changed_objects.empty()) {
        frame = Json{{"tick", tick}, {"phase", "battle"},
                     {"captureStage", "native-full-tick"},
                     {"unitColumns", Json::array({"entityId", "hp", "mp", "state", "cellX", "cellY", "nativePosition"})},
                     {"units", std::move(changed_units)}, {"objects", std::move(changed_objects)}};
    }
    const std::size_t added_bytes = event_batch.dump().size() +
        raw_event_batch.dump().size() +
        (frame.empty() ? 0 : frame.dump().size()) + 4;
    if (estimated_payload_bytes_ > kReplayPayloadLimit - std::min(kReplayPayloadLimit, added_bytes)) {
        throw std::runtime_error("Native replay exceeded the 32 MiB UI payload limit while capturing; no partial replay was returned.");
    }
    estimated_payload_bytes_ += added_bytes;
    for (auto& event : event_batch) events_.push_back(std::move(event));
    for (auto& event : raw_event_batch) raw_native_events_.push_back(std::move(event));
    next_sequence_ = sequence;
    if (!frame.empty()) frames_.push_back(std::move(frame));
    previous_unit_values_ = std::move(current_unit_values);
    previous_objects_ = std::move(current_objects);
    ++captured_tick_count_;
    (void)next_identity;
    (void)next_command;
}

Json ReplayCapture::finish(HMODULE module, void* battle, const Json& prepared,
                           const KaBattleReport& report) const {
    if (report.status != 0) throw std::runtime_error("Cannot complete a replay from a failed native battle.");
    const auto initial_tick = initial_config_.value("tick", -1);
    const auto expected_tick_count = static_cast<std::int64_t>(report.ticks) -
                                     (static_cast<std::int64_t>(initial_tick) + 1);
    if (expected_tick_count < 0 || static_cast<std::uint64_t>(expected_tick_count) != captured_tick_count_) {
        throw std::runtime_error("Native replay captured tick count does not match the canonical battle report.");
    }
    const auto expected_final_tick = report.ticks - 1;
    if (last_tick_ != expected_final_tick) {
        throw std::runtime_error("Native replay final tick does not match the canonical battle report.");
    }

    const auto native_units = read_units(module, battle, roster_size(prepared));
    if (previous_unit_values_ != packed_frame_units(native_units)) {
        throw std::runtime_error("Native replay final fighter delta differs from the live battle state.");
    }
    if (previous_objects_ != read_objects(module, battle)) {
        throw std::runtime_error("Native replay final object delta differs from the live battle state.");
    }

    Json replay{{"schema", "ka-battle-replay-1"},
                {"replaySchema", "ka-battle-replay-1"},
                {"traceComplete", true},
                {"source", Json{{"exporter", "C++ native replay capture"},
                                 {"runner", "ka_native_full_tick"},
                                 {"scenarioSchema", prepared.at("rawIntent").value("schema", std::string())},
                                 {"eventEncoding", "native seven-int KaEvent tuple plus exact enclosing tick"},
                {"phaseMapping", "native-ka-kernel-verdict-source-v1"},
                                 {"note", "Events and sparse state deltas are copied from the same canonical native battle execution; KaEvent phase is unavailable except for the uniquely sourced verdict event."}}},
                {"execution", execution_}, {"setupSummary", setup_summary_},
                {"encounter", encounter_}, {"units", units_}, {"initialObjects", initial_objects_},
                {"ticks", report.ticks}, {"events", events_},
                {"rawNativeEvents", raw_native_events_}, {"frames", frames_},
                {"frameUnitColumns", Json::array({"entityId", "hp", "mp", "state", "cellX", "cellY", "nativePosition"})},
                {"finalState", report_state(module, battle, prepared, report, events_, native_units)},
                {"finish", nullptr},
                {"postFinish", Json{{"finishApplied", false},
                                     {"inventoryCollection", "not modelled by the native kernel"},
                                     {"exp", "not modelled"},
                                     {"confirmation", "outside the native battle replay"},
                                     {"worldCollection", "not modelled"},
                                     {"traceSnapshot", "native battle events and RNG through the final simulated tick"}}},
                {"catalog", Json{{"stateNames", state_names()},
                                 {"skills", skill_catalog(prepared)},
                                 {"equipment", Json::object()}}},
                {"metrics", Json{{"heals", report.heals}, {"attackAttempts", report.attack_attempts},
                                  {"survivors", report.survivors}, {"ownHp", report.own_hp},
                                  {"ownHpMax", report.own_hp_max}, {"resourceUses", report.resource_uses}}},
                {"holyHerbRemaining", resolve<HerbStock>(module, "ka_battle_herb_stock")(battle)},
                {"itemRemaining", item_remaining(module, battle)},
                {"itemUses", item_uses(module, battle)}, {"receipts", Json::array()},
                {"runnerLimits", Json::array()},
                {"nativeTrace", Json{{"eventCount", events_.size()},
                                     {"frameCount", frames_.size()},
                                     {"capturedTickCount", captured_tick_count_},
                                     {"framesAreSparseDeltas", true},
                                     {"eventTupleFields", Json::array({"kind", "unit", "a", "b", "c", "d", "e"})},
                                     {"phaseNote", "event phase 'battle' is the renderer timeline; nativePhase is populated only for uniquely sourced events because the full-tick event buffer merges phases"}}},
                {"notes", Json::array({
                    "unitId is stable side:rosterIndex; entityId is the native fighter identity",
                    "events preserve the exact native event order and all seven signed tuple fields",
                    "frames contain only fighter or created-entity components that changed on that tick; each object component value is copied from the native entity arena",
                    "nativePhase is populated only when the source uniquely identifies it; other events retain exact tick/order with a phase-unavailable reason",
                    "finish is null because this kernel exports battle execution, not inventory or EXP collection"
                })},
                {"missing", Json::array({
                    "Finish chest dispatch, EXP, inventory receipts and world collection are outside the native battle kernel",
                    "equipment motion catalog rows are not carried in the prepared battle snapshot"
                })}};

    const auto payload_size = replay.dump().size();
    if (payload_size > kReplayPayloadLimit) {
        throw std::runtime_error("Complete native replay exceeds the 32 MiB UI payload limit.");
    }
    return replay;
}

} // namespace kaopt
