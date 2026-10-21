param([ValidateRange(1, 65535)][int]$Port = 8787)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonCandidates = @(
    (Join-Path $PSScriptRoot '.venv\Scripts\python.exe'),
    (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe')
)
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCommand -and $pythonCommand.Source -notlike '*\WindowsApps\*') {
    $pythonCandidates += $pythonCommand.Source
}
$pythonRuntime = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $pythonRuntime) {
    throw 'Install Python 3.11 or newer, create .venv, and install requirements.txt as described in README.md.'
}
& $pythonRuntime -c 'import numpy' 2>$null
if ($LASTEXITCODE -ne 0) {
    throw 'The selected Python environment needs NumPy. Install requirements.txt as described in README.md.'
}
Write-Host "WorldFork will be available at http://127.0.0.1:$Port. Keep this terminal running; press Ctrl+C to stop."
& $pythonRuntime -m worldfork.server --port $Port
exit $LASTEXITCODE
