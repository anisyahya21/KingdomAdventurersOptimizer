//go:build windows

package main

import "encoding/json"

// SnapshotRef is used by the diagnostic template executor only. Production
// optimizer candidates use PreparedTask and are rehydrated from snapshot JSON.
type SnapshotRef struct {
	SnapshotJSON     json.RawMessage `json:"snapshot"`
	SnapshotSHA256   string          `json:"snapshotSha256"`
	TemplatePath     string          `json:"templatePath"`
	TemplateSHA256   string          `json:"templateSha256"`
	KernelPath       string          `json:"kernelPath"`
	KernelSHA256     string          `json:"kernelSha256"`
	NativeBattleSize uint32          `json:"nativeBattleSize"`
	NativeReportSize uint32          `json:"nativeReportSize"`
	TemplateChecksum uint64          `json:"nativeTemplateChecksum"`
	TickLimit        uint32          `json:"tickLimit"`
	PolicyCode       int32           `json:"policyCode"`
	FollowerDraws    uint32          `json:"followerDraws"`
}

type EngineConfig struct {
	Snapshot  SnapshotRef
	Executors int
}

type Task struct {
	ID         string          `json:"id"`
	MathSeed   int32           `json:"mathSeed"`
	LibSeed    int32           `json:"libSeed"`
	Provenance json.RawMessage `json:"provenance"`
}

type BattleResult struct {
	ID                                 string             `json:"id"`
	Executor                           int                `json:"executor"`
	MathSeed                           int32              `json:"mathSeed"`
	LibSeed                            int32              `json:"libSeed"`
	FollowerDraws                      uint32             `json:"followerDraws"`
	Provenance                         json.RawMessage    `json:"provenance"`
	KernelSHA256                       string             `json:"kernelSha256,omitempty"`
	KernelPath                         string             `json:"kernelPath,omitempty"`
	SnapshotSHA256                     string             `json:"snapshotSha256,omitempty"`
	NativeBattleSize                   uint32             `json:"nativeBattleSize,omitempty"`
	NativeReportSize                   uint32             `json:"nativeReportSize,omitempty"`
	Status                             int32              `json:"status"`
	TemplateChecksumBeforeSeed         uint64             `json:"templateChecksumBeforeSeed,omitempty"`
	SnapshotChecksumBeforeRun          uint64             `json:"snapshotChecksumBeforeRun"`
	SnapshotChecksumBeforeFollowerSkip uint64             `json:"snapshotChecksumBeforeFollowerSkip,omitempty"`
	ChecksumAfter                      uint64             `json:"checksumAfter"`
	Stats                              NativeStats        `json:"stats"`
	Report                             NativeReport       `json:"report"`
	RawReportBytes                     []byte             `json:"rawReportBytes"`
	ReportJSON                         json.RawMessage    `json:"reportJSON"`
	EncounterReport                    map[string]any     `json:"encounterReport,omitempty"`
	EncounterReportRaw                 []byte             `json:"encounterReportRaw,omitempty"`
	EncounterReportVersion             uint32             `json:"encounterReportVersion,omitempty"`
	EncounterReportSize                uint32             `json:"encounterReportSize,omitempty"`
	Earned                             *int32             `json:"earned"`
	EarnedValid                        bool               `json:"earnedValid"`
	EarnedBasis                        string             `json:"earnedBasis,omitempty"`
	InventoryVerified                  bool               `json:"inventoryVerified"`
	Error                              string             `json:"error,omitempty"`
	Completed                          bool               `json:"completed"`
	StageTimings                       map[string]int64   `json:"stageTimings,omitempty"`
	Trace                              *NativeBattleTrace `json:"trace,omitempty"`
}

// PreparedTask binds one seeded execution to its own full canonical engine snapshot.
// The snapshot is rehydrated with native setter exports for every evaluation.
type PreparedTask struct {
	Task
	Snapshot      json.RawMessage `json:"snapshot"`
	TickLimit     uint32          `json:"tickLimit"`
	PolicyCode    int32           `json:"policyCode"`
	FollowerDraws uint32          `json:"followerDraws"`
	MeasureTiming bool            `json:"measureTiming,omitempty"`
	Trace         *TraceOptions   `json:"trace,omitempty"`
}

// TraceOptions asks the native runner to retain exact, ordered canonical events from
// ka_native_full_tick. A stride above one explicitly samples ticks and is never described
// as a complete trace. MaxEvents bounds retained event memory.
type TraceOptions struct {
	EveryTicks int `json:"everyTicks"`
	MaxEvents  int `json:"maxEvents"`
	MaxFrames  int `json:"maxFrames"`
}

