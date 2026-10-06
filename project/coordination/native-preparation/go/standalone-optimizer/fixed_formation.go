package main

// Fixed-formation policy enforcement for the Go engine.
//
// Latest explicit user scope (2026-10-06) supersedes the historical formation/search rules: the
// search must run one fixed six-unit formation and vary ONLY the Synthetic DPS's raw stats inside
// the derived synthetic walls. Everything else (roster order, fodder/healer identities and numbers,
// skill lists and order, triggers, weapons, equipment, formation) is pinned. This file is the single
// Go enforcement point: admission (NewSearch / MergeSeeds / Next / Defer) and candidate generation
// (Next) both go through it, so a historical, incompatible candidate can never bypass the policy.
import (
	"bytes"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"sort"
)

//go:embed fixed_formation_policy.json
var fixedFormationPolicyJSON []byte

type ffParameter struct {
	RawValue      int64 `json:"rawValue"`
	RawMax        int64 `json:"rawMax"`
	ExtraValue    int64 `json:"extraValue"`
	ExtraMax      int64 `json:"extraMax"`
	TrainingLevel int64 `json:"trainingLevel"`
}

type ffBounds struct {
	Minimum int64
	Maximum int64
}

type ffPolicy struct {
	Schema    string `json:"schema"`
	PolicyHash string `json:"policyHash"`
	Fixed     struct {
		RosterOrder    []string `json:"rosterOrder"`
		PlacementRoles []string `json:"placementRoles"`
		WeaponID       int64    `json:"weaponId"`
		UnitCount      int      `json:"unitCount"`
		FodderCount    int      `json:"fodderCount"`
	} `json:"fixed"`
	DPS struct {
		Name                 string                `json:"name"`
		Skills               []int64               `json:"skills"`
		InvocationLevels     []int64               `json:"invocationLevels"`
		WeaponID             int64                 `json:"weaponId"`
		SearchableParameters map[string][2]int64   `json:"searchableParameters"`
		Defaults             map[string]map[string]ffParameter `json:"defaults"`
	} `json:"dps"`
	Healer ffUnitTemplate `json:"healer"`
	Fodder ffUnitTemplate `json:"fodder"`
}

type ffUnitTemplate struct {
	Name             string                 `json:"name"`
	Names            []string               `json:"names"`
	Skills           []int64                `json:"skills"`
	InvocationLevels []int64                `json:"invocationLevels"`
	WeaponID         int64                  `json:"weaponId"`
	Parameters       map[string]ffParameter `json:"parameters"`
}

type ffUnit struct {
	Name             string                 `json:"name"`
	Human            *bool                  `json:"human"`
	MonsterID        json.RawMessage        `json:"monsterId"`
	LeaderIdentity   bool                   `json:"leaderIdentity"`
	Visitor          bool                   `json:"visitor"`
	WeaponID         *int64                 `json:"weaponId"`
	Equipment        []json.RawMessage      `json:"equipment"`
	Skills           []int64                `json:"skills"`
	InvocationLevels []int64                `json:"invocationLevels"`
	Parameters       map[string]ffParameter `json:"parameters"`
}

var fixedFormationPolicyCache *ffPolicy
var fixedFormationPolicyErr error
var fixedFormationPolicyOnce bool

func loadFixedFormationPolicy() (*ffPolicy, error) {
	if !fixedFormationPolicyOnce {
		fixedFormationPolicyOnce = true
		var policy ffPolicy
		if err := json.Unmarshal(fixedFormationPolicyJSON, &policy); err != nil {
			fixedFormationPolicyErr = fmt.Errorf("decode fixed-formation policy: %w", err)
		} else if policy.Schema != "ka-fixed-formation-policy-1" {
			fixedFormationPolicyErr = errors.New("fixed-formation policy has an unexpected schema")
		} else if len(policy.Fixed.RosterOrder) != 6 || len(policy.DPS.Skills) != 6 {
			fixedFormationPolicyErr = errors.New("fixed-formation policy is incomplete")
		} else {
			fixedFormationPolicyCache = &policy
		}
	}
	return fixedFormationPolicyCache, fixedFormationPolicyErr
}

