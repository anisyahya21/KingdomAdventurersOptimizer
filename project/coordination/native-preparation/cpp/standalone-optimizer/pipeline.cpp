#include "pipeline.hpp"
#include "fixed_formation.hpp"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cctype>
#include <condition_variable>
#include <cmath>
#include <cstdio>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <iterator>
#include <limits>
#include <mutex>
#include <map>
#include <memory>
#include <optional>
#include <queue>
#include <random>
#include <set>
#include <stdexcept>
#include <thread>
#include <time.h>
#include <unordered_set>
#include <utility>
#include <vector>
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <io.h>
#include <share.h>
#else
#include <sys/file.h>
#include <fcntl.h>
#include <unistd.h>
#endif

namespace kaopt {
namespace fs = std::filesystem;
namespace {

struct Candidate {
    Json raw;
    std::string id;
    std::string parent;
    std::uint64_t generation = 0;
    std::string operation = "seed";
    Json sourceBinding=Json::object();
};

struct Job { Candidate candidate; Json scenario; Json snapshot; std::int64_t seed; Json seeds; };
struct Done { Job job; Json record; Json diagnostic; };
struct ScoreStats { std::map<std::string, double> outcomesByTrial; std::map<std::string,std::string> basisByTrial; };
struct ArmStats {
    std::uint64_t tries=0, improvements=0;
    std::uint64_t lastMatchedSamples=0, lastMissingChildPairs=0, lastMissingParentPairs=0;
    std::string lastChildId, lastParentId;
    std::optional<double> lastMeanPairedDelta;
    bool lastJudgmentComplete=false;
};
struct PairedJudgment {
    std::uint64_t matchedSamples=0, missingChildPairs=0, missingParentPairs=0;
    long double deltaSum=0;
    std::optional<double> meanDelta;
    bool complete=false;
};
struct EncounterWork {
    std::uint64_t candidates=0, accepted=0, queued=0, active=0, pendingSave=0, completed=0, drainedJobs=0;
};
struct RuntimeFocus {
    bool all=true, pause=false, stop=false;
    std::set<std::string> encounterIds;
    std::string warning;
};
struct StopJoinThread {
    std::atomic<bool>& stop;
    std::thread& worker;
    void join() { stop.store(true,std::memory_order_relaxed); if(worker.joinable()) worker.join(); }
    ~StopJoinThread() { join(); }
};
long double mean_score(const ScoreStats& s) {
    if (s.outcomesByTrial.empty()) return -std::numeric_limits<long double>::infinity();
    long double sum = 0;
    for (const auto& [_, earned] : s.outcomesByTrial) sum += static_cast<long double>(earned);
    return sum / static_cast<long double>(s.outcomesByTrial.size());
}

std::string canonical(const Json& j) { return j.dump(-1, ' ', false, Json::error_handler_t::strict); }
bool current_thread_cpu_ns(std::uint64_t& value) {
#ifdef _WIN32
    FILETIME creation{}, exit{}, kernel{}, user{};
    if (!GetThreadTimes(GetCurrentThread(), &creation, &exit, &kernel, &user)) return false;
    ULARGE_INTEGER k{}, u{};
    k.LowPart = kernel.dwLowDateTime; k.HighPart = kernel.dwHighDateTime;
    u.LowPart = user.dwLowDateTime; u.HighPart = user.dwHighDateTime;
    if (k.QuadPart > (std::numeric_limits<std::uint64_t>::max() - u.QuadPart) ||
        k.QuadPart + u.QuadPart > std::numeric_limits<std::uint64_t>::max() / 100) return false;
    value = (k.QuadPart + u.QuadPart) * 100;
    return true;
#elif defined(CLOCK_THREAD_CPUTIME_ID)
    timespec ts{};
    if (::clock_gettime(CLOCK_THREAD_CPUTIME_ID, &ts) != 0 || ts.tv_sec < 0 || ts.tv_nsec < 0) return false;
    value = static_cast<std::uint64_t>(ts.tv_sec) * 1000000000ULL + static_cast<std::uint64_t>(ts.tv_nsec);
    return true;
#else
    (void)value;
    return false;
#endif
}
struct SearchBounds { std::map<std::string,std::pair<std::int64_t,std::int64_t>> byParameter; std::string sourceHash; std::string source; };
SearchBounds load_search_bounds(const Json& options) {
    SearchBounds result;
    if(!options.is_object()) return result;
    Json artifact=options.value("statBounds",Json(nullptr));
    if(options.contains("statBoundsPath")) {
        if(!options["statBoundsPath"].is_string()) throw std::runtime_error("statBoundsPath must be a path string");
        std::ifstream input(options["statBoundsPath"].get<std::string>(),std::ios::binary);
        if(!input) throw std::runtime_error("cannot read configured original stat-bounds artifact");
        std::string bytes((std::istreambuf_iterator<char>(input)),std::istreambuf_iterator<char>());
        result.sourceHash=sha256_text(bytes); result.source=options["statBoundsPath"].get<std::string>();
        if(options.contains("statBoundsSha256")&&(!options["statBoundsSha256"].is_string()||options["statBoundsSha256"].get<std::string>()!=result.sourceHash))
            throw std::runtime_error("configured stat-bounds SHA-256 does not match artifact");
        artifact=Json::parse(bytes);
    } else if(!artifact.is_null()) { result.sourceHash=sha256_text(canonical(artifact)); result.source="inline"; }
    if(artifact.is_null()) return result;
    if(!artifact.is_object()||!artifact.contains("schema")||artifact["schema"]!="ka-search-stat-bounds-2"||
       !artifact.contains("verification")||!artifact["verification"].is_object()||
       !artifact["verification"].value("verified",false)||!artifact.contains("stats")||!artifact["stats"].is_array())
        throw std::runtime_error("original stat-bounds artifact is missing or unverified");
    for(const auto& row:artifact["stats"]) {
        if(!row.is_object()||!row.contains("parameter")||!row["parameter"].is_number_integer()||!row.contains("minimum")||!row["minimum"].is_number_integer()||!row.contains("maximum")||!row["maximum"].is_number_integer()) continue;
        auto id=std::to_string(row["parameter"].get<std::int64_t>());
        if(id!="10"&&id!="11"&&id!="12"&&id!="13"&&id!="14"&&id!="15"&&id!="16"&&id!="18"&&id!="19") continue;
        auto lo=row["minimum"].get<std::int64_t>(), hi=row["maximum"].get<std::int64_t>();
        if(lo>hi) throw std::runtime_error("original stat-bounds artifact contains an inverted interval");
        result.byParameter[id]={lo,hi};
    }
    // Recovered walls are sparse: only generate stat mutations for parameters
    // actually present in this verified artifact. Missing walls stay unknown.
    return result;
}
Json stat_bounds_metadata(const SearchBounds& bounds) {
    static const std::int32_t parameters[]={10,11,12,13,14,15,16,18,19};
    Json available=Json::array(),missing=Json::array();
    for(const auto parameter:parameters) {
        if(bounds.byParameter.count(std::to_string(parameter))) available.push_back(parameter);
        else missing.push_back(parameter);
    }
    return Json{{"available",!available.empty()},
        {"availableParameterIds",std::move(available)},
        {"missingParameterIds",std::move(missing)},
        {"source",bounds.source},{"sha256",bounds.sourceHash}};
}
std::int64_t round_like_python(double value) {
    if(!std::isfinite(value)||value<0||value>static_cast<double>(std::numeric_limits<std::int64_t>::max()))
        throw std::runtime_error("effective broad target rounding overflow");
    const double floor_value=std::floor(value);
    const double fraction=value-floor_value;
    auto lower=static_cast<std::int64_t>(floor_value);
    if(fraction<0.5)return lower;
    if(fraction>0.5)return lower+1;
    return lower%2==0?lower:lower+1;
}
std::vector<std::int64_t> effective_broad_targets(
    std::int64_t baseline,const std::pair<std::int64_t,std::int64_t>* bounds,
    const std::string& direction) {
    std::vector<std::int64_t> kept;
    std::set<std::int64_t> seen;
    auto accept=[&](std::int64_t value,bool required) {
        if(value==baseline||value<0||seen.count(value))return;
        if(bounds&&(value<bounds->first||value>bounds->second))return;
        if(direction=="down"&&value>baseline)return;
        if(direction=="up"&&value<baseline)return;
        if(value>std::numeric_limits<std::int32_t>::max())
            throw std::runtime_error("verified effective stat bound exceeds the native signed32 target range");
        if(!required) {
            const auto gap=std::max<std::int64_t>(1,round_like_python(static_cast<double>(value)*0.01));
            for(const auto other:seen)if((value>other?value-other:other-value)<gap)return;
        }
        seen.insert(value);
        kept.push_back(value);
    };
    // Mirrors strategy_finetune.broad_targets exactly. Without a verified interval
    // the original ladder intentionally falls back to absolute rungs only.
    if(bounds) { accept(bounds->first,true);accept(bounds->second,true); }
    for(const auto rung:{1000LL,2000LL,4000LL})accept(rung,true);
    if(bounds) for(const double fraction:{0.5,0.91,1.09,2.0})
        accept(round_like_python(static_cast<double>(baseline)*fraction),false);
    std::sort(kept.begin(),kept.end());
    if(kept.size()>9)kept.resize(9);
    return kept;
}
std::int64_t prepared_battle_value(const Json& prepared,std::size_t unit_index,const std::string& unit_name,
                                  const std::string& parameter_id,bool bounded) {
    if(!prepared.contains("ownUnits")||!prepared["ownUnits"].is_array())
        throw std::runtime_error("canonical preparation omitted ownUnits for effective tuning");
    const Json* selected=nullptr;
    for(const auto& unit:prepared["ownUnits"]) if(unit.value("incomingIndex",std::numeric_limits<std::size_t>::max())==unit_index) {
        selected=&unit;break;
    }
    if(!selected) for(const auto& unit:prepared["ownUnits"]) if(unit.value("name",std::string{})==unit_name) {
        selected=&unit;break;
    }
    if(selected) {
        const auto& unit=*selected;
        if(!unit.contains("effectiveParameters")||!unit["effectiveParameters"].is_object()||
           !unit["effectiveParameters"].contains(parameter_id))
            throw std::runtime_error("canonical preparation omitted the effective tuning parameter");
        const auto& parameter=unit["effectiveParameters"][parameter_id];
        const char* field=bounded?"maximum":"value";
        if(!parameter.contains(field)||!parameter[field].is_number_integer())
            throw std::runtime_error(std::string("canonical preparation omitted effective ")+field);
        return parameter[field].get<std::int64_t>();
    }
    throw std::runtime_error("canonical preparation omitted the requested tuning unit");
}
std::int64_t effective_equipment_contribution(const Json& parent,const Json& parent_prepared,
                                               std::size_t unit_index,const std::string& unit_name,
                                               const std::string& parameter_id,bool bounded) {
    const auto& unit=parent.at("ownUnits").at(unit_index);
    if(!unit.contains("parameters")||!unit["parameters"].contains(parameter_id))
        throw std::runtime_error("tuning parent omitted the source parameter");
    const auto& parameter=unit["parameters"][parameter_id];
    const char* raw_field=bounded?"rawMax":"rawValue";
    const char* extra_field=bounded?"extraMax":"extraValue";
    if(!parameter.contains(raw_field)||!parameter[raw_field].is_number_integer()||
       !parameter.contains(extra_field)||!parameter[extra_field].is_number_integer())
        throw std::runtime_error("tuning parameter lacks integer raw/extra fields for effective conversion");
    const auto effective=prepared_battle_value(parent_prepared,unit_index,unit_name,parameter_id,bounded);
    const auto contribution=effective-parameter[raw_field].get<std::int64_t>()-
        parameter[extra_field].get<std::int64_t>();
    if(contribution<std::numeric_limits<std::int32_t>::min()||
       contribution>std::numeric_limits<std::int32_t>::max()||
       -contribution<std::numeric_limits<std::int32_t>::min()||
       -contribution>std::numeric_limits<std::int32_t>::max())
        throw std::runtime_error("effective tuning equipment compensation exceeds native signed32 range");
    return contribution;
}
void apply_effective_tuning_target(Json& child,std::size_t unit_index,
                                   const std::string& parameter_id,std::int64_t target,
                                   std::int64_t contribution,bool bounded) {
    auto& unit=child.at("ownUnits").at(unit_index);
    if(!unit.value("human",false)||!unit.contains("parameters")||
       !unit["parameters"].contains(parameter_id))
        throw std::runtime_error("effective tuning target requires the selected human parameter");
    auto& parameter=unit["parameters"][parameter_id];
    parameter["rawValue"]=target;
    if(bounded) {
        parameter["rawMax"]=target;
        parameter["extraMax"]=-contribution;
        parameter["extraValue"]=0;
    } else parameter["extraValue"]=-contribution;
}
struct ProbeAxisInfo { std::string label; std::string parameterId; };
const std::map<std::string,ProbeAxisInfo>& probe_axes() {
    static const std::map<std::string,ProbeAxisInfo> axes{
        {"atk",{"Attack","13"}},{"def",{"Defence","14"}},
        {"hp",{"HP","10"}},{"mp",{"MP","11"}},
        {"spd",{"Agility","15"}},{"lck",{"Luck","16"}},
        {"dex",{"Dexterity","19"}},{"int",{"Intelligence","18"}},
        {"vig",{"Energy","12"}},{"herbs",{"Holy Herb stock",""}}
    };
    return axes;
}
const ProbeAxisInfo& require_probe_axis(const std::string& axis) {
    const auto found=probe_axes().find(axis);
    if(found==probe_axes().end())
        throw std::runtime_error("unknown probe axis '"+axis+"'; supported axes: atk, def, hp, mp, spd, lck, dex, int, herbs, vig");
    return found->second;
}
bool probe_axis_is_stat(const std::string& axis) { return !require_probe_axis(axis).parameterId.empty(); }
bool unit_has_magic_attack(const Json& unit) {
    if(!unit.contains("skills")||!unit["skills"].is_array()) return false;
    return std::any_of(unit["skills"].begin(),unit["skills"].end(),[](const Json& skill) {
        return skill.is_number_integer()&&!skill.is_boolean()&&skill.get<std::int64_t>()>=5&&skill.get<std::int64_t>()<=19;
    });
}
std::pair<std::size_t,std::string> resolve_probe_unit(
    const Json& raw,const std::vector<std::string>& axes,const Json& requested_unit) {
    bool needs_unit=false,needs_magic=false;
    for(const auto& axis:axes) {
        if(probe_axis_is_stat(axis)) needs_unit=true;
        if(axis=="int") needs_magic=true;
    }
    std::string requested;
    if(!requested_unit.is_null()) {
        if(!requested_unit.is_string()) throw std::runtime_error("probe unit must be a unit name string");
        requested=requested_unit.get<std::string>();
    }
    if(!needs_unit) return {std::numeric_limits<std::size_t>::max(),requested};
    if(!raw.contains("ownUnits")||!raw["ownUnits"].is_array())
        throw std::runtime_error("this scenario has no human unit to probe a statistic on");
    const auto& units=raw["ownUnits"];
    std::size_t selected=std::numeric_limits<std::size_t>::max();
    if(!requested.empty()) {
        for(std::size_t i=0;i<units.size();++i)
            if(units[i].value("name",std::string{})==requested) {selected=i;break;}
        if(selected==std::numeric_limits<std::size_t>::max())
            throw std::runtime_error("unit '"+requested+"' is not in this scenario");
    } else if(needs_magic) {
        for(std::size_t i=0;i<units.size();++i)
            if(units[i].value("human",false)&&unit_has_magic_attack(units[i])) {selected=i;break;}
        if(selected==std::numeric_limits<std::size_t>::max())
            for(std::size_t i=0;i<units.size();++i) if(units[i].value("human",false)) {selected=i;break;}
    } else {
        for(std::size_t i=0;i<units.size();++i) if(units[i].value("human",false)) {selected=i;break;}
    }
    if(selected==std::numeric_limits<std::size_t>::max())
        throw std::runtime_error("this scenario has no human unit to probe a statistic on");
    const auto& unit=units[selected];
    const auto unit_name=unit.value("name",std::string{});
    if(!unit.value("human",false))
        throw std::runtime_error("unit '"+unit_name+"' is not a human unit; only human stat inputs are probed");
    for(const auto& axis:axes) if(probe_axis_is_stat(axis)) {
        const auto& info=require_probe_axis(axis);
        if(!unit.contains("parameters")||!unit["parameters"].is_object()||!unit["parameters"].contains(info.parameterId))
            throw std::runtime_error("unit '"+unit_name+"' carries no parameter "+info.parameterId+" ("+info.label+") to probe");
    }
    if(needs_magic&&!unit_has_magic_attack(unit))
        throw std::runtime_error("'"+unit_name+"' carries no magic attack skill, so its damage is read from Attack (parameter 13) and Intelligence is not a combat input for it. Probe a human unit that carries a magic attack skill, or equip one first.");
    return {selected,unit_name};
}
std::int64_t probe_pivot_value(const Json& scenario,std::size_t unit_index,const std::string& axis) {
    if(axis=="herbs") {
        if(!scenario.contains("holyHerbStock")||scenario["holyHerbStock"].is_null()) return 0;
        if(!scenario["holyHerbStock"].is_number_integer()||scenario["holyHerbStock"].is_boolean())
            throw std::runtime_error("Holy Herb stock must be an integer probe input");
        return scenario["holyHerbStock"].get<std::int64_t>();
    }
    const auto& info=require_probe_axis(axis);
    const auto& parameter=scenario.at("ownUnits").at(unit_index).at("parameters").at(info.parameterId);
    if(!parameter.contains("rawValue")||parameter["rawValue"].is_null()) return 0;
    if(!parameter["rawValue"].is_number_integer()||parameter["rawValue"].is_boolean())
        throw std::runtime_error("probe pivot rawValue must be an integer");
    return parameter["rawValue"].get<std::int64_t>();
}
std::vector<std::int64_t> probe_default_ladder(const Json& scenario,std::size_t unit_index,
                                               const std::string& axis,std::int64_t points) {
    const auto pivot=probe_pivot_value(scenario,unit_index,axis);
    if(pivot<=0) throw std::runtime_error("the current "+axis+" value is "+std::to_string(pivot)+", which gives no ladder to probe");
    if(points<2) throw std::runtime_error("probe points must be at least 2 for an interpolated default ladder");
    if(points>100000) throw std::runtime_error("probe points exceed the supported explicit proposal limit of 100000");
    std::vector<double> ratios;
    if(points==3) ratios={0.6,1.0,1.4};
    else if(points==5) ratios={0.5,0.75,1.0,1.25,1.5};
    else if(points==7) ratios={0.4,0.6,0.8,1.0,1.2,1.4,1.6};
    else {
        ratios.reserve(static_cast<std::size_t>(points));
        for(std::int64_t i=0;i<points;++i) ratios.push_back(0.4+1.2*static_cast<double>(i)/static_cast<double>(points-1));
    }
    std::set<std::int64_t> values{pivot};
    for(const auto ratio:ratios) values.insert(std::max<std::int64_t>(1,round_like_python(static_cast<double>(pivot)*ratio)));
    return std::vector<std::int64_t>(values.begin(),values.end());
}
void apply_probe_axis(Json& scenario,std::size_t unit_index,const std::string& axis,std::int64_t value) {
    if(axis=="herbs") { scenario["holyHerbStock"]=value; return; }
    const auto& info=require_probe_axis(axis);
    if(!scenario.contains("ownUnits")||!scenario["ownUnits"].is_array()||unit_index>=scenario["ownUnits"].size())
        throw std::runtime_error("probe unit index is out of range");
    auto& unit=scenario["ownUnits"][unit_index];
    if(!unit.value("human",false)||!unit.contains("parameters")||!unit["parameters"].contains(info.parameterId))
        throw std::runtime_error("probe stat requires the selected human carrying parameter "+info.parameterId);
    auto& parameter=unit["parameters"][info.parameterId];
    parameter["rawValue"]=value;
    if((info.parameterId=="10"||info.parameterId=="11"||info.parameterId=="12")&&
       parameter.contains("rawMax")&&!parameter["rawMax"].is_null()) {
        if(!parameter["rawMax"].is_number_integer()||parameter["rawMax"].is_boolean())
            throw std::runtime_error("bounded probe rawMax must be an integer");
        parameter["rawMax"]=std::max(parameter["rawMax"].get<std::int64_t>(),value);
    }
}
Json probe_effective_value(const Json& prepared,std::size_t unit_index,const std::string& unit_name,
                           const std::string& axis) {
    if(axis=="herbs") return nullptr;
    const auto& info=require_probe_axis(axis);
    if(!prepared.contains("ownUnits")||!prepared["ownUnits"].is_array())
        throw std::runtime_error("canonical preparation omitted ownUnits for probe effective value");
    const Json* selected=nullptr;
    for(const auto& unit:prepared["ownUnits"]) if(unit.value("incomingIndex",std::numeric_limits<std::size_t>::max())==unit_index) {
        selected=&unit;break;
    }
    if(!selected) for(const auto& unit:prepared["ownUnits"]) if(unit.value("name",std::string{})==unit_name) {
        selected=&unit;break;
    }
    if(!selected||!selected->contains("inputEffectiveParameters")||
       !selected->at("inputEffectiveParameters").is_object()||
       !selected->at("inputEffectiveParameters").contains(info.parameterId))
        throw std::runtime_error("canonical preparation omitted the pre-refill effective probe parameter");
    const auto& parameter=selected->at("inputEffectiveParameters").at(info.parameterId);
    if(!parameter.contains("value")||!parameter.at("value").is_number_integer())
        throw std::runtime_error("canonical preparation omitted the pre-refill effective probe value");
    return parameter.at("value");
}
std::string probe_cell_label(const std::string& axis,std::int64_t value,
                             const std::string& axis2,const Json& value2,
                             const std::string& unit,const std::string& pivot_label) {
    const auto& first=require_probe_axis(axis);
    const std::string subject=probe_axis_is_stat(axis)||(!axis2.empty()&&probe_axis_is_stat(axis2))?unit:std::string("scenario");
    if(axis2.empty()) return "Probe · "+first.label+" "+std::to_string(value)+" · "+subject+" · "+pivot_label;
    return "Probe · "+first.label+" "+std::to_string(value)+" × "+require_probe_axis(axis2).label+" "+
        std::to_string(value2.get<std::int64_t>())+" · "+subject+" · "+pivot_label;
}
std::int64_t probe_point_count(const Json& request,const char* points_key,std::int64_t default_points,
                               bool interpolation_required) {
    if(!request.contains(points_key)||request[points_key].is_null()) return default_points;
    if(!request[points_key].is_number_integer()||request[points_key].is_boolean())
        throw std::runtime_error(std::string("probe ")+points_key+" must be an integer");
    auto points=request[points_key].get<std::int64_t>();
    if(points==0) points=default_points;
    if(interpolation_required&&points<2) throw std::runtime_error(std::string("probe ")+points_key+" must be at least 2 when generating a default ladder");
    if(interpolation_required&&points>100000) throw std::runtime_error(std::string("probe ")+points_key+" exceeds the explicit proposal limit of 100000");
    return points;
}
std::vector<std::int64_t> probe_values(const Json& request,const char* values_key,
                                       const Json& scenario,std::size_t unit_index,const std::string& axis,
                                       std::int64_t points) {
    if(request.contains(values_key)&&!request[values_key].is_null()) {
        if(!request[values_key].is_array()) throw std::runtime_error(std::string("probe ")+values_key+" must be an integer array");
        if(!request[values_key].empty()) {
            if(request[values_key].size()>100000) throw std::runtime_error(std::string("probe ")+values_key+" exceeds the explicit proposal limit of 100000");
            std::vector<std::int64_t> result; result.reserve(request[values_key].size());
            for(const auto& value:request[values_key]) {
                if(!value.is_number_integer()||value.is_boolean()) throw std::runtime_error(std::string("probe ")+values_key+" entries must be integers");
                result.push_back(value.get<std::int64_t>());
            }
            return result;
        }
    }
    return probe_default_ladder(scenario,unit_index,axis,points);
}
std::string identity(const Json& raw) {
    if (fixed_formation_enabled()) return fixed_formation_identity(raw);
    return sha256_text(canonical(raw));
}
std::string encounter_key(const Json& raw) {
    if (!raw.is_object() || !raw.contains("encounterId") || !raw.at("encounterId").is_number_integer())
        throw std::runtime_error("raw scenario must contain an integer encounterId for per-encounter selection");
    return std::to_string(raw.at("encounterId").get<std::int64_t>());
}
bool has_error(const Json& j) {
    if (!j.is_object()) return true;
    if (j.contains("error")) return true;
    if (j.contains("ok") && j["ok"].is_boolean() && !j["ok"].get<bool>()) return true;
    return false;
}
std::string required_string(const Json& c, const char* key) {
    if (!c.contains(key) || !c[key].is_string() || c[key].get<std::string>().empty())
        throw std::runtime_error(std::string("config requires nonempty string ") + key);
    return c[key].get<std::string>();
}
std::uint64_t positive(const Json& c, const char* key, std::uint64_t max) {
    if (!c.contains(key) || (!c[key].is_number_unsigned() && !c[key].is_number_integer()))
        throw std::runtime_error(std::string("config requires integer ") + key);
    const auto n = c[key].get<std::int64_t>();
    if (n <= 0 || static_cast<std::uint64_t>(n) > max)
        throw std::runtime_error(std::string("config value outside supported range: ") + key);
    return static_cast<std::uint64_t>(n);
}

void flush_file(std::ofstream& f) {
    f.flush();
    if (!f) throw std::runtime_error("durable journal write failed");
}

class DurableJournal {
public:
    explicit DurableJournal(const fs::path& path) {
#ifdef _WIN32
        file_ = _wfsopen(path.c_str(), L"ab", _SH_DENYWR);
#else
        file_ = std::fopen(path.c_str(), "ab");
#endif
        if (!file_) throw std::runtime_error("cannot open result journal");
    }
    ~DurableJournal() { if (file_) std::fclose(file_); }
    void append(const std::string& line, std::uint64_t* durableSyncWallNs = nullptr) {
        if (std::fwrite(line.data(), 1, line.size(), file_) != line.size() || std::fflush(file_) != 0)
            throw std::runtime_error("journal write/flush failed");
#ifdef _WIN32
        const intptr_t raw = _get_osfhandle(_fileno(file_));
        const auto syncStart = durableSyncWallNs ? std::chrono::steady_clock::now() : std::chrono::steady_clock::time_point{};
        if (raw == -1 || !FlushFileBuffers(reinterpret_cast<HANDLE>(raw)))
            throw std::runtime_error("FlushFileBuffers failed for result journal");
#else
        const auto syncStart = durableSyncWallNs ? std::chrono::steady_clock::now() : std::chrono::steady_clock::time_point{};
        if (::fsync(fileno(file_)) != 0) throw std::runtime_error("fsync failed for result journal");
#endif
        if (durableSyncWallNs)
            *durableSyncWallNs += static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
                std::chrono::steady_clock::now() - syncStart).count());
    }
private:
    std::FILE* file_ = nullptr;
};

