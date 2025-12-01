"""
Utility to close the Roblox singleton event handle so multiple clients can launch.

The script enumerates all open system handles, finds those whose object name
matches ROBLOX_singletonEvent, and duplicates them with DUPLICATE_CLOSE_SOURCE
to release the lock held by the Roblox process.

Run this script from an elevated PowerShell or Command Prompt to ensure the
necessary access rights.
"""

from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import wintypes
from typing import Iterable, Optional


NTSTATUS = ctypes.c_long


class SYSTEM_INFORMATION_CLASS:
    SystemExtendedHandleInformation = 64


class OBJECT_INFORMATION_CLASS:
    ObjectNameInformation = 1
    ObjectTypeInformation = 2


STATUS_SUCCESS = 0x00000000
STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
STATUS_BUFFER_OVERFLOW = 0x80000005

DUPLICATE_CLOSE_SOURCE = 0x00000001
DUPLICATE_SAME_ACCESS = 0x00000002

PROCESS_DUP_HANDLE = 0x00000040
PROCESS_QUERY_LIMITED_INFORMATION = 0x00001000

TARGET_EVENT_NAME = "ROBLOX_singletonEvent"

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll")

SIZE_T = ctypes.c_size_t
ULONG_PTR = ctypes.c_ulonglong
HANDLE = wintypes.HANDLE


class SYSTEM_HANDLE_INFORMATION_EX(ctypes.Structure):
    _fields_ = [
        ("NumberOfHandles", ctypes.c_ulonglong),
        ("Reserved", ctypes.c_ulonglong),
    ]


class SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX(ctypes.Structure):
    _fields_ = [
        ("Object", ctypes.c_void_p),
        ("UniqueProcessId", ctypes.c_ulonglong),
        ("HandleValue", ctypes.c_void_p),
        ("GrantedAccess", ctypes.c_ulong),
        ("CreatorBackTraceIndex", ctypes.c_ushort),
        ("ObjectTypeIndex", ctypes.c_ushort),
        ("HandleAttributes", ctypes.c_ulong),
        ("Reserved", ctypes.c_ulong),
    ]


class UNICODE_STRING(ctypes.Structure):
    _fields_ = [
        ("Length", ctypes.c_ushort),
        ("MaximumLength", ctypes.c_ushort),
        ("Buffer", ctypes.c_wchar_p),
    ]


class OBJECT_NAME_INFORMATION(ctypes.Structure):
    _fields_ = [("Name", UNICODE_STRING)]


ntdll.NtQuerySystemInformation.argtypes = [
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.c_ulong,
    ctypes.POINTER(ctypes.c_ulong),
]
ntdll.NtQuerySystemInformation.restype = NTSTATUS

ntdll.NtQueryObject.argtypes = [
    HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.c_ulong,
    ctypes.POINTER(ctypes.c_ulong),
]
ntdll.NtQueryObject.restype = NTSTATUS

kernel32.OpenProcess.argtypes = [
    ctypes.c_ulong,
    wintypes.BOOL,
    wintypes.DWORD,
]
kernel32.OpenProcess.restype = HANDLE

