package main

// Raw scenario admission and the recovered InitFighters formation slice.
// This deliberately stops before SharedControllers construction: a legal
// formation is not a native engine snapshot.
import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"sort"
)

const ScenarioSchema = "ka-special-combat-research-1"

type RawScenario struct {
	Schema           string               `json:"schema"`
	EncounterID      int64                `json:"encounterId"`
	DefeatCount      int64                `json:"defeatCount"`
	MathSeed         int64                `json:"mathSeed"`
	LibSeed          int64                `json:"libSeed"`
	TickLimit        int64                `json:"tickLimit"`
	OwnUnits         []RawUnit            `json:"ownUnits"`
	Inputs           []RawInput           `json:"inputs"`
	FinishPolicy     string               `json:"finishPolicy,omitempty"`
	HousePets        map[string][]RawUnit `json:"housePets,omitempty"`
	HouseholdOwners  []string             `json:"householdOwners,omitempty"`
	PreparedOwnUnits []RawUnit            `json:"-"`
	Raw              json.RawMessage      `json:"-"`
}

// RawUnit keeps nested source data opaque after checking its required fields.
// Actual catalog-backed skill/equipment/stat admission is not claimed here.
type RawUnit struct {
	Name           string          `json:"name"`
	Human          *bool           `json:"human"`
	MonsterID      json.RawMessage `json:"monsterId"`
	Parameters     json.RawMessage `json:"parameters"`
	Skills         []int64         `json:"skills"`
	Invocation     []int64         `json:"invocationLevels"`
	WeaponID       *int64          `json:"weaponId"`
	Equipment      json.RawMessage `json:"equipment"`
	Visitor        bool            `json:"visitor"`
	LeaderIdentity bool            `json:"leaderIdentity"`
	HumanFlags     int64           `json:"humanFlags,omitempty"`
	OnVehicle      bool            `json:"onVehicle,omitempty"`
	ParameterLinks json.RawMessage `json:"parameterLinks,omitempty"`
	InvokingSkills [][]int64       `json:"invokingSkills,omitempty"`
	OwnerPlayer    bool            `json:"ownerPlayer,omitempty"`
	Friend         bool            `json:"friend,omitempty"`
	IsHouseOwner   bool            `json:"isHouseOwner,omitempty"`
	PetOwnerName   *string         `json:"petOwnerName,omitempty"`
}

type RawInput struct {
	Tick   int64           `json:"tick"`
	Type   string          `json:"type"`
	Phase  string          `json:"phase"`
	Item   json.RawMessage `json:"item,omitempty"`
	Target json.RawMessage `json:"target,omitempty"`
}

type FormationMember struct {
	Name             string
	EffectiveDefense int64 // Must come from canonical stat preparation, never display stats.
	FormationValue   *int64
	Visitor          bool
	LeaderIdentity   bool
	Monster          bool
	OwnerPlayer      bool
}

type Placement struct {
	IncomingIndex int      `json:"incomingIndex"`
	Name          string   `json:"name"`
	Priority      int64    `json:"priority"`
	Grid          int      `json:"grid"`
	Row           int      `json:"row"`
	Column        int      `json:"column"`
	Cell          [2]int64 `json:"cell"`
}

type PreparedFormation struct {
	TeamID     int         `json:"teamId"`
	RowOffset  int64       `json:"rowOffset"`
	Order      []int       `json:"order"`
	Placements []Placement `json:"placements"`
}

