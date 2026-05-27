#!/usr/bin/env python3
"""
SageClip - Portable clipboard manager.
- Data saved alongside script (portable)
- System tray with 5 recent entries (click to copy)
- Dark theme, right-click context menu, text-only capture, dedup

Dependencies:  pip install pystray pillow
"""

import tkinter as tk
from tkinter import ttk, messagebox
import datetime
import json
import os
import sys
import time
import threading
import re

# ── Portable paths ─────────────────────────────────────────────────────────────
# Always resolve relative to the script itself, regardless of cwd or shortcut location.

if getattr(sys, "frozen", False):
    # Running as a PyInstaller bundle
    SCRIPT_DIR = os.path.dirname(sys.executable)
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

DATA_FILE = os.path.join(SCRIPT_DIR, "sageclip_data.json")

# ── Tray support ───────────────────────────────────────────────────────────────

try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
    TRAY_OK = True
except ImportError:
    TRAY_OK = False

# ── Data ───────────────────────────────────────────────────────────────────────

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_data(entries):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# ── Theme ──────────────────────────────────────────────────────────────────────

T = {
    "bg":       "#171614",
    "surface":  "#1c1b19",
    "surface2": "#232220",
    "surface3": "#2d2c2a",
    "border":   "#393836",
    "text":     "#cdccca",
    "muted":    "#797876",
    "faint":    "#5a5957",
    "primary":  "#4f98a3",
    "pri_dim":  "#2a5f68",
    "gold":     "#e8af34",
    "error":    "#c45c8a",
    "success":  "#6daa45",
    "fav_bg":   "#2d2a1a",
    "sel_bg":   "#1e3538",
    "hover_bg": "#242321",
}

# ── Tray icon ──────────────────────────────────────────────────────────────────

def _make_tray_image(size=64):
    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([2, 2, size - 2, size - 2], radius=12,
                            fill=(79, 152, 163, 255))
    bw, bh = int(size * 0.52), int(size * 0.62)
    bx = (size - bw) // 2
    by = int(size * 0.26)
    draw.rectangle([bx, by, bx + bw, by + bh], fill=(255, 255, 255, 220))
    cw = int(bw * 0.48)
    cx = (size - cw) // 2
    draw.rectangle([cx, by - int(size * 0.08), cx + cw, by + int(size * 0.06)],
                   fill=(79, 152, 163, 255))
    draw.rectangle([cx + 2, by - int(size * 0.06), cx + cw - 2, by + int(size * 0.04)],
                   fill=(200, 240, 245, 255))
    lx1 = bx + int(bw * 0.18)
    lx2 = bx + int(bw * 0.82)
    for frac in [0.38, 0.52, 0.66]:
        ly = by + int(bh * frac)
        draw.rectangle([lx1, ly, lx2, ly + 2], fill=(79, 152, 163, 180))
    return img

def _clip_label(text, max_chars=42):
    """Single-line preview, clipped to max_chars."""
    s = " ".join(text.split())
    if len(s) > max_chars:
        s = s[:max_chars - 1] + "…"
    return s if s else "(empty)"

# ── App ────────────────────────────────────────────────────────────────────────

