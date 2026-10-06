//go:build windows

package main

// This file is a Go mirror of ka_abi.load_snapshot and the canonical native
// setter ABI. It contains only data marshalling; battle execution remains in
// the resident ka_kernel DLL.
import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strconv"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
	"unsafe"
)

const (
	nativeUnitLimit           = 32
	nativeObjectLimit         = 32768
	nativeSubsetCount         = 11
	nativeBucketLimit         = 2048
	nativeRowsLimit           = 64
	nativeEffectResourceLimit = 48
	nativePrizeLimit          = 16
	nativeInputLimit          = 32
	nativeItemLimit           = 8
	nativeSlotCount           = 52
	nativeBoardLimit          = 48
)

type nVec3 struct{ X, Y, Z float32 }
type nModifier struct {
	Type                                      int32
	OffsetX, OffsetY, OffsetZ, ScaleX, ScaleY float32
	Angle, Anchor, Frame, Duration            int32
	DestroyOnFinish, Looping                  uint8
	Alpha                                     int32
}
type nEffect struct {
	Type, Value1, Value2           int32
	Depth                          uint8
	Frame, MaxFrame, Parent, Scale int32
}
type nProjectile struct {
	Start, End                          nVec3
	Speed, Height, Frame, Length, Owner int32
}
type nEntity struct {
	ID               int32
	Destroyed        uint8
	Has              [nativeSlotCount]uint8
	Position, Offset nVec3
	Parent           int32
	Speed            nVec3
	Seb              [4]int32
	Depth, Cell      [2]int32
	Image            [6]int32
	Animation        [2]int32
	Direction        int32
	Modifier         nModifier
	Effect           nEffect
	Projectile       nProjectile
	Attack, Garbage  int32
}
type nParam struct{ ID, RawValue, ExtraValue, RawMax, ExtraMax, TrainingLevel int32 }
type nEquip struct {
	Level, PvPLevel, Affinity, PairCount int32
	Pairs                                [16][2]int32
	Present                              [16]uint8
}
type nParams struct {
	Count          uint32
	Rows           [16]nParam
	EquipmentCount uint32
	Equipment      [8]nEquip
}
type nEntry struct {
	Key   int32
	Value int64
}
type nBoard struct {
	Len       uint32
	Entries   [nativeBoardLimit]nEntry
	Positions [128]uint8
}
type nSkill struct{ ID, Category, Kind, Flags, MinMP, MaxMP, RequiredEquipType, ShootingRange, Range, Count, Motion, Value, Seb, Img, ImpactImg, ImpactSeb int32 }
type nCommand struct{ Opcode, Target, Skill, Tick, Duration, UseIndex int32 }
type nInvoke struct{ Skill, Remaining int32 }
type nPoint struct{ X, Y int32 }
type nUnit struct {
	Present, Team, Human, Monster                                       uint8
	Flags, ID, Identity                                                 int32
	Body                                                                nEntity
	Params                                                              nParams
	WeaponType, WeaponShootingRange, WeaponMotion, WeaponProjectileFlag int32
	Boss                                                                uint8
	MonsterType                                                         int32
	SpecialHuman                                                        uint8
	MonsterSize, HumanFlag                                              int32
	Board, LongBoard                                                    nBoard
	SkillIDs                                                            [12]int32
	SkillCount                                                          uint32
	Levels                                                              [12]int32
	LevelCount                                                          uint32
	Invoking                                                            [64]nInvoke
	InvokingCount                                                       uint32
	Commands                                                            [1024]nCommand
	CommandCount                                                        uint32
	Path                                                                [8]nPoint
	PathCount                                                           uint32
}

// nEncounterReport mirrors battle_control::KaEncounterReport (version 3).
// It is an optional additive export set; the core battle report remains stable.
type nEncounterReport struct {
	Version, OwnCount                                                                        int32
	OwnIdentity, OwnFirstDeathTick, OwnFirstLeavingTick, OwnSurvived                         [32]int32
	EnemyResolvedAttacks, EnemyHits, EnemyMisses, EnemyRolls, EnemyRollHits, EnemyRollMisses int32
	CounterChecks, CounterEnqueues, BossPostdeathAttempts, BossPostdeathLands                int32
	BossDeathTick, BossReentries, BossLeavings                                               int32
	BossPostdeathGapCount, BossPostdeathGapMin, BossPostdeathGapMax, BossPostdeathGapSum     int32
	FutureHitsAtDeath, FutureHitsIncludeExecuting                                            int32
	BossAccessFirstCommand, BossAccessFirstAttempt, TargetableTicks, UsingSkillTicks         int32
	BossDamagingResets, ItemUsesOK, FinishDispatchedChests                                   int32
}

// The JSON structs mirror the stable output of ka_abi.engine_snapshot.
type snapshotConfig struct {
	Tick            int64 `json:"tick"`
	FirstIdentity   int32 `json:"first_identity"`
	NextIdentity    int32 `json:"next_identity"`
	NextCommand     int32 `json:"next_command"`
	MapWidth        int32 `json:"map_width"`
	RowOffset       int32 `json:"row_offset"`
	MovementEnabled bool  `json:"movement_enabled"`
	BattleState     int32 `json:"battle_state"`
	BattleFrame     int32 `json:"battle_frame"`
	Verdict         int32 `json:"verdict"`
	VerdictTick     int32 `json:"verdict_tick"`
	EndingCounter   int32 `json:"ending_counter"`
	EndingGateTick  int32 `json:"ending_gate_tick"`
	PrizeCount      int32 `json:"prize_count"`
}
type snapshotUnit struct {
	Identity     int32                      `json:"identity"`
	Present      uint8                      `json:"present"`
	ID           int32                      `json:"id"`
	Team         uint8                      `json:"team"`
	Human        bool                       `json:"human"`
	Monster      bool                       `json:"monster"`
	Flags        int32                      `json:"flags"`
	Destroyed    bool                       `json:"destroyed"`
	PresentSlots []int                      `json:"present_slots"`
	Components   map[string]json.RawMessage `json:"components"`
	Board        map[int]int64              `json:"board"`
	LongBoard    map[int]int64              `json:"long_board"`
	Parameters   map[int]struct {
		RawValue      int32 `json:"rawValue"`
		ExtraValue    int32 `json:"extraValue"`
		RawMax        int32 `json:"rawMax"`
		ExtraMax      int32 `json:"extraMax"`
		TrainingLevel int32 `json:"trainingLevel"`
	} `json:"parameters"`
	Equipment []struct {
		Level      int32       `json:"level"`
		PvPLevel   int32       `json:"pvpLevel"`
		Affinity   int32       `json:"affinity"`
		Parameters []*[2]int32 `json:"parameters"`
	} `json:"equipment"`
	Skills   []int32    `json:"skills"`
	Levels   []int32    `json:"levels"`
	Invoking [][2]int32 `json:"invoking"`
	Commands []struct {
		Opcode   int32 `json:"opcode"`
		Target   int32 `json:"target"`
		Skill    int32 `json:"skill"`
		Tick     int32 `json:"tick"`
		Duration int32 `json:"duration"`
		UseIndex int32 `json:"useIndex"`
	}
	Path   [][2]int32 `json:"path"`
	Weapon struct {
		Type           int32 `json:"type"`
		ShootingRange  int32 `json:"shootingRange"`
		Motion         int32 `json:"motion"`
		ProjectileFlag int32 `json:"projectileFlag"`
	} `json:"weapon"`
	Boss         bool  `json:"boss"`
	MonsterType  int32 `json:"monsterType"`
	SpecialHuman bool  `json:"specialHuman"`
	MonsterSize  int32 `json:"monsterSize"`
	HumanFlag    int32 `json:"human_flag"`
}
type snapshotEntity struct {
	ID           int32                      `json:"id"`
	Destroyed    bool                       `json:"destroyed"`
	PresentSlots []int                      `json:"present_slots"`
	Components   map[string]json.RawMessage `json:"components"`
}
type snapshotModifier struct {
	Type            int32   `json:"type"`
	OffsetX         float32 `json:"offset_x"`
	OffsetY         float32 `json:"offset_y"`
	OffsetZ         float32 `json:"offset_z"`
	ScaleX          float32 `json:"scale_x"`
	ScaleY          float32 `json:"scale_y"`
	Angle           int32   `json:"angle"`
	Anchor          int32   `json:"anchor"`
	Frame           int32   `json:"frame"`
	Duration        int32   `json:"duration"`
	DestroyOnFinish bool    `json:"destroy_on_finish"`
	Loop            bool    `json:"loop"`
	Alpha           int32   `json:"alpha"`
}
type snapshotEffect struct {
	Type     int32  `json:"type"`
	Value1   int32  `json:"value1"`
	Value2   int32  `json:"value2"`
	Depth    bool   `json:"depth"`
	Frame    int32  `json:"frame"`
	MaxFrame int32  `json:"max_frame"`
	Parent   *int32 `json:"parent"`
	Scale    int32  `json:"scale"`
}
type snapshotProjectile struct {
	Start  [3]float32 `json:"start"`
	End    [3]float32 `json:"end"`
	Speed  int32      `json:"speed"`
	Height int32      `json:"height"`
	Frame  int32      `json:"frame"`
	Length int32      `json:"length"`
	Owner  int32      `json:"owner"`
}
type snapshotSubset struct {
	Slots   []json.RawMessage `json:"slots"`
	Free    []uint32          `json:"free"`
	Version int32             `json:"version"`
}
type snapshotRNG struct {
	Values  []int32 `json:"values"`
	Index   int32   `json:"index"`
	Partner int32   `json:"partner"`
	Draws   uint32  `json:"draws"`
}
type snapshotRow struct {
	ID                int32 `json:"id"`
	Category          int32 `json:"category"`
	Type              int32 `json:"type"`
	Flags             int32 `json:"flags"`
	MinMP             int32 `json:"minMp"`
	MaxMP             int32 `json:"maxMp"`
	RequiredEquipType int32 `json:"requiredEquipType"`
	ShootingRange     int32 `json:"shootingRange"`
	Range             int32 `json:"range"`
	Count             int32 `json:"count"`
	Motion            int32 `json:"motion"`
	Value             int32 `json:"value"`
	Seb               int32 `json:"seb"`
	Img               int32 `json:"img"`
	ImpactImg         int32 `json:"impactImg"`
	ImpactSeb         int32 `json:"impactSeb"`
}
type snapshotConsumables struct {
	HolyHerbStock   int32   `json:"holy_herb_stock"`
	HolyHerbMaxUses int32   `json:"holy_herb_max_uses"`
	MPWatch         []int32 `json:"mp_watch"`
	Items           []struct {
		Parameter    int32 `json:"parameter"`
		AllResidents uint8 `json:"all_residents"`
		BonusMin     int32 `json:"bonus_min"`
		BonusMax     int32 `json:"bonus_max"`
		Stock        int32 `json:"stock"`
	} `json:"items"`
	Inputs []struct {
		Tick  int32 `json:"tick"`
		Phase int32 `json:"phase"`
		Kind  int32 `json:"kind"`
		Item  int32 `json:"item"`
	} `json:"inputs"`
	Uses []json.RawMessage `json:"uses"`
}
type nativeSnapshot struct {
	Config             snapshotConfig         `json:"config"`
	Units              []snapshotUnit         `json:"units"`
	Objects            []snapshotEntity       `json:"objects"`
	Subsets            []snapshotSubset       `json:"subsets"`
	Buckets            [][]json.RawMessage    `json:"buckets"`
	RNG                map[string]snapshotRNG `json:"rng"`
	Rows               map[string]snapshotRow `json:"rows"`
	EffectResources    map[string]int32       `json:"effect_resources"`
	Prizes             *[]int32               `json:"prizes"`
	HumanBases         []int32                `json:"human_bases"`
	ProjectileSources  [][]int32              `json:"projectile_sources"`
	AnimationResources [][]int32              `json:"animation_resources"`
	Consumables        *snapshotConsumables   `json:"consumables"`
}

