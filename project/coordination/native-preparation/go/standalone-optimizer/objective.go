package main

import (
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"reflect"
	"sort"
	"strconv"
)

// The lane objective is opt-in so existing 09d624 snapshots and runs retain
// their original earned-only interpretation. Its decision contract mirrors
// strategy_optimizer.py's lane_record / elite_lanes / choose_parent helpers.
const (
	MechanicsObjectiveVersion = 3
	MechanicsLanePool         = 4
	MechanicsExploreEvery     = 5
	MechanicsPotentialFloor   = 10
)

const (
	ObjectiveModeEarnedOnly    = "earned-only-v1"
	ObjectiveModeMechanicLanes = "mechanism-lanes-v3"
	LearnerModeLegacy          = "legacy"
	LearnerModeBranching       = "branching"
	ConstraintProfileFixedDPS  = "fixed-dps-3stats-v1"
)

var controlledMutableParameters = []int64{13, 15, 16}
var controlledFixedParameters = map[int64]int64{10: 2500, 11: 2500, 14: 2500, 19: 18, 12: 204, 18: 376}
var learnerStatScales = []float64{1.05, 1.15, 1.4, 2, 3, .95, .7, .5}

const learnerOperatorFloor = .35

type LearnerOperatorStat struct {
	Attempts uint64 `json:"attempts"`
	Improved uint64 `json:"improved"`
}

type LearnerScaleStat struct {
	Attempts uint64 `json:"attempts"`
	Improved uint64 `json:"improved"`
	Planned  uint64 `json:"planned"`
}

type LearnerStatScale struct {
	Parameter int64   `json:"parameter"`
	Scale     float64 `json:"scale"`
}

// strategyRegion mirrors strategy_learner.strategy_region for the recovered
// raw scenario fields used by the controlled profile.
func strategyRegion(raw json.RawMessage) (string, error) {
	var scenario RawScenario
	if err := json.Unmarshal(raw, &scenario); err != nil {
		return "", err
	}
	parameters := []int64{10, 11, 13, 14, 15, 16, 18, 19}
	humans := make([]any, 0, len(scenario.OwnUnits))
	for _, unit := range scenario.OwnUnits {
		if unit.Human == nil || !*unit.Human {
			continue
		}
		parameterValues := map[string]map[string]json.RawMessage{}
		if len(unit.Parameters) != 0 {
			if err := json.Unmarshal(unit.Parameters, &parameterValues); err != nil {
				return "", err
			}
		}
		buckets := make([]int, 0, len(parameters))
		for _, id := range parameters {
			entry := parameterValues[strconv.FormatInt(id, 10)]
			var rawValue int64
			if value := entry["rawValue"]; len(value) != 0 {
				_ = json.Unmarshal(value, &rawValue)
			}
			magnitude := math.Max(1, math.Abs(float64(rawValue)))
			buckets = append(buckets, int(math.Log10(magnitude)*4))
		}
		skills := append([]int64(nil), unit.Skills...)
		sort.Slice(skills, func(i, j int) bool { return skills[i] < skills[j] })
		weapon := int64(0)
		if unit.WeaponID != nil {
			weapon = *unit.WeaponID
		}
		grid := int64(-1)
		var gridValue *int64
		if len(unit.Grid) != 0 && json.Unmarshal(unit.Grid, &gridValue) == nil && gridValue != nil {
			grid = *gridValue
		}
		humans = append(humans, []any{skills, unit.Invocation, buckets, weapon, grid})
	}
	payload, err := json.Marshal(humans)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(payload)
	return fmt.Sprintf("%x", sum[:8]), nil
}

// controlledScenarioSignature ignores only the three allowed raw stat values
// on DPS unit index 0. Every other scenario field remains identity bearing.
func controlledScenarioSignature(raw json.RawMessage) (string, error) {
	var top map[string]json.RawMessage
	if err := json.Unmarshal(raw, &top); err != nil {
		return "", err
	}
	delete(top, "mathSeed")
	delete(top, "libSeed")
	var units []map[string]json.RawMessage
	if err := json.Unmarshal(top["ownUnits"], &units); err != nil || len(units) == 0 {
		return "", errors.New("controlled profile requires a nonempty roster")
	}
	var human bool
	if err := json.Unmarshal(units[0]["human"], &human); err != nil || !human {
		return "", errors.New("controlled profile requires DPS human at ownUnits index 0")
	}
	var params map[string]json.RawMessage
	if err := json.Unmarshal(units[0]["parameters"], &params); err != nil {
		return "", errors.New("controlled profile DPS parameters are invalid")
	}
	for _, pid := range controlledMutableParameters {
		key := strconv.FormatInt(pid, 10)
		var entry map[string]json.RawMessage
		if err := json.Unmarshal(params[key], &entry); err != nil || entry["rawValue"] == nil {
			return "", fmt.Errorf("controlled profile missing mutable parameter %d rawValue", pid)
		}
		entry["rawValue"] = json.RawMessage("null")
		encoded, err := json.Marshal(entry)
		if err != nil {
			return "", err
		}
		params[key] = encoded
	}
	encodedParams, err := json.Marshal(params)
	if err != nil {
		return "", err
	}
	units[0]["parameters"] = encodedParams
	encodedUnits, err := json.Marshal(units)
	if err != nil {
		return "", err
	}
	top["ownUnits"] = encodedUnits
	canonical, err := json.Marshal(top)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(canonical)
	return fmt.Sprintf("%x", sum[:]), nil
}

