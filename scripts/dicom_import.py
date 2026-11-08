"""Recursive DICOM-to-NIfTI importer for the local DunedinPACNI pipeline.

Uses dcm2niix. NEVER creates T1 contrast from a T2/FLAIR series.
Requires explicit operator selection of a plausible 3D T1 series.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MRI = ROOT / "MRI"
CACHE = ROOT / "work" / "imports"
POSITIVE = re.compile(r"t1|mprage|mp.rage|bravo|(?:f)?spgr|mdeft|tfl|3d.?ir", re.I)
NEGATIVE = re.compile(
    r"flair|t2|dwi|diffusion|adc|swi|bold|fmri|dti|locali[sz]er|scout|"
    r"survey|field.?map|tof|angio|perfusion|asl|t2star|t2\*|swi|gre.?epi",
    re.I,
)


class ImportErrorMRI(RuntimeError):
    pass


def study_id(study):
    slug = re.sub(r"[^A-Za-z0-9_]", "_", study.name)[:30] or "scan"
    return "pacni_" + slug + "_" + hashlib.sha256(
        study.name.encode("utf-8")).hexdigest()[:10]


def converter_path():
    env = os.environ.get("PACNI_DCM2NIIX", "").strip()
    if env:
        p = Path(env)
        if p.is_file():
            return str(p)
        found = shutil.which(env)
        if found:
            return found
        raise ImportErrorMRI("PACNI_DCM2NIIX points to a missing executable: " + env)
    found = shutil.which("dcm2niix")
    if found:
        return found
    tools_dir = ROOT / "tools"
    for p in (tools_dir / "dcm2niix.exe", tools_dir / "dcm2niix"):
        if p.is_file():
            return str(p)
    if tools_dir.is_dir():
        matches = sorted(tools_dir.rglob("dcm2niix.exe"))
        if matches:
            return str(matches[0])
    raise ImportErrorMRI(
        "dcm2niix not found. Download the Windows binary from "
        "https://github.com/rordenlab/dcm2niix/releases "
        "and put dcm2niix.exe into the project tools/ folder."
    )


def source_files(study):
    """Inventory original DICOM leaf folders without relying on DICOM extensions."""
    result = []
    for folder, subfolders, files in os.walk(study, followlinks=False):
        subfolders[:] = [name for name in subfolders
                         if not (Path(folder) / name).is_symlink()]
        for name in files:
            p = Path(folder) / name
            if p.is_symlink() or p.suffix.lower() in
                (".nii", ".gz", ".mgz", ".json", ".txt", ".csv"):
                continue
            if p.name.upper() == "DICOMDIR":
                continue
            result.append(p)
    return sorted(result)


def nifti_info(path):
    """Read only the NIfTI-1 header; avoids a nibabel dependency."""
    opener = gzip.open if path.name.lower().endswith(".gz") else open
    try:
        with opener(path, "rb") as f:
            header = f.read(352)
        if len(header) < 348:
            return None
        endian = "<" if struct.unpack("<I", header[:4])[0] == 348 else ">"
        if struct.unpack(endian + "I", header[:4])[0] != 348:
            return None
        dim = struct.unpack(endian + "8h", header[40:56])
        vox = struct.unpack(endian + "8f", header[76:108])
        if dim[0] < 3 or any(x < 1 for x in dim[1:4]):
            return None
        return {
            "shape": list(dim[1:4]),
            "volumes": dim[4] if dim[0] >= 4 else 1,
            "voxel_mm": [round(abs(v), 3) for v in vox[1:4]],
        }
    except (OSError, EOFError, struct.error):
        return None


def metadata_file(image):
    if image.name.lower().endswith(".nii.gz"):
        return image.with_name(image.name[:-7] + ".json")
    return image.with_suffix(".json")


def classify(image, meta):
    desc = " ".join(str(meta.get(k, "")) for k in
                    ("SeriesDescription", "ProtocolName", "SequenceName",
                     "PulseSequenceDetails"))
    info = nifti_info(image)
    if NEGATIVE.search(desc):
        return "NOT_T1", "Series metadata indicate another contrast", info
    if str(meta.get("MRAcquisitionType", "")).upper() == "2D":
        return "NOT_3D", "The DICOM series is marked 2D", info
    if not info or info["volumes"] != 1:
        return "UNVERIFIED", "Not a valid single-volume NIfTI-1 3D image", info
    if any(v <= 0 or v > 2 for v in info["voxel_mm"]):
        return "LOW_RESOLUTION", "Voxel size incompatible with a typical high-resolution 3D T1", info
    if POSITIVE.search(desc):
        return "LIKELY_T1", "T1 sequence description; visual verification still required", info
    return "UNVERIFIED", "3D volume found but T1 contrast not established", info


def collect_candidates(output):
    images = sorted(list(output.glob("*.nii.gz")) + list(output.glob("*.nii")))
    result = []
    for image in images:
        sidecar = metadata_file(image)
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else {}
        except (OSError, ValueError):
            meta = {}
        status, reason, info = classify(image, meta)
        result.append({"image": image, "sidecar": sidecar, "meta": meta,
                       "status": status, "reason": reason, "nifti": info})
    return result


def convert(study, *, input_fn=input, run_fn=None):
    study = Path(study)
    if not study.is_dir() or study.is_symlink():
        raise ImportErrorMRI("Study folder does not exist")
    if study.parent.resolve() != MRI.resolve():
        raise ImportErrorMRI("Only immediate MRI/<study> directories are accepted")
    if (study / "T1.nii.gz").exists():
        print("A T1.nii.gz already exists; refusing to overwrite it.")
        print("If incorrect, move it aside and review existing FreeSurfer provenance.")
        return study / "T1.nii.gz"
    if not source_files(study):
        raise ImportErrorMRI("No potential DICOM source files in the study folders")
    binary = converter_path()
    output = CACHE / study_id(study) / "converted"
    # Avoid mixing old and new conversion batches. Re-running is deterministic.
    # Delete only our derived, Git-ignored cache, never original images.
    if output.is_dir():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    command = [binary, "-z", "y", "-b", "y", "-ba", "y",
               "-d", "9", "-f", "%f_%p_%s", "-o", str(output), str(study)]
    print("Converting all DICOM series recursively under:", study, flush=True)
    if run_fn is None:
        subprocess.run(command, check=True)
    else:
        run_fn(command)
    candidates = collect_candidates(output)
    if not candidates:
        raise ImportErrorMRI("No NIfTI files produced. Check whether folders contain valid DICOM.")
    print("\nConverted series:")
    for index, item in enumerate(candidates, 1):
        m = item["meta"]
        info = item["nifti"]
        geometry = (str(info["shape"]) + " voxels " +
                    str(info["voxel_mm"]) + " mm") if info else "unknown geometry"
        print(str(index) + ".", item["status"], "|",
              m.get("SeriesDescription") or m.get("ProtocolName") or item["image"].name,
              "|", geometry)
    allowed = {i for i, item in enumerate(candidates, 1)
               if item["status"] == "LIKELY_T1"}
    manifest = {
        "study": study.name,
        "series": [
            {"file": x["image"].name, "status": x["status"],
             "description": x["meta"].get("SeriesDescription"),
             "nifti": x["nifti"], "reason": x["reason"]}
            for x in candidates
        ],
    }
    (output.parent / "series-manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    if not allowed:
        raise ImportErrorMRI(
            "No confirmed T1-labeled high-resolution 3D series. "
            "Review series in 3D Slicer; never rename T2/FLAIR as T1. "
            "Converted files and series-manifest.json remain in work/imports/."
        )
    print("\nSelect the T1 series number (only LIKELY_T1 entries are allowed).")
    answer = input_fn("T1 series number (Enter to cancel): ").strip()
    if not answer.isdecimal() or int(answer) not in allowed:
        raise ImportErrorMRI("No eligible T1 series selected; no input was changed")
    item = candidates[int(answer) - 1]
    print("Selected:", item["image"].name, item["reason"])
    confirmation = input_fn("Confirm anatomical 3D T1 after review? Type YES: ").strip()
    if confirmation != "YES":
        raise ImportErrorMRI("Selection not confirmed; no T1 file created")
    target = study / "T1.nii.gz"
    if target.exists():
        raise ImportErrorMRI("T1.nii.gz exists; refusing to overwrite")
    shutil.copyfile(item["image"], target)
    if item["sidecar"].is_file():
        shutil.copyfile(item["sidecar"], study / "T1.json")
    record = {"status": "selected_by_operator", "selected_series": item["image"].name,
              "original_study": study.name, "notes": "DICOM-to-NIfTI only, NOT synthetic T1",
              "source_metadata": item["meta"], "nifti": item["nifti"]}
    (study / "pacni-import.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    if not (study / "scan.json").exists():
        m = item["meta"]
        qc = item["nifti"]
        scan = {
            "scanner_model": m.get("ManufacturersModelName"),
            "field_strength_t": m.get("MagneticFieldStrength"),
            "sequence": m.get("SeriesDescription") or m.get("ProtocolName"),
            "voxel_size_mm": qc["voxel_mm"] if qc else None,
        }
        (study / "scan.json").write_text(
            json.dumps(scan, indent=2, ensure_ascii=False), encoding="utf-8")
    print("T1 saved:", target, flush=True)
    print("Original DICOM files were not moved or modified.")
    return target


def main():
    parser = argparse.ArgumentParser(description="Convert DICOM series to a selected 3D T1")
    parser.add_argument("study")
    args = parser.parse_args()
    convert(MRI / args.study)


if __name__ == "__main__":
    try:
        main()
    except (ImportErrorMRI, subprocess.CalledProcessError, OSError, ValueError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        sys.exit(1)
