package main

// Deterministic adaptive search over raw scenarios. RNG state and feedback
// queues are checkpointable; results are applied in dispatch order regardless
// of worker completion order.
import (
	"bytes"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"
	"sync"
)

const SearchSchema = "ka-go-search-state-6"

// The exact derived synthetic stat walls used by search_contract.py. Keeping
// this source beside the binary makes the search space independent of host
// paths and lets checkpoint identity detect a changed bounds table.
//
//go:embed native_search_bounds.json
var nativeSearchBoundsJSON []byte

type nativeSearchStatBound struct {
	Minimum int64
	Maximum int64
	Bounded bool
}

type nativeSearchBoundsDocument struct {
	Schema             string `json:"schema"`
	SearchSpaceVersion int    `json:"searchSpaceVersion"`
	Verification       struct {
		Verified bool `json:"verified"`
	} `json:"verification"`
	Stats []struct {
		Parameter int64 `json:"parameter"`
		Minimum   int64 `json:"minimum"`
		Maximum   int64 `json:"maximum"`
		Bounded   bool  `json:"bounded"`
	} `json:"stats"`
}

var nativeSearchBoundsOnce sync.Once
var nativeSearchBoundsCache map[int64]nativeSearchStatBound
var nativeSearchBoundsErr error

func loadNativeSearchStatBounds() (map[int64]nativeSearchStatBound, error) {
	nativeSearchBoundsOnce.Do(func() {
		var doc nativeSearchBoundsDocument
		if err := json.Unmarshal(nativeSearchBoundsJSON, &doc); err != nil {
			nativeSearchBoundsErr = fmt.Errorf("decode embedded search stat bounds: %w", err)
			return
		}
		if doc.Schema != "ka-search-stat-bounds-2" || doc.SearchSpaceVersion != 3 || !doc.Verification.Verified {
			nativeSearchBoundsErr = errors.New("embedded synthetic stat bounds are not verified search-contract v3 data")
			return
		}
		bounds := make(map[int64]nativeSearchStatBound, len(doc.Stats))
		for _, row := range doc.Stats {
			if row.Parameter <= 0 || row.Minimum <= 0 || row.Maximum < row.Minimum {
				nativeSearchBoundsErr = fmt.Errorf("invalid embedded stat interval for parameter %d", row.Parameter)
				return
			}
			if _, exists := bounds[row.Parameter]; exists {
				nativeSearchBoundsErr = fmt.Errorf("duplicate embedded stat interval for parameter %d", row.Parameter)
				return
			}
			bounds[row.Parameter] = nativeSearchStatBound{Minimum: row.Minimum, Maximum: row.Maximum, Bounded: row.Bounded}
		}
		expected := map[int64]bool{10: true, 11: true, 13: false, 14: false, 15: false, 16: false, 18: false, 19: false}
		if len(bounds) != len(expected) {
			nativeSearchBoundsErr = errors.New("embedded synthetic stat bounds do not cover exactly the eight search parameters")
			return
		}
		for parameter, bounded := range expected {
			entry, ok := bounds[parameter]
			if !ok || entry.Bounded != bounded {
				nativeSearchBoundsErr = fmt.Errorf("embedded stat bound for parameter %d is missing or has the wrong bounded-stat flag", parameter)
				return
			}
		}
		nativeSearchBoundsCache = bounds
	})
	if nativeSearchBoundsErr != nil {
		return nil, nativeSearchBoundsErr
	}
	return nativeSearchBoundsCache, nil
}

// NativeSearchStatBounds returns an independent copy of the canonical
// parameter intervals, suitable for host-side proposal planning.
func NativeSearchStatBounds() (map[int64][2]int64, error) {
	bounds, err := loadNativeSearchStatBounds()
	if err != nil {
		return nil, err
	}
	out := make(map[int64][2]int64, len(bounds))
	for parameter, bound := range bounds {
		out[parameter] = [2]int64{bound.Minimum, bound.Maximum}
	}
	return out, nil
}

// ValidateNativeSearchStatTarget applies the same reviewed walls as the
// Python search contract. The domain is the runner's effective synthetic
// value; for HP/MP this is the prepared maximum.
func ValidateNativeSearchStatTarget(parameter, target int64) error {
	bounds, err := loadNativeSearchStatBounds()
	if err != nil {
		return err
	}
	bound, ok := bounds[parameter]
	if !ok {
		return fmt.Errorf("parameter %d is not a bounded search-contract stat axis", parameter)
	}
	if target < bound.Minimum || target > bound.Maximum {
		return fmt.Errorf("parameter %d target %d is outside the derived interval [%d, %d]", parameter, target, bound.Minimum, bound.Maximum)
	}
	return nil
}

type SearchConfig struct {
	Seed            uint64  `json:"seed"`
	Budget          uint64  `json:"budget"`
	Exploration     float64 `json:"exploration"`
	PopulationLimit int     `json:"populationLimit,omitempty"`
	// FixedFormation separately enforces the published fixed roster/equipment profile.
	FixedFormation     bool   `json:"fixedFormation,omitempty"`
	ObjectiveMode      string `json:"objectiveMode,omitempty"`
	ObjectiveVersion   int    `json:"objectiveVersion,omitempty"`
	LearnerMode        string `json:"learnerMode,omitempty"`
	ConstraintProfile  string `json:"constraintProfile,omitempty"`
	LearnerPriorSHA256 string `json:"learnerPriorSha256,omitempty"`
}

type SearchCandidate struct {
	ID                 string                      `json:"id"`
	EncounterID        int64                       `json:"encounterId"`
	DefeatCount        int64                       `json:"defeatCount"`
	Raw                json.RawMessage             `json:"raw"`
	Count              uint64                      `json:"count"`
	Mean               float64                     `json:"mean"`
	Best               float64                     `json:"best"`
	HasScore           bool                        `json:"hasScore"`
	Seed               bool                        `json:"seed"`
	SeedDispatched     bool                        `json:"seedDispatched"`
	Unusable           bool                        `json:"unusable"`
	ParentID           string                      `json:"parentId"`
	RootID             string                      `json:"rootId,omitempty"`
	ImprovedLanes      []string                    `json:"improvedLanes,omitempty"`
	ImprovementCounted bool                        `json:"improvementCounted,omitempty"`
	Generation         uint64                      `json:"generation"`
	Order              uint64                      `json:"order,omitempty"`
	Mechanics          *MechanicsCandidateEvidence `json:"mechanics,omitempty"`
}

type SearchLineage struct {
	CandidateID       string `json:"candidateId"`
	ParentID          string `json:"parentId"`
	Generation        uint64 `json:"generation"`
	Origin            string `json:"origin"`
	Mutation          string `json:"mutation,omitempty"`
	MutationParameter int64  `json:"mutationParameter,omitempty"`
	MutationScale     string `json:"mutationScale,omitempty"`
	MutationTarget    int64  `json:"mutationTarget,omitempty"`
	ParentSource      string `json:"parentSource,omitempty"`
	DeferredFrom      string `json:"deferredFrom,omitempty"`
}

type queuedFeedback struct {
	Identity  string                `json:"identity"`
	Earned    float64               `json:"earned"`
	Accepted  bool                  `json:"accepted"`
	Scored    bool                  `json:"scored"`
	Rejected  bool                  `json:"rejected"`
	Mechanics *MechanicsObservation `json:"mechanics,omitempty"`
}

// deferredEvaluation preserves an exact planned intent and its lineage when
// admission control withholds a native dispatch. Next re-reserves this raw
// scenario without drawing new seeds or mutating its strategy.
type deferredEvaluation struct {
	OriginalID string          `json:"originalId"`
	Raw        json.RawMessage `json:"raw"`
	Lineage    SearchLineage   `json:"lineage"`
}

type MutationStat struct {
	Count uint64  `json:"count"`
	Mean  float64 `json:"mean"`
	Best  float64 `json:"best"`
}

type searchSnapshot struct {
	Schema               string                                          `json:"schema"`
	PolicyHash           string                                          `json:"policyHash"`
	CatalogSHA256        string                                          `json:"catalogSha256,omitempty"`
	Config               SearchConfig                                    `json:"config"`
	RNGState             uint64                                          `json:"rngState"`
	NextSequence         uint64                                          `json:"nextSequence"`
	BranchingProposals   uint64                                          `json:"branchingProposals,omitempty"`
	NextCandidateOrder   uint64                                          `json:"nextCandidateOrder,omitempty"`
	NextFeedback         uint64                                          `json:"nextFeedback"`
	Candidates           map[string]*SearchCandidate                     `json:"candidates"`
	InFlight             map[string]uint64                               `json:"inFlight"`
	EvaluationStrategy   map[string]string                               `json:"evaluationStrategy"`
	EvaluationLineage    map[string]SearchLineage                        `json:"evaluationLineage"`
	Pending              map[uint64]queuedFeedback                       `json:"pending"`
	MutationStats        map[int64]map[string]MutationStat               `json:"mutationStats"`
	ConstraintReferences map[int64]string                                `json:"constraintReferences,omitempty"`
	LearnerPriorSHA256   string                                          `json:"learnerPriorSha256,omitempty"`
	LearnerPriorLoaded   bool                                            `json:"learnerPriorLoaded,omitempty"`
	RootChildren         map[string]uint64                               `json:"rootChildren,omitempty"`
	RootImprovedChildren map[string]uint64                               `json:"rootImprovedChildren,omitempty"`
	LearnerOperators     map[int64]map[string]LearnerOperatorStat        `json:"learnerOperators,omitempty"`
	LearnerScales        map[int64]map[int64]map[string]LearnerScaleStat `json:"learnerScales,omitempty"`
	StatAnchors          map[int64]map[int64]int64                       `json:"statAnchors,omitempty"`
	Deferred             []deferredEvaluation                            `json:"deferred,omitempty"`
	Rejected             uint64                                          `json:"rejected"`
}

type Search struct {
	state            searchSnapshot
	preparer         *Preparer
	focusEncounters  map[int64]struct{} // ephemeral dispatch filter; never serialized into learned state
	byEncounter      map[int64]map[string]*SearchCandidate
	encounterIDs     []int64
	lastParentSource string
}

func (s *Search) rebuildIndexes() {
	s.byEncounter = make(map[int64]map[string]*SearchCandidate)
	s.encounterIDs = s.encounterIDs[:0]
	for _, candidate := range s.state.Candidates {
		s.indexCandidate(candidate)
	}
	sort.Slice(s.encounterIDs, func(i, j int) bool { return s.encounterIDs[i] < s.encounterIDs[j] })
}

func (s *Search) indexCandidate(candidate *SearchCandidate) {
	if s.byEncounter == nil {
		s.byEncounter = make(map[int64]map[string]*SearchCandidate)
	}
	pool := s.byEncounter[candidate.EncounterID]
	if pool == nil {
		pool = make(map[string]*SearchCandidate)
		s.byEncounter[candidate.EncounterID] = pool
		i := sort.Search(len(s.encounterIDs), func(i int) bool { return s.encounterIDs[i] >= candidate.EncounterID })
		if i == len(s.encounterIDs) || s.encounterIDs[i] != candidate.EncounterID {
			s.encounterIDs = append(s.encounterIDs, 0)
			copy(s.encounterIDs[i+1:], s.encounterIDs[i:])
			s.encounterIDs[i] = candidate.EncounterID
		}
	}
	pool[candidate.ID] = candidate
}

func (s *Search) unindexCandidate(candidate *SearchCandidate) {
	if candidate == nil {
		return
	}
	pool := s.byEncounter[candidate.EncounterID]
	delete(pool, candidate.ID)
	if len(pool) != 0 {
		return
	}
	delete(s.byEncounter, candidate.EncounterID)
	i := sort.Search(len(s.encounterIDs), func(i int) bool { return s.encounterIDs[i] >= candidate.EncounterID })
	if i < len(s.encounterIDs) && s.encounterIDs[i] == candidate.EncounterID {
		s.encounterIDs = append(s.encounterIDs[:i], s.encounterIDs[i+1:]...)
	}
}

