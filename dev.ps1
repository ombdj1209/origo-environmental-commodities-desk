# Run the whole stack on Windows without Docker, against a throwaway PostgreSQL 16
# cluster in .pgdata. Does not touch any PostgreSQL service already installed.
#
#   .\dev.ps1            start everything
#   .\dev.ps1 -Seed      start, then book a demo trading book
#   .\dev.ps1 -Stop      stop the cluster
param([switch]$Seed, [switch]$Stop)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$data = Join-Path $root ".pgdata"
$port = 5440

$bin = Get-ChildItem "C:\Program Files\PostgreSQL\*\bin\pg_ctl.exe" -ErrorAction SilentlyContinue |
       Sort-Object { [int]($_.FullName -replace '.*PostgreSQL\\(\d+)\\.*', '$1') } -Descending |
       Select-Object -First 1 -Expand DirectoryName
if (-not $bin) { throw "No PostgreSQL binaries found under C:\Program Files\PostgreSQL" }

if ($Stop) { & "$bin\pg_ctl.exe" -D $data stop; exit }

if (-not (Test-Path $data)) {
  & "$bin\initdb.exe" -D $data -U otc --auth-local=trust --auth-host=trust -E UTF8
}
& "$bin\pg_ctl.exe" -D $data -o "-p $port -c listen_addresses=127.0.0.1" -l "$data\server.log" start
& "$bin\psql.exe" -h 127.0.0.1 -p $port -U otc -d postgres -tAc "select 1 from pg_database where datname='otc_ctrm'" |
  ForEach-Object { if (-not $_) { & "$bin\createdb.exe" -h 127.0.0.1 -p $port -U otc otc_ctrm } }

$py = Join-Path $root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
  python -m venv (Join-Path $root "backend\.venv")
  & $py -m pip install -q -r (Join-Path $root "backend\requirements.txt")
}

$env:DATABASE_URL = "postgresql+asyncpg://otc@127.0.0.1:$port/otc_ctrm"
$env:CONFIRMATIONS_DIR = Join-Path $root "backend\confirmations"
$env:HOUSE_LEGAL_NAME = "OTC Flow B.V."
$env:HOUSE_LEI = "724500RQTZBT2SGDMC91"

Start-Process -FilePath $py -ArgumentList "-m","uvicorn","app.main:app","--host","127.0.0.1","--port","8000" `
              -WorkingDirectory (Join-Path $root "backend")
Start-Sleep -Seconds 3

if ($Seed) { & $py (Join-Path $root "backend\seed_demo.py") }

Push-Location (Join-Path $root "frontend")
if (-not (Test-Path "node_modules")) { npm install --no-audit --no-fund }
npm run dev
Pop-Location
