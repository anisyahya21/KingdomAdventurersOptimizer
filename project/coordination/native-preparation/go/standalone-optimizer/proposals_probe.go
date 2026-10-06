package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"sort"
)

var nativeProbeParameters = map[string]int64{
	"atk": 13, "def": 14, "hp": 10, "mp": 11, "spd": 15,
	"lck": 16, "dex": 19, "int": 18, "vig": 12,
}

var nativeProbeInputs = map[string]string{"herbs": "holyHerbStock"}

type nativeProbePoint struct {
	Intent        json.RawMessage
	Unit          string
	Value         int64
	Value2        *int64
	Effective     int64
	Effective2    int64
	HasEffective  bool
	HasEffective2 bool
	Error         string
	Warning       string
}

// nativeProbeProposals mirrors strategy_probe.default_ladder/apply_axis. Probe
// values are raw inputs, not fineTune effective targets. Canonical preparation
// decides whether each exact input is legal and supplies its effective reading.
func nativeProbeProposals(parent json.RawMessage, p *Preparer, unitName, axis string, targets []int64, points int, axis2 string, targets2 []int64, points2 int) ([]nativeProbePoint, map[string]int64, error) {
	if p == nil {
		return nil, nil, errors.New("probing requires a canonical preparer")
	}
	if _, ok := nativeProbeParameters[axis]; !ok {
		if _, ok = nativeProbeInputs[axis]; !ok {
			return nil, nil, fmt.Errorf("unknown native probe axis %q", axis)
		}
	}
	if axis2 != "" {
		if _, ok := nativeProbeParameters[axis2]; !ok {
			if _, ok = nativeProbeInputs[axis2]; !ok {
				return nil, nil, fmt.Errorf("unknown native probe axis %q", axis2)
			}
		}
	}
	scenario, err := AdmitRawScenario(parent)
	if err != nil {
		return nil, nil, err
	}
	if _, err := p.Prepare(parent); err != nil {
		return nil, nil, fmt.Errorf("illegal probe parent: %w", err)
	}
	unitIndex, resolvedUnit, err := resolveNativeProbeUnit(scenario, unitName, axis, axis2)
	if err != nil {
		return nil, nil, err
	}
	unitName = resolvedUnit

	baseline := make(map[string]int64, 4)
	firstRaw, err := nativeProbePivot(scenario.Raw, scenario.OwnUnits, unitIndex, unitName, axis)
	if err != nil {
		return nil, nil, err
	}
	baseline["raw"] = firstRaw
	if pid, isStat := nativeProbeParameters[axis]; isStat {
		value, err := nativeProbeEffective(scenario.OwnUnits[unitIndex], p, pid)
		if err != nil {
			return nil, nil, fmt.Errorf("read baseline %s effective value: %w", axis, err)
		}
		baseline["effective"] = value
	}
	var raw2 int64
	if axis2 != "" {
		raw2, err = nativeProbePivot(scenario.Raw, scenario.OwnUnits, unitIndex, unitName, axis2)
		if err != nil {
			return nil, nil, err
		}
		baseline["raw2"] = raw2
		if pid, isStat := nativeProbeParameters[axis2]; isStat {
			value, err := nativeProbeEffective(scenario.OwnUnits[unitIndex], p, pid)
			if err != nil {
				return nil, nil, fmt.Errorf("read baseline %s effective value: %w", axis2, err)
			}
			baseline["effective2"] = value
		}
	}

	if points == 0 {
		points = 7
	}
	if points2 == 0 {
		points2 = points
	}
	firstTargets := append([]int64(nil), targets...)
	if len(firstTargets) == 0 {
		firstTargets, err = nativeProbeDefaultLadder(firstRaw, points)
		if err != nil {
			return nil, nil, fmt.Errorf("default %s ladder: %w", axis, err)
		}
	}
	var secondTargets []int64
	if axis2 != "" {
		secondTargets = append([]int64(nil), targets2...)
		if len(secondTargets) == 0 {
			secondTargets, err = nativeProbeDefaultLadder(raw2, points2)
			if err != nil {
				return nil, nil, fmt.Errorf("default %s ladder: %w", axis2, err)
			}
		}
	}
	if len(firstTargets) == 0 || (axis2 != "" && len(secondTargets) == 0) {
		return nil, nil, errors.New("probe target ladder is empty")
	}
	if len(firstTargets) > 4096 || len(secondTargets) > 4096 {
		return nil, nil, errors.New("probe target ladder exceeds 4096 values")
	}
	if axis2 != "" && len(firstTargets) > 4096/len(secondTargets) {
		return nil, nil, errors.New("two-axis probe grid exceeds 4096 proposals")
	}
	if axis2 == "" && len(firstTargets) > 4096 {
		return nil, nil, errors.New("probe target ladder exceeds 4096 values")
	}

	cellCount := len(firstTargets)
	if axis2 != "" {
		cellCount *= len(secondTargets)
	}
	proposals := make([]nativeProbePoint, 0, cellCount)
	for _, value := range firstTargets {
		if axis2 == "" {
			proposals = append(proposals, nativeProbeCandidate(parent, p, unitIndex, unitName, axis, value, "", nil))
			continue
		}
		for _, value2 := range secondTargets {
			v2 := value2
			proposals = append(proposals, nativeProbeCandidate(parent, p, unitIndex, unitName, axis, value, axis2, &v2))
		}
	}
	return proposals, baseline, nil
}

