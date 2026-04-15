"""
Unifi Stock Watcher — GUI  (v2.0)
Tabs: Watcher | History | Settings
Features: price tracking, stock history, category filters, sound alerts,
          configurable poll interval, multi-region, export/import, statistics,
          auto-start, per-item quick-check, retry with backoff.
"""

import re, sys, json, time, threading, webbrowser, subprocess, ctypes
import tkinter as tk
from tkinter import ttk, messagebox, colorchooser, filedialog
from pathlib import Path
from datetime import datetime

# ── DPI fix (must be before Tk()) ─────────────────────────────────────────────
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# ── Core imports ──────────────────────────────────────────────────────────────
from unifi_core import (
    REQUESTS_OK, STORE_BASE, STORE_REGIONS, CATEGORIES, CATEGORY_LABELS,
    HEADERS, DEFAULT_SETTINGS,
    load_settings, save_settings, build_palette,
    get_build_id, invalidate_build_id, fetch_all_products,
    is_available, get_price, check_slug,
    load_watched, save_watched, stock_history,
    notify_windows, play_sound,
    export_watchlist, import_watchlist,
)


def store_home(region="us"):
    return f"{STORE_BASE}/{STORE_REGIONS[region]['path']}"


# ── Widget helpers ────────────────────────────────────────────────────────────

def hsep(parent, C, pady=(0, 0)):
    f = tk.Frame(parent, bg=C["border"], height=1)
    f.pack(fill="x", pady=pady)


