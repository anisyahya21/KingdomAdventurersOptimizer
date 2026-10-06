package main

// Transparent Go port of the bounded scenario→initial native snapshot path.
// All catalogs are explicit JSON inputs; no Python runtime or opaque snapshot
// template is consulted.
import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"sort"
	"strconv"
)

const preparedItemLimit = 8
const preparedInputLimit = 32

type Preparer struct {
	encounters      map[int64]map[string]any
	monsters        map[int64]map[string]any
	monsterMaster   map[int64][]any
	skills          map[int64]map[string]any
	equipment       map[int64]map[string]any
	effectResources []map[string]any
	animations      map[string][]map[string]any
	humanBases      []int64
	treasures       [][]any
	treasureIDs     []int64
}

func NewPreparer(tables json.RawMessage) (*Preparer, error) {
	var root map[string]json.RawMessage
	if err := json.Unmarshal(tables, &root); err != nil {
		return nil, fmt.Errorf("catalog JSON: %w", err)
	}
	p := &Preparer{}
	encRows, err := rowsAt(root, "encounters", "encounters")
	if err != nil {
		return nil, err
	}
	monsterRows, err := rowsAt(root, "encounters", "monsters")
	if err != nil {
		return nil, err
	}
	profileRows, err := rowsAt(root, "weapon-skill-profiles", "skills")
	if err != nil {
		return nil, err
	}
	equipRows, err := rowsAt(root, "weapon-skill-profiles", "equipment")
	if err != nil {
		return nil, err
	}
	p.encounters, err = indexRows(encRows, "id")
	if err != nil {
		return nil, fmt.Errorf("encounters: %w", err)
	}
	p.monsters, err = indexRows(monsterRows, "id")
	if err != nil {
		return nil, fmt.Errorf("encounter monsters: %w", err)
	}
	var masterDoc map[string]json.RawMessage
	if err := json.Unmarshal(root["Monster"], &masterDoc); err != nil {
		return nil, errors.New("Monster table catalog required")
	}
	var masterRows []struct {
		ID  int64 `json:"id"`
		Row []any `json:"row"`
	}
	if err := json.Unmarshal(masterDoc["rows"], &masterRows); err != nil {
		return nil, errors.New("Monster.rows invalid")
	}
	p.monsterMaster = make(map[int64][]any, len(masterRows))
	for _, row := range masterRows {
		p.monsterMaster[row.ID] = row.Row
	}
	var treasureDoc map[string]json.RawMessage
	if err := json.Unmarshal(root["Treasure"], &treasureDoc); err != nil {
		return nil, errors.New("Treasure table catalog required")
	}
	var treasureRows []struct {
		ID  int64 `json:"id"`
		Row []any `json:"row"`
	}
	if err := json.Unmarshal(treasureDoc["rows"], &treasureRows); err != nil {
		return nil, errors.New("Treasure.rows invalid")
	}
	for _, row := range treasureRows {
		p.treasures = append(p.treasures, row.Row)
		p.treasureIDs = append(p.treasureIDs, row.ID)
	}
	p.skills, err = indexRows(profileRows, "id")
	if err != nil {
		return nil, fmt.Errorf("skills: %w", err)
	}
	p.equipment, err = indexRows(equipRows, "id")
	if err != nil {
		return nil, fmt.Errorf("equipment: %w", err)
	}
	var effectDoc map[string]json.RawMessage
	if err := json.Unmarshal(root["effect-resource-checks"], &effectDoc); err != nil {
		return nil, errors.New("effect-resource-checks catalog required")
	}
	if err := json.Unmarshal(effectDoc["resources"], &p.effectResources); err != nil {
		return nil, errors.New("effect-resource-checks.resources invalid")
	}
	var animDoc map[string]json.RawMessage
	if err := json.Unmarshal(root["animation-resources"], &animDoc); err != nil {
		return nil, errors.New("animation-resources catalog required")
	}
	if err := json.Unmarshal(animDoc["resources"], &p.animations); err != nil {
		return nil, errors.New("animation-resources.resources invalid")
	}
	var constantDoc map[string]json.RawMessage
	if err := json.Unmarshal(root["skill-combat-constants"], &constantDoc); err != nil {
		return nil, errors.New("skill-combat-constants catalog required")
	}
	var humanAnimation map[string]json.RawMessage
	if err := json.Unmarshal(constantDoc["humanAnimationSebBases"], &humanAnimation); err != nil || json.Unmarshal(humanAnimation["values"], &p.humanBases) != nil {
		return nil, errors.New("human animation bases missing")
	}
	return p, nil
}

