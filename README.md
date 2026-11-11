# DunedinPACNI
DunedinPACNI is novel brain-based aging biomarker of the pace of biological aging. DunedinPACNI scores represent the **speed** of aging, with higher scores indicating faster aging and lower scores indicating slower aging.

### Background
The DunedinPACNI algorithm was designed to predict the longitudinal [Pace of Aging phenotype](https://www.nature.com/articles/s43587-021-00044-4) in the Dunedin Study. Briefly, the Pace of Aging phenotype is a composite of decline in 19 biomarkers of 6 organ systems measured longitudinally as the cohort aged from 26 to 45 years old (panel A-B in figure below).

Using brain MRI scans from the Dunedin Study at age 45, we developed an algorithm to accurately estimate the Pace of Aging phenotype among Dunedin Study members (panel C in figure below). Specifically, the algorithm uses FreeSurfer-derived measures of cortical thickness, cortical surface area, cortical gray matter volume, cortical gray-white signal intensity ratio all according to the Desikan-Killiany parcellation and subcortical and cerebellar volumes according to the FreeSurfer ASEG output.

![this Figure](https://media.springernature.com/full/springer-static/image/art%3A10.1038%2Fs43587-025-00897-z/MediaObjects/43587_2025_897_Fig1_HTML.png?as=webp) 

### How do we know DunedinPACNI works?
In our recent paper, we exported DunedinPACNI to four external neuroimaging samples (Human Connectome Project, Alzheimer's Disease Neuroimaging Initiative, UK Biobank, and BrainLat) to test the utility of DunedinPACNI in new data. We found that DunedinPACNI has excellent test-retest reliability, is associated with cognition, cognitive impairment, predicts cognitive decline, predicts hippocampal atrophy, predicts chronic disease onset, predicts death, is associated with socioeconomic inequality, and generalizes well in Latin American dementia patients (panel D in figure above).

These analyses are published in [our manuscript in Nature Aging](https://www.nature.com/articles/s43587-025-00897-z).

### What do I need to estimate DunedinPACNI?
To estimate DunedinPACNI in new data, you will need brain MRI data parcellated using FreeSurfer. This package can directly read FreeSurfer output into R for FreeSurfer v5, v6, and v7. DunedinPACNI was developed using FreeSurfer v6.0.

If you have data from FreeSurfer v4 or earlier, you might run into bugs due to changes in ROI naming conventions in the newer FreeSurfer versions. To fix this, you can manually format your data into a CSV file to match the newer ROI naming conventions (see section __Steps if you do not have complete recon-all FreeSurfer outputs from your dataset__).

You also need a CSV file with all participant IDs. 

### How can I estimate DunedinPACNI?
Below, we provide instructions for estimating DunedinPACNI in two potential scenarios: 
1. you have access to complete `recon-all` output from FreeSurfer, likely because you ran FreeSurfer yourself.
2. you have access to FreeSurfer measures, but you do not have access to the complete FreeSurfer `recon-all` output. This would likely be because you downloaded FreeSurfer outputs from an open dataset such as ADNI or UK Biobank and have data formatted in a CSV file, or something similar.

You can estimate DunedinPACNI in both cases, but the steps are slightly different.

# Steps if you have complete FreeSurfer recon-all outputs for your dataset

### Step 1 - installation
First, start by using `devtools` to install and load the DunedinPACNI package using the following code:
```
install.packages("devtools")
devtools::install_github("etw11/DunedinPACNI")
library(DunedinPACNI)
```

### Step 2 - participant list
Next, you need to generate a CSV file with all participant IDs that you want to calculate DunedinPACNI scores for. To do this, you could navigate to your FreeSurfer subjects directory and run the following commands into the command line:

```
echo 'ID' > sublist.csv
ls -d */ >> sublist.csv
```

The resulting `sublist.csv` file should look something like this:
```
ID
<subject 1 ID>
<subject 2 ID>
<subject 3 ID>
...
<subject N ID>
```

### Step 3 - load data into R
Next, you can run the function `LoadFreeSurferStats` to quickly read the necessary FreeSurfer data into R.
```
df <- LoadFreeSurferStats(fsdir = '<YOUR FREESURFER SUBJECTS DIRECTORY>', 
                          sublist = '<YOUR WORKING DIRECTORY>/sublist.csv')
```
Where `fsdir` should be the path to your FreeSurfer subjects directory and `sublist` is the path to the CSV file with all participant IDs.

This function outputs a data frame of formatted FreeSurfer phenotypes for all subjects.

This function may take a bit of time if you have a lot of data. For example, loading 40,000 scans takes around 1 hour.

`LoadFreeSurferStats` will throw a warning if there is any missing data and summarize what data are missing (see section __What if I am missing estimates for some ROIs?__).

### Step 4 - estimate DunedinPACNI
Next, you can pass the output from `LoadFreeSurferStats` directly to `ExportDunedinPACNI`.

```
ExportDunedinPACNI(data = df, 
                   outdir = '<YOUR WORKING DIRECTORY>')
```
Where `data` is the output from `LoadFreeSurferStats` and `outdir` is the path to the directory where you want to save DunedinPACNI outputs.


# Steps if you do not have complete recon-all FreeSurfer outputs from your dataset

### Step 1 - installation
First, start by using `devtools` to install and load the DunedinPACNI package using the following code:
```
install.packages("devtools")
devtools::install_github("etw11/DunedinPACNI")
library(DunedinPACNI)
```

### Step 2 - formatting data
Next, you will need to format your data into a CSV file with particular formatting. Specifically, you need to have each row in your CSV file represent a unique scan, and each column with a unique phenotype.

To get a template for this file, run the following commands in R:
```
data(ROI_names)
write.csv(data.frame(ROI_names), file = '<YOUR WORKING DIRECTORY>/data_file.csv', row.names = FALSE)
```
This command will output a template CSV file called `data_file.csv` to the specified directory. You'll need to format your data to match this column order and these column names. It might be helpful to use the `ROI_names` object in R for data warngling. `ROI_names` contains the correct naming conventions for each phenotype and is in the correct order. It is loaded automatically when you install the `DunedinPACNI` package.

**Note** - If you downloaded FreeSurfer data from an open dataset (e.g. ADNI) you may not have access to gray-white signal intensity ratio measures, as these are not always distributed. If so, see below for now to run this package without gray-white signal intensity ratio (see section __What if I am missing gray-white signal intensity ratio measures?__).

### Step 3 - estimate DunedinPACNI
Once your data is formatted into a CSV with correct order and names, you can load this CSV into R and run `ExportDunedinPACNI`.
```
df <- read.csv('<YOUR WORKING DIRECTORY>/data_file.csv')
ExportDunedinPACNI(data = df, 
                   outdir = '<YOUR WORKING DIRECTORY>')
```
Where `outdir` is the path to the directory where you want to save DunedinPACNI outputs.

# Notes

### What if I am missing gray-white signal intensity ratio measures?
DunedinPACNI is valid with or without gray-white signal intensity ratio measures. You can choose which version you want to use based on your data availability and preferences.

To estimate DunedinPACNI without gray-white signal intensity ratio, simly set the `missing_gwr` option to `TRUE` for both `LoadFreeSurferStats` and/or `ExportDuinedinPACNI`. For example, if you are reading FreeSurfer data from the full `recon-all` output, run:
```
LoadFreeSurferStats(fsdir = '<YOUR FREESURFER SUBJECTS DIRECTORY>', 
                    sublist = '<YOUR WORKING DIRECTORY>/sublist.csv',
                    missing_gwr = TRUE)
```

Same for `ExportDunedinPACNI`:
```
ExportDunedinPACNI(data = df, 
                   outdir = '<YOUR WORKING DIRECTORY>', 
                   missing_gwr = TRUE)
```

### What if I am missing estimates for some ROIs?
You may have missing data from certain ROIs because of low data quality or lack of availabiliy. If so, we allow users to impute the average score from the Dunedin Study MRI dataset. Each missing ROI will slightly reduce the accuracy of resulting DunedinPACNI scores. Because the same value is being imputed for all subjects, imputed ROIs should have a uniform effect on DunedinPACNI scores in your sample, to avoid affecting within-group comparisons. If you are missing 20% or more of the ROIs included in the DunedinPACNI algorithm, we think that is too much missingness to estimate DunedinPACNI and `ExportDunedinPACNI` will throw an error.

If you have a lot of missingness, we would encourage you to consider [more sophisticated approaches to imputation](https://link.springer.com/article/10.1007/s12021-019-09426-x). Ultimately, however, how you handle missing data is up to your preferences, the specifics of your data, and your research question.

To run `ExportDunedinPACNI` with missing ROIs, first make sure columns for those phenotypes are removed from the data frame being passed to `ExportDunedinPACNI`. 

Next, find the naming convention for the missing ROIs using `ROI_names`. To check this, run these commands in R:
```
data(ROI_names)
ROI_names
```
This will output the naming covnentions for all ROIs.

Next, run  `ExportDunedinPACNI` while setting the option `missing_ROIs` to a text vector of the missing ROI names. In the following example, `ExportDunedinPACNI` would impute values for right pars operularis gray-white signal intensity ratio and right pars orbitalis gray-white signal intensity ratio.
```
ExportDunedinPACNI(data = df,
                   outdir = '<YOUR WORKING DIRECTORY>',
                   missing_ROIs = c('GWR_parsopercularis_right', 'GWR_parsorbitalis_right'))
```

If you have missing ROIs, this may affect the accuracy of DunedinPACNI scores in your dataset. `ExportDunedinPACNI` will throw a warning with the percentage of ROIs that are missing from the final DunedinPACNI algorithm. Due to elastic net regularization, many ROI values are set to 0 and are not included in the final DunedinPACNI algorithm, so the degree to which missingness affects the DunedinPACNI scores may be different from the overall amount of missingness in your data.

### Version
Current package version 0.0.0.1

This package was built using R version 4.3.2 and uses tools from the R package `progress` (version 1.2.3).

This package was built by Ethan Whitman (ethan.whitman@duke.edu).
# Windows local MRI processing (Biodata launcher)

The pipeline below processes MRI **locally**. Patient images, reconstructed brain data,
FreeSurfer license, and DunedinPACNI results are ignored by Git.

## One-time setup

1. Clone this fork on Windows 11, with Python 3 and Docker Desktop in Linux/WSL2 mode.
2. Open start.cmd and choose action 9 to obtain/install a FreeSurfer license.
   The one-time wizard opens official registration and imports the emailed
   license from Downloads, a file path or pasted license-only text.
3. Create a separate directory for each MRI study:

    MRI/2024-08-02/T1.nii.gz
    MRI/2026-06-01/T1.nii.gz

Each study can instead contain a single other NIfTI/MGZ image, or a dedicated
MRI/2024-08-02/T1/ directory of DICOM images from **one 3D T1 series**.
If a scan folder contains multiple NIfTI volumes, use the exact filename
T1.nii.gz (T1.nii or T1.mgz also work) to select the correct volume.
DICOM folders must never mix distinct SeriesInstanceUIDs; this launcher
does not automatically check DICOM series metadata.

## Interactive launcher

Double-click start.cmd (or run it from PowerShell). Select an MRI study.
The PowerShell menu has numbered actions:

1. **DICOM import:** recursively find the series in MRI/<study>/SE000000,
   SE000002 and any other nested folders, convert each with dcm2niix,
   show sequence names/resolution and ask you to pick and confirm a plausible
   anatomical 3D T1 series. It does not manufacture T1 from FLAIR or T2.
2. Preflight: Docker, license and selected 3D T1.
3. FreeSurfer reconstruction (resume supported if input has not changed).
4. Structural quality gate: completion and region statistics.
5. DunedinPACNI calculation through a local R container.
6. Full pipeline: automatically request DICOM import when T1 is missing,
   then FreeSurfer, structural QC and DunedinPACNI.
7. List all detected studies and their processing statuses.
8. Compare acquisition metadata across scans (where provided).
9. Set up or check a personal FreeSurfer license (one-time).
10. Show a local, read-only study report and research interpretation.
11. **Upscale your MRI (SynthSR):** explicit selection of a converted original
    series, generate synthetic 1mm MPRAGE in a separate local derivative.
12. **Process upscaled MRI:** FreeSurfer → QC → DunedinPACNI on that derivative.
13. Show a separate research report for the synthetic variant.

The first run downloads the pinned freesurfer/freesurfer:7.4.1 Docker image and
builds a local R 4.3.2 image using the code in this fork. The original model
was developed using FreeSurfer 6.0. To compare MRI dates, the images must be
processed with **the same version** and a comparable preprocessing protocol.
Change the FreeSurfer image only intentionally via the PACNI_FS_IMAGE environment
variable. The software refuses to reuse existing reconstruction outputs when
the input or selected FreeSurfer image differs from its provenance.

Outputs appear in results/<subject-id>/pacni.csv, qc.json and run.json.
When gray/white ratio output is unavailable, the official no-GWR coefficients
are used rather than wholesale GWR imputation.

**Limitations:** The automated QC gate verifies only structural output files;
it does not review brain boundaries, surfaces or segmentation visually.
Every score is marked **provisional** until visual FreeSurfer QC by a qualified
reviewer. Cross-scanner differences and processing/version effects can mimic
longitudinal changes; the score is a research phenotype, not a diagnosis.

For CLI automation, run:

    py -3 scripts/mri_pipeline.py list
    py -3 scripts/mri_pipeline.py all 2024-08-02
    py -3 scripts/mri_pipeline.py qc 2024-08-02

Tests without MRI or Docker: python -m unittest discover -s tests -v

## Optional cross-year comparability metadata

To compare MRI studies, place scan.json beside T1.nii.gz in each study
folder. Its fields are scanner_model (string), field_strength_t (number),
sequence (string) and voxel_size_mm (three-number array). Example:

    {"scanner_model":"GE Signa Architect","field_strength_t":3,
     "sequence":"3D MPRAGE","voxel_size_mm":[1,1,1]}

Choose menu action 8. The results/comparability.json file reports differences
as review_required, consistent declared metadata as metadata_consistent_not_validated,
or insufficient_metadata if source metadata are absent. It also compares
recorded FreeSurfer image tags across processed scans. Declared consistency
is not proof of matching image quality or biological comparability.


## Installing the DICOM converter on Windows

The importer requires the open-source program dcm2niix. No Python imaging
packages, R, or FreeSurfer are needed for the **import step itself**.

In PowerShell, open this repository directory and run:

    New-Item -ItemType Directory -Force .\tools | Out-Null
    Invoke-WebRequest 'https://github.com/rordenlab/dcm2niix/releases/latest/download/dcm2niix_win.zip' -OutFile "$env:TEMP\dcm2niix_win.zip"
    Expand-Archive "$env:TEMP\dcm2niix_win.zip" -DestinationPath .\tools -Force
    Get-ChildItem .\tools -Recurse -Filter dcm2niix.exe

If the final command finds dcm2niix.exe, the menu will locate it automatically.
Alternatively, put dcm2niix on PATH or set PACNI_DCM2NIIX to the executable
path. The converter's Windows binaries are released at
https://github.com/rordenlab/dcm2niix/releases .

**Important:** dcm2niix performs DICOM to NIfTI format conversion. A DICOM
dataset containing only T2, FLAIR or other contrast modalities cannot be
converted into the actual T1-weighted acquisition expected by PACNI.

## Importing nested DICOM directories

Original data may be structured like this:

    MRI/
      2015.07 MRI Brain/
        SE000000/
          IM0001.dcm
        SE000002/
          IM0001.dcm
        SE000013/
          IM0001.dcm

Run start.cmd, choose the study and press **1** to convert. The importer
calls dcm2niix with -d 9 to search nested directories, -b y to preserve
BIDS sidecar metadata and -ba y to anonymize JSON sidecars. All converted
series are retained in work/imports/<study-id>/converted, together with a
series-manifest.json identifying likely T1, excluded FLAIR/T2 and uncertain
images. Series metadata and voxel sizes are inspected before any selection.
Only T1-labeled, 3D, single-volume NIfTI-1 images with voxel dimensions no
greater than 2 mm can be selected automatically. Those tests are a *screening
rule*, not proof that an image meets all PACNI acquisition/QC requirements.

You must choose the series number and type YES after reviewing that it is
an anatomical T1 volume. Otherwise the importer does not create the input.
The selected image is copied to MRI/<study>/T1.nii.gz, with T1.json,
pacni-import.json and optional scan.json acquisition metadata alongside it.
Original DICOMs are never moved or changed. If no plausible T1 is found,
the program stops, keeps converted files for inspection, and does not
invent a T1 volume. For uncertain series use 3D Slicer to inspect anatomy.

To run just the conversion from PowerShell:

    py -3 scripts/dicom_import.py "2015.07 MRI Brain"

The converted T1 can then be processed using menu actions 2-6. The complete
pipeline action 6 also invokes conversion when T1 is not yet available.
Scan contents, conversion outputs and FreeSurfer licenses remain in Git-ignored
local directories.

For an existing checkout of the PR branch, update the menu and importer with:

    git pull

Then run .\start.cmd again.

## FreeSurfer license — one-time Windows wizard

FreeSurfer requires a **free personal license** issued after the user accepts
the terms on its official registration page:
https://surfer.nmr.mgh.harvard.edu/registration.html
There is no automatic license generator or registration bypass. The emailed
license text/file is reused for subsequent MRI scans.

From start.cmd, choose an MRI and select **menu action 9**. When action 2
(preflight), action 3 (recon-all) or action 6 (full pipeline) is used without
an installed license, the same wizard starts automatically.

The wizard:

1. Checks config/license.txt; an existing plausible license is left unchanged.
2. Searches Downloads, Desktop and common home paths for license*.txt and an
   existing FS_LICENSE environment path. It displays only file paths, not
   the contents, and offers numbered import choices.
3. Can open the official registration page in the default browser. Fill out
   the official form and download the emailed license file, or copy the
   license-only lines from the message.
4. Installs a selected file or pasted lines in config/license.txt once.
   The file contents are never sent over the network by the program.
5. Only checks structural plausibility (text, first line email, multi-line),
   not actual validity with FreeSurfer. FreeSurfer itself validates the key
   when it is used in a Docker container.

If you already have a license.txt, simply choose action 9 and import it.
The wizard supports [P] for a full local file path or [E] to paste the
license-only text received by email. Never paste a whole email thread.
The registration step must be completed by the user; the wizard cannot
agree to the license terms on the user's behalf.

The license and any temporary installation files remain under the
Git-ignored config/ directory. The Docker build context also excludes
config/. The license is mounted read-only for recon-all and **is not
baked into the Docker image**.

Advanced commands (optional):

    py -3 scripts/license_setup.py setup
    py -3 scripts/license_setup.py status

If you plan to use FreeSurfer in a commercial Biodata product or on clients'
data, independently review the applicable FreeSurfer license terms first.


## FreeSurfer first-run and interrupted processing safety

The first FreeSurfer recon-all import must start with a **nonexistent** subject
directory (using -i). FreeSurfer creates and owns this directory. The launcher
stores its input signature and selected FreeSurfer container version separately
under ignored work/provenance/<subject-id>.json instead of pre-creating a subject
directory. If recon-all has already imported mri/orig/001.mgz, its next invocation
continues with -all **without** -i.

**Recovery for the bug in the original Windows launcher:** the older version
created a subject directory containing only pacni-input.json, then FreeSurfer
rejected the new input. The new launcher checks that legacy provenance matches
the selected T1 file and FreeSurfer image; it moves the record to the external
provenance area, then removes **only** this one-file metadata stub. It does not
delete real recon-all output. If other output files are present but the original
001.mgz is missing, it stops for manual review rather than resetting anything.

To update an existing local installation once a fix is available in your branch:

    git pull

Then select the same MRI and rerun action 3 (FreeSurfer recon-all).
Never delete the whole work/freesurfer folder: it may contain other scans.
The original MRI/ folder and config/license.txt are not altered by recovery.

As before, completing the FreeSurfer run does not establish visual
segmentation QC or DunedinPACNI validity. Those need the later checks.

## View the result for a selected MRI (menu 10)

Select the study (for example, "2015.07 МРТ Головного мозга"),
then choose **10. Show study report and DunedinPACNI conclusion**.

The report is displayed in the PowerShell window and saved to UTF-8 text:

    results/<subject-id>/report.txt

This command does not launch Docker, FreeSurfer or R. It displays:
- selected T1 input and its provenance/integrity;
- FreeSurfer recon-all completion and automatic structural QC status;
- numerical DunedinPACNI score only with finished FreeSurfer, valid output,
  a matching input signature, CSV subject ID and matching run metadata;
- whether gray-white ratio (GWR) was available;
- conservative interpretation, limitations and next action.

**DunedinPACNI is a research-based pace-of-aging score, not anatomical
brain age in years, a clinical threshold, or a radiology diagnosis.**
No normal/abnormal classifications are assigned without supported reference
intervals. Automated structural QC cannot substitute for visual QC.

If FreeSurfer or scoring is unfinished, menu 10 gives its actual status
rather than inventing results. After successful recon, use action 4 for QC
and action 5 to score, or 6 for the full pipeline.

Command-line equivalent:

    py -3 scripts/mri_pipeline.py report "2015.07 МРТ Головного мозга"

Reports remain local under Git-ignored results/; avoid sharing private
scan identifiers or derived medical findings in public commits.

## SynthSR — upscale your MRI (research only)

**Official tool:** SynthSR uses a learned model of brain anatomy to synthesize
a 1-mm isotropic T1 MPRAGE from a clinical MRI scan of variable contrast.
It is NOT a restoration of measured slices. SynthSR may fill lesions with
normal-appearing tissue. NEVER diagnose or exclude pathology from its output.
Read the original MRI and original radiology report for clinical conclusions.
Sources: https://freesurfer.net/fswiki/SynthSR and
https://pmc.ncbi.nlm.nih.gov/articles/PMC9891693/ .

**One MRI study, not two.** The acquisition stays in `MRI/<study>/`.
Converted DICOM series remain in `work/imports/<study-id>/converted/`.
Synthesized outputs are stored under
`work/upscaled/<study-id>/<source-sha16>/T1_synthsr.nii.gz`.
The linked `work/upscaled/<study-id>/index.json` records original study/date,
selected source series and geometry, SHA-256 hashes, SynthSR code revision
and derived output. Replacing/changing the original invalidates the derivative
marker, which prevents stale results from being reused unnoticed.
The **study selection list shows one entry only** with `[✓ UPSCALED via SynthSR]`
when a verified derivative exists. Reprocessing another source still belongs
to that one acquisition; previous derivatives are not deleted.

How to use on Windows:

1. Put the original 2018/2021 DICOM images in their existing study folder.
2. Choose menu action 1 to run dcm2niix. Even when the importer reports no
   eligible native 3D T1, the full converted series list and files remain
   available under `work/imports/` for SynthSR source selection.
3. Choose **11. Upscale your MRI**. Select one full-brain series from the list,
   preferably with the original image visually checked for whole-brain coverage.
   Localizers, angiographic TOF, DWI and obvious pituitary-only small
   field-of-view volumes are excluded by screening, but coverage must still
   be confirmed visually. Type `UPSCALE` for the costly inference step.
4. A separate CPU Docker image is built the first time. It pins the
   `neurolabusc/py_synthsr` source to commit
   `04ab5548f4609c2b44ca51b3f4bc319b585dafa7` and downloads the publicly
   hosted SynthSR v2 model at build time, with its weights strictly pinned
   to SHA-256 `a472f776e7b33b5ea6e10c801f55fee488f1477a208b3e6998dc1aec1d9c5f8b`.
   A changed remote model fails the build instead of silently changing MRI
   derivatives; `index.json` stores both code revision and weight digest.
   This environment does not alter
   `freesurfer/freesurfer:7.4.1` or require a FreeSurfer license for
   the synthesis step itself. Docker execution disables networking and
   mounts the selected original file read-only.
5. To process the synthetic 1-mm T1 (research only), choose menu action 12;
   use 13 to read its separate report. The original analysis menu items
   continue using **original measured T1 only**.

Original and SynthSR analyses have different FreeSurfer subject IDs and
result directories within the SAME study. They are not two independent MRIs.
For SynthSR, PACNI **always uses the no-GWR coefficient model** because
the synthetic T1 contrast does not provide a valid measured GWR.
Never mix or directly compare synthetic-model scores with original-T1
scores without empirical calibration and quality validation.

Current limitation: local real-data SynthSR Docker build, model download,
inference and visual brain coverage have not been verified in automated CI;
CI runs synthetic-only tests. Do not claim a validated PACNI time-series
from these derived scans. If more complete original 3D T1 images are found
later, keep them under the existing `MRI/<study>` directory and prefer
the original analysis over the synthetic version.

## Patients (local identity, non-destructive merges, selective cloud DICOM)

The launcher now starts with a **Patients** screen. No patient name is hardcoded.
All labels, aliases, merges and imported MRI live locally in Git/Docker-ignored
`patients/`. Existing historical studies stay at `MRI/` without modification.

- `N` creates a local patient label (letters, digits, spaces), with a stable
  `patients/p_<UUID>/MRI/` directory. Labels live only in `patients/registry.json`.
- `D` reads locally available DICOM PatientName/PatientID (optional pydicom,
  conservative built-in fallback). Explicit `REGISTER` creates and assigns
  unambiguous identities. Identical names with different PatientIDs are not
  merged automatically; unknown studies stay unassigned.
- `A` manually attributes existing legacy MRI studies to a patient, without
  moving or changing their prior FreeSurfer/PACNI IDs.
- In a selected patient, `M` persists an operator-confirmed patient merge.
  Both source roots remain physically unchanged and accessible through one
  logical patient; old names become aliases. This preserves historical
  reconstruction paths, cache signatures, and patient provenance.
- All nested MRI processing actions are limited to the selected patient's
  visible MRI studies. New patient data have distinct DICOM import, FreeSurfer
  and SynthSR IDs, even if two patients have the same scan folder name.

### C. Public read-only cloud intake (research / local operator confirmation)

Supported cloud folder listings:
- **Yandex Disk**: public HTTPS link, no credentials.
- **Google Drive**: public folder HTTPS link plus `PACNI_GOOGLE_API_KEY`.
- **Dropbox**: shared folder HTTPS link plus read-only `PACNI_DROPBOX_TOKEN`.

Set only the relevant environment variable in PowerShell before starting
the app. Never put API keys, public access URLs or patient metadata in Git.

```powershell
$env:PACNI_GOOGLE_API_KEY = '<restricted Google API key>'
# Or, for Dropbox:
$env:PACNI_DROPBOX_TOKEN = '<read-only Dropbox OAuth token>'
.\start.cmd
```

Every link must be **read-only, download-enabled and expire at the provider**.
Operators enter the expiry date (up to 14 days) locally, but this date does
**not** revoke the remote link: expiry/revocation must be configured in Drive.

Selective workflow: provider metadata listing (paginated/bounded) -> DICOM
header byte-range previews (max 256 KiB per file) -> T1 candidate list with
native 3D T1 ranked above experimental 2D T1 / SynthSR-only -> explicit
series selection -> check every selected file's SeriesInstanceUID/PatientID
without full download -> `IMPORT` confirmation -> download only that series
to an atomic local staging directory -> check received DICOM IDs again.

T2, FLAIR, TOF, DWI, localizers, ZIP and non-DICOM content are not treated as
native T1. Mixed series, uncertain headers, unsupported partial reads,
unrecognized hosts, oversized listings/files, expired links and network
errors fail closed, never falling back to a full-folder download.

Cloud links are passed in transient process environment, never stored or
printed. Patient files and metadata are excluded from both Git and Docker
build contexts. This application never uploads patient MRI to GitHub or
ChatGPT. Real cloud link acceptance, anatomical coverage and radiological
quality require operator validation; no clinical confidence is implied.

Some public cloud links do not permit API folder listing or HTTP Range.
Google requires an API key to enumerate public folder contents, and Dropbox
requires a scoped token. Such links are rejected until a compatible connector
can provide selective reads; arbitrary cloud/ZIP links are not supported.

API references: https://developers.google.com/workspace/drive/api/guides/search-files ;
https://docs.dropboxapi.com/dropbox-api/api-reference/user-endpoints/files/list-folder ;
https://yandex.com/dev/disk/rest/

### Windows integration acceptance after this PR merges

1. Update the local `master` (`git fetch origin`, `git switch master`,
   `git pull --ff-only origin master`); close old launcher windows before restarting.
2. Run offline tests with `py -3 -m unittest discover -s tests -v` (no MRI
   data or cloud credentials are accessed by the tests).
3. Launch `start.cmd`; the first menu must be **Patients**. Use `D` to
   inspect local DICOM identity hints, or `N` to create `SKAKUN SG`. Use
   `A` to attach existing 2015/2018/2021 studies individually; no originals
   should be moved. The root `MRI/` remains the same.
4. Create a second synthetic label, merge it into the first using `M`,
   restart the app and confirm the alias is still resolved as one patient.
   Do not test with a real second person's records when uncertain.
5. With Docker Desktop **Linux Engine running**, verify
   `docker info --format '{{.OSType}}|{{.ServerVersion}}'` prints `linux|...`.
   Pick a previously upscaled 2018 study and run action `12`, then `13`.
   The synthetic and original PACNI results must not share an output ID.
6. For remote intake, use a separate public **nonpatient** DICOM test folder
   for each provider; ensure the provider API exposes folder listing and
   honors HTTP Range. Verify only the selected T1 series is downloaded.
   Do NOT post patient medical links, tokens or source images into an issue.

GitHub CI covers Python on Windows/Linux, Windows PowerShell 5.1/7 parsing,
R-model regression, Docker SynthSR actual nonclinical inference, mocked
Google Drive/Yandex/Dropbox API contracts, and a full synthetic
patient -> cloud series -> merge -> DICOM -> FreeSurfer preflight chain.
**Not yet certified:** real cloud API access/byte-range support,
clinical-anatomical quality, local Docker Desktop runtime, and scanner
comparability. A declared expiry date in the app does not make a shared URL
expire: the sender must configure provider-supported expiry or revoke it
manually. Real patient data must only be shared after appropriate consent
and secure handling have been established.

Google link-sharing resource-key support follows
https://developers.google.com/workspace/drive/api/guides/resource-keys .
