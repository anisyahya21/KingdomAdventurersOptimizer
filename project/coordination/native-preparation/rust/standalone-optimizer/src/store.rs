//! Append-only durable result ledger with deduplication and atomic checkpoints.
//! One process owns a store at a time through a crash-released OS file lock.

use serde_json::Value;
use std::collections::BTreeMap;
use std::collections::HashMap;
use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::time::Instant;

#[derive(Default)]
struct WriterTelemetry {
    serialization_ns: u64,
    write_all_ns: u64,
    sync_all_ns: u64,
    accepted_records: u64,
    attempted_records: u64,
    deduped_records: u64,
    successful_batches: u64,
    sync_calls: u64,
    successful_syncs: u64,
    bytes_written: u64,
    batch_size_histogram: BTreeMap<usize, u64>,
}

pub struct Store {
    lock_file: File,
    writer: File,
    records: HashMap<String, Value>,
    poisoned: bool,
    telemetry_enabled: bool,
    telemetry: WriterTelemetry,
}

impl Store {
    pub fn open(path: &Path) -> Result<Self, String> {
        let path = path.to_path_buf();
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).map_err(|e| format!("create store directory: {e}"))?;
        }
        let lock_path = PathBuf::from(format!("{}.lock", path.display()));
        let lock_file = acquire_lock(&lock_path)?;

        let opened = (|| -> Result<(File, HashMap<String, Value>), String> {
            let mut records = HashMap::new();
            if path.exists() {
                let f = File::open(&path).map_err(|e| format!("open ledger: {e}"))?;
                let mut valid_bytes = 0u64;
                let mut reader = BufReader::new(f);
                let mut index = 0usize;
                loop {
                    let mut bytes = Vec::new();
                    let n = reader
                        .read_until(b'\n', &mut bytes)
                        .map_err(|e| format!("read ledger line {}: {e}", index + 1))?;
                    if n == 0 {
                        break;
                    }
                    index += 1;
                    let terminated = bytes.last() == Some(&b'\n');
                    if !terminated {
                        // A crash during the last write may leave one incomplete JSONL
                        // fragment. It is safe to drop only this unterminated suffix.
                        break;
                    }
                    bytes.pop();
                    let line = String::from_utf8(bytes)
                        .map_err(|e| format!("ledger line {index} is not UTF-8: {e}"))?;
                    valid_bytes += n as u64;
                    if line.trim().is_empty() {
                        continue;
                    }
                    let record: Value = serde_json::from_str(&line)
                        .map_err(|e| format!("malformed ledger line {index}: {e}"))?;
                    let key = record
                        .get("dedupKey")
                        .and_then(Value::as_str)
                        .ok_or_else(|| format!("ledger line {index} lacks string dedupKey"))?;
                    if let Some(existing) = records.get(key) {
                        if existing != &record {
                            return Err(format!(
                                "conflicting duplicate ledger identity at line {index}"
                            ));
                        }
                    } else {
                        records.insert(key.to_owned(), record);
                    }
                }
                let file = OpenOptions::new()
                    .write(true)
                    .open(&path)
                    .map_err(|e| format!("open ledger for recovery: {e}"))?;
                if file
                    .metadata()
                    .map_err(|e| format!("stat ledger: {e}"))?
                    .len()
                    != valid_bytes
                {
                    file.set_len(valid_bytes)
                        .map_err(|e| format!("truncate incomplete ledger tail: {e}"))?;
                    file.sync_all()
                        .map_err(|e| format!("sync recovered ledger: {e}"))?;
                }
            }
            let writer = OpenOptions::new()
                .create(true)
                .append(true)
                .open(&path)
                .map_err(|e| format!("open ledger writer: {e}"))?;
            Ok((writer, records))
        })();
        match opened {
            Ok((writer, records)) => Ok(Self {
                lock_file,
                writer,
                records,
                poisoned: false,
                telemetry_enabled: false,
                telemetry: WriterTelemetry::default(),
            }),
            Err(e) => Err(e),
        }
    }

    pub fn contains(&self, key: &str) -> bool {
        self.records.contains_key(key)
    }

    pub fn get(&self, key: &str) -> Option<&Value> {
        self.records.get(key)
    }

    pub fn records(&self) -> impl Iterator<Item = (&str, &Value)> {
        self.records
            .iter()
            .map(|(key, value)| (key.as_str(), value))
    }

    /// Opt in to serial-writer timing. Calls made before enabling remain
    /// unmeasured, and the default append path performs no clock reads.
    pub fn enable_telemetry(&mut self) {
        self.telemetry_enabled = true;
    }

    /// Snapshot counters for durable inclusion in the caller's stage/report file.
    /// Operation timings are non-overlapping and include failed calls.
    pub fn telemetry_snapshot(&self) -> Value {
        serde_json::json!({
            "schema": "ka-rust-writer-telemetry-v1",
            "timingScope": "serial writer operation wall durations; non-overlapping; includes failed calls",
            "enabled": self.telemetry_enabled,
            "serializationNanoseconds": self.telemetry.serialization_ns,
            "writeAllNanoseconds": self.telemetry.write_all_ns,
            "syncAllNanoseconds": self.telemetry.sync_all_ns,
            "attemptedRecords": self.telemetry.attempted_records,
            "dedupedRecords": self.telemetry.deduped_records,
            "acceptedRecords": self.telemetry.accepted_records,
            "syncCalls": self.telemetry.sync_calls,
            "syncCallSemantics": "attempted sync_all invocations, including failures",
            "successfulSyncs": self.telemetry.successful_syncs,
            "successfulBatches": self.telemetry.successful_batches,
            "batchSizeHistogram": self.telemetry.batch_size_histogram,
            "bytesWritten": self.telemetry.bytes_written,
            "bytesWrittenSemantics": "bytes in write_all calls that returned success; may precede a failed sync",
        })
    }

    /// Append one complete record. `record` must carry its full scenario intent,
    /// seed list, mechanics/policy/kernel provenance and result fields. The store
    /// injects `dedupKey`, fsyncs each accepted record, and never overwrites history.
    pub fn append(&mut self, key: &str, record: &Value) -> Result<(), String> {
        self.append_batch(&[(key.to_owned(), record.clone())])
    }

    /// Append a wave with one flush/sync. A process crash during the write leaves
    /// complete newline-terminated records as a recoverable prefix; only the final
    /// incomplete fragment is discarded when reopening the ledger.
    pub fn append_batch(&mut self, records: &[(String, Value)]) -> Result<(), String> {
        if self.telemetry_enabled {
            self.telemetry.attempted_records = self
                .telemetry
                .attempted_records
                .saturating_add(records.len() as u64);
        }
        if self.poisoned {
            return Err(
                "ledger writer is poisoned after a prior I/O failure; reopen to resume".into(),
            );
        }
        let mut buffer = Vec::new();
        let mut accepted = Vec::new();
        let mut batch_keys = std::collections::HashSet::new();
        for (key, record) in records {
            if key.is_empty() {
                return Err("dedup key must not be empty".into());
            }
            if self.records.contains_key(key) || !batch_keys.insert(key.clone()) {
                if self.telemetry_enabled {
                    self.telemetry.deduped_records =
                        self.telemetry.deduped_records.saturating_add(1);
                }
                continue;
            }
            let mut value = record.clone();
            let object = value
                .as_object_mut()
                .ok_or("record must be a JSON object")?;
            if object
                .get("dedupKey")
                .and_then(Value::as_str)
                .is_some_and(|existing| existing != key)
            {
                return Err("record dedupKey conflicts with append key".into());
            }
            object.insert("dedupKey".into(), Value::String(key.clone()));
            let encoded = if self.telemetry_enabled {
                let started = Instant::now();
                let result = serde_json::to_vec(&value);
                self.telemetry.serialization_ns = self
                    .telemetry
                    .serialization_ns
                    .saturating_add(duration_ns(started.elapsed()));
                result
            } else {
                serde_json::to_vec(&value)
            }
            .map_err(|e| format!("serialize record: {e}"))?;
            buffer.extend_from_slice(&encoded);
            buffer.push(b'\n');
            accepted.push((key.clone(), value));
        }
        if accepted.is_empty() {
            return Ok(());
        }
        let write_result = if self.telemetry_enabled {
            let started = Instant::now();
            let result = self.writer.write_all(&buffer);
            self.telemetry.write_all_ns = self
                .telemetry
                .write_all_ns
                .saturating_add(duration_ns(started.elapsed()));
            result
        } else {
            self.writer.write_all(&buffer)
        };
        if let Err(e) = write_result {
            self.poisoned = true;
            return Err(format!("append ledger batch: {e}"));
        }
        if self.telemetry_enabled {
            self.telemetry.bytes_written = self
                .telemetry
                .bytes_written
                .saturating_add(buffer.len() as u64);
        }
        let sync_result = if self.telemetry_enabled {
            let started = Instant::now();
            let result = self.writer.sync_all();
            self.telemetry.sync_all_ns = self
                .telemetry
                .sync_all_ns
                .saturating_add(duration_ns(started.elapsed()));
            self.telemetry.sync_calls = self.telemetry.sync_calls.saturating_add(1);
            result
        } else {
            self.writer.sync_all()
        };
        if let Err(e) = sync_result {
            self.poisoned = true;
            return Err(format!("sync ledger batch: {e}"));
        }
        if self.telemetry_enabled {
            self.telemetry.accepted_records = self
                .telemetry
                .accepted_records
                .saturating_add(accepted.len() as u64);
            self.telemetry.successful_syncs = self.telemetry.successful_syncs.saturating_add(1);
            self.telemetry.successful_batches = self.telemetry.successful_batches.saturating_add(1);
            let count = self
                .telemetry
                .batch_size_histogram
                .entry(accepted.len())
                .or_insert(0);
            *count = count.saturating_add(1);
        }
        for (key, value) in accepted {
            self.records.insert(key, value);
        }
        Ok(())
    }

    /// Write a resumable snapshot through a sibling temporary file and atomic rename.
    pub fn checkpoint(path: &Path, state: &Value) -> Result<(), String> {
        let parent = path.parent().unwrap_or_else(|| Path::new("."));
        fs::create_dir_all(parent).map_err(|e| format!("create checkpoint directory: {e}"))?;
        let temp = PathBuf::from(format!("{}.tmp-{}", path.display(), std::process::id()));
        let result = (|| -> Result<(), String> {
            let mut f = OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&temp)
                .map_err(|e| format!("create checkpoint temporary: {e}"))?;
            serde_json::to_writer(&mut f, state)
                .map_err(|e| format!("serialize checkpoint: {e}"))?;
            f.write_all(b"\n")
                .map_err(|e| format!("finish checkpoint: {e}"))?;
            f.sync_all().map_err(|e| format!("sync checkpoint: {e}"))?;
            fs::rename(&temp, path).map_err(|e| format!("install checkpoint: {e}"))?;
            Ok(())
        })();
        if result.is_err() {
            let _ = fs::remove_file(&temp);
        }
        result
    }

    pub fn read_checkpoint(path: &Path) -> Result<Value, String> {
        let file = File::open(path).map_err(|e| format!("open checkpoint: {e}"))?;
        serde_json::from_reader(file).map_err(|e| format!("parse checkpoint: {e}"))
    }
}