kernel32.CloseHandle.argtypes = [HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL

kernel32.DuplicateHandle.argtypes = [
    HANDLE,
    HANDLE,
    HANDLE,
    ctypes.POINTER(HANDLE),
    ctypes.c_ulong,
    wintypes.BOOL,
    ctypes.c_ulong,
]
kernel32.DuplicateHandle.restype = wintypes.BOOL

kernel32.GetCurrentProcess.restype = HANDLE


def _fail_if_zero(result: int, func, arguments):
    if not result:
        err = ctypes.get_last_error()
        raise ctypes.WinError(err)
    return result


kernel32.CloseHandle.errcheck = _fail_if_zero
kernel32.DuplicateHandle.errcheck = _fail_if_zero


def query_system_handles() -> Iterable[SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX]:
    """Yield extended handle table entries for all open system handles."""
    buffer_length = 0x10000
    while True:
        buf = ctypes.create_string_buffer(buffer_length)
        needed = ctypes.c_ulong()
        status = ntdll.NtQuerySystemInformation(
            SYSTEM_INFORMATION_CLASS.SystemExtendedHandleInformation,
            buf,
            buffer_length,
            ctypes.byref(needed),
        )
        status_value = status & 0xFFFFFFFF
        if status_value in (STATUS_INFO_LENGTH_MISMATCH, STATUS_BUFFER_OVERFLOW):
            buffer_length = max(buffer_length * 2, needed.value or buffer_length * 2)
            continue
        if status_value != STATUS_SUCCESS:
            raise RuntimeError(f"NtQuerySystemInformation failed with NTSTATUS 0x{status_value:08X}")

        header = ctypes.cast(buf, ctypes.POINTER(SYSTEM_HANDLE_INFORMATION_EX)).contents
        count = header.NumberOfHandles
        entry_size = ctypes.sizeof(SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX)
        entries_start = ctypes.addressof(buf) + ctypes.sizeof(SYSTEM_HANDLE_INFORMATION_EX)
        for index in range(count):
            entry_ptr = entries_start + index * entry_size
            entry = ctypes.cast(entry_ptr, ctypes.POINTER(SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX)).contents
            yield entry
        return


def duplicate_handle_for_query(process_handle: HANDLE, source_handle: HANDLE) -> Optional[HANDLE]:
    """Duplicate a remote handle into the current process for inspection."""
    current_process = kernel32.GetCurrentProcess()
    duplicated = HANDLE()
    try:
        kernel32.DuplicateHandle(
            process_handle,
            source_handle,
            current_process,
            ctypes.byref(duplicated),
            0,
            False,
            DUPLICATE_SAME_ACCESS,
        )
    except OSError:
        return None
    return duplicated


def _query_object_name_sync(handle: HANDLE) -> Optional[str]:
    """Return the kernel object name associated with the provided handle (blocking)."""
    buffer_length = 0x400
    while buffer_length <= 0x100000:
        buf = ctypes.create_string_buffer(buffer_length)
        needed = ctypes.c_ulong()
        status = ntdll.NtQueryObject(
            handle,
            OBJECT_INFORMATION_CLASS.ObjectNameInformation,
            buf,
            buffer_length,
            ctypes.byref(needed),
        )
        status_value = status & 0xFFFFFFFF
        if status_value in (STATUS_BUFFER_OVERFLOW, STATUS_INFO_LENGTH_MISMATCH):
            buffer_length = max(buffer_length * 2, needed.value or buffer_length * 2)
            continue
        if status_value != STATUS_SUCCESS:
            return None

        info = ctypes.cast(buf, ctypes.POINTER(OBJECT_NAME_INFORMATION)).contents
        length = info.Name.Length // 2
        if length == 0 or not info.Name.Buffer:
            return ""
        return ctypes.wstring_at(info.Name.Buffer, length)
    return None


def query_object_name(handle: HANDLE, timeout: float = 0.2) -> Optional[str]:
    """Return the kernel object name with timeout to avoid NtQueryObject hangs."""
    result: list[Optional[str]] = [None]

    def worker():
        try:
            result[0] = _query_object_name_sync(handle)
        except Exception:
            result[0] = None

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout)

    if thread.is_alive():
        # Query is hanging, abandon it
        return None

    return result[0]


def close_remote_handle(process_handle: HANDLE, remote_handle: HANDLE) -> bool:
    """Close the remote handle within its owning process."""
    try:
        kernel32.DuplicateHandle(
            process_handle,
            remote_handle,
            HANDLE(0),
            ctypes.byref(HANDLE()),
            0,
            False,
            DUPLICATE_CLOSE_SOURCE,
        )
        return True
    except OSError:
        return False


