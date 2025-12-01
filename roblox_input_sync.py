"""
Mirror keyboard and mouse input from one Roblox client window to the others.

Usage (CLI):
    1. Run this script from an elevated PowerShell (administrator) on Windows.
    2. Launch the Roblox clients you want to mirror.
    3. Focus the Roblox window that should be the controller.
    4. Press Ctrl+F10 to toggle mirroring on or off.
    5. Press Ctrl+F12 at any time for a panic stop.
    6. Leave the console open while mirroring; press Ctrl+C to exit.

Implementation notes:
- Low-level keyboard and mouse hooks capture physical input without stealing focus.
- Events are re-posted to other Roblox window handles via PostMessage.
- All access to shared state is protected with a re-entrant lock.
"""

from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import wintypes
from typing import Callable, Optional


if sys.platform != "win32":
    print("This script must be run on Windows.")
    sys.exit(1)


user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


# Constants
WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
HC_ACTION = 0

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_RBUTTONDBLCLK = 0x0206
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MBUTTONDBLCLK = 0x0209
WM_MOUSEWHEEL = 0x020A
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
WM_XBUTTONDBLCLK = 0x020D
WM_MOUSEHWHEEL = 0x020E

MOD_CONTROL = 0x0002
VK_F10 = 0x79
VK_F12 = 0x7B

HOTKEY_TOGGLE_ID = 1
HOTKEY_PANIC_ID = 2

LLKHF_EXTENDED = 0x01
LLKHF_INJECTED = 0x10
LLKHF_ALTDOWN = 0x20
LLKHF_UP = 0x80

LLMHF_INJECTED = 0x00000001

MK_LBUTTON = 0x0001
MK_RBUTTON = 0x0002
MK_SHIFT = 0x0004
MK_CONTROL = 0x0008
MK_MBUTTON = 0x0010
MK_XBUTTON1 = 0x0020
MK_XBUTTON2 = 0x0040

XBUTTON1 = 0x0001
XBUTTON2 = 0x0002

VK_SHIFT = 0x10
VK_CONTROL = 0x11


# Structures
class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulonglong),
    ]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulonglong),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


LowLevelKeyboardProc = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
LowLevelMouseProc = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)


# Helpers
def is_window(handle: int) -> bool:
    """Return True when the supplied handle points to a valid window."""
    return bool(user32.IsWindow(wintypes.HWND(handle)))


def get_class_name(handle: int) -> str:
    """Return the Win32 class name for the provided window handle."""
    buffer = ctypes.create_unicode_buffer(256)
    if user32.GetClassNameW(wintypes.HWND(handle), buffer, len(buffer)):
        return buffer.value
    return ""


def get_window_text(handle: int) -> str:
    """Return the current window title for the provided handle."""
    length = user32.GetWindowTextLengthW(wintypes.HWND(handle))
    if length == 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    if user32.GetWindowTextW(wintypes.HWND(handle), buffer, len(buffer)):
        return buffer.value
    return ""


def is_roblox_window(handle: int) -> bool:
    """Return True if the window handle appears to belong to a Roblox client."""
    if handle == 0:
        return False
    if not user32.IsWindowVisible(wintypes.HWND(handle)):
        return False
    class_name = get_class_name(handle)
    title = get_window_text(handle)
    if class_name.upper() == "WINDOWSCLIENT":
        return True
    if title and "ROBLOX" in title.upper():
        return True
    return False


def enumerate_roblox_windows(exclude: int) -> list[int]:
    """Enumerate visible Roblox windows, optionally skipping one handle."""
    windows: list[int] = []

    @EnumWindowsProc
    def callback(hwnd: wintypes.HWND, _lparam: wintypes.LPARAM) -> bool:
        handle = int(hwnd)
        if handle == exclude:
            return True
        if is_roblox_window(handle):
            windows.append(handle)
        return True

    user32.EnumWindows(callback, 0)
    return windows


