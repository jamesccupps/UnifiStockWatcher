"""
UniFi Stock Watcher - the desktop app.

A watch list of store products, checked against the whole UniFi catalog on a
timer, with a tray icon, notifications, a history of stock changes, and signed
in-app updates. The store logic lives in unifi_core; this is the window
around it. Tabs: Watch list | History | Settings.

    unifi_watcher_gui.py [--minimized] [--watch]
      --minimized   start in the tray (Start with Windows passes this)
      --watch       start watching straight away
"""

import csv
import logging
import queue
import sys
import threading
import time
import tkinter as tk
import webbrowser
from collections import deque
from datetime import datetime
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk

import winplatform

winplatform.enable_dpi_awareness()          # before the first window exists

# ── Dependency bootstrap (must precede the unifi_core import) ─────────────────
from _bootstrap import ensure_requests, missing_message  # noqa: E402

_HAVE_REQUESTS = ensure_requests(auto_install=not getattr(sys, "frozen", False))

from unifi_core import (  # noqa: E402
    __version__, APP_NAME, APP_ID, REPO_URL, BASE_DIR, LOG_FILE,
    STORE_REGIONS, CATEGORY_LABELS,
    ProductNotFound, StoreError,
    load_settings, save_settings, product_url, store_home,
    get_build_id, invalidate_build_id, fetch_all_products,
    refresh_category_coverage, effective_categories,
    is_available, get_price, check_slug,
    describe_restock, describe_sold_out_for, is_coming_soon,
    load_watched, save_watched, stock_history,
    notify_windows, play_sound, migrate_legacy_files, setup_logging,
    export_watchlist, import_watchlist,
)
import gfx  # noqa: E402
import theme as theming  # noqa: E402
import tray as traymod  # noqa: E402
import updater  # noqa: E402

log = logging.getLogger("unifi_watcher.gui")

MAX_LOG_LINES = 500
MAX_CHANGE_LINES = 1000
UPDATE_RECHECK_MS = 24 * 3600 * 1000
ISSUE_URL = f"{REPO_URL}/issues/new?template=bug_report.yml&version={__version__}"
SITE_URL = "https://jamesccupps.github.io/UnifiStockWatcher/"

INTERVALS = [(15, "15 seconds"), (30, "30 seconds"), (60, "1 minute"),
             (120, "2 minutes"), (300, "5 minutes"), (600, "10 minutes"),
             (1800, "30 minutes"), (3600, "1 hour")]
THEME_NAMES = [("system", "Use Windows setting"), ("light", "Light"), ("dark", "Dark")]


def resource(name):
    """A file from assets/, from source or from inside the frozen build."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "assets" / name


def restock_note(product):
    """One-line restock summary for an out-of-stock product, or None.

    e.g. "back ~7 Sep  ·  sold out 13 days". The store sends restockEtaAt and
    soldOutAt on every catalog fetch; a little over half of out-of-stock
    products carry at least one of them.
    """
    if is_available(product):
        return None
    if is_coming_soon(product):
        # Never been on sale, so "back ~" and "sold out for" would both lie.
        eta = describe_restock(product)
        return f"coming soon  ·  {eta}" if eta else "coming soon"
    bits = [b for b in (describe_restock(product),
                        describe_sold_out_for(product)) if b]
    return "  ·  ".join(bits) if bits else None


def interval_label(seconds):
    for s, label in INTERVALS:
        if s == seconds:
            return label
    return f"{seconds} seconds"


def friendly_time(ts, now=None):
    """'Today 16:50', 'Yesterday 09:12', '28 Oct 16:50', '28 Oct 2025'."""
    try:
        dt = datetime.fromisoformat(str(ts))
    except ValueError:
        return str(ts)
    now = now or datetime.now()
    days = (now.date() - dt.date()).days
    hm = dt.strftime("%H:%M")
    if days == 0:
        return f"Today  {hm}"
    if days == 1:
        return f"Yesterday  {hm}"
    if dt.year == now.year:
        return f"{dt.day} {dt.strftime('%b')}  {hm}"
    return f"{dt.day} {dt.strftime('%b %Y')}"


def plural(n, word):
    return f"{n:,} {word}" + ("" if n == 1 else "s")


def category_label(cat):
    return CATEGORY_LABELS.get(cat) or cat.split("/")[-1].replace("-", " ").title()


def _trim_text(widget, max_lines):
    """Drop the oldest lines from a Text widget once it exceeds max_lines.

    The activity log appends one line per watched item per cycle; untrimmed,
    a watcher left running overnight accumulated tens of thousands of lines
    and the widget grew steadily slower to redraw.
    """
    lines = int(widget.index("end-1c").split(".")[0])
    if lines > max_lines:
        widget.delete("1.0", f"{lines - max_lines + 1}.0")


# ── Mouse wheel ───────────────────────────────────────────────────────────────

def _wheel_target(widget):
    """The nearest enclosing canvas registered as wheel-scrollable."""
    while widget is not None:
        if getattr(widget, "_wheel_scrolls", False):
            return widget
        widget = widget.master
    return None


def _dispatch_wheel(event):
    """Scroll whichever registered canvas is under the pointer."""
    try:
        under = event.widget.winfo_containing(event.x_root, event.y_root)
    except (tk.TclError, KeyError):
        return None
    target = _wheel_target(under)
    if target is None:
        return None
    try:
        target.yview_scroll(-1 * (event.delta // 120), "units")
    except tk.TclError:
        return None
    return "break"


def bind_wheel(canvas, scrollable=None):
    """Let the wheel scroll `canvas` whenever the pointer is anywhere over it.

    Two approaches that do not work, both of which shipped in 2.x:

    bind_all is application-wide, so the browse dialog's binding replaced the
    main list's and then outlived its own canvas - every later wheel event
    raised "invalid command name" and the watch list stopped scrolling.

    Enabling on <Enter> and tearing down on <Leave> is worse: moving the
    pointer onto a child row fires <Leave> on the container, so the binding
    was removed exactly where the user actually scrolls.

    Bind on the toplevel instead - a toplevel is in the bindtags of every
    widget inside it, so this catches wheel events over any descendant while
    staying scoped to that window. The handler resolves the target from the
    pointer position rather than closing over one canvas, so a destroyed
    canvas is simply never found again.
    """
    canvas._wheel_scrolls = True
    top = canvas.winfo_toplevel()
    if not getattr(top, "_wheel_bound", False):
        top._wheel_bound = True
        top.bind("<MouseWheel>", _dispatch_wheel, add="+")


# ── Small widgets ─────────────────────────────────────────────────────────────

class Tooltip:
    """A themed tooltip, shown after the pointer rests on a widget."""

    def __init__(self, widget, text, theme):
        self.widget, self.text, self.T = widget, text, theme
        self.tip = self.job = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _):
        self._hide()
        self.job = self.widget.after(550, self._show)

    def _show(self):
        self.job = None
        try:
            x = self.widget.winfo_rootx() + self.T.S(4)
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + self.T.S(6)
        except tk.TclError:
            return
        c = self.T.c
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=self.text, bg=c["surface"], fg=c["text"],
                 font=self.T.font(-1), padx=self.T.S(8), pady=self.T.S(4),
                 highlightthickness=1, highlightbackground=c["border"],
                 justify="left").pack()

    def _hide(self, _=None):
        if self.job:
            self.widget.after_cancel(self.job)
            self.job = None
        if self.tip:
            try:
                self.tip.destroy()
            except tk.TclError:
                pass
            self.tip = None


def placeholder(entry, var, text, theme):
    """Grey hint text in an empty entry, gone as soon as there's text."""
    lbl = ttk.Label(entry, text=text, style="Placeholder.TLabel", cursor="xterm")
    lbl.bind("<Button-1>", lambda _: entry.focus_set())

    def update(*_):
        if var.get():
            lbl.place_forget()
        else:
            lbl.place(x=theme.S(11), rely=0.5, anchor="w")
    var.trace_add("write", update)
    update()
    return lbl


