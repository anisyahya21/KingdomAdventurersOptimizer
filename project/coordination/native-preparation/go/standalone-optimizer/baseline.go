package main

// Fixed-baseline scheduling evaluates every admitted genotype against an
// explicit, ordered bank of seed pairs. It has no mutation, score selection,
// or adaptive policy.
import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"sort"
)

const baselineSchema = "ka-go-fixed-baseline-1"

type baselineCandidate struct {
	ID  string          `json:"id"`
	Raw json.RawMessage `json:"raw"`
}

type baselineSnapshot struct {
	Schema     string              `json:"schema"`
	PolicyHash string              `json:"policyHash"`
	Candidates []baselineCandidate `json:"candidates"`
	SeedPairs  [][2]int64          `json:"seedPairs"`
	Next       uint64              `json:"next"`
	Pending    []string            `json:"pending"`
	Completed  []string            `json:"completed"`
}

type baselineTrial struct {
	ID             string
	CandidateID    string
	CandidateIndex int
	Pair           [2]int64
}

type Baseline struct {
	state     baselineSnapshot
	trials    []baselineTrial
	byID      map[string]int
	pending   map[string]bool
	completed map[string]bool
}

// NewBaseline admits and freezes raw scenarios, then creates an exhaustive
// candidate-major, seed-bank-minor trial sequence. Candidate identity ignores
// the two battle seeds; trial identity binds that genotype to one seed pair.
func NewBaseline(candidates []json.RawMessage, pairs [][2]int64) (*Baseline, error) {
	if len(candidates) == 0 {
		return nil, errors.New("fixed baseline requires at least one candidate")
	}
	if len(pairs) == 0 {
		return nil, errors.New("fixed baseline requires at least one seed pair")
	}
	b := &Baseline{byID: map[string]int{}, pending: map[string]bool{}, completed: map[string]bool{}}
	seenCandidates := map[string]bool{}
	for i, raw := range candidates {
		admitted, err := AdmitRawScenario(raw)
		if err != nil {
			return nil, fmt.Errorf("baseline candidate %d: %w", i, err)
		}
		frozen := append(json.RawMessage(nil), admitted.Raw...)
		id := strategyIdentity(frozen)
		if seenCandidates[id] {
			return nil, fmt.Errorf("duplicate baseline candidate genotype %s", id)
		}
		seenCandidates[id] = true
		b.state.Candidates = append(b.state.Candidates, baselineCandidate{ID: id, Raw: frozen})
	}
	seenPairs := map[[2]int64]bool{}
	for i, pair := range pairs {
		for _, seed := range pair {
			if seed < 0 || seed > math.MaxInt32 {
				return nil, fmt.Errorf("seed pair %d is outside nonnegative signed31-bit range", i)
			}
		}
		if seenPairs[pair] {
			return nil, fmt.Errorf("duplicate baseline seed pair %v", pair)
		}
		seenPairs[pair] = true
		b.state.SeedPairs = append(b.state.SeedPairs, pair)
	}
	if uint64(len(candidates)) > math.MaxUint64/uint64(len(pairs)) {
		return nil, errors.New("fixed baseline trial budget overflows uint64")
	}
	total := uint64(len(candidates)) * uint64(len(pairs))
	if total > uint64(maxInt()) {
		return nil, errors.New("fixed baseline trial bank exceeds addressable capacity")
	}
	b.trials = make([]baselineTrial, 0, int(total))
	for candidateIndex, candidate := range b.state.Candidates {
		for _, pair := range b.state.SeedPairs {
			trialID, err := baselineTrialID(candidate.ID, pair)
			if err != nil {
				return nil, err
			}
			if _, exists := b.byID[trialID]; exists {
				return nil, errors.New("duplicate baseline trial identity")
			}
			b.byID[trialID] = len(b.trials)
			b.trials = append(b.trials, baselineTrial{ID: trialID, CandidateID: candidate.ID, CandidateIndex: candidateIndex, Pair: pair})
		}
	}
	policy, err := json.Marshal(struct {
		Schema       string     `json:"schema"`
		Order        string     `json:"order"`
		CandidateIDs []string   `json:"candidateIds"`
		SeedPairs    [][2]int64 `json:"seedPairs"`
	}{baselineSchema, "candidate-input-order then explicit-seed-bank-order", baselineCandidateIDs(b.state.Candidates), b.state.SeedPairs})
	if err != nil {
		return nil, err
	}
	b.state.Schema = baselineSchema
	b.state.PolicyHash = digest(policy)
	return b, nil
}

