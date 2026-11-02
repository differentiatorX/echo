# Echo one-line installer for Windows
#   irm https://raw.githubusercontent.com/differentiatorX/echo/main/install.ps1 | iex
$ErrorActionPreference = "Stop"
function Step($t) { Write-Host "`n> $t" -ForegroundColor Cyan }
function Refresh-Path { $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User") }

Write-Host "`n  echo  -  the few Reels that echo`n" -ForegroundColor Cyan

Step "Checking Python"
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py -or (python --version 2>&1) -notmatch "3\.(1[0-9])") {
  winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
  Refresh-Path
}

Step "Checking ffmpeg"
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
  Refresh-Path
}

Step "Checking Git"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements
  Refresh-Path
}

Step "Installing Echo"
$dir = Join-Path $HOME "Documents\echo"
if (Test-Path (Join-Path $dir ".git")) { git -C $dir pull --ff-only } else { git clone https://github.com/differentiatorX/echo $dir }
python -m pip install -q --upgrade pip
python -m pip install -q -e $dir

Step "Connecting your AI agents"
python -m echoclip.cli connect all
python -m echoclip.cli install-skill --force

Step "Checking everything"
python -m echoclip.cli doctor

Write-Host "`nAll set. Start with:  echoclip start" -ForegroundColor Green
Write-Host "Or ask your agent:     Use echo to make 4 Reels from <link>`n" -ForegroundColor Green