// FixedFormationPolicyHash is the canonical policy digest, folded into the search policy hash so a
// checkpoint made under a different policy can never be resumed silently.
func FixedFormationPolicyHash() string {
	policy, err := loadFixedFormationPolicy()
	if err != nil {
		return ""
	}
	return policy.PolicyHash
}

// FixedFormationSearchableParameters returns the DPS raw-stat walls, for tests and evidence.
func FixedFormationSearchableParameters() (map[int64]ffBounds, error) {
	policy, err := loadFixedFormationPolicy()
	if err != nil {
		return nil, err
	}
	out := make(map[int64]ffBounds, len(policy.DPS.SearchableParameters))
	for key, interval := range policy.DPS.SearchableParameters {
		var pid int64
		if _, err := fmt.Sscan(key, &pid); err != nil {
			return nil, fmt.Errorf("bad searchable parameter key %q", key)
		}
		out[pid] = ffBounds{Minimum: interval[0], Maximum: interval[1]}
	}
	return out, nil
}

func ffBounded(parameter int64) bool { return parameter == 10 || parameter == 11 || parameter == 12 }

func ffValue(parameter int64, entry ffParameter) int64 {
	if ffBounded(parameter) {
		return entry.RawMax
	}
	return entry.RawValue
}

func ffSameParameter(a, b ffParameter) bool {
	return a.RawValue == b.RawValue && a.RawMax == b.RawMax && a.ExtraValue == b.ExtraValue && a.ExtraMax == b.ExtraMax && a.TrainingLevel == b.TrainingLevel
}

