from __future__ import annotations

import ctypes
import os
import subprocess
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Sequence


@dataclass(frozen=True)
class GuardedProcessResult:
    returncode: int
    elapsed_seconds: float
    log_tail: str


class GuardedProcessError(RuntimeError):
    """Raised after a bounded child process is safely terminated."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class _WindowsJob:
    """Small Windows Job Object wrapper with a whole-process-tree memory cap."""

    _JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

    class _BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _ExtendedLimitInformation(ctypes.Structure):
        pass

    _ExtendedLimitInformation._fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]

    def __init__(self, process: subprocess.Popen[bytes], memory_limit_bytes: int):
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        self._kernel32 = kernel32
        self._handle = handle
        try:
            limits = self._ExtendedLimitInformation()
            limits.BasicLimitInformation.LimitFlags = (
                self._JOB_OBJECT_LIMIT_JOB_MEMORY
                | self._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            limits.JobMemoryLimit = max(1, int(memory_limit_bytes))
            if not kernel32.SetInformationJobObject(
                handle,
                self._JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
            ):
                raise OSError(
                    ctypes.get_last_error(), "SetInformationJobObject failed"
                )
            process_handle = wintypes.HANDLE(int(process._handle))  # type: ignore[attr-defined]
            if not kernel32.AssignProcessToJobObject(handle, process_handle):
                raise OSError(
                    ctypes.get_last_error(), "AssignProcessToJobObject failed"
                )
        except Exception:
            self.close()
            raise

    def terminate(self, exit_code: int = 1) -> None:
        if self._handle:
            self._kernel32.TerminateJobObject(self._handle, max(1, int(exit_code)))

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


def _tail(path: Path, *, limit: int = 4000) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - max(1, int(limit))))
            return stream.read().decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def run_guarded_process(
    command: Sequence[str],
    *,
    cwd: str | Path,
    log_path: str | Path,
    timeout_seconds: int,
    memory_limit_mb: int,
    max_log_mb: int = 32,
) -> GuardedProcessResult:
    """Run a child with bounded logs, wall time, and Windows tree memory.

    DataTube is Windows-first. A Job Object is mandatory there so a worker and
    all descendants are terminated together if they exceed their combined cap.
    """

    timeout_seconds = max(1, int(timeout_seconds))
    memory_limit_mb = max(256, int(memory_limit_mb))
    max_log_bytes = max(1, int(max_log_mb)) * 1024 * 1024
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    job: _WindowsJob | None = None
    process: subprocess.Popen[bytes] | None = None
    log_stream: IO[bytes] | None = None
    try:
        if log_path.exists():
            previous = log_path.with_name(f"{log_path.stem}.previous{log_path.suffix}")
            try:
                log_path.replace(previous)
            except OSError:
                # A stale diagnostic must never prevent a new bounded worker.
                pass
        log_stream = log_path.open("wb")
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
        process = subprocess.Popen(
            [str(item) for item in command],
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=log_stream,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
        if os.name == "nt":
            try:
                job = _WindowsJob(process, memory_limit_mb * 1024 * 1024)
            except Exception as exc:
                process.terminate()
                process.wait(timeout=10)
                raise GuardedProcessError(
                    "PROCESS_GUARD_UNAVAILABLE",
                    f"could not apply the worker memory guard: {exc}",
                ) from exc

        deadline = started + timeout_seconds
        while process.poll() is None:
            try:
                log_size = log_path.stat().st_size
            except OSError:
                log_size = 0
            if log_size > max_log_bytes:
                if job is not None:
                    job.terminate(125)
                else:
                    process.kill()
                process.wait(timeout=10)
                raise GuardedProcessError(
                    "PROCESS_LOG_LIMIT",
                    f"worker log exceeded its {max_log_mb} MiB safety limit",
                )
            if time.monotonic() >= deadline:
                if job is not None:
                    job.terminate(124)
                else:
                    process.kill()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise GuardedProcessError(
                    "PROCESS_TIMEOUT",
                    f"worker exceeded its {timeout_seconds}s time limit",
                )
            time.sleep(0.25)

        elapsed = time.monotonic() - started
        log_stream.flush()
        tail = _tail(log_path)
        if process.returncode != 0:
            raise GuardedProcessError(
                "PROCESS_RESOURCE_LIMIT",
                f"worker exited with code {process.returncode} under a "
                f"{memory_limit_mb} MiB memory cap: {tail[-2000:]}",
            )
        return GuardedProcessResult(process.returncode, elapsed, tail)
    finally:
        if job is not None:
            job.close()
        if log_stream is not None:
            log_stream.close()


__all__ = ["GuardedProcessError", "GuardedProcessResult", "run_guarded_process"]