class OutputLock {
public:
    explicit OutputLock(const fs::path& path) {
#ifdef _WIN32
        handle_ = CreateFileW(path.c_str(), GENERIC_READ | GENERIC_WRITE, 0, nullptr, OPEN_ALWAYS,
                              FILE_ATTRIBUTE_NORMAL, nullptr);
        if (handle_ == INVALID_HANDLE_VALUE) throw std::runtime_error("output directory is already locked by another optimizer");
#else
        fd_ = ::open(path.c_str(), O_CREAT | O_RDWR, 0600);
        if (fd_ < 0 || ::flock(fd_, LOCK_EX | LOCK_NB) != 0) {
            if (fd_ >= 0) ::close(fd_);
            fd_ = -1;
            throw std::runtime_error("output directory is already locked by another optimizer");
        }
#endif
    }
    ~OutputLock() {
#ifdef _WIN32
        if (handle_ != INVALID_HANDLE_VALUE) CloseHandle(handle_);
#else
        if (fd_ >= 0) { ::flock(fd_, LOCK_UN); ::close(fd_); }
#endif
    }
    OutputLock(const OutputLock&) = delete;
    OutputLock& operator=(const OutputLock&) = delete;
private:
#ifdef _WIN32
    HANDLE handle_ = INVALID_HANDLE_VALUE;
#else
    int fd_ = -1;
#endif
};

bool read_score(const Json& result, const Json::json_pointer& ptr, double& out, bool& scored) {
    try {
        if (!result.is_object() || !result.contains("ok") || result.at("ok") != true ||
            !result.contains("report") || !result.at("report").is_object() ||
            result.at("report").size() != 74 || !result.contains("nativeChecksum") ||
            result.at("nativeChecksum").is_null() || !result.contains("reportBytes")) return false;
        const auto& nbytes = result.at("reportBytes");
        if (nbytes.is_number_unsigned()) { if (nbytes.get<std::uint64_t>() == 0) return false; }
        else if (nbytes.is_number_integer()) { if (nbytes.get<std::int64_t>() <= 0) return false; }
        else return false;
        const Json& v = result.at(ptr);
        if (v.is_null()) { scored = false; return true; }
        if (!v.is_number()) return false;
        out = v.get<double>();
        scored = std::isfinite(out);
        return scored;
    } catch (...) { return false; }
}

void validate_provenance(const Json& p) {
    if (!p.is_object()) throw std::runtime_error("provenance must be an object");
    const char* required[] = {"engineSha256", "mechanicsSha256", "policySha256", "abiSha256",
        "arenaSha256", "rawCandidatesSha256", "tablesSha256", "currentKernelSha256"};
    for (const char* k : required) {
        if (!p.contains(k) || p.at(k).is_null() || p.at(k) == "")
            throw std::runtime_error(std::string("provenance is missing required field ") + k);
    }
    const char* hashes[] = {"currentKernelSha256", "engineSha256", "mechanicsSha256", "policySha256",
        "abiSha256", "arenaSha256", "rawCandidatesSha256", "tablesSha256"};
    for (const char* k : hashes) {
        if (!p.at(k).is_string()) throw std::runtime_error(std::string("provenance hash must be a string: ") + k);
        const auto h = p.at(k).get<std::string>();
        if (h.size() != 64 || !std::all_of(h.begin(), h.end(), [](unsigned char c) { return std::isxdigit(c) != 0; }))
            throw std::runtime_error(std::string("provenance hash must be 64 hexadecimal characters: ") + k);
    }
}

std::uint64_t timing_value(const Json& j, const char* key) {
    if (!j.is_object() || !j.contains(key)) return 0;
    const auto& v = j.at(key);
    if (v.is_number_unsigned()) return v.get<std::uint64_t>();
    if (v.is_number_integer() && v.get<std::int64_t>() >= 0)
        return static_cast<std::uint64_t>(v.get<std::int64_t>());
    return 0;
}

void commit_json_file(const fs::path& temporary, const fs::path& target) {
#ifdef _WIN32
    DWORD error=ERROR_SUCCESS;
    // Desktop readers may briefly open without FILE_SHARE_DELETE. Preserve the
    // previous complete file and retry only this bounded transient replacement.
    for(unsigned attempt=0;attempt<20;++attempt) {
        if(MoveFileExW(temporary.c_str(),target.c_str(),MOVEFILE_REPLACE_EXISTING|MOVEFILE_WRITE_THROUGH))return;
        error=GetLastError();
        if(error!=ERROR_SHARING_VIOLATION&&error!=ERROR_LOCK_VIOLATION&&error!=ERROR_ACCESS_DENIED)break;
        if(attempt+1<20)std::this_thread::sleep_for(std::chrono::milliseconds(25));
    }
    throw std::runtime_error("JSON replacement failed for "+target.string()+" (Windows error "+std::to_string(error)+"); previous file and temporary output preserved");
#else
    fs::rename(temporary,target);
#endif
}

void write_status(const fs::path& output, const Json& value) {
    static std::mutex statusWriteMutex;
    std::lock_guard<std::mutex> lock(statusWriteMutex);
    const fs::path path = output / "status.json";
    const fs::path tmp = path.string() + ".tmp";
    { std::ofstream f(tmp, std::ios::binary | std::ios::trunc);
      if (!f) throw std::runtime_error("cannot write status.json");
      f << value.dump(2) << '\n'; f.flush(); if (!f) throw std::runtime_error("status.json flush failed"); }
    commit_json_file(tmp,path);
}

// Only configured integer JSON pointers are mutated. Bounds are checked, never clamped.
Json mutate(const Json& parent, const Json& spec, std::uint64_t ordinal) {
    Json child = parent;
    const auto op=spec.value("op",std::string("integer-step"));
    // Fixed-formation policy: the ONLY legal generated change is one Synthetic DPS raw stat, still
    // inside the embedded walls. Every other operator is unreachable because the search is seeded
    // with fixed-dps-stat arms only and admission refuses non-compliant scenarios.
    if(op=="fixed-dps-stat") {
        if(!spec.contains("parameter")||!spec["parameter"].is_string()) throw std::runtime_error("fixed-dps-stat requires a parameter id");
        return mutate_fixed_formation_parameter(parent,spec["parameter"].get<std::string>(),ordinal);
    }
    if(op=="swap") {
        const auto pointer=Json::json_pointer(spec.at("pointer").get<std::string>());
        Json& values=child[pointer];
        if(!values.is_array()) throw std::runtime_error("swap mutation target is not an array");
        const auto i=spec.at("i").get<std::size_t>(), j=spec.at("j").get<std::size_t>();
        if(i>=values.size()||j>=values.size()||i==j) throw std::runtime_error("swap indices are invalid");
        std::swap(values[i],values[j]);
        return child;
    }
    if(op=="skill-order") {
        const auto unit=spec.at("unit").get<std::size_t>();
        if(!child.contains("ownUnits")||!child["ownUnits"].is_array()||unit>=child["ownUnits"].size()) throw std::runtime_error("skill-order unit is out of range");
        auto& row=child["ownUnits"][unit];
        if(!row.contains("skills")||!row["skills"].is_array()||!row.contains("invocationLevels")||!row["invocationLevels"].is_array()||row["skills"].size()!=row["invocationLevels"].size()) throw std::runtime_error("skill-order arrays are missing or misaligned");
        const auto i=spec.at("i").get<std::size_t>(), j=spec.at("j").get<std::size_t>();
        if(i>=row["skills"].size()||j>=row["skills"].size()||i==j) throw std::runtime_error("skill-order indices are invalid");
        std::swap(row["skills"][i],row["skills"][j]);
        std::swap(row["invocationLevels"][i],row["invocationLevels"][j]);
        return child;
    }
    if(op=="remove-skill") {
        const auto unit=spec.at("unit").get<std::size_t>(), slot=spec.at("slot").get<std::size_t>();
        if(!child.contains("ownUnits")||!child["ownUnits"].is_array()||unit>=child["ownUnits"].size()) throw std::runtime_error("remove-skill unit is out of range");
        auto& row=child["ownUnits"][unit];
        if(!row.contains("skills")||!row["skills"].is_array()||!row.contains("invocationLevels")||!row["invocationLevels"].is_array()||row["skills"].size()!=row["invocationLevels"].size()||slot>=row["skills"].size()) throw std::runtime_error("remove-skill slot is invalid or unpaired");
        const auto skill=row["skills"][slot];
        row["skills"].erase(row["skills"].begin()+static_cast<Json::difference_type>(slot));
        row["invocationLevels"].erase(row["invocationLevels"].begin()+static_cast<Json::difference_type>(slot));
        if(row.contains("invokingSkills")&&row["invokingSkills"].is_array()&&std::find(row["skills"].begin(),row["skills"].end(),skill)==row["skills"].end()) {
            for(auto it=row["invokingSkills"].begin();it!=row["invokingSkills"].end();) {
                if(it->is_array()&&it->size()==2&&(*it)[0]==skill) it=row["invokingSkills"].erase(it); else ++it;
            }
        }
        return child;
    }
    if(op=="remove-skill-group") {
        if(!spec.contains("slots")||!spec["slots"].is_array()) throw std::runtime_error("remove-skill-group slots must be an array");
        std::map<std::size_t,std::vector<std::size_t>> slots;
        for(const auto& pair:spec["slots"]) { if(!pair.is_array()||pair.size()!=2) throw std::runtime_error("remove-skill-group slot must be [unit,slot]"); slots[pair[0].get<std::size_t>()].push_back(pair[1].get<std::size_t>()); }
        for(auto& [unit,indices]:slots) {
            if(!child.contains("ownUnits")||!child["ownUnits"].is_array()||unit>=child["ownUnits"].size()) throw std::runtime_error("remove-skill-group unit out of range");
            std::sort(indices.begin(),indices.end(),std::greater<std::size_t>());
            for(auto slot:indices) child=mutate(child,Json{{"op","remove-skill"},{"unit",unit},{"slot",slot}},ordinal);
        }
        return child;
    }
    if(op=="set-value") {
        const auto pointer=Json::json_pointer(spec.at("pointer").get<std::string>());
        Json& value=child[pointer]; if(!value.is_number_integer()) throw std::runtime_error("set-value target is not an integer");
        const auto next=spec.at("value").get<std::int64_t>();
        if(spec.contains("min")&&next<spec.at("min").get<std::int64_t>()) throw std::runtime_error("set-value is below configured legal range");
        if(spec.contains("max")&&next>spec.at("max").get<std::int64_t>()) throw std::runtime_error("set-value is above configured legal range");
        value=next; return child;
    }
    if(op=="set-axis") {
        const auto unit=spec.value("unitIndex",std::numeric_limits<std::size_t>::max());
        const auto axis=spec.at("axis").get<std::string>();
        const auto target=spec.at("value").get<std::int64_t>();
        if(target<0||target>std::numeric_limits<std::int32_t>::max()) throw std::runtime_error("axis target is outside signed32 nonnegative range");
        if(axis=="herbs") { child["holyHerbStock"]=target; return child; }
        if(!child.contains("ownUnits")||!child["ownUnits"].is_array()||unit>=child["ownUnits"].size()) throw std::runtime_error("axis unit index is out of range");
        static const std::map<std::string,std::string> parameters{{"hp","10"},{"mp","11"},{"vig","12"},{"int","18"},{"atk","13"},{"def","14"},{"spd","15"},{"lck","16"},{"dex","19"}};
        const auto pi=parameters.find(axis); if(pi==parameters.end()) throw std::runtime_error("unknown tuning axis: "+axis);
        auto& row=child["ownUnits"][unit];
        if(!row.value("human",false)||!row.contains("parameters")||!row["parameters"].contains(pi->second)) throw std::runtime_error("tuning axes require a human carrying the requested parameter");
        auto& parameter=row["parameters"][pi->second]; parameter["rawValue"]=target;
        if(axis=="hp"||axis=="mp"||axis=="vig") parameter["rawMax"]=std::max(parameter.value("rawMax",std::int64_t(0)),target);
        return child;
    }
    if(op=="stat-step") {
        const auto unit=spec.at("unitIndex").get<std::size_t>();
        const auto id=spec.at("parameter").get<std::string>();
        if(id!="10"&&id!="11"&&id!="12"&&id!="13"&&id!="14"&&id!="15"&&id!="16"&&id!="18"&&id!="19") throw std::runtime_error("unsupported generated stat parameter");
        if(!child.contains("ownUnits")||!child["ownUnits"].is_array()||unit>=child["ownUnits"].size()) throw std::runtime_error("generated stat unit is out of range");
        auto& parameters=child["ownUnits"][unit]["parameters"];
        if(!parameters.contains(id)||!parameters[id].contains("rawValue")) throw std::runtime_error("generated stat parameter is absent");
        const auto step=spec.value("step",std::int64_t(0));
        const auto old=parameters[id]["rawValue"].get<std::int64_t>();
        if((step>0&&old>std::numeric_limits<std::int64_t>::max()-step)||(step<0&&old<std::numeric_limits<std::int64_t>::min()-step)) throw std::runtime_error("generated stat arithmetic overflow");
        const auto next=old+step;
        if(next<0||next>std::numeric_limits<std::int32_t>::max()) throw std::runtime_error("generated stat target outside signed32 nonnegative range");
        parameters[id]["rawValue"]=next;
        if(id=="10"||id=="11"||id=="12") parameters[id]["rawMax"]=std::max(parameters[id].value("rawMax",std::int64_t(0)),next);
        return child;
    }
    if(op!="integer-step") throw std::runtime_error("unsupported mutation op: "+op);
    const auto pointer = Json::json_pointer(spec.at("pointer").get<std::string>());
    Json& value = child[pointer];
    if (!value.is_number_integer()) throw std::runtime_error("mutation target is not an integer");
    const auto step = spec.value("step", 1LL);
    if (step == 0) throw std::runtime_error("mutation step cannot be zero");
    const auto base = value.get<std::int64_t>();
    const auto stride = spec.value("stride", 1LL);
    if (stride <= 0) throw std::runtime_error("mutation stride must be positive");
    const auto multiplier = static_cast<std::int64_t>(1 + (ordinal % static_cast<std::uint64_t>(stride)));
    if (step > 0 && multiplier > std::numeric_limits<std::int64_t>::max() / step)
        throw std::runtime_error("mutation arithmetic overflow");
    if (step < 0 && step < std::numeric_limits<std::int64_t>::min() / multiplier)
        throw std::runtime_error("mutation arithmetic overflow");
    const auto delta = step * multiplier;
    if ((delta > 0 && base > std::numeric_limits<std::int64_t>::max() - delta) ||
        (delta < 0 && base < std::numeric_limits<std::int64_t>::min() - delta))
        throw std::runtime_error("mutation arithmetic overflow");
    const auto next = base + delta;
    const auto lo = spec.at("min").get<std::int64_t>();
    const auto hi = spec.at("max").get<std::int64_t>();
    if (lo > hi || next < lo || next > hi) throw std::runtime_error("mutation would exceed configured legal range");
    value = next;
    return child;
}

std::vector<Json> native_mutations(const Json& raws,const SearchBounds* bounds=nullptr) {
    std::map<std::string,Json> unique;
    for(const auto& raw:raws) {
        if(!raw.is_object()||!raw.contains("ownUnits")||!raw["ownUnits"].is_array()) continue;
        for(std::size_t ui=0;ui<raw["ownUnits"].size();++ui) {
            const auto& unit=raw["ownUnits"][ui];
            if(unit.contains("skills")&&unit["skills"].is_array())
                for(std::size_t i=0;i<unit["skills"].size();++i) {
                    Json remove{{"op","remove-skill"},{"unit",ui},{"slot",i}}; unique[remove.dump()]=remove;
                    for(std::size_t j=i+1;j<unit["skills"].size();++j) { Json m{{"op","skill-order"},{"unit",ui},{"i",i},{"j",j}}; unique[m.dump()]=m; }
                }
            if(bounds&&!bounds->byParameter.empty()&&unit.contains("parameters")&&unit["parameters"].is_object()) for(auto p=unit["parameters"].begin();p!=unit["parameters"].end();++p) {
                const std::set<std::string> combatAxes{"10","11","12","13","14","15","16","18","19"};
                if(!combatAxes.count(p.key())) continue;
                if(p.key()=="18"&&(!unit.value("human",false)||!unit.contains("skills")||!unit["skills"].is_array()||
                    !std::any_of(unit["skills"].begin(),unit["skills"].end(),[](const Json& s){return s.is_number_integer()&&s.get<std::int64_t>()>=5&&s.get<std::int64_t>()<=19;}))) continue;
                if(!p.value().is_object()||!p.value().contains("rawValue")||!p.value().contains("rawMax")||!p.value()["rawValue"].is_number_integer()||!p.value()["rawMax"].is_number_integer()) continue;
                const auto value=p.value()["rawValue"].get<std::int64_t>();
                if(value<0) continue;
                for(const auto step:{-1LL,1LL,-5LL,5LL}) {
                    const auto interval=bounds->byParameter.find(p.key()); if(interval==bounds->byParameter.end()) continue;
                    Json m{{"op","stat-step"},{"unitIndex",ui},{"parameter",p.key()},{"step",step},
                           {"effectiveBounds",{{"minimum",interval->second.first},{"maximum",interval->second.second}}},
                           {"boundsSource",bounds->source},{"boundsSha256",bounds->sourceHash}};
                    unique[m.dump()]=m;
                }
            }
        }
    }
    std::vector<Json> result; result.reserve(unique.size()); for(auto& [_,m]:unique) result.push_back(std::move(m)); return result;
}

std::vector<Json> native_skill_removals(const Json& raw,std::size_t selectedUnit,bool namedUnitOnly) {
    std::vector<Json> result;
    if(!raw.is_object()||!raw.contains("ownUnits")||!raw["ownUnits"].is_array()) return result;
    std::map<std::int64_t,std::vector<std::pair<std::size_t,std::size_t>>> groups;
    for(std::size_t ui=0;ui<raw["ownUnits"].size();++ui) {
        if(namedUnitOnly&&ui!=selectedUnit) continue;
        const auto& unit=raw["ownUnits"][ui];
        if(!unit.value("human",false)||!unit.contains("skills")||!unit["skills"].is_array()) continue;
        for(std::size_t si=0;si<unit["skills"].size();++si) {
            const auto skill=unit["skills"][si].get<std::int64_t>();
            if(!namedUnitOnly) groups[skill].push_back({ui,si});
            result.push_back(Json{{"op","remove-skill"},{"unit",ui},{"slot",si},{"skillId",skill},
                {"label",unit.value("name",std::string("Unit"))+": remove skill #"+std::to_string(skill)}});
        }
    }
    // The original experiment groups copies across the whole team. Such an arm is not
    // unit-scoped, so named-unit requests retain only that unit's original single-slot arms.
    for(const auto& [skill,positions]:groups) if(positions.size()>1) {
        Json slots=Json::array(); for(const auto& [unit,slot]:positions) slots.push_back(Json::array({unit,slot}));
        result.push_back(Json{{"op","remove-skill-group"},{"slots",slots},{"skillId",skill},
            {"label","Team: remove all copies of skill #"+std::to_string(skill)}});
    }
    return result;
}

void enforce_generated_stat_bound(const Json& spec,const Json& prepared) {
    if(spec.value("op",std::string{})!="stat-step"||!spec.contains("effectiveBounds")) return;
    const auto unitIndex=spec.at("unitIndex").get<std::size_t>();
    const auto id=spec.at("parameter").get<std::string>();
    if(!prepared.contains("ownUnits")||!prepared["ownUnits"].is_array()) throw std::runtime_error("prepared result has no units for effective stat bounds");
    const Json* unit=nullptr;
    for(const auto& row:prepared["ownUnits"]) if(row.value("incomingIndex",std::size_t(-1))==unitIndex) { unit=&row; break; }
    if(!unit||!unit->contains("effectiveParameters")||!unit->at("effectiveParameters").contains(id)) throw std::runtime_error("prepared effective stat missing for generated bound check");
    const auto& effective=unit->at("effectiveParameters").at(id);
    const auto key=(id=="10"||id=="11")?"maximum":"value";
    if(!effective.contains(key)||!effective.at(key).is_number_integer()) throw std::runtime_error("prepared effective stat value missing for generated bound check");
    const auto value=effective.at(key).get<std::int64_t>();
    const auto lo=spec["effectiveBounds"].at("minimum").get<std::int64_t>();
    const auto hi=spec["effectiveBounds"].at("maximum").get<std::int64_t>();
    if(value<lo||value>hi) throw std::runtime_error("generated stat child outside original synthetic effective-stat search bounds");
}

