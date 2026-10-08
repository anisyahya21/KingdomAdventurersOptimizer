#include "admission.hpp"
#include "preparation.hpp"
#include "pipeline.hpp"
#include "fixed_formation.hpp"
#include "effective_combat_features.hpp"
#include "mechanism_objective.hpp"
#include "monitor.hpp"
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <bcrypt.h>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <cstdint>
#include <chrono>
#include <vector>
#include <array>
#include <algorithm>
#include <set>
#pragma comment(lib,"bcrypt.lib")
namespace kaopt {
std::string sha256_text(const std::string& data) {
 BCRYPT_ALG_HANDLE alg{};BCRYPT_HASH_HANDLE h{};DWORD len{},got{};
 if(BCryptOpenAlgorithmProvider(&alg,BCRYPT_SHA256_ALGORITHM,nullptr,0)<0)throw std::runtime_error("SHA256 provider failed");
 std::vector<UCHAR> object;
 struct Guard{BCRYPT_ALG_HANDLE& a;BCRYPT_HASH_HANDLE& h;~Guard(){if(h)BCryptDestroyHash(h);if(a)BCryptCloseAlgorithmProvider(a,0);}}guard{alg,h};
 if(BCryptGetProperty(alg,BCRYPT_OBJECT_LENGTH,reinterpret_cast<PUCHAR>(&len),sizeof len,&got,0)<0)throw std::runtime_error("SHA256 property failed");object.resize(len);std::array<UCHAR,32> digest{};
 if(BCryptCreateHash(alg,&h,object.data(),len,nullptr,0,0)<0)throw std::runtime_error("SHA256 create failed");
 for(std::size_t at=0;at<data.size();){auto n=static_cast<ULONG>(std::min<std::size_t>(data.size()-at,1u<<28));if(BCryptHashData(h,reinterpret_cast<PUCHAR>(const_cast<char*>(data.data()+at)),n,0)<0)throw std::runtime_error("SHA256 update failed");at+=n;}
 if(BCryptFinishHash(h,digest.data(),32,0)<0)throw std::runtime_error("SHA256 finish failed");std::string out;static const char* hex="0123456789abcdef";for(auto c:digest){out+=hex[c>>4];out+=hex[c&15];}return out;
}
}
namespace {
using MainClock = std::chrono::steady_clock;
std::uint64_t elapsed_ns(MainClock::time_point start, MainClock::time_point end) {
 return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(end-start).count());
}
std::string bytes(const std::string& path){std::ifstream f(path,std::ios::binary);if(!f)throw std::runtime_error("cannot open "+path);return std::string(std::istreambuf_iterator<char>(f),{});}
kaopt::Json read(const std::string& path){return kaopt::Json::parse(bytes(path));}
void verify(const kaopt::Json& c,const char* field,const char* hash){const auto actual=kaopt::sha256_text(bytes(c.at(field).get<std::string>()));if(actual!=c.at("provenance").at(hash).get<std::string>())throw std::runtime_error(std::string(field)+" SHA256 differs from provenance");}
void write_result(const std::string& path,const kaopt::Json& result){
 const std::filesystem::path target(path),temp=target.string()+".tmp";
 {std::ofstream f(temp,std::ios::binary|std::ios::trunc);f<<result.dump()<<'\n';f.flush();if(!f)throw std::runtime_error("result output failed");}
 if(!MoveFileExW(temp.c_str(),target.c_str(),MOVEFILE_REPLACE_EXISTING|MOVEFILE_WRITE_THROUGH))throw std::runtime_error("result commit failed");
}
kaopt::Json artifact_identity(const std::string& tables,const std::string& kernel=""){
 wchar_t executable[32768]{};if(!GetModuleFileNameW(nullptr,executable,32768))throw std::runtime_error("cannot identify running binary");
 kaopt::Json identity={{"implementationLanguage","cpp"},{"actualExecutableSha256",kaopt::sha256_text(bytes(std::filesystem::path(executable).string()))},{"tablesSha256",kaopt::sha256_text(bytes(tables))}};
 if(!kernel.empty()){identity["currentKernelSha256"]=kaopt::sha256_text(bytes(kernel));identity["sharedSimulationLanguage"]="rust";}return identity;
}

