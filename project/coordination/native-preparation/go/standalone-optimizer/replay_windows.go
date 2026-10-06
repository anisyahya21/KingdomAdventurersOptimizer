//go:build windows

package main

// Exact native replay capture. Events and sampled state are copied from the canonical DLL's
// existing per-tick ABI; no Python simulator, synthetic events, or reconstructed combat rules.
import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"syscall"
	"time"
	"unsafe"
)

const (
	nativeTraceSchema       = "ka-go-native-trace-1"
	replayRequestSchema     = "ka-go-replay-request-1"
	nativeTraceEventLimit   = 8192 // KA_MAX_EVENTS, from canonical ka_kernel/state.rs
	nativeTraceEventDefault = 50000
	nativeTraceFrameDefault = 30000 // maximum supported raw scenario horizon
	replayPayloadMaxBytes   = 32 << 20
)

type replayRequest struct {
	Schema            string          `json:"schema"`
	Tables            json.RawMessage `json:"tables"`
	KernelPath        string          `json:"kernelPath"`
	KernelSHA256      string          `json:"kernelSha256"`
	MechanicsIdentity json.RawMessage `json:"mechanicsIdentity"`
	Intent            json.RawMessage `json:"intent"`
	Seeds             [2]int64        `json:"seeds"`
	Trace             TraceOptions    `json:"trace"`
}

type nativeTraceEventABI struct {
	Kind, Unit, A, B, C, D, E int32
}