// NewSearch retains every admitted starting candidate exactly as supplied.
// Raw mutation is validated by AdmitRawScenario before it is returned.
func NewSearch(candidates []json.RawMessage, config SearchConfig) (*Search, error) {
	if _, err := loadNativeSearchStatBounds(); err != nil {
		return nil, err
	}
	if len(candidates) == 0 {
		return nil, errors.New("at least one starting scenario is required")
	}
	config = normalizeSearchConfig(config)
	if (config.ObjectiveMode != ObjectiveModeEarnedOnly || config.ObjectiveVersion != 1) &&
		(config.ObjectiveMode != ObjectiveModeMechanicLanes || config.ObjectiveVersion != MechanicsObjectiveVersion) {
		return nil, errors.New("unsupported search objective mode/version")
	}
	if config.LearnerMode != LearnerModeLegacy && config.LearnerMode != LearnerModeBranching {
		return nil, errors.New("unsupported learner mode")
	}
	if config.LearnerMode == LearnerModeBranching && (config.ObjectiveMode != ObjectiveModeMechanicLanes || config.ConstraintProfile != ConstraintProfileFixedDPS) {
		return nil, errors.New("branching learner requires mechanism-lanes-v3 and fixed-dps-3stats-v1")
	}
	if config.ConstraintProfile == ConstraintProfileFixedDPS && config.LearnerMode != LearnerModeBranching {
		return nil, errors.New("fixed-dps-3stats-v1 requires the controlled branching learner")
	}
	if config.LearnerPriorSHA256 != "" && config.LearnerMode != LearnerModeBranching {
		return nil, errors.New("mechanism learner prior requires branching mode")
	}
	if config.ConstraintProfile != "" && config.ConstraintProfile != ConstraintProfileFixedDPS {
		return nil, errors.New("unsupported constraint profile")
	}
	if config.Seed > math.MaxInt32 {
		return nil, errors.New("search seed must be nonnegative signed31-bit")
	}
	if config.PopulationLimit < 16 || config.PopulationLimit > 100000 {
		return nil, errors.New("population limit must be in 16..100000")
	}
	if math.IsNaN(config.Exploration) || math.IsInf(config.Exploration, 0) || config.Exploration < 0 {
		return nil, errors.New("invalid exploration coefficient")
	}
	s := &Search{state: searchSnapshot{Schema: SearchSchema, Config: config, LearnerPriorSHA256: config.LearnerPriorSHA256, RNGState: config.Seed, Candidates: map[string]*SearchCandidate{}, InFlight: map[string]uint64{}, EvaluationStrategy: map[string]string{}, EvaluationLineage: map[string]SearchLineage{}, Pending: map[uint64]queuedFeedback{}, MutationStats: map[int64]map[string]MutationStat{}, ConstraintReferences: map[int64]string{}, RootChildren: map[string]uint64{}, RootImprovedChildren: map[string]uint64{}, LearnerOperators: map[int64]map[string]LearnerOperatorStat{}, LearnerScales: map[int64]map[int64]map[string]LearnerScaleStat{}, StatAnchors: map[int64]map[int64]int64{}}}
	s.state.PolicyHash = searchPolicyHash("", config.FixedFormation)
	for _, raw := range candidates {
		admitted, err := AdmitRawScenario(raw)
		if err != nil {
			return nil, fmt.Errorf("starting candidate admission: %w", err)
		}
		if config.FixedFormation {
			if err := ValidateFixedFormation(admitted.Raw); err != nil {
				return nil, fmt.Errorf("starting candidate violates the fixed formation: %w", err)
			}
		}
		b := append(json.RawMessage(nil), admitted.Raw...)
		if config.ConstraintProfile == ConstraintProfileFixedDPS {
			signature, validateErr := validateControlledScenario(b)
			if validateErr != nil {
				return nil, fmt.Errorf("starting candidate fixed profile: %w", validateErr)
			}
			if expected, exists := s.state.ConstraintReferences[admitted.EncounterID]; exists && expected != signature {
				return nil, fmt.Errorf("starting candidates for encounter %d violate fixed-profile invariants", admitted.EncounterID)
			}
			s.state.ConstraintReferences[admitted.EncounterID] = signature
		}
		id := strategyIdentity(b)
		if _, ok := s.state.Candidates[id]; ok {
			continue
		}
		s.state.Candidates[id] = &SearchCandidate{ID: id, EncounterID: admitted.EncounterID, DefeatCount: admitted.DefeatCount, Raw: b, Seed: true, RootID: id, Order: s.state.NextCandidateOrder}
		s.state.NextCandidateOrder++
		if config.ObjectiveMode == ObjectiveModeMechanicLanes {
			s.state.RootChildren[id]++ // Python productivity counts every lineage row, including its root.
		}
	}
	if len(s.state.Candidates) == 0 {
		return nil, errors.New("no unique starting candidates")
	}
	s.rebuildIndexes()
	return s, nil
}

func normalizeSearchConfig(config SearchConfig) SearchConfig {
	if config.Exploration == 0 {
		config.Exploration = 0.7
	}
	if config.PopulationLimit == 0 {
		config.PopulationLimit = 1024
	}
	if config.Seed == 0 {
		config.Seed = 0x13579bdf
	}
	if config.ObjectiveMode == "" {
		config.ObjectiveMode = ObjectiveModeEarnedOnly
	}
	if config.ObjectiveMode == ObjectiveModeEarnedOnly && config.ObjectiveVersion == 0 {
		config.ObjectiveVersion = 1
	}
	if config.ObjectiveMode == ObjectiveModeMechanicLanes && config.ObjectiveVersion == 0 {
		config.ObjectiveVersion = MechanicsObjectiveVersion
	}
	if config.LearnerMode == "" {
		config.LearnerMode = LearnerModeLegacy
	}
	return config
}

// NewSearchWithTables enables catalog-backed mutations over the same explicit
// recovered tables used by preparation. The catalog digest is checkpointed so
// a later bounded host wave cannot silently change the mutation space.
func NewSearchWithTables(candidates []json.RawMessage, config SearchConfig, tables json.RawMessage) (*Search, error) {
	p, err := NewPreparer(tables)
	if err != nil {
		return nil, fmt.Errorf("search mutation catalogs: %w", err)
	}
	s, err := NewSearch(candidates, config)
	if err != nil {
		return nil, err
	}
	s.preparer = p
	s.state.CatalogSHA256 = digest(canonicalJSON(tables))
	s.state.PolicyHash = searchPolicyHash(s.state.CatalogSHA256, s.state.Config.FixedFormation)
	return s, nil
}

func searchPolicyHash(catalogHash string, fixedFormation bool) string {
	mutation := "search-contract-v5 skill, trigger, wall-bounded synthetic stat, weapon, formation, roster and team mutations; bounded adaptive population"
	fixed := ""
	if fixedFormation {
		mutation = "fixed-formation: pinned six-unit roster, Synthetic DPS raw stats only, no equipment"
		fixed = FixedFormationPolicyHash()
		policy, _ := json.Marshal(struct {
			Schema      string
			Mutation    string
			Selection   string
			SeedPolicy  string
			Catalog     string
			StatBounds  string
			FixedPolicy string
		}{SearchSchema, mutation, "sorted encounter round-robin; mean + UCB within encounter; ordered feedback", "splitmix64 nonnegative31bit pair", catalogHash, digest(canonicalJSON(nativeSearchBoundsJSON)), fixed})
		return digest(policy)
	}
	policy, _ := json.Marshal(struct {
		Schema     string
		Mutation   string
		Selection  string
		SeedPolicy string
		Catalog    string
		StatBounds string
	}{SearchSchema, mutation, "sorted encounter round-robin; mean + UCB within encounter; ordered feedback", "splitmix64 nonnegative31bit pair", catalogHash, digest(canonicalJSON(nativeSearchBoundsJSON))})
	return digest(policy)
}

func (s *Search) PolicyHash() string {
	if s == nil {
		return ""
	}
	return s.state.PolicyHash
}

// RequireConfig binds a checkpoint imported into another bounded host wave to
// the same search seed, exploration setting, and budget policy.
func (s *Search) RequireConfig(config SearchConfig) error {
	if s == nil {
		return errors.New("nil search")
	}
	config = normalizeSearchConfig(config)
	stateConfig := normalizeSearchConfig(s.state.Config)
	if config != stateConfig {
		return errors.New("search seed, exploration, budget, or objective policy mismatch")
	}
	return nil
}

// MergeSeeds admits newly supplied parents into a drained search wave without
// discarding the measured population. It may extend the state with new encounter
// pools; each new seed starts without fabricated score or ancestry. A candidate
// already present under strategyIdentity is left untouched, so a changed
// math/lib seed pair cannot reset its measurements or lineage.
func (s *Search) MergeSeeds(candidates []json.RawMessage) error {
	if s == nil {
		return errors.New("nil search")
	}
	if s.state.NextFeedback != s.state.NextSequence || len(s.state.InFlight) != 0 || len(s.state.Pending) != 0 || len(s.state.EvaluationStrategy) != 0 || len(s.state.EvaluationLineage) != 0 {
		return errors.New("seed merge requires a drained search state")
	}
	if len(candidates) == 0 {
		return errors.New("seed merge requires at least one candidate")
	}
	type stagedSeed struct {
		id          string
		encounter   int64
		defeatCount int64
		raw         json.RawMessage
	}
	staged := make([]stagedSeed, 0, len(candidates))
	stagedByID := make(map[string]json.RawMessage, len(candidates))
	for i, raw := range candidates {
		admitted, err := AdmitRawScenario(raw)
		if err != nil {
			return fmt.Errorf("seed merge candidate %d admission: %w", i, err)
		}
		if s.state.Config.FixedFormation {
			if err := ValidateFixedFormation(admitted.Raw); err != nil {
				return fmt.Errorf("seed merge candidate %d violates the fixed formation: %w", i, err)
			}
		}
		if s.preparer != nil {
			if _, err := s.preparer.Prepare(admitted.Raw); err != nil {
				return fmt.Errorf("seed merge candidate %d canonical preparation: %w", i, err)
			}
		}
		id := strategyIdentity(admitted.Raw)
		if old := s.state.Candidates[id]; old != nil {
			if !bytes.Equal(strategyBytes(old.Raw), strategyBytes(admitted.Raw)) {
				return errors.New("strategy identity collision during seed merge")
			}
			continue
		}
		if old, ok := stagedByID[id]; ok {
			if !bytes.Equal(strategyBytes(old), strategyBytes(admitted.Raw)) {
				return errors.New("strategy identity collision within seed merge")
			}
			continue
		}
		copyRaw := append(json.RawMessage(nil), admitted.Raw...)
		stagedByID[id] = copyRaw
		staged = append(staged, stagedSeed{id: id, encounter: admitted.EncounterID, defeatCount: admitted.DefeatCount, raw: copyRaw})
	}
	for _, seed := range staged {
		candidate := &SearchCandidate{
			ID: seed.id, EncounterID: seed.encounter, DefeatCount: seed.defeatCount,
			Raw: append(json.RawMessage(nil), seed.raw...), Seed: true, RootID: seed.id, Order: s.state.NextCandidateOrder,
		}
		s.state.NextCandidateOrder++
		s.state.Candidates[seed.id] = candidate
		s.indexCandidate(candidate)
		if s.state.Config.ObjectiveMode == ObjectiveModeMechanicLanes {
			s.state.RootChildren[seed.id]++
		}
	}
	pruned := make(map[int64]bool)
	for _, seed := range staged {
		if !pruned[seed.encounter] {
			s.pruneEncounter(seed.encounter)
			pruned[seed.encounter] = true
		}
	}
	return nil
}

// SetFocus installs a transient dispatch filter. An empty list means all
// encounter pools already represented in the search state. Focus is runtime
// workload selection, not part of the learned snapshot; callers restore the
// desired filter from the current workload after importing a checkpoint.
func (s *Search) SetFocus(encounters []int64) error {
	if s == nil {
		return errors.New("nil search")
	}
	if len(encounters) == 0 {
		s.focusEncounters = nil
		return nil
	}
	available := make(map[int64]bool, len(s.encounterIDs))
	for _, encounterID := range s.encounterIDs {
		available[encounterID] = true
	}
	focus := make(map[int64]struct{}, len(encounters))
	for _, encounterID := range encounters {
		if encounterID < 0 || encounterID > 19 {
			return fmt.Errorf("focus encounter %d is outside the supported range 0..19", encounterID)
		}
		if !available[encounterID] {
			return fmt.Errorf("focus encounter %d has no candidate after seed merge", encounterID)
		}
		if _, duplicate := focus[encounterID]; duplicate {
			return fmt.Errorf("duplicate focus encounter %d", encounterID)
		}
		focus[encounterID] = struct{}{}
	}
	s.focusEncounters = focus
	return nil
}