func rowsAt(root map[string]json.RawMessage, docKey, rowsKey string) ([]map[string]any, error) {
	var doc map[string]json.RawMessage
	if err := json.Unmarshal(root[docKey], &doc); err != nil {
		return nil, fmt.Errorf("catalog %s missing or invalid", docKey)
	}
	var rows []map[string]any
	if err := json.Unmarshal(doc[rowsKey], &rows); err != nil || len(rows) == 0 {
		return nil, fmt.Errorf("catalog %s.%s missing or invalid", docKey, rowsKey)
	}
	return rows, nil
}

func indexRows(rows []map[string]any, key string) (map[int64]map[string]any, error) {
	out := make(map[int64]map[string]any, len(rows))
	for _, r := range rows {
		id, ok := asInt(r[key])
		if !ok {
			return nil, fmt.Errorf("row has invalid %s", key)
		}
		if _, dup := out[id]; dup {
			return nil, fmt.Errorf("duplicate %s %d", key, id)
		}
		out[id] = r
	}
	return out, nil
}

func (p *Preparer) prepareConsumables(optional map[string]json.RawMessage, s RawScenario) (map[string]any, error) {
	herbRaw, ok := optional["holyHerbStock"]
	if !ok {
		return nil, errors.New("explicit holyHerbStock is required")
	}
	herbStock, err := readRawInt32(herbRaw, "holyHerbStock")
	if err != nil || herbStock < 0 {
		return nil, errors.New("holyHerbStock must be a nonnegative signed32 integer")
	}
	herbMaxUses := int64(0)
	if raw, present := optional["holyHerbMaxUses"]; present {
		herbMaxUses, err = readRawInt32(raw, "holyHerbMaxUses")
		if err != nil || herbMaxUses < 0 {
			return nil, errors.New("holyHerbMaxUses must be a nonnegative signed32 integer")
		}
	}
	if herbMaxUses > herbStock {
		return nil, errors.New("holyHerbMaxUses cannot exceed the declared holyHerbStock")
	}

	triggerIDs, err := parseNamedHumanUnits(optional["holyHerbTriggerUnits"], "holyHerbTriggerUnits", s.OwnUnits)
	if err != nil {
		return nil, err
	}
	if herbMaxUses > 0 && len(triggerIDs) == 0 {
		return nil, errors.New("holyHerbMaxUses>0 needs an explicit holyHerbTriggerUnits declaration")
	}
	mpWatch, err := parseMPWatch(optional["mpWatchUnits"], s.OwnUnits)
	if err != nil {
		return nil, err
	}
	if len(mpWatch) == 0 {
		mpWatch = triggerIDs
	}

	items := map[string]json.RawMessage{}
	if raw, present := optional["items"]; present {
		if err := json.Unmarshal(raw, &items); err != nil || items == nil {
			return nil, errors.New("items must map explicit names to item rows")
		}
	}
	stocks := map[string]json.RawMessage{}
	if raw, present := optional["itemStock"]; present {
		if err := json.Unmarshal(raw, &stocks); err != nil || stocks == nil {
			return nil, errors.New("itemStock must map item names to nonnegative integer counts")
		}
	}
	stockByName := make(map[string]int64, len(stocks))
	for name, raw := range stocks {
		count, err := readRawInt32(raw, "itemStock["+name+"]")
		if err != nil || count < 0 {
			return nil, fmt.Errorf("itemStock[%q] must be a nonnegative signed32 integer", name)
		}
		stockByName[name] = count
	}
	if len(items) > preparedItemLimit {
		return nil, fmt.Errorf("items exceeds native capacity %d", preparedItemLimit)
	}

	itemNames := make([]string, 0, len(items))
	for name := range items {
		itemNames = append(itemNames, name)
	}
	sort.Strings(itemNames)
	itemSlots := make(map[string]int64, len(items))
	itemRows := make([]any, 0, len(items))
	itemTypes := make(map[string]int64, len(items))
	parameters := [...]int64{10, 10, 11, 11, 12, 12}
	allResidents := [...]int64{1, 0, 1, 0, 1, 0}
	for slot, name := range itemNames {
		var row map[string]json.RawMessage
		if err := json.Unmarshal(items[name], &row); err != nil || row == nil {
			return nil, fmt.Errorf("items[%q] must be an object", name)
		}
		values := make(map[string]int64, 4)
		for _, field := range []string{"bonusCategory", "bonusType", "bonusMinValue", "bonusMaxValue"} {
			raw, exists := row[field]
			if !exists {
				return nil, fmt.Errorf("items[%q] requires integer %s", name, field)
			}
			value, err := readRawInt32(raw, "items["+name+"]."+field)
			if err != nil {
				return nil, fmt.Errorf("items[%q].%s must be a signed32 integer", name, field)
			}
			values[field] = value
		}
		if values["bonusCategory"] != 3 || values["bonusType"] < 0 || values["bonusType"] > 5 {
			return nil, fmt.Errorf("items[%q]: only bonusCategory 3 bonusType 0..5 recovery items are supported", name)
		}
		bonusType := values["bonusType"]
		stock := stockByName[name]
		itemSlots[name] = int64(slot)
		itemTypes[name] = bonusType
		itemRows = append(itemRows, map[string]any{
			"parameter": parameters[bonusType], "all_residents": allResidents[bonusType],
			"bonus_min": values["bonusMinValue"], "bonus_max": values["bonusMaxValue"], "stock": stock,
		})
	}

	if len(s.Inputs) > preparedInputLimit {
		return nil, fmt.Errorf("inputs exceeds native capacity %d", preparedInputLimit)
	}
	inputRows := make([]any, 0, len(s.Inputs))
	for i, input := range s.Inputs {
		phase := int64(0)
		if input.Phase == "after_fighters" {
			phase = 1
		}
		var entry map[string]any
		switch input.Type {
		case "holy_herb":
			entry = map[string]any{"tick": input.Tick, "phase": phase, "kind": 0, "item": -1}
		case "item":
			var name string
			if err := json.Unmarshal(input.Item, &name); err != nil {
				return nil, fmt.Errorf("inputs[%d]: item must be a string", i)
			}
			slot, declared := itemSlots[name]
			if !declared {
				return nil, fmt.Errorf("inputs[%d]: item %q is not declared in items", i, name)
			}
			if _, stocked := stockByName[name]; !stocked {
				return nil, fmt.Errorf("inputs[%d]: item %q needs an explicit itemStock entry", i, name)
			}
			if allResidents[itemTypes[name]] == 0 {
				return nil, fmt.Errorf("inputs[%d]: single-resident recovery items are not reachable in battle", i)
			}
			entry = map[string]any{"tick": input.Tick, "phase": phase, "kind": 1, "item": slot}
		case "finish":
			return nil, errors.New("Finish/EXP/teardown inputs are not integrated; supply a tick horizon")
		default:
			return nil, fmt.Errorf("inputs[%d]: unsupported input type %q", i, input.Type)
		}
		inputRows = append(inputRows, entry)
	}

	return map[string]any{
		"holy_herb_stock": herbStock, "holy_herb_max_uses": herbMaxUses,
		"mp_watch": mpWatch, "items": itemRows, "inputs": inputRows, "uses": []any{},
	}, nil
}