// runReplay is the CLI-facing bounded operation. Input is exactly one intent and ordered seed pair;
// output is atomically published as replay.json after native preparation, exact evaluation and trace.
func runReplay(input, output string, workers int) error {
	started := time.Now()
	if input == "" || output == "" {
		return errors.New("replay requires input and output paths")
	}
	if workers < 1 || workers > 256 {
		return errors.New("executor count outside 1..256")
	}
	if err := os.MkdirAll(output, 0700); err != nil {
		return err
	}
	data, err := os.ReadFile(input)
	if err != nil {
		return err
	}
	var req replayRequest
	if err = json.Unmarshal(data, &req); err != nil {
		return fmt.Errorf("replay request JSON: %w", err)
	}
	if req.Schema != replayRequestSchema || len(req.Tables) == 0 || len(req.MechanicsIdentity) == 0 || len(req.Intent) == 0 || req.KernelPath == "" || req.KernelSHA256 == "" {
		return errors.New("replay request requires schema, tables, mechanicsIdentity, kernel identity, exact intent and seeds")
	}
	if req.Seeds[0] < 0 || req.Seeds[0] > math.MaxInt32 || req.Seeds[1] < 0 || req.Seeds[1] > math.MaxInt32 {
		return errors.New("replay seeds must be nonnegative signed31-bit integers")
	}
	intent, err := AdmitRawScenario(req.Intent)
	if err != nil {
		return fmt.Errorf("replay intent: %w", err)
	}
	if intent.MathSeed != req.Seeds[0] || intent.LibSeed != req.Seeds[1] {
		return errors.New("replay seed pair differs from the exact intent; seed substitution is forbidden")
	}
	traceOpts, err := normalizeTraceOptions(req.Trace, int(intent.TickLimit))
	if err != nil {
		return err
	}
	prep, err := NewPreparer(req.Tables)
	if err != nil {
		return err
	}
	snapshot, err := prep.Prepare(req.Intent)
	if err != nil {
		return fmt.Errorf("replay native preparation: %w", err)
	}
	draws, err := prep.FollowerDraws(req.Intent)
	if err != nil {
		return err
	}
	policy := int32(0)
	switch intent.FinishPolicy {
	case "", "at-horizon":
	case "after-ending":
		policy = 1
	case "on-verdict":
		policy = 2
	default:
		return errors.New("unsupported finish policy")
	}
	build, err := replayBuildIdentity()
	if err != nil {
		return fmt.Errorf("replay executable identity: %w", err)
	}
	provenance := jsonValue(map[string]any{
		"mathSeed": req.Seeds[0], "libSeed": req.Seeds[1], "build": build,
		"requestSha256": digest(data), "intentSha256": digest(req.Intent), "mechanics": req.MechanicsIdentity,
		"simulation": "shared canonical Rust ka_kernel DLL", "preparation": "go-raw-v1", "purpose": "exact native replay trace",
	})
	id := "replay:" + digest(jsonValue(map[string]any{"intent": strategyIdentity(req.Intent), "seeds": req.Seeds}))
	engine, err := NewRawWindowsEngine(req.KernelPath, req.KernelSHA256, 1)
	if err != nil {
		return err
	}
	defer engine.Close()
	results, err := engine.RunPreparedBatch([]PreparedTask{{
		Task:     Task{ID: id, MathSeed: int32(req.Seeds[0]), LibSeed: int32(req.Seeds[1]), Provenance: provenance},
		Snapshot: snapshot, TickLimit: uint32(intent.TickLimit), PolicyCode: policy, FollowerDraws: draws, Trace: &traceOpts,
	}})
	if err != nil {
		return err
	}
	if len(results) != 1 || results[0].Trace == nil {
		return errors.New("native replay did not return one trace result")
	}
	result := results[0]
	if !result.Completed {
		return fmt.Errorf("native replay failed: %s", result.Error)
	}
	visualUnits, err := replayVisualUnits(prep, intent, snapshot, result.Trace)
	if err != nil {
		return fmt.Errorf("replay visual unit projection: %w", err)
	}
	payload := map[string]any{
		"schema": nativeTraceSchema, "intent": req.Intent, "seeds": req.Seeds, "build": build,
		"candidateId": strategyIdentity(req.Intent), "trialId": id,
		"visualUnits":            visualUnits,
		"preparedSnapshotSha256": result.SnapshotSHA256,
		"kernel":                 map[string]any{"path": result.KernelPath, "sha256": result.KernelSHA256, "nativeBattleSize": result.NativeBattleSize, "nativeReportSize": result.NativeReportSize, "encounterReportVersion": result.EncounterReportVersion, "encounterReportSize": result.EncounterReportSize},
		"result":                 result,
	}
	serializedBytes, err := writeReplayJSONBounded(filepath.Join(output, "replay.json"), payload, result.Trace, replayPayloadMaxBytes)
	if err != nil {
		return err
	}
	return writeJSON(filepath.Join(output, "status.json"), map[string]any{
		"stage": "complete", "completed": 1, "total": 1, "elapsedSeconds": time.Since(started).Seconds(),
		"output": output, "traceComplete": result.Trace.Complete, "eventsRetained": result.Trace.EventsRetained,
		"parameterTelemetryAvailable": result.Trace.ParameterTelemetryAvailable,
		"serializedBytes":             serializedBytes,
		"simulatedTicks":              result.Trace.SimulatedTicks, "stoppedAt": result.Trace.StoppedAt,
		"omittedTicks": result.Trace.OmittedTicks, "omittedFrames": result.Trace.OmittedFrames,
		"omittedEvents": result.Trace.OmittedEvents, "omittedEventsUnknown": result.Trace.OmittedEventsUnknown, "warnings": result.Trace.Warnings,
	})
}