func (s *Search) encounterInFocus(encounterID int64) bool {
	if len(s.focusEncounters) == 0 {
		return true
	}
	_, ok := s.focusEncounters[encounterID]
	return ok
}

// HasDeferredInFocus reports whether Next can replay an exact saved intent
// without spending another fresh-evaluation budget slot.
func (s *Search) HasDeferredInFocus() bool {
	if s == nil {
		return false
	}
	for _, item := range s.state.Deferred {
		admitted, err := AdmitRawScenario(item.Raw)
		if err == nil && s.encounterInFocus(admitted.EncounterID) {
			return true
		}
	}
	return false
}

// Lineage retains the actual parent selected for this dispatch, even when a
// mutation returns an already-known strategy. It is saved before feedback.
func (s *Search) Lineage(id string) SearchLineage {
	if s == nil {
		return SearchLineage{}
	}
	return s.state.EvaluationLineage[id]
}

// Leaderboard returns detached strategy summaries in stable best-mean order.
func (s *Search) Leaderboard() []SearchCandidate {
	if s == nil {
		return nil
	}
	out := make([]SearchCandidate, 0, len(s.state.Candidates))
	for _, c := range s.state.Candidates {
		copy := *c
		copy.Raw = append(json.RawMessage(nil), c.Raw...)
		out = append(out, copy)
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].HasScore != out[j].HasScore {
			return out[i].HasScore
		}
		if out[i].Mean != out[j].Mean {
			return out[i].Mean > out[j].Mean
		}
		return out[i].ID < out[j].ID
	})
	return out
}

func (s *Search) Progress() (proposed, budget, feedback, rejected uint64) {
	if s == nil {
		return
	}
	return s.state.NextSequence, s.state.Config.Budget, s.state.NextFeedback, s.state.Rejected
}

func RestoreSearch(raw json.RawMessage) (*Search, error) {
	return restoreSearch(raw, false)
}

// RestoreSearchWithTables resumes a catalog-backed search only when its
// mutation catalogs are identical to those used by the saved wave.
func RestoreSearchWithTables(raw, tables json.RawMessage) (*Search, error) {
	p, err := NewPreparer(tables)
	if err != nil {
		return nil, fmt.Errorf("search mutation catalogs: %w", err)
	}
	s, err := restoreSearch(raw, true)
	if err != nil {
		return nil, err
	}
	catalogHash := digest(canonicalJSON(tables))
	if s.state.CatalogSHA256 == "" || s.state.CatalogSHA256 != catalogHash {
		return nil, errors.New("search mutation catalog identity mismatch")
	}
	if s.state.PolicyHash != searchPolicyHash(catalogHash, s.state.Config.FixedFormation) {
		return nil, errors.New("search policy identity mismatch")
	}
	s.preparer = p
	return s, nil
}

func restoreSearch(raw json.RawMessage, allowCatalog bool) (*Search, error) {
	var st searchSnapshot
	if len(raw) == 0 || !json.Valid(raw) {
		return nil, errors.New("invalid search state")
	}
	if err := json.Unmarshal(raw, &st); err != nil {
		return nil, err
	}
	if st.Schema != SearchSchema || st.PolicyHash == "" || st.RNGState == 0 || st.Candidates == nil || st.InFlight == nil || st.EvaluationStrategy == nil || st.EvaluationLineage == nil || st.Pending == nil || st.MutationStats == nil {
		return nil, errors.New("unsupported or incomplete search state")
	}
	if st.Config.LearnerPriorSHA256 != st.LearnerPriorSHA256 || st.LearnerPriorSHA256 != "" && !st.LearnerPriorLoaded {
		return nil, errors.New("portable learner prior checkpoint identity/load state mismatch")
	}
	if st.NextFeedback > st.NextSequence || st.Config.Budget != 0 && st.Config.Budget < st.NextSequence {
		return nil, errors.New("inconsistent search sequence/budget")
	}
	if st.CatalogSHA256 != "" && !allowCatalog {
		return nil, errors.New("catalog-backed search requires RestoreSearchWithTables")
	}
	for id, c := range st.Candidates {
		if c == nil || id == "" || c.ID != id || len(c.Raw) == 0 || !json.Valid(c.Raw) {
			return nil, errors.New("invalid candidate in search state")
		}
		var rawScenario RawScenario
		if err := json.Unmarshal(c.Raw, &rawScenario); err != nil || rawScenario.EncounterID != c.EncounterID ||
			(c.DefeatCount != 0 && rawScenario.DefeatCount != c.DefeatCount) ||
			(st.Config.ObjectiveMode == ObjectiveModeMechanicLanes && rawScenario.DefeatCount != c.DefeatCount) {
			return nil, errors.New("candidate encounter identity mismatch in search state")
		}
		c.DefeatCount = rawScenario.DefeatCount
		if st.Config.ObjectiveMode == ObjectiveModeMechanicLanes && c.Count > 0 && c.Mechanics == nil {
			return nil, errors.New("mechanics objective state lacks measured objective evidence; rehydrate from source records first")
		}
	}
	for id, seq := range st.InFlight {
		if id == "" || seq < st.NextFeedback || seq >= st.NextSequence || st.EvaluationStrategy[id] == "" || st.EvaluationLineage[id].CandidateID != st.EvaluationStrategy[id] {
			return nil, errors.New("invalid inflight candidate sequence")
		}
	}
	for seq := range st.Pending {
		if seq < st.NextFeedback || seq >= st.NextSequence {
			return nil, errors.New("invalid queued feedback sequence")
		}
	}
	for _, item := range st.Deferred {
		if item.OriginalID == "" || len(item.Raw) == 0 || !json.Valid(item.Raw) || item.Lineage.CandidateID == "" {
			return nil, errors.New("invalid deferred evaluation")
		}
		admitted, err := AdmitRawScenario(item.Raw)
		if err != nil || strategyIdentity(admitted.Raw) != item.Lineage.CandidateID {
			return nil, errors.New("deferred evaluation strategy identity mismatch")
		}
		if st.Candidates[item.Lineage.CandidateID] == nil {
			return nil, errors.New("deferred evaluation candidate is missing")
		}
	}
	s := &Search{state: st}
	if st.Config.ObjectiveMode == ObjectiveModeMechanicLanes && len(st.RootChildren) == 0 {
		st.RootChildren = map[string]uint64{}
		for _, candidate := range st.Candidates {
			root := candidate.RootID
			if root == "" {
				root = candidate.ID
			}
			st.RootChildren[root]++
		}
	}
	s.rebuildIndexes()
	return s, nil
}

func (s *Search) Snapshot() (json.RawMessage, error) {
	if s == nil {
		return nil, errors.New("nil search")
	}
	b, e := json.Marshal(s.state)
	return b, e
}

type portableMechanismLearnerPrior struct {
	Schema        string `json:"schema"`
	ObjectiveMode string `json:"objectiveMode"`
	OperatorStats map[string]map[string]struct {
		Attempts uint64 `json:"attempts"`
		Improved uint64 `json:"improved"`
	} `json:"operatorStats"`
	StatAnchors map[string]map[string]int64 `json:"statAnchors"`
}

// ImportMechanismLearnerPrior applies retrospective learner state only to a
// fresh search. It never imports historical candidates or battles.
func (s *Search) ImportMechanismLearnerPrior(data []byte) error {
	if s == nil || s.state.Config.LearnerMode != LearnerModeBranching || s.state.Config.ObjectiveMode != ObjectiveModeMechanicLanes {
		return errors.New("portable prior requires a branching mechanism-lanes search")
	}
	if s.state.NextSequence != 0 || len(s.state.InFlight) != 0 || len(s.state.Candidates) == 0 {
		return errors.New("portable learner prior may be imported only before the first proposal")
	}
	if s.state.LearnerPriorSHA256 == "" || digest(data) != s.state.LearnerPriorSHA256 {
		return errors.New("portable learner prior SHA256 mismatch")
	}
	var prior portableMechanismLearnerPrior
	if err := json.Unmarshal(data, &prior); err != nil {
		return err
	}
	if prior.Schema != "ka-mechanism-learner-prior-1" || prior.ObjectiveMode != ObjectiveModeMechanicLanes {
		return errors.New("unsupported portable mechanism learner prior")
	}
	for encounterText, operators := range prior.OperatorStats {
		encounter, err := strconv.ParseInt(encounterText, 10, 64)
		if err != nil {
			return errors.New("invalid encounter key in learner prior")
		}
		for operation, source := range operators {
			if operation != "set-stat" && operation != "stat:atk" && operation != "stat:spd" && operation != "stat:lck" {
				return fmt.Errorf("unsupported learner prior operator %q", operation)
			}
			if s.state.LearnerOperators[encounter] == nil {
				s.state.LearnerOperators[encounter] = map[string]LearnerOperatorStat{}
			}
			s.state.LearnerOperators[encounter][operation] = LearnerOperatorStat{Attempts: source.Attempts, Improved: source.Improved}
		}
	}
	for encounterText, anchors := range prior.StatAnchors {
		encounter, err := strconv.ParseInt(encounterText, 10, 64)
		if err != nil {
			return errors.New("invalid anchor encounter key in learner prior")
		}
		for parameterText, target := range anchors {
			parameter, err := strconv.ParseInt(parameterText, 10, 64)
			if err != nil {
				return errors.New("invalid stat parameter in learner prior")
			}
			if parameter != 13 && parameter != 15 && parameter != 16 {
				return fmt.Errorf("learner prior anchor parameter %d is outside controlled axes", parameter)
			}
			if err := ValidateNativeSearchStatTarget(parameter, target); err != nil {
				return fmt.Errorf("learner prior anchor: %w", err)
			}
			if s.state.StatAnchors[encounter] == nil {
				s.state.StatAnchors[encounter] = map[int64]int64{}
			}
			s.state.StatAnchors[encounter][parameter] = target
		}
	}
	s.state.LearnerPriorLoaded = true
	return nil
}

