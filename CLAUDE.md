# UniFi Stock Watcher - notes for Claude Code

A Windows desktop app (Python, tkinter/ttk) that watches the Ubiquiti Store (store.ui.com) and alerts when a
product comes back in stock. It reads the store's public Next.js data: the whole catalog on every check (8
category pages), diffed against the previous check. A tray icon, Start with Windows, notifications, history,
an installer and a portable zip, signed in-app updates. Also a headless CLI watcher.

The repo is public on GitHub (`jamesccupps/UnifiStockWatcher`), with a page on GitHub Pages and (planned) a
SourceForge mirror and a winget package. Keep personal paths, the user's own watch list and machine settings out
of tracked files. Keep the README, `release-notes/`, CHANGELOG.md and this file current as work happens. Sibling
projects with the same conventions: Slate (Rust) and Protect Panes (`UnifiCameraViewer`, Electron), whose
release, update-signing and Pages setups this one follows.

## Commands

```
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest                     # offline suite (~15 s): the store is a stub session, GUI tests use one Tk root
python -m pytest tests_live          # contract tests against the real store (polite: one catalog fetch + 8 pages)
python -m pyflakes *.py scripts/*.py tests/*.py tests_live/*.py
python unifi_watcher_gui.py          # the app from source (uses the real %APPDATA% data, like an installed copy)
python scripts/demo.py               # the app against a pretend store (temp data folder, nothing real touched)
python scripts/demo.py --shots site\images   # the README/site screenshots (Windows; see "Pictures")
python scripts/build_windows.py      # dist\: folder, zip, installer (needs Inno Setup 6), latest.json (needs the key)
pwsh scripts/test_portable.ps1       # unzip dist's zip, check it's portable, run its self-test and the CLI
pwsh scripts/test_installer.ps1 -Version X.Y.Z   # install/self-test/uninstall dist's Setup.exe (replaces an installed copy!)
python scripts/make_icons.py         # redraw assets\*.ico / icon.png (committed; builds never run this)
```