class ScrollFrame(ttk.Frame):
    """A vertically scrolling area; `inner` is where the content goes. The
    scroll bar only shows when there is something to scroll."""

    def __init__(self, parent, theme, surface="bg", **kw):
        super().__init__(parent, style="Card.TFrame" if surface == "surface" else "TFrame", **kw)
        bg = theme.c["surface" if surface == "surface" else "bg"]
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.sb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview,
                                style=("Card." if surface == "surface" else "") + "Vertical.TScrollbar")
        self.canvas.configure(yscrollcommand=self.sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = ttk.Frame(self.canvas, style="Card.TFrame" if surface == "surface" else "TFrame")
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)
        bind_wheel(self.canvas, self.inner)
        self._sb_shown = False

    def _on_inner(self, _=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._autohide()

    def _on_canvas(self, e):
        self.canvas.itemconfig(self._win, width=e.width)
        self._autohide()

    def _autohide(self):
        need = self.inner.winfo_reqheight() > self.canvas.winfo_height() + 1
        if need and not self._sb_shown:
            self.sb.pack(side="right", fill="y", before=self.canvas)
            self._sb_shown = True
        elif not need and self._sb_shown:
            self.sb.pack_forget()
            self.canvas.yview_moveto(0)
            self._sb_shown = False


# ── Browse dialog ─────────────────────────────────────────────────────────────

class BrowseDialog(tk.Toplevel):
    """Pick products from the live catalog to add to the watch list.

    A Treeview rather than a widget per product: 2.x built ~490 rows of
    Frames, Checkbuttons and Labels, and even with pooling a filter pass cost
    ~100 ms. The first column's image is the row's check box.
    """

    COLUMNS = (("product", "Product", 330), ("category", "Category", 170),
               ("price", "Price", 90), ("status", "Status", 200))

    def __init__(self, app, already_watching, on_add):
        super().__init__(app)
        self.app, self.T = app, app.theme
        self.on_add           = on_add
        self.already_watching = {w["slug"] for w in already_watching}
        self.all_prods        = []
        self.filtered         = []
        self.selected         = set()     # slugs ticked, kept across filters
        self._filter_job      = None      # pending debounced filter
        self._closed          = False     # guards callbacks from the fetch thread
        self._sort            = ("product", False)

        T = self.T
        self.title("Add products")
        self.geometry(f"{T.S(900)}x{T.S(640)}")
        self.minsize(T.S(620), T.S(420))
        self.transient(app)
        T.style_window(self)
        self._build()
        try:
            self.grab_set()
        except tk.TclError:
            pass                      # not viewable yet; modality is a nicety
        self.bind("<Escape>", lambda _: self.destroy())
        self._fetch()

    def _build(self):
        T, S = self.T, self.T.S
        outer = ttk.Frame(self, padding=(S(20), S(16), S(20), S(16)))
        outer.pack(fill="both", expand=True)

        hdr = ttk.Frame(outer)
        hdr.pack(fill="x")
        ttk.Label(hdr, text="Add products", style="Title.TLabel").pack(side="left")
        self.status_lbl = ttk.Label(hdr, text="Fetching…", style="Muted.TLabel")
        self.status_lbl.pack(side="right")

        self.prog_var = tk.IntVar()
        self.prog = ttk.Progressbar(outer, variable=self.prog_var, maximum=100)
        self.prog.pack(fill="x", pady=(S(10), 0))

        bar = ttk.Frame(outer)
        bar.pack(fill="x", pady=(S(12), S(10)))
        self.q = tk.StringVar()
        self.q.trace_add("write", lambda *_: self._filter_soon())
        self.search = ttk.Entry(bar, textvariable=self.q, width=34)
        self.search.pack(side="left")
        placeholder(self.search, self.q, "Search products", T)
        self._cat_var = tk.StringVar(value="All categories")
        self._cat_keys = {"All categories": None}
        for cat in effective_categories(self.app.settings.get("region", "us")):
            self._cat_keys[category_label(cat)] = cat
        cat_menu = ttk.Combobox(bar, textvariable=self._cat_var, state="readonly",
                                values=list(self._cat_keys), width=26)
        cat_menu.pack(side="left", padx=(S(8), S(12)))
        cat_menu.bind("<<ComboboxSelected>>", lambda _: self._filter())
        self._stock_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="  Include products in stock", variable=self._stock_var,
                        command=self._filter).pack(side="left")
        self.search.focus_set()

        card = ttk.Frame(outer, style="Tile.TFrame", padding=S(1))
        card.pack(fill="both", expand=True)
        inner = ttk.Frame(card, style="Card.TFrame", padding=(S(4), S(4)))
        inner.pack(fill="both", expand=True, padx=S(2), pady=S(2))
        self.tree = ttk.Treeview(inner, columns=[c[0] for c in self.COLUMNS],
                                 show="tree headings", selectmode="browse")
        self.tree.column("#0", width=S(40), minwidth=S(40), stretch=False, anchor="center")
        for key, label, width in self.COLUMNS:
            self.tree.heading(key, text=label, anchor="w",
                              command=lambda k=key: self._sort_by(k))
            self.tree.column(key, width=S(width), minwidth=S(60), anchor="w",
                             stretch=key == "product")
        c = T.c
        self.tree.tag_configure("watching", foreground=c["text3"])
        self.tree.tag_configure("instock", foreground=c["green"])
        sb = ttk.Scrollbar(inner, orient="vertical", command=self.tree.yview,
                           style="Card.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<space>", self._on_space)
        self.tree.bind("<Return>", lambda _: self._confirm())

        foot = ttk.Frame(outer)
        foot.pack(fill="x", pady=(S(14), 0))
        self.sel_lbl = ttk.Label(foot, text="Tick the products to watch.", style="Muted.TLabel")
        self.sel_lbl.pack(side="left")
        self.all_btn = ttk.Button(foot, text="Select all shown", command=self._toggle_all)
        self.all_btn.pack(side="left", padx=(S(12), 0))
        self.add_btn = ttk.Button(foot, text="Add to watch list", style="Accent.TButton",
                                  command=self._confirm, state="disabled")
        self.add_btn.pack(side="right")
        ttk.Button(foot, text="Cancel", command=self.destroy).pack(side="right", padx=(0, S(8)))

    def destroy(self):
        self._closed = True
        super().destroy()

    def _post(self, fn, *args):
        """Schedule fn on the Tk thread, unless the dialog has already closed.

        The catalog fetch runs on a worker; closing the dialog mid-fetch used
        to leave it calling after() on a destroyed widget, which raises out of
        a thread with nowhere to report it.
        """
        if not self._closed:
            self.app._post(lambda: None if self._closed else fn(*args))

    def _set_status(self, text, colour=None):
        self.status_lbl.config(text=text, foreground=colour or self.T.c["text2"])

    def _fetch(self):
        def _progress(pct):
            self.prog_var.set(pct)
            self._set_status(f"Fetching… {pct}%")

        def run():
            try:
                region = self.app.settings.get("region", "us")
                self._post(self._set_status,
                           f"Connecting to the {STORE_REGIONS[region]['label']} store…")
                bid = get_build_id(region)
                self._post(self._set_status, "Fetching products…")
                all_ = fetch_all_products(
                    bid, region,
                    progress_cb=lambda p: self._post(_progress, p))
                self.all_prods = sorted(all_, key=lambda p: p.get("title", "").lower())
                self._post(self._on_fetched)
            except Exception as ex:
                # Bind the message now. `ex` is unbound the moment this block
                # exits, so a deferred lambda closing over it raises NameError
                # inside the Tk callback and the dialog sits on "Fetching…".
                self._post(self._on_fetch_failed, f"{type(ex).__name__}: {ex}")
                log.exception("Browse fetch failed")
        threading.Thread(target=run, daemon=True,
                         name="unifi-browse-fetch").start()

    def _on_fetch_failed(self, msg):
        self._set_status(f"Couldn't reach the store: {msg}", self.T.c["red"])
        self.prog.pack_forget()

    def _on_fetched(self):
        try:
            self._filter()
            self.prog.pack_forget()
        except Exception as ex:
            log.exception("Browse populate failed")
            self._set_status(f"Error displaying products: {ex}", self.T.c["red"])
            self.prog.pack_forget()

    def _filter_soon(self, delay_ms=180):
        """Coalesce rapid typing into a single filter pass."""
        if self._filter_job is not None:
            self.after_cancel(self._filter_job)
        self._filter_job = self.after(delay_ms, self._filter)

    def _filter(self):
        self._filter_job = None
        q = self.q.get().lower().strip()
        show_all = self._stock_var.get()
        cat_key = self._cat_keys.get(self._cat_var.get())
        self.filtered = [
            p for p in self.all_prods
            if (show_all or not is_available(p))
            and (not q or q in p.get("title", "").lower())
            and (not cat_key or p.get("_category") == cat_key)]
        self._apply_sort()
        self._render()
        n_oos = sum(1 for p in self.all_prods if not is_available(p))
        self._set_status(f"{len(self.filtered)} shown  ·  {len(self.all_prods)} products, "
                         f"{n_oos} out of stock")

    def _status_text(self, p):
        if p["slug"] in self.already_watching:
            return "On your watch list"
        if is_available(p):
            return "In stock"
        note = restock_note(p)
        if not note:
            return "Sold out"
        if note.startswith(("coming soon", "sold out")):
            return note[0].upper() + note[1:]
        return f"Sold out  ·  {note}"

    def _sort_by(self, key):
        col, desc = self._sort
        self._sort = (key, not desc if col == key else False)
        self._apply_sort()
        self._render()

    def _apply_sort(self):
        key, desc = self._sort
        getters = {
            "product":  lambda p: p.get("title", "").lower(),
            "category": lambda p: category_label(p.get("_category", "")),
            "price":    lambda p: _price_value(p),
            "status":   lambda p: (is_available(p), self._status_text(p)),
        }
        self.filtered.sort(key=getters[key], reverse=desc)

    def _render(self):
        tree, T = self.tree, self.T
        tree.delete(*tree.get_children())
        for p in self.filtered:
            slug = p["slug"]
            watching = slug in self.already_watching
            if watching:
                img, tags = T.image("check_on_disabled"), ("watching",)
            else:
                img = T.image("check_on" if slug in self.selected else "check_off")
                tags = ("instock",) if is_available(p) else ()
            tree.insert("", "end", iid=slug, image=img, tags=tags, values=(
                p.get("title", slug), category_label(p.get("_category", "")),
                get_price(p) or "", self._status_text(p)))
        self._count()

    def _toggle(self, slug):
        if slug in self.already_watching or not self.tree.exists(slug):
            return
        if slug in self.selected:
            self.selected.discard(slug)
        else:
            self.selected.add(slug)
        self.tree.item(slug, image=self.T.image("check_on" if slug in self.selected else "check_off"))
        self._count()

    def _on_click(self, event):
        if self.tree.identify_region(event.x, event.y) == "heading":
            return None
        row = self.tree.identify_row(event.y)
        if row:
            self.tree.focus(row)
            self.tree.selection_set(row)
            self._toggle(row)
            return "break"
        return None

    def _on_space(self, _):
        row = self.tree.focus()
        if row:
            self._toggle(row)
        return "break"

    def _toggle_all(self):
        shown = [p["slug"] for p in self.filtered if p["slug"] not in self.already_watching]
        if shown and all(s in self.selected for s in shown):
            self.selected.difference_update(shown)
        else:
            self.selected.update(shown)
        self._render()

    def _count(self):
        n = len(self.selected)
        self.sel_lbl.config(text=f"{n} selected" if n else "Tick the products to watch.")
        self.add_btn.config(state="normal" if n else "disabled",
                            text=f"Add {n} to watch list" if n > 1 else "Add to watch list")

    def _confirm(self):
        picks = []
        for p in self.all_prods:
            if p["slug"] in self.selected and p["slug"] not in self.already_watching:
                picks.append({
                    "title":     p.get("title", p["slug"]),
                    "slug":      p["slug"],
                    "favourite": False,
                    "price":     get_price(p),
                    "added_at":  datetime.now().isoformat(timespec="seconds"),
                })
        if not picks:
            messagebox.showwarning("Nothing selected",
                                   "Tick at least one product.", parent=self)
            return
        self.on_add(picks)
        self.destroy()


def _price_value(p):
    for v in p.get("variants", []):
        price = v.get("displayPrice") or v.get("price")
        if isinstance(price, dict) and price.get("amount"):
            return price["amount"]
        if isinstance(price, (int, float)) and price:
            return price * 100
    return float("inf")


# ── Watched item row ──────────────────────────────────────────────────────────