fn duration_ns(duration: std::time::Duration) -> u64 {
    duration.as_nanos().min(u64::MAX as u128) as u64
}

impl Drop for Store {
    fn drop(&mut self) {
        release_lock(&self.lock_file);
    }
}

// Kernel-managed locks are released automatically if the process crashes, so a
// stale lock sidecar never blocks resume. The empty sidecar is intentionally kept.
#[cfg(windows)]
fn acquire_lock(path: &Path) -> Result<File, String> {
    use std::ffi::c_void;
    use std::os::windows::io::AsRawHandle;
    #[repr(C)]
    struct Overlapped {
        internal: usize,
        internal_high: usize,
        offset: u32,
        offset_high: u32,
        event: *mut c_void,
    }
    #[link(name = "kernel32")]
    extern "system" {
        fn LockFileEx(
            file: *mut c_void,
            flags: u32,
            reserved: u32,
            low: u32,
            high: u32,
            overlapped: *mut Overlapped,
        ) -> i32;
    }
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .open(path)
        .map_err(|e| format!("open store lock: {e}"))?;
    let mut overlapped = Overlapped {
        internal: 0,
        internal_high: 0,
        offset: 0,
        offset_high: 0,
        event: std::ptr::null_mut(),
    };
    let ok = unsafe {
        LockFileEx(
            file.as_raw_handle().cast(),
            0x0000_0003,
            0,
            u32::MAX,
            u32::MAX,
            &mut overlapped,
        )
    };
    if ok == 0 {
        return Err(format!(
            "store is locked by another writer: {}",
            std::io::Error::last_os_error()
        ));
    }
    Ok(file)
}