func parseNamedHumanUnits(raw json.RawMessage, field string, own []RawUnit) ([]int64, error) {
	trimmed := bytes.TrimSpace(raw)
	if len(trimmed) == 0 || string(trimmed) == "null" {
		return []int64{}, nil
	}
	var names []string
	if err := json.Unmarshal(trimmed, &names); err != nil || names == nil {
		return nil, fmt.Errorf("%s must be a list of own-unit names", field)
	}
	if len(names) > 2 {
		return nil, fmt.Errorf("%s supports at most two units", field)
	}
	byName := make(map[string]int, len(own))
	for i, unit := range own {
		byName[unit.Name] = i
	}
	seen := make(map[string]bool, len(names))
	identities := make([]int64, 0, len(names))
	for _, name := range names {
		if seen[name] {
			return nil, fmt.Errorf("%s must not repeat a unit", field)
		}
		seen[name] = true
		i, ok := byName[name]
		if !ok {
			return nil, fmt.Errorf("%s names undeclared own unit %q", field, name)
		}
		if own[i].Human == nil || !*own[i].Human {
			return nil, fmt.Errorf("%s must name human residents: %s", field, name)
		}
		identities = append(identities, int64(100+i))
	}
	return identities, nil
}

func readRawInt32(raw json.RawMessage, label string) (int64, error) {
	if len(bytes.TrimSpace(raw)) == 0 || string(bytes.TrimSpace(raw)) == "null" {
		return 0, fmt.Errorf("%s is missing", label)
	}
	var value int64
	if err := json.Unmarshal(raw, &value); err != nil {
		return 0, fmt.Errorf("%s must be an integer", label)
	}
	if value < math.MinInt32 || value > math.MaxInt32 {
		return 0, fmt.Errorf("%s exceeds signed32 native storage", label)
	}
	return value, nil
}

