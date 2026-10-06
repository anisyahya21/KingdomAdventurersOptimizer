//go:build windows

package main

import (
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"sync"
	"syscall"
	"unsafe"
)

const maxBattleBytes = 64 << 20
const maxTasksPerBatch = 4096

type kernelDLL struct {
	dll                                                   *syscall.DLL
	clone, seed, skip, run, report, stats, checksum, free *syscall.Proc
	battleSize, reportSize                                *syscall.Proc
}

type workerArena struct{ baseline uintptr }

type nativeJob struct {
	index int
	task  Task
}
type nativeReply struct {
	index  int
	result BattleResult
	err    error
}

// WindowsEngine keeps the canonical DLL and one resident baseline arena per
// worker alive for its entire lifetime. Each battle is cloned from that
// baseline, seeded, executed, read back, and freed before the next job.
type WindowsEngine struct {
	mu         sync.Mutex
	k          kernelDLL
	arenas     []workerArena
	jobs       chan nativeJob
	replies    chan nativeReply
	workers    sync.WaitGroup
	closed     bool
	provenance KernelProvenance
	settings   SnapshotRef
	template   []byte
}

func shaBytes(b []byte) string { h := sha256.Sum256(b); return hex.EncodeToString(h[:]) }

func loadProc(d *syscall.DLL, name string) (*syscall.Proc, error) {
	p, err := d.FindProc(name)
	if err != nil {
		return nil, fmt.Errorf("canonical kernel missing export %s: %w", name, err)
	}
	return p, nil
}

