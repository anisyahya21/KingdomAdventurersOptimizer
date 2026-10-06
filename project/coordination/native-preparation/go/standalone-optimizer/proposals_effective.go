package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"sort"
)

// effectiveFineTunePoint is one UI-visible effective-stat request expressed as
// a neutral raw scenario. Error is per target so a bad explicit point cannot
// silently turn into a different, clamped point.
type effectiveFineTunePoint struct {
	Value        int64
	Effective    int64
	HasEffective bool
	Intent       json.RawMessage
	Error        string
}

const (
	minFineTuneInt32 int64 = -1 << 31
	maxFineTuneInt32 int64 = 1<<31 - 1
)

// nativeEffectiveFineTune builds the original fineTune target ladder when no
// values were supplied, or exact effective-stat targets otherwise. HP, MP,
// and Energy (vig) use their prepared maximum as the displayed value.
func nativeEffectiveFineTune(parent json.RawMessage, p *Preparer, unitName, axis string, requested []int64) ([]effectiveFineTunePoint, error) {
	if p == nil {
		return nil, errors.New("fine-tuning requires a canonical preparer")
	}
	pid, ok := nativeStatAxes[axis]
	if !ok {
		return nil, fmt.Errorf("unknown effective-stat axis %q", axis)
	}
	scenario, err := AdmitRawScenario(parent)
	if err != nil {
		return nil, err
	}
	unitIndex := -1
	for i := range scenario.OwnUnits {
		if scenario.OwnUnits[i].Name != unitName {
			continue
		}
		if unitIndex >= 0 {
			return nil, fmt.Errorf("unit name %q is ambiguous", unitName)
		}
		unitIndex = i
	}
	if unitIndex < 0 {
		return nil, fmt.Errorf("unit %q was not found", unitName)
	}
	unit := scenario.OwnUnits[unitIndex]
	if unit.Human == nil || !*unit.Human {
		return nil, fmt.Errorf("effective-stat tuning requires named human unit %q", unitName)
	}
	if pid == 18 && !hasMagicAttack(unit.Skills) {
		return nil, errors.New("Intelligence tuning requires a magic attack skill")
	}
	fighter, err := p.makeOwn(unit)
	if err != nil {
		return nil, fmt.Errorf("prepare unit %q: %w", unitName, err)
	}
	bounded := pid == 10 || pid == 11 || pid == 12
	baseline, err := p.effective(fighter, pid, bounded)
	if err != nil {
		return nil, fmt.Errorf("read effective %s for %q: %w", axis, unitName, err)
	}

	var bounds map[int64][2]int64
	if pid != 12 {
		bounds, err = NativeSearchStatBounds()
		if err != nil {
			return nil, err
		}
		if _, exists := bounds[pid]; !exists {
			return nil, fmt.Errorf("effective-stat axis %q has no reviewed search bounds", axis)
		}
	}
	targets := uniqueSortedFineTuneTargets(requested)
	if len(targets) == 0 {
		var wall *[2]int64
		if b, exists := bounds[pid]; exists {
			copy := b
			wall = &copy
		}
		targets = broadEffectiveFineTuneTargets(baseline, wall)
	}
	if len(targets) > 4096 {
		return nil, errors.New("fine-tuning requires at most 4096 effective targets")
	}

	points := make([]effectiveFineTunePoint, 0, len(targets))
	for _, target := range targets {
		point := effectiveFineTunePoint{Value: target}
		if target < minFineTuneInt32 || target > maxFineTuneInt32 {
			point.Error = "effective target is outside signed32 representation"
			points = append(points, point)
			continue
		}
		var targetErr error
		if pid != 12 {
			targetErr = ValidateNativeSearchStatTarget(pid, target)
		}
		raw, setErr := setNativeFineTuneEffectiveStat(parent, p, unitIndex, pid, target)
		if setErr == nil {
			point.Intent = raw
		}
		if setErr == nil {
			_, setErr = AdmitRawScenario(raw)
		}
		if setErr == nil {
			_, setErr = p.Prepare(raw)
		}
		if setErr == nil {
			var candidate RawScenario
			candidate, setErr = AdmitRawScenario(raw)
			if setErr == nil {
				var actualFighter preparedFighter
				actualFighter, setErr = p.makeOwn(candidate.OwnUnits[unitIndex])
				if setErr == nil {
					point.Effective, setErr = p.effective(actualFighter, pid, bounded)
					point.HasEffective = setErr == nil
					if setErr == nil && point.Effective != target {
						setErr = fmt.Errorf("effective target %d reads back as %d", target, point.Effective)
					}
				}
			}
		}
		if targetErr != nil {
			point.Error = targetErr.Error()
		} else if setErr != nil {
			point.Error = setErr.Error()
		} else {
			point.Intent = raw
		}
		points = append(points, point)
	}
	return points, nil
}

