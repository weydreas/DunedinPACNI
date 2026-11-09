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
    if ($Action -in @('preflight', 'recon', 'all', 'all-synthsr') -and -not (Test-Path -LiteralPath $FreeSurferLicense)) {
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

$PatientBackend = Join-Path $Root 'scripts\patient_cli.py'

function Get-PatientState {
    $argsToPass = @($Prefix) + @($PatientBackend, 'menu')
    $json = (& $Python @argsToPass) -join ''
    if ($LASTEXITCODE -ne 0) { throw 'Patient registry cannot be read' }
    return ConvertFrom-Json -InputObject $json
}

function Get-PatientScans([string]$PatientId) {
    $argsToPass = @($Prefix) + @($PatientBackend, 'studies', $PatientId)
    $json = (& $Python @argsToPass) -join ''
    if ($LASTEXITCODE -ne 0) { throw 'Patient MRI list cannot be read' }
    return ConvertFrom-Json -InputObject $json
}

function Invoke-PatientAction([string]$Action, [string]$First, [string]$Second) {
    $argsToPass = @($Prefix) + @($PatientBackend, $Action)
    if ($First) { $argsToPass += $First }
    if ($Second) { $argsToPass += $Second }
    & $Python @argsToPass
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Operation $Action was rejected; source files were preserved." -ForegroundColor Yellow
    } else {
        Write-Host "Operation $Action finished." -ForegroundColor Green
    }
    Read-Host 'Press Enter to continue' | Out-Null
}

function Show-StudyMenu([object]$SelectedScan) {
    $oldMriRoot = $env:PACNI_MRI_ROOT
    $oldPatientId = $env:PACNI_PATIENT_ID
    try {
        $env:PACNI_MRI_ROOT = [string]$SelectedScan.root
        if ($SelectedScan.storage_patient_id) {
            $env:PACNI_PATIENT_ID = [string]$SelectedScan.storage_patient_id
        } else {
            Remove-Item Env:PACNI_PATIENT_ID -ErrorAction SilentlyContinue
        }
        $Study = [string]$SelectedScan.name
        while ($true) {
            Clear-Host
            Write-Host "Selected MRI: $Study" -ForegroundColor Cyan
            $statusArgs = @($Prefix) + @($Backend, 'list')
            & $Python @statusArgs
            Write-Host ''
            Write-Host '1. Stage 0: Convert nested DICOM series to 3D T1 NIfTI'
            Write-Host '2. Preflight (T1, Docker, FreeSurfer license)'
            Write-Host '3. Stage 1: FreeSurfer recon-all'
            Write-Host '4. Stage 2: Automatic structural QC'
            Write-Host '5. Stage 3: DunedinPACNI score'
            Write-Host '6. Full pipeline (DICOM -> recon -> QC -> PACNI)'
            Write-Host '7. Show study statuses'
            Write-Host '8. Compare acquisition metadata across years'
            Write-Host '9. Set up/check FreeSurfer license (one time)'
            Write-Host '10. Show study report and DunedinPACNI conclusion'
            Write-Host '11. Upscale your MRI (SynthSR: SYNTHETIC, not diagnostic)'
            Write-Host '12. Process upscaled MRI (research-only PACNI, no GWR)'
            Write-Host '13. Show upscaled MRI report (research-only)'
            Write-Host '0. Back to patient'
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
                '9' { $ok = Invoke-LicenseSetup; Read-Host 'Press Enter to continue' | Out-Null }
                '10' { Invoke-Pipeline 'report' $Study }
                '11' { Invoke-Pipeline 'upscale' $Study }
                '12' { Invoke-Pipeline 'all-synthsr' $Study }
                '13' { Invoke-Pipeline 'report-synthsr' $Study }
            }
        }
    } finally {
        if ($null -eq $oldMriRoot) { Remove-Item Env:PACNI_MRI_ROOT -ErrorAction SilentlyContinue }
        else { $env:PACNI_MRI_ROOT = $oldMriRoot }
        if ($null -eq $oldPatientId) { Remove-Item Env:PACNI_PATIENT_ID -ErrorAction SilentlyContinue }
        else { $env:PACNI_PATIENT_ID = $oldPatientId }
    }
}