struct Queue {
    std::mutex m; std::condition_variable cv; std::queue<Job> q; bool stop = false;
    void push(Job j,const std::function<void(const Job&)>& accepted) {
        { std::lock_guard<std::mutex> g(m); q.push(std::move(j)); if(accepted) accepted(q.back()); }
        cv.notify_one();
    }
    bool pop(Job& j,const std::function<void(const Job&)>& started) {
        std::unique_lock<std::mutex> g(m); cv.wait(g, [&]{ return stop || !q.empty(); });
        if (q.empty()) return false; j=std::move(q.front()); q.pop(); if(started) started(j); return true;
    }
};
struct JoinOnExit {
    Queue& queue; std::vector<std::thread>& pool; bool joined = false;
    ~JoinOnExit() {
        if (joined) return;
        { std::lock_guard<std::mutex> g(queue.m); queue.stop = true; }
        queue.cv.notify_all();
        for (auto& t : pool) if (t.joinable()) t.join();
    }
};

// Scoped activation of the fixed-formation policy for a single CLI proposal request.
struct FixedFormationScope {
    bool previous;
    explicit FixedFormationScope(bool enabled) : previous(fixed_formation_enabled()) {
        if (enabled) set_fixed_formation_enabled(true);
    }
    ~FixedFormationScope() { set_fixed_formation_enabled(previous); }
    FixedFormationScope(const FixedFormationScope&) = delete;
    FixedFormationScope& operator=(const FixedFormationScope&) = delete;
};

} // namespace

Json propose(const Json& raw,const Json& tables,const Json& request) {
    try {
        if(!request.is_object()) throw std::runtime_error("proposal request must be an object");
        const bool fixedFormation=request.value("fixedFormation",false);
        const FixedFormationScope fixedScope(fixedFormation);
        const std::string mode=request.value("mode",std::string("search"));
        if(mode!="search"&&mode!="tuning"&&mode!="skills"&&mode!="evaluate"&&mode!="probe") throw std::runtime_error("mode must be search, tuning, skills, evaluate or probe");
        if(fixedFormation&&mode!="search"&&mode!="evaluate") throw std::runtime_error("fixed-formation policy supports only search or evaluate proposals");
        if(request.contains("count")&&(!request["count"].is_number_integer()||request["count"].is_boolean()||request["count"].get<std::int64_t>()<1||request["count"].get<std::int64_t>()>1000000000LL)) throw std::runtime_error("count must be a positive integer");
        if(mode=="search"&&request.contains("count")&&request["count"].get<std::int64_t>()>256) throw std::runtime_error("search proposal count must be at most 256");
        if(mode=="skills"&&(!request.contains("count")||request["count"].get<std::int64_t>()<128)) throw std::runtime_error("skill experiments require at least 128 paired seeds per arm");
        if(request.contains("seedPairs")) {
            if(!request["seedPairs"].is_array()||request["seedPairs"].empty()) throw std::runtime_error("seedPairs must be a nonempty array when supplied");
            std::set<std::string> uniqueSeeds;
            for(const auto& pair:request["seedPairs"]) {
                if(!pair.is_array()||pair.size()!=2||!pair[0].is_number_integer()||pair[0].is_boolean()||!pair[1].is_number_integer()||pair[1].is_boolean()||pair[0].get<std::int64_t>()<0||pair[1].get<std::int64_t>()<0||pair[0].get<std::int64_t>()>2147483647LL||pair[1].get<std::int64_t>()>2147483647LL) throw std::runtime_error("each seedPairs entry must contain two nonnegative signed31 seeds");
                if(!uniqueSeeds.insert(pair.dump()).second) throw std::runtime_error("seedPairs must be unique to avoid duplicate paired battles");
            }
            if(mode=="skills"&&request["seedPairs"].size()!=static_cast<std::size_t>(request["count"].get<std::int64_t>())) throw std::runtime_error("paired seed count must equal the requested experiment count");
        }
        if(request.contains("seed")&&(!request["seed"].is_number_integer()||request["seed"].is_boolean()||request["seed"].get<std::int64_t>()<0)) throw std::runtime_error("seed must be a nonnegative integer");
        auto count=request.value("count",std::size_t(4));
        const auto seed=request.value("seed",std::uint64_t(0));
        const auto generation=request.value("generation",std::uint64_t(1));
        const SearchBounds proposalBounds=load_search_bounds(request);
        Json specs=request.value("mutations",Json::array());
        if(!specs.is_array()) throw std::runtime_error("request.mutations must be an array");
        const std::map<std::string,std::string> axisParameters{{"hp","10"},{"mp","11"},{"vig","12"},{"int","18"},{"atk","13"},{"def","14"},{"spd","15"},{"lck","16"},{"dex","19"}};
        std::string axis,unitName; std::size_t unitIndex=std::numeric_limits<std::size_t>::max();
        std::string parameterId; std::int64_t baselineInput=0,effectiveContribution=0; Json baselineEffective=nullptr;
        bool tuningBounded=false,defaultTargetsGenerated=false,defaultTargetsUsedVerifiedBounds=false;
        Json requestedTargets=Json::array();
        std::string targetDomain="rawValue",tuningDirection="both";
        bool targetsProvided=false;
        std::string probeAxis,probeAxis2,probeUnit,probePivotLabel;
        std::string probePivotId;
        std::size_t probeUnitIndex=std::numeric_limits<std::size_t>::max();
        Json probePivotCandidateId=nullptr;
        Json probePivotEffective=nullptr,probePivotEffective2=nullptr;
        std::vector<std::int64_t> probeLadder,probeLadder2;
        std::int64_t probePivotInput=0,probePivotInput2=0,probePoints=7,probePoints2=7,probeRuns=160;
        bool probeHasAxis2=false,probeUnestablished=false;
        if(mode=="probe") {
            if(!request.contains("axis")||!request["axis"].is_string()||request["axis"].get<std::string>().empty())
                throw std::runtime_error("probe request requires a named axis");
            probeAxis=request["axis"].get<std::string>();
            require_probe_axis(probeAxis);
            if(request.contains("axis2")&&!request["axis2"].is_null()) {
                if(!request["axis2"].is_string()) throw std::runtime_error("probe axis2 must be a named axis or null");
                probeAxis2=request["axis2"].get<std::string>();
                probeHasAxis2=!probeAxis2.empty();
                if(probeHasAxis2) require_probe_axis(probeAxis2);
            }
            if(request.contains("candidateId")&&!request["candidateId"].is_null()) {
                if(!request["candidateId"].is_string()||request["candidateId"].get<std::string>().empty())
                    throw std::runtime_error("probe candidateId must be a nonempty string");
                probePivotCandidateId=request["candidateId"];
            } else if(request.contains("pivotCandidateId")&&!request["pivotCandidateId"].is_null()) {
                if(!request["pivotCandidateId"].is_string()||request["pivotCandidateId"].get<std::string>().empty())
                    throw std::runtime_error("probe pivotCandidateId must be a nonempty string");
                probePivotCandidateId=request["pivotCandidateId"];
            }
            for(const char* key:{"pivotLabel","candidateLabel"}) if(request.contains(key)&&!request[key].is_null()) {
                if(!request[key].is_string()) throw std::runtime_error(std::string("probe ")+key+" must be a string");
                if(probePivotLabel.empty()) probePivotLabel=request[key].get<std::string>();
            }
            if(request.contains("unestablished")&&!request["unestablished"].is_null()) {
                if(!request["unestablished"].is_boolean()) throw std::runtime_error("probe unestablished must be boolean");
                probeUnestablished=request["unestablished"].get<bool>();
            }
            if(request.contains("runs")&&!request["runs"].is_null()) {
                if(!request["runs"].is_number_integer()||request["runs"].is_boolean())
                    throw std::runtime_error("probe runs must be an integer");
                probeRuns=request["runs"].get<std::int64_t>();
                if(probeRuns==0) probeRuns=160;
                probeRuns=std::max<std::int64_t>(64,std::min<std::int64_t>(512,probeRuns));
            }
            std::vector<std::string> probeAxes{probeAxis};
            if(probeHasAxis2) probeAxes.push_back(probeAxis2);
            Json requestedUnit=request.value("unit",Json(nullptr));
            auto resolved=resolve_probe_unit(raw,probeAxes,requestedUnit);
            probeUnitIndex=resolved.first; probeUnit=resolved.second;
            const bool defaultLadder1=!request.contains("values")||request["values"].is_null()||
                (request["values"].is_array()&&request["values"].empty());
            probePoints=probe_point_count(request,"points",7,defaultLadder1);
            probeLadder=probe_values(request,"values",raw,probeUnitIndex,probeAxis,probePoints);
            probePivotInput=probe_pivot_value(raw,probeUnitIndex,probeAxis);
            if(probeHasAxis2) {
                const bool defaultLadder2=!request.contains("values2")||request["values2"].is_null()||
                    (request["values2"].is_array()&&request["values2"].empty());
                probePoints2=probe_point_count(request,"points2",probePoints,defaultLadder2);
                probeLadder2=probe_values(request,"values2",raw,probeUnitIndex,probeAxis2,probePoints2);
                probePivotInput2=probe_pivot_value(raw,probeUnitIndex,probeAxis2);
            }
            constexpr std::size_t maxProbeCells=10000;
            const auto secondSize=probeHasAxis2?probeLadder2.size():std::size_t(1);
            if(probeLadder.empty()||secondSize==0||probeLadder.size()>maxProbeCells/secondSize)
                throw std::runtime_error("probe grid exceeds the bounded 10000-cell native preparation proposal limit");
            specs=Json::array();
            for(const auto first:probeLadder) for(std::size_t j=0;j<secondSize;++j) {
                Json spec{{"op","probe-cell"},{"axis",probeAxis},{"value",first},{"unitIndex",probeUnitIndex},
                    {"unit",probeUnit},{"probeCellIndex",specs.size()}};
                if(probeHasAxis2) { spec["axis2"]=probeAxis2; spec["value2"]=probeLadder2[j]; }
                specs.push_back(std::move(spec));
            }
            count=specs.size();
        } else if(mode=="tuning") {
            for(const char* unsupported:{"axis2","step","budget"})
                if(request.contains(unsupported)&&!request[unsupported].is_null())
                    throw std::runtime_error(std::string("C++ native tuning proposals do not support ")+unsupported+
                        "; they are not silently ignored and require the original progressive fine-tune coordinator");
            if(!request.contains("axis")||!request["axis"].is_string()) throw std::runtime_error("tuning request requires a named axis");
            axis=request["axis"].get<std::string>();
            if(axis!="herbs"&&!axisParameters.count(axis)) throw std::runtime_error("unsupported tuning axis: "+axis);
            if(request.contains("targetDomain")) {
                if(!request["targetDomain"].is_string()) throw std::runtime_error("targetDomain must be 'rawValue' or 'effective'");
                targetDomain=request["targetDomain"].get<std::string>();
            }
            if(targetDomain!="rawValue"&&targetDomain!="effective") throw std::runtime_error("targetDomain must be 'rawValue' or 'effective'");
            if(request.contains("direction")&&!request["direction"].is_null()) {
                if(!request["direction"].is_string()) throw std::runtime_error("direction must be 'down', 'up' or 'both'");
                tuningDirection=request["direction"].get<std::string>();
                if(tuningDirection!="down"&&tuningDirection!="up"&&tuningDirection!="both")
                    throw std::runtime_error("direction must be 'down', 'up' or 'both'");
            }
            targetsProvided=request.contains("targets")&&!request["targets"].is_null();
            if(targetsProvided&&!request["targets"].is_array()) throw std::runtime_error("targets must be an array of unique nonnegative signed32 integers");
            if(targetsProvided)requestedTargets=request["targets"];
            if(targetDomain=="effective"&&axis=="herbs") throw std::runtime_error("effective targetDomain requires a combat-stat axis; herbs uses rawValue");
            if((!targetsProvided||requestedTargets.empty())&&targetDomain!="effective")
                throw std::runtime_error("targets may be omitted only for targetDomain='effective'; legacy rawValue tuning requires explicit targets");
            std::set<std::int64_t> uniqueTargets;
            for(const auto& target:requestedTargets) if(!target.is_number_integer()||target.is_boolean()||target.get<std::int64_t>()<0||target.get<std::int64_t>()>std::numeric_limits<std::int32_t>::max()||!uniqueTargets.insert(target.get<std::int64_t>()).second) throw std::runtime_error("targets must be unique nonnegative signed32 integers");
            if(axis!="herbs") parameterId=axisParameters.at(axis);
        } else if(mode=="skills") {
            const bool namedUnitOnly=request.contains("unit");
            std::size_t selectedUnit=std::numeric_limits<std::size_t>::max();
            std::string selectedUnitName;
            if(namedUnitOnly) {
                if(!request["unit"].is_string()) throw std::runtime_error("skills unit must be a nonempty exact unit name string");
                selectedUnitName=request["unit"].get<std::string>();
                if(selectedUnitName.empty()) throw std::runtime_error("skills unit must be a nonempty exact unit name string");
                if(!raw.is_object()||!raw.contains("ownUnits")||!raw["ownUnits"].is_array())
                    throw std::runtime_error("named-unit skill removal requires an ownUnits roster");
                std::vector<std::size_t> matches;
                for(std::size_t ui=0;ui<raw["ownUnits"].size();++ui) {
                    const auto& unit=raw["ownUnits"][ui];
                    if(unit.is_object()&&unit.value("human",false)&&unit.contains("name")&&unit["name"].is_string()&&
                       unit["name"].get<std::string>()==selectedUnitName) matches.push_back(ui);
                }
                if(matches.empty()) throw std::runtime_error("named-unit skill removal requires an exact matching human roster entry");
                if(matches.size()!=1) throw std::runtime_error("named-unit skill removal requires exactly one matching human roster entry");
                selectedUnit=matches.front();
            }
            specs=native_skill_removals(raw,selectedUnit,namedUnitOnly);
            if(tables.is_object()&&tables.contains("weapon-skill-profiles")&&tables["weapon-skill-profiles"].contains("skills")) {
                const auto& skillRows=tables["weapon-skill-profiles"]["skills"];
                for(auto& spec:specs) {
                    const auto skill=spec.value("skillId",std::int64_t(-1));
                    std::string name="skill #"+std::to_string(skill);
                    if(skillRows.is_array()) for(const auto& row:skillRows) if(row.value("id",std::int64_t(-2))==skill) {
                        for(const char* key:{"name","label","skillName"}) if(row.contains(key)&&row[key].is_string()&&!row[key].get<std::string>().empty()) {name=row[key].get<std::string>();break;}
                        break;
                    }
                    spec["label"]=spec.value("op",std::string{})=="remove-skill-group"?"Team: remove all copies of "+name:
                        raw["ownUnits"][spec.at("unit").get<std::size_t>()].value("name",std::string("Unit"))+": remove "+name;
                }
            }
            count=specs.size();
            if(count==0) throw std::runtime_error("this intent has no human skills to remove");
        } else if(mode=="evaluate") { specs=Json::array(); count=0; }
        else if(specs.empty()) specs=fixedFormation?fixed_formation_mutation_specs():native_mutations(Json::array({raw}),&proposalBounds);
        Json parent=admit(raw,tables);
        Json parentPreparation=prepare(parent,tables,mode=="probe",&raw);
        if(has_error(parentPreparation)) throw std::runtime_error("parent failed canonical native preparation");
        const auto parentId=identity(raw);
        if(mode=="probe") {
            probePivotId=probePivotCandidateId.is_string()?probePivotCandidateId.get<std::string>():parentId;
            if(probePivotLabel.empty()) probePivotLabel=probePivotId;
            probePivotEffective=probe_effective_value(parentPreparation,probeUnitIndex,probeUnit,probeAxis);
            if(probeHasAxis2) probePivotEffective2=probe_effective_value(parentPreparation,probeUnitIndex,probeUnit,probeAxis2);
        }
        if(mode=="tuning"&&axis!="herbs") {
            if(!parent.contains("ownUnits")||!parent["ownUnits"].is_array()) throw std::runtime_error("tuning parent has no ownUnits");
            std::string requestedUnit;
            if(request.contains("unit")) { if(!request["unit"].is_string()) throw std::runtime_error("unit must be a unit name string"); requestedUnit=request["unit"].get<std::string>(); }
            auto usable=[&](std::size_t index) {
                const auto& u=parent["ownUnits"][index];
                if(!u.value("human",false)||!u.contains("parameters")||!u["parameters"].contains(parameterId)) return false;
                if(axis=="int") return u.contains("skills")&&u["skills"].is_array()&&std::any_of(u["skills"].begin(),u["skills"].end(),[](const Json& s){return s.is_number_integer()&&s.get<std::int64_t>()>=5&&s.get<std::int64_t>()<=19;});
                return true;
            };
            if(!requestedUnit.empty()) {
                for(std::size_t i=0;i<parent["ownUnits"].size();++i) if(parent["ownUnits"][i].value("name",std::string{})==requestedUnit) {unitIndex=i;break;}
                if(unitIndex==std::numeric_limits<std::size_t>::max()) throw std::runtime_error("requested tuning unit is absent");
                if(!usable(unitIndex)) throw std::runtime_error(axis=="int"?"INT tuning requires a human carrying a magic attack skill":"requested unit cannot express this stat axis");
            } else {
                for(std::size_t i=0;i<parent["ownUnits"].size();++i) if(usable(i)) {unitIndex=i;break;}
                if(unitIndex==std::numeric_limits<std::size_t>::max()) throw std::runtime_error(axis=="int"?"INT tuning requires a human carrying a magic attack skill":"scenario has no eligible human tuning unit");
            }
            unitName=parent["ownUnits"][unitIndex].value("name",std::string{});
            baselineInput=parent["ownUnits"][unitIndex]["parameters"][parameterId]["rawValue"].get<std::int64_t>();
            tuningBounded=axis=="hp"||axis=="mp"||axis=="vig";
            baselineEffective=prepared_battle_value(parentPreparation,unitIndex,unitName,parameterId,tuningBounded);
            if(targetDomain=="effective") effectiveContribution=effective_equipment_contribution(
                parent,parentPreparation,unitIndex,unitName,parameterId,tuningBounded);
            if(targetDomain=="effective"&&(!targetsProvided||requestedTargets.empty())) {
                defaultTargetsGenerated=true;
                const auto bounds=proposalBounds.byParameter.find(parameterId);
                if(bounds!=proposalBounds.byParameter.end()&&
                   (proposalBounds.source.empty()||proposalBounds.sourceHash.empty()))
                    throw std::runtime_error("cannot generate the effective broad tuning ladder without verified stat-bounds provenance");
                const auto* interval=bounds==proposalBounds.byParameter.end()?nullptr:&bounds->second;
                defaultTargetsUsedVerifiedBounds=interval!=nullptr;
                requestedTargets=effective_broad_targets(baselineEffective.get<std::int64_t>(),interval,tuningDirection);
                if(requestedTargets.empty())
                    throw std::runtime_error("original effective broad tuning ladder produced no targets after applying the baseline and direction");
                for(const auto& target:requestedTargets) if(interval&&
                    (target.get<std::int64_t>()<interval->first||target.get<std::int64_t>()>interval->second))
                        throw std::runtime_error("effective broad target escaped the verified stat-bounds interval");
            }
            specs=Json::array();
            for(const auto& target:requestedTargets) specs.push_back(Json{{"op","set-axis"},{"axis",axis},{"unitIndex",unitIndex},{"unit",unitName},{"value",target},{"targetDomain",targetDomain},{"label",axis+"="+std::to_string(target.get<std::int64_t>())}});
            count=specs.size();
        }
        Json children=Json::array(), rejected=Json::array(),probeReused=Json::array(),probeGrid=Json::array();
        std::unordered_set<std::string> seen;
        seen.insert(parentId);
        std::map<std::string,std::string> probeCandidateByIdentity;
        std::map<std::string,Json> probeValuesByIdentity;
        if(mode=="probe") {
            probeCandidateByIdentity[parentId]=probePivotId;
            probeValuesByIdentity[parentId]=Json{{"inputValue",probePivotInput},
                {"inputValue2",probeHasAxis2?Json(probePivotInput2):Json(nullptr)},
                {"effectiveValue",probePivotEffective},{"effectiveValue2",probePivotEffective2}};
        }
        if(!specs.empty()) {
            const auto offset=mode=="probe"?std::size_t(0):static_cast<std::size_t>(seed%specs.size());
            for(std::size_t n=0;n<specs.size()&&children.size()<count;++n) {
                const auto index=(offset+n)%specs.size();
                const auto& spec=specs[index];
                Json child=Json::object();
                Json probeCell=Json::object();
                std::string probeLabel;
                try {
                    if(mode=="probe") {
                        child=raw;
                        const auto first=spec.at("value").get<std::int64_t>();
                        const Json second=probeHasAxis2?spec.at("value2"):Json(nullptr);
                        probeLabel=probe_cell_label(probeAxis,first,probeHasAxis2?probeAxis2:std::string{},second,
                            probeUnit,probePivotLabel);
                        probeCell={{"cellIndex",spec.at("probeCellIndex")},{"axis",probeAxis},{"value",first},
                            {"axis2",probeHasAxis2?Json(probeAxis2):Json(nullptr)},{"value2",second},
                            {"label",probeLabel}};
                        apply_probe_axis(child,probeUnitIndex,probeAxis,first);
                        if(probeHasAxis2) apply_probe_axis(child,probeUnitIndex,probeAxis2,second.get<std::int64_t>());
                    } else if(mode=="tuning"&&targetDomain=="effective"&&axis!="herbs") {
                        child=raw;
                        apply_effective_tuning_target(child,unitIndex,parameterId,
                            spec.at("value").get<std::int64_t>(),effectiveContribution,tuningBounded);
                    } else child=mutate(raw,spec,generation+n);
                    const auto id=identity(child);
                    if(mode=="probe"&&probeCandidateByIdentity.count(id)) {
                        const auto candidateId=probeCandidateByIdentity.at(id);
                        const auto values=probeValuesByIdentity.at(id);
                        probeCell["status"]="reused"; probeCell["candidateId"]=candidateId;
                        probeCell["reason"]=id==parentId?"this probe cell is the paired parent anchor":"this raw scenario duplicates an earlier probe cell";
                        for(const char* key:{"inputValue","inputValue2","effectiveValue","effectiveValue2"})
                            probeCell[key]=values.value(key,Json(nullptr));
                        probeReused.push_back(Json{{"candidateId",candidateId},{"value",spec.at("value")},
                            {"value2",spec.value("value2",Json(nullptr))},{"axis",probeAxis},
                            {"axis2",probeHasAxis2?Json(probeAxis2):Json(nullptr)},
                            {"label",probeLabel},{"inputValue",values.value("inputValue",Json(nullptr))},
                            {"inputValue2",values.value("inputValue2",Json(nullptr))},
                            {"effectiveValue",values.value("effectiveValue",Json(nullptr))},
                            {"effectiveValue2",values.value("effectiveValue2",Json(nullptr))},
                            {"reason",probeCell["reason"]}});
                        probeGrid.push_back(std::move(probeCell));
                        continue;
                    }
                    if(!seen.insert(id).second) {
                        Json duplicate{{"mutation",spec},{"reason","proposal produces a duplicate or unchanged raw scenario"}};
                        if(mode=="tuning") duplicate["diagnostics"]={{"targetDomain",targetDomain},
                            {"targetValue",spec.at("value")},{"targetReached",false}};
                        rejected.push_back(std::move(duplicate));
                        if(mode=="probe") {
                            probeCell["status"]="rejected"; probeCell["reason"]="proposal produces a duplicate or unchanged raw scenario";
                            probeGrid.push_back(std::move(probeCell));
                        }
                        continue;
                    }
                    Json checked=admit(child,tables);
                    Json prepared=prepare(checked,tables,mode=="probe",&child);
                    if(has_error(prepared)) throw std::runtime_error(prepared.dump());
                    enforce_generated_stat_bound(spec,prepared);
                    Json preparedUnits=Json::array();
                    if(prepared.contains("ownUnits")&&prepared["ownUnits"].is_array()) for(const auto& unit:prepared["ownUnits"]) {
                        const Json battle_effective=unit.value("effectiveParameters",Json::object());
                        Json row{{"name",unit.value("name",std::string{})},
                            {"effectiveParameters",mode=="probe"?
                                unit.value("inputEffectiveParameters",battle_effective):battle_effective}};
                        if(mode=="probe") row["battleEffectiveParameters"]=battle_effective;
                        for(const char* field:{"grid","row","column","cell","formationValue"}) if(unit.contains(field)) row[field]=unit[field];
                        preparedUnits.push_back(std::move(row));
                    }
                    Json childResult{{"candidateId",id},{"parentCandidateId",mode=="probe"?probePivotId:parentId},{"generation",generation},
                        {"mutation",spec},{"changedKnobs",spec},{"rawScenario",child},{"preparedUnits",preparedUnits},
                        {"preparedSnapshotSha256",sha256_text(canonical(prepared))}};
                    if(mode=="skills") childResult["label"]=spec.value("label",std::string("Remove skill"));
                    if(mode=="tuning") {
                        const auto requestedValue=spec.at("value").get<std::int64_t>();
                        Json effective=nullptr,rawInput=nullptr,rawMaxInput=nullptr;
                        if(axis=="herbs") {
                            rawInput=child.value("holyHerbStock",Json(nullptr));
                            if(prepared.contains("snapshot")) effective=prepared["snapshot"].value("consumables",Json::object()).value("holy_herb_stock",Json(nullptr));
                        } else {
                            effective=prepared_battle_value(prepared,unitIndex,unitName,parameterId,tuningBounded);
                            const auto& parameter=child.at("ownUnits").at(unitIndex).at("parameters").at(parameterId);
                            rawInput=parameter.value("rawValue",Json(nullptr));
                            if(parameter.contains("rawMax")) rawMaxInput=parameter["rawMax"];
                        }
                        const Json reachedValue=targetDomain=="effective"?effective:rawInput;
                        const bool targetReached=reachedValue.is_number_integer()&&
                            reachedValue.get<std::int64_t>()==requestedValue;
                        if(!targetReached)
                            throw std::runtime_error("requested tuning target was not reached by canonical preparation");
                        childResult["axis"]=axis; childResult["unit"]=axis=="herbs"?Json(nullptr):Json(unitName);
                        childResult["targetDomain"]=targetDomain;
                        childResult["targetValue"]=requestedValue; childResult["inputValue"]=rawInput;
                        childResult["rawInputValue"]=rawInput; childResult["rawMaxInputValue"]=rawMaxInput;
                        childResult["targetReached"]=targetReached;
                        childResult["baselineInput"]=axis=="herbs"?Json(raw.value("holyHerbStock",0)):Json(baselineInput);
                        childResult["baselineEffective"]=axis=="herbs"?Json(nullptr):baselineEffective;
                        childResult["effectiveValue"]=effective;
                    }
                    if(mode=="probe") {
                        const auto value=spec.at("value").get<std::int64_t>();
                        const Json value2=probeHasAxis2?spec.at("value2"):Json(nullptr);
                        Json input1=nullptr,input2=nullptr;
                        if(probeAxis=="herbs") input1=child.value("holyHerbStock",Json(nullptr));
                        else input1=child.at("ownUnits").at(probeUnitIndex).at("parameters").at(require_probe_axis(probeAxis).parameterId).value("rawValue",Json(nullptr));
                        if(probeHasAxis2) {
                            if(probeAxis2=="herbs") input2=child.value("holyHerbStock",Json(nullptr));
                            else input2=child.at("ownUnits").at(probeUnitIndex).at("parameters").at(require_probe_axis(probeAxis2).parameterId).value("rawValue",Json(nullptr));
                        }
                        const auto effective1=probe_effective_value(prepared,probeUnitIndex,probeUnit,probeAxis);
                        const auto effective2=probeHasAxis2?probe_effective_value(prepared,probeUnitIndex,probeUnit,probeAxis2):Json(nullptr);
                        childResult["source"]="probe"; childResult["kind"]=probeHasAxis2?"grid":"ladder";
                        childResult["label"]=probeLabel; childResult["axis"]=probeAxis;
                        childResult["axisLabel"]=require_probe_axis(probeAxis).label;
                        childResult["axis2"]=probeHasAxis2?Json(probeAxis2):Json(nullptr);
                        childResult["axis2Label"]=probeHasAxis2?Json(require_probe_axis(probeAxis2).label):Json(nullptr);
                        childResult["unit"]=(probe_axis_is_stat(probeAxis)||
                            (probeHasAxis2&&probe_axis_is_stat(probeAxis2)))?Json(probeUnit):Json(nullptr);
                        childResult["pivotCandidateId"]=probePivotId;
                        childResult["pivotLabel"]=probePivotLabel;
                        childResult["value"]=value; childResult["value2"]=value2;
                        childResult["inputValue"]=input1; childResult["inputValue2"]=input2;
                        childResult["rawInputValue"]=input1; childResult["rawInputValue2"]=input2;
                        childResult["effectiveValue"]=effective1; childResult["effectiveValue2"]=effective2;
                        childResult["baselineInput"]=probePivotInput; childResult["baselineInput2"]=probeHasAxis2?Json(probePivotInput2):Json(nullptr);
                        childResult["baselineEffective"]=probePivotEffective; childResult["baselineEffective2"]=probePivotEffective2;
                        childResult["unestablished"]=probeUnestablished;
                        probeCell["status"]="created"; probeCell["candidateId"]=id;
                        probeCell["inputValue"]=input1; probeCell["inputValue2"]=input2;
                        probeCell["effectiveValue"]=effective1; probeCell["effectiveValue2"]=effective2;
                    }
                    if(request.value("includePreparedSnapshot",false)) childResult["preparedSnapshot"]=std::move(prepared);
                    children.push_back(std::move(childResult));
                    if(mode=="probe") {
                        probeCandidateByIdentity[id]=id;
                        const auto& saved=children.back();
                        probeValuesByIdentity[id]=Json{{"inputValue",saved.value("inputValue",Json(nullptr))},
                            {"inputValue2",saved.value("inputValue2",Json(nullptr))},
                            {"effectiveValue",saved.value("effectiveValue",Json(nullptr))},
                            {"effectiveValue2",saved.value("effectiveValue2",Json(nullptr))}};
                        probeGrid.push_back(std::move(probeCell));
                    }
                } catch(const std::exception& e) {
                    Json row{{"mutation",spec},{"reason",e.what()}};
                    if(mode=="tuning") {
                        Json rawInput=nullptr,rawMaxInput=nullptr;
                        try {
                            if(axis=="herbs") rawInput=child.value("holyHerbStock",Json(nullptr));
                            else if(child.contains("ownUnits")&&child["ownUnits"].is_array()&&unitIndex<child["ownUnits"].size()&&
                                    child["ownUnits"][unitIndex].contains("parameters")&&child["ownUnits"][unitIndex]["parameters"].contains(parameterId)) {
                                const auto& parameter=child["ownUnits"][unitIndex]["parameters"][parameterId];
                                rawInput=parameter.value("rawValue",Json(nullptr));
                                if(parameter.contains("rawMax")) rawMaxInput=parameter["rawMax"];
                            }
                        } catch(...) {}
                        row["diagnostics"]={{"targetDomain",targetDomain},{"targetValue",spec.value("value",Json(nullptr))},
                            {"rawInputValue",rawInput},{"rawMaxInputValue",rawMaxInput},{"targetReached",false}};
                    }
                    if(mode=="probe") {
                        probeCell["status"]="rejected"; probeCell["reason"]=e.what();
                        row["probeCell"]=probeCell;
                        probeGrid.push_back(std::move(probeCell));
                    }
                    rejected.push_back(std::move(row));
                }
            }
        }
        Json arms=Json::array();
        if(mode=="probe") arms.push_back(Json{{"candidateId",probePivotId},{"label",probePivotLabel},
            {"role","paired-parent-anchor"},{"rawScenario",raw},{"scenarioSha256",parentId},
            {"preparedSnapshotSha256",sha256_text(canonical(parentPreparation))}});
        else arms.push_back(Json{{"candidateId",parentId},{"label","Original build"},{"rawScenario",raw},{"preparedSnapshotSha256",sha256_text(canonical(parentPreparation))}});
        for(const auto& child:children) arms.push_back(Json{{"candidateId",child["candidateId"]},{"label",child.value("label",child.value("axis",std::string("Candidate")))},{"rawScenario",child["rawScenario"]}});
        Json response{{"ok",true},{"mode",mode},{"parentCandidateId",mode=="probe"?Json(probePivotId):Json(parentId)},
                    {"parentScenario",raw},{"children",children},{"arms",arms},{"rejected",rejected},
                    {"requested",count},{"generated",children.size()},{"availableMutations",specs.size()}};
        if(mode=="search") {
            response["statBounds"]=stat_bounds_metadata(proposalBounds);
            response["statBounds"]["note"]=proposalBounds.byParameter.empty()?
                "Original verified stat bounds unavailable; generated stat operators were omitted.":
                "Generated stat operators are checked against prepared effective values.";
        }
        if(mode=="skills"&&request.contains("count")) response["pairedSeedCount"]=request["count"];
        if(mode=="skills"&&request.contains("unit")) {
            response["unit"]=request["unit"];
            response["proposalScope"]="named-unit-only";
        } else if(mode=="skills") response["proposalScope"]="team-wide";
        if(request.contains("seedPairs")) response["pairedSeedPairs"]=request["seedPairs"];
        if(mode=="tuning") {
            response["axis"]=axis; response["unit"]=axis=="herbs"?Json(nullptr):Json(unitName);
            response["targetDomain"]=targetDomain;
            response["baselineInput"]=axis=="herbs"?Json(raw.value("holyHerbStock",0)):Json(baselineInput);
            response["baselineEffective"]=baselineEffective;
            response["statBounds"]=stat_bounds_metadata(proposalBounds);
            if(defaultTargetsGenerated) response["defaultTargetStrategy"]=defaultTargetsUsedVerifiedBounds?
                "verified-bounds-walls-rungs-interior":"unbounded-absolute-rungs";
            if(!requestedTargets.empty()) response["requestedTargets"]=requestedTargets;
        }
        if(mode=="probe") {
            response["parentScenarioSha256"]=parentId;
            auto values_json=[](const std::vector<std::int64_t>& values) {
                Json result=Json::array(); for(const auto value:values) result.push_back(value); return result;
            };
            response["axis"]=probeAxis; response["axisLabel"]=require_probe_axis(probeAxis).label;
            response["axis2"]=probeHasAxis2?Json(probeAxis2):Json(nullptr);
            response["axis2Label"]=probeHasAxis2?Json(require_probe_axis(probeAxis2).label):Json(nullptr);
            response["unit"]=(probe_axis_is_stat(probeAxis)||(probeHasAxis2&&probe_axis_is_stat(probeAxis2)))?Json(probeUnit):Json(nullptr);
            response["pivotCandidateId"]=probePivotId; response["pivotId"]=probePivotId;
            response["pivotLabel"]=probePivotLabel; response["unestablished"]=probeUnestablished;
            response["established"]=true; response["runs"]=probeRuns;
            response["unestablishedOptInRequired"]=(probeAxis=="int"||(probeHasAxis2&&probeAxis2=="int"));
            response["axisNote"]=nullptr;
            response["pivot"]=Json{{"candidateId",probePivotId},{"label",probePivotLabel},
                {"rawScenario",raw},{"scenarioSha256",parentId},
                {"inputValue",probePivotInput},{"inputValue2",probeHasAxis2?Json(probePivotInput2):Json(nullptr)},
                {"effectiveValue",probePivotEffective},{"effectiveValue2",probePivotEffective2}};
            response["ladder"]=values_json(probeLadder);
            response["ladder2"]=probeHasAxis2?values_json(probeLadder2):Json(nullptr);
            response["grid"]=probeGrid;
            Json created=Json::array();
            for(const auto& child:children) created.push_back(Json{{"value",child.value("value",Json(nullptr))},
                {"value2",child.value("value2",Json(nullptr))},{"candidateId",child.value("candidateId",Json(nullptr))},
                {"label",child.value("label",std::string{})}});
            response["created"]=std::move(created);
            response["reused"]=probeReused;
            response["refused"]=rejected;
            response["probePointCount"]=probeLadder.size();
            response["probePointCount2"]=probeHasAxis2?Json(probeLadder2.size()):Json(nullptr);
            response["points"]=probeLadder.size();
            response["points2"]=probeHasAxis2?Json(probeLadder2.size()):Json(nullptr);
            response["probeCellCount"]=specs.size();
            response["gridCells"]=specs.size();
            response["pairing"]="each probe child is paired against the parentCandidateId arm on shared seed pairs";
            response["supportedAxes"]=Json::array({"atk","def","hp","mp","spd","lck","dex","int","herbs","vig"});
        }
        return response;
    } catch(const std::exception& e) { return Json{{"ok",false},{"error",e.what()}}; }
}

