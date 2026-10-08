#pragma once
#include "pipeline.hpp"
#include <algorithm>
#include <array>
#include <cmath>
#include <iomanip>
#include <limits>
#include <map>
#include <sstream>
#include <set>
#include <stdexcept>
#include <string>
#include <tuple>
#include <vector>

namespace kaopt::mechanism {
inline constexpr const char* version="mechanism-lanes-v3";
inline const std::array<std::string,4> lanes={"earned","potential","setup","efficiency"};
inline double number(const Json& j,const char* k,double fallback=0) {
 auto i=j.find(k);return i!=j.end()&&i->is_number()?i->get<double>():fallback;
}
inline std::vector<double> key(const Json& r,const std::string& lane) {
 const double earned=number(r,"earnedMax"),potential=number(r,"potentialMax"),resources=number(r,"meanResources"),n=number(r,"n");
 const double reliability=r.contains("winInterval")&&r["winInterval"].is_array()&&!r["winInterval"].empty()?r["winInterval"][0].get<double>():0;
 if(lane=="earned")return {earned,reliability,-resources,n};
 if(lane=="potential")return {potential-earned,potential,reliability,-resources,n};
 if(lane=="efficiency")return {earned,-resources,reliability,n};
 if(lane!="setup")throw std::runtime_error("unknown objective lane");
 const auto& p=r.contains("progress")&&r["progress"].is_object()?r["progress"]:Json::object();
 return {number(p,"postDeathPrizes"),number(p,"commandsReleasedAfterDeathTargetingBoss"),
  number(p,"storedCommandsTargetingBossAtDeath"),number(p,"maxSimultaneousCommandsTargetingBoss"),
  number(p,"commandsTargetingBossReleased"),number(p,"storedTargetHoldersPeak"),
  number(p,"maxSimultaneousStoredCommands"),potential,earned};
}
inline bool eligible(const Json& r,const std::string& lane) {
 if(lane=="setup")return number(r,"progressRuns")>0;
 if(lane=="potential")return r.contains("potentialMax")&&!r["potentialMax"].is_null()&&number(r,"potentialMax")-number(r,"earnedMax")>=10;
 return r.contains("earnedMax")&&!r["earnedMax"].is_null()&&number(r,"earnedCount")>0;
}
inline Json pools(const Json& records,std::size_t limit=4) {
 Json result=Json::object();
 for(const auto& r:records) {
  const auto group=std::to_string(r.value("encounterId",0))+":"+std::to_string(r.value("defeatCount",0));
  if(!result.contains(group)) { result[group]=Json::object();for(const auto& lane:lanes)result[group][lane]=Json::array(); }
 }
 for(auto group=result.begin();group!=result.end();++group) for(const auto& lane:lanes) {
  std::vector<const Json*> candidates;
  for(const auto& r:records) {
   const auto g=std::to_string(r.value("encounterId",0))+":"+std::to_string(r.value("defeatCount",0));
   if(g==group.key()&&eligible(r,lane))candidates.push_back(&r);
  }
  if(lane=="efficiency") {
   const auto all=candidates;
   candidates.erase(std::remove_if(candidates.begin(),candidates.end(),[&](const Json* r){
    const auto e=number(*r,"earnedMax"),cost=number(*r,"meanResources");
    for(const auto* o:all){const auto oe=number(*o,"earnedMax"),oc=number(*o,"meanResources");if(oe>=e&&oc<=cost&&(oe>e||oc<cost))return true;}return false;
   }),candidates.end());
  }
  std::stable_sort(candidates.begin(),candidates.end(),[&](const Json* a,const Json* b){return key(*a,lane)>key(*b,lane);});
  for(std::size_t i=0;i<std::min(limit,candidates.size());++i)group.value()[lane].push_back((*candidates[i])["candidate"]);
 }
 return result;
}
inline Json choose(const Json& lanePools,const Json& population,std::uint64_t ordinal,std::uint64_t draw) {
 if(population.empty())throw std::runtime_error("cannot choose from empty objective population");
 auto pick=[&](const Json& ids,const std::string& lane){return Json{{"candidate",ids[draw%ids.size()]},{"lane",lane}};};
 if(ordinal%5==4)return pick(population,"exploration");
 for(std::size_t offset=0;offset<lanes.size();++offset){const auto& lane=lanes[(ordinal+offset)%4];Json allowed=Json::array();
  if(lanePools.contains(lane))for(const auto& id:lanePools[lane])if(std::find(population.begin(),population.end(),id)!=population.end())allowed.push_back(id);
  if(!allowed.empty())return pick(allowed,lane);
 }
 return pick(population,"exploration");
}
inline Json improved_lanes(const Json& parent,const Json& child) {
 Json improved=Json::array();for(const auto& lane:lanes)if(eligible(child,lane)&&(!eligible(parent,lane)||key(child,lane)>key(parent,lane)))improved.push_back(lane);return improved;
}
inline double operator_weight(double attempts,double improved) {return std::max(.35,1.+2.*improved*(improved+1.)/(attempts+4.));}
inline std::string region(const Json& raw) {
 Json humans=Json::array();const std::array<std::string,8> parameters={"10","11","13","14","15","16","18","19"};
 for(const auto& unit:raw.value("ownUnits",Json::array()))if(unit.value("human",false)) {
  auto skills=unit.value("skills",Json::array());std::sort(skills.begin(),skills.end());Json buckets=Json::array();
  const auto p=unit.value("parameters",Json::object());for(const auto& id:parameters){const auto entry=p.value(id,Json::object());const auto magnitude=std::max(1.,std::abs(number(entry,"rawValue")));buckets.push_back(static_cast<int>(std::log10(magnitude)*4));}
  humans.push_back(Json::array({skills,unit.value("invocationLevels",Json::array()),buckets,unit.value("weaponId",0),unit.contains("grid")&&!unit["grid"].is_null()?unit["grid"]:Json(-1)}));
 }
 return sha256_text(humans.dump()).substr(0,16);
}
// Active ordinary branching helper. Fixed-roster callers deliberately disable
// fresh-team generation; absent validation banks do not fabricate mean parents.
inline Json choose_active(const Json& records,const Json& lineage,std::uint64_t proposal,std::uint64_t attempt,double draw) {
 static const std::array<std::string,7> sources={"earned","potential","setup","efficiency","region","exploration","mean"};
 if(records.empty())throw std::runtime_error("empty active parent population");
 const auto all=pools(records);Json combined=Json::object(),population=Json::array();for(const auto& lane:lanes)combined[lane]=Json::array();
 std::vector<std::tuple<std::int64_t,std::int64_t,std::string>> orderedGroups;
 for(const auto& r:records){const auto eid=r.value("encounterId",0LL),defeat=r.value("defeatCount",0LL);const auto g=std::to_string(eid)+":"+std::to_string(defeat);
  if(std::none_of(orderedGroups.begin(),orderedGroups.end(),[&](const auto& x){return std::get<2>(x)==g;}))orderedGroups.emplace_back(eid,defeat,g);}
 std::sort(orderedGroups.begin(),orderedGroups.end());
 std::vector<std::string> groupOrder;for(const auto& item:orderedGroups)groupOrder.push_back(std::get<2>(item));
 for(const auto& group:groupOrder)if(all.contains(group))for(const auto& lane:lanes)for(const auto& id:all[group][lane])combined[lane].push_back(id);
 std::map<std::string,std::pair<double,double>> productivity;
 std::set<std::string> seenRegions;Json regionChoices=Json::array();std::vector<std::pair<std::string,std::string>> regionCandidates;
 for(const auto& r:records){const auto id=r["candidate"].get<std::string>();population.push_back(id);const auto meta=lineage.value(id,Json::object());const auto root=meta.value("root",id);auto& p=productivity[root];p.first+=1;p.second+=meta.value("improved",false)?1:0;regionCandidates.emplace_back(id,meta.value("region",id));}
 std::sort(regionCandidates.begin(),regionCandidates.end());
 for(const auto& [id,signature]:regionCandidates)if(seenRegions.insert(signature).second)regionChoices.push_back(id);
 const auto source=sources[(proposal+attempt)%sources.size()];Json choices=Json::array();
 if(source=="region")choices=regionChoices;
 else if(combined.contains(source))choices=combined[source];
 if(choices.empty())choices=population;
 double total=0;std::vector<double> weights;for(const auto& v:choices){const auto id=v.get<std::string>();const auto meta=lineage.value(id,Json::object());const auto root=meta.value("root",id);const auto p=productivity.at(root);const auto weight=.5+(p.second+1.)/(p.first+2.);weights.push_back(weight);total+=weight;}
 const double target=draw*total;double upto=0;std::size_t index=choices.size()-1;for(std::size_t i=0;i<weights.size();++i){upto+=weights[i];if(target<=upto){index=i;break;}}
 return Json{{"candidate",choices[index]},{"source",source},{"choicePool",choices},{"weights",weights}};
}
inline Json oracle_case(const Json& input,std::size_t pool) {
 const auto& records=input.at("records");const auto ranked=pools(records,pool);
 Json eligibility=Json::object(),rankings=Json::object(),selections=Json::array();
 const auto group=records.empty()?std::string("0:0"):std::to_string(records[0].value("encounterId",0))+":"+std::to_string(records[0].value("defeatCount",0));
 for(const auto& lane:lanes){eligibility[lane]=Json::array();for(const auto& r:records)if(eligible(r,lane))eligibility[lane].push_back(r["candidate"]);rankings[lane]=ranked.contains(group)?ranked[group][lane]:Json::array();}
 for(const auto& p:input.value("proposals",Json::array())) {
  Json population=p.value("population",Json::array());if(!p.contains("population"))for(const auto& r:records)population.push_back(r["candidate"]);
  const auto ordinal=p.at("ordinal").get<std::uint64_t>(),draw=p.value("drawIndex",std::uint64_t(0));
  auto selected=choose(rankings,population,ordinal,draw);Json choicePool=Json::array();
  const auto lane=selected["lane"].get<std::string>();
  if(lane=="exploration")choicePool=population;else for(const auto& id:rankings[lane])if(std::find(population.begin(),population.end(),id)!=population.end())choicePool.push_back(id);
  selected["ordinal"]=ordinal;selected["drawIndex"]=draw;selected["population"]=population;selected["choicePool"]=choicePool;selections.push_back(selected);
 }
 return Json{{"eligibility",eligibility},{"laneRankings",rankings},{"selections",selections}};
}
inline std::string scale_name(double value) {
 std::ostringstream out;out<<std::setprecision(15)<<value;auto text=out.str();
 if(text.find('.')==std::string::npos&&text.find('e')==std::string::npos&&text.find('E')==std::string::npos)text+=".0";
 return text;
}
inline Json operator_weights(const Json& stats,const Json& operations) {
 Json result=Json::object();
 for(const auto& operation:operations) {
  const auto name=operation.get<std::string>();const auto entry=stats.value(name,Json::object());
  const double attempts=number(entry,"attempts"),improved=number(entry,"improved");
  const double rate=(improved+1.)/(attempts+4.);
  result[name]=std::max(.35,1.+2.*improved*rate);
 }
 return result;
}
inline std::string weighted_choice(const Json& orderedWeights,double draw) {
 if(orderedWeights.empty())throw std::runtime_error("empty weighted choice");
 double total=0.;for(const auto& e:orderedWeights)total+=e.at("weight").get<double>();
 if(total<=0.)return orderedWeights[0].at("name").get<std::string>();
 double target=draw*total,upto=0.;
 for(const auto& e:orderedWeights){upto+=e.at("weight").get<double>();if(target<=upto)return e.at("name").get<std::string>();}
 return orderedWeights.back().at("name").get<std::string>();
}
inline Json scale_weights(const Json& stats,const Json& scales) {
 Json result=Json::object();double jump=1.;
 for(auto it=stats.begin();it!=stats.end();++it)if(number(it.value(),"improved")!=0.)jump+=1.;
 for(const auto& scale:scales) {
  const auto name=scale_name(scale.get<double>());
  const auto entry=stats.value(name,Json::object());
  const double attempts=number(entry,"attempts"),improved=number(entry,"improved");
  result[name]=std::max(.35,1.+2.*improved-.15*std::max(0.,attempts-improved));
 }
 result["jump"]=jump;return result;
}
inline Json choose_stat_target(const Json& input) {
 const auto current=input.at("current").get<std::int64_t>(),minimum=input.at("minimum").get<std::int64_t>(),maximum=input.at("maximum").get<std::int64_t>();
 const auto anchor=input.contains("anchor")&&!input["anchor"].is_null()?input["anchor"].get<std::int64_t>():current;
 const auto& scales=input.at("scales");const auto& draws=input.at("randomDraws");const auto& randintCalls=input.at("randintCalls");
 if(draws.empty())throw std::runtime_error("stat-target fixture needs a scale draw");
 const auto weighted=scale_weights(input.value("stats",Json::object()),scales);
 Json ordered=Json::array();for(const auto& scale:scales){const auto name=scale_name(scale.get<double>());ordered.push_back(Json{{"name",name},{"weight",weighted.at(name)}});}ordered.push_back(Json{{"name","jump"},{"weight",weighted.at("jump")}});
 std::size_t randomIndex=0,randintIndex=0;
 const std::string scale=weighted_choice(ordered,draws[randomIndex++].get<double>());
 const std::int64_t base=anchor;
 std::int64_t target=base;
 if(scale=="jump"||base<=0) {
  if(randintCalls.empty())throw std::runtime_error("stat-target fixture needs a randint draw");
  const auto& call=randintCalls[randintIndex++];
  if(call.at("low").get<std::int64_t>()!=minimum||call.at("high").get<std::int64_t>()!=maximum)throw std::runtime_error("stat-target randint bounds mismatch");
  target=call.at("value").get<std::int64_t>();
 } else {
  if(randomIndex>=draws.size())throw std::runtime_error("stat-target fixture needs a direction draw");
  const double directionDraw=draws[randomIndex++].get<double>();
  const double factor=std::stod(scale);
  const double direction=(directionDraw<.5||factor<=1.)?1.:-1.;
  const double raw=static_cast<double>(base)*(direction>0.?factor:1./factor);
  target=static_cast<std::int64_t>(std::nearbyint(raw)); // Python round(): ties-to-even.
 }
 target=std::max(minimum,std::min(maximum,target));
 if(target==current)target=std::max(minimum,std::min(maximum,current+(current<maximum?1:-1)));
 return Json{{"scale",scale},{"target",target},{"randomCalls",randomIndex},{"randintCalls",randintIndex}};
}
inline Json choose_controlled_stat(const Json& stats,const Json& scales,std::int64_t current,
    std::int64_t minimum,std::int64_t maximum,Json anchor,double scaleDraw,double directionDraw,
    std::int64_t jumpTarget) {
 const auto effectiveAnchor=anchor.is_null()?current:anchor.get<std::int64_t>();
 Json ordered=Json::array();const auto weighted=scale_weights(stats,scales);
 for(const auto& scale:scales){const auto name=scale_name(scale.get<double>());ordered.push_back(Json{{"name",name},{"weight",weighted.at(name)}});}
 ordered.push_back(Json{{"name","jump"},{"weight",weighted.at("jump")}});
 const std::string selected=weighted_choice(ordered,scaleDraw);
 std::int64_t target=effectiveAnchor;
 if(selected=="jump"||effectiveAnchor<=0)target=jumpTarget;
 else {
  const double factor=std::stod(selected);
  const double direction=(directionDraw<.5||factor<=1.)?1.:-1.;
  const double raw=static_cast<double>(effectiveAnchor)*(direction>0.?factor:1./factor);
  target=static_cast<std::int64_t>(std::nearbyint(raw));
 }
 target=std::max(minimum,std::min(maximum,target));
 if(target==current)target=std::max(minimum,std::min(maximum,current+(current<maximum?1:-1)));
 return Json{{"scale",selected},{"target",target}};
}
inline double stable_draw(const std::string& material) {
 const auto digest=sha256_text(material);
 std::uint64_t bits=0;for(std::size_t i=0;i<13;++i){const char c=digest[i];const unsigned v=c>='0'&&c<='9'?c-'0':c-'a'+10;bits=(bits<<4)|v;}
 return static_cast<double>(bits)*(1.0/4503599627370496.0);
}
inline Json controlled_actions(const Json& raw,const std::string& encounter,const Json& operatorStats,
    const Json& statScales,const Json& statAnchors,
    const std::map<std::string,std::pair<std::int64_t,std::int64_t>>& bounds,const std::string& drawKey,
    const Json& scales=Json::array({1.05,1.15,1.4,2.0,3.0,0.95,0.7,0.5})) {
 const std::array<std::pair<std::string,std::string>,3> axes={{{"atk","13"},{"spd","15"},{"lck","16"}}};
 Json axisNames=Json::array();for(const auto& [name,_]:axes)axisNames.push_back("stat:"+name);
 const auto encounterOps=operatorStats.value(encounter,Json::object());
 const auto axisWeights=operator_weights(encounterOps,axisNames);double axisTotal=0.;for(const auto& n:axisNames)axisTotal+=axisWeights.at(n.get<std::string>()).get<double>();
 Json actions=Json::array();std::map<std::pair<std::string,std::int64_t>,std::size_t> unique;
 for(const auto& [name,pid]:axes) {
  const std::string op="stat:"+name;const double axisMass=axisWeights.at(op).get<double>()/axisTotal;
  const auto wall=bounds.at(pid);const auto minimum=wall.first,maximum=wall.second;
  const auto current=raw.at("ownUnits").at(0).at("parameters").at(pid).at("rawValue").get<std::int64_t>();
  const auto anchors=statAnchors.value(encounter,Json::object());const Json anchor=anchors.value(pid,Json(nullptr));
  const auto base=anchor.is_null()?current:anchor.get<std::int64_t>();
  const auto scaleStats=statScales.value(encounter,Json::object()).value(pid,Json::object());
  const auto weighted=scale_weights(scaleStats,scales);double scaleTotal=0.;for(const auto& scale:scales)scaleTotal+=weighted.at(scale_name(scale.get<double>())).get<double>();scaleTotal+=weighted.at("jump").get<double>();
  auto add=[&](std::int64_t target,const std::string& scale,double mass){
   target=std::max(minimum,std::min(maximum,target));if(target==current)target=std::max(minimum,std::min(maximum,current+(current<maximum?1:-1)));
   const auto key=std::make_pair(pid,target);const double actionMass=axisMass*mass;
   const Json component={{"learnerStat",op},{"learnerScale",scale},{"weight",actionMass}};
   if(auto it=unique.find(key);it!=unique.end()) {auto& action=actions[it->second];action["learnerWeight"]=action["learnerWeight"].get<double>()+actionMass;action["learnerComponents"].push_back(component);}
   else {unique[key]=actions.size();actions.push_back(Json{{"op","synthetic-dps-stat-target"},{"unitIndex",0},{"parameter",pid},{"target",target},{"minimum",minimum},{"maximum",maximum},{"learnerStat",op},{"learnerScale",scale},{"learnerWeight",actionMass},{"learnerComponents",Json::array({component})}});}
  };
  for(const auto& scaleValue:scales) {
   const std::string scale=scale_name(scaleValue.get<double>());const double scaleMass=weighted.at(scale).get<double>()/scaleTotal;const double factor=scaleValue.get<double>();
   if(scaleMass<=0)continue;
   if(factor>1.) {
    const auto up=static_cast<std::int64_t>(std::nearbyint(static_cast<double>(base)*factor));
    const auto down=static_cast<std::int64_t>(std::nearbyint(static_cast<double>(base)/factor));
    add(up,scale,axisMass==0?0:scaleMass*.5);add(down,scale,scaleMass*.5);
   } else {
    const auto target=static_cast<std::int64_t>(std::nearbyint(static_cast<double>(base)*factor));add(target,scale,scaleMass);
   }
  }
  const double jumpMass=weighted.at("jump").get<double>()/scaleTotal;
  const auto span=static_cast<std::uint64_t>(maximum-minimum)+1;
  const auto jumpOffset=static_cast<std::uint64_t>(stable_draw(drawKey+"|jump|"+pid)*static_cast<double>(span));
  add(minimum+static_cast<std::int64_t>(std::min<std::uint64_t>(span-1,jumpOffset)),"jump",jumpMass);
 }
 return actions;
}
inline std::size_t weighted_action_index(const Json& actions,double draw) {
 if(actions.empty())throw std::runtime_error("empty controlled learner action set");
 double total=0.;for(const auto& action:actions)total+=number(action,"learnerWeight");
 if(total<=0.)return 0;const double target=draw*total;double upto=0.;
 for(std::size_t i=0;i<actions.size();++i){upto+=number(actions[i],"learnerWeight");if(target<=upto)return i;}
 return actions.size()-1;
}
inline Json action_component(Json action,double draw) {
 const auto& components=action.at("learnerComponents");if(components.empty())throw std::runtime_error("controlled action has no learner components");
 double total=0.;for(const auto& c:components)total+=number(c,"weight");if(total<=0.)return action;
 const double target=draw*total;double upto=0.;const Json* selected=&components.back();
 for(const auto& c:components){upto+=number(c,"weight");if(target<=upto){selected=&c;break;}}
 action["learnerStat"]=selected->at("learnerStat");action["learnerScale"]=selected->at("learnerScale");return action;
}
inline Json guided_action_choice(const Json& actions,const Json& predictions,double draw) {
 if(!actions.is_array()||!predictions.is_array()||actions.size()!=predictions.size()||actions.empty())
  throw std::runtime_error("guided action choice requires aligned nonempty actions and predictions");
 double top=-std::numeric_limits<double>::infinity();
 for(const auto& prediction:predictions)if(prediction.is_number()&&std::isfinite(prediction.get<double>()))top=std::max(top,prediction.get<double>());
 Json tied=Json::array();std::vector<std::size_t> indexes;
 for(std::size_t i=0;i<predictions.size();++i)if(predictions[i].is_number()&&predictions[i].get<double>()==top){tied.push_back(actions[i]);indexes.push_back(i);}
 if(indexes.empty())throw std::runtime_error("guided action choice has no finite prediction");
 const auto selected=weighted_action_index(tied,draw);return Json{{"index",indexes.at(selected)},{"prediction",top}};
}
inline Json active_learner_oracle(const Json& input) {
 Json out=Json::object();const auto& f=input.at("activeLearnerFixtures");
 Json sourceRows=Json::array();const auto& rotation=f.at("sourceRotation"),&sources=rotation.at("parentSources");
 for(const auto& item:rotation.at("expected")) {
  const auto proposal=item.at("proposalNumber").get<std::uint64_t>(),attempt=item.at("attempt").get<std::uint64_t>();
  sourceRows.push_back(Json{{"proposalNumber",proposal},{"attempt",attempt},
      {"source",sources[(proposal+attempt)%sources.size()]}});
 }
 out["sourceRotation"]=sourceRows;
 Json parents=Json::array();
 for(const auto& item:f.at("weightedParents")) {
  double total=0.;Json weights=Json::array();
  for(const auto& id:item.at("pool")) {
   const auto key=id.get<std::string>();const auto lineage=item.at("lineage").value(key,Json::object());
   const auto root=lineage.value("root",key);const auto rate=item.at("productivity").value(root,Json::object()).value("rate",.5);
   const double w=.5+rate;total+=w;weights.push_back(w);
  }
  double target=item.at("drawUnit").get<double>()*total,upto=0.;std::size_t chosen=weights.size()-1;
  for(std::size_t i=0;i<weights.size();++i){upto+=weights[i].get<double>();if(target<=upto){chosen=i;break;}}
  parents.push_back(Json{{"candidate",item.at("pool")[chosen]},{"weights",weights}});
 }
 out["weightedParents"]=parents;
 Json improvements=Json::array();for(const auto& item:f.at("improvedLanes"))improvements.push_back(improved_lanes(item.at("parent"),item.at("child")));
 out["improvedLanes"]=improvements;
 Json ops=Json::array();for(const auto& item:f.at("operatorWeights")){
  const auto weights=operator_weights(item.at("stats"),item.at("operations"));Json ordered=Json::array();
  for(const auto& operation:item.at("operations")){const auto name=operation.get<std::string>();ordered.push_back(Json{{"name",name},{"weight",weights.at(name)}});}
  ops.push_back(Json{{"weights",weights},{"selected",weighted_choice(ordered,item.at("drawUnit").get<double>())}});
 }
 out["operatorWeights"]=ops;
 Json scales=Json::array();
 const auto& scaleFixture=f.at("scaleWeights");
 if(scaleFixture.is_array()) for(const auto& item:scaleFixture)scales.push_back(scale_weights(item.at("stats"),item.at("scales")));
 else scales.push_back(scale_weights(scaleFixture.at("stats"),scaleFixture.at("scales")));
 out["scaleWeights"]=scales;
 Json targets=Json::array();for(const auto& item:f.at("statTargets"))targets.push_back(choose_stat_target(item));
 out["statTargets"]=targets;
 out["region"]=region(f.at("region").at("scenario"));
 return out;
}
inline Json oracle(const Json& input) {
 const auto pool=input.value("pool",4);Json cases=Json::array();
 if(input.contains("cases")){for(const auto& c:input["cases"])cases.push_back(Json{{"id",c.at("id")},{"actual",oracle_case(c,pool)}});Json result{{"objectiveMode",version},{"cases",cases}};if(input.contains("activeLearnerFixtures"))result["activeLearnerActual"]=active_learner_oracle(input);return result;}
 return oracle_case(input,pool);
}
// Only observed native telemetry creates progress evidence. Absence stays unknown.
inline void observe(Json& aggregates,const Json& row) {
 const auto id=row.at("strategyId").get<std::string>();auto& a=aggregates[id];if(!a.is_object())a=Json::object();
 const auto& report=row.at("result").at("report");const int verdict=report.value("verdict",0);
 a["n"]=number(a,"n")+1;a["wins"]=number(a,"wins")+(verdict==1?1:0);
 if(report.contains("resource_uses")&&report["resource_uses"].is_number()&&std::isfinite(report["resource_uses"].get<double>())&&report["resource_uses"].get<double>()>=0.) {
  a["resources"]=number(a,"resources")+report["resource_uses"].get<double>();a["resourceRuns"]=number(a,"resourceRuns")+1.;
 }
 if(verdict==1&&row.contains("earned")&&row["earned"].is_number()) {a["earnedCount"]=number(a,"earnedCount")+1;a["earnedMax"]=std::max(number(a,"earnedMax"),number(row,"earned"));}
 const auto reward=row["result"].value("rewardOutcome",Json::object());
 if(reward.contains("pendingChests")&&reward["pendingChests"].is_number())a["potentialMax"]=std::max(number(a,"potentialMax"),number(reward,"pendingChests"));
 else if(report.contains("pre_verdict_prize_callbacks")&&report["pre_verdict_prize_callbacks"].is_number())a["potentialMax"]=std::max(number(a,"potentialMax"),number(report,"pre_verdict_prize_callbacks"));
 const std::array<std::pair<const char*,const char*>,13> mapping={{{"postDeathPrizes","progress_post_death_prizes"},{"postDeathBossLeavings","progress_post_death_leavings"},{"postDeathBossReentries","progress_post_death_reentries"},{"commandsReleasedAfterDeath","progress_released_after_death"},{"commandsReleasedAfterDeathTargetingBoss","progress_released_after_death_targeting_boss"},{"storedCommandsAtDeath","progress_stored_at_death"},{"storedCommandsTargetingBossAtDeath","progress_stored_targeting_boss_at_death"},{"maxSimultaneousStoredCommands","progress_max_stored"},{"maxSimultaneousCommandsTargetingBoss","progress_max_targeting_boss"},{"storedTargetHoldersPeak","progress_target_holders_peak"},{"storedTargetHoldersAtDeath","progress_stored_target_holders_at_death"},{"commandsTargetingBoss","progress_commands_targeting_boss"},{"commandsTargetingBossReleased","progress_commands_targeting_boss_released"}}};
 bool measured=false;
 for(const auto& [name,field]:mapping)if(report.contains(field)&&report[field].is_number()&&number(report,field)>=0) {measured=true;auto& p=a["progress"];if(!p.is_object())p=Json::object();p[name]=std::max(number(p,name),number(report,field));}
 if(measured)a["progressRuns"]=number(a,"progressRuns")+1;
}
inline Json record(const std::string& id,const Json& raw,const Json& a) {
 const double n=number(a,"n"),wins=number(a,"wins"),z=1.96;
 double lower=0,upper=1;if(n){const auto p=wins/n,den=1+z*z/n,center=(p+z*z/(2*n))/den,half=z*std::sqrt(p*(1-p)/n+z*z/(4*n*n))/den;lower=std::max(0.,center-half);upper=std::min(1.,center+half);}
 return Json{{"candidate",id},{"encounterId",raw.value("encounterId",0)},{"defeatCount",raw.value("defeatCount",0)},
 {"n",n},{"wins",wins},{"earnedMax",a.value("earnedMax",Json(nullptr))},{"earnedCount",number(a,"earnedCount")},
 {"potentialMax",a.value("potentialMax",Json(nullptr))},{"winInterval",Json::array({lower,upper})},
 {"meanResources",number(a,"resourceRuns")?Json(number(a,"resources")/number(a,"resourceRuns")):Json(nullptr)},
 {"progress",a.value("progress",Json::object())},{"progressRuns",number(a,"progressRuns")}};
}
// Apply feedback for a durably observed child exactly once. Missing lane
// evidence is not a negative label; an eligible child with no lane gain is.
inline bool apply_learner_feedback(Json& operatorStats,Json& statAnchors,Json& improvements,
                                   const std::string& encounter,const std::string& childId,
                                   const Json& childRecord,const Json& parentRecord,const Json& operation) {
 if(improvements.contains(childId)) return false;
 bool usable=false;for(const auto& lane:lanes)usable=usable||eligible(childRecord,lane);
 if(!usable) return false;
 const auto better=improved_lanes(parentRecord,childRecord);
 improvements[childId]=better;
 auto& stats=operatorStats[encounter];if(!stats.is_object())stats=Json::object();
 auto& op=stats["set-stat"];if(!op.is_object())op=Json::object();
 const char* field=better.empty()?"unproductive":"improved";
 auto& count=op[field];if(!count.is_number_integer())count=0;count=count.get<std::int64_t>()+1;
 if(!better.empty()&&operation.contains("parameter")&&operation.contains("target")) {
  auto& anchors=statAnchors[encounter];if(!anchors.is_object())anchors=Json::object();
  anchors[operation["parameter"].get<std::string>()]=operation["target"];
 }
 return true;
}
inline void replay_planned_attempts(Json& operatorStats,Json& statScales,const Json& rows) {
 std::set<std::string> seen;
 for(const auto& row:rows) {
  const auto id=row.value("strategyId",std::string{});if(id.empty()||!seen.insert(id).second)continue;
  const auto& spec=row.at("operation");
  if(spec.value("op",std::string{})!="synthetic-dps-stat-target"||!spec.contains("learnerStat")||
     !spec.contains("parameter")||!spec.contains("learnerScale"))continue;
  auto bump=[](Json& object,const std::string& key,const char* field) {
   auto& entry=object[key];if(!entry.is_object())entry=Json::object();
   auto& value=entry[field];if(!value.is_number_integer())value=0;value=value.get<std::int64_t>()+1;
  };
  const auto encounter=row.at("encounter").get<std::string>();
  auto& ops=operatorStats[encounter];if(!ops.is_object())ops=Json::object();
  bump(ops,"set-stat","attempts");bump(ops,spec["learnerStat"].get<std::string>(),"attempts");
  auto& perEncounter=statScales[encounter];if(!perEncounter.is_object())perEncounter=Json::object();
  auto& perStat=perEncounter[spec["parameter"].get<std::string>()];if(!perStat.is_object())perStat=Json::object();
  bump(perStat,spec["learnerScale"].get<std::string>(),"planned");bump(perStat,spec["learnerScale"].get<std::string>(),"attempts");
 }
}
inline std::uint64_t resume_trial_budget(bool importedState,std::uint64_t durableJournalRows,
                                         std::uint64_t importedCompleted) {
 return importedState?importedCompleted:durableJournalRows;
}
}
