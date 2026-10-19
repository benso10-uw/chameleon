# Run ONE task JSON from input/ at its own target_canvas.
#
#   .\run_task.ps1                          # list input/*.json and pick one
#   .\run_task.ps1 2                        # task #2 from that list
#   .\run_task.ps1 chameleon_sample_level2_core
#   .\run_task.ps1 2 -Trace                 # also write a trace + HTML report
#   .\run_task.ps1 2 -Trace -NoModel        # trace, preset logic only (no LLM)
#   .\run_task.ps1 -All                     # every task, one at a time
#
# Plain runs go through engine.temp_main (output/<task>_<W>x<H>_output.{json,png}).
# -Trace runs go through engine.tracing.trace_run (output/traces/<task>.json,
# output/traces/<task>/*.png, output/traces/index.html). index.html only covers
# the task(s) of the latest traced run.

param(
    [string]$Task,
    [switch]$Trace,
    [switch]$NoModel,
    [switch]$All
)

$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

$taskFiles = @(Get-ChildItem (Join-Path $root "input") -Filter *.json | Sort-Object Name)
if ($taskFiles.Count -eq 0) {
    Write-Error "No task files found in input/"
    exit 1
}

function Resolve-Task([string]$choice) {
    if (-not $choice) {
        Write-Host "Task files in input/:"
        for ($i = 0; $i -lt $taskFiles.Count; $i++) {
            Write-Host ("  {0}. {1}" -f ($i + 1), $taskFiles[$i].Name)
        }
        $choice = Read-Host "Pick a file [1]"
        if (-not $choice) { $choice = "1" }
    }
    if ($choice -match '^\d+$') {
        $idx = [int]$choice
        if ($idx -ge 1 -and $idx -le $taskFiles.Count) { return $taskFiles[$idx - 1] }
        throw "No file #$idx (have 1-$($taskFiles.Count))"
    }
    $match = $taskFiles | Where-Object { $_.Name -eq $choice -or $_.BaseName -eq $choice }
    if (-not $match) { throw "No task file named '$choice' in input/" }
    return $match
}

function Invoke-Task($file) {
    $target = (Get-Content $file.FullName -Raw | ConvertFrom-Json).target_canvas
    $w, $h = $target.width, $target.height
    Write-Host "`n=== $($file.Name) -> ${w}x${h} ===" -ForegroundColor Cyan

    if ($Trace) {
        $cliArgs = @("-m", "engine.tracing.trace_run", "input/$($file.Name)")
        if ($NoModel) { $cliArgs += "--no-model" }
    } else {
        $cliArgs = @("-m", "engine.temp_main", "-f", $file.BaseName,
                     "-r", "${w}:${h}", "-w", "$w", "-H", "$h")
    }
    & $python @cliArgs | Out-Host
    return $LASTEXITCODE
}

Push-Location $root
try {
    $failed = 0
    $selected = if ($All) { $taskFiles } else { @(Resolve-Task $Task) }
    foreach ($file in $selected) {
        if ((Invoke-Task $file) -ne 0) { $failed++ }
    }
    if ($failed) { Write-Host "`n$failed task(s) exited non-zero" -ForegroundColor Red }
    exit [int]($failed -gt 0)
} finally {
    Pop-Location
}
