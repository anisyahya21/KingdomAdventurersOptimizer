package main

// Explicit native operators for the original desktop tuning and skill studies.
// This produces intents only; the coordinator schedules paired native evaluation.
import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
)

type ProposalRequest struct {
	Schema           string  `json:"schema"`
	Operation        string  `json:"operation"`
	ParentID         string  `json:"parentId"`
	Unit             string  `json:"unit"`
	Axis             string  `json:"axis"`
	Axis2            string  `json:"axis2,omitempty"`
	Values           []int64 `json:"values"`
	Values2          []int64 `json:"values2,omitempty"`
	Targets          []int64 `json:"targets,omitempty"`
	Targets2         []int64 `json:"targets2,omitempty"`
	Points           int     `json:"points,omitempty"`
	Points2          int     `json:"points2,omitempty"`
	ValueMode        string  `json:"valueMode,omitempty"`
	Operator         string  `json:"operator,omitempty"`
	Seed             uint64  `json:"seed,omitempty"`
	Count            int     `json:"count,omitempty"`
	ParentGeneration uint64  `json:"parentGeneration,omitempty"`
}
type ProposalRow struct {
	CandidateID    string          `json:"candidateId"`
	ParentID       string          `json:"parentId"`
	Operation      string          `json:"operation"`
	Operator       string          `json:"operator,omitempty"`
	Generation     uint64          `json:"generation"`
	ChangedKnobs   []string        `json:"changedKnobs,omitempty"`
	Unit           string          `json:"unit,omitempty"`
	Axis           string          `json:"axis,omitempty"`
	Axis2          string          `json:"axis2,omitempty"`
	Value          *int64          `json:"value,omitempty"`
	Value2         *int64          `json:"value2,omitempty"`
	Effective      *int64          `json:"effective,omitempty"`
	Effective2     *int64          `json:"effective2,omitempty"`
	RemovedSkill   *int64          `json:"removedSkill,omitempty"`
	Intent         json.RawMessage `json:"intent"`
	PreparedSHA256 string          `json:"preparedSha256,omitempty"`
	Legal          bool            `json:"legal"`
	Error          string          `json:"error,omitempty"`
	Warning        string          `json:"warning,omitempty"`
}

var nativeStatAxes = map[string]int64{"atk": 13, "def": 14, "hp": 10, "mp": 11, "spd": 15, "lck": 16, "dex": 19, "int": 18, "vig": 12}