func validateControlledScenario(raw json.RawMessage) (string, error) {
	var scenario RawScenario
	if err := json.Unmarshal(raw, &scenario); err != nil {
		return "", err
	}
	if len(scenario.OwnUnits) == 0 || scenario.OwnUnits[0].Human == nil || !*scenario.OwnUnits[0].Human {
		return "", errors.New("controlled profile requires DPS human at ownUnits index 0")
	}
	var params map[string]map[string]json.RawMessage
	if err := json.Unmarshal(scenario.OwnUnits[0].Parameters, &params); err != nil {
		return "", errors.New("controlled profile DPS parameters are invalid")
	}
	for pid, expected := range controlledFixedParameters {
		var value int64
		entry := params[strconv.FormatInt(pid, 10)]
		if len(entry["rawValue"]) == 0 || json.Unmarshal(entry["rawValue"], &value) != nil || value != expected {
			return "", fmt.Errorf("controlled profile parameter %d must remain %d", pid, expected)
		}
	}
	return controlledScenarioSignature(raw)
}

func chooseLearnerScale(random func() uint64, stats map[int64]map[string]LearnerScaleStat, parameter int64) (float64, error) {
	byScale := stats[parameter]
	weights := make([]float64, len(learnerStatScales)+1)
	var improvedScales float64
	for i, scale := range learnerStatScales {
		entry := byScale[learnerScaleKey(scale)]
		weight := 1 + 2*float64(entry.Improved) - .15*math.Max(0, float64(entry.Attempts)-float64(entry.Improved))
		weights[i] = math.Max(learnerOperatorFloor, weight)
		if entry.Improved > 0 {
			improvedScales++
		}
	}
	if byScale["jump"].Improved > 0 {
		improvedScales++
	}
	weights[len(weights)-1] = 1 + improvedScales
	index, err := weightedIndex(random, weights)
	if err != nil {
		return 0, err
	}
	if index == len(learnerStatScales) {
		return 0, nil // uniform full-bound jump
	}
	return learnerStatScales[index], nil
}

func weightedIndex(random func() uint64, weights []float64) (int, error) {
	var total float64
	for _, weight := range weights {
		if weight < 0 || math.IsNaN(weight) || math.IsInf(weight, 0) {
			return 0, errors.New("invalid learner weight")
		}
		total += weight
	}
	if total <= 0 || len(weights) == 0 {
		return 0, errors.New("empty learner weights")
	}
	target := float64(random()>>11) / (1 << 53) * total
	var upto float64
	for i, weight := range weights {
		upto += weight
		if target <= upto {
			return i, nil
		}
	}
	return len(weights) - 1, nil
}

func learnerStatTarget(randomFloat func() float64, randomInt func(int64, int64) int64, current, minimum, maximum int64, scale float64) int64 {
	if scale == 0 || current <= 0 {
		return randomInt(minimum, maximum)
	}
	value := current
	if scale >= 1 {
		if randomFloat() < .5 {
			value = int64(math.Round(float64(current) * scale))
		} else {
			value = int64(math.Round(float64(current) / scale))
		}
	} else {
		value = int64(math.Round(float64(current) * scale))
	}
	if value < minimum {
		value = minimum
	}
	if value > maximum {
		value = maximum
	}
	if value == current {
		if value < maximum {
			value++
		} else {
			value--
		}
	}
	return value
}

var mechanicsLaneNames = []string{"earned", "potential", "setup", "efficiency"}

var mechanicsProgressFields = []string{
	"postDeathPrizes",
	"postDeathBossLeavings",
	"postDeathBossReentries",
	"commandsReleasedAfterDeath",
	"commandsReleasedAfterDeathTargetingBoss",
	"storedCommandsAtDeath",
	"storedCommandsTargetingBossAtDeath",
	"maxSimultaneousStoredCommands",
	"maxSimultaneousCommandsTargetingBoss",
	"storedTargetHoldersPeak",
	"storedTargetHoldersAtDeath",
	"commandsTargetingBoss",
	"commandsTargetingBossReleased",
}

var mechanicsReportProgress = map[string]string{
	"postDeathPrizes":                         "progress_post_death_prizes",
	"commandsReleasedAfterDeathTargetingBoss": "progress_released_after_death_targeting_boss",
	"storedCommandsTargetingBossAtDeath":      "progress_stored_targeting_boss_at_death",
	"maxSimultaneousCommandsTargetingBoss":    "progress_max_targeting_boss",
	"commandsTargetingBossReleased":           "progress_commands_targeting_boss_released",
	"storedTargetHoldersPeak":                 "progress_target_holders_peak",
	"maxSimultaneousStoredCommands":           "progress_max_stored",
	"storedCommandsAtDeath":                   "progress_stored_at_death",
	"storedTargetHoldersAtDeath":              "progress_stored_target_holders_at_death",
	"postDeathBossLeavings":                   "progress_post_death_leavings",
	"postDeathBossReentries":                  "progress_post_death_reentries",
	"commandsTargetingBoss":                   "progress_commands_targeting_boss",
	"commandsReleasedAfterDeath":              "progress_released_after_death",
}

func mechanicsObservationFromBattleResult(result BattleResult) (MechanicsObservation, *float64) {
	if !result.Completed || result.Error != "" || result.Report.Values == nil {
		return MechanicsObservation{}, nil
	}
	verdict, ok := numericReportField(result.Report.Values, "verdict")
	if !ok || (verdict != 1 && verdict != 2) {
		return MechanicsObservation{}, nil
	}
	observation := MechanicsObservation{Include: true, Win: verdict == 1}
	var earned *float64
	if observation.Win && result.EarnedValid && result.Earned != nil {
		v := float64(*result.Earned)
		earned = &v
		observation.Earned = &v
	}
	if potential, ok := numericReportField(result.Report.Values, "pre_verdict_prize_callbacks"); ok && potential >= 0 {
		observation.Potential = &potential
	}
	if resources, ok := numericReportField(result.Report.Values, "resource_uses"); ok && resources >= 0 {
		observation.Resources = &resources
	}
	progress := make(map[string]float64)
	for target, source := range mechanicsReportProgress {
		if value, found := numericReportField(result.Report.Values, source); found && value >= 0 {
			progress[target] = value
		}
	}
	if len(progress) != 0 {
		observation.Progress, observation.HasProgress = progress, true
	}
	return observation, earned
}

