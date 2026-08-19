"""Notification dispatch.

The guarantee under test is that store- and file-supplied text reaches
PowerShell as data. Title and message must never appear in the script source,
because PowerShell expands $(...) inside double-quoted strings.
"""

import unifi_core


class RecordingPopen:
    """Captures what would have been executed, without executing it."""

    instances = []

    def __init__(self, argv, env=None, **kwargs):
        self.argv = argv
        self.env = env or {}
        RecordingPopen.instances.append(self)


def _dispatch(monkeypatch, title, message):
    RecordingPopen.instances = []
    monkeypatch.setattr(unifi_core.subprocess, "Popen", RecordingPopen)
    unifi_core.notify_windows(title, message)
    return RecordingPopen.instances[-1]


INJECTION_PAYLOADS = [
    '$(Set-Content -Path pwned.txt -Value x)',
    'Switch $(Start-Process calc)',
    '`$(whatever)',
    "'; Remove-Item C:\\ -Recurse; '",
    '"; Invoke-Expression $env:EVIL; "',
    '$ExecutionContext.InvokeCommand.InvokeScript("bad")',
    'a`nb',
]


def test_payloads_never_reach_the_script_source(monkeypatch):
    for payload in INJECTION_PAYLOADS:
        call = _dispatch(monkeypatch, payload, payload)
        script = call.argv[-1]
        assert payload not in script, f"payload interpolated into script: {payload}"


def test_payloads_are_passed_as_environment_data(monkeypatch):
    payload = INJECTION_PAYLOADS[0]
    call = _dispatch(monkeypatch, payload, "body")
    assert call.env["UNIFI_NOTIFY_TITLE"] == payload
    assert call.env["UNIFI_NOTIFY_TEXT"] == "body"


def test_script_reads_only_from_the_environment(monkeypatch):
    call = _dispatch(monkeypatch, "T", "M")
    script = call.argv[-1]
    assert "$env:UNIFI_NOTIFY_TITLE" in script
    assert "$env:UNIFI_NOTIFY_TEXT" in script


def test_long_text_is_truncated_for_the_balloon(monkeypatch):
    call = _dispatch(monkeypatch, "T" * 500, "M" * 900)
    assert len(call.env["UNIFI_NOTIFY_TITLE"]) == 63
    assert len(call.env["UNIFI_NOTIFY_TEXT"]) == 255


def test_non_string_input_does_not_raise(monkeypatch):
    call = _dispatch(monkeypatch, None, 12345)
    assert call.env["UNIFI_NOTIFY_TITLE"] == "None"
    assert call.env["UNIFI_NOTIFY_TEXT"] == "12345"


def test_powershell_is_invoked_without_a_profile(monkeypatch):
    call = _dispatch(monkeypatch, "T", "M")
    assert call.argv[0] == "powershell"
    assert "-NoProfile" in call.argv
    assert "-NonInteractive" in call.argv


def test_dispatch_does_not_block(monkeypatch):
    """Popen, not run: the Tk main thread must not wait on the 3s balloon."""
    call = _dispatch(monkeypatch, "T", "M")
    assert not hasattr(call, "returncode")


def test_a_failed_launch_is_swallowed(monkeypatch, capsys):
    def boom(*a, **k):
        raise OSError("powershell missing")

    monkeypatch.setattr(unifi_core.subprocess, "Popen", boom)
    unifi_core.notify_windows("T", "M")          # must not raise
    assert "\a" in capsys.readouterr().out