func (p *Preparer) Prepare(raw json.RawMessage) (json.RawMessage, error) {
	s, err := AdmitRawScenario(raw)
	if err != nil {
		return nil, err
	}
	var optional map[string]json.RawMessage
	if err := json.Unmarshal(raw, &optional); err != nil {
		return nil, fmt.Errorf("scenario options: %w", err)
	}
	consumables, err := p.prepareConsumables(optional, s)
	if err != nil {
		return nil, err
	}
	preparedScenario := s
	preparedScenario.OwnUnits = s.PreparedOwnUnits
	profile, explicit, err := parseInitialPlacement(optional, preparedScenario)
	sequential := explicit || (len(bytes.TrimSpace(optional["startProfile"])) > 0 && string(bytes.TrimSpace(optional["startProfile"])) != "null")
	if err != nil {
		return nil, err
	}
	if s.EncounterID < 0 || s.EncounterID >= 20 {
		return nil, errors.New("encounter outside catalog")
	}
	enc, ok := p.encounters[s.EncounterID]
	if !ok {
		return nil, errors.New("encounter row missing")
	}
	levelField, _ := asInt(enc["levelField"])
	level := i32(levelField + truncDiv(i32(s.DefeatCount), 5))
	lib := newSystemRandom(s.LibSeed)
	var enemies []preparedFighter
	followers, _ := enc["followers"].([]any)
	for _, v := range followers {
		f, ok := v.(map[string]any)
		if !ok {
			return nil, errors.New("invalid follower row")
		}
		rawNext := lib.next()
		chance := randomBelow(rawNext, 100)
		rate, ok := asInt(f["checkRate"])
		if !ok {
			return nil, errors.New("invalid follower check rate")
		}
		if chance < rate {
			mid, ok := asInt(f["monsterId"])
			if !ok {
				return nil, errors.New("invalid follower monster id")
			}
			e, err := p.makeEnemy(mid, level, false)
			if err != nil {
				return nil, err
			}
			enemies = append(enemies, e)
		}
	}
	for i := range enemies {
		enemies[i].name = fmt.Sprintf("enemy:%d:%d", i, enemies[i].monsterID)
		enemies[i].member.Name = enemies[i].name
	}
	bossID, ok := asInt(enc["bossId"])
	if !ok {
		return nil, errors.New("invalid boss id")
	}
	boss, err := p.makeEnemy(bossID, level, true)
	if err != nil {
		return nil, err
	}
	enemies = append(enemies, boss)
	enemyMembers := make([]FormationMember, len(enemies))
	for i, e := range enemies {
		enemyMembers[i] = e.member
	}
	enemyFormation, err := PrepareFormation(enemyMembers, 1, len(enemies))
	if err != nil {
		return nil, err
	}
	for _, place := range enemyFormation.Placements {
		i := place.IncomingIndex
		enemies[i].grid = place.Grid
		enemies[i].cell = place.Cell
		enemies[i].sourceCell = [2]int64{0, 0}
		enemies[i].board = map[int64]int64{19: 0, 20: 0}
		if enemies[i].boss && profile.bossCell != nil {
			enemies[i].sourceCell = *profile.bossCell
		}
		if !enemies[i].boss && profile.enemyCell != nil {
			enemies[i].sourceCell = *profile.enemyCell
		}
		enemies[i].board[19], enemies[i].board[20] = enemies[i].sourceCell[0], enemies[i].sourceCell[1]
	}
	own := make([]preparedFighter, len(preparedScenario.OwnUnits))
	ownMembers := make([]FormationMember, len(preparedScenario.OwnUnits))
	for i, u := range preparedScenario.OwnUnits {
		f, err := p.makeOwn(u)
		if err != nil {
			return nil, fmt.Errorf("ownUnits[%d]: %w", i, err)
		}
		own[i] = f
		ownMembers[i] = f.member
	}
	ownFormation, err := PrepareFormation(ownMembers, 0, len(enemies))
	if err != nil {
		return nil, err
	}
	for _, place := range ownFormation.Placements {
		i := place.IncomingIndex
		own[i].grid = place.Grid
		own[i].cell = place.Cell
		own[i].sourceCell = [2]int64{0, 0}
	}
	if explicit {
		for i := range own {
			if err := applyExplicitPlacement(&own[i], profile.entries); err != nil {
				return nil, fmt.Errorf("prePlacement %s: %w", own[i].name, err)
			}
		}
		for i := range enemies {
			if err := applyExplicitPlacement(&enemies[i], profile.entries); err != nil {
				return nil, fmt.Errorf("prePlacement %s: %w", enemies[i].name, err)
			}
		}
		if len(profile.entries) != len(own)+len(enemies) {
			return nil, errors.New("prePlacement must name every own and spawned enemy exactly once")
		}
		for _, f := range append(append([]preparedFighter{}, own...), enemies...) {
			n := 0
			for _, k := range []int64{62, 63, 64} {
				if _, ok := f.board[k]; ok {
					n++
				}
			}
			if n != 0 && n != 3 {
				return nil, fmt.Errorf("prePlacement %s requires all starting status fields 62/63/64", f.name)
			}
			if n == 3 {
				row, ok := p.skills[f.board[62]]
				if !ok {
					return nil, fmt.Errorf("prePlacement %s has unknown starting status skill %d", f.name, f.board[62])
				}
				typ, _ := asInt(row["type"])
				if typ != 66 && typ != 67 {
					return nil, fmt.Errorf("prePlacement %s has unsupported starting status skill %d", f.name, f.board[62])
				}
			}
		}
	} else {
		combined := append(append([]preparedFighter{}, own...), enemies...)
		if err := applyStartStatuses(combined, profile.statuses, p.skills); err != nil {
			return nil, err
		}
		copy(own, combined[:len(own)])
		copy(enemies, combined[len(own):])
	}
	offset := int64(3)
	if n := int64(len(enemies)/5 + 1); n > offset {
		offset = n
	}
	// The oracle snapshot is captured before RunPreparedBatch applies encounter follower draws.
	// Native execution receives the draw count separately through FollowerDraws.
	engineLib := newSystemRandom(s.LibSeed)
	all := append(own, enemies...)
	group, ok := asInt(enc["rewardGroup"])
	if !ok {
		return nil, errors.New("encounter rewardGroup missing")
	}
	prizes := []int64{}
	for i, row := range p.treasures {
		if len(row) > 4 {
			value, ok := asInt(row[4])
			if !ok {
				var convErr error
				value, convErr = strconv.ParseInt(fmt.Sprint(row[4]), 10, 64)
				if convErr != nil {
					continue
				}
			}
			if value == group {
				prizes = append(prizes, p.treasureIDs[i])
			}
		}
	}
	if !sequential {
		for i := range all {
			all[i].sourceCell = all[i].cell
			all[i].board = map[int64]int64{}
		}
	}
	mpWatch, ok := consumables["mp_watch"].([]int64)
	if !ok {
		return nil, errors.New("prepared consumables mp_watch has an invalid representation")
	}
	snapshot, err := p.snapshot(all, own, enemies, offset, s.MathSeed, &engineLib, 0, prizes, sequential, mpWatch)
	if err != nil {
		return nil, err
	}
	snapshot["consumables"] = consumables
	return json.Marshal(snapshot)
}