func numericReportField(fields map[string]any, name string) (float64, bool) {
	switch value := fields[name].(type) {
	case int:
		return float64(value), true
	case int32:
		return float64(value), true
	case int64:
		return float64(value), true
	case uint32:
		return float64(value), true
	case float64:
		return value, true
	default:
		return 0, false
	}
}

func observeSearchBattleResult(search *Search, result BattleResult) error {
	observation, earned := mechanicsObservationFromBattleResult(result)
	return search.ObserveMechanics(result.ID, earned, observation)
}

type MechanicsFeedbackReplayFixture struct {
	Schema           int               `json:"schema"`
	ObjectiveVersion int               `json:"objectiveVersion"`
	Seed             uint64            `json:"seed"`
	Tables           json.RawMessage   `json:"tables"`
	Candidates       []json.RawMessage `json:"candidates"`
	Results          []struct {
		Verdict   int            `json:"verdict"`
		Earned    *float64       `json:"earned"`
		Potential *float64       `json:"potential"`
		Resources *float64       `json:"resources"`
		Progress  map[string]int `json:"progress"`
	} `json:"results"`
}

func RunMechanicsFeedbackReplay(fixture MechanicsFeedbackReplayFixture, priorBytes []byte) (map[string]any, error) {
	if fixture.Schema != 1 || fixture.ObjectiveVersion != MechanicsObjectiveVersion || len(fixture.Candidates) == 0 || len(fixture.Results) == 0 {
		return nil, errors.New("invalid mechanics feedback replay fixture")
	}
	config := SearchConfig{Seed: fixture.Seed, Exploration: .7, PopulationLimit: 128, ObjectiveMode: ObjectiveModeMechanicLanes, ObjectiveVersion: MechanicsObjectiveVersion, LearnerMode: LearnerModeBranching, ConstraintProfile: ConstraintProfileFixedDPS}
	if len(priorBytes) != 0 {
		config.LearnerPriorSHA256 = digest(priorBytes)
	}
	search, err := NewSearchWithTables(fixture.Candidates, config, fixture.Tables)
	if err != nil {
		return nil, err
	}
	if len(priorBytes) != 0 {
		if err := search.ImportMechanismLearnerPrior(priorBytes); err != nil {
			return nil, err
		}
	}
	rows := make([]map[string]any, 0, len(fixture.Results))
	baselineSignature := ""
	for index, evidence := range fixture.Results {
		raw, evaluationID, err := search.Next()
		if err != nil {
			return nil, fmt.Errorf("replay proposal %d: %w", index, err)
		}
		lineage := search.state.EvaluationLineage[evaluationID]
		admitted, err := AdmitRawScenario(raw)
		if err != nil {
			return nil, err
		}
		result := BattleResult{ID: evaluationID, Completed: true, Report: NativeReport{Values: map[string]any{"verdict": int32(evidence.Verdict)}}}
		if evidence.Verdict != 1 && evidence.Verdict != 2 {
			return nil, errors.New("replay verdict must be resolved win(1) or loss(2)")
		}
		if evidence.Earned != nil {
			v := int32(*evidence.Earned)
			result.Earned, result.EarnedValid = &v, evidence.Verdict == 1
		}
		if evidence.Potential != nil {
			result.Report.Values["pre_verdict_prize_callbacks"] = *evidence.Potential
		}
		if evidence.Resources != nil {
			result.Report.Values["resource_uses"] = *evidence.Resources
		}
		for key, value := range evidence.Progress {
			result.Report.Values[key] = int32(value)
		}
		if err := observeSearchBattleResult(search, result); err != nil {
			return nil, err
		}
		candidate := search.state.Candidates[lineage.CandidateID]
		var metrics MechanicsLaneRecord
		if candidate != nil && candidate.Mechanics != nil {
			metrics = candidate.Mechanics.LaneRecord(candidate.ID, candidate.EncounterID, candidate.DefeatCount)
		}
		signature, err := controlledScenarioSignature(admitted.Raw)
		if err != nil {
			return nil, err
		}
		if baselineSignature == "" {
			baselineSignature = search.state.ConstraintReferences[admitted.EncounterID]
		}
		if signature != baselineSignature {
			return nil, fmt.Errorf("replay proposal %d changed a fixed-profile field", index)
		}
		if index > 0 {
			if lineage.Mutation != "set-stat" || (lineage.MutationParameter != 13 && lineage.MutationParameter != 15 && lineage.MutationParameter != 16) {
				return nil, fmt.Errorf("replay proposal %d escaped controlled stat axes", index)
			}
			wantSource := mechanicsParentSources[index%len(mechanicsParentSources)]
			gotSource := lineage.ParentSource
			if wantSource == "mean" {
				wantSource = "mean:fallback-population(no-validation-bank)"
			}
			if gotSource != wantSource {
				return nil, fmt.Errorf("replay proposal %d parent source %q, want %q", index, gotSource, wantSource)
			}
		}
		if index == 1 && (evidence.Verdict != 2 || candidate.Mechanics.Wins != 0 || candidate.Mechanics.EarnedCount != 0 || !containsMechanicsString(candidate.ImprovedLanes, "setup")) {
			return nil, errors.New("replay zero-earned setup improvement was not credited")
		}
		rows = append(rows, map[string]any{"ordinal": index, "candidateId": lineage.CandidateID, "parentId": lineage.ParentID, "parentSource": lineage.ParentSource, "mutation": lineage.Mutation, "mutationParameter": lineage.MutationParameter, "mutationScale": lineage.MutationScale, "mutationTarget": lineage.MutationTarget, "mechanics": metrics, "profileSignature": signature, "improvedLanes": candidate.ImprovedLanes})
	}
	stateBytes, err := search.Snapshot()
	if err != nil {
		return nil, err
	}
	resumed, err := RestoreSearchWithTables(stateBytes, fixture.Tables)
	if err != nil {
		return nil, fmt.Errorf("replay checkpoint roundtrip: %w", err)
	}
	if resumed.state.NextFeedback != uint64(len(fixture.Results)) || resumed.state.LearnerPriorSHA256 != config.LearnerPriorSHA256 {
		return nil, errors.New("replay checkpoint lost feedback or prior identity")
	}
	return map[string]any{"schema": fixture.Schema, "objectiveVersion": fixture.ObjectiveVersion, "objectiveMode": config.ObjectiveMode, "learnerMode": config.LearnerMode, "constraintProfile": config.ConstraintProfile, "learnerPriorSha256": search.state.LearnerPriorSHA256, "learnerPriorLoaded": search.state.LearnerPriorLoaded, "resumeVerified": true, "results": rows, "rootChildren": search.state.RootChildren, "rootImprovedChildren": search.state.RootImprovedChildren, "learnerOperators": search.state.LearnerOperators, "learnerScales": search.state.LearnerScales, "statAnchors": search.state.StatAnchors}, nil
}

