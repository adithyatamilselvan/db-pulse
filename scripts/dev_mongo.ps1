# Starts the two local MongoDB instances used for development and tests:
#   27117 = DB Pulse internal metadata DB    27118 = a "monitored" MongoDB
# Requires MongoDB Community Server (mongod) on PATH or in the default install folder.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$mongod = (Get-Command mongod -ErrorAction SilentlyContinue).Source
if (-not $mongod) { $mongod = (Get-ChildItem "C:\Program Files\MongoDB\Server" -Recurse -Filter mongod.exe | Select-Object -First 1).FullName }
if (-not $mongod) { throw "mongod not found. Install MongoDB Community Edition." }

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = (Get-Command python -ErrorAction SilentlyContinue).Source }
if (-not $python) { throw "Python is required to verify MongoDB readiness. Create the project virtual environment first." }

function Test-MongoPing {
  param([int]$Port)
  & $python -c "import sys`nfrom pymongo import MongoClient`nfrom pymongo.errors import PyMongoError`ntry:`n c=MongoClient('mongodb://127.0.0.1:$Port',serverSelectionTimeoutMS=500)`n sys.exit(0 if c.admin.command('ping').get('ok') == 1 else 1)`nexcept PyMongoError:`n sys.exit(1)" 2>$null
  return $LASTEXITCODE -eq 0
}

foreach ($i in @(
  @{p=27117; d="mongo-internal"; name="Internal MongoDB"},
  @{p=27118; d="mongo-monitored"; name="Monitored MongoDB"}
)) {
  $dir = Join-Path $root ".dev\$($i.d)"
  $log = Join-Path $root ".dev\$($i.d.Replace('mongo-', '')).log"
  $pidFile = Join-Path $root ".dev\$($i.d).pid"
  New-Item -ItemType Directory -Force $dir | Out-Null

  if (Test-MongoPing $i.p) {
    Write-Host "$($i.name): $($i.p) - READY (already running)"
    continue
  }

  $portListener = Get-NetTCPConnection -LocalPort $i.p -State Listen -ErrorAction SilentlyContinue
  if ($portListener) {
    Write-Error "$($i.name): port $($i.p) is occupied but MongoDB ping failed. Resolve the port conflict before retrying."
    exit 1
  }

  $arguments = "--dbpath `"$dir`" --port $($i.p) --bind_ip 127.0.0.1 --logpath `"$log`""
  $process = Start-Process -FilePath $mongod -ArgumentList $arguments -WindowStyle Hidden -PassThru
  Set-Content -Path $pidFile -Value $process.Id -Encoding ASCII

  $ready = $false
  for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Milliseconds 500
    if (Test-MongoPing $i.p) {
      $ready = $true
      break
    }
    if ($process.HasExited) {
      break
    }
  }

  if (-not $ready) {
    Write-Error "$($i.name): $($i.p) failed to become ready. Inspect log: $log"
    exit 1
  }

  Write-Host "$($i.name): $($i.p) - READY (PID $($process.Id))"
}
