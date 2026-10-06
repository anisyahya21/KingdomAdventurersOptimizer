//go:build windows

package main

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"syscall"
	"unsafe"
)

var lockFileExProc = syscall.NewLazyDLL("kernel32.dll").NewProc("LockFileEx")
var unlockFileExProc = syscall.NewLazyDLL("kernel32.dll").NewProc("UnlockFileEx")

const (
	lockfileExclusiveLock   = 0x00000002
	lockfileFailImmediately = 0x00000001
)

// acquireWriterLock uses an OS-managed byte-range lock. The lock file may
// remain after a crash; Windows releases the handle lock automatically, so a
// stale file never blocks recovery or needs unsafe PID guessing.
func acquireWriterLock(dir string) (*os.File, error) {
	f, err := os.OpenFile(filepath.Join(dir, "writer.lock"), os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, err
	}
	var ov syscall.Overlapped
	r1, _, callErr := lockFileExProc.Call(uintptr(f.Fd()), lockfileExclusiveLock|lockfileFailImmediately, 0, 1, 0, uintptr(unsafe.Pointer(&ov)))
	if r1 == 0 {
		_ = f.Close()
		if callErr == syscall.Errno(0) {
			callErr = errors.New("writer lock is already held")
		}
		return nil, fmt.Errorf("optimizer store has another writer: %w", callErr)
	}
	return f, nil
}

func releaseWriterLock(f *os.File) error {
	if f == nil {
		return nil
	}
	var ov syscall.Overlapped
	r1, _, callErr := unlockFileExProc.Call(uintptr(f.Fd()), 0, 1, 0, uintptr(unsafe.Pointer(&ov)))
	closeErr := f.Close()
	if r1 == 0 && callErr != syscall.Errno(0) {
		return callErr
	}
	return closeErr
}