Run the suite after any change. Python on the development PC: `py -3.13` (the `python` on PATH there is
Inkscape's 3.11). Never start the app from a Claude session expecting it to persist: children of the Claude
desktop app live in its container and die with it. `UnifiStockWatcher.exe --self-test` with `USW_DATA_DIR` set
to an empty folder is the safe way to run a build.

## Layout

- `unifi_core.py`: everything that isn't UI. Paths (`BASE_DIR`, portable detection, 2.x migration, logging),
  settings (validated on load), the store API (`get_build_id`, `fetch_all_products`, `check_slug`, category
  discovery), restock phrases, watch list persistence and import/export, `StockHistory`, the PowerShell balloon
  the CLI uses. `__version__` is the single version string.
- `unifi_watcher_gui.py`: the app. `UnifiWatcherApp` (window, tabs, watcher thread, alerts, updates, tray
  wiring), `WatchedRow` (a card), `BrowseDialog` (the catalog), `HistoryTab`, `SettingsTab`, `main()` (arguments,
  single instance, `--self-test`, `--finish-update`).
- `unifi_watcher.py`: the CLI watcher.
- `theme.py`: palettes, fonts, scale, the generated element images and every ttk style. `gfx.py`: signed-distance
  drawing, PNG/ICO encoding, `expand9`, the app icon.
- `tray.py`: Shell_NotifyIconW on its own thread. `winplatform.py`: DPI, title bar colours, theme/accent from the
  registry, Start with Windows, the single-instance mutex.
- `updater.py`: manifest verification, check, download, install (installer or portable swap). `ed25519.py`:
  verification only. `update_key.py`: the public key.
- `installer/UnifiStockWatcher.iss`, `UnifiStockWatcher.spec`, `scripts/`: building and releasing.
- `site/index.html` (+ `site/images/`): the project page. `release-notes/vX.Y.Z.md`: each release's text.
- Data (never in the repo): `%APPDATA%\UniFi Stock Watcher\` or beside a portable exe: `watched_items.json`,
  `settings.json`, `stock_history.json` (version 2), `category_cache.json`, `unifi-stock-watcher.log`,
  `update.log`.

## Design decisions and gotchas

- **Install kinds** (`updater.install_kind()`): `source` (not frozen), `portable` (`portable.txt` beside the exe:
  data there too), `installed` (Inno's `unins000.exe` beside it), `folder` (frozen, neither: data in %APPDATA%,
  updates like portable). The zip carries `portable.txt`; the installer's folder must not (build_windows.py and
  test_installer.ps1 check). The portable swap never copies `portable.txt` into a folder that didn't have it.
- **2.x migration** copies (never moves, never overwrites) 2.x files from beside the program into %APPDATA% once,
  then reloads `stock_history`, which was loaded at import: without the reload its first flush would write an
  empty history over the copied one.
- **Store quirks** (README "How it works"): redirect directives in 200 bodies, bare 307s with
  `x-nextjs-redirect`, renamed categories, `$0.00` meaning "no price yet", products found by slug, then
  `historicalSlugs`, then `currentProductId`. `check_slug` raises rather than guess. Slugs are validated
  (`is_valid_slug`) before they go into any URL; `product_url()` falls back to the store's home.
- **Threads and Tk.** Workers (watcher, quick check, catalog fetch, update) never call Tk. `_post()` puts work on
  a queue that `_drain()` runs every 40 ms on the main thread. A Tk call from another thread blocks until the
  main loop polls (every 20 ms in `_tkinter`): a cycle that posted with `after()` per row spent seconds queueing,
  and posts after close raised. Tray callbacks run on the tray thread and also go through `_post`.
- **Alert once**: `_maybe_alert` marks `notified` on the worker before posting, so a quick check and a cycle
  finding the same product together alert once.
- **ttk image elements tile, they don't stretch.** A 9-slice image with a few-pixel middle made one card ~1000
  alpha-blended blits per redraw, and the Settings page took seconds per style change. `Theme._put(..., grow=)`
  draws small and widens the uniform middle with `gfx.expand9` (96-240 px). Keep any new sliced image grown.
- **Never-mapped subtrees are slow.** Tk re-lays out a hidden, never-shown subtree on every idle pass; with the
  Settings tab built up front, any style change or new dialog cost ~2 s. History and Settings are built the
  first time they're shown, and a theme change rebuilds only what exists.
- **Theme changes rebuild the UI** (`_rebuild_ui`) from the app's state (rows' status cache, log and changes
  deques, watching state); `update_idletasks()` first, or widgets built before ttk's style-change idle handler can
  stay undrawn. A rebuild must restore anything that reflects a running watcher (the Start/Stop button).
- **Transparent pixels in ttk image elements show the style's `background`**, so each style that sits on a card
  has a `Card.` twin with the card's colour (`Card.TLabel`, `Card.Subtle.TButton`, …). Rounded cards are
  `Tile.TFrame` (`Hot.Tile.TFrame` in stock); `Card.TFrame` is the flat card surface inside one.
- **clam gives every TButton `-width -11`** (11 characters minimum). Icon buttons set `width=0`.
- **DPI**: system-aware (`SetProcessDpiAwareness(1)`), not per-monitor: Tk 8.6 can't relayout on a monitor
  change. Sizes go through `Theme.S()`; fonts are in points and scale by themselves.
- **History** logs changes only, plus a watched product's first state (`first: true`, shown as "Added: …").
  Store-wide changes (`watched: false`) are never "first". `alerts` counts notifications, not in-stock checks.
- **Notifications** are the tray icon's balloons (Windows shows them as toasts under the app's name); a click
  runs that balloon's callback. Installed copies set the AppUserModelID `JamesCupps.UnifiStockWatcher`, which the
  installer also puts on the Start menu shortcut. The CLI keeps the PowerShell NotifyIcon, with title and text
  passed through the environment, never interpolated into the script.
- **Start with Windows** writes `HKCU\...\Run\UniFi Stock Watcher` only when it has to change (Defender flags
  programs that keep rewriting Run keys), and points it at the running copy if it moved. Uninstall removes it.
- **Single instance**: the named mutex `JamesCupps.UnifiStockWatcher`; a second copy finds the tray window
  (class `UnifiStockWatcherTray`) and posts `WM_SHOW_WINDOW` to it. Inno Setup waits on the same mutex.
- The tray's window class is registered once per process with one module-level window procedure; a
  per-instance ctypes callback would be called after its TrayIcon was collected.
- **Screenshots and the locked desktop**: on a locked or undisplayed desktop Windows sends no WM_PAINT for
  what moved, so a PrintWindow capture keeps stale pixels (blank labels, ghost copies of moved widgets).
  `scripts/demo.py` takes each picture in a fresh process, with the tab chosen before the window first appears,
  and invalidates everything, lets the event loop draw (Tk turns WM_PAINT into Expose events that
  `update_idletasks` doesn't process), then captures.

## Testing

- `tests/conftest.py` points `USW_DATA_DIR` at a temp folder before anything imports `unifi_core`, and blocks the
  network: a test that reaches for the real store fails. `isolated_files` redirects each data file per test.
- GUI tests share one Tk root (a second Tcl interpreter breaks Tcl's library paths intermittently); the `app`
  fixture resets state and calls `_rebuild_ui()`. It stubs `check_slug`, whose retries otherwise sleep 3 s
  against the blocked network and stall the next test's setup.
- `test_update.py` builds signed release folders with `cryptography` and runs the real download/verify/swap code.
- `tests_live/` runs nightly in CI (`contract.yml`) and opens an issue labelled `store-contract` when it fails.
- CI's `package` job builds the release files unsigned and runs `test_portable.ps1` and `test_installer.ps1`.
- Not automated yet: a frozen N → N+1 update. To check one by hand after touching `updater.py` or the .iss:
  build N+1 into a scratch release folder, run N with `USW_UPDATE_URL=<that folder>` and `USW_DATA_DIR` set to a
  scratch folder, click Install, and expect `update.log` ending "update: done" (portable) or the new version in
  Installed apps (installer), then the relaunched copy finding nothing to install. Delete the scratch release
  folder afterwards: it's validly signed.

## Releasing

1. Bump `__version__` in `unifi_core.py` (the release workflow refuses a tag that doesn't match; the updater
   compares it with the tag). Write `release-notes/vX.Y.Z.md` (the release page text) and the CHANGELOG entry.
2. `python -m pytest`, commit, push `main`, wait for CI (it builds and installer-tests the release files).
3. `git tag -a vX.Y.Z -m "UniFi Stock Watcher X.Y.Z"` and `git push origin vX.Y.Z`.

`.github/workflows/release.yml` then: checks the tag, tests, writes the `UPDATE_SIGNING_KEY` secret to a temp file,
builds the folder, zip, Setup.exe and the signed `latest.json`, deletes the key, verifies `latest.json` against
the key built into the app (`scripts/verify_release.py`), runs the portable and installer tests on those exact
files, and publishes the release with `UnifiStockWatcher-Setup.exe`, `UnifiStockWatcher-win64.zip`,
`latest.json` and `SHA256SUMS.txt`, all attached at creation (SourceForge's GitHub sync doesn't see files added
later). Installed copies find it through `releases/latest/download/latest.json` and download from that version's
tag.

What the updater reads from a release can never change, as every installed copy reads it: the tag `vX.Y.Z` (no
suffix), those asset names, the manifest's shape (`updater.verify_manifest`), this repository. It never goes
back a version, so a bad release is fixed by publishing a higher one.

**The signing key** (Ed25519):
- Private key: `%USERPROFILE%\.unifi-stock-watcher\update-signing-key.pem` on the development PC, and the same
  text in the repository secret `UPDATE_SIGNING_KEY`. Never commit it; back it up offline. Without it, installed
  copies can't update in place (they'd need one manual install of a build with a new `update_key.py`).
- `scripts/make_signing_key.py` made it and refuses to replace it. A local `build_windows.py` signs with the
  file, so a release can be built by hand if CI is down: attach the same four files.
- Not Authenticode: the exe isn't code-signed (SmartScreen shows "Run anyway"); the user chose the Protect Panes
  scheme over Slate's Azure Artifact Signing.

Workflow actions are pinned to commits (version in a comment); Dependabot proposes newer ones monthly, and newer
pins for `requirements-dev.txt`. Checkouts keep no token (`persist-credentials: false`); only the release job can
write. Security reports: SECURITY.md (GitHub's private reporting).

## Pages, SourceForge, winget

- **Pages**: `pages.yml` publishes `site/` plus `assets/icon.png` when they change on `main` (Settings → Pages →
  Source: GitHub Actions). The page fetches the latest version from the GitHub API and links
  `releases/latest/download/...`, so a release needs no deploy. `site/images/social-preview.png` (1280x640) is
  also the repository's Settings → Social preview, which the user uploads by hand.
- **SourceForge** mirrors GitHub, never the other way round: on the SourceForge project, Admin → Files settings →
  GitHub Integration, with `jamesccupps/UnifiStockWatcher`; new GitHub releases (and their files) are imported
  automatically. Older releases need the one-time GitHub Importer. The in-app updater only ever reads GitHub.
- **winget**: `scripts/winget_manifest.py X.Y.Z` writes the three manifest files for `JamesCupps.UnifiStockWatcher`
  (InstallerType inno, Scope user, ProductCode the AppId + `_is1`) into `build/winget/`, from the published
  release's Setup.exe. Validate with `winget validate --manifest <folder>`, then open a PR to microsoft/winget-pkgs
  from the user's fork `jamesccupps/winget-pkgs` (as for Slate). The app updates itself, so a winget PR per release
  only matters for new installs and `winget upgrade`.

## Pictures

`site/images/*.png` come from `python scripts/demo.py --shots site\images` (Windows; PrintWindow, so it works while
covered): the real app against `scripts/demo.py`'s sample catalog (real product names, made-up stock, prices and
dates). Never the user's own watch list. The README uses `site/images/watch-list-dark.png`.
`scripts/make_social_preview.py` composes `social-preview.png` from them (needs Pillow).