func replayBuildIdentity() (map[string]any, error) {
	identity := map[string]any{"language": "Go", "sourceRevision": buildRevision}
	path, err := os.Executable()
	if err != nil {
		return nil, err
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	identity["executablePath"] = path
	identity["executableSha256"] = digest(data)
	return identity, nil
}

// writeReplayJSONBounded preserves the complete battle result and trims only sourced trace
// samples/events until the actual compact JSON bytes fit the durable output bound.
func writeReplayJSONBounded(path string, payload map[string]any, trace *NativeBattleTrace, limit int) (int, error) {
	if trace == nil || limit <= 0 {
		return 0, errors.New("bounded replay serialization requires a trace and positive byte limit")
	}
	for {
		payloadBytes, err := replayJSONByteCount(payload)
		if err != nil {
			return 0, err
		}
		if payloadBytes <= int64(limit) {
			file, err := os.CreateTemp(filepath.Dir(path), ".replay-write-*")
			if err != nil {
				return 0, err
			}
			tmp := file.Name()
			defer os.Remove(tmp)
			written, encodeErr := replayJSONEncode(payload, file)
			if encodeErr != nil {
				file.Close()
				return 0, encodeErr
			}
			if written > int64(limit) {
				file.Close()
				return 0, errors.New("replay JSON byte count changed during bounded serialization")
			}
			if err = file.Sync(); err != nil {
				file.Close()
				return 0, err
			}
			if err = file.Close(); err != nil {
				return 0, err
			}
			if err = os.Rename(tmp, path); err != nil {
				return 0, err
			}
			return int(written), nil
		}

		frameBytes, err := replayJSONByteCount(trace.Frames)
		if err != nil {
			return 0, err
		}
		eventBytes, err := replayJSONByteCount(trace.Events)
		if err != nil {
			return 0, err
		}
		frameRemovable, eventRemovable := replayMaxInt(0, len(trace.Frames)-2), replayMaxInt(0, len(trace.Events)-2)
		if frameRemovable == 0 && eventRemovable == 0 {
			return 0, fmt.Errorf("replay payload remains %d bytes above %d-byte limit after preserving first/final trace samples", payloadBytes, limit)
		}
		useFrames := frameRemovable > 0 && (eventRemovable == 0 || frameBytes >= eventBytes)
		if useFrames {
			remove := boundedRemovalCount(frameRemovable, payloadBytes-int64(limit), frameBytes)
			trace.Frames = removeTraceMiddle(trace.Frames, remove)
			trace.OmittedFrames += uint64(remove)
		} else {
			remove := boundedRemovalCount(eventRemovable, payloadBytes-int64(limit), eventBytes)
			trace.Events = removeTraceMiddle(trace.Events, remove)
			trace.EventsRetained = uint64(len(trace.Events))
			trace.OmittedEvents += uint64(remove)
		}
		trace.Complete = false
		if !containsString(trace.Warnings, "trace retention reduced to fit serialized replay byte limit") {
			trace.Warnings = append(trace.Warnings, "trace retention reduced to fit serialized replay byte limit")
		}
	}
}

type replayCountWriter struct {
	target io.Writer
	count  int64
}

func (w *replayCountWriter) Write(data []byte) (int, error) {
	if w.target == nil {
		w.count += int64(len(data))
		return len(data), nil
	}
	n, err := w.target.Write(data)
	w.count += int64(n)
	return n, err
}

func replayJSONEncode(value any, target io.Writer) (int64, error) {
	writer := &replayCountWriter{target: target}
	if err := json.NewEncoder(writer).Encode(value); err != nil {
		return writer.count, err
	}
	return writer.count, nil
}

func replayJSONByteCount(value any) (int64, error) {
	return replayJSONEncode(value, nil)
}

func boundedRemovalCount(available int, excess, arrayBytes int64) int {
	if available <= 0 {
		return 0
	}
	if arrayBytes <= 0 {
		return 1
	}
	fraction := float64(excess) / float64(arrayBytes) * 1.2
	if fraction < 0.01 {
		fraction = 0.01
	}
	if fraction > 0.75 {
		fraction = 0.75
	}
	remove := int(math.Ceil(float64(available) * fraction))
	if remove < 1 {
		remove = 1
	}
	if remove > available {
		remove = available
	}
	return remove
}

func removeTraceMiddle[T any](items []T, count int) []T {
	if count <= 0 || len(items) <= 2 {
		return items
	}
	if count > len(items)-2 {
		count = len(items) - 2
	}
	start := (len(items) - count) / 2
	if start < 1 {
		start = 1
	}
	end := start + count
	out := make([]T, 0, len(items)-count)
	out = append(out, items[:start]...)
	out = append(out, items[end:]...)
	return out
}

func containsString(values []string, needle string) bool {
	for _, value := range values {
		if value == needle {
			return true
		}
	}
	return false
}

func replayMaxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}

