package main

// Durable single-writer result journal and resumable optimizer checkpoint.
// The journal is the source of truth: a checkpoint may lag it after a crash,
// but may never claim records that are absent from the fsynced journal.
import (
	"bufio"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"
)

const recordSchema = "ka-go-standalone-result-1"
const checkpointSchema = "ka-go-standalone-checkpoint-1"

// Record is one admitted optimizer evaluation. Candidate, prepared state, and
// result retain their exact canonical source payloads; provenance must include
// engine/mechanics, policy, kernel identity/ABI, and run configuration.
// Intent and seeds are explicit to prevent results becoming detached from the
// request that produced them. Diagnostic records must set Verified=false.
type Record struct {
	Schema               string                     `json:"schema"`
	ID                   string                     `json:"id"`
	Intent               json.RawMessage            `json:"intent"`
	Candidate            json.RawMessage            `json:"candidate"`
	Prepared             json.RawMessage            `json:"prepared"`
	Result               json.RawMessage            `json:"result"`
	Lineage              json.RawMessage            `json:"lineage,omitempty"`
	Seeds                map[string]int64           `json:"seeds"`
	Provenance           map[string]json.RawMessage `json:"provenance"`
	Verified             bool                       `json:"verified"`
	Diagnostic           bool                       `json:"diagnostic"`
	ExecutionMode        string                     `json:"executionMode"`
	TestPurpose          string                     `json:"testPurpose"`
	FinishDiagnostic     bool                       `json:"finishDiagnostic"`
	StrategyEvidence     bool                       `json:"strategyEvidence"`
	ProductionValidation *ProductionValidation      `json:"productionValidation,omitempty"`
	ScoreEligible        bool                       `json:"scoreEligible"`
	IdentityHash         string                     `json:"identityHash"`
}

// CheckpointState is intentionally opaque to the store. The search owner can
// persist RNG state, generation/population, pending work, and counters without
// binding storage to one optimizer implementation revision.
type CheckpointState struct {
	Schema       string          `json:"schema"`
	JournalCount uint64          `json:"journalCount"`
	JournalHash  string          `json:"journalHash"`
	State        json.RawMessage `json:"state"`
}

// StoreTiming reports costs measured around actual store operations. Counts
// distinguish fully written but unsynced records from durable journal records.
type StoreTiming struct {
	SaveCalls           uint64 `json:"saveCalls"`
	IdempotentSaves     uint64 `json:"idempotentSaves"`
	JournalWrites       uint64 `json:"journalWrites"`
	SerializationWallNS uint64 `json:"serializationWallNs"`
	JournalWriteWallNS  uint64 `json:"journalWriteWallNs"`
	JournalSyncWallNS   uint64 `json:"journalSyncWallNs"`
	JournalSyncCount    uint64 `json:"journalSyncCount"`
	DurableCount        uint64 `json:"durableCount"`
	PendingCount        uint64 `json:"pendingCount"`
}

type Store struct {
	mu           sync.Mutex
	dir          string
	journal      *os.File
	writerLock   *os.File
	seen         map[string]string // id -> canonical record digest
	records      map[string]Record
	byIdentity   map[string]Record
	count        uint64 // durable journal records only
	pendingCount uint64 // complete lines written but not yet fsynced
	syncBatch    uint64
	poisoned     error
	timing       StoreTiming
	closed       bool
}

func NewStore(dir string) (*Store, error) {
	return NewStoreWithSyncBatch(dir, 1)
}

