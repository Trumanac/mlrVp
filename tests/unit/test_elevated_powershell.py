"""Elevated PowerShell commands carry the script inline, never as a file."""
import base64
import re

from mlrvp.grid.controller import _elevated_powershell_command


def _payload(cmd):
    m = re.search(r"'-EncodedCommand','([A-Za-z0-9+/=]+)'", cmd)
    assert m, cmd
    return base64.b64decode(m.group(1)).decode("utf-16-le")


def test_script_round_trips_with_quotes():
    script = "Get-Process -Name 'serialosc*'; Write-Host \"it's done\""
    cmd = _elevated_powershell_command(script, wait=True)
    assert _payload(cmd) == script
    assert "-Verb RunAs -Wait" in cmd
    assert ".ps1" not in cmd and "-File" not in cmd


def test_fire_and_forget_has_no_wait():
    assert " -Wait" not in _elevated_powershell_command("Start-Service serialosc", wait=False)