// replayVisualUnits links canonical native identity/cell state to original intent names.
// The Go preparer orders all own roster units first and spawned enemies afterward.
func replayVisualUnits(prep *Preparer, intent RawScenario, snapshotJSON []byte, trace *NativeBattleTrace) ([]map[string]any, error) {
	var snap nativeSnapshot
	if err := json.Unmarshal(snapshotJSON, &snap); err != nil {
		return nil, err
	}
	if len(snap.Units) < len(intent.OwnUnits) {
		return nil, errors.New("prepared snapshot omits own units")
	}
	enemyIDs, bossName, err := replayEnemyIdentities(prep, intent)
	if err != nil {
		return nil, err
	}
	if len(snap.Units)-len(intent.OwnUnits) != len(enemyIDs) {
		return nil, fmt.Errorf("prepared enemy count %d differs from replay spawn projection %d", len(snap.Units)-len(intent.OwnUnits), len(enemyIDs))
	}
	initialState := map[int32]NativeTraceUnit{}
	if trace != nil && len(trace.Frames) > 0 {
		for _, unit := range trace.Frames[0].Units {
			initialState[unit.Identity] = unit
		}
	}
	visual := make([]map[string]any, 0, len(snap.Units))
	for i, unit := range snap.Units {
		name, source, side := "", "spawned-enemy", "enemy"
		var monsterID any
		if i < len(intent.OwnUnits) {
			name, source, side = intent.OwnUnits[i].Name, "intent-own-unit", "ally"
			if len(bytes.TrimSpace(intent.OwnUnits[i].MonsterID)) != 0 && string(bytes.TrimSpace(intent.OwnUnits[i].MonsterID)) != "null" {
				var id int64
				if err := json.Unmarshal(intent.OwnUnits[i].MonsterID, &id); err != nil {
					return nil, fmt.Errorf("own unit %q monsterId: %w", name, err)
				}
				monsterID = id
			}
		} else {
			enemyIndex := i - len(intent.OwnUnits)
			monsterID = enemyIDs[enemyIndex]
			if enemyIndex == len(enemyIDs)-1 {
				name = bossName
			} else {
				name = fmt.Sprintf("enemy:%d:%d", enemyIndex, enemyIDs[enemyIndex])
			}
		}
		var cell []int32
		if raw := unit.Components["cell"]; len(raw) != 0 {
			if err := json.Unmarshal(raw, &cell); err != nil || len(cell) != 2 {
				return nil, fmt.Errorf("unit %d prepared cell invalid", unit.Identity)
			}
		}
		visual = append(visual, map[string]any{
			"name": name, "identity": unit.Identity, "unitId": fmt.Sprintf("%s:%d", side, i), "side": side,
			"source": source, "team": unit.Team, "human": unit.Human, "monster": unit.Monster,
			"monsterId": monsterID, "monsterType": unit.MonsterType, "monsterSize": unit.MonsterSize, "boss": unit.Boss,
			"cell": cell, "grid": unit.Board[7], "state": unit.Board[5], "skills": unit.Skills, "levels": unit.Levels,
			"hp": snapshotParameter(unit.Parameters, 10), "mp": snapshotParameter(unit.Parameters, 11), "parameterSource": "prepared-snapshot-raw",
			"weapon": unit.Weapon, "equipment": unit.Equipment,
		})
		if sourced, ok := initialState[unit.Identity]; ok {
			visual[len(visual)-1]["hpCurrent"] = sourced.HP
			visual[len(visual)-1]["mpCurrent"] = sourced.MP
			visual[len(visual)-1]["currentValueSource"] = "canonical-ka_kernel-native-getters"
			visual[len(visual)-1]["hpMax"] = sourced.HPMax
			visual[len(visual)-1]["mpMax"] = sourced.MPMax
			visual[len(visual)-1]["parameters"] = sourced.Parameters
			if sourced.HPMax == nil || sourced.MPMax == nil {
				visual[len(visual)-1]["maxValueUnavailableReason"] = "loaded canonical DLL lacks optional ka_unit_param_value/ka_unit_param_maximum exports"
			}
		}
	}
	return visual, nil
}

