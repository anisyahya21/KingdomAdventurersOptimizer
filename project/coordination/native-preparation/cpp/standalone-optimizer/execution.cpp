#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include "native_abi.hpp"
#include "full_battle_abi/ka_battle_report.hpp"
#include "preparation.hpp"
#include "encounter_report.hpp"
#include "replay.hpp"
#include <algorithm>
#include <memory>
#include <filesystem>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>
#include <cstring>
#include <chrono>
namespace kaopt {
namespace {
struct Library {
 HMODULE module;
 explicit Library(const std::string& p):module(LoadLibraryW(std::filesystem::path(p).c_str())) { if(!module) throw std::runtime_error("kernel load failed"); }
 ~Library(){FreeLibrary(module);}
 template<class R=void,class... A> R call(const char* name,A... args){auto f=GetProcAddress(module,name);if(!f)throw std::runtime_error(std::string("missing export ")+name);return reinterpret_cast<R(__cdecl*)(A...)>(f)(args...);}
};
void board(KaBoard& b,const Json& j){if(j.size()>48)throw std::runtime_error("board overflow");std::map<int,std::int64_t> ordered;for(auto i=j.begin();i!=j.end();++i)ordered.emplace(std::stoi(i.key()),i.value().get<std::int64_t>());for(auto [k,v]:ordered){auto n=b.len++;b.entries[n].key=k;b.entries[n].value=v;if(k>=0&&k<128)b.positions[k]=static_cast<std::uint8_t>(n+1);}}
template<std::size_t N> void integers(std::int32_t (&a)[N],const Json& j){if(j.size()!=N)throw std::runtime_error("component vector size mismatch");for(std::size_t i=0;i<N;++i)a[i]=j[i].get<std::int32_t>();}
void fill_unit(KaUnit& u,const Json& r){std::memset(&u,0,sizeof u);u.present=r.at("present");u.team=r.at("team");u.human=r.at("human").get<bool>();u.monster=r.at("monster").get<bool>();u.flags=r.at("flags");u.id=r.at("id");u.identity=r.at("identity");auto& e=u.body;e.id=u.identity;e.destroyed=r.at("destroyed").get<bool>();e.parent=-1;for(auto s:r.at("present_slots")){int n=s;if(n<0||n>=52)throw std::runtime_error("component slot overflow");e.has[n]=1;}auto& c=r.at("components");
 auto v=[](const Json& a){return KaVec3{a[0].get<float>(),a[1].get<float>(),a[2].get<float>()};};
 if(!c.at("position").is_null()){auto& a=c.at("position");e.position=v(a);e.offset={a[3],a[4],a[5]};e.parent=a[6].is_null()?-1:a[6].get<int>();}
 if(!c.at("speed").is_null())e.speed=v(c.at("speed"));
 if(!c.at("seb").is_null())integers(e.seb,c.at("seb"));if(!c.at("depth").is_null())integers(e.depth,c.at("depth"));if(!c.at("cell").is_null())integers(e.cell,c.at("cell"));
 std::fill(std::begin(e.image),std::end(e.image),-1);e.animation[0]=1;e.animation[1]=-1;
 if(!c.at("image").is_null())integers(e.image,c.at("image"));if(!c.at("animation").is_null())integers(e.animation,c.at("animation"));if(!c.at("direction").is_null())e.direction=c.at("direction");
 for(auto key:{"modifier","effect","projectile"})if(!c.at(key).is_null())throw std::runtime_error("initial effect/modifier/projectile unsupported by C++ codec");
 if(!c.at("attack").is_null())e.attack=c.at("attack");if(!c.at("garbage").is_null())e.garbage=c.at("garbage");
 auto& w=r.at("weapon");u.weapon_type=w.at("type");u.weapon_shooting_range=w.at("shootingRange");u.weapon_motion=w.at("motion");u.weapon_projectile_flag=w.at("projectileFlag");u.boss=r.at("boss").get<bool>();u.monster_type=r.at("monsterType");u.special_human=r.at("specialHuman").get<bool>();u.monster_size=r.at("monsterSize");u.human_flag=r.at("human_flag");board(u.board,r.at("board"));board(u.long_board,r.at("long_board"));
 if(r.at("parameters").size()>16||r.at("equipment").size()>8)throw std::runtime_error("parameter/equipment overflow");
 std::map<int,Json> parameters;for(auto i=r.at("parameters").begin();i!=r.at("parameters").end();++i)parameters.emplace(std::stoi(i.key()),i.value());for(auto& [id,p]:parameters)u.params.rows[u.params.count++]={id,p.at("rawValue"),p.at("extraValue"),p.at("rawMax"),p.at("extraMax"),p.at("trainingLevel")};
 for(auto& p:r.at("equipment")){auto& t=u.params.equipment[u.params.equipment_count++];t.level=p.at("level");t.pvp_level=p.at("pvpLevel");t.affinity=p.at("affinity");auto& a=p.at("parameters");if(a.size()>16)throw std::runtime_error("equipment pair overflow");t.pair_count=static_cast<int>(a.size());for(std::size_t i=0;i<a.size();++i)if(!a[i].is_null()){t.present[i]=1;integers(t.pairs[i],a[i]);}}
 auto list=[](auto& a,std::uint32_t& count,const Json& j){if(j.size()>std::size(a))throw std::runtime_error("skill overflow");for(auto n:j)a[count++]=n.template get<int>();};list(u.skill_ids,u.skill_count,r.at("skills"));list(u.levels,u.level_count,r.at("levels"));
 const auto& invoking=r.at("invoking");if(invoking.size()>std::size(u.invoking))throw std::runtime_error("invoking skill capacity exceeded");
 for(const auto& pair:invoking){if(!pair.is_array()||pair.size()!=2)throw std::runtime_error("invoking entries must be [skill,remaining] pairs");u.invoking[u.invoking_count++]={pair[0].get<std::int32_t>(),pair[1].get<std::int32_t>()};}
 const auto& commands=r.at("commands");if(commands.size()>std::size(u.commands))throw std::runtime_error("command capacity exceeded");
 for(const auto& command:commands){auto& target=u.commands[u.command_count++];target.opcode=command.at("opcode").get<std::int32_t>();target.target=command.at("target").get<std::int32_t>();target.skill=command.at("skill").get<std::int32_t>();target.tick=command.at("tick").get<std::int32_t>();target.duration=command.at("duration").get<std::int32_t>();target.use_index=command.at("useIndex").get<std::int32_t>();}
 const auto& path=r.at("path");if(path.size()>std::size(u.path))throw std::runtime_error("path capacity exceeded");
 for(const auto& point:path){if(!point.is_array()||point.size()!=2)throw std::runtime_error("path points must be integer pairs");u.path[u.path_count++]={point[0].get<std::int32_t>(),point[1].get<std::int32_t>()};}
}
const char* phase_name(std::int32_t phase) {
 switch(phase){case 0:return "before_fighters";case 1:return "after_fighters";default:return nullptr;}
}
Json compact_mp_metrics(const Json& prepared,const KaBattleReport& report) {
 if(report.mp_watch_count<0||report.mp_watch_count>2)throw std::runtime_error("MP watch report count outside ABI capacity");
 const auto& raw=prepared.at("rawIntent");
 Json names=raw.value("mpWatchUnits",Json::array());
 if(!names.is_array())names=Json::array();
 if(names.empty()){names=raw.value("holyHerbTriggerUnits",Json::array());if(!names.is_array())names=Json::array();}
 const auto& snapshot=prepared.at("snapshot");
 const auto& configured=snapshot.at("consumables").at("mp_watch");
 if(!configured.is_array())throw std::runtime_error("prepared MP watch identities are not an array");
 std::map<std::int32_t,std::string> name_by_identity;
 const auto& own=prepared.value("ownUnits",Json::array());
 if(own.is_array())for(std::size_t i=0;i<own.size();++i)
  if(own[i].contains("name")&&own[i].at("name").is_string())name_by_identity.emplace(100+static_cast<std::int32_t>(i),own[i].at("name").get<std::string>());
 const auto& enemies=prepared.value("enemies",Json::array());
 if(enemies.is_array())for(const auto& enemy:enemies)if(enemy.contains("incomingIndex")&&enemy.contains("monsterId")){
  const auto index=enemy.at("incomingIndex").get<std::int32_t>();
  const auto monster=enemy.at("monsterId").get<std::int32_t>();
  name_by_identity.emplace(100+static_cast<std::int32_t>(own.size())+index,
   "enemy:"+std::to_string(index)+":"+std::to_string(monster));
 }
 std::string name_warning;
 if(configured.size()!=static_cast<std::size_t>(report.mp_watch_count))name_warning="prepared MP watch identity count differs from the native report; names are resolved by reported identities where possible";
 Json result=Json::array();
 for(std::int32_t i=0;i<report.mp_watch_count;++i){
  const char* phase=phase_name(report.mp_first_low_phase[i]);
  const auto identity=report.mp_identity[i];
  Json name=nullptr;
  const bool same_prepared_watch=static_cast<std::size_t>(i)<configured.size()&&
   configured[static_cast<std::size_t>(i)].is_number_integer()&&
   configured[static_cast<std::size_t>(i)].get<std::int32_t>()==identity;
  if(same_prepared_watch&&names.size()==static_cast<std::size_t>(report.mp_watch_count)&&names[static_cast<std::size_t>(i)].is_string())
   name=names[static_cast<std::size_t>(i)];
  else if(const auto it=name_by_identity.find(identity);it!=name_by_identity.end())name=it->second;
  else if(name_warning.empty())name_warning="one or more native MP watch identities have no resolved unit name";
  result.push_back(Json{{"name",std::move(name)},{"identity",identity},
   {"minimumMp",report.mp_min[i]},{"minimumMpPercent",report.mp_min_percent[i]},
   {"reachedLowMp",report.mp_low[i]!=0},{"firstLowMpTick",report.mp_first_low_tick[i]},
   {"firstLowMpPhase",phase?Json(phase):Json(nullptr)},{"reachedZero",report.mp_zero[i]!=0}});
 }
 if(!name_warning.empty())for(auto& row:result)row["nameUnavailableReason"]=name_warning;
 return result;
}
Json compact_herb_metrics(const KaBattleReport& report) {
 if(report.herb_log_count<0||report.herb_log_count>16)throw std::runtime_error("Holy Herb log count outside ABI capacity");
 Json uses=Json::array();
 for(std::int32_t i=0;i<report.herb_log_count;++i){
  const char* phase=phase_name(report.herb_use_phase[i]);
  uses.push_back(Json{{"tick",report.herb_use_tick[i]},
   {"phase",phase?Json(phase):Json(nullptr)},
   {"source",report.herb_use_source[i]>=0?Json(report.herb_use_source[i]):Json(nullptr)},
   {"used",report.herb_use_ok[i]!=0}});
 }
 return Json{{"startingStock",report.herb_stock_start},{"remainingStock",report.herb_stock_remaining},
  {"maxUses",report.herb_max_uses},{"useCount",report.herb_use_count},{"uses",std::move(uses)}};
}
Json reward_outcome(const KaBattleReport& report) {
 const bool resolved=report.verdict==1||report.verdict==2;
 const bool certificate_reported=report.certificate_held!=0;
 const bool certificate_preverdict=certificate_reported&&report.certificate_frame>=0&&
  ((resolved&&report.verdict_tick>=0&&report.certificate_frame<report.verdict_tick)||
   (!resolved&&report.ticks>report.certificate_frame));
 if(certificate_reported&&!certificate_preverdict)throw std::runtime_error("native reward certificate is not proven to precede the verdict");
 Json awarded=nullptr;
 std::string basis="unknown-unresolved-battle";
 std::string reason;
 if(!resolved){
  reason="battle unresolved (IsAnnihilated not reached): pending chest count "+std::to_string(report.pending_final)+" is reported and the awarded count is unknown";
 }else if(report.verdict==2){
  awarded=0;basis="native-win-loss-gate";
  reason="safe 0 from the recorded native Finish dispatch gate (special battle flag and winner (battle+0x50) == 1); winner==2 dispatches no chest, independent of the pending chest count ("+std::to_string(report.pending_final)+") and of any certificate. The pending count is NOT an award: reward-entitlement certificate sufficiency is not closed (enemy-Cure source in some supported encounters; C3 clearing unproven)";
 }else if(report.scope_allowed&&certificate_preverdict&&report.post_certificate_delta==0){
  awarded=report.certificate_pending;basis="reward-entitlement-certificate";
  reason="certificate C1..C4 held at frame "+std::to_string(report.certificate_frame)+", strictly before the verdict, in a Cure-free known-row encounter and no prize was queued afterwards; awarded = the pending count at the certificate";
 }else{
  basis="unknown-win-without-certificate";
  if(!report.scope_allowed)reason="win without a proven pre-verdict certificate: certificate scope refused because enemy skill rows are not all known and Cure-free";
  else if(report.post_certificate_delta!=0)reason="win without a proven pre-verdict certificate: a prize was queued after the pre-verdict certificate (delta "+std::to_string(report.post_certificate_delta)+"), so the snapshot no longer covers the final queue";
  else if(report.late_hold_frame>=0)reason="win without a proven pre-verdict certificate: C1..C4 first held only at or after the verdict (frame "+std::to_string(report.late_hold_frame)+", verdict already set); a late certificate is never accepted because the native ordering between the prize producers and EnterEnding is unproven";
  else {
   static const char* clause_names[4]={"C1_bossHpZero","C2_bossLeavingState8","C3_noStoredTarget","C4_noQueuedCommandTarget"};
   std::string failed;
   for(int i=0;i<4;++i)if(!report.clauses[i]){if(!failed.empty())failed+=", ";failed+=clause_names[i];}
   reason="win without a proven pre-verdict certificate: C1..C4 never held before the verdict"+
    (failed.empty()?std::string(" (no boss reading was observed)"):std::string("; failed clauses ")+failed);
  }
 }
 Json result{{"pendingChests",report.pending_final},{"awardedChests",std::move(awarded)},
  {"awardedBasis",basis},{"inventoryVerified",false},{"reason",reason}};
 if(certificate_preverdict){
  result["certificate"]={{"certificateId","ka-reward-entitlement-certificate-1"},{"holds",true},
   {"frame",report.certificate_frame},{"issuedBeforeVerdict",true},
   {"timingRule","issued only while the battle is unresolved (strictly before the verdict); a hold first observed at/after the verdict is never certified"}};
 }
 return result;
}
Json finish_diagnostic(const KaBattleReport& report,const std::string& finish_policy,std::int32_t policy) {
 const bool resolved=report.verdict==1||report.verdict==2;
 const bool truncated=policy==2;
 const char* boundary=!resolved?"censored-unresolved-battle":
  (truncated?"diagnostic-truncated":
   (report.ending_confirmed?"diagnostic-declared-finish; native-auto-producer-unproven":"awaiting-confirmation"));
 Json dispatched=nullptr;
 if(resolved&&truncated)dispatched=report.verdict==1
  ?(report.pending_at_verdict>=0?Json(report.pending_at_verdict):Json(nullptr)):Json(0);
 return Json{{"policy",finish_policy},{"boundary",boundary},{"rewardsTruncated",truncated},
  {"pendingChestsAtVerdict",resolved&&report.pending_at_verdict>=0?Json(report.pending_at_verdict):Json(nullptr)},
  {"dispatchedChests",std::move(dispatched)},{"inventoryVerified",false},
  {"automaticFinishProducerProven",false},
  {"reason",truncated?"on-verdict cuts post-verdict activity and is retained as a diagnostic finish policy":"native automatic Finish producer remains unproven; any declared Finish remains diagnostic"},
  {"automaticFinishReason","native STATE_ENDING 3 exit is the one-frame KEY_SELECT input edge 0x14ef538; no automatic producer of that edge exists in the direct call graph (check_combat_auto_finish_producer.py), so an automatic Ending->Finish is unproven and no dispatched-chest count is a trusted yield"}};
}
std::int32_t run_replay_capture(Library& k,void* battle,std::uint32_t steps,std::int32_t policy,
                               ReplayCapture& capture,KaBattleReport& report) {
 // This is the source loop in native/ka_kernel/src/battle_control.rs::run_battle.
 // `ka_native_full_tick` calls that module's exact `tick`; `ka_battle_report` is
 // its read-only report() projection. Replay mode uses literal per-tick animation
 // resource increments so those counters are observable; the bulk runner's
 // deferred increments are cosmetic and finish to the same modulo frame.
 if(k.call<int>("ka_battle_report",battle,policy,&report)!=0)
  throw std::runtime_error("native report getter failed before replay");
 std::int32_t status=0;
 bool control_changed=false;
 for(std::uint32_t step=0;step<steps;++step){
  status=k.call<int>("ka_native_full_tick",battle);
  if(status!=0)break;
  capture.capture_tick(k.module,battle);
  if(k.call<int>("ka_battle_report",battle,policy,&report)!=0)
   throw std::runtime_error("native report getter failed during replay");
  if(report.battle_state==3){
   if(policy==2){
    report.ending_confirmed=0;
    control_changed=true;
    break;
   }
   if(policy==1&&report.battle_frame>79){
    // Exact battle_control::update_ending(frame, verdict): (80,false) through
    // frame 79, then (frame,true). This branch only runs above frame 79.
    report.ending_counter=report.battle_frame;
    report.ending_gate_tick=report.ticks-1;
    report.ending_confirmed=1;
    control_changed=true;
    break;
   }
  }
 }
 if(policy==0&&report.battle_state==3){
  // at-horizon applies update_ending after all requested ticks.
  report.ending_counter=report.battle_frame<=79?80:report.battle_frame;
  report.ending_confirmed=report.battle_frame>79?1:0;
  if(report.ending_confirmed)report.ending_gate_tick=report.ticks-1;
  control_changed=true;
 }
 if(control_changed){
  // The setter replaces the full control tuple, so carry every unrelated field
  // through from the latest native report, especially the verdict/prize counters.
  k.call("ka_battle_set_control",battle,report.battle_state,report.battle_frame,
   report.verdict,report.verdict_tick,report.ending_counter,report.ending_gate_tick,
   report.ending_confirmed,report.pre_verdict_prize_callbacks,report.prize_callbacks);
 }
 if(k.call<int>("ka_battle_report",battle,policy,&report)!=0)
  throw std::runtime_error("native report getter failed after replay");
 report.status=status;
 return status;
}
}
Json execute(const Json& prepared,const std::string& path,bool timingTelemetry){
 using Timer=std::chrono::steady_clock;
 const auto executeStart=timingTelemetry?Timer::now():Timer::time_point{};
 if(!prepared.value("readyForNativeEngineSnapshot",false))throw std::runtime_error("preparation is not engine-ready");
 const auto& s=prepared.at("snapshot");
 thread_local std::string loaded;thread_local std::unique_ptr<Library> lib;if(loaded!=path){lib=std::make_unique<Library>(path);loaded=path;}auto& k=*lib;
 if(k.call<std::uint32_t>("ka_sizeof_unit")!=sizeof(KaUnit)||k.call<std::uint32_t>("ka_sizeof_report")!=sizeof(KaBattleReport))throw std::runtime_error("native ABI size mismatch");
 if(k.call<std::uint32_t>("ka_sizeof_encounter_report")!=sizeof(KaEncounterReport)||k.call<std::uint32_t>("ka_encounter_version")!=3)throw std::runtime_error("encounter ABI size/version mismatch");
 if(s.at("units").empty()||s.at("units").size()>32)throw std::runtime_error("roster overflow");std::vector<KaUnit> units(s.at("units").size());for(std::size_t i=0;i<units.size();++i)fill_unit(units[i],s.at("units")[i]);
 void* h=k.call<void*>("ka_battle_create");if(!h)throw std::runtime_error("battle allocation failed");struct Guard{Library& k;void* h;~Guard(){try{k.call("ka_battle_free",h);}catch(...){}}}guard{k,h};
 if(k.call<std::uint32_t>("ka_battle_import",h,units.data(),static_cast<std::uint32_t>(units.size()),std::uint64_t(0))!=units.size())throw std::runtime_error("native import incomplete");
 auto& c=s.at("config");k.call("ka_battle_identity",h,c.at("first_identity").get<int>());k.call("ka_battle_config",h,c.at("map_width").get<int>(),c.at("row_offset").get<int>(),std::uint8_t(c.at("movement_enabled").get<bool>()));k.call("ka_battle_set_counters",h,c.at("tick").get<std::uint32_t>(),c.at("next_identity").get<int>(),c.at("next_command").get<int>());
 // nlohmann::json object keys iterate lexicographically ("1", "10", "2").
 // The canonical Python codec inserts both tables in ascending numeric ID order.
 // Preserve that order so KaBattle keeps its sorted-row fast path and the whole-state
 // checksum agrees with snapshots loaded through ka_abi.load_snapshot.
 std::map<int,const Json*> ordered_rows;
 for(auto i=s.at("rows").begin();i!=s.at("rows").end();++i){
  std::size_t used=0;const int key=std::stoi(i.key(),&used);if(used!=i.key().size())throw std::runtime_error("non-numeric skill row key");
  const int id=i.value().at("id").get<int>();if(id!=key||!ordered_rows.emplace(id,&i.value()).second)throw std::runtime_error("skill row key/id mismatch or duplicate");
 }
 for(const auto& entry:ordered_rows){const auto& r=*entry.second;KaSkill row{r.at("id"),r.at("category"),r.at("type"),r.at("flags"),r.at("minMp"),r.at("maxMp"),r.at("requiredEquipType"),r.at("shootingRange"),r.at("range"),r.at("count"),r.at("motion"),r.at("value"),r.at("seb"),r.at("img"),r.at("impactImg"),r.at("impactSeb")};if(!k.call<std::uint32_t>("ka_battle_add_row",h,&row))throw std::runtime_error("row overflow");}
 std::map<int,int> ordered_effect_resources;
 for(auto i=s.at("effect_resources").begin();i!=s.at("effect_resources").end();++i){std::size_t used=0;const int id=std::stoi(i.key(),&used);if(used!=i.key().size()||!ordered_effect_resources.emplace(id,i.value().get<int>()).second)throw std::runtime_error("invalid or duplicate effect resource ID");}
 for(const auto& [id,max_frame]:ordered_effect_resources)if(!k.call<std::uint32_t>("ka_battle_add_effect_resource",h,id,max_frame))throw std::runtime_error("resource overflow");
 auto ints=[](const Json& j){return j.get<std::vector<std::int32_t>>();};auto prizes=ints(s.at("prizes"));k.call("ka_battle_set_prizes",h,prizes.data(),static_cast<std::uint32_t>(prizes.size()));auto bases=ints(s.at("human_bases"));if(!bases.empty())k.call<std::uint32_t>("ka_battle_set_human_bases",h,bases.data(),static_cast<std::uint32_t>(bases.size()));
 if(!s.at("objects").empty()||!s.at("projectile_sources").empty())throw std::runtime_error("initial created objects unsupported");
 for(std::size_t i=0;i<s.at("subsets").size();++i){auto& r=s.at("subsets")[i];std::vector<int> slots;for(auto x:r.at("slots"))slots.push_back(x.is_null()?0:x.get<int>());auto free=r.at("free").get<std::vector<std::uint32_t>>();k.call("ka_battle_set_subset",h,static_cast<std::uint32_t>(i),static_cast<std::uint32_t>(slots.size()),static_cast<std::uint32_t>(free.size()),r.at("version").get<int>(),slots.data(),free.data());}
 for(std::size_t i=0;i<s.at("buckets").size();++i){auto& r=s.at("buckets")[i];auto ids=ints(r[1]);if(!k.call<std::uint32_t>("ka_battle_set_bucket",h,static_cast<std::uint32_t>(i),r[0].get<int>(),static_cast<std::uint32_t>(ids.size()),ids.data()))throw std::runtime_error("bucket overflow");}
 for(auto& r:s.at("animation_resources"))if(!k.call<std::uint32_t>("ka_battle_set_animation_resource",h,r[0].get<int>(),r[1].get<int>(),r[2].get<int>(),r[3].get<int>()))throw std::runtime_error("animation resource overflow");
 std::uint32_t stream=0;for(auto name:{"math","lib"}){auto& r=s.at("rng").at(name);auto values=ints(r.at("values"));if(values.size()!=56)throw std::runtime_error("RNG shape");k.call("ka_battle_set_rng",h,stream++,values.data(),r.at("index").get<int>(),r.at("partner").get<int>(),r.at("draws").get<std::uint32_t>());}
 auto& con=s.at("consumables");if(!con.at("uses").empty())throw std::runtime_error("initial used consumable log unsupported");
 k.call("ka_battle_set_herb_stock",h,con.at("holy_herb_stock").get<int>());auto watch=ints(con.at("mp_watch"));if(watch.size()>2)throw std::runtime_error("MP watch overflow");k.call<std::uint32_t>("ka_battle_set_mp_watch",h,watch.data(),static_cast<std::uint32_t>(watch.size()),con.at("holy_herb_max_uses").get<int>());
 if(con.at("items").size()>8||con.at("inputs").size()>32)throw std::runtime_error("consumable capacity exceeded");
 for(auto& r:con.at("items"))if(!k.call<std::uint32_t>("ka_battle_add_item",h,0,r.at("parameter").get<int>(),std::uint8_t(r.at("all_residents").get<int>()!=0),r.at("bonus_min").get<int>(),r.at("bonus_max").get<int>(),r.at("stock").get<int>()))throw std::runtime_error("item overflow");
 for(auto& r:con.at("inputs"))if(!k.call<std::uint32_t>("ka_battle_add_input",h,r.at("tick").get<int>(),r.at("phase").get<int>(),r.at("kind").get<int>(),r.at("item").get<int>()))throw std::runtime_error("input overflow");
 k.call("ka_battle_set_control",h,c.at("battle_state").get<int>(),c.at("battle_frame").get<int>(),c.at("verdict").get<int>(),c.at("verdict_tick").get<int>(),c.at("ending_counter").get<int>(),c.at("ending_gate_tick").get<int>(),0,-1,c.at("prize_count").get<int>());k.call("ka_battle_set_scope_allowed",h,std::uint8_t(prepared.at("rewardScopeAllowed").get<bool>()));
 auto followerDraws=prepared.at("followerSelectionDraws").get<std::uint32_t>();if(followerDraws>1000000)throw std::runtime_error("follower draw count unsupported");if(followerDraws)k.call("ka_battle_skip_lib_draws",h,followerDraws);
 KaBattleReport report{};auto ticks=prepared.at("tickLimit").get<std::uint32_t>();auto policy=prepared.at("policyCode").get<int>();if(ticks<1||ticks>30000||policy<0||policy>2)throw std::runtime_error("tick/policy unsupported");
 const auto finishPolicy=prepared.at("finishPolicy").get<std::string>();
 const int expectedPolicy=finishPolicy=="on-verdict"?2:(finishPolicy=="after-ending"?1:(finishPolicy=="at-horizon"?0:-1));
 if(expectedPolicy<0||expectedPolicy!=policy)throw std::runtime_error("finish policy name/code mismatch");
 const auto nativeStart=timingTelemetry?Timer::now():Timer::time_point{};
 std::unique_ptr<ReplayCapture> replayCapture;
 std::int32_t nativeStatus=0;
 if(prepared.value("replayRequested",false)){
  replayCapture=std::make_unique<ReplayCapture>(k.module,h,prepared);
  nativeStatus=run_replay_capture(k,h,ticks,policy,*replayCapture,report);
 }else nativeStatus=k.call<int>("ka_run_battle",h,ticks,policy,&report);
 if(nativeStatus!=0)throw std::runtime_error("native battle failed");
 if(report.finish_policy!=policy)throw std::runtime_error("native runner finish policy differs from requested policy");
 if(report.verdict<0||report.verdict>2)throw std::runtime_error("native runner returned an unsupported verdict");
 const auto reportStart=timingTelemetry?Timer::now():Timer::time_point{};
 Json out={{"ok",true},{"nativeChecksum",k.call<std::uint64_t>("ka_battle_checksum",h)}};
 const auto* bytes=reinterpret_cast<const unsigned char*>(&report);out["reportBytes"]=sizeof report;out["rawReportBytes"]=std::vector<unsigned char>(bytes,bytes+sizeof report);
 out["report"]=report_json(report);
 KaEncounterReport encounter{};if(k.call<int>("ka_encounter_report",h,policy,&encounter)!=0)throw std::runtime_error("encounter report getter failed");
 out["encounterReport"]=encounter_report_json(encounter);
 out["encounterReportFields"]=encounter_report_fields_json(encounter);
 out["encounterReportBytes"]=sizeof encounter;
 const auto* encounter_bytes=reinterpret_cast<const unsigned char*>(&encounter);out["rawEncounterReportBytes"]=std::vector<unsigned char>(encounter_bytes,encounter_bytes+sizeof encounter);
 const Json reward=reward_outcome(report);
 // Keep the optimizer's historical Earned objective separate from the stricter
 // reward-entitlement report: an uncertified victory still falls back to the
 // pending count reported by the native rewardOutcome, while its award remains unknown.
 Json earned=nullptr;
 std::string earnedBasis="unresolved";
 if(report.verdict==2){earned=0;earnedBasis="loss-gate";}
 else if(report.verdict==1){
  if(reward.at("awardedBasis")=="reward-entitlement-certificate"){
   earned=reward.at("awardedChests");earnedBasis="certified-award";
  }else if(report.pending_final>=0){
   // Match the original chest_count path: rewardOutcome.pendingChests is the
   // native pending_final count, irrespective of the separate verdict snapshot.
   earned=report.pending_final;earnedBasis="queued-at-victory";
  }
 }
 out["earned"]=std::move(earned);
 out["earnedBasis"]=earnedBasis;
 out["rewardOutcome"]=reward;
 out["rewardValid"]=!reward.at("awardedChests").is_null();
 out["rewardCertified"]=reward.at("awardedBasis")=="reward-entitlement-certificate";
 out["finishPolicy"]=finishPolicy;
 out["policyCode"]=policy;
 out["finishDiagnostic"]=finish_diagnostic(report,finishPolicy,policy);
 out["yieldTrusted"]=false;
 out["autoFinishProducerProven"]=false;
 out["mpMetrics"]=compact_mp_metrics(prepared,report);
 out["herbMetrics"]=compact_herb_metrics(report);
 if(replayCapture)out["replay"]=replayCapture->finish(k.module,h,prepared,report);
 if(timingTelemetry){const auto end=Timer::now();const auto ns=[](auto a,auto b){return std::chrono::duration_cast<std::chrono::nanoseconds>(b-a).count();};
  out["timingTelemetry"]={{"importSetupWallNs",ns(executeStart,nativeStart)},{"nativeRunWallNs",ns(nativeStart,reportStart)},
   {"reportCollectionWallNs",ns(reportStart,end)},{"totalExecuteWallNs",ns(executeStart,end)},
   {"clock","steady_clock"},{"interpretation","per-worker wall durations; report includes checksum and JSON projection; total excludes battle destruction after return"}};
 }
 return out;
}
}