func NewWindowsEngine(cfg EngineConfig) (*WindowsEngine, error) {
	s := cfg.Snapshot
	if cfg.Executors < 1 || cfg.Executors > 256 {
		return nil, fmt.Errorf("executor count out of supported range: %d", cfg.Executors)
	}
	if s.TickLimit == 0 || s.TickLimit > 30000 || s.PolicyCode < 0 || s.PolicyCode > 2 || s.FollowerDraws > 1000000 {
		return nil, errors.New("snapshot tick, policy, or follower draw metadata outside supported bounds")
	}
	if s.NativeBattleSize == 0 || s.NativeBattleSize > maxBattleBytes || s.NativeReportSize != 584 {
		return nil, errors.New("snapshot battle/report sizes outside supported ABI bounds")
	}
	if len(s.SnapshotJSON) == 0 || s.SnapshotSHA256 == "" {
		return nil, errors.New("full prepared snapshot JSON and SHA-256 are required")
	}
	if got := shaBytes(s.SnapshotJSON); got != s.SnapshotSHA256 {
		return nil, fmt.Errorf("snapshot SHA-256 mismatch: got %s", got)
	}
	kernelPath, err := filepath.EvalSymlinks(s.KernelPath)
	if err != nil {
		return nil, fmt.Errorf("resolve kernel path: %w", err)
	}
	templatePath, err := filepath.EvalSymlinks(s.TemplatePath)
	if err != nil {
		return nil, fmt.Errorf("resolve template path: %w", err)
	}
	kernelPath, _ = filepath.Abs(kernelPath)
	templatePath, _ = filepath.Abs(templatePath)
	kernelRaw, err := os.ReadFile(kernelPath)
	if err != nil {
		return nil, fmt.Errorf("read kernel DLL for identity: %w", err)
	}
	if got := shaBytes(kernelRaw); got != s.KernelSHA256 {
		return nil, fmt.Errorf("kernel DLL SHA-256 mismatch: got %s", got)
	}
	template, err := os.ReadFile(templatePath)
	if err != nil {
		return nil, fmt.Errorf("read prepared native template: %w", err)
	}
	if uint64(len(template)) != uint64(s.NativeBattleSize) {
		return nil, fmt.Errorf("template size %d differs from ABI size %d", len(template), s.NativeBattleSize)
	}
	if got := shaBytes(template); got != s.TemplateSHA256 {
		return nil, fmt.Errorf("template SHA-256 mismatch: got %s", got)
	}
	dll, err := syscall.LoadDLL(kernelPath)
	if err != nil {
		return nil, fmt.Errorf("LoadLibrary canonical kernel: %w", err)
	}
	k := kernelDLL{dll: dll}
	for name, dst := range map[string]**syscall.Proc{
		"ka_battle_clone": &k.clone, "ka_battle_seed_rng": &k.seed, "ka_battle_skip_lib_draws": &k.skip,
		"ka_run_battle": &k.run, "ka_battle_report": &k.report, "ka_battle_stats": &k.stats,
		"ka_battle_checksum": &k.checksum, "ka_battle_free": &k.free,
		"ka_sizeof_battle": &k.battleSize, "ka_sizeof_report": &k.reportSize,
	} {
		p, e := loadProc(dll, name)
		if e != nil {
			_ = dll.Release()
			return nil, e
		}
		*dst = p
	}
	actualBattle, _, _ := k.battleSize.Call()
	actualReport, _, _ := k.reportSize.Call()
	if uint32(actualBattle) != s.NativeBattleSize || uint32(actualReport) != s.NativeReportSize {
		_ = dll.Release()
		return nil, fmt.Errorf("DLL ABI probes (%d,%d) disagree with snapshot (%d,%d)", actualBattle, actualReport, s.NativeBattleSize, s.NativeReportSize)
	}
	e := &WindowsEngine{k: k, settings: s, template: template, provenance: KernelProvenance{
		KernelPath: kernelPath, KernelSHA256: s.KernelSHA256, SnapshotSHA256: s.SnapshotSHA256, TemplateSHA256: s.TemplateSHA256,
		NativeBattleSize: s.NativeBattleSize, NativeReportSize: s.NativeReportSize, TemplateChecksum: s.TemplateChecksum,
		TickLimit: s.TickLimit, PolicyCode: s.PolicyCode, FollowerDraws: s.FollowerDraws,
	}}
	base, _, callErr := k.checksum.Call(uintptr(unsafe.Pointer(&template[0])))
	runtime.KeepAlive(template)
	if callErr != syscall.Errno(0) && base == 0 {
		_ = dll.Release()
		return nil, fmt.Errorf("template checksum call failed: %w", callErr)
	}
	if uint64(base) != s.TemplateChecksum {
		_ = dll.Release()
		return nil, fmt.Errorf("template native checksum mismatch: got %d expected %d", base, s.TemplateChecksum)
	}
	e.arenas = make([]workerArena, cfg.Executors)
	for i := range e.arenas {
		ptr, _, _ := k.clone.Call(uintptr(unsafe.Pointer(&template[0])))
		if ptr == 0 {
			e.releaseArenas()
			_ = dll.Release()
			return nil, fmt.Errorf("worker %d baseline clone failed", i)
		}
		check, _, _ := k.checksum.Call(ptr)
		if uint64(check) != s.TemplateChecksum {
			_, _, _ = k.free.Call(ptr)
			e.releaseArenas()
			_ = dll.Release()
			return nil, fmt.Errorf("worker %d baseline checksum mismatch", i)
		}
		e.arenas[i] = workerArena{baseline: ptr}
	}
	e.jobs = make(chan nativeJob, cfg.Executors*2)
	e.replies = make(chan nativeReply, cfg.Executors*2)
	for i := range e.arenas {
		e.workers.Add(1)
		go e.worker(i)
	}
	return e, nil
}

func (e *WindowsEngine) releaseArenas() {
	for i := range e.arenas {
		if e.arenas[i].baseline != 0 {
			_, _, _ = e.k.free.Call(e.arenas[i].baseline)
			e.arenas[i].baseline = 0
		}
	}
}

func (e *WindowsEngine) worker(id int) {
	defer e.workers.Done()
	for job := range e.jobs {
		r, err := e.runOne(id, job.task)
		e.replies <- nativeReply{index: job.index, result: r, err: err}
	}
}