// AdmitRawScenario decodes exactly one JSON object, preserves its original bytes,
// and validates the common scalar/structural contract before deeper catalog work.
func AdmitRawScenario(data []byte) (RawScenario, error) {
	var s RawScenario
	var top map[string]json.RawMessage
	if err := json.Unmarshal(data, &top); err != nil {
		return s, fmt.Errorf("scenario object: %w", err)
	}
	for _, key := range []string{"schema", "encounterId", "defeatCount", "mathSeed", "libSeed", "tickLimit", "ownUnits", "inputs"} {
		if _, ok := top[key]; !ok {
			return s, fmt.Errorf("missing required scenario field %q", key)
		}
	}
	d := json.NewDecoder(bytes.NewReader(data))
	if err := d.Decode(&s); err != nil {
		return s, fmt.Errorf("scenario JSON: %w", err)
	}
	var extra any
	if err := d.Decode(&extra); err != io.EOF {
		return s, errors.New("scenario must contain exactly one JSON value")
	}
	s.Raw = append(json.RawMessage(nil), data...)
	if s.Schema != ScenarioSchema {
		return s, errors.New("unsupported scenario schema")
	}
	if s.EncounterID < 0 || s.EncounterID >= 20 || s.DefeatCount < 0 || s.TickLimit < 1 {
		return s, errors.New("encounterId, defeatCount or tickLimit outside supported bounds")
	}
	if s.MathSeed < 0 || s.MathSeed > math.MaxInt32 || s.LibSeed < 0 || s.LibSeed > math.MaxInt32 {
		return s, errors.New("canonical optimizer seeds must be nonnegative signed31-bit values")
	}
	if len(s.OwnUnits) == 0 {
		return s, errors.New("explicit own roster required")
	}
	if s.FinishPolicy != "" && s.FinishPolicy != "at-horizon" && s.FinishPolicy != "after-ending" && s.FinishPolicy != "on-verdict" {
		return s, errors.New("unsupported finishPolicy")
	}
	seen := make(map[string]bool, len(s.OwnUnits))
	var unitRows []json.RawMessage
	if err := json.Unmarshal(top["ownUnits"], &unitRows); err != nil {
		return s, errors.New("ownUnits must be an array")
	}
	if len(unitRows) != len(s.OwnUnits) {
		return s, errors.New("ownUnits must be an array of objects")
	}
	for i, u := range s.OwnUnits {
		if err := validateRawUnit(u, unitRows[i], i, seen); err != nil {
			return s, err
		}
	}
	preparedUnits, housePets, householdOwners, err := materializeHouseholdPets(top, s.OwnUnits, seen)
	if err != nil {
		return s, err
	}
	s.HousePets = housePets
	s.HouseholdOwners = householdOwners
	s.PreparedOwnUnits = preparedUnits
	var inputRows []json.RawMessage
	if err := json.Unmarshal(top["inputs"], &inputRows); err != nil || inputRows == nil || len(inputRows) != len(s.Inputs) {
		return s, errors.New("inputs must be an array of input objects")
	}
	for i, in := range s.Inputs {
		var fields map[string]json.RawMessage
		if err := json.Unmarshal(inputRows[i], &fields); err != nil || fields == nil {
			return s, fmt.Errorf("inputs[%d] must be an object", i)
		}
		for _, key := range []string{"tick", "type", "phase"} {
			if _, ok := fields[key]; !ok {
				return s, fmt.Errorf("inputs[%d]: missing %s", i, key)
			}
		}
		if in.Tick < 0 || in.Tick > math.MaxInt32 || (in.Type != "holy_herb" && in.Type != "item" && in.Type != "finish") || (in.Phase != "before_fighters" && in.Phase != "after_fighters") {
			return s, fmt.Errorf("inputs[%d]: unsupported input row", i)
		}
		if in.Type == "item" {
			for _, key := range []string{"item", "target"} {
				if _, ok := fields[key]; !ok {
					return s, fmt.Errorf("inputs[%d]: item input requires %s", i, key)
				}
			}
			var itemName, target string
			if err := json.Unmarshal(in.Item, &itemName); err != nil {
				return s, fmt.Errorf("inputs[%d]: item must be a string", i)
			}
			if err := json.Unmarshal(in.Target, &target); err != nil || target != "all" {
				return s, fmt.Errorf("inputs[%d]: item target must be all", i)
			}
		}
	}
	return s, nil
}

func validateRawUnit(u RawUnit, raw json.RawMessage, index int, seen map[string]bool) error {
	label := fmt.Sprintf("ownUnits[%d]", index)
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(raw, &fields); err != nil || fields == nil {
		return fmt.Errorf("%s must be an object", label)
	}
	for _, key := range []string{"name", "human", "monsterId", "parameters", "skills", "invocationLevels", "weaponId", "equipment", "visitor", "leaderIdentity"} {
		if _, ok := fields[key]; !ok {
			return fmt.Errorf("%s: missing required field %q", label, key)
		}
	}
	if u.Name == "" || seen[u.Name] {
		return fmt.Errorf("%s: empty or duplicate name", label)
	}
	seen[u.Name] = true
	if u.Human == nil || u.WeaponID == nil || len(u.Parameters) == 0 || len(u.Equipment) == 0 {
		return fmt.Errorf("%s: missing explicit identity, parameters, weapon or equipment", label)
	}
	if len(u.MonsterID) == 0 {
		return fmt.Errorf("%s: missing monsterId", label)
	}
	monsterIsNull := bytes.Equal(bytes.TrimSpace(u.MonsterID), []byte("null"))
	if !monsterIsNull {
		var id int64
		if err := json.Unmarshal(u.MonsterID, &id); err != nil || id < 0 {
			return fmt.Errorf("%s: invalid monsterId", label)
		}
	}
	if (*u.Human && !monsterIsNull) || (!*u.Human && monsterIsNull) {
		return fmt.Errorf("%s: must be exactly human or monster", label)
	}
	if u.OnVehicle || u.HumanFlags&4 != 0 {
		return fmt.Errorf("%s: vehicle source components are unsupported", label)
	}
	if !*u.Human && u.HumanFlags != 0 {
		return fmt.Errorf("%s: monster has human flags", label)
	}
	if u.Friend {
		return fmt.Errorf("%s: friend source components are unsupported", label)
	}
	if len(u.ParameterLinks) != 0 && string(u.ParameterLinks) != "null" && string(u.ParameterLinks) != "[]" {
		return fmt.Errorf("%s: parameterLinks are unsupported", label)
	}
	if len(u.Skills) != len(u.Invocation) {
		return fmt.Errorf("%s: every skill slot needs an invocation setting", label)
	}
	for _, level := range u.Invocation {
		if level < 0 || level > 2 {
			return fmt.Errorf("%s: invocation setting outside 0..2", label)
		}
	}
	for _, pair := range u.InvokingSkills {
		if len(pair) != 2 || pair[0] < 0 || pair[1] < 1 || pair[1] > math.MaxInt32 {
			return fmt.Errorf("%s: invalid invokingSkills pair", label)
		}
	}
	return nil
}