kaopt::Json profile_preflight(const kaopt::Json& candidates,const kaopt::Json& tables) {
 using namespace kaopt;
 set_fixed_formation_enabled(true);
 if(!candidates.is_array()||candidates.empty()) throw std::runtime_error("profile preflight expects a nonempty raw scenario array");
 const auto bounds=fixed_formation_searchable_parameters();
 if(bounds.size()!=7) throw std::runtime_error("fixed profile must expose exactly seven DPS raw stats");
 Json rows=Json::array(); std::set<std::int64_t> encounters; std::uint64_t attempts=0,legal=0,wallRefusals=0;
 for(const auto& raw:candidates) {
  validate_fixed_formation_raw(raw);
  if(raw.at("mathSeed").get<std::int64_t>()!=2026100817||raw.at("libSeed").get<std::int64_t>()!=2026100819)
   throw std::runtime_error("fixture seed pair does not match ordered Seed-001 pair");
  const auto encounter=raw.at("encounterId").get<std::int64_t>();
  if(!encounters.insert(encounter).second) throw std::runtime_error("duplicate encounter in profile preflight input");
  const auto prepared=prepare(admit(raw,tables),tables,false,&raw);
  if(prepared.contains("error")) throw std::runtime_error("fixed profile parent rejected by native prepare: "+prepared.dump());
  if(!prepared.contains("ownFormationOrder")||!prepared.at("ownFormationOrder").is_array()||prepared.at("ownFormationOrder").size()!=6||!prepared.contains("ownUnits")||prepared.at("ownUnits").size()!=6)
   throw std::runtime_error("native prepare changed the pinned formation");
  const auto expectedOrder=prepared.at("ownFormationOrder");
  for(std::size_t i=0;i<6;++i) {
   const Json* found=nullptr;
   for(const auto& unit:prepared.at("ownUnits")) if(unit.value("incomingIndex",std::size_t(999))==i) { found=&unit; break; }
   if(!found) throw std::runtime_error("native prepare lost a fixed roster member");
   const auto& source=raw.at("ownUnits").at(i);
   for(const char* field:{"name","weaponId","equipment","skills","invocationLevels","parameters"})
    if(!source.contains(field)||!found->contains(field)||source.at(field)!=found->at(field))
     throw std::runtime_error(std::string("native prepare changed fixed field ")+field);
  }
  Json children=Json::array(); std::uint64_t rowLegal=0,rowRefused=0;
  for(const auto& [parameter,wall]:bounds) for(const auto step:{-5LL,-1LL,1LL,5LL}) {
   ++attempts;
   try {
    const auto child=mutate_fixed_formation_stat(raw,parameter,step); ++legal; ++rowLegal;
    Json expected=raw; auto& block=expected["ownUnits"][0]["parameters"][parameter];
    const bool maxBounded=parameter=="10"||parameter=="11";
    const auto value=maxBounded?block.at("rawMax").get<std::int64_t>():block.at("rawValue").get<std::int64_t>();
    block["rawValue"]=value+step; if(maxBounded) block["rawMax"]=value+step;
    if(child!=expected) throw std::runtime_error("one stat operator modified more than its one raw DPS stat");
    const auto childPrepared=prepare(admit(child,tables),tables,false,&child);
    if(childPrepared.contains("error")||childPrepared.at("ownFormationOrder")!=expectedOrder)
     throw std::runtime_error("legal DPS child rejected or changed formation during native prepare");
    children.push_back(Json{{"parameter",parameter},{"step",step},{"candidateId",fixed_formation_identity(child)},
     {"rawSha256",sha256_text(child.dump())}});
   } catch(const std::exception& error) {
    const std::string message=error.what();
    if(message.find("crosses canonical stat wall")!=std::string::npos) { ++wallRefusals; ++rowRefused; }
    else throw;
   }
  }
  rows.push_back(Json{{"encounterId",encounter},{"candidateId",fixed_formation_identity(raw)},
   {"rawSha256",sha256_text(raw.dump())},{"prepared",true},{"mutationOps",28},
   {"legalChildrenPrepared",rowLegal},{"wallBoundaryRefusals",rowRefused},{"children",children}});
 }
 return Json{{"schema","ka-synthetic-dps-native-preflight-1"},{"battlesLaunched",0},
  {"candidateProfile","synthetic-dps-fixed-formation-v1"},{"fixedFormationPolicyHash",fixed_formation_policy_hash()},
  {"seedLabel","Seed-001"},{"seedPairs",Json::array({Json::array({2026100817,2026100819})})},
  {"encounterCount",rows.size()},{"mutationOpsPerParent",28},{"mutationAttempts",attempts},
  {"legalChildrenPrepared",legal},{"wallBoundaryRefusals",wallRefusals},{"fixtures",rows}};
}