func (b *Baseline) Next() (json.RawMessage, string, error) {
	if b == nil {
		return nil, "", errors.New("nil fixed baseline")
	}
	if b.state.Next >= uint64(len(b.trials)) {
		return nil, "", errors.New("fixed baseline budget exhausted")
	}
	t := b.trials[b.state.Next]
	b.state.Next++
	if b.completed[t.ID] || b.pending[t.ID] {
		return nil, "", errors.New("fixed baseline scheduler state contains a duplicate dispatch")
	}
	b.pending[t.ID] = true
	if t.CandidateIndex < 0 || t.CandidateIndex >= len(b.state.Candidates) || b.state.Candidates[t.CandidateIndex].ID != t.CandidateID {
		delete(b.pending, t.ID)
		return nil, "", errors.New("fixed baseline trial lost its candidate source")
	}
	raw, err := replaceScenarioSeeds(b.state.Candidates[t.CandidateIndex].Raw, t.Pair)
	if err != nil {
		delete(b.pending, t.ID)
		return nil, "", fmt.Errorf("fixed baseline seed override: %w", err)
	}
	return raw, t.ID, nil
}

// NeedsResult is true only for a dispatched trial whose feedback is not yet
// durable. Unknown, undispatched, and completed IDs are rejected by absence.
func (b *Baseline) NeedsResult(id string) bool { return b != nil && b.pending[id] && !b.completed[id] }

// ValidateTrialRaw binds a checkpoint's pending payload to the deterministic
// candidate and seed pair named by its trial ID. The ID alone is not enough:
// pending raw input is separately serialized in the run checkpoint.
func (b *Baseline) ValidateTrialRaw(id string, raw json.RawMessage) error {
	if b == nil {
		return errors.New("nil fixed baseline")
	}
	i, ok := b.byID[id]
	if !ok || !b.pending[id] || b.completed[id] {
		return errors.New("trial is not pending in fixed baseline")
	}
	t := b.trials[i]
	if t.CandidateIndex < 0 || t.CandidateIndex >= len(b.state.Candidates) || b.state.Candidates[t.CandidateIndex].ID != t.CandidateID {
		return errors.New("fixed baseline trial lost its candidate source")
	}
	want, err := replaceScenarioSeeds(b.state.Candidates[t.CandidateIndex].Raw, t.Pair)
	if err != nil {
		return err
	}
	if !bytes.Equal(bytes.TrimSpace(raw), bytes.TrimSpace(want)) {
		return errors.New("pending fixed baseline payload does not match its trial identity")
	}
	return nil
}

func (b *Baseline) Observe(id string) error {
	if b == nil {
		return errors.New("nil fixed baseline")
	}
	if _, ok := b.byID[id]; !ok {
		return errors.New("unknown fixed baseline trial identity")
	}
	if b.completed[id] {
		return errors.New("duplicate fixed baseline feedback")
	}
	if !b.pending[id] {
		return errors.New("fixed baseline result was not dispatched")
	}
	delete(b.pending, id)
	b.completed[id] = true
	return nil
}

func (b *Baseline) Snapshot() (json.RawMessage, error) {
	if b == nil {
		return nil, errors.New("nil fixed baseline")
	}
	state := b.state
	state.Pending = sortedBaselineSet(b.pending)
	state.Completed = sortedBaselineSet(b.completed)
	return json.Marshal(state)
}