// materializeHouseholdPets mirrors combat_household_pets: callers provide each selected human
// house-owner's complete ordered lookup result; ownership is never inferred from pet data.
func materializeHouseholdPets(top map[string]json.RawMessage, selected []RawUnit, seen map[string]bool) ([]RawUnit, map[string][]RawUnit, []string, error) {
	households := map[string][]RawUnit{}
	if raw, present := top["housePets"]; present {
		trimmed := bytes.TrimSpace(raw)
		if len(trimmed) == 0 || bytes.Equal(trimmed, []byte("null")) {
			return nil, nil, nil, errors.New("housePets must map owner names to pets")
		}
		var rowsByOwner map[string]json.RawMessage
		if err := json.Unmarshal(trimmed, &rowsByOwner); err != nil || rowsByOwner == nil {
			return nil, nil, nil, errors.New("housePets must map owner names to pets")
		}
		keys := make([]string, 0, len(rowsByOwner))
		for owner := range rowsByOwner {
			keys = append(keys, owner)
		}
		sort.Strings(keys)
		for _, owner := range keys {
			petRaw := bytes.TrimSpace(rowsByOwner[owner])
			var rows []json.RawMessage
			if len(petRaw) == 0 || bytes.Equal(petRaw, []byte("null")) || json.Unmarshal(petRaw, &rows) != nil || rows == nil {
				return nil, nil, nil, fmt.Errorf("housePets[%q] must be a list of pets", owner)
			}
			pets := make([]RawUnit, 0, len(rows))
			for _, row := range rows {
				var pet RawUnit
				if err := json.Unmarshal(row, &pet); err != nil {
					return nil, nil, nil, fmt.Errorf("housePets[%q] contains an invalid pet: %w", owner, err)
				}
				index := len(selected) + len(pets)
				if err := validateRawUnit(pet, row, index, seen); err != nil {
					return nil, nil, nil, err
				}
				if pet.Human == nil || *pet.Human || bytes.Equal(bytes.TrimSpace(pet.MonsterID), []byte("null")) {
					return nil, nil, nil, fmt.Errorf("housePets[%q] contains a non-ally monster", owner)
				}
				ownerName := owner
				pet.PetOwnerName = &ownerName
				pets = append(pets, pet)
			}
			households[owner] = pets
		}
	}

	materialized := []string{}
	if raw, present := top["householdOwners"]; present {
		trimmed := bytes.TrimSpace(raw)
		if len(trimmed) == 0 || bytes.Equal(trimmed, []byte("null")) || json.Unmarshal(trimmed, &materialized) != nil || materialized == nil {
			return nil, nil, nil, errors.New("householdOwners must be a list of owner names")
		}
	}
	selectedNames := make(map[string]bool, len(selected))
	ownerSet := map[string]bool{}
	ownerOrder := make([]string, 0)
	inline := map[string]bool{}
	for _, unit := range selected {
		selectedNames[unit.Name] = true
		if unit.Human != nil && *unit.Human && unit.IsHouseOwner {
			ownerSet[unit.Name] = true
			ownerOrder = append(ownerOrder, unit.Name)
		}
		if unit.PetOwnerName != nil {
			owner := *unit.PetOwnerName
			if !ownerSet[owner] && !isSelectedHouseOwner(selected, owner) {
				return nil, nil, nil, fmt.Errorf("inline pet owner %q is not a selected human house owner", owner)
			}
			if unit.Human == nil || *unit.Human || bytes.Equal(bytes.TrimSpace(unit.MonsterID), []byte("null")) {
				return nil, nil, nil, fmt.Errorf("inline pet %q is not an ally monster", unit.Name)
			}
			inline[owner] = true
		}
	}
	for _, owner := range materialized {
		if !ownerSet[owner] {
			return nil, nil, nil, fmt.Errorf("materialized household %q is not a selected human house owner", owner)
		}
	}
	for owner := range households {
		if !selectedNames[owner] {
			return nil, nil, nil, fmt.Errorf("pet owner %q is not a selected unit", owner)
		}
		if !ownerSet[owner] {
			return nil, nil, nil, fmt.Errorf("pet owner %q is not a selected human house owner", owner)
		}
		if inline[owner] {
			return nil, nil, nil, fmt.Errorf("household %q is already materialized inline", owner)
		}
	}
	known := map[string]bool{}
	for owner := range households {
		known[owner] = true
	}
	for owner := range inline {
		known[owner] = true
	}
	for _, owner := range materialized {
		known[owner] = true
	}
	for _, owner := range ownerOrder {
		if !known[owner] {
			return nil, nil, nil, fmt.Errorf("selected human house owner %q has no declared household; use [] for no pets", owner)
		}
	}

	expanded := append([]RawUnit(nil), selected...)
	for _, owner := range ownerOrder {
		expanded = append(expanded, households[owner]...)
	}
	return expanded, households, ownerOrder, nil
}

