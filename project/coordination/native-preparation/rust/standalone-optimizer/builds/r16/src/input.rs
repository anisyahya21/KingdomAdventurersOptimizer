//! Read source candidates with the Windows SQLite library; preparation remains in Rust.
use serde_json::Value;
use std::{
    ffi::{c_char, c_void, CString},
    path::Path,
};
type Ptr = *mut c_void;
#[link(name = "kernel32")]
extern "system" {
    fn LoadLibraryW(name: *const u16) -> Ptr;
    fn GetProcAddress(module: Ptr, name: *const c_char) -> Ptr;
    fn FreeLibrary(module: Ptr) -> i32;
}
type Open = unsafe extern "C" fn(*const c_char, *mut Ptr, i32, *const c_char) -> i32;
type Close = unsafe extern "C" fn(Ptr) -> i32;
type Prepare = unsafe extern "C" fn(Ptr, *const c_char, i32, *mut Ptr, *mut *const c_char) -> i32;
type Step = unsafe extern "C" fn(Ptr) -> i32;
type Text = unsafe extern "C" fn(Ptr, i32) -> *const u8;
type Bytes = unsafe extern "C" fn(Ptr, i32) -> i32;
type Finalize = unsafe extern "C" fn(Ptr) -> i32;
type Error = unsafe extern "C" fn(Ptr) -> *const c_char;
unsafe fn sym<T: Copy>(module: Ptr, name: &[u8]) -> Result<T, String> {
    let p = GetProcAddress(module, name.as_ptr().cast());
    if p.is_null() {
        return Err(format!(
            "Windows SQLite lacks {}",
            String::from_utf8_lossy(name)
        ));
    }
    Ok(std::mem::transmute_copy(&p))
}
struct Module(Ptr);
impl Drop for Module {
    fn drop(&mut self) {
        unsafe {
            FreeLibrary(self.0);
        }
    }
}
struct Connection {
    ptr: Ptr,
    close: Close,
}
impl Drop for Connection {
    fn drop(&mut self) {
        unsafe {
            (self.close)(self.ptr);
        }
    }
}
struct Statement {
    ptr: Ptr,
    finalize: Finalize,
}
impl Drop for Statement {
    fn drop(&mut self) {
        unsafe {
            (self.finalize)(self.ptr);
        }
    }
}
pub fn load_candidates(path: &Path, encounter: Option<i64>, limit: usize) -> Result<Value, String> {
    Ok(load_with_identity(path, encounter, limit)?.0)
}
pub fn load_with_identity(
    path: &Path,
    encounter: Option<i64>,
    limit: usize,
) -> Result<(Value, Value), String> {
    if !(1..=100_000).contains(&limit) {
        return Err("candidate admission limit must be 1..100000".into());
    }
    if encounter.is_some_and(|id| !(0..20).contains(&id)) {
        return Err("encounter filter outside 0..19".into());
    }
    let path = std::fs::canonicalize(path).map_err(|e| e.to_string())?;
    let filename = CString::new(path.to_str().ok_or("SQLite path is not Unicode")?)
        .map_err(|e| e.to_string())?;
    let library: Vec<u16> = "winsqlite3.dll".encode_utf16().chain(Some(0)).collect();
    unsafe {
        let module = Module(LoadLibraryW(library.as_ptr()));
        if module.0.is_null() {
            return Err("Windows SQLite library unavailable".into());
        }
        let open: Open = sym(module.0, b"sqlite3_open_v2\0")?;
        let close: Close = sym(module.0, b"sqlite3_close\0")?;
        let prepare: Prepare = sym(module.0, b"sqlite3_prepare_v2\0")?;
        let step: Step = sym(module.0, b"sqlite3_step\0")?;
        let text: Text = sym(module.0, b"sqlite3_column_text\0")?;
        let bytes: Bytes = sym(module.0, b"sqlite3_column_bytes\0")?;
        let finalize: Finalize = sym(module.0, b"sqlite3_finalize\0")?;
        let error: Error = sym(module.0, b"sqlite3_errmsg\0")?;
        let mut db = std::ptr::null_mut();
        let status = open(filename.as_ptr(), &mut db, 1, std::ptr::null());
        if db.is_null() {
            return Err(format!("SQLite readonly open failed ({status})"));
        }
        let db = Connection { ptr: db, close };
        let errmsg = || {
            std::ffi::CStr::from_ptr(error(db.ptr))
                .to_string_lossy()
                .into_owned()
        };
        if status != 0 {
            return Err(format!("SQLite readonly open: {}", errmsg()));
        }
        let filter = encounter
            .map(|id| format!(" WHERE m.encounter={id}"))
            .unwrap_or_default();
        let sql=CString::new(format!("SELECT c.scenario,c.id FROM candidate c JOIN candidate_meta m ON m.id=c.id{filter} ORDER BY c.id LIMIT {limit}" )).map_err(|e|e.to_string())?;
        let mut stmt = std::ptr::null_mut();
        if prepare(db.ptr, sql.as_ptr(), -1, &mut stmt, std::ptr::null_mut()) != 0 {
            return Err(format!("SQLite prepare: {}", errmsg()));
        }
        let stmt = Statement {
            ptr: stmt,
            finalize,
        };
        let mut result = Vec::new();
        let mut identities = Vec::new();
        loop {
            match step(stmt.ptr) {
                100 => {
                    let n = bytes(stmt.ptr, 0);
                    let p = text(stmt.ptr, 0);
                    if n < 0 || n > 16 * 1024 * 1024 || p.is_null() {
                        return Err("invalid or oversized scenario JSON cell".into());
                    }
                    result.push(
                        serde_json::from_slice(std::slice::from_raw_parts(p, n as usize))
                            .map_err(|e| format!("invalid source scenario: {e}"))?,
                    );
                    let id_bytes = bytes(stmt.ptr, 1);
                    let id = text(stmt.ptr, 1);
                    if id_bytes < 1 || id_bytes > 4096 || id.is_null() {
                        return Err("invalid source candidate identity".into());
                    }
                    identities.push(Value::String(
                        std::str::from_utf8(std::slice::from_raw_parts(id, id_bytes as usize))
                            .map_err(|e| e.to_string())?
                            .to_owned(),
                    ));
                }
                101 => break,
                status => return Err(format!("SQLite step {status}: {}", errmsg())),
            }
        }
        Ok((Value::Array(result), Value::Array(identities)))
    }
}
