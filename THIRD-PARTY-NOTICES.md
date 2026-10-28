# Third-party notices

The Windows build (`UnifiStockWatcher-Setup.exe` and `UnifiStockWatcher-win64.zip`) bundles a Python runtime and
the libraries below, inside its `_internal` folder. Running from source uses your own installation of them. The
app's own code is under the MIT license in [LICENSE](LICENSE).

| Component | What it's for | License |
|---|---|---|
| [Python](https://www.python.org/) 3.13 and its standard library | the runtime | [PSF License 2.0](https://docs.python.org/3/license.html) |
| [Tcl/Tk](https://www.tcl.tk/) 8.6 (tkinter) | the window and its widgets | [Tcl/Tk license](https://www.tcl.tk/software/tcltk/license.html) (BSD-style) |
| [OpenSSL](https://www.openssl.org/) 3, as built into Python | HTTPS | [Apache 2.0](https://www.openssl.org/source/license.html) |
| [libffi](https://sourceware.org/libffi/), as built into Python | ctypes (Windows integration) | MIT |
| [zlib](https://zlib.net/), [bzip2](https://sourceware.org/bzip2/), [liblzma](https://tukaani.org/xz/), [SQLite](https://sqlite.org/), as built into Python | compression, the standard library | zlib / bzip2 / 0BSD / public domain |
| [requests](https://requests.readthedocs.io/) | talking to the store and to GitHub | Apache 2.0 |
| [urllib3](https://urllib3.readthedocs.io/) | requests' connection pool | MIT |
| [certifi](https://github.com/certifi/python-certifi) | the CA certificates HTTPS checks against | MPL 2.0 |
| [idna](https://github.com/kjd/idna) | requests' host names | BSD 3-clause |
| [charset-normalizer](https://github.com/jawah/charset_normalizer) | requests' text decoding | MIT |
| [PyInstaller](https://pyinstaller.org/) bootloader | starts the bundled Python (`UnifiStockWatcher.exe`) | GPL 2.0 with the bootloader exception, which allows distributing it with any program |

The installer is made with [Inno Setup](https://jrsoftware.org/isinfo.php) (its
[license](https://jrsoftware.org/files/is/license.txt) permits this). Signing releases uses
[cryptography](https://cryptography.io/) (Apache 2.0 / BSD) on the maintainer's machine and in CI; it is not
part of the app, which checks signatures with its own `ed25519.py`.

Icons in the app's buttons come from the Segoe Fluent Icons (Windows 11) or Segoe MDL2 Assets (Windows 10) font
already on your PC; no fonts are bundled. The app icon is drawn by `gfx.py`.
