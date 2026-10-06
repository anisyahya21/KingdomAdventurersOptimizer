package main

// Opt-in matched baseline execution. Ordinary adaptive search stays in main.go.
import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

type baselineRunState struct {
	Schema         string              `json:"schema"`
	WorkloadHash   string              `json:"workloadSha256"`
	BankHash       string              `json:"seedBankSha256"`
	BuildRevision  string              `json:"buildRevision"`
	ExecutableHash string              `json:"executableSha256"`
	Batch          int                 `json:"batchSize"`
	SyncBatch      int                 `json:"syncBatch"`
	Executors      int                 `json:"executors"`
	Baseline       json.RawMessage     `json:"baseline"`
	Pending        []pendingEvaluation `json:"pending"`
}

type comparisonTiming struct {
	Schema                  string           `json:"schema"`
	SerialWallNs            map[string]int64 `json:"serialWallNs"`
	WorkerBusySumNs         map[string]int64 `json:"workerBusySumNs"`
	NativeBatches           uint64           `json:"nativeBatches"`
	NativeTrials            uint64           `json:"nativeTaskDispatches"`
	NativeCalls             uint64           `json:"nativeCalls"`
	PreparedTrials          uint64           `json:"preparedTrials"`
	JournalRecordsLoaded    uint64           `json:"journalRecordsLoaded"`
	PendingFeedbackRestored uint64           `json:"pendingFeedbackRestored"`
	EntryToSummaryNs        int64            `json:"entryToSummaryNs"`
	Notes                   []string         `json:"notes"`
}