int run(const Json& config) {
    const auto wallStart = std::chrono::steady_clock::now();
    const auto startedUnixMs = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
    std::unique_ptr<OutputLock> outputLock;
    try {
        const fs::path rawPath(required_string(config, "rawCandidates"));
        const fs::path tablesPath(required_string(config, "tables"));
        const std::string kernel = required_string(config, "kernelPath");
        const fs::path outDir(required_string(config, "outputDir"));
        const auto workers = positive(config, "executors", 256);
        const auto generations = positive(config, "generations", 100000);
        auto seedCount = positive(config, "seedCount", 1000000);
        const auto seedStart = config.value("seedStart", 0LL);
        Json seedPairs=config.value("seedPairs",Json::array());
        if(!seedPairs.is_array()) throw std::runtime_error("seedPairs must be an array of ordered [mathSeed,libSeed] pairs");
        if(!seedPairs.empty()) {
            if(seedPairs.size()>1000000) throw std::runtime_error("seedPairs exceeds 1000000 pairs");
            std::set<std::pair<std::int64_t,std::int64_t>> uniqueSeedPairs;
            for(const auto& pair:seedPairs) {
                if(!pair.is_array()||pair.size()!=2||!pair[0].is_number_integer()||pair[0].is_boolean()||
                   !pair[1].is_number_integer()||pair[1].is_boolean()||pair[0].get<std::int64_t>()<0||
                   pair[1].get<std::int64_t>()<0||pair[0].get<std::int64_t>()>2147483647LL||
                   pair[1].get<std::int64_t>()>2147483647LL)
                    throw std::runtime_error("each seedPairs entry must contain two nonnegative signed31 seeds");
                if(!uniqueSeedPairs.emplace(pair[0].get<std::int64_t>(),pair[1].get<std::int64_t>()).second)
                    throw std::runtime_error("seedPairs must be unique to avoid counting duplicate paired battles as independent evidence");
            }
            seedCount=seedPairs.size();
        }
        if (seedPairs.empty() && (seedStart < 0 || seedStart > 2147483647LL || seedCount - 1 > static_cast<std::uint64_t>(2147483647LL - seedStart)))
            throw std::runtime_error("seed range must remain nonnegative signed 31-bit");
        Json provenance = config.value("provenance", Json::object());
        validate_provenance(provenance);
        // Fixed-formation policy (latest user scope): activated before any admission or identity
        // computation so the whole search runs under the pinned policy.
        const bool fixedFormation=config.value("fixedFormation",false);
        if(fixedFormation) set_fixed_formation_enabled(true);
        Json mutations = config.value("mutations", Json::array());
        if (!mutations.is_array()) throw std::runtime_error("mutations must be an array");
        const std::string searchProfile=config.value("searchProfile",std::string("legacy"));
        if(searchProfile!="legacy"&&searchProfile!="adaptive-native") throw std::runtime_error("searchProfile must be legacy or adaptive-native");
        const std::string searchScheduling=config.value("searchScheduling",std::string("encounter-wave"));
        if(searchScheduling!="encounter-wave"&&searchScheduling!="legacy-sequential")
            throw std::runtime_error("searchScheduling must be encounter-wave or legacy-sequential");
        const std::string adaptiveScope=config.value("adaptiveScope",std::string("per-encounter"));
        if(adaptiveScope!="per-encounter"&&adaptiveScope!="global")
            throw std::runtime_error("adaptiveScope must be per-encounter or global");
        if(searchProfile=="adaptive-native"&&adaptiveScope=="global"&&searchScheduling!="legacy-sequential")
            throw std::runtime_error("adaptiveScope=global requires searchScheduling=legacy-sequential to preserve prior cross-encounter arm feedback");
        std::set<std::string> configuredFocus;
        bool configuredFocusAll=true;
        if(config.contains("focusEncounterIds")) {
            if(!config["focusEncounterIds"].is_array()) throw std::runtime_error("focusEncounterIds must be an array of encounter IDs");
            configuredFocusAll=config["focusEncounterIds"].empty();
            for(const auto& id:config["focusEncounterIds"]) {
                if((!id.is_number_integer()&&!id.is_number_unsigned())||id.is_boolean()||id.get<std::int64_t>()<0||id.get<std::int64_t>()>19)
                    throw std::runtime_error("focusEncounterIds entries must be integers from 0 through 19");
                if(!configuredFocus.insert(std::to_string(id.get<std::int64_t>())).second)
                    throw std::runtime_error("focusEncounterIds must not contain duplicates");
            }
        }
        Json configuredFocusJson=Json::array();
        if(configuredFocusAll) for(int id=0;id<20;++id) configuredFocusJson.push_back(id);
        else for(const auto& id:configuredFocus) configuredFocusJson.push_back(std::stoi(id));
        std::string controlPath;
        if(config.contains("controlPath")) {
            if(!config["controlPath"].is_string()||config["controlPath"].get<std::string>().empty())
                throw std::runtime_error("controlPath must be a nonempty path string when supplied");
            controlPath=config["controlPath"].get<std::string>();
        }
        std::string searchStatePath;
        const std::string searchStateOutputPath=(outDir/"search-state.json").string();
        bool searchStateImportRequested=false;
        if(config.contains("searchStatePath")) {
            if(!config["searchStatePath"].is_string()||config["searchStatePath"].get<std::string>().empty())
                throw std::runtime_error("searchStatePath must be a nonempty path string when supplied");
            searchStatePath=config["searchStatePath"].get<std::string>();
            searchStateImportRequested=true;
            if(!fs::exists(searchStatePath)) throw std::runtime_error("searchStatePath was supplied but its checkpoint file does not exist; omit it only for an intentional fresh search");
        }
        const std::string executionMode=config.value("executionMode",std::string("production"));
        const std::string purpose=config.value("purpose",std::string("strategy"));
        if(executionMode!="diagnostic"&&executionMode!="production") throw std::runtime_error("executionMode must be diagnostic or production");
        if(purpose!="diagnostic"&&purpose!="strategy") throw std::runtime_error("purpose must be diagnostic or strategy");
        const bool strategyPurpose=executionMode=="production"&&purpose=="strategy";
        if(config.contains("persistPreparedSnapshot")&&!config["persistPreparedSnapshot"].is_boolean()) throw std::runtime_error("persistPreparedSnapshot must be boolean");
        const bool persistPreparedSnapshot=config.value("persistPreparedSnapshot",!strategyPurpose||searchProfile=="legacy");
        for (const auto& m : mutations) {
            const auto op=m.is_object()?m.value("op",std::string("integer-step")):std::string();
            if(!m.is_object()) throw std::runtime_error("mutation entry must be an object");
            if(op=="integer-step") { if(!m.contains("pointer")||!m["pointer"].is_string()||!m.contains("min")||!m.contains("max")) throw std::runtime_error("integer-step mutation requires pointer, min and max");
                const auto mutationPointer=m["pointer"].get<std::string>(); if(mutationPointer=="/encounterId"||mutationPointer=="/mathSeed"||mutationPointer=="/libSeed"||mutationPointer=="/tickLimit"||mutationPointer=="/finishPolicy") throw std::runtime_error("mutation may not change encounter identity, seeds or policy"); (void)Json::json_pointer(mutationPointer);
            } else if(op=="set-value") { if(!m.contains("pointer")||!m["pointer"].is_string()||!m.contains("value")) throw std::runtime_error("set-value mutation requires pointer and value"); const auto mutationPointer=m["pointer"].get<std::string>(); if(mutationPointer=="/encounterId"||mutationPointer=="/mathSeed"||mutationPointer=="/libSeed"||mutationPointer=="/tickLimit"||mutationPointer=="/finishPolicy") throw std::runtime_error("mutation may not change encounter identity, seeds or policy"); (void)Json::json_pointer(mutationPointer);
            } else if(op=="swap") { if(!m.contains("pointer")||!m["pointer"].is_string()||!m.contains("i")||!m.contains("j")) throw std::runtime_error("swap mutation requires pointer and i,j"); const auto mutationPointer=m["pointer"].get<std::string>(); if(mutationPointer=="/encounterId") throw std::runtime_error("mutation may not change encounterId"); (void)Json::json_pointer(mutationPointer);
            } else if(op=="skill-order") { if(!m.contains("unit")||!m.contains("i")||!m.contains("j")) throw std::runtime_error("skill-order mutation requires unit,i,j");
            } else if(op=="remove-skill") { if(!m.contains("unit")||!m.contains("slot")) throw std::runtime_error("remove-skill mutation requires unit and slot");
            } else if(op=="remove-skill-group") { if(!m.contains("slots")||!m["slots"].is_array()) throw std::runtime_error("remove-skill-group mutation requires slots");
            } else if(op=="set-axis") { if(!m.contains("axis")||!m["axis"].is_string()||!m.contains("value")) throw std::runtime_error("set-axis mutation requires axis and value");
            } else if(op=="stat-step") { if(!m.contains("unitIndex")||!m.contains("parameter")||!m.contains("step")||!m.contains("effectiveBounds")) throw std::runtime_error("stat-step mutation requires unitIndex, parameter, step and original effective bounds");
            } else throw std::runtime_error("unsupported mutation op: "+op);
        }
        Json seedPointers = config.value("seedPointers", Json::array({"/mathSeed", "/libSeed"}));
        if (!seedPointers.is_array() || seedPointers.empty()) throw std::runtime_error("seedPointers must be a nonempty array");
        for (const auto& p : seedPointers) {
            if (!p.is_string()) throw std::runtime_error("seedPointers entries must be JSON-pointer strings");
            (void)Json::json_pointer(p.get<std::string>());
        }
        // Seed assignment occurs after mutation. Reject overlapping paths rather than
        // silently erase a requested mutation and deduplicate every child trial.
        const auto overlaps = [](const std::string& a,const std::string& b) {
            return a==b || (a.size()<b.size() && b.compare(0,a.size(),a)==0 && b[a.size()]=='/') ||
                (b.size()<a.size() && a.compare(0,b.size(),b)==0 && a[b.size()]=='/');
        };
        if(!seedPairs.empty()) {
            if(seedPointers.size()!=2) throw std::runtime_error("ordered seedPairs require exactly two seedPointers");
            for(std::size_t i=0;i<seedPointers.size();++i) for(std::size_t j=i+1;j<seedPointers.size();++j)
                if(overlaps(seedPointers[i].get<std::string>(),seedPointers[j].get<std::string>()))
                    throw std::runtime_error("ordered seedPairs seedPointers must be distinct and non-overlapping; aliased pointers can collapse different pairs to the same trial identity");
        }
        for(const auto& m:mutations)for(const auto& p:seedPointers)
            if((m.value("op",std::string("integer-step"))=="integer-step"||m.value("op",std::string("integer-step"))=="set-value")&&overlaps(m.at("pointer").get<std::string>(),p.get<std::string>()))
                throw std::runtime_error("mutation overlaps a configured seed pointer");
        const auto scorePointerText = config.value("scorePointer", std::string("/earned"));
        const Json::json_pointer scorePointer(scorePointerText);
        const bool comparisonOptIn = config.contains("comparison");
        Json comparison = Json::object();
        std::uint64_t durableSyncBatchSize = std::numeric_limits<std::uint64_t>::max();
        bool splitDurableSyncBatches = false;
        bool stageTimingTelemetry = false;
        if (comparisonOptIn) {
            if (!config.at("comparison").is_object())
                throw std::runtime_error("comparison must be an object");
            comparison = config.at("comparison");
            for (auto it = comparison.begin(); it != comparison.end(); ++it)
                if (it.key() != "durableSyncBatchSize" && it.key() != "stageTimingTelemetry")
                    throw std::runtime_error("unsupported comparison setting: " + it.key());
            if (comparison.contains("durableSyncBatchSize")) {
                durableSyncBatchSize = positive(comparison, "durableSyncBatchSize", 1000000);
                splitDurableSyncBatches = true;
            }
            if (comparison.contains("stageTimingTelemetry")) {
                if (!comparison.at("stageTimingTelemetry").is_boolean())
                    throw std::runtime_error("comparison.stageTimingTelemetry must be boolean");
                stageTimingTelemetry = comparison.at("stageTimingTelemetry").get<bool>();
            }
            // Normalize defaults so resume compatibility binds to the effective policy.
            comparison = Json{{"durableSyncBatchSize", splitDurableSyncBatches ? Json(durableSyncBatchSize) : Json(nullptr)},
                              {"stageTimingTelemetry", stageTimingTelemetry}};
        }
        const auto mark_diagnostic = [&](Json& target,const Json& scenario) {
            const auto policy=scenario.value("finishPolicy",std::string("at-horizon"));
            if(!target.contains("finishDiagnostic")) target["finishDiagnostic"]={{"scope","automatic-finish-unproven"},{"policy",policy},{"reason","automatic finish producer and full settlement remain unproven"}};
            if(!strategyPurpose) {
                target["diagnostic"]=true;target["verified"]=false;
                target["verificationOnly"]=true;target["importDisabled"]=true;
            }
        };
        const auto eligible_score = [&](const std::string& basis,bool hasMetric) {
            if(!hasMetric||!strategyPurpose) return false;
            return basis=="certified-award"||basis=="loss-gate"||basis=="queued-at-victory";
        };
        const auto read_metric = [&](const Json& result,double& value,bool& available,std::string& basis) {
            if(!read_score(result,scorePointer,value,available)) return false;
            basis=result.value("earnedBasis",std::string("unresolved"));
            if(!available&&scorePointerText=="/earned"&&result.is_object()&&result.contains("report")&&result["report"].is_object()&&result["report"].value("verdict",0)==1&&result.contains("rewardOutcome")&&result["rewardOutcome"].is_object()) {
                const auto& pending=result["rewardOutcome"].value("pendingChests",Json(nullptr));
                if(pending.is_number()&&std::isfinite(pending.get<double>())) { value=pending.get<double>(); available=true; basis="queued-at-victory"; }
            }
            return true;
        };
        const auto inputLoadStart = std::chrono::steady_clock::now();
        std::ifstream rawFile(rawPath, std::ios::binary), tablesFile(tablesPath, std::ios::binary);
        if (!rawFile || !tablesFile) throw std::runtime_error("cannot open input JSON file");
        Json raws = Json::parse(rawFile), tables = Json::parse(tablesFile);
        const auto inputJsonLoadWallNs = static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now() - inputLoadStart).count());
        if (!raws.is_array() || raws.empty()) throw std::runtime_error("rawCandidates must be a nonempty JSON array");
        if (raws.size() > 100000) throw std::runtime_error("raw candidate input limit is 100000");
        // Refuse to start at all under the fixed policy when any supplied candidate is not the fixed
        // formation for its encounter; a historical equipped cast must never enter the search.
        if(fixedFormation) for(const auto& raw:raws) validate_fixed_formation_raw(raw);
        const SearchBounds generatedBounds=load_search_bounds(config);
        if(fixedFormation) {
            mutations=fixed_formation_mutation_specs();
        } else if(searchProfile=="adaptive-native") {
            const auto generated=native_mutations(raws,&generatedBounds);
            for(const auto& spec:generated) {
                const auto sig=spec.dump();
                const auto found=std::find_if(mutations.begin(),mutations.end(),[&](const Json& m){return m.dump()==sig;});
                if(found==mutations.end()) mutations.push_back(spec);
            }
            if(mutations.empty()) throw std::runtime_error("adaptive-native found no legal mutation dimensions in input scenarios");
        }
        Json runPolicy{{"mutations",mutations},{"searchProfile",searchProfile},{"seedPairs",seedPairs},{"seedPointers",seedPointers},{"scorePointer",scorePointerText},
            {"seedStart",seedStart},{"seedCount",seedCount},{"generations",generations},
            {"generationScope","per-encounter"},{"searchScheduling",searchScheduling},{"adaptiveScope",adaptiveScope},{"controlPath",controlPath.empty()?Json(nullptr):Json(controlPath)},
            {"selection",searchProfile=="adaptive-native"?"mean-elitism-with-uniform-exploration":"mean-earned-elitism"},{"unscoredParentPolicy","minimum-strategy-sha"},{"illegalMutationPolicy","skip-and-diagnose"},
            {"executionMode",executionMode},{"purpose",purpose},{"persistPreparedSnapshot",persistPreparedSnapshot},
            {"evidenceClassificationPolicy",strategyPurpose?"resolved-certified-loss-or-queued-at-victory-earned":"explicit-diagnostic-unscored"}};
        runPolicy["statBoundsSource"]=generatedBounds.source;
        runPolicy["statBoundsSha256"]=generatedBounds.sourceHash;
        runPolicy["statBoundsAvailable"]=!generatedBounds.byParameter.empty();
        runPolicy["statBounds"]=stat_bounds_metadata(generatedBounds);
        if(fixedFormation) {
            runPolicy["fixedFormation"]=true;
            runPolicy["fixedFormationPolicyHash"]=fixed_formation_policy_hash();
            Json fixedStatBounds=Json::object();
            for(const auto& entry:fixed_formation_searchable_parameters())
                fixedStatBounds[entry.first]={{"minimum",entry.second.first},{"maximum",entry.second.second}};
            runPolicy["fixedFormationStatBounds"]=fixedStatBounds;
            runPolicy["selection"]="fixed-formation-mean-earned-elitism";
        }
        if (comparisonOptIn) runPolicy["comparison"] = comparison;
        Json learningPolicy=runPolicy;
        for(const char* key:{"seedPairs","seedStart","seedCount","generations","controlPath"}) learningPolicy.erase(key);
        fs::create_directories(outDir);
        outputLock = std::make_unique<OutputLock>(outDir / ".pipeline.lock");
        write_status(outDir, Json{{"schema","kaopt-status-1"},{"state","running"},
            {"savedRecords",0},{"invalidCandidates",0},{"executionErrors",0},{"freshSavedRecords",0},{"restoredRecords",0},
            {"persistPreparedSnapshot",persistPreparedSnapshot},
            {"stage","input-and-journal-loading"},{"startedUnixMs",startedUnixMs},
            {"focusEncounterIds",configuredFocusJson},{"searchScheduling",searchScheduling},{"adaptiveScope",adaptiveScope},
            {"controlPath",controlPath.empty()?Json(nullptr):Json(controlPath)},
            {"searchStatePath",searchStatePath.empty()?Json(nullptr):Json(searchStatePath)},
            {"searchStateOutputPath",searchStateOutputPath},
            {"elapsedWallNs",static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
                std::chrono::steady_clock::now()-wallStart).count())}});
        const fs::path journalPath = outDir / "results.jsonl";
        const fs::path errorsPath = outDir / "errors.jsonl";
        const fs::path checkpointPath = outDir / "checkpoint.json";

        // Journal recovery accepts complete newline records and truncates a torn tail.
        std::unordered_set<std::string> completed;
        std::unordered_set<std::string> learningCompleted;
        std::map<std::string, ScoreStats> scores;
        std::map<std::string, ScoreStats> freshScores;
        std::vector<Candidate> resumedCandidates;
        std::unordered_set<std::string> resumedIds;
        std::map<std::string, std::uint64_t> lastGenerationByEncounter;
        std::map<std::string,std::uint64_t> localCompletedByEncounter;
        std::set<std::string> importedCandidateIds;
        std::set<std::string> importedPendingCandidateIds;
        std::map<std::string,ArmStats> importedMutationArms;
        Json importedSourceJournals=Json::array();
        Json importedPendingSeedPolicy=nullptr;
        if (fs::exists(journalPath)) {
            std::ifstream in(journalPath, std::ios::binary);
            std::string line; std::uintmax_t goodBytes = 0;
            while (std::getline(in, line)) {
                if (in.eof()) break; // A valid JSON fragment without its newline is still a torn append.
                Json r;
                try {
                    r = Json::parse(line);
                } catch (...) { throw std::runtime_error("malformed newline-terminated journal row; refusing destructive recovery"); }
                if (!r.is_object() || !r.contains("dedupKey") || !r["dedupKey"].is_string())
                    throw std::runtime_error("invalid journal row schema; refusing destructive recovery");
                if (!r.contains("provenance") || r["provenance"] != provenance)
                    throw std::runtime_error("journal provenance differs from requested engine/mechanics/policy hashes");
                if (!r.contains("searchPolicy") || r["searchPolicy"] != runPolicy)
                    throw std::runtime_error("journal search policy differs from requested mutation/seed/score configuration");
                const std::string key = r["dedupKey"].get<std::string>();
                if (!completed.insert(key).second) throw std::runtime_error("duplicate result identity in journal");
                if (!r.contains("candidateId") || !r.contains("strategyId") || !r.contains("candidateScenario") ||
                    identity(r["rawScenario"]) != r["candidateId"].get<std::string>() ||
                    identity(r["candidateScenario"]) != r["strategyId"].get<std::string>())
                    throw std::runtime_error("journal candidate identity mismatch");
                learningCompleted.insert(r["candidateId"].get<std::string>());
                const std::string encounter = encounter_key(r["candidateScenario"]);
                ++localCompletedByEncounter[encounter];
                const auto rowGeneration = r.value("generation", std::uint64_t(0));
                if (!r.contains("encounterId") || r["encounterId"] != r["candidateScenario"].at("encounterId"))
                    throw std::runtime_error("journal encounter identifier differs from saved raw intent");
                if (!r.contains("preparedSha256")||!r["preparedSha256"].is_string()||r["preparedSha256"].get<std::string>().size()!=64)
                    throw std::runtime_error("journal lacks prepared snapshot SHA-256");
                if(r.contains("preparedSnapshot")) {
                    if(r["preparedSha256"]!=sha256_text(canonical(r["preparedSnapshot"]))) throw std::runtime_error("journal prepared snapshot hash mismatch");
                } else if(persistPreparedSnapshot) throw std::runtime_error("journal lacks the exact prepared kernel snapshot required by this policy");
                if (!r.contains("seed") || !r["seed"].is_number_integer() || !r.contains("seedValues") ||
                    !r["seedValues"].is_array() || r["seedValues"].size() != seedPointers.size())
                    throw std::runtime_error("journal seed provenance is incomplete");
                const auto rowSeed = r["seed"].get<std::int64_t>();
                Json expectedSeeds;
                if(!seedPairs.empty()) {
                    if(!r.contains("seedPair")||!r["seedPair"].is_array()||r["seedPair"].size()!=2) throw std::runtime_error("journal lacks ordered seed pair");
                    expectedSeeds=r["seedPair"];
                    if(std::find(seedPairs.begin(),seedPairs.end(),expectedSeeds)==seedPairs.end()||rowSeed!=expectedSeeds[0].get<std::int64_t>()) throw std::runtime_error("journal seed pair differs from configured ordered seed bank");
                } else {
                    if (rowSeed < seedStart || rowSeed >= seedStart + static_cast<std::int64_t>(seedCount)) throw std::runtime_error("journal seed falls outside the configured seed range");
                    expectedSeeds=Json::array(); for(std::size_t pi=0;pi<seedPointers.size();++pi) expectedSeeds.push_back(rowSeed);
                }
                for (std::size_t pi = 0; pi < seedPointers.size(); ++pi) {
                    const auto ptr = Json::json_pointer(seedPointers[pi].get<std::string>());
                    if (!r["rawScenario"].contains(ptr) || r["rawScenario"].at(ptr) != r["seedValues"][pi] ||
                        r["seedValues"][pi] != expectedSeeds[pi])
                        throw std::runtime_error("journal seed value differs from serialized trial scenario");
                }
                if(!seedPairs.empty()&&r["seedPair"]!=r["seedValues"]) throw std::runtime_error("journal seed pair differs from serialized seed values");
                lastGenerationByEncounter[encounter] = std::max(lastGenerationByEncounter[encounter], rowGeneration);
                if (r["dedupKey"] != r["candidateId"].get<std::string>() + ":" + canonical(provenance))
                    throw std::runtime_error("journal dedup identity mismatch");
                if (!r.contains("result")) throw std::runtime_error("journal row has no native result");
                double earned = 0; bool scored = false; std::string earnedBasis;
                if (!read_metric(r["result"], earned, scored, earnedBasis))
                    throw std::runtime_error("journal contains an invalid native result");
                if(!r.contains("earnedBasis")||r["earnedBasis"].get<std::string>()!=earnedBasis) throw std::runtime_error("journal Earned basis differs from native report");
                const bool evidenceEligible=eligible_score(earnedBasis,scored);
                if (!r.contains("scoreEligible") || r["scoreEligible"].get<bool>() != scored ||
                    !r.contains("evidenceEligible") || r["evidenceEligible"].get<bool>() != evidenceEligible ||
                    (scored && (!r.contains("earned") || r["earned"].get<double>() != earned)) ||
                    (!scored && (!r.contains("earned") || !r["earned"].is_null())) ||
                    (scored&&(!r.contains("observedEarned")||r["observedEarned"].get<double>()!=earned)))
                    throw std::runtime_error("journal Earned value does not match native result");
                if (scored) {
                    auto& stats=scores[r["strategyId"].get<std::string>()];
                    auto& trials = stats.outcomesByTrial;
                    if (!trials.emplace(r["candidateId"].get<std::string>(), earned).second)
                        throw std::runtime_error("duplicate scored trial identity in journal");
                    stats.basisByTrial[r["candidateId"].get<std::string>()]=earnedBasis;
                    freshScores[r["strategyId"].get<std::string>()].outcomesByTrial.emplace(r["candidateId"].get<std::string>(),earned);
                    freshScores[r["strategyId"].get<std::string>()].basisByTrial[r["candidateId"].get<std::string>()]=earnedBasis;
                }
                if (resumedIds.insert(r["strategyId"].get<std::string>()).second) {
                    resumedCandidates.push_back(Candidate{r["candidateScenario"],
                        r["strategyId"].get<std::string>(),
                        r.value("parentCandidateId", std::string()),
                        r.value("generation", std::uint64_t(0)),
                        r.value("mutation", std::string("resumed")),r.value("sourceBinding",Json::object())});
                }
                goodBytes += line.size() + 1;
            }
            const auto size = fs::file_size(journalPath);
            if (goodBytes != size) fs::resize_file(journalPath, goodBytes);
        }
        bool importedSearchState=false;
        if(searchStateImportRequested) {
            if(!completed.empty()) throw std::runtime_error("searchStatePath imports into a fresh output journal; use ordinary resume for a populated output directory");
            std::ifstream stateFile(searchStatePath,std::ios::binary);
            if(!stateFile) throw std::runtime_error("searchStatePath cannot be opened");
            Json state=Json::parse(stateFile);
            if(!state.is_object()||state.value("schema",std::string{})!="kaopt-search-state-1")
                throw std::runtime_error("searchStatePath schema is not kaopt-search-state-1");
            Json stateProvenance=state.value("provenance",Json::object()), compatibleProvenance=provenance;
            for(const char* key:{"rawCandidatesSha256","candidateMetadataSha256","policySha256"}) { stateProvenance.erase(key); compatibleProvenance.erase(key); }
            if(stateProvenance!=compatibleProvenance) throw std::runtime_error("searchStatePath kernel, mechanics, tables, ABI, arena, or policy provenance is incompatible");
            if(state.value("learningPolicy",Json(nullptr))!=learningPolicy)
                throw std::runtime_error("searchStatePath search operators, reward policy, scheduler, adaptive scope, or evidence policy is incompatible");
            if(!state.contains("sourceJournals")||!state["sourceJournals"].is_array())
                throw std::runtime_error("searchStatePath is missing source journal integrity references");
            importedSourceJournals=state["sourceJournals"];
            for(const auto& source:importedSourceJournals) {
                if(!source.is_object()||!source.contains("path")||!source["path"].is_string()||!source.contains("sha256")||!source["sha256"].is_string())
                    throw std::runtime_error("searchStatePath contains an invalid source journal reference");
                if(!source.contains("provenance")||!source["provenance"].is_object())
                    throw std::runtime_error("searchStatePath source journal lacks provenance");
                Json sourceProvenance=source["provenance"];
                for(const char* key:{"rawCandidatesSha256","candidateMetadataSha256","policySha256"}) sourceProvenance.erase(key);
                if(sourceProvenance!=compatibleProvenance)
                    throw std::runtime_error("searchStatePath source journal provenance is incompatible");
                std::ifstream sourceFile(source["path"].get<std::string>(),std::ios::binary);
                if(!sourceFile) throw std::runtime_error("searchStatePath source result journal is missing");
                std::string sourceBytes((std::istreambuf_iterator<char>(sourceFile)),std::istreambuf_iterator<char>());
                if(sha256_text(sourceBytes)!=source["sha256"].get<std::string>())
                    throw std::runtime_error("searchStatePath source result journal SHA-256 mismatch");
                const auto rows=static_cast<std::uint64_t>(std::count(sourceBytes.begin(),sourceBytes.end(),'\n'));
                if(source.contains("recordCount")&&source["recordCount"].is_number_unsigned()&&rows!=source["recordCount"].get<std::uint64_t>())
                    throw std::runtime_error("searchStatePath source result journal record count mismatch");
            }
            if(!state.contains("candidatePool")||!state["candidatePool"].is_array()||
               !state.contains("scoreOutcomes")||!state["scoreOutcomes"].is_array()||
               !state.contains("completedTrialSignatures")||!state["completedTrialSignatures"].is_array())
                throw std::runtime_error("searchStatePath candidate, score, or dedup state is incomplete");
            for(const auto& row:state["candidatePool"]) {
                if(!row.is_object()||!row.contains("raw")||!row.contains("id")||!row["id"].is_string()||
                   !row.contains("generation")||!row["generation"].is_number_unsigned())
                    throw std::runtime_error("searchStatePath candidate row is malformed");
                Candidate candidate{row["raw"],row["id"].get<std::string>(),row.value("parent",std::string{}),
                    row["generation"].get<std::uint64_t>(),row.value("operation",std::string("resumed")),
                    row.value("sourceBinding",Json::object())};
                if(identity(candidate.raw)!=candidate.id) throw std::runtime_error("searchStatePath candidate identity mismatch");
                (void)encounter_key(candidate.raw);
                importedCandidateIds.insert(candidate.id);
                if(resumedIds.insert(candidate.id).second) resumedCandidates.push_back(std::move(candidate));
            }
            for(const auto& row:state["scoreOutcomes"]) {
                if(!row.is_object()||!row.contains("strategyId")||!row["strategyId"].is_string()||
                   !row.contains("trialId")||!row["trialId"].is_string()||!row.contains("earned")||
                   !row["earned"].is_number()||!std::isfinite(row["earned"].get<double>())||
                   !row.contains("basis")||!row["basis"].is_string())
                    throw std::runtime_error("searchStatePath score outcome is malformed");
                auto& score=scores[row["strategyId"].get<std::string>()];
                const auto trial=row["trialId"].get<std::string>();
                if(trial.size()!=64||!score.outcomesByTrial.emplace(trial,row["earned"].get<double>()).second)
                    throw std::runtime_error("searchStatePath has a duplicate or invalid scored trial identity");
                score.basisByTrial[trial]=row["basis"].get<std::string>();
            }
            for(const auto& signature:state["completedTrialSignatures"]) {
                if(!signature.is_string()||signature.get<std::string>().size()!=64)
                    throw std::runtime_error("searchStatePath contains an invalid completed trial signature");
                learningCompleted.insert(signature.get<std::string>());
            }
            if(state.contains("lastGenerationByEncounter")&&state["lastGenerationByEncounter"].is_object())
                for(auto it=state["lastGenerationByEncounter"].begin();it!=state["lastGenerationByEncounter"].end();++it)
                    if(it.value().is_number_unsigned()) lastGenerationByEncounter[it.key()]=it.value().get<std::uint64_t>();
            if(state.contains("mutationArms")&&state["mutationArms"].is_object())
                for(auto it=state["mutationArms"].begin();it!=state["mutationArms"].end();++it) {
                    if(!it.value().is_object()||!it.value().contains("tries")||!it.value().contains("improvements")||
                       !it.value()["tries"].is_number_unsigned()||!it.value()["improvements"].is_number_unsigned())
                        throw std::runtime_error("searchStatePath mutation-arm counters are malformed");
                    ArmStats arm;
                    if(it.value().value("judgmentModel",std::string{})=="matched-configured-seedpairs-v1") {
                        for(const char* key:{"lastMatchedSamples","lastMissingChildPairs","lastMissingParentPairs","lastChildId","lastParentId","lastMeanPairedDelta","lastJudgmentComplete"})
                            if(!it.value().contains(key)) throw std::runtime_error("searchStatePath paired mutation-arm metadata is incomplete");
                        if(!it.value()["lastMatchedSamples"].is_number_unsigned()||!it.value()["lastMissingChildPairs"].is_number_unsigned()||
                           !it.value()["lastMissingParentPairs"].is_number_unsigned()||!it.value()["lastChildId"].is_string()||
                           !it.value()["lastParentId"].is_string()||!it.value()["lastJudgmentComplete"].is_boolean()||
                           (!it.value()["lastMeanPairedDelta"].is_null()&&!it.value()["lastMeanPairedDelta"].is_number()))
                            throw std::runtime_error("searchStatePath paired mutation-arm metadata is malformed");
                        arm.tries=it.value()["tries"].get<std::uint64_t>();
                        arm.improvements=it.value()["improvements"].get<std::uint64_t>();
                        arm.lastMatchedSamples=it.value()["lastMatchedSamples"].get<std::uint64_t>();
                        arm.lastMissingChildPairs=it.value()["lastMissingChildPairs"].get<std::uint64_t>();
                        arm.lastMissingParentPairs=it.value()["lastMissingParentPairs"].get<std::uint64_t>();
                        arm.lastChildId=it.value()["lastChildId"].get<std::string>();
                        arm.lastParentId=it.value()["lastParentId"].get<std::string>();
                        arm.lastJudgmentComplete=it.value()["lastJudgmentComplete"].get<bool>();
                        if(!it.value()["lastMeanPairedDelta"].is_null())
                            arm.lastMeanPairedDelta=it.value()["lastMeanPairedDelta"].get<double>();
                    }
                    // Older state files contain only unmatched aggregate means; keep the file readable
                    // but do not let that unpaired history steer the new matched-seed policy.
                    importedMutationArms[it.key()]=std::move(arm);
                }
            importedPendingSeedPolicy=state.value("pendingSeedPolicy",Json(nullptr));
            if(!importedPendingSeedPolicy.is_null()) {
                const Json currentSeedPolicy{{"seedPairs",seedPairs},{"seedStart",seedStart},{"seedCount",seedCount},{"seedPointers",seedPointers}};
                if(importedPendingSeedPolicy!=currentSeedPolicy)
                    throw std::runtime_error("searchStatePath has pending seeds; resume with its exact seedPairs/seedStart/seedCount/seedPointers before reserving a new bank");
                if(!state.contains("pendingCandidateIds")||!state["pendingCandidateIds"].is_array())
                    throw std::runtime_error("searchStatePath pending seed policy lacks pending candidate IDs");
                for(const auto& id:state["pendingCandidateIds"]) {
                    if(!id.is_string()||!importedCandidateIds.count(id.get<std::string>()))
                        throw std::runtime_error("searchStatePath pending candidate is absent from candidatePool");
                    importedPendingCandidateIds.insert(id.get<std::string>());
                }
            }
            importedSearchState=true;
        }
        std::vector<Candidate> candidates;
        candidates.reserve(raws.size());
        std::unordered_set<std::string> seen;
        std::set<std::string> currentInputCandidateIds;
        Json sourceMetadata=Json::array();
        if(config.contains("candidateMetadata")){std::ifstream file(config.at("candidateMetadata").get<std::string>());if(!file)throw std::runtime_error("candidate metadata cannot be opened");sourceMetadata=Json::parse(file);if(!sourceMetadata.is_array()||sourceMetadata.size()!=raws.size())throw std::runtime_error("candidate metadata count differs from raw input count");}
        std::size_t inputIndex=0;
        for (const auto& raw : raws) {
            Json binding{{"rawInputIndex",inputIndex},{"rawInputSha256",identity(raw)}};
            if(!sourceMetadata.empty()){auto metadata=sourceMetadata[inputIndex];if(metadata.contains("encounter_id")&&metadata.at("encounter_id")!=raw.at("encounterId"))throw std::runtime_error("source metadata encounter differs from raw input");binding["suppliedSourceMetadata"]=metadata;binding["mappingStatus"]="supplied-metadata";}
            ++inputIndex;
            Candidate c{raw, identity(raw), "", 0, "seed",binding};
            (void)encounter_key(raw);
            currentInputCandidateIds.insert(c.id);
            if (seen.insert(c.id).second) candidates.push_back(std::move(c));
        }
        for (auto& c : resumedCandidates) {
            if (seen.insert(c.id).second) candidates.push_back(std::move(c));
        }
        if (candidates.size() > 100000) throw std::runtime_error("generated candidate limit is 100000");
        const auto startupMs = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - wallStart).count();
        const auto startupWallNs = static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now() - wallStart).count());

        std::atomic<std::uint32_t> focusMask{0};
        if(configuredFocusAll) focusMask.store((1u<<20)-1,std::memory_order_relaxed);
        else { std::uint32_t mask=0; for(const auto& id:configuredFocus) mask|=(1u<<static_cast<unsigned>(std::stoul(id))); focusMask.store(mask,std::memory_order_relaxed); }
        std::atomic<bool> pauseRequested{false}, stopRequested{false}, monitorStop{false};
        std::mutex controlWarningMutex;
        std::string controlWarning;
        const auto is_focused = [&](const std::string& encounter) {
            try { const auto id=static_cast<unsigned>(std::stoul(encounter)); return id<20&&(focusMask.load(std::memory_order_relaxed)&(1u<<id))!=0; }
            catch(...) { return false; }
        };

        Queue queue; std::mutex doneMutex; std::condition_variable doneCv;
        std::queue<Done> finished;
        std::mutex workMutex;
        std::map<std::string,EncounterWork> workByEncounter;
        for(int id=0;id<20;++id) workByEncounter.emplace(std::to_string(id),EncounterWork{});
        for(const auto& c:candidates) ++workByEncounter[encounter_key(c.raw)].candidates;
        std::uint64_t localCompletedCount=0;
        for(const auto& [encounter,count]:localCompletedByEncounter) { workByEncounter[encounter].completed=count; localCompletedCount+=count; }
        std::atomic<std::uint64_t> acceptedTrials{0}, queuedTrials{0}, activeTrials{0}, pendingSaveTrials{0}, durableCompletedTrials{localCompletedCount};
        std::atomic<std::uint64_t> durableCompletedThisRun{0}, drainedJobsThisRun{0};
        std::atomic<std::uint64_t> actualActiveWorkers{0}, maxActiveWorkers{0};
        std::atomic<std::uint64_t> workerThreadCpuNs{0}, workerThreadCpuSamples{0}, workerThreadCpuUnavailable{0};
        std::atomic<std::uint64_t> statusSaved{completed.size()}, statusFreshSaved{0}, statusRestoredRecords{completed.size()};
        std::atomic<std::uint64_t> statusDuplicates{0}, statusCandidateCount{candidates.size()};
        std::atomic<std::uint64_t> invalidTrials{0}, executionErrors{0}, successfulTrials{0}, scoredTrials{0};
        std::atomic<std::uint64_t> admissionWallNs{0}, preparationWallNs{0};
        std::atomic<std::uint64_t> workerAdmissionWallNs{0}, workerPreparationWallNs{0};
        std::atomic<std::uint64_t> workerAdmissionSamples{0}, workerPreparationSamples{0};
        std::atomic<std::uint64_t> executeCalls{0}, executeCallWallNs{0};
        std::atomic<std::uint64_t> workerTimingSamples{0};
        std::atomic<std::uint64_t> importSetupWallNs{0}, nativeRunWallNs{0}, reportCollectionWallNs{0}, totalExecuteWallNs{0};
        std::atomic<std::uint64_t> preparedHashWallNs{0}, workerRecordAssemblyWallNs{0};
        std::atomic<std::uint64_t> resultSerializationWallNs{0}, resultAppendSyncWallNs{0}, durableSyncWallNs{0};
        std::atomic<std::uint64_t> resultJournalBytesAppended{0}, resultJournalRowsAppended{0}, resultJournalSyncs{0};
        const auto make_timing_telemetry = [&]() {
            Json t{{"measurementClock","steady_clock nanoseconds"},
                {"startupWallNs",startupWallNs},{"inputJsonLoadWallNs",inputJsonLoadWallNs},
                {"serialAdmissionWallNs",admissionWallNs.load()},{"serialPreparationWallNs",preparationWallNs.load()},
                {"serialAdmissionPreparationScope","driver-side generated-stat feasibility checks only; trial validation/preparation runs in workers"},
                {"workerExecuteCallCount",executeCalls.load()},{"workerExecuteCallWallNs",executeCallWallNs.load()},
                {"workerExecuteCallAggregation","sum of worker-summed elapsed WALL intervals; includes preemption/waits and overlaps across the driver and other workers; NOT CPU busy time"},
                {"workerCpuBusyTime","not equivalent to engine-only busy time; see workerThreadCpuNs"},
                {"workerThreadCpuNs",workerThreadCpuSamples.load()?Json(workerThreadCpuNs.load()):Json(nullptr)},
                {"workerThreadCpuSamples",workerThreadCpuSamples.load()},
                {"workerThreadCpuScope","per-worker thread CPU deltas around admission, preparation and execute(); excludes record assembly"},
                {"workerAdmissionWallNs",workerAdmissionWallNs.load()},{"workerPreparationWallNs",workerPreparationWallNs.load()},
                {"workerAdmissionSamples",workerAdmissionSamples.load()},{"workerPreparationSamples",workerPreparationSamples.load()},
                {"workerPreparationAggregation","sum of per-worker elapsed wall intervals; intervals may overlap and are not CPU busy time"},
                {"executeTimingSuccessfulSamples",workerTimingSamples.load()},
                {"importSetupWallNs",importSetupWallNs.load()},{"nativeRunWallNs",nativeRunWallNs.load()},
                {"reportCollectionWallNs",reportCollectionWallNs.load()},{"totalExecuteWallNs",totalExecuteWallNs.load()},
                {"executionTimingAggregation","sum of successful worker-reported elapsed WALL intervals; includes preemption/waits and overlaps across the driver and other workers; NOT CPU busy time"},
                {"workerPreparedSnapshotHashWallNs",preparedHashWallNs.load()},
                {"workerRecordAssemblyWallNs",workerRecordAssemblyWallNs.load()},
                {"workerHashAndAssemblyAggregation","sum of worker elapsed WALL intervals; overlaps across the driver and other workers; NOT CPU busy time"},
                {"journalResultSerializationWallNs",resultSerializationWallNs.load()},
                {"journalResultAppendSyncWallNs",resultAppendSyncWallNs.load()},
                {"journalResultDurableSyncWallNs",durableSyncWallNs.load()},
                {"durableSyncTimingInterpretation","nested subset of append sync time; direct FlushFileBuffers/fsync call wall time"},
                {"resultJournalRowsAppended",resultJournalRowsAppended.load()},
                {"resultJournalBytesAppended",resultJournalBytesAppended.load()},
                {"resultJournalSyncs",resultJournalSyncs.load()},
                {"resultJournalDurableSyncBatchRows",splitDurableSyncBatches ? Json(durableSyncBatchSize) : Json(nullptr)}};
            if (config.contains("comparisonMainTiming")) t["mainTiming"] = config.at("comparisonMainTiming");
            return t;
        };
        const auto make_work_telemetry = [&]() {
            Json byEncounter=Json::object();
            std::uint64_t candidatesNow=0;
            {
                std::lock_guard<std::mutex> lock(workMutex);
                for(const auto& [id,w]:workByEncounter) {
                    byEncounter[id]=Json{{"candidateCount",w.candidates},{"acceptedTrials",w.accepted},
                        {"acceptedJobs",w.accepted},
                        {"queuedTrials",w.queued},{"activeTrials",w.active},{"pendingSaveTrials",w.pendingSave},
                        {"durableCompletedTrials",w.completed},{"drainedJobs",w.drainedJobs}};
                    candidatesNow+=w.candidates;
                }
            }
            const auto elapsed=static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
                std::chrono::steady_clock::now()-wallStart).count());
            const auto mask=focusMask.load(std::memory_order_relaxed);
            Json focus=Json::array(); for(int id=0;id<20;++id) if(mask&(1u<<id)) focus.push_back(id);
            Json cpuNs=workerThreadCpuSamples.load(std::memory_order_relaxed)?Json(workerThreadCpuNs.load(std::memory_order_relaxed)):Json(nullptr);
            std::string warning; { std::lock_guard<std::mutex> lock(controlWarningMutex); warning=controlWarning; }
            return Json{{"startedUnixMs",startedUnixMs},{"elapsedWallNs",elapsed},{"startupWallNs",startupWallNs},
                {"searchScheduling",searchScheduling},{"adaptiveScope",adaptiveScope},{"focusEncounterIds",focus},
                {"controlPath",controlPath.empty()?Json(nullptr):Json(controlPath)},
                {"pauseRequested",pauseRequested.load(std::memory_order_relaxed)},
                {"stopRequested",stopRequested.load(std::memory_order_relaxed)},
                {"controlWarning",warning.empty()?Json(nullptr):Json(warning)},
                {"candidateCount",std::max(candidatesNow,statusCandidateCount.load(std::memory_order_relaxed))},
                {"configuredWorkerSlots",workers},{"actualActiveWorkers",actualActiveWorkers.load(std::memory_order_relaxed)},
                {"maxObservedActiveWorkers",maxActiveWorkers.load(std::memory_order_relaxed)},
                {"acceptedTrials",acceptedTrials.load(std::memory_order_relaxed)},
                {"acceptedJobs",acceptedTrials.load(std::memory_order_relaxed)},
                {"queuedTrials",queuedTrials.load(std::memory_order_relaxed)},
                {"activeTrials",activeTrials.load(std::memory_order_relaxed)},
                {"pendingSaveTrials",pendingSaveTrials.load(std::memory_order_relaxed)},
                {"durableCompletedTrials",durableCompletedTrials.load(std::memory_order_relaxed)},
                {"durableCompletedThisRun",durableCompletedThisRun.load(std::memory_order_relaxed)},
                {"drainedJobsThisRun",drainedJobsThisRun.load(std::memory_order_relaxed)},
                {"inflightTrials",queuedTrials.load()+activeTrials.load()+pendingSaveTrials.load()},
                {"perEncounterWork",std::move(byEncounter)},
                {"workerThreadCpuNs",cpuNs},{"workerThreadCpuSamples",workerThreadCpuSamples.load()},
                {"workerThreadCpuUnavailableSamples",workerThreadCpuUnavailable.load()},
                {"workerThreadCpuScope","sum of per-worker thread CPU deltas around admission, preparation and execute(); excludes record assembly and is not engine-only CPU"},
                {"workerAdmissionWallNs",workerAdmissionWallNs.load(std::memory_order_relaxed)},
                {"workerPreparationWallNs",workerPreparationWallNs.load(std::memory_order_relaxed)},
                {"workerAdmissionSamples",workerAdmissionSamples.load(std::memory_order_relaxed)},
                {"workerPreparationSamples",workerPreparationSamples.load(std::memory_order_relaxed)},
                {"workerPreparationAggregation","sum of per-worker elapsed wall intervals; intervals may overlap and are not CPU busy time"},
                {"throughputTrialsPerSecond",elapsed?Json(static_cast<double>(durableCompletedThisRun.load())*1.0e9/static_cast<double>(elapsed)):Json(nullptr)},
                {"etaSeconds",nullptr},{"etaReason","remaining candidate and generation totals are dynamic; ETA not estimated"}};
        };
        std::thread monitor([&] {
                std::string lastBytes;
                bool haveBytes=false;
                auto nextStatus=std::chrono::steady_clock::now();
                while(!monitorStop.load(std::memory_order_relaxed)) {
                    try { if(!controlPath.empty()) {
                        std::ifstream input(controlPath,std::ios::binary);
                        if(!input) throw std::runtime_error("control file is not readable");
                        std::string bytes((std::istreambuf_iterator<char>(input)),std::istreambuf_iterator<char>());
                        if(!haveBytes||bytes!=lastBytes) {
                            Json control=Json::parse(bytes);
                            if(!control.is_object()) throw std::runtime_error("control file must contain a JSON object");
                            auto nextMask=focusMask.load(std::memory_order_relaxed);
                            bool nextPause=pauseRequested.load(std::memory_order_relaxed);
                            bool requestStop=stopRequested.load(std::memory_order_relaxed);
                            if(control.contains("focusEncounterIds")) {
                                const auto& ids=control.at("focusEncounterIds");
                                if(!ids.is_array()) throw std::runtime_error("focusEncounterIds must be an array");
                                nextMask=0;
                                for(const auto& id:ids) {
                                    if((!id.is_number_integer()&&!id.is_number_unsigned())||id.is_boolean()||id.get<std::int64_t>()<0||id.get<std::int64_t>()>19)
                                        throw std::runtime_error("focusEncounterIds entries must be integers from 0 through 19");
                                    nextMask|=1u<<static_cast<unsigned>(id.get<std::int64_t>());
                                }
                                if(ids.empty()) nextMask=(1u<<20)-1;
                            }
                            if(control.contains("pauseRequested")) {
                                if(!control.at("pauseRequested").is_boolean()) throw std::runtime_error("pauseRequested must be boolean");
                                nextPause=control.at("pauseRequested").get<bool>();
                            }
                            if(control.contains("stopRequested")) {
                                if(!control.at("stopRequested").is_boolean()) throw std::runtime_error("stopRequested must be boolean");
                                requestStop=requestStop||control.at("stopRequested").get<bool>();
                            }
                            focusMask.store(nextMask,std::memory_order_relaxed);
                            pauseRequested.store(nextPause,std::memory_order_relaxed);
                            stopRequested.store(requestStop,std::memory_order_relaxed);
                            lastBytes=std::move(bytes); haveBytes=true;
                            std::lock_guard<std::mutex> warningLock(controlWarningMutex); controlWarning.clear();
                        }
                    }} catch(const std::exception& e) {
                        std::lock_guard<std::mutex> warningLock(controlWarningMutex);
                        controlWarning=e.what();
                    }
                    const auto now=std::chrono::steady_clock::now();
                    if(now>=nextStatus) {
                        try {
                            const auto inflight=queuedTrials.load()+activeTrials.load()+pendingSaveTrials.load();
                            const char* state=stopRequested.load()?"stopping":(pauseRequested.load()?(inflight?"pausing":"paused"):"running");
                            Json status{{"schema","kaopt-status-1"},{"state",state},{"stage","search"},
                                {"savedRecords",statusSaved.load()},{"completedTrials",durableCompletedTrials.load()},
                                {"acceptedTrials",acceptedTrials.load()},{"queuedTrials",queuedTrials.load()},
                                {"activeTrials",activeTrials.load()},{"pendingSaveTrials",pendingSaveTrials.load()},
                                {"durableCompletedTrials",durableCompletedTrials.load()},
                                {"durableCompletedThisRun",durableCompletedThisRun.load()},
                                {"freshSavedRecords",statusFreshSaved.load()},{"restoredRecords",statusRestoredRecords.load()},
                                {"duplicateSkips",statusDuplicates.load()},{"invalidCandidates",invalidTrials.load()},
                                {"executionErrors",executionErrors.load()},{"scoredOutcomes",scoredTrials.load()},
                                {"persistPreparedSnapshot",persistPreparedSnapshot}};
                            const Json work=make_work_telemetry();
                            for(auto it=work.begin();it!=work.end();++it) status[it.key()]=it.value();
                            if(stageTimingTelemetry) status["timingTelemetry"]=make_timing_telemetry();
                            write_status(outDir,status);
                        } catch(...) {}
                        nextStatus=now+std::chrono::milliseconds(500);
                    }
                    std::this_thread::sleep_for(std::chrono::milliseconds(50));
                }
            });
        StopJoinThread monitorGuard{monitorStop,monitor};
        std::vector<std::thread> pool; pool.reserve(static_cast<std::size_t>(workers));
        DurableJournal journal(journalPath), errorsJournal(errorsPath);
        const auto make_candidate_rejection = [&](const char* stage,const Candidate& candidate,
                                                   const Json& scenario,std::int64_t seed,const Json& detail) {
            return Json{{"schema","kaopt-diagnostic-1"},{"kind","candidate-rejected"},{"stage",stage},
                {"candidateId",identity(scenario)},{"strategyId",candidate.id},{"candidateScenario",candidate.raw},
                {"rawScenario",scenario},{"parentCandidateId",candidate.parent},{"generation",candidate.generation},
                {"seed",seed},{"provenance",provenance},{"searchPolicy",runPolicy},{"detail",detail}};
        };
        JoinOnExit joinGuard{queue, pool};
        for (std::uint64_t w = 0; w < workers; ++w) {
            pool.emplace_back([&] {
                Job job;
                while (queue.pop(job,[&](const Job& started) {
                    const auto encounter=encounter_key(started.scenario);
                    { std::lock_guard<std::mutex> lock(workMutex); auto& work=workByEncounter[encounter];
                      if(work.queued) --work.queued; ++work.active; }
                    queuedTrials.fetch_sub(1,std::memory_order_relaxed);
                    activeTrials.fetch_add(1,std::memory_order_relaxed);
                    const auto active=actualActiveWorkers.fetch_add(1,std::memory_order_relaxed)+1;
                    auto prior=maxActiveWorkers.load(std::memory_order_relaxed);
                    while(active>prior&&!maxActiveWorkers.compare_exchange_weak(prior,active,std::memory_order_relaxed)) {}
                })) {
                    Json result=nullptr, record=nullptr, diagnostic=nullptr;
                    std::uint64_t cpuStart=0,cpuEnd=0;
                    const bool cpuStartValid=current_thread_cpu_ns(cpuStart);
                    const auto capture_worker_cpu = [&] {
                        if(cpuStartValid&&current_thread_cpu_ns(cpuEnd)&&cpuEnd>=cpuStart) {
                            workerThreadCpuNs.fetch_add(cpuEnd-cpuStart,std::memory_order_relaxed);
                            workerThreadCpuSamples.fetch_add(1,std::memory_order_relaxed);
                        } else workerThreadCpuUnavailable.fetch_add(1,std::memory_order_relaxed);
                    };
                    std::string rejectionStage;
                    Json rejectionDetail=nullptr, admitted, snapshot;
                    const auto admissionStart=stageTimingTelemetry?std::chrono::steady_clock::now():std::chrono::steady_clock::time_point{};
                    try { admitted=admit(job.scenario,tables); }
                    catch(const std::exception& e) { rejectionStage="admission-exception"; rejectionDetail=Json{{"error",e.what()}}; }
                    catch(...) { rejectionStage="admission-exception"; rejectionDetail=Json{{"error","unknown exception"}}; }
                    if(stageTimingTelemetry) {
                        workerAdmissionWallNs.fetch_add(static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
                            std::chrono::steady_clock::now()-admissionStart).count()),std::memory_order_relaxed);
                        workerAdmissionSamples.fetch_add(1,std::memory_order_relaxed);
                    }
                    if(rejectionStage.empty()&&has_error(admitted)) {
                        rejectionStage="admission-rejected";
                        rejectionDetail=admitted;
                    }
                    if(rejectionStage.empty()) {
                        const auto preparationStart=stageTimingTelemetry?std::chrono::steady_clock::now():std::chrono::steady_clock::time_point{};
                        try { snapshot=prepare(admitted,tables,false,&job.scenario); }
                        catch(const std::exception& e) { rejectionStage="preparation-exception"; rejectionDetail=Json{{"error",e.what()}}; }
                        catch(...) { rejectionStage="preparation-exception"; rejectionDetail=Json{{"error","unknown exception"}}; }
                        if(stageTimingTelemetry) {
                            workerPreparationWallNs.fetch_add(static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
                                std::chrono::steady_clock::now()-preparationStart).count()),std::memory_order_relaxed);
                            workerPreparationSamples.fetch_add(1,std::memory_order_relaxed);
                        }
                        if(rejectionStage.empty()&&has_error(snapshot)) {
                            rejectionStage="preparation-rejected";
                            rejectionDetail=snapshot;
                        }
                    }
                    if(!rejectionStage.empty()) {
                        diagnostic=make_candidate_rejection(rejectionStage.c_str(),job.candidate,job.scenario,job.seed,rejectionDetail);
                        capture_worker_cpu();
                    } else {
                    job.snapshot=std::move(snapshot);
                    const auto executeStart = stageTimingTelemetry ? std::chrono::steady_clock::now() : std::chrono::steady_clock::time_point{};
                    try { result = execute(job.snapshot, kernel, stageTimingTelemetry); }
                    catch (const std::exception& e) { result = Json{{"error", e.what()}}; }
                    catch (...) { result = Json{{"error", "unknown native execution exception"}}; }
                    capture_worker_cpu();
                    if (stageTimingTelemetry) {
                        executeCalls.fetch_add(1, std::memory_order_relaxed);
                        executeCallWallNs.fetch_add(static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
                            std::chrono::steady_clock::now() - executeStart).count()), std::memory_order_relaxed);
                    }
                    const Json executionTelemetry = result.is_object() ? result.value("timingTelemetry", Json::object()) : Json::object();
                    const auto preparedHashStart = stageTimingTelemetry ? std::chrono::steady_clock::now() : std::chrono::steady_clock::time_point{};
                    const std::string preparedSha256 = sha256_text(canonical(job.snapshot));
                    if (stageTimingTelemetry) preparedHashWallNs.fetch_add(static_cast<std::uint64_t>(
                        std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now() - preparedHashStart).count()),
                        std::memory_order_relaxed);
                    const auto recordAssemblyStart = stageTimingTelemetry ? std::chrono::steady_clock::now() : std::chrono::steady_clock::time_point{};
                    double earned = 0; bool hasMetric = false; std::string earnedBasis="unresolved";
                    if (has_error(result) || !read_metric(result, earned, hasMetric, earnedBasis)) {
                        ++executionErrors;
                        diagnostic = Json{{"schema","kaopt-diagnostic-1"},{"kind","execution-error"},
                            {"candidateId",identity(job.scenario)},{"strategyId",job.candidate.id},
                            {"candidateScenario",job.candidate.raw},{"rawScenario",job.scenario},
                            {"preparedSnapshot",job.snapshot},{"preparedSha256",preparedSha256},
                            {"parentCandidateId",job.candidate.parent},
                            {"generation",job.candidate.generation},{"encounterId",job.scenario.at("encounterId")},{"seed",job.seed},
                            {"provenance",provenance},{"searchPolicy",runPolicy},{"result",result}};
                    }
                    else {
                        ++successfulTrials;
                        const bool scored=hasMetric;
                        const bool evidenceEligible=eligible_score(earnedBasis,hasMetric);
                        if (scored) ++scoredTrials;
                        const std::string trialId = identity(job.scenario);
                        const std::string key = trialId + ":" + canonical(provenance);
                        const Json& actualSeeds=job.seeds;
                        record = Json{{"schema","kaopt-result-1"},{"dedupKey",key},
                            {"candidateId",trialId},{"strategyId",job.candidate.id},{"rawScenario",job.scenario},
                            {"candidateScenario",job.candidate.raw},
                            {"parentCandidateId",job.candidate.parent},{"generation",job.candidate.generation},
                            {"sourceBinding",job.candidate.sourceBinding},
                            {"encounterId",job.scenario.at("encounterId")},{"mutation",job.candidate.operation},
                            {"seed",job.seed},{"seedValues",actualSeeds},{"seedPair",actualSeeds},
                            {"provenance",provenance},{"searchPolicy",runPolicy},
                            {"preparedSha256",preparedSha256},
                            {"earned",scored?Json(earned):Json(nullptr)},{"observedEarned",hasMetric?Json(earned):Json(nullptr)},
                            {"earnedBasis",earnedBasis},
                            {"scoreEligible",scored},{"evidenceEligible",evidenceEligible},{"result",result}};
                        if(persistPreparedSnapshot) record["preparedSnapshot"]=job.snapshot;
                        if(!seedPairs.empty()) record["seedPair"]=actualSeeds;
                        mark_diagnostic(record,job.scenario);
                        mark_diagnostic(record["result"],job.scenario);
                        if(evidenceEligible) {
                            const auto reportHash=sha256_text(canonical(result.at("report")));
                            for(Json* target:{&record,&record["result"]}) {
                                (*target)["verified"]=true;(*target)["diagnostic"]=false;
                                (*target)["verificationOnly"]=false;(*target)["importDisabled"]=false;
                                (*target)["verificationStatus"]="verified-native-resolved-earned";
                            }
                            record["verificationProof"]={{"basis",earnedBasis},{"nativeChecksum",result.at("nativeChecksum")},
                                {"reportSha256",reportHash},{"resolvedVerdict",result.at("report").value("verdict",0)},
                                {"rewardOutcome",result.value("rewardOutcome",Json::object())}};
                        } else {
                            for(Json* target:{&record,&record["result"]}) {
                                (*target)["verified"]=false;(*target)["diagnostic"]=true;
                                (*target)["verificationOnly"]=true;(*target)["importDisabled"]=true;
                                (*target)["verificationStatus"]="unverified-or-diagnostic";
                            }
                        }
                        if (stageTimingTelemetry) {
                            workerTimingSamples.fetch_add(1, std::memory_order_relaxed);
                            importSetupWallNs.fetch_add(timing_value(executionTelemetry, "importSetupWallNs"), std::memory_order_relaxed);
                            nativeRunWallNs.fetch_add(timing_value(executionTelemetry, "nativeRunWallNs"), std::memory_order_relaxed);
                            reportCollectionWallNs.fetch_add(timing_value(executionTelemetry, "reportCollectionWallNs"), std::memory_order_relaxed);
                            totalExecuteWallNs.fetch_add(timing_value(executionTelemetry, "totalExecuteWallNs"), std::memory_order_relaxed);
                        }
                    }
                    if (stageTimingTelemetry) workerRecordAssemblyWallNs.fetch_add(static_cast<std::uint64_t>(
                        std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now() - recordAssemblyStart).count()),
                        std::memory_order_relaxed);
                    }
                    {
                        const auto encounter=encounter_key(job.scenario);
                        { std::lock_guard<std::mutex> lock(workMutex); auto& work=workByEncounter[encounter];
                          if(work.active) --work.active; ++work.pendingSave; }
                        activeTrials.fetch_sub(1,std::memory_order_relaxed);
                        pendingSaveTrials.fetch_add(1,std::memory_order_relaxed);
                        actualActiveWorkers.fetch_sub(1,std::memory_order_relaxed);
                        std::lock_guard<std::mutex> g(doneMutex); finished.push(Done{job,std::move(record),std::move(diagnostic)});
                    }
                    doneCv.notify_one();
                }
            });
        }
        const std::uint64_t restoredRecords=completed.size();
        std::uint64_t duplicateSkips=0;
        std::uint64_t drained = 0, saved = completed.size();
        const auto export_leaders=[&] {
            Json entries=Json::array();
            for(int encounter=0;encounter<20;++encounter){
                const Candidate* best=nullptr;long double bestMean=-std::numeric_limits<long double>::infinity();
                for(const auto& candidate:candidates){if(encounter_key(candidate.raw)!=std::to_string(encounter))continue;auto i=scores.find(candidate.id);if(i==scores.end()||i->second.outcomesByTrial.empty())continue;auto mean=mean_score(i->second);if(!best||mean>bestMean||(mean==bestMean&&candidate.id<best->id)){best=&candidate;bestMean=mean;}}
                if(!best){entries.push_back(Json{{"encounterId",encounter},{"status","no-scored-compatible-outcome"},{"sampleCount",0},{"verified",false},{"diagnostic",true},{"verificationOnly",true},{"importDisabled",true},{"verificationStatus","unverified-no-resolved-earned"}});continue;}
                const auto& outcomes=scores.at(best->id).outcomesByTrial;double highest=-std::numeric_limits<double>::infinity();long double sumsq=0;for(const auto& [_,v]:outcomes){highest=std::max(highest,v);auto delta=static_cast<long double>(v)-bestMean;sumsq+=delta*delta;}
                const auto count=outcomes.size();Json se=nullptr;if(count>1)se=std::sqrt(static_cast<double>(sumsq/(count-1)/count));
                entries.push_back(Json{{"encounterId",encounter},{"status","measured"},{"strategyId",best->id},{"rawScenario",best->raw},{"sampleCount",count},{"meanEarned",static_cast<double>(bestMean)},{"highestEarned",highest},{"meanStandardError",se},{"formationCompliance","not-yet-attested"}});
                mark_diagnostic(entries.back(),best->raw);
                entries.back()["verified"]=strategyPurpose; entries.back()["diagnostic"]=!strategyPurpose;
                entries.back()["verificationOnly"]=!strategyPurpose; entries.back()["importDisabled"]=!strategyPurpose;
                entries.back()["verificationStatus"]=strategyPurpose?"verified-native-resolved-earned":"diagnostic-only";
                entries.back()["earnedBasisCounts"]=Json::object();
                for(const auto& [_,basis]:scores.at(best->id).basisByTrial) entries.back()["earnedBasisCounts"][basis]=entries.back()["earnedBasisCounts"].value(basis,std::uint64_t(0))+1;
            }
            Json exportData{{"schema","kaopt-leaders-1"},{"provenance",provenance},{"encounters",entries},{"savedRecords",saved},{"scorePolicy",runPolicy}};
            auto target=outDir/"best-strategies.json";auto temp=outDir/"best-strategies.json.tmp";{std::ofstream f(temp,std::ios::binary|std::ios::trunc);if(!f)throw std::runtime_error("leader export open failed");f<<exportData.dump(2)<<'\n';f.flush();if(!f)throw std::runtime_error("leader export failed");}commit_json_file(temp,target);
        };
        const auto drain = [&](std::uint64_t amount) {
          std::string payload;
          std::string diagnosticPayload;
          std::vector<Json> batchRecords;
          std::vector<std::string> drainedEncounters;
          std::vector<std::string> savedEncounters;
          std::uint64_t rejectedCandidates=0;
          for (std::uint64_t i = 0; i < amount; ++i) {
            Done d;
            {
                std::unique_lock<std::mutex> g(doneMutex);
                doneCv.wait(g, [&]{ return !finished.empty(); });
                d = std::move(finished.front()); finished.pop();
            }
            drainedEncounters.push_back(encounter_key(d.job.scenario));
            if (!d.diagnostic.is_null()) {
                if(d.diagnostic.value("kind",std::string{})=="candidate-rejected") ++rejectedCandidates;
                diagnosticPayload += d.diagnostic.dump(-1, ' ', false) + "\n";
            }
            if (!d.record.is_null()) {
                const auto serializationStart = stageTimingTelemetry ? std::chrono::steady_clock::now() : std::chrono::steady_clock::time_point{};
                payload += d.record.dump(-1, ' ', false) + "\n";
                if (stageTimingTelemetry) resultSerializationWallNs.fetch_add(static_cast<std::uint64_t>(
                    std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now() - serializationStart).count()),
                    std::memory_order_relaxed);
                savedEncounters.push_back(encounter_key(d.job.scenario));
                batchRecords.push_back(std::move(d.record));
            }
            ++drained;
          }
          const auto oldSaved = saved;
          if (!diagnosticPayload.empty()) errorsJournal.append(diagnosticPayload);
          invalidTrials.fetch_add(rejectedCandidates,std::memory_order_relaxed);
          if (!payload.empty()) {
              std::size_t at = 0;
              while (at < payload.size()) {
                  std::size_t end = payload.size();
                  std::uint64_t rows = 0;
                  if (splitDurableSyncBatches) {
                      rows = 0;
                      end = at;
                      while (end < payload.size() && rows < durableSyncBatchSize) {
                          const auto newline = payload.find('\n', end);
                          if (newline == std::string::npos) throw std::runtime_error("result journal payload lacks a complete newline record");
                          end = newline + 1;
                          ++rows;
                      }
                  } else {
                      rows = static_cast<std::uint64_t>(batchRecords.size());
                  }
                  const auto appendStart = stageTimingTelemetry ? std::chrono::steady_clock::now() : std::chrono::steady_clock::time_point{};
                  std::uint64_t thisDurableSyncWallNs = 0;
                  if(splitDurableSyncBatches)
                      journal.append(payload.substr(at, end - at), stageTimingTelemetry ? &thisDurableSyncWallNs : nullptr);
                  else
                      journal.append(payload, stageTimingTelemetry ? &thisDurableSyncWallNs : nullptr);
                  if (stageTimingTelemetry) {
                      resultAppendSyncWallNs.fetch_add(static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
                          std::chrono::steady_clock::now() - appendStart).count()), std::memory_order_relaxed);
                      durableSyncWallNs.fetch_add(thisDurableSyncWallNs, std::memory_order_relaxed);
                      resultJournalBytesAppended.fetch_add(static_cast<std::uint64_t>(end - at), std::memory_order_relaxed);
                      resultJournalRowsAppended.fetch_add(rows, std::memory_order_relaxed);
                      resultJournalSyncs.fetch_add(1, std::memory_order_relaxed);
                  }
                  at = end;
              }
          }
          for (const auto& record : batchRecords) {
              completed.insert(record["dedupKey"].get<std::string>());
              learningCompleted.insert(record["candidateId"].get<std::string>());
              if (record["scoreEligible"].get<bool>()) {
                  const std::string id = record["strategyId"].get<std::string>();
                  if (!scores[id].outcomesByTrial.emplace(record["candidateId"].get<std::string>(),
                      record["earned"].get<double>()).second)
                      throw std::runtime_error("duplicate scored trial identity while saving");
                  scores[id].basisByTrial[record["candidateId"].get<std::string>()]=record.value("earnedBasis",std::string("unresolved"));
                  freshScores[id].outcomesByTrial[record["candidateId"].get<std::string>()]=record["earned"].get<double>();
                  freshScores[id].basisByTrial[record["candidateId"].get<std::string>()]=record.value("earnedBasis",std::string("unresolved"));
              }
              ++saved;
          }
          for(const auto& encounter:drainedEncounters) {
              std::lock_guard<std::mutex> lock(workMutex);
              auto& work=workByEncounter[encounter];
              if(work.pendingSave) --work.pendingSave;
              ++work.drainedJobs;
          }
          for(const auto& encounter:savedEncounters) {
              std::lock_guard<std::mutex> lock(workMutex);
              ++workByEncounter[encounter].completed;
          }
          // Every drained Done record was previously counted as pending-save by
          // its worker. Release the aggregate in-flight count alongside the
          // per-encounter counters so Pause can observe a fully drained batch.
          pendingSaveTrials.fetch_sub(static_cast<std::uint64_t>(drainedEncounters.size()),
                                      std::memory_order_relaxed);
          drainedJobsThisRun.fetch_add(drainedEncounters.size(),std::memory_order_relaxed);
          durableCompletedTrials.fetch_add(savedEncounters.size(),std::memory_order_relaxed);
          durableCompletedThisRun.fetch_add(savedEncounters.size(),std::memory_order_relaxed);
          statusSaved.store(saved,std::memory_order_relaxed);
          statusFreshSaved.store(saved-restoredRecords,std::memory_order_relaxed);
          if (saved / 128 > oldSaved / 128) {
              const Json& last = batchRecords.back();
              Json cp{{"schema","kaopt-checkpoint-1"},{"journalRecords",completed.size()},
                  {"lastCandidateId",last.at("candidateId")},{"lastSeed",last.at("seed")},
                  {"lastGeneration",last.at("generation")},{"provenance",provenance}};
              const fs::path tmp = checkpointPath.string() + ".tmp";
              { std::ofstream cf(tmp, std::ios::binary | std::ios::trunc); if (!cf) throw std::runtime_error("cannot write checkpoint"); cf << cp.dump(2) << '\n'; cf.flush(); if (!cf) throw std::runtime_error("checkpoint flush failed"); }
              commit_json_file(tmp,checkpointPath);
          }
          Json status{{"schema","kaopt-status-1"},{"state","running"},
              {"stage","search"},{"savedRecords",saved},{"invalidCandidates",invalidTrials.load()},
              {"freshSavedRecords",saved-restoredRecords},{"restoredRecords",restoredRecords},{"duplicateSkips",duplicateSkips},
              {"completedTrials",durableCompletedTrials.load()},{"drainedJobsThisRun",drainedJobsThisRun.load()},
              {"executionErrors",executionErrors.load()},{"scoredOutcomes",scoredTrials.load()},{"persistPreparedSnapshot",persistPreparedSnapshot},
              {"startupMs",startupMs},
              {"elapsedMs",std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-wallStart).count()},
              {"elapsedWallNs",static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now()-wallStart).count())}};
          const Json work=make_work_telemetry(); for(auto it=work.begin();it!=work.end();++it) status[it.key()]=it.value();
          if (stageTimingTelemetry) status["timingTelemetry"] = make_timing_telemetry();
          write_status(outDir, status);
          export_leaders();
        };
        // The driver bounds all accepted queue/worker/save work to at most 2*workers.
        const std::uint64_t batchLimit = std::max<std::uint64_t>(1, workers * 2);
        const auto service_control = [&](std::uint64_t& batchCount) {
            auto flush=[&] { if(batchCount) { drain(batchCount); batchCount=0; } };
            if(pauseRequested.load(std::memory_order_relaxed)||stopRequested.load(std::memory_order_relaxed)) {
                flush();
                return false;
            }
            return true;
        };
        const auto enqueue_trial = [&](const Candidate& c,std::uint64_t si,std::uint64_t& batchCount) {
            if(!is_focused(encounter_key(c.raw))) return true;
            if(!service_control(batchCount)) return false;
            const auto seed = seedPairs.empty()?seedStart+static_cast<std::int64_t>(si):seedPairs[static_cast<std::size_t>(si)][0].get<std::int64_t>();
            Json actualSeeds=Json::array();
            if(seedPairs.empty()) for(std::size_t pi=0;pi<seedPointers.size();++pi) actualSeeds.push_back(seed);
            else { if(seedPointers.size()!=2) throw std::runtime_error("seedPairs requires exactly two seedPointers"); actualSeeds=seedPairs[static_cast<std::size_t>(si)]; }
            Candidate trial = c;
            for (std::size_t pi=0;pi<seedPointers.size();++pi) {
                const auto& p=seedPointers[pi];
                Json& value = trial.raw[Json::json_pointer(p.get<std::string>())];
                if (!value.is_number_integer()) throw std::runtime_error("seed pointer must target an existing integer field");
                value = actualSeeds[pi];
            }
            const std::string key = identity(trial.raw) + ":" + canonical(provenance);
            if (completed.contains(key)||learningCompleted.count(identity(trial.raw))) { ++duplicateSkips; statusDuplicates.fetch_add(1,std::memory_order_relaxed); return true; }
            const std::string encounter=encounter_key(trial.raw);
            queue.push(Job{c, std::move(trial.raw), Json(nullptr), seed, actualSeeds},[&](const Job& accepted) {
                const auto id=encounter_key(accepted.scenario);
                { std::lock_guard<std::mutex> lock(workMutex); auto& work=workByEncounter[id]; ++work.accepted; ++work.queued; }
                acceptedTrials.fetch_add(1,std::memory_order_relaxed); queuedTrials.fetch_add(1,std::memory_order_relaxed);
            });
            (void)encounter;
            ++batchCount;
            if(batchCount==batchLimit) { drain(batchCount); batchCount=0; }
            return true;
        };
        const auto evaluate_cohort = [&](const std::vector<Candidate>& cohort) {
            std::vector<const Candidate*> ordered;
            ordered.reserve(cohort.size());
            if(searchScheduling=="encounter-wave") {
                std::vector<std::string> encounterOrder;
                std::map<std::string,std::vector<const Candidate*>> byEncounter;
                for(const auto& candidate:cohort) {
                    const auto encounter=encounter_key(candidate.raw);
                    auto [it,inserted]=byEncounter.try_emplace(encounter);
                    if(inserted) encounterOrder.push_back(encounter);
                    it->second.push_back(&candidate);
                }
                for(std::size_t parentIndex=0;;++parentIndex) {
                    bool found=false;
                    for(const auto& encounter:encounterOrder) {
                        const auto& parents=byEncounter.at(encounter);
                        if(parentIndex<parents.size()) {
                            ordered.push_back(parents[parentIndex]);
                            found=true;
                        }
                    }
                    if(!found) break;
                }
            } else {
                for(const auto& candidate:cohort) ordered.push_back(&candidate);
            }
            std::uint64_t batchCount=0;
            bool complete=true;
            for(std::uint64_t si=0;si<seedCount;++si) {
                for(const auto* candidatePointer:ordered) {
                    const auto& candidate=*candidatePointer;
                    if(!is_focused(encounter_key(candidate.raw))) { complete=false; continue; }
                    if(!enqueue_trial(candidate,si,batchCount)) { if(batchCount) drain(batchCount); return false; }
                    if(!is_focused(encounter_key(candidate.raw))) complete=false;
                }
            }
            if(batchCount) drain(batchCount);
            return complete&&!stopRequested.load(std::memory_order_relaxed);
        };
        const auto evaluate_candidate = [&](const Candidate& c) {
            return evaluate_cohort(std::vector<Candidate>{c});
        };
        const auto& selectionScores=importedSearchState?freshScores:scores;
        const std::size_t initialCount = candidates.size();
        std::set<std::string> currentPendingCandidateIds;
        std::set<std::string> initialIds;
        if(searchScheduling=="encounter-wave") {
            std::vector<Candidate> initial;
            if(importedSearchState) {
                for(std::size_t i=0;i<initialCount;++i)
                    if((currentInputCandidateIds.count(candidates[i].id)||importedPendingCandidateIds.count(candidates[i].id))&&
                       (is_focused(encounter_key(candidates[i].raw))||importedPendingCandidateIds.count(candidates[i].id))) {
                        if(initialIds.insert(candidates[i].id).second) initial.push_back(candidates[i]);
                    }
                std::set<std::string> activeEncounters;
                for(const auto& candidate:candidates) if(is_focused(encounter_key(candidate.raw))) activeEncounters.insert(encounter_key(candidate.raw));
                for(const auto& encounter:activeEncounters) {
                    const Candidate* best=nullptr; long double bestMean=-std::numeric_limits<long double>::infinity();
                    for(const auto& candidate:candidates) if(encounter_key(candidate.raw)==encounter) {
                        const auto it=scores.find(candidate.id);
                        if(it==scores.end()||it->second.outcomesByTrial.empty()) continue;
                        const auto mean=mean_score(it->second);
                        if(!best||mean>bestMean||(mean==bestMean&&candidate.id<best->id)){best=&candidate;bestMean=mean;}
                    }
                    if(!best) for(const auto& candidate:candidates) if(encounter_key(candidate.raw)==encounter&&(!best||candidate.id<best->id)) best=&candidate;
                    if(best&&initialIds.insert(best->id).second) initial.push_back(*best);
                }
                // A pending child can only be judged against its actual parent on the same
                // current seed bank. Rehydrate that parent into this cohort even when it is
                // no longer the encounter's historical mean leader or a current raw input.
                std::set<std::string> pendingParents, pendingEncounters;
                for(const auto& candidate:candidates) if(importedPendingCandidateIds.count(candidate.id)) {
                    pendingEncounters.insert(encounter_key(candidate.raw));
                    if(!candidate.parent.empty()) pendingParents.insert(candidate.parent);
                }
                for(const auto& candidate:candidates) if(pendingParents.count(candidate.id)&&
                    (is_focused(encounter_key(candidate.raw))||pendingEncounters.count(encounter_key(candidate.raw)))) {
                    if(initialIds.insert(candidate.id).second) initial.push_back(candidate);
                }
            } else for(std::size_t i=0;i<initialCount;++i)
                if(is_focused(encounter_key(candidates[i].raw))&&initialIds.insert(candidates[i].id).second) initial.push_back(candidates[i]);
            if(importedSearchState) for(const auto& candidate:initial) {
                const auto prior=scores.find(candidate.id);
                if(prior==scores.end()) continue;
                for(std::uint64_t si=0;si<seedCount;++si) {
                    const auto seed=seedPairs.empty()?seedStart+static_cast<std::int64_t>(si):seedPairs[static_cast<std::size_t>(si)][0].get<std::int64_t>();
                    Json values=Json::array();
                    if(seedPairs.empty()) for(std::size_t pi=0;pi<seedPointers.size();++pi) values.push_back(seed);
                    else values=seedPairs[static_cast<std::size_t>(si)];
                    Json scenario=candidate.raw;
                    for(std::size_t pi=0;pi<seedPointers.size();++pi) scenario[Json::json_pointer(seedPointers[pi].get<std::string>())]=values[pi];
                    const auto trial=identity(scenario);
                    const auto old=prior->second.outcomesByTrial.find(trial);
                    if(old!=prior->second.outcomesByTrial.end()) {
                        freshScores[candidate.id].outcomesByTrial[trial]=old->second;
                        const auto basis=prior->second.basisByTrial.find(trial);
                        if(basis!=prior->second.basisByTrial.end()) freshScores[candidate.id].basisByTrial[trial]=basis->second;
                    }
                }
            }
            for(const auto& candidate:initial) currentPendingCandidateIds.insert(candidate.id);
            if(evaluate_cohort(initial)) currentPendingCandidateIds.clear();
        } else {
            for (std::size_t i = 0; i < initialCount&&!stopRequested.load(); ++i) {
                if(importedSearchState&&importedCandidateIds.count(candidates[i].id)&&!importedPendingCandidateIds.count(candidates[i].id)&&!currentInputCandidateIds.count(candidates[i].id)) continue;
                if(is_focused(encounter_key(candidates[i].raw))||importedPendingCandidateIds.count(candidates[i].id)) {
                    currentPendingCandidateIds.insert(candidates[i].id);
                    if(evaluate_candidate(candidates[i])) currentPendingCandidateIds.erase(candidates[i].id);
                }
            }
        }
        const auto arm_key = [&](const std::string& encounter,const Json& spec) {
            const auto signature=spec.dump();
            return adaptiveScope=="global"?signature:(encounter+"|"+signature);
        };
        std::map<std::string,ArmStats> mutationArms;
        if(importedSearchState) mutationArms=importedMutationArms;
        // Exact raw-intent keys avoid relying on a candidate SHA alone for cache identity.
        // Cap retained trial IDs so unusually large seed banks/candidate pools fall back to
        // direct exact computation without imposing a search-size limit.
        std::map<std::string,std::vector<std::string>> trialIdBankCache;
        std::uint64_t cachedTrialIdCount=0;
        constexpr std::uint64_t maxCachedTrialIds=262144;
        const auto compute_trial_id_for_seed = [&](const Json& raw,std::uint64_t seedIndex) {
            if(seedIndex>=seedCount) throw std::runtime_error("seed index is outside the configured bank");
            Json scenario=raw;
            Json values=Json::array();
            if(seedPairs.empty()) {
                const auto seed=seedStart+static_cast<std::int64_t>(seedIndex);
                for(std::size_t pi=0;pi<seedPointers.size();++pi) values.push_back(seed);
            } else values=seedPairs.at(static_cast<std::size_t>(seedIndex));
            for(std::size_t pi=0;pi<seedPointers.size();++pi) {
                Json& value=scenario[Json::json_pointer(seedPointers[pi].get<std::string>())];
                if(!value.is_number_integer()) throw std::runtime_error("seed pointer must target an existing integer field");
                value=values.at(pi);
            }
            return identity(scenario);
        };
        const auto cached_trial_bank_for_raw = [&](const Json& raw)->const std::vector<std::string>* {
            const auto rawIntentKey=canonical(raw);
            const auto cached=trialIdBankCache.find(rawIntentKey);
            if(cached!=trialIdBankCache.end()) return &cached->second;
            if(seedCount<=maxCachedTrialIds-cachedTrialIdCount) {
                std::vector<std::string> bank;
                bank.reserve(static_cast<std::size_t>(seedCount));
                for(std::uint64_t si=0;si<seedCount;++si) bank.push_back(compute_trial_id_for_seed(raw,si));
                cachedTrialIdCount+=seedCount;
                const auto inserted=trialIdBankCache.emplace(rawIntentKey,std::move(bank)).first;
                return &inserted->second;
            }
            return nullptr;
        };
        const auto has_complete_seed_bank = [&](const Json& raw,const ScoreStats& stats) {
            (void)raw;
            return stats.outcomesByTrial.size()==seedCount;
        };
        const auto compare_seed_bank = [&](const Candidate& child,const Candidate& parent) {
            PairedJudgment judgment;
            const auto childStats=selectionScores.find(child.id);
            const auto parentStats=selectionScores.find(parent.id);
            const auto* childTrialBank=cached_trial_bank_for_raw(child.raw);
            const auto* parentTrialBank=cached_trial_bank_for_raw(parent.raw);
            for(std::uint64_t si=0;si<seedCount;++si) {
                const auto childTrial=childTrialBank?childTrialBank->at(static_cast<std::size_t>(si)):
                    compute_trial_id_for_seed(child.raw,si);
                const auto parentTrial=parentTrialBank?parentTrialBank->at(static_cast<std::size_t>(si)):
                    compute_trial_id_for_seed(parent.raw,si);
                const auto ci=childStats==selectionScores.end()?std::map<std::string,double>::const_iterator{}:
                    childStats->second.outcomesByTrial.find(childTrial);
                const auto pi=parentStats==selectionScores.end()?std::map<std::string,double>::const_iterator{}:
                    parentStats->second.outcomesByTrial.find(parentTrial);
                const bool hasChild=childStats!=selectionScores.end()&&ci!=childStats->second.outcomesByTrial.end();
                const bool hasParent=parentStats!=selectionScores.end()&&pi!=parentStats->second.outcomesByTrial.end();
                if(!hasChild) ++judgment.missingChildPairs;
                if(!hasParent) ++judgment.missingParentPairs;
                if(hasChild&&hasParent) {
                    judgment.deltaSum+=static_cast<long double>(ci->second)-static_cast<long double>(pi->second);
                    ++judgment.matchedSamples;
                }
            }
            judgment.complete=judgment.matchedSamples==seedCount&&judgment.missingChildPairs==0&&judgment.missingParentPairs==0;
            if(judgment.matchedSamples)
                judgment.meanDelta=static_cast<double>(judgment.deltaSum/static_cast<long double>(judgment.matchedSamples));
            return judgment;
        };
        const auto record_arm_judgment = [&](const Candidate& child,const Candidate& parent,const Json& spec) {
            if(searchProfile!="adaptive-native") return;
            const auto encounter=encounter_key(child.raw);
            auto& arm=mutationArms[arm_key(encounter,spec)];
            const auto judgment=compare_seed_bank(child,parent);
            const bool alreadyCounted=arm.lastChildId==child.id&&arm.lastJudgmentComplete;
            arm.lastChildId=child.id;
            arm.lastParentId=parent.id;
            arm.lastMatchedSamples=judgment.matchedSamples;
            arm.lastMissingChildPairs=judgment.missingChildPairs;
            arm.lastMissingParentPairs=judgment.missingParentPairs;
            arm.lastMeanPairedDelta=judgment.meanDelta;
            arm.lastJudgmentComplete=judgment.complete;
            if(judgment.complete&&!alreadyCounted) {
                ++arm.tries;
                if(judgment.meanDelta&&*judgment.meanDelta>0) ++arm.improvements;
            }
        };
        if(!importedSearchState&&searchProfile=="adaptive-native") for(const auto& candidate:candidates) {
            if(candidate.generation==0||candidate.operation=="seed"||candidate.operation=="resumed") continue;
            const auto parent=std::find_if(candidates.begin(),candidates.end(),[&](const Candidate& item){return item.id==candidate.parent;});
            if(parent==candidates.end()) continue;
            try { record_arm_judgment(candidate,*parent,Json::parse(candidate.operation)); } catch(...) {}
        }
        if(importedSearchState&&searchProfile=="adaptive-native") for(const auto& candidate:candidates) {
            if(!importedPendingCandidateIds.count(candidate.id)||candidate.generation==0) continue;
            const auto parent=std::find_if(candidates.begin(),candidates.end(),[&](const Candidate& item){return item.id==candidate.parent;});
            if(parent==candidates.end()) continue;
            try { record_arm_judgment(candidate,*parent,Json::parse(candidate.operation)); } catch(...) {}
        }

        const auto export_search_state = [&]() {
            if(searchStateOutputPath.empty()) return;
            Json candidatePool=Json::array();
            for(const auto& candidate:candidates) candidatePool.push_back(Json{{"raw",candidate.raw},{"id",candidate.id},
                {"parent",candidate.parent},{"generation",candidate.generation},{"operation",candidate.operation},
                {"sourceBinding",candidate.sourceBinding}});
            Json scoreOutcomes=Json::array();
            for(const auto& [strategy,stats]:scores) for(const auto& [trial,earned]:stats.outcomesByTrial)
                scoreOutcomes.push_back(Json{{"strategyId",strategy},{"trialId",trial},{"earned",earned},
                    {"basis",stats.basisByTrial.count(trial)?stats.basisByTrial.at(trial):std::string("unresolved")}});
            Json completedSignatures=Json::array();
            std::vector<std::string> orderedSignatures(learningCompleted.begin(),learningCompleted.end());
            std::sort(orderedSignatures.begin(),orderedSignatures.end());
            for(const auto& signature:orderedSignatures) completedSignatures.push_back(signature);
            Json arms=Json::object();
            for(const auto& [key,arm]:mutationArms) arms[key]=Json{{"tries",arm.tries},{"improvements",arm.improvements},
                {"judgmentModel","matched-configured-seedpairs-v1"},{"lastMatchedSamples",arm.lastMatchedSamples},
                {"lastMissingChildPairs",arm.lastMissingChildPairs},{"lastMissingParentPairs",arm.lastMissingParentPairs},
                {"lastChildId",arm.lastChildId},{"lastParentId",arm.lastParentId},
                {"lastMeanPairedDelta",arm.lastMeanPairedDelta?Json(*arm.lastMeanPairedDelta):Json(nullptr)},
                {"lastJudgmentComplete",arm.lastJudgmentComplete}};
            Json pendingIds=Json::array();
            {
                std::set<std::string> pending;
                for(const auto& id:currentPendingCandidateIds) {
                    const auto candidateIt=std::find_if(candidates.begin(),candidates.end(),[&](const Candidate& c){return c.id==id;});
                    if(candidateIt==candidates.end()) continue;
                    const auto& candidate=*candidateIt;
                    for(std::uint64_t si=0;si<seedCount;++si) {
                    const auto seed=seedPairs.empty()?seedStart+static_cast<std::int64_t>(si):seedPairs[static_cast<std::size_t>(si)][0].get<std::int64_t>();
                    Json values=Json::array();
                    if(seedPairs.empty()) for(std::size_t pi=0;pi<seedPointers.size();++pi) values.push_back(seed);
                    else values=seedPairs[static_cast<std::size_t>(si)];
                    Json scenario=candidate.raw;
                    for(std::size_t pi=0;pi<seedPointers.size();++pi) scenario[Json::json_pointer(seedPointers[pi].get<std::string>())]=values[pi];
                    if(!learningCompleted.count(identity(scenario))) { pending.insert(candidate.id); break; }
                    }
                }
                for(const auto& id:pending) pendingIds.push_back(id);
            }
            Json sourceJournals=importedSourceJournals.is_array()?importedSourceJournals:Json::array();
            std::ifstream journalInput(journalPath,std::ios::binary);
            if(!journalInput) throw std::runtime_error("cannot read result journal for search-state integrity");
            std::string journalBytes((std::istreambuf_iterator<char>(journalInput)),std::istreambuf_iterator<char>());
            const auto journalPathAbsolute=fs::absolute(journalPath).lexically_normal().string();
            Json currentSource{{"path",journalPathAbsolute},{"sha256",sha256_text(journalBytes)},
                {"recordCount",static_cast<std::uint64_t>(std::count(journalBytes.begin(),journalBytes.end(),'\n'))},
                {"provenance",provenance}};
            bool replaced=false;
            for(auto& source:sourceJournals) if(source.is_object()&&source.value("path",std::string{})==journalPathAbsolute) { source=currentSource; replaced=true; }
            if(!replaced) sourceJournals.push_back(std::move(currentSource));
            Json pendingSeedPolicy=nullptr;
            Json pendingSeedPairs=nullptr;
            if(!pendingIds.empty()) pendingSeedPolicy=Json{{"seedPairs",seedPairs},{"seedStart",seedStart},
                {"seedCount",seedCount},{"seedPointers",seedPointers}};
            if(!pendingIds.empty()) {
                pendingSeedPairs=seedPairs;
                if(seedPairs.empty()) {
                    pendingSeedPairs=Json::array();
                    for(std::uint64_t si=0;si<seedCount;++si) {
                        const auto seed=seedStart+static_cast<std::int64_t>(si);
                        pendingSeedPairs.push_back(Json::array({seed,seed}));
                    }
                }
            }
            Json state{{"schema","kaopt-search-state-1"},{"provenance",provenance},{"learningPolicy",learningPolicy},
                {"candidatePool",candidatePool},{"scoreOutcomes",scoreOutcomes},
                {"completedTrialSignatures",completedSignatures},{"lastGenerationByEncounter",lastGenerationByEncounter},
                {"mutationArms",arms},{"sourceJournals",sourceJournals},
                {"pendingCandidateIds",pendingIds},{"pendingSeedPolicy",pendingSeedPolicy},
                {"pendingSeedPairs",pendingSeedPairs},
                {"updatedUnixMs",std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::system_clock::now().time_since_epoch()).count()}};
            const fs::path statePath(searchStateOutputPath);
            if(!statePath.parent_path().empty()) fs::create_directories(statePath.parent_path());
            const fs::path temp=statePath.string()+".tmp";
            { std::ofstream output(temp,std::ios::binary|std::ios::trunc); if(!output) throw std::runtime_error("cannot create search state temporary file"); output<<state.dump(2)<<'\n'; output.flush(); if(!output) throw std::runtime_error("cannot flush search state temporary file"); }
            commit_json_file(temp,statePath);
        };
        // Independent survivor pools ensure one encounter cannot starve another.
        std::set<std::string> encounterIds;
        for (const auto& candidate : candidates) encounterIds.insert(encounter_key(candidate.raw));
        const std::uint64_t waveOrdinalBase=seedPairs.empty()?static_cast<std::uint64_t>(seedStart):
            std::stoull(sha256_text(canonical(seedPairs)).substr(0,16),nullptr,16);
        for (std::uint64_t generationOffset = 1; generationOffset <= generations && !mutations.empty() &&
             !stopRequested.load() && !pauseRequested.load(); ++generationOffset) {
            std::vector<Candidate> generationChildren;
            for (const auto& encounter : encounterIds) {
                if(stopRequested.load()||pauseRequested.load()) break;
                if(!is_focused(encounter)) continue;
                if(!importedSearchState&&lastGenerationByEncounter[encounter]>=generationOffset) continue;
                if(importedSearchState&&lastGenerationByEncounter[encounter]==std::numeric_limits<std::uint64_t>::max()) continue;
                const std::uint64_t gen=importedSearchState?lastGenerationByEncounter[encounter]+1:generationOffset;
                const auto proposalOrdinal=waveOrdinalBase+gen-1;
                const Candidate* parent = nullptr;
                long double bestMean = -std::numeric_limits<long double>::infinity();
                std::vector<const Candidate*> population;
                for (const auto& candidate : candidates) {
                    if (encounter_key(candidate.raw) != encounter) continue;
                    const auto it = selectionScores.find(candidate.id);
                    if (it == selectionScores.end() || it->second.outcomesByTrial.empty()) {
                        if(!importedSearchState) population.push_back(&candidate);
                        continue;
                    }
                    if(!has_complete_seed_bank(candidate.raw,it->second)) continue;
                    population.push_back(&candidate);
                    const long double mean = mean_score(it->second);
                    if (!parent || mean > bestMean || (mean == bestMean && candidate.id < parent->id)) {
                        parent = &candidate; bestMean = mean;
                    }
                }
                bool unscoredParent=parent==nullptr;
                    if(!parent)for(const auto& candidate:candidates)if(encounter_key(candidate.raw)==encounter&&(!parent||candidate.id<parent->id))parent=&candidate;
                if(!parent)throw std::runtime_error("encounter has no source candidate for exploration");
                const bool exploreParent=searchProfile=="adaptive-native"&&(proposalOrdinal%5==4)&&!population.empty();
                if(exploreParent) {
                    const auto drawSeed=std::stoull(sha256_text(canonical(Json{{"ordinal",proposalOrdinal},{"encounter",encounter},{"seedPairs",seedPairs}})).substr(0,16),nullptr,16);
                    std::mt19937_64 explorationRng(drawSeed);
                    parent=population[static_cast<std::size_t>(explorationRng()%population.size())];
                }
                std::size_t selectedSpec=static_cast<std::size_t>((gen-1)%mutations.size());
                if(searchProfile=="adaptive-native") {
                    long double bestArm=-std::numeric_limits<long double>::infinity();
                    const auto total=std::max<std::uint64_t>(1,proposalOrdinal+1);
                    const auto tieStart=static_cast<std::size_t>(proposalOrdinal%mutations.size());
                    for(std::size_t n=0;n<mutations.size();++n) {
                        const auto mi=(tieStart+n)%mutations.size();
                        const auto key=arm_key(encounter,mutations[mi]); const auto& arm=mutationArms[key];
                        const long double ucb=arm.tries==0?std::numeric_limits<long double>::infinity():
                            static_cast<long double>(arm.improvements)/arm.tries+std::sqrt(2.0L*std::log(static_cast<long double>(total+1))/arm.tries);
                        if(ucb>bestArm){bestArm=ucb;selectedSpec=mi;}
                    }
                    // If the leading arm is illegal for this parent or already exhausted, walk the
                    // fixed mutation list once. Nothing is clamped and invalid attempts stay diagnostic.
                    bool feasible=false;
                    for(std::size_t offset=0;offset<mutations.size();++offset) {
                        const auto index=(selectedSpec+offset)%mutations.size();
                        try { const auto test=mutate(parent->raw,mutations[index],gen-1); if(identity(test)==parent->id||seen.count(identity(test))) continue;
                            if(mutations[index].value("op",std::string{})=="stat-step") {
                                Json admittedTest;
                                const auto admissionStart=stageTimingTelemetry?std::chrono::steady_clock::now():std::chrono::steady_clock::time_point{};
                                try { admittedTest=admit(test,tables); }
                                catch(...) {
                                    if(stageTimingTelemetry) admissionWallNs.fetch_add(static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
                                        std::chrono::steady_clock::now()-admissionStart).count()),std::memory_order_relaxed);
                                    throw;
                                }
                                if(stageTimingTelemetry) admissionWallNs.fetch_add(static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
                                    std::chrono::steady_clock::now()-admissionStart).count()),std::memory_order_relaxed);
                                if(has_error(admittedTest)) continue;
                                Json preparedTest;
                                const auto preparationStart=stageTimingTelemetry?std::chrono::steady_clock::now():std::chrono::steady_clock::time_point{};
                                try { preparedTest=prepare(admittedTest,tables,false,&test); }
                                catch(...) {
                                    if(stageTimingTelemetry) preparationWallNs.fetch_add(static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
                                        std::chrono::steady_clock::now()-preparationStart).count()),std::memory_order_relaxed);
                                    throw;
                                }
                                if(stageTimingTelemetry) preparationWallNs.fetch_add(static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
                                    std::chrono::steady_clock::now()-preparationStart).count()),std::memory_order_relaxed);
                                if(has_error(preparedTest)) continue;
                                enforce_generated_stat_bound(mutations[index],preparedTest);
                            }
                            selectedSpec=index;feasible=true;break; }
                        catch(...) {}
                    }
                    if(!feasible) { lastGenerationByEncounter[encounter]=gen; continue; }
                }
                const Json& spec=mutations[selectedSpec];
                Json raw;
                try{raw=mutate(parent->raw,spec,gen-1);}catch(const std::exception& e){++invalidTrials;lastGenerationByEncounter[encounter]=gen;errorsJournal.append(Json{{"schema","kaopt-diagnostic-1"},{"stage","mutation"},{"parentCandidateId",parent->id},{"rawScenario",parent->raw},{"attemptedMutation",spec},{"encounterId",encounter},{"generation",gen},{"reason",e.what()},{"provenance",provenance}}.dump()+"\n");continue;}
                if (encounter_key(raw) != encounter) throw std::runtime_error("mutation changed encounter membership");
                Candidate child{std::move(raw), "", parent->id, gen, searchProfile=="adaptive-native"?spec.dump():(unscoredParent?"mutation-of-unscored-parent":"mutation-of-encounter-highest-mean-earned"),parent->sourceBinding};
                const std::string parentId=parent->id;
                child.id = identity(child.raw);
                lastGenerationByEncounter[encounter] = gen;
                if (!seen.insert(child.id).second) continue;
                if (candidates.size() >= 100000) throw std::runtime_error("generated candidate limit is 100000");
                candidates.push_back(child); // Keep the measured encounter pool for elitist selection.
                { std::lock_guard<std::mutex> lock(workMutex); ++workByEncounter[encounter].candidates; }
                statusCandidateCount.fetch_add(1,std::memory_order_relaxed);
                currentPendingCandidateIds.insert(child.id);
                if(searchScheduling=="legacy-sequential") {
                    if(!evaluate_candidate(candidates.back())) break;
                    currentPendingCandidateIds.erase(child.id);
                    if(searchProfile=="adaptive-native") {
                        const auto parentIt=std::find_if(candidates.begin(),candidates.end(),[&](const Candidate& item){return item.id==parentId;});
                        if(parentIt!=candidates.end()) record_arm_judgment(candidates.back(),*parentIt,spec);
                    }
                } else generationChildren.push_back(child);
            }
            if(searchScheduling=="encounter-wave"&&!generationChildren.empty()&&
               !stopRequested.load()&&!pauseRequested.load()) {
                const bool generationComplete=evaluate_cohort(generationChildren);
                if(generationComplete) for(const auto& child:generationChildren) currentPendingCandidateIds.erase(child.id);
                if(searchProfile=="adaptive-native") for(const auto& child:generationChildren) {
                    const auto parentIt=std::find_if(candidates.begin(),candidates.end(),[&](const Candidate& item){return item.id==child.parent;});
                    if(parentIt==candidates.end()) continue;
                    try { record_arm_judgment(child,*parentIt,Json::parse(child.operation)); } catch(...) {}
                }
            }
        }
        { Json pending=Json::array(); for(const auto& id:currentPendingCandidateIds) pending.push_back(id);
          Json cp{{"schema","kaopt-checkpoint-1"},{"journalRecords",completed.size()},
                    {"lastCandidateId",candidates.empty()?std::string():candidates.back().id},
                    {"lastGenerationByEncounter",lastGenerationByEncounter},
                    {"pendingCandidateIds",pending},{"searchStatePath",searchStateOutputPath},
                    {"searchPolicy",runPolicy},{"provenance",provenance}};
          const fs::path tmp = checkpointPath.string() + ".tmp";
          { std::ofstream cf(tmp, std::ios::binary | std::ios::trunc); if (!cf) throw std::runtime_error("cannot write checkpoint"); cf << cp.dump(2) << '\n'; cf.flush(); if (!cf) throw std::runtime_error("checkpoint flush failed"); }
          commit_json_file(tmp,checkpointPath);
        }
        { std::lock_guard<std::mutex> g(queue.m); queue.stop = true; } queue.cv.notify_all();
        for (auto& t : pool) t.join();
        joinGuard.joined = true;
        export_search_state();
        monitorGuard.join();
        export_leaders();
        std::cerr << "kaopt summary: saved=" << saved << " invalid=" << invalidTrials.load()
                  << " execution_errors=" << executionErrors.load() << " successful=" << successfulTrials.load()
                  << " scored=" << scoredTrials.load() << '\n';
        const bool clean = executionErrors.load() == 0 && saved>0;
        const std::string stopReason=stopRequested.load()?"control-stop":(pauseRequested.load()?"control-pause":(!currentPendingCandidateIds.empty()?"focus-deferred":"completed"));
        Json finalStatus{{"schema","kaopt-status-1"},{"state",stopRequested.load()?"stopped":(pauseRequested.load()?"paused":(clean?"complete":"completed-with-errors"))},
            {"stage","finished"},{"savedRecords",saved},{"invalidCandidates",invalidTrials.load()},
            {"freshSavedRecords",saved-restoredRecords},{"restoredRecords",restoredRecords},{"duplicateSkips",duplicateSkips},
            {"completedTrials",durableCompletedTrials.load()},{"drainedJobsThisRun",drainedJobsThisRun.load()},
            {"executionErrors",executionErrors.load()},{"successfulOutcomes",successfulTrials.load()},{"persistPreparedSnapshot",persistPreparedSnapshot},
            {"scoredOutcomes",scoredTrials.load()},{"candidateCount",candidates.size()},
            {"lastGenerationByEncounter",lastGenerationByEncounter},
            {"startupMs",startupMs},{"totalElapsedMs",std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-wallStart).count()},
            {"elapsedWallNs",static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now()-wallStart).count())},
            {"stopReason",stopReason},{"pendingCandidateCount",currentPendingCandidateIds.size()},
            {"resultJournal",journalPath.string()},{"diagnosticJournal",errorsPath.string()},
            {"searchStatePath",searchStateOutputPath}};
        const Json work=make_work_telemetry(); for(auto it=work.begin();it!=work.end();++it) finalStatus[it.key()]=it.value();
        if (stageTimingTelemetry) finalStatus["timingTelemetry"] = make_timing_telemetry();
        write_status(outDir, finalStatus);
        return (clean||stopRequested.load()||pauseRequested.load()) ? 0 : 3;
    } catch (const std::exception& e) {
        std::cerr << "kaopt pipeline: " << e.what() << '\n';
        try {
            if (outputLock && config.is_object() && config.contains("outputDir") && config["outputDir"].is_string()) {
                const fs::path out(config["outputDir"].get<std::string>());
                if (fs::exists(out)) write_status(out, Json{{"schema","kaopt-status-1"},{"state","failed"},
                    {"stage","pipeline"},{"error",e.what()},
                    {"totalElapsedMs",std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-wallStart).count()}});
            }
        } catch (...) {}
        return 2;
    }
}
} // namespace kaopt