func optionalJSON(raw json.RawMessage, out any) error {
	if len(raw) == 0 || string(raw) == "null" {
		return nil
	}
	return json.Unmarshal(raw, out)
}
func requireObjectKeys(raw json.RawMessage, label string, keys ...string) error {
	var m map[string]json.RawMessage
	if err := json.Unmarshal(raw, &m); err != nil || m == nil {
		return fmt.Errorf("snapshot %s must be an object", label)
	}
	for _, key := range keys {
		if _, ok := m[key]; !ok {
			return fmt.Errorf("snapshot %s missing required field %q", label, key)
		}
	}
	return nil
}
func rejectUnknownKeys(raw json.RawMessage, label string, allowed ...string) error {
	var m map[string]json.RawMessage
	if err := json.Unmarshal(raw, &m); err != nil {
		return fmt.Errorf("snapshot %s: %w", label, err)
	}
	set := make(map[string]bool, len(allowed))
	for _, key := range allowed {
		set[key] = true
	}
	for key := range m {
		if !set[key] {
			return fmt.Errorf("snapshot %s has unsupported field %q", label, key)
		}
	}
	return nil
}
func requireJSONArray(raw json.RawMessage, label string) error {
	if len(raw) == 0 || len(raw) == 4 && string(raw) == "null" {
		return fmt.Errorf("snapshot %s must be an array", label)
	}
	trim := bytes.TrimSpace(raw)
	if len(trim) == 0 || trim[0] != '[' {
		return fmt.Errorf("snapshot %s must be an array", label)
	}
	return nil
}
func requireJSONObject(raw json.RawMessage, label string) error {
	if len(raw) == 0 || string(raw) == "null" {
		return fmt.Errorf("snapshot %s must be an object", label)
	}
	var m map[string]json.RawMessage
	if err := json.Unmarshal(raw, &m); err != nil || m == nil {
		return fmt.Errorf("snapshot %s must be an object", label)
	}
	return nil
}
func decodeSnapshot(raw json.RawMessage) (nativeSnapshot, error) {
	var s nativeSnapshot
	if len(raw) == 0 || len(raw) > 64<<20 {
		return s, errors.New("prepared snapshot JSON missing or exceeds 64 MiB")
	}
	if err := requireObjectKeys(raw, "root", "config", "units", "objects", "subsets", "buckets", "rng", "rows", "effect_resources", "prizes", "human_bases", "projectile_sources", "animation_resources", "consumables"); err != nil {
		return s, err
	}
	rootAllowed := []string{"config", "units", "objects", "subsets", "buckets", "rng", "rows", "effect_resources", "prizes", "human_bases", "projectile_sources", "animation_resources", "consumables"}
	if err := rejectUnknownKeys(raw, "root", rootAllowed...); err != nil {
		return s, err
	}
	if err := json.Unmarshal(raw, &s); err != nil {
		return s, fmt.Errorf("decode canonical prepared snapshot: %w", err)
	}
	var root map[string]json.RawMessage
	_ = json.Unmarshal(raw, &root)
	for _, key := range []string{"units", "objects", "subsets", "buckets", "human_bases", "projectile_sources", "animation_resources"} {
		if err := requireJSONArray(root[key], key); err != nil {
			return s, err
		}
	}
	for _, key := range []string{"config", "rng", "rows", "effect_resources", "consumables"} {
		if err := requireJSONObject(root[key], key); err != nil {
			return s, err
		}
	}
	var rawRows struct {
		Units   []json.RawMessage `json:"units"`
		Objects []json.RawMessage `json:"objects"`
	}
	_ = json.Unmarshal(raw, &rawRows)
	if err := requireObjectKeys(root["config"], "config", "tick", "first_identity", "next_identity", "next_command", "map_width", "row_offset", "movement_enabled", "battle_state", "battle_frame", "verdict", "verdict_tick", "ending_counter", "ending_gate_tick", "prize_count"); err != nil {
		return s, err
	}
	if err := rejectUnknownKeys(root["config"], "config", "tick", "first_identity", "next_identity", "next_command", "map_width", "row_offset", "movement_enabled", "battle_state", "battle_frame", "verdict", "verdict_tick", "ending_counter", "ending_gate_tick", "prize_count"); err != nil {
		return s, err
	}
	if len(s.Units) == 0 || len(s.Units) > nativeUnitLimit || len(s.Objects) > nativeObjectLimit {
		return s, errors.New("snapshot unit/object count outside native capacity")
	}
	if len(s.Subsets) != nativeSubsetCount {
		return s, fmt.Errorf("snapshot requires exactly %d subsets", nativeSubsetCount)
	}
	if len(s.Buckets) > nativeBucketLimit || len(s.ProjectileSources) > nativeObjectLimit || len(s.AnimationResources) > 512 || len(s.HumanBases) > 48 {
		return s, errors.New("snapshot bucket, source, animation, or human-base count exceeds native capacity")
	}
	if s.Config.MapWidth <= 0 {
		return s, errors.New("snapshot config outside supported bounds")
	}
	if s.Config.Tick < -1 || s.Config.Tick > int64(^uint32(0)) {
		return s, errors.New("snapshot tick outside u32 ABI range (including initial -1 sentinel)")
	}
	if len(s.RNG["math"].Values) != 56 || len(s.RNG["lib"].Values) != 56 {
		return s, errors.New("snapshot must provide both complete 56-word RNG states")
	}
	if err := rejectUnknownKeys(root["rng"], "rng", "math", "lib"); err != nil {
		return s, err
	}
	for _, name := range []string{"math", "lib"} {
		rngRaw := map[string]json.RawMessage{}
		_ = json.Unmarshal(root["rng"], &rngRaw)
		if err := requireObjectKeys(rngRaw[name], "rng."+name, "values", "index", "partner", "draws"); err != nil {
			return s, err
		}
	}
	if len(s.Rows) > nativeRowsLimit || len(s.EffectResources) > nativeEffectResourceLimit {
		return s, errors.New("snapshot row or effect-resource count exceeds kernel capacity")
	}
	var rawRowsByKey map[string]json.RawMessage
	_ = json.Unmarshal(root["rows"], &rawRowsByKey)
	seenRowIDs := make(map[int32]string, len(s.Rows))
	// engine_snapshot copies complete rows from weapon-skill-profiles.json.
	// These 16 fields are required by KaSkill; the listed catalog/UI metadata is
	// present in the canonical source table but has no KaSkill ABI member. Keep
	// this allowlist explicit so any new, potentially mechanical field still
	// fails closed until its native meaning is implemented.
	rowFields := []string{"id", "category", "type", "flags", "minMp", "maxMp", "requiredEquipType", "shootingRange", "range", "count", "motion", "value", "seb", "img", "impactImg", "impactSeb"}
	rowAllowedFields := append(append([]string(nil), rowFields...), "currency", "explainArg", "explainText", "iconU", "iconV", "nameArg", "nameText", "res", "searchingRange")
	for key, row := range s.Rows {
		parsed, err := strconv.ParseInt(key, 10, 32)
		if err != nil || strconv.FormatInt(parsed, 10) != key {
			return s, fmt.Errorf("invalid skill row id %q", key)
		}
		id := int32(parsed)
		if prior, exists := seenRowIDs[id]; exists {
			return s, fmt.Errorf("skill row keys %q and %q resolve to the same native id", prior, key)
		}
		seenRowIDs[id] = key
		label := fmt.Sprintf("rows[%q]", key)
		if err := requireObjectKeys(rawRowsByKey[key], label, rowFields...); err != nil {
			return s, err
		}
		if err := rejectUnknownKeys(rawRowsByKey[key], label, rowAllowedFields...); err != nil {
			return s, err
		}
		if row.ID != id {
			return s, fmt.Errorf("skill row key %q does not match row id %d", key, row.ID)
		}
	}
	seenEffectIDs := make(map[int32]string, len(s.EffectResources))
	for key := range s.EffectResources {
		parsed, err := strconv.ParseInt(key, 10, 32)
		if err != nil || strconv.FormatInt(parsed, 10) != key {
			return s, fmt.Errorf("invalid effect resource id %q", key)
		}
		id := int32(parsed)
		if prior, exists := seenEffectIDs[id]; exists {
			return s, fmt.Errorf("effect resource keys %q and %q resolve to the same native id", prior, key)
		}
		seenEffectIDs[id] = key
	}
	for i := range s.Units {
		if err := requireObjectKeys(rawRows.Units[i], fmt.Sprintf("units[%d]", i), "identity", "present", "id", "team", "human", "monster", "flags", "destroyed", "present_slots", "components", "board", "long_board", "parameters", "equipment", "skills", "levels", "invoking", "commands", "path", "weapon", "boss", "monsterType", "specialHuman", "monsterSize", "human_flag"); err != nil {
			return s, err
		}
		if err := rejectUnknownKeys(rawRows.Units[i], fmt.Sprintf("units[%d]", i), "identity", "present", "id", "team", "human", "monster", "flags", "destroyed", "present_slots", "components", "board", "long_board", "parameters", "equipment", "skills", "levels", "invoking", "commands", "path", "weapon", "boss", "monsterType", "specialHuman", "monsterSize", "human_flag"); err != nil {
			return s, err
		}
		if (s.Units[i].Team != 0 && s.Units[i].Team != 1) || s.Units[i].Human == s.Units[i].Monster || s.Units[i].Present > 1 {
			return s, fmt.Errorf("unit %d has invalid team, human/monster identity, or presence", i)
		}
		var unitFields map[string]json.RawMessage
		_ = json.Unmarshal(rawRows.Units[i], &unitFields)
		for _, key := range []string{"present_slots", "skills", "levels", "equipment", "invoking", "commands", "path"} {
			if err := requireJSONArray(unitFields[key], fmt.Sprintf("units[%d].%s", i, key)); err != nil {
				return s, err
			}
		}
		for _, key := range []string{"components", "board", "long_board", "parameters", "weapon"} {
			if err := requireJSONObject(unitFields[key], fmt.Sprintf("units[%d].%s", i, key)); err != nil {
				return s, err
			}
		}
		if err := rejectUnknownKeys(unitFields["weapon"], fmt.Sprintf("units[%d].weapon", i), "type", "shootingRange", "motion", "projectileFlag"); err != nil {
			return s, err
		}
		var invoking, path, equipment, commands []json.RawMessage
		_ = json.Unmarshal(unitFields["invoking"], &invoking)
		_ = json.Unmarshal(unitFields["path"], &path)
		_ = json.Unmarshal(unitFields["equipment"], &equipment)
		_ = json.Unmarshal(unitFields["commands"], &commands)
		for j, pair := range invoking {
			if err := requireFixedArray(pair, fmt.Sprintf("units[%d].invoking[%d]", i, j), 2); err != nil {
				return s, err
			}
		}
		for j, pair := range path {
			if err := requireFixedArray(pair, fmt.Sprintf("units[%d].path[%d]", i, j), 2); err != nil {
				return s, err
			}
		}
		for j, command := range commands {
			label := fmt.Sprintf("units[%d].commands[%d]", i, j)
			keys := []string{"opcode", "target", "skill", "tick", "duration", "useIndex"}
			if err := requireObjectKeys(command, label, keys...); err != nil {
				return s, err
			}
			if err := rejectUnknownKeys(command, label, keys...); err != nil {
				return s, err
			}
		}
		for j := range s.Units[i].Equipment {
			label := fmt.Sprintf("units[%d].equipment[%d]", i, j)
			if err := requireObjectKeys(equipment[j], label, "level", "pvpLevel", "affinity", "parameters"); err != nil {
				return s, err
			}
			if err := rejectUnknownKeys(equipment[j], label, "level", "pvpLevel", "affinity", "parameters"); err != nil {
				return s, err
			}
			var equipFields map[string]json.RawMessage
			_ = json.Unmarshal(equipment[j], &equipFields)
			if err := requireJSONArray(equipFields["parameters"], label+".parameters"); err != nil {
				return s, err
			}
			var pairs []json.RawMessage
			_ = json.Unmarshal(equipFields["parameters"], &pairs)
			if len(pairs) > 16 {
				return s, fmt.Errorf("%s.parameters exceeds 16 entries", label)
			}
			for k, pair := range pairs {
				if string(bytes.TrimSpace(pair)) == "null" {
					continue
				}
				if err := requireFixedArray(pair, fmt.Sprintf("%s.parameters[%d]", label, k), 2); err != nil {
					return s, err
				}
			}
		}
		for key, rawValue := range s.Units[i].Parameters {
			paramLabel := fmt.Sprintf("units[%d].parameters[%d]", i, key)
			var parameterRaw map[string]json.RawMessage
			_ = json.Unmarshal(unitFields["parameters"], &parameterRaw)
			if err := requireObjectKeys(parameterRaw[strconv.Itoa(key)], paramLabel, "rawValue", "extraValue", "rawMax", "extraMax", "trainingLevel"); err != nil {
				return s, err
			}
			if err := rejectUnknownKeys(parameterRaw[strconv.Itoa(key)], paramLabel, "rawValue", "extraValue", "rawMax", "extraMax", "trainingLevel"); err != nil {
				return s, err
			}
			_ = rawValue
		}
	}
	for i := range s.Objects {
		if err := requireObjectKeys(rawRows.Objects[i], fmt.Sprintf("objects[%d]", i), "id", "destroyed", "present_slots", "components"); err != nil {
			return s, err
		}
		if err := rejectUnknownKeys(rawRows.Objects[i], fmt.Sprintf("objects[%d]", i), "id", "destroyed", "present_slots", "components"); err != nil {
			return s, err
		}
	}
	for i, sub := range s.Subsets {
		var subsetRaw []json.RawMessage
		_ = json.Unmarshal(root["subsets"], &subsetRaw)
		label := fmt.Sprintf("subsets[%d]", i)
		if err := requireObjectKeys(subsetRaw[i], label, "slots", "free", "version"); err != nil {
			return s, err
		}
		if err := rejectUnknownKeys(subsetRaw[i], label, "slots", "free", "version"); err != nil {
			return s, err
		}
		var subsetFields map[string]json.RawMessage
		_ = json.Unmarshal(subsetRaw[i], &subsetFields)
		if err := requireJSONArray(subsetFields["slots"], label+".slots"); err != nil {
			return s, err
		}
		if err := requireJSONArray(subsetFields["free"], label+".free"); err != nil {
			return s, err
		}
		if len(sub.Slots) > nativeObjectLimit+nativeUnitLimit || len(sub.Free) > len(sub.Slots) {
			return s, fmt.Errorf("subset %d exceeds native slot capacity", i)
		}
		free := make(map[uint32]bool, len(sub.Free))
		for _, slot := range sub.Free {
			if slot >= uint32(len(sub.Slots)) || free[slot] {
				return s, fmt.Errorf("subset %d has an invalid or duplicate free-list slot", i)
			}
			free[slot] = true
		}
		for j, v := range sub.Slots {
			isHole := string(v) == "null"
			if isHole != free[uint32(j)] {
				return s, fmt.Errorf("subset %d slot/free-list mismatch at %d", i, j)
			}
		}
	}
	if s.Consumables != nil {
		if len(s.Consumables.Uses) > 0 {
			return s, errors.New("snapshot with recorded consumable uses cannot be imported")
		}
		if len(s.Consumables.Items) > nativeItemLimit || len(s.Consumables.Inputs) > nativeInputLimit || len(s.Consumables.MPWatch) > 2 {
			return s, errors.New("snapshot consumable data exceeds native capacity")
		}
	}
	return s, nil
}

