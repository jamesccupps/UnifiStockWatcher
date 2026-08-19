# UniFi Stock Watcher

A desktop stock monitoring tool for the [Ubiquiti Store](https://store.ui.com). Tracks product availability across the entire UniFi catalog and alerts you the moment something comes back in stock — with Windows notifications, sound alerts, and automatic browser launch.

Built with Python and tkinter. No API key required — works by polling the public store API.

![Python](https://img.shields.io/badge/python-3.8+-blue)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)
[![CI](https://github.com/jamesccupps/UnifiStockWatcher/actions/workflows/ci.yml/badge.svg)](https://github.com/jamesccupps/UnifiStockWatcher/actions/workflows/ci.yml)

## Features

- **Full-store stock change monitoring** — Every poll cycle fetches the entire UniFi catalog (~420 products) and diffs against the previous snapshot, showing every stock transition across the store in a live feed
- **Watch list with favourites** — Pick specific out-of-stock items to monitor, star the ones you care about most
- **Windows notifications + sound alerts** — System tray balloon notifications with optional sound when a watched item comes back in stock
- **Auto browser launch** — Automatically opens the store page when something you're watching becomes available
- **Price tracking** — Displays current prices with proper currency formatting (USD, EUR, GBP, CAD)
- **Self-updating category list** — Follows the store's own redirects and adopts new categories automatically, so a rename or addition on Ubiquiti's side does not silently stop products being watched
- **Restock estimates** — Shows the store's own restock date and how long an item has been sold out, e.g. `back ~7 Sep  ·  sold out 13 days`, on watched items and in the browse list
- **Category filtering** — Browse dialog filters by product category (Gateways, Switching, WiFi, Cameras, Door Access, etc.)
- **Multi-region support** — US, EU, UK, and Canada stores
- **Configurable poll interval** — 15s to 600s, adjustable from Settings
- **Stock history tab** — Persistent log of every check with statistics
- **Export/Import watch lists** — JSON backup and sharing
- **Per-item quick check** — Check a single product immediately without waiting for the next cycle
- **Force check (F5)** — Interrupt the countdown and trigger an immediate full poll
- **Colour themes** — 5 built-in presets (Dark, Midnight Blue, Slate, Light, UniFi Blue) plus full custom colour picker
- **Customizable typography** — Font family and size settings
- **Auto-start option** — Begin monitoring immediately on launch
- **CLI mode** — Headless console watcher for servers or background use

## Screenshots

<img width="2032" height="1163" alt="image" src="https://github.com/user-attachments/assets/1f2d694a-ea53-4c13-887e-32ea800a1be9" />

## Installation

### Prerequisites

- Python 3.8+ ([python.org](https://python.org) — check "Add to PATH" during install)
- Windows 10/11 (notifications use Windows Forms)

### Quick Start — no Python needed

Grab `UnifiStockWatcher.exe` from the [latest release](https://github.com/jamesccupps/UnifiStockWatcher/releases/latest) and double-click it.

It is portable: `watched_items.json`, `settings.json` and `stock_history.json` are kept in the folder you put the executable in, so choose somewhere writable rather than `Program Files`. `UnifiStockWatcher-cli.exe` is the headless watcher, for running under Task Scheduler.

The executables are unsigned, so SmartScreen warns on first run (*More info → Run anyway*), and some antivirus engines flag unsigned PyInstaller builds. Running from source avoids both.

### From source

1. Download or clone this repo
2. Double-click `install_and_run.bat`
3. Choose option 1 to launch the GUI

### Manual Setup

```bash
pip install requests
python unifi_watcher_gui.py
```

### CLI Mode

```bash
python unifi_watcher.py           # Start watching (setup on first run)
python unifi_watcher.py --setup   # Re-pick watched items
python unifi_watcher.py --test    # Verify notifications work
python unifi_watcher.py --version # Print the version and config directory
```

## Usage

### GUI

1. Click **"+ Add Items"** to browse the store catalog
2. Filter by category, search by name, or toggle "Show in-stock" to see everything
3. Check the items you want to watch and click **"Add to Watch List"**
4. Star your highest-priority items (they get a dedicated Favourites section)
5. Click **"▶ Start Watching"** to begin monitoring

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `F5` | Force immediate check |
| `Ctrl+N` | Open browse dialog |

### Stock Changes Feed

The **STOCK CHANGES** panel below the watch list shows every stock transition across the *entire* UniFi store — not just your watched items. Green ▲ arrows for items coming back in stock, red ▼ arrows for sellouts.

The first check cycle establishes a baseline. Every cycle after that diffs the full catalog and logs transitions.

### Settings

The Settings tab lets you configure:

- **Poll interval** — How often to check (15–600 seconds)
- **Sound alerts** — Toggle system sound on in-stock alerts
- **Auto-open URL** — Toggle automatic browser launch
- **Auto-start** — Begin watching immediately on launch
- **Store region** — US, EU, UK, or Canada
- **Colour theme** — Presets or custom colours
- **Font** — Family and size

## Project Structure

```
UnifiStockWatcher/
├── unifi_core.py          # Shared module: store API, config, notifications, history
├── _bootstrap.py          # Dependency check, runs before unifi_core is imported
├── unifi_watcher.py       # CLI watcher (headless console mode)
├── unifi_watcher_gui.py   # GUI application (tkinter)
├── tests/                 # pytest suite (offline)
├── install_and_run.bat    # Windows launcher with dependency install
├── launch_gui.bat         # Quick GUI launcher
├── requirements.txt       # Python dependencies
├── .gitignore
├── LICENSE
└── README.md
```

### Generated Files (not tracked)

These are created at runtime and excluded via `.gitignore`:

| File | Purpose |
|------|---------|
| `watched_items.json` | Your watch list |
| `settings.json` | GUI settings and preferences |
| `stock_history.json` | Stock check event log |

## How It Works

The tool uses Ubiquiti's public Next.js store API:

1. Fetches the `buildId` from the store homepage (cached 5 minutes, per region)
2. Pulls product data from 8 category endpoints via `/_next/data/{buildId}/...`, fetched concurrently over one pooled connection
3. Parses variant `status` fields (`Available`, `SoldOut`, `ComingSoon`)
4. Diffs the full catalog against the previous snapshot to detect transitions
5. Checks watched items against the catalog and fires notifications on availability

Both the GUI and the CLI drive everything from that one catalog fetch, so a cycle costs 8 requests whether you watch 1 item or 50.

Category slugs drift — cameras moved from `all-cameras-nvrs` to `all-physical-security` — so category fetches follow the store's redirects, and once a day the app reads the homepage's category list and adopts any new category that carries products the built-in list misses.

### Single-product lookups

Quick-check, and any watched item the category pages do not carry, go through `check_slug()` instead. That path has to handle a quirk of the store: for roughly half the catalog, `/products/{slug}.json` answers **HTTP 200 with a redirect directive in the body** —

```json
{"pageProps": {"__N_REDIRECT": "/us/en/category/.../products/ecs-48-poe", "__N_REDIRECT_STATUS": 307}}
```

— and occasionally a bare `307` carrying `x-nextjs-redirect` rather than `Location`, which `requests` will not follow on its own. The canonical page then contains the product's whole collection plus related and upsell items, so the requested product is resolved by slug, `historicalSlugs`, then `currentProductId`. Anything unresolvable raises `ProductNotFound` rather than reporting a guess.

No authentication, scraping, or rate-limit-busting — just standard JSON endpoints with polite polling intervals.

## Configuration Files

All config files are JSON and stored alongside the script:

**watched_items.json**
```json
[
  {
    "title": "Switch Pro 24",
    "slug": "usw-pro-24",
    "favourite": true,
    "price": "$379.00",
    "added_at": "2025-03-17T15:01:00"
  }
]
```

**settings.json**
```json
{
  "poll_interval": 60,
  "sound_alerts": true,
  "auto_open_url": true,
  "auto_start": false,
  "region": "us",
  "font_size": 10,
  "font_family": "Segoe UI",
  "bg": "#0d1117",
  "accent": "#1f6feb"
}
```

## Notes

- The store's `buildId` rotates periodically. The tool auto-detects this and refreshes with exponential backoff retry. A 404 only discards the cached id if a refetch confirms it actually rotated.
- Poll interval should be kept at 60s+ to be respectful to Ubiquiti's servers. The default is 60s.
- Notifications use PowerShell's `System.Windows.Forms.NotifyIcon` — works on all Windows 10/11 systems without extra dependencies. Titles and messages are passed through the environment, never interpolated into the script, so store- or import-supplied text cannot be executed.
- Imported watch lists are untrusted input: entries are validated, length-bounded, and reduced to known keys before being stored.
- The full catalog fetch (9 HTTP requests per cycle) is more efficient than individual product checks, and does not grow with the size of your watch list.

## Building the executables

```bash
pip install pyinstaller
python -m PyInstaller UnifiStockWatcher.spec --noconfirm --clean
```

Produces `dist/UnifiStockWatcher.exe` (windowed) and `dist/UnifiStockWatcher-cli.exe` (console). CI builds and attaches both to any `v*` tag.

## Development

```bash
pip install -r requirements.txt
pip install pytest
python -m pytest
```

The suite is offline — the store is faked with a stub session — so it runs anywhere in about ten seconds. GUI tests skip themselves if Tk has no display. CI runs Windows and Linux across Python 3.10–3.13.

```
tests/
├── conftest.py           # stub session, product/page builders, temp-file fixtures
├── test_store.py         # parsing, redirect handling, buildId cache
├── test_persistence.py   # atomic + UTF-8 JSON, watch list, history
├── test_notify.py        # notification text is data, not PowerShell
└── test_gui.py           # watcher state, browse dialog, widget lifecycle
```

## License

MIT License — see [LICENSE](LICENSE) for details.