// Next returns one raw scenario, the stable strategy identity used for feedback,
// and an error after the configured evaluation budget is exhausted.
func (s *Search) Next() (json.RawMessage, string, error) {
	if s == nil {
		return nil, "", errors.New("nil search")
	}
	if s.state.Config.LearnerPriorSHA256 != "" && !s.state.LearnerPriorLoaded {
		return nil, "", errors.New("configured portable learner prior has not been imported")
	}
	if s.state.Config.Budget != 0 && s.state.NextSequence >= s.state.Config.Budget {
		return nil, "", errors.New("search budget exhausted")
	}
	var b json.RawMessage
	var identity string
	var err error
	lineage := SearchLineage{}
	deferredIndex := -1
	for i, item := range s.state.Deferred {
		admitted, admitErr := AdmitRawScenario(item.Raw)
		if admitErr != nil {
			return nil, "", fmt.Errorf("deferred candidate %s admission: %w", item.OriginalID, admitErr)
		}
		if !s.encounterInFocus(admitted.EncounterID) {
			continue
		}
		deferredIndex = i
		b = append(json.RawMessage(nil), item.Raw...)
		identity = item.Lineage.CandidateID
		lineage = item.Lineage
		if lineage.DeferredFrom == "" {
			lineage.DeferredFrom = item.OriginalID
		}
		break
	}
	if deferredIndex >= 0 {
		s.state.Deferred = append(s.state.Deferred[:deferredIndex], s.state.Deferred[deferredIndex+1:]...)
	} else {
		parent := s.selectParent()
		if parent == nil {
			return nil, "", errors.New("no admissible candidates remain")
		}
		lineage = SearchLineage{Origin: "starting-candidate"}
		if parent.Seed && !parent.HasScore && !parent.SeedDispatched {
			// Preserve and evaluate every user-supplied baseline before generating a
			// descendant from it. This gives adaptive selection a measured anchor.
			b = append(json.RawMessage(nil), parent.Raw...)
			identity = parent.ID
			parent.SeedDispatched = true
		} else {
			var child json.RawMessage
			var mutation string
			var mutateErr error
			if s.state.Config.LearnerMode == LearnerModeBranching && s.state.Config.ConstraintProfile == ConstraintProfileFixedDPS {
				child, mutation, mutateErr = s.mutateControlledStat(parent)
			} else if s.state.Config.FixedFormation {
				child, mutateErr = mutateFixedFormation(parent.Raw, s.rand)
				mutation = "set-dps-stat"
			} else if s.preparer != nil {
				child, mutation, mutateErr = mutateScenarioWithPreparer(parent.Raw, s.preparer, s.rand, s.preferredMutation(parent.EncounterID))
			} else {
				child, mutateErr = mutateScenario(parent.Raw, s.rand)
			}
			if mutateErr != nil {
				s.state.Rejected++
				return nil, "", mutateErr
			}
			lineage = SearchLineage{ParentID: parent.ID, Generation: parent.Generation + 1, Origin: "mutation", Mutation: mutation, ParentSource: s.lastParentSource}
			if strings.HasPrefix(mutation, "set-stat:") {
				parts := strings.Split(mutation, ":")
				if len(parts) >= 3 {
					lineage.Mutation = "set-stat"
					lineage.MutationParameter, _ = strconv.ParseInt(parts[1], 10, 64)
					lineage.MutationScale = parts[2]
				}
				if len(parts) == 4 {
					lineage.MutationTarget, _ = strconv.ParseInt(parts[3], 10, 64)
				}
			}
			admitted, admitErr := AdmitRawScenario(child)
			if admitErr != nil {
				s.state.Rejected++
				return nil, "", fmt.Errorf("mutated scenario rejected by canonical admission: %w", admitErr)
			}
			if s.state.Config.FixedFormation {
				if err := ValidateFixedFormation(admitted.Raw); err != nil {
					s.state.Rejected++
					return nil, "", fmt.Errorf("mutated scenario violates the fixed formation: %w", err)
				}
			}
			if s.state.Config.ConstraintProfile == ConstraintProfileFixedDPS {
				signature, validateErr := validateControlledScenario(admitted.Raw)
				if validateErr != nil || signature != s.state.ConstraintReferences[admitted.EncounterID] {
					s.state.Rejected++
					if validateErr != nil {
						return nil, "", fmt.Errorf("mutated candidate violates fixed profile: %w", validateErr)
					}
					return nil, "", errors.New("mutated candidate changed fixed-profile fields")
				}
			}
			// Each descendant gets a deterministic battle seed pair while every
			// unrecognized source field remains present in the raw object.
			var candidateObject map[string]json.RawMessage
			if err = json.Unmarshal(admitted.Raw, &candidateObject); err != nil {
				return nil, "", err
			}
			candidateObject["mathSeed"], _ = json.Marshal(s.rand() & 0x7fffffff)
			candidateObject["libSeed"], _ = json.Marshal(s.rand() & 0x7fffffff)
			b, err = json.Marshal(candidateObject)
			if err != nil {
				return nil, "", err
			}
			identity = strategyIdentity(b)
			if old, ok := s.state.Candidates[identity]; ok {
				if !bytes.Equal(strategyBytes(old.Raw), strategyBytes(b)) {
					return nil, "", errors.New("strategy identity collision")
				}
			} else {
				rootID := parent.RootID
				if rootID == "" {
					rootID = parent.ID
				}
				candidate := &SearchCandidate{ID: identity, EncounterID: admitted.EncounterID, DefeatCount: admitted.DefeatCount, Raw: append(json.RawMessage(nil), b...), ParentID: parent.ID, RootID: rootID, Generation: lineage.Generation, Order: s.state.NextCandidateOrder}
				s.state.NextCandidateOrder++
				s.state.Candidates[identity] = candidate
				s.indexCandidate(candidate)
				s.state.RootChildren[rootID]++
			}
			if lineage.Mutation == "set-stat" {
				s.bumpLearnerOperator(admitted.EncounterID, lineage.Mutation, false)
				if lineage.MutationParameter != 0 {
					s.bumpLearnerOperator(admitted.EncounterID, controlledStatOperator(lineage.MutationParameter), false)
					s.bumpLearnerScale(admitted.EncounterID, lineage.MutationParameter, lineage.MutationScale, false)
				}
			}
		}
	}
	seq := s.state.NextSequence
	s.state.NextSequence++
	evalID := fmt.Sprintf("%s:%d", identity, seq)
	s.state.InFlight[evalID] = seq
	s.state.EvaluationStrategy[evalID] = identity
	lineage.CandidateID = identity
	s.state.EvaluationLineage[evalID] = lineage
	if c := s.state.Candidates[identity]; c != nil {
		s.pruneEncounter(c.EncounterID)
	}
	return b, evalID, nil
}

// Defer closes an unsubmitted reservation without awarding a score and keeps
// its exact raw intent and lineage for the next matching focus. The next
// reservation reuses those bytes without a mutation or RNG draw.
func (s *Search) Defer(identity string, raw json.RawMessage) error {
	if s == nil || len(raw) == 0 || !json.Valid(raw) {
		return errors.New("invalid deferred search intent")
	}
	if _, ok := s.state.InFlight[identity]; !ok {
		return errors.New("cannot defer unknown or completed identity")
	}
	seq := s.state.InFlight[identity]
	if _, hasFeedback := s.state.Pending[seq]; hasFeedback {
		return errors.New("cannot defer an identity with queued feedback")
	}
	lineage, ok := s.state.EvaluationLineage[identity]
	strategy := s.state.EvaluationStrategy[identity]
	if !ok || strategy == "" || lineage.CandidateID != strategy {
		return errors.New("deferred identity has no valid strategy lineage")
	}
	admitted, err := AdmitRawScenario(raw)
	if err != nil || strategyIdentity(admitted.Raw) != strategy {
		return errors.New("deferred raw intent does not match its reserved strategy")
	}
	for _, item := range s.state.Deferred {
		if item.OriginalID == identity {
			return errors.New("identity is already deferred")
		}
	}
	if s.state.Candidates[strategy] == nil {
		return errors.New("deferred strategy is missing from the population")
	}
	s.state.Deferred = append(s.state.Deferred, deferredEvaluation{
		OriginalID: identity,
		Raw:        append(json.RawMessage(nil), raw...),
		Lineage:    lineage,
	})
	if err := s.Unscored(identity); err != nil {
		s.state.Deferred = s.state.Deferred[:len(s.state.Deferred)-1]
		return err
	}
	return nil
}

func (s *Search) selectParent() *SearchCandidate {
	// Every initial encounter gets a fixed share of dispatch slots, independent
	// of reward magnitude in other encounters. Within that stratum, parent choice
	// uses its own earned history and UCB exploration.
	encounters := make([]int64, 0, len(s.encounterIDs))
	for _, encounterID := range s.encounterIDs {
		if !s.encounterInFocus(encounterID) {
			continue
		}
		for _, candidate := range s.byEncounter[encounterID] {
			if candidate.Seed && !candidate.Unusable {
				encounters = append(encounters, encounterID)
				break
			}
		}
	}
	if len(encounters) == 0 {
		for _, encounterID := range s.encounterIDs {
			if !s.encounterInFocus(encounterID) {
				continue
			}
			for _, candidate := range s.byEncounter[encounterID] {
				if !candidate.Unusable {
					encounters = append(encounters, encounterID)
					break
				}
			}
		}
	}
	if len(encounters) == 0 {
		return nil
	}
	targetEncounter := encounters[s.state.NextSequence%uint64(len(encounters))]
	seeds := make([]*SearchCandidate, 0)
	pool := s.byEncounter[targetEncounter]
	list := make([]*SearchCandidate, 0, len(pool))
	for _, c := range pool {
		if c.Unusable {
			continue
		}
		if c.Seed && !c.HasScore && !c.SeedDispatched {
			seeds = append(seeds, c)
		}
		if c.HasScore {
			list = append(list, c)
		}
	}
	if len(seeds) > 0 {
		sort.Slice(seeds, func(i, j int) bool { return seeds[i].ID < seeds[j].ID })
		return seeds[0]
	}
	if s.state.Config.LearnerMode == LearnerModeBranching {
		return s.selectMechanicsParent(targetEncounter)
	}
	if len(list) == 0 {
		for _, c := range pool {
			if c.Seed && !c.Unusable {
				list = append(list, c)
			}
		}
	}
	sort.Slice(list, func(i, j int) bool { return list[i].ID < list[j].ID })
	if len(list) == 0 {
		for _, c := range pool {
			if !c.Unusable {
				list = append(list, c)
			}
		}
		sort.Slice(list, func(i, j int) bool { return list[i].ID < list[j].ID })
	}
	if len(list) == 0 {
		return nil
	}
	total := uint64(0)
	for _, c := range list {
		total += c.Count
	}
	if total == 0 {
		return list[0]
	}
	best := list[0]
	bestScore := math.Inf(-1)
	for _, c := range list {
		score := c.Mean + s.state.Config.Exploration*math.Sqrt(math.Log(float64(total)+1)/float64(c.Count+1))
		if score > bestScore || score == bestScore && c.ID < best.ID {
			best, bestScore = c, score
		}
	}
	return best
}

var mechanicsParentSources = []string{"earned", "potential", "setup", "efficiency", "region", "exploration", "mean"}

func (s *Search) selectMechanicsParent(encounterID int64) *SearchCandidate {
	pool := make([]*SearchCandidate, 0, len(s.byEncounter[encounterID]))
	for _, candidate := range s.byEncounter[encounterID] {
		if !candidate.Unusable {
			pool = append(pool, candidate)
		}
	}
	sort.Slice(pool, func(i, j int) bool {
		if pool[i].Order != pool[j].Order {
			return pool[i].Order < pool[j].Order
		}
		return pool[i].ID < pool[j].ID
	})
	if len(pool) == 0 {
		return nil
	}
	source := mechanicsParentSources[(int(s.state.BranchingProposals)+1)%len(mechanicsParentSources)] // attempt starts at one
	s.state.BranchingProposals++
	choices := []*SearchCandidate{}
	fallback := ""
	switch source {
	case "earned", "potential", "setup", "efficiency":
		// Python concatenates the top four per defeat-count group; the active encounter
		// roster is kept sorted so exact objective ties remain deterministic.
		groups := map[int64][]MechanicsLaneRecord{}
		for _, candidate := range pool {
			if candidate.Mechanics == nil {
				continue
			}
			groups[candidate.DefeatCount] = append(groups[candidate.DefeatCount], candidate.Mechanics.LaneRecord(candidate.ID, candidate.EncounterID, candidate.DefeatCount))
		}
		defeats := make([]int64, 0, len(groups))
		for defeat := range groups {
			defeats = append(defeats, defeat)
		}
		sort.Slice(defeats, func(i, j int) bool { return defeats[i] < defeats[j] })
		for _, defeat := range defeats {
			ranked, err := RankMechanicsRecords(groups[defeat], MechanicsLanePool)
			if err != nil {
				continue
			}
			ids := ranked.LaneRankings[source]
			for _, id := range ids {
				if candidate := s.state.Candidates[id]; candidate != nil {
					choices = append(choices, candidate)
				}
			}
		}
	case "region":
		seen := map[string]bool{}
		byID := append([]*SearchCandidate(nil), pool...)
		sort.Slice(byID, func(i, j int) bool { return byID[i].ID < byID[j].ID })
		for _, candidate := range byID {
			region, err := strategyRegion(candidate.Raw)
			if err == nil && !seen[region] {
				choices = append(choices, candidate)
				seen[region] = true
			}
		}
	case "exploration":
		choices = append(choices, pool...)
	case "mean":
		// Go has no source-equivalent >=64-sample validation bank. Never substitute
		// search observations for validation evidence; report the population fallback.
		fallback = "mean:fallback-population(no-validation-bank)"
		choices = append(choices, pool...)
	}
	if len(choices) == 0 {
		fallback = source + ":fallback-population(empty-source)"
		choices = append(choices, pool...)
	}
	sourceLabel := source
	if fallback != "" {
		sourceLabel = fallback
	}
	s.lastParentSource = sourceLabel
	weights := make([]float64, len(choices))
	var total float64
	for i, candidate := range choices {
		root := candidate.RootID
		if root == "" {
			root = candidate.ID
		}
		children, improved := float64(s.state.RootChildren[root]), float64(s.state.RootImprovedChildren[root])
		weights[i] = .5 + (improved+1)/(children+2)
		total += weights[i]
	}
	draw := float64(s.rand()>>11) / (1 << 53) * total
	for i, weight := range weights {
		draw -= weight
		if draw < 0 {
			return choices[i]
		}
	}
	return choices[len(choices)-1]
}