// FollowerDraws reports the deterministic number of special-encounter spawn
// checks. Preparation snapshots keep the lib RNG pristine; native execution
// advances this many draws after hydration and before the first update.
func (p *Preparer) FollowerDraws(raw json.RawMessage) (uint32, error) {
	s, err := AdmitRawScenario(raw)
	if err != nil {
		return 0, err
	}
	enc, ok := p.encounters[s.EncounterID]
	if !ok {
		return 0, errors.New("encounter row missing")
	}
	rows, ok := enc["followers"].([]any)
	if !ok {
		return 0, errors.New("encounter follower list invalid")
	}
	if uint64(len(rows)) > math.MaxUint32 {
		return 0, errors.New("follower draw count exceeds uint32")
	}
	return uint32(len(rows)), nil
}

type preparedFighter struct {
	name        string
	human       bool
	team        int
	grid        int
	cell        [2]int64
	sourceCell  [2]int64
	offset      [3]float64
	board       map[int64]int64
	longBoard   map[int64]int64
	params      map[int64]map[string]int64
	skills      []int64
	levels      []int64
	invoking    [][]int64
	equipment   []map[string]any
	weapon      map[string]any
	flags       int64
	monsterID   int64
	monsterType int64
	monsterSize int64
	boss        bool
	member      FormationMember
}

