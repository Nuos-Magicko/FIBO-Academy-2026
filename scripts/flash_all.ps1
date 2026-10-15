# Flash and verify every connected XIAO ESP32S3 (Windows). Extra arguments pass through, e.g.
#   .\scripts\flash_all.ps1 -Image C:\path\gmt_people_flow_merged.bin
param([string]$Image = "")
$root = Split-Path $PSScriptRoot -Parent
$py = Join-Path $root "pc\.venv\Scripts\python.exe"
$a = @("$root\scripts\verify_board.py", "--all", "--flash")
if ($Image) { $a += @("--image", $Image) }
& $py @a
exit $LASTEXITCODE