func (s *Search) bumpLearnerOperator(encounter int64, operation string, improved bool) {
	if s.state.LearnerOperators[encounter] == nil {
		s.state.LearnerOperators[encounter] = map[string]LearnerOperatorStat{}
	}
	stat := s.state.LearnerOperators[encounter][operation]
	if improved {
		stat.Improved++
	} else {
		stat.Attempts++
	}
	s.state.LearnerOperators[encounter][operation] = stat
}

func (s *Search) bumpLearnerScale(encounter, parameter int64, scale string, improved bool) {
	if s.state.LearnerScales[encounter] == nil {
		s.state.LearnerScales[encounter] = map[int64]map[string]LearnerScaleStat{}
	}
	if s.state.LearnerScales[encounter][parameter] == nil {
		s.state.LearnerScales[encounter][parameter] = map[string]LearnerScaleStat{}
	}
	stat := s.state.LearnerScales[encounter][parameter][scale]
	if improved {
		stat.Improved++
	} else {
		stat.Attempts++
	}
	s.state.LearnerScales[encounter][parameter][scale] = stat
}

func (s *Search) planLearnerScale(encounter, parameter int64, scale string) {
	if s.state.LearnerScales[encounter] == nil {
		s.state.LearnerScales[encounter] = map[int64]map[string]LearnerScaleStat{}
	}
	if s.state.LearnerScales[encounter][parameter] == nil {
		s.state.LearnerScales[encounter][parameter] = map[string]LearnerScaleStat{}
	}
	stat := s.state.LearnerScales[encounter][parameter][scale]
	stat.Planned++
	s.state.LearnerScales[encounter][parameter][scale] = stat
}

func rawParameterValue(raw json.RawMessage, parameter int64) (int64, bool) {
	var scenario RawScenario
	if json.Unmarshal(raw, &scenario) != nil || len(scenario.OwnUnits) == 0 {
		return 0, false
	}
	var values map[string]map[string]int64
	if json.Unmarshal(scenario.OwnUnits[0].Parameters, &values) != nil {
		return 0, false
	}
	entry, ok := values[strconv.FormatInt(parameter, 10)]
	return entry["rawValue"], ok && entry != nil
}

func (s *Search) mutateControlledStat(parent *SearchCandidate) (json.RawMessage, string, error) {
	if s.preparer == nil {
		return nil, "", errors.New("controlled learner requires canonical preparer")
	}
	parameters := append([]int64(nil), controlledMutableParameters...)
	labels := make([]string, len(parameters))
	weights := make(map[string]float64, len(parameters))
	for i, parameter := range parameters {
		labels[i] = controlledStatOperator(parameter)
		stat := s.state.LearnerOperators[parent.EncounterID][labels[i]]
		weights[labels[i]] = learnerOperatorWeights(map[string]learnerCounter{labels[i]: {Attempts: stat.Attempts, Improved: stat.Improved}}, []string{labels[i]})[labels[i]]
	}
	selectedLabel := weightedStringChoice(labels, weights, float64(s.rand()>>11)/(1<<53))
	parameter := int64(0)
	for i, label := range labels {
		if label == selectedLabel {
			parameter = parameters[i]
			break
		}
	}
	if parameter == 0 {
		return nil, "", errors.New("controlled stat selector returned unknown axis")
	}
	current, ok := controlledEffectiveStat(parent.Raw, s.preparer, parameter)
	if !ok {
		return nil, "", fmt.Errorf("controlled stat %d is unavailable", parameter)
	}
	encounter := parent.EncounterID
	var stats map[int64]map[string]LearnerScaleStat
	if s.state.LearnerScales[encounter] != nil {
		stats = s.state.LearnerScales[encounter]
	}
	scale, err := chooseLearnerScale(func() uint64 { return s.rand() }, stats, parameter)
	if err != nil {
		return nil, "", err
	}
	scaleName := learnerScaleKey(scale)
	if scale == 0 {
		scaleName = "jump"
	}
	s.planLearnerScale(encounter, parameter, scaleName)
	bounds, err := loadNativeSearchStatBounds()
	if err != nil {
		return nil, "", err
	}
	bound := bounds[parameter]
	base := current
	if anchor := s.state.StatAnchors[encounter][parameter]; anchor != 0 {
		base = anchor
	}
	floatDraw := func() float64 { return float64(s.rand()>>11) / (1 << 53) }
	intDraw := func(low, high int64) int64 {
		if high <= low {
			return low
		}
		return low + int64(s.rand()%uint64(high-low+1))
	}
	target := learnerStatTarget(floatDraw, intDraw, base, bound.Minimum, bound.Maximum, scale)
	unitFields := map[string]json.RawMessage{}
	var top map[string]json.RawMessage
	if err := json.Unmarshal(parent.Raw, &top); err != nil {
		return nil, "", err
	}
	var units []json.RawMessage
	if err := json.Unmarshal(top["ownUnits"], &units); err != nil || len(units) == 0 {
		return nil, "", errors.New("controlled DPS unit missing")
	}
	if err := json.Unmarshal(units[0], &unitFields); err != nil {
		return nil, "", err
	}
	if err := setNativeEffectiveStat(unitFields, s.preparer, parameter, target); err != nil {
		return nil, "", err
	}
	units[0], _ = json.Marshal(unitFields)
	top["ownUnits"], _ = json.Marshal(units)
	encoded, err := json.Marshal(top)
	if err != nil {
		return nil, "", err
	}
	return encoded, fmt.Sprintf("set-stat:%d:%s:%d", parameter, scaleName, target), nil
}

func controlledStatOperator(parameter int64) string {
	switch parameter {
	case 13:
		return "stat:atk"
	case 15:
		return "stat:spd"
	case 16:
		return "stat:lck"
	default:
		return ""
	}
}

func controlledEffectiveStat(raw json.RawMessage, preparer *Preparer, parameter int64) (int64, bool) {
	var scenario RawScenario
	if json.Unmarshal(raw, &scenario) != nil || len(scenario.OwnUnits) == 0 || preparer == nil {
		return 0, false
	}
	unit, err := preparer.makeOwn(scenario.OwnUnits[0])
	if err != nil {
		return 0, false
	}
	value, err := preparer.effective(unit, parameter, false)
	if err != nil {
		return 0, false
	}
	return value, true
}

// Observe associates a completed battle with its strategy. Out-of-order results
// wait in Pending and only affect selection after every earlier dispatch lands.
func (s *Search) Observe(identity string, earned float64) error {
	if s == nil {
		return errors.New("nil search")
	}
	if math.IsNaN(earned) || math.IsInf(earned, 0) {
		return errors.New("earned must be finite")
	}
	_, ok := s.state.InFlight[identity]
	if !ok {
		return errors.New("unknown or already observed identity")
	}
	return s.recordFeedback(queuedFeedback{Identity: identity, Earned: earned, Accepted: true, Scored: true})
}

// ObserveMechanics adds current-run measurements to versioned candidate evidence.
// Historical checkpoint telemetry is never inferred or backfilled.
func (s *Search) ObserveMechanics(identity string, earned *float64, observation MechanicsObservation) error {
	if s == nil || s.state.Config.ObjectiveMode != ObjectiveModeMechanicLanes {
		return errors.New("mechanics evidence requires mechanism-lanes-v3")
	}
	if earned != nil && (math.IsNaN(*earned) || math.IsInf(*earned, 0)) {
		return errors.New("earned must be finite")
	}
	if _, ok := s.state.InFlight[identity]; !ok {
		return errors.New("unknown or already observed identity")
	}
	feedback := queuedFeedback{Identity: identity, Accepted: true, Mechanics: &observation}
	if earned != nil {
		feedback.Earned = *earned
		feedback.Scored = true
	}
	return s.recordFeedback(feedback)
}

// Reject consumes an evaluation sequence without changing any strategy score.
// It is used when full preparation rejects a generated raw candidate.
func (s *Search) Reject(identity string) error {
	if s == nil {
		return errors.New("nil search")
	}
	_, ok := s.state.InFlight[identity]
	if !ok {
		return errors.New("unknown or already completed identity")
	}
	return s.recordFeedback(queuedFeedback{Identity: identity, Rejected: true})
}

// Unscored consumes a completed but unresolved evaluation (for example a
// native run with no trustworthy Earned result) without inventing a score or
// disqualifying the strategy.
func (s *Search) Unscored(identity string) error {
	if s == nil {
		return errors.New("nil search")
	}
	if _, ok := s.state.InFlight[identity]; !ok {
		return errors.New("unknown or already completed identity")
	}
	return s.recordFeedback(queuedFeedback{Identity: identity, Accepted: true})
}

func (s *Search) InFlight(identity string) bool {
	if s == nil {
		return false
	}
	_, ok := s.state.InFlight[identity]
	return ok
}

// NeedsResult distinguishes an unfinished dispatch from an in-flight identity
// whose Observe/Reject event is already persisted behind an earlier sequence.
func (s *Search) NeedsResult(identity string) bool {
	if s == nil {
		return false
	}
	seq, ok := s.state.InFlight[identity]
	if !ok {
		return false
	}
	_, hasFeedback := s.state.Pending[seq]
	return !hasFeedback
}

func (s *Search) recordFeedback(feedback queuedFeedback) error {
	seq := s.state.InFlight[feedback.Identity]
	if old, ok := s.state.Pending[seq]; ok {
		if jsonSemanticallyEqual(old, feedback) {
			return nil
		}
		return errors.New("conflicting result for dispatch")
	}
	s.state.Pending[seq] = feedback
	for {
		p, ready := s.state.Pending[s.state.NextFeedback]
		if !ready {
			break
		}
		pruneEncounterID := int64(-1)
		strategy := s.state.EvaluationStrategy[p.Identity]
		candidate := s.state.Candidates[strategy]
		if p.Mechanics != nil && candidate != nil {
			if candidate.Mechanics == nil {
				candidate.Mechanics = &MechanicsCandidateEvidence{}
			}
			candidate.Mechanics.Observe(*p.Mechanics)
			pruneEncounterID = candidate.EncounterID
			if s.state.Config.LearnerMode == LearnerModeBranching && !candidate.Seed && !candidate.ImprovementCounted {
				parent := s.state.Candidates[candidate.ParentID]
				if parent != nil && parent.Mechanics != nil && candidate.Mechanics.N > 0 {
					parentRecord := parent.Mechanics.LaneRecord(parent.ID, parent.EncounterID, parent.DefeatCount)
					childRecord := candidate.Mechanics.LaneRecord(candidate.ID, candidate.EncounterID, candidate.DefeatCount)
					candidate.ImprovedLanes = mechanicsImprovedLanes(parentRecord, childRecord)
					if len(candidate.ImprovedLanes) != 0 {
						candidate.ImprovementCounted = true
						rootID := candidate.RootID
						if rootID == "" {
							rootID = candidate.ID
						}
						s.state.RootImprovedChildren[rootID]++
						lineage := s.state.EvaluationLineage[p.Identity]
						if lineage.Mutation != "" {
							s.bumpLearnerOperator(candidate.EncounterID, lineage.Mutation, true)
						}
						if lineage.MutationParameter != 0 {
							if s.state.StatAnchors[candidate.EncounterID] == nil {
								s.state.StatAnchors[candidate.EncounterID] = map[int64]int64{}
							}
							if lineage.MutationTarget != 0 {
								s.state.StatAnchors[candidate.EncounterID][lineage.MutationParameter] = lineage.MutationTarget
							}
							// Python currently bumps per-scale attempts but never per-scale improvements.
						}
					}
				}
			}
		}
		if p.Scored {
			c := candidate
			if c == nil {
				return errors.New("feedback candidate missing")
			}
			c.Count++
			if c.Count == 1 {
				c.Mean = p.Earned
				c.Best = p.Earned
			} else {
				c.Mean += (p.Earned - c.Mean) / float64(c.Count)
				if p.Earned > c.Best {
					c.Best = p.Earned
				}
			}
			c.HasScore = true
			pruneEncounterID = c.EncounterID
			lineage := s.state.EvaluationLineage[p.Identity]
			if lineage.Mutation != "" {
				byOperator := s.state.MutationStats[c.EncounterID]
				if byOperator == nil {
					byOperator = map[string]MutationStat{}
					s.state.MutationStats[c.EncounterID] = byOperator
				}
				stat := byOperator[lineage.Mutation]
				stat.Count++
				if stat.Count == 1 {
					stat.Mean, stat.Best = p.Earned, p.Earned
				} else {
					stat.Mean += (p.Earned - stat.Mean) / float64(stat.Count)
					if p.Earned > stat.Best {
						stat.Best = p.Earned
					}
				}
				byOperator[lineage.Mutation] = stat
			}
		} else if p.Rejected {
			s.state.Rejected++
			strategy := s.state.EvaluationStrategy[p.Identity]
			if c := s.state.Candidates[strategy]; c != nil {
				c.Unusable = true
				pruneEncounterID = c.EncounterID
			}
		}
		delete(s.state.Pending, s.state.NextFeedback)
		delete(s.state.InFlight, p.Identity)
		delete(s.state.EvaluationStrategy, p.Identity)
		delete(s.state.EvaluationLineage, p.Identity)
		s.state.NextFeedback++
		if pruneEncounterID >= 0 {
			s.pruneEncounter(pruneEncounterID)
		}
	}
	return nil
}