// NewStoreWithSyncBatch opts into syncing once each batch complete journal
// records has been written. Checkpoints and Close always force a sync.
func NewStoreWithSyncBatch(dir string, batch int) (*Store, error) {
	if batch < 1 || batch > 4096 {
		return nil, errors.New("journal sync batch must be in 1..4096")
	}
	if err := os.MkdirAll(dir, 0700); err != nil {
		return nil, err
	}
	lock, err := acquireWriterLock(dir)
	if err != nil {
		return nil, err
	}
	path := filepath.Join(dir, "results.jsonl")
	if err := repairTail(path); err != nil {
		_ = releaseWriterLock(lock)
		return nil, err
	}
	s := &Store{dir: dir, writerLock: lock, seen: make(map[string]string), records: make(map[string]Record), byIdentity: make(map[string]Record), syncBatch: uint64(batch)}
	if err := s.replay(path); err != nil {
		_ = releaseWriterLock(lock)
		return nil, err
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
	if err != nil {
		_ = releaseWriterLock(lock)
		return nil, err
	}
	s.journal = f
	return s, nil
}

// repairTail removes only an incomplete final line. A malformed newline-ended
// record is corruption and fails closed during replay.
func repairTail(path string) error {
	f, err := os.OpenFile(path, os.O_RDWR, 0600)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return err
	}
	if info.Size() == 0 {
		return nil
	}
	var last [1]byte
	if _, err = f.ReadAt(last[:], info.Size()-1); err != nil {
		return err
	}
	if last[0] == '\n' {
		return nil
	}
	// Find the last committed newline, keeping even a valid final JSON object
	// without newline out: no fsync-delimited commit boundary exists for it.
	const chunk = 64 * 1024
	pos := info.Size()
	buf := make([]byte, chunk)
	cut := int64(0)
	for pos > 0 {
		n := int64(chunk)
		if pos < n {
			n = pos
		}
		pos -= n
		got, e := f.ReadAt(buf[:n], pos)
		if e != nil && e != io.EOF {
			return e
		}
		if i := bytes.LastIndexByte(buf[:got], '\n'); i >= 0 {
			cut = pos + int64(i) + 1
			break
		}
	}
	if err = f.Truncate(cut); err != nil {
		return err
	}
	return f.Sync()
}