def tooltip(widget, text):
    """Simple tooltip on hover."""
    tip = None
    def show(e):
        nonlocal tip
        tip = tk.Toplevel(widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{e.x_root+12}+{e.y_root+12}")
        lbl = tk.Label(tip, text=text, bg="#ffffe0", fg="#333",
                       font=("Segoe UI", 9), padx=6, pady=3, relief="solid", bd=1)
        lbl.pack()
    def hide(e):
        nonlocal tip
        if tip:
            tip.destroy()
            tip = None
    widget.bind("<Enter>", show, add="+")
    widget.bind("<Leave>", hide, add="+")


# ── Browse dialog ─────────────────────────────────────────────────────────────

class BrowseDialog(tk.Toplevel):
    def __init__(self, parent, already_watching, on_add, C, settings):
        super().__init__(parent)
        self.on_add           = on_add
        self.already_watching = {w["slug"] for w in already_watching}
        self.C                = C
        self.settings         = settings
        self.all_prods        = []
        self.filtered         = []
        self.check_vars       = {}

        self.title("Add Items to Watch List")
        self.configure(bg=C["bg"])
        self.geometry("760x660")
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()
        self._build()
        self._fetch()

    def F(self, size_delta=0, bold=False):
        return (self.settings["font_family"],
                self.settings["font_size"] + size_delta,
                "bold" if bold else "normal")

    def _build(self):
        C = self.C
        # Header
        hdr = tk.Frame(self, bg=C["panel"], pady=14)
        hdr.pack(fill="x")
        tk.Label(hdr, text="Browse Products",
                 bg=C["panel"], fg=C["text"], font=self.F(3, True)).pack(
                     side="left", padx=20)
        self.status_lbl = tk.Label(hdr, text="Fetching…",
                                   bg=C["panel"], fg=C["muted"], font=self.F(-1))
        self.status_lbl.pack(side="right", padx=20)

        # Progress bar
        self.prog_var = tk.IntVar()
        style = ttk.Style()
        style.configure("D.Horizontal.TProgressbar",
                        background=C["accent"], troughcolor=C["panel"])
        self.prog = ttk.Progressbar(self, variable=self.prog_var, maximum=100,
                                    style="D.Horizontal.TProgressbar")
        self.prog.pack(fill="x")

        # Filter toolbar
        bar = tk.Frame(self, bg=C["bg"], pady=8, padx=16)
        bar.pack(fill="x")

        # Category filter
        tk.Label(bar, text="Category:", bg=C["bg"], fg=C["muted"],
                 font=self.F(-1)).pack(side="left", padx=(0, 4))
        self._cat_var = tk.StringVar(value="All")
        cat_names = ["All"] + [CATEGORY_LABELS.get(c, c) for c in CATEGORIES]
        cat_menu = ttk.Combobox(bar, textvariable=self._cat_var,
                                values=cat_names, width=18, state="readonly")
        cat_menu.pack(side="left", padx=(0, 12))
        cat_menu.bind("<<ComboboxSelected>>", lambda _: self._filter())

        # Show in-stock toggle
        self._stock_var = tk.BooleanVar(value=False)
        tk.Checkbutton(bar, text="Show in-stock", variable=self._stock_var,
                       command=self._filter,
                       bg=C["bg"], fg=C["muted"], selectcolor=C["panel"],
                       activebackground=C["bg"], activeforeground=C["text"],
                       font=self.F(-1), bd=0, highlightthickness=0,
                       cursor="hand2").pack(side="left", padx=(0, 12))

        # Select all
        self.all_var = tk.BooleanVar(value=False)
        tk.Checkbutton(bar, text="Select all", variable=self.all_var,
                       command=self._toggle_all,
                       bg=C["bg"], fg=C["muted"], selectcolor=C["panel"],
                       activebackground=C["bg"], activeforeground=C["text"],
                       font=self.F(-1), bd=0, highlightthickness=0,
                       cursor="hand2").pack(side="left", padx=(0, 12))

        # Search
        tk.Label(bar, text="Search:", bg=C["bg"], fg=C["muted"],
                 font=self.F(-1)).pack(side="left")
        self.q = tk.StringVar()
        self.q.trace_add("write", lambda *_: self._filter())
        e = tk.Entry(bar, textvariable=self.q, bg=C["panel"], fg=C["text"],
                     insertbackground=C["text"], relief="flat",
                     font=self.F(), bd=0)
        e.pack(side="left", fill="x", expand=True, padx=8, ipady=6)
        e.focus_set()

        hsep(self, C)

        # Scrollable checkbox list
        wrap = tk.Frame(self, bg=C["bg"])
        wrap.pack(fill="both", expand=True)
        self.cv = tk.Canvas(wrap, bg=C["bg"], highlightthickness=0, bd=0)
        sb = tk.Scrollbar(wrap, orient="vertical", command=self.cv.yview,
                          bg=C["panel"], troughcolor=C["bg"], relief="flat", width=10)
        self.cv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.cv.pack(side="left", fill="both", expand=True)
        self.cb_frame = tk.Frame(self.cv, bg=C["bg"])
        self._cw = self.cv.create_window((0, 0), window=self.cb_frame, anchor="nw")
        self.cb_frame.bind("<Configure>",
            lambda e: self.cv.configure(scrollregion=self.cv.bbox("all")))
        self.cv.bind("<Configure>",
            lambda e: self.cv.itemconfig(self._cw, width=e.width))
        self.cv.bind_all("<MouseWheel>",
            lambda e: self.cv.yview_scroll(-1*(e.delta//120), "units"))

        hsep(self, C)

        # Footer
        foot = tk.Frame(self, bg=C["panel"], pady=10, padx=16)
        foot.pack(fill="x")
        self.sel_lbl = tk.Label(foot, text="0 selected",
                                bg=C["panel"], fg=C["muted"], font=self.F(-1))
        self.sel_lbl.pack(side="left")
        tk.Button(foot, text="Cancel", command=self.destroy,
                  bg=C["panel"], fg=C["text"], relief="flat", bd=0,
                  cursor="hand2", font=self.F(),
                  padx=14, pady=7).pack(side="right", padx=(6, 0))
        tk.Button(foot, text="Add to Watch List", command=self._confirm,
                  bg=C["accent"], fg=C["white"], relief="flat", bd=0,
                  cursor="hand2", font=self.F(0, True),
                  padx=14, pady=7).pack(side="right")

    def _fetch(self):
        def _progress(pct):
            try:
                self.prog_var.set(pct)
                self.status_lbl.config(text=f"Fetching… {pct}%")
            except Exception:
                pass

        def run():
            try:
                region = self.settings.get("region", "us")
                self.after(0, lambda: self.status_lbl.config(
                    text=f"Connecting to store ({region.upper()})…"))
                bid = get_build_id(region)
                self.after(0, lambda: self.status_lbl.config(
                    text="Fetching products…"))
                all_ = fetch_all_products(
                    bid, region,
                    progress_cb=lambda p: self.after(0, _progress, p))
                self.all_prods = sorted(all_, key=lambda p: p.get("title", ""))
                self.after(0, self._on_fetched)
            except Exception as ex:
                import traceback
                tb = traceback.format_exc()
                print(f"[UnifiWatcher] Browse fetch error:\n{tb}")
                self.after(0, lambda: self.status_lbl.config(
                    text=f"Error: {ex}", fg=self.C["red"]))
                self.after(0, self.prog.pack_forget)
        threading.Thread(target=run, daemon=True).start()

    def _on_fetched(self):
        try:
            self._filter()
            n_oos = sum(1 for p in self.all_prods if not is_available(p))
            self.status_lbl.config(
                text=f"{len(self.all_prods)} products ({n_oos} out of stock)",
                fg=self.C["muted"])
            self.prog.pack_forget()
        except Exception as ex:
            import traceback
            tb = traceback.format_exc()
            print(f"[UnifiWatcher] Browse populate error:\n{tb}")
            self.status_lbl.config(
                text=f"Error displaying products: {ex}", fg=self.C["red"])
            self.prog.pack_forget()

    def _filter(self):
        q = self.q.get().lower()
        show_all = self._stock_var.get()
        cat_sel  = self._cat_var.get()

        cat_key = None
        if cat_sel != "All":
            for k, v in CATEGORY_LABELS.items():
                if v == cat_sel:
                    cat_key = k
                    break

        self.filtered = []
        for p in self.all_prods:
            if not show_all and is_available(p):
                continue
            if q and q not in p.get("title", "").lower():
                continue
            if cat_key and p.get("_category") != cat_key:
                continue
            self.filtered.append(p)
        self._rebuild()
        self.status_lbl.config(
            text=f"{len(self.filtered)} item(s)", fg=self.C["muted"])

    def _rebuild(self):
        C = self.C
        for w in self.cb_frame.winfo_children():
            w.destroy()
        for p in self.filtered:
            slug  = p["slug"]
            title = p.get("title", slug)
            price = get_price(p) or ""
            avail = is_available(p)
            if slug not in self.check_vars:
                self.check_vars[slug] = tk.BooleanVar(value=False)
            already = slug in self.already_watching

            row = tk.Frame(self.cb_frame, bg=C["bg"])
            row.pack(fill="x", padx=8, pady=1)

            label_text = f"  {title}" + (" (watching)" if already else "")
            cb = tk.Checkbutton(
                row, text=label_text, variable=self.check_vars[slug],
                command=self._count,
                bg=C["bg"],
                fg=C["muted"] if already else (C["green"] if avail else C["text"]),
                selectcolor=C["panel"],
                activebackground=C["hover"], activeforeground=C["text"],
                font=self.F(), bd=0, highlightthickness=0, anchor="w",
                cursor="arrow" if already else "hand2",
                state="disabled" if already else "normal")
            cb.pack(side="left", fill="x", expand=True)

            if price:
                tk.Label(row, text=price, bg=C["bg"], fg=C["muted"],
                         font=self.F(-1)).pack(side="right", padx=(0, 8))
            if avail:
                tk.Label(row, text="IN STOCK", bg="#1a3a2a", fg=C["green"],
                         font=self.F(-2, True), padx=5, pady=1).pack(
                             side="right", padx=(0, 4))

            if not already:
                row.bind("<Enter>", lambda e, r=row: r.config(bg=C["hover"]))
                row.bind("<Leave>", lambda e, r=row: r.config(bg=C["bg"]))
                cb.bind("<Enter>",  lambda e, r=row: r.config(bg=C["hover"]))
                cb.bind("<Leave>",  lambda e, r=row: r.config(bg=C["bg"]))

    def _toggle_all(self):
        v = self.all_var.get()
        for p in self.filtered:
            if p["slug"] not in self.already_watching:
                self.check_vars.setdefault(p["slug"], tk.BooleanVar()).set(v)
        self._count()

    def _count(self):
        n = sum(1 for v in self.check_vars.values() if v.get())
        self.sel_lbl.config(text=f"{n} selected")

    def _confirm(self):
        picks = []
        for p in self.all_prods:
            if (self.check_vars.get(p["slug"], tk.BooleanVar()).get()
                    and p["slug"] not in self.already_watching):
                picks.append({
                    "title":     p.get("title", p["slug"]),
                    "slug":      p["slug"],
                    "favourite": False,
                    "price":     get_price(p),
                    "added_at":  datetime.now().isoformat(),
                })
        if not picks:
            messagebox.showwarning("Nothing selected",
                                   "Check at least one item.", parent=self)
            return
        self.on_add(picks)
        self.destroy()


# ── Watched item row ──────────────────────────────────────────────────────────

class WatchedRow(tk.Frame):
    def __init__(self, parent, item, on_remove, on_toggle_fav, on_quick_check, C, settings):
        bg = C["fav_bg"] if item.get("favourite") else C["panel"]
        super().__init__(parent, bg=bg, pady=8, padx=12,
                         highlightbackground=C["border"], highlightthickness=1)
        self.item            = item
        self.on_remove       = on_remove
        self.on_toggle_fav   = on_toggle_fav
        self.on_quick_check  = on_quick_check
        self.C               = C
        self.settings        = settings
        self.in_stock        = None
        self._build()

    def F(self, delta=0, bold=False):
        return (self.settings["font_family"],
                self.settings["font_size"] + delta,
                "bold" if bold else "normal")

    def _build(self):
        C  = self.C
        bg = self.cget("bg")
        is_fav = self.item.get("favourite", False)
        region = self.settings.get("region", "us")

        # Star
        self.star = tk.Label(self, text="★" if is_fav else "☆",
                             bg=bg, fg=C["gold"] if is_fav else C["muted"],
                             font=self.F(3), cursor="hand2")
        self.star.pack(side="left", padx=(0, 8))
        self.star.bind("<Button-1>", lambda _: self.on_toggle_fav(self.item))
        self.star.bind("<Enter>", lambda _: self.star.config(fg=C["gold"]))
        self.star.bind("<Leave>", lambda _: self.star.config(
            fg=C["gold"] if self.item.get("favourite") else C["muted"]))
        tooltip(self.star, "Toggle favourite")

        # Status dot
        self.dot = tk.Label(self, text="●", bg=bg, fg=C["muted"], font=self.F(2))
        self.dot.pack(side="left", padx=(0, 8))

        # Title + subtitle + price
        info = tk.Frame(self, bg=bg)
        info.pack(side="left", fill="x", expand=True)
        title_row = tk.Frame(info, bg=bg)
        title_row.pack(fill="x")

        self.title_lbl = tk.Label(title_row, text=self.item["title"],
                                  bg=bg, fg=C["text"], font=self.F(0, True),
                                  anchor="w", cursor="hand2")
        self.title_lbl.pack(side="left")
        self.title_lbl.bind("<Button-1>",
            lambda _: webbrowser.open(
                f"{store_home(region)}/products/{self.item['slug']}"))
        self.title_lbl.bind("<Enter>",
            lambda _: self.title_lbl.config(fg=C["accent_h"]))
        self.title_lbl.bind("<Leave>",
            lambda _: self.title_lbl.config(fg=C["text"]))

        if is_fav:
            tk.Label(title_row, text="  ★ FAVOURITE", bg=bg, fg=C["gold"],
                     font=self.F(-2, True)).pack(side="left")

        price = self.item.get("price")
        if price:
            tk.Label(title_row, text=f"  {price}", bg=bg, fg=C["muted"],
                     font=self.F(-1)).pack(side="left")

        self.sub = tk.Label(info, text="Waiting for first check…",
                            bg=bg, fg=C["muted"], font=self.F(-2), anchor="w")
        self.sub.pack(fill="x")

        # Right-side controls
        right = tk.Frame(self, bg=bg)
        right.pack(side="right")

        # Quick check
        check_btn = tk.Button(
            right, text="⟳",
            command=lambda: self.on_quick_check(self.item),
            bg=C["tag_bg"], fg=C["muted"],
            activebackground=C["accent"], activeforeground=C["white"],
            relief="flat", bd=0, cursor="hand2",
            font=self.F(1), padx=6, pady=2)
        check_btn.pack(side="left", padx=(0, 4))
        tooltip(check_btn, "Check this item now")

        # Open page
        self.store_btn = tk.Button(
            right, text="Open Page",
            command=lambda: webbrowser.open(
                f"{store_home(region)}/products/{self.item['slug']}"),
            bg=C["tag_bg"], fg=C["muted"],
            activebackground=C["accent"], activeforeground=C["white"],
            relief="flat", bd=0, cursor="hand2",
            font=self.F(-1), padx=8, pady=3)
        self.store_btn.pack(side="left", padx=(0, 8))

        # Status badge
        self.badge = tk.Label(right, text="UNKNOWN",
                              bg=C["tag_bg"], fg=C["muted"],
                              font=self.F(-2, True), padx=8, pady=3)
        self.badge.pack(side="left", padx=(0, 8))

        # Remove
        rem = tk.Label(right, text="✕", bg=bg, fg=C["muted"],
                       font=self.F(0), cursor="hand2")
        rem.pack(side="left")
        rem.bind("<Button-1>", lambda _: self.on_remove(self.item))
        rem.bind("<Enter>",    lambda _: rem.config(fg=C["red"]))
        rem.bind("<Leave>",    lambda _: rem.config(fg=C["muted"]))
        tooltip(rem, "Remove from watch list")

    def update_status(self, in_stock, checked_at=None, price=None):
        C = self.C
        self.in_stock = in_stock
        if in_stock is None:
            self.dot.config(fg=C["muted"])
            self.badge.config(text="UNKNOWN", bg=C["tag_bg"], fg=C["muted"])
            self.store_btn.config(bg=C["tag_bg"], fg=C["muted"])
        elif in_stock:
            self.dot.config(fg=C["green"])
            self.badge.config(text="IN STOCK", bg="#1a3a2a", fg=C["green"])
            self.store_btn.config(bg=C["green"], fg="#000000", font=self.F(-1, True))
        else:
            self.dot.config(fg=C["red"])
            self.badge.config(text="OUT OF STOCK", bg="#2a1a1a", fg=C["red"])
            self.store_btn.config(bg=C["tag_bg"], fg=C["muted"], font=self.F(-1, False))
        sub_parts = []
        if checked_at:
            sub_parts.append(f"Checked {checked_at}")
        if price:
            sub_parts.append(price)
        if sub_parts:
            self.sub.config(text="  ·  ".join(sub_parts))


# ── Section header ────────────────────────────────────────────────────────────

class SectionHeader(tk.Frame):
    def __init__(self, parent, text, C, settings):
        super().__init__(parent, bg=C["bg"], pady=6, padx=14)
        tk.Label(self, text=text, bg=C["bg"], fg=C["muted"],
                 font=(settings["font_family"],
                       settings["font_size"] - 2, "bold")).pack(side="left")


# ── History tab ───────────────────────────────────────────────────────────────

class HistoryTab(tk.Frame):
    def __init__(self, parent, C, settings):
        super().__init__(parent, bg=C["bg"])
        self.C = C
        self.settings = settings
        self._build()

    def F(self, d=0, b=False):
        return (self.settings["font_family"],
                self.settings["font_size"] + d,
                "bold" if b else "normal")

    def _build(self):
        C = self.C
        # Stats summary
        stats_frame = tk.Frame(self, bg=C["panel"], pady=14, padx=20)
        stats_frame.pack(fill="x")
        tk.Label(stats_frame, text="Statistics", bg=C["panel"], fg=C["text"],
                 font=self.F(2, True)).pack(side="left")
        self.stats_lbl = tk.Label(stats_frame, text="", bg=C["panel"],
                                  fg=C["muted"], font=self.F(-1))
        self.stats_lbl.pack(side="right")

        hsep(self, C)

        # Toolbar
        tbar = tk.Frame(self, bg=C["bg"], pady=8, padx=16)
        tbar.pack(fill="x")
        tk.Button(tbar, text="Refresh", command=self.refresh,
                  bg=C["panel"], fg=C["text"], relief="flat", bd=0,
                  cursor="hand2", font=self.F(-1), padx=10, pady=4
                  ).pack(side="left", padx=(0, 8))
        tk.Button(tbar, text="Clear History", command=self._clear,
                  bg=C["panel"], fg=C["red"], relief="flat", bd=0,
                  cursor="hand2", font=self.F(-1), padx=10, pady=4
                  ).pack(side="left")

        hsep(self, C)

        # Event log
        wrap = tk.Frame(self, bg=C["bg"])
        wrap.pack(fill="both", expand=True, padx=10, pady=8)
        sb = tk.Scrollbar(wrap, bg=C["panel"], troughcolor=C["bg"],
                          relief="flat", width=8)
        sb.pack(side="right", fill="y")
        self.log = tk.Text(wrap, bg=C["panel"], fg=C["text"],
                           font=("Consolas", max(8, self.settings["font_size"] - 1)),
                           relief="flat", bd=0, state="disabled", wrap="word",
                           yscrollcommand=sb.set, highlightthickness=0)
        self.log.pack(fill="both", expand=True)
        sb.config(command=self.log.yview)
        self.log.tag_config("ts",    foreground="#444d56")
        self.log.tag_config("ok",    foreground=C["green"])
        self.log.tag_config("oos",   foreground=C["red"])
        self.log.tag_config("price", foreground=C["gold"])

    def refresh(self):
        stats  = stock_history.get_stats()
        events = stock_history.get_events(limit=200)
        self.stats_lbl.config(
            text=f"Total checks: {stats['total_checks']}  ·  "
                 f"In-stock alerts: {stats['in_stock_alerts']}")
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        for ev in reversed(events):
            ts = ev["ts"][:19].replace("T", " ")
            status = "IN STOCK" if ev["in_stock"] else "out of stock"
            tag = "ok" if ev["in_stock"] else "oos"
            self.log.insert("end", f"[{ts}] ", "ts")
            self.log.insert("end", f"{status:<14}", tag)
            self.log.insert("end", f"  {ev['title']}")
            if ev.get("price"):
                self.log.insert("end", f"  {ev['price']}", "price")
            self.log.insert("end", "\n")
        self.log.config(state="disabled")

    def _clear(self):
        if messagebox.askyesno("Clear History",
                               "Delete all stock check history?", parent=self):
            stock_history.clear()
            self.refresh()


# ── Settings tab ──────────────────────────────────────────────────────────────

class SettingsTab(tk.Frame):
    COLOUR_KEYS = [
        ("bg",      "Background"),
        ("panel",   "Panel / Card"),
        ("accent",  "Accent (buttons, links)"),
        ("green",   "In Stock colour"),
        ("red",     "Out of Stock colour"),
        ("gold",    "Favourite star colour"),
        ("text",    "Primary text"),
        ("muted",   "Secondary text"),
    ]
    FONT_OPTIONS = ["Segoe UI", "Arial", "Calibri", "Verdana",
                    "Tahoma", "Trebuchet MS", "Courier New", "Consolas"]
    SIZE_OPTIONS = [8, 9, 10, 11, 12, 13, 14]
    PRESETS = {
        "Dark (default)": {
            "bg": "#0d1117", "panel": "#161b22", "accent": "#1f6feb",
            "green": "#3fb950", "red": "#f85149", "gold": "#e3b341",
            "text": "#e6edf3", "muted": "#7d8590",
        },
        "Midnight Blue": {
            "bg": "#0a0f1e", "panel": "#111827", "accent": "#3b82f6",
            "green": "#22c55e", "red": "#ef4444", "gold": "#f59e0b",
            "text": "#f1f5f9", "muted": "#64748b",
        },
        "Slate": {
            "bg": "#1e293b", "panel": "#273548", "accent": "#38bdf8",
            "green": "#4ade80", "red": "#fb7185", "gold": "#fbbf24",
            "text": "#f8fafc", "muted": "#94a3b8",
        },
        "Light": {
            "bg": "#f0f4f8", "panel": "#ffffff", "accent": "#2563eb",
            "green": "#16a34a", "red": "#dc2626", "gold": "#d97706",
            "text": "#1e293b", "muted": "#64748b",
        },
        "UniFi Blue": {
            "bg": "#0b1628", "panel": "#122040", "accent": "#006fff",
            "green": "#00e676", "red": "#ff5252", "gold": "#ffc107",
            "text": "#e8edf5", "muted": "#7889a0",
        },
    }

    def __init__(self, parent, settings, on_apply):
        super().__init__(parent, bg="#0d1117")
        self.settings = settings.copy()
        self.on_apply = on_apply
        self._vars     = {}
        self._swatches = {}
        self._build()

    def _build(self):
        bg = self.settings.get("bg", "#0d1117")
        C  = build_palette(self.settings)

        canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        sb = tk.Scrollbar(self, orient="vertical", command=canvas.yview,
                          bg=C["panel"], troughcolor=bg, relief="flat", width=10)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(canvas, bg=bg, padx=28, pady=24)
        cw = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
            lambda e: canvas.itemconfig(cw, width=e.width))

        ff  = (self.settings["font_family"], self.settings["font_size"])
        ffb = (self.settings["font_family"], self.settings["font_size"], "bold")
        ffs = (self.settings["font_family"], self.settings["font_size"] - 1)

        def head(text, pady_top=16):
            tk.Label(inner, text=text, bg=bg, fg=C["text"],
                     font=(self.settings["font_family"],
                           self.settings["font_size"] + 1, "bold"),
                     anchor="w").pack(fill="x", pady=(pady_top, 6))
            tk.Frame(inner, bg=C["border"], height=1).pack(fill="x", pady=(0, 12))

        # ── Behaviour ─────────────────────────────────────────────────
        head("Behaviour", pady_top=0)

        r1 = tk.Frame(inner, bg=bg); r1.pack(fill="x", pady=(0, 8))
        tk.Label(r1, text="Poll interval (seconds)", bg=bg, fg=C["muted"],
                 font=ffs, width=24, anchor="w").pack(side="left")
        self._vars["poll_interval"] = tk.IntVar(
            value=self.settings.get("poll_interval", 60))
        tk.Spinbox(r1, from_=15, to=600, increment=15,
                   textvariable=self._vars["poll_interval"],
                   bg=C["panel"], fg=C["text"], font=ff, width=6,
                   relief="flat", bd=1, buttonbackground=C["panel"]
                   ).pack(side="left")

        r2 = tk.Frame(inner, bg=bg); r2.pack(fill="x", pady=(0, 8))
        self._vars["sound_alerts"] = tk.BooleanVar(
            value=self.settings.get("sound_alerts", True))
        tk.Checkbutton(r2, text="Play sound on in-stock alerts",
                       variable=self._vars["sound_alerts"],
                       bg=bg, fg=C["text"], selectcolor=C["panel"],
                       activebackground=bg, activeforeground=C["text"],
                       font=ff, bd=0, highlightthickness=0).pack(side="left")

        r3 = tk.Frame(inner, bg=bg); r3.pack(fill="x", pady=(0, 8))
        self._vars["auto_open_url"] = tk.BooleanVar(
            value=self.settings.get("auto_open_url", True))
        tk.Checkbutton(r3, text="Auto-open store page when in stock",
                       variable=self._vars["auto_open_url"],
                       bg=bg, fg=C["text"], selectcolor=C["panel"],
                       activebackground=bg, activeforeground=C["text"],
                       font=ff, bd=0, highlightthickness=0).pack(side="left")

        r4 = tk.Frame(inner, bg=bg); r4.pack(fill="x", pady=(0, 8))
        self._vars["auto_start"] = tk.BooleanVar(
            value=self.settings.get("auto_start", False))
        tk.Checkbutton(r4, text="Auto-start watching on launch",
                       variable=self._vars["auto_start"],
                       bg=bg, fg=C["text"], selectcolor=C["panel"],
                       activebackground=bg, activeforeground=C["text"],
                       font=ff, bd=0, highlightthickness=0).pack(side="left")

        r5 = tk.Frame(inner, bg=bg); r5.pack(fill="x", pady=(0, 8))
        tk.Label(r5, text="Store region", bg=bg, fg=C["muted"],
                 font=ffs, width=24, anchor="w").pack(side="left")
        self._vars["region"] = tk.StringVar(
            value=self.settings.get("region", "us"))
        ttk.Combobox(r5, textvariable=self._vars["region"],
                     values=list(STORE_REGIONS.keys()), width=10,
                     state="readonly").pack(side="left")
        self._region_lbl = tk.Label(r5, text="", bg=bg, fg=C["muted"], font=ffs)
        self._region_lbl.pack(side="left", padx=(8, 0))
        def _upd_rgn(*_):
            rgn = self._vars["region"].get()
            self._region_lbl.config(text=STORE_REGIONS.get(rgn, {}).get("label", ""))
        self._vars["region"].trace_add("write", _upd_rgn)
        _upd_rgn()

        # ── Typography ────────────────────────────────────────────────
        head("Typography")
        row = tk.Frame(inner, bg=bg); row.pack(fill="x", pady=(0, 12))
        tk.Label(row, text="Font family", bg=bg, fg=C["muted"],
                 font=ffs, width=18, anchor="w").pack(side="left")
        self._vars["font_family"] = tk.StringVar(value=self.settings["font_family"])
        ttk.Combobox(row, textvariable=self._vars["font_family"],
                     values=self.FONT_OPTIONS, width=20, state="readonly").pack(side="left", padx=(0, 24))
        tk.Label(row, text="Font size", bg=bg, fg=C["muted"],
                 font=ffs, width=12, anchor="w").pack(side="left")
        self._vars["font_size"] = tk.IntVar(value=self.settings["font_size"])
        ttk.Combobox(row, textvariable=self._vars["font_size"],
                     values=self.SIZE_OPTIONS, width=6, state="readonly").pack(side="left")

        # ── Colour presets ────────────────────────────────────────────
        head("Colour Presets")
        preset_row = tk.Frame(inner, bg=bg); preset_row.pack(fill="x", pady=(0, 16))
        for name in self.PRESETS:
            tk.Button(preset_row, text=name, command=lambda n=name: self._apply_preset(n),
                      bg=C["panel"], fg=C["text"], relief="flat", bd=0,
                      cursor="hand2", font=ffs, padx=12, pady=5,
                      activebackground=C["hover"], activeforeground=C["text"]
                      ).pack(side="left", padx=(0, 8))

        # ── Individual colours ────────────────────────────────────────
        head("Individual Colours")
        for key, label in self.COLOUR_KEYS:
            r = tk.Frame(inner, bg=bg); r.pack(fill="x", pady=5)
            tk.Label(r, text=label, bg=bg, fg=C["text"], font=ff, width=24, anchor="w").pack(side="left")
            swatch = tk.Label(r, text="       ", bg=self.settings.get(key, "#888888"),
                              relief="flat", cursor="hand2", bd=1,
                              highlightbackground=C["border"], highlightthickness=1)
            swatch.pack(side="left", padx=(0, 10), ipady=4)
            swatch.bind("<Button-1>", lambda e, k=key: self._pick_colour(k))
            self._swatches[key] = swatch
            val_lbl = tk.Label(r, text=self.settings.get(key, "#888888"), bg=bg,
                               fg=C["muted"], font=("Consolas", self.settings["font_size"] - 1))
            val_lbl.pack(side="left")
            self._vars[f"_clbl_{key}"] = val_lbl

        # ── Watch List export/import ──────────────────────────────────
        head("Watch List")
        ei_row = tk.Frame(inner, bg=bg); ei_row.pack(fill="x", pady=(0, 12))
        tk.Button(ei_row, text="Export Watch List", command=self._export,
                  bg=C["panel"], fg=C["text"], relief="flat", bd=0,
                  cursor="hand2", font=ff, padx=14, pady=7,
                  activebackground=C["hover"]).pack(side="left", padx=(0, 8))
        tk.Button(ei_row, text="Import Watch List", command=self._import,
                  bg=C["panel"], fg=C["text"], relief="flat", bd=0,
                  cursor="hand2", font=ff, padx=14, pady=7,
                  activebackground=C["hover"]).pack(side="left")

        # ── Apply / Reset ─────────────────────────────────────────────
        tk.Frame(inner, bg=C["border"], height=1).pack(fill="x", pady=(24, 12))
        btn_row = tk.Frame(inner, bg=bg); btn_row.pack(fill="x")
        tk.Button(btn_row, text="Reset to Default", command=self._reset,
                  bg=C["panel"], fg=C["text"], relief="flat", bd=0,
                  cursor="hand2", font=ff, padx=14, pady=7,
                  activebackground=C["hover"]).pack(side="left")
        tk.Button(btn_row, text="Apply & Restart View", command=self._apply,
                  bg=C["accent"], fg=C["white"], relief="flat", bd=0,
                  cursor="hand2", font=ffb, padx=14, pady=7,
                  activebackground=C["accent_h"]).pack(side="right")

    def _pick_colour(self, key):
        initial = self.settings.get(key, "#888888")
        result = colorchooser.askcolor(color=initial, title=f"Choose {key} colour", parent=self)
        if result and result[1]:
            hex_val = result[1].lower()
            self.settings[key] = hex_val
            self._swatches[key].config(bg=hex_val)
            lbl = self._vars.get(f"_clbl_{key}")
            if lbl: lbl.config(text=hex_val)

    def _apply_preset(self, name):
        preset = self.PRESETS[name]
        for k, v in preset.items():
            self.settings[k] = v
            if k in self._swatches: self._swatches[k].config(bg=v)
            lbl = self._vars.get(f"_clbl_{k}")
            if lbl: lbl.config(text=v)

    def _reset(self):
        for k, v in DEFAULT_SETTINGS.items():
            self.settings[k] = v
            if k in self._swatches: self._swatches[k].config(bg=str(v))
            lbl = self._vars.get(f"_clbl_{k}")
            if lbl and isinstance(lbl, tk.Label): lbl.config(text=str(v))
        for key in ("font_family", "font_size", "poll_interval",
                    "sound_alerts", "auto_open_url", "auto_start", "region"):
            if key in self._vars: self._vars[key].set(DEFAULT_SETTINGS[key])

    def _apply(self):
        self.settings["font_family"]   = self._vars["font_family"].get()
        self.settings["font_size"]     = int(self._vars["font_size"].get())
        self.settings["poll_interval"] = int(self._vars["poll_interval"].get())
        self.settings["sound_alerts"]  = self._vars["sound_alerts"].get()
        self.settings["auto_open_url"] = self._vars["auto_open_url"].get()
        self.settings["auto_start"]    = self._vars["auto_start"].get()
        self.settings["region"]        = self._vars["region"].get()
        save_settings(self.settings)
        self.on_apply(self.settings)

    def _export(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".json", filetypes=[("JSON", "*.json")],
            initialfile="unifi_watchlist_export.json", parent=self)
        if path:
            n = export_watchlist(path)
            messagebox.showinfo("Exported", f"Exported {n} item(s) to:\n{path}", parent=self)

    def _import(self):
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")], parent=self)
        if path:
            try:
                n = import_watchlist(path)
                messagebox.showinfo("Imported", f"Added {n} new item(s).", parent=self)
            except Exception as ex:
                messagebox.showerror("Import Error", str(ex), parent=self)


# ── Main app ──────────────────────────────────────────────────────────────────

class UnifiWatcherApp(tk.Tk):

    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.C        = build_palette(self.settings)

        self.title("Unifi Stock Watcher")
        self.geometry("660x820")
        self.minsize(520, 580)
        self.configure(bg=self.C["bg"])

        self.watched        = load_watched()
        self.rows           = {}
        self.notified       = {}
        self.watching       = False
        self.watcher_thread = None
        self._log_open      = True
        self._changes_open  = True
        self._force_flag    = False
        self._prev_status   = {}   # slug -> (in_stock, title, price) for full-store diff

        self._apply_ttk_styles()
        self._build()
        self._refresh_list()

        if not REQUESTS_OK:
            self._log("requests library not installed — run: pip install requests", "warn")

        # Auto-start if enabled
        if self.settings.get("auto_start") and self.watched:
            self.after(500, self._toggle_watch)

        # Keyboard shortcuts
        self.bind("<F5>", lambda _: self._force_check())
        self.bind("<Control-n>", lambda _: self._open_browse())

    def _apply_ttk_styles(self):
        C = self.C
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("Vertical.TScrollbar",
                    background=C["panel"], troughcolor=C["bg"],
                    bordercolor=C["bg"], arrowcolor=C["muted"], relief="flat")
        s.configure("TProgressbar",
                    background=C["accent"], troughcolor=C["panel"],
                    bordercolor=C["panel"])
        s.configure("TNotebook", background=C["bg"], borderwidth=0,
                    tabmargins=[0, 0, 0, 0])
        s.configure("TNotebook.Tab",
                    background=C["panel"], foreground=C["muted"],
                    padding=[18, 9],
                    font=(self.settings["font_family"],
                          self.settings["font_size"], "normal"))
        s.map("TNotebook.Tab",
              background=[("selected", C["bg"]), ("active", C["hover"])],
              foreground=[("selected", C["text"]), ("active", C["text"])])
        s.configure("TCombobox",
                    fieldbackground=C["panel"], background=C["panel"],
                    foreground=C["text"], selectbackground=C["accent"],
                    bordercolor=C["border"], arrowcolor=C["muted"])

    def _build(self):
        C        = self.C
        settings = self.settings

        # Top bar
        topbar = tk.Frame(self, bg=C["panel"])
        topbar.pack(fill="x")
        logo = tk.Frame(topbar, bg=C["panel"], pady=14, padx=20)
        logo.pack(side="left")
        tk.Label(logo, text="◈", bg=C["panel"], fg=C["accent"],
                 font=(settings["font_family"], settings["font_size"] + 8)
                 ).pack(side="left", padx=(0, 8))
        tk.Label(logo, text="Unifi Stock Watcher",
                 bg=C["panel"], fg=C["text"],
                 font=(settings["font_family"], settings["font_size"] + 4, "bold")
                 ).pack(side="left")
        tk.Label(logo, text="v2.0", bg=C["panel"], fg=C["muted"],
                 font=(settings["font_family"], settings["font_size"] - 2)
                 ).pack(side="left", padx=(8, 0))

        region = settings.get("region", "us")
        region_label = STORE_REGIONS.get(region, {}).get("label", region.upper())
        tk.Label(topbar, text=f"Region: {region_label}", bg=C["panel"], fg=C["muted"],
                 font=(settings["font_family"], settings["font_size"] - 1)
                 ).pack(side="right", padx=20)

        tk.Frame(self, bg=C["border"], height=1).pack(fill="x")

        # Tabs
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)

        self.watcher_tab = tk.Frame(self.nb, bg=C["bg"])
        self.nb.add(self.watcher_tab, text="  ◉  Watcher  ")

        self.history_tab_frame = tk.Frame(self.nb, bg=C["bg"])
        self.nb.add(self.history_tab_frame, text="  📊  History  ")

        self.settings_tab_frame = tk.Frame(self.nb, bg=C["bg"])
        self.nb.add(self.settings_tab_frame, text="  ⚙  Settings  ")

        self._build_watcher_tab()
        self._build_history_tab()
        self._build_settings_tab()

        self.nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    def _on_tab_changed(self, event):
        tab_id = self.nb.index(self.nb.select())
        if tab_id == 1 and hasattr(self, '_history_widget'):
            self._history_widget.refresh()

    def _build_watcher_tab(self):
        C        = self.C
        settings = self.settings
        p        = self.watcher_tab
        F = lambda d=0, b=False: (settings["font_family"],
                                   settings["font_size"] + d,
                                   "bold" if b else "normal")

        # Action bar
        bar = tk.Frame(p, bg=C["bg"], pady=10, padx=16)
        bar.pack(fill="x")
        self.start_btn = tk.Button(
            bar, text="▶  Start Watching", command=self._toggle_watch,
            bg=C["accent"], fg=C["white"],
            activebackground=C["accent_h"], activeforeground=C["white"],
            relief="flat", bd=0, cursor="hand2", font=F(0, True), padx=14, pady=7)
        self.start_btn.pack(side="left", padx=(0, 8))
        tooltip(self.start_btn, "Start/stop polling  (F5 = force check)")

        add_btn = tk.Button(bar, text="+ Add Items", command=self._open_browse,
                  bg=C["panel"], fg=C["text"],
                  activebackground=C["hover"], activeforeground=C["text"],
                  relief="flat", bd=0, cursor="hand2", font=F(), padx=14, pady=7)
        add_btn.pack(side="left")
        tooltip(add_btn, "Browse products  (Ctrl+N)")

        self.force_btn = tk.Button(
            bar, text="⟳ Check Now", command=self._force_check,
            bg=C["panel"], fg=C["text"],
            activebackground=C["hover"], activeforeground=C["text"],
            relief="flat", bd=0, cursor="hand2", font=F(-1), padx=10, pady=5)
        self.force_btn.pack(side="left", padx=(8, 0))
        tooltip(self.force_btn, "Force immediate check  (F5)")

        # Status bar
        sbar = tk.Frame(p, bg=C["bg"], pady=6, padx=16)
        sbar.pack(fill="x")
        self.status_dot = tk.Label(sbar, text="●", bg=C["bg"], fg=C["muted"], font=F(2))
        self.status_dot.pack(side="left", padx=(0, 6))
        self.status_lbl = tk.Label(sbar, text="Idle", bg=C["bg"], fg=C["muted"], font=F(-1))
        self.status_lbl.pack(side="left")
        self.countdown_lbl = tk.Label(sbar, text="", bg=C["bg"], fg=C["muted"], font=F(-1))
        self.countdown_lbl.pack(side="right")

        tk.Frame(p, bg=C["border"], height=1).pack(fill="x")

        # Scrollable watched list
        list_outer = tk.Frame(p, bg=C["bg"])
        list_outer.pack(fill="both", expand=True, padx=10, pady=8)
        self.list_canvas = tk.Canvas(list_outer, bg=C["bg"], highlightthickness=0, bd=0)
        list_sb = tk.Scrollbar(list_outer, orient="vertical",
                               command=self.list_canvas.yview,
                               bg=C["panel"], troughcolor=C["bg"], relief="flat", width=10)
        self.list_canvas.configure(yscrollcommand=list_sb.set)
        list_sb.pack(side="right", fill="y")
        self.list_canvas.pack(side="left", fill="both", expand=True)
        self.list_frame = tk.Frame(self.list_canvas, bg=C["bg"])
        self._lcw = self.list_canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
        self.list_frame.bind("<Configure>",
            lambda e: self.list_canvas.configure(scrollregion=self.list_canvas.bbox("all")))
        self.list_canvas.bind("<Configure>",
            lambda e: self.list_canvas.itemconfig(self._lcw, width=e.width))
        self.list_canvas.bind_all("<MouseWheel>",
            lambda e: self.list_canvas.yview_scroll(-1*(e.delta//120), "units"))

        tk.Frame(p, bg=C["border"], height=1).pack(fill="x")

        # ── Collapsible Stock Changes feed ────────────────────────────
        self._changes_open = True
        changes_section = tk.Frame(p, bg=C["bg"])
        changes_section.pack(fill="x")

        changes_hdr = tk.Frame(changes_section, bg=C["bg"], pady=7, padx=16)
        changes_hdr.pack(fill="x")
        self._changes_arrow = tk.Label(
            changes_hdr, text="▾  STOCK CHANGES",
            bg=C["bg"], fg=C["muted"], font=F(-2, True), cursor="hand2")
        self._changes_arrow.pack(side="left")
        self._changes_arrow.bind("<Button-1>", lambda _: self._toggle_changes())
        changes_hdr.bind("<Button-1>", lambda _: self._toggle_changes())

        self._changes_count_lbl = tk.Label(
            changes_hdr, text="", bg=C["bg"], fg=C["muted"], font=F(-2))
        self._changes_count_lbl.pack(side="left", padx=(8, 0))

        tk.Button(changes_hdr, text="Clear", command=self._clear_changes,
                  bg=C["panel"], fg=C["muted"], relief="flat", bd=0,
                  cursor="hand2", font=F(-2), padx=10, pady=3,
                  activebackground=C["hover"]).pack(side="right")

        self.changes_body = tk.Frame(changes_section, bg=C["bg"])
        self.changes_body.pack(fill="x", padx=10, pady=(0, 8))
        changes_sb = tk.Scrollbar(self.changes_body, bg=C["panel"],
                                  troughcolor=C["bg"], relief="flat", width=8)
        changes_sb.pack(side="right", fill="y")
        self.changes_text = tk.Text(
            self.changes_body, height=5, bg=C["panel"], fg=C["muted"],
            font=("Consolas", max(8, settings["font_size"] - 2)),
            relief="flat", bd=0, state="disabled", wrap="word",
            yscrollcommand=changes_sb.set, highlightthickness=0)
        self.changes_text.pack(side="left", fill="x", expand=True)
        changes_sb.config(command=self.changes_text.yview)
        self.changes_text.tag_config("in_stock",  foreground=C["green"])
        self.changes_text.tag_config("oos",       foreground=C["red"])
        self.changes_text.tag_config("time",      foreground="#444d56")
        self.changes_text.tag_config("arrow_up",  foreground=C["green"])
        self.changes_text.tag_config("arrow_down", foreground=C["red"])
        self.changes_text.tag_config("price",     foreground=C["gold"])
        self.changes_text.tag_config("name",      foreground=C["text"])
        self._change_count = 0

        tk.Frame(p, bg=C["border"], height=1).pack(fill="x")

        # Collapsible log
        self._log_open = True
        log_section = tk.Frame(p, bg=C["bg"])
        log_section.pack(fill="x")
        log_hdr = tk.Frame(log_section, bg=C["bg"], pady=7, padx=16)
        log_hdr.pack(fill="x")
        self._log_arrow = tk.Label(log_hdr, text="▾  ACTIVITY LOG",
                                   bg=C["bg"], fg=C["muted"],
                                   font=F(-2, True), cursor="hand2")
        self._log_arrow.pack(side="left")
        self._log_arrow.bind("<Button-1>", lambda _: self._toggle_log())
        log_hdr.bind("<Button-1>", lambda _: self._toggle_log())
        tk.Button(log_hdr, text="Clear", command=self._clear_log,
                  bg=C["panel"], fg=C["muted"], relief="flat", bd=0,
                  cursor="hand2", font=F(-2), padx=10, pady=3,
                  activebackground=C["hover"]).pack(side="right")

        self.log_body = tk.Frame(log_section, bg=C["bg"])
        self.log_body.pack(fill="x", padx=10, pady=(0, 8))
        log_sb2 = tk.Scrollbar(self.log_body, bg=C["panel"],
                               troughcolor=C["bg"], relief="flat", width=8)
        log_sb2.pack(side="right", fill="y")
        self.log_text = tk.Text(
            self.log_body, height=6, bg=C["panel"], fg=C["muted"],
            font=("Consolas", max(8, settings["font_size"] - 2)),
            relief="flat", bd=0, state="disabled", wrap="word",
            yscrollcommand=log_sb2.set, highlightthickness=0)
        self.log_text.pack(side="left", fill="x", expand=True)
        log_sb2.config(command=self.log_text.yview)
        self.log_text.tag_config("ok",   foreground=C["green"])
        self.log_text.tag_config("warn", foreground=C["yellow"])
        self.log_text.tag_config("err",  foreground=C["red"])
        self.log_text.tag_config("fav",  foreground=C["gold"])
        self.log_text.tag_config("info", foreground=C["muted"])
        self.log_text.tag_config("time", foreground="#444d56")

        # Bottom toolbar
        tk.Frame(p, bg=C["border"], height=1).pack(fill="x")
        bottom = tk.Frame(p, bg=C["panel"], pady=7, padx=16)
        bottom.pack(fill="x")
        tk.Button(bottom, text="Test Notification", command=self._test_notif,
                  bg=C["panel"], fg=C["muted"], relief="flat", bd=0,
                  cursor="hand2", font=F(-1), padx=10, pady=3,
                  activebackground=C["hover"]).pack(side="left", padx=(0, 8))
        tk.Button(bottom, text="Open Store", command=self._open_store,
                  bg=C["panel"], fg=C["muted"], relief="flat", bd=0,
                  cursor="hand2", font=F(-1), padx=10, pady=3,
                  activebackground=C["hover"]).pack(side="left")
        interval = self.settings.get("poll_interval", 60)
        tk.Label(bottom, text=f"Interval: {interval}s",
                 bg=C["panel"], fg=C["muted"], font=F(-2)).pack(side="right")

    def _build_history_tab(self):
        for w in self.history_tab_frame.winfo_children(): w.destroy()
        self._history_widget = HistoryTab(self.history_tab_frame, self.C, self.settings)
        self._history_widget.pack(fill="both", expand=True)

    def _build_settings_tab(self):
        for w in self.settings_tab_frame.winfo_children(): w.destroy()
        st = SettingsTab(self.settings_tab_frame, self.settings, self._on_settings_apply)
        st.pack(fill="both", expand=True)

    def _on_settings_apply(self, new_settings):
        self.settings = new_settings
        self.C = build_palette(new_settings)
        self.configure(bg=self.C["bg"])
        for widget in self.winfo_children(): widget.destroy()
        self._apply_ttk_styles()
        self._build()
        self.watched = load_watched()
        self._refresh_list()
        self._log("Settings applied.", "ok")

    # ── List rendering ────────────────────────────────────────────────

    def _refresh_list(self):
        for w in self.list_frame.winfo_children(): w.destroy()
        self.rows = {}
        C = self.C

        favourites = [i for i in self.watched if i.get("favourite")]
        others     = [i for i in self.watched if not i.get("favourite")]

        if not self.watched:
            tk.Label(self.list_frame,
                     text='No items watched yet.\nClick "+ Add Items" to browse products.\n\nCtrl+N = browse   F5 = force check',
                     bg=C["bg"], fg=C["muted"],
                     font=(self.settings["font_family"], self.settings["font_size"]),
                     justify="center", pady=40).pack(fill="x")
            self._update_title()
            return

        if favourites:
            SectionHeader(self.list_frame, f"★  FAVOURITES ({len(favourites)})",
                          C, self.settings).pack(fill="x")
            for item in favourites:
                row = WatchedRow(self.list_frame, item,
                                 self._remove_item, self._toggle_fav,
                                 self._quick_check_item, C, self.settings)
                row.pack(fill="x", pady=(0, 4))
                self.rows[item["slug"]] = row

        if others:
            SectionHeader(self.list_frame, f"MONITORING ({len(others)})",
                          C, self.settings).pack(fill="x", pady=(12 if favourites else 0, 0))
            for item in others:
                row = WatchedRow(self.list_frame, item,
                                 self._remove_item, self._toggle_fav,
                                 self._quick_check_item, C, self.settings)
                row.pack(fill="x", pady=(0, 4))
                self.rows[item["slug"]] = row

        self.notified = {w["slug"]: self.notified.get(w["slug"], False) for w in self.watched}
        self._update_title()

    def _update_title(self):
        n_fav   = sum(1 for i in self.watched if i.get("favourite"))
        n_total = len(self.watched)
        self.title(f"Unifi Stock Watcher — {n_total} item(s), {n_fav} ★"
                   if n_total else "Unifi Stock Watcher")

    # ── Item actions ──────────────────────────────────────────────────

    def _remove_item(self, item):
        self.watched = [w for w in self.watched if w["slug"] != item["slug"]]
        save_watched(self.watched)
        self._refresh_list()
        self._log(f"Removed: {item['title']}", "warn")

    def _toggle_fav(self, item):
        prev_statuses = {s: r.in_stock for s, r in self.rows.items()}
        prev_subs     = {s: r.sub.cget("text") for s, r in self.rows.items()}
        is_fav = False
        for w in self.watched:
            if w["slug"] == item["slug"]:
                w["favourite"] = not w.get("favourite", False)
                is_fav = w["favourite"]
                break
        save_watched(self.watched)
        self._refresh_list()
        for slug, in_stock in prev_statuses.items():
            row = self.rows.get(slug)
            if row and in_stock is not None:
                checked = prev_subs.get(slug, "").replace("Checked ", "")
                row.update_status(in_stock, checked or None)
        self._log(f"{'★' if is_fav else '☆'} {item['title']} "
                  f"{'starred' if is_fav else 'unstarred'}",
                  "fav" if is_fav else "info")

    def _quick_check_item(self, item):
        self._log(f"Quick-checking {item['title']}…", "info")
        def run():
            try:
                region   = self.settings.get("region", "us")
                build_id = get_build_id(region)
                in_stock, price = check_slug(build_id, item["slug"], region)
                checked  = datetime.now().strftime("%H:%M:%S")
                stock_history.record_check(item["slug"], item["title"], in_stock, price)
                self.after(0, self._update_row, item["slug"], in_stock, checked, price)
                # Check for transition
                prev = self._prev_status.get(item["slug"])
                if prev is not None and prev[0] != in_stock:
                    self.after(0, self._add_change, item["title"], in_stock, price)
                self._prev_status[item["slug"]] = (in_stock, item["title"], price)
                if in_stock and not self.notified.get(item["slug"]):
                    self.after(0, self._on_in_stock, item)
                elif not in_stock:
                    self.notified[item["slug"]] = False
            except Exception as e:
                self.after(0, self._log, f"Quick-check failed: {e}", "err")
        threading.Thread(target=run, daemon=True).start()

    def _open_browse(self):
        BrowseDialog(self, self.watched, self._add_items, self.C, self.settings)

    def _add_items(self, picks):
        existing = {w["slug"] for w in self.watched}
        added = sum(1 for p in picks if p["slug"] not in existing)
        for p in picks:
            if p["slug"] not in existing:
                self.watched.append(p)
        save_watched(self.watched)
        self._refresh_list()
        self._log(f"Added {added} item(s).", "ok")

    # ── Watcher loop ──────────────────────────────────────────────────

    def _toggle_watch(self):
        if self.watching:
            self.watching = False
            self.start_btn.config(text="▶  Start Watching", bg=self.C["accent"])
            self.countdown_lbl.config(text="")
            self._set_status("Idle", self.C["muted"])
            self._log("Watcher stopped.", "warn")
        else:
            if not self.watched:
                messagebox.showinfo("No items", 'Add items first using "+ Add Items".')
                return
            self.watching = True
            self.start_btn.config(text="⏹  Stop Watching", bg=self.C["red"])
            self._set_status("Watching…", self.C["green"])
            self._log(f"Started — watching {len(self.watched)} item(s).", "ok")
            self.watcher_thread = threading.Thread(target=self._watch_loop, daemon=True)
            self.watcher_thread.start()

    def _force_check(self):
        if self.watching:
            self._force_flag = True
            self._log("Forcing immediate check…", "info")

    def _watch_loop(self):
        self._force_flag = False
        while self.watching:
            self.after(0, self._set_status, "Checking…", self.C["yellow"])
            self.after(0, self._log, "Fetching full store catalog…", "info")
            region = self.settings.get("region", "us")
            try:
                build_id = get_build_id(region)

                # ── Full catalog fetch ────────────────────────────────
                all_products = fetch_all_products(build_id, region)
                checked = datetime.now().strftime("%H:%M:%S")

                # Build current status map: slug -> (in_stock, title, price)
                current_status = {}
                for p in all_products:
                    slug = p.get("slug")
                    if not slug:
                        continue
                    avail = is_available(p)
                    price = get_price(p)
                    title = p.get("title", slug)
                    current_status[slug] = (avail, title, price)

                # ── Diff against previous state for stock changes feed ─
                if self._prev_status:
                    changes = 0
                    for slug, (now_avail, title, price) in current_status.items():
                        prev = self._prev_status.get(slug)
                        if prev is not None:
                            was_avail = prev[0]
                            if was_avail != now_avail:
                                changes += 1
                                self.after(0, self._add_change,
                                           title, now_avail, price)
                    if changes:
                        self.after(0, self._log,
                                   f"Detected {changes} stock change(s) across store.",
                                   "warn")
                    else:
                        self.after(0, self._log,
                                   f"No stock changes across {len(current_status)} products.",
                                   "info")
                else:
                    self.after(0, self._log,
                               f"Baseline set: {len(current_status)} products cataloged.",
                               "info")

                # Save current as previous for next cycle
                self._prev_status = current_status

                # ── Update watched item rows + notifications ──────────
                watched_slugs = {w["slug"] for w in self.watched}
                for item in list(self.watched):
                    if not self.watching:
                        break
                    slug, title = item["slug"], item["title"]
                    status_entry = current_status.get(slug)
                    if status_entry:
                        in_stock, _, price = status_entry
                    else:
                        # Item not in catalog (removed from store?) — try direct
                        try:
                            in_stock, price = check_slug(
                                build_id, slug, region,
                                retries=self.settings.get("max_retries", 3))
                        except Exception as e:
                            self.after(0, self._log,
                                       f"Error checking {title}: {e}", "err")
                            continue

                    stock_history.record_check(slug, title, in_stock, price)
                    self.after(0, self._update_row, slug, in_stock, checked, price)

                    if in_stock and not self.notified.get(slug):
                        self.after(0, self._on_in_stock, item)
                    elif not in_stock:
                        self.notified[slug] = False

            except Exception as e:
                self.after(0, self._log, f"Store error: {e}", "err")

            if self.watching:
                self.after(0, self._set_status, "Watching…", self.C["green"])
                interval = self.settings.get("poll_interval", 60)
                self._force_flag = False
                for i in range(interval, 0, -1):
                    if not self.watching or self._force_flag: break
                    self.after(0, self.countdown_lbl.config, {"text": f"Next check in {i}s"})
                    time.sleep(1)
                self.after(0, self.countdown_lbl.config, {"text": ""})

    def _update_row(self, slug, in_stock, checked_at, price=None):
        row = self.rows.get(slug)
        if row: row.update_status(in_stock, checked_at, price)
        title = next((w["title"] for w in self.watched if w["slug"] == slug), slug)
        tag = "ok" if in_stock else "info"
        price_text = f" ({price})" if price else ""
        self._log(f"{title}: {'IN STOCK' if in_stock else 'out of stock'}{price_text}", tag)

    def _on_in_stock(self, item):
        self.notified[item["slug"]] = True
        is_fav = item.get("favourite", False)
        region = self.settings.get("region", "us")
        self._log(f"{'★ ' if is_fav else ''}IN STOCK: {item['title']}", "ok")
        notify_windows(
            f"{'★ ' if is_fav else ''}IN STOCK: {item['title']}",
            f"{item['title']} is now available on the Unifi store!")
        if self.settings.get("sound_alerts", True):
            play_sound()
        if self.settings.get("auto_open_url", True):
            webbrowser.open(f"{store_home(region)}/products/{item['slug']}")

    # ── Stock changes helpers ─────────────────────────────────────────

    def _add_change(self, title, in_stock, price=None):
        """Add a stock transition event to the changes feed."""
        ts = datetime.now().strftime("%H:%M:%S")
        self._change_count += 1
        self.changes_text.configure(state="normal")
        self.changes_text.insert("end", f"[{ts}] ", "time")
        if in_stock:
            self.changes_text.insert("end", "▲ ", "arrow_up")
            self.changes_text.insert("end", "NOW IN STOCK", "in_stock")
        else:
            self.changes_text.insert("end", "▼ ", "arrow_down")
            self.changes_text.insert("end", "WENT OUT OF STOCK", "oos")
        self.changes_text.insert("end", "  ", "time")
        self.changes_text.insert("end", title, "name")
        if price:
            self.changes_text.insert("end", f"  {price}", "price")
        self.changes_text.insert("end", "\n")
        self.changes_text.see("end")
        self.changes_text.configure(state="disabled")
        self._changes_count_lbl.config(text=f"({self._change_count})")

    def _toggle_changes(self):
        if self._changes_open:
            self.changes_body.pack_forget()
            self._changes_arrow.config(text="▸  STOCK CHANGES")
            self._changes_open = False
        else:
            self.changes_body.pack(fill="x", padx=10, pady=(0, 8))
            self._changes_arrow.config(text="▾  STOCK CHANGES")
            self._changes_open = True

    def _clear_changes(self):
        self.changes_text.configure(state="normal")
        self.changes_text.delete("1.0", "end")
        self.changes_text.configure(state="disabled")
        self._change_count = 0
        self._changes_count_lbl.config(text="")

    # ── Log helpers ───────────────────────────────────────────────────

    def _toggle_log(self):
        if self._log_open:
            self.log_body.pack_forget()
            self._log_arrow.config(text="▸  ACTIVITY LOG")
            self._log_open = False
        else:
            self.log_body.pack(fill="x", padx=10, pady=(0, 8))
            self._log_arrow.config(text="▾  ACTIVITY LOG")
            self._log_open = True

    def _log(self, msg, tag="info"):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{ts}] ", "time")
        self.log_text.insert("end", msg + "\n", tag)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _set_status(self, text, color):
        self.status_lbl.config(text=text, fg=color)
        self.status_dot.config(fg=color)

    def _test_notif(self):
        self._log("Firing test notification…", "info")
        notify_windows("TEST: Unifi Stock Watcher",
                       "This is what an in-stock alert looks like.")
        if self.settings.get("sound_alerts", True):
            play_sound()
        self._log("Sent — check bottom-right corner.", "ok")

    def _open_store(self):
        region = self.settings.get("region", "us")
        webbrowser.open(store_home(region))


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not REQUESTS_OK:
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "requests", "--quiet"])
            import requests
        except Exception:
            pass
    app = UnifiWatcherApp()
    app.mainloop()