func containsMechanicsString(values []string, target string) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}

// MechanicsLaneRecord is deliberately neutral and JSON-compatible with the
// Python objective oracle fixture. Nil maxima/resources preserve unknown data.
type MechanicsLaneRecord struct {
	Candidate     string             `json:"candidate"`
	EncounterID   int64              `json:"encounterId"`
	DefeatCount   int64              `json:"defeatCount"`
	N             int                `json:"n"`
	Wins          int                `json:"wins"`
	EarnedMax     *float64           `json:"earnedMax"`
	EarnedCount   int                `json:"earnedCount"`
	PotentialMax  *float64           `json:"potentialMax"`
	WinInterval   []float64          `json:"winInterval"`
	MeanResources *float64           `json:"meanResources"`
	Progress      map[string]float64 `json:"progress"`
	ProgressRuns  int                `json:"progressRuns"`
}

type MechanicsProposal struct {
	Ordinal    int      `json:"ordinal"`
	Population []string `json:"population"`
	DrawIndex  int      `json:"drawIndex"`
}

type MechanicsParentSelection struct {
	Ordinal    int      `json:"ordinal"`
	DrawIndex  int      `json:"drawIndex"`
	Population []string `json:"population"`
	ChoicePool []string `json:"choicePool"`
	Candidate  string   `json:"candidate"`
	Lane       string   `json:"lane"`
}

// MechanicsObservation is one battle's eligible evidence. Unknown values are
// nil; callers must not turn absent historical fields into zero-valued data.
type MechanicsObservation struct {
	Include     bool               `json:"include"`
	Win         bool               `json:"win"`
	Earned      *float64           `json:"earned,omitempty"`
	Potential   *float64           `json:"potential,omitempty"`
	Resources   *float64           `json:"resources,omitempty"`
	Progress    map[string]float64 `json:"progress,omitempty"`
	HasProgress bool               `json:"hasProgress,omitempty"`
}

type MechanicsCandidateEvidence struct {
	N             int                `json:"n"`
	Wins          int                `json:"wins"`
	EarnedMax     *float64           `json:"earnedMax"`
	EarnedCount   int                `json:"earnedCount"`
	PotentialMax  *float64           `json:"potentialMax"`
	ResourceSum   float64            `json:"resourceSum"`
	ResourceCount int                `json:"resourceCount"`
	Progress      map[string]float64 `json:"progress"`
	ProgressRuns  int                `json:"progressRuns"`
}

func (e *MechanicsCandidateEvidence) Observe(observation MechanicsObservation) {
	if !observation.Include {
		return
	}
	e.N++
	if observation.Win {
		e.Wins++
		if observation.Earned != nil {
			e.EarnedCount++
			e.EarnedMax = maxPointer(e.EarnedMax, *observation.Earned)
		}
	}
	if observation.Potential != nil {
		e.PotentialMax = maxPointer(e.PotentialMax, *observation.Potential)
	}
	if observation.Resources != nil {
		e.ResourceSum += *observation.Resources
		e.ResourceCount++
	}
	if observation.HasProgress {
		e.ProgressRuns++
		if e.Progress == nil {
			e.Progress = make(map[string]float64, len(mechanicsProgressFields))
		}
		for _, field := range mechanicsProgressFields {
			value := observation.Progress[field]
			if value > e.Progress[field] {
				e.Progress[field] = value
			}
		}
	}
}

func (e *MechanicsCandidateEvidence) LaneRecord(candidate string, encounterID, defeatCount int64) MechanicsLaneRecord {
	var resources *float64
	if e != nil && e.ResourceCount > 0 {
		mean := e.ResourceSum / float64(e.ResourceCount)
		resources = &mean
	}
	if e == nil {
		e = &MechanicsCandidateEvidence{}
	}
	return MechanicsLaneRecord{
		Candidate: candidate, EncounterID: encounterID, DefeatCount: defeatCount,
		N: e.N, Wins: e.Wins, EarnedMax: cloneFloat(e.EarnedMax), EarnedCount: e.EarnedCount,
		PotentialMax: cloneFloat(e.PotentialMax), WinInterval: mechanicsWilson(e.Wins, e.N),
		MeanResources: resources, Progress: cloneProgress(e.Progress), ProgressRuns: e.ProgressRuns,
	}
}

func mechanicsWilson(successes, count int) []float64 {
	if count <= 0 {
		return []float64{0, 1}
	}
	const z = 1.96
	p := float64(successes) / float64(count)
	center := (p + z*z/(2*float64(count))) / (1 + z*z/float64(count))
	half := z * math.Sqrt(p*(1-p)/float64(count)+z*z/(4*float64(count*count))) / (1 + z*z/float64(count))
	return []float64{math.Max(0, center-half), math.Min(1, center+half)}
}

func maxPointer(old *float64, value float64) *float64 {
	if old == nil || value > *old {
		v := value
		return &v
	}
	return old
}

