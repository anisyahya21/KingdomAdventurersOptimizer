package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"time"
)

var buildRevision = "unversioned"

type Workload struct {
	ExecutionScope
	Schema            string            `json:"schema"`
	Tables            json.RawMessage   `json:"tables"`
	Candidates        []json.RawMessage `json:"candidates"`
	FocusEncounters   []int64           `json:"focusEncounters,omitempty"`
	ControlPath       string            `json:"controlPath,omitempty"`
	KernelPath        string            `json:"kernelPath"`
	KernelSHA256      string            `json:"kernelSha256"`
	MechanicsIdentity json.RawMessage   `json:"mechanicsIdentity"`
	CatalogPath       string            `json:"catalogPath,omitempty"`
	FactsPath         string            `json:"factsPath,omitempty"`
}

type searchControl struct {
	FocusEncounterIDs []int64 `json:"focusEncounterIds,omitempty"`
	PauseRequested    bool    `json:"pauseRequested,omitempty"`
	StopRequested     bool    `json:"stopRequested,omitempty"`
}

func controlStopsAdmission(control searchControl) bool {
	return control.PauseRequested || control.StopRequested
}

type pendingEvaluation struct {
	ID  string          `json:"id"`
	Raw json.RawMessage `json:"raw"`
}

// workloadFocus returns the requested encounter subset. When the field is
// omitted or empty, every encounter represented by this workload's candidate
// pool is active; imported pools outside that set remain learned but inactive.
func workloadFocus(w Workload) ([]int64, error) {
	if len(w.FocusEncounters) != 0 {
		return append([]int64(nil), w.FocusEncounters...), nil
	}
	seen := make(map[int64]bool)
	ids := make([]int64, 0)
	for i, raw := range w.Candidates {
		candidate, err := AdmitRawScenario(raw)
		if err != nil {
			return nil, fmt.Errorf("workload candidate %d focus admission: %w", i, err)
		}
		if !seen[candidate.EncounterID] {
			seen[candidate.EncounterID] = true
			ids = append(ids, candidate.EncounterID)
		}
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
	return ids, nil
}

type RunState struct {
	Schema          string              `json:"schema"`
	WorkloadSHA256  string              `json:"workloadSha256"`
	Config          SearchConfig        `json:"config"`
	BatchSize       int                 `json:"batchSize"`
	Search          json.RawMessage     `json:"search"`
	Pending         []pendingEvaluation `json:"pending"`
	Completed       uint64              `json:"completed"`
	Rejected        uint64              `json:"rejected"`
	Errors          uint64              `json:"errors"`
	BuildRevision   string              `json:"buildRevision"`
	DuplicateSkips  uint64              `json:"duplicateSkips"`
	DiagnosticSaved uint64              `json:"diagnosticSaved"`
	DispatchStart   uint64              `json:"dispatchStart"`
	DispatchBudget  uint64              `json:"dispatchBudget"`
}
type RunStatus struct {
	Stage                        string            `json:"stage"`
	Completed                    uint64            `json:"completed"`
	Total                        uint64            `json:"total"`
	Rejected                     uint64            `json:"rejected"`
	Errors                       uint64            `json:"errors"`
	Durable                      uint64            `json:"durable"`
	ElapsedSeconds               float64           `json:"elapsedSeconds"`
	Detail                       string            `json:"detail,omitempty"`
	Output                       string            `json:"output"`
	DuplicateSkips               uint64            `json:"duplicateSkips"`
	DurablyCompleted             uint64            `json:"durablyCompleted"`
	DurablyCompletedThisProcess  uint64            `json:"durablyCompletedThisProcess"`
	ValidDurable                 uint64            `json:"validDurable"`
	DiagnosticSaved              uint64            `json:"diagnosticSaved"`
	UpdatedUTC                   string            `json:"updatedUtc"`
	AcceptedWork                 uint64            `json:"acceptedWork"`
	NativeCompleted              uint64            `json:"nativeCompleted"`
	NativeBattleCalls            uint64            `json:"nativeBattleCalls"`
	NativeSuccessfulBattles      uint64            `json:"nativeSuccessfulBattles"`
	NativeInFlight               uint64            `json:"nativeInFlight"`
	ReadyQueueDepth              uint64            `json:"readyQueueDepth"`
	DispatchPending              uint64            `json:"dispatchPending"`
	ExecutingWorkers             uint64            `json:"executingWorkers"`
	CompletedUnharvested         uint64            `json:"completedUnharvested"`
	NativeWorkerBusySeconds      float64           `json:"nativeWorkerBusySeconds"`
	NativeCallSeconds            float64           `json:"nativeCallSeconds"`
	NativeBatchesCompleted       uint64            `json:"nativeBatchesCompleted"`
	NativeBatchTotal             uint64            `json:"nativeBatchTotal"`
	NativeStageNanoseconds       map[string]uint64 `json:"nativeStageNanoseconds"`
	NativeStageSamples           map[string]uint64 `json:"nativeStageSamples"`
	PreparationNanoseconds       uint64            `json:"preparationNanoseconds"`
	PreparationSamples           uint64            `json:"preparationSamples"`
	DurableWriteNanoseconds      uint64            `json:"durableWriteNanoseconds"`
	DurableWriteSamples          uint64            `json:"durableWriteSamples"`
	NativeBattlesPerSecond       float64           `json:"nativeBattlesPerSecond"`
	NativeBattlesPerHour         float64           `json:"nativeBattlesPerHour"`
	NativeWindowSeconds          float64           `json:"nativeWindowSeconds"`
	NativeWindowBattlesPerSecond float64           `json:"nativeWindowBattlesPerSecond"`
	NativeWindowBattlesPerHour   float64           `json:"nativeWindowBattlesPerHour"`
	DurableBattlesPerSecond      float64           `json:"durableBattlesPerSecond"`
	DurableBattlesPerHour        float64           `json:"durableBattlesPerHour"`
	PendingDurableResults        uint64            `json:"pendingDurableResults"`
	MeanWorkerJobSeconds         float64           `json:"meanWorkerJobSeconds"`
	CheckpointNanoseconds        uint64            `json:"checkpointNanoseconds"`
	CheckpointSamples            uint64            `json:"checkpointSamples"`
	LeaderboardExportNanoseconds uint64            `json:"leaderboardExportNanoseconds"`
}

func jsonValue(v any) json.RawMessage {
	b, e := json.Marshal(v)
	if e != nil {
		panic(e)
	}
	return b
}
func writeJSON(path string, v any) error {
	// Search snapshots and exports are machine-readable artifacts. Avoid
	// repeatedly expanding multi-megabyte raw scenarios with indentation.
	b, e := json.Marshal(v)
	if e != nil {
		return e
	}
	f, e := os.CreateTemp(filepath.Dir(path), ".write-*")
	if e != nil {
		return e
	}
	tmp := f.Name()
	defer os.Remove(tmp)
	if _, e = f.Write(b); e != nil {
		f.Close()
		return e
	}
	if e = f.Sync(); e != nil {
		f.Close()
		return e
	}
	if e = f.Close(); e != nil {
		return e
	}
	return os.Rename(tmp, path)
}
func main() {
	if e := run(); e != nil {
		fmt.Fprintln(os.Stderr, e)
		os.Exit(1)
	}
}
func run() error {
	entryStarted := time.Now()
	input := flag.String("input", "", "raw workload JSON")
	output := flag.String("output", "", "durable output directory")
	mode := flag.String("mode", "search", "search, evaluate (exact ordered seed bank), baseline-bank or prepare")
	executors := flag.Int("executors", 5, "resident native execution workers")
	batch := flag.Int("batch", 64, "fixed adaptive feedback batch independent of executor count")
	budget := flag.Uint64("budget", 0, "maximum fresh search attempts for this output; zero is unbounded")
	seed := flag.Uint64("seed", 1, "deterministic search seed")
	exploration := flag.Float64("exploration", 0.7, "adaptive UCB exploration coefficient")
	population := flag.Int("population", 1024, "maximum active native strategies per encounter")
	searchState := flag.String("search-state", "", "adaptive search snapshot from a prior drained wave")
	request := flag.String("request", "", "explicit native proposal request JSON")
	seedBank := flag.String("seed-bank", "", "baseline-bank mode: JSON array of [mathSeed, libSeed] pairs")
	syncBatch := flag.Int("sync-batch", 1, "baseline-bank mode: records per durable journal File.Sync")
	policy := flag.String("policy", "search-contract", "search policy: search-contract or fixed-formation")
	flag.Parse()
	if *input == "" || *output == "" {
		return errors.New("--input and --output required")
	}
	if *batch < 1 || *batch > 4096 || *executors < 1 || *executors > 256 {
		return errors.New("invalid batch or executor count")
	}
	if *mode == "propose" {
		return runProposals(*input, *output, *request)
	}
	if *mode == "replay" {
		return runReplay(*input, *output, *executors)
	}
	if *mode == "baseline-bank" || *mode == "evaluate" {
		return runBaselineComparison(*input, *output, *seedBank, *executors, *batch, *syncBatch, entryStarted)
	}
	if *seedBank != "" || *syncBatch != 1 {
		return errors.New("--seed-bank and nondefault --sync-batch require --mode baseline-bank")
	}
	if e := os.MkdirAll(*output, 0700); e != nil {
		return e
	}
	data, e := os.ReadFile(*input)
	if e != nil {
		return e
	}
	var w Workload
	if e = json.Unmarshal(data, &w); e != nil {
		return e
	}
	if w.Schema != "ka-go-workload-1" || len(w.Candidates) == 0 || len(w.MechanicsIdentity) == 0 {
		return errors.New("workload schema, raw candidates and mechanicsIdentity required")
	}
	p, e := NewPreparer(w.Tables)
	if e != nil {
		return e
	}
	if *mode == "prepare" {
		// This mode is executed only by the sole test coordinator.
		out := make([]map[string]any, 0, len(w.Candidates))
		for _, raw := range w.Candidates {
			snapshot, err := p.Prepare(raw)
			row := map[string]any{"raw": raw, "snapshot": snapshot}
			if err != nil {
				row["error"] = err.Error()
			}
			out = append(out, row)
		}
		return writeJSON(filepath.Join(*output, "prepared.json"), out)
	}
	if *mode != "search" {
		return errors.New("unsupported mode")
	}
	if *policy != "search-contract" && *policy != "fixed-formation" {
		return errors.New("--policy must be search-contract or fixed-formation")
	}
	store, e := NewStore(*output)
	if e != nil {
		return e
	}
	defer store.Close()
	config := SearchConfig{Seed: *seed, Budget: 0, Exploration: *exploration, PopulationLimit: *population, FixedFormation: *policy == "fixed-formation"}
	state := RunState{Schema: "ka-go-run-state-2", WorkloadSHA256: digest(data), Config: config, BatchSize: *batch, BuildRevision: buildRevision, DispatchBudget: *budget}
	var search *Search
	cp, e := store.LoadCheckpoint()
	if e != nil && !errors.Is(e, os.ErrNotExist) {
		return e
	}
	if len(cp) > 0 {
		if e = json.Unmarshal(cp, &state); e != nil {
			return e
		}
		if state.Schema != "ka-go-run-state-2" || state.WorkloadSHA256 != digest(data) || state.Config != config || state.BatchSize != *batch || state.BuildRevision != buildRevision || state.DispatchBudget != *budget {
			return errors.New("resume workload/search/batch identity mismatch")
		}
		search, e = RestoreSearchWithTables(state.Search, w.Tables)
	} else {
		if store.Count() != 0 {
			return errors.New("nonempty journal without search checkpoint")
		}
		if *searchState != "" {
			var imported json.RawMessage
			imported, e = os.ReadFile(*searchState)
			if e == nil {
				search, e = RestoreSearchWithTables(imported, w.Tables)
				if e == nil {
					e = search.RequireConfig(config)
				}
				if e == nil {
					proposed, _, feedback, _ := search.Progress()
					if proposed != feedback {
						e = errors.New("imported search state must be drained; resume pending work in its original output directory")
					}
					if e == nil {
						e = search.MergeSeeds(w.Candidates)
					}
				}
			}
		} else {
			search, e = NewSearchWithTables(w.Candidates, config, w.Tables)
		}
		if e == nil {
			state.DispatchStart, _, _, _ = search.Progress()
		}
	}
	if e != nil {
		return e
	}
	focusEncounters, e := workloadFocus(w)
	if e != nil {
		return e
	}
	if e = search.SetFocus(focusEncounters); e != nil {
		return e
	}
	controlPath := w.ControlPath
	if controlPath != "" && !filepath.IsAbs(controlPath) {
		controlPath = filepath.Join(filepath.Dir(*input), controlPath)
	}
	activeFocus := append([]int64(nil), focusEncounters...)
	activeFocusSet := make(map[int64]bool, len(activeFocus))
	for _, id := range activeFocus {
		activeFocusSet[id] = true
	}
	pollControl := func() (searchControl, error) {
		current := searchControl{FocusEncounterIDs: append([]int64(nil), activeFocus...)}
		if controlPath != "" {
			b, readErr := os.ReadFile(controlPath)
			if readErr != nil && !errors.Is(readErr, os.ErrNotExist) {
				return current, fmt.Errorf("read search control %s: %w", controlPath, readErr)
			}
			if readErr == nil {
				if err := json.Unmarshal(b, &current); err != nil {
					return current, fmt.Errorf("decode search control %s: %w", controlPath, err)
				}
			}
		}
		if _, stopErr := os.Stat(filepath.Join(*output, "stop.request")); stopErr == nil {
			current.StopRequested = true
		} else if !errors.Is(stopErr, os.ErrNotExist) {
			return current, fmt.Errorf("read legacy stop.request: %w", stopErr)
		}
		if len(current.FocusEncounterIDs) == 0 {
			current.FocusEncounterIDs = append([]int64(nil), focusEncounters...)
		}
		changed := len(current.FocusEncounterIDs) != len(activeFocus)
		if !changed {
			for i := range activeFocus {
				if activeFocus[i] != current.FocusEncounterIDs[i] {
					changed = true
					break
				}
			}
		}
		if changed {
			if err := search.SetFocus(current.FocusEncounterIDs); err != nil {
				return current, fmt.Errorf("apply live search focus: %w", err)
			}
			activeFocus = append(activeFocus[:0], current.FocusEncounterIDs...)
			activeFocusSet = make(map[int64]bool, len(activeFocus))
			for _, id := range activeFocus {
				activeFocusSet[id] = true
			}
		}
		return current, nil
	}
	initialDurablyCompleted := state.Completed
	var checkpointNanoseconds, checkpointSamples uint64
	var leaderboardExportNanoseconds uint64
	checkpoint := func() error {
		checkpointStart := time.Now()
		defer func() {
			checkpointNanoseconds += uint64(time.Since(checkpointStart).Nanoseconds())
			checkpointSamples++
		}()
		pending := make([]pendingEvaluation, 0, len(state.Pending))
		for _, item := range state.Pending {
			if search.NeedsResult(item.ID) {
				pending = append(pending, item)
			}
		}
		state.Pending = pending
		state.Search, e = search.Snapshot()
		if e != nil {
			return e
		}
		if err := store.SaveCheckpoint(jsonValue(state)); err != nil {
			return err
		}
		// The checkpoint contains every reserved evaluation before admission.
		// The portable learning export is consumed between drained waves, so
		// publish it once after feedback rather than also before each batch.
		if len(state.Pending) != 0 {
			return nil
		}
		return writeJSON(filepath.Join(*output, "search-state.json"), state.Search)
	}
	start := time.Now()
	var engine *RawWindowsEngine
	var nativeBatchTotal uint64
	var nativeBatchCompletedBase uint64
	var preparationNanoseconds uint64
	var preparationSamples uint64
	var durableWriteNanoseconds uint64
	var durableWriteSamples uint64
	var pendingDurableResults uint64
	type rateSample struct {
		at      time.Time
		battles uint64
	}
	rateSamples := make([]rateSample, 0, 24)
	currentStage, currentDetail := "preparing", ""
	lastStatusWrite := time.Time{}
	status := func(stage, detail string) {
		currentStage, currentDetail = stage, detail
		elapsed := time.Since(start).Seconds()
		progress := NativeEngineProgress{}
		if engine != nil {
			progress = engine.Progress()
		}
		completedThisBatch := progress.Completed - nativeBatchCompletedBase
		if completedThisBatch > nativeBatchTotal {
			completedThisBatch = nativeBatchTotal
		}
		meanWorkerJobSeconds := 0.0
		if progress.Completed > 0 {
			meanWorkerJobSeconds = progress.WorkerBusySeconds / float64(progress.Completed)
		}
		nativeRate := 0.0
		durableRate := 0.0
		if elapsed > 0 {
			nativeRate = float64(progress.SuccessfulBattles) / elapsed
			durableRate = float64(state.Completed-initialDurablyCompleted) / elapsed
		}
		now := time.Now()
		rateSamples = append(rateSamples, rateSample{at: now, battles: progress.SuccessfulBattles})
		if len(rateSamples) > 64 {
			rateSamples = append([]rateSample(nil), rateSamples[len(rateSamples)-64:]...)
		}
		cutoff := now.Add(-10 * time.Second)
		firstInWindow := 0
		for firstInWindow < len(rateSamples)-1 && rateSamples[firstInWindow].at.Before(cutoff) {
			firstInWindow++
		}
		if firstInWindow > 0 {
			rateSamples = append([]rateSample(nil), rateSamples[firstInWindow:]...)
		}
		windowSeconds := 0.0
		windowRate := 0.0
		if len(rateSamples) > 1 {
			windowSeconds = now.Sub(rateSamples[0].at).Seconds()
			if windowSeconds > 0 {
				windowRate = float64(progress.SuccessfulBattles-rateSamples[0].battles) / windowSeconds
			}
		}
		_ = writeJSON(filepath.Join(*output, "status.json"), RunStatus{
			Stage: stage, Completed: state.Completed + state.Rejected + state.DuplicateSkips, Total: *budget,
			Rejected: state.Rejected, Errors: state.Errors, Durable: store.Count(), ElapsedSeconds: elapsed,
			Detail: detail, Output: *output, DuplicateSkips: state.DuplicateSkips,
			DurablyCompleted: state.Completed, ValidDurable: state.Completed - state.DiagnosticSaved,
			DurablyCompletedThisProcess: state.Completed - initialDurablyCompleted,
			DiagnosticSaved:             state.DiagnosticSaved, UpdatedUTC: time.Now().UTC().Format(time.RFC3339Nano),
			AcceptedWork: progress.Accepted, NativeCompleted: progress.Completed, NativeBattleCalls: progress.BattleCalls,
			NativeSuccessfulBattles: progress.SuccessfulBattles,
			NativeInFlight:          progress.InFlight, ReadyQueueDepth: progress.ReadyQueueDepth,
			DispatchPending:  progress.DispatchPending,
			ExecutingWorkers: progress.ExecutingWorkers, CompletedUnharvested: progress.CompletedUnharvested,
			NativeWorkerBusySeconds: progress.WorkerBusySeconds, NativeCallSeconds: progress.NativeCallSeconds,
			NativeBatchesCompleted: completedThisBatch, NativeBatchTotal: nativeBatchTotal,
			NativeStageNanoseconds: progress.StageNanoseconds, NativeStageSamples: progress.StageSamples,
			PreparationNanoseconds: preparationNanoseconds, PreparationSamples: preparationSamples,
			DurableWriteNanoseconds: durableWriteNanoseconds, DurableWriteSamples: durableWriteSamples,
			NativeBattlesPerSecond: nativeRate, NativeBattlesPerHour: nativeRate * 3600,
			NativeWindowSeconds: windowSeconds, NativeWindowBattlesPerSecond: windowRate,
			NativeWindowBattlesPerHour: windowRate * 3600,
			DurableBattlesPerSecond:    durableRate, DurableBattlesPerHour: durableRate * 3600,
			PendingDurableResults: pendingDurableResults,
			MeanWorkerJobSeconds:  meanWorkerJobSeconds,
			CheckpointNanoseconds: checkpointNanoseconds, CheckpointSamples: checkpointSamples,
			LeaderboardExportNanoseconds: leaderboardExportNanoseconds,
		})
		lastStatusWrite = time.Now()
	}
	statusPulse := func(stage, detail string) {
		if time.Since(lastStatusWrite) >= 500*time.Millisecond {
			status(stage, detail)
		}
	}

	executablePath, e := os.Executable()
	if e != nil {
		return e
	}
	executableBytes, e := os.ReadFile(executablePath)
	if e != nil {
		return e
	}
	productionIdentity, e := workloadProductionIdentity(w, digest(executableBytes), "search")
	if e != nil {
		return e
	}
	buildIdentity := jsonValue(map[string]any{"language": "Go", "sourceRevision": buildRevision, "executableSHA256": digest(executableBytes)})
	engine, e = NewRawWindowsEngine(w.KernelPath, w.KernelSHA256, *executors)
	if e != nil {
		status("failed", e.Error())
		return e
	}
	defer engine.Close()
	engine.SetProgressCallback(func(_ NativeEngineProgress) {
		if time.Since(lastStatusWrite) >= 500*time.Millisecond {
			status(currentStage, currentDetail)
		}
	})
	if e = checkpoint(); e != nil {
		return e
	}
	records := map[string]Record{}
	for _, r := range store.Records() {
		records[r.ID] = r
	}
	feedback := func(r BattleResult) error {
		if r.EarnedValid && r.Earned != nil {
			return search.Observe(r.ID, float64(*r.Earned))
		}
		return search.Unscored(r.ID)
	}
	preservePending := func() error {
		for _, item := range state.Pending {
			if record, exists := records[item.ID]; exists {
				var result BattleResult
				if err := json.Unmarshal(record.Result, &result); err != nil {
					return err
				}
				if err := feedback(result); err != nil {
					return err
				}
				state.Completed++
				if record.Diagnostic {
					state.DiagnosticSaved++
				}
				continue
			}
			if search.NeedsResult(item.ID) {
				if err := search.Defer(item.ID, item.Raw); err != nil {
					return err
				}
			}
		}
		return checkpoint()
	}
	engineMetadata := func() map[string]any {
		p := engine.Provenance()
		return map[string]any{"kernelPath": p.KernelPath, "kernelSha256": p.KernelSHA256, "nativeBattleSize": p.NativeBattleSize, "nativeReportSize": p.NativeReportSize, "encounterReportVersion": p.EncounterVersion, "encounterReportSize": p.EncounterSize}
	}
	makeRecord := func(id string, raw, snapshot json.RawMessage, mathSeed, libSeed int32, prov json.RawMessage) Record {
		var intent struct {
			FinishPolicy string `json:"finishPolicy"`
			TickLimit    int64  `json:"tickLimit"`
		}
		_ = json.Unmarshal(raw, &intent)
		var run struct {
			FollowerDraws uint32 `json:"followerDraws"`
		}
		_ = json.Unmarshal(prov, &run)
		policyCode := int32(0)
		if intent.FinishPolicy == "after-ending" {
			policyCode = 1
		} else if intent.FinishPolicy == "on-verdict" {
			policyCode = 2
		}
		lineage := search.Lineage(id)
		return Record{
			Schema: recordSchema, ID: id, Intent: raw, Candidate: raw, Prepared: snapshot,
			Seeds:   map[string]int64{"mathSeed": int64(mathSeed), "libSeed": int64(libSeed)},
			Lineage: jsonValue(lineage),
			Provenance: map[string]json.RawMessage{
				"executionScope":     jsonValue(NormalizeExecutionScope(w.ExecutionScope)),
				"productionIdentity": jsonValue(productionIdentity),
				"engine":             jsonValue(engineMetadata()),
				"execution": jsonValue(map[string]any{
					"snapshotSha256": digest(snapshot), "tickLimit": intent.TickLimit, "policyCode": policyCode,
					"finishPolicy": intent.FinishPolicy, "followerDraws": run.FollowerDraws,
				}),
				"mechanics": w.MechanicsIdentity, "policy": jsonValue(search.PolicyHash()), "run": prov,
			},
			Verified: false, Diagnostic: intent.FinishPolicy == "on-verdict",
		}
	}
	for {
		controlNow, controlErr := pollControl()
		if controlErr != nil {
			if e = preservePending(); e != nil {
				return e
			}
			status("failed", controlErr.Error())
			return controlErr
		}
		if controlStopsAdmission(controlNow) {
			if e = preservePending(); e != nil {
				return e
			}
			status("paused", "controlPath requested pause/stop; no new native admission")
			return nil
		}
		if len(state.Pending) == 0 {
			for len(state.Pending) < *batch {
				controlNow, controlErr = pollControl()
				if controlErr != nil {
					status("failed", controlErr.Error())
					return controlErr
				}
				if controlStopsAdmission(controlNow) {
					break
				}
				spent := state.Completed + state.Rejected + state.DuplicateSkips
				pendingFresh := uint64(0)
				for _, queued := range state.Pending {
					if search.Lineage(queued.ID).DeferredFrom == "" {
						pendingFresh++
					}
				}
				if *budget != 0 && spent+pendingFresh >= *budget && !search.HasDeferredInFocus() {
					break
				}
				raw, id, err := search.Next()
				statusPulse("proposing", "")
				if err != nil {
					state.Rejected++
					if e = preservePending(); e != nil {
						return e
					}
					status("failed", err.Error())
					return err
				}
				state.Pending = append(state.Pending, pendingEvaluation{id, raw})
			}
			if len(state.Pending) == 0 {
				if controlStopsAdmission(controlNow) {
					if e = checkpoint(); e != nil {
						return e
					}
					status("paused", "controlPath requested pause/stop; no new native admission")
					return nil
				}
				break
			}
			if e = checkpoint(); e != nil {
				return e
			}
		}
		status("preparing", "")
		tasks := []PreparedTask{}
		prepared := map[string]json.RawMessage{}
		identityDonors := map[string]string{}
		aliases := map[string]string{}
		controlBlocked := false
		var controlFailure error
		for _, item := range state.Pending {
			if r, exists := records[item.ID]; exists {
				var result BattleResult
				if e = json.Unmarshal(r.Result, &result); e != nil {
					return e
				}
				if e = feedback(result); e != nil {
					return e
				}
				state.Completed++
				if r.Diagnostic {
					state.DiagnosticSaved++
				}
				continue
			}
			controlNow, pollErr := pollControl()
			if pollErr != nil {
				controlFailure = pollErr
				controlBlocked = true
				break
			}
			if controlStopsAdmission(controlNow) {
				controlBlocked = true
				break
			}
			rawScenario, err := AdmitRawScenario(item.Raw)
			if err != nil {
				return err
			}
			if !activeFocusSet[rawScenario.EncounterID] {
				if search.NeedsResult(item.ID) {
					if err = search.Defer(item.ID, item.Raw); err != nil {
						return err
					}
				}
				continue
			}
			prepareStarted := time.Now()
			snapshot, err := p.Prepare(item.Raw)
			preparationNanoseconds += uint64(time.Since(prepareStarted).Nanoseconds())
			preparationSamples++
			statusPulse("preparing", item.ID)
			if err != nil {
				reason := err.Error()
				state.Rejected++
				if err = search.Reject(item.ID); err != nil {
					return err
				}
				f, err := os.OpenFile(filepath.Join(*output, "rejections.jsonl"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
				if err != nil {
					return err
				}
				_, err = f.Write(append(jsonValue(map[string]any{"id": item.ID, "raw": item.Raw, "reason": reason}), '\n'))
				if err == nil {
					err = f.Sync()
				}
				closeErr := f.Close()
				if err != nil {
					return err
				}
				if closeErr != nil {
					return closeErr
				}
				continue
			}
			policy := int32(0)
			if rawScenario.FinishPolicy == "after-ending" {
				policy = 1
			}
			if rawScenario.FinishPolicy == "on-verdict" {
				policy = 2
			}
			followerDraws, err := p.FollowerDraws(item.Raw)
			if err != nil {
				return err
			}
			provenance := jsonValue(map[string]any{"mathSeed": rawScenario.MathSeed, "libSeed": rawScenario.LibSeed, "build": buildIdentity, "workloadSha256": state.WorkloadSHA256, "tablesSha256": digest(w.Tables), "policyHash": search.PolicyHash(), "searchConfig": config, "batchSize": *batch, "mechanics": w.MechanicsIdentity, "preparation": "go-raw-v1", "simulation": "shared canonical Rust ka_kernel DLL", "followerDraws": followerDraws})
			skeleton := makeRecord(item.ID, item.Raw, snapshot, int32(rawScenario.MathSeed), int32(rawScenario.LibSeed), provenance)
			key := IdentityHash(skeleton)
			if _, ok := store.FindIdentity(key); ok {
				if e = search.Unscored(item.ID); e != nil {
					return e
				}
				state.DuplicateSkips++
				continue
			}
			if donor, ok := identityDonors[key]; ok {
				aliases[item.ID] = donor
				continue
			}
			identityDonors[key] = item.ID
			tasks = append(tasks, PreparedTask{Task: Task{ID: item.ID, MathSeed: int32(rawScenario.MathSeed), LibSeed: int32(rawScenario.LibSeed), Provenance: provenance}, Snapshot: snapshot, TickLimit: uint32(rawScenario.TickLimit), PolicyCode: policy, FollowerDraws: followerDraws, MeasureTiming: true})
			prepared[item.ID] = snapshot
		}
		beforeBatch := engine.Progress()
		nativeBatchCompletedBase = beforeBatch.Completed
		nativeBatchTotal = uint64(len(tasks))
		status("executing", "")
		var results []BattleResult
		var err error
		if len(tasks) > 0 {
			plannedRaw := make(map[string]json.RawMessage, len(state.Pending))
			for _, item := range state.Pending {
				plannedRaw[item.ID] = item.Raw
			}
			engine.SetAdmissionControl(func(task PreparedTask) (bool, error) {
				if controlFailure != nil {
					return false, controlFailure
				}
				if controlBlocked {
					return false, nil
				}
				controlNow, pollErr := pollControl()
				if pollErr != nil {
					controlFailure = pollErr
					return false, pollErr
				}
				if controlStopsAdmission(controlNow) {
					controlBlocked = true
					return false, nil
				}
				raw, ok := plannedRaw[task.ID]
				if !ok {
					return false, fmt.Errorf("native task %s has no planned raw intent", task.ID)
				}
				scenario, admitErr := AdmitRawScenario(raw)
				if admitErr != nil {
					return false, fmt.Errorf("native task %s intent: %w", task.ID, admitErr)
				}
				return activeFocusSet[scenario.EncounterID], nil
			})
			results, err = engine.RunPreparedBatch(tasks)
			engine.SetAdmissionControl(nil)
		}
		batchError := err
		if batchError == nil && controlFailure != nil {
			batchError = controlFailure
		}
		pendingDurableResults = 0
		for _, result := range results {
			if result.Completed {
				pendingDurableResults++
			}
		}
		statusPulse("persisting", "")
		byID := map[string]json.RawMessage{}
		returnedIDs := map[string]bool{}
		for _, item := range state.Pending {
			byID[item.ID] = item.Raw
		}
		for _, result := range results {
			returnedIDs[result.ID] = true
			if !result.Completed {
				state.Errors++
				if batchError == nil {
					batchError = fmt.Errorf("native evaluation %s status %d: %s", result.ID, result.Status, result.Error)
				}
				if e = writeJSON(filepath.Join(*output, "failure-"+digest([]byte(result.ID))+".json"), result); e != nil {
					return e
				}
				continue
			}
			// Results stay unverified until coordinator parity approval; they are valid
			// locally completed observations and remain separate from production library.
			r := makeRecord(result.ID, byID[result.ID], prepared[result.ID], result.MathSeed, result.LibSeed, result.Provenance)
			applyProductionScope(&r, &result, w, productionIdentity)
			r.Result = jsonValue(result)
			durableWriteStarted := time.Now()
			if e = store.Save(r); e != nil {
				return e
			}
			durableWriteNanoseconds += uint64(time.Since(durableWriteStarted).Nanoseconds())
			durableWriteSamples++
			records[r.ID] = r
			if e = feedback(result); e != nil {
				return e
			}
			state.Completed++
			if pendingDurableResults > 0 {
				pendingDurableResults--
			}
			statusPulse("persisting", result.ID)
			if r.Diagnostic {
				state.DiagnosticSaved++
			}
		}
		for _, item := range state.Pending {
			if donor, ok := aliases[item.ID]; ok {
				if _, exists := records[donor]; exists {
					if e = search.Unscored(item.ID); e != nil {
						return e
					}
					state.DuplicateSkips++
				}
			}
		}
		for _, item := range state.Pending {
			if !returnedIDs[item.ID] && search.NeedsResult(item.ID) {
				if e = search.Defer(item.ID, item.Raw); e != nil {
					return e
				}
			}
		}
		if batchError != nil {
			if e = checkpoint(); e != nil {
				return e
			}
			status("failed", batchError.Error())
			return batchError
		}
		if e = checkpoint(); e != nil {
			return e
		}
		leaderboardExportStart := time.Now()
		e = writeJSON(filepath.Join(*output, "best-strategies.json"), search.Leaderboard())
		leaderboardExportNanoseconds += uint64(time.Since(leaderboardExportStart).Nanoseconds())
		if e != nil {
			return e
		}
		controlNow, controlErr = pollControl()
		if controlErr != nil {
			status("failed", controlErr.Error())
			return controlErr
		}
		if controlBlocked || controlStopsAdmission(controlNow) {
			status("paused", "controlPath or stop.request requested pause/stop after durable batch")
			return nil
		}
	}
	status("complete", "")
	return writeJSON(filepath.Join(*output, "summary.json"), map[string]any{"state": state, "durablySaved": store.Count(), "elapsedSeconds": time.Since(start).Seconds(), "kernel": engineMetadata(), "verification": "pending coordinator parity"})
}