func (s *Store) replay(path string) error {
	f, err := os.Open(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	defer f.Close()
	r := bufio.NewReader(f)
	for line := uint64(1); ; line++ {
		b, e := r.ReadBytes('\n')
		if e == io.EOF && len(b) == 0 {
			return nil
		}
		if e != nil {
			return fmt.Errorf("journal tail after repair at line %d: %w", line, e)
		}
		b = bytes.TrimSpace(b)
		if len(b) == 0 {
			return fmt.Errorf("empty journal record at line %d", line)
		}
		var rec Record
		if err := json.Unmarshal(b, &rec); err != nil {
			return fmt.Errorf("invalid journal record at line %d: %w", line, err)
		}
		if err := validateRecord(&rec); err != nil {
			return fmt.Errorf("invalid journal record at line %d: %w", line, err)
		}
		canon, _ := json.Marshal(rec)
		h := digest(canon)
		if old, ok := s.seen[rec.ID]; ok {
			if old != h {
				return fmt.Errorf("conflicting duplicate journal identity %q at line %d", rec.ID, line)
			}
			return fmt.Errorf("duplicate journal identity %q at line %d", rec.ID, line)
		}
		s.seen[rec.ID] = h
		s.records[rec.ID] = rec
		if prior, exists := s.byIdentity[rec.IdentityHash]; exists {
			return fmt.Errorf("duplicate execution identity %q and %q at line %d", prior.ID, rec.ID, line)
		}
		s.byIdentity[rec.IdentityHash] = rec
		s.count++
	}
}

func validateRecord(r *Record) error {
	if r.Schema == "" {
		r.Schema = recordSchema
	}
	if r.Schema != recordSchema {
		return fmt.Errorf("unsupported schema %q", r.Schema)
	}
	// Older journal rows predate execution-scope metadata. Read them as
	// diagnostic-only rows so a resume never upgrades historical evidence.
	if r.ExecutionMode == "" {
		r.ExecutionMode = "diagnostic"
	}
	if r.TestPurpose == "" {
		r.TestPurpose = "diagnostic"
	}
	if r.ExecutionMode != "diagnostic" && r.ExecutionMode != "production" {
		return fmt.Errorf("unsupported executionMode %q", r.ExecutionMode)
	}
	if r.TestPurpose == "" {
		return errors.New("missing testPurpose")
	}
	if r.ID == "" {
		return errors.New("missing identity")
	}
	for name, raw := range map[string]json.RawMessage{"intent": r.Intent, "candidate": r.Candidate, "prepared": r.Prepared, "result": r.Result} {
		if len(raw) == 0 || !json.Valid(raw) {
			return fmt.Errorf("missing or invalid %s payload", name)
		}
	}
	if len(r.Seeds) == 0 {
		return errors.New("missing explicit seeds")
	}
	if len(r.Provenance) == 0 {
		return errors.New("missing engine/mechanics/policy/kernel provenance")
	}
	if r.Verified && r.Diagnostic {
		return errors.New("diagnostic record cannot be marked verified")
	}
	if r.ScoreEligible && !r.StrategyEvidence {
		return errors.New("score-eligible record must be strategy evidence")
	}
	if (r.StrategyEvidence || r.ScoreEligible) && (r.ExecutionMode != "production" || r.TestPurpose != "production" || r.Diagnostic) {
		return errors.New("diagnostic-scope record cannot be promoted to strategy evidence")
	}
	if len(r.Lineage) > 0 && !json.Valid(r.Lineage) {
		return errors.New("lineage must be valid JSON when provided")
	}
	computed := identityHash(r)
	if r.IdentityHash != "" && r.IdentityHash != computed {
		return errors.New("identity hash does not match record intent, candidate, seeds, provenance, and result")
	}
	r.IdentityHash = computed
	return nil
}

// IdentityHash computes dedup identity from evaluation intent and immutable
// execution provenance as well as candidate and seeds; same candidate under
// different kernels/policies is a distinct evaluation.
func IdentityHash(r Record) string { return identityHash(&r) }
func identityHash(r *Record) string {
	v := struct {
		Intent, Candidate, Prepared json.RawMessage
		Seeds                       map[string]int64
		Provenance                  map[string]json.RawMessage
	}{r.Intent, r.Candidate, r.Prepared, r.Seeds, r.Provenance}
	b, _ := json.Marshal(v)
	return digest(b)
}
func digest(b []byte) string { h := sha256.Sum256(b); return hex.EncodeToString(h[:]) }

func (s *Store) Has(id string) bool { s.mu.Lock(); defer s.mu.Unlock(); _, ok := s.seen[id]; return ok }

// Save serializes all journal writes in this process. Records become visible
// to Has/Records after a complete line write; Count advances only after Sync.
// A repeated exact record is idempotent.
func (s *Store) Save(r Record) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.timing.SaveCalls++
	if s.closed {
		return errors.New("store is closed")
	}
	if s.poisoned != nil {
		return fmt.Errorf("journal is unusable after prior write/sync failure: %w", s.poisoned)
	}
	serializeStart := time.Now()
	if err := validateRecord(&r); err != nil {
		s.timing.SerializationWallNS += uint64(time.Since(serializeStart))
		return err
	}
	b, err := json.Marshal(r)
	s.timing.SerializationWallNS += uint64(time.Since(serializeStart))
	if err != nil {
		return err
	}
	h := digest(b)
	if old, ok := s.seen[r.ID]; ok {
		if old == h {
			s.timing.IdempotentSaves++
			return nil
		}
		return fmt.Errorf("identity %q already exists with different contents", r.ID)
	}
	line := append(b, '\n')
	writeStart := time.Now()
	n, writeErr := s.journal.Write(line)
	s.timing.JournalWriteWallNS += uint64(time.Since(writeStart))
	if writeErr == nil && n != len(line) {
		writeErr = io.ErrShortWrite
	}
	if writeErr != nil {
		s.poisoned = writeErr
		return writeErr
	}
	s.timing.JournalWrites++
	s.seen[r.ID] = h
	s.records[r.ID] = r
	s.byIdentity[r.IdentityHash] = r
	s.pendingCount++
	if s.pendingCount >= s.syncBatch {
		if err = s.syncJournalLocked(); err != nil {
			return err
		}
	}
	return nil
}