def handle_matches(name: Optional[str]) -> bool:
    """Check whether the queried object name corresponds to the Roblox singleton event."""
    if not name:
        return False
    # Match the event name anywhere in the path
    return TARGET_EVENT_NAME in name


def get_roblox_pids() -> set[int]:
    """Get PIDs of all Roblox processes."""
    import subprocess
    pids = set()
    try:
        # CREATE_NO_WINDOW prevents console flash when running as GUI exe
        result = subprocess.run(
            ['tasklist', '/FI', 'IMAGENAME eq RobloxPlayerBeta.exe', '/FO', 'CSV', '/NH'],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        for line in result.stdout.strip().split('\n'):
            if line and 'RobloxPlayerBeta' in line:
                parts = line.split(',')
                if len(parts) >= 2:
                    pid_str = parts[1].strip().strip('"')
                    if pid_str.isdigit():
                        pids.add(int(pid_str))
    except Exception:
        pass
    return pids


def main() -> int:
    closed = 0
    skipped = 0
    checked = 0
    timed_out = 0

    print("Scanning system handles for ROBLOX_singletonEvent ...")
    print("(This may take a few seconds)")

    # First find Roblox processes
    roblox_pids = get_roblox_pids()
    if roblox_pids:
        print(f"Found Roblox processes: {list(roblox_pids)}")
    else:
        print("Warning: No Roblox processes found. Is Roblox running?")

    try:
        handle_iter = list(query_system_handles())
    except RuntimeError as error:
        print(f"Failed to enumerate system handles: {error}")
        print("Try running this script as administrator and ensure no security software blocks handle queries.")
        return 1

    total = len(handle_iter)

    # Filter to only Roblox process handles if we found Roblox PIDs
    if roblox_pids:
        handle_iter = [e for e in handle_iter if e.UniqueProcessId in roblox_pids]
        print(f"Filtered to {len(handle_iter)} handles from Roblox processes (from {total} total)")
    else:
        print(f"Found {total} handles to scan...")

    for entry in handle_iter:
        checked += 1
        pid = entry.UniqueProcessId
        if pid == 0:
            continue
        source_handle = HANDLE(entry.HandleValue)
        process_handle = kernel32.OpenProcess(
            PROCESS_DUP_HANDLE | PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            wintypes.DWORD(pid),
        )
        if not process_handle:
            skipped += 1
            print(f"  Could not open process {pid}")
            continue

        try:
            duplicated = duplicate_handle_for_query(process_handle, source_handle)
            if not duplicated:
                skipped += 1
                continue

            try:
                name = query_object_name(duplicated)
                if name is None:
                    timed_out += 1
            finally:
                try:
                    kernel32.CloseHandle(duplicated)
                except OSError:
                    pass

            if not handle_matches(name):
                continue

            print(f"Found matching handle: {name} in PID {pid}")

            if close_remote_handle(process_handle, source_handle):
                closed += 1
                print(f"  -> Successfully closed!")
            else:
                print(f"  -> Failed to close (access denied)")
                skipped += 1
        finally:
            try:
                kernel32.CloseHandle(process_handle)
            except OSError:
                pass

    print(f"\n=== Results ===")
    print(f"Handles checked: {checked}")
    print(f"Handles closed: {closed}")
    print(f"Handles timed out: {timed_out}")
    if skipped:
        print(f"Handles skipped (access denied): {skipped}")
    if closed == 0:
        print("\nNo ROBLOX singleton event handles were closed.")
        print("Make sure:")
        print("  1. Roblox is running")
        print("  2. This script is run as Administrator")
        print("  3. Try closing any handle explorer tools (like Process Hacker)")
        return 1
    print("\nSuccessfully closed the ROBLOX singleton event handles!")
    print("You can now launch additional Roblox instances.")
    return 0


if __name__ == "__main__":
    if sys.platform != "win32":
        print("This script must be run on Windows.")
        sys.exit(1)
    sys.exit(main())