class SageClip(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SageClip")
        self.geometry("756x490")
        self.minsize(560, 360)

        self.entries    = load_data()
        self.filtered   = []
        self.sel_id     = None
        self.last_clip  = ""
        self.monitoring = True
        self.search_var = tk.StringVar()
        self.fav_var    = tk.BooleanVar(value=False)
        self._tray_icon = None

        self._setup_style()
        self._build()
        self._refresh_list()
        self._start_monitor()
        self.protocol("WM_DELETE_WINDOW", self._minimise_to_tray)

    # ── style ──────────────────────────────────────────────────────────────────

    def _setup_style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("Vertical.TScrollbar",
                    background=T["surface3"], troughcolor=T["surface"],
                    bordercolor=T["border"], arrowcolor=T["muted"])

    def _bg_deep(self, widget, color):
        try:
            widget.configure(bg=color)
        except Exception:
            pass
        for c in widget.winfo_children():
            self._bg_deep(c, color)

    # ── tray ───────────────────────────────────────────────────────────────────

    def _build_tray_menu(self):
        """Rebuild tray menu each time so recent entries are current."""
        items = []

        # 5 most recent entries (newest last in list → reversed)
        recent = list(reversed(self.entries))[:5]
        if recent:
            for entry in recent:
                label = _clip_label(entry["text"], 42)
                # capture entry id in closure
                def make_copy_fn(eid):
                    def fn(icon, item):
                        e = next((x for x in self.entries if x["id"] == eid), None)
                        if e:
                            # Must run clipboard on main thread
                            self.after(0, lambda t=e["text"]: self._copy_text_silent(t))
                    return fn
                items.append(pystray.MenuItem(label, make_copy_fn(entry["id"])))
            items.append(pystray.Menu.SEPARATOR)

        items.append(pystray.MenuItem("📋  Open SageClip", self._restore_from_tray, default=True))
        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("✕  Quit", self._quit_from_tray))
        return pystray.Menu(*items)

    def _copy_text_silent(self, text):
        """Copy text to clipboard without triggering the monitor capture."""
        self.last_clip = text   # pre-empt the monitor
        self.clipboard_clear()
        self.clipboard_append(text)

    def _minimise_to_tray(self):
        if not TRAY_OK:
            self._on_close()
            return
        self.withdraw()
        if self._tray_icon is not None:
            # Rebuild menu with latest entries
            self._tray_icon.menu = self._build_tray_menu()
            return
        img = _make_tray_image()
        self._tray_icon = pystray.Icon(
            "SageClip", img, "SageClip", self._build_tray_menu()
        )
        t = threading.Thread(target=self._tray_icon.run, daemon=True)
        t.start()

    def _restore_from_tray(self, icon=None, item=None):
        if self._tray_icon:
            self._tray_icon.stop()
            self._tray_icon = None
        self.after(0, self._do_restore)

    def _do_restore(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def _quit_from_tray(self, icon=None, item=None):
        if self._tray_icon:
            self._tray_icon.stop()
            self._tray_icon = None
        self.monitoring = False
        self.after(0, self.destroy)

    # ── build ──────────────────────────────────────────────────────────────────

    def _build(self):
        self.configure(bg=T["bg"])

        # topbar
        bar = tk.Frame(self, bg=T["surface"], height=40)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        lf = tk.Frame(bar, bg=T["surface"])
        lf.pack(side="left", padx=12)
        tk.Label(lf, text="SC", bg=T["primary"], fg="#ffffff",
                 font=("Aptos", 9, "bold"), width=3, pady=2).pack(side="left")
        tk.Label(lf, text="  SageClip", bg=T["surface"], fg=T["primary"],
                 font=("Aptos", 11, "bold")).pack(side="left")

        self.count_lbl = tk.Label(bar, text="0 entries",
                                   bg=T["surface3"], fg=T["muted"],
                                   font=("Aptos", 8), padx=8, pady=2)
        self.count_lbl.pack(side="right", padx=8)

        self.status_lbl = tk.Label(bar, text="● Monitoring",
                                    bg=T["surface"], fg=T["success"],
                                    font=("Aptos", 8))
        self.status_lbl.pack(side="right", padx=6)

        if TRAY_OK:
            tk.Button(bar, text="⌁ Tray",
                      bg=T["surface"], fg=T["muted"],
                      relief="flat", font=("Aptos", 8),
                      bd=0, padx=8, pady=6, cursor="hand2",
                      activebackground=T["surface3"],
                      activeforeground=T["text"],
                      command=self._minimise_to_tray).pack(side="right", padx=4)
        else:
            tk.Label(bar, text="pip install pystray pillow  for tray",
                     bg=T["surface"], fg=T["faint"],
                     font=("Aptos", 7)).pack(side="right", padx=8)

        tk.Frame(self, bg=T["border"], height=1).pack(fill="x")

        # main layout
        main = tk.Frame(self, bg=T["bg"])
        main.pack(fill="both", expand=True)

        left = tk.Frame(main, bg=T["surface"], width=280)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        tk.Frame(main, bg=T["border"], width=1).pack(side="left", fill="y")

        # search
        sf = tk.Frame(left, bg=T["surface"], padx=8, pady=7)
        sf.pack(fill="x")
        si = tk.Frame(sf, bg=T["surface2"],
                      highlightthickness=1,
                      highlightbackground=T["border"],
                      highlightcolor=T["primary"])
        si.pack(fill="x")
        tk.Label(si, text="🔍", bg=T["surface2"], fg=T["muted"],
                 font=("Aptos", 10), padx=4).pack(side="left")
        self.search_entry = tk.Entry(si, textvariable=self.search_var,
                                      bg=T["surface2"], fg=T["text"],
                                      insertbackground=T["text"],
                                      relief="flat", font=("Aptos", 10),
                                      highlightthickness=0)
        self.search_entry.pack(side="left", fill="x", expand=True, pady=5, padx=(0, 6))
        self.search_entry.bind("<KeyRelease>", lambda e: self._refresh_list())

        # filter row
        ff = tk.Frame(left, bg=T["surface"], padx=8)
        ff.pack(fill="x")
        self.fav_chk = tk.Checkbutton(ff, text="★ Favourites",
                                       variable=self.fav_var,
                                       bg=T["surface"], fg=T["muted"],
                                       selectcolor=T["surface3"],
                                       activebackground=T["surface"],
                                       font=("Aptos", 9), cursor="hand2",
                                       command=self._refresh_list)
        self.fav_chk.pack(side="left")
        tk.Button(ff, text="Clear", bg=T["surface"], fg=T["faint"],
                  relief="flat", font=("Aptos", 9), bd=0, padx=3,
                  cursor="hand2", activebackground=T["surface3"],
                  command=self._clear_search).pack(side="right")
        tk.Button(ff, text="🗑 All", bg=T["surface"], fg=T["error"],
                  relief="flat", font=("Aptos", 9), bd=0, padx=3,
                  cursor="hand2", activebackground=T["surface3"],
                  command=self._delete_all).pack(side="right", padx=(0, 4))

        tk.Frame(left, bg=T["border"], height=1).pack(fill="x", pady=(4, 0))

        # scrollable list
        lf2 = tk.Frame(left, bg=T["surface"])
        lf2.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(lf2, bg=T["surface"], highlightthickness=0, bd=0)
        vsb = ttk.Scrollbar(lf2, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.list_frame = tk.Frame(self.canvas, bg=T["surface"])
        self._cwin = self.canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
        self.list_frame.bind("<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
            lambda e: self.canvas.itemconfig(self._cwin, width=e.width))
        self.canvas.bind_all("<MouseWheel>",
            lambda e: self.canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        # right panel
        right = tk.Frame(main, bg=T["bg"])
        right.pack(side="left", fill="both", expand=True)
        self._build_detail(right)

    def _build_detail(self, parent):
        dh = tk.Frame(parent, bg=T["bg"], pady=9, padx=14)
        dh.pack(fill="x")
        tk.Label(dh, text="Entry Detail", bg=T["bg"], fg=T["text"],
                 font=("Aptos", 11, "bold")).pack(side="left")

        br = tk.Frame(dh, bg=T["bg"])
        br.pack(side="right")

        self.fav_btn = tk.Button(br, text="☆ Fav",
                                  bg=T["surface3"], fg=T["text"],
                                  font=("Aptos", 9), relief="flat",
                                  padx=7, pady=3, cursor="hand2",
                                  activebackground=T["surface2"],
                                  command=self._toggle_fav)
        self.fav_btn.pack(side="left", padx=(0, 4))

        self.copy_btn = tk.Button(br, text="⎘ Copy",
                                   bg=T["primary"], fg="#ffffff",
                                   font=("Aptos", 9, "bold"), relief="flat",
                                   padx=7, pady=3, cursor="hand2",
                                   activebackground=T["pri_dim"],
                                   command=self._copy_entry)
        self.copy_btn.pack(side="left", padx=(0, 4))

        self.edit_btn = tk.Button(br, text="✎ Edit",
                                   bg=T["surface3"], fg=T["text"],
                                   font=("Aptos", 9), relief="flat",
                                   padx=7, pady=3, cursor="hand2",
                                   activebackground=T["surface2"],
                                   command=self._edit_entry)
        self.edit_btn.pack(side="left", padx=(0, 4))

        self.del_btn = tk.Button(br, text="🗑 Delete",
                                  bg=T["error"], fg="#ffffff",
                                  font=("Aptos", 9), relief="flat",
                                  padx=7, pady=3, cursor="hand2",
                                  activebackground=T["error"],
                                  command=self._delete_entry)
        self.del_btn.pack(side="left")

        tk.Frame(parent, bg=T["border"], height=1).pack(fill="x", padx=14)

        mf = tk.Frame(parent, bg=T["bg"], padx=14, pady=6)
        mf.pack(fill="x")
        self.ts_lbl = tk.Label(mf, text="Select an entry",
                                bg=T["bg"], fg=T["muted"],
                                font=("Aptos", 9))
        self.ts_lbl.pack(side="left")
        self.fav_badge = tk.Label(mf, text="★ Favourite",
                                   bg=T["fav_bg"], fg=T["gold"],
                                   font=("Aptos", 8, "bold"), padx=6, pady=1)

        cf = tk.Frame(parent, bg=T["surface2"],
                      highlightthickness=1, highlightbackground=T["border"])
        cf.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        self.detail_text = tk.Text(cf, bg=T["surface2"], fg=T["text"],
                                    font=("Aptos Mono", 10), relief="flat",
                                    insertbackground=T["text"],
                                    selectbackground=T["sel_bg"],
                                    selectforeground=T["text"],
                                    wrap="word", padx=10, pady=8,
                                    state="disabled", highlightthickness=0)
        ds = ttk.Scrollbar(cf, orient="vertical", command=self.detail_text.yview)
        self.detail_text.configure(yscrollcommand=ds.set)
        ds.pack(side="right", fill="y")
        self.detail_text.pack(fill="both", expand=True)

        # Data file path indicator at bottom
        path_bar = tk.Frame(parent, bg=T["bg"], padx=14, pady=2)
        path_bar.pack(fill="x")
        tk.Label(path_bar, text="💾 Data: {}".format(DATA_FILE),
                 bg=T["bg"], fg=T["faint"],
                 font=("Aptos", 7),
                 anchor="w").pack(fill="x")

    # ── context menu ───────────────────────────────────────────────────────────

    def _show_context_menu(self, event, entry):
        menu = tk.Menu(self, tearoff=0,
                       bg=T["surface2"], fg=T["text"],
                       activebackground=T["sel_bg"],
                       activeforeground=T["text"],
                       font=("Aptos", 9),
                       relief="flat", bd=1)
        fav_label = "★ Unfavourite" if entry.get("fav") else "☆ Favourite"
        menu.add_command(label="⎘  Copy to Clipboard",
                         command=lambda: self._copy_by_id(entry["id"]))
        menu.add_separator()
        menu.add_command(label=fav_label,
                         command=lambda: self._toggle_fav_by_id(entry["id"]))
        menu.add_command(label="✎  Edit",
                         command=lambda: self._edit_by_id(entry["id"]))
        menu.add_separator()
        menu.add_command(label="🗑  Delete",
                         command=lambda: self._delete_by_id(entry["id"]))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ── list rendering ─────────────────────────────────────────────────────────

    def _refresh_list(self):
        query    = self.search_var.get().lower().strip()
        fav_only = self.fav_var.get()

        self.filtered = []
        for e in reversed(self.entries):
            if fav_only and not e.get("fav"):
                continue
            if query and query not in e.get("text", "").lower():
                continue
            self.filtered.append(e)

        for w in self.list_frame.winfo_children():
            w.destroy()

        if not self.filtered:
            ef = tk.Frame(self.list_frame, bg=T["surface"])
            ef.pack(fill="x", padx=12, pady=40)
            tk.Label(ef, text="📭", bg=T["surface"],
                     font=("Aptos", 22)).pack()
            msg = "No entries yet" if not self.entries else "No matches"
            sub = "Copy some text to get started." if not self.entries else "Try a different search."
            tk.Label(ef, text=msg, bg=T["surface"], fg=T["muted"],
                     font=("Aptos", 10, "bold")).pack(pady=(6, 2))
            tk.Label(ef, text=sub, bg=T["surface"], fg=T["faint"],
                     font=("Aptos", 9)).pack()
        else:
            for entry in self.filtered:
                self._make_item(entry)

        total = len(self.entries)
        shown = len(self.filtered)
        label = "{} / {} entries".format(shown, total) if (query or fav_only) \
                else "{} entries".format(total)
        self.count_lbl.configure(text=label)

    def _make_item(self, entry):
        is_sel = (entry["id"] == self.sel_id)
        is_fav = entry.get("fav", False)
        bg = T["sel_bg"] if is_sel else (T["fav_bg"] if is_fav else T["surface"])

        item = tk.Frame(self.list_frame, bg=bg, cursor="hand2")
        item.pack(fill="x", padx=4, pady=1)

        stripe = tk.Frame(item, bg=T["gold"] if is_fav else bg, width=2)
        stripe.pack(side="left", fill="y")

        inner = tk.Frame(item, bg=bg, padx=7, pady=5)
        inner.pack(side="left", fill="both", expand=True)

        top_row = tk.Frame(inner, bg=bg)
        top_row.pack(fill="x")

        ts_label = tk.Label(top_row, text=entry.get("timestamp", ""),
                            bg=bg, fg=T["muted"], font=("Aptos", 8))
        ts_label.pack(side="left")
        if is_fav:
            tk.Label(top_row, text="★", bg=bg, fg=T["gold"],
                     font=("Aptos", 9)).pack(side="right")

        raw     = entry.get("text", "")
        preview = " ".join(raw.split())
        if len(preview) > 70:
            preview = preview[:70] + "…"

        prev_lbl = tk.Label(inner,
                            text=preview if preview else "(empty)",
                            bg=bg,
                            fg=T["text"] if preview else T["faint"],
                            font=("Aptos", 10),
                            anchor="w", justify="left", wraplength=240)
        prev_lbl.pack(fill="x", pady=(2, 0))

        def on_enter(e, f=item, orig=bg):
            if entry["id"] != self.sel_id:
                self._bg_deep(f, T["hover_bg"])

        def on_leave(e, f=item, orig=bg):
            if entry["id"] != self.sel_id:
                self._bg_deep(f, orig)

        def on_click(e, eid=entry["id"]):
            self.sel_id = eid
            self._refresh_list()
            self._show_detail(eid)

        def on_right(e, eid=entry["id"]):
            self.sel_id = eid
            self._refresh_list()
            self._show_detail(eid)
            self._show_context_menu(e, entry)

        for w in [item, inner, top_row, ts_label, prev_lbl, stripe]:
            w.bind("<Button-1>", on_click)
            w.bind("<Button-3>", on_right)
            w.bind("<Enter>", on_enter)
            w.bind("<Leave>", on_leave)

        tk.Frame(self.list_frame, bg=T["border"], height=1).pack(fill="x", padx=4)

    # ── detail panel ───────────────────────────────────────────────────────────

    def _show_detail(self, eid):
        entry = next((e for e in self.entries if e["id"] == eid), None)
        if not entry:
            return
        self.ts_lbl.configure(text="🕐  {}".format(entry.get("timestamp", "")))
        if entry.get("fav"):
            self.fav_badge.pack(side="left", padx=(10, 0))
            self.fav_btn.configure(text="★ Unfav", bg=T["gold"], fg="#1a1a14")
        else:
            self.fav_badge.pack_forget()
            self.fav_btn.configure(text="☆ Fav", bg=T["surface3"], fg=T["text"])
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")
        self.detail_text.insert("1.0", entry.get("text", ""))
        self.detail_text.configure(state="disabled")

    def _get_sel(self):
        if not self.sel_id:
            return None
        return next((e for e in self.entries if e["id"] == self.sel_id), None)

    # ── button dispatchers ─────────────────────────────────────────────────────

    def _toggle_fav(self):
        e = self._get_sel()
        if e: self._toggle_fav_by_id(e["id"])

    def _copy_entry(self):
        e = self._get_sel()
        if e: self._copy_by_id(e["id"])

    def _edit_entry(self):
        e = self._get_sel()
        if e: self._edit_by_id(e["id"])

    def _delete_entry(self):
        e = self._get_sel()
        if e: self._delete_by_id(e["id"])

    # ── id-based actions ───────────────────────────────────────────────────────

    def _toggle_fav_by_id(self, eid):
        entry = next((e for e in self.entries if e["id"] == eid), None)
        if not entry: return
        entry["fav"] = not entry.get("fav", False)
        save_data(self.entries)
        self._refresh_list()
        self._show_detail(eid)

    def _copy_by_id(self, eid):
        entry = next((e for e in self.entries if e["id"] == eid), None)
        if not entry: return
        self._copy_text_silent(entry.get("text", ""))
        orig_text = self.copy_btn.cget("text")
        orig_bg   = self.copy_btn.cget("bg")
        self.copy_btn.configure(text="✓ Copied!", bg=T["success"])
        self.after(1400, lambda: self.copy_btn.configure(text=orig_text, bg=orig_bg))

    def _edit_by_id(self, eid):
        entry = next((e for e in self.entries if e["id"] == eid), None)
        if not entry: return

        dlg = tk.Toplevel(self)
        dlg.title("Edit Entry")
        dlg.geometry("500x340")
        dlg.configure(bg=T["bg"])
        dlg.transient(self)
        dlg.grab_set()
        dlg.resizable(True, True)

        tk.Label(dlg, text="Edit entry content:",
                 bg=T["bg"], fg=T["text"],
                 font=("Aptos", 10, "bold")).pack(padx=14, pady=(12, 3), anchor="w")
        tk.Label(dlg, text="Created: {}".format(entry.get("timestamp", "")),
                 bg=T["bg"], fg=T["muted"],
                 font=("Aptos", 9)).pack(padx=14, anchor="w")

        tf = tk.Frame(dlg, bg=T["surface2"],
                      highlightthickness=1, highlightbackground=T["border"])
        tf.pack(fill="both", expand=True, padx=14, pady=8)

        et = tk.Text(tf, bg=T["surface2"], fg=T["text"],
                     font=("Aptos Mono", 10), relief="flat",
                     insertbackground=T["text"],
                     selectbackground=T["sel_bg"],
                     wrap="word", padx=10, pady=8,
                     highlightthickness=0)
        es = ttk.Scrollbar(tf, orient="vertical", command=et.yview)
        et.configure(yscrollcommand=es.set)
        es.pack(side="right", fill="y")
        et.pack(fill="both", expand=True)
        et.insert("1.0", entry.get("text", ""))
        et.focus_set()

        brow = tk.Frame(dlg, bg=T["bg"])
        brow.pack(fill="x", padx=14, pady=(0, 12))

        def do_save():
            entry["text"] = et.get("1.0", "end-1c")
            save_data(self.entries)
            self._refresh_list()
            self._show_detail(entry["id"])
            dlg.destroy()

        tk.Button(brow, text="Save Changes",
                  bg=T["primary"], fg="#ffffff",
                  font=("Aptos", 10, "bold"), relief="flat",
                  padx=10, pady=5, cursor="hand2",
                  activebackground=T["pri_dim"],
                  command=do_save).pack(side="left", padx=(0, 6))
        tk.Button(brow, text="Cancel",
                  bg=T["surface3"], fg=T["text"],
                  font=("Aptos", 10), relief="flat",
                  padx=10, pady=5, cursor="hand2",
                  activebackground=T["surface2"],
                  command=dlg.destroy).pack(side="left")

        dlg.bind("<Escape>", lambda e: dlg.destroy())

    def _delete_by_id(self, eid):
        entry = next((e for e in self.entries if e["id"] == eid), None)
        if not entry: return
        raw     = entry.get("text", "")
        preview = (raw[:55] + "...") if len(raw) > 55 else raw
        if messagebox.askyesno("Delete Entry",
                               "Permanently delete this entry?\n\n\"{}\"".format(preview),
                               parent=self):
            self.entries = [e for e in self.entries if e["id"] != eid]
            if self.sel_id == eid:
                self.sel_id = None
                self._clear_detail()
            save_data(self.entries)
            self._refresh_list()

    def _clear_detail(self):
        self.ts_lbl.configure(text="Select an entry")
        self.fav_badge.pack_forget()
        self.fav_btn.configure(text="☆ Fav", bg=T["surface3"], fg=T["text"])
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")
        self.detail_text.configure(state="disabled")

    # ── clipboard monitor ──────────────────────────────────────────────────────

    def _start_monitor(self):
        try:
            self.last_clip = self.clipboard_get()
        except Exception:
            self.last_clip = ""
        self._poll()

    def _is_file_path(self, text):
        lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
        if not lines:
            return False
        pat = re.compile(r'^(?:[a-zA-Z]:\\|\\\\|/)' r'|^\.{1,2}[/\\]')
        return all(pat.match(line) for line in lines)

    def _poll(self):
        if not self.monitoring:
            return
        try:
            current = self.clipboard_get()
            if current and current.strip() and current != self.last_clip:
                if not self._is_file_path(current):
                    self.last_clip = current
                    self._capture(current)
                else:
                    self.last_clip = current
        except Exception:
            pass
        self.after(10000, self._poll)

    def _capture(self, text):
        now    = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        new_id = str(int(time.time() * 1000))

        fav_state = False
        if any(e.get("text") == text for e in self.entries):
            old = next((e for e in self.entries if e.get("text") == text), None)
            if old:
                fav_state = old.get("fav", False)
            self.entries = [e for e in self.entries if e.get("text") != text]

        self.entries.append({
            "id":        new_id,
            "text":      text,
            "timestamp": now,
            "fav":       fav_state,
        })
        save_data(self.entries)

        # Keep tray menu fresh if it's open
        if self._tray_icon:
            try:
                self._tray_icon.menu = self._build_tray_menu()
            except Exception:
                pass

        self._refresh_list()
        self.status_lbl.configure(text="● Captured!", fg=T["primary"])
        self.after(2500, lambda: self.status_lbl.configure(
            text="● Monitoring", fg=T["success"]))

    # ── misc ───────────────────────────────────────────────────────────────────

    def _delete_all(self):
        if not self.entries:
            return
        count = len(self.entries)
        if messagebox.askyesno(
            "Delete All Entries",
            "Permanently delete all {} entries? This cannot be undone.".format(count),
            parent=self
        ):
            self.entries = []
            self.sel_id  = None
            save_data(self.entries)
            self._clear_detail()
            self._refresh_list()

    def _clear_search(self):
        self.search_var.set("")
        self.fav_var.set(False)
        self._refresh_list()

    def _on_close(self):
        self.monitoring = False
        self.destroy()


# ── entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = SageClip()
    app.mainloop()
