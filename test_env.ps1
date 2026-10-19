# Check that the .venv is on Python 3.10 and the project actually works in it.
#
#   .\test_env.ps1              # version, deps, imports, pytest
#   .\test_env.ps1 -Smoke       # also run every input/ task (preset logic, no LLM)

param(
    [switch]$Smoke
)

$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$failed = 0

function Step([string]$name, [scriptblock]$body) {
    Write-Host "`n=== $name ===" -ForegroundColor Cyan
    & $body
    if ($LASTEXITCODE -eq 0) {
        Write-Host "PASS" -ForegroundColor Green
    } else {
        Write-Host "FAIL (exit $LASTEXITCODE)" -ForegroundColor Red
        $script:failed++
    }
}

if (-not (Test-Path $python)) {
    Write-Host ".venv not found - create it with: py -3.10 -m venv .venv" -ForegroundColor Red
    exit 1
}

Push-Location $root
try {
    Step "Python is 3.10" {
        & $python -c "import sys; print(sys.version); sys.exit(sys.version_info[:2] != (3, 10))"
    }

    Step "Dependencies are consistent" {
        & $python -m pip check
    }

    Step "Key packages import" {
        & $python -c "import PIL, skia, cr_renderer, pydantic, engine; print('Pillow', PIL.__version__, '| skia', skia.__version__, '| pydantic', pydantic.VERSION)"
    }

    Step "Test suite" {
        & $python -m pytest -q
    }

    if ($Smoke) {
        Step "Smoke run: every task, no model" {
            & (Join-Path $root "run_task.ps1") -All -Trace -NoModel
        }
    }
} finally {
    Pop-Location
}

if ($failed) {
    Write-Host "`n$failed check(s) failed" -ForegroundColor Red
    exit 1
}
Write-Host "`nAll checks passed" -ForegroundColor Green
exit 0