func (s *Search) rand() uint64 {
	s.state.RNGState += 0x9e3779b97f4a7c15
	z := s.state.RNGState
	z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9
	z = (z ^ (z >> 27)) * 0x94d049bb133111eb
	return z ^ (z >> 31)
}

func (s *Search) preferredMutation(encounterID int64) string {
	stats := s.state.MutationStats[encounterID]
	if len(stats) == 0 {
		return ""
	}
	var total uint64
	for _, stat := range stats {
		total += stat.Count
	}
	bestScore := math.Inf(-1)
	best := []string{}
	for _, op := range nativeMutationOps {
		stat := stats[op]
		score := math.Inf(1)
		if stat.Count > 0 {
			score = stat.Mean + s.state.Config.Exploration*math.Sqrt(math.Log(float64(total)+1)/float64(stat.Count+1))
		}
		if score > bestScore {
			bestScore = score
			best = []string{op}
		} else if score == bestScore {
			best = append(best, op)
		}
	}
	if len(best) == 0 {
		return ""
	}
	return best[s.rand()%uint64(len(best))]
}

// pruneEncounter bounds the adaptive population while the append-only journal
// and strategy library retain every durable evaluation and lineage row.
func (s *Search) pruneEncounter(encounterID int64) {
	limit := s.state.Config.PopulationLimit
	if limit <= 0 {
		limit = 1024
	}
	for {
		members := make([]*SearchCandidate, 0, limit+1)
		for _, c := range s.byEncounter[encounterID] {
			members = append(members, c)
		}
		if len(members) <= limit {
			return
		}
		protected := map[string]bool{}
		var bestMean, bestEarned, bestSeed *SearchCandidate
		for _, c := range members {
			if c.HasScore {
				if bestMean == nil || c.Mean > bestMean.Mean || c.Mean == bestMean.Mean && c.ID < bestMean.ID {
					bestMean = c
				}
				if bestEarned == nil || c.Best > bestEarned.Best || c.Best == bestEarned.Best && c.ID < bestEarned.ID {
					bestEarned = c
				}
			}
			if c.Seed && (bestSeed == nil || c.HasScore && !bestSeed.HasScore || c.HasScore == bestSeed.HasScore && c.Mean > bestSeed.Mean || c.HasScore == bestSeed.HasScore && c.Mean == bestSeed.Mean && c.ID < bestSeed.ID) {
				bestSeed = c
			}
		}
		for _, c := range []*SearchCandidate{bestMean, bestEarned, bestSeed} {
			if c != nil {
				protected[c.ID] = true
			}
		}
		if s.state.Config.LearnerMode == LearnerModeBranching {
			// Keep every source that the active selector can still draw: four
			// leaders per defeat group for each lane, region representatives, and
			// one durable representative per root lineage.
			groups := map[int64][]MechanicsLaneRecord{}
			for _, c := range members {
				if c.Mechanics != nil {
					groups[c.DefeatCount] = append(groups[c.DefeatCount], c.Mechanics.LaneRecord(c.ID, c.EncounterID, c.DefeatCount))
				}
				root := c.RootID
				if root == "" {
					root = c.ID
				}
				if c.ID == root {
					protected[c.ID] = true
				}
			}
			for _, records := range groups {
				ranked, err := RankMechanicsRecords(records, MechanicsLanePool)
				if err == nil {
					for _, ids := range ranked.LaneRankings {
						for _, id := range ids {
							protected[id] = true
						}
					}
				}
			}
			byID := append([]*SearchCandidate(nil), members...)
			sort.Slice(byID, func(i, j int) bool { return byID[i].ID < byID[j].ID })
			regions := map[string]bool{}
			for _, c := range byID {
				region, err := strategyRegion(c.Raw)
				if err == nil && !regions[region] {
					protected[c.ID] = true
					regions[region] = true
				}
			}
		}
		for _, c := range members {
			if c.Seed && !c.SeedDispatched {
				protected[c.ID] = true
			}
		}
		for evalID, strategyID := range s.state.EvaluationStrategy {
			if c := s.state.Candidates[strategyID]; c != nil && c.EncounterID == encounterID {
				protected[c.ID] = true
			}
			if lineage := s.state.EvaluationLineage[evalID]; lineage.ParentID != "" {
				protected[lineage.ParentID] = true
			}
		}
		for _, item := range s.state.Deferred {
			protected[item.Lineage.CandidateID] = true
			if item.Lineage.ParentID != "" {
				protected[item.Lineage.ParentID] = true
			}
		}
		var worst *SearchCandidate
		for _, c := range members {
			if protected[c.ID] {
				continue
			}
			if worst == nil || candidateWorse(c, worst) {
				worst = c
			}
		}
		if worst == nil {
			return
		}
		delete(s.state.Candidates, worst.ID)
		s.unindexCandidate(worst)
	}
}

func candidateWorse(a, b *SearchCandidate) bool {
	if a.HasScore != b.HasScore {
		return !a.HasScore
	}
	if a.Mean != b.Mean {
		return a.Mean < b.Mean
	}
	if a.Best != b.Best {
		return a.Best < b.Best
	}
	if a.Count != b.Count {
		return a.Count < b.Count
	}
	return a.ID > b.ID
}

func strategyBytes(raw json.RawMessage) json.RawMessage {
	var m map[string]json.RawMessage
	if json.Unmarshal(raw, &m) != nil {
		return raw
	}
	m["mathSeed"] = json.RawMessage("0")
	m["libSeed"] = json.RawMessage("0")
	b, e := json.Marshal(m)
	if e != nil {
		return raw
	}
	return b
}
func strategyIdentity(raw []byte) string { return digest(strategyBytes(raw)) }

// Mutations change existing invocation levels within 0..2, or swap adjacent
// functionally tied roster entries. No equipment, skill IDs, raw stats, or
// scalar values are invented. AdmitRawScenario validates every emitted row.
func mutateScenario(raw json.RawMessage, random func() uint64) (json.RawMessage, error) {
	var m map[string]json.RawMessage
	if err := json.Unmarshal(raw, &m); err != nil {
		return nil, err
	}
	var units []json.RawMessage
	if err := json.Unmarshal(m["ownUnits"], &units); err != nil {
		return nil, errors.New("scenario ownUnits unavailable")
	}
	type knob struct{ unit, level, swap int }
	knobs := []knob{}
	for i, uRaw := range units {
		var u map[string]json.RawMessage
		if json.Unmarshal(uRaw, &u) != nil {
			continue
		}
		var levels []int64
		if json.Unmarshal(u["invocationLevels"], &levels) == nil {
			for j, n := range levels {
				if n >= 0 && n <= 2 {
					knobs = append(knobs, knob{unit: i, level: j, swap: -1})
				}
			}
		}
		if i+1 < len(units) && formationTie(uRaw, units[i+1]) {
			knobs = append(knobs, knob{unit: i, level: -1, swap: i + 1})
		}
	}
	if len(knobs) == 0 {
		return nil, errors.New("scenario has no supported legal level change or tied-roster permutation")
	}
	// Choose one knob and one direction; admission rejects any unsupported schema.
	k := knobs[random()%uint64(len(knobs))]
	if k.swap >= 0 {
		units[k.unit], units[k.swap] = units[k.swap], units[k.unit]
		m["ownUnits"], _ = json.Marshal(units)
	} else {
		var u map[string]json.RawMessage
		_ = json.Unmarshal(units[k.unit], &u)
		var levels []int64
		_ = json.Unmarshal(u["invocationLevels"], &levels)
		if levels[k.level] == 2 {
			levels[k.level] = 1
		} else if levels[k.level] == 0 {
			levels[k.level] = 1
		} else if random()&1 == 0 {
			levels[k.level] = 0
		} else {
			levels[k.level] = 2
		}
		u["invocationLevels"], _ = json.Marshal(levels)
		units[k.unit], _ = json.Marshal(u)
		m["ownUnits"], _ = json.Marshal(units)
	}
	out, err := json.Marshal(m)
	return out, err
}

// A tied pair must have identical formation-relevant identity, parameters,
// skills, and invocation settings. Swapping changes only their input order,
// which is the final stable tie-break in canonical preparation.
func formationTie(a, b json.RawMessage) bool {
	var x, y map[string]json.RawMessage
	if json.Unmarshal(a, &x) != nil || json.Unmarshal(b, &y) != nil {
		return false
	}
	for _, k := range []string{"human", "monsterId", "parameters", "skills", "invocationLevels", "visitor", "leaderIdentity", "humanFlags", "weaponId", "equipment"} {
		if !bytes.Equal(canonicalJSON(x[k]), canonicalJSON(y[k])) {
			return false
		}
	}
	return true
}
func canonicalJSON(raw json.RawMessage) []byte {
	var b bytes.Buffer
	if len(raw) == 0 {
		return nil
	}
	if json.Compact(&b, raw) != nil {
		return raw
	}
	return b.Bytes()
}

// mutateScenarioWithPreparer emits one legal strategy-space step. The caller
// may request a UCB-preferred operator; all remaining legal operators remain
// available when that move does not fit the selected parent.
func mutateScenarioWithPreparer(raw json.RawMessage, p *Preparer, random func() uint64, preferred string) (json.RawMessage, string, error) {
	if p == nil {
		return nil, "", errors.New("search mutation preparer is required")
	}
	order := append([]string(nil), nativeMutationOps...)
	if preferred != "" {
		for i, op := range order {
			if op == preferred {
				order[0], order[i] = order[i], order[0]
				break
			}
		}
	}
	shuffleFrom := 0
	if preferred != "" && order[0] == preferred {
		shuffleFrom = 1
	}
	for i := len(order) - 1; i > shuffleFrom; i-- {
		j := shuffleFrom + int(random()%uint64(i-shuffleFrom+1))
		order[i], order[j] = order[j], order[i]
	}
	var reasons []string
	for _, op := range order {
		child, err := applyNativeMutation(raw, p, random, op)
		if err != nil {
			reasons = append(reasons, op+": "+err.Error())
			continue
		}
		admitted, err := AdmitRawScenario(child)
		if err == nil {
			err = validateNativeMutation(admitted, p)
		}
		if err != nil {
			reasons = append(reasons, op+": "+err.Error())
			continue
		}
		if bytes.Equal(strategyBytes(admitted.Raw), strategyBytes(raw)) {
			reasons = append(reasons, op+": mutation leaves strategy identity unchanged")
			continue
		}
		return admitted.Raw, op, nil
	}
	if len(reasons) > 4 {
		reasons = reasons[:4]
	}
	return nil, "", fmt.Errorf("no legal catalog-backed mutation: %v", reasons)
}

var nativeMutationOps = []string{"add-skill", "remove-skill", "replace-skill", "move-skill", "set-trigger", "set-stat", "set-formation-skill", "set-weapon", "reorder-roster", "add-unit", "remove-unit"}