func (p *Preparer) makeOwn(u RawUnit) (preparedFighter, error) {
	f := preparedFighter{name: u.Name, human: *u.Human, team: 0, params: map[int64]map[string]int64{}, skills: u.Skills, levels: u.Invocation, invoking: u.InvokingSkills, flags: u.HumanFlags}
	if !f.human && !bytes.Equal(bytes.TrimSpace(u.MonsterID), []byte("null")) {
		if err := json.Unmarshal(u.MonsterID, &f.monsterID); err != nil {
			return f, errors.New("invalid own monster id")
		}
		mr, ok := p.monsterMaster[f.monsterID]
		if !ok || len(mr) < 6 {
			return f, errors.New("own monster missing from Monster table")
		}
		f.monsterType, _ = strconv.ParseInt(fmt.Sprint(mr[4]), 10, 64)
		f.monsterSize, _ = strconv.ParseInt(fmt.Sprint(mr[5]), 10, 64)
	}
	for _, sid := range u.Skills {
		if _, ok := p.skills[sid]; !ok {
			return f, fmt.Errorf("unknown skill %d", sid)
		}
	}
	for _, pair := range f.invoking {
		if _, ok := p.skills[pair[0]]; !ok {
			return f, fmt.Errorf("unknown invoking skill %d", pair[0])
		}
	}
	for _, sid := range u.Skills {
		row := p.skills[sid]
		typ, _ := asInt(row["type"])
		category, _ := asInt(row["category"])
		flags, _ := asInt(row["flags"])
		if flags&0x40000 != 0 {
			if (typ != 66 && typ != 67) || category != 0 {
				return f, fmt.Errorf("skill %d generated-status route unsupported", sid)
			}
			continue
		}
		if typ == 48 {
			if category != 2 || flags&8 != 0 {
				return f, fmt.Errorf("skill %d equip-master route unsupported", sid)
			}
			continue
		}
		if !supportedType(typ) {
			return f, fmt.Errorf("skill %d type %d unsupported", sid, typ)
		}
	}
	var srcParams map[string]map[string]int64
	if err := json.Unmarshal(u.Parameters, &srcParams); err != nil {
		return f, errors.New("parameters must be raw parameter objects")
	}
	for k, v := range srcParams {
		id, err := strconv.ParseInt(k, 10, 64)
		if err != nil {
			return f, errors.New("parameter key must be an integer")
		}
		for _, field := range []string{"rawValue", "rawMax", "extraValue", "extraMax", "trainingLevel"} {
			n, ok := v[field]
			if !ok || n < math.MinInt32 || n > math.MaxInt32 {
				return f, fmt.Errorf("parameter %d missing or out-of-range %s", id, field)
			}
		}
		f.params[id] = v
	}
	if *u.Human {
		for _, id := range []int64{10, 11, 12, 13, 14, 15, 16, 18, 19, 20, 21, 22} {
			if f.params[id] == nil {
				return f, fmt.Errorf("missing human training parameter %d", id)
			}
		}
	} else {
		for _, id := range []int64{10, 11, 13, 14, 15, 16, 19} {
			if f.params[id] == nil {
				return f, fmt.Errorf("missing monster parameter %d", id)
			}
		}
	}
	var slots []map[string]any
	if err := json.Unmarshal(u.Equipment, &slots); err != nil {
		return f, errors.New("equipment must be an explicit array")
	}
	for _, slot := range slots {
		if len(slot) != 3 {
			return f, errors.New("equipment slots require exactly id, level and affinity")
		}
		id, ok := asInt(slot["id"])
		if !ok {
			return f, errors.New("equipment id missing")
		}
		row, ok := p.equipment[id]
		if !ok {
			return f, fmt.Errorf("unknown equipment %d", id)
		}
		level, ok := asInt(slot["level"])
		if !ok || level < 1 {
			return f, errors.New("equipment level must be positive")
		}
		aff, ok := asInt(slot["affinity"])
		if !ok {
			return f, errors.New("equipment affinity must be an integer in supported path")
		}
		f.equipment = append(f.equipment, map[string]any{"id": id, "level": level, "affinity": aff, "parameters": row["parameters"]})
	}
	lifted := map[int64]bool{}
	for _, sid := range f.skills {
		skill := p.skills[sid]
		typ, _ := asInt(skill["type"])
		if typ == 48 {
			v, ok := asInt(skill["value"])
			if ok {
				lifted[v] = true
			}
		}
	}
	for _, eq := range f.equipment {
		id, _ := asInt(eq["id"])
		typ, _ := asInt(p.equipment[id]["type"])
		aff, _ := asInt(eq["affinity"])
		if lifted[typ] && (aff == -1 || aff == 0) {
			eq["affinity"] = int64(1)
		}
	}
	weaponID := *u.WeaponID
	weapon, ok := p.equipment[weaponID]
	if !ok {
		return f, errors.New("unknown weapon")
	}
	cat, _ := asInt(weapon["category"])
	if cat != 0 {
		return f, errors.New("weaponId does not identify a weapon")
	}
	f.weapon = weapon
	if weaponID != 0 {
		found := false
		for _, r := range f.equipment {
			if id, _ := asInt(r["id"]); id == weaponID {
				found = true
			}
		}
		if !found {
			return f, errors.New("selected weapon missing from equipment contribution rows")
		}
	}
	def, err := p.effective(f, 14, false)
	if err != nil {
		return f, err
	}
	fv := (*int64)(nil)
	for _, sid := range f.skills {
		row := p.skills[sid]
		typ, _ := asInt(row["type"])
		if typ == 60 {
			value, _ := asInt(row["value"])
			fv = &value
			break
		}
	}
	f.member = FormationMember{Name: f.name, EffectiveDefense: def, FormationValue: fv, Visitor: u.Visitor, LeaderIdentity: u.LeaderIdentity, Monster: !f.human, OwnerPlayer: u.OwnerPlayer}
	for _, id := range []int64{10, 11} {
		if _, ok := f.params[id]; ok {
			max, err := p.effective(f, id, true)
			if err != nil {
				return f, err
			}
			row := f.params[id]
			v := i32(row["rawValue"] + max)
			upper := max
			if rawMax := row["rawMax"]; rawMax != math.MaxInt32 {
				upper = i32(rawMax + row["extraMax"])
				for _, eq := range f.equipment {
					upper = i32(upper + p.contribution(eq, id, f.human))
				}
			}
			if upper > 0 {
				v = max32(0, min32(upper, v))
			}
			row["rawValue"] = v
		}
	}
	return f, nil
}

