
---

### Downloads

| File | Use |
|---|---|
| `UnifiStockWatcher.exe` | The GUI. Double-click; no Python needed. |
| `UnifiStockWatcher-cli.exe` | Headless watcher, for running under Task Scheduler. |

Both are portable — they keep `watched_items.json`, `settings.json` and
`stock_history.json` in the folder you put the executable in, so put it
somewhere writable rather than `Program Files`.

The executables are unsigned, so SmartScreen will warn on first run
(*More info → Run anyway*). Some antivirus engines flag unsigned PyInstaller
builds; if you would rather not trust a binary, running from source is
still fully supported — see the README.
