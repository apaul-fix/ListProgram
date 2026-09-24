"""
Simple Support Activity Tracker
--------------------------------
A small desktop window with a scrollable list of client rows. Each row has:
  - A drag handle (grab and move to reorder)
  - Client Name
  - Note / Issue
  - Priority tag (click to cycle Low / Med / High)
  - "Added" timestamp (relative, e.g. "5m")
  - Done checkbox -> logs the row to completed_log.csv and removes it

Unfinished (pending) rows -- including their note, priority, order, and the
time they were added -- are saved to pending_activities.csv every time
something changes, and are reloaded automatically the next time you open
the app, so your in-progress list survives closing and reopening the
program.

A search box filters the visible rows by client name.

Build to .exe with (Windows):
    python -m PyInstaller --onefile --windowed --icon=icon.ico --add-data "icon.ico;." activity_tracker.py

Build to .exe with (macOS/Linux, note the colon instead of semicolon):
    python -m PyInstaller --onefile --windowed --icon=icon.ico --add-data "icon.ico:." activity_tracker.py

icon.ico must sit in the same folder as this script before building.
--icon embeds the icon in the exe file itself (what Explorer/shortcuts show).
--add-data bundles a copy inside the exe so the app can also load it at
runtime (via resource_path/iconbitmap below) to set the window/taskbar icon
while it's running.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import csv
import os
import sys
import subprocess
import platform
from datetime import datetime

if platform.system() == "Windows":
    try:
        import ctypes
        # Without this, Windows groups the app under the generic "python"/
        # "pythonw" taskbar icon instead of using our own icon, especially
        # for --onefile builds. Giving it a unique AppUserModelID fixes that.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "SupportActivityTracker.App.1"
        )
    except Exception:
        pass  # not on Windows, or the call isn't available -- safe to ignore

# Put the log file next to the exe (or script) rather than a temp folder
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

LOG_FILE = os.path.join(APP_DIR, "completed_log.csv")
PENDING_FILE = os.path.join(APP_DIR, "pending_activities.csv")

PRIORITIES = [("Low", "#3ddc84"), ("Med", "#ff8c1a"), ("High", "#ff4d4d")]


def resource_path(relative_path):
    """Get path to a bundled resource, works for dev and for PyInstaller --onefile."""
    base_path = getattr(sys, "_MEIPASS", os.path.abspath(os.path.dirname(__file__)))
    return os.path.join(base_path, relative_path)


def lerp_color(c1, c2, t):
    """Blend two '#rrggbb' colors together, t in [0, 1]."""
    c1 = c1.lstrip("#")
    c2 = c2.lstrip("#")
    r1, g1, b1 = int(c1[0:2], 16), int(c1[2:4], 16), int(c1[4:6], 16)
    r2, g2, b2 = int(c2[0:2], 16), int(c2[2:4], 16), int(c2[4:6], 16)
    r = int(r1 + (r2 - r1) * t)
    g = int(g1 + (g2 - g1) * t)
    b = int(b1 + (b2 - b1) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


class RoundedButton(tk.Canvas):
    """A small canvas-drawn button with true rounded corners (ttk can't do
    this natively). Used for the Add / Open Log buttons and the per-row
    Save icon."""

    def __init__(self, master, text, command=None, width=90, height=30,
                 radius=14, bg="#141414", fg="#ff8c1a", hover_bg="#ff8c1a",
                 hover_fg="#0a0a0a", font=("Consolas", 9, "bold"),
                 parent_bg="#0a0a0a"):
        super().__init__(master, width=width, height=height, bg=parent_bg,
                          highlightthickness=0, bd=0, cursor="hand2")
        self.command = command
        self.text = text
        self.width = width
        self.height = height
        self.radius = radius
        self.bg = bg
        self.fg = fg
        self.hover_bg = hover_bg
        self.hover_fg = hover_fg
        self.font = font
        self._draw(self.bg, self.fg)

        self.bind("<Enter>", lambda e: self._draw(self.hover_bg, self.hover_fg))
        self.bind("<Leave>", lambda e: self._draw(self.bg, self.fg))
        self.bind("<ButtonRelease-1>", self._on_release)

    def _round_rect(self, x1, y1, x2, y2, r, **kw):
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self.create_polygon(points, smooth=True, **kw)

    def _draw(self, fill, fg):
        self.delete("all")
        self._round_rect(2, 2, self.width - 2, self.height - 2, self.radius,
                          fill=fill, outline=fill)
        self.create_text(self.width / 2, self.height / 2, text=self.text,
                          fill=fg, font=self.font)

    def set_colors(self, bg=None, fg=None, hover_bg=None, hover_fg=None):
        if bg is not None:
            self.bg = bg
        if fg is not None:
            self.fg = fg
        if hover_bg is not None:
            self.hover_bg = hover_bg
        if hover_fg is not None:
            self.hover_fg = hover_fg
        self._draw(self.bg, self.fg)

    def set_text(self, text):
        self.text = text
        self._draw(self.bg, self.fg)

    def flash(self, color, fg, back_to_bg=None, back_to_fg=None, ms=350):
        """Briefly show a different color, then revert -- used as a
        lightweight 'saved!' confirmation."""
        self._draw(color, fg)
        back_bg = back_to_bg if back_to_bg is not None else self.bg
        back_fg = back_to_fg if back_to_fg is not None else self.fg
        self.after(ms, lambda: self._draw(back_bg, back_fg))

    def _on_release(self, event):
        # Only fire if the release happens back over the button
        if 0 <= event.x <= self.width and 0 <= event.y <= self.height:
            if self.command:
                self.command()


class PriorityChip(RoundedButton):
    """A small rounded tag that cycles Low / Med / High on click."""

    def __init__(self, master, parent_bg, initial="Med", on_change=None):
        self.levels = PRIORITIES
        self.index = next(
            (i for i, (n, _) in enumerate(self.levels) if n == initial), 1
        )
        self.on_change = on_change
        name, color = self.levels[self.index]
        super().__init__(
            master, text=name, width=52, height=22, radius=11,
            bg=color, fg="#0a0a0a", hover_bg=color, hover_fg="#0a0a0a",
            font=("Consolas", 8, "bold"), parent_bg=parent_bg,
            command=self._cycle,
        )

    def _cycle(self):
        self.index = (self.index + 1) % len(self.levels)
        name, color = self.levels[self.index]
        self.set_colors(bg=color, hover_bg=color)
        self.set_text(name)
        if self.on_change:
            self.on_change(name)

    @property
    def value(self):
        return self.levels[self.index][0]


class ActivityTracker(tk.Tk):
    BG = "#0a0a0a"          # near-black background
    BG_PANEL = "#141414"    # card / entry background
    FG = "#2596be"          # HUD orange
    FG_DIM = "#808080"      # dimmer orange for borders/inactive
    ACCENT = "#2596be"      # bright accent (hover/selected/dragging)
    SUCCESS = "#35ff89"     # flash color for completed rows

    FONT = ("Consolas", 9)
    FONT_BOLD = ("Consolas", 9, "bold")
    FONT_SMALL = ("Consolas", 9)

    # Shared column layout (grid column index -> configure() kwargs). Used
    # for both the header row and every card row so their contents always
    # line up, regardless of what each column's actual widget is.
    COLUMNS = [
        (0, {"minsize": 30}),               # drag handle
        (1, {"minsize": 90}),               # client name
        (2, {"minsize": 110, "weight": 1}), # note / issue (stretches)
        (3, {"minsize": 70}),               # priority chip
        (4, {"minsize": 50}),               # added timestamp
        (5, {"minsize": 50}),               # done checkbox
    ]

    def _configure_columns(self, frame):
        for col, opts in self.COLUMNS:
            frame.grid_columnconfigure(col, **opts)

    def __init__(self):
        super().__init__()
        self.title("Support Activity Tracker")
        self.geometry("580x480")
        self.minsize(500, 380)

        self.rows = []          # list of row dicts, in display order
        self._loading = False   # guard so loading pending rows doesn't re-trigger saves
        self._drag_row = None   # row currently being dragged, if any

        self._apply_theme()
        self._build_input_area()
        self._build_search_area()
        self._build_header()
        self._build_footer_note()
        self._build_list_area()

        self._ensure_log_file()
        self._set_window_icon()

        self._load_pending_rows()
        self._refresh_timestamps()  # also starts the recurring 30s refresh

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- Theme ----------

    def _apply_theme(self):
        BG, PANEL, FG, DIM, ACCENT = (
            self.BG, self.BG_PANEL, self.FG, self.FG_DIM, self.ACCENT,
        )

        self.configure(bg=BG)

        style = ttk.Style(self)
        # 'clam' is the only built-in theme that reliably honors custom
        # colors on every widget (default Windows/aqua themes ignore most
        # color options).
        style.theme_use("clam")

        style.configure(".", background=BG, foreground=FG,
                         fieldbackground=PANEL, bordercolor=DIM,
                         lightcolor=BG, darkcolor=BG,
                         troughcolor=PANEL, font=self.FONT)

        style.configure("TFrame", background=BG)

        style.configure("TLabel", background=BG, foreground=FG, font=self.FONT)
        style.configure("Header.TLabel", background=BG, foreground=FG,
                         font=self.FONT_BOLD)
        style.configure("Footer.TLabel", background=BG, foreground=DIM,
                         font=self.FONT_SMALL)

        style.configure("TEntry", fieldbackground=PANEL, foreground=FG,
                         insertcolor=FG, bordercolor=DIM,
                         lightcolor=DIM, darkcolor=DIM)
        style.map("TEntry", bordercolor=[("focus", FG)])

        style.configure("TCheckbutton", background=BG, foreground=FG)
        style.map("TCheckbutton",
                   background=[("active", BG)],
                   indicatorcolor=[("selected", FG), ("!selected", PANEL)])

        style.configure("Vertical.TScrollbar", background=PANEL,
                         troughcolor=BG, bordercolor=DIM, arrowcolor=FG)
        style.map("Vertical.TScrollbar", background=[("active", FG)])

    def _set_window_icon(self):
        icon_path = resource_path("icon.ico")
        if os.path.exists(icon_path):
            try:
                # Sets the window titlebar icon, and on Windows this is also
                # what shows up in the taskbar (once combined with the
                # AppUserModelID call above).
                self.iconbitmap(default=icon_path)
            except tk.TclError:
                pass  # icon.ico missing/invalid or unsupported on this platform

    # ---------- UI setup ----------

    def _build_input_area(self):
        top = ttk.Frame(self, padding=(10, 10, 10, 5))
        top.pack(fill="x")

        self.name_entry = ttk.Entry(top)
        self.name_entry.pack(side="left", fill="x", expand=True)
        self.name_entry.bind("<Return>", lambda e: self.add_row())
        self.name_entry.focus_set()

        add_btn = RoundedButton(
            top, text="Add", command=self.add_row, width=70, height=30,
            radius=14, bg=self.BG_PANEL, fg=self.FG, hover_bg=self.FG,
            hover_fg=self.BG, font=self.FONT_BOLD, parent_bg=self.BG,
        )
        add_btn.pack(side="left", padx=(6, 0))

        folder_btn = RoundedButton(
            top, text="Open Log Folder", command=self.open_log_folder,
            width=130, height=30, radius=14, bg=self.BG_PANEL, fg=self.FG,
            hover_bg=self.FG, hover_fg=self.BG, font=self.FONT_BOLD,
            parent_bg=self.BG,
        )
        folder_btn.pack(side="left", padx=(6, 0))

    def _build_search_area(self):
        bar = ttk.Frame(self, padding=(10, 0, 10, 6))
        bar.pack(fill="x")

        ttk.Label(bar, text="\U0001F50D").pack(side="left", padx=(0, 4))

        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(bar, textvariable=self.search_var)
        search_entry.pack(side="left", fill="x", expand=True)
        search_entry.bind("<KeyRelease>", lambda e: self._relayout())

    def _build_header(self):
        header = ttk.Frame(self, padding=(10, 0, 10, 4))
        header.pack(fill="x")
        self._configure_columns(header)

        ttk.Label(header, text="", style="Header.TLabel").grid(
            row=0, column=0, sticky="w", padx=(6, 4)
        )
        ttk.Label(header, text="Client Name", style="Header.TLabel").grid(
            row=0, column=1, sticky="w", padx=(0, 6)
        )
        ttk.Label(header, text="Note / Issue", style="Header.TLabel").grid(
            row=0, column=2, sticky="w", padx=(0, 6)
        )
        ttk.Label(header, text="Priority", style="Header.TLabel").grid(
            row=0, column=3, sticky="w", padx=(0, 6)
        )
        ttk.Label(header, text="Added", style="Header.TLabel").grid(
            row=0, column=4, sticky="w", padx=(0, 6)
        )
        ttk.Label(header, text="Done", style="Header.TLabel").grid(
            row=0, column=5, sticky="w"
        )

    def _build_footer_note(self):
        note = ttk.Label(
            self,
            text="Note: Ensure that completed_log.csv sheet is closed before updating the list, thanks!",
            style="Footer.TLabel",
            wraplength=560,
            justify="center",
            anchor="center",
        )
        note.pack(side="bottom", fill="x", pady=(2, 4), padx=6)

    def _build_list_area(self):
        container = ttk.Frame(self, padding=(10, 5, 10, 10))
        container.pack(fill="both", expand=True)

        canvas = tk.Canvas(container, highlightthickness=0, bg=self.BG)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        self.list_frame = tk.Frame(canvas, bg=self.BG)

        self.list_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )

        window_id = canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
        # Without this, list_frame only ever sizes to fit its own contents,
        # so row cards (packed with fill="x") stretch only to that width --
        # not to the canvas's actual width. This keeps them in sync.
        canvas.bind(
            "<Configure>",
            lambda e: canvas.itemconfig(window_id, width=e.width),
        )
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    # ---------- Logic ----------

    def _ensure_log_file(self):
        if not os.path.exists(LOG_FILE):
            with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Client Name", "Note / Issue", "Priority", "Completed At"])

    def open_log_folder(self):
        try:
            if platform.system() == "Windows":
                os.startfile(APP_DIR)  # type: ignore[attr-defined]
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", APP_DIR])
            else:
                subprocess.Popen(["xdg-open", APP_DIR])
        except Exception as e:
            messagebox.showerror("Couldn't open folder", str(e))

    def add_row(self, name=None, note="", priority="Med", created_at=None):
        """Add a row. When called from the Add button/Enter key, name is None
        and we read it from the entry box. When called during startup to
        restore pending rows, name/note/priority/created_at are passed in."""
        if name is None:
            name = self.name_entry.get().strip()
            if not name:
                messagebox.showwarning("Missing name", "Please enter a client name.")
                return

        if created_at is None:
            created_at = datetime.now()

        row = {"name": name, "created_at": created_at}

        card = tk.Frame(self.list_frame, bg=self.BG_PANEL, highlightthickness=1,
                         highlightbackground=self.FG_DIM, highlightcolor=self.FG_DIM)
        row["frame"] = card
        self._configure_columns(card)

        handle = tk.Label(card, text="\u22EE\u22EE", bg=self.BG_PANEL, fg=self.FG_DIM,
                           font=self.FONT, cursor="fleur")
        handle.grid(row=0, column=0, sticky="w", padx=(6, 4), pady=6)
        handle.bind("<ButtonPress-1>", lambda e, r=row: self._start_drag(e, r))
        handle.bind("<B1-Motion>", lambda e, r=row: self._on_drag(e, r))
        handle.bind("<ButtonRelease-1>", lambda e, r=row: self._end_drag(e, r))

        name_label = tk.Label(card, text=name, bg=self.BG_PANEL, fg=self.FG,
                               font=self.FONT_BOLD, anchor="w")
        name_label.grid(row=0, column=1, sticky="w", padx=(0, 6), pady=6)

        note_entry = ttk.Entry(card)
        note_entry.grid(row=0, column=2, sticky="ew", padx=(0, 6), pady=6)
        if note:
            note_entry.insert(0, note)
        note_entry.bind("<KeyRelease>", lambda e: self.save_pending_rows())
        row["note_entry"] = note_entry

        chip = PriorityChip(card, parent_bg=self.BG_PANEL, initial=priority,
                             on_change=lambda v: self.save_pending_rows())
        chip.grid(row=0, column=3, sticky="w", padx=(0, 6), pady=4)
        row["priority_chip"] = chip

        time_label = tk.Label(card, text=self._relative_time(created_at),
                               bg=self.BG_PANEL, fg=self.FG_DIM, font=self.FONT_SMALL)
        time_label.grid(row=0, column=4, sticky="w", padx=(0, 6), pady=6)
        row["time_label"] = time_label

        var = tk.BooleanVar(value=False)
        check = ttk.Checkbutton(card, variable=var,
                                 command=lambda r=row: self.mark_done(r))
        check.grid(row=0, column=5, sticky="w", pady=6)
        row["var"] = var
        row["check"] = check

        self.rows.append(row)
        self._relayout()

        if not self._loading:
            self.name_entry.delete(0, "end")
            self.name_entry.focus_set()
            self.save_pending_rows()
            self._animate_add(card)

    def mark_done(self, row):
        if not row["var"].get():
            return  # unchecked, do nothing

        # Lock the row while it animates out so it can't be double-triggered
        row["check"].state(["disabled"])

        name = row["name"]
        note = row["note_entry"].get().strip()
        priority = row["priority_chip"].value

        def finalize():
            with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([name, note, priority,
                                  datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
            row["frame"].destroy()
            self.rows = [r for r in self.rows if r is not row]
            self.save_pending_rows()

        self._animate_remove(row, callback=finalize)

    # ---------- Search / filter ----------

    def _relayout(self):
        """Re-pack rows in self.rows order, hiding any that don't match the
        current search text."""
        term = self.search_var.get().strip().lower() if hasattr(self, "search_var") else ""
        for r in self.rows:
            r["frame"].pack_forget()
        for r in self.rows:
            if term and term not in r["name"].lower():
                continue
            r["frame"].pack(fill="x", pady=4)

    # ---------- Drag to reorder ----------

    def _start_drag(self, event, row):
        if self.search_var.get().strip():
            return  # keep reordering simple: disabled while filtering
        self._drag_row = row
        row["frame"].configure(highlightbackground=self.ACCENT)

    def _on_drag(self, event, row):
        if self._drag_row is not row:
            return
        y = event.y_root - self.list_frame.winfo_rooty()
        target_index = 0
        for i, r in enumerate(self.rows):
            fy = r["frame"].winfo_y() + r["frame"].winfo_height() / 2
            if y > fy:
                target_index = i + 1
        current_index = self.rows.index(row)
        if target_index != current_index:
            self.rows.pop(current_index)
            target_index = max(0, min(target_index, len(self.rows)))
            self.rows.insert(target_index, row)
            self._relayout()

    def _end_drag(self, event, row):
        if self._drag_row is row:
            row["frame"].configure(highlightbackground=self.FG_DIM)
        self._drag_row = None
        self.save_pending_rows()

    # ---------- Timestamps ----------

    def _relative_time(self, dt):
        secs = int((datetime.now() - dt).total_seconds())
        if secs < 60:
            return "now"
        mins = secs // 60
        if mins < 60:
            return f"{mins}m"
        hours = mins // 60
        if hours < 24:
            return f"{hours}h"
        days = hours // 24
        return f"{days}d"

    def _refresh_timestamps(self):
        for r in self.rows:
            try:
                r["time_label"].configure(text=self._relative_time(r["created_at"]))
            except tk.TclError:
                pass
        self.after(30000, self._refresh_timestamps)

    # ---------- Animations ----------

    def _animate_add(self, frame, step=0, steps=6):
        t = step / steps
        color = lerp_color(self.BG, self.BG_PANEL, t)
        try:
            frame.configure(bg=color)
        except tk.TclError:
            return
        if step < steps:
            self.after(20, lambda: self._animate_add(frame, step + 1, steps))

    def _animate_remove(self, row, step=0, steps=8, callback=None):
        frame = row["frame"]
        half = steps // 2
        if step <= half:
            color = lerp_color(self.BG_PANEL, self.SUCCESS, step / half)
        else:
            color = lerp_color(self.SUCCESS, self.BG, (step - half) / (steps - half))
        try:
            frame.configure(bg=color)
        except tk.TclError:
            if callback:
                callback()
            return
        if step < steps:
            self.after(25, lambda: self._animate_remove(row, step + 1, steps, callback))
        elif callback:
            callback()

    # ---------- Pending list persistence ----------

    def save_pending_rows(self):
        """Write all current (unfinished) rows to pending_activities.csv,
        in their current display order."""
        try:
            with open(PENDING_FILE, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Client Name", "Note / Issue", "Priority", "Added At"])
                for r in self.rows:
                    writer.writerow([
                        r["name"],
                        r["note_entry"].get().strip(),
                        r["priority_chip"].value,
                        r["created_at"].isoformat(timespec="seconds"),
                    ])
        except OSError as e:
            # Don't crash the app over a save hiccup, just let the user know
            messagebox.showwarning("Couldn't save pending list", str(e))

    def _load_pending_rows(self):
        """Recreate rows from pending_activities.csv, if it exists."""
        if not os.path.exists(PENDING_FILE):
            return

        self._loading = True
        try:
            with open(PENDING_FILE, "r", newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                next(reader, None)  # skip header
                for row in reader:
                    if not row:
                        continue
                    name = row[0].strip()
                    note = row[1].strip() if len(row) > 1 else ""
                    priority = row[2].strip() if len(row) > 2 else "Med"
                    ts_raw = row[3].strip() if len(row) > 3 else ""
                    try:
                        created_at = datetime.fromisoformat(ts_raw) if ts_raw else datetime.now()
                    except ValueError:
                        created_at = datetime.now()
                    if name:
                        self.add_row(name=name, note=note, priority=priority,
                                     created_at=created_at)
        except OSError:
            pass
        finally:
            self._loading = False

    def _on_close(self):
        self.save_pending_rows()
        self.destroy()


if __name__ == "__main__":
    app = ActivityTracker()
    app.mainloop()

#test