func requireFixedArray(raw json.RawMessage, label string, size int) error {
	if err := requireJSONArray(raw, label); err != nil {
		return err
	}
	var values []json.RawMessage
	if err := json.Unmarshal(raw, &values); err != nil {
		return fmt.Errorf("snapshot %s: %w", label, err)
	}
	if len(values) != size {
		return fmt.Errorf("snapshot %s must have exactly %d entries", label, size)
	}
	return nil
}

func rawNumber(raw json.RawMessage, out any, label string) error {
	if err := json.Unmarshal(raw, out); err != nil {
		return fmt.Errorf("%s: %w", label, err)
	}
	return nil
}
func component(raw map[string]json.RawMessage, key string, out any) (bool, error) {
	v, ok := raw[key]
	if !ok {
		return false, fmt.Errorf("snapshot component key %q absent", key)
	}
	if string(bytes.TrimSpace(v)) == "null" {
		return false, nil
	}
	return true, json.Unmarshal(v, out)
}

func validateEntityComponentShapes(components map[string]json.RawMessage) error {
	for _, key := range []string{"position", "speed", "seb", "depth", "cell", "image", "animation", "direction", "modifier", "garbage", "effect", "projectile", "attack"} {
		raw, ok := components[key]
		if !ok {
			return fmt.Errorf("snapshot component key %q missing", key)
		}
		if string(bytes.TrimSpace(raw)) == "null" {
			continue
		}
		var err error
		switch key {
		case "position":
			err = requireFixedArray(raw, "components.position", 7)
		case "speed":
			err = requireFixedArray(raw, "components.speed", 3)
		case "seb":
			err = requireFixedArray(raw, "components.seb", 4)
		case "depth", "cell", "animation":
			err = requireFixedArray(raw, "components."+key, 2)
		case "image":
			err = requireFixedArray(raw, "components.image", 6)
		case "direction", "garbage", "attack":
			var value int32
			err = json.Unmarshal(raw, &value)
		case "modifier":
			keys := []string{"type", "offset_x", "offset_y", "offset_z", "scale_x", "scale_y", "angle", "anchor", "frame", "duration", "destroy_on_finish", "loop", "alpha"}
			err = requireObjectKeys(raw, "components.modifier", keys...)
			if err == nil {
				err = rejectUnknownKeys(raw, "components.modifier", keys...)
			}
		case "effect":
			keys := []string{"type", "value1", "value2", "depth", "frame", "max_frame", "parent", "scale"}
			err = requireObjectKeys(raw, "components.effect", keys...)
			if err == nil {
				err = rejectUnknownKeys(raw, "components.effect", keys...)
			}
		case "projectile":
			keys := []string{"start", "end", "speed", "height", "frame", "length", "owner"}
			err = requireObjectKeys(raw, "components.projectile", keys...)
			if err == nil {
				err = rejectUnknownKeys(raw, "components.projectile", keys...)
			}
			if err == nil {
				var values map[string]json.RawMessage
				_ = json.Unmarshal(raw, &values)
				err = requireFixedArray(values["start"], "components.projectile.start", 3)
				if err == nil {
					err = requireFixedArray(values["end"], "components.projectile.end", 3)
				}
			}
		}
		if err != nil {
			return err
		}
	}
	return nil
}