class WatchedRow(ttk.Frame):
    """One watched product, as a card: favourite star, name and status line,
    price, a status pill, and (when it's in stock) a button to the store."""

    def __init__(self, parent, app, item):
        T = app.theme
        S = T.S
        super().__init__(parent, style="Tile.TFrame",
                         padding=(S(6), S(8), S(10), S(8)))
        self.app, self.T, self.item = app, T, item
        self.in_stock = None
        self.columnconfigure(1, weight=1)
        fav = bool(item.get("favourite"))
        self.star = ttk.Button(
            self, text=T.icon("star_fill" if fav else "star"),
            style="Card.Fav.Icon.Subtle.TButton" if fav else "Card.Star.Icon.Subtle.TButton",
            command=lambda: app._toggle_fav(self.item), takefocus=False)
        self.star.grid(row=0, column=0, rowspan=2, padx=(0, S(6)))
        Tooltip(self.star, "Remove from favourites" if fav else
                "Add to favourites (shown first)", T)
        self.title_lbl = ttk.Label(self, text=item["title"], style="Card.Strong.TLabel",
                                   cursor="hand2")
        self.title_lbl.grid(row=0, column=1, sticky="sw")
        self.sub = ttk.Label(self, text="Not checked yet", style="Card.Faint.TLabel")
        self.sub.grid(row=1, column=1, sticky="nw")
        self.price_lbl = ttk.Label(self, text=item.get("price") or "", style="Card.Muted.TLabel",
                                   width=10, anchor="e")
        self.price_lbl.grid(row=0, column=2, rowspan=2, padx=(S(10), S(12)))
        self.pill = ttk.Label(self, text="NOT CHECKED", style="Neutral.Pill.TLabel")
        self.pill.grid(row=0, column=3, rowspan=2, padx=(0, S(8)))
        self.open_btn = ttk.Button(self, text="Open in store", style="Card.Accent.TButton",
                                   command=self._open, takefocus=False)
        self.more = ttk.Button(self, text=T.icon("more"), style="Card.Icon.Subtle.TButton",
                               command=self._menu_at_button, takefocus=False)
        self.more.grid(row=0, column=5, rowspan=2)
        Tooltip(self.more, "More", T)
        for w in (self, self.title_lbl, self.sub, self.price_lbl, self.pill):
            w.bind("<Button-3>", self._context)
            w.bind("<Double-Button-1>", lambda _: self._open())
        self.title_lbl.bind("<Button-1>", lambda _: self._open())

    def _open(self):
        webbrowser.open(product_url(self.item["slug"], self.app.settings.get("region", "us")))

    def _menu(self):
        app, item = self.app, self.item
        m = tk.Menu(self, tearoff=0, **self.T.menu_colours())
        m.add_command(label="Check now", command=lambda: app._quick_check_item(item))
        m.add_command(label="Open in store", command=self._open)
        m.add_command(label="Copy link", command=lambda: app._copy(
            product_url(item["slug"], app.settings.get("region", "us"))))
        m.add_separator()
        m.add_command(label="Remove from favourites" if item.get("favourite") else "Add to favourites",
                      command=lambda: app._toggle_fav(item))
        m.add_command(label="Remove from watch list", command=lambda: app._remove_item(item))
        return m

    def _menu_at_button(self):
        m = self._menu()
        try:
            m.tk_popup(self.more.winfo_rootx(),
                       self.more.winfo_rooty() + self.more.winfo_height())
        finally:
            m.grab_release()

    def _context(self, event):
        m = self._menu()
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def update_status(self, in_stock, checked_at=None, price=None, note=None):
        self.in_stock = in_stock
        coming = bool(note and note.startswith("coming soon"))
        if in_stock is None:
            self.pill.config(text="NOT CHECKED", style="Neutral.Pill.TLabel")
        elif in_stock:
            self.pill.config(text="IN STOCK", style="Green.Pill.TLabel")
        elif coming:
            self.pill.config(text="COMING SOON", style="Amber.Pill.TLabel")
        else:
            self.pill.config(text="SOLD OUT", style="Red.Pill.TLabel")
        self.configure(style="Hot.Tile.TFrame" if in_stock else "Tile.TFrame")
        if in_stock:
            self.open_btn.grid(row=0, column=4, rowspan=2, padx=(0, self.T.S(6)))
        else:
            self.open_btn.grid_remove()
        if price:
            self.price_lbl.config(text=price)
        parts = []
        if note and not coming:
            parts.append(note)
        elif coming and "·" in note:
            parts.append(note.split("·", 1)[1].strip())
        if checked_at:
            parts.append(f"checked {checked_at}")
        if parts:
            self.sub.config(text="  ·  ".join(parts),
                            style="Card.Amber.TLabel" if (note and not in_stock) else "Card.Faint.TLabel")

    def mark_delisted(self):
        """The store no longer carries this slug; stop implying it is tracked."""
        self.in_stock = None
        self.pill.config(text="DELISTED", style="Amber.Pill.TLabel")
        self.open_btn.grid_remove()
        self.configure(style="Tile.TFrame")
        self.sub.config(text="No longer on the store. Remove it, or add it again if it comes back.",
                        style="Card.Amber.TLabel")


# ── History tab ───────────────────────────────────────────────────────────────

class HistoryTab(ttk.Frame):
    """Stock changes: watched products, or everything the store changed."""

    def __init__(self, parent, app):
        super().__init__(parent, padding=(app.theme.S(20), app.theme.S(14)))
        self.app, self.T = app, app.theme
        self._build()

    def _build(self):
        T, S = self.T, self.T.S
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, S(12)))
        self.watched_only = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="  Watched products only", variable=self.watched_only,
                        command=self.refresh).pack(side="left")
        self.q = tk.StringVar()
        self.q.trace_add("write", lambda *_: self.refresh())
        e = ttk.Entry(bar, textvariable=self.q, width=26)
        e.pack(side="left", padx=(S(14), 0))
        placeholder(e, self.q, "Filter by product name", T)
        ttk.Button(bar, text="Clear history", command=self._clear).pack(side="right")
        ttk.Button(bar, text="Export CSV…", command=self._export).pack(side="right", padx=(0, S(8)))
        self.stats_lbl = ttk.Label(self, style="Muted.TLabel")
        self.stats_lbl.pack(fill="x", pady=(0, S(8)))

        card = ttk.Frame(self, style="Tile.TFrame", padding=S(1))
        card.pack(fill="both", expand=True)
        inner = ttk.Frame(card, style="Card.TFrame", padding=S(4))
        inner.pack(fill="both", expand=True, padx=S(2), pady=S(2))
        cols = (("time", "When", 150), ("product", "Product", 330),
                ("change", "Change", 150), ("price", "Price", 100))
        self.tree = ttk.Treeview(inner, columns=[c[0] for c in cols], show="headings",
                                 selectmode="browse")
        for key, label, width in cols:
            self.tree.heading(key, text=label, anchor="w")
            self.tree.column(key, width=S(width), minwidth=S(60), anchor="w",
                             stretch=key == "product")
        self.tree.tag_configure("in", foreground=T.c["green"])
        self.tree.tag_configure("first", foreground=T.c["text2"])
        sb = ttk.Scrollbar(inner, orient="vertical", command=self.tree.yview,
                           style="Card.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-Button-1>", self._open_selected)
        self.empty = ttk.Label(inner, style="Card.Muted.TLabel", justify="center",
                               text="No stock changes yet.\nThey appear here as the watcher "
                                    "notices products coming into or going out of stock.")

    def _events(self):
        events = stock_history.get_events(limit=0, watched_only=self.watched_only.get())
        q = self.q.get().lower().strip()
        if q:
            events = [e for e in events if q in str(e.get("title", "")).lower()]
        return list(reversed(events))

    def refresh(self):
        stats = stock_history.get_stats()
        self.stats_lbl.config(
            text=f"{plural(stats.get('total_checks', 0), 'check')} of watched products  ·  "
                 f"{plural(stats.get('alerts', 0), 'in-stock alert')}")
        self.tree.delete(*self.tree.get_children())
        events = self._events()
        for i, ev in enumerate(events[:2000]):
            ts = friendly_time(ev.get("ts", ""))
            if ev.get("first"):
                change = "Added: in stock" if ev.get("in_stock") else "Added: sold out"
                tag = "first"
            else:
                change = "▲  In stock" if ev.get("in_stock") else "▼  Sold out"
                tag = "in" if ev.get("in_stock") else "out"
            self.tree.insert("", "end", iid=f"e{i}", values=(
                ts, ev.get("title", ev.get("slug")), change, ev.get("price") or ""),
                tags=(tag, ev.get("slug", "")))
        if events:
            self.empty.place_forget()
        else:
            self.empty.place(relx=0.5, rely=0.45, anchor="center")

    def _open_selected(self, _):
        sel = self.tree.selection()
        if sel:
            tags = self.tree.item(sel[0], "tags")
            if len(tags) > 1:
                webbrowser.open(product_url(tags[1], self.app.settings.get("region", "us")))

    def _export(self):
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            initialfile="unifi-stock-history.csv")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["time", "product", "slug", "in_stock", "price", "watched"])
            for e in reversed(self._events()):
                w.writerow([e.get("ts"), e.get("title"), e.get("slug"),
                            e.get("in_stock"), e.get("price") or "", e.get("watched", True)])
        self.app._log(f"History exported to {path}", "ok")

    def _clear(self):
        if messagebox.askyesno("Clear history",
                               "Delete the whole history of stock changes?", parent=self):
            stock_history.clear()
            self.refresh()


# ── Settings tab ──────────────────────────────────────────────────────────────