func resolveNativeProbeUnit(s RawScenario, requested, axis, axis2 string) (int, string, error) {
	statAxes := make([]string, 0, 2)
	for _, name := range []string{axis, axis2} {
		if _, ok := nativeProbeParameters[name]; ok {
			statAxes = append(statAxes, name)
		}
	}
	if len(statAxes) == 0 {
		return -1, requested, nil
	}
	index := -1
	if requested != "" {
		for i, unit := range s.OwnUnits {
			if unit.Name == requested {
				index = i
				break // strategy_probe.require_unit returns the first same-name unit.
			}
		}
	} else {
		for i, unit := range s.OwnUnits {
			if unit.Human == nil || !*unit.Human {
				continue
			}
			if nativeProbeHasIntAxis(statAxes) && hasMagicAttack(unit.Skills) {
				index = i
				break
			}
			if index < 0 {
				index = i
			}
		}
	}
	if index < 0 || index >= len(s.OwnUnits) {
		return -1, "", fmt.Errorf("probe unit %q was not found and no eligible human is available", requested)
	}
	unit := s.OwnUnits[index]
	if unit.Human == nil || !*unit.Human {
		return -1, "", fmt.Errorf("probe unit %q is not a human unit", unit.Name)
	}
	if nativeProbeHasIntAxis(statAxes) && !hasMagicAttack(unit.Skills) {
		return -1, "", fmt.Errorf("probe unit %q has no magic attack skill required for INT", unit.Name)
	}
	return index, unit.Name, nil
}

func nativeProbeHasIntAxis(axes []string) bool {
	for _, axis := range axes {
		if axis == "int" {
			return true
		}
	}
	return false
}

func nativeProbeDefaultLadder(pivot int64, points int) ([]int64, error) {
	if pivot <= 0 {
		return nil, fmt.Errorf("current pivot %d is not positive", pivot)
	}
	if points == 1 {
		return nil, errors.New("one-point custom ladder has undefined ratio spacing")
	}
	if points > 4096 {
		return nil, errors.New("custom ladder exceeds 4096 points")
	}
	var ratios []float64
	switch points {
	case 3:
		ratios = []float64{0.6, 1.0, 1.4}
	case 5:
		ratios = []float64{0.5, 0.75, 1.0, 1.25, 1.5}
	case 7:
		ratios = []float64{0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6}
	default:
		for i := 0; i < points; i++ {
			ratios = append(ratios, 0.4+1.2*float64(i)/float64(points-1))
		}
	}
	seen := map[int64]struct{}{pivot: {}}
	for _, ratio := range ratios {
		target := int64(math.RoundToEven(float64(pivot) * ratio))
		if target < 1 {
			target = 1
		}
		seen[target] = struct{}{}
	}
	ladder := make([]int64, 0, len(seen))
	for target := range seen {
		ladder = append(ladder, target)
	}
	sort.Slice(ladder, func(i, j int) bool { return ladder[i] < ladder[j] })
	return ladder, nil
}

func nativeProbePivot(raw json.RawMessage, units []RawUnit, unitIndex int, unitName, axis string) (int64, error) {
	if pid, ok := nativeProbeParameters[axis]; ok {
		if unitIndex < 0 || unitIndex >= len(units) {
			return 0, errors.New("probe unit index is outside the scenario")
		}
		var params map[string]map[string]json.RawMessage
		if err := json.Unmarshal(units[unitIndex].Parameters, &params); err != nil {
			return 0, err
		}
		entry := params[fmt.Sprint(pid)]
		if entry == nil {
			return 0, fmt.Errorf("unit %q has no parameter %d", unitName, pid)
		}
		return probeRawInt(entry["rawValue"])
	}
	field, ok := nativeProbeInputs[axis]
	if !ok {
		return 0, fmt.Errorf("unknown native probe axis %q", axis)
	}
	var top map[string]json.RawMessage
	if err := json.Unmarshal(raw, &top); err != nil {
		return 0, err
	}
	return probeRawInt(top[field])
}

