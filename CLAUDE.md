# UniFi Stock Watcher - notes for Claude Code

A Windows desktop app (Python, tkinter/ttk) that watches the Ubiquiti Store (store.ui.com) and alerts when a
product comes back in stock. It reads the store's public Next.js data: the whole catalog on every check (8
category pages), diffed against the previous check. A tray icon, Start with Windows, notifications, alerts on
your phone (webhooks), history, an installer and a portable zip, signed in-app updates. Also a headless CLI
watcher.

The repo is public on GitHub (`jamesccupps/UnifiStockWatcher`), with a page on GitHub Pages, a manifest in the
user's Scoop bucket, a SourceForge mirror and (planned) a winget package. Keep personal paths, the user's own watch list and machine settings out
of tracked files. Keep the README, `release-notes/`, CHANGELOG.md and this file current as work happens. Sibling
projects with the same conventions: Slate (Rust) and Protect Panes (`UnifiCameraViewer`, Electron), whose
release, update-signing and Pages setups this one follows.

Conventions across the user's repos (from the 2026-11-11 review):
- US spelling in the app's labels (3.2.5 on), the README, docs and the page. The data keys keep their old
  spelling: `favourite` in the watch list and its export, `alert_favourites_only` / `phone_favourites_only` in
  settings, and `favourite` in the JSON phone alerts send; renaming them would break existing files and Home
  Assistant automations. `tests/test_spelling.py` checks the app's string literals (docstrings and those keys
  aside). Identifiers and comments in the code weren't renamed.
- The repository's About description starts with what the app is (search results show ~60 characters). If the
  README's opening line changes, change it to match: `gh repo edit jamesccupps/UnifiStockWatcher --description "..."`.
- Example addresses in docs and tests come from the documentation ranges (192.0.2.0/24, 198.51.100.0/24,
  203.0.113.0/24), and example names are made up: never the user's own subnets, hostnames or places.
- Release titles are "UniFi Stock Watcher X.Y.Z" (`release.yml` does this).
- Session notes for Claude (handoffs, state files) stay out of the repository; this file is public on purpose.

## Commands

