"""One host-local process-liveness fact, read without signalling or control.

Windows liveness has bitten this project twice, in opposite directions, and
both readings came from probes that looked obviously correct:

* ``os.kill(pid, 0)`` is not an existence probe here. For a process that has
  exited while some handle to it is still open it raises nothing at all and so
  reads as **alive**; for a PID that never existed it raises a bare ``OSError``
  (``WinError 87``, "The parameter is incorrect") rather than
  ``ProcessLookupError``, so a caller that catches only the documented POSIX
  exceptions lets that escape.
* A bare ``OpenProcess`` handle is not liveness either. Windows keeps the
  process object alive while any handle to it remains open, so ``OpenProcess``
  succeeds for a process that has already exited.

Only the recorded exit code separates the two states, so this module opens a
query-only handle and asks ``GetExitCodeProcess``. ``STILL_ACTIVE`` (259) is
the documented sentinel for "has not exited"; a process that genuinely exits
with code 259 is indistinguishable from a running one, and that ambiguity is
inherent to the Win32 contract rather than something this module can remove.

``PROCESS_QUERY_LIMITED_INFORMATION`` is read-only: it cannot start, signal,
terminate, or otherwise control the process being observed. That is why the
read-only dashboard is allowed to use this module.

On POSIX the probe keeps the platform's own meaning: ``ProcessLookupError``
means no such process, and ``PermissionError`` (``EPERM``) means the process
exists but belongs to another user — which is still alive.
"""

from __future__ import annotations

import ctypes
import os

WINDOWS_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _windows_pid_alive(pid: int) -> bool:
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = (ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32)
        open_process.restype = ctypes.c_void_p
        get_exit_code = kernel32.GetExitCodeProcess
        get_exit_code.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32))
        get_exit_code.restype = ctypes.c_bool
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (ctypes.c_void_p,)
        close_handle.restype = ctypes.c_bool
        handle = open_process(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    except OSError:
        return False
    if not handle:
        return False
    try:
        code = ctypes.c_uint32(0)
        if not get_exit_code(handle, ctypes.byref(code)):
            return False
        return code.value == WINDOWS_STILL_ACTIVE
    finally:
        close_handle(handle)


def pid_alive(pid: object) -> bool:
    """Return whether ``pid`` names a process that has not yet exited.

    Accepts an arbitrary object because callers read PIDs out of JSON state
    files, where a missing value is ``None`` and a malformed one may be any
    type. Anything that is not a positive integer is not a live process.
    ``bool`` is rejected explicitly: ``True`` is an ``int`` equal to 1, and
    PID 1 is a real process.
    """
    if isinstance(pid, bool) or not isinstance(pid, int) or pid < 1:
        return False
    if os.name == "nt":
        return _windows_pid_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True
