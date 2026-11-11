# Real launcher, mocked Python backends. Never touches actual patient records.
$ErrorActionPreference = 'Stop'
$launcher = Join-Path (Split-Path -Parent $PSScriptRoot) 'start.ps1'
$global:MenuTestPatientId = 'p_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
$global:MenuTestMRI = @('2015.07 TEST MRI', '2018.05 TEST MRI', '2021.08 TEST MRI')
$global:MenuTestCount = 0
$global:MenuTestAnswers = @()
$global:MenuTestIndex = 0

function global:py {
    $tokens = @($args)
    if ($tokens.Count -lt 3) { throw 'Unexpected Python launcher arguments' }
    $scriptName = [System.IO.Path]::GetFileName([string]$tokens[1])
    $command = [string]$tokens[2]
    $global:LASTEXITCODE = 0
    if ($scriptName -eq 'patient_cli.py') {
        if ($command -eq 'menu') {
            $data = @{
                patients = @(@{ id = $global:MenuTestPatientId; label = 'TEST ALPHA'; study_count = $global:MenuTestCount })
                unassigned_legacy_studies = @()
                legacy_owner = $null
            }
            return (ConvertTo-Json -InputObject $data -Depth 7 -Compress)
        }
        if ($command -eq 'studies-menu') {
            if ([string]$tokens[3] -ne $global:MenuTestPatientId) {
                throw 'Selected patient ID was not preserved'
            }
            $studies = @()
            for ($i = 0; $i -lt $global:MenuTestCount; $i++) {
                $studies += @{
                    name = $global:MenuTestMRI[$i]
                    root = 'C:\TEST\MRI'
                    patient_id = $global:MenuTestPatientId
                    storage_patient_id = ''
                    legacy = $true
                }
            }
            $data = @{
                patient_id = $global:MenuTestPatientId
                count = $global:MenuTestCount
                studies = $studies
            }
            return (ConvertTo-Json -InputObject $data -Depth 7 -Compress)
        }
        throw ('Unexpected patient command: ' + $command)
    }
    if ($scriptName -eq 'mri_pipeline.py' -and $command -eq 'list') {
        return 'Synthetic study statuses available'
    }
    throw ('Unexpected backend invocation: ' + $scriptName + ' ' + $command)
}

function global:Read-Host {
    param([string]$Prompt)
    if ($global:MenuTestIndex -ge $global:MenuTestAnswers.Count) {
        throw ('MRI menu requested unexpected extra input: ' + $Prompt)
    }
    $answer = $global:MenuTestAnswers[$global:MenuTestIndex]
    $global:MenuTestIndex++
    return $answer
}

function global:Clear-Host {}

function Test-MenuCase {
    param([int]$Count, [string[]]$Inputs, [string]$ChosenMRI)
    $global:MenuTestCount = $Count
    $global:MenuTestAnswers = @($Inputs)
    $global:MenuTestIndex = 0
    $display = (& $launcher 6>&1 | Out-String)
    if ($global:MenuTestIndex -ne $Inputs.Count) {
        throw ('Menu stopped too early: ' + $global:MenuTestIndex + ' of ' + $Inputs.Count)
    }
    if ($display -notmatch 'DunedinPACNI - Patients' -or
        $display -notmatch 'Patient: TEST ALPHA' -or
        $display -notmatch 'Level 2 of 3: MRI studies' -or
        $display -notmatch ('Attached MRI studies: ' + $Count)) {
        throw ('Patient/MRI level missing for ' + $Count + ' studies: ' + $display)
    }
    for ($i = 0; $i -lt $Count; $i++) {
        if (-not $display.Contains($global:MenuTestMRI[$i])) {
            throw ('Attached MRI ' + $i + ' is not selectable in patient screen')
        }
    }
    if ($ChosenMRI) {
        if (-not $display.Contains('MRI pipeline: ' + $ChosenMRI)) {
            throw ('MRI processing level was not reached for ' + $ChosenMRI)
        }
        foreach ($expected in @('1. Stage 0:', '6. Full pipeline',
                               '11. Upscale your MRI', '12. Process upscaled MRI',
                               '13. Show upscaled MRI report', '0. Back to patient MRI list')) {
            if (-not $display.Contains($expected)) {
                throw ('Original MRI pipeline action missing: ' + $expected)
            }
        }
    }
    [Console]::WriteLine('PASS: ' + $Count + ' patient MRI studies')
}

Test-MenuCase -Count 0 -Inputs @('1', '0', '0') -ChosenMRI ''
Test-MenuCase -Count 1 -Inputs @('1', '1', '0', '0', '0') -ChosenMRI $global:MenuTestMRI[0]
Test-MenuCase -Count 3 -Inputs @('1', '2', '0', '0', '0') -ChosenMRI $global:MenuTestMRI[1]

Remove-Item Function:\py -ErrorAction SilentlyContinue
Remove-Item Function:\Read-Host -ErrorAction SilentlyContinue
Remove-Item Function:\Clear-Host -ErrorAction SilentlyContinue