func isSelectedHouseOwner(units []RawUnit, name string) bool {
	for _, unit := range units {
		if unit.Name == name {
			return unit.Human != nil && *unit.Human && unit.IsHouseOwner
		}
	}
	return false
}

// PrepareFormation reproduces combat_initial_state.formation over members whose
// effective defense and first type-60 skill were already computed from raw stats.
// Priority table is the recovered six-entry static field [0,3,6,9,10,13].
func PrepareFormation(members []FormationMember, teamID int, opponentCount int) (PreparedFormation, error) {
	var out PreparedFormation
	if teamID != 0 && teamID != 1 {
		return out, errors.New("team id must be 0 or 1")
	}
	if opponentCount < 0 || len(members) == 0 {
		return out, errors.New("opponent count must be nonnegative and members nonempty")
	}
	type ranked struct {
		i int
		m FormationMember
		p int64
	}
	rows := make([]ranked, 0, len(members))
	priorities := [...]int64{0, 3, 6, 9, 10, 13}
	for i, m := range members {
		if m.Name == "" {
			return out, fmt.Errorf("member %d has empty name", i)
		}
		category := int64(2)
		if m.FormationValue != nil {
			category = *m.FormationValue
		}
		if m.Visitor {
			category = 3
		}
		if m.LeaderIdentity {
			category = 5
		}
		if category < 0 || category >= int64(len(priorities)) {
			return out, fmt.Errorf("member %d formation category unsupported", i)
		}
		p := priorities[category] + boolInt(m.Monster) + 2*boolInt(m.OwnerPlayer)
		rows = append(rows, ranked{i, m, p})
	}
	less := func(a, b ranked) bool {
		if a.p != b.p {
			return a.p < b.p
		}
		if a.m.EffectiveDefense != b.m.EffectiveDefense {
			return a.m.EffectiveDefense > b.m.EffectiveDefense
		}
		return a.i < b.i
	}
	// Stable insertion sort avoids importing a library unavailable in the
	// restricted offline Go toolchain snapshot used by this prototype.
	for i := 1; i < len(rows); i++ {
		v, j := rows[i], i
		for j > 0 && less(v, rows[j-1]) {
			rows[j] = rows[j-1]
			j--
		}
		rows[j] = v
	}
	offset := int64(3)
	if candidate := int64(opponentCount/5 + 1); candidate > offset {
		offset = candidate
	}
	out = PreparedFormation{TeamID: teamID, RowOffset: offset, Order: make([]int, len(rows)), Placements: make([]Placement, len(rows))}
	for grid, r := range rows {
		row, col := grid/5, grid%5
		y := offset + 1 + int64(row)
		if teamID == 1 {
			y = offset - int64(row)
		}
		out.Order[grid] = r.i
		out.Placements[r.i] = Placement{IncomingIndex: r.i, Name: r.m.Name, Priority: r.p, Grid: grid, Row: row, Column: col, Cell: [2]int64{int64(col), y}}
	}
	return out, nil
}

func boolInt(v bool) int64 {
	if v {
		return 1
	}
	return 0
}