class SettingsTab(ttk.Frame):
    """Settings take effect, and are saved, as soon as they change."""

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app, self.T = app, app.theme
        self._swatch_imgs = []
        self._vars = {}
        self._build()

    # Layout helpers -------------------------------------------------------

    def _section(self, title):
        S = self.T.S
        ttk.Label(self.body, text=title, style="Strong.TLabel").pack(
            fill="x", pady=(S(18), S(6)))
        tile = ttk.Frame(self.body, style="Tile.TFrame", padding=S(1))
        tile.pack(fill="x")
        inner = ttk.Frame(tile, style="Card.TFrame")
        inner.pack(fill="x", padx=S(2), pady=S(2))
        inner._rows = 0
        return inner

    def _row(self, section, title, desc=None):
        """A row in a section: text on the left, the returned frame on the right."""
        S = self.T.S
        if section._rows:
            ttk.Frame(section, style="Divider.TFrame", height=1).pack(fill="x")
        section._rows += 1
        row = ttk.Frame(section, style="Card.TFrame", padding=(S(16), S(11)))
        row.pack(fill="x")
        row.columnconfigure(0, weight=1)
        text = ttk.Frame(row, style="Card.TFrame")
        text.grid(row=0, column=0, sticky="w")
        ttk.Label(text, text=title, style="Card.TLabel").pack(anchor="w")
        if desc:
            ttk.Label(text, text=desc, style="Card.Faint.TLabel",
                      wraplength=S(430), justify="left").pack(anchor="w", pady=(S(1), 0))
        right = ttk.Frame(row, style="Card.TFrame")
        right.grid(row=0, column=1, sticky="e", padx=(S(16), 0))
        return right

    def _switch(self, parent, key, on_change=None, value=None):
        var = tk.BooleanVar(value=self.app.settings.get(key) if value is None else value)
        self._vars[key] = var

        def changed():
            if on_change:
                on_change(var.get())
            else:
                self.app.set_setting(key, var.get())
        sw = ttk.Checkbutton(parent, variable=var, style="Card.Switch.TCheckbutton",
                             command=changed, takefocus=True)
        sw.pack(side="right")
        return sw

    # Build ------------------------------------------------------------------

    def _build(self):
        app, T, S = self.app, self.T, self.T.S
        sf = ScrollFrame(self, T)
        sf.pack(fill="both", expand=True)
        self.body = ttk.Frame(sf.inner, padding=(S(20), S(0), S(20), S(24)))
        self.body.pack(fill="x")

        sec = self._section("Watching")
        r = self._row(sec, "Check the store every",
                      "Each check reads the whole catalog, 8 requests, however many products "
                      "you watch. Every minute or slower is kindest to the store.")
        labels = [label for _, label in INTERVALS]
        cur = app.settings["poll_interval"]
        if interval_label(cur) not in labels:
            labels.append(interval_label(cur))
        self.interval = ttk.Combobox(r, state="readonly", values=labels, width=14)
        self.interval.set(interval_label(cur))
        self.interval.bind("<<ComboboxSelected>>", self._interval_changed)
        self.interval.pack(side="right")

        r = self._row(sec, "Store", "Prices and stock come from this regional store.")
        self._regions = {v["label"]: k for k, v in STORE_REGIONS.items()}
        self.region = ttk.Combobox(r, state="readonly", values=list(self._regions), width=18)
        self.region.set(STORE_REGIONS[app.settings["region"]]["label"])
        self.region.bind("<<ComboboxSelected>>",
                         lambda _: app.set_setting("region", self._regions[self.region.get()]))
        self.region.pack(side="right")

        r = self._row(sec, "Start watching when the app opens")
        self._switch(r, "auto_start")

        sec = self._section("Alerts")
        r = self._row(sec, "Show a notification",
                      "When a product on your watch list comes back in stock. "
                      "Click it to open the product's page.")
        self._switch(r, "notifications")
        r = self._row(sec, "Play a sound")
        self._switch(r, "sound_alerts")
        r = self._row(sec, "Open the product's page", "In your browser, the moment it's back.")
        self._switch(r, "auto_open_url")
        r = self._row(sec, "Try it", "Shows what an alert looks like.")
        ttk.Button(r, text="Send a test alert", command=app._test_notif).pack(side="right")

        sec = self._section("App")
        if winplatform.IS_WINDOWS:
            r = self._row(sec, "Start with Windows",
                          "Starts in the notification area when you sign in, and starts watching.")
            self._switch(r, "start_with_windows", on_change=app._set_autostart,
                         value=winplatform.autostart_enabled())
        r = self._row(sec, "Keep running in the notification area when closed",
                      "Closing the window keeps watching. Quit from the tray icon's menu.")
        self._switch(r, "close_to_tray")

        sec = self._section("Appearance")
        r = self._row(sec, "Theme")
        names = [label for _, label in THEME_NAMES]
        self.theme_box = ttk.Combobox(r, state="readonly", values=names, width=20)
        self.theme_box.set(dict(THEME_NAMES)[app.settings["theme"]])
        self.theme_box.bind("<<ComboboxSelected>>", lambda _: app.set_setting(
            "theme", {label: k for k, label in THEME_NAMES}[self.theme_box.get()]))
        self.theme_box.pack(side="right")
        r = self._row(sec, "Accent colour", "Buttons, switches and the selected tab.")
        self._build_swatches(r)
        r = self._row(sec, "Text size")
        self.size_box = ttk.Combobox(r, state="readonly", width=6,
                                     values=[str(s) for s in (9, 10, 11, 12, 13, 14)])
        self.size_box.set(str(app.settings["font_size"]))
        self.size_box.bind("<<ComboboxSelected>>",
                           lambda _: app.set_setting("font_size", int(self.size_box.get())))
        self.size_box.pack(side="right")

        sec = self._section("Watch list")
        r = self._row(sec, "Export the watch list", "A JSON file you can keep or share.")
        ttk.Button(r, text="Export…", command=self._export).pack(side="right")
        r = self._row(sec, "Import a watch list",
                      "Adds the products in a file. Ones you already watch are skipped.")
        ttk.Button(r, text="Import…", command=self._import).pack(side="right")

        sec = self._section("Updates")
        kind = updater.install_kind()
        kinds = {"installed": "Installed for this Windows user",
                 "portable": "Portable copy",
                 "folder": "Copied to this folder by hand",
                 "source": "Running from source: update with git pull"}
        r = self._row(sec, f"{APP_NAME} {__version__}", kinds[kind])
        self.check_btn = ttk.Button(r, text="Check for updates", command=app.check_for_updates)
        self.check_btn.pack(side="right")
        r = self._row(sec, "Status")
        self.update_lbl = ttk.Label(r, text=app.update_status, style="Card.Muted.TLabel",
                                    wraplength=S(360), justify="right")
        self.update_lbl.pack(side="right")
        self.install_row = r
        self.install_btn = ttk.Button(r, text="Install and restart", style="Card.Accent.TButton",
                                      command=app.install_update)
        r = self._row(sec, "Check for updates automatically",
                      "Once a day, one small request to GitHub. Nothing is downloaded "
                      "until you click Install.")
        self._switch(r, "check_updates")
        self.refresh_update()

        sec = self._section("About")
        r = self._row(sec, "Your files", str(BASE_DIR))
        ttk.Button(r, text="Open folder", command=lambda: winplatform.open_path(BASE_DIR)).pack(side="right")
        r = self._row(sec, "Report a problem",
                      f"Opens a GitHub issue with the version filled in. The log "
                      f"({LOG_FILE.name}, in the folder above) helps.")
        ttk.Button(r, text="Report…", command=lambda: webbrowser.open(ISSUE_URL)).pack(side="right")
        r = self._row(sec, "Project page", SITE_URL)
        ttk.Button(r, text="Open", command=lambda: webbrowser.open(SITE_URL)).pack(side="right")
        ttk.Label(self.body, style="Faint.TLabel", wraplength=S(640), justify="left",
                  text=f"{APP_NAME} is free and open source under the MIT license. It is an "
                       "independent project, not made, endorsed or supported by Ubiquiti. "
                       "UniFi is a trademark of Ubiquiti Inc.").pack(fill="x", pady=(S(18), 0))

    def _build_swatches(self, parent):
        T, S = self.T, self.T.S
        current = self.app.settings.get("accent", "")
        size = S(26)
        swatches = list(theming.ACCENT_SWATCHES)
        if current and current not in [h for _, h in swatches]:
            swatches.append(("Your colour", current))
        ttk.Button(parent, text="Custom…", style="Card.Subtle.TButton",
                   command=self._custom_accent).pack(side="right", padx=(S(4), 0))
        for name, hexv in reversed(swatches):
            colour = hexv or (winplatform.system_accent(T.dark) or T.c["default_accent"])
            R = gfx.Raster(size, size)
            if hexv == current:
                R.draw(gfx.sd_circle(size / 2, size / 2, size / 2 - 0.5), T.c["text"])
                R.draw(gfx.sd_circle(size / 2, size / 2, size / 2 - S(2.5)), T.c["surface"])
            R.draw(gfx.sd_circle(size / 2, size / 2, size / 2 - S(4)), colour)
            img = tk.PhotoImage(master=self, data=R.png_b64(), format="png")
            self._swatch_imgs.append(img)
            b = ttk.Button(parent, image=img, style="Card.Subtle.TButton", takefocus=False,
                           command=lambda h=hexv: self.app.set_setting("accent", h))
            b.configure(padding=S(2))
            b.pack(side="right")
            Tooltip(b, name if hexv else "Windows' accent colour", T)

    def _custom_accent(self):
        initial = self.app.settings.get("accent") or self.T.c["accent"]
        result = colorchooser.askcolor(color=initial, title="Accent colour", parent=self)
        if result and result[1]:
            self.app.set_setting("accent", result[1].lower())

    def _interval_changed(self, _):
        label = self.interval.get()
        seconds = next((s for s, lab in INTERVALS if lab == label), None)
        if seconds is None:
            seconds = int(label.split()[0])
        self.app.set_setting("poll_interval", seconds)

    def refresh_update(self):
        app = self.app
        self.update_lbl.config(text=app.update_status)
        u = app.available_update
        if u and u.can_install:
            self.install_btn.config(text=f"Install {u.version} and restart",
                                    state="disabled" if app.update_busy else "normal")
            self.install_btn.pack(side="right", padx=(self.T.S(12), 0))
        else:
            self.install_btn.pack_forget()
        self.check_btn.config(state="disabled" if app.update_busy else "normal")

    def _export(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".json", filetypes=[("JSON", "*.json")],
            initialfile="unifi-watchlist.json", parent=self)
        if path:
            n = export_watchlist(path)
            messagebox.showinfo("Exported", f"Exported {n} product(s) to:\n{path}", parent=self)

    def _import(self):
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")], parent=self)
        if path:
            try:
                n = import_watchlist(path)
            except Exception as ex:
                messagebox.showerror("Import failed", str(ex), parent=self)
                return
            self.app.watched = load_watched()
            self.app._refresh_list()
            messagebox.showinfo("Imported", f"Added {n} new product(s).", parent=self)