// replayEnemyIdentities mirrors only the deterministic encounter spawn draws in Prepare.
// It returns follower monster IDs in insertion order followed by the boss identity.
func replayEnemyIdentities(prep *Preparer, intent RawScenario) ([]int64, string, error) {
	encounter, ok := prep.encounters[intent.EncounterID]
	if !ok {
		return nil, "", fmt.Errorf("encounter %d missing from prepared catalog", intent.EncounterID)
	}
	followers, ok := encounter["followers"].([]any)
	if !ok {
		return nil, "", errors.New("encounter follower list invalid during replay projection")
	}
	rng := newSystemRandom(intent.LibSeed)
	ids := make([]int64, 0, len(followers)+1)
	for i, follower := range followers {
		row, ok := follower.(map[string]any)
		if !ok {
			return nil, "", fmt.Errorf("encounter follower %d invalid", i)
		}
		rate, ok := asInt(row["checkRate"])
		if !ok {
			return nil, "", fmt.Errorf("encounter follower %d has invalid check rate", i)
		}
		roll := randomBelow(rng.next(), 100)
		if roll < rate {
			id, ok := asInt(row["monsterId"])
			if !ok {
				return nil, "", fmt.Errorf("encounter follower %d has invalid monsterId", i)
			}
			ids = append(ids, id)
		}
	}
	bossID, ok := asInt(encounter["bossId"])
	if !ok {
		return nil, "", errors.New("encounter bossId invalid during replay projection")
	}
	ids = append(ids, bossID)
	monster, ok := prep.monsters[bossID]
	if !ok {
		return nil, "", fmt.Errorf("boss monster %d missing from prepared catalog", bossID)
	}
	bossName, _ := monster["name"].(string)
	if bossName == "" {
		bossName = fmt.Sprintf("monster:%d", bossID)
	}
	return ids, bossName, nil
}

func snapshotParameter(parameters map[int]struct {
	RawValue      int32 `json:"rawValue"`
	ExtraValue    int32 `json:"extraValue"`
	RawMax        int32 `json:"rawMax"`
	ExtraMax      int32 `json:"extraMax"`
	TrainingLevel int32 `json:"trainingLevel"`
}, id int) map[string]int32 {
	value, ok := parameters[id]
	if !ok {
		return nil
	}
	return map[string]int32{"raw": value.RawValue, "extra": value.ExtraValue, "maxRaw": value.RawMax, "maxExtra": value.ExtraMax}
}

func normalizeTraceOptions(options TraceOptions, horizon int) (TraceOptions, error) {
	if options.EveryTicks == 0 {
		options.EveryTicks = 1
	}
	if options.MaxEvents == 0 {
		options.MaxEvents = nativeTraceEventDefault
	}
	if options.MaxFrames == 0 {
		options.MaxFrames = nativeTraceFrameDefault
	}
	if options.EveryTicks < 1 || options.EveryTicks > 30000 {
		return options, errors.New("trace.everyTicks must be in 1..30000")
	}
	if options.MaxEvents < 1 || options.MaxEvents > 1000000 {
		return options, errors.New("trace.maxEvents must be in 1..1000000")
	}
	if options.MaxFrames < 2 || options.MaxFrames > nativeTraceFrameDefault {
		return options, fmt.Errorf("trace.maxFrames must be in 2..%d", nativeTraceFrameDefault)
	}
	if horizon > nativeTraceFrameDefault {
		return options, errors.New("intent horizon exceeds native trace bound")
	}
	return options, nil
}

func validateTraceABI(a rawABI) error {
	missing := []string{}
	for name, proc := range map[string]*syscall.Proc{
		"ka_native_full_tick": a.fullTick, "ka_battle_event_count": a.eventCount,
		"ka_battle_export_events": a.exportEvents, "ka_battle_export": a.exportUnits,
		"ka_battle_counters": a.battleCounters, "ka_unit_hp": a.unitHP,
		"ka_battle_mp": a.unitMP, "ka_battle_mp_config": a.mpConfig,
		"ka_use_log_count": a.useLogCount, "ka_use_log": a.useLog,
	} {
		if proc == nil {
			missing = append(missing, name)
		}
	}
	if len(missing) != 0 {
		sort.Strings(missing)
		return fmt.Errorf("canonical replay ABI exports unavailable: %v", missing)
	}
	return nil
}

