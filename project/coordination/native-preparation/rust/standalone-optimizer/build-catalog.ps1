param([string]$OutputPath = (Join-Path $PSScriptRoot 'catalog.json'))
$ErrorActionPreference='Stop'
$workspacePath = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../../..'))
$factsPath = Join-Path $workspacePath 'RE-evidence/20260912-combat'
$tablesPath = Join-Path $workspacePath 'RE-evidence/20260911-treasure/xls-original/English.lproj'
$sourceHashes = [ordered]@{}
function Read-Fact([string]$Name) {
    $path=Join-Path $factsPath $Name
    $sourceHashes[$Name]=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    return Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
}
function Read-Table([string]$Name) {
    $path=Join-Path $tablesPath ($Name+'.txt')
    $sourceHashes[$Name+'.txt']=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    $result=[ordered]@{}
    foreach($line in [IO.File]::ReadAllLines($path)) {
        $row=$line.Split([char]9)
        if($row[0] -match '^\d+$'){$result[$row[0]]=$row}
    }
    return $result
}
$formation=Read-Fact 'formation-rules.json'
$profiles=Read-Fact 'weapon-skill-profiles.json'
$encounters=Read-Fact 'encounters.json'
$animations=Read-Fact 'animation-resources.json'
$effects=Read-Fact 'effect-resource-checks.json'
$constants=Read-Fact 'skill-combat-constants.json'
$monsters=Read-Table 'Monster'
$treasures=Read-Table 'Treasure'
$prizes=[ordered]@{}
foreach($encounter in $encounters.encounters) {
    $values=@(foreach($key in $treasures.Keys){if([int]$treasures[$key][4] -eq [int]$encounter.rewardGroup){[int]$key}})
    $prizes[[string]$encounter.id]=$values
}
$catalog=[ordered]@{
    schema='ka-rust-static-catalog-v1'
    formationPriorities=$formation.priorities
    profiles=$profiles
    skillRows=$profiles.skills
    encounterTables=$encounters
    animationResources=$animations
    effectResources=$effects
    constants=$constants
    monsterTable=$monsters
    prizeCandidates=$prizes
    sourceSha256=$sourceHashes
}
$outputAbsolute=[IO.Path]::GetFullPath($OutputPath)
[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($outputAbsolute)) | Out-Null
[IO.File]::WriteAllText($outputAbsolute,($catalog|ConvertTo-Json -Depth 100 -Compress),[Text.UTF8Encoding]::new($false))
Write-Output $outputAbsolute