kaopt::Json normalized_feature_export(const kaopt::Json& input,const kaopt::Json& tables) {
 using namespace kaopt;
 set_fixed_formation_enabled(false);
 if(!input.is_array()) throw std::runtime_error("effective feature export expects a JSON array");
 Json output=Json::array();
 for(std::size_t i=0;i<input.size();++i) {
  const auto& entry=input.at(i);
  const Json* raw=&entry;
  Json id=static_cast<std::uint64_t>(i);
  if(entry.is_object()&&entry.contains("raw")) {
   raw=&entry.at("raw");
   if(entry.contains("candidateId")) id=entry.at("candidateId");
   else if(entry.contains("id")) id=entry.at("id");
  }
  const auto prepared=prepare(admit(*raw,tables),tables,false,raw);
  if(prepared.contains("error")) throw std::runtime_error("feature export candidate rejected: "+prepared.dump());
  const auto features=effective_combat_features(*raw,prepared,tables);
  output.push_back(Json{{"candidateId",id},{"features",features},
      {"flattened",flatten_effective_combat_features(features)}});
 }
 return Json{{"schema","kaopt-effective-combat-export-1"},{"battlesLaunched",0},
  {"candidateCount",output.size()},{"rows",std::move(output)}};
}

}
int main(int argc,char** argv){const auto programStart=MainClock::now();try{
 if(argc==4&&std::string(argv[1])=="--objective-oracle"){write_result(argv[3],kaopt::mechanism::oracle(read(argv[2])));return 0;}
 if(argc==4&&std::string(argv[1])=="--controlled-actions-oracle"){
  const auto fixture=read(argv[2]);
  const auto actions=kaopt::mechanism::controlled_actions(fixture.at("raw"),fixture.at("encounter").get<std::string>(),
      fixture.at("operatorStats"),fixture.at("statScales"),fixture.at("statAnchors"),
      kaopt::fixed_formation_searchable_parameters(),fixture.at("drawKey").get<std::string>());
  kaopt::Json result{{"actions",actions}};
  if(fixture.contains("predictionCases")){result["guidedChoices"]=kaopt::Json::array();for(const auto& c:fixture["predictionCases"])
   result["guidedChoices"].push_back(kaopt::mechanism::guided_action_choice(actions,c.at("predictions"),c.at("draw").get<double>()));}
  write_result(argv[3],result);return 0;
 }
 if(argc==4&&std::string(argv[1])=="--active-parent-oracle"){
  const auto fixture=read(argv[2]);
  write_result(argv[3],kaopt::mechanism::choose_active(fixture.at("records"),fixture.at("lineage"),
      fixture.at("proposal").get<std::uint64_t>(),fixture.at("attempt").get<std::uint64_t>(),fixture.at("draw").get<double>()));return 0;
 }
 if(argc==4&&std::string(argv[1])=="--learner-feedback-replay-oracle"){
  auto fixture=read(argv[2]);auto state=fixture.at("state");
  auto pending=state.value("pendingCandidateIds",kaopt::Json::array());
  auto operatorStats=state.at("mechanismLearnerState").at("operatorStats");
  auto statScales=state.at("mechanismLearnerState").value("statScales",kaopt::Json::object());
  auto anchors=state.at("mechanismLearnerState").at("statAnchors");
  auto improvements=state.value("mechanismImprovements",kaopt::Json::object());
  if(fixture.contains("journalAttempts"))kaopt::mechanism::replay_planned_attempts(operatorStats,statScales,fixture["journalAttempts"]);
  auto replay=[&](){kaopt::Json applied=kaopt::Json::array();for(const auto& child:fixture.at("children")){
   const auto id=child.at("candidateId").get<std::string>();
   if(!child.value("fullyMeasured",false)||std::find(pending.begin(),pending.end(),kaopt::Json(id))==pending.end())continue;
   if(kaopt::mechanism::apply_learner_feedback(operatorStats,anchors,improvements,
       child.at("encounter").get<std::string>(),id,child.at("childRecord"),child.at("parentRecord"),child.at("operation"))){
    applied.push_back(id);pending.erase(std::remove(pending.begin(),pending.end(),kaopt::Json(id)),pending.end());
   }
  }return applied;};
  const auto first=replay();
  // Model a process restart: persist and reload exactly the state fields used
  // by the production checkpoint, then replay the same durable rows again.
  kaopt::Json checkpoint{{"pendingCandidateIds",pending},{"mechanismImprovements",improvements},
   {"mechanismLearnerState",{{"operatorStats",operatorStats},{"statScales",statScales},{"statAnchors",anchors}}}};
  checkpoint=kaopt::Json::parse(checkpoint.dump());pending=checkpoint.at("pendingCandidateIds");
  operatorStats=checkpoint.at("mechanismLearnerState").at("operatorStats");statScales=checkpoint.at("mechanismLearnerState").at("statScales");anchors=checkpoint.at("mechanismLearnerState").at("statAnchors");
  improvements=checkpoint.at("mechanismImprovements");
  for(auto& child:fixture["children"])if(child.at("candidateId")=="child-incomplete")child["fullyMeasured"]=true;
  const auto second=replay();
  checkpoint={{"pendingCandidateIds",pending},{"mechanismImprovements",improvements},
   {"mechanismLearnerState",{{"operatorStats",operatorStats},{"statScales",statScales},{"statAnchors",anchors}}}};
  checkpoint=kaopt::Json::parse(checkpoint.dump());pending=checkpoint.at("pendingCandidateIds");
  operatorStats=checkpoint.at("mechanismLearnerState").at("operatorStats");statScales=checkpoint.at("mechanismLearnerState").at("statScales");anchors=checkpoint.at("mechanismLearnerState").at("statAnchors");
  improvements=checkpoint.at("mechanismImprovements");const auto third=replay();
  checkpoint={{"pendingCandidateIds",pending},{"mechanismImprovements",improvements},
   {"mechanismLearnerState",{{"operatorStats",operatorStats},{"statScales",statScales},{"statAnchors",anchors}}}};
  kaopt::Json budgets=kaopt::Json::array();for(const auto& c:fixture.value("budgetCases",kaopt::Json::array()))
   budgets.push_back(kaopt::mechanism::resume_trial_budget(c.at("importedState").get<bool>(),
    c.at("durableJournalRows").get<std::uint64_t>(),c.at("importedCompleted").get<std::uint64_t>()));
  write_result(argv[3],kaopt::Json{{"firstPassApplied",first},{"restartPassApplied",second},{"repeatedRestartApplied",third},{"state",checkpoint},{"budgetResumeCounts",budgets}});return 0;
 }
 if(argc==3&&std::string(argv[1])=="--monitor")return progress_monitor(argv[2]);
 if(argc==5&&std::string(argv[1])=="--profile-preflight"){auto candidates=read(argv[2]),tables=read(argv[3]);auto report=profile_preflight(candidates,tables);write_result(argv[4],report);return 0;}
 if(argc==5&&std::string(argv[1])=="--effective-features"){auto candidates=read(argv[2]),tables=read(argv[3]);auto report=normalized_feature_export(candidates,tables);write_result(argv[4],report);return 0;}
 if(argc==7&&std::string(argv[1])=="--predict-forest"){auto rows=read(argv[5]);auto report=kaopt::predict_portable_forest(argv[2],argv[3],argv[4],rows);write_result(argv[6],report);return 0;}
 if(argc==4&&std::string(argv[1])=="--prepare"){auto raw=read(argv[2]),tables=read(argv[3]);std::cout<<kaopt::prepare(kaopt::admit(raw,tables),tables,false,&raw).dump()<<'\n';return 0;}
 if(argc==5&&std::string(argv[1])=="--simulate"){auto raw=read(argv[2]),tables=read(argv[3]);auto prepared=kaopt::prepare(kaopt::admit(raw,tables),tables,false,&raw);auto result=kaopt::execute(prepared,argv[4]);result["verificationOnly"]=true;std::cout<<result.dump()<<'\n';return 0;}
 if(argc==6&&std::string(argv[1])=="--propose"){auto raw=read(argv[2]),tables=read(argv[3]),request=read(argv[4]);auto result=kaopt::propose(raw,tables,request);result["provenance"]=artifact_identity(argv[3]);result["requestSha256"]=kaopt::sha256_text(bytes(argv[4]));write_result(argv[5],result);return result.value("ok",false)?0:2;}
 if(argc==8&&std::string(argv[1])=="--replay"){
  auto raw=read(argv[2]),tables=read(argv[3]);const auto original=raw;
  auto seed=[](const char* value){std::size_t used=0;auto n=std::stoll(value,&used);if(used!=std::string(value).size()||n<0||n>2147483647LL)throw std::runtime_error("seed must be nonnegative signed31 integer");return n;};
  raw["mathSeed"]=seed(argv[5]);raw["libSeed"]=seed(argv[6]);auto prepared=kaopt::prepare(kaopt::admit(raw,tables),tables,false,&raw);prepared["replayRequested"]=true;
  auto result=kaopt::execute(prepared,argv[4]);result["verificationOnly"]=true;result["originalIntent"]=original;result["rawScenario"]=raw;result["preparedStateSha256"]=kaopt::sha256_text(prepared.at("snapshot").dump());
  result["provenance"]=artifact_identity(argv[3],argv[4]);result["rawScenarioSha256"]=kaopt::sha256_text(raw.dump());write_result(argv[7],result);return 0;
 }
 if(argc==4&&std::string(argv[1])=="--bundle-tables"){auto mapping=read(argv[2]);kaopt::Json tables=kaopt::Json::object();for(auto i=mapping.begin();i!=mapping.end();++i)tables[i.key()]=read(i.value().get<std::string>());std::ofstream f(argv[3],std::ios::binary|std::ios::trunc);f<<tables.dump()<<'\n';if(!f)throw std::runtime_error("tables output failed");return 0;}
 if(argc!=2)throw std::runtime_error("usage: optimizer CONFIG.json | --profile-preflight RAW_CANDIDATES.json TABLES.json OUTPUT.json | --effective-features RAW_CANDIDATES.json TABLES.json OUTPUT.json | --predict-forest MODEL.json MODEL_SHA STATE_SHA FEATURE_ROWS.json OUTPUT.json | --prepare RAW.json TABLES.json | --simulate RAW.json TABLES.json KERNEL.dll | --propose RAW.json TABLES.json REQUEST.json OUTPUT.json | --replay RAW.json TABLES.json KERNEL.dll MATH_SEED LIB_SEED OUTPUT.json | --bundle-tables PATH_MAP.json OUTPUT.json");
 const auto configReadStart=MainClock::now();auto config=read(argv[1]);const auto configReadEnd=MainClock::now();
 const auto inputHashStart=MainClock::now();verify(config,"rawCandidates","rawCandidatesSha256");verify(config,"tables","tablesSha256");verify(config,"kernelPath","currentKernelSha256");const auto inputHashEnd=MainClock::now();
 const auto identityHashStart=MainClock::now();if(config.contains("candidateMetadata"))config["provenance"]["candidateMetadataSha256"]=kaopt::sha256_text(bytes(config.at("candidateMetadata").get<std::string>()));wchar_t executable[32768]{};if(!GetModuleFileNameW(nullptr,executable,32768))throw std::runtime_error("cannot identify running binary");config["provenance"]["actualExecutableSha256"]=kaopt::sha256_text(bytes(std::filesystem::path(executable).string()));config["provenance"]["implementationLanguage"]="cpp";config["provenance"]["sharedSimulationLanguage"]="rust";const auto identityHashEnd=MainClock::now();
 if(config.contains("searchStatePath")){
  if(!config.contains("searchStateSha256")||!config["searchStateSha256"].is_string()||config["searchStateSha256"].get<std::string>().size()!=64)
   throw std::runtime_error("searchStatePath requires a 64-character searchStateSha256 integrity binding");
  const auto actualStateSha=kaopt::sha256_text(bytes(config["searchStatePath"].get<std::string>()));
  if(actualStateSha!=config["searchStateSha256"].get<std::string>())throw std::runtime_error("searchStateSha256 differs from imported checkpoint bytes");
 }
 const auto monitorStart=MainClock::now();if(!config.value("headless",false))start_progress_monitor(std::filesystem::path(config.at("outputDir").get<std::string>())/"status.json");const auto monitorEnd=MainClock::now();
 const auto comparison=config.value("comparison",kaopt::Json::object());if(comparison.is_object()&&comparison.value("stageTimingTelemetry",false)){config["comparisonMainTiming"]={{"configReadWallNs",elapsed_ns(configReadStart,configReadEnd)},{"inputHashVerificationWallNs",elapsed_ns(inputHashStart,inputHashEnd)},{"identityHashWallNs",elapsed_ns(identityHashStart,identityHashEnd)},{"monitorLaunched",!config.value("headless",false)},{"monitorLaunchWallNs",config.value("headless",false)?kaopt::Json(nullptr):kaopt::Json(elapsed_ns(monitorStart,monitorEnd))},{"timingSemantics","serial main-thread elapsed wall time; stage intervals are within mainBeforePipelineWallNs and are not additive to it"}};const auto beforePipeline=MainClock::now();config["comparisonMainTiming"]["mainBeforePipelineWallNs"]=elapsed_ns(programStart,beforePipeline);}
 return kaopt::run(config);
 }catch(const std::exception& e){std::cerr<<"C++ optimizer: "<<e.what()<<'\n';return 2;}}
