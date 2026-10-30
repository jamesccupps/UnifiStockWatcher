# Changelog

Each release's own notes (what users see on the release page) are in `release-notes/`.

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
