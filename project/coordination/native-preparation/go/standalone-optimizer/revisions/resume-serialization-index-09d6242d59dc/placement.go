package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strconv"
)

type initialPlacement struct {
	entries map[string]map[string]json.RawMessage
	statuses map[string][]int64
	enemyCell, bossCell *[2]int64
}

func parseInitialPlacement(optional map[string]json.RawMessage, s RawScenario) (initialPlacement, bool, error) {
	var out initialPlacement
	var profile map[string]json.RawMessage
	if raw := bytes.TrimSpace(optional["startProfile"]); len(raw)>0 && string(raw)!="null" {
		if len(bytes.TrimSpace(optional["prePlacement"]))>0 && string(bytes.TrimSpace(optional["prePlacement"]))!="null" { return out,false,errors.New("startProfile and prePlacement are mutually exclusive") }
		if err:=json.Unmarshal(raw,&profile); err!=nil { return out,false,errors.New("startProfile must be an object") }
		for key:=range profile{if key!="kind"&&key!="enemySpawnCell"&&key!="bossCell"&&key!="startingStatus"&&key!="note"{return out,false,fmt.Errorf("unsupported startProfile field %q",key)}}
		var kind string; _=json.Unmarshal(profile["kind"],&kind)
		if kind!="isolated-scene0" { return out,false,fmt.Errorf("unsupported startProfile kind %q",kind) }
		var err error
		if out.enemyCell,err=readCell(profile["enemySpawnCell"]); err!=nil { return out,false,fmt.Errorf("startProfile enemySpawnCell: %w",err) }
		if out.bossCell,err=readCell(profile["bossCell"]); err!=nil { return out,false,fmt.Errorf("startProfile bossCell: %w",err) }
		if raw:=profile["startingStatus"];len(raw)>0 {
			if err:=json.Unmarshal(raw,&out.statuses);err!=nil{return out,false,errors.New("startProfile startingStatus must map names to [skillId,turns]")}
		}
		return out,false,nil
	}
	raw:=bytes.TrimSpace(optional["prePlacement"])
	if len(raw)==0||string(raw)=="null" { return out,false,nil }
	if err:=json.Unmarshal(raw,&out.entries);err!=nil{return out,false,errors.New("prePlacement must map fighter names to placement objects")}
	if len(out.entries)==0 { return out,false,errors.New("prePlacement must explicitly cover all fighters") }
	return out,true,nil
}

func readCell(raw json.RawMessage)(*[2]int64,error){
	if len(raw)==0||string(bytes.TrimSpace(raw))=="null" {return nil,nil}
	var a []int64;if err:=json.Unmarshal(raw,&a);err!=nil||len(a)!=2{return nil,errors.New("expected integer pair")};for _,v:=range a{if v<math.MinInt32||v>math.MaxInt32{return nil,errors.New("cell coordinate outside signed32 range")}};return &[2]int64{a[0],a[1]},nil
}

func applyExplicitPlacement(f *preparedFighter, all map[string]map[string]json.RawMessage) error {
	raw,ok:=all[f.name];if !ok{return errors.New("missing fighter placement")}
	allowed:=map[string]bool{"cell":true,"position":true,"offset":true,"board":true,"longBoard":true}
	obj:=raw
	for k:=range obj{if !allowed[k]{return fmt.Errorf("unsupported field %q",k)}}
	if len(obj)!=len(allowed){return errors.New("placement requires exactly cell, position, offset, board and longBoard")}
	cell,err:=readCell(obj["cell"]);if err!=nil||cell==nil{return errors.New("cell must be an integer pair")};f.sourceCell=*cell
	var pos,off []float64
	if err=json.Unmarshal(obj["position"],&pos);err!=nil||len(pos)!=3{return errors.New("position must be a finite triple")}
	if err=json.Unmarshal(obj["offset"],&off);err!=nil||len(off)!=3{return errors.New("offset must be a finite triple")}
	for _,v:=range append(pos,off...){if math.IsNaN(v)||math.IsInf(v,0){return errors.New("placement coordinates must be finite")}}
	f.offset=[3]float64{off[0],off[1],off[2]}
	f.board,err=readIntMap(obj["board"]);if err!=nil{return fmt.Errorf("board: %w",err)}
	for _,k:=range []int64{4,5,6,7,8}{if _,ok:=f.board[k];!ok{return fmt.Errorf("board missing required key %d",k)}}
	f.longBoard,err=readIntMap(obj["longBoard"]);if err!=nil{return fmt.Errorf("longBoard: %w",err)}
	statusN:=0;for _,k:=range []int64{62,63,64}{if _,ok:=f.board[k];ok{statusN++}}
	if statusN!=0&&statusN!=3{return errors.New("starting status requires board keys 62/63/64 together")}
	if statusN==3&&(f.board[63]<1){return errors.New("starting status turns must be positive")}
	return nil
}

func readIntMap(raw json.RawMessage)(map[int64]int64,error){
	var src map[string]int64;if err:=json.Unmarshal(raw,&src);err!=nil||src==nil{return nil,errors.New("must be an integer-keyed object")}
	out:=make(map[int64]int64,len(src));for k,v:=range src{n,err:=strconv.ParseInt(k,10,64);if err!=nil||n<math.MinInt32||n>math.MaxInt32||v<math.MinInt32||v>math.MaxInt32{return nil,fmt.Errorf("invalid signed32 board entry %q",k)};out[n]=v};return out,nil
}

func applyStartStatuses(all []preparedFighter, statuses map[string][]int64, skills map[int64]map[string]any) error {
	byName:=make(map[string]*preparedFighter,len(all));for i:=range all{byName[all[i].name]=&all[i]}
	for name,pair:=range statuses{
		f:=byName[name];if f==nil{return fmt.Errorf("startProfile status names unknown fighter %q",name)}
		if len(pair)!=2||pair[1]<1||pair[1]>math.MaxInt32||pair[0]<math.MinInt32||pair[0]>math.MaxInt32{return fmt.Errorf("startProfile status for %s must be [skillId,positive turns]",name)}
		row,ok:=skills[pair[0]];if !ok{return fmt.Errorf("startProfile skill %d missing",pair[0])};typ,_:=asInt(row["type"]);if typ!=66&&typ!=67{return fmt.Errorf("startProfile skill %d is not a generated status",pair[0])}
		if f.board==nil{f.board=map[int64]int64{}};f.board[62]=pair[0];f.board[63]=pair[1];f.board[64]=0
	}
	return nil
}

func parseMPWatch(raw json.RawMessage, own []RawUnit) ([]int64,error){
	if len(bytes.TrimSpace(raw))==0||string(bytes.TrimSpace(raw))=="null"{return []int64{},nil}
	var names []string;if err:=json.Unmarshal(raw,&names);err!=nil{return nil,errors.New("mpWatchUnits must be a list of own-unit names")};if len(names)>2{return nil,errors.New("mpWatchUnits supports at most two units")}
	seen:=map[string]bool{};byName:=map[string]int{}
	for i,u:=range own{byName[u.Name]=i}
	out:=make([]int64,0,len(names));for _,name:=range names{if seen[name]{return nil,errors.New("mpWatchUnits must not repeat a unit")};seen[name]=true;i,ok:=byName[name];if !ok{return nil,fmt.Errorf("mpWatchUnits names undeclared own unit %q",name)};if own[i].Human==nil||!*own[i].Human{return nil,fmt.Errorf("mpWatchUnits must name human residents: %s",name)};out=append(out,int64(100+i))};return out,nil
}
