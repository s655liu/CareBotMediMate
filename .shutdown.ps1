# PowerShell script to shutdown the Python backend and free up port 5000
$port = 5000
$process = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -First 1
if ($process) {
    Write-Host "Stopping process $process on port $port..."
    Stop-Process -Id $process -Force
}

# Also cleanup any python processes running main.py just in case
Get-Process -Name "python" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*main.py*" } | Stop-Process -Force

Write-Host "Backend shutdown complete."
