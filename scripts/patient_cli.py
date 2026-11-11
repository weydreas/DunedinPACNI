"""Windows launcher backend for local patient identity management."""
import argparse
import json
import os
import sys
from pathlib import Path
import patient_registry as reg
import mri_pipeline as pipeline


def menu_json():
    obj = reg.load_registry()
    data = {
        "patients": [{"id": pid, "label": record["label"],
                      "study_count": len(reg.patient_studies(pid))}
                     for pid, record in sorted(obj["patients"].items(),
                                               key=lambda v: v[1]["label"].casefold())],
        "unassigned_legacy_studies": [p.name for p in reg.legacy_studies()
                                      if not reg.legacy_patient(p.name, obj)],
        "legacy_owner": obj.get("legacy_owner"),
    }
    if not data["patients"] and data["unassigned_legacy_studies"]:
        # Read only a few original headers; this never changes the registry.
        # Unknown or conflicting DICOM identities are shown as unresolved.
        scan = reg.discover_local_patient_names()
        data["detected_local"] = [{"study": study, "name": value["label"]}
                                  for study, value in sorted(scan["found"].items())]
        data["unknown_local_count"] = len(scan["unknown_studies"])
    print(json.dumps(data, ensure_ascii=True))


def studies_json(patient):
    items = []
    for root, study in reg.patient_studies(patient):
        items.append({"name": study.name, "root": str(root),
                      "patient_id": reg.resolve_patient(patient),
                      "storage_patient_id": (root.parent.name if root != reg.LEGACY else ""),
                      "legacy": root == reg.LEGACY})
    print(json.dumps(items, ensure_ascii=True))


def studies_menu_json(patient):
    """Stable JSON object contract for the interactive Windows MRI menu.

    A named object rather than a bare JSON array avoids PowerShell pipeline
    enumeration ambiguity for zero, one, or several attached studies. Keep
    the existing 'studies' array contract unchanged for older consumers.
    """
    patient_id = reg.resolve_patient(patient)
    studies = []
    for root, study in reg.patient_studies(patient_id):
        studies.append({
            "name": study.name,
            "root": str(root),
            "patient_id": patient_id,
            "storage_patient_id": root.parent.name if root != reg.LEGACY else "",
            "legacy": root == reg.LEGACY,
        })
    print(json.dumps({"patient_id": patient_id, "count": len(studies),
                      "studies": studies}, ensure_ascii=True))


def compare_patient(patient):
    """Compare all *logical* patient studies, including merged physical roots.

    Keep the original pipeline compare API and output intact; patient-level
    comparison is a separate, local report derived from declared metadata.
    """
    patient_id = reg.resolve_patient(patient)
    required = ("scanner_model", "field_strength_t", "sequence", "voxel_size_mm")
    known, missing, versions, labels = {}, [], {}, []
    for root, study in reg.patient_studies(patient_id):
        origin = "legacy" if root == reg.LEGACY else root.parent.name
        label = study.name + " [" + origin + "]"
        if label in labels:
            raise reg.PatientError("Duplicate MRI study identities: manual review required")
        labels.append(label)
        meta = study / "scan.json"
        if not meta.is_file() or meta.is_symlink():
            missing.append(label)
        else:
            try:
                obj = json.loads(meta.read_text(encoding="utf-8"))
                if not isinstance(obj, dict) or any(
                    k not in obj or obj[k] in ("", None) for k in required
                ):
                    missing.append(label)
                else:
                    known[label] = {k: obj[k] for k in required}
            except (OSError, ValueError, TypeError):
                missing.append(label)

        # Existing provenance is tied to the *physical* study source and its
        # original namespace, not the target of a patient merge.
        old_namespace = os.environ.get("PACNI_PATIENT_ID")
        try:
            if root == reg.LEGACY:
                os.environ.pop("PACNI_PATIENT_ID", None)
            else:
                os.environ["PACNI_PATIENT_ID"] = root.parent.name
            subject = pipeline.subject_id(study)
        finally:
            if old_namespace is None:
                os.environ.pop("PACNI_PATIENT_ID", None)
            else:
                os.environ["PACNI_PATIENT_ID"] = old_namespace
        source = reg.ROOT / "work" / "provenance" / (subject + ".json")
        if not source.is_file():
            source = reg.ROOT / "work" / "freesurfer" / subject / "pacni-input.json"
        if source.is_file() and not source.is_symlink():
            try:
                value = json.loads(source.read_text(encoding="utf-8")).get("fs_image")
                if value:
                    versions[label] = value
            except (OSError, ValueError, TypeError):
                pass
    differences = []
    items = sorted(known.items())
    if len(items) >= 2:
        first, first_values = items[0]
        for name, values in items[1:]:
            for key in required:
                if values[key] != first_values[key]:
                    differences.append({"first":first,"second":name,
                                        "field":key,
                                        "first_value":first_values[key],
                                        "second_value":values[key]})
    if len(set(versions.values())) > 1:
        differences.append({"field":"freesurfer_image","values":versions})
    status = ("review_required" if differences else
              "metadata_consistent_not_validated" if len(items) >= 2
              and not missing else "insufficient_metadata")
    report = {"patient_id":patient_id, "status":status,
              "studies":labels, "declared_metadata":known,
              "missing_metadata":missing, "freesurfer_images":versions,
              "differences":differences,
              "note":"Declared metadata only; not clinical QC, scanner harmonization or evidence of score comparability."}
    folder = reg.ROOT / "results" / "patients" / patient_id
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / "comparability.json"
    pending = folder / "comparability.tmp"
    pending.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(pending, out)
    print(json.dumps(report, ensure_ascii=True, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["menu", "create", "attach-legacy",
                        "merge", "studies", "studies-menu", "discover", "register-discovered", "compare"])
    parser.add_argument("a", nargs="?")
    parser.add_argument("b", nargs="?")
    args = parser.parse_args()
    if args.command == "menu":
        menu_json()
    elif args.command == "create":
        if args.a is None:
            parser.error("Missing name")
        print(reg.create_patient(args.a))
    elif args.command == "attach-legacy":
        reg.assign_legacy(args.a, args.b)
        print("Existing MRI studies attached without moving files")
    elif args.command == "merge":
        reg.merge_patients(args.a, args.b)
        print("Patient identities linked. All study folders remain readable and unchanged.")
    elif args.command == "studies":
        studies_json(args.a)
    elif args.command == "studies-menu":
        studies_menu_json(args.a)
    elif args.command == "discover":
        print(json.dumps(reg.discover_local_patient_names(), ensure_ascii=True))
    elif args.command == "register-discovered":
        print(json.dumps(reg.register_discovered(), ensure_ascii=True))
    elif args.command == "compare":
        compare_patient(args.a)


if __name__ == "__main__":
    try:
        main()
    except (reg.PatientError, ValueError, OSError, json.JSONDecodeError) as exc:
        print("ERROR:", str(exc), file=sys.stderr)
        sys.exit(1)
