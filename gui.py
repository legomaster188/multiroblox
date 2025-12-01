"""
Roblox Multi-Instance Launcher
A sleek tool to close the singleton event and launch multiple Roblox clients.
"""

import ctypes
import sys
import threading
import tkinter as tk
from ctypes import wintypes
from typing import Optional

if sys.platform != "win32":
    print("This application must be run on Windows.")
    sys.exit(1)

import close_roblox_event

# Colors - Dark theme
BG_DARK = "#0f0f0f"
BG_CARD = "#1a1a1a"
BG_BUTTON = "#2d2d2d"
BG_BUTTON_HOVER = "#3d3d3d"
BG_BUTTON_ACTIVE = "#4a4a4a"
ACCENT = "#00d4aa"
ACCENT_GLOW = "#00ffcc"
TEXT_PRIMARY = "#ffffff"
TEXT_SECONDARY = "#888888"
TEXT_DIM = "#555555"
SUCCESS = "#00ff88"
ERROR = "#ff4466"
WARNING = "#ffaa00"


class ModernButton(tk.Canvas):
    """A modern styled button with hover effects."""

    def __init__(self, parent, text, command, width=280, height=50, **kwargs):
        super().__init__(parent, width=width, height=height, bg=BG_CARD,
                         highlightthickness=0, cursor="hand2", **kwargs)

        self.command = command
        self.text = text
        self.width = width
        self.height = height
        self.enabled = True
        self._hover = False

        self._draw()

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _draw(self, pressed=False):
        self.delete("all")

        if not self.enabled:
            bg = BG_BUTTON
            border = TEXT_DIM
            text_color = TEXT_DIM
        elif pressed:
            bg = BG_BUTTON_ACTIVE
            border = ACCENT_GLOW
            text_color = ACCENT_GLOW
        elif self._hover:
            bg = BG_BUTTON_HOVER
            border = ACCENT
            text_color = ACCENT
        else:
            bg = BG_BUTTON
            border = ACCENT
            text_color = TEXT_PRIMARY

        # Draw rounded rectangle with border
        r = 8  # corner radius
        self.create_polygon(
            r, 0, self.width-r, 0,
            self.width, 0, self.width, r,
            self.width, self.height-r, self.width, self.height,
            self.width-r, self.height, r, self.height,
            0, self.height, 0, self.height-r,
            0, r, 0, 0,
            smooth=True, fill=bg, outline=border, width=2
        )

        # Draw text
        self.create_text(
            self.width // 2, self.height // 2,
            text=self.text, fill=text_color,
            font=("Segoe UI Semibold", 12)
        )

    def _on_enter(self, event):
        if self.enabled:
            self._hover = True
            self._draw()

    def _on_leave(self, event):
        self._hover = False
        self._draw()

    def _on_click(self, event):
        if self.enabled:
            self._draw(pressed=True)

    def _on_release(self, event):
        if self.enabled:
            self._draw()
            if self._hover:
                self.command()

    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self._draw()


class PulsingDot(tk.Canvas):
    """An animated pulsing dot indicator."""

    def __init__(self, parent, size=12, **kwargs):
        super().__init__(parent, width=size, height=size, bg=BG_CARD,
                         highlightthickness=0, **kwargs)
        self.size = size
        self.color = TEXT_DIM
        self.pulse_id: Optional[str] = None
        self.pulse_phase = 0
        self._draw()

    def _draw(self):
        self.delete("all")
        padding = 2
        self.create_oval(
            padding, padding, self.size - padding, self.size - padding,
            fill=self.color, outline=""
        )

    def set_color(self, color: str):
        self.color = color
        self._draw()

    def start_pulse(self, color: str):
        self.stop_pulse()
        self.color = color
        self._pulse_step()

    def _pulse_step(self):
        import math
        self.pulse_phase += 0.15
        alpha = (math.sin(self.pulse_phase) + 1) / 2

        # Interpolate between dim and bright
        if self.color == ACCENT:
            colors = [ACCENT, ACCENT_GLOW]
        else:
            colors = [self.color, self.color]

        self.delete("all")
        padding = 2 + (1 - alpha)
        self.create_oval(
            padding, padding, self.size - padding, self.size - padding,
            fill=self.color, outline=""
        )

        self.pulse_id = self.after(50, self._pulse_step)

    def stop_pulse(self):
        if self.pulse_id:
            self.after_cancel(self.pulse_id)
            self.pulse_id = None
        self._draw()