func setNativeFineTuneEffectiveStat(parent json.RawMessage, p *Preparer, unitIndex int, pid, target int64) (json.RawMessage, error) {
	var top map[string]json.RawMessage
	if err := json.Unmarshal(parent, &top); err != nil {
		return nil, err
	}
	var units []map[string]json.RawMessage
	if err := json.Unmarshal(top["ownUnits"], &units); err != nil {
		return nil, err
	}
	if unitIndex < 0 || unitIndex >= len(units) {
		return nil, errors.New("fine-tune unit index is outside the scenario")
	}
	unitFields := units[unitIndex]
	var unit RawUnit
	if err := json.Unmarshal(marshalRawFields(unitFields), &unit); err != nil {
		return nil, err
	}
	fighter, err := p.makeOwn(unit)
	if err != nil {
		return nil, err
	}
	contribution := int64(0)
	for _, equipment := range fighter.equipment {
		contribution = i32(contribution + p.contribution(equipment, pid, fighter.human))
	}
	var params map[string]map[string]int64
	if err := json.Unmarshal(unit.Parameters, &params); err != nil {
		return nil, err
	}
	row := params[fmt.Sprint(pid)]
	if row == nil {
		return nil, fmt.Errorf("unit %q has no parameter %d", unit.Name, pid)
	}
	row["rawValue"] = target
	if pid == 10 || pid == 11 || pid == 12 {
		row["extraValue"] = 0
		row["rawMax"] = target
		row["extraMax"] = i32(-contribution)
	} else {
		row["extraValue"] = i32(-contribution)
	}
	parameters, err := json.Marshal(params)
	if err != nil {
		return nil, err
	}
	unitFields["parameters"] = parameters
	top["ownUnits"], err = json.Marshal(units)
	if err != nil {
		return nil, err
	}
	return json.Marshal(top)
}

func uniqueSortedFineTuneTargets(values []int64) []int64 {
	if len(values) == 0 {
		return nil
	}
	targets := append([]int64(nil), values...)
	sort.Slice(targets, func(i, j int) bool { return targets[i] < targets[j] })
	unique := targets[:0]
	for _, target := range targets {
		if len(unique) == 0 || unique[len(unique)-1] != target {
			unique = append(unique, target)
		}
	}
	return unique
}

// broadEffectiveFineTuneTargets mirrors strategy_finetune.broad_targets: walls,
// the 1000/2000/4000 rungs, then baseline fractions; it skips the baseline,
// suppresses near-duplicate optional points, and caps the result at nine.
func broadEffectiveFineTuneTargets(baseline int64, bounds *[2]int64) []int64 {
	var low, high int64
	hasBounds := bounds != nil
	if hasBounds {
		low, high = bounds[0], bounds[1]
		if high < low {
			low, high = high, low
		}
	}
	keep := make([]int64, 0, 9)
	seen := make(map[int64]struct{}, 9)
	accept := func(value float64, required bool) {
		target := int64(math.RoundToEven(value))
		if target == baseline || target < 0 {
			return
		}
		if hasBounds && (target < low || target > high) {
			return
		}
		if _, exists := seen[target]; exists {
			return
		}
		if !required {
			gap := int64(math.RoundToEven(math.Abs(float64(target)) * 0.01))
			if gap < 1 {
				gap = 1
			}
			for other := range seen {
				if absFineTuneInt64(target-other) < gap {
					return
				}
			}
		}
		seen[target] = struct{}{}
		keep = append(keep, target)
	}
	if hasBounds {
		accept(float64(low), true)
		accept(float64(high), true)
	}
	for _, rung := range []int64{1000, 2000, 4000} {
		accept(float64(rung), true)
	}
	if hasBounds {
		base := float64(baseline)
		for _, fraction := range []float64{0.5, 0.91, 1.09, 2.0} {
			accept(base*fraction, false)
		}
	}
	if len(keep) > 9 {
		keep = keep[:9]
	}
	sort.Slice(keep, func(i, j int) bool { return keep[i] < keep[j] })
	return keep
}

func absFineTuneInt64(value int64) int64 {
	if value < 0 {
		return -value
	}
	return value
}