func (e *WindowsEngine) runOne(worker int, task Task) (BattleResult, error) {
	if task.ID == "" || len(task.Provenance) == 0 {
		return BattleResult{}, errors.New("task identity and provenance are required")
	}
	if task.MathSeed < 0 || task.LibSeed < 0 {
		return BattleResult{}, errors.New("canonical optimizer seeds must be nonnegative signed 31-bit values")
	}
	base := e.arenas[worker].baseline
	ptr, _, _ := e.k.clone.Call(base)
	if ptr == 0 {
		return BattleResult{}, fmt.Errorf("worker %d task clone failed", worker)
	}
	defer func() { _, _, _ = e.k.free.Call(ptr) }()
	checksum, _, _ := e.k.checksum.Call(ptr)
	if uint64(checksum) != e.settings.TemplateChecksum {
		return BattleResult{}, errors.New("task clone does not match prepared template checksum")
	}
	_, _, _ = e.k.seed.Call(ptr, uintptr(uint32(task.MathSeed)), uintptr(uint32(task.LibSeed)))
	if e.settings.FollowerDraws > 0 {
		_, _, _ = e.k.skip.Call(ptr, uintptr(e.settings.FollowerDraws))
	}
	var report [584]byte
	status, _, _ := e.k.run.Call(ptr, uintptr(e.settings.TickLimit), uintptr(uint32(e.settings.PolicyCode)), uintptr(unsafe.Pointer(&report[0])))
	var reread [584]byte
	readStatus, _, _ := e.k.report.Call(ptr, uintptr(uint32(e.settings.PolicyCode)), uintptr(unsafe.Pointer(&reread[0])))
	if readStatus != 0 {
		return BattleResult{}, fmt.Errorf("native report getter returned %d", readStatus)
	}
	if report != reread {
		return BattleResult{}, errors.New("native run report differs from canonical report getter")
	}
	var stats [4]uint64
	statsStatus, _, _ := e.k.stats.Call(ptr, uintptr(unsafe.Pointer(&stats[0])))
	if statsStatus != 0 {
		return BattleResult{}, fmt.Errorf("native stats getter returned %d", statsStatus)
	}
	after, _, _ := e.k.checksum.Call(ptr)
	runtime.KeepAlive(report)
	runtime.KeepAlive(reread)
	runtime.KeepAlive(stats)
	return BattleResult{ID: task.ID, Executor: worker, MathSeed: task.MathSeed, LibSeed: task.LibSeed, Provenance: task.Provenance,
		Status: int32(status), TemplateChecksumBeforeSeed: uint64(checksum), ChecksumAfter: uint64(after), Stats: NativeStats{NotifyCalls: stats[0], SubsetChecks: stats[1], BucketLookups: stats[2], BucketSteps: stats[3]},
		Report: NativeReport{Raw: report, Values: reportValues(report[:])}, Completed: status == 0}, nil
}

func (e *WindowsEngine) RunBatch(tasks []Task) ([]BattleResult, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.closed {
		return nil, errors.New("native engine is closed")
	}
	if len(tasks) == 0 || len(tasks) > maxTasksPerBatch {
		return nil, fmt.Errorf("batch size must be 1..%d", maxTasksPerBatch)
	}
	results := make([]BattleResult, len(tasks))
	go func() {
		for i, t := range tasks {
			e.jobs <- nativeJob{index: i, task: t}
		}
	}()
	var firstErr error
	seen := make([]bool, len(tasks))
	for range tasks {
		reply := <-e.replies
		if reply.index < 0 || reply.index >= len(tasks) {
			if firstErr == nil {
				firstErr = errors.New("worker returned out-of-range task identity")
			}
			continue
		}
		if seen[reply.index] {
			if firstErr == nil {
				firstErr = errors.New("worker returned duplicate task identity")
			}
			continue
		}
		seen[reply.index] = true
		if reply.err != nil {
			if firstErr == nil {
				firstErr = fmt.Errorf("task index %d (%s): %w", reply.index, tasks[reply.index].ID, reply.err)
			}
			continue
		}
		results[reply.index] = reply.result
	}
	if firstErr != nil {
		return nil, firstErr
	}
	return results, nil
}

