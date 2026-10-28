"""Draw the app and tray icons into assets/.

    python scripts/make_icons.py

A blue rounded square with a white bell; the app icon and the tray's alert
state add a green "in stock" dot. Idle is grey. Deliberately nothing like
Ubiquiti's own logo. The files are committed, so a build never runs this;
run it again after changing the drawing.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gfx import ICON_GREY, bell_icon, ico_bytes  # noqa: E402

ASSETS = ROOT / "assets"
APP_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
TRAY_SIZES = (16, 20, 24, 32, 40, 48)


def write_ico(name, sizes, **kw):
    pngs = [(n, bell_icon(n, **kw).png()) for n in sizes]
    (ASSETS / name).write_bytes(ico_bytes(pngs))
    return pngs


def main():
    ASSETS.mkdir(exist_ok=True)
    pngs = write_ico("icon.ico", APP_SIZES, badge=True)
    (ASSETS / "icon.png").write_bytes(dict(pngs)[256])
    write_ico("tray-idle.ico", TRAY_SIZES, colours=ICON_GREY)
    write_ico("tray-watching.ico", TRAY_SIZES)
    write_ico("tray-alert.ico", TRAY_SIZES, badge=True)
    print("wrote", ", ".join(sorted(p.name for p in ASSETS.iterdir())))


if __name__ == "__main__":
    main()