func collectNativeTrace(a rawABI, battle uintptr, task PreparedTask) (*NativeBattleTrace, error) {
	opts, err := normalizeTraceOptions(*task.Trace, int(task.TickLimit))
	if err != nil {
		return nil, err
	}
	trace := &NativeBattleTrace{Schema: nativeTraceSchema, EventSource: "ka_event_count/ka_battle_export_events: canonical KaEvent kind,unit,a,b,c,d,e", EveryTicks: opts.EveryTicks, Complete: opts.EveryTicks == 1, ParameterTelemetryAvailable: a.unitParamValue != nil && a.unitParamMaximum != nil, StoppedAt: "horizon", Events: make([]NativeTraceEvent, 0, min(opts.MaxEvents, 16384)), Frames: make([]NativeTraceFrame, 0, min(opts.MaxFrames, int(task.TickLimit)))}
	if !trace.ParameterTelemetryAvailable {
		trace.Warnings = append(trace.Warnings, "canonical DLL lacks optional ka_unit_param_value/ka_unit_param_maximum exports; effective parameter maxima are unavailable")
	}
	var finalFrame NativeTraceFrame
	var finalEvents []NativeTraceEvent
	finalEventsSelected := false
	finalEventsDropped := false
	finalFrameCountedOmitted := false
	for step := uint32(0); step < task.TickLimit; step++ {
		status, _, _ := a.fullTick.Call(battle)
		if status != 0 {
			return nil, fmt.Errorf("ka_native_full_tick returned status %d at step %d", status, step)
		}
		var reportRaw [584]byte
		getter, _, _ := a.report.Call(battle, uintptr(uint32(task.PolicyCode)), uintptr(unsafe.Pointer(&reportRaw[0])))
		if getter != 0 {
			return nil, fmt.Errorf("ka_battle_report failed at step %d: %d", step, getter)
		}
		trace.SimulatedTicks++
		fields := reportValues(reportRaw[:])
		currentTick := int32Field(fields, "ticks")
		battleState, battleFrame, verdict := int32Field(fields, "battle_state"), int32Field(fields, "battle_frame"), int32Field(fields, "verdict")
		count, _, _ := a.eventCount.Call(battle)
		if count > nativeTraceEventLimit {
			return nil, fmt.Errorf("native event count %d exceeds canonical per-tick limit %d", count, nativeTraceEventLimit)
		}
		if count == nativeTraceEventLimit {
			trace.Complete = false
			trace.OmittedEventsUnknown = true
			trace.Warnings = append(trace.Warnings, "native per-tick event buffer reached its 8192-event capacity; additional events may be truncated")
		}
		events := make([]NativeTraceEvent, int(count))
		if count > 0 {
			rawEvents := make([]nativeTraceEventABI, int(count))
			copied, _, _ := a.exportEvents.Call(battle, uintptr(unsafe.Pointer(&rawEvents[0])))
			runtime.KeepAlive(rawEvents)
			if copied != count {
				return nil, fmt.Errorf("native event export copied %d of %d records at tick %d", copied, count, currentTick)
			}
			for index, event := range rawEvents {
				events[index] = NativeTraceEvent{Tick: currentTick, Seq: uint32(index), Kind: event.Kind, Name: nativeEventName(event.Kind), Unit: event.Unit, Args: [5]int32{event.A, event.B, event.C, event.D, event.E}}
			}
		}
		frame, err := exportNativeTraceFrame(a, battle, currentTick, battleState, battleFrame, verdict, step == 0)
		if err != nil {
			return nil, err
		}
		finalFrame, finalEvents = frame, events
		selected := step == 0 || currentTick%int32(opts.EveryTicks) == 0
		if selected {
			finalFrameCountedOmitted = false
			if len(trace.Frames) < opts.MaxFrames {
				trace.Frames = append(trace.Frames, frame)
			} else {
				trace.OmittedFrames++
				trace.Complete = false
			}
			if len(events) <= opts.MaxEvents-int(trace.EventsRetained) {
				trace.Events = append(trace.Events, events...)
				trace.EventsRetained += uint64(len(events))
				finalEventsSelected = true
				finalEventsDropped = false
			} else {
				trace.OmittedEvents += uint64(len(events))
				trace.Complete = false
				finalEventsSelected = true
				finalEventsDropped = true
			}
		} else {
			trace.OmittedTicks++
			trace.OmittedEvents += uint64(len(events))
			finalFrameCountedOmitted = true
			finalEventsDropped = true
			finalEventsSelected = false
			trace.Complete = false
		}
		if battleState == 3 && task.PolicyCode == 2 {
			trace.StoppedAt = "verdict"
			break
		}
		if battleState == 3 && task.PolicyCode == 1 && battleFrame > 79 {
			trace.StoppedAt = "after-ending-boundary"
			break
		}
	}
	// Always preserve the final sourced state and its event detail. If the frame limit is full, replace
	// its last periodic sample; the omission counter makes that loss explicit.
	if len(trace.Frames) == 0 || trace.Frames[len(trace.Frames)-1].Tick != finalFrame.Tick {
		if len(trace.Frames) < opts.MaxFrames {
			trace.Frames = append(trace.Frames, finalFrame)
			if finalFrameCountedOmitted && trace.OmittedTicks > 0 {
				trace.OmittedTicks--
			}
		} else if len(trace.Frames) > 0 {
			trace.Frames[len(trace.Frames)-1] = finalFrame
			trace.OmittedFrames++
			if finalFrameCountedOmitted && trace.OmittedTicks > 0 {
				trace.OmittedTicks--
			}
		}
	}
	if !finalEventsSelected && len(finalEvents) <= opts.MaxEvents-int(trace.EventsRetained) {
		trace.Events = append(trace.Events, finalEvents...)
		trace.EventsRetained += uint64(len(finalEvents))
		if finalEventsDropped && trace.OmittedEvents >= uint64(len(finalEvents)) {
			trace.OmittedEvents -= uint64(len(finalEvents))
		}
		trace.Complete = trace.Complete && opts.EveryTicks == 1
	}
	if !finalEventsSelected && len(finalEvents) > opts.MaxEvents-int(trace.EventsRetained) {
		trace.Complete = false
	}
	return trace, nil
}