# ── Main app ──────────────────────────────────────────────────────────────────

class UnifiWatcherApp(tk.Tk):

    def __init__(self, start_minimized=False, watch_now=False, use_tray=True,
                 check_updates=True):
        super().__init__()
        self.settings = load_settings()
        if start_minimized and use_tray and traymod.AVAILABLE:
            self.withdraw()                 # no flash before going to the tray
        self.title(APP_NAME)
        try:
            if sys.platform == "win32":
                self.iconbitmap(default=str(resource("icon.ico")))
            else:
                self.iconphoto(True, tk.PhotoImage(file=str(resource("icon.png"))))
        except tk.TclError:
            pass
        self.theme = theming.Theme(self, self.settings)

        self.watched        = load_watched()
        self.rows           = {}
        self.status         = {}     # slug -> (in_stock, checked_at, price, note)
        self.notified       = {}
        self.watching       = False
        self.watcher_thread = None
        self._wake          = threading.Event()  # interrupts the poll countdown
        self._prev_status   = {}     # slug -> (in_stock, title, price, note), whole store
        self._delisted      = set()  # slugs the store no longer carries
        self._closed        = False  # guards callbacks from worker threads
        self._log_lines     = deque(maxlen=MAX_LOG_LINES)
        self._changes       = deque(maxlen=MAX_CHANGE_LINES)
        self._change_count  = 0
        self._tab           = "watch"
        self._bottom_view   = "changes"
        self._status_text   = ("Idle", "text3")
        self._countdown     = None
        self._tray_hint_shown = False
        self.available_update = None
        self.update_status  = "Not checked yet."
        self.update_busy    = False
        self._update_dismissed = False
        self._ui_queue      = queue.SimpleQueue()

        self._restore_geometry()
        self._build_ui()
        self._load_recent_changes()

        self.tray = None
        if use_tray and traymod.AVAILABLE:
            self.tray = traymod.TrayIcon(
                {k: resource(f"tray-{k}.ico") for k in ("idle", "watching", "alert")},
                APP_NAME, self._tray_menu,
                on_open=lambda: self._post(self.show_window),
                on_show=lambda: self._post(self.show_window),
                on_theme=lambda: self._post(self._on_system_theme),
                app_icon=resource("icon.ico"))
            if not self.tray.start():
                self.tray = None
        if start_minimized and not self.tray:
            self.deiconify()

        if (self.settings.get("auto_start") or watch_now) and self.watched:
            self.after(500, self._toggle_watch)

        if check_updates and self.settings.get("check_updates") \
                and updater.install_kind() != "source":
            self.after(4000, lambda: self.check_for_updates(quiet=True))

        winplatform.refresh_autostart()

        self.after(50, self._drain)
        self.bind("<F5>", lambda _: self._force_check())
        self.bind("<Control-n>", lambda _: self._open_browse())
        self.bind("<Control-Key-1>", lambda _: self._show_tab("watch"))
        self.bind("<Control-Key-2>", lambda _: self._show_tab("history"))
        self.bind("<Control-Key-3>", lambda _: self._show_tab("settings"))
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Thread plumbing ───────────────────────────────────────────────

    def _post(self, fn, *args):
        """Run fn on the Tk thread soon, unless the window has closed.

        Workers never call Tk themselves. A Tk call from another thread waits
        for the main loop to pick it up, which polls every 20 ms; a cycle that
        updated a few dozen widgets that way spent seconds queueing, and a
        call landing after the window closed raised out of the thread. They
        put work on this queue instead, and _drain runs it.
        """
        if not self._closed:
            self._ui_queue.put((fn, args))

    def _drain(self):
        if self._closed:
            return
        for _ in range(1000):
            try:
                fn, args = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn(*args)
            except tk.TclError:
                if self._closed:
                    return
                log.exception("UI update failed")
            except Exception:
                log.exception("UI update failed")
        try:
            self.after(40, self._drain)
        except tk.TclError:
            pass

    def destroy(self):
        self._closed = True
        super().destroy()

    # ── Window ────────────────────────────────────────────────────────

    def _restore_geometry(self):
        S = self.theme.S
        self.minsize(S(560), S(460))
        w, h = S(780), S(720)
        g = self.settings.get("window")
        if g and winplatform.point_on_screen(g[0] + 40, g[1] + 20):
            self.geometry(f"{max(g[2], S(560))}x{max(g[3], S(460))}+{g[0]}+{g[1]}")
        else:
            x = max(0, (self.winfo_screenwidth() - w) // 2)
            y = max(0, (self.winfo_screenheight() - h) // 3)
            self.geometry(f"{w}x{h}+{x}+{y}")

    def _save_geometry(self):
        try:
            if self.state() == "normal" and self.winfo_viewable():
                self.settings["window"] = [self.winfo_x(), self.winfo_y(),
                                           self.winfo_width(), self.winfo_height()]
                save_settings(self.settings)
        except (tk.TclError, OSError):
            pass

    def show_window(self):
        self.deiconify()
        if self.state() == "iconic":
            self.state("normal")
        self.lift()
        self.focus_force()
        self.attributes("-topmost", True)
        self.after(150, lambda: self.attributes("-topmost", False))

    def _on_close(self):
        """Closing the window: keep watching in the tray, or quit."""
        if self.tray and self.settings.get("close_to_tray", True):
            self._save_geometry()
            self.withdraw()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self.tray.notify(APP_NAME, "Still running here. "
                                 + ("It keeps watching. " if self.watching else "")
                                 + "Right-click the icon to quit.")
            return
        self.quit_app()

    def quit_app(self):
        """Stop the watcher and persist buffered history before exiting."""
        self._save_geometry()
        self.watching = False
        self._wake.set()
        try:
            stock_history.flush()
        except Exception:
            log.exception("Could not flush stock history on exit")
        if self.tray:
            self.tray.stop()
            self.tray = None
        self.destroy()

    def _copy(self, text):
        self.clipboard_clear()
        self.clipboard_append(text)

    # ── Building ──────────────────────────────────────────────────────

    def _build_ui(self):
        T, S = self.theme, self.theme.S
        T.style_window(self)

        header = ttk.Frame(self, padding=(S(20), S(14), S(14), 0))
        header.pack(fill="x")
        self._logo = tk.PhotoImage(master=self, data=gfx.bell_icon(S(28), badge=True).png_b64(),
                                   format="png")
        ttk.Label(header, image=self._logo).pack(side="left", padx=(0, S(10)))
        ttk.Label(header, text=APP_NAME, style="Title.TLabel").pack(side="left")
        menu_btn = ttk.Button(header, text=T.icon("more"), style="Icon.Subtle.TButton",
                              command=self._app_menu, takefocus=False)
        menu_btn.pack(side="right")
        self._menu_btn = menu_btn
        Tooltip(menu_btn, "More", T)
        status = ttk.Frame(header)
        status.pack(side="right", padx=(0, S(10)))
        self.status_dot = ttk.Label(status, text="●", style="Dot.TLabel")
        self.status_dot.pack(side="left", padx=(0, S(6)))
        self.status_lbl = ttk.Label(status, style="Muted.TLabel")
        self.status_lbl.pack(side="left")
        self.countdown_lbl = ttk.Label(status, style="Faint.TLabel")
        self.countdown_lbl.pack(side="left", padx=(S(8), 0))

        nav = ttk.Frame(self, padding=(S(14), S(6), S(20), 0))
        nav.pack(fill="x")
        self._nav = {}
        for key, label in (("watch", "Watch list"), ("history", "History"), ("settings", "Settings")):
            col = ttk.Frame(nav)
            col.pack(side="left", padx=(S(6), S(10)))
            lbl = ttk.Label(col, text=label, style="Nav.TLabel", cursor="hand2")
            lbl.pack()
            bar = ttk.Frame(col, style="NavBar.TFrame", height=S(3))
            lbl.bind("<Button-1>", lambda _, k=key: self._show_tab(k))
            self._nav[key] = (lbl, bar)
        ttk.Frame(self, style="Divider.TFrame", height=1).pack(fill="x")

        self.body = ttk.Frame(self)
        self.body.pack(fill="both", expand=True)
        self.watcher_tab = ttk.Frame(self.body)
        self._build_watcher_tab()
        # History and Settings are built the first time they are shown. Tk
        # lays out a subtree that has never been mapped slowly on every idle
        # pass: with Settings built up front, any style change, theme change
        # or new dialog cost about two seconds.
        self._history_widget = None
        self._settings_widget = None
        self._show_tab(self._tab)
        self._set_status(*self._status_text)
        self._set_countdown(self._countdown)
        if self.watching:
            # The toolbar is built idle. The watcher thread is still running,
            # so without this the button reads "Start watching" while
            # watching, and clicking it would start a second watcher.
            self.start_btn.config(text="Stop watching", style="TButton")
        self._refresh_list()

    def _rebuild_ui(self):
        """Build every widget again from the app's state: a theme, accent or
        text size change touches nearly everything, and classic Tk widgets
        (the logs, menus) don't follow ttk styles."""
        for w in self.winfo_children():
            if not isinstance(w, tk.Toplevel):
                w.destroy()
        self._build_ui()

    def _show_tab(self, key):
        self._tab = key
        if key == "history" and self._history_widget is None:
            self._history_widget = HistoryTab(self.body, self)
        if key == "settings" and self._settings_widget is None:
            self._settings_widget = SettingsTab(self.body, self)
        frames = {"watch": self.watcher_tab, "history": self._history_widget,
                  "settings": self._settings_widget}
        for k, f in frames.items():
            lbl, bar = self._nav[k]
            if k == key:
                f.pack(fill="both", expand=True)
                lbl.configure(style="NavActive.TLabel")
                bar.pack(fill="x", padx=self.theme.S(4))
            else:
                if f is not None:
                    f.pack_forget()
                lbl.configure(style="Nav.TLabel")
                bar.pack_forget()
        if key == "history":
            self._history_widget.refresh()

    def _build_watcher_tab(self):
        T, S = self.theme, self.theme.S
        p = self.watcher_tab

        bar = ttk.Frame(p, padding=(S(20), S(14), S(20), S(8)))
        bar.pack(fill="x")
        self.start_btn = ttk.Button(bar, text="Start watching", style="Accent.TButton",
                                    command=self._toggle_watch)
        self.start_btn.pack(side="left")
        Tooltip(self.start_btn, "Check the store on a timer and alert you when a product "
                                "comes back (F5 checks now)", T)
        add_btn = ttk.Button(bar, text="Add products…", command=self._open_browse)
        add_btn.pack(side="left", padx=(S(8), 0))
        Tooltip(add_btn, "Browse the store's catalog (Ctrl+N)", T)
        self.force_btn = ttk.Button(bar, text="Check now", command=self._force_check)
        self.force_btn.pack(side="left", padx=(S(8), 0))
        Tooltip(self.force_btn, "Check the whole store right now (F5)", T)
        self.store_lbl = ttk.Label(bar, style="Faint.TLabel")
        self.store_lbl.pack(side="right")
        self._update_store_label()

        self.banner_holder = ttk.Frame(p, padding=(S(20), 0))
        self.banner_holder.pack(fill="x")
        self._build_banner()

        self.paned = ttk.Panedwindow(p, orient="vertical")
        self.paned.pack(fill="both", expand=True, padx=S(20), pady=(S(4), S(16)))
        self.list_scroll = ScrollFrame(self.paned, T)
        self.list_canvas = self.list_scroll.canvas
        self.list_frame = self.list_scroll.inner
        self.paned.add(self.list_scroll, weight=1)

        panel = ttk.Frame(self.paned, style="Tile.TFrame", padding=S(1))
        self.paned.add(panel, weight=0)
        inner = ttk.Frame(panel, style="Card.TFrame", padding=(S(10), S(6), S(10), S(8)))
        inner.pack(fill="both", expand=True, padx=S(2), pady=S(2))
        head = ttk.Frame(inner, style="Card.TFrame")
        head.pack(fill="x")
        self._seg = {}
        for key, label in (("changes", "Store changes"), ("activity", "Activity")):
            b = ttk.Button(head, text=label, command=lambda k=key: self._set_bottom(k),
                           takefocus=False)
            b.pack(side="left", padx=(0, S(4)))
            self._seg[key] = b
        self._changes_count_lbl = ttk.Label(head, style="Card.Faint.TLabel")
        self._changes_count_lbl.pack(side="left", padx=(S(6), 0))
        ttk.Button(head, text="Clear", style="Card.Subtle.TButton",
                   command=self._clear_bottom, takefocus=False).pack(side="right")
        body = ttk.Frame(inner, style="Card.TFrame")
        body.pack(fill="both", expand=True, pady=(S(6), 0))
        mono = ("Cascadia Mono", T.size - 1) if "Cascadia Mono" in T.root.tk.call("font", "families") \
            else ("Consolas", T.size - 1)
        self.log_text = tk.Text(body, height=7, font=mono, state="disabled", wrap="none",
                                padx=S(4), pady=S(2), **T.text_widget_colours())
        self.changes_text = self.log_text      # one widget, two views
        sb = ttk.Scrollbar(body, orient="vertical", command=self.log_text.yview,
                           style="Card.Vertical.TScrollbar")
        self.log_text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)
        c = T.c
        for tag, colour in (("ok", c["green"]), ("warn", c["amber"]), ("err", c["red"]),
                            ("fav", c["gold"]), ("info", c["text2"]), ("time", c["text3"]),
                            ("in_stock", c["green"]), ("oos", c["red"]),
                            ("name", c["text"]), ("price", c["text2"])):
            self.log_text.tag_config(tag, foreground=colour)
        self._render_bottom()
        self._sash_placed = False
        self.paned.bind("<Configure>", lambda _: self._sash_placed or self._place_sash())

    def _place_sash(self):
        """Give the changes panel its height once the window has its size:
        on the first <Configure>, before anything is drawn."""
        try:
            h = self.paned.winfo_height()
            if h > self.theme.S(300):
                self.paned.sashpos(0, h - self.theme.S(190))
                self._sash_placed = True
        except tk.TclError:
            pass

    def _build_banner(self):
        for w in self.banner_holder.winfo_children():
            w.destroy()
        u = self.available_update
        if not u or self._update_dismissed:
            self.banner_holder.configure(padding=0)
            return
        S = self.theme.S
        self.banner_holder.configure(padding=(S(20), 0, S(20), S(8)))
        b = ttk.Frame(self.banner_holder, style="Banner.TFrame", padding=(S(14), S(8)))
        b.pack(fill="x")
        text = (f"Version {u.version} is available." if u.can_install
                else f"Version {u.version} is out: update with git pull.")
        if self.update_busy:
            text = self.update_status
        ttk.Label(b, text=text, style="Banner.TLabel").pack(side="left")
        ttk.Button(b, text=self.theme.icon("close"), style="Banner.Icon.Subtle.TButton",
                   command=self._dismiss_banner, takefocus=False).pack(side="right")
        ttk.Button(b, text="What's new", style="Banner.TButton",
                   command=lambda: webbrowser.open(f"{updater.RELEASES_URL}/tag/v{u.version}"),
                   takefocus=False).pack(side="right", padx=(S(8), S(4)))
        if u.can_install:
            ttk.Button(b, text="Install and restart", style="Banner.Accent.TButton",
                       command=self.install_update,
                       state="disabled" if self.update_busy else "normal").pack(side="right")

    def _dismiss_banner(self):
        self._update_dismissed = True
        self._build_banner()

    def _update_store_label(self):
        region = self.settings.get("region", "us")
        self.store_lbl.config(text=f"{STORE_REGIONS[region]['label']} store  ·  "
                                   f"every {interval_label(self.settings['poll_interval'])}")

    # ── Settings ──────────────────────────────────────────────────────

    def set_setting(self, key, value):
        """Change one setting: save it, and make it take effect."""
        if self.settings.get(key) == value:
            return
        new = dict(self.settings)
        new[key] = value
        self._on_settings_apply(new)

    def _on_settings_apply(self, new_settings):
        old = self.settings
        self.settings = dict(new_settings)
        save_settings(self.settings)
        if old.get("region", "us") != self.settings.get("region", "us"):
            self._on_region_changed(old.get("region", "us"), self.settings.get("region", "us"))
        if any(old.get(k) != self.settings.get(k) for k in ("theme", "accent", "font_size")):
            self._retheme()
        else:
            self._update_store_label()

    def _retheme(self):
        self.theme.apply(self.settings)
        # ttk handles a style change in an idle callback; widgets built before
        # it has run can be left undrawn. Let it run first.
        self.update_idletasks()
        self._rebuild_ui()

    def _on_system_theme(self):
        if self.settings.get("theme") == "system" or not self.settings.get("accent"):
            self._retheme()

    def _on_region_changed(self, old, new):
        """Drop state that only made sense for the previous store."""
        # The catalog baseline is per-region; keeping it would diff US prices
        # and stock against EU ones and report the whole store as "changed".
        self._prev_status = {}
        self.notified.clear()
        self._delisted.clear()      # a slug absent from one store may exist in another
        self.status.clear()
        invalidate_build_id()
        self._refresh_list()
        self._log(f"Store changed from {STORE_REGIONS[old]['label']} to "
                  f"{STORE_REGIONS[new]['label']}: starting a new baseline.", "warn")

    def _set_autostart(self, enabled):
        if not winplatform.set_autostart(enabled):
            messagebox.showerror(APP_NAME, "Couldn't change Start with Windows. "
                                 "See the log for details.", parent=self)
        self._log("Starts with Windows." if enabled else "Doesn't start with Windows any more.", "info")

    # ── List rendering ────────────────────────────────────────────────

    def _refresh_list(self):
        for w in self.list_frame.winfo_children():
            w.destroy()
        self.rows = {}
        S = self.theme.S

        if not self.watched:
            empty = ttk.Frame(self.list_frame, padding=(0, S(60)))
            empty.pack(fill="x")
            ttk.Label(empty, text="Nothing on your watch list yet", style="Heading.TLabel").pack()
            ttk.Label(empty, style="Muted.TLabel", justify="center",
                      text="Add the products you want, and you'll hear the moment\n"
                           "one comes back in stock.").pack(pady=(S(6), S(14)))
            ttk.Button(empty, text="Add products…", style="Accent.TButton",
                       command=self._open_browse).pack()
            self._update_title()
            return

        favourites = [i for i in self.watched if i.get("favourite")]
        others     = [i for i in self.watched if not i.get("favourite")]
        for title, items, top in (("Favourites", favourites, 0),
                                  ("Watching", others, S(14) if favourites else 0)):
            if not items:
                continue
            ttk.Label(self.list_frame, text=f"{title.upper()}  ·  {len(items)}",
                      style="Caps.TLabel").pack(fill="x", pady=(top, S(6)))
            for item in items:
                row = WatchedRow(self.list_frame, self, item)
                # Status first, then pack: a row that changes size after it is
                # on screen leaves its old outline to be repainted.
                if item["slug"] in self._delisted:
                    row.mark_delisted()
                elif item["slug"] in self.status:
                    row.update_status(*self.status[item["slug"]])
                row.pack(fill="x", pady=(0, S(6)))
                self.rows[item["slug"]] = row

        self.notified = {w["slug"]: self.notified.get(w["slug"], False) for w in self.watched}
        self._update_title()

    def _update_title(self):
        n_in = sum(1 for s in self.status.values() if s[0])
        self.title(f"{APP_NAME}  ·  {n_in} in stock" if n_in else APP_NAME)
        self._update_tray()

    def _update_tray(self):
        if not getattr(self, "tray", None):
            return
        n_in = sum(1 for slug, s in self.status.items() if s[0] and slug in self.rows)
        state = "alert" if n_in else ("watching" if self.watching else "idle")
        n = len(self.watched)
        tip = (f"{APP_NAME}: watching {n} product{'s' if n != 1 else ''}" if self.watching
               else f"{APP_NAME}: not watching")
        if n_in:
            tip += f", {n_in} in stock"
        self.tray.set_state(state, tip)

    # ── Item actions ──────────────────────────────────────────────────

    def _remove_item(self, item):
        self.watched = [w for w in self.watched if w["slug"] != item["slug"]]
        self.status.pop(item["slug"], None)
        save_watched(self.watched)
        self._refresh_list()
        self._log(f"Removed {item['title']}", "warn")

    def _toggle_fav(self, item):
        is_fav = False
        for w in self.watched:
            if w["slug"] == item["slug"]:
                w["favourite"] = not w.get("favourite", False)
                is_fav = w["favourite"]
                break
        save_watched(self.watched)
        self._refresh_list()
        self._log(f"{item['title']} {'added to' if is_fav else 'removed from'} favourites",
                  "fav" if is_fav else "info")

    def _quick_check_item(self, item):
        self._log(f"Checking {item['title']}…", "info")

        def run():
            try:
                region   = self.settings.get("region", "us")
                build_id = get_build_id(region)
                in_stock, price = check_slug(build_id, item["slug"], region)
                note = None
                checked  = datetime.now().strftime("%H:%M")
                stock_history.record(item["slug"], item["title"], in_stock, price)
                self._post(self._update_row, item["slug"], in_stock, checked, price, note)
                prev = self._prev_status.get(item["slug"])
                if prev is not None and prev[0] != in_stock:
                    self._post(self._add_change, item["title"], in_stock, price)
                self._prev_status[item["slug"]] = (in_stock, item["title"], price, note)
                self._maybe_alert(item, in_stock)
            except ProductNotFound:
                self._delisted.add(item["slug"])
                self._post(self._mark_delisted, item["slug"], item["title"])
            except Exception as e:
                log.exception("Quick-check failed for %s", item["slug"])
                self._post(self._log, f"Couldn't check {item['title']}: {e}", "err")
        threading.Thread(target=run, daemon=True, name="unifi-quick").start()

    def _open_browse(self):
        BrowseDialog(self, self.watched, self._add_items)

    def _add_items(self, picks):
        existing = {w["slug"] for w in self.watched}
        added = [p for p in picks if p["slug"] not in existing]
        self.watched.extend(added)
        save_watched(self.watched)
        self._refresh_list()
        self._log(f"Added {len(added)} product(s) to the watch list.", "ok")
        if added and self.watching:
            self._force_check()

    # ── Watcher loop ──────────────────────────────────────────────────

    def _toggle_watch(self):
        if self.watching:
            self.watching = False
            self._wake.set()        # cut the countdown short
            self.start_btn.config(text="Start watching", style="Accent.TButton")
            self._set_countdown(None)
            self._set_status("Idle", "text3")
            self._log("Stopped watching.", "warn")
        else:
            if self.watcher_thread and self.watcher_thread.is_alive():
                return              # previous loop still winding down
            if not self.watched:
                messagebox.showinfo(APP_NAME, "Add some products to watch first.", parent=self)
                return
            self.watching = True
            self._wake.clear()
            self.start_btn.config(text="Stop watching", style="TButton")
            self._set_status("Watching", "green")
            self._log(f"Watching {len(self.watched)} product(s).", "ok")
            self.watcher_thread = threading.Thread(
                target=self._watch_loop, daemon=True, name="unifi-watch")
            self.watcher_thread.start()
        self._update_tray()

    def _force_check(self):
        if self.watching:
            self._wake.set()
            self._log("Checking now…", "info")
        elif not (self.watcher_thread and self.watcher_thread.is_alive()):
            # Not watching: one check, then back to idle.
            self._log("Checking once…", "info")
            self.watcher_thread = threading.Thread(
                target=self._check_once, daemon=True, name="unifi-watch-once")
            self.watcher_thread.start()

    def _check_once(self):
        self._cycle()
        self._post(self._set_status, "Idle", "text3")

    def _check_store_layout(self):
        """Once a day, notice categories the store has added and adopt them.

        Runs on the watcher thread so a slow or failed check never blocks the
        UI, and never stops a cycle - a store layout change is worth knowing
        about, but it is not worth missing a restock over.
        """
        try:
            region = self.settings.get("region", "us")
            adopted = refresh_category_coverage(get_build_id(region), region)
        except Exception as e:
            log.warning("Category coverage check failed: %s", e)
            return
        for cat in adopted:
            self._post(self._log,
                       f"The store added a category; now also watching {cat}", "warn")

    def _watch_loop(self):
        while self.watching:
            # Consume any pending wake here, at the start of the work, rather
            # than at the start of the countdown. Clearing it later would
            # discard a Check Now pressed *during* the fetch.
            self._wake.clear()
            self._cycle()
            if self.watching:
                self._post(self._set_status, "Watching", "green")
                self._wait_for_next_cycle(self.settings.get("poll_interval", 60))

    def _cycle(self):
        """One check of the whole store: diff it, update the watched rows."""
        self._post(self._set_status, "Checking…", "amber")
        region = self.settings.get("region", "us")
        # At most once a day (category_cache.json says when), so a watcher
        # left running for weeks still notices a new store category.
        self._check_store_layout()
        try:
            build_id = get_build_id(region)
            all_products = fetch_all_products(build_id, region)
            checked = datetime.now().strftime("%H:%M")
            watched_slugs = {w["slug"] for w in self.watched}

            current_status = {}      # slug -> (in_stock, title, price, note)
            for p in all_products:
                slug = p.get("slug")
                if slug:
                    current_status[slug] = (is_available(p), p.get("title", slug),
                                            get_price(p), restock_note(p))

            if self._prev_status:
                changes = 0
                for slug, (now_avail, title, price, _) in current_status.items():
                    prev = self._prev_status.get(slug)
                    if prev is not None and prev[0] != now_avail:
                        changes += 1
                        self._post(self._add_change, title, now_avail, price)
                        if slug not in watched_slugs:
                            stock_history.record(slug, title, now_avail, price, watched=False)
                self._post(self._log,
                           f"{changes} stock change(s) across the store." if changes else
                           f"No changes across {len(current_status)} products.",
                           "warn" if changes else "info")
            else:
                self._post(self._log, f"Baseline: {len(current_status)} products in the catalog.",
                           "info")
            self._prev_status = current_status

            for item in list(self.watched):
                if self._closed:
                    break
                slug, title = item["slug"], item["title"]
                if slug in self._delisted:
                    continue
                entry = current_status.get(slug)
                if entry:
                    in_stock, _, price, note = entry
                else:
                    # Not in the catalog: either genuinely delisted, or a
                    # product the category pages do not carry. Ask directly.
                    note = None
                    try:
                        in_stock, price = check_slug(
                            build_id, slug, region,
                            retries=self.settings.get("max_retries", 3))
                    except ProductNotFound:
                        self._delisted.add(slug)
                        self._post(self._mark_delisted, slug, title)
                        continue
                    except StoreError as e:
                        self._post(self._log, f"Couldn't check {title}: {e}", "err")
                        continue
                stock_history.record(slug, title, in_stock, price)
                self._post(self._update_row, slug, in_stock, checked, price, note)
                self._maybe_alert(item, in_stock)
            stock_history.flush()
        except Exception as e:
            log.warning("Store check failed: %s", e)
            self._post(self._log, f"Couldn't reach the store: {e}", "err")

    def _maybe_alert(self, item, in_stock):
        """Alert once per return to stock. Marked here, on the worker, so a
        quick check and a cycle finding it together alert once, not twice."""
        slug = item["slug"]
        if in_stock and not self.notified.get(slug):
            self.notified[slug] = True
            self._post(self._on_in_stock, item)
        elif not in_stock:
            self.notified[slug] = False

    def _wait_for_next_cycle(self, interval):
        """Count down to the next poll, waking immediately on stop or F5.

        The event is deliberately *not* cleared here - _watch_loop clears it
        when a cycle begins, so a wake raised while that cycle was running is
        still honoured when the countdown starts.
        """
        deadline = time.monotonic() + interval
        while self.watching:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self._post(self._set_countdown, int(remaining) + 1)
            if self._wake.wait(min(1.0, remaining)):
                break
        self._post(self._set_countdown, None)

    def _set_countdown(self, seconds):
        self._countdown = seconds
        if seconds is None:
            text = ""
        elif seconds >= 120:
            text = f"next check in {seconds // 60} min"
        else:
            text = f"next check in {seconds} s"
        try:
            self.countdown_lbl.config(text=text)
        except tk.TclError:
            pass

    def _mark_delisted(self, slug, title):
        row = self.rows.get(slug)
        if row:
            row.mark_delisted()
        self._log(f"{title} is no longer on the store; skipping it.", "warn")

    def _update_row(self, slug, in_stock, checked_at, price=None, note=None):
        self.status[slug] = (in_stock, checked_at, price, note)
        row = self.rows.get(slug)
        if row:
            row.update_status(in_stock, checked_at, price, note)
        title = next((w["title"] for w in self.watched if w["slug"] == slug), slug)
        price_text = f" ({price})" if price else ""
        self._log(f"{title}: {'in stock' if in_stock else 'sold out'}{price_text}",
                  "ok" if in_stock else "info")
        self._update_title()

    def _on_in_stock(self, item):
        self.notified[item["slug"]] = True
        is_fav = item.get("favourite", False)
        region = self.settings.get("region", "us")
        url = product_url(item["slug"], region)
        self._log(f"{'★ ' if is_fav else ''}In stock: {item['title']}", "ok")
        stock_history.record_alert()
        if self.settings.get("notifications", True):
            self._notify(f"{'★ ' if is_fav else ''}Back in stock: {item['title']}",
                         f"{item['title']} is available on the {STORE_REGIONS[region]['label']} "
                         "store. Click to open it.", url)
        if self.settings.get("sound_alerts", True):
            play_sound()
        if self.settings.get("auto_open_url", True):
            webbrowser.open(url)

    def _notify(self, title, text, url=None):
        if self.tray:
            self.tray.notify(title, text,
                             on_click=(lambda: webbrowser.open(url)) if url else
                             (lambda: self._post(self.show_window)))
        else:
            notify_windows(title, text)

    # ── Bottom panel: store changes and activity ──────────────────────

    def _load_recent_changes(self):
        """The changes feed starts with the latest ones from the history."""
        for ev in [e for e in stock_history.get_events(limit=200) if not e.get("first")][-50:]:
            ts = str(ev.get("ts", ""))
            self._changes.append((ts[11:16] if len(ts) >= 16 else ts, ev.get("title", ""),
                                  bool(ev.get("in_stock")), ev.get("price")))
        self._render_bottom()

    def _set_bottom(self, key):
        self._bottom_view = key
        self._render_bottom()

    def _render_bottom(self):
        if not hasattr(self, "log_text"):
            return
        for key, b in self._seg.items():
            b.configure(style="Card.TButton" if key == self._bottom_view else "Card.Subtle.TButton")
        t = self.log_text
        t.configure(state="normal")
        t.delete("1.0", "end")
        if self._bottom_view == "changes":
            for entry in self._changes:
                self._insert_change(*entry)
            self._changes_count_lbl.config(
                text=f"{self._change_count} this session" if self._change_count else
                "products coming into or going out of stock, anywhere in the store")
        else:
            for ts, msg, tag in self._log_lines:
                t.insert("end", f"{ts}  ", "time")
                t.insert("end", msg + "\n", tag)
            self._changes_count_lbl.config(text="")
        t.see("end")
        t.configure(state="disabled")

    def _insert_change(self, ts, title, in_stock, price):
        t = self.changes_text
        t.insert("end", f"{ts}  ", "time")
        if in_stock:
            t.insert("end", "▲ In stock   ", "in_stock")
        else:
            t.insert("end", "▼ Sold out   ", "oos")
        t.insert("end", title, "name")
        if price:
            t.insert("end", f"  {price}", "price")
        t.insert("end", "\n")

    def _add_change(self, title, in_stock, price=None):
        """Add a stock transition to the changes feed."""
        ts = datetime.now().strftime("%H:%M")
        self._change_count += 1
        self._changes.append((ts, title, in_stock, price))
        if self._bottom_view == "changes":
            t = self.changes_text
            t.configure(state="normal")
            self._insert_change(ts, title, in_stock, price)
            _trim_text(t, MAX_CHANGE_LINES)
            t.see("end")
            t.configure(state="disabled")
            self._changes_count_lbl.config(text=f"{self._change_count} this session")
        if self._tab == "history" and self._history_widget:
            self._history_widget.refresh()

    def _clear_bottom(self):
        if self._bottom_view == "changes":
            self._clear_changes()
        else:
            self._clear_log()

    def _clear_changes(self):
        self._changes.clear()
        self._change_count = 0
        self._render_bottom()

    def _log(self, msg, tag="info"):
        ts = datetime.now().strftime("%H:%M:%S")
        self._log_lines.append((ts, msg, tag))
        log.info(msg)
        if self._bottom_view != "activity":
            return
        t = self.log_text
        t.configure(state="normal")
        t.insert("end", f"{ts}  ", "time")
        t.insert("end", msg + "\n", tag)
        _trim_text(t, MAX_LOG_LINES)
        t.see("end")
        t.configure(state="disabled")

    def _clear_log(self):
        self._log_lines.clear()
        self._render_bottom()

    def _set_status(self, text, colour):
        self._status_text = (text, colour)
        c = self.theme.c
        self.status_lbl.config(text=text)
        self.status_dot.config(foreground=c.get(colour, colour))

    # ── Menus ─────────────────────────────────────────────────────────

    def _app_menu(self):
        m = tk.Menu(self, tearoff=0, **self.theme.menu_colours())
        m.add_command(label="Check for updates", command=self.check_for_updates)
        m.add_command(label="Open your files' folder", command=lambda: winplatform.open_path(BASE_DIR))
        m.add_command(label="Report a problem…", command=lambda: webbrowser.open(ISSUE_URL))
        m.add_command(label="Project page", command=lambda: webbrowser.open(SITE_URL))
        m.add_separator()
        m.add_command(label="Quit", command=self.quit_app)
        b = self._menu_btn
        try:
            m.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height())
        finally:
            m.grab_release()

    def _tray_menu(self):
        """Built on the tray thread: read state only, act through _post."""
        Item = traymod.MenuItem
        items = [Item(f"Open {APP_NAME}", lambda: self._post(self.show_window), default=True),
                 Item(None),
                 Item("Stop watching" if self.watching else "Start watching",
                      lambda: self._post(self._toggle_watch), enabled=bool(self.watched)),
                 Item("Check now", lambda: self._post(self._force_check),
                      enabled=bool(self.watched))]
        if self.available_update and self.available_update.can_install:
            items += [Item(None), Item(f"Install version {self.available_update.version}",
                                       lambda: self._post(self.install_update))]
        items += [Item(None), Item("Quit", lambda: self._post(self.quit_app))]
        return items

    def _test_notif(self):
        self._log("Sending a test alert…", "info")
        self._notify(f"TEST: {APP_NAME}", "This is what an in-stock alert looks like.",
                     store_home(self.settings.get("region", "us")))
        if self.settings.get("sound_alerts", True):
            play_sound()

    # ── Updates ───────────────────────────────────────────────────────

    def _refresh_update_ui(self):
        self._build_banner()
        if self._settings_widget:
            try:
                self._settings_widget.refresh_update()
            except tk.TclError:
                pass

    def check_for_updates(self, quiet=False):
        if self.update_busy:
            return
        self.update_busy = True
        self.update_status = "Checking…"
        self._refresh_update_ui()

        def run():
            try:
                u = updater.check()
                status = (f"Version {u.version} is available." if u else
                          f"You're up to date ({__version__}).")
            except Exception as e:
                log.warning("Update check failed: %s", e)
                u, status = None, f"Couldn't check: {e}"
            self._post(self._on_update_checked, u, status, quiet)
        threading.Thread(target=run, daemon=True, name="unifi-update-check").start()
        if quiet:
            self.after(UPDATE_RECHECK_MS, lambda: self.settings.get("check_updates")
                       and self.check_for_updates(quiet=True))

    def _on_update_checked(self, u, status, quiet):
        self.update_busy = False
        self.update_status = status
        if u and (not self.available_update or self.available_update.version != u.version):
            self._update_dismissed = False
            self._log(f"Version {u.version} is available.", "ok")
            if self.tray and not self.winfo_viewable():
                self.tray.notify(f"{APP_NAME} {u.version} is available",
                                 "Click to open the app and install it.",
                                 on_click=lambda: self._post(self.show_window))
        self.available_update = u
        self._refresh_update_ui()

    def install_update(self):
        u = self.available_update
        if not u or not u.can_install or self.update_busy:
            return
        self.update_busy = True
        self.update_status = f"Downloading {u.version}…"
        self._refresh_update_ui()

        def progress(done, total):
            pct = int(done * 100 / total) if total else 0
            self.update_status = f"Downloading {u.version}… {pct}%"
            self._post(self._refresh_update_ui)

        def run():
            try:
                path = updater.download(u, progress=progress)
                self.update_status = f"Installing {u.version}…"
                self._post(self._refresh_update_ui)
                self.settings["resume_watching"] = self.watching
                save_settings(self.settings)
                updater.apply(u, path)
                self._post(self.quit_app)
            except Exception as e:
                log.exception("Update failed")
                # Bound now: `e` is unbound once this block ends, before the
                # deferred dialog runs.
                msg = str(e)
                self.update_busy = False
                self.update_status = f"The update didn't install: {msg}"
                self._post(self._refresh_update_ui)
                self._post(lambda: messagebox.showerror(
                    APP_NAME, f"The update didn't install:\n\n{msg}", parent=self))
        threading.Thread(target=run, daemon=True, name="unifi-update").start()