// ValidateFixedFormation refuses any raw scenario that is not the one fixed search point (for its
// encounter) after replacing only the Synthetic DPS's searched raw stats. It is deliberately strict:
// an unknown unit, a mutated fodder/healer value, a different skill order, a weapon, any equipment or
// a permuted fodder all throw, so no incompatible candidate can be admitted.
func ValidateFixedFormation(raw json.RawMessage) error {
	policy, err := loadFixedFormationPolicy()
	if err != nil {
		return err
	}
	var root map[string]json.RawMessage
	if err := json.Unmarshal(raw, &root); err != nil {
		return fmt.Errorf("fixed-formation scenario: %w", err)
	}
	var encounterID int64
	if err := json.Unmarshal(root["encounterId"], &encounterID); err != nil {
		return errors.New("fixed-formation scenario needs an integer encounterId")
	}
	var units []json.RawMessage
	if err := json.Unmarshal(root["ownUnits"], &units); err != nil {
		return errors.New("fixed-formation scenario needs ownUnits")
	}
	if len(units) != policy.Fixed.UnitCount {
		return fmt.Errorf("fixed formation needs exactly %d units, found %d", policy.Fixed.UnitCount, len(units))
	}
	parsed := make([]ffUnit, len(units))
	for i, rawUnit := range units {
		if err := json.Unmarshal(rawUnit, &parsed[i]); err != nil {
			return fmt.Errorf("ownUnits[%d]: %w", i, err)
		}
		if parsed[i].Human == nil || !*parsed[i].Human {
			return fmt.Errorf("fixed formation unit %d is not human", i)
		}
		if !bytes.Equal(bytes.TrimSpace(parsed[i].MonsterID), []byte("null")) && len(bytes.TrimSpace(parsed[i].MonsterID)) > 0 {
			return fmt.Errorf("fixed formation unit %d must not be a monster", i)
		}
		if parsed[i].LeaderIdentity || parsed[i].Visitor {
			return fmt.Errorf("fixed formation unit %d must not be a leader or visitor", i)
		}
		if len(parsed[i].Equipment) != 0 {
			return fmt.Errorf("fixed formation unit %d must carry no equipment", i)
		}
		if parsed[i].WeaponID == nil || *parsed[i].WeaponID != policy.Fixed.WeaponID {
			return fmt.Errorf("fixed formation unit %d must carry no weapon", i)
		}
		if parsed[i].Name != policy.Fixed.RosterOrder[i] {
			return fmt.Errorf("fixed formation roster order mismatch at %d: %q", i, parsed[i].Name)
		}
	}

	// Synthetic DPS: only the seven searched raw stats may differ from the encounter's template.
	defaults, ok := policy.DPS.Defaults[fmt.Sprint(encounterID)]
	if !ok {
		return fmt.Errorf("no fixed-formation DPS template for encounter %d", encounterID)
	}
	dps := parsed[0]
	if !ffSameSkills(dps.Skills, policy.DPS.Skills) || !ffSameSkills(dps.InvocationLevels, policy.DPS.InvocationLevels) {
		return errors.New("Synthetic DPS skills/triggers must be the fixed Counter/7/5/4/3/2 High set")
	}
	if len(dps.Parameters) != len(defaults) {
		return errors.New("Synthetic DPS must carry exactly the canonical twelve parameters")
	}
	bounds, err := FixedFormationSearchableParameters()
	if err != nil {
		return err
	}
	for key, entry := range dps.Parameters {
		pid, ok := ffParameterID(key)
		if !ok {
			return fmt.Errorf("Synthetic DPS carries unknown parameter %q", key)
		}
		if interval, searchable := bounds[pid]; searchable {
			if entry.ExtraValue != 0 || entry.ExtraMax != 0 {
				return fmt.Errorf("Synthetic DPS parameter %d must have zero extra (no equipment)", pid)
			}
			value := ffValue(pid, entry)
			if value < interval.Minimum || value > interval.Maximum {
				return fmt.Errorf("Synthetic DPS parameter %d value %d outside [%d, %d]", pid, value, interval.Minimum, interval.Maximum)
			}
			if expected, ok := defaults[key]; ok && entry.TrainingLevel != expected.TrainingLevel {
				return fmt.Errorf("Synthetic DPS parameter %d trainingLevel must match the template", pid)
			}
		} else if expected, ok := defaults[key]; !ok || !ffSameParameter(entry, expected) {
			return fmt.Errorf("Synthetic DPS parameter %q is fixed and must match the template", key)
		}
	}
	for key := range defaults {
		if _, ok := dps.Parameters[key]; !ok {
			return errors.New("Synthetic DPS is missing a canonical parameter")
		}
	}

	// Fixed healer and the four identical fodders.
	if err := ffCheckFixedUnit(parsed[1], policy.Healer, "healer"); err != nil {
		return err
	}
	for i := 2; i < policy.Fixed.UnitCount; i++ {
		if err := ffCheckFixedUnit(parsed[i], policy.Fodder, fmt.Sprintf("fodder %d", i)); err != nil {
			return err
		}
	}
	return nil
}

func ffCheckFixedUnit(unit ffUnit, template ffUnitTemplate, label string) error {
	if unit.WeaponID == nil || *unit.WeaponID != template.WeaponID {
		return fmt.Errorf("%s must carry no weapon", label)
	}
	if len(unit.Equipment) != 0 {
		return fmt.Errorf("%s must carry no equipment", label)
	}
	if !ffSameSkills(unit.Skills, template.Skills) || !ffSameSkills(unit.InvocationLevels, template.InvocationLevels) {
		return fmt.Errorf("%s skills/triggers do not match the fixed template", label)
	}
	if len(unit.Parameters) != len(template.Parameters) {
		return fmt.Errorf("%s parameter blocks do not match the fixed template", label)
	}
	for key, entry := range unit.Parameters {
		expected, ok := template.Parameters[key]
		if !ok || !ffSameParameter(entry, expected) {
			return fmt.Errorf("%s parameter %q is fixed and must not mutate", label, key)
		}
	}
	return nil
}

func ffSameSkills(a, b []int64) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func ffParameterID(key string) (int64, bool) {
	var pid int64
	_, err := fmt.Sscan(key, &pid)
	if err != nil {
		return 0, false
	}
	return pid, true
}

