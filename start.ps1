# MySQL Backup Viewer - PowerShell launcher
# Run with:  .\start.ps1          (uses the backup found automatically)
#        or: .\start.ps1 -SqlFile "C:\path\to\backup.sql"
param(
    [string]$SqlFile = "",
    [int]$Port = 5000
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

Write-Host "==============================================" 
Write-Host "  MySQL Backup Viewer (Flask)"
Write-Host "==============================================" 

# --- check python ---
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host "[!] Python was not found." -ForegroundColor Red
    Write-Host "    Install it from https://www.python.org/downloads/"
    Write-Host "    IMPORTANT: check 'Add python.exe to PATH' during setup,"
    Write-Host "    then open PowerShell again and run this script."
    Read-Host "Press Enter to exit"
    exit 1
}

# --- install Flask if missing ---
python -c "import flask" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[*] Installing Flask (first run only)..."
    python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[!] pip install failed. Check your internet connection." -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }
}

# --- open the browser shortly after the server starts ---
Start-Job -ScriptBlock {
    param($p)
    Start-Sleep -Seconds 3
    Start-Process ("http://127.0.0.1:{0}" -f $p)
} -ArgumentList $Port | Out-Null

# --- run the app ---
if ($SqlFile -ne "") {
    python app.py --file $SqlFile --port $Port
} else {
    python app.py --port $Port
}

Read-Host "Server stopped. Press Enter to exit"