func nativeSearchSkillIDs() []int64 {
	ids := make([]int64, 0, len(nativeSearchSkills))
	for id := range nativeSearchSkills {
		ids = append(ids, id)
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
	return ids
}

var nativeSearchSkills = map[int64]bool{
	4: true, 5: true, 6: true, 7: true, 8: true, 9: true, 10: true, 11: true, 12: true, 13: true, 14: true, 15: true, 16: true, 17: true, 18: true, 19: true,
	20: true, 21: true, 22: true, 23: true, 24: true, 25: true, 26: true, 27: true, 28: true, 29: true, 30: true, 31: true, 32: true, 33: true, 34: true, 35: true, 36: true,
	37: true, 38: true, 39: true, 40: true, 41: true, 105: true, 106: true, 107: true, 108: true, 110: true, 114: true, 115: true, 116: true, 117: true, 118: true, 119: true,
}

func applyNativeMutation(raw json.RawMessage, p *Preparer, random func() uint64, op string) (json.RawMessage, error) {
	var root map[string]json.RawMessage
	if err := json.Unmarshal(raw, &root); err != nil {
		return nil, err
	}
	var units []json.RawMessage
	if err := json.Unmarshal(root["ownUnits"], &units); err != nil || len(units) == 0 {
		return nil, errors.New("scenario ownUnits unavailable")
	}
	humans := make([]int, 0, len(units))
	fields := make([]map[string]json.RawMessage, len(units))
	var rows []RawUnit
	for i, row := range units {
		fields[i] = map[string]json.RawMessage{}
		if err := json.Unmarshal(row, &fields[i]); err != nil {
			return nil, fmt.Errorf("ownUnits[%d]: %w", i, err)
		}
		var u RawUnit
		if err := json.Unmarshal(row, &u); err != nil {
			return nil, err
		}
		rows = append(rows, u)
		if u.Human != nil && *u.Human {
			humans = append(humans, i)
		}
	}
	if len(humans) == 0 {
		return nil, errors.New("scenario has no human unit")
	}
	choose := func(n int) int { return int(random() % uint64(n)) }
	encode := func() (json.RawMessage, error) {
		root["ownUnits"], _ = json.Marshal(units)
		return json.Marshal(root)
	}
	switch op {
	case "add-skill":
		type slot struct {
			unit, at int
			skill    int64
		}
		choices := []slot{}
		carried := map[int64]bool{}
		for _, i := range humans {
			for _, id := range rows[i].Skills {
				carried[id] = true
			}
		}
		team110 := 0
		for _, i := range humans {
			for _, id := range rows[i].Skills {
				if id == 110 {
					team110++
				}
			}
		}
		for _, id := range nativeSearchSkillIDs() {
			if carried[id] || id == 110 && team110 >= 2 {
				continue
			}
			for _, i := range humans {
				u := rows[i]
				if len(u.Skills) >= 9 || nativeFormationSkill(id) && hasFormation(u.Skills) || !nativeSkillFitsWeapon(p, u, id) {
					continue
				}
				for at := 0; at <= len(u.Skills); at++ {
					choices = append(choices, slot{i, at, id})
				}
			}
		}
		if len(choices) == 0 {
			return nil, errors.New("no approved skill can be added")
		}
		c := choices[choose(len(choices))]
		skills, levels := rows[c.unit].Skills, rows[c.unit].Invocation
		skills = append(skills, 0)
		copy(skills[c.at+1:], skills[c.at:])
		skills[c.at] = c.skill
		level := int64(choose(3))
		if nativeFormationSkill(c.skill) {
			level = 1
		}
		levels = append(levels, 0)
		copy(levels[c.at+1:], levels[c.at:])
		levels[c.at] = level
		fields[c.unit]["skills"], _ = json.Marshal(skills)
		fields[c.unit]["invocationLevels"], _ = json.Marshal(levels)
	case "remove-skill":
		type slot struct{ unit, at int }
		choices := []slot{}
		for _, i := range humans {
			for j := range rows[i].Skills {
				choices = append(choices, slot{i, j})
			}
		}
		if len(choices) == 0 {
			return nil, errors.New("no removable skill")
		}
		c := choices[choose(len(choices))]
		skills := append([]int64(nil), rows[c.unit].Skills...)
		levels := append([]int64(nil), rows[c.unit].Invocation...)
		skills = append(skills[:c.at], skills[c.at+1:]...)
		levels = append(levels[:c.at], levels[c.at+1:]...)
		fields[c.unit]["skills"], _ = json.Marshal(skills)
		fields[c.unit]["invocationLevels"], _ = json.Marshal(levels)
	case "replace-skill":
		type slot struct {
			unit, at int
			skill    int64
		}
		choices := []slot{}
		team110 := 0
		for _, i := range humans {
			for _, id := range rows[i].Skills {
				if id == 110 {
					team110++
				}
			}
		}
		for _, i := range humans {
			u := rows[i]
			present := map[int64]bool{}
			for _, id := range u.Skills {
				present[id] = true
			}
			for j := range u.Skills {
				for _, id := range nativeSearchSkillIDs() {
					if id == u.Skills[j] || present[id] || id == 110 && team110 >= 2 || nativeFormationSkill(id) && hasFormationExcept(u.Skills, j) || !nativeSkillFitsWeapon(p, u, id) {
						continue
					}
					choices = append(choices, slot{i, j, id})
				}
			}
		}
		if len(choices) == 0 {
			return nil, errors.New("no legal skill replacement")
		}
		c := choices[choose(len(choices))]
		skills := append([]int64(nil), rows[c.unit].Skills...)
		levels := append([]int64(nil), rows[c.unit].Invocation...)
		skills[c.at] = c.skill
		levels[c.at] = int64(choose(3))
		if nativeFormationSkill(c.skill) {
			levels[c.at] = 1
		}
		fields[c.unit]["skills"], _ = json.Marshal(skills)
		fields[c.unit]["invocationLevels"], _ = json.Marshal(levels)
	case "move-skill":
		type move struct{ unit, from, to int }
		choices := []move{}
		for _, i := range humans {
			for a := range rows[i].Skills {
				for b := range rows[i].Skills {
					if a != b {
						choices = append(choices, move{i, a, b})
					}
				}
			}
		}
		if len(choices) == 0 {
			return nil, errors.New("no skill order change available")
		}
		c := choices[choose(len(choices))]
		skills := append([]int64(nil), rows[c.unit].Skills...)
		levels := append([]int64(nil), rows[c.unit].Invocation...)
		skill, level := skills[c.from], levels[c.from]
		skills = append(skills[:c.from], skills[c.from+1:]...)
		levels = append(levels[:c.from], levels[c.from+1:]...)
		skills = append(skills, 0)
		copy(skills[c.to+1:], skills[c.to:])
		skills[c.to] = skill
		levels = append(levels, 0)
		copy(levels[c.to+1:], levels[c.to:])
		levels[c.to] = level
		fields[c.unit]["skills"], _ = json.Marshal(skills)
		fields[c.unit]["invocationLevels"], _ = json.Marshal(levels)
	case "set-trigger":
		type slot struct {
			unit, at int
			level    int64
		}
		choices := []slot{}
		for _, i := range humans {
			for j, id := range rows[i].Skills {
				if nativeFormationSkill(id) {
					continue
				}
				for level := int64(0); level <= 2; level++ {
					if rows[i].Invocation[j] != level {
						choices = append(choices, slot{i, j, level})
					}
				}
			}
		}
		if len(choices) == 0 {
			return nil, errors.New("no trigger change available")
		}
		c := choices[choose(len(choices))]
		levels := append([]int64(nil), rows[c.unit].Invocation...)
		levels[c.at] = c.level
		fields[c.unit]["invocationLevels"], _ = json.Marshal(levels)
	case "set-stat":
		return mutateNativeStat(root, units, fields, rows, humans, p, random)
	case "set-formation-skill":
		i := humans[choose(len(humans))]
		choices := []int64{0, 105, 106, 107}
		chosen := choices[choose(len(choices))]
		skills, levels := []int64{}, []int64{}
		if chosen != 0 {
			skills = append(skills, chosen)
			levels = append(levels, 1)
		}
		for j, id := range rows[i].Skills {
			if nativeFormationSkill(id) {
				continue
			}
			skills = append(skills, id)
			levels = append(levels, rows[i].Invocation[j])
		}
		fields[i]["skills"], _ = json.Marshal(skills)
		fields[i]["invocationLevels"], _ = json.Marshal(levels)
	case "set-weapon":
		return mutateNativeWeapon(root, units, fields, rows, humans, p, random)
	case "reorder-roster":
		if _, ok := root["prePlacement"]; ok {
			return nil, errors.New("explicit prePlacement fixes roster placement")
		}
		if len(units) < 2 {
			return nil, errors.New("roster has fewer than two units")
		}
		a := choose(len(units))
		b := choose(len(units) - 1)
		if b >= a {
			b++
		}
		before, err := nativePlacementSignature(units, p)
		if err != nil {
			return nil, err
		}
		units[a], units[b] = units[b], units[a]
		after, err := nativePlacementSignature(units, p)
		if err != nil {
			return nil, err
		}
		if equalStringSlice(before, after) {
			units[a], units[b] = units[b], units[a]
			return nil, errors.New("roster swap does not change canonical formation")
		}
	case "add-unit":
		if _, ok := root["prePlacement"]; ok {
			return nil, errors.New("explicit prePlacement cannot accept a new fighter")
		}
		var scenario RawScenario
		if err := json.Unmarshal(raw, &scenario); err != nil {
			return nil, err
		}
		enc := p.encounters[scenario.EncounterID]
		followers, _ := enc["followers"].([]any)
		if len(units)+1+len(followers)+1 > 32 {
			return nil, errors.New("native 32-fighter capacity would be exceeded")
		}
		source := humans[choose(len(humans))]
		var clone map[string]json.RawMessage
		if err := json.Unmarshal(units[source], &clone); err != nil {
			return nil, err
		}
		var name string
		for suffix := 1; ; suffix++ {
			name = fmt.Sprintf("Synthetic %d", suffix)
			used := false
			for _, u := range rows {
				if u.Name == name {
					used = true
					break
				}
			}
			if !used {
				break
			}
		}
		clone["name"], _ = json.Marshal(name)
		if countSkill(rows, 110) >= 2 {
			ss, lv := []int64{}, []int64{}
			for j, id := range rows[source].Skills {
				if id != 110 {
					ss = append(ss, id)
					lv = append(lv, rows[source].Invocation[j])
				}
			}
			clone["skills"], _ = json.Marshal(ss)
			clone["invocationLevels"], _ = json.Marshal(lv)
		}
		b, _ := json.Marshal(clone)
		units = append(units, b)
	case "remove-unit":
		if len(humans) <= 1 {
			return nil, errors.New("team must retain a human")
		}
		i := humans[choose(len(humans))]
		if profile, ok := root["prePlacement"]; ok {
			var placements map[string]json.RawMessage
			_ = json.Unmarshal(profile, &placements)
			if _, ok := placements[rows[i].Name]; ok {
				return nil, errors.New("explicit prePlacement cannot lose a fighter")
			}
		}
		units = append(units[:i], units[i+1:]...)
		if profile, ok := root["startProfile"]; ok {
			var fields map[string]json.RawMessage
			if json.Unmarshal(profile, &fields) == nil {
				var statuses map[string]json.RawMessage
				if json.Unmarshal(fields["startingStatus"], &statuses) == nil {
					delete(statuses, rows[i].Name)
					fields["startingStatus"], _ = json.Marshal(statuses)
					root["startProfile"], _ = json.Marshal(fields)
				}
			}
		}
	default:
		return nil, fmt.Errorf("unknown native mutation %q", op)
	}
	if op != "reorder-roster" && op != "add-unit" && op != "remove-unit" {
		for i := range fields {
			units[i] = marshalRawFields(fields[i])
		}
	}
	return encode()
}

func countSkill(units []RawUnit, skill int64) int {
	n := 0
	for _, u := range units {
		if u.Human != nil && *u.Human {
			for _, id := range u.Skills {
				if id == skill {
					n++
				}
			}
		}
	}
	return n
}
func nativeFormationSkill(id int64) bool { return id == 105 || id == 106 || id == 107 }
func hasFormation(skills []int64) bool {
	for _, id := range skills {
		if nativeFormationSkill(id) {
			return true
		}
	}
	return false
}
func hasFormationExcept(skills []int64, skip int) bool {
	for i, id := range skills {
		if i != skip && nativeFormationSkill(id) {
			return true
		}
	}
	return false
}

func nativeSkillFitsWeapon(p *Preparer, u RawUnit, skill int64) bool {
	row, ok := p.skills[skill]
	if !ok {
		return false
	}
	req, ok := asInt(row["requiredEquipType"])
	if !ok || req == -1 {
		return ok
	}
	if u.WeaponID == nil {
		return false
	}
	weapon, ok := p.equipment[*u.WeaponID]
	if !ok {
		return false
	}
	typ, ok := asInt(weapon["type"])
	return ok && typ == req
}

func validateNativeMutation(s RawScenario, p *Preparer) error {
	statBounds, err := loadNativeSearchStatBounds()
	if err != nil {
		return err
	}
	enc := p.encounters[s.EncounterID]
	followers, _ := enc["followers"].([]any)
	if len(s.OwnUnits)+len(followers)+1 > 32 {
		return errors.New("native 32-fighter capacity exceeded")
	}
	humans := 0
	for i, u := range s.OwnUnits {
		f, err := p.makeOwn(u)
		if err != nil {
			return fmt.Errorf("ownUnits[%d]: %w", i, err)
		}
		if !f.human {
			continue
		}
		humans++
		for _, pid := range nativeSearchStatParameters {
			if pid == 18 && !hasMagicAttack(u.Skills) {
				// The source contract treats physical INT as unsearchable and does
				// not constrain a preserved, unused value.
				continue
			}
			bound := statBounds[pid]
			effective, statErr := p.effective(f, pid, bound.Bounded)
			if statErr != nil {
				return fmt.Errorf("ownUnits[%d] parameter %d: %w", i, pid, statErr)
			}
			if effective < bound.Minimum || effective > bound.Maximum {
				return fmt.Errorf("ownUnits[%d] parameter %d effective value %d outside derived interval [%d, %d]", i, pid, effective, bound.Minimum, bound.Maximum)
			}
		}
		if len(u.Skills) > 9 {
			return fmt.Errorf("ownUnits[%d] exceeds nine-skill search contract", i)
		}
		seen := map[int64]bool{}
		formation := false
		for _, id := range u.Skills {
			if !nativeSearchSkills[id] {
				return fmt.Errorf("skill %d is outside the approved search set", id)
			}
			if seen[id] {
				return fmt.Errorf("duplicate skill %d", id)
			}
			seen[id] = true
			if nativeFormationSkill(id) {
				if formation {
					return errors.New("unit has multiple formation skills")
				}
				formation = true
			}
			if !nativeSkillFitsWeapon(p, u, id) {
				return fmt.Errorf("skill %d does not fit weapon", id)
			}
		}
	}
	if humans == 0 {
		return errors.New("search strategy must retain a human")
	}
	if countSkill(s.OwnUnits, 110) > 2 {
		return errors.New("7-Hit Attack team cap exceeded")
	}
	return nil
}

var nativeSearchStatParameters = []int64{10, 11, 13, 14, 15, 16, 18, 19}

func mutateNativeStat(root map[string]json.RawMessage, units []json.RawMessage, fields []map[string]json.RawMessage, rows []RawUnit, humans []int, p *Preparer, random func() uint64) (json.RawMessage, error) {
	type choice struct {
		unit        int
		pid, target int64
	}
	statBounds, err := loadNativeSearchStatBounds()
	if err != nil {
		return nil, err
	}
	choices := []choice{}
	for _, i := range humans {
		f, err := p.makeOwn(rows[i])
		if err != nil {
			continue
		}
		for _, pid := range nativeSearchStatParameters {
			if pid == 18 && !hasMagicAttack(rows[i].Skills) {
				continue
			}
			bound := statBounds[pid]
			current, err := p.effective(f, pid, bound.Bounded)
			if err != nil {
				continue
			}
			for _, target := range nativeStatTargets(current, bound.Minimum, bound.Maximum, random) {
				choices = append(choices, choice{i, pid, target})
			}
		}
	}
	if len(choices) == 0 {
		return nil, errors.New("no legal stat step inside the derived search-contract walls")
	}
	c := choices[int(random()%uint64(len(choices)))]
	if err := setNativeEffectiveStat(fields[c.unit], p, c.pid, c.target); err != nil {
		return nil, err
	}
	units[c.unit] = marshalRawFields(fields[c.unit])
	root["ownUnits"], _ = json.Marshal(units)
	return json.Marshal(root)
}

// nativeStatTargets mirrors search_contract._stat_targets: clamp the original
// proportional probes to the reviewed interval, include each wall, shuffle
// deterministically, and retain at most five targets for the stat axis.
func nativeStatTargets(current, minimum, maximum int64, random func() uint64) []int64 {
	factors := []float64{1.0, 1.15, 1.35, 1.6, 0.85, 0.7}
	targets := make([]int64, 0, len(factors)+2)
	add := func(target int64) {
		if target < minimum {
			target = minimum
		} else if target > maximum {
			target = maximum
		}
		if target == current {
			return
		}
		for _, existing := range targets {
			if existing == target {
				return
			}
		}
		targets = append(targets, target)
	}
	for _, factor := range factors {
		add(int64(math.RoundToEven(float64(current) * factor)))
	}
	add(minimum)
	add(maximum)
	for i := len(targets) - 1; i > 0; i-- {
		j := int(random() % uint64(i+1))
		targets[i], targets[j] = targets[j], targets[i]
	}
	if len(targets) > 5 {
		targets = targets[:5]
	}
	return targets
}

func hasMagicAttack(skills []int64) bool {
	for _, id := range skills {
		if id >= 5 && id <= 19 {
			return true
		}
	}
	return false
}

func setNativeEffectiveStat(unit map[string]json.RawMessage, p *Preparer, pid, target int64) error {
	var u RawUnit
	if err := json.Unmarshal(marshalRawFields(unit), &u); err != nil {
		return err
	}
	f, err := p.makeOwn(u)
	if err != nil {
		return err
	}
	if err := ValidateNativeSearchStatTarget(pid, target); err != nil {
		return err
	}
	contribution := int64(0)
	for _, eq := range f.equipment {
		contribution = i32(contribution + p.contribution(eq, pid, f.human))
	}
	var params map[string]map[string]int64
	if err := json.Unmarshal(u.Parameters, &params); err != nil {
		return err
	}
	row := params[fmt.Sprint(pid)]
	if row == nil {
		return fmt.Errorf("parameter %d missing", pid)
	}
	row["rawValue"] = target
	if pid == 10 || pid == 11 {
		row["extraValue"] = 0
		row["rawMax"] = target
		row["extraMax"] = i32(-contribution)
	} else {
		row["extraValue"] = i32(-contribution)
	}
	b, err := json.Marshal(params)
	if err != nil {
		return err
	}
	unit["parameters"] = b
	if err = json.Unmarshal(marshalRawFields(unit), &u); err != nil {
		return err
	}
	f, err = p.makeOwn(u)
	if err != nil {
		return err
	}
	actual, err := p.effective(f, pid, pid == 10 || pid == 11)
	if err != nil {
		return err
	}
	if actual != target {
		return fmt.Errorf("stat target %d reads back as %d", target, actual)
	}
	return nil
}

func marshalRawFields(v map[string]json.RawMessage) json.RawMessage {
	b, _ := json.Marshal(v)
	return b
}

func mutateNativeWeapon(root map[string]json.RawMessage, units []json.RawMessage, fields []map[string]json.RawMessage, rows []RawUnit, humans []int, p *Preparer, random func() uint64) (json.RawMessage, error) {
	type choice struct {
		unit int
		id   int64
	}
	choices := []choice{}
	weaponIDs := make([]int64, 0, len(p.equipment))
	for id := range p.equipment {
		weaponIDs = append(weaponIDs, id)
	}
	sort.Slice(weaponIDs, func(i, j int) bool { return weaponIDs[i] < weaponIDs[j] })
	for _, i := range humans {
		u := rows[i]
		if u.WeaponID == nil {
			continue
		}
		current := p.equipment[*u.WeaponID]
		for _, id := range weaponIDs {
			row := p.equipment[id]
			category, _ := asInt(row["category"])
			if category != 0 || weaponBehaviorEqual(current, row) {
				continue
			}
			valid := true
			for _, sid := range u.Skills {
				if !nativeSkillFitsWeaponRow(p, sid, row) {
					valid = false
					break
				}
			}
			if valid {
				choices = append(choices, choice{i, id})
			}
		}
	}
	if len(choices) == 0 {
		return nil, errors.New("no reachable alternate weapon behavior")
	}
	c := choices[int(random()%uint64(len(choices)))]
	i, u := c.unit, rows[c.unit]
	if u.WeaponID == nil {
		return nil, errors.New("selected weapon missing")
	}
	// Save effective stats before changing equipment, then compensate the new
	// weapon's exact recovered equipment contribution.
	before, err := p.makeOwn(u)
	if err != nil {
		return nil, err
	}
	targets := make([]struct{ pid, value int64 }, 0, 8)
	for _, pid := range []int64{10, 11, 13, 14, 15, 16, 18, 19} {
		v, e := p.effective(before, pid, pid == 10 || pid == 11)
		if e == nil {
			targets = append(targets, struct{ pid, value int64 }{pid, v})
		}
	}
	var equip []map[string]json.RawMessage
	if err := json.Unmarshal(u.Equipment, &equip); err != nil {
		return nil, err
	}
	level, affinity := int64(1), int64(0)
	kept := make([]map[string]json.RawMessage, 0, len(equip)+1)
	for _, slot := range equip {
		var id int64
		_ = json.Unmarshal(slot["id"], &id)
		if id == *u.WeaponID {
			_ = json.Unmarshal(slot["level"], &level)
			_ = json.Unmarshal(slot["affinity"], &affinity)
			continue
		}
		kept = append(kept, slot)
	}
	newSlot := map[string]json.RawMessage{}
	newSlot["id"], _ = json.Marshal(c.id)
	newSlot["level"], _ = json.Marshal(level)
	newSlot["affinity"], _ = json.Marshal(affinity)
	kept = append(kept, newSlot)
	fields[i]["equipment"], _ = json.Marshal(kept)
	fields[i]["weaponId"], _ = json.Marshal(c.id)
	for _, target := range targets {
		if err := setNativeEffectiveStat(fields[i], p, target.pid, target.value); err != nil {
			return nil, err
		}
	}
	units[i] = marshalRawFields(fields[i])
	root["ownUnits"], _ = json.Marshal(units)
	return json.Marshal(root)
}

func nativeSkillFitsWeaponRow(p *Preparer, skill int64, weapon map[string]any) bool {
	row, ok := p.skills[skill]
	if !ok {
		return false
	}
	req, ok := asInt(row["requiredEquipType"])
	if !ok || req == -1 {
		return ok
	}
	typ, ok := asInt(weapon["type"])
	return ok && req == typ
}
func weaponBehaviorEqual(a, b map[string]any) bool {
	for _, key := range []string{"type", "motion", "shootingRange", "projectileFlag", "category"} {
		if fmt.Sprint(a[key]) != fmt.Sprint(b[key]) {
			return false
		}
	}
	return true
}

func nativeFormationOrder(units []RawUnit, p *Preparer) ([]int, error) {
	members := make([]FormationMember, 0, len(units))
	for _, u := range units {
		f, err := p.makeOwn(u)
		if err != nil {
			return nil, err
		}
		members = append(members, f.member)
	}
	formation, err := PrepareFormation(members, 0, 1)
	if err != nil {
		return nil, err
	}
	return formation.Order, nil
}
func equalIntSlice(a, b []int) bool {
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

func nativePlacementSignature(units []json.RawMessage, p *Preparer) ([]string, error) {
	members := make([]FormationMember, len(units))
	contents := make([]string, len(units))
	for i, raw := range units {
		var u RawUnit
		if err := json.Unmarshal(raw, &u); err != nil {
			return nil, err
		}
		f, err := p.makeOwn(u)
		if err != nil {
			return nil, err
		}
		members[i] = f.member
		var fields map[string]json.RawMessage
		if err := json.Unmarshal(raw, &fields); err != nil {
			return nil, err
		}
		delete(fields, "name")
		contents[i] = digest(canonicalJSON(marshalRawFields(fields)))
	}
	formation, err := PrepareFormation(members, 0, 1)
	if err != nil {
		return nil, err
	}
	signature := make([]string, len(formation.Order))
	for grid, incoming := range formation.Order {
		signature[grid] = contents[incoming]
	}
	return signature, nil
}

func equalStringSlice(a, b []string) bool {
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