// NativeBattleTrace is sourced directly from the canonical DLL event and state exports.
// Event.Args retain the exact ABI words; Event.KindName is the source constant name.
type NativeBattleTrace struct {
	Schema                      string             `json:"schema"`
	EventSource                 string             `json:"eventSource"`
	EveryTicks                  int                `json:"everyTicks"`
	SimulatedTicks              int                `json:"simulatedTicks"`
	Complete                    bool               `json:"complete"`
	StoppedAt                   string             `json:"stoppedAt"`
	OmittedTicks                uint64             `json:"omittedTicks"`
	OmittedFrames               uint64             `json:"omittedFrames"`
	OmittedEvents               uint64             `json:"omittedEvents"`
	OmittedEventsUnknown        bool               `json:"omittedEventsUnknown"`
	ParameterTelemetryAvailable bool               `json:"parameterTelemetryAvailable"`
	EventsRetained              uint64             `json:"eventsRetained"`
	Warnings                    []string           `json:"warnings,omitempty"`
	Events                      []NativeTraceEvent `json:"events"`
	Frames                      []NativeTraceFrame `json:"frames"`
	FinalReport                 map[string]any     `json:"finalReport"`
	FeatureReport               map[string]any     `json:"featureReport"`
}

type NativeTraceEvent struct {
	Tick int32    `json:"tick"`
	Seq  uint32   `json:"seq"`
	Kind int32    `json:"kind"`
	Name string   `json:"name"`
	Unit int32    `json:"unit"`
	Args [5]int32 `json:"args"`
}

type NativeTraceFrame struct {
	Tick        int32             `json:"tick"`
	BattleState int32             `json:"battleState"`
	BattleFrame int32             `json:"battleFrame"`
	Verdict     int32             `json:"verdict"`
	Units       []NativeTraceUnit `json:"units"`
}

type NativeTraceUnit struct {
	Identity   int32                        `json:"identity"`
	Team       uint8                        `json:"team"`
	Present    bool                         `json:"present"`
	HP         int32                        `json:"hp"`
	MP         int32                        `json:"mp"`
	HPMax      *int32                       `json:"hpMax"`
	MPMax      *int32                       `json:"mpMax"`
	Parameters map[int]NativeTraceParameter `json:"parameters"`
	Cell       [2]int32                     `json:"cell"`
	State      int32                        `json:"state"`
}

type NativeTraceParameter struct {
	Value   int32 `json:"value"`
	Maximum int32 `json:"maximum"`
}

type NativeStats struct {
	NotifyCalls   uint64 `json:"notifyCalls"`
	SubsetChecks  uint64 `json:"subsetChecks"`
	BucketLookups uint64 `json:"bucketLookups"`
	BucketSteps   uint64 `json:"bucketSteps"`
}

// NativeEngineProgress separates queued/running/completed-native work from
// results already harvested and durably recorded by the caller.
type NativeEngineProgress struct {
	Accepted             uint64            `json:"accepted"`
	Completed            uint64            `json:"completed"`
	BattleCalls          uint64            `json:"battleCalls"`
	SuccessfulBattles    uint64            `json:"successfulBattles"`
	InFlight             uint64            `json:"inFlight"`
	ReadyQueueDepth      uint64            `json:"readyQueueDepth"`
	DispatchPending      uint64            `json:"dispatchPending"`
	ExecutingWorkers     uint64            `json:"executingWorkers"`
	CompletedUnharvested uint64            `json:"completedUnharvested"`
	WorkerBusySeconds    float64           `json:"workerBusySeconds"`
	NativeCallSeconds    float64           `json:"nativeCallSeconds"`
	StageNanoseconds     map[string]uint64 `json:"stageNanoseconds"`
	StageSamples         map[string]uint64 `json:"stageSamples"`
}

// NativeReport mirrors KaBattleReport as exported by the canonical ka_kernel
// DLL. The fixed-size raw representation is authoritative and checked against
// the DLL size probe; Values provides stable named JSON fields.
type NativeReport struct {
	Raw    [584]byte      `json:"-"`
	Values map[string]any `json:"fields"`
}

// Engine is the narrow execution interface consumed by the optimizer.
type Engine interface {
	RunBatch(tasks []Task) ([]BattleResult, error)
	Provenance() KernelProvenance
	Close() error
}

type PreparedEngine interface {
	RunPreparedBatch(tasks []PreparedTask) ([]BattleResult, error)
	Provenance() KernelProvenance
	Close() error
}

type KernelProvenance struct {
	KernelPath       string `json:"kernelPath"`
	KernelSHA256     string `json:"kernelSha256"`
	SnapshotSHA256   string `json:"snapshotSha256"`
	TemplateSHA256   string `json:"templateSha256"`
	NativeBattleSize uint32 `json:"nativeBattleSize"`
	NativeReportSize uint32 `json:"nativeReportSize"`
	EncounterVersion uint32 `json:"encounterReportVersion,omitempty"`
	EncounterSize    uint32 `json:"encounterReportSize,omitempty"`
	TemplateChecksum uint64 `json:"nativeTemplateChecksum"`
	TickLimit        uint32 `json:"tickLimit"`
	PolicyCode       int32  `json:"policyCode"`
	FollowerDraws    uint32 `json:"followerDraws"`
}