func fitBoard(src map[int]int64) (nBoard, error) {
	var b nBoard
	if len(src) > nativeBoardLimit {
		return b, errors.New("board exceeds 48-entry native capacity")
	}
	keys := make([]int, 0, len(src))
	for k := range src {
		keys = append(keys, k)
	}
	sort.Ints(keys)
	b.Len = uint32(len(keys))
	for i, k := range keys {
		b.Entries[i] = nEntry{Key: int32(k), Value: src[k]}
		if k >= 0 && k < 128 {
			b.Positions[k] = uint8(i + 1)
		}
	}
	return b, nil
}
func boolByte(v bool) uint8 {
	if v {
		return 1
	}
	return 0
}
func fillEntity(record snapshotEntity, forcedID int32) (nEntity, error) {
	e := nEntity{ID: forcedID, Parent: -1, Image: [6]int32{-1, -1, -1, -1, -1, -1}, Animation: [2]int32{1, -1}, Destroyed: boolByte(record.Destroyed)}
	for _, slot := range record.PresentSlots {
		if slot < 0 || slot >= nativeSlotCount {
			return e, fmt.Errorf("entity %d component slot %d outside 0..51", forcedID, slot)
		}
		e.Has[slot] = 1
	}
	if record.Components == nil {
		return e, errors.New("entity components object missing")
	}
	if err := validateEntityComponentShapes(record.Components); err != nil {
		return e, err
	}
	var vals []json.RawMessage
	if ok, err := component(record.Components, "position", &vals); err != nil {
		return e, err
	} else if ok {
		if len(vals) != 7 {
			return e, errors.New("position component must have 7 fields")
		}
		var f [6]float32
		for i := 0; i < 6; i++ {
			if err := rawNumber(vals[i], &f[i], "position float"); err != nil {
				return e, err
			}
		}
		e.Position = nVec3{f[0], f[1], f[2]}
		e.Offset = nVec3{f[3], f[4], f[5]}
		if string(vals[6]) != "null" {
			if err := rawNumber(vals[6], &e.Parent, "position parent"); err != nil {
				return e, err
			}
		}
	}
	if ok, err := component(record.Components, "speed", &vals); err != nil {
		return e, err
	} else if ok {
		if len(vals) != 3 {
			return e, errors.New("speed component must have exactly three coordinates")
		}
		var f [3]float32
		for i := range f {
			if err := rawNumber(vals[i], &f[i], "speed"); err != nil {
				return e, err
			}
		}
		e.Speed = nVec3{f[0], f[1], f[2]}
	}
	for _, spec := range []struct {
		key   string
		dst   []int32
		count int
	}{{"seb", e.Seb[:], 4}, {"depth", e.Depth[:], 2}, {"cell", e.Cell[:], 2}, {"image", e.Image[:], 6}, {"animation", e.Animation[:], 2}} {
		if ok, err := component(record.Components, spec.key, &vals); err != nil {
			return e, err
		} else if ok {
			if len(vals) != spec.count {
				return e, fmt.Errorf("%s component length %d != %d", spec.key, len(vals), spec.count)
			}
			for i := range vals {
				if err := rawNumber(vals[i], &spec.dst[i], spec.key); err != nil {
					return e, err
				}
			}
		}
	}
	if ok, err := component(record.Components, "direction", &e.Direction); err != nil {
		return e, err
	} else if !ok {
		e.Direction = 0
	}
	if _, err := component(record.Components, "garbage", &e.Garbage); err != nil {
		return e, err
	}
	if _, err := component(record.Components, "attack", &e.Attack); err != nil {
		return e, err
	}
	var modifier snapshotModifier
	if ok, err := component(record.Components, "modifier", &modifier); err != nil {
		return e, err
	} else if ok {
		e.Modifier = nModifier{Type: modifier.Type, OffsetX: modifier.OffsetX, OffsetY: modifier.OffsetY, OffsetZ: modifier.OffsetZ, ScaleX: modifier.ScaleX, ScaleY: modifier.ScaleY, Angle: modifier.Angle, Anchor: modifier.Anchor, Frame: modifier.Frame, Duration: modifier.Duration, DestroyOnFinish: boolByte(modifier.DestroyOnFinish), Looping: boolByte(modifier.Loop), Alpha: modifier.Alpha}
	}
	var effect snapshotEffect
	if ok, err := component(record.Components, "effect", &effect); err != nil {
		return e, err
	} else if ok {
		parent := int32(-1)
		if effect.Parent != nil {
			parent = *effect.Parent
		}
		e.Effect = nEffect{Type: effect.Type, Value1: effect.Value1, Value2: effect.Value2, Depth: boolByte(effect.Depth), Frame: effect.Frame, MaxFrame: effect.MaxFrame, Parent: parent, Scale: effect.Scale}
	}
	var projectile snapshotProjectile
	if ok, err := component(record.Components, "projectile", &projectile); err != nil {
		return e, err
	} else if ok {
		e.Projectile = nProjectile{Start: nVec3{projectile.Start[0], projectile.Start[1], projectile.Start[2]}, End: nVec3{projectile.End[0], projectile.End[1], projectile.End[2]}, Speed: projectile.Speed, Height: projectile.Height, Frame: projectile.Frame, Length: projectile.Length, Owner: projectile.Owner}
	}
	allowed := map[string]bool{"position": true, "speed": true, "seb": true, "depth": true, "cell": true, "image": true, "animation": true, "direction": true, "modifier": true, "garbage": true, "effect": true, "projectile": true, "attack": true}
	for key := range record.Components {
		if !allowed[key] {
			return e, fmt.Errorf("unsupported native component field %q", key)
		}
	}
	componentSlots := map[string]int{"position": 0, "speed": 1, "seb": 2, "depth": 4, "cell": 5, "image": 7, "animation": 12, "direction": 14, "modifier": 19, "garbage": 32, "effect": 38, "projectile": 39, "attack": 46}
	for key, slot := range componentSlots {
		raw, exists := record.Components[key]
		if !exists {
			return e, fmt.Errorf("snapshot component key %q missing", key)
		}
		if (string(raw) != "null") != (e.Has[slot] != 0) {
			return e, fmt.Errorf("component %s presence disagrees with component mask", key)
		}
	}
	return e, nil
}

func nativeUnitFromSnapshot(u snapshotUnit) (nUnit, error) {
	var out nUnit
	out.Present = uint8(u.Present)
	out.Team = uint8(u.Team)
	out.Human = boolByte(u.Human)
	out.Monster = boolByte(u.Monster)
	out.Flags = u.Flags
	out.ID = u.ID
	out.Identity = u.Identity
	entity, err := fillEntity(snapshotEntity{ID: u.ID, Destroyed: u.Destroyed, PresentSlots: u.PresentSlots, Components: u.Components}, u.Identity)
	if err != nil {
		return out, fmt.Errorf("unit %d: %w", u.Identity, err)
	}
	out.Body = entity
	out.WeaponType = u.Weapon.Type
	out.WeaponShootingRange = u.Weapon.ShootingRange
	out.WeaponMotion = u.Weapon.Motion
	out.WeaponProjectileFlag = u.Weapon.ProjectileFlag
	out.Boss = boolByte(u.Boss)
	out.MonsterType = u.MonsterType
	out.SpecialHuman = boolByte(u.SpecialHuman)
	out.MonsterSize = u.MonsterSize
	out.HumanFlag = u.HumanFlag
	out.Board, err = fitBoard(u.Board)
	if err != nil {
		return out, err
	}
	out.LongBoard, err = fitBoard(u.LongBoard)
	if err != nil {
		return out, err
	}
	if len(u.Parameters) > 16 || len(u.Equipment) > 8 || len(u.Skills) > 12 || len(u.Levels) > 12 || len(u.Invoking) > 64 || len(u.Commands) > 1024 || len(u.Path) > 8 {
		return out, fmt.Errorf("unit %d exceeds native roster table capacity", u.Identity)
	}
	pkeys := make([]int, 0, len(u.Parameters))
	for k := range u.Parameters {
		pkeys = append(pkeys, k)
	}
	sort.Ints(pkeys)
	out.Params.Count = uint32(len(pkeys))
	for i, k := range pkeys {
		p := u.Parameters[k]
		out.Params.Rows[i] = nParam{ID: int32(k), RawValue: p.RawValue, ExtraValue: p.ExtraValue, RawMax: p.RawMax, ExtraMax: p.ExtraMax, TrainingLevel: p.TrainingLevel}
	}
	out.Params.EquipmentCount = uint32(len(u.Equipment))
	for i, row := range u.Equipment {
		e := &out.Params.Equipment[i]
		e.Level = row.Level
		e.PvPLevel = row.PvPLevel
		e.Affinity = row.Affinity
		e.PairCount = int32(len(row.Parameters))
		if len(row.Parameters) > 16 {
			return out, errors.New("equipment parameter table exceeds 16 entries")
		}
		for j, p := range row.Parameters {
			if p != nil {
				e.Present[j] = 1
				e.Pairs[j] = *p
			}
		}
	}
	out.SkillCount = uint32(len(u.Skills))
	copy(out.SkillIDs[:], u.Skills)
	out.LevelCount = uint32(len(u.Levels))
	copy(out.Levels[:], u.Levels)
	out.InvokingCount = uint32(len(u.Invoking))
	for i, v := range u.Invoking {
		out.Invoking[i] = nInvoke{v[0], v[1]}
	}
	out.CommandCount = uint32(len(u.Commands))
	for i, v := range u.Commands {
		out.Commands[i] = nCommand{v.Opcode, v.Target, v.Skill, v.Tick, v.Duration, v.UseIndex}
	}
	out.PathCount = uint32(len(u.Path))
	for i, v := range u.Path {
		out.Path[i] = nPoint{v[0], v[1]}
	}
	return out, nil
}