func exportNativeTraceFrame(a rawABI, battle uintptr, tick, battleState, battleFrame, verdict int32, includeAllParameters bool) (NativeTraceFrame, error) {
	units := make([]nUnit, nativeUnitLimit)
	count, _, _ := a.exportUnits.Call(battle, uintptr(unsafe.Pointer(&units[0])))
	runtime.KeepAlive(units)
	if count > nativeUnitLimit {
		return NativeTraceFrame{}, fmt.Errorf("native unit export count %d exceeds limit %d", count, nativeUnitLimit)
	}
	frame := NativeTraceFrame{Tick: tick, BattleState: battleState, BattleFrame: battleFrame, Verdict: verdict, Units: make([]NativeTraceUnit, 0, count)}
	for _, unit := range units[:count] {
		if unit.Present == 0 {
			continue
		}
		hp, _, _ := a.unitHP.Call(battle, uintptr(uint32(unit.Identity)))
		mp, _, _ := a.unitMP.Call(battle, uintptr(uint32(unit.Identity)))
		var hpMax, mpMax *int32
		var parameters map[int]NativeTraceParameter
		if a.unitParamValue != nil && a.unitParamMaximum != nil {
			hpValue, _, _ := a.unitParamValue.Call(battle, uintptr(uint32(unit.Identity)), uintptr(uint32(10)))
			hpMaximum, _, _ := a.unitParamMaximum.Call(battle, uintptr(uint32(unit.Identity)), uintptr(uint32(10)))
			mpValue, _, _ := a.unitParamValue.Call(battle, uintptr(uint32(unit.Identity)), uintptr(uint32(11)))
			mpMaximum, _, _ := a.unitParamMaximum.Call(battle, uintptr(uint32(unit.Identity)), uintptr(uint32(11)))
			hpMaxValue, mpMaxValue := int32(hpMaximum), int32(mpMaximum)
			hpMax, mpMax, hp, mp = &hpMaxValue, &mpMaxValue, hpValue, mpValue
			if includeAllParameters {
				parameters = make(map[int]NativeTraceParameter, unit.Params.Count)
				parameters[10] = NativeTraceParameter{Value: int32(hpValue), Maximum: hpMaxValue}
				parameters[11] = NativeTraceParameter{Value: int32(mpValue), Maximum: mpMaxValue}
				for parameterIndex := uint32(0); parameterIndex < unit.Params.Count && parameterIndex < uint32(len(unit.Params.Rows)); parameterIndex++ {
					parameterID := unit.Params.Rows[parameterIndex].ID
					if parameterID == 10 || parameterID == 11 {
						continue
					}
					value, _, _ := a.unitParamValue.Call(battle, uintptr(uint32(unit.Identity)), uintptr(uint32(parameterID)))
					maximum, _, _ := a.unitParamMaximum.Call(battle, uintptr(uint32(unit.Identity)), uintptr(uint32(parameterID)))
					parameters[int(parameterID)] = NativeTraceParameter{Value: int32(value), Maximum: int32(maximum)}
				}
			}
		}
		state := int32(0)
		for index := uint32(0); index < unit.Board.Len && index < nativeBoardLimit; index++ {
			if unit.Board.Entries[index].Key == 5 { // KaBoard::state() uses board key 5; key 8 is attack gauge.
				state = int32(unit.Board.Entries[index].Value)
				break
			}
		}
		frame.Units = append(frame.Units, NativeTraceUnit{Identity: unit.Identity, Team: unit.Team, Present: true, HP: int32(hp), MP: int32(mp), HPMax: hpMax, MPMax: mpMax, Parameters: parameters, Cell: unit.Body.Cell, State: state})
	}
	return frame, nil
}

