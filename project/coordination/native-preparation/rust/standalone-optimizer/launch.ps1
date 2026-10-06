param([Parameter(Mandatory=$true)][string]$ConfigPath,[switch]$Headless)
$ErrorActionPreference='Stop'
$configAbsolute=(Resolve-Path -LiteralPath $ConfigPath).Path
$config=Get-Content -LiteralPath $configAbsolute -Raw|ConvertFrom-Json
$outputPath=$config.output
if(-not[IO.Path]::IsPathRooted($outputPath)){$outputPath=Join-Path ([IO.Path]::GetDirectoryName($configAbsolute)) $outputPath}
[IO.Directory]::CreateDirectory($outputPath)|Out-Null
$binaryPath=Join-Path $PSScriptRoot 'target/release/ka-rust-standalone-optimizer.exe'
$job=Start-Process -FilePath $binaryPath -ArgumentList ('"'+$configAbsolute+'"') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $outputPath 'stdout.log') -RedirectStandardError (Join-Path $outputPath 'stderr.log')
try{$job.PriorityClass='BelowNormal'}catch{}
if(-not$Headless){
    $monitorPath=Join-Path $PSScriptRoot 'monitor.ps1'
    $statusPath=Join-Path $outputPath 'status.json'
    Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-WindowStyle','Hidden','-File',('"'+$monitorPath+'"'),'-StatusPath',('"'+$statusPath+'"'),'-JobProcessId',$job.Id) -WindowStyle Hidden | Out-Null
}
Write-Output ([pscustomobject]@{ProcessId=$job.Id;Output=$outputPath})
