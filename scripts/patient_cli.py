"""Windows launcher backend for local patient identity management."""
import argparse
import json
import sys
from pathlib import Path
import patient_registry as reg


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
    print(json.dumps(data, ensure_ascii=True))


def studies_json(patient):
    items = []
    for root, study in reg.patient_studies(patient):
        items.append({"name": study.name, "root": str(root),
                      "patient_id": reg.resolve_patient(patient),
                      "storage_patient_id": (root.parent.name if root != reg.LEGACY else ""),
                      "legacy": root == reg.LEGACY})
    print(json.dumps(items, ensure_ascii=True))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["menu", "create", "attach-legacy",
                        "merge", "studies", "discover"])
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
    elif args.command == "discover":
        print(json.dumps(reg.discover_local_patient_names(), ensure_ascii=True))


if __name__ == "__main__":
    try:
        main()
    except (reg.PatientError, ValueError, OSError, json.JSONDecodeError) as exc:
        print("ERROR:", str(exc), file=sys.stderr)
        sys.exit(1)