func (p *Preparer) makeEnemy(id, level int64, boss bool) (preparedFighter, error) {
	m, ok := p.monsters[id]
	if !ok {
		return preparedFighter{}, fmt.Errorf("monster %d missing from encounter catalog", id)
	}
	curves, ok := m["parametersRaw"].([]any)
	if !ok || len(curves) != 7 {
		return preparedFighter{}, fmt.Errorf("monster %d raw curves missing", id)
	}
	ids := []int64{10, 11, 13, 14, 15, 16, 19}
	params := map[int64]map[string]int64{}
	for i, pid := range ids {
		curve, ok := curves[i].([]any)
		if !ok || len(curve) != 4 {
			return preparedFighter{}, errors.New("invalid monster parameter curve")
		}
		vals := [4]int64{}
		for j := range vals {
			v, ok := asInt(curve[j])
			if !ok {
				return preparedFighter{}, errors.New("invalid monster curve value")
			}
			vals[j] = v
		}
		v := monsterParameter(vals, level)
		max := int64(math.MaxInt32)
		if pid == 10 || pid == 11 {
			max = v
		}
		params[pid] = map[string]int64{"rawValue": v, "extraValue": 0, "rawMax": max, "extraMax": 0, "trainingLevel": 1}
	}
	sid, ok := asInt(m["skillId"])
	if !ok {
		return preparedFighter{}, errors.New("monster skill missing")
	}
	skill, ok := p.skills[sid]
	if !ok {
		return preparedFighter{}, errors.New("monster skill row missing")
	}
	typ, _ := asInt(skill["type"])
	if !supportedType(typ) {
		return preparedFighter{}, fmt.Errorf("monster skill %d unsupported", sid)
	}
	master, ok := p.monsterMaster[id]
	if !ok || len(master) < 6 {
		return preparedFighter{}, fmt.Errorf("Monster row %d missing", id)
	}
	typ, _ = strconv.ParseInt(fmt.Sprint(master[4]), 10, 64)
	size, _ := strconv.ParseInt(fmt.Sprint(master[5]), 10, 64)
	def := params[14]["rawValue"]
	name, _ := m["name"].(string)
	if name == "" {
		name = fmt.Sprintf("monster:%d", id)
	}
	weapon, ok := p.equipment[0]
	if !ok {
		return preparedFighter{}, errors.New("bare-hand equipment row 0 missing")
	}
	f := preparedFighter{name: name, human: false, team: 1, params: params, skills: []int64{sid}, levels: []int64{1}, monsterID: id, monsterType: typ, monsterSize: size, boss: boss, weapon: weapon}
	if typ != 48 && typ != 0 && typ != 1 && typ != 2 && typ != 11 && typ != 15 && typ != 18 && typ != 19 && typ != 20 && typ != 21 && typ != 22 && typ != 23 && typ != 24 && typ != 26 && typ != 27 && typ != 60 {
		return preparedFighter{}, fmt.Errorf("monster skill type %d unsupported", typ)
	}
	f.member = FormationMember{Name: name, EffectiveDefense: def, LeaderIdentity: boss, Monster: true}
	return f, nil
}

