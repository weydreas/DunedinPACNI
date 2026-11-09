"""Explicit, local SynthSR upscaling of ONE source series per existing MRI study.

The synthetic T1 is a derivative, NOT a second acquisition. The Docker image
is an independent CPU SynthSR environment. No MRI content is sent to a server.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import re
import shutil
import struct
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MRI = ROOT / "MRI"
IMPORTS = ROOT / "work" / "imports"
UPSCALED = ROOT / "work" / "upscaled"
SYNTHSR_REF = "04ab5548f4609c2b44ca51b3f4bc319b585dafa7"
SYNTHSR_IMAGE = os.getenv("PACNI_SYNTHSR_IMAGE", "biodata-synthsr:04ab5548")
WARNING = (
    "WARNING: SYNTHETIC RESEARCH IMAGE. SynthSR predicts missing anatomy "
    "using a learned brain model; this is NOT a measured or diagnostically "
    "reliable MRI. Lesions can be inpainted as healthy tissue. "
    "NEVER use the synthetic image to exclude pathology or replace a "
    "radiologist's reading of the ORIGINAL images."
)
UNSUITABLE = re.compile(
    r"(?i)(tof|angio|mra|dwi|adc|diffusion|locali[sz]er|scout|survey|"
    r"perfusion|b0|fieldmap|mip)"
)


class UpscaleError(RuntimeError):
    pass


def study_id(study):
    slug = re.sub(r"[^A-Za-z0-9_]", "_", study.name)[:30] or "scan"
    digest = hashlib.sha256(study.name.encode("utf-8")).hexdigest()[:10]
    return "pacni_" + slug + "_" + digest


def storage(study):
    return UPSCALED / study_id(study)


def nifti_info(path):
    opener = gzip.open if path.name.lower().endswith(".gz") else open
    try:
        with opener(path, "rb") as f:
            header = f.read(352)
        if len(header) < 348:
            return None
        endian = "<" if struct.unpack("<I", header[:4])[0] == 348 else ">"
        if struct.unpack(endian + "I", header[:4])[0] != 348:
            return None
        dims = struct.unpack(endian + "8h", header[40:56])
        pix = struct.unpack(endian + "8f", header[76:108])
        if dims[0] < 3 or any(d < 1 for d in dims[1:4]):
            return None
        return {"shape": list(dims[1:4]), "volumes": dims[4] if dims[0] >= 4 else 1,
                "voxel_mm": [round(abs(v), 3) for v in pix[1:4]]}
    except (OSError, EOFError, struct.error, ValueError):
        return None


def file_hash(path):
    checksum = hashlib.sha256()
    with path.open("rb") as inp:
        for block in iter(lambda: inp.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def converted_dir(study):
    return IMPORTS / study_id(study) / "converted"


def candidates(study):
    """Only local image files physically under this study or its DICOM conversion."""
    sources = []
    for p in sorted(study.iterdir()):
        if p.is_file() and not p.is_symlink() and p.name.lower().endswith((".nii", ".nii.gz")):
            sources.append((p, "Original study NIfTI", None))
    manifest_path = converted_dir(study).parent / "series-manifest.json"
    if manifest_path.is_file() and not manifest_path.is_symlink():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("study") != study.name or not isinstance(manifest.get("series"), list):
            raise UpscaleError("DICOM conversion manifest does not match this study")
        for item in manifest["series"]:
            filename = item.get("file")
            if not isinstance(filename, str) or Path(filename).name != filename:
                raise UpscaleError("Invalid converted image filename in manifest")
            p = converted_dir(study) / filename
            if p.is_file() and not p.is_symlink() and p.name.lower().endswith((".nii", ".nii.gz")):
                sources.append((p, str(item.get("description") or filename), item.get("status")))
    good = []
    for p, label, kind in sources:
        info = nifti_info(p)
        if not info or info["volumes"] != 1 or min(info["shape"]) < 8:
            continue
        extent = [a * b for a, b in zip(info["shape"], info["voxel_mm"])]
        if min(extent) < 90:
            # A tiny pituitary-focused or localizer volume does not establish
            # a whole-brain scan; this is a *screening* rule, not proof of coverage.
            continue
        if UNSUITABLE.search(label) or kind == "NOT_T1" and UNSUITABLE.search(p.name):
            continue
        good.append({"path": p, "label": label, "kind": kind,
                     "nifti": info, "extent_mm": extent})
    return good


def read_index(study, verify_source=True):
    path = storage(study) / "index.json"
    if not path.is_file() or path.is_symlink():
        return None
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
        if index.get("study") != study.name or index.get("model_ref") != SYNTHSR_REF:
            return None
        source = (ROOT / index["source_relative"]).resolve()
        dest = (ROOT / index["output_relative"]).resolve()
        valid_roots = [study.resolve(), converted_dir(study).resolve()]
        if not any(source.is_relative_to(base) for base in valid_roots):
            return None
        if not dest.is_relative_to(storage(study).resolve()):
            return None
        if source.is_symlink() or dest.is_symlink() or not source.is_file() or not dest.is_file():
            return None
        if verify_source and file_hash(source) != index["source_sha256"]:
            return None
        if not dest.stat().st_size or file_hash(dest) != index["output_sha256"]:
            return None
        info = nifti_info(dest)
        if not info or info["volumes"] != 1 or not all(.9 <= v <= 1.1 for v in info["voxel_mm"]):
            return None
        return dict(index, source_path=source, output_path=dest)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def eligible_study(study):
    if not study.is_dir() or study.is_symlink() or study.parent.resolve() != MRI.resolve():
        raise UpscaleError("Only immediate original MRI/<study> folders are supported")


def upscale(study, input_fn=input, run_fn=None):
    eligible_study(study)
    print("\n" + WARNING + "\n", flush=True)
    options = candidates(study)
    if not options:
        raise UpscaleError(
            "No plausible whole-brain NIfTI series found. Run Stage 0 to convert "
            "the original DICOM folders (conversion may report no suitable native 3D T1). "
            "Do not try to synthesize a missing field of view."
        )
    print("Source series for this SAME MRI study:")
    for i, item in enumerate(options, 1):
        print(f'{i}. {item["label"]} [{item["kind"] or "NIfTI"}] '
              f'{item["nifti"]["shape"]} voxels '
              f'{item["nifti"]["voxel_mm"]} mm')
    raw = input_fn("Select a series number (Enter cancels): ").strip()
    if not raw.isdecimal() or not (1 <= int(raw) <= len(options)):
        raise UpscaleError("Upscaling cancelled; no images changed")
    source = options[int(raw) - 1]
    print("Selected:", source["label"])
    print("Check whole-brain coverage visually, if possible.")
    if input_fn("Type UPSCALE to generate synthetic anatomy: ").strip() != "UPSCALE":
        raise UpscaleError("No operator confirmation; nothing generated")
    p = source["path"]
    source_sha = file_hash(p)
    previous = read_index(study)
    if previous and previous["source_sha256"] == source_sha:
        print("Already upscaled; reusing:", previous["output_path"])
        return previous["output_path"]

    target_dir = storage(study) / source_sha[:16]
    output = target_dir / "T1_synthsr.nii.gz"
    # Do not destroy existing derived output. The operator must inspect it.
    if output.exists():
        raise UpscaleError(
            "An older derivative already exists for this source: " + str(output) +
            ". Refusing to overwrite it; inspect index.json."
        )
    if shutil.which("docker") is None and run_fn is None:
        raise UpscaleError("Docker Desktop/CLI is required for SynthSR")
    runner = run_fn or (lambda args: subprocess.run(args, check=True))
    print("Building a separate CPU SynthSR Docker image if needed.")
    runner(["docker", "build", "-t", SYNTHSR_IMAGE, "-f",
            str(ROOT / "docker" / "Dockerfile.synthsr"), str(ROOT)])
    target_dir.mkdir(parents=True, exist_ok=True)
    # Do not copy the original source. Bind-mount it read-only instead.
    input_name = "/source.nii.gz" if p.name.lower().endswith(".nii.gz") else "/source.nii"
    runner([
        "docker", "run", "--rm", "--network", "none",
        "--mount", "type=bind,source=" + str(p.resolve()) + ",target=" + input_name + ",readonly",
        "--mount", "type=bind,source=" + str(target_dir.resolve()) + ",target=/output",
        SYNTHSR_IMAGE, "--i", input_name, "--o", "/output/T1_synthsr.nii.gz",
        "--cpu", "--threads", str(min(os.cpu_count() or 2, 8))
    ])
    info = nifti_info(output) if output.is_file() else None
    if not info or info["volumes"] != 1 or not all(.9 <= v <= 1.1 for v in info["voxel_mm"]):
        raise UpscaleError(
            "SynthSR did not create a valid 1mm isotropic NIfTI; "
            "no upscaled marker was recorded. Check Docker logs."
        )
    history = []
    if previous:
        history = previous.get("history", [])
        history.append({"source_relative": previous["source_relative"],
                        "output_relative": previous["output_relative"],
                        "source_sha256": previous["source_sha256"]})
    record = {
        "schema_version": 1, "study": study.name, "method": "SynthSR",
        "model_ref": SYNTHSR_REF, "model_family": "SynthSR v2 January 2023",
        "docker_image": SYNTHSR_IMAGE, "synthetic": True,
        "source_relative": p.relative_to(ROOT).as_posix(),
        "source_sha256": source_sha,
        "source_description": source["label"],
        "source_geometry": source["nifti"],
        "output_relative": output.relative_to(ROOT).as_posix(),
        "output_sha256": file_hash(output), "output_geometry": info,
        "history": history,
        "warning": WARNING,
    }
    store = storage(study)
    store.mkdir(parents=True, exist_ok=True)
    stage = store / ".index.tmp"
    stage.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(stage, store / "index.json")
    print("[UPSCALED] (derived; not a new MRI acquisition):", output)
    print("Original remains untouched:", p)
    print(WARNING)
    return output
