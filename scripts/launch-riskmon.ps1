# Launch the Risk Monitor app: start it hidden if it isn't already listening on
# :8000, wait until it's up, then open it in the browser. Robust replacement for
# the old batch launcher.
$ErrorActionPreference = "SilentlyContinue"
$proj = Split-Path -Parent $PSScriptRoot
$py   = Join-Path $proj ".venv\Scripts\python.exe"

if (-not (Get-NetTCPConnection -LocalPort 8000 -State Listen)) {
  Start-Process -FilePath $py -ArgumentList "run.py" -WorkingDirectory $proj -WindowStyle Hidden
}

for ($i = 0; $i -lt 40; $i++) {
  if (Get-NetTCPConnection -LocalPort 8000 -State Listen) { break }
  Start-Sleep -Milliseconds 500
}

Start-Process "http://riskmon:8000"
