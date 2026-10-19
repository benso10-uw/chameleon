# Run engine.temp_main against sample 1 (level1 warmup).
#
#   .\run_sample1.ps1                 # prompts for the aspect ratio
#   .\run_sample1.ps1 16:9            # resize to 16:9
#   .\run_sample1.ps1 4:5 -w 1200     # extra args go straight to temp_main

param(
    [string]$Ratio,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

$cliArgs = @("-m", "engine.temp_main", "-f", "chameleon_sample_level1_warmup")
if ($Ratio) { $cliArgs += @("-r", $Ratio) }
if ($Rest) { $cliArgs += $Rest }

Push-Location $root
try {
    & $python @cliArgs
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
