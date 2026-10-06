#include "admission.hpp"
#include "preparation.hpp"
#include "pipeline.hpp"
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

}
int main(int argc,char** argv){const auto programStart=MainClock::now();try{
 if(argc==3&&std::string(argv[1])=="--monitor")return progress_monitor(argv[2]);
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
 if(argc!=2)throw std::runtime_error("usage: optimizer CONFIG.json | --prepare RAW.json TABLES.json | --simulate RAW.json TABLES.json KERNEL.dll | --propose RAW.json TABLES.json REQUEST.json OUTPUT.json | --replay RAW.json TABLES.json KERNEL.dll MATH_SEED LIB_SEED OUTPUT.json | --bundle-tables PATH_MAP.json OUTPUT.json");
 const auto configReadStart=MainClock::now();auto config=read(argv[1]);const auto configReadEnd=MainClock::now();
 const auto inputHashStart=MainClock::now();verify(config,"rawCandidates","rawCandidatesSha256");verify(config,"tables","tablesSha256");verify(config,"kernelPath","currentKernelSha256");const auto inputHashEnd=MainClock::now();
 const auto identityHashStart=MainClock::now();if(config.contains("candidateMetadata"))config["provenance"]["candidateMetadataSha256"]=kaopt::sha256_text(bytes(config.at("candidateMetadata").get<std::string>()));wchar_t executable[32768]{};if(!GetModuleFileNameW(nullptr,executable,32768))throw std::runtime_error("cannot identify running binary");config["provenance"]["actualExecutableSha256"]=kaopt::sha256_text(bytes(std::filesystem::path(executable).string()));config["provenance"]["implementationLanguage"]="cpp";config["provenance"]["sharedSimulationLanguage"]="rust";const auto identityHashEnd=MainClock::now();
 const auto monitorStart=MainClock::now();if(!config.value("headless",false))start_progress_monitor(std::filesystem::path(config.at("outputDir").get<std::string>())/"status.json");const auto monitorEnd=MainClock::now();
 const auto comparison=config.value("comparison",kaopt::Json::object());if(comparison.is_object()&&comparison.value("stageTimingTelemetry",false)){config["comparisonMainTiming"]={{"configReadWallNs",elapsed_ns(configReadStart,configReadEnd)},{"inputHashVerificationWallNs",elapsed_ns(inputHashStart,inputHashEnd)},{"identityHashWallNs",elapsed_ns(identityHashStart,identityHashEnd)},{"monitorLaunched",!config.value("headless",false)},{"monitorLaunchWallNs",config.value("headless",false)?kaopt::Json(nullptr):kaopt::Json(elapsed_ns(monitorStart,monitorEnd))},{"timingSemantics","serial main-thread elapsed wall time; stage intervals are within mainBeforePipelineWallNs and are not additive to it"}};const auto beforePipeline=MainClock::now();config["comparisonMainTiming"]["mainBeforePipelineWallNs"]=elapsed_ns(programStart,beforePipeline);}
 return kaopt::run(config);
 }catch(const std::exception& e){std::cerr<<"C++ optimizer: "<<e.what()<<'\n';return 2;}}
