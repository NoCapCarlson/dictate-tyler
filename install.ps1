# Carlson installer: venv + dependencies + PATH launchers + run-at-logon.
# Run from the repo root: .\install.ps1
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "[1/4] Python venv + dependencies (faster-whisper, sounddevice, rich, pynput...)"
if (-not (Test-Path "$repo\venv")) { python -m venv "$repo\venv" }
& "$repo\venv\Scripts\python.exe" -m pip install --quiet --upgrade pip wheel
& "$repo\venv\Scripts\python.exe" -m pip install --quiet faster-whisper sounddevice numpy rich pynput pyperclip

Write-Host "[2/4] Launchers in ~\.local\bin (must be on PATH)"
$bin = "$env:USERPROFILE\.local\bin"
New-Item -ItemType Directory -Force $bin | Out-Null
$launcher = "@echo off`r`nset PYTHONUTF8=1`r`n`"$repo\venv\Scripts\python.exe`" -u `"$repo\carlson.py`" %*`r`n"
[IO.File]::WriteAllText("$bin\carlson.cmd", $launcher, [Text.Encoding]::ASCII)
$stop = "@echo off`r`npowershell -NoProfile -Command `"Get-CimInstance Win32_Process -Filter \`"Name='python.exe' or Name='pythonw.exe'\`" | Where-Object { `$_.CommandLine -match 'carlson' } | ForEach-Object { Stop-Process -Id `$_.ProcessId -Force }`"`r`necho Carlson stopped.`r`n"
[IO.File]::WriteAllText("$bin\carlson-stop.cmd", $stop, [Text.Encoding]::ASCII)

Write-Host "[3/4] Warming Whisper models (first run downloads ~225 MB)"
& "$repo\venv\Scripts\python.exe" -c "from faster_whisper import WhisperModel as W; W('tiny', device='cpu', compute_type='int8'); W('base', device='cpu', compute_type='int8')"

Write-Host "[4/4] Registering run-at-logon task and starting Carlson"
& "$repo\venv\Scripts\python.exe" "$repo\carlson.py" --autostart on

Write-Host "Done. Hold SPACE for 1 second and speak."