type rawABI struct{ create, importUnits, identity, config, counters, addRow, addEffect, setPrizes, setBases, setSource, setAnimation, setObject, setSubset, setBucket, setRNG, setControl, setScope, addInput, addItem, setHerb, setMPWatch, clone, seed, skip, run, report, stats, checksum, free, encounterReport, encounterSize, encounterVersion, fullTick, eventCount, exportEvents, exportUnits, battleCounters, unitHP, unitMP, unitParamValue, unitParamMaximum, mpConfig, useLogCount, useLog *syscall.Proc }

func getProc(d *syscall.DLL, name string) (*syscall.Proc, error) {
	p, e := d.FindProc(name)
	if e != nil {
		return nil, fmt.Errorf("native DLL export %s unavailable: %w", name, e)
	}
	return p, nil
}
func openRawABI(path string) (*syscall.DLL, rawABI, error) {
	d, e := syscall.LoadDLL(path)
	if e != nil {
		return nil, rawABI{}, e
	}
	var a rawABI
	fields := map[string]**syscall.Proc{"ka_battle_create": &a.create, "ka_battle_import": &a.importUnits, "ka_battle_identity": &a.identity, "ka_battle_config": &a.config, "ka_battle_set_counters": &a.counters, "ka_battle_add_row": &a.addRow, "ka_battle_add_effect_resource": &a.addEffect, "ka_battle_set_prizes": &a.setPrizes, "ka_battle_set_human_bases": &a.setBases, "ka_battle_set_projectile_source": &a.setSource, "ka_battle_set_animation_resource": &a.setAnimation, "ka_battle_set_object": &a.setObject, "ka_battle_set_subset": &a.setSubset, "ka_battle_set_bucket": &a.setBucket, "ka_battle_set_rng": &a.setRNG, "ka_battle_set_control": &a.setControl, "ka_battle_set_scope_allowed": &a.setScope, "ka_battle_add_input": &a.addInput, "ka_battle_add_item": &a.addItem, "ka_battle_set_herb_stock": &a.setHerb, "ka_battle_set_mp_watch": &a.setMPWatch, "ka_battle_clone": &a.clone, "ka_battle_seed_rng": &a.seed, "ka_battle_skip_lib_draws": &a.skip, "ka_run_battle": &a.run, "ka_battle_report": &a.report, "ka_battle_stats": &a.stats, "ka_battle_checksum": &a.checksum, "ka_battle_free": &a.free}
	for name, dst := range fields {
		p, err := getProc(d, name)
		if err != nil {
			_ = d.Release()
			return nil, rawABI{}, err
		}
		*dst = p
	}
	// Encounter telemetry is exported only by the separately versioned v3 DLL.
	// Partial export sets indicate a broken/mixed ABI and fail closed.
	var present int
	for name, dst := range map[string]**syscall.Proc{"ka_encounter_report": &a.encounterReport, "ka_sizeof_encounter_report": &a.encounterSize, "ka_encounter_version": &a.encounterVersion} {
		p, err := d.FindProc(name)
		if err == nil {
			*dst = p
			present++
		}
	}
	if present != 0 && present != 3 {
		_ = d.Release()
		return nil, rawABI{}, errors.New("incomplete encounter telemetry v3 ABI exports")
	}
	// Optional replay/state exports are additive. The ordinary batched runner remains usable with
	// older canonical DLLs; replay mode checks this complete set before starting a battle.
	for name, dst := range map[string]**syscall.Proc{
		"ka_native_full_tick": &a.fullTick, "ka_battle_event_count": &a.eventCount,
		"ka_battle_export_events": &a.exportEvents, "ka_battle_export": &a.exportUnits,
		"ka_battle_counters": &a.battleCounters, "ka_unit_hp": &a.unitHP,
		"ka_battle_mp": &a.unitMP, "ka_battle_mp_config": &a.mpConfig,
		"ka_unit_param_value": &a.unitParamValue, "ka_unit_param_maximum": &a.unitParamMaximum,
		"ka_use_log_count": &a.useLogCount, "ka_use_log": &a.useLog,
	} {
		if p, err := d.FindProc(name); err == nil {
			*dst = p
		}
	}
	return d, a, nil
}

func buildNativeSnapshot(a rawABI, raw json.RawMessage) (uintptr, error) {
	s, err := decodeSnapshot(raw)
	if err != nil {
		return 0, err
	}
	units := make([]nUnit, len(s.Units))
	for i, u := range s.Units {
		units[i], err = nativeUnitFromSnapshot(u)
		if err != nil {
			return 0, fmt.Errorf("unit %d: %w", i, err)
		}
	}
	battle, _, _ := a.create.Call()
	if battle == 0 {
		return 0, errors.New("ka_battle_create returned null")
	}
	fail := func(e error) (uintptr, error) { _, _, _ = a.free.Call(battle); return 0, e }
	imported, _, _ := a.importUnits.Call(battle, uintptr(unsafe.Pointer(&units[0])), uintptr(len(units)), 0)
	runtime.KeepAlive(units)
	if imported != uintptr(len(units)) {
		return fail(fmt.Errorf("native imported %d of %d units", imported, len(units)))
	}
	_, _, _ = a.identity.Call(battle, uintptr(uint32(s.Config.FirstIdentity)))
	_, _, _ = a.config.Call(battle, uintptr(uint32(s.Config.MapWidth)), uintptr(uint32(s.Config.RowOffset)), uintptr(boolByte(s.Config.MovementEnabled)))
	_, _, _ = a.counters.Call(battle, uintptr(uint32(s.Config.Tick)), uintptr(uint32(s.Config.NextIdentity)), uintptr(uint32(s.Config.NextCommand)))
	rowKeys := make([]string, 0, len(s.Rows))
	for key := range s.Rows {
		rowKeys = append(rowKeys, key)
	}
	sort.Slice(rowKeys, func(i, j int) bool {
		left, _ := strconv.ParseInt(rowKeys[i], 10, 32)
		right, _ := strconv.ParseInt(rowKeys[j], 10, 32)
		return left < right
	})
	for _, key := range rowKeys {
		r := s.Rows[key]
		row := nSkill{ID: r.ID, Category: r.Category, Kind: r.Type, Flags: r.Flags, MinMP: r.MinMP, MaxMP: r.MaxMP, RequiredEquipType: r.RequiredEquipType, ShootingRange: r.ShootingRange, Range: r.Range, Count: r.Count, Motion: r.Motion, Value: r.Value, Seb: r.Seb, Img: r.Img, ImpactImg: r.ImpactImg, ImpactSeb: r.ImpactSeb}
		count, _, _ := a.addRow.Call(battle, uintptr(unsafe.Pointer(&row)))
		runtime.KeepAlive(row)
		if count == 0 {
			return fail(errors.New("native skill row table overflow"))
		}
	}
	effectKeys := make([]string, 0, len(s.EffectResources))
	for key := range s.EffectResources {
		effectKeys = append(effectKeys, key)
	}
	sort.Slice(effectKeys, func(i, j int) bool {
		left, _ := strconv.ParseInt(effectKeys[i], 10, 32)
		right, _ := strconv.ParseInt(effectKeys[j], 10, 32)
		return left < right
	})
	for _, key := range effectKeys {
		id, _ := strconv.ParseInt(key, 10, 32)
		count, _, _ := a.addEffect.Call(battle, uintptr(uint32(int32(id))), uintptr(uint32(s.EffectResources[key])))
		if count == 0 {
			return fail(errors.New("native effect resource table overflow"))
		}
	}
	if s.Prizes != nil {
		if len(*s.Prizes) > nativePrizeLimit {
			return fail(errors.New("prize list exceeds native capacity"))
		}
		var ptr uintptr
		if len(*s.Prizes) > 0 {
			ptr = uintptr(unsafe.Pointer(&(*s.Prizes)[0]))
		}
		_, _, _ = a.setPrizes.Call(battle, ptr, uintptr(len(*s.Prizes)))
		runtime.KeepAlive(*s.Prizes)
	}
	if len(s.HumanBases) > 0 {
		n, _, _ := a.setBases.Call(battle, uintptr(unsafe.Pointer(&s.HumanBases[0])), uintptr(len(s.HumanBases)))
		runtime.KeepAlive(s.HumanBases)
		if n != uintptr(len(s.HumanBases)) {
			return fail(errors.New("native human base table truncated"))
		}
	}
	for _, v := range s.ProjectileSources {
		if len(v) != 3 {
			return fail(errors.New("projectile source row must have 3 integers"))
		}
		_, _, _ = a.setSource.Call(battle, uintptr(uint32(v[0])), uintptr(uint32(v[1])), uintptr(uint32(v[2])))
	}
	for i, v := range s.AnimationResources {
		if len(v) != 4 {
			return fail(errors.New("animation resource row must have 4 integers"))
		}
		count, _, _ := a.setAnimation.Call(battle, uintptr(uint32(v[0])), uintptr(uint32(v[1])), uintptr(uint32(v[2])), uintptr(uint32(v[3])))
		if count != uintptr(i+1) {
			return fail(fmt.Errorf("native animation resource table stopped at %d", count))
		}
	}
	for i, obj := range s.Objects {
		entity, e := fillEntity(obj, obj.ID)
		if e != nil {
			return fail(fmt.Errorf("object %d: %w", i, e))
		}
		n, _, _ := a.setObject.Call(battle, uintptr(i), uintptr(unsafe.Pointer(&entity)))
		runtime.KeepAlive(entity)
		if n < uintptr(i+1) {
			return fail(fmt.Errorf("native object table stopped at %d", n))
		}
	}
	for i, sub := range s.Subsets {
		slots := make([]int32, len(sub.Slots))
		for j, v := range sub.Slots {
			if string(v) == "null" {
				slots[j] = 0
			} else if e := json.Unmarshal(v, &slots[j]); e != nil {
				return fail(fmt.Errorf("subset %d slot %d: %w", i, j, e))
			}
		}
		free := sub.Free
		var sp, fp uintptr
		if len(slots) > 0 {
			sp = uintptr(unsafe.Pointer(&slots[0]))
		}
		if len(free) > 0 {
			fp = uintptr(unsafe.Pointer(&free[0]))
		}
		_, _, _ = a.setSubset.Call(battle, uintptr(i), uintptr(len(slots)), uintptr(len(free)), uintptr(uint32(sub.Version)), sp, fp)
		runtime.KeepAlive(slots)
		runtime.KeepAlive(free)
	}
	for i, bucket := range s.Buckets {
		if len(bucket) != 2 {
			return fail(fmt.Errorf("bucket %d must be [key,ids]", i))
		}
		var key int32
		var ids []int32
		if e := json.Unmarshal(bucket[0], &key); e != nil {
			return fail(e)
		}
		if e := json.Unmarshal(bucket[1], &ids); e != nil {
			return fail(e)
		}
		if len(ids) > 64 {
			return fail(fmt.Errorf("bucket %d exceeds 64 ids", i))
		}
		var p uintptr
		if len(ids) > 0 {
			p = uintptr(unsafe.Pointer(&ids[0]))
		}
		n, _, _ := a.setBucket.Call(battle, uintptr(i), uintptr(uint32(key)), uintptr(len(ids)), p)
		runtime.KeepAlive(ids)
		if n < uintptr(i+1) {
			return fail(errors.New("native bucket table overflow"))
		}
	}
	for i, name := range []string{"math", "lib"} {
		rng := s.RNG[name]
		var p uintptr
		if len(rng.Values) > 0 {
			p = uintptr(unsafe.Pointer(&rng.Values[0]))
		}
		_, _, _ = a.setRNG.Call(battle, uintptr(i), p, uintptr(uint32(rng.Index)), uintptr(uint32(rng.Partner)), uintptr(rng.Draws))
		runtime.KeepAlive(rng.Values)
	}
	_, _, _ = a.setControl.Call(battle, uintptr(uint32(s.Config.BattleState)), uintptr(uint32(s.Config.BattleFrame)), uintptr(uint32(s.Config.Verdict)), uintptr(uint32(s.Config.VerdictTick)), uintptr(uint32(s.Config.EndingCounter)), uintptr(uint32(s.Config.EndingGateTick)), 0, uintptr(^uint32(0)), uintptr(uint32(s.Config.PrizeCount)))
	if c := s.Consumables; c != nil {
		_, _, _ = a.setHerb.Call(battle, uintptr(uint32(c.HolyHerbStock)))
		var p uintptr
		if len(c.MPWatch) > 0 {
			p = uintptr(unsafe.Pointer(&c.MPWatch[0]))
		}
		_, _, _ = a.setMPWatch.Call(battle, p, uintptr(len(c.MPWatch)), uintptr(uint32(c.HolyHerbMaxUses)))
		runtime.KeepAlive(c.MPWatch)
		for i, row := range c.Items {
			n, _, _ := a.addItem.Call(battle, 0, uintptr(uint32(row.Parameter)), uintptr(row.AllResidents), uintptr(uint32(row.BonusMin)), uintptr(uint32(row.BonusMax)), uintptr(uint32(row.Stock)))
			if n < uintptr(i+1) {
				return fail(errors.New("native item table overflow"))
			}
		}
		for i, row := range c.Inputs {
			n, _, _ := a.addInput.Call(battle, uintptr(uint32(row.Tick)), uintptr(uint32(row.Phase)), uintptr(uint32(row.Kind)), uintptr(uint32(row.Item)))
			if n < uintptr(i+1) {
				return fail(errors.New("native input table overflow"))
			}
		}
	}
	// strategy_optimizer_native._build_template derives RewardEntitlementWatch
	// scope from enemy skills. Unknown rows and Cure/revive row types fail closed.
	rowsByID := make(map[int32]snapshotRow, len(s.Rows))
	for _, row := range s.Rows {
		rowsByID[row.ID] = row
	}
	scopeAllowed := true
	for _, unit := range s.Units {
		if unit.Team != 1 {
			continue
		}
		for _, skillID := range unit.Skills {
			row, known := rowsByID[skillID]
			if !known || row.Type == 2 || row.Type == 15 {
				scopeAllowed = false
				break
			}
		}
		if !scopeAllowed {
			break
		}
	}
	_, _, _ = a.setScope.Call(battle, uintptr(boolByte(scopeAllowed)))
	return battle, nil
}