```
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest                     # offline suite (~15 s): the store is a stub session, GUI tests use one Tk root
python -m pytest tests_live          # contract tests against the real store (polite: one catalog fetch + 8 pages)
python -m pyflakes *.py scripts/*.py tests/*.py tests_live/*.py
python unifi_watcher_gui.py          # the app from source (uses the real %APPDATA% data, like an installed copy)
python scripts/demo.py               # the app against a pretend store (temp data folder, nothing real touched)
python scripts/demo.py --shots site\images   # the README/site screenshots (Windows; see "Pictures")
python scripts/build_windows.py      # dist\: folder, zip, installer (needs Inno Setup 6), latest.json (needs the key; --no-sign)
                                     #   --program / --package [--authenticode]: the release's two halves (see Releasing)
pwsh scripts/test_portable.ps1       # unzip dist's zip, check it's portable, run its self-test and the CLI
pwsh scripts/test_installer.ps1 -Version X.Y.Z   # install/self-test/uninstall dist's Setup.exe (replaces an installed copy!)
pwsh scripts/test_update.ps1 [-Kinds portable]  # the previous release updating itself to dist's build (installed: as above!)
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
- `webhooks.py`: alerts on your phone. `in_stock_alert()` / `price_drop_alert()`, `problem()` (what a
  service's settings lack), `build_request()` (each service's format), `send()` (retries), `Sender` (one thread,
  in order). The GUI and the CLI both use it.
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
- **The portable swap** (`updater.swap_in`) counts a name as placed *before* copying it, so a folder half-copied
  when it fails is removed and its `.old` renamed back; one that can't be is `SwapFailed`, and its `.old` is the
  only copy. A successful swap lists what it renamed in `.update-swapped.json`; `cleanup_after_update` removes
  those `.old` copies only, and runs only after `--updated` (`_after_update`), never `--update-failed`.
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
  finding the same product together alert once. Whether it alerts at all is `wants_alert()` (muted, favourites
  only, phone favourites only), asked in `_on_in_stock` on the main thread; a muted return still marks
  `notified`, so unmuting an in-stock product waits for its next return. Price changes are decided inside
  `StockHistory.record` (under its lock, so also once) and posted by `_recorded`. At start `notified` is seeded
  from `stock_history.last_state()` (`_seed_notified`; the CLI likewise): seeded empty, every launch alerted for
  everything already in stock.
- **The cycle** (`_cycle`): `fetch_all_products` raises `StoreError` when no category answers (an empty catalog
  read as "no products"); failed categories come back through `error_cb`. Not reaching the store, or a 429/503
  on a category, is `_store_trouble`: said once, "Can't reach the store" in the header, and `_watch_loop` backs off
  (interval × 2^failures, up to `MAX_BACKOFF`); a refusing store is never asked product by product. With
  categories missing, the baseline is merged, not replaced. Products are parsed defensively (`_variants`,
  `_objects`, numeric `amount` only) and each in its own try. `_store_gen` is bumped on a store change: a
  cycle or quick check that sees it change drops its results. Stop ends a watch-loop cycle at the next product;
  Start while it finishes waits for it (`_start_when_idle`). `_delisted` is slug → time: the catalog is looked
  at first, the product's own page again after `DELISTED_RECHECK`, and add/remove forget it.
- **Notifications close together merge** (`_notify`, `TOAST_MERGE`): the tray has one click slot and Windows
  doesn't say which toast was clicked, so a merged one opens the window.
- **Errors in Tk callbacks** go to `report_callback_exception` (`_callback_failed`: logged, and a dialog at most
  every 10 s): the windowed build has no stderr. `setup_logging` sets `threading.excepthook` too.
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
  A watched product's price change is an event with `price_was`, against the last price recorded (so across
  restarts); `None` and a different currency symbol aren't changes. The changes feed leaves them out. A check
  that changes nothing only bumps `total_checks` in memory: counters are saved every `COUNTS_SAVE_EVERY` and by
  `flush(final=True)` (quit, atexit), because marking the file dirty per check rewrote it (≈1 MB) every cycle. The
  file is written unindented; trimming to the cap drops store-wide events first.
- **Data files** go through `write_json`: a unique `mkstemp` temp name beside the file (the app and the CLI can
  write at once), fsync, `os.replace` retried for a second on `PermissionError` (antivirus, OneDrive).
- **Notifications** are the tray icon's balloons (Windows shows them as toasts under the app's name); a click
  runs that balloon's callback. Installed copies set the AppUserModelID `JamesCupps.UnifiStockWatcher`, which the
  installer also puts on the Start menu shortcut. The CLI keeps the PowerShell NotifyIcon, with title and text
  passed through the environment, never interpolated into the script.
- **Phone alerts never log or show an address.** Discord and Slack webhook URLs and ntfy topics *are* the
  secret, and the log goes into bug reports. Messages name `webhooks.label()` (service or host). requests'
  exception text quotes the full URL, so `_attempt` maps exceptions to its own wording; `_refusal` cuts the
  path out of a service's reply; `load_settings` names an invalid phone setting without its value.
- **ntfy gets JSON at the server's root** with `topic` in the body (JSON posted to the topic's address arrives
  as the message text). Redirects aren't followed (requests would turn the POST into a GET). Each service keeps
  its own keys in settings (`ntfy_url`, `discord_url`, …) so switching services loses nothing. The Settings
  entries save 700 ms after typing stops, on timers owned by the app (a rebuilt tab would orphan its own), and
  the test button saves first. `scripts/demo.py` replaces `http_post`: its stock flips would otherwise reach a
  real phone. Checked against the real ntfy.sh (title, click, actions, priority, tags all parsed); Pushover,
  Discord and Slack against their documented APIs only.
- **Start with Windows** writes `HKCU\...\Run\UniFi Stock Watcher` only when it has to change (Defender flags
  programs that keep rewriting Run keys). `refresh_autostart` repoints it at the running copy only when the
  program it names no longer exists, and only from `main()`: from the window's constructor, every self-test,
  `demo.py` run and test run took it over. Uninstall removes it. A Scoop copy names Scoop's `current` junction
  (`_frozen_exe`, `_scoop_current`), not the version folder `resolve()` leads to: 3.2.2's entry started the old
  version after `scoop update` (its mutex made the new one just show it) and nothing after `scoop cleanup`.
  `refresh_autostart` also moves an entry naming another version folder of the same Scoop app to `current`
  (3.2.3 on); entries of other copies that exist are still left alone.
- **Single instance** (`winplatform.SingleInstance`): one copy at a time, the app and the CLI alike (3.2.2 on), as
  the named mutex `JamesCupps.UnifiStockWatcher`, which Inno Setup waits on too; the CLI also holds
  `JamesCupps.UnifiStockWatcher.CLI`, so a refused copy can say which kind is running. A copy that finds the mutex
  taken keeps no handle to it and `wait_for_other()` polls: the tray window (class `UnifiStockWatcherTray`) →
  post `WM_SHOW_WINDOW` to it and exit; the CLI's mutex → say so and exit; the mutex gone → take it and start. A
  copy that's still starting holds the mutex seconds before its tray window exists, so the app waits up to
  `OTHER_COPY_WAIT` (30 s) for it; 3.2.1 started a second copy then. The CLI waits 5 s; `--test` and
  `--version` take no lock. Tests use their own mutex names (`tests/test_single_instance.py`).
- The tray's window class is registered once per process with one module-level window procedure; a
  per-instance ctypes callback would be called after its TrayIcon was collected.
- **Screenshots and the locked desktop**: on a locked or undisplayed desktop Windows sends no WM_PAINT for
  what moved, so a PrintWindow capture keeps stale pixels (blank labels, ghost copies of moved widgets).
  `scripts/demo.py` takes each picture in a fresh process, with the tab chosen before the window first appears,
  and invalidates everything, lets the event loop draw (Tk turns WM_PAINT into Expose events that
  `update_idletasks` doesn't process), then captures.

## Testing

- `tests/conftest.py` points `USW_DATA_DIR` at a temp folder before anything imports `unifi_core`, and blocks the
  network (`get_session` and `webhooks.http_post`): a test that reaches for the real store or a webhook fails. `isolated_files` redirects each data file per test.
- GUI tests share one Tk root (a second Tcl interpreter breaks Tcl's library paths intermittently); the `app`
  fixture resets state and calls `_rebuild_ui()`. It stubs `check_slug`, whose retries otherwise sleep 3 s
  against the blocked network and stall the next test's setup.
- `test_update.py` builds signed release folders with `cryptography` and runs the real download/verify/swap code.
- `tests_live/` runs nightly in CI (`contract.yml`) and opens an issue labeled `store-contract` when it fails.
- CI's `package` job builds the release files unsigned and runs `test_portable.ps1`, `test_installer.ps1` and
  `test_update.ps1`; the release workflow runs all three on the exact files before publishing.
- `test_update.ps1` is the real N → N+1 update, both kinds. N is the newest published release older than this
  build (`update_test_helper.py previous`, through the API with `GITHUB_TOKEN`): its zip unzipped or its
  Setup.exe installed, started, and then N's own `updater.py` (from N's tag's source, not this build's) is
  driven as its Install button would: check a scratch release folder holding dist\'s files and a `latest.json`
  signed with a throwaway key (patched in as N's `UPDATE_PUBLIC_KEY`), download, verify, `apply()`, and N is
  ended. It expects `update.log` "update: done" (portable) or Installed apps at the new version (installed),
  "Updated to X.Y.Z." in the new copy's log, one copy running, the watch list unchanged and no `.old` left. The
  frozen N isn't what runs the updater (it has no headless Install), but its files, mutex and process are real,
  and the new copy's swap or installer is. Locally: `py -3.13 scripts\build_windows.py --no-installer --no-sign`
  (without `--no-sign` a local build signs `dist\latest.json` with the real key: delete it after), then
  `powershell -File scripts\test_update.ps1 -Kinds portable -Python <py 3.13's python.exe>`.

