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


def provenance(study, image):
    folder = subject_dir(study)
    metadata = folder / "pacni-input.json"
    wanted = {"signature": signature(study, image), "fs_image": FS_IMAGE}
    if metadata.exists():
        found = json.loads(metadata.read_text(encoding="utf-8"))
        if any(found.get(k) != v for k, v in wanted.items()):
            raise PipelineError("MRI input or FreeSurfer version changed: use a fresh scan folder")
    elif folder.exists():
        raise PipelineError("FreeSurfer output lacks provenance; not safe to reuse")
    return wanted


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
    was_started = subject_dir(study).is_dir()
    SUBJECTS.mkdir(parents=True, exist_ok=True)
    subject_dir(study).mkdir(exist_ok=True)
    info = subject_dir(study) / "pacni-input.json"
    if not info.exists():
        info.write_text(json.dumps(dict(meta, study=study.name,
                                       source=image.relative_to(study).as_posix()),
                                   indent=2), encoding="utf-8")
    args = [
        "docker", "run", "--rm",
        "--mount", "type=bind,source=" + str(study.resolve()) + ",target=/input,readonly",
        "--mount", "type=bind,source=" + str(SUBJECTS.resolve()) + ",target=/subjects",
        "--mount", "type=bind,source=" + str(LICENSE.resolve()) +
        ",target=/opt/pacni-license.txt,readonly",
        "-e", "SUBJECTS_DIR=/subjects", "-e", "FS_LICENSE=/opt/pacni-license.txt",
        FS_IMAGE, "recon-all", "-sd", "/subjects", "-s", subject_id(study)
    ]
    if not was_started:
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["list", "preflight", "recon", "qc", "score", "all"])
    parser.add_argument("study", nargs="?")
    args = parser.parse_args()
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
        reconstruct(study)
        score(study)


if __name__ == "__main__":
    try:
        main()
    except (PipelineError, subprocess.CalledProcessError, OSError, ValueError) as error:
        print("ERROR:", error, file=sys.stderr)
        sys.exit(1)