class RobloxLauncher:
    """Main application window."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Roblox Multi-Instance")
        self.root.configure(bg=BG_DARK)
        self.root.geometry("360x480")
        self.root.resizable(False, False)

        # Center window on screen
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() - 360) // 2
        y = (self.root.winfo_screenheight() - 480) // 2
        self.root.geometry(f"360x480+{x}+{y}")

        self._create_ui()
        self._update_roblox_count()

    def _create_ui(self):
        # Main container
        container = tk.Frame(self.root, bg=BG_DARK)
        container.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

        # Header with icon
        header = tk.Frame(container, bg=BG_DARK)
        header.pack(fill=tk.X, pady=(0, 20))

        # Big title
        title = tk.Label(
            header, text="ROBLOX", font=("Segoe UI Black", 28),
            fg=TEXT_PRIMARY, bg=BG_DARK
        )
        title.pack()

        subtitle = tk.Label(
            header, text="MULTI-INSTANCE", font=("Segoe UI", 12, "bold"),
            fg=ACCENT, bg=BG_DARK, pady=0
        )
        subtitle.pack()

        # Card container
        card = tk.Frame(container, bg=BG_CARD, padx=20, pady=20)
        card.pack(fill=tk.BOTH, expand=True)

        # Status section
        status_frame = tk.Frame(card, bg=BG_CARD)
        status_frame.pack(fill=tk.X, pady=(0, 20))

        # Roblox instances count
        count_row = tk.Frame(status_frame, bg=BG_CARD)
        count_row.pack(fill=tk.X, pady=5)

        tk.Label(
            count_row, text="Roblox Instances", font=("Segoe UI", 10),
            fg=TEXT_SECONDARY, bg=BG_CARD
        ).pack(side=tk.LEFT)

        self.count_label = tk.Label(
            count_row, text="0", font=("Segoe UI Semibold", 10),
            fg=TEXT_PRIMARY, bg=BG_CARD
        )
        self.count_label.pack(side=tk.RIGHT)

        # Divider
        tk.Frame(card, bg=TEXT_DIM, height=1).pack(fill=tk.X, pady=15)

        # Status indicator
        status_row = tk.Frame(card, bg=BG_CARD)
        status_row.pack(fill=tk.X, pady=(0, 20))

        self.status_dot = PulsingDot(status_row, size=10)
        self.status_dot.pack(side=tk.LEFT, padx=(0, 10))

        self.status_label = tk.Label(
            status_row, text="Ready", font=("Segoe UI", 10),
            fg=TEXT_SECONDARY, bg=BG_CARD
        )
        self.status_label.pack(side=tk.LEFT)

        # Main action button
        self.action_btn = ModernButton(
            card, "UNLOCK MULTI-INSTANCE", self._close_singleton
        )
        self.action_btn.pack(pady=10)

        # Description
        desc = tk.Label(
            card,
            text="Closes the singleton event lock\nso you can run multiple Roblox clients",
            font=("Segoe UI", 9),
            fg=TEXT_DIM, bg=BG_CARD, justify=tk.CENTER
        )
        desc.pack(pady=(15, 0))

        # Log area
        log_frame = tk.Frame(card, bg=BG_DARK, padx=1, pady=1)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(20, 0))

        self.log_text = tk.Text(
            log_frame, height=6, bg=BG_DARK, fg=TEXT_DIM,
            font=("Consolas", 8), relief=tk.FLAT,
            insertbackground=TEXT_DIM, selectbackground=BG_BUTTON_HOVER
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.configure(state=tk.DISABLED)

        # Footer
        footer = tk.Label(
            container, text="Run as Administrator for best results",
            font=("Segoe UI", 8), fg=TEXT_DIM, bg=BG_DARK
        )
        footer.pack(pady=(15, 0))

    def _log(self, message: str, color: str = TEXT_DIM):
        def do_log():
            self.log_text.configure(state=tk.NORMAL)
            self.log_text.insert(tk.END, f"{message}\n")
            self.log_text.see(tk.END)
            self.log_text.configure(state=tk.DISABLED)

        if threading.current_thread() is threading.main_thread():
            do_log()
        else:
            self.root.after(0, do_log)

    def _set_status(self, text: str, color: str, pulse: bool = False):
        def do_update():
            self.status_label.configure(text=text, fg=color)
            if pulse:
                self.status_dot.start_pulse(color)
            else:
                self.status_dot.stop_pulse()
                self.status_dot.set_color(color)

        if threading.current_thread() is threading.main_thread():
            do_update()
        else:
            self.root.after(0, do_update)

    def _update_roblox_count(self):
        try:
            pids = close_roblox_event.get_roblox_pids()
            count = len(pids) if pids else 0
            self.count_label.configure(text=str(count))
        except Exception:
            self.count_label.configure(text="?")

        self.root.after(2000, self._update_roblox_count)

    def _close_singleton(self):
        self.action_btn.set_enabled(False)
        self._set_status("Scanning...", ACCENT, pulse=True)
        self._log("Searching for singleton event...")

        def worker():
            try:
                roblox_pids = close_roblox_event.get_roblox_pids()
                if not roblox_pids:
                    self._log("No Roblox processes found")
                    self._set_status("No Roblox running", WARNING)
                    return

                self._log(f"Found {len(roblox_pids)} Roblox process(es)")

                handles = list(close_roblox_event.query_system_handles())
                roblox_handles = [h for h in handles if h.UniqueProcessId in roblox_pids]
                self._log(f"Scanning {len(roblox_handles)} handles...")

                closed = 0
                for entry in roblox_handles:
                    pid = entry.UniqueProcessId
                    source_handle = close_roblox_event.HANDLE(entry.HandleValue)
                    process_handle = close_roblox_event.kernel32.OpenProcess(
                        close_roblox_event.PROCESS_DUP_HANDLE | close_roblox_event.PROCESS_QUERY_LIMITED_INFORMATION,
                        False,
                        wintypes.DWORD(pid),
                    )
                    if not process_handle:
                        continue

                    try:
                        duplicated = close_roblox_event.duplicate_handle_for_query(process_handle, source_handle)
                        if not duplicated:
                            continue
                        try:
                            name = close_roblox_event.query_object_name(duplicated)
                        finally:
                            try:
                                close_roblox_event.kernel32.CloseHandle(duplicated)
                            except OSError:
                                pass

                        if not close_roblox_event.handle_matches(name):
                            continue

                        self._log(f"Found singleton event!")
                        if close_roblox_event.close_remote_handle(process_handle, source_handle):
                            closed += 1
                            self._log("Successfully closed!")
                    finally:
                        try:
                            close_roblox_event.kernel32.CloseHandle(process_handle)
                        except OSError:
                            pass

                if closed:
                    self._set_status("Unlocked! Launch more clients", SUCCESS)
                    self._log(f"Done! Closed {closed} handle(s)")
                else:
                    self._set_status("No lock found (already unlocked?)", WARNING)
                    self._log("No singleton handles found")

            except Exception as exc:
                self._log(f"Error: {exc}")
                self._set_status("Error occurred", ERROR)
            finally:
                self.root.after(0, lambda: self.action_btn.set_enabled(True))

        threading.Thread(target=worker, daemon=True).start()


def main():
    root = tk.Tk()

    # Try to set DPI awareness for crisp text on Windows
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    app = RobloxLauncher(root)
    root.mainloop()


if __name__ == "__main__":
    main()