func cloneFloat(value *float64) *float64 {
	if value == nil {
		return nil
	}
	v := *value
	return &v
}

func cloneProgress(values map[string]float64) map[string]float64 {
	if len(values) == 0 {
		return map[string]float64{}
	}
	copyValues := make(map[string]float64, len(values))
	for key, value := range values {
		copyValues[key] = value
	}
	return copyValues
}

type MechanicsCaseResult struct {
	Eligibility  map[string][]string        `json:"eligibility"`
	LaneRankings map[string][]string        `json:"laneRankings"`
	Selections   []MechanicsParentSelection `json:"selections"`
}

type MechanicsFixtureCase struct {
	ID        string                `json:"id"`
	Records   []MechanicsLaneRecord `json:"records"`
	Proposals []MechanicsProposal   `json:"proposals"`
	Expected  json.RawMessage       `json:"expected,omitempty"`
}

type MechanicsFixture struct {
	Schema                int                    `json:"schema"`
	ObjectiveVersion      int                    `json:"objectiveVersion"`
	LaneNames             []string               `json:"laneNames"`
	Pool                  int                    `json:"pool"`
	Cases                 []MechanicsFixtureCase `json:"cases"`
	ActiveLearnerFixtures json.RawMessage        `json:"activeLearnerFixtures"`
}

type MechanicsFixtureCaseResult struct {
	ID     string              `json:"id"`
	Actual MechanicsCaseResult `json:"actual"`
}

type MechanicsFixtureResult struct {
	Schema              int                          `json:"schema"`
	ObjectiveVersion    int                          `json:"objectiveVersion"`
	LaneNames           []string                     `json:"laneNames"`
	Pool                int                          `json:"pool"`
	Cases               []MechanicsFixtureCaseResult `json:"cases"`
	ActiveLearnerActual map[string]any               `json:"activeLearnerActual,omitempty"`
}

// RankMechanicsRecords computes the four exact lexicographic lane orderings
// and eligibility sets for one encounter/difficulty group.
func RankMechanicsRecords(records []MechanicsLaneRecord, pool int) (MechanicsCaseResult, error) {
	if pool < 0 {
		return MechanicsCaseResult{}, errors.New("mechanics lane pool must be nonnegative")
	}
	result := MechanicsCaseResult{
		Eligibility:  make(map[string][]string, len(mechanicsLaneNames)),
		LaneRankings: make(map[string][]string, len(mechanicsLaneNames)),
	}
	eligibleByCandidate := make(map[string]map[string]bool, len(records))
	for _, record := range records {
		if record.Candidate == "" {
			return MechanicsCaseResult{}, errors.New("mechanics record candidate is required")
		}
		if _, exists := result.Eligibility[record.Candidate]; exists {
			return MechanicsCaseResult{}, errors.New("duplicate mechanics candidate")
		}
		eligible := make(map[string]bool, len(mechanicsLaneNames))
		for _, lane := range mechanicsLaneNames {
			eligible[lane] = mechanicsLaneQualifies(record, lane)
		}
		eligibleByCandidate[record.Candidate] = eligible
	}
	for _, lane := range mechanicsLaneNames {
		ids := make([]string, 0, len(records))
		for _, record := range records {
			if eligibleByCandidate[record.Candidate][lane] {
				ids = append(ids, record.Candidate)
			}
		}
		result.Eligibility[lane] = ids
	}
	for _, lane := range mechanicsLaneNames {
		members := make([]MechanicsLaneRecord, 0, len(records))
		for _, record := range records {
			if eligibleByCandidate[record.Candidate][lane] {
				members = append(members, record)
			}
		}
		if lane == "efficiency" {
			members = mechanicsParetoFrontier(members)
		}
		sort.SliceStable(members, func(i, j int) bool {
			return mechanicsCompareLaneKey(members[i], members[j], lane) > 0
		})
		if len(members) > pool {
			members = members[:pool]
		}
		ids := make([]string, len(members))
		for i, member := range members {
			ids[i] = member.Candidate
		}
		result.LaneRankings[lane] = ids
	}
	return result, nil
}

func mechanicsLaneQualifies(record MechanicsLaneRecord, lane string) bool {
	switch lane {
	case "earned", "efficiency":
		return record.EarnedMax != nil && record.EarnedCount > 0
	case "potential":
		earned := mechanicsValue(record.EarnedMax)
		return record.PotentialMax != nil && *record.PotentialMax-earned >= MechanicsPotentialFloor
	case "setup":
		return record.ProgressRuns > 0
	default:
		return false
	}
}

func mechanicsCompareLaneKey(a, b MechanicsLaneRecord, lane string) int {
	ak, bk := mechanicsLaneKey(a, lane), mechanicsLaneKey(b, lane)
	for i := range ak {
		if ak[i] > bk[i] {
			return 1
		}
		if ak[i] < bk[i] {
			return -1
		}
	}
	return 0
}

func mechanicsLaneKey(record MechanicsLaneRecord, lane string) []float64 {
	earned, potential := mechanicsValue(record.EarnedMax), mechanicsValue(record.PotentialMax)
	reliability := 0.0
	if len(record.WinInterval) > 0 {
		reliability = record.WinInterval[0]
	}
	resources := mechanicsValue(record.MeanResources)
	progress := func(name string) float64 { return record.Progress[name] }
	switch lane {
	case "earned":
		return []float64{earned, reliability, -resources, float64(record.N)}
	case "potential":
		return []float64{potential - earned, potential, reliability, -resources, float64(record.N)}
	case "setup":
		return []float64{
			progress("postDeathPrizes"),
			progress("commandsReleasedAfterDeathTargetingBoss"),
			progress("storedCommandsTargetingBossAtDeath"),
			progress("maxSimultaneousCommandsTargetingBoss"),
			progress("commandsTargetingBossReleased"),
			progress("storedTargetHoldersPeak"),
			progress("maxSimultaneousStoredCommands"),
			potential,
			earned,
		}
	case "efficiency":
		return []float64{earned, -resources, reliability, float64(record.N)}
	default:
		return nil
	}
}

