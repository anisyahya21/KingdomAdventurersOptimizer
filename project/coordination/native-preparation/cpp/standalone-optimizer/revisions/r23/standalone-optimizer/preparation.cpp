#include "preparation.hpp"
#include "normalization.hpp"

#include <algorithm>
#include <array>
#include <bit>
#include <cmath>
#include <cstdlib>
#include <cstdint>
#include <limits>
#include <map>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

namespace kaopt {
namespace {
using I32 = std::int32_t;
constexpr I32 I32_MAX = std::numeric_limits<I32>::max();

I32 wrap32(std::int64_t n) { return static_cast<I32>(static_cast<std::uint32_t>(n)); }
I32 div0(I32 n, I32 d) { if (!d) throw std::runtime_error("division by zero"); return n / d; }
I32 checked_i32(const Json& j, const char* label) {
    std::int64_t v;
    if(j.is_number_integer()) v=j.get<std::int64_t>();
    else if(j.is_string()) { try { std::size_t used=0; v=std::stoll(j.get<std::string>(),&used); if(used!=j.get<std::string>().size()) throw std::runtime_error("tail"); } catch(...) { throw std::runtime_error(std::string(label)+" must be an integer"); } }
    else throw std::runtime_error(std::string(label) + " must be an integer");
    if (v < std::numeric_limits<I32>::min() || v > I32_MAX)
        throw std::runtime_error(std::string(label) + " is outside signed32");
    return static_cast<I32>(v);
}
float f32(float v) { return std::bit_cast<float>(std::bit_cast<std::uint32_t>(v)); }
const Json& table_rows(const Json& tables, const char* group, const char* rows) {
    const auto& r = tables.at(group).at(rows);
    if (!r.is_array()) throw std::runtime_error(std::string("tables.") + group + "." + rows + " must be an array");
    return r;
}
Json catalog_rows(const Json& tables, const char* group_name, const char* rows_name) {
    const auto& group=tables.at(group_name);
    if(group.is_object() && group.contains(rows_name)) {
        const auto& rows=group.at(rows_name);
        if(!rows.is_array()) throw std::runtime_error(std::string("tables.")+group_name+"."+rows_name+" must be an array");
        return rows;
    }
    // The canonical runtime table loader exposes Monster and Treasure as maps from
    // decimal row IDs to the untouched sheet arrays. Keep that production shape
    // usable as well as the row-object form emitted by the preparation adapter.
    if(!group.is_object()) throw std::runtime_error(std::string("tables.")+group_name+" must be an object");
    std::vector<std::pair<I32,Json>> ordered;
    ordered.reserve(group.size());
    for(auto it=group.begin();it!=group.end();++it) {
        const I32 id=checked_i32(Json(it.key()),group_name);
        Json row;
        if(it.value().is_object() && it.value().contains("id") && it.value().contains("row")) row=it.value();
        else row=Json{{"id",id},{"row",it.value()}};
        ordered.emplace_back(id,std::move(row));
    }
    std::sort(ordered.begin(),ordered.end(),[](const auto& a,const auto& b){return a.first<b.first;});
    Json result=Json::array(); for(auto& item:ordered) result.push_back(std::move(item.second));
    return result;
}
template<class F> std::map<I32, const Json*> index_rows(const Json& rows, F id_of, const char* what) {
    std::map<I32, const Json*> out;
    for (const auto& row : rows) {
        const auto id = id_of(row);
        if (!out.emplace(id, &row).second) throw std::runtime_error(std::string("duplicate ") + what + " id");
    }
    return out;
}

struct Random {
    std::array<I32, 56> values{};
    I32 index = 0, partner = 21;
    explicit Random(I32 seed) {
        const I32 magnitude = seed == std::numeric_limits<I32>::min() ? I32_MAX : (seed < 0 ? -seed : seed);
        I32 previous = wrap32(161803398LL - magnitude);
        values[55] = previous;
        I32 current = 1;
        std::size_t at = 0;
        for (int n=0; n<54; ++n) {
            at = (at + 21) % 55; values[at] = current;
            I32 d = wrap32(static_cast<std::int64_t>(previous) - current);
            if (d < 0) d = wrap32(static_cast<std::int64_t>(d) + 2147483647);
            previous = current; current = d;
        }
        for (int pass=0; pass<4; ++pass) for (std::size_t j=1; j<=55; ++j) {
            const I32 v = wrap32(static_cast<std::int64_t>(values[j]) - values[1 + (j+30)%55]);
            values[j] = v < 0 ? wrap32(static_cast<std::int64_t>(v) + 2147483647) : v;
        }
    }
    I32 next() {
        index = index < 55 ? index+1 : 1; partner = partner < 55 ? partner+1 : 1;
        I32 v = wrap32(static_cast<std::int64_t>(values[index]) - values[partner]);
        if (v == 2147483647) --v; else if (v < 0) v = wrap32(static_cast<std::int64_t>(v)+2147483647);
        values[index] = v; return v;
    }
};
I32 below(I32 raw, I32 n) {
    if (n == 0) return 0;
    const I32 magnitude = raw < 0 ? wrap32(-static_cast<std::int64_t>(raw)) : raw;
    return wrap32(static_cast<std::int64_t>(magnitude) - static_cast<std::int64_t>(div0(magnitude,n))*n);
}
I32 monster_param(const Json& curve, I32 level) {
    if (!curve.is_array() || curve.size()!=4) throw std::runtime_error("monster parameter curve must have four points");
    const I32 a=checked_i32(curve[0],"curve"), b=checked_i32(curve[1],"curve"), c=checked_i32(curve[2],"curve"), d=checked_i32(curve[3],"curve");
    if (level < 1) return a; if (level > 10000) return d;
    I32 low, high, step, span;
    if(level<=100){low=a;high=b;step=level-1;span=99;}
    else if(level<=1000){low=b;high=c;step=level-100;span=900;}
    else {low=c;high=d;step=level-1000;span=9000;}
    return wrap32(static_cast<std::int64_t>(low)+div0(wrap32(static_cast<std::int64_t>(wrap32(static_cast<std::int64_t>(high)-low))*step),span));
}

I32 parameter_value(const Json& raw, I32 pid, const std::vector<const Json*>& equipment,
                    bool human, bool maximum) {
    const std::string key=std::to_string(pid);
    if (!raw.contains(key)) throw std::runtime_error("candidate missing required parameter " + key);
    const auto& p=raw.at(key);
    const I32 raw_max=checked_i32(p.at("rawMax"),"rawMax"), extra_max=checked_i32(p.at("extraMax"),"extraMax");
    if (maximum && raw_max==I32_MAX) return I32_MAX;
    I32 value=wrap32(static_cast<std::int64_t>(maximum ? raw_max : checked_i32(p.at("rawValue"),"rawValue")) +
                     (maximum ? extra_max : checked_i32(p.at("extraValue"),"extraValue")));
    const bool bounded=raw_max!=I32_MAX;
    if (maximum==bounded) for(const Json* e:equipment) {
        const auto& pairs=e->at("parameters"); const auto index=pid-10;
        if(index<0 || static_cast<std::size_t>(index)>=pairs.size() || pairs[static_cast<std::size_t>(index)].is_null()) continue;
        const auto& pair=pairs[static_cast<std::size_t>(index)];
        const I32 level=checked_i32(e->at("level"),"equipment level");
        const I32 contribution=wrap32(static_cast<std::int64_t>(checked_i32(pair[0],"equipment parameter"))+
            static_cast<std::int64_t>(checked_i32(pair[1],"equipment growth"))*wrap32(static_cast<std::int64_t>(level)-1));
        const I32 affinity=checked_i32(e->at("affinity"),"equipment affinity");
        value=wrap32(static_cast<std::int64_t>(value)+((human && affinity==0) ? (contribution>0 ? std::max(1,contribution/2) : 0) : contribution));
    }
    if(maximum || pid==25) return value;
    const I32 upper=parameter_value(raw,pid,equipment,human,true);
    return std::max<I32>(0,std::min(upper,value));
}

I32 priority_for(const Json& unit, const std::map<I32,const Json*>& skills, const Json& priorities) {
    I32 category=2;
    for(const auto& sidj:unit.at("skills")) {
        const I32 sid=checked_i32(sidj,"skill id"); auto it=skills.find(sid);
        if(it==skills.end()) throw std::runtime_error("unknown skill id " + std::to_string(sid));
        if(checked_i32(it->second->at("type"),"skill type")==60) { category=checked_i32(it->second->at("value"),"formation skill value"); break; }
    }
    if(unit.value("visitor",false)) category=3;
    if(unit.value("leaderIdentity",false)) category=5;
    if(category<0 || static_cast<std::size_t>(category)>=priorities.size()) throw std::runtime_error("formation category outside rules table");
    return wrap32(static_cast<std::int64_t>(checked_i32(priorities[category],"formation priority")) +
        (unit.at("human").get<bool>() ? 0 : 1) + (unit.value("ownerPlayer",false) ? 2 : 0));
}
Json rng_json(I32 seed) {
    Random rng(seed);
    Json values=Json::array(); for(I32 v:rng.values) values.push_back(v);
    return Json{{"values",values},{"index",0},{"partner",21},{"draws",0}};
}
I32 linear_easing(I32 low,I32 high,I32 steps,I32 progress) {
    if(progress<0) return low; if(progress>=steps) return high;
    const float fraction=f32(static_cast<float>(progress)/static_cast<float>(steps));
    const float delta=f32(static_cast<float>(wrap32(static_cast<std::int64_t>(high)-low)));
    const float value=f32(f32(fraction*delta)+f32(static_cast<float>(low)));
    return std::max(std::min(low,high),std::min(std::max(low,high),static_cast<I32>(value)));
}
Json api_skill_row(const Json& row) {
    static constexpr const char* fields[]={"id","category","type","flags","minMp","maxMp","requiredEquipType","shootingRange","range","count","motion","value","seb","img","impactImg","impactSeb"};
    Json out=Json::object(); for(const char* f:fields) { if(!row.contains(f)) throw std::runtime_error(std::string("skill row missing ABI field ")+f); out[f]=row.at(f); }
    return row;
}
I32 cell_key(I32 x,I32 y,I32 width) { return wrap32(static_cast<std::int64_t>(x)+wrap32(static_cast<std::int64_t>(wrap32(static_cast<std::int64_t>(y)*width))*2)); }
}

Json prepare(const Json& admitted, const Json& tables, bool includeInputEffectiveParameters,
             const Json* sourceRaw) {
    if(!admitted.is_object() || admitted.value("schema",std::string{})!="ka-special-combat-research-1")
        throw std::runtime_error("unsupported admitted scenario schema");
    Json initial_status_boards=Json::object();
    const Json scenario=normalize_scenario_inputs(admitted,tables,&initial_status_boards);
    const Json start_profile=scenario.value("startProfile",Json());
    if(!start_profile.is_null()) {
        if(!start_profile.is_object() || start_profile.value("kind",std::string{})!="isolated-scene0") throw std::runtime_error("unsupported startProfile kind");
        for(auto it=start_profile.begin();it!=start_profile.end();++it) if(it.key()!="kind"&&it.key()!="enemySpawnCell"&&it.key()!="bossCell"&&it.key()!="startingStatus"&&it.key()!="note") throw std::runtime_error("unknown startProfile field");
    }
    for(const char* k:{"mathSeed","libSeed","tickLimit","defeatCount","encounterId"}) if(!scenario.contains(k)) throw std::runtime_error(std::string("missing ")+k);
    const I32 encounter_id=checked_i32(scenario.at("encounterId"),"encounterId");
    const I32 defeat=checked_i32(scenario.at("defeatCount"),"defeatCount");
    const I32 lib_seed=checked_i32(scenario.at("libSeed"),"libSeed");
    const I32 math_seed=checked_i32(scenario.at("mathSeed"),"mathSeed");
    const I32 tick_limit=checked_i32(scenario.at("tickLimit"),"tickLimit");
    if(encounter_id<0||encounter_id>=20||defeat<0||tick_limit<1||lib_seed<0||math_seed<0)
        throw std::runtime_error("encounter, defeat count, tick limit or canonical seeds outside supported domain");
    const std::string finish_policy=scenario.value("finishPolicy",std::string("at-horizon"));
    if(finish_policy!="at-horizon"&&finish_policy!="on-verdict"&&finish_policy!="after-ending")
        throw std::runtime_error("unknown finish policy");
    if(!scenario.contains("ownUnits")||!scenario.at("ownUnits").is_array()||scenario.at("ownUnits").empty())
        throw std::runtime_error("explicit non-empty ownUnits required");

    const auto& encounters=table_rows(tables,"encounters","encounters");
    const auto& monsters=table_rows(tables,"encounters","monsters");
    const auto& skill_rows=table_rows(tables,"weapon-skill-profiles","skills");
    const auto& equipment_rows=table_rows(tables,"weapon-skill-profiles","equipment");
    const auto encounter_map=index_rows(encounters,[](const Json& j){return checked_i32(j.at("id"),"encounter id");},"encounter");
    const auto monster_map=index_rows(monsters,[](const Json& j){return checked_i32(j.at("id"),"monster id");},"monster");
    const auto skill_map=index_rows(skill_rows,[](const Json& j){return checked_i32(j.at("id"),"skill id");},"skill");
    const auto equipment_map=index_rows(equipment_rows,[](const Json& j){return checked_i32(j.at("id"),"equipment id");},"equipment");
    const auto& rules=tables.at("formation-rules").at("priorities");
    const Json treasure_rows=catalog_rows(tables,"Treasure","rows");
    const Json monster_sheet_rows=catalog_rows(tables,"Monster","rows");
    const auto& effect_rows=table_rows(tables,"effect-resource-checks","resources");
    const auto& anim=tables.at("animation-resources").at("resources");
    const auto& human_bases=tables.at("skill-combat-constants").at("humanAnimationSebBases").at("values");
    const auto ei=encounter_map.find(encounter_id); if(ei==encounter_map.end()) throw std::runtime_error("unknown encounter");
    const auto& encounter=*ei->second;
    const I32 level=wrap32(static_cast<std::int64_t>(checked_i32(encounter.at("levelField"),"levelField"))+div0(defeat,5));
    static const std::set<I32> integrated_skill_types={0,1,2,11,15,18,19,20,21,22,23,24,26,27,60};
    auto require_supported_skill=[&](I32 sid) {
        auto it=skill_map.find(sid);if(it==skill_map.end())throw std::runtime_error("unknown skill id "+std::to_string(sid));
        const auto& row=*it->second;const I32 type=checked_i32(row.at("type"),"skill type"),category=checked_i32(row.at("category"),"skill category"),flags=checked_i32(row.at("flags"),"skill flags");
        if(flags&0x40000) { if((type!=66&&type!=67)||category!=0)throw std::runtime_error("unsupported generated-status skill route"); return; }
        if(type==48) { if(category!=2||(flags&8))throw std::runtime_error("unsupported EQUIP_MASTER route"); return; }
        if(!integrated_skill_types.contains(type))throw std::runtime_error("skill type outside canonical integrated subset: "+std::to_string(type));
    };

    std::vector<Json> own;
    std::set<std::string> names;
    for(std::size_t n=0;n<scenario.at("ownUnits").size();++n) {
        const auto& u=scenario.at("ownUnits")[n];
        if(!u.is_object()||!u.contains("name")||!u.at("name").is_string()||!u.contains("human")||!u.at("human").is_boolean()) throw std::runtime_error("unit requires explicit name and human flag");
        if(!names.insert(u.at("name").get<std::string>()).second) throw std::runtime_error("duplicate fighter name");
        if(u.value("onVehicle",false) || (u.value("humanFlags",0)&4)) throw std::runtime_error("vehicle-backed fighter components are unsupported");
        if(u.contains("parameterLinks")&&!u.at("parameterLinks").empty()) throw std::runtime_error("linked parameters are unsupported");
        std::vector<const Json*> gear;
        std::vector<Json> gear_storage;
        std::set<I32> lifted_types;
        for(const auto& sidj:u.at("skills")) {
            const I32 sid=checked_i32(sidj,"skill id"); auto si=skill_map.find(sid);
            if(si==skill_map.end()) throw std::runtime_error("unknown skill id " + std::to_string(sid));
            require_supported_skill(sid);
            if(checked_i32(si->second->at("type"),"skill type")==48) {
                if(checked_i32(si->second->at("category"),"skill category")!=2 || (checked_i32(si->second->at("flags"),"skill flags")&8))
                    throw std::runtime_error("EQUIP_MASTER active/category route is unsupported");
                lifted_types.insert(checked_i32(si->second->at("value"),"EQUIP_MASTER type"));
            }
        }
        if(!u.contains("equipment")||!u.at("equipment").is_array()) throw std::runtime_error("unit equipment array required");
        gear_storage.reserve(u.at("equipment").size());
        for(auto& ej:u.at("equipment")) {
            const auto id=checked_i32(ej.at("id"),"equipment id"); auto it=equipment_map.find(id);
            if(it==equipment_map.end()) throw std::runtime_error("unknown equipment id " + std::to_string(id));
            Json normalized=*it->second; normalized["level"]=ej.at("level");
            I32 affinity=checked_i32(ej.at("affinity"),"equipment affinity");
            if((affinity==-1||affinity==0)&&lifted_types.contains(checked_i32(normalized.at("type"),"equipment type"))) affinity=1;
            normalized["affinity"]=affinity;
            if(!normalized.contains("parameters")||!normalized.at("parameters").is_array()) throw std::runtime_error("equipment table row lacks parameter curves");
            gear_storage.push_back(std::move(normalized)); gear.push_back(&gear_storage.back());
        }
        const bool human=u.at("human").get<bool>();
        Json effective=Json::object();
        for(I32 pid:{10,11,12,13,14,15,16,18,19,20,21,22}) {
            effective[std::to_string(pid)]={{"value",parameter_value(u.at("parameters"),pid,gear,human,false)},
                                            {"maximum",parameter_value(u.at("parameters"),pid,gear,human,true)}};
        }
        I32 train=1;
        if(human) { I32 total=0; for(I32 pid:{10,11,12,13,14,15,16,18,19,20,21,22}) total=wrap32(static_cast<std::int64_t>(total)+checked_i32(u.at("parameters").at(std::to_string(pid)).at("trainingLevel"),"training level")); train=std::max<I32>(1,div0(total,12)); }
        Json output=u; output["incomingIndex"]=static_cast<I32>(n); output["effectiveParameters"]=effective;
        // Probe display uses the original pre-refill effective value; battle construction below
        // still refills HP/MP and updates effectiveParameters exactly as before.
        if(includeInputEffectiveParameters) output["inputEffectiveParameters"]=effective;
        output["effectiveDefense"]=effective["14"]["value"]; output["averageTrainingLevel"]=train;
        output["formationValue"]=nullptr;
        for(const auto& sidj:u.at("skills")) { I32 sid=checked_i32(sidj,"skill id");auto si=skill_map.find(sid);if(si==skill_map.end())throw std::runtime_error("unknown skill id");if(checked_i32(si->second->at("type"),"skill type")==60){output["formationValue"]=si->second->at("value");break;} }
        output["priority"]=priority_for(u,skill_map,rules); own.push_back(std::move(output));
    }

    Random rng(lib_seed); std::vector<Json> enemies; std::int32_t draws=0;
    auto add_monster=[&](I32 mid,bool boss) {
        auto mi=monster_map.find(mid); if(mi==monster_map.end()) throw std::runtime_error("broken encounter monster reference");
        const Json& m=*mi->second; const auto& curves=m.at("parametersRaw");
        if(curves.size()!=7) throw std::runtime_error("monster requires seven native parameter curves");
        Json p=Json::object(); std::size_t i=0;
        for(I32 pid:{10,11,13,14,15,16,19}) { I32 v=monster_param(curves[i++],level); p[std::to_string(pid)]={{"rawValue",v},{"rawMax",(pid==10||pid==11)?v:I32_MAX},{"extraValue",0},{"extraMax",0},{"trainingLevel",1}}; }
        const I32 sid=checked_i32(m.at("skillId"),"monster skill id");require_supported_skill(sid);
        enemies.push_back(Json{{"monsterId",mid},{"name",m.at("name")},{"human",false},{"monster",true},{"leaderIdentity",boss},{"visitor",false},{"ownerPlayer",false},{"level",level},{"rank",boss?1:level},{"parameters",p},{"skills",Json::array({sid})},{"incomingIndex",static_cast<I32>(enemies.size())},{"effectiveDefense",p["14"]["rawValue"]}});
    };
    if(!encounter.at("followers").is_array()) throw std::runtime_error("encounter followers must be an array");
    for(const auto& f:encounter.at("followers")) { ++draws; const I32 rate=checked_i32(f.at("checkRate"),"follower rate"); if(rate<0||rate>100)throw std::runtime_error("follower rate outside 0..100"); const I32 roll=below(rng.next(),100); if(roll<rate)add_monster(checked_i32(f.at("monsterId"),"follower monster id"),false); }
    add_monster(checked_i32(encounter.at("bossId"),"boss id"),true);

    auto place=[&](std::vector<Json>& units,I32 team,I32 opponents) {
        std::stable_sort(units.begin(),units.end(),[](const Json&a,const Json&b){ if(a.at("priority")!=b.at("priority"))return a.at("priority").get<I32>()<b.at("priority").get<I32>(); if(a.at("effectiveDefense")!=b.at("effectiveDefense"))return a.at("effectiveDefense").get<I32>()>b.at("effectiveDefense").get<I32>(); return a.at("incomingIndex").get<I32>()<b.at("incomingIndex").get<I32>(); });
        const I32 offset=std::max<I32>(3,opponents/5+1);
        for(std::size_t i=0;i<units.size();++i){const I32 col=static_cast<I32>(i%5),row=static_cast<I32>(i/5);units[i]["grid"]=static_cast<I32>(i);units[i]["row"]=row;units[i]["column"]=col;units[i]["cell"]=Json::array({col,team==0?offset+1+row:offset-row});}
        std::sort(units.begin(),units.end(),[](const Json&a,const Json&b){return a.at("incomingIndex").get<I32>()<b.at("incomingIndex").get<I32>();});
    };
    // Enemy priority is formation priority plus the native monster term; boss identity uses category 5.
    for(auto& e:enemies){I32 cat=e.at("leaderIdentity").get<bool>()?5:2;if(cat<0||static_cast<std::size_t>(cat)>=rules.size())throw std::runtime_error("enemy formation category unsupported");e["priority"]=wrap32(static_cast<std::int64_t>(checked_i32(rules[cat],"formation priority"))+1);}
    place(own,0,static_cast<I32>(enemies.size())); place(enemies,1,static_cast<I32>(enemies.size()));
    Json own_order=Json::array(), enemy_order=Json::array();
    auto by_grid=[](const Json&a,const Json&b){return a.at("grid").get<I32>()<b.at("grid").get<I32>();};
    std::vector<Json> own_placement=own, enemy_placement=enemies;
    std::sort(own_placement.begin(),own_placement.end(),by_grid); std::sort(enemy_placement.begin(),enemy_placement.end(),by_grid);
    for(const auto& u:own_placement) own_order.push_back(u.at("incomingIndex"));
    for(const auto& e:enemy_placement) enemy_order.push_back(e.at("incomingIndex"));
    if(own.size()+enemies.size()>32) throw std::runtime_error("native battle supports at most 32 fighters");

    // `_build_template` starts every candidate at effective HP/MP maxima. Apply Parameter.Add
    // against each raw maximum exactly once, retaining overflow in extraValue.
    for(auto& u:own) for(I32 pid:{10,11}) {
        const auto key=std::to_string(pid); auto& p=u["parameters"][key];
        const I32 maximum=u["effectiveParameters"][key]["maximum"].get<I32>();
        if(maximum<=0) throw std::runtime_error("nonpositive effective HP/MP maximum is outside supported refill path");
        const I32 raw=checked_i32(p.at("rawValue"),"raw parameter");
        const I32 total=wrap32(static_cast<std::int64_t>(raw)+maximum);
        const I32 new_value=std::max<I32>(0,std::min(maximum,total));
        p["rawValue"]=new_value;
        u["effectiveParameters"][key]["value"]=maximum;
    }

    std::map<I32,const Json*> monster_sheet;
    for(const auto& row:monster_sheet_rows) {
        if(!row.contains("id")||!row.contains("row")||!row.at("row").is_array()||row.at("row").size()<6) throw std::runtime_error("Monster catalog row lacks native type/size fields");
        const I32 id=checked_i32(row.at("id"),"Monster catalog id");
        if(!monster_sheet.emplace(id,&row.at("row")).second) throw std::runtime_error("duplicate Monster catalog id");
    }
    auto monster_meta=[&](I32 id) {
        auto it=monster_sheet.find(id); if(it==monster_sheet.end()) throw std::runtime_error("Monster catalog lacks id " + std::to_string(id));
        return std::pair<I32,I32>{checked_i32(it->second->at(4),"monster type"),checked_i32(it->second->at(5),"monster size")};
    };
    std::vector<Json> roster;
    for(auto& u:own) {
        Json s={{"name",u.at("name")},{"human",u.at("human")},{"team",0},{"grid",u.at("grid")},{"cell",u.at("cell")},
                {"parameters",u.at("parameters")},{"skills",u.at("skills")},{"levels",u.at("invocationLevels")},
                {"equipment",Json::array()},{"invoking",u.value("invokingSkills",Json::array())},
                {"boss",false},{"monsterType",0},{"monsterSize",0},{"specialHuman",false},
                {"humanFlag",u.value("humanFlags",0)},{"ownerPlayer",u.value("ownerPlayer",false)}};
        const I32 wid=checked_i32(u.at("weaponId"),"weaponId"); auto wi=equipment_map.find(wid);
        if(wi==equipment_map.end()) throw std::runtime_error("unknown weapon id"); s["weaponId"]=wid;
        s["weapon"]=*wi->second;
        for(const auto& ej:u.at("equipment")) {
            I32 id=checked_i32(ej.at("id"),"equipment id"); auto e=equipment_map.find(id); if(e==equipment_map.end())throw std::runtime_error("unknown equipment id");
            Json row={{"level",ej.at("level")},{"pvpLevel",e->second->value("pvpLevel",Json(0))},{"affinity",ej.at("affinity")},{"parameters",e->second->at("parameters")}};
            I32 affinity=checked_i32(row.at("affinity"),"equipment affinity");
            for(const auto& sidj:u.at("skills")){const auto& skill=*skill_map.at(checked_i32(sidj,"skill id"));if(checked_i32(skill.at("type"),"skill type")==48&&checked_i32(skill.at("value"),"EQUIP_MASTER type")==checked_i32(e->second->at("type"),"equipment type")&&(affinity==-1||affinity==0))affinity=1;}
            row["affinity"]=affinity;s["equipment"].push_back(std::move(row));
        }
        if(u.contains("monsterId")&&!u.at("monsterId").is_null()) { auto [type,size]=monster_meta(checked_i32(u.at("monsterId"),"monsterId"));s["monsterType"]=type;s["monsterSize"]=size; }
        roster.push_back(std::move(s));
    }
    for(const auto& e:enemies) {
        const I32 mid=e.at("monsterId").get<I32>(); auto [type,size]=monster_meta(mid);
        auto wi=equipment_map.find(0); if(wi==equipment_map.end())throw std::runtime_error("equipment table lacks bare-handed id 0");
        Json skills=e.at("skills"); Json levels=Json::array();for(std::size_t i=0;i<skills.size();++i)levels.push_back(1);
        roster.push_back(Json{{"name",std::string("enemy:")+std::to_string(e.at("incomingIndex").get<I32>())+":"+std::to_string(mid)},
            {"human",false},{"team",1},{"grid",e.at("grid")},{"cell",e.at("cell")},{"parameters",e.at("parameters")},
            {"skills",skills},{"levels",levels},{"equipment",Json::array()},{"invoking",Json::array()},
            {"boss",e.at("leaderIdentity")},{"monsterType",type},{"monsterSize",size},{"specialHuman",false},{"humanFlag",0},{"ownerPlayer",false},
            {"weaponId",0},{"weapon",*wi->second}});
    }
    const bool captured_preplacement=scenario.contains("prePlacement");
    const bool sequential=!start_profile.is_null()||captured_preplacement;
    Json enemy_spawn=Json::array({0,0}),boss_cell;
    auto normalize_pre_board=[&](const Json& source,const char* label) {
        if(!source.is_object())throw std::runtime_error(std::string(label)+" must be a mapping");
        Json normalized=Json::object();
        for(auto it=source.begin();it!=source.end();++it) {
            const I32 key=checked_i32(Json(it.key()),label);
            const std::string name=std::to_string(key);
            if(normalized.contains(name))throw std::runtime_error(std::string(label)+" has duplicate numeric keys");
            normalized[name]=it.value();
        }
        return normalized;
    };
    if(captured_preplacement) {
        const auto& supplied=scenario.at("prePlacement");
        std::set<std::string> expected;
        for(auto& s:roster)expected.insert(s.at("name").get<std::string>());
        std::set<std::string> received;
        for(auto it=supplied.begin();it!=supplied.end();++it)received.insert(it.key());
        if(expected!=received)throw std::runtime_error("prePlacement must name every own and enemy fighter exactly once");
        for(auto& s:roster) {
            const auto name=s.at("name").get<std::string>();
            Json pre=supplied.at(name);
            Json cell=Json::array();for(const auto& v:pre.at("cell"))cell.push_back(checked_i32(v,"prePlacement cell"));
            Json position=Json::array(),offset=Json::array();
            for(const auto& v:pre.at("position"))position.push_back(static_cast<float>(v.get<double>()));
            for(const auto& v:pre.at("offset"))offset.push_back(static_cast<float>(v.get<double>()));
            s["prePlacement"]={{"cell",std::move(cell)},{"position",std::move(position)},
                {"offset",std::move(offset)},{"board",normalize_pre_board(pre.at("board"),"prePlacement board")},
                {"longBoard",normalize_pre_board(pre.at("longBoard"),"prePlacement longBoard")}};
        }
    } else if(sequential) {
        auto read_cell=[&](const Json& v,const char* label){if(!v.is_array()||v.size()!=2)throw std::runtime_error(std::string(label)+" must be a two-integer cell");return Json::array({checked_i32(v[0],label),checked_i32(v[1],label)});};
        if(start_profile.contains("enemySpawnCell"))enemy_spawn=read_cell(start_profile.at("enemySpawnCell"),"enemySpawnCell");
        if(start_profile.contains("bossCell")&&!start_profile.at("bossCell").is_null())boss_cell=read_cell(start_profile.at("bossCell"),"bossCell");
        for(auto& s:roster) {
            Json source_cell=Json::array({0,0});
            if(s.at("team").get<I32>()==1)source_cell=(s.at("boss").get<bool>()&&!boss_cell.is_null())?boss_cell:enemy_spawn;
            Json board={{"4",0},{"5",0},{"6",s.at("team")},{"7",0},{"8",0}};
            if(s.at("team").get<I32>()==1){board["19"]=source_cell[0];board["20"]=source_cell[1];}
            s["prePlacement"]=Json{{"cell",source_cell},{"position",Json::array({static_cast<double>(static_cast<std::int64_t>(source_cell[0].get<I32>())*24),0.0,static_cast<double>(static_cast<std::int64_t>(source_cell[1].get<I32>())*24)})},
                {"offset",Json::array({0.0,0.0,0.0})},{"board",board},{"longBoard",Json::object()}};
        }
        for(auto it=initial_status_boards.begin();it!=initial_status_boards.end();++it) {
            auto found=std::find_if(roster.begin(),roster.end(),[&](const Json& s){return s.at("name")==it.key();});
            if(found==roster.end())throw std::runtime_error("startingStatus names an unknown fighter: "+it.key());
            for(auto field=it.value().begin();field!=it.value().end();++field)
                (*found)["prePlacement"]["board"][field.key()]=field.value();
        }
    }
    for(auto it=initial_status_boards.begin();it!=initial_status_boards.end();++it)
        if(std::none_of(roster.begin(),roster.end(),[&](const Json& s){return s.at("name")==it.key();}))
            throw std::runtime_error("startingStatus names an unknown fighter: "+it.key());

    auto gear_refs=[&](const Json& s){std::vector<const Json*> refs;for(const auto& row:s.at("equipment"))refs.push_back(&row);return refs;};
    auto hpmax=[&](const Json& s){return parameter_value(s.at("parameters"),10,gear_refs(s),s.at("human").get<bool>(),true);};
    auto mpmax=[&](const Json& s){return parameter_value(s.at("parameters"),11,gear_refs(s),s.at("human").get<bool>(),true);};
    auto stat=[&](const Json& s,I32 id){return parameter_value(s.at("parameters"),id,gear_refs(s),s.at("human").get<bool>(),false);};
    auto hp_rate=[&](const Json& s){const I32 maximum=hpmax(s);if(maximum==0)return I32(0);const I32 value=stat(s,10);const I32 product=wrap32(static_cast<std::int64_t>(value)*100);const auto quotient=static_cast<std::int64_t>(product)/static_cast<std::int64_t>(maximum);const I32 rate=static_cast<I32>(std::max<std::int64_t>(0,std::min<std::int64_t>(100,quotient)));return rate==0?I32(value>0):rate;};
    auto avg_train=[&](const Json& s){if(!s.at("human").get<bool>())return I32(1);I32 total=0;for(I32 p:{10,11,12,13,14,15,16,18,19,20,21,22})total=wrap32(static_cast<std::int64_t>(total)+checked_i32(s.at("parameters").at(std::to_string(p)).at("trainingLevel"),"training level"));return std::max<I32>(1,div0(total,12));};
    auto initial_has_skill=[&](std::size_t i) {
        const auto& s=roster[i]; const auto& ids=s.at("skills");const auto& levels=s.at("levels");
        std::vector<const Json*> active;
        for(const auto& sidj:ids){I32 sid=checked_i32(sidj,"skill id");auto it=skill_map.find(sid);if(it==skill_map.end())throw std::runtime_error("unknown skill id");const auto& r=*it->second;I32 flags=checked_i32(r.at("flags"),"skill flags");if((flags&8)&&!(flags&32))active.push_back(&r);}
        if(active.size()>levels.size()) throw std::runtime_error("filtered active skills exceed invocation-level inputs");
        for(std::size_t ix=0;ix<active.size();++ix) {
            const Json& r=*active[ix];I32 category=checked_i32(r.at("category"),"skill category");I32 type=checked_i32(r.at("type"),"skill type");I32 flags=checked_i32(r.at("flags"),"skill flags");
            if(category!=0&&category!=1)continue;
            if(category==0&&(flags&64)) continue;
            const I32 cost=s.at("human").get<bool>()?linear_easing(checked_i32(r.at("minMp"),"minMp"),checked_i32(r.at("maxMp"),"maxMp"),998,avg_train(s)-1):checked_i32(r.at("minMp"),"minMp");
            if(stat(s,11)<cost) continue;
            const I32 required=checked_i32(r.at("requiredEquipType"),"requiredEquipType");
            if(required!=-1&&checked_i32(s.at("weapon").at("type"),"weapon type")!=required) continue;
            bool has_target=false, has_in_range=false;
            const I32 range=checked_i32(r.at("shootingRange"),"skill shootingRange");
            const auto& c=s.at("cell");
            if(category==1){
                std::vector<std::size_t> targets;for(std::size_t j=0;j<roster.size();++j)targets.push_back(j);std::stable_sort(targets.begin(),targets.end(),[&](auto a,auto b){return roster[a].at("human").get<bool>()>roster[b].at("human").get<bool>();});
                std::size_t target=roster.size();std::int64_t nearest=std::numeric_limits<std::int64_t>::max();
                for(auto j:targets){if(j==i||roster[j].at("team")!=s.at("team")||hp_rate(roster[j])>=100)continue;const auto& tc=roster[j].at("cell");auto d=std::llabs(static_cast<std::int64_t>(c[0].get<I32>())-tc[0].get<I32>())+std::llabs(static_cast<std::int64_t>(c[1].get<I32>())-tc[1].get<I32>());if(d<=range&&d<nearest){nearest=d;target=j;}}
                if((flags&16)&&target==roster.size())continue;
                const I32 target_hp=target==roster.size()?0:stat(roster[target],10),target_rate=target==roster.size()?0:hp_rate(roster[target]);
                if(type==2&&!(target_rate>=1&&target_rate<=99))continue;if(type==15&&target_hp>0)continue;
                if(type==26||type==27||(flags&0x60000))throw std::runtime_error("recovery skill area/status route unsupported during initialization");
                return true;
            }
            for(std::size_t j=0;j<roster.size();++j) if(roster[j].at("team")!=s.at("team")) {
                const auto& t=roster[j];
                // InitFighters initializes team 0 completely before team 1. During
                // each team's state-decision pass, its opponents therefore retain
                // their source cells until their own placement pass.
                const auto& tc=(sequential && s.at("team")==0) ? t.at("prePlacement").at("cell") : t.at("cell");
                const std::int64_t dx=static_cast<std::int64_t>(c[0].get<I32>())-tc[0].get<I32>();
                const std::int64_t dy=static_cast<std::int64_t>(c[1].get<I32>())-tc[1].get<I32>();
                const std::int64_t d=std::llabs(dx)+std::llabs(dy);
                const bool alive=stat(t,10)>0;
                if(alive&&d<=range) has_target=true;
                bool in_cells=false;
                if(type==26){const I32 dir=s.at("team")==0?0:2;const I32 dx[4]={0,1,0,-1},dy[4]={-1,0,1,0};for(I32 n=1;n<=range;++n)if(static_cast<std::int64_t>(tc[0].get<I32>())==static_cast<std::int64_t>(c[0].get<I32>())+dx[dir]*n&&static_cast<std::int64_t>(tc[1].get<I32>())==static_cast<std::int64_t>(c[1].get<I32>())+dy[dir]*n)in_cells=true;}
                else if(type==27){const I32 depth=checked_i32(r.at("range"),"skill range");const std::int64_t start=s.at("team")==0?static_cast<std::int64_t>(c[1].get<I32>())-depth:static_cast<std::int64_t>(c[1].get<I32>())+1;const std::int64_t ty=tc[1].get<I32>();if(tc[0].get<I32>()>=0&&tc[0].get<I32>()<5&&ty>=start&&ty<start+depth)in_cells=true;}
                if(in_cells)has_in_range=true;
            }
            if((flags&16)&&!has_target) continue;
            if((type==26||type==27||(flags&0x60000))&&(!(static_cast<std::uint32_t>(s.at("grid").get<I32>()+4)<9u)||!has_in_range))continue;
            bool invoked=false;for(const auto& pair:s.at("invoking"))if(pair.is_array()&&pair.size()>=1&&checked_i32(pair[0],"invoking skill")==checked_i32(r.at("id"),"skill id"))invoked=true;
            if(invoked)continue;
            return true;
        }
        return false;
    };

    Json units_json=Json::array(); std::vector<std::pair<I32,std::vector<I32>>> buckets;
    std::array<std::vector<I32>,11> subset_members;
    Json rows_json=Json::object(); std::set<I32> needed{1,2};
    I32 row_offset=std::max<I32>(3,static_cast<I32>(enemies.size())/5+1);
    for(std::size_t i=0;i<roster.size();++i) {
        const Json& s=roster[i];const I32 id=100+static_cast<I32>(i),team=s.at("team").get<I32>();const auto& cell=s.at("cell");
        const I32 state=initial_has_skill(i)||((static_cast<std::uint32_t>(s.at("grid").get<I32>()+4)<9u))||checked_i32(s.at("weapon").at("shootingRange"),"weapon range")>1?3:1;
        const I32 dir=team==0?0:2;I32 clip;
        if(s.at("human").get<bool>()) { if(human_bases.size()<=3)throw std::runtime_error("human animation base 3 missing");clip=wrap32(static_cast<std::int64_t>(hp_rate(s)<=20?192:checked_i32(human_bases[3],"human animation base"))+dir); }
        else clip=(s.at("monsterSize").get<I32>()==3?4:0)+dir;
        Json board={{"4",0},{"5",state},{"6",team},{"7",s.at("grid")},{"8",0}};
        if(sequential)for(auto it=s.at("prePlacement").at("board").begin();it!=s.at("prePlacement").at("board").end();++it)if(it.key()!="4"&&it.key()!="5"&&it.key()!="6"&&it.key()!="7"&&it.key()!="8")board[it.key()]=it.value();
        Json params=Json::object(); for(auto it=s.at("parameters").begin();it!=s.at("parameters").end();++it){const I32 pid=checked_i32(Json(it.key()),"parameter id");params[std::to_string(pid)]={{"rawValue",it.value().at("rawValue")},{"extraValue",it.value().value("extraValue",Json(0))},{"rawMax",it.value().at("rawMax")},{"extraMax",it.value().value("extraMax",Json(0))},{"trainingLevel",it.value().value("trainingLevel",Json(1))}};}
        Json equip=Json::array();for(const auto& e:s.at("equipment"))equip.push_back(Json{{"level",e.at("level")},{"pvpLevel",e.at("pvpLevel")},{"affinity",e.at("affinity")},{"parameters",e.at("parameters")}});
        Json skillids=Json::array();for(const auto& v:s.at("skills")){I32 sid=checked_i32(v,"skill id");skillids.push_back(sid);needed.insert(sid);}
        Json levels=s.at("levels"), invoking=Json::array();for(const auto& pair:s.at("invoking"))invoking.push_back(pair);
        Json offset=sequential?s.at("prePlacement").at("offset"):Json::array({0.0f,0.0f,0.0f});
        Json comp={{"position",Json::array({static_cast<float>(wrap32(static_cast<std::int64_t>(cell[0].get<I32>())*24)),0.0f,static_cast<float>(wrap32(static_cast<std::int64_t>(cell[1].get<I32>())*24)),offset[0],offset[1],offset[2],nullptr})},
            {"speed",Json::array({0.0f,0.0f,0.0f})},{"seb",Json::array({s.at("human").get<bool>()?11:22,clip,0,-1})},{"depth",nullptr},
            {"cell",cell},{"image",Json::array({-1,-1,-1,-1,-1,-1})},{"animation",Json::array({1,-1})},{"direction",dir},
            {"modifier",nullptr},{"garbage",nullptr},{"effect",nullptr},{"projectile",nullptr},{"attack",nullptr}};
        std::vector<I32> present={0,1,2,5,7,12,14,s.at("human").get<bool>()?18:20,28,33,51};if(team==0)present.push_back(49);std::sort(present.begin(),present.end());Json pres=present;
        const auto& initial_cell=sequential?s.at("prePlacement").at("cell"):cell;
        const I32 key=cell_key(initial_cell[0].get<I32>(),initial_cell[1].get<I32>(),8);auto bucket=std::find_if(buckets.begin(),buckets.end(),[&](const auto& item){return item.first==key;});if(bucket==buckets.end()){buckets.push_back({key,{}});bucket=std::prev(buckets.end());}bucket->second.push_back(id);
        subset_members[1].push_back(id);subset_members[2].push_back(id);subset_members[4].push_back(id);subset_members[7].push_back(id);subset_members[8].push_back(id);subset_members[9].push_back(id);subset_members[10].push_back(id);
        const Json& weapon=s.at("weapon");
        const Json& projectile_flag=weapon.at("projectileFlag");const I32 numeric_projectile_flag=projectile_flag.is_boolean()?I32(projectile_flag.get<bool>()):checked_i32(projectile_flag,"projectileFlag");
        units_json.push_back(Json{{"identity",id},{"present",1},{"id",id},{"team",team},{"human",s.at("human")},{"monster",!s.at("human").get<bool>()},{"flags",0},{"destroyed",false},
            {"components",comp},{"board",board},{"long_board",sequential?s.at("prePlacement").at("longBoard"):Json::object()},{"parameters",params},{"equipment",equip},{"skills",skillids},{"levels",levels},{"invoking",invoking},{"commands",Json::array()},{"path",Json::array()},
            {"weapon",Json{{"type",weapon.at("type")},{"shootingRange",weapon.at("shootingRange")},{"motion",weapon.at("motion")},{"projectileFlag",numeric_projectile_flag}}},
            {"boss",s.at("boss")},{"monsterType",s.at("monsterType")},{"specialHuman",s.at("specialHuman")},{"monsterSize",s.at("monsterSize")},{"human_flag",s.at("humanFlag")},{"present_slots",pres}});
    }
    for(I32 sid:needed) {auto it=skill_map.find(sid);if(it==skill_map.end())throw std::runtime_error("required kernel row missing");rows_json[std::to_string(sid)]=api_skill_row(*it->second);}
    Json subset_json=Json::array();for(const auto& members:subset_members){Json slots=Json::array();for(I32 id:members)slots.push_back(id);subset_json.push_back(Json{{"slots",slots},{"free",Json::array()},{"version",static_cast<I32>(members.size())}});}
    if(sequential) {
        auto move_cell=[&](I32 identity,const Json& source,const Json& target){const I32 old_key=cell_key(source[0].get<I32>(),source[1].get<I32>(),8),new_key=cell_key(target[0].get<I32>(),target[1].get<I32>(),8);auto old=std::find_if(buckets.begin(),buckets.end(),[&](const auto& item){return item.first==old_key;});if(old!=buckets.end()){auto member=std::find(old->second.begin(),old->second.end(),identity);if(member!=old->second.end())old->second.erase(member);}auto next=std::find_if(buckets.begin(),buckets.end(),[&](const auto& item){return item.first==new_key;});if(next==buckets.end()){buckets.push_back({new_key,{}});next=std::prev(buckets.end());}next->second.push_back(identity);};
        for(const auto& placement:own_placement){const auto idx=placement.at("incomingIndex").get<I32>();const auto& s=roster.at(static_cast<std::size_t>(idx));move_cell(100+idx,s.at("prePlacement").at("cell"),s.at("cell"));}
        const I32 enemy_base=static_cast<I32>(own.size());
        for(const auto& placement:enemy_placement){const auto idx=placement.at("incomingIndex").get<I32>();const auto& s=roster.at(static_cast<std::size_t>(enemy_base+idx));move_cell(100+enemy_base+idx,s.at("prePlacement").at("cell"),s.at("cell"));}
    }
    Json buckets_json=Json::array();for(const auto& [key,ids]:buckets)buckets_json.push_back(Json::array({key,ids}));
    Json effects=Json::object();for(const auto& e:effect_rows)effects[std::to_string(checked_i32(e.at("id"),"effect resource id"))]=e.at("maxFrame");
    Json animation=Json::array();for(const auto& [name,manager]:std::array<std::pair<const char*,I32>,2>{{{"chara",11},{"monster",22}}}){std::vector<const Json*> entries;for(const auto& e:anim.at(name))entries.push_back(&e);std::sort(entries.begin(),entries.end(),[](const Json*a,const Json*b){return a->at("id").get<I32>()<b->at("id").get<I32>();});for(const Json* e:entries)animation.push_back(Json::array({manager,e->at("id"),e->at("maxFrame"),0}));}
    Json prizes=Json::array();const I32 reward_group=checked_i32(encounter.at("rewardGroup"),"encounter rewardGroup");for(const auto& r:treasure_rows){if(!r.contains("id")||!r.contains("row")||r.at("row").size()<=4)throw std::runtime_error("Treasure row lacks reward group column");if(checked_i32(r.at("row")[4],"Treasure group")==reward_group)prizes.push_back(r.at("id"));}
    Json consumable_items=Json::array(),inputs=Json::array();Json item_slots=Json::object();const I32 recovery_param[6]={10,10,11,11,12,12};const I32 all_residents[6]={1,0,1,0,1,0};
    const auto items=scenario.value("items",Json::object()),stocks=scenario.value("itemStock",Json::object());
    if(items.is_object())for(auto it=items.begin();it!=items.end();++it){const I32 bt=checked_i32(it.value().at("bonusType"),"item bonus type");if(bt<0||bt>=6)throw std::runtime_error("item recovery bonus type unsupported");I32 stock=stocks.value(it.key(),0);item_slots[it.key()]=static_cast<I32>(consumable_items.size());consumable_items.push_back(Json{{"parameter",recovery_param[bt]},{"all_residents",all_residents[bt]},{"bonus_min",it.value().at("bonusMinValue")},{"bonus_max",it.value().at("bonusMaxValue")},{"stock",stock}});}
    for(const auto& input:scenario.value("inputs",Json::array())){const std::string type=input.value("type",std::string{}),phase=input.value("phase",std::string{});I32 phase_id=phase=="before_fighters"?0:phase=="after_fighters"?1:-1;if(phase_id<0)throw std::runtime_error("input phase unsupported");if(type=="holy_herb")inputs.push_back(Json{{"tick",input.at("tick")},{"phase",phase_id},{"kind",0},{"item",-1}});else if(type=="item"){const auto name=input.at("item").get<std::string>();if(!item_slots.contains(name))throw std::runtime_error("input references undeclared item");const I32 bt=checked_i32(items.at(name).at("bonusType"),"item bonus type");if(!all_residents[bt])throw std::runtime_error("single-resident recovery input is unsupported");inputs.push_back(Json{{"tick",input.at("tick")},{"phase",phase_id},{"kind",1},{"item",item_slots.at(name)}});}else throw std::runtime_error("input kind unsupported");}
    Json watch_names=scenario.value("mpWatchUnits",Json::array());if(watch_names.empty())watch_names=scenario.value("holyHerbTriggerUnits",Json::array());
    Json watch=Json::array();for(const auto& name:watch_names){auto it=std::find_if(roster.begin(),roster.end(),[&](const Json& s){return s.at("name")==name;});if(it==roster.end())throw std::runtime_error("MP watch references unknown fighter");watch.push_back(static_cast<I32>(100+std::distance(roster.begin(),it)));}
    Json consumables={{"holy_herb_stock",scenario.value("holyHerbStock",0)},{"holy_herb_max_uses",scenario.value("holyHerbMaxUses",0)},{"mp_watch",watch},{"items",consumable_items},{"inputs",inputs},{"uses",Json::array()}};
    Json enemy_scope=Json::array();bool reward_allowed=true;for(const auto& e:enemies){const I32 sid=checked_i32(e.at("skills")[0],"enemy skill id");auto it=skill_map.find(sid);if(it==skill_map.end()){reward_allowed=false;continue;}I32 type=checked_i32(it->second->at("type"),"enemy skill type");if(type==2||type==15)reward_allowed=false;enemy_scope.push_back(Json{{"skills",{{"dataIds",Json::array({sid})}}}});}
    const std::string policy=finish_policy;const I32 policy_code=policy=="on-verdict"?2:policy=="after-ending"?1:0;
    Json snapshot={{"config",{{"tick",-1},{"first_identity",100},{"next_identity",100+static_cast<I32>(roster.size())},{"next_command",0},{"map_width",8},{"row_offset",row_offset},{"movement_enabled",true},{"battle_state",2},{"battle_frame",0},{"verdict",0},{"verdict_tick",-1},{"ending_counter",-1},{"ending_gate_tick",-1},{"prize_count",0}}},
        {"units",units_json},{"objects",Json::array()},{"subsets",subset_json},{"buckets",buckets_json},{"rng",{{"math",rng_json(math_seed)},{"lib",rng_json(lib_seed)}}},{"rows",rows_json},{"effect_resources",effects},{"prizes",prizes},{"projectile_sources",Json::array()},{"human_bases",human_bases},{"animation_resources",animation},{"consumables",consumables}};
    Json meta={{"schema","kaopt-prepared-intent-1"},{"readyForNativeEngineSnapshot",true},{"rawIntent",sourceRaw?*sourceRaw:admitted},{"snapshot",snapshot},{"tickLimit",tick_limit},{"finishPolicy",finish_policy},{"policyCode",policy_code},{"followerSelectionDraws",draws},{"rewardScopeAllowed",reward_allowed},{"initializationMode",captured_preplacement?"captured pre-placement sequential InitFighters":(sequential?"isolated-scene0 sequential InitFighters":"generated final-cell initialization")},
        {"config",{{"encounterId",encounter_id},{"defeatCount",defeat},{"level",level},{"mathSeed",math_seed},{"libSeed",lib_seed},{"tickLimit",tick_limit},{"battleState",2},{"terrain",-1}}},
        {"ownFormationOrder",own_order},{"ownUnits",own},{"enemyFormationOrder",enemy_order},{"enemies",enemies},
        {"limitations",Json::array({"The shared battle executor is the canonical Rust ka_kernel; this C++ module prepares raw intent into its ka_abi.engine_snapshot input.","Vehicle components, linked parameters, unsupported skill/item routes and execution-only native state fields remain outside the integrated path.","Reward entitlement is false when any enemy skill is unknown or has Cure type 2/15."})}};
    return meta;
}
}
