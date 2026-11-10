# Changelog

Each release's own notes (what users see on the release page) are in `release-notes/`.

## Unreleased

### Security

- **The Windows downloads are code-signed by James Cupps** (Azure Artifact Signing, timestamped):
  `UnifiStockWatcher.exe` and `UnifiStockWatcher-cli.exe` in both downloads, the installer and the uninstaller,
  so Windows names the publisher instead of "Unknown publisher". The release signs in a job of its own that runs
  nothing from PyPI and never runs the program, then tests the signed files (every signature checked, the
  installed uninstaller's too) before `latest.json` is signed and anything is published. The Ed25519 update
  signature is unchanged.

## v3.2.3

Start with Windows on a Scoop install.

### Fixed

- **Start with Windows on a Scoop install named the version folder** (`apps\unifistockwatcher\3.2.2`), not
  Scoop's `current` junction, because `autostart_command()` resolved the exe's path, which follows the junction.
  After `scoop update`, sign-in kept starting the old version, which holds the single-instance mutex, so
  starting the new one only showed the old one; after `scoop cleanup`, nothing started at sign-in, and the next
  start wrote the new version's folder, so the next update broke it again. A `current` link beside the copy's
  folder, leading to a sibling folder that holds the same exe, is now used instead (a sibling, not only this
  folder: the in-app updater writes into the folder it ran from, which `current` may since have left).
  `refresh_autostart()` moves an entry that names another version folder of the same Scoop app over to
  `current` the first time 3.2.3 starts; entries of other copies (portable, installed, a self-test) that exist
  are still left alone, and the registry is still written only when the value changes.

## v3.2.2

One copy at a time, the command-line watcher included.

### Fixed

- **The app and the command-line watcher could run together**, and alert twice for every product. The CLI took
  no lock, so the installer and the updater, which wait for the app's, could also replace the program's files
  under a running CLI. Both now hold the same mutex (`winplatform.SingleInstance`); the CLI also holds a marker
  mutex, so whichever starts second says which kind is running and exits: the CLI with code 1, the app with a
  message, unless the other copy is the app, whose window it shows as before. `--test` and `--version` take no
  lock. The installer's "is running" message mentions Ctrl+C for the CLI.
- **A second copy started while the first was still loading ran beside it.** The first holds the mutex a few
  seconds before its tray window exists, and the second, finding no window, started anyway. It now waits up to
  30 seconds for the window (or for the first copy to exit, and then starts). A copy that holds the lock and
  never answers gets a message saying so, instead of a second copy.
- The 2.x migration runs only in the copy that has the lock.

### Development

- **Releases are signed in a job of their own.** `release.yml` is three jobs: *build* (tests, the unsigned
  folder, zip and installer, and the portable, installer and update tests on them), *sign* (a fresh Linux
  runner in the `release` environment that installs only `requirements-sign.txt`, hash-pinned `cryptography`
  wheels, signs `latest.json` for the build's files and checks it against the app's key) and *publish* (the only
  job that can write, after checking the files against `SHA256SUMS.txt`). The release key was on disk while
  PyInstaller and its unpinned dependencies ran; now it reaches only the signing script, through the
  environment, and is never written to a file.
- CI rehearses the signing job on every build with a throwaway key (`update_test_helper.py key`,
  `verify_release.py --public-key`), so an update to `requirements-sign.txt` is tried before a release needs it.
- Inno Setup is pinned (6.7.4, by SHA-256, `scripts/install_inno.ps1`) in CI and the release, instead of
  whichever version the runner image has. `build_windows.py --no-sign`.

## v3.2.1

Fixes from a review of 3.2.0. Each has a test that fails on 3.2.0.

### Fixed

- **Every launch alerted again** for each watched product already in stock (notification, sound, browser tab,
  phone): `notified` started empty. It's now seeded from the history's last state (`StockHistory.last_state`), in
  the app and the CLI; a product that came back while the app was closed still alerts.
- **A product that 404'd once was skipped for the session**, even after it was back in the catalog. The catalog
  is now looked at first; a delisted product's own page is asked again after 6 hours; removing or re-adding it
  forgets it.
- **One product in an unexpected shape failed the whole check** (`"variants": null`, a variant that isn't an
  object, a text `amount`), and ended the CLI. Products are read defensively and one that still fails is skipped
  and logged once; the CLI catches errors per product and per check.
- **A store that answered no category** read as an empty catalog, "No changes across 0 products", and every
  watched product was asked for on its own page, 3 tries each, every cycle. `fetch_all_products` raises
  `StoreError` when every category fails. The app says so once ("Can't reach the store" in the header), and backs
  off, doubling the wait up to 30 minutes, also when the store answers 429 or 503; then it's asked product by
  product no more. A category that fails keeps its products' last state in the baseline. The CLI backs off too.
- **A portable update that failed half-way through `_internal`** left the half-copied folder, so the old one
  couldn't be put back, and the restarted old version's cleanup then deleted `_internal.old`. The rollback now
  removes what it half-copied, says if anything couldn't be restored (`SwapFailed`), and the cleanup runs only
  after a successful update and removes only the `.old` copies that swap made (`.update-swapped.json`).
- **The history file was rewritten every check** (about 1 MB once full, with an fsync): the check counter marked
  it dirty. Counters are saved every 10 minutes and on exit; events as before. The file is no longer indented, and
  the 5,000-event cap drops store-wide changes before the watched products' history.
- **Any other copy that started took over Start with Windows** (a portable copy tried once, a self-test in a temp
  folder, the test suite): it's repointed only when the program it names no longer exists, and only from a real
  start.
- **Errors in button handlers vanished** in the frozen app (no console), e.g. exporting to a file open in Excel.
  They're logged and shown; both exports catch their own errors; a settings save that fails is said and the change
  still applies.
- Changing the store during a check stored the old store's catalog as the new baseline and could alert "available
  on the Europe store" from it: a check that sees the store change drops its results.
- With two notifications up, clicking the first opened the second product (Windows doesn't say which was
  clicked): alerts within 8 seconds now share one notification, which opens the window.
- Start while a stopped check was finishing did nothing; Stop now ends the check at the next product, and Start
  starts as soon as it has. Check now says when a check is already running.
- Touchpad scrolling: small scrolls up did nothing and small scrolls down a whole line; deltas now add up.
- The default window (and Add products) could be taller than the screen at 150-200%.
- Restock and sold-out days were counted in UTC, so "back ~tomorrow" could be off by a day in the evening; a bare
  restock date keeps its calendar day.
- The CSV export had no byte-order mark, so Excel garbled € and £.
- Smaller: `Retry-After: nan` from a webhook ended its retries; `^…$` validation accepted a trailing newline
  (slugs, the accent colour, the update manifest's fields): `fullmatch`; data files are written through unique
  temp names, and the rename is retried while antivirus holds the file; the CLI's notification runs Windows'
  own `powershell.exe`, not the first on PATH; the frozen CLI never tries to pip-install; uncaught thread errors
  are logged.
- Light theme: faint text (every Settings description, placeholders) is 4.5:1 (was 2.5:1), dark 4.5:1 (3.4:1);
  the light favourite star 3:1.
- Docs: the 3.2.0 entry said the tray icon "turns red" (it gets the in-stock dot); the README said the home page
  is read once a day (every 5 minutes, for the build ID) and that the CSV export saves everything (what's shown).

### Development

- `scripts/test_update.ps1`, in CI and before every release: the newest published release, portable and
  installed, updates itself to the build with its own updater code (from its tag), against a release folder
  signed with a throwaway key. Replaces the manual N → N+1 check.

## v3.2.0

### Added

- Per-product alerts. *Mute alerts* on a card's menu (`muted` in the watch list, carried by export and import)
  silences one product: still checked, still in the history, no notification, sound, page or phone alert. A
  crossed-out bell on the card unmutes it. **Only for favourites** in Alerts does the same for every product
  that isn't starred, and the one in Alerts on your phone for the phone only. `unifi_core.wants_alert()` decides,
  for the app and the CLI. The tray icon gets its in-stock dot only for an in-stock product that alerts.
- Price changes. `StockHistory.record` logs a watched product's price change (`price_was` beside the new
  `price`) against the last price it recorded, so a change while the app was closed counts. No price isn't a
  change, nor is another currency (the store was switched), nor a store-wide product's price. History shows
  them as *Price down* / *Price up*, the CSV export has a `price_was` column, and the Activity view logs each.
  **Tell me when a price drops** (off by default) sends a notification and a phone alert (`price_drop` in the
  JSON, priority 3 on ntfy), subject to mute and favourites-only. `record` returns the event logged (or None)
  instead of True/False; `record_check` still returns a bool.

## v3.1.1

### Fixed

- A data file starting with a UTF-8 byte-order mark (Windows PowerShell's `Set-Content -Encoding utf8`, some
  editors) was read as unreadable: settings fell back to their defaults, the watch list to empty, an import
  failed, and the next save replaced the file. `read_json` reads `utf-8-sig`. Found by the 3.0.0 → 3.1.0
  portable update test, whose hand-written watch list had one.

## v3.1.0

### Added

- Alerts on your phone (`webhooks.py`): each in-stock alert also goes to ntfy, Pushover, Discord or Slack, in
  that service's own format, or as JSON to any address. Settings has the service, its address or keys (each
  service keeps its own), and a test that shows what the service answered. Sent in order on a thread of its
  own; no connection, timeouts, 429 and 5xx are retried twice. The Activity view logs each one. Reasons and
  log lines name the service or host, never the address, which holds the secret for Discord, Slack and ntfy.
- The CLI sends them too, from the same settings; `--test` sends a test.

## v3.0.0

### Added

- A new interface on ttk, drawn by the app itself (`theme.py`, `gfx.py`): Windows 11-style light and dark
  themes following Windows or set by hand, any accent colour (Windows' by default), text size, DPI-correct
  layout. Replaces 2.x's five presets, eight colour pickers and font list.
- The notification area (`tray.py`, Shell_NotifyIcon through ctypes): close to tray, an icon showing idle /
  watching / in stock, a menu, and notifications that open the product when clicked.
- Start with Windows, single instance (a second launch shows the first), remembered window position.
- An installer (Inno Setup, per user, no admin) and a portable zip, from a one-folder PyInstaller build with
  version information; `scripts/build_windows.py`.
- Signed in-app updates (`updater.py`): an Ed25519-signed `latest.json` per release, verified by a
  dependency-free `ed25519.py` against the key in `update_key.py`; installed copies run the new installer
  silently, portable copies swap their program files in place with rollback.
- The catalog dialog is a Treeview with check boxes and sortable columns; 600 products render in well under a
  second.
- History records changes, watched or store-wide, with friendly times, a filter and CSV export.
- `--self-test` for the frozen build, used by CI on the zip and on the installed copy.
- `scripts/demo.py`: the app against a pretend store, and the screenshots for the README and the site.
- A project page on GitHub Pages, SECURITY.md, issue forms, Dependabot, THIRD-PARTY-NOTICES.md, CLAUDE.md and
  ROADMAP.md. Workflow actions are pinned to commits.

### Changed

- Data lives in `%APPDATA%\UniFi Stock Watcher` (or beside a portable copy); 2.x files are copied there once.
- Settings are validated on load; an invalid value falls back to its default instead of breaking start-up.
- Settings apply as they change; there is no Apply button.
- The history file is version 2: 2.x files are converted (repeated states collapsed, the miscounted
  "in-stock alerts" dropped).
- Worker threads never call Tk: results go through a queue the main loop drains.
- The daily category check runs from every cycle, not only when watching starts.

### Fixed

- Statuses on every card were lost when one item was added or removed.
- A quick check and a cycle finding the same product in stock at once could alert twice.
- Imported product ids were not validated before going into store URLs.
- The 2.x window was laid out in physical pixels, so at 150 % scaling the log and row buttons were cut off;
  read-only dropdowns were unreadable.

## v2.1.0

### Added

- Restock estimates. The store sends `restockEtaAt` and `soldOutAt` on every
  catalog fetch and the app discarded them; a little over half of out-of-stock
  products carry at least one. Watched rows and the browse list now show
  e.g. `back ~7 Sep  ·  sold out 13 days`.

- Automatic category discovery. The store's category list is read from the
  homepage once a day; a category that appears and carries products the
  built-in list misses is adopted and reported. Sub-views that only repeat a
  parent's products are recorded as redundant so they are not re-fetched -
  adopting all of them would double the per-cycle request count for nothing.
- A nightly CI job (`tests_live/`) checks the real store contract and opens an
  issue when it breaks.
- Standalone Windows executables, built and attached to each `v*` tag. No
  Python install required. They are portable - runtime files sit next to the
  executable.
- `--version` on the CLI, which also prints the config directory.

### Fixed

- **Cameras and NVRs were not being watched at all.** Ubiquiti renamed
  `all-cameras-nvrs` to `all-physical-security`, and `_fetch_category` did not
  follow redirects, so the endpoint answered HTTP 200 with a redirect
  directive and no products. 13 out-of-stock cameras and NVRs were invisible,
  including the G6 Edge range, G4 Doorbell Pro and NVR G2 Pro. The catalog
  goes from 421 to 487 products.
- Announced-but-unreleased products showed `$0.00`, which reads as free. Zero
  is the store's "no price yet", so it now shows no price and the item is
  labelled `coming soon` rather than sold out.
- Mouse wheel scrolling did nothing. The previous fix for the bind_all leak
  installed the binding on `<Enter>` and removed it on `<Leave>` of the
  container, and moving onto a child row fires `<Leave>` on the container -
  so it was torn down exactly where scrolling happens.

## v2.0.0

A correctness release. Stock detection was wrong for a large part of the
catalog, in ways that produced no error and no visible symptom — the watcher
simply never fired.

### Breaking

- `check_slug()` raises `ProductNotFound` or `StoreError` instead of returning
  a fabricated `(False, None)`. Callers that treated its result as
  authoritative were being told "out of stock" for products that were in stock.
- `invalidate_build_id()` takes an optional `region`; the cache is keyed per
  region.
- `StockHistory` buffers writes. Call `flush()` before exit — `atexit` and the
  GUI's close handler already do.

### Fixed

- **PowerShell command injection in notifications.** Title and message were
  interpolated into the script with only quote escaping, and PowerShell expands
  `$(...)` inside double-quoted strings. A product title — or an imported watch
  list, which the README encourages sharing — could execute arbitrary commands.
  Both strings now reach PowerShell through the environment, as data.
- **Stock reported for the wrong product.** Variants were found by walking the
  JSON for the first `variants` key. A product page carries its whole collection
  plus related and upsell items, so `ua-g2` returned `ua-g3`'s stock.
- **Permanent false "out of stock".** For roughly half the catalog the store
  answers `/products/{slug}.json` with HTTP 200 and a `__N_REDIRECT` body, or a
  bare 307 carrying `x-nextjs-redirect` instead of `Location`. Neither was
  followed. Verified against catalog truth: a 40-product sweep went from
  10 agree / 10 mismatch to 40 / 0.
- **The CLI never alerted for those products at all**, since it routed every
  check through that path.
- A missing `requests` produced `NameError: name 'requests' is not defined` on
  every store call — the install ran after the failed import, binding the name
  in the wrong namespace.
- Browse dialog errors were invisible: a deferred lambda closed over an
  `except ... as ex` name that Python unbinds, so the dialog sat on
  "Fetching…" forever.
- Mouse-wheel scrolling used `bind_all`, so the browse dialog's binding
  replaced the main list's and outlived its own canvas. After one browse the
  watch list stopped scrolling for the session.
- Applying settings while watching left the button reading "Start Watching"
  with the watcher still running — so the next click stopped it.
- Changing region kept the previous store's baseline, reporting most of the
  catalog as changed on the next cycle.
- A delisted item was re-checked with backoff every cycle, forever. It is now
  reported once and shown as `DELISTED`.
- Watch list, settings, and history writes were non-atomic and locale-encoded;
  an interrupted write truncated the file, and non-ASCII titles round-tripped
  as mojibake on Windows.
- Imported watch lists were stored verbatim. Entries are now validated,
  length-bounded, and reduced to known keys.
- `install_and_run.bat` ignored choice 2 and started the CLI watcher on any
  typo or bare Enter.
- Closing the browse dialog or main window mid-fetch left worker threads
  scheduling onto destroyed widgets.

### Performance

| | v1.1.0 | v2.0.0 |
|---|---|---|
| Catalog fetch | 4.12s | 0.71s |
| Notification dispatch | 3.27s, blocking the UI thread | 0.04s for four |
| Browse list re-render (421 products) | 1.253s | 0.104s |
| Typing 6 characters in search | 6 passes × 219ms | 1 pass × 55ms |
| History disk writes | one full rewrite per event | one per 25, plus flush |
| CLI requests per cycle | one per watched item | 9, flat |

Category pages are fetched concurrently over one pooled connection; browse rows
are reused rather than destroyed and rebuilt; the search box debounces; Stop and
Check Now interrupt the countdown immediately instead of up to a second late;
the activity log and changes feed are bounded.

### Added

- 90 tests, offline and dependency-free, plus CI on Windows and Linux across
  Python 3.10–3.13.
- `_bootstrap.py`: dependency check that runs before `unifi_core` is imported.
- `unifi_core.__version__` as the single source of the version string.

## v1.1.0

- Accessories and network-storage categories
- Build-ID retry on 404
- Error callback in `fetch_all_products`

## v1.0.0

- Initial release