func mechanicsParetoFrontier(records []MechanicsLaneRecord) []MechanicsLaneRecord {
	frontier := make([]MechanicsLaneRecord, 0, len(records))
	for i, record := range records {
		earned := mechanicsValue(record.EarnedMax)
		resources := mechanicsValue(record.MeanResources)
		dominated := false
		for j, other := range records {
			if i == j {
				continue
			}
			otherEarned := mechanicsValue(other.EarnedMax)
			otherResources := mechanicsValue(other.MeanResources)
			if otherEarned >= earned && otherResources <= resources &&
				(otherEarned > earned || otherResources < resources) {
				dominated = true
				break
			}
		}
		if !dominated {
			frontier = append(frontier, record)
		}
	}
	return frontier
}

// SelectMechanicsParent reproduces Python choose_parent with explicit draw
// indices so Go and Python can compare decisions without sharing RNG streams.
func SelectMechanicsParent(pools map[string][]string, proposal MechanicsProposal, every int) (MechanicsParentSelection, error) {
	if every <= 0 {
		return MechanicsParentSelection{}, errors.New("exploration interval must be positive")
	}
	if len(proposal.Population) == 0 {
		return MechanicsParentSelection{}, errors.New("mechanics proposal population is empty")
	}
	if proposal.DrawIndex < 0 {
		return MechanicsParentSelection{}, errors.New("mechanics draw index must be nonnegative")
	}
	choose := func(values []string) string { return values[proposal.DrawIndex%len(values)] }
	base := MechanicsParentSelection{
		Ordinal: proposal.Ordinal, DrawIndex: proposal.DrawIndex,
		Population: append([]string(nil), proposal.Population...),
	}
	if proposal.Ordinal%every == every-1 {
		base.ChoicePool = append([]string(nil), proposal.Population...)
		base.Candidate, base.Lane = choose(base.ChoicePool), "exploration"
		return base, nil
	}
	population := make(map[string]struct{}, len(proposal.Population))
	for _, id := range proposal.Population {
		population[id] = struct{}{}
	}
	for offset := range mechanicsLaneNames {
		lane := mechanicsLaneNames[(proposal.Ordinal+offset)%len(mechanicsLaneNames)]
		filtered := make([]string, 0, len(pools[lane]))
		for _, id := range pools[lane] {
			if _, ok := population[id]; ok {
				filtered = append(filtered, id)
			}
		}
		if len(filtered) > 0 {
			base.ChoicePool = filtered
			base.Candidate, base.Lane = choose(filtered), lane
			return base, nil
		}
	}
	base.ChoicePool = append([]string(nil), proposal.Population...)
	base.Candidate, base.Lane = choose(base.ChoicePool), "exploration"
	return base, nil
}

func RunMechanicsFixture(fixture MechanicsFixture) (MechanicsFixtureResult, error) {
	if fixture.ObjectiveVersion != MechanicsObjectiveVersion {
		return MechanicsFixtureResult{}, errors.New("unsupported mechanics objective version")
	}
	if !equalStrings(fixture.LaneNames, mechanicsLaneNames) {
		return MechanicsFixtureResult{}, errors.New("mechanics lane names or order mismatch")
	}
	if fixture.Pool < 0 {
		return MechanicsFixtureResult{}, errors.New("mechanics lane pool must be nonnegative")
	}
	out := MechanicsFixtureResult{
		Schema: fixture.Schema, ObjectiveVersion: MechanicsObjectiveVersion,
		LaneNames: append([]string(nil), mechanicsLaneNames...), Pool: fixture.Pool,
		Cases: make([]MechanicsFixtureCaseResult, 0, len(fixture.Cases)),
	}
	for _, testCase := range fixture.Cases {
		decision, err := RankMechanicsRecords(testCase.Records, fixture.Pool)
		if err != nil {
			return MechanicsFixtureResult{}, err
		}
		pools := make(map[string][]string, len(decision.LaneRankings))
		for lane, ids := range decision.LaneRankings {
			pools[lane] = ids
		}
		for _, proposal := range testCase.Proposals {
			selected, err := SelectMechanicsParent(pools, proposal, MechanicsExploreEvery)
			if err != nil {
				return MechanicsFixtureResult{}, err
			}
			decision.Selections = append(decision.Selections, selected)
		}
		if len(testCase.Expected) != 0 {
			var expected MechanicsCaseResult
			if err := json.Unmarshal(testCase.Expected, &expected); err != nil {
				return MechanicsFixtureResult{}, fmt.Errorf("case %s expected: %w", testCase.ID, err)
			}
			if !jsonSemanticallyEqual(decision, expected) {
				return MechanicsFixtureResult{}, fmt.Errorf("case %s objective parity mismatch", testCase.ID)
			}
		}
		out.Cases = append(out.Cases, MechanicsFixtureCaseResult{ID: testCase.ID, Actual: decision})
	}
	if len(fixture.ActiveLearnerFixtures) != 0 {
		actual, err := runActiveLearnerOracle(fixture.ActiveLearnerFixtures)
		if err != nil {
			return MechanicsFixtureResult{}, err
		}
		out.ActiveLearnerActual = actual
	}
	return out, nil
}

func jsonSemanticallyEqual(a, b any) bool {
	left, errA := json.Marshal(a)
	right, errB := json.Marshal(b)
	if errA != nil || errB != nil {
		return false
	}
	var normalizedLeft, normalizedRight any
	if json.Unmarshal(left, &normalizedLeft) != nil || json.Unmarshal(right, &normalizedRight) != nil {
		return false
	}
	return reflect.DeepEqual(normalizedLeft, normalizedRight)
}