func (e *WindowsEngine) Provenance() KernelProvenance { return e.provenance }

func (e *WindowsEngine) Close() error {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.closed {
		return nil
	}
	e.closed = true
	close(e.jobs)
	e.workers.Wait()
	close(e.replies)
	e.releaseArenas()
	return e.k.dll.Release()
}

func reportValues(raw []byte) map[string]any {
	fields := []string{"status", "ticks", "verdict", "verdict_tick", "battle_state", "battle_frame", "finish_policy", "ending_gate_tick", "ending_confirmed", "ending_counter", "prize_callbacks", "pre_verdict_prize_callbacks", "unresolved_commands", "pending_projectiles", "active_damage_or_leaving", "math_draws", "lib_draws", "certificate_held", "certificate_frame", "certificate_pending", "late_hold_frame", "pending_at_verdict", "pending_final", "post_certificate_delta", "observations", "verdict_observations"}
	out := make(map[string]any, 95)
	for i, n := range fields {
		out[n] = int32(binary.LittleEndian.Uint32(raw[i*4:]))
	}
	idx := 26
	clauses := make([]int32, 4)
	for i := range clauses {
		clauses[i] = int32(binary.LittleEndian.Uint32(raw[(idx+i)*4:]))
	}
	out["clauses"] = clauses
	names := []string{"boss_hp", "boss_state", "stored_target_holders", "queued_command_holders", "scope_allowed", "heals", "attack_attempts", "survivors"}
	for i, n := range names {
		out[n] = int32(binary.LittleEndian.Uint32(raw[(30+i)*4:]))
	}
	out["own_hp"] = int64(binary.LittleEndian.Uint64(raw[152:]))
	out["own_hp_max"] = int64(binary.LittleEndian.Uint64(raw[160:]))
	for i, n := range []string{"own_present", "resource_uses", "progress_boss", "progress_boss_death_tick", "progress_boss_leaving_tick", "progress_stored_at_death", "progress_stored_targeting_boss_at_death", "progress_stored_target_holders_at_death", "progress_commands_targeting_boss", "progress_commands_targeting_boss_released", "progress_max_stored", "progress_max_targeting_boss", "progress_target_holders_peak", "progress_post_death_reentries", "progress_post_death_leavings", "progress_post_death_prizes", "progress_released_after_death", "progress_released_after_death_targeting_boss", "progress_first_release_after_death", "progress_last_release_after_death", "mp_watch_count"} {
		out[n] = int32(binary.LittleEndian.Uint32(raw[(42+i)*4:]))
	}
	arrays := []struct {
		name string
		off  int
	}{{"mp_identity", 252}, {"mp_min", 260}, {"mp_min_percent", 268}, {"mp_low", 276}, {"mp_first_low_tick", 284}, {"mp_first_low_phase", 292}, {"mp_zero", 300}}
	for _, a := range arrays {
		out[a.name] = []int32{int32(binary.LittleEndian.Uint32(raw[a.off:])), int32(binary.LittleEndian.Uint32(raw[a.off+4:]))}
	}
	for i, n := range []string{"herb_stock_start", "herb_stock_remaining", "herb_max_uses", "herb_use_count", "herb_log_count"} {
		out[n] = int32(binary.LittleEndian.Uint32(raw[(77+i)*4:]))
	}
	for _, a := range []struct {
		name string
		off  int
	}{{"herb_use_tick", 328}, {"herb_use_phase", 392}, {"herb_use_source", 456}, {"herb_use_ok", 520}} {
		v := make([]int32, 16)
		for i := range v {
			v[i] = int32(binary.LittleEndian.Uint32(raw[a.off+i*4:]))
		}
		out[a.name] = v
	}
	return out
}
