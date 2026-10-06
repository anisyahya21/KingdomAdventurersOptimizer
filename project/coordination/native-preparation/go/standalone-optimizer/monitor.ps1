param(
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [int]$RefreshMilliseconds = 1000
)

$ErrorActionPreference = 'SilentlyContinue'
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputDir)
$statusPath = Join-Path $resolvedOutput 'status.json'
$resultPath = Join-Path $resolvedOutput 'results.jsonl'
$rejectionPath = Join-Path $resolvedOutput 'rejections.jsonl'

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$form = New-Object System.Windows.Forms.Form
$form.Text = 'Go optimizer progress'
$form.StartPosition = 'CenterScreen'
$form.Size = New-Object System.Drawing.Size(760, 460)
$form.MinimumSize = New-Object System.Drawing.Size(600, 360)
$form.TopMost = $false

$layout = New-Object System.Windows.Forms.TableLayoutPanel
$layout.Dock = 'Fill'
$layout.ColumnCount = 1
$layout.RowCount = 5
$layout.Padding = New-Object System.Windows.Forms.Padding(12)
$layout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::AutoSize))) | Out-Null
$layout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 30))) | Out-Null
$layout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::AutoSize))) | Out-Null
$layout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Percent, 100))) | Out-Null
$layout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::AutoSize))) | Out-Null
$form.Controls.Add($layout)

$summary = New-Object System.Windows.Forms.Label
$summary.AutoSize = $true
$summary.Dock = 'Fill'
$summary.Font = New-Object System.Drawing.Font('Segoe UI', 10)
$summary.Text = "Waiting for status.json`r`nOutput: $resolvedOutput"
$layout.Controls.Add($summary, 0, 0)

$progress = New-Object System.Windows.Forms.ProgressBar
$progress.Dock = 'Fill'
$progress.Minimum = 0
$progress.Maximum = 1000
$layout.Controls.Add($progress, 0, 1)

$toggle = New-Object System.Windows.Forms.Button
$toggle.Text = 'Show details'
$toggle.AutoSize = $true
$toggle.Anchor = 'Left'
$layout.Controls.Add($toggle, 0, 2)

$details = New-Object System.Windows.Forms.TextBox
$details.Multiline = $true
$details.ReadOnly = $true
$details.ScrollBars = 'Vertical'
$details.WordWrap = $false
$details.Dock = 'Fill'
$details.Visible = $false
$details.Font = New-Object System.Drawing.Font('Consolas', 9)
$layout.Controls.Add($details, 0, 3)

$location = New-Object System.Windows.Forms.Label
$location.AutoSize = $true
$location.Dock = 'Fill'
$location.Text = "Status: $statusPath"
$layout.Controls.Add($location, 0, 4)

$toggle.Add_Click({
    $details.Visible = -not $details.Visible
    $toggle.Text = if ($details.Visible) { 'Hide details' } else { 'Show details' }
})

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = [Math]::Max(250, $RefreshMilliseconds)
$script:rateBaselineCount = $null
$script:rateBaselineTime = $null
$timer.Add_Tick({
    try {
        if (-not (Test-Path -LiteralPath $statusPath)) { return }
        $status = Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json
        $completed = [double]$status.DurablyCompleted
        $rejected = [double]$status.Rejected
        $duplicates = [double]$status.DuplicateSkips
        # Rejections and reused duplicate observations consume search budget too.
        $progressCount = $completed + $rejected + $duplicates
        $total = [double]$status.Total
        $elapsed = [double]$status.ElapsedSeconds
        if ([string]$status.Stage -notin @('complete','failed','paused','stopped')) {
            $elapsed += [Math]::Max(0, ([DateTime]::UtcNow - (Get-Item -LiteralPath $statusPath).LastWriteTimeUtc).TotalSeconds)
        }
        $hasTotal = $total -gt 0
        $percentText = 'unknown'
        $etaText = 'unknown'
        if ($hasTotal) {
            $fraction = [Math]::Max(0, [Math]::Min(1, $progressCount / $total))
            $progress.Value = [int]([Math]::Round($fraction * $progress.Maximum))
            $percentText = '{0:P1}' -f $fraction
        } else {
            $progress.Value = 0
        }
        $rateText = 'unknown'
        # Checkpoint counters span restarts, while runner elapsed is per launch.
        # Measure a real observed interval so resumed totals cannot inflate rate.
        $observedAt = [DateTime]::UtcNow
        if ($null -eq $script:rateBaselineCount -or $progressCount -lt $script:rateBaselineCount) {
            $script:rateBaselineCount = $progressCount
            $script:rateBaselineTime = $observedAt
        }
        $observedSeconds = ($observedAt - $script:rateBaselineTime).TotalSeconds
        if ($observedSeconds -gt 0) {
            $observedRate = ($progressCount - $script:rateBaselineCount) / $observedSeconds
            $rateText = '{0:N2}/s (observed completed + rejected + reused)' -f $observedRate
            if ($hasTotal -and $observedRate -gt 0) {
                $etaText = '{0:d\.hh\:mm\:ss} (provisional)' -f [TimeSpan]::FromSeconds([Math]::Max(0, $total - $progressCount) / $observedRate)
            } else { $etaText = 'unknown' }
        } else { $etaText = 'unknown' }
        $stage = [string]$status.Stage
        $warnings = @()
        if ($rejected -gt 0) { $warnings += "rejected=$rejected" }
        if ($status.Errors -gt 0) { $warnings += "errors=$($status.Errors)" }
        $warningText = if ($warnings.Count) { $warnings -join ', ' } else { 'none reported' }
        $summary.Text = "Stage: $stage`r`nEvaluations: $progressCount / $(if ($hasTotal) {$total} else {'unknown'}) ($percentText)    Elapsed: $([TimeSpan]::FromSeconds([Math]::Max(0,$elapsed)).ToString('d\.hh\:mm\:ss'))`r`nRate: $rateText    ETA: $etaText`r`nUnique durable results: $completed    Reused: $duplicates    Warnings: $warningText`r`nDetail: $($status.Detail)"
        if ($details.Visible) {
            $extra = @()
            $extra += ("status.json:`r`n" + ($status | ConvertTo-Json -Depth 12))
            if (Test-Path -LiteralPath $resultPath) { $extra += "`r`nresults.jsonl (last 40 durable records):`r`n" + ((Get-Content -LiteralPath $resultPath -Tail 40) -join "`r`n") }
            if (Test-Path -LiteralPath $rejectionPath) { $extra += "`r`nrejections.jsonl (last 100):`r`n" + ((Get-Content -LiteralPath $rejectionPath -Tail 100) -join "`r`n") }
            $failureFiles = Get-ChildItem -LiteralPath $resolvedOutput -Filter 'failure-*.json' -File | Sort-Object LastWriteTime -Descending | Select-Object -First 20
            foreach ($failureFile in $failureFiles) { $extra += "`r`n$($failureFile.Name):`r`n" + (Get-Content -LiteralPath $failureFile.FullName -Raw) }
            $details.Text = $extra -join "`r`n"
        }
        $location.Text = "Status: $statusPath`r`nOutput: $($status.Output)"
    } catch {
        $summary.Text = "Reading status.json; the runner remains independent.`r`n$($_.Exception.Message)`r`nOutput: $resolvedOutput"
    }
})

# Closing this window only disposes the monitor; it never stops or signals the optimizer.
$form.Add_FormClosed({ $timer.Stop(); $timer.Dispose() })
$timer.Start()
[void]$form.ShowDialog()