## Releasing

1. Bump `__version__` in `unifi_core.py` (the release workflow refuses a tag that doesn't match; the updater
   compares it with the tag). Write `release-notes/vX.Y.Z.md` (the release page text) and the CHANGELOG entry.
2. `python -m pytest`, commit, push `main`, wait for CI (it builds and installer-tests the release files).
3. `git tag -a vX.Y.Z -m "UniFi Stock Watcher X.Y.Z"` and `git push origin vX.Y.Z`.

`.github/workflows/release.yml` then runs five jobs. **build** (Windows, no secrets): checks the tag, tests,
builds the program folder unsigned (`build_windows.py --program`) and uploads it (`program-build`).
**authenticode** (Windows, the `release` environment, the only job with `id-token: write`): signs in to Azure
with GitHub's OIDC token (`azure/login`), code-signs both exes as James Cupps through Azure Artifact Signing
(`scripts/authenticode.ps1`), then makes the zip and Setup.exe around them (`build_windows.py --package
--authenticode --no-sign`, Inno Setup pinned by `install_inno.ps1`, whose SignTool signs the uninstaller and
Setup.exe), and uploads the two (`release-build`). Nothing from PyPI runs there and the program never does.
**verify** (Windows, no secrets): the portable, installer and update tests on those exact files, the first two
with `-Publisher 'CN=James Cupps, O=James Cupps'` (`scripts/check_signatures.ps1`: every exe, the installed
uninstaller too, validly signed and timestamped). **sign** (Linux, the `release` environment, the only job
given `UPDATE_SIGNING_KEY`): installs only `requirements-sign.txt` (`--require-hashes --only-binary :all:`),
writes `latest.json` for the signed files with the key passed as text in `USW_SIGNING_KEY_PEM` (never written to
disk), verifies it against the key built into the app (`scripts/verify_release.py`), and writes
`SHA256SUMS.txt`. **publish** (the only job with `contents: write`): `sha256sum --check`, then the release with
`UnifiStockWatcher-Setup.exe`, `UnifiStockWatcher-win64.zip`, `latest.json` and `SHA256SUMS.txt`, all attached at
creation (SourceForge's GitHub sync doesn't see files added later). Installed copies find it through
`releases/latest/download/latest.json` and download from that version's tag. So PyInstaller and its unpinned
dependencies never run where the key or the code-signing identity is. CI's `sign` job rehearses the signing job
on every build with a throwaway key (`update_test_helper.py key`, `verify_release.py --public-key`).
`tests/test_build_windows.py` holds the workflow to this.

