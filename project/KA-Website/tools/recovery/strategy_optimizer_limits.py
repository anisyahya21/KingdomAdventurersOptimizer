"""Limits apply only to optimiser-owned worker processes."""
import os

WORKER_MEMORY_MB = 768
RUN_TIMEOUT_SECONDS = 180

# Worker/duty envelope. These are throughput settings, not correctness settings: every worker runs
# the same canonical engine and the same adapter, so raising them can only change how much of the
# machine one search uses.
#
# Measured on the development machine (i7-14700K, 20 cores / 28 logical, PyPy 3.11.15, 7000-tick
# encounter, 4 timed battles per worker; see TEMP/deepastra-strategy-ui/pool-scaling.json):
#
#   workers   1     2     4     8    12    16    20    24
#   battles/s 0.65  1.28  2.19  3.02  4.52  4.22  4.43  4.69
#
# The curve flattens at ~12 workers, so 12 is the default target rather than "every CPU": past the
# knee the extra processes only add memory and heat. The ceiling keeps four logical CPUs free for
# the desktop, its WebView and the rest of the user's machine.
MAX_WORKERS = 24
THROUGHPUT_KNEE_WORKERS = 12
MIN_DUTY = .1
MAX_DUTY = 1.
DEFAULT_DUTY = .9

_job = None


def worker_ceiling(cpus=None):
    """Most workers this machine should run concurrently."""
    cpus = cpus or os.cpu_count() or 2
    return max(1, min(MAX_WORKERS, cpus - 4))


def default_workers(cpus=None):
    """The measured throughput knee, bounded by this machine's ceiling."""
    return max(1, min(THROUGHPUT_KNEE_WORKERS, worker_ceiling(cpus)))


def clamp_workers(value, cpus=None):
    """The requested worker count inside this machine's allowed range."""
    try:
        requested = int(value)
    except (TypeError, ValueError):
        requested = default_workers(cpus)
    return max(1, min(worker_ceiling(cpus), requested))


def clamp_duty(value):
    """The requested CPU duty cycle inside 0.10-1.00; 1.00 means no deliberate idle."""
    try:
        requested = float(value)
    except (TypeError, ValueError):
        return DEFAULT_DUTY
    return max(MIN_DUTY, min(MAX_DUTY, requested))


def initialize_worker():
    global _job
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes as w

        class Basic(ctypes.Structure):
            _fields_ = [('processTime', ctypes.c_longlong), ('jobTime', ctypes.c_longlong),
                        ('flags', w.DWORD), ('minWorking', ctypes.c_size_t),
                        ('maxWorking', ctypes.c_size_t), ('active', w.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', w.DWORD), ('scheduling', w.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ('reads', 'writes', 'other', 'readBytes', 'writeBytes', 'otherBytes')]

        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', IO), ('processMemory', ctypes.c_size_t),
                        ('jobMemory', ctypes.c_size_t), ('peakProcess', ctypes.c_size_t),
                        ('peakJob', ctypes.c_size_t)]

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateJobObjectW.restype = w.HANDLE
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        kernel.GetCurrentProcess.restype = w.HANDLE
        kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        kernel.SetPriorityClass.argtypes = [w.HANDLE, w.DWORD]
        _job = kernel.CreateJobObjectW(None, None)
        limits = Extended()
        limits.basic.flags = 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
        limits.processMemory = WORKER_MEMORY_MB * 1024 * 1024
        if not _job or not kernel.SetInformationJobObject(_job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise OSError(ctypes.get_last_error(), 'Could not set worker memory limit')
        if not kernel.AssignProcessToJobObject(_job, kernel.GetCurrentProcess()):
            raise OSError(ctypes.get_last_error(), 'Could not apply worker memory limit')
        kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x4000)  # below normal
    else:
        import resource
        limit = WORKER_MEMORY_MB * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def terminate_pool(pool):
    if hasattr(pool, 'terminate_workers'):
        pool.terminate_workers()
    else:
        # Python 3.10-3.13 compatibility: these are exclusively this executor's children.
        for process in list(pool._processes.values()):
            process.terminate()
        pool.shutdown(wait=True, cancel_futures=True)
