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
2. Request a FreeSurfer license from https://surfer.nmr.mgh.harvard.edu/registration.html
   and save it in config/license.txt. Never commit this file.
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
