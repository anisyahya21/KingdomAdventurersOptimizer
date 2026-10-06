package main

import (
	"errors"
	"fmt"
	"math"
	"strconv"
	"strings"
)

// JSON must preserve canonical floating component values as numbers with a
// decimal marker even when their value is integral (Python snapshot vocabulary).
type snapshotFloat float64

func (v snapshotFloat) MarshalJSON() ([]byte, error) {
	f := float64(v)
	if math.IsNaN(f) || math.IsInf(f, 0) {
		return nil, errors.New("nonfinite snapshot float")
	}
	s := strconv.FormatFloat(f, 'g', -1, 64)
	if !strings.ContainsAny(s, ".eE") {
		s += ".0"
	}
	return []byte(s), nil
}

// snapshot emits the exact field vocabulary consumed by ka_abi.engine_snapshot
// and the Go native JSON hydrator. It builds all entity, subset, bucket and RNG
// state from this candidate's raw scenario and explicit catalogs.
func (p *Preparer) snapshot(all, own, enemies []preparedFighter, rowOffset int64, mathSeed int64, lib *systemRandom, followerDraws int64, prizeCandidates []int64, sequential bool, mpWatch []int64) (map[string]any, error) {
	mathRNG := newSystemRandom(mathSeed)
	units := make([]map[string]any, 0, len(all))
	bucketKeys := make([]int64, 0)
	bucketValues := map[int64][]int64{}
	for i, f := range all {
		if f.invoking == nil {
			f.invoking = [][]int64{}
		}
		identity := int64(100 + i)
		weaponType, _ := asInt(f.weapon["type"])
		rangeN, _ := asInt(f.weapon["shootingRange"])
		motion, _ := asInt(f.weapon["motion"])
		projectileFlag, _ := f.weapon["projectileFlag"].(bool)
		projectile := int64(0)
		if projectileFlag {
			projectile = 1
		}
		state := int64(3)
		if !isMostFront(int64(f.grid)) && rangeN <= 1 && !p.hasCandidate(f, all) {
			state = 1
		}
		direction := int64(0)
		if f.team == 1 {
			direction = 2
		}
		// Both Waiting1 (state1) and Waiting2 (state3) enter with animation
		// behavior3; fighter state IDs are not animation behavior IDs.
		clip, err := p.initialClip(f, 3, direction)
		if err != nil {
			return nil, err
		}
		board := map[int64]int64{}
		for k, v := range f.board {
			board[k] = v
		}
		board[4], board[5], board[6], board[7], board[8] = 0, state, int64(f.team), int64(f.grid), 0
		position := []any{snapshotFloat(i32(f.cell[0] * 24)), snapshotFloat(0), snapshotFloat(i32(f.cell[1] * 24)), snapshotFloat(f.offset[0]), snapshotFloat(f.offset[1]), snapshotFloat(f.offset[2]), nil}
		params := map[int64]any{}
		for pid, value := range f.params {
			params[pid] = value
		}
		equipment := make([]map[string]any, 0, len(f.equipment))
		for _, row := range f.equipment {
			lv, _ := asInt(row["level"])
			aff, _ := asInt(row["affinity"])
			id, _ := asInt(row["id"])
			catalog := p.equipment[id]
			pvp, _ := asInt(catalog["pvpLevel"])
			equipment = append(equipment, map[string]any{"level": lv, "pvpLevel": pvp, "affinity": aff, "parameters": catalog["parameters"]})
		}
		present := []int{0, 1, 2, 5, 7, 12, 14}
		if f.human {
			present = append(present, 18)
		} else {
			present = append(present, 20)
		}
		present = append(present, 28, 33)
		if f.team == 0 {
			present = append(present, 49)
		}
		present = append(present, 51)
		unit := map[string]any{
			"identity": identity, "present": 1, "id": identity, "team": f.team, "human": f.human, "monster": !f.human, "flags": 0, "destroyed": false,
			"components": map[string]any{
				"position": position, "speed": []snapshotFloat{0, 0, 0}, "seb": []int64{map[bool]int64{true: 11, false: 22}[f.human], clip, 0, -1},
				"depth": nil, "cell": []int64{f.cell[0], f.cell[1]}, "image": []int64{-1, -1, -1, -1, -1, -1},
				"animation": []int64{1, -1}, "direction": direction, "modifier": nil, "garbage": nil, "effect": nil, "projectile": nil, "attack": nil,
			},
			"board": board, "long_board": nonNilIntMap(f.longBoard), "parameters": params, "equipment": equipment,
			"skills": f.skills, "levels": f.levels, "invoking": f.invoking, "commands": []any{}, "path": [][]int64{},
			"weapon": map[string]any{"type": weaponType, "shootingRange": rangeN, "motion": motion, "projectileFlag": projectile},
			"boss":   f.boss, "monsterType": f.monsterType, "specialHuman": false, "monsterSize": f.monsterSize, "human_flag": f.flags, "present_slots": present,
		}
		units = append(units, unit)
		key := cellKey(f.sourceCell[0], f.sourceCell[1], 8)
		if _, ok := bucketValues[key]; !ok {
			bucketKeys = append(bucketKeys, key)
		}
		bucketValues[key] = append(bucketValues[key], identity)
	}
	// InitFighters mutates occupancy in team formation order: remove from the
	// factory cell, then append to the final cell bucket, including same-cell moves.
	if sequential {
		for team := 0; team < 2; team++ {
			order := make([]int, 0)
			for i, f := range all {
				if f.team == team {
					order = append(order, i)
				}
			}
			for i := 1; i < len(order); i++ {
				v, j := order[i], i
				for j > 0 && all[order[j-1]].grid > all[v].grid {
					order[j] = order[j-1]
					j--
				}
				order[j] = v
			}
			for _, i := range order {
				f := all[i]
				identity := int64(100 + i)
				oldKey := cellKey(f.sourceCell[0], f.sourceCell[1], 8)
				newKey := cellKey(f.cell[0], f.cell[1], 8)
				bucket := bucketValues[oldKey]
				for n, id := range bucket {
					if id == identity {
						bucketValues[oldKey] = append(bucket[:n], bucket[n+1:]...)
						break
					}
				}
				if _, ok := bucketValues[newKey]; !ok {
					bucketKeys = append(bucketKeys, newKey)
				}
				bucketValues[newKey] = append(bucketValues[newKey], identity)
			}
		}
	}
	ids := make([]any, len(all))
	for i := range all {
		ids[i] = int64(100 + i)
	}
	emptyIDs := []any{}
	subsets := []map[string]any{
		{"slots": emptyIDs, "free": []any{}, "version": 0},
		{"slots": ids, "free": []any{}, "version": len(all)},
		{"slots": ids, "free": []any{}, "version": len(all)},
		{"slots": emptyIDs, "free": []any{}, "version": 0},
		{"slots": ids, "free": []any{}, "version": len(all)},
		{"slots": emptyIDs, "free": []any{}, "version": 0},
		{"slots": emptyIDs, "free": []any{}, "version": 0},
		{"slots": ids, "free": []any{}, "version": len(all)},
		{"slots": ids, "free": []any{}, "version": len(all)},
		{"slots": ids, "free": []any{}, "version": len(all)},
		{"slots": ids, "free": []any{}, "version": len(all)},
	}
	buckets := make([][]any, 0, len(bucketKeys))
	for _, key := range bucketKeys {
		values := make([]any, 0, len(bucketValues[key]))
		for _, id := range bucketValues[key] {
			values = append(values, id)
		}
		buckets = append(buckets, []any{key, values})
	}
	rows := map[int64]map[string]any{}
	for _, sid := range []int64{1, 2} {
		r, ok := p.skills[sid]
		if !ok {
			return nil, fmt.Errorf("required native skill row %d missing", sid)
		}
		rows[sid] = r
	}
	for _, f := range all {
		for _, sid := range f.skills {
			r, ok := p.skills[sid]
			if !ok {
				return nil, fmt.Errorf("skill row %d missing", sid)
			}
			rows[sid] = r
		}
	}
	effectResources := map[int64]int64{}
	for _, row := range p.effectResources {
		id, ok := asInt(row["id"])
		if !ok {
			return nil, errors.New("invalid effect resource id")
		}
		frame, ok := asInt(row["maxFrame"])
		if !ok {
			return nil, errors.New("invalid effect maxFrame")
		}
		effectResources[id] = frame
	}
	animations := [][]int64{}
	for _, entry := range []struct {
		manager int64
		name    string
	}{{11, "chara"}, {22, "monster"}} {
		for _, row := range p.animations[entry.name] {
			id, ok := asInt(row["id"])
			if !ok {
				return nil, errors.New("invalid animation resource id")
			}
			frame, ok := asInt(row["maxFrame"])
			if !ok {
				return nil, errors.New("invalid animation maxFrame")
			}
			animations = append(animations, []int64{entry.manager, id, frame, 0})
		}
	}
	config := map[string]any{"tick": -1, "first_identity": 100, "next_identity": 100 + len(all), "next_command": 0, "map_width": 8, "row_offset": rowOffset, "movement_enabled": true, "battle_state": 2, "battle_frame": 0, "verdict": 0, "verdict_tick": -1, "ending_counter": -1, "ending_gate_tick": -1, "prize_count": 0}
	return map[string]any{
		"config": config, "units": units, "objects": []any{}, "subsets": subsets, "buckets": buckets,
		"rng":  map[string]any{"math": rngJSON(&mathRNG, 0), "lib": rngJSON(lib, followerDraws)},
		"rows": rows, "effect_resources": effectResources, "prizes": prizeCandidates, "projectile_sources": [][]int64{}, "human_bases": p.humanBases, "animation_resources": animations,
		"consumables": map[string]any{"holy_herb_stock": 0, "holy_herb_max_uses": 0, "mp_watch": mpWatch, "items": []any{}, "inputs": []any{}, "uses": []any{}},
	}, nil
}

