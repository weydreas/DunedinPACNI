$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Backend = Join-Path $Root 'scripts\mri_pipeline.py'
$Mri = Join-Path $Root 'MRI'
if (-not (Test-Path -LiteralPath $Mri)) { New-Item -ItemType Directory -Path $Mri | Out-Null }
$Python = $null
$Prefix = @()
if (Get-Command py -ErrorAction SilentlyContinue) { $Python = 'py'; $Prefix = @('-3') }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $Python = 'python' }
if (-not $Python) { Write-Host 'ERROR: Python 3 is required.' -ForegroundColor Red; exit 1 }

function Invoke-Pipeline([string]$Action, [string]$Study) {
    $argsToPass = @($Prefix) + @($Backend, $Action, $Study)
    & $Python @argsToPass
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Step $Action failed (exit $LASTEXITCODE). See error above." -ForegroundColor Red
    } else {
        Write-Host "Step $Action finished." -ForegroundColor Green
    }
    Read-Host 'Press Enter to continue' | Out-Null
}
while ($true) {
    Clear-Host
    Write-Host 'DunedinPACNI - Local MRI Processing' -ForegroundColor Cyan
    Write-Host "Project: $Root"
    Write-Host 'Add each study under MRI\<scan-name>\T1.nii.gz'
    Write-Host ''
    $Studies = @(Get-ChildItem -LiteralPath $Mri -Directory | Sort-Object Name)
    if ($Studies.Count -eq 0) { Write-Host 'No MRI studies found.' -ForegroundColor Yellow }
    for ($i = 0; $i -lt $Studies.Count; $i++) {
        Write-Host ("{0}. {1}" -f ($i + 1), $Studies[$i].Name)
    }
    Write-Host '0. Exit'
    $pick = Read-Host 'Select study'
    if ($pick -eq '0') { break }
    $number = 0
    if (-not [int]::TryParse($pick, [ref]$number) -or $number -lt 1 -or $number -gt $Studies.Count) { continue }
    $Study = $Studies[$number - 1].Name
    while ($true) {
        Clear-Host
        Write-Host "Selected MRI: $Study" -ForegroundColor Cyan
        Write-Host '1. Preflight (input, Docker, license)'
        Write-Host '2. Stage 1: FreeSurfer recon-all'
        Write-Host '3. Stage 2: Automatic structural QC'
        Write-Host '4. Stage 3: DunedinPACNI score'
        Write-Host '5. Full pipeline (1 -> 2 -> 3)'
        Write-Host '6. Show study statuses'
        Write-Host '0. Back'
        $action = Read-Host 'Choose action'
        if ($action -eq '0') { break }
        switch ($action) {
            '1' { Invoke-Pipeline 'preflight' $Study }
            '2' { Invoke-Pipeline 'recon' $Study }
            '3' { Invoke-Pipeline 'qc' $Study }
            '4' { Invoke-Pipeline 'score' $Study }
            '5' { Invoke-Pipeline 'all' $Study }
            '6' { Invoke-Pipeline 'list' $Study }
        }
    }
}