type learnerCounter struct {
	Attempts uint64 `json:"attempts"`
	Improved uint64 `json:"improved"`
}

type activeLearnerOracle struct {
	SourceRotation struct {
		ParentSources []string `json:"parentSources"`
		Expected      []struct {
			ProposalNumber int    `json:"proposalNumber"`
			Attempt        int    `json:"attempt"`
			Source         string `json:"source"`
		} `json:"expected"`
	} `json:"sourceRotation"`
	WeightedParents []struct {
		DrawUnit float64  `json:"drawUnit"`
		Pool     []string `json:"pool"`
		Lineage  map[string]struct {
			Root string `json:"root"`
		} `json:"lineage"`
		Productivity map[string]struct {
			Rate float64 `json:"rate"`
		} `json:"productivity"`
		ExpectedWeights     map[string]float64 `json:"weights"`
		ExpectedCandidate   string             `json:"expectedCandidate"`
		ExpectedRandomCalls []float64          `json:"expectedRandomCalls"`
	} `json:"weightedParents"`
	ImprovedLanes []struct {
		ID                  string              `json:"id"`
		Parent              MechanicsLaneRecord `json:"parent"`
		Child               MechanicsLaneRecord `json:"child"`
		ExpectedBetterLanes []string            `json:"expectedBetterLanes"`
	} `json:"improvedLanes"`
	OperatorWeights []struct {
		DrawUnit            float64                   `json:"drawUnit"`
		Stats               map[string]learnerCounter `json:"stats"`
		Operations          []string                  `json:"operations"`
		ExpectedWeights     map[string]float64        `json:"expectedWeights"`
		ExpectedOperator    string                    `json:"expectedOperator"`
		ExpectedRandomCalls []float64                 `json:"expectedRandomCalls"`
	} `json:"operatorWeights"`
	ScaleWeights struct {
		Stats    map[string]learnerCounter `json:"stats"`
		Scales   []float64                 `json:"scales"`
		Expected map[string]float64        `json:"expected"`
	} `json:"scaleWeights"`
	StatTargets []struct {
		ID             string                    `json:"id"`
		Current        int64                     `json:"current"`
		Minimum        int64                     `json:"minimum"`
		Maximum        int64                     `json:"maximum"`
		Anchor         *int64                    `json:"anchor"`
		Stats          map[string]learnerCounter `json:"stats"`
		Scales         []float64                 `json:"scales"`
		ExpectedScale  string                    `json:"expectedScale"`
		ExpectedTarget int64                     `json:"expectedTarget"`
		RandomDraws    []float64                 `json:"randomDraws"`
		RandintCalls   []struct {
			Low   int64 `json:"low"`
			High  int64 `json:"high"`
			Value int64 `json:"value"`
		} `json:"randintCalls"`
	} `json:"statTargets"`
	Region struct {
		Scenario json.RawMessage `json:"scenario"`
		Expected string          `json:"expected"`
	} `json:"region"`
}

