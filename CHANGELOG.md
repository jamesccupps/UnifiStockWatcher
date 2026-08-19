# Changelog

## Unreleased

### Added

- Restock estimates. The store sends `restockEtaAt` and `soldOutAt` on every
  catalog fetch and the app discarded them; a little over half of out-of-stock
  products carry at least one. Watched rows and the browse list now show
  e.g. `back ~7 Sep  ·  sold out 13 days`.

### Fixed

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