func probeRawInt(raw json.RawMessage) (int64, error) {
	if len(raw) == 0 || bytes.Equal(bytes.TrimSpace(raw), []byte("null")) {
		return 0, nil
	}
	var value int64
	if err := json.Unmarshal(raw, &value); err != nil {
		return 0, fmt.Errorf("probe pivot is not an integer: %w", err)
	}
	return value, nil
}

func nativeProbeApplyAxis(top map[string]json.RawMessage, unitIndex int, unitName, axis string, target int64) error {
	if target < -1<<31 || target > 1<<31-1 {
		return fmt.Errorf("probe target %d is outside signed32 native input range", target)
	}
	if pid, ok := nativeProbeParameters[axis]; ok {
		var units []map[string]json.RawMessage
		if err := json.Unmarshal(top["ownUnits"], &units); err != nil {
			return err
		}
		if unitIndex < 0 || unitIndex >= len(units) {
			return errors.New("probe unit index is outside the scenario")
		}
		var params map[string]json.RawMessage
		if err := json.Unmarshal(units[unitIndex]["parameters"], &params); err != nil {
			return err
		}
		pidKey := fmt.Sprint(pid)
		var entry map[string]json.RawMessage
		if err := json.Unmarshal(params[pidKey], &entry); err != nil {
			return err
		}
		value, _ := json.Marshal(target)
		entry["rawValue"] = value
		if (pid == 10 || pid == 11 || pid == 12) && len(entry["rawMax"]) > 0 && !bytes.Equal(bytes.TrimSpace(entry["rawMax"]), []byte("null")) {
			maximum, err := probeRawInt(entry["rawMax"])
			if err != nil {
				return fmt.Errorf("unit %q rawMax: %w", unitName, err)
			}
			if target > maximum {
				maximumJSON, _ := json.Marshal(target)
				entry["rawMax"] = maximumJSON
			}
		}
		parameterJSON, err := json.Marshal(entry)
		if err != nil {
			return err
		}
		params[pidKey] = parameterJSON
		units[unitIndex]["parameters"], err = json.Marshal(params)
		if err != nil {
			return err
		}
		top["ownUnits"], err = json.Marshal(units)
		return err
	}
	field, ok := nativeProbeInputs[axis]
	if !ok {
		return fmt.Errorf("unknown native probe axis %q", axis)
	}
	value, _ := json.Marshal(target)
	top[field] = value
	return nil
}

func nativeProbeCandidate(parent json.RawMessage, p *Preparer, unitIndex int, unitName, axis string, value int64, axis2 string, value2 *int64) nativeProbePoint {
	point := nativeProbePoint{Unit: unitName, Value: value, Value2: value2}
	var top map[string]json.RawMessage
	if err := json.Unmarshal(parent, &top); err != nil {
		point.Error = err.Error()
		return point
	}
	if err := nativeProbeApplyAxis(top, unitIndex, unitName, axis, value); err != nil {
		point.Error = err.Error()
		return point
	}
	if axis2 != "" && value2 != nil {
		if err := nativeProbeApplyAxis(top, unitIndex, unitName, axis2, *value2); err != nil {
			point.Error = err.Error()
			return point
		}
	}
	raw, err := json.Marshal(top)
	if err != nil {
		point.Error = err.Error()
		return point
	}
	point.Intent = raw
	candidate, err := AdmitRawScenario(raw)
	if err != nil {
		point.Error = err.Error()
		return point
	}
	if _, err = p.Prepare(raw); err != nil {
		point.Error = err.Error()
		return point
	}
	if pid, ok := nativeProbeParameters[axis]; ok {
		point.Effective, err = nativeProbeEffective(candidate.OwnUnits[unitIndex], p, pid)
		if err != nil {
			point.Error = err.Error()
			return point
		}
		point.HasEffective = true
	}
	if axis2 != "" && value2 != nil {
		if pid, ok := nativeProbeParameters[axis2]; ok {
			point.Effective2, err = nativeProbeEffective(candidate.OwnUnits[unitIndex], p, pid)
			if err != nil {
				point.Error = err.Error()
				return point
			}
			point.HasEffective2 = true
		}
		firstRaw, firstErr := nativeProbePivot(candidate.Raw, candidate.OwnUnits, unitIndex, unitName, axis)
		secondRaw, secondErr := nativeProbePivot(candidate.Raw, candidate.OwnUnits, unitIndex, unitName, axis2)
		if firstErr == nil && secondErr == nil && (firstRaw != value || secondRaw != *value2) {
			point.Warning = fmt.Sprintf("axes overlap; final raw values are %s=%d and %s=%d", axis, firstRaw, axis2, secondRaw)
		}
	}
	return point
}

func nativeProbeEffective(unit RawUnit, p *Preparer, pid int64) (int64, error) {
	fighter, err := p.makeOwn(unit)
	if err != nil {
		return 0, err
	}
	return p.effective(fighter, pid, false)
}