def get_modifier_bits() -> int:
    """Return the current modifier keys as MK_* bit flags."""
    modifiers = 0
    if user32.GetKeyState(VK_SHIFT) & 0x8000:
        modifiers |= MK_SHIFT
    if user32.GetKeyState(VK_CONTROL) & 0x8000:
        modifiers |= MK_CONTROL
    return modifiers


def build_keyboard_lparam(message: int, data: KBDLLHOOKSTRUCT) -> int:
    """Build the lParam for a keyboard message."""
    repeat = 1
    scan = data.scanCode & 0xFF
    extended = 1 if data.flags & LLKHF_EXTENDED else 0
    lparam = repeat | (scan << 16) | (extended << 24)

    if data.flags & LLKHF_ALTDOWN:
        lparam |= 0x20000000

    if message in (WM_KEYUP, WM_SYSKEYUP) or data.flags & LLKHF_UP:
        lparam |= 0xC0000000
    return lparam


def build_mouse_lparam(hwnd: int, point: wintypes.POINT) -> int:
    """Translate the global mouse point into the target window's client coordinates."""
    client_point = wintypes.POINT(point.x, point.y)
    if not user32.ScreenToClient(wintypes.HWND(hwnd), ctypes.byref(client_point)):
        return 0
    x = client_point.x & 0xFFFF
    y = client_point.y & 0xFFFF
    return (y << 16) | x


def post_message(hwnd: int, message: int, wparam: int, lparam: int) -> bool:
    """Post a message to the destination window and return True on success."""
    result = user32.PostMessageW(wintypes.HWND(hwnd), message, wintypes.WPARAM(wparam), wintypes.LPARAM(lparam))
    return bool(result)


