param([Parameter(Mandatory=$true)][ValidatePattern('^r[0-9]+$')][string]$Revision)
$ErrorActionPreference='Stop'
$kaCppRoot=$PSScriptRoot
$kaFreeze=Join-Path $kaCppRoot "revisions/$Revision"
if(Test-Path -LiteralPath $kaFreeze){throw "Revision already exists; preserve its immutable files: $kaFreeze"}
New-Item -ItemType Directory -Path (Join-Path $kaFreeze 'standalone-optimizer'),(Join-Path $kaFreeze 'full_battle_abi'),(Join-Path $kaFreeze 'include/nlohmann') | Out-Null
Get-ChildItem -LiteralPath $kaCppRoot -File | Where-Object Extension -In '.cpp','.hpp','.cmd','.py','.ps1' | Copy-Item -Destination (Join-Path $kaFreeze 'standalone-optimizer')
Copy-Item -LiteralPath (Join-Path $kaCppRoot 'optimizer.exe') -Destination (Join-Path $kaFreeze 'standalone-optimizer/optimizer.exe')
Copy-Item -LiteralPath (Join-Path $kaCppRoot '../full_battle_abi/ka_battle_report.hpp') -Destination (Join-Path $kaFreeze 'full_battle_abi/ka_battle_report.hpp')
Copy-Item -LiteralPath (Join-Path $kaCppRoot '../include/nlohmann/json.hpp') -Destination (Join-Path $kaFreeze 'include/nlohmann/json.hpp')
Get-ChildItem -LiteralPath $kaFreeze -Recurse -File | Get-FileHash -Algorithm SHA256 | Select-Object Path,Hash | ConvertTo-Json -Depth 3 | Set-Content (Join-Path $kaFreeze 'manifest.json')
Get-FileHash -LiteralPath (Join-Path $kaFreeze 'standalone-optimizer/optimizer.exe') -Algorithm SHA256 | Select-Object Path,Hash | ConvertTo-Json
