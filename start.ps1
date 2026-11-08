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

$FreeSurferLicense = Join-Path $Root 'config\license.txt'
$LicenseWizard = Join-Path $Root 'scripts\license_setup.py'

function Invoke-LicenseSetup {
    if (-not (Test-Path -LiteralPath $LicenseWizard)) {
        Write-Host 'ERROR: scripts\license_setup.py is missing; run git pull.' -ForegroundColor Red
        return $false
    }
    $params = @($Prefix) + @($LicenseWizard, 'setup')
    & $Python @params
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'FreeSurfer license setup not completed.' -ForegroundColor Yellow
        return $false
    }
    return $true
}

function Invoke-Pipeline([string]$Action, [string]$Study) {
    if ($Action -in @('preflight', 'recon', 'all') -and -not (Test-Path -LiteralPath $FreeSurferLicense)) {
        Write-Host 'FreeSurfer license is not installed; starting one-time setup.' -ForegroundColor Yellow
        $licenseReady = Invoke-LicenseSetup
        if (-not $licenseReady) {
            Read-Host 'Press Enter to continue' | Out-Null
            return
        }
    }
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
    Write-Host 'Place original DICOM folders (SE000000 etc.) under MRI\<study>\ or add T1.nii.gz'
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
        Write-Host '1. Stage 0: Convert nested DICOM series to 3D T1 NIfTI'
        Write-Host '2. Preflight (T1, Docker, FreeSurfer license)'
        Write-Host '3. Stage 1: FreeSurfer recon-all'
        Write-Host '4. Stage 2: Automatic structural QC'
        Write-Host '5. Stage 3: DunedinPACNI score'
        Write-Host '6. Full pipeline (DICOM import if needed -> recon -> QC -> PACNI)'
        Write-Host '7. Show study statuses'
        Write-Host '8. Compare acquisition metadata across years'
        Write-Host '9. Set up/check FreeSurfer license (one time)'
        Write-Host '0. Back'
        $action = Read-Host 'Choose action'
        if ($action -eq '0') { break }
        switch ($action) {
            '1' { Invoke-Pipeline 'convert' $Study }
            '2' { Invoke-Pipeline 'preflight' $Study }
            '3' { Invoke-Pipeline 'recon' $Study }
            '4' { Invoke-Pipeline 'qc' $Study }
            '5' { Invoke-Pipeline 'score' $Study }
            '6' { Invoke-Pipeline 'all' $Study }
            '7' { Invoke-Pipeline 'list' $Study }
            '8' { Invoke-Pipeline 'compare' $Study }
            "9" { $ok = Invoke-LicenseSetup; Read-Host 'Press Enter to continue' | Out-Null }
        }
    }
}