func (s *Store) syncJournalLocked() error {
	start := time.Now()
	err := s.journal.Sync()
	s.timing.JournalSyncWallNS += uint64(time.Since(start))
	s.timing.JournalSyncCount++
	if err != nil {
		s.poisoned = err
		return err
	}
	s.count += s.pendingCount
	s.pendingCount = 0
	s.timing.DurableCount = s.count
	// A successful retry, including the final Close sync, resolves an earlier
	// uncertain sync result for the bytes still present in this open journal.
	s.poisoned = nil
	return nil
}

func (s *Store) Stats() StoreTiming {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := s.timing
	out.DurableCount = s.count
	out.PendingCount = s.pendingCount
	return out
}

func (s *Store) Count() uint64 { s.mu.Lock(); defer s.mu.Unlock(); return s.count }

// Records returns a stable identity-sorted snapshot for restoring adaptive
// feedback after a crash that left the checkpoint behind the journal.
func (s *Store) Records() []Record {
	s.mu.Lock()
	defer s.mu.Unlock()
	ids := make([]string, 0, len(s.records))
	for id := range s.records {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	out := make([]Record, 0, len(ids))
	for _, id := range ids {
		out = append(out, s.records[id])
	}
	return out
}

// FindIdentity returns the previously persisted evaluation with exactly the
// same intent, candidate, preparation, seeds, and provenance. Its result can
// be reused for another dispatch without creating duplicate evidence.
func (s *Store) FindIdentity(hash string) (Record, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	r, ok := s.byIdentity[hash]
	return r, ok
}

func (s *Store) SaveCheckpoint(state json.RawMessage) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.closed {
		return errors.New("store is closed")
	}
	if len(state) == 0 || !json.Valid(state) {
		return errors.New("checkpoint state must be valid JSON")
	}
	if s.poisoned != nil {
		return fmt.Errorf("cannot checkpoint after prior journal write/sync failure: %w", s.poisoned)
	}
	if err := s.syncJournalLocked(); err != nil {
		return fmt.Errorf("sync journal before checkpoint: %w", err)
	}
	cp := CheckpointState{Schema: checkpointSchema, JournalCount: s.count, JournalHash: s.journalHashLocked(), State: state}
	b, err := json.Marshal(cp)
	if err != nil {
		return err
	}
	return atomicFile(filepath.Join(s.dir, "checkpoint.json"), append(b, '\n'))
}

func (s *Store) LoadCheckpoint() (json.RawMessage, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	b, err := os.ReadFile(filepath.Join(s.dir, "checkpoint.json"))
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var cp CheckpointState
	if err = json.Unmarshal(b, &cp); err != nil {
		return nil, err
	}
	if cp.Schema != checkpointSchema {
		return nil, fmt.Errorf("unsupported checkpoint schema %q", cp.Schema)
	}
	if cp.JournalCount > s.count {
		return nil, errors.New("checkpoint is ahead of durable journal")
	}
	if cp.JournalCount == s.count && cp.JournalHash != s.journalHashLocked() {
		return nil, errors.New("checkpoint journal digest mismatch")
	}
	if len(cp.State) == 0 || !json.Valid(cp.State) {
		return nil, errors.New("invalid checkpoint state payload")
	}
	return cp.State, nil
}

func (s *Store) journalHashLocked() string {
	// encoding/json sorts string map keys, making this stable without rereading
	// the append-only journal in the hot save path.
	b, _ := json.Marshal(s.seen)
	return digest(b)
}

func atomicFile(path string, data []byte) error {
	tmp := path + ".tmp"
	f, err := os.OpenFile(tmp, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	_, err = f.Write(data)
	if err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err == nil {
		err = closeErr
	}
	if err != nil {
		return err
	}
	if err = os.Rename(tmp, path); err != nil {
		return err
	}
	return nil
}

func (s *Store) Close() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.closed {
		return nil
	}
	s.closed = true
	if s.journal == nil {
		return releaseWriterLock(s.writerLock)
	}
	priorFailure := s.poisoned
	e1 := s.syncJournalLocked()
	e2 := s.journal.Close()
	e3 := releaseWriterLock(s.writerLock)
	if e1 != nil {
		return e1
	}
	if priorFailure != nil {
		return priorFailure
	}
	if e2 != nil {
		return e2
	}
	return e3
}
