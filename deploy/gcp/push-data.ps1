<#
Copies what lives only on this PC to the VM: the database (profile,
applications, sources), your CV, tailored CVs, the Gmail/Google credential
files and -- with -IncludeEnv -- your .env with its API keys. Then reruns the
VM's startup script, which re-applies the server settings to that .env
(login password, hosted model, timezone) and restarts the service.

Run from the project root, after the VM exists:
  powershell -ExecutionPolicy Bypass -File deploy\gcp\push-data.ps1 -IncludeEnv

Safe to rerun; it replaces the copies on the VM each time. Stop the local
app first so the database isn't mid-write.
#>
param(
  [string]$Instance = "saei",
  [string]$Zone = "us-central1-a",
  [switch]$IncludeEnv
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root

$python = Join-Path $env:USERPROFILE "anaconda3\envs\job-agent\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
if (-not $env:CLOUDSDK_PYTHON) { $env:CLOUDSDK_PYTHON = $python }

$stage = Join-Path $env:TEMP "saei-push"
Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$stage\data", "$stage\cv", "$stage\cv_output", "$stage\credentials" | Out-Null

# A consistent copy of the database: SQLite's backup API folds the -wal file in.
& $python -c "import sqlite3; s=sqlite3.connect('data/job_agent.db'); d=sqlite3.connect(r'$stage\data\job_agent.db'); s.backup(d); d.close(); s.close()"
Get-ChildItem cv -File | Where-Object { $_.Name -like "current_cv.*" } | Copy-Item -Destination "$stage\cv"
Get-ChildItem cv_output -File -Include *.pdf, *.docx -Recurse | Copy-Item -Destination "$stage\cv_output"
Get-ChildItem credentials -File -Filter *.json | Copy-Item -Destination "$stage\credentials"
if ($IncludeEnv) { Copy-Item .env "$stage\.env" }

$remote = @'
set -e
sudo systemctl stop job-agent-api || true
cd /tmp/saei-push
for d in data cv cv_output credentials; do sudo mkdir -p /opt/job-agent/$d; sudo cp -r $d/. /opt/job-agent/$d/ 2>/dev/null || true; done
if [ -f .env ]; then sudo cp .env /opt/job-agent/.env; fi
sudo chown -R job-agent:job-agent /opt/job-agent
cd / && rm -rf /tmp/saei-push
sudo google_metadata_script_runner startup
'@
# Written as a file with LF endings and run there: a multi-line --command
# doesn't survive the trip through gcloud's Windows SSH client.
[IO.File]::WriteAllText("$stage\install.sh", ($remote -replace "`r`n", "`n"))

Write-Host "Uploading to $Instance ($Zone)..."
gcloud compute ssh $Instance --zone $Zone --command "rm -rf /tmp/saei-push && mkdir -p /tmp/saei-push"
gcloud compute scp --recurse --zone $Zone "$stage\*" "${Instance}:/tmp/saei-push/"
if ($LASTEXITCODE -ne 0) { throw "Upload failed." }

gcloud compute ssh $Instance --zone $Zone --command "bash /tmp/saei-push/install.sh"
if ($LASTEXITCODE -ne 0) { throw "Remote install failed." }
Remove-Item $stage -Recurse -Force
Write-Host "Done. Your login: gcloud compute ssh $Instance --zone $Zone --command `"sudo grep -E '^APP_(USERNAME|PASSWORD)=' /opt/job-agent/.env`""