func runProposals(input, output, requestPath string) error {
	if requestPath == "" {
		return errors.New("propose requires --request")
	}
	data, err := os.ReadFile(input)
	if err != nil {
		return err
	}
	requestData, err := os.ReadFile(requestPath)
	if err != nil {
		return err
	}
	var w Workload
	if err = json.Unmarshal(data, &w); err != nil {
		return err
	}
	if w.Schema != "ka-go-workload-1" || len(w.Candidates) != 1 {
		return errors.New("proposal workload requires exactly one parent")
	}
	var req ProposalRequest
	if err = json.Unmarshal(requestData, &req); err != nil {
		return err
	}
	if req.Schema != "ka-go-proposal-request-1" {
		return errors.New("unknown proposal request schema")
	}
	p, err := NewPreparer(w.Tables)
	if err != nil {
		return err
	}
	parent, err := AdmitRawScenario(w.Candidates[0])
	if err != nil {
		return err
	}
	if _, err = p.Prepare(parent.Raw); err != nil {
		return fmt.Errorf("illegal parent: %w", err)
	}
	parentID := strategyIdentity(parent.Raw)
	rows := []ProposalRow{}
	var baseline map[string]int64
	appendRow := func(raw json.RawMessage, row ProposalRow) {
		row.Intent = raw
		row.ParentID = parentID
		row.CandidateID = strategyIdentity(raw)
		row.Operation = req.Operation
		row.Generation = req.ParentGeneration + 1
		row.ChangedKnobs = proposalChangedKnobs(parent.Raw, raw)
		if row.CandidateID == parentID {
			// A ladder pivot reuses the parent, rather than creating a self child.
			row.ParentID = ""
			row.Generation = req.ParentGeneration
		}
		prepared, e := p.Prepare(raw)
		if row.Error != "" {
			e = errors.New(row.Error)
		}
		if e == nil && req.Operation == "fine-tune" {
			if pid, stat := nativeStatAxes[req.Axis]; stat && pid != 12 {
				scenario, err := AdmitRawScenario(raw)
				if err == nil {
					for _, unit := range scenario.OwnUnits {
						if unit.Name != req.Unit {
							continue
						}
						fighter, err := p.makeOwn(unit)
						if err != nil {
							e = err
							break
						}
						value, err := p.effective(fighter, pid, pid == 10 || pid == 11)
						if err != nil {
							e = err
							break
						}
						e = ValidateNativeSearchStatTarget(pid, value)
					}
				} else {
					e = err
				}
			}
		}
		row.Legal = e == nil
		if e != nil {
			row.Error = e.Error()
		} else {
			row.PreparedSHA256 = digest(prepared)
		}
		rows = append(rows, row)
	}
	switch req.Operation {
	case "probe":
		targets := req.Targets
		if len(targets) == 0 {
			targets = req.Values
		}
		targets2 := req.Targets2
		if len(targets2) == 0 {
			targets2 = req.Values2
		}
		if req.ValueMode != "" && req.ValueMode != "raw" {
			return errors.New("original Probe uses raw input targets")
		}
		points, values, e := nativeProbeProposals(parent.Raw, p, req.Unit, req.Axis, targets, req.Points, req.Axis2, targets2, req.Points2)
		if e != nil {
			return e
		}
		baseline = values
		for _, point := range points {
			value := point.Value
			effective, effective2 := point.Effective, point.Effective2
			row := ProposalRow{Unit: point.Unit, Axis: req.Axis, Axis2: req.Axis2, Value: &value, Error: point.Error, Warning: point.Warning, Operator: "probe"}
			if req.Axis2 != "" {
				row.Value2 = point.Value2
			}
			if point.HasEffective {
				row.Effective = &effective
			}
			if point.HasEffective2 {
				row.Effective2 = &effective2
			}
			if len(point.Intent) == 0 {
				row.ParentID, row.Operation = parentID, req.Operation
				rows = append(rows, row)
			} else {
				appendRow(point.Intent, row)
			}
		}
	case "mutate":
		known := false
		for _, op := range nativeMutationOps {
			if req.Operator == op {
				known = true
				break
			}
		}
		if !known {
			return errors.New("unknown native mutation operator")
		}
		if req.Count < 1 || req.Count > 4096 {
			return errors.New("mutation request requires count in 1..4096")
		}
		rng := req.Seed
		random := func() uint64 {
			rng += 0x9e3779b97f4a7c15
			z := rng
			z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9
			z = (z ^ (z >> 27)) * 0x94d049bb133111eb
			return z ^ (z >> 31)
		}
		for i := 0; i < req.Count; i++ {
			child, err := applyNativeMutation(parent.Raw, p, random, req.Operator)
			if err != nil {
				rows = append(rows, ProposalRow{ParentID: parentID, Operation: req.Operation, Operator: req.Operator, Legal: false, Error: err.Error()})
				continue
			}
			admitted, err := AdmitRawScenario(child)
			if err == nil {
				err = validateNativeMutation(admitted, p)
			}
			if err == nil && bytes.Equal(strategyBytes(child), strategyBytes(parent.Raw)) {
				err = errors.New("requested mutation leaves the strategy unchanged")
			}
			if err != nil {
				rows = append(rows, ProposalRow{ParentID: parentID, Operation: req.Operation, Operator: req.Operator, Intent: child, Legal: false, Error: err.Error()})
				continue
			}
			appendRow(child, ProposalRow{Operator: req.Operator})
		}
	case "fine-tune":
		if req.ValueMode == "effective" {
			points, e := nativeEffectiveFineTune(parent.Raw, p, req.Unit, req.Axis, req.Values)
			if e != nil {
				return e
			}
			for _, point := range points {
				value, effective := point.Value, point.Effective
				row := ProposalRow{Unit: req.Unit, Axis: req.Axis, Value: &value, Error: point.Error}
				if point.HasEffective {
					row.Effective = &effective
				}
				if len(point.Intent) == 0 {
					row.ParentID, row.Operation = parentID, req.Operation
					row.Legal = false
					rows = append(rows, row)
				} else {
					appendRow(point.Intent, row)
				}
			}
			break
		}
		if req.ValueMode != "" && req.ValueMode != "raw" {
			return errors.New("unknown tuning valueMode; use raw or effective")
		}
		pid, stat := nativeStatAxes[req.Axis]
		if !stat && req.Axis != "herbs" {
			return errors.New("unknown native tuning axis")
		}
		if len(req.Values) == 0 || len(req.Values) > 4096 {
			return errors.New("tuning requires 1..4096 explicit values")
		}
		index := -1
		if stat {
			for i, u := range parent.OwnUnits {
				if u.Name == req.Unit {
					if index != -1 {
						return errors.New("ambiguous unit name")
					}
					index = i
				}
			}
			if index < 0 || parent.OwnUnits[index].Human == nil || !*parent.OwnUnits[index].Human {
				return errors.New("tuning requires a named human unit")
			}
			if req.Axis == "int" {
				magic := false
				for _, sid := range parent.OwnUnits[index].Skills {
					if sid >= 5 && sid < 20 {
						magic = true
					}
				}
				if !magic {
					return errors.New("Intelligence tuning requires a magic attack skill (search_contract.MAGIC_SKILL_IDS)")
				}
			}
		}
		for _, value := range req.Values {
			if value < 0 || value > math.MaxInt32 {
				return errors.New("tuning value outside nonnegative signed32 input range")
			}
			var top map[string]json.RawMessage
			_ = json.Unmarshal(parent.Raw, &top)
			if stat {
				var units []map[string]json.RawMessage
				_ = json.Unmarshal(top["ownUnits"], &units)
				var params map[string]map[string]int64
				if err = json.Unmarshal(units[index]["parameters"], &params); err != nil {
					return err
				}
				entry := params[strconv.FormatInt(pid, 10)]
				if entry == nil {
					return errors.New("unit parameter missing")
				}
				entry["rawValue"] = value
				if (pid == 10 || pid == 11 || pid == 12) && entry["rawMax"] < value {
					entry["rawMax"] = value
				}
				units[index]["parameters"] = jsonValue(params)
				top["ownUnits"] = jsonValue(units)
			} else {
				top["holyHerbStock"] = jsonValue(value)
			}
			raw := jsonValue(top)
			row := ProposalRow{Unit: req.Unit, Axis: req.Axis, Value: &value}
			if stat {
				admitted, e := AdmitRawScenario(raw)
				if e != nil {
					return e
				}
				f, e := p.makeOwn(admitted.OwnUnits[index])
				if e == nil {
					effective, e := p.effective(f, pid, false)
					if e == nil {
						row.Effective = &effective
					}
				}
			}
			appendRow(raw, row)
		}
	case "skills":
		groups := map[int64]int{}
		for i, u := range parent.OwnUnits {
			if u.Human == nil || !*u.Human {
				continue
			}
			if req.Unit != "" && u.Name != req.Unit {
				continue
			}
			for j, skill := range u.Skills {
				groups[skill]++
				var top map[string]json.RawMessage
				_ = json.Unmarshal(parent.Raw, &top)
				var units []map[string]json.RawMessage
				_ = json.Unmarshal(top["ownUnits"], &units)
				skills := append([]int64{}, u.Skills[:j]...)
				skills = append(skills, u.Skills[j+1:]...)
				units[i]["skills"] = jsonValue(skills)
				if len(u.Invocation) == len(u.Skills) {
					levels := append([]int64{}, u.Invocation[:j]...)
					levels = append(levels, u.Invocation[j+1:]...)
					units[i]["invocationLevels"] = jsonValue(levels)
				}
				top["ownUnits"] = jsonValue(units)
				appendRow(jsonValue(top), ProposalRow{Unit: u.Name, RemovedSkill: &skill})
			}
		}
		ids := []int64{}
		for skill, count := range groups {
			if count > 1 {
				ids = append(ids, skill)
			}
		}
		sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
		for _, skill := range ids {
			var top map[string]json.RawMessage
			_ = json.Unmarshal(parent.Raw, &top)
			var units []map[string]json.RawMessage
			_ = json.Unmarshal(top["ownUnits"], &units)
			for i, u := range parent.OwnUnits {
				if u.Human == nil || !*u.Human || (req.Unit != "" && u.Name != req.Unit) {
					continue
				}
				skills := []int64{}
				levels := []int64{}
				for j, sid := range u.Skills {
					if sid != skill {
						skills = append(skills, sid)
						if len(u.Invocation) == len(u.Skills) {
							levels = append(levels, u.Invocation[j])
						}
					}
				}
				units[i]["skills"] = jsonValue(skills)
				if len(u.Invocation) == len(u.Skills) {
					units[i]["invocationLevels"] = jsonValue(levels)
				}
			}
			top["ownUnits"] = jsonValue(units)
			appendRow(jsonValue(top), ProposalRow{Unit: "Team", RemovedSkill: &skill})
		}
	default:
		return errors.New("unsupported native proposal operation")
	}
	if err = os.MkdirAll(output, 0700); err != nil {
		return err
	}
	return writeJSON(filepath.Join(output, "proposals.json"), map[string]any{"schema": "ka-go-proposals-1", "sourceRevision": buildRevision, "requestSha256": digest(requestData), "workloadSha256": digest(data), "parentId": parentID, "requestedParentId": req.ParentID, "proposals": rows, "baseline": baseline, "evaluated": false})
}