func nativeFeatureReport(result BattleResult) map[string]any {
	fields := result.Report.Values
	progressKeys := []string{"progress_boss", "progress_boss_death_tick", "progress_boss_leaving_tick", "progress_stored_at_death", "progress_stored_targeting_boss_at_death", "progress_stored_target_holders_at_death", "progress_commands_targeting_boss", "progress_commands_targeting_boss_released", "progress_max_stored", "progress_max_targeting_boss", "progress_target_holders_peak", "progress_post_death_reentries", "progress_post_death_leavings", "progress_post_death_prizes", "progress_released_after_death", "progress_released_after_death_targeting_boss", "progress_first_release_after_death", "progress_last_release_after_death", "mp_watch_count"}
	mpKeys := []string{"mp_identity", "mp_min", "mp_min_percent", "mp_low", "mp_first_low_tick", "mp_first_low_phase", "mp_zero"}
	herbKeys := []string{"herb_stock_start", "herb_stock_remaining", "herb_max_uses", "herb_use_count", "herb_log_count", "herb_use_tick", "herb_use_phase", "herb_use_source", "herb_use_ok"}
	return map[string]any{
		"earned":   map[string]any{"value": result.Earned, "valid": result.EarnedValid, "basis": nullableString(result.EarnedBasis), "inventoryVerified": result.InventoryVerified, "reason": earnedReason(result)},
		"progress": compactFields(fields, progressKeys), "mp": compactFields(fields, mpKeys), "herb": compactFields(fields, herbKeys),
		"encounter": result.EncounterReport, "coreReport": map[string]any{"verdict": fieldOrNil(fields, "verdict"), "verdictTick": fieldOrNil(fields, "verdict_tick"), "ticks": fieldOrNil(fields, "ticks"), "finishPolicy": fieldOrNil(fields, "finish_policy"), "battleState": fieldOrNil(fields, "battle_state"), "battleFrame": fieldOrNil(fields, "battle_frame")},
	}
}

func compactFields(fields map[string]any, keys []string) map[string]any {
	out := make(map[string]any, len(keys))
	for _, key := range keys {
		out[key] = fieldOrNil(fields, key)
	}
	return out
}

func fieldOrNil(fields map[string]any, key string) any {
	if value, ok := fields[key]; ok {
		return value
	}
	return nil
}

func nullableString(value string) any {
	if value == "" {
		return nil
	}
	return value
}

func earnedReason(result BattleResult) string {
	if result.EarnedValid {
		return "measured from canonical reward entitlement report; not an inventory receipt"
	}
	if result.Error != "" {
		return "native evaluation failed: " + result.Error
	}
	return "canonical reward entitlement is unresolved or unavailable"
}

func int32Field(fields map[string]any, key string) int32 {
	switch value := fields[key].(type) {
	case int32:
		return value
	case int:
		return int32(value)
	case int64:
		return int32(value)
	case float64:
		return int32(value)
	default:
		return 0
	}
}

func nativeEventName(kind int32) string {
	if name, ok := map[int32]string{
		1: "state", 2: "enqueue", 3: "invocation", 4: "animation", 5: "use", 6: "attack", 7: "mp_pay", 8: "create", 9: "destroy", 10: "hp", 11: "heal", 12: "status", 13: "prize", 14: "invoking", 15: "area_cell", 16: "state_sound", 18: "body_flight", 19: "release", 20: "projectile_launch", 21: "revive", 22: "status_text", 23: "attack_batch", 24: "cell_change", 25: "status_tick", 26: "body_impact", 27: "projectile_impact", 28: "projectile_cleanup", 29: "verdict", 30: "resource_change", 31: "battle_item",
	}[kind]; ok {
		return name
	}
	return "unknown"
}
