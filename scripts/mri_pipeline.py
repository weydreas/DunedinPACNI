"""Windows-first local MRI -> FreeSurfer -> DunedinPACNI pipeline.

All images and reconstructed outputs stay on local disk. Docker Desktop
(Linux containers), Python 3.9+, and a private FreeSurfer license are required.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MRI = ROOT / "MRI"
SUBJECTS = ROOT / "work" / "freesurfer"
PROVENANCE = ROOT / "work" / "provenance"
RESULTS = ROOT / "results"
LICENSE = ROOT / "config" / "license.txt"
FS_IMAGE = os.environ.get("PACNI_FS_IMAGE", "freesurfer/freesurfer:7.4.1")
R_IMAGE = os.environ.get("PACNI_R_IMAGE", "dunedinpacni-r:local")
STATS = ("aseg.stats", "lh.aparc.stats", "rh.aparc.stats")


class PipelineError(Exception):
    pass


def studies():
    if not MRI.exists():
        return []
    return sorted((p for p in MRI.iterdir() if p.is_dir() and not p.is_symlink()),
                  key=lambda p: p.name.casefold())


def get_study(name):
    for p in studies():
        if p.name == name:
            return p
    raise PipelineError("Study not found under MRI/: " + name)


def subject_id(study):
    slug = re.sub(r"[^A-Za-z0-9_]", "_", study.name)[:30] or "scan"
    digest = hashlib.sha256(study.name.encode("utf-8")).hexdigest()[:10]
    return "pacni_" + slug + "_" + digest


def select_input(study):
    """Explicit T1 names, one unambiguous NIfTI, or T1/ DICOM-only series."""
    images = sorted((p for p in study.iterdir()
                     if p.is_file() and not p.is_symlink()
                     and p.name.lower().endswith((".nii", ".nii.gz", ".mgz"))),
                    key=lambda p: p.name.lower())
    explicit = [p for p in images if p.name.lower() in
                ("t1.nii.gz", "t1.nii", "t1.mgz")]
    if len(explicit) > 1:
        raise PipelineError("Multiple T1 volumes found; keep exactly one")
    if len(explicit) == 1:
        return explicit[0]
    if len(images) == 1:
        return images[0]
    if len(images) > 1:
        raise PipelineError("Ambiguous MRI volumes; name selected 3D T1 T1.nii.gz")
    d = study / "T1"
    if not d.is_dir() or d.is_symlink():
        raise PipelineError("No 3D T1: add T1.nii.gz or a dedicated T1/ DICOM series")
    dicoms = sorted(p for p in d.iterdir() if p.is_file() and not p.is_symlink()
                    and (p.suffix.lower() in (".dcm", ".ima", "") or
                         p.name.upper().startswith("IM")))
    if not dicoms:
        raise PipelineError("No DICOM files in T1/")
    # Caller guarantees T1/ contains only one SeriesInstanceUID.
    return dicoms[0]


def signature(study, image):
    if image.parent == study / "T1":
        paths = sorted(p for p in image.parent.iterdir()
                       if p.is_file() and not p.is_symlink())
    else:
        paths = [image]
    entries = []
    for p in paths:
        if study.resolve() not in p.resolve().parents:
            raise PipelineError("MRI input path escapes selected study")
        stat = p.stat()
        entries.append((p.relative_to(study).as_posix(), stat.st_size,
                        stat.st_mtime_ns))
    return hashlib.sha256(json.dumps(entries).encode("utf-8")).hexdigest()


def sh(args):
    print("+", " ".join(str(a) for a in args), flush=True)
    subprocess.run(args, check=True)


def check_docker():
    if not shutil.which("docker"):
        raise PipelineError("Docker Desktop/CLI unavailable")
    sh(["docker", "info", "--format", "{{.ServerVersion}}"])


def check_license():
    if not LICENSE.is_file() or not LICENSE.stat().st_size:
        raise PipelineError("FreeSurfer license missing: config/license.txt")


def subject_dir(study):
    return SUBJECTS / subject_id(study)


def provenance_path(study):
    """Store pipeline input provenance OUTSIDE FreeSurfer-managed subject dirs."""
    return PROVENANCE / (subject_id(study) + ".json")


def provenance(study, image):
    folder = subject_dir(study)
    if folder.is_symlink():
        raise PipelineError("Refusing to reuse a symlinked FreeSurfer subject directory")
    wanted = {"signature": signature(study, image), "fs_image": FS_IMAGE}
    # Old launcher wrote provenance inside the subject before calling -i,
    # which made FreeSurfer reject every initial import. Still read legacy
    # records to allow safe and backwards-compatible recovery.
    external = provenance_path(study)
    legacy = folder / "pacni-input.json"
    found_any = False
    for record in (external, legacy):
        if record.is_symlink():
            raise PipelineError("Refusing to use symlinked provenance file")
        if record.exists():
            found_any = True
            found = json.loads(record.read_text(encoding="utf-8"))
            if any(found.get(k) != v for k, v in wanted.items()):
                raise PipelineError(
                    "MRI input or FreeSurfer version changed: use a fresh scan folder")
    if folder.exists() and not found_any:
        raise PipelineError(
            "FreeSurfer subject exists without input provenance; "
            "inspect before reusing or resetting it")
    return wanted


def record_provenance(study, image, wanted):
    """Persist provenance before processing, without creating subject dir."""
    path = provenance_path(study)
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(wanted, study=study.name,
                   source=image.relative_to(study).as_posix())
    # Atomic replace prevents a partial JSON record after interruption.
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def recover_launcher_stub(study):
    """Clean ONLY the exact one-file stub created by the old broken launcher.

    Do not delete any actual FreeSurfer output, including partial processing,
    and never delete an entire uninspected subject directory.
    """
    folder = subject_dir(study)
    if not folder.exists():
        return
    if folder.is_symlink() or not folder.is_dir():
        raise PipelineError("Unexpected FreeSurfer subject path; refusing cleanup")
    contents = list(folder.iterdir())
    if len(contents) == 1 and contents[0].name == "pacni-input.json" \
            and contents[0].is_file() and not contents[0].is_symlink():
        # By this point provenance() has validated the legacy record and
        # record_provenance() has copied it to a safe external location.
        contents[0].unlink()
        folder.rmdir()
        print("Recovered the metadata-only stub from the prior launcher.")
        return
    if not contents:
        # An empty directory has no reconstruction; FreeSurfer needs to own
        # the directory creation during the initial -i import.
        folder.rmdir()
        print("Removed empty FreeSurfer subject directory before initial import.")


def completed(study):
    return (subject_dir(study) / "scripts" / "recon-all.done").is_file()


def reconstruct(study):
    image = select_input(study)
    check_license()
    check_docker()
    meta = provenance(study, image)
    if completed(study):
        print("FreeSurfer already completed; reusing verified output.")
        return

    # Crucial invariant from the official FreeSurfer recon-all documentation:
    # -i must be provided only when the subject directory DOES NOT EXIST.
    # During resume, the already-imported 001.mgz is reused without -i.
    record_provenance(study, image, meta)
    SUBJECTS.mkdir(parents=True, exist_ok=True)
    recover_launcher_stub(study)
    folder = subject_dir(study)
    if folder.exists() and folder.is_symlink():
        raise PipelineError("Refusing to use symlinked subject directory")
    already_imported = (folder / "mri" / "orig" / "001.mgz").is_file()
    if folder.exists() and not already_imported:
        raise PipelineError(
            "FreeSurfer subject directory exists but mri/orig/001.mgz is absent. "
            "This is not a metadata-only stub; inspect the folder and "
            "recon-all.log before deciding whether to reset it. "
            "No files were deleted."
        )
    args = [
        "docker", "run", "--rm",
        "--mount", "type=bind,source=" + str(study.resolve()) + ",target=/input,readonly",
        "--mount", "type=bind,source=" + str(SUBJECTS.resolve()) + ",target=/subjects",
        "--mount", "type=bind,source=" + str(LICENSE.resolve()) +
        ",target=/opt/pacni-license.txt,readonly",
        "-e", "SUBJECTS_DIR=/subjects", "-e", "FS_LICENSE=/opt/pacni-license.txt",
        FS_IMAGE, "recon-all", "-sd", "/subjects", "-s", subject_id(study)
    ]
    if not already_imported:
        args.extend(["-i", "/input/" + image.relative_to(study).as_posix()])
    args.extend(["-all", "-parallel", "-openmp", str(min(os.cpu_count() or 2, 8))])
    sh(args)
    if not completed(study):
        raise PipelineError("FreeSurfer did not create recon-all.done; inspect recon-all.log")

def inspect(study):
    folder = subject_dir(study)
    result = {"study": study.name, "subject_id": subject_id(study),
              "recon_complete": completed(study),
              "structural_qc": "fail", "visual_qc": "not_performed",
              "stats": {}, "gwr_available": False}
    if not result["recon_complete"]:
        return result
    for name in STATS:
        path = folder / "stats" / name
        if not path.is_file():
            result["stats"][name] = "missing"
            return result
        lines = path.read_text(errors="replace").splitlines()
        n = sum(bool(x.strip()) and not x.lstrip().startswith("#") for x in lines)
        result["stats"][name] = n
        minimum = 30 if "aparc" in name else 35
        if n < minimum or not any("# ColHeaders" in x for x in lines):
            return result
    gwrs = [folder / "stats" / (h + ".w-g.pct.stats") for h in ("lh", "rh")]
    result["gwr_available"] = all(p.is_file() and p.stat().st_size > 100
                                   for p in gwrs)
    result["structural_qc"] = "pass"
    return result


def quality_gate(study):
    report = inspect(study)
    out = RESULTS / subject_id(study)
    out.mkdir(parents=True, exist_ok=True)
    (out / "qc.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if report["structural_qc"] != "pass":
        raise PipelineError("Incomplete or invalid FreeSurfer stats; scoring blocked")
    return report


def score(study):
    image = select_input(study)
    provenance(study, image)
    report = quality_gate(study)
    check_docker()
    out = RESULTS / subject_id(study)
    (out / "sublist.csv").write_text("ID\n" + subject_id(study) + "\n", encoding="utf-8")
    sh(["docker", "build", "-f", str(ROOT / "docker" / "Dockerfile.pacni"),
        "-t", R_IMAGE, str(ROOT)])
    sh(["docker", "run", "--rm",
        "--mount", "type=bind,source=" + str(SUBJECTS.resolve()) +
        ",target=/subjects,readonly",
        "--mount", "type=bind,source=" + str(out.resolve()) + ",target=/results",
        R_IMAGE, "Rscript", "/opt/pacni/run_pacni.R",
        "/subjects", "/results", "FALSE" if report["gwr_available"] else "TRUE"])
    files = sorted(out.glob("*_df_pacni.csv"), key=lambda p: p.stat().st_mtime_ns)
    if not files:
        raise PipelineError("No PACNI CSV produced by R")
    shutil.copyfile(files[-1], out / "pacni.csv")
    (out / "run.json").write_text(json.dumps({
        "study": study.name, "freesurfer_image": FS_IMAGE,
        "gwr_available": report["gwr_available"],
        "result": "pacni.csv", "qc": "automatic checks only; visual inspection pending"
    }, indent=2), encoding="utf-8")
    print("PACNI result:", out / "pacni.csv")
    print("PROVISIONAL: visual segmentation QC has not been performed.")


def compare_study_metadata():
    """Conservative cross-study consistency check; never claim validation
    when scanner/protocol metadata are absent. Optional per-study scan.json
    records declared acquisition metadata, not verified DICOM headers.
    """
    keys = ("scanner_model", "field_strength_t", "sequence", "voxel_size_mm")
    known = {}
    incomplete = []
    for study in studies():
        path = study / "scan.json"
        if not path.is_file():
            incomplete.append(study.name)
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or any(k not in data or data[k] in ("", None)
                                              for k in keys):
            incomplete.append(study.name)
            continue
        known[study.name] = {k: data[k] for k in keys}
    differences = []
    items = sorted(known.items())
    if len(items) >= 2:
        first_name, first_values = items[0]
        for name, values in items[1:]:
            for key in keys:
                if values[key] != first_values[key]:
                    differences.append({"first": first_name, "second": name,
                                        "field": key, "first_value": first_values[key],
                                        "second_value": values[key]})
    # FreeSurfer image mismatch is also a comparability warning, even when
    # scanner metadata are unavailable.
    recon_versions = {}
    for study in studies():
        source = provenance_path(study)
        if not source.is_file():
            source = subject_dir(study) / "pacni-input.json"
        if source.is_file():
            value = json.loads(source.read_text(encoding="utf-8")).get("fs_image")
            if value:
                recon_versions[study.name] = value
    if len(set(recon_versions.values())) > 1:
        differences.append({"field": "freesurfer_image", "values": recon_versions})
    status = ("review_required" if differences else
              "metadata_consistent_not_validated" if
              len(items) >= 2 and not incomplete else "insufficient_metadata")
    result = {
        "status": status, "studies": [p.name for p in studies()],
        "declared_metadata": known, "missing_metadata": incomplete,
        "freesurfer_images": recon_versions, "differences": differences,
        "note": "This is a declared-metadata check, not a visual QC or scanner harmonization."
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "comparability.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["list", "convert", "preflight", "recon", "qc", "score", "all", "compare"])
    parser.add_argument("study", nargs="?")
    args = parser.parse_args()
    if args.command == "compare":
        compare_study_metadata()
        return
    if args.command == "list":
        if not studies():
            print("Place scans under MRI/<study>/T1.nii.gz")
        for study in studies():
            try:
                image = select_input(study)
                print(study.name, "->", image.relative_to(study),
                      "[complete]" if completed(study) else "[not processed]")
            except PipelineError as error:
                print(study.name, "[input needed]", error)
        return
    if args.study is None:
        parser.error("A study name is required")
    study = get_study(args.study)
    if args.command == "convert":
        subprocess.run([sys.executable, str(ROOT / "scripts" / "dicom_import.py"),
                        study.name], check=True)
        return
    if args.command == "preflight":
        print("T1:", select_input(study))
        check_license()
        check_docker()
        print("Preflight OK")
    elif args.command == "recon":
        reconstruct(study)
    elif args.command == "qc":
        quality_gate(study)
    elif args.command == "score":
        score(study)
    elif args.command == "all":
        try:
            select_input(study)
        except PipelineError:
            # A normal DICOM export has nested numbered sequence directories.
            # The operator must choose and explicitly confirm a 3D T1 series.
            subprocess.run([sys.executable,
                            str(ROOT / "scripts" / "dicom_import.py"),
                            study.name], check=True)
        reconstruct(study)
        score(study)


if __name__ == "__main__":
    try:
        main()
    except (PipelineError, subprocess.CalledProcessError, OSError, ValueError) as error:
        print("ERROR:", error, file=sys.stderr)
        sys.exit(1)