func proposalChangedKnobs(parent, child json.RawMessage) []string {
	var a, b any
	if json.Unmarshal(parent, &a) != nil || json.Unmarshal(child, &b) != nil {
		return nil
	}
	paths := []string{}
	var walk func(any, any, string)
	walk = func(before, after any, path string) {
		if x, ok := before.(map[string]any); ok {
			if y, ok := after.(map[string]any); ok {
				keys := map[string]bool{}
				for key := range x {
					keys[key] = true
				}
				for key := range y {
					keys[key] = true
				}
				ordered := []string{}
				for key := range keys {
					ordered = append(ordered, key)
				}
				sort.Strings(ordered)
				for _, key := range ordered {
					walk(x[key], y[key], path+"/"+strings.ReplaceAll(strings.ReplaceAll(key, "~", "~0"), "/", "~1"))
				}
				return
			}
		}
		if x, ok := before.([]any); ok {
			if y, ok := after.([]any); ok {
				for i := 0; i < len(x) || i < len(y); i++ {
					var left, right any
					if i < len(x) {
						left = x[i]
					}
					if i < len(y) {
						right = y[i]
					}
					walk(left, right, path+"/"+strconv.Itoa(i))
				}
				return
			}
		}
		if !bytes.Equal(jsonValue(before), jsonValue(after)) {
			paths = append(paths, path)
		}
	}
	walk(a, b, "")
	return paths
}
