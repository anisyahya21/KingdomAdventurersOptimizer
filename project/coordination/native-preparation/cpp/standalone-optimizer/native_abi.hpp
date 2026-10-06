#pragma once
#include <cstdint>
// Generated from canonical ka_abi.py ctypes declarations; no runtime import.
struct KaVec3 {
    float x;
    float y;
    float z;
};
struct KaModifier {
    std::int32_t type_;
    float offset_x;
    float offset_y;
    float offset_z;
    float scale_x;
    float scale_y;
    std::int32_t angle;
    std::int32_t anchor;
    std::int32_t frame;
    std::int32_t duration;
    std::uint8_t destroy_on_finish;
    std::uint8_t looping;
    std::int32_t alpha;
};
struct KaEffect {
    std::int32_t type_;
    std::int32_t value1;
    std::int32_t value2;
    std::uint8_t depth;
    std::int32_t frame;
    std::int32_t max_frame;
    std::int32_t parent;
    std::int32_t scale;
};
struct KaProjectile {
    KaVec3 start;
    KaVec3 end;
    std::int32_t speed;
    std::int32_t height;
    std::int32_t frame;
    std::int32_t length;
    std::int32_t owner;
};
struct KaEntity {
    std::int32_t id;
    std::uint8_t destroyed;
    std::uint8_t has[52];
    KaVec3 position;
    KaVec3 offset;
    std::int32_t parent;
    KaVec3 speed;
    std::int32_t seb[4];
    std::int32_t depth[2];
    std::int32_t cell[2];
    std::int32_t image[6];
    std::int32_t animation[2];
    std::int32_t direction;
    KaModifier modifier;
    KaEffect effect;
    KaProjectile projectile;
    std::int32_t attack;
    std::int32_t garbage;
};
struct KaParam {
    std::int32_t id;
    std::int32_t raw_value;
    std::int32_t extra_value;
    std::int32_t raw_max;
    std::int32_t extra_max;
    std::int32_t training_level;
};
struct KaEquipRow {
    std::int32_t level;
    std::int32_t pvp_level;
    std::int32_t affinity;
    std::int32_t pair_count;
    std::int32_t pairs[16][2];
    std::uint8_t present[16];
};
struct KaParams {
    std::uint32_t count;
    KaParam rows[16];
    std::uint32_t equipment_count;
    KaEquipRow equipment[8];
};
struct KaEntry {
    std::int32_t key;
    std::int64_t value;
};
struct KaBoard {
    std::uint32_t len;
    KaEntry entries[48];
    std::uint8_t positions[128];
};
struct KaSkill {
    std::int32_t id;
    std::int32_t category;
    std::int32_t kind;
    std::int32_t flags;
    std::int32_t min_mp;
    std::int32_t max_mp;
    std::int32_t required_equip_type;
    std::int32_t shooting_range;
    std::int32_t range;
    std::int32_t count;
    std::int32_t motion;
    std::int32_t value;
    std::int32_t seb;
    std::int32_t img;
    std::int32_t impact_img;
    std::int32_t impact_seb;
};
struct KaCommand {
    std::int32_t opcode;
    std::int32_t target;
    std::int32_t skill;
    std::int32_t tick;
    std::int32_t duration;
    std::int32_t use_index;
};
struct KaInvoke {
    std::int32_t skill;
    std::int32_t remaining;
};
struct KaPoint {
    std::int32_t x;
    std::int32_t y;
};
struct KaUnit {
    std::uint8_t present;
    std::uint8_t team;
    std::uint8_t human;
    std::uint8_t monster;
    std::int32_t flags;
    std::int32_t id;
    std::int32_t identity;
    KaEntity body;
    KaParams params;
    std::int32_t weapon_type;
    std::int32_t weapon_shooting_range;
    std::int32_t weapon_motion;
    std::int32_t weapon_projectile_flag;
    std::uint8_t boss;
    std::int32_t monster_type;
    std::uint8_t special_human;
    std::int32_t monster_size;
    std::int32_t human_flag;
    KaBoard board;
    KaBoard long_board;
    std::int32_t skill_ids[12];
    std::uint32_t skill_count;
    std::int32_t levels[12];
    std::uint32_t level_count;
    KaInvoke invoking[64];
    std::uint32_t invoking_count;
    KaCommand commands[1024];
    std::uint32_t command_count;
    KaPoint path[8];
    std::uint32_t path_count;
};
struct KaComponentView {
    std::int32_t slot;
    std::int32_t ints[8];
    float floats[8];
};
struct KaEvent {
    std::int32_t kind;
    std::int32_t unit;
    std::int32_t a;
    std::int32_t b;
    std::int32_t c;
    std::int32_t d;
    std::int32_t e;
};
struct EffectSpec {
    std::int32_t type_;
    std::int32_t value1;
    std::int32_t value2;
    std::int32_t res;
    std::int32_t seb;
    float x;
    float y;
    float z;
    std::int32_t scale;
    std::int32_t image;
    std::uint8_t depth;
    std::int32_t max_frame;
    std::int32_t frame;
    std::uint8_t looping;
    std::int32_t parent;
    std::uint8_t animate;
};