func RestoreBaseline(raw json.RawMessage) (*Baseline, error) {
	if len(bytes.TrimSpace(raw)) == 0 || !json.Valid(raw) {
		return nil, errors.New("invalid fixed baseline snapshot")
	}
	var state baselineSnapshot
	if err := json.Unmarshal(raw, &state); err != nil {
		return nil, err
	}
	if state.Schema != baselineSchema || state.PolicyHash == "" || len(state.Candidates) == 0 || len(state.SeedPairs) == 0 {
		return nil, errors.New("unsupported or incomplete fixed baseline snapshot")
	}
	candidates := make([]json.RawMessage, len(state.Candidates))
	for i, c := range state.Candidates {
		if c.ID == "" || len(c.Raw) == 0 || !json.Valid(c.Raw) || strategyIdentity(c.Raw) != c.ID {
			return nil, fmt.Errorf("invalid frozen baseline candidate %d", i)
		}
		candidates[i] = append(json.RawMessage(nil), c.Raw...)
	}
	b, err := NewBaseline(candidates, state.SeedPairs)
	if err != nil {
		return nil, fmt.Errorf("restore fixed baseline inputs: %w", err)
	}
	if b.state.PolicyHash != state.PolicyHash {
		return nil, errors.New("fixed baseline policy hash mismatch")
	}
	if state.Next > uint64(len(b.trials)) {
		return nil, errors.New("fixed baseline cursor exceeds trial budget")
	}
	b.state.Next = state.Next
	for _, id := range state.Pending {
		i, ok := b.byID[id]
		if !ok || uint64(i) >= state.Next || b.pending[id] || b.completed[id] {
			return nil, errors.New("invalid pending baseline trial")
		}
		b.pending[id] = true
	}
	for _, id := range state.Completed {
		i, ok := b.byID[id]
		if !ok || uint64(i) >= state.Next || b.completed[id] || b.pending[id] {
			return nil, errors.New("invalid completed baseline trial")
		}
		b.completed[id] = true
	}
	if uint64(len(b.pending)+len(b.completed)) != state.Next {
		return nil, errors.New("baseline snapshot has unaccounted dispatched trials")
	}
	return b, nil
}

func (b *Baseline) Progress() (proposed, budget, feedback uint64) {
	if b == nil {
		return
	}
	return b.state.Next, uint64(len(b.trials)), uint64(len(b.completed))
}

func (b *Baseline) Lineage(id string) SearchLineage {
	if b == nil {
		return SearchLineage{}
	}
	i, ok := b.byID[id]
	if !ok {
		return SearchLineage{}
	}
	return SearchLineage{CandidateID: b.trials[i].CandidateID, Origin: "fixed-baseline"}
}

func (b *Baseline) PolicyHash() string {
	if b == nil {
		return ""
	}
	return b.state.PolicyHash
}

func replaceScenarioSeeds(raw json.RawMessage, pair [2]int64) (json.RawMessage, error) {
	var object map[string]json.RawMessage
	if err := json.Unmarshal(raw, &object); err != nil || object == nil {
		return nil, errors.New("scenario must be a JSON object")
	}
	object["mathSeed"], _ = json.Marshal(pair[0])
	object["libSeed"], _ = json.Marshal(pair[1])
	return json.Marshal(object)
}

func baselineTrialID(candidateID string, pair [2]int64) (string, error) {
	encoded, err := json.Marshal(struct {
		Candidate string `json:"candidate"`
		MathSeed  int64  `json:"mathSeed"`
		LibSeed   int64  `json:"libSeed"`
	}{candidateID, pair[0], pair[1]})
	if err != nil {
		return "", err
	}
	return "baseline:" + digest(encoded), nil
}

func baselineCandidateIDs(candidates []baselineCandidate) []string {
	ids := make([]string, len(candidates))
	for i, c := range candidates {
		ids[i] = c.ID
	}
	return ids
}
func sortedBaselineSet(set map[string]bool) []string {
	out := make([]string, 0, len(set))
	for id, yes := range set {
		if yes {
			out = append(out, id)
		}
	}
	sort.Strings(out)
	return out
}
func maxInt() int { return int(^uint(0) >> 1) }