// RawWindowsEngine loads the native kernel once and prepares every task directly
// from its own full engine_snapshot JSON. The opaque native-template pathway is
// intentionally not used here.
type RawWindowsEngine struct {
	dll                             *syscall.DLL
	abi                             rawABI
	executors                       int
	kernelSHA                       string
	kernelPath                      string
	battleSize, reportSize          uint32
	encounterSize, encounterVersion uint32
	mu                              sync.Mutex
	closed                          bool
	jobs                            chan preparedSnapshotJob
	replies                         chan preparedSnapshotReply
	workers                         sync.WaitGroup
	accepted                        atomic.Uint64
	completed                       atomic.Uint64
	battleCalls                     atomic.Uint64
	successfulBattles               atomic.Uint64
	inFlight                        atomic.Int64
	active                          atomic.Int64
	completedUnharvested            atomic.Int64
	workerBusyNanos                 atomic.Uint64
	nativeCallNanos                 atomic.Uint64
	progressCallbackMu              sync.RWMutex
	progressCallback                func(NativeEngineProgress)
	admissionControlMu              sync.RWMutex
	admissionControl                func(PreparedTask) (bool, error)
	progressStateMu                 sync.Mutex
	stageTimingMu                   sync.Mutex
	stageNanoseconds                map[string]uint64
	stageSamples                    map[string]uint64
}

type preparedSnapshotJob struct {
	index int
	task  PreparedTask
}

type preparedSnapshotReply struct {
	index  int
	worker int
	result BattleResult
	err    error
}

func NewRawWindowsEngine(kernelPath, kernelSHA256 string, executors int) (*RawWindowsEngine, error) {
	if executors < 1 || executors > 256 {
		return nil, errors.New("executor count outside 1..256")
	}
	if kernelSHA256 == "" {
		return nil, errors.New("kernel SHA-256 identity is required")
	}
	kernelPath, e := filepath.Abs(kernelPath)
	if e != nil {
		return nil, fmt.Errorf("resolve kernel path: %w", e)
	}
	bytes, e := os.ReadFile(kernelPath)
	if e != nil {
		return nil, e
	}
	sum := sha256.Sum256(bytes)
	got := hex.EncodeToString(sum[:])
	if got != kernelSHA256 {
		return nil, errors.New("kernel DLL SHA-256 differs from requested identity")
	}
	d, a, e := openRawABI(kernelPath)
	if e != nil {
		return nil, e
	}
	size, e := getProc(d, "ka_sizeof_battle")
	if e != nil {
		_ = d.Release()
		return nil, e
	}
	rsize, e := getProc(d, "ka_sizeof_report")
	if e != nil {
		_ = d.Release()
		return nil, e
	}
	bs, _, _ := size.Call()
	rs, _, _ := rsize.Call()
	if bs == 0 || bs > 64<<20 || rs != 584 {
		_ = d.Release()
		return nil, fmt.Errorf("unsupported native ABI size battle=%d report=%d", bs, rs)
	}
	// Verify the C mirrors before any candidate can reach the simulation.
	unitSize, e := getProc(d, "ka_sizeof_unit")
	if e != nil {
		_ = d.Release()
		return nil, e
	}
	us, _, _ := unitSize.Call()
	if us != unsafe.Sizeof(nUnit{}) {
		_ = d.Release()
		return nil, fmt.Errorf("Go KaUnit mirror size %d != DLL ABI %d", unsafe.Sizeof(nUnit{}), us)
	}
	entitySize, e := getProc(d, "ka_sizeof_entity")
	if e != nil {
		_ = d.Release()
		return nil, e
	}
	es, _, _ := entitySize.Call()
	if es != unsafe.Sizeof(nEntity{}) {
		_ = d.Release()
		return nil, fmt.Errorf("Go KaEntity mirror size %d != DLL ABI %d", unsafe.Sizeof(nEntity{}), es)
	}
	skillSize, e := getProc(d, "ka_sizeof_skill")
	if e != nil {
		_ = d.Release()
		return nil, e
	}
	ss, _, _ := skillSize.Call()
	if ss != unsafe.Sizeof(nSkill{}) {
		_ = d.Release()
		return nil, fmt.Errorf("Go KaSkill mirror size %d != DLL ABI %d", unsafe.Sizeof(nSkill{}), ss)
	}
	if a.encounterReport == nil {
		_ = d.Release()
		return nil, errors.New("native kernel lacks required encounter telemetry v3 exports; use the versioned encounter kernel")
	}
	var encounterSizeValue, encounterVersionValue uint32
	if a.encounterReport != nil {
		esize, _, _ := a.encounterSize.Call()
		version, _, _ := a.encounterVersion.Call()
		if esize != unsafe.Sizeof(nEncounterReport{}) || version != 3 {
			_ = d.Release()
			return nil, fmt.Errorf("unsupported encounter ABI size/version %d/%d (expected %d/3)", esize, version, unsafe.Sizeof(nEncounterReport{}))
		}
		encounterSizeValue, encounterVersionValue = uint32(esize), uint32(version)
	}
	engine := &RawWindowsEngine{
		dll: d, abi: a, executors: executors, kernelSHA: got, kernelPath: kernelPath,
		battleSize: uint32(bs), reportSize: uint32(rs), encounterSize: encounterSizeValue,
		encounterVersion: encounterVersionValue,
		stageNanoseconds: make(map[string]uint64), stageSamples: make(map[string]uint64),
		// One queued task per worker bounds accepted-but-not-harvested work to
		// roughly two tasks per executor while keeping workers fed.
		jobs: make(chan preparedSnapshotJob, executors),
		// A batch is limited to 4096 tasks. Buffer one reply per task so workers
		// cannot block while the caller is still filling the bounded job queue.
		replies: make(chan preparedSnapshotReply, maxTasksPerBatch),
	}
	for worker := 0; worker < executors; worker++ {
		engine.workers.Add(1)
		go func(worker int) {
			defer engine.workers.Done()
			for job := range engine.jobs {
				engine.progressStateMu.Lock()
				engine.active.Add(1)
				engine.progressStateMu.Unlock()
				started := time.Now()
				result, runErr := engine.runPrepared(worker, job.task)
				engine.workerBusyNanos.Add(uint64(time.Since(started).Nanoseconds()))
				engine.recordStageTimings(result.StageTimings)
				engine.progressStateMu.Lock()
				engine.active.Add(-1)
				engine.completed.Add(1)
				if result.Completed {
					engine.successfulBattles.Add(1)
				}
				engine.completedUnharvested.Add(1)
				engine.progressStateMu.Unlock()
				engine.replies <- preparedSnapshotReply{index: job.index, worker: worker, result: result, err: runErr}
			}
		}(worker)
	}
	return engine, nil
}