function Show-PatientMenu([object]$SelectedPatient) {
    $PatientId = [string]$SelectedPatient.id
    while ($true) {
        Clear-Host
        Write-Host "Patient: $($SelectedPatient.label)" -ForegroundColor Cyan
        $scans = @(Get-PatientScans $PatientId)
        for ($i = 0; $i -lt $scans.Count; $i++) {
            $legacyNote = if ($scans[$i].legacy) { ' [legacy]' } else { '' }
            Write-Host ("{0}. {1}{2}" -f ($i + 1), $scans[$i].name, $legacyNote)
        }
        Write-Host 'M. Merge with another patient (persistent aliases, no file deletion)'
        Write-Host 'C. Import a selected DICOM series from a public cloud link'
        Write-Host '0. Back to Patients'
        $pick = Read-Host 'Choose'
        if ($pick -eq '0') { break }
        if ($pick -eq 'M') {
            $state = Get-PatientState
            $others = @($state.patients | Where-Object { $_.id -ne $PatientId })
            for ($j = 0; $j -lt $others.Count; $j++) {
                Write-Host ("{0}. {1}" -f ($j + 1), $others[$j].label)
            }
            if ($others.Count -eq 0) { Read-Host 'No other patients; Enter' | Out-Null; continue }
            $choice = Read-Host 'Choose source patient number'
            $index = 0
            if (-not [int]::TryParse($choice, [ref]$index) -or $index -lt 1 -or $index -gt $others.Count) { continue }
            $other = $others[$index - 1]
            Write-Host "Merge '$($other.label)' into '$($SelectedPatient.label)'?"
            if ((Read-Host 'Type MERGE to confirm') -eq 'MERGE') {
                Invoke-PatientAction 'merge' $PatientId ([string]$other.id)
            }
            continue
        }
        if ($pick -eq 'C') {
            Write-Host 'Cloud import support is invoked from a separate safely validated backend.'
            $sharedLink = Read-Host 'Public read-only HTTPS link'
            if (-not $sharedLink) { continue }
            $expiry = Read-Host 'Last access date YYYY-MM-DD (link owner must also set expiry)'
            $env:PACNI_CLOUD_LINK = $sharedLink
            try {
                $importArgs = @($Prefix) + @(Join-Path $Root 'scripts\remote_mri.py', 'interactive', $PatientId, $expiry)
                & $Python @importArgs
                if ($LASTEXITCODE -ne 0) { Write-Host 'Cloud import was not completed.' -ForegroundColor Yellow }
            } finally {
                Remove-Item Env:PACNI_CLOUD_LINK -ErrorAction SilentlyContinue
            }
            Read-Host 'Press Enter to continue' | Out-Null
            continue
        }
        $index = 0
        if ([int]::TryParse($pick, [ref]$index) -and $index -gt 0 -and $index -le $scans.Count) {
            Show-StudyMenu $scans[$index - 1]
        }
    }
}

while ($true) {
    Clear-Host
    Write-Host 'DunedinPACNI - Patients' -ForegroundColor Cyan
    Write-Host "Project: $Root"
    Write-Host ''
    try {
        $state = Get-PatientState
    } catch {
        Write-Host "ERROR reading local patient registry: $_" -ForegroundColor Red
        Read-Host 'Press Enter to retry' | Out-Null
        continue
    }
    $persons = @($state.patients)
    for ($i=0; $i -lt $persons.Count; $i++) {
        Write-Host ("{0}. {1} ({2} MRI)" -f ($i+1), $persons[$i].label, $persons[$i].study_count)
    }
    Write-Host 'N. Create patient (letters, numbers and spaces)'
    if ($state.unassigned_legacy_studies.Count -gt 0) {
        Write-Host ("A. Assign {0} existing legacy MRI folders to a patient" -f $state.unassigned_legacy_studies.Count)
    }
    Write-Host 'D. Discover patient names from local DICOM headers (optional pydicom)'
    Write-Host '0. Exit'
    $pick = Read-Host 'Select patient or action'
    if ($pick -eq '0') { break }
    if ($pick -eq 'N') {
        $label = Read-Host 'Patient surname/initials or anonymous code'
        if ($label) { Invoke-PatientAction 'create' $label '' }
        continue
    }
    if ($pick -eq 'A' -and $state.unassigned_legacy_studies.Count -gt 0) {
        for ($i=0; $i -lt $persons.Count; $i++) {
            Write-Host ("{0}. {1}" -f ($i+1), $persons[$i].label)
        }
        $choice = Read-Host 'Assign all existing legacy MRI folders to patient number'
        $index = 0
        if ([int]::TryParse($choice, [ref]$index) -and $index -ge 1 -and $index -le $persons.Count) {
            if ((Read-Host 'Type ASSIGN to confirm attribution') -eq 'ASSIGN') {
                Invoke-PatientAction 'attach-legacy' ([string]$persons[$index-1].id) ''
            }
        }
        continue
    }
    if ($pick -eq 'D') { Invoke-PatientAction 'discover' '' ''; continue }
    $index = 0
    if ([int]::TryParse($pick, [ref]$index) -and $index -ge 1 -and $index -le $persons.Count) {
        Show-PatientMenu $persons[$index - 1]
    }
}