A tag `vX.Y.Z-signtest.N` (X.Y.Z = `__version__`) runs build, authenticode and verify and publishes nothing:
for trying a code-signing change. Delete it afterwards (`git push origin :refs/tags/<tag>`). The signature
checks' results are annotations (`::notice title=Signature::`), readable without signing in.

What the updater reads from a release can never change, as every installed copy reads it: the tag `vX.Y.Z` (no
suffix), those asset names, the manifest's shape (`updater.verify_manifest`), this repository. It never goes
back a version, so a bad release is fixed by publishing a higher one.

**What the Scoop bucket and winget rely on** (`bucket/unifistockwatcher.json` in `jamesccupps/scoop-bucket` updates
itself from each release, every 4 hours, so a release needs no Scoop work as long as these hold):
- Tag releases `vX.Y.Z` and publish them as the latest release, never a pre-release (`gh release create` without
  `--prerelease`, as `release.yml` does).
- Keep the file names, and the layout inside the zip, unchanged: `UnifiStockWatcher-win64.zip` holding
  `UnifiStockWatcher\UnifiStockWatcher.exe` (the bucket's shortcut), `UnifiStockWatcher\UnifiStockWatcher-cli.exe`
  (its `bin`) and `UnifiStockWatcher\portable.txt`, which the bucket deletes so a Scoop copy keeps its data in
  `%APPDATA%` (a moved or renamed marker would make it portable, its data in a version folder `scoop update`
  leaves behind).
- Keep publishing `SHA256SUMS.txt` beside them, in `sha256sum`'s format: the bucket reads the zip's hash from it.
- Sign before publishing (the `authenticode` and `sign` jobs do), with every file attached when the release is created. Never
  replace a file on a release that's out: Scoop and winget have recorded its hash. Fix it with a new version.
- If a file name or the zip's layout must change, change `bucket/unifistockwatcher.json` in
  `jamesccupps/scoop-bucket` in the same change.

**The signing key** (Ed25519):
- Private key: `%USERPROFILE%\.unifi-stock-watcher\update-signing-key.pem` on the development PC, and the same
  text in the environment secret `UPDATE_SIGNING_KEY` of the `release` environment (Settings → Environments;
  deployments limited to tags `v*.*.*`; no repository secret of that name, since 2026-11-06), so only a tag's
  signing job can read it. Never commit it; back it up offline. Without it, installed copies can't update in place (they'd need one
  manual install of a build with a new `update_key.py`).
- `scripts/make_signing_key.py` made it and refuses to replace it. A local `build_windows.py` signs with the
  file, so a release can be built by hand if CI is down: attach the same four files.

**Code signing** (Authenticode, from 3.2.4; earlier releases were unsigned): Azure Artifact
Signing, the account and certificate Slate, NoteForge and PingerPlot use (account `SlateAccount`, profile `Slate`,
endpoint `https://eus.codesigning.azure.net/`; subject `CN=James Cupps, O=James Cupps, ...`). The app registration
"Slate signing" has a federated credential for `repo:jamesccupps/UnifiStockWatcher:environment:release` (this
repository sends the classic subject format; checked 2026-11-10), and the `AZURE_CLIENT_ID`, `AZURE_TENANT_ID` and
`AZURE_SUBSCRIPTION_ID` secrets name it. No password or key is stored. Four signatures a release (both exes, the
uninstaller, Setup.exe). It sits beside the Ed25519 update signature, not instead of it.