func cellKey(x, y, width int64) int64 { return i32(x + i32(i32(y*width)*2)) }
func nonNilIntMap(m map[int64]int64) map[int64]int64 {
	if m == nil {
		return map[int64]int64{}
	}
	return m
}
func rngJSON(r *systemRandom, draws int64) map[string]any {
	return map[string]any{"values": r.values[:], "index": r.index, "partner": r.partner, "draws": draws}
}
func isMostFront(grid int64) bool { return uint64(grid+4) < 9 }
func (p *Preparer) initialClip(f preparedFighter, behavior, direction int64) (int64, error) {
	if f.human {
		if behavior < 0 || behavior >= int64(len(p.humanBases)) {
			return 0, errors.New("human animation behavior outside recovered base table")
		}
		base := p.humanBases[behavior]
		hpMax, err := p.effective(f, 10, true)
		if err != nil {
			return 0, err
		}
		hp, err := p.effective(f, 10, false)
		if err != nil {
			return 0, err
		}
		rate := int64(100)
		if hpMax > 0 {
			rate = i32(hp * 100 / hpMax)
		}
		if rate <= 20 && (behavior == 2 || behavior == 3) {
			if behavior == 2 {
				return i32(104 + direction), nil
			}
			return i32(192 + direction), nil
		}
		return i32(base + direction), nil
	}
	base := int64(0)
	if behavior == 4 {
		base = 16
	}
	if f.monsterSize == 3 {
		base += 4
	}
	return i32(base + direction), nil
}
func (p *Preparer) hasCandidate(f preparedFighter, all []preparedFighter) bool {
	mp, err := p.effective(f, 11, false)
	if err != nil {
		return false
	}
	filtered := 0
	for _, sid := range f.skills {
		r := p.skills[sid]
		flags, _ := asInt(r["flags"])
		minimum, _ := asInt(r["minMp"])
		maximum, _ := asInt(r["maxMp"])
		if flags&8 != 0 && flags&32 == 0 && mp >= skillCost(minimum, maximum, f) {
			filtered++
		}
	}
	if filtered > len(f.levels) {
		return false
	}
	for _, sid := range f.skills {
		invoking := false
		for _, active := range f.invoking {
			if len(active) == 2 && active[0] == sid {
				invoking = true
				break
			}
		}
		if invoking {
			continue
		}
		row := p.skills[sid]
		flags, _ := asInt(row["flags"])
		minimum, _ := asInt(row["minMp"])
		maximum, _ := asInt(row["maxMp"])
		if flags&8 == 0 || flags&32 != 0 || mp < skillCost(minimum, maximum, f) {
			continue
		}
		category, _ := asInt(row["category"])
		if category == 2 {
			continue
		}
		if invoking {
			continue
		}
		weaponType, _ := asInt(f.weapon["type"])
		required, _ := asInt(row["requiredEquipType"])
		if required != -1 && required != weaponType {
			continue
		}
		typ, _ := asInt(row["type"])
		rangeN, _ := asInt(row["shootingRange"])
		if rangeN == 0 {
			rangeN, _ = asInt(row["range"])
		}
		if category == 1 {
			chosen := preparedFighter{}
			chosenOK := false
			best := int64(math.MaxInt32)
			for _, target := range all {
				if target.team != f.team || target.name == f.name {
					continue
				}
				rate := preparedHPRate(p, target)
				distance := abs64(f.cell[0]-target.cell[0]) + abs64(f.cell[1]-target.cell[1])
				if distance <= rangeN && rate < 100 && distance < best {
					chosen, chosenOK, best = target, true, distance
				}
			}
			if !chosenOK {
				continue
			}
			hp, _ := p.effective(chosen, 10, false)
			rate := preparedHPRate(p, chosen)
			if typ == 2 && (rate < 1 || rate > 99) {
				continue
			}
			if typ == 15 && hp > 0 {
				continue
			}
			if (typ == 26 || typ == 27 || flags&0x60000 != 0) && (!isMostFront(int64(f.grid)) || !p.skillGeometryHits(f, all, row)) {
				continue
			}
			return true
		}
		if category != 0 || flags&64 != 0 {
			continue
		}
		if flags&16 == 0 {
			if typ != 26 && typ != 27 && flags&0x60000 == 0 {
				return true
			}
			if isMostFront(int64(f.grid)) && p.skillGeometryHits(f, all, row) {
				return true
			}
			continue
		}
		chosen := false
		best := int64(math.MaxInt32)
		for _, target := range all {
			if target.team == f.team || preparedHPRate(p, target) <= 0 {
				continue
			}
			distance := abs64(f.cell[0]-target.cell[0]) + abs64(f.cell[1]-target.cell[1])
			if distance <= rangeN && distance < best {
				best = distance
				chosen = true
			}
		}
		if !chosen {
			continue
		}
		if (typ == 26 || typ == 27 || flags&0x60000 != 0) && (!isMostFront(int64(f.grid)) || !p.skillGeometryHits(f, all, row)) {
			continue
		}
		return true
	}
	return false
}
func preparedHPRate(p *Preparer, f preparedFighter) int64 {
	v, e := p.effective(f, 10, false)
	if e != nil {
		return 0
	}
	m, e := p.effective(f, 10, true)
	if e != nil || m == 0 {
		return 0
	}
	r := i32(v*100) / m
	if r < 0 {
		r = 0
	}
	if r > 100 {
		r = 100
	}
	return r
}
func (p *Preparer) skillGeometryHits(f preparedFighter, all []preparedFighter, row map[string]any) bool {
	typ, _ := asInt(row["type"])
	rng, _ := asInt(row["shootingRange"])
	if rng == 0 {
		rng, _ = asInt(row["range"])
	}
	cells := map[[2]int64]bool{}
	if typ == 26 {
		dirs := [4][2]int64{{0, -1}, {1, 0}, {0, 1}, {-1, 0}}
		d := int(f.team * 2)
		for n := int64(1); n <= rng; n++ {
			cells[[2]int64{f.cell[0] + dirs[d][0]*n, f.cell[1] + dirs[d][1]*n}] = true
		}
	}
	if typ == 27 {
		depth, _ := asInt(row["range"])
		start := f.cell[1] - depth
		if f.team == 1 {
			start = f.cell[1] + 1
		}
		for y := start; y < start+depth; y++ {
			for x := int64(0); x < 5; x++ {
				cells[[2]int64{x, y}] = true
			}
		}
	}
	for _, t := range all {
		if t.team == f.team {
			continue
		}
		if cells[t.cell] {
			return true
		}
	}
	return false
}

func skillCost(minimum, maximum int64, f preparedFighter) int64 {
	if !f.human || minimum == 0 && maximum == 0 {
		return minimum
	}
	sum := int64(0)
	for _, id := range []int64{10, 11, 12, 13, 14, 15, 16, 18, 19, 20, 21, 22} {
		sum = i32(sum + f.params[id]["trainingLevel"])
	}
	average := max32(1, truncDiv(sum, 12))
	progress := i32(average - 1)
	return linearEasing32(minimum, maximum, 998, progress)
}
func linearEasing32(low, high, steps, progress int64) int64 {
	if progress < 0 {
		return low
	}
	if progress >= steps {
		return high
	}
	fraction := float32(progress) / float32(steps)
	value := int64(float32(fraction*float32(i32(high-low))) + float32(low))
	return max32(min32(low, high), min32(max32(low, high), value))
}
func abs64(v int64) int64 {
	if v < 0 {
		return -v
	}
	return v
}