#[cfg(windows)]
fn release_lock(file: &File) {
    use std::ffi::c_void;
    use std::os::windows::io::AsRawHandle;
    #[repr(C)]
    struct Overlapped {
        internal: usize,
        internal_high: usize,
        offset: u32,
        offset_high: u32,
        event: *mut c_void,
    }
    #[link(name = "kernel32")]
    extern "system" {
        fn UnlockFileEx(
            file: *mut c_void,
            reserved: u32,
            low: u32,
            high: u32,
            overlapped: *mut Overlapped,
        ) -> i32;
    }
    let mut overlapped = Overlapped {
        internal: 0,
        internal_high: 0,
        offset: 0,
        offset_high: 0,
        event: std::ptr::null_mut(),
    };
    unsafe {
        let _ = UnlockFileEx(
            file.as_raw_handle().cast(),
            0,
            u32::MAX,
            u32::MAX,
            &mut overlapped,
        );
    }
}

#[cfg(unix)]
fn acquire_lock(path: &Path) -> Result<File, String> {
    use std::os::fd::AsRawFd;
    extern "C" {
        fn flock(fd: i32, operation: i32) -> i32;
    }
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .open(path)
        .map_err(|e| format!("open store lock: {e}"))?;
    if unsafe { flock(file.as_raw_fd(), 2 | 4) } != 0 {
        return Err(format!(
            "store is locked by another writer: {}",
            std::io::Error::last_os_error()
        ));
    }
    Ok(file)
}

#[cfg(unix)]
fn release_lock(file: &File) {
    use std::os::fd::AsRawFd;
    extern "C" {
        fn flock(fd: i32, operation: i32) -> i32;
    }
    unsafe {
        let _ = flock(file.as_raw_fd(), 8);
    }
}

#[cfg(not(any(windows, unix)))]
fn acquire_lock(_path: &Path) -> Result<File, String> {
    Err("crash-recoverable store locking is unsupported on this platform".into())
}

#[cfg(not(any(windows, unix)))]
fn release_lock(_file: &File) {}