Workflow actions are pinned to commits (version in a comment); Dependabot proposes newer ones monthly, and newer
pins for `requirements-dev.txt` and `requirements-sign.txt` (hashes included; keep its `cryptography` at
`requirements-dev.txt`'s). Inno Setup is pinned by hand in `scripts/install_inno.ps1` (version and SHA-256 from
jrsoftware/issrc's release). Checkouts keep no token (`persist-credentials: false`); only the release's publish
job can write. Security reports: SECURITY.md (GitHub's private reporting).

## Pages, SourceForge, winget, Scoop

- **Pages**: `pages.yml` publishes `site/` plus `assets/icon.png` when they change on `main` (Settings → Pages →
  Source: GitHub Actions). The page fetches the latest version from the GitHub API and links
  `releases/latest/download/...`, so a release needs no deploy. `site/images/social-preview.png` (1280x640) is
  also the repository's Settings → Social preview, which the user uploads by hand. After each deploy the workflow
  pings IndexNow (Bing and others re-crawl now; the key is public, served from the root of jamesccupps.github.io;
  a failed ping is only a warning). Every page of the user's keeps its search tags (title ≤ 60 characters,
  description ≤ 160, canonical URL, `og:site_name`, `og:image` alt and size, `theme-color`, width and height on
  images) and ends with the "More tools from James Cupps" section (`.jc-more-grid`) linking the sibling tools and
  the hub: keep both when editing `site/index.html`.
- **SourceForge** (`unifistockwatcher`) mirrors GitHub's release files, never the other way round:
  `packaging/sourceforge/README.md` has the setup, the listing text, categories, screenshot captions and the
  logo. Unlock the project menu (lock icon), gear next to **Files** → **GitHub Integration**, with
  `jamesccupps/UnifiStockWatcher`: each new release's files are imported after it's published. A release
  published before that is re-checked when its text is edited on GitHub; files added to a release afterwards are
  never seen. Choose the new Setup.exe as the Windows default download after each release. The in-app updater
  only ever reads GitHub.
- **winget**: `scripts/winget_manifest.py X.Y.Z` writes the three manifest files for `JamesCupps.UnifiStockWatcher`
  (InstallerType inno, Scope user, ProductCode the AppId + `_is1`) into `build/winget/`, from the published
  release's Setup.exe. Validate with `winget validate --manifest <folder>`, then open a PR to microsoft/winget-pkgs
  from the user's fork `jamesccupps/winget-pkgs` (as for Slate). The app updates itself, so a winget PR per release
  only matters for new installs and `winget upgrade`. Without `gh`: sparse-clone the fork
  (`git clone --depth 1 --filter=blob:none --sparse`, `git sparse-checkout set manifests/j/JamesCupps`), commit the
  folder on a branch `JamesCupps.UnifiStockWatcher-X.Y.Z`, push it, and open the PR from the compare page (needs
  the user signed in); a stale fork master is fine, the PR is only the new folder. The body follows the repo's
  PR template. The CLA bot asks on every PR (labels it `Needs-CLA`) even though the user has signed: the user
answers `@microsoft-github-policy-service agree` there. Validation takes about 45 minutes, then a moderator
approves. First submission: 3.2.0, #470772.
- **Scoop**: the user's bucket `jamesccupps/scoop-bucket` (`scoop bucket add jamesccupps
  https://github.com/jamesccupps/scoop-bucket`, `scoop install jamesccupps/unifistockwatcher`), in the README's
  Install and on the page's hero. It installs the zip without `portable.txt` (install kind `folder`: data in
  %APPDATA%, the in-app updater swaps in place), with the app in the Start menu and the CLI on PATH. The bucket's
  checkver/autoupdate does the rest; the release rules above keep it working.

## Pictures

`site/images/*.png` come from `python scripts/demo.py --shots site\images` (Windows; PrintWindow, so it works while
covered): the real app against `scripts/demo.py`'s sample catalog (real product names, made-up stock, prices and
dates). Never the user's own watch list. The README uses `site/images/watch-list-dark.png`.
`scripts/make_social_preview.py` composes `social-preview.png` from them (needs Pillow). They come out at the
display's scale: 3.2.5's were taken at 100% (820x790; Add products 900x650), earlier ones at 150%. The page's
`<img>` width and height must match the files, and a new social preview has to be uploaded by hand.