func (e *RawWindowsEngine) SetProgressCallback(callback func(NativeEngineProgress)) {
	e.progressCallbackMu.Lock()
	e.progressCallback = callback
	e.progressCallbackMu.Unlock()
}

// SetAdmissionControl installs an optional caller-thread admission check.
// Returning false pauses further submissions without cancelling accepted jobs.
func (e *RawWindowsEngine) SetAdmissionControl(control func(PreparedTask) (bool, error)) {
	e.admissionControlMu.Lock()
	e.admissionControl = control
	e.admissionControlMu.Unlock()
}

func (e *RawWindowsEngine) currentAdmissionControl() func(PreparedTask) (bool, error) {
	e.admissionControlMu.RLock()
	control := e.admissionControl
	e.admissionControlMu.RUnlock()
	return control
}

func (e *RawWindowsEngine) Progress() NativeEngineProgress {
	e.progressStateMu.Lock()
	accepted := e.accepted.Load()
	completed := e.completed.Load()
	battleCalls := e.battleCalls.Load()
	successfulBattles := e.successfulBattles.Load()
	inFlight, active := e.inFlight.Load(), e.active.Load()
	unharvested := e.completedUnharvested.Load()
	ready := len(e.jobs)
	dispatchPending := inFlight - active - unharvested - int64(ready)
	if dispatchPending < 0 {
		dispatchPending = 0
	}
	if unharvested < 0 {
		unharvested = 0
	}
	e.progressStateMu.Unlock()
	e.stageTimingMu.Lock()
	stageNanoseconds := make(map[string]uint64, len(e.stageNanoseconds))
	for key, value := range e.stageNanoseconds {
		stageNanoseconds[key] = value
	}
	stageSamples := make(map[string]uint64, len(e.stageSamples))
	for key, value := range e.stageSamples {
		stageSamples[key] = value
	}
	e.stageTimingMu.Unlock()
	return NativeEngineProgress{
		Accepted: accepted, Completed: completed, BattleCalls: battleCalls, SuccessfulBattles: successfulBattles, InFlight: uint64(inFlight),
		ReadyQueueDepth: uint64(ready), DispatchPending: uint64(dispatchPending), ExecutingWorkers: uint64(active),
		CompletedUnharvested: uint64(unharvested),
		WorkerBusySeconds:    float64(e.workerBusyNanos.Load()) / 1e9,
		NativeCallSeconds:    float64(e.nativeCallNanos.Load()) / 1e9,
		StageNanoseconds:     stageNanoseconds, StageSamples: stageSamples,
	}
}

func (e *RawWindowsEngine) publishProgress() {
	e.progressCallbackMu.RLock()
	callback := e.progressCallback
	e.progressCallbackMu.RUnlock()
	if callback != nil {
		callback(e.Progress())
	}
}

func (e *RawWindowsEngine) recordStageTimings(timings map[string]int64) {
	if len(timings) == 0 {
		return
	}
	e.stageTimingMu.Lock()
	for stage, nanos := range timings {
		if nanos < 0 {
			continue
		}
		e.stageNanoseconds[stage] += uint64(nanos)
		e.stageSamples[stage]++
	}
	e.stageTimingMu.Unlock()
}