func supportedType(t int64) bool {
	return t == 0 || t == 1 || t == 2 || t == 11 || t == 15 || t == 18 || t == 19 || t == 20 || t == 21 || t == 22 || t == 23 || t == 24 || t == 26 || t == 27 || t == 48 || t == 60
}
func i32(x int64) int64 { return int64(int32(uint32(x))) }
func truncDiv(v, d int64) int64 {
	if v < 0 {
		return -((-v) / d)
	}
	return v / d
}
func min32(a, b int64) int64 {
	if a < b {
		return a
	}
	return b
}
func max32(a, b int64) int64 {
	if a > b {
		return a
	}
	return b
}
func asInt(v any) (int64, bool) {
	switch x := v.(type) {
	case json.Number:
		i, e := x.Int64()
		return i, e == nil
	case float64:
		i := int64(x)
		return i, float64(i) == x
	case int:
		return int64(x), true
	case int64:
		return x, true
	}
	return 0, false
}

type systemRandom struct {
	values         [56]int64
	index, partner int
	draws          int64
}

func newSystemRandom(seed int64) systemRandom {
	var r systemRandom
	m := seed
	if m < 0 {
		m = -m
	}
	prev := i32(161803398 - m)
	r.values[55] = prev
	cur := int64(1)
	idx := 0
	for range 54 {
		idx = (idx + 21) % 55
		r.values[idx] = cur
		dif := i32(prev - cur)
		if dif < 0 {
			dif = i32(dif + math.MaxInt32)
		}
		prev, cur = cur, dif
	}
	for range 4 {
		for i := 1; i < 56; i++ {
			v := i32(r.values[i] - r.values[1+(i+30)%55])
			if v < 0 {
				v = i32(v + math.MaxInt32)
			}
			r.values[i] = v
		}
	}
	r.index = 0
	r.partner = 21
	return r
}
func (r *systemRandom) next() int64 {
	if r.index < 55 {
		r.index++
	} else {
		r.index = 1
	}
	if r.partner < 55 {
		r.partner++
	} else {
		r.partner = 1
	}
	v := i32(r.values[r.index] - r.values[r.partner])
	if v == math.MaxInt32 {
		v--
	} else if v < 0 {
		v = i32(v + math.MaxInt32)
	}
	r.values[r.index] = v
	r.draws++
	return v
}
func randomBelow(raw, limit int64) int64 {
	m := raw
	if m < 0 {
		m = i32(-m)
	}
	return i32(m - truncDiv(m, limit)*limit)
}
func monsterParameter(c [4]int64, level int64) int64 {
	if level < 1 {
		return c[0]
	}
	if level > 10000 {
		return c[3]
	}
	var low, high, step, span int64
	if level <= 100 {
		low, high, step, span = c[0], c[1], level-1, 99
	} else if level <= 1000 {
		low, high, step, span = c[1], c[2], level-100, 900
	} else {
		low, high, step, span = c[2], c[3], level-1000, 9000
	}
	return i32(low + truncDiv(i32(i32(high-low)*step), span))
}

func (p *Preparer) effective(f preparedFighter, id int64, maximum bool) (int64, error) {
	row := f.params[id]
	if row == nil {
		return 0, errors.New("parameter missing")
	}
	bound := row["rawMax"] != math.MaxInt32
	if maximum && !bound {
		return math.MaxInt32, nil
	}
	base := row["rawValue"] + row["extraValue"]
	if maximum {
		base = row["rawMax"] + row["extraMax"]
	}
	v := i32(base)
	if maximum == bound {
		for _, eq := range f.equipment {
			v = i32(v + p.contribution(eq, id, f.human))
		}
	}
	if maximum || id == 25 {
		return v, nil
	}
	upper, err := p.effective(f, id, true)
	if err != nil {
		return 0, err
	}
	return max32(0, min32(upper, v)), nil
}
func (p *Preparer) contribution(eq map[string]any, id int64, human bool) int64 {
	pairs, ok := eq["parameters"].([]any)
	if !ok || id < 10 || id-10 >= int64(len(pairs)) {
		return 0
	}
	pair, ok := pairs[id-10].([]any)
	if !ok || len(pair) != 2 {
		return 0
	}
	base, a := asInt(pair[0])
	growth, b := asInt(pair[1])
	if !a || !b {
		return 0
	}
	lev, _ := asInt(eq["level"])
	value := i32(base + i32(growth*i32(lev-1)))
	aff, _ := asInt(eq["affinity"])
	if human && aff == 0 {
		if value > 0 {
			return max32(1, value/2)
		}
		return 0
	}
	return value
}
