# Flash and verify every connected XIAO ESP32S3 (Windows). Optional: -Image C:\path\gmt_people_flow_merged.bin
param([string]$Image = "")
$pc = Split-Path $PSScriptRoot -Parent
$py = Join-Path $pc ".venv\Scripts\python.exe"
$a = @("$pc\main.py", "verify-board", "--all", "--flash")
if ($Image) { $a += @("--image", $Image) }
& $py @a
exit $LASTEXITCODE
