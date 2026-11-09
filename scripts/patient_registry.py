"""Local-only patient registry for DunedinPACNI.

No patient identifiers, MRI files, or shared cloud URLs belong in Git.
Patient aliases and manual merges are recorded on disk. Registry updates are
atomic; destructive operations are deliberately not implicit.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "patients"
REGISTRY = DATA / "registry.json"
LEGACY = ROOT / "MRI"

class PatientError(RuntimeError):
    pass


def validate_name(name: str) -> str:
    name = unicodedata.normalize("NFC", name.strip())
    if not (1 <= len(name) <= 80):
        raise PatientError("Patient label must contain 1 to 80 characters")
    if name[0] == " " or name[-1] == " " or "  " in name:
        raise PatientError("No leading, trailing or doubled spaces")
    if not all(c.isalpha() or c.isdigit() or c == " " for c in name):
        raise PatientError("Only letters, digits and single spaces are allowed")
    return name


def default_registry():
    return {"schema_version": 1, "patients": {}, "aliases": {},
            "legacy_owner": None, "legacy_assignments": {}, "merges": []}


def load_registry():
    if not REGISTRY.exists():
        return default_registry()
    if REGISTRY.is_symlink():
        raise PatientError("Refusing symlinked patient registry")
    obj = json.loads(REGISTRY.read_text(encoding="utf-8"))
    if (not isinstance(obj, dict) or obj.get("schema_version") != 1
            or not isinstance(obj.get("patients"), dict)
            or not isinstance(obj.get("aliases"), dict)
            or not isinstance(obj.get("merges"), list)):
        raise PatientError("Invalid patient registry; manual recovery required")
    obj.setdefault("legacy_assignments", {})
    if not isinstance(obj["legacy_assignments"], dict):
        raise PatientError("Invalid legacy assignments")
    return obj


def save_registry(obj):
    DATA.mkdir(parents=True, exist_ok=True)
    if DATA.is_symlink() or REGISTRY.is_symlink():
        raise PatientError("Unsafe patient registry path")
    pending = DATA / ("." + uuid.uuid4().hex + ".tmp")
    try:
        with pending.open("x", encoding="utf-8", newline="\n") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(pending, REGISTRY)
    finally:
        if pending.exists():
            pending.unlink()


def root_for(patient_id):
    if not re.fullmatch(r"p_[0-9a-f]{32}", patient_id):
        raise PatientError("Invalid patient ID")
    return DATA / patient_id / "MRI"


def create_patient(label):
    label = validate_name(label)
    obj = load_registry()
    if label.casefold() in obj["aliases"]:
        raise PatientError("A patient with this name or alias already exists")
    patient_id = "p_" + uuid.uuid4().hex
    root = root_for(patient_id)
    root.mkdir(parents=True, exist_ok=False)
    obj["patients"][patient_id] = {"label": label, "created_utc": datetime.now(timezone.utc).isoformat()}
    obj["aliases"][label.casefold()] = patient_id
    try:
        save_registry(obj)
    except Exception:
        # Keep directory on disk for safe inspection; never delete MRI.
        raise
    return patient_id


def resolve_patient(name_or_id, obj=None):
    obj = obj or load_registry()
    key = obj["aliases"].get(name_or_id.casefold(), name_or_id)
    if key not in obj["patients"]:
        raise PatientError("Unknown patient")
    return key


def legacy_studies():
    if not LEGACY.is_dir() or LEGACY.is_symlink():
        return []
    return sorted(p for p in LEGACY.iterdir() if p.is_dir() and not p.is_symlink())


def legacy_patient(study_name, obj=None):
    obj = obj or load_registry()
    return obj.get("legacy_assignments", {}).get(study_name) or obj.get("legacy_owner")


def assign_legacy(patient_id, study_name=None):
    obj = load_registry()
    patient_id = resolve_patient(patient_id, obj)
    available = {p.name for p in legacy_studies()}
    targets = [study_name] if study_name is not None else sorted(available)
    if not targets or any(n not in available for n in targets):
        raise PatientError("Missing legacy study folder")
    for name in targets:
        current = legacy_patient(name, obj)
        if current and current != patient_id:
            raise PatientError("Legacy MRI study is assigned to a different patient")
    for name in targets:
        obj["legacy_assignments"][name] = patient_id
    save_registry(obj)


def list_patients():
    obj = load_registry()
    return [(pid, record["label"], len(patient_studies(pid)))
            for pid, record in sorted(obj["patients"].items(),
                                      key=lambda row: row[1]["label"].casefold())]


def study_roots(patient_id):
    return all_patient_roots(patient_id)


def studies_for(patient_id):
    found = []
    for root in all_patient_roots(patient_id):
        if root.is_dir() and not root.is_symlink():
            for study in sorted(root.iterdir()):
                if study.is_dir() and not study.is_symlink():
                    found.append((root, study))
    names = [s.name.casefold() for _,s in found]
    if len(names) != len(set(names)):
        raise PatientError("Duplicate study folder names between patient and legacy roots")
    return found


def merge_patients(target, source):
    """Merge identities without moving any MRI. Other modules keep stable paths.

    The source patient's data folder remains physically where it is; the
    registry records a persistent directory union and source alias. This
    cannot invalidate FreeSurfer recon or overwritten/renamed DICOM paths.
    """
    obj = load_registry()
    target = resolve_patient(target, obj)
    source = resolve_patient(source, obj)
    if target == source:
        raise PatientError("Patient identities already match")
    # Maintain existing physical directories, but hide source identity.
    dest = obj["patients"][target]
    source_rec = obj["patients"][source]
    parents = dest.get("linked_roots", [])
    if source not in parents:
        parents.append(source)
    parents.extend(x for x in source_rec.get("linked_roots", []) if x not in parents)
    if target in parents:
        raise PatientError("Patient link cycle")
    dest["linked_roots"] = parents
    del obj["patients"][source]
    for label, pid in list(obj["aliases"].items()):
        if pid == source:
            obj["aliases"][label] = target
    if obj.get("legacy_owner") == source:
        obj["legacy_owner"] = target
    for study, pid in list(obj.get("legacy_assignments", {}).items()):
        if pid == source:
            obj["legacy_assignments"][study] = target
    obj["merges"].append({"source": source, "target": target, "source_label": source_rec["label"],
                          "utc": datetime.now(timezone.utc).isoformat(), "mode": "non_destructive_alias_union"})
    save_registry(obj)
    return target


def all_patient_roots(patient_id, obj=None):
    obj = obj or load_registry()
    pid = resolve_patient(patient_id, obj)
    roots = [root_for(pid)]
    for linked in obj["patients"][pid].get("linked_roots", []):
        roots.append(root_for(linked))
    if (obj.get("legacy_owner") == pid
            or pid in obj.get("legacy_assignments", {}).values()):
        roots.insert(0, LEGACY)
    return roots


def active_patient_label(patient_id):
    obj = load_registry()
    return obj["patients"][resolve_patient(patient_id, obj)]["label"]


def patient_studies(patient_id):
    obj = load_registry()
    pid = resolve_patient(patient_id, obj)
    result = []
    for root in all_patient_roots(pid, obj):
        if root.is_dir() and not root.is_symlink():
            for study in sorted(root.iterdir()):
                if not study.is_dir() or study.is_symlink():
                    continue
                if root == LEGACY and legacy_patient(study.name, obj) != pid:
                    continue
                result.append((root, study))
    return result


def discover_local_patient_names(limit_per_study=3):
    """Best-effort no-network identification from local DICOM headers.

    pydicom is optional; unknown patient stays UNKNOWN, never guessed from a
    folder name or auto-merged based on initials.
    """
    try:
        import pydicom
    except ImportError:
        return {"status": "pydicom_missing", "found": {}, "unknown_studies": [p.name for p in legacy_studies()]}
    found = {}
    unknown = []
    for study in legacy_studies():
        observed = set()
        count = 0
        for p in study.rglob("*"):
            if count >= limit_per_study:
                break
            if not p.is_file() or p.is_symlink() or p.suffix.lower() in {".json",".nii",".gz",".mgz"}:
                continue
            try:
                header = pydicom.dcmread(str(p), stop_before_pixels=True,
                                        specific_tags=["PatientName"], force=False)
                name = str(getattr(header, "PatientName", "")).replace("^", " ").strip()
                if name:
                    observed.add(validate_name(name))
                    count += 1
            except Exception:
                continue
        if len(observed) == 1:
            found[study.name] = next(iter(observed))
        else:
            unknown.append(study.name)
    return {"status": "scanned", "found": found, "unknown_studies": unknown}