func runActiveLearnerOracle(raw json.RawMessage) (map[string]any, error) {
	var fixture activeLearnerOracle
	if err := json.Unmarshal(raw, &fixture); err != nil {
		return nil, err
	}
	actual := map[string]any{}
	sources := fixture.SourceRotation.ParentSources
	rotation := make([]map[string]any, 0, len(fixture.SourceRotation.Expected))
	for _, expected := range fixture.SourceRotation.Expected {
		if len(sources) == 0 || expected.Attempt < 1 {
			return nil, errors.New("invalid source rotation fixture")
		}
		index := (expected.ProposalNumber + expected.Attempt) % len(sources)
		if sources[index] != expected.Source {
			return nil, errors.New("source rotation oracle mismatch")
		}
		rotation = append(rotation, map[string]any{"proposalNumber": expected.ProposalNumber, "attempt": expected.Attempt, "source": sources[index]})
	}
	actual["sourceRotation"] = rotation

	weighted := make([]map[string]any, 0, len(fixture.WeightedParents))
	for _, item := range fixture.WeightedParents {
		weights := make(map[string]float64, len(item.Pool))
		for _, candidate := range item.Pool {
			root := item.Lineage[candidate].Root
			rate, ok := item.Productivity[root]
			if !ok {
				rate.Rate = .5
			}
			weight := .5 + rate.Rate
			weights[candidate] = weight
		}
		selected := weightedStringChoice(item.Pool, weights, item.DrawUnit)
		if !jsonSemanticallyEqual(weights, item.ExpectedWeights) || selected != item.ExpectedCandidate || !equalFloatSlices([]float64{item.DrawUnit}, item.ExpectedRandomCalls) {
			return nil, fmt.Errorf("weighted parent oracle mismatch: got %v/%s expected %v/%s", weights, selected, item.ExpectedWeights, item.ExpectedCandidate)
		}
		weighted = append(weighted, map[string]any{"weights": weights, "candidate": selected})
	}
	actual["weightedParents"] = weighted

	better := make([]map[string]any, 0, len(fixture.ImprovedLanes))
	for _, item := range fixture.ImprovedLanes {
		lanes := mechanicsImprovedLanes(item.Parent, item.Child)
		if !equalStrings(lanes, item.ExpectedBetterLanes) {
			return nil, fmt.Errorf("improved-lanes oracle mismatch for %s", item.ID)
		}
		better = append(better, map[string]any{"id": item.ID, "betterLanes": lanes})
	}
	actual["improvedLanes"] = better

	operatorResults := make([]map[string]any, 0, len(fixture.OperatorWeights))
	for _, item := range fixture.OperatorWeights {
		weights := learnerOperatorWeights(item.Stats, item.Operations)
		selected := weightedStringChoice(item.Operations, weights, item.DrawUnit)
		if !jsonSemanticallyEqual(weights, item.ExpectedWeights) || selected != item.ExpectedOperator || !equalFloatSlices([]float64{item.DrawUnit}, item.ExpectedRandomCalls) {
			return nil, errors.New("operator selection oracle mismatch")
		}
		operatorResults = append(operatorResults, map[string]any{"weights": weights, "operator": selected})
	}
	actual["operatorWeights"] = operatorResults

	actual["scaleWeights"] = learnerScaleWeightsFromCounters(fixture.ScaleWeights.Stats, fixture.ScaleWeights.Scales)
	if !jsonSemanticallyEqual(actual["scaleWeights"], fixture.ScaleWeights.Expected) {
		return nil, errors.New("scale weights oracle mismatch")
	}
	statTargets := make([]map[string]any, 0, len(fixture.StatTargets))
	for _, item := range fixture.StatTargets {
		weights := learnerScaleWeightsFromCounters(item.Stats, item.Scales)
		draws := append([]float64(nil), item.RandomDraws...)
		drawIndex, randomCalls := 0, []float64{}
		randomFloat := func() float64 {
			if drawIndex >= len(draws) {
				return 0
			}
			value := draws[drawIndex]
			drawIndex++
			randomCalls = append(randomCalls, value)
			return value
		}
		randints := item.RandintCalls
		randintIndex := 0
		actualRandints := make([]struct {
			Low   int64 `json:"low"`
			High  int64 `json:"high"`
			Value int64 `json:"value"`
		}, 0)
		randomInt := func(low, high int64) int64 {
			if randintIndex >= len(randints) {
				return low
			}
			call := randints[randintIndex]
			randintIndex++
			actualRandints = append(actualRandints, struct {
				Low   int64 `json:"low"`
				High  int64 `json:"high"`
				Value int64 `json:"value"`
			}{Low: low, High: high, Value: call.Value})
			if call.Low != low || call.High != high {
				return low
			}
			return call.Value
		}
		scaleKeys := make([]string, 0, len(weights))
		for _, scale := range item.Scales {
			scaleKeys = append(scaleKeys, learnerScaleKey(scale))
		}
		scaleKeys = append(scaleKeys, "jump")
		var totalScaleWeight float64
		for _, key := range scaleKeys {
			totalScaleWeight += weights[key]
		}
		scaleIndex := weightedKeyChoice(scaleKeys, weights, randomFloat()*totalScaleWeight)
		scaleKey := scaleKeys[scaleIndex]
		var scale float64
		if scaleKey != "jump" {
			scale, _ = strconv.ParseFloat(scaleKey, 64)
		}
		base := item.Current
		if item.Anchor != nil {
			base = *item.Anchor
		}
		target := learnerStatTarget(randomFloat, randomInt, base, item.Minimum, item.Maximum, scale)
		statTargets = append(statTargets, map[string]any{"id": item.ID, "scale": scaleKey, "target": target, "randomCalls": randomCalls})
		if scaleKey != item.ExpectedScale || target != item.ExpectedTarget || !equalFloatSlices(randomCalls, item.RandomDraws) || !jsonSemanticallyEqual(actualRandints, randints) || randintIndex != len(randints) {
			return nil, fmt.Errorf("stat target oracle mismatch for %s", item.ID)
		}
	}
	actual["statTargets"] = statTargets
	region, err := strategyRegion(fixture.Region.Scenario)
	if err != nil {
		return nil, err
	}
	if region != fixture.Region.Expected {
		return nil, errors.New("strategy region oracle mismatch")
	}
	actual["region"] = region
	return actual, nil
}

func mechanicsImprovedLanes(parent, child MechanicsLaneRecord) []string {
	better := make([]string, 0, len(mechanicsLaneNames))
	for _, lane := range mechanicsLaneNames {
		if !mechanicsLaneQualifies(child, lane) {
			continue
		}
		if !mechanicsLaneQualifies(parent, lane) || mechanicsCompareLaneKey(child, parent, lane) > 0 {
			better = append(better, lane)
		}
	}
	return better
}

func learnerOperatorWeights(stats map[string]learnerCounter, operations []string) map[string]float64 {
	weights := make(map[string]float64, len(operations))
	for _, operation := range operations {
		entry := stats[operation]
		attempts, improved := float64(entry.Attempts), float64(entry.Improved)
		weight := math.Max(learnerOperatorFloor, 1+2*improved*((improved+1)/(attempts+4)))
		weights[operation] = weight
	}
	return weights
}

func learnerScaleWeightsFromCounters(stats map[string]learnerCounter, scales []float64) map[string]float64 {
	weights := make(map[string]float64, len(scales)+1)
	var improvedScales float64
	for _, scale := range scales {
		key := learnerScaleKey(scale)
		entry := stats[key]
		attempts, improved := float64(entry.Attempts), float64(entry.Improved)
		weights[key] = math.Max(learnerOperatorFloor, 1+2*improved-.15*math.Max(0, attempts-improved))
		if entry.Improved > 0 {
			improvedScales++
		}
	}
	if stats["jump"].Improved > 0 {
		improvedScales++
	}
	weights["jump"] = 1 + improvedScales
	return weights
}

func weightedStringChoice(keys []string, weights map[string]float64, draw float64) string {
	var total float64
	for _, key := range keys {
		total += weights[key]
	}
	return keys[weightedKeyChoice(keys, weights, draw*total)]
}

func weightedKeyChoice(keys []string, weights map[string]float64, target float64) int {
	var upto float64
	for i, key := range keys {
		upto += weights[key]
		if target <= upto {
			return i
		}
	}
	return len(keys) - 1
}

func learnerScaleKey(scale float64) string {
	if scale == math.Trunc(scale) {
		return strconv.FormatFloat(scale, 'f', 1, 64)
	}
	return strconv.FormatFloat(scale, 'f', -1, 64)
}

func equalStrings(a, b []string) bool {
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

func equalFloatSlices(a, b []float64) bool {
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

func mechanicsValue(value *float64) float64 {
	if value == nil || math.IsNaN(*value) || math.IsInf(*value, 0) {
		return 0
	}
	return *value
}
