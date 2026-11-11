"""What the app shows is in US spelling (3.2.5 on), while the data keys keep
their old spelling: renaming them would break existing watch lists, settings,
exported files and the JSON phone alerts send (Home Assistant automations)."""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# What a user sees comes from these: the window, the CLI's output, alerts.
SOURCES = ["unifi_watcher_gui.py", "unifi_watcher.py", "webhooks.py", "unifi_core.py",
           "tray.py", "updater.py", "winplatform.py"]

# Keys of the files and payloads, never shown as text.
DATA_KEYS = {"favourite", "alert_favourites_only", "phone_favourites_only"}

BRITISH = ("favourit", "colour")


def _strings(tree):
    """Every string literal in the module, f-string parts included, docstrings not."""
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            yield node.lineno, node.value


@pytest.mark.parametrize("name", SOURCES)
def test_labels_are_in_us_spelling(name):
    tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
    found = [f"{name}:{line}: {text!r}" for line, text in _strings(tree)
             if text not in DATA_KEYS and any(w in text.lower() for w in BRITISH)]
    assert not found, "\n".join(found)


def test_the_data_keys_keep_their_spelling():
    """A watch list or settings file from any version still loads as it was."""
    core = (ROOT / "unifi_core.py").read_text(encoding="utf-8")
    for key in DATA_KEYS:
        assert f'"{key}"' in core
