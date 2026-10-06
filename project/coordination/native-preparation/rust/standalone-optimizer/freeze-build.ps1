param([Parameter(Mandatory=$true)][ValidatePattern('^r[0-9]+$')][string]$Revision)
$ErrorActionPreference='Stop'
$binaryPath=Join-Path $PSScriptRoot 'target/release/ka-rust-standalone-optimizer.exe'
$buildPath=Join-Path $PSScriptRoot "builds/$Revision"
if(Test-Path -LiteralPath $buildPath){throw "Revision already exists: $buildPath"}
$sourceFiles=@(Get-Item (Join-Path $PSScriptRoot 'Cargo.toml'),(Join-Path $PSScriptRoot 'Cargo.lock'),(Join-Path $PSScriptRoot 'shared-kernel/Cargo.toml'))+@(Get-ChildItem (Join-Path $PSScriptRoot 'src') -Filter '*.rs')
$binary=Get-Item -LiteralPath $binaryPath
foreach($source in $sourceFiles){if($source.LastWriteTimeUtc -gt $binary.LastWriteTimeUtc){throw "Source newer than executable: $($source.FullName)"}}
[IO.Directory]::CreateDirectory($buildPath)|Out-Null
Copy-Item -LiteralPath $binaryPath -Destination (Join-Path $buildPath 'ka-rust-standalone-optimizer.exe')
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'src') -Destination (Join-Path $buildPath 'src') -Recurse
$hashes=@{}
foreach($source in $sourceFiles){$hashes[$source.FullName]=(Get-FileHash -LiteralPath $source.FullName -Algorithm SHA256).Hash.ToLower()}
$routingPath=Join-Path $PSScriptRoot '../../../native-finish/chats.json'
$routing=Get-Content -LiteralPath $routingPath -Raw|ConvertFrom-Json
$manifest=@{schema='ka-rust-frozen-build-v1';revision=$Revision;binarySha256=(Get-FileHash -LiteralPath $binaryPath -Algorithm SHA256).Hash.ToLower();optimizerSourceSha256=$hashes;builderExecutedTests=$false;contract='OPTIMIZER-CONTRACT.md';testCoordinatorThreadId=$routing.testingCoordinator.threadId}
[IO.File]::WriteAllText((Join-Path $buildPath 'manifest.json'),($manifest|ConvertTo-Json -Depth 10),[Text.UTF8Encoding]::new($false))
$manifest.binarySha256
