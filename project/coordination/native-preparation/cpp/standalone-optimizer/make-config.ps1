[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$RawCandidates,
    [Parameter(Mandatory=$true)][string]$Tables,
    [Parameter(Mandatory=$true)][string]$KernelPath,
    [Parameter(Mandatory=$true)][string]$OutputDir,
    [Parameter(Mandatory=$true)][string]$Provenance,
    [string]$CandidateMetadata,
    [ValidateRange(1,256)][int]$Executors = 1,
    [ValidateRange(0,2147483647)][long]$SeedStart = 0,
    [ValidateRange(1,1000000)][long]$SeedCount = 1,
    [ValidateRange(1,100000)][long]$Generations = 1,
    [string]$MutationsFile,
    [string]$ConfigPath
)

$ErrorActionPreference = 'Stop'

function Resolve-InputFile([string]$Path, [string]$Label) {
    $resolved = (Resolve-Path -LiteralPath $Path).Path
    if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) { throw "$Label must be a file: $Path" }
    return $resolved
}

$rawPath = Resolve-InputFile $RawCandidates 'RawCandidates'
$tablesPath = Resolve-InputFile $Tables 'Tables'
$kernelFile = Resolve-InputFile $KernelPath 'KernelPath'
$provenanceFile = Resolve-InputFile $Provenance 'Provenance'
$outputPath = [System.IO.Path]::GetFullPath($OutputDir)
if (($SeedCount - 1) -gt (2147483647L - $SeedStart)) { throw 'SeedStart + SeedCount - 1 must remain within nonnegative signed 31-bit seeds.' }
$parsedProvenance = Get-Content -LiteralPath $provenanceFile -Raw | ConvertFrom-Json
if ($parsedProvenance.provenance) { $parsedProvenance = $parsedProvenance.provenance }
if ($parsedProvenance -isnot [System.Management.Automation.PSCustomObject]) { throw 'Provenance JSON must be an object of SHA-256 fields.' }
$provenanceObject = @{}
foreach ($property in $parsedProvenance.PSObject.Properties) { $provenanceObject[$property.Name] = $property.Value }

$requiredHashes = @('engineSha256','mechanicsSha256','policySha256','abiSha256','arenaSha256')
foreach ($field in $requiredHashes) {
    if (-not $provenanceObject.Contains($field) -or $provenanceObject[$field] -notmatch '^[0-9a-fA-F]{64}$') {
        throw "Provenance must supply a 64-digit SHA-256 field: $field"
    }
}
$provenanceObject.rawCandidatesSha256 = (Get-FileHash -LiteralPath $rawPath -Algorithm SHA256).Hash.ToLowerInvariant()
$provenanceObject.tablesSha256 = (Get-FileHash -LiteralPath $tablesPath -Algorithm SHA256).Hash.ToLowerInvariant()
$provenanceObject.currentKernelSha256 = (Get-FileHash -LiteralPath $kernelFile -Algorithm SHA256).Hash.ToLowerInvariant()

$config = [ordered]@{
    rawCandidates = $rawPath
    tables = $tablesPath
    kernelPath = $kernelFile
    outputDir = $outputPath
    executors = $Executors
    seedStart = $SeedStart
    seedCount = $SeedCount
    generations = $Generations
    mutations = @()
    provenance = $provenanceObject
}
if ($CandidateMetadata) { $config.candidateMetadata = Resolve-InputFile $CandidateMetadata 'CandidateMetadata' }
if ($MutationsFile) {
    $mutationPath = Resolve-InputFile $MutationsFile 'MutationsFile'
    $mutationText = Get-Content -LiteralPath $mutationPath -Raw
    $wrappedMutations = ('{"mutations":' + $mutationText + '}') | ConvertFrom-Json
    $config.mutations = $wrappedMutations.mutations
    if ($config.mutations -isnot [array]) { throw 'Mutations file must contain a JSON array.' }
}

if (-not $ConfigPath) { $ConfigPath = Join-Path (Get-Location) 'optimizer-config.json' }
$configPathFull = [System.IO.Path]::GetFullPath($ConfigPath)
$inputPaths = @($rawPath,$tablesPath,$kernelFile,$provenanceFile)
if ($CandidateMetadata) { $inputPaths += (Resolve-Path -LiteralPath $CandidateMetadata).Path }
if ($MutationsFile) { $inputPaths += (Resolve-Path -LiteralPath $MutationsFile).Path }
foreach ($inputPath in $inputPaths) {
    if ([string]::Equals($configPathFull,[System.IO.Path]::GetFullPath($inputPath),[StringComparison]::OrdinalIgnoreCase)) {
        throw "ConfigPath cannot overwrite an input file: $inputPath"
    }
}
$jsonText = ($config | ConvertTo-Json -Depth 100) + [Environment]::NewLine
[System.IO.File]::WriteAllText($configPathFull, $jsonText, [System.Text.UTF8Encoding]::new($false))
Write-Output $configPathFull
