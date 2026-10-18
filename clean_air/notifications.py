from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = r"""
$ErrorActionPreference='Stop'
[Console]::InputEncoding=[System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$data=[Console]::In.ReadToEnd() | ConvertFrom-Json
$icon=New-Object System.Windows.Forms.NotifyIcon
try {
    $icon.Icon=[System.Drawing.SystemIcons]::Information
    $icon.BalloonTipTitle=$data.title
    $icon.BalloonTipText=$data.message
    $icon.Visible=$true
    $icon.ShowBalloonTip(6000)
    $until=[DateTime]::UtcNow.AddSeconds(7)
    while ([DateTime]::UtcNow -lt $until) {
        [System.Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 100
    }
} finally {
    $icon.Dispose()
}
"""


def desktop_process():
    if os.name != "nt":
        raise RuntimeError("Desktop notifications require an interactive Windows user session.")
    windows = Path(os.environ["SystemRoot"])
    executable = windows / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    environment = {key: value for key, value in os.environ.items() if key.upper() != "OPENAQ_API_KEY"}
    return subprocess.Popen(
        [str(executable), "-NoProfile", "-NonInteractive", "-Command", SCRIPT],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=windows, env=environment, text=True, encoding="utf-8",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