# Service
class RobloxInputSyncService:
    """Manage low-level hooks and mirror Roblox input between windows."""

    def __init__(self, logger: Optional[Callable[[str], None]] = None, enable_hotkeys: bool = False):
        self.logger = logger or (lambda message: print(message))
        self.enable_hotkeys = enable_hotkeys
        self.state_lock = threading.RLock()
        self.mirroring_active = False
        self.primary_hwnd: Optional[int] = None
        self.target_hwnds: list[int] = []
        self.keyboard_hook_handle = wintypes.HHOOK(0)
        self.mouse_hook_handle = wintypes.HHOOK(0)
        self.mouse_button_state = 0
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._stopped_event = threading.Event()
        self._stopped_event.set()
        self._thread_id: Optional[int] = None

    @property
    def is_running(self) -> bool:
        """Return True if the hook thread is currently running."""
        return self._thread is not None or _active_service is self

    @property
    def is_mirroring(self) -> bool:
        """Return True when mirroring is active."""
        with self.state_lock:
            return self.mirroring_active

    def _log(self, message: str) -> None:
        """Emit a log message via the configured logger."""
        try:
            self.logger(message)
        except Exception:
            print(message)

    # Public API
    def start_background(self, timeout: float = 3.0) -> None:
        """Start the hook service on a background thread."""
        if self.is_running:
            return
        self._ready_event.clear()
        self._stop_event.clear()
        self._stopped_event.clear()
        self._thread = threading.Thread(target=self._thread_main, name="RobloxInputSync", daemon=True)
        self._thread.start()
        if not self._ready_event.wait(timeout):
            raise TimeoutError("Timed out waiting for input sync service to start.")

    def run_forever(self) -> None:
        """Run the hook service on the current thread until WM_QUIT is posted."""
        if self.is_running:
            raise RuntimeError("Input sync service already running.")
        self._stop_event.clear()
        self._stopped_event.clear()
        self._ready_event.clear()
        try:
            self._activate_service()
            self._ready_event.set()
            self._message_loop()
        finally:
            self._cleanup_service()
            self._stop_event.clear()
            self._ready_event.clear()
            self._stopped_event.set()

    def stop(self, wait: bool = True) -> None:
        """Request the hook service to stop and optionally wait for shutdown."""
        if not self.is_running:
            return
        self._stop_event.set()
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if wait and self._thread:
            self._thread.join(timeout=5)
        self._thread = None
        self._thread_id = None
        self._ready_event.clear()
        self._stopped_event.set()

    def wait_until_stopped(self, timeout: Optional[float] = None) -> bool:
        """Wait until the hook service fully stops."""
        return self._stopped_event.wait(timeout)

    def start_mirroring(self) -> None:
        """Begin mirroring input from the foreground Roblox window to the others."""
        with self.state_lock:
            if self.mirroring_active:
                self._log("Mirroring already active.")
                return

            primary = user32.GetForegroundWindow()
            if not primary or not is_roblox_window(int(primary)):
                self._log("Focus the Roblox window you want to control before starting mirroring.")
                return

            targets = enumerate_roblox_windows(int(primary))
            targets = [hwnd for hwnd in targets if is_window(hwnd)]

            if not targets:
                self._log("No additional Roblox windows found. Launch the other instance(s) first.")
                return

            self.primary_hwnd = int(primary)
            self.target_hwnds = targets
            self.mirroring_active = True

        self._log(
            f"Mirroring enabled. Primary window: 0x{self.primary_hwnd:X}, targets: {len(self.target_hwnds)}"
        )

    def stop_mirroring(self, reason: Optional[str] = None) -> None:
        """Stop mirroring input to secondary Roblox windows."""
        with self.state_lock:
            if not self.mirroring_active and not self.target_hwnds:
                if reason:
                    self._log(reason)
                return
            self.mirroring_active = False
            self.primary_hwnd = None
            self.target_hwnds = []
        if reason:
            self._log(f"Mirroring stopped: {reason}")
        else:
            self._log("Mirroring stopped.")

    def panic_stop(self) -> None:
        """Immediately stop mirroring and clear the target list."""
        self.stop_mirroring("panic stop invoked")

    # Hook handlers
    def handle_keyboard(self, nCode: int, wParam: wintypes.WPARAM, lParam: wintypes.LPARAM) -> int:
        """Process keyboard hook callbacks and broadcast events when needed."""
        if nCode == HC_ACTION:
            message = int(wParam)
            if message in (WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP):
                data = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                self._broadcast_keyboard_event(message, data)
        return user32.CallNextHookEx(self.keyboard_hook_handle, nCode, wParam, lParam)

    def handle_mouse(self, nCode: int, wParam: wintypes.WPARAM, lParam: wintypes.LPARAM) -> int:
        """Process mouse hook callbacks and broadcast events when needed."""
        if nCode == HC_ACTION:
            message = int(wParam)
            if message in (
                WM_MOUSEMOVE,
                WM_LBUTTONDOWN,
                WM_LBUTTONUP,
                WM_LBUTTONDBLCLK,
                WM_RBUTTONDOWN,
                WM_RBUTTONUP,
                WM_RBUTTONDBLCLK,
                WM_MBUTTONDOWN,
                WM_MBUTTONUP,
                WM_MBUTTONDBLCLK,
                WM_MOUSEWHEEL,
                WM_XBUTTONDOWN,
                WM_XBUTTONUP,
                WM_XBUTTONDBLCLK,
                WM_MOUSEHWHEEL,
            ):
                data = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                self._broadcast_mouse_event(message, data)
        return user32.CallNextHookEx(self.mouse_hook_handle, nCode, wParam, lParam)

    # Internal helpers
    def _install_hooks(self) -> None:
        """Install low-level keyboard and mouse hooks."""
        keyboard_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, keyboard_proc, None, 0)
        if not keyboard_hook:
            raise ctypes.WinError(ctypes.get_last_error())
        mouse_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, mouse_proc, None, 0)
        if not mouse_hook:
            raise ctypes.WinError(ctypes.get_last_error())
        self.keyboard_hook_handle = keyboard_hook
        self.mouse_hook_handle = mouse_hook

    def _uninstall_hooks(self) -> None:
        """Remove any installed hooks."""
        if self.keyboard_hook_handle:
            user32.UnhookWindowsHookEx(self.keyboard_hook_handle)
            self.keyboard_hook_handle = wintypes.HHOOK(0)
        if self.mouse_hook_handle:
            user32.UnhookWindowsHookEx(self.mouse_hook_handle)
            self.mouse_hook_handle = wintypes.HHOOK(0)

    def _register_hotkeys(self) -> None:
        """Register hotkeys for CLI usage when enabled."""
        if not self.enable_hotkeys:
            return
        if not user32.RegisterHotKey(None, HOTKEY_TOGGLE_ID, MOD_CONTROL, VK_F10):
            raise ctypes.WinError(ctypes.get_last_error())
        if not user32.RegisterHotKey(None, HOTKEY_PANIC_ID, MOD_CONTROL, VK_F12):
            raise ctypes.WinError(ctypes.get_last_error())

    def _unregister_hotkeys(self) -> None:
        """Remove any registered hotkeys."""
        if not self.enable_hotkeys:
            return
        user32.UnregisterHotKey(None, HOTKEY_TOGGLE_ID)
        user32.UnregisterHotKey(None, HOTKEY_PANIC_ID)

    def _handle_hotkey(self, identifier: int) -> None:
        """Handle WM_HOTKEY notifications."""
        if identifier == HOTKEY_TOGGLE_ID:
            if self.is_mirroring:
                self.stop_mirroring("toggle hotkey pressed")
            else:
                self.start_mirroring()
        elif identifier == HOTKEY_PANIC_ID:
            self.panic_stop()

    def _broadcast_keyboard_event(self, message: int, data: KBDLLHOOKSTRUCT) -> None:
        """Forward keyboard events to mirrored windows when active."""
        with self.state_lock:
            active = self.mirroring_active
            primary = self.primary_hwnd
            targets = list(self.target_hwnds)
        if not active or not targets or not primary:
            return
        if user32.GetForegroundWindow() != primary:
            return
        if data.flags & LLKHF_INJECTED:
            return

        lparam = build_keyboard_lparam(message, data)
        dead: list[int] = []
        for hwnd in targets:
            if not is_window(hwnd):
                dead.append(hwnd)
                continue
            if not post_message(hwnd, message, data.vkCode, lparam):
                dead.append(hwnd)
        self._remove_dead_targets(dead)

    def _broadcast_mouse_event(self, message: int, data: MSLLHOOKSTRUCT) -> None:
        """Forward mouse events to mirrored windows when active."""
        with self.state_lock:
            active = self.mirroring_active
            primary = self.primary_hwnd
            targets = list(self.target_hwnds)
        if not active or not targets or not primary:
            return
        if user32.GetForegroundWindow() != primary:
            return
        if data.flags & LLMHF_INJECTED:
            return

        self._update_mouse_button_state(message, data.mouseData)
        wparam = self._build_mouse_wparam(message, data.mouseData)

        dead: list[int] = []
        for hwnd in targets:
            if not is_window(hwnd):
                dead.append(hwnd)
                continue
            lparam = build_mouse_lparam(hwnd, data.pt)
            if not post_message(hwnd, message, wparam, lparam):
                dead.append(hwnd)
        self._remove_dead_targets(dead)

    def _update_mouse_button_state(self, message: int, mouse_data: int) -> None:
        """Track mouse button state for constructing WM_* parameters."""
        if message in (WM_LBUTTONDOWN, WM_LBUTTONDBLCLK):
            self.mouse_button_state |= MK_LBUTTON
        elif message == WM_LBUTTONUP:
            self.mouse_button_state &= ~MK_LBUTTON
        elif message in (WM_RBUTTONDOWN, WM_RBUTTONDBLCLK):
            self.mouse_button_state |= MK_RBUTTON
        elif message == WM_RBUTTONUP:
            self.mouse_button_state &= ~MK_RBUTTON
        elif message in (WM_MBUTTONDOWN, WM_MBUTTONDBLCLK):
            self.mouse_button_state |= MK_MBUTTON
        elif message == WM_MBUTTONUP:
            self.mouse_button_state &= ~MK_MBUTTON
        elif message in (WM_XBUTTONDOWN, WM_XBUTTONDBLCLK):
            button = (mouse_data >> 16) & 0xFFFF
            if button & XBUTTON1:
                self.mouse_button_state |= MK_XBUTTON1
            if button & XBUTTON2:
                self.mouse_button_state |= MK_XBUTTON2
        elif message == WM_XBUTTONUP:
            button = (mouse_data >> 16) & 0xFFFF
            if button & XBUTTON1:
                self.mouse_button_state &= ~MK_XBUTTON1
            if button & XBUTTON2:
                self.mouse_button_state &= ~MK_XBUTTON2

    def _build_mouse_wparam(self, message: int, mouse_data: int) -> int:
        """Construct the wParam for forwarded mouse messages."""
        modifiers = self.mouse_button_state | get_modifier_bits()
        if message in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL):
            return modifiers | (mouse_data & 0xFFFF0000)
        if message in (WM_XBUTTONDOWN, WM_XBUTTONUP, WM_XBUTTONDBLCLK):
            button = (mouse_data >> 16) & 0xFFFF
            return modifiers | (button << 16)
        return modifiers

    def _remove_dead_targets(self, dead: list[int]) -> None:
        """Remove target windows that no longer exist."""
        if not dead:
            return
        with self.state_lock:
            updated = False
            for handle in dead:
                if handle in self.target_hwnds:
                    self.target_hwnds.remove(handle)
                    updated = True
            if updated and not self.target_hwnds and self.mirroring_active:
                self.mirroring_active = False
                self.primary_hwnd = None
                self._log("Mirroring stopped because all target windows closed.")

    def _message_loop(self) -> None:
        """Process messages until WM_QUIT arrives."""
        msg = MSG()
        while True:
            result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result == 0:
                break
            if result == -1:
                raise ctypes.WinError(ctypes.get_last_error())
            if msg.message == WM_HOTKEY and self.enable_hotkeys:
                self._handle_hotkey(int(msg.wParam))
                continue
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _thread_main(self) -> None:
        """Entry point for the background hook thread."""
        try:
            self._activate_service()
            self._ready_event.set()
            self._message_loop()
        except Exception as exc:
            self._log(f"Input sync service error: {exc}")
        finally:
            self._cleanup_service()
            self._stopped_event.set()

    def _activate_service(self) -> None:
        """Install hooks and register hotkeys for the running thread."""
        global _active_service
        if _active_service is not None:
            raise RuntimeError("Another input sync service is already active.")
        self._thread_id = kernel32.GetCurrentThreadId()
        _active_service = self
        self._install_hooks()
        self._register_hotkeys()

    def _cleanup_service(self) -> None:
        """Clean up hooks, hotkeys, and mirrored state."""
        global _active_service
        try:
            self._unregister_hotkeys()
            self._uninstall_hooks()
            self.stop_mirroring()
        finally:
            _active_service = None
            self._thread = None
            self._thread_id = None