func (e *RawWindowsEngine) RunPreparedBatch(tasks []PreparedTask) ([]BattleResult, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.closed {
		return nil, errors.New("native engine closed")
	}
	if len(tasks) == 0 || len(tasks) > maxTasksPerBatch {
		return nil, fmt.Errorf("batch size must be 1..%d", maxTasksPerBatch)
	}
	results := make([]BattleResult, len(tasks))
	progressTicker := time.NewTicker(250 * time.Millisecond)
	defer progressTicker.Stop()
	submitted := 0
	harvested := 0
	var admissionErr error
	paused := false
	seen := make([]bool, len(tasks))
	harvestOne := func() error {
		for {
			var reply preparedSnapshotReply
			select {
			case reply = <-e.replies:
			case <-progressTicker.C:
				e.publishProgress()
				if control := e.currentAdmissionControl(); control != nil && submitted < len(tasks) {
					admit, err := control(tasks[submitted])
					if err != nil {
						if admissionErr == nil {
							admissionErr = err
						}
						paused = true
					} else if !admit {
						paused = true
					}
				}
				continue
			}
			if reply.index < 0 || reply.index >= submitted || seen[reply.index] {
				return errors.New("resident native worker returned invalid task index")
			}
			seen[reply.index] = true
			result := reply.result
			if reply.err != nil {
				if result.ID == "" {
					result = BattleResult{ID: tasks[reply.index].ID, Executor: reply.worker, MathSeed: tasks[reply.index].MathSeed, LibSeed: tasks[reply.index].LibSeed, FollowerDraws: tasks[reply.index].FollowerDraws,
						Provenance: tasks[reply.index].Provenance, KernelPath: e.kernelPath, KernelSHA256: e.kernelSHA,
						EncounterReportSize: e.encounterSize, EncounterReportVersion: e.encounterVersion, StageTimings: result.StageTimings}
				}
				result.Error = joinNativeError(result.Error, reply.err.Error())
				result.Completed = false
				result.Earned = nil
				result.EarnedValid = false
			}
			results[reply.index] = result
			e.progressStateMu.Lock()
			e.inFlight.Add(-1)
			e.completedUnharvested.Add(-1)
			e.progressStateMu.Unlock()
			harvested++
			e.publishProgress()
			return nil
		}
	}
	for submitted < len(tasks) {
		if int(e.inFlight.Load()) >= e.executors*2 {
			if err := harvestOne(); err != nil {
				return nil, err
			}
			if paused {
				break
			}
			continue
		}
		if control := e.currentAdmissionControl(); control != nil {
			admit, err := control(tasks[submitted])
			if err != nil {
				admissionErr, paused = err, true
				break
			}
			if !admit {
				paused = true
				break
			}
		}
		job := preparedSnapshotJob{index: submitted, task: tasks[submitted]}
		sent := false
		for !sent {
			// Send nonblocking while holding the progress lock so a worker cannot
			// publish a completion before accepted/in-flight counts include the job.
			e.progressStateMu.Lock()
			select {
			case e.jobs <- job:
				e.accepted.Add(1)
				e.inFlight.Add(1)
				sent = true
			default:
			}
			e.progressStateMu.Unlock()
			if sent {
				submitted++
				if submitted < len(tasks) && int(e.inFlight.Load()) >= e.executors*2 {
					if err := harvestOne(); err != nil {
						return nil, err
					}
				}
				break
			}
			select {
			case <-progressTicker.C:
				e.publishProgress()
				if control := e.currentAdmissionControl(); control != nil {
					admit, err := control(tasks[submitted])
					if err != nil {
						admissionErr, paused = err, true
					} else if !admit {
						paused = true
					}
					if paused {
						break
					}
				}
			}
			if paused {
				break
			}
		}
		if paused {
			break
		}
	}
	e.publishProgress()
	for harvested < submitted {
		if err := harvestOne(); err != nil {
			return nil, err
		}
	}
	// Per-task failures are carried in BattleResult.Error so callers can durably
	// retain diagnostics and successful neighbors from the same batch.
	return results[:submitted], admissionErr
}
func (e *RawWindowsEngine) runPrepared(worker int, t PreparedTask) (result BattleResult, runErr error) {
	var timings map[string]int64
	if t.MeasureTiming {
		timings = make(map[string]int64, 5)
		defer func() { result.StageTimings = timings }()
	}
	if t.ID == "" || len(t.Provenance) == 0 || len(t.Snapshot) == 0 {
		return BattleResult{}, errors.New("task id, provenance, and prepared snapshot required")
	}
	if t.MathSeed < 0 || t.LibSeed < 0 {
		return BattleResult{}, errors.New("seed outside canonical nonnegative signed31-bit range")
	}
	if t.TickLimit < 1 || t.TickLimit > 30000 || t.PolicyCode < 0 || t.PolicyCode > 2 {
		return BattleResult{}, errors.New("tick limit or policy outside bounds")
	}
	if t.FollowerDraws > 1_000_000 {
		return BattleResult{}, errors.New("follower selection draw count exceeds supported bound")
	}
	var provenance map[string]json.RawMessage
	if err := json.Unmarshal(t.Provenance, &provenance); err != nil || provenance == nil {
		return BattleResult{}, errors.New("task provenance must be a JSON object")
	}
	for key, want := range map[string]int32{"mathSeed": t.MathSeed, "libSeed": t.LibSeed} {
		var got int32
		if err := json.Unmarshal(provenance[key], &got); err != nil || got != want {
			return BattleResult{}, fmt.Errorf("task provenance %s does not match prepared task", key)
		}
	}
	var hydrateStart time.Time
	if timings != nil {
		hydrateStart = time.Now()
	}
	battle, err := buildNativeSnapshot(e.abi, t.Snapshot)
	if timings != nil {
		timings["hydration"] = time.Since(hydrateStart).Nanoseconds()
	}
	if err != nil {
		return BattleResult{}, err
	}
	defer func() {
		if timings != nil {
			start := time.Now()
			_, _, _ = e.abi.free.Call(battle)
			timings["arena_free"] = time.Since(start).Nanoseconds()
			return
		}
		_, _, _ = e.abi.free.Call(battle)
	}()
	// engine_snapshot is captured before the candidate's native template applies
	// the scenario's follower-selection draws. Retain its checksum, then replay
	// those lib draws exactly once before the battle starts.
	var setupStart time.Time
	if timings != nil {
		setupStart = time.Now()
	}
	snapshotBeforeSkip, _, _ := e.abi.checksum.Call(battle)
	before := snapshotBeforeSkip
	if t.FollowerDraws != 0 {
		_, _, _ = e.abi.skip.Call(battle, uintptr(t.FollowerDraws))
		before, _, _ = e.abi.checksum.Call(battle)
	}
	var traceBattle uintptr
	if t.Trace != nil {
		if err := validateTraceABI(e.abi); err != nil {
			return BattleResult{}, err
		}
		traceBattle, _, _ = e.abi.clone.Call(battle)
		if traceBattle == 0 {
			return BattleResult{}, errors.New("native clone failed before trace capture")
		}
		defer e.abi.free.Call(traceBattle)
	}
	if timings != nil {
		timings["pre_run_state"] = time.Since(setupStart).Nanoseconds()
	}
	var raw [584]byte
	nativeRunStart := time.Now()
	if timings != nil {
		nativeRunStart = time.Now()
	}
	status, _, _ := e.abi.run.Call(battle, uintptr(t.TickLimit), uintptr(uint32(t.PolicyCode)), uintptr(unsafe.Pointer(&raw[0])))
	e.battleCalls.Add(1)
	e.nativeCallNanos.Add(uint64(time.Since(nativeRunStart).Nanoseconds()))
	var retrievalStart time.Time
	if timings != nil {
		timings["native_call"] = time.Since(nativeRunStart).Nanoseconds()
		retrievalStart = time.Now()
		defer func() { timings["report_retrieval_decode"] = time.Since(retrievalStart).Nanoseconds() }()
	}
	var again [584]byte
	getter, _, _ := e.abi.report.Call(battle, uintptr(uint32(t.PolicyCode)), uintptr(unsafe.Pointer(&again[0])))
	if getter != 0 {
		return BattleResult{}, fmt.Errorf("native report getter status %d", getter)
	}
	if raw != again {
		return BattleResult{}, errors.New("native run report differs from report getter")
	}
	var stats [4]uint64
	stat, _, _ := e.abi.stats.Call(battle, uintptr(unsafe.Pointer(&stats[0])))
	if stat != 0 {
		return BattleResult{}, fmt.Errorf("native stats getter status %d", stat)
	}
	after, _, _ := e.abi.checksum.Call(battle)
	runtime.KeepAlive(raw)
	runtime.KeepAlive(again)
	runtime.KeepAlive(stats)
	fields := reportValues(raw[:])
	reportJSON, err := json.Marshal(fields)
	if err != nil {
		return BattleResult{}, err
	}
	snapshotHash := sha256.Sum256(t.Snapshot)
	result = BattleResult{ID: t.ID, Executor: worker, MathSeed: t.MathSeed, LibSeed: t.LibSeed, FollowerDraws: t.FollowerDraws, Provenance: t.Provenance, Status: int32(status), SnapshotChecksumBeforeRun: uint64(before), SnapshotChecksumBeforeFollowerSkip: uint64(snapshotBeforeSkip), ChecksumAfter: uint64(after), SnapshotSHA256: hex.EncodeToString(snapshotHash[:]), KernelPath: e.kernelPath, KernelSHA256: e.kernelSHA, NativeBattleSize: e.battleSize, NativeReportSize: e.reportSize, EncounterReportSize: e.encounterSize, EncounterReportVersion: e.encounterVersion, Stats: NativeStats{NotifyCalls: stats[0], SubsetChecks: stats[1], BucketLookups: stats[2], BucketSteps: stats[3]}, Report: NativeReport{Raw: raw, Values: fields}, RawReportBytes: append([]byte(nil), raw[:]...), ReportJSON: reportJSON, Completed: status == 0, Earned: nil, EarnedValid: false}
	if status != 0 {
		result.Error = fmt.Sprintf("ka_run_battle status %d", status)
	} else {
		result.Earned, result.EarnedBasis = canonicalChestCount(fields)
		result.EarnedValid = result.Earned != nil
	}
	if e.abi.encounterReport != nil {
		var encounter nEncounterReport
		encStatus, _, _ := e.abi.encounterReport.Call(battle, uintptr(uint32(t.PolicyCode)), uintptr(unsafe.Pointer(&encounter)))
		runtime.KeepAlive(encounter)
		if encStatus != 0 {
			result.Error = joinNativeError(result.Error, fmt.Sprintf("ka_encounter_report status %d", encStatus))
		} else if encounter.Version != 3 || encounter.OwnCount < 0 || encounter.OwnCount > int32(len(encounter.OwnIdentity)) {
			result.Error = joinNativeError(result.Error, fmt.Sprintf("invalid encounter report version/count %d/%d", encounter.Version, encounter.OwnCount))
		} else {
			result.EncounterReportVersion = uint32(encounter.Version)
			result.EncounterReportRaw = append([]byte(nil), unsafe.Slice((*byte)(unsafe.Pointer(&encounter)), int(unsafe.Sizeof(encounter)))...)
			result.EncounterReport = encounterValues(encounter)
		}
	}
	if result.Error != "" {
		result.Completed = false
		result.Earned = nil
		result.EarnedValid = false
	}
	if t.Trace != nil {
		trace, traceErr := collectNativeTrace(e.abi, traceBattle, t)
		if traceErr != nil {
			return result, fmt.Errorf("native trace capture: %w", traceErr)
		}
		trace.FinalReport = result.Report.Values
		trace.FeatureReport = nativeFeatureReport(result)
		result.Trace = trace
	}
	return result, nil
}

func joinNativeError(existing, next string) string {
	if existing == "" {
		return next
	}
	return existing + "; " + next
}

// canonicalChestCount mirrors strategy_optimizer.chest_count over the native
// reward observer fields. This is a produced/queued chest count, not an
// inventory receipt; callbacks alone never become an earned count.
func canonicalChestCount(fields map[string]any) (*int32, string) {
	read := func(key string) (int32, bool) {
		switch x := fields[key].(type) {
		case int32:
			return x, true
		case int:
			return int32(x), true
		case float64:
			return int32(x), true
		default:
			return 0, false
		}
	}
	verdict, ok := read("verdict")
	if !ok || verdict == 0 {
		return nil, "unresolved"
	}
	if verdict == 2 {
		v := int32(0)
		return &v, "native-loss-gate"
	}
	if verdict != 1 {
		return nil, "unknown-verdict"
	}
	cert, _ := read("certificate_held")
	delta, _ := read("post_certificate_delta")
	if cert != 0 && delta == 0 {
		if value, valid := read("certificate_pending"); valid && value >= 0 {
			return &value, "certified-award"
		}
	}
	if pending, valid := read("pending_final"); valid && pending >= 0 {
		return &pending, "queued-at-victory"
	}
	if pending, valid := read("pre_verdict_prize_callbacks"); valid && pending >= 0 {
		return &pending, "queued-at-victory-callback-fallback"
	}
	return nil, "unknown-reward-count"
}

func encounterValues(r nEncounterReport) map[string]any {
	own := make([]map[string]any, int(r.OwnCount))
	for i := range own {
		own[i] = map[string]any{"identity": r.OwnIdentity[i], "firstDeathTick": r.OwnFirstDeathTick[i], "firstLeavingTick": r.OwnFirstLeavingTick[i], "survived": r.OwnSurvived[i] != 0}
	}
	return map[string]any{"version": r.Version, "own": own, "enemyResolvedAttacks": r.EnemyResolvedAttacks, "enemyHits": r.EnemyHits, "enemyMisses": r.EnemyMisses, "enemyRolls": r.EnemyRolls, "enemyRollHits": r.EnemyRollHits, "enemyRollMisses": r.EnemyRollMisses, "counterChecks": r.CounterChecks, "counterEnqueues": r.CounterEnqueues, "bossPostdeathAttempts": r.BossPostdeathAttempts, "bossPostdeathLands": r.BossPostdeathLands, "bossDeathTick": r.BossDeathTick, "bossReentries": r.BossReentries, "bossLeavings": r.BossLeavings, "bossPostdeathGapCount": r.BossPostdeathGapCount, "bossPostdeathGapMin": r.BossPostdeathGapMin, "bossPostdeathGapMax": r.BossPostdeathGapMax, "bossPostdeathGapSum": r.BossPostdeathGapSum, "futureHitsAtDeath": r.FutureHitsAtDeath, "futureHitsIncludeExecuting": r.FutureHitsIncludeExecuting, "bossAccessFirstCommand": r.BossAccessFirstCommand, "bossAccessFirstAttempt": r.BossAccessFirstAttempt, "targetableTicks": r.TargetableTicks, "usingSkillTicks": r.UsingSkillTicks, "bossDamagingResets": r.BossDamagingResets, "itemUsesOk": r.ItemUsesOK, "finishDispatchedChests": r.FinishDispatchedChests}
}
func (e *RawWindowsEngine) KernelIdentity() string { return e.kernelSHA }
func (e *RawWindowsEngine) Provenance() KernelProvenance {
	return KernelProvenance{KernelPath: e.kernelPath, KernelSHA256: e.kernelSHA, NativeBattleSize: e.battleSize, NativeReportSize: e.reportSize, EncounterSize: e.encounterSize, EncounterVersion: e.encounterVersion}
}
func (e *RawWindowsEngine) Close() error {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.closed {
		return nil
	}
	e.closed = true
	close(e.jobs)
	e.workers.Wait()
	return e.dll.Release()
}
