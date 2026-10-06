param([Parameter(Mandatory=$true)][string]$StatusPath,[Parameter(Mandatory=$true)][int]$JobProcessId)
# Independent progress window: closing this monitor leaves the optimizer running.
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$form=[Windows.Forms.Form]::new()
$form.Text='Rust optimizer progress'; $form.Size=[Drawing.Size]::new(620,310)
$form.StartPosition='CenterScreen'
$label=[Windows.Forms.Label]::new();$label.Location=[Drawing.Point]::new(20,20);$label.Size=[Drawing.Size]::new(565,180)
$label.Text='Starting. Progress total and ETA unknown.'
$bar=[Windows.Forms.ProgressBar]::new();$bar.Location=[Drawing.Point]::new(20,205);$bar.Size=[Drawing.Size]::new(565,24)
$form.Controls.Add($label);$form.Controls.Add($bar)
$timer=[Windows.Forms.Timer]::new();$timer.Interval=500
$timer.Add_Tick({
    try {
        if(Test-Path -LiteralPath $StatusPath){
            $state=Get-Content -LiteralPath $StatusPath -Raw | ConvertFrom-Json
            $elapsed=[double]$state.elapsedSeconds
            $rate=if($elapsed -gt 0){'{0:N2}' -f ([double]$state.completedTrials/$elapsed)}else{'unknown'}
            $total=[double]$state.generationTrialsTotal
            $done=[double]$state.generationTrialsDone
            $percent=if($total -gt 0){[Math]::Min(100,[int](100*$done/$total))}else{0}
            $bar.Value=$percent
            $progress=if($total -gt 0){"Generation trials: $done / $total ($percent%)"}else{'Generation total: unknown'}
            $label.Text="Stage: $($state.stage)`r`nGeneration: $($state.generation) / $($state.generations)`r`n$progress`r`nSaved: $($state.completedTrials) | Reused: $($state.reusedTrials) | Rejected: $($state.rejectedTrials)`r`nElapsed: $([int]$elapsed) s | Overall saved rate: $rate/s | ETA: unknown`r`nOutput: $($state.output)"
        }
        if(-not(Get-Process -Id $JobProcessId -ErrorAction SilentlyContinue)){
            $timer.Stop()
            if($state.stage -ne 'complete'){$label.Text+="`r`nProcess exited. Read stderr.log and rejection details for outcome."}
        }
    }catch{$label.Text="Reading durable progress: $($_.Exception.Message)"}
})
$timer.Start();[void]$form.ShowDialog()