_active_service: Optional[RobloxInputSyncService] = None


@LowLevelKeyboardProc
def keyboard_proc(nCode: int, wParam: wintypes.WPARAM, lParam: wintypes.LPARAM) -> int:
    """Forward keyboard events to the active service, if any."""
    service = _active_service
    if service is None:
        return user32.CallNextHookEx(None, nCode, wParam, lParam)
    return service.handle_keyboard(nCode, wParam, lParam)


@LowLevelMouseProc
def mouse_proc(nCode: int, wParam: wintypes.WPARAM, lParam: wintypes.LPARAM) -> int:
    """Forward mouse events to the active service, if any."""
    service = _active_service
    if service is None:
        return user32.CallNextHookEx(None, nCode, wParam, lParam)
    return service.handle_mouse(nCode, wParam, lParam)


def main() -> int:
    """CLI entry point for the Roblox input mirroring utility."""
    print("Roblox input mirroring utility")
    print("Controls:")
    print("  Ctrl+F10 -> Toggle mirroring on/off (focus the controller window first)")
    print("  Ctrl+F12 -> Panic stop (immediate)")
    print("  Ctrl+C   -> Exit the utility")

    service = RobloxInputSyncService(enable_hotkeys=True)
    try:
        service.run_forever()
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        service.stop_mirroring("keyboard interrupt")
    except Exception as exc:
        print(f"Fatal error: {exc}")
        return 1
    finally:
        service.stop(wait=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())