func runBaselineComparison(input, output, bankPath string, workers, batch, syncBatch int, entered time.Time) (runErr error) {
	if bankPath == "" || syncBatch < 1 || syncBatch > 4096 {
		return errors.New("baseline-bank requires --seed-bank and --sync-batch in 1..4096")
	}
	timing := comparisonTiming{Schema: "ka-go-comparison-timing-1", SerialWallNs: map[string]int64{}, WorkerBusySumNs: map[string]int64{}, Notes: []string{
		"Times apply to this invocation, including resume work; durable counts describe the full scope.",
		"Worker busy sums overlap across workers and are not serial elapsed time or isolated speed gains.",
		"native_batch serial wall includes worker hydration, native calls, report decoding, arena cleanup and queueing; worker stages are its overlapping children.",
		"persistence serial wall includes record assembly, store serialization/write/sync and checkpoint/export; store timings cover active save/checkpoint calls and overlap that wall, but exclude replay/tail repair and final close sync.",
		"journalRecordsLoaded and pendingFeedbackRestored count resume work in this invocation; duplicate skips, errors and rejections are also invocation-local, while durable completion counts cover the full scope.",
		"Journal replay/tail repair is included in store_open_replay but has no separate child timing. Startup before Go run entry, final close sync, final summary write and shutdown are unavailable in the named stage timings; external process start-to-exit wall is authoritative.",
	}}
	add := func(stage string, began time.Time) { timing.SerialWallNs[stage] += time.Since(began).Nanoseconds() }
	phase := time.Now()
	data, err := os.ReadFile(input)
	if err != nil {
		return err
	}
	bankData, err := os.ReadFile(bankPath)
	if err != nil {
		return err
	}
	var w Workload
	if err = json.Unmarshal(data, &w); err != nil {
		return err
	}
	if w.Schema != "ka-go-workload-1" || len(w.Candidates) == 0 || len(w.MechanicsIdentity) == 0 {
		return errors.New("invalid baseline workload")
	}
	var rawPairs [][]int64
	if err = json.Unmarshal(bankData, &rawPairs); err != nil {
		return err
	}
	pairs := make([][2]int64, len(rawPairs))
	for i, row := range rawPairs {
		if len(row) != 2 {
			return fmt.Errorf("seed bank row %d must contain exactly two integers", i)
		}
		pairs[i] = [2]int64{row[0], row[1]}
	}
	add("input_read_parse", phase)
	phase = time.Now()
	p, err := NewPreparer(w.Tables)
	if err != nil {
		return err
	}
	add("catalog_initialization", phase)
	phase = time.Now()
	base, err := NewBaseline(w.Candidates, pairs)
	if err != nil {
		return err
	}
	workloadPolicyHash := base.PolicyHash()
	add("baseline_admission", phase)
	phase = time.Now()
	store, err := NewStoreWithSyncBatch(output, syncBatch)
	if err != nil {
		return err
	}
	defer store.Close()
	defer func() {
		if runErr != nil {
			_ = writeJSON(filepath.Join(output, "status.json"), map[string]any{"stage": "failed", "detail": runErr.Error(), "durablyCompleted": store.Count(), "elapsedSeconds": time.Since(entered).Seconds(), "output": output, "mode": "baseline-bank"})
			_ = writeJSON(filepath.Join(output, "run-error.json"), map[string]any{"error": runErr.Error(), "durablySaved": store.Count(), "importDisabled": true})
		}
	}()
	add("store_open_replay", phase)
	phase = time.Now()
	exe, err := os.Executable()
	if err != nil {
		return err
	}
	exeBytes, err := os.ReadFile(exe)
	if err != nil {
		return err
	}
	exeHash := digest(exeBytes)
	productionIdentity, err := workloadProductionIdentity(w, exeHash, "evaluate")
	if err != nil {
		return err
	}
	add("executable_identity", phase)
	state := baselineRunState{Schema: "ka-go-baseline-run-1", WorkloadHash: digest(data), BankHash: digest(bankData), BuildRevision: buildRevision, ExecutableHash: exeHash, Batch: batch, SyncBatch: syncBatch, Executors: workers}
	phase = time.Now()
	cp, err := store.LoadCheckpoint()
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if len(cp) > 0 {
		var saved baselineRunState
		if err = json.Unmarshal(cp, &saved); err != nil {
			return err
		}
		if saved.Schema != state.Schema || saved.WorkloadHash != state.WorkloadHash || saved.BankHash != state.BankHash || saved.BuildRevision != state.BuildRevision || saved.ExecutableHash != state.ExecutableHash || saved.Batch != batch || saved.SyncBatch != syncBatch || saved.Executors != workers {
			return errors.New("baseline resume identity/config mismatch")
		}
		state = saved
		base, err = RestoreBaseline(state.Baseline)
		if err != nil {
			return err
		}
		if base.PolicyHash() != workloadPolicyHash {
			return errors.New("checkpoint baseline does not match workload candidates and seed bank")
		}
		proposed, _, feedback := base.Progress()
		if uint64(len(state.Pending)) != proposed-feedback {
			return errors.New("checkpoint run pending list disagrees with baseline scheduler")
		}
		seenPending := make(map[string]bool, len(state.Pending))
		for _, item := range state.Pending {
			if seenPending[item.ID] {
				return errors.New("checkpoint run pending list contains duplicate trial IDs")
			}
			seenPending[item.ID] = true
			if err = base.ValidateTrialRaw(item.ID, item.Raw); err != nil {
				return fmt.Errorf("checkpoint pending trial %s: %w", item.ID, err)
			}
		}
	} else if store.Count() != 0 {
		return errors.New("baseline journal without checkpoint")
	}
	add("checkpoint_restore", phase)
	phase = time.Now()
	build := jsonValue(map[string]any{"language": "Go", "sourceRevision": buildRevision, "executableSHA256": exeHash})
	engine, err := NewRawWindowsEngine(w.KernelPath, w.KernelSHA256, workers)
	if err != nil {
		return err
	}
	defer engine.Close()
	add("engine_startup", phase)
	engineMeta := func() map[string]any {
		v := engine.Provenance()
		return map[string]any{"kernelPath": v.KernelPath, "kernelSha256": v.KernelSHA256, "nativeBattleSize": v.NativeBattleSize, "nativeReportSize": v.NativeReportSize, "encounterReportVersion": v.EncounterVersion, "encounterReportSize": v.EncounterSize}
	}
	checkpoint := func() error {
		pending := make([]pendingEvaluation, 0, len(state.Pending))
		for _, item := range state.Pending {
			if base.NeedsResult(item.ID) {
				pending = append(pending, item)
			}
		}
		state.Pending = pending
		state.Baseline, err = base.Snapshot()
		if err != nil {
			return err
		}
		return store.SaveCheckpoint(jsonValue(state))
	}
	records := map[string]Record{}
	var completed, diagnostic, duplicateSkips, nativeErrors, rejected uint64
	paused := false
	for _, r := range store.Records() {
		records[r.ID] = r
		timing.JournalRecordsLoaded++
		completed++
		if r.Diagnostic {
			diagnostic++
		}
	}
	status := func(stage, detail string) error {
		_, total, _ := base.Progress()
		return writeJSON(filepath.Join(output, "status.json"), map[string]any{"stage": stage, "detail": detail, "completed": completed, "total": total, "durablyCompleted": store.Count(), "diagnosticSaved": diagnostic, "validDurable": completed - diagnostic, "duplicateSkips": duplicateSkips, "errors": nativeErrors, "rejected": rejected, "elapsedSeconds": time.Since(entered).Seconds(), "output": output, "mode": "baseline-bank", "syncBatch": syncBatch})
	}
	makeRecord := func(item pendingEvaluation, snapshot json.RawMessage, raw RawScenario, draws uint32, prov json.RawMessage, policy int32) Record {
		return Record{Schema: recordSchema, ID: item.ID, Intent: item.Raw, Candidate: item.Raw, Prepared: snapshot, Seeds: map[string]int64{"mathSeed": raw.MathSeed, "libSeed": raw.LibSeed}, Lineage: jsonValue(base.Lineage(item.ID)), Provenance: map[string]json.RawMessage{"executionScope": jsonValue(NormalizeExecutionScope(w.ExecutionScope)), "productionIdentity": jsonValue(productionIdentity), "engine": jsonValue(engineMeta()), "execution": jsonValue(map[string]any{"snapshotSha256": digest(snapshot), "tickLimit": raw.TickLimit, "policyCode": policy, "finishPolicy": raw.FinishPolicy, "followerDraws": draws}), "mechanics": w.MechanicsIdentity, "policy": jsonValue(base.PolicyHash()), "run": prov}, Diagnostic: raw.FinishPolicy == "on-verdict", Verified: false}
	}
	phase = time.Now()
	if err = checkpoint(); err != nil {
		return err
	}
	add("persistence", phase)
	for {
		phase = time.Now()
		if len(state.Pending) == 0 {
			for len(state.Pending) < batch {
				proposed, total, _ := base.Progress()
				if proposed >= total {
					break
				}
				raw, id, e := base.Next()
				if e != nil {
					return e
				}
				state.Pending = append(state.Pending, pendingEvaluation{id, raw})
			}
			if len(state.Pending) == 0 {
				break
			}
			if err = checkpoint(); err != nil {
				return err
			}
		}
		add("dispatch_checkpoint", phase)
		if err = status("preparing", ""); err != nil {
			return err
		}
		phase = time.Now()
		tasks := []PreparedTask{}
		skeletons := map[string]Record{}
		for _, item := range state.Pending {
			if _, ok := records[item.ID]; ok {
				if base.NeedsResult(item.ID) {
					if err = base.Observe(item.ID); err != nil {
						return err
					}
					timing.PendingFeedbackRestored++
				}
				continue
			}
			snapshot, e := p.Prepare(item.Raw)
			if e != nil {
				rejected++
				status("failed", e.Error())
				return fmt.Errorf("baseline preparation %s: %w", item.ID, e)
			}
			timing.PreparedTrials++
			var raw RawScenario
			if e = json.Unmarshal(item.Raw, &raw); e != nil {
				return e
			}
			draws, e := p.FollowerDraws(item.Raw)
			if e != nil {
				return e
			}
			policy := int32(0)
			if raw.FinishPolicy == "after-ending" {
				policy = 1
			}
			if raw.FinishPolicy == "on-verdict" {
				policy = 2
			}
			prov := jsonValue(map[string]any{"mathSeed": raw.MathSeed, "libSeed": raw.LibSeed, "build": build, "workloadSha256": state.WorkloadHash, "seedBankSha256": state.BankHash, "tablesSha256": digest(w.Tables), "policyHash": base.PolicyHash(), "mode": "baseline-bank", "candidateOrder": "input order; seed bank inner order", "batchSize": batch, "syncBatch": syncBatch, "executors": workers, "mechanics": w.MechanicsIdentity, "preparation": "go-raw-v1", "simulation": "shared canonical Rust ka_kernel DLL", "followerDraws": draws})
			r := makeRecord(item, snapshot, raw, draws, prov, policy)
			if prior, ok := store.FindIdentity(IdentityHash(r)); ok {
				if prior.ID != item.ID {
					return errors.New("fixed stream identity collision")
				}
				if err = base.Observe(item.ID); err != nil {
					return err
				}
				duplicateSkips++
				continue
			}
			skeletons[item.ID] = r
			tasks = append(tasks, PreparedTask{Task: Task{ID: item.ID, MathSeed: int32(raw.MathSeed), LibSeed: int32(raw.LibSeed), Provenance: prov}, Snapshot: snapshot, TickLimit: uint32(raw.TickLimit), PolicyCode: policy, FollowerDraws: draws, MeasureTiming: true})
		}
		add("preparation_record_binding", phase)
		if err = status("executing", ""); err != nil {
			return err
		}
		var results []BattleResult
		phase = time.Now()
		if len(tasks) > 0 {
			results, err = engine.RunPreparedBatch(tasks)
			timing.NativeBatches++
			timing.NativeTrials += uint64(len(tasks))
		}
		add("native_batch", phase)
		batchErr := err
		phase = time.Now()
		for _, result := range results {
			if _, called := result.StageTimings["native_call"]; called {
				timing.NativeCalls++
			}
			for key, value := range result.StageTimings {
				timing.WorkerBusySumNs[key] += value
			}
			if !result.Completed {
				nativeErrors++
				if e := writeJSON(filepath.Join(output, "failure-"+digest([]byte(result.ID))+".json"), result); e != nil {
					return e
				}
				if batchErr == nil {
					batchErr = fmt.Errorf("native baseline %s: %s", result.ID, result.Error)
				}
				continue
			}
			r := skeletons[result.ID]
			applyProductionScope(&r, &result, w, productionIdentity)
			r.Result = jsonValue(result)
			if err = store.Save(r); err != nil {
				return err
			}
			records[r.ID] = r
			completed++
			if r.Diagnostic {
				diagnostic++
			}
			if err = base.Observe(r.ID); err != nil {
				return err
			}
		}
		if err = checkpoint(); err != nil {
			return err
		}
		add("persistence", phase)
		if batchErr != nil {
			status("failed", batchErr.Error())
			return batchErr
		}
		if _, e := os.Stat(filepath.Join(output, "stop.request")); e == nil {
			if err = status("paused", "stop.request after durable batch"); err != nil {
				return err
			}
			paused = true
			break
		}
	}
	finalStage := "complete"
	if paused {
		finalStage = "paused"
	}
	if err = status(finalStage, ""); err != nil {
		return err
	}
	timing.EntryToSummaryNs = time.Since(entered).Nanoseconds()
	return writeJSON(filepath.Join(output, "summary.json"), map[string]any{"schema": "ka-go-baseline-summary-1", "stage": finalStage, "mode": "baseline-bank", "state": state, "completed": completed, "durablySaved": store.Count(), "diagnosticSaved": diagnostic, "validDurable": completed - diagnostic, "duplicateSkips": duplicateSkips, "errors": nativeErrors, "rejected": rejected, "timings": timing, "storeTimings": store.Stats(), "kernel": engineMeta(), "verification": "diagnostic; import disabled; coordinator comparison pending"})
}
