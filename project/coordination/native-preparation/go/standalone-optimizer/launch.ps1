param(
    [Parameter(Mandatory = $true)][Alias('Input')][string]$InputFile,
    [Parameter(Mandatory = $true)][string]$Output,
    [int]$Executors = 0,
    [uint64]$Budget = 0,
    [int]$Batch = 64,
    [uint64]$Seed = 1,
    [switch]$Headless
)

$ErrorActionPreference = 'Stop'
$buildManifestPath = Join-Path $PSScriptRoot 'build-manifest.json'
if (-not (Test-Path -LiteralPath $buildManifestPath -PathType Leaf)) { throw "Build manifest missing: $buildManifestPath" }
$buildManifest = Get-Content -LiteralPath $buildManifestPath -Raw | ConvertFrom-Json
if ($buildManifest.exitCode -ne 0 -or $buildManifest.sourceChangedDuringBuild) { throw 'No stable successful Go compilation is selected.' }
$optimizerPath = [string]$buildManifest.executablePath
$monitorPath = Join-Path $PSScriptRoot 'monitor.ps1'
$inputPath = [System.IO.Path]::GetFullPath($InputFile)
$outputPath = [System.IO.Path]::GetFullPath($Output)

if (-not (Test-Path -LiteralPath $optimizerPath -PathType Leaf)) { throw "Optimizer executable not found: $optimizerPath" }
if ((Get-FileHash -LiteralPath $optimizerPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $buildManifest.executableSHA256) { throw 'Selected executable differs from its immutable build manifest.' }
if (-not (Test-Path -LiteralPath $inputPath -PathType Leaf)) { throw "Input workload not found: $inputPath" }
if ($Executors -eq 0) {
    $logicalCapacity = [Environment]::ProcessorCount
    $availableMiB = [double](Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1024
    if ($availableMiB -lt 6400) { throw 'Less than 6 GiB memory plus one worker is available; close heavy jobs before launching.' }
    $cpuLimit = [Math]::Max(1, [Math]::Floor($logicalCapacity * 0.8))
    $memoryLimit = [Math]::Max(1, [Math]::Floor(($availableMiB - 6144) / 128))
    $Executors = [int][Math]::Min($cpuLimit, $memoryLimit)
}
if ($Executors -lt 1 -or $Executors -gt 256 -or $Batch -lt 1 -or $Batch -gt 4096) { throw 'Invalid executors or batch value.' }
if (-not $Headless -and -not (Test-Path -LiteralPath $monitorPath -PathType Leaf)) { throw "Progress monitor not found: $monitorPath" }
New-Item -ItemType Directory -Force -Path $outputPath | Out-Null
$outputDrive = [System.IO.DriveInfo]::new([System.IO.Path]::GetPathRoot($outputPath))
if ($outputDrive.AvailableFreeSpace -lt 1GB) { throw 'Less than 1 GiB storage is available for durable results.' }

$stdoutPath = Join-Path $outputPath 'runner.stdout.log'
$stderrPath = Join-Path $outputPath 'runner.stderr.log'
$statusPath = Join-Path $outputPath 'status.json'
$failurePath = Join-Path $outputPath 'launcher-failure.json'

if (-not $Headless) {
    $powershellExe = Join-Path $PSHOME 'pwsh.exe'
    if (-not (Test-Path -LiteralPath $powershellExe -PathType Leaf)) { $powershellExe = Join-Path $PSHOME 'powershell.exe' }
    if (-not (Test-Path -LiteralPath $powershellExe -PathType Leaf)) { throw 'Could not locate PowerShell to open the progress monitor.' }
    $monitorArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $monitorPath + '"'), '-OutputDir', ('"' + $outputPath + '"'))
    Start-Process -FilePath $powershellExe -ArgumentList $monitorArgs -WindowStyle Hidden | Out-Null
}

$runnerArgs = @('--input', ('"' + $inputPath + '"'), '--output', ('"' + $outputPath + '"'), '--executors', "$Executors", '--budget', "$Budget", '--batch', "$Batch", '--seed', "$Seed")
$exitCode = 1
$launchError = $null
try {
    $runner = Start-Process -FilePath $optimizerPath -ArgumentList $runnerArgs -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
    try { $runner.PriorityClass = [System.Diagnostics.ProcessPriorityClass]::BelowNormal } catch { }
    $runner.WaitForExit()
    $exitCode = $runner.ExitCode
} catch {
    $launchError = $_.Exception.Message
}

# Preserve a runner-authored terminal status. If the runner exited before it
# could do so, create an explicit failure status while retaining both logs.
$existing = $null
if (Test-Path -LiteralPath $statusPath) {
    try { $existing = Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json } catch { $existing = $null }
}
$terminalStages = @('complete', 'failed', 'paused')
if ($null -eq $existing -or $terminalStages -notcontains [string]$existing.Stage) {
    $detail = if ($launchError) { $launchError } else { "Optimizer exited with code $exitCode without writing a terminal status." }
    $failure = [ordered]@{ stage = 'failed'; exitCode = $exitCode; detail = $detail; output = $outputPath; stdout = $stdoutPath; stderr = $stderrPath; recordedAt = [DateTimeOffset]::Now.ToString('o') }
    $failure | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $failurePath -Encoding UTF8
    $status = [ordered]@{
        Stage = 'failed'
        Completed = if ($null -ne $existing) { [uint64]$existing.Completed } else { 0 }
        Total = if ($null -ne $existing) { [uint64]$existing.Total } else { $Budget }
        Rejected = if ($null -ne $existing) { [uint64]$existing.Rejected } else { 0 }
        DuplicateSkips = if ($null -ne $existing) { [uint64]$existing.DuplicateSkips } else { 0 }
        Errors = if ($null -ne $existing) { [uint64]$existing.Errors + 1 } else { 1 }
        Durable = if ($null -ne $existing) { [uint64]$existing.Durable } else { 0 }
        ElapsedSeconds = if ($null -ne $existing) { [double]$existing.ElapsedSeconds } else { 0 }
        Detail = $detail
        Output = $outputPath
    }
    $status | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $statusPath -Encoding UTF8
}

if ($launchError) { throw $launchError }
if ($exitCode -ne 0) { throw "Optimizer failed with exit code $exitCode. See $statusPath and $stderrPath" }
Write-Output "Optimizer finished. Output: $outputPath"