# ── Entry point ───────────────────────────────────────────────────────────────

def self_test():
    """Build the real window, every tab and the tray icon, then write
    selftest.json to the data folder and exit: what CI runs against the
    frozen build, which is where a missing asset or module shows up. No
    network; point USW_DATA_DIR at an empty folder first."""
    from unifi_core import write_json
    report = {"version": __version__, "kind": updater.install_kind(),
              "data_dir": str(BASE_DIR), "frozen": bool(getattr(sys, "frozen", False))}
    app = UnifiWatcherApp(use_tray=False, check_updates=False)
    tray_icon = traymod.TrayIcon(
        {k: resource(f"tray-{k}.ico") for k in ("idle", "watching", "alert")},
        APP_NAME, lambda: [], app_icon=resource("icon.ico"))
    report["tray"] = tray_icon.start() if traymod.AVAILABLE else None
    tray_icon.stop()

    def finish():
        report.update(theme_dark=app.theme.dark, images=len(app.theme._images),
                      history=app._history_widget is not None,
                      settings=app._settings_widget is not None, ok=True)
        write_json(BASE_DIR / "selftest.json", report)
        app.quit_app()

    def tabs():
        for key in ("history", "settings", "watch"):
            app._show_tab(key)
        app.after(500, finish)
    app.after(1000, tabs)
    app.after(30000, app.quit_app)          # never hang a CI job
    app.mainloop()
    return 0 if report.get("ok") else 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--finish-update" in argv:
        # The staged copy of a portable update: swap it in, start it, exit.
        i = argv.index("--finish-update")
        return updater.finish_update(argv[i + 1], argv[i + 2])
    if "--version" in argv:
        print(f"{APP_NAME} {__version__}")
        return 0
    if "--self-test" in argv:
        return self_test()

    if not _HAVE_REQUESTS:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Missing dependency", missing_message())
        return 1

    setup_logging()
    log.info("%s %s starting (%s)", APP_NAME, __version__, updater.install_kind())
    migrate_legacy_files()

    instance = winplatform.SingleInstance()
    if instance.already_running:
        if instance.signal_existing():
            return 0
        log.warning("Another copy holds the mutex but has no tray window; starting anyway")
    if updater.install_kind() == "installed":
        winplatform.set_app_id(APP_ID)

    settings = load_settings()
    resume = False
    if "--updated" in argv or "--update-failed" in argv:
        updater.cleanup_after_update()
        resume = bool(settings.get("resume_watching"))
        if resume:
            settings["resume_watching"] = False
            save_settings(settings)

    app = UnifiWatcherApp(start_minimized="--minimized" in argv,
                          watch_now="--watch" in argv or resume)
    if "--updated" in argv:
        app._log(f"Updated to {__version__}.", "ok")
    if "--update-failed" in argv:
        app.after(800, lambda: messagebox.showerror(
            APP_NAME, "The update couldn't be installed, so this version was started "
                      f"again. Details are in {updater.UPDATE_LOG}.", parent=app))
    try:
        app.mainloop()
    finally:
        stock_history.flush()
        instance.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