// FixedFormationIdentity is the canonical identity of everything the policy lets the search vary plus
// the fight identity. Because admission pins every other field, a reordered-but-identical fodder set
// or a carrier-equipment swap that changes nothing the engine reads cannot create a new identity.
func FixedFormationIdentity(raw json.RawMessage) (string, error) {
	var root map[string]json.RawMessage
	if err := json.Unmarshal(raw, &root); err != nil {
		return "", err
	}
	var units []ffUnit
	if err := json.Unmarshal(root["ownUnits"], &units); err != nil || len(units) != 6 {
		return "", errors.New("fixed-formation identity needs six units")
	}
	bounds, err := FixedFormationSearchableParameters()
	if err != nil {
		return "", err
	}
	stats := map[string]int64{}
	for pid := range bounds {
		entry, ok := units[0].Parameters[fmt.Sprint(pid)]
		if !ok {
			return "", fmt.Errorf("Synthetic DPS is missing parameter %d", pid)
		}
		stats[fmt.Sprint(pid)] = ffValue(pid, entry)
	}
	projection := map[string]any{
		"encounterId":   json.RawMessage(root["encounterId"]),
		"defeatCount":   json.RawMessage(root["defeatCount"]),
		"tickLimit":     json.RawMessage(root["tickLimit"]),
		"holyHerbStock": json.RawMessage(root["holyHerbStock"]),
		"inputs":        json.RawMessage(root["inputs"]),
		"dpsStats":      stats,
	}
	encoded, err := json.Marshal(projection)
	if err != nil {
		return "", err
	}
	return digest(encoded), nil
}

// mutateFixedFormation generates one child that changes exactly one Synthetic DPS raw stat, still
// inside the walls. It is the ONLY candidate generator used when the fixed policy is active, so no
// skill, weapon, equipment, roster or fodder change can ever be proposed.
func mutateFixedFormation(raw json.RawMessage, random func() uint64) (json.RawMessage, error) {
	if err := ValidateFixedFormation(raw); err != nil {
		return nil, err
	}
	var root map[string]json.RawMessage
	if err := json.Unmarshal(raw, &root); err != nil {
		return nil, err
	}
	var units []map[string]json.RawMessage
	if err := json.Unmarshal(root["ownUnits"], &units); err != nil || len(units) != 6 {
		return nil, errors.New("fixed-formation mutation needs six units")
	}
	var encounterID int64
	_ = json.Unmarshal(root["encounterId"], &encounterID)
	if _, err := loadFixedFormationPolicy(); err != nil {
		return nil, err
	}
	bounds, err := FixedFormationSearchableParameters()
	if err != nil {
		return nil, err
	}
	var parameters map[string]ffParameter
	if err := json.Unmarshal(units[0]["parameters"], &parameters); err != nil {
		return nil, err
	}
	ids := make([]int64, 0, len(bounds))
	for pid := range bounds {
		ids = append(ids, pid)
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
	start := int(random() % uint64(len(ids)))
	for offset := 0; offset < len(ids); offset++ {
		pid := ids[(start+offset)%len(ids)]
		key := fmt.Sprint(pid)
		entry := parameters[key]
		interval := bounds[pid]
		current := ffValue(pid, entry)
		for _, factor := range []float64{1.15, 1.35, 1.6, 0.85, 0.7} {
			target := int64(math.Round(float64(current) * factor))
			if target < interval.Minimum {
				target = interval.Minimum
			}
			if target > interval.Maximum {
				target = interval.Maximum
			}
			if target == current {
				continue
			}
			updated := entry
			if ffBounded(pid) {
				updated.RawValue, updated.RawMax = target, target
				updated.ExtraValue, updated.ExtraMax = 0, 0
			} else {
				updated.RawValue, updated.ExtraValue = target, 0
			}
			parameters[key] = updated
			encoded, _ := json.Marshal(parameters)
			units[0]["parameters"] = encoded
			root["ownUnits"], _ = json.Marshal(units)
			child, _ := json.Marshal(root)
			if err := ValidateFixedFormation(child); err != nil {
				parameters[key] = entry
				continue
			}
			return child, nil
		}
	}
	return nil, errors.New("no legal Synthetic DPS stat step inside the fixed walls")
}
